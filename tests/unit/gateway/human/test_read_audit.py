"""DL-0150: o registro de LEITURA (quem leu o que e quando) na cadeia de auditoria do tenant.

Pendencia (a) do DL-0050. O que se prova aqui, sem banco (o banco real esta em
`tests/integration/gateway/test_read_audit_live_pg.py`):

* cada leitura de contexto bem-sucedida gera EXATAMENTE um registro, com principal, recurso, codigo
  da leitura e hora;
* o registro so' tem as chaves da lista fechada `READ_AUDIT_DETAIL_FIELDS` e nenhum valor lido;
* leitura negada (outro grupo, tarefa que nao e' de escalonamento, motor falhando, sessao ruim)
  nao gera registro;
* fail-closed: sem sink instalado a leitura recusa ANTES de perguntar ao motor; sink que falha
  recusa a leitura e o contexto nao sai.
"""

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from tests.unit.gateway.human.test_escalation_context import (
    RecordingReadAudit,
    RecordingSource,
    gateway,
    read,
)
from tests.unit.gateway.human.test_gateway import SCOPE, SECRET, setup
from tests.unit.gateway.human.test_task_completion_gateway import (
    CLINICAL_GROUP,
    HUMAN_GROUP,
    escalation_snapshot,
    staff_in,
)

from maezo.gateway.human.errors import GatewayRefusalError
from maezo.gateway.human.queue import ReadRefusalError
from maezo.gateway.human.read_audit import (
    READ_AUDIT_DETAIL_FIELDS,
    ReadAccessEntry,
    read_access_record,
)
from maezo.gateway.human.read_audit_postgres import PostgresReadAccessAudit
from maezo.portal.contracts.context import TaskContextResponse

pytestmark = pytest.mark.asyncio

SUMMARY = "Idoso com dor no peito."

#: A lista permitida ESCRITA AQUI, nao importada: um campo novo no codigo (inclusive na constante
#: `READ_AUDIT_DETAIL_FIELDS`) derruba este teste e obriga a decidir de novo.
ALLOWED = {
    "decision_ref",
    "principal_kind",
    "principal_ref",
    "session_ref",
    "membership_revision",
    "workload_ref",
    "operation",
    "read_kind",
    "resource_kind",
    "resource_ref",
    "read_at",
}


async def test_a_successful_context_read_writes_exactly_one_record_with_who_what_and_when():
    audit = RecordingReadAudit()
    g = await gateway(source=RecordingSource(), audit=audit)
    before = datetime.now(UTC)
    response = await read(g)
    assert isinstance(response, TaskContextResponse)
    assert len(audit.entries) == 1
    entry = audit.entries[0]
    resolved = await g._resolver.resolve(SECRET)
    # QUEM — as referencias pseudonimas do servidor, nunca o subject do IdP.
    assert entry.principal_ref == resolved.principal.principal_ref
    assert entry.session_ref == resolved.principal.session_ref
    assert entry.membership_revision == resolved.principal.membership_revision
    assert resolved.principal.subject not in (entry.principal_ref, entry.session_ref)
    # O QUE — id tecnico da tarefa e o codigo fechado da leitura.
    assert (entry.read_kind, entry.resource_ref) == ("task_context", "task-1")
    assert entry.scope == SCOPE
    # QUANDO — o instante da leitura, o mesmo `observed_at` que a resposta carrega.
    assert before - timedelta(seconds=1) <= entry.read_at <= datetime.now(UTC)
    assert entry.read_at == response.observed_at


async def test_two_reads_are_two_records_not_one_deduplicated():
    audit = RecordingReadAudit()
    g = await gateway(source=RecordingSource(), audit=audit)
    await read(g)
    await read(g)
    assert [e.resource_ref for e in audit.entries] == ["task-1", "task-1"]


