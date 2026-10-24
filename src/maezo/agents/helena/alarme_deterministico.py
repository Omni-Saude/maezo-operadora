"""ALARME DETERMINISTICO por texto (dossie de seguranca, fase 5 de 18/10/2026) — sentido CONSERVADOR.

O QUE E'. O dossie de seguranca da Helena mediu quadros graves que NAO escalavam: gestante com bolsa rota,
contracoes antes do tempo ou sem sentir o bebe mexer recebia "leve, moderada ou forte?" (a tabela
`triage_redflag_gestante` so' dispara com a idade gestacional dita; sem ela, cai no catch-all e a coleta
pergunta a intensidade); pressao "18 por 11" com dor de cabeca e visao embacada, crianca com febre e
"molinha", ingestao de muitos comprimidos e dor do lado direito da barriga piorando recebiam a pergunta de
alerta. Este modulo reconhece esses relatos PELO TEXTO e o grafo escala na hora (alerta clinico P1, 192).

POR QUE NO GRAFO E NAO SO' NA TABELA. A regra roda ANTES da tabela e independe do classificador ter
devolvido o codigo ou a idade gestacional; a tabela continua rodando e decidindo os demais casos. O
falso positivo leva a um 192 e a um caso (o lado seguro); o falso negativo e' o de antes.

DECISAO CLINICA. Mudanca de regra no sentido de ESCALAR MAIS, registrada no DL-0123, PENDENTE do dono
clinico (medico-auditor). Nada aqui rebaixa um caso.
"""

from __future__ import annotations

import re
from typing import Final

ALARME_GESTACAO: Final[str] = "gestacao"
ALARME_PRESSAO: Final[str] = "pressao"
ALARME_CRIANCA: Final[str] = "crianca_febre_prostrada"
ALARME_INTOXICACAO: Final[str] = "intoxicacao"
ALARME_ABDOME: Final[str] = "abdome_direito"
ALARME_ALERGIA: Final[str] = "reacao_alergica_grave"
#: Plano de ajustes v2 (DL-0154, lista de alarmes reais que SEMPRE dao 192): os sinais da propria tabela
#: (A1/A3/A5 do catalogo v1, C4/N3 da crianca, a batida na cabeca do bebe < 1 ano da base) que dependiam do
#: classificador devolver o codigo: agora reconhecidos tambem pelo TEXTO. Nenhum sinal novo; so' escala mais.
ALARME_DEFICIT_NEUROLOGICO: Final[str] = "deficit_neurologico"
ALARME_SANGRAMENTO: Final[str] = "sangramento_digestivo_ou_respiratorio"
ALARME_CRIANCA_SEM_REAGIR: Final[str] = "crianca_sem_reagir"
ALARME_BEBE_BATEU_A_CABECA: Final[str] = "bebe_bateu_a_cabeca"

