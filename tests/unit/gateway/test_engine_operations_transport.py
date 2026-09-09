"""Offline HTTP wire tests only: no simulated engine or service-acceptance claim.

Locally generated ephemeral certificates test TLS construction. httpx.MockTransport
records requests and supplies adversarial bytes; it grants no engine authority.
"""

from __future__ import annotations

import hashlib
import json
import ssl
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from maezo.gateway.engine_contracts import (
    EngineCapabilityError,
    EngineCapabilityProfile,
    EngineIdentity,
    EngineOperation,
    EngineRefusalCode,
    EngineRequest,
    EngineTarget,
    canonical_json,
)
from maezo.gateway.engine_schemas import CONSENT_REVOKED, CONTAS_PAGTO_START, HELENA_START, start_read_schema
from maezo.gateway.engine_transport import (
    EngineOperationsClient,
    EngineTLSConfig,
    EngineTransportUnavailableError,
)
from maezo.gateway.seams import SeamContext
from maezo.gateway.tool_registry import build_cibseven_seam
from maezo.tools.mcp_cibseven.transport import (
    AgentDecisionProvenance,
    CibSevenStartAuthorizationError,
    HistoryQueryingTransport,
    StartAuthorizingTransport,
    StartOutcome,
    start_process_idempotent,
)
from tests.support.audit_fakes import FakeStartAuditSink
from tests.unit.gateway.test_engine_capability_contracts import variables


@pytest.fixture
def config(tmp_path):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "SyntheticD7TestCA")])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(hours=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.SubjectAlternativeName([x509.UniformResourceIdentifier("spiffe://synthetic/helena")]),
            critical=False,
        )
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH]), critical=False)
        .sign(key, hashes.SHA256())
    )
    ca = tmp_path / "ca.pem"
    ca.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    private = tmp_path / "key.pem"
    private.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        )
    )
    private.chmod(0o600)
    identity = EngineIdentity(
        "synthetic",
        "isolated-test",
        "helena",
        "synthetic-v1",
        name.rfc4514_string(),
        "spiffe://synthetic/helena",
    )
    start = EngineCapabilityProfile(
        identity, EngineTarget(HELENA_START.process_key, 7, "definition-7"), HELENA_START
    )
    profiles = (start,) + tuple(
        replace(start, schema=start_read_schema(HELENA_START, op))
        for op in (EngineOperation.READ_ACTIVE, EngineOperation.READ_HISTORY)
    )
    return EngineTLSConfig(
        "https://engine.synthetic:8443/engine-rest",
        identity,
        profiles,
        "a" * 64,
        ca,
        hashlib.sha256(ca.read_bytes()).hexdigest(),
        ca,
        cert.fingerprint(hashes.SHA256()).hex(),
        private,
    )


def wire(monkeypatch, config, replies=None):
    calls = []
    queue = list(replies or [])
    real = httpx.AsyncClient

    def handler(request):
        calls.append(request)
        assert str(request.url).startswith(config.endpoint + "/maezo/v1/")
        assert not request.url.query
        assert "authorization" not in request.headers
        if queue:
            response = queue.pop(0)
            if isinstance(response, Exception):
                raise response
            return response
        if request.method == "GET":
            return httpx.Response(
                200,
                json=dict(
                    protocol="maezo.engine-readiness.v1",
                    ready=True,
                    policy_digest=config.policy_digest,
                    capabilities=[p.digest for p in config.profiles],
                ),
            )
        data = json.loads(request.content)
        result = (
            dict(id="started-1", definition_id="definition-7", tenant="synthetic")
            if data["operation"] == "start"
            else []
        )
        return httpx.Response(
            200,
            json=dict(
                protocol="maezo.engine-result.v1", capability_digest=data["capability_digest"], result=result
            ),
        )

    def client(**kwargs):
        assert kwargs["trust_env"] is False
        assert kwargs["follow_redirects"] is False
        context = kwargs["verify"]
        assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED
        assert context.minimum_version >= ssl.TLSVersion.TLSv1_2
        return real(**kwargs, transport=httpx.MockTransport(handler))

    monkeypatch.setattr("maezo.gateway.engine_transport.httpx.AsyncClient", client)
    return calls


