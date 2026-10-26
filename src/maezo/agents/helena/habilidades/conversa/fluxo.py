"""Habilidade `conversa` da Helena (ADR-0067 §5): as respostas fixas de conversa (duvida de saude sem sintoma,
recusa da equipe, "o que voce sabe de mim?"), a navegacao e a reclamacao sem pedido.

MOVIDO SEM MUDANCA de `graph.py` (Plano de ajustes da Helena v2, etapa 7, PR so' de mudanca de lugar): cada
metodo do `HelenaGraph` virou uma funcao deste modulo com o MESMO corpo (`self` e' o grafo) e a classe aponta
para ela. O comportamento muda so' no PR seguinte.

IMPORT CIRCULAR SEGURO: o `graph.py` importa este modulo LOGO ANTES da classe e este modulo importa os nomes
do graph NO FIM.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from maezo.agents.helena.habilidades.conversa import agendar as _agendar
from maezo.agents.helena.habilidades.conversa import app as _app
from maezo.agents.helena.habilidades.conversa import leitura_curta as _lc

if TYPE_CHECKING:
    from maezo.agents.helena.graph import HelenaGraph


def _resposta_fixa_de_conversa(self: HelenaGraph, estado: Mapping[str, Any]) -> str | None:
    """DL-0088: o texto fixo de um turno `inform` sem relato clinico, ou `None` (o caminho de antes).

    So' vale sem sintoma e sem risco (o `inform` com sintoma tem texto proprio). A recusa de falar
    com a equipe vem antes da duvida, porque a mesma mensagem pode trazer as duas ("voce nao pode
    me tirar a duvida? nao quero falar com nenhuma equipe") e a recusa e' o que a pessoa precisa
    ouvir reconhecido. A pergunta "o que voce sabe de mim?" so' tem texto fixo com o roteador
    ligado: e' ele que torna verdade o "e' so' me perguntar por aqui" sobre mensalidade.
    """
    if estado.get("sintoma_codigo") or estado.get("psychosocial_risk") is True:
        return None
    intent = estado.get("intent")
    texto = str(estado.get("message_body") or "")
    if intent in ("information", "outside_channel", "greeting") and _pergunta_de_dependentes(texto):
        return RESPOSTA_DEPENDENTES
    if intent in _INTENTS_QUE_VIRAM_COBRANCA:
        # RODADA 3: encerramento (inclusive ironico) e pedido do telefone da central. Com sinal de saude
        # ("ok obrigado, mas estou com sangramento") nunca e' despedida.
        if _e_encerramento(texto) and not _mensagem_de_saude(estado):
            # Teste do dono (25/10/2026, noite): a despedida nao leva o caso aberto de carona.
            return RESPOSTA_DESPEDIDA
        if _agendar.pergunta_de_agendamento(texto) and not _mensagem_de_saude(estado):
            # Teste do dono (25/10/2026, caso d): a Helena nao agenda; os canais (texto do pacote).
            return _agendar.texto_do_agendamento(self._textos, texto)
        if _app.pergunta_onde_baixar_o_app(texto):
            # Teste do dono (25/10/2026): onde baixar o aplicativo, sem link (o texto do pacote).
            return self._textos[_app.CHAVE_APP_ONDE_BAIXAR]
        if _pede_telefone(texto):
            return RESPOSTA_TELEFONE_DA_CENTRAL
        if _pergunta_de_navegacao(texto):
            # Navegacao vence a pergunta do plano ("vou na UBS? qual lugar cobre?").
            return self._texto_de_navegacao(estado)
    if intent in ("information", "greeting"):
        conversa = _conversa_sem_relato_clinico(texto)
        if conversa == CONVERSA_RECUSA_DE_EQUIPE:
            # Avaliacao r5 (25/10/2026): a recusa curta so' com oferta pendente (ou caso aberto).
            return _oferta.texto_da_recusa(estado)
        if conversa == CONVERSA_DUVIDA_DE_SAUDE:
            return RESPOSTA_DUVIDA_DE_SAUDE
    if intent in _INTENTS_QUE_VIRAM_COBRANCA:
        # RODADA 2: plano/cobertura com a fonte desligada e pergunta de canal, em texto fixo.
        if self._consultas is None and _pergunta_do_plano(texto):
            return _texto_plano_indisponivel(estado)
        if _pergunta_de_canal(texto) and not _pergunta_de_cobranca(texto):
            return RESPOSTA_CANAIS_DO_PLANO
    if (
        self._roteador_lucas_enabled
        and intent in _INTENTS_QUE_VIRAM_COBRANCA
        and _pergunta_sobre_o_que_sabe(texto)
    ):
        return RESPOSTA_O_QUE_SEI_DE_VOCE
    return None


def _texto_de_navegacao(estado: Mapping[str, Any]) -> str:
    """RODADA 3: as opcoes de cuidado (rede do plano, UBS, pronto-atendimento, 192). Teste do dono
    (25/10/2026, noite): sem "a nossa equipe de saude ja' esta' com o seu caso" de carona — a pessoa
    perguntou onde ir."""
    del estado
    return RESPOSTA_NAVEGACAO


def _reclamacao_sem_pedido(self: HelenaGraph, state: Mapping[str, Any], texto: str) -> bool:
    """DL-0131, so' na allowlist: a mensagem e' irritacao/reclamacao/"nao entendi" SEM pedido explicito
    de pessoa ("quero falar com um atendente", "me passa para a equipe")."""
    if not self._gerativa_para(state) or not _lc.e_reclamacao(texto):
        return False
    return not _QUERO_UMA_PESSOA.search(_plano(texto))


# Os nomes de modulo do graph.py que estes corpos usam (no FIM: import circular seguro, ver o docstring).
from maezo.agents.helena.graph import (  # noqa: E402
    _INTENTS_QUE_VIRAM_COBRANCA,
    _QUERO_UMA_PESSOA,
    CONVERSA_DUVIDA_DE_SAUDE,
    CONVERSA_RECUSA_DE_EQUIPE,
    RESPOSTA_CANAIS_DO_PLANO,
    RESPOSTA_DEPENDENTES,
    RESPOSTA_DESPEDIDA,
    RESPOSTA_DUVIDA_DE_SAUDE,
    RESPOSTA_NAVEGACAO,
    RESPOSTA_O_QUE_SEI_DE_VOCE,
    RESPOSTA_TELEFONE_DA_CENTRAL,
    _conversa_sem_relato_clinico,
    _e_encerramento,
    _mensagem_de_saude,
    _pede_telefone,
    _pergunta_de_canal,
    _pergunta_de_cobranca,
    _pergunta_de_dependentes,
    _pergunta_de_navegacao,
    _pergunta_do_plano,
    _pergunta_sobre_o_que_sabe,
    _plano,
    _texto_plano_indisponivel,
)
from maezo.agents.helena.habilidades.atendimento_humano import oferta as _oferta  # noqa: E402
