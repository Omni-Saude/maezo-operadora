"""Passagem Helena -> agente `comercial` por intencao de contratar (E-SF-17 / GTM-E-13).

DESLIGADA por padrao (`roteador_comercial_enabled=False`): desligada, `ligar` devolve a aresta de
antes sem tocar o grafo, e o grafo compilado da Helena e' o de antes, no a no' (nem o no'
`handoff_comercial` existe). Ligar depende do ato do dono que estende ADR-0062/DL-0053 a Compras e
dos portoes de GTM-E-12 (GTM-01.R1/R2/R3 e OWNER-DEPLOY). Nenhuma composicao passa `True` hoje.

PRECEDENCIA (GTM-01 D4): as travas da Helena rodam inteiras primeiro. A passagem so' e' considerada
na ARESTA que sai do `classify`, quando ele terminou em resposta informativa (`next_kind == "inform"`
e `_inform_recusado` vazio) — ou seja, sem bandeira da DMN, sem risco psicossocial, sem pergunta
clinica e sem pedido de pessoa —, e mesmo assim e' recusada se houver QUALQUER sinal de saude no
turno. Saude vence comercial.

A SAIDA TIPADA (`HandoffComercial`) carrega so' referencias: nunca o texto da mensagem, sintoma,
motivo de saude ou telefone. O lado do agente comercial repete o formato
(`agents/comercial/passagem.py`, sem import entre agentes — ADR-0067 §1); uma cerca de paridade
compara os dois.

LIMITE CONHECIDO (A9): com a flag ligada, `respond` rotula o turno pela regra existente da passagem
sem frase (`passagem_cobranca_sem_frase`). Um token proprio seria medicao nova, que este pacote nao
emite; fica como GAP para quando a ativacao (OWNER-DEPLOY) for decidida.

IMPORT CIRCULAR SEGURO: o `graph.py` importa este modulo depois dos helpers de modulo, e este modulo
importa os nomes do graph NO FIM, como as demais habilidades.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Hashable, Mapping
from typing import TYPE_CHECKING, Any, Final, Literal, TypedDict

if TYPE_CHECKING:
    from langgraph.graph import StateGraph

    from maezo.agents.helena.graph import HelenaGraph

#: Mesmo valor de `maezo.agents.comercial.passagem.PASSAGEM_SCHEMA` (cerca de paridade).
PASSAGEM_SCHEMA: Final[str] = "esf17-passagem-comercial.proposed.v1"
MOTIVO_INTENCAO_CONTRATAR: Final[str] = "intencao_contratar"

#: Nome da aresta/no' da passagem no grafo (so' existe com o roteador comercial ligado).
NO_HANDOFF_COMERCIAL: Final[str] = "handoff_comercial"

_MESSAGE_REF: Final[re.Pattern[str]] = re.compile(r"hk1_[0-9a-f]{64}\Z")

#: PROVISORIO (lexico de intencao, nao regra de negocio): o pedido explicito de contratar ou cotar
#: um plano. Nao decide elegibilidade, preco nem produto; so' escolhe para quem a conversa vai. A
#: lista e' conservadora de proposito: fora dela, a Helena segue como antes.
_INTENCAO_CONTRATAR: Final[re.Pattern[str]] = re.compile(
    r"\b(?:quero|queria|gostaria de|preciso|como (?:faco|posso) para|posso)\s+"
    r"(?:contratar|cotar|comprar|assinar|fazer)\s+(?:um |o |uma )?(?:novo )?plano\b"
    r"|\b(?:cotacao|orcamento) (?:de|do|para|pra) (?:um |o )?plano\b"
)


class HandoffComercial(TypedDict):
    """A saida TIPADA da passagem. SAIDA-ONLY do `HelenaState`; so' o no' `handoff_comercial` a escreve."""

    schema_version: str
    para: Literal["comercial"]
    motivo: Literal["intencao_contratar"]
    message_ref: str


def _normalizar(texto: str) -> str:
    decomposto = unicodedata.normalize("NFKD", texto.lower())
    return "".join(c for c in decomposto if not unicodedata.combining(c))


def intencao_de_contratar(texto: str) -> bool:
    """O texto pede, explicitamente, contratar ou cotar um plano?"""
    return bool(_INTENCAO_CONTRATAR.search(_normalizar(texto)))


def message_ref_valida(valor: object) -> bool:
    return type(valor) is str and _MESSAGE_REF.fullmatch(valor) is not None


def construir_handoff(message_ref: str) -> HandoffComercial:
    """O UNICO construtor da passagem: so' a referencia da mensagem, nunca o corpo."""
    if not message_ref_valida(message_ref):
        raise ValueError("helena_handoff_comercial_message_ref_invalida")
    return {
        "schema_version": PASSAGEM_SCHEMA,
        "para": "comercial",
        "motivo": "intencao_contratar",
        "message_ref": message_ref,
    }


