"""Structural tests for migration `0010_webhook_wamid_dedup` — the schema half of the repurpose
owner decision R-073 ordered ("REPROPOR `driver_idempotency` ... na mesma migracao que declara o
novo uso").

No Postgres required. The live application of this DDL is proven by
`tests/integration/platform/test_wamid_dedup_live_pg.py`, which runs `alembic upgrade head`
against a real database and then exercises the claim protocol against the columns created here.

Four invariants:

1.  **Chain integrity.** 0010 is the sole head of a linear chain (0007->0008->0009->0010) and no
    revision id is claimed twice. This assertion travels WITH the head: it moved from
    `test_migration_0008_a2a_fact_outbox.py` to `test_migration_0009_drop_pgvector.py` when 0009
    landed and to here when 0010 landed, exactly as the 0008 suite's own docstring prescribes. It
    is also the test that would have caught the fork this branch briefly carried, when 0010 still
    revised 0008 while `0009_drop_pgvector` was open in another PR.
2.  **The repurpose is DECLARED, not merely implied.** The migration names the gap, the owner
    decision and the new writers in SQL `COMMENT ON` statements — the only form of declaration a
    DBA inspecting the database can ever see.
3.  **The claim/commit protocol has the columns and indexes it needs**, and the status vocabulary
    in the DDL equals the one `maezo.platform.driver_idempotency` writes, in both directions.
4.  **`downgrade()` is honest and bounded**: it removes exactly what `upgrade()` added and never
    drops the table, the expiry index or anything belonging to 0003.
5.  **No statement carries an accidental bind parameter.** Alembic wraps every `op.execute(...)`
    string in `sqlalchemy.text()`, which reads a bare `:identifier` as a BIND PARAMETER. The
    original `COMMENT ON COLUMN ... 'wa:{leg}:{tenant}:hk1_{hmac}'` therefore compiled to a
    statement with an unbound `hk1_` parameter and `alembic upgrade head` died with a
    `StatementError` before Postgres ever saw the DDL — a defect the structural fences above could
    not see and only the live run surfaced. This one compiles the REAL runtime statements.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from typing import Any
from unittest.mock import patch

from sqlalchemy import text

from maezo.platform.driver_idempotency import (
    DEDUP_MIGRATION_REVISION,
    DEDUP_STATUSES,
    DEDUP_TABLE,
)

_VERSIONS_DIR = Path(__file__).resolve().parents[3] / "src/maezo/platform/migrations/versions"
_MIGRATION_PATH = _VERSIONS_DIR / "0010_webhook_wamid_dedup.py"
_SOURCE = _MIGRATION_PATH.read_text(encoding="utf-8")


def _emitted_ddl() -> str:
    """Only the SQL `upgrade()`/`downgrade()` execute — never the prose docstring.

    Same helper 0007/0008's suites use, and for the same reason: the docstring deliberately names
    things (the rejected `DROP`, `a2a_idempotency`) whose presence in prose is the record of a
    decision, so a whole-file scan would make the fences below satisfiable only by deleting the
    rationale.
    """
    ddl = "".join(re.findall(r'op\.execute\("""(.*?)"""\)', _SOURCE, re.DOTALL))
    ddl += "".join(re.findall(r'op\.execute\("(.*?)"\)', _SOURCE))
    return ddl


def _upgrade_ddl() -> str:
    body = _SOURCE.split("def upgrade() -> None:", 1)[1].split("def downgrade() -> None:", 1)[0]
    ddl = "".join(re.findall(r'op\.execute\("""(.*?)"""\)', body, re.DOTALL))
    ddl += "".join(re.findall(r'op\.execute\("(.*?)"\)', body))
    return ddl


def _downgrade_ddl() -> str:
    body = _SOURCE.split("def downgrade() -> None:", 1)[1]
    ddl = "".join(re.findall(r'op\.execute\("""(.*?)"""\)', body, re.DOTALL))
    ddl += "".join(re.findall(r'op\.execute\("(.*?)"\)', body))
    return ddl


def test_the_ddl_extractor_is_not_vacuous() -> None:
    """Negative control: the docstring DOES contain words no DDL statement below contains, so the
    extractor is doing real work rather than the fences passing over an empty string."""
    assert "InboundDriver" in _SOURCE, "the rationale names the drivers that were never built"
    assert "InboundDriver" not in _emitted_ddl()
    assert _emitted_ddl().strip(), "the extractor must actually find the statements"


# ---------------------------------------------------------------------------
# 1. Chain integrity
# ---------------------------------------------------------------------------


def test_migration_file_is_named_for_its_revision() -> None:
    assert _MIGRATION_PATH.name.startswith(f"{DEDUP_MIGRATION_REVISION}_")


def test_revision_and_down_revision() -> None:
    assert f'revision: str = "{DEDUP_MIGRATION_REVISION}"' in _SOURCE
    assert 'down_revision: str | None = "0009"' in _SOURCE


def test_the_docstring_header_agrees_with_down_revision() -> None:
    """VER-A2-WEBHOOK MINOR-1. Alembic reads `down_revision`; a human reads the `Revises:` line of
    the header. This branch re-pointed the first from 0008 to 0009 when `0009_drop_pgvector` landed
    and left the second saying 0008 — a header that lies about the chain to every reader who does
    not scroll to the identifiers. Pinned in BOTH directions so the pair can never drift again."""
    header = re.search(r"^Revises: (\S+)$", _SOURCE, re.MULTILINE)
    declared = re.search(r'^down_revision: str \| None = "([^"]+)"', _SOURCE, re.MULTILINE)
    assert header is not None, "the migration header declares no `Revises:` line"
    assert declared is not None
    assert header.group(1) == declared.group(1), (
        f"header says Revises: {header.group(1)}, down_revision says {declared.group(1)}"
    )


def test_0011_is_the_unique_head_of_a_linear_chain() -> None:
    """No fork: every revision claimed once, exactly one revision unreferenced as a parent.

    A forked chain is the failure mode where `alembic upgrade head` applies one branch while an
    operator believes it applied the other. Inherited from 0009's suite when 0010 landed (and by
    0009 from 0008's before that); the next migration moves it again — ONE file changes.
    """
    revisions: dict[str, str | None] = {}
    for path in sorted(_VERSIONS_DIR.glob("[0-9]*.py")):
        text = path.read_text(encoding="utf-8")
        rev = re.search(r'^revision: str = "([^"]+)"', text, re.MULTILINE)
        down = re.search(r'^down_revision: str \| None = (?:"([^"]+)"|None)', text, re.MULTILINE)
        assert rev is not None, f"{path.name} declares no revision"
        assert down is not None, f"{path.name} declares no down_revision"
        assert rev.group(1) not in revisions, f"duplicate revision id {rev.group(1)}"
        revisions[rev.group(1)] = down.group(1)

    parents = {down for down in revisions.values() if down is not None}
    heads = set(revisions) - parents
    assert heads == {"0011"}, f"expected 0011 to be the sole head, got {heads}"
    assert len(parents) == len(revisions) - 1, "a revision is claimed as parent by two children"


# ---------------------------------------------------------------------------
# 2. The repurpose is declared IN THE SCHEMA
# ---------------------------------------------------------------------------


def test_the_table_comment_declares_the_new_use_and_names_its_writers() -> None:
    """R-073's actual ask. A repurpose that lived only in Python would leave the database saying
    nothing about what these rows are, which is how the table became an orphan in the first place.
    """
    ddl = _upgrade_ddl()
    assert f"COMMENT ON TABLE {DEDUP_TABLE} IS" in ddl
    assert "WEBHOOK-WAMID-DEDUP" in ddl
    assert "PostgresDriverIdempotencyRegistry" in ddl


def test_the_key_column_comment_states_the_pseudonymization_rule() -> None:
    """The one property that keeps the LGPD inventory's `SEM_COLUNA_DE_TITULAR` true."""
    ddl = _upgrade_ddl()
    assert f"COMMENT ON COLUMN {DEDUP_TABLE}.key IS" in ddl
    assert "hk1_" in ddl


