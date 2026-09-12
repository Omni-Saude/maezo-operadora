"""Unit wire/provider fixtures are synthetic and do not qualify native runtime."""

import asyncio
import base64
import copy
import dataclasses
import ssl
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.x509.oid import NameOID

from maezo.gateway.native_fetch.adapter import MetadataLease, NativeFetchAdapter, WorkerMetadataProvider
from maezo.gateway.native_fetch.models import (
    FetchProfile,
    FetchUnavailableError,
    PreparedFetch,
    decode,
    encode,
    outcome,
    outcome_query,
    prepare,
    result,
    sha,
)
from maezo.gateway.native_fetch.transport import (
    AdmissionLease,
    NativeAdmissionProvider,
    NativeFetchClient,
    NativeTLS,
)
from maezo.tools.workers.auth import _ceiling_valor_cents
from maezo.tools.workers.auth_exact_amount import TYPE, AuthExactAmount

NOW = datetime(2026, 9, 10, tzinfo=UTC)
REF = base64.urlsafe_b64encode(bytes(32)).rstrip(b"=").decode()
ACQ = base64.urlsafe_b64encode(bytes([1]) * 32).rstrip(b"=").decode()
RECEIPT = base64.urlsafe_b64encode(bytes([2]) * 32).rstrip(b"=").decode()
HASH = "a" * 64


@pytest.fixture
def prepared():
    ident = dict(
        tenant="tenant",
        environment="test",
        workload="auth-worker",
        workload_version="1",
        issuer="CN=unit",
        subject="spiffe://test/worker",
        origin="verified_mtls",
    )
    schema = dict(
        schema_id="test-only-fetch",
        operation="fetch_lock",
        process_key="AUTH",
        workload="auth-worker",
        fields=[],
        sources=["test-only"],
        topic="auth-topic",
        message="",
        correlation_fields=[],
        all_matching=False,
        error_codes=[],
        source_process_key="",
        source_topic="",
        audit_actor="",
        read_projection=["valor_estimado", "tenant_id"],
    )
    doc = dict(
        protocol="maezo.engine-capability.v2",
        identity=ident,
        target=dict(
            process_key="AUTH", process_version=1, definition_id="AUTH:1:def", topic="auth-topic", message=""
        ),
        schema=schema,
        worker_id="worker",
        source_target=None,
        acquisition_policy=dict(
            resource_requirement="none", source_requirement="none", source_owner_binding_digest=None
        ),
    )
    profile = FetchProfile(
        encode(doc), (("valor_estimado", (TYPE,)), ("tenant_id", ("string",))), "portal-auth-intake.v1"
    )
    return prepare(
        profile,
        engine="engine",
        database_incarnation="db",
        native_user="native",
        activation_ref="activation",
        command_id=REF,
        resource_ref="fetch-selector",
        max_tasks=2,
        lock_millis=10000,
        poll_millis=100,
        projection=("valor_estimado", "tenant_id"),
    )


def response(prepared, state="executed"):
    acquired = dict(
        role="resource",
        task_ref="task",
        acquisition_ref=ACQ,
        lease_revision=1,
        lock_expires_at=int((NOW + timedelta(seconds=10)).timestamp()) * 1000,
        state="live",
    )
    receipt = dict(
        receipt_ref=RECEIPT,
        state="committed",
        command=decode(prepared.binding),
        resource_ref_digest=sha(b"fetch-selector"),
        source_ref_digest=sha(b""),
        resource_acquisition=None,
        source_acquisition=None,
        acquisitions=[acquired],
    )
    row = dict(
        id="task",
        definition_id="AUTH:1:def",
        process_instance_id="process",
        execution_id="execution",
        topic="auth-topic",
        worker_id="worker",
        lock_expires_at=acquired["lock_expires_at"],
        variables=dict(
            valor_estimado=dict(type=TYPE, value="90071992547409.93"),
            tenant_id=dict(type="string", value="tenant"),
        ),
        retries=None,
    )
    return dict(
        protocol="maezo.engine-result.v2",
        command=decode(prepared.binding),
        status=state,
        receipt=receipt if state in {"executed", "duplicate"} else None,
        result=dict(value=[row], acquisitions=[acquired]) if state == "executed" else None,
    )


