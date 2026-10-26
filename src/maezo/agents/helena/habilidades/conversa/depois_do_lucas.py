"""FALAS CURTAS LOGO DEPOIS DO LUCAS (avaliacao independente r5b de 25/10/2026).

Dois defeitos medidos no simulador (25/10, Helena e Lucas no mesmo processo):

* FECHAMENTO REPETIA A RESPOSTA (item D): depois da resposta do Lucas, "ok, entendi" foi lido pelo roteador
  como `lucas_cobranca`; a Helena passou de novo ao Lucas (`consulta_valores`, sem mes) e ele repetiu a
  resposta da mensalidade INTEIRA, com nova consulta a fonte (26 s). Agora, com o Lucas com a conversa, a fala
  que e' so' cortesia ou acordo ("ok", "ok, entendi", "beleza", "valeu", "certo", "entendi", o polegar)
  recebe a resposta curta de sempre (`leitura_curta.resposta_de_cortesia`), sem modelo, sem consulta e sem
  passar ao Lucas.
* PROTOCOLO PERDIDO (item E2): logo depois do pedido de cancelamento (o Lucas respondeu com o protocolo e
  terminou o turno escalando, entao a conversa voltou para a Helena), "qual meu protocolo mesmo?" foi para a
  habilidade `pedidos` do roteador, o classificador disse `information` e a pessoa leu o texto generico de
  "o que eu sei de voce". Agora a pergunta do PROTOCOLO do pedido vai ao Lucas como `cancelamento` com o foco
  `protocolo`: ele le' a instancia viva do pedido no motor e responde o protocolo dela (ou diz, honesto, que
  nao encontrou ou nao conseguiu consultar, e os canais). Nunca um protocolo inventado.

Decidido pelo TEXTO, antes do modelo, so' com o roteador do Lucas ligado e sem sinal de saude nem pedido de
pessoa (os lexicos do despachante). `aplica` e' o predicado PURO (o roteador ativo o usa para nao chamar o
modelo); `decidir` devolve o `update` do turno, ou `None` para o caminho de sempre.

IMPORT CIRCULAR SEGURO: o `graph.py` importa este modulo depois dos helpers de modulo, e este modulo importa
os nomes do graph NO FIM, como as demais habilidades.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Final

from maezo.agents.helena.habilidades.conversa import leitura_curta as _lc
from maezo.domain.conversa.cobranca_esclarecimento import e_seguimento_de_cobranca
from maezo.domain.conversa.cobranca_foco import FOCO_PROTOCOLO

if TYPE_CHECKING:
    from maezo.agents.helena.graph import HelenaGraph

#: A palavra que pede o protocolo (com os erros de digitacao mais comuns).
_PROTOCOLO: Final[re.Pattern[str]] = re.compile(r"\b(?:protocolo|protocol|protocolos|prot)\b")
#: O protocolo como PERGUNTA ou PEDIDO sobre um pedido ja' feito ("qual meu protocolo?", "me passa o
#: protocolo", "esqueci o protocolo", "o numero do protocolo", "e o protocolo?").
_PEDE_O_PROTOCOLO: Final[re.Pattern[str]] = re.compile(
    r"\b(?:qual|quais|cade|kd|onde|numero|me (?:passa|manda|envia|diz|fala|informa|lembra|repete)"
    r"|pode (?:me )?(?:passar|mandar|enviar|repetir|informar)|esqueci|perdi|nao (?:anotei|guardei|vi|recebi)"
    r"|anotar|repete|repetir|meu|minha|do (?:meu )?(?:pedido|cancelamento)|da (?:minha )?solicitacao)\b"
)
#: Protocolo de outra coisa (clinico/tratamento) nunca e' o protocolo do pedido.
_PROTOCOLO_CLINICO: Final[re.Pattern[str]] = re.compile(
    r"\bprotocolos? (?:de|do|da|para|pra) (?:tratamento|quimio\w*|manchester|atendimento medico|saude"
    r"|vacina\w*|exames?|cirurgia|medica\w*|remedios?|dor|febre|dengue|covid|avc|infarto|sepse)\b"
)
_MAX_PALAVRAS_DO_PROTOCOLO: Final[int] = 14


def e_pergunta_do_protocolo(texto: object) -> bool:
    """A mensagem pergunta o PROTOCOLO de um pedido ja' feito ("qual meu protocolo mesmo?")? Curta, com a
    palavra protocolo e um pedido ou pergunta; nunca o protocolo de um tratamento."""
    if not isinstance(texto, str):
        return False
    plano = _lc.normalizar(texto)
    return (
        bool(plano)
        and len(plano.split()) <= _MAX_PALAVRAS_DO_PROTOCOLO
        and bool(_PROTOCOLO.search(plano))
        and bool(_PEDE_O_PROTOCOLO.search(plano) or "?" in texto)
        and not _PROTOCOLO_CLINICO.search(plano)
    )


def e_fechamento(texto: object) -> bool:
    """So' cortesia ou acordo ("ok", "ok, entendi", "beleza", "valeu", "certo", o polegar), sem pergunta e
    sem um "sim" (o "sim" pode ser a resposta a uma oferta: segue o caminho de sempre)."""
    if not isinstance(texto, str) or "?" in texto or not _lc.e_cortesia(texto):
        return False
    return "sim" not in _lc.normalizar(texto).split()


def _portas_abertas(self: HelenaGraph, state: Mapping[str, Any]) -> bool:
    """Roteador do Lucas ligado e, pelos lexicos do despachante, nada de saude nem pedido de pessoa."""
    return (
        bool(self._roteador_lucas_enabled)
        and state.get("sinal_saude_lexico") is False
        and state.get("pedido_humano_lexico") is False
    )


def _fecha_depois_do_lucas(self: HelenaGraph, state: Mapping[str, Any]) -> bool:
    texto = str(state.get("message_body") or "")
    return (
        state.get("agente_ativo_conversa") == "lucas"
        and not state.get("coleta_pendente")
        and e_fechamento(texto)
    )


def _protocolo_ao_lucas(state: Mapping[str, Any]) -> bool:
    texto = str(state.get("message_body") or "")
    return (
        e_pergunta_do_protocolo(texto)
        and not _conteudo_clinico(texto)
        and not _pedido_explicito_de_pessoa(texto)
        and not _mensagem_de_saude({"message_body": texto})
    )


def aplica(self: HelenaGraph, state: Mapping[str, Any]) -> bool:
    """Predicado PURO (sem log): o turno e' um fechamento depois do Lucas ou a pergunta do protocolo."""
    if not _portas_abertas(self, state):
        return False
    return _fecha_depois_do_lucas(self, state) or _protocolo_ao_lucas(state)


