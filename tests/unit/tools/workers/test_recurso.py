"""Unit tests for maezo.tools.workers.recurso — SP-OP-RECURSO-001.

TDD London School: tests exercise the external task contracts.

PERSPECTIVE (ADR-0040): the process owner is the OPERADORA. She RECEIVES the glosa appeal the
prestador filed and ANSWERS it. There is no `submit_appeal`, no `track_status` and no
`reconcile_payment` here any more — those were the appellant's acts, and they were deleted, not
renamed. What replaces them is `comunicar_resposta` (the payer's answer) and `handoff_pagamento`
(the payment order for a glosa the payer reversed).
"""

import asyncio
import re
from decimal import Decimal
from itertools import product
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

import pytest
import structlog

from maezo.agents.andre.keys import key_segment
from maezo.gateway.audit import AuditRecord
from maezo.tools.mcp_cibseven.transport import (
    FakeCibSevenTransport,
    ProcessInstance,
    StartDedupGateUnavailableError,
    start_dedup_key,
)
from maezo.tools.workers.dmn_transport import DmnEvaluationError, FakeDmnTransport
from maezo.tools.workers.harness import (
    AUDIT_AGENT_ID,
    ExternalTask,
    FakeKafkaPublisher,
    WorkerBpmnError,
)
from maezo.tools.workers.phi_vars import PHI_PROCESS_VARS
from maezo.tools.workers.recurso import (
    COMUNICACAO_TIPOS,
    FONTES_VALOR_PERMITIDAS,
    HANDOFF_PAGTO_FORBIDDEN_KEYS,
    HANDOFF_PAGTO_SEEDED_KEYS,
    LASTRO_ORIGEM_RECURSO,
    PAGTO_PROCESS_KEY,
    RECURSO_BPMN_ERROR_ALLOWLIST,
    ComunicarRespostaInput,
    EscalateAnsTimeoutInput,
    NotifySlaRiskInput,
    RecursoHandoffPagamentoInvalidoError,
    RecursoIndeferimentoInput,
    RecursoIndeferimentoNotHumanError,
    RecursoInput,
    _mint_protocolo_resposta,
    _ordem_pagamento_id,
    _pagto_business_key,
    _require_glosa_id,
    analyze_merits,
    analyze_request_entry,
    assess_eligibility,
    comunicar_resposta,
    escalate_ans_timeout,
    escalate_to_junta,
    handoff_pagamento,
    handoff_pagamento_entry,
    make_comunicar_resposta_handler,
    make_escalate_ans_timeout_handler,
    make_notify_sla_risk_handler,
    notify_sla_risk,
    prepare_dossier,
    publish_completed,
    publish_completed_entry,
    registrar_indeferimento,
    registrar_indeferimento_entry,
    request_documents,
    request_documents_entry,
    validate_recurso,
    validate_recurso_entry,
)
from tests.support.audit_fakes import FakeStartAuditSink


def _task(
    *,
    topic: str = "operadora.recurso.probe",
    business_key: str = "RECURSO-amh-GUIA-1-GLOSA-1",
    variables: dict | None = None,
) -> ExternalTask:
    return ExternalTask(
        task_id="task-1",
        topic=topic,
        process_instance_id="proc-1",
        business_key=business_key,
        worker_id="w-1",
        variables=variables or {},
    )


def _recurso_admissibility_fake(*, roteamento: str, motivo: str = "") -> FakeDmnTransport:
    """Rows verified live against the compose engine (T1.5 parity run)."""
    fake = FakeDmnTransport()
    fake.register("recurso_admissibility", [{"roteamento": roteamento, "motivo": motivo}])
    return fake


def _recurso_admissibility_and_eligibility_fake(
    *,
    adm_roteamento: str = "SEGUE_ANALISE",
    adm_motivo: str = "",
    elig_roteamento: str,
    grupo_revisor: str,
    elig_motivo: str = "",
) -> FakeDmnTransport:
    """Rows verified live against the compose engine (T1.5 parity run)."""
    fake = FakeDmnTransport()
    fake.register("recurso_admissibility", [{"roteamento": adm_roteamento, "motivo": adm_motivo}])
    fake.register(
        "recurso_eligibility",
        [{"roteamento": elig_roteamento, "grupo_revisor": grupo_revisor, "motivo": elig_motivo}],
    )
    return fake


# ---------------------------------------------------------------------------
# validate_recurso — the INTAKE task (ST_ValidarRecurso)
# ---------------------------------------------------------------------------


def test_validate_recurso_valid() -> None:
    """validate_recurso accepts a valid recurso with glosa confirmada, and ECHOES the normalised
    anchor back as a process variable (recurso_sla's FEEL has no other branch to fall into)."""
    inp = RecursoInput(
        tenant_id="amh",
        glosa_id="GLOSA-001",
        numero_guia_tiss="GUIDE-001",
        prestador_id="PREST-001",
        data_recebimento_recurso_iso="2026-05-20",
        glosa_existe=True,
        dentro_prazo_recurso=True,
        documentacao_recurso_completa=True,
    )
    result = validate_recurso(inp)
    assert result.valid is True
    assert result.glosa_existe is True
    assert result.data_recebimento_recurso_iso == "2026-05-20"


def test_validate_recurso_missing_glosa_id() -> None:
    """validate_recurso marks invalid when glosa_id is empty (the typed function still runs; the
    dict-boundary entry is where the ERR_RECURSO_INVALID_GLOSA guard fires first)."""
    inp = RecursoInput(
        tenant_id="amh",
        glosa_id="",
        numero_guia_tiss="GUIDE-001",
        data_recebimento_recurso_iso="2026-05-20",
        dentro_prazo_recurso=True,
    )
    result = validate_recurso(inp)
    assert result.valid is False
    assert result.glosa_existe is False


def test_validate_recurso_fora_prazo() -> None:
    """validate_recurso captures fora_prazo in errors but doesn't decide, and the anchor it
    normalises is the OPERADORA's receipt date — never the prestador's ciencia date."""
    inp = RecursoInput(
        tenant_id="amh",
        glosa_id="GLOSA-001",
        numero_guia_tiss="GUIDE-001",
        glosa_existe=True,
        dentro_prazo_recurso=False,
        data_ciencia_alegada_prestador="2024-01-01",
        data_recebimento_recurso_iso="2026-05-20",
    )
    result = validate_recurso(inp)
    assert "fora do prazo" in str(result.errors).lower()
    assert result.data_recebimento_recurso_iso == "2026-05-20"


def test_validate_recurso_normaliza_data_recebimento_ausente_com_warning() -> None:
    """GAP-RECURSO-1 (ADR-0040 §2.3): the anchor is defaulted FAIL-SAFE to TODAY/UTC when absent
    — never silently, and NEVER to the appellant's own alleged ciencia date (which is exactly the
    perspective swap this redesign removes: an earlier date would be a plausible-looking value
    that belongs to the other party's clock)."""
    from datetime import UTC, datetime

    inp = RecursoInput(
        tenant_id="amh",
        glosa_id="GLOSA-001",
        numero_guia_tiss="GUIDE-001",
        data_ciencia_alegada_prestador="2024-01-01",
        data_recebimento_recurso_iso="",
        glosa_existe=True,
        dentro_prazo_recurso=True,
    )
    with structlog.testing.capture_logs() as logs:
        result = validate_recurso(inp)
    assert result.data_recebimento_recurso_iso == datetime.now(UTC).strftime("%Y-%m-%d")
    assert result.data_recebimento_recurso_iso != "2024-01-01"
    warnings = [
        entry for entry in logs if entry.get("event") == "recurso.validate_recurso.data_recebimento_defaulted"
    ]
    assert warnings, "o default fail-safe DEVE ser logado — nunca um fallback silencioso"
    assert warnings[0]["log_level"] == "warning"


@pytest.mark.parametrize("raw", ["", "   ", "nao-e-uma-data", "20/05/2026"])
def test_validate_recurso_normaliza_data_recebimento_invalida(raw: str) -> None:
    """Any unusable shape defaults with a warning — the DMN must never see null/garbage."""
    from datetime import UTC, datetime

    inp = RecursoInput(tenant_id="amh", glosa_id="G-1", data_recebimento_recurso_iso=raw)
    with structlog.testing.capture_logs() as logs:
        result = validate_recurso(inp)
    assert result.data_recebimento_recurso_iso == datetime.now(UTC).strftime("%Y-%m-%d")
    assert any(e.get("event") == "recurso.validate_recurso.data_recebimento_defaulted" for e in logs)


def test_validate_recurso_normaliza_datetime_iso_para_data_calendario() -> None:
    """A full ISO datetime is trimmed to the calendar date the DMN concatenates 'T00:00:00' to."""
    inp = RecursoInput(tenant_id="amh", glosa_id="G-1", data_recebimento_recurso_iso="2026-05-20T09:31:00Z")
    assert validate_recurso(inp).data_recebimento_recurso_iso == "2026-05-20"


def test_validate_recurso_raises_worker_bpmn_error_when_glosa_id_absent() -> None:
    """THIRD site of ERR_RECURSO_INVALID_GLOSA and the FIRST on every path: an origin-invalid
    appeal never reaches BRT_Admissibilidade (BE_GlosaInvalidaValidacao boundary catch)."""
    with pytest.raises(WorkerBpmnError) as exc:
        validate_recurso_entry({"tenant_id": "amh", "glosa_id": "  "})
    assert exc.value.error_code == "ERR_RECURSO_INVALID_GLOSA"


# ---------------------------------------------------------------------------
# assess_eligibility
# ---------------------------------------------------------------------------


def test_assess_eligibility_glosa_nao_existe() -> None:
    """Glosa not confirmed -> ANALISE_HUMANA, never auto-inadmissibility (live-verified motivo)."""
    inp = RecursoInput(tenant_id="amh", glosa_id="GLOSA-001")
    from maezo.tools.workers.recurso import RecursoValidationResult

    validation = RecursoValidationResult(
        valid=False,
        glosa_existe=False,
        dentro_prazo_recurso=True,
        documentacao_recurso_completa=True,
    )
    fake = _recurso_admissibility_fake(
        roteamento="ANALISE_HUMANA",
        motivo=(
            "Glosa referida nao confirmada/ativa em CONTAS — analise humana obrigatoria "
            "(R5; nunca inadmissibilidade automatica)"
        ),
    )
    result = assess_eligibility(inp, validation, dmn=fake)
    assert result.roteamento == "ANALISE_HUMANA"
    assert "nao confirmada" in result.motivo
    # Terminal admissibility state (not SEGUE_ANALISE) — recurso_eligibility is NOT consulted.
    assert fake.calls == [
        (
            "recurso_admissibility",
            {
                "glosa_existe": False,
                "dentro_prazo_recurso": True,
                "documentacao_recurso_completa": True,
            },
        )
    ]


def test_assess_eligibility_tecnica_clinica_routes_auditor() -> None:
    """Glosa tecnica/clinica -> grupo_revisor = medico-auditor (via recurso_eligibility chain)."""
    inp = RecursoInput(
        tenant_id="amh",
        glosa_id="GLOSA-001",
        glosa_type="clinica",
    )
    from maezo.tools.workers.recurso import RecursoValidationResult

    validation = RecursoValidationResult(
        valid=True,
        glosa_existe=True,
        dentro_prazo_recurso=True,
        documentacao_recurso_completa=True,
    )
    fake = _recurso_admissibility_and_eligibility_fake(
        elig_roteamento="SEGUE_MERITO", grupo_revisor="medico-auditor"
    )
    result = assess_eligibility(inp, validation, dmn=fake)
    assert result.grupo_revisor == "medico-auditor"


def test_assess_eligibility_segue_analise() -> None:
    """All conditions met -> SEGUE_ANALISE + segue_merito derived from recurso_eligibility."""
    inp = RecursoInput(
        tenant_id="amh",
        glosa_id="GLOSA-001",
        glosa_type="administrativa",
    )
    from maezo.tools.workers.recurso import RecursoValidationResult

    validation = RecursoValidationResult(
        valid=True,
        glosa_existe=True,
        dentro_prazo_recurso=True,
        documentacao_recurso_completa=True,
    )
    fake = _recurso_admissibility_and_eligibility_fake(
        elig_roteamento="SEGUE_MERITO", grupo_revisor="analista-recurso-glosa"
    )
    result = assess_eligibility(inp, validation, dmn=fake)
    assert result.roteamento == "SEGUE_ANALISE"
    assert result.segue_merito is True
    assert fake.calls == [
        (
            "recurso_admissibility",
            {
                "glosa_existe": True,
                "dentro_prazo_recurso": True,
                "documentacao_recurso_completa": True,
            },
        ),
        (
            "recurso_eligibility",
            {"glosa_type": "administrativa", "glosa_reason_code": "", "valor_glosado_brl": 0.0},
        ),
    ]


def test_assess_eligibility_pendente_documentacao() -> None:
    """Missing documents -> PENDENTE_DOCUMENTACAO (terminal — eligibility not consulted)."""
    inp = RecursoInput(tenant_id="amh", glosa_id="GLOSA-001")
    from maezo.tools.workers.recurso import RecursoValidationResult

    validation = RecursoValidationResult(
        valid=True,
        glosa_existe=True,
        dentro_prazo_recurso=True,
        documentacao_recurso_completa=False,
    )
    fake = _recurso_admissibility_fake(roteamento="PENDENTE_DOCUMENTACAO")
    result = assess_eligibility(inp, validation, dmn=fake)
    assert result.roteamento == "PENDENTE_DOCUMENTACAO"
    assert len(fake.calls) == 1  # eligibility NOT consulted for a terminal admissibility state


