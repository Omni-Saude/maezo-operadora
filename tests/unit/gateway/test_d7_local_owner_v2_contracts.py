"""Pure contract refusals: test values never constitute enrolled owner authority."""

from __future__ import annotations

import pytest

from maezo.platform.engine_bootstrap import owner_local_v2_contracts as c
from maezo.platform.engine_bootstrap.controller_storage import Refusal, canonical, digest, parse_wire


def uid(n=1):
    return f"00000000-0000-4000-8000-{n:012d}"


def body():
    role = "arn:aws:iam::123456789012:role/protected/"
    return {
        "protocol": c.PREFIX + "intent.v2",
        "profile": c.PROFILE,
        "mode": "PUBLIC_SYNTHETIC",
        "topology": c.PENDING,
        **{n: uid(i) for i, n in enumerate(c.IDS.split(), 1)},
        "scope": {
            "account": "123456789012",
            "region": "us-east-1",
            "tenant": "synthetic",
            "environment": "isolated",
            "engine_name": "cib",
            "database_binding": {
                "endpoint_host": "database.example.invalid",
                "endpoint_port": 5432,
                "ca_sha256": "a" * 64,
                "server_identity": "database.example.invalid",
                "database_name": "synthetic",
                "database_oid": 100,
                "schema_name": "engine",
                "schema_oid": 200,
                "database_incarnation": uid(90),
            },
        },
        "trust": {
            "key_id": "parent",
            "public_key_sha256": "b" * 64,
            "owner_arn": role + "owner",
            "owner_role_id": "AROA" + "A" * 17,
            "enrollment_reader_arn": role + "enrollment-reader",
            "enrollment_reader_role_id": "AROA" + "B" * 17,
            "owner_ingress_arn": role + "ingress",
            "owner_ingress_role_id": "AROA" + "C" * 17,
            "cloud_reader_arn": role + "cloud-reader",
            "cloud_reader_role_id": "AROA" + "D" * 17,
            "enrollment_table_arn": "arn:aws:dynamodb:us-east-1:123456789012:table/independent-enrollment",
            "enrollment_table_id": uid(91),
            "owner_source_identity": "independent-owner",
        },
        "resources": c.resource_value(uid(2), "123456789012", "us-east-1"),
        "source": {n: "a" * (40 if n in {"git_sha", "tree_sha"} else 64) for n in c.SOURCE.split()},
        "not_before_ms": 1000,
        "deadline_ms": 901000,
        "operations": ["bootstrap_scope", "install_d", "initial_reconcile", "reserve_initial"],
    }


def intent(value=None):
    return c.V2Record("intent", canonical(body() if value is None else value))


def session(i, purpose="L"):
    request = c.session_request(i, purpose)
    return {
        "protocol": c.PREFIX + "session.v2",
        "intent_sha256": i.digest(),
        "parent_interval_id": uid(50),
        "purpose": purpose,
        "request": request,
        "request_sha256": digest(canonical(request)),
        "source_artifact_sha256": i.value()["source"]["artifact_sha256"],
    }


def purpose(i, name="L"):
    return {
        "protocol": c.PREFIX + "purpose.v2",
        "intent_sha256": i.digest(),
        "parent_interval_id": uid(50),
        "purpose": name,
        "role_arn": c.role_for(i, name),
        "request_sha256": digest(canonical(c.session_request(i, name))),
        "source_artifact_sha256": i.value()["source"]["artifact_sha256"],
        "recorded_at_ms": 2000,
        "child": {
            "instance_id": uid(30),
            "boot_id": uid(31),
            "pid": 101,
            "start_ticks": 500,
            "uid": 501,
            "executable_sha256": "e" * 64,
            "channel_id": uid(32),
        },
    }


def identity(i, name="L"):
    request = c.session_request(i, name)
    account, _, role_name = c.role_parts(request["RoleArn"])
    rid = i.value()["trust"].get(
        {"O": "owner_role_id", "Q": "cloud_reader_role_id"}.get(name, ""), "AROA" + "F" * 17
    )
    arn = f"arn:aws:sts::{account}:assumed-role/{role_name}/{request['RoleSessionName']}"
    user = rid + ":" + request["RoleSessionName"]
    return {
        "protocol": c.PREFIX + "identity.v2",
        "intent_sha256": i.digest(),
        "purpose": name,
        "request_sha256": digest(canonical(request)),
        "role_arn": request["RoleArn"],
        "role_id": rid,
        "session_arn": arn,
        "user_id": user,
        "caller_account": account,
        "caller_arn": arn,
        "caller_user_id": user,
        "issued_at_ms": 2000,
        "expires_at_ms": 902000,
        "packed_policy_size": 10,
    }


