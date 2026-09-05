"""NEW-B1: uma excecao NAO-`DelegationError` levantada por um handler NAO pode escapar crua de
`DelegationDispatcher.delegate()`.

O FATO reproduzido antes desta correcao (handler carolina REAL, registrado ao vivo em
`runtime/agent_runtime/a2a_composition.py`, envelope sem `prestador_id`):

    RAISED OUT OF delegate(): ValueError: credentialing.analyze envelope has no prestador_id ...
    audit rows: [('a2a.delegate:carolina', 'ALLOW')]
    fact topics: ['agents.events.delegation.requested']
    record_agent_error calls: []

`dispatcher._execute` envolvia `await handler(envelope)` num `except DelegationError` APENAS, e os
oito `agents/*/delegation.py::state_from_envelope` (7 deles levantam `ValueError` num bug de
produtor) sao chamados FORA do `try` do proprio handler. Consequencias, todas visiveis acima:
(a) o contrato "never a raise out of the dispatcher" dos sete `delegate_*` era FALSO; (b) so' a
linha `ALLOW` de pre-execucao existia, nunca a linha terminal `:outcome`; (c) um fato `requested`
sem irmao `completed`/`rejected`; (d) contador de erro nunca disparava. No caminho DURAVEL a
`store.claim_or_get` ja tinha reivindicado a linha `processing` e `store.complete` nunca era
alcancada, entao a reentrega do MESMO `task_id` queimava o orcamento inteiro de poll do
`PostgresIdempotencyStore` (`40 x 0.05 s = 2 s`, medido) para so' entao reexecutar e relevantar.

O QUE ESTE MODULO FIXA (regra vigente, apos o reparo F1/F2). Sao DUAS propriedades
INDEPENDENTES, e e' separa-las que fecha o achado por inteiro:

  1. TRACO SEMPRE. TODA excecao que escapa do handler — terminal ou nao, classificada ou nao —
     deixa a linha de audit de desfecho e o contador `maezo_a2a_handler_error_total` ANTES de
     qualquer `raise`. Nenhuma classe atravessa `delegate()` em silencio.
  2. SELO SO' NAS CLASSES TERMINAIS, decididas por `isinstance` contra
     `a2a/dispatcher.py::_TERMINAL_HANDLER_ERROR_CLASSES = (ValueError, TypeError, KeyError)`,
     SUBCLASSES INCLUIDAS. Um `ValueError` NOMEADO (`class X(ValueError)`) e o
     `json.JSONDecodeError` da stdlib sao bugs de produtor identicos aos da classe base e por isso
     selam. A classificacao por NOME (`classify_agent_error_type`) deixava os dois escaparem — ela
     e' um lookup EXATO de `type(exc).__name__`, sem walk de MRO, e o proprio `error_types.py`
     documenta isso de proposito, porque ela existe para limitar a CARDINALIDADE de um ROTULO de
     metrica. Ela voltou a ser SO' isso: o rotulo, nunca o oraculo de fluxo.

Em forma de matriz:

  - TERMINAL (`ValueError`/`TypeError`/`KeyError` e subclasses) = bug de PRODUTOR sobre um envelope
    IMUTAVEL -> a reentrega do mesmo envelope falharia identicamente: rejeicao estruturada + linha
    `:outcome` FAILED + fato `rejected` + contador + selo de idempotencia.
  - QUALQUER OUTRA classe -> PROPAGA, sem selo e sem fato novo, mas com a linha de audit
    `PROPAGATED` (chave de dedup PROPRIA, para nao consumir a chave `:outcome` de uma entrega
    posterior bem-sucedida) e com o contador. E' o canal RETENTAVEL: `StartProcessFailedError` TEM
    de continuar retentavel
    (`test_start_failure_a2a_handlers.py::test_dispatcher_neither_seals_nor_completes_a_failed_start`),
    `AuditPersistenceError` idem, e um bug NAO CLASSIFICADO do grafo (`AttributeError`,
    `IndexError`, `ZeroDivisionError`) tambem — mas nenhum deles e' mais invisivel.
"""

from __future__ import annotations

import ast
import inspect
import json
from pathlib import Path
from typing import Any

import pytest

from maezo.a2a import (
    TOPIC_COMPLETED,
    TOPIC_REJECTED,
    TOPIC_REQUESTED,
    Budget,
    DelegationEnvelope,
    DelegationResult,
    HandlerOutput,
    RejectionReason,
    StoredResult,
)
from maezo.a2a.dispatcher import (
    AgentHandler,
    DelegationDispatcher,
    FactProducer,
    a2a_audit_outcome_dedup_key,
)
from maezo.a2a.registry import A2ARegistry
from maezo.agents.carolina.delegation import TASK_TYPE_CRED_DOSSIER, make_carolina_handler
from maezo.platform.error_types import (
    AGENT_ERROR_TYPE_OUTRO,
    AGENT_ERROR_TYPE_VALIDACAO,
    classify_agent_error_type,
)
from maezo.runtime.start_outcome import StartProcessFailedError
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport
from maezo.tools.workers.dmn_transport import FakeDmnTransport
from maezo.tools.workers.harness import FakeAuditSink
from tests.support.audit_fakes import FakeStartAuditSink

from .fakes import RecordingProducer, build_test_dispatcher, make_card

#: CPF sintetico plantado na MENSAGEM da excecao do handler (nunca num payload): a prova de que o
#: dispatcher nunca carrega `str(exc)` para o resultado, o fato ou o audit. Construcao deterministica
#: e de baixa entropia, como manda a disciplina de segredos de teste.
_SYNTHETIC_CPF = "123.456.789-01"