_GESTACAO_NO_TEXTO: Final[re.Pattern[str]] = re.compile(
    r"\b(?:gravida|gestante|gravidez|gestacao|semanas de gestacao|gestando|bebe na barriga)\b"
)
#: Sinais obstetricos que NAO precisam da palavra "gravida" (o proprio sinal ja' diz gestacao).
_OBSTETRICO: Final[re.Pattern[str]] = re.compile(
    r"\bbolsa (?:estourou|rompeu|rompida|estourada|esta vazando)\b"
    r"|\b(?:rompeu|estourou) a bolsa\b"
    r"|\bcontrac\w*\b[^.?!]{0,40}\b(?:regulares|ritmadas|antes do tempo|de \d+ em \d+ minutos)\b"
    r"|\b(?:nao (?:sinto|senti|to sentindo|estou sentindo)|parei de sentir) (?:o |a |meu |minha )?"
    r"(?:bebe|nenem|neném) mex\w*"
    r"|\b(?:bebe|nenem) (?:nao (?:esta |ta )?mex\w*|parou de mexer|nao se mexe|mexendo menos|mexe pouco)"
)
#: Sinais que so' sao obstetricos COM a gestacao no texto.
_OBSTETRICO_COM_GESTACAO: Final[re.Pattern[str]] = re.compile(
    r"\b(?:perdendo|saindo|escorrendo|vazando)\b[^.?!]{0,20}\bliquido\b|\bperda de liquido\b"
    r"|\bsangr\w*|\bcontrac\w*|\bdor de cabeca\b[^.?!]{0,40}\b(?:visao|vista|enxergando)\b"
    r"|\b(?:inchaco|inchad\w*) (?:no rosto|na cara|nas maos)\b"
)
_PRESSAO: Final[re.Pattern[str]] = re.compile(
    r"\bpressao\b[^.?!\d]{0,30}?(?P<s>\d{2,3})(?:[.,]\d)?(?:\s*(?:por|x|/|com)\s*|\s+)(?P<d>\d{1,3})(?:[.,]\d)?\b"
)
_PRESSAO_ALTA_COM_SINAL: Final[re.Pattern[str]] = re.compile(
    r"\bpressao\b[^.?!]{0,40}\b(?:alta|subiu|altissima|muito alta|nas alturas)\b"
)
_SINAL_DE_LESAO: Final[re.Pattern[str]] = re.compile(
    r"\b(?:visao (?:embacada|turva|borrada)|vista (?:embacada|turva|escura)|enxergando (?:mal|embacado)"
    r"|dor no peito|falta de ar|confus\w*|fraqueza de um lado|formigamento de um lado|boca torta"
    r"|fala (?:enrolada|arrastada)|desmai\w*|dor de cabeca (?:muito )?forte)\b"
)
_CRIANCA: Final[re.Pattern[str]] = re.compile(
    r"\b(?:meu|minha) (?:filh[oa]|bebe|nenem|neto|neta|sobrinh[oa]|enteado|enteada)\b|\bcrianca\b|\bbebe\b"
    r"|\bnenem\b|\b(?:filh[oa]|menin[oa]) de \d+ (?:anos?|mes(?:es)?)\b"
)
_FEBRE: Final[re.Pattern[str]] = re.compile(r"\bfebre\w*\b|\bfervendo\b|\bquente demais\b")
_PROSTRADA: Final[re.Pattern[str]] = re.compile(
    r"\b(?:molinh[oa]|mole|prostrad[oa]|sonolent[oa]|muito sono|nao acorda|dificil de acordar|largad[oa]"
    r"|desanimad[oa] demais|caidinh[oa]|sem reacao|nao reage|letargic\w*|apatic[oa]|abatid[oa])\b"
)
_INTOXICACAO: Final[re.Pattern[str]] = re.compile(
    r"\b(?:engoliu|engoli|ingeriu|ingeri)\b[^.?!]{0,40}\b(?:comprimid\w*|remedi\w*|pilul\w*|pilha|bateria|moeda"
    r"|veneno|produto|agua sanitaria|soda|querosene|alvejante|detergente|cartela)\b"
    r"|\b(?:tomou|tomei|bebeu|bebi|tomaram)\b[^.?!]{0,20}\b(?:um monte|muitos|muitas|varios|varias|cartela"
    r"|caixa inteira|vidro inteiro|todos os|todas as)\b[^.?!]{0,20}\b(?:comprimid\w*|remedi\w*|pilul\w*)\b"
    r"|\b(?:veneno de rato|chumbinho|agua sanitaria|soda caustica)\b"
)
_ABDOME_DIREITO: Final[re.Pattern[str]] = re.compile(
    r"\bdor\b[^.?!]{0,30}\b(?:barriga|abdomen|abdominal|pe da barriga)\b[^.?!]{0,30}\blado direito\b"
    r"|\blado direito\b[^.?!]{0,30}\b(?:barriga|abdomen)\b"
)
#: Inchaco de boca/garganta/lingua/rosto: reacao alergica grave.
_ALERGIA_GRAVE: Final[re.Pattern[str]] = re.compile(
    r"\b(?:boca|garganta|lingua|labios?|rosto|cara|olhos)\b[^.?!]{0,30}\b(?:inchand\w*|inchad\w*|inchou"
    r"|inchaco|fechando)\b"
    r"|\b(?:inchaco|inchou|inchando|inchad\w*)\b[^.?!]{0,20}\b(?:boca|garganta|lingua|labios?|rosto)\b"
)
_PIORANDO: Final[re.Pattern[str]] = re.compile(
    r"\b(?:piorando|piora|pior|cada vez mais|so aumenta|aumentando|insuportavel|muito forte)\b"
    r"|\b(?:faz|ha|desde) (?:umas? )?\d+ ?(?:h|horas?)\b"
)


