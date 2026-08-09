"""Unit tests for `maezo.platform.integrations.amh_inbox` — the DBA-gated AMH durable inbox.

Three things are proved here, and the third is the one the DBA gate rests on:

1.  **Every fail-closed branch of the ratification loader refuses** (absent path, bad override,
    missing/unreadable/mis-encoded/malformed file, schema violations, placeholder values, a draft
    `ratificado`, a pending `dba_review`, the wrong migration revision, a stale migration digest)
    — mirroring `tests/unit/platform/test_legal_bases_matrix.py`'s coverage of the retention-matrix
    loader, whose refusal shape this module follows.

2.  **The on-disk artifact is itself refused.** `spec/policies/amh/inbox-ratification.yaml` is a
    DRAFT, and pointing the loader at the real file must fail — never silently succeed on
    placeholder markers.

3.  **An unratified inbox can never report settlement success**, and the proof is structural
    rather than behavioural: the only public constructor requires an `InboxRatification`, and an
    `InboxRatification` cannot be constructed for anything but a real ratification. A caller
    therefore has no path — not a flag, not a default argument, not a subclass — to an object on
    which `mark_settled` could be called at all. The forged-capability test closes the last door
    by bypassing `__post_init__` with `object.__setattr__` and showing the constructor still
    refuses.

Plus the PHI fence on `_stored_row` (planted sentinel in all six excluded envelope fields, and the
positive non-vacuity half), and the ratified-fixture test proving activation needs NO code change.

No Postgres is touched by this file. The SQL is exercised for real by the live-PG proof recorded in
`docs/reviews/mzo-060-dba-review-packet.md`; here the DB-free surface is what is under test.
"""

from __future__ import annotations

import dataclasses
import hashlib
from datetime import UTC, datetime, timedelta, timezone, tzinfo
from pathlib import Path
from typing import Any

import pytest
import yaml

from maezo.platform.integrations.amh_inbox import (
    APPROVED_DBA_REVIEW,
    INBOX_MIGRATION_FILENAME,
    INBOX_MIGRATION_REVISION,
    INBOX_RATIFICATION_PATH_ENV,
    INBOX_RATIFICATION_RELATIVE_PATH,
    PLACEHOLDER_MARKERS,
    RATIFIED_STATUS,
    REASON_DBA_REVIEW_PENDING,
    REASON_FILE_NOT_FOUND,
    REASON_INVALID_ENCODING,
    REASON_INVALID_ENVELOPE,
    REASON_INVALID_SCHEMA,
    REASON_INVALID_YAML,
    REASON_MIGRATION_DIGEST_MISMATCH,
    REASON_MIGRATION_REVISION_MISMATCH,
    REASON_NOT_RATIFIED,
    REASON_PATH_NOT_SET,
    REASON_PLACEHOLDER_VALUE,
    REASON_UNRATIFIED_REPOSITORY,
    REQUIRED_RATIFICATION_FIELDS,
    AmhInbox,
    AmhInboxUnavailableError,
    InboxRatification,
    InboxRecordOutcome,
    InboxSettleOutcome,
    InboxStatus,
    InboxStream,
    PostgresAmhInbox,
    build_amh_inbox_repository,
    load_inbox_ratification,
    migration_digest,
    migration_file_path,
    resolve_ratification_path,
)
from maezo.ports.envelope import CanonicalEnvelope, SourcePosition

_REPO_ROOT = Path(__file__).resolve().parents[4]
_DRAFT_ARTIFACT = _REPO_ROOT / INBOX_RATIFICATION_RELATIVE_PATH

_DSN = "postgresql://maezo:maezo@localhost:5432/maezo"

#: A string that must never appear in a bind parameter, a log line or a stored row.
_PHI_SENTINEL = "PHI-SENTINEL-52dfe1-DO-NOT-PERSIST"


def _ratified_mapping(**overrides: Any) -> dict[str, Any]:
    """A fully-ratified artifact mapping, digest bound to the REAL migration on disk."""
    data: dict[str, Any] = {
        "status": RATIFIED_STATUS,
        "ratificado": True,
        "dba_review": APPROVED_DBA_REVIEW,
        "dba_reviewer": "Ana Souza (DBA, plataforma)",
        "dba_review_date": "2026-08-09",
        "evidence_ref": "mzo-060",
        "notes": "Reviewed against the packet; partial indexes accepted as specified.",
        "migration_revision": INBOX_MIGRATION_REVISION,
        "migration_sha256": migration_digest(),
        "review_packet": "docs/reviews/mzo-060-dba-review-packet.md",
    }
    data.update(overrides)
    return data