def managed(i):
    document = parse_wire(c.policy_document(i, "O"))
    return {
        "protocol": c.PREFIX + "managed-policy.v2",
        "intent_sha256": i.digest(),
        "parent_interval_id": uid(50),
        "source_artifact_sha256": i.value()["source"]["artifact_sha256"],
        "policy_arn": i.value()["resources"]["owner_policy_arn"],
        "policy_id": "ANPA" + "G" * 17,
        "version_id": "v1",
        "created_at_ms": 100,
        "observed_at_ms": 1500,
        "document": document,
        "document_sha256": digest(canonical(document)),
    }


def record(kind, v):
    return c.V2Record(kind, canonical(v))


@pytest.mark.parametrize("kind", ["intent", "resources", "managed_policy", "purpose", "session", "identity"])
def test_closed_records_reject_extra_fields(kind):
    i = intent()
    value = {
        "intent": body(),
        "resources": body()["resources"],
        "managed_policy": managed(i),
        "purpose": purpose(i),
        "session": session(i),
        "identity": identity(i),
    }[kind]
    value["verified"] = True
    with pytest.raises(Refusal):
        record(kind, value)


@pytest.mark.parametrize(
    "field,value",
    [
        ("mode", "PRODUCTION"),
        ("topology", "QUALIFIED"),
        ("topology", "EMPTY_CLUSTER_NO_SLR"),
        ("profile", "maezo.d7-local-owner.fresh.v1"),
        ("operations", ["bootstrap_scope", "install_d", "prepare_generation"]),
    ],
)
def test_topology_and_production_cannot_be_admitted(field, value):
    v = body()
    v[field] = value
    with pytest.raises(Refusal):
        intent(v)


def test_v1_parser_does_not_accept_new_intent():
    from maezo.platform.engine_bootstrap.owner_local_contracts import LocalOwnerRecord

    with pytest.raises(Refusal):
        LocalOwnerRecord("intent", canonical(body()))


def test_canonical_and_immutable_value_boundary():
    i = intent()
    changed = i.value()
    changed["topology"] = "QUALIFIED"
    assert i.value()["topology"] == c.PENDING
    with pytest.raises(Refusal):
        c.V2Record("intent", b" " + i.wire)
    with pytest.raises(Refusal):
        c.V2Record("intent", b'{"protocol":1,"protocol":2}')


@pytest.mark.parametrize(
    "field",
    [
        "writer_arn",
        "reader_arn",
        "continuation_arn",
        "control_observer_arn",
        "owner_policy_arn",
        "control_table_arn",
    ],
)
def test_resource_alias_or_other_installation_refused(field):
    v = body()
    v["resources"][field] = v["resources"][field].replace(uid(2), uid(99))
    with pytest.raises(Refusal):
        intent(v)


def test_iam_api_fields_and_path_free_sts_identity():
    i = intent()
    arn = c.role_for(i, "L")
    assert c.role_parts(arn) == ("123456789012", "/maezo-d7-v2/", "l-" + uid(2))
    other = c.resource_value(uid(99), "123456789012", "us-east-1")
    assert c.role_parts(other["continuation_arn"])[2] != c.role_parts(arn)[2]
    r = record("identity", identity(i))
    c.bind_to_intent(i, r)
    assert "/maezo-d7-v2/" not in r.value()["session_arn"]
    bad = identity(i)
    bad["session_arn"] = bad["session_arn"].replace("assumed-role/", "assumed-role/maezo-d7-v2/")
    bad["caller_arn"] = bad["session_arn"]
    with pytest.raises(Refusal):
        record("identity", bad)


@pytest.mark.parametrize(
    "arn",
    [
        "arn:aws:iam::123456789012:role/a//b",
        "arn:aws:iam::123456789012:role/../b",
        "arn:aws:iam::123456789012:role/" + "b" * 65,
        "arn:aws-cn:iam::123456789012:role/b",
    ],
)
def test_role_shape_refuses_unsupported_alias(arn):
    with pytest.raises(Refusal):
        c.role_parts(arn)


