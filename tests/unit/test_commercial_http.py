"""Candidate-wire HTTP UNIT: synthetic verified ports, no engine/REAL integration.

The immutable TEST artifact records exact source-owned C6-4 bytes and provenance.
An optional COMMERCIAL_CANDIDATE_SCHEMA override must match those same bytes.
This artifact is never runtime admission or an editable canonical schema.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI, Request

from maezo.adapters.amh.commercial import CANDIDATE_SCHEMA_SHA256, CommercialWire
from maezo.adapters.amh.commercial_http import AmhCommercialAuthority, AmhConnection
from maezo.gateway.commercial_admission import (
    SalesCallerVerifier,
    SalesScopeAuthority,
    VerifiedSalesConnection,
    compose_commercial_gateway,
)
from maezo.portal.api.commercial import COMMERCIAL_PATH, commercial_command
from maezo.ports.commercial import (
    CommercialPreparation,
    CommercialPublication,
    CommercialRefusalError,
    CommercialScope,
    command_digest,
    scope_digest,
)


@pytest.fixture
def schema_bytes():
    # Required for this actual-codec evidence. Missing source artifact is a failure,
    # never converted to a skip, invented local schema or synthetic codec.
    artifact_dir = Path(__file__).resolve().parents[1] / "candidate_artifacts" / "commercial"
    provenance = json.loads((artifact_dir / "PROVENANCE-C6-4.json").read_text(encoding="utf-8"))
    assert provenance["schema"] == "commercial-candidate-test-custody.v1"
    assert provenance["classification"] == "IMMUTABLE_CANDIDATE_TEST_ARTIFACT_NOT_CANONICAL_OR_PUBLISHED"
    assert provenance["source_repository"] == "Omni-Saude/amh-data-platform"
    assert provenance["source_commit"] == "b3ab95a07a016526aa5dc09ead22aafccd630b45"
    assert len(provenance["source_commit"]) == 40
    assert all(character in "0123456789abcdef" for character in provenance["source_commit"])
    assert provenance["source_path"] == (
        "schemas/proposals/maezo/commercial-v1/shared-contract-c6-4.proposed.v1.json"
    )
    expected_digest = "d1d796b8fd4546bd8d684baa9dd58c4536c3745dc2dce748909f69384eebd1ea"
    assert provenance["source_sha256"] == CANDIDATE_SCHEMA_SHA256 == expected_digest
    assert provenance["publication"] == "NOT_PUBLISHED"
    assert provenance["runtime_admission"] == "UNBOUND"
    assert provenance["runtime_import_allowed"] is False
    assert provenance["existing_published_pin_modified"] is False
    artifact = (artifact_dir / "contract-c6-4.json").read_bytes()
    assert len(artifact) == provenance["bytes"] == 320163
    assert hashlib.sha256(artifact).hexdigest() == expected_digest
    override = os.environ.get("COMMERCIAL_CANDIDATE_SCHEMA")
    supplied = Path(override).read_bytes() if override is not None else artifact
    assert supplied == artifact
    assert hashlib.sha256(supplied).hexdigest() == expected_digest
    return supplied


def qualified_scope():
    now = datetime.now(UTC)
    return CommercialScope(
        admission_ref="SYN-ADMISSION",
        workload_ref="SYN-WORKLOAD",
        tenant_ref="SYN-TENANT",
        legal_entity_ref="SYN-ENTITY",
        purpose_ref="SYN-PURPOSE",
        lifecycle_ref="SYN-LIFECYCLE",
        identity_receipt_ref="SYN-IDENTITY",
        source_binding_revision="1",
        authority_revision="1",
        correlation_ref="SYN-CORRELATION",
        source_grant_ref="SYN-GRANT",
        publication_ref="SYN-PUB",
        allowed_operations=frozenset({"operator.get", "operation.get"}),
        valid_from=now - timedelta(seconds=5),
        valid_until=now + timedelta(minutes=5),
        audience="account",
        session_ref="SYN-SESSION",
        actor_ref="SYN-ACTOR",
        actor_kind="customer",
        account_ref="SYN-ACCOUNT",
    )


def wire_command():
    return {
        "schema_version": "omni-commercial.proposed.v1",
        "command": "operator.get",
        "command_ref": "SYN-CMD",
        "expected_revision": None,
        "payload": {"operator_ref": "SYN-OPERATOR"},
    }


def unavailable_capabilities(kind, ref):
    return {
        "status": "unavailable",
        "scope": {"kind": kind, "ref": ref},
        "reason": "refresh_required",
        "choices": [],
        "default_card_choice_ref": None,
    }


def wire_result():
    return {
        "schema_version": "omni-commercial.proposed.v1",
        "status": "succeeded",
        "operation_ref": "SYN-OP",
        "reason_code": None,
        "data": {
            "kind": "operator",
            "operator_ref": "SYN-OPERATOR",
            "display_name": "Synthetic operator",
            "revision": 1,
            "configuration_state": "qualified",
            "available_methods": ["pix", "card", "boleto"],
            "contracting_ready": True,
            "payment_capabilities": unavailable_capabilities("operator", "SYN-OPERATOR"),
        },
    }


class UnitCaller(SalesCallerVerifier):
    async def authenticate_and_resolve(self, credential, connection_ref):
        if credential != "SYN-SALES-CREDENTIAL" or connection_ref != "SYN-SALES-CONNECTION":
            raise CommercialRefusalError("denied", "admission_required")
        return VerifiedSalesConnection(
            connection_ref=connection_ref,
            authenticated_caller_ref="SYN-SALES-CALLER",
            valid_until=datetime.now(UTC) + timedelta(minutes=1),
        )


class UnitScope(SalesScopeAuthority):
    def __init__(self):
        self.scope = qualified_scope()
        self.phases = []
        self.revoke = None

    async def acquire(self, connection_ref, authenticated_caller_ref):
        assert (connection_ref, authenticated_caller_ref) == ("SYN-SALES-CONNECTION", "SYN-SALES-CALLER")
        return self.scope

    async def current(self, scope, *, phase, command=None):
        self.phases.append(phase)
        if phase == self.revoke:
            raise CommercialRefusalError("denied", "authority_revoked")
        return scope


class UnitSourceAuthority(AmhCommercialAuthority):
    def __init__(self, scope):
        self.proof = CommercialPublication(
            publication_ref=scope.publication_ref,
            source_contract_digest="a" * 64,
            shared_contract_digest=CANDIDATE_SCHEMA_SHA256,
            source_binding_ref=scope.source_grant_ref,
            source_binding_revision=scope.source_binding_revision,
            tenant_ref=scope.tenant_ref,
            legal_entity_ref=scope.legal_entity_ref,
            currentness_ref="SYN-CURRENT",
            valid_until=scope.valid_until,
        )
        self.phases = []

    async def publication(self, scope):
        return self.proof

    async def prepare(self, command, scope):
        return CommercialPreparation(
            operation_ref="SYN-OP",
            command_digest=command_digest(command),
            scope_digest=scope_digest(scope),
            source_evidence_ref="SYN-SOURCE-EVIDENCE",
            publication=self.proof,
            valid_until=scope.valid_until,
        )

    async def connection(self, command, scope, preparation):
        return AmhConnection(
            connection_ref="SYN-AMH-CONNECTION",
            operation_ref=preparation.operation_ref,
            command_digest=command_digest(command),
            scope_digest=scope_digest(scope),
            valid_until=scope.valid_until,
        )

    async def current(self, command, scope, preparation, connection, *, phase):
        self.phases.append(phase)
        assert connection.operation_ref == preparation.operation_ref

    async def credential(self, connection):
        return "SYN-MAEZO-CREDENTIAL"


def make_request(
    gateway, *, authorization=b"Bearer SYN-SALES-CREDENTIAL", connection=b"SYN-SALES-CONNECTION", receive=None
):
    app = FastAPI()
    app.state.commercial_gateway = gateway

    async def receive_body():
        return {"type": "http.request", "body": json.dumps(wire_command()).encode(), "more_body": False}

    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": COMMERCIAL_PATH,
            "query_string": b"",
            "headers": [
                (b"authorization", authorization),
                (b"x-commercial-connection", connection),
                (b"content-type", b"application/json"),
                (b"cookie", b"browser-cookie-never-forward"),
            ],
            "app": app,
        },
        receive=receive or receive_body,
    )


def composition(schema_bytes, handler):
    admission = UnitScope()
    authority = UnitSourceAuthority(admission.scope)
    gateway = compose_commercial_gateway(
        schema_bytes=schema_bytes,
        amh_origin="https://amh.invalid",
        caller=UnitCaller(),
        scope_authority=admission,
        source_authority=authority,
        transport=httpx.MockTransport(handler),
    )
    return gateway, admission, authority


@pytest.mark.asyncio
async def test_actual_candidate_codec_and_fixed_authenticated_http_positive(schema_bytes):
    sent = []

    async def amh(request):
        sent.append(request)
        assert str(request.url) == "https://amh.invalid/interop/sales/v1/commercial"
        assert request.headers["authorization"] == "Bearer SYN-MAEZO-CREDENTIAL"
        assert request.headers["x-commercial-connection"] == "SYN-AMH-CONNECTION"
        assert "cookie" not in request.headers
        assert json.loads(request.content) == wire_command()
        return httpx.Response(200, json=wire_result())

    gateway, admission, source = composition(schema_bytes, amh)
    response = await commercial_command(make_request(gateway))
    assert response.status_code == 200
    assert json.loads(bytes(response.body)) == wire_result()
    assert len(sent) == 1
    assert admission.phases == ["before_body", "before_source", "before_effect", "before_disclosure"]
    assert source.phases == ["before_io", "before_disclosure"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "authorization,connection",
    [
        (b"Bearer browser-claim", b"SYN-SALES-CONNECTION"),
        (b"Bearer SYN-SALES-CREDENTIAL", b"FOREIGN-CONNECTION"),
    ],
)
async def test_authenticated_connection_binding_before_any_body(schema_bytes, authorization, connection):
    async def forbidden(*args):
        raise AssertionError("No body or network before caller/source qualification")

    gateway, _, _ = composition(schema_bytes, forbidden)
    response = await commercial_command(
        make_request(gateway, authorization=authorization, connection=connection, receive=forbidden)
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_default_composition_refuses_before_body(schema_bytes):
    async def forbidden():
        raise AssertionError("body")

    gateway = compose_commercial_gateway(schema_bytes=schema_bytes)
    response = await commercial_command(make_request(gateway, receive=forbidden))
    assert response.status_code == 503


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [
        "lost",
        "oversize",
        "duplicate",
        "wrong-operation",
        "wrong-target",
        "redirect",
        "null-status",
        "array-status",
        "unknown-status",
    ],
)
async def test_uncertainty_preserves_original_identity_without_post_retry(schema_bytes, failure):
    sent = []

    async def amh(request):
        sent.append(request)
        if failure == "lost":
            raise httpx.ReadTimeout("synthetic dropped response")
        if failure == "oversize":
            return httpx.Response(
                200, content=b" " * (1024 * 1024 + 1), headers={"Content-Type": "application/json"}
            )
        if failure == "duplicate":
            return httpx.Response(
                200,
                content=b'{"status":"succeeded","status":"denied"}',
                headers={"Content-Type": "application/json"},
            )
        if failure == "redirect":
            return httpx.Response(307, headers={"Location": "https://foreign.invalid"})
        result = wire_result()
        if failure in {"null-status", "array-status", "unknown-status"}:
            result["status"] = {"null-status": None, "array-status": [], "unknown-status": "foreign"}[failure]
        elif failure == "wrong-operation":
            result["operation_ref"] = "FOREIGN"
        else:
            result["data"]["operator_ref"] = "FOREIGN"
        return httpx.Response(200, json=result)

    gateway, _, _ = composition(schema_bytes, amh)
    response = await commercial_command(make_request(gateway))
    assert response.status_code == 202
    body = json.loads(bytes(response.body))
    assert body["operation_ref"] == "SYN-OP"
    assert body["data"] is None
    assert len(sent) == 1
    assert json.loads(sent[0].content)["command_ref"] == "SYN-CMD"


@pytest.mark.asyncio
async def test_revocation_before_disclosure_suppresses_result(schema_bytes):
    async def amh(request):
        return httpx.Response(200, json=wire_result())

    gateway, admission, _ = composition(schema_bytes, amh)
    admission.revoke = "before_disclosure"
    response = await commercial_command(make_request(gateway))
    assert response.status_code == 202
    assert json.loads(bytes(response.body))["data"] is None


# AS-CODE-06: source-shaped synthetic opaque URL; no real Checkout Session.
STRIPE_HOSTED_URL = "https://checkout.stripe.com/c/pay/cs_test_SYN#fidkdWxOYHwn%2FM2F8bUA2%3dopaque"


def hosted_case(kind, provider="stripe", method="card", url=STRIPE_HOSTED_URL):
    action = {"action_ref": "SYN-HOSTED", "url": url, "expires_at": "2099-01-01T00:00:00Z"}
    if kind == "payment":
        command = {
            **wire_command(),
            "command": "payment.create",
            "expected_revision": 1,
            "payload": {
                "receivable_ref": "SYN-REC",
                "method": method,
                "provider": provider,
                "mandate_ref": None,
                "choice_ref": "SYN-CHOICE",
            },
        }
        data = {
            "kind": "payment",
            "payment": {
                "attempt_ref": "SYN-ATTEMPT",
                "receivable_ref": "SYN-REC",
                "revision": 1,
                "method": method,
                "provider": provider,
                "state": "pending",
                "money": {"amount_minor": 100, "currency": "BRL"},
                "paid_receipt_ref": None,
                "settled_receipt_ref": None,
                "tasy_ack_state": "pending",
                "tasy_ack_receipt_ref": None,
                "hosted_action": action,
                "observed_method": None,
                "observed_method_receipt_ref": None,
                "payment_capabilities": unavailable_capabilities("payment", "SYN-ATTEMPT"),
            },
        }
    else:
        command = {
            **wire_command(),
            "command": "mandate.create",
            "payload": {"contract_ref": "SYN-CONTRACT", "provider": provider, "choice_ref": "SYN-CHOICE"},
        }
        data = {
            "kind": "mandate",
            "mandate": {
                "mandate_ref": "SYN-MANDATE",
                "revision": 1,
                "provider": provider,
                "state": "pending",
                "provider_receipt_ref": None,
                "hosted_action": action,
                "payment_capabilities": unavailable_capabilities("mandate", "SYN-MANDATE"),
            },
        }
    return command, {**wire_result(), "data": data}


@pytest.mark.parametrize("kind", ["payment", "mandate"])
def test_stripe_fragment_preserved_only_after_exact_origin_qualification(schema_bytes, kind):
    command, result = hosted_case(kind)
    codec = CommercialWire(
        schema_bytes, hosted_origins={"stripe": frozenset({"https://checkout.stripe.com"})}
    )
    parsed = codec.parse_result(
        json.dumps(result).encode(), codec.parse_command(json.dumps(command).encode())
    )
    assert json.loads(parsed.wire_bytes)["data"][kind]["hosted_action"]["url"] == STRIPE_HOSTED_URL
    unbound = CommercialWire(schema_bytes)
    with pytest.raises(CommercialRefusalError):
        unbound.parse_result(json.dumps(result).encode(), unbound.parse_command(json.dumps(command).encode()))


@pytest.mark.parametrize(
    "provider,method,url",
    [
        ("stripe", "card", "https://checkout.stripe.com.foreign/c/pay/cs_test_SYN#opaque"),
        ("stripe", "card", "https://user@checkout.stripe.com/c/pay/cs_test_SYN#opaque"),
        ("stripe", "card", "http://checkout.stripe.com/c/pay/cs_test_SYN#opaque"),
        ("asaas", "boleto", STRIPE_HOSTED_URL),
        ("infinitepay", "card", STRIPE_HOSTED_URL),
    ],
)
def test_fragment_does_not_relax_provider_origin_or_userinfo_guards(schema_bytes, provider, method, url):
    command, result = hosted_case("payment", provider, method, url)
    codec = CommercialWire(
        schema_bytes, hosted_origins={provider: frozenset({"https://checkout.stripe.com"})}
    )
    with pytest.raises(CommercialRefusalError):
        codec.parse_result(json.dumps(result).encode(), codec.parse_command(json.dumps(command).encode()))


@pytest.mark.parametrize("kind", ["payment", "mandate"])
def test_stripe_expired_fragment_refused_and_recovery_omits_hosted_action(schema_bytes, kind):
    original_command, result = hosted_case(kind)
    codec = CommercialWire(
        schema_bytes, hosted_origins={"stripe": frozenset({"https://checkout.stripe.com"})}
    )
    result["data"][kind]["hosted_action"]["expires_at"] = "2000-01-01T00:00:00Z"
    with pytest.raises(CommercialRefusalError):
        codec.parse_result(
            json.dumps(result).encode(), codec.parse_command(json.dumps(original_command).encode())
        )
    result["data"][kind]["hosted_action"]["expires_at"] = "2099-01-01T00:00:00Z"
    recovery = {**wire_command(), "command": "operation.get", "payload": {"target_command_ref": "SYN-CMD"}}
    recovered = {
        **wire_result(),
        "data": {
            "kind": "operation_status",
            "target_command_ref": "SYN-CMD",
            "original_command": original_command["command"],
            "state": "completed",
            "original_result": result,
        },
    }
    parsed = codec.parse_result(
        json.dumps(recovered).encode(), codec.parse_command(json.dumps(recovery).encode())
    )
    assert json.loads(parsed.wire_bytes)["data"]["original_result"]["data"][kind]["hosted_action"] is None
    assert STRIPE_HOSTED_URL.encode() not in parsed.wire_bytes
