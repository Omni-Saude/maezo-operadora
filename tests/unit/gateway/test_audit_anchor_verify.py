"""Tests for `maezo.gateway.audit_anchor_verify` — the Postgres↔anchor verification job (leg 2).

TEST DOCTRINE FOR THIS FILE — consequence-level, with named RED controls, continuing leg 1's
`D1..D12` numbering as `V1..V12`. Every defense below is paired with the test that goes RED when
the defense is neutered, so a reviewer can neuter any one of them and predict the failure:

  V1  RECOMPUTE, NEVER TRUST `envelope["root"]`.
      NEUTER: compare the database root against `anchor.stored_root` instead of against
      `sha256(anchor.checkpoint_bytes)`.
      RED: `test_an_envelope_root_edited_to_match_the_database_is_still_not_clean` — the direct
      consequence probe. It seals a genuine, signature-VALID checkpoint for chain A while setting
      the (unsigned) `root` field to the root of chain B, and puts chain B in the database. A job
      that trusted the stored field reports MATCH; this one reports DIVERGENCE. Its CONTROL is
      `test_untampered_chain_matches_the_anchor` — the recomputation must still be able to say
      clean, or the whole verdict is a constant.

  V2  The database side is recomputed through the WRITER's own derivation, never a second copy.
      NEUTER: re-implement the canonical mapping / the root here instead of calling
      `checkpoint_for_chain` + `checkpoint_root`.
      RED: `test_untampered_chain_matches_the_anchor` (any drift makes an honest chain diverge) and
      `test_the_verify_module_holds_no_canonicalization_of_its_own`.

  V3  A listing is ADVISORY; a clean verdict may never rest on one.
      NEUTER: drop the `listed != probed` comparison (or default `key_probe` to something derived
      from the listing).
      RED: `test_a_newer_anchor_invisible_to_the_listing_does_not_produce_a_clean_verdict`.
      Its RED CONTROL is the test immediately after it,
      `test_red_control_a_credulous_corroborator_hands_back_the_silent_clean_verdict`, which wires
      a corroborator that merely echoes the listing and DEMONSTRATES the disaster: the job selects
      the older anchor, verifies a chain whose last two records were deleted, and returns MATCH.
      The motivating asymmetry itself is pinned on leg 1's real store by
      `test_labeled_fake_store_listing_omits_what_get_serves_through_a_symlinked_directory`.

  V4  Every absence-state is its OWN outcome, and none of them is clean.
      NEUTER: collapse any of NO_ANCHOR / DB_ERROR / SIGNATURE_INVALID / STORE_LISTING_SUSPECT /
      ANCHOR_UNREADABLE / DISABLED into another status, or give any of them exit code 0.
      RED: `test_absence_states_are_each_their_own_outcome_and_none_is_clean`,
      `test_only_match_exits_zero`, `test_status_vocabulary_is_closed_and_pinned`.

  V5  Nothing downstream of the signature runs on an unverified checkpoint.
      NEUTER: skip `verifier.verify`, or treat a raising verifier as a pass.
      RED: `test_a_delabeled_signature_is_signature_invalid`,
      `test_an_anchor_signed_by_another_key_is_signature_invalid`,
      `test_a_verifier_that_raises_is_signature_invalid_not_clean`, and the adversary probe
      `test_an_adversary_who_can_add_to_the_store_can_only_deny_never_clean`.

  V6  Flag OFF => provably zero store I/O and zero database I/O.
      NEUTER: remove the `anchor_verification_enabled()` early return from `verify_latest_anchor`
      or from `run_verification_loop`.
      RED: `test_disabled_verification_touches_no_seam_and_no_filesystem`,
      `test_disabled_loop_never_sleeps_and_never_touches_a_seam`.

  V7  A divergence report carries hashes and counts ONLY.
      NEUTER: add record content (details/agent_id/action) to the outcome or the event.
      RED: `test_divergence_event_carries_no_record_content`,
      `test_outcome_json_shape_is_pinned`, `test_a_database_error_never_carries_the_exception_text`.

  V8  The job is READ-ONLY on both sides and has no import-time side effect.
      NEUTER: call `store.put`, add a mutating SQL literal, or start a task at module scope.
      RED: `test_the_verify_module_never_writes_to_the_anchor_store`,
      `test_the_verify_module_issues_only_the_two_pinned_read_statements`,
      `test_the_verify_module_has_no_import_time_side_effect`.

  V9  The dark build: nothing under `src/maezo/` imports the verification job.
      NEUTER: import `audit_anchor_verify` from any production module, in any spelling.
      RED: `test_no_production_module_imports_the_verify_job` (hardcoded EMPTY allowlist) plus
      `test_verify_fence_predicate_catches_every_spelling[...]`. The sibling fence in
      `test_audit_anchor.py` moved from an EMPTY allowlist to exactly one named entry — this
      module — which is the declaration leg 1 designed that list for.

  V10 The offline CLI's contract (exit codes, JSON shape) is pinned.
      NEUTER: renumber an exit code, or add/remove a JSON key.
      RED: `test_cli_exit_codes_are_pinned`, `test_cli_json_shape_is_pinned`,
      `test_cli_refuses_without_a_verifier_secret`.

  V11 Continuous mode is explicitly invoked, refuses a busy interval, and never swallows its sink.
      NEUTER: default `interval_seconds` to 0, swallow `on_outcome` exceptions, or stop the loop
      on the first divergence.
      RED: `test_loop_refuses_a_non_positive_interval`,
      `test_loop_does_not_swallow_a_failing_alert_sink`,
      `test_loop_keeps_verifying_after_a_divergence`.

  V12 The corroborating probe is independent by construction and cannot hang.
      NEUTER: implement `probe_keys` with `Path.rglob` (the listing's own primitive), or drop the
      `(st_dev, st_ino)` cycle guard.
      RED: `test_filesystem_probe_sees_the_anchor_the_listing_misses`,
      `test_filesystem_probe_terminates_on_a_symlink_cycle`.

  V13 The anchor set must be ONE contiguous in-band chain, and selection follows the LINKS.
      NEUTER: delete `_walk_anchor_chain`'s leftover/genesis/fork checks, replace it with
      `tuple(sorted(verified))`, walk links against envelopes whose signatures were not verified,
      or go back to selecting `max(listed)`.
      RED: `test_an_anchor_deleted_from_the_middle_of_the_chain_is_caught_by_the_evidence` — the
      direct consequence probe (a HISTORICAL anchor removed from a store whose retention lock
      failed; both enumerations agree it is gone, so V3's probe cannot fire, and the database is
      honest, so no content check fires either). Its RED CONTROL is the test immediately after it,
      `test_red_control_a_credulous_chain_walk_reports_the_thinned_ledger_as_clean`, which swaps in
      the pre-chaining behaviour (trust the key order, check no links) and DEMONSTRATES the silent
      MATCH over a thinned ledger. Genesis semantics are pinned in both directions by
      `test_a_single_genesis_anchor_is_a_legal_chain_of_length_one` and
      `test_a_lone_non_genesis_anchor_has_nowhere_to_start`; the ordering independence by
      `test_the_chain_walk_orders_by_links_not_by_key_ordering` (relabelled keys, adversarial
      insertion order — the divergence is INJECTED, never inherited from the platform) and
      `test_selection_follows_the_chain_even_when_the_stored_key_stamps_lie`; the signature
      precondition by `test_every_anchor_is_signature_verified_before_any_link_is_believed`.
      NOT claimed: truncation of the NEWEST anchors, which no backward link can see — that stays
      V3's job, and both defenses are wired.

  V14 The anchor's SIGNED schema version is cross-checked against the tenant's LIVE one, LAST.
      NEUTER: make `_schema_versions_agree` always agree, drop the `schema_version` field from
      `ChainSnapshot`, default it, or move the check ahead of the content comparison.
      RED: `test_a_migration_the_anchor_never_saw_is_surfaced_not_silently_matched`, with its RED
      CONTROL `test_red_control_a_schema_cross_check_that_always_agrees_calls_a_stale_anchor_clean`.
      The ORDERING half — that a real divergence can never be masked by the milder schema finding —
      is `test_a_content_divergence_is_never_masked_by_a_stale_schema_version`; the fail-closed
      seam is `test_snapshot_from_rows_refuses_to_default_the_schema_version`.

The RED probes actually executed for this file are tabulated in
`docs/design/wave4-audit-anchor.md` §9 with their exact counts.
"""

from __future__ import annotations

import ast
import json
import os
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import pytest
import structlog

from maezo.gateway import audit_anchor_verify
from maezo.gateway.audit import AuditRecord, AuditSink
from maezo.gateway.audit_anchor import (
    ANCHOR_ENABLED_ENV,
    ANCHOR_FORMAT,
    FAKE_ANCHOR_SIGNATURE_PREFIX,
    GENESIS_PREV_ANCHOR_ROOT,
    AnchorCheckpoint,
    AnchorSignature,
    LabeledFakeKmsAnchorSigner,
    LabeledFakeWormAnchorStore,
    RefusingAnchorStore,
    anchor_key,
    build_envelope,
    canonical_bytes,
    checkpoint_bytes,
    checkpoint_for_chain,
    checkpoint_root,
    write_anchor,
)
from maezo.gateway.audit_anchor_verify import (
    ALL_STATUSES,
    ANCHOR_VERIFY_ENABLED_ENV,
    EVENT_BY_STATUS,
    EXIT_CLI_REFUSED,
    EXIT_CODE_BY_STATUS,
    LEVEL_BY_STATUS,
    REASON_ANCHOR_CHAIN_FORK,
    REASON_ANCHOR_CHAIN_LINK_MISSING,
    REASON_ANCHOR_CHAIN_NO_GENESIS,
    REASON_ANCHOR_ROOT_FIELD_INCONSISTENT,
    REASON_CHAIN_DISCONTINUITY,
    REASON_CHAIN_FORK,
    REASON_CHECKPOINT_TENANT_MISMATCH,
    REASON_DATABASE_UNREACHABLE,
    REASON_ENVELOPE_FORMAT_UNKNOWN,
    REASON_ENVELOPE_NOT_JSON,
    REASON_ENVELOPE_SHAPE_UNKNOWN,
    REASON_LISTING_PROBE_DISAGREEMENT,
    REASON_LISTING_UNAVAILABLE,
    REASON_PROBE_UNAVAILABLE,
    REASON_RECORD_COUNT_SHORTFALL,
    REASON_ROOT_MISMATCH,
    REASON_SCHEMA_VERSION_MISMATCH,
    REASON_SCHEMA_VERSION_UNREADABLE,
    REASON_SELECTED_ANCHOR_UNREADABLE,
    REASON_SIGNATURE_REJECTED,
    REASON_SIGNATURE_VERIFIER_FAILED,
    REASON_TO_STATUS,
    REASON_VERIFICATION_DISABLED,
    STATUS_ANCHOR_CHAIN_BROKEN,
    STATUS_ANCHOR_UNREADABLE,
    STATUS_DB_ERROR,
    STATUS_DISABLED,
    STATUS_DIVERGENCE,
    STATUS_MATCH,
    STATUS_NO_ANCHOR,
    STATUS_SCHEMA_VERSION_MISMATCH,
    STATUS_SIGNATURE_INVALID,
    STATUS_STORE_LISTING_SUSPECT,
    AnchorVerificationOutcome,
    ChainSnapshot,
    FilesystemAnchorKeyProbe,
    PostgresChainRecordSource,
    _cli,
    anchor_verification_enabled,
    run_verification_loop,
    snapshot_from_rows,
    verify_latest_anchor,
)

# tests/unit/gateway/<file> -> parents[3] == repo root (same idiom as test_audit_anchor.py).
_REPO_ROOT = Path(__file__).resolve().parents[3]
_SRC_MAEZO = _REPO_ROOT / "src" / "maezo"
_VERIFY_MODULE_PATH = _SRC_MAEZO / "gateway" / "audit_anchor_verify.py"

_TENANT: Final[str] = "amh"
_SCHEMA_VERSION: Final[str] = "0005_audit_emit_dedup"
_FIXTURE_SECRET: Final[bytes] = b"labeled-fake-anchor-secret-nao-vinculativo"
_FIXTURE_KEY_LABEL: Final[str] = "leg2-fixture"


# =================================================================================================
# Fixtures — a REAL chain, REAL anchors written by leg 1's writer, and rows shaped like the table
# =================================================================================================


def _emit_chain(count: int = 5, *, marker: str = "ok") -> list[AuditRecord]:
    """An honest, internally-valid chain of `count` records with distinct minute timestamps."""
    sink = AuditSink()
    records: list[AuditRecord] = []
    for index in range(count):
        record = AuditRecord(
            agent_id="anchor-verify-fixture",
            tenant_id=_TENANT,
            agent_version="1.0.0",
            action="fixture.acao",
            decision="ALLOW",
            details={"i": index, "marker": marker},
            timestamp=datetime(2026, 3, 1, 12, index, 0, tzinfo=UTC),
        )
        sink.emit(record)
        records.append(record)
    assert sink.verify_chain() is True  # the fixture really is a valid chain
    return records


def _rows_from_records(records: list[AuditRecord]) -> list[dict[str, Any]]:
    """`audit_chain` rows as `_row_to_record` reads them (column names, not attribute names).

    Tests drive the job through `snapshot_from_rows` -> `audit_postgres._row_to_record` rather than
    handing `AuditRecord`s straight back, so the row->record mapping the real Postgres source uses
    is exercised offline instead of being the one untested link in the chain.
    """
    return [
        {
            "agent_id": record.agent_id,
            "tenant_id": record.tenant_id,
            "agent_version": record.agent_version,
            "action": record.action,
            "decision": record.decision,
            "decision_basis": dict(record.details),
            "timestamp": record.timestamp,
            "dmn_versions": dict(record.dmn_versions),
            "model_id": record.model_id,
            "prompt_version": record.prompt_version,
            "prev_record_hash": record.prev_hash,
            "record_hash": record.record_hash,
        }
        for record in records
    ]


class _RowRecordSource:
    """A `ChainRecordSource` over in-memory rows. The DB seam, with no database.

    `schema_version` is what the tenant's LIVE `alembic_version` would report; it defaults to the
    SAME revision the fixture anchors are sealed under (`_SCHEMA_VERSION`), so the schema
    cross-check (V14) is satisfied by default and only the tests that deliberately move it exercise
    the mismatch. `None` models a source that could not establish the live revision.
    """

    def __init__(self, rows: list[dict[str, Any]], *, schema_version: str | None = _SCHEMA_VERSION) -> None:
        self._rows = rows
        self._schema_version = schema_version

    async def read_chain(self, tenant_id: str) -> ChainSnapshot:
        return snapshot_from_rows(self._rows, schema_version=self._schema_version)


class _RaisingRecordSource:
    def __init__(self, exc: BaseException) -> None:
        self._exc = exc

    async def read_chain(self, tenant_id: str) -> ChainSnapshot:
        raise self._exc