# --- Infra de teste ----------------------------------------------------------------------------


class _CountingStore:
    """Store em memoria que MODELA a semantica do `PostgresIdempotencyStore`.

    Uma linha reivindicada mas NAO selada (`complete` nunca chamada) devolve `None` na reentrega —
    e' exatamente o que o Postgres faz quando `_poll_until_done` esgota o orcamento sobre uma linha
    `processing` que ninguem selou (`test_idempotency_store.py::
    test_claim_conflict_with_processing_row_polls_then_times_out`). Ou seja: sem selo, a reentrega
    REEXECUTA o handler; com selo, ela REPETE o resultado terminal sem executar.
    """

    def __init__(self) -> None:
        self.claims: list[str] = []
        self.completes: list[str] = []
        self._done: dict[str, StoredResult] = {}

    async def claim_or_get(self, *, tenant: str, task_id: str) -> StoredResult | None:
        self.claims.append(task_id)
        return self._done.get(task_id)

    async def complete(self, *, tenant: str, task_id: str, result: DelegationResult) -> None:
        self.completes.append(task_id)
        self._done[task_id] = StoredResult.from_result(result)


class _FakeInference:
    async def generate(
        self,
        prompt: str,
        *,
        phi: bool = False,
        agent_id: str | None = None,
        tenant_id: str | None = None,
        task_kind: str | None = None,
    ) -> str:
        return "dossie factual sintetico"


def _carolina_handler() -> AgentHandler:
    """O handler carolina REAL (grafo real sobre fakes) — o mesmo que
    `a2a_composition.build_dossier_delegation_dispatcher` registra ao vivo."""
    return make_carolina_handler(
        _FakeInference(),
        dmn=FakeDmnTransport(),
        cibseven=FakeCibSevenTransport(),
        audit_sink=FakeStartAuditSink(),
    )


def _cred_envelope_without_prestador() -> DelegationEnvelope:
    """Envelope CRED sem `prestador_id` — o bug de produtor que faz
    `carolina.delegation.state_from_envelope` levantar `ValueError` FORA do `try` do handler."""
    return DelegationEnvelope.root(
        task_id="CRED-amh-nf1",
        task_type=TASK_TYPE_CRED_DOSSIER,
        origin="worker-cred",
        target="carolina",
        tenant="amh",
        budget=Budget(tokens=100, time_ms=1000),
        payload_ref="process://CRED-amh-nf1",
        payload_meta={"protocolo_cred": "PC-1"},
    )


def _envelope(task_id: str = "t1", **kw: object) -> DelegationEnvelope:
    base: dict[str, object] = {
        "task_id": task_id,
        "task_type": "authorization.analyze",
        "origin": "helena",
        "target": "rafael",
        "tenant": "amh",
        "budget": Budget(tokens=100, time_ms=100),
        "payload_ref": "fhir://Patient/abc",
    }
    base.update(kw)
    return DelegationEnvelope.root(**base)  # type: ignore[arg-type]


def _dispatcher_with_store(
    store: _CountingStore, handlers: dict[str, AgentHandler]
) -> tuple[DelegationDispatcher, FakeAuditSink, RecordingProducer]:
    registry = A2ARegistry()
    registry.register(make_card("rafael"))
    sink = FakeAuditSink()
    producer = RecordingProducer()
    dispatcher = DelegationDispatcher(
        registry=registry,
        handlers=handlers,
        audit=sink,
        facts=FactProducer(producer),
        idempotency=store,
    )
    return dispatcher, sink, producer


def _capture_metric(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, str]]:
    """Captura o contador de falha de handler do dispatcher.

    `raising=False` E' DELIBERADO e e' o que torna o commit VERMELHO legivel: antes da correcao o
    simbolo nao existe no modulo, o patch apenas cria o atributo, o dispatcher nunca o chama e a
    asserção falha por "o contador nunca disparou" — o motivo certo — em vez de por `AttributeError`
    na coleta. Depois da correcao o patch substitui o simbolo real.
    """
    calls: list[dict[str, str]] = []

    def _record(*, target: str, error_type: str) -> None:
        calls.append({"target": target, "error_type": error_type})

    monkeypatch.setattr("maezo.a2a.dispatcher.record_a2a_handler_error", _record, raising=False)
    return calls


# --- (a) O caminho VIVO: carolina real, ValueError de produtor --------------------------------


async def test_live_registered_handler_value_error_becomes_a_structured_rejection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """NEW-B1, o achado exato: `delegate()` NAO levanta, devolve rejeicao tipada e deixa rastro."""
    calls = _capture_metric(monkeypatch)
    dispatcher, sink, producer = build_test_dispatcher(
        cards=[make_card("carolina", accepted=frozenset({TASK_TYPE_CRED_DOSSIER}))],
        handlers={"carolina": _carolina_handler()},
    )

    result = await dispatcher.delegate(_cred_envelope_without_prestador())

    assert result.success is False
    assert result.rejection_reason is RejectionReason.HANDLER_ERROR
    assert result.rejection_reason == "handler_error"

    # (b) do achado: a linha de audit TERMINAL existe, com a chave de dedup `:outcome`.
    decisions = [record.decision for (record, _) in sink.emitted]
    assert decisions == ["ALLOW", "FAILED"], decisions
    outcome_record, outcome_key = sink.emitted[1]
    assert outcome_record.action == "a2a.delegate:carolina:outcome"
    assert outcome_key == a2a_audit_outcome_dedup_key("amh", "CRED-amh-nf1")
    assert outcome_record.details["decision_basis"] == "A2A:handler_error:handler_error"

    # (c) do achado: o fato `requested` ganha o irmao `rejected`, nunca `completed`.
    assert producer.topics() == [TOPIC_REQUESTED, TOPIC_REJECTED]
    assert TOPIC_COMPLETED not in producer.topics()

    # (d) do achado: o contador dispara EXATAMENTE uma vez, com o rotulo fechado R-063.
    assert calls == [{"target": "carolina", "error_type": AGENT_ERROR_TYPE_VALIDACAO}]


