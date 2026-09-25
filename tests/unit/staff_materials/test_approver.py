"""Comandos do aprovador: revisao obrigatoria, confirmacao do digest lido e dominio da admissao Q2.

A raiz e gerada no teste (`Ed25519PrivateKey.generate()` ou `root_keygen` num tmp do teste).
"""

from __future__ import annotations

import base64
import hashlib
import json
import stat
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from tools.staff_materials import approver
from tools.staff_materials.generate import REPO, Generated
from tools.staff_materials.secure_io import MaterialError

from maezo.gateway.external_cases.models import digest, instant, parse
from maezo.gateway.staff_cases.authority import fingerprint
from maezo.gateway.staff_cases.models import Proof
from maezo.portal.engine.profile import canonicalize


def _designation(generated: Generated) -> bytes:
    return (generated.directory / "portal/designation.json").read_bytes()


@pytest.mark.parametrize("role", ["case_issuer", "read_requester"])
def test_designation_without_the_escalation_projection_is_refused(generated: Generated, role: str) -> None:
    value = json.loads(_designation(generated))
    for entry in value["entries"]:
        if entry["role"] == role:
            entry["projections"] = [p for p in entry["projections"] if p != "staff_escalation.v1"]
    with pytest.raises(MaterialError, match="staff_escalation.v1"):
        approver.review_designation(canonicalize(value))


def test_review_shows_the_escalation_projection(generated: Generated) -> None:
    _, _, lines = approver.review_designation(_designation(generated))
    assert sum("staff_escalation.v1" in line for line in lines) == 2


def test_signing_requires_the_digest_of_what_was_shown(generated: Generated, now: datetime) -> None:
    raw = _designation(generated)
    root = Ed25519PrivateKey.generate()
    _, shown, lines = approver.review_designation(raw)
    # A revisao mostra cada papel, com a chave e as capacidades, e nenhum segredo.
    assert len([line for line in lines if line.startswith("- ")]) == 6
    assert all("PRIVATE" not in line for line in lines)
    with pytest.raises(approver.ReviewRequiredError):
        approver.sign_designation(raw, root, confirm_digest=None, expires_at=now + timedelta(days=1), now=now)
    with pytest.raises(MaterialError, match="nada foi assinado"):
        approver.sign_designation(
            raw, root, confirm_digest="0" * 64, expires_at=now + timedelta(days=1), now=now
        )
    signed = approver.sign_designation(
        raw, root, confirm_digest=shown, expires_at=now + timedelta(days=1), now=now
    )
    proof = parse(Proof, signed)
    assert proof.statement_digest == shown == generated.summary["designation_sha256"]
    assert proof.key_fingerprint == fingerprint(root.public_key())


@pytest.mark.parametrize("expires", ["beyond_designation", "fifteen_days", "past"])
def test_proof_window_is_bounded(expires: str, generated: Generated, now: datetime) -> None:
    raw = _designation(generated)
    _, shown, _ = approver.review_designation(raw)
    at = {
        "beyond_designation": now + timedelta(days=13, hours=1),
        "fifteen_days": now + timedelta(days=15),
        "past": now - timedelta(seconds=1),
    }[expires]
    with pytest.raises(MaterialError):
        approver.sign_designation(
            raw, Ed25519PrivateKey.generate(), confirm_digest=shown, expires_at=at, now=now
        )


def test_root_equal_to_a_designated_key_is_refused(generated: Generated, now: datetime) -> None:
    raw = _designation(generated)
    _, shown, _ = approver.review_designation(raw)
    read_key = serialization.load_pem_private_key(
        (generated.directory / "portal/read-signing-key.pem").read_bytes(), password=None
    )
    assert isinstance(read_key, Ed25519PrivateKey)
    with pytest.raises(MaterialError, match="verificador do portal"):
        approver.sign_designation(
            raw, read_key, confirm_digest=shown, expires_at=now + timedelta(days=1), now=now
        )


