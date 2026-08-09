"""Structural tests for migration `0007_amh_inbox` — the DDL half of the DBA-gated inbox (MZO-060).

These run without Postgres. They are not a substitute for executing the migration (that proof is
recorded in `docs/reviews/mzo-060-dba-review-packet.md`); they are the ANTI-REGRESSION half — the
things that must stay true about the DDL after this commit, expressed so that a later edit which
quietly breaks one fails CI instead of a compliance review.

Four invariants:

1.  **Chain integrity.** `0007` revises `0006`, is the head, and no other migration claims either
    slot. A forked chain is the failure mode where `alembic upgrade head` picks one branch and an
    operator believes it applied the other.
2.  **PHI posture.** The five subject-linkable envelope fields are ABSENT from the DDL, and so is
    any payload column (the payload was never an envelope field — `CanonicalEnvelope` has none) —
    with a non-vacuity half asserting the columns that must be PRESENT.
3.  **The refusal vocabulary cannot drift.** The `quarantine_reason` CHECK set must EQUAL
    `maezo.ports.errors.PortFailureReason`. A member added to the enum without the migration is a
    row the consumer can construct and the database will reject at runtime; a value in the CHECK
    without the enum is a reason nothing can ever produce.
4.  **`downgrade()` is honest.** It drops exactly what `upgrade()` created and touches nothing
    else — no table from 0001..0006 appears anywhere in this file.
"""

from __future__ import annotations

import re

import pytest

from maezo.platform.integrations.amh_inbox import (
    INBOX_MIGRATION_FILENAME,
    INBOX_MIGRATION_REVISION,
    migration_file_path,
)
from maezo.ports.errors import PortFailureReason

_VERSIONS_DIR = migration_file_path().parent
_SOURCE = migration_file_path().read_text(encoding="utf-8")

#: Envelope fields that must never become a column here (ADR-0037 immutable prohibition #5 +
#: XRD-05's DPO/Legal gate on identity), plus the payload itself (consume-not-duplicate).
_FORBIDDEN_COLUMNS: frozenset[str] = frozenset(
    {
        "protected_source_record_ref",
        "portable_subject_ref",
        "amh_mpi_ref",
        "beneficiary_ref",
        "consent_decision_ref",
        "payload",
        "payload_bytes",
        "event_body",
        "cpf",
        "cns",
        "patient",
        "fhir_patient_id",
    }
)

#: Columns the durable-settlement contract requires. Non-vacuity for the fence above.
_REQUIRED_COLUMNS: frozenset[str] = frozenset(
    {
        "contract_manifest_digest",
        "event_id",
        "amh_tenant",
        "legal_entity",
        "inbox_stream",
        "event_type",
        "idempotency_key",
        "payload_hash",
        "replay_count",
        "redelivery_count",
        "status",
        "received_at",
        "settled_at",
        "quarantined_at",
        "quarantine_reason",
    }
)


def _emitted_ddl() -> str:
    """Only the SQL `upgrade()`/`downgrade()` actually execute — never the prose docstring.

    The docstring deliberately NAMES the excluded fields and cites the 0004 custody/erasure tables
    (that naming IS the record of the decision), so a whole-file substring scan would be a fence
    that can only be satisfied by deleting the rationale.
    """
    ddl = "".join(re.findall(r'op\.execute\("""(.*?)"""\)', _SOURCE, re.DOTALL))
    ddl += "".join(re.findall(r'op\.execute\("(.*?)"\)', _SOURCE))
    return ddl


def _column_names() -> set[str]:
    """Column identifiers declared in the CREATE TABLE body (leading token of each line)."""
    body = _SOURCE.split("CREATE TABLE IF NOT EXISTS amh_inbox (", 1)[1]
    body = body.split("CONSTRAINT pk_amh_inbox", 1)[0]
    names: set[str] = set()
    for line in body.splitlines():
        match = re.match(r"\s{12}([a-z_][a-z0-9_]*)\s+\S", line)
        if match:
            names.add(match.group(1))
    return names


# ---------------------------------------------------------------------------
# 1. Chain integrity
# ---------------------------------------------------------------------------


def test_migration_file_is_present_and_named_for_its_revision() -> None:
    assert migration_file_path().name == INBOX_MIGRATION_FILENAME
    assert INBOX_MIGRATION_FILENAME.startswith(f"{INBOX_MIGRATION_REVISION}_")


