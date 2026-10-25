"""O alerta JA' respondido na conversa (teste do dono de 25/10/2026, itens 1, 2, 8 e 12; DL-0154).

A pergunta de alerta e' feita UMA vez por queixa. Depois do "nao" (as opcoes 1/2 ou o caminho da opcao 1)
ou depois do 192 (alarme tratado), a conversa sobre a MESMA queixa nunca reinicia a pergunta de alerta: a
pergunta de agendamento ("ja' posso procurar um medico?") recebe o caminho (onde marcar e o prazo da ANS)
e o resto, a continuacao da orientacao. So' o historico curto (as falas da propria Helena e da pessoa)
decide; nenhum campo novo no estado.

Seguranca: estas funcoes so' sao consultadas DEPOIS da tabela de alarme do turno (`_v4_sintoma_leve` roda com
a tabela sem bandeira). Sinal de alarme na propria mensagem continua escalando como sempre.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from typing import Final

#: As falas FIXAS da Helena que dizem "a pergunta de alerta desta queixa ja' foi respondida com 'nao'": as
#: opcoes 1/2 (v4, base de saude e saude gerativa) e o caminho da opcao 1 (adulto, crianca, gestante).
_MARCAS_DO_ALERTA_RESPONDIDO: Final[tuple[str, ...]] = (
    "1) saber onde marcar consulta",
    "Eu não avalio sintomas, mas posso te mostrar o caminho.",
)
#: As opcoes 1/2 em si (o que nao pode ser oferecido de novo para a mesma queixa).
_MARCA_DAS_OPCOES: Final[str] = "1) saber onde marcar consulta"
_MARCA_DE_CRIANCA: Final[str] = "pediatra"

#: A pessoa reclama da repeticao ("eu ja' tinha te respondido que nao", "ja' falei", "de novo essa pergunta").
_RECLAMA_DA_REPETICAO: Final[re.Pattern[str]] = re.compile(
    r"\bja (?:te |lhe )?(?:respondi|disse|falei|expliquei)\b"
    r"|\bja (?:tinha|havia) (?:te |lhe )?(?:respondido|dito|falado|explicado)\b"
    r"|\b(?:de novo|outra vez|mesma pergunta|repetindo|repetiu|repete)\b"
)
#: Pergunta de AGENDAMENTO / de onde ir ("ja' posso procurar um medico?", "onde marco a consulta?", "posso
#: agendar?", "que medico eu procuro?"). Nao e' sintoma novo: e' o caminho.
_PERGUNTA_DE_AGENDAMENTO: Final[re.Pattern[str]] = re.compile(
    r"\b(?:procurar|procuro|buscar|ver) (?:um |uma |o |a )?(?:medic[oa]|doutor[a]?|especialista|clinico)\b"
    r"|\b(?:agendar|agendo|marcar|marco|marca|consigo marcar|consigo agendar) (?:uma |a |o )?"
    r"(?:consulta|medic[oa]|horario|atendimento)\b"
    r"|\b(?:agendar|marcar)\b[^.?!]{0,20}\bconsulta\b"
    r"|\b(?:que|qual) (?:medic[oa]|especialista|especialidade)\b"
    r"|\bonde (?:eu )?(?:marco|agendo|consulto|sou atendid[oa]|me consulto)\b"
    r"|\b(?:posso|devo|preciso) (?:ir|ir ao|ir no|ir pro|passar) (?:medic[oa]|consulta|doutor)\b"
)


def _sem_acento(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn").lower()


def alerta_ja_respondido(falas_da_helena: Iterable[str]) -> bool:
    """A Helena ja' mandou, nesta conversa, as opcoes 1/2 ou o caminho da opcao 1 (o "nao" ao alerta)?"""
    return any(marca in fala for fala in falas_da_helena for marca in _MARCAS_DO_ALERTA_RESPONDIDO)


def opcoes_ja_enviadas(falas_da_helena: Iterable[str]) -> bool:
    """As opcoes 1/2 do sintoma ja' sairam nesta conversa?"""
    return any(_MARCA_DAS_OPCOES in fala for fala in falas_da_helena)


def populacao_do_alerta_respondido(
    falas_da_helena: Iterable[str], *, crianca: str, adulto: str
) -> str | None:
    """A populacao (pela especialidade dita: pediatra = crianca) da ULTIMA orientacao de sintoma."""
    for fala in reversed(list(falas_da_helena)):
        if any(marca in fala for marca in _MARCAS_DO_ALERTA_RESPONDIDO):
            return crianca if _MARCA_DE_CRIANCA in fala else adulto
    return None


def reclama_da_repeticao(texto: str) -> bool:
    """ "Eu ja' tinha te respondido que nao sobre isso", "ja' falei", "de novo essa pergunta"."""
    return _RECLAMA_DA_REPETICAO.search(_sem_acento(texto or "")) is not None


def pergunta_de_agendamento(texto: str) -> bool:
    """ "Ja' posso procurar um medico para agendar uma consulta?", "onde eu marco?", "que medico procuro?"."""
    return _PERGUNTA_DE_AGENDAMENTO.search(_sem_acento(texto or "")) is not None


__all__ = [
    "alerta_ja_respondido",
    "opcoes_ja_enviadas",
    "pergunta_de_agendamento",
    "populacao_do_alerta_respondido",
    "reclama_da_repeticao",
]
