"""ADR-0049 D5 environment fences; real signatures and HTTP doubles, no live TLS."""

import base64
import hashlib
from unittest.mock import Mock

import httpx
import pytest
from tests.unit.gateway.human.test_decision_durable_propagation import payload
from tests.unit.gateway.human.test_durable_projection import wire
from tests.unit.gateway.human.test_durable_transport import signer, transport

from maezo.gateway.human.transport import EngineUnavailableError
from maezo.portal.engine.decision import HumanDecisionCommand
from maezo.portal.engine.profile import canonicalize, strict_loads


def classified(scope, mismatch=None):
    value = payload()
    value["scope"] = scope.model_dump()
    if mismatch == "environment_case":
        value["scope"]["environment"] = scope.environment.upper()
        assert value["scope"]["environment"] != scope.environment
    elif mismatch is not None:
        value["scope"][mismatch] = "foreign-value"
    value["audit_intent_ref"] = hashlib.sha256(
        canonicalize([value["scope"], value["target"]["task_id"], value["target"]["command_id"]])
    ).hexdigest()
    return HumanDecisionCommand(canonicalize(value))


@pytest.mark.parametrize("purpose", ["human-command", "human-receipt"])
@pytest.mark.parametrize("mismatch", ["environment", "environment_case", "tenant", "workload_ref"])
def test_classified_scope_refuses_before_private_key_use(purpose, mismatch):
    signing, _, key = signer()
    command = classified(signing.scope, mismatch)
    observed_key = Mock(wraps=key)
    signing._key = observed_key

    with pytest.raises(EngineUnavailableError):
        signing.envelope(command, purpose=purpose)

    observed_key.sign.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("query", [False, True])
@pytest.mark.parametrize("mismatch", ["environment", "environment_case", "tenant", "workload_ref"])
async def test_transport_owns_scope_guard_before_signer_or_http_client(monkeypatch, query, mismatch):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(404, json={"error": "RECEIPT_NOT_FOUND"})

    adapter, clients = transport(monkeypatch, handler)
    command = classified(adapter.scope, mismatch)
    # A permissive signer cannot substitute for the transport's independent fence.
    observed_signer = Mock(return_value=b"unit-only-permissive-envelope")
    monkeypatch.setattr(adapter._signer, "envelope", observed_signer)

    with pytest.raises(EngineUnavailableError):
        await (adapter.receipt(command) if query else adapter.dispatch(command))

    observed_signer.assert_not_called()
    assert clients == []
    assert requests == []


@pytest.mark.parametrize("purpose", ["human-command", "human-receipt"])
@pytest.mark.parametrize("profile", ["classified", "assignment_v1"])
def test_matching_scope_preserves_canonical_command_and_receipt_query_signature(purpose, profile):
    signing, _, key = signer()
    command = classified(signing.scope) if profile == "classified" else wire()
    original = command.canonical
    envelope = strict_loads(signing.envelope(command, purpose=purpose))
    signature = base64.urlsafe_b64decode(envelope.pop("signature") + "==")
    key.public_key().verify(signature, canonicalize(envelope))
    assert envelope["purpose"] == purpose
    assert command.canonical == original
    if purpose == "human-command":
        assert canonicalize(envelope["command"]) == original
    else:
        assert envelope["command"] == {
            "schema": "human-receipt-query.v1",
            "tenant": command.tenant,
            "workload_ref": command.workload_ref,
            "task_id": command.task_id,
            "command_id": command.command_id,
            "principal_ref": command.principal_ref,
            "principal_issuer": command.principal_issuer,
            "principal_subject": command.principal_subject,
            "payload_digest": command.digest,
        }
    if profile == "assignment_v1":
        assert strict_loads(original)["schema"] == "human-command.v1"
        assert not hasattr(command, "environment")
