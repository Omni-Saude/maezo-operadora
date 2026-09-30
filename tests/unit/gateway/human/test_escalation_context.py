"""INTERIM escalation context (DL-0050): the pure parsers, the engine adapter and the gateway.

Synthetic HTTP and the unit-composed gateway from `test_task_completion_gateway.py`. Nothing here is
a qualified engine or a real `engine-rest`: what it proves is the exact set of requests the adapter
issues (and that none of them asks for "all the variables"), the shape rules that keep engine text
out of the response, and that the gateway authorizes the task BEFORE the engine is asked anything.
"""

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from tests.unit.gateway.human.test_gateway import SCOPE, SECRET, setup
from tests.unit.gateway.human.test_task_completion_gateway import (
    CLINICAL_GROUP,
    HUMAN_GROUP,
    escalation_snapshot,
    staff_in,
)

from maezo.gateway.human.errors import GatewayRefusalError
from maezo.gateway.human.escalation_context import (
    EscalationContext,
    EscalationContextSource,
    clean_group,
    clean_priority,
    clean_summary,
    clean_token,
    parse_engine_datetime,
    parse_iso_duration,
)
from maezo.gateway.human.escalation_context_engine import EngineRestEscalationContext
from maezo.gateway.human.queue import ReadRefusalError
from maezo.portal.contracts.context import TaskContextResponse

pytestmark = pytest.mark.asyncio

ORIGIN = "https://engine.internal:8443/engine-rest"
CREATED = "2026-09-28T21:31:33.000+0000"
STARTED = "2026-09-28T21:31:32.500+0000"
PROCESS = "SP-OP-ESCALATION-001"


# ---------------------------------------------------------------------------------------
# Pure parsers.
# ---------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("PT5M", timedelta(minutes=5)),
        ("PT30M", timedelta(minutes=30)),
        ("PT4H", timedelta(hours=4)),
        ("PT24H", timedelta(hours=24)),
        ("P1D", timedelta(days=1)),
        ("PT1H30M", timedelta(hours=1, minutes=30)),
        ("P1DT2H", timedelta(days=1, hours=2)),
        ("PT45S", timedelta(seconds=45)),
    ],
)
async def test_the_durations_the_routing_table_emits_are_parsed(text, expected):
    assert parse_iso_duration(text) == expected


@pytest.mark.parametrize(
    "bad", ["", "P", "PT", "P1M", "P1Y", "5m", "PT5", "-PT5M", "PT1.5H", "pt5m", None, 5]
)
async def test_anything_that_is_not_a_fixed_length_duration_is_refused_not_guessed(bad):
    # Months and years have no fixed length; guessing one would print a deadline nobody agreed to.
    assert parse_iso_duration(bad) is None


async def test_the_engine_date_shape_becomes_aware_utc():
    parsed = parse_engine_datetime("2026-09-28T21:31:33.000+0000")
    assert parsed == datetime(2026, 9, 28, 21, 31, 33, tzinfo=UTC)
    assert parse_engine_datetime("2026-09-28T18:31:33.000-0300") == parsed
    assert parse_engine_datetime("2026-09-28T21:31:33Z") == parsed


@pytest.mark.parametrize("bad", ["2026-09-28T21:31:33", "ontem", "", None, 1790000000])
async def test_a_naive_or_unreadable_date_is_dropped(bad):
    assert parse_engine_datetime(bad) is None


async def test_vocabulary_tokens_outside_their_shape_are_dropped_not_repaired():
    assert clean_token("red_flag_clinico") == "red_flag_clinico"
    assert clean_token("Red Flag") is None and clean_token("a/b") is None and clean_token("") is None
    assert clean_token("x" * 65) is None and clean_token(3) is None
    assert clean_group("plantao-clinico") == "plantao-clinico"
    assert clean_group("plantao clinico") is None and clean_group("../etc") is None
    assert clean_priority("P1") == "P1" and clean_priority("P12") == "P12"
    assert clean_priority("P") is None and clean_priority("p1") is None and clean_priority("P1 ") is None


async def test_the_summary_is_scrubbed_again_and_stripped_of_control_characters():
    raw = "Paciente com dor.\x00\x1b Contato 11 91234-5678, jo@ex.test, cpf 123.456.789-09."
    text = clean_summary(raw)
    assert text is not None
    for leaked in ("91234-5678", "jo@ex.test", "123.456.789-09", "\x00", "\x1b"):
        assert leaked not in text
    assert "Paciente com dor." in text


@pytest.mark.parametrize("empty", ["", "   ", "\n\t", "\x00\x01", None, 7, ["x"]])
async def test_an_empty_or_non_text_summary_is_absent_not_an_empty_string(empty):
    assert clean_summary(empty) is None


