"""Recording fake for the `AuditStartSink` seam (T-C2 process-start provenance fence).

Test-only (never imported by `src/`). Mirrors the labeled-double posture of
`FakeCibSevenTransport`/`FakeDmnTransport`: a pure-Python stand-in for the durable
`PostgresAuditSink.emit_once` contract, recording calls so a test can assert the fence emitted
exactly once, with the right record + dedup key, in the right order relative to the engine start.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from maezo.gateway.audit_postgres import AuditPersistenceError

if TYPE_CHECKING:
    from maezo.gateway.audit import AuditRecord


class FakeStartAuditSink:
    """Records every `emit_once` call; satisfies the `AuditStartSink` Protocol structurally.

    `fail=True` makes `emit_once` raise `AuditPersistenceError` (the durable sink's fail-closed
    contract) so a test can prove the fence gates the engine start behind a successful audit.
    `already_audited` simulates the dedup no-op path (a re-delivered effect): `emit_once` returns
    a fixed prior hash and records the call without appending a "new link".
    """

    def __init__(self, *, fail: bool = False, already_audited: bool = False) -> None:
        self.calls: list[tuple[AuditRecord, str]] = []
        self._fail = fail
        self._already_audited = already_audited

    async def emit_once(self, record: AuditRecord, *, dedup_key: str) -> str:
        self.calls.append((record, dedup_key))
        if self._fail:
            raise AuditPersistenceError("fake durable audit failure (fail-closed test)")
        if self._already_audited:
            return "ALREADY_AUDITED_PRIOR_HASH"
        return record.record_hash or "fake-record-hash"

    @property
    def records(self) -> list[AuditRecord]:
        return [record for record, _ in self.calls]

    @property
    def dedup_keys(self) -> list[str]:
        return [dedup_key for _, dedup_key in self.calls]
