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
#: Valor em reais ("R$ 20", "r$20,50"): quantidade com unidade propria (HEL-5a).
_DINHEIRO: Final[re.Pattern[str]] = re.compile(r"r\$\s*(\d+(?:[.,]\d+)?)")
#: Numero seguido da palavra que diz O QUE ele conta ("20 sessoes", "20 exames", "20 reais", "10 dias uteis").
_NUMERO_COM_SEGUINTE: Final[re.Pattern[str]] = re.compile(
    r"(?<![\d/.,$-])(\d+(?:[.,]\d+)?)\s*(%|[a-z]+(?: [a-z]+)?)"
)
#: A frequencia em qualquer ponto da frase (HEL-5a: "por mes, sao 20", "mensais", "toda semana").
_FREQUENCIA: Final[re.Pattern[str]] = re.compile(
    r"\b(?:por|a cada|ao|cada|todo|toda|todos|todas)\s+(dia|dias|semana|semanas|mes|meses|ano|anos|pedido"
    r"|pedidos|guia|sessao|consulta|vez)\b|\b(mensal|mensais|anual|anuais|semanal|semanais|diario|diaria"
    r"|diarios|diarias)\b"
)
_FREQUENCIA_NORMAL: Final[dict[str, str]] = {
    "mensal": "mes", "mensais": "mes", "anual": "ano", "anuais": "ano", "semanal": "semana",
    "semanais": "semana", "diario": "dia", "diaria": "dia", "diarios": "dia", "diarias": "dia",
    "pedidos": "pedido",
}  # fmt: skip
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
    """ "20,00" e "20.0" valem "20" (centavos zerados nao sao outro valor)."""
    numero = bruto.replace(",", ".")
    return re.sub(r"\.0+$", "", numero)


def _singular(palavra: str) -> str:
    if palavra.endswith("oes"):
        return palavra[:-3] + "ao"
    if palavra.endswith("ais"):
        return palavra[:-3] + "al"
    if palavra.endswith("es") and len(palavra) > 4 and palavra[-3] in "rsz":
        return palavra[:-2]
    if palavra.endswith("s") and len(palavra) > 3:
        return palavra[:-1]
    return palavra


def _unidade(seguinte: str) -> str:
    """A unidade de uma quantidade: a conhecida (`_UNIDADES`), "reais" = brl, ou a PROPRIA palavra seguinte no
    singular ("20 exames" e' 20 exame, nunca 20 sessao)."""
    for multipla in ("dias uteis", "dia util", "por cento"):
        if seguinte.startswith(multipla):
            return _UNIDADES[multipla]
    palavra = seguinte.split()[0] if seguinte.split() else ""
    if palavra in ("real", "reais"):
        return "brl"
    if palavra == "%":
        return "pct"
    return _UNIDADES.get(palavra) or _singular(palavra)


def _frequencia(frase: str) -> str | None:
    """A frequencia dita na FRASE ("por mes", "mensal", "toda semana", "ao ano"...), em qualquer posicao."""
    m = _FREQUENCIA.search(frase)
    if m is None:
        return None
    palavra = next(g for g in m.groups() if g)
    return _FREQUENCIA_NORMAL.get(palavra, _UNIDADES.get(palavra, _singular(palavra)))


def _quantidades(plano: str) -> set[tuple[str, str, str | None]]:
    """(numero, unidade, frequencia): "20 sessoes por mes" = ("20", "sessao", "mes"); "R$ 20" = ("20", "brl",
    None); "20 exames" = ("20", "exame", None). A frequencia e' a da frase (HEL-5a)."""
    achadas: set[tuple[str, str, str | None]] = set()
    for frase in _frases(plano):
        por = _frequencia(frase)
        for m in _NUMERO_COM_SEGUINTE.finditer(frase):
            unidade = _unidade(m.group(2))
            if unidade:
                achadas.add((_numero(m.group(1)), unidade, por))
        for m in _DINHEIRO.finditer(frase):
            achadas.add((_numero(m.group(1)), "brl", por))
    return achadas


def _quantidades_conferem(
    do_rascunho: set[tuple[str, str, str | None]], das_fontes: set[tuple[str, str, str | None]]
) -> bool:
    """Cada quantidade do rascunho existe numa fonte com o MESMO numero e unidade; se o rascunho diz uma
    frequencia, a fonte tem de dizer a mesma (frequencia inventada nao passa)."""
    return all(
        any(n == fn and u == fu and (por is None or por == fpor) for fn, fu, fpor in das_fontes)
        for n, u, por in do_rascunho
    )


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


