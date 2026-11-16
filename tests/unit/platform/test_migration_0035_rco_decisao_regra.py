"""Estrutura da migracao 0035 (`rco_decisao_regra` + `rco_decisao_regra_desfecho`) — sem banco.

A execucao real (cadeia alembic, triggers append-only, ordinal concorrente, idempotencia, downgrade)
e' provada ao vivo em `test_rco_decisao_regra_live_pg.py`. Aqui ficam as invariantes anti-regressao:
cadeia, nenhuma coluna de beneficiario ou texto livre, append-only declarado nas DUAS tabelas e as
colunas do ADR-0077 §4.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

import maezo
from maezo.platform import rco_registro

_ROOT = Path(__file__).resolve().parents[3]
_VERSIONS = Path(maezo.__file__).parent / "platform" / "migrations" / "versions"
_SOURCE = (_VERSIONS / "0035_rco_decisao_regra.py").read_text(encoding="utf-8")
_CODE = _SOURCE.split('"""', 2)[2]


def test_cadeia_0035_revisa_a_0034() -> None:
    script = ScriptDirectory.from_config(Config(str(_ROOT / "alembic.ini")))
    rev = script.get_revision("0035")
    assert rev is not None and rev.down_revision == "0034"
    assert script.get_heads() == ["0035"]


@pytest.mark.parametrize(
    "coluna",
    [
        "cpf",
        "cns",
        "nome",
        "beneficiario_pseudo_id",
        "chave_negocio",
        "business_key",
        "task_id",
        "payload",
        "cid10",
    ],
)
def test_nenhuma_coluna_de_beneficiario_chave_bruta_ou_texto_livre(coluna: str) -> None:
    assert not re.search(rf"^\s+{coluna}\b", _CODE, re.MULTILINE), coluna


def test_colunas_do_adr_0077_secao_4_estao_no_ddl() -> None:
    for coluna in (
        "id_regra",
        "versao_regra",
        "sha_dmn",
        "decision_key",
        "decisao",
        "classe_adverso",
        "motivo_tab38",
        "fila",
        "estado_manual",
        "ativacao_pacote",
        "ativacao_efetiva",
        "modo",
        "acao",
        "chave_negocio_pseudonimizada",
        "amostrada",
        "motivo_amostra",
        "id_chave_amostra",
        "ordinal",
        "criado_em",
        "concordou",
        "desfecho_em",
        "registrado_em",
    ):
        assert re.search(rf"^\s+{coluna}\b", _CODE, re.MULTILINE), coluna


def test_o_store_grava_exatamente_as_colunas_do_ddl() -> None:
    """O INSERT do store e o DDL nao divergem: toda coluna gravada existe na tabela."""
    for coluna in rco_registro.COLUNAS:
        assert re.search(rf"^\s+{coluna}\b", _CODE, re.MULTILINE), coluna


def test_append_only_por_trigger_e_sem_grant_nas_duas_tabelas() -> None:
    assert "BEFORE UPDATE OR DELETE ON" in _CODE and "BEFORE TRUNCATE ON" in _CODE
    assert "REVOKE ALL ON TABLE {tabela} FROM PUBLIC" in _CODE
    assert re.search(r'_append_only\("rco_decisao_regra"\)', _CODE)
    assert re.search(r'_append_only\("rco_decisao_regra_desfecho"\)', _CODE)
    assert not re.search(r"\bGRANT\b", _CODE)


def test_idempotencia_ordinal_e_desfecho_separado_declarados() -> None:
    assert "UNIQUE (tenant, chave_idempotencia)" in _CODE
    assert "CREATE UNIQUE INDEX rco_decisao_regra_ordinal_uidx" in _CODE
    assert "WHERE ordinal IS NOT NULL" in _CODE
    assert "REFERENCES rco_decisao_regra (id)" in _CODE
    # o desfecho NAO e coluna da tabela append-only da decisao
    decisao = _CODE.split("_CREATE_DESFECHO")[0]
    assert "concordou" not in decisao


def test_downgrade_apaga_a_filha_antes_da_mae() -> None:
    down = _CODE.split("def downgrade")[1]
    assert down.index("rco_decisao_regra_desfecho") < down.index('"DROP TABLE rco_decisao_regra"')
