"""R009-EIR01--06: actual subprocess CLI and uncached state/recovery contract."""

from __future__ import annotations

import copy
import importlib
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/ci"))
import check_phi_scrub_prereqs as gate  # noqa: E402

REFERENCE = "evidence://synthetic-fixture/deployment-check-20260906"


def manifest(mode: str = "scrub_only") -> dict[str, Any]:
    return {
        "version": 1,
        "status": "RATIFICADO",
        "modo": mode,
        "ratificacao": {"ratificado": True, "revisor": "SYNTHETIC-REVIEWER", "ratificado_em": "2026-09-06"},
        gate._BLOCK_KEY: [
            {"id": "phi_hmac_key_provisionado", "atendido": True, "evidencia_provisionamento": REFERENCE},
            {
                "id": "janela_drenagem_cancel_inad",
                "atendido": True,
                "janela_drenagem": {
                    "inicio": "2026-10-01T02:00:00-03:00",
                    "fim": "2026-10-01T04:00:00-03:00",
                },
            },
        ],
    }


def write(tmp_path: Path, data: dict[str, Any]) -> Path:
    path = tmp_path / "manifest.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def cli(path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts/ci/check_phi_scrub_prereqs.py"), "--manifest", str(path)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": str(ROOT / "src")},
        timeout=30,
    )


@pytest.mark.parametrize(
    "mode", ["scrub_only", "pseudo_keys", "SCRUB_ONLY", " scrub_only ", "PSEUDO_KEYS", " pseudo_keys "]
)
@pytest.mark.parametrize("complete", [False, True])
def test_effective_mode_cli(tmp_path: Path, mode: str, complete: bool) -> None:
    data = manifest(mode)
    if not complete:
        del data[gate._BLOCK_KEY]
    path = write(tmp_path, data)
    policy = importlib.import_module("maezo.platform.privacy.phi_key_policy").load_phi_key_policy(path)
    assert policy.scrubbing_enabled and policy.modo.value == mode.strip().lower()
    assert cli(path).returncode == (0 if complete else 1)


@pytest.mark.parametrize(
    "kind",
    [
        "absent",
        "directory",
        "unreadable",
        "encoding",
        "empty",
        "sequence",
        "scalar",
        "yaml",
        "duplicate_root",
        "duplicate_ratification",
        "duplicate_item",
        "duplicate_window",
        "nonstring_key",
        "recursive_alias",
        "merge_override",
    ],
)
def test_invalid_ingestion_actual_cli(tmp_path: Path, kind: str) -> None:
    path = write(tmp_path, manifest())
    good = path.read_text()
    if kind == "absent":
        path.unlink()
    elif kind == "directory":
        path.unlink()
        path.mkdir()
    elif kind == "unreadable":
        path.chmod(0)
    elif kind == "encoding":
        path.write_bytes(b"\xff")
    else:
        raw = {
            "empty": "",
            "sequence": "[]",
            "scalar": "true",
            "yaml": "status: [",
            "duplicate_root": good + "\nmodo: off\n",
            "duplicate_ratification": good.replace(
                "  ratificado: true", "  ratificado: false\n  ratificado: true"
            ),
            "duplicate_item": good.replace("- atendido: true", "- atendido: false\n  atendido: true", 1),
            "duplicate_window": good.replace("    inicio:", "    inicio: null\n    inicio:"),
            "nonstring_key": good + "\n[1, 2]: ignored\n",
            "recursive_alias": (
                "status: DRAFT\nmodo: off\n"
                "ratificacao: &a {ratificado: false, revisor: *a, ratificado_em: null}\n"
            ),
            "merge_override": good + "\nextra: {<<: &defaults {atendido: false}, atendido: true}\n",
        }[kind]
        path.write_text(raw)
    try:
        result = cli(path)
        assert result.returncode == 1, result.stdout + result.stderr
        assert "Traceback" not in result.stderr
    finally:
        if kind == "unreadable":
            path.chmod(0o600)


@pytest.mark.parametrize(
    "field,value",
    [
        ("status", None),
        ("status", "PENDING"),
        ("modo", "unknown"),
        ("modo", 1),
        ("version", True),
        ("version", 2),
        ("unratified", "false"),
        ("unratified", True),
        ("ratificacao", None),
        ("ratificacao", {}),
        ("ratificacao.ratificado", False),
        ("ratificacao.ratificado", 1),
        ("ratificacao.revisor", None),
        ("ratificacao.revisor", " "),
        ("ratificacao.ratificado_em", None),
        ("ratificacao.ratificado_em", 20260906),
    ],
)
def test_partial_promotion_cli(tmp_path: Path, field: str, value: Any) -> None:
    data = manifest()
    if "." in field:
        parent, child = field.split(".")
        data[parent][child] = value
    else:
        data[field] = value
    assert cli(write(tmp_path, data)).returncode == 1


@pytest.mark.parametrize("mode", ["off", False, " OFF ", "scrub_only", "pseudo_keys"])
@pytest.mark.parametrize("template", [None, False, True])
def test_valid_inert_draft_matrix(tmp_path: Path, mode: Any, template: bool | None) -> None:
    data = {
        "status": "DRAFT",
        "modo": mode,
        "ratificacao": {"ratificado": False, "revisor": None, "ratificado_em": None},
    }
    if template is not None:
        data["unratified"] = template
    path = write(tmp_path, data)
    assert gate.exit_code_for(gate.run_gate(path)) == 0
    assert (
        not importlib.import_module("maezo.platform.privacy.phi_key_policy")
        .load_phi_key_policy(path)
        .scrubbing_enabled
    )


