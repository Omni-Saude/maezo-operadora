"""LEITURA DE TEXTO SEM MODELO da Helena, neutra (nenhuma camada de saude): normalizacao, negacao de um
trecho, a lista de risco psicossocial (DL-0123/DL-0151) e a higiene do texto final.

Limpeza da saude, etapa 1 (dono, 02/11/2026): MOVIDO SEM MUDANCA de `conhecimento/base_saude.py`
(`normalizar`, `normalizar_com_cortes`, `negado`, `mencao_afirmada` e as listas da negativa) e de
`conhecimento/saude_gerativa.py` (`tem_risco`, `sem_preambulo`, `sem_abertura_repetida`). A porta unica, as
travas de entrada, o roteador e a conversa leem daqui; os modulos de origem reimportam os mesmos nomes ate'
sairem. Modulo FOLHA (so' biblioteca padrao); mora no dominio porque o agente e o conhecimento o leem
(ADR-0067: fronteira plataforma x agentes).
"""

from __future__ import annotations

import re
import unicodedata
from typing import Final


# --- Normalizacao --------------------------------------------------------------------------------------
def normalizar(texto: str) -> str:
    """Minusculas, sem acento, sem caractere de formato, pontuacao virando espaco, espacos colapsados."""
    decomposto = unicodedata.normalize("NFKD", texto or "")
    sem_marca = "".join(c for c in decomposto if unicodedata.category(c) not in ("Mn", "Cf"))
    return " ".join(re.sub(r"[^\w\s]", " ", sem_marca.lower()).split())


# --- Negacao de um trecho --------------------------------------------------------------------------------
#: TRAVA DE SEGURANCA (r4 de 22/10/2026; ADR-0066, trava de alarme): a NEGACAO de um sinal de alarme e'
#: lida como negacao. "Nao, sem falta de ar, sem febre alta" respondia 192 e abria caso clinico. Um termo
#: so' e' NEGADO quando a palavra logo antes dele (pulando no maximo `_MAX_LIGACOES` palavras de ligacao) e'
#: uma negativa, na mesma frase. Do lado seguro: "nao para de vomitar", "sem conseguir respirar" e
#: "nao, mas vomitou" continuam alarme (a negativa nao esta' colada ao termo).
NEGATIVAS_DE_SINAL: Final[frozenset[str]] = frozenset({"sem", "nao", "nem", "nenhum", "nenhuma", "nada"})
LIGACOES_DA_NEGATIVA: Final[frozenset[str]] = frozenset(
    {"tem", "teve", "tinha", "ha", "houve", "apresentou", "apresenta", "sentiu", "sente", "senti", "sinto",
     "esta", "ta", "estou", "to", "com", "de", "o", "a", "os", "as", "um", "uma", "qualquer", "sinal",
     "sinais", "foi", "deu", "notei", "percebi", "vi", "tenho", "tive", "estava", "tava", "sentindo"}
)  # fmt: skip
#: (25/10/2026, caso b do teste do dono: "nao tenho dor no peito" e' negacao; tenho/tive/sentindo ligam.)
_MAX_LIGACOES: Final[int] = 3


#: A pontuacao que separa trechos vira esta marca no texto normalizado: a negativa nunca atravessa uma virgula
#: ("nao, falta de ar sim" e' alarme).
CORTE_DE_TRECHO: Final[str] = "xcortedetrechox"
_SEPARADORES: Final[re.Pattern[str]] = re.compile(r"[,.;:!?]")
_FIM_DO_ALCANCE: Final[re.Pattern[str]] = re.compile(rf"[,.;:!?]|\b(?:mas|porem|{CORTE_DE_TRECHO})\b")


def normalizar_com_cortes(texto: str) -> str:
    """Como `normalizar`, com a pontuacao que separa trechos virando `CORTE_DE_TRECHO`."""
    return normalizar(_SEPARADORES.sub(f" {CORTE_DE_TRECHO} ", texto or ""))


def negado(plano: str, inicio: int) -> bool:
    """O trecho que comeca em `inicio` esta' NEGADO pela palavra logo antes dele (mesmo trecho da frase)?"""
    antes = _FIM_DO_ALCANCE.split(plano[:inicio])[-1].split()
    for puladas, palavra in enumerate(reversed(antes)):
        if palavra in NEGATIVAS_DE_SINAL:
            return True
        if palavra not in LIGACOES_DA_NEGATIVA or puladas >= _MAX_LIGACOES:
            return False
    return False


