"""Unit tests for maezo.tools.workers.contas — SP-OP-CONTAS-001.

TDD London School: tests exercise the external task contracts.
"""

import asyncio
from typing import Any

import pytest

from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport, ProcessInstance, start_dedup_key
from maezo.tools.workers.contas import (
    FRAUDE_PROCESS_KEY,
    RECURSO_PROCESS_KEY,
    ContasFraudeSemAlvoError,
    ContasLoteInvalidoError,
    ContasRecursoSemGlosaError,
    GlosaAcceptInput,
    GlosaAcceptNotHumanError,
    GlosaInput,
    _recurso_business_key,
    analyze_reason,
    analyze_reason_entry,
    calculate_impact,
    identify_glosa,
    identify_glosa_entry,
    notify_sla_risk,
    prepare_triage_dossier,
    publish,
    publish_entry,
    reconcile_payment,
    register_glosa_accept,
    register_glosa_accept_entry,
    start_fraude,
    start_recurso,
)
from maezo.tools.workers.dmn_transport import DmnEvaluationError, FakeDmnTransport
from maezo.tools.workers.harness import AUDIT_AGENT_ID
from tests.support.audit_fakes import FakeStartAuditSink


def _glosa_reason_normalization_fake(*, categoria_normalizada: str, descricao: str = "") -> FakeDmnTransport:
    """Rows verified live against the compose engine (T1.5 parity run)."""
    fake = FakeDmnTransport()
    fake.register(
        "glosa_reason_normalization",
        [{"categoria_normalizada": categoria_normalizada, "descricao": descricao}],
    )
    return fake


def _glosa_triage_fake(*, roteamento: str, motivo: str = "") -> FakeDmnTransport:
    """Rows verified live against the compose engine (T1.5 parity run)."""
    fake = FakeDmnTransport()
    fake.register("glosa_triage", [{"roteamento": roteamento, "motivo": motivo}])
    return fake


# ---------------------------------------------------------------------------
# identify_glosa
# ---------------------------------------------------------------------------


def test_identify_glosa_no_glosas() -> None:
    """identify_glosa returns has_glosas=False for a clean lote."""
    inp = GlosaInput(
        tenant_id="amh",
        numero_lote_tiss="LOTE-001",
        valor_apresentado_brl=1000.0,
        linhas_conta_refs=[
            {"valor_apresentado_centavos": 50000, "valor_glosado_centavos": 0},
            {"valor_apresentado_centavos": 50000, "valor_glosado_centavos": 0},
        ],
    )
    result = identify_glosa(inp)
    assert result.has_glosas is False
    assert result.denial_ratio == 0.0
    assert result.divergencia_valor is False
    assert result.glosa_count == 0


def test_identify_glosa_with_glosas() -> None:
    """identify_glosa detects glosa candidates from linhas with valor_glosado > 0."""
    inp = GlosaInput(
        tenant_id="amh",
        numero_lote_tiss="LOTE-002",
        valor_apresentado_brl=1500.0,
        linhas_conta_refs=[
            {"valor_apresentado_centavos": 100000, "valor_glosado_centavos": 30000},
            {"valor_apresentado_centavos": 50000, "valor_glosado_centavos": 0},
        ],
    )
    result = identify_glosa(inp)
    assert result.has_glosas is True
    assert result.glosa_count == 1
    assert result.denial_ratio > 0.0
    assert result.divergencia_valor is True


def test_identify_glosa_fail_closed_empty_lines() -> None:
    """Empty linhas_conta_refs -> fail-closed: has_glosas=True, denial_ratio=1.0."""
    inp = GlosaInput(
        tenant_id="amh",
        numero_lote_tiss="LOTE-003",
        valor_apresentado_brl=500.0,
        linhas_conta_refs=[],
    )
    result = identify_glosa(inp)
    assert result.has_glosas is True
    assert result.denial_ratio == 1.0
    assert result.divergencia_valor is True


def test_identify_glosa_reason_codes_trigger_glosa() -> None:
    """Reason codes in the lote-level list also trigger has_glosas."""
    inp = GlosaInput(
        tenant_id="amh",
        numero_lote_tiss="LOTE-004",
        valor_apresentado_brl=1000.0,
        linhas_conta_refs=[
            {"valor_apresentado_centavos": 100000, "valor_glosado_centavos": 0},
        ],
        reason_codes_tiss=["COD001"],
    )
    result = identify_glosa(inp)
    assert result.has_glosas is True


# ---------------------------------------------------------------------------
# analyze_reason
# ---------------------------------------------------------------------------


