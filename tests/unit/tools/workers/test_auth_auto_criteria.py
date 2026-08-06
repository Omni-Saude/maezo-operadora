"""`operadora.auth.validate_auto_criteria` — the GAP-AUTH-4 criteria gate.

Behaviour pins for `ValidateAutoCriteriaWorker` and the ratification loader. The STRUCTURAL
claims (the BPMN fence, the DMN rewire, the audit reach, the never-raise rule) live in
`tests/unit/sec/test_auto_approval_criteria_fence.py` — those are the assertions that genuinely
failed before this change; these are regression pins on new code.

The single most important test in this file is
`test_a_draft_source_cannot_pass_even_when_the_table_says_yes`: the ratification gate is what
stops SYNTHETIC clinical rules from granting REAL authorizations.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from maezo.tools.workers.auth import ValidateAutoCriteriaWorker
from maezo.tools.workers.auth_criteria import (
    MANIFEST_PATH_ENV,
    CriteriaSources,
    load_criteria_sources,
)
from maezo.tools.workers.dmn_transport import DmnEvaluationError, FakeDmnTransport

_REPO_ROOT = Path(__file__).resolve().parents[4]
_REAL_MANIFEST = _REPO_ROOT / "spec" / "processes" / "dmn" / "auth-criteria-ratification.yaml"

_ALL_SOURCES = (
    "dut_rol_coverage",
    "dut_criteria_bariatrica",
    "dut_criteria_oncologia_pet_ct",
    "dut_criteria_terapias_especiais",
    "carencia_check",
    "auth_criteria_contratual",
)


# ---------------------------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------------------------


def _sources(*ratified: str, dut_map: dict[str, str] | None = None) -> CriteriaSources:
    return CriteriaSources(
        ratified=frozenset(ratified),
        dut_criteria_by_ref=dut_map
        if dut_map is not None
        else {"DUT-BARIATRICA-001": "dut_criteria_bariatrica"},
    )


def _dmn(
    *,
    no_rol: Any = False,
    requer_dut: Any = False,
    dut_ref: Any = "NAO_APLICA",
    carencia_cumprida: Any = True,
    contratual_ok: Any = False,
    contratual_motivo: Any = "SEM_REGRA_RATIFICADA",
    dut_atendida: Any = True,
) -> FakeDmnTransport:
    dmn = FakeDmnTransport()
    dmn.register("dut_rol_coverage", [{"no_rol": no_rol, "requer_dut": requer_dut, "dut_ref": dut_ref}])
    dmn.register("dut_criteria_bariatrica", [{"dut_atendida": dut_atendida, "motivo": "x"}])
    dmn.register("carencia_check", [{"carencia_cumprida": carencia_cumprida, "prazo_restante_dias": 0}])
    dmn.register(
        "auth_criteria_contratual",
        [{"criterio_contratual_ok": contratual_ok, "motivo": contratual_motivo}],
    )
    return dmn


class _Resolver:
    """Stand-in for `CeilingResolver` — `within` is what `within_l2_ceiling` returns."""

    def __init__(self, within: Any = True, raises: bool = False) -> None:
        self._within = within
        self._raises = raises
        self.calls: list[dict[str, Any]] = []

    def within_l2_ceiling(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self._raises:
            raise RuntimeError("matrix unavailable")
        return self._within


def _vars(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "tenant_id": "amh",
        "numero_guia_tiss": "GUIA-1",
        "valor_estimado_brl": 180.0,
        "codigo_procedimento_tuss": "10101012",
        "categoria_procedimento": "consulta",
        "tipo_procedimento": "eletivo",
        "dias_desde_adesao": 400,
        "cpt_declarada": False,
    }
    base.update(overrides)
    return base


def _worker(
    *,
    within: Any = True,
    raises: bool = False,
    dmn: FakeDmnTransport | None = None,
    sources: CriteriaSources | None = None,
) -> ValidateAutoCriteriaWorker:
    return ValidateAutoCriteriaWorker(
        resolver=_Resolver(within, raises),  # type: ignore[arg-type]
        dmn=dmn if dmn is not None else _dmn(),
        sources=sources if sources is not None else _sources(*_ALL_SOURCES),
    )


# ---------------------------------------------------------------------------------------------
# THE RATIFICATION GATE — the single most important behaviour here
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("criterion", "unratified", "token", "shadow"),
    [
        (
            "criterio_tecnico_ok",
            "dut_rol_coverage",
            "TECNICO_FONTE_NAO_RATIFICADA",
            "TECNICO_SOMBRA_APROVARIA",
        ),
        (
            "criterio_regulatorio_ok",
            "carencia_check",
            "REGULATORIO_FONTE_NAO_RATIFICADA",
            "REGULATORIO_SOMBRA_APROVARIA",
        ),
    ],
)
def test_a_draft_source_cannot_pass_even_when_the_table_says_yes(
    criterion: str, unratified: str, token: str, shadow: str
) -> None:
    """THE SAFETY CRUX. Each table below returns its FAVOURABLE verdict. With its source left
    unratified the criterion is still FALSE — a synthetic rule can never grant a real
    authorization. The would-be verdict survives only as shadow evidence."""
    ratified = tuple(s for s in _ALL_SOURCES if s != unratified)
    worker = _worker(sources=_sources(*ratified))
    result = worker.execute(_vars())

    assert result[criterion] is False
    assert token in result["auto_criteria_falhas"]
    assert shadow in result["auto_criteria_shadow"]


def test_ratifying_the_source_lets_the_same_table_verdict_through() -> None:
    """The converse — proving the previous test is not vacuous. Identical inputs, identical
    tables; the ONLY difference is the manifest. This is what "activates with no code change"
    means, expressed as a test."""
    unratified = _worker(sources=_sources()).execute(_vars())
    ratified = _worker(sources=_sources(*_ALL_SOURCES)).execute(_vars())

    assert unratified["criterio_tecnico_ok"] is False
    assert unratified["criterio_regulatorio_ok"] is False
    assert ratified["criterio_tecnico_ok"] is True
    assert ratified["criterio_regulatorio_ok"] is True


def test_the_procedure_specific_table_must_also_be_ratified() -> None:
    """A ratified coverage table pointing at an UNRATIFIED clinical criteria table is still a
    DRAFT chain — the criterion fails closed. Ratification is per-source, and ALL consulted
    sources must hold."""
    worker = _worker(
        dmn=_dmn(requer_dut=True, dut_ref="DUT-BARIATRICA-001 DRAFT/verify", dut_atendida=True),
        sources=_sources("dut_rol_coverage", "carencia_check", "auth_criteria_contratual"),
    )
    result = worker.execute(_vars())
    assert result["criterio_tecnico_ok"] is False
    assert "TECNICO_FONTE_NAO_RATIFICADA" in result["auto_criteria_falhas"]
    assert "TECNICO_SOMBRA_APROVARIA" in result["auto_criteria_shadow"]


# ---------------------------------------------------------------------------------------------
# Shadow mode: recorded, never influential
# ---------------------------------------------------------------------------------------------


def test_shadow_records_a_refusal_as_well_as_an_approval() -> None:
    worker = _worker(dmn=_dmn(no_rol=True), sources=_sources())
    result = worker.execute(_vars())
    assert "TECNICO_SOMBRA_REPROVARIA" in result["auto_criteria_shadow"]
    assert result["criterio_tecnico_ok"] is False


def test_shadow_is_never_emitted_for_a_ratified_source() -> None:
    """Once ratified the table's verdict IS the verdict; shadowing it would be noise."""
    result = _worker(sources=_sources(*_ALL_SOURCES)).execute(_vars())
    assert [t for t in result["auto_criteria_shadow"] if t.startswith(("TECNICO", "REGULATORIO"))] == []


