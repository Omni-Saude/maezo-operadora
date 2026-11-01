"""Cercas de autonomia da FROTA INTEIRA (R-043 / F-054 + F-184; decisao do dono 2026-10-31 em
#1084: "estender o enforcement aos 4 agentes").

Tres familias de guarda, cada uma parametrizada sobre TODOS os agentes de `spec/agents/` (sem
allowlist), cada uma com sondas de mutacao que injetam a violacao e exigem a recusa:

1. ``hard_action`` — nenhum `agent.yaml` pode DECLARAR uma acao `hard` (negativa de cobertura,
   decisao clinica, acusacao de fraude, manutencao de negativa NIP, rescisao contratual: o
   conjunto congelado em codigo `maezo.gateway.pep.HARD_ACTIONS`, AGENTS.md regra dura 7,
   ADR-0008). A recusa e' do GATE `make validate-artifacts` (`agent_def.validate_dir`, o mesmo
   chamado pela CLI): uma acao hard em `autonomy_actions`, no Agent Card (`a2a.capabilities` /
   `skills` / `accepted_task_types`) ou num tool cujo operation catalogado exerce acao hard vira
   erro bloqueante com o nome da acao. A cerca F-054 original
   (`tests/unit/gateway/test_pep_policy_unification.py::test_no_agent_declares_a_hard_action`)
   so' olhava `autonomy_actions` e so' rodava como teste; esta torna a recusa parte do gate de
   artefatos e prova cada caminho por mutacao.
2. ``no_denial`` — alcancabilidade: pelo que o agente declara, nenhuma acao hard nem nenhuma
   operacao de classe ADVERSA (rung C4: emissao de autorizacao, notificacao de negativa,
   acusacao de fraude, mudanca de vinculo contratual, ...) e' alcancavel. Os tools declarados sao
   resolvidos pelo catalogo congelado `maezo.gateway.effect_classes.OPERATIONS` (a mesma tabela
   que o effect-PEP usa em runtime); cada acao declarada passa pelo PEP real (`build_pep()`) e
   nenhuma pode cair em DENY; cada acao hard, pedida com o contexto do agente, cai em DENY; e todo
   agente tem uma rota humana declarada (`escalation.process` no universo ADR-0016 + gatilhos).
3. ``trigger_coverage`` — todo `escalation.triggers` declarado tem >= 1 golden em
   `tests/evals/golden/<agente>/` que o EXERCITA (tag `"triggers"`). Espelho, no lane unitario
   normal, da cerca de evals `tests/evals/test_trigger_coverage.py` — e cross-check de que aquela
   cerca enforca a frota inteira e nao reintroduziu a allowlist `_AGENTS_NOT_YET_ENFORCED`.

Nada aqui define ou altera o conjunto hard nem a matriz de autonomia (`spec/policies/autonomy/`
e' CODEOWNERS): as cercas LEEM `HARD_ACTIONS`/`OPERATIONS`/`build_pep()`.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from types import MappingProxyType
from typing import Any

import pytest
import yaml

from maezo.agents import AgentDefinition, AgentLoader, resolve_spec_agents_dir
from maezo.gateway.effect_classes import ACTION_CLASSES, OPERATIONS, RUNG_C4_ADVERSO, OperationSpec
from maezo.gateway.pep import HARD_ACTIONS, Decision, build_pep
from maezo.platform.validation import agent_def
from maezo.platform.validation.result import Report
from maezo.tools.process_allowlist import KNOWN_PROCESS_KEYS

_REPO = Path(__file__).resolve().parents[3]
_TOOLS_ROOT = _REPO / "src" / "maezo" / "tools"
_GOLDEN_ROOT = _REPO / "tests" / "evals" / "golden"
_EVAL_FENCE = _REPO / "tests" / "evals" / "test_trigger_coverage.py"

_AGENTS_DIR = resolve_spec_agents_dir()
#: A frota = todo diretorio de `spec/agents/` com `agent.yaml`, menos o template. SEM allowlist.
FLEET: tuple[str, ...] = tuple(
    sorted(
        p.name
        for p in _AGENTS_DIR.iterdir()
        if p.is_dir() and p.name != agent_def.TEMPLATE_DIR_NAME and (p / "agent.yaml").exists()
    )
)
HARD: tuple[str, ...] = tuple(sorted(HARD_ACTIONS))


def _definition(agent_id: str) -> AgentDefinition:
    return AgentLoader().load_by_id(agent_id)


def _raw(agent_id: str) -> dict[str, Any]:
    data = yaml.safe_load((_AGENTS_DIR / agent_id / "agent.yaml").read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def _write_agent(root: Path, agent_id: str, data: dict[str, Any]) -> Path:
    path = root / agent_id / "agent.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


def _gate(agents_root: Path) -> Report:
    """O gate de artefatos de agentes, exatamente como `make validate-artifacts` o chama."""
    report = Report()
    agent_def.validate_dir(agents_root, _TOOLS_ROOT, report)
    return report


def _hard_findings(report: Report) -> list[str]:
    return [f.message for f in report.findings if "hard action" in f.message]


# ---------------------------------------------------------------------------------------------
# Nao-vacuidade do universo
# ---------------------------------------------------------------------------------------------


def test_hard_action_fleet_universe_is_not_vacuous() -> None:
    """A frota sai do disco; se o glob devolvesse vazio, toda cerca parametrizada abaixo passaria
    sem testar nada. Os 10 agentes de hoje, e os 5 itens hard congelados."""
    assert len(FLEET) >= 10, FLEET
    assert {"carolina", "helena", "lucas", "rafael"} <= set(FLEET)
    assert {"authorization_denial", "clinical_decision", "fraud_accusation"} <= HARD_ACTIONS
    assert len(HARD) == 5


# ---------------------------------------------------------------------------------------------
# 1. hard_action — o gate de artefatos RECUSA agent.yaml que declara acao hard
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("agent_id", FLEET)
def test_hard_action_real_agent_yaml_declares_none(agent_id: str, tmp_path: Path) -> None:
    """O `agent.yaml` real de cada agente passa o gate sem NENHUM achado de acao hard."""
    _write_agent(tmp_path, agent_id, _raw(agent_id))
    report = _gate(tmp_path)
    assert _hard_findings(report) == []


@pytest.mark.parametrize("hard_action", HARD)
@pytest.mark.parametrize("agent_id", FLEET)
def test_hard_action_in_autonomy_actions_is_refused_by_the_gate(
    agent_id: str, hard_action: str, tmp_path: Path
) -> None:
    """MUTACAO: injetar cada acao hard em `autonomy_actions` de cada agente -> o gate reprova,
    nomeando a acao. 10 agentes x 5 acoes; nenhum caminho silencioso."""
    data = _raw(agent_id)
    data["autonomy_actions"] = [*(data.get("autonomy_actions") or []), hard_action]
    path = _write_agent(tmp_path, agent_id, data)
    report = _gate(tmp_path)
    assert not report.ok
    assert any(
        f.path == path and f"autonomy_actions declares the hard action '{hard_action}'" in f.message
        for f in report.findings
    ), report.findings


@pytest.mark.parametrize("hard_action", HARD)
@pytest.mark.parametrize("field", ["capabilities", "skills", "accepted_task_types"])
def test_hard_action_advertised_in_the_a2a_card_is_refused(
    field: str, hard_action: str, tmp_path: Path
) -> None:
    """MUTACAO: uma acao hard anunciada no Agent Card (o que o agente diz que FAZ a outros
    agentes) e' recusada como em `autonomy_actions`."""
    data = _raw("rafael")
    a2a = dict(data["a2a"])
    a2a[field] = [*(a2a.get(field) or []), hard_action]
    data["a2a"] = a2a
    _write_agent(tmp_path, "rafael", data)
    assert f"a2a.{field} declares the hard action '{hard_action}'" in " | ".join(
        _hard_findings(_gate(tmp_path))
    )