def _write(tmp_path: Path, mapping: dict[str, Any], name: str = "ratified.yaml") -> Path:
    path = tmp_path / name
    path.write_text(yaml.safe_dump(mapping, sort_keys=False), encoding="utf-8")
    return path


def _envelope(**overrides: Any) -> CanonicalEnvelope:
    """A canonical envelope whose six EXCLUDED fields all carry the PHI sentinel."""
    fields: dict[str, Any] = {
        "event_id": "evt-0001",
        "event_type": "work_item.created",
        "canonical_schema_version": "1.0.0",
        "occurred_at": datetime(2026, 8, 9, 12, 0, tzinfo=UTC),
        "ingested_at": datetime(2026, 8, 9, 12, 0, 1, tzinfo=UTC),
        "source_vendor": "philips",
        "source_product": "tasy_healthcare_plan",
        "source_instance": "inst-a",
        "source_tenant": "srct-a",
        "source_entity": "authorization_request",
        "protected_source_record_ref": _PHI_SENTINEL,
        "source_position": SourcePosition(kind="scn", value="99001", transaction_ref=None),
        "amh_tenant": "amh-tenant-a",
        "legal_entity": "le-a",
        "portable_subject_ref": _PHI_SENTINEL,
        "amh_mpi_ref": _PHI_SENTINEL,
        "beneficiary_ref": _PHI_SENTINEL,
        "correlation_id": "corr-1",
        "causation_id": "caus-1",
        "idempotency_key": "idem-1",
        "consent_decision_ref": _PHI_SENTINEL,
        "purpose_of_use": "treatment",
        "data_classification": "restricted",
        "trace_id": "trace-1",
        "producer_version": "1.2.3",
        "contract_manifest_digest": "d" * 64,
        "payload_hash": "a" * 64,
        "replay_count": 0,
    }
    fields.update(overrides)
    return CanonicalEnvelope(**fields)


# ===========================================================================
# Fail-closed: path resolution
# ===========================================================================


