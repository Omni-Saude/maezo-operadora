"""Composicao de decisoes K1 do rco (DL-0173; contrato-k1.md §11 item 14; formato_manifesto 2.2.0).

O QUE ISTO E. Uma tabela do rco dividida em varias decisoes (para caber no orcamento do motor) volta a
ser UMA decisao pela composicao que o manifesto declara em `composicoes[]` (lido e conferido por
`maezo.platform.rco_manifesto`). Aqui fica a semantica, generica e sem motor: quem chama passa o grupo,
um avaliador e as entradas.

CONTRATO DO AVALIADOR. `avaliador(id da decisao, entradas)` devolve OU a tupla `(linhas, DmnVersion)` de
`DmnTransport.evaluate` OU so as linhas (sequencia de objetos). A `DmnVersion` de cada decisao avaliada
vai para `ResultadoComposicao.versoes` (auditoria: qual definicao de cada decisao do grupo rodou).

REGRA `primeira_com_idRegra` (formato 2.2.0; vocabulario fechado `rco_manifesto.REGRAS_COMPOSICAO`):
- TODAS as decisoes do grupo sao avaliadas, com as MESMAS entradas (cada chamada recebe uma copia
  profunda do mesmo instantaneo: um avaliador nao muda o que o seguinte ve);
- vale a primeira decisao, na ordem do grupo, cujo resultado traz `idRegra` preenchido. Ausente =
  chave faltando, `None`, `""`, so espacos ou o literal `'""'` (o mesmo `vazio()` de
  `scripts/checar_composicao_dmn.py` do rco);
- se nenhuma antes do padrao trouxer, vale o resultado do padrao (com ou sem `idRegra`);
- o padrao pode trazer `idRegra` junto de uma anterior: vale a anterior.
As linhas devolvidas sao COPIAS profundas das do avaliador.

REGRA `mais_restritiva` (formato 2.3.0, DL-0183; contrato-k1.md §3 e §11 item 16; decisao do dono
PROG-COORD-26): TODAS as decisoes sao avaliadas com as mesmas entradas; vale a de `decisao` mais restritiva
pela `ordem_restricao` de `vocabulario.json` (passada por quem chama, do pin), da mais restritiva para a
menos; empate de `decisao`: a primeira na ordem de `decisoes`. Nao ha desempate por sinal. O resultado
leva a linha da vencedora (`decisao`, `idRegra`, `motivoTab38`, `fila`, `classeAdverso`). Cada membro tem
de trazer EXATAMENTE uma linha com `decisao` dentro da ordem; senao o grupo inteiro vai a `revisao_humana`.

SINAL (`sinalAuditoria`, formato 2.3.0): saida opcional; membro sem a coluna = sinal vazio. Valor ausente,
`None`, `""` ou `'""'` = vazio. O resultado carrega a UNIAO dos sinais nao vazios do grupo (`sinais`, na
ordem do grupo, sem repeticao, de TODAS as linhas de cada membro), nas duas regras e tambem em
`revisao_humana` (o aviso nunca se perde).
Sinal que nao e texto ou fora do vocabulario `sinais_auditoria` (do pin; o padrao so conhece o vazio)
leva o grupo a `revisao_humana` (`violacao_prova_rco`, codigo `CMP12_SINAL`). Aqui NADA e enviado: a
notificacao as filas de `avisos` do K6 e de unidade posterior.

AUDITORIA. `veredictos` guarda, por decisao avaliada, COPIAS das linhas que ela devolveu (o veredicto
inteiro de cada membro), e `versoes` a `DmnVersion` de cada uma: nas duas regras, em `escolhido` e em
`revisao_humana`.

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
- `mais_restritiva`: membro sem linha (`violacao_prova_rco`, `CMP10_LACUNA`), com `decisao` que nao e
  texto ou fora da ordem (`violacao_prova_rco`, `CMP11_FORA_DA_ORDEM`), ou `ordem_restricao` vazia ou com
  repeticao (`resultado_invalido`, `ordem_restricao_invalida`); nunca negativa por ausencia;
- sinal fora do vocabulario em QUALQUER linha do membro (`violacao_prova_rco`, `CMP12_SINAL`);
- vencedora adversa (`adversas`) sem algum campo de `adversa_exige_preenchidos` (`resultado_invalido`,
  `adversa_incompleta`), nas duas regras; quem chama passa o `combinacao` do pin, senao vale o piso;
- o avaliador falha em QUALQUER decisao do grupo (`avaliador_falhou`): nunca se usa resultado parcial.
Cada caso grava um log estruturado so com os ids das decisoes, o codigo e o `modo` de quem compoe, e conta
`maezo_rco_composicao_revisao_humana_total{motivo, modo}` (`modo` obrigatorio: `sombra` | `efeito`).

GUARDA. NENHUMA decisao de um grupo e avaliada sozinha, o padrao inclusive: sozinho, o padrao perde os
pares que as anteriores decidem e erra para o lado de pagar (o rco: "consumidor que avalia so mex_ortop
deixa de glosar os pares"). Quem avalia uma decisao `rco_` chama `exigir_avaliacao_isolada(pin, id)`
antes e, se a decisao for de um grupo, `compor`. Ligacao a worker (DL-0187): so em SOMBRA,
`composicao_carencia_cpt` no portao de criterios do AUTH (`tools.workers.auth_rco` ->
`tools.workers.rco_sombra.avaliar_composicao_em_sombra`, que registra o resultado com `modo=sombra`); a
ortopedia nao tem dominio no montador, e a ligacao dela vem com ele (DL-0173).
"""

