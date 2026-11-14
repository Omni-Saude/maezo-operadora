"""O turno do agente de ENTRADA do numero unico (ADR-0062), visto pela plataforma (ADR-0067).

O despachante do WhatsApp (`platform/webhooks/whatsapp/dispatch.py`) e' da plataforma: acesso do
beneficiario, teto de volume, custodia do telefone, deduplicacao, remetente cercado e a passagem ao Lucas.
O que e' do AGENTE — montar o grafo, o estado de entrada e de retomada, a memoria neutra, os escalonamentos
pelos nos do agente, as respostas fixas de cadastro — fica atras de `AgenteDeEntrada`. A implementacao e'
resolvida pelo framework de agentes a partir do id (`maezo.agents.carregar_agente_de_entrada("helena")`),
e o despachante nunca importa o agente.

ESTA E' A V1 DA PORTA, e ela ainda tem a forma da Helena: o despachante continua dono das chaves de
comportamento da Helena (`helena_*_enabled`) e as passa no `config` do grafo. Leva-las para o agente e'
a fase seguinte do ADR-0067; a porta muda junto, nos dois lados.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Final, Protocol

#: `intent` de cobranca no resultado do turno: o despachante o sintetiza no atalho do recuo do
#: cancelamento (DL-0111), com a mesma forma de um turno do agente que passou a conversa ao Lucas.
INTENT_COBRANCA: Final[str] = "cobranca"
#: `response_kind` do turno que passou a conversa ao Lucas.
RESPONSE_KIND_HANDOFF: Final[str] = "handoff"


@dataclass
class TurnoSemPreparo:
    """O turno como chega: sem espera, sem juntar mensagens e sem trocar o checkpoint."""

    estado: Mapping[str, Any]
    config: Any
    juntado: dict[str, Any] | None = None

    def concluir(self, resultado: Any) -> Any:
        return resultado


@asynccontextmanager
async def turno_sem_preparo(
    estado: Mapping[str, Any], config: Any, _compilado: Any
) -> AsyncIterator[TurnoSemPreparo]:
    """O contexto do turno quando o agente nao prepara nada (`turno_juntado` opcional do agente, ADR-0067):
    a costura roda o grafo com `turno.estado`/`turno.config` e devolve `turno.concluir(resultado)`; se o
    agente disser `turno.juntado`, a costura nao roda o grafo e devolve esse resultado."""
    yield TurnoSemPreparo(estado, config)


class RemetenteDoTurno(Protocol):
    """Envio de WhatsApp de UM turno. Opera sobre o HASH do telefone, nunca sobre o numero cru (ADR-0006):
    resolver o hash para o numero real e' do despachante, que tem o numero em maos durante o turno."""

    async def send(self, to_hash: str, text: str) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class RespostaDeCadastro:
    """A resposta fixa a uma pergunta sobre o proprio cadastro (DL-0083 item 8), sem grafo e sem modelo.

    `texto` vem ANTES do aviso de outra pessoa (D-01), que o despachante aplica ao enviar. `com_dados`:
    o perfil da AMH foi lido para responder (so' para o log, nunca o dado)."""

    tipo: str
    texto: str
    com_dados: bool


class AgenteDeEntrada(Protocol):
    """O agente que recebe a mensagem do numero unico. Hoje so' a Helena implementa."""

    #: O id do agente (rotulo de metrica e de log).
    agent_id: str
    #: `agent_version` passado ao grafo.
    agent_version: str

    def construir_grafo(self, config: Mapping[str, Any]) -> Any:
        """O grafo (LangGraph `StateGraph`, ainda nao compilado) com as dependencias e chaves de `config`.

        `config["fabrica_de_inferencia"]` (opcional): `fabrica(modelo=, prazo_s=, max_tokens=, concorrencia=)`
        devolve um provedor de inferencia DEDICADO, ja' pela costura do gateway — e' assim que o pacote de
        comportamento do agente (ADR-0067 fase 3) escolhe modelo e orcamento sem a plataforma mudar."""
        ...

    def estado_inicial(self, **campos: Any) -> dict[str, Any]:
        """O estado de entrada de um turno. Construtor TIPADO do agente (portao de entrada T1.11): campo
        de saida forjado levanta `TypeError` em vez de entrar no estado."""
        ...

    def estado_de_retomada(self, **campos: Any) -> dict[str, Any]:
        """O estado de entrada de uma RETOMADA (GAP-XHITL-4)."""
        ...

    def memoria_neutra(self) -> dict[str, Any]:
        """A memoria de conversa NEUTRA, gravada no checkpoint quando a pessoa verificada muda (DL-0083)."""
        ...

    async def escalar_identidade(
        self, dependencias: Mapping[str, Any], estado: dict[str, Any], *, motivo: str
    ) -> Mapping[str, Any]:
        """Abre o escalonamento `solicitacao_humano` do acesso pelo caminho do agente, sem modelo."""
        ...

    async def escalar_e_responder(
        self, dependencias: Mapping[str, Any], estado: dict[str, Any]
    ) -> dict[str, Any]:
        """Os nos de escalonamento e de resposta do agente (falha do Lucas, pedido de pessoa)."""
        ...

    def eh_pedido_explicito_de_pessoa(self, texto: str) -> bool:
        """`True` quando o texto pede, explicitamente, uma pessoa da equipe."""
        ...

    async def resposta_de_cadastro(
        self,
        texto: str,
        *,
        consultas_ligadas: bool,
        fonte_ligada: bool,
        sinal_de_emergencia: Callable[[str], bool],
        resolver_identidade: Callable[[], Awaitable[dict[str, Any] | None]] | None,
    ) -> RespostaDeCadastro | None:
        """A resposta fixa a uma pergunta EXPLICITA sobre o cadastro, ou `None` (o fluxo de sempre)."""
        ...