def test_shadow_output_cannot_flip_a_verdict() -> None:
    """Non-influence, stated as a property: with EVERY source unratified, every possible shadow
    outcome (would-approve, would-refuse) still yields all-false criteria."""
    for dmn in (_dmn(), _dmn(no_rol=True), _dmn(carencia_cumprida=False)):
        result = _worker(dmn=dmn, sources=_sources()).execute(_vars())
        assert result["auto_criteria_shadow"], "shadow evidence must still be produced"
        assert not any(
            result[k] for k in ("criterio_tecnico_ok", "criterio_regulatorio_ok", "criterio_contratual_ok")
        )


# ---------------------------------------------------------------------------------------------
# FINANCEIRO — the one criterion with an already-ratified source
# ---------------------------------------------------------------------------------------------


def test_financeiro_passes_only_when_the_tenant_ceiling_admits_the_value() -> None:
    assert _worker(within=True).execute(_vars())["criterio_financeiro_ok"] is True
    blocked = _worker(within=False).execute(_vars())
    assert blocked["criterio_financeiro_ok"] is False
    assert "FINANCEIRO_TETO_NAO_AUTORIZA" in blocked["auto_criteria_falhas"]


def test_financeiro_uses_the_authorization_approval_ceiling() -> None:
    """The SAME action/param `AnalyzeRequestWorker` and `IssueAuthorizationWorker` already use —
    never a second, divergent ceiling."""
    resolver = _Resolver(True)
    ValidateAutoCriteriaWorker(
        resolver=resolver,  # type: ignore[arg-type]
        dmn=_dmn(),
        sources=_sources(),
    ).execute(_vars())
    assert resolver.calls[0]["action"] == "authorization_approval"
    assert resolver.calls[0]["param"] == "max_value_brl"
    assert resolver.calls[0]["value_cents"] == 18000