def test_exact_native_integer_and_amount_round_trip(prepared):
    parsed = result(encode(response(prepared)), 200, prepared)
    assert parsed.status == "executed" and len(parsed.rows) == 1
    assert decode(prepared.body)["parameters"]["maxTasks"] == 2
    assert decode(prepared.binding)["request_digest"] == sha(prepared.body)
    assert "valor_estimado" not in decode(parsed.receipt)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda v: v.update(unexpected=True),
        lambda v: v["command"].update(native_user="other"),
        lambda v: v["receipt"].update(resource_ref_digest=sha(encode("fetch-selector"))),
        lambda v: v["receipt"].update(receipt_ref=REF + "="),
        lambda v: v["receipt"]["acquisitions"][0].update(lease_revision=True),
        lambda v: v["receipt"]["acquisitions"][0].update(lease_revision="1"),
        lambda v: v["receipt"]["acquisitions"][0].update(role="source"),
        lambda v: v["result"].update(value=[]),
        lambda v: v["result"]["value"][0].update(business_key="invented"),
        lambda v: v["result"]["value"][0].update(worker_id="other"),
        lambda v: v["result"]["value"][0].update(id="other"),
        lambda v: v["result"]["value"][0].update(lock_expires_at=2),
        lambda v: v["result"]["value"][0].update(retries=False),
        lambda v: v["result"]["value"][0]["variables"].update(secret=dict(type="string", value="hidden")),
        lambda v: v["result"]["value"][0]["variables"]["valor_estimado"].update(value="1.0"),
        lambda v: v["result"]["value"][0]["variables"]["valor_estimado"].update(type="double", value=1.0),
        lambda v: v["result"]["value"][0]["variables"]["tenant_id"].update(valueInfo={}),
    ],
)
def test_closed_projection_and_receipt_refuse_mismatch(prepared, mutate):
    value = response(prepared)
    mutate(value)
    with pytest.raises(FetchUnavailableError):
        result(encode(value), 200, prepared)


@pytest.mark.parametrize(
    "raw", [b'{"a":1,"a":2}', b'{"a":NaN}', b'{"a":"\\ud800"}', b"{}{}", b"[]", b'{"x":9223372036854775808}']
)
def test_global_strict_codec(raw):
    with pytest.raises(FetchUnavailableError):
        decode(raw)


def test_duplicate_outcome_never_reconstruct_inputs(prepared):
    value = response(prepared, "duplicate")
    parsed = result(encode(value), 200, prepared)
    assert parsed.rows == () and parsed.receipt is not None
    value["result"] = dict(value=[], acquisitions=[])
    with pytest.raises(FetchUnavailableError):
        result(encode(value), 200, prepared)
    query = outcome_query(prepared, HASH, "new-activation")
    value = dict(
        protocol="maezo.engine-outcome.v2",
        recovery_capability_digest=HASH,
        reader_activation_ref="new-activation",
        query_digest=sha(query),
        command=decode(prepared.binding),
        status="committed",
        receipt=response(prepared)["receipt"],
    )
    parsed = outcome(encode(value), 200, prepared, query)
    assert parsed.status == "committed" and parsed.rows == ()
    value["query_digest"] = "b" * 64
    with pytest.raises(FetchUnavailableError):
        outcome(encode(value), 200, prepared, query)


def test_profile_selector_does_not_admit_generic_custom_money(prepared):
    with pytest.raises(FetchUnavailableError):
        dataclasses.replace(prepared.profile, input_profile="native-classified.v2")
    with pytest.raises(FetchUnavailableError):
        dataclasses.replace(prepared.profile, classifications=(("other", (TYPE,)),))