async def test_the_rejection_carries_the_exception_class_token_never_its_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """So' o TOKEN DE CLASSE viaja. Um CPF plantado na mensagem da excecao nao aparece em lugar
    nenhum: nem no `detail`, nem no `meta`, nem no audit, nem no fato."""
    _capture_metric(monkeypatch)

    async def leaking_handler(envelope: DelegationEnvelope) -> Any:
        raise ValueError(f"identidade ausente para o beneficiario {_SYNTHETIC_CPF}")

    dispatcher, sink, producer = build_test_dispatcher(
        cards=[make_card("rafael")], handlers={"rafael": leaking_handler}
    )
    result = await dispatcher.delegate(_envelope())

    assert result.success is False
    assert result.detail is not None
    assert "ValueError" in result.detail  # o token de classe, util para ops
    assert _SYNTHETIC_CPF not in result.detail
    assert _SYNTHETIC_CPF not in str(result.meta)
    for record, _ in sink.emitted:
        assert _SYNTHETIC_CPF not in str(record.details)
    for _, value, _ in producer.sent:
        assert _SYNTHETIC_CPF.encode("utf-8") not in value


async def test_type_error_from_a_wrong_signature_handler_is_also_converted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Forma F1-E NEW-12: um fake/handler cuja assinatura divergiu do `AgentHandler` real levanta
    `TypeError` na propria CHAMADA — tambem classe `validacao`, tambem terminal."""
    calls = _capture_metric(monkeypatch)

    async def wrong_signature_handler(envelope: DelegationEnvelope, extra: str) -> Any:
        raise AssertionError("nunca alcancado — a chamada ja falha na aridade")

    dispatcher, sink, producer = build_test_dispatcher(
        cards=[make_card("rafael")],
        handlers={"rafael": wrong_signature_handler},  # type: ignore[dict-item]
    )
    result = await dispatcher.delegate(_envelope())

    assert result.rejection_reason is RejectionReason.HANDLER_ERROR
    assert result.detail is not None and "TypeError" in result.detail
    assert [r.decision for (r, _) in sink.emitted] == ["ALLOW", "FAILED"]
    assert producer.topics() == [TOPIC_REQUESTED, TOPIC_REJECTED]
    assert calls == [{"target": "rafael", "error_type": AGENT_ERROR_TYPE_VALIDACAO}]


# --- (b) Caminho duravel: o selo da falha ------------------------------------------------------


async def test_durable_path_seals_the_failure_and_the_redelivery_replays_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A metade DURAVEL do achado. Antes: `complete` nunca era chamada, a linha ficava `processing`
    para sempre e cada reentrega reexecutava (medido: 3 entregas -> 3 execucoes, 0 selos). Agora a
    rejeicao e' terminal (ADR-0003: rejeicao nao retenta), entao `complete` SELA e a reentrega
    devolve o resultado guardado sem reexecutar nem gastar o orcamento de poll."""
    _capture_metric(monkeypatch)
    executions: list[str] = []

    async def failing_handler(envelope: DelegationEnvelope) -> Any:
        executions.append(envelope.task_id)
        raise ValueError("bug de produtor: chave de negocio ausente")

    store = _CountingStore()
    dispatcher, _, _ = _dispatcher_with_store(store, {"rafael": failing_handler})

    first = await dispatcher.delegate(_envelope("dur-1"))
    second = await dispatcher.delegate(_envelope("dur-1"))
    third = await dispatcher.delegate(_envelope("dur-1"))

    assert first.rejection_reason is RejectionReason.HANDLER_ERROR
    assert first.idempotent_replay is False
    assert store.completes == ["dur-1"], "a falha nao foi SELADA — a linha fica `processing`"
    assert len(executions) == 1, f"a reentrega reexecutou o handler: {executions}"
    for replay in (second, third):
        assert replay.idempotent_replay is True
        assert replay.rejection_reason is RejectionReason.HANDLER_ERROR
        assert replay.success is False