def test_analyze_reason_normalizes_codes() -> None:
    """analyze_reason maps TISS codes to normalized categories via the DMN seam."""
    inp = GlosaInput(
        tenant_id="amh",
        numero_lote_tiss="LOTE-005",
        reason_codes_tiss=["PROCEDIMENTO_NAO_INDICADO", "GUIA_INCOMPLETA"],
    )
    fake = FakeDmnTransport()
    fake.register(
        "glosa_reason_normalization",
        [{"categoria_normalizada": "tecnica", "descricao": "..."}],
        version=1,
    )
    result = analyze_reason(inp, dmn=fake)
    assert "reason_map" in result
    assert result["reason_map"]["PROCEDIMENTO_NAO_INDICADO"] == "tecnica"
    # FakeDmnTransport returns one canned response per registration — real engine parity
    # (each code evaluated independently) is proven live (T1.5 PR body / evidence ledger).
    assert fake.calls == [
        ("glosa_reason_normalization", {"reason_code_tiss": "PROCEDIMENTO_NAO_INDICADO"}),
        ("glosa_reason_normalization", {"reason_code_tiss": "GUIA_INCOMPLETA"}),
    ]


def test_analyze_reason_unknown_code() -> None:
    """Unknown TISS codes map to 'desconhecida' (catch-all) — live-verified."""
    inp = GlosaInput(
        tenant_id="amh",
        numero_lote_tiss="LOTE-006",
        reason_codes_tiss=["XYZ-999"],
    )
    fake = _glosa_reason_normalization_fake(categoria_normalizada="desconhecida")
    result = analyze_reason(inp, dmn=fake)
    assert result["categoria_normalizada"] == "desconhecida"


def test_analyze_reason_carencia_maps_to_clinica() -> None:
    """golden-parity finding (T1.5, live-verified): the old Python substring-match mapping had
    NO entry for CARENCIA/EXCLUSAO_CONTRATUAL/BENEFICIARIO_INATIVO (fell through to
    'desconhecida'); the deployed DMN maps them to 'clinica'. DMN wins (documented, not
    patched); no adverse output either way (both route conservatively via glosa_triage)."""
    inp = GlosaInput(tenant_id="amh", numero_lote_tiss="LOTE-009", reason_codes_tiss=["CARENCIA"])
    fake = _glosa_reason_normalization_fake(categoria_normalizada="clinica")
    result = analyze_reason(inp, dmn=fake)
    assert result["categoria_normalizada"] == "clinica"


def test_analyze_reason_dmn_unwired_raises_dmn_evaluation_error() -> None:
    inp = GlosaInput(tenant_id="amh", numero_lote_tiss="LOTE-010", reason_codes_tiss=["X"])
    with pytest.raises(DmnEvaluationError):
        analyze_reason(inp, dmn=None)


# ---------------------------------------------------------------------------
# calculate_impact
# ---------------------------------------------------------------------------


def test_calculate_impact_computes_brl() -> None:
    """calculate_impact converts centavos to BRL and computes ratio."""
    from maezo.tools.workers.contas import GlosaIdentified

    identified = GlosaIdentified(
        has_glosas=True,
        denial_ratio=0.2,
        divergencia_valor=True,
        glosa_count=2,
        total_glosado_candidato_centavos=20000,
    )
    result = calculate_impact(identified, 1000.0)
    assert result["total_glosado_candidato_brl"] == 200.0
    assert result["impacto_percentual"] == 20.0


# ---------------------------------------------------------------------------
# prepare_triage_dossier
# ---------------------------------------------------------------------------


def test_prepare_triage_dossier_routes_to_human() -> None:
    """Glosa tecnica/clinica ALWAYS routes to ANALISE_HUMANA — never auto-accepted, regardless
    of the other facts (live-verified: default conservador R4, no sign-off-of-compliance path)."""
    inp = GlosaInput(tenant_id="amh", numero_lote_tiss="LOTE-007")
    from maezo.tools.workers.contas import GlosaIdentified

    identified = GlosaIdentified(has_glosas=True, denial_ratio=0.5, divergencia_valor=True, glosa_count=1)
    reason = {"categoria_normalizada": "tecnica"}
    fake = _glosa_triage_fake(roteamento="ANALISE_HUMANA")

    result = prepare_triage_dossier(inp, identified, reason, dmn=fake)
    assert result.roteamento == "ANALISE_HUMANA"


