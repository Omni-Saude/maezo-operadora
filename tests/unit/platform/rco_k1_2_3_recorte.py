"""Carga do recorte REAL do K1 2.3.0 do rco (`tests/fixtures/rco/k1-2.3.0-recorte/`, d9b8adfa; DL-0183).

O recorte (procedencia em `ORIGEM.json` e no `README.md` da pasta) traz o manifesto inteiro, sem alteracao,
`vocabulario.json`, `k6-filas.json`, as duas decisoes da composicao `composicao_carencia_cpt` (regra
`mais_restritiva`), os casos-teste do rco por decisao e os casos do GRUPO
(`validacao/composicao/regras-gerais/composicao_carencia_cpt.json`), e o recorte do dicionario de entradas
1.4.0. Aqui fica so a carga; os testes unitarios e o de integracao (motor real) usam o mesmo codigo.

O `esperado` de um caso do grupo traz `decisao`, `decisao_de` (a decisao vencedora, sem o prefixo do pacote),
`idRegra`, `motivoTab38` e `fila` quando o rco os declara, e `sinais` (a uniao dos `sinalAuditoria` nao
vazios). A comparacao de cada campo e a do runner do rco (`rco_k1_2_2_recorte.iguais`: nulo == `""`).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from maezo.platform import rco_manifesto as k1
from maezo.platform.rco_entradas import DicionarioEntradasK1, ler_dicionario_entradas
from tests.unit.platform.rco_k1_2_2_recorte import iguais

RECORTE = Path(__file__).resolve().parents[2] / "fixtures" / "rco" / "k1-2.3.0-recorte"
ORIGEM = RECORTE / "ORIGEM.json"
COMMIT_RCO = "d9b8adfafc0d7cf92db32cf255f61cfc8ae45fa7"
COMPOSICAO = "composicao_carencia_cpt"
#: A ordem documentada (contrato-k1.md §3), com o prefixo do pacote.
ORDEM = ("rco_geral_carencia", "rco_geral_cpt")
DOCUMENTACAO = frozenset({"ORIGEM.json", "README.md"})
#: A forma acordada com a trilha rco (T-MZO-A, K1 2.3.0): ordem de restricao, vocabulario do sinal e o aviso.
ORDEM_RESTRICAO_ACORDADA = (
    "negar",
    "glosar",
    "recomendar_negativa",
    "revisao_humana",
    "exigir_autorizacao",
    "exigir_documento",
    "limitar",
    "pagar_referencia",
    "pagar_pacote",
    "prazo",
    "informar",
    "liberar",
)
SINAIS_ACORDADOS = ("", "aviso_urgencia_carencia")


def origem() -> dict[str, Any]:
    dados: dict[str, Any] = json.loads(ORIGEM.read_text(encoding="utf-8"))
    return dados


def manifesto() -> k1.ManifestoK1:
    return k1.ler_manifesto((RECORTE / "manifesto.json").read_bytes())


def composicao() -> k1.ComposicaoK1:
    return next(c for c in manifesto().composicoes if c.nome == COMPOSICAO)


def vocabulario() -> k1.VocabularioPacoteK1:
    return k1.carregar_vocabulario(manifesto(), k1_dir=RECORTE)


def dicionario() -> DicionarioEntradasK1:
    return ler_dicionario_entradas((RECORTE / "entradas-recorte.json").read_bytes())


def dmn(decisao_id: str) -> Path:
    return RECORTE / manifesto().decisoes[decisao_id].arquivo


def sha256_prefixado(caminho: Path) -> str:
    return "sha256:" + hashlib.sha256(caminho.read_bytes()).hexdigest()


@dataclass(frozen=True, slots=True)
class CasoRco:
    decisao: str  # id no pacote (`rco_` + `decisao` do arquivo de casos)
    arquivo: str
    id: str
    descricao: str
    entradas: Mapping[str, Any]
    esperado: Mapping[str, Any]

    @property
    def chave(self) -> str:
        return f"{self.decisao}#{self.id}"


def casos() -> list[CasoRco]:
    """Os casos do rco POR DECISAO, na ordem do grupo e do arquivo."""
    out: list[CasoRco] = []
    for decisao in ORDEM:
        original = decisao.removeprefix(k1.PREFIXO_DECISAO)
        rel = f"validacao/casos/regras-gerais/{original}.json"
        dados = json.loads((RECORTE / rel).read_text(encoding="utf-8"))
        assert dados["decisao"] == original, rel
        out.extend(
            CasoRco(decisao, rel, c["id"], c["descricao"], c["entradas"], c["esperado"])
            for c in dados["casos"]
        )
    return out


def casos_do_grupo() -> list[CasoRco]:
    """Os casos do GRUPO (`decisao` = o nome da composicao)."""
    rel = f"validacao/composicao/regras-gerais/{COMPOSICAO}.json"
    dados = json.loads((RECORTE / rel).read_text(encoding="utf-8"))
    assert (dados["grupo"], dados["regra"]) == (COMPOSICAO, k1.REGRA_MAIS_RESTRITIVA), rel
    return [
        CasoRco(COMPOSICAO, rel, c["id"], c["descricao"], c["entradas"], c["esperado"])
        for c in dados["casos"]
    ]


def diferencas_do_grupo(
    esperado: Mapping[str, Any], vencedora: str | None, linha: Mapping[str, Any], sinais: tuple[str, ...]
) -> list[str]:
    """Campos do `esperado` de um caso do grupo que o resultado nao satisfaz (vazio = casa)."""
    erros: list[str] = []
    for chave, valor in esperado.items():
        if chave == "decisao_de":
            obtido: Any = vencedora.removeprefix(k1.PREFIXO_DECISAO) if vencedora else None
            if obtido != valor:
                erros.append(f"decisao_de: esperado={valor!r} obtido={obtido!r}")
        elif chave == "sinais":
            if list(sinais) != list(valor):
                erros.append(f"sinais: esperado={valor!r} obtido={list(sinais)!r}")
        elif not iguais(valor, linha.get(chave)):
            erros.append(f"{chave}: esperado={valor!r} obtido={linha.get(chave)!r}")
    return erros