async def test_inflight_path_caches_the_failure_and_the_redelivery_replays_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mesma propriedade no caminho EM MEMORIA (Guard 4 `_inflight`, `idempotency=None`)."""
    _capture_metric(monkeypatch)
    executions: list[str] = []

    async def failing_handler(envelope: DelegationEnvelope) -> Any:
        executions.append(envelope.task_id)
        raise KeyError("chave obrigatoria ausente no payload_meta")

    dispatcher, sink, producer = build_test_dispatcher(
        cards=[make_card("rafael")], handlers={"rafael": failing_handler}
    )
    first = await dispatcher.delegate(_envelope())
    second = await dispatcher.delegate(_envelope())

    assert first.rejection_reason is RejectionReason.HANDLER_ERROR
    assert second.idempotent_replay is True
    assert second.rejection_reason is RejectionReason.HANDLER_ERROR
    assert len(executions) == 1
    # A repeticao nao re-audita nem re-emite: duas linhas e dois fatos, nunca quatro.
    assert [r.decision for (r, _) in sink.emitted] == ["ALLOW", "FAILED"]
    assert producer.topics() == [TOPIC_REQUESTED, TOPIC_REJECTED]


# --- (f) O canal RETENTAVEL RAF-02 continua intacto -------------------------------------------


async def test_start_process_failure_still_propagates_and_is_never_sealed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RAF-02 e' o contra-exemplo que define a fronteira desta correcao.

    `StartProcessFailedError` NAO e' um bug de produtor: e' um engine indisponivel, transitorio, e o
    `agents/*/delegation.py` o levanta de PROPOSITO para que o dispatcher NAO sele o `task_id`. Se
    a conversao fosse "toda excecao vira rejeicao", a falha de start viraria um resultado terminal
    IRRETENTAVEL — exatamente o agravante que RAF-02 existe para impedir."""
    calls = _capture_metric(monkeypatch)
    executions: list[str] = []

    async def start_failing_handler(envelope: DelegationEnvelope) -> Any:
        executions.append(envelope.task_id)
        raise StartProcessFailedError("rafael nao conseguiu iniciar SP-OP-AUTH-001")

    store = _CountingStore()
    dispatcher, sink, producer = _dispatcher_with_store(store, {"rafael": start_failing_handler})

    for _ in range(2):
        with pytest.raises(StartProcessFailedError):
            await dispatcher.delegate(_envelope("raf02-1"))

    assert len(executions) == 2, "a reentrega NAO reexecutou — a falha transitoria foi selada"
    assert store.completes == [], "uma falha transitoria foi SELADA como terminal (RAF-02)"
    assert TOPIC_REJECTED not in producer.topics()
    assert producer.topics() == [TOPIC_REQUESTED, TOPIC_REQUESTED], (
        "o canal retentavel emitiu um fato NOVO: o pino RAF-02 exige `topics() == [REQUESTED]` por entrega"
    )

    # ...E O TRACO EXISTE MESMO ASSIM (F1). Nada foi selado, nada virou terminal, mas a entrega
    # deixou de ser invisivel: uma linha NAO-terminal `PROPAGATED` e uma contagem POR ENTREGA.
    decisoes = [r.decision for (r, _) in sink.emitted]
    assert "FAILED" not in decisoes and "COMPLETED" not in decisoes, decisoes
    assert decisoes == ["ALLOW", "PROPAGATED"], (
        f"a linha NAO-terminal do canal retentavel sumiu (ou virou terminal): {decisoes}"
    )
    assert calls == [
        {"target": "rafael", "error_type": AGENT_ERROR_TYPE_OUTRO},
        {"target": "rafael", "error_type": AGENT_ERROR_TYPE_OUTRO},
    ], (
        "o contador tem de disparar em CADA entrega do canal retentavel — a linha de audit e' "
        f"dedup por `task_id`, o contador nao: {calls}"
    )


