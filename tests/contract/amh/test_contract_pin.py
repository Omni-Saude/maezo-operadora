"""Consumer contract tests for the AMH contract pin (MZO-010, ADR-0037 XRD-04 / gate XRG-3).

Three layers, all hermetic — no network, no engine, no `integration` marker, so they run in the
unit CI lane:

1. **Real-tree**: the committed `config/integrations/amh/contracts.lock.json` passes the full
   fail-closed validator, and the vendored AMH fixture bytes hash to the pinned digests (recomputed
   HERE, independently of the validator — a bug that made the validator's own digest check vacuous
   would still be caught).
2. **Injected-violation**: every fail-closed path in the validator is exercised against a mutated
   copy of the real pin (corrupt each digest class, drop a schema, rename a topic, downgrade the
   version, inject each placeholder token) or against a synthetic vendor directory (delete a
   fixture, add a rogue file, flip one byte).
3. **Semantic**: the fixture bytes actually mean what the contract says. Valid fixtures carry the
   28 frozen envelope fields IN ORDER and a `payload_hash` that recomputes under the pinned
   canonicalisation; each `*.invalid.json` carries a violation a Maezo consumer can detect and
   REJECT.

`contract_manifest_digest` note (invariant pinned from the actual bytes, not assumed): every
fixture carries the SYNTHETIC placeholder `"f" * 64`, not a real digest. The AMH fixtures README
states this explicitly ("O `contract_manifest_digest` das fixtures e o placeholder sintetico
`ff...` (64 hex): o digest real so nasce na publicacao (XRG-2)"). So the invariant asserted below
is the one that actually holds: the value is well-formed 64-hex AND is NOT the pinned
`manifest_sha256`, the pre-publication digest, or any pinned artifact digest — a Maezo consumer
must never accept a fixture's synthetic digest as a real manifest binding.
"""

from __future__ import annotations

import copy
import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from scripts.ci.verify_amh_contract_pin import (
    DEFAULT_LOCK_PATH,
    DEFAULT_VENDOR_DIR,
    FROZEN_ARTIFACT_PATHS,
    FROZEN_ENVELOPE_FIELD_ORDER,
    FROZEN_GLUE_SCHEMA_KEYS,
    FROZEN_SOURCE_PRODUCT_VOCABULARY,
    FROZEN_TOPICS,
    load_lock,
    topic_major,
    verify_candidate,
    verify_lock,
    verify_manifest,
)

# tests/contract/amh/<file> -> parents[3] == repo root.
REPO_ROOT = Path(__file__).resolve().parents[3]
LOCK_PATH = REPO_ROOT / DEFAULT_LOCK_PATH
VENDOR_DIR = REPO_ROOT / DEFAULT_VENDOR_DIR

#: Synthetic, non-real digest every AMH fixture carries in `contract_manifest_digest`.
SYNTHETIC_MANIFEST_DIGEST = "f" * 64


def read_lock() -> dict[str, Any]:
    """A fresh, mutable copy of the real pin."""
    return copy.deepcopy(json.loads(LOCK_PATH.read_text(encoding="utf-8")))


def fixtures_by_role(lock: dict[str, Any], role: str) -> list[dict[str, Any]]:
    return [entry for entry in lock["fixtures"] if entry["role"] == role]


def canonical_payload_hash(payload: Any) -> str:
    """The pinned canonicalisation: sha256 hex over UTF-8 JSON, sorted keys, compact separators."""
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def set_at(lock: dict[str, Any], dotted: str, value: Any) -> None:
    node: Any = lock
    parts = dotted.split(".")
    for part in parts[:-1]:
        node = node[part]
    node[parts[-1]] = value


def codes(violations: list[Any]) -> set[str]:
    return {v.code for v in violations}


def verify_no_vendor(lock: dict[str, Any]) -> list[Any]:
    """Structure/format/catalog checks only — used when the mutation is not about vendored bytes."""
    return verify_lock(lock, repo_root=REPO_ROOT, check_vendored=False)


def build_temp_repo(tmp_path: Path, lock: dict[str, Any]) -> tuple[Path, Path]:
    """Materialise a throwaway repo root with a lock + a real copy of the vendored fixtures."""
    vendor = tmp_path / DEFAULT_VENDOR_DIR
    vendor.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(VENDOR_DIR, vendor)
    lock_path = tmp_path / DEFAULT_LOCK_PATH
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")
    return tmp_path, vendor


# ---------------------------------------------------------------------------
# Layer 1 — the real pin and the real bytes
# ---------------------------------------------------------------------------


