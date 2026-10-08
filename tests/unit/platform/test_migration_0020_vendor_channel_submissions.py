"""Structural tests for migration `0020_vendor_channel_submissions` — the OP16 DDL (VW1-P4).

Same posture as the 0019 structural tests: no Postgres required (the live upgrade/downgrade/
upgrade proof is the integration lane); the properties that must stay true about this DDL are
expressed so a later edit which quietly breaks one fails CI instead of failing a human.

Invariants:

1.  **Chain integrity.** `0020` revises `0019`, is the head, and no other migration claims
    either slot.
2.  **The named business key IS the primary key.** NO-DUPLICATE-INSTANCE (registry OP16) is
    structural: `(tenant, operation, submission_ref, business_revision)` — a re-send can never
    create a second instance, at the storage level, not by application convention.
3.  **Refusal evidence is check-constrained.** `lifecycle = 'refused'` iff `refusal_code` is
    set — the refusal invariant is storage truth, not application hygiene.
4.  **No payload content, no cipher, no retention.** The store keeps the DECLARED class token
    only; the health-declaration schema is VW0-D16 (RATIFY-LATER DPO) and no one invents it in
    a migration; ZERO cifras and ZERO retention deadline.
5.  **`downgrade()` is honest.** It refuses a populated store, drops exactly what `upgrade()`
    created and touches no other table.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import maezo

# `versions/` is a namespace package (no `__init__.py`), so `__file__` is None there — resolve
# from the installed package root, the way the 0019 structural test does.
_VERSIONS_DIR = Path(maezo.__file__).parent / "platform" / "migrations" / "versions"
_MIGRATION = _VERSIONS_DIR / "0020_vendor_channel_submissions.py"
_SOURCE = _MIGRATION.read_text(encoding="utf-8")
_CODE = _SOURCE.split('"""', 2)[2]

_TABLE = "portal_vendor_submissions"


def test_a_migracao_existe_e_encadeia_em_0019() -> None:
    assert _MIGRATION.is_file()
    assert re.search(r'^revision: str = "0020"$', _SOURCE, re.MULTILINE)
    assert re.search(r'^down_revision: str \| None = "0019"$', _SOURCE, re.MULTILINE)


def test_a_cadeia_nao_bifurca() -> None:
    """Exactly one migration claims revision `0020`, and exactly one revises `0019`."""
    claims_0020: list[str] = []
    revises_0019: list[str] = []
    for path in sorted(_VERSIONS_DIR.glob("0*.py")):
        text = path.read_text(encoding="utf-8")
        if re.search(r'^revision: str = "0020"$', text, re.MULTILINE):
            claims_0020.append(path.name)
        if re.search(r'^down_revision: str \| None = "0019"$', text, re.MULTILINE):
            revises_0019.append(path.name)

    assert claims_0020 == ["0020_vendor_channel_submissions.py"]
    assert revises_0019 == ["0020_vendor_channel_submissions.py"]


def test_0020_e_revisada_so_pela_proxima_quando_ela_existir() -> None:
    """0020 has exactly ONE child — the GP11 wiring store (0021, VW4); nothing else claims it."""
    revising = [
        path.name
        for path in sorted(_VERSIONS_DIR.glob("0*.py"))
        if re.search(r'^down_revision: str \| None = "0020"$', path.read_text(encoding="utf-8"), re.MULTILINE)
    ]
    assert revising == ["0021_vendor_suppression_records.py"]


def test_a_business_key_nomeada_e_a_primary_key() -> None:
    """NO-DUPLICATE-INSTANCE is the storage truth: the registry OP16 key IS the PK."""
    assert f"CREATE TABLE {_TABLE}" in _CODE
    assert "PRIMARY KEY (tenant, operation, submission_ref, business_revision)" in _CODE
    assert "CHECK (operation = 'channel_submission')" in _CODE
    assert "business_revision text NOT NULL CHECK (business_revision ~ '^(0|[1-9][0-9]*)$')" in _CODE


def test_a_identidade_da_instancia_e_unica_e_opaca() -> None:
    assert "submission_id text NOT NULL UNIQUE CHECK (submission_id <> '')" in _CODE
    assert "channel_ref text NOT NULL CHECK (channel_ref <> '')" in _CODE
    assert "tenant text NOT NULL CHECK (tenant <> '')" in _CODE


def test_o_ciclo_e_fechado_e_a_recusa_e_comprovada_no_storage() -> None:
    # Whitespace-normalized: the CLOSED cycle and the refusal invariant are storage truth,
    # regardless of how the DDL wraps its lines.
    flat = " ".join(_CODE.split()).replace("( ", "(").replace(" )", ")")
    assert (
        "lifecycle text NOT NULL CHECK (lifecycle IN ('received', 'documents_pending', "
        "'responded', 'refused'))" in flat
    )
    assert "CHECK ((lifecycle = 'refused') = (refusal_code IS NOT NULL))" in flat


def test_o_digest_e_sha256_e_nada_de_conteudo_de_payload() -> None:
    assert "command_digest text NOT NULL CHECK (command_digest ~ '^[0-9a-f]{64}$')" in _CODE
    # The store carries the DECLARED class token only — never payload content.
    assert "payload_class text NOT NULL CHECK (payload_class <> '')" in _CODE
    for conteudo in ("answers", "form_content", "body jsonb", "payload jsonb"):
        assert conteudo not in _CODE


def test_os_selos_de_tempo_sao_tz_aware() -> None:
    assert "created_at timestamptz NOT NULL" in _CODE
    assert "updated_at timestamptz NOT NULL" in _CODE


@pytest.mark.parametrize(
    "inventado",
    [
        "ciphertext",
        "wrapped_key",
        "nonce",
        "kms",
        "aes",
        "expires_at",
        "INTERVAL '",
        "pg_cron",
        "DELETE FROM",
        "DROP PARTITION",
    ],
)
def test_nenhuma_cifra_ou_retencao_e_inventada(inventado: str) -> None:
    """Cipher material and retention deadlines are RATIFY-LATER DPO decisions."""
    assert inventado.lower() not in _CODE.lower(), (
        f"`{inventado}` must never appear in the vendor submission store: the OP16 store keeps "
        "instance identity and stage only and invents neither a cipher nor a retention deadline"
    )


def test_a_tabela_nao_e_legivel_para_o_publico() -> None:
    assert f"REVOKE ALL ON TABLE {_TABLE} FROM PUBLIC" in _CODE


def test_o_downgrade_e_honesto_e_recusa_store_populado() -> None:
    downgrade = _SOURCE.split("def downgrade()", 1)[1]
    assert "IF EXISTS (SELECT 1 FROM portal_vendor_submissions) THEN" in downgrade
    assert "RAISE EXCEPTION 'populated vendor submission store" in downgrade
    assert f"DROP TABLE {_TABLE}" in downgrade
    for foreign in (
        "portal_memberships",
        "portal_sessions",
        "portal_vendor_channels",
        "v21_journey_journal",
        "amh_inbox",
    ):
        assert foreign not in _CODE
