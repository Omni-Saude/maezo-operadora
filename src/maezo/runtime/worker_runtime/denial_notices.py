"""One owner for `operadora.auth.send_denial_notice` — the AUTH contract's worker (R-004).

WP-J1-06 once installed a second, dark owner on this topic (`NativeDenialNoticeWorker`,
switched by `MAEZO_DENIAL_NOTICE_OWNER`). It was not in the SP-OP-AUTH-001 contract
(forensic finding F-032), and the owner's decision DEC-01 (issue #1081) removed it: the
contract's `SendDenialNoticeWorker` is the single owner, and the written notice of RN 623/2024
art. 14 is delivered through that worker — manually, by the médico auditor, until a real
beneficiary channel exists (contract §SLAs, "Negativa por escrito").

What this module keeps is the exclusivity, asserted rather than assumed:

* `assert_single_owner` runs after the daemon registers its workers. Two workers on one topic
  race for the same external task and the loser's guard never runs; a topic with NO owner
  stalls every human denial at `ST_EnviarNegativaFormal`. Both are composition defects, not
  warnings, so both refuse.
* `refuse_retired_second_owner` turns the old switch into a tombstone. A deployment that still
  sets `MAEZO_DENIAL_NOTICE_OWNER=true` asked for an owner that no longer exists; ignoring that
  silently would leave it believing the custody-basis path is live. The daemon refuses to
  register its workers instead (`/readyz` stays red), which is visible and recoverable.

Neither function decides anything about a denial. The denial DECISION stays with the human
médico auditor (`hard` autonomy item, ADR-0005/ADR-0008).
"""

from __future__ import annotations

from maezo.tools.workers.auth import SEND_DENIAL_NOTICE_TOPIC as TOPIC
from maezo.tools.workers.auth import SendDenialNoticeWorker, TopicOwnershipError
from maezo.tools.workers.harness import WorkerHarness


class RetiredDenialOwnerError(RuntimeError):
    """`MAEZO_DENIAL_NOTICE_OWNER` names an owner that was removed (R-004 / F-032)."""

    def __init__(self) -> None:
        super().__init__("denial_notice_second_owner_removed")


def assert_single_owner(harness: WorkerHarness) -> None:
    """Refuse unless the contract's worker is the one and only owner of the topic."""
    if harness.registered_topics.count(TOPIC) != 1:
        raise TopicOwnershipError()
    if type(harness.registry.get(TOPIC)) is not SendDenialNoticeWorker:
        raise TopicOwnershipError()


def refuse_retired_second_owner(requested: bool) -> None:
    """Fail closed when a deployment still asks for the removed second owner."""
    if requested:
        raise RetiredDenialOwnerError()


__all__ = [
    "TOPIC",
    "RetiredDenialOwnerError",
    "TopicOwnershipError",
    "assert_single_owner",
    "refuse_retired_second_owner",
]
