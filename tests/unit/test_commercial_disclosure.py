"""Third-party C5 timing regressions; synthetic clocks/authority, no real IO."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from maezo.adapters.amh import commercial as wire_module
from maezo.adapters.amh.commercial import CommercialWire
from maezo.gateway.commercial import CommercialGateway
from maezo.ports.commercial import CommercialRefusalError
from tests.unit import test_commercial_http
from tests.unit.test_commercial_c5 import SyntheticIssuerOrigins, parse, presentation, request, result
from tests.unit.test_commercial_gateway import NOW, UnitAdmission, UnitSource, scope


@pytest.fixture
def schema_bytes():
    return test_commercial_http.schema_bytes.__wrapped__()


@pytest.mark.parametrize("expiry", ["known", "unknown"])
def test_document_expiry_is_checked_after_current_issuer_lookup(schema_bytes, monkeypatch, expiry):
    tick = [NOW]

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return tick[0]

    class SlowIssuer(SyntheticIssuerOrigins):
        def current_origins(self, selection, terms_review_ref):
            origins = super().current_origins(selection, terms_review_ref)
            tick[0] += timedelta(seconds=2)
            return origins

    monkeypatch.setattr(wire_module, "datetime", Clock)
    issuer = SlowIssuer()
    codec = CommercialWire(schema_bytes, issuer_document_origins=issuer)
    data = presentation()
    data["hosted_document"]["expires_at"] = (
        (NOW + timedelta(seconds=1)).isoformat() if expiry == "known" else None
    )
    if expiry == "known":
        with pytest.raises(CommercialRefusalError) as refusal:
            parse(codec, result(data))
        assert refusal.value.status == "unavailable"
        assert refusal.value.reason_code == "source_unavailable"
    else:
        parsed = parse(codec, result(data))
        assert parsed.status == "succeeded"
        assert json.loads(parsed.wire_bytes)["data"]["hosted_document"]["expires_at"] is None
    assert len(issuer.observed) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("expires", ["scope", "prepared", "presentation_receipt", "none"])
async def test_final_issuer_lookup_cannot_outlive_admitted_or_prepared_lease(schema_bytes, expires):
    tick = [NOW]
    admitted = replace(
        scope(),
        allowed_operations=scope().allowed_operations | {"offer.terms"},
        valid_until=NOW + timedelta(seconds=1) if expires == "scope" else scope().valid_until,
    )

    class FinalSlowIssuer(SyntheticIssuerOrigins):
        def current_origins(self, selection, terms_review_ref):
            origins = super().current_origins(selection, terms_review_ref)
            # Source parse, gateway input parse, then final post-currentness parse.
            if len(self.observed) == 3:
                tick[0] = NOW + timedelta(seconds=2)
            return origins

    issuer = FinalSlowIssuer()
    codec = CommercialWire(schema_bytes, issuer_document_origins=issuer)
    source = UnitSource(admitted)
    original_prepare = source.prepare

    async def prepare(command, current_scope):
        prepared = await original_prepare(command, current_scope)
        return replace(
            prepared,
            valid_until=NOW + timedelta(seconds=1) if expires == "prepared" else prepared.valid_until,
            terms_review=replace(
                prepared.terms_review,
                terms_review_ref="SYN-REVIEW",
                presentation_receipt_ref="SYN-PRESENTATION",
                presentation_valid_until=NOW + timedelta(seconds=1)
                if expires == "presentation_receipt"
                else NOW + timedelta(minutes=5),
            ),
        )

    async def execute(command, current_scope, prepared):
        source.executed_commands.append(command.command)
        body = result(presentation())  # Null document expiry requires current source; no made-up TTL.
        body["operation_ref"] = prepared.operation_ref
        return codec.parse_result(json.dumps(body).encode(), command)

    source.prepare = prepare
    source.execute = execute
    gateway = CommercialGateway(
        codec=codec, admission=UnitAdmission(admitted), source=source, clock=lambda: tick[0]
    )
    command = codec.parse_command(json.dumps(request()).encode())
    parsed = await gateway.execute(command, admitted)
    assert source.executed_commands == ["offer.terms"]
    assert len(issuer.observed) == 3
    assert parsed.operation_ref == "operation-source"
    assert tick[0].tzinfo is UTC
    if expires == "none":
        assert parsed.status == "succeeded" and parsed.terms_presentation is not None
    else:
        assert parsed.status == "uncertain"
        assert parsed.reason_code == "reconciliation_required"
        assert json.loads(parsed.wire_bytes)["data"] is None
        assert b"https://" not in parsed.wire_bytes
