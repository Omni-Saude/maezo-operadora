"""Synthetic private wire vectors; no engine or production-authority claim."""

import base64
import hashlib
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from pydantic import ValidationError

from maezo.gateway.human.auth_profile import (
    Actor,
    AuditIntent,
    Definition,
    HumanReceiptQuery,
    HumanStartCommand,
    InputPublication,
    NativeEffectReceipt,
    NativeReceiptLookup,
    Pin,
    Scope,
    SessionBinding,
)
from maezo.gateway.human.auth_transport import (
    AuthCredentialLease,
    AuthEffectCeiling,
    AuthNativeTrustLease,
    AuthUnavailableError,
    sign_request,
    verify_result,
)
from maezo.gateway.human.read_profile import SourceProvenance, digest, parse_model, wire
from maezo.portal.engine.profile import ProfileError, canonicalize, strict_loads

NOW = datetime(2026, 9, 10, 12, tzinfo=UTC)
HASH = "a" * 64
KEY = Ed25519PrivateKey.from_private_bytes(b"a" * 32)
NATIVE = Ed25519PrivateKey.from_private_bytes(b"n" * 32)


def scope():
    return Scope(
        tenant="tenant",
        environment="test",
        engine_name="engine",
        database_incarnation="db",
        installation_ref="installed",
        installation_revision=1,
    )


def source():
    return SourceProvenance(
        publisher_ref="publisher",
        source_ref="source",
        source_revision=1,
        source_digest=HASH,
        receipt_ref="source-receipt",
        observed_at=NOW,
        valid_until=NOW + timedelta(seconds=60),
    )


def command():
    actor = Actor(
        principal_ref="principal",
        issuer="https://identity.example",
        subject="subject",
        membership_revision=1,
        audience="provider",
    )
    pins = tuple(
        Pin(kind=kind, resource_ref=ref, head_generation=1, source=source(), payload_digest=HASH)
        for kind, ref in [
            ("actor", "principal"),
            ("document_policy", "assessment"),
            ("guide", "guide"),
            ("resource_authority", "authority"),
            ("start_facts", "facts"),
        ]
    )
    return HumanStartCommand(
        schema="human-auth-start.v1",
        scope=scope(),
        workload_ref="auth-sender",
        actor=actor,
        intake_ref="intake",
        command_id="command",
        admission=AuditIntent(
            intent_ref="intake", admitted_command_id="command", admitted_digest=HASH, source=source()
        ),
        guide_identity_ref="guide",
        definition=Definition(
            process_key="SP-OP-AUTH-001",
            definition_id="definition",
            definition_digest=HASH,
            deployment_id="deployment",
            input_profile="portal-auth-intake.v1",
            profile_digest=HASH,
        ),
        input_pins=pins,
        start_facts_ref="facts",
        start_facts_digest=HASH,
        projected_variables_digest=HASH,
    )


def effect_cap(c=None, seconds=20):
    c = c or command()
    return AuthEffectCeiling(
        digest(c),
        SessionBinding(
            session_ref="session",
            authenticated_at=NOW - timedelta(seconds=5),
            session_expires_at=NOW + timedelta(seconds=60),
            authorization_until=NOW + timedelta(seconds=seconds),
            session_source_revision=1,
            session_record_digest=HASH,
        ),
        NOW + timedelta(seconds=seconds),
    )


def receipt(c=None):
    c = c or command()
    return NativeEffectReceipt(
        schema="human-auth-effect-receipt.v1",
        scope=c.scope,
        receipt_ref="receipt",
        command_id=c.command_id,
        command_digest=digest(c),
        operation="auth.start",
        actor_principal_ref=c.actor.principal_ref,
        admission_ref=c.admission.intent_ref,
        admitted_digest=c.admission.admitted_digest,
        intake_ref=c.intake_ref,
        case_ref="case",
        process_instance_id="instance",
        definition=c.definition,
        guide_identity_ref=c.guide_identity_ref,
        request_ref=None,
        occurrence_generation=None,
        subscription_id=None,
        document_set_digest=None,
        outcome="started",
        committed_at=NOW,
    )


def signing(purpose="human-auth-start"):
    return AuthCredentialLease(
        scope(),
        purpose,
        "auth-sender",
        "client-key",
        "native",
        hashlib.sha256(
            KEY.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
        ).hexdigest(),
        HASH,
        NOW - timedelta(seconds=1),
        NOW + timedelta(seconds=60),
        30,
        KEY,
        lambda: None,
    )


