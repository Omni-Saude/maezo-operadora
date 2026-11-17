"""Composicao de decisoes K1 do rco (DL-0173; contrato-k1.md §11 item 14; formato_manifesto 2.2.0).

O QUE ISTO E. Uma tabela do rco dividida em varias decisoes (para caber no orcamento do motor) volta a
ser UMA decisao pela composicao que o manifesto declara em `composicoes[]` (lido e conferido por
`maezo.platform.rco_manifesto`). Aqui fica a semantica, generica e sem motor: quem chama passa o grupo,
um avaliador e as entradas.

CONTRATO DO AVALIADOR. `avaliador(id da decisao, entradas)` devolve OU a tupla `(linhas, DmnVersion)` de
`DmnTransport.evaluate` OU so as linhas (sequencia de objetos). A `DmnVersion` de cada decisao avaliada
vai para `ResultadoComposicao.versoes` (auditoria: qual definicao de cada decisao do grupo rodou).

REGRA `primeira_com_idRegra` (unica do vocabulario fechado `rco_manifesto.REGRAS_COMPOSICAO`):
- TODAS as decisoes do grupo sao avaliadas, com as MESMAS entradas (cada chamada recebe uma copia
  profunda do mesmo instantaneo: um avaliador nao muda o que o seguinte ve);
- vale a primeira decisao, na ordem do grupo, cujo resultado traz `idRegra` preenchido. Ausente =
  chave faltando, `None`, `""`, so espacos ou o literal `'""'` (o mesmo `vazio()` de
  `scripts/checar_composicao_dmn.py` do rco);
- se nenhuma antes do padrao trouxer, vale o resultado do padrao (com ou sem `idRegra`);
- o padrao pode trazer `idRegra` junto de uma anterior: vale a anterior.
As linhas devolvidas sao COPIAS profundas das do avaliador.

FAIL-CLOSED: o desfecho nunca e glosa nem negativa, e sempre `revisao_humana`, quando:
- duas ou mais decisoes antes do padrao trazem `idRegra` (`violacao_prova_rco`, codigo
  `CMP06_EXCLUSIVIDADE`: o gate `make composicao-dmn` do rco prova que isso nao acontece);
- uma decisao do grupo casa mais de uma linha (`violacao_prova_rco`, codigo `CMP08_UNIQUE`): seja o
  avaliador devolvendo mais de uma linha, seja levantando `DmnHitPolicyViolationError` (o motor nomeou
  `DMN-03001` numa tabela UNIQUE). LIMITE: o CIB Seven 2.1.0 NAO nomeia a causa (o transporte levanta a
  classe-mae `DmnDeterministicEvaluationError`, que tambem cobre erro de FEEL e de entrada; ver
  `tests/integration/dmn/test_unique_multimatch_engine.py`), entao nesse motor uma sobreposicao UNIQUE
  aparece como `avaliador_falhou` — o desfecho e o mesmo (`revisao_humana`), so o motivo e menos exato;
- um resultado nao e lista de objetos ou traz `idRegra` que nao e texto (`resultado_invalido`);
- o avaliador falha em QUALQUER decisao do grupo (`avaliador_falhou`): nunca se usa resultado parcial.
Cada caso grava um log estruturado so com os ids das decisoes e o codigo, e conta
`maezo_rco_composicao_revisao_humana_total{motivo}`.

GUARDA. NENHUMA decisao de um grupo e avaliada sozinha, o padrao inclusive: sozinho, o padrao perde os
pares que as anteriores decidem e erra para o lado de pagar (o rco: "consumidor que avalia so mex_ortop
deixa de glosar os pares"). Quem avalia uma decisao `rco_` chama `exigir_avaliacao_isolada(pin, id)`
antes e, se a decisao for de um grupo, `compor`. NADA aqui esta ligado a um worker ainda: ortopedia nao
tem dominio no montador, e a ligacao vem com ele (DL-0173).
"""

from __future__ import annotations

import copy
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Final, Protocol

import structlog

from maezo.platform.observability import record_rco_composicao_revisao
from maezo.platform.rco_manifesto import (
    REGRA_PRIMEIRA_COM_ID_REGRA,
    SAIDA_ID_REGRA,
    ComposicaoK1,
)
from maezo.tools.workers.dmn_transport import DmnHitPolicyViolationError, DmnVersion

logger = structlog.get_logger(__name__)

