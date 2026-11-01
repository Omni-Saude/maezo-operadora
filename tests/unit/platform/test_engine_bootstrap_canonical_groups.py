"""R-041 / DEC-21 — todo grupo para onde o corpus BPMN/DMN pode mandar uma User Task existe
no bootstrap do engine, e o modelo de grupos decidido pelo dono (#1084, rodadas 2 e 3) esta
refletido nos `candidateGroups`.

Fence offline (texto + parse XML). Nao ratifica nome nenhum: o mapeamento grupo -> cargo da
operadora continua PROPOSTO na tabela de taxonomia (`nome_real` vazio ate a nomeacao do dono).
"""

from __future__ import annotations

from pathlib import Path

import yaml

from maezo.platform.engine_bootstrap import bootstrap as b
from maezo.platform.xml_safe import parse_xml_without_dtd

ROOT = Path(__file__).resolve().parents[3]
BPMN_DIR = ROOT / "spec/processes/bpmn"
DMN_DIR = ROOT / "spec/processes/dmn"
TAXONOMY = ROOT / "docs/sme-dispatch/po/org-taxonomy-table.yaml"
CAMUNDA = "{http://camunda.org/schema/1.0/bpmn}"
BPMN_NS = "{http://www.omg.org/spec/BPMN/20100524/MODEL}"

#: Expressao de candidateGroups -> (DMN, coluna de saida) que a alimenta.
DYNAMIC_SOURCES = {
    "${grupo_humano}": ("nip_routing", "grupo_humano"),
    "${pagto_alcada.grupo_aprovador}": ("pagto_alcada", "grupo_aprovador"),
    "${roteamento.grupo_atendimento}": ("escalation_routing", "grupo_atendimento"),
    "${roteamento_dsr.grupo_revisor}": ("lgpd_dsr_routing", "grupo_revisor"),
    "${grupo_decisao}": ("suppression_routing", "grupo_decisao"),
}

#: Rotulo de DMN que NUNCA vira fila: a faixa clerical do pagto_alcada nao tem User Task.
DMN_ONLY_LABELS = frozenset({"clerical-pagamentos"})

#: As 4 tarefas adversas de decisor unico desta entrega (decisao do dono #1084, rodada 2
#: "Remover co-grupos nas 5 tarefas adversas"). A 5a (NIP UT_RevisaoJuridicaNip) fica para o
#: pacote NIP, que precisa entregar a cossinatura medica junto (ver taxonomia).
SOLE_DECIDER = {
    ("SP-OP-CRED-001", "UT_AnaliseDescredenciamento"): "juridico-rede",
    ("SP-OP-CANCEL-001", "UT_AnaliseRescisao"): "juridico-contratos",
    ("SP-OP-INADIMPLENCIA-001", "UT_AnaliseInadimplencia"): "juridico-contratos",
    ("SP-OP-PROGRAMA-001", "UT_DecisaoClinica"): "coordenacao-clinica",
}

APPROVAL_GROUPS = frozenset(
    {
        "coordenacao-financeira",
        "aprovacao-financeira-l1",
        "aprovacao-financeira-l2",
        "aprovacao-financeira-l3",
        "comite-financeiro",
    }
)


def _user_tasks() -> dict[tuple[str, str], str]:
    tasks: dict[tuple[str, str], str] = {}
    for path in sorted(BPMN_DIR.glob("*.bpmn")):
        root = parse_xml_without_dtd(path.read_bytes())
        for process in root.iter(f"{BPMN_NS}process"):
            for task in process.iter(f"{BPMN_NS}userTask"):
                tasks[(process.get("id", ""), task.get("id", ""))] = task.get(f"{CAMUNDA}candidateGroups", "")
    return tasks


def _dmn_output_literals(table: str, output: str) -> set[str]:
    root = parse_xml_without_dtd((DMN_DIR / f"{table}.dmn").read_bytes())
    values: set[str] = set()
    for decision in root.iter():
        if not isinstance(decision.tag, str) or not decision.tag.endswith("}decisionTable"):
            continue
        outputs = [o for o in decision if isinstance(o.tag, str) and o.tag.endswith("}output")]
        index = [o.get("name") for o in outputs].index(output)
        for rule in decision:
            if not isinstance(rule.tag, str) or not rule.tag.endswith("}rule"):
                continue
            entries = [e for e in rule if isinstance(e.tag, str) and e.tag.endswith("}outputEntry")]
            text = next(t for t in entries[index] if isinstance(t.tag, str) and t.tag.endswith("}text"))
            values.add((text.text or "").strip().strip('"'))
    return values


def _routable_groups() -> set[str]:
    groups: set[str] = set()
    for value in _user_tasks().values():
        if "${" in value:
            table, output = DYNAMIC_SOURCES[value]
            groups |= _dmn_output_literals(table, output) - DMN_ONLY_LABELS
        elif value:
            groups |= {g.strip() for g in value.split(",")}
    return groups


