"""R009 build validation, runtime canonical mode, and reference-only prerequisite proofs."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO_ROOT / "scripts" / "ci"))

import check_phi_scrub_prereqs as gate  # noqa: E402 - path shim above, mirrors sibling CI tests

# `maezo.platform.privacy.__init__` re-exports a FUNCTION also named `phi_key_policy` (the cached
# accessor), which shadows the submodule of the same name in the package's own namespace once the
# package is imported — `import maezo.platform.privacy.phi_key_policy as phi_key_policy` is NOT
# safe here for that reason. `importlib.import_module` always returns the real submodule straight
# out of `sys.modules`, bypassing the shadowed attribute.
phi_key_policy = importlib.import_module("maezo.platform.privacy.phi_key_policy")  # noqa: E402

_MANIFEST = _REPO_ROOT / "spec" / "policies" / "privacy" / "phi-business-key-remediation.yaml"

_RATIFICACAO_COMPLETA: dict[str, Any] = {
    "ratificado": True,
    "revisor": "test-suite",
    "ratificado_em": "2026-09-06",
}

_MET_HMAC_ITEM: dict[str, Any] = {
    "id": "phi_hmac_key_provisionado",
    "atendido": True,
    "evidencia_provisionamento": "evidence://synthetic-fixture/deployment-check-20260906",
}
_MET_JANELA_ITEM: dict[str, Any] = {
    "id": "janela_drenagem_cancel_inad",
    "atendido": True,
    "janela_drenagem": {"inicio": "2026-10-01T02:00:00-03:00", "fim": "2026-10-01T04:00:00-03:00"},
}
_UNMET_HMAC_ITEM: dict[str, Any] = {
    "id": "phi_hmac_key_provisionado",
    "atendido": False,
    "evidencia_provisionamento": "",
}
_UNMET_JANELA_ITEM: dict[str, Any] = {
    "id": "janela_drenagem_cancel_inad",
    "atendido": False,
    "janela_drenagem": {"inicio": None, "fim": None},
}


@pytest.fixture(autouse=True)
def _clear_manifest_cache() -> Any:
    """`phi_key_policy` exposes an lru_cache; `run_gate` calls the uncached loader directly, but
    clearing here matches the sibling gate tests' defensive posture and protects any future
    caller that switches to the cached accessor."""
    phi_key_policy.reset_phi_key_policy_cache()
    yield
    phi_key_policy.reset_phi_key_policy_cache()


def _write(tmp_path: Path, manifest: dict[str, Any], name: str = "phi-business-key-remediation.yaml") -> Path:
    path = tmp_path / name
    path.write_text(yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


def _ratified(modo: str, block: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    manifest: dict[str, Any] = {
        "status": "RATIFICADO",
        "modo": modo,
        "ratificacao": _RATIFICACAO_COMPLETA,
    }
    if block is not None:
        manifest[gate._BLOCK_KEY] = block
    return manifest


# ---------------------------------------------------------------------------------------------
# (a) GREEN TODAY against the real shipped manifest
# ---------------------------------------------------------------------------------------------


def test_the_gate_is_green_against_the_shipped_manifest_today() -> None:
    assert gate.main(["--manifest", str(_MANIFEST)]) == 0


def test_the_shipped_manifest_is_draft_with_the_new_block_present_but_unmet() -> None:
    """Pin of the current, honest state: DRAFT, and both new prerequisites still `atendido: false`.

    Independent of the gate itself — a direct read of the shipped YAML — so a future PR that
    silently flips one to `true` without evidence is caught here even before the gate's own
    ratification check would (correctly) still pass it as DRAFT.
    """
    data = yaml.safe_load(_MANIFEST.read_text(encoding="utf-8"))
    assert data["status"] == "DRAFT"
    block = {item["id"]: item for item in data[gate._BLOCK_KEY]}
    assert set(block) == set(gate.TRACKED_PREREQUISITE_IDS)
    assert block["phi_hmac_key_provisionado"]["atendido"] is False
    assert block["janela_drenagem_cancel_inad"]["atendido"] is False


# ---------------------------------------------------------------------------------------------
# (b) GREEN while DRAFT, whatever `modo`/the block declare
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "block",
    [
        pytest.param(None, id="block-absent"),
        pytest.param([_UNMET_HMAC_ITEM, _UNMET_JANELA_ITEM], id="block-present-both-unmet"),
    ],
)
def test_draft_passes_only_with_well_formed_block(tmp_path: Path, block: list[dict[str, Any]] | None) -> None:
    manifest: dict[str, Any] = {"status": "DRAFT", "modo": "scrub_only"}
    if block is not None:
        manifest[gate._BLOCK_KEY] = block
    path = _write(tmp_path, manifest)
    assert gate.main(["--manifest", str(path)]) == 0


# ---------------------------------------------------------------------------------------------
# (f) GREEN when ratified with modo: off
# ---------------------------------------------------------------------------------------------


def test_ratified_off_passes_with_no_block_at_all(tmp_path: Path) -> None:
    path = _write(tmp_path, _ratified("off"))
    assert gate.main(["--manifest", str(path)]) == 0


# ---------------------------------------------------------------------------------------------
# (d) GREEN when ratified scrub_only/pseudo_keys with both prerequisites met
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("modo", ["scrub_only", "pseudo_keys"])
def test_ratified_with_both_prerequisites_met_passes(tmp_path: Path, modo: str) -> None:
    path = _write(tmp_path, _ratified(modo, [_MET_HMAC_ITEM, _MET_JANELA_ITEM]))
    assert gate.main(["--manifest", str(path)]) == 0


# ---------------------------------------------------------------------------------------------
# (c) RED when ratified and a tracked prerequisite is unmet — scrub_only AND pseudo_keys
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("modo", ["scrub_only", "pseudo_keys"])
def test_ratified_with_hmac_key_unmet_fails(tmp_path: Path, modo: str) -> None:
    path = _write(tmp_path, _ratified(modo, [_UNMET_HMAC_ITEM, _MET_JANELA_ITEM]))
    assert gate.main(["--manifest", str(path)]) == 1


@pytest.mark.parametrize("modo", ["scrub_only", "pseudo_keys"])
def test_ratified_with_drain_window_unmet_fails(tmp_path: Path, modo: str) -> None:
    path = _write(tmp_path, _ratified(modo, [_MET_HMAC_ITEM, _UNMET_JANELA_ITEM]))
    assert gate.main(["--manifest", str(path)]) == 1


def test_ratified_atendido_true_without_evidence_string_fails(tmp_path: Path) -> None:
    """`atendido: true` alone is not enough — the evidence field must be non-blank."""
    item = {"id": "phi_hmac_key_provisionado", "atendido": True, "evidencia_provisionamento": "   "}
    path = _write(tmp_path, _ratified("scrub_only", [item, _MET_JANELA_ITEM]))
    assert gate.main(["--manifest", str(path)]) == 1


def test_ratified_atendido_true_without_evidence_field_fails(tmp_path: Path) -> None:
    item = {"id": "phi_hmac_key_provisionado", "atendido": True}
    path = _write(tmp_path, _ratified("scrub_only", [item, _MET_JANELA_ITEM]))
    assert gate.main(["--manifest", str(path)]) == 1


def test_ratified_atendido_true_without_drain_dates_fails(tmp_path: Path) -> None:
    item = {
        "id": "janela_drenagem_cancel_inad",
        "atendido": True,
        "janela_drenagem": {"inicio": "2026-10-01", "fim": ""},
    }
    path = _write(tmp_path, _ratified("scrub_only", [_MET_HMAC_ITEM, item]))
    assert gate.main(["--manifest", str(path)]) == 1


# ---------------------------------------------------------------------------------------------
# (e) RED on malformed block shapes
# ---------------------------------------------------------------------------------------------


def test_ratified_scrub_only_with_absent_block_fails(tmp_path: Path) -> None:
    path = _write(tmp_path, _ratified("scrub_only"))
    assert gate.main(["--manifest", str(path)]) == 1


def test_ratified_scrub_only_with_non_list_block_fails(tmp_path: Path) -> None:
    manifest = _ratified("scrub_only")
    manifest[gate._BLOCK_KEY] = "not-a-list"
    path = _write(tmp_path, manifest)
    assert gate.main(["--manifest", str(path)]) == 1


def test_ratified_scrub_only_missing_one_tracked_id_fails(tmp_path: Path) -> None:
    path = _write(tmp_path, _ratified("scrub_only", [_MET_HMAC_ITEM]))
    assert gate.main(["--manifest", str(path)]) == 1


def test_ratified_scrub_only_unknown_extra_id_fails(tmp_path: Path) -> None:
    extra = {"id": "algum_id_novo_nao_rastreado", "atendido": True}
    path = _write(tmp_path, _ratified("scrub_only", [_MET_HMAC_ITEM, _MET_JANELA_ITEM, extra]))
    assert gate.main(["--manifest", str(path)]) == 1


def test_ratified_scrub_only_non_bool_atendido_fails(tmp_path: Path) -> None:
    item = {"id": "phi_hmac_key_provisionado", "atendido": "true", "evidencia_provisionamento": "x"}
    path = _write(tmp_path, _ratified("scrub_only", [item, _MET_JANELA_ITEM]))
    assert gate.main(["--manifest", str(path)]) == 1


def test_ratified_scrub_only_missing_atendido_key_fails(tmp_path: Path) -> None:
    item = {"id": "phi_hmac_key_provisionado", "evidencia_provisionamento": "x"}
    path = _write(tmp_path, _ratified("scrub_only", [item, _MET_JANELA_ITEM]))
    assert gate.main(["--manifest", str(path)]) == 1


def test_ratified_scrub_only_item_not_a_mapping_fails(tmp_path: Path) -> None:
    path = _write(tmp_path, _ratified("scrub_only", ["phi_hmac_key_provisionado", _MET_JANELA_ITEM]))
    assert gate.main(["--manifest", str(path)]) == 1


def test_ratified_scrub_only_janela_drenagem_not_a_mapping_fails(tmp_path: Path) -> None:
    item = {"id": "janela_drenagem_cancel_inad", "atendido": True, "janela_drenagem": "2026-10-01"}
    path = _write(tmp_path, _ratified("scrub_only", [_MET_HMAC_ITEM, item]))
    assert gate.main(["--manifest", str(path)]) == 1


def test_duplicate_id_in_block_fails(tmp_path: Path) -> None:
    path = _write(tmp_path, _ratified("scrub_only", [_MET_HMAC_ITEM, _MET_HMAC_ITEM, _MET_JANELA_ITEM]))
    assert gate.main(["--manifest", str(path)]) == 1


# ---------------------------------------------------------------------------------------------
# (g) Missing or malformed manifests fail build validation, even though runtime stays OFF.


def test_missing_manifest_file_fails_build(tmp_path: Path) -> None:
    findings = gate.run_gate(tmp_path / "does-not-exist.yaml")
    assert all(f.level == gate.LEVEL_FAIL for f in findings)
    assert gate.main(["--manifest", str(tmp_path / "does-not-exist.yaml")]) == 1


def test_malformed_yaml_fails_build(tmp_path: Path) -> None:
    path = tmp_path / "phi-business-key-remediation.yaml"
    path.write_text("status: RATIFICADO\nmodo: [unterminated\n", encoding="utf-8")
    findings = gate.run_gate(path)
    assert all(f.level == gate.LEVEL_FAIL for f in findings)
    assert gate.main(["--manifest", str(path)]) == 1


# ---------------------------------------------------------------------------------------------
# Non-vacuity of the gate itself
# ---------------------------------------------------------------------------------------------


def test_self_check_reports_no_problems() -> None:
    assert gate.self_check() == []


def test_self_check_scenarios_declare_at_least_one_ok_and_one_fail() -> None:
    levels = {level for _, level in gate.SELF_CHECK_SCENARIOS}
    assert gate.LEVEL_OK in levels
    assert gate.LEVEL_FAIL in levels


def test_self_check_catches_a_comparator_that_never_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """The self-check itself must be able to REPORT a broken comparator, not just run clean."""

    def _always_ok(data: dict[str, Any]) -> list[gate.Finding]:
        return [gate.Finding(level=gate.LEVEL_OK, prereq_id="x", headline="sempre ok")]

    monkeypatch.setattr(gate, "evaluate", _always_ok)
    problems = gate.self_check()
    assert problems, "a comparator that always returns OK must be caught by its own scenario set"


# ---------------------------------------------------------------------------------------------
# evaluate() unit-level coverage (pure function, no I/O)
# ---------------------------------------------------------------------------------------------


def test_evaluate_off_mode_short_circuits_without_reading_block() -> None:
    findings = gate.evaluate({"modo": "off", gate._BLOCK_KEY: "this would fail if read"})
    assert [f.level for f in findings] == [gate.LEVEL_OK]


def test_evaluate_reports_every_unmet_prerequisite_not_just_the_first() -> None:
    data = {"modo": "scrub_only", gate._BLOCK_KEY: [_UNMET_HMAC_ITEM, _UNMET_JANELA_ITEM]}
    findings = gate.evaluate(data)
    fail_ids = {f.prereq_id for f in findings if f.level == gate.LEVEL_FAIL}
    assert fail_ids == set(gate.TRACKED_PREREQUISITE_IDS)


# ---------------------------------------------------------------------------------------------
# ISOLATED evaluate()-level mutation targets.
#
# Every fixture below is deliberately built so that ONLY the one check named in the test's name
# can produce its verdict — every OTHER prerequisite/branch in the same call is fully, separately
# satisfied. This matters because `gate.main()` runs its own `self_check()` first (over ITS OWN
# synthetic fixtures) and `evaluate()`'s checks can otherwise sit right next to a sibling check
# that independently reproduces the same FAIL on an overlapping fixture (e.g. `_UNMET_HMAC_ITEM`
# is BOTH `atendido: false` AND has blank evidence, so disabling the `atendido` check alone still
# leaves the evidence check catching it). A test built on such a fixture would still show green
# after `main()`-level assertions even with the specific check gutted — exactly the "unprovable
# check" shape BRIEF-COMMON's mutation-proof requirement exists to catch. These call `evaluate()`
# directly (bypassing `main()`/`self_check()`/the ratification gate entirely) with a MINIMAL
# fixture per check, so each one is provably killed by exactly one mutation. See the task report's
# mutation-proof section for the paired RED/GREEN runs.
# ---------------------------------------------------------------------------------------------

_HMAC_UNMET_BUT_EVIDENCE_PRESENT: dict[str, Any] = {
    "id": "phi_hmac_key_provisionado",
    "atendido": False,
    "evidencia_provisionamento": "não deveria importar — atendido é false",
}
_JANELA_UNMET_BUT_DATES_PRESENT: dict[str, Any] = {
    "id": "janela_drenagem_cancel_inad",
    "atendido": False,
    "janela_drenagem": {"inicio": "2026-10-01T02:00:00-03:00", "fim": "2026-10-01T04:00:00-03:00"},
}


def test_evaluate_malformed_non_dict_entry_is_reported_as_its_own_fail() -> None:
    """A non-mapping entry must be a NAMED "(bloco)" fail, never silently dropped."""
    data = {"modo": "scrub_only", gate._BLOCK_KEY: ["not-a-dict", _MET_HMAC_ITEM, _MET_JANELA_ITEM]}
    findings = gate.evaluate(data)
    block_fails = [f for f in findings if f.prereq_id == "(bloco)" and f.level == gate.LEVEL_FAIL]
    assert block_fails, "a non-dict entry must produce its own (bloco) FAIL finding"
    by_id = {f.prereq_id: f for f in findings if f.prereq_id != "(bloco)"}
    assert by_id["phi_hmac_key_provisionado"].level == gate.LEVEL_OK
    assert by_id["janela_drenagem_cancel_inad"].level == gate.LEVEL_OK


def test_evaluate_entry_without_string_id_is_reported_as_malformed() -> None:
    """Asserts the SPECIFIC 'entrada malformada' wording, not just "some FAIL exists": an id-less
    entry that slipped past this check would still surface as an "unknown id" FAIL keyed on
    `None` (the adjacent D-check), which is a real but far more confusing message — this pins the
    clearer, dedicated one so a mutation that guts this check (while leaving D intact) still goes
    red here instead of hiding behind D's redundant catch."""
    data = {
        "modo": "scrub_only",
        gate._BLOCK_KEY: [{"atendido": True}, _MET_HMAC_ITEM, _MET_JANELA_ITEM],
    }
    findings = gate.evaluate(data)
    malformed = [f for f in findings if f.prereq_id == "(bloco)" and "entrada malformada" in f.headline]
    assert malformed, (
        f"expected a dedicated 'entrada malformada' finding, got: {[f.render() for f in findings]}"
    )


