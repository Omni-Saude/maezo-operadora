"""WP-DOCS-HYGIENE, gap AF-18a: documentation anchors must be SYMBOLS, not line numbers.

The finding, restated so the failure message is self-explanatory: `docs/adr/0029-*.md` cited the two
chain verifiers as `src/maezo/gateway/audit.py:310-344` and `src/maezo/gateway/audit_postgres.py:333-410`.
The mechanism never changed — the FILES moved. By 2026-09-05 `def verify_chain` sat at `audit.py:346`
and `audit_postgres.py:561`, so an auditor following the ADR's own numbers read unrelated code and
could reasonably conclude the ADR described something that no longer existed.

Re-deriving the numbers is not the fix, and this repo has the receipt: `docs/reviews/adr-0029-erasure-packet.md`
already re-derived them on 2026-08-09 (`audit.py:344-378`, `audit_postgres.py:560-637`) and those
numbers had rotted again four weeks later. The fix is structural — anchor by SYMBOL — and this fence
is what keeps it fixed, in two directions:

  1. **Every symbol the re-anchored prose cites still exists in the tree.** The day someone renames
     `AuditSink.verify_chain`, this test — not a reader six months later — reports it.
  2. **A line anchor may not creep back into the re-anchored passages.** Without this, the next
     editor "helpfully" re-adds `audit.py:346` and the gap silently reopens.

Deliberately NOT policed: line anchors into applied Alembic migrations (`0002_audit_chain.py:44-50`)
and into ADR sections. An applied migration is immutable by design, so its line numbers are stable
anchors; forcing a symbol there would lose precision, not gain it.

Scope note: this module fences the AF-18a repairs specifically. It is not a repo-wide ban on line
citations — that would be a different, much larger decision, and one for the owner.
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).parents[3]

#: The passages AF-18a re-anchored: doc -> the marker that opens the passage and the marker that
#: closes it (exclusive). Slicing by CONTENT, never by line number — a fence that anchored itself by
#: line number would be the very defect it exists to prevent.
_REANCHORED_PASSAGES: dict[str, tuple[str, str]] = {
    "docs/adr/0029-audit-chain-pruning-reanchor.md": (
        "   - **In-memory** `src/maezo/gateway/audit.py::AuditSink.verify_chain`",
        "   - **`UNIQUE(prev_record_hash)` ≠ contiguity.**",
    ),
    # Ends at the hygiene note, NOT after it: that note QUOTES the old `audit.py:310-344` anchors
    # on purpose, as the historical evidence of the drift. Policing it would demand deleting the
    # very record that explains why the re-anchoring happened.
    "docs/reviews/adr-0029-erasure-packet.md": (
        "E o que quebra, quebra para os dois verificadores:",
        "> **Nota de higiene para o revisor",
    ),
}

#: `(source file, symbol text that must appear verbatim in it)` — every code symbol the re-anchored
#: passages now cite, re-derived from the tree on every run.
_CITED_SYMBOLS: tuple[tuple[str, str], ...] = (
    ("src/maezo/gateway/audit.py", "class AuditSink:"),
    ("src/maezo/gateway/audit.py", "    def verify_chain(self) -> bool:"),
    ("src/maezo/gateway/audit.py", '    def _compute_hash(self) -> str:'),
    ("src/maezo/gateway/audit.py", 'GENESIS_PREV_HASH: str = "0" * 64'),
    ("src/maezo/gateway/audit.py", "        prev: str = GENESIS_PREV_HASH"),
    ("src/maezo/gateway/audit.py", "            if record.prev_hash != prev:"),
    ("src/maezo/gateway/audit_postgres.py", "async def verify_chain(dsn: str, tenant_id: str)"),
    ("src/maezo/gateway/audit_postgres.py", "    by_prev: dict[str, asyncpg.Record] = {}"),
    ("src/maezo/gateway/audit_postgres.py", "        if prev in by_prev:"),
    ("src/maezo/gateway/audit_postgres.py", "    current = by_prev.get(GENESIS_PREV_HASH)"),
    ("src/maezo/gateway/audit_postgres.py", "unreachable from genesis"),
    ("src/maezo/gateway/audit_postgres.py", '_ADVISORY_LOCK_SQL = "SELECT pg_advisory_xact_lock(hashtext($1))"'),
    ("src/maezo/gateway/audit_postgres.py", "class PostgresAuditSink:"),
    ("src/maezo/gateway/audit_postgres.py", "    async def emit(self, record: AuditRecord) -> str:"),
    (
        "src/maezo/gateway/audit_postgres.py",
        "    async def emit_once_status(self, record: AuditRecord, *, dedup_key: str) -> EmitOnceOutcome:",
    ),
    (
        "src/maezo/platform/migrations/versions/0002_audit_chain.py",
        "CONSTRAINT uq_audit_chain_prev_hash UNIQUE (prev_record_hash)",
    ),
)

#: `file.py:NN` / `file.py:NN-MM` — the anchor shape AF-18a removed from these passages.
_LINE_ANCHOR_RE = re.compile(r"[A-Za-z0-9_./-]+\.py:\d+")

#: The second half of AF-18a: migration `0006`'s docstring claimed the checkpointer was wired
#: nowhere. It is wired in two fail-closed bring-ups. `(module, symbol)` proving each.
_CHECKPOINTER_BRINGUP_SYMBOLS: tuple[tuple[str, str], ...] = (
    ("src/maezo/runtime/agent_runtime/service.py", "checkpointer: Checkpointer | None = None"),
    ("src/maezo/runtime/agent_runtime/service.py", "    async def checkpointer_ready("),
    ("src/maezo/platform/webhooks/service.py", "async def _provision_dispatch_checkpointer("),
    ("src/maezo/runtime/checkpoint.py", "async def provision_checkpointer("),
    ("src/maezo/runtime/checkpoint.py", "            await saver.setup()"),
)

_MIGRATION_0006 = "src/maezo/platform/migrations/versions/0006_retire_dead_checkpoint_tables.py"


def _read(relative: str) -> str:
    path = _REPO_ROOT / relative
    assert path.is_file(), f"expected {relative} to exist"
    return path.read_text(encoding="utf-8")


def _passage(relative: str) -> str:
    start, end = _REANCHORED_PASSAGES[relative]
    text = _read(relative)
    assert start in text, f"{relative}: re-anchored passage no longer starts with {start!r}"
    assert end in text, f"{relative}: re-anchored passage no longer ends before {end!r}"
    begin = text.index(start)
    stop = text.index(end, begin)
    assert stop > begin, f"{relative}: passage markers are out of order"
    return text[begin:stop]


def test_every_symbol_the_reanchored_prose_cites_still_exists() -> None:
    """Direction 1: a symbol anchor is only better than a line anchor while it resolves."""
    missing = [
        f"{relative} :: {symbol!r}" for relative, symbol in _CITED_SYMBOLS if symbol not in _read(relative)
    ]
    assert not missing, (
        "AF-18a re-anchored ADR-0029 (and its erasure packet) onto these symbols; they moved or "
        "were renamed, so the documentation now points at nothing:\n  " + "\n  ".join(missing)
    )


def test_no_line_anchor_creeps_back_into_the_reanchored_passages() -> None:
    """Direction 2: the repair must not be undone by the next well-meaning edit."""
    offenders: list[str] = []
    for relative in _REANCHORED_PASSAGES:
        for hit in _LINE_ANCHOR_RE.findall(_passage(relative)):
            offenders.append(f"{relative}: {hit}")
    assert not offenders, (
        "a `file.py:NN` anchor came back into a passage AF-18a re-anchored by symbol — cite "
        "`file.py::Class.method` instead (applied migrations and ADR sections are exempt, and are "
        "outside these passages):\n  " + "\n  ".join(offenders)
    )


def test_migration_0006_no_longer_leaves_its_stale_claim_uncorrected() -> None:
    """AF-18a's second example: the docstring said the checkpointer was wired nowhere.

    Append-only, exactly as that file's own rule demands: the original paragraph STAYS (deleting it
    would rewrite a historical record and move the `:25-35` anchors the audit cites), and a dated
    reconciliation note follows it. Both halves are asserted, so neither can be dropped.
    """
    text = _read(_MIGRATION_0006)
    assert "wired nowhere in prod bootstrap" in text, (
        f"{_MIGRATION_0006}: the original T3.4 claim was deleted — a migration is an append-only "
        "historical record; correct it with a note, never by rewriting it"
    )
    assert "NOTA DE RECONCILIACAO 2026-09-05 (gap AF-18a" in text, (
        f"{_MIGRATION_0006}: the stale claim carries no reconciliation note"
    )


def test_the_checkpointer_really_is_wired_in_two_fail_closed_bringups() -> None:
    """The reconciliation note is only honest while its facts hold — re-derive them every run."""
    missing = [
        f"{relative} :: {symbol!r}"
        for relative, symbol in _CHECKPOINTER_BRINGUP_SYMBOLS
        if symbol not in _read(relative)
    ]
    assert not missing, (
        "migration 0006's reconciliation note claims the durable checkpointer is provisioned in "
        "two fail-closed bring-ups; these anchors no longer resolve, so the note has gone stale "
        "in its turn:\n  " + "\n  ".join(missing)
    )
