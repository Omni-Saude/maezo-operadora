"""ESPERAR E JUNTAR as mensagens picotadas (Plano de ajustes da Helena v2, etapa 9; DL-0154).

Quem escreve em tres mensagens seguidas ("oi" / "minha filha ta com febre" / "e o boleto?") recebia tres
respostas cruzadas. Agora, nas conversas da lista do pacote (`juntar_mensagens.conversas`), a Helena ESPERA
alguns segundos (`juntar_mensagens.espera_s`, padrao 4 s) depois de cada mensagem:

- chegou outra mensagem na espera: este turno nao responde (`juntada`) e o turno da ULTIMA leva todas, na
  ordem, como uma mensagem so';
- chegou outra mensagem DEPOIS da espera, enquanto a resposta era calculada: a resposta velha NAO sai (o
  envio e' barrado) e o turno da mensagem nova REFAZ a resposta com tudo, a partir do estado de ANTES do
  turno barrado (o checkpoint de partida) — a pergunta que nao saiu nunca fica pendente;
- sinal claro de emergencia na mensagem (lexico de sempre e alarme deterministico): sem espera (192 na hora).

Os sinais lexicos de cada mensagem (saude, pedido de pessoa) somam no lote: nenhum se perde ao juntar.

QUEM RODA O GRAFO continua sendo a costura de sempre (o despachante, ou o servico do agente remoto), com a
contagem de falhas dela: `turno_juntado` so' PREPARA o turno (espera, junta, escolhe o checkpoint) e o
CONCLUI (confirma ou devolve `juntada`). Fica na memoria do processo do agente (uma tarefa no dev). Fora da
lista (padrao: lista vazia) nada muda.
"""

from __future__ import annotations

import asyncio
import contextvars
from collections import OrderedDict
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, Final

import structlog

logger = structlog.get_logger(__name__)

#: O resultado do turno que nao responde (a mensagem foi juntada a outra, ou a resposta foi refeita).
CHAVE_JUNTADA: Final[str] = "juntada"
ESPERA_PADRAO_S: Final[float] = 4.0
_MAXIMO_DE_CONVERSAS: Final[int] = 4096
_MAXIMO_DE_MENSAGENS: Final[int] = 12


class RespostaSuperadaError(RuntimeError):
    """A resposta deste turno ficou velha: chegou mensagem nova antes do envio. O envio nao acontece."""


@dataclass
class _Lote:
    textos: list[str] = field(default_factory=list)
    geracao: int = 0
    sinal_saude: bool = False
    pedido_humano: bool = False
    #: O checkpoint de ANTES do primeiro turno do lote (`None` = o atual vale).
    partida: str | None = None
    tem_partida: bool = False
    #: Um turno de cada vez por conversa: quem refaz espera o barrado terminar (o checkpoint dele nunca
    #: chega depois do que refaz).
    trava: asyncio.Lock = field(default_factory=asyncio.Lock)


class Juntador:
    """Os lotes por conversa (memoria do processo, limitada)."""

    def __init__(self) -> None:
        self._lotes: OrderedDict[str, _Lote] = OrderedDict()

    def _lote(self, conversa: str) -> _Lote:
        lote = self._lotes.get(conversa)
        if lote is None:
            lote = self._lotes[conversa] = _Lote()
            while len(self._lotes) > _MAXIMO_DE_CONVERSAS:
                self._lotes.popitem(last=False)
        self._lotes.move_to_end(conversa)
        return lote

    def chegou(self, conversa: str, texto: str, *, sinal_saude: bool, pedido_humano: bool) -> int:
        lote = self._lote(conversa)
        lote.textos = [*lote.textos, texto][-_MAXIMO_DE_MENSAGENS:]
        lote.geracao += 1
        lote.sinal_saude = lote.sinal_saude or sinal_saude
        lote.pedido_humano = lote.pedido_humano or pedido_humano
        return lote.geracao

    def geracao(self, conversa: str) -> int:
        lote = self._lotes.get(conversa)
        return lote.geracao if lote is not None else 0

    def lote(self, conversa: str) -> _Lote:
        return self._lote(conversa)

    def confirmar(self, conversa: str, geracao: int) -> None:
        """A resposta da geracao `geracao` saiu: o lote dela acabou (o que chegou depois fica)."""
        lote = self._lotes.get(conversa)
        if lote is not None and lote.geracao == geracao:
            self._lotes.pop(conversa, None)


JUNTADOR: Final[Juntador] = Juntador()
#: (conversa, geracao) do turno em curso, para o remetente barrar a resposta superada.
_TURNO: contextvars.ContextVar[tuple[str, int] | None] = contextvars.ContextVar(
    "helena_juntar_turno", default=None
)


