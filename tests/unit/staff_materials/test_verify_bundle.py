"""T1.3 `verify`: o pacote e julgado pelo loader do portal (`decode_bundle`), com pins do aprovador.

Pronto quando (plano, T1.3): `verify` recusa pacote com 1 byte trocado, pin trocado e designacao
sem assinatura da raiz. A raiz e gerada aqui, no teste.
"""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from tools.staff_materials.__main__ import main
from tools.staff_materials.generate import Generated
from tools.staff_materials.secure_io import MaterialError
from tools.staff_materials.verify import load_pins, manifest_digest, verify_bundle

from maezo.gateway.external_cases.models import digest
from maezo.portal.engine.profile import canonicalize

from .conftest import Assembled, assemble_v1, bundle_bytes, pins_for, settings


def test_generated_and_approver_signed_bundle_is_accepted_by_the_loader(
    generated: Generated, now: datetime
) -> None:
    assembled = assemble_v1(generated, now)
    manifest = verify_bundle(bundle_bytes(assembled), settings(pins_for(assembled)))
    assert manifest.designation_digest == generated.summary["designation_sha256"]
    assert manifest_digest(canonicalize(assembled.manifest)) == digest(assembled.manifest)


def test_control_repinning_an_untouched_manifest_still_loads(generated: Generated, now: datetime) -> None:
    """Controle do teste abaixo: re-pinar sem adulterar nada continua passando."""
    assembled = assemble_v1(generated, now)
    for name, value in list(assembled.manifest["files"].items()):
        if value is not None:
            assembled.manifest["files"][name] = hashlib.sha256(assembled.files[name]).hexdigest()
    pins = pins_for(assembled)
    pins["staff_public_manifest_sha256"] = digest(assembled.manifest)
    verify_bundle(bundle_bytes(assembled), settings(pins))


def _flip(data: bytes, index: int = -40) -> bytes:
    raw = bytearray(data)
    raw[index] ^= 0x01
    return bytes(raw)


@pytest.mark.parametrize(
    "mutation",
    [
        "public_byte",
        "designation_byte",
        "private_key_byte",
        "certificate_byte",
        "dsn_swapped",
        "root_pin",
        "server_spki_pin",
        "manifest_pin",
        "read_key_pin",
        "unsigned_designation",
        "signed_by_other_root",
        "signed_by_role_key",
    ],
)
def test_loader_refuses_every_tampering(mutation: str, generated: Generated, now: datetime) -> None:
    assembled: Assembled = assemble_v1(generated, now)
    pins: dict[str, Any] = pins_for(assembled)
    files = assembled.files
    if mutation == "public_byte":
        files["native-ca.pem"] = _flip(files["native-ca.pem"])
    elif mutation == "designation_byte":
        files["designation.json"] = files["designation.json"].replace(
            b'"state":"active"', b'"state":"revoked"'
        )
    elif mutation == "private_key_byte":
        files["read-signing-key.pem"] = _flip(files["read-signing-key.pem"], 40)
    elif mutation == "certificate_byte":
        files["read-client-certificate.pem"] = _flip(files["read-client-certificate.pem"], 100)
    elif mutation == "dsn_swapped":
        files["native-witness-dsn.txt"], files["session-lock-dsn.txt"] = (
            files["session-lock-dsn.txt"],
            files["native-witness-dsn.txt"],
        )
    elif mutation == "root_pin":
        pins["staff_root_key_sha256"] = "0" * 64
    elif mutation == "server_spki_pin":
        pins["staff_native_server_spki_sha256"] = "1" * 64
    elif mutation == "manifest_pin":
        pins["staff_public_manifest_sha256"] = "2" * 64
    elif mutation == "read_key_pin":
        pins["staff_read_key_sha256"] = "3" * 64
    elif mutation in {"unsigned_designation", "signed_by_other_root", "signed_by_role_key"}:
        proof = json.loads(files["installation-proof.json"])
        signer = {
            "unsigned_designation": None,
            "signed_by_other_root": Ed25519PrivateKey.generate(),
            "signed_by_role_key": serialization.load_pem_private_key(
                files["read-signing-key.pem"], password=None
            ),
        }[mutation]
        proof.pop("signature")
        if signer is None:
            proof["signature"] = base64.b64encode(b"\x00" * 64).decode()
        else:
            assert isinstance(signer, Ed25519PrivateKey)
            proof["signature"] = base64.b64encode(signer.sign(canonicalize(proof))).decode()
        files["installation-proof.json"] = canonicalize(proof)
    if mutation not in {"public_byte", "manifest_pin"}:
        # Pior caso: o atacante reescreve o manifesto E o aprovador re-pinou o manifesto novo.
        # Sobram os pins independentes (raiz, designacao, chaves, SPKI) e a assinatura da raiz:
        # e cada um deles que tem de recusar, nao o SHA do manifesto.
        for name, value in list(assembled.manifest["files"].items()):
            if value is not None:
                assembled.manifest["files"][name] = hashlib.sha256(files[name]).hexdigest()
        if mutation != "manifest_pin":
            pins["staff_public_manifest_sha256"] = digest(assembled.manifest)
    with pytest.raises(MaterialError, match="decode_bundle"):
        verify_bundle(bundle_bytes(assembled), settings(pins))


def test_cli_verify_uses_the_approver_pins_file_and_refuses_a_dirty_environment(
    generated: Generated,
    now: datetime,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assembled = assemble_v1(generated, now)
    (tmp_path / "bundle.json").write_bytes(bundle_bytes(assembled))
    (tmp_path / "pins.json").write_text(json.dumps(pins_for(assembled)))
    (tmp_path / "manifest.json").write_bytes(canonicalize(assembled.manifest))
    assert (
        main(["verify", "--bundle", str(tmp_path / "bundle.json"), "--pins", str(tmp_path / "pins.json")])
        == 0
    )
    assert main(["verify", "--manifest", str(tmp_path / "manifest.json"), "--print-manifest-digest"]) == 0
    assert f"public_manifest_sha256={digest(assembled.manifest)}" in capsys.readouterr().out
    monkeypatch.setenv("MAEZO_PORTAL_DIRECT_COMPLETION", "true")
    with pytest.raises(MaterialError, match="MAEZO_PORTAL_"):
        load_pins((tmp_path / "pins.json").read_bytes())
    assert (
        main(["verify", "--bundle", str(tmp_path / "bundle.json"), "--pins", str(tmp_path / "pins.json")])
        == 1
    )


def test_pins_file_with_duplicate_field_is_refused(generated: Generated, now: datetime) -> None:
    raw = json.dumps(pins_for(assemble_v1(generated, now)))
    duplicated = raw[:-1] + ', "tenant": "outro"}'
    with pytest.raises(MaterialError, match="repetido"):
        load_pins(duplicated.encode())
