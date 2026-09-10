"""Concrete gateway administration composition; no authority/environment defaults."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal, Self

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import load_pem_private_key
from pydantic import Field, model_validator

from maezo.gateway.external_cases.models import Digest, Ref, Scope, now_utc
from maezo.gateway.human.read_profile import parse_model
from maezo.gateway.portal_identity import build_human_identity_adapters
from maezo.portal.api.config import PortalSettings
from maezo.portal.api.session import HumanSessionResolver
from maezo.portal.engine.profile import strict_loads

from .authority import InstalledStaffAuthority, fingerprint
from .census_plan import protected_read
from .census_source import CensusCut, OwnerManifestStaffCensusSource, ordered_unique
from .composition import StaffCaseRuntime
from .materials import load_materials
from .models import Closed, StaffCaseError
from .production import _close, staff_runtime
from .production_config import MATERIAL_DIRECTORY, PortalProductionSettings
from .publisher import StaffNativeClient, StaffSigner


class ProtectedFile(Closed):
    path: str = Field(repr=False)
    sha256: Digest = Field(repr=False)


class CensusIssuerBinding(Closed):
    entry_ref: Ref
    source_namespace: Ref
    key_fingerprint: Digest
    purposes: tuple[Literal["staff_case_grant", "staff_policy_head", "scope_complete"], ...]
    signing_key: ProtectedFile | None = Field(repr=False)


class CensusSourceBinding(Closed):
    source_ref: Ref
    issuers: tuple[CensusIssuerBinding, ...]
    importer_entry_ref: Ref
    importer_key_fingerprint: Digest
    importer_signing_key: ProtectedFile = Field(repr=False)
    importer_certificate: ProtectedFile = Field(repr=False)
    importer_tls_key: ProtectedFile = Field(repr=False)


class CensusDependencyConfiguration(Closed):
    schema_: Literal["staff-case-census-dependencies.v1"] = Field(alias="schema")
    scope: Scope
    installation_digest: Digest
    native_configuration_digest: Digest
    cut_digest: Digest
    sources: tuple[CensusSourceBinding, ...]

    @model_validator(mode="after")
    def exact_sets(self) -> Self:
        ordered_unique([s.source_ref for s in self.sources])
        for source in self.sources:
            ordered_unique([i.entry_ref for i in source.issuers])
            for issuer in source.issuers:
                if not issuer.purposes or len(set(issuer.purposes)) != len(issuer.purposes):
                    raise StaffCaseError("invalid")
        return self


class CensusProductionConfiguration(Closed):
    schema_: Literal["staff-case-census-administration.v1"] = Field(alias="schema")
    dependencies: CensusDependencyConfiguration
    # Reuse the exact installed production witness/session composition and fixed
    # material closure; these inputs do not install source/importer identities.
    witness_profile: PortalProductionSettings
    identity_settings: ProtectedFile = Field(repr=False)
    session_secret: ProtectedFile = Field(repr=False)
    source_cut: ProtectedFile = Field(repr=False)
    material_root: str = Field(repr=False)
    retained_source_root: str = Field(repr=False)
    custody_registration_ref: Ref
    custody_registration_digest: Digest
    output_plan_path: str = Field(repr=False)
    input_plan: ProtectedFile | None = Field(repr=False)
    maximum_source_bytes: int = Field(ge=1, le=67108864)
    maximum_plan_bytes: int = Field(ge=1, le=134217728)
    maximum_records: int = Field(ge=1, le=100000)
    maximum_seconds: int = Field(ge=1, le=10)

    @model_validator(mode="after")
    def explicit_profile(self) -> Self:
        s = self.witness_profile
        d = self.dependencies
        if (
            s.capabilities != "identity,staff_cases"
            or s.staff_scope != d.scope
            or s.staff_designation_sha256 != d.installation_digest
            or s.staff_native_configuration_sha256 != d.native_configuration_digest
            or self.source_cut.sha256 != d.cut_digest
        ):
            raise StaffCaseError("invalid")
        retained = Path(self.retained_source_root)
        if not retained.is_absolute() or str(retained).startswith(("/run/", "/tmp/", "/private/tmp/")):
            raise StaffCaseError("invalid")
        for path in [self.output_plan_path, self.source_cut.path] + (
            [] if self.input_plan is None else [self.input_plan.path]
        ):
            if not Path(path).is_absolute() or not Path(path).is_relative_to(retained):
                raise StaffCaseError("invalid")
        return self


def material(config: CensusProductionConfiguration, value: ProtectedFile, maximum: int = 65536) -> bytes:
    path, root = Path(value.path), Path(config.material_root)
    if not root.is_absolute() or not path.is_absolute() or not path.is_relative_to(root):
        raise StaffCaseError("invalid")
    return protected_read(path, expected=value.sha256, maximum=maximum)


def signing_key(raw: bytes, expected: str) -> Ed25519PrivateKey:
    key = load_pem_private_key(raw, password=None)
    if not isinstance(key, Ed25519PrivateKey) or fingerprint(key.public_key()) != expected:
        raise StaffCaseError("denied")
    return key


@dataclass(repr=False)
class CensusProduction:
    config: CensusProductionConfiguration
    authority: InstalledStaffAuthority
    source: OwnerManifestStaffCensusSource
    signers: dict[tuple[str, str], StaffSigner]
    clients: dict[str, StaffNativeClient]
    resolver: HumanSessionResolver | None
    runtime: StaffCaseRuntime | None
    secret: str | None

    def signer_for(self, source: str, purpose: str) -> StaffSigner:
        signer = self.signers.get((source, purpose))
        if signer is None:
            raise StaffCaseError("unavailable")
        signer.guard(purpose)
        return signer

    def require_cut(self, cut: CensusCut) -> None:
        selected = {
            s.source_ref: {i.key_fingerprint: i for i in s.issuers} for s in self.config.dependencies.sources
        }
        if set(selected) != {p.source_ref for p in cut.source_positions}:
            raise StaffCaseError("denied")

        def require(source: str, fingerprint: str, purpose: str) -> None:
            issuer = selected.get(source, {}).get(fingerprint)
            if issuer is None or purpose not in issuer.purposes:
                raise StaffCaseError("denied")

        require(cut.source_ref, cut.proof.key_fingerprint, "scope_complete")
        for material in cut.materials:
            pub = material.publication
            require(pub.source_ref, pub.proof.key_fingerprint, pub.proof.purpose)
            inner = getattr(pub.payload, "proof", None)
            if inner is not None:
                require(pub.source_ref, inner.key_fingerprint, inner.purpose)
        for grant in cut.grants:
            for decision in grant.decisions:
                require(
                    grant.source_ref, decision.decision_proof.key_fingerprint, decision.decision_proof.purpose
                )

    def current(self) -> datetime:
        now = now_utc()
        if now >= self.authority.valid_until:
            raise StaffCaseError("unavailable")
        for client in self.clients.values():
            client.signer.guard("staff-case-publication.v1")
        return now


def load_configuration(path: Path) -> CensusProductionConfiguration:
    raw = protected_read(path, expected=None, maximum=65536)
    return parse_model(CensusProductionConfiguration, strict_loads(raw))


@asynccontextmanager
async def production(
    config: CensusProductionConfiguration, *, prepare: bool
) -> AsyncIterator[CensusProduction]:
    seconds = config.maximum_seconds
    async with AsyncExitStack() as resources:
        loaded = load_materials(config.witness_profile)
        authority = loaded.authority
        signers: dict[tuple[str, str], StaffSigner] = {}
        clients: dict[str, StaffNativeClient] = {}
        for source in config.dependencies.sources:
            for selected in source.issuers:
                entry = authority.entries.get(selected.key_fingerprint)
                if (
                    entry is None
                    or entry.entry_ref != selected.entry_ref
                    or entry.role != "case_issuer"
                    or entry.source_ref != source.source_ref
                    or entry.source_namespace != selected.source_namespace
                    or not set(selected.purposes) <= set(entry.purposes)
                ):
                    raise StaffCaseError("denied")
                if selected.signing_key is not None and prepare:
                    key = signing_key(material(config, selected.signing_key), selected.key_fingerprint)
                    signer = StaffSigner(authority, key, "case_issuer")
                    for purpose in selected.purposes:
                        if (source.source_ref, purpose) in signers:
                            # Do not arbitrarily choose one of multiple issuer namespaces.
                            raise StaffCaseError("invalid")
                        signer.guard(purpose)
                        signers[source.source_ref, purpose] = signer
            entry = authority.entries.get(source.importer_key_fingerprint)
            if (
                entry is None
                or entry.entry_ref != source.importer_entry_ref
                or entry.role != "publication_importer"
                or entry.source_ref != source.source_ref
                or "staff-case-publication.v1" not in entry.purposes
            ):
                raise StaffCaseError("denied")
            key = signing_key(material(config, source.importer_signing_key), source.importer_key_fingerprint)
            material(config, source.importer_certificate)
            material(config, source.importer_tls_key)
            client = StaffNativeClient(
                origin=loaded.manifest.native_origin,
                ca_file=Path(MATERIAL_DIRECTORY) / "native-ca.pem",
                certificate_file=Path(source.importer_certificate.path),
                private_key_file=Path(source.importer_tls_key.path),
                server_spki_sha256=loaded.manifest.native_server_spki_sha256,
                signer=StaffSigner(authority, key, "publication_importer"),
                result_authority=authority,
                configuration_digest=config.dependencies.native_configuration_digest,
                seconds=seconds,
            )
            resources.push_async_callback(_close, client.close, seconds)
            clients[source.source_ref] = client
        resolver = None
        runtime = None
        secret = None
        if prepare:
            # PortalSettings contains SecretStr and ordinary config integers; it
            # is not a number-free signed native record. Reject duplicate keys,
            # then use its existing actual settings validator without logging.
            def pairs(items):
                result = {}
                for key, value in items:
                    if key in result:
                        raise StaffCaseError("invalid")
                    result[key] = value
                return result

            raw_settings = material(config, config.identity_settings)
            parsed_settings = json.loads(raw_settings, object_pairs_hook=pairs)
            settings = PortalSettings.model_validate(parsed_settings)
            if (
                settings.mode != "production"
                or settings.tenant != config.dependencies.scope.tenant
                or settings.issuer != config.witness_profile.issuer
            ):
                raise StaffCaseError("denied")
            adapters = build_human_identity_adapters(settings)
            resources.push_async_callback(_close, adapters.store.close, seconds)
            resources.push_async_callback(_close, adapters.authenticator.close, seconds)
            resolver = HumanSessionResolver(settings, adapters.store)
            runtime = await resources.enter_async_context(staff_runtime(config.witness_profile, settings))
            secret = material(config, config.session_secret).decode("ascii")
            if len(secret) != 43:
                raise StaffCaseError("invalid")
        result = CensusProduction(
            config,
            authority,
            OwnerManifestStaffCensusSource(
                authority, maximum_bytes=config.maximum_source_bytes, maximum_records=config.maximum_records
            ),
            signers,
            clients,
            resolver,
            runtime,
            secret,
        )
        result.current()
        try:
            yield result
        except asyncio.CancelledError:
            raise
