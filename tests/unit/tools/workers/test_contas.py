"""Unit tests for maezo.tools.workers.contas — SP-OP-CONTAS-001.

TDD London School: tests exercise the external task contracts.
"""

import pytest

from maezo.tools.workers.contas import (
    ContasLoteInvalidoError,
    GlosaAcceptInput,
    GlosaAcceptNotHumanError,
    GlosaInput,
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
    start_recurso,
)
from maezo.tools.workers.dmn_transport import DmnEvaluationError, FakeDmnTransport


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


def test_start_recurso_handoff() -> None:
    """start_recurso creates handoff payload to SP-OP-RECURSO-001."""
    result = start_recurso(
        glosa_id="GLOSA-001",
        numero_guia_tiss="GUIDE-001",
        glosa_type="administrativa",
        documentacao_anexa=True,
    )
    assert result["handoff"] == "SP-OP-RECURSO-001"
    assert result["glosa_existe"] is True
    assert result["glosa_id"] == "GLOSA-001"


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
