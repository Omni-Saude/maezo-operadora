"""Helena E2E acceptance (T1.11, defect B6 — G1 gate): WhatsApp message -> Helena -> red-flag
-> SP-OP-ESCALATION-001 process instance ACTIVE in the REAL engine, correct business key +
candidate group (ADR-0011: never a mocked engine here).

Runs Helena's REAL graph (`agents.helena.graph.build`) with the REAL `CibSevenDmnTransport`
(engine-side `triage_redflag_*` evaluation, T1.5) and the REAL `CibSevenHttpTransport` (starting
`SP-OP-ESCALATION-001`, T1.11) against the compose engine. The LLM is a deterministic in-file
fake (not `llm_live` — this suite's acceptance is "the real engine reacts correctly to Helena's
graph", not "a real LLM classifies correctly"; see `pyproject.toml`'s `llm_live` marker for that
separate, API-key-gated concern) and the WhatsApp send is a fake (no real WABA network call in
this build — `dispatch.HelenaDispatcher`'s unit tests cover that wiring; here we assert the
process-instance/task outcome directly against the engine).

If the engine is unreachable, every test SKIPS via the session-scoped `_skip_if_engine_unreachable`
autouse fixture (`tests/integration/conftest.py`) — never a silent pass, never a fabricated
result (constraint 3).

WORKER PROBE (function-scoped, autouse): reaching `UT_TratarEscalonamento` requires the BPMN's
own `ST_PublishRequested` (`operadora.events.publish`) and `ST_NotificarTime`
(`operadora.escalation.notify_team`) external tasks to be serviced first — this suite runs the
REAL, UNMODIFIED `register_escalation_workers` (`tools/workers/escalation.py`) plus the test-only
`operadora.events.publish` no-op (see `_engine_helpers.py`'s docstring for why that topic has no
production worker yet) through a lightweight `WorkerHarness`/`CibSevenWorkerTransport` probe,
mirroring `tests/integration/test_worker_runtime_spine.py`'s own pattern. Deliberately
FUNCTION-scoped (not module-scoped): `pytest-asyncio`'s default `asyncio_default_test_loop_scope
= function` gives every test its own event loop, and a module-scoped async fixture's background
`asyncio.create_task` would be bound to whichever loop was active at fixture setup — live-
reproduced as a ~3-minute stall (the task effectively never got scheduled against the test's own
loop). Function scope guarantees the probe's background task runs on the SAME loop as the test
awaiting its effects.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

from maezo.agents.helena.graph import build
from maezo.tools.mcp_cibseven.transport import CibSevenHttpTransport
from maezo.tools.workers.dmn_transport import CibSevenDmnTransport
from maezo.tools.workers.escalation import register_escalation_workers
from maezo.tools.workers.harness import CibSevenWorkerTransport, WorkerHarness
from tests.support.audit_fakes import FakeStartAuditSink

from ._engine_helpers import active_instances, candidate_groups, noop_events_publish, wait_for_task

pytestmark = pytest.mark.integration

_RUN_ID = uuid.uuid4().hex[:8]


@pytest.fixture(autouse=True)
async def _escalation_worker_probe(engine_base_url: str) -> AsyncIterator[None]:
    """Services `operadora.escalation.*` + `operadora.events.publish` for the test using it
    (function-scoped — see module docstring for why)."""
    transport = CibSevenWorkerTransport(engine_base_url, timeout=30.0)
    harness = WorkerHarness(
        transport,
        worker_id=f"it-helena-escalation-probe-{_RUN_ID}",
        async_response_timeout_ms=5_000,
        # Fast local polling for test turnaround — the DEFAULT (5s) idle-backoff cadence is tuned
        # for production (avoid hammering the engine); against a local compose engine this is
        # deliberately more aggressive so `wait_for_task`'s budget isn't spent on backoff sleeps.
        poll_interval_ms=250,
    )
    register_escalation_workers(harness)
    harness.register("operadora.events.publish", noop_events_publish)
    run_task = asyncio.create_task(harness.run(), name="helena-it-escalation-probe")
    try:
        yield
    finally:
        run_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await run_task
        await transport.close()


class _FakeInference:
    """Deterministic, in-order fake — see module docstring for why no real LLM is needed here."""

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)

    async def generate(self, prompt: str, *, phi: bool = False) -> str:
        assert phi is True, "every Helena LLM call must be phi=True (ADR-0006/ADR-0017/T1.7)"
        return self._responses.pop(0) if self._responses else ""


class _FakeWhatsAppSender:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    async def send(self, to_hash: str, text: str) -> dict[str, Any]:
        self.sent.append((to_hash, text))
        return {"ok": True}


def _classify_json(**overrides: Any) -> str:
    base = {
        "intent": "information",
        "population": "none",
        "psychosocial_risk": False,
        "sintoma_codigo": None,
        "intensidade": "desconhecida",
    }
    base.update(overrides)
    return json.dumps(base)


async def test_red_flag_message_starts_escalation_with_correct_business_key_and_group(
    engine_base_url: str, engine_client: httpx.AsyncClient
) -> None:
    """(a) simulated red-flag input -> SP-OP-ESCALATION-001 instance ACTIVE, business key
    `ESC-{tenant}-{conversation_id}`, candidate group `plantao-clinico` (motivo_categoria=
    red_flag_clinico, severidade=grave -> escalation_routing r1, verified via engine REST)."""
    conversation_id = f"wa:amh:t111-redflag-{_RUN_ID}"
    business_key = f"ESC-amh-{conversation_id}"

    dmn = CibSevenDmnTransport(engine_base_url, timeout=30.0)
    cibseven = CibSevenHttpTransport(engine_base_url, timeout=30.0)
    whatsapp = _FakeWhatsAppSender()
    inference = _FakeInference(
        [
            _classify_json(
                intent="symptom", population="adult", sintoma_codigo="dor_toracica", intensidade="grave"
            ),
            "Resumo: beneficiario relata dor toracica intensa; encaminhado como emergencia.",
            "Um profissional de saude vai entrar em contato imediatamente. Se piorar, procure "
            "o pronto-socorro mais proximo.",
        ]
    )

    graph = build(
        {
            "inference": inference,
            "dmn": dmn,
            "cibseven": cibseven,
            "whatsapp": whatsapp,
            "audit_sink": FakeStartAuditSink(),
        }
    )
    compiled = graph.compile()

    try:
        result = await compiled.ainvoke(
            {
                "tenant_id": "amh",
                "conversation_id": conversation_id,
                "canal": "whatsapp",
                "beneficiario_pseudo_id": f"pseudo-{_RUN_ID}",
                "message_body": "estou com uma dor muito forte no peito",
            }
        )

        assert result["next_kind"] == "escalate"
        assert result["escalation_motivo"] == "red_flag_clinico"
        assert result["escalation_severidade"] == "grave"
        assert result["escalation_started"] is True
        assert result["escalation_business_key"] == business_key
        assert whatsapp.sent, "Helena must tell the beneficiary a human is taking over"

        actives = await active_instances(engine_client, business_key)
        assert actives, (
            f"Helena should have originated SP-OP-ESCALATION-001 for business_key={business_key!r} "
            f"— none found active in the engine"
        )
        instance_id = str(actives[0]["id"])

        task = await wait_for_task(engine_client, instance_id)
        groups = await candidate_groups(engine_client, task["id"])
        assert groups == {"plantao-clinico"}, (
            f"expected candidate group 'plantao-clinico' (red_flag_clinico/grave -> escalation_routing "
            f"r1 -> P1), got {groups!r}"
        )
    finally:
        await dmn.close()
        await cibseven.close()


async def test_non_red_flag_message_never_starts_escalation(
    engine_base_url: str, engine_client: httpx.AsyncClient
) -> None:
    """(b) non-red-flag input -> Helena's `inform` path — no SP-OP-ESCALATION-001 instance is
    ever started for this conversation's business key."""
    conversation_id = f"wa:amh:t111-inform-{_RUN_ID}"
    business_key = f"ESC-amh-{conversation_id}"

    dmn = CibSevenDmnTransport(engine_base_url, timeout=30.0)
    cibseven = CibSevenHttpTransport(engine_base_url, timeout=30.0)
    whatsapp = _FakeWhatsAppSender()
    inference = _FakeInference(
        [
            _classify_json(intent="symptom", population="adult", sintoma_codigo="febre", intensidade="leve"),
            "Febre leve costuma melhorar com repouso; se persistir por mais de 2 dias, "
            "entre em contato novamente.",
        ]
    )

    graph = build(
        {
            "inference": inference,
            "dmn": dmn,
            "cibseven": cibseven,
            "whatsapp": whatsapp,
            "audit_sink": FakeStartAuditSink(),
        }
    )
    compiled = graph.compile()

    try:
        result = await compiled.ainvoke(
            {
                "tenant_id": "amh",
                "conversation_id": conversation_id,
                "canal": "whatsapp",
                "beneficiario_pseudo_id": f"pseudo-{_RUN_ID}-b",
                "message_body": "estou com uma febre baixa desde ontem",
            }
        )

        assert result["next_kind"] == "inform"
        assert result.get("escalation_started") is not True
        assert result["response_kind"] == "inform"
        assert whatsapp.sent

        actives = await active_instances(engine_client, business_key)
        assert actives == [], (
            f"non-red-flag turn must NEVER start SP-OP-ESCALATION-001 — found active instance(s) "
            f"for business_key={business_key!r}: {actives}"
        )
    finally:
        await dmn.close()
        await cibseven.close()


