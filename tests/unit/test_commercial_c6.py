"""C6 composed UNIT with synthetic authority/HTTP; no engine or source admission."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import get_args

import httpx
import pytest
from fastapi import FastAPI
from starlette.requests import Request

import maezo.adapters.amh.commercial as wire_module
from maezo.adapters.amh.commercial import CommercialWire, _strip_replay_actions
from maezo.adapters.amh.commercial_http import AmhPublicCatalogAuthority, PublicAmhConnection
from maezo.gateway.commercial_admission import (
    PublicCatalogScopeAuthority,
    compose_commercial_gateway,
    compose_public_catalog_gateway,
)
from maezo.portal.api.commercial import (
    PUBLIC_CATALOG_PATH,
    commercial_command,
    commercial_router,
    public_catalog,
)
from maezo.ports.commercial import (
    CommercialDeliveryOrigins,
    CommercialOperation,
    CommercialRefusalError,
    CommercialResultAuthority,
    PublicCatalogProof,
    PublicCatalogScope,
    command_digest,
    public_scope_digest,
    scope_digest,
)
from tests.unit.test_commercial_gateway import NOW, UnitCodec, composed, scope
from tests.unit.test_commercial_http import (
    UnitCaller,
    UnitScope,
    UnitSourceAuthority,
    hosted_case,
    sales_session_admission,
    support_handoff_admission,
    unavailable_capabilities,
    wire_command,
    wire_result,
)
from tests.unit.test_commercial_http import (
    schema_bytes as schema_bytes,
)


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def envelope(name, payload):
    return {
        **wire_command(),
        "command": name,
        "payload": payload,
        "expected_revision": 1
        if name in {"work.claim", "work.assign", "work.handoff", "work.reconcile", "support.handoff"}
        else None,
    }


def outcome(data):
    return {**wire_result(), "data": data}


def parse(codec, request, data):
    return codec.parse_result(encode(outcome(data)), codec.parse_command(encode(request)))


def qualified_caps(kind, ref, *, provider="stripe", method="card"):
    return {
        "status": "qualified",
        "scope": {"kind": kind, "ref": ref},
        "capability_ref": "SYN-CAP",
        "source_revision": 7,
        "verified_at": "2026-10-01T00:00:00Z",
        "valid_until": "2099-01-01T00:00:00Z",
        "choices": [
            {
                "choice_ref": "SYN-CHOICE",
                "provider": provider,
                "method": method,
                "operation": "payment.create",
                "use": "one_time",
            }
        ],
        "default_card_choice_ref": "SYN-CHOICE" if provider == "stripe" else None,
    }


def test_exact_command_and_nonrecursive_recovery_denominators(schema_bytes):
    schema = json.loads(schema_bytes)
    assert len(schema["x-commands"]) == len(get_args(CommercialOperation)) == 42
    assert set(schema["x-commands"]) == set(get_args(CommercialOperation))
    pairings = schema["$defs"]["operation_statusResultData"]["allOf"]
    originals = [
        p["if"]["properties"]["original_command"]["const"]
        for p in pairings
        if "if" in p and "original_command" in p["if"].get("properties", {})
    ]
    assert len(originals) == 41
    assert set(originals) == set(schema["x-commands"]) - {"operation.get"}
    with pytest.raises(ValueError):
        CommercialWire(schema_bytes + b" ")


def test_paid_method_is_independent_of_requested_choice(schema_bytes):
    codec = CommercialWire(
        schema_bytes, hosted_origins={"infinitepay": frozenset({"https://checkout.infinitepay.com.br"})}
    )
    request, result = hosted_case("payment", "infinitepay", "pix", "https://checkout.infinitepay.com.br/link")
    result["data"]["payment"].update(
        state="paid",
        paid_receipt_ref="SYN-PAID",
        observed_method="card",
        observed_method_receipt_ref="SYN-OBSERVED",
    )
    observed = codec.parse_result(encode(result), codec.parse_command(encode(request)))
    payment = json.loads(observed.wire_bytes)["data"]["payment"]
    assert payment["method"] == "pix" and payment["observed_method"] == "card"
    assert payment["tasy_ack_state"] == "pending"


@pytest.mark.parametrize(
    "change",
    [
        {"observed_method": "card"},
        {"observed_method_receipt_ref": "SYN-RECEIPT"},
        {"observed_method": "card", "observed_method_receipt_ref": "SYN-RECEIPT"},
        {"tasy_ack_state": "acknowledged", "tasy_ack_receipt_ref": "SYN-ACK"},
    ],
)
def test_observed_method_cannot_be_inferred_without_paid_evidence(schema_bytes, change):
    request, value = hosted_case("payment")
    value["data"]["payment"].update(change)
    codec = CommercialWire(
        schema_bytes, hosted_origins={"stripe": frozenset({"https://checkout.stripe.com"})}
    )
    with pytest.raises(CommercialRefusalError):
        codec.parse_result(encode(value), codec.parse_command(encode(request)))


@pytest.mark.parametrize("change", ["scope", "default", "duplicate", "expired"])
def test_capability_projection_requires_exact_resource_and_current_choice(schema_bytes, change):
    value = wire_result()
    caps = qualified_caps("operator", "SYN-OPERATOR")
    if change == "scope":
        caps["scope"]["ref"] = "FOREIGN"
    elif change == "default":
        caps["choices"][0].update(provider="asaas", method="pix")
    elif change == "duplicate":
        caps["choices"].append(deepcopy(caps["choices"][0]))
    else:
        caps["valid_until"] = "2000-01-01T00:00:00Z"
    value["data"]["payment_capabilities"] = caps
    codec = CommercialWire(schema_bytes)
    with pytest.raises(CommercialRefusalError):
        codec.parse_result(encode(value), codec.parse_command(encode(wire_command())))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutation", ["missing", "account", "scope", "command", "selection", "revision", "expired"]
)
async def test_payment_preparation_requires_current_exact_choice_proof(mutation):
    gateway, _, source = composed()
    prepare = source.prepare

    async def altered(*args):
        prepared = await prepare(*args)
        choice = prepared.payment_choice
        if mutation == "missing":
            return replace(prepared, payment_choice=None)
        changes = {
            "account": {"account_binding_ref": ""},
            "scope": {"scope_digest": "foreign"},
            "command": {"command_digest": "foreign"},
            "revision": {"source_revision": 0},
            "expired": {"valid_until": NOW - timedelta(seconds=1)},
            "selection": {"selection": replace(choice.selection, choice_ref="foreign")},
        }
        return replace(prepared, payment_choice=replace(choice, **changes[mutation]))

    source.prepare = altered
    result = await gateway.execute(UnitCodec().parse_command(b"payment.create"), scope())
    assert result.status != "succeeded"
    assert not source.executed_commands


class DeliveryOrigins(CommercialDeliveryOrigins):
    def __init__(self):
        self.calls = []
        self.after = lambda: None

    def current_origins(self, **selection):
        self.calls.append(selection)
        self.after()
        return frozenset({"https://issuer.invalid", "https://clinical.invalid"})


def journey():
    return {
        "journey_ref": "SYN-JOURNEY",
        "revision": 1,
        "prospect_ref": "SYN-PROSPECT",
        "account_ref": "SYN-ACCOUNT",
        "party_ref": None,
        "payer_ref": None,
        "quote_contexts": [],
        "acceptance": {
            "schemaVersion": "customer-acceptance.proposed.v1",
            "status": "refused",
            "outcome": "source_unavailable",
        },
        "contract": None,
        "enrollment": {
            "schemaVersion": "customer-enrollment.proposed.v1",
            "status": "refused",
            "outcome": "source_unavailable",
        },
        "coverage": {
            "schemaVersion": "customer-coverage.proposed.v1",
            "coverageState": "unknown",
            "unknownReason": "source_unavailable",
            "observedAt": "2026-10-01T00:00:00Z",
        },
        "documents": [
            {
                "document_ref": "SYN-DOC",
                "source_revision": 1,
                "document_kind_ref": "SYN-KIND",
                "label": "Synthetic administrative document",
                "state": "available",
                "required": None,
                "hosted_document": {
                    "url": "https://issuer.invalid/document",
                    "expires_at": "2099-01-01T00:00:00Z",
                },
            }
        ],
        "pending_items": [],
        "pending_operations": [],
        "next_actions": [],
        "support_topics": [],
        "support": {
            "schemaVersion": "sales-support-case.proposed.v2",
            "status": "refused",
            "outcome": "SOURCE_UNAVAILABLE",
        },
        "observed_at": "2026-10-01T00:00:00Z",
        "profile_ref": None,
        "party_linkage": None,
        "commercial_acceptance": None,
        "receivable_refs": [],
        "payment_attempt_refs": [],
        "mandate_refs": [],
        "financial_action_refs": [],
    }


def journey_request():
    return envelope("journey.get", {"journey_ref": None, "cursor": None, "limit": 20})


def journey_result():
    return {"kind": "journey_snapshot", "journeys": [journey()], "next_cursor": None, "plan_choices": []}


def recovery(name, data):
    return {
        "kind": "operation_status",
        "target_command_ref": "SYN-ORIGINAL",
        "original_command": name,
        "state": "completed",
        "original_result": outcome(data),
    }


def test_journey_document_requires_separate_issuer_not_payment_origins(schema_bytes):
    request, data = journey_request(), journey_result()
    with pytest.raises(CommercialRefusalError):
        parse(
            CommercialWire(schema_bytes, hosted_origins={"stripe": frozenset({"https://issuer.invalid"})}),
            request,
            data,
        )
    origins = DeliveryOrigins()
    parsed = parse(CommercialWire(schema_bytes, delivery_origins=origins), request, data)
    assert parsed.requires_current_result and parsed.authorization_command == "journey.get"
    assert origins.calls[-1]["resource_ref"] == "SYN-JOURNEY"


def test_nested_recovery_rejects_urls_then_preserves_only_sanitized_history(schema_bytes):
    codec = CommercialWire(schema_bytes, delivery_origins=DeliveryOrigins())
    payload = envelope("operation.get", {"target_command_ref": "SYN-ORIGINAL"})
    data = recovery("journey.get", journey_result())
    with pytest.raises(CommercialRefusalError):
        parse(codec, payload, data)
    safe = _strip_replay_actions(data)
    parsed = parse(codec, payload, safe)
    assert b"https://" not in parsed.wire_bytes
    assert parsed.authorization_command == "journey.get" and parsed.requires_current_result
    assert data["original_result"]["data"]["journeys"][0]["documents"][0]["hosted_document"] is not None


def test_recursive_redaction_includes_capabilities_and_external_channel(schema_bytes):
    original = {
        "data": {
            "journeys": [{"contract": {"payment_capabilities": qualified_caps("contract", "SYN-CONTRACT")}}],
            "external_handoff": {"url": "https://clinical.invalid/private", "expires_at": None},
        }
    }
    safe = _strip_replay_actions(original)
    assert safe["data"]["external_handoff"] is None
    assert safe["data"]["journeys"][0]["contract"]["payment_capabilities"] == unavailable_capabilities(
        "contract", "SYN-CONTRACT"
    )
    assert original["data"]["external_handoff"] is not None


def test_last_document_lookup_cannot_disclose_an_earlier_expired_document(schema_bytes, monkeypatch):
    now = datetime.now(UTC)
    current = [now]

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return current[0]

    monkeypatch.setattr(wire_module, "datetime", Clock)
    origins = DeliveryOrigins()
    data = journey_result()
    docs = data["journeys"][0]["documents"]
    docs[0]["hosted_document"]["expires_at"] = (now + timedelta(seconds=1)).isoformat()
    docs.append(
        {
            **deepcopy(docs[0]),
            "document_ref": "SYN-DOC-2",
            "hosted_document": {"url": "https://issuer.invalid/second", "expires_at": None},
        }
    )

    def advance():
        if len(origins.calls) == 2:
            current[0] += timedelta(seconds=2)

    origins.after = advance
    with pytest.raises(CommercialRefusalError):
        parse(CommercialWire(schema_bytes, delivery_origins=origins), journey_request(), data)
    assert len(origins.calls) == 2


class CurrentResultSource(UnitSourceAuthority):
    def __init__(self, value):
        super().__init__(value)
        self.result_calls = []
        self.deny_result = False
        self.wrong_digest = False

    async def result_authority(self, command, value, result):
        self.result_calls.append(command.command)
        if self.deny_result:
            raise CommercialRefusalError("denied", "representation_denied")
        return CommercialResultAuthority(
            command_digest=command_digest(command),
            scope_digest=scope_digest(value),
            result_digest="foreign" if self.wrong_digest else hashlib.sha256(result.wire_bytes).hexdigest(),
            resource_grant_ref=value.resource_grant_ref,
            currentness_ref="SYN-CURRENT-RESOURCE",
            valid_until=value.valid_until,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure", [None, "old_scope", "wrong_actor", "missing_linkage", "revoked_result", "wrong_digest"]
)
async def test_private_journey_uses_current_linkage_and_exact_postsource_authority(schema_bytes, failure):
    authority = UnitScope()
    authority.scope = replace(
        authority.scope,
        allowed_operations=frozenset({"journey.get"}),
        granted_scopes=frozenset({"interop/sales.journey.read"}),
        resource_grant_ref="SYN-RESOURCE-GRANT",
        journey_linkage_ref="SYN-LINKAGE",
    )
    if failure == "old_scope":
        authority.scope = replace(authority.scope, granted_scopes=frozenset())
    if failure == "wrong_actor":
        authority.scope = replace(authority.scope, actor_kind="vendor")
    if failure == "missing_linkage":
        authority.scope = replace(authority.scope, journey_linkage_ref=None)
    source = CurrentResultSource(authority.scope)
    source.deny_result, source.wrong_digest = failure == "revoked_result", failure == "wrong_digest"
    calls = []

    def transport(request):
        calls.append(request)
        return httpx.Response(200, json=outcome(journey_result()))

    gateway = compose_commercial_gateway(
        schema_bytes=schema_bytes,
        caller=UnitCaller(),
        scope_authority=authority,
        source_authority=source,
        amh_origin="https://amh.invalid",
        delivery_origins=DeliveryOrigins(),
        sales_admission=sales_session_admission(),
        transport=httpx.MockTransport(transport),
    )
    app = FastAPI()
    app.state.commercial_gateway = gateway
    body = encode(journey_request())

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/sales/commercial",
            "query_string": b"",
            "app": app,
            "headers": [
                (b"authorization", b"Bearer SYN-SALES-CREDENTIAL"),
                (b"x-commercial-connection", b"SYN-SALES-CONNECTION"),
                (b"content-type", b"application/json"),
            ],
        },
        receive,
    )
    response = await commercial_command(request)
    if failure is None:
        assert response.status_code == 200
        assert source.result_calls == ["journey.get"]
    else:
        assert response.status_code != 200 and json.loads(response.body)["data"] is None
        assert len(calls) == int(failure in {"revoked_result", "wrong_digest"})


@pytest.mark.asyncio
@pytest.mark.parametrize("actor,purpose", [("customer", "sales_operations"), ("staff", "sales_billing")])
async def test_work_scope_cannot_be_borrowed_from_customer_or_billing(actor, purpose):
    gateway, _, _ = composed()
    value = replace(
        scope(),
        actor_kind=actor,
        purpose_ref=purpose,
        allowed_operations=frozenset({"work.list"}),
        granted_scopes=frozenset({"interop/sales.work.read"}),
        resource_grant_ref="SYN-QUEUE-GRANT",
    )
    with pytest.raises(CommercialRefusalError):
        gateway._scope(value)


def public_body():
    unknown = {"dataState": "unknown"}
    return {
        "schemaVersion": "sales-catalog-read.proposed.v3",
        "status": "usable",
        "source": {
            "authorityRef": "SYN-SOURCE",
            "catalogueRef": "SYN-CATALOG",
            "catalogueVersion": "1",
            "publicationState": "published",
        },
        "currentness": {"businessRevision": "7", "verifiedAt": "2026-10-01T00:00:00Z"},
        "expiry": {"dataState": "known", "validUntil": "2099-01-01T00:00:00Z"},
        "withdrawal": {"state": "none_published"},
        "networkChannelClassification": "portal_corporativo",
        "publishedCriteria": {
            "comparableCriteria": ["price_and_conditions"],
            "filterableFields": ["modality"],
            "sortableFields": ["price_monthly_per_life"],
        },
        "products": [
            {
                "optionRef": "SYN-OPTION",
                "productRef": "SYN-PRODUCT",
                "productVersion": "1",
                "productName": "Synthetic product",
                "operatorName": "Synthetic operator",
                "ansRegistry": deepcopy(unknown),
                "modality": "individual_familiar",
                "offerState": "usable",
                **{
                    key: deepcopy(unknown)
                    for key in (
                        "segmentation",
                        "geographicScope",
                        "accommodation",
                        "coparticipation",
                        "waitingPeriod",
                        "cpt",
                        "conditions",
                        "network",
                    )
                },
                "documents": [],
                "networkAssociations": [],
                "price": {
                    "dataState": "known",
                    "amounts": [
                        {
                            "priceRef": "SYN-PRICE",
                            "money": {"amountMinor": 12345, "currency": "BRL"},
                            "amountKind": "reference",
                            "unit": "per_life",
                            "billingPeriod": {
                                "periodRef": "SYN-PERIOD",
                                "kind": "monthly",
                                "label": "Synthetic monthly",
                            },
                            "applicability": {
                                "contextRef": "SYN-CONTEXT",
                                "version": "1",
                                "label": "Source-published synthetic scope",
                                "comparisonBasisRef": None,
                            },
                            "currentness": {
                                "sourceRevision": "7",
                                "verifiedAt": "2026-10-01T00:00:00Z",
                                "validUntil": None,
                            },
                        }
                    ],
                },
            }
        ],
    }


class PublicScope(PublicCatalogScopeAuthority):
    def __init__(self):
        self.value = PublicCatalogScope(
            workload_ref="SYN-MACHINE",
            tenant_ref="SYN-TENANT",
            legal_entity_ref="SYN-ENTITY",
            operator_ref="SYN-OPERATOR",
            source_grant_ref="SYN-GRANT",
            source_binding_revision="1",
            publication_ref="SYN-PUBLICATION",
            lifecycle_ref="SYN-PUBLIC-LIFECYCLE",
            currentness_ref="SYN-PUBLIC-CURRENT",
            purpose="sales_public_catalog",
            granted_scope="interop/sales.catalog.public.read",
            valid_until=datetime.now(UTC) + timedelta(minutes=5),
        )
        self.revoked = False

    async def acquire(self, connection_ref, authenticated_caller_ref):
        assert (connection_ref, authenticated_caller_ref) == ("SYN-SALES-CONNECTION", "SYN-SALES-CALLER")
        return self.value

    async def current(self, value, *, phase):
        if self.revoked:
            raise CommercialRefusalError("unavailable", "source_unavailable")
        return value


class PublicSource(AmhPublicCatalogAuthority):
    def __init__(self, codec):
        self.codec, self.phases, self.digests = codec, [], []
        self.after = lambda: None
        self.mismatch = False

    async def authorize(self, value, result_digest):
        self.digests.append(result_digest)
        if result_digest:
            self.after()
        return PublicCatalogProof(
            scope_digest=public_scope_digest(value),
            schema_digest=self.codec.schema_digest,
            publication_ref=value.publication_ref,
            currentness_ref="SYN-PUBLIC-QUALIFIED",
            result_digest="foreign" if self.mismatch else result_digest,
            valid_until=value.valid_until,
        )

    async def connection(self, value):
        return PublicAmhConnection(
            connection_ref="SYN-AMH-PUBLIC",
            scope_digest=public_scope_digest(value),
            valid_until=value.valid_until,
        )

    async def credential(self, connection):
        return "SYN-AMH-CREDENTIAL"

    async def current(self, value, connection, *, phase):
        self.phases.append(phase)


def catalog_admission():
    """Valid A1 record for the bound-path rigs; the shipped repo has no record file."""
    from maezo.gateway.commercial_catalog_admission import A1_CONTRACT_SHA256, parse_catalog_source_admission

    record = {
        "schema": "omni-commercial-admission-record.v1",
        "admission_id": "A1",
        "decision": "admitted",
        "owners": ["produto", "comercial", "legal"],
        "decided_at": "2026-10-24T00:00:00+00:00",
        "valid_until": "2099-01-01T00:00:00+00:00",
        "unlocks": "P6.1",
        "admitted_commands": ["catalog.get"],
        "contract_pin": {
            "contract_id": "omni-commercial.proposed.v1",
            "revision": "C6-4",
            "sha256": A1_CONTRACT_SHA256,
        },
        "evidence": {
            "object_url": "https://amh.invalid/admissions/a1-object.json",
            "object_sha256": "a" * 64,
            "origin_git_sha": "b" * 40,
        },
    }
    return parse_catalog_source_admission(json.dumps(record).encode(), now=datetime.now(UTC))


def public_rig(schema_bytes, body=None, status=200):
    scope_authority = PublicScope()
    source = PublicSource(CommercialWire(schema_bytes))
    calls = []

    def transport(request):
        calls.append(request)
        return httpx.Response(status, json=body if body is not None else public_body())

    gateway = compose_public_catalog_gateway(
        schema_bytes=schema_bytes,
        amh_origin="https://amh.invalid",
        caller=UnitCaller(),
        scope_authority=scope_authority,
        source_authority=source,
        source_admission=catalog_admission(),
        transport=httpx.MockTransport(transport),
    )
    app = FastAPI()
    app.state.public_catalog_gateway = gateway
    app.include_router(commercial_router)
    return app, gateway, scope_authority, source, calls


@pytest.mark.asyncio
async def test_actual_public_factory_route_calls_fixed_machine_get_and_preserves_numeric_values(schema_bytes):
    app, _, _, source, calls = public_rig(schema_bytes)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://maezo.invalid"
    ) as client:
        result = await client.get(
            PUBLIC_CATALOG_PATH,
            headers={
                "Authorization": "Bearer SYN-SALES-CREDENTIAL",
                "X-Commercial-Connection": "SYN-SALES-CONNECTION",
                "Cookie": "foreign=ignored",
            },
        )
    assert result.status_code == 200 and result.json() == public_body()
    assert result.headers["cache-control"] == "no-store"
    assert len(calls) == 1 and str(calls[0].url) == "https://amh.invalid/interop/sales/v1/public/catalog"
    assert calls[0].method == "GET" and calls[0].content == b""
    assert "cookie" not in calls[0].headers and "x-tenant" not in calls[0].headers
    assert source.phases == ["before_io", "before_disclosure"]
    assert source.digests[-1] == hashlib.sha256(httpx.Response(200, json=public_body()).content).hexdigest()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure", ["unbound", "bad_machine", "query", "body", "override", "duplicate", "revoked"]
)
async def test_public_invalid_transport_or_authority_never_calls_source(schema_bytes, failure):
    app, gateway, scope_authority, _, calls = public_rig(schema_bytes)
    headers = [
        ("Authorization", "Bearer SYN-SALES-CREDENTIAL"),
        ("X-Commercial-Connection", "SYN-SALES-CONNECTION"),
    ]
    if failure == "unbound":
        app.state.public_catalog_gateway = compose_public_catalog_gateway(schema_bytes=schema_bytes)
    elif failure == "bad_machine":
        headers[0] = ("Authorization", "Bearer invalid")
    elif failure == "override":
        headers.append(("X-Tenant", "foreign"))
    elif failure == "duplicate":
        headers.append(headers[0])
    elif failure == "revoked":
        scope_authority.revoked = True
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://maezo.invalid"
    ) as client:
        result = await client.request(
            "GET",
            PUBLIC_CATALOG_PATH + ("?tenant=foreign" if failure == "query" else ""),
            headers=headers,
            content=b"private" if failure == "body" else b"",
        )
    assert result.status_code == 503 and result.json() == {
        "schemaVersion": "sales-catalog.proposed.v1",
        "status": "unavailable",
        "reason": "source_unavailable",
    }
    assert not calls


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [
        "withdrawn",
        "expired",
        "numeric_expired",
        "future_verified",
        "unknown_with_value",
        "legacy",
        "source503",
        "proof",
    ],
)
async def test_public_source_failure_is_categorical_and_not_stale_success(schema_bytes, failure):
    body = public_body()
    if failure == "withdrawn":
        body["source"]["publicationState"] = "withdrawn"
    elif failure == "expired":
        body["expiry"]["validUntil"] = "2000-01-01T00:00:00Z"
    elif failure == "numeric_expired":
        body["products"][0]["price"]["amounts"][0]["currentness"]["validUntil"] = "2000-01-01T00:00:00Z"
    elif failure == "future_verified":
        body["currentness"]["verifiedAt"] = "2099-01-01T00:00:00Z"
    elif failure == "unknown_with_value":
        body["products"][0]["segmentation"]["value"] = "unqualified"
    elif failure == "legacy":
        body["schemaVersion"] = "sales-catalog-read.proposed.v2"
    app, _, _, source, _ = public_rig(schema_bytes, body, status=503 if failure == "source503" else 200)
    source.mismatch = failure == "proof"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://maezo.invalid"
    ) as client:
        result = await client.get(
            PUBLIC_CATALOG_PATH,
            headers={
                "Authorization": "Bearer SYN-SALES-CREDENTIAL",
                "X-Commercial-Connection": "SYN-SALES-CONNECTION",
            },
        )
    assert result.status_code == 503 and result.json()["reason"] == "source_unavailable"
    assert "SYN-SOURCE" not in result.text


@pytest.mark.asyncio
async def test_public_price_expiry_rechecked_after_last_source_authority_io(schema_bytes, monkeypatch):
    now = datetime.now(UTC)
    current = [now]

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return current[0]

    monkeypatch.setattr(wire_module, "datetime", Clock)
    body = public_body()
    body["products"][0]["price"]["amounts"][0]["currentness"]["validUntil"] = (
        now + timedelta(seconds=1)
    ).isoformat()
    app, _, _, source, _ = public_rig(schema_bytes, body)
    source.after = lambda: current.__setitem__(0, now + timedelta(seconds=2))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://maezo.invalid"
    ) as client:
        result = await client.get(
            PUBLIC_CATALOG_PATH,
            headers={
                "Authorization": "Bearer SYN-SALES-CREDENTIAL",
                "X-Commercial-Connection": "SYN-SALES-CONNECTION",
            },
        )
    assert result.status_code == 503
    assert source.digests[-1] is not None


def test_c6_2_connection_slots_nullable_but_keys_required(schema_bytes):
    codec = CommercialWire(schema_bytes)
    payload = {
        "operator_ref": "SYN-OPERATOR",
        "display_name": "Synthetic",
        "legal_entity_ref": "SYN-ENTITY",
        "tasy_connection_ref": "SYN-TASY",
        "payment_connections": {
            "stripe_card": "SYN-STRIPE",
            "infinitepay_pix_card": None,
            "asaas_boleto": "SYN-ASAAS",
        },
    }
    parsed = codec.parse_command(encode(envelope("operator.draft", payload)))
    assert json.loads(parsed.wire_bytes)["payload"]["payment_connections"]["infinitepay_pix_card"] is None
    del payload["payment_connections"]["infinitepay_pix_card"]
    with pytest.raises(CommercialRefusalError):
        codec.parse_command(encode(envelope("operator.draft", payload)))


@pytest.mark.asyncio
async def test_public_unbound_before_even_one_body_read(schema_bytes):
    app = FastAPI()
    app.state.public_catalog_gateway = compose_public_catalog_gateway(schema_bytes=schema_bytes)
    reads = []

    async def receive():
        reads.append(True)
        raise AssertionError("unadmitted request body was read")

    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": PUBLIC_CATALOG_PATH,
            "query_string": b"",
            "headers": [],
            "app": app,
        },
        receive,
    )
    response = await public_catalog(request)
    assert response.status_code == 503 and not reads


@pytest.mark.asyncio
async def test_real_app_factory_mounts_public_gateway_and_host_refuses_before_source(schema_bytes):
    from maezo.portal.api.app import create_app
    from maezo.portal.api.config import PortalSettings
    from maezo.portal.api.store import LocalTestIdentityStore

    _, gateway, _, _, calls = public_rig(schema_bytes)
    settings = PortalSettings(
        tenant="synthetic",
        issuer="https://cognito-idp.sa-east-1.amazonaws.com/sa-east-1_SYN",
        cognito_origin="https://auth.invalid",
        client_id="synthetichuman",
        machine_client_id="syntheticmachine",
        client_purpose="dedicated-human-code-pkce",
        public_origin="https://maezo.invalid",
        mode="local-test",
    )

    def no_identity_io(request):
        raise AssertionError("public catalog invoked customer identity IO")

    async with httpx.AsyncClient(transport=httpx.MockTransport(no_identity_io)) as oidc:
        app = create_app(
            settings=settings,
            store=LocalTestIdentityStore("synthetic"),
            oidc_client=oidc,
            public_catalog_gateway=gateway,
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://maezo.invalid"
        ) as client:
            headers = {
                "Authorization": "Bearer SYN-SALES-CREDENTIAL",
                "X-Commercial-Connection": "SYN-SALES-CONNECTION",
            }
            good = await client.get(PUBLIC_CATALOG_PATH, headers=headers)
            assert good.status_code == 200 and good.json() == public_body()
            refused = await client.get(PUBLIC_CATALOG_PATH, headers={**headers, "Host": "foreign.invalid"})
            assert refused.status_code == 503 and refused.json()["reason"] == "source_unavailable"
            queried = await client.get(PUBLIC_CATALOG_PATH + "?profile=private", headers=headers)
            assert queried.status_code == 503
    assert len(calls) == 1


def test_current_journey_can_show_expired_quote_history_without_renewing_it(schema_bytes):
    data = journey_result()
    data["journeys"][0]["quote_contexts"] = [
        {
            "quote_ref": "SYN-EXPIRED-QUOTE",
            "quote": None,
            "source_revision": 1,
            "terms_digest": "a" * 64,
            "valid_until": "2000-01-01T00:00:00Z",
        }
    ]
    parsed = parse(CommercialWire(schema_bytes, delivery_origins=DeliveryOrigins()), journey_request(), data)
    assert (
        json.loads(parsed.wire_bytes)["data"]["journeys"][0]["quote_contexts"][0]["valid_until"]
        == "2000-01-01T00:00:00Z"
    )
    assert parsed.requires_current_result


def test_support_handoff_exact_context_and_separate_channel_recovery(schema_bytes):
    request = envelope("support.handoff", {"journey_ref": "SYN-JOURNEY", "topic_ref": "SYN-PUBLISHED-TOPIC"})
    data = {
        "kind": "support_handoff",
        "journey_ref": "SYN-JOURNEY",
        "handoff_ref": "SYN-HANDOFF",
        "source_revision": 1,
        "state": "received",
        "source_receipt_ref": "SYN-SOURCE-RECEIVED",
        "case_read": journey()["support"],
        "external_handoff": {"url": "https://clinical.invalid/channel", "expires_at": None},
    }
    codec = CommercialWire(schema_bytes, delivery_origins=DeliveryOrigins())
    assert parse(codec, request, data).requires_current_result
    recovery_request = envelope("operation.get", {"target_command_ref": "SYN-ORIGINAL"})
    with pytest.raises(CommercialRefusalError):
        parse(codec, recovery_request, recovery("support.handoff", data))
    recovered = parse(codec, recovery_request, recovery("support.handoff", _strip_replay_actions(data)))
    assert b"https://" not in recovered.wire_bytes
    assert json.loads(recovered.wire_bytes)["data"]["original_result"]["data"]["state"] == "received"
    data["journey_ref"] = "FOREIGN"
    with pytest.raises(CommercialRefusalError):
        parse(codec, request, data)


@pytest.mark.parametrize(
    "name,payload",
    [
        ("work.list", {"states": ["open"], "cursor": None, "limit": 10}),
        ("work.claim", {"work_ref": "SYN-WORK"}),
        ("work.assign", {"work_ref": "SYN-WORK", "assignee_ref": "SYN-STAFF"}),
        ("work.handoff", {"work_ref": "SYN-WORK", "action_ref": "SYN-SOURCE-ACTION"}),
        ("work.reconcile", {"work_ref": "SYN-WORK"}),
        ("journey.save", {"quote_ref": "SYN-QUOTE", "source_revision": 1, "terms_digest": "b" * 64}),
    ],
)
def test_new_commands_accept_only_source_selectors_never_completion_proofs(schema_bytes, name, payload):
    codec = CommercialWire(schema_bytes)
    assert codec.parse_command(encode(envelope(name, payload))).command == name
    for forbidden in ("source_receipt_ref", "completed", "amount_minor", "actor_ref"):
        with pytest.raises(CommercialRefusalError):
            codec.parse_command(encode(envelope(name, {**payload, forbidden: "caller-proof"})))


@pytest.mark.asyncio
async def test_actual_work_list_http_composition_requires_staff_result_authority(schema_bytes):
    authority = UnitScope()
    authority.scope = replace(
        authority.scope,
        actor_kind="staff",
        purpose_ref="sales_operations",
        allowed_operations=frozenset({"work.list"}),
        granted_scopes=frozenset({"interop/sales.work.read"}),
        resource_grant_ref="SYN-QUEUE-GRANT",
    )
    source = CurrentResultSource(authority.scope)
    data = {"kind": "work_queue", "items": [], "assignees": [], "next_cursor": None}
    calls = []

    def transport(request):
        calls.append(request)
        return httpx.Response(200, json=outcome(data))

    gateway = compose_commercial_gateway(
        schema_bytes=schema_bytes,
        caller=UnitCaller(),
        scope_authority=authority,
        source_authority=source,
        amh_origin="https://amh.invalid",
        sales_admission=sales_session_admission(),
        # A5 (BRDG-3.3): the support/work dimension is its own admission — this rig
        # exercises the staff result-authority lane, so it carries the registered A5
        # record; without it the composition refuses `work.list` before any source step.
        support_admission=support_handoff_admission(),
        transport=httpx.MockTransport(transport),
    )
    trusted = await gateway.admission.transport("Bearer SYN-SALES-CREDENTIAL", "SYN-SALES-CONNECTION")
    admitted = await gateway.admit(trusted)
    request = gateway.codec.parse_command(
        encode(envelope("work.list", {"states": ["open"], "cursor": None, "limit": 10}))
    )
    output = await gateway.execute(request, admitted)
    assert output.status == "succeeded" and json.loads(output.wire_bytes)["data"] == data
    assert source.result_calls == ["work.list"] and len(calls) == 1
    source.deny_result = True
    denied = await gateway.execute(request, admitted)
    assert denied.status == "uncertain" and json.loads(denied.wire_bytes)["data"] is None
    assert len(calls) == 2


@pytest.mark.parametrize("field,value", [("work_ref", "FOREIGN"), ("resolution_receipt_ref", None)])
def test_work_result_cannot_rebind_selector_or_claim_resolution_without_receipt(schema_bytes, field, value):
    item = {
        "work_ref": "SYN-WORK",
        "revision": 2,
        "responsible_queue_ref": "SYN-QUEUE",
        "original_operation_ref": "SYN-OP",
        "original_command": "payment.reconcile",
        "target_kind": "payment",
        "target_ref": "SYN-ATTEMPT",
        "state": "resolved",
        "reason_code": "accounting_pending",
        "owner_ref": "SYN-STAFF",
        "handoff_ref": None,
        "handoff_receipt_ref": None,
        "resolution_receipt_ref": "SYN-SOURCE-RESOLVED",
        "next_actions": [],
    }
    item[field] = value
    with pytest.raises(CommercialRefusalError):
        parse(
            CommercialWire(schema_bytes),
            envelope("work.reconcile", {"work_ref": "SYN-WORK"}),
            {"kind": "work_item", "item": item},
        )


@pytest.mark.asyncio
async def test_admitted_public_get_never_consumes_subject_body_stream(schema_bytes):
    app, _, _, _, calls = public_rig(schema_bytes)
    reads = []

    async def receive():
        reads.append(True)
        raise AssertionError("public-data grant cannot admit subject body bytes")

    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": PUBLIC_CATALOG_PATH,
            "query_string": b"",
            "app": app,
            "headers": [
                (b"authorization", b"Bearer SYN-SALES-CREDENTIAL"),
                (b"x-commercial-connection", b"SYN-SALES-CONNECTION"),
                (b"content-length", b"0"),
            ],
        },
        receive,
    )
    response = await public_catalog(request)
    assert response.status_code == 200 and not reads and len(calls) == 1


def continuation_journey():
    value = journey()
    value.update(
        profile_ref="SYN-PROFILE",
        party_ref="SYN-PARTY",
        payer_ref="SYN-PAYER",
        receivable_refs=["SYN-RECEIVABLE"],
        payment_attempt_refs=["SYN-ATTEMPT"],
        mandate_refs=["SYN-MANDATE"],
        financial_action_refs=["SYN-ACTION"],
    )
    value["party_linkage"] = {
        "linkage_ref": "SYN-LINKAGE",
        "revision": 2,
        "prospect_ref": value["prospect_ref"],
        "account_ref": value["account_ref"],
        "party_ref": value["party_ref"],
        "payer_ref": value["payer_ref"],
        "policyholder_ref": "SYN-POLICYHOLDER",
        "beneficiary_refs": ["SYN-BENEFICIARY"],
    }
    quote = {
        "kind": "quote",
        "quote_ref": "SYN-QUOTE",
        "source_revision": 7,
        "catalog_ref": "SYN-CATALOG",
        "catalog_version": 1,
        "product_ref": "SYN-PRODUCT",
        "network_version": 1,
        "terms_digest": "b" * 64,
        "money": {"amount_minor": 12345, "currency": "BRL"},
        "valid_until": "2099-01-01T00:00:00Z",
        "contractable": True,
    }
    value["quote_contexts"] = [
        {key: quote[key] for key in ("quote_ref", "source_revision", "terms_digest", "valid_until")}
    ]
    value["quote_contexts"][0]["quote"] = quote
    value["commercial_acceptance"] = {
        "kind": "acceptance",
        "acceptance_receipt_ref": "SYN-COMMERCIAL-RECEIPT",
        "quote_ref": quote["quote_ref"],
        "source_revision": quote["source_revision"],
        "terms_digest": quote["terms_digest"],
        "party_linkage_ref": value["party_linkage"]["linkage_ref"],
        "decision": "accepted",
        "recorded_at": "2026-10-01T00:00:00Z",
        "terms_review_ref": "SYN-REVIEW",
    }
    value["acceptance"] = {
        "schemaVersion": "customer-acceptance.proposed.v1",
        "status": "recorded",
        "decision": "accepted",
        "acceptanceRef": "SYN-OP03-RECEIPT",
        "recordedAt": "2026-10-01T00:00:00Z",
    }
    return value


def parse_continuation(codec, value, recovered):
    data = {"kind": "journey_snapshot", "journeys": [value], "next_cursor": None, "plan_choices": []}
    if recovered:
        return parse(
            codec,
            envelope("operation.get", {"target_command_ref": "SYN-ORIGINAL"}),
            recovery("journey.get", _strip_replay_actions(data)),
        )
    return parse(codec, journey_request(), data)


@pytest.mark.parametrize("recovered", [False, True])
def test_c6_3_continuation_preserves_exact_source_facts_without_op03_alias(schema_bytes, recovered):
    codec = CommercialWire(schema_bytes, delivery_origins=DeliveryOrigins())
    value = continuation_journey()
    parsed = parse_continuation(codec, value, recovered)
    output = json.loads(parsed.wire_bytes)["data"]
    if recovered:
        output = output["original_result"]["data"]
        assert b"https://" not in parsed.wire_bytes
    actual = output["journeys"][0]
    assert actual["commercial_acceptance"]["acceptance_receipt_ref"] == "SYN-COMMERCIAL-RECEIPT"
    assert actual["acceptance"]["acceptanceRef"] == "SYN-OP03-RECEIPT"
    assert actual["quote_contexts"][0]["quote"]["money"]["amount_minor"] == 12345
    assert actual["profile_ref"] == "SYN-PROFILE" and actual["payment_attempt_refs"] == ["SYN-ATTEMPT"]
    assert parsed.requires_current_result


@pytest.mark.parametrize("recovered", [False, True])
@pytest.mark.parametrize("field", ["quote_ref", "source_revision", "terms_digest", "valid_until"])
def test_c6_3_full_quote_must_match_all_four_context_fields(schema_bytes, recovered, field):
    value = continuation_journey()
    value["quote_contexts"][0]["quote"][field] = {
        "quote_ref": "FOREIGN",
        "source_revision": 8,
        "terms_digest": "c" * 64,
        "valid_until": "2098-01-01T00:00:00Z",
    }[field]
    with pytest.raises(CommercialRefusalError):
        parse_continuation(CommercialWire(schema_bytes, delivery_origins=DeliveryOrigins()), value, recovered)


@pytest.mark.parametrize("recovered", [False, True])
@pytest.mark.parametrize("field", ["prospect_ref", "account_ref", "party_ref", "payer_ref"])
def test_c6_3_party_linkage_cannot_rebind_snapshot_identity(schema_bytes, recovered, field):
    value = continuation_journey()
    value["party_linkage"][field] = "FOREIGN"
    with pytest.raises(CommercialRefusalError):
        parse_continuation(CommercialWire(schema_bytes, delivery_origins=DeliveryOrigins()), value, recovered)


@pytest.mark.parametrize("recovered", [False, True])
@pytest.mark.parametrize(
    "field",
    [
        "quote_ref",
        "source_revision",
        "terms_digest",
        "party_linkage_ref",
        "missing_linkage",
        "missing_context",
    ],
)
def test_c6_3_commercial_acceptance_requires_original_quote_and_linkage(schema_bytes, recovered, field):
    value = continuation_journey()
    if field == "missing_linkage":
        value["party_linkage"] = None
    elif field == "missing_context":
        value["quote_contexts"] = []
    else:
        value["commercial_acceptance"][field] = (
            8 if field == "source_revision" else "c" * 64 if field == "terms_digest" else "FOREIGN"
        )
    with pytest.raises(CommercialRefusalError):
        parse_continuation(CommercialWire(schema_bytes, delivery_origins=DeliveryOrigins()), value, recovered)


def test_c6_3_op03_acceptance_cannot_substitute_commercial_receipt(schema_bytes):
    value = continuation_journey()
    value["commercial_acceptance"] = deepcopy(value["acceptance"])
    with pytest.raises(CommercialRefusalError):
        parse_continuation(CommercialWire(schema_bytes, delivery_origins=DeliveryOrigins()), value, False)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field",
    [None, "profile_ref", "receivable_refs", "payment_attempt_refs", "mandate_refs", "financial_action_refs"],
)
async def test_c6_3_new_continuation_facts_require_current_exact_result_authority(schema_bytes, field):
    authority = UnitScope()
    authority.scope = replace(
        authority.scope,
        allowed_operations=frozenset({"journey.get"}),
        granted_scopes=frozenset({"interop/sales.journey.read"}),
        resource_grant_ref="SYN-RESOURCE-GRANT",
        journey_linkage_ref="SYN-LINKAGE",
    )
    approved = {
        "kind": "journey_snapshot",
        "journeys": [continuation_journey()],
        "next_cursor": None,
        "plan_choices": [],
    }
    sent = deepcopy(approved)
    if field:
        sent["journeys"][0][field] = "FOREIGN-PROFILE" if field == "profile_ref" else ["FOREIGN-RESOURCE"]
    source = CurrentResultSource(authority.scope)

    async def bound_authority(command, value, result):
        proof = await CurrentResultSource.result_authority(source, command, value, result)
        # This independent synthetic source approves its original journey facts,
        # not whatever financial selectors arrived in the response under test.
        return replace(proof, result_digest=hashlib.sha256(encode(outcome(approved))).hexdigest())

    source.result_authority = bound_authority
    calls = []

    def transport(request):
        calls.append(request)
        return httpx.Response(200, json=outcome(sent))

    gateway = compose_commercial_gateway(
        schema_bytes=schema_bytes,
        caller=UnitCaller(),
        scope_authority=authority,
        source_authority=source,
        amh_origin="https://amh.invalid",
        delivery_origins=DeliveryOrigins(),
        sales_admission=sales_session_admission(),
        transport=httpx.MockTransport(transport),
    )
    trusted = await gateway.admission.transport("Bearer SYN-SALES-CREDENTIAL", "SYN-SALES-CONNECTION")
    admitted = await gateway.admit(trusted)
    command = gateway.codec.parse_command(encode(journey_request()))
    result = await gateway.execute(command, admitted)
    assert result.status == ("succeeded" if field is None else "uncertain")
    if field:
        assert json.loads(result.wire_bytes)["data"] is None
    assert len(calls) == 1 and source.result_calls == ["journey.get"]


def continuation_contract(value):
    acceptance = value["commercial_acceptance"]
    return {
        "kind": "contract",
        "contract_ref": "SYN-CONTRACT",
        "revision": 2,
        "state": "issued",
        "issued_receipt": {
            "contract_ref": "SYN-CONTRACT",
            "issuer_receipt_ref": "SYN-ISSUED",
            "accepted_receipt_ref": acceptance["acceptance_receipt_ref"],
            "quote_ref": acceptance["quote_ref"],
            "terms_digest": acceptance["terms_digest"],
            "payer_ref": value["payer_ref"],
            "party_linkage_ref": value["party_linkage"]["linkage_ref"],
            "issued_at": "2026-10-01T00:00:00Z",
            "source_revision": 1,
        },
        "billing_obligations": [],
        "payment_capabilities": unavailable_capabilities("contract", "SYN-CONTRACT"),
    }


@pytest.mark.parametrize(
    "field", ["payer_ref", "party_linkage_ref", "accepted_receipt_ref", "quote_ref", "terms_digest"]
)
def test_c6_3_existing_contract_cannot_rebind_new_continuation_context(schema_bytes, field):
    value = continuation_journey()
    value["contract"] = continuation_contract(value)
    codec = CommercialWire(schema_bytes, delivery_origins=DeliveryOrigins())
    assert parse_continuation(codec, value, False).requires_current_result
    value["contract"]["issued_receipt"][field] = "c" * 64 if field == "terms_digest" else "FOREIGN"
    with pytest.raises(CommercialRefusalError):
        parse_continuation(codec, value, False)


@pytest.mark.parametrize("recovered", [False, True])
def test_c6_3_expired_full_quote_is_history_only_not_fresh_continuation(schema_bytes, recovered):
    value = continuation_journey()
    context = value["quote_contexts"][0]
    context["valid_until"] = context["quote"]["valid_until"] = "2000-01-01T00:00:00Z"
    codec = CommercialWire(schema_bytes, delivery_origins=DeliveryOrigins())
    if recovered:
        parsed = parse_continuation(codec, value, True)
        assert parsed.authorization_command == "journey.get"
        assert b"https://" not in parsed.wire_bytes
    else:
        with pytest.raises(CommercialRefusalError):
            parse_continuation(codec, value, False)


def test_c6_3_duplicate_financial_refs_are_not_silently_deduplicated(schema_bytes):
    value = continuation_journey()
    value["receivable_refs"].append(value["receivable_refs"][0])
    with pytest.raises(CommercialRefusalError):
        parse_continuation(CommercialWire(schema_bytes, delivery_origins=DeliveryOrigins()), value, False)


def formal_plan_choice():
    return {
        "option_ref": "SYN-PRIVATE-CHOICE",
        "catalog_ref": "SYN-CATALOG",
        "catalog_version": 7,
        "product_ref": "SYN-PRODUCT",
        "label": "Synthetic qualified choice",
    }


def formal_entry_data():
    return {
        "kind": "journey_snapshot",
        "journeys": [],
        "next_cursor": None,
        "plan_choices": [formal_plan_choice()],
    }


@pytest.mark.parametrize("field", ["catalog_ref", "catalog_version", "product_ref", "label"])
def test_c6_4_duplicate_private_option_ref_rejected_even_with_conflicting_choice(schema_bytes, field):
    data = formal_entry_data()
    other = deepcopy(data["plan_choices"][0])
    other[field] = 8 if field == "catalog_version" else "CONFLICTING"
    data["plan_choices"].append(other)
    with pytest.raises(CommercialRefusalError):
        parse(CommercialWire(schema_bytes), journey_request(), data)


def test_c6_4_recovery_never_bootstraps_formal_entry_choices(schema_bytes):
    codec = CommercialWire(schema_bytes)
    fresh = formal_entry_data()
    request = envelope("operation.get", {"target_command_ref": "SYN-ORIGINAL"})
    with pytest.raises(CommercialRefusalError):
        parse(codec, request, recovery("journey.get", fresh))
    safe = _strip_replay_actions(fresh)
    assert safe["plan_choices"] == []
    assert fresh["plan_choices"] == [formal_plan_choice()]
    recovered = parse(codec, request, recovery("journey.get", safe))
    assert json.loads(recovered.wire_bytes)["data"]["original_result"]["data"]["plan_choices"] == []
    assert recovered.requires_current_result


@pytest.mark.parametrize("bad", ["public_alias", "extra_proof", "overflow", "missing"])
def test_c6_4_choices_are_bounded_private_closed_source_selectors(schema_bytes, bad):
    data = formal_entry_data()
    if bad == "public_alias":
        data["plan_choices"][0]["optionRef"] = data["plan_choices"][0].pop("option_ref")
    elif bad == "extra_proof":
        data["plan_choices"][0]["authority_ref"] = "CALLER-CLAIM"
    elif bad == "overflow":
        data["plan_choices"] = [{**formal_plan_choice(), "option_ref": f"SYN-CHOICE-{i}"} for i in range(101)]
    else:
        del data["plan_choices"]
    with pytest.raises(CommercialRefusalError):
        parse(CommercialWire(schema_bytes), journey_request(), data)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [None, "foreign_choice", "foreign_owner", "no_current_source"])
async def test_c6_4_fresh_formal_choices_need_exact_current_owner_source_proof(schema_bytes, change):
    authority = UnitScope()
    authority.scope = replace(
        authority.scope,
        allowed_operations=frozenset({"journey.get"}),
        granted_scopes=frozenset({"interop/sales.journey.read"}),
        resource_grant_ref="SYN-RESOURCE-GRANT",
        journey_linkage_ref="SYN-CURRENT-OWNER-LINKAGE",
    )
    approved = formal_entry_data()
    sent = deepcopy(approved)
    if change == "foreign_choice":
        sent["plan_choices"][0].update(catalog_ref="FOREIGN-CATALOG", product_ref="FOREIGN-PRODUCT")
    source = CurrentResultSource(authority.scope)

    async def current_authority(command, value, result):
        proof = await CurrentResultSource.result_authority(source, command, value, result)
        if change == "no_current_source":
            raise CommercialRefusalError("unavailable", "source_unavailable")
        return replace(
            proof,
            result_digest=hashlib.sha256(encode(outcome(approved))).hexdigest(),
            scope_digest="FOREIGN-OWNER" if change == "foreign_owner" else proof.scope_digest,
        )

    source.result_authority = current_authority
    calls = []

    def transport(request):
        calls.append(json.loads(request.content)["command"])
        return httpx.Response(200, json=outcome(sent))

    gateway = compose_commercial_gateway(
        schema_bytes=schema_bytes,
        caller=UnitCaller(),
        scope_authority=authority,
        source_authority=source,
        amh_origin="https://amh.invalid",
        sales_admission=sales_session_admission(),
        transport=httpx.MockTransport(transport),
    )
    trusted = await gateway.admission.transport("Bearer SYN-SALES-CREDENTIAL", "SYN-SALES-CONNECTION")
    admitted = await gateway.admit(trusted)
    command = gateway.codec.parse_command(encode(journey_request()))
    result = await gateway.execute(command, admitted)
    assert result.status == ("succeeded" if change is None else "uncertain")
    if change:
        assert json.loads(result.wire_bytes)["data"] is None
    else:
        assert json.loads(result.wire_bytes)["data"] == approved
    assert calls == ["journey.get"] and source.result_calls == ["journey.get"]
