"""Unit tests for the fail-closed AMH pin loader (MZO-050a, ADR-0037 XRD-04).

Structure: the real committed pin loads and yields the pinned truth; then every fail-closed path is
exercised against a MUTATED COPY written to `tmp_path`. The real pin is never edited — it is
immutable (XRD-04) and digest-gated by `make verify-amh-contract-pin`.

XRD-04 names four failure modes verbatim ("Digest divergente, schema ausente, topico errado ou versao
rebaixada"); each has a test below, plus the ones the loader adds (unpublished status, placeholder
token, envelope-order drift, missing Glue id).
"""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from maezo.adapters.amh import contract as contract_module
from maezo.adapters.amh.contract import (
    CONTRACT_PIN_RELATIVE_PATH,
    FROZEN_COMPATIBILITY_RESULT,
    FROZEN_ENVELOPE_FIELD_ORDER,
    FROZEN_SCHEMA_VERSION_STATUS,
    MAEZO_AMH_CONTRACT_PIN_ENV,
    MAX_SEMVER_COMPONENT_DIGITS,
    REQUIRED_EVIDENCE_SECTIONS,
    AmhAdapterError,
    AmhContractPinError,
    _default_pin_path_candidates,
    load_contract_pin,
    parse_semver,
    resolve_contract_pin_path,
)

REPO_ROOT = Path(__file__).resolve().parents[4]
REAL_PIN = REPO_ROOT / CONTRACT_PIN_RELATIVE_PATH


def _real_pin_dict() -> dict[str, Any]:
    parsed: dict[str, Any] = json.loads(REAL_PIN.read_text(encoding="utf-8"))
    return parsed


def _write(tmp_path: Path, pin: dict[str, Any]) -> Path:
    target = tmp_path / "contracts.lock.json"
    target.write_text(json.dumps(pin), encoding="utf-8")
    return target


def _mutated(tmp_path: Path, mutate: Callable[[dict[str, Any]], None]) -> Path:
    pin = _real_pin_dict()
    mutate(pin)
    return _write(tmp_path, pin)


# ---------------------------------------------------------------------------
# Happy path against the REAL committed pin
# ---------------------------------------------------------------------------


def test_real_pin_loads_and_exposes_the_pinned_truth() -> None:
    pin = load_contract_pin(REAL_PIN)

    assert pin.status == "PUBLISHED"
    assert pin.compatibility_mode == "BACKWARD"
    assert pin.contract_name == "amh-maezo-boundary"
    assert pin.canonical_schema_version == "1.0.0"
    assert pin.canonical_schema_major == 1
    assert pin.envelope_field_count == 28
    assert pin.envelope_field_order == FROZEN_ENVELOPE_FIELD_ORDER
    assert pin.source_product_vocabulary == ("tasy_hospital", "tasy_healthcare_plan")
    assert pin.source_path == REAL_PIN


def test_real_pin_exposes_every_topic_with_its_quarantine_sibling_and_major() -> None:
    """`nack` must route to quarantine, so a topic without its sibling is unusable."""
    pin = load_contract_pin(REAL_PIN)
    assert [t.name for t in pin.topics] == [
        "amh.maezo.work-items.v1",
        "amh.maezo.consent.v1",
        "maezo.amh.outcomes.v1",
    ]
    for topic in pin.topics:
        assert topic.quarantine == f"{topic.name}.quarantine.v1"
        assert topic.major_version == 1
    assert pin.topic("amh.maezo.consent.v1").direction == "amh-to-maezo"
    assert pin.topic("maezo.amh.outcomes.v1").direction == "maezo-to-amh"


def test_real_pin_exposes_the_three_glue_schema_version_ids_and_registry() -> None:
    pin = load_contract_pin(REAL_PIN)
    assert pin.glue.region == "sa-east-1"
    assert pin.glue.registry_name == "amh-fhir-dev"
    assert pin.glue.schema_version_status == "AVAILABLE"
    assert set(pin.glue.schema_version_ids) == {
        "amh_maezo_work_item",
        "amh_maezo_consent",
        "maezo_amh_outcome",
    }
    assert pin.glue_schema_version_id("amh_maezo_work_item") == "76c0d475-59d3-4f45-94fc-fed3947ba84b"


def test_real_pin_exposes_artifact_fixture_and_manifest_digests() -> None:
    pin = load_contract_pin(REAL_PIN)
    assert len(pin.artifact_digests) == 5
    assert len(pin.fixture_digests) == 10
    assert pin.manifest_digest == "946266fb9ab10d27c01768e78fb3b673cba3db71680f652b10ccd0798065b126"
    assert pin.manifest_path == "schemas/contracts/maezo/v1/contract-manifest.yaml"
    assert all(len(d) == 64 for d in pin.artifact_digests.values())


def test_pinned_mappings_are_read_only_views() -> None:
    """A caller must not be able to mutate the pinned truth in place — a later validation would then
    be comparing against something nobody verified."""
    pin = load_contract_pin(REAL_PIN)
    with pytest.raises(TypeError):
        pin.artifact_digests["x"] = "y"  # type: ignore[index]
    with pytest.raises(TypeError):
        pin.glue.schema_version_ids["x"] = "y"  # type: ignore[index]


def test_unpinned_topic_and_glue_key_lookups_fail_closed() -> None:
    """A miss returns no `None` a caller could read as "nothing to do"."""
    pin = load_contract_pin(REAL_PIN)
    with pytest.raises(AmhContractPinError):
        pin.topic("cdc.amh.tasy.raw")
    with pytest.raises(AmhContractPinError):
        pin.glue_schema_version_id("not_a_pinned_schema")