def test_env_override_pointing_at_missing_file_refuses(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An explicit-but-wrong override is a configuration error, never a silent fallback."""
    monkeypatch.setenv(INBOX_RATIFICATION_PATH_ENV, str(tmp_path / "nope.yaml"))
    with pytest.raises(AmhInboxUnavailableError) as excinfo:
        resolve_ratification_path()
    assert excinfo.value.reason == REASON_FILE_NOT_FOUND


def test_env_override_that_stdlib_cannot_resolve_refuses_typed(monkeypatch: pytest.MonkeyPatch) -> None:
    """A path stdlib cannot even resolve refuses as AmhInboxUnavailableError, not RuntimeError.

    `~nosuchuser` makes `Path.expanduser()` raise `RuntimeError("Could not determine home
    directory.")` — a foreign exception type that would otherwise escape a function whose declared
    contract is `AmhInboxUnavailableError`. This is the exact escape class MZO-050a's three
    zero-trust rounds found 18 instances of in the sibling adapter.
    """
    monkeypatch.setenv(INBOX_RATIFICATION_PATH_ENV, "~nosuchuser12345/inbox-ratification.yaml")
    with pytest.raises(AmhInboxUnavailableError) as excinfo:
        resolve_ratification_path()
    assert excinfo.value.reason == REASON_FILE_NOT_FOUND
    assert "RuntimeError" in str(excinfo.value)


def test_default_resolution_finds_the_repo_checkout_artifact(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(INBOX_RATIFICATION_PATH_ENV, raising=False)
    assert resolve_ratification_path() == _DRAFT_ARTIFACT.resolve()


def test_unresolvable_default_refuses_with_path_not_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """Non-vacuity for the default candidates: with none on disk, resolution refuses."""
    monkeypatch.delenv(INBOX_RATIFICATION_PATH_ENV, raising=False)
    monkeypatch.setattr(
        "maezo.platform.integrations.amh_inbox._default_ratification_candidates",
        lambda: (Path("/nonexistent/a.yaml"), Path("/nonexistent/b.yaml")),
    )
    with pytest.raises(AmhInboxUnavailableError) as excinfo:
        resolve_ratification_path()
    assert excinfo.value.reason == REASON_PATH_NOT_SET
    assert INBOX_RATIFICATION_PATH_ENV in str(excinfo.value)


# ===========================================================================
# THE GATE: the on-disk artifact is a DRAFT and is refused
# ===========================================================================


def test_the_shipped_artifact_is_a_draft_and_is_refused() -> None:
    """The artifact in `spec/policies/amh/` must never load. This is the DBA gate itself."""
    with pytest.raises(AmhInboxUnavailableError) as excinfo:
        load_inbox_ratification(_DRAFT_ARTIFACT)
    assert excinfo.value.reason == REASON_NOT_RATIFIED


def test_the_shipped_artifact_declares_every_required_field_as_a_placeholder() -> None:
    """Not just unratified — every HUMAN field is an explicit, machine-detectable placeholder.

    Guards the failure mode where a draft carries a real-looking value that a later ratification
    would inherit without anyone deciding it.
    """
    data = yaml.safe_load(_DRAFT_ARTIFACT.read_text(encoding="utf-8"))
    assert set(REQUIRED_RATIFICATION_FIELDS) <= set(data), "the draft must declare the full schema"
    assert data["ratificado"] is False
    assert data["dba_review"] == "PENDING"
    assert data["status"] == "DRAFT"
    for field in ("dba_reviewer", "dba_review_date", "evidence_ref", "notes", "migration_sha256"):
        assert any(marker in str(data[field]).upper() for marker in PLACEHOLDER_MARKERS), (
            f"draft field {field!r} = {data[field]!r} is not a detectable placeholder"
        )


def test_build_repository_against_the_shipped_artifact_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    """The factory refuses in the deployment's DEFAULT configuration — no argument required."""
    monkeypatch.delenv(INBOX_RATIFICATION_PATH_ENV, raising=False)
    with pytest.raises(AmhInboxUnavailableError) as excinfo:
        build_amh_inbox_repository(dsn=_DSN, tenant="public")
    assert excinfo.value.reason == REASON_NOT_RATIFIED


# ===========================================================================
# THE INVARIANT: an unratified inbox never reports settlement success
# ===========================================================================


def test_no_repository_can_exist_without_a_ratification_capability() -> None:
    """Structural: `PostgresAmhInbox` has no constructor path that omits the ratification."""
    with pytest.raises(TypeError):
        PostgresAmhInbox(dsn=_DSN, tenant="public")  # type: ignore[call-arg]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("ratificado", False),
        ("ratificado", None),
        ("status", "DRAFT"),
        ("dba_review", "PENDING"),
    ],
)
def test_an_unratified_ratification_object_cannot_be_constructed(field: str, value: Any) -> None:
    """There is no such thing as an `InboxRatification` that says "not ratified yet"."""
    kwargs = {
        "status": RATIFIED_STATUS,
        "ratificado": True,
        "dba_review": APPROVED_DBA_REVIEW,
        "dba_reviewer": "Ana Souza",
        "dba_review_date": "2026-08-09",
        "evidence_ref": "mzo-060",
        "notes": "ok",
        "migration_revision": INBOX_MIGRATION_REVISION,
        "migration_sha256": migration_digest(),
        "review_packet": "docs/reviews/mzo-060-dba-review-packet.md",
    }
    kwargs[field] = value
    with pytest.raises(AmhInboxUnavailableError):
        InboxRatification(**kwargs)  # type: ignore[arg-type]


def test_ratificado_must_be_the_boolean_true_not_a_truthy_value() -> None:
    """`1 == True` in Python — an `==` check would accept `ratificado: 1` as a human's approval."""
    with pytest.raises(AmhInboxUnavailableError) as excinfo:
        InboxRatification(
            status=RATIFIED_STATUS,
            ratificado=1,  # type: ignore[arg-type]
            dba_review=APPROVED_DBA_REVIEW,
            dba_reviewer="Ana Souza",
            dba_review_date="2026-08-09",
            evidence_ref="mzo-060",
            notes="ok",
            migration_revision=INBOX_MIGRATION_REVISION,
            migration_sha256=migration_digest(),
            review_packet="p.md",
        )
    assert excinfo.value.reason == REASON_NOT_RATIFIED


