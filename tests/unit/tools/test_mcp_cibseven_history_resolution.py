"""Costura de leitura do historico por chave/filtro (PAGTO PR-C, MPTT P2, padrao DL-0050).

`CibSevenHttpTransport.resolve_historic_instance` sobre um `httpx.MockTransport` (fio real do
httpx, motor simulado so' nesta camada unitaria — o motor real fica em
`tests/integration/test_mcp_cibseven_history_resolution.py`, ADR-0011).

Cada caminho falha-fechada tem o seu teste: erro HTTP, instancia ausente, casamento ambiguo,
payload malformado e timeout — e o seletor invalido / nome PHI e' recusado ANTES da rede.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from maezo.tools.mcp_cibseven.transport import (
    CibSevenError,
    CibSevenHttpTransport,
    CibSevenTenantError,
    FakeCibSevenTransport,
    HistoricInstanceAmbiguousError,
    HistoricInstanceNotFoundError,
    HistoricInstanceRecord,
    HistoricInstanceResolvingTransport,
    HistoryPayloadMalformedError,
    HistoryReadUnavailableError,
)

_PK = "SP-OP-CONTAS-001"
_BK = "CONTAS-amh-L1"
_TENANT = "amh"


def _row(**over: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": "pi-1",
        "businessKey": _BK,
        "processDefinitionKey": _PK,
        "tenantId": _TENANT,
        "state": "COMPLETED",
        "endActivityId": "End_ContaAprovadaIntegral",
        "endTime": "2026-11-01T10:00:00.000-0300",
    }
    row.update(over)
    return row


def _var(name: str, value: Any, type_: str = "String", **over: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"name": name, "value": value, "type": type_, "processInstanceId": "pi-1"}
    out.update(over)
    return out


Handler = Callable[[httpx.Request], httpx.Response]


def _transport(
    handler: Handler, *, tenant_id: str | None = _TENANT
) -> tuple[CibSevenHttpTransport, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def recording(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    transport = CibSevenHttpTransport("http://engine/engine-rest", tenant_id=tenant_id)
    # injeta o fio simulado (httpx.MockTransport) no cliente real do transporte
    transport._client = httpx.AsyncClient(
        base_url="http://engine/engine-rest", transport=httpx.MockTransport(recording)
    )
    return transport, seen


def _engine(
    instances: Any,
    variables: Any = (),
    *,
    inst_status: int = 200,
    var_status: int = 200,
    activities: Any = None,
    act_status: int = 200,
) -> Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/history/activity-instance") and activities is not None:
            if isinstance(activities, bytes):
                return httpx.Response(act_status, content=activities)
            return httpx.Response(act_status, json=activities)
        if request.url.path.endswith("/history/process-instance"):
            if isinstance(instances, bytes):
                return httpx.Response(inst_status, content=instances)
            return httpx.Response(inst_status, json=instances)
        if request.url.path.endswith("/history/variable-instance"):
            if isinstance(variables, bytes):
                return httpx.Response(var_status, content=variables)
            return httpx.Response(
                var_status, json=list(variables) if isinstance(variables, tuple) else variables
            )
        return httpx.Response(599, json={"unexpected": request.url.path})

    return handler


# --- caminho feliz ---------------------------------------------------------------------------


async def test_resolves_one_completed_instance_by_business_key_with_end_activity_and_named_vars() -> None:
    transport, seen = _transport(
        _engine(
            [_row()],
            (
                _var("valor_liberado_centavos", 150000, "Long"),
                _var("linhas", json.dumps([{"g": "G1"}]), "Json"),
            ),
        )
    )
    record = await transport.resolve_historic_instance(
        _PK, business_key=_BK, names=("valor_liberado_centavos", "linhas")
    )
    assert record == HistoricInstanceRecord(
        instance_id="pi-1",
        business_key=_BK,
        process_key=_PK,
        state="COMPLETED",
        end_activity_id="End_ContaAprovadaIntegral",
        variables={"valor_liberado_centavos": 150000, "linhas": [{"g": "G1"}]},
    )
    query, var_read = seen
    assert query.method == "POST"
    assert query.url.params["firstResult"] == "0"
    assert query.url.params["maxResults"] == "2", "duas linhas bastam para provar unicidade"
    assert json.loads(query.content) == {
        "processDefinitionKey": _PK,
        "tenantIdIn": [_TENANT],
        "processInstanceBusinessKey": _BK,
    }
    assert var_read.method == "GET"
    assert var_read.url.params["processInstanceId"] == "pi-1"
    assert var_read.url.params["variableNameIn"] == "valor_liberado_centavos,linhas"
    assert var_read.url.params["deserializeValues"] == "false"


async def test_resolves_by_variable_filter_and_returns_only_the_requested_names() -> None:
    transport, seen = _transport(
        _engine(
            [_row(businessKey="PAGTO-amh-L1-P9")], (_var("prestador_id", "P9"), _var("valor", 10, "Integer"))
        )
    )
    record = await transport.resolve_historic_instance(
        _PK, variable_equals={"prestador_id": "P9"}, names=("valor",)
    )
    assert record.variables == {"valor": 10}, "a variavel do filtro so' volta se tambem foi pedida"
    body = json.loads(seen[0].content)
    assert body["variables"] == [{"name": "prestador_id", "operator": "eq", "value": "P9"}]
    assert "processInstanceBusinessKey" not in body
    # o nome do filtro entra na leitura de variaveis para CONFERIR que o filtro foi aplicado
    assert seen[1].url.params["variableNameIn"] == "prestador_id,valor"


async def test_active_instance_has_no_end_activity_and_no_variable_read_when_nothing_named() -> None:
    transport, seen = _transport(_engine([_row(state="ACTIVE", endActivityId=None, endTime=None)]))
    record = await transport.resolve_historic_instance(_PK, business_key=_BK)
    assert record.state == "ACTIVE"
    assert record.end_activity_id is None
    assert record.variables == {}
    assert len(seen) == 1, "sem nomes nem filtro, nenhuma variavel atravessa a rede"


async def test_absent_named_variable_stays_absent() -> None:
    transport, _ = _transport(_engine([_row()], ()))
    record = await transport.resolve_historic_instance(
        _PK, business_key=_BK, names=("protocolo_demonstrativo",)
    )
    assert record.variables == {}


# --- erro HTTP / timeout -> indisponivel (nunca "nao encontrado") ----------------------------


@pytest.mark.parametrize("status", [404, 500, 503])
async def test_http_error_on_instance_query_is_unavailable_never_not_found(status: int) -> None:
    transport, _ = _transport(_engine([], inst_status=status))
    with pytest.raises(HistoryReadUnavailableError) as info:
        await transport.resolve_historic_instance(_PK, business_key=_BK)
    assert not isinstance(info.value, HistoricInstanceNotFoundError)


async def test_http_error_on_variable_read_is_unavailable() -> None:
    transport, _ = _transport(_engine([_row()], (), var_status=500))
    with pytest.raises(HistoryReadUnavailableError):
        await transport.resolve_historic_instance(_PK, business_key=_BK, names=("valor",))


@pytest.mark.parametrize(
    "exc", [httpx.ReadTimeout("t"), httpx.ConnectTimeout("t"), httpx.ConnectError("refused")]
)
@pytest.mark.parametrize("leg", ["/history/process-instance", "/history/variable-instance"])
async def test_timeout_or_unreachable_on_either_leg_is_unavailable(exc: Exception, leg: str) -> None:
    base = _engine([_row()], (_var("valor", 1, "Integer"),))

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(leg):
            raise exc
        return base(request)

    transport, _ = _transport(handler)
    with pytest.raises(HistoryReadUnavailableError):
        await transport.resolve_historic_instance(_PK, business_key=_BK, names=("valor",))


# --- ausente / ambiguo -----------------------------------------------------------------------


async def test_empty_answer_is_not_found_and_only_that_class_claims_absence() -> None:
    transport, _ = _transport(_engine([]))
    with pytest.raises(HistoricInstanceNotFoundError):
        await transport.resolve_historic_instance(_PK, business_key=_BK)


async def test_two_matching_instances_are_ambiguous_never_first_row_wins() -> None:
    transport, seen = _transport(_engine([_row(), _row(id="pi-2", state="ACTIVE", endActivityId=None)]))
    with pytest.raises(HistoricInstanceAmbiguousError):
        await transport.resolve_historic_instance(_PK, business_key=_BK, names=("valor",))
    assert len(seen) == 1, "ambiguo e' decidido antes de ler variaveis"


async def test_same_variable_with_conflicting_values_is_ambiguous() -> None:
    transport, _ = _transport(_engine([_row()], (_var("valor", 1, "Integer"), _var("valor", 2, "Integer"))))
    with pytest.raises(HistoricInstanceAmbiguousError):
        await transport.resolve_historic_instance(_PK, business_key=_BK, names=("valor",))


# --- payload malformado / contraditorio ------------------------------------------------------


@pytest.mark.parametrize(
    "instances",
    [
        b"<html>not json</html>",
        {"id": "pi-1"},
        ["pi-1"],
        [_row(id=None)],
        [_row(id="")],
        [_row(id=7)],
        [_row(state=None)],
        [_row(endActivityId=12)],
        [_row(processDefinitionKey="SP-OP-OUTRO-001")],
        [_row(processDefinitionKey=None)],
        [_row(tenantId="outro")],
        [_row(tenantId=None)],
        [_row(businessKey="CONTAS-amh-OUTRO")],
    ],
    ids=[
        "non-json",
        "dict-not-list",
        "list-of-str",
        "id-missing",
        "id-empty",
        "id-not-str",
        "state-missing",
        "end-activity-not-str",
        "other-definition",
        "definition-absent",
        "other-tenant",
        "tenant-absent",
        "other-business-key",
    ],
)
async def test_malformed_or_contradictory_instance_row_fails_closed(instances: Any) -> None:
    transport, _ = _transport(_engine(instances))
    with pytest.raises(HistoryPayloadMalformedError):
        await transport.resolve_historic_instance(_PK, business_key=_BK)


@pytest.mark.parametrize(
    "variables",
    [
        b"nope",
        {"valor": 1},
        ["valor"],
        [_var("valor", "{not json", "Json")],
        [_var("valor", 1, "Integer", processInstanceId="pi-OUTRA")],
    ],
    ids=["non-json", "dict-not-list", "list-of-str", "json-undecodable", "other-instance-row"],
)
async def test_malformed_variable_payload_fails_closed(variables: Any) -> None:
    transport, _ = _transport(_engine([_row()], variables))
    with pytest.raises(HistoryPayloadMalformedError):
        await transport.resolve_historic_instance(_PK, business_key=_BK, names=("valor",))


@pytest.mark.parametrize("seen_value", [None, "P-OUTRO"], ids=["filter-var-absent", "filter-var-differs"])
async def test_engine_that_ignored_the_variable_filter_proves_nothing(seen_value: str | None) -> None:
    variables = () if seen_value is None else (_var("prestador_id", seen_value),)
    transport, _ = _transport(_engine([_row()], variables))
    with pytest.raises(HistoryPayloadMalformedError):
        await transport.resolve_historic_instance(_PK, variable_equals={"prestador_id": "P9"})


# --- seletor invalido e nomes PHI: recusados ANTES da rede ----------------------------------


@pytest.mark.parametrize(
    ("args", "kwargs"),
    [
        ((_PK,), {}),
        (("",), {"business_key": _BK}),
        (("SP OP",), {"business_key": _BK}),
        ((_PK,), {"business_key": 7}),
        ((_PK,), {"business_key": _BK, "names": ["valor"]}),
        ((_PK,), {"business_key": _BK, "names": ("valor-x",)}),
        ((_PK,), {"variable_equals": {"prestador_id": 9}}),
        ((_PK,), {"variable_equals": {"prestador_id": ""}}),
        ((_PK,), {"variable_equals": {"prestador_id": True}}),
        ((_PK,), {"business_key": _BK, "names": tuple(f"v{i}" for i in range(17))}),
        ((_PK,), {"variable_equals": {f"v{i}": "x" for i in range(5)}}),
    ],
    ids=[
        "no-selector",
        "empty-process-key",
        "bad-process-key",
        "business-key-not-str",
        "names-not-tuple",
        "bad-variable-name",
        "filter-value-int",
        "filter-value-empty",
        "filter-value-bool",
        "too-many-names",
        "too-many-filters",
    ],
)
async def test_invalid_selector_is_refused_before_any_request(
    args: tuple[Any, ...], kwargs: dict[str, Any]
) -> None:
    transport, seen = _transport(_engine([_row()]))
    with pytest.raises(ValueError):
        await transport.resolve_historic_instance(*args, **kwargs)
    assert seen == []


@pytest.mark.parametrize(
    "name",
    ["notas_resolucao", "matricula_beneficiario", "justificativa_clinica", "narrativa", "dossie_rafael"],
)
@pytest.mark.parametrize("where", ["names", "filter"])
async def test_phi_variable_names_are_refused_before_the_wire(name: str, where: str) -> None:
    """Guarda PHI (ADR-0006, DL-0050): nome PHI nao sai do motor nem viaja como filtro."""
    transport, seen = _transport(_engine([_row()], (_var(name, "texto clinico"),)))
    kwargs: dict[str, Any] = (
        {"business_key": _BK, "names": (name,)} if where == "names" else {"variable_equals": {name: "x"}}
    )
    with pytest.raises(ValueError, match="PHI"):
        await transport.resolve_historic_instance(_PK, **kwargs)
    assert seen == [], "a recusa PHI acontece antes de qualquer requisicao"


async def test_transport_without_engine_tenant_refuses_before_any_request() -> None:
    transport, seen = _transport(_engine([_row()]), tenant_id=None)
    with pytest.raises(CibSevenTenantError):
        await transport.resolve_historic_instance(_PK, variable_equals={"prestador_id": "P9"})
    assert seen == []


# --- hierarquia de erros: so' um afirma ausencia ---------------------------------------------


def test_error_contract_every_failure_is_a_cibseven_error_and_only_not_found_claims_absence() -> None:
    for cls in (
        HistoryReadUnavailableError,
        HistoricInstanceNotFoundError,
        HistoricInstanceAmbiguousError,
        HistoryPayloadMalformedError,
    ):
        assert issubclass(cls, CibSevenError)
    for cls in (HistoryReadUnavailableError, HistoricInstanceAmbiguousError, HistoryPayloadMalformedError):
        assert not issubclass(cls, HistoricInstanceNotFoundError)


def test_http_transport_implements_the_resolving_protocol() -> None:
    assert isinstance(
        CibSevenHttpTransport("http://engine/engine-rest", tenant_id=_TENANT),
        HistoricInstanceResolvingTransport,
    )


# --- dublê: mesmo seletor, mesmos erros ------------------------------------------------------


def _record(instance_id: str, **variables: Any) -> HistoricInstanceRecord:
    return HistoricInstanceRecord(
        instance_id=instance_id,
        business_key=f"BK-{instance_id}",
        process_key=_PK,
        state="COMPLETED",
        end_activity_id="End_X",
        variables=variables,
    )


async def test_fake_mirrors_not_found_ambiguous_and_named_projection() -> None:
    fake = FakeCibSevenTransport()
    assert isinstance(fake, HistoricInstanceResolvingTransport)
    with pytest.raises(HistoricInstanceNotFoundError):
        await fake.resolve_historic_instance(_PK, business_key="BK-a")
    fake.seed_historic_instance_record(_record("a", prestador_id="P1", valor=5))
    fake.seed_historic_instance_record(_record("b", prestador_id="P1", valor=6))
    got = await fake.resolve_historic_instance(_PK, business_key="BK-a", names=("valor",))
    assert got.variables == {"valor": 5}
    assert got.end_activity_id == "End_X"
    with pytest.raises(HistoricInstanceAmbiguousError):
        await fake.resolve_historic_instance(_PK, variable_equals={"prestador_id": "P1"})
    with pytest.raises(ValueError, match="PHI"):
        await fake.resolve_historic_instance(_PK, business_key="BK-a", names=("notas_resolucao",))


# --- endActivityId ausente no motor: derivado do historico de atividades ----------------------
#
# Observado no motor do CI (CIB Seven, PR #1167, 01/11/2026): instancia COMPLETED pelo evento final
# com `endActivityId: null` em `/history/process-instance`. A costura deriva o fato do historico de
# atividades, com o mesmo significado (o evento final de nivel de processo que terminou por ultimo).


def _act(
    activity_id: str, activity_type: str, end_time: Any, *, parent: str = "pi-1", **over: Any
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": f"{activity_id}:ai",
        "activityId": activity_id,
        "activityType": activity_type,
        "parentActivityInstanceId": parent,
        "processInstanceId": "pi-1",
        "endTime": end_time,
    }
    out.update(over)
    return out


_T0 = "2026-11-01T10:00:00.000-0300"
_T1 = "2026-11-01T10:00:01.000-0300"
_T2 = "2026-11-01T10:00:02.000-0300"


async def test_completed_without_engine_end_activity_derives_the_process_level_end_event() -> None:
    activities = [
        _act("StartEvent_1", "startEvent", _T0),
        _act("Task_Adjudicar", "serviceTask", _T1),
        _act("End_SubprocessoInterno", "noneEndEvent", _T2, parent="Sub_1:ai"),  # fim de subprocesso
        _act("End_ContaAprovadaIntegral", "noneEndEvent", _T1),
    ]
    transport, seen = _transport(_engine([_row(endActivityId=None)], activities=activities))
    record = await transport.resolve_historic_instance(_PK, business_key=_BK)
    assert record.end_activity_id == "End_ContaAprovadaIntegral", (
        "o fim do subprocesso nao encerra a instancia"
    )
    act_read = seen[1]
    assert act_read.method == "GET"
    assert act_read.url.path.endswith("/history/activity-instance")
    assert act_read.url.params["processInstanceId"] == "pi-1"
    assert act_read.url.params["finished"] == "true"


async def test_engine_reported_end_activity_is_used_verbatim_without_activity_read() -> None:
    transport, seen = _transport(_engine([_row()], activities=[_act("End_Outro", "noneEndEvent", _T0)]))
    record = await transport.resolve_historic_instance(_PK, business_key=_BK)
    assert record.end_activity_id == "End_ContaAprovadaIntegral"
    assert len(seen) == 1


async def test_parallel_process_level_ends_pick_the_one_that_ended_last() -> None:
    activities = [_act("End_RamoA", "noneEndEvent", _T2), _act("End_RamoB", "noneEndEvent", _T1)]
    transport, _ = _transport(_engine([_row(endActivityId=None)], activities=activities))
    record = await transport.resolve_historic_instance(_PK, business_key=_BK)
    assert record.end_activity_id == "End_RamoA"


async def test_two_process_level_ends_at_the_same_last_instant_are_ambiguous() -> None:
    activities = [_act("End_RamoA", "noneEndEvent", _T1), _act("End_RamoB", "noneEndEvent", _T1)]
    transport, _ = _transport(_engine([_row(endActivityId=None)], activities=activities))
    with pytest.raises(HistoricInstanceAmbiguousError):
        await transport.resolve_historic_instance(_PK, business_key=_BK)


async def test_terminated_instance_without_end_event_has_no_end_activity() -> None:
    activities = [_act("StartEvent_1", "startEvent", _T0), _act("Task_Adjudicar", "serviceTask", _T1)]
    transport, _ = _transport(
        _engine([_row(state="EXTERNALLY_TERMINATED", endActivityId=None)], activities=activities)
    )
    record = await transport.resolve_historic_instance(_PK, business_key=_BK)
    assert record.end_activity_id is None


@pytest.mark.parametrize("status", [404, 500])
async def test_http_error_on_activity_read_is_unavailable(status: int) -> None:
    transport, _ = _transport(_engine([_row(endActivityId=None)], activities=[], act_status=status))
    with pytest.raises(HistoryReadUnavailableError):
        await transport.resolve_historic_instance(_PK, business_key=_BK)


async def test_timeout_on_activity_read_is_unavailable() -> None:
    base = _engine([_row(endActivityId=None)])

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/history/activity-instance"):
            raise httpx.ReadTimeout("t")
        return base(request)

    transport, _ = _transport(handler)
    with pytest.raises(HistoryReadUnavailableError):
        await transport.resolve_historic_instance(_PK, business_key=_BK)


@pytest.mark.parametrize(
    "activities",
    [
        b"nope",
        {"activityId": "End_X"},
        ["End_X"],
        [_act("", "noneEndEvent", _T0)],
        [_act("End_X", "noneEndEvent", "ontem")],
        [_act("End_X", "noneEndEvent", None)],
        [_act("End_X", "noneEndEvent", "2026-11-01T10:00:00")],
        [_act("End_X", "noneEndEvent", _T0, processInstanceId="pi-OUTRA")],
    ],
    ids=[
        "non-json",
        "dict-not-list",
        "list-of-str",
        "activity-id-empty",
        "end-time-unparseable",
        "end-time-absent",
        "end-time-without-offset",
        "other-instance-row",
    ],
)
async def test_malformed_activity_payload_fails_closed(activities: Any) -> None:
    transport, _ = _transport(_engine([_row(endActivityId=None)], activities=activities))
    with pytest.raises(HistoryPayloadMalformedError):
        await transport.resolve_historic_instance(_PK, business_key=_BK)


async def test_cancelled_instance_with_a_prior_parallel_end_event_has_no_end_activity() -> None:
    activities = [_act("End_RamoA", "noneEndEvent", _T1), _act("UT_RamoB", "userTask", _T2)]
    transport, seen = _transport(
        _engine([_row(state="EXTERNALLY_TERMINATED", endActivityId=None)], activities=activities)
    )
    record = await transport.resolve_historic_instance(_PK, business_key=_BK)
    assert record.end_activity_id is None
    assert len(seen) == 1, "cancelled: no activity-history read"
