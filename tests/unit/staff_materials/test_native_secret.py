"""T1.3/Onda 4: `native-secret` monta o segredo `engine/native-materials` da saida do `generate` (F7 do C1).

Confere o que o engine exige dos arquivos que ele le (`PortalReadTrust`, `human-trust.v1`,
`portal-read-provider.v1`, `staff-deployment-composition.v1`): uma chave, um `key_id` e um peer mTLS
por proposito Q2 (F8), a chave do job amarrada ao certificado de cliente DELE, e o digest da
configuracao nativa recalculado do mesmo jeito que `StaffCaseInstallation.Configuration.digest()`.
"""

from __future__ import annotations

import base64
import hashlib
import stat
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat, pkcs12
from tools.staff_materials.__main__ import main
from tools.staff_materials.generate import Generated
from tools.staff_materials.native_secret import PRIVATE_OUTPUT, build, load_input
from tools.staff_materials.secure_io import MaterialError

from maezo.gateway.external_cases.models import instant
from maezo.portal.engine.profile import canonicalize, strict_loads


def _spki(key: Ed25519PrivateKey) -> bytes:
    return key.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)


def _public(key_id: str, workload: str, key: Ed25519PrivateKey, peer: str) -> dict[str, str]:
    return dict(
        key_id=key_id,
        workload_ref=workload,
        public_key_spki_base64=base64.b64encode(_spki(key)).decode(),
        peer_spki_sha256=peer,
    )


def native_input(now: datetime, **changes: Any) -> dict[str, Any]:
    value: dict[str, Any] = dict(
        schema="staff-materials-native-secret.v1",
        scope=dict(tenant="amh", environment="dev", workload_ref="portal-staff"),
        engine_name="default",
        database_incarnation="inc-test-1",
        not_before=instant(now - timedelta(minutes=5)),
        not_after=instant(now + timedelta(days=5)),
        read_trust=dict(
            audience="engine-read",
            read_deployment_ref="read-release-1",
            read_deployment_digest="a" * 64,
            validity_policy_ref="validity-1",
            validity_policy_digest="b" * 64,
            max_envelope_seconds="60",
            catalog_ref="catalog-staff",
            portal_read_key=_public("portal-read-1", "portal-staff", Ed25519PrivateKey.generate(), "c" * 64),
            publication_key_id="portal-publication-1",
        ),
        human_trust=dict(
            audience="engine-human",
            max_lifetime_seconds="60",
            command_key=_public(
                "portal-command-1", "portal-staff-command", Ed25519PrivateKey.generate(), "d" * 64
            ),
            authority_key_id="portal-authority-1",
        ),
        provider=dict(
            admission_ref="admission-amh",
            minimum_admission_revision="1",
            native_schema="maezo_native",
            admission_table=dict(oid="4242", owner="maezo_native_schema_owner"),
            continuity_keys_file="/run/maezo/native/continuity-keys.json",
            continuity_generation="1",
            membership_source=dict(
                dsn_file="/run/maezo/native/observer-dsn.txt",
                ca_file="/run/maezo/native/pg-ca.pem",
                source_schema="amh",
                publisher_ref="portal-staff",
            ),
        ),
        staff=dict(
            auth_scope=dict(
                tenant="amh",
                environment="dev",
                engine_name="default",
                database_incarnation="inc-test-1",
                installation_ref="auth-installation",
                installation_revision="1",
            ),
            native_role="cibseven_app",
            native_schema="maezo_native",
            engine_schema="cibseven",
            relation_pins={"mzo_staff_case_grant": dict(oid="11", owner="maezo_native_schema_owner")},
            maximum_seconds="10",
            catalog_ref="catalog-staff",
            catalog_publisher_ref="portal-staff",
            auth=dict(
                audience="engine-auth",
                max_lifetime_seconds="60",
                timeout_seconds="5",
                alias="auth-result",
                key_id="auth-result-1",
                issuer="engine",
            ),
        ),
    )
    value.update(changes)
    return value


def _root() -> bytes:
    return _spki(Ed25519PrivateKey.generate())


def _build(generated: Generated, now: datetime, value: dict[str, Any] | None = None):
    return build(generated.directory, _root(), load_input(canonicalize(value or native_input(now))))


def test_q2_trust_has_one_key_key_id_and_peer_per_purpose(generated: Generated, now: datetime) -> None:
    files, public = _build(generated, now)
    trust = strict_loads(files["engine-run/portal-read-trust.json"])
    keys = {k["purpose"]: k for k in trust["public_keys"]}
    assert set(keys) == {"portal-task-read", "portal-read-publication"}
    read, publication = keys["portal-task-read"], keys["portal-read-publication"]
    assert set(read) == {
        "key_id",
        "purpose",
        "workload_ref",
        "peer_spki_sha256",
        "public_key_spki_base64",
        "not_before",
        "not_after",
    }
    assert set(publication) == set(read) | {"publication_kinds", "catalog_ref"}
    # H4 (fim do D11 do C1): a chave do job pode publicar tarefas; a admissao decide se admite.
    assert publication["publication_kinds"] == ["catalog-designate", "membership", "resource"]
    assert read["key_id"] != publication["key_id"]
    assert read["public_key_spki_base64"] != publication["public_key_spki_base64"]
    assert read["peer_spki_sha256"] != publication["peer_spki_sha256"]
    # O peer da publicacao e o certificado de cliente do JOB, emitido pela CA de clientes nativa.
    job = generated.directory / "job"
    certificate = x509.load_pem_x509_certificate((job / "job-client-certificate.pem").read_bytes())
    spki = certificate.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    assert publication["peer_spki_sha256"] == hashlib.sha256(spki).hexdigest()
    ca = x509.load_pem_x509_certificate((generated.directory / "engine/native-client-ca.pem").read_bytes())
    assert certificate.issuer == ca.subject
    signing = serialization.load_pem_private_key((job / "publication-signing-key.pem").read_bytes(), None)
    assert publication["public_key_spki_base64"] == base64.b64encode(_spki(signing)).decode()  # type: ignore[arg-type]
    assert (
        public["publication_key"]["fingerprint"]
        == generated.summary["job_key_fingerprints"]["portal-read-publication"]
    )
    assert (
        public["trust_configuration_digest"]
        == hashlib.sha256(files["engine-run/portal-read-trust.json"]).hexdigest()
    )


