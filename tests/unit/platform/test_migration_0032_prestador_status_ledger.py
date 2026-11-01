"""Estrutura da migracao 0032 (`prestador_status_ledger`) + espelho Python dos CHECKs — sem banco.

A execucao real (cadeia alembic, triggers append-only, UNIQUE de idempotencia) e' provada ao vivo
em `test_prestador_status_ledger_live_pg.py`. Aqui ficam as invariantes anti-regressao:
cadeia, nenhuma coluna de beneficiario, append-only declarado, e `EntradaLedger` recusando antes
da rede o mesmo que o banco recusaria.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

import maezo
from maezo.platform.prestador_status.ledger import EntradaLedger
from maezo.platform.prestador_status.projecao import Dimensao, Origem

_ROOT = Path(__file__).resolve().parents[3]
_VERSIONS = Path(maezo.__file__).parent / "platform" / "migrations" / "versions"
_SOURCE = (_VERSIONS / "0032_prestador_status_ledger.py").read_text(encoding="utf-8")
_CODE = _SOURCE.split('"""', 2)[2]


def test_cadeia_0032_revisa_a_head_da_main() -> None:
    script = ScriptDirectory.from_config(Config(str(_ROOT / "alembic.ini")))
    rev = script.get_revision("0032")
    assert rev is not None
    # Se a 0031 do PR #1074 entrar antes, o orquestrador re-aponta para "0031" (docstring da 0032).
    assert rev.down_revision == "0030"
    assert script.get_heads() == ["0032"]


@pytest.mark.parametrize(
    "coluna",
    [
        "cpf",
        "cns",
        "nome",
        "beneficiario_pseudo_id",
        "titular_pseudo_id",
        "cid10",
        "payload",
        "motivo_informado",
    ],
)
def test_nenhuma_coluna_de_beneficiario_ou_texto_livre(coluna: str) -> None:
    assert not re.search(rf"\b{coluna}\b", _CODE), coluna


def test_append_only_por_trigger_e_sem_grant() -> None:
    assert "BEFORE UPDATE OR DELETE ON" in _CODE
    assert "BEFORE TRUNCATE ON" in _CODE
    assert "REVOKE ALL ON TABLE prestador_status_ledger FROM PUBLIC" in _CODE
    assert not re.search(r"\bGRANT\b", _CODE)


def test_idempotencia_e_tenant_declarados_no_ddl() -> None:
    assert "UNIQUE (tenant, chave_evento)" in _CODE
    assert "tenant text NOT NULL" in _CODE


def test_estados_de_leitura_nao_sao_gravaveis_no_ddl() -> None:
    for proibido in ("DESCONHECIDO", "INDISPONIVEL", "SUSPENSO", "DESCRED_EM_CURSO", "ABERTA"):
        assert f"'{proibido}'" not in _CODE


def _entrada(**campos: object) -> EntradaLedger:
    base: dict[str, object] = {
        "tenant": "amh",
        "prestador_id": "P-1",
        "origem": Origem.CRED,
        "dimensao": Dimensao.SITUACAO,
        "valor": "CREDENCIADO",
        "chave_evento": "CRED:i:End_PrestadorCredenciado",
        "fonte_ref": "i",
        "evidencia_ref": "CRED-amh-P-1",
        "versao_projecao": "mptt-p3-projecao/v1",
    }
    base.update(campos)
    return EntradaLedger(**base)  # type: ignore[arg-type]


def test_entrada_valida() -> None:
    assert _entrada().valor == "CREDENCIADO"


@pytest.mark.parametrize(
    "campos",
    [
        {"valor": "DESCONHECIDO"},
        {"valor": "INDISPONIVEL"},
        {"valor": "CONFIRMADA"},  # valor de fraude na dimensao situacao
        {"origem": Origem.FRAUDE},  # FRAUDE nao fala de situacao
        {"origem": Origem.CRED, "dimensao": Dimensao.FRAUDE, "valor": "CONFIRMADA"},
        {"tenant": "amh outro"},
        {"prestador_id": "P 1"},
        {"chave_evento": "CRED i"},
        {"evidencia_ref": ""},
        {"tipo_prestador": "Clinica"},
        {"especialidade": "x" * 129},
        {"regiao_saude": "linha\nquebrada"},
    ],
)
def test_entrada_fora_do_formato_e_recusada_antes_da_rede(campos: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        _entrada(**campos)
