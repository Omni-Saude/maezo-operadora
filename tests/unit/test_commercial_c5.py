"""Exact-C5 synthetic codec/proof regressions; no real issuer, acceptance or engine."""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest

from maezo.adapters.amh.commercial import CommercialWire, _strip_replay_actions
from maezo.ports.commercial import CommercialRefusalError, IssuerDocumentOrigins, TermsSelection
from tests.unit import test_commercial_http
from tests.unit.test_commercial_gateway import NOW, UnitCodec, composed, scope


@pytest.fixture(name="schema_bytes")
def candidate_bytes():
    # Reuse the mandatory exact-byte and source-provenance assertions, no fallback.
    return test_commercial_http.schema_bytes.__wrapped__()


class SyntheticIssuerOrigins(IssuerDocumentOrigins):
    """Explicit synthetic issuer source; no production origin or grant is declared."""

    def __init__(self):
        self.revoked = False
        self.observed = []

    def current_origins(self, selection: TermsSelection, terms_review_ref: str) -> frozenset[str]:
        self.observed.append((selection, terms_review_ref))
        if self.revoked:
            raise CommercialRefusalError("denied", "authority_revoked")
        return frozenset({"https://issuer.invalid"})


def request(name="offer.terms", *, decision="accept"):
    payload = {"quote_ref": "SYN-QUOTE", "source_revision": 7, "terms_digest": "b" * 64}
    if name == "acceptance.record":
        payload.update(party_linkage_ref="SYN-PARTY", terms_review_ref="SYN-REVIEW", decision=decision)
    return {
        "schema_version": "omni-commercial.proposed.v1",
        "command": name,
        "command_ref": "SYN-CMD",
        "expected_revision": None,
        "payload": payload,
    }


def presentation(state="presented"):
    return {
        "kind": "terms_presentation",
        "quote_ref": "SYN-QUOTE",
        "source_revision": 7,
        "terms_digest": "b" * 64,
        "terms_review_ref": "SYN-REVIEW",
        "review_state": state,
        "presentation_receipt_ref": "SYN-PRESENTATION" if state == "presented" else None,
        "hosted_document": {"url": "https://issuer.invalid/document?opaque=ephemeral", "expires_at": None},
    }


def result(data):
    return {
        "schema_version": "omni-commercial.proposed.v1",
        "status": "succeeded",
        "operation_ref": "SYN-OP",
        "reason_code": None,
        "data": data,
    }


def parse(codec, value, command=None):
    return codec.parse_result(
        json.dumps(value).encode(), codec.parse_command(json.dumps(command or request()).encode())
    )


@pytest.mark.parametrize("state", ["pending", "presented"])
@pytest.mark.parametrize("expiry", [None, "2099-01-01T00:00:00Z"])
def test_fresh_terms_exact_source_selectors_and_unknown_source_expiry(schema_bytes, state, expiry):
    authority = SyntheticIssuerOrigins()
    codec = CommercialWire(schema_bytes, issuer_document_origins=authority)
    data = presentation(state)
    data["hosted_document"]["expires_at"] = expiry
    parsed = parse(codec, result(data))
    assert json.loads(parsed.wire_bytes)["data"] == data
    assert parsed.terms_presentation.review_state == state
    assert authority.observed[-1] == (
        TermsSelection(quote_ref="SYN-QUOTE", source_revision=7, terms_digest="b" * 64),
        "SYN-REVIEW",
    )


def test_document_authority_is_absent_by_default_and_cannot_reuse_psp_origins(schema_bytes):
    codec = CommercialWire(schema_bytes, hosted_origins={"stripe": frozenset({"https://issuer.invalid"})})
    with pytest.raises(CommercialRefusalError):
        parse(codec, result(presentation()))


@pytest.mark.parametrize(
    "url",
    [
        "http://issuer.invalid/document",
        "https://attacker.invalid/document",
        "https://issuer.invalid.attacker.invalid/document",
        "https://issuer.invalid:444/document",
        "https://user:secret@issuer.invalid/document",
        "https://issuer.invalid/document#opaque",
        "https://checkout.stripe.com/document#stripefragment",
    ],
)
def test_document_origin_policy_rejects_unsafe_destination_without_stripe_exception(schema_bytes, url):
    codec = CommercialWire(
        schema_bytes,
        issuer_document_origins=SyntheticIssuerOrigins(),
        hosted_origins={"stripe": frozenset({"https://checkout.stripe.com"})},
    )
    data = presentation()
    data["hosted_document"]["url"] = url
    with pytest.raises(CommercialRefusalError):
        parse(codec, result(data))