def test_source_product_membership_is_closed() -> None:
    pin = load_contract_pin(REAL_PIN)
    assert pin.is_known_source_product("tasy_hospital")
    assert pin.is_known_source_product("tasy_healthcare_plan")
    assert not pin.is_known_source_product("tasy")
    assert not pin.is_known_source_product(None)
    assert not pin.is_known_source_product(42)


# ---------------------------------------------------------------------------
# Fail-closed: file-level
# ---------------------------------------------------------------------------


def test_missing_pin_is_refused(tmp_path: Path) -> None:
    with pytest.raises(AmhContractPinError, match="not found"):
        load_contract_pin(tmp_path / "absent.json")


def test_unparseable_pin_is_refused(tmp_path: Path) -> None:
    bad = tmp_path / "contracts.lock.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(AmhContractPinError, match="not valid JSON"):
        load_contract_pin(bad)


def test_non_object_pin_is_refused(tmp_path: Path) -> None:
    bad = tmp_path / "contracts.lock.json"
    bad.write_text('["a", "list"]', encoding="utf-8")
    with pytest.raises(AmhContractPinError, match="must be a JSON object"):
        load_contract_pin(bad)


# ---------------------------------------------------------------------------
# Fail-closed: XRD-04's four named failure modes + the loader's own
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", ["DRAFT", "PENDING", "REVOKED", "published", ""])
def test_status_other_than_published_is_refused(tmp_path: Path, status: str) -> None:
    """An adapter never runs against an unpublished contract."""
    path = _mutated(tmp_path, lambda p: p["provenance"].__setitem__("status", status))
    with pytest.raises(AmhContractPinError) as exc:
        load_contract_pin(path)
    assert any("status" in v for v in exc.value.violations)


@pytest.mark.parametrize(
    "token", ["SET-AT-PUBLICATION", "SELF-AT-PUBLICATION", "PLACEHOLDER", "CHANGEME", "TBD"]
)
def test_placeholder_token_anywhere_is_refused(tmp_path: Path, token: str) -> None:
    """An unfinished publication masquerading as a verified one."""
    path = _mutated(tmp_path, lambda p: p["glue_registration"].__setitem__("registry_name", token))
    with pytest.raises(AmhContractPinError) as exc:
        load_contract_pin(path)
    assert any("placeholder" in v for v in exc.value.violations)


def test_placeholder_is_detected_deep_inside_a_nested_list(tmp_path: Path) -> None:
    """Non-vacuity for the placeholder walk: it must reach list elements, not just top-level keys."""
    path = _mutated(tmp_path, lambda p: p["topics"][1].__setitem__("direction", "TBD"))
    with pytest.raises(AmhContractPinError) as exc:
        load_contract_pin(path)
    assert any("placeholder" in v for v in exc.value.violations)


@pytest.mark.parametrize("version", ["0.9.0", "0.0.1"])
def test_canonical_schema_version_downgrade_is_refused(tmp_path: Path, version: str) -> None:
    """XRD-04 "versao rebaixada": the running code would be newer than the contract it serves."""

    def mutate(p: dict[str, Any]) -> None:
        p["provenance"]["canonical_schema_version"] = version
        p["envelope"]["canonical_schema_version"] = version

    with pytest.raises(AmhContractPinError) as exc:
        load_contract_pin(_mutated(tmp_path, mutate))
    assert any("downgrade" in v for v in exc.value.violations)


def test_non_semver_canonical_schema_version_is_refused(tmp_path: Path) -> None:
    def mutate(p: dict[str, Any]) -> None:
        p["provenance"]["canonical_schema_version"] = "v1"
        p["envelope"]["canonical_schema_version"] = "v1"

    with pytest.raises(AmhContractPinError, match="MAJOR.MINOR.PATCH"):
        load_contract_pin(_mutated(tmp_path, mutate))


def test_provenance_and_envelope_version_disagreement_is_refused(tmp_path: Path) -> None:
    path = _mutated(tmp_path, lambda p: p["envelope"].__setitem__("canonical_schema_version", "1.1.0"))
    with pytest.raises(AmhContractPinError) as exc:
        load_contract_pin(path)
    assert any("!=" in v for v in exc.value.violations)


def test_a_forward_version_bump_is_accepted(tmp_path: Path) -> None:
    """The pin moving FORWARD is legitimate — only a downgrade is refused."""

    def mutate(p: dict[str, Any]) -> None:
        p["provenance"]["canonical_schema_version"] = "1.4.2"
        p["envelope"]["canonical_schema_version"] = "1.4.2"

    pin = load_contract_pin(_mutated(tmp_path, mutate))
    assert pin.canonical_schema_version == "1.4.2"
    assert pin.canonical_schema_major == 1


def test_envelope_field_order_drift_is_refused(tmp_path: Path) -> None:
    """XRD-04: the frozen baseline is byte-exact and ORDERED."""

    def mutate(p: dict[str, Any]) -> None:
        order = list(p["envelope"]["field_order"])
        order[3], order[4] = order[4], order[3]  # transpose occurred_at / ingested_at
        p["envelope"]["field_order"] = order

    with pytest.raises(AmhContractPinError) as exc:
        load_contract_pin(_mutated(tmp_path, mutate))
    assert any("first divergence at index 3" in v for v in exc.value.violations)


def test_envelope_field_removal_is_refused(tmp_path: Path) -> None:
    def mutate(p: dict[str, Any]) -> None:
        p["envelope"]["field_order"] = list(p["envelope"]["field_order"])[:-1]
        p["envelope"]["field_count"] = 27

    with pytest.raises(AmhContractPinError) as exc:
        load_contract_pin(_mutated(tmp_path, mutate))
    violations = " ".join(exc.value.violations)
    assert "field_count" in violations
    assert "field_order" in violations


def test_envelope_field_count_mismatch_is_refused(tmp_path: Path) -> None:
    path = _mutated(tmp_path, lambda p: p["envelope"].__setitem__("field_count", 27))
    with pytest.raises(AmhContractPinError, match="field_count"):
        load_contract_pin(path)