@pytest.mark.parametrize(
    ("valor", "token"),
    [
        (None, "FINANCEIRO_VALOR_INVALIDO"),
        (True, "FINANCEIRO_VALOR_INVALIDO"),  # bool is an int subclass — never R$1,00
        ("abc", "FINANCEIRO_VALOR_INVALIDO"),
        (-1.0, "FINANCEIRO_VALOR_INVALIDO"),
        (float("inf"), "FINANCEIRO_VALOR_INVALIDO"),
        (float("nan"), "FINANCEIRO_VALOR_INVALIDO"),
        ([180.0], "FINANCEIRO_VALOR_INVALIDO"),
    ],
)
def test_financeiro_fails_closed_on_every_untrustworthy_value(valor: Any, token: str) -> None:
    result = _worker().execute(_vars(valor_estimado_brl=valor))
    assert result["criterio_financeiro_ok"] is False
    assert token in result["auto_criteria_falhas"]


@pytest.mark.parametrize("tenant", [None, "", "   ", 42])
def test_financeiro_fails_closed_without_a_usable_tenant(tenant: Any) -> None:
    result = _worker().execute(_vars(tenant_id=tenant))
    assert result["criterio_financeiro_ok"] is False
    assert "FINANCEIRO_TENANT_AUSENTE" in result["auto_criteria_falhas"]


def test_financeiro_rejects_a_truthy_non_boolean_resolver_verdict() -> None:
    """`is not True`, not `not within`: a stub returning truthy junk must not open approval."""
    result = _worker(within="yes").execute(_vars())
    assert result["criterio_financeiro_ok"] is False


def test_financeiro_fails_closed_when_the_resolver_raises() -> None:
    result = _worker(raises=True).execute(_vars())
    assert result["criterio_financeiro_ok"] is False
    assert "FINANCEIRO_RESOLVER_INDISPONIVEL" in result["auto_criteria_falhas"]


# ---------------------------------------------------------------------------------------------
# TECNICO
# ---------------------------------------------------------------------------------------------


def test_tecnico_fails_closed_when_the_procedure_is_outside_the_rol() -> None:
    result = _worker(dmn=_dmn(no_rol=True), sources=_sources(*_ALL_SOURCES)).execute(_vars())
    assert result["criterio_tecnico_ok"] is False
    assert "TECNICO_FORA_DO_ROL" in result["auto_criteria_falhas"]


