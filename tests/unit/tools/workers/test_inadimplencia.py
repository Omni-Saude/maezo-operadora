"""Unit tests for maezo.tools.workers.inadimplencia (SP-OP-INADIMPLENCIA-001).

TDD London School: tests verify the guard contracts from the SP-OP contract.
"""

import asyncio
from typing import Any

import pytest

from maezo.gateway.audit_postgres import AuditPersistenceError, FreshSinkAuditEmitter
from maezo.tools.mcp_cibseven.transport import (
    CibSevenError,
    DedupReportingAuditSink,
    FakeCibSevenTransport,
    HistoryQueryingTransport,
    ProcessInstance,
    StartDedupGateUnavailableError,
    StartDedupPosture,
    is_strict_start_dedup,
    start_dedup_key,
    start_dedup_posture,
)
from maezo.tools.workers.cibseven_engine import FreshClientCibSevenTransport
from maezo.tools.workers.dmn_transport import DmnEvaluationError, FakeDmnTransport
from maezo.tools.workers.harness import AUDIT_AGENT_ID
from maezo.tools.workers.inadimplencia import (
    CANCEL_PROCESS_KEY,
    DECISAO_ENCAMINHAR_RESCISAO,
    DECISAO_MANTER,
    DECISAO_SUSPENDER,
    ERR_CONTRACT_SUSPENSION_NOT_HUMAN,
    ERR_INAD_INVALID_CONTRATO,
    InadimplenciaError,
    _cancel_business_key,
    assess_status,
    calculate_purge,
    handoff_rescisao,
    notify_beneficiario,
    notify_sla_risk,
    prepare_dossier,
    register_contract_suspension,
    register_inadimplencia_workers,
    register_suspension,
    resolve_facts,
)
from tests.support.audit_fakes import FakeStartAuditSink


def _inadimplencia_status_fake(*, roteamento: str, motivo: str = "") -> FakeDmnTransport:
    """Rows verified live against the compose engine (T1.5 parity run)."""
    fake = FakeDmnTransport()
    fake.register("inadimplencia_status", [{"roteamento": roteamento, "motivo": motivo}])
    return fake


# ---------------------------------------------------------------
# resolve_facts
# ---------------------------------------------------------------


def test_resolve_facts_computes_meses_from_competencias() -> None:
    result = resolve_facts(
        {
            "competencias_em_aberto": ["2024-01", "2024-02", "2024-03"],
            "valor_total_devido_cents": 150000,
            "notificacao_previa_feita": False,
        }
    )
    assert result["meses_inadimplencia"] == 3
    assert result["valor_total_devido_cents"] == 150000


def test_resolve_facts_empty_competencias() -> None:
    result = resolve_facts(
        {
            "competencias_em_aberto": [],
            "valor_total_devido_cents": 0,
        }
    )
    assert result["meses_inadimplencia"] == 0


# ---------------------------------------------------------------
# assess_status
# ---------------------------------------------------------------


def test_assess_status_coletivo_to_human() -> None:
    fake = _inadimplencia_status_fake(roteamento="ANALISE_HUMANA")
    result = assess_status(
        {
            "tipo_plano": "coletivo_empresarial",
            "meses_inadimplencia": 6,
            "dentro_periodo_minimo": True,
            "notificacao_previa_feita": True,
            "dentro_janela_purga": False,
        },
        dmn=fake,
    )
    assert result["roteamento"] == "ANALISE_HUMANA"


def test_assess_status_pendente_notificacao() -> None:
    fake = _inadimplencia_status_fake(roteamento="PENDENTE_NOTIFICACAO")
    result = assess_status(
        {
            "tipo_plano": "individual",
            "meses_inadimplencia": 3,
            "dentro_periodo_minimo": True,
            "notificacao_previa_feita": False,
            "dentro_janela_purga": False,
        },
        dmn=fake,
    )
    assert result["roteamento"] == "PENDENTE_NOTIFICACAO"


def test_assess_status_aguarda_purga() -> None:
    fake = _inadimplencia_status_fake(roteamento="AGUARDA_PURGA")
    result = assess_status(
        {
            "tipo_plano": "individual",
            "meses_inadimplencia": 3,
            "dentro_periodo_minimo": True,
            "notificacao_previa_feita": True,
            "dentro_janela_purga": True,
        },
        dmn=fake,
    )
    assert result["roteamento"] == "AGUARDA_PURGA"


def test_assess_status_segue_analise() -> None:
    fake = _inadimplencia_status_fake(roteamento="SEGUE_ANALISE")
    result = assess_status(
        {
            "tipo_plano": "individual",
            "meses_inadimplencia": 3,
            "dentro_periodo_minimo": True,
            "notificacao_previa_feita": True,
            "dentro_janela_purga": False,
        },
        dmn=fake,
    )
    assert result["roteamento"] == "SEGUE_ANALISE"


def test_assess_status_catch_all_to_human() -> None:
    """Catch-all conservador: tudo que nao casa cai em ANALISE_HUMANA."""
    fake = _inadimplencia_status_fake(roteamento="ANALISE_HUMANA")
    result = assess_status(
        {
            "tipo_plano": "individual",
            "meses_inadimplencia": 0,
            "dentro_periodo_minimo": False,
            "notificacao_previa_feita": True,
            "dentro_janela_purga": False,
        },
        dmn=fake,
    )
    assert result["roteamento"] == "ANALISE_HUMANA"


def test_assess_status_order_divergence_purga_wins_over_notificacao() -> None:
    """golden-parity finding (T1.5, live-verified): the deployed inadimplencia_status DMN
    checks dentro_janela_purga BEFORE notificacao_previa_feita (FIRST hit policy) — the old
    Python checked notificacao first. notificacao_previa_feita=False AND
    dentro_janela_purga=True now yields AGUARDA_PURGA (was PENDENTE_NOTIFICACAO). Neither is
    adverse — no SUSPENDER/RESCINDIR path exists in either version."""
    fake = _inadimplencia_status_fake(roteamento="AGUARDA_PURGA")
    result = assess_status(
        {
            "tipo_plano": "individual",
            "meses_inadimplencia": 3,
            "dentro_periodo_minimo": True,
            "notificacao_previa_feita": False,
            "dentro_janela_purga": True,
        },
        dmn=fake,
    )
    assert result["roteamento"] == "AGUARDA_PURGA"


def test_assess_status_dmn_unwired_raises_dmn_evaluation_error() -> None:
    with pytest.raises(DmnEvaluationError):
        assess_status({"tipo_plano": "individual"}, dmn=None)


# ---------------------------------------------------------------
# calculate_purge
# ---------------------------------------------------------------


def test_calculate_purge_individual() -> None:
    result = calculate_purge({"tipo_plano": "individual"})
    assert result["prazo_purga"] == "P10D"
    assert result["periodo_minimo"] == "P60D"


def test_calculate_purge_coletivo() -> None:
    result = calculate_purge({"tipo_plano": "coletivo_empresarial"})
    assert result["prazo_purga"] == "P30D"
    assert result["periodo_minimo"] == "P90D"


# ---------------------------------------------------------------
# notify_beneficiario
# ---------------------------------------------------------------


def test_notify_beneficiario_sets_flag() -> None:
    result = notify_beneficiario(
        {
            "numero_contrato": "C-123",
            "matricula_beneficiario": "pseudo-abc",
        }
    )
    assert result["notificacao_previa_feita"] is True


# ---------------------------------------------------------------
# register_suspension / register_contract_suspension — GUARD
# ---------------------------------------------------------------


