"""Real tiny locked captures; production D7 is source-inspected, never executed."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from scripts.dev import run_historical_catalog_recipe as successor

ROOT = Path(__file__).resolve().parents[3]
TEST = "tests/unit/test_tiny.py"
FILES = (TEST, "docs/evidence-ledger.md", "uv.lock", "pyproject.toml", ".python-version")


def rewrite(path: Path, data: bytes) -> None:
    """Deliberately mutate a private test copy; production writes are exclusive."""
    path.write_bytes(data)
    path.chmod(0o600)


@pytest.fixture(scope="module")
def producer() -> Any:
    return successor.load_producer(ROOT)


def test_d7_source_preflight_reads_exact_git_without_execution(producer: Any) -> None:
    successor.preflight(producer, ROOT, successor.select("D7unit802"))
    assert set(producer.CATALOG) == {"PFV8821", "D6unit136d"}
    assert producer.digest(Path(producer.__file__).read_bytes()) == (
        "25a412499cd64ae663de1358e488480cb6b6a5c0e772583ccc0bece45380877c"
    )
    assert (
        producer.digest(Path(producer.runner.__file__).read_bytes())
        == producer.PINS["scripts/dev/run_engine_integration.py"]
    )
    assert (
        producer.digest(Path(producer.checker.__file__).read_bytes())
        == producer.PINS["scripts/ci/check_evidence_ledger_hashes.py"]
    )


@pytest.mark.parametrize("name", ["PFV8821", "D6unit136d", "D6migrationa92", "../D7unit802", "", "80206"])
def test_no_record_controlled_source_or_borrowed_original_catalogue(name: str) -> None:
    with pytest.raises(successor.CatalogueRefusedError, match="catalogued"):
        successor.select(name)


@pytest.mark.parametrize(
    "field", ["source", "test", "task", "date", "row_sha256", "declared_sha256", "files"]
)
def test_source_identity_tampering_refused_before_capture(producer: Any, field: str) -> None:
    entry = successor.select("D7unit802")
    value: Any = "f" * 40 if field == "source" else "unbound"
    if field == "files":
        value = tuple((path, "0" * 64) for path, _ in entry.files)
    with pytest.raises((ValueError, RuntimeError, subprocess.CalledProcessError)):
        successor.preflight(producer, ROOT, entry._replace(**{field: value}))


@pytest.fixture(scope="module")
def tiny(tmp_path_factory: pytest.TempPathFactory, producer: Any) -> Any:
    repo = tmp_path_factory.mktemp("successor-tiny-source")
    content = {
        TEST: b"from maezo import VALUE\ndef test_real(): assert VALUE == 7\n",
        "src/maezo/__init__.py": b"VALUE = 7\n",
        "pyproject.toml": (
            b'[project]\nname="tiny"\nversion="0.0.0"\nrequires-python=">=3.12,<3.13"\n'
            b'[project.optional-dependencies]\ndev=["pytest==9.1.1"]\n[tool.uv]\npackage=false\n'
        ),
        ".python-version": b"3.12\n",
    }
    declaration = successor.digest(content[TEST])
    row = (
        "| TINY | 2026-09-08 | A | V | source | synthetic source fixture | "
        f"sha256:{declaration} ({TEST}) | unverified |"
    )
    content["docs/evidence-ledger.md"] = (row + "\n").encode()
    for path, payload in content.items():
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)

    def command(*argv: str) -> str:
        return (
            subprocess.check_output(argv, cwd=repo, env=producer.environment(), stderr=subprocess.PIPE)
            .decode()
            .strip()
        )

    command("uv", "lock", "--offline", "--no-config")
    command("git", "init", "-q")
    command("git", "add", ".")
    command(
        "git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "tiny"
    )
    entry = successor.SourceEntry(
        "fixture-only",
        command("git", "rev-parse", "HEAD"),
        TEST,
        "TINY",
        "2026-09-08",
        successor.digest(row.encode()),
        declaration,
        tuple((name, successor.digest((repo / name).read_bytes())) for name in FILES),
    )
    return repo, entry


@pytest.fixture
def isolated(monkeypatch: pytest.MonkeyPatch, tiny: Any, producer: Any) -> Any:
    repo, entry = tiny
    # Test-only tiny source injection. No production command accepts these fields.
    monkeypatch.setattr(successor, "SOURCE_CATALOG_V1", (entry,))
    monkeypatch.setattr(successor, "load_producer", lambda repo: producer)
    return repo, entry


@pytest.fixture(scope="module")
def captured(tmp_path_factory: pytest.TempPathFactory, tiny: Any, producer: Any) -> Path:
    repo, entry = tiny
    out = tmp_path_factory.mktemp("successor-tiny-output") / "packet"
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(successor, "SOURCE_CATALOG_V1", (entry,))
        patch.setattr(successor, "load_producer", lambda repo: producer)
        successor.capture_catalogued(repo, out, entry.proof_id)
    return out


def test_real_tiny_capture_has_distinct_producer_and_original_receipt(
    isolated: Any,
    captured: Path,
    producer: Any,
) -> None:
    repo, entry = isolated
    receipt = successor.validate_receipt(repo, captured, entry.proof_id)
    outer = producer.load(captured / "successor.json")
    assert receipt["coverage"]["selected_count"] == 1
    assert receipt["scope"]["historical_claim_truth"] == "mismatch"
    assert outer["producer"]["sha256"] != receipt["source"]["tooling"]["helper"]
    assert receipt["source"]["tooling"]["helper"] == (
        "25a412499cd64ae663de1358e488480cb6b6a5c0e772583ccc0bece45380877c"
    )
    assert outer["scope"] == {
        "ledger_acceptance": False,
        "independent_review": False,
        "fresh_current_execution": False,
    }
    assert set(receipt["scope"]["original_observations"]) == {
        "successor-source-catalogue",
        "successor-producer",
        "immutable-source-loader",
    }


@pytest.mark.parametrize(
    "field", ["producer", "catalogue", "observations", "scope", "capture", "original_producer_sources"]
)
def test_rehashed_outer_identity_substitution_refused(
    isolated: Any,
    captured: Path,
    tmp_path: Path,
    producer: Any,
    field: str,
) -> None:
    repo, entry = isolated
    out = tmp_path / "copy"
    shutil.copytree(captured, out)
    value = producer.load(out / "successor.json")
    value[field] = {"forged": True}
    rewrite(out / "successor.json", producer.encode(value))
    rewrite(out / "manifest.json", producer.encode(producer.Packet(out).hashes(exclude="manifest.json")))
    with pytest.raises(successor.CatalogueRefusedError, match="binding"):
        successor.validate_receipt(repo, out, entry.proof_id)


@pytest.mark.parametrize("mutation", ["raw", "bool-count", "skipped", "helper", "extra", "distribution"])
def test_rehashed_inner_forgeries_still_require_literal_full_validator(
    isolated: Any,
    captured: Path,
    tmp_path: Path,
    producer: Any,
    mutation: str,
) -> None:
    repo, entry = isolated
    out = tmp_path / "copy"
    shutil.copytree(captured, out)
    inner = out / "capture"
    if mutation == "raw":
        rewrite(inner / "pytest.stdout", b"fabricated PASSED\n")
    elif mutation == "skipped":
        phases = producer.load(inner / "phase.json")
        phases["coverage"]["phases"][0]["outcome"] = "skipped"
        rewrite(inner / "phase.json", producer.encode(phases))
    elif mutation == "extra":
        rewrite(out / "unbound.json", b"{}\n")
    else:
        value = producer.load(inner / "receipt.json")
        if mutation == "bool-count":
            value["coverage"]["selected_count"] = True
        elif mutation == "helper":
            value["source"]["tooling"]["helper"] = successor.digest(Path(successor.__file__).read_bytes())
        else:
            value["environment"]["inventory"]["distributions"][0]["version"] = "999.999"
        rewrite(inner / "receipt.json", producer.encode(value))
    rewrite(inner / "manifest.json", producer.encode(producer.Packet(inner).hashes(exclude="manifest.json")))
    rewrite(out / "successor.json", producer.encode(successor.envelope(producer, entry, out)))
    rewrite(out / "manifest.json", producer.encode(producer.Packet(out).hashes(exclude="manifest.json")))
    with pytest.raises((ValueError, RuntimeError)):
        successor.validate_receipt(repo, out, entry.proof_id)


@pytest.mark.parametrize("mutation", ["missing", "symlink", "fifo", "corrupt", "hardlink"])
def test_entrypoint_refuses_loader_substitution_without_executing_it(tmp_path: Path, mutation: str) -> None:
    scripts = tmp_path / "scripts/dev"
    scripts.mkdir(parents=True)
    entrypoint = scripts / "run_historical_catalog_recipe.py"
    entrypoint.write_bytes(Path(successor.__file__).read_bytes())
    loader = scripts / "historical_tool_sources.py"
    if mutation == "symlink":
        loader.symlink_to(ROOT / successor.LOADER)
    elif mutation == "hardlink":
        original = tmp_path / "loader-copy.py"
        original.write_bytes((ROOT / successor.LOADER).read_bytes())
        os.link(original, loader)
    elif mutation == "fifo":
        os.mkfifo(loader)
    elif mutation == "corrupt":
        loader.write_text("raise RuntimeError('UNREVIEWED_LOADER_EXECUTED')\n")
    result = subprocess.run(
        [sys.executable, "-I", "-S", str(entrypoint), "preflight", "D7unit802", "--repo", str(ROOT)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode != 0
    assert "UNREVIEWED_LOADER_EXECUTED" not in result.stderr


def test_isolated_cli_exposes_executable_d7_preflight() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-S",
            str(ROOT / successor.ENTRYPOINT),
            "preflight",
            "D7unit802",
            "--repo",
            str(ROOT),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["entry"]["row_sha256"] == successor.select("D7unit802").row_sha256


def test_no_source_is_executed_after_failed_preflight(
    isolated: Any,
    tmp_path: Path,
    producer: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, entry = isolated
    monkeypatch.setattr(successor, "SOURCE_CATALOG_V1", (entry._replace(row_sha256="0" * 64),))
    monkeypatch.setattr(producer, "capture_source", lambda *args: pytest.fail("capture must not start"))
    out = tmp_path / "never-created"
    with pytest.raises(successor.CatalogueRefusedError):
        successor.capture_catalogued(repo, out, entry.proof_id)
    assert not out.exists()
