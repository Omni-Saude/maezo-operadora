"""The effect-chokepoint DECISION CORE — `EffectCall`, `EffectDecision`, `decide` (Onda 1, W1/P0).

WHAT THIS IS. One total function that composes the layers this repo has ALREADY ratified into a
single verdict for one attempted external effect, agent-side or worker-side. It decides; it does
not act. The seam wrappers and the registry that (later) call it hold the real arguments and
perform the effect; nothing in this module touches a transport, a payload, or the network.

WHAT IT IS NOT. It is not a new authority. Every layer below is an existing, human-ratified
artefact — the per-agent capability lists in `spec/agents/*/agent.yaml`, the autonomy matrix in
`spec/policies/autonomy/L0-core.yaml`, the ADR-0016 process-key universe, the tenant ceilings, and
the MZO-040 approval record in `action-approvals.yaml`. This module can only SUBTRACT permission.
The L0-hard guards (ADR-0018 structural no-denial, the `ERR_*_NOT_HUMAN` worker guards, the BPMN
error allowlist, `pep.HARD_ACTIONS`, the process-start fence) keep refusing with this module
removed — that is design invariant I-6, and it is why nothing here is wired INTO those guards.

=================================================================================================
THE ERROR-CONTAINMENT CHAIN (design I-4: "the PEP itself must be total")
=================================================================================================
A gateway bug must never crash a care path, and must never fail OPEN once enforcing. The posture
is `evaluate_worker_task`'s double-guarded fallback (`action_execution.py:1076-1127`), generalised
per layer. Enumerated failure mode -> containment choice -> the proof that holds it:

  L-0 CATALOGUE (pure lookup on a frozen `MappingProxyType`)
      · operation is a non-string / empty / unknown token -> `lookup_operation` returns None ->
        DENY `OPERACAO_DESCONHECIDA`. There is no raising path; the catalogue cannot be mutated.
      · the lookup itself raises (a monkeypatched or corrupted module) -> the per-layer guard
        returns DENY `GATEWAY_ERRO_INTERNO` at `L0_CATALOGO`.
      Proof: `test_unknown_operation_denies_at_the_catalogue`, `test_every_layer_contains_an
      _injected_exception`.

  L-1 CAPABILITY (set membership on data the composition root resolved)
      · NO capability view supplied -> DENY `CAPACIDADE_INDISPONIVEL`. Absence is never "allowed";
        a wrapper built without a principal's declared tools has nothing to authorise against.
      · the operation names an MCP tool the agent did not declare -> DENY `TOOL_NAO_DECLARADA`.
      · the operation is not an MCP tool at all (`tool_id is None`) -> the tool check does not
        apply; the seam still traverses L-2 and L-5 (see `effect_classes.OperationSpec.tool_id`).
      · the operation needs a process key and the call carries none, or one outside the agent's
        declared keys ∩ the ADR-0016 universe -> DENY `PROCESS_KEY_NAO_PERMITIDA` (closes R-1).
      · the capability view raises from any predicate -> DENY `GATEWAY_ERRO_INTERNO` at
        `L1_CAPACIDADE`.
      Proofs: the capability tests + the injected-exception sweep.

  L-2 AUTONOMY (`pep.PEP.evaluate` — INJECTED third-party object)
      · the catalogue carries NO ratified action name (Q-4: LLM / A2A / population) -> DENY
        `VOCABULARIO_PENDENTE` WITHOUT calling the PEP. Inventing a name is forbidden
        (`pep.py:12-18`); an honest would-deny in shadow telemetry is the correct record.
      · no PEP injected (e.g. `build_pep` refused to start) -> DENY `POLITICA_INDISPONIVEL`.
      · `evaluate` raises -> DENY `GATEWAY_ERRO_INTERNO` at `L2_AUTONOMIA`.
      · `evaluate` returns something that is not `Decision.ALLOW` — including junk, None, or a
        different enum — -> DENY. Only the ALLOW identity proceeds; there is no truthiness test.
      · `REQUIRE_HUMAN` -> DENY `HUMANO_REQUERIDO`: an agent may not proceed autonomously, and
        turning a human gate into an ALLOW is exactly the fail-open this layer exists to prevent.
      Proofs: `test_l2_*` family.

  L-3 CEILING (`CeilingResolver.within_l2_ceiling` — INJECTED)
      · the catalogue declares no money ceiling for the operation (every catalogued operation
        today) -> the layer is INERT and is skipped. Zero added I/O on read classes (I-9).
      · a ceiling IS declared and no resolver was injected -> DENY `TETO_EXCEDIDO`. "Cannot check
        the teto" is never "within the teto" — the `ceilings.py` fail-closed posture, restated.
      · a ceiling is declared and the call carries no `value_cents` -> DENY `TETO_EXCEDIDO`.
      · the resolver raises, or returns a non-`True` value -> DENY (`is not True`, never a
        truthiness test — `ceilings._is_valid_value_cents` documents why a bool-ish comparison is
        the money defect class).
      Proofs: `test_l3_*` family (synthetic catalogue entry — no real operation carries a teto).

  L-4 CONSENT (declared, unwired — Q-5 is human)
      · no class is flagged `consentimento_exigido` -> the layer is INERT for every real call, and
        a test asserts that inertness against the shipped catalogue so it cannot drift silently.
      · a flagged class with no consent source -> DENY `CONSENTIMENTO_AUSENTE` (honest and
        fail-closed: the mechanism is present, the adapter is not).
      · the source raises or returns non-`True` -> DENY `CONSENTIMENTO_AUSENTE`.
      Proofs: `test_l4_*` family.

  L-5 RATIFICATION (`ActionExecutionGateway.evaluate` — UNCHANGED, `action_execution.py:1001-1048`)
      · the manifest could not be loaded -> the loader already fails closed into
        `_EMPTY_APPROVALS`; this layer passes its `MANIFESTO_INDISPONIVEL` through verbatim.
      · loading raises anyway -> guarded; the view degrades to empty and the mode to `unresolved`.
      · `evaluate` raises despite its total contract -> DENY `GATEWAY_ERRO_INTERNO` at
        `L5_RATIFICACAO`.
      · nothing is approved (today, always) -> the manifest's own precise reason is passed through
        UNCHANGED, so an approver keeps the `APROVACAO_PENDENTE` / `ACAO_NAO_DECLARADA` /
        `MANIFESTO_NAO_RATIFICADO` distinction the existing telemetry already gives them.

  L-6 SAMPLING (ADR-0034 :78-85 — hook only; the sampler/queue are Q-3)
      · runs ONLY after the verdict exists, only on an L2-level ALLOW, and inside its own guard.
        A raising or slow sampler cannot change, delay past its own call, or fail the decision.
      · the default is a no-op; the `sample_key` is a sha256 over BOUNDED TOKENS ONLY.
      Proof: `test_a_raising_sampler_cannot_change_or_escape_the_decision`.

  OUTERMOST. `decide` is a GUARD around `_decide_ladder`, so an unanticipated failure still
  returns a bounded `L_ENTRADA` DENY rather than an exception. Three such paths are PROVEN, not
  imagined: a raising `logger` inside a per-layer guard (the guard's own `logger.error` re-raises
  past it — which is why the outermost handler's log is itself suppressed), `denial_shape_for`
  raising (it runs inside `_deny`, i.e. AFTER the layer guard returned), and `enforcement_for`
  raising (it runs BEFORE the first guard exists). `decide` has no raising path reachable by a
  caller — asserted directly, against `decide` itself, by
  `test_decide_itself_never_raises_on_the_three_proven_raising_paths`.
    The outermost DENY resolves the GLOBAL mode from the cached manifest and does NOT apply the
  per-class dimension (`_outermost_enforcement`): a gateway failure must still block under a live
  `modo: enforcing`, exactly as `_fail_closed_decision` makes it on the worker leg.

MOST-RESTRICTIVE-WINS is the short-circuit itself: the ladder returns at the FIRST deny and no
later layer can widen it. The layers are ordered structural-before-human so that an approver
reading shadow telemetry can tell "no human signed this class yet" (`APROVACAO_PENDENTE`, L-5)
apart from "this agent was never allowed to do this at all" (`TOOL_NAO_DECLARADA`, L-1).

`enforced` IS COMPUTED SEPARATELY FROM `allow` (design §7.3). The verdict is resolved once,
identically, for every layer, from the manifest: `global modo == enforcing AND the class's
enforcement == enforcing`. That is what lets a class be EVALUATED for months before it can BLOCK.

NO PHI, BY CONSTRUCTION (I-3). `EffectCall` has no field that can hold free text: every one is a
bounded token, a bounded dotted token, an `SP-OP-*` key, a non-negative integer of centavos, or an
enum. There is no payload, no prompt, no recipient, no business key and no patient id — not
"filtered out", but absent from the type. The wrapper keeps the real arguments and hands them to
the inner seam only after an ALLOW.
"""

