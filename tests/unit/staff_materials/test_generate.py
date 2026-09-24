"""T1.3 `generate`: arvore, capacidades por papel, recusas do spec e DSNs que o loader aceita."""

from __future__ import annotations

import hashlib
import os
import stat
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import pkcs12
from tools.staff_materials.generate import REPO, Generated, continuity_commitment, generate
from tools.staff_materials.pki import spki_sha256
from tools.staff_materials.secure_io import MaterialError
from tools.staff_materials.spec import load_spec

from maezo.gateway.external_cases.models import digest, instant, parse
from maezo.gateway.staff_cases.authority import fingerprint
from maezo.gateway.staff_cases.materials import connection_url
from maezo.gateway.staff_cases.models import Designation, DesignationEntry

from .conftest import spec_bytes, spec_value

PORTAL = {
    "read-signing-key.pem",
    "witness-signing-key.pem",
    "read-client-key.pem",
    "read-client-certificate.pem",
    "native-ca.pem",
    "session-lock-ca.pem",
    "native-witness-ca.pem",
    "session-lock-dsn.txt",
    "native-witness-dsn.txt",
    "designation.json",
}
PRIVATE = {
    "portal/read-signing-key.pem",
    "portal/witness-signing-key.pem",
    "portal/read-client-key.pem",
    "portal/session-lock-dsn.txt",
    "portal/native-witness-dsn.txt",
    "engine/native-server-key.pem",
    "engine/native-result-signing-key.pem",
    "engine/portal-read-continuity-key.bin",
    "issuer/case-issuer-signing-key.pem",
    "issuer/issuer-witness-signing-key.pem",
    "issuer/publication-importer-signing-key.pem",
    "issuer/publication-importer-client-key.pem",
    "dba/role-verifiers.json",
}


def _tree(generated: Generated) -> set[str]:
    return {
        str(p.relative_to(generated.directory)).replace(os.sep, "/") for p in generated.directory.rglob("*")
    }


def test_tree_is_exact_and_carries_no_root_and_no_ca_private_key(generated: Generated) -> None:
    tree = _tree(generated)
    portal = {name.split("/", 1)[1] for name in tree if name.startswith("portal/")}
    # 10 dos 12 arquivos do manifesto: os 2 que faltam sao do aprovador.
    assert portal == PORTAL
    assert "installation-root.der" not in portal and "installation-proof.json" not in portal
    assert not any("installation-root" in name for name in tree)
    # As chaves das CAs nao existem em disco: os unicos PEM privados sao as folhas e os papeis.
    private_pems = {
        n for n in tree if n.endswith(".pem") and b"PRIVATE KEY" in (generated.directory / n).read_bytes()
    }
    assert private_pems == {n for n in PRIVATE if n.endswith(".pem")}
    assert set(generated.summary["pending_from_approver"]) == {
        "installation-root.der",
        "installation-proof.json",
    }


def test_private_modes(generated: Generated) -> None:
    assert stat.S_IMODE(generated.directory.stat().st_mode) == 0o700
    for name in _tree(generated):
        path = generated.directory / name
        if path.is_dir():
            assert stat.S_IMODE(path.stat().st_mode) == 0o700, name
        elif name in PRIVATE:
            assert stat.S_IMODE(path.stat().st_mode) == 0o400, name
        else:
            assert stat.S_IMODE(path.stat().st_mode) == 0o444, name


def _key(path: Path) -> Ed25519PrivateKey:
    key = serialization.load_pem_private_key(path.read_bytes(), password=None)
    assert isinstance(key, Ed25519PrivateKey)
    return key


def test_designation_draft_binds_every_generated_key_and_is_unsigned(generated: Generated) -> None:
    raw = (generated.directory / "portal/designation.json").read_bytes()
    designation = parse(Designation, raw)
    assert digest(designation.wire()) == generated.summary["designation_sha256"]
    entries: dict[str, DesignationEntry] = {e.entry_ref: e for e in designation.entries}
    on_disk = {
        "read_requester": "portal/read-signing-key.pem",
        "identity_verifier": "portal/witness-signing-key.pem",
        "native_result": "engine/native-result-signing-key.pem",
        "case_issuer": "issuer/case-issuer-signing-key.pem",
        "publication_importer": "issuer/publication-importer-signing-key.pem",
        "case-issuer-witness": "issuer/issuer-witness-signing-key.pem",
    }
    assert set(entries) == set(on_disk)
    for role, path in on_disk.items():
        assert entries[role].key_fingerprint == fingerprint(_key(generated.directory / path).public_key())
    for role, path in (
        ("read_requester", "portal/read-client-certificate.pem"),
        ("publication_importer", "issuer/publication-importer-client-certificate.pem"),
    ):
        cert = x509.load_pem_x509_certificate((generated.directory / path).read_bytes())
        assert entries[role].certificate_spki == spki_sha256(cert)
    for role in ("identity_verifier", "native_result", "case_issuer", "case-issuer-witness"):
        assert entries[role].certificate_spki is None
    # Rascunho: nenhum campo de prova/assinatura existe na designacao.
    assert b"signature" not in raw and b"proof" not in raw


