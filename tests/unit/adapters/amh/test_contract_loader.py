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
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from maezo.adapters.amh.contract import (
    CONTRACT_PIN_RELATIVE_PATH,
    FROZEN_ENVELOPE_FIELD_ORDER,
    MAEZO_AMH_CONTRACT_PIN_ENV,
    AmhAdapterError,
    AmhContractPinError,
    load_contract_pin,
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