def test_prepare_triage_dossier_no_glosas() -> None:
    """No glosas + item conforme + documentado + non-tecnica/clinica category -> SEM_GLOSA."""
    inp = GlosaInput(
        tenant_id="amh",
        numero_lote_tiss="LOTE-008",
        item_conforme_tabela=True,
        documentacao_anexa=True,
    )
    from maezo.tools.workers.contas import GlosaIdentified

    identified = GlosaIdentified(has_glosas=False, divergencia_valor=False)
    reason = {"categoria_normalizada": "administrativa"}
    fake = _glosa_triage_fake(roteamento="SEM_GLOSA")

    result = prepare_triage_dossier(inp, identified, reason, dmn=fake)
    assert result.roteamento == "SEM_GLOSA"


def test_prepare_triage_dossier_no_glosas_but_missing_attachments_routes_to_human() -> None:
    """golden-parity finding (T1.5, MAJOR — live-verified): the old Python said "no glosas ->
    SEM_GLOSA" using ONLY has_glosas/divergencia_valor. The deployed glosa_triage table ALSO
    requires item_conforme_tabela=true AND documentacao_anexa=true for its SEM_GLOSA row — the
    `GlosaInput` defaults (both False) that the old test relied on now correctly route to
    ANALISE_HUMANA (pendencia documental) instead. DMN wins (fail-safe: a "no glosa" claim
    without proof of conformidade/documentacao is NOT auto-cleared)."""
    inp = GlosaInput(tenant_id="amh", numero_lote_tiss="LOTE-008b")
    from maezo.tools.workers.contas import GlosaIdentified

    identified = GlosaIdentified(has_glosas=False, divergencia_valor=False)
    reason = {"categoria_normalizada": "administrativa"}
    fake = _glosa_triage_fake(roteamento="ANALISE_HUMANA")

    result = prepare_triage_dossier(inp, identified, reason, dmn=fake)
    assert result.roteamento == "ANALISE_HUMANA"


def test_prepare_triage_dossier_recorrer_reachable() -> None:
    """golden-parity finding (T1.5, MAJOR — live-verified): RECORRER was structurally
    unreachable dead code in the old Python (its own docstring: "For now, always route to
    ANALISE_HUMANA (conservative)"). This cutover activates it: categoria "valor" + item
    conforme + divergencia + documentado -> RECORRER."""
    inp = GlosaInput(
        tenant_id="amh",
        numero_lote_tiss="LOTE-011",
        item_conforme_tabela=True,
        documentacao_anexa=True,
    )
    from maezo.tools.workers.contas import GlosaIdentified

    identified = GlosaIdentified(has_glosas=True, divergencia_valor=True, glosa_count=1, denial_ratio=0.3)
    reason = {"categoria_normalizada": "valor"}
    fake = _glosa_triage_fake(roteamento="RECORRER")

    result = prepare_triage_dossier(inp, identified, reason, dmn=fake, tipo_item="consulta")
    assert result.roteamento == "RECORRER"
    assert fake.calls == [
        (
            "glosa_triage",
            {
                "tipo_item": "consulta",
                "categoria_normalizada": "valor",
                "item_conforme_tabela": True,
                "divergencia_valor": True,
                "documentacao_anexa": True,
            },
        )
    ]


def test_prepare_triage_dossier_dmn_unwired_raises_dmn_evaluation_error() -> None:
    inp = GlosaInput(tenant_id="amh", numero_lote_tiss="LOTE-012")
    from maezo.tools.workers.contas import GlosaIdentified

    identified = GlosaIdentified()
    with pytest.raises(DmnEvaluationError):
        prepare_triage_dossier(inp, identified, {}, dmn=None)


# ---------------------------------------------------------------------------
# register_glosa_accept — GUARD tests
# ---------------------------------------------------------------------------


def test_glosa_accept_guard_missing_decisao() -> None:
    """register_glosa_accept raises if decisao_contas != ACEITAR_GLOSA."""
    inp = GlosaAcceptInput(
        decisao_contas="RECORRER",
        justificativa_glosa="test",
        codigo_glosa_aceito="COD001",
        valor_glosa_aceito_brl=100.0,
        analista_id="analista-1",
    )
    with pytest.raises(GlosaAcceptNotHumanError) as exc:
        register_glosa_accept(inp)
    assert "decisao_contas" in str(exc.value)


def test_glosa_accept_guard_missing_justificativa() -> None:
    """register_glosa_accept raises if justificativa_glosa is empty."""
    inp = GlosaAcceptInput(
        decisao_contas="ACEITAR_GLOSA",
        justificativa_glosa="",
        codigo_glosa_aceito="COD001",
        valor_glosa_aceito_brl=100.0,
        analista_id="analista-1",
    )
    with pytest.raises(GlosaAcceptNotHumanError) as exc:
        register_glosa_accept(inp)
    assert "justificativa_glosa" in str(exc.value)


