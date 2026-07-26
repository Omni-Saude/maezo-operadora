"""Unit tests for maezo.tools.workers.nip — SP-OP-NIP-001.

TDD London School: tests exercise the external task contracts.
"""

import datetime as _dt

import pytest

from maezo.tools.workers.harness import FakeKafkaPublisher, FakeWorkerTransport, WorkerHarness
from maezo.tools.workers.nip import (
    NipInput,
    NipNegativaNotHumanError,
    NipProtocoloInvalidoError,
    NipResponseInput,
    _coerce_anchor_date_iso,
    assemble_response,
    handoff_ans_submit,
    handoff_ans_submit_entry,
    instruct_dossier_entry,
    notify_deadline_risk,
    notify_deadline_risk_entry,
    publish_completed,
    publish_completed_entry,
    register_nip_workers,
    submit_response_entry,
    submit_to_ans,
)

# ---------------------------------------------------------------------------
# assemble_response
# ---------------------------------------------------------------------------


def test_assemble_response() -> None:
    """assemble_response builds a response dossier."""
    from maezo.tools.workers.nip import NipClassificationResult, NipRoutingResult

    classification = NipClassificationResult()
    routing = NipRoutingResult()
    inp = NipInput(tenant_id="amh", numero_nip_ans="NIP-005", tema_nip="reembolso")

    result = assemble_response(classification, routing, inp)
    assert result["numero_nip_ans"] == "NIP-005"
    assert "dossie" in result


# ---------------------------------------------------------------------------
# submit_to_ans — GUARD tests
# ---------------------------------------------------------------------------


def test_submit_to_ans_guard_missing_revisor() -> None:
    """submit_to_ans raises if MANTER_NEGATIVA without revisor_id."""
    inp = NipResponseInput(
        decisao_nip="MANTER_NEGATIVA",
        fundamentacao_regulatoria="Fundamentacao XYZ",
        referencia_negativa_original="AUTH-001",
        texto_resposta_nip="Texto da resposta",
        revisor_id="",
    )
    with pytest.raises(NipNegativaNotHumanError) as exc:
        submit_to_ans(inp)
    assert "revisor_id" in str(exc.value)


def test_submit_to_ans_guard_missing_fundamentacao() -> None:
    """submit_to_ans raises if MANTER_NEGATIVA without fundamentacao_regulatoria."""
    inp = NipResponseInput(
        decisao_nip="MANTER_NEGATIVA",
        fundamentacao_regulatoria="",
        referencia_negativa_original="AUTH-001",
        texto_resposta_nip="Texto da resposta",
        revisor_id="juridico-001",
    )
    with pytest.raises(NipNegativaNotHumanError) as exc:
        submit_to_ans(inp)
    assert "fundamentacao_regulatoria" in str(exc.value)


def test_submit_to_ans_conceder_no_guard() -> None:
    """submit_to_ans allows CONCEDER without the MANTER_NEGATIVA guard."""
    inp = NipResponseInput(
        decisao_nip="CONCEDER",
        texto_resposta_nip="Resposta favoravel ao beneficiario",
    )
    result = submit_to_ans(inp)
    assert result["submitted"] is True
    assert result["decisao_nip"] == "CONCEDER"


def test_submit_to_ans_missing_texto() -> None:
    """submit_to_ans raises if texto_resposta_nip is empty."""
    inp = NipResponseInput(
        decisao_nip="CONCEDER",
        texto_resposta_nip="",
    )
    with pytest.raises(NipNegativaNotHumanError):
        submit_to_ans(inp)


# ---------------------------------------------------------------------------
# notify_deadline_risk (t2.5-p2b-nip-mechanical — BPMN-declared, previously missing)
# ---------------------------------------------------------------------------


def test_notify_deadline_risk_happy_path() -> None:
    """notify_deadline_risk is informational-only: no guard, no decision, UT stays open."""
    result = notify_deadline_risk(
        numero_nip_ans="NIP-010",
        tenant_id="amh",
        grupo_humano="juridico-regulatorio",
        sla_breach_task_name="UT_RevisaoJuridicaNip",
        event_topic_deadline_risk="agents.events.nip.deadline_risk",
    )
    assert result["deadline_risk_notified"] is True
    assert result["numero_nip_ans"] == "NIP-010"
    assert result["grupo_humano"] == "juridico-regulatorio"
    assert result["sla_breach_task_name"] == "UT_RevisaoJuridicaNip"