@pytest.mark.parametrize("expiry", ["2000-01-01T00:00:00Z", "invalid", "2099-01-01T00:00:00"])
def test_known_expired_or_invalid_document_expiry_refused(schema_bytes, expiry):
    codec = CommercialWire(schema_bytes, issuer_document_origins=SyntheticIssuerOrigins())
    data = presentation()
    data["hosted_document"]["expires_at"] = expiry
    with pytest.raises(CommercialRefusalError):
        parse(codec, result(data))


@pytest.mark.parametrize(
    "field,value",
    [
        ("quote_ref", "OTHER"),
        ("source_revision", 8),
        ("terms_digest", "c" * 64),
        ("presentation_receipt_ref", None),
    ],
)
def test_exact_terms_and_presented_receipt_must_match(schema_bytes, field, value):
    codec = CommercialWire(schema_bytes, issuer_document_origins=SyntheticIssuerOrigins())
    data = presentation()
    data[field] = value
    with pytest.raises(CommercialRefusalError):
        parse(codec, result(data))


def test_source_revocation_denies_even_unknown_url_expiry(schema_bytes):
    authority = SyntheticIssuerOrigins()
    codec = CommercialWire(schema_bytes, issuer_document_origins=authority)
    parse(codec, result(presentation()))
    authority.revoked = True
    with pytest.raises(CommercialRefusalError):
        parse(codec, result(presentation()))


def test_c4_candidate_and_acceptance_without_review_are_not_upgraded(schema_bytes):
    artifact = Path(__file__).resolve().parents[1] / "candidate_artifacts/commercial/contract-c4.json"
    with pytest.raises(ValueError):
        CommercialWire(artifact.read_bytes())
    codec = CommercialWire(schema_bytes)
    command = request("acceptance.record")
    del command["payload"]["terms_review_ref"]
    with pytest.raises(CommercialRefusalError):
        codec.parse_command(json.dumps(command).encode())


@pytest.mark.parametrize(
    "field,value",
    [
        ("terms_review_ref", "FOREIGN"),
        ("quote_ref", "FOREIGN"),
        ("source_revision", 8),
        ("party_linkage_ref", "FOREIGN"),
        ("terms_digest", "c" * 64),
        ("decision", "declined"),
    ],
)
def test_acceptance_receipt_exact_review_and_all_source_selectors(schema_bytes, field, value):
    codec = CommercialWire(schema_bytes)
    command = request("acceptance.record")
    data = {
        "kind": "acceptance",
        **command["payload"],
        "decision": "accepted",
        "acceptance_receipt_ref": "SYN-ACCEPTANCE",
        "recorded_at": "2026-10-22T00:00:00Z",
    }
    parse(codec, result(data), command)
    data[field] = value
    with pytest.raises(CommercialRefusalError):
        parse(codec, result(data), command)


def recovered(data, original_command="offer.terms"):
    return result(
        {
            "kind": "operation_status",
            "target_command_ref": "SYN-CMD",
            "original_command": original_command,
            "state": "completed",
            "original_result": result(data),
        }
    )


def recovery_request():
    value = request("operation.get")
    value["payload"] = {"target_command_ref": "SYN-CMD"}
    return value


