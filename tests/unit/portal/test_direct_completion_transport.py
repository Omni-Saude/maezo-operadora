"""INTERIM completion transport (DL-0049): the bundle guard, the wire body, the error posture.

Synthetic HTTP and synthetic native responses. Nothing here is a qualified engine, a real
`engine-rest` deployment or an audit chain — what it proves is the custody around the one write
this Q2 read transport performs, the exact bytes it puts on the wire, and the rule that an
inconclusive answer is UNCERTAIN rather than a rollback.
"""

import json
from dataclasses import FrozenInstanceError

import httpx
import pytest
from tests.unit.gateway.human.test_gateway import SCOPE
from tests.unit.portal.test_engine_queue_adapters import bundle_fixture

from maezo.gateway.human.completion import (
    CompletionAck,
    CompletionAuditEntry,
    PseudonymizedNotes,
    pseudonymize_notes,
)
from maezo.gateway.human.completion_engine import (
    EngineRestTaskCompletion,
    InterimCompletionConfig,
    completion_audit_record,
)
from maezo.gateway.human.engine_reads import EngineHumanTaskTransport
from maezo.gateway.human.errors import GatewayRefusalError
from maezo.gateway.human.queue import ReadRefusalError

pytestmark = pytest.mark.asyncio

ORIGIN = "https://engine.internal:8443/engine-rest"
NOTES = pseudonymize_notes("colaborador resolveu na linha")


class Capability:
    def __init__(self, *, scope=SCOPE, fail=None):
        self.scope = scope
        self.calls: list[tuple] = []
        self.fail = fail

    async def complete(self, *, task_id, resultado, notes, expected_task_revision):
        self.calls.append((task_id, resultado, notes, expected_task_revision))
        if self.fail is not None:
            raise self.fail
        return CompletionAck(
            task_id=task_id,
            resultado=resultado,
            consumed_task_revision=expected_task_revision,
            completed_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
        )


def client(handler, *, origin=ORIGIN, timeout=5):
    return EngineRestTaskCompletion(
        scope=SCOPE, origin=origin, timeout_seconds=timeout, transport=httpx.MockTransport(handler)
    )


# ---------------------------------------------------------------------------------------
# The bundle guard: a completion rides a read this request already did.
# ---------------------------------------------------------------------------------------
async def test_without_a_capability_the_engine_transport_refuses():
    """The shipped composition. `complete_task` falls through to the refusing base port."""
    bundle, _ = await bundle_fixture()
    transport = EngineHumanTaskTransport(bundle)
    task = await transport.read_task("task-1")
    with pytest.raises(ReadRefusalError):
        await transport.complete_task(
            "task-1",
            resultado="resolvido_humano",
            notes=NOTES,
            expected_task_revision=task.snapshot.task_revision,
        )
    assert bundle._closed


async def test_completion_is_admitted_only_for_the_task_this_bundle_read():
    bundle, _ = await bundle_fixture()
    capability = Capability(scope=bundle.scope)
    transport = EngineHumanTaskTransport(bundle, capability)
    task = await transport.read_task("task-1")
    ack = await transport.complete_task(
        "task-1",
        resultado="resolvido_humano",
        notes=NOTES,
        expected_task_revision=task.snapshot.task_revision,
    )
    assert ack.task_id == "task-1"
    assert ack.consumed_task_revision == task.snapshot.task_revision
    assert capability.calls[0][:2] == ("task-1", "resolvido_humano")
    assert bundle._completed == {"task-1"}


async def test_a_task_the_bundle_never_read_is_refused_without_calling_the_engine():
    bundle, _ = await bundle_fixture()
    capability = Capability(scope=bundle.scope)
    transport = EngineHumanTaskTransport(bundle, capability)
    with pytest.raises(ReadRefusalError):
        await transport.complete_task(
            "task-1", resultado="resolvido_humano", notes=NOTES, expected_task_revision=1
        )
    assert capability.calls == [] and bundle._closed