@pytest.mark.parametrize("name", c.PURPOSES)
def test_each_purpose_has_exact_request_and_identity(name):
    i = intent()
    s = record("session", session(i, name))
    p = record("purpose", purpose(i, name))
    ident = record("identity", identity(i, name))
    c.bind_to_intent(i, s)
    c.compare_parent_interval(s, p)
    c.compare_purpose_identity(i, p, ident, ident.value()["role_id"])
    trust = parse_wire(c.role_trust(i, name))["Statement"]
    assert len(trust) == 1
    assert (
        trust[0]["Condition"]["StringEquals"]["sts:RoleSessionName"]
        == s.value()["request"]["RoleSessionName"]
    )
    if name == "O":
        assert set(s.value()["request"]) - {
            "RoleArn",
            "RoleSessionName",
            "SourceIdentity",
            "DurationSeconds",
        } == {"PolicyArns"}
    else:
        assert len(s.value()["request"]["Policy"].encode()) <= 2048


@pytest.mark.parametrize(
    "field,value",
    [
        ("caller_account", "999999999999"),
        ("caller_user_id", "AROA" + "Z" * 17 + ":same"),
        ("packed_policy_size", 101),
        ("packed_policy_size", True),
        ("expires_at_ms", 902001),
    ],
)
def test_identity_rejects_wrong_actor_or_expiry(field, value):
    v = identity(intent())
    v[field] = value
    with pytest.raises(Refusal):
        record("identity", v)


def test_same_role_name_wrong_role_incarnation_refused():
    i = intent()
    p = record("purpose", purpose(i))
    r = record("identity", identity(i))
    with pytest.raises(Refusal):
        c.compare_purpose_identity(i, p, r, "AROA" + "Z" * 17)
    v = identity(i)
    v["session_arn"] = v["session_arn"].replace("d7-l-", "d7-r-")
    v["caller_arn"] = v["session_arn"]
    v["user_id"] = v["user_id"].replace("d7-l-", "d7-r-")
    v["caller_user_id"] = v["user_id"]
    r = record("identity", v)
    with pytest.raises(Refusal):
        c.bind_to_intent(i, r)


def test_cross_intent_source_and_parent_interval_refusal():
    i = intent()
    s = record("session", session(i))
    other = body()
    other["run_id"] = uid(99)
    with pytest.raises(Refusal):
        c.bind_to_intent(intent(other), s)
    v = session(i)
    v["source_artifact_sha256"] = "b" * 64
    with pytest.raises(Refusal):
        c.bind_to_intent(i, record("session", v))
    p = purpose(i)
    p["parent_interval_id"] = uid(99)
    with pytest.raises(Refusal):
        c.compare_parent_interval(s, record("purpose", p))


@pytest.mark.parametrize("mutation", ["duration", "tags", "managed", "policy", "source"])
def test_session_request_substitution_is_not_an_intent_binding(mutation):
    i = intent()
    v = session(i)
    if mutation == "duration":
        v["request"]["DurationSeconds"] = 3600
    elif mutation == "tags":
        v["request"]["Tags"] = []
    elif mutation == "managed":
        v["request"]["PolicyArns"] = []
    elif mutation == "policy":
        v["request"]["Policy"] = (
            '{"Statement":[{"Action":"*","Effect":"Allow","Resource":"*"}],"Version":"2012-10-17"}'
        )
    else:
        v["request"]["SourceIdentity"] = "other"
    v["request_sha256"] = digest(canonical(v["request"]))
    with pytest.raises(Refusal):
        c.bind_to_intent(i, record("session", v))


def test_literal_policy_principals_and_separate_key_ceilings():
    i = intent()
    table = parse_wire(c.control_table_policy(i))
    rows = {r["Sid"]: r for r in table["Statement"]}
    assert len(rows) == 8
    assert rows["DenyForeignData"]["Condition"]["ArnNotEquals"]["aws:PrincipalArn"] == sorted(
        c.role_for(i, p) for p in c.CHILDREN
    )
    assert c.role_for(i, "Q") not in rows["DenyForeignData"]["Condition"]["ArnNotEquals"]["aws:PrincipalArn"]
    assert rows["DenyNonWriterPut"]["Condition"]["ArnNotEquals"]["aws:PrincipalArn"] == sorted(
        [c.role_for(i, "W"), c.role_for(i, "L")]
    )
    assert rows["DenyContinuationForeignKey"]["Condition"]["ForAnyValue:StringNotEquals"][
        "dynamodb:LeadingKeys"
    ] == ["D7#" + i.value()["control_scope_id"]]
    assert "dynamodb:ConditionCheckItem" in rows["DenyOtherDataMutation"]["Action"]
    assert set(rows["DenyUnusedReadAliases"]["Action"]) == {
        "dynamodb:BatchGetItem",
        "dynamodb:PartiQLSelect",
        "dynamodb:Scan",
    }
    for name in ("R", "C0"):
        actions = {a for row in parse_wire(c.policy_document(i, name))["Statement"] for a in row["Action"]}
        assert actions == set(c.META + c.DATA)
    assert parse_wire(c.policy_document(i, "L"))["Statement"][-1]["Action"] == ["dynamodb:PutItem"]


