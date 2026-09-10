"""Closed FETCH_LOCK v2 codec; approved native acquisition contract §3–7.

Numbers here are native JSON integers, unlike the human AUTH signing codec.
Immutable bytes carry projections; technical receipts never carry variable bodies.
"""

from __future__ import annotations

import base64
import hashlib
import math
import re
from dataclasses import dataclass, field
from typing import Any, cast

from maezo.gateway.engine_contracts import canonical_json, parse_json
from maezo.tools.workers.auth_exact_amount import TYPE, AuthExactAmount


class FetchUnavailableError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("native_fetch_unavailable")


def require(condition: bool) -> None:
    if not condition:
        raise FetchUnavailableError()


def closed(value: Any, names: str) -> dict[str, Any]:
    require(type(value) is dict and value.keys() == set(names.split()))
    return value  # type: ignore[no-any-return]


def token(value: Any) -> str:
    require(type(value) is str and re.fullmatch(r"[!-~]{1,256}", value) is not None and "*" not in value)
    return value  # type: ignore[no-any-return]


def digest(value: Any) -> str:
    require(type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None)
    return value  # type: ignore[no-any-return]


def ref(value: Any) -> str:
    require(type(value) is str and re.fullmatch(r"[A-Za-z0-9_-]{43}", value) is not None)
    raw = base64.urlsafe_b64decode(value + "=")
    require(len(raw) == 32 and base64.urlsafe_b64encode(raw).rstrip(b"=").decode() == value)
    return value  # type: ignore[no-any-return]


def integer(value: Any, minimum: int = -(2**63), maximum: int = 2**63 - 1) -> int:
    require(type(value) is int and minimum <= value <= maximum)
    return value  # type: ignore[no-any-return]


def encode(value: Any) -> bytes:
    try:
        return canonical_json(value)
    except Exception:
        raise FetchUnavailableError() from None


def decode(value: bytes) -> dict[str, Any]:
    try:
        return parse_json(value)
    except Exception:
        raise FetchUnavailableError() from None


def sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def identity(value: Any) -> dict[str, Any]:
    value = closed(value, "tenant environment workload workload_version issuer subject origin")
    for item in value.values():
        token(item)
    require(value["origin"] == "verified_mtls")
    return cast(dict[str, Any], value)


def command(value: Any) -> dict[str, Any]:
    value = closed(
        value,
        "engine database_incarnation identity native_user operation capability_digest command_id "
        "request_digest activation_ref",
    )
    for name in ("engine", "database_incarnation", "native_user", "activation_ref"):
        token(value[name])
    identity(value["identity"])
    require(value["operation"] == "fetch_lock")
    digest(value["capability_digest"])
    digest(value["request_digest"])
    ref(value["command_id"])
    return cast(dict[str, Any], value)


@dataclass(frozen=True, slots=True, repr=False)
class FetchProfile:
    """Owner-installed selector and classification, never a caller-selected grant.

    This slice accepts only source-free, zero-input-field fetch schemas. A real
    installed schema and D lease must admit its exact digest before transport.
    """

    document: bytes = field(repr=False)
    classifications: tuple[tuple[str, tuple[str, ...]], ...]
    input_profile: str

    def __post_init__(self) -> None:
        require(type(self.document) is bytes and type(self.classifications) is tuple)
        doc = closed(
            decode(self.document),
            "protocol identity target schema worker_id source_target acquisition_policy",
        )
        require(encode(doc) == self.document and doc["protocol"] == "maezo.engine-capability.v2")
        ident = identity(doc["identity"])
        target = closed(doc["target"], "process_key process_version definition_id topic message")
        for name in ("process_key", "definition_id", "topic"):
            token(target[name])
        integer(target["process_version"], 1)
        token(doc["worker_id"])
        require(target["message"] == "" and doc["source_target"] is None)
        policy = closed(
            doc["acquisition_policy"], "resource_requirement source_requirement source_owner_binding_digest"
        )
        require(
            policy
            == dict(resource_requirement="none", source_requirement="none", source_owner_binding_digest=None)
        )
        schema = closed(
            doc["schema"],
            "schema_id operation process_key workload fields sources topic message correlation_fields "
            "all_matching error_codes source_process_key source_topic audit_actor read_projection",
        )
        token(schema["schema_id"])
        require(schema["operation"] == "fetch_lock" and schema["workload"] == ident["workload"])
        require(all(schema[name] == target[name] for name in ("process_key", "topic", "message")))
        require(schema["fields"] == [] and schema["correlation_fields"] == [] and schema["error_codes"] == [])
        require(
            schema["all_matching"] is False and schema["source_process_key"] == schema["source_topic"] == ""
        )
        require(type(schema["audit_actor"]) is str)
        for name in ("sources", "read_projection"):
            values = schema[name]
            require(type(values) is list)
            for item in values:
                token(item)
            require(len(values) == len(set(values)))
        require(bool(schema["sources"]))
        allowed = {"null", "string", "boolean", "double", "long", "integer", "json"}
        if self.input_profile == "portal-auth-intake.v1":
            allowed.add(TYPE)
        else:
            require(self.input_profile == "native-classified.v2")
        names = []
        for entry in self.classifications:
            require(type(entry) is tuple and len(entry) == 2)
            name, tags = entry
            token(name)
            require(type(tags) is tuple and bool(tags) and len(tags) == len(set(tags)))
            require(set(tags) <= allowed)
            if TYPE in tags:
                require(name == "valor_estimado" and tags == (TYPE,))
            names.append(name)
        require(len(names) == len(set(names)) and set(names) <= set(schema["read_projection"]))

    @property
    def digest(self) -> str:
        return sha(self.document)

    def value(self) -> dict[str, Any]:
        return decode(self.document)


