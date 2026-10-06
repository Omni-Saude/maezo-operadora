"""Pure mechanism tests; null/technical arithmetic fixtures are not source data.

No source/owner/privacy/current-head proof or operational numerical release is
qualified by these tests. The real-wheel smoke exercises this module/asset only.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import sys
from dataclasses import FrozenInstanceError, asdict, fields, replace
from importlib import resources
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch
from zipfile import ZipFile

import pytest

from maezo.gateway import aggregate_export_validation as module
from maezo.gateway.aggregate_export_validation import (
    AggregateValidationRefusal as Refusal,
)
from maezo.gateway.aggregate_export_validation import (
    AggregateValidationResult,
    ConstraintCheck,
    ConstraintCode,
    ConstraintStatus,
    ProposedRecordKind,
    ValidationTechnicalStatus,
    validate_proposed_record,
)

ROOT = Path(__file__).resolve().parents[3]
PROPOSALS = ROOT / "docs/design/v21-capabilities/proposals"
CANONICAL = PROPOSALS / "measurement-export-closed-shapes.proposed-v2.json"
PROTOCOL = PROPOSALS / "measurement-protocol.proposed-v2.json"
ASSET = ROOT / "src/maezo/gateway/measurement-export-closed-shapes.proposed-v2.json"
PIN = "9f7063d5e3a633e22459be25e6c61ce64e07eaff92ccb7259deaf06bc577313c"
SCHEMAS = json.loads(CANONICAL.read_text())
METRICS = json.loads(PROTOCOL.read_text())["metrics"]
IDS = [metric["id"] for metric in METRICS]
AGGREGATE: ProposedRecordKind = "minimized_aggregate_export"


def raw(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode()


def null_aggregate(mid: str = "M10") -> dict[str, Any]:
    shape = SCHEMAS[AGGREGATE]
    metadata = dict.fromkeys(shape["$defs"]["metadata"]["required"])
    metadata["suppressed"] = True
    metric = next(metric for metric in METRICS if metric["id"] == mid)
    result = dict(
        schema_version="v21-measurement-aggregate-export.proposed.v2",
        metric_id=mid,
        measurement_status="UNMEASURED",
        export_state="PROPOSED_UNADMITTED",
        metadata=metadata,
        components=dict.fromkeys(metric["allowed_aggregate_output_components_proposed"]),
        numerator=None,
        denominator=None,
        ratio=None,
        distribution=None,
    )
    if mid == "M01":
        result["no_offer_rate"] = None
    return result


def technical_m10() -> dict[str, Any]:
    """Explicit arithmetic oracle; declarations do not attest any source fact."""
    result = null_aggregate()
    result["measurement_status"] = "INCOMPLETE"
    result["export_state"] = "PROVISIONAL"
    metadata = result["metadata"]
    for key in metadata:
        metadata[key] = "unit-declaration-not-authority"
    metadata.update(
        suppressed=False,
        applied_minimum_k=10,
        aggregate_content_digest="a" * 64,
        supersedes_aggregate_revision="opaque-earlier/Z",
    )
    result["components"].update(
        candidate_total_n=26,
        committed_current_n=10,
        valid_current_n=4,
        missing_current_n=3,
        receipt_uncertain_current_n=3,
        committed_withdrawn_n=5,
        valid_withdrawn_n=2,
        missing_withdrawn_n=1,
        receipt_uncertain_withdrawn_n=2,
        commit_unknown_current_n=6,
        commit_unknown_withdrawn_n=0,
        not_committed_current_n=1,
        not_committed_withdrawn_n=2,
        eligibility_unknown_n=2,
        immature_current_n=2,
        right_censored_current_n=1,
    )
    result.update(numerator=4, denominator=10, ratio=0.4)
    return result


def check(result: AggregateValidationResult, code: ConstraintCode) -> ConstraintStatus:
    return next(item.status for item in result.checks if item.constraint == code)


def unadmitted(result: AggregateValidationResult) -> None:
    assert result.source_authority_assessed is False
    assert result.source_currentness_assessed is False
    assert result.privacy_release_assessed is False
    assert result.numeric_disclosure_permitted is False
    assert set(asdict(result)) == {field.name for field in fields(AggregateValidationResult)}


@pytest.mark.parametrize("metric", METRICS, ids=IDS)
def test_all_exact_pending_metric_definitions_and_components_consumed(metric: dict[str, Any]) -> None:
    for kind, field in (
        ("metric_definition_pending_admission", "definition_admission_proposed"),
        ("metric_component_mapping_pending_admission", "component_mapping_admission_proposed"),
    ):
        result = validate_proposed_record(cast(ProposedRecordKind, kind), raw(metric[field]))
        assert result.technical_status == ValidationTechnicalStatus.CHECKED_UNADMITTED
        assert result.schema_sha256 == PIN
        unadmitted(result)


@pytest.mark.parametrize("mid", IDS)
def test_all_null_aggregate_shapes_remain_unmeasured_and_unadmitted(mid: str) -> None:
    result = validate_proposed_record(AGGREGATE, raw(null_aggregate(mid)))
    assert result.technical_status == ValidationTechnicalStatus.CHECKED_UNADMITTED
    assert check(result, ConstraintCode.DECLARED_K_FLOOR) == ConstraintStatus.NOT_ASSESSABLE
    assert check(result, ConstraintCode.DECLARED_HEAD_EQUALITY) == ConstraintStatus.NOT_ASSESSABLE
    unadmitted(result)


def test_private_computation_shape_is_not_export_and_never_echoes_input() -> None:
    shape = SCHEMAS["provider_private_computation_row"]
    value = dict.fromkeys(shape["required"])
    value.update(
        schema_version="v21-measurement-provider-private-lineage.proposed.v2",
        metric_id="M10",
        source_record_ref="UNIT_PRIVATE_SOURCE_MARKER_NOT_PROOF",
    )
    result = validate_proposed_record("provider_private_computation_row", raw(value))
    assert result.technical_status == ValidationTechnicalStatus.CHECKED_UNADMITTED
    assert "UNIT_PRIVATE_SOURCE_MARKER" not in repr(result)
    unadmitted(result)
    assert validate_proposed_record(AGGREGATE, raw(value)).reason == Refusal.SCHEMA_MISMATCH


@pytest.mark.parametrize("kind", ["unknown-private-kind", "MINIMIZED_AGGREGATE_EXPORT", 7, True, None])
def test_wrong_kind_has_no_schema_fallback_or_resource_access(kind: Any) -> None:
    with patch.object(resources, "files", side_effect=AssertionError("must not read")):
        result = validate_proposed_record(kind, raw(null_aggregate()))
    assert result.reason == Refusal.INVALID_RECORD_KIND and result.record_kind is None
    unadmitted(result)


@pytest.mark.parametrize("value", [None, {}, "raw text", bytearray(b"{}"), 10])
def test_only_exact_bytes_are_accepted(value: Any) -> None:
    with patch.object(resources, "files", side_effect=AssertionError("must not read")):
        assert validate_proposed_record(AGGREGATE, value).reason == Refusal.INVALID_JSON_INPUT


@pytest.mark.parametrize(
    "value",
    [
        b'{"private-key":1,"private-key":2}',
        b'{"a":{"private-key":1,"private-key":2}}',
        b'{"a":NaN}',
        b'{"a":Infinity}',
        b'{"a":-Infinity}',
        b"\xff",
        b"{",
        b'{"a":"\\ud800"}',
        b'{"\\udfff":null}',
        b'{"a":["\\ud800"]}',
    ],
)
def test_malformed_json_duplicates_unicode_and_nonfinite_before_asset(value: bytes) -> None:
    with patch.object(resources, "files", side_effect=AssertionError("must not read")):
        result = validate_proposed_record(AGGREGATE, value)
    assert result.reason == Refusal.MALFORMED_JSON
    unadmitted(result)


@pytest.mark.parametrize(
    "raw_value",
    [
        b"0" * 1048577,
        b"[" * 33 + b"null" + b"]" * 33,
        b'{"a":1e1024}',
        b'{"a":1e-1025}',
        b'{"a":' + b"9" * 1025 + b"}",
    ],
)
def test_resource_limits_never_truncate_round_or_echo(raw_value: bytes) -> None:
    result = validate_proposed_record(AGGREGATE, raw_value)
    assert result.reason == Refusal.RESOURCE_LIMIT and result.schema_sha256 is None
    unadmitted(result)


@pytest.mark.parametrize("mid", IDS)
@pytest.mark.parametrize("scope", ["top", "metadata", "components"])
def test_private_clinical_extra_fields_rejected_every_metric_scope(mid: str, scope: str) -> None:
    value = null_aggregate(mid)
    target = value if scope == "top" else value[scope]
    target["UNIT_PRIVATE_KEY_MARKER"] = "UNIT_RAW_CLINICAL_MARKER"
    result = validate_proposed_record(AGGREGATE, raw(value))
    assert result.reason == Refusal.SCHEMA_MISMATCH
    assert "UNIT_PRIVATE" not in repr(result) and "UNIT_RAW" not in repr(result)
    unadmitted(result)


@pytest.mark.parametrize("state", ["PROPOSED_UNADMITTED", "SUPERSEDED", "RETRACTED", "WITHHELD"])
@pytest.mark.parametrize("channel", ["numerator", "denominator", "ratio", "components", "distribution"])
def test_invalidated_or_unadmitted_numeric_channels_stay_none(state: str, channel: str) -> None:
    value = null_aggregate()
    value["metadata"]["suppressed"] = False
    value["export_state"] = state
    if channel == "components":
        value[channel]["valid_current_n"] = 0
    elif channel == "distribution":
        value[channel] = {"p50_seconds": 0}
    else:
        value[channel] = 0
    assert validate_proposed_record(AGGREGATE, raw(value)).reason == Refusal.SCHEMA_MISMATCH


@pytest.mark.parametrize("value", [True, "10", 1.5, -1])
def test_count_types_no_bool_string_fraction_or_negative(value: Any) -> None:
    body = technical_m10()
    body["components"]["committed_current_n"] = value
    assert validate_proposed_record(AGGREGATE, raw(body)).reason == Refusal.SCHEMA_MISMATCH


def test_integral_json_decimal_preserves_canonical_integer_meaning_and_exact_large_sum() -> None:
    body = technical_m10()
    data = raw(body).replace(b'"committed_current_n":10', b'"committed_current_n":10.0')
    assert (
        validate_proposed_record(AGGREGATE, data).technical_status
        == ValidationTechnicalStatus.CHECKED_UNADMITTED
    )
    large = 10**70 + 3
    body["components"].update(
        committed_current_n=large,
        valid_current_n=large - 6,
        missing_current_n=3,
        receipt_uncertain_current_n=3,
        candidate_total_n=large + 16,
    )
    body.update(numerator=large - 6, denominator=large)
    result = validate_proposed_record(AGGREGATE, raw(body))
    assert check(result, ConstraintCode.M10_CURRENT_COMMIT_PARTITION) == ConstraintStatus.SATISFIED
    assert check(result, ConstraintCode.M10_CANDIDATE_PARTITION) == ConstraintStatus.SATISFIED
    unadmitted(result)


def test_tiny_nonintegral_numeric_token_is_not_binary_float_underflow_zero() -> None:
    body = technical_m10()
    data = raw(body).replace(b'"committed_current_n":10', b'"committed_current_n":1e-400')
    assert validate_proposed_record(AGGREGATE, data).reason == Refusal.SCHEMA_MISMATCH


def test_complete_m10_relations_are_local_only_and_ratio_rule_is_unassessed() -> None:
    result = validate_proposed_record(AGGREGATE, raw(technical_m10()))
    assert result.technical_status == ValidationTechnicalStatus.CHECKED_UNADMITTED
    for code in [
        ConstraintCode.M10_CURRENT_COMMIT_PARTITION,
        ConstraintCode.M10_WITHDRAWN_COMMIT_PARTITION,
        ConstraintCode.M10_CANDIDATE_PARTITION,
        ConstraintCode.M10_NUMERATOR_BINDING,
        ConstraintCode.M10_DENOMINATOR_BINDING,
    ]:
        assert check(result, code) == ConstraintStatus.SATISFIED
    assert check(result, ConstraintCode.M10_RATIO_REPRESENTATION) == ConstraintStatus.NOT_ASSESSABLE
    assert "26" not in repr(result) and "0.4" not in repr(result)
    unadmitted(result)


@pytest.mark.parametrize(
    "field,code",
    [
        ("candidate_total_n", ConstraintCode.M10_CANDIDATE_PARTITION),
        ("missing_current_n", ConstraintCode.M10_CURRENT_COMMIT_PARTITION),
        ("receipt_uncertain_current_n", ConstraintCode.M10_CURRENT_COMMIT_PARTITION),
        ("missing_withdrawn_n", ConstraintCode.M10_WITHDRAWN_COMMIT_PARTITION),
        ("numerator", ConstraintCode.M10_NUMERATOR_BINDING),
        ("denominator", ConstraintCode.M10_DENOMINATOR_BINDING),
    ],
)
def test_individual_m10_arithmetic_conflicts(field: str, code: ConstraintCode) -> None:
    body = technical_m10()
    target = body if field in {"numerator", "denominator"} else body["components"]
    target[field] += 1
    result = validate_proposed_record(AGGREGATE, raw(body))
    assert (
        result.reason == Refusal.LOCAL_CONSTRAINT_CONFLICT
        and check(result, code) == ConstraintStatus.CONFLICT
    )
    unadmitted(result)


@pytest.mark.parametrize("field", SCHEMAS[AGGREGATE]["$defs"]["M10_components"]["required"])
def test_each_m10_null_operand_is_not_reconstructed_from_totals(field: str) -> None:
    body = technical_m10()
    body["components"][field] = None
    before = copy.deepcopy(body)
    result = validate_proposed_record(AGGREGATE, raw(body))
    assert result.technical_status == ValidationTechnicalStatus.CHECKED_UNADMITTED
    if field not in {"immature_current_n", "right_censored_current_n"}:
        assert any(
            item.status == ConstraintStatus.NOT_ASSESSABLE
            and item.constraint != ConstraintCode.M10_RATIO_REPRESENTATION
            for item in result.checks
        )
    assert body == before and body["components"][field] is None
    assert not hasattr(result, "payload") and not hasattr(result, "values")
    unadmitted(result)


def test_null_numerator_and_denominator_have_no_backfill_and_zero_is_na() -> None:
    body = technical_m10()
    body.update(numerator=None, denominator=None, ratio=None)
    result = validate_proposed_record(AGGREGATE, raw(body))
    assert check(result, ConstraintCode.M10_NUMERATOR_BINDING) == ConstraintStatus.NOT_ASSESSABLE
    assert check(result, ConstraintCode.M10_DENOMINATOR_BINDING) == ConstraintStatus.NOT_ASSESSABLE
    body["components"].update(
        committed_current_n=0,
        valid_current_n=0,
        missing_current_n=0,
        receipt_uncertain_current_n=0,
        candidate_total_n=16,
    )
    body.update(numerator=0, denominator=0, ratio=None, measurement_status="N_A")
    zero = validate_proposed_record(AGGREGATE, raw(body))
    assert zero.technical_status == ValidationTechnicalStatus.CHECKED_UNADMITTED
    unadmitted(zero)
    body["ratio"] = 0
    assert validate_proposed_record(AGGREGATE, raw(body)).reason == Refusal.SCHEMA_MISMATCH


@pytest.mark.parametrize("case", ["equal", "different", "absent", "self_supersession", "opaque_nonorder"])
def test_head_declarations_never_authenticate_currentness(case: str) -> None:
    body = technical_m10()
    if case == "different":
        body["metadata"]["current_aggregate_head_revision"] = "other-head"
    elif case == "absent":
        body = null_aggregate()
    elif case == "self_supersession":
        body["metadata"]["supersedes_aggregate_revision"] = body["metadata"]["aggregate_revision"]
    elif case == "opaque_nonorder":
        body["metadata"].update(
            aggregate_revision="opaque/A-current",
            current_aggregate_head_revision="opaque/A-current",
            supersedes_aggregate_revision="opaque/Z-old",
            aggregate_source_order_revision="opaque/unordered",
        )
    result = validate_proposed_record(AGGREGATE, raw(body))
    if case in {"different", "self_supersession"}:
        assert result.reason == Refusal.LOCAL_CONSTRAINT_CONFLICT
    else:
        assert result.technical_status == ValidationTechnicalStatus.CHECKED_UNADMITTED
    if case == "absent":
        assert check(result, ConstraintCode.DECLARED_HEAD_EQUALITY) == ConstraintStatus.NOT_ASSESSABLE
    unadmitted(result)


def test_published_k_is_declared_floor_only_and_never_protected_person_proof() -> None:
    body = technical_m10()
    body["metadata"]["applied_minimum_k"] = 100
    result = validate_proposed_record(AGGREGATE, raw(body))
    assert check(result, ConstraintCode.DECLARED_K_FLOOR) == ConstraintStatus.SATISFIED
    unadmitted(result)
    body["metadata"]["applied_minimum_k"] = 9
    assert validate_proposed_record(AGGREGATE, raw(body)).reason == Refusal.SCHEMA_MISMATCH


def test_immutable_results_no_authority_constructor_or_input_echo(
    caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str]
) -> None:
    body = technical_m10()
    body["PRIVATE_UNKNOWN_KEY_MARKER"] = "PRIVATE_PAYLOAD_MARKER"
    result = validate_proposed_record(AGGREGATE, raw(body))
    assert "PRIVATE_" not in repr(result) and "PRIVATE_" not in caplog.text
    assert capsys.readouterr().out == ""
    with pytest.raises(FrozenInstanceError):
        result.numeric_disclosure_permitted = True  # type: ignore[misc,assignment]
    with pytest.raises(ValueError):
        replace(result, numeric_disclosure_permitted=cast(Any, True))
    checked = validate_proposed_record(AGGREGATE, raw(technical_m10()))
    with pytest.raises(FrozenInstanceError):
        checked.checks[0].status = ConstraintStatus.CONFLICT  # type: ignore[misc]
    with pytest.raises(ValueError, match="aggregate_constraint_contract_mismatch"):
        ConstraintCheck(cast(Any, "PRIVATE_CODE_MARKER"), ConstraintStatus.SATISFIED)
    unadmitted(result)


@pytest.mark.parametrize("failure", ["missing", "mutation", "exception"])
def test_asset_missing_drift_exception_no_docs_env_or_cache_fallback(
    failure: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    validate_proposed_record(AGGREGATE, raw(null_aggregate()))  # prime only verified schema cache
    monkeypatch.setenv("MAEZO_MEASUREMENT_SCHEMA_PATH", str(CANONICAL))

    class BrokenResource:
        def joinpath(self, name: str) -> BrokenResource:
            assert name == "measurement-export-closed-shapes.proposed-v2.json"
            return self

        def read_bytes(self) -> bytes:
            if failure == "mutation":
                return b'{"weakened_schema_private_marker":true}'
            if failure == "exception":
                raise ValueError("PRIVATE_BACKEND_ERROR_MARKER")
            raise FileNotFoundError("PRIVATE_MISSING_PATH_MARKER")

    with patch.object(resources, "files", return_value=BrokenResource()):
        result = validate_proposed_record(AGGREGATE, raw(null_aggregate()))
    assert result.reason == (
        Refusal.SCHEMA_DIGEST_MISMATCH if failure == "mutation" else Refusal.SCHEMA_ASSET_UNAVAILABLE
    )
    assert result.schema_sha256 is None and "PRIVATE_" not in repr(result)
    unadmitted(result)


def test_asset_exact_canonical_bytes_all_four_selectors_and_no_external_ref() -> None:
    assert ASSET.read_bytes() == CANONICAL.read_bytes()
    assert hashlib.sha256(ASSET.read_bytes()).hexdigest() == PIN
    assert set(module._KINDS) == {
        "provider_private_computation_row",
        "metric_definition_pending_admission",
        "metric_component_mapping_pending_admission",
        "minimized_aggregate_export",
    }
    with pytest.raises(module._RefusalError):
        module._references({"$ref": "https://must-never-fetch.invalid/private-marker"})


def test_no_release_source_callback_schema_override_or_payload_output_api() -> None:
    import inspect

    assert list(inspect.signature(validate_proposed_record).parameters) == ["record_kind", "raw_json"]
    assert not inspect.iscoroutinefunction(validate_proposed_record)
    assert not hasattr(module, "release_numeric") and not hasattr(module, "fetch_source")
    result = validate_proposed_record(AGGREGATE, raw(technical_m10()))
    assert {name for name in asdict(result) if name.endswith("assessed") or name.endswith("permitted")} == {
        "source_authority_assessed",
        "source_currentness_assessed",
        "privacy_release_assessed",
        "numeric_disclosure_permitted",
    }


def test_actual_built_wheel_consumes_asset_away_from_checkout_docs(tmp_path: Path) -> None:
    """Actual isolated build/import of this module/asset, not a whole-app gate."""
    out, cache = tmp_path / "wheels", tmp_path / "uv-cache"
    out.mkdir()
    env = {**os.environ, "UV_CACHE_DIR": str(cache)}
    build = subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(out)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    (tmp_path / "wheel-build.stdout").write_text(build.stdout)
    (tmp_path / "wheel-build.stderr").write_text(build.stderr)
    assert build.returncode == 0, "actual isolated wheel build failed; inspect private pytest build log"
    wheels = list(out.glob("*.whl"))
    assert len(wheels) == 1
    wheel = wheels[0]
    resource_name = "maezo/gateway/measurement-export-closed-shapes.proposed-v2.json"
    with ZipFile(wheel) as archive:
        assert archive.read(resource_name) == CANONICAL.read_bytes()
        assert "maezo/gateway/aggregate_export_validation.py" in archive.namelist()
    away = tmp_path / "away-without-docs"
    away.mkdir()
    script = """
