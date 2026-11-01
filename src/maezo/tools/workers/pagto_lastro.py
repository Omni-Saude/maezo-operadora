"""SP-OP-PAGTO-001 — `resolve_lastro`: o lastro da ordem conferido contra o HISTORICO do motor.

Pacote PAGTO PR-D (analise PAGTO §2 fatos L1-L4, §9 item 4; ADR-0040 D6-bis I-PAGTO-1: a origem
fornece EVIDENCIA, PAGTO resolve o FATO). DARK: so roda com `MAEZO_PAGTO_RESOLVE_LASTRO` ligada.

O QUE FAZ. Para a ordem em maos, acha no historico do motor (costura `resolve_historic_instance`
de PR-C, padrao DL-0050, falha fechada) a instancia de ORIGEM que a produziu e devolve FATOS:

  * `lastro_status` — `CONFIRMADO` | `DIVERGENTE_VALOR` | `NAO_ENCONTRADO` | `NAO_CONCLUIDO` |
    `INDISPONIVEL` (vocabulario da analise §2);
  * `lastro_origem_tipo` — `contas_humana` | `contas_auto_sem_glosa` | `contas_auto_triagem` |
    `recurso_humano` | `outro`;
  * a evidencia usada: `lastro_origem_instancia_id`, `lastro_origem_terminal` (o evento final
    que encerrou a origem) e `lastro_valor_origem_cents` (o valor que a origem registrou).

O QUE NAO FAZ. Nao decide rota nem libera nada: quem decide e' a DMN (`pagto_admissibility` v0.2,
pacote PR-F, le `lastro_status`/`lastro_origem_tipo`; a elegibilidade de origem do dono — humana +
sem glosa + recurso deferido, triagem PAGAR fora ate M-4 — e linha DE DMN sobre
`lastro_origem_tipo`, nunca um `if` aqui). Nao toca `lastro_confirmado`: a DMN viva (v0.1) segue
lendo o booleano que `validate_pagto` produz, entao ligar a flag NAO muda rota nenhuma — so
acrescenta os fatos ao escopo, para a sombra (PR-G) medir.

FONTE DO FATO. Nunca a semente: o processo de origem a escolher vem da evidencia `lastro_origem`
(so' diz ONDE procurar — uma semente errada so' produz `NAO_ENCONTRADO`), e a identidade usada na
busca e' a da propria ordem (`numero_lote_tiss`+`prestador_id` para CONTAS — as duas formas de
chave de CONTAS-001, lote e guia/conta, carregam essas variaveis; `RECURSO-{tenant}-{guia}-{glosa}`
para RECURSO, a chave contratual). Terminal, valor e `has_glosas` sao lidos do que o MOTOR gravou
na instancia de origem.

FALHA FECHADA. Costura ausente, motor fora, resposta ambigua/malformada, estado desconhecido ou
qualquer excecao viram `INDISPONIVEL` — nunca `CONFIRMADO`, e nunca uma excecao que derrube a
validacao da ordem (o fato vai a humano; a validacao segue como sempre foi).

TOLERANCIA DE VALOR (decisao do dono, 31/10/2026, "Pagamentos": abaixo da origem segue, acima vai a
humano): `CONFIRMADO` quando `valor_pagamento_cents` <= valor da origem; acima, ou qualquer lado
sem valor confiavel, `DIVERGENTE_VALOR`. O valor da origem vai junto como evidencia, para a DMN.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Final

import structlog

from maezo.platform.keys import key_segment
from maezo.tools.mcp_cibseven.transport import (
    HistoricInstanceNotFoundError,
    HistoricInstanceRecord,
    HistoricInstanceResolvingTransport,
)
from maezo.tools.workers import contas as _contas
from maezo.tools.workers import recurso as _recurso
from maezo.tools.workers.pagto import _valor_pagamento_cents_or_none

logger = structlog.get_logger(__name__)

TOPIC_VALIDATE_PAYMENT_DATA: Final[str] = "operadora.pagto.validate_payment_data"

# ---------------------------------------------------------------------------------------------
# Vocabulario FECHADO dos fatos (analise PAGTO §2). Os dois nomes de fato ja estao em
# `pagto.PAGTO_FATOS_PROIBIDOS_NA_SEMENTE` (nenhuma origem os semeia).
# ---------------------------------------------------------------------------------------------

LASTRO_STATUS_VAR: Final[str] = "lastro_status"
LASTRO_ORIGEM_TIPO_VAR: Final[str] = "lastro_origem_tipo"
LASTRO_ORIGEM_INSTANCIA_VAR: Final[str] = "lastro_origem_instancia_id"
LASTRO_ORIGEM_TERMINAL_VAR: Final[str] = "lastro_origem_terminal"
LASTRO_VALOR_ORIGEM_VAR: Final[str] = "lastro_valor_origem_cents"

#: As cinco chaves que o worker escreve — sempre todas, em todo caminho.
LASTRO_SAIDAS: Final[frozenset[str]] = frozenset(
    {
        LASTRO_STATUS_VAR,
        LASTRO_ORIGEM_TIPO_VAR,
        LASTRO_ORIGEM_INSTANCIA_VAR,
        LASTRO_ORIGEM_TERMINAL_VAR,
        LASTRO_VALOR_ORIGEM_VAR,
    }
)

STATUS_CONFIRMADO: Final[str] = "CONFIRMADO"
STATUS_DIVERGENTE_VALOR: Final[str] = "DIVERGENTE_VALOR"
STATUS_NAO_ENCONTRADO: Final[str] = "NAO_ENCONTRADO"
STATUS_NAO_CONCLUIDO: Final[str] = "NAO_CONCLUIDO"
STATUS_INDISPONIVEL: Final[str] = "INDISPONIVEL"
LASTRO_STATUS: Final[frozenset[str]] = frozenset(
    {
        STATUS_CONFIRMADO,
        STATUS_DIVERGENTE_VALOR,
        STATUS_NAO_ENCONTRADO,
        STATUS_NAO_CONCLUIDO,
        STATUS_INDISPONIVEL,
    }
)

TIPO_CONTAS_HUMANA: Final[str] = "contas_humana"
TIPO_CONTAS_AUTO_SEM_GLOSA: Final[str] = "contas_auto_sem_glosa"
TIPO_CONTAS_AUTO_TRIAGEM: Final[str] = "contas_auto_triagem"
TIPO_RECURSO_HUMANO: Final[str] = "recurso_humano"
TIPO_OUTRO: Final[str] = "outro"
LASTRO_ORIGEM_TIPOS: Final[frozenset[str]] = frozenset(
    {
        TIPO_CONTAS_HUMANA,
        TIPO_CONTAS_AUTO_SEM_GLOSA,
        TIPO_CONTAS_AUTO_TRIAGEM,
        TIPO_RECURSO_HUMANO,
        TIPO_OUTRO,
    }
)

#: Terminal da perna automatica de CONTAS (as DUAS pernas automaticas — `has_glosas==false` e
#: `triagem.roteamento=='PAGAR'` — terminam nele; `has_glosas` historico as distingue).
_CONTAS_TERMINAL_AUTOMATICO: Final[str] = "End_ContaAprovadaIntegral"

_FAMILIA_CONTAS: Final[str] = "contas"
_FAMILIA_RECURSO: Final[str] = "recurso"


@dataclass(frozen=True, slots=True)
class FonteLastro:
    """Onde e como conferir uma familia de origem. FATOS DO BPMN, nao regra: `terminais` sao os
    eventos finais alcancaveis a partir das tarefas de handoff da origem (cercado por teste contra o
    proprio arquivo BPMN), cada um com o `lastro_origem_tipo` que ele atesta; `variavel_valor` diz
    de onde o valor da origem e' lido para cada terminal (a mesma variavel que o handoff usou)."""

    familia: str
    process_key: str
    terminais: Mapping[str, str]
    variavel_valor: Mapping[str, str]
    parse_valor: Callable[[Any], float | None]
    to_cents: Callable[[float], int]