from __future__ import annotations

import contextlib
import enum
import hashlib
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Protocol, runtime_checkable

import structlog

from maezo.gateway import action_execution, effect_classes, pep
from maezo.gateway.action_execution import (
    ENFORCEMENT_ENFORCING,
    ENFORCEMENT_SHADOW,
    EVENT_ENFORCED,
    EVENT_SHADOW,
    MODE_UNRESOLVED,
    ActionApprovals,
    ActionExecutionGateway,
)
from maezo.gateway.effect_classes import OperationSpec

logger = structlog.get_logger(__name__)

# -- Bounded-token discipline, restated from `action_execution.py:206-215` for the same reason it
# is restated there from `harness._ENUM_TOKEN_RE`: keeping ONE regex per layering boundary is
# cheaper than a cross-package import, and a cross-check test pins the two against each other.
#
# ALL THREE ANCHOR WITH `\Z`, NEVER `$` — and so do the two MIRROR SOURCES (`action_execution.
# _TOKEN_RE`, `process_allowlist._PROCESS_KEY_PATTERN`), tightened in the same commit so the
# pattern-equality cross-checks keep holding. Python's `$` also matches immediately BEFORE a
# trailing newline, so `EffectCall(tenant="acme\n", …)` was accepted as a bounded token and the
# newline travelled into a structured telemetry line — a log-injection primitive on the ONE value
# object whose entire contract (I-3) is "there is no field free text fits in". On the ADR-0016
# side the same `$` cleared a FORMAT guard whose stated job is defending against encoding tricks.
# Strictly narrowing in every case: nothing that matched before stops matching except the
# trailing-newline forms, and no real token, reason, operation or SP-OP-* key carries one.
# THIS COMMENT IS THE CANONICAL RATIONALE for all five patterns: `process_allowlist.py` carries a
# one-line pointer here instead of a copy, because several ADRs and the evidence ledger cite that
# file by `file:line` and a multi-line note there would have silently invalidated all of them.
_TOKEN_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,39}\Z")
_DOTTED_TOKEN_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z][A-Za-z0-9_.]{0,79}\Z")

#: MIRRORED from `tools/process_allowlist.py:63`, NOT imported: `maezo.gateway` must not depend on
#: `maezo.tools` (`action_execution.py:206-208` states the rule and the precedent — the dependency
#: runs the other way). A cross-check test asserts this pattern and the ADR-0016 one accept and
#: reject the same strings, so the duplication cannot drift into a second vocabulary.
_PROCESS_KEY_RE: Final[re.Pattern[str]] = re.compile(r"^SP-OP-[A-Z]+(?:-[A-Z]+)*-[0-9]{3}\Z")

#: MIRRORED from `tools/process_allowlist.py:21-42` (ADR-0016's 15-key universe), same layering
#: reason, same cross-check test. This is the set the per-agent `process_keys` list is intersected
#: with at L-1 — the closure of design finding R-1 (`ProcessAllowlist.ensure_allowed` has ZERO
#: production importers, so the allowlist was data nobody enforced).
KNOWN_PROCESS_KEYS: Final[frozenset[str]] = frozenset(
    {
        "SP-OP-ESCALATION-001",
        "SP-OP-LGPD-DSR-001",
        "SP-OP-AUTH-001",
        "SP-OP-CONTAS-001",
        "SP-OP-RECURSO-001",
        "SP-OP-NIP-001",
        "SP-OP-ANS-SUBMIT-001",
        "SP-OP-CANCEL-001",
        "SP-OP-REEMBOLSO-001",
        "SP-OP-INADIMPLENCIA-001",
        "SP-OP-CRED-001",
        "SP-OP-ADEQUACAO-001",
        "SP-OP-FRAUDE-001",
        "SP-OP-PROGRAMA-001",
        "SP-OP-PAGTO-001",
    }
)

