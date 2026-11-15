"""VERIFICADOR DETERMINISTICO de fatos da cobertura, ANTES do envio (dono, 14/11/2026; DL-0167, HEL-3b).

A redacao pode ser livre; os fatos, nao. Todo rascunho da habilidade `cobertura` passa por aqui com os
trechos de onde saiu (texto das regras do pacote K2 recuperadas, ou o texto dos fatos de uma consulta) e so'
e' enviado se TODOS os fatos conferem:

1. QUANTIDADES com unidade (prazo em dias/dias uteis/horas/meses/anos, numero de sessoes/consultas/vezes,
   percentuais): cada uma do rascunho tem de aparecer, com o MESMO numero e a MESMA unidade, em algum trecho.
   Numero por extenso (ate' as dezenas e centenas usuais de prazo) conta como o numero.
2. DATAS (`dd/mm/aaaa`, `dd/mm`, `aaaa-mm-dd`) e todo outro TOKEN COM DIGITO (norma `465/2021`, codigo,
   numero solto): cada um tem de aparecer igual em algum trecho (nada de numero inventado).
3. NECESSIDADE DE AUTORIZACAO: se o rascunho diz que precisa (ou que nao precisa) de autorizacao, algum
   trecho tem de dizer o MESMO, com a mesma polaridade; e nenhum trecho pode dizer o contrario.
4. FRONTEIRA DA DECISAO: o rascunho nunca promete nem nega cobertura ao caso da pessoa ("seu plano cobre",
   "vai ser autorizado", "voce tem direito", "nao tem cobertura"...), nunca leva link, e termina com a
   formula fixa "informacao nao e' decisao" (`FORMULA_FIXA`) — as MESMAS de `travas.py` (o esqueleto as aplica
   de novo a todo texto, com as cercas de saida de sempre).

Puro: so' texto, sem I/O, sem modelo. O veredito leva so' os MOTIVOS (codigos curtos), nunca o texto.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from maezo.agents.helena.habilidades.cobertura.travas import DECISAO_DO_CASO, FORMULA_FIXA

_UNIDADES: Final[dict[str, str]] = {
    "dia util": "dia_util",
    "dias uteis": "dia_util",
    "dia": "dia",
    "dias": "dia",
    "hora": "hora",
    "horas": "hora",
    "mes": "mes",
    "meses": "mes",
    "ano": "ano",
    "anos": "ano",
    "semana": "semana",
    "semanas": "semana",
    "sessao": "sessao",
    "sessoes": "sessao",
    "consulta": "consulta",
    "consultas": "consulta",
    "vez": "vez",
    "vezes": "vez",
    "%": "pct",
    "por cento": "pct",
}
_EXTENSO: Final[dict[str, int]] = {
    "dois": 2, "duas": 2, "tres": 3, "quatro": 4, "cinco": 5, "seis": 6, "sete": 7,
    "oito": 8, "nove": 9, "dez": 10, "onze": 11, "doze": 12, "treze": 13, "catorze": 14, "quatorze": 14,
    "quinze": 15, "dezesseis": 16, "dezessete": 17, "dezoito": 18, "dezenove": 19, "vinte": 20, "trinta": 30,
    "quarenta": 40, "cinquenta": 50, "sessenta": 60, "setenta": 70, "oitenta": 80, "noventa": 90, "cem": 100,
    "cento": 100, "duzentos": 200, "trezentos": 300,
}  # fmt: skip
_NUMERO_EXTENSO: Final[str] = r"(?:" + "|".join(sorted(_EXTENSO, key=len, reverse=True)) + r")"
#: "cento e oitenta", "vinte e quatro", "trinta": a soma das partes ligadas por "e".
_EXTENSO_COMPOSTO: Final[re.Pattern[str]] = re.compile(rf"\b{_NUMERO_EXTENSO}(?: e {_NUMERO_EXTENSO})*\b")
_UNIDADE: Final[str] = (
    r"(?:" + "|".join(re.escape(u) for u in sorted(_UNIDADES, key=len, reverse=True)) + r")"
)
_QUANTIDADE: Final[re.Pattern[str]] = re.compile(rf"(?<![\d/.,-])(\d+(?:[.,]\d+)?)\s*({_UNIDADE})(?![a-z])")
_DATA: Final[re.Pattern[str]] = re.compile(r"^(?:\d{1,2}/\d{1,2}(?:/\d{2,4})?|\d{4}-\d{2}-\d{2})$")
#: Todo "token com digito" (numero, data, norma `465/2021`, codigo, `24h`): tem de existir igual numa fonte.
_TOKEN_COM_DIGITO: Final[re.Pattern[str]] = re.compile(r"[\w/.,%-]*\d[\w/.,%-]*")
#: A necessidade de autorizacao e seus sinonimos (revisao independente: "liberacao", "guia", "aprovacao
#: previa", "senha" diziam a mesma coisa sem serem conferidos).
_AUTORIZACAO: Final[re.Pattern[str]] = re.compile(
    r"\b(?:pre ?autoriza\w*|autoriza\w*|libera\w*|aprovac\w* previa|pre ?aprovac\w*|guias?|senhas?)\b"
)
_NEGA_ANTES: Final[re.Pattern[str]] = re.compile(
    r"\b(?:nao|nem|sem|nunca|jamais|dispensa\w*|independe\w*|isent\w*|desnecessari\w*)\b"
)
#: A negacao DEPOIS do termo ("a autorizacao nao e' necessaria", "autorizacao dispensada").
_NEGA_DEPOIS: Final[re.Pattern[str]] = re.compile(
    r"^\W*(?:\w+ ){0,2}(?:nao (?:e|sera|eh) (?:necessari|precis|exigid|obrigatori)\w*"
    r"|dispensad\w*|desnecessari\w*|nao se aplica|nao e preciso)"
)
#: "uma semana", "um mes", "um ano": o artigo vira 1 SO' diante de unidade (nunca "um exame").
_UM_COM_UNIDADE: Final[re.Pattern[str]] = re.compile(
    r"\b(?:um|uma) (?=(?:dia|hora|semana|mes|ano|sessao|consulta|vez)\b)"
)
_LINK: Final[re.Pattern[str]] = re.compile(r"https?://|www\.|\b[\w-]+\.(?:com|br|org|gov|net|invalid)\b")


@dataclass(frozen=True, slots=True)
class Veredito:
    """`ok` so' com todos os fatos conferidos. `motivos`: codigos curtos (`quantidade`, `data`...)."""

    ok: bool
    motivos: tuple[str, ...]


