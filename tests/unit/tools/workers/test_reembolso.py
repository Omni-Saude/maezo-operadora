"""Unit tests for maezo.tools.workers.reembolso — SP-OP-REEMBOLSO-001.

TDD London School: tests exercise the external task contracts.
"""

import ast
import asyncio
import dataclasses
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import pytest
import structlog

from maezo.tools.workers import reembolso
from maezo.tools.workers.base import FunctionWorker, pick_fields
from maezo.tools.workers.ceilings import CeilingResolver
from maezo.tools.workers.harness import (
    ExternalTask,
    FakeAuditSink,
    FakeKafkaPublisher,
    FakeWorkerTransport,
    WorkerBpmnError,
    WorkerHarness,
)
from maezo.tools.workers.reembolso import (
    ERR_REEMBOLSO_INVALID_PROTOCOLO,
    ORIGEM_PAGAMENTO_AUTO,
    REEMBOLSO_AUTO_PAGAMENTO_LIBERADO,
    REEMBOLSO_BPMN_ERROR_ALLOWLIST,
    VAR_REEMBOLSO_AUTO_LIBERADO,
    ReembolsoAutoPagamentoBloqueadoError,
    ReembolsoCalculoDmn,
    ReembolsoCalculoIndisponivelError,
    ReembolsoDenialInput,
    ReembolsoDenialNotHumanError,
    ReembolsoInput,
    ReembolsoProtocoloInvalidoError,
    ReembolsoValorPagamentoInvalidoError,
    analyze_request,
    analyze_request_entry,
    calculate_amount_entry,
    calculate_value,
    check_coverage,
    check_coverage_entry,
    check_prazo_entry,
    issue_payment_entry,
    notify_sla_risk,
    notify_sla_risk_entry,
    process_payment,
    publish_completed,
    publish_completed_entry,
    register_reembolso_workers,
    request_documents,
    request_documents_entry,
    send_reembolso_denial,
    send_reembolso_denial_entry,
    validate_reembolso,
)

# tests/unit/tools/workers/<file> -> parents[4] == repo root (same idiom as
# test_auth_denial_guard.py / test_auth_auto_criteria.py).
_REPO_ROOT = Path(__file__).resolve().parents[4]

# Synthetic-core helper (mirrors test_ceilings) — pins the reembolso ceiling in isolation so
# these tests do not depend on the D-07 value in the real spec matrix.
_HARD_BLOCK = """\
  clinical_decision:      { level: L0, hard: true }
  authorization_denial:   { level: L0, hard: true }
  nip_manter_negativa:    { level: L0, hard: true }
  fraud_accusation:       { level: L0, hard: true }
  contract_termination:   { level: L0, hard: true }
"""


def _pin_resolver(tmp_path: Path, max_value_brl: int) -> CeilingResolver:
    """Return a CeilingResolver pinned to a synthetic core with a given reembolso ceiling.

    ``tmp_path`` may be a not-yet-created SUBDIRECTORY of the fixture: the matrix load is
    ``lru_cache``d by resolved path, so a test that needs two different ceilings must pin them
    to two different paths.
    """
    tmp_path.mkdir(parents=True, exist_ok=True)
    core = tmp_path / "L0-core.yaml"
    core.write_text(
        "version: 1\nactions:\n"
        f"{_HARD_BLOCK}"
        f"  reembolso_auto_approval: {{ level: L2, params: {{ max_value_brl: {max_value_brl} }} }}\n",
        encoding="utf-8",
    )
    return CeilingResolver(core_path=core)


# ---------------------------------------------------------------------------
# validate_reembolso
# ---------------------------------------------------------------------------


def test_validate_reembolso_valid() -> None:
    """validate_reembolso accepts a valid reembolso."""
    inp = ReembolsoInput(
        tenant_id="amh",
        protocolo_reembolso="REEMB-001",
        codigo_procedimento_tuss="10101012",
        valor_solicitado_cents=35000,
        cobertura_prevista=True,
        documentacao_completa=True,
        dentro_prazo=True,
        beneficiario_ativo=True,
        carencia_cumprida=True,
    )
    result = validate_reembolso(inp)
    assert result.valid is True
    assert result.errors == []


def test_validate_reembolso_missing_protocolo() -> None:
    """validate_reembolso raises ERR_REEMBOLSO_INVALID_PROTOCOLO on missing protocolo."""
    inp = ReembolsoInput(
        tenant_id="amh",
        protocolo_reembolso="",
        codigo_procedimento_tuss="10101012",
    )
    with pytest.raises(ReembolsoProtocoloInvalidoError):
        validate_reembolso(inp)


def test_validate_reembolso_missing_procedimento() -> None:
    """validate_reembolso flags missing codigo_procedimento_tuss."""
    inp = ReembolsoInput(
        tenant_id="amh",
        protocolo_reembolso="REEMB-002",
        codigo_procedimento_tuss="",
        valor_solicitado_cents=10000,
    )
    result = validate_reembolso(inp)
    assert any("procedimento" in e.lower() or "tuss" in e.lower() for e in result.errors)


def test_validate_reembolso_zero_value() -> None:
    """validate_reembolso flags valor_solicitado_cents <= 0."""
    inp = ReembolsoInput(
        tenant_id="amh",
        protocolo_reembolso="REEMB-003",
        codigo_procedimento_tuss="10101012",
        valor_solicitado_cents=0,
    )
    result = validate_reembolso(inp)
    assert any("valor" in e.lower() for e in result.errors)


# ---------------------------------------------------------------------------
# check_coverage
# ---------------------------------------------------------------------------


def test_check_coverage_prevista() -> None:
    """check_coverage returns cobertura_prevista flag."""
    inp = ReembolsoInput(
        tenant_id="amh",
        protocolo_reembolso="REEMB-COV-1",
        codigo_procedimento_tuss="10101012",
        tipo_reembolso="livre_escolha",
        cobertura_prevista=True,
    )
    result = check_coverage(inp)
    assert result["cobertura_prevista"] is True


def test_check_coverage_nao_prevista() -> None:
    """check_coverage returns False for uncovered procedures."""
    inp = ReembolsoInput(
        tenant_id="amh",
        protocolo_reembolso="REEMB-COV-2",
        codigo_procedimento_tuss="99999999",
        tipo_reembolso="livre_escolha",
        cobertura_prevista=False,
    )
    result = check_coverage(inp)
    assert result["cobertura_prevista"] is False


def test_check_coverage_raises_workerbpmnerror_on_blank_protocolo() -> None:
    """Root-cause proof (ADR-0030 Tier-2, WP-ADR-0030-COMPLETION D3-01): check_coverage — the
    ACTUAL boundary-carrying task (ST_CheckCoverage / BE_ReembolsoProtocoloInvalido) — raises the
    MODELED WorkerBpmnError when protocolo_reembolso is blank/absent. Previously the coded
    ReembolsoProtocoloInvalidoError was raised on the WRONG topic (check_prazo/validate_reembolso,
    which carries no boundary at all), so the modeled boundary could never fire."""
    with pytest.raises(WorkerBpmnError) as excinfo:
        check_coverage(ReembolsoInput(tenant_id="amh", protocolo_reembolso=""))
    assert excinfo.value.error_code == ERR_REEMBOLSO_INVALID_PROTOCOLO
    assert not isinstance(excinfo.value, ReembolsoProtocoloInvalidoError)


def test_reembolso_invalid_protocolo_is_gate_proven_and_tier0() -> None:
    assert ERR_REEMBOLSO_INVALID_PROTOCOLO in REEMBOLSO_BPMN_ERROR_ALLOWLIST


# ---------------------------------------------------------------------------
# Boundary REACHABILITY (WP-ADR-0030-COMPLETION, D3-01) — check_coverage raises WorkerBpmnError so
# its modeled BPMN boundary catch (BE_ReembolsoProtocoloInvalido) can fire, instead of the
# coded-exception path (which, raised from the wrong topic, could never reach it). Mutation-minded:
# allowlisted -> boundary (handle_bpmn_error); NOT allowlisted -> the fail-closed incident
# (handle_failure, retries=0). Drives the REAL harness dispatch path (harness._handle) end-to-end,
# no live engine. Mirrors test_credenciamento.py's `_drive_guard_failure`.
# ---------------------------------------------------------------------------


def _drive_check_coverage_failure(variables: dict, *, allowlist: frozenset[str]):
    """Run check_coverage_entry through the real harness; return (bpmn_errors, failures)."""
    transport = FakeWorkerTransport()
    harness = WorkerHarness(
        transport,
        worker_id="reembolso-boundary-test",
        tenant="amh",
        audit_sink=FakeAuditSink(),
        bpmn_error_allowlist=allowlist,
    )
    harness.register_worker(FunctionWorker("operadora.reembolso.check_coverage", check_coverage_entry))
    task = ExternalTask(
        task_id="task-1",
        topic="operadora.reembolso.check_coverage",
        process_instance_id="proc-1",
        business_key="REEMB-amh-1",
        worker_id="reembolso-boundary-test",
        variables=variables,
    )
    asyncio.run(harness._handle(task))
    return transport.bpmn_errors, transport.failures


def test_check_coverage_reaches_boundary_when_allowlisted() -> None:
    """ALLOWLISTED: the origin-validation guard routes to handle_bpmn_error
    (BE_ReembolsoProtocoloInvalido fires), NEVER to a failure/incident — the boundary is now
    REACHABLE at its OWN task (was unreachable at ANY task pre-fix)."""
    bpmn_errors, failures = _drive_check_coverage_failure(
        {"protocolo_reembolso": ""},
        allowlist=frozenset({ERR_REEMBOLSO_INVALID_PROTOCOLO}),
    )
    assert bpmn_errors == [("task-1", ERR_REEMBOLSO_INVALID_PROTOCOLO, bpmn_errors[0][2])]
    assert failures == []


def test_check_coverage_demotes_to_incident_when_not_allowlisted() -> None:
    """NOT ALLOWLISTED: fail-closed by construction — demotes to a failure/incident, never a
    silent scope-end."""
    bpmn_errors, failures = _drive_check_coverage_failure(
        {"protocolo_reembolso": ""},
        allowlist=frozenset(),
    )
    assert bpmn_errors == []
    assert len(failures) == 1
    assert failures[0][0] == "task-1"


# ---------------------------------------------------------------------------
# calculate_value / calculate_amount_entry — GAP F-1: the amount is the DMN's
#
# The worker used to pay from a Python dict (`_BASE_VALUES_CENTS`: consulta 35000,
# exame_especial 45000, ...) plus `_MULTIPLO_ACESSO = 1.5`, which DISAGREED with
# `spec/processes/dmn/reembolso_calculo.dmn` (consulta 12000, exame_especial 25000) — the
# authoritative table that `BRT_Calculo` already evaluates BEFORE this worker runs
# (GAP-REEMBOLSO-5). The worker now CONSUMES that decision's outputs and originates no amount.
#
# THE TESTS BELOW READ THE DMN FILE. Every expected amount is parsed out of
# `reembolso_calculo.dmn` at test time (`_parse_dmn_rules`), never transcribed here: an
# actuarial/regulatory re-tune of the table (an OWNER decision, not this suite's) must move the
# worker's output with it, and a test carrying its own copy of the numbers would go quietly stale
# exactly the way the deleted Python table did.
# ---------------------------------------------------------------------------

_DMN_CALCULO_PATH = _REPO_ROOT / "spec" / "processes" / "dmn" / "reembolso_calculo.dmn"
_BPMN_REEMBOLSO_PATH = (
    _REPO_ROOT / "spec" / "processes" / "bpmn" / "SP-OP-REEMBOLSO-001_Reembolso_Beneficiario.bpmn"
)
_DMN_NS = {"dmn": "https://www.omg.org/spec/DMN/20191111/MODEL/"}
_BPMN_MODEL_NS = "http://www.omg.org/spec/BPMN/20100524/MODEL"
_CAMUNDA_NS = "http://camunda.org/schema/1.0/bpmn"

#: A condicao INTEIRA de `Flow_GW_AutoAprovar` (R-058), montada a partir do nome pinado no worker
#: para que modelo e codigo tenham UMA fonte do nome da variavel. Fixar a expressao completa — e
#: nao apenas os dois termos — e' o que impede a troca silenciosa de `&&` por `||` (§Delta-F1):
#: com `||`, a recomendacao da DMN sozinha voltaria a abrir o caminho automatico.
_CONDICAO_GW_AUTO_APROVAR_ESPERADA = (
    "${auto_aprovacao.recomendacao == 'AUTO_APROVAR' && " + VAR_REEMBOLSO_AUTO_LIBERADO + "}"
)

#: The DMN's three output names, in table order — also the three process variables
#: `ST_CalculateAmount`'s input mapping flattens and the worker reads.
_DMN_OUTPUT_NAMES = ("valor_calculado_tabela_cents", "multiplo_tabela_aplicado", "fonte_tabela")


@dataclasses.dataclass(frozen=True)
class _DmnRule:
    """One parsed row of `dt_reembolso_calculo`. `None` in an input slot is FEEL `-` (any)."""

    rule_id: str
    tipo_reembolso: str | None
    categoria_procedimento: str | None
    valor_calculado_tabela_cents: int
    multiplo_tabela_aplicado: float
    fonte_tabela: str

    @property
    def is_catch_all(self) -> bool:
        return self.tipo_reembolso is None and self.categoria_procedimento is None


def _feel_text(node: Any) -> str:
    return (node.findtext("dmn:text", default="", namespaces=_DMN_NS) or "").strip()


def _feel_literal(text: str) -> str | None:
    """`-` (any) -> None; `"consulta"` -> `consulta`."""
    return None if text == "-" else text.strip('"')