def test_capabilities_follow_the_loader_and_the_onda7_restriction(generated: Generated) -> None:
    designation = parse(Designation, (generated.directory / "portal/designation.json").read_bytes())
    entries = {e.entry_ref: e for e in designation.entries}
    read = entries["read_requester"]
    # `materials.py`: operations == ("detail", "list") e as tres projecoes (D-H.4).
    assert read.operations == ("detail", "list")
    assert set(read.projections) == {"staff_summary.v1", "staff_identity.v1", "staff_current_task.v1"}
    issuer = entries["case_issuer"]
    assert "staff_current_task.v1" not in issuer.projections
    assert set(issuer.purposes) == {"staff_case_grant", "staff_policy_head", "scope_complete"}
    assert issuer.operations == ("detail", "list")
    assert entries["identity_verifier"].login_role == "portal_native_witness"
    assert entries["publication_importer"].source_ref == issuer.source_ref
    # D-H.2 (F7 do C1): o witness PROPRIO do emissor, mesma fonte do portal, chave e login dele.
    own = entries["case-issuer-witness"]
    portal = entries["identity_verifier"]
    assert own.role == "identity_verifier" and tuple(own.purposes) == ("membership_current",)
    assert own.login_role == "maezo_native_issuer_witness"
    assert (own.source_namespace, own.source_ref) == (portal.source_namespace, portal.source_ref)
    assert own.key_fingerprint != portal.key_fingerprint
    assert generated.summary["key_fingerprints"]["case_issuer_witness"] == own.key_fingerprint


def test_membership_source_is_a_separator_terminated_prefix(now: datetime) -> None:
    """F9 do C1: a membership publicada e `prefixo + principal_ref`; a designacao guarda o prefixo."""
    value = spec_value(now)
    value["identity_verifier"]["source_ref"] = "portal-identity:amh"
    with pytest.raises(MaterialError, match="prefixo"):
        load_spec(spec_bytes(value))


def test_issuer_witness_login_cannot_repeat_another_role(now: datetime) -> None:
    value = spec_value(now)
    value["native_result"]["login_role"] = "maezo_native_issuer_witness"
    with pytest.raises(MaterialError, match="login proprio"):
        load_spec(spec_bytes(value))


def test_dsns_are_accepted_by_the_loader_codec_and_carry_the_scram_password(
    generated: Generated, now: datetime
) -> None:
    from maezo.gateway.staff_cases.production_config import Connection

    spec = spec_value(now)
    cas: tuple[tuple[str, Literal["session-lock-ca.pem", "native-witness-ca.pem"]], ...] = (
        ("session_lock", "session-lock-ca.pem"),
        ("native_witness", "native-witness-ca.pem"),
    )
    for name, ca in cas:
        connection = spec[f"{name}_connection"]
        raw = (generated.directory / f"portal/{name.replace('_', '-')}-dsn.txt").read_bytes()
        url = connection_url(
            raw, Connection(**connection, tls_server_name=connection["host"], ca_file=ca, function_pin=None)
        )
        assert url.password and len(url.password) >= 40
    verifiers = (generated.directory / "dba/role-verifiers.json").read_bytes()
    assert b"SCRAM-SHA-256$4096:" in verifiers
    for name in ("session-lock", "native-witness"):
        password = connection_url_password(generated, name)
        assert password.encode() not in verifiers


def connection_url_password(generated: Generated, name: str) -> str:
    from sqlalchemy.engine import make_url

    return make_url((generated.directory / f"portal/{name}-dsn.txt").read_text()).password or ""