def test_notify_deadline_risk_never_alters_a_decision() -> None:
    """Invariant: notify_deadline_risk's output never carries a decision/adverse marker."""
    result = notify_deadline_risk(numero_nip_ans="NIP-011")
    assert "decisao_nip" not in result
    assert set(result.keys()) == {
        "deadline_risk_notified",
        "numero_nip_ans",
        "grupo_humano",
        "sla_breach_task_name",
        "event_topic_deadline_risk",
    }


def test_notify_deadline_risk_defaults_for_solicitar_info_reuse() -> None:
    """ST_SolicitarInfoNip reuses this same topic with NEITHER inputParameter set (BPMN comment
    "reusa o canal de notificacao regulatoria") — sla_breach_task_name/event_topic_deadline_risk
    must default gracefully rather than raise."""
    result = notify_deadline_risk(numero_nip_ans="NIP-012", tenant_id="amh")
    assert result["deadline_risk_notified"] is True
    assert result["sla_breach_task_name"] == ""
    assert result["event_topic_deadline_risk"] == ""


def test_notify_deadline_risk_entry_round_trips() -> None:
    variables = {
        "numero_nip_ans": "NIP-013",
        "tenant_id": "amh",
        "grupo_humano": "regulatorio-ans",
        "sla_breach_task_name": "UT_ElaborarRespostaNip",
        "event_topic_deadline_risk": "agents.events.nip.deadline_risk",
    }
    direct = notify_deadline_risk(**variables)
    assert notify_deadline_risk_entry(variables) == direct


def test_notify_deadline_risk_entry_ignores_kafka_seam() -> None:
    """Matches every other nip.py entry function: kafka is accepted but never published to
    (systemic Kafka-producer-wiring gap, out of scope for this worker)."""
    kafka = FakeKafkaPublisher()
    result = notify_deadline_risk_entry({"numero_nip_ans": "NIP-014"}, kafka=kafka)
    assert result["deadline_risk_notified"] is True
    assert kafka.published == []


# ---------------------------------------------------------------------------
# handoff_ans_submit
# ---------------------------------------------------------------------------


def test_handoff_ans_submit_valid() -> None:
    """handoff_ans_submit with protocolo_ans=None is valid."""
    result = handoff_ans_submit(
        numero_nip_ans="NIP-007",
        protocolo_ans=None,
        decisao_nip="CONCEDER",
    )
    assert result["handoff"] == "SP-OP-ANS-SUBMIT-001"
    assert result["origem_envio"] == "nip_filing"


def test_handoff_ans_submit_empty_protocolo_raises() -> None:
    """handoff_ans_submit with empty string raises ERR_NIP_PROTOCOLO_INVALIDO."""
    with pytest.raises(NipProtocoloInvalidoError):
        handoff_ans_submit(
            numero_nip_ans="NIP-008",
            protocolo_ans="   ",
        )


def test_handoff_ans_submit_with_protocolo() -> None:
    """handoff_ans_submit with valid protocolo_ans passes."""
    result = handoff_ans_submit(
        numero_nip_ans="NIP-009",
        protocolo_ans="ANSPROTO-123",
    )
    assert result["protocolo_ans"] == "ANSPROTO-123"


def test_handoff_ans_submit_defaults_tenant_id_to_blank() -> None:
    """T2.6-EB3 part 3: tenant_id defaults to "" fail-closed when the caller supplies none —
    never fabricated."""
    result = handoff_ans_submit(numero_nip_ans="NIP-010", protocolo_ans=None)
    assert result["tenant_id"] == ""


def test_handoff_ans_submit_carries_real_tenant_id() -> None:
    """T2.6-EB3 part 3 — SOURCE fix: the return dict now carries tenant_id (previously always
    absent, breaking the notification_bridge's downstream ANSSUB business-key/routing)."""
    result = handoff_ans_submit(
        numero_nip_ans="NIP-011",
        protocolo_ans=None,
        tenant_id="amh",
    )
    assert result["tenant_id"] == "amh"


def test_handoff_ans_submit_entry_forwards_tenant_id_from_process_variables() -> None:
    """T2.6-EB3 part 3: handoff_ans_submit_entry reads tenant_id off the process instance's own
    variables — the SAME place notify_deadline_risk_entry already reads it from."""
    result = handoff_ans_submit_entry({"numero_nip_ans": "NIP-012", "tenant_id": "amh"})
    assert result["tenant_id"] == "amh"
    assert result == handoff_ans_submit("NIP-012", None, "", "", "amh")


# ---------------------------------------------------------------------------
# publish_completed
# ---------------------------------------------------------------------------