async def test_psychosocial_risk_always_escalates_even_when_intent_looks_administrative(
    engine_base_url: str, engine_client: httpx.AsyncClient
) -> None:
    """Gatilho 5 (always active): psychosocial risk escalates regardless of `intent`, verified
    against the REAL `triage_redflag_mental_health` table + `escalation_routing` (motivo=
    risco_psicossocial, severidade=grave -> r2 -> plantao-clinico)."""
    conversation_id = f"wa:amh:t111-psy-{_RUN_ID}"
    business_key = f"ESC-amh-{conversation_id}"

    dmn = CibSevenDmnTransport(engine_base_url, timeout=30.0)
    cibseven = CibSevenHttpTransport(engine_base_url, timeout=30.0)
    whatsapp = _FakeWhatsAppSender()
    inference = _FakeInference(
        [
            _classify_json(intent="information", psychosocial_risk=True),
            "Resumo: sinal de risco psicossocial identificado; encaminhado com prioridade maxima.",
            "Um profissional vai falar com voce agora. Se estiver em perigo imediato, ligue 192.",
        ]
    )

    graph = build(
        {
            "inference": inference,
            "dmn": dmn,
            "cibseven": cibseven,
            "whatsapp": whatsapp,
            "audit_sink": FakeStartAuditSink(),
        }
    )
    compiled = graph.compile()

    try:
        result = await compiled.ainvoke(
            {
                "tenant_id": "amh",
                "conversation_id": conversation_id,
                "canal": "whatsapp",
                "beneficiario_pseudo_id": f"pseudo-{_RUN_ID}-c",
                "message_body": "nao aguento mais, quero desistir de tudo",
            }
        )

        assert result["escalation_motivo"] == "risco_psicossocial"
        assert result["escalation_severidade"] == "grave"
        assert result["escalation_started"] is True

        actives = await active_instances(engine_client, business_key)
        assert actives
        instance_id = str(actives[0]["id"])
        task = await wait_for_task(engine_client, instance_id)
        groups = await candidate_groups(engine_client, task["id"])
        assert groups == {"plantao-clinico"}
    finally:
        await dmn.close()
        await cibseven.close()