def test_source_product_vocabulary_widening_is_refused(tmp_path: Path) -> None:
    """ "Nenhum outro valor de `source_product` existe" — the vocabulary is closed by ADR."""
    path = _mutated(
        tmp_path,
        lambda p: p["envelope"].__setitem__(
            "source_product_vocabulary", ["tasy_hospital", "tasy_healthcare_plan", "tasy"]
        ),
    )
    with pytest.raises(AmhContractPinError, match="source_product_vocabulary"):
        load_contract_pin(path)


def test_topic_rename_is_refused(tmp_path: Path) -> None:
    """XRD-04 "topico errado"."""
    path = _mutated(tmp_path, lambda p: p["topics"][0].__setitem__("name", "amh.maezo.workitems.v1"))
    with pytest.raises(AmhContractPinError) as exc:
        load_contract_pin(path)
    violations = " ".join(exc.value.violations)
    assert "not in the ADR-0037 frozen catalogue" in violations
    assert "is absent" in violations


def test_quarantine_rename_is_refused(tmp_path: Path) -> None:
    path = _mutated(tmp_path, lambda p: p["topics"][0].__setitem__("quarantine", "amh.maezo.dlq.v1"))
    with pytest.raises(AmhContractPinError, match="quarantine"):
        load_contract_pin(path)


def test_topic_major_downgrade_is_refused(tmp_path: Path) -> None:
    path = _mutated(tmp_path, lambda p: p["topics"][2].__setitem__("major_version", 0))
    with pytest.raises(AmhContractPinError, match="major_version"):
        load_contract_pin(path)


def test_topic_name_without_a_major_suffix_is_refused(tmp_path: Path) -> None:
    def mutate(p: dict[str, Any]) -> None:
        p["topics"][0]["name"] = "amh.maezo.work-items.v1"
        p["topics"][0]["quarantine"] = "amh.maezo.work-items.quarantine"

    with pytest.raises(AmhContractPinError, match="trailing .vN major"):
        load_contract_pin(_mutated(tmp_path, mutate))


def test_missing_topic_is_refused(tmp_path: Path) -> None:
    """XRD-04 "schema ausente" at the topic level — a consumer cannot silently lose a stream."""
    path = _mutated(tmp_path, lambda p: p.__setitem__("topics", p["topics"][:2]))
    with pytest.raises(AmhContractPinError, match="frozen topic .* is absent"):
        load_contract_pin(path)


@pytest.mark.parametrize("key", ["amh_maezo_work_item", "amh_maezo_consent", "maezo_amh_outcome"])
def test_missing_glue_schema_version_id_is_refused(tmp_path: Path, key: str) -> None:
    path = _mutated(tmp_path, lambda p: p["glue_registration"]["schema_version_ids"].pop(key))
    with pytest.raises(AmhContractPinError, match=f"missing key '{key}'"):
        load_contract_pin(path)


def test_non_uuid_glue_schema_version_id_is_refused(tmp_path: Path) -> None:
    path = _mutated(
        tmp_path,
        lambda p: p["glue_registration"]["schema_version_ids"].__setitem__("amh_maezo_consent", "abc"),
    )
    with pytest.raises(AmhContractPinError, match="not a UUID"):
        load_contract_pin(path)


def test_duplicated_glue_schema_version_id_is_refused(tmp_path: Path) -> None:
    """Two schemas cannot share one Glue version id — one of them is not what the pin claims."""

    def mutate(p: dict[str, Any]) -> None:
        ids = p["glue_registration"]["schema_version_ids"]
        ids["amh_maezo_consent"] = ids["amh_maezo_work_item"]

    with pytest.raises(AmhContractPinError, match="reuses the id"):
        load_contract_pin(_mutated(tmp_path, mutate))


def test_missing_artifact_is_refused(tmp_path: Path) -> None:
    """XRD-04 "schema ausente"."""
    path = _mutated(tmp_path, lambda p: p.__setitem__("artifacts", p["artifacts"][1:]))
    with pytest.raises(AmhContractPinError, match="frozen artifact absent"):
        load_contract_pin(path)


def test_malformed_artifact_digest_is_refused(tmp_path: Path) -> None:
    """XRD-04 "digest divergente" in its structural form: a value that is not a sha256 at all."""
    path = _mutated(tmp_path, lambda p: p["artifacts"][0].__setitem__("sha256", "A" * 64))
    with pytest.raises(AmhContractPinError, match="not a 64-hex sha256"):
        load_contract_pin(path)


def test_malformed_manifest_digest_is_refused(tmp_path: Path) -> None:
    path = _mutated(tmp_path, lambda p: p["manifest_pin"].__setitem__("sha256", "deadbeef"))
    with pytest.raises(AmhContractPinError, match="manifest_pin.sha256"):
        load_contract_pin(path)


def test_unknown_artifact_path_is_refused(tmp_path: Path) -> None:
    path = _mutated(
        tmp_path,
        lambda p: p["artifacts"].append({"path": "schemas/avro/rogue.avsc", "sha256": "a" * 64}),
    )
    with pytest.raises(AmhContractPinError, match="not in the ADR-0037 frozen catalogue"):
        load_contract_pin(path)


def test_compatibility_mode_change_is_refused(tmp_path: Path) -> None:
    """The whole unknown-field tolerance in `mapping` rests on BACKWARD; a pin declaring something
    else would invalidate that reasoning silently."""
    path = _mutated(tmp_path, lambda p: p["provenance"].__setitem__("compatibility_mode", "NONE"))
    with pytest.raises(AmhContractPinError, match="compatibility_mode"):
        load_contract_pin(path)


