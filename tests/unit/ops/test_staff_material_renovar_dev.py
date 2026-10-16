"""Trava contra producao da renovacao delegada (N1, so dev) e a renovacao da fixture SYN."""

from __future__ import annotations

import base64
import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID
from scripts.ops.staff_material_renovar_dev import PINS_PORTAL, renovar_syn, trava_dev

_REPO_ROOT = Path(__file__).resolve().parents[3]
_RAIZ = b"\x30\x2a" + b"r" * 42
_PINS = f'  staff = {{\n    root_key_sha256 = "{hashlib.sha256(_RAIZ).hexdigest()}"\n  }}\n'
_DEV = {"scope": {"tenant": "amh", "environment": "dev", "engine_name": "default"}}


def test_dev_amh_com_a_raiz_pinada_passa() -> None:
    trava_dev(_DEV, _RAIZ, _PINS)


@pytest.mark.parametrize(
    ("designacao", "raiz", "pins"),
    [
        ({"scope": {"tenant": "amh", "environment": "prod"}}, _RAIZ, _PINS),
        ({"scope": {"tenant": "amh", "environment": "homolog"}}, _RAIZ, _PINS),
        ({"scope": {"tenant": "outro", "environment": "dev"}}, _RAIZ, _PINS),
        ({}, _RAIZ, _PINS),
        (_DEV, b"outra raiz", _PINS),
        (_DEV, _RAIZ, ""),
        (_DEV, _RAIZ, _PINS + _PINS),
    ],
)
def test_qualquer_outra_coisa_aborta(designacao: dict, raiz: bytes, pins: str) -> None:
    with pytest.raises(SystemExit):
        trava_dev(designacao, raiz, pins)


_NOVA = hashlib.sha256(_RAIZ).hexdigest()


def test_nova_raiz_aprovada_dispensa_o_pin_do_terraform() -> None:
    """DL-0113 (troca de ancora, so dev): o pin ainda e o da raiz antiga; vale o hash que o dono aprovou."""
    trava_dev(_DEV, _RAIZ, "", nova_raiz_sha256=_NOVA)
    trava_dev(_DEV, _RAIZ, _PINS.replace(_NOVA, "0" * 64), nova_raiz_sha256=_NOVA)


@pytest.mark.parametrize(
    ("designacao", "raiz", "aprovado"),
    [
        (_DEV, b"outra raiz", _NOVA),  # raiz carregada nao e a aprovada
        (_DEV, _RAIZ, "0" * 64),  # aprovacao de outra raiz
        (_DEV, _RAIZ, "zz"),  # nao e SHA-256 hex
        (_DEV, _RAIZ, _NOVA.upper()),  # hex maiusculo nao passa (formato canonico minusculo)
        ({"scope": {"tenant": "amh", "environment": "prod"}}, _RAIZ, _NOVA),  # prod nunca
        ({"scope": {"tenant": "outro", "environment": "dev"}}, _RAIZ, _NOVA),
        ({}, _RAIZ, _NOVA),
    ],
)
def test_nova_raiz_recusa_o_que_nao_e_exatamente_a_aprovada_em_dev_amh(
    designacao: dict, raiz: bytes, aprovado: str
) -> None:
    with pytest.raises(SystemExit):
        trava_dev(designacao, raiz, "", nova_raiz_sha256=aprovado)


def test_o_pin_versionado_e_lido() -> None:
    texto = (_REPO_ROOT / PINS_PORTAL).read_text(encoding="utf-8")
    raiz_de_outro = b"\x30\x2a" + b"x" * 42
    with pytest.raises(SystemExit):
        trava_dev(_DEV, raiz_de_outro, texto)


def test_syn_troca_so_o_certificado_do_auth() -> None:
    chave = Ed25519PrivateKey.generate()
    nome = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "maezo-operadora-dev:engine")])
    agora = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(nome)
        .issuer_name(nome)
        .public_key(chave.public_key())
        .serial_number(1)
        .not_valid_before(agora)
        .not_valid_after(agora + timedelta(days=14))
        .sign(chave, None)
    )
    p12 = pkcs12.serialize_key_and_certificates(
        b"auth-result", chave, cert, None, serialization.BestAvailableEncryption(b"senha")
    )
    nativo = {
        "engine-run/staff/auth-signing.p12": base64.b64encode(p12).decode(),
        "engine-run/staff/auth-signing.password": base64.b64encode(b"senha").decode(),
    }
    syn = {
        "schema": "staff-syn-runner.v1",
        "config": {"a": "b"},
        "files": {"result_certificate": "x", "native_ca": "y"},
    }
    novo = renovar_syn(syn, nativo)
    pem = cert.public_bytes(serialization.Encoding.PEM)
    assert novo["files"] == {"result_certificate": base64.b64encode(pem).decode(), "native_ca": "y"}
    assert novo["config"] == syn["config"] and syn["files"]["result_certificate"] == "x"


def test_spec_dos_segredos_devolve_as_conexoes_como_o_assemble_as_le() -> None:
    import json

    from scripts.ops.staff_material_renovar_dev import _SpecDosSegredos

    bruto = json.dumps({"session_lock": {"login": "lock", "host": "h"}, "native_witness": {"login": "wit"}})
    spec = _SpecDosSegredos(bruto.encode())
    assert spec.native_witness_connection.login == "wit"
    assert spec.session_lock_connection.validated("session-lock-ca.pem").wire() == {
        "login": "lock",
        "host": "h",
    }