def test_glosa_accept_guard_missing_analista() -> None:
    """register_glosa_accept raises if analista_id is empty."""
    inp = GlosaAcceptInput(
        decisao_contas="ACEITAR_GLOSA",
        justificativa_glosa="Motivo valido",
        codigo_glosa_aceito="COD001",
        valor_glosa_aceito_brl=100.0,
        analista_id="",
    )
    with pytest.raises(GlosaAcceptNotHumanError) as exc:
        register_glosa_accept(inp)
    assert "analista_id" in str(exc.value)


def test_glosa_accept_success() -> None:
    """register_glosa_accept succeeds with all required fields."""
    inp = GlosaAcceptInput(
        decisao_contas="ACEITAR_GLOSA",
        justificativa_glosa="Glosa justificada conforme contrato",
        codigo_glosa_aceito="COD001",
        valor_glosa_aceito_brl=150.0,
        analista_id="analista-123",
    )
    result = register_glosa_accept(inp)
    assert result.registered is True
    assert result.glosa_id.startswith("GLOSA-")


# ---------------------------------------------------------------------------
# M-9 — glosa_id is DETERMINISTIC (was sha256(time.time_ns()))
#
# `glosa_id` is not an inert output: it anchors SP-OP-RECURSO-001's business key
# `RECURSO-{tenant}-{numero_guia_tiss}-{glosa_id}` (`contas._recurso_business_key`,
# `platform/notification_bridge._recurso_business_key`, `agents/marina/graph._business_key`).
# A wall-clock mint meant every engine RE-DELIVERY of the same human acceptance produced a new
# id -> a new recurso business key -> a duplicate RECURSO instance for one glosa.
# ---------------------------------------------------------------------------


def _accept(**overrides: Any) -> GlosaAcceptInput:
    base: dict[str, Any] = {
        "decisao_contas": "ACEITAR_GLOSA",
        "justificativa_glosa": "item fora da tabela pactuada",
        "codigo_glosa_aceito": "1401",
        "valor_glosa_aceito_brl": 150.0,
        "analista_id": "analista-123",
        "tenant_id": "amh",
        "numero_lote_tiss": "LOTE-9",
        "numero_guia_tiss": "GUIA-7",
    }
    base.update(overrides)
    return GlosaAcceptInput(**base)


def test_glosa_id_is_stable_across_redelivery_of_the_same_acceptance() -> None:
    """THE M-9 defect. The engine re-delivers an external task on lock-expiry / retry with the
    SAME variables; the id must therefore be the same on every delivery."""
    first = register_glosa_accept(_accept()).glosa_id
    second = register_glosa_accept(_accept()).glosa_id

    assert first == second
    assert first.startswith("GLOSA-analista-123-")


def test_the_recurso_business_key_no_longer_drifts_across_redelivery() -> None:
    """The consequence that made this a duplicate-instance defect rather than a cosmetic one."""
    keys = {
        _recurso_business_key("amh", "GUIA-7", register_glosa_accept(_accept()).glosa_id) for _ in range(3)
    }
    assert len(keys) == 1


@pytest.mark.parametrize(
    "override",
    [
        {"codigo_glosa_aceito": "1402"},
        {"valor_glosa_aceito_brl": 151.0},
        {"analista_id": "analista-999"},
        {"tenant_id": "outra"},
        {"numero_lote_tiss": "LOTE-10"},
        {"numero_guia_tiss": "GUIA-8"},
    ],
)
def test_a_genuinely_different_acceptance_gets_a_different_id(override: dict[str, Any]) -> None:
    """Determinism must not collapse DISTINCT acceptances onto one id — that would silently merge
    two providers' glosas onto one recurso anchor, the opposite harm."""
    assert register_glosa_accept(_accept()).glosa_id != register_glosa_accept(_accept(**override)).glosa_id


def test_the_analista_stays_attributable_in_the_clear() -> None:
    """ADR-0007: the acceptance is recorded in the analyst's name. Unchanged by M-9."""
    assert register_glosa_accept(_accept(analista_id="ana-7")).glosa_id.startswith("GLOSA-ana-7-")


