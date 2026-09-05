"""Structural tests for migration `0008_a2a_fact_outbox` — the DDL half of the A2A fact outbox.

No Postgres required. These are the ANTI-REGRESSION half of the outbox: the live round-trip proof
lives in `tests/unit/a2a/test_outbox_live_pg.py` (which executes THIS file's SQL, not a copy of
it), and what is asserted here is the set of things that must stay true about the DDL so that a
later edit which quietly breaks one fails CI rather than production.

Five invariants:

1.  **Chain integrity.** 0008 revises 0007 and is claimed as parent by exactly one child. It is no
    longer the head: the chain is now 0007->0008->0009->0010, and the sole-head / no-fork assertion
    travels WITH the head — it moved from here to `test_migration_0009_drop_pgvector.py` when 0009
    landed and from there to `test_migration_0010_webhook_wamid_dedup.py` when 0010 landed, exactly
    as this file's own instruction prescribes ("the head belongs to whichever migration currently
    IS the head"). A forked chain is the failure mode where `alembic upgrade head` applies one
    branch and an operator believes it applied the other.
2.  **The DDL and `maezo.a2a.outbox` cannot drift.** The table name, the revision id and the
    STATUS VOCABULARY are asserted equal to the module constants in both directions, and every
    column the module's SQL binds is asserted to exist in the DDL. A status the code writes and
    the CHECK rejects is a row Postgres refuses at runtime; a column the SQL names and the DDL
    lacks is a `UndefinedColumnError` in the middle of a delegation.
3.  **At-least-once is structurally possible.** The lease columns exist, the terminal/lease CHECKs
    are BICONDITIONAL (a one-directional check admits a row carrying `delivered_at` while sitting
    in `pending`), and the drain index is PARTIAL on `status <> 'delivered'`.
4.  **`dedup_key` is NOT unique.** A UNIQUE index here would turn a legitimate re-emission into an
    exception raised out of `DelegationDispatcher.delegate` — a dropped delegation to protect a
    duplicate fact. See the migration's own docstring.
5.  **`downgrade()` is honest.** It drops exactly what `upgrade()` created and names no table from
    0001..0007.

PROVENANCE OF THE EXPECTATION TABLES BELOW: `_REQUIRED_COLUMNS` is the column set the outbox
CONTRACT requires — enumerated from this leg's charter (dedup key, created_at,
delivered_at/attempts, status/claim discipline) plus the two `FactProducer.emit` arguments the
producer must persist to reconstruct a broker message (`topic`, the partition `key`).
`_FORBIDDEN_COLUMNS` is the subject-linkable/PHI name set `test_migration_0007_amh_inbox.py`
already uses, which is the repo's established list. NEITHER is derived from the DDL under test.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from maezo.a2a.outbox import (
    CLAIM_SQL,
    COUNT_BY_STATUS_SQL,
    ENQUEUE_SQL,
    MARK_DELIVERED_SQL,
    MARK_FAILED_SQL,
    OUTBOX_MIGRATION_REVISION,
    OUTBOX_STATUSES,
    OUTBOX_TABLE,
)

_VERSIONS_DIR = Path(__file__).resolve().parents[3] / "src/maezo/platform/migrations/versions"
_MIGRATION_PATH = _VERSIONS_DIR / "0008_a2a_fact_outbox.py"
_SOURCE = _MIGRATION_PATH.read_text(encoding="utf-8")

#: Columns the outbox contract requires (see the module docstring's provenance note).
_REQUIRED_COLUMNS: frozenset[str] = frozenset(
    {
        "id",
        "tenant",
        "dedup_key",
        "topic",
        "partition_key",
        "payload",
        "status",
        "attempts",
        "created_at",
        "claimed_at",
        "claim_expires_at",
        "claimed_by",
        "delivered_at",
        "last_error",
    }
)

#: Subject-linkable / PHI column names that must never appear (same list 0007's suite fences with).
_FORBIDDEN_COLUMNS: frozenset[str] = frozenset(
    {
        "cpf",
        "cns",
        "patient",
        "fhir_patient_id",
        "portable_subject_ref",
        "beneficiary_ref",
        "protected_source_record_ref",
        "matricula_beneficiario",
        "case_meta",
        "payload_meta",
    }
)

#: Named CHECK constraints the DDL must carry.
_REQUIRED_CONSTRAINTS: tuple[str, ...] = (
    "ck_a2a_fact_outbox_status",
    "ck_a2a_fact_outbox_delivered_at",
    "ck_a2a_fact_outbox_claim_lease",
    "ck_a2a_fact_outbox_attempts",
)

#: Every table created by 0001..0007 — none may be named anywhere in 0008.
_PRE_EXISTING_TABLES: tuple[str, ...] = (
    "audit_chain",
    "a2a_idempotency",
    "driver_idempotency",
    "custody_bundles",
    "erasure_log",
    "audit_emit_dedup",
    "amh_inbox",
)


def _emitted_ddl() -> str:
    """Only the SQL `upgrade()`/`downgrade()` execute — never the prose docstring.

    Same helper 0007's suite uses, for the same reason: the docstring deliberately NAMES things
    (jsonb, `_business_key`, the retention alternatives) whose presence in prose is the record of
    a decision, so a whole-file substring scan would be a fence satisfiable only by deleting the
    rationale.

    Extended past 0007's version in one way: SQL `--` comments are STRIPPED. The DDL below
    deliberately explains itself inline (it names `jsonb` to say why the column is not one, and
    cites `amh_inbox`'s quarantine columns for the `last_error` posture), and a fence that reads
    comments as executable SQL is again a fence satisfiable only by deleting the rationale — the
    exact failure this helper exists to avoid one level up.
    """
    ddl = "".join(re.findall(r'op\.execute\("""(.*?)"""\)', _SOURCE, re.DOTALL))
    ddl += "".join(re.findall(r'op\.execute\("(.*?)"\)', _SOURCE))
    return re.sub(r"--[^\n]*", "", ddl)


def test_the_comment_stripper_is_not_vacuous() -> None:
    """Negative control for `_emitted_ddl`: the raw DDL DOES contain the words the fences forbid
    (they are in the inline rationale), so the strip is doing real work rather than the fences
    passing because the words were never there."""
    raw = "".join(re.findall(r'op\.execute\("""(.*?)"""\)', _SOURCE, re.DOTALL))
    assert "jsonb" in raw and "amh_inbox" in raw
    assert "jsonb" not in _emitted_ddl() and "amh_inbox" not in _emitted_ddl()


def _column_names() -> set[str]:
    """Column identifiers declared in the CREATE TABLE body (leading token of each line)."""
    body = _SOURCE.split(f"CREATE TABLE IF NOT EXISTS {OUTBOX_TABLE} (", 1)[1]
    body = body.split("CONSTRAINT ck_a2a_fact_outbox_status", 1)[0]
    names: set[str] = set()
    for line in body.splitlines():
        match = re.match(r"\s{12}([a-z_][a-z0-9_]*)\s+\S", line)
        if match:
            names.add(match.group(1))
    return names


# ---------------------------------------------------------------------------
# 1. Chain integrity
# ---------------------------------------------------------------------------


def test_migration_file_is_named_for_its_revision() -> None:
    assert _MIGRATION_PATH.name == f"{OUTBOX_MIGRATION_REVISION}_a2a_fact_outbox.py"


def test_revision_and_down_revision() -> None:
    assert f'revision: str = "{OUTBOX_MIGRATION_REVISION}"' in _SOURCE
    assert 'down_revision: str | None = "0007"' in _SOURCE


def test_0008_is_claimed_as_a_parent_by_exactly_one_migration() -> None:
    """0008 is no longer the head — the chain is 0007->0008->0009->0010 — so the SOLE-HEAD
    assertion lives in `test_migration_0010_webhook_wamid_dedup.py`, having travelled with the head
    through 0009's suite, exactly as this file's own predecessor instruction prescribes.

    What stays here is the half that is about 0008 itself: its revision id must be claimed as
    parent by exactly ONE child (today `0009_drop_pgvector.py`), which is what makes a fork over
    THIS node impossible even while the head moves on.
    """
    children = []
    for path in sorted(_VERSIONS_DIR.glob("[0-9]*.py")):
        text = path.read_text(encoding="utf-8")
        down = re.search(r'^down_revision: str \| None = "([^"]+)"', text, re.MULTILINE)
        if down is not None and down.group(1) == OUTBOX_MIGRATION_REVISION:
            children.append(path.name)
    assert children == ["0009_drop_pgvector.py"], f"expected 0009 as the only child of 0008, got {children}"


# ---------------------------------------------------------------------------
# 2. DDL <-> maezo.a2a.outbox cannot drift
# ---------------------------------------------------------------------------


def test_table_name_agrees_with_the_module_constant() -> None:
    assert f"CREATE TABLE IF NOT EXISTS {OUTBOX_TABLE} (" in _emitted_ddl()


def test_status_check_vocabulary_equals_the_module_status_set() -> None:
    """Both directions. A status the code writes and the CHECK rejects is a runtime refusal; a
    value in the CHECK that no code can produce is a state nothing can ever reach."""
    match = re.search(r"CHECK \(status IN \(([^)]*)\)\)", _emitted_ddl())
    assert match is not None, "the status CHECK constraint is missing"
    checked = {value.strip().strip("'") for value in match.group(1).split(",")}
    assert checked == set(OUTBOX_STATUSES), f"DDL {sorted(checked)} != module {sorted(OUTBOX_STATUSES)}"


def test_every_column_the_module_sql_binds_exists_in_the_ddl() -> None:
    """The five module SQL constants may only name columns this migration declares.

    Non-vacuity is guaranteed by the assertion that the scan finds something: if the extraction
    regex ever stops matching, `found` is empty and the test fails rather than passing silently.
    """
    declared = _column_names()
    sql = " ".join((ENQUEUE_SQL, CLAIM_SQL, MARK_DELIVERED_SQL, MARK_FAILED_SQL, COUNT_BY_STATUS_SQL))
    # Identifiers that appear in the SQL and look like outbox columns (bare snake_case words),
    # minus SQL keywords/functions and the table/alias names.
    noise = {
        "insert",
        "into",
        "values",
        "returning",
        "update",
        "set",
        "where",
        "and",
        "or",
        "select",
        "from",
        "order",
        "by",
        "for",
        "skip",
        "locked",
        "limit",
        "now",
        "make_interval",
        "secs",
        "double",
        "precision",
        "null",
        "any",
        "bigint",
        "count",
        "group",
        "as",
        "in",
        "o",
        "c",
        "n",
        OUTBOX_TABLE,
        *OUTBOX_STATUSES,
    }
    found = {word for word in re.findall(r"\b[a-z_][a-z0-9_]*\b", sql)} - noise
    assert found, "non-vacuity: the identifier scan must find something"
    assert found <= declared, f"SQL names columns the DDL does not declare: {sorted(found - declared)}"


def test_required_columns_are_present() -> None:
    declared = _column_names()
    assert declared >= _REQUIRED_COLUMNS, f"missing: {sorted(_REQUIRED_COLUMNS - declared)}"


@pytest.mark.parametrize("forbidden", sorted(_FORBIDDEN_COLUMNS))
def test_no_subject_linkable_column_exists(forbidden: str) -> None:
    assert forbidden not in _column_names()
    assert forbidden not in _emitted_ddl()


def test_payload_is_bytea_not_jsonb() -> None:
    """Verbatim bytes, so what the relay hands the broker is what a direct producer would have
    sent — the property ADR-0039 (envelope signing) would otherwise lose to re-serialization."""
    ddl = _emitted_ddl()
    assert re.search(r"payload\s+bytea\s+NOT NULL", ddl) is not None
    assert "jsonb" not in ddl


# ---------------------------------------------------------------------------
# 3. At-least-once is structurally possible
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("constraint", _REQUIRED_CONSTRAINTS)
def test_named_constraints_are_present(constraint: str) -> None:
    assert f"CONSTRAINT {constraint}" in _emitted_ddl()


def test_terminal_and_lease_checks_are_biconditional() -> None:
    """`=` between the two predicates, never a one-directional `OR NOT`.

    One-directional admits a row that claims a delivery timestamp while sitting in `pending` — a
    row an operator reads as delivered and the relay reads as pending.
    """
    ddl = _emitted_ddl()
    assert "CHECK ((status = 'delivered') = (delivered_at IS NOT NULL))" in ddl
    assert "CHECK ((status = 'claimed') = (claim_expires_at IS NOT NULL))" in ddl


def test_drain_index_is_partial_on_undelivered() -> None:
    """Partial, so index size tracks IN-FLIGHT work rather than the full delivery history."""
    ddl = _emitted_ddl()
    match = re.search(
        r"CREATE INDEX IF NOT EXISTS ix_a2a_fact_outbox_undelivered.*?WHERE status <> 'delivered'",
        ddl,
        re.DOTALL,
    )
    assert match is not None, "the drain index must exist and be partial"


# ---------------------------------------------------------------------------
# 4. dedup_key is NOT unique
# ---------------------------------------------------------------------------


def test_dedup_index_exists_and_is_not_unique() -> None:
    ddl = _emitted_ddl()
    assert "CREATE INDEX IF NOT EXISTS ix_a2a_fact_outbox_dedup" in ddl
    assert "CREATE UNIQUE INDEX" not in ddl
    assert "UNIQUE (tenant, dedup_key)" not in ddl
    assert "dedup_key" not in ddl.split("PRIMARY KEY", 1)[1].split(",", 1)[0]


# ---------------------------------------------------------------------------
# 5. downgrade() is honest
# ---------------------------------------------------------------------------


def test_downgrade_drops_exactly_what_upgrade_created() -> None:
    upgrade_src = _SOURCE.split("def upgrade()", 1)[1].split("def downgrade()", 1)[0]
    downgrade_src = _SOURCE.split("def downgrade()", 1)[1]
    created = set(re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", upgrade_src))
    dropped = set(re.findall(r"DROP TABLE IF EXISTS (\w+)", downgrade_src))
    assert created == {OUTBOX_TABLE}
    assert dropped == created


@pytest.mark.parametrize("foreign_table", _PRE_EXISTING_TABLES)
def test_no_pre_existing_table_is_touched(foreign_table: str) -> None:
    assert foreign_table not in _emitted_ddl()