def test_prepared_exact_body_and_identity_binding(prepared):
    value = decode(prepared.body)
    value["parameters"]["maxTasks"] = True
    with pytest.raises(FetchUnavailableError):
        PreparedFetch(encode(value), prepared.binding, prepared.profile)
    changed = decode(prepared.binding)
    changed["identity"]["workload"] = "other"
    with pytest.raises(FetchUnavailableError):
        PreparedFetch(prepared.body, encode(changed), prepared.profile)


class TestAuthority(NativeAdmissionProvider):
    __test__ = False

    def __init__(self, prepared, tls, time):
        self.prepared, self.tls, self.time = prepared, tls, time
        self.until = NOW + timedelta(seconds=5)
        self.revoked = False

    async def acquire(self, selection_digest, purpose, command_binding):
        def live():
            if self.revoked:
                raise ValueError("provider private error")

        return AdmissionLease(
            purpose,
            selection_digest,
            command_binding,
            self.prepared.profile.digest if purpose == "fetch" else HASH,
            "activation",
            "db",
            HASH,
            HASH,
            (self.prepared.profile.digest,),
            NOW - timedelta(seconds=1),
            self.until,
            live,
        )


@pytest.fixture
def client(prepared, monkeypatch):
    time = [NOW]
    tls = NativeTLS(
        "https://native.test:8443",
        encode(prepared.profile.value()["identity"]),
        Path("/ca"),
        HASH,
        Path("/cert"),
        HASH,
        Path("/key"),
        HASH,
    )
    authority = TestAuthority(prepared, tls, time)
    client = NativeFetchClient(tls, (prepared.profile,), authority, clock=lambda: time[0])
    calls = []

    async def exchange(method, route, body, guard):
        guard()
        calls.append((method, route, body))
        if route.endswith("readiness"):
            return 200, encode(
                dict(
                    protocol="maezo.engine-readiness.v2",
                    ready=True,
                    policy_digest=HASH,
                    config_digest=HASH,
                    schema_digest=HASH,
                    capabilities=[prepared.profile.digest],
                    activation_ref="activation",
                    database_incarnation="db",
                    purpose="runtime",
                )
            )
        if route.endswith("outcomes"):
            query = decode(body)
            return 200, encode(
                dict(
                    protocol="maezo.engine-outcome.v2",
                    recovery_capability_digest=HASH,
                    reader_activation_ref="activation",
                    query_digest=sha(body),
                    command=query["command"],
                    status="not_observed",
                    receipt=None,
                )
            )
        return 200, encode(response(prepared))

    monkeypatch.setattr(client, "_exchange", exchange)
    return client, authority, time, calls


class TestMetadata(WorkerMetadataProvider):
    __test__ = False

    async def acquire(self, command_binding, receipt, task_ref):
        return MetadataLease(
            command_binding,
            RECEIPT,
            task_ref,
            ACQ,
            1,
            "AUTH:1:def",
            "process",
            "execution",
            "AUTH",
            "protected-engine-guide",
            "actual-activity",
            "portal-auth-intake.v1",
            NOW,
            NOW + timedelta(seconds=5),
            lambda: None,
        )


@pytest.mark.asyncio
async def test_initial_acquisition_reaches_exact_worker_branch(client, prepared):
    native, authority, time, calls = client
    adapter = NativeFetchAdapter(native, clock=lambda: time[0])
    first, acquired = await adapter.acquire(prepared)
    assert first.status == "executed" and len(acquired) == 1
    task = await acquired[0].worker_task(TestMetadata())
    assert type(task.variables["valor_estimado"]) is AuthExactAmount
    assert _ceiling_valor_cents(task.variables["valor_estimado"]) == 9007199254740993
    assert task.business_key == "protected-engine-guide" and task.activity_id == "actual-activity"
    with pytest.raises(FetchUnavailableError):
        await acquired[0].worker_task(TestMetadata())
    with pytest.raises(FetchUnavailableError):
        copy.copy(acquired[0])
    with pytest.raises(FetchUnavailableError):
        await native.fetch(prepared)
    assert [route for _, route, _ in calls] == [
        "/maezo-workload/v2/readiness",
        "/maezo-workload/v2/operations",
    ]
    await native.close()