def factory(config, **kwargs):
    return build_cibseven_seam(
        seam=SeamContext("synthetic", config.identity.workload),
        base_url=config.endpoint,
        engine_tls=config,
        engine_process_key=config.profiles[0].target.process_key,
        **kwargs,
    )


def result(config, operation, value):
    profile = next(p for p in config.profiles if p.schema.operation is operation)
    return httpx.Response(
        200, json=dict(protocol="maezo.engine-result.v1", capability_digest=profile.digest, result=value)
    )


@pytest.mark.asyncio
async def test_actual_factory_audited_start_and_wire_projection(config, monkeypatch):
    calls = wire(monkeypatch, config)
    transport = factory(config)
    assert isinstance(transport, StartAuthorizingTransport)
    assert isinstance(transport, HistoryQueryingTransport)
    sink = FakeStartAuditSink()
    started = await start_process_idempotent(
        transport,
        process_key=HELENA_START.process_key,
        business_key="ESC-synthetic-conversation",
        variables=variables(),
        audit_sink=sink,
        provenance=AgentDecisionProvenance("helena", "synthetic-v1", "synthetic", {}),
    )
    assert started.start_outcome is StartOutcome.STARTED
    assert len(sink.records) == 1
    assert [r.method for r in calls] == ["GET", "POST", "POST"]
    body = json.loads(calls[-1].content)
    assert body["variables"]["tenant_id"] == "synthetic"
    assert body["variables"]["source_agent_version"] == "synthetic-v1"
    assert body["source_ref"] == ""
    assert body["capability_digest"] == config.profiles[0].digest
    assert set(body) == {
        "protocol",
        "capability_digest",
        "operation",
        "process_key",
        "resource_ref",
        "variables",
        "correlation",
        "all_matching",
        "error_code",
        "topic",
        "message",
        "worker_id",
        "parameters",
        "source_ref",
    }
    await transport.close()
    with pytest.raises(CibSevenStartAuthorizationError):
        await transport.find_active_instance("ESC-synthetic-conversation")


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["tenant", "actor", "version", "field"])
async def test_preclaim_refusal_has_no_http_audit_or_effect(config, monkeypatch, bad):
    calls = wire(monkeypatch, config)
    data = variables()
    provenance = AgentDecisionProvenance("helena", "synthetic-v1", "synthetic", {})
    if bad == "field":
        data["decisao_cobertura"] = "DENY"
    else:
        provenance = replace(
            provenance, **{dict(tenant="tenant_id", actor="agent_id", version="agent_version")[bad]: "wrong"}
        )
    sink = FakeStartAuditSink()
    with pytest.raises(CibSevenStartAuthorizationError):
        await start_process_idempotent(
            factory(config),
            process_key=HELENA_START.process_key,
            business_key="ESC-synthetic-conversation",
            variables=data,
            audit_sink=sink,
            provenance=provenance,
        )
    assert calls == [] and sink.records == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(503, json={"error": "engine_profile_unavailable"}),
        httpx.Response(403, json={"error": "engine_operation_denied"}),
        httpx.Response(401, text="secret provider echo"),
        httpx.Response(302, headers={"location": "https://attacker.invalid"}),
        httpx.Response(
            200, content=b'{"result":[],"result":[]}', headers={"content-type": "application/json"}
        ),
        httpx.Response(200, json={"protocol": "native-rest", "result": []}),
        httpx.Response(503, json={"error": []}),
        httpx.Response(409, json={"error": "engine_revision_conflict"}),
        httpx.Response(200, content=b"x" * 1_048_577, headers={"content-type": "application/json"}),
    ],
)
async def test_refusal_malformed_redirect_not_absence_or_retry(config, monkeypatch, response):
    calls = wire(monkeypatch, config, [response])
    with pytest.raises(CibSevenStartAuthorizationError) as exc:
        await factory(config).find_active_instance("synthetic-business-key")
    assert len(calls) == 1
    assert "secret" not in str(exc.value) and "business-key" not in str(exc.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "rows",
    [
        None,
        {},
        [dict(id="i", definition_id="wrong", state="ACTIVE")],
        [dict(id="i", definition_id="definition-7", state=[])],
        [dict(id="i", definition_id="definition-7", state="UNKNOWN")],
        [dict(id="i", definition_id="definition-7", state="COMPLETED")],
        [dict(id="i", definition_id="definition-7", state="ACTIVE", secret="x")],
        [dict(id="i", definition_id="definition-7", state="ACTIVE")] * 2,
    ],
)
async def test_read_shape_never_becomes_absence(config, monkeypatch, rows):
    wire(monkeypatch, config, [result(config, EngineOperation.READ_ACTIVE, rows)])
    with pytest.raises(CibSevenStartAuthorizationError):
        await factory(config).find_active_instance("synthetic")