def test_real_lock_loads() -> None:
    lock, violations = load_lock(LOCK_PATH)
    assert violations == []
    assert lock is not None


def test_real_lock_passes_the_full_validator() -> None:
    lock, _ = load_lock(LOCK_PATH)
    assert lock is not None
    violations = verify_lock(lock, repo_root=REPO_ROOT)
    assert violations == [], "\n".join(v.render() for v in violations)


def test_real_lock_is_not_vacuous() -> None:
    """A passing gate must actually be looking at the whole catalog."""
    lock = read_lock()
    assert len(lock["topics"]) == len(FROZEN_TOPICS) == 3
    assert len(lock["artifacts"]) == len(FROZEN_ARTIFACT_PATHS) == 5
    assert len(lock["fixtures"]) == 10
    assert len(lock["glue_registration"]["schema_version_ids"]) == len(FROZEN_GLUE_SCHEMA_KEYS) == 3
    assert lock["envelope"]["field_count"] == 28


def test_vendored_fixture_bytes_hash_to_the_locked_digests() -> None:
    """Independent recompute — deliberately does NOT go through the validator."""
    lock = read_lock()
    recomputed: dict[str, str] = {}
    for entry in lock["fixtures"]:
        target = REPO_ROOT / entry["vendored_path"]
        assert target.is_file(), f"vendored fixture missing: {entry['vendored_path']}"
        recomputed[entry["vendored_path"]] = hashlib.sha256(target.read_bytes()).hexdigest()
        assert recomputed[entry["vendored_path"]] == entry["sha256"], entry["vendored_path"]
    assert len(recomputed) == 10


def test_vendor_dir_holds_only_pinned_bytes_plus_provenance() -> None:
    lock = read_lock()
    pinned = {Path(entry["vendored_path"]).name for entry in lock["fixtures"]}
    present = {p.name for p in VENDOR_DIR.iterdir() if p.is_file()}
    assert present == pinned | {"PROVENANCE.md"}


def test_no_editable_amh_schema_was_copied_into_this_repo() -> None:
    """ADR-0037 prohibition 4: only the pin, never an editable Avro/OpenAPI source."""
    forbidden = [
        p for p in VENDOR_DIR.rglob("*") if p.suffix in {".avsc"} or p.name.endswith(".openapi.yaml")
    ]
    assert forbidden == []


# ---------------------------------------------------------------------------
# Layer 2 — every fail-closed path fires on a mutated copy
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("dotted", "value", "expected_code"),
    [
        # -- digest classes -------------------------------------------------
        ("manifest_pin.sha256", "not-a-digest", "format"),
        ("manifest_pin.sha256", "A" * 64, "format"),  # uppercase hex is not the canonical form
        ("manifest_pin.prepublication_sha256", "deadbeef", "format"),
        ("manifest_pin.git_blob_sha", "0" * 39, "format"),
        ("provenance.amh_commit_sha", "09a0a282", "format"),
        ("provenance.amh_manifest_commit_sha", "zz" * 20, "format"),
        # -- run ids / timestamps -------------------------------------------
        ("glue_registration.publication_run_id", "run-30991849241", "format"),
        ("compatibility_report.dry_run_run_id", "not-numeric", "format"),
        ("compatibility_report.evidence_artifact_id", "8924396642a", "format"),
        ("glue_registration.published_at_utc", "2026-08-05 09:07:40", "format"),
        ("glue_registration.published_at_utc", "2026-08-05T09:07:40-03:00", "format"),
        ("xrg3_verification.verified_at_utc", "yesterday", "format"),
        # -- frozen scalars --------------------------------------------------
        ("provenance.status", "DRAFT", "frozen-value"),
        ("provenance.status", "UNPUBLISHED", "frozen-value"),
        ("glue_registration.schema_version_status", "PENDING", "frozen-value"),
        ("provenance.compatibility_mode", "FORWARD", "frozen-value"),
        ("provenance.contract_name", "amh-maezo-boundary-v2", "frozen-value"),
        ("compatibility_report.result", "FAILED", "frozen-value"),
        # -- glue ids ---------------------------------------------------------
        ("glue_registration.schema_version_ids.amh_maezo_consent", "953bfaed", "glue-format"),
        # -- envelope ---------------------------------------------------------
        ("envelope.field_count", 27, "envelope-count"),
        ("envelope.field_count", 29, "envelope-count"),
        ("envelope.source_product_vocabulary", ["tasy_hospital"], "envelope-vocabulary"),
        (
            "envelope.source_product_vocabulary",
            ["tasy_healthcare_plan", "tasy_hospital"],
            "envelope-vocabulary",
        ),
        (
            "envelope.source_product_vocabulary",
            ["tasy_hospital", "tasy_healthcare_plan", "tasy"],
            "envelope-vocabulary",
        ),
        # -- nulls / empties ---------------------------------------------------
        ("provenance.evidence_id", None, "field-null"),
        ("provenance.evidence_id", "", "field-empty"),
        ("provenance.evidence_id", 30991849241, "field-type"),
        ("xrg3_verification.verified_by", "   ", "field-empty"),
    ],
)
def test_scalar_mutation_fails_closed(dotted: str, value: Any, expected_code: str) -> None:
    lock = read_lock()
    set_at(lock, dotted, value)
    violations = verify_no_vendor(lock)
    assert expected_code in codes(violations), [v.render() for v in violations]


