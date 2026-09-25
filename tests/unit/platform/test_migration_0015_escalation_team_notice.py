"""Structural tests for migration `0015_escalation_team_notice` — the DDL half of WP-J1-09.

These run without Postgres. They are not a substitute for EXECUTING the migration (that proof is
the engine runner's); they are the ANTI-REGRESSION half — the properties that must stay true
about this DDL, expressed so a later edit which quietly breaks one fails CI instead of failing a
human in a queue.

Four invariants:

1.  **Chain integrity.** `0015` revises `0014`, is the head, and no other migration claims either
    slot. A forked chain is the failure mode where `alembic upgrade head` applies one branch while
    an operator believes it applied the other.
2.  **PHI posture.** No clinical column, no free text, no payload, no beneficiary reference. The
    notification never carried any of them and this table must never grow a place to put them.
3.  **The row is addressed to a STAFF GROUP and cannot be addressed to anything else.** The
    `audience` CHECK admits exactly `'staff'`, so an escalation notice can never be written for a
    beneficiary or a provider even by a caller that tries.
4.  **`downgrade()` is honest.** It drops exactly what `upgrade()` created and touches no table
    from 0001..0014.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import maezo

# `versions/` is a namespace package (no `__init__.py`), so `__file__` is None there — resolve
# from the installed package root instead, the way `amh_inbox.migration_file_path` does.
_VERSIONS_DIR = Path(maezo.__file__).parent / "platform" / "migrations" / "versions"
_MIGRATION = _VERSIONS_DIR / "0015_escalation_team_notice.py"
_SOURCE = _MIGRATION.read_text(encoding="utf-8")

#: The DDL and its inline comments, with the module docstring stripped. The column fences below
#: assert what the TABLE contains, so they must read the code — the docstring legitimately uses
#: words like "payload" and "beneficiary" to explain what is deliberately ABSENT, and matching
#: those would turn an accurate explanation into a test failure.
_CODE = _SOURCE.split('"""', 2)[2]

#: Columns that must never exist here. The first group is clinical/free-text (ADR-0006); the
#: second is any re-materialisation of the notification body this table deliberately does not
#: store; the third is the subject reference the notification never carried.
_FORBIDDEN_COLUMNS: frozenset[str] = frozenset(
    {
        "justificativa_clinica",
        "cid10_referencia",
        "fundamentacao_dut",
        "resumo_contexto",
        "payload",
        "mensagem",
        "corpo",
        "body",
        "beneficiario_pseudo_id",
        "titular_pseudo_id",
        "cpf",
        "cns",
        "nome",
    }
)

#: Columns the delivery contract requires. Non-vacuity for the fence above.
_REQUIRED_COLUMNS: frozenset[str] = frozenset(
    {
        "tenant",
        "notice_ref",
        "business_key",
        "audience",
        "grupo_atendimento",
        "severidade",
        "prioridade",
        "motivo_categoria",
        "recorded_at",
    }
)


def test_a_migracao_existe_e_encadeia_em_0014() -> None:
    assert _MIGRATION.is_file()
    assert re.search(r'^revision: str = "0015"$', _SOURCE, re.MULTILINE)
    assert re.search(r'^down_revision: str \| None = "0014"$', _SOURCE, re.MULTILINE)


def test_a_cadeia_nao_bifurca() -> None:
    """Exactly one migration claims revision `0015`, and exactly one revises `0014`."""
    claims_0015: list[str] = []
    revises_0014: list[str] = []
    for path in sorted(_VERSIONS_DIR.glob("0*.py")):
        text = path.read_text(encoding="utf-8")
        if re.search(r'^revision: str = "0015"$', text, re.MULTILINE):
            claims_0015.append(path.name)
        if re.search(r'^down_revision: str \| None = "0014"$', text, re.MULTILINE):
            revises_0014.append(path.name)

    assert claims_0015 == ["0015_escalation_team_notice.py"]
    assert revises_0014 == ["0015_escalation_team_notice.py"]


