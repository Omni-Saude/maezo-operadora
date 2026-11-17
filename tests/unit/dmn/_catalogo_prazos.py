"""Leitor e cercas do catalogo de prazos normativos do funil (E-SF-08; EP-REG-23a/23b, TB-E-13-b, TB-E-16-a,
EP-REG-G4a).

O catalogo (`spec/processes/dmn/funnel_deadline_catalog.dmn`) e' DADO: uma linha por dispositivo normativo,
todas DRAFT nesta onda. Este modulo NAO decide prazo nenhum e nao roda em producao: ele le a tabela pelo
leitor FIRST-hit de `tests/support/dmn_first_hit.py` e aplica as REGRAS DE LEITURA que a descricao da DMN
declara, devolvendo motivos tipados. As cercas (`test_*.py` vizinhos) afirmam (a) que o catalogo real nao
tem achado nenhum e (b) que cada violacao injetada produz o motivo esperado — se a regra de leitura for
afrouxada ou a tabela ganhar uma linha "ratificada" sem lastro, o teste fica vermelho.

Quando a projecao em `journey.get` existir (campo novo no sucessor do C6-4, casa AMH), a regra de ETA
(`motivo_eta`) vira DMN consumida pelo engine; ate' la ela e' so' cerca.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

from tests.support.dmn_first_hit import DMN_DIR, DecisionTable, Rule, evaluate, read_live_table

CATALOGO: Final[Path] = DMN_DIR / "funnel_deadline_catalog.dmn"
DECISION_ID: Final = "funnel_deadline_catalog"
SAIDAS: Final[tuple[str, ...]] = (
    "dispositivo_citacao",
    "dispositivo_verbatim",
    "verbatim_sha256",
    "entrada_versao",
    "prazo_quantidade",
    "calendario",
    "evento_ancora_ref",
    "regras_suspensao",
    "estagios_tracker",
    "escopo",
    "enquadramento_fonte",
    "assistencial",
    "regulatory_approval_ref",
    "clinical_approval_ref",
    "status",
)

#: sha256 (UTF-8) dos verbatins TRANSCRITOS da baseline normativa do programa — pin independente da DMN:
#: editar verbatim E hash juntos na tabela continua vermelho contra este dicionario.
VERBATIM_SHA256_APROVADO: Final[Mapping[str, str]] = {
    "RN413-art5-par2": "111c8ce00cca97939a5aca936a18d72b168aa3d54eec605be36ebda4a87de598",
    "RN413-art5-par7": "bad8a5fb4e8227987eb2824c82aa4abda81dc877ae54bd8b24dc2abcc5b75d58",
    "Lei9656-art12-VI": "b04e1a73b597f5380110277c8a447bf6e957cf26c2f60259586e8849869a731d",
}

STATUS_VALIDOS: Final = frozenset({"DRAFT", "RATIFIED"})
#: Estagios do tracker sem dispositivo localizado (TB-13: 4-6 DS/analise/matricula; 7 segue TB-15).
ESTAGIOS_SEM_PRAZO: Final = frozenset({"4", "5", "6", "7"})
ESCOPO_CONTRATACAO: Final = "contratacao_eletronica"
ESCOPO_REEMBOLSO_URGENCIA: Final = "reembolso_urgencia_emergencia"
SUSPENSAO_OBRIGATORIA: Final = "RN413-art5-par7"
ANCORA_REEMBOLSO: Final = "documentacao_adequada_em"
ENQUADRAMENTO_CONFIAVEL: Final = "registro_assistencial_do_analista"
#: Inicios de relogio proibidos para o prazo de reembolso (contagem desde a submissao/solicitacao).
INICIOS_PROIBIDOS: Final = frozenset(
    {"data_solicitacao", "data_submissao", "dias_desde_solicitacao", "dias_desde_submissao"}
)
#: Unicas entradas que uma linha de prazo de `reembolso_prazo.dmn` pode ler (alem do catch-all).
ENTRADAS_PRAZO_REEMBOLSO: Final = frozenset({"tipo_reembolso", "dias_desde_documentacao_adequada"})

SEM_ETA_NAO_RATIFICADO: Final = "eta_unavailable:deadline_not_ratified"
SEM_ETA_ANCORA_AUSENTE: Final = "eta_unavailable:anchor_not_observed"

#: Duracao ISO 8601 (P5D, P30D, PT4H, PT30M, P1Y2M...).
_ISO_DURACAO: Final = re.compile(r"\bP(?=\d|T\d)(?:\d+[YMWD])*(?:T(?:\d+[HMS])+)?\b")
#: RN 623 art. 12 par. 5o — rotulos genericos (comparados sobre texto dobrado: minusculo, sem acento).
_ROTULO_GENERICO: Final = re.compile(r"\bem (analise|processamento|auditoria)\b")
#: Promessa de prazo em texto livre ("em ate 30 dias", "em 5 dias uteis", "48 horas").
_PROMESSA_DE_PRAZO: Final = re.compile(r"\b\d+\s*(dias?|horas?|semanas?)\b")

Motivo = tuple[str, str]  # (onde, codigo)


@dataclass(frozen=True, slots=True)
class LinhaCatalogo:
    regra: str
    deadline_ref: str  # literal da entrada; "" no catch-all
    dispositivo_citacao: str
    dispositivo_verbatim: str
    verbatim_sha256: str
    entrada_versao: int
    prazo_quantidade: int
    calendario: str
    evento_ancora_ref: str
    regras_suspensao: str
    estagios_tracker: str
    escopo: str
    enquadramento_fonte: str
    assistencial: bool
    regulatory_approval_ref: str
    clinical_approval_ref: str
    status: str


def ler_catalogo() -> DecisionTable:
    tabela = read_live_table(CATALOGO)
    assert tabela.decision_id == DECISION_ID
    assert tabela.input_names == ("deadline_ref",)
    assert tabela.output_names == SAIDAS
    return tabela


def _linha(regra: Rule, nomes: tuple[str, ...]) -> LinhaCatalogo:
    (entrada,) = regra.inputs
    ref = "" if entrada == "-" else entrada.strip('"')
    return LinhaCatalogo(
        regra=regra.rule_id, deadline_ref=ref, **dict(zip(nomes, regra.outputs, strict=True))
    )


def linhas(tabela: DecisionTable | None = None) -> tuple[LinhaCatalogo, ...]:
    tabela = tabela if tabela is not None else ler_catalogo()
    return tuple(_linha(regra, tabela.output_names) for regra in tabela.rules)


def linha_para(deadline_ref: str) -> LinhaCatalogo:
    """Avalia o catalogo REAL (FIRST-hit) para `deadline_ref` e devolve a linha que casou."""
    tabela = ler_catalogo()
    veredito = evaluate(tabela, {"deadline_ref": deadline_ref})
    (regra,) = (r for r in tabela.rules if r.rule_id == veredito.regra)
    return _linha(regra, tabela.output_names)


def status_efetivo(linha: LinhaCatalogo) -> Literal["DRAFT", "RATIFIED"]:
    """Sem `regulatory_approval_ref` le como DRAFT; assistencial exige tambem `clinical_approval_ref`."""
    if linha.status != "RATIFIED" or not linha.regulatory_approval_ref:
        return "DRAFT"
    if linha.assistencial and not linha.clinical_approval_ref:
        return "DRAFT"
    return "RATIFIED"


def motivo_eta(linha: LinhaCatalogo, *, ancora_observada: bool) -> str | None:
    """Regra de ETA (REG-23, decisao tecnica 1): None = prazo exibivel; senao o motivo de `eta=null`."""
    if status_efetivo(linha) != "RATIFIED":
        return SEM_ETA_NAO_RATIFICADO
    if not linha.evento_ancora_ref or not ancora_observada:
        return SEM_ETA_ANCORA_AUSENTE
    return None


def _estagios(linha: LinhaCatalogo) -> set[str]:
    return {e.strip() for e in linha.estagios_tracker.split(",") if e.strip()}


def auditar_catalogo(todas: Iterable[LinhaCatalogo]) -> list[Motivo]:
    """Achados tipados do catalogo. Catalogo conforme = lista vazia."""
    achados: list[Motivo] = []
    for linha in todas:
        r = linha.regra
        if linha.status not in STATUS_VALIDOS:
            achados.append((r, "deadline_status_unknown"))
        if linha.status == "RATIFIED" and not linha.regulatory_approval_ref:
            achados.append((r, "deadline_unratified"))
        if linha.status == "RATIFIED" and linha.assistencial and not linha.clinical_approval_ref:
            achados.append((r, "deadline_clinical_unratified"))
        if linha.deadline_ref:
            calculado = hashlib.sha256(linha.dispositivo_verbatim.encode("utf-8")).hexdigest()
            pin = VERBATIM_SHA256_APROVADO.get(linha.deadline_ref)
            if not linha.dispositivo_verbatim or calculado != linha.verbatim_sha256 or calculado != pin:
                achados.append((r, "deadline_verbatim_mismatch"))
        elif linha.dispositivo_verbatim or linha.verbatim_sha256 or linha.prazo_quantidade >= 0:
            achados.append((r, "deadline_verbatim_mismatch"))  # catch-all nunca carrega prazo nem verbatim
        if linha.status == "RATIFIED" and not linha.evento_ancora_ref:
            achados.append((r, "DEADLINE_WITHOUT_START_EVENT"))
        if _estagios(linha) & ESTAGIOS_SEM_PRAZO:
            achados.append((r, "DEADLINE_NOT_ANCHORED"))
        if (
            linha.escopo == ESCOPO_CONTRATACAO
            and linha.calendario == "corridos"
            and SUSPENSAO_OBRIGATORIA not in linha.regras_suspensao.split(";")
        ):
            achados.append((r, "SUSPENSION_IGNORED"))
        if linha.escopo.startswith("reembolso_"):
            if linha.escopo != ESCOPO_REEMBOLSO_URGENCIA:
                achados.append((r, "REFUND_DEADLINE_WITHOUT_ANCHOR"))
            if linha.evento_ancora_ref != ANCORA_REEMBOLSO:
                achados.append((r, "REFUND_CLOCK_START_INVALID"))
            if linha.enquadramento_fonte != ENQUADRAMENTO_CONFIAVEL:
                achados.append((r, "emergency_flag_untrusted_source"))
    return achados


def auditar_reembolso_prazo(tabela: DecisionTable) -> list[Motivo]:
    """Cerca de `reembolso_prazo.dmn`: linha que devolve prazo (!= NAO_AVALIADO) so' em urgencia/emergencia
    e so' contando a partir da documentacao adequada."""
    achados: list[Motivo] = []
    i_situacao = tabela.output_names.index("situacao_prazo")
    for regra in tabela.rules:
        if regra.outputs[i_situacao] == "NAO_AVALIADO":
            continue
        entradas = dict(zip(tabela.input_names, regra.inputs, strict=True))
        if entradas.get("tipo_reembolso") != '"urgencia_emergencia"':
            achados.append((regra.rule_id, "REFUND_DEADLINE_WITHOUT_ANCHOR"))
        lidas = {nome for nome, entrada in entradas.items() if entrada.strip() != "-"}
        if lidas & INICIOS_PROIBIDOS or lidas - ENTRADAS_PRAZO_REEMBOLSO:
            achados.append((regra.rule_id, "REFUND_CLOCK_START_INVALID"))
    return achados