def decidir(self: HelenaGraph, state: Mapping[str, Any]) -> dict[str, Any] | None:
    """O `update` do turno (resposta curta, ou passagem ao Lucas com o foco `protocolo`), ou `None`."""
    if not _portas_abertas(self, state):
        return None
    texto = str(state.get("message_body") or "")
    if _fecha_depois_do_lucas(self, state):
        logger.info("helena_fechamento_depois_do_lucas", node="classify")
        return self._base_texto_fixo(self._update_sem_modelo(state), _lc.resposta_de_cortesia(texto))
    if not _protocolo_ao_lucas(state):
        return None
    update = self._handoff_deterministico_ao_lucas(state, texto, "helena_protocolo_ao_lucas")
    if update.get("next_kind") == "handoff":
        # O assunto e' o pedido (cancelamento), sem mes; o foco diz ao Lucas que e' o protocolo dele.
        update["cobranca_subtipo"] = "cancelamento"
        update["cobranca_competencia"] = None
        update["cobranca_foco"] = FOCO_PROTOCOLO
    return update


# Os nomes de modulo do graph.py que estes corpos usam (no FIM: import circular seguro, ver o docstring).
def seguimento_com_o_lucas(self: HelenaGraph, state: Mapping[str, Any]) -> dict[str, Any] | None:
    """SEGUIMENTO CURTO COM O LUCAS ATIVO VAI AO LUCAS, NAO IMPORTA O MODELO (rodada 11, 14/10/2026).

    No teste real do dono, "de qual ano?" logo depois do Lucas (coparticipacao de setembro) foi ao Lucas
    em duas execucoes e, na terceira, o modelo devolveu `consulta_plano`/`elegibilidade` e a Helena
    respondeu a vigencia do plano. A regra "seguimento enquanto o Lucas esta' ativo e' cobranca" dependia
    da intencao do modelo. Agora e' decidida ANTES dele, pelo texto: Lucas ativo (a janela de inatividade
    ja' foi aplicada pelo despachante), mensagem curta de esclarecimento/seguimento, SEM pista de consulta
    do plano, sem pessoa/saude, sem pedido de pessoa e sem sinal de saude do lexico. Devolve o `update`
    do turno ja' roteado (handoff), ou `None` para o caminho de sempre."""
    if (depois := decidir(self, state)) is not None:  # r5b: "ok" e "qual meu protocolo?"
        return depois
    if (
        not self._roteador_lucas_enabled
        or state.get("agente_ativo_conversa") != "lucas"
        or state.get("sinal_saude_lexico") is not False
        or state.get("pedido_humano_lexico") is not False
    ):
        return None
    texto = str(state.get("message_body") or "")
    if not e_seguimento_de_cobranca(texto) or _pedido_explicito_de_pessoa(texto) or _conteudo_clinico(texto):
        return None
    return self._handoff_deterministico_ao_lucas(state, texto, "helena_seguimento_com_o_lucas")


from maezo.agents.helena.graph import (  # noqa: E402
    _conteudo_clinico,
    _mensagem_de_saude,
    _pedido_explicito_de_pessoa,
    logger,
)