def _parse_dmn_rules() -> list[_DmnRule]:
    """Every rule of `reembolso_calculo.dmn`, read from the file — the single source of the amounts.

    Also PINS the table shape the worker depends on: the two inputs in
    (tipo_reembolso, categoria_procedimento) order and the three outputs in `_DMN_OUTPUT_NAMES`
    order. A column reorder or rename in the DMN fails here loudly instead of silently shifting
    which number the worker relays.
    """
    table = ET.parse(_DMN_CALCULO_PATH).getroot().find("dmn:decision/dmn:decisionTable", _DMN_NS)
    assert table is not None, "decisionTable dt_reembolso_calculo nao encontrada"

    inputs = [
        (el.findtext("dmn:inputExpression/dmn:text", default="", namespaces=_DMN_NS) or "").strip()
        for el in table.findall("dmn:input", _DMN_NS)
    ]
    assert inputs == ["tipo_reembolso", "categoria_procedimento"], inputs
    outputs = tuple(el.get("name", "") for el in table.findall("dmn:output", _DMN_NS))
    assert outputs == _DMN_OUTPUT_NAMES, outputs

    rules: list[_DmnRule] = []
    for rule in table.findall("dmn:rule", _DMN_NS):
        ins = [_feel_text(el) for el in rule.findall("dmn:inputEntry", _DMN_NS)]
        outs = [_feel_text(el) for el in rule.findall("dmn:outputEntry", _DMN_NS)]
        assert len(ins) == 2 and len(outs) == 3, (rule.get("id"), ins, outs)
        rules.append(
            _DmnRule(
                rule_id=rule.get("id", ""),
                tipo_reembolso=_feel_literal(ins[0]),
                categoria_procedimento=_feel_literal(ins[1]),
                valor_calculado_tabela_cents=int(outs[0]),
                multiplo_tabela_aplicado=float(outs[1]),
                fonte_tabela=outs[2].strip('"'),
            )
        )
    assert len(rules) >= 2, "parser vacuo — a DMN precisa de ao menos uma row de tabela + catch-all"
    assert any(r.is_catch_all for r in rules), "reembolso_calculo perdeu a row catch-all fail-safe"
    return rules


_DMN_RULES: list[_DmnRule] = _parse_dmn_rules()
_DMN_RULE_IDS: list[str] = [r.rule_id for r in _DMN_RULES]
_DMN_CATCH_ALL: _DmnRule = next(r for r in _DMN_RULES if r.is_catch_all)


def _rule(rule_id: str) -> _DmnRule:
    return next(r for r in _DMN_RULES if r.rule_id == rule_id)


def _dmn_flat_vars(rule: _DmnRule) -> dict[str, Any]:
    """The three activity-LOCAL variables `ST_CalculateAmount`'s input mapping delivers."""
    return {
        "valor_calculado_tabela_cents": rule.valor_calculado_tabela_cents,
        "multiplo_tabela_aplicado": rule.multiplo_tabela_aplicado,
        "fonte_tabela": rule.fonte_tabela,
    }


def _calculo(rule: _DmnRule) -> ReembolsoCalculoDmn:
    return ReembolsoCalculoDmn(
        valor_calculado_tabela_cents=rule.valor_calculado_tabela_cents,
        multiplo_tabela_aplicado=rule.multiplo_tabela_aplicado,
        fonte_tabela=rule.fonte_tabela,
    )


def _request(**overrides: Any) -> ReembolsoInput:
    base: dict[str, Any] = {
        "tenant_id": "amh",
        "protocolo_reembolso": "REEMB-F1",
        "codigo_procedimento_tuss": "10101012",
        "tipo_reembolso": "livre_escolha",
    }
    base.update(overrides)
    return ReembolsoInput(**base)


def _entry_vars(rule: _DmnRule, **overrides: Any) -> dict[str, Any]:
    """A full `ST_CalculateAmount` payload: the request plus the flattened DMN outputs."""
    variables: dict[str, Any] = {
        "tenant_id": "amh",
        "protocolo_reembolso": f"REEMB-F1-{rule.rule_id}",
        "codigo_procedimento_tuss": "10101012",
        "categoria_procedimento": rule.categoria_procedimento or "",
        "tipo_reembolso": rule.tipo_reembolso or "livre_escolha",
        "valor_solicitado_cents": 1,
        **_dmn_flat_vars(rule),
    }
    variables.update(overrides)
    return variables


@pytest.fixture(scope="module")
def teto_zero(tmp_path_factory: pytest.TempPathFactory) -> CeilingResolver:
    """Resolver pinned to the D-07 ceiling (0) — every request routes to ANALISE_HUMANA."""
    return _pin_resolver(tmp_path_factory.mktemp("teto-zero"), max_value_brl=0)


@pytest.fixture(scope="module")
def teto_alto(tmp_path_factory: pytest.TempPathFactory) -> CeilingResolver:
    """Resolver pinned to a ceiling above every value in the shipped DMN table."""
    return _pin_resolver(tmp_path_factory.mktemp("teto-alto"), max_value_brl=1_000_000)


# --- (a) the worker's output IS the DMN's output, rule by rule -----------------------------


@pytest.mark.parametrize("rule", _DMN_RULES, ids=_DMN_RULE_IDS)
def test_calculate_value_relays_every_dmn_rule_verbatim(rule: _DmnRule, teto_zero: CeilingResolver) -> None:
    """For EVERY row of `reembolso_calculo.dmn`, the worker emits that row's values unchanged.

    Drives the worker with what `BRT_Calculo` writes (the three flattened outputs) and asserts
    identity — no re-derivation, no multiplier re-application, no rounding. The expectations come
    from the parsed file, so the DMN and this assertion cannot drift apart.
    """
    result = calculate_value(
        _request(categoria_procedimento=rule.categoria_procedimento or "", valor_solicitado_cents=1),
        _calculo(rule),
        resolver=teto_zero,
    )
    assert result.valor_calculado_tabela_cents == rule.valor_calculado_tabela_cents
    assert result.multiplo_tabela_aplicado == rule.multiplo_tabela_aplicado
    assert result.fonte_tabela == rule.fonte_tabela
    # money stays integer centavos end-to-end (ADR-0018): never a float, never a bool
    assert type(result.valor_calculado_tabela_cents) is int


@pytest.mark.parametrize("rule", _DMN_RULES, ids=_DMN_RULE_IDS)
def test_calculate_amount_entry_relays_every_dmn_rule_verbatim(rule: _DmnRule) -> None:
    """Same identity across the ENGINE BOUNDARY: the entry function is what ST_CalculateAmount calls.

    The facts only reach `BRT_AutoApproval` (which gates AUTO_APROVAR on `dentro_tabela`) and the
    human dossie if they survive `_require_calculo_dmn` -> `calculate_value` -> `asdict`.
    """
    out = calculate_amount_entry(_entry_vars(rule))
    assert out["valor_calculado_tabela_cents"] == rule.valor_calculado_tabela_cents
    assert out["multiplo_tabela_aplicado"] == rule.multiplo_tabela_aplicado
    assert out["fonte_tabela"] == rule.fonte_tabela


def test_worker_amounts_are_the_dmn_amounts_not_the_deleted_python_table(
    teto_zero: CeilingResolver,
) -> None:
    """THE GAP F-1 REGRESSION: the retired Python amounts must be unreachable.

    `consulta` was 35000 in the deleted `_BASE_VALUES_CENTS` and is 12000 in the DMN;
    `exame_especial` was 45000 and is 25000. Asserted against the PARSED DMN value AND against the
    literal retired numbers, so this bites both if the shadow table comes back and if someone
    "reconciles" the DMN upward to match it without the actuarial sign-off the table is blocked on.
    """
    retired_python_amounts = {"consulta": 35000, "exame_especial": 45000}
    for rule_id, categoria in (("r_consulta", "consulta"), ("r_exame_especial", "exame_especial")):
        rule = _rule(rule_id)
        assert rule.categoria_procedimento == categoria
        result = calculate_value(
            _request(categoria_procedimento=categoria, valor_solicitado_cents=1),
            _calculo(rule),
            resolver=teto_zero,
        )
        assert result.valor_calculado_tabela_cents == rule.valor_calculado_tabela_cents
        assert result.valor_calculado_tabela_cents != retired_python_amounts[categoria], (
            f"{categoria}: the worker is paying the DELETED Python table amount again"
        )


def test_no_python_multiplier_is_applied_to_the_dmn_value(teto_zero: CeilingResolver) -> None:
    """`urgencia_emergencia` / `fora_rede` no longer uplift anything worker-side.

    `_MULTIPLO_ACESSO = 1.5` multiplied the Python base (consulta 35000 -> 52500). The DMN's rows
    are `-` on `tipo_reembolso` and emit `multiplo_tabela_aplicado = 1.0`, so the same request must
    now produce the DMN value UNCHANGED whatever the tipo. Any per-access multiplier is the DMN's
    to express (it is exactly what its DRAFT sign-off covers), never the worker's.
    """
    rule = _rule("r_consulta")
    for tipo in ("livre_escolha", "urgencia_emergencia", "fora_rede"):
        result = calculate_value(
            _request(categoria_procedimento="consulta", tipo_reembolso=tipo, valor_solicitado_cents=1),
            _calculo(rule),
            resolver=teto_zero,
        )
        assert result.valor_calculado_tabela_cents == rule.valor_calculado_tabela_cents, tipo
        assert result.multiplo_tabela_aplicado == rule.multiplo_tabela_aplicado, tipo
        assert result.valor_calculado_tabela_cents != int(35000 * 1.5), tipo


# --- (b) the Python amount tables are GONE -------------------------------------------------


@pytest.mark.parametrize(
    "symbol",
    ["_BASE_VALUES_CENTS", "_MULTIPLO_ACESSO", "_TIPOS_COM_MULTIPLICADOR", "_lookup_tabela_referencia"],
)
def test_python_amount_table_symbols_are_deleted(symbol: str) -> None:
    """No shim, no re-export: the money rule does not live in Python any more (ADR-0012)."""
    assert not hasattr(reembolso, symbol), (
        f"`{symbol}` is back in workers/reembolso.py — the amount must come from "
        "spec/processes/dmn/reembolso_calculo.dmn only (GAP F-1)"
    )


def test_module_declares_no_categoria_keyed_amount_table() -> None:
    """AST scan: a RENAMED clone of the shadow table is caught too.

    `_BASE_VALUES_CENTS` was a dict literal mapping categoria strings to integer centavos. Any
    dict literal of that shape anywhere in the module — module level or inside a function — is a
    business rule re-entering Python against ADR-0012, whatever it is called. Mirrors the
    AST-scanner idiom of `tests/unit/sec/test_dentro_teto_source.py`.
    """
    tree = ast.parse(Path(reembolso.__file__).read_text(encoding="utf-8"))
    offenders: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict) or not node.keys:
            continue
        keys_are_strings = all(isinstance(k, ast.Constant) and isinstance(k.value, str) for k in node.keys)
        values_are_ints = all(
            isinstance(v, ast.Constant) and isinstance(v.value, int) and not isinstance(v.value, bool)
            for v in node.values
        )
        if keys_are_strings and values_are_ints:
            offenders.append(node.lineno)
    assert offenders == [], (
        "workers/reembolso.py declares a string->int table again (lines "
        f"{offenders}) — reimbursement amounts belong to reembolso_calculo.dmn (ADR-0012)"
    )


# --- (c) missing / malformed DMN output fails the money path CLOSED ------------------------


def test_calculate_amount_entry_fails_closed_without_any_dmn_output() -> None:
    """No `calculo`, no flattened variables => incident, never a guessed amount.

    `ST_CalculateAmount` is reachable ONLY from `BRT_Calculo` (`Flow_BRTCalc_Calc`), so absence is
    a model/deploy defect. The safe outcome is to compute NOTHING: with no `dentro_tabela` and no
    `dentro_teto_l2` written, `BRT_AutoApproval` cannot see an AUTO_APROVAR combination and no
    payment can be issued.
    """
    with pytest.raises(ReembolsoCalculoIndisponivelError) as exc:
        calculate_amount_entry(
            {
                "tenant_id": "amh",
                "protocolo_reembolso": "REEMB-F1-NODMN",
                "categoria_procedimento": "consulta",
                "tipo_reembolso": "livre_escolha",
                "valor_solicitado_cents": 12000,
            }
        )
    assert str(exc.value).startswith("ERR_REEMBOLSO_CALCULO_DMN_INDISPONIVEL")


def test_calculo_indisponivel_is_a_value_error_not_a_bpmn_error() -> None:
    """ADR-0030: a `ValueError` => `failure(retries=0)` => a visible incident.

    `ST_CalculateAmount` carries NO error boundary event (BPMN `attachedToRef` names only
    `ST_CheckCoverage` and `UT_AnaliseReembolso`), and an unmodeled `bpmnError` silently ENDS the
    process scope on CIB Seven 2.1.0 — it would quietly close a reimbursement whose amount was
    never determined. Mirrors `ReembolsoValorPagamentoInvalidoError`'s deliberate typing, and is
    why `ERR_REEMBOLSO_CALCULO_DMN_INDISPONIVEL` is not (and must not be) an ADR-0030 allowlist
    code.
    """
    assert issubclass(ReembolsoCalculoIndisponivelError, ValueError)
    assert not issubclass(ReembolsoCalculoIndisponivelError, WorkerBpmnError)
    root = ET.parse(_BPMN_REEMBOLSO_PATH).getroot()
    attached = {el.get("attachedToRef") for el in root.iter(f"{{{_BPMN_MODEL_NS}}}boundaryEvent")}
    assert "ST_CalculateAmount" not in attached, (
        "ST_CalculateAmount gained an error boundary — re-decide the error semantics (ADR-0030 §2) "
        "before turning this into a WorkerBpmnError"
    )


@pytest.mark.parametrize("missing", list(_DMN_OUTPUT_NAMES))
def test_calculate_amount_entry_fails_closed_on_partial_dmn_output(missing: str) -> None:
    """A HALF-delivered decision is refused — it is not completed from the other channel.

    Mixing halves of two deliveries is exactly how a value and its provenance drift apart (the M-2
    failure mode). Either the whole triple arrived or nothing did.
    """
    variables = _entry_vars(_rule("r_consulta"))
    del variables[missing]
    with pytest.raises(ReembolsoCalculoIndisponivelError):
        calculate_amount_entry(variables)