# ---------------------------------------------------------------------------
# R1 cycle-1 regression (live): classifier failure is fail-CLOSED — escalate falha_tecnica,
# never inform. These are the verifier's two exact live-reproduced cases; pre-fix, BOTH routed
# to `inform` with zero engine instances started.
# ---------------------------------------------------------------------------


class _RaisingInference:
    """LLM seam that always raises — the verifier's 'LLM exception' live case. `phi=True` is
    still asserted on entry so the PHI-flag discipline check covers this path too."""

    async def generate(self, prompt: str, *, phi: bool = False) -> str:
        assert phi is True, "every Helena LLM call must be phi=True (ADR-0006/ADR-0017/T1.7)"
        raise RuntimeError("LLM provider unavailable")


async def test_malformed_classifier_json_escalates_falha_tecnica(
    engine_base_url: str, engine_client: httpx.AsyncClient
) -> None:
    """Verifier live case 1: malformed classify JSON + 'não consigo respirar' -> a REAL
    SP-OP-ESCALATION-001 instance (motivo=falha_tecnica -> escalation_routing r6 -> P3
    atendimento-humano), never inform."""
    conversation_id = f"wa:amh:t111-clf-badjson-{_RUN_ID}"
    business_key = f"ESC-amh-{conversation_id}"

    dmn = CibSevenDmnTransport(engine_base_url, timeout=30.0)
    cibseven = CibSevenHttpTransport(engine_base_url, timeout=30.0)
    whatsapp = _FakeWhatsAppSender()
    inference = _FakeInference(
        [
            "this is {not valid json at all",  # classify -> unparseable -> falha_tecnica
            "Resumo tecnico: classificador indisponivel; encaminhado para atendimento humano.",
            "Tivemos um problema tecnico ao processar sua mensagem. Um atendente humano vai "
            "continuar o atendimento.",
        ]
    )

    graph = build(
        {
            "inference": inference,
            "dmn": dmn,
            "cibseven": cibseven,
            "whatsapp": whatsapp,
            "audit_sink": FakeStartAuditSink(),
        }
    )
    compiled = graph.compile()

    try:
        result = await compiled.ainvoke(
            {
                "tenant_id": "amh",
                "conversation_id": conversation_id,
                "canal": "whatsapp",
                "beneficiario_pseudo_id": f"pseudo-{_RUN_ID}-clf1",
                "message_body": "não consigo respirar",
            }
        )

        assert result["next_kind"] == "escalate", (
            f"pre-fix fail-open regression: classifier failure must escalate, got "
            f"next_kind={result.get('next_kind')!r}"
        )
        assert result["escalation_motivo"] == "falha_tecnica"
        assert result["escalation_started"] is True
        assert "unparseable JSON" in result["error"]
        assert whatsapp.sent

        actives = await active_instances(engine_client, business_key)
        assert actives, (
            f"classifier failure must start SP-OP-ESCALATION-001 for business_key={business_key!r} "
            f"— none found active (the pre-fix behavior: zero instances)"
        )
        instance_id = str(actives[0]["id"])
        task = await wait_for_task(engine_client, instance_id)
        groups = await candidate_groups(engine_client, task["id"])
        assert groups == {"atendimento-humano"}, (
            f"falha_tecnica -> escalation_routing r6 -> P3 atendimento-humano; got {groups!r}"
        )
    finally:
        await dmn.close()
        await cibseven.close()