async def test_a_revision_that_drifted_from_the_read_snapshot_is_refused():
    bundle, _ = await bundle_fixture()
    capability = Capability(scope=bundle.scope)
    transport = EngineHumanTaskTransport(bundle, capability)
    task = await transport.read_task("task-1")
    with pytest.raises(ReadRefusalError):
        await transport.complete_task(
            "task-1",
            resultado="resolvido_humano",
            notes=NOTES,
            expected_task_revision=task.snapshot.task_revision + 1,
        )
    assert capability.calls == [] and bundle._closed


async def test_a_second_completion_in_the_same_request_is_refused():
    bundle, _ = await bundle_fixture()
    capability = Capability(scope=bundle.scope)
    transport = EngineHumanTaskTransport(bundle, capability)
    task = await transport.read_task("task-1")
    await transport.complete_task(
        "task-1",
        resultado="resolvido_humano",
        notes=NOTES,
        expected_task_revision=task.snapshot.task_revision,
    )
    with pytest.raises(ReadRefusalError):
        await transport.complete_task(
            "task-1",
            resultado="devolvido_agente",
            notes=NOTES,
            expected_task_revision=task.snapshot.task_revision,
        )
    assert len(capability.calls) == 1


async def test_a_capability_from_another_tenant_is_refused():
    bundle, _ = await bundle_fixture()
    foreign = Capability(scope=bundle.scope.model_copy(update={"tenant": "other-tenant"}))
    transport = EngineHumanTaskTransport(bundle, foreign)
    task = await transport.read_task("task-1")
    with pytest.raises(ReadRefusalError):
        await transport.complete_task(
            "task-1",
            resultado="resolvido_humano",
            notes=NOTES,
            expected_task_revision=task.snapshot.task_revision,
        )
    assert foreign.calls == []


async def test_an_acknowledgement_that_does_not_match_the_request_is_refused():
    class Lying(Capability):
        async def complete(self, *, task_id, resultado, notes, expected_task_revision):
            return CompletionAck(
                task_id="another-task",
                resultado=resultado,
                consumed_task_revision=expected_task_revision,
                completed_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
            )

    bundle, _ = await bundle_fixture()
    transport = EngineHumanTaskTransport(bundle, Lying(scope=bundle.scope))
    task = await transport.read_task("task-1")
    with pytest.raises(ReadRefusalError):
        await transport.complete_task(
            "task-1",
            resultado="resolvido_humano",
            notes=NOTES,
            expected_task_revision=task.snapshot.task_revision,
        )


async def test_composition_refuses_a_half_configured_interim_pair():
    """A client with no audit sink, or a sink with no client, is not a valid interim plane."""
    from maezo.gateway.human.engine_reads import EngineReadComposition

    bundle, _ = await bundle_fixture()
    with pytest.raises(ReadRefusalError):
        EngineReadComposition(
            new_bundle=lambda: bundle,
            command_credentials=object(),  # type: ignore[arg-type]
            command_admission=object(),  # type: ignore[arg-type]
            transport_pool=httpx.MockTransport(lambda r: httpx.Response(204)),
            direct_completion=Capability(scope=bundle.scope),  # type: ignore[arg-type]
            completion_audit=None,
        )


# ---------------------------------------------------------------------------------------
# The wire: exactly the BPMN's two variables, on exactly one fixed route.
# ---------------------------------------------------------------------------------------
async def test_the_request_is_the_bpmn_contract_and_nothing_else():
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["method"] = request.method
        seen["content_type"] = request.headers.get("content-type")
        seen["body"] = json.loads(request.content)
        return httpx.Response(204)

    completion = client(handler)
    ack = await completion.complete(
        task_id="task-9", resultado="emergencia_acionada", notes=NOTES, expected_task_revision=4
    )
    await completion.close()

    assert seen["url"] == "https://engine.internal:8443/engine-rest/task/task-9/complete"
    assert seen["method"] == "POST"
    assert seen["content_type"] == "application/json"
    assert seen["body"] == {
        "variables": {
            "resultado": {"value": "emergencia_acionada", "type": "String"},
            "notas_resolucao": {"value": NOTES.text, "type": "String"},
        },
        "withVariablesInReturn": False,
    }
    assert ack.resultado == "emergencia_acionada" and ack.consumed_task_revision == 4