@pytest.mark.parametrize(
    ("label", "field", "value"),
    [
        # money must be INTEGER centavos (ADR-0018; the DMN declares typeRef="integer")
        ("valor-float", "valor_calculado_tabela_cents", 12000.0),
        ("valor-float-fracionario", "valor_calculado_tabela_cents", 12000.5),
        ("valor-bool", "valor_calculado_tabela_cents", True),
        ("valor-str", "valor_calculado_tabela_cents", "12000"),
        ("valor-none", "valor_calculado_tabela_cents", None),
        ("valor-negativo", "valor_calculado_tabela_cents", -12000),
        ("valor-lista", "valor_calculado_tabela_cents", [12000]),
        # multiplo is a double, but not any object that happens to look numeric
        ("multiplo-bool", "multiplo_tabela_aplicado", False),
        ("multiplo-str", "multiplo_tabela_aplicado", "1.0"),
        ("multiplo-none", "multiplo_tabela_aplicado", None),
        ("multiplo-negativo", "multiplo_tabela_aplicado", -1.0),
        ("multiplo-nan", "multiplo_tabela_aplicado", float("nan")),
        ("multiplo-inf", "multiplo_tabela_aplicado", float("inf")),
        # provenance must be a real, non-blank token — a value with no recorded origin never
        # enters the ADR-0007 audit chain
        ("fonte-vazia", "fonte_tabela", ""),
        ("fonte-branco", "fonte_tabela", "   "),
        ("fonte-none", "fonte_tabela", None),
        ("fonte-int", "fonte_tabela", 0),
    ],
)
def test_calculate_amount_entry_fails_closed_on_malformed_dmn_output(
    label: str, field: str, value: Any
) -> None:
    """Every malformed shape returns the SAME safe outcome: no amount, no facts, one incident."""
    variables = _entry_vars(_rule("r_consulta"), **{field: value})
    with pytest.raises(ReembolsoCalculoIndisponivelError) as exc:
        calculate_amount_entry(variables)
    assert field in str(exc.value), label


def test_calculate_value_cannot_be_called_without_the_dmn_result() -> None:
    """SIGNATURE-level enforcement of "no fallback": `calculo` is required, with no default.

    The property is not a comment — a caller that forgets the DMN cannot construct a valid call at
    all, which is what makes "the worker never invents an amount" structural rather than a habit.
    """
    untyped: Any = calculate_value
    with pytest.raises(TypeError):
        untyped(_request(categoria_procedimento="consulta"))


# --- the two delivery channels (flattened locals; the `calculo` map) ------------------------


def test_calculate_amount_entry_accepts_the_unflattened_calculo_map() -> None:
    """Belt-and-braces channel: a real `Mapping` under `calculo` is accepted — what in-process
    fixtures carry and what an engine configured to deserialize the singleResult Object delivers.
    Same values, same relay."""
    rule = _rule("r_exame_simples")
    variables = _entry_vars(rule, calculo=_dmn_flat_vars(rule))
    for name in _DMN_OUTPUT_NAMES:
        del variables[name]
    out = calculate_amount_entry(variables)
    assert out["valor_calculado_tabela_cents"] == rule.valor_calculado_tabela_cents
    assert out["fonte_tabela"] == rule.fonte_tabela


def test_calculate_amount_entry_ignores_an_opaque_calculo_object() -> None:
    """The REAL engine shape: `calculo` arrives as a non-`Mapping` opaque Object.

    `fetchAndLock`'s `deserializeValues` defaults to false and `_from_camunda_var` re-decodes only
    `type == "Json"` (auth item-9, live-caught on CIB Seven 2.1.0). The flattened locals are the
    channel that works; an opaque object must neither be parsed nor mistaken for a delivery.
    """
    rule = _rule("r_consulta")
    opaque = "org.camunda.bpm.engine.variable.value.ObjectValue@deadbeef"
    out = calculate_amount_entry(_entry_vars(rule, calculo=opaque))
    assert out["valor_calculado_tabela_cents"] == rule.valor_calculado_tabela_cents

    only_opaque = _entry_vars(rule, calculo=opaque)
    for name in _DMN_OUTPUT_NAMES:
        del only_opaque[name]
    with pytest.raises(ReembolsoCalculoIndisponivelError):
        calculate_amount_entry(only_opaque)


def test_both_channels_present_and_agreeing_is_used_normally() -> None:
    """When the flat locals and the `calculo` Mapping AGREE, the (single) triple is used.

    On a correctly configured engine the two channels are two views of the SAME `BRT_Calculo`
    result, so agreement is the expected shape and must not be penalized.
    """
    rule = _rule("r_consulta")
    out = calculate_amount_entry(_entry_vars(rule, calculo=_dmn_flat_vars(rule)))
    assert out["valor_calculado_tabela_cents"] == rule.valor_calculado_tabela_cents
    assert out["fonte_tabela"] == rule.fonte_tabela


def test_only_flat_channel_present_is_used_normally() -> None:
    """Precedence when only ONE channel is complete: the flat locals alone are used."""
    rule = _rule("r_consulta")
    variables = _entry_vars(rule)
    assert "calculo" not in variables
    out = calculate_amount_entry(variables)
    assert out["valor_calculado_tabela_cents"] == rule.valor_calculado_tabela_cents


def test_only_calculo_mapping_present_is_used_normally() -> None:
    """Precedence when only ONE channel is complete: the `calculo` Mapping alone is used."""
    rule = _rule("r_exame_simples")
    variables = _entry_vars(rule, calculo=_dmn_flat_vars(rule))
    for name in _DMN_OUTPUT_NAMES:
        del variables[name]
    out = calculate_amount_entry(variables)
    assert out["valor_calculado_tabela_cents"] == rule.valor_calculado_tabela_cents


def test_disagreeing_channels_fail_closed_instead_of_silently_preferring_flat() -> None:
    """VERIFY-WP-REEMBOLSO MINOR-1: a DISAGREEMENT between channels must not be resolved silently.

    Before this fix, the flat locals silently won over a `calculo` Mapping that disagreed (e.g. a
    stale or seeded one) — exactly the drift/deserialize-mode case the module's own docstring
    claims cannot happen ("both channels carry the DMN's own answer or there is no answer"). Now
    it raises, and the reason names the disagreement.
    """
    fresh = _rule("r_consulta")
    stale = {
        "valor_calculado_tabela_cents": 999_999,
        "multiplo_tabela_aplicado": 9.0,
        "fonte_tabela": "STALE",
    }
    with pytest.raises(ReembolsoCalculoIndisponivelError) as exc:
        calculate_amount_entry(_entry_vars(fresh, calculo=stale))
    message = str(exc.value)
    assert "divergentes" in message
    assert str(fresh.valor_calculado_tabela_cents) in message
    assert "999999" in message


@pytest.mark.parametrize(
    ("differing_field", "override_value"),
    [
        ("valor_calculado_tabela_cents", 1),
        ("multiplo_tabela_aplicado", 2.0),
        ("fonte_tabela", "OUTRA_FONTE"),
    ],
)
def test_disagreeing_channels_fail_closed_on_a_single_differing_field(
    differing_field: str, override_value: Any
) -> None:
    """Disagreement on ANY one of the three fields is enough to refuse — not just a full mismatch."""
    rule = _rule("r_exame_simples")
    mismatched_map = dict(_dmn_flat_vars(rule))
    assert mismatched_map[differing_field] != override_value
    mismatched_map[differing_field] = override_value
    with pytest.raises(ReembolsoCalculoIndisponivelError) as exc:
        calculate_amount_entry(_entry_vars(rule, calculo=mismatched_map))
    assert "divergentes" in str(exc.value)


# --- (d) no rounding / overflow regression --------------------------------------------------


@pytest.mark.parametrize(
    "cents",
    [
        1,
        12_000,
        2**31 - 1,  # int32 boundary — Integer/Long typing (ADR-0018 part 2)
        2**31,
        2**53 + 1,  # first integer a float64 cannot represent exactly
        2**63 - 1,  # Long boundary
    ],
)
def test_dmn_value_is_relayed_bit_exact_only_within_declared_integer(
    cents: int, teto_zero: CeilingResolver
) -> None:
    """Relay real Integer output exactly; refuse magnitudes this DMN cannot emit.

    CIB Seven 2.1.0 integer output uses java.lang.Integer (not Long). Retain the
    former synthetic large cases as refusal controls, not as invented DMN output.
    The multiplier remains provenance only; no re-derived amount or DMN edit.
    """
    if cents > 2**31 - 1:
        with pytest.raises(ReembolsoCalculoIndisponivelError):
            calculate_value(
                _request(valor_solicitado_cents=cents),
                ReembolsoCalculoDmn(cents, 1.5, "TABELA_REFERENCIA_CONSULTA"),
                resolver=teto_zero,
            )
        return
    result = calculate_value(
        _request(categoria_procedimento="consulta", valor_solicitado_cents=cents),
        ReembolsoCalculoDmn(
            valor_calculado_tabela_cents=cents,
            multiplo_tabela_aplicado=1.5,
            fonte_tabela="TABELA_REFERENCIA_CONSULTA",
        ),
        resolver=teto_zero,
    )
    assert result.valor_calculado_tabela_cents == cents
    assert type(result.valor_calculado_tabela_cents) is int
    # the multiplier is provenance ONLY — it is recorded, never re-applied
    assert result.multiplo_tabela_aplicado == 1.5
    assert result.dentro_tabela is True


# --- dentro_tabela: the comparison the worker still owns, fail-closed ------------------------


@pytest.mark.parametrize(
    ("label", "delta", "expected"),
    [("abaixo", -1, True), ("igual", 0, True), ("acima", 1, False)],
)
def test_dentro_tabela_is_the_inclusive_comparison_against_the_dmn_value(
    label: str, delta: int, expected: bool, teto_zero: CeilingResolver
) -> None:
    """`solicitado <= valor da DMN`, walked around the inclusive boundary of the parsed value."""
    rule = _rule("r_consulta")
    result = calculate_value(
        _request(
            categoria_procedimento="consulta",
            valor_solicitado_cents=rule.valor_calculado_tabela_cents + delta,
        ),
        _calculo(rule),
        resolver=teto_zero,
    )
    assert result.dentro_tabela is expected, label


def test_dentro_tabela_is_false_for_the_dmn_catch_all(teto_alto: CeilingResolver) -> None:
    """SEM_TABELA claims nothing: no row, no value, and NEVER a within-table verdict.

    The DMN catch-all (`r_catchall`) emits value 0 / multiplo 0.0 / `SEM_TABELA`, which the
    contract, the test spec and an engine-side assertion all pin. `0 <= 0` would read True on
    arithmetic alone — `dentro_tabela` is a claim about the reference TABLE, and there is no row.
    """
    assert _DMN_CATCH_ALL.valor_calculado_tabela_cents == 0
    assert _DMN_CATCH_ALL.fonte_tabela == "SEM_TABELA"
    for solicitado in (0, 1, 12_000, 100_000_000):
        result = calculate_value(
            _request(categoria_procedimento="categoria_que_nao_existe", valor_solicitado_cents=solicitado),
            _calculo(_DMN_CATCH_ALL),
            resolver=teto_alto,
        )
        assert result.dentro_tabela is False, solicitado
        assert result.valor_calculado_tabela_cents == 0, solicitado
        assert result.multiplo_tabela_aplicado == 0.0, solicitado
        assert result.fonte_tabela == "SEM_TABELA", solicitado
        # the M-2 signature: the reference value is never the claimant's own figure
        assert result.valor_calculado_tabela_cents != solicitado or solicitado == 0, solicitado
        # vacuously within a positive ceiling (0 <= teto) — gated shut by dentro_tabela=False
        assert result.dentro_teto_l2 is True, solicitado


def test_dentro_tabela_is_false_when_a_hit_carries_a_zero_reference_value(
    teto_alto: CeilingResolver,
) -> None:
    """Defence in depth: even a NON-catch-all token with value 0 corroborates nothing.

    The shipped DMN has no such row; this asserts the worker would not be talked into one by a
    future table edit that emits a positive-looking provenance token with a zero amount.
    """
    result = calculate_value(
        _request(categoria_procedimento="consulta", valor_solicitado_cents=0),
        ReembolsoCalculoDmn(
            valor_calculado_tabela_cents=0,
            multiplo_tabela_aplicado=1.0,
            fonte_tabela="TABELA_REFERENCIA_CONSULTA",
        ),
        resolver=teto_alto,
    )
    assert result.dentro_tabela is False


@pytest.mark.parametrize(
    "fonte_tabela",
    [" SEM_TABELA", "sem_tabela", "SEM_TABELA\n", "SEM_TABELA ", " sem_tabela \n"],
)
def test_categoria_na_tabela_treats_whitespace_and_case_variants_as_the_catch_all(
    fonte_tabela: str,
) -> None:
    """VERIFY-WP-REEMBOLSO MINOR-2: a whitespace/case variant of `SEM_TABELA` must NEVER read as a hit.

    `_require_dmn_fonte` validates non-blank with `.strip()`, so any of these pass validation; the
    comparison against the catch-all literal must normalize the SAME way (and case-fold too, on
    the deny side — see `_is_sem_tabela`) so it is treated as no-table, never as a table hit. This
    is a fail-OPEN direction in a money gate: a hit inflates `dentro_tabela`, potentially feeding
    auto-approval.
    """
    calculo = ReembolsoCalculoDmn(
        valor_calculado_tabela_cents=0,
        multiplo_tabela_aplicado=0.0,
        fonte_tabela=fonte_tabela,
    )
    assert calculo.categoria_na_tabela is False
    # the raw, un-normalized value is still what the audit trail records verbatim
    assert calculo.fonte_tabela == fonte_tabela


@pytest.mark.parametrize(
    "fonte_tabela",
    [" SEM_TABELA", "sem_tabela", "SEM_TABELA\n"],
)
def test_dentro_tabela_is_false_for_whitespace_and_case_variants_of_sem_tabela(
    fonte_tabela: str, teto_alto: CeilingResolver
) -> None:
    """End-to-end: the whitespace/case variant reaches `calculate_value` and still forces False.

    Even with a nonzero reference value and a solicited amount at or below it — the shape that
    would otherwise satisfy `dentro_tabela` — the catch-all token (however mangled its spacing or
    case) must still force `dentro_tabela=False`, never a hit.
    """
    result = calculate_value(
        _request(categoria_procedimento="categoria_que_nao_existe", valor_solicitado_cents=100),
        ReembolsoCalculoDmn(
            valor_calculado_tabela_cents=12_000,
            multiplo_tabela_aplicado=1.0,
            fonte_tabela=fonte_tabela,
        ),
        resolver=teto_alto,
    )
    assert result.dentro_tabela is False