# ---------------------------------------------------------------------------
# 3. The protocol's columns and indexes
# ---------------------------------------------------------------------------


def test_the_status_column_is_added_idempotently() -> None:
    assert "ADD COLUMN IF NOT EXISTS status text NOT NULL DEFAULT 'processed'" in _upgrade_ddl()


def test_the_status_default_is_terminal_not_in_flight() -> None:
    """A pre-existing row defaulting to `pending` would be treated as an abandoned in-flight claim
    and re-claimed by the first delivery that hashed to it."""
    match = re.search(r"ADD COLUMN IF NOT EXISTS status text NOT NULL DEFAULT '(\w+)'", _upgrade_ddl())
    assert match is not None
    assert match.group(1) == "processed"


def test_the_check_constraint_vocabulary_equals_the_module_status_set() -> None:
    """Both directions: a status the code writes and the CHECK rejects is a row Postgres refuses
    in the middle of a webhook; one the CHECK admits and the code never writes is dead vocabulary.
    """
    match = re.search(r"CHECK \(status IN \(([^)]+)\)\)", _upgrade_ddl())
    assert match is not None
    in_ddl = {token.strip().strip("'") for token in match.group(1).split(",")}
    assert in_ddl == set(DEDUP_STATUSES)


def test_the_constraint_is_added_under_an_existence_guard() -> None:
    """Postgres has no `ADD CONSTRAINT IF NOT EXISTS`; without the guard, re-running the migration
    on an upgraded schema raises `duplicate_object` instead of being a no-op."""
    ddl = _upgrade_ddl()
    assert "pg_constraint" in ddl
    assert "ck_driver_idempotency_status" in ddl