from __future__ import annotations

import copy
from collections.abc import Awaitable, Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Final, Protocol

import structlog

from maezo.platform.observability import record_rco_composicao_revisao
from maezo.platform.rco_manifesto import (
    REGRA_MAIS_RESTRITIVA,
    REGRA_PRIMEIRA_COM_ID_REGRA,
    SAIDA_DECISAO,
    SAIDA_ID_REGRA,
    SAIDA_SINAL_AUDITORIA,
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

#: Modo de quem compoe, vocabulario fechado (= `MODOS_COMPOSICAO` do registro de composicoes e o rotulo
#: `modo` de `maezo_rco_composicao_revisao_humana_total`, paridade em teste; definido aqui porque este modulo
#: e puro e nao importa o registro, `test_compor_e_puro_e_nao_registra_nada`): `sombra` quando a composicao
#: nao muda o caso (`tools.workers.rco_sombra`), `efeito` quando pode mudar. OBRIGATORIO em `compor` e
#: `escolher`: sem ele a revisao humana de uma sombra seria indistinguivel da de efeito no log e na metrica.
MODO_SOMBRA: Final[str] = "sombra"
MODO_EFEITO: Final[str] = "efeito"
MODOS: Final[frozenset[str]] = frozenset({MODO_SOMBRA, MODO_EFEITO})
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
#: `mais_restritiva` e sinal (formato 2.3.0): os codigos das provas do rco (`checar_composicao_dmn.py`).
CODIGO_LACUNA: Final[str] = "CMP10_LACUNA"
CODIGO_FORA_DA_ORDEM: Final[str] = "CMP11_FORA_DA_ORDEM"
CODIGO_SINAL: Final[str] = "CMP12_SINAL"
CODIGO_ORDEM_INVALIDA: Final[str] = "ordem_restricao_invalida"
#: Vencedora adversa sem algum campo de `combinacao.adversa_exige_preenchidos` (contrato-k1.md §6).
CODIGO_ADVERSA_INCOMPLETA: Final[str] = "adversa_incompleta"
#: Piso usado quando quem chama nao passa o `combinacao` do `vocabulario.json` pinado (o pin passa o dele):
#: decisao adversa (ADR-0075 (d)) e o campo sem o qual a linha nao e auditavel. Nunca afrouxa o do pin.
ADVERSAS_PISO: Final[frozenset[str]] = frozenset({"negar", "glosar", "recomendar_negativa"})
ADVERSA_EXIGE_PISO: Final[tuple[str, ...]] = ("idRegra",)
#: Vocabulario de sinal que se assume quando quem chama nao passa o do pin: so o vazio.
SINAIS_SO_VAZIO: Final[frozenset[str]] = frozenset({""})
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
    #: Uniao dos `sinalAuditoria` nao vazios do grupo, na ordem do grupo, sem repeticao (formato 2.3.0).
    sinais: tuple[str, ...] = ()
    #: Por decisao avaliada cujo resultado e lista de objetos: COPIAS das linhas (veredicto de cada membro).
    veredictos: Mapping[str, tuple[Mapping[str, Any], ...]] = field(
        default_factory=lambda: MappingProxyType({})
    )

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


def _exigir_modo(modo: object) -> None:
    if not isinstance(modo, str) or modo not in MODOS:
        raise ValueError(f"modo de composicao fora do vocabulario fechado {sorted(MODOS)}")


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
    modo: str,
    evento: str | None = None,
    sinais: Sequence[str] = (),
    veredictos: Mapping[str, tuple[Mapping[str, Any], ...]] | None = None,
) -> ResultadoComposicao:
    """Desfecho `revisao_humana`: log so com ids de decisao, codigo e modo, e a metrica. Nunca glosa."""
    if evento is None:
        evento = (
            "rco_composicao_violacao_prova" if motivo == MOTIVO_VIOLACAO_PROVA else "rco_composicao_revisao"
        )
    logger.warning(evento, decisoes=list(decisoes), codigo=codigo, modo=modo)
    record_rco_composicao_revisao(motivo, modo=modo)
    return ResultadoComposicao(
        composicao=composicao.nome,
        desfecho=DESFECHO_REVISAO_HUMANA,
        motivo=motivo,
        avaliadas=tuple(avaliadas),
        versoes=_versoes(versoes),
        sinais=tuple(sinais),
        veredictos=MappingProxyType(dict(veredictos or {})),
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


def _sinal_da_linha(linha: Mapping[str, Any]) -> str:
    """`sinalAuditoria` de uma linha (`""` se faltar a coluna ou for vazio). `TypeError` se nao for texto."""
    valor = linha.get(SAIDA_SINAL_AUDITORIA)
    if valor is None:
        return ""
    if not isinstance(valor, str):
        raise TypeError("sinalAuditoria nao e texto")
    valor = valor.strip()
    return "" if valor in _VAZIOS else valor


def _sinais_validos(linhas: Sequence[Mapping[str, Any]], vocabulario: Collection[str]) -> tuple[str, ...]:
    """Sinais nao vazios do vocabulario em TODAS as linhas de um membro (uma linha a mais, violacao CMP08,
    nao apaga o aviso). Sinal que nao e texto ou fora do vocabulario fica de fora (o membro ja vai a
    revisao por isso)."""
    achados: list[str] = []
    for linha in linhas:
        try:
            valor = _sinal_da_linha(linha)
        except TypeError:
            continue
        if valor and valor in vocabulario:
            achados.append(valor)
    return tuple(achados)


def _uniao(sinais: Mapping[str, tuple[str, ...]], ordem: Sequence[str]) -> tuple[str, ...]:
    """Sinais nao vazios, na ordem do grupo (e das linhas), sem repeticao."""
    return tuple(dict.fromkeys(v for d in ordem for v in sinais.get(d, ())))


def _preenchido(valor: object) -> bool:
    return isinstance(valor, str) and valor.strip() not in _VAZIOS


def _copias(linhas: Sequence[Mapping[str, Any]]) -> tuple[Mapping[str, Any], ...]:
    return tuple(copy.deepcopy(dict(r)) for r in linhas)


def _ordem_valida(ordem: Sequence[str]) -> bool:
    return (
        bool(ordem)
        and not isinstance(ordem, str)
        and all(isinstance(d, str) and d for d in ordem)
        and len(set(ordem)) == len(ordem)
    )


def escolher(
    composicao: ComposicaoK1,
    resultados: Mapping[str, Retorno],
    *,
    modo: str,
    ordem_restricao: Sequence[str] = (),
    sinais_auditoria: Collection[str] = SINAIS_SO_VAZIO,
    adversas: Collection[str] = ADVERSAS_PISO,
    adversa_exige_preenchidos: Sequence[str] = ADVERSA_EXIGE_PISO,
) -> ResultadoComposicao:
    """Aplica a regra do grupo aos resultados JA avaliados de TODAS as decisoes. Pura (salvo log/metrica).

    Cada resultado e `(linhas, DmnVersion)` ou so as linhas. Tem de haver um por decisao do grupo;
    faltar algum e `avaliador_falhou` (nunca resultado parcial). `ordem_restricao` (so `mais_restritiva`)
    e `sinais_auditoria` vem do `vocabulario.json` pinado (`RcoRulesPin`). `modo` (obrigatorio, em `MODOS`)
    vai ao log e ao rotulo da metrica de revisao humana; fora do vocabulario levanta `ValueError`.
    """
    _exigir_modo(modo)
    ordem = composicao.decisoes
    avaliadas = tuple(d for d in ordem if d in resultados)
    versoes: dict[str, DmnVersion] = {}
    brutos: dict[str, object] = {}
    veredictos: dict[str, tuple[Mapping[str, Any], ...]] = {}
    for d in avaliadas:
        brutos[d], versao = _separar(resultados[d])
        if versao is not None:
            versoes[d] = versao
        bruto = brutos[d]
        if (
            isinstance(bruto, Sequence)
            and not isinstance(bruto, str | bytes)
            and all(isinstance(r, Mapping) for r in bruto)
        ):
            veredictos[d] = _copias(bruto)
    sinais = {d: _sinais_validos(linhas_d, sinais_auditoria) for d, linhas_d in veredictos.items()}

    def revisao(motivo: str, codigo: str, decisoes: Sequence[str]) -> ResultadoComposicao:
        return _revisao(
            composicao,
            motivo,
            codigo,
            decisoes,
            avaliadas,
            versoes,
            modo=modo,
            sinais=_uniao(sinais, ordem),
            veredictos=veredictos,
        )

    if composicao.regra not in (REGRA_PRIMEIRA_COM_ID_REGRA, REGRA_MAIS_RESTRITIVA):
        # o leitor ja recusa; defesa em profundidade
        return revisao(MOTIVO_RESULTADO_INVALIDO, CODIGO_REGRA_DESCONHECIDA, ordem)
    faltam = [d for d in ordem if d not in resultados]
    if faltam:
        return revisao(MOTIVO_AVALIADOR_FALHOU, MOTIVO_AVALIADOR_FALHOU, faltam)
    mais_restritiva = composicao.regra == REGRA_MAIS_RESTRITIVA
    if mais_restritiva and not _ordem_valida(ordem_restricao):
        return revisao(MOTIVO_RESULTADO_INVALIDO, CODIGO_ORDEM_INVALIDA, ordem)
    lidos: dict[str, tuple[tuple[Mapping[str, Any], ...], str | None]] = {}
    for d in ordem:
        if d not in veredictos:
            return revisao(MOTIVO_RESULTADO_INVALIDO, CODIGO_RESULTADO_INVALIDO, [d])
        linhas = veredictos[d]
        try:
            sinais_d = [_sinal_da_linha(linha) for linha in linhas]
        except TypeError:
            return revisao(MOTIVO_RESULTADO_INVALIDO, CODIGO_RESULTADO_INVALIDO, [d])
        if any(v not in sinais_auditoria for v in sinais_d):
            return revisao(MOTIVO_VIOLACAO_PROVA, CODIGO_SINAL, [d])
        if len(linhas) > 1:
            return revisao(MOTIVO_VIOLACAO_PROVA, CODIGO_UNIQUE, [d])
        if mais_restritiva and not linhas:
            return revisao(MOTIVO_VIOLACAO_PROVA, CODIGO_LACUNA, [d])
        try:
            lidos[d] = (linhas, _id_regra(linhas))
        except TypeError:
            return revisao(MOTIVO_RESULTADO_INVALIDO, CODIGO_RESULTADO_INVALIDO, [d])
        if mais_restritiva:
            decisao = linhas[0].get(SAIDA_DECISAO)
            if not isinstance(decisao, str) or decisao not in ordem_restricao:
                return revisao(MOTIVO_VIOLACAO_PROVA, CODIGO_FORA_DA_ORDEM, [d])
    if mais_restritiva:
        posicao = {valor: i for i, valor in enumerate(ordem_restricao)}
        # `min` devolve o PRIMEIRO de menor chave: empate de `decisao` -> a primeira em `decisoes`.
        vencedora = min(ordem, key=lambda d: posicao[str(lidos[d][0][0][SAIDA_DECISAO])])
        pelo_padrao = False
    else:
        com_id = [d for d in composicao.anteriores if lidos[d][1] is not None]
        if len(com_id) > 1:
            return revisao(MOTIVO_VIOLACAO_PROVA, CODIGO_EXCLUSIVIDADE, com_id)
        vencedora = com_id[0] if com_id else composicao.padrao
        pelo_padrao = not com_id
    linhas_vencedora = lidos[vencedora][0]
    if linhas_vencedora and linhas_vencedora[0].get(SAIDA_DECISAO) in adversas:
        # Vencedora adversa sem os campos que o vocabulario exige (contrato-k1.md §6): nunca vira efeito.
        faltam = [c for c in adversa_exige_preenchidos if not _preenchido(linhas_vencedora[0].get(c))]
        if faltam:
            return revisao(MOTIVO_RESULTADO_INVALIDO, CODIGO_ADVERSA_INCOMPLETA, [vencedora])
    return ResultadoComposicao(
        composicao=composicao.nome,
        desfecho=DESFECHO_ESCOLHIDO,
        linhas=_copias(lidos[vencedora][0]),
        decisao_vencedora=vencedora,
        pelo_padrao=pelo_padrao,
        avaliadas=avaliadas,
        versoes=_versoes(versoes),
        sinais=_uniao(sinais, ordem),
        veredictos=MappingProxyType(veredictos),
    )


def _falhou(
    composicao: ComposicaoK1,
    decisao: str,
    exc: Exception,
    resultados: Mapping[str, Retorno],
    sinais_auditoria: Collection[str],
    *,
    modo: str,
) -> ResultadoComposicao:
    """Falha do avaliador numa decisao: `revisao_humana`, sem resultado parcial. Sobreposicao UNIQUE
    nomeada pelo motor (`DmnHitPolicyViolationError`) e violacao da prova CMP08 do rco. Os veredictos e
    os sinais validos das decisoes ja avaliadas vao junto (auditoria; o aviso nao se perde)."""
    avaliadas = tuple(resultados)
    versoes = {d: v for d in avaliadas if (v := _separar(resultados[d])[1]) is not None}
    veredictos: dict[str, tuple[Mapping[str, Any], ...]] = {}
    sinais: dict[str, tuple[str, ...]] = {}
    for d in avaliadas:
        bruto = _separar(resultados[d])[0]
        if (
            isinstance(bruto, Sequence)
            and not isinstance(bruto, str | bytes)
            and all(isinstance(r, Mapping) for r in bruto)
        ):
            veredictos[d] = _copias(bruto)
            sinais[d] = _sinais_validos(veredictos[d], sinais_auditoria)
    versao_da_falha = getattr(exc, "version", None)  # `DmnDeterministicEvaluationError` traz a resolvida
    if isinstance(versao_da_falha, DmnVersion):
        versoes[decisao] = versao_da_falha
    uniao = _uniao(sinais, composicao.decisoes)
    if isinstance(exc, DmnHitPolicyViolationError):
        return _revisao(
            composicao,
            MOTIVO_VIOLACAO_PROVA,
            CODIGO_UNIQUE,
            [decisao],
            avaliadas,
            versoes,
            modo=modo,
            sinais=uniao,
            veredictos=veredictos,
        )
    return _revisao(
        composicao,
        MOTIVO_AVALIADOR_FALHOU,
        type(exc).__name__,
        [decisao],
        avaliadas,
        versoes,
        modo=modo,
        evento="rco_composicao_avaliador_falhou",
        sinais=uniao,
        veredictos=veredictos,
    )


def compor(
    composicao: ComposicaoK1,
    avaliador: Avaliador,
    entradas: Mapping[str, Any],
    *,
    modo: str,
    ordem_restricao: Sequence[str] = (),
    sinais_auditoria: Collection[str] = SINAIS_SO_VAZIO,
    adversas: Collection[str] = ADVERSAS_PISO,
    adversa_exige_preenchidos: Sequence[str] = ADVERSA_EXIGE_PISO,
) -> ResultadoComposicao:
    """Avalia TODAS as decisoes do grupo, na ordem, com as MESMAS entradas, e aplica a regra.

    Falha do avaliador (qualquer `Exception`) em qualquer decisao -> `revisao_humana`, sem avaliar as
    seguintes e sem resultado parcial. `BaseException` (cancelamento, saida) propaga. `ordem_restricao` e
    `sinais_auditoria`: os do `vocabulario.json` pinado (`RcoRulesPin`); `modo` (obrigatorio): ver
    `escolher`. Modo fora do vocabulario levanta `ValueError` antes de avaliar qualquer decisao.
    """
    _exigir_modo(modo)
    instantaneo = copy.deepcopy(dict(entradas))
    resultados: dict[str, Retorno] = {}
    for decisao in composicao.decisoes:
        try:
            resultados[decisao] = avaliador(decisao, copy.deepcopy(instantaneo))
        except Exception as exc:  # qualquer falha do avaliador: revisao humana, nunca parcial.
            return _falhou(composicao, decisao, exc, resultados, sinais_auditoria, modo=modo)
    return escolher(
        composicao,
        resultados,
        modo=modo,
        ordem_restricao=ordem_restricao,
        sinais_auditoria=sinais_auditoria,
        adversas=adversas,
        adversa_exige_preenchidos=adversa_exige_preenchidos,
    )


async def compor_async(
    composicao: ComposicaoK1,
    avaliador: AvaliadorAsync,
    entradas: Mapping[str, Any],
    *,
    modo: str,
    ordem_restricao: Sequence[str] = (),
    sinais_auditoria: Collection[str] = SINAIS_SO_VAZIO,
    adversas: Collection[str] = ADVERSAS_PISO,
    adversa_exige_preenchidos: Sequence[str] = ADVERSA_EXIGE_PISO,
) -> ResultadoComposicao:
    """`compor` para avaliador assincrono (`DmnTransport.evaluate` serve, com `decision_key` = id da
    decisao). Mesma regra; em serie, na ordem do grupo."""
    _exigir_modo(modo)
    instantaneo = copy.deepcopy(dict(entradas))
    resultados: dict[str, Retorno] = {}
    for decisao in composicao.decisoes:
        try:
            resultados[decisao] = await avaliador(decisao, copy.deepcopy(instantaneo))
        except Exception as exc:  # qualquer falha do avaliador: revisao humana, nunca parcial.
            return _falhou(composicao, decisao, exc, resultados, sinais_auditoria, modo=modo)
    return escolher(
        composicao,
        resultados,
        modo=modo,
        ordem_restricao=ordem_restricao,
        sinais_auditoria=sinais_auditoria,
        adversas=adversas,
        adversa_exige_preenchidos=adversa_exige_preenchidos,
    )


__all__ = [
    "Avaliador",
    "AvaliadorAsync",
    "ADVERSAS_PISO",
    "ADVERSA_EXIGE_PISO",
    "CODIGO_ADVERSA_INCOMPLETA",
    "CODIGO_EXCLUSIVIDADE",
    "CODIGO_FORA_DA_ORDEM",
    "CODIGO_LACUNA",
    "CODIGO_ORDEM_INVALIDA",
    "CODIGO_SINAL",
    "CODIGO_UNIQUE",
    "SINAIS_SO_VAZIO",
    "DESFECHO_ESCOLHIDO",
    "DESFECHO_REVISAO_HUMANA",
    "DecisaoSoEmComposicaoError",
    "MODOS",
    "MODO_EFEITO",
    "MODO_SOMBRA",
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