async def test_audit_sink_failure_inside_the_handler_still_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`AuditPersistenceError` levantada de DENTRO do handler tambem e' transitoria: propaga, sem
    selo — a mesma razao que o docstring de RAF-02 ja registra."""
    calls = _capture_metric(monkeypatch)
    from maezo.gateway.audit_postgres import AuditPersistenceError

    async def audit_failing_handler(envelope: DelegationEnvelope) -> Any:
        raise AuditPersistenceError("audit chain unreachable (simulado)")

    store = _CountingStore()
    dispatcher, sink, _ = _dispatcher_with_store(store, {"rafael": audit_failing_handler})

    with pytest.raises(AuditPersistenceError):
        await dispatcher.delegate(_envelope("transient-1"))
    assert store.completes == []
    assert [r.decision for (r, _) in sink.emitted] == ["ALLOW", "PROPAGATED"]
    assert calls == [{"target": "rafael", "error_type": AGENT_ERROR_TYPE_OUTRO}]


# --- (e) F1: a MATRIZ de classes — traco SEMPRE, selo so' nas TERMINAIS -----------------------


class _EnvelopeSchemaError(ValueError):
    """Subclasse NOMEADA de `ValueError` — exatamente o caso que a classificacao por NOME perdia.

    `classify_agent_error_type` e' um lookup EXATO por `type(exc).__name__` (o proprio
    `platform/error_types.py` documenta a AUSENCIA de walk de MRO como decisao deliberada: ele
    limita a cardinalidade de um ROTULO de metrica). Uma excecao assim e' um bug de produtor
    identico ao `ValueError` base, mas caia no balde `outro` — e portanto escapava crua.
    """


def _json_decode_error() -> json.JSONDecodeError:
    """Um `json.JSONDecodeError` REAL da stdlib: subclasse de `ValueError`, nome nao mapeado.

    Nao e' hipotetico: qualquer handler que valide um envelope com `json.loads` levanta esta
    classe, e ela e' a prova viva de que a fronteira tem de ser por `isinstance`.
    """
    try:
        json.loads("{isto nao e json}")
    except json.JSONDecodeError as exc:
        return exc
    raise AssertionError("nao-vacuidade: `json.loads` tinha de ter falhado")


#: `(id, instancia, e_terminal)`. Os ids sao ASCII e SEM ESPACO de proposito: o gate
#: `scripts/ci/check_evidence_ledger_hashes.py` so' reconhece linhas de resultado que casam
#: `\S+::\S+`, entao um id com espaco sumiria do hash declarado no ledger.
_MATRIZ_DE_CLASSES: list[tuple[str, Exception, bool]] = [
    ("value_error", ValueError("bug de produtor"), True),
    ("type_error", TypeError("assinatura divergente"), True),
    ("key_error", KeyError("chave ausente"), True),
    ("value_error_subclasse_nomeada", _EnvelopeSchemaError("envelope fora do schema"), True),
    ("json_decode_error", _json_decode_error(), True),
    ("attribute_error", AttributeError("bug de programacao num no' do grafo"), False),
    ("index_error", IndexError("lista vazia"), False),
    ("zero_division_error", ZeroDivisionError("divisao por zero"), False),
    ("runtime_error", RuntimeError("estado impossivel"), False),
    ("start_process_failed_error", StartProcessFailedError("engine fora (RAF-02)"), False),
]


@pytest.mark.parametrize(
    ("exc", "terminal"),
    [(exc, terminal) for (_, exc, terminal) in _MATRIZ_DE_CLASSES],
    ids=[nome for (nome, _, _) in _MATRIZ_DE_CLASSES],
)
async def test_every_escaping_exception_leaves_a_trace_and_only_terminal_classes_are_sealed(
    exc: Exception, terminal: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A matriz inteira do achado F1, medida pelo dispatcher REAL.

    Coluna 1 (vale para as DEZ linhas): existe linha de audit de desfecho e existe contagem. Era
    a metade que faltava — antes, tudo que nao fosse `validacao` atravessava `delegate()` sem
    audit, sem fato e sem contador, e no caminho duravel deixava uma linha `processing` que
    ninguem selava.
    Coluna 2 (separa as linhas): so' as classes TERMINAIS selam e devolvem rejeicao; as outras
    relevantam, sem selo, para que a entrega continue retentavel (RAF-02).
    """
    calls = _capture_metric(monkeypatch)
    executions: list[str] = []

    async def failing_handler(envelope: DelegationEnvelope) -> Any:
        executions.append(envelope.task_id)
        raise exc

    store = _CountingStore()
    dispatcher, sink, producer = _dispatcher_with_store(store, {"rafael": failing_handler})
    task_id = f"matriz-{type(exc).__name__}"

    if terminal:
        result = await dispatcher.delegate(_envelope(task_id))
        assert result.success is False
        assert result.rejection_reason is RejectionReason.HANDLER_ERROR
        assert result.detail is not None and type(exc).__name__ in result.detail
        assert store.completes == [task_id], (
            f"{type(exc).__name__} e' TERMINAL e nao foi selada: a reentrega do mesmo `task_id` "
            "reexecutaria o handler para sempre"
        )
        assert producer.topics() == [TOPIC_REQUESTED, TOPIC_REJECTED]
    else:
        with pytest.raises(type(exc)):
            await dispatcher.delegate(_envelope(task_id))
        assert store.completes == [], (
            f"{type(exc).__name__} foi SELADA: uma falha possivelmente transitoria virou "
            "irretentavel por `task_id` (o agravante RAF-02)"
        )
        assert producer.topics() == [TOPIC_REQUESTED], "o canal retentavel emitiu um fato novo"

    assert len(executions) == 1
    decisoes = [record.decision for (record, _) in sink.emitted]
    assert decisoes == ["ALLOW", "FAILED" if terminal else "PROPAGATED"], (
        f"{type(exc).__name__} atravessou o dispatcher sem linha de desfecho: {decisoes}"
    )
    assert calls == [{"target": "rafael", "error_type": classify_agent_error_type(exc)}], (
        f"{type(exc).__name__} atravessou o dispatcher sem contagem: {calls}"
    )