def test_calculate_amount_entry_ignores_a_seeded_dentro_tabela_claim() -> None:
    """A `dentro_tabela=true` seeded upstream is recomputed, not echoed."""
    out = calculate_amount_entry(
        _entry_vars(
            _DMN_CATCH_ALL,
            categoria_procedimento="categoria_desconhecida",
            tipo_reembolso="urgencia_emergencia",
            valor_solicitado_cents=777_000,
            dentro_tabela=True,  # attacker/upstream seed — must not survive
        )
    )
    assert out["dentro_tabela"] is False
    assert out["valor_calculado_tabela_cents"] == 0
    assert out["fonte_tabela"] == "SEM_TABELA"
    assert out["valor_solicitado_cents"] == 777_000


# --- audit-trail provenance: the record names the decision it came from ---------------------


def test_result_cites_the_dmn_decision_that_produced_the_amount() -> None:
    """ADR-0007: every calculated amount carries the coordinates of the decision that made it.

    `dmn_decisao_id` is re-derived here from the BPMN's own `camunda:decisionRef` and
    `dmn_atividade_bpmn` from the activity id, so the citation cannot drift from the model.

    HONEST LIMIT (disclosed, not fixed here): the decision-definition VERSION is deliberately
    ABSENT. `mapDecisionResult="singleResult"` returns output values only and no JUEL expression on
    a businessRuleTask exposes the definition, so the worker cites coordinates it can verify
    instead of a version it never observed — the concrete version is the engine's own
    `/history/decision-instance` record for this (decision, activity) pair.
    """
    root = ET.parse(_BPMN_REEMBOLSO_PATH).getroot()
    brt = next(
        el for el in root.iter(f"{{{_BPMN_MODEL_NS}}}businessRuleTask") if el.get("id") == "BRT_Calculo"
    )
    decision_ref = brt.get(f"{{{_CAMUNDA_NS}}}decisionRef")
    assert decision_ref == "reembolso_calculo"
    assert brt.get(f"{{{_CAMUNDA_NS}}}resultVariable") == "calculo"
    assert brt.get(f"{{{_CAMUNDA_NS}}}mapDecisionResult") == "singleResult"

    out = calculate_amount_entry(_entry_vars(_rule("r_consulta")))
    assert out["dmn_decisao_id"] == decision_ref
    assert out["dmn_atividade_bpmn"] == "BRT_Calculo"


def test_bpmn_flattens_the_dmn_outputs_into_the_worker_task() -> None:
    """THE WIRE: `ST_CalculateAmount` must carry the input mapping the worker depends on.

    Without it `calculo` reaches the worker as an opaque Object (`deserializeValues` defaults to
    false) and every request fails closed on a healthy engine. Pins the three parameters, their
    `${calculo.<name>}` expressions, and that `BRT_Calculo` is the task's only inbound edge (so the
    decision is always fresh — GAP-REEMBOLSO-5).
    """
    root = ET.parse(_BPMN_REEMBOLSO_PATH).getroot()
    task = next(
        el for el in root.iter(f"{{{_BPMN_MODEL_NS}}}serviceTask") if el.get("id") == "ST_CalculateAmount"
    )
    assert task.get(f"{{{_CAMUNDA_NS}}}topic") == "operadora.reembolso.calculate_amount"
    params = {el.get("name"): (el.text or "").strip() for el in task.iter(f"{{{_CAMUNDA_NS}}}inputParameter")}
    assert set(params) == set(_DMN_OUTPUT_NAMES), params
    for name in _DMN_OUTPUT_NAMES:
        assert params[name] == f"${{calculo.{name}}}", (name, params[name])

    incoming = [el.text for el in task.findall(f"{{{_BPMN_MODEL_NS}}}incoming")]
    assert incoming == ["Flow_BRTCalc_Calc"], incoming
    flow = next(
        el for el in root.iter(f"{{{_BPMN_MODEL_NS}}}sequenceFlow") if el.get("id") == "Flow_BRTCalc_Calc"
    )
    assert flow.get("sourceRef") == "BRT_Calculo"


# ---------------------------------------------------------------------------
# calculate_value — T1.9 ceiling enforcement (defect B3), now over the DMN's value
# ---------------------------------------------------------------------------


def test_within_table_max_value_zero_routes_analise_humana(tmp_path: Path) -> None:
    """THE T1.9 property: a within-table request with max_value_brl=0 computes dentro_teto_l2=False.

    A request at the DMN's `consulta` value is within the reference table (dentro_tabela=True), but
    the reembolso_auto_approval ceiling is 0 (D-07) -> dentro_teto_l2=False. The auto-approval
    routing itself is the NATIVE DMN `reembolso_auto_approval` (BRT_AutoApproval,
    spec/processes/dmn/reembolso_coverage.dmn — its ONLY AUTO_APROVAR rule, `r_auto`, requires
    dentro_teto_l2=true; hitPolicy FIRST with fail-safe catch-all ANALISE_HUMANA), so the False
    fact this worker computes is exactly what forces ANALISE_HUMANA engine-side. This is the exact
    acceptance criterion.
    """
    rule = _rule("r_consulta")
    calculo = calculate_value(
        _request(
            categoria_procedimento="consulta",
            valor_solicitado_cents=rule.valor_calculado_tabela_cents,
        ),
        _calculo(rule),
        resolver=_pin_resolver(tmp_path, max_value_brl=0),
    )
    assert calculo.dentro_tabela is True
    assert calculo.dentro_teto_l2 is False


def test_calculate_value_ignores_inbound_dentro_teto_l2(tmp_path: Path) -> None:
    """A seeded inbound `dentro_teto_l2=True` is IGNORED — the resolver truth (ceiling 0) wins.

    Proves the echo-through at old reembolso.py:227 is dead.
    """
    rule = _rule("r_consulta")
    calculo = calculate_value(
        _request(
            categoria_procedimento="consulta",
            valor_solicitado_cents=rule.valor_calculado_tabela_cents,
            dentro_teto_l2=True,  # attacker/upstream seed — must be ignored
        ),
        _calculo(rule),
        resolver=_pin_resolver(tmp_path, max_value_brl=0),
    )
    assert calculo.dentro_teto_l2 is False


def test_calculate_value_within_ceiling_when_configured(tmp_path: Path) -> None:
    """A real positive ceiling computes dentro_teto_l2=True, config-driven, at the inclusive boundary.

    The ceiling is compared against the DMN's reference value, not the requested amount, so the
    boundary is walked by moving the ceiling around that fixed value — which is READ FROM THE DMN,
    so the test follows an actuarial re-tune instead of pinning a number of its own. Ceiling == the
    value in reais -> within (True); one real less -> above (False). (The AUTO_APROVAR routing on a
    True fact is the native DMN `reembolso_auto_approval`, BRT_AutoApproval — engine-side, not this
    worker.)
    """
    rule = _rule("r_consulta")
    valor_brl = rule.valor_calculado_tabela_cents // 100
    assert valor_brl * 100 == rule.valor_calculado_tabela_cents, (
        "a DMN passou a emitir centavos que nao fecham em reais inteiros — reescreva a borda"
    )
    at_boundary = _request(
        categoria_procedimento="consulta", valor_solicitado_cents=rule.valor_calculado_tabela_cents
    )
    calculo_ok = calculate_value(
        at_boundary, _calculo(rule), resolver=_pin_resolver(tmp_path / "at", max_value_brl=valor_brl)
    )
    assert calculo_ok.valor_calculado_tabela_cents == rule.valor_calculado_tabela_cents
    assert calculo_ok.dentro_tabela is True
    assert calculo_ok.dentro_teto_l2 is True

    above = _pin_resolver(tmp_path / "above", max_value_brl=valor_brl - 1)
    assert calculate_value(at_boundary, _calculo(rule), resolver=above).dentro_teto_l2 is False


def test_calculate_value_sem_tabela_with_positive_ceiling_is_vacuously_within(tmp_path: Path) -> None:
    """SEM_TABELA feeds the ceiling check a reference value of 0 — vacuously within ANY positive ceiling.

    `dentro_teto_l2` compares the reference value against the tenant ceiling (`within_l2_ceiling`,
    ceilings.py). Under the DMN catch-all that value is 0 by construction, so `0 <= ceiling_brl *
    100` is True for any positive ceiling. This does NOT change routing: the DMN's ONLY
    AUTO_APROVAR rule (`r_auto`, `spec/processes/dmn/reembolso_coverage.dmn:122-123,133-136`)
    requires `dentro_tabela=true` too, and SEM_TABELA FORCES `dentro_tabela=False` — so the request
    still falls through to ANALISE_HUMANA. Anticipated by
    `docs/design/T1.9-ceiling-enforcement.md:219-222`.
    """
    calculo = calculate_value(
        _request(categoria_procedimento="categoria_que_nao_existe", valor_solicitado_cents=999999),
        _calculo(_DMN_CATCH_ALL),
        resolver=_pin_resolver(tmp_path, max_value_brl=350),
    )
    assert calculo.fonte_tabela == "SEM_TABELA"
    assert calculo.valor_calculado_tabela_cents == 0
    assert calculo.dentro_tabela is False
    assert calculo.dentro_teto_l2 is True  # vacuous — gated shut by dentro_tabela=False above


# ---------------------------------------------------------------------------
# calculate_value — M-2 audit-trail honesty, preserved under the DMN
#
# The facts calculate_value writes into the ADR-0007 audit chain (`dentro_tabela`,
# `multiplo_tabela_aplicado`, `fonte_tabela`) must describe what actually happened. The M-2
# defect: an unknown categoria fell back to the claimant's own `valor_solicitado_cents`, so
# `valor_solicitado <= valor_calculado` was unconditionally True — a fabricated corroboration
# shown to the human reviewer — while `multiplo_tabela_aplicado` was hardcoded 1.0 and
# `fonte_tabela` hardcoded "TUSS-REFERENCIA". Under GAP F-1 the provenance is stronger still:
# `fonte_tabela` is now the DMN's OWN token, not a worker-invented vocabulary.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("rule", _DMN_RULES, ids=_DMN_RULE_IDS)
def test_audit_facts_are_internally_consistent_for_every_dmn_rule(
    rule: _DmnRule, teto_zero: CeilingResolver
) -> None:
    """Invariants over whatever the DMN produced — they keep biting if the table is re-tuned."""
    solicitado = max(rule.valor_calculado_tabela_cents, 1)
    result = calculate_value(
        _request(
            categoria_procedimento=rule.categoria_procedimento or "",
            valor_solicitado_cents=solicitado,
        ),
        _calculo(rule),
        resolver=teto_zero,
    )

    if result.fonte_tabela == "SEM_TABELA":
        assert result.valor_calculado_tabela_cents == 0, rule.rule_id
        assert result.multiplo_tabela_aplicado == 0.0, rule.rule_id
        assert result.dentro_tabela is False, rule.rule_id
    else:
        assert result.valor_calculado_tabela_cents > 0, rule.rule_id
        assert result.dentro_tabela is (solicitado <= result.valor_calculado_tabela_cents), rule.rule_id

    # provenance is never blank, never the retired worker-side vocabulary, and always the DMN's
    assert result.fonte_tabela, rule.rule_id
    assert result.fonte_tabela not in {
        "TUSS-REFERENCIA",
        "TABELA_REFERENCIA",
        "TABELA_REFERENCIA_MULTIPLICADA",
    }, f"{rule.rule_id}: a worker-invented provenance token is back"
    assert result.fonte_tabela == rule.fonte_tabela, rule.rule_id
    assert result.dmn_decisao_id == "reembolso_calculo", rule.rule_id


def test_worker_speaks_the_dmn_catch_all_token_and_nothing_of_its_own(teto_zero: CeilingResolver) -> None:
    """The refusal token is the DMN's, read from the file — never this module's invention.

    `spec/processes/dmn/reembolso_calculo.dmn` rule `r_catchall` emits the triple the contract, the
    test spec and an engine-side assertion all pin ("Catch-all (procedimento sem tabela) ->
    valor_calculado_tabela_cents = 0 + fonte_tabela = 'SEM_TABELA', o que forca dentro_tabela=false
    -> ANALISE_HUMANA"). The worker relays that token; it no longer emits a parallel vocabulary.
    """
    assert '"SEM_TABELA"' in _DMN_CALCULO_PATH.read_text(encoding="utf-8")
    result = calculate_value(
        _request(categoria_procedimento="sem_row_na_tabela", valor_solicitado_cents=12345),
        _calculo(_DMN_CATCH_ALL),
        resolver=teto_zero,
    )
    assert result.fonte_tabela == "SEM_TABELA"
    assert result.valor_calculado_tabela_cents == 0
    assert result.multiplo_tabela_aplicado == 0.0


# ---------------------------------------------------------------------------
# request_documents (T2.5-P2B — BPMN ST_SolicitarDocumentos, previously missing)
# ---------------------------------------------------------------------------


def test_request_documents_nao_afirma_notificacao() -> None:
    """GAP-REEMBOLSO-8 / FAB-NOTIFIED-TRIO: the pendency STEP returns `{}` — never `notified=True`.

    The old return was `{"notified": True, "beneficiario_pseudo_id": ..., "protocolo_reembolso":
    ..., "status": "pended", "message_type": ...}` on every delivery, with no channel contacted
    and no delivery observed — while the SAME docstring disclosed the worker never publishes. The
    harness loads a handler's return into process scope on `complete` (`harness.py:1778-1782`), so
    that constant became an audit-trail claim inside the instance.
    """
    assert request_documents("BEN-PSEUDO-001", "REEMB-010") == {}


@pytest.mark.parametrize(
    "beneficiario_pseudo_id,protocolo_reembolso,message_type",
    [
        ("BEN-PSEUDO-001", "REEMB-010", "pendencia_documentacao"),
        ("", "", ""),
        ("B-2", "R-2", "outro_tipo"),
    ],
)
def test_request_documents_nenhuma_entrada_produz_afirmacao(
    beneficiario_pseudo_id: str, protocolo_reembolso: str, message_type: str
) -> None:
    """No input shape may produce a `notified`/`status` claim (or any other key)."""
    result = request_documents(beneficiario_pseudo_id, protocolo_reembolso, message_type)
    assert result == {}
    assert "notified" not in result
    # `status: "pended"` was the only key that WROTE a new value into process scope, under a
    # generic name with no declared owner — it is gone with the rest.
    assert "status" not in result