def test_canonicalisation_declaration_losing_sorted_keys_is_refused(tmp_path: Path) -> None:
    """`mapping.canonical_payload_hash` sorts keys because the PIN says so. If the pin stopped saying
    it, the recomputation would silently stop reproducing producer hashes."""
    path = _mutated(
        tmp_path,
        lambda p: p["envelope"].__setitem__("payload_hash_canonicalization", "sha256 of the payload"),
    )
    with pytest.raises(AmhContractPinError, match="sorted keys"):
        load_contract_pin(path)


# ---------------------------------------------------------------------------
# Gate parity: the four pin states the loader used to accept and CI refuses
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", ["PENDING", "FAILURE", "DELETING", "available", ""])
def test_a_glue_schema_version_status_other_than_available_is_refused(tmp_path: Path, status: str) -> None:
    """LOW-3. `FROZEN_SCHEMA_VERSION_STATUS` was the ONE frozen constant the CI gate carried and the
    runtime loader did not, so the loader booted happily against a pin declaring a registry state in
    which phase B's decoder cannot resolve a single writer schema."""
    path = _mutated(tmp_path, lambda p: p["glue_registration"].__setitem__("schema_version_status", status))
    with pytest.raises(AmhContractPinError) as exc:
        load_contract_pin(path)
    assert any("schema_version_status" in v for v in exc.value.violations), exc.value.violations


@pytest.mark.parametrize("result", ["FAILED", "PENDING", "passed", ""])
def test_a_compatibility_report_that_did_not_pass_is_refused(tmp_path: Path, result: str) -> None:
    """LOW-3. The unknown-extra-field tolerance in `maezo.adapters.amh.mapping` (decision 3) rests on
    BACKWARD compatibility having been DEMONSTRATED. A pin carrying `FAILED` says it was not."""
    path = _mutated(tmp_path, lambda p: p["compatibility_report"].__setitem__("result", result))
    with pytest.raises(AmhContractPinError) as exc:
        load_contract_pin(path)
    assert any("compatibility_report.result" in v for v in exc.value.violations), exc.value.violations


@pytest.mark.parametrize("section", ["compatibility_report", "xrg3_verification"])
def test_a_deleted_evidence_section_is_refused(tmp_path: Path, section: str) -> None:
    """LOW-3, the easier attack. Tampering with a value inside a section is more work than deleting the
    section, and before this the loader read a pin with either one removed as valid — while the gate says
    in its own words that "a pin that has quietly lost its compatibility evidence or its XRG-3 record is
    not a pin"."""
    path = _mutated(tmp_path, lambda p: p.pop(section))
    with pytest.raises(AmhContractPinError) as exc:
        load_contract_pin(path)
    assert any(section in v and "evidence section absent" in v for v in exc.value.violations), (
        exc.value.violations
    )


@pytest.mark.parametrize("replacement", [{}, [], "PASSED", None, 42])
def test_an_evidence_section_that_is_not_a_populated_object_is_refused(
    tmp_path: Path, replacement: object
) -> None:
    """Emptying a section is the same attack as deleting it, one level down: an empty
    `xrg3_verification: {}` carries no verification record at all."""
    path = _mutated(tmp_path, lambda p: p.__setitem__("xrg3_verification", replacement))
    with pytest.raises(AmhContractPinError) as exc:
        load_contract_pin(path)
    assert any("xrg3_verification" in v for v in exc.value.violations), exc.value.violations


@pytest.mark.parametrize("field", ["verified_at_utc", "verified_by"])
def test_an_xrg3_record_missing_a_required_string_is_refused(tmp_path: Path, field: str) -> None:
    """Both strings the gate's `REQUIRED_STRING_FIELDS` demands of the section."""
    path = _mutated(tmp_path, lambda p: p["xrg3_verification"].pop(field))
    with pytest.raises(AmhContractPinError, match=f"xrg3_verification.{field}"):
        load_contract_pin(path)


def test_the_real_pin_satisfies_every_new_evidence_check() -> None:
    """NON-VACUITY for all of the above: the committed pin passes, so these are real comparisons rather
    than an unconditional refusal that would have bricked every deployment."""
    pin = load_contract_pin(REAL_PIN)
    assert pin.glue.schema_version_status == FROZEN_SCHEMA_VERSION_STATUS
    raw = _real_pin_dict()
    assert raw["compatibility_report"]["result"] == FROZEN_COMPATIBILITY_RESULT
    for section in REQUIRED_EVIDENCE_SECTIONS:
        assert isinstance(raw[section], dict) and raw[section]


def test_digest_recomputation_remains_out_of_scope_and_is_stated_as_such(tmp_path: Path) -> None:
    """The ONE gate check deliberately NOT duplicated here, recorded so it is a known boundary rather
    than an oversight: a tampered-but-well-formed `fixtures[].sha256` is accepted, because recomputing it
    needs the artifact BYTES and `schemas/` is not in the wheel (only `config/integrations/amh/` is
    force-included). The gate recomputes; the runtime loader validates SHAPE only. Structural malformation
    IS still caught — see `test_malformed_artifact_digest_is_refused`."""
    path = _mutated(tmp_path, lambda p: p["fixtures"][0].__setitem__("sha256", "0" * 64))
    pin = load_contract_pin(path)
    assert "0" * 64 in pin.fixture_digests.values()


# ---------------------------------------------------------------------------
# Strict semver (ONE validator, shared with maezo.adapters.amh.mapping)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("version", ["01.0.0", "1.00.0", "１.０.０", "1.0.٠", "1.0.0 ", " 1.0.0"])
def test_a_non_strict_semver_in_the_pin_is_refused(tmp_path: Path, version: str) -> None:
    """LOW-6. `\\d` in a `str` pattern matches every Unicode decimal digit, so the previous regex read
    fullwidth `１.０.０` and Arabic-Indic `1.0.٠` as version 1.0.0; leading zeros were accepted too. That
    matters inside this file: the downgrade check compares TUPLES while the provenance/envelope
    cross-check compares STRINGS, so two spellings of one version could pass one and fail the other."""

    def mutate(p: dict[str, Any]) -> None:
        p["provenance"]["canonical_schema_version"] = version
        p["envelope"]["canonical_schema_version"] = version

    with pytest.raises(AmhContractPinError, match="MAJOR.MINOR.PATCH"):
        load_contract_pin(_mutated(tmp_path, mutate))