class _ExplodingRecordSource:
    async def read_chain(self, tenant_id: str) -> ChainSnapshot:  # pragma: no cover - must not run
        raise AssertionError("the database was read while the verification flag was OFF")


class _ExplodingStore:
    def put(self, key: str, payload: bytes) -> None:  # pragma: no cover - must not run
        raise AssertionError("store.put was called by the verification job")

    def get(self, key: str) -> bytes:  # pragma: no cover - must not run
        raise AssertionError("store.get was called while the verification flag was OFF")

    def list_keys(self) -> tuple[str, ...]:  # pragma: no cover - must not run
        raise AssertionError("store.list_keys was called while the verification flag was OFF")


class _ExplodingProbe:
    def probe_keys(self, tenant_id: str) -> tuple[str, ...]:  # pragma: no cover - must not run
        raise AssertionError("the key probe ran while the verification flag was OFF")


class _ExplodingSleep:
    async def __call__(self, delay: float) -> None:  # pragma: no cover - must not run
        raise AssertionError("the loop slept while the verification flag was OFF")


class _RecordingSleep:
    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)


class _EchoingProbe:
    """A corroborator that merely repeats the store's listing — i.e. no corroboration at all.

    Used ONLY as the RED control for V3: it is what the job would effectively have if the probe
    seam did not exist, and it makes the silent clean verdict reproducible.
    """

    def __init__(self, store: Any) -> None:
        self._store = store

    def probe_keys(self, tenant_id: str) -> tuple[str, ...]:
        return tuple(k for k in self._store.list_keys() if k.startswith(f"{tenant_id}/"))


class _ListingOmittingStore:
    """A store whose `list_keys()` hides some keys that `get()` still serves.

    The measured asymmetry of `LabeledFakeWormAnchorStore` (rglob does not descend into symlinked
    directories while get/put resolve through them — pinned by
    `test_labeled_fake_store_listing_omits_what_get_serves_through_a_symlinked_directory`) in a
    controllable form, and equally the shape of an eventually-consistent object-store listing.
    """

    def __init__(self, delegate: LabeledFakeWormAnchorStore, hidden: frozenset[str]) -> None:
        self._delegate = delegate
        self._hidden = hidden

    def put(self, key: str, payload: bytes) -> None:
        self._delegate.put(key, payload)

    def get(self, key: str) -> bytes:
        return self._delegate.get(key)

    def list_keys(self) -> tuple[str, ...]:
        return tuple(k for k in self._delegate.list_keys() if k not in self._hidden)


class _UnreadableKeyStore:
    """Lists a key it cannot serve — listing/get disagreement in the other direction."""

    def __init__(self, key: str) -> None:
        self._key = key

    def put(self, key: str, payload: bytes) -> None:  # pragma: no cover - never used
        raise AssertionError("unused")

    def get(self, key: str) -> bytes:
        raise FileNotFoundError(key)

    def list_keys(self) -> tuple[str, ...]:
        return (self._key,)


def _signer(label: str = _FIXTURE_KEY_LABEL, secret: bytes = _FIXTURE_SECRET) -> LabeledFakeKmsAnchorSigner:
    return LabeledFakeKmsAnchorSigner(key_label=label, secret=secret)


def _seal_anchor(
    store: Any,
    records: list[AuditRecord],
    *,
    count: int | None = None,
    signer: LabeledFakeKmsAnchorSigner | None = None,
    prev_anchor_root: str = GENESIS_PREV_ANCHOR_ROOT,
) -> str:
    """Seal a REAL anchor over `records[:count]` through leg 1's `write_anchor`. Returns its key.

    Deliberately the writer, not a hand-built envelope: the pair under test is leg 1's writer and
    leg 2's verifier, and a fixture that hand-rolled the envelope would prove the verifier agrees
    with the fixture rather than with the writer. `prev_anchor_root` defaults to the genesis
    sentinel (the common single-anchor case); the anchor-chain tests pass a real predecessor root
    to link a second/third anchor.
    """
    window = records if count is None else records[:count]
    checkpoint = checkpoint_for_chain(
        window,
        tenant_id=_TENANT,
        chain_schema_version=_SCHEMA_VERSION,
        prev_anchor_root=prev_anchor_root,
    )
    outcome = write_anchor(checkpoint, signer=signer or _signer(), store=store)
    assert outcome.written is True
    assert outcome.anchor_key is not None
    return outcome.anchor_key


def _put_envelope(
    store: Any,
    checkpoint: AnchorCheckpoint,
    *,
    root: str | None = None,
    signature: AnchorSignature | None = None,
) -> str:
    """Seal a HAND-BUILT envelope (for forgery/corruption probes the writer would never produce)."""
    payload = checkpoint_bytes(checkpoint)
    effective_root = root if root is not None else checkpoint_root(checkpoint)
    effective_signature = signature if signature is not None else _signer().sign(payload)
    key = anchor_key(checkpoint, effective_root)
    store.put(key, canonical_bytes(build_envelope(checkpoint, effective_root, effective_signature)))
    return key


@pytest.fixture
def store(tmp_path: Path) -> LabeledFakeWormAnchorStore:
    return LabeledFakeWormAnchorStore(tmp_path / "anchors")


@pytest.fixture
def probe(tmp_path: Path) -> FilesystemAnchorKeyProbe:
    return FilesystemAnchorKeyProbe(tmp_path / "anchors")


@pytest.fixture
def anchors_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Both dark-build flags ON: leg 1's writer (to SEAL fixtures) and leg 2's verifier."""
    monkeypatch.setenv(ANCHOR_ENABLED_ENV, "1")
    monkeypatch.setenv(ANCHOR_VERIFY_ENABLED_ENV, "1")


async def _verify(
    store: Any,
    records: Any,
    probe: Any,
    *,
    verifier: Any = None,
    tenant_id: str = _TENANT,
) -> AnchorVerificationOutcome:
    return await verify_latest_anchor(
        tenant_id=tenant_id,
        store=store,
        verifier=verifier or _signer(),
        key_probe=probe,
        records=records,
    )


# =================================================================================================
# V4 — the closed vocabulary, its completeness, and "no absence-state is clean"
# =================================================================================================


def test_status_vocabulary_is_closed_and_pinned() -> None:
    """HARDCODED set-equality. Provenance: the six outcomes named in the leg-2 brief plus the four
    the brief's own rule requires — `ANCHOR_UNREADABLE` (bytes that are not an anchor are not a
    forged signature), `DISABLED` ("did not run" is not "ran and passed"), and the two this
    follow-up adds: `ANCHOR_CHAIN_BROKEN` (the signed evidence is not one contiguous in-band chain,
    which is not a statement about the database) and `SCHEMA_VERSION_MISMATCH` (the content agreed
    but the anchor attests a migration revision the database no longer has). Set-equality so BOTH
    an addition and a removal fail: leg 3's drills and the runbook branch on these tokens."""
    expected = frozenset(
        {
            "MATCH",
            "DIVERGENCE",
            "NO_ANCHOR",
            "SIGNATURE_INVALID",
            "STORE_LISTING_SUSPECT",
            "DB_ERROR",
            "ANCHOR_UNREADABLE",
            "ANCHOR_CHAIN_BROKEN",
            "SCHEMA_VERSION_MISMATCH",
            "DISABLED",
        }
    )
    assert expected == ALL_STATUSES


def test_every_status_has_an_exit_code_an_event_and_a_level() -> None:
    """Completeness in all three directions — a status with no exit code would crash the CLI at the
    worst possible moment, and one with no event would be an incident nobody is paged for."""
    assert frozenset(EXIT_CODE_BY_STATUS) == ALL_STATUSES
    assert frozenset(EVENT_BY_STATUS) == ALL_STATUSES
    assert frozenset(LEVEL_BY_STATUS) == ALL_STATUSES
    assert len(frozenset(EVENT_BY_STATUS.values())) == len(ALL_STATUSES)  # no two share an event
    assert len(frozenset(EXIT_CODE_BY_STATUS.values())) == len(ALL_STATUSES)


def test_only_match_exits_zero() -> None:
    """THE consequence of "no absence-state is clean", expressed where a CI job can feel it."""
    assert EXIT_CODE_BY_STATUS[STATUS_MATCH] == 0
    non_clean = {status: code for status, code in EXIT_CODE_BY_STATUS.items() if status != STATUS_MATCH}
    assert all(code != 0 for code in non_clean.values()), non_clean
    # Deliberately above argparse's usage code (2) and above a bare traceback's 1, so that a crash
    # or a typo can never be read as a verdict.
    assert all(code >= 10 for code in non_clean.values()), non_clean
    assert EXIT_CLI_REFUSED not in set(EXIT_CODE_BY_STATUS.values())


def test_exit_code_table_is_pinned() -> None:
    """HARDCODED. Provenance: the leg-2 brief's "exiting 0 on MATCH, non-zero per outcome class".
    Leg 3's drills and the runbook assert on these numbers, so a renumbering must be a reviewed
    diff, never a refactor side effect.

    The two new statuses take 17 and 18 — APPENDED, deliberately not slotted in beside their
    conceptual neighbours. Inserting `ANCHOR_CHAIN_BROKEN` next to `ANCHOR_UNREADABLE` would have
    shifted `DISABLED` from 16 to 17, silently re-labelling an incident class in every consumer
    that already pins these integers."""
    assert dict(EXIT_CODE_BY_STATUS) == {
        "MATCH": 0,
        "DIVERGENCE": 10,
        "NO_ANCHOR": 11,
        "SIGNATURE_INVALID": 12,
        "STORE_LISTING_SUSPECT": 13,
        "DB_ERROR": 14,
        "ANCHOR_UNREADABLE": 15,
        "DISABLED": 16,
        "ANCHOR_CHAIN_BROKEN": 17,
        "SCHEMA_VERSION_MISMATCH": 18,
    }


def test_every_reason_maps_to_a_status_in_the_closed_vocabulary() -> None:
    """A reason can never be emitted beside a status that contradicts it."""
    assert frozenset(REASON_TO_STATUS.values()) <= ALL_STATUSES
    assert STATUS_MATCH not in REASON_TO_STATUS.values()  # MATCH has no reason; it is the absence


@pytest.mark.parametrize(
    "status",
    sorted(ALL_STATUSES - {STATUS_MATCH}),
)
def test_absence_states_are_each_their_own_outcome_and_none_is_clean(status: str) -> None:
    outcome = AnchorVerificationOutcome(status=status, tenant_id=_TENANT)
    assert outcome.is_clean is False
    assert outcome.exit_code != 0


def test_match_is_the_only_clean_outcome() -> None:
    assert AnchorVerificationOutcome(status=STATUS_MATCH, tenant_id=_TENANT).is_clean is True


# =================================================================================================
# V6 — the sibling flag: OFF by default, provably inert
# =================================================================================================


def test_verify_flag_name_is_pinned() -> None:
    """HARDCODED. Provenance: the leg-2 dark-build switch, deliberately a SIBLING of leg 1's."""
    assert ANCHOR_VERIFY_ENABLED_ENV == "MAEZO_AUDIT_ANCHOR_VERIFY_ENABLED"
    assert ANCHOR_VERIFY_ENABLED_ENV != ANCHOR_ENABLED_ENV


def test_the_writer_flag_alone_does_not_enable_verification(monkeypatch: pytest.MonkeyPatch) -> None:
    """THE consequence of the sibling-flag decision. Enabling anchor WRITING on a host must not
    also arm a database-reading, human-paging verification loop there: the two sides are separately
    deployable and separately privileged (sign vs verify), and "anchoring but not yet verifying" is
    a real intermediate state a single flag could not express."""
    monkeypatch.setenv(ANCHOR_ENABLED_ENV, "1")
    monkeypatch.delenv(ANCHOR_VERIFY_ENABLED_ENV, raising=False)
    assert anchor_verification_enabled() is False


def test_verify_flag_is_off_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ANCHOR_VERIFY_ENABLED_ENV, raising=False)
    assert anchor_verification_enabled() is False


