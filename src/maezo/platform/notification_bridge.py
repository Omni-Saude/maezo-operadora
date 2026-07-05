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

London School TDD: the bridge is pure domain logic. Kafka consumption and
CIB Seven HTTP calls are injected as async callables so tests remain fast.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


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
            cibseven_starter: Callable(process_key, variables) that starts a
                              CIB Seven process. In production this is injected
                              from CibSevenServer.start_process. In tests it
                              can be a simple spy.
        """
        self._starter = cibseven_starter or _noop_starter
        self._rules: list[_HandoffRule] = []
        self._register_default_handoffs()

    # -------------------------------------------------------------------
    # Default handoff rules (cross-process choreography)
    # -------------------------------------------------------------------

    def _register_default_handoffs(self) -> None:
        """Register the five cross-process handoff rules from the spec.

        CONTAS→RECURSO, CONTAS→FRAUDE, FRAUDE→CRED, FRAUDE→CANCEL/INADIMPLENCIA.
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