def test_parse_semver_is_total_and_strict() -> None:
    """The shared validator never raises — a caller cannot leak an exception by using it — and it agrees
    with the mapping layer because it IS the mapping layer's validator."""
    from maezo.adapters.amh import mapping

    assert mapping.parse_semver is parse_semver
    assert parse_semver("1.0.0") == (1, 0, 0)
    assert parse_semver("0.0.0") == (0, 0, 0)
    assert parse_semver("1.10.20") == (1, 10, 20)
    for rejected in ("01.0.0", "１.０.０", "1.0.٠", "².0.0", "1.0", "v1", "", None, 1.0, ["1", "0", "0"]):
        assert parse_semver(rejected) is None, rejected


def test_parse_semver_totality_is_true_even_past_the_int_conversion_limit() -> None:
    """The docstring's totality claim was FALSE and load-bearing: consolidating the mapping layer onto
    this one validator was justified partly on it, and the unbounded `[0-9]*` let the regex match a
    digit run `int()` refuses (`sys.int_max_str_digits`, 4300 by default). `parse_semver("1"*4301 +
    ".0.0")` raised a bare `ValueError` — out of BOTH `load_contract_pin` and the mapping layer.

    The fix is in the GRAMMAR, not in a `try`, which is what makes the totality provable by
    inspection: every component is bounded to `MAX_SEMVER_COMPONENT_DIGITS`, so `int()` cannot fail."""
    over = "1" * (sys.get_int_max_str_digits() + 1)
    assert parse_semver(f"{over}.0.0") is None
    assert parse_semver(f"1.{over}.0") is None
    assert parse_semver(f"1.0.{over}") is None
    assert parse_semver("9" * 4301) is None


def test_the_semver_component_bound_is_an_exact_edge() -> None:
    """Bounded, not merely "small": exactly `MAX_SEMVER_COMPONENT_DIGITS` digits parses and one more
    does not, in every one of the three positions."""
    nine = "9" * MAX_SEMVER_COMPONENT_DIGITS
    ten = f"9{nine}"
    assert parse_semver(f"{nine}.{nine}.{nine}") == (int(nine), int(nine), int(nine))
    for spelling in (f"{ten}.0.0", f"0.{ten}.0", f"0.0.{ten}"):
        assert parse_semver(spelling) is None, spelling
    assert MAX_SEMVER_COMPONENT_DIGITS < 640, (
        "640 is the lowest sys.int_max_str_digits CPython accepts, so the bound must stay below it or "
        "the totality proof stops holding on an embedder that lowers the limit"
    )


def test_the_ascii_only_guarantee_comes_from_the_character_classes_not_a_flag() -> None:
    """The pattern used to carry `re.ASCII` with a comment claiming the flag "matters". It did not:
    `re.ASCII` only changes what `\\d`, `\\w`, `\\s` and `\\b` mean, and the pattern contains none of
    them — so the flag was provably inert and a mutation deleting it survived because it was a no-op.

    This test pins the guarantee to its real mechanism, so a future "simplification" of `[0-9]` back to
    `\\d` fails HERE rather than silently re-admitting fullwidth and Arabic-Indic digits."""
    assert not contract_module._SEMVER_RE.flags & re.ASCII, (
        "the flag is gone on purpose — it was inert against explicit classes, and keeping an inert "
        "flag is what made a reader believe it was the protection"
    )
    assert "\\d" not in contract_module._SEMVER_RE.pattern, (
        "no bare `\\d` in this pattern: in a `str` pattern it matches every Unicode decimal digit"
    )
    for unicode_digits in ("１.０.０", "1.0.٠", "٣.٠.٠", "1.٢.0", "².0.0"):
        assert parse_semver(unicode_digits) is None, unicode_digits


def test_a_topic_major_past_the_int_conversion_limit_is_a_violation_not_a_crash(tmp_path: Path) -> None:
    """`_topic_major` fed an unbounded `\\d+` capture to `int()`, so a quarantine name carrying a
    5000-digit major raised a bare `ValueError` out of the loader. Same root cause as the semver one,
    a different call — which is exactly why the sweep had to be systematic rather than per-finding."""
    over = "1" * (sys.get_int_max_str_digits() + 1)
    path = _mutated(tmp_path, lambda p: p["topics"][0].__setitem__("quarantine", f"q.v{over}"))
    with pytest.raises(AmhContractPinError) as exc:
        load_contract_pin(path)
    assert any("quarantine" in v for v in exc.value.violations), exc.value.violations


# ---------------------------------------------------------------------------
# The read/parse layer: what `read_text` and `json.loads` raise that is not what was caught
# ---------------------------------------------------------------------------


def test_a_pin_file_that_is_not_valid_utf8_is_refused(tmp_path: Path) -> None:
    """`UnicodeDecodeError` is a `ValueError`, NOT an `OSError`, so the `except OSError` on `read_text`
    never saw it and one invalid byte crashed the loader. The pin is DECLARED UTF-8 — `encoding="utf-8"`
    is the contract, not a hint — so non-UTF-8 bytes are a refusal like any other."""
    path = tmp_path / "contracts.lock.json"
    path.write_bytes(b'{"provenance": {"status": "PUBLISH\xffED"}}')
    with pytest.raises(AmhContractPinError) as exc:
        load_contract_pin(path)
    assert any("not valid UTF-8" in v for v in exc.value.violations), exc.value.violations


