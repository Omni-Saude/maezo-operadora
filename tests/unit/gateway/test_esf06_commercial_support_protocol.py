"""E-SF-06 — A5 support protocol custody (EP-CORE-04a) and the prospect floor (EP-REG-01a).

Plan file name `tests/unit/gateway/test_commercial_support_protocol.py`; renamed with the
`esf06_` prefix the shared `tests/unit/gateway/` tree requires (EXECUTION-LOG §4).

EP-CORE-04a (DEC-CORE CORE-04 item 2): the protocol is only the OP07 receipt's
`protocol_ref` — "nunca minta protocolo" — and resolution is OP07 custody, never OP03
("support is never acceptance", C6-4 `x-customer-journey.support_readback`). The checks
live in `SupportGatedCodec` and hold WITH the A5 record (the admission gate is inert
then), so they are exercised on admitted compositions; the refusal is the gate's own
`denied/purpose_denied`, which the gateway answers as `202 uncertain/
reconciliation_required` with `data: null` (zero protocol bytes disclosed).

EP-REG-01a: no numeric prazo reaches a support disclosure before Q1/DEC-18
(`unratified_sla_claim`). "Protocolo como primeira ação" for every prospect demand is not
achievable at the C6-4 pin — `contact.request` has no protocol slot — so the plan's
`test_prospect_contact_returns_protocol_first` is pinned as the contract GAP it is
(`test_prospect_contact_protocol_first_is_gap_at_c64_pin`).
"""

from __future__ import annotations

import json

import httpx
import pytest

import tests.unit.test_commercial_admission_a5 as a5
from maezo.adapters.amh.commercial import CommercialWire, _strip_replay_actions
from maezo.gateway.commercial_admission import SupportGatedCodec
from maezo.gateway.commercial_fences import CommercialFenceError, CommercialFenceRefusal, sla_claim_marker
from maezo.ports.commercial import CommercialRefusalError
from tests.unit.test_commercial_admission_a4 import snapshot_result
from tests.unit.test_commercial_c6 import (
    DeliveryOrigins,
    encode,
    journey_request,
    outcome,
    recovery,
)
from tests.unit.test_commercial_http import schema_bytes as schema_bytes

HANDOFF_COMMAND_REF = a5.support_command("support.handoff")["command_ref"]


def projection(**changes):
    arm = a5.case_arm("projection")
    arm.update(changes)
    return arm


def handoff(arm, *, state="pending", receipt=None):
    value = a5.handoff_result(arm)
    value.update(state=state, source_receipt_ref=receipt)
    return value


def admitted_handoff_app(schema_bytes, calls, result):
    def transport(request):
        calls.append(request)
        return httpx.Response(200, json=outcome(result))

    return a5.build_app(
        schema_bytes,
        calls,
        sales_admitted=True,
        catalog_admitted=False,
        support_admitted=True,
        transport=httpx.MockTransport(transport),
    )


def ungated(schema_bytes):
    return SupportGatedCodec(CommercialWire(schema_bytes, delivery_origins=DeliveryOrigins()), gated=False)


def assert_purpose_denied(call):
    with pytest.raises(CommercialRefusalError) as err:
        call()
    assert (err.value.status, err.value.reason_code) == ("denied", "purpose_denied")
    return err.value


# ---------------------------------------------------------------------------------------
# EP-CORE-04a (1): protocol only with the OP07 receipt
# ---------------------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("minted", ["from_command_ref", "without_receipt"])
async def test_protocol_ref_exists_only_with_op07_receipt(schema_bytes, minted):
    # Injected violation: the A5-admitted `support.handoff` result projects a protocol the
    # source never receipted — derived from the caller's own `command_ref`, or any protocol
    # on a `pending` handoff with `source_receipt_ref: null`. Typed refusal at the
    # disclosure gate (`denied/purpose_denied`), the read executed once, and not one byte
    # of the protocol crosses: the HTTP answer is `uncertain` with `data: null`.
    protocol = HANDOFF_COMMAND_REF if minted == "from_command_ref" else "SYN-PORTAL-PROTOCOL"
    receipt = "SYN-OP07-RECEIPT" if minted == "from_command_ref" else None
    state = "received" if receipt else "pending"
    result = handoff(projection(protocolRef=protocol), state=state, receipt=receipt)
    calls = []
    app = admitted_handoff_app(schema_bytes, calls, result)
    served = await a5.post_command(app, a5.support_command("support.handoff"))
    assert served.status_code == 202
    body = served.json()
    assert body["status"] == "uncertain" and body["reason_code"] == "reconciliation_required"
    assert body["data"] is None
    assert protocol.encode() not in served.content
    assert len(calls) == 1
    # The same refusal, typed, at the codec — and on the recovery carrier too.
    codec = ungated(schema_bytes)
    command = codec.parse_command(encode(a5.support_command("support.handoff")))
    assert_purpose_denied(lambda: codec.parse_result(encode(outcome(result)), command))
    recovered = handoff(projection(protocolRef="SYN-ORIGINAL"), state="received", receipt="SYN-OP07-RECEIPT")
    payload = encode(outcome(recovery("support.handoff", _strip_replay_actions(recovered))))
    assert_purpose_denied(lambda: codec.parse_result(payload, a5.operation_request(codec)))


