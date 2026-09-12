"""D7-ASYNC-R1/R2 tiny own-lock executions; never execute historical D7 here."""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from scripts.dev import run_historical_catalog_recipe as catalog

ROOT = Path(__file__).resolve().parents[3]
TEST = "tests/unit/test_mixed.py"
FILES = (TEST, "docs/evidence-ledger.md", "uv.lock", "pyproject.toml", ".python-version")


def rewrite(path: Path, data: bytes) -> None:
    path.write_bytes(data)
    path.chmod(0o600)


@pytest.fixture(scope="module")
def original() -> Any:
    return catalog.load_producer(ROOT)


@pytest.fixture(scope="module")
def async_module() -> Any:
    return catalog.load_async_producer()


def tiny_source(parent: Path, original: Any, kind: str = "positive") -> Any:
    repo = parent / "repo"
    repo.mkdir()
    witness = parent / "body-witness.txt"
    body = f"""import asyncio
from pathlib import Path
import pytest
from maezo import VALUE
WITNESS = Path({str(witness)!r})
def record(value):
    with WITNESS.open("a") as stream:
        stream.write(value + "\\n")
def test_sync():
    assert VALUE == 7
    record("sync")
@pytest.mark.asyncio
async def test_awaited_body():
    record("entered")
    async def exercise():
        await asyncio.sleep(0)
        record("awaited")
        return VALUE
    assert await exercise() == {8 if kind == "assertion-failure" else 7}
    record("completed")
"""
    deps = '["pytest==9.1.1"]' if kind == "missing-plugin" else '["pytest==9.1.1", "pytest-asyncio==1.4.0"]'
    content = {
        TEST: body.encode(),
        "src/maezo/__init__.py": b"VALUE = 7\n",
        "pyproject.toml": (
            '[project]\nname="tiny-async"\nversion="0.0.0"\nrequires-python=">=3.12,<3.13"\n'
            f"[project.optional-dependencies]\ndev={deps}\n[tool.uv]\npackage=false\n"
        ).encode(),
        ".python-version": b"3.12\n",
    }
    if kind == "shadow-plugin":
        content["pytest_asyncio/__init__.py"] = b"# synthetic wrong-location plugin\n"
        content["pytest_asyncio/plugin.py"] = b"# no async execution hooks\n"
    declaration = catalog.digest(content[TEST])
    row = f"| TINY-ASYNC | 2026-09-09 | sha256:{declaration} ({TEST}) |"
    content["docs/evidence-ledger.md"] = (row + "\n").encode()
    for name, payload in content.items():
        target = repo / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)

    def command(*argv: str) -> str:
        result = subprocess.run(argv, cwd=repo, env=original.environment(), capture_output=True, check=True)
        return result.stdout.decode().strip()

    command("uv", "lock", "--offline", "--no-config")
    command("git", "init", "-q")
    command("git", "add", ".")
    command(
        "git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "tiny"
    )
    entry = catalog.SourceEntry(
        "tiny-async-only",
        command("git", "rev-parse", "HEAD"),
        TEST,
        "TINY-ASYNC",
        "2026-09-09",
        catalog.digest(row.encode()),
        declaration,
        tuple((name, catalog.digest((repo / name).read_bytes())) for name in FILES),
    )
    return repo, entry, witness


@pytest.fixture(scope="module")
def tiny(tmp_path_factory: pytest.TempPathFactory, original: Any) -> Any:
    return tiny_source(tmp_path_factory.mktemp("async-positive-source"), original)


@pytest.fixture
def isolated(monkeypatch: pytest.MonkeyPatch, tiny: Any, original: Any) -> Any:
    repo, entry, _ = tiny
    # Only substitute the synthetic Git catalogue, not execution argv/environment.
    monkeypatch.setattr(catalog, "SOURCE_CATALOG_V1", (entry,))
    monkeypatch.setattr(catalog, "load_producer", lambda repo: original)
    return repo, entry


