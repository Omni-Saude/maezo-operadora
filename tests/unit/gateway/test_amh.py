"""Actual gateway/executor source with synthetic consent, durable-sink and HTTP ports.

No AMH API, database, credentials or policy approval is provisioned by these tests.
The tiny contract fixture tests mechanics; exact upstream-pin controls live in evidence.
"""

import asyncio
import json
import logging
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest

from maezo.gateway import amh, effect_classes, tool_registry
from maezo.gateway.audit_postgres import PostgresAuditSink
from maezo.gateway.credential_vault import CredentialVault
from maezo.gateway.effect_pep import AgentCapabilities
from maezo.gateway.seams._base import SeamContext
from maezo.ports.consent import ConsentDecision
from maezo.ports.errors import PortFailureReason as Reason
from maezo.ports.errors import PortResult
from tests.unit.adapters.amh.test_subject_context import consumer  # noqa: F401
from tests.unit.gateway import test_effect_pep as policy


class ConsentSource:
    def __init__(self, events):
        self.events = events
        self.result = ConsentDecision(
            consent_decision_ref="consent1",
            portable_subject_ref="subject1",
            purpose_of_use="purpose1",
            granted=True,
            consent_revision=1,
            decided_at=datetime.now(UTC),
        )

    def stream(self):
        raise NotImplementedError

    async def ack(self, *args, **kwargs):
        raise NotImplementedError

    async def nack(self, *args, **kwargs):
        raise NotImplementedError

    async def latest_decision(self, subject, *, purpose_of_use, timeout_seconds):
        self.events.append(("consent", subject, purpose_of_use))
        return PortResult.ok(self.result)


class DurableSinkDouble(PostgresAuditSink):
    """Offline subclass, explicitly not a durable commit claim."""

    def __init__(self, events):
        self._tenant_id = "amh"
        self.events = events
        self.error = None
        self.records = []
        self.delay = 0

    async def emit(self, record):
        self.events.append(("audit",))
        self.records.append(record)
        await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        return "a" * 64


class Body(httpx.AsyncByteStream):
    def __init__(self, harness):
        self.harness = harness

    async def __aiter__(self):
        await asyncio.sleep(self.harness.delay)
        yield self.harness.body

    async def aclose(self):
        self.harness.events.append(("response_closed",))


@pytest.fixture
def harness(consumer, tmp_path, monkeypatch):  # noqa: F811
    _, _, raw = consumer
    events = []
    h = SimpleNamespace(
        events=events,
        requests=[],
        options=[],
        body=b"{}",
        status=200,
        delay=0,
        headers={"content-type": "application/json"},
    )
    h.started = asyncio.Event()
    h.consent = ConsentSource(events)
    h.audit = DurableSinkDouble(events)
    h.vault = CredentialVault()
    h.runtime = amh.AmhRuntime(
        tenant="amh",
        legal_entity_ref="legal-entity1",
        principal="rafael",
        agent_version="1.0.0",
        origin="https://amh.example",
        purpose_of_use="purpose1",
        openapi_bytes=raw,
        credentials=h.vault,
        consent=h.consent,
        audit=h.audit,
    )
    h.vault.store_agent_credential("rafael", "runtime", h.runtime.credential_key, "synthetic-token")
    h.seam = SeamContext(
        tenant="amh",
        principal="rafael",
        phi_zone="phi",
        decision=policy._ctx(
            tmp_path,
            policy._manifest(status=policy.STATUS_RATIFIED, approved=frozenset({"leitura_phi_clinica"})),
            capabilities=AgentCapabilities.of(
                principal="rafael",
                tools=tuple("mcp-" + op for op in amh.OPERATIONS.values()),
                process_keys=(),
            ),
            autonomy=policy._StubPep(),
        ),
    )

    class Transport:
        def __init__(self, **options):
            h.options.append(options)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            events.append(("transport_closed",))

        async def handle_async_request(self, request):
            events.append(("http",))
            h.requests.append(request)
            h.started.set()
            return httpx.Response(h.status, headers=h.headers, stream=Body(h), request=request)

    monkeypatch.setattr(amh.httpx, "AsyncHTTPTransport", Transport)
    h.context = tool_registry.build_amh_context(runtime=h.runtime, seam=h.seam)
    h.executor = h.context.inner
    h.request = amh.GovernedSubjectContextRequest(
        operation="amh.get_subject_context",
        path="/interop/subject-context/v1/subjects/subject1/context",
        query=(("purpose_of_use", "purpose1"),),
        consent_decision_ref="consent1",
        timeout_seconds=1,
    )
    return h


