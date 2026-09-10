"""Synthetic contract mechanics; exact upstream artifact exercised in E02 evidence."""

import asyncio
import hashlib
import json
from dataclasses import replace

import pytest
import yaml

from maezo.adapters.amh import subject_context as sc
from maezo.adapters.amh.contract import load_contract_pin
from maezo.ports.clinical_context import ClinicalContextPort
from maezo.ports.errors import PortFailureReason as Reason
from maezo.ports.errors import PortResult


class Executor(sc.GovernedSubjectContextExecutor):
    """Contract executor double: records only dispatched, registered reads; no network."""

    registered_operations = frozenset(sc.OPERATIONS.values())

    def __init__(self):
        self.calls = []
        self.status = 200
        self.body = {
            "portable_subject_ref": "subject1",
            "purpose_of_use": "purpose1",
            "consent_decision_ref": "consent1",
        }
        self.failure = None
        self.delay = 0

    async def execute(self, request):
        self.calls.append(request)
        await asyncio.sleep(self.delay)
        if self.failure:
            raise self.failure
        return PortResult.ok(sc.GovernedSubjectContextResponse(self.status, json.dumps(self.body).encode()))


@pytest.fixture
def consumer(monkeypatch):
    # A deliberately tiny synthetic contract, not a copied canonical AMH schema.
    schemas = {
        "SubjectContext": {
            "type": "object",
            "additionalProperties": False,
            "required": ["portable_subject_ref", "purpose_of_use", "consent_decision_ref"],
            "properties": {
                k: {"type": "string"}
                for k in ("portable_subject_ref", "purpose_of_use", "consent_decision_ref")
            },
        },
        "CoverageSummary": {
            "type": "object",
            "additionalProperties": False,
            "required": ["status"],
            "properties": {"status": {"type": "string", "enum": ["active", "suspended"]}},
        },
        "EncounterPage": {
            "type": "object",
            "required": ["items"],
            "additionalProperties": False,
            "properties": {
                "items": {"type": "array", "items": {"type": "object", "additionalProperties": False}},
                "next_page_token": {"type": "string", "nullable": True},
            },
        },
    }
    schemas["ConditionPage"] = schemas["EncounterPage"]
    params = {
        "PortableSubjectRef": {
            "name": "portable_subject_ref",
            "in": "path",
            "required": True,
            "schema": {"type": "string", "pattern": "^subject[0-9]+$"},
        },
        "PurposeOfUse": {
            "name": "purpose_of_use",
            "in": "query",
            "required": True,
            "schema": {"type": "string", "enum": ["purpose1"]},
        },
        "Limit": {
            "name": "limit",
            "in": "query",
            "schema": {"type": "integer", "minimum": 1, "maximum": 200},
        },
        "PageToken": {"name": "page_token", "in": "query", "schema": {"type": "string"}},
    }
    paths = {}
    for method, (suffix, schema) in sc.READS.items():
        refs = ["PortableSubjectRef", "PurposeOfUse"] + (["Limit", "PageToken"] if "list" in method else [])
        paths["/subjects/{portable_subject_ref}/context" + suffix] = {
            "get": {
                "parameters": [{"$ref": "#/components/parameters/" + p} for p in refs],
                "responses": {
                    "200": {
                        "content": {
                            "application/json": {"schema": {"$ref": "#/components/schemas/" + schema}}
                        }
                    }
                },
            }
        }
    raw = yaml.safe_dump(
        {
            "openapi": "3.0.3",
            "servers": [{"url": "/interop/subject-context/v1"}],
            "paths": paths,
            "components": {"schemas": schemas, "parameters": params},
        }
    ).encode()
    real_pin = load_contract_pin()
    monkeypatch.setattr(
        sc,
        "load_contract_pin",
        lambda path=None: replace(real_pin, artifact_digests={sc.ARTIFACT: hashlib.sha256(raw).hexdigest()}),
    )
    executor = Executor()
    return sc.AmhSubjectContextAdapter(raw, executor=executor), executor, raw


