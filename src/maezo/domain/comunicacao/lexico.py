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


# --- Frases afirmadas (a pergunta sobre um sinal nao e' o sinal) ----------------------------------------
#: Limpeza da saude, etapa 3 (dono, 02/11/2026): MOVIDO de `agents/helena/frase_afirmada.py` (teste do dono de
#: 25/10/2026), SO' a parte que a conversa ainda usa (os lexicos de emergencia de `graph._sinal_afirmado`): a
#: frase que PERGUNTA sobre um sinal, ou que levanta uma hipotese, sem marca de afirmacao, sai da leitura. O
#: eco da fala da Helena e o vocabulario da tabela sairam com o alarme por palavra (ninguem mais os le).
_RETICENCIAS: Final[re.Pattern[str]] = re.compile(r"\.{2,}|…")
_FIM_DE_FRASE: Final[re.Pattern[str]] = re.compile(r"(?<=[.?!;\n])")
_AFIRMACAO: Final[re.Pattern[str]] = re.compile(
    r"\b(?:sim|tenho|tive|teve|tinha|tem(?! a ver| haver| que ver| relacao| algo a ver| alguma relacao)"
    r"|estou|esta|to|ta|tava|estava|estamos|estao|sinto|sente|senti|sentiu|sentindo|sentia|comecei|comecou"
    r"|apareceu|fiquei|ficou|deu|veio|vem|piorou|agora|acordou|acordei|caiu|bateu|bati|sou|fui|foi)\b"
)
_VERBO_CONJUGADO: Final[re.Pattern[str]] = re.compile(r"\b\w{3,}(?:ou|ei|ando|endo|indo)\b")
_NAO_SAO_VERBO: Final[frozenset[str]] = frozenset({"quando", "enquanto", "sendo", "lindo", "vindo"})
_PEDE_ORIENTACAO: Final[re.Pattern[str]] = re.compile(
    r"\b(?:o que (?:eu )?(?:faco|fazer|devo|faz)|devo|chamo|ligo|levo|preciso (?:ir|ligar|chamar|levar)"
    r"|socorro|samu|192|ambulancia|pronto socorro|emergencia|hospital)\b"
)
_PERGUNTA_SOBRE_O_SINAL: Final[re.Pattern[str]] = re.compile(
    r"\b(?:o que|oque|por que|sera|seria|pode ser|podem ser|tem a ver|tem haver|tem que ver|a ver com"
    r"|relacao|e normal|eh normal|e grave|eh grave|e perigoso|e sinal|significa|quer dizer|como assim"
    r"|qual|quais|isso e)\b"
)
_HIPOTESE: Final[re.Pattern[str]] = re.compile(
    r"\b(?:e se|se|caso)\b(?: \w+){0,3} (?:ficar|parar|tiver|tiverem|desmaiar|convulsionar|acontecer"
    r"|nao acordar|nao respirar)\b"
)
_ABRE_COM_SIM: Final[re.Pattern[str]] = re.compile(r"^(?:sim|s|tenho|tem sim)\b|^isso(?: mesmo| ai| sim)?$")
_CONFIRMACAO_PALAVRAS: Final[int] = 5


def _so_letras(texto: str) -> str:
    sem = "".join(c for c in unicodedata.normalize("NFD", texto or "") if unicodedata.category(c) != "Mn")
    return " ".join(re.sub(r"[^\w\s]", " ", sem.lower()).split())


def _afirma(plano: str) -> bool:
    if _AFIRMACAO.search(plano):
        return True
    return any(m.group(0) not in _NAO_SAO_VERBO for m in _VERBO_CONJUGADO.finditer(plano))


def _descartada(frase: str) -> bool:
    plano = _so_letras(frase)
    if not plano or _afirma(plano):
        return False
    if _HIPOTESE.search(plano):
        return True  # "e se ele parar de respirar?", "o que fazer se o bebe ficar roxo?"
    if _PEDE_ORIENTACAO.search(plano):
        return False
    return frase.rstrip().endswith("?") or _PERGUNTA_SOBRE_O_SINAL.search(plano) is not None


def frases_afirmadas(texto: str, sinal: re.Pattern[str]) -> str:
    """O texto SO' com as frases afirmadas: sai a frase que so' PERGUNTA sobre o sinal (ou levanta uma
    hipotese), sem marca de afirmacao. "Sim" no comeco de qualquer frase mantem tudo; a frase curta com
    afirmacao e sem `sinal` proprio, logo depois de uma pergunta descartada, confirma a pergunta ("dor no
    peito? to sentindo agora"). Sem nada descartado, `texto` identico."""
    corrido = _RETICENCIAS.sub(" ", texto or "")
    partes = [f for f in _FIM_DE_FRASE.split(corrido) if f.strip()]
    if not partes:
        return texto or ""
    if any(_ABRE_COM_SIM.search(_so_letras(p)) for p in partes):
        return texto
    fora = [_descartada(p) for p in partes]
    for i in range(1, len(partes)):
        seguinte = _so_letras(partes[i])
        curta = 0 < len(seguinte.split()) <= _CONFIRMACAO_PALAVRAS
        if fora[i - 1] and not fora[i] and curta and _afirma(seguinte) and not sinal.search(seguinte):
            fora[i - 1] = False
    if not any(fora):
        return texto
    return " ".join(p.strip() for p, f in zip(partes, fora, strict=True) if not f)


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
    "frases_afirmadas",
    "mencao_afirmada",
    "negado",
    "normalizar",
    "normalizar_com_cortes",
    "sem_abertura_repetida",
    "sem_preambulo",
    "tem_risco",
]