def test_a_forged_ratification_is_still_refused_by_the_constructor() -> None:
    """The second lock: bypassing `__post_init__` does not buy a working repository.

    `object.__setattr__` on the frozen dataclass is the only way to produce an instance whose
    `ratificado` is False, and the constructor re-check catches exactly that.
    """
    forged = InboxRatification(
        status=RATIFIED_STATUS,
        ratificado=True,
        dba_review=APPROVED_DBA_REVIEW,
        dba_reviewer="Ana Souza",
        dba_review_date="2026-08-09",
        evidence_ref="mzo-060",
        notes="ok",
        migration_revision=INBOX_MIGRATION_REVISION,
        migration_sha256=migration_digest(),
        review_packet="p.md",
    )
    object.__setattr__(forged, "ratificado", False)
    with pytest.raises(AmhInboxUnavailableError) as excinfo:
        PostgresAmhInbox(dsn=_DSN, tenant="public", ratification=forged)
    assert excinfo.value.reason == REASON_UNRATIFIED_REPOSITORY

    object.__setattr__(forged, "ratificado", True)
    object.__setattr__(forged, "dba_review", "PENDING")
    with pytest.raises(AmhInboxUnavailableError) as excinfo:
        PostgresAmhInbox(dsn=_DSN, tenant="public", ratification=forged)
    assert excinfo.value.reason == REASON_UNRATIFIED_REPOSITORY


def test_dataclasses_replace_cannot_downgrade_a_ratification() -> None:
    """`dataclasses.replace` re-runs `__post_init__`, so it is not a laundering path either."""
    ratified = InboxRatification(**_ratified_mapping())  # type: ignore[arg-type]
    with pytest.raises(AmhInboxUnavailableError):
        dataclasses.replace(ratified, ratificado=False)


# ===========================================================================
# ACTIVATION: flipping a ratified artifact needs NO code change
# ===========================================================================


def test_a_ratified_artifact_activates_the_factory(tmp_path: Path) -> None:
    """Same code, same call: only the YAML changed, and the repository now exists."""
    path = _write(tmp_path, _ratified_mapping())
    repo = build_amh_inbox_repository(dsn=_DSN, tenant="public", ratification_path=path)
    assert isinstance(repo, PostgresAmhInbox)
    assert repo.ratification.dba_reviewer == "Ana Souza (DBA, plataforma)"
    assert repo.ratification.migration_sha256 == migration_digest()

    # And it satisfies the seam the future MZO-050b consumer will depend on.
    seam: AmhInbox = repo
    assert seam is repo


def test_the_ratified_artifact_differs_from_the_draft_only_in_human_fields(tmp_path: Path) -> None:
    """Non-vacuity for the test above: the draft on disk and the ratified fixture share a schema.

    Without this, the activation test could be passing against a fixture whose SHAPE has drifted
    from the artifact a DBA will actually edit — proving the loader works on a file nobody will
    ever write.
    """
    draft = yaml.safe_load(_DRAFT_ARTIFACT.read_text(encoding="utf-8"))
    ratified = _ratified_mapping()
    assert set(ratified) == set(REQUIRED_RATIFICATION_FIELDS)
    assert set(REQUIRED_RATIFICATION_FIELDS) <= set(draft)
    assert draft["migration_revision"] == ratified["migration_revision"]
    assert draft["review_packet"] == ratified["review_packet"]


# ===========================================================================
# Fail-closed: artifact schema and content
# ===========================================================================


def test_missing_file_refuses(tmp_path: Path) -> None:
    with pytest.raises(AmhInboxUnavailableError) as excinfo:
        load_inbox_ratification(tmp_path / "absent.yaml")
    assert excinfo.value.reason == REASON_FILE_NOT_FOUND


def test_directory_path_refuses(tmp_path: Path) -> None:
    with pytest.raises(AmhInboxUnavailableError) as excinfo:
        load_inbox_ratification(tmp_path)
    assert excinfo.value.reason == REASON_FILE_NOT_FOUND


def test_non_utf8_file_refuses_with_invalid_encoding(tmp_path: Path) -> None:
    """UnicodeDecodeError subclasses ValueError, not OSError — without its own catch it escapes."""
    path = tmp_path / "cp1252.yaml"
    path.write_bytes("dba_reviewer: Ana Sou\xe7a\n".encode("cp1252"))
    with pytest.raises(AmhInboxUnavailableError) as excinfo:
        load_inbox_ratification(path)
    assert excinfo.value.reason == REASON_INVALID_ENCODING