def test_tecnico_consults_the_procedure_specific_table_when_a_dut_is_required() -> None:
    dmn = _dmn(requer_dut=True, dut_ref="DUT-BARIATRICA-001 DRAFT/verify", dut_atendida=True)
    result = _worker(dmn=dmn, sources=_sources(*_ALL_SOURCES)).execute(_vars())
    assert [key for key, _ in dmn.calls] == [
        "dut_rol_coverage",
        "dut_criteria_bariatrica",
        "carencia_check",
        "auth_criteria_contratual",
    ]
    assert result["criterio_tecnico_ok"] is True


def test_tecnico_fails_closed_when_the_clinical_criteria_table_refuses() -> None:
    dmn = _dmn(requer_dut=True, dut_ref="DUT-BARIATRICA-001", dut_atendida=False)
    result = _worker(dmn=dmn, sources=_sources(*_ALL_SOURCES)).execute(_vars())
    assert result["criterio_tecnico_ok"] is False
    assert "TECNICO_DUT_NAO_ATENDIDA" in result["auto_criteria_falhas"]


def test_tecnico_fails_closed_on_an_unmapped_dut_reference() -> None:
    """Guessing which clinical criteria table applies to a DUT would be inventing a clinical
    judgement — an unmapped ref routes to a human instead."""
    dmn = _dmn(requer_dut=True, dut_ref="DUT-ALGO-NOVO-001 DRAFT/verify")
    result = _worker(dmn=dmn, sources=_sources(*_ALL_SOURCES)).execute(_vars())
    assert result["criterio_tecnico_ok"] is False
    assert "TECNICO_PROCEDIMENTO_NAO_MAPEADO" in result["auto_criteria_falhas"]


@pytest.mark.parametrize("codigo", [None, "", "  ", 10101012])
def test_tecnico_fails_closed_without_a_usable_tuss_code(codigo: Any) -> None:
    result = _worker().execute(_vars(codigo_procedimento_tuss=codigo))
    assert result["criterio_tecnico_ok"] is False
    assert "TECNICO_ENTRADA_AUSENTE" in result["auto_criteria_falhas"]


@pytest.mark.parametrize("bad", [None, "true", 1, "false"])
def test_tecnico_fails_closed_on_a_non_boolean_coverage_output(bad: Any) -> None:
    """A DMN output that is not a real boolean is indeterminate, never a truthy pass-through."""
    result = _worker(dmn=_dmn(no_rol=bad), sources=_sources(*_ALL_SOURCES)).execute(_vars())
    assert result["criterio_tecnico_ok"] is False


def test_tecnico_forwards_only_the_declared_clinical_inputs() -> None:
    """PHI egress (ADR-0006) + the `_to_camunda_vars` wrong-type caveat: only inputs the selected
    table declares are shipped. Clinical free text and the pseudo id must never reach the DMN
    endpoint."""
    dmn = _dmn(requer_dut=True, dut_ref="DUT-BARIATRICA-001", dut_atendida=True)
    _worker(dmn=dmn, sources=_sources(*_ALL_SOURCES)).execute(
        _vars(
            imc=41,
            imc_acima_40=True,
            justificativa_clinica="texto clinico livre",
            cid10_referencia="E66.0",
            beneficiario_pseudo_id="PSEUDO-1",
        )
    )
    sent = dict(next(v for key, v in dmn.calls if key == "dut_criteria_bariatrica"))
    assert sent == {"imc": 41, "imc_acima_40": True}
    for leaked in ("justificativa_clinica", "cid10_referencia", "beneficiario_pseudo_id"):
        assert leaked not in sent


def test_tecnico_fails_closed_when_the_coverage_table_is_unreachable() -> None:
    dmn = FakeDmnTransport()  # nothing registered -> DmnEvaluationError
    result = _worker(dmn=dmn, sources=_sources(*_ALL_SOURCES)).execute(_vars())
    assert result["criterio_tecnico_ok"] is False
    assert "TECNICO_TABELA_INDISPONIVEL" in result["auto_criteria_falhas"]