#: ADR-0006 zones. The zone is carried for telemetry and for the C2 operation split; it is NOT a
#: gate here — `runtime/inference/errors.py::PhiZoneRoutingError` stays the independent enforcement.
PHI_ZONE_GENERAL: Final[str] = "general"
PHI_ZONE_PHI: Final[str] = "phi"
PHI_ZONES: Final[frozenset[str]] = frozenset({PHI_ZONE_GENERAL, PHI_ZONE_PHI})


class EffectLayer(enum.StrEnum):
    """WHICH layer produced a decision. Bounded tokens; required for approver legibility (§5.3)."""

    ENTRADA = "L_ENTRADA"
    CATALOGO = "L0_CATALOGO"
    CAPACIDADE = "L1_CAPACIDADE"
    AUTONOMIA = "L2_AUTONOMIA"
    TETO = "L3_TETO"
    CONSENTIMENTO = "L4_CONSENTIMENTO"
    RATIFICACAO = "L5_RATIFICACAO"


# -- Reason vocabulary: extends `action_execution`'s CLOSED enum with the layers it does not have.
# Every token is bounded and safe in the clear (asserted by a test against `_TOKEN_RE`). L-5's
# reasons are passed through verbatim rather than re-encoded, so one telemetry vocabulary covers
# the whole ladder.
REASON_ALLOWED: Final[str] = action_execution.REASON_APPROVED
REASON_OPERATION_UNKNOWN: Final[str] = "OPERACAO_DESCONHECIDA"
REASON_TOOL_UNDECLARED: Final[str] = "TOOL_NAO_DECLARADA"
REASON_PROCESS_KEY_FORBIDDEN: Final[str] = "PROCESS_KEY_NAO_PERMITIDA"
REASON_CAPABILITIES_UNAVAILABLE: Final[str] = "CAPACIDADE_INDISPONIVEL"
REASON_VOCABULARY_PENDING: Final[str] = "VOCABULARIO_PENDENTE"
REASON_AUTONOMY_DENIED: Final[str] = "AUTONOMIA_NEGADA"
REASON_HUMAN_REQUIRED: Final[str] = "HUMANO_REQUERIDO"
REASON_POLICY_UNAVAILABLE: Final[str] = "POLITICA_INDISPONIVEL"
REASON_CEILING_EXCEEDED: Final[str] = "TETO_EXCEDIDO"
REASON_CONSENT_MISSING: Final[str] = "CONSENTIMENTO_AUSENTE"
REASON_INTERNAL_ERROR: Final[str] = action_execution.REASON_INTERNAL_ERROR
REASON_INVALID_CALL: Final[str] = "CHAMADA_INVALIDA"

#: Every reason this module can originate (L-5's pass-throughs live in `action_execution`).
EFFECT_REASONS: Final[frozenset[str]] = frozenset(
    {
        REASON_ALLOWED,
        REASON_OPERATION_UNKNOWN,
        REASON_TOOL_UNDECLARED,
        REASON_PROCESS_KEY_FORBIDDEN,
        REASON_CAPABILITIES_UNAVAILABLE,
        REASON_VOCABULARY_PENDING,
        REASON_AUTONOMY_DENIED,
        REASON_HUMAN_REQUIRED,
        REASON_POLICY_UNAVAILABLE,
        REASON_CEILING_EXCEEDED,
        REASON_CONSENT_MISSING,
        REASON_INTERNAL_ERROR,
        REASON_INVALID_CALL,
    }
)


class EffectCallError(ValueError):
    """An `EffectCall` was built from a value that is unbounded, mistyped, or PHI-shaped.

    Raised at CONSTRUCTION, deliberately loudly: I-3 makes the value object structurally incapable
    of carrying free text, and a caller that tried is a programming defect, not a policy outcome.
    Callers on an effect path must never let it escape — use :func:`decide_effect`, which builds
    the call inside its own guard and turns this into a bounded DENY.
    """


def _require_token(field_name: str, value: Any) -> str:
    if not isinstance(value, str) or not _TOKEN_RE.match(value):
        raise EffectCallError(
            f"{field_name} must be a bounded non-PHI token matching {_TOKEN_RE.pattern!r}; "
            f"got {type(value).__name__} of length {len(value) if isinstance(value, str) else 0}"
        )
    return value


def _require_dotted(field_name: str, value: Any) -> str:
    if not isinstance(value, str) or not _DOTTED_TOKEN_RE.match(value):
        raise EffectCallError(
            f"{field_name} must be a bounded dotted token matching {_DOTTED_TOKEN_RE.pattern!r}; "
            f"got {type(value).__name__} of length {len(value) if isinstance(value, str) else 0}"
        )
    return value


