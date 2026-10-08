"""GP11 wiring worker (`suppression.py`) — resolução real no store + DMN engine-side (VW4).

Insumos ACEITOS PELO DONO (2026-10-07 — VW0-DECISION-REGISTER §"INCORPORAÇÃO VW4-ANSWERS",
sha `ab262f7b…`). O worker migrou do estado de admissão para o wiring real COM as formas de
recusa preservadas: seam ausente/ilegível = UNKNOWN (nunca zero); transport ausente = roteamento
indisponível; linha da DMN fora do vocabulário fechado = incidente `CONTRACT_MISMATCH` — nunca
honra por omissão, nunca rota fabricada. Os dois erros modelados continuam consumption-covered
ADR-0030 (boundaries de SP-OP-SUPP-001).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from maezo.gateway.vendor_suppression import (
    K_ANON_FLOOR,
    EscalationClock,
    SuppressionRecord,
    SuppressionStore,
    suppression_ref_of,
)
from maezo.tools.workers.bootstrap import ALL_WORKER_BOOTSTRAPS, register_all_workers
from maezo.tools.workers.dmn_transport import DmnEvaluationError, DmnNoResultError
from maezo.tools.workers.harness import WorkerBpmnError, WorkerHarness
from maezo.tools.workers.suppression import (
    ERR_SUPP_ROUTING_UNAVAILABLE,
    ERR_SUPP_SUBJECT_UNRESOLVED,
    SUPPRESSION_ROUTE_TOPIC,
    SUPPRESSION_ROUTING_DMN,
    SUPPRESSION_VERIFY_TOPIC,
    SuppressionContractMismatchError,
    make_route_handler,
    make_verify_subject_handler,
    register_suppression_workers,
)

_T0 = datetime(2026, 10, 7, 10, 0, tzinfo=UTC)


class _FakeTransport:
    def complete(self, task_id: str, variables: dict | None = None, **_: object) -> None:  # pragma: no cover
        raise AssertionError("handler nunca completa com erro modelado — ou devolve, ou levanta")


class _FakeKafka:
    def publish(self, topic: str, payload: bytes, **_: object) -> None:  # pragma: no cover
        raise AssertionError("suppression worker nao publica no broker (egress = events.publish)")


def _fresh_harness() -> WorkerHarness:
    return WorkerHarness(_FakeTransport(), worker_id="suppression-probe")


def _admission_error(exc: BaseException) -> str:
    assert isinstance(exc, WorkerBpmnError), f"esperado WorkerBpmnError, veio {type(exc).__name__}"
    return exc.error_code


class _FakeTask:
    """ExternalTask mínimo — variáveis do contrato de tópico (fetch scope declarado)."""

    def __init__(self, variables: dict[str, Any] | None = None) -> None:
        self.id = "supp-probe-task"
        self.variables: dict[str, Any] = variables or {}
        self.business_key = "SUPP-probe-ref"


class _FakeStore:
    """Store mínimo do Protocol — resolve pelo digest cunhado."""

    def __init__(self, record: SuppressionRecord | None = None, *, boom: bool = False) -> None:
        self.record = record
        self.boom = boom
        self.resolved_with: tuple[str, str] | None = None

    async def resolve(self, tenant: str, suppression_ref: str) -> SuppressionRecord | None:
        self.resolved_with = (tenant, suppression_ref)
        if self.boom:
            raise RuntimeError("db down")
        return self.record


def _record(tenant: str = "t-1") -> SuppressionRecord:
    clock = EscalationClock.compute(_T0)
    return SuppressionRecord(
        tenant=tenant,
        suppression_ref=suppression_ref_of("subj-1", "chan-A"),
        subject_ref="subj-1",
        contact_channel="chan-A",
        canal="whatsapp",
        categoria_sujeito="vendedor",
        t_recorded=clock.t_recorded,
        t_lembrete=clock.t_lembrete,
        t_escalonado=clock.t_escalonado,
        t_deadline=clock.t_deadline,
        status="pendente",
        motivo="oposicao art. 18 §2o",
    )


class _FakeDmn:
    """DmnTransport mínimo — registra as rows e observa as chamadas (molde FakeDmnTransport)."""

    def __init__(self, rows: list[dict[str, Any]] | None = None, *, boom: Exception | None = None) -> None:
        self.rows = rows if rows is not None else [{"rota": "RECLAMACAO_ENCARREGADO", "grupo_decisao": "dpo"}]
        self.boom = boom
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def evaluate(
        self, decision_key: str, variables: dict[str, Any], *, tenant: str | None = None
    ) -> tuple[list[dict[str, Any]], Any]:
        del tenant
        if self.boom is not None:
            raise self.boom
        self.calls.append((decision_key, dict(variables)))
        version = type("V", (), {"version": 3})()
        return list(self.rows), version


# ----------------------------------------------------------------------------------
# Registro — fetch scopes EXPLÍCITOS (minimização art. 10 §1º como contrato de tópico)
# ----------------------------------------------------------------------------------


def test_both_suppression_topics_are_registered_with_declared_fetch_scopes() -> None:
    harness = _fresh_harness()
    register_suppression_workers(harness)
    subscriptions = {s.topic_name: s for s in harness._topic_subscriptions()}  # sonda do harness
    assert SUPPRESSION_VERIFY_TOPIC in harness.registered_topics
    assert SUPPRESSION_ROUTE_TOPIC in harness.registered_topics
    # verify: identificador + canal (o digest é cunhado AQUI, nunca fetchado como ref).
    assert subscriptions[SUPPRESSION_VERIFY_TOPIC].variables == (
        "tenant_id",
        "subject_ref",
        "contact_channel",
    )
    # route: + fatos de CLASSE do contexto lista/egresso (token declarado + cardinal agregada).
    assert subscriptions[SUPPRESSION_ROUTE_TOPIC].variables == (
        "tenant_id",
        "canal",
        "categoria_sujeito",
        "classe_campo",
        "celula_tamanho",
    )


def test_registration_is_idempotent() -> None:
    harness = _fresh_harness()
    register_suppression_workers(harness)
    first = len(harness.registered_topics)
    register_suppression_workers(harness)
    assert len(harness.registered_topics) == first


def test_full_composition_includes_suppression_topics_without_collision() -> None:
    harness = _fresh_harness()
    register_all_workers(harness, kafka=_FakeKafka())
    topics = harness.registered_topics
    assert len(topics) == len(set(topics)), "duplicate topic strings registered"
    assert SUPPRESSION_VERIFY_TOPIC in topics
    assert SUPPRESSION_ROUTE_TOPIC in topics


def test_suppression_bootstrap_is_the_19th_member() -> None:
    from maezo.tools.workers.suppression import register_suppression_workers as fn

    assert fn in ALL_WORKER_BOOTSTRAPS
    assert len(ALL_WORKER_BOOTSTRAPS) == 19


def test_o_seam_dmn_da_plataforma_e_herdado_pelo_route() -> None:
    """O runtime serve `dmn=` a TODOS os workers — `register_suppression_workers` deve aceitar
    o seam plataforma (fallback) e o nome reservado do envelope (precedência)."""
    harness = _fresh_harness()
    register_suppression_workers(harness, dmn="via-seam-plataforma")
    # A sonda de wiring é o próprio registro bem-sucedido; a precedência é provada no handler:
    transport_probe = object()
    harness2 = _fresh_harness()
    register_suppression_workers(harness2, dmn="plataforma", suppression_dmn_transport=transport_probe)
    assert harness.registered_topics and harness2.registered_topics


# ----------------------------------------------------------------------------------
# verify_subject — UNKNOWN nunca zero; formas de recusa preservadas
# ----------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_verify_subject_sem_store_levanta_o_erro_modelado() -> None:
    handler = make_verify_subject_handler(None)
    with pytest.raises(WorkerBpmnError) as exc:
        await handler(_FakeTask({"tenant_id": "t-1", "subject_ref": "s", "contact_channel": "c"}))
    assert _admission_error(exc.value) == ERR_SUPP_SUBJECT_UNRESOLVED


@pytest.mark.asyncio
async def test_verify_subject_com_placeholder_recusa_e_nunca_responde() -> None:
    # Um seam placeholder (nao-None, sem a operação da fonte) NAO autoriza resposta.
    handler = make_verify_subject_handler(object())
    with pytest.raises(WorkerBpmnError) as exc:
        await handler(_FakeTask({"tenant_id": "t-1", "subject_ref": "s", "contact_channel": "c"}))
    assert _admission_error(exc.value) == ERR_SUPP_SUBJECT_UNRESOLVED


@pytest.mark.asyncio
async def test_verify_subject_com_identificador_ausente_recusa() -> None:
    handler = make_verify_subject_handler(_FakeStore(_record()))
    with pytest.raises(WorkerBpmnError) as exc:
        await handler(_FakeTask({"tenant_id": "t-1"}))  # subject/contact ausentes
    assert _admission_error(exc.value) == ERR_SUPP_SUBJECT_UNRESOLVED


@pytest.mark.asyncio
async def test_verify_subject_resolve_o_par_opaco_e_devolve_o_relogio() -> None:
    record = _record()
    store = _FakeStore(record)
    handler = make_verify_subject_handler(store)
    out = await handler(_FakeTask({"tenant_id": "t-1", "subject_ref": "subj-1", "contact_channel": "chan-A"}))
    assert out is not None
    assert out["subject_resolved"] is True
    # O handler cunha o MESMO digest do plano de aplicação (nunca recebe `suppression_ref`).
    assert store.resolved_with == ("t-1", suppression_ref_of("subj-1", "chan-A"))
    # Relógio visível (§4c): as três datas ISO computadas NO NASCIMENTO.
    assert out["t_deadline_iso"] == record.t_deadline.isoformat()
    assert out["t_escalonado_iso"] == record.t_escalonado.isoformat()
    assert out["t_lembrete_iso"] == record.t_lembrete.isoformat()
    assert out["categoria_sujeito"] == "vendedor"


@pytest.mark.asyncio
async def test_verify_subject_registro_inexistente_e_unknown_nunca_zero() -> None:
    handler = make_verify_subject_handler(_FakeStore(None))
    with pytest.raises(WorkerBpmnError) as exc:
        await handler(_FakeTask({"tenant_id": "t-1", "subject_ref": "s", "contact_channel": "c"}))
    assert _admission_error(exc.value) == ERR_SUPP_SUBJECT_UNRESOLVED


@pytest.mark.asyncio
async def test_verify_subject_fonte_ilegivel_mantem_a_forma_de_recusa() -> None:
    handler = make_verify_subject_handler(_FakeStore(None, boom=True))
    with pytest.raises(WorkerBpmnError) as exc:
        await handler(_FakeTask({"tenant_id": "t-1", "subject_ref": "s", "contact_channel": "c"}))
    assert _admission_error(exc.value) == ERR_SUPP_SUBJECT_UNRESOLVED


# ----------------------------------------------------------------------------------
# route — DMN engine-side via o seam `dmn=`; drift = CONTRACT_MISMATCH, nunca rota fabricada
# ----------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_route_sem_transport_levanta_o_erro_modelado() -> None:
    handler = make_route_handler(None)
    with pytest.raises(WorkerBpmnError) as exc:
        await handler(_FakeTask(_route_vars()))
    assert _admission_error(exc.value) == ERR_SUPP_ROUTING_UNAVAILABLE


def _route_vars() -> dict[str, Any]:
    return {
        "tenant_id": "t-1",
        "canal": "whatsapp",
        "categoria_sujeito": "vendedor",
        "classe_campo": "C2",
        "celula_tamanho": K_ANON_FLOOR + 1,
    }


@pytest.mark.asyncio
async def test_route_avalia_a_dmn_engine_side_com_k_como_parametro() -> None:
    dmn = _FakeDmn([{"rota": "REGISTRO_HONRADO", "grupo_decisao": "dpo"}])
    handler = make_route_handler(dmn)
    out = await handler(_FakeTask(_route_vars()))
    assert out is not None
    assert out["rota"] == "REGISTRO_HONRADO"
    assert out["grupo_decisao"] == "dpo"
    assert out["dmn_decision_version"] == 3
    decision, inputs = dmn.calls[0]
    assert decision == SUPPRESSION_ROUTING_DMN
    # k=100 entra como INPUT/PARAM — a cifra vive na constante aceita, nunca na tabela.
    assert inputs["k_piso"] == K_ANON_FLOOR
    assert inputs["classe_campo"] == "C2"
    assert inputs["celula_tamanho"] == K_ANON_FLOOR + 1


@pytest.mark.asyncio
async def test_route_com_placeholder_de_transport_recusa() -> None:
    handler = make_route_handler(object())
    with pytest.raises(WorkerBpmnError) as exc:
        await handler(_FakeTask(_route_vars()))
    assert _admission_error(exc.value) == ERR_SUPP_ROUTING_UNAVAILABLE


@pytest.mark.asyncio
async def test_route_engine_inacessivel_propaga_transient_para_engine_retry() -> None:
    """ADR-0028 §3: `DmnEvaluationError` é TRANSIENT — o handler NÃO o converte em recusa
    modelada; deixa o engine computar o retry (incidente a 0, nunca rota fabricada)."""
    handler = make_route_handler(_FakeDmn(boom=DmnEvaluationError("engine down")))
    with pytest.raises(DmnEvaluationError):
        await handler(_FakeTask(_route_vars()))


@pytest.mark.asyncio
async def test_route_sem_match_recusa_modelada() -> None:
    handler = make_route_handler(_FakeDmn(rows=[], boom=DmnNoResultError(SUPPRESSION_ROUTING_DMN)))
    with pytest.raises(WorkerBpmnError) as exc:
        await handler(_FakeTask(_route_vars()))
    assert _admission_error(exc.value) == ERR_SUPP_ROUTING_UNAVAILABLE


@pytest.mark.asyncio
async def test_route_com_drift_de_vocabulario_incidente_contract_mismatch() -> None:
    """DMN drift ⇒ CONTRACT_MISMATCH (recusa da camada de aplicação — `failure(retries=0)`),
    nunca honra por omissão nem rota fabricada."""
    for drift in (
        [{"rota": "LIBERAR", "grupo_decisao": "dpo"}],  # verbo proibido E fora do vocabulário
        [{"rota": "REGISTRO_HONRADO", "grupo_decisao": ""}],  # grupo ausente
        [{"outro": "valor"}],  # sem rota
    ):
        handler = make_route_handler(_FakeDmn(rows=drift))
        with pytest.raises(SuppressionContractMismatchError) as exc:
            await handler(_FakeTask(_route_vars()))
        assert exc.value.code == "CONTRACT_MISMATCH"


@pytest.mark.asyncio
async def test_route_celula_nao_inteira_vira_none_e_a_dmn_fail_closed() -> None:
    """`celula_tamanho` não-inteiro (str do engine) vai como None — a DMN fail-closed para o
    catch-all (comparação FEEL contra null não casa as rows C2/C6)."""
    dmn = _FakeDmn([{"rota": "RECLAMACAO_ENCARREGADO", "grupo_decisao": "dpo"}])
    handler = make_route_handler(dmn)
    variables = _route_vars() | {"celula_tamanho": "250"}  # str do engine
    await handler(_FakeTask(variables))
    assert dmn.calls[0][1]["celula_tamanho"] is None


def test_o_protocol_do_store_e_a_superficie_do_seam() -> None:
    """O seam `suppression_store` é o Protocol tipado — a forma exigida pelo guarda."""
    assert hasattr(SuppressionStore, "resolve")
    assert hasattr(SuppressionStore, "record")
    assert hasattr(SuppressionStore, "active_suppressions")
    assert hasattr(SuppressionStore, "set_status")
