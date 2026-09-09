"""Finite tiny real Git/pytest runs; no Maezo runtime/service or original history runs."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location(
    "capture_adapter", ROOT / "scripts/dev/run_historical_unit_recipe.py"
)
h = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(h)


def cmd(repo, *argv):
    return subprocess.run(argv, cwd=repo, env=h.environment(), check=True, capture_output=True).stdout


@pytest.fixture(scope="module")
def source(tmp_path_factory):
    repo = tmp_path_factory.mktemp("tiny-historical-git")
    (repo / "src/maezo").mkdir(parents=True)
    (repo / "src/maezo/__init__.py").write_text("VALUE = 7\n")
    (repo / "tests/unit").mkdir(parents=True)
    (repo / "tests/unit/test_tiny.py").write_text(
        "from maezo import VALUE\ndef test_real():\n    assert VALUE == 7\n"
    )
    (repo / "docs").mkdir()
    declaration = h.checker.compute_recipe_hash(["tests/unit/test_tiny.py::test_real PASSED"])
    (repo / "docs/evidence-ledger.md").write_text(
        f"| TINY | 2026-09-08 | {declaration} (tests/unit/test_tiny.py) |\n"
    )
    (repo / "pyproject.toml").write_text(
        '[project]\nname="tiny"\nversion="0.0.0"\nrequires-python=">=3.12,<3.13"\n[project.optional-dependencies]\ndev=["pytest==9.1.1"]\n[tool.uv]\npackage=false\n'
    )
    (repo / ".python-version").write_text("3.12\n")
    cmd(repo, "uv", "lock", "--offline", "--no-config")
    cmd(repo, "git", "init", "-q")
    cmd(repo, "git", "add", ".")
    cmd(
        repo,
        "git",
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "commit",
        "-qm",
        "tiny reviewed source",
    )
    return repo, cmd(repo, "git", "rev-parse", "HEAD").decode().strip()


@pytest.fixture(scope="module")
def positive(source, tmp_path_factory):
    repo, sha = source
    out = tmp_path_factory.mktemp("historical-proof-parent") / "proof"
    receipt = h.capture_source(
        repo, sha, "tests/unit/test_tiny.py", out, "fixture", "TINY", "2026-09-08", {"observation": "0" * 64}
    )
    return out, receipt


def test_positive_owned_source(positive):
    out, receipt = positive
    assert receipt["coverage"]["nodes"] == ["tests/unit/test_tiny.py::test_real"]
    assert receipt["scope"]["historical_claim_truth"] == "equal"
    assert receipt["scope"]["current_proof_disposition"] == "unresolved"
    assert receipt["cleanup"]["removed"]
    assert not Path(receipt["environment"]["checkout"]).exists()
    assert "--locked" in receipt["environment"]["preparation"]["prepare"]["argv"]
    assert out.stat().st_mode & 0o777 == 0o700
    assert all(p.stat().st_mode & 0o777 == 0o600 for p in out.rglob("*") if p.is_file())


@pytest.mark.parametrize("name", ["PYTEST_ADDOPTS", "PYTEST_PLUGINS", "PYTHONPATH", "UV_PROJECT_ENVIRONMENT"])
def test_contamination_refused(monkeypatch, name):
    monkeypatch.setenv(name, "deliberately-forbidden")
    with pytest.raises(h.CaptureRefusedError, match="ambient"):
        h.environment()


def test_wrong_lock_dirty_source(source):
    repo, sha = source
    lock = repo / "uv.lock"
    original = lock.read_bytes()
    try:
        lock.write_bytes(original + b"\n# modified\n")
        with pytest.raises(h.CaptureRefusedError, match="dirty"):
            h.ancestry(repo, sha)
    finally:
        lock.write_bytes(original)


def test_split_stream_raw_recipe(tmp_path):
    raw = "tests/unit/t.py::test_a[password=private-marker] PASSED [ 50%]\n"
    argv = [
        sys.executable,
        "-I",
        "-c",
        f"import os; os.write(1,{raw.encode()!r}); os.write(2,b'tests/unit/t.py::test_b PASSED\\n')",
    ]
    c = h.capture(argv, tmp_path, h.environment(), tmp_path, "split")
    joined = (tmp_path / c["stdout"]).read_bytes() + (tmp_path / c["stderr"]).read_bytes()
    assert c["combined_sha256"] == h.digest(joined)
    lines = h.checker.extract_result_lines(joined.decode())
    assert len(lines) == 2
    assert h.checker.compute_recipe_hash(lines) != h.checker.compute_recipe_hash(
        h.checker.extract_result_lines(h.runner._redact_text(joined.decode()))
    )
    assert b"private-marker" in joined


@pytest.mark.parametrize(
    "code,expected",
    [
        ("raise SystemExit(1)", "completed"),
        ("import time; print('started',flush=True); time.sleep(10)", "timeout"),
        ("import os; os.write(1,b'x'*20000)", "oversized"),
    ],
)
def test_failure_timeout_and_size(tmp_path, code, expected):
    c = h.capture(
        [sys.executable, "-I", "-c", code],
        tmp_path,
        h.environment(),
        tmp_path,
        "failure",
        timeout=0.2,
        limit=1024,
    )
    assert c["status"] == expected
    assert c["quiescent"]
    assert not h.runner._group_exists(c["pgid"])
    assert (tmp_path / c["stdout"]).stat().st_size <= 1024
    with pytest.raises(h.CaptureRefusedError):
        h.successful(c)


def test_timeout_child_cleanup(tmp_path):
    code = (
        "import subprocess,sys,time; subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']);"
        " print('child spawned',flush=True); time.sleep(30)"
    )
    c = h.capture(
        [sys.executable, "-I", "-c", code], tmp_path, h.environment(), tmp_path, "child", timeout=0.3
    )
    assert c["status"] == "timeout"
    assert c["quiescent"] and not h.runner._group_exists(c["pgid"])
    assert b"child spawned" in (tmp_path / c["stdout"]).read_bytes()


@pytest.mark.parametrize(
    "body", [b'{"a":1,"a":2}', b"[[[[[[[[[[[[[[[[[[1]]]]]]]]]]]]]]]]]]", b'{"n":NaN}', b"\xff"]
)
def test_bad_json(tmp_path, body):
    p = tmp_path / "bad.json"
    p.write_bytes(body)
    with pytest.raises(h.CaptureRefusedError):
        h.load(p)


def test_missing_forged_and_phase_receipts(positive, tmp_path):
    out, receipt = positive
    sources = h.load(out / "source-before.json")
    checkout = Path(receipt["environment"]["checkout"])
    for file in ("pytest.stdout", "pytest.stderr", "guard-base.json"):
        (tmp_path / file).write_bytes((out / file).read_bytes())
    with pytest.raises(FileNotFoundError):
        h.validate_run(
            tmp_path, checkout, receipt["source"]["test"], sources, receipt["execution"], check_files=False
        )
    marker = h.load(out / "phase.json")
    for mutate in [
        lambda m: m.update(archived=[]),
        lambda m: m.update(escaped=["/sibling/source.py"]),
        lambda m: m["coverage"].update(selected=[]),
        lambda m: m["coverage"]["phases"][0].update(outcome="failed"),
        lambda m: m["coverage"].update(started=1),
        lambda m: m.update(forged="field"),
    ]:
        altered = json.loads(json.dumps(marker))
        mutate(altered)
        (tmp_path / "phase.json").write_bytes(h.encode(altered))
        with pytest.raises(h.CaptureRefusedError):
            h.validate_run(
                tmp_path,
                checkout,
                receipt["source"]["test"],
                sources,
                receipt["execution"],
                check_files=False,
            )


def test_borrowed_editable_and_inventory(positive, tmp_path):
    out, receipt = positive
    inv = json.loads(json.dumps(receipt["environment"]["inventory"]))
    command = dict(h.load(out / "inventory-before.command.json"))
    command["stdout"] = "inventory.json"
    checkout = Path(receipt["environment"]["checkout"])
    inv["distributions"][0]["direct_url"] = {
        "url": "file:///sibling/workspace",
        "dir_info": {"editable": True},
    }
    (tmp_path / "inventory.json").write_bytes(h.encode(inv))
    with pytest.raises(h.CaptureRefusedError, match="borrowed"):
        h.inventory(command, tmp_path, checkout)


def test_closed_receipt_recomputed(positive, source):
    out, receipt = positive
    repo, sha = source
    got = h.validate_receipt(
        out,
        repo,
        ("fixture", sha, "tests/unit/test_tiny.py", "TINY", "2026-09-08"),
        {"observation": "0" * 64},
    )
    assert got == receipt
    original = (out / "receipt.json").read_bytes()
    try:
        for mutate in [
            lambda r: r.update(unknown=True),
            lambda r: r["source"].update(physical_occurrences=True),
            lambda r: r["environment"]["inventory"]["distributions"][0].update(version="forged"),
            lambda r: r["execution"]["argv"].append("-k"),
            lambda r: r["scope"].update(operational_validation="accepted"),
        ]:
            altered = json.loads(original)
            mutate(altered)
            (out / "receipt.json").write_bytes(h.encode(altered))
            with pytest.raises(h.CaptureRefusedError):
                h.validate_receipt(
                    out,
                    repo,
                    ("fixture", sha, "tests/unit/test_tiny.py", "TINY", "2026-09-08"),
                    {"observation": "0" * 64},
                )
    finally:
        (out / "receipt.json").write_bytes(original)


@pytest.mark.parametrize(
    "body",
    [
        "def test_real(): assert False\n",
        "# no tests\n",
        "import pytest\n@pytest.fixture(autouse=True)\ndef bad(): raise RuntimeError('setup')\n"
        "def test_real(): pass\n",
        "import importlib.metadata as m\ndef test_real():\n"
        "    p=m.distribution('pytest')._path / 'METADATA'\n"
        "    p.write_text(p.read_text().replace('Version:', 'Version: mutated'))\n",
    ],
)
def test_real_nonpassing_and_inventory_mutation(source, tmp_path, body):
    repo, _ = source
    copy = tmp_path / "repo"
    cmd(tmp_path, "git", "clone", "--quiet", str(repo), str(copy))
    (copy / "tests/unit/test_tiny.py").write_text(body)
    cmd(copy, "git", "add", ".")
    cmd(
        copy,
        "git",
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "commit",
        "-qm",
        "negative reviewed fixture",
    )
    sha = cmd(copy, "git", "rev-parse", "HEAD").decode().strip()
    out = tmp_path / "negative-proof"
    with pytest.raises(h.CaptureRefusedError):
        h.capture_source(
            copy,
            sha,
            "tests/unit/test_tiny.py",
            out,
            "fixture-negative",
            "TINY",
            "2026-09-08",
            {"observation": "0" * 64},
        )
    assert (out / "unresolved.json").exists()
    assert not (out / "receipt.json").exists()
    assert h.load(out / "checkout-cleanup.json")["removed"]


@pytest.mark.parametrize("case", ["escaped-sibling", "committed-lock-mismatch"])
def test_real_source_escape_and_locked_preparation(source, tmp_path, case):
    repo, _ = source
    copy = tmp_path / "repo"
    cmd(tmp_path, "git", "clone", "--quiet", str(repo), str(copy))
    if case == "escaped-sibling":
        sibling = tmp_path / "sibling.py"
        sibling.write_text("VALUE = 8\n")
        (copy / "tests/unit/test_tiny.py").write_text(
            f"import runpy\ndef test_real():\n    assert runpy.run_path({str(sibling)!r})['VALUE'] == 8\n"
        )
    else:
        config = copy / "pyproject.toml"
        config.write_text(config.read_text().replace("pytest==9.1.1", "pytest==8.4.2"))
    cmd(copy, "git", "add", ".")
    cmd(
        copy,
        "git",
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "commit",
        "-qm",
        "reviewed source boundary negative",
    )
    sha = cmd(copy, "git", "rev-parse", "HEAD").decode().strip()
    out = tmp_path / "negative-proof"
    with pytest.raises(h.CaptureRefusedError):
        h.capture_source(
            copy,
            sha,
            "tests/unit/test_tiny.py",
            out,
            "fixture-negative",
            "TINY",
            "2026-09-08",
            {"observation": "0" * 64},
        )
    assert (out / "unresolved.json").exists()
    assert not (out / "receipt.json").exists()
    assert h.load(out / "checkout-cleanup.json")["removed"]


def test_oversized_json_and_strict_raw_utf8(positive, tmp_path):
    p = tmp_path / "oversized.json"
    p.write_bytes(b" " * 65)
    with pytest.raises(h.CaptureRefusedError, match="oversized"):
        h.load(p, limit=64)
    out, receipt = positive
    command = dict(receipt["execution"])
    (tmp_path / "pytest.stdout").write_bytes(b"\xff")
    (tmp_path / "pytest.stderr").write_bytes(b"")
    command["combined_sha256"] = h.digest(b"\xff")
    with pytest.raises(h.CaptureRefusedError, match="UTF-8"):
        h.validate_run(
            tmp_path,
            Path(receipt["environment"]["checkout"]),
            receipt["source"]["test"],
            h.load(out / "source-before.json"),
            command,
            check_files=False,
        )
