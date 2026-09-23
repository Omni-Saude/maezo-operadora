"""Approved SC1 production bootstrap: explicit deployment inputs, no authority defaults."""

from __future__ import annotations

import re
from typing import Annotated, Literal, Self
from urllib.parse import urlsplit

from pydantic import AfterValidator, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from maezo.gateway.external_cases.models import Digest, Ref, Scope, timestamp

from .models import Closed, N, T

MATERIAL_PARENT = "/run/maezo-staff-materials"
MATERIAL_DIRECTORY = MATERIAL_PARENT + "/current"
SCRATCH_DIRECTORY = "/run/maezo-staff-scratch"
MAX_BUNDLE = 65536
PRIVATE_FILES = frozenset(
    {
        "read-signing-key.pem",
        "witness-signing-key.pem",
        "read-client-key.pem",
        "session-lock-dsn.txt",
        "native-witness-dsn.txt",
    }
)
PUBLIC_FILES = frozenset(
    {
        "designation.json",
        "installation-proof.json",
        "installation-root.der",
        "native-ca.pem",
        "read-client-certificate.pem",
        "session-lock-ca.pem",
        "native-witness-ca.pem",
    }
)
FILES = PRIVATE_FILES | PUBLIC_FILES
#: ADR-0060 D3: the PostgreSQL schema of the native `mzo_*` relations (`maezo_native` in
#: dev) is a pin of the v2 manifest and of the deployment settings, compared exactly (D5).
#: Never a shared or system schema: `public` (D-C, refuted), `cibseven` (the engine's own)
#: and the catalog/temp/toast namespaces (`pg_*`, `information_schema`). Same rule as
#: `StaffCaseStore.schema` and `portal-variables.tf`.
RESERVED_SCHEMAS = frozenset({"public", "cibseven", "information_schema"})


def is_native_schema(value: object) -> bool:
    return (
        type(value) is str
        and re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", value) is not None
        and value not in RESERVED_SCHEMAS
        and not value.startswith("pg_")
    )


def _native_schema(value: str) -> str:
    if not is_native_schema(value):
        raise ValueError("native_schema")
    return value


NativeSchema = Annotated[str, AfterValidator(_native_schema)]


class PortalStaffBootstrapError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("portal_staff_bootstrap_unavailable")


def fixed_origin(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.path
        or parsed.query
        or parsed.fragment
        or parsed.port not in (None, 443)
        or value != f"https://{parsed.netloc}"
        or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value)
        or any(c in value for c in "?#\\")
    ):
        raise PortalStaffBootstrapError()
    return value


class PortalProductionSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MAEZO_PORTAL_", frozen=True, extra="forbid")

    capabilities: Literal["identity", "identity,staff_cases", "identity,staff_cases,human"]
    tenant: Ref
    issuer: str
    #: WP-J1-00. The human plane is dark unless this single fixed path is configured
    #: AND the capabilities literal names it; the material bundle itself carries the
    #: keys, connection strings and surface pins (`gateway/human/production_materials.py`).
    human_material_directory: Literal["/run/maezo-human-materials/current"] | None = None
    #: The out-of-band anchor for that bundle, the human counterpart of
    #: `staff_material_version_id` / `staff_public_manifest_sha256`. A bundle attests
    #: itself; these two facts, plus `tenant`, are the second channel that says WHICH
    #: bundle this deployment accepts, and they are compared before any key material is
    #: read (`gateway/human/production_materials.py` `manifest_matches`). Every
    #: revocation-snapshot refresh republishes the manifest and therefore changes the
    #: digest — exactly as it does for staff.
    human_material_version_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9-]{32,64}$")
    human_public_manifest_sha256: Digest | None = None
    #: WP-J1-06 Phase 0. The decision ports (#22 binding / #23 PHI custody / #25
    #: admission) bind only when this second fixed path is configured; without it the
    #: human plane still serves reads and assignments and `submit_decision` refuses,
    #: which is exactly `main`'s behaviour. It may not be named without the human plane.
    decision_material_directory: Literal["/run/maezo-decision-materials/current"] | None = None
    #: The decision plane's out-of-band anchor, complete-or-absent with the directory
    #: above and the exact counterpart of the human/staff pins. This plane holds the
    #: AES-GCM custody keys and the PHI deployment designation, so a self-attesting
    #: bundle here is worth more to an adversary than anywhere else: these two facts
    #: are what say WHICH bundle the deployment accepts, and they are compared before
    #: a byte of vault material is parsed (`decision_materials.decision_manifest_matches`).
    decision_material_version_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9-]{32,64}$")
    decision_public_manifest_sha256: Digest | None = None
    #: WP-J1-04. The document plane (concrete `DocumentAuthority` /
    #: `ProtectedDocumentProvider` and the PHI byte route's provider) binds only when
    #: this third fixed path is configured; without it the document routes keep refusing
    #: exactly as `main` ships them. It may not be named without the human plane, and it
    #: holds the PHI document key, so the same out-of-band anchor discipline applies.
    document_material_directory: Literal["/run/maezo-document-materials/current"] | None = None
    document_material_version_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9-]{32,64}$")
    document_public_manifest_sha256: Digest | None = None
    staff_material_directory: Literal["/run/maezo-staff-materials/current"] | None = None
    staff_material_version_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9-]{32,64}$")
    staff_public_manifest_sha256: Digest | None = None
    staff_root_key_sha256: Digest | None = None
    staff_designation_sha256: Digest | None = None
    staff_native_configuration_sha256: Digest | None = None
    staff_scope: Scope | None = None
    staff_native_origin: str | None = None
    staff_native_server_spki_sha256: Digest | None = None
    staff_native_schema: NativeSchema | None = None
    staff_read_key_sha256: Digest | None = None
    staff_witness_key_sha256: Digest | None = None
    staff_maximum_seconds: int | None = Field(default=None, ge=1, le=10)
    #: INTERIM (`docs/decisions-log.md` DL-0049). The same env var
    #: (`MAEZO_PORTAL_DIRECT_COMPLETION`) that opens the route in `PortalSettings` also
    #: installs the engine completion capability here. The two gates are INDEPENDENT on
    #: purpose: one is the HTTP surface, the other is the capability behind it, and neither
    #: reads the other. Both default off; `scripts/ci/check_portal_direct_completion.py`
    #: reproves turning them on outside `dev-sa-east-1`.
    direct_completion: bool = False
    #: Complete-or-absent with the flag. `{engine}/task/{id}/complete` is where the interim
    #: completion goes, so this is the engine REST base — deployment-owned, never derived.
    direct_completion_engine_origin: str | None = None
    #: An explicit positive operational interval. No default: an invented timeout on a call
    #: that changes engine state is an invented promise about how long a case may hang.
    direct_completion_timeout_seconds: int | None = Field(default=None, ge=1, le=120)

    @model_validator(mode="after")
    def complete_profile(self) -> Self:
        values = [getattr(self, n) for n in type(self).model_fields if n.startswith("staff_")]
        human = [getattr(self, n) for n in type(self).model_fields if n.startswith("human_")]
        # The human profile is complete-or-absent exactly like the staff one: a
        # half-configured plane must not start (WP-J1-00). "Complete" now includes the
        # out-of-band anchor, so the plane cannot start pinned to nothing; and the two
        # planes never share a material version or a manifest digest.
        if all(v is not None for v in human) != (self.capabilities == "identity,staff_cases,human"):
            raise PortalStaffBootstrapError()
        if any(v is not None for v in human) and any(v is None for v in human):
            raise PortalStaffBootstrapError()
        if any(v is not None for v in human) and (
            self.human_material_version_id == self.staff_material_version_id
            or self.human_public_manifest_sha256 == self.staff_public_manifest_sha256
        ):
            raise PortalStaffBootstrapError()
        # The decision plane is an addition to the human plane, never a substitute for
        # it: there is no deployment in which decisions bind while reads do not.
        if self.decision_material_directory is not None and self.human_material_directory is None:
            raise PortalStaffBootstrapError()
        # INTERIM (DL-0049), same discipline as every plane above: complete-or-absent, and an
        # addition to a trusted human plane, never a way to reach one. A deployment that sets
        # the flag without the engine origin and the timeout does not start — better a portal
        # that refuses to boot than a portal whose completion button fails at the first click.
        interim = (
            self.direct_completion,
            self.direct_completion_engine_origin is not None,
            self.direct_completion_timeout_seconds is not None,
        )
        if len(set(interim)) != 1:
            raise PortalStaffBootstrapError()
        if self.direct_completion and self.human_material_directory is None:
            raise PortalStaffBootstrapError()
        # And it is complete-or-absent in its own right: naming the directory without
        # the anchor would start the plane pinned to nothing, which is the whole of
        # V14 MAJOR-2. Its version and digest are distinct from both other planes'.
        decision = [getattr(self, n) for n in type(self).model_fields if n.startswith("decision_")]
        if any(v is not None for v in decision) and any(v is None for v in decision):
            raise PortalStaffBootstrapError()
        if any(v is not None for v in decision) and (
            len(
                {
                    self.decision_material_version_id,
                    self.human_material_version_id,
                    self.staff_material_version_id,
                }
            )
            != 3
            or len(
                {
                    self.decision_public_manifest_sha256,
                    self.human_public_manifest_sha256,
                    self.staff_public_manifest_sha256,
                }
            )
            != 3
        ):
            raise PortalStaffBootstrapError()
        # The document plane (WP-J1-04) is also an addition to the human plane, and it
        # is complete-or-absent in its own right: naming its directory without its anchor
        # would start it pinned to nothing. Its version and digest are distinct from the
        # other three planes'.
        document = [getattr(self, n) for n in type(self).model_fields if n.startswith("document_")]
        if any(v is not None for v in document) and any(v is None for v in document):
            raise PortalStaffBootstrapError()
        if any(v is not None for v in document) and (
            self.document_material_directory is not None
            and (
                len(
                    {
                        self.document_material_version_id,
                        self.decision_material_version_id,
                        self.human_material_version_id,
                        self.staff_material_version_id,
                    }
                )
                != 4
                or len(
                    {
                        self.document_public_manifest_sha256,
                        self.decision_public_manifest_sha256,
                        self.human_public_manifest_sha256,
                        self.staff_public_manifest_sha256,
                    }
                )
                != 4
            )
        ):
            raise PortalStaffBootstrapError()
        if self.capabilities == "identity":
            if any(v is not None for v in values):
                raise PortalStaffBootstrapError()
            return self
        if any(v is None for v in values):
            raise PortalStaffBootstrapError()
        assert self.staff_scope is not None and self.staff_native_origin is not None
        if (
            self.staff_scope.tenant != self.tenant
            or len({self.staff_root_key_sha256, self.staff_read_key_sha256, self.staff_witness_key_sha256})
            != 3
        ):
            raise PortalStaffBootstrapError()
        fixed_origin(self.staff_native_origin)
        return self