def test_assess_eligibility_unmapped_glosa_type_nao_segue_merito() -> None:
    """golden-parity finding (T1.5, live-verified): recurso_eligibility's catch-all (unmapped
    glosa_type) returns ANALISE_HUMANA, not SEGUE_MERITO — the old Python's hand-coded
    tecnica/clinica-only split never modeled this third possibility (it derived the flag purely
    from admissibility, never from the real eligibility table)."""
    inp = RecursoInput(tenant_id="amh", glosa_id="GLOSA-001", glosa_type="outra")
    from maezo.tools.workers.recurso import RecursoValidationResult

    validation = RecursoValidationResult(
        valid=True,
        glosa_existe=True,
        dentro_prazo_recurso=True,
        documentacao_recurso_completa=True,
    )
    fake = _recurso_admissibility_and_eligibility_fake(
        elig_roteamento="ANALISE_HUMANA", grupo_revisor="analista-recurso-glosa"
    )
    result = assess_eligibility(inp, validation, dmn=fake)
    assert result.roteamento == "SEGUE_ANALISE"  # admissibility still says proceed
    assert result.segue_merito is False  # but eligibility's own signal says: human decides


def test_assess_eligibility_dmn_unwired_raises_dmn_evaluation_error() -> None:
    from maezo.tools.workers.recurso import RecursoValidationResult

    inp = RecursoInput(tenant_id="amh", glosa_id="GLOSA-001")
    validation = RecursoValidationResult(glosa_existe=True)
    with pytest.raises(DmnEvaluationError):
        assess_eligibility(inp, validation, dmn=None)


# ---------------------------------------------------------------------------
# analyze_merits
# ---------------------------------------------------------------------------


def test_analyze_merits_never_decides() -> None:
    """analyze_merits returns dossie instructions, never decides."""
    inp = RecursoInput(
        tenant_id="amh",
        glosa_id="GLOSA-001",
        glosa_type="administrativa",
        valor_glosado_brl=500.0,
        codigo_procedimento_tuss="10101012",
    )
    result = analyze_merits(inp)
    assert result["glosa_id"] == "GLOSA-001"
    assert result["merito_sugerido"] == "ANALISE_HUMANA"


# ---------------------------------------------------------------------------
# prepare_dossier
# ---------------------------------------------------------------------------


def test_prepare_dossier() -> None:
    """prepare_dossier combines validation and merits."""
    from maezo.tools.workers.recurso import RecursoValidationResult

    validation = RecursoValidationResult(
        valid=True,
        glosa_existe=True,
        dentro_prazo_recurso=True,
        documentacao_recurso_completa=True,
    )
    merits = {"analise": "dossie_instruido"}
    result = prepare_dossier(validation, merits)
    assert "dossier" in result
    assert result["dossier"]["validacao"]["glosa_existe"] is True


# ---------------------------------------------------------------------------
# request_documents (GAP-RECURSO-5 / FAB-NOTIFIED-TRIO: was `notify_prestador`, which returned an
# unconditional `notified=True` with no channel; now returns `{}` and asserts nothing)
# ---------------------------------------------------------------------------


def test_request_documents_nao_afirma_notificacao() -> None:
    """The pendency STEP returns `{}` — never `notified=True`.

    The old `notify_prestador` returned `{"notified": True, "prestador_id": ..., "glosa_id": ...,
    "message_type": ...}` on every delivery, with no channel contacted and no delivery observed.
    The harness loads a handler's return into process scope on `complete`
    (`harness.py:1778-1782`), so that constant became an audit-trail claim inside the instance.
    """
    assert request_documents("PREST-001", "GLOSA-001", message_type="pendencia_documentacao") == {}


@pytest.mark.parametrize(
    "prestador_id,glosa_id,message_type",
    [
        ("PREST-001", "GLOSA-001", "pendencia_documentacao"),
        ("", "", ""),
        ("P-2", "G-2", "outro_tipo"),
    ],
)
def test_request_documents_nenhuma_entrada_produz_afirmacao(
    prestador_id: str, glosa_id: str, message_type: str
) -> None:
    """No input shape may produce a `notified` claim (or any other key)."""
    out = request_documents(prestador_id, glosa_id, message_type)
    assert out == {}
    assert "notified" not in out


def test_request_documents_registra_a_etapa_sem_afirmar_entrega() -> None:
    """Observability survives the fix: the step still logs, and the log states plainly that no
    notification fact was asserted (so a reader of the trail cannot infer one)."""
    with structlog.testing.capture_logs() as logs:
        request_documents("PREST-001", "GLOSA-001")
    events = [entry for entry in logs if entry.get("event") == "recurso.request_documents"]
    assert events, "a etapa TEM de continuar observavel no log"
    assert events[0]["notified_asserted"] is False


# ---------------------------------------------------------------------------
# escalate_to_junta
# ---------------------------------------------------------------------------


def test_escalate_to_junta() -> None:
    """escalate_to_junta routes to medico-auditor."""
    result = escalate_to_junta("GLOSA-001", motivo="Glosa clinica complexa", glosa_type="clinica")
    assert result["escalated"] is True
    assert result["grupo"] == "medico-auditor"


# ---------------------------------------------------------------------------
# registrar_indeferimento — GUARD tests (ERR_RECURSO_INDEFERIMENTO_NOT_HUMAN)
# ---------------------------------------------------------------------------


def _indeferimento_input(**over: object) -> RecursoIndeferimentoInput:
    base: dict[str, object] = {
        "tenant_id": "amh",
        "numero_guia_tiss": "GUIA-1",
        "glosa_id": "GLOSA-1",
        "decisao_recurso": "INDEFERIR",
        "fundamentacao_indeferimento": "Sem elementos novos que afastem a glosa",
        "valor_glosa_mantido_brl": 100.0,
        "referencia_contratual": "CLAUSULA-1",
        "analista_id": "analista-1",
    }
    base.update(over)
    return RecursoIndeferimentoInput(**base)  # type: ignore[arg-type]


def test_indeferimento_guard_missing_decisao() -> None:
    """registrar_indeferimento raises if decisao_recurso is not an adverse human decision."""
    with pytest.raises(RecursoIndeferimentoNotHumanError) as exc:
        registrar_indeferimento(_indeferimento_input(decisao_recurso="SOLICITAR_INFO"))
    assert "decisao_recurso" in str(exc.value)


def test_indeferimento_guard_recusa_decisao_deferir() -> None:
    """DEFERIR is FAVOURABLE — it must never reach the adverse-effect worker. The gateway routes
    it to `ST_ComunicarDeferimento`; a forged direct dispatch of this topic refuses."""
    with pytest.raises(RecursoIndeferimentoNotHumanError) as exc:
        registrar_indeferimento(_indeferimento_input(decisao_recurso="DEFERIR"))
    assert "ERR_RECURSO_INDEFERIMENTO_NOT_HUMAN" in str(exc.value)


def test_indeferimento_guard_missing_fundamentacao() -> None:
    with pytest.raises(RecursoIndeferimentoNotHumanError) as exc:
        registrar_indeferimento(_indeferimento_input(fundamentacao_indeferimento=""))
    assert "fundamentacao_indeferimento" in str(exc.value)


def test_indeferimento_guard_missing_referencia() -> None:
    with pytest.raises(RecursoIndeferimentoNotHumanError) as exc:
        registrar_indeferimento(_indeferimento_input(referencia_contratual=""))
    assert "referencia_contratual" in str(exc.value)


def test_indeferimento_guard_missing_analista() -> None:
    with pytest.raises(RecursoIndeferimentoNotHumanError) as exc:
        registrar_indeferimento(_indeferimento_input(analista_id=""))
    assert "analista_id" in str(exc.value)


@pytest.mark.parametrize("valor", ["", "   ", "nao-numerico", 0.0, -1.0, None, True])
def test_indeferimento_guard_valor_mantido_fail_closed(valor: object) -> None:
    """`_parse_valor_monetario` NEVER defaults to 0 — a wrong monetary decision on a glosa is a
    financial defect, so an unusable amount is treated as a MISSING required field."""
    with pytest.raises(RecursoIndeferimentoNotHumanError) as exc:
        registrar_indeferimento(_indeferimento_input(valor_glosa_mantido_brl=valor))
    assert "valor_glosa_mantido_brl" in str(exc.value)


def test_indeferimento_success() -> None:
    """registrar_indeferimento succeeds with all required fields, minting a DETERMINISTIC
    protocolo from the instance's own business identity (no wall clock, no uuid)."""
    result = registrar_indeferimento(_indeferimento_input())
    assert result.registered is True
    assert result.protocolo == "RECIND-RECURSO-amh-GUIA-1-GLOSA-1"
    assert result.protocolo == registrar_indeferimento(_indeferimento_input()).protocolo


def test_indeferimento_aceita_deferir_parcial() -> None:
    """DEFERIR_PARCIAL is L0 adverse (part of the glosa is MAINTAINED against the prestador) —
    same guarded worker, with `valor_deferido_brl` additionally required."""
    result = registrar_indeferimento(
        _indeferimento_input(
            decisao_recurso="DEFERIR_PARCIAL",
            valor_glosa_mantido_brl=40.0,
            valor_deferido_brl=60.0,
            valor_glosado_brl=100.0,
        )
    )
    assert result.registered is True


def test_indeferimento_deferir_parcial_exige_valor_deferido() -> None:
    with pytest.raises(RecursoIndeferimentoNotHumanError) as exc:
        registrar_indeferimento(
            _indeferimento_input(
                decisao_recurso="DEFERIR_PARCIAL",
                valor_glosa_mantido_brl=40.0,
                valor_deferido_brl="",
                valor_glosado_brl=100.0,
            )
        )
    assert "valor_deferido_brl" in str(exc.value)


def test_indeferimento_deferir_parcial_soma_confere() -> None:
    """`valor_deferido + valor_glosa_mantido == valor_glosado`, in INTEGER CENTS with exact
    equality. Pure arithmetic, not a business rule: comparing floats would fail spuriously on
    exact amounts (0.1 + 0.2 != 0.3). A sum that does not close REFUSES — the worker never rounds
    on its own authority (OQ-R2: if the operation uses a tolerance, that is an SME decision)."""
    # Closes exactly at a value where float arithmetic is treacherous (0.1 + 0.2).
    ok = registrar_indeferimento(
        _indeferimento_input(
            decisao_recurso="DEFERIR_PARCIAL",
            valor_deferido_brl=0.1,
            valor_glosa_mantido_brl=0.2,
            valor_glosado_brl=0.3,
        )
    )
    assert ok.registered is True

    with pytest.raises(RecursoIndeferimentoNotHumanError) as exc:
        registrar_indeferimento(
            _indeferimento_input(
                decisao_recurso="DEFERIR_PARCIAL",
                valor_deferido_brl=60.0,
                valor_glosa_mantido_brl=41.0,  # one real off
                valor_glosado_brl=100.0,
            )
        )
    assert "soma nao fecha" in str(exc.value)


# ---------------------------------------------------------------------------
# registrar_indeferimento — R-155: exact integer-cent equality is a PERMANENT invariant
# ---------------------------------------------------------------------------
#
# Owner decision R-155 of 2026-09-04 closed OQ-R2: *"Fechar a pergunta declarando a igualdade
# exata em centavos-inteiros como invariante PERMANENTE do guard de `registrar_indeferimento`
# (nao como default provisorio): qualquer tolerancia futura entra depois como regra nova assinada
# por financas, em PR proprio, e o sign-off de SP-OP-RECURSO-001 deixa de esperar por ela."*
#
# What these tests pin is the SHAPE of the comparison, because that is what a future edit would
# quietly change. Before them, `test_indeferimento_deferir_parcial_soma_confere` above proved the
# guard refuses a sum off by ONE REAL — so a guard rewritten with a tolerance of a few centavos
# stayed green (measured: injecting `abs(...) > 1` left all 144 tests of this file passing).
# Three mutation classes must now go RED: a float comparison, ANY tolerance, and a comparison
# that is not at integer-cent grain.


def _parcial(**over: object) -> RecursoIndeferimentoInput:
    base: dict[str, object] = {
        "decisao_recurso": "DEFERIR_PARCIAL",
        "valor_deferido_brl": 60.0,
        "valor_glosa_mantido_brl": 40.0,
        "valor_glosado_brl": 100.0,
    }
    base.update(over)
    return _indeferimento_input(**base)


@pytest.mark.parametrize("delta_centavos", [1, -1, 2, -2, 7, -7, 50, -50, 99, -99])
def test_indeferimento_soma_nao_fecha_recusa_sem_qualquer_tolerancia(delta_centavos: int) -> None:
    """NO tolerance, at any width. The smallest possible miss — ONE CENTAVO — refuses exactly like
    a miss of one real. This is the test that a `abs(soma - total) <= N` rewrite fails, for every
    N >= 1; loosening the guard is a finanças-signed rule in its own PR (R-155), never an edit."""
    with pytest.raises(RecursoIndeferimentoNotHumanError) as exc:
        registrar_indeferimento(_parcial(valor_deferido_brl=60.0 + delta_centavos / 100))
    assert "soma nao fecha" in str(exc.value)


