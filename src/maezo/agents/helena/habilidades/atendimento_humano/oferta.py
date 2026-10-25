"""A OFERTA da equipe e a resposta a ela (avaliacao independente r5 de 25/10/2026; regra do dono: caso so'
com pedido ou "sim").

Tres defeitos medidos na avaliacao r5 (logs do simulador de 25/10, turnos sem excecao nem timeout):

* LOOP QUE ABRIA CASO (item 10): a mesma resposta fixa pela terceira vez seguida disparava o gatilho de loop
  (DL-0147) e abria caso `falha_tecnica` sem a pessoa pedir — depois de uma recusa ("esquece") e numa
  pergunta de remedio. Agora o loop OFERECE a equipe (`TEXTO_OFERTA_POR_REPETICAO`, pendencia
  `PENDENTE_OFERTA_EQUIPE`) e so' o "sim" abre o caso. O aviso fixo da cobertura (DL-0154: sem oferta da
  equipe) nunca vira loop.
* "SIM" IGNORADO (item 11): com o roteador lendo `atendimento_humano`, o classificador dizia
  `outside_channel`, a habilidade `equipe` fechava a intencao em `information` e nada abria. Agora o "sim"
  ("quero", "pode passar", "pode sim") a uma oferta da equipe na conversa abre o caso `solicitacao_humano`.
* RECUSA FORA DE CONTEXTO (item 13b): o paragrafo longo da recusa saia sem oferta nem caso. Agora a recusa
  curta so' sai com oferta pendente; com caso aberto, a frase curta do caso; sem nada, so' o reconhecimento.

IMPORT CIRCULAR SEGURO: o `graph.py` importa este modulo LOGO ANTES do roteador (que usa estas funcoes) e
este modulo importa os nomes do graph NO FIM, como as demais habilidades.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Final

from maezo.agents.helena import roteador_v7 as _rv7
from maezo.agents.helena.habilidades.atendimento_humano.textos import (
    PENDENTE_OFERTA_EQUIPE,
    RESPOSTA_RECUSA_COM_CASO,
    RESPOSTA_RECUSA_DE_EQUIPE,
    TEXTO_CONFIRMA_PASSAR,
    TEXTO_OFERTA_POR_REPETICAO,
)
from maezo.agents.helena.habilidades.conversa import leitura_curta as _lc

if TYPE_CHECKING:
    from maezo.agents.helena.graph import HelenaGraph

#: A ultima fala da Helena ofereceu a equipe (texto normalizado, sem acento): a pergunta da oferta, a
#: confirmacao e o convite que fecha varias respostas ("se quiser falar com uma pessoa da equipe, e' so' me
#: pedir").
_OFERTA_NA_FALA: Final[re.Pattern[str]] = re.compile(
    r"\b(?:posso passar o seu (?:atendimento|caso) para|quer que eu passe o seu atendimento"
    r"|se (?:voce )?quiser falar com uma pessoa da (?:nossa )?equipe)\b"
)
#: O pedido de PASSAR dito na resposta curta ("pode passar", "quero sim, pode encaminhar").
_VERBO_DE_PASSAR: Final[re.Pattern[str]] = re.compile(
    r"\b(?:passa|passar|passe|encaminha|encaminhar|encaminhe)\b"
)


def oferta_pendente(state: Mapping[str, Any]) -> bool:
    """Ha' uma oferta da equipe esperando resposta: a pendencia da oferta, ou (sem outra pendencia de outra
    habilidade) a ULTIMA fala da Helena oferecendo a equipe: a pergunta da oferta (a memoria de coleta nem
    sempre atravessa o turno) ou o convite sem outra pergunta ("?": o "sim" podia ser a ela)."""
    pendente = str(state.get("coleta_pendente") or "")
    if pendente:
        return pendente.startswith(PENDENTE_OFERTA_EQUIPE)
    falas = _falas_da_helena(state)
    if not falas:
        return False
    ultima = _sem_prefixo(falas[-1].strip())
    if ultima.startswith((TEXTO_OFERTA_POR_REPETICAO, TEXTO_CONFIRMA_PASSAR)):
        return True
    return "?" not in ultima and bool(_OFERTA_NA_FALA.search(_lc.normalizar(ultima)))


def resposta_a_oferta(texto: str) -> str | None:
    """ "sim", "nao" ou `None` (nao e' resposta a oferta: pedido novo, cortesia, outro assunto).

    Cortesia ("ok", "beleza", "valeu") NAO e' sim: o "sim" tem de ter palavra de aceite (`_SIM_A_OFERTA`).
    Com o roteador ativo, a recusa e o "sim" lidos no contexto tambem valem."""
    plano = _plano(texto)
    if not plano:
        return None
    v7 = _rv7.LEITURA_DO_TURNO.get()
    curta = _lc.ler_resposta(texto)
    if (
        curta in ("nao", "encerrar")
        or (v7 is not None and v7.recusa_equipe)
        or _conversa_sem_relato_clinico(texto) == CONVERSA_RECUSA_DE_EQUIPE
    ):
        return "nao"
    aceite = bool(_SIM_A_OFERTA.search(plano))
    if aceite and (curta == "sim" or (v7 is not None and v7.sim_nao == "sim")):
        return "sim"
    return None


def sim_a_oferta_pendente(state: Mapping[str, Any]) -> bool:
    """Para a trava `caso_so_com_sim`: a mensagem e' o "sim" a uma oferta da equipe da conversa."""
    return oferta_pendente(state) and resposta_a_oferta(str(state.get("message_body") or "")) == "sim"


def texto_da_recusa(state: Mapping[str, Any], *, caso_aberto: bool = False) -> str:
    """A resposta a "nao quero a equipe": com caso aberto, a frase curta do caso (nada se encerra aqui); com
    oferta pendente, a recusa curta; sem oferta e sem caso, so' o reconhecimento curto."""
    if (
        caso_aberto
        or _caso_aberto_afirmavel(state) is not None
        or _caso_aberto_no_historico(state) is not None
    ):
        return RESPOSTA_RECUSA_COM_CASO
    if oferta_pendente(state):
        return RESPOSTA_RECUSA_DE_EQUIPE
    return _lc.resposta_de_cortesia(str(state.get("message_body") or ""))


def oferta_por_repeticao(state: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    """O gatilho de loop (DL-0147) sem caso: o texto da oferta e a pendencia dela. Nenhum start."""
    logger.warning(
        "helena_repeticao_oferece_equipe", node="respond", response_kind=state.get("response_kind")
    )
    texto = TEXTO_OFERTA_POR_REPETICAO
    return texto, {"response_text": texto, "coleta_pendente": PENDENTE_OFERTA_EQUIPE, "coleta_contexto": ""}


def _abre_caso(self: HelenaGraph, state: Mapping[str, Any], como: str) -> dict[str, Any]:
    logger.info("helena_oferta_de_equipe_aceita", node="classify", como=como)
    update = self._update_sem_modelo(state)
    update.update(
        {
            "intent": "human_request",
            "next_kind": "escalate",
            "escalation_motivo": "solicitacao_humano",
            "escalation_severidade": "leve",
            "coleta_pendente": None,
            "coleta_contexto": "",
        }
    )
    return update


def responder(self: HelenaGraph, state: Mapping[str, Any]) -> dict[str, Any] | None:
    """A resposta a uma oferta da equipe, decidida no codigo (`None` = o caminho de sempre).

    - oferta pendente + "sim" -> caso `solicitacao_humano` (leve), como o pedido explicito;
    - oferta pendente + "nao" -> a recusa curta, sem caso, e a pendencia sai;
    - sem oferta, o roteador leu `atendimento_humano` num "sim" curto: com verbo de passar ("pode passar")
      abre o caso; sem ele ("quero", "sim") a Helena confirma antes (a oferta fica pendente).
    Com caso ja' aberto na conversa nada muda aqui (o caso aberto e o encerramento seguem como sempre)."""
    texto = str(state.get("message_body") or "")
    if _caso_aberto_no_historico(state) is not None:
        return None
    resposta = resposta_a_oferta(texto)
    if oferta_pendente(state):
        if resposta == "sim":
            return _abre_caso(self, state, "oferta")
        if resposta == "nao":
            logger.info("helena_oferta_de_equipe_recusada", node="classify")
            update = self._update_sem_modelo(state)
            update.update({"coleta_pendente": None, "coleta_contexto": ""})
            return self._base_texto_fixo(update, RESPOSTA_RECUSA_DE_EQUIPE)
        return None
    v7 = _rv7.LEITURA_DO_TURNO.get()
    if (
        v7 is None
        or v7.skill != "atendimento_humano"
        or v7.recusa_equipe
        or state.get("coleta_pendente")
        or _lc.ler_resposta(texto) != "sim"
        or not _SIM_A_OFERTA.search(_plano(texto))
    ):
        return None
    if _VERBO_DE_PASSAR.search(_plano(texto)):
        return _abre_caso(self, state, "pedido_de_passar")
    logger.info("helena_oferta_de_equipe_confirmar", node="classify")
    return self._base_texto_fixo(
        self._update_sem_modelo(state), TEXTO_CONFIRMA_PASSAR, pendente=PENDENTE_OFERTA_EQUIPE
    )


# Os nomes de modulo do graph.py que estes corpos usam (no FIM: import circular seguro, ver o docstring).
from maezo.agents.helena.graph import (  # noqa: E402
    _SIM_A_OFERTA,
    CONVERSA_RECUSA_DE_EQUIPE,
    _caso_aberto_afirmavel,
    _caso_aberto_no_historico,
    _conversa_sem_relato_clinico,
    _falas_da_helena,
    _plano,
    _sem_prefixo,
    logger,
)