def test_revision_and_down_revision() -> None:
    assert f'revision: str = "{INBOX_MIGRATION_REVISION}"' in _SOURCE
    assert 'down_revision: str | None = "0006"' in _SOURCE


def test_0007_is_the_unique_head_of_a_linear_chain() -> None:
    """No fork: every revision is claimed once and exactly one revision is unreferenced as a parent."""
    revisions: dict[str, str | None] = {}
    for path in sorted(_VERSIONS_DIR.glob("[0-9]*.py")):
        text = path.read_text(encoding="utf-8")
        rev = re.search(r'^revision: str = "([^"]+)"', text, re.MULTILINE)
        down = re.search(r'^down_revision: str \| None = (?:"([^"]+)"|None)', text, re.MULTILINE)
        assert rev is not None, f"{path.name} declares no revision"
        assert down is not None, f"{path.name} declares no down_revision"
        assert rev.group(1) not in revisions, f"duplicate revision id {rev.group(1)}"
        revisions[rev.group(1)] = down.group(1)

    assert INBOX_MIGRATION_REVISION in revisions
    parents = {down for down in revisions.values() if down is not None}
    heads = set(revisions) - parents
    assert heads == {INBOX_MIGRATION_REVISION}, f"expected 0007 to be the sole head, got {heads}"
    assert len(parents) == len(revisions) - 1, "a revision is claimed as parent by two children"


# ---------------------------------------------------------------------------
# 2. PHI posture
# ---------------------------------------------------------------------------


def test_no_subject_bearing_or_payload_column_exists() -> None:
    columns = _column_names()
    offenders = columns & _FORBIDDEN_COLUMNS
    assert not offenders, (
        f"migration 0007 declares subject-linkable / payload column(s) {sorted(offenders)} — "
        "ADR-0037 immutable prohibition #5 (no PHI nor raw source id in keys/logs/quarantine "
        "metadata) and consume-not-duplicate (the AMH is the lake of record) forbid both"
    )


def test_forbidden_names_are_absent_from_the_emitted_ddl_not_just_the_column_list() -> None:
    """A forbidden name must not appear in an index, a CHECK or a COMMENT either.

    Matched on WORD BOUNDARIES: `payload_hash` is a legitimate, non-reversible digest column, so a
    naive substring scan for `payload` would forbid the very column that lets a mutated replay be
    detected. `\\bpayload\\b` does not match inside `payload_hash` (`_` is a word character).
    """
    ddl = _emitted_ddl()
    assert "CREATE TABLE IF NOT EXISTS amh_inbox" in ddl, "non-vacuity: no DDL was extracted"
    lowered = ddl.lower()
    for name in _FORBIDDEN_COLUMNS:
        assert re.search(rf"\b{re.escape(name)}\b", lowered) is None, (
            f"forbidden identifier {name!r} appears in the emitted DDL"
        )


def test_required_columns_are_present() -> None:
    """Non-vacuity for the fence above: an empty column extraction must not read as 'clean'."""
    columns = _column_names()
    assert len(columns) == 27, f"expected 27 columns, extracted {len(columns)}: {sorted(columns)}"
    missing = _REQUIRED_COLUMNS - columns
    assert not missing, f"migration 0007 is missing required column(s): {sorted(missing)}"


def test_dedup_key_is_the_xrd10_pair_and_amh_tenant_is_not_in_it() -> None:
    """XRD-10 names `{contract_manifest_digest, event_id}` literally; widening the key weakens it."""
    assert "PRIMARY KEY (contract_manifest_digest, event_id)" in _SOURCE
    assert "PRIMARY KEY (contract_manifest_digest, event_id, amh_tenant)" not in _SOURCE


def test_idempotency_key_index_is_not_unique() -> None:
    """The contract declares the FIELD but no uniqueness SCOPE — inventing one is XRD-04's line."""
    assert "CREATE INDEX IF NOT EXISTS ix_amh_inbox_idempotency" in _SOURCE
    assert "UNIQUE INDEX" not in _SOURCE.upper()


# ---------------------------------------------------------------------------
# 3. The refusal vocabulary cannot drift from PortFailureReason
# ---------------------------------------------------------------------------


