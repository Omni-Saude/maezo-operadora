"""A RECUSA da equipe e o CASO ABERTO que nao contamina a conversa (teste do dono de 25/10/2026, noite).

Logs do agente real (`/ecs/maezo-operadora-dev/agent-helena`, 25/10, 19:59-20:07 UTC), depois de um P1 falso:

* (a) a recusa virou pedido da equipe: o roteador leu a insistencia em encerrar como `equipe` sem
  `refusal_of_team` (`helena_roteador_decidiu habilidade=equipe` sem `helena_recusa_de_equipe_pelo_roteador`)
  e o turno foi ao `escalate` `solicitacao_humano` -> `escalonamento_ja_aberto` ("seu pedido ja' esta' com a
  equipe"). Agora a recusa e' lida TAMBEM no codigo (`e_recusa_da_equipe`), antes de a habilidade da equipe
  usar o pedido, e o `escalate` de pedido de pessoa nunca abre, mantem nem confirma caso numa recusa;
* (b) P1 aberto por engano (decisao B do dono, opcao 1): o caso FICA para a equipe confirmar (a Helena nunca
  encerra P1/P2 de saude), mas a conversa volta ao normal: o aviso "nao posso encerrar"/"mantive o seu caso"
  sai no MAXIMO uma vez por conversa (`texto_final`);
* (c) o caso aberto nao vai de carona nas outras respostas: a frase "seu caso continua com a equipe" sai das
  respostas informativas, a menos que a pessoa pergunte do caso.

O alarme NOVO depois de um 192 continua escalando (`_sinal_novo_de_alarme`, `_urgencia_que_piorou`): nada
aqui toca a rota `escalate` de alarme.

IMPORT CIRCULAR SEGURO: o `graph.py` importa este modulo LOGO ANTES da classe, e este modulo importa os
nomes do graph NO FIM, como as demais habilidades.
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Final, cast

from maezo.agents.helena.habilidades.atendimento_humano.textos import (
    TEXTO_RECUSA_JA_RESPONDIDA,
    TEXTO_SEM_SINAL_NOVO,
)
from maezo.agents.helena.habilidades.conversa import leitura_curta as _lc

if TYPE_CHECKING:
    from maezo.agents.helena.graph import HelenaGraph, HelenaState


def e_recusa_da_equipe(texto: str) -> bool:
    """A mensagem RECUSA a equipe, o contato ou o atendimento ("nao quero que ninguem entre em contato",
    "encerre", "nao autorizo", "nao quero falar com ninguem", "para de me mandar mensagem")?

    Pedido explicito de pessoa FORA do trecho da recusa vence ("nao quero falar com robo, quero um
    atendente"; "nao quero que me liguem, me passa para uma pessoa aqui"): nao e' recusa."""
    plano = _lc.normalizar(texto)
    if not plano:
        return False
    resto = " ".join(_RECUSA_DE_EQUIPE.sub(" ", _lc.sem_recusa_de_contato(texto)).split())
    if resto == plano:
        return False
    return not (_pedido_explicito_com_verbo(resto) or _pedido_explicito_de_pessoa(resto))


def com_recusa_lida(decisao: Any, texto: str) -> Any:
    """A decisao do roteador com `refusal_of_team` ligado quando o CODIGO le' a recusa que o modelo nao leu.
    O resto da leitura do modelo (habilidade, sim/nao, alarme) fica como veio."""
    v7 = getattr(decisao, "v7", None)
    if v7 is None or v7.recusa_equipe or not e_recusa_da_equipe(texto):
        return decisao
    logger.info("helena_recusa_lida_no_codigo", node="classify", habilidade_do_modelo=v7.skill)
    return dataclasses.replace(decisao, v7=dataclasses.replace(v7, recusa_equipe=True))


async def recusa_sem_caso(self: HelenaGraph, state: Mapping[str, Any]) -> dict[str, Any] | None:
    """TRAVA no `escalate` de pedido de pessoa: a recusa nunca abre, mantem nem confirma caso. Recebe a mesma
    resposta da recusa lida pelo roteador (P2/P3 se encerram; P1/grave fica, com o aviso uma vez so')."""
    if not e_recusa_da_equipe(str(state.get("message_body") or "")):
        return None
    from maezo.agents.helena import roteador as _rt

    logger.warning("helena_recusa_nao_abre_caso", node="escalate")
    saida = await _rt._recusa_da_equipe(self, cast("HelenaState", state))
    return {
        **saida,
        "response_kind": "inform",
        "next_kind": "inform",
        "escalation_motivo": None,
        "escalation_severidade": None,
    }


# --- (b) o aviso do caso UMA vez -------------------------------------------------------------------------

#: As marcas (texto normalizado) do aviso de que o caso fica com a equipe: o texto do caso grave, o "mantive
#: o seu caso" (do pacote), o "continua aberto com a equipe" e a recusa com caso aberto.
_MARCAS_DO_AVISO: Final[tuple[str, ...]] = (
    "mantive o seu caso",
    "nao posso encerra",
    "atendimento continua aberto com a equipe",
    "atendimento aberto com a nossa equipe ele continua",
)


def _e_aviso_do_caso(texto: str) -> bool:
    plano = _lc.normalizar(texto)
    return any(marca in plano for marca in _MARCAS_DO_AVISO)


def aviso_do_caso_ja_dado(state: Mapping[str, Any]) -> bool:
    """A Helena ja' disse NESTA conversa que o caso fica com a equipe (historico curto)?"""
    return any(_e_aviso_do_caso(fala) for fala in _falas_da_helena(state))


# --- (c) o caso aberto so' quando a pessoa pergunta dele -------------------------------------------------

#: A frase "o seu caso (ja') esta'/continua com a equipe" e "a equipe ja' esta' com o seu caso".
_FRASE_DO_CASO: Final[re.Pattern[str]] = re.compile(
    r"\b(?:seu|sua) (?:caso|pedido|atendimento|solicitacao|chamado)(?: de saude)? (?:ja )?"
    r"(?:esta|continua|segue|fica|ficou)(?: aberto| aberta)? com a (?:nossa )?equipe"
    r"|\bequipe(?: de saude)? (?:ja )?(?:esta|continua|segue) com (?:o )?seu (?:caso|pedido|atendimento)"
)
#: A pessoa fala do caso, da equipe ou do retorno: ai' o caso aberto e' a resposta.
_FALA_DO_CASO: Final[re.Pattern[str]] = re.compile(
    r"\b(?:novidades?|meu caso|meu pedido|meu atendimento|minha solicitacao|o caso|esse caso|chamado"
    r"|protocolo|equipe|atendente|alguem|retorno|retornar|retornam|andamento|status|encerr\w*|cancel\w*"
    r"|quanto tempo|demora\w*|previsao|aguard\w*|esperando|resposta)\b"
)


def fala_do_caso(texto: str) -> bool:
    """A mensagem pergunta do caso aberto, da equipe ou do retorno?"""
    return bool(_FALA_DO_CASO.search(_lc.normalizar(texto)) or _pergunta_sobre_caso_aberto(texto))


def caso_e_a_resposta(texto: str) -> bool:
    """Com caso aberto, o aviso do caso responde a mensagem? Sim quando ela fala do caso; nunca numa pergunta
    do plano, de consulta/onde ir ou de saude (essas recebem a resposta do assunto delas)."""
    if fala_do_caso(texto):
        return True
    plano = _lc.normalizar(texto)
    return not (_pergunta_do_plano(texto) or _pergunta_de_navegacao(texto) or _ASSUNTO_DE_SAUDE.search(plano))


def sem_frase_do_caso(texto: str) -> str:
    """O texto sem as frases que dizem que o caso esta' com a equipe (sobrando nada, o texto como veio)."""
    frases = re.split(r"(?<=[.!?])\s+", texto.strip())
    ficam = [f for f in frases if not _FRASE_DO_CASO.search(_lc.normalizar(f))]
    if len(ficam) == len(frases) or not ficam:
        return texto
    return " ".join(ficam)


def texto_final(state: Mapping[str, Any], texto: str) -> str:
    """A ultima palavra do `respond` sobre o caso aberto:

    - o aviso de que o caso fica com a equipe, ja' dado nesta conversa, nao sai de novo: a recusa repetida e'
      so' reconhecida; a fala sobre o mesmo alarme (sem sinal novo) guarda so' a linha do 192;
    - resposta informativa nao leva "seu caso continua com a equipe", a menos que a pessoa pergunte do caso.
    """
    if _e_aviso_do_caso(texto) and aviso_do_caso_ja_dado(state):
        mensagem = str(state.get("message_body") or "")
        recusa = e_recusa_da_equipe(mensagem) or _lc.ler_resposta(mensagem) in ("nao", "encerrar")
        logger.info("helena_aviso_do_caso_uma_vez", node="respond", recusa=recusa)
        return TEXTO_RECUSA_JA_RESPONDIDA if recusa else TEXTO_SEM_SINAL_NOVO
    if (
        state.get("response_kind") != "inform"
        or texto == RESPOSTA_INFORM_CASO_ABERTO  # o aviso escolhido no codigo (`caso_e_a_resposta`)
        or fala_do_caso(str(state.get("message_body") or ""))
    ):
        return texto
    limpo = sem_frase_do_caso(texto)
    if limpo != texto:
        logger.info("helena_caso_aberto_fora_da_resposta", node="respond")
    return limpo


# Os nomes de modulo do graph.py que estes corpos usam (no FIM: import circular seguro, ver o docstring).
from maezo.agents.helena.graph import (  # noqa: E402
    _ASSUNTO_DE_SAUDE,
    _RECUSA_DE_EQUIPE,
    RESPOSTA_INFORM_CASO_ABERTO,
    _falas_da_helena,
    _pedido_explicito_com_verbo,
    _pedido_explicito_de_pessoa,
    _pergunta_de_navegacao,
    _pergunta_do_plano,
    _pergunta_sobre_caso_aberto,
    logger,
)
