"""Exact technical cursor map from journey-orchestration-contract.json; no business rules."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Final


@dataclass(frozen=True, slots=True)
class JourneyStage:
    cursor_ref: str
    operations: frozenset[str]
    successor_cursor_refs: frozenset[str]


STAGES_BY_TASK: Final = MappingProxyType(
    {
        "journey.compras.step": MappingProxyType(
            {
                "C1_need": JourneyStage(
                    "C1_need", frozenset(["access.resolve"]), frozenset(["C1_options", "C_recovery"])
                ),
                "C1_options": JourneyStage(
                    "C1_options", frozenset(["offer.compose"]), frozenset(["C1_present", "C_recovery"])
                ),
                "C1_present": JourneyStage(
                    "C1_present",
                    frozenset(["notice.prepare_or_send", "milestone.publish"]),
                    frozenset(["C2_wait_manifestation", "C_recovery"]),
                ),
                "C2_wait_manifestation": JourneyStage(
                    "C2_wait_manifestation",
                    frozenset(["external_wait.settle"]),
                    frozenset(["C2_acceptance", "C_recovery"]),
                ),
                "C2_acceptance": JourneyStage(
                    "C2_acceptance",
                    frozenset(["acceptance.record"]),
                    frozenset(["C3_route", "C_terminal", "C_recovery"]),
                ),
                "C3_route": JourneyStage(
                    "C3_route",
                    frozenset([]),
                    frozenset(["C3_enrollment", "C3_reservation", "C3_existing_AUTH", "C_recovery"]),
                ),
                "C3_enrollment": JourneyStage(
                    "C3_enrollment",
                    frozenset(["enrollment.request"]),
                    frozenset(["C3_wait", "C3_reservation", "C4_fulfillment", "C_recovery"]),
                ),
                "C3_reservation": JourneyStage(
                    "C3_reservation",
                    frozenset(["reservation.command"]),
                    frozenset(["C3_wait", "C4_fulfillment", "C4_maintenance", "C_recovery"]),
                ),
                "C3_existing_AUTH": JourneyStage(
                    "C3_existing_AUTH", frozenset([]), frozenset(["C3_wait", "C4_fulfillment", "C_recovery"])
                ),
                "C3_wait": JourneyStage(
                    "C3_wait",
                    frozenset(["external_wait.settle"]),
                    frozenset(
                        [
                            "C3_enrollment",
                            "C3_reservation",
                            "C3_existing_AUTH",
                            "C4_fulfillment",
                            "C_recovery",
                        ]
                    ),
                ),
                "C4_fulfillment": JourneyStage(
                    "C4_fulfillment",
                    frozenset(["fulfillment.observe"]),
                    frozenset(["C3_wait", "C4_maintenance", "C_terminal", "C_recovery"]),
                ),
                "C4_maintenance": JourneyStage(
                    "C4_maintenance",
                    frozenset(["reservation.command", "case.open_or_update", "external_wait.settle"]),
                    frozenset(["C3_wait", "C4_fulfillment", "C_recovery"]),
                ),
                "C_recovery": JourneyStage(
                    "C_recovery",
                    frozenset(["case.open_or_update", "external_wait.settle", "notice.prepare_or_send"]),
                    frozenset(["C1_options", "C3_wait", "C4_maintenance", "C_terminal", "C_to_support"]),
                ),
                "C_to_support": JourneyStage("C_to_support", frozenset([]), frozenset(["C_recovery"])),
                "C_terminal": JourneyStage(
                    "C_terminal", frozenset(["notice.prepare_or_send", "milestone.publish"]), frozenset([])
                ),
            }
        ),
        "journey.suporte.step": MappingProxyType(
            {
                "S1_case": JourneyStage(
                    "S1_case",
                    frozenset(["access.resolve", "case.open_or_update"]),
                    frozenset(["S1_ack", "S_recovery"]),
                ),
                "S1_ack": JourneyStage(
                    "S1_ack",
                    frozenset(["notice.prepare_or_send", "milestone.publish"]),
                    frozenset(["S2_route", "S_recovery"]),
                ),
                "S2_route": JourneyStage(
                    "S2_route",
                    frozenset(["case.open_or_update"]),
                    frozenset(["S3_resolve_or_handoff", "S_to_compras", "S_recovery"]),
                ),
                "S3_resolve_or_handoff": JourneyStage(
                    "S3_resolve_or_handoff",
                    frozenset([]),
                    frozenset(["S3_wait", "S4_current_confirmation", "S_recovery"]),
                ),
                "S3_wait": JourneyStage(
                    "S3_wait",
                    frozenset(["external_wait.settle", "notice.prepare_or_send"]),
                    frozenset(["S3_resume", "S_recovery"]),
                ),
                "S3_resume": JourneyStage(
                    "S3_resume",
                    frozenset(["external_wait.settle", "case.open_or_update"]),
                    frozenset(["S3_wait", "S4_current_confirmation", "S_recovery"]),
                ),
                "S4_current_confirmation": JourneyStage(
                    "S4_current_confirmation",
                    frozenset([]),
                    frozenset(["S4_record_confirmation", "S3_wait", "S_recovery"]),
                ),
                "S4_record_confirmation": JourneyStage(
                    "S4_record_confirmation",
                    frozenset(["case.open_or_update"]),
                    frozenset(["S_terminal", "S3_wait", "S_recovery"]),
                ),
                "S_to_compras": JourneyStage("S_to_compras", frozenset([]), frozenset(["S3_wait"])),
                "S_recovery": JourneyStage(
                    "S_recovery",
                    frozenset(["case.open_or_update", "external_wait.settle", "notice.prepare_or_send"]),
                    frozenset(["S2_route", "S3_wait", "S4_current_confirmation", "S_terminal"]),
                ),
                "S_terminal": JourneyStage(
                    "S_terminal", frozenset(["notice.prepare_or_send", "milestone.publish"]), frozenset([])
                ),
                "S_optional_feedback": JourneyStage(
                    "S_optional_feedback", frozenset(["feedback.record"]), frozenset([])
                ),
            }
        ),
    }
)