def test_inadimplencia_guard_suspension_happy_path() -> None:
    result = register_contract_suspension(
        {
            "decisao_inadimplencia": DECISAO_SUSPENDER,
            "responsavel_id": "juridico-001",
            "fundamentacao_contratual": "Art. 13 Lei 9656",
            "referencia_regulatoria": "RN 593",
            "comprovacao_notificacao_previa": "ref-notif-001",
            "comprovacao_periodo_minimo": "ref-periodo-001",
            "ja_em_rescisao_cancel": False,
            "numero_contrato": "C-123",
            "data_efeito_iso": "2024-06-01",
        }
    )
    assert result["suspensao_registrada"] is True


def test_inadimplencia_guard_rejects_wrong_decisao() -> None:
    with pytest.raises(InadimplenciaError) as excinfo:
        register_contract_suspension(
            {
                "decisao_inadimplencia": DECISAO_MANTER,
                "responsavel_id": "juridico-001",
                "fundamentacao_contratual": "x",
                "referencia_regulatoria": "x",
                "comprovacao_notificacao_previa": "x",
                "comprovacao_periodo_minimo": "x",
                "ja_em_rescisao_cancel": False,
            }
        )
    assert excinfo.value.code == ERR_CONTRACT_SUSPENSION_NOT_HUMAN
    assert "MANTER" in excinfo.value.message


def test_inadimplencia_guard_rejects_missing_responsavel() -> None:
    with pytest.raises(InadimplenciaError) as excinfo:
        register_contract_suspension(
            {
                "decisao_inadimplencia": DECISAO_SUSPENDER,
                "responsavel_id": "",
                "fundamentacao_contratual": "x",
                "referencia_regulatoria": "x",
                "comprovacao_notificacao_previa": "x",
                "comprovacao_periodo_minimo": "x",
                "ja_em_rescisao_cancel": False,
            }
        )
    assert excinfo.value.code == ERR_CONTRACT_SUSPENSION_NOT_HUMAN
    assert "responsavel_id" in excinfo.value.message


def test_inadimplencia_guard_rejects_missing_fundamentacao() -> None:
    with pytest.raises(InadimplenciaError) as excinfo:
        register_contract_suspension(
            {
                "decisao_inadimplencia": DECISAO_SUSPENDER,
                "responsavel_id": "j-001",
                "fundamentacao_contratual": "",
                "referencia_regulatoria": "RN 593",
                "comprovacao_notificacao_previa": "x",
                "comprovacao_periodo_minimo": "x",
                "ja_em_rescisao_cancel": False,
            }
        )
    assert excinfo.value.code == ERR_CONTRACT_SUSPENSION_NOT_HUMAN


def test_inadimplencia_guard_rejects_ja_em_rescisao() -> None:
    """Anti-double-termination: se ja_em_rescisao_cancel, recusa suspensao."""
    with pytest.raises(InadimplenciaError) as excinfo:
        register_contract_suspension(
            {
                "decisao_inadimplencia": DECISAO_SUSPENDER,
                "responsavel_id": "j-001",
                "fundamentacao_contratual": "Art. 13",
                "referencia_regulatoria": "RN 593",
                "comprovacao_notificacao_previa": "ref-001",
                "comprovacao_periodo_minimo": "ref-002",
                "ja_em_rescisao_cancel": True,
            }
        )
    assert excinfo.value.code == ERR_CONTRACT_SUSPENSION_NOT_HUMAN
    assert "ja_em_rescisao_cancel" in excinfo.value.message


def test_inadimplencia_register_suspension_alias() -> None:
    """register_suspension is an alias for register_contract_suspension."""
    result = register_suspension(
        {
            "decisao_inadimplencia": DECISAO_SUSPENDER,
            "responsavel_id": "j-001",
            "fundamentacao_contratual": "x",
            "referencia_regulatoria": "x",
            "comprovacao_notificacao_previa": "x",
            "comprovacao_periodo_minimo": "x",
            "ja_em_rescisao_cancel": False,
        }
    )
    assert result["suspensao_registrada"] is True


# ---------------------------------------------------------------
# register_contract_suspension — whitespace-bypass vectors (t3.1-guard-input-hardening,
# same class as the c1377fa fix for pagto.register_payment_refusal: bare `if not field:`
# let WHITESPACE-ONLY decision + accountability fields through. Every vector below MUST
# refuse with ERR_CONTRACT_SUSPENSION_NOT_HUMAN — whitespace-only is the SAME as absent
# (ADR-0007/RN 593). The ja_em_rescisao_cancel anti-dupla boolean guard is UNTOUCHED by
# the normalization (input normalization only) — pinned by the last test in this block.
# ---------------------------------------------------------------

_WHITESPACE_VARIANTS = [" ", "   ", "\t", "\n", "\t\n ", "\r\n"]
_NON_STRING_VARIANTS: list[object] = [123, True, 0.5, ["x"], {"k": "v"}]
_SUSPENSION_ACCOUNTABILITY_FIELDS = [
    "responsavel_id",
    "fundamentacao_contratual",
    "referencia_regulatoria",
    "comprovacao_notificacao_previa",
    "comprovacao_periodo_minimo",
]


def _suspension_baseline(**overrides: object) -> dict[str, object]:
    """Happy-path baseline (ja_em_rescisao_cancel explicitly False — engine positively
    confirmed no active CANCEL-001 instance) so any guard failure observed in a test is
    attributable ONLY to the field under test."""
    base: dict[str, object] = {
        "decisao_inadimplencia": DECISAO_SUSPENDER,
        "responsavel_id": "juridico-001",
        "fundamentacao_contratual": "Art. 13 Lei 9656",
        "referencia_regulatoria": "RN 593",
        "comprovacao_notificacao_previa": "ref-notif-001",
        "comprovacao_periodo_minimo": "ref-periodo-001",
        "ja_em_rescisao_cancel": False,
        "numero_contrato": "C-123",
    }
    base.update(overrides)
    return base


@pytest.mark.parametrize("field", _SUSPENSION_ACCOUNTABILITY_FIELDS)
@pytest.mark.parametrize("whitespace", _WHITESPACE_VARIANTS)
def test_suspension_whitespace_only_accountability_field_refuses(field: str, whitespace: str) -> None:
    """Bare-truthiness bypass (pre-fix): whitespace-only accountability field must refuse and
    be named in the guard's error message."""
    with pytest.raises(InadimplenciaError) as excinfo:
        register_contract_suspension(_suspension_baseline(**{field: whitespace}))
    assert excinfo.value.code == ERR_CONTRACT_SUSPENSION_NOT_HUMAN
    assert field in excinfo.value.message


@pytest.mark.parametrize("field", _SUSPENSION_ACCOUNTABILITY_FIELDS)
@pytest.mark.parametrize("non_string", _NON_STRING_VARIANTS)
def test_suspension_non_string_accountability_field_refuses(field: str, non_string: object) -> None:
    """A NON-string accountability field normalizes to '' and refuses -- the pre-fix bare
    truthiness check would have silently PASSED a truthy non-string (e.g. 123)."""
    with pytest.raises(InadimplenciaError) as excinfo:
        register_contract_suspension(_suspension_baseline(**{field: non_string}))
    assert excinfo.value.code == ERR_CONTRACT_SUSPENSION_NOT_HUMAN
    assert field in excinfo.value.message


@pytest.mark.parametrize("whitespace", _WHITESPACE_VARIANTS)
def test_suspension_both_responsavel_and_fundamentacao_whitespace_refuses(
    whitespace: str,
) -> None:
    """Both responsavel_id AND fundamentacao_contratual whitespace-only -- both named."""
    with pytest.raises(InadimplenciaError) as excinfo:
        register_contract_suspension(
            _suspension_baseline(responsavel_id=whitespace, fundamentacao_contratual=whitespace)
        )
    assert excinfo.value.code == ERR_CONTRACT_SUSPENSION_NOT_HUMAN
    assert "responsavel_id" in excinfo.value.message
    assert "fundamentacao_contratual" in excinfo.value.message


