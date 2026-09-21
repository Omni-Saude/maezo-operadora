"""Offline record/oracle tests only; no signed test vector is real owner authority."""

from __future__ import annotations

from copy import deepcopy

import pytest

from maezo.platform.engine_bootstrap import owner_local_contracts as c
from maezo.platform.engine_bootstrap.controller_storage import Refusal, canonical, encode64, parse_wire


def uid(n):
    return f"00000000-0000-4000-8000-{n:012d}"


def intent_body():
    scope = {
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
    }
    table = "arn:aws:dynamodb:us-east-1:123456789012:table/"
    role = "arn:aws:iam::123456789012:role/"
    base = "maezo-d7-" + uid(2)
    return {
        "protocol": c.PREFIX + "intent.v1",
        "mode": "PUBLIC_SYNTHETIC",
        "profile": c.PROFILE,
        **{name: uid(i) for i, name in enumerate(c.IDENTIFIERS.split(), 1)},
        "scope": scope,
        "trust": {
            "key_id": "owner-independent",
            "public_key_sha256": "b" * 64,
            "owner_arn": role + "protected/owner",
            "owner_role_id": "AROA" + "A" * 17,
            "enrollment_reader_arn": role + "protected/enrollment-reader",
            "enrollment_reader_role_id": "AROA" + "D" * 17,
            "owner_ingress_arn": role + "protected/ingress",
            "owner_ingress_role_id": "AROA" + "E" * 17,
            "owner_source_identity": "independent-owner",
            "enrollment_table_arn": table + "independent-enrollment",
            "enrollment_table_id": uid(91),
        },
        "resources": {
            "control_table_arn": table + base + "-control",
            "writer_arn": role + "maezo-d7-synthetic/" + base + "-writer",
            "reader_arn": role + "maezo-d7-synthetic/" + base + "-reader",
        },
        "source": {
            name: "a" * (40 if name in {"git_sha", "tree_sha"} else 64)
            for name in (
                "git_sha",
                "tree_sha",
                "artifact_sha256",
                "manifest_sha256",
                "cib_abi_sha256",
                "policy_profile_sha256",
            )
        },
        "not_before_ms": 1000,
        "deadline_ms": 901000,
        "operations": list(c.OPERATIONS),
    }


def intent():
    return c.LocalOwnerRecord("intent", canonical(intent_body()))


def event(state="CLAIMED", previous=None, **changes):
    value = {
        "protocol": c.PREFIX + "acquisition.v1",
        "intent_sha256": intent().digest(),
        "acquisition_id": uid(6),
        "revision": 1 if previous is None else previous.value()["revision"] + 1,
        "state": state,
        "previous_sha256": None if previous is None else previous.digest(),
        "evidence_sha256": "c" * 64 if state in c.OUTCOMES else None,
        "recorded_at_ms": 2000,
    }
    value.update(changes)
    return c.LocalOwnerRecord("acquisition", canonical(value))


def enrollment(**changes):
    value = {
        "protocol": c.PREFIX + "enrollment-operation.v1",
        "intent_sha256": intent().digest(),
        "enrollment_sha256": "d" * 64,
        "acquisition_id": uid(6),
        "owner_session_arn": "arn:aws:sts::123456789012:assumed-role/owner/private-session",
        "writer_role_id": "AROA" + "B" * 17,
        "reader_role_id": "AROA" + "C" * 17,
        "control_table_id": uid(92),
        **{
            name: "e" * 64
            for name in (
                "writer_creation_sha256",
                "reader_creation_sha256",
                "table_creation_sha256",
                "policy_observation_sha256",
                "pg_observation_sha256",
            )
        },
        "completed_at_ms": 2000,
    }
    value.update(changes)
    return c.LocalOwnerRecord("enrollment_operation", canonical(value))