def test_root_keygen_encrypts_by_default_in_a_new_private_directory(tmp_path: Path) -> None:
    out = tmp_path / "root"
    shown = approver.root_keygen(out, passphrase=b"senha de teste longa")
    public = serialization.load_der_public_key((out / "installation-root.der").read_bytes())
    assert isinstance(public, Ed25519PublicKey) and fingerprint(public) == shown
    assert b"ENCRYPTED PRIVATE KEY" in (out / "installation-root-key.pem").read_bytes()
    with pytest.raises(MaterialError, match="senha"):
        approver.load_root(out / "installation-root-key.pem")  # cifrada: sem senha nao abre
    with pytest.raises(MaterialError, match="senha"):
        approver.load_root(out / "installation-root-key.pem", b"senha errada qualquer")
    opened = approver.load_root(out / "installation-root-key.pem", b"senha de teste longa")
    assert fingerprint(opened.public_key()) == shown
    assert stat.S_IMODE(out.stat().st_mode) == 0o700
    assert stat.S_IMODE((out / "installation-root-key.pem").stat().st_mode) == 0o400
    with pytest.raises(MaterialError, match="NOVO"):
        approver.root_keygen(out, passphrase=b"senha de teste longa")


def test_plaintext_root_only_with_explicit_no_encrypt(tmp_path: Path) -> None:
    with pytest.raises(MaterialError, match="cifrada por padrao"):
        approver.root_keygen(tmp_path / "a", passphrase=None)
    with pytest.raises(MaterialError, match="curta"):
        approver.root_keygen(tmp_path / "b", passphrase=b"curta")
    with pytest.raises(MaterialError, match="excludentes"):
        approver.root_keygen(tmp_path / "c", passphrase=b"senha de teste longa", plaintext=True)
    assert not any((tmp_path / n).exists() for n in "abc")
    approver.root_keygen(tmp_path / "d", passphrase=None, plaintext=True)
    assert b"BEGIN PRIVATE KEY" in (tmp_path / "d/installation-root-key.pem").read_bytes()


def test_root_keygen_refuses_the_repository() -> None:
    target = REPO / "tmp-root-should-never-exist"
    with pytest.raises(MaterialError, match="repositorio"):
        approver.root_keygen(target, passphrase=b"senha de teste longa")
    assert not target.exists()


def admission(now: datetime, **changes: Any) -> dict[str, Any]:
    """Registro no shape fechado de `AdmissionRecord.java` (T1.7a)."""
    value: dict[str, Any] = dict(
        schema="portal-read-admission.v1",
        admission_ref="admission-amh",
        admission_revision="1",
        scope=dict(tenant="amh", environment="dev", workload_ref="portal-staff"),
        engine_name="default",
        database_incarnation="inc-test-1",
        read_deployment_ref="read-release-1",
        read_deployment_digest="c" * 64,
        trust_configuration_digest="a" * 64,
        purposes=["portal-read-publication", "portal-task-read"],
        code_digests=dict(engine="d" * 64, provider="e" * 64),
        continuity_keys=[
            dict(
                key_id="continuity-1",
                generation="1",
                commitment="f" * 64,
                not_before=instant(now),
                not_after=instant(now + timedelta(days=14)),
            )
        ],
        catalog=dict(catalog_ref="catalog-staff", publisher_ref="portal-staff", catalog_digest="b" * 64),
        publishers=[
            dict(kind="membership", publisher_ref="portal-staff", source_ref_prefix="portal-identity:amh:"),
            dict(
                kind="catalog-designate", publisher_ref="portal-staff", source_ref_prefix="staff-catalog:amh:"
            ),
        ],
        statement_timeout_seconds="5",
        observation_seconds="300",
        not_before=instant(now),
        valid_until=instant(now + timedelta(days=14)),
    )
    value.update(changes)
    return value


def test_admission_signature_is_domain_separated(now: datetime) -> None:
    root = Ed25519PrivateKey.generate()
    raw = canonicalize(admission(now))
    _, shown, lines = approver.review_admission(raw)
    assert shown == digest(admission(now)) and any(line.startswith("catalog=") for line in lines)
    with pytest.raises(approver.ReviewRequiredError):
        approver.sign_admission(raw, root, confirm_digest=None)
    signature = base64.b64decode(approver.sign_admission(raw, root, confirm_digest=shown))
    root.public_key().verify(signature, b"maezo/portal-read-admission/v1\x00" + raw)
    # Sem o dominio a assinatura nao vale: ela nao serve como prova de designacao nem de outro tipo.
    with pytest.raises(InvalidSignature):
        root.public_key().verify(signature, raw)


def _four_field_scope(value: dict[str, Any]) -> None:
    value["scope"] = dict(
        tenant="amh", environment="dev", engine_name="default", database_incarnation="inc-1"
    )