@pytest.mark.parametrize("whitespace", [" ", "\t", "\n", "  \t\n"])
def test_suspension_whitespace_only_decisao_refuses(whitespace: str) -> None:
    """Whitespace-only decisao_inadimplencia normalizes to '' -> != SUSPENDER -> refuses."""
    with pytest.raises(InadimplenciaError) as excinfo:
        register_contract_suspension(_suspension_baseline(decisao_inadimplencia=whitespace))
    assert excinfo.value.code == ERR_CONTRACT_SUSPENSION_NOT_HUMAN
    assert "decisao_inadimplencia" in excinfo.value.message


def test_suspension_padded_valid_literal_normalizes_and_registers() -> None:
    """Whitespace-PADDED but otherwise exact literal/fields normalize via `_norm_str` and still
    register (pins the normalization -- NOT a bypass, the documented `.strip()` consequence).
    ja_em_rescisao_cancel remains an explicit False boolean -- untouched by normalization."""
    result = register_contract_suspension(
        _suspension_baseline(
            decisao_inadimplencia=" SUSPENDER ",
            responsavel_id=" juridico-001 ",
            fundamentacao_contratual=" Art. 13 Lei 9656 ",
            referencia_regulatoria=" RN 593 ",
            comprovacao_notificacao_previa=" ref-notif-001 ",
            comprovacao_periodo_minimo=" ref-periodo-001 ",
        )
    )
    assert result["suspensao_registrada"] is True


@pytest.mark.parametrize("decision", ["suspender", "Suspender", "SUSPENDER_X", "XSUSPENDER", "MANTER "])
def test_suspension_non_exact_decisao_literal_still_refuses(decision: str) -> None:
    """Exact-match discipline survives normalization: case variants/substrings never pass."""
    with pytest.raises(InadimplenciaError) as excinfo:
        register_contract_suspension(_suspension_baseline(decisao_inadimplencia=decision))
    assert excinfo.value.code == ERR_CONTRACT_SUSPENSION_NOT_HUMAN


def test_suspension_anti_dupla_guard_unchanged_by_normalization() -> None:
    """The ja_em_rescisao_cancel anti-dupla-terminacao guard (GAP-INAD-1, fail-closed
    `is not False`) is byte-identical after the normalization change: all string fields
    valid + ja_em_rescisao_cancel absent (defaults True — correlation never confirmed)
    STILL refuses, proving normalization touched only the string inputs."""
    variables = _suspension_baseline()
    del variables["ja_em_rescisao_cancel"]
    with pytest.raises(InadimplenciaError) as excinfo:
        register_contract_suspension(variables)
    assert excinfo.value.code == ERR_CONTRACT_SUSPENSION_NOT_HUMAN
    assert "ja_em_rescisao_cancel" in excinfo.value.message


# ---------------------------------------------------------------
# handoff_rescisao — NEUTRO
# ---------------------------------------------------------------


def test_handoff_rescisao_executes_for_encaminhar() -> None:
    """ENCAMINHAR_RESCISAO now ACTUALLY starts CANCEL-001 via the engine seam (T1.10 T-D),
    AUDITED through the T-C2 fence (10th start site, wave integration)."""
    engine = FakeCibSevenTransport()
    sink = FakeStartAuditSink()
    result = handoff_rescisao(
        {
            "decisao_inadimplencia": DECISAO_ENCAMINHAR_RESCISAO,
            "tenant_id": "t1",
            "numero_contrato": "C-456",
        },
        engine=engine,
        audit_sink=sink,
    )
    assert result["handoff_executado"] is True
    assert result["processo_destino"] == "SP-OP-CANCEL-001"
    assert result["cancel_business_key"] == "CANCEL-t1-C-456"
    # T-C2 fence: exactly ONE durable start record, under the exact-once dedup key.
    assert sink.dedup_keys == [start_dedup_key("t1", CANCEL_PROCESS_KEY, "CANCEL-t1-C-456")]
    assert sink.dedup_keys == ["t1:start:SP-OP-CANCEL-001:CANCEL-t1-C-456"]


def test_handoff_rescisao_starts_cancel_with_exact_business_key_and_payload() -> None:
    """The BINDING business-key format `CANCEL-{tenant}-{contrato}` (harmonization §1) AND the
    handoff payload (tipo/origem + carried RN-593 evidence + audit-trail responsavel_id)."""
    engine = FakeCibSevenTransport()
    sink = FakeStartAuditSink()
    result = handoff_rescisao(
        {
            "decisao_inadimplencia": DECISAO_ENCAMINHAR_RESCISAO,
            "tenant_id": "amh",
            "numero_contrato": "C-777",
            "matricula_beneficiario": "mat-1",
            "meses_inadimplencia": 4,
            "notificacao_previa_feita": True,
            "comprovacao_periodo_minimo": "ref-periodo-1",
            "responsavel_id": "juridico-cobranca-9",
            "referencia_regulatoria": "RN 593",
            "decisao_secreta_phi": "SHOULD-NOT-LEAK",  # not in allowlist -> must not be carried
        },
        engine=engine,
        audit_sink=sink,
    )
    assert result["cancel_business_key"] == "CANCEL-amh-C-777"
    assert result["cancel_already_existed"] is False

    # The CANCEL-001 instance was really started under the exact business key.
    started = asyncio.run(engine.find_active_instance("CANCEL-amh-C-777"))
    assert started is not None
    assert started.process_key == CANCEL_PROCESS_KEY

    payload = asyncio.run(engine.get_process_status("CANCEL-amh-C-777")).variables
    assert payload["tipo_solicitacao"] == "inadimplencia"
    assert payload["origem_solicitacao"] == "operadora"
    assert payload["numero_contrato"] == "C-777"
    assert payload["responsavel_id"] == "juridico-cobranca-9"
    assert payload["notificacao_previa_feita"] is True
    # PHI-safe allowlist, never a passthrough: a non-allowlisted key is NOT carried into CANCEL.
    assert "decisao_secreta_phi" not in payload

    # T-C2 fence: the durable ADR-0007 start record — worker-context provenance, exactly once.
    assert sink.dedup_keys == [start_dedup_key("amh", CANCEL_PROCESS_KEY, "CANCEL-amh-C-777")]
    [record] = sink.records
    assert record.agent_id == AUDIT_AGENT_ID  # stable service identity ("operadora-worker")
    assert record.tenant_id == "amh"
    assert record.action == f"start_process:{CANCEL_PROCESS_KEY}"
    assert record.decision == "START_PROCESS"
    assert record.model_id is None  # deterministic worker context — no LLM legs, honest nulls
    assert record.prompt_version is None
    # decision_basis: bounded enum/flag tokens ONLY (design §3.3) + the one-way input hash.
    assert record.details["decisao_inadimplencia"] == DECISAO_ENCAMINHAR_RESCISAO
    assert record.details["origem_solicitacao"] == "operadora"
    assert record.details["notificacao_previa_feita"] is True
    assert record.details["process_key"] == CANCEL_PROCESS_KEY
    assert "input_sha256" in record.details
    # Identifiers/PHI never in the clear in the chain: hash-bound via input_sha256 only.
    assert "responsavel_id" not in record.details
    assert "numero_contrato" not in record.details
    assert "matricula_beneficiario" not in record.details
    assert "decisao_secreta_phi" not in record.details