def test_request_documents_never_carries_a_decision() -> None:
    """Invariant (L0): request_documents never fabricates or carries an adverse decision.

    The BPMN's own ST_SolicitarDocumentos documentation: expiry of the pendency NEVER
    auto-denies — a human decides in UT_DecidirPendenciaExpirada.
    """
    result = request_documents("", "")
    assert "decisao_reembolso" not in result
    assert "decisao_pendencia" not in result
    assert result == {}


def test_request_documents_registra_a_etapa_sem_afirmar_entrega() -> None:
    """Observability survives the fix: the step still logs, and the log states plainly that no
    notification fact was asserted (so a reader of the trail cannot infer one)."""
    with structlog.testing.capture_logs() as logs:
        request_documents("BEN-PSEUDO-001", "REEMB-010")
    events = [entry for entry in logs if entry.get("event") == "reembolso.request_documents"]
    assert events, "a etapa TEM de continuar observavel no log"
    assert events[0]["notified_asserted"] is False


def test_request_documents_entry_aplica_o_default_pendencia_no_log() -> None:
    """Default message_type is 'pendencia_documentacao' (the pendency-open semantics of
    ST_SolicitarDocumentos; mirrors recurso's request_documents idiom). It now reaches only the
    LOG, never process scope — asserted against the log line because a `{} == {}` round-trip
    would be vacuous and would still pass if the entry stopped applying the default."""
    variables = {"beneficiario_pseudo_id": "B-1", "protocolo_reembolso": "R-1"}
    with structlog.testing.capture_logs() as logs:
        assert request_documents_entry(variables) == {}
    events = [entry for entry in logs if entry.get("event") == "reembolso.request_documents"]
    assert events and events[0]["message_type"] == "pendencia_documentacao"


def test_request_documents_entry_missing_inputs_fail_safe() -> None:
    """Missing inputs degrade to empty identifiers — never an exception, never a decision."""
    with structlog.testing.capture_logs() as logs:
        assert request_documents_entry({}) == {}
    events = [entry for entry in logs if entry.get("event") == "reembolso.request_documents"]
    assert events
    assert events[0]["beneficiario_pseudo_id"] == ""
    assert events[0]["protocolo_reembolso"] == ""


def test_request_documents_entry_ignores_kafka_seam() -> None:
    """Entry accepts the kafka seam (donor contract) but never publishes.

    The domain event this branch owes (`agents.events.reembolso.pended`) IS published — by the
    BPMN's own `ST_PublishReembolsoPended` (`:151-161`), one task downstream, through the generic
    `operadora.events.publish`. So the worker publishing nothing is CORRECT, not a gap; what was
    wrong was returning `notified=True` as if it had.
    """
    kafka = FakeKafkaPublisher()
    assert request_documents_entry({"protocolo_reembolso": "R-2"}, kafka=kafka) == {}
    assert kafka.published == []


# ---------------------------------------------------------------------------
# analyze_request (T2.5-P2B — BPMN ST_PrepararDossie, previously missing)
# ---------------------------------------------------------------------------


def test_analyze_request_never_decides() -> None:
    """analyze_request assembles the dossie; it NEVER substitutes the human decision (ADR-0005)."""
    inp = ReembolsoInput(
        tenant_id="amh",
        protocolo_reembolso="REEMB-011",
        tipo_reembolso="livre_escolha",
        categoria_procedimento="consulta",
        codigo_procedimento_tuss="10101012",
        valor_solicitado_cents=35000,
    )
    result = analyze_request(inp)
    assert result["dossie"] == "dossie_instruido"
    assert result["recomendacao_sugerida"] == "ANALISE_HUMANA"
    assert result["protocolo_reembolso"] == "REEMB-011"
    assert "decisao_reembolso" not in result


def test_analyze_request_carries_calculo_evidence_opportunistically() -> None:
    """dentro_tabela/requer_avaliacao_clinica ride as evidence when present — facts, not verdicts."""
    inp = ReembolsoInput(
        protocolo_reembolso="REEMB-012",
        dentro_tabela=True,
        requer_avaliacao_clinica=True,
        valor_solicitado_cents=99000,
    )
    result = analyze_request(inp)
    assert result["dentro_tabela"] is True
    assert result["requer_avaliacao_clinica"] is True
    assert result["valor_solicitado_cents"] == 99000
    assert result["recomendacao_sugerida"] == "ANALISE_HUMANA"


def test_analyze_request_missing_inputs_fail_safe() -> None:
    """Empty input still yields a human-routing dossie — never an exception, never a verdict."""
    result = analyze_request(ReembolsoInput())
    assert result["recomendacao_sugerida"] == "ANALISE_HUMANA"
    assert "decisao_reembolso" not in result


def test_analyze_request_entry_round_trips() -> None:
    variables = {
        "protocolo_reembolso": "REEMB-013",
        "tipo_reembolso": "urgencia_emergencia",
        "valor_solicitado_cents": 12000,
    }
    assert analyze_request_entry(variables) == analyze_request(ReembolsoInput(**variables))


def test_analyze_request_entry_ignores_kafka_seam() -> None:
    kafka = FakeKafkaPublisher()
    result = analyze_request_entry({"protocolo_reembolso": "REEMB-014"}, kafka=kafka)
    assert result["recomendacao_sugerida"] == "ANALISE_HUMANA"
    assert kafka.published == []


# ---------------------------------------------------------------------------
# notify_sla_risk (T2.5-P2B — BPMN ST_NotificarRiscoSla, previously missing;
# mirrors cancel.notify_sla_risk's proven pattern)
# ---------------------------------------------------------------------------


def test_notify_sla_risk_nao_afirma_notificacao() -> None:
    """FAB-SLA-RISK-NOTIFIED-SLICE4: retorna `{}` — NAO afirma `sla_risk_notified`.

    O `== {}` e' deliberado (nao `"sla_risk_notified" not in result`): so a igualdade exata pega
    uma fabricacao remontada chave-a-chave num local, que a cerca AST de
    `test_worker_handler_purity.py` documenta nao alcancar.
    """
    inp = ReembolsoInput(protocolo_reembolso="REEMB-SLA-001", tipo_reembolso="livre_escolha")
    assert notify_sla_risk(inp) == {}


@pytest.mark.parametrize(
    "inp",
    [
        ReembolsoInput(),
        ReembolsoInput(protocolo_reembolso="REEMB-SLA-002"),
        ReembolsoInput(protocolo_reembolso="REEMB-SLA-002", tipo_reembolso="urgencia_emergencia"),
    ],
)
def test_notify_sla_risk_nenhuma_entrada_produz_afirmacao(inp: ReembolsoInput) -> None:
    """Entrada vazia tambem nao levanta (fail-safe, nunca adversa) — e nao afirma nada."""
    assert notify_sla_risk(inp) == {}


def test_notify_sla_risk_entry_round_trips() -> None:
    variables = {"protocolo_reembolso": "REEMB-SLA-003", "tipo_reembolso": "urgencia_emergencia"}
    input_data = ReembolsoInput(
        **{k: v for k, v in variables.items() if k in ReembolsoInput.__dataclass_fields__}
    )
    assert notify_sla_risk_entry(variables) == notify_sla_risk(input_data)


def test_notify_sla_risk_entry_ignores_kafka_seam() -> None:
    kafka = FakeKafkaPublisher()
    result = notify_sla_risk_entry({"protocolo_reembolso": "REEMB-SLA-004"}, kafka=kafka)
    assert result == {}
    assert kafka.published == []


# ---------------------------------------------------------------------------
# register_reembolso_workers — registry/drift coverage (T2.5-P2B reconciliation)
# ---------------------------------------------------------------------------

# The 8 `operadora.reembolso.*` topics SP-OP-REEMBOLSO-001 declares as camunda:topic (grep
# spec/processes/bpmn/SP-OP-REEMBOLSO-001_Reembolso_Beneficiario.bpmn), excl. the shared
# `operadora.events.publish` (registered separately by events.register_events_workers).
_BPMN_REEMBOLSO_TOPICS = frozenset(
    {
        "operadora.reembolso.check_coverage",
        "operadora.reembolso.check_prazo",
        "operadora.reembolso.calculate_amount",
        "operadora.reembolso.request_documents",
        "operadora.reembolso.analyze_request",
        "operadora.reembolso.notify_sla_risk",
        "operadora.reembolso.issue_payment",
        "operadora.reembolso.send_reembolso_denial",
    }
)

# Function-derived topic KEPT by documented registry-completeness convention (shared with
# recurso.py; the generic events.publish task serves the BPMN's ST_Publish* nodes).
_CONVENTION_TOPICS = frozenset({"operadora.reembolso.publish_completed"})


def test_register_reembolso_workers_matches_bpmn_topics_plus_convention() -> None:
    """Registry coverage (ADR-0026 test strategy): the registered `operadora.reembolso.*` set is
    EXACTLY the 8 BPMN-declared topics + the documented publish_completed convention — no gap
    (previously-missing request_documents/analyze_request/notify_sla_risk now registered) and no
    orphan (auto_approve_or_route/notify_beneficiario deleted: BRT_AutoApproval is a NATIVE
    businessRuleTask with camunda:decisionRef=reembolso_auto_approval, and no BPMN topic ever
    referenced notify_beneficiario)."""
    harness = WorkerHarness(FakeWorkerTransport(), worker_id="test-worker")
    register_reembolso_workers(harness, FakeKafkaPublisher())
    reembolso_topics = {t for t in harness.registered_topics if t.startswith("operadora.reembolso.")}
    assert reembolso_topics == _BPMN_REEMBOLSO_TOPICS | _CONVENTION_TOPICS


def test_register_reembolso_workers_orphans_absent() -> None:
    """The two T2.5-P2B-deleted orphan registrations must never come back."""
    harness = WorkerHarness(FakeWorkerTransport(), worker_id="test-worker")
    register_reembolso_workers(harness, FakeKafkaPublisher())
    assert "operadora.reembolso.auto_approve_or_route" not in harness.registered_topics
    assert "operadora.reembolso.notify_beneficiario" not in harness.registered_topics


# ---------------------------------------------------------------------------
# process_payment
# ---------------------------------------------------------------------------


def test_process_payment() -> None:
    """process_payment issues payment and returns comprovante."""
    result = process_payment("REEMB-009", 35000, "BEN-PSEUDO-002")
    assert result["payment_issued"] is True
    assert result["comprovante_pagamento_ref"].startswith("PAY-")
    assert result["valor_cents"] == 35000


def test_process_payment_echoes_contract_output_variable() -> None:
    """The paid amount is written back under its CONTRACT name (SP-OP-REEMBOLSO-001
    §"Variaveis de saida": `valor_reembolso_aprovado_cents`), so the value that actually moved
    is observable in engine history/`reembolso.completed` — not only under the internal
    `valor_cents` key."""
    result = process_payment("REEMB-009", 8000, "BEN-PSEUDO-002")
    assert result["valor_reembolso_aprovado_cents"] == 8000
    assert result["valor_cents"] == 8000


# --- process_payment: fail-closed money guard (ADR-0018 integer-centavos) -------------------


def test_process_payment_refuses_missing_amount() -> None:
    """No amount => NO payment. Fail-closed: never a fabricated R$0,00 comprovante."""
    with pytest.raises(ReembolsoValorPagamentoInvalidoError) as exc:
        process_payment("REEMB-009", None, "BEN-PSEUDO-002")  # type: ignore[arg-type]
    assert str(exc.value).startswith("ERR_REEMBOLSO_VALOR_PAGAMENTO_INVALIDO")


def test_process_payment_refuses_zero_amount() -> None:
    """`0` is the exact shape of the pre-fix defect (`variables.get("valor_cents", 0)`): a
    payment issued for money that never moved. Refused."""
    with pytest.raises(ReembolsoValorPagamentoInvalidoError):
        process_payment("REEMB-009", 0, "BEN-PSEUDO-002")


def test_process_payment_refuses_negative_amount() -> None:
    with pytest.raises(ReembolsoValorPagamentoInvalidoError):
        process_payment("REEMB-009", -8000, "BEN-PSEUDO-002")


@pytest.mark.parametrize("valor", [8000.0, 80.5])
def test_process_payment_refuses_float_amount(valor: float) -> None:
    """Money is integer centavos ONLY (ADR-0018) — a float NEVER round-trips to a payment,
    not even an integral one (8000.0). No rounding, no coercion: refuse."""
    with pytest.raises(ReembolsoValorPagamentoInvalidoError):
        process_payment("REEMB-009", valor, "BEN-PSEUDO-002")  # type: ignore[arg-type]


def test_process_payment_refuses_bool_amount() -> None:
    """`True` is an `int` in Python — the guard must reject it explicitly."""
    with pytest.raises(ReembolsoValorPagamentoInvalidoError):
        process_payment("REEMB-009", True, "BEN-PSEUDO-002")  # type: ignore[arg-type]


def test_process_payment_refuses_string_amount() -> None:
    """A String-typed money variable is itself an ADR-0018 typing defect (`_to_camunda_var`
    types every int as Integer/Long) — refuse, never parse."""
    with pytest.raises(ReembolsoValorPagamentoInvalidoError):
        process_payment("REEMB-009", "8000", "BEN-PSEUDO-002")  # type: ignore[arg-type]


def test_valor_pagamento_invalido_is_value_error() -> None:
    """`ValueError` family => harness reports `failure(retries=0)` => engine incident, never a
    silent retry and never a bpmnError (`ST_IssuePayment*` has NO error boundary event)."""
    assert issubclass(ReembolsoValorPagamentoInvalidoError, ValueError)


# ---------------------------------------------------------------------------
# send_reembolso_denial — GUARD tests
# ---------------------------------------------------------------------------