async def test_the_propagated_trace_is_a_non_terminal_row_that_never_carries_the_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A FORMA do traco do canal retentavel: acao `:outcome`, decisao NAO-terminal, chave propria,
    e o mesmo net de PHI do ramo terminal (so' o token de classe viaja)."""
    from maezo.a2a.dispatcher import a2a_audit_propagated_dedup_key

    _capture_metric(monkeypatch)

    async def leaking_bug_handler(envelope: DelegationEnvelope) -> Any:
        raise AttributeError(f"objeto sem atributo para o beneficiario {_SYNTHETIC_CPF}")

    dispatcher, sink, producer = build_test_dispatcher(
        cards=[make_card("rafael")], handlers={"rafael": leaking_bug_handler}
    )
    with pytest.raises(AttributeError):
        await dispatcher.delegate(_envelope("prop-1"))

    record, key = sink.emitted[1]
    assert record.action == "a2a.delegate:rafael:outcome"
    assert record.decision == "PROPAGATED"
    assert record.decision != "COMPLETED", "uma entrega que so' propagou nao pode constar concluida"
    assert key == a2a_audit_propagated_dedup_key("amh", "prop-1")
    assert record.details["decision_basis"] == "A2A:handler_error:propagated"
    assert "AttributeError" in str(record.details["detail"])
    assert _SYNTHETIC_CPF not in str(record.details)
    for _, value, _ in producer.sent:
        assert _SYNTHETIC_CPF.encode("utf-8") not in value
    assert producer.topics() == [TOPIC_REQUESTED], "o canal retentavel nao emite fato novo"


async def test_a_propagated_delivery_does_not_consume_the_terminal_outcome_dedup_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regressao de PROJETO do traco novo: ele tem chave de dedup PROPRIA.

    `emit_once` e' exatamente-uma-vez POR CHAVE. Se o traco NAO-terminal reusasse
    `a2a_audit_outcome_dedup_key`, a linha COMPLETED da entrega que — depois de uma falha
    transitoria — finalmente desse certo seria engolida em silencio, e o traco novo teria APAGADO
    o registro terminal que o T-F exige. Aqui a mesma delegacao falha uma vez e conclui na
    seguinte: as tres linhas tem de sobreviver.
    """
    from maezo.a2a.dispatcher import a2a_audit_propagated_dedup_key

    _capture_metric(monkeypatch)
    tentativas: list[str] = []

    async def flaky_handler(envelope: DelegationEnvelope) -> Any:
        tentativas.append(envelope.task_id)
        if len(tentativas) == 1:
            raise StartProcessFailedError("engine fora na primeira entrega")
        return HandlerOutput(output_ref="process://flaky-ok")

    dispatcher, sink, producer = build_test_dispatcher(
        cards=[make_card("rafael")], handlers={"rafael": flaky_handler}
    )
    with pytest.raises(StartProcessFailedError):
        await dispatcher.delegate(_envelope("flaky-1"))
    result = await dispatcher.delegate(_envelope("flaky-1"))

    assert result.success is True
    decisoes = [r.decision for (r, _) in sink.emitted]
    assert decisoes == ["ALLOW", "PROPAGATED", "COMPLETED"], (
        f"o traco NAO-terminal consumiu a chave `:outcome` da conclusao: {decisoes}"
    )
    chaves = [key for (_, key) in sink.emitted]
    assert chaves[1] == a2a_audit_propagated_dedup_key("amh", "flaky-1")
    assert chaves[2] == a2a_audit_outcome_dedup_key("amh", "flaky-1")
    assert chaves[1] != chaves[2]
    assert TOPIC_COMPLETED in producer.topics()


# --- (d) Regressao: as rejeicoes existentes e o `DelegationError` nao mudam --------------------


async def test_delegation_error_keeps_its_anti_loop_reason_and_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """O ramo `except DelegationError` continua byte-a-byte o de antes: razao `anti_loop`, `detail`
    = a mensagem estrutural da excecao (PHI-free por construcao) e linha `:outcome` FAILED."""
    _capture_metric(monkeypatch)
    from .fakes import FakeAgentHandler

    handler = FakeAgentHandler(subdelegate_to="helena")
    dispatcher, sink, producer = build_test_dispatcher(
        cards=[make_card("rafael")], handlers={"rafael": handler}
    )
    result = await dispatcher.delegate(_envelope())

    assert result.rejection_reason is RejectionReason.ANTI_LOOP
    assert result.detail is not None and "helena" in result.detail
    assert [r.decision for (r, _) in sink.emitted] == ["ALLOW", "FAILED"]
    assert sink.emitted[1][0].details["decision_basis"] == "A2A:handler_error:anti_loop"
    assert producer.topics() == [TOPIC_REQUESTED, TOPIC_REJECTED]


@pytest.mark.parametrize(
    ("member_name", "value"),
    [
        ("UNKNOWN_TARGET", "unknown_target"),
        ("TASK_TYPE_NOT_ACCEPTED", "task_type_not_accepted"),
        ("NO_HANDLER", "no_handler"),
        ("EXPIRED", "expired"),
        ("ANTI_LOOP", "anti_loop"),
        ("SIGNATURE_INVALID", "signature_invalid"),
        ("HANDLER_ERROR", "handler_error"),
    ],
)
def test_rejection_reason_vocabulary_is_stable(member_name: str, value: str) -> None:
    """O vocabulario e' um contrato de fio (persistido em `a2a_idempotency.result`,
    `StoredResult.rejection_reason`): os seis tokens existentes ficam intocados e o setimo entra.

    Os membros sao resolvidos por NOME em tempo de execucao de proposito: um acesso de atributo no
    corpo do `parametrize` transformaria a ausencia do setimo token num erro de COLETA, que derruba
    o modulo inteiro em vez de falhar o teste que cobre o token faltante."""
    assert getattr(RejectionReason, member_name).value == value


def test_the_new_reason_round_trips_through_the_durable_store_shape() -> None:
    """Propagacao: `StoredResult`/`_stored_to_result` remapeiam o token novo para o enum — sem isso
    uma reentrega devolveria a rejeicao sem razao tipada."""
    from maezo.a2a.dispatcher import _stored_to_result

    stored = StoredResult.from_result(
        DelegationResult.rejected("t1", RejectionReason.HANDLER_ERROR, detail="handler raised X")
    )
    assert stored.rejection_reason == "handler_error"
    assert _stored_to_result(stored).rejection_reason is RejectionReason.HANDLER_ERROR


# --- Cerca estrutural (AST): o ramo largo nao pode ser removido em silencio --------------------


def _execute_try_block() -> ast.Try:
    """O `try` de `DelegationDispatcher._execute` que envolve `await handler(envelope)`."""
    import maezo.a2a.dispatcher as dispatcher_module

    tree = ast.parse(Path(inspect.getfile(dispatcher_module)).read_text(encoding="utf-8"))
    execute = next(
        node for node in ast.walk(tree) if isinstance(node, ast.AsyncFunctionDef) and node.name == "_execute"
    )
    blocks = [
        node
        for node in ast.walk(execute)
        if isinstance(node, ast.Try) and "await handler(envelope)" in ast.unparse(node.body)
    ]
    assert len(blocks) == 1, f"esperado UM try envolvendo a chamada do handler, achei {len(blocks)}"
    return blocks[0]


def _handler_names(handler: ast.ExceptHandler) -> list[str]:
    if handler.type is None:
        return ["<bare>"]
    if isinstance(handler.type, ast.Tuple):
        return [ast.unparse(e) for e in handler.type.elts]
    return [ast.unparse(handler.type)]


def _dispatcher_tree() -> ast.Module:
    import maezo.a2a.dispatcher as dispatcher_module

    return ast.parse(Path(inspect.getfile(dispatcher_module)).read_text(encoding="utf-8"))


def _branch_source_including_the_methods_it_delegates_to(branch: ast.ExceptHandler) -> str:
    """O texto do ramo MAIS o corpo de todo `self._m(...)` que ele chama.

    Sem isto a cerca seria trivialmente contornavel: mover o audit/fato/contador para um metodo
    privado — que e' exatamente o que a correcao faz, e legitimamente — deixaria o ramo com tres
    linhas e a cerca passaria a nao verificar nada. Um nivel de indirecao basta; a cerca cobre a
    forma que o codigo tem hoje e falha alto se alguem a esconder mais fundo."""
    tree = _dispatcher_tree()
    methods = {
        node.name: node for node in ast.walk(tree) if isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef)
    }
    pieces = [ast.unparse(branch)]
    for node in ast.walk(branch):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "self"
            and node.func.attr in methods
        ):
            pieces.append(ast.unparse(methods[node.func.attr]))
    return "\n".join(pieces)


def _broad_handler_branch() -> ast.ExceptHandler:
    """O ramo `except Exception` que acompanha `except DelegationError` em `_execute`."""
    block = _execute_try_block()
    clauses = {name: h for h in block.handlers for name in _handler_names(h)}
    assert "Exception" in clauses, "o ramo largo companheiro sumiu — o defeito NEW-B1 voltou"
    return clauses["Exception"]


def _stringifies_the_exception(node: ast.AST) -> bool:
    """Ha uma chamada `str(<nome>)` sobre o nome ligado ao `except ... as <nome>` neste ramo?

    Checagem por AST e nao por texto: as docstrings do proprio ramo CITAM `str(exc)` para explicar
    por que ele nao e usado, e um `in`-de-texto acusaria a explicacao junto com o defeito."""
    bound = {h.name for h in ast.walk(node) if isinstance(h, ast.ExceptHandler) and h.name}
    if isinstance(node, ast.ExceptHandler) and node.name:
        bound.add(node.name)
    return any(
        isinstance(call, ast.Call)
        and isinstance(call.func, ast.Name)
        and call.func.id == "str"
        and any(isinstance(a, ast.Name) and a.id in bound for a in call.args)
        for call in ast.walk(node)
    )


def test_the_handler_call_is_guarded_by_a_broad_companion_branch() -> None:
    """CERCA. `except DelegationError` sozinho e' exatamente o defeito NEW-B1: qualquer outra
    excecao escapa crua de `delegate()`. Esta cerca morre se o ramo largo for removido, estreitado
    de volta para so' `DelegationError`, ou se ele parar de auditar/emitir/contar/devolver."""
    block = _execute_try_block()
    clauses = {name: h for h in block.handlers for name in _handler_names(h)}

    assert "DelegationError" in clauses, "o ramo estrutural sumiu"
    assert "Exception" in clauses, (
        "`_execute` envolve `await handler(envelope)` sem ramo largo companheiro: toda excecao "
        "nao-`DelegationError` (ValueError de `state_from_envelope`, TypeError de assinatura) "
        "escapa crua de `delegate()` — o defeito NEW-B1"
    )
    assert "BaseException" not in clauses, (
        "um `except BaseException` engoliria `asyncio.CancelledError`: uma delegacao drenada nao "
        "e' uma falha de handler"
    )

    broad = clauses["Exception"]
    body = _branch_source_including_the_methods_it_delegates_to(broad)
    for required in (
        "_audit_delegation_outcome",  # (b) do achado: a linha de desfecho duravel
        "DelegationFactKind.REJECTED",  # (c) do achado: o fato `rejected`
        "record_a2a_handler_error",  # (d) do achado: o contador
        "_TERMINAL_HANDLER_ERROR_CLASSES",  # a fronteira terminal-vs-retentavel, por classe
        "classify_agent_error_type",  # o ROTULO limitado da metrica (nunca o oraculo de fluxo)
        "RejectionReason.HANDLER_ERROR",  # a razao tipada devolvida ao chamador
    ):
        assert required in body, f"o ramo largo nao chama/usa `{required}`: {body[:400]}"
    assert not _stringifies_the_exception(broad), (
        "o ramo largo carrega `str(exc)`: a mensagem de um bug de produtor pode ter sido formatada "
        "com dados do caso e viaja para tres superficies persistidas (detail/audit/fato)"
    )
    assert any(isinstance(n, ast.Raise) and n.exc is None for n in ast.walk(broad)), (
        "o ramo largo nao tem `raise` nu: o canal RETENTAVEL RAF-02 sumiu e uma falha transitoria "
        "seria selada como terminal"
    )
    assert any(isinstance(n, ast.Return) for n in ast.walk(broad)), (
        "o ramo largo nao devolve um `DelegationResult`: a excecao continuaria escapando"
    )


