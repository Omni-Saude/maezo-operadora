"""Recording fake for the `AuditStartSink` seam (T-C2 process-start provenance fence).

Test-only (never imported by `src/`). Mirrors the labeled-double posture of
`FakeCibSevenTransport`/`FakeDmnTransport`: a pure-Python stand-in for the durable
`PostgresAuditSink.emit_once` contract, recording calls so a test can assert the fence emitted
exactly once, with the right record + dedup key, in the right order relative to the engine start.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from maezo.gateway.audit import EmitOnceOutcome
from maezo.gateway.audit_postgres import AuditPersistenceError

if TYPE_CHECKING:
    from maezo.gateway.audit import AuditRecord


PRIOR_HASH = "ALREADY_AUDITED_PRIOR_HASH"


class FakeStartAuditSink:
    """Records every emit; satisfies BOTH `AuditStartSink` and `DedupReportingAuditSink`.

    `fail=True` makes the emit raise `AuditPersistenceError` (the durable sink's fail-closed
    contract) so a test can prove the fence gates the engine start behind a successful audit.
    `already_audited=True` pre-seeds the claim table so the FIRST emit for any key already reports
    `deduped=True` — the re-delivered-effect path.

    Real per-key CLAIM TABLE (B-3): `emit_once_status` returns `deduped=False` for the first emit
    of a `dedup_key` and `deduped=True` for every later one, exactly like
    `PostgresAuditSink.emit_once_status`. Two coroutines racing the same key therefore produce
    exactly ONE winner, which is what lets a concurrency-shaped test exercise the strict start gate
    without a database.
    """

    def __init__(self, *, fail: bool = False, already_audited: bool = False) -> None:
        self.calls: list[tuple[AuditRecord, str]] = []
        self._fail = fail
        self._already_audited = already_audited
        self._claimed: dict[str, str] = {}

    async def emit_once(self, record: AuditRecord, *, dedup_key: str) -> str:
        return (await self.emit_once_status(record, dedup_key=dedup_key)).record_hash

    async def emit_once_status(self, record: AuditRecord, *, dedup_key: str) -> EmitOnceOutcome:
        self.calls.append((record, dedup_key))
        if self._fail:
            raise AuditPersistenceError("fake durable audit failure (fail-closed test)")
        if self._already_audited:
            return EmitOnceOutcome(record_hash=PRIOR_HASH, deduped=True)
        prior = self._claimed.get(dedup_key)
        if prior is not None:
            return EmitOnceOutcome(record_hash=prior, deduped=True)
        record_hash = record.record_hash or "fake-record-hash"
        self._claimed[dedup_key] = record_hash
        return EmitOnceOutcome(record_hash=record_hash, deduped=False)

    @property
    def records(self) -> list[AuditRecord]:
        return [record for record, _ in self.calls]

    @property
    def dedup_keys(self) -> list[str]:
        return [dedup_key for _, dedup_key in self.calls]
