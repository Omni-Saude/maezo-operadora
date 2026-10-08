"""Structural tests for migration `0021_vendor_suppression_records` — the OP20 DDL (VW4 wiring).

Same posture as the 0019/0020 structural tests: no Postgres required (the live upgrade/downgrade/
upgrade proof is the integration lane); the properties that must stay true about this DDL are
expressed so a later edit which quietly breaks one fails CI instead of failing a human.

Invariants:

1.  **Chain integrity.** `0021` revises `0020`, is the head, and no other migration claims
    either slot.
2.  **The named business key IS the primary key.** NO-DUPLICATE-INSTANCE (registry OP20) is
    structural: `(tenant, suppression_ref)` — the digest projects `(subject_ref, contact_channel)`
    without losing a component (BPMN SP-OP-SUPP-001 business key by NAME).
3.  **The per-record clock is storage truth.** `t_deadline`/`t_lembrete`/`t_escalonado` are
    NOT NULL: a record without a computed clock does not exist (§4c fail-closed — "registro
    sem deadline = recusa do store"). The timestamps arrive COMPUTED by the application layer
    (own-code seg–sex calendar, NO holidays — documented limit); the DDL invents no SQL
    business-day logic, no INTERVAL of business days and no pg_cron.
4.  **No payload content, no cipher, no retention.** Minimization art. 10 §1º: identificador
    opaco + data + canal, nada mais; ZERO cifras and ZERO retention deadline (VW0-D12
    RATIFY-LATER DPO).
5.  **`downgrade()` is honest.** It refuses a populated store, drops exactly what `upgrade()`
    created and touches no other table.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import maezo

_VERSIONS_DIR = Path(maezo.__file__).parent / "platform" / "migrations" / "versions"
_MIGRATION = _VERSIONS_DIR / "0021_vendor_suppression_records.py"
_SOURCE = _MIGRATION.read_text(encoding="utf-8")
_CODE = _SOURCE.split('"""', 2)[2]

_TABLE = "portal_vendor_suppressions"


def test_a_migracao_existe_e_encadeia_em_0020() -> None:
    assert _MIGRATION.is_file()
    assert re.search(r'^revision: str = "0021"$', _SOURCE, re.MULTILINE)
    assert re.search(r'^down_revision: str \| None = "0020"$', _SOURCE, re.MULTILINE)


def test_a_cadeia_nao_bifurca() -> None:
    """Exactly one migration claims revision `0021`, and exactly one revises `0020`."""
    claims_0021: list[str] = []
    revises_0020: list[str] = []
    for path in sorted(_VERSIONS_DIR.glob("0*.py")):
        text = path.read_text(encoding="utf-8")
        if re.search(r'^revision: str = "0021"$', text, re.MULTILINE):
            claims_0021.append(path.name)
        if re.search(r'^down_revision: str \| None = "0020"$', text, re.MULTILINE):
            revises_0020.append(path.name)

    assert claims_0021 == ["0021_vendor_suppression_records.py"]
    assert revises_0020 == ["0021_vendor_suppression_records.py"]


def test_0021_e_revisada_so_pela_proxima_quando_ela_existir() -> None:
    """A later migration must be 0021's ONLY child (0022, DL-0083)."""
    revising = [
        path.name
        for path in sorted(_VERSIONS_DIR.glob("0*.py"))
        if re.search(r'^down_revision: str \| None = "0021"$', path.read_text(encoding="utf-8"), re.MULTILINE)
    ]
    assert revising == ["0022_conversa_acesso_beneficiario.py"]


def test_a_business_key_nomeada_e_a_primary_key() -> None:
    """NO-DUPLICATE-INSTANCE is the storage truth: the OP20 key collapses to the PK."""
    assert f"CREATE TABLE {_TABLE}" in _CODE
    assert "PRIMARY KEY (tenant, suppression_ref)" in _CODE
    assert "suppression_ref text NOT NULL CHECK (suppression_ref ~ '^[0-9a-f]{64}$')" in _CODE


def test_o_sujeito_e_opaco_e_o_canal_e_obrigatorio() -> None:
    # Minimização art. 10 §1º: identificador opaco + data + canal, nada mais.
    assert "subject_ref text NOT NULL CHECK (subject_ref <> '')" in _CODE
    assert "contact_channel text NOT NULL CHECK (contact_channel <> '')" in _CODE
    assert "canal text NOT NULL CHECK (canal <> '')" in _CODE
    assert "categoria_sujeito text NOT NULL CHECK (categoria_sujeito IN ('vendedor', 'lead'))" in _CODE


def test_o_relogio_por_registro_e_verdade_de_storage() -> None:
    """§4c: registro sem deadline computado NÃO é aceito — as três datas são NOT NULL."""
    flat = " ".join(_CODE.split())
    assert "t_deadline timestamptz NOT NULL" in flat
    assert "t_lembrete timestamptz NOT NULL" in flat
    assert "t_escalonado timestamptz NOT NULL" in flat
    assert "t_recorded timestamptz NOT NULL DEFAULT clock_timestamp()" in flat
    # O cálculo é CÓDIGO (calendário own-code seg–sex), nunca SQL: nenhum INTERVAL de negócio,
    # nenhuma função SQL de dia útil, nenhum agendador no banco.
    assert "INTERVAL" not in _CODE
    assert "pg_cron" not in _CODE
    assert "make_interval" not in _CODE


def test_o_ciclo_do_relogio_e_fechado_e_o_motivo_e_obrigatorio() -> None:
    flat = " ".join(_CODE.split())
    ciclo = "status text NOT NULL DEFAULT 'pendente' CHECK (status IN ('pendente', 'honrado', 'escalado'))"
    assert ciclo in flat
    assert "motivo text NOT NULL CHECK (motivo <> '')" in flat


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
        "historyTimeToLive",
    ],
)
def test_nenhuma_cifra_ou_retencao_e_inventada(inventado: str) -> None:
    """Cipher material and retention deadlines are RATIFY-LATER DPO decisions (VW0-D12)."""
    assert inventado.lower() not in _CODE.lower(), (
        f"`{inventado}` must never appear in the vendor suppression store: the OP20 store keeps "
        "identificador opaco + data + canal only and invents neither a cipher nor a retention deadline"
    )


def test_a_tabela_nao_e_legivel_para_o_publico() -> None:
    assert f"REVOKE ALL ON TABLE {_TABLE} FROM PUBLIC" in _CODE


def test_o_downgrade_e_honesto_e_recusa_store_populado() -> None:
    downgrade = _SOURCE.split("def downgrade()", 1)[1]
    assert "IF EXISTS (SELECT 1 FROM portal_vendor_suppressions) THEN" in downgrade
    assert "RAISE EXCEPTION 'populated vendor suppression store" in downgrade
    assert f"DROP TABLE {_TABLE}" in downgrade
    for foreign in (
        "portal_memberships",
        "portal_sessions",
        "portal_vendor_channels",
        "portal_vendor_submissions",
        "v21_journey_journal",
        "amh_inbox",
    ):
        assert foreign not in _CODE
