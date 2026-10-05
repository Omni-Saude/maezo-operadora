"""Closed installation contract for ADR-0063's bilateral source plane.

Descriptor comes from independently qualified deployment, never an operation caller.
Evidence received locally does not confer contract, signature or source authority.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

from maezo.portal.engine.profile import canonicalize, strict_loads

from .models import CapabilityContractError, CapabilityRefusalReason

Identifier = Annotated[str, StringConstraints(strict=True, pattern=r"^[a-z_][a-z0-9_]{0,62}$")]
Ref = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=256)]
Digest = Annotated[str, StringConstraints(strict=True, pattern=r"^[0-9a-f]{64}$")]
TABLES = frozenset({"evidence", "source_binding", "authority_proof", "publication", "instrument_head"})


class AuthoritySourceError(CapabilityContractError):
    def __init__(
        self,
        reason: Literal["SOURCE_UNAVAILABLE", "CONTRACT_MISMATCH", "STALE_REVISION", "AUTHORITY_UNPROVEN"]
        | CapabilityRefusalReason,
    ) -> None:
        super().__init__(CapabilityRefusalReason(reason))


class Closed(BaseModel):
    model_config = ConfigDict(
        strict=True, extra="forbid", frozen=True, hide_input_in_errors=True, revalidate_instances="always"
    )


class RelationPin(Closed):
    oid: Annotated[int, Field(gt=0)]


class SourceDescriptor(Closed):
    schema_version: Literal["provider-authority-source.v1"]
    database_oid: Annotated[int, Field(gt=0)]
    schema_name: Identifier
    schema_oid: Annotated[int, Field(gt=0)]
    owner_role: Identifier
    publisher_role: Identifier
    validator_role: Identifier
    reader_role: Identifier
    relation_pins: dict[str, RelationPin]
    immutable_function_oid: Annotated[int, Field(gt=0)]
    immutable_function_sha256: Digest
    publication_function_oid: Annotated[int, Field(gt=0)]
    publication_function_sha256: Digest
    history_function_oid: Annotated[int, Field(gt=0)]
    history_function_sha256: Digest
    tenant_ref: Ref
    legal_entity_ref: Ref
    source_authority_ref: Ref
    source_contract_publication_ref: Ref
    valid_until: datetime

    @field_validator("valid_until")
    @classmethod
    def aware(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("timezone required")
        return value

    @model_validator(mode="after")
    def scope(self) -> SourceDescriptor:
        if self.schema_name in {"maezo_native", "public", "cibseven"}:
            raise ValueError("source namespace unavailable")
        if len({self.owner_role, self.publisher_role, self.validator_role, self.reader_role}) != 4:
            raise ValueError("roles must be separated")
        if set(self.relation_pins) != TABLES or len({x.oid for x in self.relation_pins.values()}) != len(
            TABLES
        ):
            raise ValueError("exact relation pins required")
        return self


class SourceBinding(Closed):
    binding_ref: Ref
    tenant_ref: Ref
    legal_entity_ref: Ref
    provider_ref: Ref
    principal_ref: Ref
    task_ref: Ref
    source_authority_ref: Ref
    policy_revision: Ref
    purpose_policy_ref: Ref
    purpose_ref: Ref
    clause_purposes: tuple[str, ...]
    data_classification: Ref
    source_contract_publication_ref: Ref
    valid_until: datetime

    @field_validator("valid_until")
    @classmethod
    def aware(cls, value: datetime) -> datetime:
        return SourceDescriptor.aware(value)

    @field_validator("clause_purposes")
    @classmethod
    def purposes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        allowed = {
            "payment_prazo",
            "glosa_symmetry",
            "tabela_valor",
            "prior_notice_prazo",
            "rescisao_procedimento",
            "credenciamento_conteudo",
        }
        if not value or len(set(value)) != len(value) or not set(value) <= allowed:
            raise ValueError("closed purposes required")
        return value


class CustodyReceipt(Closed):
    state: Literal["received"]
    evidence_ref: Ref
    snapshot_sha256: Digest
    received_at: datetime


def pack(model: BaseModel) -> bytes:
    result = canonicalize(model.model_dump(mode="json"))
    if len(result) > 65536:
        raise AuthoritySourceError("CONTRACT_MISMATCH")
    return result


def parse_binding(raw: bytes, expected_digest: str) -> SourceBinding:
    try:
        value = SourceBinding.model_validate_json(raw)
        if (
            canonicalize(strict_loads(raw)) != raw
            or pack(value) != raw
            or hashlib.sha256(raw).hexdigest() != expected_digest
        ):
            raise ValueError("integrity unavailable")
        return value
    except Exception:
        raise AuthoritySourceError("AUTHORITY_UNPROVEN") from None


def descriptor_digest(value: SourceDescriptor) -> str:
    # Installation OIDs are integer metadata, outside human/operation wire profile.
    raw = json.dumps(value.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode()).hexdigest()
