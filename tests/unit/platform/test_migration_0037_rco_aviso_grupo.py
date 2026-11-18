"""Estrutura da migracao 0037 (`rco_aviso_grupo` e o CHECK `fila_sem_grupo` do outbox, DL-0186) — sem banco.

A execucao real (cadeia, insercao idempotente, imutabilidade, CHECKs, downgrade com e sem `fila_sem_grupo`)
e' provada ao vivo em `test_rco_aviso_grupo_live_pg.py`. Aqui ficam as invariantes anti-regressao: cadeia,
nenhuma coluna de beneficiario ou texto livre, o destino do inbox grava exatamente as colunas do DDL, as
formas de token iguais as da 0036, a imutabilidade por trigger, o CHECK alargado e o downgrade explicito.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

import maezo
from maezo.platform import rco_avisos
from maezo.platform.integrations import notifications_inbox as inbox_mod

_ROOT = Path(__file__).resolve().parents[3]
_VERSIONS = Path(maezo.__file__).parent / "platform" / "migrations" / "versions"
_SOURCE = (_VERSIONS / "0037_rco_aviso_grupo.py").read_text(encoding="utf-8")
_CODE = _SOURCE.split('"""', 2)[2]
_CODE_0036 = (_VERSIONS / "0036_rco_aviso_auditoria.py").read_text(encoding="utf-8").split('"""', 2)[2]
_DDL = _CODE.split("_CREATE = ", 1)[1].split('"""', 2)[1]


def test_cadeia_0037_revisa_a_0036_e_e_a_head() -> None:
    script = ScriptDirectory.from_config(Config(str(_ROOT / "alembic.ini")))
    rev = script.get_revision("0037")
    assert rev is not None and rev.down_revision == "0036"
    assert script.get_heads() == ["0037"]


@pytest.mark.parametrize(
    "coluna",
    ["cpf", "cns", "nome", "beneficiario_pseudo_id", "business_key", "chave_negocio", "payload", "mensagem"],
)
def test_nenhuma_coluna_de_beneficiario_chave_de_negocio_ou_texto_livre(coluna: str) -> None:
    assert not re.search(rf"^\s+{coluna}\b", _DDL, re.MULTILINE), coluna


def _colunas_do_ddl() -> list[str]:
    return re.findall(r"^\s{4}([a-z_]+) (?:text|timestamptz)\b", _DDL, re.MULTILINE)


def test_o_destino_do_inbox_grava_exatamente_as_colunas_do_ddl() -> None:
    insert = re.search(r"INSERT INTO rco_aviso_grupo \(([^)]*)\)", inbox_mod._INSERT_AVISO_RCO_SQL)
    assert insert is not None
    gravadas = [c.strip() for c in insert.group(1).split(",")]
    assert set(_colunas_do_ddl()) == {*gravadas, "registrado_em"}  # o carimbo vem do DEFAULT
    assert "RETURNING registrado_em" in inbox_mod._INSERT_AVISO_RCO_SQL


_TENANT = "tenant <> '' AND char_length(tenant) <= 512 AND tenant !~ '[[:space:][:cntrl:]/?#]'"


@pytest.mark.parametrize("coluna", ["chave_idempotencia", "task_id", "composicao", "sinal", "fila"])
def test_formas_iguais_as_da_0036(coluna: str) -> None:
    def forma(code: str) -> str:
        linha = re.search(rf"^\s+{coluna} text NOT NULL CHECK \((.*)$", code, re.MULTILINE)
        assert linha is not None, coluna
        return linha.group(1)

    assert forma(_CODE) == forma(_CODE_0036)
    assert _TENANT in _CODE and _TENANT in _CODE_0036


def test_prazo_grupo_audience_e_chave_primaria() -> None:
    assert "prazo_parametro text NOT NULL CHECK (prazo_parametro ~ '^[A-Za-z0-9._:-]{{1,128}}$')" in _DDL
    assert "grupo text NOT NULL CHECK (grupo ~ {_TOKEN})" in _DDL
    assert "_TOKEN = \"'^[A-Za-z0-9._-]{1,64}$'\"" in _CODE
    assert "notice_ref text NOT NULL CHECK (notice_ref ~ '^[0-9a-f]{{64}}$')" in _DDL
    assert "audience text NOT NULL DEFAULT 'staff' CHECK (audience = 'staff')" in _DDL
    assert "PRIMARY KEY (tenant, notice_ref)" in _DDL
    assert inbox_mod.NOTICE_AUDIENCE == "staff"


def test_imutavel_por_trigger_e_sem_grant() -> None:
    up = _CODE.split("def upgrade")[1].split("def downgrade")[0]
    assert "BEFORE UPDATE OR DELETE ON rco_aviso_grupo" in up
    assert "BEFORE TRUNCATE ON rco_aviso_grupo" in up
    assert "REVOKE ALL ON TABLE rco_aviso_grupo FROM PUBLIC" in up
    assert "REVOKE ALL ON FUNCTION rco_aviso_grupo_imutavel() FROM PUBLIC" in up
    assert not re.search(r"\bGRANT\b", _CODE)


def test_o_check_do_outbox_ganha_fila_sem_grupo_sem_editar_a_0036() -> None:
    novo = re.search(r'_ULTIMO_ERRO_0037 = "ultimo_erro IN \(([^)]*)\)"', _CODE)
    assert novo is not None
    assert {s.strip().strip("'") for s in novo.group(1).split(",")} == rco_avisos.ERROS
    assert rco_avisos.ERRO_FILA_SEM_GRUPO == "fila_sem_grupo"
    antigo = re.search(r'_ULTIMO_ERRO_0036 = "ultimo_erro IN \(([^)]*)\)"', _CODE)
    assert antigo is not None
    assert {s.strip().strip("'") for s in antigo.group(1).split(",")} == {rco_avisos.ERRO_PUBLICACAO}
    assert "ultimo_erro text CHECK (ultimo_erro IN ('publicacao_falhou'))" in _CODE_0036
    assert '_CHECK_ULTIMO_ERRO = "rco_aviso_auditoria_ultimo_erro_check"' in _CODE


def test_downgrade_recusa_fila_sem_grupo_antes_de_mudar_qualquer_coisa() -> None:
    down = _CODE.split("def downgrade")[1]
    passos = re.findall(r"op\.execute\(([^\n]*)", down)
    assert passos[0].startswith("_RECUSAR_DOWNGRADE_COM_SEM_GRUPO")
    assert "RAISE EXCEPTION" in _CODE and "WHERE ultimo_erro = 'fila_sem_grupo'" in _CODE
    assert '"DROP TABLE rco_aviso_grupo"' in down and '"DROP FUNCTION rco_aviso_grupo_imutavel()"' in down
    assert "UPDATE rco_aviso_auditoria" not in _CODE  # nunca reescreve o codigo gravado
    assert "DELETE FROM rco_aviso_auditoria" not in _CODE
