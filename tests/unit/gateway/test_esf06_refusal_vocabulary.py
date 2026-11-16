"""E-SF-06 — closed, non-clinical refusal vocabulary (TB-E-08-a, EP-REG-29a, EP-REG-29b).

Plan target `tests/unit/gateway/test_refusal_vocabulary.py`; renamed with the `esf06_`
prefix of the shared `tests/unit/gateway/` tree (EXECUTION-LOG §4).

- TB-E-08-a: a source result whose refusal outcome is outside the closed C6-4 vocabulary
  (`health_condition` / `preexisting_condition` / `age_limit`) is refused typed at the codec
  (C6-4 invariant "Unknown keys rejected recursively", :448), and the vocabulary is
  exactly the current one. GAP: the "alerta de compliance" has no emitter in this plane.
- EP-REG-29a: CI fails if any refusal enum (every candidate contract C4..C6-4 and the
  commercial admission refusal Literals) carries health/age/disability/fraud semantics.
  The lint lives here, not in the gate path, which carries no age/disability identifier
  (E-SF-04 `test_no_age_or_disability_gate`).
- EP-REG-29b (only after ratification RL-REG-29): closed-state guard. A refused arm carries
  only `schemaVersion/status/outcome`; any motive/anchor/prazo slot is refused until the
  ratified anchors exist (GAP).
"""

from __future__ import annotations

import importlib
import json
import typing
from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from maezo.adapters.amh.commercial import CommercialWire
from maezo.gateway.commercial_fences import health_marker
from maezo.ports.commercial import CommercialRefusalError
from tests.unit.test_commercial_admission_a4 import snapshot_result
from tests.unit.test_commercial_c6 import (
    DeliveryOrigins,
    encode,
    journey,
    journey_request,
    outcome,
)
from tests.unit.test_commercial_http import schema_bytes as schema_bytes

ROOT = Path(__file__).resolve().parents[3]
CONTRACTS = sorted((ROOT / "tests" / "candidate_artifacts" / "commercial").glob("contract-*.json"))
ADMISSION_MODULES = sorted((ROOT / "src" / "maezo" / "gateway").glob("commercial_*_admission.py"))

#: The C6-4 property names whose enums are refusal vocabularies.
REFUSAL_ENUM_KEYS = frozenset({"outcome", "reason", "reason_code", "refusalReason", "unknownReason"})
# REG-29a forbids "saúde/idade/deficiência/fraude"; TB-E-08-a injects `health_condition`,
# `preexisting_condition`, `age_limit`. Health via the E-SF-04 marker; the rest are tokens.
_EXACT = frozenset({"age", "idade", "etaria", "etario", "pcd", "dlp", "condition"})
_PREFIXES = ("preexist", "disab", "deficien", "fraud", "idos", "elder")
INJECTED = ("health_condition", "preexisting_condition", "age_limit")


def forbidden(member: str) -> bool:
    tokens = [token for token in member.casefold().replace("-", "_").split("_") if token]
    return health_marker(member) or any(token in _EXACT or token.startswith(_PREFIXES) for token in tokens)


def refusal_enums(document: Any) -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}

    def walk(node: Any) -> None:
        if isinstance(node, Mapping):
            for key, item in node.items():
                if key in REFUSAL_ENUM_KEYS and isinstance(item, Mapping) and "enum" in item:
                    found.setdefault(key, set()).update(m for m in item["enum"] if isinstance(m, str))
                walk(item)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(document)
    return found


def violations(document: Any) -> list[str]:
    return sorted(
        f"{key}={member}"
        for key, members in refusal_enums(document).items()
        for member in members
        if forbidden(member)
    )


def literal_members(module_path: Path) -> dict[str, set[str]]:
    module = importlib.import_module(f"maezo.gateway.{module_path.stem}")
    found = {}
    for name, value in vars(module).items():
        if name.endswith("Reason") and typing.get_origin(value) is typing.Literal:
            found[name] = set(typing.get_args(value))
    return found


# The exact current C6-4 refusal vocabulary ("o conjunto é exatamente o atual").
C64_REFUSAL_VOCABULARY = {
    "outcome": {
        "AUDIT_UNAVAILABLE", "AUTHORITY_UNPROVEN", "CONTRACT_MISMATCH", "PURPOSE_DENIED",
        "SOURCE_UNAVAILABLE", "STALE_REVISION", "UNKNOWN_OPERATION", "accepted_commitment_mismatch",
        "accepted_commitment_missing", "authority_unproven", "duplicate_submission",
        "offer_version_mismatch", "session_absent", "session_altered", "session_expired",
        "session_revoked", "source_unavailable", "stale_revision", "terms_hash_mismatch",
        "vendor_seat_refused", "wrong_principal", "wrong_purpose", "wrong_tenant",
    },
    "reason": {
        "not_applicable", "refresh_required", "revoked", "source_unavailable", "unqualified", "unsupported",
    },
    "refusalReason": {
        "AUDIT_UNAVAILABLE", "AUTHORITY_UNPROVEN", "CONTRACT_MISMATCH", "PURPOSE_DENIED",
        "SOURCE_UNAVAILABLE", "STALE_REVISION", "UNKNOWN_OPERATION",
    },
    "unknownReason": {
        "absent_generation", "identity_unproven", "purpose_denied", "source_unavailable", "stale_generation",
    },
}  # fmt: skip