@pytest.mark.parametrize(
    "token",
    [
        "SET-AT-PUBLICATION",
        "SELF-AT-PUBLICATION",
        "TBD",
        "PLACEHOLDER",
        "CHANGEME",
        "set-at-publication",
        "tbd",
    ],
)
def test_placeholder_token_anywhere_fails_closed(token: str) -> None:
    lock = read_lock()
    set_at(lock, "glue_registration.registry_name", token)
    violations = verify_no_vendor(lock)
    assert "placeholder" in codes(violations), [v.render() for v in violations]


def test_placeholder_token_is_found_deep_inside_a_list() -> None:
    lock = read_lock()
    lock["xrg3_verification"]["verification_method"].append("glue ids: TBD")
    assert "placeholder" in codes(verify_no_vendor(lock))


def test_tbd_substring_inside_a_word_does_not_false_positive() -> None:
    lock = read_lock()
    set_at(lock, "glue_registration.registry_name", "amh-fhir-dev-outbound")
    assert "placeholder" not in codes(verify_no_vendor(lock))


@pytest.mark.parametrize(
    "section",
    [
        "provenance",
        "manifest_pin",
        "glue_registration",
        "topics",
        "artifacts",
        "fixtures",
        "envelope",
        "xrg3_verification",
        "compatibility_report",
        "lock_format",
    ],
)
def test_removing_a_required_section_fails_closed(section: str) -> None:
    lock = read_lock()
    del lock[section]
    assert "section-missing" in codes(verify_no_vendor(lock))


def test_removing_a_schema_artifact_fails_closed() -> None:
    lock = read_lock()
    removed = lock["artifacts"].pop(2)
    violations = verify_no_vendor(lock)
    assert "artifact-missing" in codes(violations)
    assert "artifact-count" in codes(violations)
    assert removed["path"] in "\n".join(v.render() for v in violations)


def test_adding_an_unknown_artifact_fails_closed() -> None:
    lock = read_lock()
    lock["artifacts"].append({"path": "schemas/avro/integration/maezo/v1/rogue.avsc", "sha256": "a" * 64})
    violations = verify_no_vendor(lock)
    assert "artifact-unknown" in codes(violations)


def test_corrupting_an_artifact_digest_fails_closed() -> None:
    lock = read_lock()
    lock["artifacts"][0]["sha256"] = "b52fa765"
    assert "artifact-digest" in codes(verify_no_vendor(lock))


def test_renaming_a_topic_fails_closed() -> None:
    lock = read_lock()
    lock["topics"][0]["name"] = "amh.maezo.work-items.v2"
    violations = verify_no_vendor(lock)
    assert "topic-missing" in codes(violations)
    assert "topic-unknown" in codes(violations)


@pytest.mark.parametrize(
    ("key", "value", "expected_code"),
    [
        ("direction", "maezo-to-amh", "topic-direction"),
        ("schema_path", "schemas/avro/integration/maezo/v1/other.avsc", "topic-schema"),
        ("quarantine", "amh.maezo.work-items.quarantine.v1", "topic-quarantine"),
        ("quarantine", "amh.maezo.work-items.v1.quarantine.v2", "topic-quarantine"),
        ("major_version", 2, "topic-major"),
    ],
)
def test_topic_field_mutation_fails_closed(key: str, value: Any, expected_code: str) -> None:
    lock = read_lock()
    lock["topics"][0][key] = value
    assert expected_code in codes(verify_no_vendor(lock))


def test_dropping_a_topic_fails_closed() -> None:
    lock = read_lock()
    lock["topics"].pop()
    violations = verify_no_vendor(lock)
    assert "topic-count" in codes(violations)
    assert "topic-missing" in codes(violations)


