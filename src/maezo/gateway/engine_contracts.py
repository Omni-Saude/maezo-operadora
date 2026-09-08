"""D7-A operation contracts, independent of CIB's HTTP/authentication implementation.

ADR-0049 D4/D7: a profile is trusted deployment configuration, never request data.
The authority port must resolve authenticated identity and engine-owned resources; constructing
these value objects does not authenticate anybody. B must enforce the same profile at the
engine, and C must supply the port through gateway-owned credential composition. Neither is
implemented here. Unknown or unavailable authority raises; it never means an absent instance.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol


class EngineRefusalCode(StrEnum):
    PROFILE_UNAVAILABLE = "engine_profile_unavailable"
    IDENTITY_UNAVAILABLE = "engine_identity_unavailable"
    IDENTITY_MISMATCH = "engine_identity_mismatch"
    RESOURCE_MISMATCH = "engine_resource_mismatch"
    OPERATION_DENIED = "engine_operation_denied"
    INVALID_BODY = "engine_invalid_body"
    FIELD_DENIED = "engine_field_denied"
    EVIDENCE_UNAVAILABLE = "engine_evidence_unavailable"


class EngineCapabilityError(RuntimeError):
    """Safe, bounded refusal: no body, identifiers, credential or provider exception text."""

    def __init__(self, code: EngineRefusalCode) -> None:
        self.code = code
        super().__init__(code.value)


class EngineOperation(StrEnum):
    START = "start"
    CORRELATE = "correlate"
    READ_ACTIVE = "read_active"
    READ_HISTORY = "read_history"
    READ_STATUS = "read_status"
    FETCH_LOCK = "fetch_lock"
    COMPLETE = "external_complete"
    BPMN_ERROR = "external_bpmn_error"
    FAILURE = "external_failure"
    EXTEND_LOCK = "external_extend_lock"
    UNLOCK = "external_unlock"
    DMN_METADATA = "dmn_metadata"
    DMN_EVALUATE = "dmn_evaluate"


_LOCKED_OPERATIONS = frozenset(
    {
        EngineOperation.COMPLETE,
        EngineOperation.BPMN_ERROR,
        EngineOperation.FAILURE,
        EngineOperation.EXTEND_LOCK,
        EngineOperation.UNLOCK,
    }
)


class IdentityOrigin(StrEnum):
    MTLS = "verified_mtls"


class FieldOrigin(StrEnum):
    FACT = "caller_fact"
    BOUND = "deployment_bound"
    PRIOR_HUMAN = "prior_human_evidence"
    ENGINE = "engine_fact"
    FIXED_NULL = "fixed_null"


class ValueKind(StrEnum):
    STRING = "String"
    BOOLEAN = "Boolean"
    INTEGER = "Integer"
    DOUBLE = "Double"
    JSON = "Json"


def _token(value: object) -> None:
    if type(value) is not str or not value or len(value) > 256:
        raise EngineCapabilityError(EngineRefusalCode.INVALID_BODY)
    if "*" in value or any(ord(c) < 33 or ord(c) > 126 for c in value):
        raise EngineCapabilityError(EngineRefusalCode.INVALID_BODY)


def _json_value(value: object, *, depth: int = 0) -> None:
    if depth > 16:
        raise EngineCapabilityError(EngineRefusalCode.INVALID_BODY)
    if value is None or type(value) in (str, bool):
        return
    if type(value) is int and -(2**63) <= value < 2**63:
        return
    if type(value) is float and math.isfinite(value):
        return
    if type(value) is list:
        for item in value:
            _json_value(item, depth=depth + 1)
        return
    if type(value) is dict and all(type(k) is str for k in value):
        for item in value.values():
            _json_value(item, depth=depth + 1)
        return
    raise EngineCapabilityError(EngineRefusalCode.INVALID_BODY)


def canonical_json(value: object) -> bytes:
    """Bounded JSON, without coercion, NaN, duplicate keys or engine serialization escapes.

    This is D7 policy JSON, not the distinct D3 human-command canonicalization/signature profile.
    The byte/depth bounds are transport bounds, not financial or clinical business rules.
    """
    _json_value(value)
    try:
        result = json.dumps(
            value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    except (ValueError, UnicodeError, RecursionError):
        raise EngineCapabilityError(EngineRefusalCode.INVALID_BODY) from None
    if len(result) > 1_048_576:
        raise EngineCapabilityError(EngineRefusalCode.INVALID_BODY)
    return result


def parse_json(body: bytes) -> dict[str, Any]:
    """Strict request/response object parser for the future B/C adapter."""

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise EngineCapabilityError(EngineRefusalCode.INVALID_BODY)
            result[key] = value
        return result

    if type(body) is not bytes or len(body) > 1_048_576:
        raise EngineCapabilityError(EngineRefusalCode.INVALID_BODY)
    try:
        result = json.loads(body.decode("utf-8"), object_pairs_hook=pairs)
        if type(result) is not dict:
            raise EngineCapabilityError(EngineRefusalCode.INVALID_BODY)
        canonical_json(result)
        return result
    except (ValueError, UnicodeError, RecursionError):
        raise EngineCapabilityError(EngineRefusalCode.INVALID_BODY) from None


@dataclass(frozen=True, slots=True)
class EngineIdentity:
    tenant: str
    environment: str
    workload: str
    workload_version: str
    issuer: str
    subject: str
    origin: IdentityOrigin = IdentityOrigin.MTLS

    def __post_init__(self) -> None:
        for value in (
            self.tenant,
            self.environment,
            self.workload,
            self.workload_version,
            self.issuer,
            self.subject,
        ):
            _token(value)
        if self.origin is not IdentityOrigin.MTLS:
            raise EngineCapabilityError(EngineRefusalCode.IDENTITY_UNAVAILABLE)


@dataclass(frozen=True, slots=True)
class EngineTarget:
    """Exact deployed definition identity; no latest, wildcard, default tenant or engine override."""

    process_key: str
    process_version: int
    definition_id: str
    topic: str = ""
    message: str = ""

    def __post_init__(self) -> None:
        for value in (self.process_key, self.definition_id):
            _token(value)
        for value in (self.topic, self.message):
            if value:
                _token(value)
        if type(self.process_version) is not int or self.process_version < 1:
            raise EngineCapabilityError(EngineRefusalCode.RESOURCE_MISMATCH)


@dataclass(frozen=True, slots=True)
class VariableField:
    name: str
    kind: ValueKind
    required: bool = False
    origin: FieldOrigin = FieldOrigin.FACT
    # Dossiers are contract-declared JSON, not maps of engine variables. These named clinical
    # initialization markers stay null when present; other JSON is still data, never authority.
    null_members: tuple[str, ...] = ()
    fixed_json: bytes | None = field(default=None, repr=False)
    empty_evidence_when: tuple[str, bytes] | None = None

    def __post_init__(self) -> None:
        _token(self.name)
        if (
            not isinstance(self.kind, ValueKind)
            or not isinstance(self.origin, FieldOrigin)
            or type(self.required) is not bool
            or type(self.null_members) is not tuple
        ):
            raise EngineCapabilityError(EngineRefusalCode.INVALID_BODY)
        for member in self.null_members:
            _token(member)

    def validate(self, value: object) -> None:
        if self.fixed_json is not None and canonical_json(value) != self.fixed_json:
            raise EngineCapabilityError(EngineRefusalCode.FIELD_DENIED)
        if self.origin is FieldOrigin.FIXED_NULL:
            valid = value is None
        else:
            valid = {
                ValueKind.STRING: type(value) is str,
                ValueKind.BOOLEAN: type(value) is bool,
                ValueKind.INTEGER: type(value) is int and -(2**63) <= value < 2**63,
                ValueKind.DOUBLE: type(value) is float and math.isfinite(value),
                ValueKind.JSON: type(value) in (dict, list),
            }[self.kind]
        if not valid:
            raise EngineCapabilityError(EngineRefusalCode.INVALID_BODY)
        if self.kind is ValueKind.JSON:
            # The legacy mapper treats any dict containing `value` as engine-shaped. Such an
            # envelope must never enter through a declared Json field on the local A seam.
            if type(value) is dict:
                if "value" in value:
                    raise EngineCapabilityError(EngineRefusalCode.FIELD_DENIED)
                if any(value.get(name) is not None for name in self.null_members):
                    raise EngineCapabilityError(EngineRefusalCode.FIELD_DENIED)
            canonical_json(value)


@dataclass(frozen=True, slots=True)
class EngineSchema:
    """Reviewed positive field row; row sources travel into the policy digest for B."""

    schema_id: str
    operation: EngineOperation
    process_key: str
    workload: str
    fields: tuple[VariableField, ...]
    sources: tuple[str, ...]
    topic: str = ""
    message: str = ""
    correlation_fields: tuple[str, ...] = ()
    all_matching: bool = False
    error_codes: tuple[str, ...] = ()
    source_process_key: str = ""
    source_topic: str = ""
    audit_actor: str = ""
    read_projection: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for value in (self.schema_id, self.process_key, self.workload):
            _token(value)
        if (
            not isinstance(self.operation, EngineOperation)
            or type(self.fields) is not tuple
            or type(self.sources) is not tuple
            or not self.sources
            or type(self.correlation_fields) is not tuple
            or type(self.error_codes) is not tuple
            or type(self.read_projection) is not tuple
            or type(self.all_matching) is not bool
        ):
            raise EngineCapabilityError(EngineRefusalCode.INVALID_BODY)
        names = [f.name for f in self.fields]
        if len(names) != len(set(names)):
            raise EngineCapabilityError(EngineRefusalCode.INVALID_BODY)
        if self.operation is EngineOperation.START and (self.topic or self.message):
            raise EngineCapabilityError(EngineRefusalCode.INVALID_BODY)
        if self.operation is EngineOperation.CORRELATE and not self.message:
            raise EngineCapabilityError(EngineRefusalCode.INVALID_BODY)
        if self.operation is not EngineOperation.CORRELATE and (self.correlation_fields or self.all_matching):
            raise EngineCapabilityError(EngineRefusalCode.INVALID_BODY)
        if self.operation in _LOCKED_OPERATIONS | {EngineOperation.FETCH_LOCK} and not self.topic:
            raise EngineCapabilityError(EngineRefusalCode.INVALID_BODY)

    def validate(self, variables: dict[str, Any]) -> None:
        if type(variables) is not dict:
            raise EngineCapabilityError(EngineRefusalCode.INVALID_BODY)
        allowed = {f.name: f for f in self.fields}
        if set(variables) - allowed.keys():
            raise EngineCapabilityError(EngineRefusalCode.FIELD_DENIED)
        if any(f.required and f.name not in variables for f in self.fields):
            raise EngineCapabilityError(EngineRefusalCode.INVALID_BODY)
        for name, value in variables.items():
            allowed[name].validate(value)
        canonical_json(variables)

    def serialize_variables(self, variables: dict[str, Any]) -> dict[str, Any]:
        """Strict B/C wire projection; preserves existing Integer/Long/Json/null semantics.

        Not installed in raw HTTP in A. The local preflight preserves original builder bytes;
        a future typed transport consumes this projection instead of accepting pre-shaped values.
        """
        from maezo.tools.workers.engine_var_types import camunda_int_type

        self.validate(variables)
        result: dict[str, Any] = {}
        for name, value in variables.items():
            wire: Any
            if value is None:
                kind, wire = "String", None
            elif type(value) is bool:
                kind, wire = "Boolean", value
            elif type(value) is int:
                kind, wire = camunda_int_type(name, value), value
            elif type(value) is float:
                kind, wire = "Double", value
            elif type(value) in (dict, list):
                kind, wire = "Json", json.dumps(value, ensure_ascii=False, allow_nan=False)
            else:
                kind, wire = "String", value
            result[name] = {"type": kind, "value": wire}
        return result


@dataclass(frozen=True, slots=True)
class EngineCapabilityProfile:
    identity: EngineIdentity
    target: EngineTarget
    schema: EngineSchema
    worker_id: str = ""
    source_target: EngineTarget | None = None

    def __post_init__(self) -> None:
        from maezo.gateway.engine_schemas import registered_schema

        if not registered_schema(self.schema):
            raise EngineCapabilityError(EngineRefusalCode.PROFILE_UNAVAILABLE)
        if (
            self.identity.workload != self.schema.workload
            or self.target.process_key != self.schema.process_key
            or self.target.topic != self.schema.topic
            or self.target.message != self.schema.message
        ):
            raise EngineCapabilityError(EngineRefusalCode.PROFILE_UNAVAILABLE)
        if self.schema.operation in _LOCKED_OPERATIONS | {EngineOperation.FETCH_LOCK}:
            _token(self.worker_id)
        elif self.worker_id:
            raise EngineCapabilityError(EngineRefusalCode.PROFILE_UNAVAILABLE)
        if self.schema.source_process_key:
            if (
                type(self.source_target) is not EngineTarget
                or self.source_target.process_key != self.schema.source_process_key
                or self.source_target.topic != self.schema.source_topic
            ):
                raise EngineCapabilityError(EngineRefusalCode.PROFILE_UNAVAILABLE)
        elif self.source_target is not None:
            raise EngineCapabilityError(EngineRefusalCode.PROFILE_UNAVAILABLE)

    def document(self) -> dict[str, Any]:
        from dataclasses import asdict

        result: dict[str, Any] = json.loads(json.dumps(asdict(self), default=lambda v: v.decode("utf-8")))
        return {"protocol": "maezo.engine-capability.v1", **result}

    @property
    def digest(self) -> str:
        return hashlib.sha256(canonical_json(self.document())).hexdigest()


@dataclass(frozen=True, slots=True)
class EngineRequest:
    operation: EngineOperation
    process_key: str
    resource_ref: str
    variables_json: bytes = field(repr=False)
    correlation_json: bytes = field(default=b"{}", repr=False)
    all_matching: bool = False
    error_code: str = ""
    topic: str = ""
    message: str = ""
    worker_id: str = ""
    parameters_json: bytes = field(default=b"{}", repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.operation, EngineOperation) or type(self.all_matching) is not bool:
            raise EngineCapabilityError(EngineRefusalCode.OPERATION_DENIED)
        _token(self.process_key)
        # An empty business key is legal only for the explicitly modeled all-matching message.
        if self.resource_ref:
            _token(self.resource_ref)
        elif self.operation is not EngineOperation.CORRELATE or not self.all_matching:
            raise EngineCapabilityError(EngineRefusalCode.RESOURCE_MISMATCH)
        parse_json(self.variables_json)
        parse_json(self.correlation_json)
        parse_json(self.parameters_json)


@dataclass(frozen=True, slots=True)
class AttestedField:
    name: str
    origin: FieldOrigin
    value_json: bytes = field(repr=False)
    source_ref: str

    def __post_init__(self) -> None:
        _token(self.name)
        _token(self.source_ref)
        if (
            self.origin not in (FieldOrigin.ENGINE, FieldOrigin.PRIOR_HUMAN)
            or type(self.value_json) is not bytes
        ):
            raise EngineCapabilityError(EngineRefusalCode.EVIDENCE_UNAVAILABLE)
        try:
            if canonical_json(json.loads(self.value_json)) != self.value_json:
                raise EngineCapabilityError(EngineRefusalCode.EVIDENCE_UNAVAILABLE)
        except (ValueError, UnicodeError, RecursionError):
            raise EngineCapabilityError(EngineRefusalCode.EVIDENCE_UNAVAILABLE) from None


@dataclass(frozen=True, slots=True)
class EngineAuthority:
    """Fresh resolver result, not accepted from an agent/body/header.

    B/C must resolve the exact task/lock/process and attest each carried prior-human field
    from its completed source task. Merely finding an actor id in input is not attestation.
    """

    identity: EngineIdentity
    target: EngineTarget
    resource_ref: str
    policy_digest: str
    valid_until: datetime
    fields: tuple[AttestedField, ...] = ()
    source_target: EngineTarget | None = None
    source_task_ref: str = ""
    lock_owner: str = ""
    lock_expires_at: datetime | None = None
    prior_human_task_ref: str = ""
    prior_human_completed: bool = False

    def __post_init__(self) -> None:
        if (
            type(self.fields) is not tuple
            or any(type(item) is not AttestedField for item in self.fields)
            or len({item.name for item in self.fields}) != len(self.fields)
        ):
            raise EngineCapabilityError(EngineRefusalCode.EVIDENCE_UNAVAILABLE)


class EngineAuthoritySource(Protocol):
    async def resolve(self, request: EngineRequest) -> EngineAuthority: ...


@dataclass(frozen=True, slots=True)
class AuthorizedEngineOperation:
    """Immutable local authorization result; not a credential or an engine receipt."""

    profile_digest: str
    request: EngineRequest
    target: EngineTarget


@dataclass(frozen=True, slots=True, init=False)
class EngineOperationAuthorizer:
    """Testable A port used by the typed gateway. Missing policy/authority never allows."""

    _profiles: tuple[EngineCapabilityProfile, ...]
    _authority: EngineAuthoritySource | None

    def __init__(
        self, profiles: tuple[EngineCapabilityProfile, ...], authority: EngineAuthoritySource | None
    ) -> None:
        if type(profiles) is not tuple:
            raise EngineCapabilityError(EngineRefusalCode.PROFILE_UNAVAILABLE)
        keys = [
            (p.schema.operation, p.target.process_key, p.target.topic, p.target.message) for p in profiles
        ]
        if len(keys) != len(set(keys)):
            raise EngineCapabilityError(EngineRefusalCode.PROFILE_UNAVAILABLE)
        object.__setattr__(self, "_profiles", profiles)
        object.__setattr__(self, "_authority", authority)

    async def authorize_start_identity(
        self,
        request: EngineRequest,
        *,
        tenant: str,
        actor: str,
        actor_version: str,
    ) -> AuthorizedEngineOperation:
        result = await self.authorize(request)
        profile = next(p for p in self._profiles if p.digest == result.profile_digest)
        if (
            request.operation is not EngineOperation.START
            or tenant != profile.identity.tenant
            or actor != (profile.schema.audit_actor or profile.identity.workload)
            or actor_version != profile.identity.workload_version
        ):
            raise EngineCapabilityError(EngineRefusalCode.IDENTITY_MISMATCH)
        return result

    async def authorize(self, request: EngineRequest) -> AuthorizedEngineOperation:
        if not self._profiles:
            raise EngineCapabilityError(EngineRefusalCode.PROFILE_UNAVAILABLE)
        profile = next(
            (
                p
                for p in self._profiles
                if p.schema.operation is request.operation
                and p.target.process_key == request.process_key
                and p.target.topic == request.topic
                and p.target.message == request.message
            ),
            None,
        )
        if profile is None:
            raise EngineCapabilityError(EngineRefusalCode.OPERATION_DENIED)
        variables = parse_json(request.variables_json)
        profile.schema.validate(variables)
        _validate_parameters(request, profile.schema)
        correlation = parse_json(request.correlation_json)
        if (
            set(correlation) != set(profile.schema.correlation_fields)
            or request.all_matching is not profile.schema.all_matching
            or request.worker_id != profile.worker_id
        ):
            raise EngineCapabilityError(EngineRefusalCode.FIELD_DENIED)
        if request.operation is EngineOperation.BPMN_ERROR:
            if request.error_code not in profile.schema.error_codes:
                raise EngineCapabilityError(EngineRefusalCode.OPERATION_DENIED)
        elif request.error_code:
            raise EngineCapabilityError(EngineRefusalCode.OPERATION_DENIED)
        if self._authority is None:
            raise EngineCapabilityError(EngineRefusalCode.IDENTITY_UNAVAILABLE)
        try:
            authority = await self._authority.resolve(request)
        except Exception:
            raise EngineCapabilityError(EngineRefusalCode.IDENTITY_UNAVAILABLE) from None
        if (
            type(authority) is not EngineAuthority
            or authority.identity != profile.identity
            or authority.policy_digest != profile.digest
            or type(authority.valid_until) is not datetime
            or authority.valid_until.tzinfo is None
            or authority.valid_until <= datetime.now(UTC)
        ):
            raise EngineCapabilityError(EngineRefusalCode.IDENTITY_MISMATCH)
        if authority.target != profile.target or authority.resource_ref != request.resource_ref:
            raise EngineCapabilityError(EngineRefusalCode.RESOURCE_MISMATCH)
        if request.operation in _LOCKED_OPERATIONS and (
            authority.lock_owner != profile.worker_id
            or type(authority.lock_expires_at) is not datetime
            or authority.lock_expires_at.tzinfo is None
            or authority.lock_expires_at <= datetime.now(UTC)
        ):
            raise EngineCapabilityError(EngineRefusalCode.RESOURCE_MISMATCH)
        if profile.schema.source_process_key and (
            type(authority.source_target) is not EngineTarget
            or authority.source_target != profile.source_target
            or not authority.source_task_ref
        ):
            raise EngineCapabilityError(EngineRefusalCode.EVIDENCE_UNAVAILABLE)
        bound = {
            "tenant_id": profile.identity.tenant,
            "source_agent_id": profile.identity.workload,
            "source_agent_version": profile.identity.workload_version,
        }
        for name, value in correlation.items():
            if type(value) is not str or not value:
                raise EngineCapabilityError(EngineRefusalCode.INVALID_BODY)
            if name == "tenant_id" and value != profile.identity.tenant:
                raise EngineCapabilityError(EngineRefusalCode.IDENTITY_MISMATCH)
            if name != "tenant_id":
                expected = [v for v in authority.fields if v.name == name]
                if (
                    len(expected) != 1
                    or expected[0].origin is not FieldOrigin.ENGINE
                    or expected[0].value_json != canonical_json(value)
                ):
                    raise EngineCapabilityError(EngineRefusalCode.EVIDENCE_UNAVAILABLE)
        for rule in profile.schema.fields:
            if rule.name not in variables:
                continue
            value = variables[rule.name]
            if rule.origin is FieldOrigin.BOUND and (rule.name not in bound or value != bound[rule.name]):
                raise EngineCapabilityError(EngineRefusalCode.IDENTITY_MISMATCH)
            if rule.origin in (FieldOrigin.ENGINE, FieldOrigin.PRIOR_HUMAN):
                expected = [v for v in authority.fields if v.name == rule.name]
                if (
                    len(expected) != 1
                    or expected[0].origin is not rule.origin
                    or not expected[0].source_ref
                    or expected[0].value_json != canonical_json(value)
                ):
                    raise EngineCapabilityError(EngineRefusalCode.EVIDENCE_UNAVAILABLE)
            if rule.origin is FieldOrigin.PRIOR_HUMAN:
                condition = rule.empty_evidence_when
                empty_automatic = (
                    value == ""
                    and condition is not None
                    and canonical_json(variables.get(condition[0])) == condition[1]
                )
                if not empty_automatic and (
                    value == ""
                    or authority.prior_human_completed is not True
                    or not authority.prior_human_task_ref
                ):
                    raise EngineCapabilityError(EngineRefusalCode.EVIDENCE_UNAVAILABLE)
        return AuthorizedEngineOperation(profile.digest, request, profile.target)


def _validate_parameters(request: EngineRequest, schema: EngineSchema) -> None:
    """Closed v1 lifecycle metadata, distinct from process/local variable maps.

    Milliseconds/counts retain integer types. B/C translates this typed interface to the pinned
    engine API. A failure carries a bounded category, never an engine-echoed PHI error dump.
    """
    params = parse_json(request.parameters_json)
    names = {
        EngineOperation.FETCH_LOCK: {"maxTasks", "lockDuration", "asyncResponseTimeout", "variables"},
        EngineOperation.FAILURE: {"retries", "retryTimeout", "errorCategory"},
        EngineOperation.EXTEND_LOCK: {"newDuration"},
    }.get(request.operation, set())
    if set(params) != names:
        raise EngineCapabilityError(EngineRefusalCode.FIELD_DENIED)
    for name, value in params.items():
        if name == "variables":
            if (
                type(value) is not list
                or any(type(item) is not str for item in value)
                or len(set(value)) != len(value)
                or not set(value) <= set(schema.read_projection)
            ):
                raise EngineCapabilityError(EngineRefusalCode.FIELD_DENIED)
        elif name == "errorCategory":
            # Technical category, no clinical/provider error text and no new retry policy.
            if value != "worker_failure":
                raise EngineCapabilityError(EngineRefusalCode.FIELD_DENIED)
        elif type(value) is not int or value < (0 if name in ("retries", "retryTimeout") else 1):
            raise EngineCapabilityError(EngineRefusalCode.INVALID_BODY)