def test_valid_shapes_are_values_and_defensive_copies():
    value = intent()
    assert value.value() == intent_body()
    copied = value.value()
    copied["trust"]["owner_arn"] = "attacker"
    assert value.value() == intent_body()
    assert not hasattr(value, "authorize")
    c.bind_to_intent(value, enrollment())


@pytest.mark.parametrize(
    "path,replacement",
    [
        (("mode",), "PRODUCTION"),
        (("profile",), "arbitrary"),
        (("operations",), ["bootstrap_scope", "install_d", "readback", "prepare_generation"]),
        (("scope", "database_binding", "database_oid"), True),
        (("resources", "writer_arn"), "arn:aws:iam::123456789012:role/another"),
        (("trust", "owner_arn"), "arn:aws:iam::999999999999:role/owner"),
        (("trust", "owner_role_id"), "AROA-not-an-incarnation"),
        (("trust", "enrollment_table_arn"), "arn:aws:dynamodb:us-west-2:123456789012:table/e"),
        (("source", "git_sha"), "a" * 39),
        (("source", "artifact_sha256"), "g" * 64),
        (("deadline_ms",), 901001),
        (("deadline_ms",), 1000),
        (("run_id",), uid(3).upper()),
    ],
)
def test_intent_refuses_wrong_profile_resources_types_and_lifetime(path, replacement):
    value = intent_body()
    target = value
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = replacement
    # uid(3) contains no letters; use a noncanonical UUID in that case.
    if path == ("run_id",):
        target[path[-1]] = "00000000-0000-4000-8000-00000000000A"
    with pytest.raises(Refusal):
        c.LocalOwnerRecord("intent", canonical(value))


@pytest.mark.parametrize("path", [(), ("scope",), ("trust",), ("resources",), ("source",)])
def test_closed_shapes_reject_extra_fields_at_every_level(path):
    value = intent_body()
    target = value
    for key in path:
        target = target[key]
    target["self_attested_authority"] = True
    with pytest.raises(Refusal):
        c.LocalOwnerRecord("intent", canonical(value))


@pytest.mark.parametrize(
    "raw", [b"{}", b'{"x":1,"x":1}', b'{"x":1.0}', b" " + b"{}", b"x" * (c.MAX_BYTES + 1)]
)
def test_bad_wire_cannot_be_repaired_into_a_record(raw):
    with pytest.raises(Refusal):
        c.LocalOwnerRecord("intent", raw)


def test_permanent_reservations_survive_new_operation_enrollment_and_run_aliases():
    original = intent()
    keys = set(c.reservation_keys(original))
    assert len(keys) == 10
    value = original.value()
    for name in ("enrollment_id", "acquisition_id", "bootstrap_operation_id", "run_id", "control_scope_id"):
        value[name] = uid(80 + len(name))
    alternate = c.LocalOwnerRecord("intent", canonical(value))
    collisions = keys & set(c.reservation_keys(alternate))
    assert len(collisions) == 5  # installation, complete DB scope and all three exact resource ARNs
    assert ("D7LOCAL#INSTALLATION#" + uid(2), "RESERVATION") in collisions


def test_different_database_incarnation_still_cannot_reuse_installation_or_run():
    value = intent_body()
    value["scope"]["database_binding"]["database_incarnation"] = uid(99)
    collisions = set(c.reservation_keys(intent())) & set(
        c.reservation_keys(c.LocalOwnerRecord("intent", canonical(value)))
    )
    assert len(collisions) == 9


def test_normal_path_requires_claim_before_issue_and_exact_linked_outcomes():
    previous = event()
    assert c.event_key(previous)[1] == "EVENT#0000000001"
    for state in c.STATES[1:]:
        current = event(state, previous)
        c.bind_to_intent(intent(), current)
        c.validate_successor(previous, current)
        previous = current
    assert previous.value()["state"] == "COMMITTED"
    assert previous.value()["revision"] == 16


@pytest.mark.parametrize("target", ["ISSUE_STARTED", "ISSUED", "TABLE_CREATED", "COMMITTED"])
def test_claim_cannot_jump_to_effect_or_receipt(target):
    previous = event()
    with pytest.raises(Refusal):
        c.validate_successor(previous, event(target, previous))