async def test_context_is_bound_and_consent_is_not_added_to_wire_query(consumer):
    adapter, executor, _ = consumer
    assert isinstance(adapter, ClinicalContextPort)
    result = await adapter.get_subject_context(
        "subject1", purpose_of_use="purpose1", consent_decision_ref="consent1"
    )
    assert result.succeeded
    request = executor.calls[0]
    assert request.operation == "amh.get_subject_context"
    assert request.path == "/interop/subject-context/v1/subjects/subject1/context"
    assert dict(request.query) == {"purpose_of_use": "purpose1"}
    assert request.consent_decision_ref == "consent1"
    assert "subject1" not in repr(request)


@pytest.mark.parametrize(
    "field,value",
    [("portable_subject_ref", "subject2"), ("purpose_of_use", "other"), ("consent_decision_ref", "other")],
)
async def test_mismatched_context_refuses_without_leaking(consumer, field, value):
    adapter, executor, _ = consumer
    executor.body[field] = value
    result = await adapter.get_subject_context(
        "subject1", purpose_of_use="purpose1", consent_decision_ref="consent1"
    )
    assert not result.succeeded
    assert result.failure.detail is None


@pytest.mark.parametrize("method", ["list_subject_encounters", "list_subject_conditions"])
async def test_pages_preserve_continuation_and_empty_page(consumer, method):
    adapter, executor, _ = consumer
    executor.body = {"items": [], "next_page_token": "opaque_next"}
    result = await getattr(adapter, method)(
        "subject1",
        purpose_of_use="purpose1",
        consent_decision_ref="consent1",
        limit=1,
        page_token="opaque_previous",
    )
    assert result.value.items == ()
    assert result.value.next_page_token == "opaque_next"
    assert dict(executor.calls[0].query)["page_token"] == "opaque_previous"


async def test_coverage_remains_fact_not_authority(consumer):
    adapter, executor, _ = consumer
    executor.body = {"status": "active"}
    result = await adapter.get_subject_coverage(
        "subject1", purpose_of_use="purpose1", consent_decision_ref="consent1"
    )
    assert dict(result.value.attributes) == {"status": "active"}


@pytest.mark.parametrize(
    "kwargs",
    [
        {"purpose_of_use": "wrong"},
        {"consent_decision_ref": ""},
        {"limit": True},
        {"limit": 201},
        {"timeout_seconds": 0},
        {"timeout_seconds": float("nan")},
    ],
)
async def test_invalid_request_never_dispatches(consumer, kwargs):
    adapter, executor, _ = consumer
    args = {"purpose_of_use": "purpose1", "consent_decision_ref": "consent1", **kwargs}
    result = await adapter.list_subject_conditions("subject1", **args)
    assert result.failure.reason == Reason.INVALID_REQUEST
    assert not executor.calls


async def test_unregistered_operation_never_dispatches(consumer):
    adapter, executor, _ = consumer
    executor.registered_operations = frozenset()
    result = await adapter.get_subject_context(
        "subject1", purpose_of_use="purpose1", consent_decision_ref="consent1"
    )
    assert result.failure.reason == Reason.SCOPE_NOT_SUPPORTED
    assert not executor.calls


@pytest.mark.parametrize(
    "status,body,reason",
    [
        (401, {}, Reason.NOT_AUTHENTICATED),
        (403, {"reason": "consent_denied"}, Reason.CONSENT_REQUIRED),
        (403, {"reason": "purpose_not_permitted"}, Reason.PURPOSE_DENIED),
        (403, {"reason": "scope_not_supported"}, Reason.SCOPE_NOT_SUPPORTED),
        (403, {"reason": "invented"}, Reason.CONTRACT_VIOLATION),
        (404, {}, Reason.NOT_FOUND),
        (429, {}, Reason.RATE_LIMITED),
        (500, {}, Reason.UPSTREAM_UNAVAILABLE),
        (302, {}, Reason.CONTRACT_VIOLATION),
    ],
)
async def test_refusal_taxonomy(consumer, status, body, reason):
    adapter, executor, _ = consumer
    executor.status, executor.body = status, body
    result = await adapter.get_subject_context(
        "subject1", purpose_of_use="purpose1", consent_decision_ref="consent1"
    )
    assert result.failure.reason == reason