def test_handoff_rescisao_matricula_fallback_key() -> None:
    """Individual/familiar plans (no numero_contrato) key CANCEL by matricula fallback."""
    engine = FakeCibSevenTransport()
    sink = FakeStartAuditSink()
    result = handoff_rescisao(
        {
            "decisao_inadimplencia": DECISAO_ENCAMINHAR_RESCISAO,
            "tenant_id": "t1",
            "matricula_beneficiario": "mat-42",
        },
        engine=engine,
        audit_sink=sink,
    )
    assert result["cancel_business_key"] == "CANCEL-t1-mat-42"
    # The dedup key follows the matricula-fallback business key too (same identity scheme).
    assert sink.dedup_keys == [start_dedup_key("t1", CANCEL_PROCESS_KEY, "CANCEL-t1-mat-42")]


def test_handoff_rescisao_idempotent_returns_existing_active_cancel() -> None:
    """A CANCEL-001 instance already active for this contract is returned unchanged — the handoff
    NEVER starts a second one (anti-dupla-terminacao via business-key idempotency)."""
    engine = FakeCibSevenTransport()
    sink = FakeStartAuditSink()
    engine.seed_instance(
        ProcessInstance(
            instance_id="pre-existing-cancel",
            process_key=CANCEL_PROCESS_KEY,
            business_key="CANCEL-t1-C-5",
            state="ACTIVE",
            already_existed=True,  # the real find_active_instance flags an idempotent hit this way
        )
    )
    result = handoff_rescisao(
        {
            "decisao_inadimplencia": DECISAO_ENCAMINHAR_RESCISAO,
            "tenant_id": "t1",
            "numero_contrato": "C-5",
        },
        engine=engine,
        audit_sink=sink,
    )
    assert result["handoff_executado"] is True
    assert result["cancel_already_existed"] is True
    assert result["cancel_instance_id"] == "pre-existing-cancel"
    # The fence still records the (idempotent) start decision — emit precedes the active-hit
    # check, and a re-delivery of the same key dedups in the sink, never double-chains.
    assert sink.dedup_keys == [start_dedup_key("t1", CANCEL_PROCESS_KEY, "CANCEL-t1-C-5")]


def test_handoff_rescisao_fail_closed_when_engine_seam_not_wired() -> None:
    """FAIL-CLOSED: no engine seam -> the human's ENCAMINHAR_RESCISAO can NOT be silently dropped;
    raises (transient) instead of a no-op that would strand the rescisao."""
    with pytest.raises(RuntimeError, match="engine seam"):
        handoff_rescisao(
            {
                "decisao_inadimplencia": DECISAO_ENCAMINHAR_RESCISAO,
                "tenant_id": "t1",
                "numero_contrato": "C-1",
            },
            audit_sink=FakeStartAuditSink(),
        )  # engine defaults to None


def test_handoff_rescisao_fail_closed_when_audit_sink_not_wired() -> None:
    """FAIL-CLOSED (T-C2 fence co-requisite): no audit sink -> the fenced start CANNOT emit the
    ADR-0007 record, so CANCEL-001 is NOT started (emit-before-effect) and the handoff raises
    (transient -> retry -> incident) — never an un-audited start, never a silent no-op."""
    engine = FakeCibSevenTransport()
    with pytest.raises(RuntimeError, match="audit sink"):
        handoff_rescisao(
            {
                "decisao_inadimplencia": DECISAO_ENCAMINHAR_RESCISAO,
                "tenant_id": "t1",
                "numero_contrato": "C-1",
            },
            engine=engine,
        )  # audit_sink defaults to None
    # No engine effect happened: the fence never ran, nothing was started.
    assert asyncio.run(engine.find_active_instance("CANCEL-t1-C-1")) is None


def test_handoff_rescisao_emit_failure_blocks_engine_start() -> None:
    """EMIT-BEFORE-EFFECT (design §4.2): a durable-audit failure propagates
    (`AuditPersistenceError` -> transient -> retry) and the engine start NEVER happens — the
    forbidden direction (a CANCEL-001 start with no audit row) is structurally impossible."""
    engine = FakeCibSevenTransport()
    sink = FakeStartAuditSink(fail=True)
    with pytest.raises(AuditPersistenceError):
        handoff_rescisao(
            {
                "decisao_inadimplencia": DECISAO_ENCAMINHAR_RESCISAO,
                "tenant_id": "t1",
                "numero_contrato": "C-1",
            },
            engine=engine,
            audit_sink=sink,
        )
    assert len(sink.calls) == 1  # the emit was attempted...
    assert asyncio.run(engine.find_active_instance("CANCEL-t1-C-1")) is None  # ...the start was not


def test_handoff_rescisao_fail_closed_no_contract_identity() -> None:
    """No numero_contrato/matricula -> never start CANCEL-001 with an empty business key: raises."""
    engine = FakeCibSevenTransport()
    sink = FakeStartAuditSink()
    with pytest.raises(InadimplenciaError) as excinfo:
        handoff_rescisao(
            {"decisao_inadimplencia": DECISAO_ENCAMINHAR_RESCISAO, "tenant_id": "t1"},
            engine=engine,
            audit_sink=sink,
        )
    assert excinfo.value.code == ERR_INAD_INVALID_CONTRATO
    assert sink.calls == []  # refused BEFORE any audit emit — no record for a refused handoff


@pytest.mark.parametrize("tenant_id", [None, "", "   "])
def test_handoff_rescisao_fail_closed_blank_tenant_anchor(tenant_id: object) -> None:
    """GK MINOR F7 — the TENANT anchor is validated with the SHARED `non_blank`, like every other
    CANCEL-001 composer (`fraude.start_contratual`, the bridge's `_anchored`).

    This call site did not. A blank/whitespace/explicit-`None` tenant mints the degenerate key
    `CANCEL--C-1`: EVERY tenant's contract `C-1` collapses onto ONE business key, ONE dedup claim
    and — since GAP-D3-02 — ONE `EXCLUSIVE` mutual-exclusion token, so tenant A's in-flight
    rescisao review would gate tenant B's. `str(None)` is the truthy `"None"` and `"   "` is
    truthy, so plain truthiness let both through; `non_blank` is what refuses them.
    """
    engine = FakeCibSevenTransport()
    sink = FakeStartAuditSink()
    variables: dict[str, object] = {
        "decisao_inadimplencia": DECISAO_ENCAMINHAR_RESCISAO,
        "numero_contrato": "C-1",
    }
    if tenant_id is not None:
        variables["tenant_id"] = tenant_id

    with pytest.raises(InadimplenciaError) as excinfo:
        handoff_rescisao(variables, engine=engine, audit_sink=sink)

    assert excinfo.value.code == ERR_INAD_INVALID_CONTRATO
    assert sink.calls == [], "refused BEFORE any audit emit — no claim on a degenerate key"
    assert asyncio.run(engine.find_active_instance("CANCEL--C-1")) is None
    assert asyncio.run(engine.find_active_instance("CANCEL-None-C-1")) is None


def test_handoff_rescisao_explicit_none_tenant_is_refused_not_stringified() -> None:
    """The `str(None) == "None"` trap, pinned on its own: an explicit `None` must REFUSE, never
    mint `CANCEL-None-C-1`. Same rule the shared `non_blank` already enforced at the other two
    CANCEL composers."""
    engine = FakeCibSevenTransport()
    sink = FakeStartAuditSink()

    with pytest.raises(InadimplenciaError):
        handoff_rescisao(
            {
                "decisao_inadimplencia": DECISAO_ENCAMINHAR_RESCISAO,
                "tenant_id": None,
                "numero_contrato": "C-1",
            },
            engine=engine,
            audit_sink=sink,
        )

    assert sink.calls == []


