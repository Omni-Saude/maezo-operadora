"""Fábrica de repositório mínimo e catálogo para os testes da chancela de regras (unit e CI)."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml

from maezo.regras.catalogo.hash import regra_sha256
from maezo.regras.catalogo.schema import validar_catalogo

PROCESSO = "SP-OP-TESTE-001"
ARQ_CATALOGO = f"spec/regras/catalogo/{PROCESSO}.yaml"


def regra_base(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "TESTE.FAIXA.TETO",
        "alias": "TST-01",
        "estado_declarado": "legado_pendente",
        "tipo": "parametro_politica",
        "fonte": [{"arquivo": "spec/policies/p.yaml", "caminho": ["acoes", "reembolso", "params", "teto"]}],
        "sha256": "0" * 64,
        "aprovam": ["financas", "dono"],
        "ciencia": ["po"],
        "titulo": "Teto do teste",
        "regra": "O teto do teste vale trezentos reais.",
        "exemplo": "A Ana pede R$ 250,00 e o pedido cabe no teto.",
        "orientacao": "Aceitar.",
        "o_que_e": "Aprovar o teto do teste.",
        "o_que_nao_e": "Nao liga a faixa automatica.",
        "norma": None,
        "validade": "P1Y",
        "editavel": [],
        "historico_de_mudanca": [],
        "aprovacoes": [],
        "liberacoes": [],
        "comprovacoes": [],
    }
    base.update(over)
    return base


def catalogo_base(*regras: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": "maezo-regras-catalogo.v1",
        "processo": PROCESSO,
        "regras": list(regras) or [regra_base()],
    }


def criar_repo(tmp_path: Path) -> Path:
    (tmp_path / "docs/processes/contracts").mkdir(parents=True)
    (tmp_path / f"docs/processes/contracts/{PROCESSO}.md").write_text("# Contrato\n", encoding="utf-8")
    (tmp_path / "spec/policies").mkdir(parents=True)
    (tmp_path / "spec/policies/p.yaml").write_bytes(
        b"acoes:\n  reembolso:\n    params:\n      teto: 300\n      limite: 2\n"
    )
    (tmp_path / "spec/regras/catalogo").mkdir(parents=True)
    (tmp_path / "spec/regras/normas").mkdir(parents=True)
    (tmp_path / "spec/regras/normas/RN566.txt").write_bytes(b"Art. 1 texto literal.\n")
    return tmp_path


def com_hash(repo: Path, catalogo: dict[str, Any]) -> dict[str, Any]:
    """Preenche `sha256` de cada regra com o valor recalculado (o catálogo tem de ser válido)."""
    saida = copy.deepcopy(catalogo)
    for bruto in saida["regras"]:
        bruto["sha256"] = "0" * 64
    validado, achados = validar_catalogo(saida, arquivo=ARQ_CATALOGO, raiz=repo)
    assert validado is not None, achados
    for bruto, regra in zip(saida["regras"], validado.regras, strict=True):
        bruto["sha256"] = regra_sha256(repo, regra)
    return saida


def gravar(repo: Path, catalogo: dict[str, Any], arquivo: str = ARQ_CATALOGO) -> None:
    (repo / arquivo).write_text(
        yaml.safe_dump(catalogo, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