def test_exact_policy_comparison_rejects_observer_omission_and_widening():
    i = intent()
    v = parse_wire(c.control_table_policy(i))
    v["Statement"][0]["Condition"]["ArnNotEquals"]["aws:PrincipalArn"].remove(c.role_for(i, "C0"))
    with pytest.raises(Refusal):
        c.compare_policy(i, "TABLE", canonical(v))
    v = parse_wire(c.policy_document(i, "L"))
    v["Statement"][-1]["Resource"] = ["*"]
    with pytest.raises(Refusal):
        c.compare_policy(i, "L", canonical(v))
    c.compare_policy(i, "L", c.policy_document(i, "L"))


def test_owner_overflow_and_exact_managed_document():
    i = intent()
    assert len(c.policy_document(i, "O")) == 2376
    with pytest.raises(Refusal):
        c.bounded_inline(c.policy_document(i, "O"))
    assert len(c.bounded_inline(b" " * 2048)) == 2048
    with pytest.raises(Refusal):
        c.bounded_inline(b" " * 2049)
    with pytest.raises(Refusal):
        c.bounded_inline("é".encode())
    m = record("managed_policy", managed(i))
    c.bind_to_intent(i, m)
    owner = m.value()["document"]["Statement"]
    assert (
        len(owner) == 7
        and owner[-1]["Action"] == ["dynamodb:ConditionCheckItem"]
        and "Condition" not in owner[-1]
    )
    v = managed(i)
    v["document"]["Statement"][2]["Resource"] = ["*"]
    v["document_sha256"] = digest(canonical(v["document"]))
    with pytest.raises(Refusal):
        c.bind_to_intent(i, record("managed_policy", v))


def test_managed_policy_version_is_observation_not_approval():
    i = intent()
    v = managed(i)
    v["version_id"] = "v2"
    # Shape is not permission to accept a changed version. Full current tuple is
    # compared against independently retained creation data by later source.
    newer = record("managed_policy", v)
    assert newer.digest() != record("managed_policy", managed(i)).digest()
    v["version_id"] = "latest"
    with pytest.raises(Refusal):
        record("managed_policy", v)


def test_permanent_cross_version_anchors_and_purpose_domains():
    i = intent()
    keys = c.reservation_keys(i)
    assert len(keys) == len(set(keys)) == 13
    assert ("D7LOCAL#INSTALLATION#" + uid(2), "RESERVATION") in keys
    assert len({c.purpose_claim_key(i, p) for p in c.PURPOSES}) == 6
    assert c.purpose_claim_key(i, "O")[0] == c.purpose_claim_key(i, "Q")[0] == "PARENT_INGRESS"
    assert c.purpose_claim_key(i, "L")[0] == "ENROLLMENT"
    v = body()
    v["acquisition_id"] = uid(99)
    assert c.purpose_claim_key(i, "L") == c.purpose_claim_key(intent(v), "L")


def event(i, state="CLAIMED", previous=None):
    return record(
        "issuance",
        {
            "protocol": c.PREFIX + "issuance.v2",
            "intent_sha256": i.digest(),
            "purpose": "L",
            "claim_sha256": "a" * 64,
            "revision": 1 if previous is None else previous.value()["revision"] + 1,
            "state": state,
            "previous_sha256": None if previous is None else previous.digest(),
            "evidence_sha256": "e" * 64 if state in {"ISSUED", "DELIVERED", "DISPOSED"} else None,
            "recorded_at_ms": 2000,
        },
    )


