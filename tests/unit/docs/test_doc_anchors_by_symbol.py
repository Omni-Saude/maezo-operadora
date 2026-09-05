"""WP-DOCS-HYGIENE, gap AF-18a: documentation anchors must be SYMBOLS, not line numbers.

The finding, restated so the failure message is self-explanatory: `docs/adr/0029-*.md` cited the two
chain verifiers as `src/maezo/gateway/audit.py:310-344` and `src/maezo/gateway/audit_postgres.py:333-410`.
The mechanism never changed — the FILES moved. By 2026-09-05 `def verify_chain` sat at `audit.py:346`
and `audit_postgres.py:561`, so an auditor following the ADR's own numbers read unrelated code and
could reasonably conclude the ADR described something that no longer existed.

Re-deriving the numbers is not the fix, and this repo has the receipt:
`docs/reviews/adr-0029-erasure-packet.md` already re-derived them on 2026-08-09
(`audit.py:344-378`, `audit_postgres.py:560-637`) and those numbers had rotted again four weeks
later. The fix is structural — anchor by SYMBOL — and this fence is what keeps it fixed, in TWO
directions:

  1. **Every symbol the re-anchored prose cites still exists in the tree.** The day someone renames
     `AuditSink.verify_chain`, this test — not a reader six months later — reports it.
  2. **A line anchor may not creep back into the re-anchored passages.** Without this, the next
     editor "helpfully" re-adds `audit.py:346` and the gap silently reopens.

A repair found by the gatekeeper (F1/F2/F4, VERIFY-ADR-BATCH REVISE) widens both:

  - **A third and fourth passage were re-anchored by the same PR but were not policed** (F1: the
    `## Decisao` §3 advisory-lock sentence in ADR-0029 itself had already dropped its line numbers
    for symbol citations, but nothing stopped them creeping back; F2: two rotted `CODEOWNERS`/
    `Makefile` line anchors sat, unflagged, inside a section of `docs/adr/0041-*.md` this very PR
    edits). `_REANCHORED_PASSAGES` is now a tuple of `(path, start_marker, end_marker)` entries —
    MULTIPLE entries per path are allowed — so all four passages are policed the same way.
  - **Direction 1 was one-way** (F4): it proved every cited symbol still exists in `src/`, but never
    proved the re-anchored prose still CITES it — a citation could be silently deleted and the fence
    stayed green. `_CITED_SYMBOLS` entries are now `(source path, source text, doc citation text)`
    triples: direction 1 checks the middle element against the source file (unchanged), and the new
    `test_the_reanchored_passages_still_cite_every_symbol_they_claim_to` checks the third element
    against the UNION of every re-anchored passage.

A second repair, found by the §Delta re-verification of the F1/F2/F4 repair above (§Δ-D1): two
SIBLING files (`docs/archive/predeploy-dl-rows-STAGED.md`, `docs/reports/predeploy-audit-report.md`)
carried a dangling-pointer errata that cited a historical mention INSIDE `docs/adr/0041-*.md` BY LINE
NUMBER (`0041:589`) — and that number moved TWICE (`:598`, then `:607`) purely from this PR's own
earlier edits to that same ADR-0041 file, before the branch was even done. Re-anchored by section
name + paragraph text (`## Convencao seguida`, paragraph `**Historico honesto deste documento.**`)
and added as two more `_REANCHORED_PASSAGES` entries, each running to end-of-file (`end=None` — the
sentence is the last line of both files). `_LINE_ANCHOR_RE` now also catches the bare `NNNN:MM`
shape of an ADR self-citation, not just `*.py:NN`/`Makefile:NN`/`CODEOWNERS:NN`. No new test
function was needed: `test_no_line_anchor_creeps_back_into_the_reanchored_passages` (direction 2)
already iterates every `_REANCHORED_PASSAGES` entry, so it polices these two automatically.

Deliberately NOT policed: line anchors into applied Alembic migrations (`0002_audit_chain.py:44-50`)
and an ADR's citations of ITS OWN sections (e.g. `0029`'s `§1 :64-70`) or of a DIFFERENT ADR that is
not under active edit in this same PR. An applied migration is immutable by design, so its line
numbers are stable anchors; forcing a symbol there would lose precision, not gain it. §Δ-D1 is a
different case — a citation FROM another document INTO an ADR that this very PR is still editing —
and that instability is exactly why it needed policing.

Scope note: this module fences the AF-18a (and its F1/F2/F4/§Δ-D1 repairs) specifically. It is not a
repo-wide ban on line citations — that would be a different, much larger decision, and one for the
owner.
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).parents[3]

#: The passages this fence polices: `(path, start marker, end marker)`. `end=None` means "to end of
#: file" (see `_slice`). Slicing by CONTENT, never by line number — a fence that anchored itself by
#: line number would be the very defect it exists to prevent. A path may appear more than once
#: (ADR-0029 has two independent re-anchored passages).
_REANCHORED_PASSAGES: tuple[tuple[str, str, str | None], ...] = (
    (
        "docs/adr/0029-audit-chain-pruning-reanchor.md",
        "   - **In-memory** `src/maezo/gateway/audit.py::AuditSink.verify_chain`",
        "   - **`UNIQUE(prev_record_hash)` ≠ contiguity.**",
    ),
    # §3 "Atomic delete-then-checkpoint": the advisory-lock sentence that used to cite
    # `audit_postgres.py:137` "held ... at `:219`" (see the `## Nota de reancoragem` below). F1: this
    # passage was re-anchored by the same commit as the one above but was not policed until the
    # gatekeeper's mutation (re-inserting `audit_postgres.py:137`/`:219` here) proved it stayed green.
    (
        "docs/adr/0029-audit-chain-pruning-reanchor.md",
        "The prefix delete **and** the checkpoint insert run in **one transaction**, under the **same",
        "1. **SELECT + snapshot** the prune range",
    ),
    # Ends at the hygiene note, NOT after it: that note QUOTES the old `audit.py:310-344` anchors
    # on purpose, as the historical evidence of the drift. Policing it would demand deleting the
    # very record that explains why the re-anchoring happened. Starts at "A propriedade." (not at
    # "E o que quebra"), because the `AuditRecord._compute_hash` citation the sentence right before
    # depends on is part of the SAME re-anchor edit (F4: it was excluded by the old, narrower start
    # marker, so deleting it went undetected).
    (
        "docs/reviews/adr-0029-erasure-packet.md",
        "**A propriedade.** `record_hash` é computado sobre todos os campos persistidos, e o preimage inclui",
        "> **Nota de higiene para o revisor",
    ),
    # F2: `docs/adr/0041-*.md` §3 quoted `.github/CODEOWNERS` and `Makefile` BY LINE NUMBER and both
    # had already rotted (`:55-59`/`:60` and `:43` moved to `:58`/`:63` and `:47`) by the time this
    # branch based itself on top of the ADR-0041 commit — inside a file this very PR otherwise edits.
    # Re-anchored by quoted text; policed here so the numbers cannot creep back. Ends BEFORE the
    # "Nota de reancoragem" that follows, same reasoning as the packet's hygiene note above: that
    # note QUOTES the old rotted `:NN` numbers on purpose, as the historical record of the drift.
    (
        "docs/adr/0041-reconciliacao-adrs-0005-0006-0008-0012-0015-0024-0032.md",
        '  - `.github/CODEOWNERS` — comentario: "AUDITORIA 2026-08-13:',
        "  Nota de reancoragem 2026-09-05 (gap AF-18a/F2):",
    ),
    # §Δ-D1 (VERIFY-ADR-BATCH §Delta, REVISE — D1): the AF-02 "dangling pointer" errata in these two
    # sibling report files cited the historical `## Emenda 2026-09-03` mention inside ADR-0041 BY
    # LINE NUMBER (`0041:589`) — and this very PR's OWN edits to ADR-0041 moved that line twice
    # (`:589` -> `:598` -> `:607`) before the branch even finished. Re-anchored by section name +
    # paragraph text instead. `end=None`: the sentence is the last line of the file in both cases.
    (
        "docs/archive/predeploy-dl-rows-STAGED.md",
        "- Registro completo: `docs/adr/0041-reconciliacao-adrs-0005-0006-0008-0012-0015-0024-0032.md`,",
        None,
    ),
    (
        "docs/reports/predeploy-audit-report.md",
        "- Registro completo: `docs/adr/0041-reconciliacao-adrs-0005-0006-0008-0012-0015-0024-0032.md`,",
        None,
    ),
)

#: `(source file, symbol text that must appear verbatim in it, citation text that must appear
#: verbatim somewhere in the re-anchored passages)` — every code symbol (or, for the F2 entries,
#: quoted comment text) the re-anchored passages cite, re-derived from the tree on every run.
_CITED_SYMBOLS: tuple[tuple[str, str, str], ...] = (
    ("src/maezo/gateway/audit.py", "class AuditSink:", "AuditSink.verify_chain"),
    (
        "src/maezo/gateway/audit.py",
        "    def verify_chain(self) -> bool:",
        "AuditSink.verify_chain",
    ),
    (
        "src/maezo/gateway/audit.py",
        "    def _compute_hash(self) -> str:",
        "AuditRecord._compute_hash",
    ),
    (
        "src/maezo/gateway/audit.py",
        'GENESIS_PREV_HASH: str = "0" * 64',
        'GENESIS_PREV_HASH: str = "0" * 64',
    ),
    (
        "src/maezo/gateway/audit.py",
        "        prev: str = GENESIS_PREV_HASH",
        "prev: str = GENESIS_PREV_HASH",
    ),
    (
        "src/maezo/gateway/audit.py",
        "            if record.prev_hash != prev:",
        "if record.prev_hash != prev",
    ),
    (
        "src/maezo/gateway/audit_postgres.py",
        "async def verify_chain(dsn: str, tenant_id: str)",
        "audit_postgres.py::verify_chain",
    ),
    (
        "src/maezo/gateway/audit_postgres.py",
        "    by_prev: dict[str, asyncpg.Record] = {}",
        "by_prev",
    ),
    (
        "src/maezo/gateway/audit_postgres.py",
        "        if prev in by_prev:",
        "if prev in by_prev",
    ),
    (
        "src/maezo/gateway/audit_postgres.py",
        "    current = by_prev.get(GENESIS_PREV_HASH)",
        "by_prev.get(GENESIS_PREV_HASH)",
    ),
    (
        "src/maezo/gateway/audit_postgres.py",
        "unreachable from genesis",
        "unreachable from genesis",
    ),
    (
        "src/maezo/gateway/audit_postgres.py",
        '_ADVISORY_LOCK_SQL = "SELECT pg_advisory_xact_lock(hashtext($1))"',
        '_ADVISORY_LOCK_SQL = "SELECT pg_advisory_xact_lock(hashtext($1))"',
    ),
    (
        "src/maezo/gateway/audit_postgres.py",
        "class PostgresAuditSink:",
        "PostgresAuditSink.emit",
    ),
    (
        "src/maezo/gateway/audit_postgres.py",
        "    async def emit(self, record: AuditRecord) -> str:",
        "PostgresAuditSink.emit",
    ),
    (
        "src/maezo/gateway/audit_postgres.py",
        "    async def emit_once_status(self, record: AuditRecord, *, dedup_key: str) -> EmitOnceOutcome:",
        "PostgresAuditSink.emit_once_status",
    ),
    (
        "src/maezo/platform/migrations/versions/0002_audit_chain.py",
        "CONSTRAINT uq_audit_chain_prev_hash UNIQUE (prev_record_hash)",
        "CONSTRAINT uq_audit_chain_prev_hash",
    ),
    # F2: the two `docs/adr/0041-*.md` §3 citations, re-anchored by quoted text instead of by line
    # number. Direction 1 here proves the QUOTE is still verbatim in the target file (catching wording
    # drift, not just line drift); direction 2 proves ADR-0041 still carries the quote.
    (
        ".github/CODEOWNERS",
        "# AUDITORIA 2026-08-13: `/src/maezo/policies/` NUNCA EXISTIU (`git log --all --diff-filter=A` vazio",
        "AUDITORIA 2026-08-13: `/src/maezo/policies/` NUNCA EXISTIU",
    ),
    (
        ".github/CODEOWNERS",
        "/src/maezo/policies/  @rodaquino-OMNI",
        "/src/maezo/policies/  @rodaquino-OMNI",
    ),
    (
        "Makefile",
        "# src/maezo/processes and src/maezo/policies paths never existed (T0.4). Pointer",
        "src/maezo/processes and src/maezo/policies paths never existed (T0.4)",
    ),
)

#: `file.py:NN` / `file.py:NN-MM`, (F2) `Makefile:NN` / `CODEOWNERS:NN`, and (§Δ-D1) `NNNN:MM` — a
#: bare 4-digit ADR number followed by a line number, e.g. `0041:589` — the anchor shapes AF-18a and
#: its F2/§Δ-D1 repairs removed from these passages. `.py:` covers source/migration citations; the
#: next two cover the F2 passage (non-Python files anchored by line number); `\d{4}:\d+` covers the
#: §Δ-D1 passages, where the anchor was into an ADR's OWN prose (`0041:589`, moved twice since —
#: `:598`, then `:607` — by this very PR's edits to that same file).
_LINE_ANCHOR_RE = re.compile(
    r"[A-Za-z0-9_./-]+\.py:\d+|\bMakefile:\d+|\bCODEOWNERS:\d+|\b\d{4}:\d+(?:-\d+)?\b"
)

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


def _slice(relative: str, start: str, end: str | None) -> str:
    """`end=None` means "to end of file" — for a passage that IS the tail of the document (§Δ-D1's
    two dangling-pointer sentences each end the file they're in; there is no later marker to slice
    against, and inventing one would be more fragile than saying so)."""
    text = _read(relative)
    assert start in text, f"{relative}: re-anchored passage no longer starts with {start!r}"
    begin = text.index(start)
    if end is None:
        return text[begin:]
    assert end in text, f"{relative}: re-anchored passage no longer ends before {end!r}"
    stop = text.index(end, begin)
    assert stop > begin, f"{relative}: passage markers are out of order"
    return text[begin:stop]


def _all_passages() -> list[tuple[str, str]]:
    """`(path, sliced passage text)` for every entry in `_REANCHORED_PASSAGES`."""
    return [(relative, _slice(relative, start, end)) for relative, start, end in _REANCHORED_PASSAGES]


def test_every_symbol_the_reanchored_prose_cites_still_exists() -> None:
    """Direction 1: a symbol anchor is only better than a line anchor while it resolves."""
    missing = [
        f"{relative} :: {symbol!r}"
        for relative, symbol, _citation in _CITED_SYMBOLS
        if symbol not in _read(relative)
    ]
    assert not missing, (
        "AF-18a re-anchored ADR-0029 (and its erasure packet, and ADR-0041 §3) onto these symbols; "
        "they moved or were renamed, so the documentation now points at nothing:\n  " + "\n  ".join(missing)
    )


def test_the_reanchored_passages_still_cite_every_symbol_they_claim_to() -> None:
    """Direction 2 of the symbol check (F4): a citation may not silently vanish from the prose.

    Direction 1 proves the SOURCE symbol still exists; on its own that is one-directional — deleting
    the citation from the doc, while leaving the source untouched, stayed green. This proves the doc
    side too: every citation text in `_CITED_SYMBOLS` must appear somewhere across the union of the
    re-anchored passages themselves (not the whole file — a mention outside the policed passage does
    not count, by the same logic that keeps direction-2's line-anchor check scoped to those passages).
    """
    combined = "\n".join(text for _relative, text in _all_passages())
    missing = [
        f"{relative} :: {citation!r}"
        for relative, _symbol, citation in _CITED_SYMBOLS
        if citation not in combined
    ]
    assert not missing, (
        "a citation AF-18a's re-anchoring introduced is no longer present in any re-anchored "
        "passage — the prose stopped citing a symbol it claims to cite:\n  " + "\n  ".join(missing)
    )


def test_no_line_anchor_creeps_back_into_the_reanchored_passages() -> None:
    """Direction 2 of the line-anchor check: the repair must not be undone by the next well-meaning edit."""
    offenders: list[str] = []
    for relative, passage in _all_passages():
        for hit in _LINE_ANCHOR_RE.findall(passage):
            offenders.append(f"{relative}: {hit}")
    assert not offenders, (
        "a `file.py:NN` anchor came back into a passage AF-18a re-anchored by symbol/quoted-text — "
        "cite `file.py::Class.method` (or quote the text) instead (applied migrations and ADR "
        "sections are exempt, and are outside these passages):\n  " + "\n  ".join(offenders)
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