@pytest.mark.parametrize("state", c.STATES[:-1])
def test_every_ambiguous_stage_consumes_invocation_and_cannot_reissue(state):
    previous = event()
    for next_state in c.STATES[1 : c.STATES.index(state) + 1]:
        previous = event(next_state, previous)
    unknown = event("UNKNOWN", previous, recorded_at_ms=999999)
    c.validate_successor(previous, unknown)
    c.bind_to_intent(intent(), unknown)  # failure custody after expiry is legal, not admission
    for target in ("ISSUE_STARTED", "ISSUED", "WRITER_CREATE_STARTED", "COMMITTED", "UNKNOWN"):
        with pytest.raises(Refusal):
            c.validate_successor(unknown, event(target, unknown))


def test_committed_is_terminal_even_for_unknown_or_new_issue():
    previous = event()
    for state in c.STATES[1:]:
        previous = event(state, previous)
    for state in ("UNKNOWN", "ISSUE_STARTED"):
        with pytest.raises(Refusal):
            c.validate_successor(previous, event(state, previous))


@pytest.mark.parametrize(
    "changes",
    [
        {"previous_sha256": "f" * 64},
        {"revision": 3},
        {"acquisition_id": uid(88)},
        {"intent_sha256": "f" * 64},
        {"recorded_at_ms": 1999},
    ],
)
def test_successor_refuses_cross_invocation_stale_parent_and_clock_reversal(changes):
    previous = event()
    with pytest.raises(Refusal):
        c.validate_successor(previous, event("WRITER_CREATE_STARTED", previous, **changes))


@pytest.mark.parametrize(
    "changes",
    [
        {"intent_sha256": "f" * 64},
        {"acquisition_id": uid(88)},
        {"completed_at_ms": 901000},
        {"owner_session_arn": "arn:aws:sts::999999999999:assumed-role/owner/private-session"},
        {"owner_session_arn": "arn:aws:sts::123456789012:assumed-role/other/private-session"},
        {"writer_role_id": "AROA" + "A" * 17},
        {"control_table_id": uid(91)},
    ],
)
def test_enrollment_association_cannot_be_rebound(changes):
    with pytest.raises(Refusal):
        c.bind_to_intent(intent(), enrollment(**changes))


def test_success_receipt_requires_digest_but_never_authenticates_that_digest():
    with pytest.raises(Refusal):
        event("ISSUED", event(), evidence_sha256=None)
    with pytest.raises(Refusal):
        event("CLAIMED", evidence_sha256="a" * 64)
    with pytest.raises(Refusal):
        event(revision=True)


def test_signed_shape_checks_encoding_and_domain_without_claiming_authentication():
    record = intent()
    # All-zero bytes intentionally demonstrate shape != signature verification.
    envelope = {
        "protocol": c.PREFIX + "signed.v1",
        "kind": "intent",
        "key_id": "owner-independent",
        "body": record.value(),
        "signature": encode64(bytes(64)),
    }
    assert c.signed_shape(canonical(envelope)) == record
    message = c.signing_bytes(record, "owner-independent")
    assert message.startswith(c.SIGNATURE_DOMAIN)
    assert b'"kind":"intent"' in message
    for field, replacement in (
        ("kind", "acquisition"),
        ("key_id", "attacker"),
        ("signature", encode64(bytes(63))),
        ("protocol", "another"),
    ):
        altered = deepcopy(envelope)
        altered[field] = replacement
        with pytest.raises(Refusal):
            c.signed_shape(canonical(altered))
    assert c.signing_bytes(enrollment(), "owner-independent") != message


