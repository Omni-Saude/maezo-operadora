"""Guarda da copia vendorizada da fixture `teste-unario-booleano` do rco (commit 323fec5c).

A copia em `tests/fixtures/rco/provas/teste-unario-booleano/` e somente leitura: cada arquivo tem o
sha256 gravado em `ORIGEM.json` (o mesmo do rco) e nenhum arquivo pode sobrar ou faltar.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2] / "fixtures" / "rco" / "provas" / "teste-unario-booleano"
DOCUMENTACAO = {"ORIGEM.json", "README.md"}


def _origem() -> dict[str, object]:
    return json.loads((RAIZ / "ORIGEM.json").read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def test_origem_registra_commit_do_rco() -> None:
    origem = _origem()
    assert str(origem["origem_commit"]).startswith("323fec5c")
    assert origem["origem_repo"] == "Omni-Saude/regras-contratos-operadora"


def test_cada_arquivo_copiado_tem_o_sha256_do_rco() -> None:
    arquivos = _origem()["arquivos"]
    assert isinstance(arquivos, dict) and len(arquivos) == 11  # 1 DMN + cel01..cel10
    for rel, esperado in arquivos.items():
        obtido = "sha256:" + hashlib.sha256((RAIZ / rel).read_bytes()).hexdigest()
        assert obtido == esperado, f"{rel}: a copia da fixture do rco foi editada"


def test_nenhum_arquivo_fora_do_registro() -> None:
    arquivos = set(_origem()["arquivos"])  # type: ignore[call-overload]
    presentes = {
        p.relative_to(RAIZ).as_posix() for p in RAIZ.rglob("*") if p.is_file() and p.name not in DOCUMENTACAO
    }
    assert presentes == arquivos