def test_evaluate_atendido_non_bool_is_reported_as_fail() -> None:
    item = {"id": "phi_hmac_key_provisionado", "atendido": "true", "evidencia_provisionamento": "x"}
    findings = gate.evaluate({"modo": "scrub_only", gate._BLOCK_KEY: [item, _MET_JANELA_ITEM]})
    by_id = {f.prereq_id: f for f in findings}
    assert by_id["phi_hmac_key_provisionado"].level == gate.LEVEL_FAIL
    assert by_id["janela_drenagem_cancel_inad"].level == gate.LEVEL_OK


def test_evaluate_hmac_unmet_is_reported_as_fail_even_with_evidence_text_present() -> None:
    """Isolates the `atendido` check from the evidence check: evidence is non-blank here, so a
    disabled `atendido` check alone would flip this to OK — proving the two are independent."""
    data = {"modo": "scrub_only", gate._BLOCK_KEY: [_HMAC_UNMET_BUT_EVIDENCE_PRESENT, _MET_JANELA_ITEM]}
    findings = gate.evaluate(data)
    by_id = {f.prereq_id: f for f in findings}
    assert by_id["phi_hmac_key_provisionado"].level == gate.LEVEL_FAIL
    assert by_id["janela_drenagem_cancel_inad"].level == gate.LEVEL_OK


