"""`human-bundle` (D6 do C1): o pacote `portal-human-material.v1` do job, conferido pelo loader do job.

A raiz que assina a admissao humana nasce aqui, no teste (regra D-F): a ferramenta so recebe o
SPKI e o documento ja assinado.
"""

from __future__ import annotations

import base64
import json
import stat
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import pkcs12
from tools.staff_materials import human_bundle
from tools.staff_materials.__main__ import main
from tools.staff_materials.generate import Generated
from tools.staff_materials.native_secret import build, load_input
from tools.staff_materials.secure_io import MaterialError

from maezo.gateway.external_cases.models import instant
from maezo.gateway.human.production_materials import FILES, HumanMaterialError
from maezo.gateway.human.read_credentials import ReadAdmission
from maezo.gateway.human.read_profile import parse_model, wire
from maezo.portal.engine.profile import canonicalize, strict_loads

from .test_native_secret import native_input

SCOPE = dict(tenant="amh", environment="dev", workload_ref="portal-staff")
ORIGIN = "https://engine-native.maezo-operadora-dev.internal"
READ_AUDIENCE = "engine-native.maezo-operadora-dev.internal"


def spec_value(now: datetime, **changes: Any) -> dict[str, Any]:
    connection = dict(host="db.example.internal", port="5432", database="maezo")
    value: dict[str, Any] = dict(
        schema="staff-materials-human-bundle.v1",
        scope=SCOPE,
        engine_name="default",
        database_incarnation="inc-test-1",
        issuer="https://cognito-idp.sa-east-1.amazonaws.com/sa-east-1_test",
        issued_at=instant(now - timedelta(minutes=5)),
        valid_until=instant(now + timedelta(days=5)),
        origin=ORIGIN,
        catalog_ref="catalog-staff",
        material_version_prefix="amhdev-human-",
        certificate_label="amh dev",
        read_key=dict(key_id="amh-dev-read-20260924", audience=READ_AUDIENCE),
        assignment_key=dict(key_id="amh-dev-assignment-20260924", audience="engine-human-assignment"),
        command_key=dict(key_id="amh-dev-command-20260924", audience="engine-human-command"),
        max_envelope_seconds="30",
        timeout_seconds="10",
        outbox_connection=dict(connection, login="human_outbox"),
        source_connection=dict(connection, login="human_source"),
        relay_lease_seconds="30",
        relay_retry_seconds="1",
        relay_poll_seconds="1",
        revocation=dict(source_ref="revocations", revision="1"),
    )
    value.update(changes)
    return value


def signed_admission(root: Ed25519PrivateKey, now: datetime) -> bytes:
    record = parse_model(
        ReadAdmission,
        dict(
            scope=SCOPE,
            engine_name="default",
            database_incarnation="inc-test-1",
            read_deployment_ref="read-release-1",
            read_deployment_digest="a" * 64,
            runtime_admission_generation="1",
            capability_digest="b" * 64,
            observed_at=instant(now - timedelta(minutes=5)),
            valid_until=instant(now + timedelta(days=4)),
            provider_ref="admission-amh",
            provider_revision="1",
        ),
    )
    return json.dumps(
        {
            "schema": "portal-human-read-admission.v1",
            "record": wire(record),
            "signature": base64.b64encode(root.sign(canonicalize(wire(record)))).decode("ascii"),
        }
    ).encode()


@pytest.fixture
def spec_file(tmp_path: Path, now: datetime) -> Path:
    path = tmp_path / "human-spec.json"
    path.write_bytes(canonicalize(spec_value(now)))
    return path


@pytest.fixture
def keys_dir(tmp_path: Path, spec_file: Path) -> Path:
    assert (
        main(["human-bundle", "keys", "--spec", str(spec_file), "--out", str(tmp_path / "human-keys")]) == 0
    )
    return tmp_path / "human-keys"