class FunctionPin(Closed):
    oid: N
    owner: Ref
    definition_sha256: Digest

    @model_validator(mode="after")
    def valid_pin(self) -> Self:
        if int(self.oid) < 1 or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", self.owner):
            raise PortalStaffBootstrapError()
        return self


class Connection(Closed):
    host: str
    port: N
    database: str
    login: Ref
    tls_server_name: str
    ca_file: Literal["session-lock-ca.pem", "native-witness-ca.pem"]
    function_pin: FunctionPin | None

    @model_validator(mode="after")
    def fixed_connection(self) -> Self:
        if (
            self.host != self.tls_server_name
            or not re.fullmatch(r"[A-Za-z0-9.-]{1,253}", self.host)
            or ".." in self.host
            or not 1 <= int(self.port) <= 65535
            or not self.database
            or len(self.database) > 63
            or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", self.login)
        ):
            raise PortalStaffBootstrapError()
        return self


class NativeRelationPin(Closed):
    oid: N
    owner: Ref

    @model_validator(mode="after")
    def positive_oid(self) -> Self:
        if int(self.oid) < 1 or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", self.owner):
            raise PortalStaffBootstrapError()
        return self


class RevocationSnapshot(Closed):
    scope: Scope
    designation_digest: Digest
    source_ref: Ref
    revision: N
    observed_at: T
    valid_until: T
    revoked_fingerprints: tuple[Digest, ...]

    @model_validator(mode="after")
    def closed_snapshot(self) -> Self:
        if tuple(sorted(set(self.revoked_fingerprints))) != self.revoked_fingerprints or timestamp(
            self.observed_at
        ) >= timestamp(self.valid_until):
            raise PortalStaffBootstrapError()
        return self


class PublicManifest(Closed):
    # v2 (ADR-0060 D3) adds `native_schema`. v1 is refused on load: no v1 package was
    # ever published, so there is nothing to migrate.
    schema_: Literal["portal-staff-material.v2"] = Field(alias="schema")
    material_version_id: str = Field(pattern=r"^[A-Za-z0-9-]{32,64}$")
    scope: Scope
    issuer: str
    issued_at: T
    valid_until: T
    root_key_fingerprint: Digest
    designation_digest: Digest
    native_configuration_digest: Digest
    native_maximum_seconds: N
    read_key_fingerprint: Digest
    witness_key_fingerprint: Digest
    native_origin: str
    native_server_spki_sha256: Digest
    native_schema: NativeSchema
    session_lock_connection: Connection
    native_witness_connection: Connection
    native_relation_pins: dict[str, NativeRelationPin]
    revocation_snapshot: RevocationSnapshot
    # Public material is hashed. Private files have required null entries; their
    # contents are never turned into public evidence/dictionary-attack hashes.
    files: dict[str, Digest | None]

    @model_validator(mode="after")
    def exact_manifest(self) -> Self:
        fixed_origin(self.native_origin)
        if (
            not 1 <= int(self.native_maximum_seconds) <= 10
            or set(self.files) != FILES
            or any(self.files[n] is None for n in PUBLIC_FILES)
            or any(self.files[n] is not None for n in PRIVATE_FILES)
            or set(self.native_relation_pins) != {"mzo_portal_read_membership", "mzo_human_principal"}
            or self.session_lock_connection.ca_file != "session-lock-ca.pem"
            or self.native_witness_connection.ca_file != "native-witness-ca.pem"
            or self.session_lock_connection.function_pin is None
            or self.native_witness_connection.function_pin is not None
            or self.session_lock_connection.login == self.native_witness_connection.login
            or self.revocation_snapshot.scope != self.scope
            or self.revocation_snapshot.designation_digest != self.designation_digest
            or timestamp(self.issued_at) >= timestamp(self.valid_until)
        ):
            raise PortalStaffBootstrapError()
        return self


class SecretBundle(Closed):
    schema_: Literal["portal-staff-secret-bundle.v1"] = Field(alias="schema")
    material_version_id: str = Field(pattern=r"^[A-Za-z0-9-]{32,64}$")
    public_manifest: PublicManifest
    files: dict[str, str] = Field(repr=False)

    @model_validator(mode="after")
    def exact_files(self) -> Self:
        if set(self.files) != FILES or self.material_version_id != self.public_manifest.material_version_id:
            raise PortalStaffBootstrapError()
        return self