def test_session_profiles_bind_exact_resources_and_real_transaction_actions():
    i = intent()
    for purpose in ("owner", "writer", "reader"):
        raw = c.session_policy(i, purpose)
        assert len(raw) <= 2048
        policy = parse_wire(raw)
        assert set(policy) == {"Version", "Statement"}
        assert all(row["Effect"] == "Allow" for row in policy["Statement"])
        assert all("*" not in resource for row in policy["Statement"] for resource in row["Resource"])
        assert b'dynamodb:TransactWriteItems"' not in raw  # not an IAM action
    writer = parse_wire(c.session_policy(i, "writer"))["Statement"]
    assert len(writer) == 2
    put = writer[1]
    assert put["Action"] == ["dynamodb:PutItem"]
    assert put["Resource"] == [i.value()["resources"]["control_table_arn"]]
    conditions = put["Condition"]
    assert conditions["StringEquals"] == {"dynamodb:EnclosingOperation": "TransactWriteItems"}
    assert conditions["Null"] == {"dynamodb:LeadingKeys": "false"}
    assert set(conditions["ForAllValues:StringEquals"]["dynamodb:LeadingKeys"]) == {
        "D7OWNER#INSTALLATION#" + uid(2),
        "D7OWNER#SCOPE#" + uid(4),
        "D7#" + uid(4),
        "D7OWNER#OPERATION#" + uid(5),
    }
    reader = parse_wire(c.session_policy(i, "reader"))["Statement"]
    assert reader == writer[:1]
    owner = parse_wire(c.session_policy(i, "owner"))["Statement"]
    assert len(owner) == 7
    # The issuer cannot modify its own IAM role through its factory grant.
    assert i.value()["trust"]["owner_arn"] not in owner[2]["Resource"]
    assert set(owner[5]["Resource"]) == {
        i.value()["resources"]["writer_arn"],
        i.value()["resources"]["reader_arn"],
    }
    assert b"GetSecretValue" not in c.session_policy(i, "owner")
    assert b"RunTask" not in c.session_policy(i, "owner")


def test_overlong_exact_policy_refuses_instead_of_dropping_conditions_or_resources():
    body = intent_body()
    role_prefix = "arn:aws:iam::123456789012:role/"
    for n, name in enumerate(("owner_arn", "enrollment_reader_arn", "owner_ingress_arn")):
        body["trust"][name] = role_prefix + "p" * 170 + "/" + "r" * 40 + str(n)
    record = c.LocalOwnerRecord("intent", canonical(body))
    with pytest.raises(Refusal):
        c.session_policy(record, "owner")
    # Independent reader/writer templates remain small; this does not admit the owner.
    assert len(c.session_policy(record, "writer")) <= 2048


@pytest.mark.parametrize("field", ["enrollment_reader_arn", "owner_ingress_arn"])
def test_independent_trust_roles_cannot_alias_owner_or_factory(field):
    for substitute in (intent_body()["trust"]["owner_arn"], intent_body()["resources"]["writer_arn"]):
        value = intent_body()
        value["trust"][field] = substitute
        with pytest.raises(Refusal):
            c.LocalOwnerRecord("intent", canonical(value))


def test_reader_issuance_is_after_writer_receipt_and_cannot_reopen_writer():
    previous = event()
    for state in c.STATES[1 : c.STATES.index("ISSUED") + 1]:
        current = event(state, previous)
        c.validate_successor(previous, current)
        previous = current
    reader_started = event("READER_ISSUE_STARTED", previous)
    c.validate_successor(previous, reader_started)
    with pytest.raises(Refusal):
        c.validate_successor(reader_started, event("ISSUE_STARTED", reader_started))
    with pytest.raises(Refusal):
        c.validate_successor(reader_started, event("BOOTSTRAP_STARTED", reader_started))


def test_different_signing_domains_and_unrecognized_purposes_refuse():
    with pytest.raises(Refusal):
        c.session_policy(intent(), "native_qualified")
    with pytest.raises(Refusal):
        c.LocalOwnerRecord("factory_receipt", b"{}")