async def test_classifier_llm_exception_escalates_falha_tecnica(
    engine_base_url: str, engine_client: httpx.AsyncClient
) -> None:
    """Verifier live case 2: LLM exception + 'dor no peito muito forte' -> a REAL
    SP-OP-ESCALATION-001 instance, never inform. The LLM is fully down for the whole turn
    (classify, resumo, respond) — resumo/respond degrade to canned fail-safe text while the
    escalation still starts."""
    conversation_id = f"wa:amh:t111-clf-exc-{_RUN_ID}"
    business_key = f"ESC-amh-{conversation_id}"

    dmn = CibSevenDmnTransport(engine_base_url, timeout=30.0)
    cibseven = CibSevenHttpTransport(engine_base_url, timeout=30.0)
    whatsapp = _FakeWhatsAppSender()

    graph = build({"inference": _RaisingInference(), "dmn": dmn, "cibseven": cibseven, "whatsapp": whatsapp})
    compiled = graph.compile()

    try:
        result = await compiled.ainvoke(
            {
                "tenant_id": "amh",
                "conversation_id": conversation_id,
                "canal": "whatsapp",
                "beneficiario_pseudo_id": f"pseudo-{_RUN_ID}-clf2",
                "message_body": "dor no peito muito forte",
            }
        )

        assert result["next_kind"] == "escalate"
        assert result["escalation_motivo"] == "falha_tecnica"
        assert result["escalation_started"] is True
        assert "classify LLM call failed" in result["error"]
        assert whatsapp.sent, "canned fail-safe reply must still reach the beneficiary"

        actives = await active_instances(engine_client, business_key)
        assert actives
        instance_id = str(actives[0]["id"])
        task = await wait_for_task(engine_client, instance_id)
        groups = await candidate_groups(engine_client, task["id"])
        assert groups == {"atendimento-humano"}
    finally:
        await dmn.close()
        await cibseven.close()