def test_evaluate_hmac_evidence_blank_is_reported_as_fail_when_atendido_true() -> None:
    item = {"id": "phi_hmac_key_provisionado", "atendido": True, "evidencia_provisionamento": "   "}
    findings = gate.evaluate({"modo": "scrub_only", gate._BLOCK_KEY: [item, _MET_JANELA_ITEM]})
    by_id = {f.prereq_id: f for f in findings}
    assert by_id["phi_hmac_key_provisionado"].level == gate.LEVEL_FAIL
    assert by_id["janela_drenagem_cancel_inad"].level == gate.LEVEL_OK


def test_evaluate_janela_not_a_mapping_is_reported_as_fail() -> None:
    item = {"id": "janela_drenagem_cancel_inad", "atendido": True, "janela_drenagem": "not-a-dict"}
    findings = gate.evaluate({"modo": "scrub_only", gate._BLOCK_KEY: [_MET_HMAC_ITEM, item]})
    by_id = {f.prereq_id: f for f in findings}
    assert by_id["janela_drenagem_cancel_inad"].level == gate.LEVEL_FAIL
    assert by_id["phi_hmac_key_provisionado"].level == gate.LEVEL_OK


def test_evaluate_janela_unmet_is_reported_as_fail_even_with_dates_present() -> None:
    """Isolates the janela `atendido` check from the dates-blank check the same way the HMAC
    pair above does."""
    data = {"modo": "scrub_only", gate._BLOCK_KEY: [_MET_HMAC_ITEM, _JANELA_UNMET_BUT_DATES_PRESENT]}
    findings = gate.evaluate(data)
    by_id = {f.prereq_id: f for f in findings}
    assert by_id["janela_drenagem_cancel_inad"].level == gate.LEVEL_FAIL
    assert by_id["phi_hmac_key_provisionado"].level == gate.LEVEL_OK


