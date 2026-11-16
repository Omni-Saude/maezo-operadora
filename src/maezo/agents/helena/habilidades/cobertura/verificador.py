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
#: Todo numero, com a palavra que diz O QUE ele conta ("20 sessoes", "20 exames", "20 reais", "10 dias
#: uteis"); sem palavra logo depois ("e' de 20.", "20, em reais") a unidade e' VAZIA e so' confere com numero
#: tambem sem unidade na fonte (HEL-5a, segunda revisao).
_NUMERO_COM_SEGUINTE: Final[re.Pattern[str]] = re.compile(
    r"(?<![\d/.,$-])(\d+(?:[.,]\d+)?)(?!\d)(?:\s*(%|[a-z]+(?: [a-z]+)?))?"
)
#: A frequencia em qualquer ponto da frase (HEL-5a: "por mes, sao 20", "mensais", "toda semana").
_PERIODO: Final[str] = r"(?:dias?|semanas?|mes|meses|anos?|semestres?|trimestres?|bimestres?|quinzenas?)"
_FREQUENCIA: Final[re.Pattern[str]] = re.compile(
    rf"\b(?:por|a cada|ao|cada|todo|toda|todos|todas)\s+({_PERIODO}|pedidos?|guias?|sessao|consulta|vez)\b"
    rf"|\b(?:no|na|num|numa|em um|em uma)\s+({_PERIODO})\b"
    r"|\b((?:mensa|anua|semana|semestra|trimestra|bimestra|quinzena)(?:l|is|lmente)|diari(?:o|a|os|as|amente))\b"
)
#: O radical do adjetivo/adverbio de frequencia e o periodo que ele diz ("mensalmente" = mes).
_FREQUENCIA_NORMAL: Final[tuple[tuple[str, str], ...]] = (
    ("mensa", "mes"), ("anua", "ano"), ("semestra", "semestre"), ("trimestra", "trimestre"),
    ("bimestra", "bimestre"), ("quinzena", "quinzena"), ("semana", "semana"), ("diari", "dia"),
)  # fmt: skip
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


def singular(palavra: str) -> str:
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
    return _UNIDADES.get(palavra) or singular(palavra)


def _frequencias(frase: str) -> frozenset[str]:
    """TODAS as frequencias ditas no texto ("por mes", "mensalmente", "toda semana", "no mes", "ao ano"), em
    qualquer posicao (HEL-5a, segunda revisao: nao so' a primeira)."""
    achadas: set[str] = set()
    for m in _FREQUENCIA.finditer(frase):
        palavra = next(g for g in m.groups() if g)
        normal = next((p for radical, p in _FREQUENCIA_NORMAL if palavra.startswith(radical)), None)
        if m.group(3) is None or normal is None:
            normal = _UNIDADES.get(palavra, singular(palavra))
        achadas.add(normal)
    return frozenset(achadas)


Quantidade = tuple[str, str, frozenset[str]]


def _quantidades(plano: str) -> set[Quantidade]:
    """(numero, unidade, frequencias da frase): "20 sessoes por mes" = ("20", "sessao", {"mes"}); "R$ 20" =
    ("20", "brl", {}); "20 exames" = ("20", "exame", {}); "e' de 20." = ("20", "", {}) (HEL-5a)."""
    achadas: set[Quantidade] = set()
    for frase in _frases(plano):
        por = _frequencias(frase)
        for m in _NUMERO_COM_SEGUINTE.finditer(frase):
            achadas.add((_numero(m.group(1)), _unidade(m.group(2) or ""), por))
        for m in _DINHEIRO.finditer(frase):
            achadas.add((_numero(m.group(1)), "brl", por))
    return achadas