def test_numeric_amount_digests_identically_whether_delivered_as_int_or_float() -> None:
    """An engine round-trip can present `150` where it earlier presented `150.0` (Long/Double
    decoding). Naive `str()` would then digest to two ids for ONE decision — re-introducing the
    very drift this fix removes."""
    assert (
        register_glosa_accept(_accept(valor_glosa_aceito_brl=150)).glosa_id
        == register_glosa_accept(_accept(valor_glosa_aceito_brl=150.0)).glosa_id
    )


def test_blank_instance_anchors_degrade_scope_but_never_determinism() -> None:
    """The anchors are context, NOT new guard fields: a case that lacks them still registers (the
    L0 guard set is unchanged) and still mints a STABLE id."""
    bare = _accept(tenant_id="", numero_lote_tiss="", numero_guia_tiss="")
    result = register_glosa_accept(bare)

    assert result.registered is True
    assert result.glosa_id == register_glosa_accept(bare).glosa_id


def test_the_new_anchor_fields_are_not_part_of_the_l0_guard() -> None:
    """Widening an L0 refusal set is a human decision, not a side effect of an idempotency fix."""
    result = register_glosa_accept(_accept(tenant_id="", numero_guia_tiss=""))
    assert result.registered is True


def test_entry_selects_the_anchors_from_the_process_variables() -> None:
    """`pick_fields` is the whole wiring: the anchors arrive as ordinary process variables, so the
    entry function and the typed function agree."""
    variables = {
        "decisao_contas": "ACEITAR_GLOSA",
        "justificativa_glosa": "erro de tabela",
        "codigo_glosa_aceito": "COD-1",
        "valor_glosa_aceito_brl": 100.0,
        "analista_id": "analista-1",
        "tenant_id": "amh",
        "numero_lote_tiss": "LOTE-1",
        "numero_guia_tiss": "GUIA-1",
        "beneficiario_pseudo_id": "IGNORED-not-a-field",
    }
    from_entry = register_glosa_accept_entry(variables)
    typed = register_glosa_accept(
        GlosaAcceptInput(**{k: v for k, v in variables.items() if k != "beneficiario_pseudo_id"})
    )

    assert from_entry["glosa_id"] == typed.glosa_id
    # The anchors genuinely participate: dropping them changes the id.
    assert (
        from_entry["glosa_id"]
        != register_glosa_accept_entry({k: v for k, v in variables.items() if k != "numero_guia_tiss"})[
            "glosa_id"
        ]
    )


# ---------------------------------------------------------------------------
# notify_sla_risk
# ---------------------------------------------------------------------------


def test_notify_sla_risk() -> None:
    """notify_sla_risk alerts the coordination group."""
    result = notify_sla_risk("amh", "LOTE-009", sla_remaining="P2D")
    assert result["notified"] is True
    assert result["grupo"] == "coordenacao-contas"


# ---------------------------------------------------------------------------
# start_recurso
# ---------------------------------------------------------------------------


def _recurso_vars(**over: object) -> dict[str, object]:
    base: dict[str, object] = {
        "tenant_id": "amh",
        "glosa_id": "GLOSA-001",
        "numero_guia_tiss": "GUIDE-001",
        "glosa_type": "administrativa",
        "documentacao_anexa": True,
        "numero_lote_tiss": "LOTE-9",
    }
    base.update(over)
    return base


def test_start_recurso_starts_recurso_with_exact_business_key_and_payload() -> None:
    """start_recurso now REALLY starts SP-OP-RECURSO-001 through the fenced chokepoint (was a
    stub). Asserts the exact business key, the started instance, the carried payload, and the
    exactly-once ADR-0007 start record (NOT just no-exception)."""
    engine = FakeCibSevenTransport()
    sink = FakeStartAuditSink()
    result = start_recurso(_recurso_vars(secreta_phi="SHOULD-NOT-LEAK"), engine=engine, audit_sink=sink)
    assert result["handoff"] == RECURSO_PROCESS_KEY
    assert result["handoff_executado"] is True
    assert result["glosa_existe"] is True
    assert result["recurso_business_key"] == "RECURSO-amh-GUIDE-001-GLOSA-001"
    assert result["recurso_already_existed"] is False

    started = asyncio.run(engine.find_active_instance("RECURSO-amh-GUIDE-001-GLOSA-001"))
    assert started is not None and started.process_key == RECURSO_PROCESS_KEY
    payload = asyncio.run(engine.get_process_status("RECURSO-amh-GUIDE-001-GLOSA-001")).variables
    assert payload["glosa_id"] == "GLOSA-001"
    assert payload["numero_guia_tiss"] == "GUIDE-001"
    assert payload["glosa_existe"] is True
    assert "secreta_phi" not in payload  # explicit allowlist, never a passthrough

    assert sink.dedup_keys == [start_dedup_key("amh", RECURSO_PROCESS_KEY, "RECURSO-amh-GUIDE-001-GLOSA-001")]
    [record] = sink.records
    assert record.agent_id == AUDIT_AGENT_ID
    assert record.tenant_id == "amh"
    assert record.action == f"start_process:{RECURSO_PROCESS_KEY}"
    assert record.model_id is None and record.prompt_version is None
    assert record.details["decisao_contas"] == "RECORRER"
    assert "input_sha256" in record.details
    assert "glosa_id" not in record.details  # identifiers hash-bound only, never in the clear