def test_publish_completed_nip() -> None:
    """publish_completed emits domain event."""
    result = publish_completed(desfecho="resolvida_favoravel")
    assert result["published"] is True
    assert result["payload"]["desfecho"] == "resolvida_favoravel"


# ---------------------------------------------------------------------------
# Error types
# ---------------------------------------------------------------------------


def test_nip_negativa_not_human_is_permission_error() -> None:
    """NipNegativaNotHumanError must be a subclass of PermissionError."""
    assert issubclass(NipNegativaNotHumanError, PermissionError)


def test_nip_protocolo_invalido_is_value_error() -> None:
    """NipProtocoloInvalidoError must be a subclass of ValueError."""
    assert issubclass(NipProtocoloInvalidoError, ValueError)


# ---------------------------------------------------------------------------
# Dict-boundary entry functions (T1.2/ADR-0026 §2b) — round-trip vs calling the
# typed function directly; fail-closed marshalling on invalid/missing input.
# ---------------------------------------------------------------------------


def test_submit_response_entry_guards_missing_human_decision() -> None:
    """submit_response_entry raises the UNCHANGED NipNegativaNotHumanError guard."""
    with pytest.raises(NipNegativaNotHumanError):
        submit_response_entry({"decisao_nip": "MANTER_NEGATIVA"})


def test_submit_response_entry_happy_path() -> None:
    variables = {
        "decisao_nip": "MANTER_NEGATIVA",
        "revisor_id": "revisor-1",
        "fundamentacao_regulatoria": "RN 259",
        "referencia_negativa_original": "NEG-1",
        "texto_resposta_nip": "resposta final",
    }
    direct = submit_to_ans(NipResponseInput(**variables))
    assert submit_response_entry(variables) == direct


def test_handoff_ans_submit_entry_raises_on_blank_protocolo() -> None:
    """Absent (None) protocolo_ans is legitimate; blank/empty raises NipProtocoloInvalidoError
    (unchanged guard, GAP-NIP-6)."""
    with pytest.raises(NipProtocoloInvalidoError):
        handoff_ans_submit_entry({"numero_nip_ans": "NIP-1", "protocolo_ans": "  "})


def test_handoff_ans_submit_entry_allows_absent_protocolo() -> None:
    result = handoff_ans_submit_entry({"numero_nip_ans": "NIP-1"})
    assert result == handoff_ans_submit("NIP-1", None, "", "")


def test_publish_completed_entry_round_trips_publish_completed() -> None:
    variables = {"event_type": "nip.completed", "desfecho": "resolvida_favoravel"}
    assert publish_completed_entry(variables) == publish_completed(
        event_type="nip.completed", payload={}, desfecho="resolvida_favoravel"
    )


# ---------------------------------------------------------------------------
# register_nip_workers — registry/drift coverage (t2.5-p2b-nip-mechanical)
# ---------------------------------------------------------------------------

_BPMN_NIP_TOPICS = frozenset(
    {
        "operadora.nip.instruct_dossier",
        "operadora.nip.submit_response",
        "operadora.nip.notify_deadline_risk",
        "operadora.nip.handoff_ans_submit",
        "operadora.nip.publish_completed",
    }
)


def test_register_nip_workers_matches_bpmn_topics_exactly() -> None:
    """Registry coverage (ADR-0026 test strategy, mirrors cancel.py's own drift-guard unit test):
    the registered `operadora.nip.*` topic set equals EXACTLY the 4 BPMN-declared topics
    (instruct_dossier/submit_response/notify_deadline_risk/handoff_ans_submit) plus
    `publish_completed` (KEPT — registry-completeness convention, mirrors
    inadimplencia.py's assess_status/calculate_purge; folds into the generic
    operadora.events.publish per BPMN) — no gap and no orphan."""
    harness = WorkerHarness(FakeWorkerTransport(), worker_id="test-worker")
    register_nip_workers(harness, FakeKafkaPublisher())
    nip_topics = {t for t in harness.registered_topics if t.startswith("operadora.nip.")}
    assert nip_topics == _BPMN_NIP_TOPICS


def test_register_nip_workers_does_not_register_orphan_topics() -> None:
    """t2.5-p2b-nip-mechanical: classify_nip/route_nip (superseded by the native DMN
    businessRuleTasks BRT_Classificacao/BRT_Roteamento — camunda:decisionRef="nip_classification"/
    "nip_routing") and review_juridico/notify_beneficiario (no BPMN consumer at all — zero hits
    anywhere in spec/) must never be registered."""
    harness = WorkerHarness(FakeWorkerTransport(), worker_id="test-worker")
    register_nip_workers(harness, FakeKafkaPublisher())
    assert "operadora.nip.classify_nip" not in harness.registered_topics
    assert "operadora.nip.route_nip" not in harness.registered_topics
    assert "operadora.nip.review_juridico" not in harness.registered_topics
    assert "operadora.nip.notify_beneficiario" not in harness.registered_topics


