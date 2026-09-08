"""V-CIG-08: coleta pública minimizada e expected original em custódia local."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from tests.unit.ci.test_pytest_output_privacy import WRAPPER, invoke, prepare

SECRET = "private collection detail 815693"


def subject(root: Path, origin: str = "parameter", secret: str = SECRET) -> None:
    decorator = '@pytest.mark.parametrize("value",[VALUE],ids=[VALUE])\n' if origin == "parameter" else ""
    params = "value, record_property" if origin == "parameter" else "record_property"
    prepare(
        root,
        f"import pytest\nfrom pathlib import Path\nVALUE={secret!r}\n"
        + decorator
        + "@pytest.mark.xfail(strict=True,reason=VALUE)\n"
        + f"def test_subject({params}):\n"
        + "    Path('body-observed').touch()\n"
        + "    record_property('build_label','useful public diagnostic')\n"
        + ("    record_property('password',VALUE)\n" if origin == "property" else "")
        + "    assert 1 == 0\n",
    )


def private_collection(root: Path) -> tuple[dict[str, Any], Path]:
    public = json.loads((root / "collection.json").read_text())
    return public, root / public["private_collection"]["directory"]


@pytest.mark.parametrize("origin", ["parameter", "property"])
@pytest.mark.parametrize(
    "secret", [SECRET, 'private "quote" \\ end\nálpha', "1"], ids=["literal", "escaped", "short"]
)
def test_collection_and_execution_keep_originals_private_and_allow_rerun(
    tmp_path: Path, origin: str, secret: str
) -> None:
    subject(tmp_path, origin, secret)
    collected = invoke(tmp_path, "collect")
    assert collected.returncode == 0
    assert not (tmp_path / "body-observed").exists()
    public, directory = private_collection(tmp_path)
    original_bytes = (directory / "collection.json").read_bytes()
    original = json.loads(original_bytes)
    assert directory.stat().st_mode & 0o777 == 0o700
    for name in ["collection.json", "binding.json", "output.log"]:
        assert (directory / name).stat().st_mode & 0o777 == 0o600
    assert original["collection"][0]["xfail"]["reason"] == secret
    assert public["collection"][0]["xfail"]["reason"] != secret
    assert hashlib.sha256(original_bytes).hexdigest() == public["private_collection"]["sha256"]
    for _ in range(2):
        result = invoke(tmp_path, "run")
        assert result.returncode == 0
        assert (tmp_path / "body-observed").exists()
        report = json.loads((tmp_path / "validation.json").read_text())
        execution = json.loads((tmp_path / "execution.json").read_text())
        assert report["case_counts"] == {"xfailed_executed": 1}
        assert execution["collection"] == public["collection"]
        assert execution["reports"][1]["body_entered"] is True
        assert original_bytes == (directory / "collection.json").read_bytes()
        assert (
            execution["collection"][0]["xfail"]["reason_sha256"]
            == original["collection"][0]["xfail"]["reason_sha256"]
        )
        text = "".join(
            (tmp_path / name).read_text()
            for name in ["collection.json", "execution.json", "result.xml", "validation.json"]
        )
        if secret != "1":
            assert all(
                form not in text + result.stdout + result.stderr + collected.stdout + collected.stderr
                for form in {secret, repr(secret)[1:-1], json.dumps(secret)[1:-1]}
            )
        assert "useful public diagnostic" in (tmp_path / "result.xml").read_text()


@pytest.mark.parametrize(
    "fault",
    [
        "reference-missing",
        "reference-shape",
        "traversal",
        "digest",
        "raw-bytes",
        "raw-malformed",
        "raw-missing",
        "binding-missing",
        "binding-malformed",
        "root",
        "manifest",
        "instance",
        "projection",
        "directory-mode",
        "raw-mode",
        "binding-mode",
        "directory-symlink",
        "raw-symlink",
        "binding-symlink",
        "raw-fifo",
    ],
)
def test_custody_tampering_refuses_before_body_and_fresh_collection_recovers(
    tmp_path: Path, fault: str
) -> None:
    subject(tmp_path)
    assert invoke(tmp_path, "collect").returncode == 0
    public, directory = private_collection(tmp_path)
    raw = directory / "collection.json"
    binding_path = directory / "binding.json"
    binding = json.loads(binding_path.read_text())
    if fault == "reference-missing":
        public.pop("private_collection")
    elif fault == "reference-shape":
        public["private_collection"]["unexpected"] = "value"
    elif fault == "traversal":
        public["private_collection"]["directory"] = "../" + directory.name
    elif fault == "digest":
        public["private_collection"]["sha256"] = "0" * 64
    elif fault == "raw-bytes":
        raw.write_bytes(raw.read_bytes() + b" ")
    elif fault == "raw-malformed":
        raw.write_text("{")
    elif fault in {"raw-missing", "binding-missing"}:
        (raw if fault == "raw-missing" else binding_path).unlink()
    elif fault == "binding-malformed":
        binding_path.write_text("{")
    elif fault in {"root", "manifest", "instance"}:
        binding[{"root": "root", "manifest": "manifest", "instance": "directory"}[fault]] += "different"
        binding_path.write_text(json.dumps(binding))
    elif fault == "projection":
        public["collection"][0]["xfail"]["strict"] = False
    elif fault.endswith("-mode"):
        {"directory-mode": directory, "raw-mode": raw, "binding-mode": binding_path}[fault].chmod(
            0o755 if fault == "directory-mode" else 0o644
        )
    elif fault.endswith("-symlink"):
        path = {"directory-symlink": directory, "raw-symlink": raw, "binding-symlink": binding_path}[fault]
        target = path.with_name(path.name + "-target")
        path.rename(target)
        path.symlink_to(target)
    elif fault == "raw-fifo":
        raw.unlink()
        os.mkfifo(raw, 0o600)
    (tmp_path / "collection.json").write_text(json.dumps(public))
    result = invoke(tmp_path, "run")
    assert result.returncode != 0
    assert not (tmp_path / "body-observed").exists()
    assert SECRET not in result.stdout + result.stderr
    assert not (tmp_path / "execution.json").exists()
    assert invoke(tmp_path, "collect").returncode == 0
    assert invoke(tmp_path, "run").returncode == 0


def test_cross_root_reference_and_same_root_other_manifest_are_not_authority(tmp_path: Path) -> None:
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    for root in (first, second):
        subject(root)
        assert invoke(root, "collect").returncode == 0
    public, directory = private_collection(first)
    copied = second / directory.name
    import shutil

    shutil.copytree(directory, copied)
    (second / "collection.json").write_text(json.dumps(public))
    assert invoke(second, "run").returncode != 0
    assert not (second / "body-observed").exists()
    (first / "renamed.json").write_bytes((first / "collection.json").read_bytes())
    driver = (
        f"import sys;sys.path.insert(0,{str(WRAPPER.parent)!r});"
        "import run_live_pytest as w;from pathlib import Path;"
        "w._expected_original(Path('renamed.json'),Path('.'))"
    )
    result = subprocess.run([sys.executable, "-c", driver], cwd=first, capture_output=True, timeout=30)
    assert result.returncode != 0


@pytest.mark.parametrize("fault", ["binding", "projection", "replace"])
def test_collection_publication_failure_preserves_private_evidence_and_removes_stale(
    tmp_path: Path, fault: str
) -> None:
    subject(tmp_path)
    assert invoke(tmp_path, "collect").returncode == 0
    driver = f"""
import sys
from pathlib import Path
sys.path.insert(0,{str(WRAPPER.parent)!r})
import run_live_pytest as w
def fail(*args,**kwargs): raise OSError({SECRET!r})
if {fault!r}=='binding': w._private_json_write=fail
elif {fault!r}=='projection': w._collection_projection=fail
else: Path.replace=fail
raise SystemExit(w.main(['--root','.', 'collect','--output','collection.json',
    '--','test_probe.py','-q','-c','pytest.ini']))
"""
    result = subprocess.run(
        [sys.executable, "-c", driver], cwd=tmp_path, capture_output=True, text=True, timeout=30
    )
    assert result.returncode != 0
    assert SECRET not in result.stdout + result.stderr
    assert not (tmp_path / "collection.json").exists()
    assert not (tmp_path / ".collection.json.tmp").exists()
    assert list(tmp_path.glob(".collection.json.pytest-*/collection.json"))
    assert not (tmp_path / "body-observed").exists()
    assert invoke(tmp_path, "collect").returncode == 0
    assert invoke(tmp_path, "run").returncode == 0