def test_quarantine_reason_check_equals_the_port_failure_taxonomy() -> None:
    match = re.search(
        r"ck_amh_inbox_quarantine_reason_vocabulary\s*\n\s*CHECK \(quarantine_reason IS NULL OR "
        r"quarantine_reason IN \((.*?)\)\)",
        _SOURCE,
        re.DOTALL,
    )
    assert match is not None, "the quarantine-reason vocabulary CHECK is missing from 0007"
    # `[a-z0-9_]`, NOT `[a-z_]`: a digit-bearing token like `oauth2_expired` would otherwise be
    # split into fragments that match neither side, and the set comparison would go on passing
    # while the DDL and the enum had actually drifted. Mutation-proved.
    checked = set(re.findall(r"'([a-z0-9_]+)'", match.group(1)))
    expected = {reason.value for reason in PortFailureReason}
    assert checked == expected, (
        f"DDL vocabulary drifted from PortFailureReason: only-in-DDL={sorted(checked - expected)}, "
        f"only-in-enum={sorted(expected - checked)}"
    )
    assert len(checked) == 12


# ---------------------------------------------------------------------------
# 4. Constraint shape + honest downgrade
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "constraint",
    [
        "ck_amh_inbox_status",
        "ck_amh_inbox_stream",
        "ck_amh_inbox_settled_at",
        "ck_amh_inbox_processed_at",
        "ck_amh_inbox_quarantined_at",
        "ck_amh_inbox_quarantine_reason",
        "ck_amh_inbox_quarantine_topic",
        "ck_amh_inbox_quarantine_reason_vocabulary",
        "ck_amh_inbox_replay_count",
        "ck_amh_inbox_redelivery_count",
    ],
)
def test_named_constraints_are_present(constraint: str) -> None:
    assert f"CONSTRAINT {constraint}" in _SOURCE


def test_terminal_state_checks_are_biconditional() -> None:
    """One-directional checks permit a row that claims a settlement timestamp while sitting in
    RECEIVED — an operator reads it as settled while the repository reads it as pending."""
    assert "CHECK ((status = 'SETTLED') = (settled_at IS NOT NULL))" in _SOURCE
    assert "CHECK ((status = 'QUARANTINED') = (quarantined_at IS NOT NULL))" in _SOURCE
    assert "CHECK ((status = 'QUARANTINED') = (quarantine_reason IS NOT NULL))" in _SOURCE


def test_pending_and_quarantine_indexes_are_partial() -> None:
    """An inbox grows monotonically; a full index on the scan predicate would keep every settled
    row in the index the scan reads, forever."""
    assert "WHERE status IN ('RECEIVED', 'PROCESSED')" in _SOURCE
    assert "WHERE status = 'QUARANTINED'" in _SOURCE


def test_downgrade_drops_exactly_what_upgrade_created() -> None:
    upgrade_src = _SOURCE.split("def upgrade()", 1)[1].split("def downgrade()", 1)[0]
    downgrade_src = _SOURCE.split("def downgrade()", 1)[1]

    created = set(re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", upgrade_src))
    dropped = set(re.findall(r"DROP TABLE IF EXISTS (\w+)", downgrade_src))
    assert created == {"amh_inbox"}
    assert dropped == created, f"downgrade drops {sorted(dropped)}, upgrade created {sorted(created)}"

    # Indexes live on the table, so dropping the table drops them; nothing else may be dropped.
    assert "DROP INDEX" not in downgrade_src
    assert "ALTER TABLE" not in _SOURCE


@pytest.mark.parametrize(
    "foreign_table",
    [
        "audit_chain",
        "audit_emit_dedup",
        "a2a_idempotency",
        "driver_idempotency",
        "custody_bundles",
        "erasure_log",
        "agent_checkpoints",
        "checkpoints",
        "checkpoint_blobs",
    ],
)
def test_no_pre_existing_table_is_touched(foreign_table: str) -> None:
    """0007 is additive. A DROP/ALTER against an existing table here would be a data-loss defect
    hiding inside an "add a table" review.

    Scoped to the EMITTED DDL: the docstring cites `custody_bundles`/`erasure_log` as the
    precedent it is reasoning against, and a fence that forbade naming the precedent would be a
    fence satisfied by deleting the rationale.
    """
    assert foreign_table not in _emitted_ddl()