#: Linhas do resultado de uma decisao.
Linhas = Sequence[Mapping[str, Any]]
#: O que o avaliador devolve: `(linhas, DmnVersion)` (forma de `DmnTransport.evaluate`) ou so as linhas.
Retorno = Linhas | tuple[Linhas, DmnVersion]
Avaliador = Callable[[str, Mapping[str, Any]], Retorno]
AvaliadorAsync = Callable[[str, Mapping[str, Any]], Awaitable[Retorno]]

DESFECHO_ESCOLHIDO: Final[str] = "escolhido"
DESFECHO_REVISAO_HUMANA: Final[str] = "revisao_humana"
#: Motivos de `revisao_humana` (= `observability.RCO_COMPOSICAO_MOTIVOS`, paridade em teste).
MOTIVO_VIOLACAO_PROVA: Final[str] = "violacao_prova_rco"
MOTIVO_AVALIADOR_FALHOU: Final[str] = "avaliador_falhou"
MOTIVO_RESULTADO_INVALIDO: Final[str] = "resultado_invalido"
MOTIVOS: Final[frozenset[str]] = frozenset(
    {MOTIVO_VIOLACAO_PROVA, MOTIVO_AVALIADOR_FALHOU, MOTIVO_RESULTADO_INVALIDO}
)
#: Codigos do log (os de `scripts/checar_composicao_dmn.py` do rco quando a prova dele foi violada).
CODIGO_EXCLUSIVIDADE: Final[str] = "CMP06_EXCLUSIVIDADE"
CODIGO_UNIQUE: Final[str] = "CMP08_UNIQUE"
CODIGO_RESULTADO_INVALIDO: Final[str] = "resultado_invalido"
CODIGO_REGRA_DESCONHECIDA: Final[str] = "regra_desconhecida"
#: Valores de `idRegra` que contam como ausentes depois de `strip()` (o `vazio()` do rco).
_VAZIOS: Final[frozenset[str]] = frozenset({"", '""'})


class DecisaoSoEmComposicaoError(RuntimeError):
    """Pedido de avaliar SOZINHA uma decisao de um grupo de `composicoes[]` (padrao inclusive)."""


class _ComComposicoes(Protocol):
    """O pin (`tools.workers.rco_pin.RcoRulesPin`) ou o manifesto (`rco_manifesto.ManifestoK1`)."""

    @property
    def composicoes(self) -> Sequence[ComposicaoK1]: ...


@dataclass(frozen=True, slots=True)
class ResultadoComposicao:
    """O que a composicao decidiu. Em `revisao_humana`, `linhas` e vazio e `decisao_vencedora` e None."""

    composicao: str
    desfecho: str  # DESFECHO_ESCOLHIDO | DESFECHO_REVISAO_HUMANA
    linhas: tuple[Mapping[str, Any], ...] = ()  # copias profundas das linhas da vencedora
    decisao_vencedora: str | None = None
    #: `True` quando nenhuma decisao antes do padrao trouxe `idRegra` e valeu o padrao.
    pelo_padrao: bool = False
    motivo: str | None = None  # em MOTIVOS, so em revisao_humana
    #: As decisoes avaliadas, na ordem (todas, salvo falha do avaliador).
    avaliadas: tuple[str, ...] = ()
    #: `DmnVersion` de cada decisao avaliada cujo avaliador a devolveu (auditoria).
    versoes: Mapping[str, DmnVersion] = field(default_factory=lambda: MappingProxyType({}))

    @property
    def revisao_humana(self) -> bool:
        return self.desfecho == DESFECHO_REVISAO_HUMANA


def composicao_de(pin: _ComComposicoes, decisao_id: str) -> ComposicaoK1 | None:
    """O grupo que contem `decisao_id` (o leitor garante no maximo um), ou None."""
    return next((c for c in pin.composicoes if decisao_id in c.decisoes), None)


def membro_de_composicao(pin: _ComComposicoes, decisao_id: str) -> bool:
    """`True` se `decisao_id` esta num grupo de `composicoes[]`, padrao inclusive."""
    return composicao_de(pin, decisao_id) is not None


def exigir_avaliacao_isolada(pin: _ComComposicoes, decisao_id: str) -> None:
    """Guarda de quem avalia UMA decisao `rco_`: qualquer membro de grupo (padrao inclusive) levanta
    `DecisaoSoEmComposicaoError` (avalie o grupo com `compor`)."""
    grupo = composicao_de(pin, decisao_id)
    if grupo is not None:
        papel = "padrao" if decisao_id == grupo.padrao else "membro"
        raise DecisaoSoEmComposicaoError(
            f"decisao {decisao_id!r} e {papel} da composicao {grupo.nome!r}: nunca e avaliada sozinha; "
            "avalie o grupo inteiro (rco_composicao.compor, DL-0173)"
        )