@pytest.mark.asyncio
async def test_history_live_generation_wins_and_empty_is_authoritative(config, monkeypatch):
    rows = [
        dict(id="old", definition_id="definition-7", state="COMPLETED"),
        dict(id="live", definition_id="definition-7", state="SUSPENDED"),
    ]
    wire(
        monkeypatch,
        config,
        [result(config, EngineOperation.READ_HISTORY, rows), result(config, EngineOperation.READ_ACTIVE, [])],
    )
    transport = factory(config)
    found = await transport.find_any_instance("synthetic")
    assert found is not None and found.instance_id == "live"
    assert await transport.find_active_instance("synthetic") is None
    with pytest.raises(CibSevenStartAuthorizationError):
        await transport.get_process_status("synthetic")


@pytest.mark.parametrize(
    "changes",
    [
        dict(endpoint="http://engine:8443/engine-rest"),
        dict(endpoint="https://engine/engine-rest"),
        dict(endpoint="https://user:secret@engine:8443/engine-rest"),
        dict(endpoint="https://engine:8443/engine-rest?x=1"),
        dict(ca_sha256="b" * 64),
        dict(certificate_sha256="b" * 64),
        dict(policy_digest="default"),
        dict(timeout=float("nan")),
        dict(profiles=()),
        dict(private_key_file=Path("relative")),
    ],
)
def test_invalid_configuration_fails_before_transport(config, changes):
    with pytest.raises(EngineCapabilityError):
        EngineOperationsClient(replace(config, **changes))


def test_identity_and_ca_rotation_refuse(config):
    with pytest.raises(EngineCapabilityError):
        EngineOperationsClient(replace(config, identity=replace(config.identity, subject="spiffe://wrong")))
    client = EngineOperationsClient(config)
    config.ca_file.write_bytes(b"invalid rotated certificate")
    with pytest.raises(EngineTransportUnavailableError):
        client.config.context()


@pytest.mark.parametrize(
    "kwargs", [dict(auth_token="synthetic-not-a-secret"), dict(fresh_client=True), dict(timeout=1)]
)
def test_secure_factory_rejects_legacy_credentials_and_options(config, kwargs):
    with pytest.raises(EngineCapabilityError):
        factory(config, **kwargs)


@pytest.mark.asyncio
async def test_readiness_wrong_policy_stops_before_audit(config, monkeypatch):
    calls = wire(
        monkeypatch,
        config,
        [
            httpx.Response(
                200,
                json=dict(
                    protocol="maezo.engine-readiness.v1",
                    ready=True,
                    policy_digest="b" * 64,
                    capabilities=[p.digest for p in config.profiles],
                ),
            )
        ],
    )
    sink = FakeStartAuditSink()
    with pytest.raises(CibSevenStartAuthorizationError):
        await start_process_idempotent(
            factory(config),
            process_key=HELENA_START.process_key,
            business_key="synthetic",
            variables=variables(),
            audit_sink=sink,
            provenance=AgentDecisionProvenance("helena", "synthetic-v1", "synthetic", {}),
        )
    assert len(calls) == 1 and sink.records == []