def test_start_recurso_idempotent_returns_existing_active_instance() -> None:
    """A RECURSO-001 already active for this glosa is returned unchanged — never a second start."""
    engine = FakeCibSevenTransport()
    sink = FakeStartAuditSink()
    engine.seed_instance(
        ProcessInstance(
            instance_id="pre-existing-recurso",
            process_key=RECURSO_PROCESS_KEY,
            business_key="RECURSO-amh-GUIDE-001-GLOSA-001",
            state="ACTIVE",
            already_existed=True,
        )
    )
    result = start_recurso(_recurso_vars(), engine=engine, audit_sink=sink)
    assert result["recurso_already_existed"] is True
    assert result["recurso_instance_id"] == "pre-existing-recurso"


def test_start_recurso_fail_closed_when_engine_seam_not_wired() -> None:
    with pytest.raises(RuntimeError, match="engine seam"):
        start_recurso(_recurso_vars(), audit_sink=FakeStartAuditSink())


def test_start_recurso_fail_closed_when_audit_sink_not_wired() -> None:
    with pytest.raises(RuntimeError, match="audit sink"):
        start_recurso(_recurso_vars(), engine=FakeCibSevenTransport())


def test_start_recurso_refuses_without_glosa_identity() -> None:
    """No glosa_id/numero_guia_tiss -> deterministic refusal, never a start under an empty key."""
    engine = FakeCibSevenTransport()
    sink = FakeStartAuditSink()
    with pytest.raises(ContasRecursoSemGlosaError):
        start_recurso(_recurso_vars(glosa_id="", numero_guia_tiss=""), engine=engine, audit_sink=sink)
    assert sink.dedup_keys == []  # emit-before-effect: no audit, no start


@pytest.mark.parametrize(
    "over",
    [
        {"glosa_id": "   "},  # whitespace-only anchor
        {"numero_guia_tiss": "\t "},  # whitespace-only anchor
        {"glosa_id": None},  # explicit None (str(None)=="None" is truthy — must NOT slip through)
        {"numero_guia_tiss": None},
        {"tenant_id": ""},  # blank tenant -> RECURSO--GUIA-…-GLOSA-… degenerate key forbidden
        {"tenant_id": "   "},
        {"tenant_id": None},
    ],
    ids=[
        "glosa-ws",
        "guia-ws",
        "glosa-none",
        "guia-none",
        "tenant-empty",
        "tenant-ws",
        "tenant-none",
    ],
)
def test_start_recurso_refuses_degenerate_anchors_before_any_engine_call(
    over: dict[str, object],
) -> None:
    """EB-4 R1 finding: whitespace-only / explicit-None anchors and a blank/None tenant_id all
    refuse (`_non_blank` semantics, shared with the bridge) BEFORE any engine call — no start
    attempted, no audit emitted, never a degenerate business key like `RECURSO--G-None`."""
    engine = FakeCibSevenTransport()
    sink = FakeStartAuditSink()
    with pytest.raises(ContasRecursoSemGlosaError):
        start_recurso(_recurso_vars(**over), engine=engine, audit_sink=sink)
    assert sink.dedup_keys == []  # emit-before-effect: refusal precedes ANY audit/engine call
    assert sink.records == []


# ---------------------------------------------------------------------------
# start_fraude (Phase-3 CONTAS→FRAUDE leg)
# ---------------------------------------------------------------------------


def _fraude_vars(**over: object) -> dict[str, object]:
    base: dict[str, object] = {
        "tenant_id": "amh",
        "prestador_id": "prov:teste-0001",
        "numero_lote_tiss": "LOTE-9",
        "analista_id": "analista-001",
        "evidencia_refs": ["ref:1"],
        "indicadores_presentes": ["ind_a"],
    }
    base.update(over)
    return base