@pytest.mark.asyncio
async def test_receipted_protocol_is_disclosed_byte_exact_with_a5(schema_bytes):
    # Controlled positive: a protocol the source receipted (OP07 `source_receipt_ref`,
    # `received`) crosses unchanged — the custody check never rewrites a source fact.
    arm = projection(protocolRef="SYN-OP07-PROTOCOL")
    calls = []
    app = admitted_handoff_app(
        schema_bytes, calls, handoff(arm, state="received", receipt="SYN-OP07-RECEIPT")
    )
    served = await a5.post_command(app, a5.support_command("support.handoff"))
    assert served.status_code == 200
    assert served.json()["data"]["case_read"] == arm
    # Without a protocol, a pending handoff stays "pedido registrado, aguardando protocolo".
    calls = []
    pending = admitted_handoff_app(schema_bytes, calls, handoff(projection()))
    assert (await a5.post_command(pending, a5.support_command("support.handoff"))).status_code == 200


def test_journey_protocol_derived_from_a_pending_command_ref_is_refused(schema_bytes):
    # The snapshot carrier: a projection whose protocol is one of the journey's own pending
    # command/operation refs is a minted protocol, refused on the direct and recovered
    # snapshot alike.
    codec = ungated(schema_bytes)
    value = a5.journey_with_support(projection(protocolRef="SYN-PENDING-COMMAND"))
    value["pending_operations"] = [
        {
            "command_ref": "SYN-PENDING-COMMAND",
            "operation_ref": "SYN-PENDING-OPERATION",
            "command": "support.handoff",
            "state": "pending",
        }
    ]
    journey_command = codec.parse_command(encode(journey_request()))
    assert_purpose_denied(
        lambda: codec.parse_result(encode(outcome(snapshot_result(value))), journey_command)
    )
    recovered = encode(outcome(recovery("journey.get", _strip_replay_actions(snapshot_result(value)))))
    assert_purpose_denied(lambda: codec.parse_result(recovered, a5.operation_request(codec)))


# ---------------------------------------------------------------------------------------
# EP-CORE-04a (2): resolution never via OP03
# ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("field", ["protocolRef", "caseRef"])
@pytest.mark.parametrize("receipt", ["customer_acceptance", "commercial_acceptance"])
def test_case_resolution_via_op03_is_refused(schema_bytes, field, receipt):
    # Injected violation: the OP03 acceptance receipt of the same journey presented as the
    # case's protocol or case ref. Refused with the gate's reason code even with A5 (gate
    # inert), on the direct and the recovered snapshot.
    value = a5.issued_journey_with_support(a5.case_arm("refused"))
    acceptance_ref = (
        value["acceptance"]["acceptanceRef"]
        if receipt == "customer_acceptance"
        else value["commercial_acceptance"]["acceptance_receipt_ref"]
    )
    value["support"] = projection(**{field: acceptance_ref})
    codec = ungated(schema_bytes)
    journey_command = codec.parse_command(encode(journey_request()))
    assert_purpose_denied(
        lambda: codec.parse_result(encode(outcome(snapshot_result(value))), journey_command)
    )
    recovered = encode(outcome(recovery("journey.get", _strip_replay_actions(snapshot_result(value)))))
    assert_purpose_denied(lambda: codec.parse_result(recovered, a5.operation_request(codec)))
    # Control: the same issued+accepted snapshot with a source-own case ref is disclosed.
    value["support"] = projection()
    parsed = codec.parse_result(encode(outcome(snapshot_result(value))), journey_command)
    assert json.loads(parsed.wire_bytes)["data"]["journeys"][0]["support"] == projection()


# ---------------------------------------------------------------------------------------
# EP-CORE-04a (3): existing A5 pins, repinned under this epic
# ---------------------------------------------------------------------------------------


def test_repin_a5_scope_is_exactly_the_six_contract_commands(schema_bytes):
    # Repin of `test_admitted_scope_is_exactly_the_contract_support_work_commands`
    # (plan: `test_commercial_admission_a5.py:397`).
    a5.test_admitted_scope_is_exactly_the_contract_support_work_commands(schema_bytes)


def test_repin_a5_gate_refuses_every_carrier(schema_bytes):
    # Repin of `test_gate_refuses_every_carrier_with_the_exact_reason_code` (plan :652).
    a5.test_gate_refuses_every_carrier_with_the_exact_reason_code(schema_bytes)
    # …and the direct `support.handoff` carrier is a fourth one under the closed gate.
    codec = SupportGatedCodec(CommercialWire(schema_bytes, delivery_origins=DeliveryOrigins()), gated=True)
    command = codec.parse_command(encode(a5.support_command("support.handoff")))
    assert_purpose_denied(lambda: codec.parse_result(encode(outcome(handoff(projection()))), command))