#: SP-OP-CONTAS-001: `fonte_valor=apresentado` na perna automatica, `liberado` nas humanas
#: (`ST_HandoffPagamento*`, `contas.FONTE_VALOR`).
FONTE_CONTAS: Final[FonteLastro] = FonteLastro(
    familia=_FAMILIA_CONTAS,
    process_key="SP-OP-CONTAS-001",
    terminais={
        _CONTAS_TERMINAL_AUTOMATICO: TIPO_CONTAS_AUTO_SEM_GLOSA,  # refinado por `has_glosas`
        "End_ContaAprovadaHumano": TIPO_CONTAS_HUMANA,
        "End_PagamentoParcialHumano": TIPO_CONTAS_HUMANA,
    },
    variavel_valor={
        _CONTAS_TERMINAL_AUTOMATICO: _contas.FONTE_VALOR["apresentado"],
        "End_ContaAprovadaHumano": _contas.FONTE_VALOR["liberado"],
        "End_PagamentoParcialHumano": _contas.FONTE_VALOR["liberado"],
    },
    parse_valor=_contas._parse_valor_monetario,
    to_cents=_contas._to_cents,
)

#: SP-OP-RECURSO-001: as duas pernas de deferimento (`valor_deferido_brl`, `fonte_valor=deferido`).
FONTE_RECURSO: Final[FonteLastro] = FonteLastro(
    familia=_FAMILIA_RECURSO,
    process_key="SP-OP-RECURSO-001",
    terminais={
        "End_RecursoDeferido": TIPO_RECURSO_HUMANO,
        "End_RecursoDeferidoParcial": TIPO_RECURSO_HUMANO,
    },
    variavel_valor={
        "End_RecursoDeferido": "valor_deferido_brl",
        "End_RecursoDeferidoParcial": "valor_deferido_brl",
    },
    parse_valor=_recurso._parse_valor_monetario,
    to_cents=_recurso._to_cents,
)