def trust():
    return AuthNativeTrustLease(
        scope(),
        "native",
        "native-key",
        "gateway",
        HASH,
        NOW - timedelta(seconds=1),
        NOW + timedelta(seconds=60),
        30,
        NATIVE.public_key(),
        lambda: None,
    )


def envelope(result, expires=30):
    value = dict(
        schema="human-auth-envelope.v1",
        purpose="human-auth-result",
        algorithm="Ed25519",
        audience="gateway",
        issuer="native",
        tenant="tenant",
        key_id="native-key",
        issued_at=str(int(NOW.timestamp())),
        expires_at=str(int(NOW.timestamp()) + expires),
        digest=digest(result),
        command=wire(result),
    )
    value["signature"] = base64.urlsafe_b64encode(NATIVE.sign(canonicalize(value))).rstrip(b"=").decode()
    return canonicalize(value)


def test_command_roundtrip_and_independent_signature_layout():
    c = command()
    raw = sign_request(c, signing(), NOW, effect_ceiling=effect_cap(c))
    value = strict_loads(raw)
    assert value["digest"] == hashlib.sha256(canonicalize(wire(c))).hexdigest()
    signature = base64.urlsafe_b64decode(value.pop("signature") + "==")
    KEY.public_key().verify(signature, canonicalize(value))
    assert parse_model(HumanStartCommand, value["command"]) == c
    assert value["command"]["scope"]["installation_revision"] == "1"


@pytest.mark.parametrize("bad", [1, True, "01", "+1", "1.0", "1e0", "9223372036854775808"])
def test_noncanonical_nested_revision_refused(bad):
    value = wire(command())
    value["scope"]["installation_revision"] = bad
    with pytest.raises((ProfileError, ValidationError)):
        parse_model(HumanStartCommand, value)


@pytest.mark.parametrize("change", ["extra", "missing", "wrong-schema", "duplicate-pin", "numeric-boolean"])
def test_closed_command_refuses_unrecognized_shape(change):
    value = wire(command())
    if change == "extra":
        value["variables"] = {}
    elif change == "missing":
        del value["admission"]["source"]["receipt_ref"]
    elif change == "wrong-schema":
        value["schema"] = "agent-decision.v1"
    elif change == "duplicate-pin":
        value["input_pins"].append(value["input_pins"][0])
    else:
        value["actor"]["membership_revision"] = False
    with pytest.raises((ProfileError, ValidationError)):
        parse_model(HumanStartCommand, value)


def test_native_result_signature_and_binding():
    c = command()
    r = receipt(c)
    assert verify_result(envelope(r), c, trust(), NOW) == r


@pytest.mark.parametrize(
    "field,value",
    [
        ("command_id", "another"),
        ("command_digest", "b" * 64),
        ("intake_ref", "another"),
        ("guide_identity_ref", "another"),
        ("actor_principal_ref", "another"),
        ("admitted_digest", "b" * 64),
    ],
)
def test_signed_wrong_receipt_cannot_bind(field, value):
    c = command()
    r = receipt(c).model_copy(update={field: value})
    with pytest.raises(AuthUnavailableError):
        verify_result(envelope(r), c, trust(), NOW)


@pytest.mark.parametrize("mutation", ["signature", "purpose", "issuer", "scope", "expired"])
def test_invalid_native_authentication(mutation):
    c = command()
    raw = envelope(receipt(c))
    v = strict_loads(raw)
    if mutation == "signature":
        v["signature"] = "A" * 86
    elif mutation == "purpose":
        v["purpose"] = "human-command"
    elif mutation == "issuer":
        v["issuer"] = "auth-sender"
    elif mutation == "scope":
        v["command"]["scope"]["database_incarnation"] = "other"
    if mutation != "expired":
        raw = canonicalize(v)
    with pytest.raises((AuthUnavailableError, InvalidSignature, ProfileError)):
        verify_result(raw, c, trust(), NOW + timedelta(seconds=30) if mutation == "expired" else NOW)