def test_send_denial_guard_not_adverse() -> None:
    """send_reembolso_denial raises if decisao not in {NEGAR, APROVAR_PARCIAL}."""
    inp = ReembolsoDenialInput(
        decisao_reembolso="APROVAR",
        justificativa="test",
        fundamentacao_contratual="clausula",
        analista_id="analista-1",
    )
    with pytest.raises(ReembolsoDenialNotHumanError) as exc:
        send_reembolso_denial(inp)
    assert "decisao_reembolso" in str(exc.value)


def test_send_denial_guard_missing_justificativa() -> None:
    """send_reembolso_denial raises if justificativa is empty."""
    inp = ReembolsoDenialInput(
        decisao_reembolso="NEGAR",
        justificativa="",
        fundamentacao_contratual="clausula",
        analista_id="analista-1",
    )
    with pytest.raises(ReembolsoDenialNotHumanError) as exc:
        send_reembolso_denial(inp)
    assert "justificativa" in str(exc.value)


def test_send_denial_guard_missing_fundamentacao() -> None:
    """send_reembolso_denial raises if fundamentacao_contratual is empty."""
    inp = ReembolsoDenialInput(
        decisao_reembolso="NEGAR",
        justificativa="Motivo",
        fundamentacao_contratual="",
        analista_id="analista-1",
    )
    with pytest.raises(ReembolsoDenialNotHumanError) as exc:
        send_reembolso_denial(inp)
    assert "fundamentacao_contratual" in str(exc.value)


def test_send_denial_guard_missing_human() -> None:
    """send_reembolso_denial raises if no human identifier."""
    inp = ReembolsoDenialInput(
        decisao_reembolso="NEGAR",
        justificativa="Motivo",
        fundamentacao_contratual="clausula",
        analista_id="",
        auditor_id="",
    )
    with pytest.raises(ReembolsoDenialNotHumanError) as exc:
        send_reembolso_denial(inp)
    assert "analista_id" in str(exc.value) or "auditor_id" in str(exc.value)


def test_send_denial_parcial_guard_invalid_reduction() -> None:
    """APROVAR_PARCIAL with approved >= solicited raises."""
    inp = ReembolsoDenialInput(
        decisao_reembolso="APROVAR_PARCIAL",
        justificativa="Reducao indevida",
        fundamentacao_contratual="clausula",
        valor_reembolso_aprovado_cents=10000,
        valor_solicitado_cents=5000,  # Approved > solicited
        analista_id="analista-1",
    )
    with pytest.raises(ReembolsoDenialNotHumanError) as exc:
        send_reembolso_denial(inp)
    assert "reducao" in str(exc.value).lower() or "aprovado" in str(exc.value).lower()


def test_send_denial_success_negar() -> None:
    """send_reembolso_denial succeeds for NEGAR with all fields."""
    inp = ReembolsoDenialInput(
        decisao_reembolso="NEGAR",
        justificativa="Procedimento nao coberto pela segmentacao",
        fundamentacao_contratual="Clausula 5.2",
        valor_solicitado_cents=50000,
        analista_id="analista-789",
    )
    result = send_reembolso_denial(inp)
    assert result["denial_sent"] is True
    assert result["decisao_reembolso"] == "NEGAR"


def test_send_denial_success_aprovado_parcial() -> None:
    """send_reembolso_denial succeeds for APROVAR_PARCIAL with auditor."""
    inp = ReembolsoDenialInput(
        decisao_reembolso="APROVAR_PARCIAL",
        justificativa="Valor reduzido conforme tabela",
        fundamentacao_contratual="Clausula 8.1",
        valor_reembolso_aprovado_cents=30000,
        valor_solicitado_cents=50000,
        auditor_id="auditor-001",
        parecer_auditor="Parecer tecnico favoravel a reducao",
    )
    result = send_reembolso_denial(inp)
    assert result["denial_sent"] is True


# ---------------------------------------------------------------------------
# publish_completed
# ---------------------------------------------------------------------------


def test_publish_completed_reembolso() -> None:
    """publish_completed emits domain event."""
    result = publish_completed(desfecho="aprovado_automatico")
    assert result["published"] is True
    assert result["payload"]["desfecho"] == "aprovado_automatico"


# ---------------------------------------------------------------------------
# Error types
# ---------------------------------------------------------------------------


def test_reembolso_denial_not_human_is_permission_error() -> None:
    """ReembolsoDenialNotHumanError must be a subclass of PermissionError."""
    assert issubclass(ReembolsoDenialNotHumanError, PermissionError)


def test_reembolso_protocolo_invalido_is_value_error() -> None:
    """ReembolsoProtocoloInvalidoError must be a subclass of ValueError."""
    assert issubclass(ReembolsoProtocoloInvalidoError, ValueError)


# ---------------------------------------------------------------------------
# Dict-boundary entry functions (T1.2/ADR-0026 §2b) — round-trip vs calling the
# typed function directly; fail-closed marshalling on invalid/missing input.
# T1.9: calculate_amount_entry only marshals into `calculate_value` — it never
# re-derives `dentro_teto_l2` (see calculate_amount_entry's docstring).
# ---------------------------------------------------------------------------


def test_check_coverage_entry_round_trips_check_coverage() -> None:
    variables = {
        "protocolo_reembolso": "REEMB-2",
        "codigo_procedimento_tuss": "10101012",
        "cobertura_prevista": True,
    }
    assert check_coverage_entry(variables) == check_coverage(ReembolsoInput(**variables))


def test_check_prazo_entry_raises_on_missing_protocolo() -> None:
    """Fail-closed (unchanged guard): validate_reembolso raises ReembolsoProtocoloInvalidoError
    when protocolo_reembolso is blank."""
    with pytest.raises(ReembolsoProtocoloInvalidoError):
        check_prazo_entry({"protocolo_reembolso": ""})


def test_check_prazo_entry_happy_path_round_trips_validate_reembolso() -> None:
    variables = {
        "protocolo_reembolso": "REEMB-1",
        "codigo_procedimento_tuss": "10101012",
        "valor_solicitado_cents": 35000,
        "dentro_prazo": True,
    }
    direct = validate_reembolso(ReembolsoInput(**variables))
    result = check_prazo_entry(variables)
    assert result["valid"] == direct.valid
    assert result["dentro_prazo"] == direct.dentro_prazo


def test_calculate_amount_entry_round_trips_calculate_value() -> None:
    """The entry resolves BRT_Calculo's outputs and marshals; it adds no fact of its own.

    Both sides are driven with the SAME DMN result (the parsed `r_consulta` row), so any
    divergence would be the entry function deriving something — which it must not.
    """
    rule = _rule("r_consulta")
    variables: dict[str, Any] = {
        "codigo_procedimento_tuss": "10101012",
        "categoria_procedimento": "consulta",
        "tipo_reembolso": "eletivo",
        "valor_solicitado_cents": 30000,
        "dentro_teto_l2": True,
        **_dmn_flat_vars(rule),
    }
    direct = calculate_value(ReembolsoInput(**pick_fields(variables, ReembolsoInput)), _calculo(rule))
    result = calculate_amount_entry(variables)
    assert result["dentro_teto_l2"] == direct.dentro_teto_l2
    assert result["valor_calculado_tabela_cents"] == direct.valor_calculado_tabela_cents
    assert result["dentro_tabela"] == direct.dentro_tabela
    assert result["fonte_tabela"] == direct.fonte_tabela


def test_issue_payment_entry_round_trips_process_payment() -> None:
    # R-058: com o bloqueio do caminho automatico fechado, `issue_payment_entry` so' emite com
    # decisao humana no registro — `decisao_reembolso` e' o contexto que as duas tasks humanas de
    # pagamento sempre carregam (a User Task o escreve antes de o token chegar la').
    # §Delta-F3: NADA alem disso. Um `APROVAR` integral segue pagavel SEM `analista_id` —
    # o contrato so' cobra o id nas decisoes adversas.
    variables = {
        "protocolo_reembolso": "REEMB-1",
        "valor_reembolso_aprovado_cents": 30000,
        "beneficiario_pseudo_id": "B-1",
        "decisao_reembolso": "APROVAR",
    }
    direct = process_payment("REEMB-1", 30000, "B-1")
    result = issue_payment_entry(variables)
    assert result["payment_issued"] == direct["payment_issued"]
    assert result["valor_cents"] == direct["valor_cents"]


# --- issue_payment_entry: the amount each of the THREE payment paths must pay ---------------
# One topic (`operadora.reembolso.issue_payment`) serves three BPMN service tasks. Contract
# SP-OP-REEMBOLSO-001 §"Variaveis de saida": `valor_reembolso_aprovado_cents` = "Valor
# efetivamente aprovado (humano em APROVAR/APROVAR_PARCIAL; = valor_calculado_tabela_cents no
# caminho automatico)" — ONE variable, three producers.


def test_issue_payment_entry_auto_path_e_bloqueado_fail_closed() -> None:
    """R-058 (GAP REEMBOLSO-AUTO-OVERPAY-a): ST_IssuePaymentAuto NAO paga — recusa fail-closed.

    Este teste ERA `test_issue_payment_entry_auto_path_pays_calculated_amount` e afirmava o
    contrario: que o caminho automatico paga `valor_calculado_tabela_cents`. Esse era exatamente o
    defeito — a task paga o valor da TABELA, e o gate `dentro_tabela` compara com `<=`, nunca com
    `==`, entao `solicitado < tabela` a alcanca e ela paga o MAIOR. Enquanto a atuaria
    (+ regulatorio verifica) nao assinar qual formula corrige (A `min(solicitado, tabela)` vs B),
    o caminho automatico inteiro esta bloqueado e o pedido vai a analise humana.

    Exercita o payload EXATO que o engine entregaria: o `valor_reembolso_aprovado_cents` ja
    resolvido pelo inputParameter da task mais o marcador `origem_pagamento` LOCAL da activity.
    """
    variables = {
        "protocolo_reembolso": "REEMB-AUTO",
        "beneficiario_pseudo_id": "B-1",
        # As delivered by the BPMN input mapping on ST_IssuePaymentAuto.
        "valor_reembolso_aprovado_cents": 12000,
        "valor_solicitado_cents": 12000,
        "origem_pagamento": ORIGEM_PAGAMENTO_AUTO,
    }
    with pytest.raises(ReembolsoAutoPagamentoBloqueadoError):
        issue_payment_entry(variables)


def test_issue_payment_entry_auto_path_bloqueado_mesmo_sem_o_marcador_de_origem() -> None:
    """SEGUNDO canal da guarda: sem `origem_pagamento`, a AUSENCIA de decisao humana ja recusa.

    Prova que o bloqueio nao depende de um unico atributo do BPMN — apagar o inputParameter
    `origem_pagamento` de `ST_IssuePaymentAuto` nao o torna inerte. As duas tasks humanas de
    pagamento so' sao alcancaveis depois de uma User Task humana ter escrito
    `decisao_reembolso`/`analista_id`, entao "sem decisao humana" e', por eliminacao, o caminho
    automatico.
    """
    variables = {
        "protocolo_reembolso": "REEMB-AUTO",
        "beneficiario_pseudo_id": "B-1",
        "valor_reembolso_aprovado_cents": 12000,
    }
    with pytest.raises(ReembolsoAutoPagamentoBloqueadoError):
        issue_payment_entry(variables)


def test_issue_payment_entry_bloqueio_independe_do_teto_d07() -> None:
    """O bloqueio R-058 NAO le o teto: `dentro_teto_l2`/`max_value_brl` sao irrelevantes aqui.

    Semeia o cenario POS-D-07 (teto positivo ja resolvido, tudo dentro da tabela e do teto, que e'
    o mundo em que o sobrepagamento deixa de ser latente) e prova que o caminho automatico
    continua recusado. E' a propriedade central pedida por R-058: quando o teto subir de zero, o
    caminho automatico ainda falha fechado ate a assinatura da atuaria.
    """
    variables = {
        "protocolo_reembolso": "REEMB-AUTO",
        "beneficiario_pseudo_id": "B-1",
        "origem_pagamento": ORIGEM_PAGAMENTO_AUTO,
        "valor_reembolso_aprovado_cents": 12000,
        "valor_solicitado_cents": 9000,  # solicitado < tabela: o caso de SOBREPAGAMENTO
        "dentro_tabela": True,
        "dentro_teto_l2": True,
    }
    with pytest.raises(ReembolsoAutoPagamentoBloqueadoError):
        issue_payment_entry(variables)


def test_issue_payment_entry_origem_auto_bloqueia_mesmo_com_decisao_humana_semeada() -> None:
    """Canal 1 vence o canal 2: um `decisao_reembolso`/`analista_id` semeado no start nao abre.

    `origem_pagamento` e' variavel LOCAL da activity `ST_IssuePaymentAuto` — nenhum payload de
    start consegue remove-la de la'. Logo, forjar uma decisao humana no escopo de processo nao
    transforma o caminho automatico num caminho humano.
    """
    variables = {
        "protocolo_reembolso": "REEMB-AUTO",
        "beneficiario_pseudo_id": "B-1",
        "origem_pagamento": ORIGEM_PAGAMENTO_AUTO,
        "valor_reembolso_aprovado_cents": 12000,
        "decisao_reembolso": "APROVAR",
        "analista_id": "analista-forjado-no-start",
    }
    with pytest.raises(ReembolsoAutoPagamentoBloqueadoError):
        issue_payment_entry(variables)


@pytest.mark.parametrize("origem", ["auto_l2", "  AUTO_L2  ", "Auto_L2"])
def test_issue_payment_entry_origem_auto_normalizada(origem: str) -> None:
    """O token de origem e normalizado (strip + upper) antes da comparacao — direcao fail-closed.

    Mesma polaridade de `_is_sem_tabela`: o token e' um DENY token, entao normalizar amplia o
    conjunto de recusas, nunca o de liberacoes. Um hop de decodificacao que mude espacamento ou
    caixa nao pode transformar o caminho automatico num caminho pagavel.
    """
    variables = {
        "protocolo_reembolso": "REEMB-AUTO",
        "beneficiario_pseudo_id": "B-1",
        "origem_pagamento": origem,
        "valor_reembolso_aprovado_cents": 12000,
        "decisao_reembolso": "APROVAR",
        "analista_id": "analista-sintetico-001",
    }
    with pytest.raises(ReembolsoAutoPagamentoBloqueadoError):
        issue_payment_entry(variables)