#: Evidencia `lastro_origem` (semeada pelo handoff) -> familia a consultar. So' ESCOLHE onde
#: procurar; o fato vem do historico.
FONTE_POR_EVIDENCIA: Final[Mapping[str, FonteLastro]] = {
    _contas.LASTRO_ORIGEM_AUTOMATICA: FONTE_CONTAS,
    _contas.LASTRO_ORIGEM_HUMANA: FONTE_CONTAS,
    _recurso.LASTRO_ORIGEM_RECURSO: FONTE_RECURSO,
}

#: Escopo de leitura EXPLICITO do topico `validate_payment_data` quando a flag liga (menor
#: privilegio): o que `validate_pagto` le (cercado por teste contra o corpo da funcao) + o que
#: `resolve_lastro` le.
VALIDATE_COM_LASTRO_VARIABLES: Final[tuple[str, ...]] = (
    "ordem_pagamento_id",
    "lastro_confirmado",
    "duplicidade_suspeita",
    "prestador_id",
    "instrumento_pagamento",
    "tenant_id",
    "lastro_origem",
    "numero_lote_tiss",
    "numero_guia_tiss",
    "glosa_id",
    "valor_pagamento_cents",
)

_ESTADOS_EM_CURSO: Final[frozenset[str]] = frozenset({"ACTIVE", "SUSPENDED"})
_ESTADOS_ENCERRADOS_SEM_TERMINAL: Final[frozenset[str]] = frozenset(
    {"EXTERNALLY_TERMINATED", "INTERNALLY_TERMINATED"}
)


