"""Estrutura da migracao 0036 (`rco_aviso_auditoria`, DL-0185) — sem banco.

A execucao real (cadeia alembic, idempotencia, CHECKs, lease, downgrade) e' provada ao vivo em
`test_rco_aviso_auditoria_live_pg.py`. Aqui ficam as invariantes anti-regressao: cadeia, nenhuma coluna de
beneficiario ou texto livre, o store grava exatamente as colunas do DDL, estados e codigo de erro fechados.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

import maezo
from maezo.platform import rco_avisos

_ROOT = Path(__file__).resolve().parents[3]
_VERSIONS = Path(maezo.__file__).parent / "platform" / "migrations" / "versions"
_SOURCE = (_VERSIONS / "0036_rco_aviso_auditoria.py").read_text(encoding="utf-8")
_CODE = _SOURCE.split('"""', 2)[2]


def test_cadeia_0036_revisa_a_0035() -> None:
    script = ScriptDirectory.from_config(Config(str(_ROOT / "alembic.ini")))
    rev = script.get_revision("0036")
    assert rev is not None and rev.down_revision == "0035"
    # DL-0186: a 0037 (linha de grupo dos avisos rco + `fila_sem_grupo`) empilha sobre esta.
    assert script.get_heads() == ["0037"]
    # a 0031 ficou vaga (reservada ao PR #1074, que entrou como 0033); nada a reaproveitar
    assert "0031" not in {r.revision for r in script.walk_revisions()}


@pytest.mark.parametrize(
    "coluna",
    ["cpf", "cns", "nome", "beneficiario_pseudo_id", "business_key", "chave_negocio", "payload", "mensagem"],
)
def test_nenhuma_coluna_de_beneficiario_chave_de_negocio_ou_texto_livre(coluna: str) -> None:
    assert not re.search(rf"^\s+{coluna}\b", _CODE, re.MULTILINE), coluna


def test_o_store_grava_exatamente_colunas_do_ddl() -> None:
    for coluna in (*rco_avisos.COLUNAS, "status", "tentativas", "ultimo_erro", "entregue_em"):
        assert re.search(rf"^\s+{coluna}\b", _CODE, re.MULTILINE), coluna


def test_estados_e_erro_fechados_no_ddl_e_no_codigo() -> None:
    estados = re.search(r"status IN \(([^)]*)\)", _CODE)
    assert estados is not None
    assert {s.strip().strip("'") for s in estados.group(1).split(",")} == rco_avisos.STATUS
    erros = re.search(r"ultimo_erro IN \(([^)]*)\)", _CODE)
    assert erros is not None
    # A 0036 nasceu com `publicacao_falhou`; a 0037 (DL-0186) troca o CHECK e acrescenta `fila_sem_grupo`
    # (`ERROS` inteiro e' conferido contra a 0037 em `test_migration_0037_rco_aviso_grupo.py`).
    assert {s.strip().strip("'") for s in erros.group(1).split(",")} == {rco_avisos.ERRO_PUBLICACAO}
    assert rco_avisos.ERRO_PUBLICACAO in rco_avisos.ERROS


def test_idempotencia_lease_e_entrega_declarados() -> None:
    assert "CONSTRAINT rco_aviso_auditoria_idempotencia UNIQUE (tenant, chave_idempotencia)" in _CODE
    assert "(status = 'entregue') = (entregue_em IS NOT NULL)" in _CODE
    assert "(status = 'reivindicada') = (reivindicacao_expira_em IS NOT NULL)" in _CODE
    assert "WHERE status IN ('pendente', 'reivindicada')" in _CODE
    assert not re.search(r"\bGRANT\b", _CODE)


def test_downgrade_apaga_so_a_tabela() -> None:
    down = _CODE.split("def downgrade")[1]
    assert re.findall(r'op\.execute\("([^"]*)"\)', down) == ["DROP TABLE rco_aviso_auditoria"]
