"""Carga do recorte REAL do pacote K1 2.2.0 do rco (`tests/fixtures/rco/k1-2.2.0-recorte/`, a1978375).

O recorte (procedencia em `ORIGEM.json` e no `README.md` da pasta) traz o manifesto inteiro, sem
alteracao, as tres decisoes da composicao `pares_mutuamente_exclusivos_ortop` e os casos-teste do rco
para elas. Aqui ficam so a carga e a expectativa da composicao DERIVADA do que o rco declara, sem motor;
o teste unitario e o de integracao (motor real) usam o mesmo codigo.

EXPECTATIVA DA COMPOSICAO. Os casos do rco sao POR DECISAO (`validacao/casos/<dominio>/<decisao>.json`:
o `esperado` e a saida daquela decisao). A expectativa do grupo sai dessas declaracoes pela regra
`primeira_com_idRegra` (contrato-k1.md §3 e §11 itens 14 e 15), cruzando os casos do rco com as MESMAS
`entradas`, sem nenhum valor escrito aqui:
1. se algum caso de uma decisao ANTERIOR ao padrao, com essas entradas, declara `idRegra` preenchido,
   ela vence e a saida esperada do grupo e o `esperado` desse caso (o rco prova, no
   `make composicao-dmn` CMP06, que no maximo uma anterior traz `idRegra`);
2. senao, se o caso e do PADRAO, o padrao vence com o `esperado` do proprio caso;
3. senao (caso de uma anterior com `idRegra` vazio), se ha caso do padrao com essas entradas, ele vence
   com o `esperado` dele; sem esse caso o rco nao declara a saida do grupo, e so se exige que a decisao
   do caso NAO venca.
A comparacao de cada chave do `esperado` e a do runner do rco
(`validacao/dmn-runner/.../Runner.java::iguais`): nulo e `""` sao iguais; numeros, por valor; o resto,
pelo texto.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from maezo.platform import rco_manifesto as k1

RECORTE = Path(__file__).resolve().parents[2] / "fixtures" / "rco" / "k1-2.2.0-recorte"
ORIGEM = RECORTE / "ORIGEM.json"
COMMIT_RCO = "a19783753b5b7ef839bc6ec5a565699097265177"
COMPOSICAO = "pares_mutuamente_exclusivos_ortop"
#: A ordem documentada (contrato-k1.md §3 e §11 item 14), com o prefixo do pacote.
ORDEM = ("rco_mex_ortop_joelho_curativo", "rco_mex_ortop_mesma_data", "rco_mex_ortop")
DOCUMENTACAO = frozenset({"ORIGEM.json", "README.md"})


def origem() -> dict[str, Any]:
    dados: dict[str, Any] = json.loads(ORIGEM.read_text(encoding="utf-8"))
    return dados


def manifesto_raw() -> bytes:
    return (RECORTE / "manifesto.json").read_bytes()


def manifesto() -> k1.ManifestoK1:
    return k1.ler_manifesto(manifesto_raw())


def composicao() -> k1.ComposicaoK1:
    grupo = next(c for c in manifesto().composicoes if c.nome == COMPOSICAO)
    return grupo


def dmn(decisao_id: str) -> Path:
    """O arquivo do pacote da decisao (`dmn/<arquivo>.dmn` do manifesto)."""
    return RECORTE / manifesto().decisoes[decisao_id].arquivo


def sha256_prefixado(caminho: Path) -> str:
    return "sha256:" + hashlib.sha256(caminho.read_bytes()).hexdigest()


@dataclass(frozen=True, slots=True)
class CasoRco:
    decisao: str  # id no pacote (`rco_` + `decisao` do arquivo de casos)
    arquivo: str  # relativo ao recorte
    id: str
    tipo: str
    descricao: str
    entradas: Mapping[str, Any]
    esperado: Mapping[str, Any]

    @property
    def chave(self) -> str:
        return f"{self.decisao}#{self.id}"


def casos() -> list[CasoRco]:
    """Todos os casos do rco das decisoes do grupo, na ordem do grupo e do arquivo."""
    out: list[CasoRco] = []
    for decisao in ORDEM:
        original = decisao.removeprefix(k1.PREFIXO_DECISAO)
        rel = f"validacao/casos/ortopedia-opme/{original}.json"
        dados = json.loads((RECORTE / rel).read_text(encoding="utf-8"))
        assert dados["decisao"] == original, rel
        for c in dados["casos"]:
            out.append(
                CasoRco(
                    decisao=decisao,
                    arquivo=rel,
                    id=c["id"],
                    tipo=c["tipo"],
                    descricao=c["descricao"],
                    entradas=c["entradas"],
                    esperado=c["esperado"],
                )
            )
    return out


def _vazio(valor: object) -> bool:
    return valor is None or valor == ""


def iguais(esperado: object, obtido: object) -> bool:
    """`Runner.java::iguais` do rco: nulo == `""`; numero por valor; o resto pelo texto."""
    if _vazio(esperado) or _vazio(obtido):
        return _vazio(esperado) and _vazio(obtido)
    numeros = (int, float)
    if (
        isinstance(esperado, numeros)
        and not isinstance(esperado, bool)
        and isinstance(obtido, numeros)
        and not isinstance(obtido, bool)
    ):
        return float(esperado) == float(obtido)
    return str(esperado) == str(obtido)


def diferencas(esperado: Mapping[str, Any], linha: Mapping[str, Any]) -> list[str]:
    """Chaves do `esperado` que a linha nao satisfaz (vazio = casa)."""
    return [
        f"{k}: esperado={v!r} obtido={linha.get(k)!r}"
        for k, v in esperado.items()
        if not iguais(v, linha.get(k))
    ]


def _id_preenchido(esperado: Mapping[str, Any]) -> bool:
    valor = esperado.get(k1.SAIDA_ID_REGRA)
    return isinstance(valor, str) and valor.strip() not in ("", '""')


@dataclass(frozen=True, slots=True)
class Expectativa:
    """O que o rco declara para o GRUPO neste caso.

    `vencedora` None = o rco nao declara a saida do grupo; vale so `nao_vence`."""

    vencedora: str | None
    esperado: Mapping[str, Any] | None
    fonte: str | None  # chave do caso do rco de onde vem o `esperado`
    nao_vence: str | None = None


def expectativa(caso: CasoRco, todos: list[CasoRco], grupo: k1.ComposicaoK1) -> Expectativa:
    """Regras 1 a 3 do docstring do modulo."""
    mesmos = [c for c in todos if dict(c.entradas) == dict(caso.entradas)]
    com_id = [c for c in mesmos if c.decisao in grupo.anteriores and _id_preenchido(c.esperado)]
    if com_id:
        venc = min(com_id, key=lambda c: grupo.decisoes.index(c.decisao))
        return Expectativa(venc.decisao, venc.esperado, venc.chave)
    if caso.decisao == grupo.padrao:
        return Expectativa(grupo.padrao, caso.esperado, caso.chave)
    do_padrao = [c for c in mesmos if c.decisao == grupo.padrao]
    if do_padrao:
        return Expectativa(grupo.padrao, do_padrao[0].esperado, do_padrao[0].chave)
    return Expectativa(None, None, None, nao_vence=caso.decisao)