@dataclass(frozen=True, slots=True)
class EffectCall:
    """The non-PHI description of ONE attempted effect (design §5.2).

    Every field is bounded and validated at construction; there is no field a payload, a prompt, a
    recipient, a business key or a patient id could be placed in. The wrapper holds those and
    passes them to the inner seam only after an ALLOW.

    Attributes:
        tenant: bounded token, closure-bound at wrapper construction. Mandatory (I-8).
        principal: bounded token — the agent id, or `worker_<topic-ish>` for the worker leg.
            CLOSURE-BOUND, never a call argument: a wrapper that trusts a caller-supplied
            principal gates nothing (adversary A-8, and `dispatch.py:100-118`'s precedent).
        operation: dotted catalogue token (`fhir.read_patient`). Unknown => L-0 DENY.
        action_ref: dotted governance ref — the external-task topic verbatim on the worker leg,
            `agente.<operation>` on the agent leg (design §7.1).
        autonomy_action: the ratified `L0-core.yaml` name, or None when none exists (Q-4).
        process_key: `SP-OP-<DOMAIN>-<NNN>` or None. Bounded and non-PHI by the ADR-0016 shape.
        value_cents: non-negative `int` of centavos, or None. A NUMBER for the ceiling leg — never
            a payload. `bool` is refused (it is an `int` in Python, and `True` compared as 1
            centavo is the exact fail-open `ceilings._is_valid_value_cents` was written to close).
        phi_zone: `general` | `phi` (ADR-0006), from the agent's `security_zone`.

    Raises:
        EffectCallError: on any unbounded, mistyped, or PHI-shaped value.
    """

    tenant: str
    principal: str
    operation: str
    action_ref: str
    autonomy_action: str | None = None
    process_key: str | None = None
    value_cents: int | None = None
    phi_zone: str = PHI_ZONE_GENERAL

    def __post_init__(self) -> None:
        _require_token("tenant", self.tenant)
        _require_token("principal", self.principal)
        _require_dotted("operation", self.operation)
        _require_dotted("action_ref", self.action_ref)
        if self.autonomy_action is not None:
            _require_token("autonomy_action", self.autonomy_action)
        if self.process_key is not None and (
            not isinstance(self.process_key, str) or not _PROCESS_KEY_RE.match(self.process_key)
        ):
            raise EffectCallError(
                f"process_key must match {_PROCESS_KEY_RE.pattern!r} (ADR-0016 shape — also the "
                "defense against homoglyph/encoding smuggling)"
            )
        cents = self.value_cents
        if cents is not None and (isinstance(cents, bool) or not isinstance(cents, int) or cents < 0):
            raise EffectCallError("value_cents must be a non-negative int of centavos, or None")
        # `isinstance` FIRST, and not for tidiness: `{"general"} in PHI_ZONES` raises `TypeError`
        # (unhashable), which is NOT an `EffectCallError` — so `decide_effect`'s `except
        # EffectCallError` would have missed it and the generic guard below it would have reported
        # `GATEWAY_ERRO_INTERNO` for what is plainly a malformed call. One membership test against
        # a frozenset is a type error waiting for a dict, a list or a set; the guard makes the
        # declared contract ("every bad value raises EffectCallError") true for every input.
        if not isinstance(self.phi_zone, str) or self.phi_zone not in PHI_ZONES:
            raise EffectCallError(f"phi_zone must be one of {sorted(PHI_ZONES)}")


@dataclass(frozen=True, slots=True)
class EffectDecision:
    """The verdict for one `EffectCall`. Bounded tokens only; safe to log in the clear.

    Attributes:
        allow: the ENFORCEMENT-TERMS verdict. False means "this call would be blocked".
        enforced: whether a DENY must ACTUALLY block. Computed SEPARATELY from `allow` (§7.3) as
            `global modo == enforcing AND the class's enforcement == enforcing`. False for every
            class today, in both dimensions.
        reason: one bounded token — from :data:`EFFECT_REASONS` or, at L-5, from
            `action_execution`'s enum passed through verbatim.
        layer: which layer produced this decision (:class:`EffectLayer`). Required by §5.3: an
            approver must be able to tell an unsigned class from an undeclared capability.
        action_class: the resolved manifest class, or None when the operation was not catalogued.
        denial_shape: the class's DECLARED refusal shape (`effect_classes`), or None on an ALLOW /
            an unresolvable class. A wrapper must use THIS, never an improvised refusal (§5.4).
        operation: echoed for telemetry; None only when the call itself could not be built.
        mode: the manifest mode (`shadow` | `enforcing` | `unresolved` | `shadow_override`).
        enforcement: the per-class enforcement dimension (`shadow` | `enforcing`).
        sample_key: non-PHI sample key for the ADR-0034 L2 review hook, set only on an L2-level
            ALLOW (§5.6). None otherwise.
    """

    allow: bool
    enforced: bool
    reason: str
    layer: str
    action_class: str | None = None
    denial_shape: str | None = None
    operation: str | None = None
    mode: str = MODE_UNRESOLVED
    enforcement: str = ENFORCEMENT_SHADOW
    sample_key: str | None = None

    @property
    def telemetry_decision(self) -> str:
        """The bounded token a shadow log line carries: WOULD_ALLOW | WOULD_DENY."""
        return "WOULD_ALLOW" if self.allow else "WOULD_DENY"


# =================================================================================================
# Injected seams. Every one is a Protocol so `maezo.gateway` imports no concrete implementation —
# the layering `action_execution.py:206-208` protects. `pep.PEP` and
# `tools.workers.ceilings.CeilingResolver` satisfy theirs STRUCTURALLY, with no edit to either.
# =================================================================================================


@runtime_checkable
class CapabilityView(Protocol):
    """What the decision core needs to know about ONE principal's declared capabilities."""

    @property
    def principal(self) -> str: ...

    def allows_tool(self, tool_id: str) -> bool: ...

    def allows_process_key(self, process_key: str) -> bool: ...


class PepEvaluator(Protocol):
    """Structural shape of `maezo.gateway.pep.PEP.evaluate` (`pep.py:412`)."""

    def evaluate(self, action: str, agent_context: dict[str, Any] | None = None) -> Any: ...


class CeilingEvaluator(Protocol):
    """Structural shape of `maezo.tools.workers.ceilings.CeilingResolver` (`ceilings.py:134`)."""

    def within_l2_ceiling(self, *, tenant: str, action: str, param: str, value_cents: int) -> bool: ...


class ConsentEvaluator(Protocol):
    """L-4's seam. No adapter exists (`src/maezo/ports/consent.py` is a port only) — Q-5."""

    def has_consent(self, *, tenant: str, principal: str, action_class: str) -> bool: ...


class L2SampleHook(Protocol):
    """ADR-0034 :78-85's sampler seam. Best-effort: it may never block and never fail the call."""

    def __call__(
        self,
        *,
        tenant: str,
        principal: str,
        operation: str,
        action_class: str,
        sample_key: str,
    ) -> None: ...


