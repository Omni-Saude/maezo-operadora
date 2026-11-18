"""E-SF-05r (parte b), EP-REG-17a parcial — declarações nunca no payload do aceite.

O teste nomeado pelo plano, `test_declarations_never_in_acceptance_payload`, pinado no comando
`acceptance.record` pelo codec congelado de ponta a ponta. As outras duas guardas da EP-REG-17a
(recibo da declaração plano-referência e `signature_mode`) NÃO entram: o contrato congelado não
tem campo para elas, e criar campo exige contrato sucessor. Enquanto isso, todo `accept` já é
recusado no portão de divulgação RN 413 (`terms_review_required`); essa regressão já está pinada
em `tests/unit/gateway/test_esf36_disclosure_wiring.py`, nos testes
`test_accept_on_the_pinned_wire_refuses_before_decision` e
`test_bound_route_refuses_accept_with_zero_source_dispatch`.
"""

from __future__ import annotations

import json

import pytest

from maezo.adapters.amh.commercial import CommercialWire
from maezo.ports.commercial import CommercialRefusalError
from tests.unit.test_commercial_admission_a3 import acceptance_command
from tests.unit.test_commercial_http import schema_bytes as schema_bytes


def _encode(command: dict) -> bytes:
    return json.dumps(command, separators=(",", ":")).encode()


def test_acceptance_command_is_accepted_by_the_codec_as_control(schema_bytes) -> None:
    # Controle: sem chave de declaração o comando passa pelo codec (o teste abaixo não é vácuo).
    parsed = CommercialWire(schema_bytes).parse_command(_encode(acceptance_command()))
    assert parsed.command == "acceptance.record"


@pytest.mark.parametrize(
    "key",
    [
        "declaration",
        "declaracao_saude",
        "health_declaration",
        "ds_respostas",
        "cpt",
        "plano_referencia_declaration",
    ],
)
@pytest.mark.parametrize("decision", ["accept", "decline"])
def test_declarations_never_in_acceptance_payload(schema_bytes, key, decision) -> None:
    codec = CommercialWire(schema_bytes)
    top = acceptance_command(decision)
    top["payload"][key] = {"value": True}
    with pytest.raises(CommercialRefusalError):
        codec.parse_command(_encode(top))
    envelope = acceptance_command(decision)
    envelope[key] = True  # também fora do payload, no envelope
    with pytest.raises(CommercialRefusalError):
        codec.parse_command(_encode(envelope))


def test_no_declaration_slot_exists_in_the_frozen_acceptance_payload(schema_bytes) -> None:
    contract = json.loads(schema_bytes)
    payload = contract["$defs"]["AcceptanceRecordPayload"]
    assert payload["additionalProperties"] is False
    assert not [k for k in payload["properties"] if "declar" in k.lower() or "signature" in k.lower()]