def _inputs(tmp_path: Path, now: datetime) -> tuple[Path, Path]:
    approver, dsns = tmp_path / "approver", tmp_path / "dsns"
    approver.mkdir()
    dsns.mkdir()
    root = Ed25519PrivateKey.generate()
    (approver / "installation-root.der").write_bytes(human_bundle.spki(root))
    (approver / "human-read-admission.json").write_bytes(signed_admission(root, now))
    (dsns / "outbox-dsn.txt").write_bytes(
        b"postgresql+asyncpg://human_outbox:pw@db.example.internal:5432/maezo"
    )
    (dsns / "source-dsn.txt").write_bytes(
        b"postgresql+asyncpg://human_source:pw@db.example.internal:5432/maezo"
    )
    return approver, dsns


def _package(tmp_path: Path, spec_file: Path, keys_dir: Path, generated: Generated, now: datetime) -> int:
    approver, dsns = _inputs(tmp_path, now)
    return main(
        [
            "human-bundle",
            "package",
            "--spec",
            str(spec_file),
            "--keys",
            str(keys_dir),
            "--materials",
            str(generated.directory),
            "--approver",
            str(approver),
            "--dsns",
            str(dsns),
            "--out",
            str(tmp_path / "human-package"),
        ]
    )


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_keys_writes_private_0400_public_0444_and_a_summary_without_secrets(keys_dir: Path) -> None:
    assert _mode(keys_dir) == 0o700
    assert {p.name for p in (keys_dir / "private").iterdir()} == set(human_bundle.KEYS_PRIVATE)
    assert all(_mode(p) == 0o400 for p in (keys_dir / "private").iterdir())
    assert {p.name for p in (keys_dir / "public").iterdir()} == {
        *human_bundle.KEYS_PUBLIC,
        human_bundle.SUMMARY,
    }
    assert all(_mode(p) == 0o444 for p in (keys_dir / "public").iterdir())
    summary_raw = (keys_dir / "public" / human_bundle.SUMMARY).read_bytes()
    summary = strict_loads(summary_raw)
    assert summary["schema"] == "staff-materials-human-keys.v1"
    assert summary["keys"]["portal-task-read"]["key_id"] == "amh-dev-read-20260924"
    assert summary["keys"]["human-command"]["key_id"] == "amh-dev-command-20260924"
    assert summary["keys"]["human-command"]["workload_ref"] == SCOPE["workload_ref"]
    assert b"PRIVATE" not in summary_raw
    for private in (keys_dir / "private").iterdir():
        assert private.read_bytes() not in summary_raw


def test_keys_refuses_an_existing_output_directory(tmp_path: Path, spec_file: Path) -> None:
    out = tmp_path / "exists"
    out.mkdir()
    assert main(["human-bundle", "keys", "--spec", str(spec_file), "--out", str(out)]) == 1
    assert list(out.iterdir()) == []