def test_issue_payment_entry_analista_path_pays_human_approved_amount() -> None:
    """ST_IssuePaymentAnalista (human APROVAR): pays the value the human set in the User Task."""
    variables = {
        "protocolo_reembolso": "REEMB-ANALISTA",
        "beneficiario_pseudo_id": "B-1",
        "decisao_reembolso": "APROVAR",
        "valor_reembolso_aprovado_cents": 12000,
        "analista_id": "analista-sintetico-001",
    }
    result = issue_payment_entry(variables)
    assert result["valor_cents"] == 12000


def test_issue_payment_entry_parcial_path_pays_reduced_human_amount() -> None:
    """ST_IssuePaymentParcial (human APROVAR_PARCIAL): pays the REDUCED value the human set —
    8000 of a 12000 request (the exact shape the live parcial suite asserts). Pre-fix this path
    issued 0."""
    variables = {
        "protocolo_reembolso": "REEMB-PARCIAL",
        "beneficiario_pseudo_id": "B-1",
        "decisao_reembolso": "APROVAR_PARCIAL",
        "valor_solicitado_cents": 12000,
        "valor_reembolso_aprovado_cents": 8000,
        "analista_id": "analista-sintetico-001",
    }
    result = issue_payment_entry(variables)
    assert result["valor_cents"] == 8000
    assert result["valor_reembolso_aprovado_cents"] == 8000


def test_issue_payment_entry_approved_overrides_calculated() -> None:
    """PRECEDENCE: the human-approved value wins over the table-calculated one. A parcial
    reduction (8000) must NEVER be paid at the calculated 12000."""
    variables = {
        "protocolo_reembolso": "REEMB-PARCIAL",
        "beneficiario_pseudo_id": "B-1",
        "valor_calculado_tabela_cents": 12000,
        "valor_reembolso_aprovado_cents": 8000,
        # R-058: contexto humano que a User Task escreve antes de ST_IssuePaymentParcial.
        "decisao_reembolso": "APROVAR_PARCIAL",
        "analista_id": "analista-sintetico-001",
    }
    assert issue_payment_entry(variables)["valor_cents"] == 8000


def test_issue_payment_entry_never_falls_back_to_calculated_amount() -> None:
    """NO fallback to `valor_calculado_tabela_cents`: paying a machine-calculated amount no
    human approved would originate a reduction/overpayment in the worker — the L0-hard
    violation this process exists to prevent (contract §"Invariante L0 hard")."""
    variables = {
        "protocolo_reembolso": "REEMB-ANALISTA",
        "beneficiario_pseudo_id": "B-1",
        "decisao_reembolso": "APROVAR",
        "valor_calculado_tabela_cents": 12000,
    }
    with pytest.raises(ReembolsoValorPagamentoInvalidoError):
        issue_payment_entry(variables)


def test_issue_payment_entry_refuses_orphan_valor_cents_variable() -> None:
    """Regression fence for the fixed defect: `valor_cents` is produced by NOTHING in this
    process (not by calculate_amount, not by any BPMN mapping, not by the start seeds). It must
    never be the amount source again."""
    variables = {
        "protocolo_reembolso": "REEMB-1",
        "beneficiario_pseudo_id": "B-1",
        "valor_cents": 30000,
        # R-058: contexto humano, para que a guarda de VALOR continue sendo a que dispara aqui.
        "decisao_reembolso": "APROVAR",
    }
    with pytest.raises(ReembolsoValorPagamentoInvalidoError):
        issue_payment_entry(variables)


def test_issue_payment_entry_refuses_when_amount_absent() -> None:
    """Nothing resolvable => incident, never a R$0,00 payment (the pre-fix behaviour)."""
    with pytest.raises(ReembolsoValorPagamentoInvalidoError):
        issue_payment_entry(
            {
                "protocolo_reembolso": "REEMB-1",
                "beneficiario_pseudo_id": "B-1",
                # R-058: contexto humano, para isolar a guarda de VALOR (sem ele a guarda do
                # caminho automatico dispararia antes — ver
                # test_issue_payment_entry_auto_path_bloqueado_mesmo_sem_o_marcador_de_origem).
                "decisao_reembolso": "APROVAR",
            }
        )


# ===========================================================================
# R-058 / GAP REEMBOLSO-AUTO-OVERPAY-a — bloqueio fail-closed do caminho automatico
# ===========================================================================
#
# O que estes testes fixam: enquanto o interruptor nomeado
# `REEMBOLSO_AUTO_PAGAMENTO_LIBERADO` estiver fechado, NENHUM reembolso e' pago pelo caminho
# automatico — nem no modelo (condicao de `Flow_GW_AutoAprovar`) nem no worker
# (`require_pagamento_autorizado`). O bloqueio e' GENERICO (nao distingue `solicitado < tabela`
# de `solicitado >= tabela`) e INDEPENDENTE do teto D-07. A escolha definitiva entre A
# (`min(solicitado, tabela)`) e B (rotear so' quando `solicitado < tabela`) e ato atuarial
# (+ regulatorio verifica) — R-059 — e esta registrada como pergunta de revisao 4 em
# `docs/sme-dispatch/financas/PACKAGE.md` e no `docs/review-queue.md`.


def test_interruptor_r058_esta_fechado() -> None:
    """FENCE: a constante e' `False`. Vira-la e' o ato que materializa o sign-off atuarial.

    Este teste existe para que abrir o caminho automatico seja impossivel de fazer em silencio:
    qualquer PR que mude a constante FALHA aqui e obriga a discussao sobre a assinatura da atuaria
    (+ regulatorio verifica) e sobre qual formula (A vs B) esta entrando junto.
    """
    assert REEMBOLSO_AUTO_PAGAMENTO_LIBERADO is False, (
        "REEMBOLSO_AUTO_PAGAMENTO_LIBERADO foi aberto sem o sign-off atuarial de "
        "SP-OP-REEMBOLSO-001 (R-058/R-059) — o caminho automatico voltaria a pagar o valor da "
        "TABELA quando solicitado < tabela (sobrepagamento por construcao)"
    )


def test_calculate_value_nunca_libera_o_caminho_automatico(tmp_path: Path) -> None:
    """O fato publicado e' `False` mesmo no cenario mais favoravel POSSIVEL — teto positivo incluso.

    Cenario deliberadamente pos-D-07: teto positivo pinado, `dentro_tabela=True`,
    `dentro_teto_l2=True` — exatamente o mundo em que o sobrepagamento deixa de ser latente. O
    terceiro fato, `reembolso_auto_liberado`, continua `False`: e' a prova de que o bloqueio NAO le
    o teto e nao e' um efeito colateral de `max_value_brl: 0`.
    """
    rule = _rule("r_consulta")
    valor_brl = rule.valor_calculado_tabela_cents // 100
    result = calculate_value(
        _request(
            categoria_procedimento="consulta",
            valor_solicitado_cents=rule.valor_calculado_tabela_cents - 3000,
        ),
        _calculo(rule),
        resolver=_pin_resolver(tmp_path / "teto-positivo", max_value_brl=valor_brl),
    )
    # O cenario de sobrepagamento: dentro da tabela E dentro do teto, com solicitado < tabela.
    assert result.dentro_tabela is True
    assert result.dentro_teto_l2 is True
    assert result.valor_solicitado_cents < result.valor_calculado_tabela_cents
    # ... e ainda assim o caminho automatico esta fechado.
    assert result.reembolso_auto_liberado is False


def test_calculate_amount_entry_publica_a_variavel_do_gateway() -> None:
    """A saida do worker carrega a variavel EXATA que a condicao do gateway le.

    `calculate_amount_entry` devolve `dataclasses.asdict(...)`, e o engine grava cada chave como
    variavel de escopo de processo — e' assim que `reembolso_auto_liberado` chega a
    `Flow_GW_AutoAprovar`. O nome vem de `VAR_REEMBOLSO_AUTO_LIBERADO`, a mesma constante que o
    teste de modelo abaixo procura no XML: uma unica fonte do nome dos dois lados.
    """
    out = calculate_amount_entry(_entry_vars(_rule("r_consulta")))
    assert VAR_REEMBOLSO_AUTO_LIBERADO in out
    assert out[VAR_REEMBOLSO_AUTO_LIBERADO] is False


def test_calculate_amount_entry_ignora_um_reembolso_auto_liberado_semeado() -> None:
    """Um `reembolso_auto_liberado=true` semeado no start e' SOBRESCRITO, nunca lido.

    Mesma especie do seed hostil que `test_calculate_amount_entry_ignores_a_seeded_dentro_tabela_
    claim` ja cobre: o worker RECOMPUTA (aqui, relaia da constante) e o valor final que o gateway
    le e' o do worker. `ST_CalculateAmount` e' o unico caminho ate `GW_AutoAprovacao`.
    """
    out = calculate_amount_entry(_entry_vars(_rule("r_consulta"), reembolso_auto_liberado=True))
    assert out[VAR_REEMBOLSO_AUTO_LIBERADO] is False


def test_bpmn_gateway_de_auto_aprovacao_exige_o_interruptor() -> None:
    """MODELO: `Flow_GW_AutoAprovar` so' dispara com o interruptor aberto; o default segue humano.

    Le o BPMN pelo ID do elemento (nao por numero de linha, que anda). Prova as tres metades do
    bloqueio no modelo: (a) a condicao e' EXATAMENTE
    `${auto_aprovacao.recomendacao == 'AUTO_APROVAR' && reembolso_auto_liberado}` — a expressao
    inteira, nao dois substrings (§Delta-F1: fixar substrings deixava passar `&&` -> `||`, um
    caractere que reabre o caminho automatico no dia em que a DMN voltar a recomendar
    AUTO_APROVAR, i.e. quando o teto D-07 subir); (b) o gateway continua com
    `default="Flow_GW_AnaliseHumana"`, i.e. o token nao fica preso nem vira incidente — ele VAI
    para a analise humana; (c) `ST_IssuePaymentAuto` NAO foi apagada (o desenho segue visivel para
    o sign-off) e agora carrega o marcador de origem que o worker reconhece.
    """
    root = ET.parse(_BPMN_REEMBOLSO_PATH).getroot()

    flows = [
        el for el in root.iter(f"{{{_BPMN_MODEL_NS}}}sequenceFlow") if el.get("id") == "Flow_GW_AutoAprovar"
    ]
    assert len(flows) == 1
    bruta = flows[0].findtext(f"{{{_BPMN_MODEL_NS}}}conditionExpression") or ""
    condicao = " ".join(bruta.split())  # normaliza espacos/quebras; ET ja desescapou &amp;&amp;
    assert condicao == _CONDICAO_GW_AUTO_APROVAR_ESPERADA, (
        "BLOQUEIO R-058 ALTERADO no modelo: a condicao de Flow_GW_AutoAprovar deixou de ser "
        f"exatamente {_CONDICAO_GW_AUTO_APROVAR_ESPERADA!r} — e' {condicao!r}. A EXPRESSAO INTEIRA "
        "e' fixada aqui de proposito (§Delta-F1): fixar so' os dois termos deixava passar a "
        "troca de `&&` por `||`, que reabre o caminho automatico assim que a DMN voltar a "
        "recomendar AUTO_APROVAR (i.e. no dia em que o teto D-07 subir de zero)."
    )
    assert "||" not in condicao, (
        f"disjuncao na condicao do gateway: qualquer `||` torna o interruptor R-058 dispensavel. "
        f"Condicao: {condicao!r}"
    )

    gateways = [
        el for el in root.iter(f"{{{_BPMN_MODEL_NS}}}exclusiveGateway") if el.get("id") == "GW_AutoAprovacao"
    ]
    assert len(gateways) == 1
    assert gateways[0].get("default") == "Flow_GW_AnaliseHumana", (
        "sem o default humano, uma condicao falsa nao roteia para lugar nenhum"
    )

    tasks = [
        el for el in root.iter(f"{{{_BPMN_MODEL_NS}}}serviceTask") if el.get("id") == "ST_IssuePaymentAuto"
    ]
    assert len(tasks) == 1, "ST_IssuePaymentAuto foi apagada — o bloqueio nao remove elementos"
    params = {
        el.get("name"): (el.text or "").strip() for el in tasks[0].iter(f"{{{_CAMUNDA_NS}}}inputParameter")
    }
    assert params.get("origem_pagamento") == ORIGEM_PAGAMENTO_AUTO, (
        f"marcador de origem ausente/divergente em ST_IssuePaymentAuto: {params!r}"
    )


def test_require_pagamento_autorizado_libera_as_duas_origens_humanas() -> None:
    """A guarda NAO estorva os dois caminhos humanos de pagamento (APROVAR e APROVAR_PARCIAL).

    Prova que o bloqueio e' cirurgico: com decisao humana no registro e sem o marcador de origem
    automatica, a funcao e' um no-op (nao levanta). O `auditor_id` cobre o caminho
    `UT_RevisaoAuditorMedico`, onde `analista_id` pode nao existir.
    """
    for decisao, analista, auditor in (
        ("APROVAR", "analista-sintetico-001", ""),
        ("APROVAR_PARCIAL", "analista-sintetico-001", ""),
        ("APROVAR", "", "auditor-sintetico-001"),
        ("APROVAR_PARCIAL", "", "auditor-sintetico-001"),
        # §Delta-F3: APROVAR integral SEM id nenhum — o contrato nao o exige, logo a guarda
        # tambem nao pode exigir.
        ("APROVAR", "", ""),
        ("  aprovar  ", "", ""),
    ):
        reembolso.require_pagamento_autorizado(
            protocolo_reembolso="REEMB-1",
            origem_pagamento="",
            decisao_reembolso=decisao,
            analista_id=analista,
            auditor_id=auditor,
        )


