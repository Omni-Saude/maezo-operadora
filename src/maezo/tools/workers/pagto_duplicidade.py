"""SP-OP-PAGTO-001 — `detect_duplicidade`: a ordem conferida contra a razao append-only de obrigacoes.

Pacote PAGTO PR-E (analise PAGTO §4 niveis 1-3 + `INDETERMINADA`; §8 D6(A), aceita pelo dono:
"razao append-only de pagamentos"; §9 item 5). DARK: so roda com `MAEZO_PAGTO_DETECT_DUPLICIDADE`
ligada. Fecha o residuo de R-063: hoje `duplicidade_suspeita` e' so' um sinal semeado/ecoado
(`pagto_admissibility.dmn`, `pagto.validate_pagto`) e nao existe detector.

O QUE FAZ. No MESMO topico `operadora.pagto.validate_payment_data` (sem tarefa nova no BPMN), depois
da validacao de sempre (e dos fatos do lastro do PR-D quando a flag dele tambem esta ligada):

  1. monta a CHAVE DA OBRIGACAO da ordem (`OBRIG-{origem}-{sha256}`; componentes da analise §4
     nivel 1 — CONTAS: lote + prestador + guia; RECURSO: `glosa_id`; outro: a propria ordem);
  2. numa UNICA transacao da razao (`pagto_obrigacao_ledger`, migracao 0033), serializada por lock
     de prestador e de obrigacao, le os INDICIOS (nivel 1: a mesma obrigacao em outra ordem;
     nivel 2: o que outras ordens ja comprometeram contra a mesma instancia de origem; nivel 3: a
     ordem similar mais recente — mesmo prestador + competencia + valor, outra obrigacao, outro
     lote) e grava a linha `REGISTRADA` desta ordem;
  3. avalia a DMN `pagto_duplicidade` NO MOTOR (costura `dmn=`, ADR-0028; nada de avaliador local)
     com esses fatos e devolve `duplicidade_status` (`NENHUMA` | `SUSPEITA` | `CONFIRMADA` |
     `INDETERMINADA`) + `duplicidade_motivo_codigo` + a evidencia.

O QUE NAO FAZ. Nao roteia nem libera nem cancela: `duplicidade_status` e' FATO; quem o le para
rotear e' `pagto_admissibility` v0.2 (pacote PR-F). Nao toca `duplicidade_suspeita` — a DMN viva
(v0.1) segue lendo o booleano de sempre, entao ligar a flag NAO muda rota nenhuma. A janela W do
nivel 3 e as classes de cada nivel moram na DMN (DRAFT, financas), nunca aqui.

QUEM ESCREVE A RAZAO, E QUANDO (analise §4 D6(A), "written at start and at release"):
  * `REGISTRADA` — este detector, na validacao (primeiro worker da ordem em PAGTO), so' quando a
    ordem tem identidade suficiente para a chave; a validacao que recusa (`ERR_PAGTO_ORDEM_INVALIDA`)
    sai ANTES e nada e' gravado;
  * `LIBERADA` — `registrar_liberacao`, que ENVOLVE os dois workers de liberacao: chama o guard de
    sempre primeiro e so' grava quando ele devolveu a liberacao. Recusa do guard (excecao) sobe
    intacta e nada e' gravado; o retorno do guard nunca e' alterado. Nao e' um segundo caminho de
    efeito: sem o guard, nada acontece aqui.

FALHA FECHADA. Razao ausente/fora, DMN fora/ausente ou saida fora do vocabulario viram
`INDETERMINADA` — nunca `NENHUMA`, e nunca uma excecao que derrube a validacao (o fato vai a
humano). Ordem sem identidade (sem prestador, valor invalido, componente da chave ausente) tambem e'
`INDETERMINADA`, sem gravar. ERRO DE PROGRAMACAO NAO e' absorvido (`PROGRAMMING_ERRORS` re-levanta,
licao NEW-12).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from typing import Any, Final

import structlog

from maezo.platform.keys import key_segment
from maezo.platform.pagto_obrigacao.ledger import (
    EVENTO_LIBERADA,
    EVENTO_REGISTRADA,
    NIVEL1_LIBERADA_OUTRA_ORDEM,
    NIVEL1_REGISTRADA_OUTRA_ORDEM,
    NIVEL1_SEM_OUTRA_ORDEM,
    ORIGEM_CONTAS,
    ORIGEM_OUTRO,
    ORIGEM_RECURSO,
    EntradaObrigacao,
    IndiciosDuplicidade,
    RazaoObrigacoesPagamento,
    chave_obrigacao,
)
from maezo.runtime.dependency_failures import EXTERNAL_DEPENDENCY_FAILURES, PROGRAMMING_ERRORS
from maezo.tools.workers.dmn_transport import DmnTransport, evaluate_sync, first_row
from maezo.tools.workers.pagto import _valor_pagamento_cents_or_none
from maezo.tools.workers.pagto_lastro import (
    FONTE_POR_EVIDENCIA,
    LASTRO_ORIGEM_INSTANCIA_VAR,
    LASTRO_STATUS_VAR,
    LASTRO_VALOR_ORIGEM_VAR,
    STATUS_CONFIRMADO,
    STATUS_DIVERGENTE_VALOR,
    VALIDATE_COM_LASTRO_VARIABLES,
)

logger = structlog.get_logger(__name__)

TOPIC_VALIDATE_PAYMENT_DATA: Final[str] = "operadora.pagto.validate_payment_data"
DMN_DUPLICIDADE: Final[str] = "pagto_duplicidade"

# ---------------------------------------------------------------------------------------------
# Vocabulario FECHADO (analise PAGTO §3/§4). `duplicidade_status` ja esta em
# `pagto.PAGTO_FATOS_PROIBIDOS_NA_SEMENTE`; a evidencia abaixo tambem (nenhuma origem a semeia).
# ---------------------------------------------------------------------------------------------

DUPLICIDADE_STATUS_VAR: Final[str] = "duplicidade_status"
DUPLICIDADE_MOTIVO_VAR: Final[str] = "duplicidade_motivo_codigo"
DUPLICIDADE_CHAVE_VAR: Final[str] = "duplicidade_chave_obrigacao"
DUPLICIDADE_ORDEM_CONFLITANTE_VAR: Final[str] = "duplicidade_ordem_conflitante_id"
DUPLICIDADE_NIVEL1_VAR: Final[str] = "duplicidade_nivel1_situacao"
DUPLICIDADE_NIVEL2_VAR: Final[str] = "duplicidade_nivel2_situacao"
DUPLICIDADE_NIVEL3_IDADE_VAR: Final[str] = "duplicidade_nivel3_similar_idade_dias"

#: As chaves que o detector escreve — sempre todas, em todo caminho.
DUPLICIDADE_SAIDAS: Final[frozenset[str]] = frozenset(
    {
        DUPLICIDADE_STATUS_VAR,
        DUPLICIDADE_MOTIVO_VAR,
        DUPLICIDADE_CHAVE_VAR,
        DUPLICIDADE_ORDEM_CONFLITANTE_VAR,
        DUPLICIDADE_NIVEL1_VAR,
        DUPLICIDADE_NIVEL2_VAR,
        DUPLICIDADE_NIVEL3_IDADE_VAR,
    }
)
#: A evidencia (tudo menos o status, que ja era fato proibido antes deste pacote).
DUPLICIDADE_EVIDENCIAS: Final[frozenset[str]] = DUPLICIDADE_SAIDAS - {DUPLICIDADE_STATUS_VAR}

STATUS_NENHUMA: Final[str] = "NENHUMA"
STATUS_SUSPEITA: Final[str] = "SUSPEITA"
STATUS_CONFIRMADA: Final[str] = "CONFIRMADA"
STATUS_INDETERMINADA: Final[str] = "INDETERMINADA"
DUPLICIDADE_STATUS: Final[frozenset[str]] = frozenset(
    {STATUS_NENHUMA, STATUS_SUSPEITA, STATUS_CONFIRMADA, STATUS_INDETERMINADA}
)

#: Motivos que NAO vem da DMN (o detector nem chegou a avalia-la) — todos `INDETERMINADA`.
MOTIVO_RAZAO_INDISPONIVEL: Final[str] = "RAZAO_INDISPONIVEL"
MOTIVO_DMN_INDISPONIVEL: Final[str] = "DMN_INDISPONIVEL"
MOTIVO_DADOS_INSUFICIENTES: Final[str] = "DADOS_INSUFICIENTES"
MOTIVO_DMN_SAIDA_INVALIDA: Final[str] = "DMN_SAIDA_INVALIDA"

#: Situacoes de nivel (entradas da DMN). O nivel 1 vem da razao; `NAO_AVALIAVEL` so' existe aqui.
NIVEL_NAO_AVALIAVEL: Final[str] = "NAO_AVALIAVEL"
NIVEL1_SITUACOES: Final[frozenset[str]] = frozenset(
    {NIVEL1_SEM_OUTRA_ORDEM, NIVEL1_REGISTRADA_OUTRA_ORDEM, NIVEL1_LIBERADA_OUTRA_ORDEM, NIVEL_NAO_AVALIAVEL}
)
NIVEL2_EXCEDE: Final[str] = "EXCEDE"
NIVEL2_NAO_EXCEDE: Final[str] = "NAO_EXCEDE"
NIVEL2_SITUACOES: Final[frozenset[str]] = frozenset({NIVEL2_EXCEDE, NIVEL2_NAO_EXCEDE, NIVEL_NAO_AVALIAVEL})

#: O que o detector le alem do que a validacao (+ lastro) ja le. `competencia` alimenta o nivel 3.
_LEITURAS_DO_DETECTOR: Final[tuple[str, ...]] = ("competencia",)

#: Escopo de leitura EXPLICITO do topico `validate_payment_data` quando a flag liga (menor
#: privilegio; nunca "todas"): o escopo do PR-D (validacao + lastro, cercado por teste contra o
#: corpo de `validate_pagto`) + o que o detector le. Os fatos do lastro que o nivel 2 usa NAO sao
#: lidos do motor: chegam da saida do proprio handler (o PR-D os produz no mesmo despacho).
VALIDATE_COM_DUPLICIDADE_VARIABLES: Final[tuple[str, ...]] = tuple(
    dict.fromkeys((*VALIDATE_COM_LASTRO_VARIABLES, *_LEITURAS_DO_DETECTOR))
)

#: Situacoes do lastro (PR-D) em que a instancia de origem foi achada e terminou num desfecho que
#: gera obrigacao — so' nelas o nivel 2 tem contra o que somar.
_LASTRO_COM_ORIGEM: Final[frozenset[str]] = frozenset({STATUS_CONFIRMADO, STATUS_DIVERGENTE_VALOR})


def _fatos(
    status: str,
    motivo: str,
    *,
    chave: str = "",
    ordem_conflitante: str = "",
    nivel1: str = NIVEL_NAO_AVALIAVEL,
    nivel2: str = NIVEL_NAO_AVALIAVEL,
    nivel3_idade: int | None = None,
) -> dict[str, Any]:
    return {
        DUPLICIDADE_STATUS_VAR: status,
        DUPLICIDADE_MOTIVO_VAR: motivo,
        DUPLICIDADE_CHAVE_VAR: chave,
        DUPLICIDADE_ORDEM_CONFLITANTE_VAR: ordem_conflitante,
        DUPLICIDADE_NIVEL1_VAR: nivel1,
        DUPLICIDADE_NIVEL2_VAR: nivel2,
        DUPLICIDADE_NIVEL3_IDADE_VAR: nivel3_idade,
    }


def _origem_da_ordem(variables: Mapping[str, Any]) -> str:
    """A familia de origem pela evidencia `lastro_origem` (o MESMO mapa do PR-D). Evidencia
    desconhecida/ausente = `outro` (ordem manual, Andre)."""
    fonte = FONTE_POR_EVIDENCIA.get(key_segment(variables.get("lastro_origem")))
    if fonte is None:
        return ORIGEM_OUTRO
    return ORIGEM_CONTAS if fonte.familia == ORIGEM_CONTAS else ORIGEM_RECURSO


def _componentes(origem: str, variables: Mapping[str, Any]) -> tuple[str, ...] | None:
    """Componentes da chave da obrigacao (analise §4 nivel 1). `None` = identidade insuficiente."""
    if origem == ORIGEM_CONTAS:
        lote = key_segment(variables.get("numero_lote_tiss"))
        prestador = key_segment(variables.get("prestador_id"))
        if not (lote and prestador):
            return None
        # Guia vazia = conta no nivel do lote, marcada "-" (uma guia literalmente "-" cai na mesma
        # obrigacao do lote: colisao conservadora — vira indicio, nunca esconde um).
        return (lote, prestador, key_segment(variables.get("numero_guia_tiss")) or "-")
    if origem == ORIGEM_RECURSO:
        glosa = key_segment(variables.get("glosa_id"))
        return (glosa,) if glosa else None
    ordem = key_segment(variables.get("ordem_pagamento_id"))
    return (ordem,) if ordem else None


def _origem_conferida(variables: Mapping[str, Any]) -> tuple[str | None, int | None]:
    """(instancia de origem, valor da origem em centavos) do PR-D, so' quando o lastro achou a
    origem num desfecho que gera obrigacao. Valor nao-inteiro/<=0 = sem valor."""
    if key_segment(variables.get(LASTRO_STATUS_VAR)) not in _LASTRO_COM_ORIGEM:
        return None, None
    instancia = key_segment(variables.get(LASTRO_ORIGEM_INSTANCIA_VAR)) or None
    valor = variables.get(LASTRO_VALOR_ORIGEM_VAR)
    if not isinstance(valor, int) or isinstance(valor, bool) or valor <= 0:
        return instancia, None
    return instancia, valor


def _entrada(
    tenant: str, evento: str, variables: Mapping[str, Any], *, origem_instancia_id: str | None
) -> EntradaObrigacao | None:
    """A linha da razao para esta ordem, ou `None` quando a ordem nao tem identidade suficiente
    ou um campo esta fora do formato da 0033 (nunca gravado, nunca adivinhado)."""
    origem = _origem_da_ordem(variables)
    componentes = _componentes(origem, variables)
    ordem = key_segment(variables.get("ordem_pagamento_id"))
    prestador = key_segment(variables.get("prestador_id"))
    valor_cents, _motivo = _valor_pagamento_cents_or_none(variables.get("valor_pagamento_cents"))
    if componentes is None or not (tenant and ordem and prestador) or valor_cents is None:
        return None
    try:
        return EntradaObrigacao(
            tenant=tenant,
            evento=evento,
            origem=origem,
            chave_obrigacao=chave_obrigacao(tenant, origem, componentes),
            ordem_pagamento_id=ordem,
            prestador_id=prestador,
            valor_cents=valor_cents,
            numero_lote_tiss=key_segment(variables.get("numero_lote_tiss")) or None,
            competencia=key_segment(variables.get("competencia")) or None,
            origem_instancia_id=origem_instancia_id,
        )
    except ValueError:
        # Recusa do PROPRIO construtor (formato da 0033): dado que a razao nao aceitaria.
        return None


def _tenant_da_razao(ledger: RazaoObrigacoesPagamento) -> str:
    """A razao e' por tenant (schema do tenant, DL-0017): a identidade de tenant e' a DO STORE,
    nunca a da semente da ordem."""
    return key_segment(ledger.tenant)


def _nivel2(indicios: IndiciosDuplicidade, valor_ordem: int, valor_origem: int | None) -> str:
    if indicios.nivel2_soma_outras_cents is None or valor_origem is None:
        return NIVEL_NAO_AVALIAVEL
    total = indicios.nivel2_soma_outras_cents + valor_ordem
    return NIVEL2_EXCEDE if total > valor_origem else NIVEL2_NAO_EXCEDE


def detect_duplicidade(
    variables: Mapping[str, Any],
    *,
    ledger: RazaoObrigacoesPagamento | None,
    dmn: DmnTransport | None,
) -> dict[str, Any]:
    """Niveis 1-3 da analise PAGTO §4 -> fatos + `duplicidade_status` pela DMN. So' levanta erro
    de programacao."""
    if ledger is None:
        logger.warning("pagto_duplicidade_razao_ausente")
        return _fatos(STATUS_INDETERMINADA, MOTIVO_RAZAO_INDISPONIVEL)
    instancia, valor_origem = _origem_conferida(variables)
    entrada = _entrada(_tenant_da_razao(ledger), EVENTO_REGISTRADA, variables, origem_instancia_id=instancia)
    if entrada is None:
        logger.info("pagto_duplicidade_sem_identidade")
        return _fatos(STATUS_INDETERMINADA, MOTIVO_DADOS_INSUFICIENTES)
    try:
        indicios = asyncio.run(ledger.registrar_e_ler(entrada))
    except PROGRAMMING_ERRORS:
        raise
    except EXTERNAL_DEPENDENCY_FAILURES as exc:
        # `RazaoIndisponivelError` e' `RuntimeError`. "Nao sei" — nunca "nao ha duplicidade".
        logger.warning("pagto_duplicidade_razao_indisponivel", erro=type(exc).__name__)
        return _fatos(STATUS_INDETERMINADA, MOTIVO_RAZAO_INDISPONIVEL, chave=entrada.chave_obrigacao)

    nivel2 = _nivel2(indicios, entrada.valor_cents, valor_origem)
    conflitante = indicios.nivel1_ordem_id or indicios.nivel3_ordem_id
    evidencia: dict[str, Any] = {
        "chave": entrada.chave_obrigacao,
        "ordem_conflitante": conflitante,
        "nivel1": indicios.nivel1_situacao,
        "nivel2": nivel2,
        "nivel3_idade": indicios.nivel3_similar_idade_dias,
    }
    if dmn is None:
        logger.warning("pagto_duplicidade_dmn_ausente")
        return _fatos(STATUS_INDETERMINADA, MOTIVO_DMN_INDISPONIVEL, **evidencia)
    entradas_dmn = {
        "nivel1_situacao": indicios.nivel1_situacao,
        "nivel2_situacao": nivel2,
        "nivel3_avaliado": indicios.nivel3_avaliado,
        "nivel3_similar_idade_dias": indicios.nivel3_similar_idade_dias,
    }
    try:
        rows, version = evaluate_sync(dmn, DMN_DUPLICIDADE, entradas_dmn)
    except PROGRAMMING_ERRORS:
        raise
    except EXTERNAL_DEPENDENCY_FAILURES as exc:
        # `DmnEvaluationError` (motor fora / nao-2xx / versao irresolvivel) e' `RuntimeError`.
        logger.warning("pagto_duplicidade_dmn_indisponivel", erro=type(exc).__name__)
        return _fatos(STATUS_INDETERMINADA, MOTIVO_DMN_INDISPONIVEL, **evidencia)
    row = first_row(rows, DMN_DUPLICIDADE, entradas_dmn)
    status = str(row.get(DUPLICIDADE_STATUS_VAR, ""))
    motivo = str(row.get(DUPLICIDADE_MOTIVO_VAR, ""))
    if status not in DUPLICIDADE_STATUS or not motivo:
        logger.error("pagto_duplicidade_dmn_saida_invalida", dmn_decision_version=version.version)
        return _fatos(STATUS_INDETERMINADA, MOTIVO_DMN_SAIDA_INVALIDA, **evidencia)
    logger.info(
        "pagto_duplicidade_classificada",
        duplicidade_status=status,
        duplicidade_motivo_codigo=motivo,
        nivel1=indicios.nivel1_situacao,
        nivel2=nivel2,
        nivel3_avaliado=indicios.nivel3_avaliado,
        gravou=indicios.gravou,
        dmn_decision_version=version.version,
    )
    return _fatos(status, motivo, **evidencia)


def validar_com_duplicidade(
    variables: dict[str, Any],
    *,
    validar: Callable[[dict[str, Any]], dict[str, Any]],
    ledger: RazaoObrigacoesPagamento | None,
    dmn: DmnTransport | None,
) -> dict[str, Any]:
    """O handler de `operadora.pagto.validate_payment_data` com a flag ligada: a validacao de sempre
    (inclusive o `ERR_PAGTO_ORDEM_INVALIDA`, que sai ANTES de qualquer escrita; e os fatos do lastro
    quando o PR-D esta ligado) + os fatos da duplicidade. As chaves anteriores nunca sao
    sobrescritas."""
    saida = dict(validar(variables))
    contexto = {**variables, **saida}
    for chave, valor in detect_duplicidade(contexto, ledger=ledger, dmn=dmn).items():
        saida.setdefault(chave, valor)
    return saida


def _liberou(saida: Mapping[str, Any]) -> bool:
    """Os dois workers de liberacao dizem que liberaram com estes booleanos (`execute_pagto` ->
    `pagamento_executado`; `release_high_value_payment` -> `pagamento_liberado`)."""
    return saida.get("pagamento_executado") is True or saida.get("pagamento_liberado") is True


def registrar_liberacao(
    variables: dict[str, Any],
    *,
    liberar: Callable[[dict[str, Any]], dict[str, Any]],
    ledger: RazaoObrigacoesPagamento | None,
) -> dict[str, Any]:
    """Envolve um worker de liberacao: o GUARD de sempre roda primeiro e decide; so' depois de uma
    liberacao devolvida a linha `LIBERADA` e' gravada. Recusa do guard sobe intacta (nada gravado)
    e o retorno do guard volta byte a byte. Falha da razao NAO desfaz nem bloqueia a liberacao ja
    decidida pelo guard: a `REGISTRADA` da validacao continua na razao (o nivel 1 segue achando
    a obrigacao), e a falha sai alta no log."""
    saida = liberar(variables)
    if not _liberou(saida):
        return saida
    if ledger is None:
        logger.error("pagto_duplicidade_liberacao_nao_registrada", motivo="razao_ausente")
        return saida
    instancia, _valor_origem = _origem_conferida(variables)
    entrada = _entrada(_tenant_da_razao(ledger), EVENTO_LIBERADA, variables, origem_instancia_id=instancia)
    if entrada is None:
        logger.error("pagto_duplicidade_liberacao_nao_registrada", motivo="sem_identidade")
        return saida
    try:
        gravou = asyncio.run(ledger.registrar_liberacao(entrada))
    except PROGRAMMING_ERRORS:
        raise
    except EXTERNAL_DEPENDENCY_FAILURES as exc:
        logger.error(
            "pagto_duplicidade_liberacao_nao_registrada", motivo="razao_indisponivel", erro=type(exc).__name__
        )
        return saida
    logger.info("pagto_duplicidade_liberacao_registrada", gravou=gravou)
    return saida


__all__ = [
    "DMN_DUPLICIDADE",
    "DUPLICIDADE_EVIDENCIAS",
    "DUPLICIDADE_SAIDAS",
    "DUPLICIDADE_STATUS",
    "NIVEL1_SITUACOES",
    "NIVEL2_SITUACOES",
    "TOPIC_VALIDATE_PAYMENT_DATA",
    "VALIDATE_COM_DUPLICIDADE_VARIABLES",
    "detect_duplicidade",
    "registrar_liberacao",
    "validar_com_duplicidade",
]