async def test_the_record_carries_only_the_allowed_fields_and_no_phi():
    audit = RecordingReadAudit()
    g = await gateway(source=RecordingSource(), audit=audit)
    await read(g)
    record = read_access_record(audit.entries[0], workload_ref=SCOPE.workload_ref)
    assert set(record.details) == ALLOWED == READ_AUDIT_DETAIL_FIELDS
    assert record.action == "portal_read.task_context"
    assert record.decision == "ALLOW"
    assert record.tenant_id == SCOPE.tenant
    assert record.timestamp == audit.entries[0].read_at
    serialized = json.dumps(record.details, sort_keys=True) + record.action + record.agent_id
    resolved = await g._resolver.resolve(SECRET)
    for leaked in (
        SUMMARY,
        "dor no peito",
        "red_flag_clinico",
        "grave",
        "plantao-clinico",
        resolved.principal.subject,
        resolved.principal.issuer,
    ):
        assert leaked not in serialized, leaked
    assert record.details["resource_kind"] == "task"
    assert record.details["read_kind"] == "task_context"
    assert record.details["decision_ref"] == "DL-0150"


async def test_a_member_of_another_group_writes_no_record():
    audit = RecordingReadAudit()
    g = await gateway(source=RecordingSource(), audit=audit, member=staff_in(HUMAN_GROUP))
    with pytest.raises(ReadRefusalError):
        await read(g)
    assert audit.entries == []


async def test_a_task_that_is_not_an_escalation_writes_no_record():
    audit = RecordingReadAudit()
    g, *_ = await setup()  # AUTH task
    g._escalation_context = RecordingSource()
    g._read_audit = audit
    with pytest.raises(ReadRefusalError):
        await read(g)
    assert audit.entries == []


@pytest.mark.parametrize(
    "fail", [GatewayRefusalError("task_unavailable"), RuntimeError("motor caiu")], ids=["absent", "error"]
)
async def test_an_engine_failure_writes_no_record(fail):
    audit = RecordingReadAudit()
    g = await gateway(source=RecordingSource(fail=fail), audit=audit)
    with pytest.raises(ReadRefusalError):
        await read(g)
    assert audit.entries == []


async def test_a_bad_session_writes_no_record():
    audit = RecordingReadAudit()
    g = await gateway(source=RecordingSource(), audit=audit)
    with pytest.raises(ReadRefusalError):
        await read(g, session_secret="x" * 43)
    assert audit.entries == []


async def test_a_render_failure_writes_no_record():
    audit = RecordingReadAudit()
    g = await gateway(source=RecordingSource(), audit=audit)

    def broken(_):
        raise ValueError("render")

    with pytest.raises(ReadRefusalError):
        await read(g, render=broken)
    assert audit.entries == []


async def test_without_a_sink_the_read_refuses_before_the_engine_is_asked():
    source = RecordingSource()
    g = await gateway(source=source, audit=None)
    with pytest.raises(ReadRefusalError) as refusal:
        await read(g)
    assert refusal.value.code == "read_dependency_unavailable"
    assert source.calls == []


async def test_a_failing_sink_refuses_the_read_and_the_context_does_not_leave():
    source = RecordingSource()
    audit = RecordingReadAudit(fail=RuntimeError("audit_chain down"))
    g = await gateway(source=source, audit=audit)
    delivered: list[TaskContextResponse] = []

    def keep(value):
        delivered.append(value)
        return value

    with pytest.raises(ReadRefusalError) as refusal:
        await read(g, render=keep)
    assert refusal.value.code == "read_dependency_unavailable"
    assert "audit_chain" not in str(refusal.value)
    # The render ran (it is pure), but the caller got a refusal, not the context.
    assert source.calls == ["task-1"] and len(delivered) == 1


async def test_a_sink_of_another_scope_cannot_be_installed():
    other = RecordingReadAudit()
    other.scope = SCOPE.model_copy(update={"tenant": "other-tenant"})
    g, *_ = await setup(snap=escalation_snapshot(), member=staff_in(CLINICAL_GROUP))
    with pytest.raises(GatewayRefusalError) as refusal:
        type(g)(
            resolver=g._resolver,
            scope=g._scope,
            ports=g._ports,
            credentials=g._credentials,
            read_audit=other,
        )
    assert refusal.value.code == "credential_scope_mismatch"


async def test_a_sink_swapped_for_another_scope_after_construction_refuses_the_read():
    other = RecordingReadAudit()
    other.scope = SCOPE.model_copy(update={"workload_ref": "other-workload"})
    source = RecordingSource()
    g = await gateway(source=source, audit=other)
    with pytest.raises(ReadRefusalError):
        await read(g)
    assert source.calls == [] and other.entries == []