def _versoes(versoes: Mapping[str, DmnVersion]) -> Mapping[str, DmnVersion]:
    return MappingProxyType(dict(versoes))


def _revisao(
    composicao: ComposicaoK1,
    motivo: str,
    codigo: str,
    decisoes: Sequence[str],
    avaliadas: Sequence[str],
    versoes: Mapping[str, DmnVersion],
    *,
    evento: str | None = None,
) -> ResultadoComposicao:
    """Desfecho `revisao_humana`: log so com ids de decisao e codigo, e a metrica. Nunca glosa."""
    if evento is None:
        evento = (
            "rco_composicao_violacao_prova" if motivo == MOTIVO_VIOLACAO_PROVA else "rco_composicao_revisao"
        )
    logger.warning(evento, decisoes=list(decisoes), codigo=codigo)
    record_rco_composicao_revisao(motivo)
    return ResultadoComposicao(
        composicao=composicao.nome,
        desfecho=DESFECHO_REVISAO_HUMANA,
        motivo=motivo,
        avaliadas=tuple(avaliadas),
        versoes=_versoes(versoes),
    )


def _id_regra(linhas: tuple[Mapping[str, Any], ...]) -> str | None:
    """`idRegra` da unica linha; ausente (None) se faltar, for None, `""`, so espacos ou `'""'`.
    Levanta `TypeError` se nao for texto."""
    if not linhas:
        return None
    valor = linhas[0].get(SAIDA_ID_REGRA)
    if valor is None:
        return None
    if not isinstance(valor, str):
        raise TypeError("idRegra nao e texto")
    valor = valor.strip()
    return None if valor in _VAZIOS else valor


def _separar(bruto: object) -> tuple[object, DmnVersion | None]:
    """`(linhas, DmnVersion)` -> as duas partes; qualquer outra coisa -> `(bruto, None)`."""
    if isinstance(bruto, tuple) and len(bruto) == 2 and isinstance(bruto[1], DmnVersion):
        return bruto[0], bruto[1]
    return bruto, None


def escolher(composicao: ComposicaoK1, resultados: Mapping[str, Retorno]) -> ResultadoComposicao:
    """Aplica a regra do grupo aos resultados JA avaliados de TODAS as decisoes. Pura (salvo log/metrica).

    Cada resultado e `(linhas, DmnVersion)` ou so as linhas. Tem de haver um por decisao do grupo;
    faltar algum e `avaliador_falhou` (nunca resultado parcial).
    """
    ordem = composicao.decisoes
    avaliadas = tuple(d for d in ordem if d in resultados)
    versoes: dict[str, DmnVersion] = {}
    brutos: dict[str, object] = {}
    for d in avaliadas:
        brutos[d], versao = _separar(resultados[d])
        if versao is not None:
            versoes[d] = versao
    if composicao.regra != REGRA_PRIMEIRA_COM_ID_REGRA:  # o leitor ja recusa; defesa em profundidade
        return _revisao(
            composicao, MOTIVO_RESULTADO_INVALIDO, CODIGO_REGRA_DESCONHECIDA, ordem, avaliadas, versoes
        )
    faltam = [d for d in ordem if d not in resultados]
    if faltam:
        return _revisao(
            composicao, MOTIVO_AVALIADOR_FALHOU, MOTIVO_AVALIADOR_FALHOU, faltam, avaliadas, versoes
        )
    lidos: dict[str, tuple[tuple[Mapping[str, Any], ...], str | None]] = {}
    for d in ordem:
        bruto = brutos[d]
        if (
            not isinstance(bruto, Sequence)
            or isinstance(bruto, str | bytes)
            or not all(isinstance(r, Mapping) for r in bruto)
        ):
            return _revisao(
                composicao, MOTIVO_RESULTADO_INVALIDO, CODIGO_RESULTADO_INVALIDO, [d], avaliadas, versoes
            )
        linhas = tuple(bruto)
        if len(linhas) > 1:
            return _revisao(composicao, MOTIVO_VIOLACAO_PROVA, CODIGO_UNIQUE, [d], avaliadas, versoes)
        try:
            lidos[d] = (linhas, _id_regra(linhas))
        except TypeError:
            return _revisao(
                composicao, MOTIVO_RESULTADO_INVALIDO, CODIGO_RESULTADO_INVALIDO, [d], avaliadas, versoes
            )
    com_id = [d for d in composicao.anteriores if lidos[d][1] is not None]
    if len(com_id) > 1:
        return _revisao(composicao, MOTIVO_VIOLACAO_PROVA, CODIGO_EXCLUSIVIDADE, com_id, avaliadas, versoes)
    vencedora = com_id[0] if com_id else composicao.padrao
    return ResultadoComposicao(
        composicao=composicao.nome,
        desfecho=DESFECHO_ESCOLHIDO,
        linhas=tuple(copy.deepcopy(dict(r)) for r in lidos[vencedora][0]),
        decisao_vencedora=vencedora,
        pelo_padrao=not com_id,
        avaliadas=avaliadas,
        versoes=_versoes(versoes),
    )


