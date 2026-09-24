"""`assemble` v2 real: pacote aceito pelo loader, recusas fail-closed e CLI sem tocar a raiz."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from tools.staff_materials.__main__ import main
from tools.staff_materials.assemble import assemble, load_input
from tools.staff_materials.generate import Generated
from tools.staff_materials.secure_io import MaterialError
from tools.staff_materials.spec import load_spec
from tools.staff_materials.verify import verify_bundle

from maezo.portal.engine.profile import canonicalize, strict_loads

from .conftest import (
    approver_files,
    assemble_input,
    assemble_v2,
    bundle_bytes,
    pins_for,
    settings,
    spec_bytes,
    spec_value,
)


def _run(generated: Generated, now: datetime, *, approver=None, inputs=None, portal=None, summary=None):
    root = Ed25519PrivateKey.generate()
    return assemble(
        portal or {p.name: p.read_bytes() for p in (generated.directory / "portal").iterdir()},
        approver if approver is not None else approver_files(generated, root, now),
        summary or generated.summary,
        load_spec(spec_bytes(spec_value(now))),
        load_input(canonicalize(inputs or assemble_input(now))),
    )


def test_manifest_is_v2_with_schemas_operations_and_own_witness_key(
    generated: Generated, now: datetime
) -> None:
    assembled = assemble_v2(generated, now)
    m = assembled.manifest
    assert m["schema"] == "portal-staff-material.v2"
    assert (m["native_schema"], m["engine_schema"]) == ("maezo_native", "cibseven")
    assert m["witness_key_fingerprint"] != m["read_key_fingerprint"]
    entries = {e["role"]: e for e in strict_loads(assembled.files["designation.json"])["entries"]}
    assert entries["read_requester"]["operations"] == ["detail", "list"]
    assert entries["identity_verifier"]["key_fingerprint"] == m["witness_key_fingerprint"]
    verify_bundle(bundle_bytes(assembled), settings(pins_for(assembled)))


def test_refuses_without_approver_material(generated: Generated, now: datetime) -> None:
    with pytest.raises(MaterialError, match="aprovador"):
        _run(generated, now, approver={})


def test_refuses_foreign_designation(generated: Generated, now: datetime) -> None:
    summary = dict(generated.summary, designation_sha256="0" * 64)
    with pytest.raises(MaterialError, match="summary"):
        _run(generated, now, summary=summary)


def test_refuses_shared_witness_key(generated: Generated, now: datetime) -> None:
    keys = dict(generated.summary["key_fingerprints"])
    keys["identity_verifier"] = keys["read_requester"]
    with pytest.raises(MaterialError, match="D-H.2"):
        _run(generated, now, summary=dict(generated.summary, key_fingerprints=keys))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("native_schema", "public"),
        ("engine_schema", "maezo_native"),
        ("schema", "staff-materials-assemble.v0"),
    ],
)
def test_refuses_bad_input(generated: Generated, now: datetime, field: str, value: str) -> None:
    inputs = assemble_input(now)
    inputs[field] = value
    with pytest.raises(MaterialError):
        _run(generated, now, inputs=inputs)


def test_cli_assemble_writes_bundle_that_loader_accepts(
    tmp_path: Path, generated: Generated, now: datetime
) -> None:
    root = Ed25519PrivateKey.generate()
    approver = tmp_path / "approver"
    approver.mkdir()
    for name, data in approver_files(generated, root, now).items():
        (approver / name).write_bytes(data)
    (tmp_path / "spec.json").write_bytes(spec_bytes(spec_value(now)))
    (tmp_path / "input.json").write_bytes(canonicalize(assemble_input(now)))
    out = tmp_path / "bundle"
    argv = ["assemble", "--materials", str(generated.directory), "--spec", str(tmp_path / "spec.json")]
    argv += ["--approver", str(approver), "--input", str(tmp_path / "input.json"), "--out", str(out)]
    assert main(argv) == 0
    assert oct((out / "bundle.json").stat().st_mode & 0o777) == "0o400"
    manifest = json.loads((out / "public-manifest.json").read_bytes())
    pins = pins_for(type("A", (), {"manifest": manifest})())  # type: ignore[arg-type]
    verify_bundle((out / "bundle.json").read_bytes(), settings(pins))
    # A ferramenta nunca escreve a raiz: so os dois arquivos publicos do aprovador entram.
    assert sorted(p.name for p in approver.iterdir()) == ["installation-proof.json", "installation-root.der"]