def test_an_unwired_dmn_seam_still_returns_a_verdict_instead_of_stalling() -> None:
    """`require_dmn` is deliberately NOT used: raising would become an incident, and an incident
    stalls a care-authorization request. Every DMN-backed criterion degrades instead."""
    worker = ValidateAutoCriteriaWorker(
        resolver=_Resolver(True),  # type: ignore[arg-type]
        dmn=None,
        sources=_sources(*_ALL_SOURCES),
    )
    result = worker.execute(_vars())
    assert result["auto_criteria_verificado"] is True
    assert result["criterio_tecnico_ok"] is False
    assert result["criterio_regulatorio_ok"] is False
    assert result["criterio_contratual_ok"] is False
    # the ceiling is NOT DMN-backed, so it still computes normally
    assert result["criterio_financeiro_ok"] is True


# ---------------------------------------------------------------------------------------------
# REGULATORIO
# ---------------------------------------------------------------------------------------------


def test_regulatorio_fails_closed_when_carencia_is_not_met() -> None:
    result = _worker(dmn=_dmn(carencia_cumprida=False), sources=_sources(*_ALL_SOURCES)).execute(_vars())
    assert result["criterio_regulatorio_ok"] is False
    assert "REGULATORIO_CARENCIA_NAO_CUMPRIDA" in result["auto_criteria_falhas"]


@pytest.mark.parametrize(
    "override",
    [
        {"tipo_procedimento": None},
        {"tipo_procedimento": ""},
        {"dias_desde_adesao": None},
        {"dias_desde_adesao": True},  # bool is an int subclass — never "1 day"
        {"dias_desde_adesao": "400"},
        {"dias_desde_adesao": -1},
        {"cpt_declarada": None},
        {"cpt_declarada": "false"},
    ],
)
def test_regulatorio_fails_closed_without_trustworthy_inputs(override: dict[str, Any]) -> None:
    """These three inputs do NOT exist in SP-OP-AUTH-001's start contract today (cadastro data
    behind the AMH boundary, MZO-050b). Failing closed here is the honest outcome — the seam is
    real and evaluates the moment the inputs exist."""
    result = _worker().execute(_vars(**override))
    assert result["criterio_regulatorio_ok"] is False
    assert "REGULATORIO_ENTRADA_AUSENTE" in result["auto_criteria_falhas"]


def test_regulatorio_does_not_overwrite_the_seeded_carencia_cumprida() -> None:
    """`carencia_cumprida` is a DIFFERENT variable with a different provenance, consumed upstream
    by `auth_admissibility`. This gate publishes its own unambiguous fact instead of silently
    redefining a homonym other readers already interpret."""
    result = _worker(sources=_sources(*_ALL_SOURCES)).execute(_vars(carencia_cumprida=False))
    assert "carencia_cumprida" not in result
    assert result["criterio_regulatorio_ok"] is True


# ---------------------------------------------------------------------------------------------
# CONTRATUAL
# ---------------------------------------------------------------------------------------------


def test_contratual_reports_the_absence_of_any_ratified_rule() -> None:
    result = _worker(sources=_sources(*_ALL_SOURCES)).execute(_vars())
    assert result["criterio_contratual_ok"] is False
    assert "CONTRATUAL_SEM_FONTE" in result["auto_criteria_falhas"]


def test_contratual_fails_closed_when_the_table_is_unreachable() -> None:
    dmn = _dmn()
    dmn._responses.pop("auth_criteria_contratual")  # noqa: SLF001 - simulate a missing deployment
    result = _worker(dmn=dmn, sources=_sources(*_ALL_SOURCES)).execute(_vars())
    assert result["criterio_contratual_ok"] is False
    assert "CONTRATUAL_TABELA_INDISPONIVEL" in result["auto_criteria_falhas"]


# ---------------------------------------------------------------------------------------------
# Execution fence, computed-overwrites-seeded, degradation
# ---------------------------------------------------------------------------------------------


def test_every_path_writes_all_five_criteria_variables() -> None:
    """Computed OVERWRITES seeded — on every path, including degradation. A start-payload
    homonym can never survive to `BRT_AutoApproval`."""
    seeded = _vars(
        criterio_tecnico_ok=True,
        criterio_financeiro_ok=True,
        criterio_regulatorio_ok=True,
        criterio_contratual_ok=True,
        auto_criteria_verificado=True,
    )
    for worker in (_worker(sources=_sources()), _BoomWorker()):
        result = worker.execute(seeded)
        assert result["auto_criteria_verificado"] is True
        assert result["criterio_tecnico_ok"] is False
        assert result["criterio_financeiro_ok"] is False or isinstance(worker, ValidateAutoCriteriaWorker)
        assert result["criterio_regulatorio_ok"] is False
        assert result["criterio_contratual_ok"] is False