def test_the_terminal_error_classes_exclude_every_retryable_channel() -> None:
    """A fronteira e' um `isinstance` contra CLASSES REAIS, e e' testada contra os SIMBOLOS REAIS.

    Bug de produtor sobre um envelope imutavel (`ValueError`/`TypeError`/`KeyError` E SUBCLASSES)
    e' terminal; `StartProcessFailedError` (RAF-02) e `AuditPersistenceError` — familia
    `RuntimeError` — NAO sao. Renomear a excecao de RAF-02 nao muda mais nada (era o perigo da
    fronteira por NOME); adicionar a classe dela ao conjunto terminal mata este teste."""
    from maezo.a2a.dispatcher import _TERMINAL_HANDLER_ERROR_CLASSES
    from maezo.gateway.audit_postgres import AuditPersistenceError

    assert (ValueError, TypeError, KeyError) == _TERMINAL_HANDLER_ERROR_CLASSES
    for terminal in (
        ValueError("x"),
        TypeError("x"),
        KeyError("x"),
        _EnvelopeSchemaError("x"),
        _json_decode_error(),
    ):
        assert isinstance(terminal, _TERMINAL_HANDLER_ERROR_CLASSES), (
            f"{type(terminal).__name__} deixou de ser terminal: um bug de produtor voltaria a "
            "propagar sem selo e a reentrega o reexecutaria para sempre"
        )
    for retryable in (
        StartProcessFailedError("x"),
        AuditPersistenceError("x"),
        TimeoutError("x"),
        AttributeError("x"),
    ):
        assert not isinstance(retryable, _TERMINAL_HANDLER_ERROR_CLASSES), (
            f"{type(retryable).__name__} virou terminal: uma falha transitoria passaria a ser "
            "selada por `task_id` e a entrega deixaria de ser retentavel (RAF-02)"
        )

    # E a razao de a fronteira NAO poder ser o classificador: para as duas subclasses acima ele
    # devolve `outro` (lookup por NOME exato, sem MRO) — elas escapariam do conjunto terminal.
    for subclasse in (_EnvelopeSchemaError("x"), _json_decode_error()):
        assert classify_agent_error_type(subclasse) == AGENT_ERROR_TYPE_OUTRO
        assert isinstance(subclasse, _TERMINAL_HANDLER_ERROR_CLASSES)
    assert classify_agent_error_type(ValueError("x")) == AGENT_ERROR_TYPE_VALIDACAO


