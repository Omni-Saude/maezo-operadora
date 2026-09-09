"""HCAP-R1/R2/R3: private tiny packets only; no historical catalog or service run."""

from __future__ import annotations

import importlib.util
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "historical_original_fixtures", Path(__file__).with_name("test_historical_unit_recipe.py")
)
base = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(base)
h = base.h
source = base.source
positive = base.positive


def copy_packet(positive, tmp_path):
    packet, receipt = positive
    out = tmp_path / "copy"
    shutil.copytree(packet, out)
    return out, receipt


def seal(out):
    # Deliberately reseal the actual mutation, so a stale manifest cannot mask it.
    files = {
        str(p.relative_to(out)): h.digest(p.read_bytes())
        for p in out.rglob("*")
        if p.is_file() and p.name != "manifest.json"
    }
    (out / "manifest.json").write_bytes(h.encode(files))


def validate(out, source):
    repo, sha = source
    return h.validate_receipt(
        out,
        repo,
        ("fixture", sha, "tests/unit/test_tiny.py", "TINY", "2026-09-08"),
        {"observation": "0" * 64},
    )


@pytest.mark.parametrize(
    "path,value",
    [
        (("coverage", "selected_count"), True),
        (("coverage", "collected_count"), True),
        (("coverage", "deselected_count"), False),
        (("coverage", "nodes", 0), True),
        (("execution", "rc"), False),
        (("execution", "quiescent"), 1),
        (("cleanup", "removed"), 1),
        (("environment", "preparation", "prepare", "rc"), False),
        (("environment", "preparation", "prepare", "quiescent"), 1),
        (("environment", "inventory", "distributions", 0, "direct_url"), False),
        (("environment", "inventory", "interpreter", "cache_tag"), True),
        (("scope", "original_observations", "observation"), 0),
    ],
)
def test_resealed_nested_copy_types(positive, source, tmp_path, path, value):
    out, _ = copy_packet(positive, tmp_path)
    receipt = h.load(out / "receipt.json")
    target = receipt
    for component in path[:-1]:
        target = target[component]
    target[path[-1]] = value
    (out / "receipt.json").write_bytes(h.encode(receipt))
    seal(out)
    with pytest.raises(h.CaptureRefusedError, match="assertions|selector|cleanup"):
        validate(out, source)


def test_resealed_unchanged_positive(positive, source, tmp_path):
    out, receipt = copy_packet(positive, tmp_path)
    seal(out)
    assert validate(out, source) == receipt
    assert receipt["scope"]["operational_validation"] == "not-performed"
    assert receipt["scope"]["current_proof_disposition"] == "unresolved"


@pytest.mark.parametrize("case", ["missing", "replaced", "extra-plugin", "extra-cache", "guard-pin"])
def test_resealed_guard_artifact_binding(positive, source, tmp_path, case):
    out, _ = copy_packet(positive, tmp_path)
    guard = out / "guard/ledger_source_guard.py"
    if case == "missing":
        guard.unlink()
    elif case == "replaced":
        guard.write_text("# replaced preserved guard artifact\n")
    elif case == "extra-plugin":
        (guard.parent / "conftest.py").write_text("# not generated\n")
    elif case == "extra-cache":
        (guard.parent / "__pycache__").mkdir(mode=0o700)
        (guard.parent / "__pycache__/ledger_source_guard.pyc").write_bytes(b"extra")
    else:
        receipt = h.load(out / "receipt.json")
        receipt["source"]["tooling"]["guard"] = "f" * 64
        (out / "receipt.json").write_bytes(h.encode(receipt))
    seal(out)
    with pytest.raises(h.CaptureRefusedError, match="guard artifact|source receipt"):
        validate(out, source)


@pytest.mark.parametrize(
    "file",
    [
        "receipt.json",
        "pytest.stdout",
        "pytest.stderr",
        "phase.json",
        "guard/ledger_source_guard.py",
        "manifest.json",
    ],
)
def test_symlink_sentinel_refused_before_any_read(positive, source, tmp_path, monkeypatch, file):
    out, _ = copy_packet(positive, tmp_path)
    sentinel = tmp_path / "owned-sentinel"
    sentinel.write_bytes((out / file).read_bytes())
    (out / file).unlink()
    (out / file).symlink_to(sentinel)
    reads = []

    def forbid_read(*args):
        reads.append(args)
        raise AssertionError("content read before metadata refusal")

    monkeypatch.setattr(os, "read", forbid_read)
    with pytest.raises(h.CaptureRefusedError, match="nonregular"):
        validate(out, source)
    assert reads == []


