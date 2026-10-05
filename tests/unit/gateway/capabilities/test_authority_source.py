"""Installation and integrity negative controls; these doubles do not qualify a source."""

import hashlib
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from maezo.gateway.capabilities.authority_postgres import PostgresProviderAuthoritySource
from maezo.gateway.capabilities.authority_source import (
    TABLES,
    AuthoritySourceError,
    SourceBinding,
    SourceDescriptor,
    pack,
    parse_binding,
)
from maezo.gateway.capabilities.models import CapabilityContractError, CapabilityRefusalReason


def descriptor(**changes):
    data = dict(
        schema_version="provider-authority-source.v1",
        database_oid=123,
        schema_oid=234,
        schema_name="authority_test",
        owner_role="source_owner",
        publisher_role="source_publisher",
        validator_role="source_validator",
        reader_role="source_reader",
        relation_pins={name: {"oid": index + 1000} for index, name in enumerate(sorted(TABLES))},
        immutable_function_oid=432,
        immutable_function_sha256="a" * 64,
        publication_function_oid=433,
        publication_function_sha256="b" * 64,
        history_function_oid=434,
        history_function_sha256="c" * 64,
        tenant_ref="tenant-a",
        legal_entity_ref="legal-a",
        source_authority_ref="source-a",
        source_contract_publication_ref="publication-a",
        valid_until=datetime.now(UTC) + timedelta(hours=1),
    )
    return SourceDescriptor.model_validate(data | changes)


def binding():
    return SourceBinding(
        binding_ref="binding",
        tenant_ref="tenant-a",
        legal_entity_ref="legal-a",
        provider_ref="provider-a",
        principal_ref="actor",
        task_ref="task",
        source_authority_ref="source-a",
        policy_revision="1",
        purpose_policy_ref="policy-a",
        purpose_ref="contract-administration",
        clause_purposes=("payment_prazo",),
        data_classification="administrative",
        source_contract_publication_ref="publication-a",
        valid_until=datetime.now(UTC) + timedelta(minutes=5),
    )


@pytest.mark.parametrize("schema", ["public", "maezo_native", "cibseven", "schema; DROP TABLE proof"])
def test_native_or_injected_schema_cannot_be_authority_source(schema):
    with pytest.raises(ValidationError):
        descriptor(schema_name=schema)


@pytest.mark.parametrize("field", ["publisher_role", "validator_role", "reader_role"])
def test_owner_role_cannot_double_as_runtime_or_validation(field):
    with pytest.raises(ValidationError):
        descriptor(**{field: "source_owner"})


def test_source_reparses_constructed_descriptor_before_any_io():
    forged = descriptor().model_copy(update={"schema_name": "maezo_native"})
    with pytest.raises(ValidationError):
        PostgresProviderAuthoritySource(SimpleNamespace(), forged)


def test_pin_map_requires_exact_relations_and_distinct_oids():
    d = descriptor().model_dump()
    d["relation_pins"].pop("authority_proof")
    with pytest.raises(ValidationError):
        SourceDescriptor.model_validate(d)
    d = descriptor().model_dump()
    d["relation_pins"]["authority_proof"]["oid"] = d["relation_pins"]["evidence"]["oid"]
    with pytest.raises(ValidationError):
        SourceDescriptor.model_validate(d)


def test_binding_bytes_digest_and_duplicate_json_are_not_caller_proof():
    raw = pack(binding())
    digest = hashlib.sha256(raw).hexdigest()
    assert parse_binding(raw, digest) == binding().model_copy(
        update={"valid_until": parse_binding(raw, digest).valid_until}
    )
    for forged, fingerprint in [(raw, "b" * 64), (b'{"binding_ref":"a","binding_ref":"b"}', digest)]:
        with pytest.raises(AuthoritySourceError) as failure:
            parse_binding(forged, fingerprint)
        assert failure.value.reason == CapabilityRefusalReason.AUTHORITY_UNPROVEN


def test_unpublished_or_combined_purpose_string_denied():
    with pytest.raises(ValidationError):
        SourceBinding.model_validate(
            binding().model_dump() | {"clause_purposes": ("payment_prazo,tabela_valor",)}
        )


@pytest.mark.asyncio
async def test_descriptor_mutation_is_refused_before_database_query():
    source = PostgresProviderAuthoritySource(SimpleNamespace(), descriptor())
    source.descriptor.relation_pins.pop("authority_proof")
    with pytest.raises(CapabilityContractError) as failure:
        await source.qualify(SimpleNamespace())
    assert failure.value.reason == CapabilityRefusalReason.AUTHORITY_UNPROVEN


@pytest.mark.asyncio
async def test_expired_descriptor_fails_before_source_io():
    source = PostgresProviderAuthoritySource(
        SimpleNamespace(dialect=SimpleNamespace(name="postgresql")),
        descriptor(valid_until=datetime.now(UTC) - timedelta(seconds=1)),
    )
    with pytest.raises(AuthoritySourceError) as failure:
        await source.qualify(SimpleNamespace())
    assert failure.value.reason == CapabilityRefusalReason.SOURCE_UNAVAILABLE
