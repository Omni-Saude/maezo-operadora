"""GP11 envelope worker (`suppression.py`) — registration + fail-closed admission semantics.

VW4/GP11 (admitido pelo dono 2026-10-07 — VW0-DECISION-REGISTER §"Decisoes de Fechamento"
item 1). O worker pousa NO ESTADO DE ADMISSAO: ambos os handlers levantam os erros
modelados de SP-OP-SUPP-001 (consumption-covered ADR-0030 — ver
`test_check_bpmn_error_allowlist.py`, census 18) porque os insumos (store de supressao,
`CibSevenDmnTransport`, taxonomia/piso/SLA — VW0-D11/VW0-D20) sao pendentes DPO. A recusa
tipada E o contrato — nunca um stub que responde, nunca um zero fabricado.
"""

from __future__ import annotations

import pytest

from maezo.tools.workers.bootstrap import ALL_WORKER_BOOTSTRAPS, register_all_workers
from maezo.tools.workers.harness import WorkerBpmnError, WorkerHarness
from maezo.tools.workers.suppression import (
    ERR_SUPP_ROUTING_UNAVAILABLE,
    ERR_SUPP_SUBJECT_UNRESOLVED,
    SUPPRESSION_ROUTE_TOPIC,
    SUPPRESSION_VERIFY_TOPIC,
    make_route_handler,
    make_verify_subject_handler,
    register_suppression_workers,
)


class _FakeTransport:
    def complete(self, task_id: str, variables: dict | None = None, **_: object) -> None:  # pragma: no cover
        raise AssertionError("handler em estado de admissao NUNCA completa — so levanta/fracassa")


class _FakeKafka:
    def publish(self, topic: str, payload: bytes, **_: object) -> None:  # pragma: no cover
        raise AssertionError("suppression worker nao publica no broker (egress = events.publish)")


def _fresh_harness() -> WorkerHarness:
    return WorkerHarness(_FakeTransport(), worker_id="suppression-probe")


def _admission_error(exc: BaseException) -> str:
    assert isinstance(exc, WorkerBpmnError), f"esperado WorkerBpmnError, veio {type(exc).__name__}"
    return exc.error_code


def test_both_suppression_topics_are_registered_with_declared_fetch_scopes() -> None:
    harness = _fresh_harness()
    register_suppression_workers(harness)
    subscriptions = {s.topic_name: s for s in harness._topic_subscriptions()}
    assert SUPPRESSION_VERIFY_TOPIC in harness.registered_topics
    assert SUPPRESSION_ROUTE_TOPIC in harness.registered_topics
    # Fetch scopes EXPLICITOS — a minimizacao art. 10 §1o e o contrato de topico (nunca o
    # None legado, que o engine le como "todas as variaveis").
    assert subscriptions[SUPPRESSION_VERIFY_TOPIC].variables == (
        "tenant_id",
        "subject_ref",
        "contact_channel",
    )
    assert subscriptions[SUPPRESSION_ROUTE_TOPIC].variables == ("tenant_id", "canal", "categoria_sujeito")


def test_verify_subject_in_admission_state_raises_the_modeled_error() -> None:
    handler = make_verify_subject_handler(None)
    with pytest.raises(WorkerBpmnError) as exc:
        handler(_FakeTask())
    assert _admission_error(exc.value) == ERR_SUPP_SUBJECT_UNRESOLVED


def test_route_in_admission_state_raises_the_modeled_error() -> None:
    handler = make_route_handler(None)
    with pytest.raises(WorkerBpmnError) as exc:
        handler(_FakeTask())
    assert _admission_error(exc.value) == ERR_SUPP_ROUTING_UNAVAILABLE


def test_wired_store_does_not_change_the_admission_refusal_without_real_source() -> None:
    # Um seam placeholder (nao-None) NAO autoriza resposta: a fonte real vem do pacote de
    # wiring; o handler de admissao continua levantando o erro modelado (nunca fabrica).
    handler = make_verify_subject_handler(object())
    with pytest.raises(WorkerBpmnError) as exc:
        handler(_FakeTask())
    assert _admission_error(exc.value) == ERR_SUPP_SUBJECT_UNRESOLVED


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


class _FakeTask:
    """ExternalTask minimo — handlers de admissao levantam ANTES de ler qualquer variavel."""

    def __init__(self) -> None:
        self.id = "supp-probe-task"
        self.variables: dict = {}
        self.business_key = "SUPP-probe-ref"