@pytest.mark.parametrize(
    "change",
    [
        "flag_true",
        "flag_missing",
        "reviewer",
        "date",
        "null_block",
        "bad_block",
        "bad_window",
        "bad_evidence",
    ],
)
def test_malformed_draft_rejected(tmp_path: Path, change: str) -> None:
    data = manifest()
    data["status"] = "DRAFT"
    data["ratificacao"] = {"ratificado": False, "revisor": None, "ratificado_em": None}
    if change == "flag_true":
        data["ratificacao"]["ratificado"] = True
    if change == "flag_missing":
        del data["ratificacao"]["ratificado"]
    if change == "reviewer":
        data["ratificacao"]["revisor"] = "SYNTHETIC"
    if change == "date":
        data["ratificacao"]["ratificado_em"] = "2026-09-06"
    if change == "null_block":
        data["ratificacao"] = None
    if change == "bad_block":
        data[gate._BLOCK_KEY] = "malformed"
    if change == "bad_window":
        data[gate._BLOCK_KEY][1]["janela_drenagem"]["fim"] = "garbage"
    if change == "bad_evidence":
        data[gate._BLOCK_KEY][0]["evidencia_provisionamento"] = "TODO"
    assert cli(write(tmp_path, data)).returncode == 1


@pytest.mark.parametrize(
    "value",
    [
        "TODO",
        "PENDENTE",
        "placeholder",
        "x",
        "arn:aws:...:key",
        "evidence://receipts/TODO-20260906",
        "evidence://receipts/placeholder-20260906",
        "evidence://receipts/..",
        "evidence://receipts/a/../receipt-20260906",
        "evidence://receipts/receipt%2D20260906",
        "https://user:password@host/receipt-20260906",
        "https://host/receipt-20260906?key=secret",
        "https://host/receipt-20260906#secret",
        "file:///secret-key",
        "evidence://receipts/x",
        "evidence://receipts/receipt-20260906/",
        None,
        True,
        42,
        {},
        [],
    ],
)
def test_reference_contract_isolated(tmp_path: Path, value: Any) -> None:
    data = manifest()
    data[gate._BLOCK_KEY][0]["evidencia_provisionamento"] = value
    findings = gate.run_gate(write(tmp_path, data))
    assert gate.exit_code_for(findings) == 1
    assert any(f.prereq_id == "phi_hmac_key_provisionado" and f.level == gate.LEVEL_FAIL for f in findings)


@pytest.mark.parametrize("value", [REFERENCE, "https://audit.maezo.internal/receipts/receipt-20260906"])
def test_concrete_reference_cli_no_secret_or_receipt_lookup(tmp_path: Path, value: str) -> None:
    data = manifest()
    data[gate._BLOCK_KEY][0]["evidencia_provisionamento"] = value
    result = cli(write(tmp_path, data))
    assert result.returncode == 0
    assert value not in result.stdout + result.stderr
    assert "não verificado" in result.stdout


@pytest.mark.parametrize(
    "start,end",
    [
        ("garbage", "tomorrow"),
        ("2026-02-30T00:00:00Z", "2026-03-01T00:00:00Z"),
        ("2026-10-01T01:00:00", "2026-10-01T02:00:00"),
        ("2026-10-01T01:00:00Z", "2026-10-01T01:00:00Z"),
        ("2026-10-01T01:00:00Z", "2026-10-01T00:00:00Z"),
        ("2026-10-01T01:00:00-03:00", "2026-10-01T04:00:00Z"),
        ("2026-10-01T01:00:00-03:00", "2026-10-01T02:00:00+03:00"),
        ("2026-10-01T01:00:00+00:60", "2026-10-01T04:00:00Z"),
        ("2026-10-01T01:00:00+24:00", "2026-10-01T04:00:00Z"),
        (float("inf"), "2026-10-01T04:00:00Z"),
        (float("nan"), "2026-10-01T04:00:00Z"),
        (True, "2026-10-01T04:00:00Z"),
        (None, None),
        ([], {}),
    ],
)
def test_drain_validation_isolated(tmp_path: Path, start: Any, end: Any) -> None:
    data = manifest()
    data[gate._BLOCK_KEY][1]["janela_drenagem"] = {"inicio": start, "fim": end}
    findings = gate.run_gate(write(tmp_path, data))
    assert gate.exit_code_for(findings) == 1
    assert any(f.prereq_id == "janela_drenagem_cancel_inad" and f.level == gate.LEVEL_FAIL for f in findings)


@pytest.mark.parametrize(
    "start,end",
    [
        ("2026-10-01T01:00:00-03:00", "2026-10-01T04:00:00.000001Z"),
        ("2026-10-01T05:00:00+03:00", "2026-10-01T03:00:00Z"),
    ],
)
def test_positive_utc_interval_without_invented_minimum(tmp_path: Path, start: str, end: str) -> None:
    data = manifest()
    data[gate._BLOCK_KEY][1]["janela_drenagem"] = {"inicio": start, "fim": end}
    assert cli(write(tmp_path, data)).returncode == 0


def test_same_path_failure_and_recovery_cli(tmp_path: Path) -> None:
    good = manifest()
    path = write(tmp_path, good)
    assert cli(path).returncode == 0
    bad = copy.deepcopy(good)
    bad[gate._BLOCK_KEY][0]["atendido"] = False
    write(tmp_path, bad)
    assert cli(path).returncode == 1
    write(tmp_path, good)
    assert cli(path).returncode == 0
    path.write_text("status: [")
    assert cli(path).returncode == 1
    write(tmp_path, good)
    assert cli(path).returncode == 0


def test_exact_draft_and_ratified_off_controls(tmp_path: Path) -> None:
    for mode in ["off", " OFF ", False]:
        data = manifest()
        data["modo"] = mode
        del data[gate._BLOCK_KEY]
        assert cli(write(tmp_path, data)).returncode == 0