import hashlib, importlib.resources, json, sys
sys.path.insert(0, sys.argv[1])
from maezo.gateway.aggregate_export_validation import validate_proposed_record
import maezo.gateway.aggregate_export_validation as module
assert str(module.__file__).startswith(sys.argv[1])
resource_name = 'measurement-export-closed-shapes.proposed-v2.json'
asset = importlib.resources.files('maezo.gateway').joinpath(resource_name)
data = asset.read_bytes()
assert hashlib.sha256(data).hexdigest() == sys.argv[2]
result = validate_proposed_record('metric_definition_pending_admission', sys.argv[3].encode())
assert result.technical_status.value == 'CHECKED_UNADMITTED'
assert result.numeric_disclosure_permitted is False
print(json.dumps({'module_from_wheel':True,'asset_digest':hashlib.sha256(data).hexdigest(),'numeric_disclosure_permitted':False}))
"""
    smoke = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            script,
            str(wheel),
            PIN,
            json.dumps(METRICS[0]["definition_admission_proposed"]),
        ],
        cwd=away,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    (tmp_path / "wheel-smoke.stdout").write_text(smoke.stdout)
    (tmp_path / "wheel-smoke.stderr").write_text(smoke.stderr)
    assert smoke.returncode == 0, (
        "isolated wheel module/asset validation failed; inspect private pytest smoke log"
    )
    proof = json.loads(smoke.stdout)
    assert proof["module_from_wheel"] is True and proof["asset_digest"] == PIN


@pytest.mark.parametrize(
    "token",
    [
        "1e100000000000000000000",
        "1e-100000000000000000000",
        "-1e+100000000000000000000",
        "0e-100000000000000000000",
        "1e" + "9" * 5000,
        "1e-" + "9" * 5000,
        "1e1024",
        "1e-1025",
        "1.0e1024",
        "0." + "0" * 1024 + "1",
        "9" * 1025,
        "1." + "0" * 1024 + "e1024",
    ],
)
def test_numeric_resource_budget_precedes_decimal_and_schema(token: str) -> None:
    # Finite JSON is refused for resource cost, not the backend exponent range.
    with (
        patch.object(module, "Decimal", side_effect=AssertionError("must not construct")),
        patch.object(resources, "files", side_effect=AssertionError("must not read")),
    ):
        result = validate_proposed_record(AGGREGATE, ('{"a":' + token + "}").encode())
    assert result.reason == Refusal.RESOURCE_LIMIT and result.schema_sha256 is None
    unadmitted(result)


@pytest.mark.parametrize(
    ("token", "expected"),
    [
        ("1e1023", "1e1023"),
        ("1e-1024", "1e-1024"),
        ("-1e-1024", "-1e-1024"),
        ("9" * 1024, "9" * 1024),
        ("1." + "0" * 1023 + "e1023", "1e1023"),
        ("0." + "0" * 10000 + "1e10001", "1"),
        ("-0." + "0" * 10000 + "1e+10001", "-1"),
        ("1e+" + "0" * 5000 + "1023", "1e1023"),
        ("1e-" + "0" * 5000 + "1024", "1e-1024"),
        ("0." + "0" * 10000 + "e10000", "0"),
    ],
)
def test_numeric_resource_boundaries_and_exponent_cancellation_preserve_exact_value(
    token: str, expected: str
) -> None:
    from decimal import Decimal

    # Accepted parser numerals retain exact values. Unknown schema field remains
    # refused independently; neither parser acceptance nor a number grants release.
    decoded = module._decode(('{"a":' + token + "}").encode())
    assert decoded["a"] == Decimal(expected)
    assert type(decoded["a"]) in (int, Decimal)
    result = validate_proposed_record(AGGREGATE, ('{"a":' + token + "}").encode())
    assert result.reason == Refusal.SCHEMA_MISMATCH and result.schema_sha256 == PIN
    unadmitted(result)