def _noop_sample_hook(
    *,
    tenant: str,
    principal: str,
    operation: str,
    action_class: str,
    sample_key: str,
) -> None:
    """The DEFAULT sampler: does nothing, on purpose.

    ADR-0034 requires a concrete persistent `ReviewQueue` "as part of this chokepoint"; whether it
    ships in Wave 1 is Q-3, an owner decision. Shipping the HOOK with a no-op default is the
    honest middle: the seam and its `sample_key` contract exist and are tested, and no half-built
    persistence pretends to discharge the clause.
    """
    del tenant, principal, operation, action_class, sample_key


@dataclass(frozen=True, slots=True)
class AgentCapabilities:
    """A principal's DECLARED capabilities, already normalised and intersected. Immutable.

    Built ONCE per (tenant, principal) at composition time and closed over by the wrapper, so the
    principal can never be a call argument (adversary A-8).
    """

    principal: str
    tools: frozenset[str]
    process_keys: frozenset[str]

    @classmethod
    def of(
        cls,
        *,
        principal: str,
        tools: Iterable[str] | None = None,
        process_keys: Iterable[str] | None = None,
    ) -> AgentCapabilities:
        """Normalise an `agent.yaml` declaration into a capability view. FAIL-CLOSED.

        `process_keys` is INTERSECTED with :data:`KNOWN_PROCESS_KEYS`, so a key that is malformed,
        retired, or simply invented in an `agent.yaml` is silently absent from the allowed set and
        therefore denies at L-1. That intersection is the R-1 closure: ADR-0016's universe becomes
        load-bearing for the first time.
        """
        declared_tools = frozenset(
            item.strip() for item in (tools or ()) if isinstance(item, str) and item.strip()
        )
        declared_keys = frozenset(
            item.strip() for item in (process_keys or ()) if isinstance(item, str) and item.strip()
        )
        return cls(
            principal=principal,
            tools=declared_tools,
            process_keys=declared_keys & KNOWN_PROCESS_KEYS,
        )

    def allows_tool(self, tool_id: str) -> bool:
        return tool_id in self.tools

    def allows_process_key(self, process_key: str) -> bool:
        return process_key in self.process_keys


@dataclass(frozen=True, slots=True)
class DecisionContext:
    """Everything `decide` may consult, injected. Built once per (tenant, principal) seam.

    Attributes:
        capabilities: the principal's declared tools/process keys. None => L-1 fails closed.
        autonomy: the autonomy PEP (`pep.build_pep(...)`). None => L-2 fails closed.
        ceilings: the money-teto resolver. None is INERT while no catalogued operation declares a
            ceiling, and fails closed the moment one does.
        consent: the L-4 source. None is INERT while no class is flagged (Q-5).
        sampler: the ADR-0034 L2 hook. Defaults to a no-op; never influences the verdict.
        approvals_path: explicit manifest path — the composition-root/test seam, mirroring
            `load_action_approvals(path)`. Production passes None.
    """

    capabilities: CapabilityView | None = None
    autonomy: PepEvaluator | None = None
    ceilings: CeilingEvaluator | None = None
    consent: ConsentEvaluator | None = None
    sampler: L2SampleHook = _noop_sample_hook
    approvals_path: str | Path | None = None


# =================================================================================================
# The ladder
# =================================================================================================


def _load_approvals(path: str | Path | None) -> ActionApprovals:
    """The cached manifest view. NEVER raises — the loader already fails closed, and this guard
    covers the residual (a corrupted cache, a monkeypatched accessor)."""
    try:
        return action_execution.action_approvals(path)
    except Exception:  # noqa: BLE001 - fail-closed: an unreadable record approves nothing
        logger.error("effect_pep_approvals_unavailable", exc_info=True)
        return action_execution.ActionApprovals(
            mode=MODE_UNRESOLVED,
            approved=frozenset(),
            declared=frozenset(),
            topic_to_class={},
            degraded=True,
        )


def _sample_key(call: EffectCall, action_class: str) -> str:
    """A stable, NON-PHI sample key: sha256 over bounded tokens only, truncated to 32 hex chars.

    Shaped after `transport.py:704`'s `start_dedup_key` in POSTURE (a hash, never the raw value),
    not in inputs: that key embeds a business key, and a business key may never enter this module
    (I-3). Everything hashed here is already safe in the clear; the hash exists so a review queue
    can group and de-duplicate without the queue itself becoming a new identifier surface.
    """
    material = f"{call.tenant}|{call.principal}|{call.operation}|{action_class}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


def _deny(
    reason: str,
    layer: EffectLayer,
    *,
    enforced: bool,
    mode: str,
    enforcement: str,
    action_class: str | None = None,
    operation: str | None = None,
) -> EffectDecision:
    return EffectDecision(
        allow=False,
        enforced=enforced,
        reason=reason,
        layer=layer.value,
        action_class=action_class,
        denial_shape=effect_classes.denial_shape_for(action_class),
        operation=operation,
        mode=mode,
        enforcement=enforcement,
    )


def _l1_capability(call: EffectCall, spec: OperationSpec, ctx: DecisionContext) -> str | None:
    """L-1. Returns a bounded DENY reason, or None to continue. Raises nothing of its own."""
    capabilities = ctx.capabilities
    if capabilities is None:
        return REASON_CAPABILITIES_UNAVAILABLE
    # The tool question is only meaningful for an operation an `agent.yaml` CAN declare. A seam
    # that is not an MCP tool (`tool_id is None`) is not thereby waved through: it still faces
    # L-2 (VOCABULARIO_PENDENTE for the three nameless ones) and L-5 (no class is approved).
    if spec.tool_id is not None and not capabilities.allows_tool(spec.tool_id):
        return REASON_TOOL_UNDECLARED
    if spec.requires_process_key:
        key = call.process_key
        # No key at all is NOT "no restriction" — an engine start with an unnamed process is
        # unauthorizable. The membership test is against the agent's declared keys ALREADY
        # intersected with the ADR-0016 universe (`AgentCapabilities.of`), which is R-1's closure.
        if key is None or not capabilities.allows_process_key(key):
            return REASON_PROCESS_KEY_FORBIDDEN
    return None