@pytest.mark.parametrize("status", [200, 204])
async def test_only_the_engines_success_statuses_are_a_completion(status):
    completion = client(lambda request: httpx.Response(status, json={}))
    ack = await completion.complete(
        task_id="task-1", resultado="resolvido_humano", notes=NOTES, expected_task_revision=2
    )
    await completion.close()
    assert ack.task_id == "task-1"


@pytest.mark.parametrize("status", [404, 409])
async def test_a_proven_absence_or_conflict_is_a_revision_conflict(status):
    completion = client(lambda request: httpx.Response(status, json={"message": "PRIVATE engine narrative"}))
    with pytest.raises(GatewayRefusalError) as refusal:
        await completion.complete(
            task_id="task-1", resultado="resolvido_humano", notes=NOTES, expected_task_revision=2
        )
    await completion.close()
    assert refusal.value.code == "revision_conflict"
    assert "PRIVATE engine narrative" not in str(refusal.value)


@pytest.mark.parametrize("status", [400, 401, 403, 500, 502, 503])
async def test_every_other_answer_is_uncertain_never_a_conflict(status):
    completion = client(lambda request: httpx.Response(status, json={"message": "PRIVATE engine narrative"}))
    with pytest.raises(GatewayRefusalError) as refusal:
        await completion.complete(
            task_id="task-1", resultado="resolvido_humano", notes=NOTES, expected_task_revision=2
        )
    await completion.close()
    assert refusal.value.code == "admission_unavailable"
    assert "PRIVATE engine narrative" not in str(refusal.value)


async def test_a_transport_failure_is_uncertain_and_carries_no_upstream_text():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("PRIVATE dns detail")

    completion = client(handler)
    with pytest.raises(GatewayRefusalError) as refusal:
        await completion.complete(
            task_id="task-1", resultado="resolvido_humano", notes=NOTES, expected_task_revision=2
        )
    await completion.close()
    assert refusal.value.code == "admission_unavailable"
    assert "PRIVATE dns detail" not in str(refusal.value)


async def test_an_unbounded_answer_is_refused_before_it_is_read_whole():
    completion = client(lambda request: httpx.Response(204, content=b"x" * 70000))
    with pytest.raises(GatewayRefusalError) as refusal:
        await completion.complete(
            task_id="task-1", resultado="resolvido_humano", notes=NOTES, expected_task_revision=2
        )
    await completion.close()
    assert refusal.value.code == "admission_unavailable"


async def test_a_closed_client_completes_nothing():
    completion = client(lambda request: httpx.Response(204))
    await completion.close()
    with pytest.raises(GatewayRefusalError):
        await completion.complete(
            task_id="task-1", resultado="resolvido_humano", notes=NOTES, expected_task_revision=2
        )


# ---------------------------------------------------------------------------------------
# Input the client refuses on its own, independently of the gateway above it.
# ---------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "origin",
    [
        "http://engine.example.com/engine-rest",  # plaintext to a PUBLIC name
        "https://engine.internal/",  # not the engine REST base
        "https://engine.internal/engine-rest/",  # trailing slash is not the canonical base
        "https://engine.internal/engine-rest?x=1",
        "https://engine.internal/engine-rest#f",
        "https://user:pw@engine.internal/engine-rest",
        "ftp://engine.internal/engine-rest",
        "engine.internal/engine-rest",
        "",
    ],
)
async def test_only_a_fixed_engine_rest_base_is_accepted(origin):
    with pytest.raises(GatewayRefusalError):
        client(lambda request: httpx.Response(204), origin=origin)


@pytest.mark.parametrize(
    "origin",
    [
        "http://localhost:8080/engine-rest",
        "http://127.0.0.1:8080/engine-rest",
        "http://cibseven:8080/engine-rest",
        "http://cibseven.maezo.internal:8080/engine-rest",
        "https://engine.example.com/engine-rest",
    ],
)
async def test_plaintext_is_accepted_only_towards_a_private_name(origin):
    completion = client(lambda request: httpx.Response(204), origin=origin)
    await completion.close()