def test_require_pagamento_autorizado_nao_amplia_o_caminho_humano_aprovar() -> None:
    """§Delta-F3: um `APROVAR` humano SEM `analista_id`/`auditor_id` continua PAGANDO.

    A guarda R-058 fecha o caminho AUTOMATICO — esse e' todo o escopo aprovado em R-058
    ("bloqueio fail-closed generico do caminho automatico"). Ela NAO pode acrescentar precondicao
    ao caminho humano: o contrato SP-OP-REEMBOLSO-001 exige `analista_id`/`auditor_id` apenas nas
    decisoes ADVERSAS (`ERR_REEMBOLSO_DENIAL_NOT_HUMAN`: "recusa se `decisao_reembolso` fora de
    {NEGAR, APROVAR_PARCIAL} setado por humano ou se faltar `analista_id`/`auditor_id`"), e a
    documentacao de `UT_AnaliseReembolso` no BPMN so' os lista para NEGAR e APROVAR_PARCIAL. Uma
    revisao anterior desta guarda exigia o id tambem no APROVAR integral: isso transformava em
    INCIDENTE um pagamento humano que o contrato considera valido.

    Este teste vai VERMELHO se alguem reintroduzir a exigencia.
    """
    variables = {
        "protocolo_reembolso": "REEMB-ANALISTA",
        "beneficiario_pseudo_id": "B-1",
        "decisao_reembolso": "APROVAR",
        "valor_reembolso_aprovado_cents": 12000,
    }
    assert issue_payment_entry(variables)["valor_cents"] == 12000


def test_require_pagamento_autorizado_exige_id_somente_no_aprovar_parcial() -> None:
    """§Delta-F3: o id humano segue OBRIGATORIO na reducao — exigencia do contrato, nao de R-058.

    `APROVAR_PARCIAL` e' decisao ADVERSA (reduz o pedido) e o contrato ja a condiciona a
    `analista_id`/`auditor_id` (mesmo conjunto que `send_reembolso_denial` cobra em
    `ST_ComunicarReducao`, o predecessor imediato de `ST_IssuePaymentParcial`). Manter a exigencia
    AQUI nao amplia nada — apenas espelha o guard adverso no ponto onde o dinheiro sai.
    """
    with pytest.raises(ReembolsoAutoPagamentoBloqueadoError):
        issue_payment_entry(
            {
                "protocolo_reembolso": "REEMB-PARCIAL",
                "beneficiario_pseudo_id": "B-1",
                "decisao_reembolso": "APROVAR_PARCIAL",
                "valor_solicitado_cents": 12000,
                "valor_reembolso_aprovado_cents": 8000,
            }
        )


def test_require_pagamento_autorizado_loga_sem_phi() -> None:
    """O log da recusa nomeia motivo, protocolo e a linha do ledger — e NENHUM dado do beneficiario.

    `beneficiario_pseudo_id`, matricula, valores, CID e documentos NAO entram no evento: a recusa
    precisa ser diagnosticavel sem levar PHI para o pipeline de logs (ADR-0006). Usa o idioma
    `structlog.testing.capture_logs` ja empregado neste arquivo.
    """
    with (
        structlog.testing.capture_logs() as logs,
        pytest.raises(ReembolsoAutoPagamentoBloqueadoError),
    ):
        reembolso.require_pagamento_autorizado(
            protocolo_reembolso="REEMB-AUTO",
            origem_pagamento=f"  {ORIGEM_PAGAMENTO_AUTO.lower()}  ",
            decisao_reembolso="",
            analista_id="",
            auditor_id="",
        )

    eventos = [e for e in logs if e.get("event") == "reembolso.issue_payment.auto_bloqueado"]
    assert len(eventos) == 1, f"a recusa TEM de ser observavel exatamente uma vez: {logs!r}"
    campos = eventos[0]
    assert campos["protocolo_reembolso"] == "REEMB-AUTO"
    assert campos["unlock_ledger"] == "R-058"
    assert campos["gap"] == "REEMBOLSO-AUTO-OVERPAY-a"
    assert campos["interruptor"] == "REEMBOLSO_AUTO_PAGAMENTO_LIBERADO"
    assert ORIGEM_PAGAMENTO_AUTO in campos["motivo"]
    # §Delta-INFO-I3: `origem_pagamento` e' controlado pelo CHAMADOR (uma variavel de escopo de
    # processo pode ser semeada no start). O log emite um token NORMALIZADO de conjunto fechado,
    # nunca a string bruta — aqui a entrada era `"  auto_l2  "`.
    assert campos["origem_pagamento"] == ORIGEM_PAGAMENTO_AUTO
    proibidos = {
        "beneficiario_pseudo_id",
        "matricula_beneficiario",
        "cid10",
        "documentos_refs",
        "valor_solicitado_cents",
        "valor_reembolso_aprovado_cents",
    }
    assert not (proibidos & set(campos)), f"PHI no log de recusa: {sorted(proibidos & set(campos))}"


@pytest.mark.parametrize(
    ("origem", "esperado"),
    [
        ("", "<ausente>"),
        ("   ", "<ausente>"),
        ("HUMANO-ANALISTA", "<desconhecida>"),
        ("cpf 000.000.000-00 do beneficiario", "<desconhecida>"),
    ],
)
def test_require_pagamento_autorizado_nao_ecoa_origem_bruta_no_log(origem: str, esperado: str) -> None:
    """§Delta-INFO-I3: o valor bruto de `origem_pagamento` NUNCA chega ao log.

    Nas duas tasks HUMANAS de pagamento nao existe `origem_pagamento` local, entao uma homonima
    semeada no start do processo alcanca o worker e seria ecoada verbatim se a guarda logasse a
    string crua. Como o campo e' texto nao confiavel (poderia carregar qualquer coisa, inclusive
    dado do beneficiario), a recusa loga um token de um conjunto FECHADO:
    `AUTO_L2` / `<ausente>` / `<desconhecida>`.
    """
    with (
        structlog.testing.capture_logs() as logs,
        pytest.raises(ReembolsoAutoPagamentoBloqueadoError),
    ):
        reembolso.require_pagamento_autorizado(
            protocolo_reembolso="REEMB-1",
            origem_pagamento=origem,
            decisao_reembolso="",  # sem decisao humana => canal 2 recusa
            analista_id="",
            auditor_id="",
        )

    campos = next(e for e in logs if e.get("event") == "reembolso.issue_payment.auto_bloqueado")
    assert campos["origem_pagamento"] == esperado
    assert origem.strip() not in str(campos) or not origem.strip()


def test_send_reembolso_denial_entry_guards_missing_human_decision() -> None:
    """send_reembolso_denial_entry raises the UNCHANGED ReembolsoDenialNotHumanError guard."""
    with pytest.raises(ReembolsoDenialNotHumanError):
        send_reembolso_denial_entry({"decisao_reembolso": ""})


def test_send_reembolso_denial_entry_happy_path() -> None:
    variables = {
        "decisao_reembolso": "NEGAR",
        "justificativa": "fora de cobertura",
        "fundamentacao_contratual": "clausula 5",
        "analista_id": "analista-1",
    }
    direct = send_reembolso_denial(ReembolsoDenialInput(**variables))
    assert send_reembolso_denial_entry(variables) == direct


def test_publish_completed_entry_round_trips_publish_completed() -> None:
    variables = {"event_type": "reembolso.completed", "desfecho": "aprovado_automatico"}
    assert publish_completed_entry(variables) == publish_completed(
        event_type="reembolso.completed", payload={}, desfecho="aprovado_automatico"
    )


class _FiniteMoneyIntSubclass(int):
    pass


@pytest.mark.parametrize(
    "value", [True, False, 12.9, 12.0, "12", None, _FiniteMoneyIntSubclass(12), 2**63, -(2**63) - 1]
)
def test_finite_money_request_ingress_rejects_before_comparison(value):
    result = reembolso.check_prazo_entry(
        {
            "tenant_id": "synthetic",
            "protocolo_reembolso": "synthetic",
            "codigo_procedimento_tuss": "synthetic",
            "valor_solicitado_cents": value,
        }
    )
    assert result["valid"] is False
    assert "valor_solicitado_cents invalido" in result["errors"]


@pytest.mark.parametrize("value", [True, 12.9, "12", _FiniteMoneyIntSubclass(12), 2**63, -(2**63) - 1])
def test_finite_money_calculation_rejects_requested_type_range_before_ceiling(value):
    class NoCeiling:
        def within_l2_ceiling(self, **kwargs):
            pytest.fail("invalid input reached ceiling")

    with pytest.raises(ReembolsoCalculoIndisponivelError):
        calculate_value(_request(valor_solicitado_cents=value), _calculo(_DMN_RULES[0]), resolver=NoCeiling())


@pytest.mark.parametrize("channel", ["flat", "nested"])
@pytest.mark.parametrize("value", [2**31, 2**63, _FiniteMoneyIntSubclass(12)])
def test_finite_money_dmn_output_respects_actual_integer_representation(channel, value):
    variables = _entry_vars(_DMN_RULES[0])
    variables["valor_calculado_tabela_cents"] = value
    if channel == "nested":
        variables["calculo"] = {name: variables.pop(name) for name in _DMN_OUTPUT_NAMES}
    with pytest.raises(ReembolsoCalculoIndisponivelError):
        calculate_amount_entry(variables)


@pytest.mark.parametrize("value", [2**63, _FiniteMoneyIntSubclass(12)])
def test_finite_money_payment_guard_precedes_receipt_generation(value, monkeypatch):
    import time

    monkeypatch.setattr(time, "time_ns", lambda: pytest.fail("invalid amount minted payment receipt"))
    with pytest.raises(ReembolsoValorPagamentoInvalidoError):
        issue_payment_entry(
            {
                "protocolo_reembolso": "synthetic",
                "decisao_reembolso": "APROVAR",
                "valor_reembolso_aprovado_cents": value,
            }
        )


@pytest.mark.parametrize("field", ["valor_solicitado_cents", "valor_reembolso_aprovado_cents"])
@pytest.mark.parametrize("value", [True, 12.9, "12", _FiniteMoneyIntSubclass(12), 2**63])
def test_finite_money_partial_human_guard_rejects_types_before_comparing(field, value):
    variables = {
        "decisao_reembolso": "APROVAR_PARCIAL",
        "justificativa": "synthetic",
        "fundamentacao_contratual": "synthetic",
        "analista_id": "synthetic",
        "valor_solicitado_cents": 100,
        "valor_reembolso_aprovado_cents": 12,
        field: value,
    }
    with pytest.raises(ReembolsoDenialNotHumanError):
        send_reembolso_denial_entry(variables)


@pytest.mark.parametrize("value", [1, 2**31, 2**63 - 1])
def test_finite_money_positive_requested_and_approved_long_values_survive(value):
    assert validate_reembolso(_request(valor_solicitado_cents=value)).valid is True
    result = issue_payment_entry(
        {
            "protocolo_reembolso": "synthetic",
            "decisao_reembolso": "APROVAR",
            "valor_reembolso_aprovado_cents": value,
        }
    )
    assert result["valor_reembolso_aprovado_cents"] == value


@pytest.mark.parametrize("value", [0, 2**31 - 1])
def test_finite_money_dmn_integer_boundaries_and_sem_tabela_preserved(value):
    variables = _entry_vars(
        _DMN_RULES[0],
        valor_calculado_tabela_cents=value,
        fonte_tabela="SEM_TABELA" if value == 0 else "synthetic",
    )
    result = calculate_amount_entry(variables)
    assert result["valor_calculado_tabela_cents"] == value
    assert result["reembolso_auto_liberado"] is False
    if value == 0:
        assert result["dentro_tabela"] is False


@pytest.mark.parametrize(
    "field,value",
    [
        ("valor_solicitado_cents", True),
        ("valor_solicitado_cents", 12.9),
        ("valor_solicitado_cents", 2**63),
        ("valor_calculado_tabela_cents", 2**31),
        ("valor_calculado_tabela_cents", 2**63),
    ],
)
async def test_finite_money_public_calculation_producer_refuses_before_completion(field, value):
    import json

    import httpx

    from maezo.tools.workers.harness import CibSevenWorkerTransport

    posts = []

    def capture(request):
        posts.append(json.loads(request.content))
        return httpx.Response(204)

    transport = CibSevenWorkerTransport("https://engine.invalid")
    await transport._client.aclose()
    async with httpx.AsyncClient(
        base_url="https://engine.invalid", transport=httpx.MockTransport(capture)
    ) as client:
        transport._client = client
        with pytest.raises(ReembolsoCalculoIndisponivelError):
            result = calculate_amount_entry(_entry_vars(_DMN_RULES[0], **{field: value}))
            await transport.complete("synthetic-task", "synthetic-worker", result)
    assert posts == []


def test_finite_money_dmn_declared_integer_output_is_the_narrow_guard_authority():
    outputs = ET.parse(_DMN_CALCULO_PATH).findall(".//dmn:output", _DMN_NS)
    amount = next(node for node in outputs if node.attrib["name"] == "valor_calculado_tabela_cents")
    assert amount.attrib["typeRef"] == "integer"


@pytest.mark.parametrize(
    "kind,value", [("calculation", 0), ("calculation", 2**31 - 1), ("payment", 2**31), ("payment", 2**63 - 1)]
)
async def test_finite_money_public_producer_completes_exact_valid_amount(kind, value):
    import json

    import httpx

    from maezo.tools.workers.harness import CibSevenWorkerTransport

    posts = []

    def capture(request):
        posts.append(json.loads(request.content))
        return httpx.Response(204)

    if kind == "calculation":
        result = calculate_amount_entry(
            _entry_vars(
                _DMN_RULES[0],
                valor_calculado_tabela_cents=value,
                fonte_tabela="SEM_TABELA" if value == 0 else "synthetic",
            )
        )
        field = "valor_calculado_tabela_cents"
    else:
        result = issue_payment_entry(
            {
                "protocolo_reembolso": "synthetic",
                "decisao_reembolso": "APROVAR",
                "valor_reembolso_aprovado_cents": value,
            }
        )
        field = "valor_reembolso_aprovado_cents"
    transport = CibSevenWorkerTransport("https://engine.invalid")
    await transport._client.aclose()
    async with httpx.AsyncClient(
        base_url="https://engine.invalid", transport=httpx.MockTransport(capture)
    ) as client:
        transport._client = client
        await transport.complete("synthetic-task", "synthetic-worker", result)
    assert len(posts) == 1
    assert posts[0]["variables"][field] == {
        "value": value,
        "type": "Integer" if value <= 2**31 - 1 else "Long",
    }
