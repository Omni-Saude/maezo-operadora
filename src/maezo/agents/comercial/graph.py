"""Grafo do agente `comercial` (E-SF-17 / GTM-E-12 e SC-03): conduzir e encaminhar, nunca decidir.

O QUE ELE FAZ
-------------
Dois modos da MESMA definicao (`spec/agents/comercial/agent.yaml`):

* `prospect` (GTM-E-12): conversa iniciada pelo prospect, depois da passagem da Helena. No 1o turno
  o texto se identifica como atendimento automatizado e oferece uma pessoa da equipe comercial.
  Fora do 1o turno, sem catalogo publicado (CR-F2) e sem as ratificacoes R1/R2, o agente nao
  recomenda nada: encaminha ao Especialista comercial (fila humana `sales_operations`), que e' o
  fallback "D1 sozinho" do plano.
* `staff_assist` (SC-03, copiloto interno): nunca fala com o prospect. Devolve a rota de nota
  interna ao especialista; o envio ao comprador e' sempre clique do humano. Sem conteudo gerado
  enquanto SC-03.D1 (DPO) nao responder. O turno devolve, como DADO, a acao de autonomia que o
  rascunho exerce (`staff_assist_draft`, L1: o agente so' PREPARA, o humano revisa e decide); quem
  avalia a acao e' o gateway (ADR-0067: o agente nao importa o PEP). Nunca ha' acao de envio.

PRECEDENCIA (fixa, nesta ordem): sinal de saude -> volta a Helena (saida clinica, sem texto
comercial); pedido de pessoa -> fila humana; so' entao o modo.

O QUE ELE NUNCA FAZ
-------------------
Nenhum LLM, nenhum DMN, nenhum processo (`process_keys: []`), nenhum dado de saude, nenhuma acao
`hard`, nenhum envio proativo e nenhuma decisao de elegibilidade, preco, desconto, comissao,
aceite ou legitimidade. O grafo nao envia mensagem: devolve o texto e a acao de envio
(`send_prospect_message`) para o despachante, que hoje nao existe para este agente (flag
desligada). O envio, quando existir, passa pelo chokepoint do WhatsApp em `shadow`.

TELEMETRIA: o no terminal `finalize` entrega o desfecho a `runtime.turn_telemetry`, que valida o
vocabulario e NAO grava no contador (medicao nao admitida para este agente).

Imports: so' o SDK do runtime e bibliotecas (ADR-0067 §4.1); nenhum outro agente.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final, Literal, TypedDict, cast

import structlog
from langgraph.graph import END, START, StateGraph

from maezo.runtime.turn_telemetry import emit_turn_desfecho

from .passagem import ComercialInputError, parse_passagem

logger = structlog.get_logger(__name__)

AGENT_ID: Final[str] = "comercial"

Modo = Literal["prospect", "staff_assist"]
MODOS: Final[frozenset[str]] = frozenset({"prospect", "staff_assist"})

#: A acao de autonomia que o envio ao prospect exerce (`spec/policies/autonomy/L0-core.yaml`, L3).
ACAO_ENVIO_PROSPECT: Final[str] = "send_prospect_message"

#: E-SF-ADD01-03: a acao de autonomia que o rascunho do copiloto exerce (`L0-core.yaml`, L1, sem
#: parametros, sem tool, nao `hard`). L1 = o agente so' prepara; o gateway devolve REQUIRE_HUMAN.
ACAO_RASCUNHO_ATENDENTE: Final[str] = "staff_assist_draft"

#: Rotas fechadas do turno.
ROTA_SAIDA_CLINICA: Final[str] = "devolver_helena"
ROTA_FILA_HUMANA: Final[str] = "fila_humana_sales_operations"
ROTA_CONVERSA: Final[str] = "conversa_comercial"
ROTA_NOTA_INTERNA: Final[str] = "nota_interna_especialista"

#: PROVISORIO (GTM-01.R3, jurídico): o texto exato da identificacao do agente automatizado depende
#: da ratificacao. O criterio de aceite fixa so' o conteudo minimo: "atendimento automatizado" e a
#: oferta de uma pessoa. Sem nome de persona (GAP-PERSONA-NAME), sem prazo, sem promessa de cobertura.
TEXTO_IDENTIFICACAO_PRIMEIRO_TURNO: Final[str] = (
    "Olá! Este é um atendimento automatizado da equipe comercial. "
    "Se preferir falar com uma pessoa da equipe comercial, é só pedir."
)

#: PROVISORIO: o encaminhamento ao especialista. Sem ETA (prazo de primeira resposta so' com o N
#: assinado pelo dono, SC-02) e sem afirmar que o pedido foi resolvido.
TEXTO_ENCAMINHAMENTO_ESPECIALISTA: Final[str] = (
    "Vou passar sua conversa para uma pessoa da equipe comercial, que continua daqui."
)


class ComercialState(TypedDict, total=False):
    """Estado do turno. TODO campo esta' classificado abaixo (guarda de import-time)."""

    # --- ENTRADA: so' o despachante preenche. Nenhum texto do prospect entra aqui. ---
    tenant_id: str
    conversation_id: str
    modo: str
    passagem: Mapping[str, Any]
    primeiro_turno: bool
    #: Sinais deterministicos do receptor, depois da triagem dele; o agente nao le texto para isso.
    sinal_saude: bool
    pedido_humano: bool

    # --- SAIDA: so' os nos deste grafo escrevem. ---
    rota: str
    response_text: str
    acao_de_envio: str | None
    #: So' no modo `staff_assist`: a acao do rascunho (L1), devolvida como dado; nunca um envio.
    acao_de_rascunho: str | None
    envio_pelo_humano: bool
    desfecho: str
    error: str | None