def test_the_terminal_boundary_is_decided_by_isinstance_not_by_the_label_classifier() -> None:
    """CERCA F1. O `if` que separa TERMINAL de RETENTAVEL tem de ser um `isinstance` contra
    `_TERMINAL_HANDLER_ERROR_CLASSES`.

    Voltar para `classify_agent_error_type(exc) in {...}` faz um `ValueError` NOMEADO e o
    `json.JSONDecodeError` deixarem de ser selados, porque aquele lookup e' por NOME exato — e a
    regressao seria invisivel enquanto os sete `state_from_envelope` levantassem `ValueError` cru.
    """
    broad = _broad_handler_branch()
    fronteiras = [n for n in ast.walk(broad) if isinstance(n, ast.If)]
    assert len(fronteiras) == 1, f"esperado UM `if` de fronteira no ramo largo, achei {len(fronteiras)}"
    teste = ast.unparse(fronteiras[0].test)
    assert "isinstance" in teste and "_TERMINAL_HANDLER_ERROR_CLASSES" in teste, (
        f"a fronteira terminal-vs-retentavel nao e' mais um `isinstance` por classe: {teste}"
    )
    assert "classify_agent_error_type" not in teste, (
        "o classificador de ROTULO voltou a ser o oraculo de fluxo: ele e' um lookup exato por "
        f"`type(exc).__name__`, sem walk de MRO, e subclasses escapam do conjunto terminal: {teste}"
    )


def test_the_retryable_branch_writes_the_trace_before_it_re_raises() -> None:
    """CERCA F1. O ramo que RELEVANTA tem de auditar e contar ANTES do `raise` nu.

    Sem isto o achado volta inteiro para toda classe nao-terminal: fato `requested` orfao, nenhuma
    linha de desfecho, nenhum contador — e, no caminho duravel, uma linha `processing` que ninguem
    sela, queimando o orcamento de `_poll_until_done` a cada reentrega.
    """
    broad = _broad_handler_branch()
    fronteiras = [n for n in ast.walk(broad) if isinstance(n, ast.If)]
    assert len(fronteiras) == 1
    corpo = fronteiras[0].body
    posicao_raise = next(
        i
        for i, stmt in enumerate(corpo)
        if any(isinstance(n, ast.Raise) and n.exc is None for n in ast.walk(stmt))
    )
    metodos = {
        node.name: node
        for node in ast.walk(_dispatcher_tree())
        if isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef)
    }
    tracadores = [
        node.func.attr
        for stmt in corpo[:posicao_raise]
        for node in ast.walk(stmt)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "self"
        and node.func.attr in metodos
    ]
    assert tracadores, (
        "o ramo RETENTAVEL relevanta sem chamar nada antes: uma excecao nao-terminal volta a "
        "atravessar `delegate()` sem linha de audit e sem contador — o achado F1"
    )
    corpos = "\n".join(ast.unparse(metodos[nome]) for nome in tracadores)
    for exigido in ("_audit_delegation_outcome", "record_a2a_handler_error"):
        assert exigido in corpos, f"o traco do ramo retentavel ({tracadores}) nao chama `{exigido}`"
    assert "_DECISION_COMPLETED" not in corpos, (
        "o traco NAO-terminal usaria a decisao COMPLETED: uma delegacao que apenas propagou "
        "ficaria registrada como concluida, e o pino RAF-02 exige o contrario"
    )


def test_the_delegate_contract_docstrings_no_longer_claim_an_unconditional_no_raise() -> None:
    """CC-04: os sete `delegate_*` afirmavam "never a raise out of the dispatcher" sem ressalva —
    falso hoje (RAF-02) e falso antes desta correcao (NEW-B1). Toda ocorrencia da frase tem de
    carregar a ressalva RAF-02 na MESMA docstring."""
    offenders: list[str] = []
    for path in sorted(Path("src/maezo/agents").rglob("delegation.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef):
                continue
            doc = ast.get_docstring(node) or ""
            if "never a raise out of the dispatcher" in doc and "RAF-02" not in doc:
                offenders.append(f"{path}::{node.name}")
    assert not offenders, (
        "docstring(s) afirmando um 'never a raise' incondicional que o canal retentavel RAF-02 "
        f"torna falso: {offenders}"
    )