def test_indeferimento_um_centavo_a_menos_e_um_centavo_a_mais_recusam_igual() -> None:
    """Symmetry: the guard has no favoured direction — it does not round toward the operadora on
    a short sum nor toward the prestador on a long one."""
    for valor_mantido in (39.99, 40.01):
        with pytest.raises(RecursoIndeferimentoNotHumanError) as exc:
            registrar_indeferimento(_parcial(valor_glosa_mantido_brl=valor_mantido))
        assert "soma nao fecha" in str(exc.value)
    assert registrar_indeferimento(_parcial(valor_glosa_mantido_brl=40.0)).registered is True


@pytest.mark.parametrize(
    ("deferido", "mantido", "glosado"),
    [
        (0.1, 0.2, 0.3),  # 0.1 + 0.2 == 0.30000000000000004 as floats
        (1.1, 2.2, 3.3),  # 3.3000000000000003
        (0.1, 0.7, 0.8),  # 0.7999999999999999
        (100.1, 200.2, 300.3),  # 300.29999999999995
    ],
)
def test_indeferimento_a_comparacao_e_em_centavos_inteiros_nao_em_float(
    deferido: float, mantido: float, glosado: float
) -> None:
    """A sum that closes EXACTLY in centavos is accepted even where binary floats disagree with
    decimal arithmetic. A guard rewritten to compare the floats directly refuses every row here —
    it would reject correct money because of a representation artefact."""
    assert deferido + mantido != glosado, "row nao exercita a divergencia float (revise o caso)"
    assert (
        registrar_indeferimento(
            _parcial(valor_deferido_brl=deferido, valor_glosa_mantido_brl=mantido, valor_glosado_brl=glosado)
        ).registered
        is True
    )


def test_indeferimento_o_grao_da_igualdade_e_o_centavo_inteiro() -> None:
    """DECLARED LIMIT, part 1 — ONE operand's residue. Each operand is converted to centavos by
    `_to_cents` BEFORE the comparison, so a sub-centavo residue in an input is resolved by that
    conversion and not by slack in the comparison: the comparison itself has none, which is why a
    miss of a whole centavo IN THE CONVERTED VALUE refuses. What this case does NOT show is that
    the three operands are converted independently, so their residues compound — that is part 2,
    `test_indeferimento_o_residuo_agregado_dos_tres_operandos_chega_a_um_centavo_e_meio`, and it
    is the case that bounds what the guard really absorbs. Any rule about sub-centavo amounts is a
    new finanças-signed rule in its own PR (R-155), which is why this behaviour is pinned rather
    than changed here."""
    # 60,004 + 40,00 == 100,00 at centavo grain (6000 + 4000 == 10000).
    assert (
        registrar_indeferimento(
            _parcial(valor_deferido_brl=60.004, valor_glosa_mantido_brl=40.0, valor_glosado_brl=100.0)
        ).registered
        is True
    )
    # ...and the very next centavo does not close.
    with pytest.raises(RecursoIndeferimentoNotHumanError) as exc:
        registrar_indeferimento(
            _parcial(valor_deferido_brl=60.004, valor_glosa_mantido_brl=40.0, valor_glosado_brl=100.01)
        )
    assert "soma nao fecha" in str(exc.value)


def _residuo_centavos(deferido: float, mantido: float, glosado: float) -> Decimal:
    """Discrepância REAL da trinca, em centavos, calculada em decimal exato (nunca em float) a
    partir do texto do número — é o valor que o guard absorve, não o que ele compara."""
    return (Decimal(str(deferido)) + Decimal(str(mantido)) - Decimal(str(glosado))) * 100


@pytest.mark.parametrize(
    ("deferido", "mantido", "glosado", "residuo"),
    [
        (60.0049, 40.0049, 99.9951, Decimal("1.47")),
        (60.005, 40.0049, 99.995, Decimal("1.49")),
        (0.005, 0.025, 0.015, Decimal("1.50")),
    ],
)
def test_indeferimento_o_residuo_agregado_dos_tres_operandos_chega_a_um_centavo_e_meio(
    deferido: float, mantido: float, glosado: float, residuo: Decimal
) -> None:
    """DECLARED LIMIT, part 2 — the bound the operator is entitled to know. `_to_cents` quantises
    each of the THREE operands INDEPENDENTLY, so the residues compound on opposite sides of the
    comparison: a real discrepancy of up to 1,5 centavo (3 × meio centavo) closes the guard. The
    third row ATTAINS the ceiling — `round`'s banker's tie-breaking makes 1,50 exact, not merely
    approached. Nothing here changes behaviour; it makes the true bound visible, so replacing
    `_to_cents`'s rounding mode (truncation, `Decimal`, half-up) cannot pass silently."""
    assert _residuo_centavos(deferido, mantido, glosado) == residuo, "linha nao exercita o residuo declarado"
    assert (
        registrar_indeferimento(
            _parcial(valor_deferido_brl=deferido, valor_glosa_mantido_brl=mantido, valor_glosado_brl=glosado)
        ).registered
        is True
    )


@pytest.mark.parametrize(
    ("deferido", "mantido", "glosado"),
    [
        (60.0049, 40.0049, 99.9931),  # 1,67 centavo
        (60.006, 40.006, 99.99),  # 2,20 centavos
    ],
)
def test_indeferimento_residuo_acima_do_teto_de_um_centavo_e_meio_recusa(
    deferido: float, mantido: float, glosado: float
) -> None:
    """The other side of the same bound: above 1,5 centavo the compounded residues can no longer
    make the cents agree, and the guard refuses. Without this the ceiling would be an upper claim
    with nothing on the far side of it."""
    assert _residuo_centavos(deferido, mantido, glosado) > Decimal("1.5"), "a linha nao passa do teto"
    with pytest.raises(RecursoIndeferimentoNotHumanError) as exc:
        registrar_indeferimento(
            _parcial(valor_deferido_brl=deferido, valor_glosa_mantido_brl=mantido, valor_glosado_brl=glosado)
        )
    assert "soma nao fecha" in str(exc.value)


def test_indeferimento_nenhuma_soma_aceita_passa_do_teto_de_um_centavo_e_meio() -> None:
    """Tree-free sweep over the sub-centavo neighbourhood of 60 + 40 == 100 (21³ = 9261 trincas,
    passo de 0,0005): every triple IN THIS SWEEP that the guard accepts has an aggregate residue
    of at most 1,5 centavo, and the sweep gets within 0,05 centavo of that ceiling. The ceiling
    itself is not an empirical guess — `round` is round-to-nearest, so each of the three operands
    differs from its value in centavos by at most half a centavo. The second assertion is what
    makes this non-vacuous — under a truncating `_to_cents` the accepted residues top out at 1,0
    centavo, so the fence goes RED on a change of rounding mode instead of quietly re-passing."""
    passo = Decimal("0.0005")
    deltas = [passo * i for i in range(-10, 11)]
    aceitas = 0
    pior = Decimal(0)
    for a in deltas:
        deferido = float(Decimal("60") + a)
        for b in deltas:
            mantido = float(Decimal("40") + b)
            for c in deltas:
                glosado = float(Decimal("100") + c)
                try:
                    registrar_indeferimento(
                        _parcial(
                            valor_deferido_brl=deferido,
                            valor_glosa_mantido_brl=mantido,
                            valor_glosado_brl=glosado,
                        )
                    )
                except RecursoIndeferimentoNotHumanError:
                    continue
                aceitas += 1
                pior = max(pior, abs(_residuo_centavos(deferido, mantido, glosado)))
    assert aceitas > 0, "a varredura nao aceitou nenhuma trinca (revise a vizinhanca)"
    assert pior <= Decimal("1.5"), f"trinca aceita com residuo acima do teto declarado: {pior}"
    assert pior >= Decimal("1.45"), f"a varredura nao chega perto do teto declarado: {pior}"


def test_a_recusa_declara_a_invariante_permanente_ao_operador() -> None:
    """The refusal message is the operator-facing declaration: it names the invariant and where a
    tolerance would have to come from. It must not advertise an OPEN SME question — R-155 closed
    OQ-R2, and the sign-off of SP-OP-RECURSO-001 no longer waits on it."""
    with pytest.raises(RecursoIndeferimentoNotHumanError) as exc:
        registrar_indeferimento(_parcial(valor_glosa_mantido_brl=41.0))
    mensagem = str(exc.value)
    assert "centavos-inteiros, igualdade exata" in mensagem
    assert "invariante permanente do guard" in mensagem
    assert "R-155" in mensagem
    assert "financas" in mensagem
    assert "OQ-R2" not in mensagem


# ---------------------------------------------------------------------------
# registrar_indeferimento — auditor channel (finding 4, t3.1-recurso-findings-a)
# ---------------------------------------------------------------------------


def test_indeferimento_guard_neither_channel() -> None:
    """Raises if NEITHER the analista nor the auditor channel matches."""
    with pytest.raises(RecursoIndeferimentoNotHumanError) as exc:
        registrar_indeferimento(
            _indeferimento_input(decisao_recurso="", decisao_auditor_recurso="", analista_id="")
        )
    assert "decisao_recurso" in str(exc.value)
    assert "decisao_auditor_recurso" in str(exc.value)


def test_indeferimento_guard_auditor_missing_auditor_id() -> None:
    """The auditor channel is attempted (INDEFERIR) but auditor_id is empty — same fail-closed
    shape as the analista channel's missing analista_id."""
    with pytest.raises(RecursoIndeferimentoNotHumanError) as exc:
        registrar_indeferimento(
            _indeferimento_input(
                decisao_recurso="",
                analista_id="",
                decisao_auditor_recurso="INDEFERIR",
                fundamentacao_indeferimento="Merito tecnico confirma a glosa",
                valor_glosa_mantido_brl=150.0,
                referencia_contratual="Diretriz DUT",
                auditor_id="",
            )
        )
    assert "ERR_RECURSO_INDEFERIMENTO_NOT_HUMAN" in str(exc.value)
    assert "auditor_id" in str(exc.value)
    assert "analista_id" not in str(exc.value), (
        "o canal auditor foi o atacado — a mensagem nao deve reclamar de analista_id"
    )


def test_indeferimento_success_auditor_channel() -> None:
    """Succeeds via the auditor channel (INDEFERIR + auditor_id) — ST_RegistrarIndeferimentoAuditor
    routes here on the SAME topic as the analista's INDEFERIR path."""
    result = registrar_indeferimento(
        _indeferimento_input(
            decisao_recurso="",
            analista_id="",
            decisao_auditor_recurso="INDEFERIR",
            fundamentacao_indeferimento="Merito tecnico-clinico confirma a glosa",
            valor_glosa_mantido_brl=150.0,
            referencia_contratual="Diretriz clinica DUT",
            auditor_id="auditor-1",
        )
    )
    assert result.registered is True
    assert result.protocolo.startswith("RECIND-")


# ---------------------------------------------------------------------------
# publish_completed
# ---------------------------------------------------------------------------


def test_publish_completed_recurso() -> None:
    """publish_completed emits domain event."""
    result = publish_completed(desfecho="deferido_humano")
    assert result["published"] is True
    assert result["payload"]["desfecho"] == "deferido_humano"


# ---------------------------------------------------------------------------
# Error types
# ---------------------------------------------------------------------------


def test_recurso_indeferimento_not_human_is_permission_error() -> None:
    """`RecursoIndeferimentoNotHumanError` must be a subclass of `PermissionError` — that is what
    makes `harness._handle` classify it as an AUDITED REFUSAL (ADR-0030 §4), and the errorCode's
    `_NOT_HUMAN` suffix is what `harness.is_guard_refusal_code` recognises."""
    assert issubclass(RecursoIndeferimentoNotHumanError, PermissionError)


# ---------------------------------------------------------------------------
# Dict-boundary entry functions (T1.2/ADR-0026 §2b) — round-trip vs calling the
# typed function directly; fail-closed marshalling on invalid/missing input.
# ---------------------------------------------------------------------------


def test_validate_recurso_entry_round_trips_validate_recurso() -> None:
    variables = {
        "glosa_id": "G-1",
        "glosa_existe": True,
        "dentro_prazo_recurso": True,
        "data_recebimento_recurso_iso": "2026-05-20",
    }
    direct = validate_recurso(RecursoInput(**variables))
    result = validate_recurso_entry(variables)
    assert result["valid"] == direct.valid
    assert result["glosa_existe"] == direct.glosa_existe
    assert result["data_recebimento_recurso_iso"] == "2026-05-20"
    # m8: ST_ValidarRecurso is the FIRST service task of EVERY path, so this is the first
    # completion payload the engine sees. No value in it may be a container.
    assert "errors" not in result
    assert result["errors_validacao"] == ""
    assert all(isinstance(v, str | bool) for v in result.values()), result