def test_terms_recovery_requires_sanitized_variant_and_never_becomes_fresh_success(schema_bytes):
    codec = CommercialWire(schema_bytes, issuer_document_origins=SyntheticIssuerOrigins())
    fresh = presentation()
    retained = deepcopy(fresh)
    sanitized = _strip_replay_actions(fresh)
    assert fresh == retained
    assert sanitized["hosted_document"] is None
    parsed = parse(codec, recovered(sanitized), recovery_request())
    assert b"https://" not in parsed.wire_bytes
    assert parsed.terms_presentation is None
    with pytest.raises(CommercialRefusalError):
        parse(codec, recovered(fresh), recovery_request())
    with pytest.raises(CommercialRefusalError):
        parse(codec, result(sanitized))
    for wrong in ["offer.quote", "operation.get", "acceptance.record"]:
        with pytest.raises(CommercialRefusalError):
            parse(codec, recovered(sanitized, wrong), recovery_request())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "defect",
    [
        "absent",
        "foreign_scope",
        "quote",
        "revision",
        "digest",
        "review",
        "pending",
        "expired_receipt",
        "expired_source",
        "missing_receipt",
        "missing_validity",
    ],
)
async def test_acceptance_source_proof_defects_prevent_any_effect(defect):
    gateway, _, source = composed()
    original_prepare = source.prepare

    async def prepare(command, admitted):
        prepared = await original_prepare(command, admitted)
        proof = prepared.terms_review
        if defect == "absent":
            return replace(prepared, terms_review=None)
        if defect == "foreign_scope":
            proof = replace(proof, scope_digest="different-actor-or-representation")
        if defect == "quote":
            proof = replace(proof, selection=replace(proof.selection, quote_ref="foreign"))
        if defect == "revision":
            proof = replace(proof, selection=replace(proof.selection, source_revision=2))
        if defect == "digest":
            proof = replace(proof, selection=replace(proof.selection, terms_digest="c" * 64))
        if defect == "review":
            proof = replace(proof, terms_review_ref="foreign")
        if defect == "pending":
            proof = replace(
                proof, review_state="pending", presentation_receipt_ref=None, presentation_valid_until=None
            )
        if defect == "expired_receipt":
            proof = replace(proof, presentation_valid_until=NOW - timedelta(seconds=1))
        if defect == "expired_source":
            proof = replace(proof, valid_until=NOW - timedelta(seconds=1))
        if defect == "missing_receipt":
            proof = replace(proof, presentation_receipt_ref=None)
        if defect == "missing_validity":
            proof = replace(proof, document_validity_ref="")
        return replace(prepared, terms_review=proof)

    source.prepare = prepare
    parsed = await gateway.execute(UnitCodec().parse_command(b"acceptance.record"), scope())
    assert parsed.status == "denied"
    assert "execute" not in source.calls


@pytest.mark.asyncio
async def test_current_bound_pending_review_allows_decline_without_claiming_presentation():
    gateway, _, source = composed()
    original_prepare = source.prepare
    original_parse = gateway.codec.parse_command

    def parse_decline(body):
        command = original_parse(body)
        return (
            replace(command, terms_selection=replace(command.terms_selection, decision="decline"))
            if command.terms_selection
            else command
        )

    gateway.codec.parse_command = parse_decline

    async def prepare(command, admitted):
        prepared = await original_prepare(command, admitted)
        return replace(
            prepared,
            terms_review=replace(
                prepared.terms_review,
                review_state="pending",
                presentation_receipt_ref=None,
                presentation_valid_until=None,
            ),
        )

    source.prepare = prepare
    parsed = await gateway.execute(gateway.codec.parse_command(b"acceptance.record"), scope())
    assert parsed.status == "succeeded"
    assert source.executed_commands == ["acceptance.record"]


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["pending", "presented"])
@pytest.mark.parametrize("revoke_at_disclosure", [False, True])
async def test_actual_c5_wire_gateway_fresh_delivery_and_final_issuer_recheck(
    schema_bytes, state, revoke_at_disclosure
):
    from maezo.gateway.commercial import CommercialGateway
    from tests.unit.test_commercial_gateway import UnitAdmission, UnitSource

    admitted = replace(scope(), allowed_operations=scope().allowed_operations | {"offer.terms"})
    issuer = SyntheticIssuerOrigins()
    codec = CommercialWire(schema_bytes, issuer_document_origins=issuer)
    admission = UnitAdmission(admitted)
    source = UnitSource(admitted)
    original_prepare = source.prepare
    original_current = admission.current

    async def prepare(command, current_scope):
        prepared = await original_prepare(command, current_scope)
        return replace(
            prepared,
            terms_review=replace(
                prepared.terms_review,
                terms_review_ref="SYN-REVIEW",
                review_state=state,
                presentation_receipt_ref="SYN-PRESENTATION" if state == "presented" else None,
                presentation_valid_until=NOW + timedelta(minutes=5) if state == "presented" else None,
            ),
        )

    async def execute(command, current_scope, prepared):
        source.executed_commands.append(command.command)
        body = result(presentation(state))
        body["operation_ref"] = prepared.operation_ref
        return codec.parse_result(json.dumps(body).encode(), command)

    async def current(value, *, phase, command=None):
        value = await original_current(value, phase=phase, command=command)
        if revoke_at_disclosure and phase == "before_disclosure":
            issuer.revoked = True
        return value

    source.prepare = prepare
    source.execute = execute
    admission.current = current
    gateway = CommercialGateway(codec=codec, admission=admission, source=source, clock=lambda: NOW)
    command = codec.parse_command(json.dumps(request()).encode())
    parsed = await gateway.execute(command, admitted)
    assert source.executed_commands == ["offer.terms"]
    if revoke_at_disclosure:
        assert parsed.status == "uncertain"
        assert b"https://" not in parsed.wire_bytes
    else:
        assert parsed.status == "succeeded"
        assert parsed.terms_presentation.review_state == state