def test_evaluate_janela_dates_blank_is_reported_as_fail_when_atendido_true() -> None:
    item = {
        "id": "janela_drenagem_cancel_inad",
        "atendido": True,
        "janela_drenagem": {"inicio": "2026-10-01", "fim": ""},
    }
    findings = gate.evaluate({"modo": "scrub_only", gate._BLOCK_KEY: [_MET_HMAC_ITEM, item]})
    by_id = {f.prereq_id: f for f in findings}
    assert by_id["janela_drenagem_cancel_inad"].level == gate.LEVEL_FAIL
    assert by_id["phi_hmac_key_provisionado"].level == gate.LEVEL_OK


def test_evaluate_block_absent_is_reported_as_fail() -> None:
    findings = gate.evaluate({"modo": "scrub_only"})
    assert findings and all(f.level == gate.LEVEL_FAIL for f in findings)


def test_evaluate_block_not_a_list_is_reported_as_fail() -> None:
    """Asserts the SPECIFIC 'não é uma lista' wording: a string block, if the type-check were
    gutted, would still be iterated character-by-character and every character caught by the
    adjacent malformed-entry check — still FAIL, but for the wrong, confusing reason. Pinning the
    dedicated message keeps this test sensitive to exactly this check."""
    findings = gate.evaluate({"modo": "scrub_only", gate._BLOCK_KEY: "not-a-list"})
    assert any("não é uma lista" in f.headline for f in findings)