@pytest.fixture(scope="module")
def positive(tmp_path_factory: pytest.TempPathFactory, tiny: Any, original: Any) -> Path:
    repo, entry, _ = tiny
    out = tmp_path_factory.mktemp("async-positive-output") / "packet"
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(catalog, "SOURCE_CATALOG_V1", (entry,))
        patch.setattr(catalog, "load_producer", lambda repo: original)
        catalog.capture_async_catalogued(repo, out, entry.proof_id)
    return out


def test_real_mixed_capture_and_readback_exercises_await(
    positive: Path,
    isolated: Any,
    tiny: Any,
    original: Any,
    async_module: Any,
) -> None:
    repo, entry = isolated
    receipt = catalog.validate_async_receipt(repo, positive, entry.proof_id)
    assert tiny[2].read_text().splitlines() == ["sync", "entered", "awaited", "completed"]
    assert receipt["coverage"]["selected_count"] == 2
    phase = original.load(positive / "capture/phase.json")["coverage"]
    assert len(phase["phases"]) == 6
    assert all(p["outcome"] == "passed" and p["wasxfail"] is False for p in phase["phases"])
    assert phase["deselected"] == []
    assert receipt["schema"] == async_module.SCHEMA
    assert receipt["recipe"] == async_module.RECIPE
    assert receipt["execution"]["argv"][-2:] == ["-p", "pytest_asyncio.plugin"]
    assert receipt["source"]["tooling"]["helper"] == catalog.ASYNC_SHA256
    assert (
        receipt["source"]["tooling"]["original_helper"]
        == "25a412499cd64ae663de1358e488480cb6b6a5c0e772583ccc0bece45380877c"
    )
    assert receipt["plugin"]["distribution"]["version"] == "1.4.0"
    assert any(path.endswith("/pytest_asyncio/plugin.py") for path in receipt["plugin"]["imports"])
    assert all(
        Path(path).is_relative_to(receipt["environment"]["venv"]) for path in receipt["plugin"]["imports"]
    )
    assert receipt["cleanup"]["removed"] is True and receipt["cleanup"]["pending_pgids"] == []
    assert not Path(receipt["cleanup"]["path"]).exists()
    assert set(original.Packet(positive / "capture").files) == async_module.MEMBERS
    assert receipt["scope"]["status"] == "observation-only"
    assert receipt["scope"]["current_proof_disposition"] == "unresolved"
    with pytest.raises(ValueError):
        original.validate_receipt(
            positive / "capture", repo, entry.identity(), catalog.async_observations(original, entry)
        )