def verificar(
    rascunho: str, trechos: Sequence[str], *, procedimentos: frozenset[str] = frozenset()
) -> Veredito:
    """O rascunho confere com os trechos? Sem trecho nenhum, nada confere (falha fechada).

    `procedimentos`: o vocabulario de procedimentos (frases normalizadas) para conferir A QUE procedimento a
    autorizacao afirmada se refere; vazio, vale o vocabulario generico (`PROCEDIMENTOS_GENERICOS`)."""
    procedimentos = procedimentos | PROCEDIMENTOS_GENERICOS
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
    quantidades_das_fontes: set[tuple[str, str, str | None]] = set().union(*(_quantidades(f) for f in fontes))
    if not _quantidades_conferem(_quantidades(corpo), quantidades_das_fontes):
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
    elif afirmado and not _sujeito_confere(corpo, fontes, procedimentos):
        motivos.append("autorizacao_de_outro_procedimento")
    return Veredito(not motivos, tuple(motivos))


def _frases(plano: str) -> list[str]:
    return [f.strip() for f in re.split(r"[.;!?]+", plano) if f.strip()]


def _palavras(texto: str) -> list[str]:
    """Minusculas, sem acento e SEM pontuacao ("tomografia," e "Tomografia:" sao "tomografia")."""
    return re.sub(r"[^a-z0-9]+", " ", _plano(texto)).split()


def procedimentos_citados(texto: str, procedimentos: frozenset[str]) -> set[str]:
    """Os procedimentos que o texto cita, no SINGULAR: frases do vocabulario (1 a 4 palavras), palavras com
    sufixo de procedimento ("mamografia", "angiotomografia") e siglas em maiuscula do texto original ("TC",
    "PET") que nao sao de documento (HEL-5a, revisao independente)."""
    palavras = [_singular(p) for p in _palavras(texto)]
    vocabulario = {" ".join(_singular(p) for p in v.split()) for v in procedimentos}
    citados = {
        " ".join(palavras[i : i + n])
        for n in range(1, 5)
        for i in range(len(palavras) - n + 1)
        if " ".join(palavras[i : i + n]) in vocabulario
    }
    citados.update(p for p in palavras if len(p) >= 7 and _SUFIXO_DE_PROCEDIMENTO.search(p))
    citados.update(s.lower() for s in _SIGLA.findall(texto) if s not in _SIGLAS_QUE_NAO_SAO)
    return citados


def _cita(frase_da_fonte: str, citado: str) -> bool:
    palavras = [_singular(p) for p in _palavras(frase_da_fonte)]
    alvo = citado.split()
    return any(palavras[i : i + len(alvo)] == alvo for i in range(len(palavras) - len(alvo) + 1))


def _sujeito_confere(corpo: str, fontes: Sequence[str], procedimentos: frozenset[str]) -> bool:
    """HEL-5a (revisao independente): a frase do rascunho que afirma (ou nega) a necessidade de autorizacao
    so' confere se TODO procedimento que ela cita aparece numa frase da fonte que afirma o MESMO sobre
    autorizacao ("a consulta basica precisa de autorizacao" contra a regra da ressonancia nao passa)."""
    frases_das_fontes = [f for fonte in fontes for f in _frases(fonte) if _autorizacao(f)]
    anterior: set[bool] = set()
    for frase in _frases(corpo):
        polaridade = _autorizacao(frase)
        if not polaridade and anterior and _ANAFORA.search(frase):
            polaridade = anterior  # "A ressonancia precisa de autorizacao. A tomografia tambem."
        anterior = polaridade
        if not polaridade:
            continue
        for citado in procedimentos_citados(frase, procedimentos):
            if not any(_cita(f, citado) and polaridade <= _autorizacao(f) for f in frases_das_fontes):
                return False
    return True


#: Sufixos de nome de procedimento (o vocabulario nunca e' completo: "mamografia", "angiotomografia").
_SUFIXO_DE_PROCEDIMENTO: Final[re.Pattern[str]] = re.compile(
    r"(?:grafia|scopia|tomia|ectomia|plastia|metria|terapia|grama|cintilo\w*)$"
)
#: Siglas em maiuscula no texto original (PET, TC, RM); as de documento/orgao nao contam.
_SIGLA: Final[re.Pattern[str]] = re.compile(r"\b[A-Z]{2,4}\b")
_SIGLAS_QUE_NAO_SAO: Final[frozenset[str]] = frozenset(
    {"ANS", "SUS", "CPF", "RG", "OK", "CID", "TUSS", "DUT", "CPT", "NIP", "CNPJ", "BRL", "SMS", "APP"}
)
#: A frase seguinte que retoma a afirmacao anterior ("A tomografia tambem.").
_ANAFORA: Final[re.Pattern[str]] = re.compile(
    r"\b(?:tambem|idem|o mesmo|a mesma|igualmente|da mesma forma)\b"
)


#: Procedimentos genericos, sempre no vocabulario do sujeito da autorizacao (HEL-5a).
PROCEDIMENTOS_GENERICOS: Final[frozenset[str]] = frozenset(
    {
        "cirurgia",
        "consulta",
        "consulta basica",
        "internacao",
        "terapia",
        "fisioterapia",
        "quimioterapia",
        "radioterapia",
        "parto",
        "ressonancia",
        "tomografia",
        "endoscopia",
        "colonoscopia",
    }
)

__all__ = ["FORMULA_FIXA", "PROCEDIMENTOS_GENERICOS", "Veredito", "procedimentos_citados", "verificar"]