def test_handoff_rescisao_transport_error_propagates() -> None:
    """A transport/engine error during the start propagates (transient -> engine retry -> incident),
    never a silent success. The audit record was already durably emitted (emit-before-effect):
    the retry re-emit dedups to the same chain link, so no double-audit on the eventual success."""
    sink = FakeStartAuditSink()
    with pytest.raises(CibSevenError):
        handoff_rescisao(
            {
                "decisao_inadimplencia": DECISAO_ENCAMINHAR_RESCISAO,
                "tenant_id": "t1",
                "numero_contrato": "C-1",
            },
            engine=_RaisingCibSevenTransport(),
            audit_sink=sink,
        )
    # Emit happened BEFORE the failing engine call — the audited-decision-without-effect direction
    # is the SAFE one (idempotent start + emit_once dedup make the retry converge).
    assert sink.dedup_keys == [start_dedup_key("t1", CANCEL_PROCESS_KEY, "CANCEL-t1-C-1")]


def test_handoff_rescisao_supplies_the_exclusive_gate_prerequisites() -> None:
    """GAP-D3-02 caller inventory, proven not asserted. SP-OP-CANCEL-001 is an `EXCLUSIVE`
    start-dedup family, so this handoff's two seams must satisfy `HistoryQueryingTransport` and
    `DedupReportingAuditSink` or the chokepoint refuses the start. The doubles used by every test
    in this file mirror the live daemon's seams (`FreshClientCibSevenTransport` /
    `FreshSinkAuditEmitter`), and this pins that they really do carry the capabilities — a double
    that quietly lost one would turn the whole suite green for the wrong reason."""
    assert is_strict_start_dedup(CANCEL_PROCESS_KEY) is True
    assert start_dedup_posture(CANCEL_PROCESS_KEY) is StartDedupPosture.EXCLUSIVE
    assert isinstance(FakeCibSevenTransport(), HistoryQueryingTransport)
    assert isinstance(FakeStartAuditSink(), DedupReportingAuditSink)
    # The PRODUCTION seams the worker daemon injects (`runtime/worker_runtime/service.py:787-790`).
    assert isinstance(FreshClientCibSevenTransport("http://engine.invalid"), HistoryQueryingTransport)
    assert isinstance(FreshSinkAuditEmitter("postgresql://x/y", "amh"), DedupReportingAuditSink)


def test_handoff_rescisao_fails_closed_behind_a_history_blind_engine_seam() -> None:
    """NEVER FALL BACK. A composition root whose engine seam cannot answer the engine-history
    question cannot gate an `EXCLUSIVE` CANCEL-001 start — so the handoff refuses outright rather
    than silently reverting to the TOCTOU-only path GAP-D3-02 closed. The refusal happens BEFORE
    the durable claim, so a mis-wired root leaves no orphan claim to wedge the contract."""
    engine = _HistoryBlindCibSevenTransport()
    sink = FakeStartAuditSink()

    with pytest.raises(StartDedupGateUnavailableError):
        handoff_rescisao(
            {
                "decisao_inadimplencia": DECISAO_ENCAMINHAR_RESCISAO,
                "tenant_id": "t1",
                "numero_contrato": "C-1",
            },
            engine=engine,
            audit_sink=sink,
        )

    assert engine.starts == [], "an un-gateable rescisao handoff must not start CANCEL-001"
    assert sink.calls == [], "the refusal must precede the durable claim (no orphan claim)"


def test_handoff_rescisao_fails_closed_behind_a_sink_that_cannot_report_dedup() -> None:
    """The sink half of the same rule: without `emit_once_status` the durable claim cannot be
    observed, so the gate cannot exist and the handoff must not start CANCEL-001."""

    class _HashOnlySink:
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def emit_once(self, record: Any, *, dedup_key: str) -> str:
            self.calls.append(dedup_key)
            return "hash"

    engine = FakeCibSevenTransport()
    sink = _HashOnlySink()

    with pytest.raises(StartDedupGateUnavailableError):
        handoff_rescisao(
            {
                "decisao_inadimplencia": DECISAO_ENCAMINHAR_RESCISAO,
                "tenant_id": "t1",
                "numero_contrato": "C-1",
            },
            engine=engine,
            audit_sink=sink,
        )

    assert sink.calls == []
    assert asyncio.run(engine.find_active_instance("CANCEL-t1-C-1")) is None


def test_handoff_rescisao_redelivery_returns_the_same_instance_never_a_second() -> None:
    """The `EXCLUSIVE` posture at the CALLER level: a re-delivered ENCAMINHAR_RESCISAO handoff
    (external-task lock expiry) reports the SAME CANCEL-001 instance, and the worker's own return
    dict stays honest — `handoff_executado` True with `cancel_already_existed` True, which is a
    true statement (a rescisao review IS open for this contract), never a swallowed decision."""
    engine = FakeCibSevenTransport()
    sink = FakeStartAuditSink()
    variables = {
        "decisao_inadimplencia": DECISAO_ENCAMINHAR_RESCISAO,
        "tenant_id": "t1",
        "numero_contrato": "C-909",
    }

    first = handoff_rescisao(dict(variables), engine=engine, audit_sink=sink)
    second = handoff_rescisao(dict(variables), engine=engine, audit_sink=sink)

    assert first["cancel_already_existed"] is False
    assert second["cancel_already_existed"] is True
    assert second["cancel_instance_id"] == first["cancel_instance_id"]
    assert second["handoff_executado"] is True
    # ONE dedup key across both deliveries -> one chain link (the durable sink's own invariant).
    assert set(sink.dedup_keys) == {start_dedup_key("t1", CANCEL_PROCESS_KEY, "CANCEL-t1-C-909")}


def test_handoff_rescisao_second_case_after_a_finished_cancel_is_not_swallowed() -> None:
    """THE ANTI-SWALLOW PIN AT THE CALLER. The first CANCEL-001 ended with the contract ALIVE (the
    human decided MANTER). A LATER delinquency cycle reaches ENCAMINHAR_RESCISAO again and mints
    the SAME business key — that human decision MUST reach the engine. Under a `PERMANENT` posture
    this returns `handoff_executado: True` while starting nothing: the decision silently dropped.
    `EXCLUSIVE` starts the second, real case."""
    engine = FakeCibSevenTransport()
    sink = FakeStartAuditSink()
    variables = {
        "decisao_inadimplencia": DECISAO_ENCAMINHAR_RESCISAO,
        "tenant_id": "t1",
        "numero_contrato": "C-909",
    }

    first = handoff_rescisao(dict(variables), engine=engine, audit_sink=sink)
    # The CANCEL-001 instance ends (End_ContratoMantido) — contract alive, key re-usable.
    engine.seed_instance(
        ProcessInstance(
            instance_id=first["cancel_instance_id"],
            process_key=CANCEL_PROCESS_KEY,
            business_key="CANCEL-t1-C-909",
            state="COMPLETED",
        )
    )

    second = handoff_rescisao(dict(variables), engine=engine, audit_sink=sink)

    assert second["cancel_already_existed"] is False, "the second, legitimate rescisao was swallowed"
    assert second["cancel_instance_id"] != ""
    assert asyncio.run(engine.find_active_instance("CANCEL-t1-C-909")) is not None


def test_handoff_rescisao_noop_for_other_decisao() -> None:
    """decisao != ENCAMINHAR_RESCISAO -> neutral no-op, never touches the engine (no engine seam)."""
    result = handoff_rescisao(
        {
            "decisao_inadimplencia": DECISAO_MANTER,
        }
    )
    assert result["handoff_executado"] is False


# ---------------------------------------------------------------
# GAP-INAD-1 — anti-dupla-terminacao cross-process CANCEL-001 query
# ---------------------------------------------------------------