def test_the_pending_scan_index_is_partial_and_not_unique() -> None:
    """The re-drive / ack-then-queue scan index. UNIQUE here would be a redundant second primary
    key; a non-partial one would index every settled row forever."""
    ddl = _upgrade_ddl()
    assert "CREATE INDEX IF NOT EXISTS ix_driver_idempotency_pending" in ddl
    assert "WHERE status = 'pending'" in ddl
    assert "CREATE UNIQUE INDEX" not in ddl


def test_the_expiry_index_is_asserted_by_this_migration_too() -> None:
    """The TTL sweep depends on it; 0003 created it for a different feature, so 0010 states the
    dependency instead of inheriting it silently (`IF NOT EXISTS` makes it a no-op)."""
    assert "CREATE INDEX IF NOT EXISTS ix_driver_idempotency_expires" in _upgrade_ddl()


def test_the_migration_creates_no_table_at_all() -> None:
    """Option 2 was REPROPOR, not "build a second dedup table" — the whole point of R-073."""
    assert "CREATE TABLE" not in _emitted_ddl()


def test_the_migration_never_touches_the_sibling_a2a_table() -> None:
    """`a2a_idempotency` shares migration 0003 and NOTHING else: a different protocol, a different
    owner, and a live one. Asserted on STATEMENT TARGETS rather than on the word, because the
    table comment legitimately cites `0003_a2a_idempotency.py` as this table's provenance."""
    targets = re.findall(
        r"(?:ALTER TABLE|DROP TABLE(?: IF EXISTS)?|CREATE TABLE(?: IF NOT EXISTS)?|ON)\s+([a-z_]+)",
        _emitted_ddl(),
    )
    assert targets, "non-vacuity: the DDL must name at least one table"
    assert set(targets) == {DEDUP_TABLE}, f"unexpected statement targets: {sorted(set(targets))}"


# ---------------------------------------------------------------------------
# 4. Downgrade honesty
# ---------------------------------------------------------------------------


def test_downgrade_removes_exactly_what_upgrade_added() -> None:
    ddl = _downgrade_ddl()
    assert "DROP INDEX IF EXISTS ix_driver_idempotency_pending" in ddl
    assert "DROP CONSTRAINT IF EXISTS ck_driver_idempotency_status" in ddl
    assert "DROP COLUMN IF EXISTS status" in ddl


def test_downgrade_never_drops_the_table_or_the_expiry_index() -> None:
    """Both belong to 0003. A downgrade that dropped them would delete another migration's schema
    — and, for the table, the entire dedup history."""
    ddl = _downgrade_ddl()
    assert "DROP TABLE" not in ddl
    assert "DROP INDEX IF EXISTS ix_driver_idempotency_expires" not in ddl


# ---------------------------------------------------------------------------
# 5. No accidental bind parameters (the defect the live run found)
# ---------------------------------------------------------------------------


def _runtime_statements() -> list[str]:
    """The strings `upgrade()`/`downgrade()` actually hand to alembic.

    Captured by importing the migration and intercepting `op.execute`, NOT by regex over the
    source: the source carries Python-level escaping (`\\:`), and reading it textually would test
    a different string than the one alembic compiles.
    """
    spec = importlib.util.spec_from_file_location("_migration_0010", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    captured: list[str] = []

    def _capture(sql: Any) -> None:
        captured.append(str(sql))

    with patch.object(module.op, "execute", _capture):
        module.upgrade()
        module.downgrade()
    return captured


def test_no_statement_carries_an_accidental_bind_parameter() -> None:
    """`:hk1_` inside a comment literal is a BIND PARAMETER to `sqlalchemy.text()`, and alembic
    wraps every `op.execute` string in exactly that. The first live `alembic upgrade head` raised
    `StatementError: A value is required for bind parameter 'hk1_'` — the migration never reached
    Postgres. Escaping the colon (`\\:`) is the fix; this is the fence."""
    statements = _runtime_statements()
    assert len(statements) >= 10, "non-vacuity: upgrade+downgrade must have been captured"
    offenders = {
        statement.strip()[:60]: text(statement).compile().params
        for statement in statements
        if text(statement).compile().params
    }
    assert offenders == {}, f"statements with unbound parameters: {offenders}"


def test_the_key_comment_still_reaches_postgres_with_its_real_colons() -> None:
    """The escape must not change what the DBA reads: the compiled SQL carries the key format
    verbatim, colons and all."""
    key_comment = next(
        statement
        for statement in _runtime_statements()
        if "COMMENT ON COLUMN driver_idempotency.key" in statement
    )
    compiled = str(text(key_comment).compile())
    assert "wa:{leg}:{tenant}:hk1_{hmac}" in compiled
    assert "\\" not in compiled