def mencao_afirmada(padrao: re.Pattern[str], plano: str) -> bool:
    """Algum trecho do `padrao` aparece no texto (normalizado, com a pontuacao ou os cortes) SEM estar negado.
    Sem nenhuma ocorrencia, `False`."""
    return any(not negado(plano, m.start()) for m in padrao.finditer(plano))


# --- Risco psicossocial pelo texto (DL-0123; antes do modelo, DL-0151) -----------------------------------
RISCO_NA_MENSAGEM: Final[re.Pattern[str]] = re.compile(
    r"suicid|\bme matar\b|\bquero morrer\b|\btirar (?:a )?minha (?:propria )?vida\b"
    r"|\bnao (?:quero|aguento) mais viver\b|\bmelhor (?:nao acordar|morrer|sumir)\b|\bnao acordar mais\b"
    r"|\bme cortar\b|\bme cortado\b|\bme cortando\b|\bme machucar\b|\bautolesao\b"
    # Dossie de seguranca (18/10/2026): formas que o classificador deixou passar.
    r"|\bacabar com (?:tudo|a minha vida|minha vida|a vida)\b|\bsumir de vez\b|\bme cortei\b"
    r"|\bme machuquei de proposito\b|\bqueria (?:simplesmente )?desaparecer\b|\bnao quero mais estar aqui\b"
)


def tem_risco(plano: str) -> bool:
    """A mensagem (normalizada) fala em autolesao ou em nao querer viver? (os portoes de sempre decidem)."""
    return bool(RISCO_NA_MENSAGEM.search(plano))


# --- Higiene do texto final ------------------------------------------------------------------------------
_ABERTURA_REPETIDA: Final[re.Pattern[str]] = re.compile(
    r"^(entendi|certo|ok|claro|oi|ola|olá)[.!,:]?\s+(?=\1\b)", re.IGNORECASE
)


#: DOSSIE DE SEGURANCA (18/10/2026): o PREAMBULO de reescrita que vazou para a pessoa ("Claro, vou reescrever
#: sem essas palavras.", "Aqui esta' a versao reescrita:"). Tirado do comeco; se sobrar metatexto no meio, a
#: checagem (`metatexto`) reprova.
_PREAMBULO: Final[re.Pattern[str]] = re.compile(
    r"^\s*(?:(?:claro|certo|ok|entendido|perfeito|sem problemas?)[,.!]?\s*)?"
    r"(?:(?:vou|irei|posso) (?:reescrever|refazer|corrigir|ajustar)[^.:!\n]*[.:!]"
    r"|(?:aqui (?:esta|está|vai)|segue)[^.:!\n]{0,60}"
    r"(?:reescrit|corrigid|nova vers|versao|versão|texto)[^.:!\n]*[.:!]"
    r"|(?:versao|versão) (?:reescrita|corrigida)[^.:!\n]*[.:!])\s*",
    re.IGNORECASE,
)


def sem_preambulo(texto: str) -> str:
    """Tira o preambulo de reescrita do comeco do texto ("Claro, vou reescrever sem essas palavras.")."""
    t = texto.strip()
    for _ in range(2):
        novo = _PREAMBULO.sub("", t, count=1).strip()
        if novo == t:
            break
        t = novo
    return t


def sem_abertura_repetida(texto: str) -> str:
    """ "Entendi. Entendi: ..." / "Oi! Oi!" -> uma vez so' (a ultima, com a pontuacao dela)."""
    t = sem_preambulo(texto)
    while True:
        novo = _ABERTURA_REPETIDA.sub("", t, count=1)
        if novo == t:
            return t
        t = novo


__all__ = [
    "CORTE_DE_TRECHO",
    "LIGACOES_DA_NEGATIVA",
    "NEGATIVAS_DE_SINAL",
    "RISCO_NA_MENSAGEM",
    "mencao_afirmada",
    "negado",
    "normalizar",
    "normalizar_com_cortes",
    "sem_abertura_repetida",
    "sem_preambulo",
    "tem_risco",
]