@pytest.mark.asyncio
async def test_expiry_after_response_refuses_projection_and_retry(client, prepared, monkeypatch):
    native, authority, time, calls = client
    exchange = native._exchange

    async def late(method, route, body, guard):
        value = await exchange(method, route, body, guard)
        if route.endswith("operations"):
            time[0] = authority.until
        return value

    monkeypatch.setattr(native, "_exchange", late)
    with pytest.raises(FetchUnavailableError):
        await native.fetch(prepared)
    assert native.attempts[0].state == "ambiguous"
    with pytest.raises(FetchUnavailableError):
        await native.fetch(prepared)
    assert len(calls) == 2
    await native.close()


@pytest.mark.asyncio
async def test_expiry_during_metadata_read_refuses_worker(client, prepared):
    native, authority, time, _ = client
    _, acquired = await NativeFetchAdapter(native, clock=lambda: time[0]).acquire(prepared)

    class LateMetadata(TestMetadata):
        async def acquire(self, *args):
            lease = await super().acquire(*args)
            time[0] = authority.until
            return lease

    with pytest.raises(FetchUnavailableError):
        await acquired[0].worker_task(LateMetadata())
    await native.close()


@pytest.mark.asyncio
async def test_absence_is_read_only_and_does_not_reset_ambiguity(client, prepared, monkeypatch):
    native, _, _, calls = client
    exchange = native._exchange

    async def failed(method, route, body, guard):
        if route.endswith("operations"):
            calls.append((method, route, body))
            raise ValueError("private socket error")
        return await exchange(method, route, body, guard)

    monkeypatch.setattr(native, "_exchange", failed)
    with pytest.raises(FetchUnavailableError, match="^native_fetch_unavailable$"):
        await native.fetch(prepared)
    recovered = await native.recover(prepared)
    assert recovered.status == "not_observed" and recovered.rows == ()
    with pytest.raises(FetchUnavailableError):
        await native.fetch(prepared)
    assert native.attempts[0].state == "ambiguous"
    assert sum(route.endswith("operations") for _, route, _ in calls) == 1
    await native.close()


@pytest.mark.asyncio
async def test_cancel_retains_actual_child_until_terminal(client, prepared, monkeypatch):
    native, _, _, _ = client
    exchange = native._exchange
    started, release = asyncio.Event(), asyncio.Event()

    async def blocked(method, route, body, guard):
        if route.endswith("operations"):
            started.set()
            await release.wait()
        return await exchange(method, route, body, guard)

    monkeypatch.setattr(native, "_exchange", blocked)
    caller = asyncio.create_task(native.fetch(prepared))
    await started.wait()
    caller.cancel()
    with pytest.raises(asyncio.CancelledError):
        await caller
    assert not await native.close(0)
    assert native.attempts[0].state == "ambiguous"
    release.set()
    assert await native.close(1)


@pytest.fixture
def tls_material(prepared, tmp_path):
    key = Ed25519PrivateKey.generate()
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "unit")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(1)
        .not_valid_before(NOW - timedelta(days=1))
        .not_valid_after(NOW + timedelta(seconds=4))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.SubjectAlternativeName([x509.UniformResourceIdentifier("spiffe://test/worker")]),
            critical=False,
        )
        .add_extension(x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.CLIENT_AUTH]), critical=False)
        .sign(key, None)
    )
    certificate = cert.public_bytes(serialization.Encoding.PEM)
    cert_path, key_path = tmp_path / "cert.pem", tmp_path / "key.pem"
    cert_path.write_bytes(certificate)
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        )
    )
    public = key.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    tls = NativeTLS(
        "https://native.test:8443",
        encode(prepared.profile.value()["identity"]),
        cert_path,
        sha(certificate),
        cert_path,
        cert.fingerprint(hashes.SHA256()).hex(),
        key_path,
        sha(public),
    )
    return tls, cert