def test_start_fraude_starts_fraude_with_converged_business_key_and_payload() -> None:
    """start_fraude REALLY starts SP-OP-FRAUDE-001 through the fenced chokepoint. Asserts the
    exact business key (CONVERGENT with the notification_bridge CONTAS→FRAUDE rule:
    FRAUDE-{tenant}-{prestador_id} when no numero_caso), the started instance, the carried
    payload, and the exactly-once ADR-0007 start record."""
    engine = FakeCibSevenTransport()
    sink = FakeStartAuditSink()
    result = start_fraude(_fraude_vars(secreta_phi="SHOULD-NOT-LEAK"), engine=engine, audit_sink=sink)
    assert result["handoff"] == FRAUDE_PROCESS_KEY
    assert result["handoff_executado"] is True
    assert result["fraude_business_key"] == "FRAUDE-amh-prov:teste-0001"
    assert result["fraude_already_existed"] is False

    started = asyncio.run(engine.find_active_instance("FRAUDE-amh-prov:teste-0001"))
    assert started is not None and started.process_key == FRAUDE_PROCESS_KEY
    payload = asyncio.run(engine.get_process_status("FRAUDE-amh-prov:teste-0001")).variables
    assert payload["prestador_id"] == "prov:teste-0001"
    assert payload["origem_encaminhamento"] == "contas"
    assert payload["numero_caso"] == "prov:teste-0001"
    assert payload["entidade_tipo"] == "prestador"
    assert "secreta_phi" not in payload  # explicit allowlist, never a passthrough

    assert sink.dedup_keys == [start_dedup_key("amh", FRAUDE_PROCESS_KEY, "FRAUDE-amh-prov:teste-0001")]
    [record] = sink.records
    assert record.agent_id == AUDIT_AGENT_ID
    assert record.tenant_id == "amh"
    assert record.action == f"start_process:{FRAUDE_PROCESS_KEY}"
    assert record.model_id is None and record.prompt_version is None
    assert record.details["decisao_contas"] == "ENCAMINHAR_FRAUDE"
    assert "input_sha256" in record.details
    assert "prestador_id" not in record.details  # identifiers hash-bound only, never in the clear


def test_start_fraude_prefers_assigned_numero_caso_when_present() -> None:
    """When the CONTAS process already carries an assigned numero_caso, the business key uses it
    (still convergent with the bridge's own numero_caso-first derivation)."""
    engine = FakeCibSevenTransport()
    sink = FakeStartAuditSink()
    result = start_fraude(_fraude_vars(numero_caso="CASO-777"), engine=engine, audit_sink=sink)
    assert result["fraude_business_key"] == "FRAUDE-amh-CASO-777"
    assert result["numero_caso"] == "CASO-777"


def test_start_fraude_idempotent_returns_existing_active_instance() -> None:
    """A FRAUDE-001 already active for this case is returned unchanged — never a second start
    (the L0 convergence guarantee: in-flow start + bridge re-delivery never double-start)."""
    engine = FakeCibSevenTransport()
    sink = FakeStartAuditSink()
    engine.seed_instance(
        ProcessInstance(
            instance_id="pre-existing-fraude",
            process_key=FRAUDE_PROCESS_KEY,
            business_key="FRAUDE-amh-prov:teste-0001",
            state="ACTIVE",
            already_existed=True,
        )
    )
    result = start_fraude(_fraude_vars(), engine=engine, audit_sink=sink)
    assert result["fraude_already_existed"] is True
    assert result["fraude_instance_id"] == "pre-existing-fraude"


def test_start_fraude_fail_closed_when_engine_seam_not_wired() -> None:
    with pytest.raises(RuntimeError, match="engine seam"):
        start_fraude(_fraude_vars(), audit_sink=FakeStartAuditSink())


def test_start_fraude_fail_closed_when_audit_sink_not_wired() -> None:
    with pytest.raises(RuntimeError, match="audit sink"):
        start_fraude(_fraude_vars(), engine=FakeCibSevenTransport())


@pytest.mark.parametrize(
    "over",
    [
        {"prestador_id": ""},
        {"prestador_id": "   "},
        {"prestador_id": None},
        {"tenant_id": ""},
        {"tenant_id": "   "},
        {"tenant_id": None},
    ],
    ids=["prest-empty", "prest-ws", "prest-none", "tenant-empty", "tenant-ws", "tenant-none"],
)
def test_start_fraude_refuses_degenerate_anchors_before_any_engine_call(
    over: dict[str, object],
) -> None:
    """Whitespace-only / explicit-None anchors and a blank/None tenant_id all refuse
    (`non_blank` semantics, shared with the bridge) BEFORE any engine call — no start attempted,
    no audit emitted, never a degenerate business key like `FRAUDE-amh-None`."""
    engine = FakeCibSevenTransport()
    sink = FakeStartAuditSink()
    with pytest.raises(ContasFraudeSemAlvoError):
        start_fraude(_fraude_vars(**over), engine=engine, audit_sink=sink)
    assert sink.dedup_keys == []  # emit-before-effect: refusal precedes ANY audit/engine call
    assert sink.records == []


