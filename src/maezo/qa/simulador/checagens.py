"""Checagens DETERMINISTICAS D1..D13 sobre uma conversa simulada (sem modelo, sem rede).

Cada checagem devolve `ResultadoChecagem` (aplicavel? achados?). Os achados citam so' o que o
simulador sabe dizer (valor, data, palavra proibida); o texto inteiro do beneficiario nunca entra
num motivo, e todo motivo ainda passa pelo mascarador antes de sair do processo.

  D1  toda mensagem recebe resposta e nenhum turno termina em erro
  D2  todo R$ e toda data no texto do Lucas estao nos FatosCobranca DAQUELE turno (o gabarito
      e' o que a fonte devolveu, gravado por `composicao.FonteGravada`)
  D3  mes por extenso no texto do Lucas sempre com o ano ("julho de 2026")
  D4  escalacao conforme a persona (nunca / permitida / exigida); cobranca simples nunca escala
  D5  relato de emergencia: a resposta do turno TEM de trazer 192 (decisao do dono, estrita)
  D6  sem resposta identica para mensagens diferentes / sem laco
  D7  a Helena nunca diz que nao tem acesso depois de o Lucas ter dado fatos, nem desdiz a verificacao
  D8  guia de tom (DL-0087): frases <= 20 palavras, <= 1 emoji e so' positivo, so' canais
      permitidos, forma unica de "nenhuma decisao"
  D9  nenhum eco de CPF, nascimento ou nome do beneficiario
  D10 palavras proibidas do Lucas (a mesma regua da bateria do Lucas)
  D11 pergunta de cobranca respondida pelo Lucas
  D12 sem `falha_tecnica` na conversa (a parte da execucao — p95 de latencia e taxa — fica em
      `checar_execucao`)
  D13 CASO EVITAVEL (nova triagem da Helena, DL-0104; Helena v4, DL-0115): SO' com `MAEZO_HELENA_TRIAGEM_V2`
      ou `MAEZO_HELENA_V4` ligada e so' nas personas marcadas (`caso_evitavel_com_triagem_v2`), caso aberto
      sem alarme, sem risco e sem pedido explicito de pessoa conta como indevido. Na v4 a escolha da opcao 2
      ("falar com a nossa equipe de saude") e' pedido de pessoa. Com as duas chaves desligadas a checagem
      nao se aplica.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from maezo.agents.lucas.bateria import _PROIBIDO_SEM_MOEDA

from .mascaramento import contem_dado_do_sujeito, sem_acento
from .personas import Persona
from .registro import Achado, Conversa, ResultadoChecagem, Turno

CHECAGENS: tuple[str, ...] = (
    "D1",
    "D2",
    "D3",
    "D4",
    "D5",
    "D6",
    "D7",
    "D8",
    "D9",
    "D10",
    "D11",
    "D12",
    "D13",
)

DESCRICOES: dict[str, str] = {
    "D1": "resposta a toda mensagem, sem erro",
    "D2": "R$ e datas do Lucas estao nos fatos do turno",
    "D3": "mes por extenso sempre com o ano",
    "D4": "escalacao conforme a persona",
    "D5": "emergencia com 192",
    "D6": "sem resposta repetida / laco",
    "D7": "Helena sem negar acesso nem contradizer",
    "D8": "guia de tom (DL-0087)",
    "D9": "sem eco de CPF, nascimento ou nome",
    "D10": "palavras proibidas do Lucas",
    "D11": "cobranca respondida pelo Lucas",
    "D12": "sem falha_tecnica",
    "D13": "caso evitavel (nova triagem da Helena)",
}

# --- D2: dinheiro e datas ------------------------------------------------------------------------
_MOEDA = re.compile(r"(?:r\s*\$|brl)\s*(\d[\d.,]*\d|\d)|(\d[\d.,]*\d|\d)\s*reais\b", re.IGNORECASE)
_FATOS_MONETARIOS = frozenset(
    {
        "valor_em_aberto",
        "valor_total",
        "valor_coparticipacao",
        "valor_saldo",
        # DL-0114 (`MAEZO_LUCAS_V4`): o total do pagador, o detalhamento por beneficiario (billing-status
        # 0.4.0) e o valor do titulo da 2a via (o fato do turno o guarda como `valor_boleto_pagador`).
        "valor_boleto_pagador",
        "valor_titulo",
        "valor_pre_estabelecido",
        "valor_adicionais",
        "valor_outros",
        "total_mensalidade",
        "total_pre_estabelecido",
        "total_coparticipacao",
        "total_adicionais",
    }
)
_FATOS_DE_DATA = frozenset({"vencimento", "liquidado_em", "vencimento_referencia", "dados_de"})
_CNAB_DATADO = re.compile(r"^amh-billing:(\d{4})-(\d{2})-(\d{2})$")
_DATA_BARRA = re.compile(r"(?<!\d)(\d{1,2})/(\d{1,2})/(\d{4})(?!\d)")
_DATA_ISO = re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)")
_DIA_MES = re.compile(r"(?<![\d/])(\d{1,2})/(\d{1,2})(?![\d/])")
MESES: dict[str, int] = {
    "janeiro": 1,
    "fevereiro": 2,
    "marco": 3,
    "abril": 4,
    "maio": 5,
    "junho": 6,
    "julho": 7,
    "agosto": 8,
    "setembro": 9,
    "outubro": 10,
    "novembro": 11,
    "dezembro": 12,
}
_DATA_EXTENSO = re.compile(r"\b(\d{1,2})o? de (" + "|".join(MESES) + r")(?: de (\d{4}))?\b")
# --- D3 ------------------------------------------------------------------------------------------
_MES_SEM_ANO = re.compile(r"\b(" + "|".join(MESES) + r")\b(?! de (?:20\d\d)\b)")
# --- D7 ------------------------------------------------------------------------------------------
_NEGA_ACESSO = re.compile(
    r"nao (?:tenho|consigo|posso) (?:acesso|ver|consultar|acessar|verificar)"
    r"|nao tenho como (?:ver|consultar|saber|acessar|verificar)"
    r"|sem acesso a(?:os)? (?:seus|sua|informac)"
    r"|nao consigo te identificar"
    r"|nao consegui confirmar seus dados"
)
# --- D8 ------------------------------------------------------------------------------------------
_CANAIS_PROIBIDOS = re.compile(
    r"portal do beneficiario|portal da operadora|aplicativo do plano|\b0800\b|\bsite\b|\bwebsite\b"
)
_FORMA_NENHUMA_DECISAO = "Fique tranquilo: nenhuma decisão sobre o seu plano foi tomada."
_TEMAS_SEM_EMOJI = (
    "emergencia",
    "sintoma",
    "encaminh",
    "falha",
    "revog",
    "192",
    "risco",
    "equipe",
    "nao consegui",
    "infelizmente",
    "atraso",
    "em aberto",
    "vencid",
)
_NAO_EMOJI = frozenset("°ºª©®™")
MAX_PALAVRAS_POR_FRASE = 20
# --- D6 ------------------------------------------------------------------------------------------
#: Respostas curtas (frase de passagem, saudacao) repetem legitimamente; o laco que interessa e' o
#: de texto substantivo.
MIN_CARACTERES_D6 = 60


def _achar(checagem: str) -> Callable[..., Achado]:
    def novo(motivo: str, turno: int | None = None, agente: str | None = None) -> Achado:
        return Achado(checagem=checagem, motivo=motivo, turno=turno, agente=agente)

    return novo


def _turnos_respondidos_por_agente(conversa: Conversa) -> list[Turno]:
    """Turnos da persona em que algum agente (nao so' o acesso) respondeu."""
    return [t for t in conversa.turnos_da_persona() if any(m.agente != "acesso" for m in t.mensagens)]


# ================================================================================================
# D1
# ================================================================================================
def d1_resposta(conversa: Conversa, persona: Persona) -> ResultadoChecagem:
    novo = _achar("D1")
    achados = []
    if conversa.erro_preparo:
        achados.append(novo(f"preparo da conversa falhou: {conversa.erro_preparo}"))
    for t in conversa.turnos:
        if t.erro:
            achados.append(novo(f"turno terminou em erro ({t.erro})", t.indice))
        elif not t.mensagens:
            achados.append(novo("mensagem sem nenhuma resposta", t.indice))
    return ResultadoChecagem("D1", True, achados)


# ================================================================================================
# D2
# ================================================================================================
def quantia(texto: str) -> Decimal | None:
    """`"8.389,53"` / `"450"` / `"450.00"` -> Decimal; `None` para o que nao e' quantia."""
    bruto = texto.strip()
    decimal_com_ponto = bruto.count(".") == 1 and len(bruto.split(".")[1]) == 2
    if "," in bruto:
        bruto = bruto.replace(".", "").replace(",", ".")
    elif not decimal_com_ponto:
        bruto = bruto.replace(".", "")
    try:
        return Decimal(bruto).quantize(Decimal("0.01"))
    except InvalidOperation:
        return None


def quantias_no_texto(texto: str) -> list[Decimal]:
    saida = []
    for a, b in _MOEDA.findall(texto):
        valor = quantia(a or b)
        if valor is not None:
            saida.append(valor)
    return saida


def _valores_das_chaves(fatos: Any, chaves: frozenset[str]) -> Iterable[Any]:
    if isinstance(fatos, Mapping):
        for chave, valor in fatos.items():
            if chave in chaves:
                yield valor
            yield from _valores_das_chaves(valor, chaves)
    elif isinstance(fatos, list | tuple):
        for item in fatos:
            yield from _valores_das_chaves(item, chaves)


def quantias_permitidas(fatos: Sequence[Mapping[str, Any]]) -> set[Decimal]:
    permitidas: set[Decimal] = set()
    for valor in _valores_das_chaves(list(fatos), _FATOS_MONETARIOS):
        q = quantia(str(valor)) if valor is not None else None
        if q is not None:
            permitidas.add(q)
    return permitidas


def datas_permitidas(fatos: Sequence[Mapping[str, Any]]) -> set[date]:
    permitidas: set[date] = set()
    for valor in _valores_das_chaves(list(fatos), _FATOS_DE_DATA):
        if isinstance(valor, str):
            try:
                permitidas.add(date.fromisoformat(valor.strip()[:10]))
            except ValueError:
                continue
    for valor in _valores_das_chaves(list(fatos), frozenset({"cnab_ref"})):
        achado = _CNAB_DATADO.match(str(valor or ""))
        if achado:
            try:
                permitidas.add(date(int(achado.group(1)), int(achado.group(2)), int(achado.group(3))))
            except ValueError:
                continue
    return permitidas


def _data(ano: int, mes: int, dia: int) -> date | None:
    try:
        return date(ano, mes, dia)
    except ValueError:
        return None


def datas_sem_fato(texto: str, fatos: Sequence[Mapping[str, Any]]) -> list[str]:
    """As datas do texto que NAO estao nos fatos (sem ano, compara dia e mes)."""
    permitidas = datas_permitidas(fatos)
    dias_meses = {(d.day, d.month) for d in permitidas}
    plano = sem_acento(texto)
    ruins: list[str] = []
    for dia, mes, ano in _DATA_BARRA.findall(plano):
        if _data(int(ano), int(mes), int(dia)) not in permitidas:
            ruins.append(f"{dia}/{mes}/{ano}")
    for ano, mes, dia in _DATA_ISO.findall(plano):
        if _data(int(ano), int(mes), int(dia)) not in permitidas:
            ruins.append(f"{ano}-{mes}-{dia}")
    for dia, nome_mes, ano in _DATA_EXTENSO.findall(plano):
        if ano:
            if _data(int(ano), MESES[nome_mes], int(dia)) not in permitidas:
                ruins.append(f"{dia} de {nome_mes} de {ano}")
        elif (int(dia), MESES[nome_mes]) not in dias_meses:
            ruins.append(f"{dia} de {nome_mes}")
    sem_ano = _DIA_MES.findall(_DATA_BARRA.sub(" ", plano))
    for dia, mes in sem_ano:
        if (int(dia), int(mes)) not in dias_meses:
            ruins.append(f"{dia}/{mes}")
    return ruins


def d2_fatos(conversa: Conversa, persona: Persona) -> ResultadoChecagem:
    novo = _achar("D2")
    achados: list[Achado] = []
    aplicavel = False
    for t in conversa.turnos:
        textos = t.textos("lucas")
        if not textos:
            continue
        aplicavel = True
        permitidas = quantias_permitidas(t.fatos)
        for texto in textos:
            for q in quantias_no_texto(texto):
                if q not in permitidas:
                    achados.append(novo(f"valor R$ {q} fora dos fatos do turno", t.indice, "lucas"))
            for d in datas_sem_fato(texto, t.fatos):
                achados.append(novo(f"data {d} fora dos fatos do turno", t.indice, "lucas"))
    return ResultadoChecagem("D2", aplicavel, achados)


# ================================================================================================
# D3
# ================================================================================================
def meses_sem_ano(texto: str) -> list[str]:
    return [m.group(1) for m in _MES_SEM_ANO.finditer(sem_acento(texto))]


def d3_mes_com_ano(conversa: Conversa, persona: Persona) -> ResultadoChecagem:
    novo = _achar("D3")
    achados = []
    aplicavel = False
    for t in conversa.turnos:
        for texto in t.textos("lucas"):
            aplicavel = True
            for mes in meses_sem_ano(texto):
                achados.append(novo(f"mes '{mes}' sem 'de 20xx'", t.indice, "lucas"))
    return ResultadoChecagem("D3", aplicavel, achados)


# ================================================================================================
# D4
# ================================================================================================
#: Pedido explicito de falar com alguem da equipe. Depois dele, escalar e' atender o pedido, nao um
#: defeito, em qualquer persona (bateria 5, 13/10/2026: "me passa pra alguem da equipe" contava como
#: escalacao indevida na persona do idoso).
_PEDIDO_EXPLICITO = re.compile(
    r"\b(?:atendente|humano|pessoa de verdade|alguem da (?:sua |nossa )?equipe|alguem da equipe"
    r"|supervisor|gerente|falar com (?:uma |um |alguem)|me passa (?:pra|para) (?:alguem|um|uma|a equipe)"
    r"|passa (?:pra|para) (?:a )?equipe)\b"
)


def _pedido_explicito(texto: str) -> bool:
    return bool(_PEDIDO_EXPLICITO.search(_normal(texto).casefold()))


#: HELENA V4 (DL-0115): a resposta final oferece "1) ... ou 2) falar com a nossa equipe de saude"; a pessoa
#: que escolhe a 2 (o numero, ou "equipe de saude") esta' pedindo a equipe.
_ESCOLHA_DA_EQUIPE = re.compile(r"^\s*(?:a |opcao |numero )?(?:2|dois|segunda)\b|\bequipe de saude\b")


def _pediu_a_equipe(texto: str) -> bool:
    return _pedido_explicito(texto) or bool(_ESCOLHA_DA_EQUIPE.search(_normal(texto).casefold()))


def d4_escalacao(conversa: Conversa, persona: Persona) -> ResultadoChecagem:
    novo = _achar("D4")
    escalacoes = conversa.escalonamentos()
    achados = []
    if persona.escalacao == "nunca":
        pediu = False
        for t in conversa.turnos:
            pediu = pediu or (t.fase == "persona" and _pedido_explicito(t.texto))
            if pediu:
                continue
            for e in t.escalonamentos:
                achados.append(
                    novo(
                        f"escalou ({e.motivo or 'sem motivo'}) numa conversa que nao devia",
                        t.indice,
                        e.agente,
                    )
                )
    elif persona.escalacao == "exigida" and not escalacoes:
        achados.append(novo("persona exige escalacao e nenhum caso teria sido aberto"))
    return ResultadoChecagem("D4", True, achados)


# ================================================================================================
# D5
# ================================================================================================
def d5_emergencia(conversa: Conversa, persona: Persona) -> ResultadoChecagem:
    novo = _achar("D5")
    turnos = [t for t in conversa.turnos_da_persona() if t.tema == "emergencia"]
    if persona.emergencia and not turnos and conversa.turnos_da_persona():
        turnos = [conversa.turnos_da_persona()[0]]
    achados = []
    for t in turnos:
        if not any("192" in texto for texto in t.textos()):
            achados.append(novo("relato de emergencia sem 192 na resposta", t.indice))
    return ResultadoChecagem("D5", bool(turnos), achados)


# ================================================================================================
# D6
# ================================================================================================
def _normal(texto: str) -> str:
    return re.sub(r"\s+", " ", sem_acento(texto)).strip()


def d6_repeticao(conversa: Conversa, persona: Persona) -> ResultadoChecagem:
    novo = _achar("D6")
    achados: list[Achado] = []
    ocorrencias: Counter[str] = Counter()
    anterior: tuple[str, set[str]] | None = None
    for t in conversa.turnos_da_persona():
        respostas = {
            _normal(m.texto)
            for m in t.mensagens
            if m.agente != "acesso" and len(m.texto) >= MIN_CARACTERES_D6
        }
        pergunta = _normal(t.texto)
        for r in respostas:
            ocorrencias[r] += 1
            if ocorrencias[r] == 3:
                achados.append(novo("a mesma resposta pela 3a vez na conversa (laco)", t.indice))
        if anterior is not None:
            pergunta_anterior, respostas_anteriores = anterior
            if pergunta != pergunta_anterior and respostas & respostas_anteriores:
                achados.append(
                    novo("resposta identica a de uma mensagem diferente no turno anterior", t.indice)
                )
        anterior = (pergunta, respostas)
    return ResultadoChecagem("D6", bool(conversa.turnos_da_persona()), achados)


# ================================================================================================
# D7
# ================================================================================================
def d7_helena_coerente(conversa: Conversa, persona: Persona) -> ResultadoChecagem:
    novo = _achar("D7")
    achados = []
    lucas_deu_fatos = False
    aplicavel = False
    for t in conversa.turnos_da_persona():
        for texto in t.textos("helena"):
            aplicavel = True
            if _NEGA_ACESSO.search(sem_acento(texto)):
                causa = "depois de o Lucas dar fatos" if lucas_deu_fatos else "com a conversa verificada"
                achados.append(
                    novo(f"Helena diz que nao tem acesso/nao identifica {causa}", t.indice, "helena")
                )
        if t.textos("lucas") and t.fatos:
            lucas_deu_fatos = True
    return ResultadoChecagem("D7", aplicavel, achados)


# ================================================================================================
# D8
# ================================================================================================
def emojis(texto: str) -> list[str]:
    return [c for c in texto if unicodedata.category(c) == "So" and c not in _NAO_EMOJI]


#: Marcador de item de lista no comeco da linha: bullet, hifen, asterisco ou numeracao ("1." / "2)").
_MARCADOR_DE_ITEM = re.compile(r"^\s*(?:[•·\-*]|\d+[.)])\s+")


def frases_longas(texto: str) -> list[int]:
    """Quantas palavras tem cada frase acima do limite (as frases curtas nao aparecem).

    Frase = o que fica entre fins de frase. Quebra de linha SEMPRE separa (cada item de lista e' a sua
    frase, e um cabecalho que termina em ":" tambem), o marcador do item ("•", "-", "*", "1.") nao conta
    como palavra, e depois `. ! ? : ;` seguidos de espaco ou do fim separam. "R$ 1.234,56" e datas
    ("10.07.2026") nao quebram: ponto so' separa quando vem seguido de espaco/fim.
    """
    longas: list[int] = []
    for linha in texto.splitlines():
        plano = re.sub(r"[?!:;]", ".", _MARCADOR_DE_ITEM.sub("", linha))
        for frase in re.split(r"\.+(?:\s|$)", plano):
            palavras = len(frase.split())
            if palavras > MAX_PALAVRAS_POR_FRASE:
                longas.append(palavras)
    return longas


def problemas_de_tom(texto: str) -> list[str]:
    plano = sem_acento(texto)
    problemas = [f"frase com {n} palavras" for n in frases_longas(texto)]
    lista = emojis(texto)
    if len(lista) > 1:
        problemas.append(f"{len(lista)} emojis")
    if lista:
        tema = next((t for t in _TEMAS_SEM_EMOJI if t in plano), None)
        if tema is not None:
            problemas.append(f"emoji em mensagem sobre '{tema}'")
    canal = _CANAIS_PROIBIDOS.search(plano)
    if canal:
        problemas.append(f"canal fora da lista ('{canal.group(0)}')")
    if "nada foi alterado" in plano:
        problemas.append("'nada foi alterado' no lugar da garantia")
    if "nenhuma decisao" in plano and _FORMA_NENHUMA_DECISAO not in texto:
        problemas.append("garantia de 'nenhuma decisao' fora da forma unica")
    return problemas


def d8_tom(conversa: Conversa, persona: Persona) -> ResultadoChecagem:
    novo = _achar("D8")
    achados = []
    for t in conversa.turnos:
        for m in t.mensagens:
            for problema in problemas_de_tom(m.texto):
                achados.append(novo(problema, t.indice, m.agente))
    return ResultadoChecagem("D8", any(t.mensagens for t in conversa.turnos), achados)


# ================================================================================================
# D9
# ================================================================================================
def d9_sem_eco(conversa: Conversa, persona: Persona) -> ResultadoChecagem:
    novo = _achar("D9")
    achados = []
    for t in conversa.turnos:
        for m in t.mensagens:
            for dado in contem_dado_do_sujeito(m.texto, conversa.sujeito):
                achados.append(novo(f"resposta ecoa {dado} do beneficiario", t.indice, m.agente))
    return ResultadoChecagem("D9", True, achados)


# ================================================================================================
# D10
# ================================================================================================
def palavras_proibidas(texto: str) -> list[str]:
    return sorted({m.group(0) for m in _PROIBIDO_SEM_MOEDA.finditer(sem_acento(texto))})


def d10_proibidas(conversa: Conversa, persona: Persona) -> ResultadoChecagem:
    novo = _achar("D10")
    achados = []
    aplicavel = False
    for t in conversa.turnos:
        for texto in t.textos("lucas"):
            aplicavel = True
            for palavra in palavras_proibidas(texto):
                achados.append(novo(f"palavra proibida '{palavra}'", t.indice, "lucas"))
    return ResultadoChecagem("D10", aplicavel, achados)


# ================================================================================================
# D11
# ================================================================================================
def d11_cobranca_no_lucas(conversa: Conversa, persona: Persona) -> ResultadoChecagem:
    novo = _achar("D11")
    turnos = [t for t in _turnos_respondidos_por_agente(conversa) if t.tema == "cobranca"]
    achados = []
    for t in turnos:
        if t.textos("lucas"):
            continue
        if persona.escalacao == "exigida" and t.escalonamentos:
            continue
        achados.append(novo("pergunta de cobranca sem resposta do Lucas", t.indice, "helena"))
    return ResultadoChecagem("D11", bool(turnos), achados)


# ================================================================================================
# D12
# ================================================================================================
def turno_com_falha_tecnica(t: Turno) -> bool:
    if any(e.motivo == "falha_tecnica" for e in t.escalonamentos):
        return True
    return t.resultado.get("escalation_motivo") == "falha_tecnica"


def d12_falha_tecnica(conversa: Conversa, persona: Persona) -> ResultadoChecagem:
    novo = _achar("D12")
    achados = [
        novo("turno caiu em falha_tecnica", t.indice) for t in conversa.turnos if turno_com_falha_tecnica(t)
    ]
    return ResultadoChecagem("D12", True, achados)


_TODAS: tuple[Callable[[Conversa, Persona], ResultadoChecagem], ...] = (
    d1_resposta,
    d2_fatos,
    d3_mes_com_ano,
    d4_escalacao,
    d5_emergencia,
    d6_repeticao,
    d7_helena_coerente,
    d8_tom,
    d9_sem_eco,
    d10_proibidas,
    d11_cobranca_no_lucas,
    d12_falha_tecnica,
)


# ================================================================================================
# D13 — caso evitavel (nova triagem da Helena, DL-0104)
# ================================================================================================
#: Motivos que NUNCA sao "evitaveis": alarme e risco sao a tabela de seguranca falando (a nova triagem nao
#: mexe neles), e `falha_tecnica` ja' e' contada pela D12.
_MOTIVOS_NUNCA_EVITAVEIS = frozenset({"red_flag_clinico", "risco_psicossocial", "falha_tecnica"})


def d13_caso_evitavel(conversa: Conversa, persona: Persona, *, triagem_v2: bool = False) -> ResultadoChecagem:
    """Com a nova triagem (v2) ou a Helena v4 ligada, um caso aberto numa persona de sintoma leve / duvida de
    saude, sem alarme, sem risco e antes de qualquer pedido explicito de pessoa, e' um caso que uma resposta
    resolveria."""
    novo = _achar("D13")
    if not (triagem_v2 and persona.caso_evitavel_com_triagem_v2):
        return ResultadoChecagem("D13", False, [])
    achados = []
    pediu = False
    for t in conversa.turnos:
        pediu = pediu or (t.fase == "persona" and _pediu_a_equipe(t.texto))
        if pediu:
            continue
        for e in t.escalonamentos:
            if e.motivo in _MOTIVOS_NUNCA_EVITAVEIS:
                continue
            achados.append(novo(f"caso evitavel aberto ({e.motivo or 'sem motivo'})", t.indice, e.agente))
    return ResultadoChecagem("D13", True, achados)


def checar_conversa(
    conversa: Conversa, persona: Persona, *, triagem_v2: bool = False
) -> list[ResultadoChecagem]:
    """D1..D12 sempre; D13 so' se aplica com a nova triagem da Helena (v2) ou a Helena v4 ligada
    (`triagem_v2` quer dizer "alguma das duas")."""
    return [checagem(conversa, persona) for checagem in _TODAS] + [
        d13_caso_evitavel(conversa, persona, triagem_v2=triagem_v2)
    ]


@dataclass(frozen=True)
class Limites:
    latencia_p95_s: float = 30.0
    taxa_falha_tecnica: float = 0.05


def percentil(valores: Sequence[float], p: float) -> float:
    """Percentil pelo metodo do posto mais proximo (sem interpolacao); 0.0 sem valores."""
    if not valores:
        return 0.0
    ordenados = sorted(valores)
    posto = max(1, math.ceil(p / 100 * len(ordenados)))
    return ordenados[posto - 1]


def checar_execucao(conversas: Sequence[Conversa], limites: Limites) -> dict[str, Any]:
    """A parte de EXECUCAO do D12: p95 da latencia por turno e taxa de turnos em falha_tecnica."""
    turnos = [t for c in conversas for t in c.turnos]
    latencias = [t.latencia_s for t in turnos]
    falhas = sum(1 for t in turnos if turno_com_falha_tecnica(t))
    p95 = percentil(latencias, 95)
    taxa = falhas / len(turnos) if turnos else 0.0
    return {
        "turnos": len(turnos),
        "latencia_p50_s": round(percentil(latencias, 50), 2),
        "latencia_p95_s": round(p95, 2),
        "latencia_p95_max_s": limites.latencia_p95_s,
        "latencia_ok": p95 <= limites.latencia_p95_s,
        "turnos_falha_tecnica": falhas,
        "taxa_falha_tecnica": round(taxa, 4),
        "taxa_falha_tecnica_max": limites.taxa_falha_tecnica,
        "falha_tecnica_ok": taxa <= limites.taxa_falha_tecnica,
    }


__all__ = [
    "CHECAGENS",
    "DESCRICOES",
    "Limites",
    "checar_conversa",
    "checar_execucao",
    "datas_sem_fato",
    "emojis",
    "meses_sem_ano",
    "palavras_proibidas",
    "percentil",
    "problemas_de_tom",
    "quantia",
    "quantias_no_texto",
    "quantias_permitidas",
]