def _l2_autonomy(call: EffectCall, spec: OperationSpec, ctx: DecisionContext) -> str | None:
    """L-2. Returns a bounded DENY reason, or None to continue."""
    if spec.autonomy_action is None:
        # Q-4: no ratified name exists for this seam. Recording an honest would-deny is the only
        # option that does not invent vocabulary (`pep.py:12-18`).
        return REASON_VOCABULARY_PENDING
    if ctx.autonomy is None:
        return REASON_POLICY_UNAVAILABLE
    verdict = ctx.autonomy.evaluate(
        spec.autonomy_action,
        {"tenant": call.tenant, "agent_id": call.principal, "operation": call.operation},
    )
    if verdict is pep.Decision.ALLOW:
        return None
    if verdict is pep.Decision.REQUIRE_HUMAN:
        return REASON_HUMAN_REQUIRED
    # Includes DENY and ANY unexpected return value. Identity against the ALLOW member, never a
    # truthiness test: a stub returning `"ALLOW"`, `1`, or `None` must not read as permission.
    return REASON_AUTONOMY_DENIED


def _l3_ceiling(call: EffectCall, spec: OperationSpec, ctx: DecisionContext) -> str | None:
    """L-3. Returns a bounded DENY reason, or None to continue. INERT unless the catalogue says so."""
    if spec.ceiling_action is None or spec.ceiling_param is None:
        return None
    if ctx.ceilings is None or call.value_cents is None:
        return REASON_CEILING_EXCEEDED
    within = ctx.ceilings.within_l2_ceiling(
        tenant=call.tenant,
        action=spec.ceiling_action,
        param=spec.ceiling_param,
        value_cents=call.value_cents,
    )
    return None if within is True else REASON_CEILING_EXCEEDED


def _l4_consent(call: EffectCall, action_class: str, ctx: DecisionContext) -> str | None:
    """L-4. Returns a bounded DENY reason, or None to continue. INERT unless a class is flagged."""
    class_spec = effect_classes.lookup_class(action_class)
    if class_spec is None or not class_spec.consentimento_exigido:
        return None
    if ctx.consent is None:
        return REASON_CONSENT_MISSING
    granted = ctx.consent.has_consent(tenant=call.tenant, principal=call.principal, action_class=action_class)
    return None if granted is True else REASON_CONSENT_MISSING


def _outermost_enforcement(ctx: DecisionContext | None) -> tuple[str, bool]:
    """The `(mode, enforced)` an OUTERMOST failure reports. NEVER raises.

    Mirrors `evaluate_worker_task`'s double-guarded fallback exactly, including the part that took
    a GK finding to get right: the GLOBAL ceiling is re-read from the already-cached manifest, and
    the PER-CLASS dimension is deliberately NOT applied. Reaching this function means no class was
    resolved, so `enforcement_padrao_nao_mapeado` — a decision about UNCLASSIFIED TRAFFIC, `shadow`
    during the ramp — must not be allowed to downgrade a GATEWAY FAILURE under a live global
    `modo: enforcing`. `enforced` therefore reduces to `mode == enforcing`, which is precisely what
    `action_execution._fail_closed_decision`'s `enforcing` default encodes on the worker leg.

    If even the cached read fails (it is one of the raising paths this guard exists for), the mode
    is `unresolved` and nothing enforces: an unresolvable record may never CLAIM enforcement.
    """
    try:
        mode = action_execution.action_approvals(ctx.approvals_path if ctx is not None else None).mode
    except Exception:  # noqa: BLE001 - nothing left to trust; refuse to claim enforcement
        return MODE_UNRESOLVED, False
    return mode, mode == action_execution.MODE_ENFORCING


def decide(call: EffectCall, ctx: DecisionContext | None = None) -> EffectDecision:
    """Decide ONE attempted effect. TOTAL: every input maps to a bounded `EffectDecision`.

    NEVER raises — see the module docstring's containment chain, whose OUTERMOST link is this
    function body. Most-restrictive-wins: the ladder returns at the first DENY and no later layer
    can widen it.

    The per-layer guards in :func:`_decide_ladder` contain every failure they can ATTRIBUTE. This
    wrapper contains the ones they cannot, and they are real, not hypothetical — a GK proved three:
    a raising `logger` INSIDE a layer guard (the guard's own `logger.error` re-raises past it),
    `effect_classes.denial_shape_for` raising (it runs inside `_deny`, after the guard returned),
    and `ActionApprovals.enforcement_for` raising (it runs before the first guard exists). Until
    this wrapper landed, `decide` was total for its layers and partial for itself, which is not the
    same claim — and B2's wrappers call `decide` on a care path.
    """
    try:
        return _decide_ladder(call, ctx)
    except Exception:  # noqa: BLE001 - design I-4: the PEP ITSELF must be total, not just its layers
        # The log is suppressed-guarded because a RAISING LOGGER is one of the proven paths into
        # this handler; logging the containment must not re-raise out of it.
        with contextlib.suppress(Exception):
            logger.error("effect_pep_internal_error", layer=EffectLayer.ENTRADA.value, exc_info=True)
        mode, enforced = _outermost_enforcement(ctx)
        return EffectDecision(
            allow=False,
            enforced=enforced,
            reason=REASON_INTERNAL_ERROR,
            # `L_ENTRADA`, not the layer that happened to blow up: attribution is only honest when
            # the guard that produced it knows which layer that was, and this one does not.
            layer=EffectLayer.ENTRADA.value,
            action_class=None,
            denial_shape=None,
            # NOT read off `call`: reaching here means an arbitrary object may be in that name, and
            # touching its attributes is another raising path. Bounded constants only.
            operation=None,
            mode=mode,
            enforcement=ENFORCEMENT_ENFORCING if enforced else ENFORCEMENT_SHADOW,
        )