async def test_the_summary_is_bounded():
    text = clean_summary("dor no peito " * 400)
    assert text is not None and len(text) <= 1000


async def test_the_context_never_prints_the_summary():
    ctx = EscalationContext(
        etapa="atendimento",
        motivo_categoria=None,
        severidade=None,
        prioridade=None,
        grupo_atendimento=None,
        aberto_em=None,
        ack_vence_em=None,
        resolucao_vence_em=None,
        resumo_contexto="dor no peito",
    )
    assert "dor no peito" not in repr(ctx) and "dor no peito" not in str(ctx)


# ---------------------------------------------------------------------------------------
# The engine adapter.
# ---------------------------------------------------------------------------------------
class Engine:
    """A synthetic `engine-rest` that records every request it receives."""

    def __init__(self, *, task=None, process=None, variables=None, routing=None, status=None, raw=None):
        self.requests: list[httpx.Request] = []
        self.task = (
            {
                "id": "task-1",
                "taskDefinitionKey": "UT_TratarEscalonamento",
                "processInstanceId": "pi-1",
                "created": CREATED,
                "tenantId": SCOPE.tenant,
            }
            if task is None
            else task
        )
        self.process = (
            {"processDefinitionKey": PROCESS, "startTime": STARTED, "tenantId": SCOPE.tenant}
            if process is None
            else process
        )
        self.variables = (
            {
                "motivo_categoria": "red_flag_clinico",
                "severidade": "grave",
                "resumo_contexto": "Idoso com dor no peito e falta de ar.",
            }
            if variables is None
            else variables
        )
        self.routing = (
            [
                {
                    "outputs": [
                        {"variableName": "prioridade", "value": "P1"},
                        {"variableName": "grupo_atendimento", "value": "plantao-clinico"},
                        {"variableName": "sla_ack", "value": "PT5M"},
                        {"variableName": "sla_resolucao", "value": "PT30M"},
                    ]
                }
            ]
            if routing is None
            else routing
        )
        self.status = status or {}
        self.raw = raw or {}

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path.removeprefix("/engine-rest")
        if path in self.raw:
            return httpx.Response(200, content=self.raw[path])
        if path in self.status:
            return httpx.Response(self.status[path], json={"message": "PRIVATE upstream narrative"})
        if path == "/task/task-1":
            return httpx.Response(200, json=self.task)
        if path == "/history/process-instance/pi-1":
            return httpx.Response(200, json=self.process)
        if path == "/history/variable-instance":
            name = request.url.params["variableName"]
            if name not in self.variables:
                return httpx.Response(200, json=[])
            return httpx.Response(200, json=[{"name": name, "type": "String", "value": self.variables[name]}])
        if path == "/history/decision-instance":
            return httpx.Response(200, json=self.routing)
        return httpx.Response(404, json={})

    def source(self, **kw) -> EngineRestEscalationContext:
        return EngineRestEscalationContext(
            scope=SCOPE,
            origin=kw.get("origin", ORIGIN),
            timeout_seconds=5,
            transport=httpx.MockTransport(self.handler),
        )


async def test_the_full_context_is_assembled_from_fixed_reads():
    engine = Engine()
    context = await engine.source().read(task_id="task-1")
    assert context.etapa == "atendimento"
    assert (context.motivo_categoria, context.severidade) == ("red_flag_clinico", "grave")
    assert (context.prioridade, context.grupo_atendimento) == ("P1", "plantao-clinico")
    assert context.aberto_em == datetime(2026, 9, 28, 21, 31, 32, 500000, tzinfo=UTC)
    created = datetime(2026, 9, 28, 21, 31, 33, tzinfo=UTC)
    assert context.ack_vence_em == created + timedelta(minutes=5)
    assert context.resolucao_vence_em == created + timedelta(minutes=30)
    assert context.resumo_contexto == "Idoso com dor no peito e falta de ar."


async def test_the_adapter_issues_only_gets_and_never_asks_for_all_the_variables():
    engine = Engine()
    await engine.source().read(task_id="task-1")
    assert {r.method for r in engine.requests} == {"GET"}
    paths = sorted(r.url.path.removeprefix("/engine-rest") for r in engine.requests)
    assert paths == [
        "/history/decision-instance",
        "/history/process-instance/pi-1",
        "/history/variable-instance",
        "/history/variable-instance",
        "/history/variable-instance",
        "/task/task-1",
    ]
    asked = {r.url.params["variableName"] for r in engine.requests if "variable-instance" in r.url.path}
    assert asked == {"motivo_categoria", "severidade", "resumo_contexto"}
    # A variable request with no name would return every variable of the instance.
    assert all("variableName" in r.url.params for r in engine.requests if "variable-instance" in r.url.path)
    decision = next(r for r in engine.requests if "decision-instance" in r.url.path)
    assert decision.url.params["decisionDefinitionKey"] == "escalation_routing"
    assert decision.url.params["processInstanceId"] == "pi-1"


