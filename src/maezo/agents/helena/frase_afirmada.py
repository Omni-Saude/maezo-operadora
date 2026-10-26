"""A TRAVA DE ENTRADA SO' COM FRASE AFIRMADA CLARA (teste do dono de 25/10/2026, caso b; complemento da
ADR-0012, decisao do dono de 25/10/2026).

O CASO. Depois da pergunta de alerta ("Junto com isso, tem dor no peito, falta de ar, desmaio, boca torta ou
fraqueza de um lado do corpo?"), a pessoa respondeu "nao, mas o que dor no peito ... tem a ver" repetindo os
sinais que a Helena listou. A trava lexica achou os sinais (o alarme deterministico leu "fraqueza de um lado"
como deficit neurologico) e abriu P1 + 192 sem o roteador. Sinal dentro de PERGUNTA, depois de "nao" ou
copiado da pergunta da Helena nao e' sinal relatado.

A REGRA. A decisao de urgencia continua deterministica (ADR-0012), agora sobre as frases AFIRMADAS: cada frase
da mensagem (separada por `.`, `?`, `!`, `;` ou quebra de linha; reticencias nao separam) e' DESCARTADA da
leitura de alarme quando, sem nenhuma marca de afirmacao ("tenho", "esta'", "to", "sinto", "ele tem", "sim",
um verbo no passado ou no gerundio como "desmaiou"/"sangrando") e sem pedido de orientacao ("o que eu faco?",
"chamo o samu?"):
  * e' PERGUNTA sobre o sinal (termina em `?` ou traz "o que", "tem a ver", "pode ser", "e' normal",
    "significa"...);
  * comeca NEGANDO ("nao, mas ...") e repete dois ou mais sinais que a Helena listou na ultima fala;
  * e' ECO da ultima fala da Helena (8+ palavras seguidas iguais, ou 3+ sinais da lista dela).
Do lado seguro: "sim" no comeco de qualquer frase mantem a mensagem inteira; a negacao colada ao sinal
continua lida como antes (`base_saude.negado`); risco psicossocial (CVV 188) nao passa por aqui. O que sobra
(o texto AFIRMADO) e' o que os lexicos de alarme leem; sem nada descartado, o texto volta identico.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from typing import Final

#: Reticencias continuam a frase ("dor no peito... tem a ver?" e' UMA pergunta).
_RETICENCIAS: Final[re.Pattern[str]] = re.compile(r"\.{2,}|…")
_FIM_DE_FRASE: Final[re.Pattern[str]] = re.compile(r"(?<=[.?!;\n])")

#: Marca de AFIRMACAO (primeira ou terceira pessoa, presente ou passado): a frase relata, nao pergunta.
#: "tem a ver"/"tem haver"/"tem relacao" nao contam (sao a pergunta do caso b).
_AFIRMACAO: Final[re.Pattern[str]] = re.compile(
    r"\b(?:sim|tenho|tive|teve|tinha|tem(?! a ver| haver| que ver| relacao| algo a ver| alguma relacao)"
    r"|estou|esta|to|ta|tava|estava|estamos|estao|sinto|sente|senti|sentiu|sentindo|sentia|comecei|comecou"
    r"|apareceu|fiquei|ficou|deu|veio|vem|piorou|agora|acordou|acordei|caiu|bateu|bati|sou|fui|foi)\b"
)
#: Verbo no passado (-ou/-ei) ou no gerundio (-ando/-endo/-indo): "desmaiou", "vomitei", "sangrando".
_VERBO_CONJUGADO: Final[re.Pattern[str]] = re.compile(r"\b\w{3,}(?:ou|ei|ando|endo|indo)\b")
_NAO_SAO_VERBO: Final[frozenset[str]] = frozenset({"quando", "enquanto", "sendo", "lindo", "vindo"})
#: Pergunta que PRESSUPOE o sinal (pede o que fazer): a frase vale como relato.
_PEDE_ORIENTACAO: Final[re.Pattern[str]] = re.compile(
    r"\b(?:o que (?:eu )?(?:faco|fazer|devo|faz)|devo|chamo|ligo|levo|preciso (?:ir|ligar|chamar|levar)"
    r"|socorro|samu|192|ambulancia|pronto socorro|emergencia|hospital)\b"
)
#: Pergunta SOBRE o sinal, mesmo sem o `?` (o caso b nao tinha ponto de interrogacao).
_PERGUNTA_SOBRE_O_SINAL: Final[re.Pattern[str]] = re.compile(
    r"\b(?:o que|oque|por que|sera|seria|pode ser|podem ser|tem a ver|tem haver|tem que ver|a ver com"
    r"|relacao|e normal|eh normal|e grave|eh grave|e perigoso|e sinal|significa|quer dizer|como assim"
    r"|qual|quais|isso e)\b"
)
_ABRE_NEGANDO: Final[re.Pattern[str]] = re.compile(r"^(?:nao|n|nem|nenhum|nenhuma|nada|negativo|sem)\b")
_ABRE_COM_SIM: Final[re.Pattern[str]] = re.compile(r"^(?:sim|s|isso|tenho|tem sim)\b")
#: Eco longo: tantas palavras SEGUIDAS iguais as da ultima fala da Helena.
ECO_LONGO_PALAVRAS: Final[int] = 8
#: Trecho copiado da Helena que nao conta como afirmacao ("tem dor no peito" dentro da pergunta copiada).
_ECO_CURTO_PALAVRAS: Final[int] = 5
#: A frase curta que, logo depois de uma pergunta descartada, confirma o sinal dela ("to sentindo agora").
_CONFIRMACAO_PALAVRAS: Final[int] = 5


def normalizar(texto: str) -> str:
    """Sem acento, minusculo, so' letras/numeros e espacos simples."""
    sem = "".join(c for c in unicodedata.normalize("NFD", texto or "") if unicodedata.category(c) != "Mn")
    return " ".join(re.sub(r"[^\w\s]", " ", sem.lower()).split())