def source_config(config, schema):
    # Projection-only controls: use schema-workload identity consistently; the certificate's
    # signed subject remains exact. Server policy provisioning remains D's responsibility.
    identity = replace(config.identity, workload=schema.workload)
    profile = EngineCapabilityProfile(
        identity,
        EngineTarget(schema.process_key, 7, "definition-7", schema.topic, schema.message),
        schema,
        source_target=EngineTarget(schema.source_process_key, 3, "source-definition", schema.source_topic),
    )
    return replace(config, identity=identity, profiles=(profile,))


@pytest.mark.asyncio
async def test_actual_human_source_and_correlation_propagate(config, monkeypatch):
    config = source_config(config, CONSENT_REVOKED)
    calls = wire(monkeypatch, config, [result(config, EngineOperation.CORRELATE, {"correlated": 2})])
    transport = factory(config, engine_source_ref="actual-completed-human-task")
    await transport.correlate_message(
        CONSENT_REVOKED.message,
        "",
        {"consent_event_ref": "actual-receipt"},
        correlation_keys={"tenant_id": "synthetic", "beneficiario_pseudo_id": "pseudo"},
        all_matching=True,
    )
    body = json.loads(calls[0].content)
    assert body["source_ref"] == "actual-completed-human-task"
    assert body["variables"] == {"consent_event_ref": "actual-receipt"}
    assert body["correlation"] == {"tenant_id": "synthetic", "beneficiario_pseudo_id": "pseudo"}
    assert body["all_matching"] is True


def test_required_external_source_is_never_invented(config):
    config = source_config(config, CONTAS_PAGTO_START)
    client = EngineOperationsClient(config)
    request = EngineRequest(
        EngineOperation.START,
        CONTAS_PAGTO_START.process_key,
        "synthetic-payment",
        canonical_json(variables(CONTAS_PAGTO_START)),
    )
    with pytest.raises(EngineCapabilityError) as exc:
        client.project(request)
    assert exc.value.code is EngineRefusalCode.EVIDENCE_UNAVAILABLE
    _, encoded = client.project(request, source_ref="actual-locked-external-task")
    body = json.loads(encoded)
    assert body["source_ref"] == "actual-locked-external-task"
    assert body["variables"]["valor_pagamento_cents"] == 37
    assert type(body["variables"]["valor_pagamento_cents"]) is int


@pytest.mark.asyncio
async def test_lost_start_response_preserves_audit_and_does_not_retry(config, monkeypatch):
    # Readiness and dedup query are authenticated wire responses, then POST is inconclusive.
    ready = httpx.Response(
        200,
        json=dict(
            protocol="maezo.engine-readiness.v1",
            ready=True,
            policy_digest=config.policy_digest,
            capabilities=[p.digest for p in config.profiles],
        ),
    )
    calls = wire(
        monkeypatch,
        config,
        [
            ready,
            result(config, EngineOperation.READ_ACTIVE, []),
            httpx.ReadTimeout("synthetic provider content must not escape"),
        ],
    )
    sink = FakeStartAuditSink()
    with pytest.raises(CibSevenStartAuthorizationError) as exc:
        await start_process_idempotent(
            factory(config),
            process_key=HELENA_START.process_key,
            business_key="synthetic",
            variables=variables(),
            audit_sink=sink,
            provenance=AgentDecisionProvenance("helena", "synthetic-v1", "synthetic", {}),
        )
    assert exc.value.code is EngineRefusalCode.IDENTITY_UNAVAILABLE
    assert len(sink.records) == 1
    assert len(calls) == 3 and json.loads(calls[-1].content)["operation"] == "start"
    assert "provider" not in str(exc.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "row",
    [
        dict(id="", definition_id="definition-7", tenant="synthetic"),
        dict(id="i", definition_id="definition-7", tenant="wrong"),
        dict(id="i", definition_id="wrong", tenant="synthetic"),
        dict(id="i", definition_id="definition-7", tenant="synthetic", variables={}),
        None,
    ],
)
async def test_malformed_start_response_never_reports_success(config, monkeypatch, row):
    calls = wire(monkeypatch, config, [result(config, EngineOperation.START, row)])
    client = EngineOperationsClient(config)
    with pytest.raises(EngineCapabilityError):
        await client.execute(
            EngineRequest(
                EngineOperation.START, HELENA_START.process_key, "synthetic", canonical_json(variables())
            )
        )
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_missing_source_ref_refuses_correlation_without_http(config, monkeypatch):
    config = source_config(config, CONSENT_REVOKED)
    calls = wire(monkeypatch, config)
    with pytest.raises(CibSevenStartAuthorizationError) as exc:
        await factory(config).correlate_message(
            CONSENT_REVOKED.message,
            "",
            {"consent_event_ref": "actual-receipt"},
            correlation_keys={"tenant_id": "synthetic", "beneficiario_pseudo_id": "pseudo"},
            all_matching=True,
        )
    assert exc.value.code is EngineRefusalCode.EVIDENCE_UNAVAILABLE
    assert calls == []