#: A3 (deficit neurologico): um lado do corpo paralisado/sem forca/dormente, boca torta, fala enrolada.
_DEFICIT_NEUROLOGICO: Final[re.Pattern[str]] = re.compile(
    r"\b(?:braco|perna|mao|lado do corpo|metade do corpo)\b[^.?!]{0,25}\b(?:paralisad\w*|sem forca|nao mexe"
    r"|nao consigo mexer|dormente|adormecid\w*|caido|caida)\b"
    r"|\bnao (?:mexo|consigo mexer|sinto) (?:o |a |meu |minha )?(?:braco|perna|mao)\b"
    r"|\b(?:fraqueza|formigamento|dormencia) (?:de|em|no|na) um lado\b|\bboca torta\b|\brosto caido\b"
    r"|\bfala (?:enrolada|arrastada)\b"
)
#: A5: sangue no vomito, vomito com sangue, tosse/cuspe com sangue.
_SANGRAMENTO: Final[re.Pattern[str]] = re.compile(
    r"\b(?:vomit\w*|golfad\w*)\b[^.?!]{0,15}\bsangue\b|\bsangue no vomito\b"
    r"|\b(?:tossi\w*|tosse|tossindo|cuspi\w*|cuspindo|escarr\w*)\b[^.?!]{0,15}\bsangue\b"
)
#: C4/N3: a crianca nao reage, nao acorda, desmaiou ou convulsionou (com ou sem febre).
_SEM_REAGIR: Final[re.Pattern[str]] = re.compile(
    r"\b(?:nao reage|sem reacao|nao responde|nao acorda|desacordad[oa]|desmai\w*|convuls\w*|ficou roxo"
    r"|ficou roxa|esta roxo|esta roxa|ta roxo|ta roxa)\b"
)
#: Batida na cabeca de BEBE com menos de 1 ano (o arquivo da base de batida na cabeca, populacao bebe).
_BATIDA_NA_CABECA: Final[re.Pattern[str]] = re.compile(
    r"\b(?:bateu|batido|batida|bater)\b[^.?!]{0,20}\bcabeca\b|\bcaiu\b[^.?!]{0,40}\bcabeca\b"
)
#: Menos de 1 ano DITO (a idade em meses ou "recem-nascido"); "bebe" sem idade segue as perguntas de alerta do
#: arquivo da base (avaliacao de 21/10/2026), com a tabela decidindo.
_BEBE_MENOR_DE_1_ANO: Final[re.Pattern[str]] = re.compile(
    r"\brecem nascid[oa]\b|\bde (?P<meses>\d{1,2}) mes(?:es)?\b"
)


def _bebe_menor_de_1_ano(plano: str) -> bool:
    for m in _BEBE_MENOR_DE_1_ANO.finditer(plano):
        meses = m.group("meses")
        if meses is None or int(meses) < 12:
            return True
    return False


def _pressao_em_mmhg(valor: int) -> int:
    """ "18 por 11" (cmHg, como se fala no Brasil) -> 180/110; "180 por 110" fica."""
    return valor * 10 if valor < 30 else valor