def test_human_trust_binds_the_authority_key_to_the_job_certificate(
    generated: Generated, now: datetime
) -> None:
    files, _ = _build(generated, now)
    trust = strict_loads(files["engine-run/trust.json"])
    keys = {k["purpose"]: k for k in trust["keys"]}
    assert set(keys) == {"human-command", "human-authority"}
    assert (
        keys["human-authority"]["peer_spki_sha256"]
        == generated.summary["client_certificate_spki_sha256"]["publication_job"]
    )
    assert keys["human-authority"]["not_before"].isdigit() and trust["enable_synthetic_fixture"] is False


def test_staff_composition_digest_and_auth_pkcs12(generated: Generated, now: datetime) -> None:
    files, public = _build(generated, now)
    composition = strict_loads(files["engine-run/staff/staff-composition.json"])
    assert composition["designation_digest"] == generated.summary["designation_sha256"]
    configuration = dict(
        schema="staff-case-native-configuration.v2",
        auth_scope=composition["auth_scope"],
        designation_digest=composition["designation_digest"],
        root_key_fingerprint=hashlib.sha256(base64.b64decode(composition["root_public_key"])).hexdigest(),
        result_key_fingerprint=hashlib.sha256(base64.b64decode(composition["result_public_key"])).hexdigest(),
        native_role=composition["native_role"],
        native_schema=composition["native_schema"],
        engine_schema=composition["engine_schema"],
        relation_pins=composition["relation_pins"],
        maximum_seconds=composition["maximum_seconds"],
    )
    assert composition["configuration_digest"] == hashlib.sha256(canonicalize(configuration)).hexdigest()
    assert public["staff_native_configuration_digest"] == composition["configuration_digest"]
    key, certificate, _ = pkcs12.load_key_and_certificates(
        files["engine-run/staff/auth-signing.p12"], files["engine-run/staff/auth-signing.password"]
    )
    assert isinstance(key, Ed25519PrivateKey) and certificate is not None
    spki = certificate.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    assert composition["auth"]["signing_spki_sha256"] == hashlib.sha256(spki).hexdigest()
    # O resumo publico nunca carrega segredo: os privados aparecem sem digest.
    assert all(public["files"][name] is None for name in PRIVATE_OUTPUT)
    assert b"secret_base64" not in canonicalize(public)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda v: v["read_trust"].update(publication_key_id=v["read_trust"]["portal_read_key"]["key_id"]),
        lambda v: v["human_trust"].update(authority_key_id=v["human_trust"]["command_key"]["key_id"]),
        lambda v: v.update(
            not_after=instant(datetime.fromisoformat(v["not_before"][:-1]) + timedelta(days=15))
        ),
        lambda v: v["read_trust"]["portal_read_key"].update(workload_ref="outro"),
        lambda v: v["read_trust"]["portal_read_key"].update(public_key_spki_base64="AAAA"),
        lambda v: v.update(extra="x"),
    ],
)
def test_refuses_shared_or_malformed_facts(generated: Generated, now: datetime, mutate: Any) -> None:
    value = native_input(now)
    mutate(value)
    with pytest.raises(MaterialError):
        _build(generated, now, value)


def test_refuses_the_portal_read_peer_for_the_publication(generated: Generated, now: datetime) -> None:
    value = native_input(now)
    value["read_trust"]["portal_read_key"]["peer_spki_sha256"] = generated.summary[
        "client_certificate_spki_sha256"
    ]["publication_job"]
    with pytest.raises(MaterialError, match="peer"):
        _build(generated, now, value)


def test_cli_writes_the_secret_with_private_modes(
    generated: Generated, now: datetime, tmp_path: Path
) -> None:
    approver_dir = tmp_path / "approver"
    approver_dir.mkdir()
    (approver_dir / "installation-root.der").write_bytes(_root())
    source = tmp_path / "native-input.json"
    source.write_bytes(canonicalize(native_input(now)))
    out = tmp_path / "native-secret"
    code = main(
        [
            "native-secret",
            "--materials",
            str(generated.directory),
            "--approver",
            str(approver_dir),
            "--input",
            str(source),
            "--out",
            str(out),
        ]
    )
    assert code == 0
    assert stat.S_IMODE(out.stat().st_mode) == 0o700
    for name in PRIVATE_OUTPUT:
        assert stat.S_IMODE((out / name).stat().st_mode) == 0o400, name
    assert stat.S_IMODE((out / "engine-run/portal-read-trust.json").stat().st_mode) == 0o444
    assert (out / "public/native-secret-summary.json").is_file()