INPUT_FIELDS: Final[frozenset[str]] = frozenset(
    {"tenant_id", "conversation_id", "modo", "passagem", "primeiro_turno", "sinal_saude", "pedido_humano"}
)

NEUTRAL_OUTPUTS: Final[Mapping[str, Any]] = {
    "rota": "",
    "response_text": "",
    "acao_de_envio": None,
    "acao_de_rascunho": None,
    "envio_pelo_humano": False,
    "desfecho": "",
    "error": None,
}


def _assert_particao_completa() -> None:
    declarados = frozenset(ComercialState.__annotations__)
    classificados = INPUT_FIELDS | frozenset(NEUTRAL_OUTPUTS)
    if declarados != classificados or INPUT_FIELDS & frozenset(NEUTRAL_OUTPUTS):
        raise RuntimeError(
            "particao input/output do ComercialState incompleta: "
            f"nao classificados={sorted(declarados - classificados)} "
            f"obsoletos={sorted(classificados - declarados)}"
        )


_assert_particao_completa()


def new_comercial_state(values: Mapping[str, object]) -> ComercialState:
    """Fronteira ESTRITA: chave fora de `INPUT_FIELDS` ou valor fora do tipo e' recusa tipada."""
    if not isinstance(values, Mapping) or not frozenset(values) <= INPUT_FIELDS:
        raise ComercialInputError("comercial_input_contract_mismatch")
    modo = values.get("modo")
    if modo not in MODOS:
        raise ComercialInputError("comercial_modo_invalido")
    for chave in ("primeiro_turno", "sinal_saude", "pedido_humano"):
        if type(values.get(chave, False)) is not bool:
            raise ComercialInputError("comercial_sinal_contract_mismatch")
    if modo == "prospect":
        # Conversa com prospect so' nasce da passagem tipada da Helena (sem corpo).
        parse_passagem(values.get("passagem"))
    elif "passagem" in values:
        # O copiloto interno nao recebe passagem de conversa.
        raise ComercialInputError("comercial_input_contract_mismatch")
    return cast(ComercialState, dict(values))


