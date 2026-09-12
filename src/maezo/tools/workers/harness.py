"""External-task worker harness (CIB Seven fetch-and-lock REST loop).

T1.1 runtime spine (design: ``docs/design/T1.1-runtime-spine.md``). ADR-0001: BPMN processes
call workers via External Task (the residual direction — engine -> worker, not agent -> tool).

Interface::

    harness = WorkerHarness(transport, worker_id="maezo-worker-01", lock_duration_ms=30_000)
    harness.register("operadora.escalation.notify_team", handler)   # raw async handler
    harness.register_worker(NotifyTeamWorker())                     # WorkerBase adapter
    await harness.run()          # long-poll loop, runs until cancelled
    await harness.stop()         # stop fetching (loop exits on next check)
    await harness.drain(20.0)    # wait for in-flight handlers, then unlock stragglers

Each raw handler::

    async def handler(task: ExternalTask) -> Mapping[str, Any] | None:
        ...  # idempotent business logic
        # raise WorkerBpmnError(code) to signal a modeled BPMN error
        # raise WorkerFailureError(msg, retries_left=n) to override the computed retry count
        # RETURN a dict of output variables (loaded on complete), or None for no variables.

The harness completes/fails/reports EXACTLY ONCE per task — handlers never call the transport
themselves (that would risk a double-complete: the second call fails because the task already
left its lock).

Retry ownership (design §9 — the crux this module exists to fix). The **engine** is the single
system of record for durable retry/incident state:

- The harness performs **no** in-process retry of a handler (that is `WorkerBase.run()`'s
  business, scoped to "transient, idempotent, within-lock", and defaults OFF for the runtime
  path via ``max_retries=1``. See ``FunctionWorker`` — T1.2/ADR-0026, not this module).
- On failure the harness computes ``retries = task.retries - 1`` (first delivery ``retries is
  None`` seeds from ``max_retry_attempts``) and reports it to the engine via
  ``transport.handle_failure``. The engine — never the client — decides whether to re-deliver
  (``retries > 0``) or open an incident (``retries == 0``).
  ``WorkerFailureError.retries_left`` overrides the computed value when a handler raises it
  explicitly.
- Guard errors (``PermissionError`` family — e.g. ``*NotHumanError``) and validation errors
  (``ValueError`` family) ALWAYS report ``retries=0`` — an immediate, engine-guaranteed incident.
  Retrying an L0 guard could drive an adverse action (ADR-0008); retrying bad/immutable input
  wastes the re-delivery — both must reach a human, never be retried.
- ``WorkerBpmnError`` is reported as a BPMN error **only** when its ``error_code`` is in the
  harness's ``bpmn_error_allowlist`` — a set of codes proven (by a boundary-proof gate, design
  §9) to have a matching ``bpmn:error@errorCode`` boundary event in every consuming process. An
  unmodeled ``bpmnError`` does not open an incident on CIB Seven 2.1.0 — it silently **ends the
  process scope** (live-verified hazard). A code not in the allowlist is therefore demoted to
  ``failure(retries=0)`` with a loud log line, never silently dropped. The CI-side static gate
  that computes/verifies this allowlist against ``spec/processes/bpmn/**`` is a follow-up (T1.1
  design §9 "Design requirement (fail-closed gate)"); this module implements the runtime-side
  refusal only.
- A ``WorkerBpmnError`` may ALSO carry ``variables`` — an allowlisted, bounded output channel for a
  worker that raises (and therefore never ``complete``s, so its normal output channel is not taken).
  The payload is screened at the harness call site (``screen_bpmn_error_variables``): explicit key
  allowlist + value boundedness, refusal ALL-OR-NOTHING, and dropped with a loud log on any
  demotion (a ``failure`` report has no variables channel). See ``WorkerBpmnError``.

Idempotency: handlers MUST be idempotent (same task_id -> same result) — the explicit-unlock
drain (design §8) can cause the engine to re-deliver a task whose handler already ran.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import random
import re
import time
from collections.abc import Callable, Coroutine, Mapping
from dataclasses import dataclass, field
from typing import Any, NamedTuple, Protocol, runtime_checkable

import httpx
import structlog

from maezo.gateway.action_execution import Decision as ActionDecision
from maezo.gateway.action_execution import evaluate_worker_task
from maezo.gateway.audit import AuditRecord, EmitOnceOutcome, hash_input
from maezo.tools.workers._audit_ctx import collect_dmn_versions
from maezo.tools.workers.base import WorkerBase, WorkerRegistry
from maezo.tools.workers.engine_var_types import (
    camunda_int_type,
    declared_long_variable,
    validate_centavos_engine_var,
)
from maezo.tools.workers.phi_vars import redact_error_message

logger = structlog.get_logger(__name__)

# Also expose a stdlib logger for callers/tests that patch `logging.getLogger` — structlog's
# stdlib bridge is not configured in every test context (mirrors the dual-logging seam used by
# `maezo.runtime.log_phi`), so warnings/errors are never silently lost outside the structlog chain.
_stdlib_logger = logging.getLogger(__name__)

# Integer typing on the engine wire is decided by `engine_var_types.camunda_int_type`: by NAME for
# the variables declared `Long` unconditionally (owner decision R-173), by magnitude otherwise
# (Java `int32` bounds, ADR-0018 part 2 — high-value BRL cents overflow int32). The bounds
# constants live in that leaf module, the single copy in `src/`; see `_to_camunda_var`. (The
# test-infra mapper `tests/integration/processes/engine_rest.py` keeps its own copy on purpose —
# it imports nothing from `maezo` by design; it is outside this declaration, not covered by it.)

#: Terminal dispatch outcomes emitted on the `maezo_worker_task_total` / `_duration_seconds`
#: metrics (design §13). `incident` is additive over v1's {completed, bpmn_error, failed} — it
#: marks the subset of `failed` reports where `retries == 0` (an engine incident was opened).
WORKER_TASK_OUTCOMES: frozenset[str] = frozenset({"completed", "bpmn_error", "failed", "incident"})


# --------------------------------------------------------------------------------------------
# Audit emit-before-complete seam (T-C, T1.10; ADR-0007 non-repudiation, L0)
# --------------------------------------------------------------------------------------------
#
# ADR-0007 invariant: every effect on the world is recorded. The worker harness's `complete` call
# is the single conduit for engine->worker effects (ADR-0001), so it is the one chokepoint where
# a PHI-safe `AuditRecord` is durably emitted BEFORE the effect is committed (design §4.2). See
# `WorkerHarness._handle` for the fail-closed ordering.

#: Stable SERVICE identity for the ADR-0007 tuple (design §2.2 "Agent identity note"). Workers are
#: deterministic BPMN handlers, not LLM agents, so `model_id`/`prompt_version` are `None`, and
#: `agent_id` must be a stable service identity — NOT the ephemeral per-replica `worker_id` (pod
#: name). The signed service-account/cert identity ADR-0007 also asks for is orthogonal hardening,
#: deferred to T-G.
AUDIT_AGENT_ID: str = "operadora-worker"

#: `AuditRecord.decision` for a worker completion. PEP (ALLOW/DENY/REQUIRE_HUMAN) has ZERO runtime
#: callers today (design §2.1 site 3), so no policy verdict is available to record — the audited
#: decision is the worker's committed COMPLETE effect, honestly labeled as such rather than
#: fabricating a PEP ALLOW that never happened.
AUDIT_DECISION_COMPLETE: str = "COMPLETE"

#: `AuditRecord.decision` for a GUARD REFUSAL (T-E, ADR-0030 finding F4). When a worker refuses to
#: perform an adverse L0 action automatically — every `*_NOT_HUMAN` guard plus the denial-block
#: `ERR_AUTH_DENIAL_INCOMPLETE` — the harness records the *refusal decision* so it is non-repudiable
#: (ADR-0007), even though the refusal itself is a control-flow signal that BLOCKS the effect (never
#: an "efeito no mundo" — ADR-0030 §4). Honestly labeled as a refusal, not a fabricated PEP DENY.
AUDIT_DECISION_REFUSED: str = "REFUSED"

#: ADR-0030 §4 denial-block codes hard-gated on T-E that do NOT carry the `_NOT_HUMAN` suffix. The
#: `*_NOT_HUMAN` guard family is matched by suffix (see `is_guard_refusal_code`), so a NEW guard code
#: is recognized automatically; this set carries only the non-suffixed denial-block(s). Mirrors
#: `scripts/ci/check_bpmn_error_allowlist.py::_DENIAL_BLOCK_CODES` (the two are pinned equal by
#: `tests/unit/tools/workers/test_harness_audited_refusal.py`, so they cannot drift).
_DENIAL_BLOCK_CODES: frozenset[str] = frozenset({"ERR_AUTH_DENIAL_INCOMPLETE"})

#: `decision_basis["guard_code"]` fallback when a `PermissionError` guard's message carries no
#: parseable `ERR_*` prefix. A `PermissionError` is ALWAYS the guard family in this harness (module
#: docstring §"Guard errors"), so it is audited unconditionally — the specific code is best-effort.
_GUARD_REFUSAL_FALLBACK_CODE: str = "ERR_GUARD_NOT_HUMAN"

#: MZO-040 (ADR-0037 XRD-09): the guard code recorded when the `ActionExecutionGateway` blocks a
#: dispatch. It is INERT today — reachable only when a human sets `modo: enforcing` in
#: `spec/policies/autonomy/action-approvals.yaml`, which no code change can do. Deliberately NOT in
#: any `*_BPMN_ERROR_ALLOWLIST`: an enforced denial takes the audited-refusal + fail-closed incident
#: path (retries=0), the always-human-visible outcome ADR-0008/ADR-0030 §4 prescribe for a guard —
#: never a modeled boundary, never a silent clean end, never a retry.
_ACTION_GATE_REFUSAL_CODE: str = "ERR_ACTION_GATEWAY_NOT_HUMAN"

#: Extracts the leading `ERR_*` code token from a refusal exception message. The codebase convention
#: is `"ERR_<DOMAIN>_<CONDITION>: <detail>"` — the `*NotHumanError` classes (`recurso.py:42`,
#: `cancel.py:44,60`, …) and the `FunctionWorker`-reclassified coded exceptions
#: (`base.py:293` → `ValueError(f"{code}: {message}")`, e.g. `CredError(ERR_DECRED_NOT_HUMAN, …)`)
#: both follow it. Only the CODE token (bounded, non-PHI) is ever read; the free-text detail (which
#: may name missing fields) is never parsed into the audit payload.
_REFUSAL_CODE_RE = re.compile(r"^(ERR_[A-Z0-9_]+)")

#: Explicit ALLOWLIST of worker OUTPUT keys that are bounded routing/enum/flag tokens — never PHI,
#: never free-text, never a resolvable business identifier (design §3.3). `build_decision_basis`
#: is an allowlist, NEVER a passthrough of `out_vars`; even an allowlisted key is dropped unless
#: its value also passes `_is_bounded_token`. Curated conservatively from the migrated workers
#: (pagto/contas/cancel/recurso/...): routing verdicts, alcada bands, approver groups, tier
#: numbers, terminal desfechos, and boolean effect flags. Free-text fields (`motivo`,
#: `justificativa*`, `fundamentacao*`) and minted identifiers (`auth_number`, `dossier_ref`,
#: `protocolo*`, `*_ref`) are deliberately EXCLUDED — they are hashed into `input_sha256`, never
#: stored in the clear.
_SAFE_DECISION_BASIS_KEYS: frozenset[str] = frozenset(
    {
        "roteamento",
        "faixa_valor",
        "grupo_aprovador",
        "tier_minimo",
        "desfecho",
        "decisao",
        "decisao_pagamento",
        "tipo_liberacao",
        "pagamento_executado",
        "pagamento_liberado",
        # FAB-PUBLISH-CONTACT: "evento_publicado" REMOVIDA, pelo motivo EXATO que a nota de
        # `notice_sent` abaixo registra. Os dois unicos escritores da chave eram
        # `fraude.publish_completed` e `pagto.publish_completed`, workers ORFAOS (topico que nenhum
        # `serviceTask` declara) que a fabricavam como `True` constante de um corpo cujo unico
        # comando era `logger.info`; ambos foram aposentados, e `grep -rnw evento_publicado
        # src/maezo/` nao acha mais nenhum emissor. Manter a chave viva aqui nao custaria nada
        # operacionalmente, mas convidaria um worker futuro a ressuscitar a afirmacao de publish
        # so reemitindo o nome. Quem publica de verdade e `events.py`, e ele usa `event_published`
        # (abaixo), preenchida com o bool de entrega REAL do produtor.
        "event_published",
        # t2-notify-integrity: bounded bool flag marking a SWALLOWED best-effort publish failure
        # (`events.py` sets it alongside `event_published=False` so the audit row records the
        # honest outcome, never a fabricated success).
        "event_publish_best_effort_failure",
        # AUTH-CONTRACT-TRANSMIT-PROSE: "notice_sent" removed. AUTH-SEND-DENIAL-NOTICE-STATUS-LITERAL
        # already stopped `SendDenialNoticeWorker` (the only worker that ever wrote this literal)
        # from emitting `status="notice_sent"` -- grep '"notice_sent"' src/maezo/ finds it nowhere
        # in a production worker any more, only as history in auth.py's own docstring and in a
        # test fixture (test_action_execution_gateway.py). Keeping a dead key in this allowlist
        # costs nothing operationally but invites a future worker to resurrect the fabricated
        # "transmitted" claim by simply emitting the key again.
        # GK-ceiling finding 3: without these, a ceiling-refused issuance writes an audit row
        # reading `decision=COMPLETE` with NO guard evidence — the refusal was visible only in
        # engine history, not in the non-repudiable chain. Both are bounded non-PHI tokens.
        "dentro_teto_l2",
        "motivo_bloqueio_teto",
        # GK-cred finding 5: o fato que ESCOLHE entre o terminal adverso e o de substituicao
        # so vivia no historico do engine — a linha nao-repudiavel de um descredenciamento nao
        # registrava qual rotulo foi aplicado. Booleano limitado, nao-PHI.
        "tem_plano_substituicao",
        "admissivel",
        "elegivel",
        # GAP-AUTH-4 criteria gate (`auth.ValidateAutoCriteriaWorker`), for exactly the reason
        # GK-ceiling finding 3 added the two keys above: without these, a request refused by the
        # auto-approval criteria writes an audit row reading `decision=COMPLETE` with NO evidence
        # of WHY — the four per-criterion verdicts would live only in engine history, never in
        # the non-repudiable ADR-0007 chain. All five are booleans; `motivo_bloqueio_criterios`
        # is a single bounded token from a closed enum (the `auto_criteria_falhas` LIST cannot
        # travel here — `_is_bounded_token` admits scalars only, by design).
        "criterio_tecnico_ok",
        "criterio_financeiro_ok",
        "criterio_regulatorio_ok",
        "criterio_contratual_ok",
        "auto_criteria_verificado",
        "motivo_bloqueio_criterios",
    }
)

#: A bounded enum/routing TOKEN in the clear: alphanumerics + underscore only (no spaces, no
#: hyphens, no punctuation), starting with a letter, length-capped. Matches enum outputs in BOTH
#: casings seen in the workers — UPPERCASE (`DENTRO_TETO_L2`, `APROVAR`, `PENDENTE_DADOS`) and
#: lowercase_snake (`liberado_automatico`). Rejects free text (has spaces), minted identifiers
#: (`AUTH-amh-guia-abc`, `ANSPROTO-...` — hyphens), and anything over the length cap. This is the
#: value-side fail-closed guard behind the key allowlist (defense in depth).
_ENUM_TOKEN_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,39}$")

#: Bound on integer tokens admitted in the clear (tier numbers, small counts). A bound keeps a
#: large numeric that happened to land under an allowlisted key (should not occur) out of the
#: chain; money amounts are not allowlisted keys anyway.
_MAX_BASIS_INT: int = 1_000_000

# --------------------------------------------------------------------------------------------
# `WorkerBpmnError` VARIABLES channel (t9-nack-vars) — allowlisted, bounded, fail-closed
# --------------------------------------------------------------------------------------------
#
# Why this channel exists. A worker that raises `WorkerBpmnError` NEVER completes, so its normal
# output-variable channel (`complete(variables=...)`) is not taken — nothing it computed reaches
# process scope. When the MODELED boundary that catches the error routes into a branch whose next
# worker has a fail-closed guard on one of those variables, the modeled route degrades into an
# incident. That is not hypothetical: SP-OP-ANS-SUBMIT-001's `ST_SubmeterEnvio` raises
# `ERR_ANS_PROTOCOLO_NACK` on an ANS refusal, `BE_SubmitNack` routes to `SUB_RetryEnvio`, and
# `ST_RetransmitirEnvio`'s worker refuses fail-closed on a blank `protocolo_ans` — a variable the
# raising worker HAD in hand and could not hand over. `WorkerTransport.handle_bpmn_error` has
# always ACCEPTED `variables`; only the harness call site never passed any.
#
# Why it is not a passthrough. `complete()` is a worker's declared, per-topic output surface,
# already scoped by `TopicSubscription.variables` on the read side and reviewed per worker. An
# ERROR payload is different in kind: it is built on an exceptional path, from exception-adjacent
# state, and error paths are exactly where raw source values leak (the same reason
# `redact_error_message` exists for `error_message`). Opening a second, unreviewed write path into
# process scope for EVERY worker in the fleet — which is what threading `variables` through
# `_handle` does — is only safe if the payload is constrained. So this channel follows the SAME
# two-part discipline `build_decision_basis` uses: an explicit KEY allowlist plus a value-side
# boundedness guard. A worker cannot smuggle arbitrary (or PHI-bearing) content through a BPMN
# error, no matter what it puts in the dict.
#
# Refusal semantics: ALL-OR-NOTHING (see `screen_bpmn_error_variables`).

#: Explicit ALLOWLIST of variable names a `WorkerBpmnError` may write into process scope. Each key
#: MUST be a variable the consuming BPMN/contract already declares — this channel exists to deliver
#: state the model is already modeled around, never to introduce new process state through a side
#: door. Curated minimally (two keys today); every addition is a reviewed act, not a default.
#:
#:   `protocolo_ans`  SP-OP-ANS-SUBMIT-001. Declared process variable (contract
#:                    `docs/processes/contracts/SP-OP-ANS-SUBMIT-001.md:72`; BPMN
#:                    `ST_SubmeterEnvio` documentation "Emite protocolo_ans + status_envio=enviado"
#:                    and the `event_payload_vars` of `ST_PublishSubmitted`/`ST_PublishRetransmitido`).
#:                    LOAD-BEARING on the NACK route: `ST_RetransmitirEnvio`'s worker refuses
#:                    fail-closed on a blank one. NOT a patient identifier — an ANS protocol keys a
#:                    COMPETENCE-level regulatory batch (`ANSSUB-{tenant}-{report_type}-{competencia}`),
#:                    carries no beneficiary, and the model already publishes it to Kafka.
#:   `status_envio`   SP-OP-ANS-SUBMIT-001. Declared process variable with a CLOSED value set
#:                    (`enviado|ack|nack|retransmitido`, contract :73); read as a routing condition
#:                    by `GW_RetransmissaoOk` (`${status_envio == 'retransmitido'}`). A bounded enum.
#:
#: DELIBERATELY ABSENT — `nack_motivo`: the contract scopes it to the retransmission leg
#: ("preenchido apenas em retransmissao", :75), so the submit-side raise has no contract line to
#: cite for writing it, and this channel never invents process state.
#:
#: ADDING A KEY (F5, review rule). Every NEW key needs its OWN provenance argument on its own
#: merits — the contract/BPMN line that already declares the variable, the branch that reads it, and
#: why its CONTENT is safe to write into process scope. The value guard below does NOT supply that
#: argument: `_is_bounded_error_variable` is a SHAPE control (scalar string, bounded charset,
#: bounded length), not a CONTENT control. A CPF, a CNS, a matricula and a prontuario number are all
#: perfectly bounded tokens — they would sail through the shape gate. What keeps them out is that no
#: one wrote a provenance paragraph for them here. "It passes the regex" is never the argument.
#:
#: KNOWN SCOPE LIMIT (F6, recorded follow-up — NOT closed by this channel). The screen is
#: TOPIC-AGNOSTIC: the allowlist is fleet-wide, so ANY worker on ANY topic may write ANY allowlisted
#: key, even one whose own contract never declares it (e.g. a pagamento worker could write
#: `protocolo_ans`). The blast radius is bounded — two contract-declared, non-identifying keys — so
#: this is a precision gap, not a leak. The tightening is a per-(topic, variable-key) gate, and the
#: fleet already has the precedent to copy: `scripts/ci/check_bpmn_error_allowlist.py` gates
#: (topic, errorCode) pairs against the deployed BPMN, and the same static shape would gate
#: (topic, variable-key) against each contract's declared variables. Deferred deliberately: the
#: per-topic table is only worth its maintenance cost once this channel has more than one raiser.
_SAFE_BPMN_ERROR_VARIABLE_KEYS: frozenset[str] = frozenset({"protocolo_ans", "status_envio"})

#: Value-side guard for the bpmn-error variables channel. WIDER than `_ENUM_TOKEN_RE` by exactly
#: one axis — it admits `-` and `.` inside the token — because the allowlisted keys include a
#: minted protocol identifier (`MOCK-ANS-NAO-VINCULATIVO-ANSSUB-amh-RN_124_SIP-2026-01`), which the
#: enum regex rejects on the hyphens. It still refuses everything that makes free text free text:
#: whitespace, `,`/`;`/`:`/`/`/`@`, quotes, newlines, and anything over the length cap. It is NOT a
#: PHI detector (no regex is) — the KEY allowlist above is the primary control; this is the
#: defence-in-depth second gate that keeps an allowlisted key from carrying a paragraph.
_ERROR_VAR_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,127}$")


class BpmnErrorVariables(NamedTuple):
    """Result of screening a `WorkerBpmnError.variables` payload.

    `refused_keys` NON-EMPTY means the whole payload is REFUSED — `accepted` is then meaningless
    and must not be sent. See `screen_bpmn_error_variables` for why refusal is all-or-nothing.
    """

    accepted: dict[str, Any]
    refused_keys: tuple[str, ...]


def screen_bpmn_error_variables(variables: Mapping[str, Any] | None) -> BpmnErrorVariables:
    """Screen a `WorkerBpmnError.variables` payload against the allowlist + value guard.

    Two gates, both fail-closed: the key MUST be in `_SAFE_BPMN_ERROR_VARIABLE_KEYS`, AND the value
    MUST pass `_is_bounded_error_variable`. A key failing EITHER gate is reported in `refused_keys`.

    **Refusal is ALL-OR-NOTHING, by design.** The tempting alternative — drop the offending entries
    and send the rest — is the worse failure mode here: the modeled boundary would still fire, but
    into a scope that is missing exactly the variable the branch needed, producing a subtly-wrong
    route or a downstream fail-closed guard trip whose incident points at the WRONG worker. A
    partial payload is how this channel would silently misbehave, so the harness refuses the
    bpmnError entirely instead (demoting to `failure(retries=0)` — a loud, human-visible incident
    naming the offending KEYS, never their values).

    Only key NAMES are ever reported/logged. A refused VALUE is never echoed anywhere: the reason
    it was refused is precisely that nothing is known about what it contains.

    `None`/empty input screens clean to an empty payload — the backward-compatible case (every
    `WorkerBpmnError` raised before this channel existed, and every one raised without it since).
    """
    if not variables:
        return BpmnErrorVariables(accepted={}, refused_keys=())
    accepted: dict[str, Any] = {}
    refused: list[str] = []
    for key in sorted(variables):
        if key not in _SAFE_BPMN_ERROR_VARIABLE_KEYS or not _is_bounded_error_variable(variables[key]):
            refused.append(key)
        else:
            accepted[key] = variables[key]
    return BpmnErrorVariables(accepted=accepted, refused_keys=tuple(refused))


def _resolve_app_version(override: str | None = None) -> str:
    """Resolve `AuditRecord.agent_version` — the deployed service version (ADR-0007 "sob-qual-versao").

    Precedence: explicit `override` > `MAEZO_APP_VERSION` env > the installed
    `maezo-operadora` distribution version > `"unknown"` (never fabricated; `"unknown"` is an
    honest fallback for a source checkout with no installed metadata).
    """
    if override:
        return override
    env = os.environ.get("MAEZO_APP_VERSION")
    if env:
        return env
    try:
        from importlib.metadata import PackageNotFoundError, version

        try:
            return version("maezo-operadora")
        except PackageNotFoundError:
            return "unknown"
    except Exception:  # metadata lookup must never break dispatch; fall back honestly.
        return "unknown"


def _is_bounded_token(value: Any) -> bool:
    """True iff `value` is safe to store IN THE CLEAR in `decision_basis` (design §3.3)."""
    if isinstance(value, bool):
        return True
    if isinstance(value, int):  # note: bool is handled above (bool is a subclass of int)
        return -_MAX_BASIS_INT <= value <= _MAX_BASIS_INT
    if isinstance(value, str):
        return bool(_ENUM_TOKEN_RE.match(value))
    return False  # dicts/lists/floats/None/identifiers -> never in the clear


def _is_bounded_error_variable(value: Any) -> bool:
    """True iff `value` is safe to write into process scope through the bpmn-error channel.

    STRINGS ONLY, matching `_ERROR_VAR_TOKEN_RE`. Everything else — `bool`, `int`, `float`, `None`,
    dicts, lists — is refused.

    Why string-only rather than "scalars, like `_is_bounded_token`". Both allowlisted keys are
    contract-declared STRING fields (`protocolo_ans`, a minted identifier, :72; `status_envio`, a
    closed enum `enviado|ack|nack|retransmitido`, :73), so a `bool`/`int` under either key is a
    worker DEFECT, not a payload this channel should faithfully deliver. Accepting it would write a
    Boolean/Integer typed variable into process scope where the model reads a String — for
    `status_envio` that silently falsifies `GW_RetransmissaoOk`'s `${status_envio ==
    'retransmitido'}` (never true, but never an incident either), which is precisely the
    subtly-wrong-route failure this screen exists to convert into a loud refusal. Refusing types the
    contract does not declare keeps the guard aligned with the declared shape; when an allowlisted
    key with a numeric/boolean contract type is eventually added, THAT key's provenance argument is
    where the widening gets made and justified.

    Related-but-distinct: `_ERROR_VAR_TOKEN_RE` is one axis wider than `_ENUM_TOKEN_RE` (it admits
    `-`/`.`) so a minted identifier like `MOCK-ANS-NAO-VINCULATIVO-...` fits, which the enum regex
    rejects on the hyphens. Deliberately NOT reusing `_is_bounded_token`: widening THAT predicate
    would also widen `decision_basis`, and the audit chain's rule that minted identifiers are hashed
    rather than stored in the clear must not move because an unrelated channel needed hyphens.
    """
    if isinstance(value, str):
        return bool(_ERROR_VAR_TOKEN_RE.match(value))
    return False  # bools/ints/floats/None/dicts/lists -> never through the error channel


def build_decision_basis(variables: Mapping[str, Any], out_vars: Mapping[str, Any] | None) -> dict[str, Any]:
    """Curate a PHI-safe `decision_basis` (`AuditRecord.details`) — design §3.3.

    Two parts, both fail-closed:
      1. ``input_sha256`` — a ONE-WAY SHA-256 of the raw (possibly PHI-bearing) `variables`. The
         raw inputs are hashed, NEVER stored; this binds the audit record to the exact tool input
         without persisting it (the `input_hash` column is in turn `hash_input(details)`).
      2. A CURATED allowlist of bounded routing/enum/flag tokens from `out_vars` — an explicit
         allowlist (`_SAFE_DECISION_BASIS_KEYS`) gated by a value guard (`_is_bounded_token`),
         NEVER a passthrough. Nothing else from `out_vars` enters the chain.
    """
    details: dict[str, Any] = {"input_sha256": hash_input(dict(variables))}
    if out_vars:
        for key in sorted(_SAFE_DECISION_BASIS_KEYS):
            if key in out_vars and _is_bounded_token(out_vars[key]):
                details[key] = out_vars[key]
    return details


class AuditEmitError(RuntimeError):
    """Raised on the `_handle` success path when a PHI-safe audit record cannot be durably emitted
    BEFORE `complete` — the fail-closed belt-and-suspenders for a missing/None sink.

    A `RuntimeError` subclass deliberately: it lands in `_handle`'s transient branch
    (`_transient_types` includes `RuntimeError`) -> engine-computed retry -> the effect is
    re-attempted by a properly-wired daemon (self-healing), never completed un-audited. Mirrors
    the fail-closed posture of `PostgresAuditSink.emit_once`'s own `AuditPersistenceError`
    (also a `RuntimeError`).
    """


@runtime_checkable
class AuditEmitter(Protocol):
    """The exactly-once durable-audit seam the harness depends on (design §4.3, T-A landed).

    Satisfied by `maezo.gateway.audit_postgres.PostgresAuditSink` (the real, fail-closed sink) and
    by `FakeAuditSink` (tests). Kept a narrow Protocol so the harness carries no hard dependency on
    asyncpg — only `AuditRecord` (a pure dataclass) is imported. `dedup_key` shape is the T-C
    caller contract: ``f"{tenant}:{task_id}"`` for worker completions (the CIB Seven external-task
    id is stable across re-delivery), matching `PostgresAuditSink.emit_once`'s documented contract.
    """

    async def emit_once(self, record: AuditRecord, *, dedup_key: str) -> str: ...


# --------------------------------------------------------------------------------------------
# Data model
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ExternalTask:
    """A task fetched from CIB Seven via `fetchAndLock`.

    `retries` is `None` on first delivery (the engine has not yet recorded a retry count for
    this task) and an `int` on redelivery. Kept for the retry-ownership computation (module
    docstring, design §9) — NOT present in v1's donor dataclass, added additively.
    """

    task_id: str
    topic: str
    process_instance_id: str
    business_key: str
    worker_id: str
    variables: dict[str, Any] = field(default_factory=dict)
    retries: int | None = None
    # CIB Seven LockedExternalTaskDto top-level engine metadata. Never reconstruct
    # from businessKey or variables: publication uses this immutable source identity.
    process_definition_key: str | None = None
    activity_id: str | None = None


class TopicSubscription(NamedTuple):
    """One `fetchAndLock` topic subscription: per-topic lock duration + variable scoping.

    `variables`, when not None, restricts which process variables the engine returns for tasks
    on this topic (least-privilege — PHI stays out of the general zone unless a worker declares
    it, design §5). `None` means "return all variables" (engine default).
    """

    topic_name: str
    lock_duration_ms: int
    variables: list[str] | None = None


class WorkerBpmnError(Exception):
    """Raise to signal a BPMN error to the engine (e.g. `ERR_ESC_NOTIFY_FAILED`).

    Reported as `bpmnError` ONLY when `error_code` is in the harness's `bpmn_error_allowlist`
    (module docstring) — otherwise demoted to `failure(retries=0)`.

    `variables` (OPTIONAL, t9-nack-vars) is the allowlisted output channel for a raising worker: a
    worker that raises never `complete`s, so without it nothing it computed reaches process scope
    and a modeled boundary can route into a branch whose next worker fail-closes on the missing
    variable. The payload is NOT trusted here — it is screened at the harness call site
    (`screen_bpmn_error_variables`: explicit key allowlist + value boundedness, refusal is
    all-or-nothing). Two rules a raising worker must know:

      1. A payload that fails screening REFUSES the whole bpmnError — the harness demotes to
         `failure(retries=0)` (loud incident naming the offending keys), it does NOT send a
         partial payload.
      2. On DEMOTION for any reason (code not in the `bpmn_error_allowlist`, or a screening
         refusal) the variables are DROPPED with a loud log. A `failure` report has no variables
         channel on the External Task REST contract — there is nowhere for them to go — so this is
         a fact about the engine's API, not a policy choice. The demotion itself is already the
         human-visible incident.
    """

    def __init__(
        self,
        error_code: str,
        message: str = "",
        *,
        variables: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message or error_code)
        self.error_code = error_code
        self.variables: dict[str, Any] | None = dict(variables) if variables else None


class WorkerFailureError(Exception):  # noqa: N818 — domain name predates the *Error suffix convention.
    """Raise to report a failure to the engine with an explicit retry budget.

    `retries_left` OVERRIDES the harness's computed `task.retries - 1` (design §9) — this is how
    a handler opts out of the default engine-computed decrement, e.g. to force an immediate
    incident (`retries_left=0`) for a condition it recognizes as non-transient.
    """

    def __init__(self, message: str, *, retries_left: int = 3) -> None:
        super().__init__(message)
        self.retries_left = retries_left


# Alias kept for compatibility with donor-ported call sites and handler readability.
WorkerFailure = WorkerFailureError


def is_guard_refusal_code(code: str | None) -> bool:
    """True iff `code` is a guard/denial refusal the T-E audited-refusal chokepoint must record.

    ADR-0030 finding F4 (pattern, not a hand-list): ALL `*_NOT_HUMAN` guard codes (a worker refusing
    to perform an adverse L0 action automatically — negativa, descredenciamento, suspensão,
    aplicação de glosa, indeferimento de recurso, …) PLUS the denial-block
    `ERR_AUTH_DENIAL_INCOMPLETE`. A NEW guard code
    is recognized automatically by the `_NOT_HUMAN` suffix — that is what makes the chokepoint
    drift-proof (a future guard cannot bypass the audit emit without also breaking this predicate,
    which the arch-test pins against the CI gate's `is_te_gated`).

    Kept semantically identical to `scripts/ci/check_bpmn_error_allowlist.py::is_te_gated` — the
    boundary-proof gate uses it to decide which codes are hard-gated on T-E before they may enter the
    production allowlist; this harness uses it to decide which refusals to audit. The two are pinned
    equal by `tests/unit/tools/workers/test_harness_audited_refusal.py` so they cannot silently diverge.
    """
    return code is not None and (code.endswith("_NOT_HUMAN") or code in _DENIAL_BLOCK_CODES)


def extract_refusal_code(exc: BaseException) -> str | None:
    """Best-effort extraction of the `ERR_*` code from a refusal exception (T-E).

    A `WorkerBpmnError` carries its code as a first-class attribute (`.error_code`). Every other
    refusal shape — the `PermissionError` `*NotHumanError` family and the `FunctionWorker`-reclassified
    coded exceptions (`base.py:293` → `ValueError("ERR_<CODE>: <detail>")`) — carries it as the leading
    `ERR_*` token of the message (codebase convention `"ERR_<DOMAIN>_<CONDITION>: <detail>"`). Only the
    bounded code token is returned; the free-text detail is never parsed. Returns `None` when no
    `ERR_*` prefix is present (e.g. a plain `ValueError("bad input")` — not a guard refusal).
    """
    if isinstance(exc, WorkerBpmnError):
        return exc.error_code
    match = _REFUSAL_CODE_RE.match(str(exc))
    return match.group(1) if match else None


# A handler returns a Mapping of output variables (loaded on the harness's `complete`,
# EXACTLY ONCE) or None to complete with no variables. Handlers NEVER call `transport.complete`
# themselves (that would double-complete).
TaskHandler = Callable[["ExternalTask"], Coroutine[Any, Any, Mapping[str, Any] | None]]


def _to_camunda_var(value: Any, *, name: str | None = None) -> dict[str, Any]:
    """Type an output-variable value into the CIB Seven / Camunda variable wire format.

    Mirrors the canonical serialization used elsewhere in this codebase (`mcp_cibseven`), with
    the extension the workers need: objects/lists (e.g. a dossier) serialize as a `Json`
    variable (JSON string + implied structure), never as Python `repr()` — only then does the
    engine store/deserialize them as a structured process variable. A value already in variable
    format (`{"value": ...}`) passes through unchanged, except declared Long centavos
    are validated and typed Long before that passthrough (R-173) and the five declared
    integer-centavos names are validated for exact wire representability first.

    **Load-bearing**: money (BRL cents) and dossier round-trips depend on the `Long`/`Json`
    typing below — v1 fixtures assert it; preserved verbatim (design §5/§16.1).

    `name` is the variable NAME the value will be written under. It is what lets
    `engine_var_types.camunda_int_type` honour the names declared `Long` UNCONDITIONALLY (owner
    decision R-173) instead of typing them by magnitude; callers that hold no name (a bare value
    conversion) pass none and get the magnitude rule.
    """
    validate_centavos_engine_var(name, value)
    if (declared := declared_long_variable(name, value)) is not None:
        return declared
    if isinstance(value, dict) and "value" in value:
        return value
    if isinstance(value, bool):
        return {"value": value, "type": "Boolean"}
    if isinstance(value, int):
        # `Long` by NAME for the variables declared int64 unconditionally (R-173); otherwise by
        # magnitude — fits Java int32 -> Integer, else Long. High-value BRL cents (e.g. a R$50MM
        # payment = 5,000,000,000 cents) overflow int32 and MUST be Long, or the engine rejects
        # the complete with "Cannot convert value '<n>' of type 'Integer' to java type
        # java.lang.Integer" (ADR-0018 part 2).
        return {"value": value, "type": camunda_int_type(name, value)}
    if isinstance(value, float):
        return {"value": value, "type": "Double"}
    if isinstance(value, dict | list):
        return {
            "value": json.dumps(value, ensure_ascii=False, default=str),
            "type": "Json",
        }
    if value is None:
        return {"value": None, "type": "String"}
    return {"value": str(value), "type": "String"}


def _from_camunda_var(entry: Mapping[str, Any]) -> Any:
    """Decode ONE inbound CIB Seven / Camunda variable entry (`{"value": ..., "type": ...}`)
    into the Python value a handler actually consumes. Symmetric read-side counterpart to
    `_to_camunda_var` above.

    **Load-bearing** (T1.1 defect, live-caught by the marina graph author): every wire type
    except `Json` already arrives value-ready — `Integer`/`Long`/`Double`/`Boolean`/`String`/a
    `null` value deserialize straight off the `fetchAndLock` JSON response body, so `.get
    ("value")` alone was correct for them. `Json` is the ONE type that needs a second decode:
    CIB Seven/Camunda 7's External Task REST contract returns a `Json`-typed variable's
    `value` as a JSON STRING (the structured content re-encoded — the exact mirror of what
    `_to_camunda_var` WRITES on `complete`/`bpmnError`), never as an already-parsed
    object/array. Without this, list/dict process variables (e.g. SP-OP-CONTAS-001's
    `linhas_conta_refs`) arrive at every consuming worker as a raw string instead of a Python
    `list`/`dict` — every worker iterating/indexing it breaks or silently misbehaves.

    Raises `json.JSONDecodeError` (a `ValueError` subclass) on malformed JSON content, and
    `TypeError`/`AttributeError` on a malformed entry shape — both left for the caller
    (`fetch_and_lock`) to fail-closed per-task (module docstring §9 ValueError convention:
    bad/immutable input never retried, never silently passed through). A `Json`-typed variable
    with `value: null` (unset) decodes to `None`, never attempted through `json.loads` (which
    would raise `TypeError` on a non-str/bytes argument).
    """
    if entry.get("type") == "maezo-auth-exact-decimal.v1":
        # This exact type belongs to the qualified native AUTH acquisition adapter.
        # Generic legacy hydration must never erase its type and route it through float.
        raise ValueError("AUTH exact amount requires native profile hydration")
    value = entry.get("value")
    if entry.get("type") == "Json" and isinstance(value, str):
        return json.loads(value)
    return value


# --------------------------------------------------------------------------------------------
# Transport abstraction
# --------------------------------------------------------------------------------------------


@runtime_checkable
class WorkerTransport(Protocol):
    """Transport interface for the fetch-and-lock loop.

    `fetch_and_lock`'s signature is v2-specific (design §16.2, NOT preserved from v1): topics
    carry per-topic lock duration + variable scoping, and `async_response_timeout_ms` drives the
    engine's long-poll (design §6). Every other method preserves v1's name/shape and adds
    `extend_lock`/`unlock` (design §8, additive).
    """

    async def fetch_and_lock(
        self,
        worker_id: str,
        topics: list[TopicSubscription],
        *,
        max_tasks: int,
        async_response_timeout_ms: int,
    ) -> list[ExternalTask]: ...

    async def complete(
        self,
        task_id: str,
        worker_id: str,
        variables: dict[str, Any],
    ) -> None: ...

    async def handle_failure(
        self,
        task_id: str,
        worker_id: str,
        *,
        error_message: str,
        error_details: str = "",
        retries: int,
        retry_timeout_ms: int,
    ) -> None: ...

    async def handle_bpmn_error(
        self,
        task_id: str,
        worker_id: str,
        *,
        error_code: str,
        error_message: str = "",
        variables: dict[str, Any] | None = None,
    ) -> None: ...

    async def extend_lock(
        self,
        task_id: str,
        worker_id: str,
        *,
        new_duration_ms: int,
    ) -> None: ...

    async def unlock(self, task_id: str) -> None: ...

    async def close(self) -> None: ...


class CibSevenWorkerTransport:
    """Real transport: CIB Seven External Task REST API (`/engine-rest/external-task/*`).

    CIB Seven 2.x preserves the Camunda 7 REST contract under `/engine-rest` (design §2). Never
    swallows a transport error into a fake-empty success (design §6/§13 fail-closed rule) — every
    method raises `httpx.HTTPStatusError`/`httpx.RequestError` on failure; the caller (harness
    loop) is responsible for backoff/readiness, never this transport.

    `fetch_and_lock` decodes `Json`-typed variables (`_from_camunda_var`) — CIB Seven returns a
    `Json` variable's `value` as a JSON STRING, the exact wire-symmetric counterpart of what
    `complete`/`handle_bpmn_error` WRITE via `_to_camunda_var`'s `dict|list -> Json` branch (T1.1
    fix, live-caught: list/dict process variables were arriving at workers as raw strings). A
    task whose `Json` variable fails to decode is fail-closed per-task — reported
    `failure(retries=0)` directly and excluded from the returned batch — never handed to a
    worker undecoded, and never allowed to abort the rest of the polled batch.
    """

    def __init__(self, base_url: str, *, auth_token: str | None = None, timeout: float = 20.0) -> None:
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if auth_token:
            headers["Authorization"] = f"Bearer {auth_token}"
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers=headers,
            timeout=timeout,
        )

    async def fetch_and_lock(
        self,
        worker_id: str,
        topics: list[TopicSubscription],
        *,
        max_tasks: int,
        async_response_timeout_ms: int,
    ) -> list[ExternalTask]:
        topic_payload: list[dict[str, Any]] = []
        for sub in topics:
            entry: dict[str, Any] = {"topicName": sub.topic_name, "lockDuration": sub.lock_duration_ms}
            if sub.variables is not None:
                entry["variables"] = sub.variables
            topic_payload.append(entry)
        payload = {
            "workerId": worker_id,
            "maxTasks": max_tasks,
            "usePriority": True,
            "asyncResponseTimeout": async_response_timeout_ms,
            "topics": topic_payload,
        }
        resp = await self._client.post("/external-task/fetchAndLock", json=payload)
        resp.raise_for_status()

        tasks: list[ExternalTask] = []
        for item in resp.json():
            task_id = item.get("id", "")
            topic = item.get("topicName", "")
            try:
                variables: dict[str, Any] = {
                    k: _from_camunda_var(v) for k, v in (item.get("variables") or {}).items()
                }
            except (ValueError, TypeError, AttributeError) as exc:
                # Fail-closed (design §9 ValueError convention, applied at the transport seam):
                # a malformed `Json`-typed variable is bad/immutable input — it will not fix
                # itself on redelivery. NEVER hand the worker the raw undecoded string (silent
                # corruption) and NEVER let this abort the WHOLE batch (a decode defect on one
                # task must not drop every other polled task — that would look like, but is not,
                # a transport error). Report an immediate incident directly for just this task
                # and exclude it from the returned batch; the harness dispatch loop never sees it.
                _stdlib_logger.error(
                    "worker_task_json_variable_malformed_demoted_to_failure task_id=%s topic=%s error=%s",
                    task_id,
                    topic,
                    exc,
                )
                logger.error(
                    "worker_task_json_variable_malformed_demoted_to_failure",
                    task_id=task_id,
                    topic=topic,
                    error=str(exc),
                )
                try:
                    # T3.4 F5: `str(exc)` on a malformed-Json decode failure can legitimately
                    # embed the offending raw value verbatim (e.g. a CPF pasted into a `Json`
                    # variable) — redact before it reaches the engine's Cockpit-visible incident
                    # store, same backstop as `_report_failure` (this call bypasses that harness
                    # method entirely, since it fires from within the transport's own
                    # `fetch_and_lock`, before a task is even handed to the harness dispatch loop).
                    await self.handle_failure(
                        task_id,
                        worker_id,
                        error_message=redact_error_message(f"malformed Json-typed variable: {exc}"),
                        retries=0,
                        retry_timeout_ms=0,
                    )
                except Exception:  # best-effort: reporting the incident must
                    # never itself crash fetch_and_lock; an unreported task simply stays locked
                    # until it expires and is redelivered (safe fallback, never silently dropped
                    # forever).
                    logger.error(
                        "worker_task_json_variable_malformed_failure_report_failed",
                        task_id=task_id,
                        topic=topic,
                        exc_info=True,
                    )
                continue
            tasks.append(
                ExternalTask(
                    task_id=item["id"],
                    topic=item["topicName"],
                    process_instance_id=item.get("processInstanceId", ""),
                    business_key=item.get("businessKey", "") or "",
                    worker_id=item.get("workerId", worker_id),
                    variables=variables,
                    retries=item.get("retries"),
                    process_definition_key=item.get("processDefinitionKey"),
                    activity_id=item.get("activityId"),
                )
            )
        return tasks

    async def complete(self, task_id: str, worker_id: str, variables: dict[str, Any]) -> None:
        camunda_vars = {k: _to_camunda_var(v, name=k) for k, v in variables.items()}
        resp = await self._client.post(
            f"/external-task/{task_id}/complete",
            json={"workerId": worker_id, "variables": camunda_vars},
        )
        resp.raise_for_status()

    async def handle_failure(
        self,
        task_id: str,
        worker_id: str,
        *,
        error_message: str,
        error_details: str = "",
        retries: int,
        retry_timeout_ms: int,
    ) -> None:
        payload: dict[str, Any] = {
            "workerId": worker_id,
            "errorMessage": error_message,
            "retries": retries,
            "retryTimeout": retry_timeout_ms,
        }
        if error_details:
            payload["errorDetails"] = error_details
        resp = await self._client.post(f"/external-task/{task_id}/failure", json=payload)
        resp.raise_for_status()

    async def handle_bpmn_error(
        self,
        task_id: str,
        worker_id: str,
        *,
        error_code: str,
        error_message: str = "",
        variables: dict[str, Any] | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "workerId": worker_id,
            "errorCode": error_code,
            "errorMessage": error_message,
        }
        if variables:
            payload["variables"] = {k: _to_camunda_var(v, name=k) for k, v in variables.items()}
        resp = await self._client.post(f"/external-task/{task_id}/bpmnError", json=payload)
        resp.raise_for_status()

    async def extend_lock(self, task_id: str, worker_id: str, *, new_duration_ms: int) -> None:
        resp = await self._client.post(
            f"/external-task/{task_id}/extendLock",
            json={"workerId": worker_id, "newDuration": new_duration_ms},
        )
        resp.raise_for_status()

    async def unlock(self, task_id: str) -> None:
        resp = await self._client.post(f"/external-task/{task_id}/unlock")
        resp.raise_for_status()

    async def close(self) -> None:
        await self._client.aclose()


class FakeWorkerTransport:
    """In-memory transport double for unit tests. NEVER imported by production code.

    `.completed` / `.failures` / `.bpmn_errors` / `.unlocked` / `.extended` record every call for
    assertions. `.failures` is a 4-tuple `(task_id, error_message, retries, retry_timeout_ms)` —
    widened from v1's 2-tuple so retry-ownership tests can assert the computed/overridden retry
    count (design §16.2: fixtures adapt to the new retry semantics). `.bpmn_errors` is a 3-tuple
    `(task_id, error_code, error_message)` — widened (T3.4 F5) so tests can assert the harness
    redacts `error_message` before it reaches this chokepoint too (the allowlisted-bpmnError path
    bypasses `_report_failure` entirely, so it needs its own coverage).

    `.bpmn_error_variables` is the PARALLEL list of the `variables` payload each `bpmn_errors`
    entry carried (t9-nack-vars) — a separate list rather than a 5th tuple element on purpose:
    widening the tuple would break every existing 3-tuple assertion in the suite for a channel most
    of them do not exercise (same shape as `FakeKafkaPublisher.best_effort_calls`).
    """

    def __init__(self, tasks: list[ExternalTask] | None = None) -> None:
        self._tasks: list[ExternalTask] = list(tasks or [])
        self.completed: list[tuple[str, dict[str, Any]]] = []
        self.failures: list[tuple[str, str, int, int]] = []
        self.bpmn_errors: list[tuple[str, str, str]] = []
        self.bpmn_error_variables: list[dict[str, Any] | None] = []
        self.unlocked: list[str] = []
        self.extended: list[tuple[str, int]] = []
        self.closed: bool = False

    def enqueue(self, task: ExternalTask) -> None:
        self._tasks.append(task)

    async def fetch_and_lock(
        self,
        worker_id: str,
        topics: list[TopicSubscription],
        *,
        max_tasks: int,
        async_response_timeout_ms: int,
    ) -> list[ExternalTask]:
        topic_names = {sub.topic_name for sub in topics}
        matched: list[ExternalTask] = []
        remaining: list[ExternalTask] = []
        for task in self._tasks:
            if len(matched) < max_tasks and task.topic in topic_names:
                matched.append(task)
            else:
                remaining.append(task)
        self._tasks = remaining
        return matched

    async def complete(self, task_id: str, worker_id: str, variables: dict[str, Any]) -> None:
        self.completed.append((task_id, variables))

    async def handle_failure(
        self,
        task_id: str,
        worker_id: str,
        *,
        error_message: str,
        error_details: str = "",
        retries: int,
        retry_timeout_ms: int,
    ) -> None:
        self.failures.append((task_id, error_message, retries, retry_timeout_ms))

    async def handle_bpmn_error(
        self,
        task_id: str,
        worker_id: str,
        *,
        error_code: str,
        error_message: str = "",
        variables: dict[str, Any] | None = None,
    ) -> None:
        self.bpmn_errors.append((task_id, error_code, error_message))
        self.bpmn_error_variables.append(dict(variables) if variables is not None else None)

    async def extend_lock(self, task_id: str, worker_id: str, *, new_duration_ms: int) -> None:
        self.extended.append((task_id, new_duration_ms))

    async def unlock(self, task_id: str) -> None:
        self.unlocked.append(task_id)

    async def close(self) -> None:
        self.closed = True


# --------------------------------------------------------------------------------------------
# Kafka publishing seam (T1.1 boundary — real producer wiring is a later task)
# --------------------------------------------------------------------------------------------


@runtime_checkable
class KafkaPublisher(Protocol):
    """Domain-event publishing seam injected into worker bootstraps (T1.2/ADR-0026).

    Not exercised by the 3 WorkerBase modules registered today (none declare a Kafka
    dependency) — present so the harness/bootstrap surface matches the donor contract v1's
    process-integration fixtures expect to port against (design §16.1).

    `best_effort` is the OPTIONAL per-call failure posture (t8-escalation-boundary — the
    criticality of a publish is a property of the CALL, not the topic the producer routes on):
      - `None` (default): the producer keeps its own topic-based default
        (`topic in BEST_EFFORT_TOPICS`), so no existing caller changes behavior.
      - `False`: FORCE propagate-on-failure regardless of topic — the caller has a modeled BPMN
        boundary (escalation notify_team/notify_supervisor -> ERR_ESC_NOTIFY_FAILED) or needs the
        harness retry/incident (lgpd notify_sla_risk) on a lost notification, so a broker-down
        failure must REACH it instead of being silently swallowed one layer below.
      - `True`: FORCE best-effort (swallow) regardless of topic.

    Return contract (t2-notify-integrity — the false-success fix): `True` = the PRIMARY publish
    was actually delivered; `False` = the primary send failed but the failure was SWALLOWED
    (best-effort posture) — the caller's ONLY in-band signal of the swallow, so an output
    variable like `events.py`'s `event_published` can stop lying about a swallowed failure. A
    non-best-effort failure raises instead of returning. Mirror-leg outcomes never affect the
    return value.

    `unordered` is the GAP-SC-04-a fail-closed partition-key escape hatch. The real producer
    REFUSES a publish for which no partition key can be resolved (`MissingPartitionKeyError`,
    `platform/integrations/partition_key.py`) — a keyless record is assigned round-robin across the
    topic's partitions, which loses per-entity ordering the moment a consumer is scaled past one
    replica. `unordered=True` is the caller DECLARING that ordering is meaningless for this
    publish; no call site in this build passes it (see
    `AioKafkaEventsProducer.resolve_partition_key`'s enumeration).
    """

    async def publish(
        self,
        topic: str,
        value: dict[str, Any],
        *,
        key: str | None = None,
        best_effort: bool | None = None,
        unordered: bool = False,
    ) -> bool: ...


class FakeKafkaPublisher:
    """In-memory `KafkaPublisher` double for unit tests. NEVER imported by production code.

    Records each call's `best_effort` posture in `best_effort_calls` (parallel to `published`) so
    callers can assert they opted into propagate-on-failure where a lost publish is unacceptable,
    and each call's `unordered` declaration in `unordered_calls` (GAP-SC-04-a). Always reports
    delivery (`True`) — a recorded publish IS a delivered publish here; failure modes are
    exercised by dedicated failing doubles in the individual test modules.

    DELIBERATELY NOT a partition-key gate: this double records what the caller asked for; the
    fail-closed refusal lives in exactly ONE place (`AioKafkaEventsProducer.resolve_partition_key`)
    and is proven there, never re-implemented in a test double that could drift from it.
    """

    def __init__(self) -> None:
        self.published: list[tuple[str, dict[str, Any], str | None]] = []
        self.best_effort_calls: list[bool | None] = []
        self.unordered_calls: list[bool] = []

    async def publish(
        self,
        topic: str,
        value: dict[str, Any],
        *,
        key: str | None = None,
        best_effort: bool | None = None,
        unordered: bool = False,
    ) -> bool:
        self.published.append((topic, value, key))
        self.best_effort_calls.append(best_effort)
        self.unordered_calls.append(unordered)
        return True


class FakeAuditSink:
    """In-memory `AuditEmitter` double for unit tests. NEVER imported by production code.

    Records every `emit_once` call (`.emitted` — the `(record, dedup_key)` pairs, in call order)
    and honours the exactly-once contract: a second `emit_once` for an already-seen `dedup_key` is
    a no-op that returns the PRIOR record's hash (mirrors `PostgresAuditSink.emit_once`'s dedup
    semantics) — so idempotency/re-delivery tests can drive it without a real Postgres. Set
    `.fail_next = <exc>` (or `.always_fail = <exc>`) to make the next (or every) emit raise, for
    the fail-closed ordering tests.
    """

    def __init__(self) -> None:
        self.emitted: list[tuple[AuditRecord, str]] = []
        self._by_key: dict[str, str] = {}
        self.fail_next: BaseException | None = None
        self.always_fail: BaseException | None = None

    async def emit_once(self, record: AuditRecord, *, dedup_key: str) -> str:
        return (await self.emit_once_status(record, dedup_key=dedup_key)).record_hash

    async def emit_once_status(self, record: AuditRecord, *, dedup_key: str) -> EmitOnceOutcome:
        """Mirrors `PostgresAuditSink.emit_once_status`, INCLUDING the dedup FLAG.

        The flag is what makes the claim an effect gate rather than only a double-audit guard
        (`mcp_cibseven.transport.start_process_idempotent`, B-3) — a fake that reported only the
        hash would let a strict-family test pass while the real gate was dead, so this double
        carries the same bit the durable sink does. Single-threaded by construction (no lock
        needed): `asyncio` gives this coroutine exclusive execution between awaits, which is the
        same mutual exclusion the real sink gets from `pg_advisory_xact_lock`.
        """
        if self.always_fail is not None:
            raise self.always_fail
        if self.fail_next is not None:
            exc, self.fail_next = self.fail_next, None
            raise exc
        prior = self._by_key.get(dedup_key)
        if prior is not None:
            # dedup no-op: no second chain link, prior identity returned
            return EmitOnceOutcome(record_hash=prior, deduped=True)
        self.emitted.append((record, dedup_key))
        record_hash = record.record_hash or record._compute_hash()
        self._by_key[dedup_key] = record_hash
        return EmitOnceOutcome(record_hash=record_hash, deduped=False)


# --------------------------------------------------------------------------------------------
# Observability (best-effort, never breaks dispatch — design §13)
# --------------------------------------------------------------------------------------------


def _emit_worker_task_outcome(
    *,
    tenant: str,
    topic: str,
    outcome: str,
    duration_seconds: float | None = None,
) -> None:
    """Record a dispatch outcome DEFENSIVELY — never raises into dispatch.

    Delegates to `maezo.platform.observability.record_worker_task_outcome`. Import is local to
    avoid a hard dependency of this module on the observability stack at import time (mirrors
    `WorkerBase.run()`'s own defensive metrics import, `base.py:148-152`).
    """
    try:
        if outcome not in WORKER_TASK_OUTCOMES:
            logger.debug("worker_task_metric_skipped", outcome=outcome, topic=topic)
            return
        from maezo.platform.observability import record_worker_task_outcome

        record_worker_task_outcome(
            tenant=tenant or "unknown",
            topic=topic,
            outcome=outcome,
            duration_seconds=duration_seconds,
        )
    except Exception:  # defensive: a metric error must never break dispatch.
        logger.debug("worker_task_metric_emit_failed", topic=topic, outcome=outcome, exc_info=True)


def derive_handler_name(handler: TaskHandler) -> str:
    """A STABLE, bounded, non-PHI `worker` label for a raw `register()`ed handler.

    WORKER-METRICS-COVERAGE. `maezo_worker_execution_time_seconds` / `maezo_worker_error_count_total`
    — the two metrics `MaezoSLAWorkerLatencyHigh`, `MaezoSLAWorkerErrorRateHigh` and
    `MaezoWorkerCrashLoop` are built on (`deploy/observability/alert-rules.yml:18-33,:56-73,:81-95`)
    — are emitted by `WorkerBase.run()` and by nothing else, so every topic served by a raw
    `harness.register()` handler was invisible to all three alerts. Those alerts carry a
    `{{ $labels.worker }}` in their descriptions, so the raw leg needs a `worker` value that is
    stable across restarts and bounded in cardinality.

    Derived from the handler's own identity rather than invented: `<module leaf>.<factory>`, e.g.
    `programa.make_stratify_risk_handler` for the closure `make_stratify_risk_handler` returns.
    One value per registered topic, so the label set is bounded by the worker registry exactly as
    `WorkerBase`'s class-name label is. Never anything from a task: no business key, no task id,
    no process instance — the ADR-0010 rule `record_worker_task_outcome` states for its own labels.
    """
    module = str(getattr(handler, "__module__", "") or "")
    qualname = str(
        getattr(handler, "__qualname__", "") or getattr(handler, "__name__", "") or type(handler).__name__
    )
    # `make_x.<locals>._handler` -> `make_x`: the INNERMOST ENCLOSING factory is the meaningful
    # identity; the inner `_handler` name is shared by a dozen unrelated closures and would
    # collapse every raw topic onto one series. Split on the `<locals>` marker rather than taking
    # the first dotted segment — the first segment is the OUTERMOST scope, which for any closure
    # nested more than one level deep (a factory defined inside another function) names the wrong
    # thing entirely.
    parts = qualname.split(".<locals>.")
    factory = (parts[-2] if len(parts) > 1 else parts[-1]).rsplit(".", 1)[-1] or "handler"
    leaf = module.rsplit(".", 1)[-1]
    return f"{leaf}.{factory}" if leaf else factory


def _emit_raw_handler_worker_metrics(
    *,
    worker_name: str,
    topic: str,
    outcome: str,
    error_type: str | None,
    duration_seconds: float,
) -> None:
    """Emit the M11 per-worker metrics for a RAW-handler topic. Never raises into dispatch.

    WHY THIS IS NOT A SECOND EMITTER FOR EVERY TOPIC. A `WorkerBase`-wrapped topic already emits
    both metrics from inside `WorkerBase.run()`; emitting again here would double every observation
    — `MaezoSLAWorkerLatencyHigh` reads a p95 out of that histogram, and two observations per task
    (the handler body AND the whole dispatch) would corrupt it silently. So the harness emits for,
    and only for, the topics `register_worker` did NOT claim. Coverage is then complete BY
    CONSTRUCTION and by exactly one emitter per topic:
    `tests/unit/tools/workers/test_harness_worker_metrics.py` pins both halves side by side.

    DISCLOSED SEMANTIC DIFFERENCE, not smoothed over: `WorkerBase.run()` times its own `execute()`
    body and counts one error PER RETRY ATTEMPT; this measures the whole dispatch (handler +
    audit emit + the engine report) and counts one error per TASK. Both are honest measures of
    "worker work"; they are not the identical quantity, and an operator comparing a raw topic's
    p95 against a `WorkerBase` topic's should know that. The alternative — moving `WorkerBase`'s
    emission here to unify the semantics — would silently change every existing series' meaning,
    which is a bigger change than the gap it closes.
    """
    try:
        from maezo.platform.observability import (  # lazy, mirrors `_emit_worker_task_outcome`
            record_worker_error,
            record_worker_execution,
        )

        if outcome == "completed":
            record_worker_execution(worker_name=worker_name, topic=topic, duration_seconds=duration_seconds)
        else:
            record_worker_error(worker_name=worker_name, topic=topic, error_type=error_type or "UnknownError")
    except Exception:  # defensive: a metric error must never break dispatch.
        logger.debug("worker_raw_handler_metric_emit_failed", topic=topic, outcome=outcome, exc_info=True)


# --------------------------------------------------------------------------------------------
# Harness
# --------------------------------------------------------------------------------------------


class WorkerHarness:
    """Fetch-and-lock loop for CIB Seven external tasks.

    Each iteration:
      1. `fetchAndLock` (long-poll) across every registered topic.
      2. Every returned task is dispatched to its handler as a tracked background task (so the
         loop can immediately re-poll — design §6 point 3 — while `drain()` can still observe
         and bound in-flight work at shutdown).
      3. The handler's outcome is reported to the engine EXACTLY ONCE: `complete` | `bpmnError`
         (gate-proven codes only) | `failure` (retry ownership — module docstring, design §9).

    Idempotency is the handler's responsibility (the harness never deduplicates by task_id).
    """

    def __init__(
        self,
        transport: WorkerTransport,
        *,
        worker_id: str,
        tenant: str = "unknown",
        lock_duration_ms: int = 30_000,
        poll_interval_ms: int = 5_000,
        max_tasks_per_poll: int = 10,
        max_retry_attempts: int = 3,
        async_response_timeout_ms: int = 25_000,
        bpmn_error_allowlist: frozenset[str] | None = None,
        engine_unreachable_after: int = 3,
        audit_sink: AuditEmitter | None = None,
        app_version: str | None = None,
    ) -> None:
        self._transport = transport
        self._worker_id = worker_id
        # `tenant` is BOTH an observability dimension (bounded, non-PHI metric label) AND the
        # audit tuple's `tenant_id` / dedup-key prefix (design §4.3). Defaults to "unknown" so
        # every existing construction call site (tests, readiness probes) keeps working unchanged;
        # the service passes `settings.tenant_id`.
        self._tenant = tenant

        # T-C (ADR-0007, L0): the durable exactly-once audit sink. The emit-before-complete gate
        # (`_handle`) FAILS CLOSED on a missing sink — a task is never completed without a
        # preceding durable audit row (design §4.2 + MUST-FIX 2 belt-and-suspenders). `None` is
        # accepted at CONSTRUCTION only (the topic-probe / unit fixtures that never complete a
        # task, and the daemon before its T-D go-live co-requisite wires a real
        # `PostgresAuditSink`); any harness that actually dispatches a task to a successful
        # completion without a sink raises `AuditEmitError` -> incident, never a silent
        # un-audited complete. See the T-D seam-contract note in the PR body.
        self._audit_sink = audit_sink
        self._audit_app_version = _resolve_app_version(app_version)
        self._lock_duration_ms = lock_duration_ms
        self._poll_interval_ms = poll_interval_ms
        self._max_tasks = max_tasks_per_poll
        self._max_retries = max_retry_attempts
        self._async_response_timeout_ms = async_response_timeout_ms
        self._bpmn_error_allowlist = bpmn_error_allowlist or frozenset()
        self._engine_unreachable_after = max(engine_unreachable_after, 1)

        self._handlers: dict[str, TaskHandler] = {}
        self._topic_variables: dict[str, list[str] | None] = {}
        # WORKER-METRICS-COVERAGE: which topics emit the M11 per-worker metrics THEMSELVES (every
        # `register_worker` topic, via `WorkerBase.run()`) and what `worker` label the rest get.
        # `_handle` reads both to emit exactly once per topic — see
        # `_emit_raw_handler_worker_metrics`.
        self._worker_base_topics: set[str] = set()
        self._handler_names: dict[str, str] = {}
        self._registry = WorkerRegistry()
        self._running = False

        # In-flight tracking for drain()/unlock (design §8): task_id -> (asyncio.Task, ExternalTask).
        self._inflight: dict[str, tuple[asyncio.Task[None], ExternalTask]] = {}

        # Fetch-error / idle backoff + readiness state (design §6).
        self._consecutive_fetch_errors = 0
        self._idle_streak = 0
        self._engine_reachable = True
        self.fetch_errors_total = 0

    # -- registration -------------------------------------------------------------------------

    def register(self, topic: str, handler: TaskHandler, *, variables: list[str] | None = None) -> None:
        """Register a raw async handler for a topic. Idempotent (last registration wins).

        WORKER-METRICS-COVERAGE: also records the `worker` metric label this topic will be
        reported under and clears any prior `register_worker` claim on it — "last registration
        wins" has to hold for the metric identity too, or a topic re-registered raw would keep
        being treated as `WorkerBase`-emitted and would emit nothing at all.
        """
        self._handlers[topic] = handler
        self._topic_variables[topic] = variables
        self._handler_names[topic] = derive_handler_name(handler)
        self._worker_base_topics.discard(topic)

    def register_worker(self, worker: WorkerBase) -> None:
        """Register a `WorkerBase` instance (today's 3 modules: auth/escalation/lgpd).

        Adds `worker` to the harness's own `WorkerRegistry` (ADR-0026 §Decisao 2 contract —
        `register_worker` delegates to it) AND wraps it into a raw async handler stored in the
        same `_handlers` table `.register()` uses, so `_handle`'s dispatch stays uniform.

        Sync/async bridge (design §7): `WorkerBase.run()` is synchronous by design and may
        internally `time.sleep` between in-process retries — running it on the event loop would
        stall every other in-flight task. The dispatcher therefore runs it via
        `asyncio.to_thread`, UNLESS the worker overrides `run_async` (detected by identity
        against the base-class default, which just re-enters `run()` synchronously and would
        defeat the bridge if awaited directly).
        """
        self._registry.register(worker.topic, worker)
        run_async_overridden = type(worker).run_async is not WorkerBase.run_async

        async def _adapter(task: ExternalTask, *, _worker: WorkerBase = worker) -> Mapping[str, Any] | None:
            if run_async_overridden:
                return await _worker.run_async(task.variables)
            return await asyncio.to_thread(_worker.run, task.variables)

        self.register(worker.topic, _adapter)
        # AFTER `register` (which clears the flag): this topic's M11 metrics come from
        # `WorkerBase.run()` itself, so `_handle` must NOT emit a second, doubling observation.
        # The label matches what `WorkerBase.run()` uses (`type(self).__name__`) so the two legs
        # of `registered_topics` share one vocabulary.
        self._worker_base_topics.add(worker.topic)
        self._handler_names[worker.topic] = type(worker).__name__

    @property
    def registered_topics(self) -> list[str]:
        return sorted(self._handlers)

    @property
    def registry(self) -> WorkerRegistry:
        """The `WorkerBase` sub-registry (only entries added via `register_worker`)."""
        return self._registry

    @property
    def engine_reachable(self) -> bool:
        """False after `engine_unreachable_after` consecutive `fetch_and_lock` failures."""
        return self._engine_reachable

    @property
    def inflight_count(self) -> int:
        return len(self._inflight)

    def _topic_subscriptions(self) -> list[TopicSubscription]:
        return [
            TopicSubscription(topic, self._lock_duration_ms, self._topic_variables.get(topic))
            for topic in self.registered_topics
        ]

    # -- run loop -------------------------------------------------------------------------------

    async def run(self) -> None:
        """Long-poll loop. Exits cleanly on `asyncio.CancelledError` (design §6/§8)."""
        self._running = True
        logger.info("worker_harness_started", worker_id=self._worker_id, topics=self.registered_topics)
        try:
            while self._running:
                try:
                    tasks = await self._transport.fetch_and_lock(
                        self._worker_id,
                        self._topic_subscriptions(),
                        max_tasks=self._max_tasks,
                        async_response_timeout_ms=self._async_response_timeout_ms,
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # fail-closed backoff, never a silent []`.
                    self._consecutive_fetch_errors += 1
                    self.fetch_errors_total += 1
                    if self._consecutive_fetch_errors >= self._engine_unreachable_after:
                        self._engine_reachable = False
                    logger.warning(
                        "fetch_and_lock_failed",
                        error=str(exc),
                        consecutive_failures=self._consecutive_fetch_errors,
                    )
                    await self._error_backoff()
                    continue

                self._consecutive_fetch_errors = 0
                self._engine_reachable = True

                if tasks:
                    self._idle_streak = 0
                    for task in tasks:
                        self._spawn(task)
                else:
                    await self._idle_backoff()
        except asyncio.CancelledError:
            logger.info("worker_harness_cancelled", worker_id=self._worker_id)
        finally:
            self._running = False

    async def stop(self) -> None:
        """Stop the loop (v1-compatible signature). Does not itself drain in-flight work —
        callers that need bounded drain + explicit unlock should also call `drain()`."""
        self._running = False

    async def drain(self, deadline_s: float) -> None:
        """Wait up to `deadline_s` for in-flight handlers, then cancel + unlock stragglers.

        Design §8 step 3-4: completed handlers `complete` normally during the wait; anything
        still locked-but-not-completed at the deadline is explicitly unlocked so the engine can
        re-deliver it to a surviving replica immediately (rather than waiting out
        `lock_duration_ms`, v1's only mechanism).
        """
        pending = [fut for fut, _task in self._inflight.values()]
        if not pending:
            return
        _done, still_pending = await asyncio.wait(pending, timeout=max(deadline_s, 0.0))
        if not still_pending:
            return
        stragglers = [(fut, task) for fut, task in self._inflight.values() if fut in still_pending]
        for fut, _task in stragglers:
            fut.cancel()
        for fut, ext_task in stragglers:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await fut
            with contextlib.suppress(Exception):
                await self._transport.unlock(ext_task.task_id)
                logger.warning(
                    "worker_task_unlocked_on_drain",
                    task_id=ext_task.task_id,
                    topic=ext_task.topic,
                )

    async def _idle_backoff(self) -> None:
        """Full-jitter backoff after an EMPTY fetch (design §6 point 4).

        With engine long-polling, the common idle wait is already the engine's
        `asyncResponseTimeout` — this jitter only spaces immediate reconnects (e.g. when the
        engine returns early with no tasks) so N replicas don't hammer the engine in lockstep.
        """
        self._idle_streak += 1
        base_s = max(self._poll_interval_ms / 1000.0, 0.01)
        cap_s = max(base_s, 30.0)
        ceiling = min(base_s * (2 ** min(self._idle_streak, 5)), cap_s)
        await asyncio.sleep(random.uniform(0, ceiling))

    async def _error_backoff(self) -> None:
        """Full-jitter exponential backoff after a `fetch_and_lock` TRANSPORT error (design §6
        point 5) — base 1s, cap 30s. Distinct from `_idle_backoff` (empty-but-successful fetch)."""
        ceiling = min(1.0 * (2 ** min(self._consecutive_fetch_errors, 5)), 30.0)
        await asyncio.sleep(random.uniform(0, ceiling))

    def _spawn(self, task: ExternalTask) -> None:
        fut = asyncio.create_task(self._run_handle(task), name=f"worker-task-{task.task_id}")
        self._inflight[task.task_id] = (fut, task)

        def _cleanup(_f: asyncio.Task[None], task_id: str = task.task_id) -> None:
            self._inflight.pop(task_id, None)

        fut.add_done_callback(_cleanup)

    async def _run_handle(self, task: ExternalTask) -> None:
        """Wrapper around `_handle` run as a tracked background task.

        `_handle` already catches every business/transport exception it can meaningfully
        classify (module docstring); this is a last-resort guard so a truly unexpected bug never
        leaves an "exception was never retrieved" warning from a fire-and-forget task.
        """
        try:
            await self._handle(task)
        except asyncio.CancelledError:
            raise
        except Exception:  # last-resort: dispatch must never crash the loop.
            logger.error("worker_handle_task_crashed", task_id=task.task_id, topic=task.topic, exc_info=True)

    # -- dispatch -------------------------------------------------------------------------------

    def _compute_engine_retries(self, task: ExternalTask) -> int:
        """`task.retries - 1`, seeding from `max_retry_attempts` on first delivery (retries=None).

        The engine does NOT auto-decrement retries (design §2) — the client (this harness) must.
        """
        current = task.retries if task.retries is not None else self._max_retries
        return max(current - 1, 0)

    def _jittered_retry_timeout_ms(self, task: ExternalTask) -> int:
        """Full-jitter exponential backoff for the engine `retryTimeout`, based on how many
        attempts this task has already had (`max_retry_attempts - task.retries`, bounded)."""
        current = task.retries if task.retries is not None else self._max_retries
        attempt = max(self._max_retries - current, 0)
        cap_ms = 60_000
        base_ms = 2_000
        ceiling = min(cap_ms, base_ms * (2 ** min(attempt, 5)))
        return int(random.uniform(base_ms, max(base_ms, ceiling)))

    async def _report_failure(
        self,
        task: ExternalTask,
        error: BaseException | str,
        *,
        retries_override: int | None,
    ) -> str:
        """Report `failure` to the engine with the given (or computed) retry count.

        `error` is redacted via `redact_error_message` (T3.4 F5) BEFORE it reaches the transport
        — this is the single chokepoint for every `_handle` failure branch, so callers pass the
        raw exception (or a static string) and never need to redact it themselves. The engine's
        incident store (Cockpit) is otherwise a direct, unredacted PHI-egress path: `str(exc)` on
        an input-validation failure can legitimately embed the offending value (a CPF, a long
        numeric id) verbatim.

        Returns the emitted outcome label: "incident" when the reported retries is 0 (the engine
        will open an incident), else "failed" (the engine will re-deliver after the timeout).
        """
        retries = retries_override if retries_override is not None else self._compute_engine_retries(task)
        retries = max(retries, 0)
        retry_timeout_ms = 0 if retries == 0 else self._jittered_retry_timeout_ms(task)
        await self._transport.handle_failure(
            task.task_id,
            self._worker_id,
            error_message=redact_error_message(error),
            retries=retries,
            retry_timeout_ms=retry_timeout_ms,
        )
        return "incident" if retries == 0 else "failed"

    # -- audit emit-before-complete (T-C, ADR-0007, L0) -----------------------------------------

    def _build_audit_record(
        self, task: ExternalTask, out_vars: Mapping[str, Any] | None, dmn_versions: dict[str, Any]
    ) -> AuditRecord:
        """Construct the PHI-safe ADR-0007 audit record for a worker completion (design §2.2/§3.3).

        `agent_id` is the stable SERVICE identity (not the per-replica `worker_id`); `action` is
        the task topic; `model_id`/`prompt_version` are `None` (deterministic BPMN worker, no LLM);
        `details` is the curated PHI-safe `decision_basis`; `dmn_versions` is the provenance the
        `_audit_ctx` collector captured during the handler run.
        """
        return AuditRecord(
            agent_id=AUDIT_AGENT_ID,
            tenant_id=self._tenant,
            agent_version=self._audit_app_version,
            action=task.topic,
            decision=AUDIT_DECISION_COMPLETE,
            details=build_decision_basis(task.variables, out_vars),
            dmn_versions=dmn_versions,
            model_id=None,
            prompt_version=None,
        )

    def _audit_dedup_key(self, task: ExternalTask) -> str:
        """Exactly-once key for a worker completion (design §4.3; matches emit_once's contract).

        `f"{tenant}:{task_id}"` — the CIB Seven external-task id is STABLE across lock-expiry /
        failure-with-retries re-delivery (the same task entity is re-locked), so a re-delivered
        effect dedups to the SAME chain row (no double-audit).
        """
        return f"{self._tenant}:{task.task_id}"

    async def _emit_audit(self, record: AuditRecord, task: ExternalTask) -> str:
        """Durably emit `record` exactly-once BEFORE the effect is committed. FAIL-CLOSED.

        Raises `AuditEmitError` when no sink is wired (belt-and-suspenders for a missing/
        misconfigured sink — MUST-FIX 2), or propagates the sink's own `AuditPersistenceError`
        (a `RuntimeError`) on a DB failure. Either raise lands in `_handle`'s transient branch ->
        the task is NOT completed -> engine re-delivery, so no effect is ever committed to the
        engine without a preceding durable audit row (ADR-0007). Returns the persisted (or
        deduped-prior) record hash.
        """
        if self._audit_sink is None:
            _stdlib_logger.error(
                "worker_task_audit_sink_missing_fail_closed task_id=%s topic=%s tenant=%s",
                task.task_id,
                task.topic,
                self._tenant,
            )
            logger.error(
                "worker_task_audit_sink_missing_fail_closed",
                task_id=task.task_id,
                topic=task.topic,
                tenant=self._tenant,
            )
            raise AuditEmitError(
                f"no audit sink configured for tenant={self._tenant!r} — refusing to complete "
                f"task {task.task_id!r} un-audited (ADR-0007 fail-closed); a PostgresAuditSink is "
                "the T-D go-live co-requisite of T-C"
            )
        return await self._audit_sink.emit_once(record, dedup_key=self._audit_dedup_key(task))

    # -- audited refusal (T-E, ADR-0007 / ADR-0030 F4) ------------------------------------------

    def _build_refusal_record(
        self, task: ExternalTask, guard_code: str, dmn_versions: dict[str, Any]
    ) -> AuditRecord:
        """Construct the PHI-safe ADR-0007 record for a GUARD REFUSAL (T-E, ADR-0030 §4).

        Same PHI discipline as the completion record (`_build_audit_record`): raw inputs are hashed
        into `input_sha256`, never stored. Adds `guard_code` (a bounded, non-PHI `ERR_*` token, guarded
        by `_is_bounded_token` as defence-in-depth) so the chain attests WHICH guard blocked the adverse
        action. `decision` is the honest REFUSED label — no `out_vars` exist (the handler raised), so no
        output tokens are curated; `dmn_versions` carries any table the handler consulted before it
        refused (the collector stays bound across the raise — design §3.2).
        """
        details = build_decision_basis(task.variables, out_vars=None)
        details["guard_code"] = guard_code if _is_bounded_token(guard_code) else _GUARD_REFUSAL_FALLBACK_CODE
        return AuditRecord(
            agent_id=AUDIT_AGENT_ID,
            tenant_id=self._tenant,
            agent_version=self._audit_app_version,
            action=task.topic,
            decision=AUDIT_DECISION_REFUSED,
            details=details,
            dmn_versions=dmn_versions,
            model_id=None,
            prompt_version=None,
        )

    def _audit_refusal_dedup_key(self, task: ExternalTask) -> str:
        """Exactly-once key for a REFUSAL audit — a distinct namespace from the completion key.

        `f"{tenant}:refuse:{task_id}"` (mirrors the T-C2 `{tenant}:start:...` namespacing). A task's
        terminal outcome is deterministic (P1) and either a completion OR a refusal, never both, so the
        namespace prevents any cross-collision and dedups a re-fired refusal (same `task_id`) to the
        SAME chain row — exactly-once per refused effect.
        """
        return f"{self._tenant}:refuse:{task.task_id}"

    async def _audit_guard_refusal(
        self, task: ExternalTask, *, guard_code: str, dmn_versions: dict[str, Any]
    ) -> None:
        """Emit the PHI-safe refusal audit BEFORE the refusal is reported (emit-before-refuse).

        BEST-EFFORT, deliberately — NOT the fail-closed GATE the T-C success path uses. A refusal is a
        control-flow signal that BLOCKS the adverse action; it is NOT an ADR-0007 "efeito no mundo"
        (ADR-0030 §4), and the path is ALREADY fail-closed to a human via the incident / neutral
        terminal (audit-emit design §2.1 row 2, §7 "additive"). So a sink failure here must NOT block
        the refusal: blocking it could only mean (a) failing-open the guard — forbidden — or (b)
        converting the refusal into a guard RETRY — forbidden by ADR-0008 ("guards ALWAYS report
        retries=0, never retried"). Instead the failure is logged LOUDLY (stdlib + structlog, so it is
        never *silent*) and the refusal proceeds to its incident. No adverse action is ever performed
        un-audited, because no adverse action is performed at all. In normal operation (a live sink —
        the T-D `audit_sink_ready` readiness gate keeps `/readyz` red otherwise) every refusal is
        durably recorded before it is reported; the log-and-proceed path is the bounded-outage fallback.
        """
        record = self._build_refusal_record(task, guard_code, dmn_versions)
        try:
            if self._audit_sink is None:
                raise AuditEmitError(
                    f"no audit sink configured for tenant={self._tenant!r} — guard refusal "
                    f"{guard_code!r} on task {task.task_id!r} could not be recorded (ADR-0007); the "
                    "refusal still fails closed to an incident (never fail-open)"
                )
            await self._audit_sink.emit_once(record, dedup_key=self._audit_refusal_dedup_key(task))
        except RuntimeError as exc:
            # AuditEmitError (missing sink) or the sink's AuditPersistenceError (DB fault) — both
            # RuntimeError. Loud on BOTH loggers (the refusal is NEVER silent); the guard still refuses.
            _stdlib_logger.error(
                "worker_guard_refusal_audit_unavailable_proceeding_fail_closed "
                "task_id=%s topic=%s tenant=%s guard_code=%s error=%s",
                task.task_id,
                task.topic,
                self._tenant,
                guard_code,
                exc,
            )
            logger.error(
                "worker_guard_refusal_audit_unavailable_proceeding_fail_closed",
                task_id=task.task_id,
                topic=task.topic,
                tenant=self._tenant,
                guard_code=guard_code,
            )

    # -- action-execution gateway (MZO-040, ADR-0037 XRD-09) — SHADOW ---------------------------

    def _evaluate_action_gate(self, task: ExternalTask) -> ActionDecision | None:
        """Evaluate this dispatch against the `ActionExecutionGateway`. NEVER raises.

        SHADOW TODAY. `spec/policies/autonomy/action-approvals.yaml` ships `modo: shadow` and
        `status: DRAFT`, so the returned `Decision.enforced` is False for every call and `_handle`
        ignores the verdict entirely — the only observable effect is one structured, non-PHI
        telemetry line per dispatch (`action_execution_gateway_shadow`; the event is named
        `action_execution_gateway_enforced` once a decision actually blocks). That inertness is
        proven, not asserted: `tests/unit/gateway/test_action_execution_gateway.py` runs the choked
        path with the gateway neutralized and with it live, asserting identical transport outcomes.

        Enforcement is a DATA act: a human sets `status: RATIFICADO` + `modo: enforcing` in the
        CODEOWNERS-LISTED manifest (listed, not gated — `main` carries no server-side protection
        today, so the listing requests a reviewer rather than requiring one; owner finding recorded
        in evidence-ledger row `mzo-000`) after the Médica/ANS/Security blocks are filled. No code
        change, no redeploy — which is exactly why this method must never be the thing that decides.

        Returns None only if the gateway itself is unusable in a way `evaluate_worker_task` could
        not classify; None means "no opinion" and leaves dispatch untouched.
        """
        try:
            return evaluate_worker_task(topic=task.topic, tenant=self._tenant)
        except Exception:  # belt-and-suspenders; the gateway is already total.
            logger.error("action_gateway_evaluate_failed", topic=task.topic, exc_info=True)
            return None

    async def _handle(self, task: ExternalTask) -> None:
        handler = self._handlers.get(task.topic)
        started_at = time.perf_counter()
        outcome: str | None = None
        # WORKER-METRICS-COVERAGE: the `error_type` label `maezo_worker_error_count_total` carries.
        # Tracked as a local rather than read from `sys.exc_info()` in the `finally` (the exception
        # is already handled by then) and rather than a second try/except (which would re-order the
        # engine report). Set on every non-completed branch below.
        error_type: str | None = None
        try:
            if handler is None:
                # Should only happen if fetchAndLock somehow returns a task for a topic we no
                # longer serve (race with a concurrent re-registration) — the readiness check
                # (worker_runtime/service.py) prevents this at boot by requiring full topic
                # coverage before /readyz goes green. Fail-closed: report an incident, never
                # silently drop the task (design §7 — "a task with no handler must not vanish").
                logger.error("worker_unregistered", topic=task.topic, task_id=task.task_id)
                error_type = "UnregisteredTopic"
                outcome = await self._report_failure(
                    task,
                    f"no handler registered for topic {task.topic!r}",
                    retries_override=0,
                )
                return

            try:
                # DMN provenance capture (T-B): a fresh per-task collector is bound for the
                # handler run; `evaluate_sync` populates it in-thread via the ContextVar bridge
                # (design §3.2). Reset-per-task in `collect_dmn_versions`'s `finally`.
                with collect_dmn_versions() as dmn_versions:
                    # MZO-040 (ADR-0037 XRD-09): the per-call chokepoint, evaluated BEFORE the
                    # handler runs — the handler IS where the external effect happens, so a
                    # denial must pre-empt it, not follow it. In SHADOW (today, always)
                    # `enforced` is False and this branch is dead: the gateway only observes.
                    # Under `modo: enforcing` a denial takes the guard path — audited refusal
                    # then a fail-closed incident (retries=0, never retried per ADR-0008) — so
                    # the call lands in front of a human instead of proceeding or vanishing.
                    action_gate = self._evaluate_action_gate(task)
                    if action_gate is not None and action_gate.enforced and not action_gate.allow:
                        await self._audit_guard_refusal(
                            task,
                            guard_code=_ACTION_GATE_REFUSAL_CODE,
                            dmn_versions=dict(dmn_versions),
                        )
                        logger.error(
                            "action_gateway_denied_enforcing",
                            task_id=task.task_id,
                            topic=task.topic,
                            action_class=action_gate.action_class or "NAO_MAPEADA",
                            reason=action_gate.reason,
                        )
                        error_type = _ACTION_GATE_REFUSAL_CODE
                        outcome = await self._report_failure(
                            task,
                            f"{_ACTION_GATE_REFUSAL_CODE}: {action_gate.reason}",
                            retries_override=0,
                        )
                        return
                    out_vars = await handler(task)
                # T-C (ADR-0007, L0): audit-BEFORE-complete. Build the PHI-safe record, then emit
                # it durably + exactly-once. A failure here (missing sink OR DB error) raises and
                # is classified transient below -> `complete` NEVER runs -> engine re-delivery.
                # The forbidden direction (an engine effect with no audit row) is structurally
                # impossible (design §4.2).
                record = self._build_audit_record(task, out_vars, dict(dmn_versions))
                audit_record_hash = await self._emit_audit(record, task)
                await self._transport.complete(
                    task.task_id,
                    self._worker_id,
                    dict(out_vars) if out_vars else {},
                )
                outcome = "completed"
                logger.info(
                    "worker_task_completed",
                    task_id=task.task_id,
                    topic=task.topic,
                    business_key=task.business_key,
                    audit_record_hash=audit_record_hash,
                )
            except WorkerBpmnError as exc:
                error_type = type(exc).__name__
                # T-E (ADR-0030 F4): a guard/denial bpmnError (ERR_CANCEL_MANTER_NOT_HUMAN,
                # ERR_AUTH_DENIAL_INCOMPLETE, …) records the REFUSAL decision BEFORE it is reported —
                # whether it routes to a modeled boundary (allowlisted) or demotes to an incident.
                # Non-guard bpmnErrors (fail-safe ERR_EVENT_PUBLISH_FAILED, origin-validation
                # ERR_NIP_PROTOCOLO_INVALIDO, …) are NOT T-E-audited (ADR-0030 §4: desirable, not a
                # hard blocker for those). emit-before-refuse, best-effort (`_audit_guard_refusal`).
                if is_guard_refusal_code(exc.error_code):
                    await self._audit_guard_refusal(
                        task, guard_code=exc.error_code, dmn_versions=dict(dmn_versions)
                    )
                screened = screen_bpmn_error_variables(exc.variables)
                if exc.error_code not in self._bpmn_error_allowlist:
                    # Live-verified hazard (design §9): an unmodeled bpmnError silently ends the
                    # process with NO incident on CIB Seven 2.1.0. Demote to a fail-closed
                    # incident instead of risking a silent drop, and log loudly so this shows up
                    # in on-call triage even without the CI-side boundary-proof gate.
                    # Checked FIRST so the log always names the PRIMARY cause: an uncatalogued code
                    # is a modelling defect regardless of what its payload looks like.
                    _stdlib_logger.error(
                        "bpmn_error_code_not_gate_proven_demoted_to_failure "
                        "task_id=%s topic=%s error_code=%s",
                        task.task_id,
                        task.topic,
                        exc.error_code,
                    )
                    logger.error(
                        "bpmn_error_code_not_gate_proven_demoted_to_failure",
                        task_id=task.task_id,
                        topic=task.topic,
                        error_code=exc.error_code,
                    )
                    if exc.variables:
                        # t9-nack-vars demotion rule: a `failure` report has NO variables channel
                        # on the External Task REST contract, so the payload cannot follow the
                        # demoted outcome anywhere. It is dropped — LOUDLY, naming only the KEYS,
                        # so a worker author debugging "my variable never reached scope" finds the
                        # reason in the log instead of inferring it. (Only key names: an unscreened
                        # payload's VALUES are exactly what this channel refuses to trust.)
                        _stdlib_logger.error(
                            "bpmn_error_variables_dropped_on_demotion "
                            "task_id=%s topic=%s error_code=%s dropped_keys=%s",
                            task.task_id,
                            task.topic,
                            exc.error_code,
                            ",".join(sorted(exc.variables)),
                        )
                        logger.error(
                            "bpmn_error_variables_dropped_on_demotion",
                            task_id=task.task_id,
                            topic=task.topic,
                            error_code=exc.error_code,
                            dropped_keys=sorted(exc.variables),
                        )
                    outcome = await self._report_failure(task, exc, retries_override=0)
                elif not screened.refused_keys:
                    outcome = "bpmn_error"
                    await self._transport.handle_bpmn_error(
                        task.task_id,
                        self._worker_id,
                        error_code=exc.error_code,
                        # T3.4 F5: this call bypasses `_report_failure` (it is the ALLOWLISTED
                        # bpmn-error path, not a failure report), so it needs its own redaction —
                        # `error_message` is still `str(exc)`-derived and still Cockpit-visible.
                        error_message=redact_error_message(exc),
                        # t9-nack-vars: the SCREENED payload only (allowlisted keys, bounded
                        # values). `None` when the worker passed none — byte-identical to every
                        # pre-channel raise, so no existing worker's wire call changes.
                        variables=screened.accepted or None,
                    )
                else:
                    # The payload failed screening. Refusing is ALL-OR-NOTHING (see
                    # `screen_bpmn_error_variables`): a partial payload would fire the modeled
                    # boundary into a scope missing exactly what the branch needed. Demote to a
                    # fail-closed incident naming the offending KEYS (never their values — the
                    # reason they were refused is that nothing is known about their contents).
                    _stdlib_logger.error(
                        "bpmn_error_variables_refused_demoted_to_failure "
                        "task_id=%s topic=%s error_code=%s refused_keys=%s",
                        task.task_id,
                        task.topic,
                        exc.error_code,
                        ",".join(screened.refused_keys),
                    )
                    logger.error(
                        "bpmn_error_variables_refused_demoted_to_failure",
                        task_id=task.task_id,
                        topic=task.topic,
                        error_code=exc.error_code,
                        refused_keys=list(screened.refused_keys),
                    )
                    outcome = await self._report_failure(task, exc, retries_override=0)
            except WorkerFailureError as exc:
                error_type = type(exc).__name__
                outcome = await self._report_failure(task, exc, retries_override=max(exc.retries_left, 0))
            except PermissionError as exc:
                error_type = type(exc).__name__
                # ERR_*_NOT_HUMAN guard family — NEVER retried (ADR-0008): an incident is the
                # engine-guaranteed, always-human-visible outcome. T-E (ADR-0030 F4): record the
                # refusal decision BEFORE the incident. A `PermissionError` is ALWAYS the guard family
                # in this harness (module docstring §"Guard errors"), so it is audited unconditionally;
                # emit-before-refuse, best-effort (never fail-open, never a guard retry).
                await self._audit_guard_refusal(
                    task,
                    guard_code=extract_refusal_code(exc) or _GUARD_REFUSAL_FALLBACK_CODE,
                    dmn_versions=dict(dmn_versions),
                )
                outcome = await self._report_failure(task, exc, retries_override=0)
            except ValueError as exc:
                error_type = type(exc).__name__
                # Bad/immutable input — won't fix itself on retry; route straight to incident. T-E
                # (ADR-0030 F4): a coded guard exception reclassified by FunctionWorker (base.py:293 ->
                # ValueError("ERR_*_NOT_HUMAN: …"), e.g. CredError(ERR_DECRED_NOT_HUMAN)) IS a guard
                # refusal — record it before the incident. A plain bad-input ValueError carries no
                # ERR_* guard code and is NOT audited (it is not a guard refusal).
                refusal_code = extract_refusal_code(exc)
                if refusal_code is not None and is_guard_refusal_code(refusal_code):
                    await self._audit_guard_refusal(
                        task, guard_code=refusal_code, dmn_versions=dict(dmn_versions)
                    )
                outcome = await self._report_failure(task, exc, retries_override=0)
            except Exception as exc:  # classified below; never escapes dispatch.
                error_type = type(exc).__name__
                _transient_types = (RuntimeError, OSError, TimeoutError, ConnectionError, httpx.HTTPError)
                transient = isinstance(exc, _transient_types)
                if not transient:
                    logger.error(
                        "worker_unclassified_error",
                        task_id=task.task_id,
                        topic=task.topic,
                        error_type=type(exc).__name__,
                    )
                outcome = await self._report_failure(task, exc, retries_override=None)
        finally:
            if outcome is not None:
                elapsed = time.perf_counter() - started_at
                _emit_worker_task_outcome(
                    tenant=self._tenant,
                    topic=task.topic,
                    outcome=outcome,
                    duration_seconds=elapsed,
                )
                # WORKER-METRICS-COVERAGE: the M11 per-worker metrics the three worker alerts
                # actually read, for the topics `WorkerBase.run()` does not cover. `_handle` is
                # the right place because every task — raw handler or `WorkerBase` adapter —
                # terminates here exactly once, which is what makes the coverage structural
                # instead of a rule each new worker module has to remember.
                if task.topic not in self._worker_base_topics:
                    _emit_raw_handler_worker_metrics(
                        worker_name=self._handler_names.get(task.topic, "raw_handler"),
                        topic=task.topic,
                        outcome=outcome,
                        error_type=error_type,
                        duration_seconds=elapsed,
                    )

    # Public alias (design §16.1: "keep `_handle` callable, public `handle_task` alias") — v1
    # fixtures that drive dispatch directly call `harness._handle(task)`; new code should prefer
    # the public name.
    handle_task = _handle