def passagem_recusada(self: HelenaGraph, state: Mapping[str, Any]) -> str | None:
    """Precondicao DETERMINISTICA da passagem. Devolve o token da condicao violada, ou `None`."""
    if not self._roteador_comercial_enabled:
        return "roteador_comercial_desligado"
    if state.get("next_kind") != "inform" or _inform_recusado(state) is not None:
        return "nao_informativo"
    texto = str(state.get("message_body") or "")
    if _mensagem_de_saude(state) or _conteudo_clinico(texto):
        return "sinal_de_saude"
    if state.get("pedido_humano_lexico") is True or _pedido_explicito_de_pessoa(texto):
        return "pedido_de_pessoa"
    if not intencao_de_contratar(texto):
        return "sem_intencao_de_contratar"
    if not message_ref_valida(state.get("message_ref")):
        return "message_ref_invalida"
    return None


def ligar(
    self: HelenaGraph,
    g: StateGraph[HelenaState],
    destinos: dict[Hashable, str],
    anterior: Callable[[HelenaState], str],
) -> Callable[[HelenaState], str]:
    """Com o roteador comercial DESLIGADO devolve `anterior` e nao toca o grafo. Ligado, acrescenta o
    no' de passagem e devolve a aresta que o escolhe so' quando a precondicao deixa."""
    if not self._roteador_comercial_enabled:
        return anterior
    g.add_node(NO_HANDOFF_COMERCIAL, self.handoff_comercial)
    g.add_edge(NO_HANDOFF_COMERCIAL, "respond")
    destinos[NO_HANDOFF_COMERCIAL] = NO_HANDOFF_COMERCIAL

    def aresta(state: HelenaState) -> str:
        return NO_HANDOFF_COMERCIAL if passagem_recusada(self, state) is None else anterior(state)

    return aresta


async def handoff_comercial(self: HelenaGraph, state: HelenaState) -> dict[str, Any]:
    """Escreve `handoff_comercial` (so' a referencia) e nenhuma frase: quem se apresenta e' o agente
    comercial. Confere a precondicao de novo (2a camada); recusada, fica OBSERVAVEL (`error` + a
    guarda HEL-07 de resposta vazia em `respond`), nunca uma passagem silenciosa."""
    recusa = passagem_recusada(self, state)
    if recusa is not None:
        logger.warning("helena_handoff_comercial_recusado", motivo=recusa, node=NO_HANDOFF_COMERCIAL)
        return {"error": f"handoff_comercial_recusado:{recusa}", "response_text": ""}
    logger.info("helena_handoff_comercial", node=NO_HANDOFF_COMERCIAL)
    return {
        "handoff_comercial": construir_handoff(str(state.get("message_ref"))),
        "response_kind": RESPONSE_KIND_HANDOFF,
        "response_text": "",
    }


from maezo.agents.helena.graph import (  # noqa: E402
    RESPONSE_KIND_HANDOFF,
    HelenaState,
    _conteudo_clinico,
    _inform_recusado,
    _mensagem_de_saude,
    _pedido_explicito_de_pessoa,
    logger,
)