@pytest.mark.parametrize("method", tuple(amh.READS))
async def test_four_real_canonical_operations_have_distinct_gates_and_fixed_get(harness, method):
    h = harness
    if method == "get_subject_context":
        h.body = json.dumps(
            dict(portable_subject_ref="subject1", purpose_of_use="purpose1", consent_decision_ref="consent1")
        ).encode()
    elif method == "get_subject_coverage":
        h.body = b'{"status":"active"}'
    else:
        h.body = b'{"items":[],"next_page_token":null}'
    result = await getattr(h.context, method)(
        "subject1", purpose_of_use="purpose1", consent_decision_ref="consent1"
    )
    assert result.succeeded
    assert [event[0] for event in h.events] == [
        "consent",
        "audit",
        "http",
        "response_closed",
        "transport_closed",
    ]
    request = h.requests[0]
    assert request.method == "GET" and request.url.host == "amh.example"
    assert request.headers["Authorization"] == "Bearer synthetic-token"
    assert "consent" not in str(request.url) and h.options == [{"retries": 0, "trust_env": False}]
    assert request.url.path.endswith("/context" + amh.READS[method][0])
    record = h.audit.records[0]
    assert record.action == "amh." + method
    assert all(
        secret not in json.dumps(record.details) for secret in ("subject1", "consent1", "synthetic-token")
    )
    assert record.details["consent_revision"] == 1
    assert effect_classes.OPERATIONS[record.action].tool_id == "mcp-amh." + method


@pytest.mark.parametrize("fault", ["undeclared", "autonomy", "unapproved", "general", "credential", "audit"])
async def test_missing_current_control_prevents_transport_even_under_shadow(harness, tmp_path, fault):
    h = harness
    seam = h.seam
    if fault == "undeclared":
        seam = replace(
            seam,
            decision=replace(
                seam.decision,
                capabilities=AgentCapabilities.of(
                    principal="rafael", tools=("mcp-fhir.read_patient",), process_keys=()
                ),
            ),
        )
    if fault == "autonomy":
        seam = replace(seam, decision=replace(seam.decision, autonomy=None))
    if fault == "unapproved":
        seam = replace(
            seam,
            decision=replace(
                seam.decision, approvals_path=policy._write(tmp_path, policy._manifest(), "unapproved.yaml")
            ),
        )
    if fault == "general":
        seam = replace(seam, phi_zone="general")
    runtime = replace(h.runtime, credentials=CredentialVault()) if fault == "credential" else h.runtime
    if fault == "audit":
        h.audit.error = RuntimeError("synthetic sensitive error")
    result = await tool_registry.build_amh_context(runtime=runtime, seam=seam).inner.execute(h.request)
    assert not result.succeeded and not h.requests
    assert "sensitive" not in repr(result)


@pytest.mark.parametrize("fault", ["granted", "subject", "purpose", "reference", "revision"])
async def test_each_call_rechecks_exact_consent_and_refuses_revocation(harness, fault):
    h = harness
    assert (await h.executor.execute(h.request)).succeeded
    changes = {
        "granted": {"granted": False},
        "subject": {"portable_subject_ref": "subject2"},
        "purpose": {"purpose_of_use": "other"},
        "reference": {"consent_decision_ref": "consent2"},
        "revision": {"consent_revision": -1},
    }
    h.consent.result = replace(h.consent.result, **changes[fault])
    result = await h.executor.execute(h.request)
    assert result.failure.reason == Reason.CONSENT_REQUIRED and len(h.requests) == 1
    assert len(h.audit.records) == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"operation": "fhir.read_patient"},
        {"path": "https://other.example/"},
        {"path": "/interop/subject-context/v1/subjects/subject1/context/coverage"},
        {"path": "/interop/subject-context/v1/subjects/subject1%2F..%2Fother/context"},
        {"query": (("purpose_of_use", "purpose1"), ("purpose_of_use", "purpose1"))},
        {"query": (("purpose_of_use", "purpose1"), ("consent_decision_ref", "consent1"))},
        {"query": (("purpose_of_use", "other"),)},
        {"timeout_seconds": float("nan")},
        {"timeout_seconds": 0},
        {"timeout_seconds": 31},
    ],
)
async def test_forged_descriptor_never_reaches_consent_audit_or_http(harness, changes):
    result = await harness.executor.execute(replace(harness.request, **changes))
    assert result.failure.reason == Reason.INVALID_REQUEST and harness.events == []


@pytest.mark.parametrize("fault", ["redirect", "compressed", "oversize", "content_type"])
async def test_response_limits_and_redirects_fail_closed_and_close_resources(harness, fault):
    h = harness
    if fault == "redirect":
        h.status = 302
        h.headers["Location"] = "https://other.example/patient"
    if fault == "compressed":
        h.headers["content-encoding"] = "gzip"
    if fault == "oversize":
        h.body = b"x" * (1_048_576 + 1)
    if fault == "content_type":
        h.headers["content-type"] = "text/html"
    result = await h.executor.execute(h.request)
    assert not result.succeeded and len(h.requests) == 1
    assert h.events[-2:] == [("response_closed",), ("transport_closed",)]