_VALID_SUSPENSION_HUMAN_FIELDS: dict[str, Any] = {
    "decisao_inadimplencia": DECISAO_SUSPENDER,
    "responsavel_id": "juridico-001",
    "fundamentacao_contratual": "Art. 13 Lei 9656",
    "referencia_regulatoria": "RN 593",
    "comprovacao_notificacao_previa": "ref-notif-001",
    "comprovacao_periodo_minimo": "ref-periodo-001",
}


class _RaisingCibSevenTransport:
    """Test double whose engine queries ALWAYS raise — proves the fail-closed path (query error).

    `find_any_instance` is present (and equally raising) because SP-OP-CANCEL-001 is an
    `EXCLUSIVE` start-dedup family since GAP-D3-02: the chokepoint probes the injected transport
    for `HistoryQueryingTransport` and REFUSES the start outright when it is absent. A double
    without this method would therefore prove the fail-closed SEAM check, not the engine-error
    propagation this class exists for — and an unreachable engine is unreachable on both reads,
    so raising here is the faithful shape (`_HistoryBlindCibSevenTransport` below is the double
    for the missing-capability case)."""

    async def find_active_instance(self, business_key: str) -> ProcessInstance | None:
        raise CibSevenError(f"engine unreachable querying `{business_key}`")

    async def find_any_instance(self, business_key: str, *, process_key: str = "") -> ProcessInstance | None:
        raise CibSevenError(f"engine unreachable querying history for `{business_key}`")


class _HistoryBlindCibSevenTransport:
    """A transport that CANNOT answer "did an instance for this key EVER exist?".

    Structurally a `CibSevenTransport` but NOT a `HistoryQueryingTransport` (no
    `find_any_instance` attribute at all — the `runtime_checkable` probe is attribute-presence,
    so the method must be genuinely absent, not stubbed). This is the shape a composition root
    produces if a decorator drops the capability, which `tools/workers/cibseven_engine.py`'s own
    delegation guard exists to prevent."""

    def __init__(self) -> None:
        self.starts: list[str] = []

    async def find_active_instance(self, business_key: str) -> ProcessInstance | None:
        return None

    async def start_process_instance(
        self, process_key: str, business_key: str, variables: dict[str, Any]
    ) -> ProcessInstance:
        self.starts.append(business_key)
        return ProcessInstance(
            instance_id=f"blind-{business_key}",
            process_key=process_key,
            business_key=business_key,
            state="ACTIVE",
        )

    async def correlate_message(self, *a: Any, **k: Any) -> None:
        return None

    async def get_process_status(self, business_key: str) -> Any:
        raise NotImplementedError

    async def close(self) -> None:
        return None


class _RecordingHarness:
    """Minimal harness double capturing registered workers by topic (register_worker only)."""

    def __init__(self) -> None:
        self.workers: dict[str, Any] = {}

    def register_worker(self, worker: Any) -> None:
        self.workers[worker.topic] = worker


def _seed_active_cancel(fake: FakeCibSevenTransport, business_key: str) -> None:
    fake.seed_instance(
        ProcessInstance(
            instance_id=f"cancel-inst-{business_key}",
            process_key=CANCEL_PROCESS_KEY,
            business_key=business_key,
            state="ACTIVE",
        )
    )


def test_cancel_business_key_format_and_matricula_fallback() -> None:
    """Distinct CANCEL- prefix over the SAME contract identity; matricula fallback (individual plans)."""
    assert _cancel_business_key("t1", "C-123", "mat-9") == "CANCEL-t1-C-123"
    assert _cancel_business_key("t1", "", "mat-9") == "CANCEL-t1-mat-9"


def test_anti_dupla_terminacao_blocks_suspension_when_cancel_active() -> None:
    """THE anti-dupla-terminacao invariant (first-class): a live SP-OP-CANCEL-001 instance for the
    same contract makes resolve_facts resolve ja_em_rescisao_cancel=True, and the suspension guard
    then REFUSES the independent suspension — a contract already in rescisao is never also suspended."""
    fake = FakeCibSevenTransport()
    _seed_active_cancel(fake, "CANCEL-t1-C-123")

    facts = resolve_facts(
        {
            "tenant_id": "t1",
            "numero_contrato": "C-123",
            "competencias_em_aberto": ["2024-01", "2024-02"],
        },
        engine=fake,
    )
    # Invariant, asserted directly: the cross-process correlation is a resolved FACT == True.
    assert facts["ja_em_rescisao_cancel"] is True

    with pytest.raises(InadimplenciaError) as excinfo:
        register_contract_suspension({**_VALID_SUSPENSION_HUMAN_FIELDS, "numero_contrato": "C-123", **facts})
    assert excinfo.value.code == ERR_CONTRACT_SUSPENSION_NOT_HUMAN
    assert "ja_em_rescisao_cancel" in excinfo.value.message


def test_anti_dupla_terminacao_proceeds_when_no_active_cancel() -> None:
    """No live CANCEL-001 instance -> resolve_facts confirms ja_em_rescisao_cancel=False -> the
    legitimate human-decided suspension PROCEEDS."""
    fake = FakeCibSevenTransport()  # nothing seeded — engine positively confirms no active instance

    facts = resolve_facts({"tenant_id": "t1", "numero_contrato": "C-123"}, engine=fake)
    assert facts["ja_em_rescisao_cancel"] is False

    result = register_contract_suspension(
        {**_VALID_SUSPENSION_HUMAN_FIELDS, "numero_contrato": "C-123", **facts}
    )
    assert result["suspensao_registrada"] is True


def test_anti_dupla_terminacao_matricula_keyed_cancel_blocks() -> None:
    """Individual/familiar plans (no numero_contrato) key the query by matricula fallback."""
    fake = FakeCibSevenTransport()
    _seed_active_cancel(fake, "CANCEL-t1-mat-99")

    facts = resolve_facts({"tenant_id": "t1", "matricula_beneficiario": "mat-99"}, engine=fake)
    assert facts["ja_em_rescisao_cancel"] is True


def test_fail_closed_when_query_raises() -> None:
    """Query cannot be answered (engine/transport error) -> fail closed (block)."""
    facts = resolve_facts({"tenant_id": "t1", "numero_contrato": "C-9"}, engine=_RaisingCibSevenTransport())
    assert facts["ja_em_rescisao_cancel"] is True


def test_fail_closed_when_engine_seam_not_wired() -> None:
    """No engine seam injected -> correlation unconfirmable -> fail closed (block)."""
    facts = resolve_facts({"tenant_id": "t1", "numero_contrato": "C-9"})  # engine defaults to None
    assert facts["ja_em_rescisao_cancel"] is True


def test_fail_closed_when_contract_identity_missing() -> None:
    """No contract identity to key on -> cannot rule out a rescisao -> fail closed (block)."""
    fake = FakeCibSevenTransport()
    facts = resolve_facts({"tenant_id": "t1"}, engine=fake)
    assert facts["ja_em_rescisao_cancel"] is True


def test_register_suspension_fail_closed_when_fact_absent() -> None:
    """Defense in depth at the adverse boundary: register REFUSES when ja_em_rescisao_cancel was
    never resolved (absent) — 'inability to decide = do not suspend'. Only explicit False proceeds."""
    with pytest.raises(InadimplenciaError) as excinfo:
        register_contract_suspension(
            {**_VALID_SUSPENSION_HUMAN_FIELDS, "numero_contrato": "C-9"}  # ja_em_rescisao_cancel absent
        )
    assert excinfo.value.code == ERR_CONTRACT_SUSPENSION_NOT_HUMAN
    assert "ja_em_rescisao_cancel" in excinfo.value.message