def _dobrar(texto: str) -> str:
    decomposto = unicodedata.normalize("NFKD", texto.casefold())
    return "".join(c for c in decomposto if not unicodedata.combining(c))


def duracoes_draft_internas() -> frozenset[str]:
    """Toda duracao ISO 8601 que as tabelas de SLA interno DRAFT devolvem (nunca projetavel ao comprador)."""
    duracoes: set[str] = set()
    for nome in ("cancel_sla.dmn", "reembolso_sla.dmn", "escalation_routing.dmn"):
        for regra in read_live_table(DMN_DIR / nome).rules:
            duracoes.update(o for o in regra.outputs if isinstance(o, str) and _ISO_DURACAO.fullmatch(o))
    return frozenset(duracoes)


def auditar_textos_ao_comprador(
    textos: Mapping[str, str], *, verbatim: frozenset[str] = frozenset()
) -> list[Motivo]:
    """Texto que chega ao interessado/beneficiario: sem duracao ISO, sem duracao DRAFT, sem promessa de prazo
    fora de verbatim normativo, sem rotulo generico (RN 623 art. 12 par. 5o)."""
    achados: list[Motivo] = []
    draft = duracoes_draft_internas()
    for onde, texto in textos.items():
        promessa = texto not in verbatim and _PROMESSA_DE_PRAZO.search(_dobrar(texto)) is not None
        if _ISO_DURACAO.search(texto) or any(d in texto for d in draft) or promessa:
            achados.append((onde, "DRAFT_DEADLINE_DISCLOSED"))
        if _ROTULO_GENERICO.search(_dobrar(texto)):
            achados.append((onde, "generic_status_label_refused"))
    return achados


def textos_do_catalogo(todas: Iterable[LinhaCatalogo]) -> dict[str, str]:
    textos: dict[str, str] = {}
    for linha in todas:
        textos[f"{linha.regra}.dispositivo_citacao"] = linha.dispositivo_citacao
        textos[f"{linha.regra}.dispositivo_verbatim"] = linha.dispositivo_verbatim
    return textos


def textos_do_assistente_de_reembolso() -> dict[str, str]:
    """Todo `TEXTO_*` (str) do assistente de reembolso pelo WhatsApp — o que chega ao beneficiario."""
    from maezo.domain.reembolso import pedido

    return {
        nome: valor
        for nome, valor in vars(pedido).items()
        if nome.startswith("TEXTO_") and isinstance(valor, str)
    }