def _decide_ladder(call: EffectCall, ctx: DecisionContext | None = None) -> EffectDecision:
    """The ladder itself (L-0 .. L-6). Called ONLY through :func:`decide`, which is its guard."""
    context = ctx if ctx is not None else DecisionContext()
    approvals = _load_approvals(context.approvals_path)
    mode = approvals.mode
    unmapped_enforcement = approvals.enforcement_for(None)

    def enforced_for(action_class: str | None) -> tuple[bool, str]:
        enforcement = approvals.enforcement_for(action_class)
        return (
            mode == action_execution.MODE_ENFORCING and enforcement == ENFORCEMENT_ENFORCING,
            enforcement,
        )

    operation = call.operation if isinstance(call, EffectCall) else None

    # -- L-0 CATALOGUE -----------------------------------------------------------------------
    try:
        spec = effect_classes.lookup_operation(operation)
    except Exception:  # noqa: BLE001 - a catalogue bug denies; it never crashes a care path
        logger.error("effect_pep_layer_error", layer=EffectLayer.CATALOGO.value, exc_info=True)
        return _deny(
            REASON_INTERNAL_ERROR,
            EffectLayer.CATALOGO,
            enforced=mode == action_execution.MODE_ENFORCING
            and unmapped_enforcement == ENFORCEMENT_ENFORCING,
            mode=mode,
            enforcement=unmapped_enforcement,
            operation=operation,
        )
    if spec is None:
        enforced, enforcement = enforced_for(None)
        return _deny(
            REASON_OPERATION_UNKNOWN,
            EffectLayer.CATALOGO,
            enforced=enforced,
            mode=mode,
            enforcement=enforcement,
            operation=operation,
        )

    action_class = spec.action_class
    enforced, enforcement = enforced_for(action_class)

    def deny_at(reason: str, layer: EffectLayer) -> EffectDecision:
        return _deny(
            reason,
            layer,
            enforced=enforced,
            mode=mode,
            enforcement=enforcement,
            action_class=action_class,
            operation=spec.operation,
        )

    # -- L-1 .. L-4: each layer is guarded INDIVIDUALLY, so a failure is attributed to the layer
    # that produced it rather than collapsing into one opaque "gateway error".
    ladder: tuple[tuple[EffectLayer, Callable[[], str | None]], ...] = (
        (EffectLayer.CAPACIDADE, lambda: _l1_capability(call, spec, context)),
        (EffectLayer.AUTONOMIA, lambda: _l2_autonomy(call, spec, context)),
        (EffectLayer.TETO, lambda: _l3_ceiling(call, spec, context)),
        (EffectLayer.CONSENTIMENTO, lambda: _l4_consent(call, action_class, context)),
    )
    for layer, evaluate_layer in ladder:
        try:
            reason = evaluate_layer()
        except Exception:  # noqa: BLE001 - an injected seam that misbehaves DENIES, never raises
            logger.error("effect_pep_layer_error", layer=layer.value, exc_info=True)
            return deny_at(REASON_INTERNAL_ERROR, layer)
        if reason is not None:
            return deny_at(reason, layer)

    # -- L-5 RATIFICATION — the HUMAN gate. `ActionExecutionGateway` is used UNCHANGED, and its
    # precise reason vocabulary is passed through so the existing shadow telemetry keeps meaning
    # exactly what it means on the worker leg.
    try:
        verdict = ActionExecutionGateway(approvals).evaluate(action_class, {"tenant": call.tenant})
    except Exception:  # noqa: BLE001 - belt-and-suspenders over a function that cannot raise
        logger.error("effect_pep_layer_error", layer=EffectLayer.RATIFICACAO.value, exc_info=True)
        return deny_at(REASON_INTERNAL_ERROR, EffectLayer.RATIFICACAO)
    if not verdict.allow:
        return deny_at(verdict.reason, EffectLayer.RATIFICACAO)

    # -- L-6 SAMPLING — after the verdict, never part of it.
    sample_key = _sample_key(call, action_class)
    try:
        context.sampler(
            tenant=call.tenant,
            principal=call.principal,
            operation=spec.operation,
            action_class=action_class,
            sample_key=sample_key,
        )
    except Exception:  # noqa: BLE001 - best-effort by contract (ADR-0034 :78-85)
        logger.warning("effect_pep_sampler_failed", operation=spec.operation, exc_info=True)

    return EffectDecision(
        allow=True,
        enforced=enforced,
        reason=verdict.reason,
        layer=EffectLayer.RATIFICACAO.value,
        action_class=action_class,
        denial_shape=None,
        operation=spec.operation,
        mode=mode,
        enforcement=enforcement,
        sample_key=sample_key,
    )