@pytest.mark.parametrize(
    "change",
    [
        {"valid_until": "15d"},
        {"schema": "portal-read-admission.v2"},
        {"statement_timeout_seconds": "11"},
        {"observation_seconds": "59"},
        {"admission_revision": "0"},
        {"revoked": False},
        {"purposes": ["portal-task-read", "portal-task-read"]},
        {"purposes": ["human-authority"]},
        {"purposes": []},
        {"publishers": [dict(kind="resource", publisher_ref="p", source_ref_prefix="r:")]},
        {"publishers": [dict(kind="membership", publisher_ref="p", source_ref_prefix="portal-identity:amh")]},
        {"continuity_keys": []},
        {"code_digests": dict(engine="d" * 64)},
        {"read_deployment_digest": "C" * 64},
        {"four_field_scope": True},
        {"noncanonical": True},
    ],
)
def test_admission_outside_the_closed_shape_is_refused(change: dict[str, Any], now: datetime) -> None:
    value = admission(now)
    raw: bytes
    if "noncanonical" in change:
        raw = canonicalize(value).replace(b",", b", ", 1)
    else:
        if change.get("valid_until") == "15d":
            change = {"valid_until": instant(now + timedelta(days=15))}
        if change.pop("four_field_scope", False):
            _four_field_scope(value)
        value.update(change)
        raw = canonicalize(value)
    with pytest.raises(MaterialError):
        approver.review_admission(raw)


RESOURCE_VECTOR = REPO / "tests" / "fixtures" / "portal_read" / "jcs-resource-vector.json"


def _human() -> dict[str, Any]:
    """O bloco `human` do vetor compartilhado com o provedor Java (H1, `HumanAdmissionTest`)."""
    return json.loads(RESOURCE_VECTOR.read_text(encoding="utf-8"))["human"]


def _artifact(name: str) -> dict[str, str]:
    raw = f"SYN {name}".encode()
    return dict(
        artifact_ref=f"SYN-{name}",
        digest=hashlib.sha256(raw).hexdigest(),
        bytes_base64=base64.b64encode(raw).decode(),
    )


def _pin(name: str) -> dict[str, str]:
    a = _artifact(name)
    return dict(artifact_ref=a["artifact_ref"], digest=a["digest"])


POLICIES = (
    "subject-policy",
    "consent-policy",
    "resource-policy",
    "disclosure-policy",
    "opaque-task-id-policy",
)


def _task_catalog(human: dict[str, Any]) -> bytes:
    """O catalogo com a tarefa do bloco `human` (H4): policies sinteticas `SYN-`, bytes reais."""
    entry = _entry(human)
    form = dict(_artifact("form-escalation"), artifact_ref="escalation")
    value = dict(
        schema="portal-read-catalog.v1",
        catalog_ref="catalog-staff",
        publisher_ref="portal-staff",
        entries=[
            dict(
                process_definition_id=entry["process_definition_id"],
                process_definition_key="SP-OP-ESCALATION-001",
                process_definition_version="1",
                process_definition_digest="1" * 64,
                task_definition_key=entry["task_definition_key"],
                form_key="escalation",
                form_version="1",
                form_digest=form["digest"],
                form_source_status="BPMN_FORMDATA",
                allowed_inputs=["resultado", "notas_resolucao"],
                required_roles=["atendente"],
                subject_policy=_pin("subject-policy"),
                consent_policy=_pin("consent-policy"),
                resource_policy=_pin("resource-policy"),
                disclosure_policy=_pin("disclosure-policy"),
                opaque_task_id_policy=_pin("opaque-task-id-policy"),
                group_domain=dict(
                    kind="static",
                    groups=list(entry["candidate_groups"]),
                    dmn_definition_id=None,
                    dmn_definition_key=None,
                    dmn_definition_version=None,
                    dmn_resource_digest=None,
                ),
            )
        ],
        policies=[_artifact(p) for p in POLICIES],
        forms=[form],
        deployment_receipt_ref="SYN-deployment",
        deployment_receipt_digest="2" * 64,
    )
    return canonicalize(value)


def _with_human(now: datetime, human: Any) -> dict[str, Any]:
    value = admission(now)
    value["human"] = human
    value["publishers"].append(
        dict(kind="resource", publisher_ref="portal-staff", source_ref_prefix="portal-resource:amh:task:")
    )
    return value


def _admitted(now: datetime) -> tuple[dict[str, Any], bytes]:
    """Vetor `human` com as politicas do catalogo (a identidade e a divulgacao sao as dele)."""
    human = _human()
    entry = _entry(human)
    entry["identity_policy"] = _pin("opaque-task-id-policy")
    entry["classification"].update(
        policy_ref=_pin("disclosure-policy")["artifact_ref"],
        policy_digest=_pin("disclosure-policy")["digest"],
    )
    entry["task_id_format"] = "uuid"  # H4: a imagem do engine de dev gera UUID
    catalog = _task_catalog(human)
    value = _with_human(now, human)
    value["catalog"]["catalog_digest"] = hashlib.sha256(catalog).hexdigest()
    return value, catalog