@pytest.mark.parametrize("raw", ["", "   ", "0", "false", "no", "off", "maybe", "2", "enabled", "sim"])
def test_verify_flag_is_off_for_non_truthy_values(monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
    monkeypatch.setenv(ANCHOR_VERIFY_ENABLED_ENV, raw)
    assert anchor_verification_enabled() is False


@pytest.mark.parametrize("raw", ["1", "true", "TRUE", " yes ", "on", "On"])
def test_verify_flag_shares_the_writer_truthy_vocabulary(monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
    """HARDCODED vocabulary, IMPORTED not re-spelled: `audit_anchor._TRUTHY`. A per-flag dialect
    (one side accepting `"on"`, the other ignoring it) would make an operator's enablement
    half-apply, silently."""
    monkeypatch.setenv(ANCHOR_VERIFY_ENABLED_ENV, raw)
    assert anchor_verification_enabled() is True


async def test_disabled_verification_touches_no_seam_and_no_filesystem(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """THE inertness proof, quadrupled: an exploding store, an exploding probe, an exploding record
    source, AND a filesystem probe for non-existence. Each half matters — a job that skipped the
    database but still listed the store would still be reaching into a security account on every
    run of a feature that is supposed to be off."""
    monkeypatch.delenv(ANCHOR_VERIFY_ENABLED_ENV, raising=False)
    monkeypatch.setenv(ANCHOR_ENABLED_ENV, "1")  # even with the WRITER fully enabled

    outcome = await verify_latest_anchor(
        tenant_id=_TENANT,
        store=_ExplodingStore(),
        verifier=_signer(),
        key_probe=_ExplodingProbe(),
        records=_ExplodingRecordSource(),
    )

    assert outcome.status == STATUS_DISABLED
    assert outcome.reason == REASON_VERIFICATION_DISABLED
    assert outcome.is_clean is False
    assert outcome.anchor_key is None
    assert outcome.anchor_root is None
    assert outcome.database_root is None
    assert os.listdir(tmp_path) == []  # noqa: PTH208 - pathlib in an async test trips ASYNC240


async def test_disabled_verification_creates_nothing_with_the_real_seams(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Same proof against the REAL labeled-fake store and the REAL filesystem probe: probe the
    filesystem, not a mock's call count."""
    monkeypatch.delenv(ANCHOR_VERIFY_ENABLED_ENV, raising=False)
    root = tmp_path / "anchors"

    outcome = await _verify(
        LabeledFakeWormAnchorStore(root), _ExplodingRecordSource(), FilesystemAnchorKeyProbe(root)
    )

    assert outcome.status == STATUS_DISABLED
    assert not root.exists()


async def test_disabled_loop_never_sleeps_and_never_touches_a_seam(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A disabled feature must not own a task that wakes up forever to do nothing."""
    monkeypatch.delenv(ANCHOR_VERIFY_ENABLED_ENV, raising=False)

    outcomes = await run_verification_loop(
        tenant_id=_TENANT,
        store=_ExplodingStore(),
        verifier=_signer(),
        key_probe=_ExplodingProbe(),
        records=_ExplodingRecordSource(),
        interval_seconds=60.0,
        max_iterations=5,
        sleep=_ExplodingSleep(),
    )

    assert [o.status for o in outcomes] == [STATUS_DISABLED]


# =================================================================================================
# V1/V2 — MATCH (the control) and the recompute-never-trust consequence
# =================================================================================================


async def test_untampered_chain_matches_the_anchor(
    anchors_enabled: None, store: LabeledFakeWormAnchorStore, probe: FilesystemAnchorKeyProbe
) -> None:
    """THE CONTROL for every divergence test below: the job must be ABLE to say clean. A verifier
    that always alarms detects nothing — it just moves the blindness from the chain to the pager."""
    records = _emit_chain(5)
    key = _seal_anchor(store, records)

    outcome = await _verify(store, _RowRecordSource(_rows_from_records(records)), probe)

    assert outcome.status == STATUS_MATCH
    assert outcome.reason is None
    assert outcome.is_clean is True
    assert outcome.anchor_key == key
    assert outcome.anchor_root == outcome.database_root
    assert outcome.anchored_head_hash == outcome.database_head_hash == records[-1].record_hash
    assert outcome.anchored_record_count == outcome.database_record_count == 5
    assert outcome.signature_key_id.startswith("FAKE-KMS-NAO-VINCULATIVO:")  # type: ignore[union-attr]
    assert outcome.listing_disagreement == ()


async def test_a_chain_that_grew_since_the_anchor_still_matches(
    anchors_enabled: None, store: LabeledFakeWormAnchorStore, probe: FilesystemAnchorKeyProbe
) -> None:
    """An append-only chain is SUPPOSED to grow. The anchored window is a genesis-anchored PREFIX
    of `record_count` records; treating growth as divergence would alarm on every healthy tenant
    between two anchor writes, and an alarm that fires constantly is an alarm that is muted."""
    records = _emit_chain(8)
    _seal_anchor(store, records, count=3)

    outcome = await _verify(store, _RowRecordSource(_rows_from_records(records)), probe)

    assert outcome.status == STATUS_MATCH
    assert outcome.anchored_record_count == 3
    assert outcome.database_record_count == 8
    assert outcome.database_head_hash == records[2].record_hash  # the WINDOW's head, not the tail


async def test_an_envelope_root_edited_to_match_the_database_is_still_not_clean(
    anchors_enabled: None, store: LabeledFakeWormAnchorStore, probe: FilesystemAnchorKeyProbe
) -> None:
    """V1, DIRECT. The `root` field of the envelope is NOT covered by the signature (leg 1's
    `build_envelope` signs the CHECKPOINT bytes), so an actor who can edit an anchor file can set
    it to exactly what the rewritten database hashes to. This test builds that artifact: a genuine,
    signature-VALID checkpoint attesting chain A, with `root` overwritten by the root of chain B —
    and chain B in the database.

    A job that compared `envelope["root"]` against the recomputed database root would find them
    EQUAL and report MATCH. This one recomputes the anchored root from the signed checkpoint and
    refuses. The assertion below on `database_root_of_b == forged_root` is what makes the test
    non-vacuous: it proves the trap was actually set."""
    chain_a = _emit_chain(5, marker="attested")
    chain_b = _emit_chain(5, marker="rewritten")
    checkpoint_a = checkpoint_for_chain(
        chain_a,
        tenant_id=_TENANT,
        chain_schema_version=_SCHEMA_VERSION,
        prev_anchor_root=GENESIS_PREV_ANCHOR_ROOT,
    )
    checkpoint_b = checkpoint_for_chain(
        chain_b,
        tenant_id=_TENANT,
        chain_schema_version=_SCHEMA_VERSION,
        prev_anchor_root=GENESIS_PREV_ANCHOR_ROOT,
    )
    forged_root = checkpoint_root(checkpoint_b)
    assert forged_root != checkpoint_root(checkpoint_a)

    key = _put_envelope(store, checkpoint_a, root=forged_root)
    envelope = json.loads(store.get(key))
    assert envelope["root"] == forged_root  # the trap is set: the stored field points at chain B

    outcome = await _verify(store, _RowRecordSource(_rows_from_records(chain_b)), probe)

    assert outcome.status == STATUS_DIVERGENCE
    assert outcome.reason == REASON_ANCHOR_ROOT_FIELD_INCONSISTENT
    assert outcome.anchor_root == checkpoint_root(checkpoint_a)  # recomputed, not the stored field
    assert outcome.anchor_root != forged_root


async def test_a_rewritten_chain_that_the_in_database_verifier_calls_valid_is_caught(
    anchors_enabled: None, store: LabeledFakeWormAnchorStore, probe: FilesystemAnchorKeyProbe
) -> None:
    """THE headline: the exact adversary the external audit §4 names. The forged chain is fully
    self-consistent — `AuditSink.verify_chain()` (and, by the same recomputation,
    `audit_postgres.verify_chain()`) declares it VALID, because every byte was written by the
    forger. The external anchor is the only thing that disagrees."""
    attested = _emit_chain(5, marker="attested")
    _seal_anchor(store, attested)

    forged_sink = AuditSink()
    forged: list[AuditRecord] = []
    for index in range(5):
        record = AuditRecord(
            agent_id="anchor-verify-fixture",
            tenant_id=_TENANT,
            agent_version="1.0.0",
            action="fixture.acao",
            decision="ALLOW",
            details={"i": index, "marker": "REWRITTEN-BY-A-PRIVILEGED-ACTOR"},
            timestamp=datetime(2026, 3, 1, 12, index, 0, tzinfo=UTC),
        )
        forged_sink.emit(record)
        forged.append(record)
    assert forged_sink.verify_chain() is True  # the in-DB defense is satisfied by the forgery

    outcome = await _verify(store, _RowRecordSource(_rows_from_records(forged)), probe)

    assert outcome.status == STATUS_DIVERGENCE
    assert outcome.reason == REASON_ROOT_MISMATCH
    assert outcome.anchored_head_hash == attested[-1].record_hash
    assert outcome.database_head_hash == forged[-1].record_hash
    assert outcome.anchor_root != outcome.database_root
    assert outcome.anchored_record_count == outcome.database_record_count == 5


async def test_a_removed_record_is_caught_as_a_shortfall(
    anchors_enabled: None, store: LabeledFakeWormAnchorStore, probe: FilesystemAnchorKeyProbe
) -> None:
    """Deletion is the cheapest tamper and the one an in-DB verifier cannot see at all: rows 1..3
    of a truncated chain are still perfectly hash-linked."""
    records = _emit_chain(5)
    _seal_anchor(store, records)

    truncated = _rows_from_records(records)[:3]
    outcome = await _verify(store, _RowRecordSource(truncated), probe)

    assert outcome.status == STATUS_DIVERGENCE
    assert outcome.reason == REASON_RECORD_COUNT_SHORTFALL
    assert outcome.anchored_record_count == 5
    assert outcome.database_record_count == 3
    assert outcome.database_root is None  # there is no window to recompute a root over


async def test_reordered_records_are_caught(
    anchors_enabled: None, store: LabeledFakeWormAnchorStore, probe: FilesystemAnchorKeyProbe
) -> None:
    """Reordering with a full re-chain — content swapped between two positions, then every hash and
    link recomputed, so the result is internally valid and only the anchored root disagrees."""
    records = _emit_chain(5)
    _seal_anchor(store, records)

    swapped_details = [dict(record.details) for record in records]
    swapped_details[1], swapped_details[3] = swapped_details[3], swapped_details[1]
    sink = AuditSink()
    reordered: list[AuditRecord] = []
    for index, details in enumerate(swapped_details):
        record = AuditRecord(
            agent_id="anchor-verify-fixture",
            tenant_id=_TENANT,
            agent_version="1.0.0",
            action="fixture.acao",
            decision="ALLOW",
            details=details,
            timestamp=datetime(2026, 3, 1, 12, index, 0, tzinfo=UTC),
        )
        sink.emit(record)
        reordered.append(record)
    assert sink.verify_chain() is True

    outcome = await _verify(store, _RowRecordSource(_rows_from_records(reordered)), probe)

    assert outcome.status == STATUS_DIVERGENCE
    assert outcome.reason == REASON_ROOT_MISMATCH


async def test_a_row_edited_without_rehashing_is_caught_as_a_discontinuity(
    anchors_enabled: None, store: LabeledFakeWormAnchorStore, probe: FilesystemAnchorKeyProbe
) -> None:
    """The lazy tamper — edit `decision_basis`, leave `record_hash` alone — and a DELIBERATE
    consequence of reusing `audit_postgres._row_to_record`.

    That helper RECOMPUTES the record hash from stored content (`AuditRecord.__post_init__` does),
    while `snapshot_from_rows` walks the STORED links. On an honest chain the two coincide. On an
    edited row they do not, so `checkpoint_for_chain`'s contiguity PRECONDITION fails and the run
    is reported as `CHAIN_DISCONTINUITY` — a divergence, never a match. Documented here because it
    is non-obvious: a reader could otherwise assume this job only ever compares roots."""
    records = _emit_chain(5)
    _seal_anchor(store, records)

    rows = _rows_from_records(records)
    rows[1]["decision_basis"] = {"i": 1, "marker": "EDITED-WITHOUT-REHASHING"}

    outcome = await _verify(store, _RowRecordSource(rows), probe)

    assert outcome.status == STATUS_DIVERGENCE
    assert outcome.reason == REASON_CHAIN_DISCONTINUITY


async def test_a_forked_chain_is_a_divergence_not_a_match(
    anchors_enabled: None, store: LabeledFakeWormAnchorStore, probe: FilesystemAnchorKeyProbe
) -> None:
    """Two rows sharing a `prev_record_hash`: the anti-fork UNIQUE constraint was bypassed. There
    is no single run to walk, so there is no window to recompute — and "cannot recompute" is never
    "clean"."""
    records = _emit_chain(5)
    _seal_anchor(store, records)

    rows = _rows_from_records(records)
    rows.append({**rows[2], "record_hash": "f" * 64})  # a second row claiming the same predecessor

    outcome = await _verify(store, _RowRecordSource(rows), probe)

    assert outcome.status == STATUS_DIVERGENCE
    assert outcome.reason == REASON_CHAIN_FORK
    assert outcome.database_head_hash == rows[2]["prev_record_hash"]


def test_snapshot_from_rows_walks_structurally_and_detects_a_fork() -> None:
    """The row-walk in isolation: order comes from `prev_record_hash` links, never from timestamps
    (`audit_postgres._TAIL_SQL`'s clock-skew argument), and a duplicate predecessor is a fork."""
    records = _emit_chain(4)
    rows = _rows_from_records(records)

    shuffled = [rows[2], rows[0], rows[3], rows[1]]  # storage order is not chain order
    snapshot = snapshot_from_rows(shuffled, schema_version=_SCHEMA_VERSION)
    assert snapshot.fork_at_prev_hash is None
    assert snapshot.total_records == 4
    assert [r.record_hash for r in snapshot.records] == [r.record_hash for r in records]

    forked = snapshot_from_rows([*rows, {**rows[1], "record_hash": "e" * 64}], schema_version=_SCHEMA_VERSION)
    assert forked.fork_at_prev_hash == rows[1]["prev_record_hash"]
    assert forked.records == ()


def test_snapshot_from_rows_reports_an_unreachable_tail_as_a_short_run() -> None:
    """A gap (rows unreachable from genesis) yields a run SHORTER than the row count — the job's
    shortfall branch, not a silent success."""
    records = _emit_chain(5)
    rows = _rows_from_records(records)
    del rows[2]  # everything after the hole is now unreachable from genesis

    snapshot = snapshot_from_rows(rows, schema_version=_SCHEMA_VERSION)
    assert snapshot.total_records == 4
    assert len(snapshot.records) == 2


# =================================================================================================
# V5 — the signature gate
# =================================================================================================


async def test_a_delabeled_signature_is_signature_invalid(
    anchors_enabled: None, store: LabeledFakeWormAnchorStore, probe: FilesystemAnchorKeyProbe
) -> None:
    """Leg 1's fake refuses to be laundered: a signature stripped of its `FAKE-SIG-...` prefix does
    not verify. The consequence HERE is what matters — the job reports tamper, not clean, and never
    reaches the database."""
    records = _emit_chain(3)
    checkpoint = checkpoint_for_chain(
        records,
        tenant_id=_TENANT,
        chain_schema_version=_SCHEMA_VERSION,
        prev_anchor_root=GENESIS_PREV_ANCHOR_ROOT,
    )
    genuine = _signer().sign(checkpoint_bytes(checkpoint))
    _put_envelope(
        store,
        checkpoint,
        signature=AnchorSignature(
            algorithm=genuine.algorithm,
            key_id=genuine.key_id,
            value=genuine.value.removeprefix(FAKE_ANCHOR_SIGNATURE_PREFIX),
        ),
    )

    outcome = await _verify(store, _ExplodingRecordSource(), probe)

    assert outcome.status == STATUS_SIGNATURE_INVALID
    assert outcome.reason == REASON_SIGNATURE_REJECTED
    assert outcome.database_root is None  # the database was never read — the anchor is not evidence


async def test_an_anchor_signed_by_another_key_is_signature_invalid(
    anchors_enabled: None, store: LabeledFakeWormAnchorStore, probe: FilesystemAnchorKeyProbe
) -> None:
    records = _emit_chain(3)
    _seal_anchor(store, records, signer=_signer(secret=b"a-different-secret"))

    outcome = await _verify(store, _ExplodingRecordSource(), probe)

    assert outcome.status == STATUS_SIGNATURE_INVALID
    assert outcome.reason == REASON_SIGNATURE_REJECTED


async def test_a_verifier_that_raises_is_signature_invalid_not_clean(
    anchors_enabled: None, store: LabeledFakeWormAnchorStore, probe: FilesystemAnchorKeyProbe
) -> None:
    """A KMS that times out has NOT verified anything. Fail-closed: an unavailable verifier is
    indistinguishable, from the evidence's point of view, from a rejected signature."""
    records = _emit_chain(3)
    _seal_anchor(store, records)

    class _RaisingVerifier:
        def verify(self, payload: bytes, signature: AnchorSignature) -> bool:
            raise TimeoutError("kms unreachable")

    outcome = await _verify(store, _ExplodingRecordSource(), probe, verifier=_RaisingVerifier())

    assert outcome.status == STATUS_SIGNATURE_INVALID
    assert outcome.reason == REASON_SIGNATURE_VERIFIER_FAILED
    assert outcome.error_type == "TimeoutError"


async def test_an_adversary_who_can_add_to_the_store_can_only_deny_never_clean(
    anchors_enabled: None, store: LabeledFakeWormAnchorStore, probe: FilesystemAnchorKeyProbe
) -> None:
    """A property worth stating as a test: the WORM store refuses OVERWRITES, but adding a NEW key
    is not an overwrite. An adversary can therefore plant a later-stamped anchor that matches their
    rewritten database — and it becomes the selected one, because selection is chronological.

    Without a signing key, the best they achieve is a LOUD denial of verification, never a clean
    verdict. The control inside this test is that the older, genuine anchor WOULD have matched: the
    adversary changed the answer from MATCH to SIGNATURE_INVALID, which is exactly the trade an
    external anchor is supposed to force."""
    records = _emit_chain(5)
    _seal_anchor(store, records, count=3)
    assert (await _verify(store, _RowRecordSource(_rows_from_records(records)), probe)).status == STATUS_MATCH

    planted = AnchorCheckpoint(
        tenant_id=_TENANT,
        chain_head_hash="a" * 64,
        record_count=99,
        window_start=datetime(2030, 1, 1, tzinfo=UTC),
        window_end=datetime(2030, 1, 2, tzinfo=UTC),  # later stamp => selected first
        chain_schema_version=_SCHEMA_VERSION,
        prev_anchor_root="b" * 64,  # a forgery's link is irrelevant: it dies at the signature gate
    )
    _put_envelope(store, planted, signature=_signer(secret=b"the-adversary-has-no-kms-key").sign(b"x"))

    outcome = await _verify(store, _RowRecordSource(_rows_from_records(records)), probe)

    assert outcome.status == STATUS_SIGNATURE_INVALID
    assert outcome.anchor_key is not None
    assert outcome.anchor_key.startswith(f"{_TENANT}/20300102T")


# =================================================================================================
# V3 — the latest-anchor defense, its RED control, and the asymmetry that motivates it
# =================================================================================================


def test_labeled_fake_store_listing_omits_what_get_serves_through_a_symlinked_directory(
    tmp_path: Path,
) -> None:
    """A DISCLOSURE PIN on LEG 1's store, in leg 1's own idiom — it asserts a LIMIT, not a defense.

    `list_keys()` walks with `Path.rglob`, which never descends into a symlinked directory;
    `get()`/`put()` resolve through one happily (they refuse only paths that LEAVE the root). So a
    key can be perfectly readable and simultaneously invisible to the listing. Measured here rather
    than assumed, because the entire corroboration defense rests on this being real.

    If leg 1's store is ever repaired so listing and get agree, THIS TEST GOES RED — and the right
    response is to rewrite this example, NOT to delete the corroboration: an eventually-consistent
    object-store listing produces the identical failure from an entirely different cause."""
    root = tmp_path / "anchors"
    physical = root / "physical"
    physical.mkdir(parents=True)
    (root / _TENANT).symlink_to(physical, target_is_directory=True)
    name = "20270101T000000Z-" + "f" * 64 + ".anchor.json"
    (physical / name).write_bytes(b"AN-ANCHOR-THE-LISTING-CANNOT-SEE")

    store = LabeledFakeWormAnchorStore(root)
    key = f"{_TENANT}/{name}"

    assert store.get(key) == b"AN-ANCHOR-THE-LISTING-CANNOT-SEE"  # get serves it
    assert key not in store.list_keys()  # the listing does not name it
    assert store.list_keys() == (f"physical/{name}",)  # under a DIFFERENT string entirely


def test_filesystem_probe_sees_the_anchor_the_listing_misses(tmp_path: Path) -> None:
    """V12: the corroborator is independent BY CONSTRUCTION — `os.walk(followlinks=True)` against
    `Path.rglob`. A probe built on the listing's own primitive would be a mirror, not a witness."""
    root = tmp_path / "anchors"
    physical = root / "physical"
    physical.mkdir(parents=True)
    (root / _TENANT).symlink_to(physical, target_is_directory=True)
    name = "20270101T000000Z-" + "f" * 64 + ".anchor.json"
    (physical / name).write_bytes(b"payload")

    assert FilesystemAnchorKeyProbe(root).probe_keys(_TENANT) == (f"{_TENANT}/{name}",)
    assert f"{_TENANT}/{name}" not in LabeledFakeWormAnchorStore(root).list_keys()


def test_filesystem_probe_reports_the_tenant_key_even_when_the_physical_alias_is_walked_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """V12 DETERMINISM PIN. `os.walk`'s readdir order is arbitrary across platforms; the probe's
    output must NOT be. The scenario above passed on macOS purely because that OS happened to walk
    the symlinked `<tenant>` alias before its physical target; Linux CI walked `physical` first and
    the probe returned `()` — the newer-anchor listing-suspect defense silently failing to fire on
    the very platform we deploy on.

    This forces the adversarial order (`physical` before the symlinked `<tenant>`) so the assertion
    is deterministic on EVERY platform, not a coin flip. NEUTER: restore the inode guard's `continue`
    so it suppresses file enumeration at the second logical path — this goes RED regardless of OS,
    because both aliases resolve to the same `(st_dev, st_ino)` and the physical route is now first,
    so the tenant-prefixed key is never enumerated."""
    root = tmp_path / "anchors"
    physical = root / "physical"
    physical.mkdir(parents=True)
    (root / _TENANT).symlink_to(physical, target_is_directory=True)
    name = "20270101T000000Z-" + "f" * 64 + ".anchor.json"
    (physical / name).write_bytes(b"payload")

    real_walk = os.walk

    def physical_first_walk(top: Any, *args: Any, **kwargs: Any) -> Any:
        # os.walk honours in-place reordering of `dirnames` to fix recursion order (documented
        # contract), so this pins the exact readdir order that fails on Linux CI: physical first.
        for dirpath, dirnames, filenames in real_walk(top, *args, **kwargs):
            dirnames.sort(key=lambda entry: (entry != "physical", entry))
            yield dirpath, dirnames, filenames

    monkeypatch.setattr(os, "walk", physical_first_walk)

    assert FilesystemAnchorKeyProbe(root).probe_keys(_TENANT) == (f"{_TENANT}/{name}",)


def test_filesystem_probe_terminates_on_a_symlink_cycle(tmp_path: Path) -> None:
    """`followlinks=True` is a foot-gun: a cycle makes `os.walk` recurse forever, and a
    verification job that HANGS is a verification job that silently stops verifying. The
    `(st_dev, st_ino)` guard turns the cycle into a finite (and then disagreeing) enumeration."""
    root = tmp_path / "anchors"
    tenant_dir = root / _TENANT
    tenant_dir.mkdir(parents=True)
    (tenant_dir / "loop").symlink_to(root, target_is_directory=True)
    (tenant_dir / "a.anchor.json").write_bytes(b"payload")

    keys = FilesystemAnchorKeyProbe(root).probe_keys(_TENANT)

    assert f"{_TENANT}/a.anchor.json" in keys
    assert len(keys) < 10  # finite, and it returned at all


def test_filesystem_probe_construction_is_pure(tmp_path: Path) -> None:
    root = tmp_path / "anchors"
    FilesystemAnchorKeyProbe(root)
    assert not root.exists()


def test_filesystem_probe_ignores_the_self_labeling_marker(anchors_enabled: None, tmp_path: Path) -> None:
    """The store's marker file is not an anchor — if the probe counted it, EVERY run would report a
    listing disagreement and the defense would be permanently crying wolf."""
    root = tmp_path / "anchors"
    store = LabeledFakeWormAnchorStore(root)
    _seal_anchor(store, _emit_chain(3))

    assert FilesystemAnchorKeyProbe(root).probe_keys(_TENANT) == tuple(store.list_keys())


async def test_a_newer_anchor_invisible_to_the_listing_does_not_produce_a_clean_verdict(
    anchors_enabled: None, store: LabeledFakeWormAnchorStore, probe: FilesystemAnchorKeyProbe
) -> None:
    """V3, THE defense. The scenario is the dangerous one, end to end:

      - anchor T1 attests records 1..3; anchor T2 attests records 1..5;
      - an actor DELETES records 4 and 5 from the database;
      - and hides T2 from the listing (a symlinked directory, or an eventually-consistent listing).

    Verifying against T1 is genuinely, correctly clean — records 1..3 are untouched — so the
    deletion disappears. The corroborating probe still sees T2, the two enumerations disagree, and
    the job refuses to issue ANY verdict about the database.

    The RED control immediately below demonstrates what happens without the corroboration."""
    records = _emit_chain(5)
    older = _seal_anchor(store, records, count=3)
    newer = _seal_anchor(store, records, count=5)
    assert newer > older  # leg 1's key shape makes lexical order chronological order

    hiding_store = _ListingOmittingStore(store, frozenset({newer}))
    truncated = _RowRecordSource(_rows_from_records(records)[:3])

    outcome = await _verify(hiding_store, truncated, probe)

    assert outcome.status == STATUS_STORE_LISTING_SUSPECT
    assert outcome.reason == REASON_LISTING_PROBE_DISAGREEMENT
    assert outcome.listing_disagreement == (newer,)
    assert outcome.database_root is None  # no verdict about the database was issued
    assert outcome.is_clean is False


async def test_red_control_a_credulous_corroborator_hands_back_the_silent_clean_verdict(
    anchors_enabled: None, store: LabeledFakeWormAnchorStore, probe: FilesystemAnchorKeyProbe
) -> None:
    """THE RED CONTROL for V3 — the same scenario with a corroborator that merely ECHOES the
    listing (i.e. the job as it would behave with no probe seam at all).

    It returns MATCH against the STALE anchor while two audit records are missing from the
    database. This test asserts the disaster on purpose: it is the measurement that makes the
    defense above meaningful rather than decorative. If a future change makes this scenario return
    something other than MATCH, the corroboration is no longer what is doing the work, and this
    file's V3 claim must be re-derived rather than trusted."""
    records = _emit_chain(5)
    older = _seal_anchor(store, records, count=3)
    newer = _seal_anchor(store, records, count=5)

    hiding_store = _ListingOmittingStore(store, frozenset({newer}))
    truncated = _RowRecordSource(_rows_from_records(records)[:3])

    outcome = await _verify(hiding_store, truncated, _EchoingProbe(hiding_store))

    assert outcome.status == STATUS_MATCH  # <- the silent clean verdict, reproduced
    assert outcome.anchor_key == older  # <- against the STALE anchor
    assert outcome.database_record_count == 3  # <- while records 4 and 5 are gone


async def test_a_listing_that_invents_a_key_the_probe_cannot_see_is_also_suspect(
    anchors_enabled: None, store: LabeledFakeWormAnchorStore, probe: FilesystemAnchorKeyProbe
) -> None:
    """Disagreement is refused SYMMETRICALLY. Two independent enumerations that disagree mean at
    least one is wrong, and a verifier that picks a side is guessing — including when the extra key
    is on the listing's side (a stale index, a deleted-but-still-listed object)."""
    records = _emit_chain(3)
    _seal_anchor(store, records)
    phantom = f"{_TENANT}/29991231T235959Z-{'a' * 64}.anchor.json"

    class _InventingStore(_ListingOmittingStore):
        def list_keys(self) -> tuple[str, ...]:
            return (*self._delegate.list_keys(), phantom)

    outcome = await _verify(
        _InventingStore(store, frozenset()), _RowRecordSource(_rows_from_records(records)), probe
    )

    assert outcome.status == STATUS_STORE_LISTING_SUSPECT
    assert outcome.reason == REASON_LISTING_PROBE_DISAGREEMENT
    assert outcome.listing_disagreement == (phantom,)


async def test_a_listed_but_unreadable_anchor_is_suspect(
    anchors_enabled: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    key = f"{_TENANT}/20260101T000000Z-{'b' * 64}.anchor.json"
    unreadable = _UnreadableKeyStore(key)

    outcome = await _verify(unreadable, _ExplodingRecordSource(), _EchoingProbe(unreadable))

    assert outcome.status == STATUS_STORE_LISTING_SUSPECT
    assert outcome.reason == REASON_SELECTED_ANCHOR_UNREADABLE
    assert outcome.anchor_key == key
    assert outcome.error_type == "FileNotFoundError"


async def test_an_unwired_store_is_suspect_not_clean(
    anchors_enabled: None, probe: FilesystemAnchorKeyProbe
) -> None:
    """The production DEFAULT store refuses every operation. "I could not look" must never be
    reported as "I looked and it was fine"."""
    outcome = await _verify(RefusingAnchorStore(), _ExplodingRecordSource(), probe)

    assert outcome.status == STATUS_STORE_LISTING_SUSPECT
    assert outcome.reason == REASON_LISTING_UNAVAILABLE
    assert outcome.error_type == "AnchorStoreUnavailableError"


async def test_a_probe_that_fails_leaves_the_listing_uncorroborated_and_suspect(
    anchors_enabled: None, store: LabeledFakeWormAnchorStore
) -> None:
    """An uncorroborated listing may not produce a verdict — that is the whole position."""
    _seal_anchor(store, _emit_chain(3))

    class _FailingProbe:
        def probe_keys(self, tenant_id: str) -> tuple[str, ...]:
            raise PermissionError("inventory bucket denied")

    outcome = await _verify(store, _ExplodingRecordSource(), _FailingProbe())

    assert outcome.status == STATUS_STORE_LISTING_SUSPECT
    assert outcome.reason == REASON_PROBE_UNAVAILABLE
    assert outcome.error_type == "PermissionError"


# =================================================================================================
# V13 — the in-band anchor chain: contiguity, genesis semantics, structural selection
# =================================================================================================


def _chain_anchors(store: Any, records: list[AuditRecord], counts: Sequence[int]) -> list[str]:
    """Seal one genuinely CHAINED anchor per entry in `counts`, genesis-first. Returns their keys.

    Each anchor attests `records[:count]` and commits to the ROOT of the previous anchor, exactly as
    an owner's periodic writer would. The roots are recomputed here through leg 1's own
    `checkpoint_root`, never read back out of a stored envelope, so the fixture chains the anchors
    by the same value the verifier will recompute.
    """
    keys: list[str] = []
    previous = GENESIS_PREV_ANCHOR_ROOT
    for count in counts:
        keys.append(_seal_anchor(store, records, count=count, prev_anchor_root=previous))
        previous = checkpoint_root(
            checkpoint_for_chain(
                records[:count],
                tenant_id=_TENANT,
                chain_schema_version=_SCHEMA_VERSION,
                prev_anchor_root=previous,
            )
        )
    return keys


def _delete_anchor(store: LabeledFakeWormAnchorStore, key: str) -> None:
    """Remove a stored anchor from BOTH enumerations — the WORM/retention lock having failed.

    Deliberately a real unlink rather than a listing filter: an anchor merely HIDDEN from the
    listing is the `_ListingOmittingStore` scenario the corroborating probe already catches. This is
    the harder case the probe cannot catch — the object is genuinely gone, both enumerations agree
    about that, and only the in-band chain still remembers it existed.
    """
    path = Path(store.root, *key.split("/"))
    path.chmod(0o644)  # the fake store chmods anchors 0o444; the owner may still remove them
    path.unlink()


async def test_a_chained_anchor_set_verifies_clean_and_selects_the_chain_tip(
    anchors_enabled: None, store: LabeledFakeWormAnchorStore, probe: FilesystemAnchorKeyProbe
) -> None:
    """THE NON-VACUITY CONTROL for V13. A whole three-anchor chain must still reach MATCH, and the
    anchor it matches against must be the chain's TIP — otherwise every assertion below would be
    satisfied by a job that simply always reports ANCHOR_CHAIN_BROKEN."""
    records = _emit_chain(6)
    keys = _chain_anchors(store, records, [2, 4, 6])

    outcome = await _verify(store, _RowRecordSource(_rows_from_records(records)), probe)

    assert outcome.status == STATUS_MATCH
    assert outcome.anchor_key == keys[-1]
    assert outcome.anchored_record_count == 6
    assert outcome.anchor_chain_break_keys == ()


async def test_a_single_genesis_anchor_is_a_legal_chain_of_length_one(
    anchors_enabled: None, store: LabeledFakeWormAnchorStore, probe: FilesystemAnchorKeyProbe
) -> None:
    """GENESIS SEMANTICS, stated explicitly. A tenant's first anchor is the one whose signed
    `prev_anchor_root` EQUALS the genesis sentinel — not "the oldest key", and not "the one whose
    predecessor happens to be missing". A lone genesis anchor is a complete chain, and this is what
    every deployment looks like on day one."""
    records = _emit_chain(3)
    key = _seal_anchor(store, records, prev_anchor_root=GENESIS_PREV_ANCHOR_ROOT)

    outcome = await _verify(store, _RowRecordSource(_rows_from_records(records)), probe)

    assert outcome.status == STATUS_MATCH
    assert outcome.anchor_key == key


async def test_a_lone_non_genesis_anchor_has_nowhere_to_start(
    anchors_enabled: None, store: LabeledFakeWormAnchorStore, probe: FilesystemAnchorKeyProbe
) -> None:
    """The other half of the genesis rule, and the reason it may never be INFERRED. This anchor is
    the only one in the store, so "the one whose predecessor is missing" would nominate it as
    genesis and the job would report clean. Its signed `prev_anchor_root` says otherwise: a real
    predecessor existed and is gone. Leg 1 refuses to seal a blank/defaulted `prev_anchor_root`
    precisely so this state cannot be spelled any other way."""
    records = _emit_chain(3)
    _seal_anchor(store, records, prev_anchor_root="a" * 64)

    outcome = await _verify(store, _ExplodingRecordSource(), probe)

    assert outcome.status == STATUS_ANCHOR_CHAIN_BROKEN
    assert outcome.reason == REASON_ANCHOR_CHAIN_NO_GENESIS
    assert outcome.is_clean is False
    assert outcome.exit_code == 17


async def test_an_anchor_deleted_from_the_middle_of_the_chain_is_caught_by_the_evidence(
    anchors_enabled: None, store: LabeledFakeWormAnchorStore, probe: FilesystemAnchorKeyProbe
) -> None:
    """V13, THE defense. The store's retention lock failed (or was never real) and a HISTORICAL
    anchor was removed. Both enumerations agree it is gone, so the corroborating probe — which
    compares two views of what EXISTS — has nothing to disagree about and cannot fire. The database
    is honest, so every content check passes too.

    The only thing that still remembers T2 existed is T3's signed `prev_anchor_root`. That is the
    whole point of chaining the anchors: the anchor store stops being a SET of files that can be
    quietly thinned and becomes a LEDGER whose gaps are visible in the evidence itself.

    The RED control immediately below shows what this scenario returns without the check."""
    records = _emit_chain(6)
    first, middle, tip = _chain_anchors(store, records, [2, 4, 6])
    _delete_anchor(store, middle)

    outcome = await _verify(store, _RowRecordSource(_rows_from_records(records)), probe)

    assert outcome.status == STATUS_ANCHOR_CHAIN_BROKEN
    assert outcome.reason == REASON_ANCHOR_CHAIN_LINK_MISSING
    # The stranded anchor is the one whose predecessor vanished — not the surviving genesis.
    assert outcome.anchor_chain_break_keys == (tip,)
    assert first not in outcome.anchor_chain_break_keys
    # NO verdict about the database was issued: the evidence was not whole.
    assert outcome.database_root is None
    assert outcome.anchor_key is None
    assert outcome.is_clean is False


async def test_red_control_a_credulous_chain_walk_reports_the_thinned_ledger_as_clean(
    anchors_enabled: None,
    store: LabeledFakeWormAnchorStore,
    probe: FilesystemAnchorKeyProbe,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """THE RED CONTROL for V13 — the identical scenario with the contiguity check NEUTERED.

    The replacement is what the job did BEFORE the anchors were chained: trust the store's key order
    and check no links at all. It returns MATCH, and the deleted historical anchor is never
    mentioned by anything. This test asserts the disaster on purpose — it is the measurement that
    makes the defense above meaningful rather than decorative. If a future change makes this
    scenario return something other than MATCH, the contiguity walk is no longer what is doing the
    work and V13's claim must be re-derived rather than trusted."""
    records = _emit_chain(6)
    _first, middle, tip = _chain_anchors(store, records, [2, 4, 6])
    _delete_anchor(store, middle)

    monkeypatch.setattr(audit_anchor_verify, "_walk_anchor_chain", lambda verified: tuple(sorted(verified)))

    outcome = await _verify(store, _RowRecordSource(_rows_from_records(records)), probe)

    assert outcome.status == STATUS_MATCH  # <- the silent clean verdict, reproduced
    assert outcome.anchor_key == tip  # <- against a chain with a hole punched in its history
    assert outcome.anchor_chain_break_keys == ()


async def test_a_foreign_anchor_planted_beside_the_chain_cannot_be_absorbed_into_it(
    anchors_enabled: None, store: LabeledFakeWormAnchorStore, probe: FilesystemAnchorKeyProbe
) -> None:
    """An anchor that is validly signed and validly this tenant's, but was never on this chain — a
    checkpoint from a restored backup, a different environment's store copied in, or a splice
    attempt. Its `prev_anchor_root` names a root nothing here has, so the walk cannot reach it."""
    records = _emit_chain(6)
    _chain_anchors(store, records, [2, 4])
    foreign = _seal_anchor(store, records, count=6, prev_anchor_root="c" * 64)

    outcome = await _verify(store, _ExplodingRecordSource(), probe)

    assert outcome.status == STATUS_ANCHOR_CHAIN_BROKEN
    assert outcome.reason == REASON_ANCHOR_CHAIN_LINK_MISSING
    assert outcome.anchor_chain_break_keys == (foreign,)


async def test_two_anchors_claiming_the_same_predecessor_are_a_fork(
    anchors_enabled: None, store: LabeledFakeWormAnchorStore, probe: FilesystemAnchorKeyProbe
) -> None:
    """Two competing successors to one anchor — the anchor-chain analogue of the duplicate
    `prev_record_hash` that `snapshot_from_rows` calls a forked record chain, and the shape a
    second writer (or a replayed writer) racing the first would leave behind."""
    records = _emit_chain(6)
    (genesis_key,) = _chain_anchors(store, records, [2])
    genesis_root = checkpoint_root(
        checkpoint_for_chain(
            records[:2],
            tenant_id=_TENANT,
            chain_schema_version=_SCHEMA_VERSION,
            prev_anchor_root=GENESIS_PREV_ANCHOR_ROOT,
        )
    )
    branch_a = _seal_anchor(store, records, count=4, prev_anchor_root=genesis_root)
    branch_b = _seal_anchor(store, records, count=5, prev_anchor_root=genesis_root)

    outcome = await _verify(store, _ExplodingRecordSource(), probe)

    assert outcome.status == STATUS_ANCHOR_CHAIN_BROKEN
    assert outcome.reason == REASON_ANCHOR_CHAIN_FORK
    assert outcome.anchor_chain_break_keys == tuple(sorted((branch_a, branch_b)))
    assert genesis_key not in outcome.anchor_chain_break_keys


async def test_two_genesis_anchors_are_the_same_fork_at_the_start_of_the_chain(
    anchors_enabled: None, store: LabeledFakeWormAnchorStore, probe: FilesystemAnchorKeyProbe
) -> None:
    """ "No predecessor" is just another predecessor VALUE, so a second genesis is a second chain and
    lands on the same token. Worth its own test because the alternative — special-casing genesis —
    is exactly the branch that would rot into accepting two of them."""
    records = _emit_chain(6)
    first = _seal_anchor(store, records, count=3, prev_anchor_root=GENESIS_PREV_ANCHOR_ROOT)
    second = _seal_anchor(store, records, count=5, prev_anchor_root=GENESIS_PREV_ANCHOR_ROOT)

    outcome = await _verify(store, _ExplodingRecordSource(), probe)

    assert outcome.status == STATUS_ANCHOR_CHAIN_BROKEN
    assert outcome.reason == REASON_ANCHOR_CHAIN_FORK
    assert outcome.anchor_chain_break_keys == tuple(sorted((first, second)))


async def test_every_anchor_is_signature_verified_before_any_link_is_believed(
    anchors_enabled: None, store: LabeledFakeWormAnchorStore, probe: FilesystemAnchorKeyProbe
) -> None:
    """The chain is only as good as the signatures under it. An unsigned anchor spliced into the
    middle must die at the SIGNATURE gate, never be walked as though its `prev_anchor_root` meant
    something — otherwise an actor who cannot sign could still redraw the chain by writing links.

    Note the outcome names the FORGERY, not the genesis anchor that verified fine before it."""
    records = _emit_chain(6)
    _chain_anchors(store, records, [2])
    forged = _put_envelope(
        store,
        checkpoint_for_chain(
            records[:4],
            tenant_id=_TENANT,
            chain_schema_version=_SCHEMA_VERSION,
            prev_anchor_root="d" * 64,
        ),
        signature=_signer(secret=b"the-adversary-has-no-kms-key").sign(b"x"),
    )

    outcome = await _verify(store, _ExplodingRecordSource(), probe)

    assert outcome.status == STATUS_SIGNATURE_INVALID
    assert outcome.reason == REASON_SIGNATURE_REJECTED
    assert outcome.anchor_key == forged
    assert outcome.anchor_chain_break_keys == ()


def test_the_chain_walk_orders_by_links_not_by_key_ordering(anchors_enabled: None, tmp_path: Path) -> None:
    """ANTI-ORDERING PIN, forced rather than observed. The walk's input is a Mapping, so its
    iteration order is an accident of how the caller built it; the output must not be.

    The keys here are RELABELLED so that lexical order is the exact REVERSE of chain order, and the
    mapping is built in that reversed order too. A walk that leaned on either — the caller's
    insertion order or the keys' sort order — returns the chain backwards and names the wrong tip.
    NEUTER: replace the body of `_walk_anchor_chain` with `tuple(sorted(verified))` and this goes
    RED deterministically, on every platform, with no dependence on filesystem or readdir order."""
    records = _emit_chain(6)
    store = LabeledFakeWormAnchorStore(tmp_path / "anchors")
    keys = _chain_anchors(store, records, [2, 4, 6])
    parsed = [audit_anchor_verify._parse_envelope(store.get(key), tenant_id=_TENANT) for key in keys]
    # `zzz` < `mmm` < `aaa` is false lexically, so chain order (genesis first) is now the reverse
    # of key order — the divergence is injected, not hoped for.
    relabelled = [f"{_TENANT}/zzz", f"{_TENANT}/mmm", f"{_TENANT}/aaa"]
    verified = {
        label: audit_anchor_verify._VerifiedAnchor(
            key=label, parsed=anchor, root=audit_anchor_verify._recomputed_root(anchor)
        )
        for label, anchor in reversed(list(zip(relabelled, parsed, strict=True)))
    }

    assert list(verified) == list(reversed(relabelled))  # the input really is adversarially ordered
    assert audit_anchor_verify._walk_anchor_chain(verified) == tuple(relabelled)
    assert sorted(verified) != list(relabelled)  # ... and lexical order really would be wrong


async def test_selection_follows_the_chain_even_when_the_stored_key_stamps_lie(
    anchors_enabled: None, store: LabeledFakeWormAnchorStore, probe: FilesystemAnchorKeyProbe
) -> None:
    """The same anti-ordering property, end to end through `verify_latest_anchor`.

    Leg 1's key shape puts a UTC `window_end` stamp in the key so a lexical listing reads
    chronologically — but the stamp is part of an object NAME, not part of the signed evidence, and
    two anchors sealed over the same newest record share it outright. Here the stamps are outright
    wrong: the genesis anchor is stored under a 2099 key and its successor under a 2026 one, so a
    lexical `max` selects the OLDER anchor and reports MATCH against a 3-record prefix while the
    chain attests 5. Structural selection reads the links and picks the real tip."""
    records = _emit_chain(5)
    genesis = checkpoint_for_chain(
        records[:3],
        tenant_id=_TENANT,
        chain_schema_version=_SCHEMA_VERSION,
        prev_anchor_root=GENESIS_PREV_ANCHOR_ROOT,
    )
    successor = checkpoint_for_chain(
        records,
        tenant_id=_TENANT,
        chain_schema_version=_SCHEMA_VERSION,
        prev_anchor_root=checkpoint_root(genesis),
    )
    misstamped_genesis = f"{_TENANT}/20990101T000000Z-{checkpoint_root(genesis)}.anchor.json"
    misstamped_successor = f"{_TENANT}/20260101T000000Z-{checkpoint_root(successor)}.anchor.json"
    for key, checkpoint in ((misstamped_genesis, genesis), (misstamped_successor, successor)):
        store.put(
            key,
            canonical_bytes(
                build_envelope(
                    checkpoint,
                    checkpoint_root(checkpoint),
                    _signer().sign(checkpoint_bytes(checkpoint)),
                )
            ),
        )

    outcome = await _verify(store, _RowRecordSource(_rows_from_records(records)), probe)

    assert max(store.list_keys()) == misstamped_genesis  # lexical order would pick the OLDER one
    assert outcome.status == STATUS_MATCH
    assert outcome.anchor_key == misstamped_successor  # the links win
    assert outcome.anchored_record_count == 5


def test_the_chain_walk_terminates_and_reports_the_leftovers(anchors_enabled: None, tmp_path: Path) -> None:
    """The walk pops each entry out of its index as it consumes it, so it cannot revisit a key and
    the loop is finite by construction rather than by a counter somebody has to keep correct. What
    remains in the index when it stops IS the unreached set — the leftovers are not recomputed by a
    second pass that could disagree with the first."""
    records = _emit_chain(6)
    store = LabeledFakeWormAnchorStore(tmp_path / "anchors")
    keys = _chain_anchors(store, records, [2, 4, 6])
    parsed = {key: audit_anchor_verify._parse_envelope(store.get(key), tenant_id=_TENANT) for key in keys}
    verified = {
        key: audit_anchor_verify._VerifiedAnchor(
            key=key, parsed=anchor, root=audit_anchor_verify._recomputed_root(anchor)
        )
        for key, anchor in parsed.items()
    }

    assert audit_anchor_verify._walk_anchor_chain(verified) == tuple(keys)

    del verified[keys[1]]  # punch out the middle link
    with pytest.raises(audit_anchor_verify._AnchorChainBrokenError) as excinfo:
        audit_anchor_verify._walk_anchor_chain(verified)
    assert excinfo.value.reason == REASON_ANCHOR_CHAIN_LINK_MISSING
    assert excinfo.value.keys == (keys[2],)


# =================================================================================================
# V14 — the schema-version cross-check (G1b)
# =================================================================================================


async def test_a_migration_the_anchor_never_saw_is_surfaced_not_silently_matched(
    anchors_enabled: None, store: LabeledFakeWormAnchorStore, probe: FilesystemAnchorKeyProbe
) -> None:
    """V14, THE defense. The chain CONTENT agrees — same records, same root — but the database has
    since been migrated past the revision the anchor attests. Before this check the run reported
    MATCH, because the database side is deliberately recomputed with the ANCHOR's own
    `chain_schema_version` to isolate the comparison to content; that isolation is what made the
    schema drift invisible, and this is the check that puts it back.

    Both revisions are reported, so an operator sees which way the drift runs without re-reading
    either side."""
    records = _emit_chain(4)
    _seal_anchor(store, records)
    migrated = _RowRecordSource(_rows_from_records(records), schema_version="0009_alguma_migracao")

    outcome = await _verify(store, migrated, probe)

    assert outcome.status == STATUS_SCHEMA_VERSION_MISMATCH
    assert outcome.reason == REASON_SCHEMA_VERSION_MISMATCH
    assert outcome.anchored_schema_version == _SCHEMA_VERSION
    assert outcome.database_schema_version == "0009_alguma_migracao"
    assert outcome.is_clean is False
    assert outcome.exit_code == 18
    # The content really did agree — this is a schema finding, not a disguised content divergence.
    assert outcome.database_root == outcome.anchor_root


async def test_red_control_a_schema_cross_check_that_always_agrees_calls_a_stale_anchor_clean(
    anchors_enabled: None,
    store: LabeledFakeWormAnchorStore,
    probe: FilesystemAnchorKeyProbe,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """THE RED CONTROL for V14 — the identical scenario with the comparison NEUTERED to always
    agree, which is precisely what deleting the `if` would do. It returns MATCH against an anchor
    that attests a schema the database no longer has. Asserted on purpose, as the measurement that
    makes the check above load-bearing."""
    records = _emit_chain(4)
    _seal_anchor(store, records)
    migrated = _RowRecordSource(_rows_from_records(records), schema_version="0009_alguma_migracao")

    monkeypatch.setattr(audit_anchor_verify, "_schema_versions_agree", lambda anchored, live: True)

    outcome = await _verify(store, migrated, probe)

    assert outcome.status == STATUS_MATCH  # <- the stale anchor, reported clean
    assert outcome.database_schema_version == "0009_alguma_migracao"


async def test_a_live_schema_version_the_source_cannot_establish_is_db_error_not_clean(
    anchors_enabled: None, store: LabeledFakeWormAnchorStore, probe: FilesystemAnchorKeyProbe
) -> None:
    """ "I could not establish the live revision" is not "the revisions agree". It is a partial read
    of the database, so it lands on DB_ERROR — under its own reason token, because an operator sent
    to the network by a `DATABASE_UNREACHABLE` would be looking in the wrong place when the real
    answer is that the tenant schema has no migration state."""
    records = _emit_chain(4)
    _seal_anchor(store, records)
    unknown = _RowRecordSource(_rows_from_records(records), schema_version=None)

    outcome = await _verify(store, unknown, probe)

    assert outcome.status == STATUS_DB_ERROR
    assert outcome.reason == REASON_SCHEMA_VERSION_UNREADABLE
    assert outcome.database_schema_version is None
    assert outcome.anchored_schema_version == _SCHEMA_VERSION
    assert outcome.is_clean is False


async def test_a_content_divergence_is_never_masked_by_a_stale_schema_version(
    anchors_enabled: None, store: LabeledFakeWormAnchorStore, probe: FilesystemAnchorKeyProbe
) -> None:
    """WHY THE CROSS-CHECK IS THE LAST GATE. Both faults are present: records were deleted AND the
    schema moved. The urgent incident is the deletion, and reporting the milder schema finding would
    hand an operator "re-anchor and move on" for what is actually a tamper.

    Placing the check last means it can only ever downgrade a would-be MATCH — the one direction
    that cannot hide anything."""
    records = _emit_chain(5)
    _seal_anchor(store, records)
    truncated_and_migrated = _RowRecordSource(
        _rows_from_records(records)[:3], schema_version="0009_alguma_migracao"
    )

    outcome = await _verify(store, truncated_and_migrated, probe)

    assert outcome.status == STATUS_DIVERGENCE
    assert outcome.reason == REASON_RECORD_COUNT_SHORTFALL


def test_snapshot_from_rows_refuses_to_default_the_schema_version() -> None:
    """FAIL-CLOSED at the seam. `schema_version` is keyword-only and has no default, so a record
    source that never thought about the cross-check cannot silently hand back `None` — which is a
    CLAIM ("I looked and could not establish it") that only a source is entitled to make."""
    rows = _rows_from_records(_emit_chain(2))

    with pytest.raises(TypeError, match="schema_version"):
        snapshot_from_rows(rows)  # type: ignore[call-arg]

    assert snapshot_from_rows(rows, schema_version=None).schema_version is None


def test_the_matching_schema_version_is_the_one_the_writer_seals() -> None:
    """The two sides of the cross-check must be the same KIND of string, or it would compare a
    label against a revision and alarm forever. Leg 1's checkpoint documents `chain_schema_version`
    as the tenant's live `alembic_version.version_num`; this pins that the verifier compares it by
    exact equality against exactly that, with no normalization in between."""
    assert audit_anchor_verify._schema_versions_agree("0005_audit_emit_dedup", "0005_audit_emit_dedup")
    assert not audit_anchor_verify._schema_versions_agree("0005_audit_emit_dedup", "0005")
    assert not audit_anchor_verify._schema_versions_agree("0005_audit_emit_dedup", "0006_x")


# =================================================================================================
# V4 — NO_ANCHOR and ANCHOR_UNREADABLE
# =================================================================================================


async def test_an_empty_store_is_no_anchor_not_clean(
    anchors_enabled: None, store: LabeledFakeWormAnchorStore, probe: FilesystemAnchorKeyProbe
) -> None:
    """ "Nothing has ever been attested" is the opposite of "everything checks out"."""
    outcome = await _verify(store, _ExplodingRecordSource(), probe)

    assert outcome.status == STATUS_NO_ANCHOR
    assert outcome.is_clean is False
    assert outcome.exit_code != 0


async def test_another_tenants_anchor_does_not_count_as_this_tenants(
    anchors_enabled: None, store: LabeledFakeWormAnchorStore, probe: FilesystemAnchorKeyProbe
) -> None:
    """Selection is prefix-scoped. A busy shared store must not make an unanchored tenant look
    anchored — nor make it borrow another tenant's verdict."""
    _seal_anchor(store, _emit_chain(3))

    outcome = await _verify(store, _ExplodingRecordSource(), probe, tenant_id="outro_tenant")

    assert outcome.status == STATUS_NO_ANCHOR


@pytest.mark.parametrize(
    ("payload", "expected_reason"),
    [
        (b"not json at all", REASON_ENVELOPE_NOT_JSON),
        (b"[1,2,3]", REASON_ENVELOPE_SHAPE_UNKNOWN),
        (b'{"root":"x"}', REASON_ENVELOPE_SHAPE_UNKNOWN),
        (b"{}", REASON_ENVELOPE_SHAPE_UNKNOWN),
    ],
    ids=["not-json", "not-an-object", "missing-keys", "empty-object"],
)
async def test_bytes_that_are_not_an_anchor_are_unreadable_not_clean(
    anchors_enabled: None,
    store: LabeledFakeWormAnchorStore,
    probe: FilesystemAnchorKeyProbe,
    payload: bytes,
    expected_reason: str,
) -> None:
    """A truncated or garbled anchor file is its OWN incident: reporting it as SIGNATURE_INVALID
    would send an operator to review key compromise when the evidence says storage corruption."""
    store.put(f"{_TENANT}/20260101T000000Z-{'c' * 64}.anchor.json", payload)

    outcome = await _verify(store, _ExplodingRecordSource(), probe)

    assert outcome.status == STATUS_ANCHOR_UNREADABLE
    assert outcome.reason == expected_reason


async def test_an_envelope_with_an_extra_key_is_refused_rather_than_half_read(
    anchors_enabled: None, store: LabeledFakeWormAnchorStore, probe: FilesystemAnchorKeyProbe
) -> None:
    """EXACT key-set equality, not "contains what I need": quietly reading the four familiar keys
    out of an unfamiliar document is how a verifier ends up attesting to something nobody
    designed."""
    records = _emit_chain(3)
    checkpoint = checkpoint_for_chain(
        records,
        tenant_id=_TENANT,
        chain_schema_version=_SCHEMA_VERSION,
        prev_anchor_root=GENESIS_PREV_ANCHOR_ROOT,
    )
    root = checkpoint_root(checkpoint)
    envelope = build_envelope(checkpoint, root, _signer().sign(checkpoint_bytes(checkpoint)))
    envelope["extra"] = "smuggled"
    store.put(anchor_key(checkpoint, root), canonical_bytes(envelope))

    outcome = await _verify(store, _ExplodingRecordSource(), probe)

    assert outcome.status == STATUS_ANCHOR_UNREADABLE
    assert outcome.reason == REASON_ENVELOPE_SHAPE_UNKNOWN


async def test_a_future_anchor_format_is_refused_not_half_understood(
    anchors_enabled: None, store: LabeledFakeWormAnchorStore, probe: FilesystemAnchorKeyProbe
) -> None:
    """A future (v3) anchor must be REFUSED by this v2 verifier. Half-understanding a future format
    is how a format bump silently turns every verification into a false clean. (The v1->v2 bump this
    follow-up made is the same discipline one step back: a v1 anchor is now equally refused.)"""
    records = _emit_chain(3)
    checkpoint = checkpoint_for_chain(
        records,
        tenant_id=_TENANT,
        chain_schema_version=_SCHEMA_VERSION,
        prev_anchor_root=GENESIS_PREV_ANCHOR_ROOT,
    )
    root = checkpoint_root(checkpoint)
    envelope = build_envelope(checkpoint, root, _signer().sign(checkpoint_bytes(checkpoint)))
    envelope["anchor_format"] = "maezo.audit-anchor.v3"
    store.put(anchor_key(checkpoint, root), canonical_bytes(envelope))

    outcome = await _verify(store, _ExplodingRecordSource(), probe)

    assert outcome.status == STATUS_ANCHOR_UNREADABLE
    assert outcome.reason == REASON_ENVELOPE_FORMAT_UNKNOWN
    assert ANCHOR_FORMAT == "maezo.audit-anchor.v2"  # the constant this verifier reads


async def test_an_anchor_whose_signed_tenant_disagrees_with_its_key_is_unreadable(
    anchors_enabled: None, store: LabeledFakeWormAnchorStore, probe: FilesystemAnchorKeyProbe
) -> None:
    """The key prefix said one tenant and the SIGNED content says another. Whatever produced that,
    the envelope is not this tenant's evidence — and it must not be verified against this tenant's
    chain."""
    records = _emit_chain(3)
    checkpoint = checkpoint_for_chain(
        records,
        tenant_id="outro_tenant",
        chain_schema_version=_SCHEMA_VERSION,
        prev_anchor_root=GENESIS_PREV_ANCHOR_ROOT,
    )
    root = checkpoint_root(checkpoint)
    envelope = build_envelope(checkpoint, root, _signer().sign(checkpoint_bytes(checkpoint)))
    # Stored under THIS tenant's prefix while attesting another tenant's chain.
    store.put(f"{_TENANT}/20260101T000000Z-{root}.anchor.json", canonical_bytes(envelope))

    outcome = await _verify(store, _ExplodingRecordSource(), probe)

    assert outcome.status == STATUS_ANCHOR_UNREADABLE
    assert outcome.reason == REASON_CHECKPOINT_TENANT_MISMATCH


# =================================================================================================
# V4/V7 — DB_ERROR, and the secret hygiene of its report
# =================================================================================================


async def test_an_unreachable_database_is_db_error_not_clean(
    anchors_enabled: None, store: LabeledFakeWormAnchorStore, probe: FilesystemAnchorKeyProbe
) -> None:
    records = _emit_chain(3)
    _seal_anchor(store, records)

    outcome = await _verify(store, _RaisingRecordSource(ConnectionResetError("boom")), probe)

    assert outcome.status == STATUS_DB_ERROR
    assert outcome.reason == REASON_DATABASE_UNREACHABLE
    assert outcome.error_type == "ConnectionResetError"
    assert outcome.is_clean is False
    assert outcome.database_root is None


async def test_a_database_error_never_carries_the_exception_text(
    anchors_enabled: None, store: LabeledFakeWormAnchorStore, probe: FilesystemAnchorKeyProbe
) -> None:
    """A driver's connection error quotes the DSN, and a DSN carries a password. The outcome and
    the log event carry the exception CLASS name only — diagnostic enough to route an incident,
    and reproducing with the CLI recovers the detail on a human's terminal instead of in a log
    aggregator."""
    records = _emit_chain(3)
    _seal_anchor(store, records)
    leaky = ConnectionRefusedError("could not connect to postgresql://maezo:hunter2@db:5432/maezo")

    with structlog.testing.capture_logs() as logs:
        outcome = await _verify(store, _RaisingRecordSource(leaky), probe)

    rendered = json.dumps(outcome.to_json_mapping()) + json.dumps(logs, default=str)
    assert "hunter2" not in rendered
    assert "postgresql://" not in rendered
    assert outcome.error_type == "ConnectionRefusedError"


# =================================================================================================
# V7 — the divergence event: loud, structured, hashes only
# =================================================================================================


async def test_divergence_event_carries_no_record_content(
    anchors_enabled: None, store: LabeledFakeWormAnchorStore, probe: FilesystemAnchorKeyProbe
) -> None:
    """The loud event, pinned: name, level, and the exact key set — and NOTHING from the records.

    A divergence report is a statement about ROOTS. Including the diverging records would put the
    very content under investigation into an alerting sink, which is where PHI goes to be copied."""
    attested = _emit_chain(3, marker="attested")
    _seal_anchor(store, attested)
    diverged = _emit_chain(3, marker="MARCADOR-DE-CONTEUDO-SIGILOSO")

    with structlog.testing.capture_logs() as logs:
        outcome = await _verify(store, _RowRecordSource(_rows_from_records(diverged)), probe)

    assert outcome.status == STATUS_DIVERGENCE
    events = [entry for entry in logs if entry.get("event") == "audit_anchor_divergence_detected"]
    assert len(events) == 1
    event = events[0]
    assert event["log_level"] == "error"
    assert frozenset(event) == frozenset({"event", "log_level"}) | frozenset(_EXPECTED_JSON_KEYS)

    rendered = json.dumps(event, default=str)
    assert "MARCADOR-DE-CONTEUDO-SIGILOSO" not in rendered
    assert "anchor-verify-fixture" not in rendered  # no agent id
    assert "fixture.acao" not in rendered  # no action
    assert "decision_basis" not in rendered and "details" not in rendered


@pytest.mark.parametrize("status", sorted(ALL_STATUSES))
def test_every_status_logs_under_its_own_pinned_event_name(status: str) -> None:
    """HARDCODED names. Provenance: one event per status so an alerting rule matches an EVENT
    rather than parsing a field — `audit_anchor_divergence_detected` is the one leg 3's drills and
    the runbook consume."""
    expected = {
        "MATCH": ("audit_anchor_verification_match", "info"),
        "DIVERGENCE": ("audit_anchor_divergence_detected", "error"),
        "NO_ANCHOR": ("audit_anchor_absent", "warning"),
        "SIGNATURE_INVALID": ("audit_anchor_signature_invalid", "error"),
        "STORE_LISTING_SUSPECT": ("audit_anchor_store_listing_suspect", "error"),
        "DB_ERROR": ("audit_anchor_database_unreadable", "error"),
        "ANCHOR_UNREADABLE": ("audit_anchor_envelope_unreadable", "error"),
        "ANCHOR_CHAIN_BROKEN": ("audit_anchor_chain_broken", "error"),
        "SCHEMA_VERSION_MISMATCH": ("audit_anchor_schema_version_mismatch", "error"),
        "DISABLED": ("audit_anchor_verification_skipped_disabled", "debug"),
    }
    assert (EVENT_BY_STATUS[status], LEVEL_BY_STATUS[status]) == expected[status]


#: HARDCODED wire shape. Provenance: the leg-2 brief pins the CLI's JSON output so leg 3's drills
#: and the runbook can consume it; every key is always present (never omitted when null) so a
#: MISSING field reads as a contract break rather than as an absent value.
_EXPECTED_JSON_KEYS: Final[frozenset[str]] = frozenset(
    {
        "status",
        "tenant_id",
        "reason",
        "anchor_key",
        "anchor_root",
        "database_root",
        "anchored_record_count",
        "database_record_count",
        "anchored_head_hash",
        "database_head_hash",
        "signature_key_id",
        "error_type",
        "anchored_schema_version",
        "database_schema_version",
        "listing_disagreement",
        "anchor_chain_break_keys",
    }
)

#: The two list-valued members of the wire shape. Stated once, here, so the scalar-only PHI
#: assertion below cannot be quietly widened by adding a key to the exemption inline.
_JSON_LIST_KEYS: Final[frozenset[str]] = frozenset({"listing_disagreement", "anchor_chain_break_keys"})


def test_outcome_json_shape_is_pinned() -> None:
    """Set-equality on the keys, plus a scalar-only assertion on the values — that second half is
    the PHI guarantee (no nested free-form structure can be smuggled into a report). The two
    schema-version members are migration revisions (`"0005_audit_emit_dedup"`), which are
    organizational identifiers exactly as `tenant_id` is, never record content."""
    mapping = AnchorVerificationOutcome(
        status=STATUS_MATCH,
        tenant_id=_TENANT,
        listing_disagreement=("a/b",),
        anchor_chain_break_keys=("a/c",),
    ).to_json_mapping()

    assert frozenset(mapping) == _EXPECTED_JSON_KEYS
    for key, value in mapping.items():
        if key in _JSON_LIST_KEYS:
            assert isinstance(value, list)
            assert all(isinstance(item, str) for item in value)
        else:
            assert value is None or isinstance(value, str | int)


async def test_the_json_shape_is_identical_for_every_outcome(
    anchors_enabled: None, store: LabeledFakeWormAnchorStore, probe: FilesystemAnchorKeyProbe
) -> None:
    """MATCH, DIVERGENCE and an absence-state must produce the SAME keys — a consumer that has to
    branch on which fields exist ends up branching wrong on the one that matters."""
    records = _emit_chain(3)
    _seal_anchor(store, records)
    rows = _rows_from_records(records)

    matched = await _verify(store, _RowRecordSource(rows), probe)
    diverged = await _verify(store, _RowRecordSource(rows[:1]), probe)
    absent = await _verify(
        LabeledFakeWormAnchorStore(store.root / "empty"),
        _ExplodingRecordSource(),
        FilesystemAnchorKeyProbe(store.root / "empty"),
    )

    assert matched.status == STATUS_MATCH
    assert diverged.status == STATUS_DIVERGENCE
    assert absent.status == STATUS_NO_ANCHOR
    for outcome in (matched, diverged, absent):
        assert frozenset(outcome.to_json_mapping()) == _EXPECTED_JSON_KEYS


# =================================================================================================
# V11 — continuous mode
# =================================================================================================


async def test_loop_runs_the_requested_iterations_and_sleeps_between_them(
    anchors_enabled: None, store: LabeledFakeWormAnchorStore, probe: FilesystemAnchorKeyProbe
) -> None:
    records = _emit_chain(3)
    _seal_anchor(store, records)
    sleeper = _RecordingSleep()

    outcomes = await run_verification_loop(
        tenant_id=_TENANT,
        store=store,
        verifier=_signer(),
        key_probe=probe,
        records=_RowRecordSource(_rows_from_records(records)),
        interval_seconds=30.0,
        max_iterations=3,
        sleep=sleeper,
    )

    assert [o.status for o in outcomes] == [STATUS_MATCH] * 3
    assert sleeper.delays == [30.0, 30.0]  # between iterations, never after the last one


@pytest.mark.parametrize("interval", [0.0, -1.0, -0.001])
async def test_loop_refuses_a_non_positive_interval(
    anchors_enabled: None, store: LabeledFakeWormAnchorStore, probe: FilesystemAnchorKeyProbe, interval: float
) -> None:
    """A zero interval is a busy loop reading the whole audit chain as fast as Postgres answers.
    Refused at the door rather than discovered in production."""
    with pytest.raises(ValueError, match="must be > 0"):
        await run_verification_loop(
            tenant_id=_TENANT,
            store=store,
            verifier=_signer(),
            key_probe=probe,
            records=_ExplodingRecordSource(),
            interval_seconds=interval,
            max_iterations=1,
            sleep=_ExplodingSleep(),
        )


async def test_loop_keeps_verifying_after_a_divergence(
    anchors_enabled: None, store: LabeledFakeWormAnchorStore, probe: FilesystemAnchorKeyProbe
) -> None:
    """A divergence does not resolve itself, and stopping would silence the alarm after the first
    page — precisely when a human most needs the next data point. Suppression is an alerting-sink
    concern, deliberately not decided here."""
    _seal_anchor(store, _emit_chain(3, marker="attested"))
    diverged = _rows_from_records(_emit_chain(3, marker="diverged"))
    seen: list[str] = []

    outcomes = await run_verification_loop(
        tenant_id=_TENANT,
        store=store,
        verifier=_signer(),
        key_probe=probe,
        records=_RowRecordSource(diverged),
        interval_seconds=1.0,
        max_iterations=3,
        on_outcome=lambda outcome: seen.append(outcome.status),
        sleep=_RecordingSleep(),
    )

    assert [o.status for o in outcomes] == [STATUS_DIVERGENCE] * 3
    assert seen == [STATUS_DIVERGENCE] * 3  # every one reported, not just the first


async def test_loop_does_not_swallow_a_failing_alert_sink(
    anchors_enabled: None, store: LabeledFakeWormAnchorStore, probe: FilesystemAnchorKeyProbe
) -> None:
    """A verification job whose alarm channel is broken must not keep running quietly green."""
    records = _emit_chain(3)
    _seal_anchor(store, records)

    def _broken_sink(outcome: AnchorVerificationOutcome) -> None:
        raise RuntimeError("pagerduty is down")

    with pytest.raises(RuntimeError, match="pagerduty is down"):
        await run_verification_loop(
            tenant_id=_TENANT,
            store=store,
            verifier=_signer(),
            key_probe=probe,
            records=_RowRecordSource(_rows_from_records(records)),
            interval_seconds=1.0,
            max_iterations=3,
            on_outcome=_broken_sink,
            sleep=_RecordingSleep(),
        )


async def test_loop_stops_when_the_flag_is_turned_off_mid_flight(
    anchors_enabled: None,
    monkeypatch: pytest.MonkeyPatch,
    store: LabeledFakeWormAnchorStore,
    probe: FilesystemAnchorKeyProbe,
) -> None:
    """Disabling is effective without a restart, and the last outcome says DISABLED rather than
    leaving a reader to infer why the stream of verdicts stopped."""
    records = _emit_chain(3)
    _seal_anchor(store, records)

    def _disable_after_first(outcome: AnchorVerificationOutcome) -> None:
        monkeypatch.delenv(ANCHOR_VERIFY_ENABLED_ENV, raising=False)

    outcomes = await run_verification_loop(
        tenant_id=_TENANT,
        store=store,
        verifier=_signer(),
        key_probe=probe,
        records=_RowRecordSource(_rows_from_records(records)),
        interval_seconds=1.0,
        max_iterations=None,  # would run forever if the flag were not honoured
        on_outcome=_disable_after_first,
        sleep=_RecordingSleep(),
    )

    assert [o.status for o in outcomes] == [STATUS_MATCH, STATUS_DISABLED]


@pytest.mark.parametrize("iterations", [0, -1])
async def test_loop_refuses_a_meaningless_iteration_count(
    anchors_enabled: None, store: LabeledFakeWormAnchorStore, probe: FilesystemAnchorKeyProbe, iterations: int
) -> None:
    with pytest.raises(ValueError, match="max_iterations"):
        await run_verification_loop(
            tenant_id=_TENANT,
            store=store,
            verifier=_signer(),
            key_probe=probe,
            records=_ExplodingRecordSource(),
            interval_seconds=1.0,
            max_iterations=iterations,
            sleep=_ExplodingSleep(),
        )


# =================================================================================================
# V10 — the offline CLI
# =================================================================================================


def _verdict(capsys: pytest.CaptureFixture[str]) -> dict[str, Any]:
    """Read the CLI's verdict per its documented contract: the LAST LINE of stdout, one line.

    Not a convenience wrapper — this IS the contract. The repo renders structlog events to stdout
    (`platform/observability.py`'s `PrintLoggerFactory`), so a machine consumer must be able to
    find the verdict in a stream that also carries console log lines. The assertions below are the
    pin: exactly one line, valid JSON, last."""
    captured = capsys.readouterr().out
    lines = [line for line in captured.splitlines() if line.strip()]
    assert lines, "the CLI printed nothing"
    payload: dict[str, Any] = json.loads(lines[-1])
    assert "\n" not in json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return payload


def _cli_argv(tmp_path: Path, tenant: str = _TENANT) -> list[str]:
    return [
        "--dsn",
        "postgresql+asyncpg://maezo:maezo@localhost:5433/maezo",
        "--tenant",
        tenant,
        "--store-root",
        str(tmp_path / "anchors"),
        "--fake-verifier-key-label",
        _FIXTURE_KEY_LABEL,
    ]


def test_cli_exit_codes_are_pinned(
    anchors_enabled: None,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """MATCH exits 0; a divergence exits its own class code. A CI job that only checks `!= 0` still
    works, and one that wants to distinguish "no anchor yet" from "the chain was rewritten" can."""
    monkeypatch.setenv("MAEZO_AUDIT_ANCHOR_FAKE_SECRET", _FIXTURE_SECRET.decode("utf-8"))
    records = _emit_chain(4)
    _seal_anchor(LabeledFakeWormAnchorStore(tmp_path / "anchors"), records)
    rows = _rows_from_records(records)

    assert _cli(_cli_argv(tmp_path), record_source_factory=lambda dsn: _RowRecordSource(rows)) == 0
    assert _verdict(capsys)["status"] == "MATCH"

    code = _cli(_cli_argv(tmp_path), record_source_factory=lambda dsn: _RowRecordSource(rows[:2]))
    assert code == EXIT_CODE_BY_STATUS[STATUS_DIVERGENCE] == 10
    assert _verdict(capsys)["reason"] == REASON_RECORD_COUNT_SHORTFALL


def test_cli_json_shape_is_pinned(
    anchors_enabled: None,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The wire contract leg 3's drills consume: exactly these keys, on stdout, parseable."""
    monkeypatch.setenv("MAEZO_AUDIT_ANCHOR_FAKE_SECRET", _FIXTURE_SECRET.decode("utf-8"))
    records = _emit_chain(4)
    _seal_anchor(LabeledFakeWormAnchorStore(tmp_path / "anchors"), records)

    _cli(
        _cli_argv(tmp_path),
        record_source_factory=lambda dsn: _RowRecordSource(_rows_from_records(records)),
    )

    payload = _verdict(capsys)
    assert frozenset(payload) == _EXPECTED_JSON_KEYS
    assert payload["tenant_id"] == _TENANT
    assert payload["anchor_root"] == payload["database_root"]


def test_the_cli_verdict_is_the_last_line_of_stdout_and_is_one_line(
    anchors_enabled: None,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """THE output contract, pinned as a consequence rather than as prose.

    This command emits a structured event for its own verdict, and the repo renders structlog to
    STDOUT (`platform/observability.py`'s `PrintLoggerFactory`) — so the verdict shares a stream
    with console log lines. A pretty-printed, multi-line JSON document interleaved with them is not
    parseable, which is exactly the failure this test was written after observing. Compact,
    single-line, last: `... | tail -n 1 | jq` works, and if a future change reintroduces
    `indent=2`, this goes red."""
    monkeypatch.setenv("MAEZO_AUDIT_ANCHOR_FAKE_SECRET", _FIXTURE_SECRET.decode("utf-8"))
    records = _emit_chain(4)
    _seal_anchor(LabeledFakeWormAnchorStore(tmp_path / "anchors"), records)

    _cli(
        _cli_argv(tmp_path),
        record_source_factory=lambda dsn: _RowRecordSource(_rows_from_records(records)),
    )

    lines = [line for line in capsys.readouterr().out.splitlines() if line.strip()]
    assert len(lines) > 1, "the log event and the verdict really do share stdout"
    assert json.loads(lines[-1])["status"] == "MATCH"  # the LAST line parses on its own
    with pytest.raises(json.JSONDecodeError):
        json.loads("\n".join(lines))  # ...and the whole stream deliberately does not


def test_cli_reports_no_anchor_for_an_empty_store(
    anchors_enabled: None,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("MAEZO_AUDIT_ANCHOR_FAKE_SECRET", _FIXTURE_SECRET.decode("utf-8"))

    code = _cli(_cli_argv(tmp_path), record_source_factory=lambda dsn: _ExplodingRecordSource())

    assert code == EXIT_CODE_BY_STATUS[STATUS_NO_ANCHOR]
    assert _verdict(capsys)["status"] == "NO_ANCHOR"


def test_cli_reports_disabled_rather_than_pretending_to_have_checked(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Run in CI with the flag unset and the job says so, loudly and non-zero."""
    monkeypatch.delenv(ANCHOR_VERIFY_ENABLED_ENV, raising=False)
    monkeypatch.setenv("MAEZO_AUDIT_ANCHOR_FAKE_SECRET", _FIXTURE_SECRET.decode("utf-8"))

    code = _cli(_cli_argv(tmp_path), record_source_factory=lambda dsn: _ExplodingRecordSource())

    assert code == EXIT_CODE_BY_STATUS[STATUS_DISABLED]
    assert _verdict(capsys)["status"] == "DISABLED"


def test_cli_refuses_without_a_verifier_secret(
    anchors_enabled: None,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """No verifier => no verification. The CLI REFUSES rather than falling back to "skip the
    signature check": a clean-looking line of JSON that skipped the signature is worse than no
    output, and its exit code is distinct from every verdict so it can never be mistaken for one."""
    monkeypatch.delenv("MAEZO_AUDIT_ANCHOR_FAKE_SECRET", raising=False)

    code = _cli(_cli_argv(tmp_path), record_source_factory=lambda dsn: _ExplodingRecordSource())

    assert code == EXIT_CLI_REFUSED == 3
    payload = _verdict(capsys)
    assert payload["status"] == "CLI_REFUSED"
    assert payload["status"] not in ALL_STATUSES  # never mistakable for a verdict


def test_cli_usage_errors_exit_two_and_are_not_verdicts(tmp_path: Path) -> None:
    """argparse exits 2 on a usage error; the verdict codes start at 10 precisely so a typo in a
    cron line can never be read as "the chain is fine" — or as any other finding."""
    with pytest.raises(SystemExit) as excinfo:
        _cli(["--tenant", _TENANT])  # --dsn / --store-root / --fake-verifier-key-label missing
    assert excinfo.value.code == 2
    assert 2 not in set(EXIT_CODE_BY_STATUS.values())


def test_cli_defaults_the_record_source_to_the_real_postgres_reader() -> None:
    """The factory seam exists so the CLI's own contract is provable offline; it must not have
    quietly become the DEFAULT, or the shipped CLI would read from a fixture."""
    import inspect

    default = inspect.signature(_cli).parameters["record_source_factory"].default
    assert default is PostgresChainRecordSource


def test_postgres_record_source_construction_is_pure_and_normalizes_the_dsn() -> None:
    """Construction opens no socket (a bad DSN surfaces on the first read, which raises — never a
    silent no-op), and the platform's `postgresql+asyncpg://` convention is stripped exactly as
    every other consumer strips it."""
    source = PostgresChainRecordSource("postgresql+asyncpg://maezo:maezo@localhost:5433/maezo")
    assert source._dsn == "postgresql://maezo:maezo@localhost:5433/maezo"


# =================================================================================================
# V8/V9 — the dark build: read-only, side-effect-free, unimported
# =================================================================================================

#: Every import spelling this fence catches, stated once so a reviewer can check the predicate
#: against the list instead of re-deriving it from `ast` semantics. Mirrors leg 1's
#: `_CAUGHT_IMPORT_SPELLINGS` one-for-one — including the two `node.names` forms that leg 1's
#: predicate originally MISSED (external-review finding F1), because a fence exercised by only the
#: one spelling a probe happened to pick is how that blind spot survived the first time.
_CAUGHT_IMPORT_SPELLINGS: Final[tuple[str, ...]] = (
    "from maezo.gateway.audit_anchor_verify import verify_latest_anchor",
    "from maezo.gateway.audit_anchor_verify import verify_latest_anchor as v",
    "from .audit_anchor_verify import verify_latest_anchor",
    "import maezo.gateway.audit_anchor_verify",
    "import maezo.gateway.audit_anchor_verify as aav",
    "from maezo.gateway import audit_anchor_verify",
    "from maezo.gateway import audit_anchor_verify as aav",
    "from . import audit_anchor_verify",
    "from .. import gateway, audit_anchor_verify",
)

#: Spellings this AST scan structurally CANNOT catch, disclosed rather than pretended away — a
#: string-keyed import is not an import node. Compensated by the allowlist being EMPTY (any
#: importer at all is a reviewed diff), exactly as leg 1 compensates for the same gap.
_UNCAUGHT_IMPORT_SPELLINGS: Final[tuple[str, ...]] = (
    "import maezo.gateway  # then maezo.gateway.audit_anchor_verify.verify_latest_anchor(...)",
    "importlib.import_module('maezo.gateway.audit_anchor_verify')",
    "__import__('maezo.gateway.audit_anchor_verify')",
)


def _imports_module(node: ast.AST, module_name: str) -> bool:
    """True iff `node` is an import statement that binds `module_name` (any spelling).

    The GENERALIZED form of leg 1's `_imports_the_anchor_module`, with identical semantics — two
    branches, because the module can appear on either side of an `ImportFrom`: `node.module`
    (`from pkg.mod import X`, `from .mod import X`, matched by SUFFIX) or `node.names`
    (`from pkg import mod`, `from . import mod`, matched by EXACT equality, where `node.module` is
    the PACKAGE or `None`). `test_the_two_fence_predicates_agree_on_the_anchor_spellings` pins the
    two against each other so this generalization cannot drift from the original.

    Note the suffix branch does NOT confuse the two modules: `maezo.gateway.audit_anchor_verify`
    does not end with `audit_anchor`, and `maezo.gateway.audit_anchor` does not end with
    `audit_anchor_verify`.
    """
    if isinstance(node, ast.ImportFrom):
        return (node.module or "").endswith(module_name) or any(
            alias.name == module_name for alias in node.names
        )
    if isinstance(node, ast.Import):
        return any(alias.name.endswith(module_name) for alias in node.names)
    return False


@pytest.mark.parametrize("source", _CAUGHT_IMPORT_SPELLINGS)
def test_verify_fence_predicate_catches_every_spelling(source: str) -> None:
    tree = ast.parse(source)
    assert any(_imports_module(node, "audit_anchor_verify") for node in ast.walk(tree)), source


@pytest.mark.parametrize(
    "source",
    [
        "from maezo.gateway.audit import AuditRecord",
        "from maezo.gateway.audit_anchor import write_anchor",
        "from maezo.gateway import audit_anchor",
        "import maezo.gateway.audit_postgres",
        "import ast",
    ],
    ids=["audit", "anchor-module", "anchor-name", "audit-postgres", "stdlib"],
)
def test_verify_fence_predicate_does_not_fire_on_unrelated_imports(source: str) -> None:
    """A fence that flags everything is a fence nobody keeps — and the SIBLING module (whose name
    is a prefix of this one's) is the single most likely false positive."""
    tree = ast.parse(source)
    assert not any(_imports_module(node, "audit_anchor_verify") for node in ast.walk(tree)), source


def test_the_two_fence_predicates_agree_on_the_anchor_spellings() -> None:
    """Anti-drift: this file's GENERALIZED predicate must decide exactly what leg 1's decides, on
    leg 1's own spelling matrix. Without this, two fences guarding two dark modules could quietly
    develop two different ideas of what an import is — and the weaker one would be the one nobody
    noticed."""
    from tests.unit.gateway.test_audit_anchor import (  # noqa: PLC0415 - deliberate cross-pin
        _CAUGHT_IMPORT_SPELLINGS as _LEG1_SPELLINGS,
    )
    from tests.unit.gateway.test_audit_anchor import (  # noqa: PLC0415 - deliberate cross-pin
        _imports_the_anchor_module,
    )

    assert len(_LEG1_SPELLINGS) == len(_CAUGHT_IMPORT_SPELLINGS)
    for source in _LEG1_SPELLINGS:
        tree = ast.parse(source)
        nodes = list(ast.walk(tree))
        assert any(_imports_the_anchor_module(node) for node in nodes), source
        assert any(_imports_module(node, "audit_anchor") for node in nodes), source


def test_no_production_module_imports_the_verify_job() -> None:
    """HARDCODED allowlist, EMPTY. Provenance: leg 2 ships the verification job merged, tested and
    INERT — nothing schedules it, and the composition root that would is an owner decision (leg 3's
    drills invoke it directly from tests). The strongest inertness proof is not "the flag is off"
    but "no production code can reach this at all".

    SCOPE, stated honestly: a static scan of import STATEMENTS, catching the spellings enumerated
    in `_CAUGHT_IMPORT_SPELLINGS` and structurally blind to the dynamic routes disclosed in
    `_UNCAUGHT_IMPORT_SPELLINGS`. What compensates is the shape of the allowlist — it is EMPTY, so
    any importer at all is a reviewed diff.

    When an owner wires this into a composition root, THIS list is where the importer is
    declared — deliberately, in a diff a reviewer sees."""
    allowed_importers: frozenset[str] = frozenset()

    importers: set[str] = set()
    for path in sorted(_SRC_MAEZO.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        if any(_imports_module(node, "audit_anchor_verify") for node in ast.walk(tree)):
            importers.add(str(path.relative_to(_SRC_MAEZO)))

    assert frozenset(importers) == allowed_importers


def _module_tree() -> ast.Module:
    return ast.parse(_VERIFY_MODULE_PATH.read_text(encoding="utf-8"))


def _docstring_constant_ids(tree: ast.AST) -> set[int]:
    """`id()` of every `ast.Constant` that IS a docstring — same technique as leg 1's fence and
    `scripts/ci/check_effect_chokepoint_fence.py::_docstring_constant_ids`. Prose is not code."""
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            body = getattr(node, "body", [])
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                ids.add(id(body[0].value))
    return ids


def _executable_string_literals(tree: ast.Module) -> list[str]:
    docstrings = _docstring_constant_ids(tree)
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings
    ]


def test_the_verify_module_issues_only_the_three_pinned_read_statements() -> None:
    """HARDCODED. Provenance: the verification job is READ-ONLY against the database, and these are
    the only three statements it issues — the tenant search_path pin (validated by the same
    `schema_for_tenant` that guards every other interpolation), one unqualified SELECT over
    `audit_chain`, and the G1b schema cross-check's read of alembic's per-tenant version table
    (`platform/migrations/env.py` names it `f"{TENANT_ID}_alembic_version"`), whose identifier is
    built from that SAME validated schema name.

    Pinned as an exact SET rather than as a "no INSERT/UPDATE/DELETE" blacklist because a blacklist
    only catches the verbs somebody remembered to list; a set-equality pin turns ANY new statement —
    including a `COPY`, a `CREATE TEMP TABLE`, or a `SELECT ... FOR UPDATE` — into a reviewed diff.

    A literal counts as a statement when it BEGINS with a SQL verb (the verbs carry their trailing
    space, so the reason token `SELECTED_ANCHOR_UNREADABLE` and the prose "... is unset or empty"
    are not mistaken for SQL). A second, containment-based check catches a statement smuggled into
    the middle of a longer literal, where the leading-verb rule would not see it."""
    sql_statement_openers = (
        "SELECT ",
        "INSERT ",
        "UPDATE ",
        "DELETE ",
        "DROP ",
        "ALTER ",
        "TRUNCATE ",
        "GRANT ",
        "COPY ",
        "CREATE ",
        "SET ",
        "WITH ",
    )
    literals = _executable_string_literals(_module_tree())
    statements = {
        literal for literal in literals if literal.strip().upper().startswith(sql_statement_openers)
    }
    assert statements == {
        'SET search_path TO "',
        "SELECT * FROM audit_chain",
        'SELECT version_num FROM "',
    }

    embedded = ("INSERT INTO", "DELETE FROM", "TRUNCATE TABLE", "DROP TABLE", "UPDATE AUDIT_")
    offenders = [
        (fragment, literal) for literal in literals for fragment in embedded if fragment in literal.upper()
    ]
    assert offenders == []


def test_the_verify_module_never_writes_to_the_anchor_store() -> None:
    """The job reads evidence; it never produces any. A `store.put` here would let a verification
    run manufacture the very anchor it then verifies against — a closed loop that always agrees."""
    tree = _module_tree()
    writes = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"put", "write_anchor", "emit", "emit_once", "write_bytes", "write_text"}
    ]
    assert writes == [], [ast.dump(node.func) for node in writes]


def test_the_verify_module_has_no_import_time_side_effect() -> None:
    """Nothing schedules the loop. Importing this module must define names and nothing else — no
    task creation, no thread, no connection, no `asyncio.run`. The ONLY module-level `if` permitted
    is the `__name__ == "__main__"` CLI guard, which cannot fire on import."""
    tree = _module_tree()
    module_level_ifs = [node for node in tree.body if isinstance(node, ast.If)]
    assert len(module_level_ifs) == 2  # `if TYPE_CHECKING:` and the `__main__` guard

    allowed = (
        ast.Expr,  # the module docstring
        ast.Import,
        ast.ImportFrom,
        ast.Assign,
        ast.AnnAssign,
        ast.FunctionDef,
        ast.AsyncFunctionDef,
        ast.ClassDef,
        ast.If,
    )
    offenders = [type(node).__name__ for node in tree.body if not isinstance(node, allowed)]
    assert offenders == []
    # The only module-level expression statement is the docstring itself.
    expressions = [node for node in tree.body if isinstance(node, ast.Expr)]
    assert len(expressions) == 1
    assert isinstance(expressions[0].value, ast.Constant)


def test_the_verify_module_holds_no_canonicalization_of_its_own() -> None:
    """V2: every root this job compares must come out of LEG 1's canonicalizer, never a second copy
    that would drift from the bytes the signatures were actually made over.

    Pinned structurally rather than by grepping for `json.dumps` (the CLI legitimately serializes
    its verdict): there is exactly ONE hash in this module, and its argument is the preimage leg 1
    produced. Add a second `sha256`, or hash anything other than `anchor.checkpoint_bytes`, and
    this goes red — which is precisely the shape a home-grown canonicalization would take."""
    tree = _module_tree()
    hashes = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "sha256"
    ]
    assert [ast.unparse(node) for node in hashes] == ["hashlib.sha256(anchor.checkpoint_bytes)"]

    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and (node.module or "") == "maezo.gateway.audit_anchor"
        for alias in node.names
    }
    assert {"canonical_bytes", "checkpoint_for_chain", "checkpoint_root"} <= imported


def test_the_verify_module_reuses_the_real_row_mapper() -> None:
    """The lazy-import lesson from leg 1, applied to the other reuse: `snapshot_from_rows` must go
    through `audit_postgres._row_to_record` — the one place that knows `input_hash` is RECOMPUTED
    rather than read back — and not through a second, drifting copy of the mapping."""
    from maezo.gateway import audit_anchor_verify
    from maezo.gateway.audit_postgres import _row_to_record

    assert audit_anchor_verify._row_to_record is _row_to_record

    record = _emit_chain(1)[0]
    row = _rows_from_records([record])[0]
    assert (
        snapshot_from_rows([row], schema_version=_SCHEMA_VERSION).records[0].record_hash == record.record_hash
    )


def test_audit_and_audit_postgres_remain_unmodified_by_this_leg() -> None:
    """Leg 1's additive-only doctrine, still held at ZERO modifications. This job imports FROM the
    two audit modules and never into them; the check is a cheap structural one — neither file
    mentions the anchor or the verifier anywhere, so no import fence, no wrapper and no
    conditional could have crept in."""
    for name in ("audit.py", "audit_postgres.py"):
        source = (_SRC_MAEZO / "gateway" / name).read_text(encoding="utf-8")
        assert "audit_anchor" not in source, name
        assert "anchor" not in source.lower().replace("anchoring", ""), name
