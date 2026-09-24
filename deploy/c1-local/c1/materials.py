"""Passo `materials`: `generate` (T1.3) -> raiz de TESTE -> `sign-designation`.

A raiz nasce aqui, em `/c1/TEST-ROOT-DESCARTAVEL-C1`, SO para o C1, e morre com o volume. Nunca e a do
aprovador (regra D-F): o C1 prova o encadeamento das ferramentas, nao a aprovacao.

Desvios do harness (cada um e uma pendencia medida, listada no README):
* D1 `session-lock-ca.pem`/`native-witness-ca.pem`: o `generate` grava o bundle RDS pinado; aqui o
  banco e local, entao os dois viram a CA local ANTES do `assemble` (que hasheia o que esta no disco);
* D2 a designacao do `generate` nao traz a entrada `identity_verifier` PROPRIA do emissor
  (`entry_ref=case-issuer-witness`, D-H.2), que `IssuerWitness` exige. O harness acrescenta essa
  entrada (chave nova, login `maezo_native_issuer_witness`) ao rascunho e ao `summary.json` antes de
  assinar. Sem isso a T1.6 nao roda com material da ferramenta.
"""

from __future__ import annotations

import base64
import os
from datetime import timedelta
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from tools.staff_materials import approver
from tools.staff_materials.generate import generate
from tools.staff_materials.spec import load_spec

from maezo.gateway.external_cases.models import digest, instant, parse, timestamp
from maezo.gateway.staff_cases.authority import fingerprint
from maezo.gateway.staff_cases.case_issuer import policy_ref_for
from maezo.gateway.staff_cases.models import Designation
from maezo.portal.engine.profile import strict_loads

from .common import (
    APPROVER_OUT,
    DATABASE,
    ENGINE_NAME,
    ENVIRONMENT,
    INCARNATION,
    ISSUER_LOGIN,
    ISSUER_WITNESS_LOGIN,
    MATERIALS,
    NATIVE_HOSTNAME,
    PG_HOST,
    PGTLS,
    ROOT,
    SESSION_LOCK_LOGIN,
    TENANT,
    TEST_ROOT,
    WITNESS_LOGIN,
    jcs,
    now,
    save_state,
    step,
    write,
)

DESIGNATION_REVISION = 1


def spec() -> dict:
    start = now() - timedelta(minutes=2)
    connection = dict(host=PG_HOST, port="5432", database=DATABASE)
    return dict(
        schema="staff-materials-spec.v1",
        scope=dict(tenant=TENANT, environment=ENVIRONMENT, engine_name=ENGINE_NAME, database_incarnation=INCARNATION),
        native_hostname=NATIVE_HOSTNAME,
        certificate_not_after=instant(start + timedelta(days=20)),
        designation=dict(
            designation_ref="staff-designation-c1",
            designation_revision=str(DESIGNATION_REVISION),
            expected_previous_revision=str(DESIGNATION_REVISION - 1),
            authority_ref="c1-test-root",
            authority_revision="1",
            not_before=instant(start),
            valid_until=instant(start + timedelta(days=13)),
        ),
        read_requester=dict(login_role="portal_staff_read_amh", source_namespace="portal", source_ref="portal-read"),
        identity_verifier=dict(
            login_role=WITNESS_LOGIN, source_namespace="portal-identity", source_ref="membership-amh"
        ),
        native_result=dict(login_role="cibseven_app", source_namespace="engine", source_ref="engine-result"),
        case_issuer=dict(
            login_role=ISSUER_LOGIN,
            source_namespace=policy_ref_for(str(DESIGNATION_REVISION)),
            source_ref="staff-cases-amh",
        ),
        publication_importer=dict(
            login_role="staff_publication_importer_amh", source_namespace="staff-import", source_ref="staff-cases-amh"
        ),
        session_lock_connection=dict(connection, login=SESSION_LOCK_LOGIN),
        native_witness_connection=dict(connection, login=WITNESS_LOGIN),
    )


def _replace(path: Path, data: bytes) -> None:
    mode = path.stat().st_mode & 0o777
    os.chmod(path.parent, 0o700)
    path.unlink()
    write(path, data, mode)


def main() -> None:
    raw_spec = jcs(spec())
    write(ROOT / "spec.json", raw_spec, 0o444)
    generated = generate(load_spec(raw_spec), MATERIALS)
    portal = MATERIALS / "portal"
    ca = (PGTLS / "ca.pem").read_bytes()
    for name in ("session-lock-ca.pem", "native-witness-ca.pem"):  # D1
        _replace(portal / name, ca)

    # D2: a entrada `case-issuer-witness` que a T1.6 exige e o generate nao produz.
    issuer_witness = Ed25519PrivateKey.generate()
    designation = strict_loads((portal / "designation.json").read_bytes())
    public = issuer_witness.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    # Inserida ANTES da entrada do portal: o `assemble` indexa as entradas por `role` (dict) e so
    # confere a ultima `identity_verifier`; na ordem inversa ele recusa (F5 no README).
    designation["entries"].insert(
        0,
        dict(
            entry_ref="case-issuer-witness",
            role="identity_verifier",
            source_namespace="portal-identity",
            source_ref="membership-amh",
            key_fingerprint=fingerprint(issuer_witness.public_key()),
            certificate_spki=None,
            public_key=base64.b64encode(public).decode("ascii"),
            login_role=ISSUER_WITNESS_LOGIN,
            purposes=["membership_current"],
            projections=[],
            operations=[],
            not_before=designation["entries"][0]["not_before"],
            valid_until=designation["entries"][0]["valid_until"],
        )
    )
    parse(Designation, jcs(designation))
    _replace(portal / "designation.json", jcs(designation))
    summary_path = MATERIALS / "public" / "summary.json"
    summary = strict_loads(summary_path.read_bytes())
    summary["designation_sha256"] = digest(designation)
    _replace(summary_path, jcs(summary))
    issuer = MATERIALS / "issuer"
    os.chmod(issuer, 0o700)
    write(
        issuer / "issuer-witness-signing-key.pem",
        issuer_witness.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        ),
    )

    # Raiz de TESTE (descartavel) e a assinatura da designacao, pelas funcoes do `approver`.
    approver.root_keygen(TEST_ROOT, passphrase=None, plaintext=True)
    root = approver.load_root(TEST_ROOT / "installation-root-key.pem")
    _, designation_digest, _ = approver.review_designation(jcs(designation))
    proof = approver.sign_designation(
        jcs(designation),
        root,
        confirm_digest=designation_digest,
        expires_at=timestamp(designation["valid_until"]) - timedelta(minutes=1),
    )
    APPROVER_OUT.mkdir(mode=0o700)
    write(APPROVER_OUT / "installation-root.der", (TEST_ROOT / "installation-root.der").read_bytes(), 0o444)
    write(APPROVER_OUT / "installation-proof.json", proof, 0o444)
    save_state(
        "materials",
        dict(
            designation_digest=designation_digest,
            root_sha256=fingerprint(root.public_key()),
            summary=summary,
        ),
    )
    count = sum(1 for p in generated.directory.rglob("*") if p.is_file())
    step(
        "materials",
        True,
        f"generate={count} arquivos; raiz de TESTE {fingerprint(root.public_key())[:12]}; "
        f"designacao {designation_digest[:12]} assinada (6 entradas, D2)",
    )


if __name__ == "__main__":
    main()