def test_resolve_facts_default_engine_is_fail_closed_true() -> None:
    """resolve_facts now ALWAYS resolves ja_em_rescisao_cancel (never a silent pass-through)."""
    facts = resolve_facts({"numero_contrato": "C-1"})
    assert "ja_em_rescisao_cancel" in facts
    assert facts["ja_em_rescisao_cancel"] is True  # no engine wired -> fail closed


# ---------------------------------------------------------------
# B-2 — the guard must sweep EVERY derivable CANCEL business-key form
#
# THE DEFECT. `_query_ja_em_rescisao_cancel` used to query exactly one key:
# `CANCEL-{tenant}-{numero_contrato or matricula}`. But `fraude.start_contratual` and the
# notification bridge's `cancel.*` rule both mint `CANCEL-{tenant}-{numero_contrato}` with NO
# matricula fallback. For a contract carrying BOTH identifiers, an active CANCEL-001 started
# under the OTHER form was invisible to the guard, which then reported "no rescisao in flight"
# and let the independent suspension proceed — a double termination.
#
# These tests seed a live CANCEL instance under each form in turn, with BOTH identifiers present,
# and require the guard to find it either way. The `matricula`-form case is the one that fails
# against the pre-fix single-key query.
# ---------------------------------------------------------------


_BOTH_IDS: dict[str, Any] = {
    "tenant_id": "t1",
    "numero_contrato": "C-123",
    "matricula_beneficiario": "mat-99",
}


def test_b2_guard_finds_a_cancel_started_under_the_contract_form() -> None:
    """The form `fraude`/`notification_bridge` mint (no matricula fallback)."""
    fake = FakeCibSevenTransport()
    _seed_active_cancel(fake, "CANCEL-t1-C-123")
    assert resolve_facts(dict(_BOTH_IDS), engine=fake)["ja_em_rescisao_cancel"] is True


def test_b2_guard_finds_a_cancel_started_under_the_matricula_form() -> None:
    """THE B-2 REGRESSION. Both identifiers present, the live instance is keyed on the matricula
    form (what `inadimplencia`'s own handoff would mint for a plan whose contract number arrived
    later or blank). Pre-fix this returned False and the suspension proceeded alongside a live
    rescisao."""
    fake = FakeCibSevenTransport()
    _seed_active_cancel(fake, "CANCEL-t1-mat-99")
    assert resolve_facts(dict(_BOTH_IDS), engine=fake)["ja_em_rescisao_cancel"] is True


def test_b2_guard_blocks_the_suspension_end_to_end_under_the_matricula_form() -> None:
    """Not just the FACT: the adverse effect it guards is actually refused."""
    fake = FakeCibSevenTransport()
    _seed_active_cancel(fake, "CANCEL-t1-mat-99")
    facts = resolve_facts(dict(_BOTH_IDS), engine=fake)
    with pytest.raises(InadimplenciaError) as excinfo:
        register_contract_suspension({**_VALID_SUSPENSION_HUMAN_FIELDS, **_BOTH_IDS, **facts})
    assert excinfo.value.code == ERR_CONTRACT_SUSPENSION_NOT_HUMAN


def test_b2_guard_queries_every_derivable_form() -> None:
    """The sweep is exhaustive, not merely two-of-three by luck."""
    queried: list[str] = []

    class _RecordingTransport:
        async def find_active_instance(self, business_key: str) -> ProcessInstance | None:
            queried.append(business_key)
            return None

    assert resolve_facts(dict(_BOTH_IDS), engine=_RecordingTransport())["ja_em_rescisao_cancel"] is False
    assert queried == ["CANCEL-t1-C-123", "CANCEL-t1-mat-99"]


def test_b2_guard_deduplicates_identical_forms() -> None:
    """When both anchors are the same string there is one form, and one query — no wasted call."""
    queried: list[str] = []

    class _RecordingTransport:
        async def find_active_instance(self, business_key: str) -> ProcessInstance | None:
            queried.append(business_key)
            return None

    resolve_facts(
        {"tenant_id": "t1", "numero_contrato": "SAME", "matricula_beneficiario": "SAME"},
        engine=_RecordingTransport(),
    )
    assert queried == ["CANCEL-t1-SAME"]


def test_b2_guard_permits_only_when_no_form_has_a_live_instance() -> None:
    """The conservative direction has a floor: a genuinely clean contract still proceeds."""
    fake = FakeCibSevenTransport()
    _seed_active_cancel(fake, "CANCEL-t1-SOMEONE-ELSE")
    facts = resolve_facts(dict(_BOTH_IDS), engine=fake)
    assert facts["ja_em_rescisao_cancel"] is False
    assert (
        register_contract_suspension({**_VALID_SUSPENSION_HUMAN_FIELDS, **_BOTH_IDS, **facts})[
            "suspensao_registrada"
        ]
        is True
    )


def test_b2_guard_fails_closed_when_any_form_query_raises() -> None:
    """An error on the SECOND form must block just as an error on the first does — the guard
    never concludes 'no rescisao' from a set of queries it could not complete."""

    class _RaisesOnMatriculaForm:
        async def find_active_instance(self, business_key: str) -> ProcessInstance | None:
            if business_key.endswith("mat-99"):
                raise CibSevenError("engine unreachable")
            return None

    facts = resolve_facts(dict(_BOTH_IDS), engine=_RaisesOnMatriculaForm())
    assert facts["ja_em_rescisao_cancel"] is True


@pytest.mark.parametrize("modo", ["off", "scrub_only", "pseudo_keys"])
def test_b2_dual_form_sweep_is_unconditional_across_every_policy_mode(tmp_path: Any, modo: str) -> None:
    """(d) The B-2 fix is a DEFECT FIX, not a flag-gated feature: it holds in every mode.

    `pseudo_keys` additionally dual-reads the `beneficiario_pseudo_id` form — a superset, never a
    replacement — so an instance still live under a legacy form stays visible through the
    migration window.
    """
    from tests.support.privacy_policy import phi_key_mode

    fake = FakeCibSevenTransport()
    _seed_active_cancel(fake, "CANCEL-t1-mat-99")
    with phi_key_mode(tmp_path, modo):
        facts = resolve_facts({**_BOTH_IDS, "beneficiario_pseudo_id": "hk1_abc"}, engine=fake)
    assert facts["ja_em_rescisao_cancel"] is True


def test_pseudo_keys_mode_dual_reads_the_legacy_and_pseudo_forms(tmp_path: Any) -> None:
    """Under `pseudo_keys` the guard queries legacy AND pseudo forms, in that order."""
    from tests.support.privacy_policy import phi_key_mode

    queried: list[str] = []

    class _RecordingTransport:
        async def find_active_instance(self, business_key: str) -> ProcessInstance | None:
            queried.append(business_key)
            return None

    with phi_key_mode(tmp_path, "pseudo_keys"):
        resolve_facts({**_BOTH_IDS, "beneficiario_pseudo_id": "hk1_abc"}, engine=_RecordingTransport())
    assert queried == ["CANCEL-t1-C-123", "CANCEL-t1-mat-99", "CANCEL-t1-hk1_abc"]


# ---------------------------------------------------------------
# prepare_dossier — INSTRUCTS, never originates an adverse decision
# ---------------------------------------------------------------


def _flatten_values(obj: Any) -> set[Any]:
    out: set[Any] = set()
    if isinstance(obj, dict):
        for value in obj.values():
            out |= _flatten_values(value)
    elif isinstance(obj, (list, tuple, set)):
        for item in obj:
            out |= _flatten_values(item)
    else:
        out.add(obj)
    return out


