"""Migration 0024 (`consulta_valores` no CHECK de `conversa_agente_ativo`, DL-0086).

Revisao de seguranca do PR #709: o downgrade nao pode ficar PRESO ate' a purga de 30 dias do roteador
(um rollback urgente seria bloqueado por qualquer conversa viva). Ele apaga as linhas `consulta_valores`
com a contagem no log (`RAISE NOTICE`) e so' entao volta ao CHECK da 0017.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

_ARQUIVO = (
    Path(__file__).resolve().parents[3]
    / "src/maezo/platform/migrations/versions/0024_cobranca_subtipo_consulta_valores.py"
)


def _migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("migration_0024", _ARQUIVO)
    assert spec is not None and spec.loader is not None
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def _sql_de(fase: str, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    modulo = _migration()
    executado: list[str] = []
    monkeypatch.setattr(modulo.op, "execute", lambda sql: executado.append(str(sql)))
    getattr(modulo, fase)()
    return executado


def test_downgrade_apaga_as_linhas_consulta_valores_com_contagem_e_nao_fica_preso(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    primeiro, *resto = _sql_de("downgrade", monkeypatch)
    assert "DELETE FROM conversa_agente_ativo WHERE lucas_cobranca_subtipo = 'consulta_valores'" in primeiro
    assert "GET DIAGNOSTICS apagadas = ROW_COUNT" in primeiro
    assert "RAISE NOTICE" in primeiro
    assert "RAISE EXCEPTION" not in "".join([primeiro, *resto])
    # So' depois do DELETE o CHECK volta ao dominio da 0017 (sem `consulta_valores`).
    check = resto[-1]
    assert "ADD CONSTRAINT conversa_agente_ativo_lucas_cobranca_subtipo_check" in check
    assert "consulta_valores" not in check and "'cancelamento'" in check


def test_upgrade_so_alarga_o_check(monkeypatch: pytest.MonkeyPatch) -> None:
    sql = "".join(_sql_de("upgrade", monkeypatch))
    assert "'consulta_valores'" in sql
    assert "DELETE" not in sql and "UPDATE" not in sql