def test_a_pin_file_nested_past_the_json_parser_budget_is_refused(tmp_path: Path) -> None:
    """`json.loads` raises `RecursionError`, which is not a `JSONDecodeError`."""
    path = tmp_path / "contracts.lock.json"
    path.write_text("[" * 200_000 + "]" * 200_000, encoding="utf-8")
    with pytest.raises(AmhContractPinError) as exc:
        load_contract_pin(path)
    assert any("not parseable as JSON" in v for v in exc.value.violations), exc.value.violations


def test_a_pin_file_with_an_integer_literal_past_the_int_limit_is_refused(tmp_path: Path) -> None:
    """Well-formed JSON that CPython declines to convert: `json.loads` raises a plain `ValueError`, not
    a `JSONDecodeError`, so the original clause did not hold it either."""
    path = tmp_path / "contracts.lock.json"
    over = "1" * (sys.get_int_max_str_digits() + 1)
    path.write_text(f'{{"envelope": {{"field_count": {over}}}}}', encoding="utf-8")
    with pytest.raises(AmhContractPinError) as exc:
        load_contract_pin(path)
    assert any("not parseable as JSON" in v for v in exc.value.violations), exc.value.violations


@pytest.mark.parametrize("depth", [1_200, 5_000, 9_900])
def test_the_placeholder_walk_survives_nesting_json_loads_accepts(tmp_path: Path, depth: int) -> None:
    """The gap that made the recursive walk unsafe: `json.loads` parses ~10x deeper than a Python-level
    walk of the SAME object survives (~9997 vs ~1000), so every depth in this band is a file the parser
    accepts and the walker used to die on — a bare `RecursionError` out of `load_contract_pin`.

    Fixed by making `_iter_strings` ITERATIVE, so the failure is impossible rather than caught. These
    depths must produce a normal fail-closed refusal (the nested key is not in the frozen catalogue)."""
    base = REAL_PIN.read_text(encoding="utf-8").rstrip().rstrip("}").rstrip()
    nested = '{"a":' * depth + '"x"' + "}" * depth
    path = tmp_path / "contracts.lock.json"
    path.write_text(f'{base},\n"extra_nest": {nested}\n}}', encoding="utf-8")
    load_contract_pin(path)  # an unknown EXTRA top-level key is not itself a violation


def test_the_placeholder_walk_still_reports_paths_in_source_order(tmp_path: Path) -> None:
    """Making the walk iterative must not have reversed the order violations are reported in — an
    explicit LIFO stack yields children backwards unless they are pushed reversed."""
    path = _mutated(
        tmp_path,
        lambda p: p.__setitem__("probe", ["TBD-first", {"k": "PLACEHOLDER-second"}, "CHANGEME-third"]),
    )
    with pytest.raises(AmhContractPinError) as exc:
        load_contract_pin(path)
    paths = [v.split(":")[0] for v in exc.value.violations if v.startswith("$.probe")]
    assert paths == ["$.probe[0]", "$.probe[1].k", "$.probe[2]"], exc.value.violations


# ---------------------------------------------------------------------------
# Path resolution: `expanduser` / `resolve` / `is_file` are not the total calls they look like
# ---------------------------------------------------------------------------