@pytest.mark.asyncio
@pytest.mark.parametrize("issuer_bound", [False, True])
async def test_public_composition_factory_forwards_independent_current_issuer_port(
    schema_bytes, issuer_bound
):
    import httpx

    from maezo.gateway.commercial_admission import compose_commercial_gateway
    from maezo.ports.commercial import TermsReviewAuthority, scope_digest

    scope_authority = test_commercial_http.UnitScope()
    scope_authority.scope = replace(
        scope_authority.scope, allowed_operations=scope_authority.scope.allowed_operations | {"offer.terms"}
    )
    source_authority = test_commercial_http.UnitSourceAuthority(scope_authority.scope)
    original_prepare = source_authority.prepare
    calls = []

    async def prepare(command, admitted):
        prepared = await original_prepare(command, admitted)
        return replace(
            prepared,
            terms_review=TermsReviewAuthority(
                selection=command.terms_selection,
                scope_digest=scope_digest(admitted),
                terms_review_ref="SYN-REVIEW",
                review_state="presented",
                presentation_receipt_ref="SYN-PRESENTATION",
                presentation_valid_until=admitted.valid_until,
                currentness_ref="SYN-TERMS-CURRENT",
                document_validity_ref="SYN-DOCUMENT-VALID",
                valid_until=admitted.valid_until,
            ),
        )

    source_authority.prepare = prepare

    async def amh(http_request):
        assert http_request.url == "https://amh.invalid/interop/sales/v1/commercial"
        assert http_request.headers["authorization"] == "Bearer SYN-MAEZO-CREDENTIAL"
        assert "cookie" not in http_request.headers
        calls.append(json.loads(http_request.content)["command"])
        return httpx.Response(200, json=result(presentation()))

    issuer = SyntheticIssuerOrigins()
    gateway = compose_commercial_gateway(
        schema_bytes=schema_bytes,
        amh_origin="https://amh.invalid",
        caller=test_commercial_http.UnitCaller(),
        scope_authority=scope_authority,
        source_authority=source_authority,
        sales_admission=test_commercial_http.sales_session_admission(),
        issuer_document_origins=issuer if issuer_bound else None,
        # This PSP origin cannot admit an issuer document when its own port is absent.
        hosted_origins={"stripe": frozenset({"https://issuer.invalid"})},
        transport=httpx.MockTransport(amh),
    )
    transport = await gateway.admission.transport("Bearer SYN-SALES-CREDENTIAL", "SYN-SALES-CONNECTION")
    admitted = await gateway.admit(transport)
    command = gateway.codec.parse_command(json.dumps(request()).encode())
    parsed = await gateway.execute(command, admitted)
    assert calls == ["offer.terms"]
    if issuer_bound:
        assert parsed.status == "succeeded"
        assert issuer.observed
        assert parsed.terms_presentation.terms_review_ref == "SYN-REVIEW"
    else:
        assert parsed.status == "uncertain"
        assert b"https://" not in parsed.wire_bytes
        assert not issuer.observed
