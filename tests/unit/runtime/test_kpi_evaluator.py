"""R-085 / F-091: todo KPI declarado em `spec/agents/*/agent.yaml` tem avaliador de meta e fonte
de medicao DECLARADA (inclusive `sem_fonte`, honestamente) — nunca um nome sem consumidor."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Final

import pytest
import yaml

from maezo.runtime.kpi_evaluator import (
    MEASUREMENT_SOURCES,
    DeclaredKpi,
    KpiTargetError,
    KpiVerdict,
    SourceKind,
    TargetKind,
    evaluate_declared,
    evaluate_kpi,
    load_declared_kpis,
    parse_target,
)

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[3]
_AGENTS_DIR: Final[Path] = _REPO_ROOT / "spec" / "agents"
_ALERT_RULES: Final[Path] = _REPO_ROOT / "deploy" / "observability" / "alert-rules.yml"


# --- cobertura: a tabela de fontes casa EXATAMENTE os agent.yaml --------------------------------


def test_kpi_evaluator_parse_of_real_blueprints_is_non_vacuous() -> None:
    declared = load_declared_kpis(_AGENTS_DIR)
    names = {kpi.name for kpi in declared}
    # Os oito nomes que F-091 encontrou sem consumidor em `src/`.
    assert {
        "eval_score",
        "dossier_completeness",
        "nps",
        "submission_on_time_rate",
        "notice_compliance",
        "care_plan_completeness",
        "lgpd_erasure_coverage",
        "sla_analise_p95",
    } <= names
    assert "_template" not in {kpi.agent_id for kpi in declared}


def test_kpi_evaluator_every_declared_kpi_has_a_declared_source_and_no_stale_entry() -> None:
    declared = {(kpi.agent_id, kpi.name) for kpi in load_declared_kpis(_AGENTS_DIR)}
    assert declared - set(MEASUREMENT_SOURCES) == set(), "KPI declarado sem fonte de medicao declarada"
    assert set(MEASUREMENT_SOURCES) - declared == set(), "fonte declarada para KPI que nao existe mais"


def test_kpi_evaluator_sem_fonte_entries_say_why() -> None:
    for key, source in MEASUREMENT_SOURCES.items():
        if source.kind is SourceKind.SEM_FONTE:
            assert source.note.strip(), key
            assert source.ref == "", key
        else:
            assert source.ref.strip(), key


def test_kpi_evaluator_recording_rule_sources_exist_in_the_shipped_rules_file() -> None:
    document: Any = yaml.safe_load(_ALERT_RULES.read_text(encoding="utf-8"))
    records = {rule["record"] for group in document["groups"] for rule in group["rules"] if "record" in rule}
    wanted = {s.ref for s in MEASUREMENT_SOURCES.values() if s.kind is SourceKind.RECORDING_RULE}
    assert wanted, "nenhuma fonte recording_rule — teste vacuo"
    assert wanted <= records, sorted(wanted - records)


def test_kpi_evaluator_histogram_sources_are_declared_histograms() -> None:
    from maezo.runtime.metrics import MetricsCollector

    histograms = {f.name for f in MetricsCollector().registry.collect() if f.type == "histogram"}
    wanted = {s.ref for s in MEASUREMENT_SOURCES.values() if s.kind is SourceKind.HISTOGRAM_P95}
    assert wanted, "nenhuma fonte histogram_p95 — teste vacuo"
    assert wanted <= histograms, sorted(wanted - histograms)


def test_kpi_evaluator_eval_score_is_sem_fonte_while_no_dataset_is_collectable() -> None:
    """`make evals` existe, mas tests/evals nao tem golden dataset coletavel: sem medicao."""
    entries = [s for (_agent, kpi), s in MEASUREMENT_SOURCES.items() if kpi == "eval_score"]
    assert len(entries) == 10, len(entries)
    for source in entries:
        assert source.kind is SourceKind.SEM_FONTE
        assert "make evals" in source.note and "coletavel" in source.note


# --- gramatica de meta -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "kind", "operator", "threshold", "unit"),
    [
        ("track", TargetKind.TRACK, None, None, None),
        ("==0", TargetKind.COMPARACAO, "==", 0.0, None),
        ("==1.0", TargetKind.COMPARACAO, "==", 1.0, None),
        (">0.95", TargetKind.COMPARACAO, ">", 0.95, None),
        (">75", TargetKind.COMPARACAO, ">", 75.0, None),
        ("<15s", TargetKind.COMPARACAO, "<", 15.0, "s"),
        (">=0.5", TargetKind.COMPARACAO, ">=", 0.5, None),
        ("<=2", TargetKind.COMPARACAO, "<=", 2.0, None),
        ("<RN259/395", TargetKind.REFERENCIA, "<", None, None),
    ],
)
def test_kpi_evaluator_parses_the_closed_target_grammar(
    raw: str, kind: TargetKind, operator: str | None, threshold: float | None, unit: str | None
) -> None:
    target = parse_target(raw)
    assert (target.kind, target.operator, target.threshold, target.unit) == (kind, operator, threshold, unit)
    assert target.raw == raw


@pytest.mark.parametrize("raw", ["", "   ", "alto", "~0.9", ">", "== ", "0.9", "track it"])
def test_kpi_evaluator_rejects_targets_outside_the_grammar(raw: str) -> None:
    with pytest.raises(KpiTargetError):
        parse_target(raw)


# --- veredito ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "measured", "verdict"),
    [
        ("track", 0.5, KpiVerdict.SEM_META),
        ("track", None, KpiVerdict.SEM_META),
        ("<RN259/395", 10.0, KpiVerdict.META_NAO_RESOLVIVEL),
        (">0.9", None, KpiVerdict.SEM_MEDICAO),
        (">0.9", 0.91, KpiVerdict.ATINGIDA),
        (">0.9", 0.9, KpiVerdict.NAO_ATINGIDA),  # estrito: igual nao atinge `>`
        (">0.9", 0.89, KpiVerdict.NAO_ATINGIDA),
        (">=0.9", 0.9, KpiVerdict.ATINGIDA),
        ("==0", 0.0, KpiVerdict.ATINGIDA),
        ("==0", 1.0, KpiVerdict.NAO_ATINGIDA),
        ("==1.0", 0.9999999999999, KpiVerdict.ATINGIDA),  # razao calculada, tolerancia absoluta
        ("==1.0", 0.99, KpiVerdict.NAO_ATINGIDA),
        ("<15s", 14.9, KpiVerdict.ATINGIDA),
        ("<15s", 15.0, KpiVerdict.NAO_ATINGIDA),
        ("<=15", 15.0, KpiVerdict.ATINGIDA),
        ("<=15", 15.1, KpiVerdict.NAO_ATINGIDA),
    ],
)
def test_kpi_evaluator_verdicts(raw: str, measured: float | None, verdict: KpiVerdict) -> None:
    assert evaluate_kpi(parse_target(raw), measured) is verdict


@pytest.mark.parametrize("bad", [True, float("nan"), "0.9"])
def test_kpi_evaluator_refuses_an_invalid_measurement(bad: object) -> None:
    with pytest.raises(KpiTargetError):
        evaluate_kpi(parse_target(">0.9"), bad)  # type: ignore[arg-type]


def test_kpi_evaluator_evaluates_declared_kpis_with_injected_measurements() -> None:
    declared = load_declared_kpis(_AGENTS_DIR)
    results = {
        (r.agent_id, r.name): r
        for r in evaluate_declared(
            declared,
            {("helena", "first_response_p95"): 12.0, ("lucas", "resolution_rate"): 0.4},
        )
    }
    assert results[("helena", "first_response_p95")].verdict is KpiVerdict.ATINGIDA
    assert results[("lucas", "resolution_rate")].verdict is KpiVerdict.SEM_META
    assert results[("helena", "eval_score")].verdict is KpiVerdict.SEM_MEDICAO
    assert results[("helena", "nps")].verdict is KpiVerdict.SEM_MEDICAO
    assert results[("helena", "escalation_rate")].verdict is KpiVerdict.SEM_META
    assert results[("rafael", "sla_analise_p95")].verdict is KpiVerdict.META_NAO_RESOLVIVEL
    assert results[("helena", "first_response_p95")].source.kind is SourceKind.HISTOGRAM_P95


def test_kpi_evaluator_fails_closed_on_a_kpi_without_declared_source() -> None:
    orphan = DeclaredKpi("helena", "kpi_inventado_sem_fonte", parse_target("track"))
    with pytest.raises(KeyError):
        evaluate_declared([orphan], {})


def test_kpi_evaluator_rejects_a_malformed_blueprint_entry(tmp_path: Path) -> None:
    agent_dir = tmp_path / "x"
    agent_dir.mkdir()
    (agent_dir / "agent.yaml").write_text("kpis:\n  - { name: so_nome }\n", encoding="utf-8")
    with pytest.raises(KpiTargetError):
        load_declared_kpis(tmp_path)


@pytest.mark.parametrize(
    "key",
    [("helena", "nps"), ("rafael", "false_denial_rate"), ("andre", "eval_score")],
)
def test_kpi_evaluator_refuses_a_measurement_injected_for_a_sem_fonte_kpi(key: tuple[str, str]) -> None:
    """Sem emissor, o numero veio de lugar nenhum: nunca vira `atingida` (inclusive invariante L0)."""
    assert MEASUREMENT_SOURCES[key].kind is SourceKind.SEM_FONTE
    with pytest.raises(KpiTargetError):
        evaluate_declared(load_declared_kpis(_AGENTS_DIR), {key: 0.0})