# ---------------------------------------------------------------------------------------
# Composition: the PHI context read never composes without the record.
# ---------------------------------------------------------------------------------------
async def test_the_composition_refuses_a_context_source_without_a_read_audit():
    from maezo.gateway.human.engine_reads import EngineReadComposition

    with pytest.raises(ReadRefusalError):
        EngineReadComposition(
            new_bundle=lambda: None,  # type: ignore[arg-type,return-value]
            command_credentials=object(),  # type: ignore[arg-type]
            command_admission=object(),  # type: ignore[arg-type]
            transport_pool=httpx.MockTransport(lambda r: httpx.Response(204)),
            escalation_context=RecordingSource(),
        )


async def test_the_composition_exposes_the_installed_read_audit():
    from maezo.gateway.human.engine_reads import EngineReadComposition

    audit = RecordingReadAudit()
    composition = EngineReadComposition(
        new_bundle=lambda: None,  # type: ignore[arg-type,return-value]
        command_credentials=object(),  # type: ignore[arg-type]
        command_admission=object(),  # type: ignore[arg-type]
        transport_pool=httpx.MockTransport(lambda r: httpx.Response(204)),
        escalation_context=RecordingSource(),
        read_audit=audit,
    )
    assert composition.read_audit is audit


# ---------------------------------------------------------------------------------------
# The Postgres adapter: same sink, `emit` (not `emit_once`), refuses a foreign entry.
# ---------------------------------------------------------------------------------------
#: `PostgresAuditSink` takes the tenant as a schema name (`[a-z][a-z0-9_]*`).
PG_SCOPE = SCOPE.model_copy(update={"tenant": "test_tenant"})


def _entry(**changes):
    values = dict(
        scope=PG_SCOPE,
        read_kind="task_context",
        resource_ref="task-1",
        principal_ref="principal-1",
        session_ref="session-1",
        membership_revision=3,
        read_at=datetime(2026, 10, 19, 12, 0, tzinfo=UTC),
    )
    values.update(changes)
    return ReadAccessEntry(**values)


async def test_the_postgres_adapter_emits_one_link_per_record_through_the_tenant_sink():
    adapter = PostgresReadAccessAudit(scope=PG_SCOPE, pool=object())  # type: ignore[arg-type]
    emitted = []

    async def emit(record):
        emitted.append(record)
        return "h" * 64

    async def emit_once_status(*_a, **_k):  # pragma: no cover - would be the wrong primitive
        raise AssertionError("a read is never deduplicated")

    adapter._sink.emit = emit  # type: ignore[method-assign]
    adapter._sink.emit_once_status = emit_once_status  # type: ignore[method-assign]
    assert await adapter.record(_entry()) == "h" * 64
    assert await adapter.record(_entry()) == "h" * 64
    assert len(emitted) == 2
    assert emitted[0].action == "portal_read.task_context"
    assert set(emitted[0].details) == READ_AUDIT_DETAIL_FIELDS
    assert adapter._sink._tenant_id == PG_SCOPE.tenant


async def test_the_postgres_adapter_refuses_an_entry_of_another_scope():
    adapter = PostgresReadAccessAudit(scope=PG_SCOPE, pool=object())  # type: ignore[arg-type]

    async def emit(record):  # pragma: no cover - must not be reached
        raise AssertionError("emitted a foreign entry")

    adapter._sink.emit = emit  # type: ignore[method-assign]
    with pytest.raises(GatewayRefusalError):
        await adapter.record(_entry(scope=PG_SCOPE.model_copy(update={"tenant": "other_tenant"})))


@pytest.mark.parametrize(
    "changes",
    [{"read_kind": "everything"}, {"read_at": datetime(2026, 10, 19, 12, 0)}],
    ids=["unknown-kind", "naive-time"],
)
async def test_the_record_refuses_an_unknown_kind_or_a_naive_time(changes):
    with pytest.raises(GatewayRefusalError):
        read_access_record(_entry(**changes), workload_ref=SCOPE.workload_ref)
