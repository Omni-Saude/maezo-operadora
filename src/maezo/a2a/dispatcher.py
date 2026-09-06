"""A2A delegation dispatcher (ADR-0003, ADR-0007) — the SINGLE agent->agent delegation chokepoint.

Ported from the donor `Maezo-Healthcare-Plan` reference implementation (`src/maezo/a2a/
dispatcher.py:1-319`) as part of the T2.4 A2A W2 (delegation runtime) build wave — see
`docs/design/A2A-dispatcher-card-signing.md` §2/§3/§5 for the full port rationale, and this
module's `_audit_delegation` docstring for the AUDIT-SEAM REWRITE (the largest single port
friction — see design §2 friction table rows 1-3).

`DelegationDispatcher.delegate(envelope) -> DelegationResult` is the one place every agent->agent
delegation passes through. In this order:

  1. **Idempotency** (Guard 4): if `task_id` was already seen, return the prior result (or an
     in-flight handle) — NEVER re-execute the handler.
  2. **Anti-loop / contract**: validate the chain (guards 1-3 are already guaranteed at envelope
     construction) + that the target accepts the `task_type` (its Agent Card). Rejection ->
     a structured `DelegationResult` (never raised to the caller) + a `rejected` fact + an audit
     record.
  3. **Audit** the delegation (v2 `AuditRecord` via `emit_once`, T-F/design §3.2 "site 5") —
     delegation is an auditable external effect, audited BEFORE the effect.
  4. **Emit** the `requested` fact on Kafka before routing.
  5. **Route** to the target's handler (an injectable `agent_id -> handler` map). BOTH terminal
     outcomes get a durable TERMINAL-outcome audit row (T-F follow-up — see
     `_audit_delegation_outcome`): success -> a `COMPLETED` outcome row + a `completed` fact (the
     `task_id` is cached for idempotency); a handler-internal `DelegationError` (e.g. a cyclic
     sub-delegation) -> a `FAILED` outcome row + a `rejected` fact. The pre-exec ALLOW row is not
     itself terminal, so without these an ALLOWed delegation — whether it later completed or crashed
     — left no durable trace distinguishing the two (the facts are ephemeral observability, not the
     durable T-F audit). Uma excecao NAO-`DelegationError` do handler segue a fronteira de
     `_TERMINAL_HANDLER_ERROR_CLASSES` (NEW-B1), decidida por `isinstance`: `ValueError`/
     `TypeError`/`KeyError` E SUBCLASSES (bug de produtor sobre um envelope imutavel) -> a MESMA
     linha `FAILED` + fato `rejected`, razao `handler_error`, e o `task_id` selado; QUALQUER OUTRA
     classe PROPAGA sem selo — o canal retentavel de que RAF-02 (`StartProcessFailedError`) e
     `AuditPersistenceError` dependem, e por onde sai tambem qualquer bug nao classificado do
     grafo — mas sempre com a linha NAO-terminal `PROPAGATED` e o contador
     (`_trace_propagated_handler_error`). Nenhuma excecao atravessa `delegate()` sem rastro.

The runtime wires real handlers (agent graphs) in W3; here the handler is an injectable callable
(`FakeAgentHandler` in tests). Handler and registry resolution are both injected.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Protocol

import structlog

from maezo.gateway.audit import AuditRecord
from maezo.platform.error_types import classify_agent_error_type
from maezo.platform.observability import record_a2a_handler_error
from maezo.tools.workers.phi_vars import redact_error_message

from .delegation import DelegationEnvelope, DelegationError
from .facts import DelegationFact, DelegationFactKind, build_fact
from .registry import A2ARegistry, RegistryError

logger = structlog.get_logger(__name__)

if TYPE_CHECKING:
    from .envelope_signing import EnvelopeSigner, EnvelopeVerifier
    from .idempotency import IdempotencyStore, StoredResult

# Handler of a target agent: receives the envelope, returns an output reference (output_ref). Like
# everything A2A, the output is a REFERENCE (FHIR/pseudonymized), never raw PHI.
AgentHandler = Callable[[DelegationEnvelope], Awaitable["HandlerOutput"]]


class AuditEmitter(Protocol):
    """The exactly-once audit-emit seam this dispatcher requires (T-F, design §3.2 "site 5").

    Structurally identical to `maezo.tools.mcp_cibseven.transport.AuditStartSink` — both are
    satisfied by the SAME production sink, `maezo.gateway.audit_postgres.PostgresAuditSink` — but
    declared LOCALLY rather than imported from `tools.mcp_cibseven`: per the design doc, A2A
    delegation is a DISTINCT effect surface (§2.1 site 5, "the second effect surface"), not a
    client of the process-start transport module, and the two seams should be free to evolve
    independently even though they share an implementation today. Typed as a Protocol (not the
    concrete class) so unit tests can inject a recording fake with no `asyncpg` import.
    """

    async def emit_once(self, record: AuditRecord, *, dedup_key: str) -> str: ...


def a2a_audit_dedup_key(tenant: str, task_id: str) -> str:
    """Exactly-once audit dedup key for an A2A delegation (design §3.2/§7.2 decision #4).

    ``f"{tenant}:a2a:delegate:{task_id}"`` — mirrors `tools.mcp_cibseven.transport.
    start_dedup_key`'s ``f"{tenant}:start:{process_key}:{business_key}"`` shape. Delegation is
    ALREADY `task_id`-idempotent (Guard 4), so keying the audit dedup the same way needs no extra
    business identifier. Exactly one `_audit_delegation` (pre-exec ALLOW/DENY) call happens per
    `_execute` invocation; the TERMINAL-outcome audit (`_audit_delegation_outcome`, W4) is a
    DISTINCT, second call with its own key (`a2a_audit_outcome_dedup_key`) — never the same key,
    so `emit_once` never collapses the two into one chain link.
    """
    return f"{tenant}:a2a:delegate:{task_id}"


def a2a_audit_outcome_dedup_key(tenant: str, task_id: str) -> str:
    """Exactly-once audit dedup key for the TERMINAL delegation-outcome audit (T-F follow-up, W4).

    ``f"{tenant}:a2a:delegate:{task_id}:outcome"`` — the `:outcome` suffix is what distinguishes
    this row from the pre-execution ALLOW row's key (`a2a_audit_dedup_key`), so the two audits
    are independently deduplicated and never collide under `emit_once`.
    """
    return f"{tenant}:a2a:delegate:{task_id}:outcome"


def a2a_audit_propagated_dedup_key(tenant: str, task_id: str) -> str:
    """Chave exactly-once do traco NAO-TERMINAL de uma excecao que o dispatcher RELEVANTA.

    ``f"{tenant}:a2a:delegate:{task_id}:propagated"`` — uma TERCEIRA chave, e ela precisa mesmo ser
    a terceira. Reusar `a2a_audit_outcome_dedup_key` seria um defeito silencioso: `emit_once` e'
    exactly-once POR CHAVE, entao a linha `COMPLETED` da entrega que — depois de uma falha
    transitoria — finalmente desse certo seria engolida como duplicata, e o traco novo teria
    APAGADO o registro terminal que o T-F exige (`test_a_propagated_delivery_does_not_consume_the_
    terminal_outcome_dedup_key`).

    A consequencia deliberada da chave ser por `task_id` (e nao por entrega, que nao existe como
    conceito aqui): reentregas sucessivas da MESMA delegacao produzem UMA linha `PROPAGATED`
    duravel, nao N. A cadencia por entrega esta no contador `maezo_a2a_handler_error_total`, que
    nao passa por `emit_once`.
    """
    return f"{tenant}:a2a:delegate:{task_id}:propagated"


# v2 `AuditRecord.decision` follows the PEP ALLOW/DENY/REQUIRE_HUMAN convention (`gateway/
# audit.py`'s own docstring) — delegation admission is a binary allow/deny gate (no
# REQUIRE_HUMAN path exists in the dispatcher).
_DECISION_ALLOW = "ALLOW"
_DECISION_DENY = "DENY"

# Terminal-outcome decisions (T-F follow-up): distinct from the pre-exec ALLOW/DENY admission
# vocabulary above, so a chain reader never mistakes a SECOND, terminal row for a second admission
# decision. Both record what became of an ALREADY-ALLOWED delegation AFTER its handler ran:
#   - `_DECISION_FAILED`    — the handler raised a structural `DelegationError` (e.g. an internal
#                             cyclic sub-delegation).
#   - `_DECISION_COMPLETED` — the handler returned successfully (T-F completeness fix): without a
#                             terminal SUCCESS row the durable chain recorded only the pre-exec
#                             ALLOW, leaving a completed delegation indistinguishable from one still
#                             in flight / crashed (the `completed` Kafka fact is ephemeral
#                             observability, NOT the durable T-F audit). See
#                             `_audit_delegation_outcome`.
_DECISION_FAILED = "FAILED"
_DECISION_COMPLETED = "COMPLETED"

# NAO-terminal, e e' esse o ponto. Registra que uma entrega ALLOWed terminou por PROPAGACAO de uma
# excecao do handler: a delegacao NAO foi selada, a reentrega vai reexecutar, e portanto ela nao e'
# nem `_DECISION_COMPLETED` nem `_DECISION_FAILED`. Escrever qualquer uma das duas aqui quebraria o
# pino RAF-02 (`tests/unit/agents/test_start_failure_a2a_handlers.py::
# test_dispatcher_neither_seals_nor_completes_a_failed_start`, que exige `"COMPLETED" not in
# decisoes`) e, pior, mentiria para quem le a cadeia: um desfecho terminal que nao ocorreu.
_DECISION_PROPAGATED = "PROPAGATED"

# NEW-B1 — a FRONTEIRA entre uma falha de handler TERMINAL e uma RETENTAVEL. Ela e um teste de
# `isinstance` contra CLASSES DE EXCECAO, e essa escolha e o conserto de um defeito real: a versao
# anterior perguntava `classify_agent_error_type(exc) in {validacao}`, e aquele classificador e um
# lookup EXATO por `type(exc).__name__`, SEM walk de MRO — o proprio `platform/error_types.py`
# documenta a ausencia do walk como decisao deliberada, porque ele existe para limitar a
# CARDINALIDADE de um rotulo de metrica (ALERT-COUNTER-LABELS / R-063), nao para decidir
# retentabilidade. Consequencia medida: um `json.JSONDecodeError` (subclasse de `ValueError`) e
# qualquer `class X(ValueError)` de dominio caiam no balde `outro` e escapavam CRUS — o defeito
# NEW-B1 inteiro, reaberto por uma subclasse, sem nenhum teste notando.
#
# `classify_agent_error_type` continua no ramo, mas SO' no papel dele: o ROTULO limitado da metrica
# e do `detail`. Nunca o oraculo de fluxo.
#
# TERMINAL = `ValueError`/`TypeError`/`KeyError` E SUBCLASSES: o BUG DE PRODUTOR, em que o handler
# recusou o proprio ENVELOPE. O envelope e imutavel e a idempotencia e por `task_id`, entao a
# reentrega da MESMA entrega falharia identicamente para sempre — a falha e terminal, e a resposta
# certa e uma rejeicao estruturada + selo, como qualquer outra rejeicao (ADR-0003: uma rejeicao
# nunca retenta). `DelegationError` tambem herda de `ValueError`, mas nunca chega aqui: o
# `except DelegationError` acima o captura primeiro, e o classifica do mesmo lado da fronteira.
#
# TODA OUTRA CLASSE PROPAGA, sem selo e sem linha terminal — mas NUNCA em silencio: desde o traco
# de `_trace_propagated_handler_error` ela sempre deixa linha de audit e contador. E o canal
# RETENTAVEL de que dois mecanismos vivos dependem, ambos da familia `RuntimeError`:
# `StartProcessFailedError` (RAF-02 — os handlers a levantam DE PROPOSITO para que uma
# indisponibilidade transitoria do engine NAO vire um sucesso/uma rejeicao selada e irretentavel,
# ver `tests/unit/agents/test_start_failure_a2a_handlers.py::
# test_dispatcher_neither_seals_nor_completes_a_failed_start`) e `AuditPersistenceError` (um sink de
# audit indisponivel ja propaga hoje, `test_dispatcher.py::
# test_audit_sink_failure_propagates_and_handler_never_runs`). Converter esses em rejeicao terminal
# REINTRODUZIRIA o agravante RAF-02 numa forma nova. Junto com eles propaga tambem qualquer BUG NAO
# CLASSIFICADO do grafo (`AttributeError`, `IndexError`, `ZeroDivisionError`, ...): a escolha
# conservadora e deixar a entrega retentavel e VISIVEL, nunca sela-la por engano.
#
# A fronteira e verificada contra os SIMBOLOS REAIS em `tests/unit/a2a/
# test_dispatcher_handler_escape.py::test_the_terminal_error_classes_exclude_every_retryable_channel`
# e a sua FORMA (isinstance, nao classificador) em
# `::test_the_terminal_boundary_is_decided_by_isinstance_not_by_the_label_classifier`.
_TERMINAL_HANDLER_ERROR_CLASSES: tuple[type[Exception], ...] = (ValueError, TypeError, KeyError)


class FactProducer:
    """Thin adapter over the injected Kafka producer, partitioned by tenant."""

    def __init__(self, producer: KafkaLike) -> None:
        self._producer = producer

    async def emit(self, fact: DelegationFact) -> None:
        await self._producer.send(
            fact.topic,
            fact.to_value(),
            key=fact.tenant.encode("utf-8"),
        )


class KafkaLike(Protocol):
    """Structural protocol for the producer (same shape as `gateway.audit.KafkaProducer`)."""

    async def send(self, topic: str, value: bytes, *, key: bytes | None = None) -> None: ...


class RejectionReason(StrEnum):
    UNKNOWN_TARGET = "unknown_target"
    TASK_TYPE_NOT_ACCEPTED = "task_type_not_accepted"
    NO_HANDLER = "no_handler"
    EXPIRED = "expired"
    ANTI_LOOP = "anti_loop"
    # ADR-0039 §4.4: envelope signature missing/invalid. New vocabulary the ADR authorizes. The
    # FIRST check inside `delegate` (ahead of any idempotency claim/audit/routing) returns this as a
    # structured rejection — never a raise (the dispatcher's 'never raised to caller' contract).
    SIGNATURE_INVALID = "signature_invalid"
    # NEW-B1: o handler ALVO falhou TERMINALMENTE (uma excecao de classe `validacao` — bug de
    # produtor sobre um envelope imutavel), em vez de devolver um `HandlerOutput`. Antes desta
    # entrada a excecao escapava CRUA de `delegate()`, deixando um fato `requested` sem irmao, sem
    # linha de audit terminal e sem contador. `detail` carrega SO o token de CLASSE da excecao,
    # nunca `str(exc)`. Token novo no vocabulario: ver `_reject_handler_error`.
    HANDLER_ERROR = "handler_error"


@dataclass(frozen=True, slots=True)
class HandlerOutput:
    """A target agent handler's output: a result reference (no PHI) + optional meta."""

    output_ref: str
    meta: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class DelegationResult:
    """Structured result of `delegate`. Success or rejection — nunca um raise por FALHA TERMINAL.

    A REGRA REAL, enunciada sem exclusividade falsa: a fronteira e' `_TERMINAL_HANDLER_ERROR_CLASSES`
    (`ValueError`/`TypeError`/`KeyError` E SUBCLASSES, por `isinstance`). Toda falha TERMINAL de
    handler volta como `rejection_reason=HANDLER_ERROR` (NEW-B1) e e' selada; TODA OUTRA excecao do
    handler ATRAVESSA `delegate()` sem selo — e sao duas familias, nao uma: o canal RETENTAVEL
    RAF-02 (`StartProcessFailedError`, `AuditPersistenceError`), que propaga de proposito para que a
    entrega continue retentavel, E qualquer bug NAO CLASSIFICADO do grafo alvo (`AttributeError`,
    `IndexError`, `ZeroDivisionError`, ...), que propaga porque selar um bug desconhecido seria
    pior. Um chamador que precise ser exaustivo TEM de tratar `except Exception`, nao apenas as
    classes transitorias.

    O que nao acontece mais em nenhum dos dois casos: propagar SEM RASTRO. Antes do traco de
    `DelegationDispatcher._trace_propagated_handler_error`, uma excecao nao-terminal saia com o
    fato `requested` orfao, sem linha de desfecho e sem contador; hoje ela deixa a linha
    NAO-terminal `PROPAGATED` e uma contagem em `maezo_a2a_handler_error_total` por entrega.

    `idempotent_replay=True` indicates this response came from the `task_id` cache (Guard 4): the
    handler did NOT run again.

    `meta` (DL-0033 real-wiring follow-through, dossier A2A edges): the target handler's
    `HandlerOutput.meta` — bounded, non-PHI `str -> str` summary tokens (route/desfecho class
    tokens, never a decision, never the dossier body) — propagated back to the ORIGINATOR. This is
    the ONLY channel from a target handler back to an origin worker; without it the origin could
    not carry the agent-produced compact summary into its own output variables, and an idempotent
    REPLAY would return a different (meta-less) shape than the first delivery — an
    idempotency-semantics break for engine-retried external tasks. Persisted alongside the
    terminal result (`StoredResult.meta`, `a2a_idempotency.result` jsonb) for exactly that reason.
    """

    task_id: str
    success: bool
    output_ref: str | None = None
    rejection_reason: RejectionReason | None = None
    detail: str | None = None
    idempotent_replay: bool = False
    meta: Mapping[str, str] = field(default_factory=dict)

    @classmethod
    def ok(
        cls,
        task_id: str,
        output_ref: str,
        *,
        replay: bool = False,
        meta: Mapping[str, str] | None = None,
    ) -> DelegationResult:
        return cls(
            task_id=task_id,
            success=True,
            output_ref=output_ref,
            idempotent_replay=replay,
            meta=dict(meta or {}),
        )

    @classmethod
    def rejected(
        cls, task_id: str, reason: RejectionReason, *, detail: str | None = None
    ) -> DelegationResult:
        return cls(task_id=task_id, success=False, rejection_reason=reason, detail=detail)


@dataclass
class _InflightEntry:
    """Per-`task_id` state for idempotency. A lock guarantees a single concurrent execution."""

    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    result: DelegationResult | None = None
    #: A2A-RETRY-REEMITS-REQUESTED-FACT: set on the FIRST attempt at this `task_id` (in-memory
    #: mirror of the durable store's row-already-exists signal, `idempotency.claim_or_get`'s
    #: `False` outcome). A retryable handler failure leaves `result` `None` — this flag is what
    #: lets a REDELIVERY (`_delegate_inflight` re-entering with the SAME entry) tell `_execute` to
    #: skip re-emitting `agents.events.delegation.requested`, without preventing the re-execution
    #: RAF-02's retryable channel depends on.
    requested_emitted: bool = False


def _as_replay(prev: DelegationResult) -> DelegationResult:
    """Mark a prior result as an idempotent replay (Guard 4), without re-executing the handler."""
    return DelegationResult(
        task_id=prev.task_id,
        success=prev.success,
        output_ref=prev.output_ref,
        rejection_reason=prev.rejection_reason,
        detail=prev.detail,
        idempotent_replay=True,
        meta=prev.meta,
    )


def _handler_error_detail(exc: BaseException, error_type: str) -> str:
    """Descricao PHI-free e limitada de uma falha de handler (NEW-B1) — nunca `str(exc)`.

    Duas partes, ambas de baixa cardinalidade: o TOKEN DE CLASSE da excecao (o que um operador
    precisa para diagnosticar QUE especie de falha ocorreu) e o `error_type` do vocabulario fechado
    R-063. A mensagem da excecao fica de fora INTEIRA — um bug de produtor pode te-la formatado com
    dados do caso, e este texto viaja para tres superficies persistidas (o `detail` da
    `DelegationResult`, que e gravado em `a2a_idempotency.result`; a linha de audit `:outcome`; e o
    fato `rejected` no Kafka).

    `type(exc).__name__` e um identificador Python em todo caso normal, mas nao ha nada que impeca
    uma classe criada dinamicamente de carregar um `__name__` arbitrario — entao o resultado ainda
    passa por `redact_error_message` (o net compartilhado de `tools/workers/phi_vars.py`), que
    redige as familias de identificador e limita o comprimento. Reuso, nao um redator novo.
    """
    return redact_error_message(f"handler raised {type(exc).__name__} (error_type={error_type})")


def _stored_to_result(stored: StoredResult) -> DelegationResult:
    """Reconstruct a `DelegationResult` (replay) from the durable `StoredResult`.

    The persisted `rejection_reason` (a string) maps back to the `RejectionReason` enum when
    recognized; if it doesn't map (a future/unknown shape), the outcome is preserved without a
    typed reason (`detail` still carries the reason). Always `idempotent_replay=True` — the
    handler did NOT run on this delivery.
    """
    reason: RejectionReason | None = None
    if stored.rejection_reason is not None:
        try:
            reason = RejectionReason(stored.rejection_reason)
        except ValueError:
            reason = None
    return DelegationResult(
        task_id=stored.task_id,
        success=stored.success,
        output_ref=stored.output_ref,
        rejection_reason=reason,
        detail=stored.detail,
        idempotent_replay=True,
        meta=stored.meta,
    )


class DelegationDispatcher:
    """Validates (anti-loop), audits, emits a fact, and routes a delegation to its target agent.

    Injected dependencies: `registry` (Agent Cards), `handlers` (agent_id->handler map), `audit`
    (the `emit_once` seam, T-F) and `facts` (fact producer). Idempotency by `task_id`:

      - `idempotency=None` (default): IN-MEMORY backing store (`_inflight`). Correct for a single
        process/test; the landmine is that it does NOT survive replica restarts (re-delegation).
      - `idempotency` set (`IdempotencyStore`): DURABLE cross-replica backing store (R9). The
        atomic per-`task_id` claim guarantees single execution even across replicas/restarts; a
        replay returns the persisted terminal result (`idempotent_replay=True`).
    """

    def __init__(
        self,
        *,
        registry: A2ARegistry,
        handlers: Mapping[str, AgentHandler],
        audit: AuditEmitter,
        facts: FactProducer,
        idempotency: IdempotencyStore | None = None,
        envelope_verifier: EnvelopeVerifier | None = None,
        origin_envelope_signer: EnvelopeSigner | None = None,
    ) -> None:
        self._registry = registry
        self._handlers = dict(handlers)
        self._audit = audit
        self._facts = facts
        self._idempotency = idempotency
        self._inflight: dict[str, _InflightEntry] = {}
        self._guard = asyncio.Lock()  # protects the in-flight dict (entry creation)
        # ADR-0039 §4.4 envelope-signature verification. `None` (default / dev) -> verification is
        # OFF (unsigned envelopes accepted, W2/dev behaviour preserved). Present -> `_verify_or_reject`
        # is the FIRST admission check in `delegate`. Wired fail-closed by the composition root
        # (`_require_envelope_verifier_or_fail_closed`).
        self._envelope_verifier = envelope_verifier
        # The edge's per-tenant ORIGIN signer (the SIGN half of the same symmetric per-tenant key
        # whose VERIFY half is `_envelope_verifier` — mirroring Card signing's 'one key both jobs').
        # It is NOT used by the dispatcher; it rides here purely as the edge-custody handle the
        # per-request originators (`agents/*/delegation.py::delegate_*`) retrieve via
        # `origin_signer_of(dispatcher)` to sign at construction, in this in-process Option-A runtime.
        self._origin_envelope_signer = origin_envelope_signer

    def _verify_or_reject(self, envelope: DelegationEnvelope) -> DelegationResult | None:
        """ADR-0039 §4.4 fail-closed signature gate — `None` = proceed, else a structured rejection.

        No verifier wired (dev / opt-out) -> `None` (verification off). Otherwise the envelope's
        signature must be valid under the trusted per-tenant keyset (current key + prior-in-grace,
        current epoch + optional short grace, unexpired, MAC over the v2 digest) — an unsigned,
        tampered, cross-tenant-retagged, stale-key, prior-epoch or expired envelope yields a
        `SIGNATURE_INVALID` rejection. NEVER raises (the 'never raised to caller' contract)."""
        if self._envelope_verifier is None:
            return None
        if self._envelope_verifier.verify(envelope):
            return None
        return DelegationResult.rejected(
            envelope.task_id,
            RejectionReason.SIGNATURE_INVALID,
            detail="envelope signature missing or invalid (ADR-0039 §4.4)",
        )

    async def delegate(self, envelope: DelegationEnvelope) -> DelegationResult:
        """Dispatch a delegation. Idempotent by `task_id`; rejection = a structured result."""
        # ADR-0039 §4.4: signature verification is the FIRST check, AHEAD of the durable-vs-inflight
        # branch below — so a forged/tampered envelope never claims an idempotency key of the
        # attacker's choosing (`_delegate_durable`/`_delegate_inflight` claim BEFORE `_execute`),
        # never triggers a registry lookup, and never reaches the audit emission in `_execute`.
        rejected = self._verify_or_reject(envelope)
        if rejected is not None:
            return rejected
        if self._idempotency is not None:
            return await self._delegate_durable(self._idempotency, envelope)
        return await self._delegate_inflight(envelope)

    async def _delegate_inflight(self, envelope: DelegationEnvelope) -> DelegationResult:
        """IN-MEMORY path (idempotency=None): Guard 4 via `_inflight` + a per-task_id lock."""
        entry = await self._entry_for(envelope.task_id)
        async with entry.lock:
            if entry.result is not None:
                # Guard 4 — re-delivery of the same task_id: return the prior result, without
                # re-executing the handler.
                return _as_replay(entry.result)
            # A2A-RETRY-REEMITS-REQUESTED-FACT: `entry.result is None` here covers BOTH a
            # brand-new task_id and a REDELIVERY after a retryable failure left it unsealed
            # (`entry.result` is only ever set on the line below, which a raised exception never
            # reaches). `requested_emitted` is the durable-in-this-process signal that
            # distinguishes the two — set on the first attempt, so a redelivery skips the fact
            # without skipping the (retryable) re-execution.
            skip_requested_fact = entry.requested_emitted
            entry.requested_emitted = True
            result = await self._execute(envelope, skip_requested_fact=skip_requested_fact)
            entry.result = result
            return result

    async def _delegate_durable(
        self, store: IdempotencyStore, envelope: DelegationEnvelope
    ) -> DelegationResult:
        """DURABLE path (R9): atomic cross-replica claim -> execute -> seal the result.

        - `claim_or_get` WON (`None`) -> execute the handler and seal with `complete` (success OR
          rejection — BOTH are terminal; a rejection never retries, ADR-0003).
        - `claim_or_get` returns a `StoredResult` -> replay: the handler does NOT run; the
          persisted outcome comes back with `idempotent_replay=True`.

        NEW-B1 — o SELO DA FALHA vive aqui e nao precisou de mecanismo novo. Uma falha TERMINAL de
        handler agora volta de `_execute` como uma `DelegationResult` REJEITADA, entao a
        `store.complete` desta linha a sela como qualquer outra rejeicao: a reentrega do mesmo
        `task_id` devolve o resultado guardado em vez de reexecutar. Antes, a excecao crua saltava
        POR CIMA desta chamada — `claim_or_get` ja tinha criado a linha `processing` e ninguem a
        selava, entao cada reentrega gastava todo o `PostgresIdempotencyStore._poll_until_done`
        (`_POLL_MAX_ATTEMPTS x _POLL_INTERVAL_S`) para so entao reexecutar e relevantar, para sempre.
        Uma falha RETENTAVEL continua saltando por cima desta chamada, e e assim que RAF-02 mantem a
        entrega retentavel — a mesma linha `processing`, o mesmo poll, mas agora e uma escolha
        registrada em vez de um efeito colateral.

        A2A-RETRY-REEMITS-REQUESTED-FACT: `claim_or_get` returning `False` (see `IdempotencyStore.
        claim_or_get`'s own docstring) is a THIRD outcome — a REDELIVERY of a `task_id` an earlier
        attempt already claimed but never sealed. The handler still runs (retryability preserved,
        same as the `None`/fresh-claim path), but `_execute` is told to skip re-emitting
        `requested`: an earlier attempt already emitted it, and re-emitting it here would be the
        exact duplicate this fix closes.
        """
        stored = await store.claim_or_get(tenant=envelope.tenant, task_id=envelope.task_id)
        if stored is None:
            result = await self._execute(envelope)
        elif stored is False:
            result = await self._execute(envelope, skip_requested_fact=True)
        else:
            return _stored_to_result(stored)
        await store.complete(tenant=envelope.tenant, task_id=envelope.task_id, result=result)
        return result

    async def _entry_for(self, task_id: str) -> _InflightEntry:
        async with self._guard:
            entry = self._inflight.get(task_id)
            if entry is None:
                entry = _InflightEntry()
                self._inflight[task_id] = entry
            return entry

    async def _execute(
        self, envelope: DelegationEnvelope, *, skip_requested_fact: bool = False
    ) -> DelegationResult:
        # --- Contract validation (chain/hops/budget already guaranteed at construction) ---
        validation = self._validate(envelope)
        if validation is not None:
            await self._audit_delegation(
                envelope,
                decision=_DECISION_DENY,
                basis=f"A2A:reject:{validation.rejection_reason}",
            )
            await self._emit(
                envelope,
                DelegationFactKind.REJECTED,
                reason=validation.detail or str(validation.rejection_reason),
            )
            return validation

        # --- Audit BEFORE the effect (delegation is an auditable external effect, ADR-0007) ---
        await self._audit_delegation(envelope, decision=_DECISION_ALLOW, basis="A2A:delegate:allow")
        # A2A-RETRY-REEMITS-REQUESTED-FACT: `skip_requested_fact` is set ONLY by a caller that
        # already knows THIS `task_id` was claimed by an earlier attempt (`_delegate_durable`'s
        # `False` branch, `_delegate_inflight`'s `requested_emitted` flag) — never inferred here.
        # The pre-exec ALLOW audit row above is unaffected: `emit_once`'s OWN durable dedup key
        # (`a2a_audit_dedup_key`) already collapses a repeated call to one row, so re-running it on
        # a redelivery is safe and cheap; only the raw Kafka fact (no such dedup) needs the gate.
        if not skip_requested_fact:
            await self._emit(envelope, DelegationFactKind.REQUESTED)

        # --- Route to the target's handler ---
        handler = self._handlers[envelope.target]
        try:
            output = await handler(envelope)
        except DelegationError as exc:
            # Structural failure raised by the handler (e.g. a cyclic sub-delegation): rejection.
            reason = RejectionReason.ANTI_LOOP
            # TERMINAL-outcome audit (T-F follow-up): the pre-exec ALLOW row above already fired —
            # without this, an ALLOWed-then-internally-failed delegation would leave no audit trace
            # distinguishing it from one still silently in flight (see `_audit_delegation_outcome`).
            await self._audit_delegation_outcome(
                envelope,
                decision=_DECISION_FAILED,
                basis=f"A2A:handler_error:{reason}",
                detail=str(exc),
            )
            await self._emit(envelope, DelegationFactKind.REJECTED, reason=str(exc))
            return DelegationResult.rejected(envelope.task_id, reason, detail=str(exc))
        except Exception as exc:
            # NEW-B1 — o RAMO LARGO COMPANHEIRO. `except DelegationError` sozinho era o defeito: os
            # oito `agents/*/delegation.py` chamam `state_from_envelope(envelope)` FORA do `try` do
            # proprio handler e sete deles levantam `ValueError` num bug de produtor, entao essa
            # excecao atravessava `_execute`, `_delegate_inflight`/`_delegate_durable` e `delegate`
            # inteiros e chegava CRUA ao chamador — deixando um fato `requested` orfao, nenhuma
            # linha de audit terminal, nenhum contador e (no caminho duravel) uma linha
            # `processing` que ninguem sela.
            #
            # `Exception`, JAMAIS `BaseException`: uma delegacao drenada por `asyncio.CancelledError`
            # nao e uma falha de handler (a mesma regra que os `except` dos handlers seguem).
            if not isinstance(exc, _TERMINAL_HANDLER_ERROR_CLASSES):
                # Classe RETENTAVEL: propaga, SEM selo, SEM linha terminal e SEM fato novo — a
                # semantica de retentativa de sempre, preservada de proposito (ver
                # `_TERMINAL_HANDLER_ERROR_CLASSES`, canal RAF-02). O que ela NAO e' mais e
                # INVISIVEL: o traco (linha de desfecho NAO-terminal + contador) e escrito ANTES
                # do `raise`.
                await self._trace_propagated_handler_error(envelope, exc)
                raise
            return await self._reject_handler_error(envelope, exc)

        # TERMINAL-outcome audit (T-F completeness fix, LOW-1): the SUCCESS path is symmetric to the
        # handler-error branch above — the pre-exec ALLOW row is not itself terminal, so a durable
        # COMPLETED row is what lets a chain reader tell a delegation that finished from one that was
        # allowed then crashed / is still in flight. Audited BEFORE the `completed` fact, mirroring
        # the error branch's audit-before-fact ordering (the fact is ephemeral observability; the
        # audit row is the durable T-F record).
        await self._audit_delegation_outcome(
            envelope,
            decision=_DECISION_COMPLETED,
            basis="A2A:delegate:completed",
        )
        await self._emit(envelope, DelegationFactKind.COMPLETED, output_ref=output.output_ref)
        # `output.meta` rides back to the originator (and into the durable idempotency record):
        # bounded non-PHI summary tokens only — `HandlerOutput.meta`'s own contract. The facts/
        # audit surfaces above deliberately keep EXCLUDING meta (unchanged posture).
        return DelegationResult.ok(envelope.task_id, output.output_ref, meta=output.meta)

    async def _reject_handler_error(self, envelope: DelegationEnvelope, exc: Exception) -> DelegationResult:
        """Converte uma falha TERMINAL de handler numa rejeicao estruturada (NEW-B1).

        Chamada apenas pelo ramo largo de `_execute`, e apenas para as classes de
        `_TERMINAL_HANDLER_ERROR_CLASSES` (por `isinstance`, subclasses incluidas). Faz, nesta
        ordem e exatamente uma vez:

          1. a linha de audit TERMINAL (`_audit_delegation_outcome`, `FAILED`, chave `:outcome`) —
             primeiro, porque e o registro DURAVEL; se o sink estiver indisponivel a sua
             `AuditPersistenceError` propaga, igual ao ramo `DelegationError` que esta logo acima;
          2. o fato `rejected`, que da ao `requested` ja emitido o irmao terminal que faltava;
          3. o contador `maezo_a2a_handler_error_total` (`record_a2a_handler_error` — contador
             PROPRIO e nao `maezo_agent_errors_total`, ver o docstring daquela funcao);
          4. uma linha de log estruturada em nivel WARNING.

        E devolve uma `DelegationResult` rejeitada, que os dois caminhos de idempotencia SELAM como
        terminal pelos seus mecanismos ja existentes — nenhum "selo de falha" novo foi inventado:
        `_delegate_inflight` guarda em `entry.result` e `_delegate_durable` chama `store.complete`,
        cujo contrato ja e "success OR rejection — BOTH are terminal; a rejection never retries"
        (ADR-0003). E isso que impede a reentrega de reexecutar o handler para sempre e, no
        Postgres, de queimar todo o orcamento de `_poll_until_done` sobre uma linha `processing`
        que ninguem selaria.

        SEGURANCA PHI: `str(exc)` NUNCA viaja. A mensagem de um bug de produtor pode ter sido
        formatada com dados do caso; so o TOKEN DE CLASSE (`type(exc).__name__`) e o rotulo limitado
        `error_type` entram no `detail`, no fato, no audit e no log — e mesmo essa cadeia passa por
        `redact_error_message` (o mesmo net de `phi_vars` usado no chokepoint agente->engine), que
        tambem a limita em tamanho, para que nem um `__name__` patologico possa vazar ou crescer.
        """
        error_type = classify_agent_error_type(exc)
        detail = _handler_error_detail(exc, error_type)
        reason = RejectionReason.HANDLER_ERROR
        await self._audit_delegation_outcome(
            envelope,
            decision=_DECISION_FAILED,
            basis=f"A2A:handler_error:{reason}",
            detail=detail,
        )
        await self._emit(envelope, DelegationFactKind.REJECTED, reason=detail)
        record_a2a_handler_error(target=envelope.target, error_type=error_type)
        logger.warning(
            "a2a_handler_error",
            task_id=envelope.task_id,
            tenant=envelope.tenant,
            origin=envelope.origin,
            target=envelope.target,
            task_type=envelope.task_type,
            error_class=type(exc).__name__,
            error_type=error_type,
        )
        return DelegationResult.rejected(envelope.task_id, reason, detail=detail)

    async def _trace_propagated_handler_error(self, envelope: DelegationEnvelope, exc: Exception) -> None:
        """Registra a excecao que o dispatcher esta prestes a RELEVANTAR, sem mudar nada mais.

        A metade do NEW-B1 que a primeira correcao deixou aberta: uma falha de handler que nao e'
        TERMINAL continua propagando (e tem de continuar — e' o canal retentavel RAF-02), mas ela
        atravessava `delegate()` sem NENHUM rastro: fato `requested` orfao, nenhuma linha de
        desfecho, nenhum contador, e no caminho duravel uma linha `processing` que ninguem sela.
        Retentabilidade e observabilidade eram protegidas pelo MESMO ramo; aqui elas se separam.

        Faz DUAS coisas, e so' duas:

          1. a linha de audit de desfecho com `decision=_DECISION_PROPAGATED` — NAO-terminal de
             proposito — na chave `a2a_audit_propagated_dedup_key`, que nunca e' a chave
             `:outcome` (ver aquela funcao: reusa-la apagaria o `COMPLETED` de uma entrega
             posterior bem-sucedida);
          2. o contador `maezo_a2a_handler_error_total`, com o MESMO rotulo fechado R-063 do ramo
             terminal — e' ele que da a cadencia POR ENTREGA que a linha de audit, deduplicada por
             `task_id`, deliberadamente nao da.

        O que NAO faz, e por que: NENHUM fato novo. O pino RAF-02
        (`test_dispatcher_neither_seals_nor_completes_a_failed_start`) exige
        `producer.topics() == [TOPIC_REQUESTED]` numa entrega que falhou no start, e um fato
        `rejected` aqui anunciaria uma rejeicao que nao houve — a delegacao continua viva e
        retentavel. Tambem NAO sela e NAO devolve: quem chamou continua recebendo a excecao
        original.

        ORDEM E FALHA DO SINK: audit primeiro, contador depois, igual a `_reject_handler_error`. Se
        o sink estiver indisponivel a `AuditPersistenceError` dele propaga NO LUGAR da excecao
        original — o mesmo comportamento fail-closed do ramo `DelegationError` logo acima, e sem
        consequencia semantica aqui, porque as duas propagam e nenhuma das duas sela.

        PHI: identica ao ramo terminal — `str(exc)` NUNCA viaja; so' o TOKEN DE CLASSE e o rotulo
        limitado entram, via `_handler_error_detail` (que ainda passa por `redact_error_message`).
        """
        error_type = classify_agent_error_type(exc)
        detail = _handler_error_detail(exc, error_type)
        await self._audit_delegation_outcome(
            envelope,
            decision=_DECISION_PROPAGATED,
            basis="A2A:handler_error:propagated",
            detail=detail,
            dedup_key=a2a_audit_propagated_dedup_key(envelope.tenant, envelope.task_id),
        )
        record_a2a_handler_error(target=envelope.target, error_type=error_type)
        logger.warning(
            "a2a_handler_error_propagated",
            task_id=envelope.task_id,
            tenant=envelope.tenant,
            origin=envelope.origin,
            target=envelope.target,
            task_type=envelope.task_type,
            error_class=type(exc).__name__,
            error_type=error_type,
        )

    def _validate(self, envelope: DelegationEnvelope) -> DelegationResult | None:
        """Contract checks. Returns a rejection `DelegationResult`, or None (ok)."""
        if envelope.expired():
            return DelegationResult.rejected(
                envelope.task_id, RejectionReason.EXPIRED, detail="deadline has passed"
            )
        try:
            card = self._registry.lookup(envelope.target, tenant=envelope.tenant)
        except RegistryError as exc:
            return DelegationResult.rejected(
                envelope.task_id, RejectionReason.UNKNOWN_TARGET, detail=str(exc)
            )
        if not card.accepts(envelope.task_type):
            return DelegationResult.rejected(
                envelope.task_id,
                RejectionReason.TASK_TYPE_NOT_ACCEPTED,
                detail=f"{envelope.target} does not accept task_type={envelope.task_type!r}",
            )
        if envelope.target not in self._handlers:
            return DelegationResult.rejected(
                envelope.task_id,
                RejectionReason.NO_HANDLER,
                detail=f"no handler registered for {envelope.target}",
            )
        return None

    async def _audit_delegation(self, envelope: DelegationEnvelope, *, decision: str, basis: str) -> None:
        """Record the delegation in the audit hash chain (v2 `AuditRecord`) via `emit_once`.

        **AUDIT-SEAM REWRITE** (design §2 friction rows 1-3, §3.2 "site 5") — the donor audits via
        `self._audit.record(record)` against a donor-only `AuditLog` sink and a donor-only
        `AuditRecord(agent_id, agent_version, tenant, tool, input_hash, decision_basis,
        autonomy_level)`. v2 has NO `AuditLog` (only `AuditSink`/`PostgresAuditSink`, both exposing
        `emit_once`) and v2's `AuditRecord` has a DIFFERENT shape (`agent_id, tenant_id,
        agent_version, action, decision, details: dict, dmn_versions, model_id, prompt_version,
        prev_hash, record_hash`). This is a REWRITE against v2's shape, not a copy. Field mapping:

          donor `tool=f"a2a.delegate:{target}"`         -> v2 `action=f"a2a.delegate:{target}"`
          donor `agent_id=envelope.origin`               -> v2 `agent_id=envelope.origin`
                                                            (UNCHANGED: the chain originator is the
                                                            audited identity — design §3.3, ahead
                                                            of T-G's signed-Card identity binding)
          donor `decision_basis=basis` (free-form str)    -> v2 `details["decision_basis"]`; the
                                                            top-level `decision` field instead
                                                            takes v2's own ALLOW/DENY convention
                                                            (`gateway.audit.AuditRecord`'s own
                                                            docstring), computed by the caller from
                                                            the SAME allow/reject branch that used
                                                            to produce the donor's `basis` string
          donor `input_hash=hash_input({task_id, ...})`   -> v2 derives `input_hash` AUTOMATICALLY
                                                            from `details`
                                                            (`AuditRecord.compute_input_hash`) —
                                                            `details` carries the SAME PHI-safe
                                                            fields the donor hashed (task_id/
                                                            task_type/chain/payload_ref), plus
                                                            `target`/`decision_basis`
          donor `autonomy_level="a2a"`                    -> DROPPED (no v2 field); the `a2a.`
                                                            action prefix already marks the effect
                                                            surface
          donor `agent_version="a2a"` (a fixed marker,     -> PRESERVED verbatim: neither v2's
            never derived from anything real)                `AgentDefinition` nor the envelope
                                                              carries a per-hop "delegation runtime
                                                              version"; a signed Card's `version`
                                                              IS available via a registry lookup,
                                                              but binding it here would add a
                                                              second registry read to the audit hot
                                                              path for a field with no consumer yet
                                                              (deferred, not a T-F blocker)
          (no donor equivalent)                           -> v2 `dmn_versions={}` / `model_id=None`
                                                            / `prompt_version=None` (honest: no
                                                            DMN/LLM provenance at the dispatch
                                                            decision itself, mirrors
                                                            `AgentDecisionProvenance`'s
                                                            "else None — honest" convention)

        PHI safety: `details` deliberately EXCLUDES `envelope.payload_meta` — the one envelope
        field NOT covered by the `_looks_like_phi` guard on `payload_ref` — mirroring `facts.
        build_fact`'s own exclusion of `meta`. Only bounded, structural fields are persisted
        (task_id/task_type/chain/target/payload_ref/decision_basis); `payload_ref` itself is
        already a FHIR/pseudonymized reference, never raw PHI (ADR-0006, enforced at envelope
        construction, `delegation.py`'s `_looks_like_phi`).

        `dedup_key` (design §3.2/§7.2 decision #4): `a2a_audit_dedup_key(tenant, task_id)` ==
        `f"{tenant}:a2a:delegate:{task_id}"`, mirroring `tools.mcp_cibseven.transport.
        start_dedup_key`. This is belt-and-suspenders alongside Guard 4 (`delegate`'s own task_id
        idempotency): the in-memory `_inflight` path does NOT survive a replica restart (class
        docstring), so `emit_once`'s OWN durable dedup is what prevents a forked audit chain link
        if `_execute` is re-entered for the same `task_id` from a fresh process.
        """
        details: dict[str, Any] = {
            "task_id": envelope.task_id,
            "task_type": envelope.task_type,
            "chain": list(envelope.delegation_chain),
            "payload_ref": envelope.payload_ref,
            "target": envelope.target,
            "decision_basis": basis,
        }
        record = AuditRecord(
            agent_id=envelope.origin,
            tenant_id=envelope.tenant,
            agent_version="a2a",
            action=f"a2a.delegate:{envelope.target}",
            decision=decision,
            details=details,
        )
        await self._audit.emit_once(record, dedup_key=a2a_audit_dedup_key(envelope.tenant, envelope.task_id))

    async def _audit_delegation_outcome(
        self,
        envelope: DelegationEnvelope,
        *,
        decision: str,
        basis: str,
        detail: str | None = None,
        dedup_key: str | None = None,
    ) -> None:
        """TERMINAL delegation-outcome audit (T-F follow-up — closes the W2 completeness gap).

        The pre-execution audit (`_audit_delegation`) fires an ALLOW row BEFORE the handler runs —
        but that row is NOT terminal: it records only that the delegation was admitted, not what
        became of it. This method emits the SECOND row recording what the handler actually did,
        called from EVERY branch of `_execute` that ends a delivery:

          - handler raised `DelegationError` (e.g. an internal cyclic sub-delegation) ->
            `decision=_DECISION_FAILED` + the exception message as `detail`.
          - handler raised uma excecao TERMINAL nao-`DelegationError` (NEW-B1, as classes de
            `_TERMINAL_HANDLER_ERROR_CLASSES`) -> `decision=_DECISION_FAILED` + o TOKEN DE CLASSE
            da excecao como `detail`, nunca a mensagem (`_handler_error_detail`).
          - handler raised qualquer OUTRA excecao, que o dispatcher RELEVANTA ->
            `decision=_DECISION_PROPAGATED`, NAO-terminal, na chave
            `a2a_audit_propagated_dedup_key` (`_trace_propagated_handler_error`). Esta entrega nao
            selou nada e vai ser reexecutada numa reentrega; a linha existe para que a propagacao
            deixe de ser invisivel, nunca para afirmar um desfecho.
          - handler returned successfully (LOW-1 completeness fix) ->
            `decision=_DECISION_COMPLETED`, no `detail`.

        Why the success path ALSO needs this row: the durable audit chain otherwise recorded only
        the pre-exec ALLOW for a completed delegation, leaving it INDISTINGUISHABLE from one that
        was allowed then crashed / is still in flight — the `completed` Kafka FACT is ephemeral
        observability, never the durable T-F audit. Auditing BOTH terminal outcomes restores the
        symmetry the failure branch already had (a validation rejection never reaches here — it is
        terminal at the pre-exec DENY row, before the handler is ever routed to).

        Distinct from `_audit_delegation` in three ways, all deliberate:
          - `action` gets a `:outcome` suffix (`f"a2a.delegate:{target}:outcome"`) so a chain
            reader can tell the pre-exec admission row apart from the terminal-outcome row at a
            glance, without inspecting `decision`.
          - `decision` is an OUTCOME value (`_DECISION_COMPLETED`/`_DECISION_FAILED`, or the
            NON-terminal `_DECISION_PROPAGATED`) — NEVER the admission `_DECISION_ALLOW`/
            `_DECISION_DENY` — so this is never mistaken for a second ADMISSION decision (the
            delegation WAS allowed; this records what happened to it).
          - `dedup_key` defaults to `a2a_audit_outcome_dedup_key(tenant, task_id)` (the
            `:outcome`-suffixed key), never the same key as the pre-exec row's
            `a2a_audit_dedup_key` — so `emit_once` treats them as two independent,
            individually-deduplicated chain links, and a re-entry of `_execute` for the same
            `task_id` (from a fresh process, mirroring the pre-exec row's own re-entry-safety
            rationale) can never fork either one. Exactly ONE TERMINAL outcome fires per `task_id`
            (success XOR failure), so the single `:outcome` key never collides between the two
            decisions. O `dedup_key` EXPLICITO existe para o unico chamador NAO-terminal
            (`_trace_propagated_handler_error`): ele passa a chave `:propagated` justamente para
            NAO consumir a chave `:outcome`, que a entrega seguinte — a que talvez conclua — ainda
            vai precisar.

        PHI safety: identical discipline to `_audit_delegation` — `details` excludes
        `envelope.payload_meta` entirely; `detail`, when present, is the exception message, itself
        PHI-free by construction (`CyclicDelegationError`/`MaxHopsExceededError`/
        `BudgetExhaustedError` only ever name agent_ids, chain tuples, and counts — never envelope
        payload data). O ramo NEW-B1 e mais estrito ainda, porque a excecao dele NAO e da familia
        estrutural e a sua mensagem pode ter sido formatada com dados do caso: la o `detail` e
        construido a partir do token de classe e redigido (`_handler_error_detail`).
        """
        details: dict[str, Any] = {
            "task_id": envelope.task_id,
            "task_type": envelope.task_type,
            "chain": list(envelope.delegation_chain),
            "payload_ref": envelope.payload_ref,
            "target": envelope.target,
            "decision_basis": basis,
        }
        if detail is not None:
            details["detail"] = detail
        record = AuditRecord(
            agent_id=envelope.origin,
            tenant_id=envelope.tenant,
            agent_version="a2a",
            action=f"a2a.delegate:{envelope.target}:outcome",
            decision=decision,
            details=details,
        )
        await self._audit.emit_once(
            record,
            dedup_key=dedup_key or a2a_audit_outcome_dedup_key(envelope.tenant, envelope.task_id),
        )

    async def _emit(
        self,
        envelope: DelegationEnvelope,
        kind: DelegationFactKind,
        *,
        reason: str | None = None,
        output_ref: str | None = None,
    ) -> None:
        fact = build_fact(
            kind,
            task_id=envelope.task_id,
            task_type=envelope.task_type,
            tenant=envelope.tenant,
            origin=envelope.origin,
            target=envelope.target,
            delegation_chain=envelope.delegation_chain,
            reason=reason,
            output_ref=output_ref,
        )
        await self._facts.emit(fact)


def origin_signer_of(dispatcher: object) -> EnvelopeSigner | None:
    """The per-tenant ORIGIN envelope signer carried by `dispatcher`'s edge, or `None` (ADR-0039).

    The per-request originators (`agents/*/delegation.py::delegate_*`) receive the dispatcher as
    their handle to the edge; this returns the edge's signer so they can sign at construction. It
    UNWRAPS the gated seam: `GatedDelegationDispatcher` (`gateway/seams/a2a.py`) does not call the
    base `__init__`, so the real dispatcher — and thus `_origin_envelope_signer` — lives on its
    `_inner`. A dispatcher composed with no signer (dev / opt-out) yields `None`, and the originators
    then build UNSIGNED envelopes (which a verifier-less dev dispatcher accepts). Reading a `_`
    attribute keeps the dispatcher's public method surface exactly `{delegate}` (the seam-proof pin).
    """
    inner = getattr(dispatcher, "_inner", dispatcher)
    signer: EnvelopeSigner | None = getattr(inner, "_origin_envelope_signer", None)
    return signer