class _RemetenteQueJunta:
    """O remetente do turno com a trava: resposta superada por mensagem nova nao sai."""

    def __init__(self, remetente: Any, juntador: Juntador) -> None:
        self._remetente = remetente
        self._juntador = juntador

    async def send(self, to_hash: str, text: str, **kw: Any) -> Any:
        turno = _TURNO.get()
        if turno is not None and self._juntador.geracao(turno[0]) != turno[1]:
            logger.info("helena_juntar_envio_barrado")
            raise RespostaSuperadaError("resposta superada por mensagem nova")
        return await self._remetente.send(to_hash, text, **kw)

    def __getattr__(self, nome: str) -> Any:
        return getattr(self._remetente, nome)


def remetente_que_junta(remetente: Any, juntador: Juntador = JUNTADOR) -> Any:
    return None if remetente is None else _RemetenteQueJunta(remetente, juntador)


@dataclass
class Turno:
    """O turno preparado: `juntado` (o resultado sem resposta, quando a mensagem foi juntada a outra) ou o
    `estado` e o `config` com que a costura roda o grafo; `concluir` recebe o resultado dela."""

    estado: Mapping[str, Any]
    config: Any
    juntado: dict[str, Any] | None = None
    _conversa: str = ""
    _geracao: int = 0
    _juntador: Juntador | None = None

    def concluir(self, resultado: Any) -> Any:
        j = self._juntador
        if j is None or self.juntado is not None:
            return resultado
        if j.geracao(self._conversa) != self._geracao:
            # Chegou mensagem nova durante o calculo: o envio foi barrado e o turno dela refaz com tudo.
            logger.info("helena_juntar_resposta_refeita")
            return {CHAVE_JUNTADA: True, "refeita": True, "conversation_id": self._conversa}
        j.confirmar(self._conversa, self._geracao)
        return resultado


@asynccontextmanager
async def sem_juntar(estado: Mapping[str, Any], config: Any, _compilado: Any) -> AsyncIterator[Turno]:
    """O turno de sempre (fora da lista): nada a preparar nem a concluir."""
    yield Turno(estado=estado, config=config)


@asynccontextmanager
async def turno_juntado(
    estado: Mapping[str, Any],
    config: Any,
    compilado: Any,
    *,
    espera_s: float,
    conversas: frozenset[str],
    urgente: Callable[[str], bool],
    juntador: Juntador = JUNTADOR,
) -> AsyncIterator[Turno]:
    conversa = str(estado.get("conversation_id") or "")
    texto = estado.get("message_body")
    if conversa not in conversas or not isinstance(texto, str) or not texto.strip():
        yield Turno(estado=estado, config=config)
        return
    geracao = juntador.chegou(
        conversa,
        texto,
        sinal_saude=estado.get("sinal_saude_lexico") is True,
        pedido_humano=estado.get("pedido_humano_lexico") is True,
    )
    juntada = {CHAVE_JUNTADA: True, "conversation_id": conversa}
    if not urgente(texto):
        await asyncio.sleep(espera_s)
        if juntador.geracao(conversa) != geracao:
            logger.info("helena_juntar_mensagem_juntada")
            yield Turno(estado=estado, config=config, juntado=juntada)
            return
    lote = juntador.lote(conversa)
    async with lote.trava:
        if juntador.geracao(conversa) != geracao:
            # Chegou outra mensagem enquanto este turno esperava a vez: a dela leva tudo.
            yield Turno(estado=estado, config=config, juntado=juntada)
            return
        do_lote = dict(estado)
        if len(lote.textos) > 1:
            do_lote["message_body"] = "\n".join(lote.textos)
            do_lote["sinal_saude_lexico"] = lote.sinal_saude
            do_lote["pedido_humano_lexico"] = lote.pedido_humano
            logger.info("helena_juntar_lote", mensagens=len(lote.textos))
        marca = _TURNO.set((conversa, geracao))
        try:
            yield Turno(
                estado=do_lote,
                config=await _config_de_partida(lote, config, compilado),
                _conversa=conversa,
                _geracao=geracao,
                _juntador=juntador,
            )
        finally:
            _TURNO.reset(marca)


async def _config_de_partida(lote: _Lote, config: Any, compilado: Any) -> Any:
    """Com checkpointer: o primeiro turno do lote guarda o checkpoint de ANTES dele; um turno que REFAZ parte
    desse checkpoint (o turno barrado nao deixa pergunta pendente nem fala no historico)."""
    if not isinstance(config, Mapping) or not isinstance(config.get("configurable"), Mapping):
        return config
    if not lote.tem_partida:
        atual = getattr(await compilado.aget_state(config), "config", None) or {}
        lote.partida = (atual.get("configurable") or {}).get("checkpoint_id")
        lote.tem_partida = True
        return config
    if lote.partida is None:
        return config
    return {**config, "configurable": {**config["configurable"], "checkpoint_id": lote.partida}}


__all__ = [
    "CHAVE_JUNTADA",
    "ESPERA_PADRAO_S",
    "JUNTADOR",
    "Juntador",
    "RespostaSuperadaError",
    "Turno",
    "remetente_que_junta",
    "sem_juntar",
    "turno_juntado",
]