class _BoomWorker(ValidateAutoCriteriaWorker):
    """A validator whose internals blow up — exercises the design §6 degradation path."""

    def __init__(self) -> None:
        super().__init__(
            resolver=_Resolver(True),  # type: ignore[arg-type]
            dmn=_dmn(),
            sources=_sources(),
        )

    def _sources(self) -> CriteriaSources:
        raise RuntimeError("boom — simulated internal failure")


def test_degradation_returns_all_false_with_the_validador_indisponivel_token() -> None:
    """Fail-safe AND visible: never an incident (that would stall the request), always a
    disclosed, audited refusal."""
    result = _BoomWorker().execute(_vars())
    assert result["status"] == "validator_unavailable"
    assert result["auto_criteria_verificado"] is True
    assert result["motivo_bloqueio_criterios"] == "VALIDADOR_INDISPONIVEL"
    # ONE token, not four copies: the same failure across all four criteria must not read as
    # four distinct problems in process history.
    assert result["auto_criteria_falhas"] == ["VALIDADOR_INDISPONIVEL"]
    for key in (
        "criterio_tecnico_ok",
        "criterio_financeiro_ok",
        "criterio_regulatorio_ok",
        "criterio_contratual_ok",
    ):
        assert result[key] is False


def test_degradation_never_raises() -> None:
    """ADR-0030: `ST_ValidateAutoApprovalCriteria` has no boundary, so a raise would silently end
    the process scope."""
    assert _BoomWorker().execute({}) is not None


def test_the_primary_audit_token_is_the_first_failure_in_criterion_order() -> None:
    result = _worker(sources=_sources()).execute(_vars())
    assert result["motivo_bloqueio_criterios"] == result["auto_criteria_falhas"][0]
    assert result["motivo_bloqueio_criterios"].startswith("TECNICO_")


def test_all_criteria_met_yields_no_failures_and_an_empty_audit_token() -> None:
    """The ONLY shape that lets `auth_auto_approval` rule r1 fire."""
    result = _worker(within=True, sources=_sources(*_ALL_SOURCES), dmn=_dmn(contratual_ok=True)).execute(
        _vars()
    )
    assert result["auto_criteria_falhas"] == []
    assert result["motivo_bloqueio_criterios"] == ""
    assert all(
        result[k]
        for k in (
            "criterio_tecnico_ok",
            "criterio_financeiro_ok",
            "criterio_regulatorio_ok",
            "criterio_contratual_ok",
            "auto_criteria_verificado",
        )
    )


def test_the_worker_never_emits_a_denial_or_an_error_code() -> None:
    """L0 hard: this gate chooses auto-approve vs human review. It has no adverse output."""
    for sources in (_sources(), _sources(*_ALL_SOURCES)):
        result = _worker(sources=sources).execute(_vars())
        assert "error_code" not in result
        assert result["status"] in {"criteria_validated", "validator_unavailable"}
        assert "NEGAR" not in str(result)


# ---------------------------------------------------------------------------------------------
# The ratification loader
# ---------------------------------------------------------------------------------------------


def test_the_real_shipped_manifest_ratifies_nothing_and_maps_two_refs() -> None:
    sources = load_criteria_sources(_REAL_MANIFEST)
    assert sources.ratified == frozenset()
    assert sources.criteria_table_for("DUT-BARIATRICA-001 DRAFT/verify") == "dut_criteria_bariatrica"
    assert sources.criteria_table_for("DUT-PET-CT-ONCOLOGIA-001 x") == "dut_criteria_oncologia_pet_ct"
    assert sources.criteria_table_for("DUT-FONOAUDIOLOGIA-001") is None


