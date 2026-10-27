"""A leitura DETERMINISTICA (sem modelo) das respostas OBVIAS as perguntas de alerta do protocolo.

Teste do dono de 26/10/2026 (logs `/ecs/maezo-operadora-dev/agent-helena`, 16:39-17:12 UTC): com o modelo em
`ReadTimeout` em quase metade das chamadas, a leitura da resposta falhou (`helena_protocolo_modelo_falhou
etapa=leitura`) e "nehuma", "nao, eu respondi NAO para todas" e "eu ja' respondi nao para todas as perguntas
acima" viraram re-pergunta e depois a oferta da equipe. A resposta obvia nao precisa do modelo:

- TODAS NAO: so' negativas ("nao", "nenhuma", "nada", erro leve de digitacao em "nenhuma"/"nenhum") e palavras
  de ligacao de um vocabulario FECHADO ("eu ja' respondi", "para todas", "as suas perguntas acima", "pqp");
- SIM EM ALGUMAS: "a 2 sim", "so' a 3", "sim na 1", "1 e 3 sim", "a primeira sim" (as outras = nao);
- TODAS SIM: "todas", "sim para todas"; o "sim" sozinho so' quando ha' UMA pergunta.

Qualquer palavra FORA do vocabulario fechado ("nao, mas to' com falta de ar agora", "nao sei", "acho que nao")
= `None`: o modelo le' (e o sinal de alarme afirmado ja' passou pela trava de entrada antes daqui). Na duvida,
nunca se adivinha.
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


def _simbolos(texto: str, quantas: int) -> tuple[str, list[int]] | None:
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


#: "so' a 3" / "sim na 1" / "a 2 sim" / "1 e 3 sim", com o "as outras nao" opcional no fim.
_SIM_EM_ALGUMAS: Final[re.Pattern[str]] = re.compile(r"^(?:[SO]+N+|N+S+|ON+S+)(?:RX|XR|RT?X|XRT?)?$")
_TODAS_NAO: Final[re.Pattern[str]] = re.compile(r"^[XT]*X[XT]*$")
_TODAS_SIM: Final[re.Pattern[str]] = re.compile(r"^S*TS*$")


def ler_sem_modelo(texto: str, quantas: int) -> tuple[str, ...] | None:
    """As respostas (`sim`/`nao`, uma por pergunta, na ordem) quando a mensagem e' obvia; senao `None`."""
    if quantas < 1:
        return None
    lido = _simbolos(texto, quantas)
    if lido is None or not lido[0]:
        return None
    simbolos, numeros = lido
    if _TODAS_NAO.match(simbolos):
        return ("nao",) * quantas
    if _SIM_EM_ALGUMAS.match(simbolos):
        return tuple("sim" if i in numeros else "nao" for i in range(1, quantas + 1))
    if _TODAS_SIM.match(simbolos) or (simbolos == "S" and quantas == 1):
        return ("sim",) * quantas
    return None


def nega_tudo(texto: str) -> bool:
    """A mensagem e' so' "nao" (a tudo), no vocabulario fechado: "nao", "nehuma", "ja' respondi nao para
    todas"."""
    lido = _simbolos(texto, 9)
    return lido is not None and bool(lido[0]) and _TODAS_NAO.match(lido[0]) is not None


__all__ = ["ler_sem_modelo", "nega_tudo"]