def snapshot_with(arm_key: str, arm: dict[str, Any]) -> bytes:
    value = journey()
    value[arm_key] = arm
    return encode(outcome(snapshot_result(value)))


@pytest.mark.parametrize("injected", INJECTED)
@pytest.mark.parametrize("arm_key", ["acceptance", "enrollment", "support"])
def test_refusal_vocabulary_closed_and_non_clinical(schema_bytes, injected, arm_key):
    # Exact set: the pinned contract's refusal vocabulary is the current one (reason_code
    # pinned by size: it is the wire's operational vocabulary, already c6-pinned).
    enums = refusal_enums(json.loads(schema_bytes))
    assert {key: members for key, members in enums.items() if key != "reason_code"} == C64_REFUSAL_VOCABULARY
    assert len(enums["reason_code"]) == 54
    assert not violations(json.loads(schema_bytes))
    # Injected violation: the source returns a clinical/age refusal outcome.
    codec = CommercialWire(schema_bytes, delivery_origins=DeliveryOrigins())
    command = codec.parse_command(encode(journey_request()))
    clean = journey()[arm_key]
    assert codec.parse_result(snapshot_with(arm_key, clean), command).status == "succeeded"
    tampered = dict(clean, outcome=injected)
    with pytest.raises(CommercialRefusalError) as err:
        codec.parse_result(snapshot_with(arm_key, tampered), command)
    # Typed, closed refusal: the source result is unusable, nothing of it is disclosed.
    assert (err.value.status, err.value.reason_code) == ("unavailable", "source_unavailable")
    # …and as an extra key next to a valid outcome (unknown keys rejected recursively).
    with pytest.raises(CommercialRefusalError) as extra:
        codec.parse_result(snapshot_with(arm_key, dict(clean, **{injected: True})), command)
    assert extra.value.reason_code == "source_unavailable"


@pytest.mark.parametrize("contract", CONTRACTS, ids=lambda path: path.name)
def test_refusal_enums_have_no_health_age_disability_tokens(contract):
    document = json.loads(contract.read_text(encoding="utf-8"))
    assert not violations(document)
    # Injected violation: `health_condition_declined` added to a refusal enum → lint fails.
    tampered = deepcopy(document)
    target = _enum_nodes(tampered)[0]
    target["enum"].append("health_condition_declined")
    assert "health_condition_declined" in " ".join(violations(tampered))
    for member in ("fraud_suspected", "age_limit", "disability_unsupported", "preexisting_condition"):
        assert forbidden(member), member
    for member in ("stage_invalid", "page_expired", "source_unavailable", "wrong_purpose"):
        assert not forbidden(member), member


def _enum_nodes(document: Any) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, item in node.items():
                if key in REFUSAL_ENUM_KEYS and isinstance(item, dict) and isinstance(item.get("enum"), list):
                    nodes.append(item)
                walk(item)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(document)
    assert nodes, "no refusal enum found; re-anchor this guard"
    return nodes


def test_admission_refusal_literals_have_no_health_age_disability_tokens():
    # The python side of the same lint: every `*Reason` Literal of the commercial admissions.
    literals = {path.stem: literal_members(path) for path in ADMISSION_MODULES}
    members = {member for found in literals.values() for values in found.values() for member in values}
    assert members, "no refusal Literal found; re-anchor this guard"
    assert not sorted(member for member in members if forbidden(member))


def refused_arm_slots(document: Any) -> list[str]:
    """Every property of a `status: refused` arm beyond the bounded technical outcome."""
    hits: list[str] = []

    def walk(node: Any, path: str) -> None:
        if isinstance(node, Mapping):
            properties = node.get("properties")
            if isinstance(properties, Mapping) and properties.get("status", {}).get("enum") == ["refused"]:
                hits.extend(
                    f"{path}.{name}" for name in set(properties) - {"schemaVersion", "status", "outcome"}
                )
            for key, item in node.items():
                walk(item, f"{path}/{key}")
        elif isinstance(node, list):
            for item in node:
                walk(item, path)

    walk(document["$defs"], "$defs")
    return sorted(hits)


def test_refusal_reason_requires_ratified_anchor(schema_bytes):
    # Closed state until RL-REG-29 (motivos com âncora; prazo só se ratificado): a refused
    # arm carries no motive, anchor or prazo slot at the pinned contract…
    document = json.loads(schema_bytes)
    assert not refused_arm_slots(document)
    # …and a successor adding one is caught before it can be disclosed.
    tampered = deepcopy(document)
    arm = tampered["$defs"]["CustomerSupportRead"]["oneOf"][1]
    arm["properties"]["reason_text"] = {"type": "string"}
    arm["properties"]["response_deadline"] = {"type": "string"}
    assert refused_arm_slots(tampered) == [
        "$defs/CustomerSupportRead/oneOf.reason_text",
        "$defs/CustomerSupportRead/oneOf.response_deadline",
    ]
