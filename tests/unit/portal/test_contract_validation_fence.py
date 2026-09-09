"""PIC-01/PIC-02: JSON syntax and exact centavos fences, ADR-0049 D3/D4.

These fixtures assert DTO shape only; no membership, authorization or commit is proved.
"""

from __future__ import annotations

import json
import sys
from copy import deepcopy

import pytest
from pydantic import BaseModel, ValidationError

from maezo.portal.contracts import Centavos, HumanCommandReceipt, HumanPrincipal, TaskDecision, TaskSnapshot

NOW = "2026-09-08T16:00:00Z"
DIGEST = "c" * 64
PRINCIPAL: dict[str, object] = {
    "schema_version": 1,
    "principal_ref": "principal:1",
    "issuer": "https://issuer.example/path",
    "subject": "subject:1",
    "tenant": "tenant:1",
    "membership_revision": 1,
    "memberships": [{"membership_ref": "membership:1", "roles": ["employee"], "groups": ["dynamic-group:1"]}],
    "session_ref": "session:1",
    "authenticated_at": NOW,
    "subject_bindings": [{"kind": "provider", "resource_ref": "provider:1"}],
}
DECISION: dict[str, object] = {
    "schema_version": 1,
    "command_id": "command:1",
    "task_id": "task:1",
    "process_definition_key": "SP-OP-PAGTO-001",
    "process_definition_version": 1,
    "process_definition_id": "SP-OP-PAGTO-001:1:deployment",
    "process_definition_digest": DIGEST,
    "task_definition_key": "UT_AnaliseAdmissibilidade",
    "form_key": "pagto_admissibilidade",
    "form_version": 1,
    "form_digest": DIGEST,
    "expected_task_revision": 1,
    "expected_evidence_revision": 1,
    "expected_evidence_digest": DIGEST,
    "expected_membership_revision": 1,
    "inputs": {"kind": "pagto_admissibilidade", "decisao_admissibilidade": "PROSSEGUIR"},
}
SNAPSHOT: dict[str, object] = {
    key: value
    for key, value in DECISION.items()
    if key not in {"command_id", "inputs"} and not key.startswith("expected_")
}
SNAPSHOT.update(
    snapshot_at=NOW,
    task_revision=1,
    assignee_ref="principal:1",
    eligible_candidate_groups=["dynamic-group:1"],
    evidence_revision=1,
    evidence_digest=DIGEST,
    engine_due_at=None,
    allowed_actions=["decision"],
    allowed_inputs=["decisao_admissibilidade", "justificativa_recusa"],
    form_source_status="BPMN_TASK_DOCUMENTATION_DRAFT_VERIFY",
    read_only_evidence={
        "kind": "pagto_admissibilidade",
        "valor_pagamento_cents": "0",
        "dados_pagamento_validos": True,
        "lastro_confirmado": False,
        "duplicidade_suspeita": False,
        "lastro_decisor_id": "principal:1",
    },
)
RECEIPT: dict[str, object] = {
    "schema_version": 1,
    "status": "committed",
    "operation": "decision",
    "command_id": "command:1",
    "tenant": "tenant:1",
    "task_id": "task:1",
    "payload_digest": DIGEST,
    "principal_ref": "principal:1",
    "workload_ref": "workload:1",
    "audit_intent_ref": "intent:1",
    "audit_result_ref": "result:1",
    "recorded_at": NOW,
    "consumed_task_revision": 1,
    "engine_receipt_ref": "receipt:1",
    "engine_commit_ref": "commit:1",
    "engine_committed_at": NOW,
}

MODELS: dict[str, type[BaseModel]] = {
    "principal": HumanPrincipal,
    "decision": TaskDecision,
    "snapshot": TaskSnapshot,
    "receipt": HumanCommandReceipt,
}
PAYLOADS = {"principal": PRINCIPAL, "decision": DECISION, "snapshot": SNAPSHOT, "receipt": RECEIPT}
REF_PATHS = {
    "principal": (
        "principal_ref",
        "subject",
        "tenant",
        "session_ref",
        "memberships.0.membership_ref",
        "memberships.0.roles.0",
        "memberships.0.groups.0",
        "subject_bindings.0.resource_ref",
    ),
    "decision": (
        "command_id",
        "task_id",
        "process_definition_id",
        "process_definition_key",
        "task_definition_key",
    ),
    "snapshot": (
        "task_id",
        "process_definition_id",
        "process_definition_key",
        "task_definition_key",
        "assignee_ref",
        "eligible_candidate_groups.0",
        "read_only_evidence.lastro_decisor_id",
    ),
    "receipt": (
        "command_id",
        "tenant",
        "task_id",
        "principal_ref",
        "workload_ref",
        "audit_intent_ref",
        "audit_result_ref",
        "engine_receipt_ref",
        "engine_commit_ref",
        "technical_code",
    ),
}


def _put(payload: dict[str, object], path: str, value: object) -> None:
    target: object = payload
    *parents, last = path.split(".")
    for part in parents:
        if isinstance(target, list):
            target = target[int(part)]
        else:
            assert isinstance(target, dict)
            target = target[part]
    if isinstance(target, list):
        target[int(last)] = value
    else:
        assert isinstance(target, dict)
        target[last] = value