def test_repin_escalation_stays_outside_the_admission_stack():
    # Repin of `test_escalation_substrate_stays_outside_the_admission_stack` (plan :765):
    # non-technical refusals go to the human `SP-OP-ESCALATION-001` queue, never a code path
    # of the admission stack.
    a5.test_escalation_substrate_stays_outside_the_admission_stack()


# ---------------------------------------------------------------------------------------
# EP-REG-01a: prospect floor
# ---------------------------------------------------------------------------------------


def test_prospect_contact_protocol_first_is_gap_at_c64_pin(schema_bytes):
    # Plan name `test_prospect_contact_returns_protocol_first` (EP-REG-01a) is a GAP at this
    # pin. Honest closed-state pin: "toda demanda de prospect retorna protocolo antes de
    # qualquer outra resposta" has no carrier at the C6-4 pin. `contact.request` returns
    # `contact_request_ref/revision/state` and no protocol slot, and the `support.handoff`
    # protocol is nullable until the OP07 receipt — so a `protocol_missing` refusal would
    # close every contact request. The protocol slot is successor-contract work (AMH);
    # until then the floor that holds is "never a minted protocol" (EP-CORE-04a above).
    defs = json.loads(schema_bytes)["$defs"]
    contact = defs["contact_requestResultData"]
    assert set(contact["properties"]) == {"kind", "contact_request_ref", "revision", "state"}
    assert not [name for name in contact["properties"] if "protocol" in name.lower()]
    protocol = defs["CustomerSupportRead"]["oneOf"][0]["properties"]["protocolRef"]
    assert {"type": "null"} in protocol["anyOf"]


@pytest.mark.parametrize(
    "claim",
    [
        "Responderemos em 7 dias úteis",
        "Retorno em até setenta e duas horas",
        "Prazo: 10 dias",
        "em 48h",
        "Prazo máximo de 10 dias",
        "Resolvemos em no máximo 3 dias",
        "Retorno em 2 a 3 dias",
        "Retorno em ate 2 (dois) dias",
        "Retorno: 5 dias úteis",
    ],
)
def test_prospect_no_sla_claim_before_q1(schema_bytes, claim):
    # Injected violation: a numeric prazo in a source-provided support label (topic label,
    # case kind/status label) before Q1/DEC-18 are ratified. Refused before disclosure,
    # typed `unratified_sla_claim`, with or without A5.
    assert sla_claim_marker(claim)
    codec = ungated(schema_bytes)
    journey_command = codec.parse_command(encode(journey_request()))
    topic = a5.journey_with_support(a5.case_arm("refused"))
    topic["support_topics"][0]["label"] = claim
    case_label = a5.journey_with_support(projection(caseStatusLabel=claim))
    for value in (topic, case_label):
        refusal = assert_purpose_denied(
            lambda value=value: codec.parse_result(encode(outcome(snapshot_result(value))), journey_command)
        )
        assert isinstance(refusal.__cause__, CommercialFenceError)
        assert refusal.__cause__.reason is CommercialFenceRefusal.UNRATIFIED_SLA_CLAIM
        assert str(refusal.__cause__) == "unratified_sla_claim"
    command = codec.parse_command(encode(a5.support_command("support.handoff")))
    labelled = handoff(projection(caseKindLabel=claim))
    assert_purpose_denied(lambda: codec.parse_result(encode(outcome(labelled)), command))


@pytest.mark.parametrize(
    "label",
    [
        "Dúvidas sobre o plano",
        "Etapa 2 de 5",
        "Plano Empresarial 2024",
        "algum dia",
        "Atendimento 24 horas",
        "Central 24h",
        "Carência de 180 dias",
        "Cobertura parcial temporária de 24 meses",
        "Um dia de visita",
    ],
)
def test_label_without_a_prazo_is_not_an_sla_claim(schema_bytes, label):
    # Control: labels with numbers that are not a time span stay disclosed byte-exact.
    assert not sla_claim_marker(label)
    codec = ungated(schema_bytes)
    value = a5.journey_with_support(a5.case_arm("refused"))
    value["support_topics"][0]["label"] = label
    parsed = codec.parse_result(
        encode(outcome(snapshot_result(value))), codec.parse_command(encode(journey_request()))
    )
    assert json.loads(parsed.wire_bytes)["data"]["journeys"][0]["support_topics"][0]["label"] == label


def test_wire_envelope_is_unchanged_for_non_support_commands(schema_bytes):
    # The new custody checks key only on support carriers.
    codec = ungated(schema_bytes)
    reference = CommercialWire(schema_bytes, delivery_origins=DeliveryOrigins())
    command = codec.parse_command(encode(a5.wire_command()))
    payload = encode(a5.wire_result())
    assert codec.parse_result(payload, command) == reference.parse_result(payload, command)
