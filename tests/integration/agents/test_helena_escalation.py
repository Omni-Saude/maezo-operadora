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
from tests.support.dmn_first_hit import DMN_DIR, evaluate, read_live_table

from ._engine_helpers import (
    active_instances,
    candidate_groups,
    noop_events_publish,
    process_variables,
    wait_for_task,
)

pytestmark = pytest.mark.integration

_RUN_ID = uuid.uuid4().hex[:8]


async def _assert_falha_tecnica_roteada_pela_r6_com_severidade_nula(
    engine_client: httpx.AsyncClient, instance_id: str, task: dict[str, Any]
) -> None:
    """§Delta-3 (regressao P-12) — o que estas duas provas tem de dizer sobre a instancia VIVA.

    A regressao que elas pegaram: HEL-04 passou a emitir `severidade=None` na falha do
    classificador (o valor honesto — nao houve extracao de onde derivar), mas
    `escalation.py::_exigir_severidade` recusava fail-closed nos DOIS canais de notificacao, e o
    caso morria antes de `UT_TratarEscalonamento`: NENHUM humano via um caso que a maquina nao
    conseguiu ler. `wait_for_task` falhava com "no user task ever appeared".

    Reaparecer a User Task nao basta como prova — um `leve` fabricado a faria reaparecer tambem, e
    foi exatamente esse rotulo que HEL-04 removeu. Entao, alem da tarefa, exigimos:

    1. a variavel de processo `severidade` EXISTE e vale `null` (nunca `leve`, nunca ausente);
    2. o roteamento saiu da regra `r6` da `escalation_routing` — `prioridade`/`grupo_atendimento`
       sao lidos da DMN VIVA aqui (nunca digitados), e a DMN e' CODEOWNED e NAO foi tocada;
    3. o `candidateGroups` da User Task (`${roteamento.grupo_atendimento}`) e' o mesmo grupo da
       `r6`, ou seja quem foi paginado e' quem a tabela escolheu.
    """
    tabela = read_live_table(DMN_DIR / "escalation_routing.dmn")
    r6 = evaluate(tabela, {"motivo_categoria": "falha_tecnica", "severidade": None})
    assert r6.regra == "r6", f"a DMN viva nao roteia mais falha_tecnica/null por r6: {r6.regra}"

    variaveis = await process_variables(engine_client, instance_id)
    assert "severidade" in variaveis, (
        "`severidade` tem de EXISTIR na instancia (presente e nula), nao sumir do escopo"
    )
    assert variaveis["severidade"]["value"] is None, (
        "falha do classificador nao tem severidade a derivar: a variavel e' `null`, nunca `leve` "
        f"— o motor tem {variaveis['severidade']['value']!r}"
    )
    assert variaveis["motivo_categoria"]["value"] == "falha_tecnica"
    # Escritas de volta ao escopo pelo worker `notify_team` a partir da saida da DMN (`${roteamento.*}`)
    # — sua presenca prova que a tarefa de notificacao COMPLETOU em vez de recusar.
    assert variaveis["grupo_atendimento"]["value"] == r6.saidas["grupo_atendimento"]
    assert variaveis["prioridade"]["value"] == r6.saidas["prioridade"]
    assert variaveis["severidade"]["value"] != "leve"

    groups = await candidate_groups(engine_client, task["id"])
    assert groups == {r6.saidas["grupo_atendimento"]}, (
        f"falha_tecnica -> escalation_routing r6 -> {r6.saidas['prioridade']} "
        f"{r6.saidas['grupo_atendimento']}; got {groups!r}"
    )


@pytest.fixture(autouse=True)
async def _escalation_worker_probe(
    engine_base_url: str, audit_sink: Any, audit_tenant: str
) -> AsyncIterator[None]:
    """Services `operadora.escalation.*` + `operadora.events.publish` for the test using it
    (function-scoped — see module docstring for why). T1.10 wave: completions are
    emit-before-complete against the lane's REAL durable sink (a sink-less harness now
    correctly refuses to complete)."""
    transport = CibSevenWorkerTransport(engine_base_url, timeout=30.0)
    harness = WorkerHarness(
        transport,
        worker_id=f"it-helena-escalation-probe-{_RUN_ID}",
        tenant=audit_tenant,
        audit_sink=audit_sink,
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

    async def generate(
        self,
        prompt: str,
        *,
        phi: bool = False,
        agent_id: str | None = None,
        tenant_id: str | None = None,
        # CC-12 x integracao lote3 (LOTE3-INTEGRATION-FAKES-TASK-KIND): assinatura acompanha o
        # Protocol real (`runtime/inference::InferenceProvider.generate`), mesma especie do
        # defeito f1bc87f.
        task_kind: str | None = None,
    ) -> str:
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
    engine_base_url: str, engine_client: httpx.AsyncClient, audit_sink: Any
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
            "audit_sink": audit_sink,
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
    engine_base_url: str, engine_client: httpx.AsyncClient, audit_sink: Any
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
            "audit_sink": audit_sink,
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
    engine_base_url: str, engine_client: httpx.AsyncClient, audit_sink: Any
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
            "audit_sink": audit_sink,
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

    async def generate(
        self,
        prompt: str,
        *,
        phi: bool = False,
        agent_id: str | None = None,
        tenant_id: str | None = None,
        # CC-12 x integracao lote3 (LOTE3-INTEGRATION-FAKES-TASK-KIND): assinatura acompanha o
        # Protocol real (`runtime/inference::InferenceProvider.generate`), mesma especie do
        # defeito f1bc87f.
        task_kind: str | None = None,
    ) -> str:
        assert phi is True, "every Helena LLM call must be phi=True (ADR-0006/ADR-0017/T1.7)"
        raise RuntimeError("LLM provider unavailable")


async def test_malformed_classifier_json_escalates_falha_tecnica(
    engine_base_url: str, engine_client: httpx.AsyncClient, audit_sink: Any
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
            "audit_sink": audit_sink,
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
        await _assert_falha_tecnica_roteada_pela_r6_com_severidade_nula(engine_client, instance_id, task)
    finally:
        await dmn.close()
        await cibseven.close()


async def test_classifier_llm_exception_escalates_falha_tecnica(
    engine_base_url: str, engine_client: httpx.AsyncClient, audit_sink: Any
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

    graph = build(
        {
            "inference": _RaisingInference(),
            "dmn": dmn,
            "cibseven": cibseven,
            "whatsapp": whatsapp,
            # T-C2 fence: REQUIRED build dep (this 6th build site was missed on the fence branch —
            # caught by the wave's live lane run; real sink, like every other build here).
            "audit_sink": audit_sink,
        }
    )
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
        await _assert_falha_tecnica_roteada_pela_r6_com_severidade_nula(engine_client, instance_id, task)
    finally:
        await dmn.close()
        await cibseven.close()


async def test_cpf_bearing_field_value_never_reaches_engine_variables_live(
    engine_base_url: str, engine_client: httpx.AsyncClient, audit_sink: Any
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
            "audit_sink": audit_sink,
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