# ---------------------------------------------------------------------------
# GAP-NIP-1 anchor-date fail-safe (data_recebimento_nip_iso -> BRT_NipSla FEEL).
# The nip_sla DMN feeds this anchor into FEEL `date and time(anchor + "T00:00:00")`
# NATIVELY in BRT_NipSla, which runs BEFORE any human task — a malformed anchor
# faulted the businessRuleTask into an incident. instruct_dossier_entry (the only
# worker before BRT_NipSla) now coerces-or-routes-to-human. Pure input validation.
# ---------------------------------------------------------------------------


def _today_iso() -> str:
    return _dt.datetime.now(_dt.UTC).date().isoformat()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2026-01-15", "2026-01-15"),  # bare ISO date — canonical passthrough
        ("  2026-01-15  ", "2026-01-15"),  # surrounding whitespace tolerated
        ("2026-01-15T10:30:00", "2026-01-15"),  # ISO datetime — date part only (no doubled T00:00)
        ("2026-01-15T10:30:00+00:00", "2026-01-15"),  # ISO datetime w/ tz — date part only
    ],
)
def test_coerce_anchor_date_iso_valid_canonicalizes(raw: str, expected: str) -> None:
    assert _coerce_anchor_date_iso(raw) == expected


def test_coerce_anchor_date_iso_accepts_native_date_and_datetime() -> None:
    assert _coerce_anchor_date_iso(_dt.date(2026, 1, 15)) == "2026-01-15"
    assert _coerce_anchor_date_iso(_dt.datetime(2026, 1, 15, 9, 0, tzinfo=_dt.UTC)) == "2026-01-15"


@pytest.mark.parametrize(
    "raw",
    ["not-a-date", "15/01/2026", "2026-1-5", "", "   ", "2026-13-45", None, 20260115, []],
)
def test_coerce_anchor_date_iso_rejects_malformed(raw: object) -> None:
    """Anything not a well-formed ISO date -> None (the caller substitutes a flagged fallback)."""
    assert _coerce_anchor_date_iso(raw) is None


def test_instruct_dossier_entry_valid_anchor_passes_through_flagged_valid() -> None:
    out = instruct_dossier_entry(
        {"numero_nip_ans": "NIP-1", "tema_nip": "reembolso", "data_recebimento_nip_iso": "2026-01-15"}
    )
    assert out["data_recebimento_nip_iso"] == "2026-01-15"
    assert out["data_recebimento_nip_iso_valida"] is True


def test_instruct_dossier_entry_datetime_anchor_stripped_to_date() -> None:
    """Latent footgun guard: an ISO datetime anchor would become `...T00:00:00T00:00:00` in the
    DMN's FEEL and crash — the entry strips it to the bare date."""
    out = instruct_dossier_entry(
        {"numero_nip_ans": "NIP-1", "data_recebimento_nip_iso": "2026-01-15T00:00:00"}
    )
    assert out["data_recebimento_nip_iso"] == "2026-01-15"
    assert out["data_recebimento_nip_iso_valida"] is True


@pytest.mark.parametrize("raw", ["not-a-date", "15/01/2026", ""])
def test_instruct_dossier_entry_malformed_anchor_routes_to_human_with_safe_fallback(raw: str) -> None:
    """Malformed/absent anchor: FEEL-safe fallback (processing date) + valida=False flag, so
    BRT_NipSla never faults and the human review task sees the substitution. NO adverse decision."""
    out = instruct_dossier_entry(
        {"numero_nip_ans": "NIP-1", "tema_nip": "x", "data_recebimento_nip_iso": raw}
    )
    assert out["data_recebimento_nip_iso"] == _today_iso()
    assert out["data_recebimento_nip_iso_valida"] is False
    # the substituted anchor is always FEEL-parseable (the whole point of the fail-safe)
    _dt.date.fromisoformat(out["data_recebimento_nip_iso"])


def test_instruct_dossier_entry_absent_anchor_key_routes_to_human() -> None:
    """The anchor process variable entirely absent (not just blank) is handled identically."""
    out = instruct_dossier_entry({"numero_nip_ans": "NIP-1", "tema_nip": "x"})
    assert out["data_recebimento_nip_iso"] == _today_iso()
    assert out["data_recebimento_nip_iso_valida"] is False