def frases(texto: str) -> list[str]:
    """As frases da mensagem, com a pontuacao final de cada uma (reticencias nao separam)."""
    corrido = _RETICENCIAS.sub(" ", texto or "")
    return [f for f in _FIM_DE_FRASE.split(corrido) if f.strip()]


def _sem_trechos_da_helena(palavras: list[str], helena: list[str], minimo: int) -> list[str]:
    """As palavras da frase sem os trechos de `minimo`+ palavras seguidas iguais aos da fala da Helena."""
    if not helena or len(palavras) < minimo:
        return palavras
    tira = [False] * len(palavras)
    for i in range(len(palavras)):
        for j in range(len(helena)):
            n = 0
            while i + n < len(palavras) and j + n < len(helena) and palavras[i + n] == helena[j + n]:
                n += 1
            if n >= minimo:
                for k in range(i, i + n):
                    tira[k] = True
    return [p for p, t in zip(palavras, tira, strict=True) if not t]


def _afirma(plano: str) -> bool:
    if _AFIRMACAO.search(plano):
        return True
    return any(m.group(0) not in _NAO_SAO_VERBO for m in _VERBO_CONJUGADO.finditer(plano))


def _sinais(plano: str) -> set[str]:
    """Os trechos de sinal (lexico forte e vocabulario da tabela) na frase normalizada."""
    from maezo.agents.helena.graph import _SINAL_FORTE_DE_EMERGENCIA
    from maezo.agents.helena.travas_entrada import _VOCABULARIO_DA_TABELA

    return {
        m.group(0) for p in (_SINAL_FORTE_DE_EMERGENCIA, _VOCABULARIO_DA_TABELA) for m in p.finditer(plano)
    }


def _descartada(frase: str, helena: str) -> bool:
    plano = normalizar(frase)
    if not plano:
        return False
    palavras_helena = helena.split()
    proprias = " ".join(_sem_trechos_da_helena(plano.split(), palavras_helena, _ECO_CURTO_PALAVRAS))
    if _afirma(proprias) or _PEDE_ORIENTACAO.search(plano):
        return False
    if frase.rstrip().endswith("?") or _PERGUNTA_SOBRE_O_SINAL.search(plano):
        return True
    if not helena:
        return False
    ecoados = {s for s in _sinais(plano) if s in helena}
    if _ABRE_NEGANDO.search(plano) and len(ecoados) >= 2:
        return True
    eco_longo = len(_sem_trechos_da_helena(plano.split(), palavras_helena, ECO_LONGO_PALAVRAS)) < len(
        plano.split()
    )
    return eco_longo or len(ecoados) >= 3


def texto_afirmado(texto: str, fala_da_helena: str = "") -> str:
    """O texto SO' com as frases afirmadas (ver o docstring do modulo). Sem nada descartado, `texto` identico.
    `fala_da_helena` = a ULTIMA fala da Helena na conversa (para o eco); vazia, so' a regra da pergunta."""
    partes = frases(texto)
    if len(partes) == 0:
        return texto or ""
    if any(_ABRE_COM_SIM.search(normalizar(p)) for p in partes):
        return texto
    helena = normalizar(fala_da_helena)
    fora = [_descartada(p, helena) for p in partes]
    for i in range(1, len(partes)):
        seguinte = normalizar(partes[i])
        if fora[i - 1] and not fora[i] and _confirma_a_anterior(seguinte):
            # "dor no peito? to sentindo agora": a frase curta seguinte afirma o sinal da pergunta.
            fora[i - 1] = False
    if not any(fora):
        return texto
    return " ".join(p.strip() for p, f in zip(partes, fora, strict=True) if not f)


def _confirma_a_anterior(plano: str) -> bool:
    """Frase CURTA (ate' `_CONFIRMACAO_PALAVRAS`), com afirmacao e sem sinal proprio: confirma a anterior."""
    return 0 < len(plano.split()) <= _CONFIRMACAO_PALAVRAS and _afirma(plano) and not _sinais(plano)


def ultima_fala(falas_da_helena: Iterable[str]) -> str:
    lista = list(falas_da_helena)
    return lista[-1] if lista else ""


__all__ = ["ECO_LONGO_PALAVRAS", "frases", "normalizar", "texto_afirmado", "ultima_fala"]