def test_0015_e_revisada_so_pela_0016() -> None:
    """GAP-XHITL-4 disse deliberadamente: `0016_recipient_custody` revisa `0015`, e so' ela."""
    revising = [
        path.name
        for path in sorted(_VERSIONS_DIR.glob("0*.py"))
        if re.search(r'^down_revision: str \| None = "0015"$', path.read_text(encoding="utf-8"), re.MULTILINE)
    ]
    assert revising == ["0016_recipient_custody.py"]


@pytest.mark.parametrize("coluna", sorted(_FORBIDDEN_COLUMNS))
def test_nenhuma_coluna_clinica_de_texto_livre_ou_de_sujeito(coluna: str) -> None:
    """PHI posture, per column so a failure names the offender."""
    assert not re.search(rf"\b{re.escape(coluna)}\b", _CODE), (
        f"`{coluna}` must never be a column of escalation_team_notice: the notify_team "
        "notification carries only bounded routing tokens plus the engine's own business key"
    )


@pytest.mark.parametrize("coluna", sorted(_REQUIRED_COLUMNS))
def test_as_colunas_do_contrato_de_entrega_estao_presentes(coluna: str) -> None:
    """Non-vacuity: the fence above would pass trivially on an empty file."""
    assert re.search(rf"^\s+{re.escape(coluna)}\s", _CODE, re.MULTILINE)


def test_o_aviso_so_pode_ser_enderecado_a_staff() -> None:
    """The audience CHECK is an EQUALITY, not a set: there is no route in this schema by which a
    beneficiary or a provider could receive an escalation notice."""
    assert "CHECK (audience = 'staff')" in _CODE
    assert "beneficiary" not in _CODE
    assert "provider" not in _CODE


def test_a_identidade_da_linha_e_um_digest_e_a_chave_primaria() -> None:
    """`notice_ref` is content-derived (the module computes it) and the DDL pins its SHAPE, so a
    caller cannot smuggle a business identifier into the key column."""
    assert "notice_ref text NOT NULL CHECK (notice_ref ~ '^[0-9a-f]{64}$')" in _CODE
    assert "PRIMARY KEY (tenant, notice_ref)" in _CODE


def test_a_prioridade_e_restringida_ao_dominio_da_dmn() -> None:
    """Defence in depth: the publisher already validates `prioridade` against `{P1,P2,P3}`, and a
    corrupted value must be a WRITE FAILURE here rather than a wrong row in a human's queue."""
    assert "CHECK (prioridade IN ('P1','P2','P3'))" in _CODE


def test_a_linha_e_imutavel_por_trigger() -> None:
    """A notice records what was published. Correcting one means publishing a NEW notification
    (a new `notice_ref`), never rewriting a line a human may already have acted on."""
    assert "BEFORE UPDATE OR DELETE ON" in _CODE
    assert "BEFORE TRUNCATE ON" in _CODE
    assert "RAISE EXCEPTION 'immutable escalation team notice'" in _CODE


def test_a_fila_do_grupo_tem_indice() -> None:
    """The read a staff queue actually performs must not be a sequential scan."""
    assert "(tenant, grupo_atendimento, recorded_at DESC)" in _CODE


def test_o_downgrade_e_honesto() -> None:
    """Drops exactly what upgrade created; names no table from an earlier migration."""
    downgrade = _SOURCE.split("def downgrade()", 1)[1]
    assert "DROP TABLE escalation_team_notice" in downgrade
    assert "DROP FUNCTION escalation_team_notice_immutable()" in downgrade
    for foreign in (
        "amh_inbox",
        "audit_chain",
        "human_command_outbox",
        "portal_assignment_source",
        "portal_sessions",
    ):
        assert foreign not in _CODE


def test_nao_ha_retencao_inventada() -> None:
    """Retention values belong to the DPO/security matrix. This migration must not invent one."""
    for inventado in ("DROP PARTITION", "pg_cron", "INTERVAL '", "DELETE FROM"):
        assert inventado not in _CODE
