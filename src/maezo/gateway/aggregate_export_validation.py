"""Pure proposal diagnostics, never owner/source/privacy or numerical admission.

Consumes the exact canonical measurement v2 shapes through a pinned wheel asset.
No source fetching, payload output, logging, effects or numerical release API.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from functools import lru_cache
from importlib import resources
from typing import Any, Final, Literal, cast

from jsonschema import Draft202012Validator, validators
from jsonschema.exceptions import SchemaError, ValidationError

ProposedRecordKind = Literal[
    "provider_private_computation_row",
    "metric_definition_pending_admission",
    "metric_component_mapping_pending_admission",
    "minimized_aggregate_export",
]
_KINDS: Final[tuple[ProposedRecordKind, ...]] = (
    "provider_private_computation_row",
    "metric_definition_pending_admission",
    "metric_component_mapping_pending_admission",
    "minimized_aggregate_export",
)
_SCHEMA_RESOURCE: Final[str] = "measurement-export-closed-shapes.proposed-v2.json"
_SCHEMA_PIN: Final[str] = "9f7063d5e3a633e22459be25e6c61ce64e07eaff92ccb7259deaf06bc577313c"
_MAX_BYTES: Final[int] = 1048576
_MAX_DEPTH: Final[int] = 32
_MAX_NUMBER_DIGITS: Final[int] = 1024
_SURROGATE: Final[re.Pattern[str]] = re.compile("[\ud800-\udfff]")


class AggregateValidationRefusal(StrEnum):
    INVALID_RECORD_KIND = "INVALID_RECORD_KIND"
    INVALID_JSON_INPUT = "INVALID_JSON_INPUT"
    MALFORMED_JSON = "MALFORMED_JSON"
    RESOURCE_LIMIT = "RESOURCE_LIMIT"
    SCHEMA_ASSET_UNAVAILABLE = "SCHEMA_ASSET_UNAVAILABLE"
    SCHEMA_DIGEST_MISMATCH = "SCHEMA_DIGEST_MISMATCH"
    SCHEMA_DEFINITION_INVALID = "SCHEMA_DEFINITION_INVALID"
    SCHEMA_MISMATCH = "SCHEMA_MISMATCH"
    LOCAL_CONSTRAINT_CONFLICT = "LOCAL_CONSTRAINT_CONFLICT"
    VALIDATOR_UNAVAILABLE = "VALIDATOR_UNAVAILABLE"


class ValidationTechnicalStatus(StrEnum):
    CHECKED_UNADMITTED = "CHECKED_UNADMITTED"
    REFUSED = "REFUSED"


class ConstraintStatus(StrEnum):
    SATISFIED = "SATISFIED"
    CONFLICT = "CONFLICT"
    NOT_ASSESSABLE = "NOT_ASSESSABLE"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class ConstraintCode(StrEnum):
    SCHEMA_STRUCTURE = "SCHEMA_STRUCTURE"
    DECLARED_SUPPRESSION_NULL = "DECLARED_SUPPRESSION_NULL"
    DECLARED_LIFECYCLE_NULL = "DECLARED_LIFECYCLE_NULL"
    DECLARED_K_FLOOR = "DECLARED_K_FLOOR"
    DECLARED_HEAD_EQUALITY = "DECLARED_HEAD_EQUALITY"
    DECLARED_SUPERSESSION_NOT_SELF = "DECLARED_SUPERSESSION_NOT_SELF"
    M10_CURRENT_COMMIT_PARTITION = "M10_CURRENT_COMMIT_PARTITION"
    M10_WITHDRAWN_COMMIT_PARTITION = "M10_WITHDRAWN_COMMIT_PARTITION"
    M10_CANDIDATE_PARTITION = "M10_CANDIDATE_PARTITION"
    M10_NUMERATOR_BINDING = "M10_NUMERATOR_BINDING"
    M10_DENOMINATOR_BINDING = "M10_DENOMINATOR_BINDING"
    M10_RATIO_REPRESENTATION = "M10_RATIO_REPRESENTATION"


@dataclass(frozen=True, slots=True)
class ConstraintCheck:
    constraint: ConstraintCode
    status: ConstraintStatus

    def __post_init__(self) -> None:
        if type(self.constraint) is not ConstraintCode or type(self.status) is not ConstraintStatus:
            raise ValueError("aggregate_constraint_contract_mismatch")


@dataclass(frozen=True, slots=True)
class AggregateValidationResult:
    technical_status: ValidationTechnicalStatus
    reason: AggregateValidationRefusal | None
    record_kind: ProposedRecordKind | None
    schema_sha256: str | None
    checks: tuple[ConstraintCheck, ...]
    source_authority_assessed: Literal[False] = field(default=False, init=False)
    source_currentness_assessed: Literal[False] = field(default=False, init=False)
    privacy_release_assessed: Literal[False] = field(default=False, init=False)
    numeric_disclosure_permitted: Literal[False] = field(default=False, init=False)

    def __post_init__(self) -> None:
        if (
            type(self.technical_status) is not ValidationTechnicalStatus
            or (self.reason is not None and type(self.reason) is not AggregateValidationRefusal)
            or (
                self.record_kind is not None
                and (type(self.record_kind) is not str or self.record_kind not in _KINDS)
            )
            or (
                self.schema_sha256 is not None
                and (type(self.schema_sha256) is not str or self.schema_sha256 != _SCHEMA_PIN)
            )
            or type(self.checks) is not tuple
            or any(type(check) is not ConstraintCheck for check in self.checks)
            or (self.technical_status == ValidationTechnicalStatus.REFUSED) != (self.reason is not None)
        ):
            raise ValueError("aggregate_validation_result_contract_mismatch")


class _RefusalError(Exception):
    def __init__(self, reason: AggregateValidationRefusal) -> None:
        self.reason = reason
        super().__init__(reason.value)


def _pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in items:
        if key in result:
            raise _RefusalError(AggregateValidationRefusal.MALFORMED_JSON)
        result[key] = value
    return result


def _constant(_: str) -> Any:
    raise _RefusalError(AggregateValidationRefusal.MALFORMED_JSON)


def _number(token: str) -> int | Decimal:
    # JSON supplies a valid numeral. Derive Decimal's precision/scale lexically
    # before its native exponent range or an expanded integer can be reached.
    mantissa, _, explicit = token.lower().partition("e")
    integer, _, fraction = mantissa.lstrip("-").partition(".")
    precision = len((integer + fraction).lstrip("0")) or 1
    exponent_digits = explicit.lstrip("+-").lstrip("0") or "0"
    # A valid effective exponent can differ from the explicit exponent by the
    # fraction length. This bound permits cancellation without a token cutoff.
    explicit_bound = str(len(fraction) + _MAX_NUMBER_DIGITS)
    if precision > _MAX_NUMBER_DIGITS or (
        len(exponent_digits) > len(explicit_bound)
        or (len(exponent_digits) == len(explicit_bound) and exponent_digits > explicit_bound)
    ):
        raise _RefusalError(AggregateValidationRefusal.RESOURCE_LIMIT)
    exponent = (-1 if explicit.startswith("-") else 1) * int(exponent_digits) - len(fraction)
    if abs(exponent) > _MAX_NUMBER_DIGITS or max(precision + exponent, 0) > _MAX_NUMBER_DIGITS:
        raise _RefusalError(AggregateValidationRefusal.RESOURCE_LIMIT)
    number = Decimal(token)
    return int(token) if "." not in token and "e" not in token.lower() else number


def _tree(value: Any, depth: int = 0) -> None:
    if depth > _MAX_DEPTH:
        raise _RefusalError(AggregateValidationRefusal.RESOURCE_LIMIT)
    if type(value) is dict:
        for key, item in value.items():
            if _SURROGATE.search(key) is not None:
                raise _RefusalError(AggregateValidationRefusal.MALFORMED_JSON)
            _tree(item, depth + 1)
    elif type(value) is list:
        for item in value:
            _tree(item, depth + 1)
    elif type(value) is str and _SURROGATE.search(value) is not None:
        raise _RefusalError(AggregateValidationRefusal.MALFORMED_JSON)


def _decode(raw: bytes) -> Any:
    if len(raw) > _MAX_BYTES:
        raise _RefusalError(AggregateValidationRefusal.RESOURCE_LIMIT)
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_pairs,
            parse_int=_number,
            parse_float=_number,
            parse_constant=_constant,
        )
        _tree(value)
        return value
    except _RefusalError:
        raise
    except RecursionError:
        raise _RefusalError(AggregateValidationRefusal.RESOURCE_LIMIT) from None
    except (UnicodeError, ValueError, ArithmeticError):
        raise _RefusalError(AggregateValidationRefusal.MALFORMED_JSON) from None


def _numeric(_: Any, value: Any) -> bool:
    return type(value) is int or (type(value) is Decimal and value.is_finite())


def _integer(_: Any, value: Any) -> bool:
    return type(value) is int or (
        type(value) is Decimal and value.is_finite() and value == value.to_integral_value()
    )


_extend = cast(Callable[..., Any], validators.extend)
_ExactValidator = _extend(
    Draft202012Validator,
    type_checker=Draft202012Validator.TYPE_CHECKER.redefine("number", _numeric).redefine("integer", _integer),
)


def _references(value: Any) -> None:
    """Only the pinned local-fragment schemas can be resolved; never fetch a URI."""
    if type(value) is dict:
        for key, item in value.items():
            if key == "$ref" and (type(item) is not str or not item.startswith("#/")):
                raise _RefusalError(AggregateValidationRefusal.SCHEMA_DEFINITION_INVALID)
            _references(item)
    elif type(value) is list:
        for item in value:
            _references(item)


def _asset() -> dict[str, Any]:
    try:
        raw = resources.files("maezo.gateway").joinpath(_SCHEMA_RESOURCE).read_bytes()
    except Exception:
        raise _RefusalError(AggregateValidationRefusal.SCHEMA_ASSET_UNAVAILABLE) from None
    if hashlib.sha256(raw).hexdigest() != _SCHEMA_PIN:
        raise _RefusalError(AggregateValidationRefusal.SCHEMA_DIGEST_MISMATCH)
    return _verified_schemas(raw)


@lru_cache(maxsize=1)
def _verified_schemas(raw: bytes) -> dict[str, Any]:
    """Cache only already-pinned schemas; each request rechecks packaged bytes."""
    try:
        # Canonical schemas contain only tiny structural integers, not input data.
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs)
        if type(data) is not dict or any(type(data.get(kind)) is not dict for kind in _KINDS):
            raise _RefusalError(AggregateValidationRefusal.SCHEMA_DEFINITION_INVALID)
        for kind in _KINDS:
            _references(data[kind])
            Draft202012Validator.check_schema(data[kind])
        return data
    except _RefusalError:
        raise
    except Exception:
        raise _RefusalError(AggregateValidationRefusal.SCHEMA_DEFINITION_INVALID) from None


def _relation(code: ConstraintCode, values: list[Any]) -> ConstraintCheck:
    if any(value is None for value in values):
        return ConstraintCheck(code, ConstraintStatus.NOT_ASSESSABLE)
    # The full schema already established exact nonnegative mathematical integers.
    # Convert exact integral Decimals to integers; never infer an absent component.
    exact = [int(value) for value in values]
    return ConstraintCheck(
        code, ConstraintStatus.SATISFIED if exact[0] == sum(exact[1:]) else ConstraintStatus.CONFLICT
    )


def _checks(kind: ProposedRecordKind, value: dict[str, Any]) -> tuple[ConstraintCheck, ...]:
    statuses = {code: ConstraintStatus.NOT_APPLICABLE for code in ConstraintCode}
    statuses[ConstraintCode.SCHEMA_STRUCTURE] = ConstraintStatus.SATISFIED
    if kind != "minimized_aggregate_export":
        return tuple(ConstraintCheck(code, status) for code, status in statuses.items())
    metadata = value["metadata"]
    statuses[ConstraintCode.DECLARED_SUPPRESSION_NULL] = (
        ConstraintStatus.SATISFIED if metadata["suppressed"] else ConstraintStatus.NOT_APPLICABLE
    )
    statuses[ConstraintCode.DECLARED_LIFECYCLE_NULL] = (
        ConstraintStatus.SATISFIED
        if value["export_state"] in {"PROPOSED_UNADMITTED", "SUPERSEDED", "RETRACTED", "WITHHELD"}
        else ConstraintStatus.NOT_APPLICABLE
    )
    statuses[ConstraintCode.DECLARED_K_FLOOR] = (
        ConstraintStatus.NOT_ASSESSABLE
        if metadata["applied_minimum_k"] is None
        else ConstraintStatus.SATISFIED
    )
    revision, head = metadata["aggregate_revision"], metadata["current_aggregate_head_revision"]
    statuses[ConstraintCode.DECLARED_HEAD_EQUALITY] = (
        ConstraintStatus.NOT_ASSESSABLE
        if revision is None or head is None
        else ConstraintStatus.SATISFIED
        if revision == head
        else ConstraintStatus.CONFLICT
    )
    supersedes = metadata["supersedes_aggregate_revision"]
    statuses[ConstraintCode.DECLARED_SUPERSESSION_NOT_SELF] = (
        ConstraintStatus.NOT_ASSESSABLE
        if supersedes is None or revision is None
        else ConstraintStatus.CONFLICT
        if supersedes == revision
        else ConstraintStatus.SATISFIED
    )
    if value["metric_id"] == "M10":
        c = value["components"]
        relations = (
            (
                ConstraintCode.M10_CURRENT_COMMIT_PARTITION,
                [
                    c["committed_current_n"],
                    c["valid_current_n"],
                    c["missing_current_n"],
                    c["receipt_uncertain_current_n"],
                ],
            ),
            (
                ConstraintCode.M10_WITHDRAWN_COMMIT_PARTITION,
                [
                    c["committed_withdrawn_n"],
                    c["valid_withdrawn_n"],
                    c["missing_withdrawn_n"],
                    c["receipt_uncertain_withdrawn_n"],
                ],
            ),
            (
                ConstraintCode.M10_CANDIDATE_PARTITION,
                [
                    c["candidate_total_n"],
                    c["committed_current_n"],
                    c["committed_withdrawn_n"],
                    c["commit_unknown_current_n"],
                    c["commit_unknown_withdrawn_n"],
                    c["not_committed_current_n"],
                    c["not_committed_withdrawn_n"],
                    c["eligibility_unknown_n"],
                ],
            ),
            (ConstraintCode.M10_NUMERATOR_BINDING, [value["numerator"], c["valid_current_n"]]),
            (ConstraintCode.M10_DENOMINATOR_BINDING, [value["denominator"], c["committed_current_n"]]),
        )
        for code, operands in relations:
            statuses[code] = _relation(code, operands).status
        statuses[ConstraintCode.M10_RATIO_REPRESENTATION] = (
            ConstraintStatus.NOT_ASSESSABLE if value["ratio"] is not None else ConstraintStatus.NOT_APPLICABLE
        )
    return tuple(ConstraintCheck(code, status) for code, status in statuses.items())


def validate_proposed_record(record_kind: ProposedRecordKind, raw_json: bytes) -> AggregateValidationResult:
    """Check local consistency only. All authority/disclosure fields stay False."""
    kind: ProposedRecordKind | None = None
    pin: str | None = None
    checks: tuple[ConstraintCheck, ...] = ()
    try:
        if type(record_kind) is not str or record_kind not in _KINDS:
            raise _RefusalError(AggregateValidationRefusal.INVALID_RECORD_KIND)
        kind = record_kind
        if type(raw_json) is not bytes:
            raise _RefusalError(AggregateValidationRefusal.INVALID_JSON_INPUT)
        value = _decode(raw_json)
        schemas = _asset()
        pin = _SCHEMA_PIN
        try:
            _ExactValidator(schemas[kind]).validate(value)
        except (ValidationError, SchemaError):
            raise _RefusalError(AggregateValidationRefusal.SCHEMA_MISMATCH) from None
        checks = _checks(kind, value)
        if any(check.status == ConstraintStatus.CONFLICT for check in checks):
            raise _RefusalError(AggregateValidationRefusal.LOCAL_CONSTRAINT_CONFLICT)
        return AggregateValidationResult(
            ValidationTechnicalStatus.CHECKED_UNADMITTED, None, kind, pin, checks
        )
    except _RefusalError as exc:
        return AggregateValidationResult(ValidationTechnicalStatus.REFUSED, exc.reason, kind, pin, checks)
    except Exception:
        return AggregateValidationResult(
            ValidationTechnicalStatus.REFUSED,
            AggregateValidationRefusal.VALIDATOR_UNAVAILABLE,
            kind,
            pin,
            checks,
        )