def test_prior_event_conditioncheck_is_owner_only_on_exact_enrollment_store():
    i = intent()
    previous = event()
    following = event("WRITER_CREATE_STARTED", previous)
    c.validate_successor(previous, following)
    assert c.event_key(previous) != c.event_key(following)
    owner = parse_wire(c.session_policy(i, "owner"))["Statement"]
    checks = [row for row in owner if "dynamodb:ConditionCheckItem" in row["Action"]]
    assert checks == [
        {
            "Effect": "Allow",
            "Action": ["dynamodb:ConditionCheckItem"],
            "Resource": [i.value()["trust"]["enrollment_table_arn"]],
        }
    ]
    assert "Condition" not in checks[0]  # EnclosingOperation is unsupported for this action.
    for purpose in ("writer", "reader"):
        assert b"ConditionCheckItem" not in c.session_policy(i, purpose)


def test_ttl_profile_can_read_exact_current_table_status_without_mutation():
    i = intent()
    for purpose, expected in (
        ("owner", {i.value()["trust"]["enrollment_table_arn"], i.value()["resources"]["control_table_arn"]}),
        ("writer", {i.value()["resources"]["control_table_arn"]}),
        ("reader", {i.value()["resources"]["control_table_arn"]}),
    ):
        policy = parse_wire(c.session_policy(i, purpose))
        actual = {
            resource
            for row in policy["Statement"]
            if "dynamodb:DescribeTimeToLive" in row["Action"]
            for resource in row["Resource"]
        }
        assert actual == expected
        assert b"UpdateTimeToLive" not in c.session_policy(i, purpose)
        assert b"DeleteItem" not in c.session_policy(i, purpose)


@pytest.mark.parametrize(
    "description",
    [
        None,
        {},
        [],
        "DISABLED",
        {"TimeToLiveStatus": None},
        {"TimeToLiveStatus": True},
        {"TimeToLiveStatus": "ENABLING"},
        {"TimeToLiveStatus": "ENABLED"},
        {"TimeToLiveStatus": "DISABLING"},
        {"TimeToLiveStatus": "UNKNOWN"},
        {"TimeToLiveStatus": "disabled"},
        {"TimeToLiveStatus": "DISABLED", "extra": 1},
        {"TimeToLiveStatus": "DISABLED", "AttributeName": None},
        {"TimeToLiveStatus": "DISABLED", "AttributeName": ""},
        {"TimeToLiveStatus": "DISABLED", "AttributeName": "x" * 256},
        {"TimeToLiveStatus": "DISABLED", "AttributeName": 12},
        {"TimeToLiveDescription": {"TimeToLiveStatus": "DISABLED"}},
    ],
)
def test_missing_transitional_or_unrecognized_ttl_evidence_refuses(description):
    with pytest.raises(Refusal):
        c.require_disabled_ttl(description)


@pytest.mark.parametrize(
    "description",
    [
        {"TimeToLiveStatus": "DISABLED"},
        {"TimeToLiveStatus": "DISABLED", "AttributeName": "previous_expiry"},
        {"TimeToLiveStatus": "DISABLED", "AttributeName": "x" * 255},
    ],
)
def test_disabled_ttl_description_is_a_value_check_not_an_owner_receipt(description):
    assert c.require_disabled_ttl(description) is None


def test_required_permissions_are_preserved_at_exact_sts_size_boundary():
    body = intent_body()
    remaining = 2048 - len(c.session_policy(intent(), "owner"))
    body["trust"]["owner_arn"] += "x" * remaining
    record = c.LocalOwnerRecord("intent", canonical(body))
    raw = c.session_policy(record, "owner")
    assert len(raw) == 2048
    assert b"ConditionCheckItem" in raw and b"DescribeTimeToLive" in raw
    assert b"EnclosingOperation" in raw
    body["trust"]["owner_arn"] += "x"
    over = c.LocalOwnerRecord("intent", canonical(body))
    with pytest.raises(Refusal):
        c.session_policy(over, "owner")