def test_real_tls_snapshot_pins_and_identity(tls_material):
    tls, _ = tls_material
    context, until = tls.material(NOW)
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
    assert until == NOW + timedelta(seconds=4)
    with pytest.raises(FetchUnavailableError):
        dataclasses.replace(tls, certificate_sha256=HASH).material(NOW)
    with pytest.raises(FetchUnavailableError):
        dataclasses.replace(tls, origin="https://native.test:8443/engine-rest").material(NOW)


@pytest.mark.asyncio
@pytest.mark.parametrize("expiry", ["none", "response_exit", "client_exit", "peer_after_chunk"])
async def test_actual_stream_final_deadlines(client, tls_material, monkeypatch, expiry):
    native, _, time, _ = client
    tls, certificate = tls_material
    native._tls = tls

    class Stream:
        def get_extra_info(self, name):
            assert name == "ssl_object"
            return self

        def getpeercert(self, binary_form):
            assert binary_form is True
            return certificate.public_bytes(serialization.Encoding.DER)

    class Response:
        headers = {"content-type": "application/json"}
        extensions = {"network_stream": Stream()}
        status_code = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            if expiry == "response_exit":
                time[0] = NOW + timedelta(seconds=1)

        async def aiter_bytes(self):
            yield b"{}"
            if expiry == "peer_after_chunk":
                time[0] = NOW + timedelta(seconds=4)

    class Client:
        def __init__(self, **options):
            assert options["trust_env"] is False and options["follow_redirects"] is False
            assert isinstance(options["verify"], ssl.SSLContext)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            if expiry == "client_exit":
                time[0] = NOW + timedelta(seconds=1)

        def stream(self, method, url, **options):
            assert method == "GET" and url == "https://native.test:8443/maezo-workload/v2/readiness"
            assert options["content"] is None
            return Response()

    def current():
        if expiry != "peer_after_chunk" and time[0] >= NOW + timedelta(seconds=1):
            raise FetchUnavailableError()

    monkeypatch.setattr("maezo.gateway.native_fetch.transport.httpx.AsyncClient", Client)
    exchange = NativeFetchClient._exchange(native, "GET", "/maezo-workload/v2/readiness", None, current)
    if expiry == "none":
        assert await exchange == (200, b"{}")
    else:
        with pytest.raises(FetchUnavailableError):
            await exchange
    await native.close()


@pytest.mark.asyncio
async def test_current_provider_callback_cannot_extend_ceiling(client, prepared):
    native, authority, time, calls = client
    original = authority.acquire

    async def delayed(*args):
        lease = await original(*args)
        return dataclasses.replace(lease, live=lambda: time.__setitem__(0, authority.until))

    authority.acquire = delayed
    with pytest.raises(FetchUnavailableError):
        await native.fetch(prepared)
    assert calls == []
    await native.close()


@pytest.mark.asyncio
async def test_classification_is_part_of_authenticated_selection(client, prepared):
    native, authority, _, _ = client
    other_profile = dataclasses.replace(prepared.profile, classifications=(("tenant_id", ("string",)),))
    other = NativeFetchClient(native._tls, (other_profile,), authority)
    assert sha(native.selection_document) != sha(other.selection_document)
    with pytest.raises(FetchUnavailableError):
        copy.copy(native)
    await native.close()
    await other.close()


@pytest.mark.asyncio
async def test_receipt_cannot_reopen_or_replace_first_projection(client, prepared):
    native, _, _, _ = client
    initial = await native.fetch(prepared)
    with pytest.raises(FetchUnavailableError):
        native.claim(prepared, dataclasses.replace(initial))
    current = native.claim(prepared, initial)
    current()
    with pytest.raises(FetchUnavailableError):
        native.claim(prepared, initial)
    await native.close()
    with pytest.raises(FetchUnavailableError):
        current()