@pytest.mark.parametrize("seconds", [0, -1])
async def test_a_nonpositive_timeout_is_refused(seconds):
    with pytest.raises(GatewayRefusalError):
        client(lambda request: httpx.Response(204), timeout=seconds)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"resultado": "aprovar"},
        {"resultado": ""},
        {"notes": "texto cru"},
        {"notes": PseudonymizedNotes("   ")},
        {"expected_task_revision": -1},
        {"expected_task_revision": "2"},
        {"task_id": ""},
        {"task_id": "../other/complete"},
        {"task_id": "a?b"},
        {"task_id": "a%2fb"},
    ],
)
async def test_the_client_revalidates_its_own_input_fail_closed(kwargs):
    """Belt under the gateway: raw text, an open outcome or a path-bending id never travel."""
    called = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        called["n"] += 1
        return httpx.Response(204)

    values: dict[str, object] = dict(
        task_id="task-1", resultado="resolvido_humano", notes=NOTES, expected_task_revision=2
    )
    values.update(kwargs)
    completion = client(handler)
    with pytest.raises(GatewayRefusalError):
        await completion.complete(**values)  # type: ignore[arg-type]
    await completion.close()
    assert called["n"] == 0


# ---------------------------------------------------------------------------------------
# The audit record: who, on what basis, at which revision — and no narrative.
# ---------------------------------------------------------------------------------------
def _entry(**changes):
    from tests.unit.gateway.human.test_task_completion_gateway import escalation_snapshot

    values: dict[str, object] = dict(
        scope=SCOPE,
        phase="intent",
        principal_ref="human-internal-1",
        session_ref="session-1",
        membership_revision=1,
        snapshot=escalation_snapshot(),
        authority_revision=7,
        resultado="resolvido_humano",
        notes_digest=NOTES.digest,
    )
    values.update(changes)
    return CompletionAuditEntry(**values)  # type: ignore[arg-type]


async def test_the_audit_record_is_the_adr0007_tuple_with_no_phi():
    record = completion_audit_record(_entry(), workload_ref="human-gateway")
    assert record.action == "portal_direct_completion.intent"
    assert record.decision == "REQUIRE_HUMAN"
    assert record.tenant_id == SCOPE.tenant
    assert record.agent_id == "human-gateway"
    details = record.details
    # WHO
    assert details["principal_ref"] == "human-internal-1"
    assert details["session_ref"] == "session-1"
    assert details["membership_revision"] == 1
    # ON WHAT BASIS
    assert details["resultado"] == "resolvido_humano"
    assert details["notas_resolucao_digest"] == NOTES.digest
    assert details["eligible_candidate_groups"] == ["plantao-clinico"]
    assert details["interim_decision_ref"] == "DL-0049"
    # AT WHICH REVISION
    assert (details["task_revision"], details["authority_revision"]) == (2, 7)
    assert details["evidence_revision"] == 3
    # The note itself is nowhere in the row.
    assert NOTES.text not in json.dumps(details)
    assert "notas_resolucao" not in [k for k in details if k != "notas_resolucao_digest"]


async def test_the_result_phase_adds_the_outcome_and_its_own_claim():
    intent = _entry()
    result = _entry(phase="result", outcome="completed")
    assert intent.dedup_key != result.dedup_key
    assert intent.dedup_key == "portal-direct-completion:intent:task-1:2"
    assert result.dedup_key == "portal-direct-completion:result:task-1:2"
    assert completion_audit_record(intent, workload_ref="w").details.get("outcome") is None
    assert completion_audit_record(result, workload_ref="w").details["outcome"] == "completed"


async def test_the_interim_config_carries_only_an_origin_and_a_timeout():
    config = InterimCompletionConfig(engine_origin=ORIGIN, timeout_seconds=7)
    assert (config.engine_origin, config.timeout_seconds) == (ORIGIN, 7)
    with pytest.raises(FrozenInstanceError):
        config.engine_origin = "https://other.internal/engine-rest"  # type: ignore[misc]