def _bootstrap_ids() -> list[str]:
    return [gid for gid, _ in b.GRUPOS_CANONICOS]


def test_every_routable_group_is_created_by_the_bootstrap() -> None:
    """Uma tarefa num grupo que o engine nao tem e uma tarefa que nenhuma fila mostra."""
    missing = _routable_groups() - set(_bootstrap_ids())
    assert not missing, f"grupos roteaveis sem criacao no bootstrap: {sorted(missing)}"


def test_bootstrap_registers_the_39_groups_of_the_owner_model_once() -> None:
    ids = _bootstrap_ids()
    # 38 do modelo do dono (R-041, #1084) + `financeiro-reembolso` (DL-0160, #1074: UT_RegistrarPagamento).
    assert len(ids) == len(set(ids)) == 39
    assert {gid for gid, _ in b.GRUPOS_DE_ATENDIMENTO} <= set(ids)
    # Novos (#1084): divisao de coordenacao-financeira, escalacao NIP, ouvidoria (RN 623 art. 16).
    assert {"analise-contas-pagar", "gerencia-regulatoria", "ouvidoria"} <= set(ids)
    # Fundido (nucleo-ans -> regulatorio-ans), removidos de candidateGroups e rotulo so de DMN.
    assert not {"nucleo-ans", "equipe-cuidado", "gestao-cobranca", "clerical-pagamentos"} & set(ids)
    for gid, nome in b.GRUPOS_CANONICOS:
        assert nome.strip(), gid


def test_bootstrap_ids_pass_the_engine_group_whitelist_pattern() -> None:
    import re

    script = (ROOT / "deploy/cibseven/configure-group-whitelist.sh").read_text()
    assert "[a-zA-Z0-9]+(-[a-zA-Z0-9]+)*" in script
    pattern = re.compile(r"[a-zA-Z0-9]+(-[a-zA-Z0-9]+)*")
    assert all(pattern.fullmatch(gid) and len(gid) <= 64 for gid in _bootstrap_ids())


def test_adverse_single_decider_tasks_have_exactly_the_decider_group() -> None:
    tasks = _user_tasks()
    for key, group in SOLE_DECIDER.items():
        assert tasks[key] == group, f"{key}: {tasks[key]!r} != {group!r}"


def test_payment_admissibility_is_segregated_from_every_approval_group() -> None:
    tasks = _user_tasks()
    admissibilidade = tasks[("SP-OP-PAGTO-001", "UT_AnaliseAdmissibilidade")]
    assert admissibilidade == "analise-contas-pagar"
    assert tasks[("SP-OP-PAGTO-001", "UT_CoordenacaoAlcada")] == "coordenacao-financeira"
    assert admissibilidade not in APPROVAL_GROUPS


def test_escalation_group_is_not_co_candidate_of_the_first_instance_fallback() -> None:
    tasks = _user_tasks()
    assert tasks[("SP-OP-ADEQUACAO-001", "UT_DecisaoFallback")] == "gestao-rede"
    assert tasks[("SP-OP-ADEQUACAO-001", "UT_CoordenacaoRede")] == "coordenacao-rede"


def test_taxonomy_registry_mirrors_the_bootstrap() -> None:
    data = yaml.safe_load(TAXONOMY.read_text(encoding="utf-8"))
    registry = data["grupos_registrados"]
    assert [g["id"] for g in registry] == _bootstrap_ids()
    for entry in registry:
        assert entry["cargo_mapeado"].strip(), entry["id"]
        assert entry["decisao"] in {"CONFIRMAR", "NOVO"}, entry["id"]
        assert entry["status"].startswith("PROPOSTO"), entry["id"]
    nao = {g["id"] for g in data["grupos_nao_registrados"]}
    assert nao == {"nucleo-ans", "equipe-cuidado", "gestao-cobranca", "clerical-pagamentos"}


#: Estado CONGELADO da 5a tarefa adversa ate o pacote NIP (R-041 sec. 3): MANTER_NEGATIVA clinica
#: exige medico-auditor; nenhum grupo nao-decisor (p.ex. o redator regulatorio-ans, SoD S8) entra.
NIP_DEFERRED_FROZEN = {
    ("SP-OP-NIP-001", "UT_RevisaoJuridicaNip"): frozenset({"juridico-regulatorio", "medico-auditor"}),
    ("SP-OP-NIP-001", "UT_CoordenacaoNip"): frozenset({"coordenacao-regulatorio", "juridico-regulatorio"}),
}


def test_nip_adverse_task_stays_frozen_until_the_nip_package() -> None:
    tasks = _user_tasks()
    for key, groups in NIP_DEFERRED_FROZEN.items():
        got = frozenset(g.strip() for g in tasks[key].split(","))
        assert got == groups, f"{key}: {sorted(got)} != {sorted(groups)} (pacote NIP, R-041 sec. 3)"