def _plano(texto: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"\s+", " ", sem_acento)


def _por_extenso_em_digitos(plano: str) -> str:
    def _trocar(m: re.Match[str]) -> str:
        partes = m.group(0).split(" e ")
        return str(sum(_EXTENSO[p] for p in partes))

    return _EXTENSO_COMPOSTO.sub(_trocar, plano)


def _normal(texto: str) -> str:
    return _por_extenso_em_digitos(_UM_COM_UNIDADE.sub("1 ", _plano(texto)))


def _numero(bruto: str) -> str:
    return bruto.replace(",", ".")


def _quantidades(plano: str) -> set[tuple[str, str]]:
    return {(_numero(n), _UNIDADES[u]) for n, u in _QUANTIDADE.findall(plano)}


def _tokens_com_digito(plano: str) -> set[str]:
    return {t.strip(".,;:-").replace(",", ".") for t in _TOKEN_COM_DIGITO.findall(plano)} - {""}


def _autorizacao(plano: str) -> set[bool]:
    """As afirmacoes sobre necessidade de autorizacao: `True` = precisa, `False` = nao precisa."""
    polaridades: set[bool] = set()
    for m in _AUTORIZACAO.finditer(plano):
        inicio_da_frase = max(plano.rfind(".", 0, m.start()), plano.rfind(";", 0, m.start()), 0)
        antes = plano[inicio_da_frase : m.start()].split()[-5:]
        depois = plano[m.end() : m.end() + 60]
        negada = bool(_NEGA_ANTES.search(" ".join(antes))) or bool(_NEGA_DEPOIS.search(depois))
        polaridades.add(not negada)
    return polaridades


def verificar(rascunho: str, trechos: Sequence[str]) -> Veredito:
    """O rascunho confere com os trechos? Sem trecho nenhum, nada confere (falha fechada)."""
    motivos: list[str] = []
    texto = rascunho.strip()
    if not texto or not trechos or not any(t.strip() for t in trechos):
        return Veredito(False, ("sem_fonte",))
    if not texto.endswith(FORMULA_FIXA):
        motivos.append("sem_formula_fixa")
    corpo = _normal(texto[: -len(FORMULA_FIXA)] if texto.endswith(FORMULA_FIXA) else texto)
    fontes = [_normal(t) for t in trechos]
    if _LINK.search(_plano(rascunho)):
        motivos.append("link")
    if DECISAO_DO_CASO.search(corpo):
        motivos.append("decisao_do_caso")
    quantidades_das_fontes = set().union(*(_quantidades(f) for f in fontes))
    if not _quantidades(corpo) <= quantidades_das_fontes:
        motivos.append("quantidade")
    tokens_das_fontes = set().union(*(_tokens_com_digito(f) for f in fontes))
    soltos = _tokens_com_digito(corpo) - tokens_das_fontes
    if any(_DATA.match(t) for t in soltos):
        motivos.append("data")
    if any(not _DATA.match(t) for t in soltos):
        motivos.append("numero")
    afirmado = _autorizacao(corpo)
    nas_fontes = set().union(*(_autorizacao(f) for f in fontes))
    if afirmado and (not afirmado <= nas_fontes or len(nas_fontes) > 1):
        # Fonte que diz as duas coisas (precisa e nao precisa) e' dado CONFLITANTE: nada se afirma.
        motivos.append("autorizacao")
    return Veredito(not motivos, tuple(motivos))


__all__ = ["FORMULA_FIXA", "Veredito", "verificar"]