def _payload(name: str, path: str) -> dict[str, object]:
    payload = deepcopy(PAYLOADS[name])
    if path == "technical_code":
        payload["status"] = "failure"
        for field in (
            "consumed_task_revision",
            "engine_receipt_ref",
            "engine_commit_ref",
            "engine_committed_at",
        ):
            payload.pop(field)
        payload["technical_code"] = "REVISION:CONFLICT"
    return payload


@pytest.mark.parametrize("name,path", [(name, path) for name, paths in REF_PATHS.items() for path in paths])
def test_json_rejects_controls_at_every_opaque_reference(name: str, path: str) -> None:
    baseline = _payload(name, path)
    # Verify the unmodified fixture first: no unrelated binding/status rejection may satisfy the fence.
    MODELS[name].model_validate_json(json.dumps(baseline))
    for code in (*range(32), 127):
        payload = deepcopy(baseline)
        _put(payload, path, "ref" + chr(code) + ":1")
        with pytest.raises(ValidationError) as error:
            MODELS[name].model_validate_json(json.dumps(payload))
        expected = tuple(int(part) if part.isdigit() else part for part in path.split("."))
        assert any(item["loc"][: len(expected)] == expected for item in error.value.errors())


@pytest.mark.parametrize("name", list(MODELS))
def test_valid_opaque_references_keep_colons_and_dynamic_groups(name: str) -> None:
    payload = PAYLOADS[name]
    result = MODELS[name].model_validate_json(json.dumps(payload)).model_dump(mode="json")
    for field, value in payload.items():
        if field not in {"inputs", "read_only_evidence"}:
            assert result[field] == value


@pytest.mark.parametrize("position", ("prefix", "hostname", "path"))
def test_issuer_rejects_c0_del_before_parser_normalization(position: str) -> None:
    for code in (*range(32), 127):
        control = chr(code)
        value = {
            "prefix": control + "https://issuer.example/path",
            "hostname": "https://iss" + control + "uer.example/path",
            "path": "https://issuer.example/pa" + control + "th",
        }[position]
        with pytest.raises(ValidationError):
            HumanPrincipal.model_validate_json(json.dumps({**PRINCIPAL, "issuer": value}))


@pytest.mark.parametrize(
    "value",
    [
        "https://issuer.example:bad",
        "https://issuer.example:65536",
        "https://issuer.example:-1",
        "https://issuer.example:",
        "https://issuer.example/?",
        "https://issuer.example/#",
        "https://issuer.example/?query=1",
        "https://issuer.example/#fragment",
        "https://user:password@issuer.example/path",
        "https://@issuer.example/path",
        "https://:password@issuer.example/path",
        "https://",
        "https://:443/path",
        "issuer.example/path",
        "ftp://issuer.example/path",
        "https:///issuer.example",
        " https://issuer.example",
        "https://issuer.example/space here",
        "https://iss uer.example",
        "https://[::1",
        "https://[::1]garbage/path",
    ],
)
def test_issuer_rejects_malformed_urls_and_forbidden_delimiters(value: str) -> None:
    with pytest.raises(ValidationError):
        HumanPrincipal.model_validate_json(json.dumps({**PRINCIPAL, "issuer": value}))


@pytest.mark.parametrize(
    "value",
    [
        "https://issuer.example",
        "https://issuer.example/",
        "https://issuer.example/a/b/",
        "HTTPS://Issuer.Example:00443/Tenant%2FSub",
        "http://localhost:8080/realm",
        "https://[2001:db8::1]:443/realm",
        "https://[2001:db8::1]/realm",
        "https://issuer.example/%3F%23",
    ],
)
def test_valid_issuer_is_preserved_byte_for_byte(value: str) -> None:
    principal = HumanPrincipal.model_validate_json(json.dumps({**PRINCIPAL, "issuer": value}))
    assert principal.issuer.encode() == value.encode()
    assert json.loads(principal.model_dump_json())["issuer"] == value


@pytest.mark.parametrize("digits", [1, 9, 10, 18, 19, 100, 4299, 4300, 4301, 8603])
@pytest.mark.parametrize("sign", [1, -1])
def test_canonical_centavos_convert_exactly_across_decimal_limit(digits: int, sign: int) -> None:
    raw = "9" * digits
    if sign == -1:
        raw = "-" + raw
    before = sys.get_int_max_str_digits()
    money = Centavos.model_validate_json(json.dumps(raw))
    assert money.as_int() == sign * (10**digits - 1)
    assert sys.get_int_max_str_digits() == before
    assert money.model_dump_json() == json.dumps(raw)


@pytest.mark.parametrize(
    "value,expected", [("0", 0), ("9007199254740993", 2**53 + 1), ("-9007199254740993", -(2**53 + 1))]
)
def test_centavos_zero_and_browser_precision_boundary(value: str, expected: int) -> None:
    assert Centavos.model_validate_json(json.dumps(value)).as_int() == expected


def test_nested_pagto_centavos_use_exact_conversion_without_confirming_lastro() -> None:
    payload = deepcopy(SNAPSHOT)
    _put(payload, "read_only_evidence.valor_pagamento_cents", "1" + "0" * 4300)
    snapshot = TaskSnapshot.model_validate_json(json.dumps(payload))
    assert snapshot.read_only_evidence is not None
    assert snapshot.read_only_evidence.valor_pagamento_cents.as_int() == 10**4300
    assert snapshot.read_only_evidence.lastro_confirmado is False
    assert snapshot.form_source_status == "BPMN_TASK_DOCUMENTATION_DRAFT_VERIFY"