def test_factory_cannot_cross_seam_tenant(config):
    with pytest.raises(EngineCapabilityError):
        build_cibseven_seam(
            seam=SeamContext("wrong", "helena"),
            base_url=config.endpoint,
            engine_tls=config,
            engine_process_key=HELENA_START.process_key,
        )


@pytest.mark.asyncio
async def test_payment_completed_dedup_never_reissues_start(config, monkeypatch):
    config = source_config(config, CONTAS_PAGTO_START)
    start = config.profiles[0]
    config = replace(
        config,
        profiles=(start,)
        + tuple(
            replace(start, schema=start_read_schema(start.schema, op))
            for op in (EngineOperation.READ_ACTIVE, EngineOperation.READ_HISTORY)
        ),
    )
    ready = httpx.Response(
        200,
        json=dict(
            protocol="maezo.engine-readiness.v1",
            ready=True,
            policy_digest=config.policy_digest,
            capabilities=[p.digest for p in config.profiles],
        ),
    )
    history = result(
        config,
        EngineOperation.READ_HISTORY,
        [dict(id="paid-instance", definition_id="definition-7", state="COMPLETED")],
    )
    calls = wire(monkeypatch, config, [ready, result(config, EngineOperation.READ_ACTIVE, []), history])
    sink = FakeStartAuditSink(already_audited=True)
    outcome = await start_process_idempotent(
        factory(config, engine_source_ref="actual-source-task"),
        process_key=CONTAS_PAGTO_START.process_key,
        business_key="synthetic-payment",
        variables=variables(CONTAS_PAGTO_START),
        audit_sink=sink,
        provenance=AgentDecisionProvenance("operadora-worker", "synthetic-v1", "synthetic", {}),
    )
    assert outcome.start_outcome is StartOutcome.ALREADY_COMPLETED
    assert outcome.instance_id == "paid-instance"
    assert len(calls) == 3
    assert all(json.loads(r.content)["operation"] != "start" for r in calls if r.method == "POST")


@pytest.mark.parametrize("variant", ["expired", "server_only", "wrong_san", "mismatched_key"])
def test_actual_certificate_identity_controls(config, variant):
    key = serialization.load_pem_private_key(config.private_key_file.read_bytes(), password=None)
    now = datetime.now(UTC)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "SyntheticD7TestCA")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(hours=2))
        .not_valid_after(now - timedelta(hours=1) if variant == "expired" else now + timedelta(hours=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.SubjectAlternativeName(
                [
                    x509.UniformResourceIdentifier(
                        "spiffe://wrong" if variant == "wrong_san" else config.identity.subject
                    )
                ]
            ),
            critical=False,
        )
        .add_extension(
            x509.ExtendedKeyUsage(
                [
                    ExtendedKeyUsageOID.SERVER_AUTH
                    if variant == "server_only"
                    else ExtendedKeyUsageOID.CLIENT_AUTH
                ]
            ),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    config.certificate_file.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    config = replace(
        config,
        ca_sha256=hashlib.sha256(config.ca_file.read_bytes()).hexdigest(),
        certificate_sha256=cert.fingerprint(hashes.SHA256()).hex(),
    )
    if variant == "mismatched_key":
        other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        config.private_key_file.write_bytes(
            other.private_bytes(
                serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
            )
        )
    with pytest.raises(EngineTransportUnavailableError):
        EngineOperationsClient(config)
