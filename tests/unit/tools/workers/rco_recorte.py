"""Carga do recorte REAL do K1 usado pelos testes do montador de entradas (MZO-2b).

`tests/fixtures/rco/k1-1.1.0-recorte/` e um RECORTE do pacote K1 gerado do rco no commit
fe1dc8975c0a486da0699f0fd6a0279e4b70f9af (dicionario de entradas 1.1.0, modo nao publicacao), para os
dominios `regras-gerais` e `diarias-taxas`: `entradas.json` (so as entradas desses dominios; cada
entrada como o pacote a escreve, mas `sha256_conteudo` e o hash do dicionario COMPLETO, nao do
recorte), `decisoes.json` (id, dominio, entradas e saidas das decisoes, recortados do manifesto;
procedencia no proprio arquivo) e `geral_carencia.dmn` (o arquivo da tabela de carencia do pacote, id
`rco_geral_carencia`). Material de teste: nada disso e
regra do Maezo nem entra em `spec/processes/dmn/rco/` (o pin segue `sem_pacote`).
"""

from __future__ import annotations

import json
from pathlib import Path
from types import MappingProxyType

from maezo.platform.rco_entradas import DicionarioEntradasK1, ler_dicionario_entradas
from maezo.platform.rco_manifesto import DecisaoK1

RECORTE = Path(__file__).resolve().parents[3] / "fixtures" / "rco" / "k1-1.1.0-recorte"
CARENCIA_DMN = RECORTE / "geral_carencia.dmn"


def dicionario() -> DicionarioEntradasK1:
    return ler_dicionario_entradas((RECORTE / "entradas.json").read_bytes())


def decisoes() -> dict[str, DecisaoK1]:
    dados = json.loads((RECORTE / "decisoes.json").read_text(encoding="utf-8"))
    out: dict[str, DecisaoK1] = {}
    for d in dados["decisoes"]:
        original = d["id_original"]
        out[d["id"]] = DecisaoK1(
            id=d["id"],
            arquivo=f"dmn/{original}.dmn",
            caminho_consumidor=f"spec/processes/dmn/rco/{original}.dmn",
            sha256="sha256:" + "0" * 64,  # o montador nao le o sha
            saidas=tuple(d["saidas"]),
            regras=MappingProxyType({}),
            dominio=d["dominio"],
            entradas=tuple(d["entradas"]),
        )
    return out