def load_mutant(path: Path, code: str) -> Any:
    rewrite(path, code.encode())
    spec = importlib.util.spec_from_file_location("explicit_test_source_mutant", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "kind", ["no-plugin", "changed-plugin", "missing-plugin", "shadow-plugin", "assertion-failure"]
)
def test_real_failure_packets_never_mint_receipt(
    tmp_path: Path,
    original: Any,
    async_module: Any,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    repo, entry, witness = tiny_source(tmp_path, original, kind)
    module = async_module
    if kind in {"no-plugin", "changed-plugin"}:
        code = Path(async_module.__file__).read_text()
        needle = '                "-p",\n                "pytest_asyncio.plugin",\n'
        assert code.count(needle) == 1
        replacement = (
            "" if kind == "no-plugin" else needle.replace("pytest_asyncio.plugin", "no:pytest_asyncio.plugin")
        )
        module = load_mutant(tmp_path / "killed_source_mutant.py", code.replace(needle, replacement))
    monkeypatch.setattr(catalog, "SOURCE_CATALOG_V1", (entry,))
    monkeypatch.setattr(catalog, "load_producer", lambda repo: original)
    monkeypatch.setattr(catalog, "load_async_producer", lambda: module)
    out = tmp_path / "failure-packet"
    with pytest.raises(ValueError):
        catalog.capture_async_catalogued(repo, out, entry.proof_id)
    assert not (out / "successor.json").exists()
    assert not (out / "manifest.json").exists()
    assert not (out / "capture/receipt.json").exists()
    assert (out / "capture/unresolved.json").exists()
    cleanup = original.load(out / "capture/checkout-cleanup.json")
    assert cleanup["removed"] is True and cleanup["pending_pgids"] == []
    assert not Path(cleanup["path"]).exists()
    command = original.load(out / "capture/pytest.command.json")
    assert command["rc"] != 0 and command["quiescent"] is True
    observed = witness.read_text().splitlines() if witness.exists() else []
    if kind == "assertion-failure":
        assert observed == ["sync", "entered", "awaited"]
    else:
        assert "entered" not in observed
    if kind == "no-plugin":
        assert observed == ["sync"]
        phase = original.load(out / "capture/phase.json")["coverage"]
        assert phase["selected"] == [TEST + "::test_sync", TEST + "::test_awaited_body"]
        assert sum(p["when"] == "call" and p["outcome"] == "failed" for p in phase["phases"]) == 1


def reseal(out: Path, original: Any, entry: Any) -> None:
    inner = out / "capture"
    rewrite(inner / "manifest.json", original.encode(original.Packet(inner).hashes(exclude="manifest.json")))
    rewrite(out / "successor.json", original.encode(catalog.async_envelope(original, entry, out)))
    rewrite(out / "manifest.json", original.encode(original.Packet(out).hashes(exclude="manifest.json")))


def replace_inventory(inner: Path, original: Any, receipt: Any, mutation: str) -> None:
    inventory = receipt["environment"]["inventory"]
    item = next(item for item in inventory["distributions"] if item["name"] == "pytest-asyncio")
    if mutation == "missing-inventory":
        inventory["distributions"].remove(item)
    elif mutation == "wrong-version":
        item["version"] = "99.0"
    elif mutation == "wrong-location":
        item["location"] = "/outside/host/site-packages"
    elif mutation == "borrowed-interpreter":
        inventory["interpreter"]["path"] = "/outside/host/python"
    elif mutation == "bool-editable":
        item["direct_url"] = {"url": "file:///outside", "dir_info": {"editable": 1}}
    receipt["environment"]["inventory_sha256"] = original.digest(original.encode(inventory))
    for label in ("inventory-before", "inventory-after"):
        raw = original.encode(inventory)
        rewrite(inner / (label + ".stdout"), raw)
        command = original.load(inner / (label + ".command.json"))
        command["stdout_sha256"] = original.digest(raw)
        command["combined_sha256"] = original.digest(raw + (inner / (label + ".stderr")).read_bytes())
        rewrite(inner / (label + ".command.json"), original.encode(command))


MUTATIONS = [
    "omitted-argv",
    "changed-argv",
    "argv-bool",
    "autoload",
    "recipe",
    "v1-schema",
    "producer",
    "dependency",
    "loader",
    "row",
    "source",
    "source-map",
    "missing-phase",
    "skip",
    "xfail",
    "bool-count",
    "phase-type",
    "plugin-trace",
    "plugin-trace-host",
    "plugin-metadata",
    "missing-inventory",
    "wrong-version",
    "wrong-location",
    "borrowed-interpreter",
    "bool-editable",
    "cleanup-false",
    "cleanup-bool",
    "cleanup-pending",
    "extra-field",
    "extra-file",
    "extra-directory",
    "guard-source",
    "command-rc",
    "command-time",
    "command-quiescent",
    "clone-argv",
    "uv-argv",
    "raw",
    "schema-extra",
    "foreign-uv",
]


@pytest.mark.parametrize("mutation", MUTATIONS)
def test_resealed_adversarial_packet_refuses(
    mutation: str,
    isolated: Any,
    positive: Path,
    original: Any,
    tmp_path: Path,
) -> None:
    repo, entry = isolated
    out = tmp_path / "copy"
    shutil.copytree(positive, out)
    inner = out / "capture"
    receipt = original.load(inner / "receipt.json")
    if mutation in {
        "omitted-argv",
        "changed-argv",
        "argv-bool",
        "command-rc",
        "command-time",
        "command-quiescent",
        "clone-argv",
        "uv-argv",
    }:
        label = "clone" if mutation == "clone-argv" else "uv-version" if mutation == "uv-argv" else "pytest"
        command = original.load(inner / (label + ".command.json"))
        if mutation == "omitted-argv":
            command["argv"] = command["argv"][:-2]
        elif mutation == "changed-argv":
            command["argv"][-1] = "no:pytest_asyncio.plugin"
        elif mutation == "argv-bool":
            command["argv"][-1] = True
        elif mutation == "command-rc":
            command["rc"] = 1
        elif mutation == "command-time":
            command["started_at"] = "not-a-time"
        elif mutation == "command-quiescent":
            command["quiescent"] = False
        else:
            command["argv"][-1] = "substituted"
        rewrite(inner / (label + ".command.json"), original.encode(command))
        if label == "pytest":
            receipt["execution"] = command
    elif mutation in {"autoload", "recipe", "v1-schema", "schema-extra"}:
        if mutation == "autoload":
            receipt["recipe"]["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "0"
        elif mutation == "recipe":
            receipt["recipe"]["id"] = "result-lines-fixed-v1"
        elif mutation == "v1-schema":
            receipt["schema"] = "maezo-historical-recipe-proof/v1"
        else:
            receipt["recipe"]["extra"] = True
    elif mutation in {"producer", "dependency", "loader", "row", "source", "source-map"}:
        if mutation == "producer":
            receipt["source"]["tooling"]["helper"] = receipt["source"]["tooling"]["original_helper"]
        elif mutation == "dependency":
            receipt["source"]["tooling"]["original_helper"] = "0" * 64
        elif mutation == "loader":
            receipt["scope"]["original_observations"]["immutable-source-loader"] = "0" * 64
        elif mutation == "source-map":
            mapping = original.load(inner / "source-before.json")
            mapping[TEST]["sha256"] = "0" * 64
            rewrite(inner / "source-before.json", original.encode(mapping))
            rewrite(inner / "source-after.json", original.encode(mapping))
        else:
            receipt["source"]["row_sha256" if mutation == "row" else "commit"] = "0" * 64
    elif mutation in {"missing-phase", "skip", "xfail", "phase-type", "plugin-trace", "plugin-trace-host"}:
        marker = original.load(inner / "phase.json")
        if mutation == "missing-phase":
            marker["coverage"]["phases"].pop()
        elif mutation == "skip":
            marker["coverage"]["phases"][0]["outcome"] = "skipped"
        elif mutation == "xfail":
            marker["coverage"]["phases"][0]["wasxfail"] = True
        elif mutation == "phase-type":
            marker["coverage"]["started"] = 1
        else:
            marker["archived"] = [value for value in marker["archived"] if "/pytest_asyncio/" not in value]
            if mutation == "plugin-trace-host":
                marker["archived"] += [
                    "/outside/pytest_asyncio/plugin.py",
                    "/outside/pytest_asyncio/__init__.py",
                ]
            rewrite(
                inner / "guard-base.json", original.encode({k: marker[k] for k in ("archived", "escaped")})
            )
        rewrite(inner / "phase.json", original.encode(marker))
    elif mutation == "bool-count":
        receipt["coverage"]["selected_count"] = True
    elif mutation == "plugin-metadata":
        receipt["plugin"]["distribution"]["version"] = "fake"
    elif mutation in {
        "missing-inventory",
        "wrong-version",
        "wrong-location",
        "borrowed-interpreter",
        "bool-editable",
    }:
        replace_inventory(inner, original, receipt, mutation)
    elif mutation.startswith("cleanup-"):
        cleanup = original.load(inner / "checkout-cleanup.json")
        if mutation == "cleanup-pending":
            cleanup["pending_pgids"] = [1]
        else:
            cleanup["removed"] = False if mutation == "cleanup-false" else 1
        receipt["cleanup"] = cleanup
        rewrite(inner / "checkout-cleanup.json", original.encode(cleanup))
    elif mutation == "extra-field":
        receipt["extra"] = 1
    elif mutation == "extra-file":
        rewrite(inner / "unbound.json", b"{}\n")
    elif mutation == "extra-directory":
        (inner / "unbound").mkdir(mode=0o700)
    elif mutation == "guard-source":
        rewrite(inner / "guard/ledger_source_guard.py", b"# substituted\n")
    elif mutation == "foreign-uv":
        receipt["environment"]["preparation"]["uv_path"] = "/outside/uv"
    else:
        assert mutation == "raw"
        rewrite(inner / "pytest.stdout", b"made-up PASSED\n")
    rewrite(inner / "receipt.json", original.encode(receipt))
    reseal(out, original, entry)
    with pytest.raises(ValueError):
        catalog.validate_async_receipt(repo, out, entry.proof_id)


@pytest.mark.parametrize(
    "field",
    [
        "schema",
        "inner_producer",
        "inner_schema",
        "recipe",
        "original_producer_sources",
        "producer",
        "observations",
        "scope",
        "catalogue",
    ],
)
def test_resealed_outer_identity_refuses(
    field: str, isolated: Any, positive: Path, original: Any, tmp_path: Path
) -> None:
    repo, entry = isolated
    out = tmp_path / "copy"
    shutil.copytree(positive, out)
    envelope = original.load(out / "successor.json")
    envelope[field] = "maezo-historical-unit-successor-capture/v1" if field == "schema" else {"forged": True}
    rewrite(out / "successor.json", original.encode(envelope))
    rewrite(out / "manifest.json", original.encode(original.Packet(out).hashes(exclude="manifest.json")))
    with pytest.raises(ValueError, match="binding"):
        catalog.validate_async_receipt(repo, out, entry.proof_id)


@pytest.mark.parametrize("mutation", ["missing", "corrupt", "symlink", "fifo", "hardlink"])
def test_async_module_pin_refuses_before_import(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    target = tmp_path / catalog.ASYNC_PRODUCER
    target.parent.mkdir(parents=True)
    if mutation == "corrupt":
        target.write_text("raise RuntimeError('UNREVIEWED_ASYNC_EXECUTED')\n")
    elif mutation == "symlink":
        target.symlink_to(ROOT / catalog.ASYNC_PRODUCER)
    elif mutation == "fifo":
        os.mkfifo(target)
    elif mutation == "hardlink":
        copy = tmp_path / "copy.py"
        copy.write_bytes((ROOT / catalog.ASYNC_PRODUCER).read_bytes())
        os.link(copy, target)
    monkeypatch.setattr(catalog, "ROOT", tmp_path)
    with pytest.raises((OSError, ValueError)):
        catalog.load_async_producer()


# test_explicit_cli_preflight_reads_only_closed_d7_source removed 2026-09-12 (owner decision,
# V6-Q6 D7 catalogue exclusion): it preflighted the "D7unit802" catalogue entry, whose
# source_commit (80206e29ab4b...) is train-only and never an ancestor of main. The entry
# itself was removed from scripts/ci/ledger_history_proofs.py::D7_CATALOG for the same
# reason. See that module's D7_CATALOG comment for the full rationale. Not disabled
# validation: the CLI's own ancestry refusal (run_historical_catalog_recipe.py, unedited,
# still catalogues "D7unit802" in its own SOURCE_CATALOG_V1) already fails closed with a
# non-zero exit and a clear error when the commit is not an ancestor — this removes only
# the test asserting success against a source that can never be valid on main.
