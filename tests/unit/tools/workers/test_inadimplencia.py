"""Unit tests for maezo.tools.workers.inadimplencia (SP-OP-INADIMPLENCIA-001).

TDD London School: tests verify the guard contracts from the SP-OP contract.
"""

import asyncio
from typing import Any

import pytest

from maezo.gateway.audit_postgres import AuditPersistenceError
from maezo.tools.mcp_cibseven.transport import (
    CibSevenError,
    FakeCibSevenTransport,
    ProcessInstance,
    start_dedup_key,
)
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
    """Test double whose engine query ALWAYS raises — proves the fail-closed path (query error)."""

    async def find_active_instance(self, business_key: str) -> ProcessInstance | None:
        raise CibSevenError(f"engine unreachable querying `{business_key}`")


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