@pytest.mark.asyncio
async def test_bad_metadata_binding_and_revocation_refuse(client, prepared):
    native, authority, time, _ = client
    _, acquired = await NativeFetchAdapter(native, clock=lambda: time[0]).acquire(prepared)

    class WrongMetadata(TestMetadata):
        async def acquire(self, *args):
            return dataclasses.replace(await super().acquire(*args), acquisition_ref=REF)

    with pytest.raises(FetchUnavailableError):
        await acquired[0].worker_task(WrongMetadata())
    authority.revoked = True
    with pytest.raises(FetchUnavailableError, match="^native_fetch_unavailable$"):
        acquired[0]._guard()
    with pytest.raises(FetchUnavailableError):
        acquired[0]._row = b"{}"
    await native.close()


@pytest.mark.parametrize("state,http_status", [("conflict", 409), ("unavailable", 503), ("duplicate", 200)])
def test_exact_nonexecuted_union(prepared, state, http_status):
    assert result(encode(response(prepared, state)), http_status, prepared).rows == ()
    with pytest.raises(FetchUnavailableError):
        result(encode(response(prepared, state)), 400, prepared)


@pytest.mark.parametrize(
    "tag,value",
    [
        ("boolean", True),
        ("integer", 2**31 - 1),
        ("long", 2**63 - 1),
        ("double", 1.5),
        ("null", None),
        ("json", {"safe": [True, 1]}),
        ("string", None),
    ],
)
def test_native_primitive_codec_keeps_classified_types(tag, value):
    from maezo.gateway.native_fetch.models import variable

    variable({"type": tag, "value": value}, (tag,))


@pytest.mark.parametrize(
    "tag,value",
    [
        ("boolean", 1),
        ("integer", 2**31),
        ("long", True),
        ("long", "1"),
        ("null", "null"),
        ("double", True),
        ("string", {}),
    ],
)
def test_native_primitive_codec_refuses_coercion(tag, value):
    from maezo.gateway.native_fetch.models import variable

    with pytest.raises(FetchUnavailableError):
        variable({"type": tag, "value": value}, (tag,))


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["fetch", "recover"])
@pytest.mark.parametrize("handoff", ["current", "expired", "revoked"])
async def test_original_admission_guards_public_terminal_delivery(
    client, prepared, monkeypatch, operation, handoff
):
    native, authority, time, calls = client
    original_exchange, original_acquire = native._exchange, authority.acquire
    terminal_route = "operations" if operation == "fetch" else "outcomes"
    acquired = []
    delivered = []

    async def capture(*args):
        lease = await original_acquire(*args)
        acquired.append(lease)
        return lease

    def at_handoff():
        delivered.append(True)
        if handoff == "expired":
            time[0] = acquired[0].valid_until  # Exact original exclusive bound.
        elif handoff == "revoked":
            authority.revoked = True

    async def exchange(method, route, body, guard):
        answer = await original_exchange(method, route, body, guard)
        if route.endswith(terminal_route):
            asyncio.get_running_loop().call_soon(at_handoff)
        return answer

    monkeypatch.setattr(authority, "acquire", capture)
    monkeypatch.setattr(native, "_exchange", exchange)
    if handoff == "current":
        value = await getattr(native, operation)(prepared)
        assert value.status == ("executed" if operation == "fetch" else "not_observed")
    else:
        with pytest.raises(FetchUnavailableError, match="^native_fetch_unavailable$"):
            await getattr(native, operation)(prepared)
    assert delivered and len(acquired) == 1  # No reacquisition or renewal to return old data.
    assert acquired[0].valid_until == authority.until
    assert acquired[0].binding == prepared.binding
    assert sum(route.endswith(terminal_route) for _, route, _ in calls) == 1
    if operation == "fetch":
        # The child completed and accounted for execution before delivery expired.
        # Keep that technical receipt, not an invented rollback or no-effect state.
        assert native.attempts[0].state == "executed"
        assert native.attempts[0].receipt is not None
        with pytest.raises(FetchUnavailableError):
            await native.fetch(prepared)
        assert sum(route.endswith("operations") for _, route, _ in calls) == 1
    else:
        assert native.attempts == ()  # Receipt query never creates a fetch attempt.
    await native.close()