async def test_raw_extra_fields_and_exceptions_are_not_exposed(consumer):
    adapter, executor, _ = consumer
    executor.body["private_patient_note"] = "secret"
    result = await adapter.get_subject_context(
        "subject1", purpose_of_use="purpose1", consent_decision_ref="consent1"
    )
    assert result.failure.reason == Reason.CONTRACT_VIOLATION
    executor.failure = RuntimeError("secret")
    result = await adapter.get_subject_context(
        "subject1", purpose_of_use="purpose1", consent_decision_ref="consent1"
    )
    assert result.failure.reason == Reason.UPSTREAM_UNAVAILABLE
    assert "secret" not in repr(result)


async def test_timeout_and_cancellation(consumer):
    adapter, executor, _ = consumer
    executor.delay = 1
    result = await adapter.get_subject_context(
        "subject1", purpose_of_use="purpose1", consent_decision_ref="consent1", timeout_seconds=0.001
    )
    assert result.failure.reason == Reason.TIMEOUT
    executor.delay = 0
    executor.failure = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await adapter.get_subject_context(
            "subject1", purpose_of_use="purpose1", consent_decision_ref="consent1"
        )


def test_tampered_contract_refuses_construction(consumer):
    _, executor, raw = consumer
    with pytest.raises(sc.SubjectContextContractError, match="subject_context_contract_invalid"):
        sc.AmhSubjectContextAdapter(raw + b"\n", executor=executor)


async def test_governed_executor_refusal_is_not_retried_or_echoed(consumer):
    adapter, executor, _ = consumer

    async def refuse(request):
        executor.calls.append(request)
        return PortResult.refused(Reason.PURPOSE_DENIED, detail="private upstream detail")

    executor.execute = refuse
    result = await adapter.get_subject_context(
        "subject1", purpose_of_use="purpose1", consent_decision_ref="consent1"
    )
    assert len(executor.calls) == 1
    assert result.failure.reason == Reason.PURPOSE_DENIED
    assert result.failure.detail is None


@pytest.mark.parametrize(
    "body",
    [
        b'{"status":"active","status":"suspended"}',
        b'{"status":NaN}',
        b"[1]",
        b"null",
        b"bad",
        b"x" * (sc._MAX_BYTES + 1),
    ],
)
async def test_malformed_duplicate_or_oversize_wire_refuses(consumer, body):
    adapter, executor, _ = consumer

    async def respond(request):
        return PortResult.ok(sc.GovernedSubjectContextResponse(200, body))

    executor.execute = respond
    result = await adapter.get_subject_coverage(
        "subject1", purpose_of_use="purpose1", consent_decision_ref="consent1"
    )
    assert result.failure.reason == Reason.CONTRACT_VIOLATION


@pytest.mark.parametrize(
    "value",
    ["2026-09-10", "2026-09-10T10:30", "2026-09-10T10:30:00", "2026-02-30T10:30:00Z", "20260910T10:30:00Z"],
)
def test_strict_aware_wire_timestamp(value):
    assert not sc._FORMATS.conforms(value, "date-time")


def test_valid_aware_wire_timestamp():
    assert sc._FORMATS.conforms("2026-09-10T10:30:00.000001Z", "date-time")


async def test_oversize_page_and_mutable_registration_refuse(consumer):
    adapter, executor, _ = consumer
    executor.body = {"items": [{}, {}], "next_page_token": None}
    result = await adapter.list_subject_conditions(
        "subject1", purpose_of_use="purpose1", consent_decision_ref="consent1", limit=1
    )
    assert result.failure.reason == Reason.CONTRACT_VIOLATION
    executor.calls.clear()
    executor.registered_operations = set(sc.OPERATIONS.values())
    result = await adapter.get_subject_context(
        "subject1", purpose_of_use="purpose1", consent_decision_ref="consent1"
    )
    assert result.failure.reason == Reason.SCOPE_NOT_SUPPORTED
    assert not executor.calls