def test_dropping_a_glue_version_id_fails_closed() -> None:
    lock = read_lock()
    del lock["glue_registration"]["schema_version_ids"]["maezo_amh_outcome"]
    violations = verify_no_vendor(lock)
    assert "glue-missing" in codes(violations)
    assert "glue-count" in codes(violations)


def test_reusing_one_glue_version_id_for_two_schemas_fails_closed() -> None:
    lock = read_lock()
    ids = lock["glue_registration"]["schema_version_ids"]
    ids["maezo_amh_outcome"] = ids["amh_maezo_consent"]
    assert "glue-duplicate" in codes(verify_no_vendor(lock))


def test_reordering_the_envelope_fails_closed() -> None:
    lock = read_lock()
    order = lock["envelope"]["field_order"]
    order[1], order[2] = order[2], order[1]
    violations = verify_no_vendor(lock)
    assert "envelope-order" in codes(violations)
    assert "first divergence at index 1" in "\n".join(v.render() for v in violations)


def test_inventing_an_envelope_field_fails_closed() -> None:
    lock = read_lock()
    lock["envelope"]["field_order"].append("source_patient_id")
    violations = verify_no_vendor(lock)
    assert "envelope-order" in codes(violations)
    assert "envelope-count" in codes(violations)


def test_manifest_digest_equal_to_prepublication_digest_fails_closed() -> None:
    lock = read_lock()
    set_at(lock, "manifest_pin.prepublication_sha256", lock["manifest_pin"]["sha256"])
    assert "manifest-digest-degenerate" in codes(verify_no_vendor(lock))


def test_violations_are_collected_not_short_circuited() -> None:
    """One run must report every problem — an early failure never masks a later one."""
    lock = read_lock()
    set_at(lock, "provenance.status", "DRAFT")
    set_at(lock, "manifest_pin.sha256", "nope")
    lock["topics"][0]["direction"] = "maezo-to-amh"
    lock["envelope"]["field_count"] = 27
    del lock["glue_registration"]["schema_version_ids"]["amh_maezo_consent"]
    found = codes(verify_no_vendor(lock))
    assert {"frozen-value", "format", "topic-direction", "envelope-count", "glue-missing"} <= found


# ---------------------------------------------------------------------------
# Layer 2b — the vendored-bytes gate, against a synthetic vendor directory
# ---------------------------------------------------------------------------


def test_temp_repo_baseline_passes(tmp_path: Path) -> None:
    """The synthetic harness itself is sound — otherwise the mutations below prove nothing."""
    root, vendor = build_temp_repo(tmp_path, read_lock())
    assert verify_lock(read_lock(), repo_root=root, vendor_dir=vendor) == []


def test_deleting_a_vendored_fixture_fails_closed(tmp_path: Path) -> None:
    root, vendor = build_temp_repo(tmp_path, read_lock())
    (vendor / "consent.revoked.json").unlink()
    violations = verify_lock(read_lock(), repo_root=root, vendor_dir=vendor)
    assert "fixture-missing" in codes(violations)


def test_adding_a_rogue_file_to_the_vendor_dir_fails_closed(tmp_path: Path) -> None:
    root, vendor = build_temp_repo(tmp_path, read_lock())
    (vendor / "work_item.local_hack.json").write_text("{}", encoding="utf-8")
    violations = verify_lock(read_lock(), repo_root=root, vendor_dir=vendor)
    assert "fixture-unlisted" in codes(violations)


def test_copying_an_editable_avro_schema_into_the_vendor_dir_fails_closed(tmp_path: Path) -> None:
    """ADR-0037 prohibition 4 enforced mechanically, not only by review."""
    root, vendor = build_temp_repo(tmp_path, read_lock())
    (vendor / "amh_maezo_work_item.avsc").write_text('{"type":"record"}', encoding="utf-8")
    assert "fixture-unlisted" in codes(verify_lock(read_lock(), repo_root=root, vendor_dir=vendor))


def test_tampering_one_fixture_byte_fails_closed(tmp_path: Path) -> None:
    root, vendor = build_temp_repo(tmp_path, read_lock())
    target = vendor / "work_item.eligibility_check.json"
    target.write_bytes(target.read_bytes().replace(b"routine", b"urgent!"))
    violations = verify_lock(read_lock(), repo_root=root, vendor_dir=vendor)
    assert "fixture-digest-mismatch" in codes(violations)