def test_validate_recurso_entry_escreve_de_volta_a_ancora_quando_ausente_do_start() -> None:
    """GAP-RECURSO-1, metade ENGINE-INDEPENDENTE de `test_prazo_max_ancora_defaultada_pelo_intake`.

    O cenario vivo remove `data_recebimento_recurso_iso` das variaveis de start (o fixture
    dropa `None`), e o BPMN nao tem mais fail-safe para a data do RECORRENTE: se o intake nao
    escrevesse a ancora de volta, `recurso_sla` receberia nulo e os tres boundary de teto
    (`timeDate ${sla.prazo_max_absoluto_iso}`) nao teriam instante — a instancia nem alcancaria
    a User Task. Aqui a chave esta AUSENTE do dict (nao vazia): `pick_fields` cai no default do
    dataclass, o worker defaulta fail-safe para HOJE/UTC com warning e a ancora SAI no payload
    de completion, que e como ela vira variavel de processo.

    A data alegada pelo prestador nao pode virar a ancora: e o relogio do recorrente, e a
    tempestividade continua sendo o fato separado `dentro_prazo_recurso`.
    """
    from datetime import UTC, datetime, timedelta  # noqa: PLC0415 — convencao local deste modulo

    hoje = datetime.now(UTC).strftime("%Y-%m-%d")
    ciencia = (datetime.now(UTC) - timedelta(days=5)).strftime("%Y-%m-%d")

    with structlog.testing.capture_logs() as logs:
        result = validate_recurso_entry(
            {
                "glosa_id": "G-1",
                "glosa_existe": True,
                "dentro_prazo_recurso": True,
                "data_ciencia_alegada_prestador": ciencia,
            }
        )

    assert result["data_recebimento_recurso_iso"] == hoje
    assert result["data_recebimento_recurso_iso"] != ciencia
    warnings = [
        entry for entry in logs if entry.get("event") == "recurso.validate_recurso.data_recebimento_defaulted"
    ]
    assert warnings, "o default fail-safe da ancora TEM de logar warning — nunca silencioso"
    assert warnings[0]["log_level"] == "warning"


def test_validate_recurso_entry_achata_erros_em_escalar() -> None:
    """m8: the diagnosis survives, as a SCALAR — never a `list[str]` process variable."""
    result = validate_recurso_entry({"glosa_id": "G-1", "glosa_existe": True, "dentro_prazo_recurso": False})
    assert result["errors_validacao"] == "fora do prazo recursal"
    assert not isinstance(result["errors_validacao"], list)


def test_request_documents_entry_aplica_o_default_pendencia_e_nao_afirma_nada() -> None:
    """The default message_type ("pendencia_documentacao") IS what makes this the
    request_documents topic's handler (see recurso.py bootstrap docstring) — it now reaches only
    the LOG, never the process scope, and the entry returns `{}` like the function it wraps.

    Asserted against the observable log line rather than against
    `request_documents(...) == request_documents_entry(...)`: with both sides `{}` an equality
    round-trip would be vacuous and would pass even if the entry stopped applying the default.
    """
    variables = {"prestador_id": "P-1", "glosa_id": "G-1"}
    assert request_documents_entry(variables) == {}


def test_request_documents_entry_default_message_type_chega_ao_log() -> None:
    """The `pendencia_documentacao` default is really applied by the entry (non-vacuous check)."""
    with structlog.testing.capture_logs() as logs:
        request_documents_entry({"prestador_id": "P-1", "glosa_id": "G-1"})
    events = [entry for entry in logs if entry.get("event") == "recurso.request_documents"]
    assert events and events[0]["message_type"] == "pendencia_documentacao"


def test_analyze_request_entry_round_trips_analyze_merits() -> None:
    variables = {"glosa_id": "G-1", "glosa_type": "tecnica", "valor_glosado_brl": 100.0}
    assert analyze_request_entry(variables) == analyze_merits(RecursoInput(**variables))


# ---------------------------------------------------------------------------
# GAP-RECURSO-3 / Finding 5 — ERR_RECURSO_INVALID_GLOSA guard (WorkerBpmnError, NOT ValueError)
# ---------------------------------------------------------------------------


def test_require_glosa_id_raises_worker_bpmn_error_when_absent() -> None:
    with pytest.raises(WorkerBpmnError) as exc:
        _require_glosa_id("")
    assert exc.value.error_code == "ERR_RECURSO_INVALID_GLOSA"


def test_require_glosa_id_raises_worker_bpmn_error_when_whitespace_only() -> None:
    with pytest.raises(WorkerBpmnError) as exc:
        _require_glosa_id("   ")
    assert exc.value.error_code == "ERR_RECURSO_INVALID_GLOSA"


def test_require_glosa_id_accepts_non_empty() -> None:
    _require_glosa_id("GLOSA-001")  # must not raise


def test_request_documents_entry_raises_before_notifying_prestador_when_glosa_id_absent() -> None:
    """The guard fires BEFORE `request_documents` — test-spec invariant
    (`test_glosa_id_ausente_pendencia_termina_limpo_sem_incidente_travado`: no
    `notifications_of_type("recurso.request_documents")` may be observed)."""
    with pytest.raises(WorkerBpmnError) as exc:
        request_documents_entry({"prestador_id": "P-1", "glosa_id": ""})
    assert exc.value.error_code == "ERR_RECURSO_INVALID_GLOSA"


def test_request_documents_entry_raises_when_glosa_id_missing_entirely() -> None:
    with pytest.raises(WorkerBpmnError):
        request_documents_entry({"prestador_id": "P-1"})


def test_analyze_request_entry_raises_before_analyzing_merits_when_glosa_id_absent() -> None:
    """Test-spec invariant (`test_glosa_id_ausente_analise_termina_limpo_sem_incidente_travado`):
    no `notifications_of_type("recurso.analyze_request")` may be observed."""
    with pytest.raises(WorkerBpmnError) as exc:
        analyze_request_entry({"glosa_id": "", "glosa_type": "tecnica"})
    assert exc.value.error_code == "ERR_RECURSO_INVALID_GLOSA"


def test_recurso_bpmn_error_allowlist_is_exactly_invalid_glosa() -> None:
    """`RECURSO_BPMN_ERROR_ALLOWLIST` is unioned into `worker_runtime/service.py`'s production
    allowlist (SHARED FILE) — pin its exact membership here so a drift is caught locally too."""
    assert frozenset({"ERR_RECURSO_INVALID_GLOSA"}) == RECURSO_BPMN_ERROR_ALLOWLIST


def test_registrar_indeferimento_entry_guards_missing_human_decision() -> None:
    """registrar_indeferimento_entry raises the UNCHANGED two-channel guard."""
    with pytest.raises(RecursoIndeferimentoNotHumanError):
        registrar_indeferimento_entry({"decisao_recurso": ""})


def test_registrar_indeferimento_entry_happy_path() -> None:
    variables = {
        "tenant_id": "amh",
        "numero_guia_tiss": "GUIA-1",
        "glosa_id": "GLOSA-1",
        "decisao_recurso": "INDEFERIR",
        "fundamentacao_indeferimento": "sem elementos novos",
        "valor_glosa_mantido_brl": 100.0,
        "referencia_contratual": "clausula-1",
        "analista_id": "analista-1",
    }
    direct = registrar_indeferimento(RecursoIndeferimentoInput(**variables))
    result = registrar_indeferimento_entry(variables)
    assert result["registered"] == direct.registered
    assert result["protocolo"] == direct.protocolo


def test_registrar_indeferimento_entry_happy_path_auditor_channel() -> None:
    """Round-trips the auditor channel (finding 4) — `pick_fields` picks up
    `decisao_auditor_recurso`/`auditor_id` off the flat BPMN variables dict."""
    variables = {
        "tenant_id": "amh",
        "numero_guia_tiss": "GUIA-1",
        "glosa_id": "GLOSA-1",
        "decisao_auditor_recurso": "INDEFERIR",
        "fundamentacao_indeferimento": "merito tecnico-clinico confirma a glosa",
        "valor_glosa_mantido_brl": 150.0,
        "referencia_contratual": "diretriz DUT",
        "auditor_id": "auditor-1",
    }
    direct = registrar_indeferimento(RecursoIndeferimentoInput(**variables))
    result = registrar_indeferimento_entry(variables)
    assert result["registered"] == direct.registered is True


def test_publish_completed_entry_round_trips_publish_completed() -> None:
    variables = {"event_type": "recurso.completed", "desfecho": "deferido_humano"}
    assert publish_completed_entry(variables) == publish_completed(
        event_type="recurso.completed", payload={}, desfecho="deferido_humano"
    )


# ---------------------------------------------------------------------------
# Raw-handler workers
# ---------------------------------------------------------------------------

# ----- notify_sla_risk (BT_AlertaSlaRecurso -> ST_NotificarRiscoSla) --------------------------


def test_notify_sla_risk_nao_afirma_notificacao() -> None:
    """FAB-SLA-RISK-NOTIFIED-SLICE4: a funcao pura retorna `{}` — NAO afirma nada.

    Ela nunca publicou: quem publica e' `make_notify_sla_risk_handler`. O `== {}` e' deliberado
    (nao `"sla_risk_notified" not in result`): so a igualdade exata pega uma fabricacao remontada
    chave-a-chave num local, que a cerca AST documenta nao alcancar.
    """
    inp = NotifySlaRiskInput(tenant_id="amh", numero_guia_tiss="G-1", glosa_id="GLOSA-1")
    assert notify_sla_risk(inp) == {}


@pytest.mark.parametrize(
    "inp",
    [
        NotifySlaRiskInput(),
        NotifySlaRiskInput(glosa_id="GLOSA-1"),
        NotifySlaRiskInput(tenant_id="amh", glosa_id="GLOSA-1", glosa_type="administrativa"),
    ],
)
def test_notify_sla_risk_nenhuma_entrada_produz_afirmacao(inp: NotifySlaRiskInput) -> None:
    """Entrada em branco tambem completa (informacional, nao adversa) e nada afirma."""
    assert notify_sla_risk(inp) == {}


async def test_make_notify_sla_risk_handler_publishes_notification() -> None:
    kafka = FakeKafkaPublisher()
    handler = make_notify_sla_risk_handler(kafka)
    task = _task(
        topic="operadora.recurso.notify_sla_risk",
        variables={
            "tenant_id": "amh",
            "numero_guia_tiss": "G-1",
            "glosa_id": "GLOSA-1",
            "glosa_type": "administrativa",
        },
    )
    result = await handler(task)
    # FAB-SLA-RISK-NOTIFIED-SLICE4: mesmo COM publish, o retorno nao afirma notificacao — o
    # registro interno e' um PEDIDO de alerta, nunca a prova de que a coordenacao foi avisada.
    # O publish em si (topico, payload, chave, best_effort) segue pinado byte-a-byte abaixo.
    assert result == {}
    assert len(kafka.published) == 1
    topic, payload, key = kafka.published[0]
    assert topic == "operadora.notifications.internal"
    assert payload["type"] == "recurso.notify_sla_risk"
    assert payload["glosa_id"] == "GLOSA-1"
    assert key == task.business_key
    # NON-HOLLOW (t2-notify-integrity item 1): forced propagate-on-failure — the coordenacao
    # alert is this task's only effect; a swallowed failure would fabricate success.
    assert kafka.best_effort_calls == [False]


class _FailingPublisher:
    """Raising `KafkaPublisher` double (records the posture) — for the raw-propagate pins."""

    def __init__(self) -> None:
        self.best_effort_calls: list[bool | None] = []

    async def publish(
        self,
        topic: str,
        value: dict,
        *,
        key: str | None = None,
        best_effort: bool | None = None,
    ) -> bool:
        del topic, value, key
        self.best_effort_calls.append(best_effort)
        raise RuntimeError("kafka unavailable (test)")


async def test_make_notify_sla_risk_handler_publish_failure_propagates_raw() -> None:
    """No BPMN boundary on ST_NotificarRiscoSla -> raw propagate (harness retry/incident,
    ADR-0030); contained to the non-interrupting BT_AlertaSlaRecurso side branch."""
    kafka = _FailingPublisher()
    handler = make_notify_sla_risk_handler(kafka)
    task = _task(
        topic="operadora.recurso.notify_sla_risk",
        variables={"tenant_id": "amh", "glosa_id": "GLOSA-1"},
    )
    with pytest.raises(RuntimeError, match="kafka unavailable"):
        await handler(task)
    assert kafka.best_effort_calls == [False]


async def test_make_notify_sla_risk_handler_fail_closed_default_no_producer() -> None:
    """kafka=None: completa mesmo assim (task informativa nao pode travar o ramo do timer) e NAO
    fabrica publish nenhum.

    FAB-SLA-RISK-NOTIFIED-SLICE4 — era exatamente AQUI que a fabricacao doia: o wrapper devolvia
    `{"sla_risk_notified": True, ...}` no caminho SEM produtor, isto e', afirmava a notificacao
    justamente quando nada tinha sido publicado. Agora `{}`.
    """
    handler = make_notify_sla_risk_handler(None)
    result = await handler(_task(variables={"glosa_id": "GLOSA-1"}))
    assert result == {}


# ----- escalate_ans_timeout (BT_PrazoMax* -> ST_EscalateAnsTimeout) ---------------------------


def test_escalate_ans_timeout_happy_path() -> None:
    inp = EscalateAnsTimeoutInput(tenant_id="amh", numero_guia_tiss="G-1", glosa_id="GLOSA-1")
    result = escalate_ans_timeout(inp)
    assert result["escalated"] is True
    assert result["fase"] == "prazo_max"