def test_state_chain_unknown_and_disposal_are_terminal_values():
    i = intent()
    p = event(i)
    for name in c.STATES[1:]:
        r = event(i, name, p)
        c.compare_successor(p, r)
        p = r
    with pytest.raises(Refusal):
        c.compare_successor(p, event(i, "UNKNOWN", p))
    p = event(i)
    unknown = event(i, "UNKNOWN", p)
    c.compare_successor(p, unknown)
    with pytest.raises(Refusal):
        c.compare_successor(unknown, event(i, "ISSUE_STARTED", unknown))


@pytest.mark.parametrize("mutation", ["skip", "purpose", "claim", "previous", "time"])
def test_state_drift_reissue_and_time_regression_refused(mutation):
    i = intent()
    p = event(i)
    v = event(i, "ISSUE_STARTED", p).value()
    if mutation == "skip":
        v["state"] = "DELIVERY_STARTED"
    elif mutation == "purpose":
        v["purpose"] = "W"
    elif mutation == "claim":
        v["claim_sha256"] = "b" * 64
    elif mutation == "previous":
        v["previous_sha256"] = "b" * 64
    else:
        v["recorded_at_ms"] = 1999
    with pytest.raises(Refusal):
        c.compare_successor(p, record("issuance", v))


def role_identity(i):
    arn = c.role_for(i, "L")
    _, path, name = c.role_parts(arn)
    return {
        "protocol": c.PREFIX + "role-identity.v2",
        "intent_sha256": i.digest(),
        "purpose": "L",
        "role_arn": arn,
        "role_id": "AROA" + "F" * 17,
        "path": path,
        "role_name": name,
        "created_at_ms": 1200,
        "observed_at_ms": 1500,
    }


@pytest.mark.parametrize(
    "field,value",
    [
        ("version_id", "v2"),
        ("policy_id", "ANPA" + "Z" * 17),
        ("created_at_ms", 101),
        ("parent_interval_id", uid(99)),
        ("observed_at_ms", 1499),
    ],
)
def test_managed_snapshot_change_refuses_even_if_document_matches(field, value):
    i = intent()
    old = record("managed_policy", managed(i))
    v = managed(i)
    v[field] = value
    with pytest.raises(Refusal):
        c.compare_managed_snapshots(i, old, record("managed_policy", v))


def test_managed_later_equal_snapshot_is_only_value_equality():
    i = intent()
    old = record("managed_policy", managed(i))
    v = managed(i)
    v["observed_at_ms"] = 1600
    assert c.compare_managed_snapshots(i, old, record("managed_policy", v)) is None


def test_role_creation_tuple_and_session_are_exact():
    i = intent()
    role = record("role_identity", role_identity(i))
    s = record("identity", identity(i))
    c.compare_role_session(i, role, s)
    for field, value in [("path", "/different/"), ("role_name", "l"), ("observed_at_ms", 999)]:
        v = role_identity(i)
        v[field] = value
        with pytest.raises(Refusal):
            c.bind_to_intent(i, record("role_identity", v))
    v = role_identity(i)
    v["role_id"] = "AROA" + "Z" * 17
    with pytest.raises(Refusal):
        c.compare_role_session(i, record("role_identity", v), s)


def test_actual_parameter_bounds_do_not_silently_widen_policy():
    v = body()
    for prefix, letter in [
        ("owner", "o"),
        ("enrollment_reader", "r"),
        ("owner_ingress", "i"),
        ("cloud_reader", "q"),
    ]:
        v["trust"][prefix + "_arn"] = "arn:aws:iam::123456789012:role/" + "p/" * 250 + letter * 64
    v["trust"]["enrollment_table_arn"] = "arn:aws:dynamodb:us-east-1:123456789012:table/" + "e" * 255
    i = intent(v)
    assert len(c.policy_document(i, "O")) <= 6144
    with pytest.raises(Refusal):
        c.bounded_inline(c.policy_document(i, "O"))
    for name in c.CHILDREN:
        assert len(c.bounded_inline(c.policy_document(i, name))) <= 2048
    with pytest.raises(Refusal):
        c.resource_value(uid(2), "123456789012", "us-" + "x" * 33 + "-1")


def test_nested_session_secret_field_and_role_identity_extra_refused():
    i = intent()
    v = session(i)
    v["request"]["AccessKeyId"] = "not-a-credential"
    v["request_sha256"] = digest(canonical(v["request"]))
    with pytest.raises(Refusal):
        record("session", v)
    v = role_identity(i)
    v["verified"] = True
    with pytest.raises(Refusal):
        record("role_identity", v)
