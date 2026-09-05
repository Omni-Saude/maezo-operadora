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

O QUE ESTE MODULO FIXA. A conversao e' NARROW de proposito e a fronteira e' o vocabulario fechado
`platform/error_types.py` (ALERT-COUNTER-LABELS / R-063), nao "toda excecao":

  - classe `validacao` (`ValueError`/`TypeError`/`KeyError`/`ValidationError`) = bug de PRODUTOR
    sobre um envelope IMUTAVEL -> a reentrega do mesmo envelope falharia identicamente, entao a
    falha e' TERMINAL: rejeicao estruturada + linha de audit `:outcome` + fato `rejected` +
    contador + selo de idempotencia.
  - qualquer outra classe (transitoria/nao classificada) -> PROPAGA, sem selo, exatamente como
    hoje. E' o canal RAF-02: `StartProcessFailedError` TEM de continuar retentavel
    (`test_start_failure_a2a_handlers.py::test_dispatcher_neither_seals_nor_completes_a_failed_start`),
    e `AuditPersistenceError` idem.
"""

from __future__ import annotations

import ast
import inspect
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
from maezo.platform.error_types import AGENT_ERROR_TYPE_VALIDACAO, classify_agent_error_type
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
    assert "FAILED" not in [r.decision for (r, _) in sink.emitted]
    assert TOPIC_REJECTED not in producer.topics()
    assert calls == [], "uma falha transitoria nao e' uma falha terminal de handler"


async def test_audit_sink_failure_inside_the_handler_still_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`AuditPersistenceError` levantada de DENTRO do handler tambem e' transitoria: propaga, sem
    selo — a mesma razao que o docstring de RAF-02 ja registra."""
    _capture_metric(monkeypatch)
    from maezo.gateway.audit_postgres import AuditPersistenceError

    async def audit_failing_handler(envelope: DelegationEnvelope) -> Any:
        raise AuditPersistenceError("audit chain unreachable (simulado)")

    store = _CountingStore()
    dispatcher, _, _ = _dispatcher_with_store(store, {"rafael": audit_failing_handler})

    with pytest.raises(AuditPersistenceError):
        await dispatcher.delegate(_envelope("transient-1"))
    assert store.completes == []


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
    body = ast.unparse(broad)
    for required in (
        "_audit_delegation_outcome",  # (b) do achado: a linha terminal duravel
        "_emit",  # (c) do achado: o fato `rejected`
        "record_a2a_handler_error",  # (d) do achado: o contador
        "classify_agent_error_type",  # a fronteira terminal-vs-retentavel
    ):
        assert required in body, f"o ramo largo nao chama `{required}`: {body[:200]}"
    assert any(isinstance(n, ast.Raise) and n.exc is None for n in ast.walk(broad)), (
        "o ramo largo nao tem `raise` nu: o canal RETENTAVEL RAF-02 sumiu e uma falha transitoria "
        "seria selada como terminal"
    )
    assert any(isinstance(n, ast.Return) for n in ast.walk(broad)), (
        "o ramo largo nao devolve um `DelegationResult`: a excecao continuaria escapando"
    )


def test_the_terminal_error_classes_exclude_every_retryable_channel() -> None:
    """A fronteira e' o vocabulario fechado R-063, e ela e' testada contra os SIMBOLOS REAIS.

    `validacao` (bug de produtor sobre um envelope imutavel) e' terminal; a classe de
    `StartProcessFailedError` (RAF-02) e a de `AuditPersistenceError` NAO sao — renomear a excecao
    de RAF-02, ou adicionar sua classe ao conjunto terminal, mata este teste."""
    from maezo.a2a.dispatcher import _TERMINAL_HANDLER_ERROR_TYPES
    from maezo.gateway.audit_postgres import AuditPersistenceError

    assert frozenset({AGENT_ERROR_TYPE_VALIDACAO}) == _TERMINAL_HANDLER_ERROR_TYPES
    for terminal in (ValueError("x"), TypeError("x"), KeyError("x")):
        assert classify_agent_error_type(terminal) in _TERMINAL_HANDLER_ERROR_TYPES
    for retryable in (StartProcessFailedError("x"), AuditPersistenceError("x"), TimeoutError("x")):
        assert classify_agent_error_type(retryable) not in _TERMINAL_HANDLER_ERROR_TYPES, (
            f"{type(retryable).__name__} virou terminal: uma falha transitoria passaria a ser "
            "selada por `task_id` e a entrega deixaria de ser retentavel (RAF-02)"
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