@dataclass(frozen=True, slots=True, repr=False)
class PreparedFetch:
    body: bytes = field(repr=False)
    binding: bytes = field(repr=False)
    profile: FetchProfile = field(repr=False)

    def __post_init__(self) -> None:
        require(type(self.profile) is FetchProfile)
        self.profile.__post_init__()
        req = closed(
            decode(self.body),
            "protocol capability_digest operation process_key resource_ref variables correlation "
            "all_matching error_code topic message worker_id parameters source_ref command_id "
            "activation_ref resource_acquisition source_acquisition",
        )
        bound = command(decode(self.binding))
        doc = self.profile.value()
        require(req["protocol"] == "maezo.engine-operation.v2" and req["operation"] == "fetch_lock")
        require(req["capability_digest"] == self.profile.digest == bound["capability_digest"])
        require(bound["identity"] == doc["identity"] and bound["request_digest"] == sha(self.body))
        require(req["command_id"] == bound["command_id"] and req["activation_ref"] == bound["activation_ref"])
        require(req["worker_id"] == doc["worker_id"])
        require(all(req[name] == doc["target"][name] for name in ("process_key", "topic", "message")))
        token(req["resource_ref"])
        require(req["source_ref"] == req["error_code"] == "" and req["variables"] == req["correlation"] == {})
        require(
            req["all_matching"] is False and req["resource_acquisition"] is req["source_acquisition"] is None
        )
        parameters = closed(req["parameters"], "maxTasks lockDuration asyncResponseTimeout variables")
        for name in ("maxTasks", "lockDuration", "asyncResponseTimeout"):
            integer(parameters[name], 1)
        projection = parameters["variables"]
        require(type(projection) is list)
        for name in projection:
            token(name)
        require(
            len(projection) == len(set(projection))
            and set(projection) <= {n for n, _ in self.profile.classifications}
        )


def prepare(
    profile: FetchProfile,
    *,
    engine: str,
    database_incarnation: str,
    native_user: str,
    activation_ref: str,
    command_id: str,
    resource_ref: str,
    max_tasks: int,
    lock_millis: int,
    poll_millis: int,
    projection: tuple[str, ...],
) -> PreparedFetch:
    doc = profile.value()
    body = encode(
        dict(
            protocol="maezo.engine-operation.v2",
            capability_digest=profile.digest,
            operation="fetch_lock",
            process_key=doc["target"]["process_key"],
            resource_ref=resource_ref,
            variables={},
            correlation={},
            all_matching=False,
            error_code="",
            topic=doc["target"]["topic"],
            message="",
            worker_id=doc["worker_id"],
            parameters=dict(
                maxTasks=max_tasks,
                lockDuration=lock_millis,
                asyncResponseTimeout=poll_millis,
                variables=list(projection),
            ),
            source_ref="",
            command_id=command_id,
            activation_ref=activation_ref,
            resource_acquisition=None,
            source_acquisition=None,
        )
    )
    binding = encode(
        dict(
            engine=engine,
            database_incarnation=database_incarnation,
            identity=doc["identity"],
            native_user=native_user,
            operation="fetch_lock",
            capability_digest=profile.digest,
            command_id=command_id,
            request_digest=sha(body),
            activation_ref=activation_ref,
        )
    )
    return PreparedFetch(body, binding, profile)


def receipt(value: Any, expected: PreparedFetch) -> dict[str, Any]:
    result = closed(
        value,
        "receipt_ref state command resource_ref_digest source_ref_digest resource_acquisition "
        "source_acquisition acquisitions",
    )
    ref(result["receipt_ref"])
    require(result["state"] == "committed" and command(result["command"]) == decode(expected.binding))
    request = decode(expected.body)
    for role in ("resource", "source"):
        require(result[role + "_ref_digest"] == sha(request[role + "_ref"].encode()))
        require(result[role + "_acquisition"] is None)
    acquisitions = result["acquisitions"]
    require(type(acquisitions) is list and len(acquisitions) <= request["parameters"]["maxTasks"])
    tasks, references = set(), set()
    for item in acquisitions:
        row = closed(item, "role task_ref acquisition_ref lease_revision lock_expires_at state")
        require(row["role"] == "resource" and row["state"] == "live")
        task, reference = token(row["task_ref"]), ref(row["acquisition_ref"])
        require(task not in tasks and reference not in references)
        tasks.add(task)
        references.add(reference)
        require(integer(row["lease_revision"], 1) == 1)
        integer(row["lock_expires_at"], 0)
    return result