async def test_cpf_bearing_field_value_never_reaches_engine_variables_live(
    engine_base_url: str, engine_client: httpx.AsyncClient
) -> None:
    """R1 cycle-2 regression (leak, the verifier's exact live probe): the LLM copies a
    beneficiary-typed CPF into `sintoma_codigo` — schema-invalid -> escalate falha_tecnica —
    and the REAL engine instance's process variables must contain NO fragment of the offending
    value, only the class token. Pre-fix, `_short()`'s `repr(value)[:80]` shipped the CPF
    verbatim into the engine var `resumo_contexto` (verifier's instance c0cbc6d2...)."""
    leaked_value = "CPF 123.456.789-00 dor"
    conversation_id = f"wa:amh:t111-clf-leak-{_RUN_ID}"
    business_key = f"ESC-amh-{conversation_id}"

    dmn = CibSevenDmnTransport(engine_base_url, timeout=30.0)
    cibseven = CibSevenHttpTransport(engine_base_url, timeout=30.0)
    whatsapp = _FakeWhatsAppSender()
    inference = _FakeInference(
        [
            _classify_json(intent="symptom", population="adult", sintoma_codigo=leaked_value),
            "Resumo tecnico: classificador produziu saida invalida; encaminhado para atendimento humano.",
            "Tivemos um problema tecnico ao processar sua mensagem. Um atendente humano vai "
            "continuar o atendimento.",
        ]
    )

    graph = build(
        {
            "inference": inference,
            "dmn": dmn,
            "cibseven": cibseven,
            "whatsapp": whatsapp,
            "audit_sink": FakeStartAuditSink(),
        }
    )
    compiled = graph.compile()

    try:
        result = await compiled.ainvoke(
            {
                "tenant_id": "amh",
                "conversation_id": conversation_id,
                "canal": "whatsapp",
                "beneficiario_pseudo_id": f"pseudo-{_RUN_ID}-clf3",
                "message_body": "qualquer coisa",
            }
        )

        assert result["next_kind"] == "escalate"
        assert result["escalation_motivo"] == "falha_tecnica"
        assert result["escalation_started"] is True
        assert "non_allowlisted_sintoma_codigo" in result["error"]

        actives = await active_instances(engine_client, business_key)
        assert actives
        instance_id = str(actives[0]["id"])

        # THE leak assertion: fetch the REAL engine process variables and prove no fragment of
        # the offending value is present anywhere — only the class token.
        resp = await engine_client.get(f"/process-instance/{instance_id}/variables")
        resp.raise_for_status()
        engine_vars: dict[str, Any] = resp.json()
        serialized = json.dumps(engine_vars, ensure_ascii=False, default=str)
        for fragment in ("123.456.789-00", "123.456.789", "456.789", "CPF"):
            assert fragment not in serialized, (
                f"ENGINE process variables leaked a fragment of the offending field value "
                f"({fragment!r}) for instance {instance_id}: {serialized}"
            )
        resumo = engine_vars.get("resumo_contexto", {}).get("value", "")
        assert "non_allowlisted_sintoma_codigo" in resumo, (
            f"resumo_contexto must carry the class token only; got {resumo!r}"
        )
    finally:
        await dmn.close()
        await cibseven.close()