# ---------------------------------------------------------------------------
# reconcile_payment
# ---------------------------------------------------------------------------


def test_reconcile_payment() -> None:
    """reconcile_payment reconciles a reenvio without adverse effect."""
    result = reconcile_payment("LOTE-010", "GUIDE-002")
    assert result["reconciled"] is True
    assert result["status"] == "REENVIAR"


# ---------------------------------------------------------------------------
# publish
# ---------------------------------------------------------------------------


def test_publish_contas() -> None:
    """publish emits a domain event."""
    result = publish("contas.completed", {"desfecho": "sem_glosa"})
    assert result["published"] is True
    assert "contas.completed" in result["topic"]


# ---------------------------------------------------------------------------
# Error types
# ---------------------------------------------------------------------------


def test_glosa_accept_not_human_is_permission_error() -> None:
    """GlosaAcceptNotHumanError must be a subclass of PermissionError."""
    assert issubclass(GlosaAcceptNotHumanError, PermissionError)


def test_contas_lote_invalido_is_value_error() -> None:
    """ContasLoteInvalidoError must be a subclass of ValueError."""
    assert issubclass(ContasLoteInvalidoError, ValueError)


# ---------------------------------------------------------------------------
# Dict-boundary entry functions (T1.2/ADR-0026 §2b) — round-trip vs calling the
# typed function directly; fail-closed marshalling on invalid/missing input.
# ---------------------------------------------------------------------------


def test_identify_glosa_entry_round_trips_identify_glosa() -> None:
    variables = {
        "tenant_id": "amh",
        "numero_lote_tiss": "LOTE-1",
        "linhas_conta_refs": [{"valor_apresentado_brl": 100.0, "valor_glosado_brl": 10.0}],
    }
    direct = identify_glosa(GlosaInput(**variables))
    result = identify_glosa_entry(variables)
    assert result["has_glosas"] == direct.has_glosas
    assert result["glosa_count"] == direct.glosa_count


def test_identify_glosa_entry_raises_on_missing_required_fields() -> None:
    """GlosaInput.tenant_id/numero_lote_tiss have NO dataclass default — fail-closed (ADR-0026
    §2b): a missing required field raises this module's own ContasLoteInvalidoError, never a
    bare TypeError."""
    with pytest.raises(ContasLoteInvalidoError):
        identify_glosa_entry({})


def test_analyze_reason_entry_round_trips_analyze_reason() -> None:
    variables = {"tenant_id": "amh", "numero_lote_tiss": "LOTE-1", "reason_codes_tiss": ["TECNICA"]}
    direct = analyze_reason(
        GlosaInput(**variables), dmn=_glosa_reason_normalization_fake(categoria_normalizada="tecnica")
    )
    result = analyze_reason_entry(
        variables, dmn=_glosa_reason_normalization_fake(categoria_normalizada="tecnica")
    )
    assert result == direct


def test_analyze_reason_entry_raises_on_missing_required_fields() -> None:
    with pytest.raises(ContasLoteInvalidoError):
        analyze_reason_entry({"reason_codes_tiss": ["TECNICA"]})


def test_register_glosa_accept_entry_guards_missing_human_decision() -> None:
    """register_glosa_accept_entry raises the UNCHANGED GlosaAcceptNotHumanError guard."""
    with pytest.raises(GlosaAcceptNotHumanError):
        register_glosa_accept_entry({"decisao_contas": ""})


def test_register_glosa_accept_entry_happy_path() -> None:
    variables = {
        "decisao_contas": "ACEITAR_GLOSA",
        "justificativa_glosa": "erro de tabela",
        "codigo_glosa_aceito": "COD-1",
        "valor_glosa_aceito_brl": 100.0,
        "analista_id": "analista-1",
    }
    direct = register_glosa_accept(GlosaAcceptInput(**variables))
    result = register_glosa_accept_entry(variables)
    assert result["registered"] == direct.registered


def test_publish_entry_round_trips_publish() -> None:
    variables = {"event_type": "contas.completed", "payload": {"desfecho": "sem_glosa"}}
    assert publish_entry(variables) == publish("contas.completed", {"desfecho": "sem_glosa"}, "")
