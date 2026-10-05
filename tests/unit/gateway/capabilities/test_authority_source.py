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


class TriggerMetadataRows:
    """UNIT selected catalogue data, not PostgreSQL installation or source authority."""

    def __init__(self, value):
        self.value = value

    def mappings(self):
        return self

    def one(self):
        return self.value

    def one_or_none(self):
        return self.value

    def all(self):
        return self.value


class TriggerMetadataConnection:
    """Reproduce the actual asyncpg `char` codec seen in ROOT's diagnostic.

    Uncast pg_trigger.tgenabled is bytes, even for enabled O. PostgreSQL's
    explicit text projection is text. All unrelated catalogue prerequisites are
    synthetic UNIT values; no receipt/act/publication is constructed or admitted.
    Unknown SQL raises instead of providing a permissive metadata fallback.
    """

    definitions = {
        "immutable_source_record": "UNIT-immutable-function",
        "publish_validated": "UNIT-publication-function",
        "protect_authority_history": "UNIT-history-function",
    }

    def __init__(
        self,
        d,
        *,
        publisher=False,
        enabled=b"O",
        drift=None,
        changed_mask=None,
        guard_changes=None,
        extra_trigger=None,
        missing_guard=None,
        duplicate_guard=None,
        session_replica=False,
        disable_privilege_role=None,
    ):
        self.d = d
        self.publisher = publisher
        self.enabled = enabled
        self.drift = drift
        self.changed_mask = changed_mask
        self.guard_changes = guard_changes
        self.extra_trigger = extra_trigger
        self.missing_guard = missing_guard
        self.duplicate_guard = duplicate_guard
        self.session_replica = session_replica
        self.disable_privilege_role = disable_privilege_role
        self.trigger_keys = []
        self.trigger_relations = []
        self.queries = []

    async def execute(self, statement, params=None):
        sql = str(statement)
        self.queries.append(sql)
        d = self.d
        params = params or {}
        if "AS database_oid" in sql:
            role = d.publisher_role if self.publisher else d.reader_role
            row = dict(login=role, effective=role, database_oid=d.database_oid, temp_oid=0, tls=True)
            if "AS guards_fire" in sql:
                row["guards_fire"] = not self.session_replica
            return TriggerMetadataRows(row)
        if "FROM pg_roles r JOIN pg_namespace" in sql:
            row = dict(
                rolsuper=False,
                rolcreatedb=False,
                rolcreaterole=False,
                rolbypassrls=False,
                rolreplication=False,
                member=False,
                can_create=params["role"] == d.owner_role,
                can_temp=False,
            )
            if "AS can_disable_triggers" in sql:
                row["can_disable_triggers"] = params["role"] == self.disable_privilege_role
            return TriggerMetadataRows(row)
        if "FROM pg_class c JOIN pg_namespace" in sql:
            name = params["name"]
            return TriggerMetadataRows(
                dict(
                    oid=d.relation_pins[name].oid,
                    schema_oid=d.schema_oid,
                    kind="r",
                    schema_owner=d.owner_role,
                    relation_owner=d.owner_role,
                    relrowsecurity=False,
                    extra_acl=False,
                    column_acl=False,
                    reader_select=True,
                    reader_write=False,
                    publisher_select=True,
                    publisher_mutate=False,
                    publisher_insert=name == "evidence",
                    validator_write=name in {"source_binding", "authority_proof"},
                    validator_extra=False,
                    public_table=False,
                    public_schema=False,
                )
            )
        if "p.proname='immutable_source_record'" in sql:
            return TriggerMetadataRows(
                dict(
                    oid=d.immutable_function_oid,
                    definition=self.definitions["immutable_source_record"],
                    owner=d.owner_role,
                    prosecdef=False,
                    public_execute=False,
                )
            )
        if "p.proname='publish_validated'" in sql:
            return TriggerMetadataRows(
                dict(
                    oid=d.publication_function_oid,
                    definition=self.definitions["publish_validated"],
                    owner=d.owner_role,
                    prosecdef=True,
                    proconfig=["search_path=" + d.schema_name],
                    publisher_execute=True,
                    reader_execute=False,
                    extra_acl=False,
                )
            )
        if "p.proname='protect_authority_history'" in sql:
            return TriggerMetadataRows(
                dict(
                    oid=d.history_function_oid,
                    definition=self.definitions["protect_authority_history"],
                    owner=d.owner_role,
                    prosecdef=False,
                    public_execute=False,
                )
            )
        if "FROM pg_trigger" in sql:
            relation = next(name for name, pin in d.relation_pins.items() if pin.oid == params["relation"])
            self.trigger_relations.append(relation)
            declared = {
                "source_binding": [
                    ("binding_history", d.history_function_oid, 19),
                    ("binding_no_delete", d.immutable_function_oid, 42),
                ],
                "authority_proof": [
                    ("proof_history", d.history_function_oid, 19),
                    ("proof_no_delete", d.immutable_function_oid, 42),
                ],
                "evidence": [("evidence_immutable", d.immutable_function_oid, 58)],
                "publication": [("publication_immutable", d.immutable_function_oid, 58)],
                "instrument_head": [],
            }[relation]
            rows = []
            for name, function, mask in declared:
                key = (relation, "history" if function == d.history_function_oid else "immutable")
                if self.missing_guard == key:
                    continue
                raw_enabled = self.drift[1] if self.drift and key == self.drift[0] else self.enabled
                mask = self.changed_mask[1] if self.changed_mask and key == self.changed_mask[0] else mask
                enabled = (
                    raw_enabled.decode("ascii") if "tgenabled::text" in sql and raw_enabled else raw_enabled
                )
                row = dict(
                    tgname=name,
                    tgfoid=function,
                    tgtype=mask,
                    tgenabled=enabled,
                    tgisinternal=False,
                    unconditional=True,
                    tgnargs=0,
                    no_args=True,
                    tgattr="",
                    tgconstraint=0,
                    tgdeferrable=False,
                    tginitdeferred=False,
                    no_transition=True,
                )
                if self.guard_changes and key == self.guard_changes[0]:
                    row.update(self.guard_changes[1])
                if "function" in params and row["tgfoid"] != params["function"]:
                    continue
                self.trigger_keys.append(key)
                # Selected metadata only: old filtered SQL cannot observe omitted guard attributes.
                selected = (
                    row
                    if "tgqual IS NULL AS unconditional" in sql
                    else {"tgtype": row["tgtype"], "tgenabled": row["tgenabled"]}
                )
                rows.append(selected)
                if self.duplicate_guard == key:
                    rows.append(dict(selected))
            if self.extra_trigger == relation and "function" not in params:
                rows.append(
                    dict(
                        tgname="unexpected_guard",
                        tgfoid=9999,
                        tgtype=19,
                        tgenabled="O",
                        tgisinternal=False,
                        unconditional=True,
                        tgnargs=0,
                        no_args=True,
                        tgattr="",
                        tgconstraint=0,
                        tgdeferrable=False,
                        tginitdeferred=False,
                        no_transition=True,
                    )
                )
            return TriggerMetadataRows(rows)
        raise AssertionError("Unexpected catalogue query; no permissive UNIT fallback")


