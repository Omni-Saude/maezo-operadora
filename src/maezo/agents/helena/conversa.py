"""A Helena como agente de ENTRADA do numero unico: implementa `maezo.contratos.conversa.AgenteDeEntrada`.

O despachante do WhatsApp (plataforma) chega aqui pelo framework de agentes
(`maezo.agents.carregar_agente_de_entrada("helena")`), nunca por import (ADR-0067). Tudo o que ele fazia
com o interior da Helena — `build`, `new_helena_state`, `HelenaGraph(...).escalate`, os textos fixos do
cadastro — passa por este adaptador, que so' delega: nenhuma regra nova mora aqui.

`construir_grafo` le' `graph.build` NA HORA da chamada (atributo do modulo), para que um teste possa trocar o
grafo da Helena por um dublê com `monkeypatch.setattr(graph, "build", ...)`.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Final, cast

from maezo.contratos.conversa import RespostaDeCadastro

from . import cadastro, graph
from . import juntar as _juntar
from .pacote import PacoteDaHelena, do_ambiente


class HelenaDeEntrada:
    """Sem estado: as dependencias de cada chamada chegam do despachante."""

    agent_id: str = "helena"
    agent_version: str = "helena@v0"

    def __init__(self) -> None:
        self._pacote_da_helena: PacoteDaHelena | None = None

    def _pacote(self) -> PacoteDaHelena:
        """O pacote de comportamento (ADR-0067 fase 3), resolvido na primeira chamada (le' a task)."""
        if self._pacote_da_helena is None:
            self._pacote_da_helena = do_ambiente()
        return self._pacote_da_helena

    def construir_grafo(self, config: Mapping[str, Any]) -> Any:
        juntar = self._pacote().juntar_mensagens()
        if juntar is None:
            return graph.build(self._pacote().aplicar_no_grafo(config))
        # Etapa 9 (esperar e juntar): o remetente barra a resposta superada por mensagem nova.
        com_trava = {**config, "whatsapp": _juntar.remetente_que_junta(config.get("whatsapp"))}
        return graph.build(self._pacote().aplicar_no_grafo(com_trava))

    def turno_juntado(self, estado: Mapping[str, Any], config: Any, compilado: Any) -> Any:
        """Etapa 9 (esperar e juntar): o contexto do turno para a costura que roda o grafo (espera, junta as
        mensagens picotadas e escolhe o checkpoint; `juntado` = nao rodar). Fora da lista: o de sempre."""
        juntar = self._pacote().juntar_mensagens()
        if juntar is None:
            return _juntar.sem_juntar(estado, config, compilado)
        espera_s, conversas = juntar
        return _juntar.turno_juntado(
            estado, config, compilado, espera_s=espera_s, conversas=conversas, urgente=graph.mensagem_urgente
        )

    def estado_inicial(self, **campos: Any) -> dict[str, Any]:
        return dict(graph.new_helena_state(**campos))

    def estado_de_retomada(self, **campos: Any) -> dict[str, Any]:
        return dict(graph.new_helena_resume_state(**campos))

    def memoria_neutra(self) -> dict[str, Any]:
        return graph.memoria_de_conversa_neutra()

    async def escalar_identidade(
        self, dependencias: Mapping[str, Any], estado: dict[str, Any], *, motivo: str
    ) -> Mapping[str, Any]:
        helena = graph.HelenaGraph(**self._pacote().aplicar_nos_nos(dependencias))
        if motivo == "revogacao_consentimento_pendente":
            return await helena.escalar_revogacao_pendente(cast(graph.HelenaState, estado))
        return await helena.escalar_identidade_nao_verificada(cast(graph.HelenaState, estado))

    async def escalar_e_responder(
        self, dependencias: Mapping[str, Any], estado: dict[str, Any]
    ) -> dict[str, Any]:
        helena = graph.HelenaGraph(**self._pacote().aplicar_nos_nos(dependencias))
        estado.update(await helena.escalate(cast(graph.HelenaState, estado)))
        estado.update(await helena.respond(cast(graph.HelenaState, estado)))
        return estado

    def eh_pedido_explicito_de_pessoa(self, texto: str) -> bool:
        return graph.eh_pedido_explicito_de_pessoa(texto)

    async def resposta_de_cadastro(
        self,
        texto: str,
        *,
        consultas_ligadas: bool,
        fonte_ligada: bool,
        sinal_de_emergencia: Callable[[str], bool],
        resolver_identidade: Callable[[], Awaitable[dict[str, Any] | None]] | None,
    ) -> RespostaDeCadastro | None:
        """Pergunta EXPLICITA sobre o cadastro, conversa VERIFICADA: texto fixo, sem grafo e sem modelo
        (DL-0083 item 8). A mesma ordem que o despachante tinha: tipo -> consulta do plano ligada ->
        emergencia -> perfil da AMH (so' quando o tipo precisa) -> texto -> fato ausente vai ao grafo com a
        consulta ligada.
        """
        tipo = cadastro.tipo_de_pergunta(texto)
        if tipo is None:
            return None
        if consultas_ligadas and tipo in (cadastro.TIPO_PLANO_DETALHE, cadastro.TIPO_CARENCIA):
            # RODADA 3: com os fatos do plano ligados (TINA), tipo de plano, cobertura e carencia sao
            # respondidos pela consulta da Helena, com segmentacao e carencias do cadastro.
            return None
        if sinal_de_emergencia(texto):
            return None
        identidade: dict[str, Any] | None = None
        if tipo not in _SEM_PERFIL and resolver_identidade is not None:
            identidade = await resolver_identidade()
        resposta = cadastro.resposta_de_cadastro(tipo, identidade, fonte_ligada=fonte_ligada)
        if consultas_ligadas and resposta == graph.RESPOSTA_PLANO_POR_CANAIS:
            # RODADA 8: com os fatos do plano (TINA) ligados e a pessoa verificada, "ainda nao estao
            # disponiveis por este chat" nunca sai: o fato ausente do perfil segue para o grafo, que consulta.
            # A comparacao vem ANTES do aviso de terceiro (revisao do #785), que o despachante aplica
            # ao enviar.
            return None
        return RespostaDeCadastro(tipo=tipo, texto=resposta, com_dados=identidade is not None)


#: Tipos respondidos sem o perfil da AMH (RODADA 2).
_SEM_PERFIL: Final[frozenset[str]] = frozenset(
    {
        cadastro.TIPO_QUEM_SOU_EU,
        cadastro.TIPO_NOME,
        cadastro.TIPO_IDENTIDADE_E_PLANO,
        cadastro.TIPO_PLANO_DETALHE,
    }
)

#: O que o framework de agentes devolve para `carregar_agente_de_entrada("helena")`.
AGENTE_DE_ENTRADA: Final[HelenaDeEntrada] = HelenaDeEntrada()
