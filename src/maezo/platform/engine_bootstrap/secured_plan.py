"""D7-D read-only grant-plan compiler for the frozen B boundary manifest.

This compiler neither grants authority nor claims that certificates, runtime isolation,
migrations or installed native definitions have been verified. It produces the exact
native prerequisites enforced by WorkloadPlugin.authorizeGrants for operator review.
The one wildcard is CIB's required PROCESS_INSTANCE CREATE resource, never general REST
or definition authority. Existing human/D5/D6 grants are outside this compiler's mutation
scope; no mutation adapter is installed in this delivery.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict
from typing import Any

from maezo.gateway.engine_contracts import (
    EngineCapabilityProfile,
    EngineIdentity,
    EngineOperation,
    EngineTarget,
    FieldOrigin,
    IdentityOrigin,
    canonical_json,
    parse_json,
)
from maezo.gateway.engine_schemas import SCHEMAS, start_read_schema, worker_lifecycle_schema


class ProvisioningPlanError(RuntimeError):
    """Fixed safe refusal: never include manifest, secret, or provider text."""

    def __init__(self) -> None:
        super().__init__("engine_provisioning_plan_refused")


_BOUNDARY_KEYS = {
    "protocol",
    "tenant",
    "environment",
    "engine_name",
    "not_before",
    "expires_at",
    "roots",
    "peers",
    "files",
    "listener_port",
    "max_tasks",
    "max_lock_millis",
    "max_poll_millis",
}
_PEER_KEYS = {
    "certificate_sha256",
    "spki_sha256",
    "issuer_dn",
    "subject_dn",
    "uri_san",
    "purpose",
    "engine_user",
    "identity",
    "not_before",
    "expires_at",
    "capabilities",
}
_BINDING_KEYS = {"document", "digest", "source_kind", "source_worker_id", "attestations"}
_PROFILE_KEYS = {"protocol", "identity", "target", "schema", "worker_id", "source_target"}
_IDENTITY_KEYS = {"tenant", "environment", "workload", "workload_version", "issuer", "subject", "origin"}
_TARGET_KEYS = {"process_key", "process_version", "definition_id", "topic", "message"}
_LIFECYCLE = {
    EngineOperation.FETCH_LOCK,
    EngineOperation.COMPLETE,
    EngineOperation.FAILURE,
    EngineOperation.BPMN_ERROR,
    EngineOperation.UNLOCK,
    EngineOperation.EXTEND_LOCK,
    EngineOperation.CORRELATE,
}


def _keys(value: Any, names: set[str]) -> None:
    if type(value) is not dict or set(value) != names:
        raise ProvisioningPlanError()


def _token(value: Any) -> None:
    if (
        type(value) is not str
        or not 0 < len(value) <= 256
        or "*" in value
        or any(ord(c) < 33 or ord(c) > 126 for c in value)
    ):
        raise ProvisioningPlanError()


def _digest(value: Any) -> None:
    if type(value) is not str or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ProvisioningPlanError()


def _window(value: dict[str, Any]) -> None:
    if (
        type(value["not_before"]) is not int
        or type(value["expires_at"]) is not int
        or not 0 <= value["not_before"] < value["expires_at"]
    ):
        raise ProvisioningPlanError()


def _identity(value: Any) -> EngineIdentity:
    _keys(value, _IDENTITY_KEYS)
    return EngineIdentity(**{**value, "origin": IdentityOrigin(value["origin"])})


def _target(value: Any) -> EngineTarget:
    _keys(value, _TARGET_KEYS)
    return EngineTarget(**value)


def _registered() -> dict[str, Any]:
    schemas = list(SCHEMAS)
    for base in SCHEMAS:
        if base.operation is EngineOperation.START:
            schemas.extend(
                start_read_schema(base, op)
                for op in (EngineOperation.READ_ACTIVE, EngineOperation.READ_HISTORY)
            )
        if base.topic:
            schemas.extend(
                worker_lifecycle_schema(base, op)
                for op in (
                    EngineOperation.FETCH_LOCK,
                    EngineOperation.FAILURE,
                    EngineOperation.EXTEND_LOCK,
                    EngineOperation.UNLOCK,
                )
            )
    return {s.schema_id: s for s in schemas}


def _binding(value: Any, identity: EngineIdentity) -> EngineCapabilityProfile:
    _keys(value, _BINDING_KEYS)
    doc = value["document"]
    _keys(doc, _PROFILE_KEYS)
    schema_doc = doc["schema"]
    if type(schema_doc) is not dict:
        raise ProvisioningPlanError()
    schema_id = schema_doc.get("schema_id")
    if type(schema_id) is not str:
        raise ProvisioningPlanError()
    schema = _registered().get(schema_id)
    if schema is None:
        raise ProvisioningPlanError()
    profile = EngineCapabilityProfile(
        _identity(doc["identity"]),
        _target(doc["target"]),
        schema,
        worker_id=doc["worker_id"],
        source_target=_target(doc["source_target"]) if doc["source_target"] is not None else None,
    )
    if profile.identity != identity or doc != profile.document() or value["digest"] != profile.digest:
        raise ProvisioningPlanError()
    kind, worker, attestations = value["source_kind"], value["source_worker_id"], value["attestations"]
    if type(attestations) is not list or type(kind) is not str or type(worker) is not str:
        raise ProvisioningPlanError()
    if not schema.source_process_key:
        if kind or worker or attestations:
            raise ProvisioningPlanError()
    elif kind not in {"locked_external", "completed_human"}:
        raise ProvisioningPlanError()
    if kind == "locked_external":
        _token(worker)
    elif worker:
        raise ProvisioningPlanError()
    required = {f.name for f in schema.fields if f.origin in (FieldOrigin.ENGINE, FieldOrigin.PRIOR_HUMAN)}
    required.update(name for name in schema.correlation_fields if name != "tenant_id")
    found = set()
    for item in attestations:
        _keys(item, {"name", "source_variable", "human_task_definition"})
        _token(item["name"])
        _token(item["source_variable"])
        if item["human_task_definition"]:
            _token(item["human_task_definition"])
        elif type(item["human_task_definition"]) is not str:
            raise ProvisioningPlanError()
        if item["name"] in found:
            raise ProvisioningPlanError()
        found.add(item["name"])
    if found != required:
        raise ProvisioningPlanError()
    return profile


def compile_boundary_plan(raw: bytes, *, expected_sha256: str) -> dict[str, Any]:
    """Compile pinned public B bytes; never access engine, credentials or mounted trust files.

    Structural validity is for review only. Mounted paths/certificates/current timestamps,
    native definitions and enforceable runtime isolation require independent actual readback.
    Every output is marked execution_authorized=False; there is deliberately no apply option.
    """
    try:
        return _compile(raw, expected_sha256)
    except Exception:
        raise ProvisioningPlanError() from None


def _compile(raw: bytes, expected_sha256: str) -> dict[str, Any]:
    _digest(expected_sha256)
    if type(raw) is not bytes or hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ProvisioningPlanError()
    boundary = parse_json(raw)
    _keys(boundary, _BOUNDARY_KEYS)
    if boundary["protocol"] != "maezo.engine-boundary.v1":
        raise ProvisioningPlanError()
    for name in ("tenant", "environment", "engine_name"):
        _token(boundary[name])
    _window(boundary)
    for name, upper in (
        ("listener_port", 65535),
        ("max_tasks", 2**31 - 1),
        ("max_lock_millis", 2**63 - 1),
        ("max_poll_millis", 2**31 - 1),
    ):
        if type(boundary[name]) is not int or not 1 <= boundary[name] <= upper:
            raise ProvisioningPlanError()
    if type(boundary["roots"]) is not list or not boundary["roots"] or type(boundary["files"]) is not list:
        raise ProvisioningPlanError()
    paths: dict[str, str] = {}
    for item in boundary["roots"] + boundary["files"]:
        _keys(item, {"path", "sha256"})
        path = item["path"]
        _digest(item["sha256"])
        if (
            type(path) is not str
            or not path.startswith("/")
            or "\x00" in path
            or any(part in (".", "..", "") for part in path.split("/")[1:])
            or (path in paths and paths[path] != item["sha256"])
        ):
            raise ProvisioningPlanError()
        paths[path] = item["sha256"]
    if type(boundary["peers"]) is not list or not boundary["peers"]:
        raise ProvisioningPlanError()
    certificates: set[str] = set()
    principals: dict[str, dict[str, Any]] = {}
    key_owners: dict[str, tuple[str, str, bytes]] = {}
    subject_owners: dict[str, tuple[str, str, bytes]] = {}
    operators: dict[str, str] = {}
    grants: dict[tuple[str, str, str], set[str]] = {}
    preserves: set[str] = set()
    traces: dict[tuple[str, str, str], set[str]] = {}
    targets: dict[bytes, dict[str, Any]] = {}

    def add(user: str, resource: str, resource_id: str, permissions: set[str], digest: str) -> None:
        key = user, resource, resource_id
        grants.setdefault(key, set()).update(permissions)
        traces.setdefault(key, set()).add(digest)

    for peer in boundary["peers"]:
        _keys(peer, _PEER_KEYS)
        identity = _identity(peer["identity"])
        purpose, user = peer["purpose"], peer["engine_user"]
        _token(purpose)
        _token(user)
        if purpose not in {"nonhuman", "bootstrap", "deployment", "human-relay", "observer"}:
            raise ProvisioningPlanError()
        _window(peer)
        if (
            identity.tenant != boundary["tenant"]
            or identity.environment != boundary["environment"]
            or not boundary["not_before"] <= peer["not_before"] < peer["expires_at"] <= boundary["expires_at"]
            or peer["issuer_dn"] != identity.issuer
            or peer["uri_san"] != identity.subject
            or not identity.subject.startswith("spiffe://")
            or type(peer["subject_dn"]) is not str
            or not peer["subject_dn"]
            or type(peer["capabilities"]) is not list
        ):
            raise ProvisioningPlanError()
        for digest in (peer["certificate_sha256"], peer["spki_sha256"]):
            _digest(digest)
        if peer["certificate_sha256"] in certificates:
            raise ProvisioningPlanError()
        certificates.add(peer["certificate_sha256"])
        owner = (purpose, user, canonical_json(peer["identity"]))
        for index, key in ((key_owners, peer["spki_sha256"]), (subject_owners, identity.subject)):
            if key in index and index[key] != owner:
                raise ProvisioningPlanError()
            index[key] = owner
        authority = dict(identity=peer["identity"], purpose=purpose, capabilities=peer["capabilities"])
        if user in principals and principals[user] != authority:
            raise ProvisioningPlanError()
        principals[user] = authority
        if purpose != "nonhuman":
            if peer["capabilities"]:
                raise ProvisioningPlanError()
            if purpose in {"bootstrap", "deployment"}:
                if purpose in operators and operators[purpose] != user:
                    raise ProvisioningPlanError()
                operators[purpose] = user
            else:
                preserves.add(user)
            continue
        if not peer["capabilities"]:
            raise ProvisioningPlanError()
        seen: set[str] = set()
        for binding in peer["capabilities"]:
            profile = _binding(binding, identity)
            if profile.digest in seen:
                raise ProvisioningPlanError()
            seen.add(profile.digest)
            target = asdict(profile.target)
            targets[canonical_json(target)] = target
            permissions = {"READ"}
            operation = profile.schema.operation
            if operation is EngineOperation.START:
                permissions.add("CREATE_INSTANCE")
                add(user, "PROCESS_INSTANCE", "*", {"CREATE"}, profile.digest)
            elif operation is EngineOperation.READ_ACTIVE:
                permissions.add("READ_INSTANCE")
            elif operation is EngineOperation.READ_HISTORY:
                permissions.add("READ_HISTORY")
            elif operation in _LIFECYCLE:
                permissions.update({"READ_INSTANCE", "UPDATE_INSTANCE"})
            else:
                raise ProvisioningPlanError()
            add(user, "PROCESS_DEFINITION", profile.target.process_key, permissions, profile.digest)
            if profile.source_target is not None:
                target = asdict(profile.source_target)
                targets[canonical_json(target)] = target
                permissions = {"READ"}
                if binding["source_kind"] == "locked_external":
                    permissions.add("READ_INSTANCE")
                if (
                    binding["source_kind"] == "completed_human"
                    or any(a["source_variable"] == "@business_key" for a in binding["attestations"])
                    or any(f.origin is FieldOrigin.PRIOR_HUMAN for f in profile.schema.fields)
                ):
                    permissions.add("READ_HISTORY")
                add(
                    user, "PROCESS_DEFINITION", profile.source_target.process_key, permissions, profile.digest
                )
    if set(operators) != {"bootstrap", "deployment"} or not grants:
        raise ProvisioningPlanError()
    return dict(
        protocol="maezo.engine-provisioning-plan.v1",
        execution_authorized=False,
        boundary_sha256=expected_sha256,
        tenant=boundary["tenant"],
        environment=boundary["environment"],
        engine_name=boundary["engine_name"],
        operators=operators,
        preserve_existing_users=sorted(preserves),
        definitions=[targets[k] for k in sorted(targets)],
        grants=[
            dict(
                engine_user=user, resource=resource, resource_id=resource_id, permissions=sorted(permissions)
            )
            for (user, resource, resource_id), permissions in sorted(grants.items())
        ],
        grant_sources=[
            dict(
                engine_user=user,
                resource=resource,
                resource_id=resource_id,
                capability_digests=sorted(digests),
            )
            for (user, resource, resource_id), digests in sorted(traces.items())
        ],
        required_readbacks=[
            "exclusive_runtime_fence",
            "separate_privileged_database_role",
            "native_schema_and_artifacts",
            "all_existing_authorizations_preserved",
            "actual_certificate_and_mount_validation",
            "one_transaction_receipt",
            "bootstrap_credential_disposal",
            "secured_engine_readiness",
        ],
    )