def test_admission_with_the_human_block_of_the_shared_vector_is_admitted(now: datetime) -> None:
    value, catalog = _admitted(now)
    _, shown, lines = approver.review_admission(canonicalize(value), catalog)
    assert shown == digest(value)
    assert any(line.startswith("human=") for line in lines)
    assert any("task_id_format=uuid" in line for line in lines)


def test_admission_with_tasks_is_never_reviewed_without_its_catalog(now: datetime) -> None:
    value, _ = _admitted(now)
    with pytest.raises(MaterialError, match="catalogo"):
        approver.review_admission(canonicalize(value))


@pytest.mark.parametrize(
    "breaks",
    [
        lambda v, c: (v, c + b" "),  # bytes que a admissao nao pina
        lambda v, c: (v, c.replace(b"catalog-staff", b"catalog-other")),
        lambda v, c: (_set(v, "identity_policy", _pin("resource-policy")), c),
        lambda v, c: (_set(v, "candidate_groups", ["atendimento-humano", "fora-do-dominio"]), c),
        lambda v, c: (_classification(v, policy_ref="SYN-resource-policy"), c),
        lambda v, c: (_second_human_entry(v), c),  # tarefa admitida que o catalogo nao tem
    ],
)
def test_catalog_that_does_not_match_the_human_block_is_refused(breaks: Any, now: datetime) -> None:
    value, catalog = _admitted(now)
    approver.review_admission(canonicalize(value), catalog)  # controle positivo
    value, catalog = breaks(value, catalog)
    with pytest.raises(MaterialError):
        approver.review_admission(canonicalize(value), catalog)


def _set(value: dict[str, Any], key: str, item: Any) -> dict[str, Any]:
    _entry(value["human"])[key] = item
    return value


def _classification(value: dict[str, Any], **changes: str) -> dict[str, Any]:
    _entry(value["human"])["classification"].update(changes)
    return value


def _second_human_entry(value: dict[str, Any]) -> dict[str, Any]:
    other = json.loads(json.dumps(_entry(value["human"])))
    other["task_definition_key"] = "UT_Outra"
    value["human"]["entries"].append(other)
    return value


def test_staff_only_catalog_given_must_still_be_the_pinned_one(now: datetime) -> None:
    value = admission(now)
    with pytest.raises(MaterialError):
        approver.review_admission(canonicalize(value), b"{}")


def _entry(human: dict[str, Any]) -> dict[str, Any]:
    return human["entries"][0]


@pytest.mark.parametrize(
    "breaks",
    [
        lambda v: v.pop("human"),  # publicador resource sem o bloco
        lambda v: v["publishers"].pop(),  # bloco sem o publicador resource
        lambda v: v["human"].update(extra="x"),
        lambda v: v["human"].update(entries=[]),
        lambda v: v["human"]["entries"].append(dict(_entry(v["human"]))),
        lambda v: _entry(v["human"]).update(task_id_format="any"),
        lambda v: _entry(v["human"]).update(user_candidates="anyone"),
        lambda v: _entry(v["human"]).update(candidate_groups=[]),
        lambda v: _entry(v["human"]).update(candidate_groups=["g", "g"]),
        lambda v: _entry(v["human"]).update(candidate_groups=["${expr}"]),
        lambda v: _entry(v["human"])["classification"].update(projection="x"),
        lambda v: _entry(v["human"])["classification"].update(valid_until="2026-01-01T00:00:00.000000Z"),
        lambda v: _entry(v["human"])["identity_policy"].update(digest="x"),
    ],
)
def test_human_block_outside_the_java_shape_is_refused(breaks: Any, now: datetime) -> None:
    value, catalog = _admitted(now)
    approver.review_admission(canonicalize(value), catalog)  # controle positivo
    breaks(value)
    with pytest.raises(MaterialError):
        approver.review_admission(canonicalize(value), catalog)