def test_appending_a_single_newline_to_a_fixture_fails_closed(tmp_path: Path) -> None:
    root, vendor = build_temp_repo(tmp_path, read_lock())
    target = vendor / "consent.granted.json"
    target.write_bytes(target.read_bytes() + b"\n")
    assert "fixture-digest-mismatch" in codes(verify_lock(read_lock(), repo_root=root, vendor_dir=vendor))


def test_removing_provenance_md_fails_closed(tmp_path: Path) -> None:
    root, vendor = build_temp_repo(tmp_path, read_lock())
    (vendor / "PROVENANCE.md").unlink()
    assert "provenance-missing" in codes(verify_lock(read_lock(), repo_root=root, vendor_dir=vendor))


def test_missing_vendor_dir_fails_closed(tmp_path: Path) -> None:
    assert "vendor-dir-missing" in codes(
        verify_lock(read_lock(), repo_root=tmp_path, vendor_dir=tmp_path / "nowhere")
    )


def test_relaxing_a_fixture_digest_in_the_lock_is_refused_by_the_drift_guard(tmp_path: Path) -> None:
    """The 'fix' a future agent would be tempted by: edit the digest so the red gate goes green.

    Locally that silences `fixture-digest-mismatch` — the digest gate alone cannot tell an edited
    pin from an honest one. The drift guard is what closes it: a fixture digest that moved without
    a canonical_schema_version increase and without a new AMH commit is refused outright.
    """
    root, vendor = build_temp_repo(tmp_path, read_lock())
    target = vendor / "outcome.revision-2.json"
    target.write_bytes(target.read_bytes().replace(b'"approved"', b'"denied"  '))
    forged = read_lock()
    for entry in forged["fixtures"]:
        if entry["vendored_path"].endswith("outcome.revision-2.json"):
            entry["sha256"] = hashlib.sha256(target.read_bytes()).hexdigest()

    assert "fixture-digest-mismatch" not in codes(verify_lock(forged, repo_root=root, vendor_dir=vendor))

    drift = codes(verify_candidate(read_lock(), forged))
    assert "candidate-silent-artifact-change" in drift
    assert "candidate-commit-regression" in drift


@pytest.mark.parametrize(
    ("key", "value", "expected_code"),
    [
        ("role", "probably-valid", "fixture-role"),
        ("sha256", "d766abc3", "fixture-digest"),
        ("sha256", "", "fixture-field"),
        ("vendored_path", "../../../etc/passwd", "fixture-path"),
        ("vendored_path", "/etc/passwd", "fixture-path"),
        ("path", None, "fixture-field"),
    ],
)
def test_fixture_entry_mutation_fails_closed(key: str, value: Any, expected_code: str) -> None:
    lock = read_lock()
    lock["fixtures"][1][key] = value
    assert expected_code in codes(verify_no_vendor(lock))


def test_duplicate_vendored_path_fails_closed() -> None:
    lock = read_lock()
    lock["fixtures"][2]["vendored_path"] = lock["fixtures"][1]["vendored_path"]
    assert "fixture-duplicate" in codes(verify_no_vendor(lock))


def test_emptying_the_fixture_list_fails_closed() -> None:
    lock = read_lock()
    lock["fixtures"] = []
    assert "fixtures-empty" in codes(verify_no_vendor(lock))


# ---------------------------------------------------------------------------
# Layer 2c — downgrade / drift guard (--candidate)
# ---------------------------------------------------------------------------


def legitimate_upgrade() -> dict[str, Any]:
    """A well-formed NEXT publication: new version, new commit, new evidence, newer run."""
    candidate = read_lock()
    set_at(candidate, "provenance.canonical_schema_version", "1.1.0")
    set_at(candidate, "envelope.canonical_schema_version", "1.1.0")
    set_at(candidate, "provenance.amh_commit_sha", "1" * 40)
    set_at(candidate, "provenance.amh_manifest_commit_sha", "2" * 40)
    set_at(candidate, "manifest_pin.sha256", "3" * 64)
    set_at(candidate, "manifest_pin.prepublication_sha256", "4" * 64)
    set_at(candidate, "provenance.evidence_id", "XRG2-AMH-DEV-GHA-40000000001")
    set_at(candidate, "glue_registration.publication_run_id", "40000000001")
    candidate["artifacts"][0]["sha256"] = "5" * 64
    return candidate


def test_identical_candidate_is_accepted() -> None:
    assert verify_candidate(read_lock(), read_lock()) == []


