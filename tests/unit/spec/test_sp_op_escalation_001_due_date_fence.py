"""Cerca do prazo (dueDate) de `UT_TratarEscalonamento` — DL-0145.

A coluna Prazo da fila de tarefas do portal mostra `engine_due_at`, lido do `DUE_DATE_` da tarefa
no motor. O prazo NAO e' regra nova: e' a saida `sla_resolucao` da DMN `escalation_routing`, a
mesma que arma o timer `TD_SlaResolucao` (AGENTS.md regra 5: prazo vem da DMN/BPMN). Este modulo
prova, TUDO derivado dos artefatos (BPMN, DMN, contrato) e nada digitado aqui:

1. a tarefa declara `camunda:dueDate`;
2. a expressao le SO' variaveis ja declaradas: a raiz e' o `resultVariable` da businessRuleTask
   que chama `escalation_routing`, e o atributo e' uma saida dessa DMN declarada no contrato
   (tabela `### escalation_routing`) como ISO 8601;
3. a expressao e' identica a do timer `TD_SlaResolucao` (uma so' fonte do prazo);
4. todo valor que a DMN pode devolver nessa saida e' uma duracao ISO 8601 literal (o motor
   recusa texto invalido em `dueDate`, ENGINE-09027 — provado no CIB Seven 2.1.0);
5. `UT_SupervisorAssume` continua sem prazo (contrato, secao SLAs).

A prova de COMPORTAMENTO (o motor resolve a duracao em data) vive no teste de integracao
`tests/integration/processes/test_sp_op_escalation_001.py::test_due_date_*`.
"""

from __future__ import annotations

import re
from pathlib import Path
from xml.etree import ElementTree

_REPO = Path(__file__).resolve().parents[3]
BPMN = _REPO / "spec/processes/bpmn/SP-OP-ESCALATION-001_Escalonamento_Humano_Universal.bpmn"
DMN = _REPO / "spec/processes/dmn/escalation_routing.dmn"
CONTRATO = _REPO / "docs/processes/contracts/SP-OP-ESCALATION-001.md"

B = "{http://www.omg.org/spec/BPMN/20100524/MODEL}"
C = "{http://camunda.org/schema/1.0/bpmn}"
D = "{https://www.omg.org/spec/DMN/20191111/MODEL/}"

DECISAO = "escalation_routing"
TAREFA = "UT_TratarEscalonamento"
_EXPR = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)\}$")
_DURACAO = re.compile(r'^"P(?:\d+D)?(?:T(?=\d)(?:\d+H)?(?:\d+M)?(?:\d+S)?)?"$')


def _processo() -> ElementTree.Element:
    procs = ElementTree.parse(BPMN).getroot().findall(f"{B}process")
    assert len(procs) == 1
    return procs[0]


def _por_id(proc: ElementTree.Element, el_id: str) -> ElementTree.Element:
    achados = [el for el in proc.iter() if el.get("id") == el_id]
    assert len(achados) == 1, f"{el_id} deve existir exatamente uma vez no BPMN"
    return achados[0]


def _due_date() -> str:
    due = _por_id(_processo(), TAREFA).get(f"{C}dueDate")
    assert due, (
        f"{TAREFA} sem camunda:dueDate — a coluna Prazo do portal volta a dizer 'Sem prazo "
        "informado' (DL-0145). Restaure dueDate=${roteamento.sla_resolucao} no BPMN."
    )
    return due


def _saidas_dmn() -> dict[str, list[str]]:
    """{nome da saida: [texto de cada regra]} da decisao `escalation_routing`."""
    root = ElementTree.parse(DMN).getroot()
    decisoes = [d for d in root.findall(f"{D}decision") if d.get("id") == DECISAO]
    assert len(decisoes) == 1
    tabela = decisoes[0].find(f"{D}decisionTable")
    assert tabela is not None
    nomes = [str(o.get("name")) for o in tabela.findall(f"{D}output")]
    valores: dict[str, list[str]] = {n: [] for n in nomes}
    for regra in tabela.findall(f"{D}rule"):
        entradas = regra.findall(f"{D}outputEntry")
        assert len(entradas) == len(nomes)
        for nome, entrada in zip(nomes, entradas, strict=True):
            texto = entrada.find(f"{D}text")
            valores[nome].append("" if texto is None or texto.text is None else texto.text.strip())
    return valores


def _saidas_iso_do_contrato() -> set[str]:
    """Saidas da tabela `### `escalation_routing`` do contrato tipadas como ISO 8601."""
    texto = CONTRATO.read_text(encoding="utf-8")
    inicio = texto.index("### `escalation_routing`")
    fim = texto.index("\n## ", inicio)
    saidas: set[str] = set()
    for linha in texto[inicio:fim].splitlines():
        celulas = [c.strip() for c in linha.strip().strip("|").split("|")]
        if len(celulas) >= 3 and celulas[0] == "out" and "ISO 8601" in celulas[2]:
            saidas.add(celulas[1].strip("`"))
    return saidas


def test_ut_tratar_escalonamento_tem_due_date() -> None:
    assert _due_date()


def test_due_date_usa_so_variaveis_declaradas() -> None:
    match = _EXPR.match(_due_date())
    assert match, f"dueDate deve ser ${{<resultVariable>.<saida da DMN>}}, veio {_due_date()!r}"
    raiz, atributo = match.groups()

    rotas = [el for el in _processo().iter(f"{B}businessRuleTask") if el.get(f"{C}decisionRef") == DECISAO]
    assert len(rotas) == 1, f"o BPMN deve chamar {DECISAO} em exatamente uma businessRuleTask"
    assert rotas[0].get(f"{C}mapDecisionResult") == "singleResult"
    assert raiz == rotas[0].get(f"{C}resultVariable"), (
        "a raiz do dueDate nao e' o resultVariable da DMN escalation_routing"
    )
    assert atributo in _saidas_dmn(), f"{atributo!r} nao e' saida da DMN {DECISAO}"
    assert atributo in _saidas_iso_do_contrato(), (
        f"{atributo!r} nao esta declarado como saida ISO 8601 da DMN no contrato "
        "(docs/processes/contracts/SP-OP-ESCALATION-001.md, ### escalation_routing)"
    )


def test_due_date_e_o_mesmo_prazo_do_timer_de_resolucao() -> None:
    timer = _por_id(_processo(), "TD_SlaResolucao")
    duracao = timer.find(f"{B}timeDuration")
    assert duracao is not None and duracao.text is not None
    assert _por_id(_processo(), "BT_SlaResolucao").get("attachedToRef") == TAREFA
    assert _due_date() == duracao.text.strip(), (
        "dueDate e timer TD_SlaResolucao divergem — o prazo exibido deixaria de ser o prazo "
        "que o motor cobra (DL-0145)"
    )


def test_toda_saida_possivel_da_dmn_e_duracao_iso() -> None:
    match = _EXPR.match(_due_date())
    assert match
    valores = _saidas_dmn()[match.group(2)]
    assert valores, "DMN sem regras"
    invalidos = [v for v in valores if not _DURACAO.match(v)]
    assert not invalidos, f"saidas que o motor recusaria em dueDate (ENGINE-09027): {invalidos}"


def test_supervisor_assume_continua_sem_prazo() -> None:
    assert _por_id(_processo(), "UT_SupervisorAssume").get(f"{C}dueDate") is None