@pytest.mark.parametrize("spelling", ["Authorization_Denial", "fraud-accusation", " clinical_decision "])
def test_hard_action_spelling_variant_does_not_slip_past_the_gate(spelling: str, tmp_path: Path) -> None:
    """MUTACAO: caixa, hifen ou espaco nao disfarcam uma acao hard."""
    data = _raw("helena")
    data["autonomy_actions"] = [*data["autonomy_actions"], spelling]
    _write_agent(tmp_path, "helena", data)
    assert _hard_findings(_gate(tmp_path)), "variante de grafia passou pelo gate"


@pytest.mark.parametrize("agent_id", FLEET)
def test_hard_action_through_a_declared_tool_is_refused(
    agent_id: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """MUTACAO no catalogo: se um tool que o agente declara passasse a exercer uma acao hard
    (`OPERATIONS[...].autonomy_action`), o gate reprova o agente nomeando tool e acao. Hoje
    nenhum operation catalogado mapeia para acao hard (prova abaixo, `no_denial`)."""
    definition = _definition(agent_id)
    catalogued = [s for s in OPERATIONS.values() if s.tool_id in definition.tools]
    assert catalogued, f"{agent_id} nao declara nenhum tool catalogado — mutacao seria vacua"
    alvo = catalogued[0]
    mutated = dict(OPERATIONS)
    mutated[alvo.operation] = OperationSpec(
        operation=alvo.operation,
        action_class=alvo.action_class,
        autonomy_action="authorization_denial",
        tool_id=alvo.tool_id,
    )
    monkeypatch.setattr(agent_def, "OPERATIONS", MappingProxyType(mutated))
    _write_agent(tmp_path, agent_id, _raw(agent_id))
    assert f"tool '{alvo.tool_id}' exercises the hard action 'authorization_denial'" in " | ".join(
        _hard_findings(_gate(tmp_path))
    )


def test_hard_action_refusal_is_wired_into_validate_file() -> None:
    """A recusa mora no caminho do gate (`validate_file`), nao numa funcao solta: neutralizar a
    chamada faz as mutacoes acima passarem a reprovar (prova manual no corpo do PR)."""
    source = Path(agent_def.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    validate_file = next(
        n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "validate_file"
    )
    called = {
        c.func.id for c in ast.walk(validate_file) if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)
    }
    assert "_validate_no_hard_action" in called


# ---------------------------------------------------------------------------------------------
# 2. no_denial — nenhuma acao hard / operacao adversa alcancavel pelo que o agente declara
# ---------------------------------------------------------------------------------------------


def _no_denial_violations(
    definition: AgentDefinition, operations: MappingProxyType[str, OperationSpec]
) -> list[str]:
    """Tudo o que o agente alcanca pelos tools/acoes declarados e que seria negativa/adverso."""
    violations: list[str] = []
    for spec in operations.values():
        if spec.tool_id is None or spec.tool_id not in definition.tools:
            continue
        if spec.autonomy_action in HARD_ACTIONS:
            violations.append(f"tool {spec.tool_id} -> acao hard {spec.autonomy_action}")
        klass = ACTION_CLASSES.get(spec.action_class)
        if klass is None or klass.rung == RUNG_C4_ADVERSO:
            violations.append(f"tool {spec.tool_id} -> classe adversa/desconhecida {spec.action_class}")
    violations.extend(f"autonomy_action hard {a}" for a in definition.autonomy_actions if a in HARD_ACTIONS)
    return violations


@pytest.mark.parametrize("agent_id", FLEET)
def test_no_denial_declared_surface_reaches_no_hard_or_adverse_operation(agent_id: str) -> None:
    assert _no_denial_violations(_definition(agent_id), OPERATIONS) == []


@pytest.mark.parametrize("agent_id", FLEET)
def test_no_denial_mutation_adverse_class_behind_a_declared_tool_is_detected(agent_id: str) -> None:
    """MUTACAO: um tool declarado que passasse a cair numa classe C4 (adversa) e' detectado."""
    definition = _definition(agent_id)
    alvo = next(s for s in OPERATIONS.values() if s.tool_id in definition.tools)
    adversa = next(n for n, c in ACTION_CLASSES.items() if c.rung == RUNG_C4_ADVERSO)
    mutated = dict(OPERATIONS)
    mutated[alvo.operation] = OperationSpec(
        operation=alvo.operation,
        action_class=adversa,
        autonomy_action=alvo.autonomy_action,
        tool_id=alvo.tool_id,
    )
    assert _no_denial_violations(definition, MappingProxyType(mutated)) == [
        f"tool {alvo.tool_id} -> classe adversa/desconhecida {adversa}"
    ]


@pytest.mark.parametrize("hard_action", HARD)
@pytest.mark.parametrize("agent_id", FLEET)
def test_no_denial_mutation_hard_autonomy_action_is_detected(agent_id: str, hard_action: str) -> None:
    definition = _definition(agent_id)
    mutated = definition.model_copy(update={"autonomy_actions": [*definition.autonomy_actions, hard_action]})
    assert _no_denial_violations(mutated, OPERATIONS) == [f"autonomy_action hard {hard_action}"]


@pytest.mark.parametrize("agent_id", FLEET)
def test_no_denial_every_declared_action_resolves_without_deny(agent_id: str) -> None:
    """Cada acao declarada passa pelo PEP real: nenhuma e' hard/desconhecida (DENY)."""
    pep = build_pep()
    definition = _definition(agent_id)
    ctx = {"agent_id": agent_id, "level": definition.autonomy_level}
    denied = [a for a in definition.autonomy_actions if pep.evaluate(a, agent_context=ctx) is Decision.DENY]
    assert denied == [], f"{agent_id} declara acao(oes) que o PEP nega: {denied}"


@pytest.mark.parametrize("hard_action", HARD)
@pytest.mark.parametrize("agent_id", FLEET)
def test_no_denial_hard_action_requested_with_agent_context_is_denied(
    agent_id: str, hard_action: str
) -> None:
    """Recusa tipada em runtime: pedida por QUALQUER agente, com o nivel que ele declara, a acao
    hard e' `Decision.DENY` — o agente nunca executa negativa/decisao clinica/acusacao."""
    definition = _definition(agent_id)
    ctx = {"agent_id": agent_id, "level": definition.autonomy_level, "tenant": "amh"}
    assert build_pep().evaluate(hard_action, agent_context=ctx) is Decision.DENY


@pytest.mark.parametrize("agent_id", FLEET)
def test_no_denial_every_agent_declares_a_human_route(agent_id: str) -> None:
    """Quando o agente nao pode seguir, ele ESCALA a um humano (nunca nega): todo agente declara
    `escalation.process` no universo ADR-0016 e ao menos um gatilho."""
    escalation = _definition(agent_id).escalation
    assert escalation.get("process") in KNOWN_PROCESS_KEYS, escalation.get("process")
    assert escalation.get("triggers"), f"{agent_id} sem escalation.triggers"


# ---------------------------------------------------------------------------------------------
# 3. trigger_coverage — todo gatilho declarado tem golden que o exercita (frota inteira)
# ---------------------------------------------------------------------------------------------


def _canonical_triggers(definition: AgentDefinition) -> list[str]:
    canonical: list[str] = []
    for entry in definition.escalation.get("triggers") or []:
        assert isinstance(entry, dict) and len(entry) == 1, entry
        ((kind, value),) = entry.items()
        canonical.append(f"{kind}:{value}")
    return canonical


def _golden_cases(agent_id: str) -> list[dict[str, Any]]:
    return [
        json.loads(p.read_text(encoding="utf-8")) for p in sorted((_GOLDEN_ROOT / agent_id).glob("*.json"))
    ]


def _uncovered(declared: list[str], cases: list[dict[str, Any]]) -> list[str]:
    tagged = {t for case in cases for t in (case.get("triggers") or [])}
    return [t for t in declared if t not in tagged]


def _all_declared_triggers() -> list[tuple[str, str]]:
    return [(a, t) for a in FLEET for t in _canonical_triggers(_definition(a))]


@pytest.mark.parametrize("agent_id", FLEET)
def test_trigger_coverage_every_declared_trigger_has_a_golden(agent_id: str) -> None:
    declared = _canonical_triggers(_definition(agent_id))
    assert declared, f"{agent_id} nao declara gatilho — cerca vacua"
    assert _uncovered(declared, _golden_cases(agent_id)) == []


@pytest.mark.parametrize("agent_id", FLEET)
def test_trigger_coverage_golden_tags_name_declared_triggers(agent_id: str) -> None:
    declared = set(_canonical_triggers(_definition(agent_id)))
    stale = {
        case["id"]: unknown
        for case in _golden_cases(agent_id)
        if (unknown := [t for t in (case.get("triggers") or []) if t not in declared])
    }
    assert stale == {}, f"tag(s) de golden sem gatilho declarado em {agent_id}: {stale}"


@pytest.mark.parametrize(("agent_id", "trigger"), _all_declared_triggers(), ids=lambda v: str(v))
def test_trigger_coverage_mutation_dropping_the_tag_turns_red(agent_id: str, trigger: str) -> None:
    """MUTACAO por gatilho: tirar a tag de TODO golden que prova `trigger` deixa exatamente ele
    descoberto — cada gatilho da frota tem prova propria, nenhum passa por acidente."""
    cases = [
        {**c, "triggers": [t for t in (c.get("triggers") or []) if t != trigger]}
        for c in _golden_cases(agent_id)
    ]
    assert _uncovered(_canonical_triggers(_definition(agent_id)), cases) == [trigger]


def test_trigger_coverage_eval_fence_enforces_the_whole_fleet() -> None:
    """A cerca de evals enforca TODOS os agentes com golden e nao tem mais allowlist: lido por
    AST (sem importar o modulo de evals e o seu conftest)."""
    tree = ast.parse(_EVAL_FENCE.read_text(encoding="utf-8"))
    assigned: dict[str, Any] = {}
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
            assigned[node.target.id] = node.value
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    assigned[target.id] = node.value
    assert "_AGENTS_NOT_YET_ENFORCED" not in assigned, "allowlist de agentes nao-enforcados voltou"
    enforced = set(ast.literal_eval(assigned["_AGENTS_ENFORCED"]))
    golden_agents = {p.name for p in _GOLDEN_ROOT.iterdir() if p.is_dir()}
    assert enforced == golden_agents == set(FLEET)
