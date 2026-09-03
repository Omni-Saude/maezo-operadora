"""Unit tests for maezo.tools.workers.contas — SP-OP-CONTAS-001.

TDD London School: tests exercise the external task contracts.
"""

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from maezo.agents.andre.keys import key_segment
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport, ProcessInstance, start_dedup_key
from maezo.tools.workers.contas import (
    COMUNICACAO_TIPOS,
    FRAUDE_PROCESS_KEY,
    HANDOFF_PAGTO_FORBIDDEN_KEYS,
    HANDOFF_PAGTO_SEEDED_KEYS,
    LASTRO_ORIGEM_AUTOMATICA,
    LASTRO_ORIGEM_HUMANA,
    LASTROS_ORIGEM_PERMITIDOS,
    PAGTO_PROCESS_KEY,
    ContasComunicacaoInvalidaError,
    ContasDevolucaoInvalidaError,
    ContasFraudeSemAlvoError,
    ContasGlosaNotHumanError,
    ContasHandoffPagamentoInvalidoError,
    ContasLoteInvalidoError,
    DemonstrativoInput,
    GlosaInput,
    GlosaRegistroInput,
    _ordem_pagamento_id,
    _pagto_business_key,
    analyze_reason,
    analyze_reason_entry,
    calculate_impact,
    devolver_conta,
    emitir_demonstrativo,
    emitir_demonstrativo_entry,
    handoff_pagamento,
    identify_glosa,
    identify_glosa_entry,
    notify_sla_risk,
    prepare_triage_dossier,
    publish,
    publish_entry,
    register_contas_workers,
    registrar_glosa,
    registrar_glosa_entry,
    start_fraude,
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
    """No glosas + item conforme + documentado + non-tecnica/clinica category -> PAGAR."""
    inp = GlosaInput(
        tenant_id="amh",
        numero_lote_tiss="LOTE-008",
        item_conforme_tabela=True,
        documentacao_anexa=True,
    )
    from maezo.tools.workers.contas import GlosaIdentified

    identified = GlosaIdentified(has_glosas=False, divergencia_valor=False)
    reason = {"categoria_normalizada": "administrativa"}
    fake = _glosa_triage_fake(roteamento="PAGAR")

    result = prepare_triage_dossier(inp, identified, reason, dmn=fake)
    assert result.roteamento == "PAGAR"


def test_prepare_triage_dossier_no_glosas_but_missing_attachments_routes_to_human() -> None:
    """golden-parity finding (T1.5, MAJOR — live-verified): the old Python said "no glosas ->
    permissive row" using ONLY has_glosas/divergencia_valor. The deployed glosa_triage table ALSO
    requires item_conforme_tabela=true AND documentacao_anexa=true for its `PAGAR` row — the
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


def test_prepare_triage_dossier_divergencia_de_valor_roteia_a_humano() -> None:
    """The value-divergence row is reachable (T1.5 cutover) AND routes to a human (ADR-0040).

    SUBSTITUI `test_prepare_triage_dossier_recorrer_reachable`. The T1.5 finding stands — the old
    Python never reached this row at all — but under the payer perspective its output is
    `ANALISE_HUMANA`, not a value naming the appellant's act: a value divergence is a glosa
    CANDIDATE, and only `UT_AnalistaContas` may turn a candidate into a glosa."""
    inp = GlosaInput(
        tenant_id="amh",
        numero_lote_tiss="LOTE-011",
        item_conforme_tabela=True,
        documentacao_anexa=True,
    )
    from maezo.tools.workers.contas import GlosaIdentified

    identified = GlosaIdentified(has_glosas=True, divergencia_valor=True, glosa_count=1, denial_ratio=0.3)
    reason = {"categoria_normalizada": "valor"}
    fake = _glosa_triage_fake(roteamento="ANALISE_HUMANA")

    result = prepare_triage_dossier(inp, identified, reason, dmn=fake, tipo_item="consulta")
    assert result.roteamento == "ANALISE_HUMANA"
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
# registrar_glosa — GUARD tests (ERR_CONTAS_GLOSA_NOT_HUMAN)
#
# The guard's OBJECT changed with ADR-0040: what a payer does adversely is APPLY a glosa, not
# accept one. Same PermissionError base, same audited-incident path, same `_NOT_HUMAN` suffix the
# harness recognises — two guarded outcomes now (`GLOSAR`, `PAGAR_PARCIAL`), the precedent being
# `reembolso.registrar_decisao`.
# ---------------------------------------------------------------------------


def test_registrar_glosa_guard_missing_decisao() -> None:
    """registrar_glosa raises if decisao_contas is not one of the two guarded human outcomes."""
    inp = GlosaRegistroInput(
        decisao_contas="DEVOLVER",
        justificativa_glosa="test",
        codigo_glosa_tiss="COD001",
        valor_glosado_brl=100.0,
        analista_id="analista-1",
    )
    with pytest.raises(ContasGlosaNotHumanError) as exc:
        registrar_glosa(inp)
    assert "decisao_contas" in str(exc.value)


def test_registrar_glosa_guard_recusa_decisao_pagar() -> None:
    """PAGAR is a FAVOURABLE outcome — it must never reach the adverse worker at all.

    The old guard read `!= ACEITAR_GLOSA`, so any non-matching value refused by accident. The new
    one refuses `PAGAR` on purpose: routing a favourable decision through the glosa worker would
    mint a `glosa_id` for a conta nobody glosou."""
    inp = GlosaRegistroInput(
        decisao_contas="PAGAR",
        justificativa_glosa="conta conforme",
        codigo_glosa_tiss="COD001",
        valor_glosado_brl=100.0,
        analista_id="analista-1",
    )
    with pytest.raises(ContasGlosaNotHumanError):
        registrar_glosa(inp)


def test_registrar_glosa_guard_missing_justificativa() -> None:
    """registrar_glosa raises if justificativa_glosa is empty."""
    inp = GlosaRegistroInput(
        decisao_contas="GLOSAR",
        justificativa_glosa="",
        codigo_glosa_tiss="COD001",
        valor_glosado_brl=100.0,
        analista_id="analista-1",
    )
    with pytest.raises(ContasGlosaNotHumanError) as exc:
        registrar_glosa(inp)
    assert "justificativa_glosa" in str(exc.value)


def test_registrar_glosa_guard_recusa_campos_so_com_espacos() -> None:
    """A form that submits `"   "` must refuse exactly like one that submits `""` (the two-channel
    guard idiom `recurso.registrar_indeferimento` uses: `.strip()` before every emptiness test)."""
    inp = GlosaRegistroInput(
        decisao_contas="GLOSAR",
        justificativa_glosa="   ",
        codigo_glosa_tiss="\t",
        valor_glosado_brl=100.0,
        analista_id="  ",
    )
    with pytest.raises(ContasGlosaNotHumanError) as exc:
        registrar_glosa(inp)
    for field in ("justificativa_glosa", "codigo_glosa_tiss", "analista_id"):
        assert field in str(exc.value)


def test_registrar_glosa_guard_missing_analista() -> None:
    """registrar_glosa raises if analista_id is empty."""
    inp = GlosaRegistroInput(
        decisao_contas="GLOSAR",
        justificativa_glosa="Motivo valido",
        codigo_glosa_tiss="COD001",
        valor_glosado_brl=100.0,
        analista_id="",
    )
    with pytest.raises(ContasGlosaNotHumanError) as exc:
        registrar_glosa(inp)
    assert "analista_id" in str(exc.value)


def test_registrar_glosa_success() -> None:
    """registrar_glosa succeeds with all required fields on the GLOSAR outcome."""
    inp = GlosaRegistroInput(
        decisao_contas="GLOSAR",
        justificativa_glosa="Item fora da tabela contratada",
        codigo_glosa_tiss="COD001",
        valor_glosado_brl=150.0,
        analista_id="analista-123",
    )
    result = registrar_glosa(inp)
    assert result.registered is True
    assert result.decisao_contas == "GLOSAR"
    assert result.valor_glosado_brl == 150.0
    assert result.glosa_id.startswith("GLOSA-")


def test_registrar_glosa_aceita_pagar_parcial() -> None:
    """PAGAR_PARCIAL is the SECOND guarded adverse outcome (a reduction), not a separate worker."""
    inp = GlosaRegistroInput(
        decisao_contas="PAGAR_PARCIAL",
        justificativa_glosa="Duas linhas fora da tabela contratada",
        codigo_glosa_tiss="COD002",
        valor_glosado_brl=60.0,
        valor_liberado_brl=90.0,
        analista_id="analista-123",
    )
    result = registrar_glosa(inp)
    assert result.registered is True
    assert result.decisao_contas == "PAGAR_PARCIAL"
    assert (result.valor_glosado_brl, result.valor_liberado_brl) == (60.0, 90.0)


def test_registrar_glosa_pagar_parcial_exige_valor_liberado() -> None:
    """A reduction must state what it DOES release, not only what it withholds — otherwise the
    demonstrativo cannot close `apresentado = liberado + glosa` and the handoff has no amount."""
    inp = GlosaRegistroInput(
        decisao_contas="PAGAR_PARCIAL",
        justificativa_glosa="Duas linhas fora da tabela contratada",
        codigo_glosa_tiss="COD002",
        valor_glosado_brl=60.0,
        analista_id="analista-123",
    )
    with pytest.raises(ContasGlosaNotHumanError) as exc:
        registrar_glosa(inp)
    assert "valor_liberado_brl" in str(exc.value)


@pytest.mark.parametrize(
    "valor_liberado",
    [0.0, "0", "0,00", -1.0],
    ids=["zero", "zero_str", "zero_ptbr", "negativo"],
)
def test_registrar_glosa_pagar_parcial_recusa_valor_liberado_nao_positivo(valor_liberado: Any) -> None:
    """`> 0`, not `>= 0`: um "parcial" que libera R$ 0,00 e, materialmente, um GLOSAR integral.

    Aceita-lo registrava o efeito adverso, emitia ao prestador um demonstrativo de "pagamento
    parcial" que nao paga nada, e depois TRAVAVA a instancia em `ST_HandoffPagamentoParcial`, que
    recusa `valor <= 0` — o ato adverso materializado com o desfecho inalcancavel. As duas metades
    do mesmo ato passam a concordar: quem quer glosar tudo usa `decisao_contas=GLOSAR`.
    """
    with pytest.raises(ContasGlosaNotHumanError) as exc:
        registrar_glosa(
            GlosaRegistroInput(
                decisao_contas="PAGAR_PARCIAL",
                justificativa_glosa="Todas as linhas glosadas nesta conta",
                codigo_glosa_tiss="COD002",
                valor_glosado_brl=60.0,
                valor_liberado_brl=valor_liberado,
                analista_id="analista-123",
            )
        )
    assert "valor_liberado_brl" in str(exc.value)


def test_registrar_glosa_pagar_parcial_aceita_valor_liberado_positivo() -> None:
    """O controle NEGATIVO do teste acima: com uma parcela realmente liberada, o parcial passa —
    sem ele a recusa acima valeria por construcao."""
    ok = registrar_glosa(
        GlosaRegistroInput(
            decisao_contas="PAGAR_PARCIAL",
            justificativa_glosa="Linhas 3 e 4 glosadas; o restante e devido",
            codigo_glosa_tiss="COD002",
            valor_glosado_brl=60.0,
            valor_liberado_brl=40.0,
            analista_id="analista-123",
        )
    )
    assert ok.registered is True


@pytest.mark.parametrize("valor", ["", "   ", None, "abc", 0, -1.0], ids=list("abcdef"))
def test_registrar_glosa_recusa_valor_glosado_ausente_ou_nao_numerico(valor: Any) -> None:
    """`_parse_valor_monetario` NEVER defaults to 0: the engine can deliver a monetary field as a
    Camunda String, and a raw `<= 0` on a `str` would raise TypeError instead of refusing."""
    with pytest.raises(ContasGlosaNotHumanError) as exc:
        registrar_glosa(
            GlosaRegistroInput(
                decisao_contas="GLOSAR",
                justificativa_glosa="Item fora da tabela",
                codigo_glosa_tiss="COD001",
                valor_glosado_brl=valor,
                analista_id="analista-1",
            )
        )
    assert "valor_glosado_brl" in str(exc.value)


def test_registrar_glosa_aceita_valor_como_string_do_engine() -> None:
    """The engine seeds monetary fields as Camunda Strings; `"150.00"` is a valid amount."""
    assert (
        registrar_glosa(
            GlosaRegistroInput(
                decisao_contas="GLOSAR",
                justificativa_glosa="Item fora da tabela",
                codigo_glosa_tiss="COD001",
                valor_glosado_brl="150.00",
                analista_id="analista-1",
            )
        ).valor_glosado_brl
        == 150.0
    )


# ---------------------------------------------------------------------------
# M-9 — glosa_id is DETERMINISTIC (was sha256(time.time_ns()))
#
# `glosa_id` is not an inert output: it is published in `agents.events.contas.completed`, printed
# on the demonstrativo, and is the identity the prestador cites when it files a recurso. A
# wall-clock mint meant every engine RE-DELIVERY of the same human adjudication produced a new id.
# ---------------------------------------------------------------------------


def _glosar(**overrides: Any) -> GlosaRegistroInput:
    base: dict[str, Any] = {
        "decisao_contas": "GLOSAR",
        "justificativa_glosa": "item fora da tabela pactuada",
        "codigo_glosa_tiss": "1401",
        "valor_glosado_brl": 150.0,
        "analista_id": "analista-123",
        "tenant_id": "amh",
        "numero_lote_tiss": "LOTE-9",
        "numero_guia_tiss": "GUIA-7",
    }
    base.update(overrides)
    return GlosaRegistroInput(**base)


def test_glosa_id_is_stable_across_redelivery_of_the_same_adjudication() -> None:
    """THE M-9 defect. The engine re-delivers an external task on lock-expiry / retry with the
    SAME variables; the id must therefore be the same on every delivery."""
    first = registrar_glosa(_glosar()).glosa_id
    second = registrar_glosa(_glosar()).glosa_id

    assert first == second
    assert first.startswith("GLOSA-analista-123-")


@pytest.mark.parametrize(
    "override",
    [
        {"codigo_glosa_tiss": "1402"},
        {"valor_glosado_brl": 151.0},
        {"analista_id": "analista-999"},
        {"tenant_id": "outra"},
        {"numero_lote_tiss": "LOTE-10"},
        {"numero_guia_tiss": "GUIA-8"},
    ],
)
def test_a_genuinely_different_adjudication_gets_a_different_id(override: dict[str, Any]) -> None:
    """Determinism must not collapse DISTINCT adjudications onto one id — that would silently
    merge two prestadores' glosas onto one recurso anchor, the opposite harm."""
    assert registrar_glosa(_glosar()).glosa_id != registrar_glosa(_glosar(**override)).glosa_id


def test_glosar_e_pagar_parcial_nao_colidem_no_mesmo_id() -> None:
    """`decisao_contas` is IN the digest (it was not before, when only one outcome was guarded):
    GLOSAR and PAGAR_PARCIAL on otherwise identical facts are two different adjudications."""
    parcial = _glosar(decisao_contas="PAGAR_PARCIAL", valor_liberado_brl=10.0)
    assert registrar_glosa(_glosar()).glosa_id != registrar_glosa(parcial).glosa_id


def test_the_analista_stays_attributable_in_the_clear() -> None:
    """ADR-0007: the adjudication is recorded in the analyst's name. Unchanged by M-9."""
    assert registrar_glosa(_glosar(analista_id="ana-7")).glosa_id.startswith("GLOSA-ana-7-")


def test_numeric_amount_digests_identically_whether_delivered_as_int_or_float() -> None:
    """An engine round-trip can present `150` where it earlier presented `150.0` (Long/Double
    decoding). Naive `str()` would then digest to two ids for ONE decision — re-introducing the
    very drift this fix removes."""
    assert (
        registrar_glosa(_glosar(valor_glosado_brl=150)).glosa_id
        == registrar_glosa(_glosar(valor_glosado_brl=150.0)).glosa_id
    )


def test_blank_instance_anchors_degrade_scope_but_never_determinism() -> None:
    """The anchors are context, NOT new guard fields: a case that lacks them still registers (the
    L0 guard set is unchanged) and still mints a STABLE id."""
    bare = _glosar(tenant_id="", numero_lote_tiss="", numero_guia_tiss="")
    result = registrar_glosa(bare)

    assert result.registered is True
    assert result.glosa_id == registrar_glosa(bare).glosa_id


def test_the_new_anchor_fields_are_not_part_of_the_l0_guard() -> None:
    """Widening an L0 refusal set is a human decision, not a side effect of an idempotency fix."""
    result = registrar_glosa(_glosar(tenant_id="", numero_guia_tiss=""))
    assert result.registered is True


def test_entry_selects_the_anchors_from_the_process_variables() -> None:
    """`pick_fields` is the whole wiring: the anchors arrive as ordinary process variables, so the
    entry function and the typed function agree."""
    variables = {
        "decisao_contas": "GLOSAR",
        "justificativa_glosa": "erro de tabela",
        "codigo_glosa_tiss": "COD-1",
        "valor_glosado_brl": 100.0,
        "analista_id": "analista-1",
        "tenant_id": "amh",
        "numero_lote_tiss": "LOTE-1",
        "numero_guia_tiss": "GUIA-1",
        "beneficiario_pseudo_id": "IGNORED-not-a-field",
    }
    from_entry = registrar_glosa_entry(variables)
    typed = registrar_glosa(
        GlosaRegistroInput(**{k: v for k, v in variables.items() if k != "beneficiario_pseudo_id"})
    )

    assert from_entry["glosa_id"] == typed.glosa_id
    # The anchors genuinely participate: dropping them changes the id.
    assert (
        from_entry["glosa_id"]
        != registrar_glosa_entry({k: v for k, v in variables.items() if k != "numero_guia_tiss"})["glosa_id"]
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
# handoff_pagamento (CONTAS→PAGTO leg) — SUBSTITUI a familia `start_recurso`
#
# The deleted worker's six assertions are preserved one-for-one against the new handoff: exact
# business key + payload, idempotency, fail-closed without engine, fail-closed without audit sink,
# refusal without a key anchor, refusal on a degenerate anchor before any engine call.
# Added on top: the I-PAGTO-1 invariant (the seeded set is pinned by EQUALITY, the four
# admissibility booleans are named negatively) and the declared source of the amount (M7).
# ---------------------------------------------------------------------------


def _pagto_vars(**over: object) -> dict[str, object]:
    base: dict[str, object] = {
        "tenant_id": "amh",
        "numero_lote_tiss": "LOTE-9",
        "numero_guia_tiss": "GUIA-7",
        "prestador_id": "prov:teste-0001",
        "competencia": "2026-05",
        "data_vencimento": "2026-07-10",
        "valor_apresentado_brl": 150.0,
        "valor_liberado_brl": 90.0,
        "conta_origem_ref": "conta:1",
        "instrumento_pagamento": "ted",
    }
    base.update(over)
    return base


_PAGTO_KEY = "PAGTO-amh-LOTE-9-prov:teste-0001"


def test_handoff_pagamento_starts_pagto_with_exact_business_key_and_payload() -> None:
    """The handoff REALLY starts SP-OP-PAGTO-001 through the fenced chokepoint. Asserts the exact
    business key, the started instance, the carried payload, and the exactly-once ADR-0007 start
    record (NOT just no-exception)."""
    engine = FakeCibSevenTransport()
    sink = FakeStartAuditSink()
    result = handoff_pagamento(
        _pagto_vars(secreta_phi="SHOULD-NOT-LEAK"), fonte_valor="apresentado", engine=engine, audit_sink=sink
    )
    assert result["handoff"] == PAGTO_PROCESS_KEY
    assert result["handoff_executado"] is True
    assert result["pagto_business_key"] == _PAGTO_KEY
    assert result["pagto_already_existed"] is False
    assert result["valor_pagamento_cents"] == 15000

    started = asyncio.run(engine.find_active_instance(_PAGTO_KEY))
    assert started is not None and started.process_key == PAGTO_PROCESS_KEY
    payload = asyncio.run(engine.get_process_status(_PAGTO_KEY)).variables
    assert payload["numero_lote_tiss"] == "LOTE-9"
    assert payload["prestador_id"] == "prov:teste-0001"
    assert payload["tipo_pagamento"] == "prestador_rede"
    assert payload["moeda"] == "BRL"
    assert "secreta_phi" not in payload  # explicit allowlist, never a passthrough

    assert sink.dedup_keys == [start_dedup_key("amh", PAGTO_PROCESS_KEY, _PAGTO_KEY)]
    [record] = sink.records
    assert record.agent_id == AUDIT_AGENT_ID
    assert record.tenant_id == "amh"
    assert record.action == f"start_process:{PAGTO_PROCESS_KEY}"
    assert record.model_id is None and record.prompt_version is None
    assert record.details["lastro_origem"] == LASTRO_ORIGEM_AUTOMATICA
    assert "input_sha256" in record.details
    assert "numero_lote_tiss" not in record.details  # identifiers hash-bound only, never in the clear


def test_handoff_pagamento_semeia_exatamente_o_conjunto_declarado() -> None:
    """I-PAGTO-1, positively. EQUALITY, never `issubset`: this is the test that stops the seed set
    from growing silently — an editor who adds a key must break THIS, not discover it in production
    as a skipped human admissibility review."""
    engine = FakeCibSevenTransport()
    handoff_pagamento(
        _pagto_vars(), fonte_valor="apresentado", engine=engine, audit_sink=FakeStartAuditSink()
    )
    payload = asyncio.run(engine.get_process_status(_PAGTO_KEY)).variables
    assert set(payload) == HANDOFF_PAGTO_SEEDED_KEYS


def test_handoff_pagamento_nunca_semeia_os_fatos_de_admissibilidade_de_pagto() -> None:
    """I-PAGTO-1, negatively and NOMINALLY. Seeding `lastro_confirmado=true` would put the order
    straight into PAGTO's clerical `DENTRO_TETO_L2` lane (`ST_ReleaseLowValue` — no User Task, and
    NOT covered by `ERR_PAYMENT_RELEASE_NOT_HUMAN`). Named one by one so a future edit that adds
    one of them fails on the name, not on a set-size mismatch."""
    engine = FakeCibSevenTransport()
    handoff_pagamento(
        _pagto_vars(
            lastro_confirmado=True,
            dados_pagamento_validos=True,
            duplicidade_suspeita=False,
            dentro_teto_l2=True,
        ),
        fonte_valor="apresentado",
        engine=engine,
        audit_sink=FakeStartAuditSink(),
    )
    payload = asyncio.run(engine.get_process_status(_PAGTO_KEY)).variables
    for forbidden in HANDOFF_PAGTO_FORBIDDEN_KEYS:
        assert forbidden not in payload, forbidden
    assert {
        "lastro_confirmado",
        "dados_pagamento_validos",
        "duplicidade_suspeita",
        "dentro_teto_l2",
    } == HANDOFF_PAGTO_FORBIDDEN_KEYS
    # Even when the caller PASSES them explicitly, they are dropped — the allowlist is the payload,
    # not a filter over the inbound variables.
    assert set(payload) == HANDOFF_PAGTO_SEEDED_KEYS


def test_handoff_pagamento_semeia_lastro_origem_e_decisor_vazio_na_perna_automatica() -> None:
    """In place of the FACT, the EVIDENCE. On the automatic leg there is no human decisor, and the
    field is left EMPTY — never filled with a plausible-looking id, which would falsify the trail
    the human admissibility form reads (ADR-0007)."""
    engine = FakeCibSevenTransport()
    handoff_pagamento(
        _pagto_vars(), fonte_valor="apresentado", engine=engine, audit_sink=FakeStartAuditSink()
    )
    payload = asyncio.run(engine.get_process_status(_PAGTO_KEY)).variables
    assert payload["lastro_origem"] == LASTRO_ORIGEM_AUTOMATICA
    assert payload["lastro_decisor_id"] == ""


def test_handoff_pagamento_semeia_lastro_humano_quando_ha_analista() -> None:
    """The human legs carry the analista as evidence — and say so in `lastro_origem`."""
    engine = FakeCibSevenTransport()
    handoff_pagamento(
        _pagto_vars(analista_id="analista-123", lastro_origem=LASTRO_ORIGEM_HUMANA),
        fonte_valor="liberado",
        engine=engine,
        audit_sink=FakeStartAuditSink(),
    )
    payload = asyncio.run(engine.get_process_status(_PAGTO_KEY)).variables
    assert payload["lastro_origem"] == LASTRO_ORIGEM_HUMANA
    assert payload["lastro_decisor_id"] == "analista-123"


@pytest.mark.parametrize(
    "forjado",
    ["recurso_deferimento_humano", "qualquer-coisa-inventada", "CONTAS_ADJUDICACAO_HUMANA", "   x   "],
    ids=["outra_cadeia", "texto_livre", "caixa_errada", "espacos"],
)
def test_handoff_pagamento_recusa_lastro_origem_fora_do_enum(forjado: str) -> None:
    """`lastro_origem` alimenta o FORMULARIO de `UT_AnaliseAdmissibilidade` (SP-OP-PAGTO-001.md).

    "O elemento chamador declara" nao e "o chamador escreve o que quiser no formulario do humano":
    um rotulo desconhecido, o valor da OUTRA cadeia (`recurso_deferimento_humano`) ou texto livre
    chegariam ao revisor como se fossem proveniencia. Paridade com `recurso.py`, que fixa a sua
    constante.
    """
    engine = FakeCibSevenTransport()
    with pytest.raises(ContasHandoffPagamentoInvalidoError) as exc:
        handoff_pagamento(
            _pagto_vars(analista_id="analista-123", lastro_origem=forjado),
            fonte_valor="liberado",
            engine=engine,
            audit_sink=FakeStartAuditSink(),
        )
    assert "lastro_origem" in str(exc.value)
    assert asyncio.run(engine.find_any_instance(_PAGTO_KEY)) is None, (
        "a recusa e ANTES do start: nenhuma ordem de pagamento pode ter sido criada"
    )


def test_handoff_pagamento_recusa_lastro_humano_sem_decisor() -> None:
    """O par contraditorio que o gatekeeper exibiu: "adjudicado por humano" sem humano nomeado.

    Espelha `recurso.py`, que recusa decisor em branco. O campo NAO e preenchido com um valor
    plausivel — a ordem simplesmente nao sai.
    """
    engine = FakeCibSevenTransport()
    with pytest.raises(ContasHandoffPagamentoInvalidoError) as exc:
        handoff_pagamento(
            _pagto_vars(analista_id="   ", lastro_origem=LASTRO_ORIGEM_HUMANA),
            fonte_valor="liberado",
            engine=engine,
            audit_sink=FakeStartAuditSink(),
        )
    assert "analista_id" in str(exc.value)
    assert asyncio.run(engine.find_any_instance(_PAGTO_KEY)) is None


def test_handoff_pagamento_recusa_lastro_automatico_com_decisor() -> None:
    """A contradicao SIMETRICA: "adjudicado automaticamente" carregando o id de um humano.

    A documentacao de `ST_HandoffPagamentoAuto` diz `lastro_decisor_id` VAZIO, "nunca inventado";
    o inverso — carregar um id numa perna onde nenhum humano decidiu — e igualmente evidencia
    falsa, so que na direcao que o revisor tenderia a acreditar.
    """
    engine = FakeCibSevenTransport()
    with pytest.raises(ContasHandoffPagamentoInvalidoError) as exc:
        handoff_pagamento(
            _pagto_vars(analista_id="analista-123", lastro_origem=LASTRO_ORIGEM_AUTOMATICA),
            fonte_valor="apresentado",
            engine=engine,
            audit_sink=FakeStartAuditSink(),
        )
    assert "analista_id" in str(exc.value) or "decisor" in str(exc.value)
    assert asyncio.run(engine.find_any_instance(_PAGTO_KEY)) is None


def test_handoff_pagamento_paridade_de_lastro_com_a_cadeia_recurso() -> None:
    """As duas metades de I-PAGTO-1 tem de ter a MESMA forma (a alegacao de `contas.py`).

    `recurso.py` fixa `LASTRO_ORIGEM_RECURSO` e recusa decisor em branco. CONTAS tem um enum de
    dois valores porque tem duas pernas (automatica e humana), mas as regras sao as mesmas: enum
    FECHADO e nenhum par contraditorio. Este teste prova a propriedade estrutural, nao repete os
    casos.
    """
    from maezo.tools.workers import recurso as recurso_mod

    assert set(LASTROS_ORIGEM_PERMITIDOS) == {LASTRO_ORIGEM_AUTOMATICA, LASTRO_ORIGEM_HUMANA}
    assert recurso_mod.LASTRO_ORIGEM_RECURSO not in LASTROS_ORIGEM_PERMITIDOS, (
        "o valor da cadeia RECURSO nunca pode ser emitido pela cadeia CONTAS"
    )
    assert LASTRO_ORIGEM_HUMANA not in {recurso_mod.LASTRO_ORIGEM_RECURSO}


def test_handoff_pagamento_fonte_valor_apresentado_usa_valor_apresentado_brl() -> None:
    """M7: the CALLING ELEMENT declares the source. `apresentado` = nothing was glosado."""
    engine = FakeCibSevenTransport()
    result = handoff_pagamento(
        _pagto_vars(), fonte_valor="apresentado", engine=engine, audit_sink=FakeStartAuditSink()
    )
    assert result["valor_pagamento_cents"] == 15000
    assert result["fonte_valor"] == "apresentado"


def test_handoff_pagamento_fonte_valor_liberado_usa_valor_liberado_brl() -> None:
    """M7: `liberado` = the User Task's own field — never the presented amount, which on the
    PAGAR_PARCIAL leg still includes the glosada part."""
    engine = FakeCibSevenTransport()
    result = handoff_pagamento(
        _pagto_vars(), fonte_valor="liberado", engine=engine, audit_sink=FakeStartAuditSink()
    )
    assert result["valor_pagamento_cents"] == 9000
    assert result["fonte_valor"] == "liberado"


def test_handoff_pagamento_recusa_fonte_valor_desconhecida() -> None:
    """An undeclared source is a refusal, never a guess at which variable the amount lives in."""
    with pytest.raises(ContasHandoffPagamentoInvalidoError, match="fonte_valor"):
        handoff_pagamento(
            _pagto_vars(),
            fonte_valor="chutado",
            engine=FakeCibSevenTransport(),
            audit_sink=FakeStartAuditSink(),
        )


@pytest.mark.parametrize(
    "valor",
    [0.0, "", "   ", None, "abc", -1.0],
    ids=["zero", "vazio", "ws", "none", "nao-numerico", "negativo"],
)
def test_handoff_pagamento_recusa_valor_zero_ausente_ou_nao_numerico(valor: object) -> None:
    """The `0.0` case is REAL, not theoretical: `marina/graph.py` seeds
    `float(state.get("valor_apresentado_brl", 0.0))`, so a state missing the field produces `0.0`,
    and 0 cents would sail through PAGTO's `r_dentro_teto_l2` (`0 <= 10000000`) creating a silent
    R$ 0,00 order. `_parse_valor_monetario` NEVER defaults to 0."""
    engine = FakeCibSevenTransport()
    sink = FakeStartAuditSink()
    with pytest.raises(ContasHandoffPagamentoInvalidoError, match="valor_apresentado_brl"):
        handoff_pagamento(
            _pagto_vars(valor_apresentado_brl=valor),
            fonte_valor="apresentado",
            engine=engine,
            audit_sink=sink,
        )
    assert sink.dedup_keys == []  # emit-before-effect: refusal precedes ANY audit/engine call


def test_handoff_pagamento_recusa_data_vencimento_em_branco() -> None:
    """`data_vencimento` is `sim` in the PAGTO contract. An order with no due date is malformed;
    refusing produces a visible incident, defaulting would produce a FALSE deadline (OQ-2)."""
    engine = FakeCibSevenTransport()
    sink = FakeStartAuditSink()
    with pytest.raises(ContasHandoffPagamentoInvalidoError, match="data_vencimento"):
        handoff_pagamento(
            _pagto_vars(data_vencimento="   "), fonte_valor="apresentado", engine=engine, audit_sink=sink
        )
    assert sink.dedup_keys == []


def test_handoff_pagamento_idempotent_returns_existing_active_instance() -> None:
    """A PAGTO-001 already active for this lote is returned unchanged — never a second order.

    This is what makes a re-delivered handoff (or a second decision on the same lote after a
    `Msg_ContasLinhasAtualizadas` round-trip) converge instead of paying twice."""
    engine = FakeCibSevenTransport()
    engine.seed_instance(
        ProcessInstance(
            instance_id="pre-existing-pagto",
            process_key=PAGTO_PROCESS_KEY,
            business_key=_PAGTO_KEY,
            state="ACTIVE",
            already_existed=True,
        )
    )
    result = handoff_pagamento(
        _pagto_vars(), fonte_valor="apresentado", engine=engine, audit_sink=FakeStartAuditSink()
    )
    assert result["pagto_already_existed"] is True
    assert result["pagto_instance_id"] == "pre-existing-pagto"


def test_handoff_pagamento_fail_closed_when_engine_seam_not_wired() -> None:
    with pytest.raises(RuntimeError, match="engine seam"):
        handoff_pagamento(_pagto_vars(), fonte_valor="apresentado", audit_sink=FakeStartAuditSink())


def test_handoff_pagamento_fail_closed_when_audit_sink_not_wired() -> None:
    with pytest.raises(RuntimeError, match="audit sink"):
        handoff_pagamento(_pagto_vars(), fonte_valor="apresentado", engine=FakeCibSevenTransport())


def test_handoff_pagamento_refuses_without_lote_identity() -> None:
    """No lote/prestador anchor -> deterministic refusal, never a start under an empty key."""
    engine = FakeCibSevenTransport()
    sink = FakeStartAuditSink()
    with pytest.raises(ContasHandoffPagamentoInvalidoError):
        handoff_pagamento(
            _pagto_vars(numero_lote_tiss="", prestador_id=""),
            fonte_valor="apresentado",
            engine=engine,
            audit_sink=sink,
        )
    assert sink.dedup_keys == []  # emit-before-effect: no audit, no start


@pytest.mark.parametrize(
    "over",
    [
        {"numero_lote_tiss": "   "},  # whitespace-only anchor
        {"prestador_id": "\t "},  # whitespace-only anchor
        {"numero_lote_tiss": None},  # explicit None (str(None)=="None" is truthy — must NOT slip)
        {"prestador_id": None},
        {"tenant_id": ""},  # blank tenant -> PAGTO--LOTE-…-prov degenerate key forbidden
        {"tenant_id": "   "},
        {"tenant_id": None},
    ],
    ids=[
        "lote-ws",
        "prestador-ws",
        "lote-none",
        "prestador-none",
        "tenant-empty",
        "tenant-ws",
        "tenant-none",
    ],
)
def test_handoff_pagamento_refuses_degenerate_anchors_before_any_engine_call(
    over: dict[str, object],
) -> None:
    """EB-4 R1 finding: whitespace-only / explicit-None anchors and a blank/None tenant_id all
    refuse (`non_blank` semantics, shared with the bridge) BEFORE any engine call — no start
    attempted, no audit emitted, never a degenerate business key like `PAGTO--L-None`."""
    engine = FakeCibSevenTransport()
    sink = FakeStartAuditSink()
    with pytest.raises(ContasHandoffPagamentoInvalidoError):
        handoff_pagamento(_pagto_vars(**over), fonte_valor="apresentado", engine=engine, audit_sink=sink)
    assert sink.dedup_keys == []  # emit-before-effect: refusal precedes ANY audit/engine call
    assert sink.records == []


# ----- M2 (espelhado do PR-3): os segmentos da chave STRICT sao NORMALIZADOS -----------------

#: As 6 formas do MESMO lote adjudicado que uma interpolacao crua mintaria como 6 chaves
#: distintas. `non_blank` recusa ausente/vazio/whitespace-only/`None`, mas ACEITA
#: whitespace-PADDED — e `str()` preserva o padding. Numa familia STRICT de dedup, uma chave a
#: mais e uma SEGUNDA ordem de pagamento para a mesma conta.
_VARIANTES_DO_MESMO_LOTE: tuple[dict[str, object], ...] = (
    {"tenant_id": "amh", "numero_lote_tiss": "LOTE-9", "prestador_id": "PREST-1"},
    {"tenant_id": "amh", "numero_lote_tiss": " LOTE-9 ", "prestador_id": "PREST-1"},
    {"tenant_id": "amh", "numero_lote_tiss": "LOTE-9", "prestador_id": "PREST-1\t"},
    {"tenant_id": " amh", "numero_lote_tiss": "LOTE-9", "prestador_id": "PREST-1"},
    {"tenant_id": "amh", "numero_lote_tiss": "LOTE-9", "prestador_id": "  PREST-1  "},
    {"tenant_id": "amh", "numero_lote_tiss": "LOTE-9\n", "prestador_id": "PREST-1"},
)

_PAGTO_BK_CANONICA = "PAGTO-amh-LOTE-9-PREST-1"


@pytest.mark.parametrize("variante", _VARIANTES_DO_MESMO_LOTE)
def test_pagto_business_key_colapsa_as_seis_variantes_numa_chave(variante: dict) -> None:
    """M2: as 6 variantes do MESMO lote adjudicado produzem UMA chave — a de referencia."""
    assert _pagto_business_key(**variante) == _PAGTO_BK_CANONICA  # type: ignore[arg-type]


@pytest.mark.parametrize("variante", _VARIANTES_DO_MESMO_LOTE)
def test_ordem_pagamento_id_colapsa_as_seis_variantes_num_id(variante: dict) -> None:
    """A ordem que a chave carrega tem de concordar com ela sobre a identidade da conta."""
    assert _ordem_pagamento_id(**variante) == "ORDEM-CONTA-amh-LOTE-9-PREST-1"  # type: ignore[arg-type]


def test_as_seis_variantes_sao_realmente_distintas_sem_normalizacao() -> None:
    """CONTROLE NEGATIVO: sem `key_segment`, a interpolacao crua mintaria 6 chaves diferentes. Se
    este teste ficar verde com 1 chave, o corpus perdeu o poder de discriminar."""
    cruas = {
        "PAGTO-{tenant_id}-{numero_lote_tiss}-{prestador_id}".format(**v) for v in _VARIANTES_DO_MESMO_LOTE
    }
    assert len(cruas) == 6, cruas


@pytest.mark.parametrize("variante", _VARIANTES_DO_MESMO_LOTE)
def test_pagto_business_key_usa_a_normalizacao_canonica_do_repo(variante: dict) -> None:
    """PINO DE IGUALDADE (M2): a normalizacao e a MESMA de `maezo.agents.andre.keys.key_segment`
    que `recurso._pagto_business_key` usa — uma familia STRICT, um composer, duas cadeias."""
    esperado = "PAGTO-{}-{}-{}".format(
        key_segment(variante["tenant_id"]),
        key_segment(variante["numero_lote_tiss"]),
        key_segment(variante["prestador_id"]),
    )
    assert _pagto_business_key(**variante) == esperado  # type: ignore[arg-type]


def test_handoff_pagamento_recusa_ancora_que_normaliza_para_vazio() -> None:
    """`non_blank` ACEITA `0`, cujo `key_segment` e `""` — a emptiness e re-checada DEPOIS de
    normalizar, senao a chave sairia degenerada (`PAGTO-amh--PREST-1`)."""
    engine = FakeCibSevenTransport()
    sink = FakeStartAuditSink()
    with pytest.raises(ContasHandoffPagamentoInvalidoError):
        handoff_pagamento(
            _pagto_vars(numero_lote_tiss=0), fonte_valor="apresentado", engine=engine, audit_sink=sink
        )
    assert sink.dedup_keys == []


def test_handoff_pagamento_targets_the_strict_dedup_family() -> None:
    """SP-OP-PAGTO-001 is the ONE STRICT start-dedup family: the chokepoint additionally demands a
    dedup-reporting sink + history-querying transport, so a mis-wired handoff FAILS rather than
    paying twice. This test pins the process key the policy is keyed on."""
    from maezo.tools.mcp_cibseven.transport import _START_DEDUP_POLICY

    assert PAGTO_PROCESS_KEY == "SP-OP-PAGTO-001"
    assert _START_DEDUP_POLICY[PAGTO_PROCESS_KEY] is True


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
# devolver_conta — SUBSTITUI `reconcile_payment`
# ---------------------------------------------------------------------------


def test_devolver_conta() -> None:
    """devolver_conta registers the return of the conta for correction — no adverse effect."""
    result = devolver_conta("LOTE-010", "GUIDE-002", "faltam anexos das linhas 3 e 4", "analista-1")
    assert result["devolvida"] is True
    assert result["numero_lote_tiss"] == "LOTE-010"
    assert result["justificativa_devolucao"] == "faltam anexos das linhas 3 e 4"
    assert result["analista_id"] == "analista-1"


def test_devolver_conta_exige_justificativa() -> None:
    """A conta that comes back without a stated reason cannot be corrected by the prestador."""
    with pytest.raises(ContasDevolucaoInvalidaError, match="justificativa_devolucao"):
        devolver_conta("LOTE-010", "GUIDE-002", "   ", "analista-1")


def test_devolver_conta_exige_analista() -> None:
    """ADR-0007: the devolucao goes out in the name of whoever decided it — never anonymous."""
    with pytest.raises(ContasDevolucaoInvalidaError, match="analista_id"):
        devolver_conta("LOTE-010", "GUIDE-002", "faltam anexos", "  ")


# ---------------------------------------------------------------------------
# emitir_demonstrativo — the communication to the prestador (M6)
# ---------------------------------------------------------------------------


def _demonstrativo(**over: Any) -> DemonstrativoInput:
    base: dict[str, Any] = {
        "tenant_id": "amh",
        "numero_lote_tiss": "LOTE-9",
        "numero_guia_tiss": "GUIA-7",
        "prestador_id": "prov:teste-0001",
        "competencia": "2026-05",
        "desfecho": "glosa_aplicada_humano",
    }
    base.update(over)
    return DemonstrativoInput(**base)


def test_emitir_demonstrativo_protocolo_deterministico() -> None:
    """A re-delivered external task must carry the SAME protocol — one act, one identity.

    The purity fence keeps `time`/`uuid`/`random` out of this module; this is the property that
    makes the constraint satisfiable, and it mirrors `recurso._mint_protocolo_resposta`."""
    first = emitir_demonstrativo(
        _demonstrativo(), tipo_comunicacao="demonstrativo_analise", business_key="CONTAS-amh-LOTE-9"
    )
    second = emitir_demonstrativo(
        _demonstrativo(), tipo_comunicacao="demonstrativo_analise", business_key="CONTAS-amh-LOTE-9"
    )
    assert first["protocolo_demonstrativo"] == second["protocolo_demonstrativo"]
    assert first["protocolo_demonstrativo"].startswith("DEMONS-")


def test_emitir_demonstrativo_protocolo_difere_por_tipo_comunicacao() -> None:
    """Two DIFFERENT communications about one conta are two acts, not one."""
    key = "CONTAS-amh-LOTE-9"
    a = emitir_demonstrativo(_demonstrativo(), tipo_comunicacao="demonstrativo_analise", business_key=key)
    b = emitir_demonstrativo(_demonstrativo(), tipo_comunicacao="devolucao_para_correcao", business_key=key)
    assert a["protocolo_demonstrativo"] != b["protocolo_demonstrativo"]


def test_emitir_demonstrativo_nunca_usa_time_ou_random() -> None:
    """Mirrors `test_recurso.py`'s protocol-purity assertion: the mint is a pure function of the
    business key and the discriminator, so it is stable across a fake wall-clock jump."""
    import time

    key = "CONTAS-amh-LOTE-9"
    first = emitir_demonstrativo(
        _demonstrativo(), tipo_comunicacao="demonstrativo_analise", business_key=key
    )["protocolo_demonstrativo"]
    time.sleep(0.01)
    second = emitir_demonstrativo(
        _demonstrativo(), tipo_comunicacao="demonstrativo_analise", business_key=key
    )["protocolo_demonstrativo"]
    assert first == second

    # And structurally: no non-deterministic identifier source is IMPORTED by the module at all
    # (AST, not substring — the `_glosa_id` docstring legitimately NAMES the wall-clock mint it
    # replaced, and a substring check would fail on the history it is right to record).
    import ast

    tree = ast.parse(Path("src/maezo/tools/workers/contas.py").read_text(encoding="utf-8"))
    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert imported.isdisjoint({"time", "uuid", "random", "secrets"}), sorted(imported)


def test_emitir_demonstrativo_recusa_tipo_comunicacao_desconhecido() -> None:
    """An undeclared discriminator is a refusal — never a silent default to the common type,
    which would send an adjudication where a devolucao was meant."""
    with pytest.raises(ContasComunicacaoInvalidaError, match="tipo_comunicacao"):
        emitir_demonstrativo(_demonstrativo(), tipo_comunicacao="carta_bonita")
    with pytest.raises(ContasComunicacaoInvalidaError):
        emitir_demonstrativo(_demonstrativo(), tipo_comunicacao="")
    assert {"demonstrativo_analise", "devolucao_para_correcao"} == COMUNICACAO_TIPOS


def test_emitir_demonstrativo_nao_inventa_responsavel_na_perna_automatica() -> None:
    """On the two automatic legs there is no human decision to transmit: the document does NOT
    cite an analista and the signature field stays EMPTY. Inventing a human author for an
    automatic outcome would falsify the trail (ADR-0007)."""
    auto = emitir_demonstrativo(
        _demonstrativo(desfecho="pagar_integral"), tipo_comunicacao="demonstrativo_analise"
    )
    assert auto["analista_id"] == ""
    assert auto["assinatura_analista"] == ""

    humano = emitir_demonstrativo(
        _demonstrativo(analista_id="analista-1"), tipo_comunicacao="demonstrativo_analise"
    )
    assert humano["assinatura_analista"] == "analista-1"


def test_emitir_demonstrativo_nao_transmite_tiss_nesta_fase() -> None:
    """TASY write DROP (ADR-0013): the protocol is SYNTHETIC and the worker says so, rather than
    claiming a transmission it did not make (OQ-1)."""
    assert (
        emitir_demonstrativo(_demonstrativo(), tipo_comunicacao="demonstrativo_analise")["tiss_transmitido"]
        is False
    )


def test_emitir_demonstrativo_entry_le_o_tipo_do_elemento_chamador() -> None:
    """`tipo_comunicacao` is the calling element's `camunda:inputParameter`, merged into the task
    variables by the engine — read here, NEVER inferred from the outcome."""
    variables = {
        "tenant_id": "amh",
        "numero_lote_tiss": "LOTE-9",
        "prestador_id": "prov:teste-0001",
        "tipo_comunicacao": "devolucao_para_correcao",
        "motivo_devolucao": "MOT-01",
        "beneficiario_pseudo_id": "IGNORED-not-a-field",
    }
    result = emitir_demonstrativo_entry(variables)
    assert result["tipo_comunicacao"] == "devolucao_para_correcao"

    with pytest.raises(ContasComunicacaoInvalidaError):
        emitir_demonstrativo_entry({k: v for k, v in variables.items() if k != "tipo_comunicacao"})


# ---------------------------------------------------------------------------
# publish
# ---------------------------------------------------------------------------


def test_publish_contas() -> None:
    """publish emits a domain event."""
    result = publish("contas.completed", {"desfecho": "pagar_integral"})
    assert result["published"] is True
    assert "contas.completed" in result["topic"]


# ---------------------------------------------------------------------------
# Error types
# ---------------------------------------------------------------------------


def test_contas_glosa_not_human_is_permission_error() -> None:
    """`ContasGlosaNotHumanError` must keep the SAME base class as the guard it replaces.

    The base class is not cosmetic: `PermissionError` is what routes the refusal down ADR-0030
    §5's audited-incident path instead of a `bpmnError` (which on CIB Seven 2.1.0 would silently
    end the process scope). And the `_NOT_HUMAN` suffix is what makes
    `harness.is_guard_refusal_code` recognise it with no harness change at all."""
    assert issubclass(ContasGlosaNotHumanError, PermissionError)

    from maezo.tools.workers.harness import is_guard_refusal_code

    assert is_guard_refusal_code("ERR_CONTAS_GLOSA_NOT_HUMAN") is True


def test_contas_handoff_pagamento_invalido_is_value_error() -> None:
    assert issubclass(ContasHandoffPagamentoInvalidoError, ValueError)


def test_contas_comunicacao_e_devolucao_invalidas_sao_value_error() -> None:
    assert issubclass(ContasComunicacaoInvalidaError, ValueError)
    assert issubclass(ContasDevolucaoInvalidaError, ValueError)


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


def test_identify_glosa_entry_ecoa_data_vencimento_do_lote() -> None:
    """MAJOR-1: `data_vencimento` e produzida no INTAKE, e o intake e `ST_ApurarDivergencias`.

    Ecoada VERBATIM (so `strip`), nunca defaultada: um vencimento inventado e um prazo falso, e
    `handoff_pagamento` recusa em branco. O que o intake acrescenta e visibilidade — a variavel
    passa a existir no processo a partir da PRIMEIRA tarefa, em vez de so ser lida quatro tarefas
    depois pelo handoff.
    """
    out = identify_glosa_entry(
        {
            "tenant_id": "amh",
            "numero_lote_tiss": "LOTE-1",
            "data_vencimento": "  2026-07-10  ",
            "linhas_conta_refs": [{"valor_apresentado_brl": 100.0}],
        }
    )
    assert out["data_vencimento"] == "2026-07-10"


def test_identify_glosa_entry_nao_inventa_data_vencimento_ausente() -> None:
    """A metade NEGATIVA da anterior: sem a data no lote, a variavel sai VAZIA — o intake nunca
    fabrica um vencimento. A recusa fail-closed continua sendo do `handoff_pagamento`."""
    out = identify_glosa_entry(
        {
            "tenant_id": "amh",
            "numero_lote_tiss": "LOTE-1",
            "linhas_conta_refs": [{"valor_apresentado_brl": 100.0}],
        }
    )
    assert out["data_vencimento"] == ""


@pytest.mark.parametrize(
    ("recebido", "esperado"),
    [("2026-05-20", "2026-05-20"), ("2026-05-20T00:00:00Z", "2026-05-20")],
    ids=["iso_date", "iso_datetime"],
)
def test_identify_glosa_entry_normaliza_a_ancora_de_sla(recebido: str, esperado: str) -> None:
    """GAP-CONTAS-4: `contas_sla` computa `timeDate` ABSOLUTO a partir desta ancora; um datetime
    completo tem de virar a data de calendario que a FEEL concatena com "T00:00:00"."""
    out = identify_glosa_entry(
        {
            "tenant_id": "amh",
            "numero_lote_tiss": "LOTE-1",
            "data_recebimento_lote": recebido,
            "linhas_conta_refs": [{"valor_apresentado_brl": 100.0}],
        }
    )
    assert out["data_recebimento_lote"] == esperado


@pytest.mark.parametrize("recebido", ["", "   ", "20/05/2026", "nao-e-data"], ids="abcd")
def test_identify_glosa_entry_defaulta_a_ancora_fail_safe_para_hoje(recebido: str) -> None:
    """Ausente/malformada => HOJE/UTC (fail-safe), nunca uma data passada plausivel: encurtar um
    prazo que a operadora DEVE ao prestador seria o erro adverso. A FEEL da `contas_sla` nunca
    pode ver `null`/`""` — nao produz um timer atrasado, produz um timer irresolvivel."""
    hoje = datetime.now(UTC).strftime("%Y-%m-%d")
    out = identify_glosa_entry(
        {
            "tenant_id": "amh",
            "numero_lote_tiss": "LOTE-1",
            "data_recebimento_lote": recebido,
            "linhas_conta_refs": [{"valor_apresentado_brl": 100.0}],
        }
    )
    assert out["data_recebimento_lote"] == hoje


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


def test_registrar_glosa_entry_guards_missing_human_decision() -> None:
    """registrar_glosa_entry raises the guard at the dict boundary too."""
    with pytest.raises(ContasGlosaNotHumanError):
        registrar_glosa_entry({"decisao_contas": ""})


def test_registrar_glosa_entry_happy_path() -> None:
    variables = {
        "decisao_contas": "GLOSAR",
        "justificativa_glosa": "erro de tabela",
        "codigo_glosa_tiss": "COD-1",
        "valor_glosado_brl": 100.0,
        "analista_id": "analista-1",
    }
    direct = registrar_glosa(GlosaRegistroInput(**variables))
    result = registrar_glosa_entry(variables)
    assert result["registered"] == direct.registered


def test_register_contas_workers_registers_all_11_topics() -> None:
    """The registration is the contract with the BPMN: 11 topics, exactly these names.

    `start_recurso` and `reconcile_payment` are GONE (not disabled); `registrar_glosa`,
    `emitir_demonstrativo`, `devolver_conta` and `handoff_pagamento` are the new surface. Pinned
    so a rename in either direction has to come here first."""

    class _Harness:
        def __init__(self) -> None:
            self.topics: list[str] = []

        def register_worker(self, worker: Any) -> None:
            self.topics.append(worker.topic)

    harness = _Harness()
    register_contas_workers(harness)  # type: ignore[arg-type]
    assert sorted(harness.topics) == sorted(
        [
            "operadora.contas.identify_glosa",
            "operadora.contas.analyze_reason",
            "operadora.contas.calculate_impact",
            "operadora.contas.prepare_triage_dossier",
            "operadora.contas.registrar_glosa",
            "operadora.contas.emitir_demonstrativo",
            "operadora.contas.devolver_conta",
            "operadora.contas.notify_sla_risk",
            "operadora.contas.handoff_pagamento",
            "operadora.contas.start_fraude",
            "operadora.contas.publish",
        ]
    )
    assert len(harness.topics) == 11


def test_publish_entry_round_trips_publish() -> None:
    variables = {"event_type": "contas.completed", "payload": {"desfecho": "pagar_integral"}}
    assert publish_entry(variables) == publish("contas.completed", {"desfecho": "pagar_integral"}, "")
