"""NotificationBridge — Cross-process choreography via Kafka → CIB Seven.

Listens to domain-event Kafka topics and starts downstream BPMN processes
via the CIB Seven REST API when handoff conditions are met.

Handoffs:
- CONTAS→RECURSO: when glosa confirmed (decisao_contas == RECORRER),
  starts SP-OP-RECURSO-001 with glosa context.
- CONTAS→FRAUDE: when encaminhar_fraude == true, starts SP-OP-FRAUDE-001
  with evidence references and provenance.
- FRAUDE→CRED: when fraud confirmed against a prestador, starts SP-OP-CRED-001.
- FRAUDE→CANCEL/INADIMPLENCIA: when fraud confirmed against a beneficiario,
  starts SP-OP-CANCEL-001 / SP-OP-INADIMPLENCIA-001.
- NIP→ANS-SUBMIT (T2.6-7): when SP-OP-NIP-001 hands off a formal NIP response
  (`operadora.nip.handoff_ans_submit` worker output, `origem_envio == "nip_filing"`),
  starts SP-OP-ANS-SUBMIT-001 so `Flow_GW_NipFiling` (`${origem_envio=='nip_filing'}`,
  BPMN `SP-OP-ANS-SUBMIT-001_Envios_Periodicos_ANS.bpmn:595-596`) routes it to the
  mandatory juridical review `UT_RevisarEnvioJuridico` — this handoff NEVER transmits;
  it only opens the gated pipeline (ADR-0007 HITL pre-filing is unchanged downstream).
- ans.cron_due→ANS-SUBMIT (T2.6-7): when SP-OP-ANS-CRON-001's per-report_type timer
  publishes the typed `ans.cron_due` fact, starts SP-OP-ANS-SUBMIT-001 seeded with
  fail-closed admissibility facts (`Start_DespachoEnvio` BPMN comment) — the human
  resolves the real competência/dataset facts in `UT_CorrigirPendenciaEnvio`.

Design source of truth for the two T2.6-7 handoffs:
`docs/design/T2.6-ans-submission-rescope.md` §1.5 (current-state map) + the T2.6-7 row
of §5's task table (business-key/variable derivation, SME-gated placeholders, and the
explicit "no live trigger today" finding this module now (partially) closes — the
Kafka-publish leg on the `nip.py`/`ans_cron.py` side and the live-consumer wiring that
actually calls `on_event`/`execute_handoff` in production remain open, see this module's
`build_cibseven_process_starter` docstring and the xfail-strict integration coverage in
`tests/integration/processes/`).

London School TDD: the bridge is pure domain logic. Kafka consumption and
CIB Seven HTTP calls are injected as async callables so tests remain fast.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import structlog

from maezo.tools.mcp_cibseven.transport import (
    AgentDecisionProvenance,
    AuditStartSink,
    CibSevenTransport,
    start_process_idempotent,
)

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# T2.6-7 constants — SP-OP-ANS-SUBMIT-001 target + SME-gated placeholders
# ---------------------------------------------------------------------------

#: Target process key for both new T2.6-7 handoffs (contract `SP-OP-ANS-SUBMIT-001.md`).
PROCESS_KEY_ANS_SUBMIT = "SP-OP-ANS-SUBMIT-001"

#: Sentinel competência the BPMN's own `Start_DespachoEnvio` comment documents for the
#: calendar path ("competencia=COMPETENCIA_PENDENTE (sentinela)") — reused verbatim for the
#: nip_filing path below (per-case filings do not carry a periodic competência either; the
#: fail-closed admissibility facts route BOTH paths to a human who resolves the real value).
_COMPETENCIA_PENDENTE = "COMPETENCIA_PENDENTE"

#: SME-GATED PLACEHOLDER (docs/review-queue.md:315; design doc §1.5 table row
#: "RN_209_UTILIZACAO ... Also the slot the NIP-response filing reuses"): the exact
#: `report_type` classification for a NIP-originated filing is NOT yet confirmed by
#: regulatório/jurídico. Reusing the already-documented placeholder here is NOT a new
#: regulatory claim — it is the value the design's own current-state map already records as
#: today's best-available slot; a future SME sign-off only needs to change this one constant.
_ANS_REPORT_TYPE_NIP_FILING = "RN_209_UTILIZACAO"

#: SME-gated placeholder periodicity for the nip_filing path (docs/review-queue.md:315 flags
#: it for regulatório confirmation) — conservative monthly granularity, never derived from the
#: processing clock (constants only preserve business-key idempotency on redelivery).
_PERIODICIDADE_NIP_FILING = "mensal"


# ---------------------------------------------------------------------------
# Domain event types
# ---------------------------------------------------------------------------


@dataclass
class HandoffEvent:
    """A domain event that may trigger a cross-process handoff."""

    event_type: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class HandoffResult:
    """Result of evaluating and (optionally) executing a handoff."""

    evaluated: bool = False
    handoff_triggered: bool = False
    target_process: str = ""
    variables: dict[str, Any] = field(default_factory=dict)
    process_instance_id: str = ""
    reason: str = ""


# ---------------------------------------------------------------------------
# Internal rule representation
# ---------------------------------------------------------------------------


@dataclass
class _HandoffRule:
    """Internal representation of a handoff rule."""

    event_type: str
    predicate: Callable[[dict[str, Any]], bool]
    target_process: str
    variables_fn: Callable[[dict[str, Any]], dict[str, Any]]


# ---------------------------------------------------------------------------
# T2.6-7 business-key derivation (contract "Business key" / "Variaveis de saida")
# ---------------------------------------------------------------------------


def _ans_cron_business_key(tenant_id: str, report_type: str, competencia: str) -> str:
    """Deterministic SUBMIT business key for the calendar path.

    `ANSSUB-{tenant_id}-{report_type}-{competencia}` (contract `SP-OP-ANS-SUBMIT-001.md`
    "Business key") — one instance per `{report_type, competencia}`. A re-tick for the same
    (unresolved) `COMPETENCIA_PENDENTE` sentinel converges on the SAME pending instance —
    idempotent, never duplicates the filing (BPMN `:461`'s own invariant).
    """
    return f"ANSSUB-{tenant_id}-{report_type}-{competencia}"


def _ans_nip_business_key(tenant_id: str, numero_nip_ans: str) -> str:
    """Deterministic SUBMIT business key for the nip_filing path.

    `ANSSUB-{tenant_id}-nipfiling-{numero_nip_ans}` (contract `SP-OP-ANS-SUBMIT-001.md`
    "Variaveis de saida", `protocolo_ans` row: "correlacionar por nip_protocolo_origem/business
    key ANSSUB-{tenant}-nipfiling-{submit_id}"). `numero_nip_ans` anchors `submit_id` — it is
    the one per-case identifier `nip.handoff_ans_submit` ALWAYS carries; `protocolo_ans` cannot
    anchor it because it may legitimately be absent (GAP-NIP-6, nip.py). One instance per NIP
    case (per-case filing, not periodic — the report_type/competência pair alone would collide
    every nip_filing case sharing a tenant onto a single pending instance).
    """
    return f"ANSSUB-{tenant_id}-nipfiling-{numero_nip_ans}"


# ---------------------------------------------------------------------------
# T2.6-7 variable derivation (NIP handoff + ans.cron_due fact -> SUBMIT seed variables)
# ---------------------------------------------------------------------------


def _ans_submit_variables_from_nip_handoff(payload: dict[str, Any]) -> dict[str, Any]:
    """Map `nip.handoff_ans_submit`'s worker output (`nip.py:327-355`) onto
    SP-OP-ANS-SUBMIT-001 seed variables.

    `origem_envio="nip_filing"` drives `Flow_GW_NipFiling` straight to the mandatory
    `UT_RevisarEnvioJuridico` — this mapping NEVER transmits, it only opens the gated
    pipeline. Admissibility facts are seeded FAIL-CLOSED (`False`) — mirrors the BPMN's own
    documented calendar-path convention (`Start_DespachoEnvio` comment); a human resolves them
    in `UT_CorrigirPendenciaEnvio`/`UT_RevisarEnvioJuridico`, never auto-passed.

    `report_type`/`competencia`/`periodicidade` are SME-gated placeholders (module-level
    constants, see their docstrings) — CONSTANTS, never derived from the processing clock, so
    redelivery of the same NIP case always reconstructs the same business key.

    `tenant_id` is a genuine, pre-existing gap: `handoff_ans_submit`'s own return dict
    (`nip.py:348-355`) does not carry it (only `numero_nip_ans`/`protocolo_ans`/`decisao_nip`/
    `data_recebimento_nip_iso`) — this mapping passes through whatever `tenant_id` key the
    eventual event envelope supplies, defaulting to `""` fail-closed. Boundary flagged in the
    T2.6-7 PR body; NOT fabricated here (out of this rule's edit authority over `nip.py`).
    """
    tenant_id = str(payload.get("tenant_id", ""))
    numero_nip_ans = str(payload.get("numero_nip_ans", ""))
    protocolo_ans = payload.get("protocolo_ans")
    return {
        "tenant_id": tenant_id,
        "report_type": _ANS_REPORT_TYPE_NIP_FILING,
        "competencia": _COMPETENCIA_PENDENTE,
        "periodicidade": _PERIODICIDADE_NIP_FILING,
        "origem_envio": "nip_filing",
        "nip_protocolo_origem": protocolo_ans if isinstance(protocolo_ans, str) else "",
        "numero_nip_ans": numero_nip_ans,
        "dataset_complete": False,
        "schema_valid": False,
        "lgpd_anonimizado": False,
        "dataset_ref": "",
        "business_key": _ans_nip_business_key(tenant_id, numero_nip_ans),
    }


def _ans_submit_variables_from_cron_due(payload: dict[str, Any]) -> dict[str, Any]:
    """Map the `ans.cron_due` fact (SP-OP-ANS-CRON-001's per-report_type timer dispatch,
    `ans_cron.py:79-108`) onto SP-OP-ANS-SUBMIT-001 seed variables.

    `origem_envio="calendario"` (the fact's own literal). Admissibility facts are seeded
    FAIL-CLOSED (`False`) — same `Start_DespachoEnvio` BPMN convention as the nip_filing rule
    above. `competencia` passes through the fact's own value (today always the literal
    `COMPETENCIA_PENDENTE` sentinel baked into the BPMN's `camunda:inputParameter`s — FINDING #1,
    `tests/integration/processes/test_sp_op_ans_cron_001.py`), falling back to the sentinel only
    if the fact is missing it entirely (never silently invents a real period).

    `tenant_id` is a genuine, pre-existing gap: the fact carries NO `tenant_id` today
    (`test_sp_op_ans_cron_001.py:400` asserts `"tenant_id" not in fact`) — SP-OP-ANS-CRON-001's
    BPMN bakes `report_type`/`periodicidade`/`origem_envio`/`competencia` as literal
    `camunda:inputParameter`s with no tenant scoping. Fixing that is a `spec/` edit (BPMN),
    outside this mechanical wiring task's edit authority (T2.6-7 is explicitly "no spec/contract
    change"). This mapping passes through whatever `tenant_id` key the eventual event envelope
    supplies, defaulting to `""` fail-closed — boundary flagged in the T2.6-7 PR body.
    """
    tenant_id = str(payload.get("tenant_id", ""))
    report_type = str(payload.get("report_type", ""))
    competencia = str(payload.get("competencia", "")).strip() or _COMPETENCIA_PENDENTE
    periodicidade = str(payload.get("periodicidade", ""))
    return {
        "tenant_id": tenant_id,
        "report_type": report_type,
        "competencia": competencia,
        "periodicidade": periodicidade,
        "origem_envio": "calendario",
        "dataset_complete": False,
        "schema_valid": False,
        "lgpd_anonimizado": False,
        "dataset_ref": "",
        "business_key": _ans_cron_business_key(tenant_id, report_type, competencia),
    }


# ---------------------------------------------------------------------------
# NotificationBridge
# ---------------------------------------------------------------------------


class NotificationBridge:
    """Listens to domain-event Kafka topics and starts CIB Seven processes.

    Handoff rules are defined by their event_type prefix, a predicate
    over the payload, and the target process key + variable mapping.

    Multiple rules may match the same event_type (e.g., fraude.acusacao_registrada
    triggers both CRED, CANCEL, and INADIMPLENCIA for different entity types).

    Injectables (London School):
    - cibseven_starter: async callable(process_key, variables) -> instance_id
    """

    # List of (event_type, predicate, process_key, variables_fn)
    _HANDOFF_RULES: list[_HandoffRule] = []

    def __init__(
        self,
        cibseven_starter: Callable[..., Any] | None = None,
    ) -> None:
        """Initialize the bridge.

        Args:
            cibseven_starter: Callable(process_key, variables) that starts a CIB Seven
                              process. Production MUST inject a starter built by
                              `build_cibseven_process_starter` (below) — the ONLY sanctioned
                              path, since it funnels every start through the fenced
                              `start_process_idempotent` chokepoint (ADR-0007/T-C2: durable
                              audit BEFORE effect, idempotent by business key). NEVER inject a
                              raw engine call directly. In tests it can be a simple spy.
        """
        self._starter = cibseven_starter or _noop_starter
        self._rules: list[_HandoffRule] = []
        self._register_default_handoffs()

    # -------------------------------------------------------------------
    # Default handoff rules (cross-process choreography)
    # -------------------------------------------------------------------

    def _register_default_handoffs(self) -> None:
        """Register the seven cross-process handoff rules from the spec.

        CONTAS→RECURSO, CONTAS→FRAUDE, FRAUDE→CRED, FRAUDE→CANCEL/INADIMPLENCIA (5, pre-T2.6-7)
        + NIP→ANS-SUBMIT, ans.cron_due→ANS-SUBMIT (2, T2.6-7).
        """
        # CONTAS→RECURSO: when glosa confirmed → start SP-OP-RECURSO-001
        self.register_handoff(
            event_type="contas.glosa_confirmed",
            predicate=lambda p: p.get("decisao_contas") == "RECORRER",
            target_process="SP-OP-RECURSO-001",
            variables_fn=lambda p: {
                "glosa_id": p.get("glosa_id", ""),
                "numero_guia_tiss": p.get("numero_guia_tiss", ""),
                "glosa_type": p.get("glosa_type", ""),
                "glosa_existe": True,
                "documentacao_anexa": p.get("documentacao_anexa", False),
                "numero_lote_tiss": p.get("numero_lote_tiss", ""),
            },
        )

        # CONTAS→FRAUDE: when encaminhar_fraude == true
        self.register_handoff(
            event_type="contas.encaminhar_fraude",
            predicate=lambda p: p.get("encaminhar_fraude") is True,
            target_process="SP-OP-FRAUDE-001",
            variables_fn=lambda p: {
                "origem_encaminhamento": "contas",
                "encaminhado_por_id": p.get("analista_id", ""),
                "numero_lote_tiss": p.get("numero_lote_tiss", ""),
                "prestador_id": p.get("prestador_id", ""),
                "evidencia_refs": p.get("evidencia_refs", []),
                "indicadores_presentes": p.get("indicadores_presentes", []),
            },
        )

        # FRAUDE→CRED: when fraud confirmed against a prestador
        self.register_handoff(
            event_type="fraude.acusacao_registrada",
            predicate=lambda p: (
                p.get("decisao_fraude") == "ACUSAR_FRAUDE" and p.get("entidade_tipo") == "prestador"
            ),
            target_process="SP-OP-CRED-001",
            variables_fn=lambda p: {
                "prestador_id": p.get("prestador_id", ""),
                "numero_caso": p.get("numero_caso", ""),
                "bundle_root": p.get("bundle_root", ""),
                "destino_referral": p.get("destino_referral", {}),
            },
        )

        # FRAUDE→CANCEL: when fraud confirmed against a beneficiario/contract
        self.register_handoff(
            event_type="fraude.acusacao_registrada",
            predicate=lambda p: (
                p.get("decisao_fraude") == "ACUSAR_FRAUDE"
                and p.get("entidade_tipo") in ("beneficiario", "contrato")
            ),
            target_process="SP-OP-CANCEL-001",
            variables_fn=lambda p: {
                "numero_contrato": p.get("numero_contrato", ""),
                "beneficiario_pseudo_id": p.get("beneficiario_pseudo_id", ""),
                "numero_caso": p.get("numero_caso", ""),
                "bundle_root": p.get("bundle_root", ""),
            },
        )

        # FRAUDE→INADIMPLENCIA: secondary handoff for contract fraud
        self.register_handoff(
            event_type="fraude.acusacao_registrada",
            predicate=lambda p: (
                p.get("decisao_fraude") == "ACUSAR_FRAUDE" and p.get("entidade_tipo") == "contrato"
            ),
            target_process="SP-OP-INADIMPLENCIA-001",
            variables_fn=lambda p: {
                "numero_contrato": p.get("numero_contrato", ""),
                "beneficiario_pseudo_id": p.get("beneficiario_pseudo_id", ""),
                "numero_caso": p.get("numero_caso", ""),
            },
        )

        # NIP→ANS-SUBMIT (T2.6-7): formal NIP response handed off for official filing.
        # Fail-closed predicate: BOTH the origin marker AND the per-case anchor
        # (numero_nip_ans, the business-key anchor) must be present/non-blank, or the
        # handoff does not trigger — a malformed/incomplete event never starts a process
        # with a garbage/non-deterministic business key.
        self.register_handoff(
            event_type="nip.handoff_ans_submit",
            predicate=lambda p: (
                p.get("origem_envio") == "nip_filing" and bool(str(p.get("numero_nip_ans", "")).strip())
            ),
            target_process=PROCESS_KEY_ANS_SUBMIT,
            variables_fn=_ans_submit_variables_from_nip_handoff,
        )

        # ans.cron_due→ANS-SUBMIT (T2.6-7): per-report_type scheduler tick.
        # Fail-closed predicate: report_type must be present/non-blank (no sensible
        # calendar/business-key derivation without it).
        self.register_handoff(
            event_type="ans.cron_due",
            predicate=lambda p: bool(str(p.get("report_type", "")).strip()),
            target_process=PROCESS_KEY_ANS_SUBMIT,
            variables_fn=_ans_submit_variables_from_cron_due,
        )

    # -------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------

    def register_handoff(
        self,
        event_type: str,
        predicate: Callable[[dict[str, Any]], bool],
        target_process: str,
        variables_fn: Callable[[dict[str, Any]], dict[str, Any]],
    ) -> None:
        """Register a handoff rule.

        Args:
            event_type: The domain event type (e.g., 'contas.glosa_confirmed').
            predicate: Returns True when the handoff condition is satisfied.
            target_process: CIB Seven process definition key (e.g., 'SP-OP-RECURSO-001').
            variables_fn: Maps the event payload to process start variables.
        """
        rule = _HandoffRule(
            event_type=event_type,
            predicate=predicate,
            target_process=target_process,
            variables_fn=variables_fn,
        )
        self._rules.append(rule)
        logger.debug(
            "notification_bridge.handoff_registered",
            event_type=event_type,
            target_process=target_process,
        )

    def evaluate(self, event: HandoffEvent) -> HandoffResult:
        """Evaluate whether a handoff should be triggered.

        Returns the FIRST matching rule. Use evaluate_all() to get
        all matching rules (e.g., for events that trigger multiple processes).

        Args:
            event: The domain event to evaluate.

        Returns:
            HandoffResult with evaluation outcome.
        """
        matches = self._find_matches(event)
        if matches:
            rule, variables = matches[0]
            return self._build_result(rule, variables)
        return HandoffResult(
            evaluated=True,
            handoff_triggered=False,
            reason=f"No handoff rule matched for {event.event_type}",
        )

    def evaluate_all(self, event: HandoffEvent) -> list[HandoffResult]:
        """Evaluate all matching handoff rules for an event.

        Use this when an event may trigger multiple downstream processes
        (e.g., fraude.acusacao_registrada → CRED + CANCEL + INADIMPLENCIA).

        Args:
            event: The domain event to evaluate.

        Returns:
            List of HandoffResult (at least one, with handoff_triggered=False
            if no rules matched).
        """
        matches = self._find_matches(event)
        if not matches:
            return [
                HandoffResult(
                    evaluated=True,
                    handoff_triggered=False,
                    reason=f"No handoff rule matched for {event.event_type}",
                )
            ]
        return [self._build_result(rule, variables) for rule, variables in matches]

    async def execute_handoff(self, result: HandoffResult) -> HandoffResult:
        """Execute the handoff by starting the target CIB Seven process.

        Args:
            result: The evaluated HandoffResult (must have handoff_triggered=True).

        Returns:
            The result with process_instance_id populated (or error details).
        """
        if not result.handoff_triggered:
            logger.warning(
                "notification_bridge.execute_skipped",
                reason=result.reason,
            )
            return result

        logger.info(
            "notification_bridge.execute_handoff",
            target_process=result.target_process,
            variables_keys=list(result.variables.keys()),
        )

        try:
            instance_id = await self._starter(result.target_process, result.variables)
            result.process_instance_id = str(instance_id)
            logger.info(
                "notification_bridge.handoff_complete",
                target_process=result.target_process,
                instance_id=result.process_instance_id,
            )
        except Exception as exc:
            logger.error(
                "notification_bridge.handoff_failed",
                target_process=result.target_process,
                error=str(exc),
            )
            result.reason = f"CIB Seven start failed: {exc}"

        return result

    async def on_event(self, event_type: str, payload: dict[str, Any]) -> list[HandoffResult]:
        """Full pipeline: evaluate all matching handoffs and execute them.

        This is the entry point called by Kafka consumers.
        Multiple processes may be started for a single event
        (e.g., fraude.acusacao_registrada triggers CRED + CANCEL + INADIMPLENCIA).

        Args:
            event_type: Domain event type (e.g., 'contas.glosa_confirmed').
            payload: Event payload dictionary.

        Returns:
            List of HandoffResult with evaluation and execution outcome.
        """
        event = HandoffEvent(event_type=event_type, payload=payload)
        results = self.evaluate_all(event)
        executed: list[HandoffResult] = []
        for result in results:
            if result.handoff_triggered:
                result = await self.execute_handoff(result)
            executed.append(result)
        return executed

    # -------------------------------------------------------------------
    # Introspection (for tests)
    # -------------------------------------------------------------------

    def list_handoffs(self) -> list[dict[str, str]]:
        """Return all registered handoff rules for introspection."""
        return [{"event_type": r.event_type, "target_process": r.target_process} for r in self._rules]

    def count_handoffs(self) -> int:
        """Return the number of registered handoff rules."""
        return len(self._rules)

    def get_handoff(self, event_type: str) -> list[tuple[str, Callable[[dict[str, Any]], bool]]]:
        """Return [(target_process, predicate), ...] for all rules matching event_type."""
        return [(r.target_process, r.predicate) for r in self._rules if r.event_type == event_type]

    # -------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------

    def _find_matches(self, event: HandoffEvent) -> list[tuple[_HandoffRule, dict[str, Any]]]:
        """Find all rules that match the event, resolving variables."""
        results: list[tuple[_HandoffRule, dict[str, Any]]] = []
        for r in self._rules:
            if r.event_type == event.event_type and r.predicate(event.payload):
                results.append((r, r.variables_fn(event.payload)))
        return results

    @staticmethod
    def _build_result(rule: _HandoffRule, variables: dict[str, Any]) -> HandoffResult:
        """Build a HandoffResult from a matched rule."""
        return HandoffResult(
            evaluated=True,
            handoff_triggered=True,
            target_process=rule.target_process,
            variables=variables,
            reason="Handoff condition satisfied",
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _noop_starter(process_key: str, variables: dict[str, Any]) -> str:
    """No-op starter for tests — returns a placeholder instance ID."""
    return f"instance-{process_key}-noop"


# ---------------------------------------------------------------------------
# T2.6-7 fenced starter — the ONE sanctioned way to build a production
# `cibseven_starter` for this bridge (ADR-0007/T-C2 chokepoint).
# ---------------------------------------------------------------------------


class NotificationBridgeMissingBusinessKeyError(ValueError):
    """Raised when a matched handoff rule's variables lack a `business_key`.

    Fail-closed: `start_process_idempotent` requires a deterministic business key to dedupe
    against; a rule that produced variables without one is a bridge-side bug (every rule's
    `variables_fn` in this module sets it), never something to paper over with a fabricated or
    empty key that could silently collide across unrelated cases.
    """

    def __init__(self, process_key: str) -> None:
        super().__init__(
            f"notification_bridge: missing/blank business_key for target_process={process_key!r} "
            "— refusing to start (fail-closed, never a non-deterministic engine start)"
        )


def build_cibseven_process_starter(
    transport: CibSevenTransport,
    audit_sink: AuditStartSink,
    *,
    agent_id: str = "notification_bridge",
    agent_version: str = "notification_bridge@v1",
) -> Callable[[str, dict[str, Any]], Awaitable[str]]:
    """Build a `cibseven_starter` for `NotificationBridge` that goes through the FENCED
    process-start chokepoint (`start_process_idempotent`, ADR-0007/T-C2) — never a raw,
    un-audited engine POST.

    This is the ONLY starter production code should inject into `NotificationBridge(...)`. The
    default `_noop_starter` and any ad hoc raw callable (e.g. a bare
    `transport.start_process_instance` call) are test/dev-only shortcuts that bypass the durable
    ADR-0007 provenance record `start_process_idempotent` structurally requires — exactly the
    "raw engine start" this fence exists to make impossible in production.

    Requires `variables["business_key"]` to already be populated by the matched rule's
    `variables_fn` (every rule registered by `_register_default_handoffs`, including the two
    T2.6-7 ANS-SUBMIT rules, sets it) — raises `NotificationBridgeMissingBusinessKeyError`
    (fail-closed) rather than starting with a garbage/empty key when it is missing.

    `decision_basis` carries ONLY bounded class tokens (`trigger`, `target_process`) — no
    resolvable business identifier or free text, per `AgentDecisionProvenance`'s PHI discipline;
    the chokepoint's own `redact_phi_vars`/`input_sha256` backstop applies regardless.
    """

    async def _start(process_key: str, variables: dict[str, Any]) -> str:
        business_key = variables.get("business_key")
        if not isinstance(business_key, str) or not business_key.strip():
            raise NotificationBridgeMissingBusinessKeyError(process_key)

        tenant_id = str(variables.get("tenant_id", ""))
        provenance = AgentDecisionProvenance(
            agent_id=agent_id,
            agent_version=agent_version,
            tenant_id=tenant_id,
            decision_basis={"trigger": "notification_bridge", "target_process": process_key},
        )
        instance = await start_process_idempotent(
            transport,
            process_key=process_key,
            business_key=business_key,
            variables=variables,
            audit_sink=audit_sink,
            provenance=provenance,
        )
        return instance.instance_id

    return _start