def test_escalate_ans_timeout_never_touches_ans_gateway() -> None:
    """'ANS' in the topic name is HISTORICAL — grep-proof no import of ans_gateway anywhere in
    recurso.py (AST-based: prose mentions in docstrings/comments don't count, only real
    `import`/`from ... import` statements)."""
    import ast

    import maezo.tools.workers.recurso as recurso_module

    with open(recurso_module.__file__, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    imported_roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_roots.add(node.module.split(".")[0])
            imported_roots.update(alias.name for alias in node.names)
    assert "ans_gateway" not in imported_roots


async def test_make_escalate_ans_timeout_handler_publishes_domain_event_prazo_max() -> None:
    """has_event(_RECURSO_SLA_BREACHED, fase="prazo_max") — a REAL domain event (not merely an
    internal notification), matching the BPMN's own embedded event_topic_breach inputParameter."""
    kafka = FakeKafkaPublisher()
    handler = make_escalate_ans_timeout_handler(kafka)
    task = _task(
        topic="operadora.recurso.escalate_ans_timeout",
        variables={
            "tenant_id": "amh",
            "numero_guia_tiss": "G-1",
            "glosa_id": "GLOSA-1",
            "glosa_type": "administrativa",
            "event_topic_breach": "agents.events.recurso.sla_breached",
        },
    )
    result = await handler(task)
    assert result["escalated"] is True
    assert len(kafka.published) == 1
    topic, payload, key = kafka.published[0]
    assert topic == "agents.events.recurso.sla_breached"
    assert payload["fase"] == "prazo_max"
    assert payload["glosa_id"] == "GLOSA-1"
    assert key == task.business_key


async def test_make_escalate_ans_timeout_handler_defaults_event_topic_when_absent() -> None:
    """Defensive default when `event_topic_breach` is missing from variables (should never
    happen against the real BPMN, which always carries it)."""
    kafka = FakeKafkaPublisher()
    handler = make_escalate_ans_timeout_handler(kafka)
    await handler(_task(variables={"glosa_id": "GLOSA-1"}))
    topic, _payload, _key = kafka.published[0]
    assert topic == "agents.events.recurso.sla_breached"


async def test_make_escalate_ans_timeout_handler_fail_closed_default_no_producer() -> None:
    handler = make_escalate_ans_timeout_handler(None)
    result = await handler(_task(variables={"glosa_id": "GLOSA-1"}))
    assert result["escalated"] is True


# ----- comunicar_resposta (the 5 terminal ST_Comunicar* tasks) --------------------------------


def test_comunicar_resposta_protocolo_is_deterministic_by_business_key() -> None:
    """ADR-0030/T-H determinism: the SAME business key always mints the IDENTICAL protocol — no
    time_ns/uuid/random (mirrors LabeledMockAnsGatewayTransport's MOCK-...-{business_key}). This
    is the ONE idiom that survives from the deleted appellant branch, because it is a real
    retry-idempotency fix, not vocabulary."""
    bk = "RECURSO-amh-GUIA-1-GLOSA-1"
    first = _mint_protocolo_resposta(bk, "amh", "GUIA-1", "GLOSA-1")
    second = _mint_protocolo_resposta(bk, "amh", "GUIA-1", "GLOSA-1")
    assert first == second
    assert first == f"RECRESP-{bk}"


def test_comunicar_resposta_protocolo_differs_by_business_key() -> None:
    a = _mint_protocolo_resposta("RECURSO-amh-G-1-X", "amh", "G-1", "X")
    b = _mint_protocolo_resposta("RECURSO-amh-G-2-Y", "amh", "G-2", "Y")
    assert a != b


def test_comunicar_resposta_protocolo_falls_back_to_composed_key_when_blank() -> None:
    """Defensive fallback (production always carries business_key) — still deterministic."""
    result = _mint_protocolo_resposta("", "amh", "GUIA-1", "GLOSA-1")
    assert result == "RECRESP-RECURSO-amh-GUIA-1-GLOSA-1"
    assert result == _mint_protocolo_resposta("  ", "amh", "GUIA-1", "GLOSA-1")


def test_comunicar_resposta_protocolo_never_uses_time_or_random() -> None:
    """Static proof (independent of the shared arch-test baseline fence) that THIS specific
    function's BODY (not its docstring prose) contains no nondeterministic call — AST-based,
    mirrors test_worker_handler_purity.py's `_nondeterministic_calls` detector."""
    import ast
    import inspect

    nondet_dotted = {"time.time_ns", "time.time", "uuid.uuid4", "uuid.uuid1", "uuid.uuid3", "uuid.uuid5"}
    nondet_roots = {"random", "secrets", "os"}

    tree = ast.parse(inspect.getsource(_mint_protocolo_resposta))
    hits: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            dotted = f"{node.value.id}.{node.attr}"
            if dotted in nondet_dotted or node.value.id in nondet_roots:
                hits.add(dotted)
    assert not hits, f"nondeterministic call(s) found in _mint_protocolo_resposta: {hits}"


def test_comunicar_resposta_serve_os_quatro_desfechos() -> None:
    """ONE worker serves every terminal that touches the prestador — the payer answers in ALL of
    them, favourable or adverse. The tipo is carried by the BPMN's own inputParameter; the worker
    never derives it (it holds no decision)."""
    seen = set()
    for tipo in sorted(COMUNICACAO_TIPOS):
        result = comunicar_resposta(
            ComunicarRespostaInput(
                tenant_id="amh",
                numero_guia_tiss="GUIA-1",
                glosa_id="GLOSA-1",
                prestador_id="PREST-1",
                tipo_comunicacao=tipo,
            ),
            business_key="RECURSO-amh-GUIA-1-GLOSA-1",
        )
        assert result["comunicado"] is True
        assert result["tipo_comunicacao"] == tipo
        assert result["protocolo_resposta_recurso"] == "RECRESP-RECURSO-amh-GUIA-1-GLOSA-1"
        seen.add(tipo)
    assert seen == set(COMUNICACAO_TIPOS)
    assert (
        frozenset({"deferimento", "deferimento_parcial", "indeferimento", "inadmissibilidade"})
        == COMUNICACAO_TIPOS
    )


async def test_make_comunicar_resposta_handler_publishes_notification_and_result() -> None:
    """`test_todos_os_fins_comunicam_o_prestador`'s worker-side half:
    notifications_of_type("recurso.comunicar_resposta") must be observable."""
    kafka = FakeKafkaPublisher()
    handler = make_comunicar_resposta_handler(kafka)
    task = _task(
        topic="operadora.recurso.comunicar_resposta",
        business_key="RECURSO-amh-G-1-GLOSA-1",
        variables={
            "tenant_id": "amh",
            "numero_guia_tiss": "G-1",
            "glosa_id": "GLOSA-1",
            "prestador_id": "PREST-1",
            "tipo_comunicacao": "indeferimento",
        },
    )
    result = await handler(task)
    assert result["protocolo_resposta_recurso"] == "RECRESP-RECURSO-amh-G-1-GLOSA-1"
    assert len(kafka.published) == 1
    topic, payload, key = kafka.published[0]
    assert topic == "operadora.notifications.internal"
    assert payload["type"] == "recurso.comunicar_resposta"
    assert payload["tipo_comunicacao"] == "indeferimento"
    assert payload["protocolo_resposta_recurso"] == result["protocolo_resposta_recurso"]
    assert key == task.business_key
    # NON-HOLLOW (t2-notify-integrity item 1): forced propagate-on-failure — the prestador is
    # ENTITLED to this answer; silent loss is unacceptable.
    assert kafka.best_effort_calls == [False]


async def test_make_comunicar_resposta_handler_publish_failure_propagates_raw() -> None:
    """No BPMN boundary on any ST_Comunicar* -> raw propagate (harness retry/incident, ADR-0030);
    re-dispatch republishes the IDENTICAL fact (deterministic protocolo minting)."""
    kafka = _FailingPublisher()
    handler = make_comunicar_resposta_handler(kafka)
    task = _task(
        topic="operadora.recurso.comunicar_resposta",
        business_key="RECURSO-amh-G-1-GLOSA-1",
        variables={"tenant_id": "amh", "numero_guia_tiss": "G-1", "glosa_id": "GLOSA-1"},
    )
    with pytest.raises(RuntimeError, match="kafka unavailable"):
        await handler(task)
    assert kafka.best_effort_calls == [False]


async def test_make_comunicar_resposta_handler_fail_closed_default_no_producer() -> None:
    handler = make_comunicar_resposta_handler(None)
    result = await handler(_task(variables={"glosa_id": "GLOSA-1"}))
    assert result["comunicado"] is True
    assert result["protocolo_resposta_recurso"]


# ----- handoff_pagamento (ST_HandoffPagamentoRecurso / ST_HandoffPagamentoParcial) ------------

_PAGTO_BK = "PAGTO-amh-GUIA-1-GLOSA-1"


def _handoff_vars(**over: object) -> dict[str, object]:
    base: dict[str, object] = {
        "tenant_id": "amh",
        "numero_guia_tiss": "GUIA-1",
        "glosa_id": "GLOSA-1",
        "numero_lote_tiss": "LOTE-9",
        "prestador_id": "PREST-1",
        "valor_deferido_brl": 150.0,
        "data_vencimento": "2026-06-30",
        "competencia": "2026-05",
        "conta_origem_ref": "CONTA-REF-1",
        "instrumento_pagamento": "pix",
        "fonte_valor": "deferido",
        "analista_id": "analista-1",
    }
    base.update(over)
    return base


def test_handoff_pagamento_glosa_revertida_inicia_pagto_idempotente() -> None:
    """The handoff REALLY starts SP-OP-PAGTO-001 through the fenced chokepoint: exact business
    key, `tipo_pagamento=glosa_revertida`, integer-cents value, and the exactly-once ADR-0007
    start record."""
    engine = FakeCibSevenTransport()
    sink = FakeStartAuditSink()
    result = handoff_pagamento(_handoff_vars(), engine=engine, audit_sink=sink)
    assert result["handoff"] == PAGTO_PROCESS_KEY
    assert result["handoff_executado"] is True
    assert result["pagto_business_key"] == _PAGTO_BK
    assert result["pagto_already_existed"] is False
    assert result["tipo_pagamento"] == "glosa_revertida"
    assert result["valor_pagamento_cents"] == 15000

    started = asyncio.run(engine.find_active_instance(_PAGTO_BK))
    assert started is not None and started.process_key == PAGTO_PROCESS_KEY
    payload = asyncio.run(engine.get_process_status(_PAGTO_BK)).variables
    assert payload["tipo_pagamento"] == "glosa_revertida"
    assert payload["valor_pagamento_cents"] == 15000
    assert payload["moeda"] == "BRL"
    assert payload["data_vencimento"] == "2026-06-30"

    assert sink.dedup_keys == [start_dedup_key("amh", PAGTO_PROCESS_KEY, _PAGTO_BK)]
    [record] = sink.records
    assert isinstance(record, AuditRecord)
    assert record.agent_id == AUDIT_AGENT_ID
    assert record.action == f"start_process:{PAGTO_PROCESS_KEY}"
    assert record.model_id is None and record.prompt_version is None
    assert record.details["lastro_origem"] == LASTRO_ORIGEM_RECURSO
    assert "glosa_id" not in record.details  # identifiers hash-bound only, never in the clear


def test_handoff_pagamento_idempotente_retorna_instancia_ativa() -> None:
    """A PAGTO-001 already active for this reverted glosa is returned unchanged — one order per
    reverted glosa, never a duplicate payment."""
    engine = FakeCibSevenTransport()
    sink = FakeStartAuditSink()
    engine.seed_instance(
        ProcessInstance(
            instance_id="pre-existing-pagto",
            process_key=PAGTO_PROCESS_KEY,
            business_key=_PAGTO_BK,
            state="ACTIVE",
            already_existed=True,
        )
    )
    result = handoff_pagamento(_handoff_vars(), engine=engine, audit_sink=sink)
    assert result["pagto_instance_id"] == "pre-existing-pagto"
    assert result["pagto_already_existed"] is True


def test_handoff_pagamento_semeia_exatamente_o_conjunto_declarado() -> None:
    """I-PAGTO-1 amarra 1: EQUALITY, never `issubset` — a new key must fail here, not surface in
    production as a skipped human admissibility review."""
    engine = FakeCibSevenTransport()
    handoff_pagamento(_handoff_vars(), engine=engine, audit_sink=FakeStartAuditSink())
    payload = asyncio.run(engine.get_process_status(_PAGTO_BK)).variables
    assert set(payload) == set(HANDOFF_PAGTO_SEEDED_KEYS)


def test_handoff_pagamento_nunca_semeia_os_fatos_de_admissibilidade_de_pagto() -> None:
    """I-PAGTO-1 amarra 2 (the negative, named): the four booleans PAGTO resolves for itself are
    NEVER seeded — not even when they arrive in RECURSO's own process scope. Seeding
    `lastro_confirmado=true` would drop the order straight into the clerical DENTRO_TETO_L2 lane
    (`ST_ReleaseLowValue`, no User Task) and skip the segregation of duties."""
    engine = FakeCibSevenTransport()
    hostile = _handoff_vars(
        lastro_confirmado=True,
        dados_pagamento_validos=True,
        duplicidade_suspeita=False,
        dentro_teto_l2=True,
    )
    handoff_pagamento(hostile, engine=engine, audit_sink=FakeStartAuditSink())
    payload = asyncio.run(engine.get_process_status(_PAGTO_BK)).variables
    for forbidden in HANDOFF_PAGTO_FORBIDDEN_KEYS:
        assert forbidden not in payload, f"{forbidden} vazou para PAGTO — I-PAGTO-1 violada"
    assert (
        frozenset({"lastro_confirmado", "dados_pagamento_validos", "duplicidade_suspeita", "dentro_teto_l2"})
        == HANDOFF_PAGTO_FORBIDDEN_KEYS
    )


def test_handoff_pagamento_semeia_lastro_origem_recurso_deferimento_humano() -> None:
    """EVIDENCE, not the fact: `lastro_origem` + `lastro_decisor_id` feed the human
    `UT_AnaliseAdmissibilidade` form. The decisor falls back to the auditor when the auditor
    channel granted the appeal."""
    engine = FakeCibSevenTransport()
    handoff_pagamento(_handoff_vars(), engine=engine, audit_sink=FakeStartAuditSink())
    payload = asyncio.run(engine.get_process_status(_PAGTO_BK)).variables
    assert payload["lastro_origem"] == "recurso_deferimento_humano"
    assert payload["lastro_decisor_id"] == "analista-1"

    engine2 = FakeCibSevenTransport()
    handoff_pagamento(
        _handoff_vars(analista_id="", auditor_id="auditor-1"),
        engine=engine2,
        audit_sink=FakeStartAuditSink(),
    )
    payload2 = asyncio.run(engine2.get_process_status(_PAGTO_BK)).variables
    assert payload2["lastro_decisor_id"] == "auditor-1"


def test_handoff_pagamento_declara_a_fonte_do_valor() -> None:
    """M7: every monetary origin is declared. `fonte_valor=deferido` says the amount came from
    the human's `valor_deferido_brl`, not from an inferred remainder."""
    engine = FakeCibSevenTransport()
    handoff_pagamento(_handoff_vars(), engine=engine, audit_sink=FakeStartAuditSink())
    payload = asyncio.run(engine.get_process_status(_PAGTO_BK)).variables
    assert payload["fonte_valor"] == "deferido"


@pytest.mark.parametrize("valor", ["", "   ", "nao-numerico", 0.0, -1.0, None, True])
def test_handoff_pagamento_recusa_valor_deferido_zero_ausente_ou_nao_numerico(valor: object) -> None:
    """`_parse_valor_monetario` NEVER defaults to 0 — even though `valor_deferido_brl` is a
    MANDATORY User Task field, the worker refuses rather than emitting an order for nothing."""
    engine = FakeCibSevenTransport()
    with pytest.raises(RecursoHandoffPagamentoInvalidoError) as exc:
        handoff_pagamento(
            _handoff_vars(valor_deferido_brl=valor), engine=engine, audit_sink=FakeStartAuditSink()
        )
    assert "valor_deferido_brl" in str(exc.value)
    assert asyncio.run(engine.find_active_instance(_PAGTO_BK)) is None


@pytest.mark.parametrize("vencimento", ["", "   ", None])
def test_handoff_pagamento_recusa_data_vencimento_em_branco(vencimento: object) -> None:
    """`data_vencimento` is mandatory in PAGTO and inherited from the intake envelope. Where it
    comes from for a REVERTED glosa is OQ-3 (DRAFT/verify, financas + juridico) — until that is
    settled the worker refuses instead of inventing a due date."""
    engine = FakeCibSevenTransport()
    with pytest.raises(RecursoHandoffPagamentoInvalidoError) as exc:
        handoff_pagamento(
            _handoff_vars(data_vencimento=vencimento), engine=engine, audit_sink=FakeStartAuditSink()
        )
    assert "data_vencimento" in str(exc.value)
    assert asyncio.run(engine.find_active_instance(_PAGTO_BK)) is None


@pytest.mark.parametrize(
    "over",
    [
        {"tenant_id": ""},
        {"tenant_id": "   "},
        {"tenant_id": None},
        {"numero_guia_tiss": ""},
        {"glosa_id": None},
    ],
)
def test_handoff_pagamento_recusa_ancora_degenerada(over: dict) -> None:
    """`non_blank` BEFORE `str()`: an explicit `None` must refuse, never stringify to the truthy
    "None" and mint `PAGTO-amh--None`."""
    engine = FakeCibSevenTransport()
    with pytest.raises(RecursoHandoffPagamentoInvalidoError):
        handoff_pagamento(_handoff_vars(**over), engine=engine, audit_sink=FakeStartAuditSink())


def test_handoff_pagamento_fail_closed_when_engine_seam_not_wired() -> None:
    with pytest.raises(RuntimeError, match="engine seam"):
        handoff_pagamento(_handoff_vars(), audit_sink=FakeStartAuditSink())


def test_handoff_pagamento_fail_closed_when_audit_sink_not_wired() -> None:
    with pytest.raises(RuntimeError, match="audit sink"):
        handoff_pagamento(_handoff_vars(), engine=FakeCibSevenTransport())


class _HashOnlySink:
    """A legacy sink implementing ONLY `emit_once` — it cannot report the dedup flag."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def emit_once(self, record: AuditRecord, *, dedup_key: str) -> str:
        self.calls.append(dedup_key)
        return "hash"


def test_handoff_pagamento_exige_seams_strict_de_dedup() -> None:
    """SP-OP-PAGTO-001 is the ONE STRICT start-dedup family. A sink that cannot report its
    durable claim makes the start un-gateable, and the chokepoint REFUSES rather than degrading
    to the TOCTOU-only path — which is exactly the duplicate-payment behaviour the gate exists to
    prevent. No engine effect happens."""
    engine = FakeCibSevenTransport()
    with pytest.raises(StartDedupGateUnavailableError):
        handoff_pagamento(_handoff_vars(), engine=engine, audit_sink=_HashOnlySink())  # type: ignore[arg-type]
    assert asyncio.run(engine.find_active_instance(_PAGTO_BK)) is None


def test_handoff_pagamento_entry_round_trips_and_never_calls_kafka() -> None:
    """kafka is accepted (donor contract) but never invoked — a real FakeKafkaPublisher must stay
    untouched (no `.published` entries); the handoff starts a process, it publishes nothing."""
    kafka = FakeKafkaPublisher()
    engine = FakeCibSevenTransport()
    result = handoff_pagamento_entry(
        _handoff_vars(), kafka=kafka, engine=engine, audit_sink=FakeStartAuditSink()
    )
    assert result["pagto_business_key"] == _PAGTO_BK
    assert kafka.published == []


def test_handoff_pagamento_converte_centavos_sem_truncar() -> None:
    """BRL -> integer cents by ROUNDING, not truncation: `1.15 * 100` is 114.999... in binary and
    truncating would shave a cent off a real payment."""
    engine = FakeCibSevenTransport()
    handoff_pagamento(
        _handoff_vars(valor_deferido_brl="1.15"), engine=engine, audit_sink=FakeStartAuditSink()
    )
    payload = asyncio.run(engine.get_process_status(_PAGTO_BK)).variables
    assert payload["valor_pagamento_cents"] == 115


# ----- M2: os segmentos da chave STRICT sao NORMALIZADOS -------------------------------------

#: As 6 formas da MESMA glosa revertida que o gatekeeper mintou como 6 chaves distintas
#: (VERIFY-PR3-RECURSO §4.4). `non_blank` recusa ausente/vazio/whitespace-only/`None`, mas ACEITA
#: whitespace-PADDED — e `str()` preserva o padding. Numa familia STRICT de dedup, uma chave a
#: mais e uma SEGUNDA ordem de pagamento para a mesma glosa.
_VARIANTES_DA_MESMA_GLOSA: tuple[dict[str, object], ...] = (
    {"tenant_id": "amh", "numero_guia_tiss": "GUIA-1", "glosa_id": "GLOSA-1"},
    {"tenant_id": "amh", "numero_guia_tiss": " GUIA-1 ", "glosa_id": "GLOSA-1"},
    {"tenant_id": "amh", "numero_guia_tiss": "GUIA-1", "glosa_id": "GLOSA-1\t"},
    {"tenant_id": " amh", "numero_guia_tiss": "GUIA-1", "glosa_id": "GLOSA-1"},
    {"tenant_id": "amh", "numero_guia_tiss": "GUIA-1", "glosa_id": "  GLOSA-1  "},
    {"tenant_id": "amh", "numero_guia_tiss": "GUIA-1\n", "glosa_id": "GLOSA-1"},
)


@pytest.mark.parametrize("variante", _VARIANTES_DA_MESMA_GLOSA)
def test_pagto_business_key_colapsa_as_seis_variantes_numa_chave(variante: dict) -> None:
    """M2: as 6 variantes da MESMA glosa revertida produzem UMA chave — a de referencia."""
    assert _pagto_business_key(**variante) == _PAGTO_BK  # type: ignore[arg-type]


@pytest.mark.parametrize("variante", _VARIANTES_DA_MESMA_GLOSA)
def test_ordem_pagamento_id_colapsa_as_seis_variantes_num_id(variante: dict) -> None:
    """A ordem que a chave carrega tem de concordar com ela sobre a identidade da glosa."""
    assert _ordem_pagamento_id(**variante) == "ORDEM-GLOSAREV-amh-GUIA-1-GLOSA-1"  # type: ignore[arg-type]


def test_as_seis_variantes_sao_realmente_distintas_sem_normalizacao() -> None:
    """CONTROLE NEGATIVO do teste acima: sem `key_segment`, a interpolacao crua mintaria 6 chaves
    diferentes. Se este teste ficar verde com 1 chave, o corpus perdeu o poder de discriminar."""
    cruas = {"PAGTO-{tenant_id}-{numero_guia_tiss}-{glosa_id}".format(**v) for v in _VARIANTES_DA_MESMA_GLOSA}
    assert len(cruas) == 6, cruas


@pytest.mark.parametrize("variante", _VARIANTES_DA_MESMA_GLOSA)
def test_pagto_business_key_usa_a_normalizacao_canonica_do_repo(variante: dict) -> None:
    """PINO DE IGUALDADE (M2): a normalizacao e a MESMA de `maezo.agents.andre.keys.key_segment`
    — o unico composer STRICT da familia PAGTO —, nao uma copia local que possa divergir de novo
    (o defeito M-8 que o cabecalho daquele modulo descreve para ESTA familia)."""
    esperado = "PAGTO-{}-{}-{}".format(
        key_segment(variante["tenant_id"]),
        key_segment(variante["numero_guia_tiss"]),
        key_segment(variante["glosa_id"]),
    )
    assert _pagto_business_key(**variante) == esperado  # type: ignore[arg-type]


def test_handoff_pagamento_dedup_converge_com_guia_com_padding() -> None:
    """A prova de ponta a ponta do vetor: a MESMA glosa reentregue com um espaco a mais na guia
    encontra a instancia anterior em vez de abrir uma segunda ordem de pagamento."""
    engine = FakeCibSevenTransport()
    sink = FakeStartAuditSink()
    primeira = handoff_pagamento(_handoff_vars(), engine=engine, audit_sink=sink)
    segunda = handoff_pagamento(
        _handoff_vars(numero_guia_tiss=" GUIA-1 ", glosa_id="GLOSA-1\t"),
        engine=engine,
        audit_sink=sink,
    )
    assert primeira["pagto_business_key"] == segunda["pagto_business_key"] == _PAGTO_BK
    assert segunda["pagto_already_existed"] is True
    assert primeira["pagto_instance_id"] == segunda["pagto_instance_id"]
    # A identidade que VIAJA para PAGTO tambem e a normalizada — nao o padding do reenvio.
    payload = asyncio.run(engine.get_process_status(_PAGTO_BK)).variables
    assert payload["numero_guia_tiss"] == "GUIA-1"
    assert payload["glosa_id"] == "GLOSA-1"


def test_chave_de_pagto_nunca_carrega_phi() -> None:
    """A chave e a ordem sao ancoradas em `tenant_id`/`numero_guia_tiss`/`glosa_id` — nenhuma
    variavel de `PHI_PROCESS_VARS` entra nelas nem no conjunto semeado (ADR-0006 duas zonas)."""
    assert not (HANDOFF_PAGTO_SEEDED_KEYS & PHI_PROCESS_VARS)
    engine = FakeCibSevenTransport()
    phi_semeada = {var: f"PHI-SENTINELA-{var}" for var in sorted(PHI_PROCESS_VARS)}
    result = handoff_pagamento(_handoff_vars(**phi_semeada), engine=engine, audit_sink=FakeStartAuditSink())
    payload = asyncio.run(engine.get_process_status(_PAGTO_BK)).variables
    assert result["pagto_business_key"] == _PAGTO_BK
    assert set(payload) == HANDOFF_PAGTO_SEEDED_KEYS
    for var, sentinela in phi_semeada.items():
        assert sentinela not in result["pagto_business_key"], var
        assert sentinela not in str(payload["ordem_pagamento_id"]), var
        assert sentinela not in payload.values(), var


# ----- m1/m2: proveniencia da ordem -----------------------------------------------------------


@pytest.mark.parametrize("fonte", ["apresentado", "glosado", "", "  ", "DEFERIDO", None])
def test_handoff_pagamento_recusa_fonte_valor_desconhecida(fonte: object) -> None:
    """m1: `fonte_valor` era ECOADA e entrava em `AgentDecisionProvenance.decision_basis`,
    enquanto `valor_pagamento_cents` vem SEMPRE de `valor_deferido_brl` — proveniencia que podia
    contradizer a fonte real. Fora do dominio declarado: recusa, sem efeito no engine.

    `""`/`"  "`/`None` sao o caso benigno (a ausencia defaulta para `deferido`); qualquer OUTRO
    rotulo e uma afirmacao falsa sobre a origem do dinheiro.
    """
    engine = FakeCibSevenTransport()
    esperado_ok = str(fonte or "deferido").strip() in FONTES_VALOR_PERMITIDAS
    if esperado_ok:
        handoff_pagamento(_handoff_vars(fonte_valor=fonte), engine=engine, audit_sink=FakeStartAuditSink())
        payload = asyncio.run(engine.get_process_status(_PAGTO_BK)).variables
        assert payload["fonte_valor"] == "deferido"
        return
    with pytest.raises(RecursoHandoffPagamentoInvalidoError, match="fonte_valor"):
        handoff_pagamento(_handoff_vars(fonte_valor=fonte), engine=engine, audit_sink=FakeStartAuditSink())
    assert asyncio.run(engine.find_active_instance(_PAGTO_BK)) is None


@pytest.mark.parametrize("over", [{"analista_id": ""}, {"analista_id": "   "}, {"analista_id": None}])
def test_handoff_pagamento_recusa_ordem_sem_decisor(over: dict) -> None:
    """m2: `lastro_decisor_id` aceitava vazio. Uma ordem de pagamento nascida de decisao humana
    nao pode sair sem o decisor identificado — e a EVIDENCIA que UT_AnaliseAdmissibilidade le
    (ADR-0007). Sem `auditor_id` de reserva, a recusa e a resposta; nenhum efeito no engine."""
    engine = FakeCibSevenTransport()
    with pytest.raises(RecursoHandoffPagamentoInvalidoError, match="decisor"):
        handoff_pagamento(_handoff_vars(**over), engine=engine, audit_sink=FakeStartAuditSink())
    assert asyncio.run(engine.find_active_instance(_PAGTO_BK)) is None


def test_handoff_pagamento_cai_para_o_auditor_como_decisor() -> None:
    """O ramo do merito: sem `analista_id`, o decisor e o `auditor_id` — normalizado, nao cru."""
    engine = FakeCibSevenTransport()
    handoff_pagamento(
        _handoff_vars(analista_id=None, auditor_id="  auditor-7  "),
        engine=engine,
        audit_sink=FakeStartAuditSink(),
    )
    payload = asyncio.run(engine.get_process_status(_PAGTO_BK)).variables
    assert payload["lastro_decisor_id"] == "auditor-7"


# ----- register_recurso_workers wires exactly 12 topics ---------------------------------------

_EXPECTED_TOPICS = frozenset(
    {
        "operadora.recurso.validate_recurso",
        "operadora.recurso.assess_eligibility",
        "operadora.recurso.request_documents",
        "operadora.recurso.analyze_request",
        "operadora.recurso.prepare_dossier",
        "operadora.recurso.escalate_to_junta",
        "operadora.recurso.registrar_indeferimento",
        "operadora.recurso.publish_completed",
        "operadora.recurso.handoff_pagamento",
        "operadora.recurso.notify_sla_risk",
        "operadora.recurso.escalate_ans_timeout",
        "operadora.recurso.comunicar_resposta",
    }
)


def test_register_recurso_workers_registers_all_12_topics() -> None:
    """EXACT SET, not a count: 13 - 3 (submit_appeal, track_status, reconcile_payment) + 2
    (comunicar_resposta, handoff_pagamento) = 12. A matching number with the wrong membership
    must not pass, which is why this asserts equality and not `>=`."""
    from maezo.tools.workers.harness import FakeWorkerTransport, WorkerHarness
    from maezo.tools.workers.recurso import register_recurso_workers

    harness = WorkerHarness(FakeWorkerTransport(), worker_id="probe")
    register_recurso_workers(harness, FakeKafkaPublisher())
    assert set(harness.registered_topics) == set(_EXPECTED_TOPICS)
    assert len(_EXPECTED_TOPICS) == 12
    for dead in (
        "operadora.recurso.submit_appeal",
        "operadora.recurso.track_status",
        "operadora.recurso.reconcile_payment",
        "operadora.recurso.register_desistencia",
    ):
        assert dead not in harness.registered_topics, f"{dead} nao deveria existir (sem shim)"


# ---------------------------------------------------------------------------
# BPMN — inicializacao das variaveis de decisao humana (defeito de engine, prova estatica)
# ---------------------------------------------------------------------------

_BPMN_RECURSO = Path(__file__).resolve().parents[4] / (
    "spec/processes/bpmn/SP-OP-RECURSO-001_Recurso_Glosa.bpmn"
)

#: Variaveis humanas OPCIONAIS que as conditionExpressions dos dois gateways decisorios leem.
#: Toda uma delas TEM de ser inicializada por `ST_PublishReceived` (a primeira service task de
#: TODO caminho) — ver `_publish_received_output_parameters` abaixo.
_VARS_DE_DECISAO_HUMANA = ("decisao_recurso", "decisao_auditor_recurso", "desfecho_humano")


def _bpmn_root() -> ET.Element:
    return ET.parse(_BPMN_RECURSO).getroot()


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _publish_received_output_parameters() -> dict[str, str]:
    """`name -> texto` dos `camunda:outputParameter` de `ST_PublishReceived`."""
    for el in _bpmn_root().iter():
        if _local(el.tag) == "serviceTask" and el.get("id") == "ST_PublishReceived":
            return {
                str(p.get("name")): (p.text or "")
                for p in el.iter()
                if _local(p.tag) == "outputParameter" and p.get("name")
            }
    raise AssertionError("ST_PublishReceived nao existe no BPMN")


_CAMUNDA_NS = "http://camunda.org/schema/1.0/bpmn"

#: Topico da service task de handoff de pagamento — a rota do dinheiro, que uma decisao humana
#: TEM de preceder.
_TOPICO_HANDOFF_PAGAMENTO = "operadora.recurso.handoff_pagamento"

#: No do AST produzido por `_parse_juel` (`(tag, *filhos)`).
_JuelNode = tuple[Any, ...]


class _JuelUnsupportedExpressionError(AssertionError):
    """Construto JUEL que este avaliador NAO entende.

    Falha ALTO de proposito. Um oraculo que "assume False" no que nao consegue ler aprova em
    silencio o mutante que introduziu o construto — foi assim que `!x.equals('ZZZ')` passaria
    por um teste que so procura `== ''`.
    """


class _JuelUnresolvedIdentifierError(AssertionError):
    """Identificador lido por uma condicao e ausente do ambiente inicializado.

    E exatamente o `Unknown property used in expression ... Cannot resolve identifier` que o
    CIB Seven lanca (HTTP 500 no complete da User Task) ANTES de cair no default do gateway.
    """


_JUEL_TOKENS = re.compile(
    r"""(?P<ws>\s+)
       |(?P<str>'[^']*'|"[^"]*")
       |(?P<op>==|!=|&&|\|\||[!()])
       |(?P<name>[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)""",
    re.VERBOSE,
)


def _juel_tokenize(inner: str) -> list[tuple[str, str]]:
    tokens: list[tuple[str, str]] = []
    pos = 0
    while pos < len(inner):
        match = _JUEL_TOKENS.match(inner, pos)
        if match is None:
            raise _JuelUnsupportedExpressionError(
                f"construto JUEL nao suportado por este oraculo em {inner!r}, coluna {pos}: "
                f"{inner[pos:]!r}. Estenda o avaliador — nao o silencie."
            )
        pos = match.end()
        kind = str(match.lastgroup)
        if kind != "ws":
            tokens.append((kind, match.group()))
    return tokens


class _JuelParser:
    """Descida recursiva sobre o subconjunto de JUEL realmente usado neste BPMN.

    Precedencia crescente: `||` < `&&` < (`==` | `!=`) < (`!` | `empty`) < primario.
    Primarios: literal de string, `null`, `true`/`false`, identificador, `( ... )`.
    Qualquer outra coisa -> `_JuelUnsupportedExpressionError`.
    """

    def __init__(self, tokens: list[tuple[str, str]], source: str) -> None:
        self._tokens = tokens
        self._pos = 0
        self._source = source

    def _peek(self) -> tuple[str, str] | None:
        return self._tokens[self._pos] if self._pos < len(self._tokens) else None

    def _take(self) -> tuple[str, str]:
        token = self._peek()
        if token is None:
            raise _JuelUnsupportedExpressionError(f"expressao JUEL truncada: {self._source!r}")
        self._pos += 1
        return token

    def parse(self) -> _JuelNode:
        node = self._or()
        if self._peek() is not None:
            raise _JuelUnsupportedExpressionError(
                f"sobra nao consumida no fim de {self._source!r}: {self._tokens[self._pos :]!r} "
                "(chamada de metodo? operador aritmetico? ternario?)"
            )
        return node

    def _or(self) -> _JuelNode:
        node = self._and()
        while self._peek() == ("op", "||"):
            self._take()
            node = ("or", node, self._and())
        return node

    def _and(self) -> _JuelNode:
        node = self._equality()
        while self._peek() == ("op", "&&"):
            self._take()
            node = ("and", node, self._equality())
        return node

    def _equality(self) -> _JuelNode:
        node = self._unary()
        token = self._peek()
        if token is not None and token[0] == "op" and token[1] in ("==", "!="):
            self._take()
            return ("eq" if token[1] == "==" else "ne", node, self._unary())
        return node

    def _unary(self) -> _JuelNode:
        token = self._peek()
        if token == ("op", "!"):
            self._take()
            return ("not", self._unary())
        if token is not None and token[0] == "name" and token[1] == "empty":
            self._take()
            return ("empty", self._unary())
        return self._primary()

    def _primary(self) -> _JuelNode:
        kind, text = self._take()
        if (kind, text) == ("op", "("):
            node = self._or()
            if self._peek() != ("op", ")"):
                raise _JuelUnsupportedExpressionError(f"parentese nao fechado em {self._source!r}")
            self._take()
            return node
        if kind == "str":
            return ("lit", text[1:-1])
        if kind == "name":
            if text == "null":
                return ("lit", None)
            if text in ("true", "false"):
                return ("lit", text == "true")
            return ("var", text)
        raise _JuelUnsupportedExpressionError(f"token inesperado {text!r} em {self._source!r}")


def _parse_juel(expr_text: str) -> _JuelNode:
    text = expr_text.strip()
    if not (text.startswith("${") and text.endswith("}")):
        raise _JuelUnsupportedExpressionError(f"conditionExpression fora da forma ${{...}}: {expr_text!r}")
    inner = text[2:-1]
    if "${" in inner or "}" in inner:
        raise _JuelUnsupportedExpressionError(f"expressao JUEL composta/aninhada: {expr_text!r}")
    return _JuelParser(_juel_tokenize(inner), inner).parse()


def _juel_identifiers(node: _JuelNode) -> set[str]:
    if node[0] == "lit":
        return set()
    if node[0] == "var":
        return {str(node[1])}
    lidos: set[str] = set()
    for child in node[1:]:
        lidos |= _juel_identifiers(child)
    return lidos


def _juel_equals(left: Any, right: Any) -> bool:
    """`==` de EL restrito ao que o dominio usa: string, `null` e booleano.

    `null` so e igual a `null` (logo `"" == null` e False, como no engine) e um booleano nunca e
    igual a uma string.
    """
    if left is None or right is None:
        return left is None and right is None
    if isinstance(left, bool) != isinstance(right, bool):
        return False
    return bool(left == right)


def _juel_require_bool(value: Any, onde: str, source: str) -> bool:
    if not isinstance(value, bool):
        raise _JuelUnsupportedExpressionError(
            f"operando nao-booleano em {onde} ({value!r}) na expressao {source!r} — a coercao "
            "implicita de EL nao e suportada por este oraculo; escreva a comparacao explicita."
        )
    return value


def _juel_eval(node: _JuelNode, env: dict[str, Any], source: str) -> Any:
    """Avalia o AST.

    SEM curto-circuito, de proposito: um identificador nao resolvivel do lado direito de um `&&`
    tem de aparecer como falha, nao ser escondido por um lado esquerdo falso.
    """
    tag = node[0]
    if tag == "lit":
        return node[1]
    if tag == "var":
        name = str(node[1])
        if name not in env:
            raise _JuelUnresolvedIdentifierError(
                f"{name!r} nao esta entre as variaveis inicializadas por ST_PublishReceived; no "
                "engine isto e `Cannot resolve identifier` (HTTP 500) e NAO o default do gateway. "
                f"Expressao: {source!r}"
            )
        return env[name]
    if tag == "not":
        return not _juel_require_bool(_juel_eval(node[1], env, source), "'!'", source)
    if tag == "empty":
        value = _juel_eval(node[1], env, source)
        return value is None or value == "" or (isinstance(value, (list, dict, set)) and not value)
    if tag in ("and", "or"):
        esquerda = _juel_require_bool(_juel_eval(node[1], env, source), f"'{tag}'", source)
        direita = _juel_require_bool(_juel_eval(node[2], env, source), f"'{tag}'", source)
        return (esquerda and direita) if tag == "and" else (esquerda or direita)
    if tag in ("eq", "ne"):
        iguais = _juel_equals(_juel_eval(node[1], env, source), _juel_eval(node[2], env, source))
        return iguais if tag == "eq" else not iguais
    raise _JuelUnsupportedExpressionError(f"no de AST desconhecido {tag!r} em {source!r}")


def _sequence_flows() -> list[dict[str, str | None]]:
    """Todo `sequenceFlow` com `sourceRef`, `targetRef` e o texto da condicao (ou None)."""
    flows: list[dict[str, str | None]] = []
    for el in _bpmn_root().iter():
        if _local(el.tag) != "sequenceFlow":
            continue
        condicao: str | None = None
        for child in el:
            if _local(child.tag) == "conditionExpression" and child.text:
                condicao = child.text
        flows.append(
            {
                "id": str(el.get("id")),
                "source": str(el.get("sourceRef")),
                "target": str(el.get("targetRef")),
                "condition": condicao,
            }
        )
    return flows


def _gateways_de_decisao_humana() -> dict[str, str | None]:
    """`exclusiveGateway id -> id do flow default`, para os gateways alimentados DIRETAMENTE por
    uma User Task — i.e. os que roteiam uma decisao humana.

    Resolvido por `sourceRef`/`targetRef`, NUNCA por prefixo de id de flow: renomear
    `Flow_GWDec_*`/`Flow_GWMerito_*` nao esconde o gateway deste oraculo. Foi exatamente por essa
    costura que o mutante M_G (flow renomeado + variavel nunca inicializada) reintroduziu o
    defeito debaixo da versao anterior destes testes sem acender nada.
    """
    root = _bpmn_root()
    user_tasks = {str(el.get("id")) for el in root.iter() if _local(el.tag) == "userTask"}
    gateways: dict[str, str | None] = {}
    for el in root.iter():
        if _local(el.tag) == "exclusiveGateway":
            default = el.get("default")
            gateways[str(el.get("id"))] = None if default is None else str(default)
    alimentados = {
        str(flow["target"])
        for flow in _sequence_flows()
        if flow["source"] in user_tasks and flow["target"] in gateways
    }
    return {gid: default for gid, default in gateways.items() if gid in alimentados}


def _tarefas_de_handoff_de_pagamento() -> set[str]:
    return {
        str(el.get("id"))
        for el in _bpmn_root().iter()
        if _local(el.tag) == "serviceTask" and el.get(f"{{{_CAMUNDA_NS}}}topic") == _TOPICO_HANDOFF_PAGAMENTO
    }


def _alcanca(origem: str, alvos: set[str]) -> bool:
    """Alcancabilidade por `sequenceFlow` (fecho transitivo, ignorando as condicoes).

    Conservador de proposito: uma rota so e considerada FORA da rota do dinheiro quando NENHUM
    caminho dela chega a uma task de handoff de pagamento.
    """
    adjacencia: dict[str, set[str]] = {}
    for flow in _sequence_flows():
        adjacencia.setdefault(str(flow["source"]), set()).add(str(flow["target"]))
    vistos = {origem}
    fila = [origem]
    while fila:
        atual = fila.pop()
        if atual in alvos:
            return True
        for proximo in adjacencia.get(atual, set()):
            if proximo not in vistos:
                vistos.add(proximo)
                fila.append(proximo)
    return False


def _e_fim_de_erro(element_id: str) -> bool:
    """O elemento e um `endEvent` com `errorEventDefinition` (terminal fail-closed)?"""
    for el in _bpmn_root().iter():
        if str(el.get("id")) != element_id:
            continue
        return _local(el.tag) == "endEvent" and any(
            _local(child.tag) == "errorEventDefinition" for child in el
        )
    return False


def test_variaveis_de_decisao_humana_sao_inicializadas_no_primeiro_service_task() -> None:
    """CIB Seven 2.1.0 lanca `Unknown property used in expression ... Cannot resolve identifier`
    quando uma conditionExpression le uma variavel que NUNCA foi setada — e o faz ANTES de cair
    no default do gateway. Sem esta inicializacao, o caso "decisao AUSENTE" nao alcanca
    `End_ErrRecursoDecisaoInvalida`: o `POST /task/{id}/complete` devolve 500 e a instancia fica
    PARADA na User Task. O terminal de erro cobre "ausente OU desconhecida" (documentacao do
    proprio end event); so o segundo caso funcionava.

    `""` e o valor certo: nenhuma condicao do dominio casa com ele, entao o token cai no default
    fail-closed, e o guard do worker (`ERR_RECURSO_INDEFERIMENTO_NOT_HUMAN`) recusa `""` como
    recusa qualquer nao-INDEFERIR — a inicializacao nao afrouxa o L0 hard.
    """
    outputs = _publish_received_output_parameters()
    for var in _VARS_DE_DECISAO_HUMANA:
        assert outputs.get(var) == '${""}', (
            f'{var} tem de ser inicializada como ${{""}} em ST_PublishReceived; veio '
            f"{outputs.get(var)!r}. Sem isso o default fail-closed do gateway e INALCANCAVEL "
            "para uma decisao ausente (a instancia trava na UT com HTTP 500)."
        )


def test_toda_variavel_lida_por_gateway_decisorio_esta_inicializada() -> None:
    """Fecha a CLASSE, nao so as tres variaveis de hoje: todo identificador lido por qualquer
    `conditionExpression` de um gateway de decisao humana tem de estar inicializado em
    `ST_PublishReceived`.

    O gateway e resolvido semanticamente — pelo `sourceRef` de quem o alimenta (uma User Task) —
    e os identificadores saem do AST da expressao. Nem prefixo de id de flow, nem regex sobre
    `==`/`!=`: um mutante que renomeie os flows e leia uma variavel nunca inicializada (M_G) fica
    VERMELHO aqui, e passava debaixo da versao anterior deste teste.
    """
    inicializadas = set(_publish_received_output_parameters())
    gateways = _gateways_de_decisao_humana()
    assert gateways, (
        "nenhum exclusiveGateway alimentado por User Task — o oraculo ficou inerte; o BPMN mudou "
        "de forma e este teste precisa ser reancorado, nao apagado"
    )
    lidas: set[str] = set()
    condicoes_por_gateway = dict.fromkeys(gateways, 0)
    for flow in _sequence_flows():
        origem = str(flow["source"])
        if origem not in gateways or flow["condition"] is None:
            continue
        condicoes_por_gateway[origem] += 1
        lidas |= _juel_identifiers(_parse_juel(str(flow["condition"])))
    mudos = sorted(gid for gid, n in condicoes_por_gateway.items() if n == 0)
    assert not mudos, f"gateway(s) de decisao humana sem nenhuma saida condicional: {mudos}"
    assert lidas, "nenhum identificador lido pelas condicoes — teste inerte"
    assert lidas <= inicializadas, (
        "variaveis lidas por gateway de decisao humana e NAO inicializadas em ST_PublishReceived: "
        f"{sorted(lidas - inicializadas)}. Sem a inicializacao o engine lanca `Cannot resolve "
        "identifier` (HTTP 500) e a instancia trava na User Task em vez de cair no default."
    )


def test_a_inicializacao_nao_casa_com_nenhuma_rota_de_acao() -> None:
    """ORACULO SEMANTICO, nao sintatico: parseia e AVALIA cada `conditionExpression` dos gateways
    de decisao humana com as variaveis inicializadas ligadas a `""`.

    Prova tres coisas:

    (a) toda condicao de acao e False sobre a inicializacao — o token cai no default e nenhum ato
        nasce por omissao;
    (b) cada gateway declara um `default` sem condicao, e esse default termina num `endEvent` com
        `errorEventDefinition` (fail-closed: erro visivel, nenhum efeito materializado);
    (c) nenhuma rota que ALCANCA o handoff de pagamento dispara enquanto as variaveis que ela le
        nao tiverem valor humano — verificado por enumeracao de TODAS as atribuicoes sobre
        {`""`, `null`}, nao so a inicializacao.

    E por AVALIAR — e nao por procurar `== ''` — que ele fica vermelho em `!= 'ZZZ'` (M_B) e em
    `!x.equals('ZZZ')` (M_E), mutantes em que a rota DEFERIR -> `ST_ComunicarDeferimento` ->
    `ST_HandoffPagamentoRecurso` dispara com a decisao humana AUSENTE: uma ordem de pagamento por
    omissao. O avaliador falha ALTO no que nao entende; ele nunca assume False.
    """
    outputs = _publish_received_output_parameters()
    env: dict[str, Any] = {nome: "" for nome, texto in outputs.items() if texto.strip() == '${""}'}
    assert env, 'ST_PublishReceived nao inicializa nenhuma variavel com ${""} — teste inerte'
    gateways = _gateways_de_decisao_humana()
    assert gateways, "nenhum gateway de decisao humana — teste inerte"
    pagamento = _tarefas_de_handoff_de_pagamento()
    assert pagamento, f"nenhuma service task com topic {_TOPICO_HANDOFF_PAGAMENTO} — teste inerte"

    flows = _sequence_flows()
    avaliadas = 0
    for gid, default_id in gateways.items():
        saidas = [flow for flow in flows if flow["source"] == gid]

        # (b) o default existe, e incondicional e termina no erro fail-closed
        assert default_id is not None, (
            f"{gid} nao declara default=... — uma decisao ausente/desconhecida ficaria sem rota"
        )
        defaults = [flow for flow in saidas if flow["id"] == default_id]
        assert len(defaults) == 1, f"o default {default_id!r} de {gid} nao e uma saida do proprio gateway"
        assert defaults[0]["condition"] is None, (
            f"o default {default_id!r} de {gid} carrega conditionExpression — deixa de ser default"
        )
        alvo_default = str(defaults[0]["target"])
        assert _e_fim_de_erro(alvo_default), (
            f"o default de {gid} vai para {alvo_default!r}, que nao e um endEvent de erro — o "
            "fail-closed exige terminar em erro visivel, sem efeito materializado"
        )

        for flow in saidas:
            if flow["condition"] is None:
                continue
            texto = str(flow["condition"])
            ast = _parse_juel(texto)
            avaliadas += 1

            # (a) nenhuma rota de acao casa com a inicializacao
            casou = _juel_require_bool(_juel_eval(ast, env, texto), "a condicao inteira", texto)
            assert casou is False, (
                f"{flow['id']} ({gid}) casa com a inicializacao {env!r}: {texto!r}. A inicializacao "
                "teria criado um ATO POR OMISSAO — exatamente o que o default fail-closed existe "
                "para impedir."
            )

            # (c) nenhuma rota do dinheiro dispara sem valor humano nenhum
            if not _alcanca(str(flow["target"]), pagamento):
                continue
            lidas = sorted(_juel_identifiers(ast))
            for combinacao in product(("", None), repeat=len(lidas)):
                ambiente: dict[str, Any] = dict(zip(lidas, combinacao, strict=True))
                disparou = _juel_require_bool(_juel_eval(ast, ambiente, texto), "a condicao inteira", texto)
                assert disparou is False, (
                    f"{flow['id']} alcanca {sorted(pagamento)} e dispara com {ambiente!r} — i.e. "
                    "SEM nenhum valor humano. Seria uma ordem de pagamento por omissao."
                )

    assert avaliadas >= 9, (
        f"apenas {avaliadas} condicoes de gateway decisorio avaliadas (esperado >= 9) — o oraculo "
        "perdeu cobertura ou o BPMN encolheu sem que este teste fosse reancorado"
    )


def test_o_oraculo_juel_avalia_e_recusa_alto_o_que_nao_entende() -> None:
    """O oraculo dos dois testes acima e codigo, e codigo sem teste nao e prova.

    Pinos positivos e negativos do avaliador, mais a garantia de que ele FALHA ALTO no que nao
    suporta — nunca "assume False", que e por onde um mutante entraria sem acender nada.
    """
    base: dict[str, Any] = {"x": "", "y": ""}

    def ev(expr: str, env: dict[str, Any] | None = None) -> Any:
        return _juel_eval(_parse_juel(expr), base if env is None else env, expr)

    assert ev("${x == 'DEFERIR'}") is False
    assert ev("${x != 'ZZZ'}") is True  # M_B: e por isto que a rota DEFERIR acenderia
    assert ev("${x == ''}") is True  # M_C
    assert ev("${x == null}") is False  # "" nao e null
    assert ev("${empty x}") is True
    assert ev("${!(x == 'A')}") is True
    assert ev("${x == 'A' && y == 'B'}") is False
    assert ev("${x == 'A' || y == ''}") is True
    assert ev("${x == 'INDEFERIR' && (y == null || y != 'inadmissivel')}") is False
    assert ev("${x == 'A'}", {"x": None}) is False
    assert ev("${x == 'A'}", {"x": "A"}) is True

    with pytest.raises(_JuelUnresolvedIdentifierError):
        ev("${z == 'A'}")
    with pytest.raises(_JuelUnsupportedExpressionError):
        ev("${!x.equals('ZZZ')}")  # M_E: chamada de metodo
    with pytest.raises(_JuelUnsupportedExpressionError):
        ev("${x + 1 == 2}")  # aritmetica
    with pytest.raises(_JuelUnsupportedExpressionError):
        ev("${x == 'A' ? true : false}")  # ternario
    with pytest.raises(_JuelUnsupportedExpressionError):
        _juel_require_bool(ev("${x}"), "a condicao inteira", "${x}")  # coercao implicita
