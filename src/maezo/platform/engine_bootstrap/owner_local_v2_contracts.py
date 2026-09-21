"""Inert original-creation L1/L2 values and exact policy templates (ADR-0058).

These records authenticate nothing. No SDK, credential, signer, journal,
process, database or effect-admission implementation is present.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .controller_storage import canonical, digest, exact, parse_wire, require, scalar

PROFILE = "maezo.d7-local-owner.initial-l1-l2.v2"
PENDING = "UNQUALIFIED_OC_F01"
PREFIX = "maezo.d7-local-owner."
MAX_BYTES = 131_072
PURPOSES = ("W", "R", "L", "C0", "Q", "O")
CHILDREN = {"W": "w", "R": "r", "L": "l", "C0": "c"}
STATES = ("CLAIMED", "ISSUE_STARTED", "ISSUED", "DELIVERY_STARTED", "DELIVERED", "DISPOSED")
IDS = "enrollment_id installation_id run_id control_scope_id bootstrap_operation_id acquisition_id"
SOURCE = "git_sha tree_sha artifact_sha256 manifest_sha256 cib_abi_sha256 policy_profile_sha256"
TRUST = (
    "key_id public_key_sha256 owner_arn owner_role_id enrollment_table_arn enrollment_table_id "
    "enrollment_reader_arn enrollment_reader_role_id owner_ingress_arn owner_ingress_role_id "
    "owner_source_identity cloud_reader_arn cloud_reader_role_id"
)
FIELDS = {
    "intent": "protocol mode profile topology "
    + IDS
    + " scope trust resources source not_before_ms deadline_ms operations",
    "resources": (
        "protocol installation_id account region control_table_arn writer_arn reader_arn "
        "continuation_arn control_observer_arn owner_policy_arn"
    ),
    "managed_policy": (
        "protocol intent_sha256 parent_interval_id source_artifact_sha256 policy_arn "
        "policy_id version_id created_at_ms observed_at_ms document document_sha256"
    ),
    "purpose": (
        "protocol intent_sha256 parent_interval_id purpose role_arn request_sha256 "
        "source_artifact_sha256 child recorded_at_ms"
    ),
    "session": (
        "protocol intent_sha256 parent_interval_id purpose request request_sha256 source_artifact_sha256"
    ),
    "role_identity": (
        "protocol intent_sha256 purpose role_arn role_id path role_name created_at_ms observed_at_ms"
    ),
    "identity": (
        "protocol intent_sha256 purpose request_sha256 role_arn role_id session_arn user_id "
        "caller_account caller_arn caller_user_id issued_at_ms expires_at_ms "
        "packed_policy_size"
    ),
    "issuance": (
        "protocol intent_sha256 purpose claim_sha256 revision state previous_sha256 "
        "evidence_sha256 recorded_at_ms"
    ),
}
META = ("dynamodb:DescribeTable", "dynamodb:DescribeTimeToLive", "dynamodb:GetResourcePolicy")
DATA = ("dynamodb:GetItem", "dynamodb:Query")
CLOUD_READS = (
    "iam:GetAccountSummary",
    "iam:ListUsers",
    "iam:ListGroups",
    "iam:ListRoles",
    "iam:ListPolicies",
    "iam:GetRole",
    "iam:GetRolePolicy",
    "iam:ListRolePolicies",
    "iam:ListAttachedRolePolicies",
    "iam:GetPolicy",
    "iam:GetPolicyVersion",
    "iam:ListPolicyVersions",
    "ecs:ListClusters",
    "ecs:DescribeClusters",
    "ecs:ListServices",
    "ecs:DescribeServices",
    "ecs:ListTasks",
    "ecs:DescribeTasks",
    "ecs:ListTaskDefinitions",
    "ecs:DescribeTaskDefinition",
    "ecs:DescribeTaskSets",
    "scheduler:ListScheduleGroups",
    "scheduler:ListSchedules",
    "events:ListEventBuses",
    "events:ListRules",
    "events:ListTargetsByRule",
    "application-autoscaling:DescribeScalableTargets",
    "application-autoscaling:DescribeScalingPolicies",
    "application-autoscaling:DescribeScheduledActions",
    "autoscaling:DescribeAutoScalingGroups",
    "autoscaling:DescribePolicies",
    "autoscaling:DescribeScheduledActions",
)


def role_parts(arn: Any) -> tuple[str, str, str]:
    """Return account, real IAM Path and RoleName; no AWS observation."""
    require(type(arn) is str and len(arn) <= 2048)
    match = re.fullmatch(r"arn:aws:iam::([0-9]{12}):role/([A-Za-z0-9_+=,.@/-]+)", arn)
    require(match is not None)
    assert match is not None
    account, resource = match.groups()
    parts = resource.split("/")
    require(all(p not in {"", ".", ".."} for p in parts))
    name = parts[-1]
    path = "/" + "/".join(parts[:-1]) + ("/" if len(parts) > 1 else "")
    require(len(name) <= 64 and len(path) <= 512)
    return account, path, name


def _role_id(value: Any) -> None:
    require(type(value) is str and re.fullmatch(r"AROA[A-Z0-9]{17}", value) is not None)


def resource_value(installation_id: str, account: str, region: str) -> dict[str, Any]:
    scalar("Uuid", installation_id)
    require(type(account) is str and re.fullmatch(r"[0-9]{12}", account) is not None)
    require(
        type(region) is str
        and len(region) <= 32
        and re.fullmatch(r"[a-z]{2}-[a-z]+-[0-9]", region) is not None
    )
    role = f"arn:aws:iam::{account}:role/maezo-d7-v2/"
    return {
        "protocol": PREFIX + "resources.v2",
        "installation_id": installation_id,
        "account": account,
        "region": region,
        "control_table_arn": f"arn:aws:dynamodb:{region}:{account}:table/maezo-d7-{installation_id}-control",
        "writer_arn": role + "w-" + installation_id,
        "reader_arn": role + "r-" + installation_id,
        "continuation_arn": role + "l-" + installation_id,
        "control_observer_arn": role + "c-" + installation_id,
        "owner_policy_arn": f"arn:aws:iam::{account}:policy/maezo-d7-v2/owner-{installation_id}",
    }


def _resources(v: dict[str, Any]) -> None:
    require(v == resource_value(v["installation_id"], v["account"], v["region"]), "SCOPE_REFUSED")


def _intent(v: dict[str, Any]) -> None:
    require(v["protocol"] == PREFIX + "intent.v2" and v["profile"] == PROFILE)
    require(v["mode"] == "PUBLIC_SYNTHETIC" and v["topology"] == PENDING, "UNSUPPORTED_ADAPTER")
    require(v["operations"] == ["bootstrap_scope", "install_d", "initial_reconcile", "reserve_initial"])
    for name in IDS.split():
        scalar("Uuid", v[name])
    scalar("Scope", v["scope"])
    r = exact(v["resources"], FIELDS["resources"])
    _resources(r)
    require(
        r["installation_id"] == v["installation_id"]
        and r["account"] == v["scope"]["account"]
        and r["region"] == v["scope"]["region"],
        "SCOPE_REFUSED",
    )
    t = exact(v["trust"], TRUST)
    scalar("Id", t["key_id"])
    scalar("Sha256", t["public_key_sha256"])
    scalar("Uuid", t["enrollment_table_id"])
    table_prefix = f"arn:aws:dynamodb:{r['region']}:{r['account']}:table/"
    table = t["enrollment_table_arn"]
    require(type(table) is str and table.startswith(table_prefix))
    require(re.fullmatch(r"[A-Za-z0-9_.-]{3,255}", table[len(table_prefix) :]) is not None)
    require(table != r["control_table_arn"], "SCOPE_REFUSED")
    roles, role_ids = [], []
    for prefix in ("owner", "enrollment_reader", "owner_ingress", "cloud_reader"):
        arn, rid = t[prefix + "_arn"], t[prefix + "_role_id"]
        require(role_parts(arn)[0] == r["account"], "SCOPE_REFUSED")
        _role_id(rid)
        roles.append(arn)
        role_ids.append(rid)
    children = [r[k] for k in ("writer_arn", "reader_arn", "continuation_arn", "control_observer_arn")]
    require(len(set(roles + children)) == 8 and len(set(role_ids)) == 4, "AUTH_REFUSED")
    sid = t["owner_source_identity"]
    require(
        type(sid) is str
        and re.fullmatch(r"[A-Za-z0-9_+=,.@-]{2,64}", sid) is not None
        and not sid.startswith("aws:")
    )
    for name, value in exact(v["source"], SOURCE).items():
        scalar("GitOid" if name in {"git_sha", "tree_sha"} else "Sha256", value)
    for name in ("not_before_ms", "deadline_ms"):
        scalar("UInt", v[name])
    require(0 < v["deadline_ms"] - v["not_before_ms"] <= 900_000)


def _child(v: Any) -> None:
    exact(v, "instance_id boot_id pid start_ticks uid executable_sha256 channel_id")
    for name in ("instance_id", "boot_id", "channel_id"):
        scalar("Uuid", v[name])
    for name in ("pid", "start_ticks", "uid"):
        scalar("Positive", v[name])
    scalar("Sha256", v["executable_sha256"])


def _record(kind: str, v: dict[str, Any]) -> None:
    require(v["protocol"] == PREFIX + kind.replace("_", "-") + ".v2")
    if kind in {"intent", "resources"}:
        (_intent if kind == "intent" else _resources)(v)
        return
    scalar("Sha256", v["intent_sha256"])
    if kind in {"managed_policy", "purpose", "session"}:
        scalar("Uuid", v["parent_interval_id"])
        scalar("Sha256", v["source_artifact_sha256"])
    if kind in {"purpose", "session", "identity", "role_identity", "issuance"}:
        require(type(v["purpose"]) is str and v["purpose"] in PURPOSES)
    if kind in {"purpose", "session", "identity"}:
        scalar("Sha256", v["request_sha256"])
    if kind == "managed_policy":
        require(
            type(v["policy_arn"]) is str
            and re.fullmatch(
                r"arn:aws:iam::[0-9]{12}:policy/maezo-d7-v2/owner-[0-9a-f-]{36}", v["policy_arn"]
            )
            is not None
        )
        require(type(v["policy_id"]) is str and re.fullmatch(r"ANPA[A-Z0-9]{17}", v["policy_id"]) is not None)
        require(
            type(v["version_id"]) is str
            and re.fullmatch(r"v[1-9][0-9]*", v["version_id"]) is not None
            and len(v["version_id"]) <= 32
        )
        for name in ("created_at_ms", "observed_at_ms"):
            scalar("UInt", v[name])
        require(v["created_at_ms"] <= v["observed_at_ms"])
        require(type(v["document"]) is dict)
        scalar("Sha256", v["document_sha256"])
        require(digest(canonical(v["document"])) == v["document_sha256"])
    elif kind == "purpose":
        role_parts(v["role_arn"])
        _child(v["child"])
        scalar("UInt", v["recorded_at_ms"])
    elif kind == "session":
        require(type(v["request"]) is dict)
        require(digest(canonical(v["request"])) == v["request_sha256"])
        # Field equality against the exact template is checked with its intent.
        expected = "RoleArn RoleSessionName SourceIdentity DurationSeconds " + (
            "PolicyArns" if v["purpose"] == "O" else "Policy"
        )
        exact(v["request"], expected)
        role_parts(v["request"]["RoleArn"])
        for field in ("RoleSessionName", "SourceIdentity"):
            value = v["request"][field]
            require(type(value) is str and re.fullmatch(r"[A-Za-z0-9_+=,.@-]{2,64}", value) is not None)
        if v["purpose"] == "O":
            policies = v["request"]["PolicyArns"]
            require(type(policies) is list and len(policies) == 1)
            policy = exact(policies[0], "arn")["arn"]
            require(type(policy) is str and policy.isascii() and 0 < len(policy) <= 2048)
        else:
            policy = v["request"]["Policy"]
            require(type(policy) is str and policy.isascii() and 0 < len(policy) <= 2048)
            document = exact(parse_wire(policy.encode("ascii")), "Version Statement")
            require(document["Version"] == "2012-10-17" and type(document["Statement"]) is list)
        require(v["request"]["DurationSeconds"] == 900 and type(v["request"]["DurationSeconds"]) is int)
    elif kind == "role_identity":
        _, path, name = role_parts(v["role_arn"])
        _role_id(v["role_id"])
        require(v["path"] == path and v["role_name"] == name, "AUTH_REFUSED")
        for field in ("created_at_ms", "observed_at_ms"):
            scalar("UInt", v[field])
        require(v["created_at_ms"] <= v["observed_at_ms"])
    elif kind == "identity":
        account, _, name = role_parts(v["role_arn"])
        _role_id(v["role_id"])
        prefix = f"arn:aws:sts::{account}:assumed-role/{name}/"
        require(type(v["session_arn"]) is str and v["session_arn"].startswith(prefix))
        session = v["session_arn"][len(prefix) :]
        require(re.fullmatch(r"[A-Za-z0-9_+=,.@-]{2,64}", session) is not None)
        require(v["user_id"] == v["role_id"] + ":" + session, "AUTH_REFUSED")
        require(
            v["caller_account"] == account
            and v["caller_arn"] == v["session_arn"]
            and v["caller_user_id"] == v["user_id"],
            "AUTH_REFUSED",
        )
        for name in ("issued_at_ms", "expires_at_ms"):
            scalar("UInt", v[name])
        require(0 < v["expires_at_ms"] - v["issued_at_ms"] <= 900_000)
        require(type(v["packed_policy_size"]) is int and 0 <= v["packed_policy_size"] <= 100)
    elif kind == "issuance":
        scalar("Sha256", v["claim_sha256"])
        scalar("UInt", v["recorded_at_ms"])
        require(type(v["revision"]) is int and 1 <= v["revision"] <= 64)
        require(type(v["state"]) is str and v["state"] in {*STATES, "UNKNOWN"})
        if v["state"] == "CLAIMED":
            require(v["revision"] == 1 and v["previous_sha256"] is None and v["evidence_sha256"] is None)
        else:
            require(v["revision"] > 1)
            scalar("Sha256", v["previous_sha256"])
            if v["state"] in {"ISSUED", "DELIVERED", "DISPOSED"} or (
                v["state"] == "UNKNOWN" and v["evidence_sha256"] is not None
            ):
                scalar("Sha256", v["evidence_sha256"])
            else:
                require(v["evidence_sha256"] is None)


@dataclass(frozen=True, slots=True)
class V2Record:
    """Canonical value, never an authenticated or effect-capable object."""

    kind: str
    wire: bytes

    def __post_init__(self) -> None:
        require(type(self.kind) is str and self.kind in FIELDS)
        require(type(self.wire) is bytes and 0 < len(self.wire) <= MAX_BYTES)
        v = exact(parse_wire(self.wire), FIELDS[self.kind])
        require(canonical(v) == self.wire)
        _record(self.kind, v)

    def value(self) -> dict[str, Any]:
        return exact(parse_wire(self.wire), FIELDS[self.kind])

    def digest(self) -> str:
        return digest(self.wire)


def _v(intent: V2Record) -> dict[str, Any]:
    require(type(intent) is V2Record and intent.kind == "intent")
    return intent.value()


def role_for(intent: V2Record, purpose: str) -> str:
    v = _v(intent)
    require(type(purpose) is str and purpose in PURPOSES)
    if purpose in {"O", "Q"}:
        return str(v["trust"]["owner_arn" if purpose == "O" else "cloud_reader_arn"])
    return str(
        v["resources"][
            {"W": "writer_arn", "R": "reader_arn", "L": "continuation_arn", "C0": "control_observer_arn"}[
                purpose
            ]
        ]
    )


def _allow(
    actions: list[str] | tuple[str, ...], resources: list[str], condition: Any = None
) -> dict[str, Any]:
    result: dict[str, Any] = {"Effect": "Allow", "Action": sorted(actions), "Resource": sorted(resources)}
    if condition is not None:
        result["Condition"] = condition
    return result


def _policy(statements: list[dict[str, Any]]) -> bytes:
    return canonical({"Version": "2012-10-17", "Statement": statements})


def bootstrap_keys(intent: V2Record) -> list[str]:
    v = _v(intent)
    return sorted(
        [
            "D7OWNER#INSTALLATION#" + v["installation_id"],
            "D7OWNER#SCOPE#" + v["control_scope_id"],
            "D7#" + v["control_scope_id"],
            "D7OWNER#OPERATION#" + v["bootstrap_operation_id"],
        ]
    )


def policy_document(intent: V2Record, purpose: str) -> bytes:
    """Render literal policy bytes; does not evaluate or grant IAM permissions."""
    v = _v(intent)
    require(type(purpose) is str and purpose in PURPOSES)
    t, r = v["trust"], v["resources"]
    table = r["control_table_arn"]
    if purpose == "O":
        roles = [role_for(intent, p) for p in CHILDREN]
        return _policy(
            [
                _allow(META + DATA, [t["enrollment_table_arn"]]),
                _allow(
                    ["dynamodb:PutItem"],
                    [t["enrollment_table_arn"]],
                    {"StringEquals": {"dynamodb:EnclosingOperation": "TransactWriteItems"}},
                ),
                _allow(["iam:CreateRole", "iam:PutRolePolicy"], roles),
                _allow(
                    [
                        "iam:GetRole",
                        "iam:GetRolePolicy",
                        "iam:ListAttachedRolePolicies",
                        "iam:ListRolePolicies",
                    ],
                    roles + [t["owner_arn"], t["enrollment_reader_arn"], t["owner_ingress_arn"]],
                ),
                _allow(["dynamodb:CreateTable", "dynamodb:PutResourcePolicy", *META], [table]),
                _allow(["sts:AssumeRole", "sts:SetSourceIdentity"], roles),
                _allow(["dynamodb:ConditionCheckItem"], [t["enrollment_table_arn"]]),
            ]
        )
    if purpose == "Q":
        return _policy([_allow(CLOUD_READS, ["*"])])
    keys = bootstrap_keys(intent) if purpose in {"W", "R"} else ["D7#" + v["control_scope_id"]]
    condition = {
        "ForAllValues:StringEquals": {"dynamodb:LeadingKeys": keys},
        "Null": {"dynamodb:LeadingKeys": "false"},
    }
    rows = [_allow(META, [table]), _allow(DATA, [table], condition)]
    if purpose in {"W", "L"}:
        rows.append(
            _allow(
                ["dynamodb:PutItem"],
                [table],
                {**condition, "StringEquals": {"dynamodb:EnclosingOperation": "TransactWriteItems"}},
            )
        )
    return _policy(rows)


def bounded_inline(raw: bytes) -> str:
    require(type(raw) is bytes and 0 < len(raw) <= 2048, "UNSUPPORTED_ADAPTER")
    require(raw.isascii(), "UNSUPPORTED_ADAPTER")
    return raw.decode("ascii")


def session_request(intent: V2Record, purpose: str) -> dict[str, Any]:
    v = _v(intent)
    arn = role_for(intent, purpose)
    prefix = {"W": "d7-", "R": "d7-r-", "L": "d7-l-", "C0": "d7-c-", "Q": "d7-q-", "O": "d7-o-"}[purpose]
    request: dict[str, Any] = {
        "RoleArn": arn,
        "RoleSessionName": prefix + v["acquisition_id"],
        "SourceIdentity": v["trust"]["owner_source_identity"],
        "DurationSeconds": 900,
    }
    if purpose == "O":
        policy_arn = v["resources"]["owner_policy_arn"]
        require(len(policy_arn) <= 2048)
        request["PolicyArns"] = [{"arn": policy_arn}]
    else:
        request["Policy"] = bounded_inline(policy_document(intent, purpose))
    return request


def role_trust(intent: V2Record, purpose: str) -> bytes:
    v = _v(intent)
    request = session_request(intent, purpose)
    issuer = v["trust"]["owner_ingress_arn" if purpose in {"O", "Q"} else "owner_arn"]
    return _policy(
        [
            {
                "Effect": "Allow",
                "Principal": {"AWS": issuer},
                "Action": ["sts:AssumeRole", "sts:SetSourceIdentity"],
                "Condition": {
                    "StringEquals": {
                        "sts:RoleSessionName": request["RoleSessionName"],
                        "sts:SourceIdentity": request["SourceIdentity"],
                        "aws:PrincipalArn": issuer,
                    }
                },
            }
        ]
    )


def control_table_policy(intent: V2Record) -> bytes:
    v = _v(intent)
    table, keys = v["resources"]["control_table_arn"], bootstrap_keys(intent)
    w, continuation = role_for(intent, "W"), role_for(intent, "L")
    rows: list[dict[str, Any]] = []

    def deny(sid: str, actions: list[str], condition: Any = None) -> None:
        row = _allow(actions, [table], condition)
        row.update(Effect="Deny", Sid=sid, Principal="*")
        rows.append(row)

    deny(
        "DenyForeignData",
        [
            "dynamodb:" + x
            for x in (
                "BatchGetItem",
                "BatchWriteItem",
                "ConditionCheckItem",
                "DeleteItem",
                "GetItem",
                "PartiQLDelete",
                "PartiQLInsert",
                "PartiQLSelect",
                "PartiQLUpdate",
                "PutItem",
                "Query",
                "Scan",
                "UpdateItem",
            )
        ],
        {"ArnNotEquals": {"aws:PrincipalArn": sorted(role_for(intent, p) for p in CHILDREN)}},
    )
    deny(
        "DenyNonWriterPut",
        ["dynamodb:PutItem"],
        {"ArnNotEquals": {"aws:PrincipalArn": sorted([w, continuation])}},
    )
    deny(
        "DenyDirectPut",
        ["dynamodb:PutItem"],
        {"StringNotEquals": {"dynamodb:EnclosingOperation": "TransactWriteItems"}},
    )
    deny(
        "DenyBootstrapForeignKey",
        ["dynamodb:PutItem"],
        {"ArnEquals": {"aws:PrincipalArn": w}, "ForAnyValue:StringNotEquals": {"dynamodb:LeadingKeys": keys}},
    )
    deny(
        "DenyContinuationForeignKey",
        ["dynamodb:PutItem"],
        {
            "ArnEquals": {"aws:PrincipalArn": continuation},
            "ForAnyValue:StringNotEquals": {"dynamodb:LeadingKeys": ["D7#" + v["control_scope_id"]]},
        },
    )
    deny("DenyMissingWriteKey", ["dynamodb:PutItem"], {"Null": {"dynamodb:LeadingKeys": "true"}})
    deny(
        "DenyOtherDataMutation",
        [
            "dynamodb:" + x
            for x in (
                "BatchWriteItem",
                "ConditionCheckItem",
                "DeleteItem",
                "PartiQLInsert",
                "PartiQLUpdate",
                "PartiQLDelete",
                "UpdateItem",
            )
        ],
    )
    deny("DenyUnusedReadAliases", ["dynamodb:BatchGetItem", "dynamodb:PartiQLSelect", "dynamodb:Scan"])
    return _policy(rows)


def compare_policy(intent: V2Record, purpose: str, document: bytes) -> None:
    """Byte comparison only; no observations, IAM evaluation or authority."""
    expected = control_table_policy(intent) if purpose == "TABLE" else policy_document(intent, purpose)
    require(type(document) is bytes and document == expected, "PRECONDITION_MISMATCH")


def bind_to_intent(intent: V2Record, record: V2Record) -> None:
    """Compare closed values; even a complete match authenticates nothing."""
    v, r = _v(intent), record.value()
    if record.kind == "resources":
        require(r == v["resources"], "SCOPE_REFUSED")
        return
    require(record.kind != "intent" and r["intent_sha256"] == intent.digest(), "SCOPE_REFUSED")
    if record.kind in {"purpose", "session", "managed_policy"}:
        require(r["source_artifact_sha256"] == v["source"]["artifact_sha256"], "PRECONDITION_MISMATCH")
    if record.kind == "managed_policy":
        require(r["policy_arn"] == v["resources"]["owner_policy_arn"], "SCOPE_REFUSED")
        compare_policy(intent, "O", canonical(r["document"]))
        require(v["not_before_ms"] <= r["observed_at_ms"] <= v["deadline_ms"], "PRECONDITION_MISMATCH")
    if record.kind == "role_identity":
        require(r["role_arn"] == role_for(intent, r["purpose"]), "AUTH_REFUSED")
        require(v["not_before_ms"] <= r["observed_at_ms"] <= v["deadline_ms"], "PRECONDITION_MISMATCH")
        if r["purpose"] in {"O", "Q"}:
            field = "owner_role_id" if r["purpose"] == "O" else "cloud_reader_role_id"
            require(r["role_id"] == v["trust"][field], "AUTH_REFUSED")
        else:
            parent_ids = [
                v["trust"][p + "_role_id"]
                for p in ("owner", "enrollment_reader", "owner_ingress", "cloud_reader")
            ]
            require(r["role_id"] not in parent_ids, "AUTH_REFUSED")
    if record.kind in {"purpose", "session", "identity"}:
        request = session_request(intent, r["purpose"])
        require(r["request_sha256"] == digest(canonical(request)), "PRECONDITION_MISMATCH")
        if record.kind == "session":
            require(r["request"] == request, "PRECONDITION_MISMATCH")
        else:
            require(r["role_arn"] == request["RoleArn"], "AUTH_REFUSED")
        if record.kind == "identity":
            account, _, name = role_parts(request["RoleArn"])
            require(
                r["session_arn"]
                == f"arn:aws:sts::{account}:assumed-role/{name}/{request['RoleSessionName']}",
                "AUTH_REFUSED",
            )
            if r["purpose"] in {"O", "Q"}:
                require(
                    r["role_id"]
                    == v["trust"]["owner_role_id" if r["purpose"] == "O" else "cloud_reader_role_id"],
                    "AUTH_REFUSED",
                )
            require(v["not_before_ms"] <= r["issued_at_ms"] < v["deadline_ms"], "PRECONDITION_MISMATCH")
    if record.kind in {"purpose", "issuance"}:
        require(v["not_before_ms"] <= r["recorded_at_ms"] <= v["deadline_ms"], "PRECONDITION_MISMATCH")


def compare_purpose_identity(
    intent: V2Record, purpose: V2Record, identity: V2Record, observed_role_id: str
) -> None:
    """Match supplied identity observations; same-handle provenance is NOT implemented."""
    require(purpose.kind == "purpose" and identity.kind == "identity")
    bind_to_intent(intent, purpose)
    bind_to_intent(intent, identity)
    p, i = purpose.value(), identity.value()
    _role_id(observed_role_id)
    require(all(p[n] == i[n] for n in ("purpose", "role_arn", "request_sha256")), "AUTH_REFUSED")
    require(i["role_id"] == observed_role_id, "AUTH_REFUSED")


def compare_parent_interval(left: V2Record, right: V2Record) -> None:
    require(
        left.kind in {"purpose", "session", "managed_policy"}
        and right.kind in {"purpose", "session", "managed_policy"}
    )
    a, b = left.value(), right.value()
    require(
        all(a[n] == b[n] for n in ("intent_sha256", "parent_interval_id", "source_artifact_sha256")),
        "PRECONDITION_MISMATCH",
    )


def reservation_keys(intent: V2Record) -> tuple[tuple[str, str], ...]:
    """Same permanent v1 collision namespaces; no database/store consumer."""
    v = _v(intent)
    rows = [
        ("D7LOCAL#INSTALLATION#" + v["installation_id"], "RESERVATION"),
        ("D7LOCAL#RUN#" + v["run_id"], "RESERVATION"),
        ("D7LOCAL#SCOPE#" + digest(canonical(v["scope"])), "RESERVATION"),
        ("D7LOCAL#CONTROL_SCOPE#" + v["control_scope_id"], "RESERVATION"),
        ("D7LOCAL#ENROLLMENT#" + v["enrollment_id"], "RESERVATION"),
        ("D7LOCAL#BOOTSTRAP#" + v["bootstrap_operation_id"], "RESERVATION"),
        ("D7LOCAL#ACQUISITION#" + v["acquisition_id"], "CLAIM"),
    ]
    for field in (
        "control_table_arn",
        "writer_arn",
        "reader_arn",
        "continuation_arn",
        "control_observer_arn",
        "owner_policy_arn",
    ):
        rows.append(("D7LOCAL#RESOURCE#" + digest(canonical(v["resources"][field])), "RESERVATION"))
    return tuple(sorted(rows))


def purpose_claim_key(intent: V2Record, purpose: str) -> tuple[str, str, str]:
    """Return logical custody domain/PK/SK only, never a committed claim."""
    v = _v(intent)
    require(type(purpose) is str and purpose in PURPOSES)
    return (
        "PARENT_INGRESS" if purpose in {"O", "Q"} else "ENROLLMENT",
        "D7LOCAL2#ISSUE#" + v["installation_id"] + "#" + purpose,
        "CLAIM",
    )


def compare_successor(previous: V2Record, current: V2Record) -> None:
    require(previous.kind == current.kind == "issuance")
    p, c = previous.value(), current.value()
    require(all(p[n] == c[n] for n in ("intent_sha256", "purpose", "claim_sha256")), "PRECONDITION_MISMATCH")
    require(c["revision"] == p["revision"] + 1 and c["previous_sha256"] == previous.digest())
    require(c["recorded_at_ms"] >= p["recorded_at_ms"])
    require(p["state"] not in {"UNKNOWN", "DISPOSED"}, "PRECONDITION_MISMATCH")
    require(
        c["state"] == "UNKNOWN" or c["state"] == STATES[STATES.index(p["state"]) + 1], "PRECONDITION_MISMATCH"
    )


def compare_managed_snapshots(intent: V2Record, retained: V2Record, current: V2Record) -> None:
    """Match supplied complete version tuples; does NOT enforce a no-edit interval."""
    require(retained.kind == current.kind == "managed_policy")
    bind_to_intent(intent, retained)
    bind_to_intent(intent, current)
    before, after = retained.value(), current.value()
    require(after["observed_at_ms"] >= before["observed_at_ms"], "PRECONDITION_MISMATCH")
    require(
        {k: v for k, v in before.items() if k != "observed_at_ms"}
        == {k: v for k, v in after.items() if k != "observed_at_ms"},
        "PRECONDITION_MISMATCH",
    )


def compare_role_session(intent: V2Record, role: V2Record, session: V2Record) -> None:
    """Compare observed creation role to observed session; provenance remains external."""
    require(role.kind == "role_identity" and session.kind == "identity")
    bind_to_intent(intent, role)
    bind_to_intent(intent, session)
    r, s = role.value(), session.value()
    require(all(r[k] == s[k] for k in ("purpose", "role_arn", "role_id")), "AUTH_REFUSED")
    require(r["created_at_ms"] <= s["issued_at_ms"], "PRECONDITION_MISMATCH")
