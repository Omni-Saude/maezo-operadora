#!/usr/bin/env python3
"""Gera a PLANILHA DE REVISAO CLINICA do escalonamento da Helena (SP-OP-ESCALATION-001) a partir do repo.

O QUE E'. O pacote que o medico-auditor (e, na aba de SLAs e na do caso paralelo, o regulatorio) recebe
para so' CONFIRMAR, CORRIGIR e ASSINAR. Toda regra vem dos arquivos — nada e' digitado a mao:

  * as tabelas `spec/processes/dmn/triage_redflag_{adult,gestante,mental_health,pediatric}.dmn` e
    `triage_sufficiency.dmn` sao lidas como XML (uma linha da planilha por `<rule>`, na ordem da tabela,
    que e' a ordem que decide: hit policy FIRST);
  * o alarme por texto de `src/maezo/agents/helena/alarme_deterministico.py` (DL-0123) e' lido com `ast`
    (uma linha por `if ...: return ALARME_*` de `alarme_no_relato`); cada expressao regular usada
    precisa de uma descricao legivel em `_GLOSSARIO_ALARME` — expressao nova sem descricao REPROVA a
    geracao, em vez de sair uma linha que o medico nao consegue ler;
  * prazos e grupos vem de `escalation_routing.dmn` e do BPMN do processo.

O QUE E' DIGITADO A MAO, e por que: a SUGESTAO de resposta e a justificativa (`_SUGESTOES`). Sao o
"rascunho do parecer" para o medico corrigir. Cada sugestao tem uma GUARDA (um trecho da condicao que
ela pressupoe): se a regra mudar e a guarda nao casar mais, a celula sai "SUGESTAO DESATUALIZADA" em vez
de recomendar algo sobre uma regra que ja' nao e' aquela. A sugestao NAO e' parecer medico.

O QUE ESTE SCRIPT NAO FAZ: nao aprova, nao assina, nao preenche nome/data/veredito de ninguem (as
colunas de veredito saem VAZIAS), nao altera DMN/BPMN. Nao sobrescreve planilha existente.

Uso (precisa de `openpyxl`, que nao e' dependencia do projeto: use um Python que ja' o tenha):

    python scripts/sme/revisao_clinica.py --destino "<pasta>"

A volta (planilha preenchida -> YAMLs de ratificacao, sem aplicar) e' `revisao_clinica_para_yaml.py`.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import importlib
import re
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Final

RAIZ_REPO: Final[Path] = Path(__file__).resolve().parents[2]
DESTINO_PADRAO: Final[Path] = Path("D:/OneDrive - Dafe/DAFE/Austa/Projetos/Automação 2026/Maezo Operadora")
PREFIXO_ARQUIVO: Final[str] = "Revisao_Clinica_Helena_Escalonamento"

DMN_DIR: Final[str] = "spec/processes/dmn"
TABELAS_DE_ALARME: Final[tuple[str, ...]] = (
    "triage_redflag_adult",
    "triage_redflag_gestante",
    "triage_redflag_mental_health",
    "triage_redflag_pediatric",
)
TABELA_SUFICIENCIA: Final[str] = "triage_sufficiency"
TABELAS_REVISADAS: Final[tuple[str, ...]] = (*TABELAS_DE_ALARME, TABELA_SUFICIENCIA)
ROTEAMENTO: Final[str] = f"{DMN_DIR}/escalation_routing.dmn"
BPMN_ESCALONAMENTO: Final[str] = (
    "spec/processes/bpmn/SP-OP-ESCALATION-001_Escalonamento_Humano_Universal.bpmn"
)
CONTRATO: Final[str] = "docs/processes/contracts/SP-OP-ESCALATION-001.md"
GRAFO_HELENA: Final[str] = "src/maezo/agents/helena/graph.py"
ALARME_CODIGO: Final[str] = "src/maezo/agents/helena/alarme_deterministico.py"
TABELA_DO_ALARME: Final[str] = "alarme_deterministico (código, DL-0123)"

# --- abas e colunas (o conversor le' pelos MESMOS nomes) -------------------------------------------------
ABA_LEIA_ME: Final[str] = "Leia-me"
ABA_REGRAS: Final[str] = "Regras de alarme"
ABA_SLAS: Final[str] = "SLAs e grupos"
ABA_PARALELO: Final[str] = "Caso paralelo"
ABA_ADR: Final[str] = "ADR-0059"
ABA_PENDENCIAS: Final[str] = "Pendências do dono clínico"

COL_TABELA: Final[str] = "Tabela"
COL_REGRA: Final[str] = "Regra (id)"
COL_QUANDO: Final[str] = "Quando (condições)"
COL_ENTAO: Final[str] = "Então (saída)"
COL_INTERINA: Final[str] = "Interina? (e DL)"
COL_SUGESTAO: Final[str] = "Sugestão de resposta (pré-preenchida — NÃO é parecer médico)"
COL_POR_QUE: Final[str] = "Por que sugerimos (evidência)"
COL_VEREDITO: Final[str] = "Veredito do médico"
COL_AJUSTE: Final[str] = "Ajuste proposto pelo médico"
COL_COMENTARIO: Final[str] = "Comentário"
COL_ORDEM: Final[str] = "Ordem na tabela"
COL_NOTA: Final[str] = "Nota da própria tabela"
COL_ARQUIVO: Final[str] = "Arquivo"
COL_SHA: Final[str] = "sha256 (LF) do arquivo na geração"

COLUNAS_REGRAS: Final[tuple[tuple[str, int], ...]] = (
    (COL_TABELA, 24),
    (COL_REGRA, 14),
    (COL_QUANDO, 48),
    (COL_ENTAO, 44),
    (COL_INTERINA, 26),
    (COL_SUGESTAO, 52),
    (COL_POR_QUE, 70),
    (COL_VEREDITO, 16),
    (COL_AJUSTE, 40),
    (COL_COMENTARIO, 40),
    (COL_ORDEM, 9),
    (COL_NOTA, 60),
    (COL_ARQUIVO, 44),
    (COL_SHA, 20),
)
VEREDITO_CONFIRMO: Final[str] = "Confirmo"
VEREDITOS: Final[tuple[str, ...]] = (VEREDITO_CONFIRMO, "Alterar", "Perigoso")
SEM_BASE: Final[str] = "Sem base suficiente: decisão do médico."
DESATUALIZADA: Final[str] = "SUGESTÃO DESATUALIZADA"

# Rotulos da aba de pendencias que o conversor le' (o medico preenche; o script nunca).
PEND_REVISOR: Final[str] = "Nome completo do médico-auditor (revisor)"
PEND_CRM: Final[str] = "CRM / UF"
PEND_CAPACIDADE: Final[str] = "Capacidade (como deve constar na ratificação)"
PEND_DATA: Final[str] = "Data da revisão (AAAA-MM-DD)"
PEND_DONO: Final[str] = "Dono clínico — owners.clinico (Nome <e-mail>)"
PEND_REG_NOME: Final[str] = "Nome completo do revisor regulatório"
PEND_REG_DATA: Final[str] = "Data da revisão regulatória (AAAA-MM-DD)"


def sha256_lf(caminho: Path) -> str:
    """O MESMO hash de `scripts/ci/check_aprovacao_de_regras.py::sha256_normalizado` (CRLF -> LF), e o de
    `tabela_viva.sha256` da ratificacao neonatal: o checkout Windows e o do CI dao o mesmo valor."""
    return hashlib.sha256(caminho.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


# =========================================================================================================
# DMN
# =========================================================================================================

_NS: Final[dict[str, str]] = {"d": "https://www.omg.org/spec/DMN/20191111/MODEL/"}
_DL_RE: Final[re.Pattern[str]] = re.compile(r"DL-\d{4}")


@dataclass(frozen=True)
class RegraDmn:
    id: str
    descricao: str
    entradas: tuple[str, ...]
    saidas: tuple[str, ...]


@dataclass(frozen=True)
class TabelaDmn:
    decisao: str
    caminho: str
    descricao: str
    variaveis: tuple[str, ...]
    saidas: tuple[str, ...]
    regras: tuple[RegraDmn, ...]


def _texto(elem: ET.Element | None) -> str:
    return " ".join((elem.text or "").split()) if elem is not None else ""


def ler_dmn(raiz: Path, relativo: str) -> TabelaDmn:
    arvore = ET.parse(raiz / relativo)
    decisao = arvore.getroot().find("d:decision", _NS)
    if decisao is None:
        raise ValueError(f"{relativo}: sem <decision>")
    dt = decisao.find("d:decisionTable", _NS)
    if dt is None:
        raise ValueError(f"{relativo}: sem <decisionTable>")
    variaveis = tuple(_texto(i.find("d:inputExpression/d:text", _NS)) for i in dt.findall("d:input", _NS))
    saidas = tuple(o.get("name", "") for o in dt.findall("d:output", _NS))
    regras = tuple(
        RegraDmn(
            id=r.get("id", ""),
            descricao=_texto(r.find("d:description", _NS)),
            entradas=tuple(_texto(e.find("d:text", _NS)) for e in r.findall("d:inputEntry", _NS)),
            saidas=tuple(_texto(e.find("d:text", _NS)) for e in r.findall("d:outputEntry", _NS)),
        )
        for r in dt.findall("d:rule", _NS)
    )
    return TabelaDmn(
        decisao=decisao.get("id", ""),
        caminho=relativo,
        descricao=_texto(arvore.getroot().find("d:description", _NS)),
        variaveis=variaveis,
        saidas=saidas,
        regras=regras,
    )


def _valor_legivel(texto: str) -> str:
    """`"grave","moderada"` -> `grave, moderada`; `>= 65` -> `≥ 65`; literal -> sem aspas."""
    t = texto.replace("&gt;", ">").replace("&lt;", "<")
    t = t.replace(">=", "≥").replace("<=", "≤")
    partes = [p.strip().strip('"') for p in t.split(",")] if t.startswith('"') else [t]
    return ", ".join(partes)


def condicoes_legiveis(tabela: TabelaDmn, regra: RegraDmn) -> str:
    partes: list[str] = []
    for var, entrada in zip(tabela.variaveis, regra.entradas, strict=True):
        if entrada in ("", "-"):
            continue
        valor = _valor_legivel(entrada)
        if entrada.startswith('"') and "," in entrada:
            partes.append(f"{var} ∈ {{{valor}}}")
        elif entrada[:1] in "<>":
            partes.append(f"{var} {valor}")
        else:
            partes.append(f"{var} = {valor}")
    return "; ".join(partes) if partes else "(qualquer caso — linha final/fallback da tabela)"


def saida_legivel(tabela: TabelaDmn, regra: RegraDmn) -> str:
    return "; ".join(
        f"{nome} = {_valor_legivel(valor)}" for nome, valor in zip(tabela.saidas, regra.saidas, strict=True)
    )


def _situacao(tabela: TabelaDmn, regra: RegraDmn) -> str:
    dls = sorted(set(_DL_RE.findall(regra.descricao)))
    sufixo = f" ({', '.join(dls)})" if dls else ""
    desc = regra.descricao.upper()
    if "INTERINO" in desc:
        return f"Sim — INTERINA{sufixo}, pendente do dono clínico"
    if "RATIFICADA PELO DONO" in desc:
        return f"Ratificada só pelo dono, não por médico{sufixo}"
    if "ABERTA A RATIFICACAO" in desc:
        return "Linha ABERTA ao médico (tabela não ratificada)"
    if "NAO RATIFICADO" in tabela.descricao.upper():
        return "Tabela inteira NÃO RATIFICADA"
    return f"Não (conteúdo clínico DRAFT, sem ratificação){sufixo}"


# =========================================================================================================
# Alarme por texto (codigo, DL-0123)
# =========================================================================================================

#: Descricao legivel de cada expressao regular usada por `alarme_no_relato`. A geracao REPROVA se o
#: codigo usar uma expressao que nao esta aqui (o medico nao le regex).
_GLOSSARIO_ALARME: Final[dict[str, str]] = {
    "_GESTACAO_NO_TEXTO": "o texto cita gestação (grávida, gestante, gravidez, semanas de gestação, "
    "bebê na barriga)",
    "_OBSTETRICO": "sinal obstétrico que dispensa a palavra 'grávida' (bolsa rompeu/estourou/vazando; "
    "contrações regulares, ritmadas, 'de X em X minutos' ou antes do tempo; não sente o bebê mexer, bebê "
    "parou de mexer/mexe menos)",
    "_OBSTETRICO_COM_GESTACAO": "perda/escorrimento de líquido, sangramento, contração, dor de cabeça com "
    "alteração da visão ou inchaço no rosto/mãos",
    "_PRESSAO_ALTA_COM_SINAL": "'pressão alta / subiu / muito alta / nas alturas'",
    "_SINAL_DE_LESAO": "sinal de lesão (visão embaçada/turva, dor no peito, falta de ar, confusão, "
    "fraqueza ou formigamento de um lado, boca torta, fala enrolada, desmaio, dor de cabeça forte)",
    "_CRIANCA": "o relato é de criança (meu filho/filha, bebê, neném, neto/a, sobrinho/a, enteado/a, "
    "'filho de N anos/meses')",
    "_FEBRE": "febre ('febre', 'fervendo', 'quente demais')",
    "_PROSTRADA": "prostração (molinho, mole, prostrado, sonolento, não acorda, difícil de acordar, "
    "largado, sem reação, letárgico, apático, abatido)",
    "_INTOXICACAO": "ingestão de comprimidos/remédio/veneno/produto de limpeza/pilha/bateria/moeda, ou "
    "'tomou um monte / cartela / caixa inteira' de remédio, ou chumbinho/água sanitária/soda cáustica",
    "_ALERGIA_GRAVE": "inchaço de boca, garganta, língua, lábios, rosto ou olhos ('fechando a garganta')",
    "_ABDOME_DIREITO": "dor na barriga do lado direito",
    "_PIORANDO": "piora ('piorando', 'cada vez mais', 'insuportável', 'muito forte', 'faz N horas')",
}
_ROTULO_CHAMADA: Final[dict[str, str]] = {
    "pressao_relatada": "pressão dita no texto (mmHg; '18 por 11' = 180/110)",
}
_INDICE_PRESSAO: Final[dict[int, str]] = {0: "sistólica", 1: "diastólica"}
_OPERADOR: Final[dict[type[ast.cmpop], str]] = {
    ast.GtE: "≥",
    ast.Gt: ">",
    ast.LtE: "≤",
    ast.Lt: "<",
    ast.Eq: "=",
}


class _Renderizador:
    """Traduz a condicao de um `if` de `alarme_no_relato` em portugues. No que nao conhece, levanta."""

    def __init__(self, atribuicoes: dict[str, ast.expr]) -> None:
        self.atribuicoes = atribuicoes
        self.regex_usadas: set[str] = set()

    def __call__(self, no: ast.expr) -> str:
        if isinstance(no, ast.BoolOp):
            juntor = " E " if isinstance(no.op, ast.And) else " OU "
            return juntor.join(self._parenteses(v) for v in no.values)
        if isinstance(no, ast.Call):
            return self._chamada(no)
        if isinstance(no, ast.Compare):
            return self._comparacao(no)
        if isinstance(no, ast.Name) and no.id in self.atribuicoes:
            return self(self.atribuicoes[no.id])
        if isinstance(no, ast.Constant):
            return str(no.value)
        raise ValueError(f"alarme: expressao nao suportada na planilha: {ast.unparse(no)}")

    def _parenteses(self, no: ast.expr) -> str:
        if isinstance(no, ast.Name) and no.id in self.atribuicoes:
            no = self.atribuicoes[no.id]
        texto = self(no)
        return f"({texto})" if isinstance(no, ast.BoolOp) else texto

    def _chamada(self, no: ast.Call) -> str:
        func = no.func
        if isinstance(func, ast.Name) and func.id == "bool" and len(no.args) == 1:
            return self(no.args[0])
        if isinstance(func, ast.Name) and func.id in _ROTULO_CHAMADA:
            return _ROTULO_CHAMADA[func.id]
        if isinstance(func, ast.Attribute) and func.attr == "search" and isinstance(func.value, ast.Name):
            nome = func.value.id
            if nome not in _GLOSSARIO_ALARME:
                raise ValueError(f"alarme: a expressao {nome} nao tem descricao em _GLOSSARIO_ALARME")
            self.regex_usadas.add(nome)
            return f"[{_GLOSSARIO_ALARME[nome]}]"
        raise ValueError(f"alarme: chamada nao suportada na planilha: {ast.unparse(no)}")

    def _comparacao(self, no: ast.Compare) -> str:
        if len(no.ops) != 1:
            raise ValueError(f"alarme: comparacao encadeada nao suportada: {ast.unparse(no)}")
        op, direita = no.ops[0], no.comparators[0]
        if isinstance(op, ast.IsNot) and isinstance(direita, ast.Constant) and direita.value is None:
            return f"{self(no.left)} (presente)"
        esquerda = no.left
        if (
            isinstance(esquerda, ast.Subscript)
            and isinstance(esquerda.slice, ast.Constant)
            and isinstance(esquerda.slice.value, int)
            and esquerda.slice.value in _INDICE_PRESSAO
        ):
            lado = _INDICE_PRESSAO[esquerda.slice.value]
        else:
            lado = self(esquerda)
        simbolo = _OPERADOR.get(type(op))
        if simbolo is None:
            raise ValueError(f"alarme: operador nao suportado: {ast.unparse(no)}")
        return f"{lado} {simbolo} {self(direita)}"


@dataclass(frozen=True)
class RegraAlarme:
    id: str
    categoria: str
    quando: str
    populacao: str


def ler_alarme(raiz: Path) -> tuple[list[RegraAlarme], set[str]]:
    """As regras de `alarme_no_relato`, na ordem do codigo, e as expressoes regulares que elas usam."""
    arvore = ast.parse((raiz / ALARME_CODIGO).read_text(encoding="utf-8"))
    constantes: dict[str, str] = {}
    populacao: dict[str, str] = {}
    funcao: ast.FunctionDef | None = None
    for no in arvore.body:
        if isinstance(no, ast.AnnAssign) and isinstance(no.target, ast.Name) and no.value is not None:
            if isinstance(no.value, ast.Constant) and no.target.id.startswith("ALARME_"):
                constantes[no.target.id] = str(no.value.value)
            if no.target.id == "POPULACAO_DO_ALARME" and isinstance(no.value, ast.Dict):
                for k, v in zip(no.value.keys, no.value.values, strict=True):
                    if isinstance(k, ast.Name) and isinstance(v, ast.Constant):
                        populacao[k.id] = str(v.value)
        if isinstance(no, ast.FunctionDef) and no.name == "alarme_no_relato":
            funcao = no
    if funcao is None:
        raise ValueError(f"{ALARME_CODIGO}: alarme_no_relato nao encontrada")
    atribuicoes: dict[str, ast.expr] = {}
    render = _Renderizador(atribuicoes)
    regras: list[RegraAlarme] = []
    vistos: Counter[str] = Counter()
    for stmt in funcao.body:
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name):
            atribuicoes[stmt.targets[0].id] = stmt.value
            continue
        if not isinstance(stmt, ast.If):
            continue
        retorno = stmt.body[0] if len(stmt.body) == 1 else None
        if not (isinstance(retorno, ast.Return) and isinstance(retorno.value, ast.Name)):
            raise ValueError(f"{ALARME_CODIGO}: if sem 'return ALARME_*' unico: {ast.unparse(stmt.test)}")
        nome = retorno.value.id
        categoria = constantes[nome]
        vistos[categoria] += 1
        regras.append(
            RegraAlarme(
                id=f"{categoria}-{vistos[categoria]}",
                categoria=categoria,
                quando=render(stmt.test),
                populacao=populacao.get(nome, ""),
            )
        )
    return regras, render.regex_usadas


# =========================================================================================================
# Sugestoes (o rascunho do parecer — o medico corrige)
# =========================================================================================================


@dataclass(frozen=True)
class Sugestao:
    veredito: str  # "Confirmo" | "Alterar" | SEM_BASE
    texto: str
    por_que: str
    guarda: str  # trecho da condicao que a sugestao pressupoe


_REL: Final[str] = "Relatório 14/09 (docs/reports/revisao-das-34-regras-14-09-2026.md)"
_P1: Final[str] = f"{_REL}, padrão 1 ('a rede de segurança só pega grave; moderada é o buraco')"
_P2: Final[str] = f"{_REL}, padrão 2 (gestante: sem a idade gestacional a regra não dispara)"
_MANCH: Final[str] = "protocolos de classificação de risco usuais (ex.: Sistema Manchester)"
_NICE: Final[str] = "diretriz de febre pediátrica NICE NG143 (sinais 'vermelhos')"
_NEONATAL: Final[str] = (
    "Ratificada em 14/10/2026 SÓ pelo dono (DL-0101; triage-redflag-pediatric-neonatal-ratification.yaml): "
    "'não é ratificação médica; recomenda-se revisão do médico-auditor'. Recém-nascido (< 28 dias) com "
    "sinal de sofrimento é emergência neonatal nos protocolos usuais (AIDPI Neonatal/Ministério da Saúde)."
)
_CATCH_ALL: Final[Sugestao] = Sugestao(
    VEREDITO_CONFIRMO,
    "Manter: a tabela (hit policy FIRST) precisa de uma linha final; o que não escala segue para a coleta "
    "(triage_sufficiency) e para as perguntas de alerta.",
    "Sem linha final o motor devolve vazio e o turno vira falha técnica (comentário da suf_r7). Os buracos "
    "que chegam ao catch-all estão tratados nas sugestões das linhas específicas e da rede de segurança.",
    "qualquer caso",
)

_SUGESTOES: Final[dict[tuple[str, str], Sugestao]] = {
    # ---------------------------------------------------------------- adulto
    ("triage_redflag_adult", "r1"): Sugestao(
        VEREDITO_CONFIRMO,
        "Manter P1/emergência para dor torácica em qualquer intensidade.",
        f"Dor torácica é discriminador de prioridade máxima nos {_MANCH}; síndrome coronariana aguda é "
        f"tempo-dependente. {_REL}, r1: 'conservadora por desenho'. Nenhuma DL rebaixou.",
        "dor_toracica",
    ),
    ("triage_redflag_adult", "r2"): Sugestao(
        VEREDITO_CONFIRMO,
        "Tornar definitiva a interina DL-0068: falta de ar de qualquer intensidade (inclusive 'leve') em P1.",
        f"DL-0068 (caso C06): quem diz 'só um pouquinho' de falta de ar é quem mais subestima. {_REL}, r2: "
        "dispneia leve não escalava (lacuna C2) e a Helena não conhece histórico cardíaco/respiratório. "
        "Custo conhecido (DL-0068): mais P1 no plantão. Se o médico preferir, 'leve' pode ir a P2 — nunca "
        "sair sem bandeira.",
        "dispneia",
    ),
    ("triage_redflag_adult", "r3"): Sugestao(
        VEREDITO_CONFIRMO,
        "Manter.",
        f"AVC é tempo-dependente; déficit focal agudo é emergência em qualquer protocolo. {_REL}, r3: "
        "'tempo-dependente, sem gate. Correto'.",
        "deficit_neurologico",
    ),
    ("triage_redflag_adult", "r4"): Sugestao(
        VEREDITO_CONFIRMO,
        "Manter. O risco medido é de EXTRAÇÃO ('dor de cabeça' comum virar este código), não da regra.",
        f"Cefaleia súbita 'a pior da vida' é o sinal clássico de hemorragia subaracnoide. {_REL}, r4: 'a "
        "regra está certa; o problema medido em 13/09 foi de extração'. ADR-0059 D2 trata código fora da "
        "população.",
        "cefaleia_subita_intensa",
    ),
    ("triage_redflag_adult", "r5"): Sugestao(
        "Alterar",
        "Aceitar também intensidade 'desconhecida' em P1 e criar linha para 'leve' em P2 (enfermagem), para "
        "que nenhum sangramento ativo saia sem bandeira.",
        f"{_P1}: 'sangramento ativo leve' sai sem bandeira. A DL-0067 aplicou 'intensidade não informada "
        "escala' a três regras e deixou esta 'intocada' por escopo, não por decisão clínica. Na dúvida, "
        "escalar mais.",
        "sangramento_ativo",
    ),
    ("triage_redflag_adult", "r6"): Sugestao(
        "Alterar",
        "Aceitar 'moderada' e 'desconhecida' (além de 'grave') em P1.",
        f"{_REL}, r6: 'o caso mais discutível da tabela: anafilaxia progride' — reação moderada não casa "
        "nenhuma regra. A DL-0123 (item 5) já escala PELO TEXTO o inchaço de boca/garganta/língua: a tabela "
        "ficou mais restrita que o código. À distância não se gradua progressão.",
        "reacao_alergica",
    ),
    ("triage_redflag_adult", "r7a"): Sugestao(
        VEREDITO_CONFIRMO,
        "Manter P1 para síncope relatada como grave.",
        "Caso B03 (02/10/2026): o resultado variava com a leitura do classificador. Síncope com gravidade é "
        f"emergência nos {_MANCH}.",
        "sincope",
    ),
    ("triage_redflag_adult", "r7"): Sugestao(
        "Alterar",
        "Subir para P1 (emergência) em qualquer intensidade. Se o médico entender que enfermagem basta, "
        "manter P2 — nunca abaixo disso.",
        f"{_REL}, r7: 'síncope em adulto pode ser cardíaca; P2 em vez de P1 é decisão clínica explícita — "
        f"confirmar'. Perda de consciência é discriminador de alta prioridade nos {_MANCH}; à distância não "
        "há ECG nem sinais vitais.",
        "sincope",
    ),
    ("triage_redflag_adult", "r8"): Sugestao(
        VEREDITO_CONFIRMO,
        "Tornar definitiva a interina DL-0067 (inclui intensidade desconhecida). Ver também a suf_r6 (idade "
        "não é perguntada ao adulto).",
        f"DL-0067 (caso E02): idoso com febre tem risco de sepse e raramente mede o degrau. {_REL}, r8: "
        "degrau duro em 65 e dependência da idade na mensagem. Incluir 'leve': sem base suficiente — "
        "decisão do médico.",
        "febre",
    ),
    ("triage_redflag_adult", "r9"): Sugestao(
        "Alterar",
        "Aceitar 'moderada' e 'desconhecida' (P2, enfermagem).",
        f"{_P1}: dor abdominal moderada sai sem bandeira. A DL-0123 já escala pelo texto a dor do lado "
        "direito piorando (P1). Dor abdominal moderada pode ser abdome agudo no início.",
        "dor_abdominal",
    ),
    ("triage_redflag_adult", "r10"): Sugestao(
        "Alterar",
        "Rede de segurança: aceitar também 'moderada' (P2, enfermagem).",
        f"{_P1}: trocar 'grave' por 'grave','moderada' é uma linha por tabela; o custo é mais P2 de caso "
        "banal — o lado seguro. A suf_r3 pergunta a intensidade quando ela não é dita.",
        "intensidade = grave",
    ),
    ("triage_redflag_adult", "r11"): _CATCH_ALL,
    # ---------------------------------------------------------------- gestante
    ("triage_redflag_gestante", "r1"): Sugestao(
        VEREDITO_CONFIRMO,
        "Manter P1, sem depender de semanas.",
        f"Sangramento vaginal na gestação é sinal de alarme obstétrico. {_REL}: 'único P1 sem gate de "
        "semanas. Correto'.",
        "sangramento_vaginal",
    ),
    ("triage_redflag_gestante", "r2"): Sugestao(
        "Alterar",
        "Disparar também quando a idade gestacional NÃO for informada (tirar o '≥ 20' ou acrescentar uma "
        "linha com IG desconhecida antes da r7).",
        f"{_P2} — 'a mudança de maior impacto clínico deste documento'. A DL-0123 já escala pelo TEXTO dor "
        "de cabeça com alteração visual na gestação, sem IG: a tabela ficou mais restrita que o código. "
        "Pré-eclâmpsia é emergência obstétrica.",
        "cefaleia_alteracao_visual",
    ),
    ("triage_redflag_gestante", "r3"): Sugestao(
        "Alterar",
        "Disparar também com idade gestacional desconhecida.",
        f"{_P2}. A DL-0123 escala 'não sinto o bebê mexer' pelo texto, sem IG. Quem relata o sinal costuma "
        "estar no 3º trimestre.",
        "movimentos_fetais_reduzidos",
    ),
    ("triage_redflag_gestante", "r4"): Sugestao(
        "Alterar",
        "Remover o gate '< 37': escalar com IG desconhecida e também a termo. Se o médico preferir, ≥ 37 "
        "semanas pode ser P2 — nunca sem bandeira.",
        f"{_REL}, r4: 'acima de 37 semanas a perda de líquido não escala — é termo, mas ainda é trabalho de "
        "parto'. Padrão 2. A DL-0123 escala 'bolsa rompeu/estourou' pelo texto, sem IG.",
        "perda_liquido",
    ),
    ("triage_redflag_gestante", "r5"): Sugestao(
        "Alterar",
        "Escalar com IG desconhecida (P1). Para ≥ 37 semanas: sem base suficiente — decisão do médico "
        "(sugerimos ao menos P2, com orientação de maternidade).",
        f"{_REL}, r5: 'mesma observação sobre o termo'. Padrão 2. A DL-0123 escala contrações "
        "regulares/antes do tempo pelo texto.",
        "contracoes_regulares",
    ),
    ("triage_redflag_gestante", "r6"): Sugestao(
        VEREDITO_CONFIRMO,
        "Tornar definitiva a interina DL-0067 (febre na gestação, inclusive intensidade desconhecida).",
        "DL-0067 (caso E07). Febre na gestação pede avaliação. Incluir 'leve': sem base suficiente — decisão "
        "do médico.",
        "febre",
    ),
    ("triage_redflag_gestante", "r7"): Sugestao(
        "Alterar",
        "Rede de segurança: aceitar 'moderada' (P2) E criar, ANTES desta linha, uma regra de falta de ar na "
        "gestante (P1).",
        f"{_P1}. Lacuna C1 do mesmo relatório: não existe código respiratório na gestante — falta de ar só "
        "escala se ela disser 'grave'. Código e regra entram juntos (test_helena_codigos_casam_com_a_dmn.py) "
        "e antes do fail-safe (hit policy FIRST).",
        "intensidade = grave",
    ),
    ("triage_redflag_gestante", "r8"): _CATCH_ALL,
    # ---------------------------------------------------------------- saude mental
    ("triage_redflag_mental_health", "r1"): Sugestao(
        VEREDITO_CONFIRMO,
        "Manter.",
        "Ideação com plano/meio é emergência em qualquer protocolo. O texto fixo traz o CVV 188 e o 192 "
        "(DL-0123 item 2).",
        "ideacao_suicida",
    ),
    ("triage_redflag_mental_health", "r2"): Sugestao(
        VEREDITO_CONFIRMO,
        "Manter (a r1 é redundante — limpeza opcional, não muda a conduta).",
        f"{_REL}: a r2 garante P1 mesmo sem o campo risco_imediato; 'é a única tabela em que a ausência de "
        "dado não enfraquece a regra'.",
        "ideacao_suicida",
    ),
    ("triage_redflag_mental_health", "r3"): Sugestao(
        VEREDITO_CONFIRMO,
        "Manter.",
        "Autolesão é risco psicossocial; escalation_routing r2 põe risco_psicossocial sempre em P1; a "
        "DL-0123 reconhece termos de autolesão no texto.",
        "autolesao",
    ),
    ("triage_redflag_mental_health", "r4"): Sugestao(
        VEREDITO_CONFIRMO,
        "Manter.",
        "Agitação grave/surto psicótico agudo: risco para si e para terceiros; humano imediato.",
        "agitacao_agressividade",
    ),
    ("triage_redflag_mental_health", "r5"): Sugestao(
        VEREDITO_CONFIRMO,
        "Tornar definitiva a interina DL-0069 (crise de ansiedade/pânico em P1).",
        "DL-0069 (caso E08): o cabeçalho da tabela e a escalation_routing r2 já dizem que saúde mental é "
        "sempre P1. Crise de pânico pode mimetizar quadro cardíaco — humano imediato é o lado seguro.",
        "crise_ansiedade",
    ),
    ("triage_redflag_mental_health", "r6"): Sugestao(
        "Alterar",
        "Subir para P1/ESCALATE_EMERGENCY e aceitar também 'moderada'.",
        "DL-0069: 'a r6 tem a mesma contradição e NÃO foi tocada' — a linha diz P2 enquanto o cabeçalho diz "
        f"'em saúde mental quase tudo escala'. {_P1}.",
        "intensidade = grave",
    ),
    ("triage_redflag_mental_health", "r7"): _CATCH_ALL,
    # ---------------------------------------------------------------- pediatria
    ("triage_redflag_pediatric", "r1"): Sugestao(
        VEREDITO_CONFIRMO,
        "Manter. Note: (a) depende de idade_meses — a suf_r5 pergunta a idade; (b) a DL-0105 item 4 (bebê "
        "< 3 meses SEM alarme recebe a oferta de falar agora com a equipe) também espera este médico — ver "
        "aba de pendências.",
        f"Febre em lactente < 3 meses é sinal de alto risco na {_NICE}. {_REL}, padrão 3: a bateria "
        "confirmou que funciona; mudar o degrau de 3 meses: sem base suficiente.",
        "febre",
    ),
    ("triage_redflag_pediatric", "r2"): Sugestao(
        VEREDITO_CONFIRMO,
        "Manter.",
        f"Esforço respiratório em criança é emergência. {_REL}: 'sem gate. Correto'.",
        "dificuldade_respiratoria",
    ),
    ("triage_redflag_pediatric", "r3"): Sugestao(
        VEREDITO_CONFIRMO,
        "Manter.",
        f"Febre com petéquias (manchas que não somem à pressão) = suspeita meningocócica; {_NICE}. {_REL}: "
        "correto.",
        "petequias_febre",
    ),
    ("triage_redflag_pediatric", "r4"): Sugestao(
        VEREDITO_CONFIRMO, "Manter.", f"Crise convulsiva é emergência. {_REL}: correto.", "convulsao"
    ),
    ("triage_redflag_pediatric", "r5"): Sugestao(
        VEREDITO_CONFIRMO,
        "Manter.",
        f"Criança que não reage/difícil de acordar é sinal vermelho na {_NICE}. A DL-0123 também escala "
        "'febre + molinha' pelo texto.",
        "letargia",
    ),
    ("triage_redflag_pediatric", "r9"): Sugestao(
        VEREDITO_CONFIRMO, "Confirmar (é a ratificação médica que faltava).", _NEONATAL, "choro_inconsolavel"
    ),
    ("triage_redflag_pediatric", "r10"): Sugestao(
        VEREDITO_CONFIRMO, "Confirmar (é a ratificação médica que faltava).", _NEONATAL, "recusa_alimentar"
    ),
    ("triage_redflag_pediatric", "r11"): Sugestao(
        VEREDITO_CONFIRMO, "Confirmar (é a ratificação médica que faltava).", _NEONATAL, "hipotonia"
    ),
    ("triage_redflag_pediatric", "r12"): Sugestao(
        VEREDITO_CONFIRMO, "Confirmar (é a ratificação médica que faltava).", _NEONATAL, "cianose"
    ),
    ("triage_redflag_pediatric", "r13"): Sugestao(
        VEREDITO_CONFIRMO,
        "Confirmar (é a ratificação médica que faltava).",
        _NEONATAL,
        "vomitos_persistentes",
    ),
    ("triage_redflag_pediatric", "r6"): Sugestao(
        "Alterar",
        "Manter a interina DL-0067 (intensidade desconhecida escala) E aceitar também 'leve' (P2), ao menos "
        "em menores de 1 ano.",
        f"{_REL}, r6: 'desidratação leve em criança pequena progride rápido — vale confirmar se leve deveria "
        "entrar'. DL-0067 (caso E06): o cuidador raramente mede o degrau.",
        "sinais_desidratacao",
    ),
    ("triage_redflag_pediatric", "r7"): Sugestao(
        "Alterar",
        "Rede de segurança: aceitar também 'moderada' (P2).",
        f"{_P1}. Ausência apontada no mesmo relatório: vômitos persistentes e recusa alimentar só têm regra "
        "no recém-nascido (r10, r13); a criança maior que ainda não desidratou não casa nada.",
        "intensidade = grave",
    ),
    ("triage_redflag_pediatric", "r8"): _CATCH_ALL,
    # ---------------------------------------------------------------- suficiencia
    ("triage_sufficiency", "suf_r1_tem_fim"): Sugestao(
        VEREDITO_CONFIRMO,
        "Confirmar a linha (limite → ESCALAR). Decisão aberta 1 (para onde escalar): sugerimos "
        "'intencao_clinica' (P2, 30 min) em vez de 'solicitacao_humano' (P3, 4 h). Decisão aberta 3 (duas "
        "rodadas): sem base suficiente — decisão do médico.",
        "Comentário da própria tabela: 'quem não consegue descrever o próprio sintoma em duas rodadas pode "
        "estar confuso pelo próprio quadro'. Na dúvida, escalar mais: enfermagem (P2) antes de atendimento "
        "(P3). COLETA_MAX_RODADAS = 2 'veio do documento, não de medição'.",
        "rodadas ≥ 2",
    ),
    ("triage_sufficiency", "suf_r2_sem_codigo"): Sugestao(
        VEREDITO_CONFIRMO,
        "Manter: sem código, perguntar — nunca adivinhar.",
        "Comentário da tabela: escolher o código mais próximo foi o defeito de 13/09 (P1 de 5 min aberto por "
        "dor comum); é a regra R2 da régua de extração.",
        "sintoma_reconhecido = false",
    ),
    ("triage_sufficiency", "suf_r3_sem_intensidade"): Sugestao(
        VEREDITO_CONFIRMO,
        "Manter: perguntar a intensidade.",
        f"Fecha pelo lado do dado o buraco do {_P1}.",
        "intensidade_informada = false",
    ),
    ("triage_sufficiency", "suf_r4_gestante_sem_ig"): Sugestao(
        VEREDITO_CONFIRMO,
        "Manter: perguntar a idade gestacional — mas perguntar não pode ser a única rede: ver as sugestões "
        "de r2–r5 da gestante (disparar sem IG).",
        f"{_P2}: 'a maior lacuna medida das quatro tabelas' (comentário da própria linha).",
        "population = gestante",
    ),
    ("triage_sufficiency", "suf_r5_pediatrica_sem_idade"): Sugestao(
        VEREDITO_CONFIRMO,
        "Manter: perguntar a idade em meses.",
        f"{_REL}, padrão 3: febre < 3 meses depende da idade.",
        "population = pediatric",
    ),
    ("triage_sufficiency", "suf_r6_adulto_sem_idade"): Sugestao(
        "Alterar",
        "Perguntar a idade ao adulto com FEBRE (o resto segue SUFICIENTE). Exige acrescentar a entrada "
        "sintoma_codigo à tabela; a alternativa sem mudar o esquema é esta linha virar PERGUNTAR_IDADE para "
        "todo adulto.",
        "É a alternativa que a própria linha oferece ('ou uma linha nova restrita a febre'). A única regra "
        "do adulto que usa idade é febre ≥ 65 (r8, interina DL-0067): sem perguntar, o idoso com febre que "
        "não diz a idade sai sem bandeira. Custo: uma rodada, só na febre.",
        "population = adult",
    ),
    ("triage_sufficiency", "suf_r7_catchall"): _CATCH_ALL,
    # ---------------------------------------------------------------- alarme por texto (DL-0123)
    (TABELA_DO_ALARME, "gestacao-1"): Sugestao(
        VEREDITO_CONFIRMO,
        "Manter P1 + 192 pelo texto, sem depender da idade gestacional.",
        "DL-0123 (1): o dossiê de segurança mediu 43,8% de 192 na gestante porque a tabela exige IG. Bolsa "
        "rota, contrações regulares/pré-termo, parada de movimentos fetais, sangramento, cefaleia com "
        "alteração visual e edema de face/mãos são sinais de alarme obstétrico usuais. Falso positivo = 192 "
        "+ caso (lado seguro).",
        "gestação",
    ),
    (TABELA_DO_ALARME, "pressao-1"): Sugestao(
        VEREDITO_CONFIRMO,
        "Manter. Atenção: 180/110 SEM sintoma é 'urgência hipertensiva' (não emergência) nas diretrizes "
        "brasileiras de hipertensão; P1 + 192 é a escolha conservadora — rebaixar a P2 é decisão do médico.",
        "DL-0123 (1): pressão '18 por 11' recebia a pergunta de alerta em vez de escalar.",
        "sistólica ≥ 180",
    ),
    (TABELA_DO_ALARME, "pressao-2"): Sugestao(
        VEREDITO_CONFIRMO,
        "Manter.",
        "Pressão elevada com sinal de lesão de órgão-alvo (visão, dor no peito, falta de ar, déficit) é "
        "possível emergência hipertensiva. DL-0123 (1) e (4): pressão alta relatada nunca é 'erro de "
        "medição'.",
        "sistólica ≥ 140",
    ),
    (TABELA_DO_ALARME, "crianca_febre_prostrada-1"): Sugestao(
        VEREDITO_CONFIRMO,
        "Manter.",
        f"Criança febril que não reage/difícil de acordar é sinal vermelho na {_NICE}. DL-0123: o dossiê "
        "mediu o caso sem escalar.",
        "febre",
    ),
    (TABELA_DO_ALARME, "intoxicacao-1"): Sugestao(
        VEREDITO_CONFIRMO,
        "Manter P1 + 192. Se a ingestão deve abrir TAMBÉM risco psicossocial (CVV 188): sem base suficiente "
        "— decisão do médico.",
        "Ingestão de muitos comprimidos, cáustico, pilha/bateria é emergência toxicológica (DL-0123 (1)); "
        "pode ser tentativa de autoextermínio, que hoje só o portão de risco psicossocial trata.",
        "ingestão",
    ),
    (TABELA_DO_ALARME, "reacao_alergica_grave-1"): Sugestao(
        VEREDITO_CONFIRMO,
        "Manter.",
        "Inchaço de boca/garganta/língua = angioedema/possível anafilaxia (DL-0123 item 5). Ver a sugestão "
        "da r6 do adulto (tabela mais restrita que o código).",
        "inchaço",
    ),
    (TABELA_DO_ALARME, "abdome_direito-1"): Sugestao(
        VEREDITO_CONFIRMO,
        "Manter. P2 (enfermagem) seria defensável; P1 + 192 é a escolha conservadora — decisão do médico.",
        "Dor do lado direito da barriga que piora sugere apendicite (DL-0123 (1): o dossiê mediu o caso sem "
        "escalar).",
        "lado direito",
    ),
}


def _sugestao(tabela: str, regra: str, quando: str) -> tuple[str, str]:
    sug = _SUGESTOES.get((tabela, regra))
    if sug is None:
        return SEM_BASE, "Nenhuma sugestão escrita para esta linha."
    if sug.guarda not in quando:
        return (
            f"{DESATUALIZADA}: a regra mudou depois que a sugestão foi escrita. {SEM_BASE}",
            f"A sugestão pressupunha '{sug.guarda}' na condição, que não está mais lá.",
        )
    prefixo = sug.veredito if sug.veredito != SEM_BASE else ""
    return (f"{prefixo}: {sug.texto}" if prefixo else sug.texto), sug.por_que


# =========================================================================================================
# Coleta das linhas
# =========================================================================================================

Linha = dict[str, str]


def coletar_regras(raiz: Path = RAIZ_REPO) -> list[Linha]:
    """Uma linha por regra: as 4 tabelas de alarme, o alarme por texto (codigo) e a suficiencia."""
    linhas: list[Linha] = []

    def _dmn(decisao: str) -> None:
        relativo = f"{DMN_DIR}/{decisao}.dmn"
        tabela = ler_dmn(raiz, relativo)
        sha = sha256_lf(raiz / relativo)
        for ordem, regra in enumerate(tabela.regras, start=1):
            quando = condicoes_legiveis(tabela, regra)
            sugestao, por_que = _sugestao(decisao, regra.id, quando)
            linhas.append(
                _linha(
                    decisao,
                    regra.id,
                    quando,
                    saida_legivel(tabela, regra),
                    _situacao(tabela, regra),
                    sugestao,
                    por_que,
                    ordem,
                    regra.descricao,
                    relativo,
                    sha,
                )
            )

    for decisao in TABELAS_DE_ALARME:
        _dmn(decisao)
    regras_alarme, _ = ler_alarme(raiz)
    sha_alarme = sha256_lf(raiz / ALARME_CODIGO)
    for ordem, ra in enumerate(regras_alarme, start=1):
        entao = (
            f"alerta clínico P1 (motivo red_flag_clinico, severidade grave), 192 imperativo e caso aberto na "
            f"hora — categoria '{ra.categoria}'"
            + (f"; população do caso: {ra.populacao}" if ra.populacao else "")
        )
        sugestao, por_que = _sugestao(TABELA_DO_ALARME, ra.id, ra.quando)
        linhas.append(
            _linha(
                TABELA_DO_ALARME,
                ra.id,
                ra.quando,
                entao,
                "Sim — regra do CÓDIGO (DL-0123), pendente do dono clínico",
                sugestao,
                por_que,
                ordem,
                "Roda no grafo DEPOIS do portão de risco psicossocial e ANTES da tabela; nada aqui rebaixa "
                "um caso (docstring do módulo).",
                ALARME_CODIGO,
                sha_alarme,
            )
        )
    _dmn(TABELA_SUFICIENCIA)
    return linhas


def _linha(
    tabela: str,
    regra: str,
    quando: str,
    entao: str,
    interina: str,
    sugestao: str,
    por_que: str,
    ordem: int,
    nota: str,
    arquivo: str,
    sha: str,
) -> Linha:
    return {
        COL_TABELA: tabela,
        COL_REGRA: regra,
        COL_QUANDO: quando,
        COL_ENTAO: entao,
        COL_INTERINA: interina,
        COL_SUGESTAO: sugestao,
        COL_POR_QUE: por_que,
        COL_VEREDITO: "",
        COL_AJUSTE: "",
        COL_COMENTARIO: "",
        COL_ORDEM: str(ordem),
        COL_NOTA: nota,
        COL_ARQUIVO: arquivo,
        COL_SHA: sha,
    }


def contagem_esperada(raiz: Path = RAIZ_REPO) -> dict[str, int]:
    """Quantas regras cada fonte tem, contadas por um caminho INDEPENDENTE da coleta (texto cru)."""
    esperado: dict[str, int] = {}
    for decisao in TABELAS_REVISADAS:
        texto = (raiz / DMN_DIR / f"{decisao}.dmn").read_text(encoding="utf-8")
        esperado[decisao] = len(re.findall(r"<rule\s", texto))
    fonte = (raiz / ALARME_CODIGO).read_text(encoding="utf-8")
    corpo = fonte.split("def alarme_no_relato", 1)[1].split("\ndef ", 1)[0]
    esperado[TABELA_DO_ALARME] = len(re.findall(r"^\s+return ALARME_", corpo, flags=re.MULTILINE))
    return esperado


def conferir_contagem(linhas: list[Linha], raiz: Path = RAIZ_REPO) -> dict[str, int]:
    """Levanta se o numero de linhas por tabela nao bate com as regras dos arquivos."""
    obtido = Counter(linha[COL_TABELA] for linha in linhas)
    esperado = contagem_esperada(raiz)
    if dict(obtido) != esperado:
        raise ValueError(f"contagem nao bate: planilha {dict(obtido)} x arquivos {esperado}")
    return esperado


# ---------------------------------------------------------------------------------------- SLAs e grupos

COLUNAS_SLAS: Final[tuple[tuple[str, int], ...]] = (
    ("Item", 26),
    ("Onde decide (fonte)", 34),
    ("Valor atual", 14),
    ("Valor legível", 18),
    ("Sugestão", 52),
    ("Por que", 70),
    ("Confirmo / Altero", 16),
    ("Altero para", 20),
    ("Comentário", 40),
)
_DURACAO: Final[dict[str, str]] = {"M": "min", "H": "h"}

_SUG_SLA: Final[dict[tuple[str, str], tuple[str, str]]] = {
    ("ack", "P1"): (
        "Confirmo.",
        f"Ciência humana em até 5 min. Os {_MANCH} vão de atendimento imediato (vermelho) a 10 min "
        "(laranja); 5 min de ciência remota é compatível com o art. 35-C ('urgência = imediato'). O "
        "beneficiário já recebe o 192 na hora; este prazo é o do contato humano.",
    ),
    ("resolucao", "P1"): (
        "Confirmo.",
        "30 min para concluir o contato clínico de um P1; estourou, o supervisor assume "
        "(UT_SupervisorAssume). Prazo menor: sem base suficiente — decisão do médico.",
    ),
    ("ack", "P2"): (
        "Alterar para PT15M.",
        f"P2 inclui quadros que os {_MANCH} põem em 'muito urgente' (laranja, 10 min) — síncope, febre em "
        "idoso, dor abdominal grave; 30 min fica entre o laranja e o amarelo (60 min). Na dúvida, escalar "
        "mais. Custo: escala de enfermagem.",
    ),
    ("resolucao", "P2"): ("Confirmo.", "Sem base no repositório para outro valor; decisão do médico."),
    ("ack", "P3"): (
        "Confirmo (regulatório confere contra a RN 623/2024).",
        "P3 é pedido de pessoa e falha técnica (não clínico). Atenção: a falha técnica vai a P3 mesmo quando "
        "nasce de recusa clínica (ADR-0059 D1 — ver aba ADR-0059).",
    ),
    ("resolucao", "P3"): (
        "Confirmo (regulatório confere contra a RN 623/2024).",
        "Prazo de atendimento de demanda não assistencial — o enquadramento é do regulatório.",
    ),
}
_SUG_GRUPO: Final[dict[str, tuple[str, str]]] = {
    "plantao-clinico": (
        "Confirmo o papel (P1). Sugerimos que o plantão tenha MÉDICO alcançável.",
        "Contrato: 'Enfermeiro/medico de plantao' — DRAFT/verify; o NOME real é da sessão R-034 (PO), não "
        "deste médico.",
    ),
    "enfermagem-triagem": (
        "Confirmo o papel (P2).",
        "Contrato: DRAFT/verify (nome candidato, R-034).",
    ),
    "atendimento-humano": (
        "Alterar: o catch-all r7 (motivo desconhecido, P2) deveria ir para enfermagem-triagem, não para "
        "atendimento-humano.",
        "Motivo desconhecido pode ser clínico; o próprio contrato diz 'motivo desconhecido nunca vira P3' — "
        "o mesmo raciocínio pede um profissional de saúde na ponta. Nome do grupo: R-034.",
    ),
    "supervisao-atendimento": (
        "Confirmo o papel. Para P1 estourado, sugerimos supervisão CLÍNICA (sem base suficiente — decisão do "
        "médico).",
        "UT_SupervisorAssume recebe o SLA de resolução estourado de QUALQUER prioridade, inclusive P1 "
        "clínico.",
    ),
}


def _legivel(iso: str) -> str:
    m = re.fullmatch(r"PT(\d+)([MH])", iso)
    return f"{m.group(1)} {_DURACAO[m.group(2)]}" if m else iso


def coletar_slas(raiz: Path = RAIZ_REPO) -> list[Linha]:
    tabela = ler_dmn(raiz, ROTEAMENTO)
    idx = {nome: i for i, nome in enumerate(tabela.saidas)}
    por_prioridade: dict[str, set[tuple[str, str]]] = {}
    regras_por_prioridade: dict[str, list[str]] = {}
    grupos: dict[str, list[str]] = {}
    for regra in tabela.regras:
        valor = [s.strip('"') for s in regra.saidas]
        prio = valor[idx["prioridade"]]
        por_prioridade.setdefault(prio, set()).add((valor[idx["sla_ack"]], valor[idx["sla_resolucao"]]))
        regras_por_prioridade.setdefault(prio, []).append(regra.id)
        grupos.setdefault(valor[idx["grupo_atendimento"]], []).append(f"{regra.id} ({prio})")
    linhas: list[Linha] = []
    for prio in sorted(por_prioridade):
        pares = por_prioridade[prio]
        if len(pares) != 1:
            raise ValueError(f"{ROTEAMENTO}: {prio} com mais de um par de SLAs: {sorted(pares)}")
        ack, res = next(iter(pares))
        fonte = f"{ROTEAMENTO} ({', '.join(regras_por_prioridade[prio])})"
        for tipo, rotulo, prazo in (("ack", "Ciência (ack)", ack), ("resolucao", "Resolução", res)):
            sug, por_que = _SUG_SLA.get((tipo, prio), (SEM_BASE, ""))
            linhas.append(_linha_sla(f"{rotulo} {prio}", fonte, prazo, _legivel(prazo), sug, por_que))
    bpmn = (raiz / BPMN_ESCALONAMENTO).read_text(encoding="utf-8")
    for literal in re.findall(r'candidateGroups="([^"$]+)"', bpmn):
        grupos.setdefault(literal, []).append("BPMN UT_SupervisorAssume")
    for grupo, onde in grupos.items():
        sug, por_que = _SUG_GRUPO.get(grupo, (SEM_BASE, ""))
        linhas.append(_linha_sla(f"Grupo {grupo}", ", ".join(onde), grupo, "", sug, por_que))
    return linhas


def _linha_sla(item: str, fonte: str, valor: str, legivel: str, sug: str, por_que: str) -> Linha:
    nomes = [c for c, _ in COLUNAS_SLAS]
    return dict(zip(nomes, [item, fonte, valor, legivel, sug, por_que, "", "", ""], strict=True))


# ---------------------------------------------------------------------------------------- caso paralelo

COLUNAS_PARALELO: Final[tuple[tuple[str, int], ...]] = (
    ("Diferença", 22),
    ("O contrato v1.1.0 diz", 52),
    ("O código faz", 52),
    ("Sugestão", 44),
    ("Por que", 60),
    ("Confirmo / Altero", 16),
    ("Altero para", 30),
    ("Comentário", 40),
)


def _linha_de(raiz: Path, relativo: str, trecho: str) -> str:
    for n, texto in enumerate((raiz / relativo).read_text(encoding="utf-8").splitlines(), start=1):
        if trecho in texto:
            return f"{relativo}:{n}"
    raise ValueError(f"{relativo}: trecho {trecho!r} nao encontrado (a planilha citaria linha errada)")


def coletar_caso_paralelo(raiz: Path = RAIZ_REPO) -> list[Linha]:
    c_exc = _linha_de(raiz, CONTRATO, "Excecao ratificada (DL-0072")
    c_um = _linha_de(raiz, CONTRATO, "Com um caso CLINICO ja aberto")
    g_motivos = _linha_de(raiz, GRAFO_HELENA, "_MOTIVOS_CLINICOS_COM_CHAVE_PROPRIA: frozenset")
    g_cobre = _linha_de(raiz, GRAFO_HELENA, "def _caso_clinico_cobre_o_motivo")
    g_uso = _linha_de(raiz, GRAFO_HELENA, "and motivo in _MOTIVOS_CLINICOS_COM_CHAVE_PROPRIA")
    emenda = "Emendar o contrato (v1.2.0) para o comportamento do código, que é o mais conservador."
    dados = [
        (
            "1. Severidade",
            f"Só alerta clínico GRAVE abre o caso paralelo '-clin' ({c_exc}).",
            f"Abre '-clin' para QUALQUER severidade clínica ({g_motivos}; uso em {g_uso}).",
            emenda,
            "DL-0097 (B), teste real do dono de 14/10/2026: uma queixa clínica moderada recebia 'seu "
            "atendimento já está aberto' e ficava atrás de um caso de cobrança.",
        ),
        (
            "2. Motivos",
            f"Só motivo_categoria ∈ {{red_flag_clinico, risco_psicossocial}} ({c_exc}).",
            f"Também 'intencao_clinica' (pergunta clínica) abre '-clin' ({g_motivos}).",
            emenda,
            "DL-0097 (B): 'conteúdo clínico sem pedido explícito de pessoa nunca vira solicitacao_humano; o "
            "caso abre na chave -clin'. A pergunta clínica vai a P2/enfermagem (escalation_routing r4).",
        ),
        (
            "3. Caso clínico já aberto",
            "'Com um caso CLINICO ja aberto na conversa (qualquer das duas chaves) nao se abre outro' "
            f"({c_um}).",
            "Um caso 'intencao_clinica' aberto na chave da conversa NÃO cobre um ALERTA novo "
            f"(red_flag_clinico/risco_psicossocial): o alerta abre o próprio '-clin' ({g_cobre}).",
            emenda,
            "Um alerta (P1) nunca deve ficar atrás de uma pergunta clínica (P2) aberta antes — é a mesma "
            "lógica da DL-0072 aplicada dentro da classe clínica.",
        ),
    ]
    nomes = [c for c, _ in COLUNAS_PARALELO]
    return [dict(zip(nomes, [*d, "", "", ""], strict=True)) for d in dados]


# ---------------------------------------------------------------------------------------- ADR-0059

COLUNAS_ADR: Final[tuple[tuple[str, int], ...]] = (
    ("Item", 10),
    ("Resumo", 60),
    ("Sugestão", 44),
    ("Por que", 60),
    ("Veredito", 22),
    ("Comentário", 40),
)
LISTA_ADR_DECISAO: Final[str] = '"Ratifico,Derrubo"'
LISTA_ADR_RESIDUAL: Final[str] = '"Aceito,Exijo conserto antes"'


def coletar_adr0059() -> list[Linha]:
    dados = [
        (
            "D1",
            "Recusa da cerca de saída num turno 'inform': só 'negativa_clinica' escala (como falha_tecnica); "
            "promessa de humano/de capacidade e canal não confirmado viram TROCA DE TEXTO "
            "(RESPOSTA_INFORM_RECUSADA), sem abrir fila. Já em produção.",
            "Ratifico. Observação: a falha_tecnica vai a P3/atendimento-humano na escalation_routing (r6), "
            "mesmo quando nasce de negativa_clínica — se deve ir à enfermagem: sem base suficiente — decisão "
            "do médico.",
            "Medição C1 × B5 (ADR-0059): a fila dependia da frase sorteada pelo modelo, com rótulo falso de "
            "falha técnica. A parte clínica (negativa_clinica) continua escalando.",
        ),
        (
            "D2",
            "Um sintoma_codigo que não existe na tabela da população do turno é DESCARTADO (vira None) antes "
            "de consultar a tabela. Já em produção.",
            "Ratifico, com a condição de que o código descartado leve à PERGUNTA de caracterização "
            "(triage_sufficiency suf_r2) e nunca direto à resposta automática.",
            "Medição D2: P1 de cefaleia adulta numa conversa pediátrica de febre. Residual R2 (ADR-0059): "
            "uma população lembrada errada derruba um código legítimo — perguntar é o lado seguro do "
            "descarte.",
        ),
        (
            "R1",
            "D1 desliga uma rede de segurança ACIDENTAL: o pedido de pessoa mal classificado como "
            "'information' deixa de abrir fila; a constante pede que a pessoa repita o pedido (custa um "
            "turno).",
            "Exijo conserto antes: a classificação deve parar de errar human_request, cobrada por caso no "
            "corpus de extração.",
            "Pedido de pessoa sempre vence (RN 623/2024; dossiê de segurança §2 regra 4). O próprio ADR diz "
            "que 'o conserto certo é a classificação parar de errar human_request'.",
        ),
    ]
    nomes = [c for c, _ in COLUNAS_ADR]
    return [dict(zip(nomes, [*d, "", ""], strict=True)) for d in dados]


# ---------------------------------------------------------------------------------------- pendencias

COLUNAS_PENDENCIAS: Final[tuple[tuple[str, int], ...]] = (
    ("Campo", 52),
    ("Valor (preenchido pela pessoa)", 44),
    ("Para que serve", 90),
)


def coletar_pendencias() -> list[Linha]:
    dados = [
        (PEND_REVISOR, "Vai para 'revisor' das ratificações e 'aprovado_por' do registro de aprovações."),
        (PEND_CRM, "Identificação profissional do revisor."),
        (PEND_CAPACIDADE, "Vai para 'capacidade' das ratificações (ex.: 'Médico-auditor, CRM ...')."),
        (PEND_DATA, "Vai para 'ratificado_em' e 'aprovado_em'. Não pode ser data futura."),
        (
            PEND_DONO,
            "owners.clinico de spec/agents/helena/agent.yaml. Tem de ser o MESMO nome do signoff "
            "(tests/unit/agents/test_helena_dono_declarado.py).",
        ),
        (PEND_REG_NOME, "Revisor do papel 'regulatorio' no signoff (abas SLAs e Caso paralelo)."),
        (PEND_REG_DATA, "Data do signoff do regulatório."),
        (
            "DL-0105 item 4 — bebê < 3 meses SEM alarme",
            "Decisão clínica pendente: hoje recebe a oferta explícita de falar agora com a equipe de saúde "
            "(sem abrir caso automaticamente), com pronto-atendimento/192 se piorar. Sugestão: Confirmo "
            "(conservador). Escreva no valor: Confirmo / Altero para ...",
        ),
        (
            "DL-0097 (A) — severidade 'grave' do alerta neonatal do grafo",
            "A regra do grafo (_alerta_neonatal) escala recém-nascido com sofrimento como GRAVE; a DL diz "
            "que depende de ratificação clínica. Sugestão: Confirmo. Escreva no valor: Confirmo / Altero.",
        ),
    ]
    nomes = [c for c, _ in COLUNAS_PENDENCIAS]
    return [dict(zip(nomes, [campo, "", para], strict=True)) for campo, para in dados]


LEIA_ME: Final[tuple[str, ...]] = (
    "REVISÃO CLÍNICA E REGULATÓRIA — ESCALONAMENTO DA HELENA (SP-OP-ESCALATION-001, contrato FINAL v1.1.0)",
    "",
    "O QUE É. Todas as regras que decidem quando a Helena manda ligar 192 e abre um caso para a equipe "
    "humana, geradas DIRETAMENTE dos arquivos do sistema (tabelas DMN e código) pelo script "
    "scripts/sme/revisao_clinica.py — nenhuma regra foi digitada à mão. Hoje TODAS estão como rascunho "
    "clínico (DRAFT): nenhuma foi assinada por médico.",
    "",
    "PARA QUEM. Médico-auditor: abas 'Regras de alarme', 'SLAs e grupos', 'ADR-0059' e 'Pendências do dono "
    "clínico'. Regulatório: abas 'SLAs e grupos' e 'Caso paralelo' (a pergunta sobre o art. 35-C e a RN "
    "623 está em docs/sme-dispatch/medico-auditor/EMENDA-v1.2.0-caso-paralelo.md).",
    "",
    "A COLUNA 'SUGESTÃO' NÃO É PARECER MÉDICO. É um rascunho preparado pela equipe de tecnologia (com "
    "apoio de IA) para poupar seu tempo: conservador (na dúvida, escalar mais), apoiado nas decisões já "
    "registradas (DL), no relatório das 34 regras de 14/09, no dossiê de segurança e em critérios de "
    "triagem amplamente usados. Quando não há base, ela diz 'Sem base suficiente: decisão do médico'. "
    "Quem decide é o senhor(a).",
    "",
    "COMO PREENCHER (aba 'Regras de alarme'): em cada linha, escolha na lista da coluna 'Veredito do "
    "médico': Confirmo (a regra está certa como está) / Alterar (descreva o ajuste na coluna seguinte) / "
    "Perigoso (a regra como está pode causar dano — explique no comentário). Veredito vazio = não revisada. "
    "Leia a regra pelas colunas 'Quando' e 'Então'; 'Ordem na tabela' importa: a primeira linha que casa "
    "decide.",
    "",
    "O QUE ACONTECE DEPOIS. O script scripts/sme/revisao_clinica_para_yaml.py lê esta planilha preenchida e "
    "GERA (sem aplicar) os arquivos de ratificação de cada tabela — só com as linhas 'Confirmo', amarrados "
    "ao hash do arquivo que o senhor(a) leu — e as entradas candidatas do registro de aprovações (uma "
    "tabela inteira só entra se TODAS as linhas dela forem 'Confirmo'). 'Alterar' e 'Perigoso' viram "
    "mudança de regra num PR próprio, revisado. Se a tabela mudar depois de gerada a planilha, o conversor "
    "recusa: é preciso gerar e revisar de novo.",
    "",
    "ASSINATURA. A planilha não é a assinatura. A assinatura é o arquivo "
    "docs/processes/contracts/signoffs/SP-OP-ESCALATION-001.signoff.yaml (modelo em "
    "docs/sme-dispatch/medico-auditor/SIGNOFF-MODELO-SP-OP-ESCALATION-001.yaml), com nome e data de quem "
    "assina. Nenhum agente de IA preenche nome, data ou veredito.",
    "",
    "COLUNAS TÉCNICAS (à direita): 'Arquivo' e 'sha256' amarram cada linha à versão exata do arquivo lido. "
    "Não edite.",
)


# =========================================================================================================
# Planilha
# =========================================================================================================


def _openpyxl() -> Any:
    try:
        return importlib.import_module("openpyxl")
    except ModuleNotFoundError as exc:  # pragma: no cover - depende do interpretador
        raise SystemExit(
            "precisa de openpyxl (use um Python que o tenha; nao e' dependencia do projeto)"
        ) from exc


def _aba(
    wb: Any, titulo: str, colunas: tuple[tuple[str, int], ...], linhas: list[Linha], congelar: str = "A2"
) -> Any:
    _openpyxl()
    styles = importlib.import_module("openpyxl.styles")
    utils = importlib.import_module("openpyxl.utils")
    ws = wb.create_sheet(titulo)
    fonte = styles.Font(name="Calibri", size=11)
    negrito = styles.Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    fundo = styles.PatternFill("solid", fgColor="1F4E78")
    quebra = styles.Alignment(wrap_text=True, vertical="top")
    for j, (nome, largura) in enumerate(colunas, start=1):
        c = ws.cell(row=1, column=j, value=nome)
        c.font, c.fill, c.alignment = negrito, fundo, quebra
        ws.column_dimensions[utils.get_column_letter(j)].width = largura
    for i, linha in enumerate(linhas, start=2):
        for j, (nome, _) in enumerate(colunas, start=1):
            c = ws.cell(row=i, column=j, value=linha.get(nome) or None)
            c.font, c.alignment = fonte, quebra
    ws.freeze_panes = congelar
    if linhas:
        ws.auto_filter.ref = f"A1:{utils.get_column_letter(len(colunas))}{len(linhas) + 1}"
    return ws


def _lista(ws: Any, coluna: str, n: int, formula: str) -> None:
    dv_mod = importlib.import_module("openpyxl.worksheet.datavalidation")
    dv = dv_mod.DataValidation(type="list", formula1=formula, allow_blank=True, showErrorMessage=True)
    dv.error = "Escolha um valor da lista."
    ws.add_data_validation(dv)
    dv.add(f"{coluna}2:{coluna}{n + 1}")


def _letra(colunas: tuple[tuple[str, int], ...], nome: str) -> str:
    utils = importlib.import_module("openpyxl.utils")
    return str(utils.get_column_letter([c for c, _ in colunas].index(nome) + 1))


def escrever_planilha(destino: Path, raiz: Path = RAIZ_REPO) -> dict[str, int]:
    """Grava a planilha em `destino` (nunca sobrescreve) e devolve o numero de linhas por aba."""
    if destino.exists():
        raise FileExistsError(f"{destino} ja existe - nao sobrescrevo (apague ou escolha outro nome)")
    regras = coletar_regras(raiz)
    conferir_contagem(regras, raiz)
    slas, paralelo = coletar_slas(raiz), coletar_caso_paralelo(raiz)
    adr, pend = coletar_adr0059(), coletar_pendencias()
    op = _openpyxl()
    styles = importlib.import_module("openpyxl.styles")
    wb = op.Workbook()
    leia = wb.active
    leia.title = ABA_LEIA_ME
    leia.column_dimensions["A"].width = 130
    for i, texto in enumerate(LEIA_ME, start=1):
        c = leia.cell(row=i, column=1, value=texto or None)
        c.font = styles.Font(name="Calibri", size=12 if i == 1 else 11, bold=i == 1)
        c.alignment = styles.Alignment(wrap_text=True, vertical="top")
    ws = _aba(wb, ABA_REGRAS, COLUNAS_REGRAS, regras, congelar="C2")
    _lista(ws, _letra(COLUNAS_REGRAS, COL_VEREDITO), len(regras), '"' + ",".join(VEREDITOS) + '"')
    ws = _aba(wb, ABA_SLAS, COLUNAS_SLAS, slas)
    _lista(ws, _letra(COLUNAS_SLAS, "Confirmo / Altero"), len(slas), '"Confirmo,Altero"')
    ws = _aba(wb, ABA_PARALELO, COLUNAS_PARALELO, paralelo)
    _lista(ws, _letra(COLUNAS_PARALELO, "Confirmo / Altero"), len(paralelo), '"Confirmo,Altero"')
    ws = _aba(wb, ABA_ADR, COLUNAS_ADR, adr)
    col = _letra(COLUNAS_ADR, "Veredito")
    dv_mod = importlib.import_module("openpyxl.worksheet.datavalidation")
    for formula, celulas in ((LISTA_ADR_DECISAO, f"{col}2:{col}3"), (LISTA_ADR_RESIDUAL, f"{col}4")):
        dv = dv_mod.DataValidation(type="list", formula1=formula, allow_blank=True, showErrorMessage=True)
        ws.add_data_validation(dv)
        dv.add(celulas)
    _aba(wb, ABA_PENDENCIAS, COLUNAS_PENDENCIAS, pend)
    destino.parent.mkdir(parents=True, exist_ok=True)
    wb.save(destino)
    return conferir_planilha(destino, raiz)


def ler_aba(caminho: Path, aba: str) -> list[Linha]:
    """As linhas de dados de uma aba como {cabecalho: texto} (celula vazia = "")."""
    op = _openpyxl()
    wb = op.load_workbook(caminho, read_only=True, data_only=True)
    try:
        linhas = list(wb[aba].iter_rows(values_only=True))
    finally:
        wb.close()
    if not linhas:
        return []
    cab = [str(c or "").strip() for c in linhas[0]]
    saida: list[Linha] = []
    for bruta in linhas[1:]:
        if all(v is None or str(v).strip() == "" for v in bruta):
            continue
        saida.append({cab[j]: ("" if v is None else str(v).strip()) for j, v in enumerate(bruta) if cab[j]})
    return saida


def conferir_planilha(caminho: Path, raiz: Path = RAIZ_REPO) -> dict[str, int]:
    """Reabre a planilha e confere: linhas por tabela = regras dos arquivos; veredito VAZIO em toda linha."""
    regras = ler_aba(caminho, ABA_REGRAS)
    conferir_contagem(regras, raiz)
    preenchidas = [r[COL_REGRA] for r in regras if r.get(COL_VEREDITO) or r.get(COL_AJUSTE)]
    if preenchidas:
        raise ValueError(f"a planilha gerada saiu com veredito preenchido: {preenchidas}")
    return {
        aba: len(ler_aba(caminho, aba))
        for aba in (ABA_REGRAS, ABA_SLAS, ABA_PARALELO, ABA_ADR, ABA_PENDENCIAS)
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    p.add_argument("--destino", type=Path, default=DESTINO_PADRAO, help="pasta onde gravar a planilha")
    p.add_argument("--data", default=date.today().isoformat(), help="data no nome do arquivo (AAAA-MM-DD)")
    p.add_argument("--raiz", type=Path, default=RAIZ_REPO, help="raiz do repositorio")
    args = p.parse_args(argv)
    reconfigurar = getattr(sys.stdout, "reconfigure", None)
    if callable(reconfigurar):  # console Windows (cp1252) nao imprime "≥"/acentos
        reconfigurar(encoding="utf-8")
    destino = args.destino / f"{PREFIXO_ARQUIVO}_{args.data}.xlsx"
    try:
        contagem = escrever_planilha(destino, args.raiz)
    except FileExistsError as exc:
        print(f"ERRO: {exc}", file=sys.stderr)
        return 2
    print(f"planilha: {destino}")
    for aba, n in contagem.items():
        print(f"  {aba}: {n} linha(s)")
    por_tabela = Counter(r[COL_TABELA] for r in coletar_regras(args.raiz))
    for tabela, n in por_tabela.items():
        print(f"    {tabela}: {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