async def test_the_supervisor_stage_carries_no_clock_of_its_own():
    engine = Engine(
        task={
            "id": "task-1",
            "taskDefinitionKey": "UT_SupervisorAssume",
            "processInstanceId": "pi-1",
            "created": "2026-09-28T22:01:33.000+0000",
            "tenantId": SCOPE.tenant,
        }
    )
    context = await engine.source().read(task_id="task-1")
    assert context.etapa == "supervisao"
    # A deadline computed from the supervisor task's creation would be a number nobody agreed to.
    assert context.ack_vence_em is None and context.resolucao_vence_em is None
    assert context.prioridade == "P1" and context.motivo_categoria == "red_flag_clinico"


@pytest.mark.parametrize(
    "changes",
    [
        {"task": {"taskDefinitionKey": "UT_Outra", "processInstanceId": "pi-1", "tenantId": SCOPE.tenant}},
        {
            "task": {
                "taskDefinitionKey": "UT_TratarEscalonamento",
                "processInstanceId": "pi-1",
                "tenantId": "other",
            }
        },
        {"task": {"taskDefinitionKey": "UT_TratarEscalonamento", "processInstanceId": "pi-1"}},
        {"task": {"taskDefinitionKey": "UT_TratarEscalonamento", "tenantId": SCOPE.tenant}},
        {
            "task": {
                "taskDefinitionKey": "UT_TratarEscalonamento",
                "processInstanceId": "a/b",
                "tenantId": SCOPE.tenant,
            }
        },
        {"process": {"processDefinitionKey": "SP-OP-AUTH-001", "tenantId": SCOPE.tenant}},
        {"process": {"processDefinitionKey": PROCESS, "tenantId": "other"}},
    ],
)
async def test_a_task_or_process_that_is_not_this_tenants_escalation_is_refused(changes):
    with pytest.raises(GatewayRefusalError) as refusal:
        await Engine(**changes).source().read(task_id="task-1")
    assert refusal.value.code == "task_unavailable"


@pytest.mark.parametrize("task_id", ["a/b", "../x", "a?b", "a#b", "a%2Fb", "", " ", "a b", "\\x", "x" * 129])
async def test_an_unsafe_task_id_is_refused_before_any_request_leaves(task_id):
    engine = Engine()
    with pytest.raises(GatewayRefusalError) as refusal:
        await engine.source().read(task_id=task_id)
    assert refusal.value.code == "task_unavailable"
    assert engine.requests == []


async def test_engine_values_outside_their_shape_are_dropped_from_the_context():
    engine = Engine(
        variables={"motivo_categoria": "Red Flag!", "severidade": "a/b", "resumo_contexto": "   "},
        routing=[
            {
                "outputs": [
                    {"variableName": "prioridade", "value": "urgent"},
                    {"variableName": "grupo_atendimento", "value": "plantao clinico"},
                    {"variableName": "sla_ack", "value": "P1M"},
                    {"variableName": "sla_resolucao", "value": "soon"},
                ]
            }
        ],
    )
    context = await engine.source().read(task_id="task-1")
    assert context.motivo_categoria is None and context.severidade is None
    assert context.prioridade is None and context.grupo_atendimento is None
    assert context.resumo_contexto is None
    assert context.ack_vence_em is None and context.resolucao_vence_em is None


async def test_a_missing_routing_decision_or_variables_degrade_to_absent_fields():
    context = await Engine(variables={}, routing=[]).source().read(task_id="task-1")
    assert context.motivo_categoria is None and context.prioridade is None and context.resumo_contexto is None
    assert context.etapa == "atendimento" and context.aberto_em is not None


async def test_a_non_string_variable_is_ignored():
    engine = Engine()
    original = engine.handler

    def handler(request):
        if request.url.path.endswith("/history/variable-instance") and (
            request.url.params["variableName"] == "resumo_contexto"
        ):
            return httpx.Response(200, json=[{"name": "resumo_contexto", "type": "Json", "value": {"x": 1}}])
        return original(request)

    source = EngineRestEscalationContext(
        scope=SCOPE, origin=ORIGIN, timeout_seconds=5, transport=httpx.MockTransport(handler)
    )
    assert (await source.read(task_id="task-1")).resumo_contexto is None


