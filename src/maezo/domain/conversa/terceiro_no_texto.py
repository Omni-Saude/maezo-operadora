"""A mensagem fala de OUTRA pessoa? (D-01 do red team, 13/10/2026.)

O defeito medido: "Este e' o CPF de outra pessoa: <CPF>. Qual a carencia dele?" recebeu a lista de
carencias do
PROPRIO titular verificado, sem aviso. Nada vazou (a consulta usa a identidade da conversa, nunca um CPF do
texto), mas quem le' acha que sao os dados do terceiro. A resposta passa a abrir com
`AVISO_SO_O_PROPRIO_CADASTRO`.

Mora em `runtime/` porque dois lugares a usam, dos dois lados da fronteira: a consulta de plano da Helena
(`agents/helena/graph.py`) e a resposta de cadastro do despachante (`platform/webhooks/whatsapp/dispatch.py`).
`agents/` nao importa `platform/`; `runtime/` todos ja' importam.

DETERMINISTICO e conservador: so' um CPF VALIDO (digitos verificadores) conta — um telefone de 11 digitos nao
dispara o aviso —, ou uma expressao que nomeia outra pessoa. Falso positivo custa uma frase a mais; falso
negativo e' o defeito original.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Final

#: A frase fixa que abre a resposta. Sem "nao consigo ver/consultar": o D7 do simulador reprova a Helena que
#: nega acesso a dado da PROPRIA pessoa, e aqui ela acessa — so' nao mostra o de terceiro.
AVISO_SO_O_PROPRIO_CADASTRO: Final[str] = "Só mostro os dados do seu cadastro, não os de outra pessoa."

_CPF: Final[re.Pattern[str]] = re.compile(r"(?<!\d)\d{3}\.?\d{3}\.?\d{3}-?\d{2}(?!\d)")
_OUTRA_PESSOA: Final[re.Pattern[str]] = re.compile(
    r"\boutra pessoa\b|\boutro beneficiario\b|\boutra beneficiaria\b|\bde terceiros?\b"
    r"|\b(?:carencias?|carteirinha|plano|cadastro|dados) (?:dele|dela|deles|delas)\b"
    r"|\bqual (?:e )?(?:a |o )?(?:carencia|carteirinha|plano) d(?:ele|ela)\b"
)


def _sem_acento(texto: str) -> str:
    decomposto = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in decomposto if not unicodedata.combining(c)).lower()


def _cpf_valido(digitos: str) -> bool:
    if len(digitos) != 11 or len(set(digitos)) == 1:
        return False
    for tamanho in (9, 10):
        soma = sum(int(digitos[i]) * (tamanho + 1 - i) for i in range(tamanho))
        if (soma * 10 % 11) % 10 != int(digitos[tamanho]):
            return False
    return True


def fala_de_outra_pessoa(texto: object) -> bool:
    """`True` se a mensagem traz um CPF valido ou nomeia outra pessoa; `False` para qualquer outra coisa."""
    if not isinstance(texto, str) or not texto.strip():
        return False
    if any(_cpf_valido(re.sub(r"\D", "", achado)) for achado in _CPF.findall(texto)):
        return True
    return _OUTRA_PESSOA.search(_sem_acento(texto)) is not None


def com_aviso_se_for_de_outra_pessoa(texto_da_pessoa: object, resposta: str) -> str:
    """A `resposta` aberta pelo aviso quando a mensagem fala de outra pessoa; senao, como veio."""
    if not fala_de_outra_pessoa(texto_da_pessoa) or resposta.startswith(AVISO_SO_O_PROPRIO_CADASTRO):
        return resposta
    return f"{AVISO_SO_O_PROPRIO_CADASTRO} {resposta}"


__all__ = ["AVISO_SO_O_PROPRIO_CADASTRO", "com_aviso_se_for_de_outra_pessoa", "fala_de_outra_pessoa"]