def trigger_descriptor():
    bodies = TriggerMetadataConnection.definitions
    return descriptor(
        immutable_function_sha256=hashlib.sha256(bodies["immutable_source_record"].encode()).hexdigest(),
        publication_function_sha256=hashlib.sha256(bodies["publish_validated"].encode()).hexdigest(),
        history_function_sha256=hashlib.sha256(bodies["protect_authority_history"].encode()).hexdigest(),
    )


@pytest.mark.parametrize("publisher", [False, True])
@pytest.mark.parametrize("enabled", [b"O", b"A"])
async def test_typed_trigger_projection_accepts_real_enabled_codec_through_complete_qualifier(
    publisher, enabled
):
    d = trigger_descriptor()
    db = TriggerMetadataConnection(d, publisher=publisher, enabled=enabled)
    source = PostgresProviderAuthoritySource(
        SimpleNamespace(dialect=SimpleNamespace(name="postgresql")), d, publisher=publisher
    )
    await source.qualify(db)
    assert len(db.trigger_keys) == 6
    assert db.trigger_keys[-1] == ("publication", "immutable")


@pytest.mark.parametrize(
    "key",
    [
        ("source_binding", "history"),
        ("authority_proof", "immutable"),
        ("evidence", "immutable"),
        ("publication", "immutable"),
    ],
)
@pytest.mark.parametrize("enabled", [b"D", b"R", b"?", None])
async def test_typed_trigger_projection_still_refuses_disabled_replica_and_unknown_at_target(key, enabled):
    d = trigger_descriptor()
    db = TriggerMetadataConnection(d, drift=(key, enabled))
    source = PostgresProviderAuthoritySource(SimpleNamespace(dialect=SimpleNamespace(name="postgresql")), d)
    with pytest.raises(AuthoritySourceError) as failure:
        await source.qualify(db)
    assert failure.value.reason == CapabilityRefusalReason.SOURCE_UNAVAILABLE
    assert key in db.trigger_keys
    assert db.trigger_relations[-1] == key[0]  # census must reach the altered table, not fail elsewhere


