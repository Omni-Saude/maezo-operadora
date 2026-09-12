"""Closed, number-free Q2 wire. Parsing proves shape, never producer authority.

Trace: reviewed Q2 contract, continuity delta Q2-NATIVE-READ-01; ADR-0049.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import types
from datetime import UTC, datetime
from typing import Annotated, Any, Literal, Union, get_args, get_origin

from pydantic import BaseModel, ConfigDict, Field, model_validator

from maezo.portal.contracts.models import (
    MembershipBinding,
    OpaqueRef,
    PagtoAdmissibilityEvidence,
    Revision,
    Sha256Digest,
    SubjectBinding,
)
from maezo.portal.engine.profile import ProfileError, canonicalize, strict_loads

from .models import AuthoritativeTask, CurrentTaskAuthority, Scope
from .models import Closed as _Closed
from .queue import CatalogExpectation, CatalogTrustAnchor, QueueBinding, TaskDisclosureGrant


class Closed(_Closed):
    # Internal alias-aware revalidation. _decode still requires exact WIRE aliases.
    model_config = ConfigDict(validate_by_name=True)


Operation = Literal["catalog", "discover", "task", "authority", "disclosure"]
PublicationKind = Literal["catalog-designate", "catalog-revoke", "membership", "resource", "revoke-key"]
_TIME = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z")
_DECIMAL = re.compile(r"0|[1-9][0-9]*")


def utc(value: datetime) -> str:
    if value.tzinfo is None:
        raise ProfileError("invalid read time")
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def instant(value: object) -> datetime:
    if not isinstance(value, str) or not _TIME.fullmatch(value):
        raise ProfileError("invalid read time")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ProfileError("invalid read time") from None
    if utc(result) != value:
        raise ProfileError("invalid read time")
    return result


def wire(value: Any) -> Any:
    """Lossless typed encoding; financial decimal RootModel remains a string."""
    if isinstance(value, BaseModel):
        value = (
            type(value)
            .model_validate(value.model_dump(mode="python", by_alias=True))
            .model_dump(mode="python", by_alias=True)
        )
    if isinstance(value, datetime):
        return utc(value)
    if type(value) is int:
        return str(value)
    if isinstance(value, (tuple, list)):
        return [wire(x) for x in value]
    if isinstance(value, dict):
        return {k: wire(v) for k, v in value.items()}
    if value is None or type(value) in (bool, str):
        return value
    raise ProfileError("invalid read value")


def _decode(value: Any, annotation: Any) -> Any:
    origin, args = get_origin(annotation), get_args(annotation)
    if origin is Annotated:
        return _decode(value, args[0])
    if origin in (Union, types.UnionType):
        if value is None and type(None) in args:
            return None
        for option in args:
            if option is not type(None):
                try:
                    return _decode(value, option)
                except (ProfileError, TypeError, ValueError):
                    pass
        raise ProfileError("invalid read union")
    if origin is Literal and args and type(args[0]) is int:
        annotation = int
    if annotation is int:
        if type(value) is not str or not _DECIMAL.fullmatch(value) or len(value) > 19:
            raise ProfileError("invalid native integer")
        result = int(value)
        if result > 2**63 - 1:
            raise ProfileError("invalid native integer")
        return result
    if annotation is datetime:
        return utc(instant(value))
    if origin in (tuple, list):
        if type(value) is not list:
            raise ProfileError("invalid read array")
        return [_decode(v, args[0]) for v in value]
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        if annotation.__pydantic_root_model__:
            return _decode(value, annotation.model_fields["root"].annotation)
        if type(value) is not dict:
            raise ProfileError("invalid read object")
        fields = {f.alias or k: f for k, f in annotation.model_fields.items()}
        if value.keys() != fields.keys():
            raise ProfileError("invalid read members")
        return {k: _decode(v, fields[k].annotation) for k, v in value.items()}
    return value


def parse_model[T: BaseModel](model: type[T], value: Any) -> T:
    try:
        result = model.model_validate_json(json.dumps(_decode(value, model)), strict=True)
        if canonicalize(wire(result)) != canonicalize(value):
            raise ProfileError("noncanonical typed read")
        return result
    except (ValueError, TypeError, OverflowError):
        raise ProfileError("invalid closed read record") from None


def digest(value: Any) -> str:
    return hashlib.sha256(canonicalize(wire(value))).hexdigest()


def b64decode(value: object, *, url: bool = False, size: int | None = None) -> bytes:
    if type(value) is not str:
        raise ProfileError("invalid read encoding")
    try:
        if url:
            if not re.fullmatch(r"[A-Za-z0-9_-]+", value):
                raise ValueError
            raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
            encoded = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
        else:
            raw = base64.b64decode(value, validate=True)
            encoded = base64.b64encode(raw).decode("ascii")
        if encoded != value or (size is not None and len(raw) != size) or len(raw) > 65536:
            raise ValueError
        return raw
    except (ValueError, UnicodeError):
        raise ProfileError("invalid read encoding") from None


class SourceProvenance(Closed):
    publisher_ref: OpaqueRef
    source_ref: OpaqueRef
    source_revision: Revision
    source_digest: Sha256Digest
    receipt_ref: OpaqueRef
    observed_at: datetime
    valid_until: datetime


class ArtifactPin(Closed):
    artifact_ref: OpaqueRef
    digest: Sha256Digest


class Artifact(ArtifactPin):
    bytes_base64: str = Field(repr=False)

    @model_validator(mode="after")
    def authentic_bytes(self) -> Artifact:
        if hashlib.sha256(b64decode(self.bytes_base64)).hexdigest() != self.digest:
            raise ValueError("artifact digest mismatch")
        return self


class GroupDomain(Closed):
    kind: Literal["static", "dmn"]
    groups: tuple[OpaqueRef, ...]
    dmn_definition_id: OpaqueRef | None
    dmn_definition_key: OpaqueRef | None
    dmn_definition_version: int | None
    dmn_resource_digest: Sha256Digest | None

    @model_validator(mode="after")
    def complete(self) -> GroupDomain:
        pins = (
            self.dmn_definition_id,
            self.dmn_definition_key,
            self.dmn_definition_version,
            self.dmn_resource_digest,
        )
        if not self.groups or len(set(self.groups)) != len(self.groups):
            raise ValueError("invalid group domain")
        if (self.kind == "static" and any(x is not None for x in pins)) or (
            self.kind == "dmn"
            and (any(x is None for x in pins) or not 0 < int(self.dmn_definition_version or 0) < 2**31)
        ):
            raise ValueError("invalid group provenance")
        return self


class ReadCatalogEntry(Closed):
    process_definition_id: OpaqueRef
    process_definition_key: OpaqueRef
    process_definition_version: int = Field(gt=0, lt=2**31)
    process_definition_digest: Sha256Digest
    task_definition_key: OpaqueRef
    form_key: OpaqueRef
    form_version: int = Field(gt=0, lt=2**31)
    form_digest: Sha256Digest
    form_source_status: str
    allowed_inputs: tuple[OpaqueRef, ...]
    required_roles: tuple[OpaqueRef, ...] = Field(min_length=1)
    subject_policy: ArtifactPin
    consent_policy: ArtifactPin
    resource_policy: ArtifactPin
    disclosure_policy: ArtifactPin
    group_domain: GroupDomain
    opaque_task_id_policy: ArtifactPin

    @model_validator(mode="after")
    def compiled_binding(self) -> ReadCatalogEntry:
        from maezo.portal.contracts.models import _INPUTS_BY_FORM, _binding_for

        if (self.form_key, self.form_source_status) != _binding_for(
            self.process_definition_key, self.task_definition_key
        ):
            raise ValueError("unrecognized read binding")
        if tuple(self.allowed_inputs) != _INPUTS_BY_FORM[self.form_key]:  # type: ignore[index]
            raise ValueError("invalid read inputs")
        return self


class ReadCatalogArtifact(Closed):
    schema_: Literal["portal-read-catalog.v1"] = Field(alias="schema")
    catalog_ref: OpaqueRef
    publisher_ref: OpaqueRef
    entries: tuple[ReadCatalogEntry, ...]
    policies: tuple[Artifact, ...]
    forms: tuple[Artifact, ...]
    deployment_receipt_ref: OpaqueRef
    deployment_receipt_digest: Sha256Digest

    @model_validator(mode="after")
    def complete(self) -> ReadCatalogArtifact:
        if len({(e.process_definition_id, e.task_definition_key) for e in self.entries}) != len(self.entries):
            raise ValueError("duplicate read catalog entry")
        policies = {(p.artifact_ref, p.digest) for p in self.policies}
        required = {
            (p.artifact_ref, p.digest)
            for e in self.entries
            for p in (
                e.subject_policy,
                e.consent_policy,
                e.resource_policy,
                e.disclosure_policy,
                e.opaque_task_id_policy,
            )
        }
        forms = {(f.artifact_ref, f.digest) for f in self.forms}
        if (
            policies != required
            or len(policies) != len(self.policies)
            or forms != {(e.form_key, e.form_digest) for e in self.entries}
            or len(forms) != len(self.forms)
        ):
            raise ValueError("incomplete catalog artifacts")
        return self


class CatalogValue(Closed):
    expectation: CatalogExpectation
    catalog_artifact_base64: str = Field(repr=False)

    def verified_artifact(self, anchor: CatalogTrustAnchor) -> ReadCatalogArtifact:
        raw = b64decode(self.catalog_artifact_base64)
        artifact = parse_model(ReadCatalogArtifact, strict_loads(raw))
        if (
            canonicalize(wire(artifact)) != raw
            or hashlib.sha256(raw).hexdigest() != self.expectation.catalog_digest
            or self.expectation.anchor != anchor
            or artifact.catalog_ref != anchor.catalog_ref
            or artifact.publisher_ref != anchor.publisher_ref
        ):
            raise ProfileError("read designation mismatch")
        return artifact


class MembershipProjection(Closed):
    principal_ref: OpaqueRef
    issuer: str = Field(repr=False)
    subject: OpaqueRef = Field(repr=False)
    membership_revision: Revision
    audience: Literal["staff", "beneficiary", "provider"]
    memberships: tuple[MembershipBinding, ...]
    subject_bindings: tuple[SubjectBinding, ...]
    state: Literal["active", "revoked"]
    reviewed_until: datetime


class ResourceIdentityGrant(Closed):
    issuer: str = Field(repr=False)
    subject: OpaqueRef = Field(repr=False)
    principal_ref: OpaqueRef
    membership_revision: Revision
    consent_scopes: tuple[OpaqueRef, ...]
    decision_receipt_ref: OpaqueRef
    decision_digest: Sha256Digest
    valid_until: datetime


class FullTaskClassification(Closed):
    classification_ref: OpaqueRef
    classification_digest: Sha256Digest
    policy_ref: OpaqueRef
    policy_digest: Sha256Digest
    projection: Literal["full_task_detail.v1"]
    fields_digest: Sha256Digest
    valid_until: datetime


class ResourceProjection(Closed):
    task_id: OpaqueRef
    process_definition_id: OpaqueRef
    process_definition_digest: Sha256Digest
    observed_task_revision: Revision
    evidence_ref: OpaqueRef
    evidence_revision: Revision
    evidence_digest: Sha256Digest
    resource_ref: OpaqueRef
    resource_revision: Revision
    resource_digest: Sha256Digest
    resource_policy: ArtifactPin
    classification: FullTaskClassification
    required_subject_bindings: tuple[SubjectBinding, ...]
    required_consent_scopes: tuple[OpaqueRef, ...]
    positive_grants: tuple[ResourceIdentityGrant, ...]
    read_only_evidence: PagtoAdmissibilityEvidence | None
    state: Literal["complete", "revoked"]
    valid_until: datetime


class CatalogDesignation(Closed):
    catalog_ref: OpaqueRef
    catalog_revision: Revision
    catalog_digest: Sha256Digest
    catalog_artifact_base64: str = Field(repr=False)
    deployment_receipt_ref: OpaqueRef
    deployment_receipt_digest: Sha256Digest
    valid_until: datetime


class CatalogRevocation(Closed):
    catalog_ref: OpaqueRef
    expected_catalog_revision: Revision


class KeyRevocation(Closed):
    key_fingerprint: Sha256Digest


class ReadContextBinding(Closed):
    scope: Scope
    engine_name: OpaqueRef
    database_incarnation: OpaqueRef
    read_deployment_ref: OpaqueRef
    read_deployment_digest: Sha256Digest
    runtime_admission_generation: Revision
    read_context_id: str
    requester: Requester  # defined below, rebuilt after definition


class Requester(Closed):
    issuer: OpaqueRef
    key_id: OpaqueRef
    public_key_sha256: Sha256Digest
    peer_spki_sha256: Sha256Digest


ReadContextBinding.model_rebuild()


class ContinuityCeiling(Closed):
    kind: Literal[
        "native_admission",
        "requester_key",
        "request_envelope",
        "native_key",
        "catalog",
        "resource",
        "evidence",
        "classification",
        "membership",
        "task_predecessor",
        "authority_predecessor",
    ]
    source_ref: OpaqueRef
    source_revision: Revision
    source_digest: Sha256Digest
    observed_at: datetime
    valid_until: datetime


class ContinuityClaims(Closed):
    schema_: Literal["portal-native-read-continuity.v1"] = Field(alias="schema")
    algorithm: Literal["HMAC-SHA256"]
    stage: Literal["task", "authority"]
    key_id: OpaqueRef
    binding: ReadContextBinding
    origin_request_digest: Sha256Digest
    task_digest: Sha256Digest
    snapshot_digest: Sha256Digest
    snapshot_at: datetime
    native_task_state_digest: Sha256Digest
    catalog_state_digest: Sha256Digest
    task_continuity_digest: Sha256Digest | None
    authority_digest: Sha256Digest | None
    principal_digest: Sha256Digest | None
    native_authority_state_digest: Sha256Digest | None
    issued_at: datetime
    valid_until: datetime
    ceilings: tuple[ContinuityCeiling, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def chain(self) -> ContinuityClaims:
        b64decode(self.binding.read_context_id, url=True, size=32)
        values = (
            self.task_continuity_digest,
            self.authority_digest,
            self.principal_digest,
            self.native_authority_state_digest,
        )
        if (self.stage == "task" and any(v is not None for v in values)) or (
            self.stage == "authority" and any(v is None for v in values)
        ):
            raise ValueError("invalid read stage")
        identities = [(c.kind, c.source_ref, str(c.source_revision), c.source_digest) for c in self.ceilings]
        if identities != sorted(set(identities), key=lambda row: tuple(v.encode("utf-8") for v in row)):
            raise ValueError("invalid read ceilings")
        if any(
            not c.observed_at <= self.issued_at < c.valid_until for c in self.ceilings
        ) or self.valid_until != min(c.valid_until for c in self.ceilings):
            raise ValueError("invalid read lifetime")
        return self


class NativeContinuity(Closed):
    claims: ContinuityClaims = Field(repr=False)
    mac: Sha256Digest = Field(repr=False)


class TaskValue(Closed):
    catalog: CatalogValue
    task: AuthoritativeTask = Field(repr=False)
    task_continuity: NativeContinuity = Field(repr=False)


class AuthorityValue(Closed):
    catalog: CatalogValue
    authority: CurrentTaskAuthority = Field(repr=False)
    task_continuity_digest: Sha256Digest
    authority_continuity: NativeContinuity = Field(repr=False)


class DisclosureValue(Closed):
    catalog: CatalogValue
    grant: TaskDisclosureGrant = Field(repr=False)
    task_continuity_digest: Sha256Digest
    authority_continuity_digest: Sha256Digest


class DiscoverValue(Closed):
    binding: QueueBinding
    task_ids: tuple[OpaqueRef, ...]
    after_task_id: OpaqueRef | None


VALUE_TYPES: dict[str, type[BaseModel]] = {
    "catalog": CatalogValue,
    "discover": DiscoverValue,
    "task": TaskValue,
    "authority": AuthorityValue,
    "disclosure": DisclosureValue,
}
PAYLOAD_TYPES: dict[str, type[BaseModel]] = {
    "catalog-designate": CatalogDesignation,
    "catalog-revoke": CatalogRevocation,
    "membership": MembershipProjection,
    "resource": ResourceProjection,
    "revoke-key": KeyRevocation,
}