async def test_audit_timeout_never_dispatches_and_http_cancellation_closes(harness):
    h = harness
    h.audit.delay = 0.05
    result = await h.executor.execute(replace(h.request, timeout_seconds=0.01))
    assert result.failure.reason == Reason.TIMEOUT and not h.requests
    h.audit.delay = 0
    h.delay = 10
    task = asyncio.create_task(h.executor.execute(h.request))
    await h.started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert h.events[-2:] == [("response_closed",), ("transport_closed",)]


async def test_close_refuses_new_work_and_readiness_catches_raw_dependency(harness):
    h = harness
    assert tool_registry.effect_seams_gated({"clinical_context": h.context})[0]
    assert not tool_registry.effect_seams_gated({"clinical_context": object()})[0]
    assert not tool_registry.effect_seams_gated({"clinical_context": None})[0]
    await h.context.aclose()
    assert not (await h.executor.execute(h.request)).succeeded and not h.requests


@pytest.mark.parametrize(
    "change",
    [
        {"origin": "http://amh.example"},
        {"origin": "https://amh.example/"},
        {"origin": "https://user:secret@amh.example"},
        {"origin": "https://amh.example?other=1"},
        {"origin": "https://amh.example#other"},
        {"origin": "https://amh.example/path"},
        {"consent": None},
        {"audit": None},
    ],
)
def test_composition_requires_real_typed_producer_bindings(harness, change):
    with pytest.raises(amh.AmhCompositionError):
        replace(harness.runtime, **change)


def test_cross_tenant_or_principal_composition_is_rejected(harness):
    for seam in (replace(harness.seam, tenant="other"), replace(harness.seam, principal="helena")):
        with pytest.raises(amh.AmhCompositionError):
            tool_registry.build_amh_context(runtime=harness.runtime, seam=seam)


async def test_closing_runtime_cancels_inflight_transport(harness):
    h = harness
    h.delay = 10
    task = asyncio.create_task(h.executor.execute(h.request))
    await h.started.wait()
    await h.context.aclose()
    assert task.cancelled() and h.events[-2:] == [("response_closed",), ("transport_closed",)]
    assert not h.executor._active


async def test_debug_header_logging_refuses_without_global_logging_changes(harness):
    logger = logging.getLogger("httpcore.http11")
    old = logger.level
    try:
        logger.setLevel(logging.DEBUG)
        result = await harness.executor.execute(harness.request)
        assert result.failure.reason == Reason.UPSTREAM_UNAVAILABLE and not harness.requests
        assert logger.level == logging.DEBUG
    finally:
        logger.setLevel(old)


@pytest.mark.parametrize(
    "status,reason",
    [(401, Reason.NOT_AUTHENTICATED), (429, Reason.RATE_LIMITED), (503, Reason.UPSTREAM_UNAVAILABLE)],
)
async def test_machine_auth_and_transport_failures_do_not_claim_consent_denial(harness, status, reason):
    harness.status = status
    harness.headers = {"content-type": "text/html"}
    result = await harness.executor.execute(harness.request)
    assert result.failure.reason == reason
    assert harness.events[-2:] == [("response_closed",), ("transport_closed",)]


def test_registry_composes_optional_context_without_replacing_fhir_or_granting_tools(harness, monkeypatch):
    h = harness
    monkeypatch.setattr(tool_registry, "build_agent_seam_context", lambda **kwargs: h.seam)
    settings = SimpleNamespace(
        tenant_id="amh",
        cibseven_base_url="http://engine.invalid",
        fhir_base_url="http://fhir.invalid",
        agent_version="1.0.0",
    )
    absent = tool_registry.build_agent_seams(settings=settings, agent_id="rafael")
    assert "clinical_context" not in absent and "fhir" in absent
    composed = tool_registry.build_agent_seams(settings=settings, agent_id="rafael", amh_runtime=h.runtime)
    assert isinstance(composed["clinical_context"], amh.GatedAmhContext)
    assert type(composed["fhir"]) is type(absent["fhir"])
    assert tool_registry.effect_seams_gated(composed)[0]
    assert not h.requests


def test_invalid_runtime_purpose_and_other_tenant_audit_refuse(harness):
    with pytest.raises(amh.AmhCompositionError):
        tool_registry.build_amh_context(
            runtime=replace(harness.runtime, purpose_of_use="not-published"), seam=harness.seam
        )
    harness.audit._tenant_id = "other"
    with pytest.raises(amh.AmhCompositionError):
        replace(harness.runtime)
    assert not any(isinstance(value, CredentialVault) for value in vars(harness.executor).values())