def test_receipt_absence_binds_fresh_query_not_original_effect():
    c = command()
    q = HumanReceiptQuery(
        schema="human-auth-receipt-query.v1",
        scope=c.scope,
        workload_ref=c.workload_ref,
        actor=c.actor,
        query_id="query",
        operation="auth.start",
        command_id=c.command_id,
        expected_command_digest=digest(c),
        intake_or_case_ref=c.intake_ref,
    )
    result = NativeReceiptLookup(
        schema="human-auth-receipt-lookup.v1",
        scope=c.scope,
        query_id=q.query_id,
        query_digest=digest(q),
        observed_at=NOW,
        status="absent",
        receipt=None,
    )
    assert verify_result(envelope(result), q, trust(), NOW) == result
    with pytest.raises(AuthUnavailableError):
        verify_result(envelope(result), q.model_copy(update={"query_id": "new-query"}), trust(), NOW)


def test_dedicated_purpose_cannot_use_legacy_or_read_signer():
    with pytest.raises(AuthUnavailableError):
        sign_request(command(), signing("human-auth-read"), NOW)


def test_input_tombstone_cannot_carry_payload_digest():
    with pytest.raises(ValidationError):
        InputPublication(
            schema="human-auth-input-publication.v1",
            scope=scope(),
            workload_ref="publisher",
            publication_id="publication",
            kind="guide",
            resource_ref="guide",
            expected_generation=0,
            source=source(),
            state="frozen",
            payload=None,
            payload_digest=HASH,
            valid_until=NOW + timedelta(seconds=30),
        )


def test_native_receipt_cannot_claim_documents_without_occurrence():
    with pytest.raises(ValidationError):
        NativeEffectReceipt.model_validate(
            receipt().model_dump()
            | {"operation": "auth.documents.respond", "outcome": "documents_correlated"}
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode", ["valid", "wrong-peer", "redirect", "oversize", "release-expiry", "source-expiry"]
)
async def test_real_http_client_peer_boundaries_and_final_io_deadlines(mode):
    import ssl
    from dataclasses import replace

    import httpx
    from cryptography import x509
    from cryptography.x509.oid import NameOID

    from maezo.gateway.human.auth_transport import AuthNativeClient

    def certificate(key):
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "synthetic.test")])
        return (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(key.public_key())
            .serial_number(1)
            .not_valid_before(NOW - timedelta(days=1))
            .not_valid_after(NOW + timedelta(days=1))
            .sign(key, None)
            .public_bytes(Encoding.DER)
        )

    server_cert = certificate(NATIVE)
    client_cert = certificate(KEY)
    server_pin = hashlib.sha256(
        NATIVE.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    ).hexdigest()
    client_pin = hashlib.sha256(
        KEY.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    ).hexdigest()
    now = [NOW]
    live = [True]

    class Provider:
        async def acquire(self, scope, purpose):
            return replace(signing(purpose), peer_spki_sha256=client_pin)

        async def native_trust(self, scope):
            return replace(trust(), server_spki_sha256=server_pin)

    class TLS:
        def getpeercert(self, **kwargs):
            return client_cert if mode == "wrong-peer" else server_cert

    class Stream:
        def get_extra_info(self, name):
            return TLS()

    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"x" * 65537 if mode == "oversize" else envelope(receipt())

        async def aclose(self):
            if mode == "release-expiry":
                now[0] += timedelta(seconds=30)
            if mode == "source-expiry":
                live[0] = False

    sent = []

    async def respond(request):
        sent.append(request)
        assert request.url.path == "/maezo-human/v1/auth-start"
        return httpx.Response(
            302 if mode == "redirect" else 200,
            headers={"content-type": "application/json", "cache-control": "no-store"},
            stream=Body(),
            extensions={"network_stream": Stream()},
        )

    def current():
        if not live[0]:
            raise AuthUnavailableError()

    async def checkpoint():
        current()

    client = AuthNativeClient(
        origin="https://native.test",
        tls_context=ssl.create_default_context(),
        credentials=Provider(),
        client_certificate_der=client_cert,
        transport=httpx.MockTransport(respond),
        clock=lambda: now[0],
    )
    try:
        if mode == "valid":
            assert (
                await client.execute(
                    command(), current=current, checkpoint=checkpoint, effect_ceiling=effect_cap()
                )
                == receipt()
            )
        else:
            with pytest.raises(AuthUnavailableError):
                await client.execute(
                    command(), current=current, checkpoint=checkpoint, effect_ceiling=effect_cap()
                )
        assert len(sent) == 1
    finally:
        await client.close()
