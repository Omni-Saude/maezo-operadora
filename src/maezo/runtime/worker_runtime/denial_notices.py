"""Dedicated host for the `auth_denial_record` consumer of SP-OP-AUTH-001 (WP-J1-06).

One topic, one owner. `operadora.auth.send_denial_notice` is served either by the
generic `SendDenialNoticeWorker` (which reads the ANS grounding from process
variables) or by this host's producer (which reads the human-decision basis the
portal's decision plane actually carries) — never by both, because two workers on one
topic race for the same external task and the loser's guard never runs.

The exclusivity is asserted, not assumed: `assert_exclusive` must pass at
installation and again after any re-registration on the harness. `register_auth_workers`
takes the same seam and simply does not register the generic worker when this host
owns the topic, so a deployment cannot end up with both by forgetting a step.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from maezo.gateway.denial_notices.models import (
    DenialIncompleteError,
    DenialNotHumanError,
)
from maezo.gateway.denial_notices.producer import TOPIC, DenialNoticeProducer
from maezo.tools.workers.base import ERR_AUTH_DENIAL_INCOMPLETE, ERR_DENIAL_NOT_HUMAN, WorkerBase
from maezo.tools.workers.harness import WorkerBpmnError
from maezo.tools.workers.phi_vars import redact_phi_vars


class TopicOwnershipError(RuntimeError):
    """Two owners for one external-task topic is a composition defect, not a warning."""

    def __init__(self) -> None:
        super().__init__("denial_notice_topic_not_exclusive")


class NativeDenialNoticeWorker(WorkerBase):
    """External task: `operadora.auth.send_denial_notice`, portal-decision variant.

    Serves `ST_EnviarNegativaFormal` exactly like the generic worker — same topic,
    same boundary, same error codes — but grounds its completeness guard in the human
    decision basis (custody reference + digests + the projected principal) instead of
    clinical strings that ADR-0006 keeps out of engine variables in the first place.

    Like its sibling it TRANSMITS NOTHING: it composes the formal record and returns
    it. The AUTH contract places the real secure channel in Fase 1, and no key emitted
    here claims a delivery happened.
    """

    def __init__(self, producer: DenialNoticeProducer) -> None:
        # max_retries=1 for the same reason the generic worker uses it: these guards
        # are deterministic, not transient, and the engine owns durable retry. An
        # in-process retry would only delay the modeled bpmnError.
        super().__init__(topic=TOPIC, max_retries=1)
        self._producer = producer

    def execute(self, process_vars: dict[str, Any]) -> dict[str, Any]:
        """Compose the formal denial, or refuse with the code the BPMN models.

        Raises:
            WorkerBpmnError: `ERR_AUTH_DENIAL_INCOMPLETE` when no complete human
                decision basis backs this denial — caught by `BE_NegativaIncompleta`
                and ended, neutrally, at `End_FundamentacaoIncompletaBloqueada`.
        """
        tenant_id = process_vars.get("tenant_id", "")
        decisao = process_vars.get("decisao_auditor", "")
        if decisao != "NEGAR":
            # Defensive, exactly as in the generic worker: `ST_EnviarNegativaFormal`
            # has one inbound flow and it is `${decisao_auditor == 'NEGAR'}`.
            self.logger.info("auth_approval_notice_composed", tenant_id=tenant_id)
            return {
                "notice_type": "approval",
                "error_code": None,
                "event": "agents.events.auth.completed",
            }
        try:
            record = self._producer.compose(process_vars)
        except DenialIncompleteError as incomplete:
            # Names only, never values: a reference is not PHI but the habit is.
            self.logger.error(
                "auth_denial_blocked_incomplete",
                tenant_id=tenant_id,
                missing_fields=list(incomplete.missing),
                reason=(
                    "negativa formal exige fundamentacao completa em custodia PHI (RN 395 art. 10, L0 hard)"
                ),
            )
            raise WorkerBpmnError(
                ERR_AUTH_DENIAL_INCOMPLETE,
                "send_denial_notice: decisao humana sem base completa em custodia, campos "
                f"ausentes: {list(incomplete.missing)} — negativa formal NAO transmitida "
                "(RN 395 art. 10, L0 hard ADR-0005).",
            ) from None
        except DenialNotHumanError:
            self.logger.error(
                "auth_denial_blocked_by_guard",
                tenant_id=tenant_id,
                reason="NEGAR sem responsabilidade humana correspondente (L0 hard, ADR-0007)",
            )
            return {
                "status": "blocked_by_guard",
                "error_code": ERR_DENIAL_NOT_HUMAN,
                "mensagem": "Negativa automatica PROIBIDA. Requer decisao de medico auditor humano.",
            }
        self.logger.info(
            "auth_denial_notice_composed",
            tenant_id=tenant_id,
            notice_composed_asserted_transmission=False,
        )
        # The record holds no clinical field by construction; the ADR-0006 egress point
        # stays on the path anyway, so a future key cannot skip it by being added here.
        return redact_phi_vars(record.variables())


class DenialNoticeHost:
    """Installs the portal-decision denial owner and keeps the topic exclusive."""

    topic = TOPIC

    def __init__(self, *, producer: DenialNoticeProducer, generic_topics: Iterable[str] = ()) -> None:
        if type(producer) is not DenialNoticeProducer:
            raise TopicOwnershipError()
        self.assert_exclusive(generic_topics)
        self._producer = producer

    @staticmethod
    def assert_exclusive(generic_topics: Iterable[str]) -> None:
        """Run at installation and after any daemon worker re-registration."""
        if TOPIC in tuple(generic_topics):
            raise TopicOwnershipError()

    def worker(self) -> NativeDenialNoticeWorker:
        return NativeDenialNoticeWorker(self._producer)


def install_denial_notice_host(generic_topics: Iterable[str] = ()) -> DenialNoticeHost:
    """The one construction path: a real producer, and a topic nobody else holds."""
    return DenialNoticeHost(producer=DenialNoticeProducer(), generic_topics=generic_topics)


__all__ = [
    "TOPIC",
    "DenialNoticeHost",
    "NativeDenialNoticeWorker",
    "TopicOwnershipError",
    "install_denial_notice_host",
]