def _write(tmp_path: Path, payload: Any) -> Path:
    path = tmp_path / "manifest.yaml"
    path.write_text(yaml.safe_dump(payload, allow_unicode=True), encoding="utf-8")
    return path


def test_loader_accepts_a_complete_ratification(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        {
            "fontes": {
                "carencia_check": {
                    "ratificado": True,
                    "revisor": "juridico",
                    "ratificado_em": "2026-08-06",
                }
            }
        },
    )
    assert load_criteria_sources(path).is_ratified("carencia_check")


@pytest.mark.parametrize(
    "entry",
    [
        {"ratificado": "true", "revisor": "j", "ratificado_em": "2026-08-06"},  # truthy string
        {"ratificado": 1, "revisor": "j", "ratificado_em": "2026-08-06"},
        {"ratificado": True, "ratificado_em": "2026-08-06"},  # no reviewer
        {"ratificado": True, "revisor": "  ", "ratificado_em": "2026-08-06"},
        {"ratificado": True, "revisor": "j"},  # no date
        {"ratificado": True, "revisor": "j", "ratificado_em": None},
        {"ratificado": False, "revisor": "j", "ratificado_em": "2026-08-06"},
        "not-a-mapping",
    ],
)
def test_loader_refuses_every_incomplete_or_junk_ratification(tmp_path: Path, entry: Any) -> None:
    """Ratification is an ACCOUNTABLE act (who + when), pinned to the boolean literal `True`."""
    path = _write(tmp_path, {"fontes": {"carencia_check": entry}})
    assert not load_criteria_sources(path).is_ratified("carencia_check")


@pytest.mark.parametrize(
    "payload",
    [
        {
            "unratified": True,
            "fontes": {"carencia_check": {"ratificado": True, "revisor": "j", "ratificado_em": "d"}},
        },
        ["not", "a", "mapping"],
        {"fontes": "not-a-mapping"},
    ],
)
def test_loader_fails_closed_on_a_template_or_malformed_manifest(tmp_path: Path, payload: Any) -> None:
    """Mirrors `load_retention_matrix`'s refusal of its own `unratified: true` template — note
    the ratified entry inside the first payload is discarded WHOLESALE."""
    path = _write(tmp_path, payload)
    assert load_criteria_sources(path).ratified == frozenset()


def test_loader_fails_closed_on_a_missing_file(tmp_path: Path) -> None:
    sources = load_criteria_sources(tmp_path / "absent.yaml")
    assert sources.ratified == frozenset()
    assert sources.criteria_table_for("DUT-BARIATRICA-001") is None


def test_loader_fails_closed_on_malformed_yaml(tmp_path: Path) -> None:
    path = tmp_path / "m.yaml"
    path.write_text("fontes: [unclosed\n", encoding="utf-8")
    assert load_criteria_sources(path).ratified == frozenset()


def test_loader_fails_closed_on_a_non_utf8_manifest(tmp_path: Path) -> None:
    path = tmp_path / "m.yaml"
    path.write_bytes("fontes: {carencia_check: {ratificado: true, revisor: jurídico}}".encode("cp1252"))
    assert load_criteria_sources(path).ratified == frozenset()


def test_env_override_selects_the_manifest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _write(
        tmp_path,
        {"fontes": {"dut_rol_coverage": {"ratificado": True, "revisor": "m", "ratificado_em": "d"}}},
    )
    monkeypatch.setenv(MANIFEST_PATH_ENV, str(path))
    assert load_criteria_sources().is_ratified("dut_rol_coverage")


@pytest.mark.parametrize("ref", [None, 42, "", "   ", ["DUT-BARIATRICA-001"]])
def test_criteria_table_lookup_fails_closed_on_junk_refs(ref: Any) -> None:
    assert load_criteria_sources(_REAL_MANIFEST).criteria_table_for(ref) is None


def test_dmn_evaluation_error_is_the_fake_transports_unregistered_signal() -> None:
    """Guards the negative-path fixtures above: an unregistered key really does raise."""
    with pytest.raises(DmnEvaluationError):
        import asyncio

        asyncio.run(FakeDmnTransport().evaluate("nope", {}))