def test_cli_prints_review_and_does_not_sign_without_confirmation(
    generated: Generated, tmp_path: Path, capsys: pytest.CaptureFixture[str], now: datetime
) -> None:
    root_dir = tmp_path / "approver-root"
    approver.root_keygen(root_dir, passphrase=b"senha de teste longa")
    (tmp_path / "senha.txt").write_bytes(b"senha de teste longa" + b"\n")
    out = tmp_path / "installation-proof.json"
    args = [
        "sign-designation",
        "--designation",
        str(generated.directory / "portal/designation.json"),
        "--root-key",
        str(root_dir / "installation-root-key.pem"),
        "--passphrase-file",
        str(tmp_path / "senha.txt"),
        "--expires-at",
        instant(now + timedelta(days=2)),
        "--out",
        str(out),
    ]
    assert approver.main(args) == 2
    assert not out.exists()
    shown = next(
        line.split("=", 1)[1]
        for line in capsys.readouterr().out.splitlines()
        if line.startswith("designation_sha256=")
    )
    assert approver.main([*args, "--confirm-digest", shown]) == 0
    assert parse(Proof, out.read_bytes()).statement_digest == shown
    assert approver.main([*args, "--confirm-digest", shown]) == 1  # saida existente: nunca sobrescreve


# --- sign-assignment-owner (D-O: documento de dono do plano de atribuicao) -----------------------


def _owner(**change: Any) -> dict[str, Any]:
    value: dict[str, Any] = dict(
        schema="staff-assignment-owner.v1",
        owner_ref="maezo-operadora-dev:assignment-owner:amh:1",
        source_ref="maezo-operadora-dev:assignment-source:amh:default:1",
        tenant="amh",
        environment="dev",
        engine_name="default",
        database_incarnation="inc-1",
        source_key_fingerprint="a" * 64,
        not_before="2026-09-25T00:00:00.000000Z",
        valid_until="2026-10-08T00:00:00.000000Z",
    )
    value.update(change)
    return value


def test_assignment_owner_is_signed_only_after_the_shown_digest() -> None:
    from tools.staff_materials.assignment_trust import OWNER_DOMAIN

    raw = canonicalize(_owner())
    root = Ed25519PrivateKey.generate()
    record, shown, lines = approver.review_assignment_owner(raw)
    assert shown == hashlib.sha256(raw).hexdigest() and len(lines) == 10
    with pytest.raises(approver.ReviewRequiredError):
        approver.sign_assignment_owner(raw, root, confirm_digest=None)
    with pytest.raises(MaterialError, match="nada foi assinado"):
        approver.sign_assignment_owner(raw, root, confirm_digest="0" * 64)
    proof = json.loads(approver.sign_assignment_owner(raw, root, confirm_digest=shown))
    assert set(proof) == {"schema", "document", "signature"} and proof["document"] == record
    root.public_key().verify(base64.b64decode(proof["signature"]), OWNER_DOMAIN + raw)
    with pytest.raises(InvalidSignature):
        root.public_key().verify(base64.b64decode(proof["signature"]), raw)


@pytest.mark.parametrize(
    "raw",
    [
        canonicalize(_owner(extra="x")),
        canonicalize({k: v for k, v in _owner().items() if k != "source_key_fingerprint"}),
        canonicalize(_owner(schema="staff-assignment-owner.v2")),
        canonicalize(_owner(source_key_fingerprint="A" * 64)),
        canonicalize(_owner(not_before="2026-09-25T00:00:00Z")),
        canonicalize(_owner(valid_until="2026-10-10T00:00:00.000000Z")),  # > 14 dias
        canonicalize(_owner(valid_until="2026-09-25T00:00:00.000000Z")),  # janela vazia
        json.dumps(_owner(), indent=1).encode(),  # nao-JCS
    ],
)
def test_assignment_owner_outside_the_closed_format_is_refused(raw: bytes) -> None:
    with pytest.raises(MaterialError):
        approver.review_assignment_owner(raw)


def test_sign_assignment_owner_cli(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    approver.root_keygen(tmp_path / "root", passphrase=None, plaintext=True)
    record = tmp_path / "owner.json"
    record.write_bytes(canonicalize(_owner()))
    key = str(tmp_path / "root" / "installation-root-key.pem")
    base = ["sign-assignment-owner", "--record", str(record), "--root-key", key]
    assert approver.main([*base, "--out", str(tmp_path / "a.json")]) == 2  # so revisao, nada assinado
    assert not (tmp_path / "a.json").exists()
    shown = hashlib.sha256(record.read_bytes()).hexdigest()
    assert f"assignment_owner_sha256={shown}" in capsys.readouterr().out
    assert approver.main([*base, "--confirm-digest", shown, "--out", str(tmp_path / "b.json")]) == 0
    signed = (tmp_path / "b.json").read_bytes()
    assert f"owner_receipt_digest={hashlib.sha256(signed).hexdigest()}" in capsys.readouterr().out