def _falhou(
    composicao: ComposicaoK1, decisao: str, exc: Exception, resultados: Mapping[str, Retorno]
) -> ResultadoComposicao:
    """Falha do avaliador numa decisao: `revisao_humana`, sem resultado parcial. Sobreposicao UNIQUE
    nomeada pelo motor (`DmnHitPolicyViolationError`) e violacao da prova CMP08 do rco."""
    avaliadas = tuple(resultados)
    versoes = {d: v for d in avaliadas if (v := _separar(resultados[d])[1]) is not None}
    versao_da_falha = getattr(exc, "version", None)  # `DmnDeterministicEvaluationError` traz a resolvida
    if isinstance(versao_da_falha, DmnVersion):
        versoes[decisao] = versao_da_falha
    if isinstance(exc, DmnHitPolicyViolationError):
        return _revisao(composicao, MOTIVO_VIOLACAO_PROVA, CODIGO_UNIQUE, [decisao], avaliadas, versoes)
    return _revisao(
        composicao,
        MOTIVO_AVALIADOR_FALHOU,
        type(exc).__name__,
        [decisao],
        avaliadas,
        versoes,
        evento="rco_composicao_avaliador_falhou",
    )


def compor(
    composicao: ComposicaoK1, avaliador: Avaliador, entradas: Mapping[str, Any]
) -> ResultadoComposicao:
    """Avalia TODAS as decisoes do grupo, na ordem, com as MESMAS entradas, e aplica a regra.

    Falha do avaliador (qualquer `Exception`) em qualquer decisao -> `revisao_humana`, sem avaliar as
    seguintes e sem resultado parcial. `BaseException` (cancelamento, saida) propaga.
    """
    instantaneo = copy.deepcopy(dict(entradas))
    resultados: dict[str, Retorno] = {}
    for decisao in composicao.decisoes:
        try:
            resultados[decisao] = avaliador(decisao, copy.deepcopy(instantaneo))
        except Exception as exc:  # qualquer falha do avaliador: revisao humana, nunca parcial.
            return _falhou(composicao, decisao, exc, resultados)
    return escolher(composicao, resultados)


async def compor_async(
    composicao: ComposicaoK1, avaliador: AvaliadorAsync, entradas: Mapping[str, Any]
) -> ResultadoComposicao:
    """`compor` para avaliador assincrono (`DmnTransport.evaluate` serve, com `decision_key` = id da
    decisao). Mesma regra; em serie, na ordem do grupo."""
    instantaneo = copy.deepcopy(dict(entradas))
    resultados: dict[str, Retorno] = {}
    for decisao in composicao.decisoes:
        try:
            resultados[decisao] = await avaliador(decisao, copy.deepcopy(instantaneo))
        except Exception as exc:  # qualquer falha do avaliador: revisao humana, nunca parcial.
            return _falhou(composicao, decisao, exc, resultados)
    return escolher(composicao, resultados)


__all__ = [
    "Avaliador",
    "AvaliadorAsync",
    "CODIGO_EXCLUSIVIDADE",
    "CODIGO_UNIQUE",
    "DESFECHO_ESCOLHIDO",
    "DESFECHO_REVISAO_HUMANA",
    "DecisaoSoEmComposicaoError",
    "MOTIVOS",
    "MOTIVO_AVALIADOR_FALHOU",
    "MOTIVO_RESULTADO_INVALIDO",
    "MOTIVO_VIOLACAO_PROVA",
    "ResultadoComposicao",
    "Retorno",
    "composicao_de",
    "compor",
    "compor_async",
    "escolher",
    "exigir_avaliacao_isolada",
    "membro_de_composicao",
]
