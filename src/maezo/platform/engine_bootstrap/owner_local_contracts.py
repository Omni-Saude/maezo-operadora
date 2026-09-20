"""ADR-0056 inert local-owner records. Parsing never grants owner authority.

No credential, network, signing, database or durable journal operation lives here.
The separate concrete producer must authenticate records and observe actual facts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .controller_storage import canonical, decode64, digest, exact, parse_wire, require, scalar

PROFILE = "maezo.d7-local-owner.fresh.v1"
PREFIX = "maezo.d7-local-owner."
MAX_BYTES = 131_072
MAX_INTENT_MS = 900_000
SIGNATURE_DOMAIN = b"maezo.d7-local-owner.signature.v1\0"
OPERATIONS = ["bootstrap_scope", "install_d", "readback"]
IDENTIFIERS = "enrollment_id installation_id run_id control_scope_id bootstrap_operation_id acquisition_id"
INTENT_FIELDS = (
    "protocol mode profile "
    + IDENTIFIERS
    + " scope trust resources source not_before_ms deadline_ms operations"
)
ENROLLMENT_FIELDS = (
    "protocol intent_sha256 enrollment_sha256 acquisition_id owner_session_arn writer_role_id "
    "reader_role_id control_table_id writer_creation_sha256 reader_creation_sha256 "
    "table_creation_sha256 policy_observation_sha256 pg_observation_sha256 completed_at_ms"
)
ACQUISITION_FIELDS = (
    "protocol intent_sha256 acquisition_id revision state previous_sha256 evidence_sha256 recorded_at_ms"
)
STATES = (
    "CLAIMED",
    "WRITER_CREATE_STARTED",
    "WRITER_CREATED",
    "READER_CREATE_STARTED",
    "READER_CREATED",
    "TABLE_CREATE_STARTED",
    "TABLE_CREATED",
    "ENROLLED",
    "ISSUE_STARTED",
    "ISSUED",
    "READER_ISSUE_STARTED",
    "READER_ISSUED",
    "BOOTSTRAP_STARTED",
    "BOOTSTRAPPED",
    "INSTALL_STARTED",
    "COMMITTED",
)
OUTCOMES = frozenset(
    {
        "WRITER_CREATED",
        "READER_CREATED",
        "TABLE_CREATED",
        "ENROLLED",
        "ISSUED",
        "READER_ISSUED",
        "BOOTSTRAPPED",
        "COMMITTED",
    }
)


def _role_id(value: Any) -> None:
    require(type(value) is str and re.fullmatch(r"AROA[A-Z0-9]{17}", value) is not None)


def _intent(v: dict[str, Any]) -> None:
    exact(v, INTENT_FIELDS)
    require(v["protocol"] == PREFIX + "intent.v1")
    require(v["mode"] == "PUBLIC_SYNTHETIC" and v["profile"] == PROFILE, "AUTH_REFUSED")
    require(v["operations"] == OPERATIONS, "UNSUPPORTED_ADAPTER")
    for name in IDENTIFIERS.split():
        scalar("Uuid", v[name])
    scalar("Scope", v["scope"])
    scope = v["scope"]
    trust = exact(
        v["trust"],
        "key_id public_key_sha256 owner_arn owner_role_id enrollment_table_arn enrollment_table_id "
        "enrollment_reader_arn enrollment_reader_role_id owner_ingress_arn owner_ingress_role_id "
        "owner_source_identity",
    )
    scalar("Id", trust["key_id"])
    scalar("Sha256", trust["public_key_sha256"])
    _role_id(trust["owner_role_id"])
    for name in ("enrollment_reader_role_id", "owner_ingress_role_id"):
        _role_id(trust[name])
    require(
        len(
            {
                trust[n]
                for n in (
                    "owner_role_id",
                    "enrollment_reader_role_id",
                    "owner_ingress_role_id",
                )
            }
        )
        == 3,
        "AUTH_REFUSED",
    )
    require(
        type(trust["owner_source_identity"]) is str
        and re.fullmatch(
            r"[A-Za-z0-9_+=,.@-]{2,64}",
            trust["owner_source_identity"],
        )
        is not None
    )
    require(not trust["owner_source_identity"].startswith("aws:"))
    scalar("Uuid", trust["enrollment_table_id"])
    for name in ("owner_arn", "enrollment_table_arn"):
        scalar("Arn", trust[name])
    role_prefix = f"arn:aws:iam::{scope['account']}:role/"
    require(trust["owner_arn"].startswith(role_prefix))
    require(re.fullmatch(r"[A-Za-z0-9_+=,.@/-]+", trust["owner_arn"][len(role_prefix) :]) is not None)
    for name in ("enrollment_reader_arn", "owner_ingress_arn"):
        scalar("Arn", trust[name])
        require(trust[name].startswith(role_prefix))
        require(re.fullmatch(r"[A-Za-z0-9_+=,.@/-]+", trust[name][len(role_prefix) :]) is not None)
    require(
        len(
            {
                trust[n]
                for n in (
                    "owner_arn",
                    "enrollment_reader_arn",
                    "owner_ingress_arn",
                )
            }
        )
        == 3,
        "AUTH_REFUSED",
    )
    table_prefix = f"arn:aws:dynamodb:{scope['region']}:{scope['account']}:table/"
    require(trust["enrollment_table_arn"].startswith(table_prefix))
    require(
        re.fullmatch(r"[A-Za-z0-9_.-]{3,255}", trust["enrollment_table_arn"][len(table_prefix) :]) is not None
    )
    resources = exact(v["resources"], "control_table_arn writer_arn reader_arn")
    base = "maezo-d7-" + v["installation_id"]
    expected = {
        "control_table_arn": table_prefix + base + "-control",
        "writer_arn": role_prefix + "maezo-d7-synthetic/" + base + "-writer",
        "reader_arn": role_prefix + "maezo-d7-synthetic/" + base + "-reader",
    }
    require(resources == expected, "SCOPE_REFUSED")
    require(
        all(
            trust[n] not in resources.values()
            for n in (
                "owner_arn",
                "enrollment_reader_arn",
                "owner_ingress_arn",
            )
        ),
        "AUTH_REFUSED",
    )
    require(trust["enrollment_table_arn"] != resources["control_table_arn"], "AUTH_REFUSED")
    source = exact(
        v["source"], "git_sha tree_sha artifact_sha256 manifest_sha256 cib_abi_sha256 policy_profile_sha256"
    )
    for name, value in source.items():
        scalar("GitOid" if name in {"git_sha", "tree_sha"} else "Sha256", value)
    for name in ("not_before_ms", "deadline_ms"):
        scalar("UInt", v[name])
    require(0 < v["deadline_ms"] - v["not_before_ms"] <= MAX_INTENT_MS)


def _enrollment(v: dict[str, Any]) -> None:
    exact(v, ENROLLMENT_FIELDS)
    require(v["protocol"] == PREFIX + "enrollment-operation.v1")
    for name in ENROLLMENT_FIELDS.split():
        if name.endswith("_sha256"):
            scalar("Sha256", v[name])
    for name in ("acquisition_id", "control_table_id"):
        scalar("Uuid", v[name])
    for name in ("writer_role_id", "reader_role_id"):
        _role_id(v[name])
    require(v["writer_role_id"] != v["reader_role_id"], "AUTH_REFUSED")
    scalar("Arn", v["owner_session_arn"])
    require(
        re.fullmatch(
            r"arn:aws:sts::[0-9]{12}:assumed-role/[A-Za-z0-9_+=,.@-]+/"
            r"[A-Za-z0-9_+=,.@-]{2,64}",
            v["owner_session_arn"],
        )
        is not None
    )
    scalar("UInt", v["completed_at_ms"])


def _acquisition(v: dict[str, Any]) -> None:
    exact(v, ACQUISITION_FIELDS)
    require(v["protocol"] == PREFIX + "acquisition.v1")
    scalar("Sha256", v["intent_sha256"])
    scalar("Uuid", v["acquisition_id"])
    scalar("UInt", v["recorded_at_ms"])
    require(type(v["revision"]) is int and 1 <= v["revision"] <= 64)
    state = v["state"]
    require(type(state) is str and state in {*STATES, "UNKNOWN"})
    if state == "CLAIMED":
        require(v["revision"] == 1 and v["previous_sha256"] is None)
    else:
        require(v["revision"] > 1)
        scalar("Sha256", v["previous_sha256"])
    if state in OUTCOMES or (state == "UNKNOWN" and v["evidence_sha256"] is not None):
        scalar("Sha256", v["evidence_sha256"])
    else:
        require(v["evidence_sha256"] is None)


@dataclass(frozen=True, slots=True)
class LocalOwnerRecord:
    """A well-shaped value, explicitly NOT an authenticated capability."""

    kind: str
    wire: bytes

    def __post_init__(self) -> None:
        require(
            type(self.kind) is str
            and self.kind
            in {
                "intent",
                "enrollment_operation",
                "acquisition",
            }
        )
        require(type(self.wire) is bytes and 0 < len(self.wire) <= MAX_BYTES)
        v = parse_wire(self.wire)
        require(type(v) is dict)
        if self.kind == "intent":
            _intent(v)
        elif self.kind == "enrollment_operation":
            _enrollment(v)
        else:
            _acquisition(v)

    def value(self) -> dict[str, Any]:
        # Re-parse immutable bytes: caller mutation cannot change the retained record.
        return exact(
            parse_wire(self.wire),
            {
                "intent": INTENT_FIELDS,
                "enrollment_operation": ENROLLMENT_FIELDS,
                "acquisition": ACQUISITION_FIELDS,
            }[self.kind],
        )

    def digest(self) -> str:
        return digest(self.wire)


def signed_shape(raw: bytes) -> LocalOwnerRecord:
    """Validate envelope/encoding only; deliberately performs NO authentication."""
    require(type(raw) is bytes and 0 < len(raw) <= MAX_BYTES)
    v = exact(parse_wire(raw), "protocol kind key_id body signature")
    require(v["protocol"] == PREFIX + "signed.v1")
    scalar("Id", v["key_id"])
    scalar("Signature", v["signature"])
    require(len(decode64(v["signature"])) == 64)
    record = LocalOwnerRecord(v["kind"], canonical(v["body"]))
    if record.kind == "intent":
        require(v["key_id"] == record.value()["trust"]["key_id"], "AUTH_REFUSED")
    return record


def signing_bytes(record: LocalOwnerRecord, key_id: str) -> bytes:
    """Public domain-separated message construction, no key access/signing."""
    scalar("Id", key_id)
    if record.kind == "intent":
        require(key_id == record.value()["trust"]["key_id"], "AUTH_REFUSED")
    return SIGNATURE_DOMAIN + canonical(
        {
            "protocol": PREFIX + "signed.v1",
            "kind": record.kind,
            "key_id": key_id,
            "body": record.value(),
        }
    )


def reservation_keys(intent: LocalOwnerRecord) -> tuple[tuple[str, str], ...]:
    require(intent.kind == "intent")
    v = intent.value()
    keys = [
        ("D7LOCAL#" + name + "#" + v[field], "RESERVATION")
        for name, field in (
            ("INSTALLATION", "installation_id"),
            ("RUN", "run_id"),
            ("CONTROL_SCOPE", "control_scope_id"),
            ("ENROLLMENT", "enrollment_id"),
            ("BOOTSTRAP", "bootstrap_operation_id"),
        )
    ]
    keys.append(("D7LOCAL#SCOPE#" + digest(canonical(v["scope"])), "RESERVATION"))
    keys.extend(
        ("D7LOCAL#RESOURCE#" + digest(canonical(arn)), "RESERVATION") for arn in v["resources"].values()
    )
    keys.append(("D7LOCAL#ACQUISITION#" + v["acquisition_id"], "CLAIM"))
    return tuple(sorted(keys))


def require_disabled_ttl(description: Any) -> None:
    """Closed DescribeTimeToLive value check, never response or table authentication."""
    require(type(description) is dict)
    fields = ["TimeToLiveStatus"]
    if "AttributeName" in description:
        fields.append("AttributeName")
    value = exact(description, fields)
    require(
        type(value["TimeToLiveStatus"]) is str and value["TimeToLiveStatus"] == "DISABLED",
        "AUTH_REFUSED",
    )
    if "AttributeName" in value:
        name = value["AttributeName"]
        require(type(name) is str and 1 <= len(name) <= 255)


def session_policy(intent: LocalOwnerRecord, purpose: str) -> bytes:
    """Exact inert STS policy template; no authority, IAM evaluation or issuance."""
    require(intent.kind == "intent" and purpose in {"owner", "writer", "reader"})
    v = intent.value()
    t, r = v["trust"], v["resources"]

    def allow(actions: list[str], resources: list[str], condition: Any = None) -> dict[str, Any]:
        statement: dict[str, Any] = {
            "Effect": "Allow",
            "Action": sorted(actions),
            "Resource": sorted(resources),
        }
        if condition is not None:
            statement["Condition"] = condition
        return statement

    reads = [
        "dynamodb:DescribeTable",
        "dynamodb:DescribeTimeToLive",
        "dynamodb:GetItem",
        "dynamodb:GetResourcePolicy",
        "dynamodb:Query",
    ]
    if purpose == "owner":
        statements = [
            allow(reads, [t["enrollment_table_arn"]]),
            allow(
                ["dynamodb:PutItem"],
                [t["enrollment_table_arn"]],
                {
                    "StringEquals": {"dynamodb:EnclosingOperation": "TransactWriteItems"},
                },
            ),
            allow(["iam:CreateRole", "iam:PutRolePolicy"], [r["writer_arn"], r["reader_arn"]]),
            allow(
                ["iam:GetRole", "iam:GetRolePolicy", "iam:ListAttachedRolePolicies", "iam:ListRolePolicies"],
                [
                    t["owner_arn"],
                    t["enrollment_reader_arn"],
                    t["owner_ingress_arn"],
                    r["writer_arn"],
                    r["reader_arn"],
                ],
            ),
            allow(
                [
                    "dynamodb:CreateTable",
                    "dynamodb:PutResourcePolicy",
                    "dynamodb:DescribeTable",
                    "dynamodb:DescribeTimeToLive",
                    "dynamodb:GetResourcePolicy",
                ],
                [r["control_table_arn"]],
            ),
            allow(["sts:AssumeRole", "sts:SetSourceIdentity"], [r["writer_arn"], r["reader_arn"]]),
            # The prior immutable EVENT is a separate item. This action does not
            # support PutItem's dynamodb:EnclosingOperation condition key.
            allow(["dynamodb:ConditionCheckItem"], [t["enrollment_table_arn"]]),
        ]
    else:
        statements = [allow(reads, [r["control_table_arn"]])]
        if purpose == "writer":
            keys = sorted(
                [
                    "D7OWNER#INSTALLATION#" + v["installation_id"],
                    "D7OWNER#SCOPE#" + v["control_scope_id"],
                    "D7#" + v["control_scope_id"],
                    "D7OWNER#OPERATION#" + v["bootstrap_operation_id"],
                ]
            )
            statements.append(
                allow(
                    ["dynamodb:PutItem"],
                    [r["control_table_arn"]],
                    {
                        "StringEquals": {"dynamodb:EnclosingOperation": "TransactWriteItems"},
                        "ForAllValues:StringEquals": {"dynamodb:LeadingKeys": keys},
                        "Null": {"dynamodb:LeadingKeys": "false"},
                    },
                )
            )
    raw = canonical({"Version": "2012-10-17", "Statement": statements})
    require(len(raw) <= 2048, "UNSUPPORTED_ADAPTER")
    return raw


def event_key(record: LocalOwnerRecord) -> tuple[str, str]:
    require(record.kind == "acquisition")
    v = record.value()
    return "D7LOCAL#ACQUISITION#" + v["acquisition_id"], f"EVENT#{v['revision']:010d}"


def bind_to_intent(intent: LocalOwnerRecord, record: LocalOwnerRecord) -> None:
    """Structural association only; live trust/receipt proof remains mandatory."""
    require(intent.kind == "intent" and record.kind in {"enrollment_operation", "acquisition"})
    i, r = intent.value(), record.value()
    require(
        r["intent_sha256"] == intent.digest() and r["acquisition_id"] == i["acquisition_id"], "SCOPE_REFUSED"
    )
    when = r["completed_at_ms"] if record.kind == "enrollment_operation" else r["recorded_at_ms"]
    # UNKNOWN may be recorded after expiry to retain failure custody; never admits an effect.
    require(when >= i["not_before_ms"])
    if record.kind != "acquisition" or r["state"] != "UNKNOWN":
        require(when < i["deadline_ms"], "AUTH_REFUSED")
    if record.kind == "enrollment_operation":
        require(
            i["trust"]["owner_role_id"]
            not in {
                r["writer_role_id"],
                r["reader_role_id"],
            },
            "AUTH_REFUSED",
        )
        role_name = i["trust"]["owner_arn"].rsplit("/", 1)[1]
        expected = f"arn:aws:sts::{i['scope']['account']}:assumed-role/{role_name}/"
        require(r["owner_session_arn"].startswith(expected), "AUTH_REFUSED")
        require(r["control_table_id"] != i["trust"]["enrollment_table_id"], "SCOPE_REFUSED")


def validate_successor(previous: LocalOwnerRecord, current: LocalOwnerRecord) -> None:
    """Validate a proposed journal edge; this cannot commit or win the claim."""
    require(previous.kind == current.kind == "acquisition")
    p, c = previous.value(), current.value()
    require(p["state"] not in {"UNKNOWN", "COMMITTED"}, "AUTH_REFUSED")
    require(
        c["intent_sha256"] == p["intent_sha256"] and c["acquisition_id"] == p["acquisition_id"],
        "SCOPE_REFUSED",
    )
    require(c["revision"] == p["revision"] + 1 and c["previous_sha256"] == previous.digest())
    require(c["recorded_at_ms"] >= p["recorded_at_ms"])
    require(c["state"] == "UNKNOWN" or c["state"] == STATES[STATES.index(p["state"]) + 1], "AUTH_REFUSED")