@pytest.mark.parametrize(
    "key,mask",
    [
        (("source_binding", "history"), 17),
        (("authority_proof", "immutable"), 58),
        (("evidence", "immutable"), 42),
    ],
)
async def test_typed_trigger_projection_preserves_exact_trigger_bitmask(key, mask):
    d = trigger_descriptor()
    db = TriggerMetadataConnection(d, changed_mask=(key, mask))
    source = PostgresProviderAuthoritySource(SimpleNamespace(dialect=SimpleNamespace(name="postgresql")), d)
    with pytest.raises(AuthoritySourceError) as failure:
        await source.qualify(db)
    assert failure.value.reason == CapabilityRefusalReason.SOURCE_UNAVAILABLE
    assert key in db.trigger_keys
    assert db.trigger_relations[-1] == key[0]


@pytest.mark.parametrize(
    "changes",
    [
        {"unconditional": False},
        {"tgnargs": 1, "no_args": False},
        {"no_args": False},
        {"tgattr": "1"},
        {"tgconstraint": 55},
        {"tgdeferrable": True},
        {"tginitdeferred": True},
        {"no_transition": False},
        {"tgisinternal": True},
        {"tgname": "unregistered_name"},
        {"tgfoid": 9999},
    ],
)
async def test_complete_trigger_census_refuses_conditional_arguments_and_partial_guard(changes):
    d = trigger_descriptor()
    db = TriggerMetadataConnection(d, guard_changes=(("source_binding", "history"), changes))
    source = PostgresProviderAuthoritySource(SimpleNamespace(dialect=SimpleNamespace(name="postgresql")), d)
    with pytest.raises(AuthoritySourceError) as failure:
        await source.qualify(db)
    assert failure.value.reason == CapabilityRefusalReason.SOURCE_UNAVAILABLE
    assert db.trigger_relations[-1] == "source_binding"


@pytest.mark.parametrize("relation", sorted(TABLES))
async def test_complete_trigger_census_refuses_unexpected_function_on_every_owned_table(relation):
    d = trigger_descriptor()
    db = TriggerMetadataConnection(d, extra_trigger=relation)
    source = PostgresProviderAuthoritySource(SimpleNamespace(dialect=SimpleNamespace(name="postgresql")), d)
    with pytest.raises(AuthoritySourceError) as failure:
        await source.qualify(db)
    assert failure.value.reason == CapabilityRefusalReason.SOURCE_UNAVAILABLE
    assert db.trigger_relations[-1] == relation


@pytest.mark.parametrize("field", ["missing_guard", "duplicate_guard"])
async def test_complete_trigger_census_requires_exact_registered_count(field):
    d = trigger_descriptor()
    db = TriggerMetadataConnection(d, **{field: ("evidence", "immutable")})
    source = PostgresProviderAuthoritySource(SimpleNamespace(dialect=SimpleNamespace(name="postgresql")), d)
    with pytest.raises(AuthoritySourceError) as failure:
        await source.qualify(db)
    assert failure.value.reason == CapabilityRefusalReason.SOURCE_UNAVAILABLE
    assert db.trigger_relations[-1] == "evidence"


async def test_trigger_guards_must_fire_in_current_session():
    d = trigger_descriptor()
    db = TriggerMetadataConnection(d, session_replica=True)
    source = PostgresProviderAuthoritySource(SimpleNamespace(dialect=SimpleNamespace(name="postgresql")), d)
    with pytest.raises(AuthoritySourceError):
        await source.qualify(db)
    assert db.trigger_relations == []


@pytest.mark.parametrize("role_field", ["owner_role", "reader_role", "publisher_role", "validator_role"])
async def test_source_roles_cannot_disable_trigger_execution_via_parameter_set(role_field):
    d = trigger_descriptor()
    db = TriggerMetadataConnection(d, disable_privilege_role=getattr(d, role_field))
    source = PostgresProviderAuthoritySource(SimpleNamespace(dialect=SimpleNamespace(name="postgresql")), d)
    with pytest.raises(AuthoritySourceError):
        await source.qualify(db)
    assert db.trigger_relations == []
