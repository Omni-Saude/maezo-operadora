"""Pinned AMH factual reads through a gateway-owned executor (ADR-0037 XRD-04/06/09).

No HTTP client, host, credential or policy is created here. The executor must be
installed by the gateway, enforce each exact operation for its bound principal,
record the consent anchor, and return only its own completed request's response.
Registration is necessary, never proof of authorization. No production executor
is installed by this module; missing registration refuses before dispatch.

The OpenAPI bytes are a deployment artifact checked against the existing immutable
pin, not an editable schema shipped in Maezo. Summary shape proves neither human
representation nor source currentness. Coverage/individual pages lack the context
response's consent/subject anchor; do not promote them to authorization evidence.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any
from urllib.parse import quote

import yaml
from jsonschema import Draft4Validator, FormatChecker

from maezo.adapters.amh.contract import load_contract_pin
from maezo.ports.clinical_context import CodedSummary, SummaryPage
from maezo.ports.errors import DEFAULT_PORT_TIMEOUT_SECONDS, PortFailure, PortFailureReason, PortResult

ARTIFACT = "schemas/openapi/maezo/v1/subject-context.openapi.yaml"
READS = MappingProxyType(
    {
        "get_subject_context": ("", "SubjectContext"),
        "list_subject_encounters": ("/encounters", "EncounterPage"),
        "list_subject_conditions": ("/conditions", "ConditionPage"),
        "get_subject_coverage": ("/coverage", "CoverageSummary"),
    }
)
OPERATIONS = MappingProxyType({name: "amh." + name for name in READS})
_BASE = "/subjects/{portable_subject_ref}/context"
_MAX_BYTES = 1_048_576  # Technical parsing bound, not a clinical/retention policy.
Reason = PortFailureReason


class SubjectContextContractError(ValueError):
    def __init__(self) -> None:
        super().__init__("subject_context_contract_invalid")


@dataclass(frozen=True, slots=True, repr=False)
class GovernedSubjectContextRequest:
    """Server-only descriptor. Consent ref is audit metadata, never a wire query."""

    operation: str
    path: str
    query: tuple[tuple[str, str | int], ...]
    consent_decision_ref: str
    timeout_seconds: float
    method: str = field(default="GET", init=False)


@dataclass(frozen=True, slots=True, repr=False)
class GovernedSubjectContextResponse:
    status_code: int
    body: bytes


class GovernedSubjectContextExecutor(ABC):
    """Gateway composition port, not an arbitrary HTTP-client callback.

    Runtime registration must bind tenant/legal entity, workload, principal/tool
    capability, purpose/consent, PHI zone, audit and credential controls. Execute
    must reject unauthorized or stale state per call; a registered operation is
    not itself allowed. Responses and exceptions must not be logged with PHI.
    Never follow redirects, accept browser-supplied destinations, or return a
    response cached under a different subject/purpose. No default implementation.
    """

    registered_operations: frozenset[str] = frozenset()

    @abstractmethod
    async def execute(
        self, request: GovernedSubjectContextRequest
    ) -> PortResult[GovernedSubjectContextResponse]: ...


def _json(raw: bytes) -> Any:
    if type(raw) is not bytes or len(raw) > _MAX_BYTES:
        raise ValueError

    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError
            result[key] = value
        return result

    def invalid(value: str) -> None:
        raise ValueError

    return json.loads(raw, object_pairs_hook=unique, parse_constant=invalid)


def _json_schema(value: Any) -> Any:
    """Mechanical OpenAPI 3.0 nullable/ref conversion; no copied field catalogue."""
    if isinstance(value, list):
        return [_json_schema(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {key: _json_schema(item) for key, item in value.items() if key != "nullable"}
    if "$ref" in result:
        ref = result["$ref"]
        if not isinstance(ref, str) or not ref.startswith("#/components/schemas/"):
            raise ValueError
        result["$ref"] = ref.replace("#/components/schemas/", "#/definitions/", 1)
    if value.get("nullable"):
        result = {"anyOf": [result, {"type": "null"}]}
    return result


_FORMATS = FormatChecker()


@_FORMATS.checks("date-time", raises=ValueError)
def _aware_timestamp(value: Any) -> bool:
    if not isinstance(value, str):
        return True
    if not re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}[Tt][0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?(?:[Zz]|[+-][0-9]{2}:[0-9]{2})",
        value,
    ):
        return False
    parsed = datetime.fromisoformat(value.upper().replace("Z", "+00:00"))
    return parsed.tzinfo is not None and parsed.utcoffset() is not None


@_FORMATS.checks("date", raises=ValueError)
def _date(value: Any) -> bool:
    return not isinstance(value, str) or date.fromisoformat(value).isoformat() == value


def _freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


class AmhSubjectContextAdapter:
    """Concrete ClinicalContextPort, with governed IO delegated at every call."""

    def __init__(
        self,
        openapi_bytes: bytes,
        *,
        executor: GovernedSubjectContextExecutor,
        pin_path: Path | None = None,
    ) -> None:
        try:
            if not isinstance(executor, GovernedSubjectContextExecutor):
                raise ValueError
            pin = load_contract_pin(pin_path)
            if (
                len(openapi_bytes) > _MAX_BYTES
                or hashlib.sha256(openapi_bytes).hexdigest() != pin.artifact_digests[ARTIFACT]
            ):
                raise ValueError
            document = yaml.safe_load(openapi_bytes)
            if (
                document["openapi"] != "3.0.3"
                or document["servers"][0]["url"] != "/interop/subject-context/v1"
            ):
                raise ValueError
            definitions = _json_schema(document["components"]["schemas"])
            self._validators = {}
            self._parameters = {}
            for method, (suffix, schema_name) in READS.items():
                operation = document["paths"][_BASE + suffix]["get"]
                if operation["responses"]["200"]["content"]["application/json"]["schema"] != {
                    "$ref": "#/components/schemas/" + schema_name
                }:
                    raise ValueError
                schema = {"$ref": "#/definitions/" + schema_name, "definitions": definitions}
                Draft4Validator.check_schema(schema)
                self._validators[method] = Draft4Validator(schema, format_checker=_FORMATS)
                parameters = []
                for ref in operation["parameters"]:
                    path = ref["$ref"]
                    if not path.startswith("#/components/parameters/"):
                        raise ValueError
                    parameters.append(document["components"]["parameters"][path.rsplit("/", 1)[1]])
                self._parameters[method] = parameters
            self._executor = executor
        except Exception:
            raise SubjectContextContractError() from None

    async def _read(
        self,
        method: str,
        subject: str,
        purpose: str,
        consent: str,
        timeout_seconds: float,
        limit: int | None = None,
        page_token: str | None = None,
    ) -> PortResult[Any]:
        try:
            if (
                not isinstance(consent, str)
                or not consent.strip()
                or type(timeout_seconds) not in (int, float)
                or not math.isfinite(timeout_seconds)
                or timeout_seconds <= 0
            ):
                return PortResult.refused(Reason.INVALID_REQUEST)
            values = {
                "portable_subject_ref": subject,
                "purpose_of_use": purpose,
                "limit": limit,
                "page_token": page_token,
            }
            query: list[tuple[str, str | int]] = []
            effective_limit = limit
            for parameter in self._parameters[method]:
                value = values[parameter["name"]]
                if parameter["name"] == "limit" and value is None:
                    # Omitted wire limit still has the pinned contract's maximum
                    # page size; do not invent a local pagination policy.
                    effective_limit = parameter["schema"].get("default")
                if value is None and not parameter.get("required", False):
                    continue
                validator = Draft4Validator(_json_schema(parameter["schema"]), format_checker=_FORMATS)
                if not validator.is_valid(value):
                    return PortResult.refused(Reason.INVALID_REQUEST)
                if parameter["in"] == "query":
                    if not isinstance(value, (str, int)):
                        return PortResult.refused(Reason.INVALID_REQUEST)
                    query.append((parameter["name"], value))
            operation = OPERATIONS[method]
            if (
                type(self._executor.registered_operations) is not frozenset
                or operation not in self._executor.registered_operations
            ):
                return PortResult.refused(Reason.SCOPE_NOT_SUPPORTED)
            request = GovernedSubjectContextRequest(
                operation=operation,
                path="/interop/subject-context/v1"
                + _BASE.replace("{portable_subject_ref}", quote(subject, safe=""))
                + READS[method][0],
                query=tuple(query),
                consent_decision_ref=consent,
                timeout_seconds=timeout_seconds,
            )
            async with asyncio.timeout(timeout_seconds):
                response = await self._executor.execute(request)
            if not isinstance(response, PortResult) or type(response.succeeded) is not bool:
                return PortResult.refused(Reason.CONTRACT_VIOLATION)
            if not response.succeeded:
                failure = response.failure
                if not isinstance(failure, PortFailure):
                    return PortResult.refused(Reason.CONTRACT_VIOLATION)
                reason = failure.reason
                if not isinstance(reason, Reason):
                    return PortResult.refused(Reason.CONTRACT_VIOLATION)
                # A foreign executor's detail is not trusted as PHI-safe.
                return PortResult.refused(reason)
        except TimeoutError:
            return PortResult.refused(Reason.TIMEOUT)
        except Exception:
            return PortResult.refused(Reason.UPSTREAM_UNAVAILABLE)
        try:
            received = response.value
            if (
                not isinstance(received, GovernedSubjectContextResponse)
                or type(received.status_code) is not int
            ):
                return PortResult.refused(Reason.CONTRACT_VIOLATION)
            status = received.status_code
            if status == 403:
                body = _json(received.body)
                reason = {
                    "consent_denied": Reason.CONSENT_REQUIRED,
                    "purpose_not_permitted": Reason.PURPOSE_DENIED,
                    "scope_not_supported": Reason.SCOPE_NOT_SUPPORTED,
                }.get(body.get("reason"), Reason.CONTRACT_VIOLATION)
                return PortResult.refused(reason)
            if status != 200:
                reason = {
                    400: Reason.INVALID_REQUEST,
                    401: Reason.NOT_AUTHENTICATED,
                    404: Reason.NOT_FOUND,
                    429: Reason.RATE_LIMITED,
                    500: Reason.UPSTREAM_UNAVAILABLE,
                }.get(status, Reason.CONTRACT_VIOLATION)
                return PortResult.refused(reason)
            body = _json(received.body)
            if not self._validators[method].is_valid(body):
                return PortResult.refused(Reason.CONTRACT_VIOLATION)
            if method == "get_subject_context":
                if body["portable_subject_ref"] != subject or body["purpose_of_use"] != purpose:
                    return PortResult.refused(Reason.CONTRACT_VIOLATION)
                if body["consent_decision_ref"] != consent:
                    return PortResult.refused(Reason.CONSENT_REQUIRED)
            if (
                method == "get_subject_coverage"
                and body.get("beneficiary_ref") is not None
                and body["source_product"] != "tasy_healthcare_plan"
            ):
                # Canonical CoverageSummary prose scopes this optional lineage
                # to Healthcare Plan; its nullable JSON shape alone cannot.
                return PortResult.refused(Reason.CONTRACT_VIOLATION)
            if method.startswith("list_"):
                if type(effective_limit) is not int or len(body["items"]) > effective_limit:
                    return PortResult.refused(Reason.CONTRACT_VIOLATION)
                return PortResult.ok(
                    SummaryPage(
                        items=tuple(CodedSummary(attributes=_freeze(item)) for item in body["items"]),
                        next_page_token=body.get("next_page_token"),
                    )
                )
            return PortResult.ok(CodedSummary(attributes=_freeze(body)))
        except Exception:
            return PortResult.refused(Reason.CONTRACT_VIOLATION)

    async def get_subject_context(
        self,
        portable_subject_ref: str,
        *,
        purpose_of_use: str,
        consent_decision_ref: str,
        timeout_seconds: float = DEFAULT_PORT_TIMEOUT_SECONDS,
    ) -> PortResult[CodedSummary]:
        return await self._read(
            "get_subject_context", portable_subject_ref, purpose_of_use, consent_decision_ref, timeout_seconds
        )

    async def get_subject_coverage(
        self,
        portable_subject_ref: str,
        *,
        purpose_of_use: str,
        consent_decision_ref: str,
        timeout_seconds: float = DEFAULT_PORT_TIMEOUT_SECONDS,
    ) -> PortResult[CodedSummary]:
        return await self._read(
            "get_subject_coverage",
            portable_subject_ref,
            purpose_of_use,
            consent_decision_ref,
            timeout_seconds,
        )

    async def list_subject_encounters(
        self,
        portable_subject_ref: str,
        *,
        purpose_of_use: str,
        consent_decision_ref: str,
        limit: int | None = None,
        page_token: str | None = None,
        timeout_seconds: float = DEFAULT_PORT_TIMEOUT_SECONDS,
    ) -> PortResult[SummaryPage]:
        return await self._read(
            "list_subject_encounters",
            portable_subject_ref,
            purpose_of_use,
            consent_decision_ref,
            timeout_seconds,
            limit,
            page_token,
        )

    async def list_subject_conditions(
        self,
        portable_subject_ref: str,
        *,
        purpose_of_use: str,
        consent_decision_ref: str,
        limit: int | None = None,
        page_token: str | None = None,
        timeout_seconds: float = DEFAULT_PORT_TIMEOUT_SECONDS,
    ) -> PortResult[SummaryPage]:
        return await self._read(
            "list_subject_conditions",
            portable_subject_ref,
            purpose_of_use,
            consent_decision_ref,
            timeout_seconds,
            limit,
            page_token,
        )