def test_legitimate_upgrade_is_accepted() -> None:
    current = read_lock()
    candidate = legitimate_upgrade()
    comparative = verify_candidate(current, candidate)
    assert comparative == [], [v.render() for v in comparative]
    structural = verify_lock(candidate, repo_root=REPO_ROOT, check_vendored=False)
    assert structural == [], [v.render() for v in structural]


@pytest.mark.parametrize("version", ["0.9.9", "1.0.0"])
def test_canonical_schema_version_downgrade_is_refused(version: str) -> None:
    current = read_lock()
    set_at(current, "provenance.canonical_schema_version", "1.2.0")
    candidate = legitimate_upgrade()
    set_at(candidate, "provenance.canonical_schema_version", version)
    assert "candidate-version-downgrade" in codes(verify_candidate(current, candidate))


def test_topic_removal_is_refused() -> None:
    candidate = legitimate_upgrade()
    candidate["topics"].pop()
    assert "candidate-topic-removed" in codes(verify_candidate(read_lock(), candidate))


def test_topic_rename_is_refused() -> None:
    candidate = legitimate_upgrade()
    candidate["topics"][0]["name"] = "amh.maezo.workitems.v1"
    assert "candidate-topic-removed" in codes(verify_candidate(read_lock(), candidate))


def test_topic_major_downgrade_is_refused() -> None:
    current = read_lock()
    for entry in current["topics"]:
        entry["name"] = entry["name"].replace(".v1", ".v2")
        entry["quarantine"] = entry["quarantine"].replace(".v1.quarantine.v1", ".v2.quarantine.v2")
        entry["major_version"] = 2
    candidate = copy.deepcopy(current)
    candidate["topics"][0]["major_version"] = 1
    set_at(candidate, "provenance.canonical_schema_version", "2.0.0")
    set_at(current, "provenance.canonical_schema_version", "2.0.0")
    assert "candidate-topic-major-downgrade" in codes(verify_candidate(current, candidate))


def test_quarantine_rename_is_refused() -> None:
    candidate = legitimate_upgrade()
    candidate["topics"][1]["quarantine"] = "amh.maezo.consent.v1.dlq.v1"
    assert "candidate-quarantine-renamed" in codes(verify_candidate(read_lock(), candidate))


def test_artifact_digest_change_without_a_version_bump_is_refused() -> None:
    candidate = read_lock()
    candidate["artifacts"][1]["sha256"] = "9" * 64
    set_at(candidate, "provenance.amh_commit_sha", "1" * 40)
    set_at(candidate, "manifest_pin.sha256", "3" * 64)
    set_at(candidate, "provenance.evidence_id", "XRG2-AMH-DEV-GHA-40000000001")
    set_at(candidate, "glue_registration.publication_run_id", "40000000001")
    assert "candidate-silent-artifact-change" in codes(verify_candidate(read_lock(), candidate))


def test_fixture_digest_change_with_a_version_bump_is_accepted() -> None:
    """The legitimate direction: AMH republished the vectors, and the version moved with them."""
    candidate = legitimate_upgrade()
    candidate["fixtures"][3]["sha256"] = "7" * 64
    assert verify_candidate(read_lock(), candidate) == []


def test_artifact_digest_change_without_a_commit_move_is_refused() -> None:
    candidate = legitimate_upgrade()
    set_at(candidate, "provenance.amh_commit_sha", read_lock()["provenance"]["amh_commit_sha"])
    assert "candidate-commit-regression" in codes(verify_candidate(read_lock(), candidate))


def test_reusing_a_spent_evidence_id_is_refused() -> None:
    candidate = legitimate_upgrade()
    set_at(candidate, "provenance.evidence_id", read_lock()["provenance"]["evidence_id"])
    assert "candidate-evidence-reuse" in codes(verify_candidate(read_lock(), candidate))


def test_publication_run_regression_is_refused() -> None:
    candidate = legitimate_upgrade()
    set_at(candidate, "glue_registration.publication_run_id", "10000000000")
    assert "candidate-evidence-regression" in codes(verify_candidate(read_lock(), candidate))


def test_same_publication_run_with_a_different_manifest_is_refused() -> None:
    candidate = legitimate_upgrade()
    set_at(
        candidate,
        "glue_registration.publication_run_id",
        read_lock()["glue_registration"]["publication_run_id"],
    )
    assert "candidate-evidence-regression" in codes(verify_candidate(read_lock(), candidate))


def test_unpublished_candidate_is_refused() -> None:
    candidate = legitimate_upgrade()
    set_at(candidate, "provenance.status", "UNPUBLISHED")
    assert "candidate-status" in codes(verify_candidate(read_lock(), candidate))