@pytest.mark.parametrize(
    "case", ["directory", "hardlink", "fifo", "socket", "public-file", "public-root", "oversize"]
)
def test_load_metadata_refusal_before_content(tmp_path, monkeypatch, case):
    path = tmp_path / "artifact.json"
    sock = None
    if case == "directory":
        path.mkdir(mode=0o700)
    elif case == "fifo":
        os.mkfifo(path, 0o600)
    elif case == "socket":
        sock = socket.socket(socket.AF_UNIX)
        monkeypatch.chdir(tmp_path)
        sock.bind(path.name)
    else:
        path.write_bytes(b"{}")
        if case == "hardlink":
            os.link(path, tmp_path / "linked")
        elif case == "public-file":
            path.chmod(0o644)
        elif case == "public-root":
            tmp_path.chmod(0o755)
        else:
            with path.open("ab") as stream:
                stream.truncate(h.MAX_BYTES + 1)
    reads = []
    monkeypatch.setattr(os, "read", lambda *args: reads.append(args))
    try:
        with pytest.raises(h.CaptureRefusedError):
            h.load(path)
        assert reads == []
    finally:
        if sock:
            sock.close()
        tmp_path.chmod(0o700)


def test_real_fifo_finishes_without_timeout(tmp_path):
    fifo = tmp_path / "owned.fifo"
    os.mkfifo(fifo, 0o600)
    code = (
        "import importlib.util;from pathlib import Path;"
        f"s=importlib.util.spec_from_file_location('h',{h.__file__!r});"
        "h=importlib.util.module_from_spec(s);s.loader.exec_module(h);"
        f"\ntry:h.load(Path({str(fifo)!r}))"
        "\nexcept h.CaptureRefusedError:raise SystemExit(0)"
        "\nraise SystemExit(9)"
    )
    result = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, timeout=1)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("at_root", [False, True])
def test_symlink_parent_refused_before_read(tmp_path, monkeypatch, at_root):
    real = tmp_path / "real"
    real.mkdir(mode=0o700)
    (real / "data.json").write_bytes(b"{}")
    alias = tmp_path / "alias"
    alias.symlink_to(real, target_is_directory=True)
    reads = []
    monkeypatch.setattr(os, "read", lambda *args: reads.append(args))
    with pytest.raises(h.CaptureRefusedError):
        if at_root:
            h.load(alias / "data.json")
        else:
            h.read(alias / "data.json", root=tmp_path)
    assert reads == []


@pytest.mark.parametrize("budget", ["files", "entries", "aggregate"])
def test_packet_budgets_before_read(tmp_path, monkeypatch, budget):
    if budget == "files":
        monkeypatch.setattr(h, "MAX_FILES", 2)
    elif budget == "entries":
        monkeypatch.setattr(h, "MAX_ENTRIES", 2)
    else:
        monkeypatch.setattr(h, "MAX_TOTAL_BYTES", 5)
    for i in range(3):
        (tmp_path / f"{i}.json").write_bytes(b"{}")
    reads = []
    monkeypatch.setattr(os, "read", lambda *args: reads.append(args))
    with pytest.raises(h.CaptureRefusedError, match="budget|too many"):
        h.load(tmp_path / "0.json")
    assert reads == []