class ComercialGraph:
    """`receive -> decide -> finalize -> END`, sem efeito externo."""

    def receive(self, state: ComercialState) -> dict[str, Any]:
        """Revalida a entrada e zera toda saida plantada antes de qualquer no' ler o estado."""
        new_comercial_state({k: v for k, v in state.items() if k in INPUT_FIELDS})
        return dict(NEUTRAL_OUTPUTS)

    def decide(self, state: ComercialState) -> dict[str, Any]:
        if state.get("sinal_saude") is True:
            # Saude vence comercial: nenhum texto comercial, a conversa volta a Helena.
            logger.info("comercial_saida_clinica", agent=AGENT_ID)
            return {"rota": ROTA_SAIDA_CLINICA, "desfecho": "saida_clinica"}
        if state.get("pedido_humano") is True:
            return self._encaminhar("encaminhado_especialista_a_pedido", state)
        if state.get("modo") == "staff_assist":
            # SC-03: nota interna; quem envia ao comprador e' o humano. ADD01-03: a acao do rascunho
            # (L1) volta como dado; nenhum texto gerado e nenhuma acao de envio.
            return {
                "rota": ROTA_NOTA_INTERNA,
                "acao_de_rascunho": ACAO_RASCUNHO_ATENDENTE,
                "envio_pelo_humano": True,
                "desfecho": "nota_interna",
            }
        if state.get("primeiro_turno") is True:
            return {
                "rota": ROTA_CONVERSA,
                "response_text": TEXTO_IDENTIFICACAO_PRIMEIRO_TURNO,
                "acao_de_envio": ACAO_ENVIO_PROSPECT,
                "desfecho": "identificacao_enviada",
            }
        return self._encaminhar("encaminhado_especialista", state)

    @staticmethod
    def _encaminhar(desfecho: str, state: ComercialState) -> dict[str, Any]:
        prospect = state.get("modo") == "prospect"
        return {
            "rota": ROTA_FILA_HUMANA,
            "response_text": TEXTO_ENCAMINHAMENTO_ESPECIALISTA if prospect else "",
            "acao_de_envio": ACAO_ENVIO_PROSPECT if prospect else None,
            "envio_pelo_humano": not prospect,
            "desfecho": desfecho,
        }

    def finalize(self, state: ComercialState) -> dict[str, Any]:
        """No terminal: entrega o desfecho do turno ao ponto unico de telemetria (CC-09).

        Nao muda o estado. A gravacao no contador esta' fechada para este agente em
        `runtime.turn_telemetry._AGENTES_SEM_MEDICAO_ADMITIDA` (medicao nao admitida).
        """
        emit_turn_desfecho(state, agent_id=AGENT_ID, route=state.get("rota") or None)
        return {}

    def compile_graph(self) -> StateGraph[ComercialState]:
        g: StateGraph[ComercialState] = StateGraph(ComercialState)
        g.add_node("receive", self.receive)
        g.add_node("decide", self.decide)
        g.add_node("finalize", self.finalize)
        g.add_edge(START, "receive")
        g.add_edge("receive", "decide")
        g.add_edge("decide", "finalize")
        g.add_edge("finalize", END)
        return g


class ComercialDesligadoError(ValueError):
    """O agente esta' desligado: so' uma composicao que diga `comercial_ligado=True` o monta."""


def build(config: dict[str, Any] | None = None) -> StateGraph[ComercialState]:
    """Contrato `graph.py:build`. FAIL-CLOSED: sem `comercial_ligado is True`, recusa montar.

    A ativacao fora de dev depende de GTM-01.R1, R2, R3 e OWNER-DEPLOY; nenhuma raiz de composicao
    passa a chave hoje.
    """
    cfg = config or {}
    if cfg.get("comercial_ligado") is not True:
        raise ComercialDesligadoError(
            "agente comercial desligado (E-SF-17, flag off): a composicao precisa dizer "
            "comercial_ligado=True, o que depende dos portoes GTM-01.R1/R2/R3 e OWNER-DEPLOY"
        )
    return ComercialGraph().compile_graph()