async def test_a_missing_task_is_a_proven_absence_and_everything_else_is_uncertain():
    with pytest.raises(GatewayRefusalError) as absent:
        await Engine(status={"/task/task-1": 404}).source().read(task_id="task-1")
    assert absent.value.code == "task_unavailable"
    for status in (204, 301, 302, 400, 401, 403, 409, 500, 503):
        with pytest.raises(GatewayRefusalError) as uncertain:
            await Engine(status={"/task/task-1": status}).source().read(task_id="task-1")
        assert uncertain.value.code == "admission_unavailable", status


async def test_a_failure_in_any_later_read_is_uncertainty_never_a_partial_context():
    for path in (
        "/history/process-instance/pi-1",
        "/history/variable-instance",
        "/history/decision-instance",
    ):
        with pytest.raises(GatewayRefusalError) as refusal:
            await Engine(status={path: 500}).source().read(task_id="task-1")
        assert refusal.value.code == "admission_unavailable", path


async def test_transport_failures_and_unreadable_bodies_never_leak_upstream_text():
    def boom(request):
        raise httpx.ConnectTimeout("PRIVATE engine host 10.0.0.9")

    timed_out = EngineRestEscalationContext(
        scope=SCOPE, origin=ORIGIN, timeout_seconds=5, transport=httpx.MockTransport(boom)
    )
    with pytest.raises(GatewayRefusalError) as refusal:
        await timed_out.read(task_id="task-1")
    assert refusal.value.code == "admission_unavailable"
    assert "PRIVATE" not in str(refusal.value) and "10.0.0.9" not in str(refusal.value)

    with pytest.raises(GatewayRefusalError) as garbage:
        await Engine(raw={"/task/task-1": b"<html>PRIVATE</html>"}).source().read(task_id="task-1")
    assert garbage.value.code == "admission_unavailable" and "PRIVATE" not in str(garbage.value)


async def test_an_oversized_answer_is_refused():
    huge = b'{"padding":"' + b"x" * 70000 + b'"}'
    with pytest.raises(GatewayRefusalError) as refusal:
        await Engine(raw={"/task/task-1": huge}).source().read(task_id="task-1")
    assert refusal.value.code == "admission_unavailable"


@pytest.mark.parametrize(
    "origin",
    [
        "http://engine.example.com/engine-rest",  # plaintext to a public name
        "https://engine.internal/other",  # not the engine-rest base
        "https://user:pw@engine.internal/engine-rest",
        "https://engine.internal/engine-rest?x=1",
        "ftp://engine.internal/engine-rest",
    ],
)
async def test_the_origin_rules_are_the_ones_the_completion_adapter_already_enforces(origin):
    with pytest.raises(GatewayRefusalError) as refusal:
        EngineRestEscalationContext(scope=SCOPE, origin=origin, timeout_seconds=5)
    assert refusal.value.code == "production_capabilities_unavailable"


async def test_a_non_positive_timeout_is_refused_and_a_private_http_name_is_accepted():
    with pytest.raises(GatewayRefusalError):
        EngineRestEscalationContext(scope=SCOPE, origin=ORIGIN, timeout_seconds=0)
    EngineRestEscalationContext(
        scope=SCOPE, origin="http://cibseven.maezo.internal:8080/engine-rest", timeout_seconds=5
    )


async def test_a_closed_adapter_refuses():
    source = Engine().source()
    await source.close()
    with pytest.raises(GatewayRefusalError):
        await source.read(task_id="task-1")


async def test_a_port_that_forgets_an_abstract_method_cannot_be_built():
    class Half(EscalationContextSource):
        scope = SCOPE

        async def read(self, *, task_id):  # pragma: no cover
            raise NotImplementedError

    with pytest.raises(TypeError):
        Half()  # type: ignore[abstract]


# ---------------------------------------------------------------------------------------
# The gateway: authorize first, then ask.
# ---------------------------------------------------------------------------------------
class RecordingSource(EscalationContextSource):
    def __init__(self, *, context=None, fail=None):
        self.scope = SCOPE
        self.calls: list[str] = []
        self.fail = fail
        self.context = context or EscalationContext(
            etapa="atendimento",
            motivo_categoria="red_flag_clinico",
            severidade="grave",
            prioridade="P1",
            grupo_atendimento="plantao-clinico",
            aberto_em=datetime(2026, 9, 28, 21, 31, 32, tzinfo=UTC),
            ack_vence_em=datetime(2026, 9, 28, 21, 36, 33, tzinfo=UTC),
            resolucao_vence_em=datetime(2026, 9, 28, 22, 1, 33, tzinfo=UTC),
            resumo_contexto="Idoso com dor no peito.",
        )

    async def read(self, *, task_id):
        self.calls.append(task_id)
        if self.fail is not None:
            raise self.fail
        return self.context

    async def close(self):  # pragma: no cover
        return None