def test_contract_rename_is_refused() -> None:
    candidate = legitimate_upgrade()
    set_at(candidate, "provenance.contract_name", "amh-maezo-boundary-2")
    assert "candidate-contract-name" in codes(verify_candidate(read_lock(), candidate))


# ---------------------------------------------------------------------------
# Layer 2d — manifest byte mode
# ---------------------------------------------------------------------------


def test_manifest_mode_rejects_wrong_bytes(tmp_path: Path) -> None:
    """No published manifest bytes are vendored here (prohibition 4), so a synthetic file suffices."""
    bogus = tmp_path / "contract-manifest.yaml"
    bogus.write_text("status: PUBLISHED\n", encoding="utf-8")
    found = codes(verify_manifest(read_lock(), bogus))
    assert "manifest-digest-mismatch" in found
    assert "manifest-size-mismatch" in found
    assert "manifest-key-missing" in found


def test_manifest_mode_rejects_an_absent_file(tmp_path: Path) -> None:
    assert "manifest-missing" in codes(verify_manifest(read_lock(), tmp_path / "nope.yaml"))


# ---------------------------------------------------------------------------
# Layer 3 — the fixture bytes mean what the contract says
# ---------------------------------------------------------------------------


def load_fixture(entry: dict[str, Any]) -> dict[str, Any]:
    payload = json.loads((REPO_ROOT / entry["vendored_path"]).read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def test_there_are_six_valid_and_three_invalid_fixtures() -> None:
    lock = read_lock()
    assert len(fixtures_by_role(lock, "valid")) == 6
    assert len(fixtures_by_role(lock, "invalid")) == 3
    assert len(fixtures_by_role(lock, "readme")) == 1


def test_valid_fixtures_carry_exactly_the_28_frozen_envelope_fields_in_order() -> None:
    for entry in fixtures_by_role(read_lock(), "valid"):
        event = load_fixture(entry)
        envelope_fields = tuple(k for k in event if k != "payload")
        assert envelope_fields == FROZEN_ENVELOPE_FIELD_ORDER, entry["vendored_path"]
        assert len(envelope_fields) == 28
        assert "payload" in event, entry["vendored_path"]


def test_valid_fixture_payload_hashes_recompute_under_the_pinned_canonicalization() -> None:
    lock = read_lock()
    assert "sorted keys" in lock["envelope"]["payload_hash_canonicalization"]
    for entry in fixtures_by_role(lock, "valid"):
        event = load_fixture(entry)
        assert canonical_payload_hash(event["payload"]) == event["payload_hash"], entry["vendored_path"]


def test_payload_hash_is_sensitive_to_a_payload_edit() -> None:
    """Non-vacuity: the canonicalisation actually binds the payload."""
    entry = fixtures_by_role(read_lock(), "valid")[0]
    event = load_fixture(entry)
    tampered = dict(event["payload"])
    tampered["workflow_business_ref"] = "wf-tampered"
    assert canonical_payload_hash(tampered) != event["payload_hash"]


def test_valid_fixtures_declare_the_pinned_canonical_schema_version() -> None:
    lock = read_lock()
    expected = lock["envelope"]["canonical_schema_version"]
    for entry in fixtures_by_role(lock, "valid"):
        assert load_fixture(entry)["canonical_schema_version"] == expected, entry["vendored_path"]


def test_valid_fixtures_use_only_the_closed_source_product_vocabulary() -> None:
    for entry in fixtures_by_role(read_lock(), "valid"):
        assert load_fixture(entry)["source_product"] in FROZEN_SOURCE_PRODUCT_VOCABULARY, entry[
            "vendored_path"
        ]


def test_fixture_contract_manifest_digest_is_the_synthetic_placeholder_not_a_real_pin() -> None:
    """Pinned from the actual bytes: fixtures carry `ff...` (64 hex), never a real digest.

    The AMH fixtures README states this outright, and it matters for the consumer: a fixture's
    `contract_manifest_digest` must NEVER be mistaken for a binding to the published manifest. The
    real digest lives only in the pin.
    """
    lock = read_lock()
    real_digests = {lock["manifest_pin"]["sha256"], lock["manifest_pin"]["prepublication_sha256"]}
    real_digests |= {a["sha256"] for a in lock["artifacts"]}
    real_digests |= {f["sha256"] for f in lock["fixtures"]}
    for entry in lock["fixtures"]:
        if entry["role"] == "readme":
            continue
        declared = load_fixture(entry)["contract_manifest_digest"]
        assert declared == SYNTHETIC_MANIFEST_DIGEST, entry["vendored_path"]
        assert len(declared) == 64
        assert declared not in real_digests, entry["vendored_path"]


def test_no_fixture_leaks_a_raw_source_identifier() -> None:
    """ADR-0037 prohibition 5 — opaque refs only; no raw patient/MPI/beneficiary id on the wire."""
    forbidden_keys = {"mpi_id", "source_patient_id", "cpf", "cns", "patient_id", "beneficiary_id"}
    for entry in read_lock()["fixtures"]:
        if entry["role"] == "readme":
            continue
        event = load_fixture(entry)
        assert forbidden_keys.isdisjoint(event.keys()), entry["vendored_path"]
        assert forbidden_keys.isdisjoint(event.get("payload", {}).keys()), entry["vendored_path"]


# -- the three declared violations must be DETECTABLE by a consumer --------


def invalid_fixture(name: str) -> dict[str, Any]:
    entry = next(e for e in read_lock()["fixtures"] if e["vendored_path"].endswith(name))
    assert entry["role"] == "invalid"
    return load_fixture(entry)


def test_bad_source_product_fixture_is_rejectable() -> None:
    event = invalid_fixture("work_item.bad-source-product.invalid.json")
    assert event["source_product"] == "tasy"
    assert event["source_product"] not in FROZEN_SOURCE_PRODUCT_VOCABULARY


def test_missing_consent_decision_ref_fixture_is_rejectable() -> None:
    event = invalid_fixture("consent.missing-consent-decision-ref.invalid.json")
    assert event.get("consent_decision_ref") is None
    assert "consent_decision_ref" not in event
    # ... and it is the ONLY frozen envelope field it is missing — the violation is precise.
    missing = [f for f in FROZEN_ENVELOPE_FIELD_ORDER if f not in event]
    assert missing == ["consent_decision_ref"]


def test_non_integer_revision_fixture_is_rejectable() -> None:
    event = invalid_fixture("outcome.non-integer-revision.invalid.json")
    revision = event["payload"]["outcome_revision"]
    assert not isinstance(revision, int)
    assert isinstance(revision, str)
    assert revision == "1"


def test_valid_outcome_fixtures_carry_integer_monotonic_revisions() -> None:
    """The counterpart of the invalid one — the type IS an integer in the valid vectors."""
    revisions = []
    for entry in fixtures_by_role(read_lock(), "valid"):
        payload = load_fixture(entry)["payload"]
        if "outcome_revision" in payload:
            assert isinstance(payload["outcome_revision"], int)
            assert not isinstance(payload["outcome_revision"], bool)
            revisions.append(payload["outcome_revision"])
    assert sorted(revisions) == [1, 2]


def test_invalid_fixtures_all_still_parse_as_json() -> None:
    """They are semantically invalid, not syntactically broken — the consumer must reach the rule."""
    for entry in fixtures_by_role(read_lock(), "invalid"):
        assert isinstance(load_fixture(entry), dict)


def test_a_topic_major_past_the_int_conversion_limit_is_a_violation_not_a_crash() -> None:
    """The gate's own `topic_major` must REPORT a malformed major, never die computing it.

    `_TOPIC_MAJOR_RE` bounds the digit run for one reason: an unbounded `\\d+` matches an
    arbitrarily long run, and `int()` then hits CPython's 4300-digit conversion limit. A crafted pin
    made THIS GATE raise a bare `ValueError` — it still exited non-zero, so it failed closed by
    accident rather than by design, and the operator lost the violation report, which is the whole
    artifact the gate exists to produce.

    Mirrors the adapter-side pin in `tests/unit/adapters/amh/test_mapping.py`
    (`test_a_schema_version_past_the_int_conversion_limit_is_refused`): the two implementations of
    this rule must not drift, and until this test existed only the adapter's half was pinned.
    """
    # The real pinned names still resolve, so the bound did not cost anything real.
    assert topic_major("amh.maezo.work-items.v1") == 1
    assert topic_major("amh.maezo.consent.v1.quarantine.v1") == 1
    assert topic_major("maezo.amh.outcomes.v1") == 1

    # A 9-digit major is still a major; 10 digits is no longer recognised as one.
    assert topic_major("x.y.z.v" + "9" * 9) == int("9" * 9)
    assert topic_major("x.y.z.v" + "9" * 10) is None

    # The crash case: far past the interpreter's int() limit. `None` is a reportable violation
    # ("no trailing .vN major"); an exception would be the defect this pins.
    assert topic_major("x.y.z.v" + "9" * 5000) is None