def pressao_relatada(plano: str) -> tuple[int, int] | None:
    """A pressao dita no texto normalizado, em mmHg (sistolica, diastolica), ou `None`."""
    m = _PRESSAO.search(plano)
    if m is None:
        return None
    return _pressao_em_mmhg(int(m.group("s"))), _pressao_em_mmhg(int(m.group("d")))


def alarme_no_relato(plano: str) -> str | None:
    """A categoria do ALARME reconhecido no texto NORMALIZADO (sem acento, minusculo), ou `None`."""
    gestacao = bool(_GESTACAO_NO_TEXTO.search(plano))
    if _OBSTETRICO.search(plano) or (gestacao and _OBSTETRICO_COM_GESTACAO.search(plano)):
        return ALARME_GESTACAO
    pressao = pressao_relatada(plano)
    if pressao is not None and (pressao[0] >= 180 or pressao[1] >= 110):
        return ALARME_PRESSAO
    elevada = pressao is not None and (pressao[0] >= 140 or pressao[1] >= 90)
    if (elevada or _PRESSAO_ALTA_COM_SINAL.search(plano)) and _SINAL_DE_LESAO.search(plano):
        return ALARME_PRESSAO
    if _CRIANCA.search(plano) and _FEBRE.search(plano) and _PROSTRADA.search(plano):
        return ALARME_CRIANCA
    if _INTOXICACAO.search(plano):
        return ALARME_INTOXICACAO
    if _ALERGIA_GRAVE.search(plano):
        return ALARME_ALERGIA
    if _ABDOME_DIREITO.search(plano) and _PIORANDO.search(plano):
        return ALARME_ABDOME
    if _afirmado(_DEFICIT_NEUROLOGICO, plano):
        return ALARME_DEFICIT_NEUROLOGICO
    if _afirmado(_SANGRAMENTO, plano):
        return ALARME_SANGRAMENTO
    if _CRIANCA.search(plano) and _afirmado(_SEM_REAGIR, plano):
        return ALARME_CRIANCA_SEM_REAGIR
    if _afirmado(_BATIDA_NA_CABECA, plano) and _bebe_menor_de_1_ano(plano):
        return ALARME_BEBE_BATEU_A_CABECA
    return None


#: A negacao colada ANTES do sinal ("nao tem sangue no vomito", "sem boca torta"): o sinal nao conta.
_NEGACAO_ANTES: Final[re.Pattern[str]] = re.compile(r"\b(?:nao|sem|nunca|nenhum|nenhuma|nem)\b[^.?!,]{0,12}$")


def _afirmado(padrao: re.Pattern[str], plano: str) -> bool:
    """O sinal aparece ao menos uma vez SEM negacao colada antes."""
    return any(not _NEGACAO_ANTES.search(plano[: m.start()]) for m in padrao.finditer(plano))


#: A populacao da tabela que o alerta leva para o caso (o roteamento clinico de sempre).
POPULACAO_DO_ALARME: Final[dict[str, str]] = {
    ALARME_GESTACAO: "gestante",
    ALARME_CRIANCA: "pediatric",
    ALARME_CRIANCA_SEM_REAGIR: "pediatric",
    ALARME_BEBE_BATEU_A_CABECA: "pediatric",
}


__all__ = [
    "ALARME_ABDOME",
    "ALARME_ALERGIA",
    "ALARME_BEBE_BATEU_A_CABECA",
    "ALARME_CRIANCA_SEM_REAGIR",
    "ALARME_DEFICIT_NEUROLOGICO",
    "ALARME_SANGRAMENTO",
    "ALARME_CRIANCA",
    "ALARME_GESTACAO",
    "ALARME_INTOXICACAO",
    "ALARME_PRESSAO",
    "POPULACAO_DO_ALARME",
    "alarme_no_relato",
    "pressao_relatada",
]