@pytest.mark.parametrize("replacement", ["regular", "symlink", "fifo"])
def test_stat_open_leaf_race_refuses_before_read(tmp_path, monkeypatch, replacement):
    path = tmp_path / "data.json"
    path.write_bytes(b"{}")
    packet = h.Packet(tmp_path)
    sentinel = tmp_path / "sentinel"
    sentinel.write_bytes(b"[]")
    # The root snapshot was taken before sentinel creation; rebuild before the race.
    packet = h.Packet(tmp_path)
    original_open = os.open
    reads = []
    raced = False

    def replacing_open(name, flags, *args, **kwargs):
        nonlocal raced
        if name == "data.json" and not raced:
            raced = True
            path.unlink()
            if replacement == "regular":
                path.write_bytes(b"[]")
            elif replacement == "symlink":
                path.symlink_to(sentinel)
            else:
                os.mkfifo(path, 0o600)
        return original_open(name, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", replacing_open)
    monkeypatch.setattr(os, "read", lambda *args: reads.append(args))
    with pytest.raises(h.CaptureRefusedError):
        packet.read("data.json")
    assert raced and reads == []


def test_parent_stat_open_race(tmp_path, monkeypatch):
    directory = tmp_path / "inner"
    directory.mkdir(mode=0o700)
    (directory / "data").write_bytes(b"safe")
    outside = tmp_path / "other"
    outside.mkdir(mode=0o700)
    original_open = os.open
    raced = False
    reads = []

    def replacing_open(name, flags, *args, **kwargs):
        nonlocal raced
        if name == "inner" and not raced:
            raced = True
            directory.rename(tmp_path / "preserved-inner")
            directory.symlink_to(outside, target_is_directory=True)
        return original_open(name, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", replacing_open)
    monkeypatch.setattr(os, "read", lambda *args: reads.append(args))
    with pytest.raises(h.CaptureRefusedError):
        h.Packet(tmp_path)
    assert raced and reads == []


@pytest.mark.parametrize("growth", [False, True])
def test_after_open_file_mutation_is_refused(tmp_path, monkeypatch, growth):
    path = tmp_path / "data.json"
    path.write_bytes(b"{}")
    original_read = os.read
    mutated = False
    requested = []

    def mutate_on_read(fd, length):
        nonlocal mutated
        requested.append(length)
        if not mutated:
            mutated = True
            path.write_bytes(b"x" * 100 if growth else b"[]")
        return original_read(fd, length)

    monkeypatch.setattr(os, "read", mutate_on_read)
    with pytest.raises(h.CaptureRefusedError, match="oversized|during read"):
        h.load(path, limit=64)
    assert mutated and sum(requested) <= 130


@pytest.mark.parametrize("kind", ["python", "uv"])
def test_receipt_cannot_choose_external_identity(tmp_path, monkeypatch, kind):
    outside = tmp_path / "owned-runtime-sentinel"
    outside.write_bytes(b"do not read")
    opened = []
    monkeypatch.setattr(os, "open", lambda *args, **kwargs: opened.append(args))
    with pytest.raises(h.CaptureRefusedError, match="untrusted runtime"):
        h.runtime_digest(kind, str(outside))
    assert opened == []


@pytest.mark.parametrize("value", [1, 0, "true", None])
def test_direct_url_editable_requires_exact_bool(positive, tmp_path, value):
    _, receipt = positive
    inv = receipt["environment"]["inventory"]
    import copy

    inv = copy.deepcopy(inv)
    checkout = Path(receipt["environment"]["checkout"])
    inv["distributions"][0]["direct_url"] = {"url": checkout.as_uri(), "dir_info": {"editable": value}}
    h.write(tmp_path / "inventory.json", h.encode(inv))
    command = {"rc": 0, "status": "completed", "quiescent": True, "stdout": "inventory.json"}
    with pytest.raises(h.CaptureRefusedError, match="scalar type"):
        h.inventory(command, tmp_path, checkout)


@pytest.mark.parametrize("when", ["before", "after"])
def test_live_guard_mutation_emits_no_receipt(source, tmp_path, monkeypatch, when):
    real_capture = h.capture
    recipe_entered = []

    def changed_capture(argv, cwd, env, out, label, *args, **kwargs):
        if label == "pytest" and when == "before":
            (out / "guard/ledger_source_guard.py").write_text("# changed before spawn\n")
        result = real_capture(argv, cwd, env, out, label, *args, **kwargs)
        if label == "pytest":
            recipe_entered.append(result["rc"])
            if when == "after":
                (out / "guard/ledger_source_guard.py").write_text("# changed after execution\n")
        return result

    monkeypatch.setattr(h, "capture", changed_capture)
    repo, sha = source
    out = tmp_path / "proof"
    with pytest.raises(h.CaptureRefusedError, match="guard artifact"):
        h.capture_source(
            repo,
            sha,
            "tests/unit/test_tiny.py",
            out,
            "fixture",
            "TINY",
            "2026-09-08",
            {"observation": "0" * 64},
        )
    assert not (out / "receipt.json").exists()
    assert (out / "unresolved.json").exists()
    assert h.load(out / "checkout-cleanup.json")["removed"]
    assert recipe_entered == ([] if when == "before" else [0])


def test_json_exponent_nonfinite_refused(tmp_path):
    h.write(tmp_path / "data.json", b'{"value":1e999}')
    with pytest.raises(h.CaptureRefusedError, match="nonfinite"):
        h.load(tmp_path / "data.json")


def test_oversized_stream_refuses_before_receipt_read(positive, source, tmp_path, monkeypatch):
    out, _ = copy_packet(positive, tmp_path)
    with (out / "pytest.stdout").open("ab") as stream:
        stream.truncate(h.MAX_BYTES + 1)
    reads = []
    monkeypatch.setattr(os, "read", lambda *args: reads.append(args))
    with pytest.raises(h.CaptureRefusedError, match="oversized"):
        validate(out, source)
    assert reads == []


def test_nested_parent_replacement_during_read(tmp_path, monkeypatch):
    outer = tmp_path / "outer"
    outer.mkdir(mode=0o700)
    inner = outer / "inner"
    inner.mkdir(mode=0o700)
    (inner / "data.json").write_bytes(b"{}")
    packet = h.Packet(tmp_path)
    original_read = os.read
    raced = False

    def replace_parent(fd, length):
        nonlocal raced
        data = original_read(fd, length)
        if not raced:
            raced = True
            inner.rename(outer / "preserved-inner")
            inner.mkdir(mode=0o700)
            (inner / "data.json").write_bytes(b"[]")
        return data

    monkeypatch.setattr(os, "read", replace_parent)
    with pytest.raises(h.CaptureRefusedError, match="parent changed"):
        packet.read("outer/inner/data.json")
    assert raced