def decide_effect(
    *,
    tenant: str,
    principal: str,
    operation: str,
    ctx: DecisionContext | None = None,
    process_key: str | None = None,
    value_cents: int | None = None,
    phi_zone: str = PHI_ZONE_GENERAL,
    action_ref: str | None = None,
) -> EffectDecision:
    """Build the `EffectCall` and decide, in one TOTAL call. The entry point the wrappers use.

    This exists so a seam wrapper cannot accidentally skip validation or let an `EffectCallError`
    escape onto a care path: a malformed call is a bounded DENY at `L_ENTRADA`, not an exception.
    `autonomy_action` is taken from the CATALOGUE, never from the caller — a caller-supplied
    autonomy name (or action class) would be adversary A-7's opening.

    EVERY GUARD BELOW RESOLVES `enforced` THE SAME WAY `decide` DOES. This function sits in FRONT
    of `decide`, so its own guards catch failures `decide` never sees — a raising
    `lookup_operation`, a raising `agent_action_ref`, a `decide` that escaped anyway. Each of those
    used to hardcode `enforced=False`, which meant the SAME class of gateway bug blocked or did not
    block depending only on which of the two entry points a wrapper had called: `decide` returned
    `ENFORCED=True` under a live `modo: enforcing` while `decide_effect` returned `False` for the
    identical fault. `_outermost_enforcement` is now used in all three, so the two entry points are
    indistinguishable in enforcement terms — the property B2's wrappers depend on, since they call
    `decide_effect` and the incident-shape proofs are written against `decide`.

    The ONE guard that does not call it is the `EffectCallError` branch, deliberately: a malformed
    CALL is a policy outcome with a resolvable class (the catalogue lookup already succeeded), so
    it reports the real per-class enforcement rather than the classless global fallback.

    Args:
        tenant: bounded token, closure-bound at wrapper construction.
        principal: bounded token, closure-bound. NEVER accept this from the call site.
        operation: a catalogue operation token.
        ctx: the injected seams. `None` builds an empty context, which denies at L-1.
        process_key: `SP-OP-*` when the operation starts a process.
        value_cents: non-negative centavos, for the ceiling leg only.
        phi_zone: ADR-0006 zone of the principal.
        action_ref: override the derived governance ref. Defaults to `agente.<operation>`; the
            worker leg passes its external-task topic verbatim.
    """
    context = ctx if ctx is not None else DecisionContext()
    try:
        # Guarded with the construction itself: a catalogue lookup that raises here would escape
        # BEFORE `decide`'s own containment could see it, which would make the "total" claim true
        # of `decide` and false of the function the wrappers actually call.
        spec = effect_classes.lookup_operation(operation)
    except Exception:  # noqa: BLE001 - a catalogue bug denies; it never crashes a care path
        logger.error("effect_pep_catalogue_error", layer=EffectLayer.CATALOGO.value, exc_info=True)
        mode, enforced = _outermost_enforcement(context)
        return EffectDecision(
            allow=False,
            enforced=enforced,
            reason=REASON_INTERNAL_ERROR,
            layer=EffectLayer.CATALOGO.value,
            mode=mode,
            enforcement=ENFORCEMENT_ENFORCING if enforced else ENFORCEMENT_SHADOW,
        )
    approvals = _load_approvals(context.approvals_path)
    try:
        call = EffectCall(
            tenant=tenant,
            principal=principal,
            operation=operation,
            action_ref=action_ref if action_ref is not None else effect_classes.agent_action_ref(operation),
            autonomy_action=None if spec is None else spec.autonomy_action,
            process_key=process_key,
            value_cents=value_cents,
            phi_zone=phi_zone,
        )
    except EffectCallError:
        logger.error("effect_pep_call_rejected", layer=EffectLayer.ENTRADA.value, exc_info=True)
        enforcement = approvals.enforcement_for(None if spec is None else spec.action_class)
        return EffectDecision(
            allow=False,
            enforced=approvals.mode == action_execution.MODE_ENFORCING
            and enforcement == ENFORCEMENT_ENFORCING,
            reason=REASON_INVALID_CALL,
            layer=EffectLayer.ENTRADA.value,
            action_class=None if spec is None else spec.action_class,
            denial_shape=None if spec is None else effect_classes.denial_shape_for(spec.action_class),
            operation=operation if isinstance(operation, str) and _DOTTED_TOKEN_RE.match(operation) else None,
            mode=approvals.mode,
            enforcement=enforcement,
        )
    except Exception:  # noqa: BLE001 - nothing may escape onto an effect path
        logger.error("effect_pep_call_rejected", layer=EffectLayer.ENTRADA.value, exc_info=True)
        mode, enforced = _outermost_enforcement(context)
        return EffectDecision(
            allow=False,
            enforced=enforced,
            reason=REASON_INTERNAL_ERROR,
            layer=EffectLayer.ENTRADA.value,
            mode=mode,
            enforcement=ENFORCEMENT_ENFORCING if enforced else ENFORCEMENT_SHADOW,
        )
    try:
        return decide(call, context)
    except Exception:  # noqa: BLE001 - `decide` is total; this is the outermost belt-and-suspenders
        logger.error("effect_pep_internal_error", operation=call.operation, exc_info=True)
        mode, enforced = _outermost_enforcement(context)
        return EffectDecision(
            allow=False,
            enforced=enforced,
            reason=REASON_INTERNAL_ERROR,
            layer=EffectLayer.ENTRADA.value,
            operation=call.operation,
            mode=mode,
            enforcement=ENFORCEMENT_ENFORCING if enforced else ENFORCEMENT_SHADOW,
        )


def log_effect_decision(decision: EffectDecision, call: EffectCall) -> None:
    """Emit the ONE telemetry line for a decided effect. Every field is a bounded, non-PHI token.

    Deliberately the SAME event names the worker leg already emits
    (`action_execution_gateway_shadow` / `_enforced`, `action_execution.py:1072-1073`): the
    approval packet counts lines by `decision` × `reason` × `tenant`, and a second event family
    would fragment exactly the evidence §9.2 asks the approvers to read. The agent leg adds
    `operation`, `layer`, `principal` and `denial_shape` — all bounded, all new dimensions rather
    than changed ones, so nothing that filtered on the old fields stops working.
    """
    logger.info(
        EVENT_ENFORCED if decision.enforced else EVENT_SHADOW,
        topic=decision.operation or "OPERACAO_INVALIDA",
        operation=decision.operation or "OPERACAO_INVALIDA",
        action_ref=call.action_ref,
        principal=call.principal,
        action_class=decision.action_class or "NAO_MAPEADA",
        decision=decision.telemetry_decision,
        reason=decision.reason,
        layer=decision.layer,
        denial_shape=decision.denial_shape or "NENHUMA",
        mode=decision.mode,
        enforcement=decision.enforcement,
        phi_zone=call.phi_zone,
        tenant=call.tenant,
    )


__all__ = [
    "EFFECT_REASONS",
    "KNOWN_PROCESS_KEYS",
    "PHI_ZONES",
    "PHI_ZONE_GENERAL",
    "PHI_ZONE_PHI",
    "REASON_ALLOWED",
    "REASON_AUTONOMY_DENIED",
    "REASON_CAPABILITIES_UNAVAILABLE",
    "REASON_CEILING_EXCEEDED",
    "REASON_CONSENT_MISSING",
    "REASON_HUMAN_REQUIRED",
    "REASON_INTERNAL_ERROR",
    "REASON_INVALID_CALL",
    "REASON_OPERATION_UNKNOWN",
    "REASON_POLICY_UNAVAILABLE",
    "REASON_PROCESS_KEY_FORBIDDEN",
    "REASON_TOOL_UNDECLARED",
    "REASON_VOCABULARY_PENDING",
    "AgentCapabilities",
    "CapabilityView",
    "CeilingEvaluator",
    "ConsentEvaluator",
    "DecisionContext",
    "EffectCall",
    "EffectCallError",
    "EffectDecision",
    "EffectLayer",
    "L2SampleHook",
    "PepEvaluator",
    "decide",
    "decide_effect",
    "log_effect_decision",
]