def _quantidades_conferem(do_rascunho: set[Quantidade], das_fontes: set[Quantidade]) -> bool:
    """Cada quantidade do rascunho existe numa fonte com o MESMO numero e a MESMA unidade (vazia so' com
    vazia), e toda frequencia que a frase do rascunho diz, a frase da fonte diz tambem (frequencia inventada
    ou somada nao passa)."""
    return all(
        any(n == fn and u == fu and por <= fpor for fn, fu, fpor in das_fontes) for n, u, por in do_rascunho
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
    # Siglas em maiuscula do rascunho ORIGINAL ("TC", "PET-CT") entram no vocabulario: o corpo e' minusculo.
    siglas = {s.lower() for s in _SIGLA.findall(rascunho) if s not in _SIGLAS_QUE_NAO_SAO}
    procedimentos = procedimentos | PROCEDIMENTOS_GENERICOS | APELIDOS_DE_PROCEDIMENTO | siglas
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
    quantidades_das_fontes: set[Quantidade] = set().union(*(_quantidades(f) for f in fontes))
    frequencias_das_fontes = frozenset().union(*(_frequencias(f) for f in fontes))
    # A frequencia dita em OUTRA frase ("Sao 20 sessoes. Isso vale por mes.") tambem tem de estar nas fontes.
    if not _quantidades_conferem(_quantidades(corpo), quantidades_das_fontes) or not (
        _frequencias(corpo) <= frequencias_das_fontes
    ):
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
    elif afirmado and not _sujeito_confere(corpo, fontes, procedimentos | siglas):
        motivos.append("autorizacao_de_outro_procedimento")
    elif not afirmado and _procedimento_sem_fonte(corpo, fontes, procedimentos | siglas):
        # Sem afirmar autorizacao, o rascunho tambem nao cita exame que nenhuma fonte cita ("Com a tomografia
        # e' igual." contra a regra da ressonancia): a frase solta retoma o que a pessoa perguntou.
        motivos.append("procedimento_sem_fonte")
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
    palavras = [singular(p) for p in _palavras(texto)]
    vocabulario = {" ".join(singular(p) for p in v.split()) for v in procedimentos | APELIDOS_DE_PROCEDIMENTO}
    citados = {
        " ".join(palavras[i : i + n])
        for n in range(1, 5)
        for i in range(len(palavras) - n + 1)
        if " ".join(palavras[i : i + n]) in vocabulario
    }
    citados.update(
        p
        for p in palavras
        if len(p) >= 7 and _SUFIXO_DE_PROCEDIMENTO.search(p) and p not in _PALAVRAS_COMUNS_COM_SUFIXO
    )
    citados.update(s.lower() for s in _SIGLA.findall(texto) if s not in _SIGLAS_QUE_NAO_SAO)
    return citados


def _cita(frase_da_fonte: str, citado: str) -> bool:
    palavras = [singular(p) for p in _palavras(frase_da_fonte)]
    alvo = citado.split()
    return any(palavras[i : i + len(alvo)] == alvo for i in range(len(palavras) - len(alvo) + 1))


def _procedimento_sem_fonte(corpo: str, fontes: Sequence[str], procedimentos: frozenset[str]) -> bool:
    frases_das_fontes = [f for fonte in fontes for f in _frases(fonte)]
    return any(
        not any(_cita(f, c) for f in frases_das_fontes)
        for frase in _frases(corpo)
        for c in procedimentos_citados(frase, procedimentos)
    )


def _sujeito_confere(corpo: str, fontes: Sequence[str], procedimentos: frozenset[str]) -> bool:
    """HEL-5a (revisao independente): a afirmacao sobre autorizacao so' confere se TODO procedimento a que ela
    se refere aparece numa frase da fonte que afirma o MESMO sobre autorizacao. Falha fechada:

    1. todo procedimento citado em QUALQUER frase do rascunho tem de existir nas fontes (nome solto de outro
       exame, em frase propria, nao passa: "A tomografia? Precisa de autorizacao.");
    2. a frase que afirma, a frase colada a ela (antes ou depois) e a que a retoma ("tambem", "isso se
       aplica", "e' igual") contam como a MESMA afirmacao, salvo se forem a frase da fonte, literal."""
    frases_das_fontes = [f for fonte in fontes for f in _frases(fonte)]
    com_autorizacao = [f for f in frases_das_fontes if _autorizacao(f)]
    literais = set(frases_das_fontes)
    frases = _frases(corpo)
    polaridades = [_autorizacao(f) for f in frases]
    for i, frase in enumerate(frases):
        citados = procedimentos_citados(frase, procedimentos)
        if _procedimento_sem_fonte(frase, fontes, procedimentos):
            return False
        polaridade = set(polaridades[i])
        if frase not in literais or _ANAFORA.search(frase):
            for j in (i - 1, i + 1):
                if 0 <= j < len(frases):
                    polaridade |= polaridades[j]
        if not polaridade:
            continue
        for citado in citados:
            if not any(_cita(f, citado) and polaridade <= _autorizacao(f) for f in com_autorizacao):
                return False
    return True


#: Sufixos de nome de procedimento (o vocabulario nunca e' completo: "mamografia", "angiotomografia").
_SUFIXO_DE_PROCEDIMENTO: Final[re.Pattern[str]] = re.compile(
    r"(?:grafia|scopia|tomia|ectomia|plastia|metria|terapia|grama|cintilo\w*)$"
)
#: Siglas em maiuscula no texto original (PET, TC, RM); as de documento/orgao nao contam.
_SIGLA: Final[re.Pattern[str]] = re.compile(r"\b[A-Z]{2,4}\b")
_SIGLAS_QUE_NAO_SAO: Final[frozenset[str]] = frozenset(
    {
        "ANS",
        "SUS",
        "CPF",
        "RG",
        "OK",
        "CID",
        "TUSS",
        "DUT",
        "CPT",
        "NIP",
        "CNPJ",
        "BRL",
        "SMS",
        "APP",
        "PS",
        "PA",
        "UPA",
        "UBS",
        "SAC",
        "CEP",
        "PIX",
        "WPP",
        "ZAP",
        "VC",
        "VCS",
        "OBS",
        "PF",
        "PJ",
    }
)
#: A frase seguinte que retoma a afirmacao anterior ("A tomografia tambem.").
_ANAFORA: Final[re.Pattern[str]] = re.compile(
    r"\b(?:tambem|idem|o mesmo|a mesma|igualmente|da mesma forma|igual|isso|isto|vale|valem|se aplica\w*)\b"
)
#: Palavras comuns com sufixo de procedimento, que NAO sao procedimento ("programa", "fotografia").
_PALAVRAS_COMUNS_COM_SUFIXO: Final[frozenset[str]] = frozenset(
    {
        "programa", "fotografia", "telegrama", "ortografia", "geografia", "biografia", "caligrafia",
        "cronograma", "organograma", "fluxograma", "diagrama", "quilograma", "miligrama", "datilografia",
        "anagrama", "holograma", "pentagrama", "monografia", "cartografia", "coreografia", "taquigrafia",
    }
)  # fmt: skip
#: Siglas e apelidos de procedimento escritos em minuscula (WhatsApp): "tc", "pet", "mamo", "eco".
APELIDOS_DE_PROCEDIMENTO: Final[frozenset[str]] = frozenset(
    {
        "tc", "rm", "rnm", "pet", "pet ct", "pet scan", "rx", "raio x", "usg", "us", "eco", "ecg", "eeg",
        "emg", "mamo", "tomo", "colono", "endo", "cintilo", "dexa", "holter", "mapa",
    }
)  # fmt: skip


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

__all__ = [
    "APELIDOS_DE_PROCEDIMENTO",
    "FORMULA_FIXA",
    "PROCEDIMENTOS_GENERICOS",
    "Veredito",
    "procedimentos_citados",
    "singular",
    "verificar",
]
