"""A leitura DETERMINISTICA (sem modelo) do "nao" a tudo: "nao", "nehuma", "ja' respondi nao para todas".

Limpeza da saude, etapa 1 (dono, 02/11/2026): MOVIDO SEM MUDANCA de
`habilidades/protocolo/leitura_sem_modelo.py` (teste do dono de 26/10/2026). A oferta da equipe
(`atendimento_humano.oferta`) le' o "nao" por aqui; o protocolo usa os mesmos simbolos para `ler_sem_modelo`.
Vocabulario FECHADO: palavra fora dele = `None`.
"""

from __future__ import annotations

import re
from typing import Final

from maezo.agents.helena.habilidades.conversa.leitura_curta import normalizar

#: Negativas (a resposta "nao" a TODAS).
_NEGATIVAS: Final[frozenset[str]] = frozenset(
    {"nao", "naum", "n", "nn", "nada", "nenhuma", "nenhum", "nem", "negativo", "nunca", "nenhuminha"}
)
#: Erro de digitacao em "nenhuma"/"nenhum" ("nehuma", "nenhma", "nenuma"): distancia de edicao curta.
_NENHUMA: Final[tuple[tuple[str, int], ...]] = (("nenhuma", 2), ("nenhum", 1))
_SIM: Final[frozenset[str]] = frozenset({"sim", "s", "ss", "simm", "positivo"})
_SO: Final[frozenset[str]] = frozenset({"so", "somente", "apenas"})
_TODAS: Final[frozenset[str]] = frozenset({"todas", "todos"})
_OUTRAS: Final[frozenset[str]] = frozenset({"outras", "outra", "demais", "resto"})
_ORDINAIS: Final[dict[str, int]] = {
    "primeira": 1,
    "primeiro": 1,
    "segunda": 2,
    "segundo": 2,
    "terceira": 3,
    "terceiro": 3,
    "quarta": 4,
    "quarto": 4,
    "quinta": 5,
    "quinto": 5,
    "sexta": 6,
    "sexto": 6,
    "setima": 7,
    "oitava": 8,
}
_NUMERO: Final[re.Pattern[str]] = re.compile(r"^([1-9])[ao]?$")
#: Palavras de LIGACAO que nao mudam a resposta. Vocabulario FECHADO: sem verbo de estado ("estou", "bem"),
#: sem "isso" (pode ser "sim"), sem sintoma — o que nao esta' aqui vai ao modelo.
_LIGACAO: Final[frozenset[str]] = frozenset(
    {
        "a", "o", "as", "os", "e", "na", "no", "nas", "nos", "pra", "para", "pro", "de", "da", "do", "das",
        "dos", "em", "eu", "ja", "te", "lhe", "voce", "vc", "como", "falei", "disse", "respondi", "respondo",
        "responder", "suas", "sua", "tuas", "perguntas", "pergunta", "questoes", "acima", "anteriores",
        "desses", "dessas", "deles", "delas", "disso", "dessa", "desse", "essas", "esses", "sinto", "senti",
        "tenho", "tive", "tem", "teve", "acontece", "aconteceu", "mesmo", "mesma", "obrigado", "obrigada",
        "obg", "ok", "pqp", "poxa", "aff", "affe", "puts", "gente", "ne", "ue", "oxe", "cara", "moca",
        "ainda", "agora", "ai", "aqui", "por", "enquanto", "gracas", "deus", "que",
    }
)  # fmt: skip


def _distancia(a: str, b: str) -> int:
    anterior = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        atual = [i]
        for j, cb in enumerate(b, start=1):
            atual.append(min(anterior[j] + 1, atual[j - 1] + 1, anterior[j - 1] + (ca != cb)))
        anterior = atual
    return anterior[-1]


def _negativa(palavra: str) -> bool:
    if palavra in _NEGATIVAS:
        return True
    if len(palavra) < 5 or not palavra.startswith("ne"):
        return False
    return any(_distancia(palavra, alvo) <= teto for alvo, teto in _NENHUMA)


def simbolos(texto: str, quantas: int) -> tuple[str, list[int]] | None:
    """A mensagem em simbolos (X nao, S sim, O so', T todas, R outras, N numero) sem as palavras de ligacao;
    `None` quando ha' palavra fora do vocabulario ou numero fora das perguntas."""
    simbolos: list[str] = []
    numeros: list[int] = []
    for palavra in normalizar(texto).split():
        if palavra in _LIGACAO:
            continue
        numero = _NUMERO.match(palavra)
        valor = int(numero.group(1)) if numero else _ORDINAIS.get(palavra)
        if palavra == "ultima":
            valor = quantas
        if valor is not None:
            if not 1 <= valor <= quantas:
                return None
            simbolos.append("N")
            numeros.append(valor)
        elif palavra in _SIM:
            simbolos.append("S")
        elif palavra in _SO:
            simbolos.append("O")
        elif palavra in _TODAS:
            simbolos.append("T")
        elif palavra in _OUTRAS:
            simbolos.append("R")
        elif _negativa(palavra):
            simbolos.append("X")
        else:
            return None
    return "".join(simbolos), numeros


#: So' negativas (e "todas"): o "nao" a tudo.
TODAS_NAO: Final[re.Pattern[str]] = re.compile(r"^[XT]*X[XT]*$")


def nega_tudo(texto: str) -> bool:
    """A mensagem e' so' "nao" (a tudo), no vocabulario fechado: "nao", "nehuma", "ja' respondi nao para
    todas"."""
    lido = simbolos(texto, 9)
    return lido is not None and bool(lido[0]) and TODAS_NAO.match(lido[0]) is not None


__all__ = ["TODAS_NAO", "nega_tudo", "simbolos"]