def _fatos(
    status: str,
    tipo: str = TIPO_OUTRO,
    *,
    instancia_id: str = "",
    terminal: str = "",
    valor_origem_cents: int | None = None,
) -> dict[str, Any]:
    return {
        LASTRO_STATUS_VAR: status,
        LASTRO_ORIGEM_TIPO_VAR: tipo,
        LASTRO_ORIGEM_INSTANCIA_VAR: instancia_id,
        LASTRO_ORIGEM_TERMINAL_VAR: terminal,
        LASTRO_VALOR_ORIGEM_VAR: valor_origem_cents,
    }


def _consultar(
    engine: HistoricInstanceResolvingTransport, fonte: FonteLastro, variables: Mapping[str, Any]
) -> HistoricInstanceRecord | None:
    """A UNICA instancia de origem desta ordem (ou `None` quando a ordem nao carrega identidade
    para a busca). Erros da costura PROPAGAM — quem chama os classifica."""
    tenant = key_segment(variables.get("tenant_id"))
    if fonte.familia == _FAMILIA_CONTAS:
        lote = key_segment(variables.get("numero_lote_tiss"))
        prestador = key_segment(variables.get("prestador_id"))
        if not (lote and prestador):
            return None
        nomes: tuple[str, ...] = ("has_glosas", *sorted(set(fonte.variavel_valor.values())))
        return asyncio.run(
            engine.resolve_historic_instance(
                fonte.process_key,
                variable_equals={"numero_lote_tiss": lote, "prestador_id": prestador},
                names=nomes,
            )
        )
    guia = key_segment(variables.get("numero_guia_tiss"))
    glosa = key_segment(variables.get("glosa_id"))
    if not (tenant and guia and glosa):
        return None
    return asyncio.run(
        engine.resolve_historic_instance(
            fonte.process_key,
            # A chave contratual de RECURSO-001 (`recurso._recurso_business_key`).
            business_key=_recurso._recurso_business_key(tenant, guia, glosa),
            names=("prestador_id", *sorted(set(fonte.variavel_valor.values()))),
        )
    )


def resolve_lastro(
    variables: Mapping[str, Any],
    *,
    engine: Any = None,
    fontes: Mapping[str, FonteLastro] = FONTE_POR_EVIDENCIA,
) -> dict[str, Any]:
    """L1-L4 da analise PAGTO §2 -> fatos. Nunca levanta; nunca escreve `lastro_confirmado`.

    `fontes` existe SO' para o teste de integracao apontar a mesma logica para processos ad hoc
    num motor real (sem redeployar `SP-OP-*` no motor compartilhado); producao usa o default."""
    evidencia = key_segment(variables.get("lastro_origem"))
    fonte = fontes.get(evidencia)
    if fonte is None:
        # Ordem sem origem conferivel (manual, Andre, evidencia desconhecida): nada a conferir.
        logger.info("pagto_lastro_sem_origem_conferivel", lastro_origem=evidencia)
        return _fatos(STATUS_NAO_ENCONTRADO)
    if not isinstance(engine, HistoricInstanceResolvingTransport):
        logger.warning("pagto_lastro_costura_ausente", processo=fonte.process_key)
        return _fatos(STATUS_INDISPONIVEL)
    try:
        registro = _consultar(engine, fonte, variables)
    except HistoricInstanceNotFoundError:
        logger.info("pagto_lastro_origem_nao_encontrada", processo=fonte.process_key)
        return _fatos(STATUS_NAO_ENCONTRADO)
    except Exception as exc:  # costura fora/ambigua/malformada/negada: "nao sei", nunca "confirmado"
        logger.warning("pagto_lastro_indisponivel", processo=fonte.process_key, erro=type(exc).__name__)
        return _fatos(STATUS_INDISPONIVEL)
    if registro is None:
        logger.info("pagto_lastro_sem_identidade", processo=fonte.process_key)
        return _fatos(STATUS_NAO_ENCONTRADO)
    return _classificar(registro, fonte, variables)


