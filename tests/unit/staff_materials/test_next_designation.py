"""Rotacao da designacao (Onda 8): rascunho N+1 com as MESMAS chaves, assinado pelo aprovador e
conferido pelo instalador `rows` com os verificadores do runtime. Raiz de TESTE gerada no teste."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from tools.staff_materials import approver
from tools.staff_materials.__main__ import main as cli
from tools.staff_materials.generate import Generated, next_designation
from tools.staff_ops import rows
from tools.staff_ops.common import OpsError

from maezo.gateway.external_cases.models import instant
from maezo.gateway.staff_cases.case_issuer import policy_ref_for
from maezo.portal.engine.profile import canonicalize, strict_loads


def _r1(generated: Generated) -> dict[str, Any]:
    return strict_loads((generated.directory / "portal/designation.json").read_bytes())


def _without_current_task(value: dict[str, Any]) -> dict[str, Any]:
    """A r1 como o dev a tem hoje: emissor sem `staff_current_task.v1`."""
    entries = [
        dict(e, projections=[p for p in e["projections"] if p != "staff_current_task.v1"])
        if e["role"] == "case_issuer"
        else e
        for e in value["entries"]
    ]
    return dict(value, entries=entries)


def _signed(raw: bytes, root: Ed25519PrivateKey, now: datetime) -> bytes:
    _, shown, _ = approver.review_designation(raw)
    return approver.sign_designation(
        raw, root, confirm_digest=shown, expires_at=now + timedelta(days=1), now=now
    )


def _rows(raw: bytes, proof: bytes, root: Ed25519PrivateKey, revision: int) -> dict[str, Any]:
    return dict(
        designation=raw,
        designation_sha256=hashlib.sha256(raw).hexdigest(),
        designation_revision=revision,
        proof=proof,
        root=root.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        ),
    )


def test_next_designation_keeps_keys_and_restores_capabilities(generated: Generated, now: datetime) -> None:
    r1 = _without_current_task(_r1(generated))
    r2 = next_designation(r1, not_before=instant(now - timedelta(minutes=1)), valid_until=r1["valid_until"])
    assert (r2["designation_revision"], r2["expected_previous_revision"]) == ("2", "1")
    old = {e["entry_ref"]: e for e in r1["entries"]}
    for entry in r2["entries"]:
        before = old[entry["entry_ref"]]
        assert (
            entry["key_fingerprint"],
            entry["public_key"],
            entry["login_role"],
            entry["certificate_spki"],
        ) == (
            before["key_fingerprint"],
            before["public_key"],
            before["login_role"],
            before["certificate_spki"],
        )
    issuer = next(e for e in r2["entries"] if e["role"] == "case_issuer")
    assert issuer["projections"] == [
        "staff_current_task.v1",
        "staff_escalation.v1",
        "staff_identity.v1",
        "staff_summary.v1",
    ]
    assert issuer["source_namespace"] == policy_ref_for("2")


def test_signed_r2_verifies_and_foreign_root_is_refused(generated: Generated, now: datetime) -> None:
    root, other = Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()
    raw = canonicalize(
        next_designation(
            _r1(generated),
            not_before=instant(now - timedelta(minutes=1)),
            valid_until=_r1(generated)["valid_until"],
        )
    )
    rows.verify_designation(_rows(raw, _signed(raw, root, now), root, 2))
    with pytest.raises(OpsError, match="prova"):
        rows.verify_designation(_rows(raw, _signed(raw, other, now), root, 2))
    with pytest.raises(OpsError, match="designation_revision"):
        rows.verify_designation(_rows(raw, _signed(raw, root, now), root, 3))
    tampered = raw.replace(b'"designation_revision":"2"', b'"designation_revision":"2" ')
    with pytest.raises(OpsError):
        rows.verify_designation(dict(_rows(raw, _signed(raw, root, now), root, 2), designation=tampered))


def test_cli_next_designation_writes_a_new_file(generated: Generated, now: datetime, tmp_path: Path) -> None:
    previous = generated.directory / "portal/designation.json"
    out = tmp_path / "r2.json"
    args = [
        "next-designation",
        "--previous",
        str(previous),
        "--not-before",
        instant(now - timedelta(minutes=1)),
        "--valid-until",
        _r1(generated)["valid_until"],
        "--out",
        str(out),
    ]
    assert cli(args) == 0
    assert json.loads(out.read_bytes())["designation_revision"] == "2"
    with pytest.raises(FileExistsError):  # nao sobrescreve
        cli(args)