async def gateway(*, source, member=None, snap=None):
    g, *_ = await setup(snap=snap or escalation_snapshot(), member=member or staff_in(CLINICAL_GROUP))
    g._escalation_context = source
    return g


def rendered(value: TaskContextResponse) -> TaskContextResponse:
    return value


async def read(g, **kw):
    values = dict(session_secret=SECRET, task_id="task-1", render=rendered)
    values.update(kw)
    return await g.read_escalation_context(**values)


async def test_the_gateway_returns_the_context_of_a_task_the_member_may_read():
    source = RecordingSource()
    g = await gateway(source=source)
    response = await read(g)
    assert isinstance(response, TaskContextResponse)
    assert response.task_id == "task-1" and response.etapa == "atendimento"
    assert (response.prioridade, response.grupo_atendimento) == ("P1", "plantao-clinico")
    assert response.resumo_contexto == "Idoso com dor no peito."
    assert response.observed_at <= datetime.now(UTC) + timedelta(seconds=1)
    assert source.calls == ["task-1"]


async def test_a_member_of_another_group_is_refused_and_the_engine_is_never_asked():
    source = RecordingSource()
    g = await gateway(source=source, member=staff_in(HUMAN_GROUP))
    with pytest.raises(ReadRefusalError) as refusal:
        await read(g)
    assert refusal.value.code == "resource_unavailable"
    assert source.calls == []


async def test_a_task_that_is_not_an_escalation_task_is_refused_and_the_engine_is_never_asked():
    source = RecordingSource()
    g, *_ = await setup()  # the default fixture is an AUTH task
    g._escalation_context = source
    with pytest.raises(ReadRefusalError) as refusal:
        await read(g)
    assert refusal.value.code == "resource_unavailable"
    assert source.calls == []


async def test_without_an_installed_source_the_gateway_is_uncertain_not_empty():
    g = await gateway(source=None)
    with pytest.raises(ReadRefusalError) as refusal:
        await read(g)
    assert refusal.value.code == "read_dependency_unavailable"


async def test_a_source_of_another_scope_cannot_be_used():
    other = RecordingSource()
    other.scope = SCOPE.model_copy(update={"tenant": "other-tenant"})
    g, *_ = await setup(snap=escalation_snapshot(), member=staff_in(CLINICAL_GROUP))
    with pytest.raises(GatewayRefusalError) as refusal:
        type(g)(
            resolver=g._resolver,
            scope=g._scope,
            ports=g._ports,
            credentials=g._credentials,
            escalation_context=other,
        )
    assert refusal.value.code == "credential_scope_mismatch"


async def test_the_ports_proven_absence_is_a_404_and_everything_else_is_uncertainty():
    g = await gateway(source=RecordingSource(fail=GatewayRefusalError("task_unavailable")))
    with pytest.raises(ReadRefusalError) as absent:
        await read(g)
    assert absent.value.code == "resource_unavailable"
    for code in ("admission_unavailable", "authority_unavailable", "operation_forbidden"):
        g = await gateway(source=RecordingSource(fail=GatewayRefusalError(code)))
        with pytest.raises(ReadRefusalError) as uncertain:
            await read(g)
        assert uncertain.value.code == "read_dependency_unavailable", code


async def test_an_unexpected_failure_is_uncertainty_and_carries_no_narrative():
    g = await gateway(source=RecordingSource(fail=RuntimeError("PRIVATE narrative 11 91234-5678")))
    with pytest.raises(ReadRefusalError) as refusal:
        await read(g)
    assert refusal.value.code == "read_dependency_unavailable"
    assert "PRIVATE" not in str(refusal.value)


@pytest.mark.parametrize("task_id", ["", " ", "a/b", "a?b", "a#b", "a\nb"])
async def test_a_malformed_task_id_is_a_bad_request_before_any_session_is_resolved(task_id):
    source = RecordingSource()
    g = await gateway(source=source)
    with pytest.raises(ReadRefusalError) as refusal:
        await read(g, task_id=task_id)
    assert refusal.value.code == "invalid_request"
    assert source.calls == []


async def test_a_bad_session_is_refused_before_anything_else():
    source = RecordingSource()
    g = await gateway(source=source)
    with pytest.raises(ReadRefusalError) as refusal:
        await read(g, session_secret="x" * 43)
    assert refusal.value.code == "session_unavailable"
    assert source.calls == []