def test_evaluate_missing_tracked_id_is_reported_as_fail_without_crashing() -> None:
    findings = gate.evaluate({"modo": "scrub_only", gate._BLOCK_KEY: [_MET_HMAC_ITEM]})
    by_id = {f.prereq_id: f for f in findings}
    assert by_id["janela_drenagem_cancel_inad"].level == gate.LEVEL_FAIL
    assert by_id["phi_hmac_key_provisionado"].level == gate.LEVEL_OK


def test_evaluate_duplicate_id_is_reported_as_fail_even_when_both_copies_are_met() -> None:
    """Isolates duplicate-id detection: BOTH copies are otherwise fully met, so only the
    duplicate-id check itself can be the source of the FAIL."""
    data = {"modo": "scrub_only", gate._BLOCK_KEY: [_MET_HMAC_ITEM, _MET_HMAC_ITEM, _MET_JANELA_ITEM]}
    findings = gate.evaluate(data)
    assert any(f.prereq_id == "(bloco)" and f.level == gate.LEVEL_FAIL for f in findings)


def test_evaluate_unknown_extra_id_is_reported_as_fail_even_when_tracked_ids_are_met() -> None:
    extra = {"id": "algum_id_novo_nao_rastreado", "atendido": True}
    data = {"modo": "scrub_only", gate._BLOCK_KEY: [_MET_HMAC_ITEM, _MET_JANELA_ITEM, extra]}
    findings = gate.evaluate(data)
    assert any(f.prereq_id == "(bloco)" and f.level == gate.LEVEL_FAIL for f in findings)