def _classificar(
    registro: HistoricInstanceRecord, fonte: FonteLastro, variables: Mapping[str, Any]
) -> dict[str, Any]:
    instancia = registro.instance_id
    estado = (registro.state or "").strip().upper()
    terminal = registro.end_activity_id or ""
    if estado in _ESTADOS_EM_CURSO:
        return _fatos(STATUS_NAO_CONCLUIDO, instancia_id=instancia)
    if estado in _ESTADOS_ENCERRADOS_SEM_TERMINAL:
        return _fatos(STATUS_NAO_ENCONTRADO, instancia_id=instancia, terminal=terminal)
    if estado != "COMPLETED" or not terminal:
        # Estado que este modulo nao conhece, ou COMPLETED sem evento final provado: nao sei.
        return _fatos(STATUS_INDISPONIVEL, instancia_id=instancia, terminal=terminal)
    tipo = fonte.terminais.get(terminal)
    if tipo is None:
        # Terminou, mas num desfecho que nao gera obrigacao (glosa, devolucao, fraude, erro...).
        return _fatos(STATUS_NAO_ENCONTRADO, instancia_id=instancia, terminal=terminal)

    if fonte.familia == _FAMILIA_RECURSO:
        prestador_origem = key_segment(registro.variables.get("prestador_id"))
        if not prestador_origem or prestador_origem != key_segment(variables.get("prestador_id")):
            # L3: a origem achada pela chave e' de outro credor (ou nao o registrou).
            return _fatos(STATUS_NAO_ENCONTRADO, instancia_id=instancia, terminal=terminal)
    if terminal == _CONTAS_TERMINAL_AUTOMATICO and fonte.familia == _FAMILIA_CONTAS:
        has_glosas = registro.variables.get("has_glosas")
        if has_glosas is False:
            tipo = TIPO_CONTAS_AUTO_SEM_GLOSA
        elif has_glosas is True:
            tipo = TIPO_CONTAS_AUTO_TRIAGEM
        else:
            tipo = TIPO_OUTRO  # perna automatica sem o fato que a distingue: nao rotula

    bruto = fonte.parse_valor(registro.variables.get(fonte.variavel_valor[terminal]))
    origem_cents = fonte.to_cents(bruto) if bruto is not None and bruto > 0 else None
    ordem_cents, _motivo = _valor_pagamento_cents_or_none(variables.get("valor_pagamento_cents"))
    if origem_cents is None or ordem_cents is None or ordem_cents > origem_cents:
        status = STATUS_DIVERGENTE_VALOR
    else:
        status = STATUS_CONFIRMADO
    logger.info(
        "pagto_lastro_resolvido",
        processo=fonte.process_key,
        lastro_status=status,
        lastro_origem_tipo=tipo,
        terminal=terminal,
    )
    return _fatos(status, tipo, instancia_id=instancia, terminal=terminal, valor_origem_cents=origem_cents)


def validar_com_lastro(
    variables: dict[str, Any],
    *,
    validar: Callable[[dict[str, Any]], dict[str, Any]],
    engine: Any = None,
) -> dict[str, Any]:
    """O handler de `operadora.pagto.validate_payment_data` com a flag ligada: a validacao de
    sempre (inclusive o `ERR_PAGTO_ORDEM_INVALIDA`, que continua saindo ANTES de qualquer leitura)
    + os fatos do lastro. As chaves da validacao nunca sao sobrescritas."""
    saida = dict(validar(variables))
    for chave, valor in resolve_lastro(variables, engine=engine).items():
        saida.setdefault(chave, valor)
    return saida


__all__ = [
    "FONTE_CONTAS",
    "FONTE_POR_EVIDENCIA",
    "FONTE_RECURSO",
    "LASTRO_ORIGEM_TIPOS",
    "LASTRO_SAIDAS",
    "LASTRO_STATUS",
    "TOPIC_VALIDATE_PAYMENT_DATA",
    "VALIDATE_COM_LASTRO_VARIABLES",
    "FonteLastro",
    "resolve_lastro",
    "validar_com_lastro",
]