def variable(value: Any, tags: tuple[str, ...]) -> None:
    wire = closed(value, "type value")
    tag, item = wire["type"], wire["value"]
    require(type(tag) is str and tag in tags)
    if tag == TYPE:
        try:
            AuthExactAmount.parse(item)
        except ValueError:
            raise FetchUnavailableError() from None
    elif tag == "null":
        require(item is None)
    elif tag == "json" or item is None:
        encode(item)
    elif tag == "string":
        require(type(item) is str)
    elif tag == "boolean":
        require(type(item) is bool)
    elif tag in ("integer", "long"):
        integer(
            item, -(2**31) if tag == "integer" else -(2**63), 2**31 - 1 if tag == "integer" else 2**63 - 1
        )
    elif tag == "double":
        require(type(item) in (int, float) and math.isfinite(item))
    else:
        raise FetchUnavailableError()


@dataclass(frozen=True, slots=True, repr=False)
class FetchResult:
    status: str
    receipt: bytes | None = field(repr=False)
    rows: tuple[bytes, ...] = field(repr=False)


def refusal(value: dict[str, Any], status: int) -> bool:
    if value.get("protocol") != "maezo.engine-refusal.v2":
        return False
    closed(value, "protocol code")
    require((status, value["code"]) in {(400, "invalid_request"), (403, "denied"), (503, "unavailable")})
    return True


def result(raw: bytes, status: int, expected: PreparedFetch) -> FetchResult:
    value = decode(raw)
    if refusal(value, status):
        return FetchResult("refused" if status != 503 else "unavailable", None, ())
    value = closed(value, "protocol command status receipt result")
    require(
        value["protocol"] == "maezo.engine-result.v2"
        and command(value["command"]) == decode(expected.binding)
    )
    state = value["status"]
    require(
        (status, state) in {(200, "executed"), (200, "duplicate"), (409, "conflict"), (503, "unavailable")}
    )
    if state in ("conflict", "unavailable"):
        require(value["receipt"] is value["result"] is None)
        return FetchResult(state, None, ())
    rec = receipt(value["receipt"], expected)
    if state == "duplicate":
        require(value["result"] is None)
        return FetchResult(state, encode(rec), ())
    initial = closed(value["result"], "value acquisitions")
    require(initial["acquisitions"] == rec["acquisitions"] and type(initial["value"]) is list)
    rows = initial["value"]
    require(len(rows) == len(rec["acquisitions"]))
    request, doc = decode(expected.body), expected.profile.value()
    classifications = dict(expected.profile.classifications)
    for row, acquired in zip(rows, rec["acquisitions"], strict=True):
        closed(
            row,
            "id definition_id process_instance_id execution_id topic worker_id lock_expires_at "
            "variables retries",
        )
        for name in ("id", "definition_id", "process_instance_id", "execution_id", "topic", "worker_id"):
            token(row[name])
        require(
            row["id"] == acquired["task_ref"]
            and integer(row["lock_expires_at"], 0) == acquired["lock_expires_at"]
        )
        require(
            row["definition_id"] == doc["target"]["definition_id"]
            and row["topic"] == doc["target"]["topic"]
            and row["worker_id"] == doc["worker_id"]
        )
        if row["retries"] is not None:
            integer(row["retries"], -(2**31), 2**31 - 1)
        require(
            type(row["variables"]) is dict
            and row["variables"].keys() <= set(request["parameters"]["variables"])
        )
        for name, entry in row["variables"].items():
            variable(entry, classifications[name])
    return FetchResult(state, encode(rec), tuple(encode(row) for row in rows))


def outcome_query(expected: PreparedFetch, recovery_digest: str, reader_activation: str) -> bytes:
    return encode(
        dict(
            protocol="maezo.engine-outcome-query.v2",
            recovery_capability_digest=digest(recovery_digest),
            reader_activation_ref=token(reader_activation),
            command=decode(expected.binding),
        )
    )


def outcome(raw: bytes, status: int, expected: PreparedFetch, query: bytes) -> FetchResult:
    value, sent = decode(raw), decode(query)
    closed(sent, "protocol recovery_capability_digest reader_activation_ref command")
    require(sent["protocol"] == "maezo.engine-outcome-query.v2")
    require(command(sent["command"]) == decode(expected.binding))
    digest(sent["recovery_capability_digest"])
    token(sent["reader_activation_ref"])
    if refusal(value, status):
        return FetchResult("refused" if status != 503 else "unavailable", None, ())
    closed(
        value, "protocol recovery_capability_digest reader_activation_ref query_digest command status receipt"
    )
    require(value["protocol"] == "maezo.engine-outcome.v2" and value["query_digest"] == sha(query))
    for name in ("command", "recovery_capability_digest", "reader_activation_ref"):
        require(value[name] == sent[name])
    command(value["command"])
    state = value["status"]
    require(
        (status, state)
        in {(200, "committed"), (200, "not_observed"), (409, "conflict"), (503, "unavailable")}
    )
    if state == "committed":
        return FetchResult(state, encode(receipt(value["receipt"], expected)), ())
    require(value["receipt"] is None)
    return FetchResult(state, None, ())