def test_an_unresolvable_home_directory_override_is_a_refusal_not_a_crash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`Path.expanduser()` raises `RuntimeError("Could not determine home directory.")` for a `~user`
    it cannot resolve — a type nothing in this module was catching."""
    monkeypatch.setenv(MAEZO_AMH_CONTRACT_PIN_ENV, "~nosuchuser4711/contracts.lock.json")
    with pytest.raises(AmhContractPinError) as exc:
        resolve_contract_pin_path()
    assert any("not resolvable to a filesystem path" in v for v in exc.value.violations)
    assert any(MAEZO_AMH_CONTRACT_PIN_ENV in v for v in exc.value.violations), (
        "the refusal must still tell the operator which knob is wrong"
    )


def test_an_overlong_override_path_is_a_refusal_not_a_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    """`Path.is_file()` LOOKS total and is not: it swallows only the errnos in its own `_ignore_error`
    list (ENOENT/ENOTDIR/EBADF/ELOOP/EINVAL) and re-raises everything else — so `ENAMETOOLONG` came
    straight out of a line that reads as a pure predicate, which is why it was never guarded."""
    monkeypatch.setenv(MAEZO_AMH_CONTRACT_PIN_ENV, "/tmp/" + "x" * 5_000 + ".json")
    with pytest.raises(AmhContractPinError) as exc:
        resolve_contract_pin_path()
    assert exc.value.violations


def test_an_overlong_explicit_path_is_a_refusal_not_a_crash() -> None:
    """The same `is_file()` trap on the OTHER call site — `load_contract_pin(path)` — because the fix
    had to be a choke point, not a guard at the one place the finding happened to name."""
    with pytest.raises(AmhContractPinError) as exc:
        load_contract_pin(Path("/tmp/" + "x" * 5_000 + ".json"))
    assert any("not found" in v for v in exc.value.violations), exc.value.violations


def test_a_path_with_an_embedded_nul_is_a_refusal_not_a_crash() -> None:
    """`Path.is_file()` does catch `ValueError`, so this one already worked — pinned so the choke-point
    helper cannot regress it while fixing the errno case above."""
    with pytest.raises(AmhContractPinError) as exc:
        load_contract_pin(Path("a\x00b.json"))
    assert exc.value.violations


# ---------------------------------------------------------------------------
# Bounded echo of pin content
# ---------------------------------------------------------------------------


def test_a_short_pin_value_is_echoed_exactly_as_repr(tmp_path: Path) -> None:
    """The bound must be invisible to every legitimate pin: below it the rendering is byte-identical to
    `repr`, which is what keeps the existing violation texts unchanged."""
    path = _mutated(tmp_path, lambda p: p["provenance"].__setitem__("status", "DRAFT"))
    with pytest.raises(AmhContractPinError) as exc:
        load_contract_pin(path)
    assert any("got 'DRAFT'" in v for v in exc.value.violations), exc.value.violations


def test_an_absurdly_long_pin_value_is_bounded_in_the_violation(tmp_path: Path) -> None:
    """`$MAEZO_AMH_CONTRACT_PIN` can point at ANY readable JSON, so an unbounded echo would let a
    misconfigured override paste an arbitrary file's strings into a log line. Bounded, with the true
    length named so an operator knows the value was long rather than mangled."""
    huge = "Z" * 50_000
    path = _mutated(tmp_path, lambda p: p["provenance"].__setitem__("status", huge))
    with pytest.raises(AmhContractPinError) as exc:
        load_contract_pin(path)

    offending = [v for v in exc.value.violations if "provenance.status" in v]
    assert offending, exc.value.violations
    for violation in offending:
        assert len(violation) < 1000, f"unbounded echo: {len(violation)} chars"
        assert huge not in violation
    assert any("chars, elided" in v for v in offending)
    assert len(str(exc.value)) < 5000


def test_an_absurdly_long_json_path_is_bounded_too(tmp_path: Path) -> None:
    """The placeholder walk reports a JSON PATH assembled from the scanned file's own KEYS, so `$.<64KB
    key>` is exactly as unbounded as a 64KB value. This is the half a value-only bound would have missed."""
    path = _mutated(tmp_path, lambda p: p["provenance"].__setitem__("Q" * 50_000, "CHANGEME"))
    with pytest.raises(AmhContractPinError) as exc:
        load_contract_pin(path)
    offending = [v for v in exc.value.violations if "placeholder" in v]
    assert offending, exc.value.violations
    for violation in offending:
        assert len(violation) < 1000, f"unbounded echo: {len(violation)} chars"
        assert "Q" * 50_000 not in violation


def test_violations_are_collected_not_masked(tmp_path: Path) -> None:
    """Non-masking: one load reports the COMPLETE violation set, mirroring the CI gate's design."""

    def mutate(p: dict[str, Any]) -> None:
        p["provenance"]["status"] = "DRAFT"
        p["envelope"]["field_count"] = 27
        p["glue_registration"]["schema_version_ids"].pop("maezo_amh_outcome")
        p["topics"][0]["quarantine"] = "wrong.v1"

    with pytest.raises(AmhContractPinError) as exc:
        load_contract_pin(_mutated(tmp_path, mutate))
    violations = " ".join(exc.value.violations)
    assert len(exc.value.violations) >= 4, exc.value.violations
    for expected in ("status", "field_count", "maezo_amh_outcome", "quarantine"):
        assert expected in violations
    # The count is also in the message, so a log line alone tells an operator the scale of the problem.
    assert "violation(s)" in str(exc.value)


def test_pin_error_is_an_adapter_error() -> None:
    """The port boundary catches `AmhAdapterError` and maps to CONTRACT_VIOLATION, so the pin error
    must be part of that hierarchy."""
    assert issubclass(AmhContractPinError, AmhAdapterError)
    assert issubclass(AmhAdapterError, Exception)


# ---------------------------------------------------------------------------
# Path resolution / packaging
# ---------------------------------------------------------------------------


def test_default_resolution_finds_the_repo_checkout_pin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(MAEZO_AMH_CONTRACT_PIN_ENV, raising=False)
    assert resolve_contract_pin_path() == REAL_PIN


def test_both_layout_candidates_are_offered_for_a_normal_repo_layout() -> None:
    """G07, the positive half. The two candidates are the repo checkout (`parents[4]`) and the
    package-adjacent wheel copy (`parents[2]`), and they are computed by unpinned index arithmetic — so
    pin WHAT they are as well as that neither raises."""
    candidates = _default_pin_path_candidates()
    parents = Path(contract_module.__file__ or "").resolve().parents
    assert candidates == (
        parents[4] / CONTRACT_PIN_RELATIVE_PATH,
        parents[2] / CONTRACT_PIN_RELATIVE_PATH,
    )
    assert candidates[0] == REAL_PIN


@pytest.mark.parametrize(
    ("module_file", "expected_candidates"),
    [
        ("/pkg/maezo/adapters/amh/contract.py", 2),  # 5 parents: both candidates
        ("/a/b/c/contract.py", 1),  # 4 parents: only the parents[2] candidate
        ("/a/b/contract.py", 1),  # 3 parents: only the parents[2] candidate
        ("/a/contract.py", 0),  # 2 parents: neither
        ("/contract.py", 0),  # 1 parent: neither
    ],
    ids=["deep", "four", "three", "two", "one"],
)
def test_a_layout_too_shallow_for_a_candidate_omits_it_instead_of_raising(
    monkeypatch: pytest.MonkeyPatch, module_file: str, expected_candidates: int
) -> None:
    """G07, the surviving-mutant half. `parents[4]` / `parents[2]` are unpinned index arithmetic guarded
    by `len(parents) > 4` / `> 2`; delete either guard and the corresponding shallow layout raises a bare
    `IndexError` out of the adapter instead of the fail-closed `AmhContractPinError` an operator can act
    on. Each depth here is chosen to be the one that trips exactly one of the two guards."""
    monkeypatch.setattr(contract_module, "__file__", module_file)
    candidates = _default_pin_path_candidates()  # must not raise IndexError
    assert len(candidates) == expected_candidates
    assert all(c.name == "contracts.lock.json" for c in candidates)


@pytest.mark.parametrize("module_file", ["/a/contract.py", "/contract.py"])
def test_a_layout_with_no_candidate_at_all_fails_closed_not_with_an_indexerror(
    monkeypatch: pytest.MonkeyPatch, module_file: str
) -> None:
    """The consequence that makes the guards load-bearing: with no candidate the resolver must raise the
    ADAPTER's own fail-closed error (which a port maps to CONTRACT_VIOLATION), never an `IndexError`
    escaping the boundary."""
    monkeypatch.setattr(contract_module, "__file__", module_file)
    monkeypatch.delenv(MAEZO_AMH_CONTRACT_PIN_ENV, raising=False)
    with pytest.raises(AmhContractPinError) as exc:
        resolve_contract_pin_path()
    assert "not found at any default candidate" in str(exc.value)
    assert MAEZO_AMH_CONTRACT_PIN_ENV in str(exc.value), "the refusal must tell the operator how to fix it"


def test_env_override_is_honoured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A deployment whose pin lives somewhere the defaults do not cover."""
    relocated = _write(tmp_path, _real_pin_dict())
    monkeypatch.setenv(MAEZO_AMH_CONTRACT_PIN_ENV, str(relocated))
    assert resolve_contract_pin_path() == relocated.resolve()
    assert load_contract_pin().source_path == relocated.resolve()


def test_env_override_pointing_at_a_missing_file_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No fallback to a default: an explicit-but-wrong override is a configuration error, and silently
    running against a DIFFERENT pin than the one declared is the failure this prevents."""
    monkeypatch.setenv(MAEZO_AMH_CONTRACT_PIN_ENV, str(tmp_path / "nope.json"))
    with pytest.raises(AmhContractPinError, match="pin file not found"):
        resolve_contract_pin_path()


def test_env_override_of_a_tampered_pin_still_fails_the_content_checks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The override changes WHERE the pin is read from, never WHETHER it is verified."""
    path = _mutated(tmp_path, lambda p: p["provenance"].__setitem__("status", "DRAFT"))
    monkeypatch.setenv(MAEZO_AMH_CONTRACT_PIN_ENV, str(path))
    with pytest.raises(AmhContractPinError, match="status"):
        load_contract_pin()


def test_blank_env_override_falls_through_to_the_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty env var is how a Helm template renders an unset optional value; it must not be read
    as "the pin lives at the empty path"."""
    monkeypatch.setenv(MAEZO_AMH_CONTRACT_PIN_ENV, "")
    assert resolve_contract_pin_path() == REAL_PIN


def test_wheel_layout_candidate_is_resolvable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """PACKAGING PROOF (the wheel half): with no repo checkout above it, the loader must still find a
    pin laid down package-adjacent at `maezo/config/integrations/amh/`.

    Simulates an installed wheel by building that exact tree and importing `contract.py` from it, so
    `Path(__file__).parents` has the site-packages shape rather than the repo shape. This is the
    layout `[tool.hatch.build.targets.wheel.force-include]` produces; without the second candidate in
    `_default_pin_path_candidates` a packaged deployment would boot with no pin at all.
    """
    import importlib.util

    site = tmp_path / "site-packages"
    pkg = site / "maezo" / "adapters" / "amh"
    pkg.mkdir(parents=True)
    shipped = site / "maezo" / CONTRACT_PIN_RELATIVE_PATH
    shipped.parent.mkdir(parents=True)
    shipped.write_text(REAL_PIN.read_text(encoding="utf-8"), encoding="utf-8")

    module_path = pkg / "contract.py"
    module_path.write_text(
        (REPO_ROOT / "src/maezo/adapters/amh/contract.py").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    spec = importlib.util.spec_from_file_location("wheel_shaped_contract", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Registered before exec: `@dataclass(slots=True)` rebuilds each class and resolves its
    # annotations through `sys.modules[cls.__module__]`, which is absent for a spec-loaded module.
    monkeypatch.setitem(sys.modules, spec.name, module)
    monkeypatch.delenv(MAEZO_AMH_CONTRACT_PIN_ENV, raising=False)
    spec.loader.exec_module(module)

    # The repo-checkout candidate (parents[4]) does not exist in this tree; the wheel candidate
    # (parents[2]) does. Prove BOTH: the resolved path is the shipped one, and it actually loads.
    repo_shaped_candidate = tmp_path / CONTRACT_PIN_RELATIVE_PATH
    assert not repo_shaped_candidate.exists(), "the repo-checkout candidate must be absent here"
    resolved = module.resolve_contract_pin_path()
    assert resolved == shipped, f"wheel-layout resolution picked {resolved}, expected {shipped}"
    pin = module.load_contract_pin()
    assert pin.status == "PUBLISHED"
    assert pin.source_path == shipped


def test_wheel_layout_without_a_shipped_pin_fails_closed_naming_its_candidates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The counter-test that gives the one above its meaning: remove the force-included pin and the
    same wheel-shaped tree must refuse to resolve — proving the previous test passed because the
    shipped file was FOUND, not because some other path happened to work."""
    import importlib.util

    site = tmp_path / "site-packages"
    pkg = site / "maezo" / "adapters" / "amh"
    pkg.mkdir(parents=True)
    module_path = pkg / "contract.py"
    module_path.write_text(
        (REPO_ROOT / "src/maezo/adapters/amh/contract.py").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    spec = importlib.util.spec_from_file_location("wheel_shaped_contract_no_pin", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    monkeypatch.delenv(MAEZO_AMH_CONTRACT_PIN_ENV, raising=False)
    spec.loader.exec_module(module)

    with pytest.raises(module.AmhContractPinError) as exc:
        module.resolve_contract_pin_path()
    message = str(exc.value)
    assert "not found at any default candidate" in message
    assert MAEZO_AMH_CONTRACT_PIN_ENV in message, "the refusal must tell the operator how to fix it"
    assert CONTRACT_PIN_RELATIVE_PATH in message