def test_prepare_dossier_happy_path() -> None:
    result = prepare_dossier(
        {
            "numero_contrato": "C-123",
            "matricula_beneficiario": "pseudo-abc",
            "tipo_plano": "individual",
            "meses_inadimplencia": 3,
            "valor_total_devido_cents": 150000,
            "ja_em_rescisao_cancel": False,
        }
    )
    assert result["dossier_prepared"] is True
    assert result["dossier_ref"].startswith("dossier-inad-")
    assert result["numero_contrato"] == "C-123"
    # The summary INSTRUCTS the human with the pre-resolved facts.
    assert result["dossier_summary"]["meses_inadimplencia"] == 3
    assert result["dossier_summary"]["ja_em_rescisao_cancel"] is False


def test_prepare_dossier_no_adverse_origination() -> None:
    """No-adverse-origination guard: even when the INPUT carries an adverse decision, the dossier
    output originates NONE — no decisao_inadimplencia key, no SUSPENDER/RESCINDIR/ENCAMINHAR value."""
    result = prepare_dossier(
        {
            "numero_contrato": "C-123",
            "decisao_inadimplencia": DECISAO_SUSPENDER,  # adverse hint present in input
            "suspensao_registrada": True,
        }
    )
    assert "decisao_inadimplencia" not in result
    assert "suspensao_registrada" not in result
    adverse_values = {DECISAO_SUSPENDER, DECISAO_ENCAMINHAR_RESCISAO, "RESCINDIR"}
    assert adverse_values.isdisjoint(_flatten_values(result))


# ---------------------------------------------------------------
# notify_sla_risk — informational, never adverse
# ---------------------------------------------------------------


def test_notify_sla_risk_informational() -> None:
    result = notify_sla_risk({"numero_contrato": "C-123", "tenant_id": "t1"})
    assert result["sla_risk_notified"] is True
    assert result["grupo_alertado"] == "coordenacao-cobranca"
    assert result["numero_contrato"] == "C-123"


def test_notify_sla_risk_no_adverse_outcome() -> None:
    """The non-interruptive timer alert never produces or propagates an adverse decision."""
    result = notify_sla_risk({"numero_contrato": "C-123", "decisao_inadimplencia": DECISAO_SUSPENDER})
    assert "decisao_inadimplencia" not in result
    adverse_values = {DECISAO_SUSPENDER, DECISAO_ENCAMINHAR_RESCISAO, "RESCINDIR"}
    assert adverse_values.isdisjoint(_flatten_values(result))


# ---------------------------------------------------------------
# register_inadimplencia_workers — registration + engine seam wiring
# ---------------------------------------------------------------


def test_register_inadimplencia_workers_registers_new_topics() -> None:
    harness = _RecordingHarness()
    register_inadimplencia_workers(harness, None, dmn=FakeDmnTransport(), engine=FakeCibSevenTransport())
    topics = set(harness.workers)
    assert "operadora.inadimplencia.prepare_dossier" in topics
    assert "operadora.inadimplencia.notify_sla_risk" in topics
    assert "operadora.inadimplencia.resolve_facts" in topics
    assert len(topics) == 8  # resolve_facts, assess_status, calculate_purge, check_prior_notice,
    #                          prepare_dossier, register_contract_suspension, handoff_rescisao,
    #                          notify_sla_risk


def test_registered_resolve_facts_threads_engine_seam() -> None:
    """The engine seam is genuinely threaded into the REGISTERED resolve_facts worker (not just the
    bare function): dispatching it with an active CANCEL-001 instance yields the fail-closed True."""
    harness = _RecordingHarness()
    engine = FakeCibSevenTransport()
    _seed_active_cancel(engine, "CANCEL-t1-C-1")
    register_inadimplencia_workers(harness, None, dmn=FakeDmnTransport(), engine=engine)

    worker = harness.workers["operadora.inadimplencia.resolve_facts"]
    out = worker.execute({"tenant_id": "t1", "numero_contrato": "C-1"})
    assert out["ja_em_rescisao_cancel"] is True


def test_registered_resolve_facts_without_engine_fails_closed() -> None:
    """Composition root has not wired the engine seam yet -> registered resolve_facts fails closed."""
    harness = _RecordingHarness()
    register_inadimplencia_workers(harness, None, dmn=FakeDmnTransport())  # no engine seam
    worker = harness.workers["operadora.inadimplencia.resolve_facts"]
    out = worker.execute({"tenant_id": "t1", "numero_contrato": "C-1"})
    assert out["ja_em_rescisao_cancel"] is True


def test_registered_handoff_rescisao_threads_engine_seam() -> None:
    """The engine + audit_sink seams are genuinely threaded into the REGISTERED handoff_rescisao
    worker (not just the bare function): dispatching it really starts CANCEL-001 under the exact
    business key AND emits the fenced ADR-0007 start record through the threaded sink."""
    harness = _RecordingHarness()
    engine = FakeCibSevenTransport()
    sink = FakeStartAuditSink()
    register_inadimplencia_workers(harness, None, dmn=FakeDmnTransport(), engine=engine, audit_sink=sink)

    worker = harness.workers["operadora.inadimplencia.handoff_rescisao"]
    out = worker.execute(
        {
            "decisao_inadimplencia": DECISAO_ENCAMINHAR_RESCISAO,
            "tenant_id": "t1",
            "numero_contrato": "C-1",
        }
    )
    assert out["handoff_executado"] is True
    assert out["cancel_business_key"] == "CANCEL-t1-C-1"
    assert asyncio.run(engine.find_active_instance("CANCEL-t1-C-1")) is not None
    # The threaded sink received the fenced start record, exactly once, exact dedup key.
    assert sink.dedup_keys == [start_dedup_key("t1", CANCEL_PROCESS_KEY, "CANCEL-t1-C-1")]


def test_registered_handoff_rescisao_without_engine_fails_closed() -> None:
    """Composition root has not wired the engine seam -> registered handoff_rescisao fails closed
    (raises RuntimeError -> transient -> engine retry) rather than silently dropping the human's
    ENCAMINHAR_RESCISAO decision. RuntimeError is `_HARNESS_CLASSIFIED`, so FunctionWorker re-raises
    it unchanged (not reclassified)."""
    harness = _RecordingHarness()
    register_inadimplencia_workers(harness, None, dmn=FakeDmnTransport())  # no engine seam
    worker = harness.workers["operadora.inadimplencia.handoff_rescisao"]
    with pytest.raises(RuntimeError, match="engine seam"):
        worker.execute(
            {
                "decisao_inadimplencia": DECISAO_ENCAMINHAR_RESCISAO,
                "tenant_id": "t1",
                "numero_contrato": "C-1",
            }
        )


def test_registered_handoff_rescisao_without_audit_sink_fails_closed() -> None:
    """Engine wired but audit sink NOT wired -> registered handoff_rescisao fails closed BEFORE
    any engine effect (T-C2 fence co-requisite: an un-audited CANCEL-001 start is impossible).
    Same transient RuntimeError posture as the engine seam (`_HARNESS_CLASSIFIED`)."""
    harness = _RecordingHarness()
    engine = FakeCibSevenTransport()
    register_inadimplencia_workers(harness, None, dmn=FakeDmnTransport(), engine=engine)  # no sink
    worker = harness.workers["operadora.inadimplencia.handoff_rescisao"]
    with pytest.raises(RuntimeError, match="audit sink"):
        worker.execute(
            {
                "decisao_inadimplencia": DECISAO_ENCAMINHAR_RESCISAO,
                "tenant_id": "t1",
                "numero_contrato": "C-1",
            }
        )
    assert asyncio.run(engine.find_active_instance("CANCEL-t1-C-1")) is None  # no engine effect