def test_malformed_yaml_refuses(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text("status: [unclosed\n", encoding="utf-8")
    with pytest.raises(AmhInboxUnavailableError) as excinfo:
        load_inbox_ratification(path)
    assert excinfo.value.reason == REASON_INVALID_YAML


@pytest.mark.parametrize("body", ["- a\n- b\n", "just a string\n", ""])
def test_non_mapping_root_refuses(tmp_path: Path, body: str) -> None:
    path = tmp_path / "root.yaml"
    path.write_text(body, encoding="utf-8")
    with pytest.raises(AmhInboxUnavailableError) as excinfo:
        load_inbox_ratification(path)
    assert excinfo.value.reason == REASON_INVALID_SCHEMA


@pytest.mark.parametrize("field", REQUIRED_RATIFICATION_FIELDS)
def test_every_required_field_is_actually_required(tmp_path: Path, field: str) -> None:
    mapping = _ratified_mapping()
    del mapping[field]
    with pytest.raises(AmhInboxUnavailableError) as excinfo:
        load_inbox_ratification(_write(tmp_path, mapping))
    # `ratificado` missing reports schema (it is absent), everything else likewise.
    assert excinfo.value.reason == REASON_INVALID_SCHEMA
    assert field in str(excinfo.value)


@pytest.mark.parametrize("value", ["", "   ", 42, None, ["a"], {"a": 1}])
def test_non_string_or_blank_field_refuses(tmp_path: Path, value: Any) -> None:
    mapping = _ratified_mapping(dba_reviewer=value)
    with pytest.raises(AmhInboxUnavailableError) as excinfo:
        load_inbox_ratification(_write(tmp_path, mapping))
    assert excinfo.value.reason == REASON_INVALID_SCHEMA


@pytest.mark.parametrize("marker", PLACEHOLDER_MARKERS)
def test_every_placeholder_marker_is_detected(tmp_path: Path, marker: str) -> None:
    """A half-filled ratification is not a ratification — each marker must refuse on its own."""
    mapping = _ratified_mapping(dba_reviewer=f"{marker}-fill-me-in")
    with pytest.raises(AmhInboxUnavailableError) as excinfo:
        load_inbox_ratification(_write(tmp_path, mapping))
    assert excinfo.value.reason == REASON_PLACEHOLDER_VALUE
    assert marker in str(excinfo.value)


def test_placeholder_detection_is_case_insensitive(tmp_path: Path) -> None:
    mapping = _ratified_mapping(notes="pending the security review")
    with pytest.raises(AmhInboxUnavailableError) as excinfo:
        load_inbox_ratification(_write(tmp_path, mapping))
    assert excinfo.value.reason == REASON_PLACEHOLDER_VALUE


def test_ratificado_string_true_is_not_a_ratification(tmp_path: Path) -> None:
    """A quoted `"true"` is a string, not a human's boolean — it must not activate anything."""
    mapping = _ratified_mapping(ratificado="true")
    with pytest.raises(AmhInboxUnavailableError) as excinfo:
        load_inbox_ratification(_write(tmp_path, mapping))
    assert excinfo.value.reason == REASON_NOT_RATIFIED


def test_ratificado_integer_one_is_not_a_ratification(tmp_path: Path) -> None:
    mapping = _ratified_mapping(ratificado=1)
    with pytest.raises(AmhInboxUnavailableError) as excinfo:
        load_inbox_ratification(_write(tmp_path, mapping))
    assert excinfo.value.reason == REASON_NOT_RATIFIED


def test_ratified_but_dba_review_pending_refuses(tmp_path: Path) -> None:
    """Both switches, independently: `ratificado: true` alone is not a DBA approval."""
    mapping = _ratified_mapping(dba_review="APPROVED_BY_NOBODY")
    with pytest.raises(AmhInboxUnavailableError) as excinfo:
        load_inbox_ratification(_write(tmp_path, mapping))
    assert excinfo.value.reason == REASON_DBA_REVIEW_PENDING


def test_status_must_be_ratified(tmp_path: Path) -> None:
    mapping = _ratified_mapping(status="ACCEPTED")
    with pytest.raises(AmhInboxUnavailableError) as excinfo:
        load_inbox_ratification(_write(tmp_path, mapping))
    assert excinfo.value.reason == REASON_NOT_RATIFIED


# ===========================================================================
# Fail-closed: the migration digest binding
# ===========================================================================


def test_wrong_migration_revision_refuses(tmp_path: Path) -> None:
    mapping = _ratified_mapping(migration_revision="0006")
    with pytest.raises(AmhInboxUnavailableError) as excinfo:
        load_inbox_ratification(_write(tmp_path, mapping))
    assert excinfo.value.reason == REASON_MIGRATION_REVISION_MISMATCH


def test_stale_migration_digest_refuses(tmp_path: Path) -> None:
    """THE anti-carryover guard: a ratification cannot cover DDL the reviewer never saw."""
    mapping = _ratified_mapping(migration_sha256="0" * 64)
    with pytest.raises(AmhInboxUnavailableError) as excinfo:
        load_inbox_ratification(_write(tmp_path, mapping))
    assert excinfo.value.reason == REASON_MIGRATION_DIGEST_MISMATCH


def test_migration_digest_matches_the_file_on_disk() -> None:
    """Non-vacuity for the digest guard: it hashes the REAL migration, not a constant."""
    path = migration_file_path()
    assert path.name == INBOX_MIGRATION_FILENAME
    assert path.is_file(), f"migration file not resolvable at {path}"
    assert migration_digest() == hashlib.sha256(path.read_bytes()).hexdigest()
    assert len(migration_digest()) == 64


def test_digest_comparison_tolerates_case_and_whitespace(tmp_path: Path) -> None:
    """A DBA pasting `SHASUM` output with an uppercase digest or a trailing space still ratifies."""
    mapping = _ratified_mapping(migration_sha256=f"  {migration_digest().upper()}  ")
    ratification = load_inbox_ratification(_write(tmp_path, mapping))
    assert ratification.migration_sha256 == migration_digest()


# ===========================================================================
# The PHI fence on `_stored_row`
# ===========================================================================


def test_stored_row_never_carries_a_subject_bearing_field() -> None:
    """THE PHI fence. Six excluded envelope fields all carry a sentinel; none may reach a param."""
    from maezo.platform.integrations.amh_inbox import _stored_row

    params = _stored_row(_envelope(), InboxStream.WORK_ITEM)
    rendered = [str(p) for p in params]
    assert _PHI_SENTINEL not in rendered, f"sentinel reached a bind parameter: {rendered}"
    assert not any(_PHI_SENTINEL in value for value in rendered)


def test_stored_row_projects_the_expected_18_columns_in_order() -> None:
    """Non-vacuity for the fence above: an empty/short projection must not read as 'clean'."""
    from maezo.platform.integrations.amh_inbox import _INSERT_COLUMNS, _stored_row

    params = _stored_row(_envelope(), InboxStream.CONSENT)
    assert len(params) == len(_INSERT_COLUMNS) == 18
    assert params[0] == "d" * 64  # contract_manifest_digest
    assert params[1] == "evt-0001"  # event_id
    assert params[2] == "amh-tenant-a"  # amh_tenant
    assert params[4] == "consent"  # inbox_stream — the payer-core vocabulary, not a topic name
    assert params[10] == "scn"  # source_position_kind
    assert params[11] == "99001"  # source_position_value
    assert params[12] == "a" * 64  # payload_hash
    assert params[13] == 0  # replay_count


def test_stored_row_excluded_fields_are_the_six_named_ones() -> None:
    """Names the exclusion set explicitly, so widening it silently is not possible.

    The sentinel test above proves no EXCLUDED value leaks; this proves the exclusion set is the
    one the migration docstring and the DBA packet promise, field for field.
    """
    from maezo.platform.integrations.amh_inbox import _INSERT_COLUMNS

    excluded = {
        "protected_source_record_ref",
        "portable_subject_ref",
        "amh_mpi_ref",
        "beneficiary_ref",
        "consent_decision_ref",
        "payload",
    }
    assert excluded.isdisjoint(_INSERT_COLUMNS)
    # And the non-excluded envelope fields we DO claim to store really are stored.
    assert {"contract_manifest_digest", "event_id", "amh_tenant", "payload_hash"} <= set(_INSERT_COLUMNS)


@pytest.mark.parametrize(
    "field",
    ["event_id", "amh_tenant", "legal_entity", "payload_hash", "idempotency_key", "trace_id"],
)
def test_blank_required_envelope_field_refuses(field: str) -> None:
    from maezo.platform.integrations.amh_inbox import _stored_row

    with pytest.raises(AmhInboxUnavailableError) as excinfo:
        _stored_row(_envelope(**{field: "   "}), InboxStream.WORK_ITEM)
    assert excinfo.value.reason == REASON_INVALID_ENVELOPE


def test_naive_timestamp_refuses() -> None:
    """A naive datetime bound to timestamptz silently adopts the server's TimeZone."""
    from maezo.platform.integrations.amh_inbox import _stored_row

    with pytest.raises(AmhInboxUnavailableError) as excinfo:
        _stored_row(_envelope(occurred_at=datetime(2026, 8, 9, 12, 0)), InboxStream.WORK_ITEM)
    assert excinfo.value.reason == REASON_INVALID_ENVELOPE
    assert "naive" in str(excinfo.value)


def test_non_utc_but_aware_timestamp_is_accepted() -> None:
    """Aware is the requirement, not UTC: timestamptz stores the instant, not the offset."""
    from maezo.platform.integrations.amh_inbox import _stored_row

    brt = timezone(timedelta(hours=-3))
    params = _stored_row(_envelope(occurred_at=datetime(2026, 8, 9, 9, 0, tzinfo=brt)), InboxStream.WORK_ITEM)
    assert params[14] == datetime(2026, 8, 9, 9, 0, tzinfo=brt)


def test_hostile_tzinfo_refuses_typed() -> None:
    """A tzinfo that raises must not escape as a foreign exception across this seam."""
    from maezo.platform.integrations.amh_inbox import _stored_row

    class Exploding(tzinfo):
        def utcoffset(self, dt: datetime | None) -> timedelta:
            raise RuntimeError("boom")

        def tzname(self, dt: datetime | None) -> str:
            return "BOOM"

        def dst(self, dt: datetime | None) -> timedelta | None:
            return None

    with pytest.raises(AmhInboxUnavailableError) as excinfo:
        _stored_row(
            _envelope(occurred_at=datetime(2026, 8, 9, 12, 0, tzinfo=Exploding())),
            InboxStream.WORK_ITEM,
        )
    assert excinfo.value.reason == REASON_INVALID_ENVELOPE


@pytest.mark.parametrize("value", [-1, True, "0", 1.0])
def test_invalid_replay_count_refuses(value: Any) -> None:
    """`bool` is a subclass of `int` — `replay_count: True` would otherwise bind as 1."""
    from maezo.platform.integrations.amh_inbox import _stored_row

    with pytest.raises(AmhInboxUnavailableError) as excinfo:
        _stored_row(_envelope(replay_count=value), InboxStream.WORK_ITEM)
    assert excinfo.value.reason == REASON_INVALID_ENVELOPE


# ===========================================================================
# Vocabulary + SQL shape (DB-free)
# ===========================================================================


def test_lifecycle_and_outcome_vocabularies_are_closed() -> None:
    assert {s.value for s in InboxStatus} == {"RECEIVED", "PROCESSED", "SETTLED", "QUARANTINED"}
    assert {s.value for s in InboxStream} == {"work_item", "consent"}
    assert {o.value for o in InboxRecordOutcome} == {
        "recorded",
        "duplicate",
        "conflict",
        "concurrent",
    }
    assert {o.value for o in InboxSettleOutcome} == {
        "applied",
        "already",
        "refused_terminal",
        "not_found",
    }


def test_transition_sql_is_conditional_on_a_non_terminal_status() -> None:
    """The monotonic lifecycle is enforced by the WHERE clause, not by a trigger or by hope.

    An UPDATE without the status predicate would silently rewrite a QUARANTINED row into a
    SETTLED one — i.e. would let `ack` report success for an event that was quarantined.
    """
    from maezo.platform.integrations.amh_inbox import (
        _MARK_PROCESSED_SQL,
        _MARK_QUARANTINED_SQL,
        _MARK_SETTLED_SQL,
    )

    assert "AND status IN ('RECEIVED', 'PROCESSED')" in _MARK_SETTLED_SQL
    assert "AND status IN ('RECEIVED', 'PROCESSED')" in _MARK_QUARANTINED_SQL
    assert "AND status = 'RECEIVED'" in _MARK_PROCESSED_SQL
    for sql in (_MARK_SETTLED_SQL, _MARK_QUARANTINED_SQL, _MARK_PROCESSED_SQL):
        assert "RETURNING status" in sql, "a transition must report what it observed"


def test_insert_sql_is_idempotent_on_the_xrd10_dedup_key() -> None:
    from maezo.platform.integrations.amh_inbox import _INSERT_SQL

    assert "ON CONFLICT (contract_manifest_digest, event_id) DO NOTHING" in _INSERT_SQL
    assert _INSERT_SQL.count("$") == 18
