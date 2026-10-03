"""Cerca de derivacao (IMPL-4): as enumeracoes do emissor de escalonamento vem do BPMN.

`PROCESS_KEY`, `TASK_KEY`, `SUPERVISOR_TASK_KEY`, `SUPERVISOR_GROUP` e `LIVE_TASK_KEYS`
(`src/maezo/gateway/staff_cases/case_issuer_sources.py`) e `ESCALATION_PROCESS_KEY`/`TASK_STAGES`
(`src/maezo/gateway/human/escalation_context.py`) sao copias MANUAIS do que
`spec/processes/bpmn/SP-OP-ESCALATION-001_Escalonamento_Humano_Universal.bpmn` declara — e
nenhum teste derivava esses conjuntos. A proxima evolucao do BPMN (uma userTask a mais ou a
menos, um candidate group trocado) dessincronizaria o emissor EM SILENCIO: ele consultaria o
engine por `taskDefinitionKey` que o processo nao tem mais, ou deixaria de consultar a linha
humana nova (a classe de defeito do #549). Este fence deriva os conjuntos DIRETO do BPMN, com o
mesmo parsing de
`tests/unit/portal/test_remaining_auth_form_contracts.py::test_all_43_actual_bpmn_tasks_have_closed_source_shapes_only`
(ElementTree, namespace BPMN), e cada falha nomeia o conserto.

O que NAO e coberto aqui: o CONTEUDO contratual das tarefas (formData, SLAs, timers) — esse e o
territorio dos testes de forma (`test_remaining_auth_form_contracts.py`) e dos testes de
`escalation_context`. Aqui so a IDENTIDADE das enumeracoes vivas.
"""

from __future__ import annotations

from pathlib import Path
from xml.etree import ElementTree

from maezo.gateway.human.escalation_context import ESCALATION_PROCESS_KEY, TASK_STAGES
from maezo.gateway.staff_cases import case_issuer_sources

BPMN = (
    Path(__file__).resolve().parents[3]
    / "spec/processes/bpmn/SP-OP-ESCALATION-001_Escalonamento_Humano_Universal.bpmn"
)
#: Namespace BPMN 2.0 (`<bpmn:definitions xmlns:bpmn=...>` no arquivo) e o namespace de
#: extensao do Camunda, onde vive `camunda:candidateGroups`.
BPMN_NS = {"b": "http://www.omg.org/spec/BPMN/20100524/MODEL"}
CAMUNDA_NS = "http://camunda.org/schema/1.0/bpmn"

#: O conserto que o fence exige quando o BPMN e as enumeracoes divergem (nomeia ARQUIVO, nao
#: "algum lugar"): as duas fontes de verdade tem de voltar a casar.
CONSERTO_ENUMERACAO = (
    "BPMN ganhou/perdeu userTask — atualize LIVE_TASK_KEYS/TASK_STAGES em "
    "src/maezo/gateway/staff_cases/case_issuer_sources.py e "
    "src/maezo/gateway/human/escalation_context.py"
)


def _bpmn() -> tuple[str, dict[str, str | None]]:
    """Deriva do BPMN: (id do `bpmn:process`, {id da userTask: `camunda:candidateGroups`}).

    `candidateGroups` e `None` quando a userTask nao declara o atributo — a ausencia e um fato
    que os testes abaixo julgam, nunca um KeyError.
    """
    tree = ElementTree.parse(BPMN)
    processos = tree.findall("b:process", BPMN_NS)
    assert len(processos) == 1, f"{BPMN.name} deve conter EXATAMENTE um bpmn:process"
    tasks = {
        task.attrib["id"]: task.get(f"{{{CAMUNDA_NS}}}candidateGroups")
        for task in processos[0].findall(".//b:userTask", BPMN_NS)
    }
    return str(processos[0].attrib["id"]), tasks


def test_live_task_keys_sao_as_user_tasks_do_bpmn() -> None:
    """O emissor consulta o engine por EXATAMENTE as userTasks que o processo declara."""
    process_id, tasks = _bpmn()
    assert process_id == case_issuer_sources.PROCESS_KEY, (
        "o BPMN renomeou o processo — atualize PROCESS_KEY em "
        "src/maezo/gateway/staff_cases/case_issuer_sources.py"
    )
    assert set(tasks) == set(case_issuer_sources.LIVE_TASK_KEYS), CONSERTO_ENUMERACAO


def test_task_stages_e_fechado_nas_mesmas_user_tasks() -> None:
    """O contexto do atendente conhece as MESMAS linhas humanas que o emissor emite."""
    assert ESCALATION_PROCESS_KEY == case_issuer_sources.PROCESS_KEY, (
        "os dois modulos nomeiam o processo de escalonamento de forma divergente — atualize "
        "ESCALATION_PROCESS_KEY em src/maezo/gateway/human/escalation_context.py ou PROCESS_KEY "
        "em src/maezo/gateway/staff_cases/case_issuer_sources.py"
    )
    assert set(TASK_STAGES) == set(case_issuer_sources.LIVE_TASK_KEYS), CONSERTO_ENUMERACAO


def test_supervisor_group_e_o_literal_candidate_groups_do_bpmn() -> None:
    """Na fase do supervisor o grupo e o LITERAL do BPMN (docstring de `case_issuer_sources.py`).

    `EngineEscalationSource._qualify` substitui a saida da DMN por `SUPERVISOR_GROUP` quando
    `taskDefinitionKey == SUPERVISOR_TASK_KEY` — se o BPMN mudar o literal (ou perder o
    atributo), o emissor passa a exigir um grupo que o engine nao concede e toda escalacao de
    SLA vencido vira recusa.
    """
    _, tasks = _bpmn()
    assert tasks.get(case_issuer_sources.SUPERVISOR_TASK_KEY) == case_issuer_sources.SUPERVISOR_GROUP, (
        "UT_SupervisorAssume esta ausente do BPMN ou seu camunda:candidateGroups diverge de "
        "SUPERVISOR_GROUP — atualize SUPERVISOR_GROUP em "
        "src/maezo/gateway/staff_cases/case_issuer_sources.py (ou o BPMN, se o literal correto "
        "for outro)"
    )


def test_atendimento_segue_roteado_pela_dmn() -> None:
    """O grupo de `UT_TratarEscalonamento` NAO e literal: e a saida da DMN `escalation_routing`.

    `EngineEscalationSource` confere os identity-links da tarefa contra o output registrado no
    historico da decisao — so fecha porque o `candidateGroups` do BPMN resolve para a MESMA
    variavel (`${roteamento.grupo_atendimento}`). Um literal aqui quebraria esse cross-check
    (recusa de tudo, ou pior: um grupo que nunca e o da DMN).
    """
    _, tasks = _bpmn()
    assert tasks.get(case_issuer_sources.TASK_KEY) == "${roteamento.grupo_atendimento}", (
        "UT_TratarEscalonamento deixou de receber o grupo da saida da DMN — o cross-check de "
        "identity-links x historico da decisao em EngineEscalationSource ("
        "src/maezo/gateway/staff_cases/case_issuer_sources.py) pressupoe "
        "`${roteamento.grupo_atendimento}`; atualize o BPMN ou esse cross-check junto"
    )