def test_package_is_accepted_by_the_job_loader_with_exact_format_and_modes(
    tmp_path: Path,
    spec_file: Path,
    keys_dir: Path,
    generated: Generated,
    now: datetime,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert _package(tmp_path, spec_file, keys_dir, generated, now) == 0
    out = tmp_path / "human-package"
    current = out / "current"
    assert _mode(out) == 0o700 and _mode(current) == 0o500
    assert {p.name for p in current.iterdir()} == FILES | {"manifest.json"}
    assert all(_mode(p) == 0o400 for p in current.iterdir())
    manifest, files = human_bundle.load_package(current)
    human_bundle.verify(manifest, files, now=now)  # o verify_materials do job
    assert manifest.material_version_id.startswith("amhdev-human-")
    assert {k.purpose: k.key_id for k in manifest.keys}["portal-task-read"] == "amh-dev-read-20260924"
    assert manifest.read_surface.server_spki_sha256 == generated.summary["native_server_spki_sha256"]
    assert files["read-ca.pem"] == (generated.directory / "portal" / "native-ca.pem").read_bytes()
    pins = strict_loads((out / "public" / "human-bundle-pins.json").read_bytes())
    printed = capsys.readouterr().out
    assert f"MAEZO_HUMAN_MATERIAL_VERSION_ID={pins['material_version_id']}" in printed
    assert f"MAEZO_HUMAN_PUBLIC_MANIFEST_SHA256={pins['public_manifest_sha256']}" in printed


@pytest.mark.parametrize(
    "name", ["read-admission.json", "read-client-certificate.pem", "command-signing-key.pem"]
)
def test_one_byte_tamper_is_refused_by_the_loader(
    tmp_path: Path, spec_file: Path, keys_dir: Path, generated: Generated, now: datetime, name: str
) -> None:
    assert _package(tmp_path, spec_file, keys_dir, generated, now) == 0
    manifest, files = human_bundle.load_package(tmp_path / "human-package" / "current")
    raw = bytearray(files[name])
    raw[len(raw) // 2] ^= 0x01
    with pytest.raises(HumanMaterialError):
        human_bundle.verify(manifest, {**files, name: bytes(raw)}, now=now)


def test_package_refuses_an_admission_not_signed_by_the_given_root(
    tmp_path: Path, spec_file: Path, keys_dir: Path, generated: Generated, now: datetime
) -> None:
    approver, dsns = _inputs(tmp_path, now)
    (approver / "installation-root.der").chmod(0o644)
    (approver / "installation-root.der").write_bytes(human_bundle.spki(Ed25519PrivateKey.generate()))
    code = main(
        [
            "human-bundle",
            "package",
            "--spec",
            str(spec_file),
            "--keys",
            str(keys_dir),
            "--materials",
            str(generated.directory),
            "--approver",
            str(approver),
            "--dsns",
            str(dsns),
            "--out",
            str(tmp_path / "human-package"),
        ]
    )
    assert code == 1
    assert not (tmp_path / "human-package").exists()


def test_package_refuses_keys_from_another_spec(
    tmp_path: Path, keys_dir: Path, generated: Generated, now: datetime
) -> None:
    other = tmp_path / "other-spec.json"
    other.write_bytes(
        canonicalize(spec_value(now, catalog_ref="other", read_key=dict(key_id="x", audience="y")))
    )
    assert _package(tmp_path, other, keys_dir, generated, now) == 1


def test_spec_refuses_shared_key_ids_and_long_windows(now: datetime) -> None:
    same = dict(key_id="k", audience="a")
    with pytest.raises(MaterialError):
        human_bundle.load_spec(canonicalize(spec_value(now, read_key=same, command_key=same)))
    with pytest.raises(MaterialError):
        human_bundle.load_spec(canonicalize(spec_value(now, valid_until=instant(now + timedelta(days=15)))))
    # A audience D-L em forma de URL nao e um OpaqueRef: o loader do job a recusaria.
    url = dict(key_id="amh-dev-read-20260924", audience=ORIGIN)
    with pytest.raises(MaterialError, match="OpaqueRef"):
        human_bundle.load_spec(canonicalize(spec_value(now, read_key=url)))


def test_native_secret_takes_the_read_and_command_keys_from_the_summary(
    keys_dir: Path, generated: Generated, now: datetime
) -> None:
    summary = strict_loads((keys_dir / "public" / human_bundle.SUMMARY).read_bytes())
    value = native_input(now)
    del value["read_trust"]["portal_read_key"], value["human_trust"]["command_key"]
    files, _ = build(
        generated.directory,
        human_bundle.spki(Ed25519PrivateKey.generate()),
        load_input(canonicalize(value), summary),
        human_client_ca=summary["client_ca_pem"].encode("ascii"),
    )
    trust = {k["purpose"]: k for k in strict_loads(files["engine-run/portal-read-trust.json"])["public_keys"]}
    read = trust["portal-task-read"]
    assert read["key_id"] == "amh-dev-read-20260924"
    assert read["public_key_spki_base64"] == summary["keys"]["portal-task-read"]["public_key_spki_base64"]
    assert read["peer_spki_sha256"] == summary["peers"]["read"]
    human = {k["purpose"]: k for k in strict_loads(files["engine-run/trust.json"])["keys"]}
    assert human["human-command"]["id"] == "amh-dev-command-20260924"
    assert human["human-command"]["peer_spki_sha256"] == summary["peers"]["command"]
    store = pkcs12.load_pkcs12(files["engine-native/client-ca.p12"], None)
    assert len(store.additional_certs) == 2


def test_native_secret_refuses_keys_given_twice(keys_dir: Path, now: datetime) -> None:
    summary = strict_loads((keys_dir / "public" / human_bundle.SUMMARY).read_bytes())
    with pytest.raises(MaterialError):
        load_input(canonicalize(native_input(now)), summary)