def test_client_truststore_is_a_passwordless_pkcs12_with_only_the_client_ca(generated: Generated) -> None:
    raw = (generated.directory / "engine/client-ca.p12").read_bytes()
    loaded = pkcs12.load_pkcs12(raw, None)  # sem senha: abre com None
    assert loaded.key is None and loaded.cert is None
    (only,) = loaded.additional_certs
    client_ca = x509.load_pem_x509_certificate(
        (generated.directory / "engine/native-client-ca.pem").read_bytes()
    )
    server_ca = x509.load_pem_x509_certificate((generated.directory / "portal/native-ca.pem").read_bytes())
    assert only.certificate == client_ca and only.certificate != server_ca
    assert only.friendly_name == b"maezo-native-client-ca"
    assert b"PRIVATE" not in raw
    der = client_ca.public_bytes(serialization.Encoding.DER)
    assert generated.summary["native_client_ca_der_sha256"] == hashlib.sha256(der).hexdigest()


def test_non_posix_host_is_refused_before_anything_is_written(
    now: datetime, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tools.staff_materials import secure_io

    spec = load_spec(spec_bytes(spec_value(now)))
    monkeypatch.setattr(secure_io.os, "name", "nt")
    with pytest.raises(MaterialError, match="POSIX"):
        generate(spec, tmp_path / "out")
    with pytest.raises(MaterialError, match="POSIX"):
        secure_io.write_new(tmp_path / "x", b"segredo", 0o400)
    monkeypatch.undo()
    assert not (tmp_path / "out").exists() and not (tmp_path / "x").exists()


def test_continuity_key_and_commitment(generated: Generated) -> None:
    key = (generated.directory / "engine/portal-read-continuity-key.bin").read_bytes()
    assert len(key) == 32
    assert generated.summary["continuity"]["commitment"] == continuity_commitment(key)
    assert key.hex() not in str(generated.summary)


def test_certificates_chain_to_their_own_ca_only(generated: Generated) -> None:
    def load(name: str) -> x509.Certificate:
        return x509.load_pem_x509_certificate((generated.directory / name).read_bytes())

    server_ca, client_ca = load("portal/native-ca.pem"), load("engine/native-client-ca.pem")
    server = load("engine/native-server-certificate.pem")
    server.verify_directly_issued_by(server_ca)
    for name in ("portal/read-client-certificate.pem", "issuer/publication-importer-client-certificate.pem"):
        client = load(name)
        client.verify_directly_issued_by(client_ca)
        with pytest.raises(ValueError):
            client.verify_directly_issued_by(server_ca)
    san = server.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert san.get_values_for_type(x509.DNSName) == [generated.summary["native_hostname"]]
    assert generated.summary["native_server_spki_sha256"] == spki_sha256(server)


@pytest.mark.parametrize(
    "mutation",
    [
        "window_15_days",
        "witness_login",
        "importer_source",
        "duplicate_login",
        "hostname",
        "cert_shorter_than_designation",
        "succession",
        "json_number",
    ],
)
def test_incoherent_spec_is_refused_before_any_key(mutation: str, now: datetime, tmp_path: Path) -> None:
    value = spec_value(now)
    if mutation == "window_15_days":
        value["designation"]["valid_until"] = instant(now + timedelta(days=15))
        value["certificate_not_after"] = instant(now + timedelta(days=20))
    elif mutation == "witness_login":
        value["identity_verifier"]["login_role"] = "outro_login"
    elif mutation == "importer_source":
        value["publication_importer"]["source_ref"] = "outra-fonte"
    elif mutation == "duplicate_login":
        value["case_issuer"]["login_role"] = value["read_requester"]["login_role"]
    elif mutation == "hostname":
        value["native_hostname"] = "https://engine"
    elif mutation == "cert_shorter_than_designation":
        value["certificate_not_after"] = instant(now + timedelta(days=2))
    elif mutation == "succession":
        value["designation"]["designation_revision"] = "3"
    raw = spec_bytes(value)
    if mutation == "json_number":
        raw = raw.replace(b'"port":"5432"', b'"port":5432', 1)
    with pytest.raises(MaterialError):
        generate(load_spec(raw), tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_output_must_be_new_and_outside_the_repository(now: datetime, tmp_path: Path) -> None:
    spec = load_spec(spec_bytes(spec_value(now)))
    existing = tmp_path / "existing"
    existing.mkdir()
    with pytest.raises(MaterialError, match="NOVO"):
        generate(spec, existing)
    with pytest.raises(MaterialError, match="repositorio"):
        generate(spec, REPO / "tmp-materials-should-never-exist")
    assert not (REPO / "tmp-materials-should-never-exist").exists()
    with pytest.raises(MaterialError, match="absoluto"):
        generate(spec, Path("relative-out"))
