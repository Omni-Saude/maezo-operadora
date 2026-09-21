"""Synthetic structural vectors only: no signature, SQL or admission claim."""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from pathlib import Path

import pytest

from maezo.platform.engine_bootstrap import initial_pg_contracts as c
from maezo.platform.engine_bootstrap.controller_storage import Refusal, canonical, digest

ROOT = Path(__file__).parents[4]
SAMPLES = {
    x["type"]: x["value"]
    for x in json.loads((ROOT / "spec/provisioning/d7d-stage1-v1.json").read_bytes())["structural_vectors"][
        "positive"
    ]
}
H = "a" * 64


def uid(n):
    return f"00000000-0000-4000-8000-{n:012d}"


def vectors(phase="INSTALLED"):
    raw = deepcopy(SAMPLES["DatabaseObservation"])
    identity = dict(installation_id=uid(10), control_scope_id=uid(11), scope=deepcopy(raw["scope"]))
    roles = {name: dict(name=name.lower(), oid=i) for i, name in enumerate(c.ROLE_NAMES, 201)}
    m = dict(
        protocol=c.PREFIX + "manifest.v1",
        **identity,
        original_sql_sha256=c.SQL_SHA,
        original_manifest_sha256=c.MANIFEST_SHA,
        d_table_count=7,
        d_function_count=57,
        roles=roles,
        statistics_membership=dict(
            member_oid=202,
            role_oid=205,
            grantor_oid=206,
            admin_option=False,
            inherit_option=True,
            set_option=False,
        ),
        helpers=[],
        external_acl_sha256=H,
        host_profile_sha256=H,
        supervisor_source_sha256=H,
        observer_source_sha256=H,
        issuer_source_sha256=H,
        routes=[],
    )
    for i, (purpose, namespace, function, owner) in enumerate(
        [
            ("READBACK", "maezo_d7_initial_readback_v1", "read_initial_state", "D_OWNER"),
            ("STATS", "maezo_d7_initial_stats_v1", "read_initial_census", "STATS_OWNER"),
        ]
    ):
        m["helpers"].append(
            dict(
                purpose=purpose,
                namespace=namespace,
                namespace_oid=501 + i,
                function=function,
                function_oid=601 + i,
                owner_oid=roles[owner]["oid"],
                definition_sha256=H,
                security_definer=True,
                volatility="VOLATILE",
                parallel="UNSAFE",
                search_path=["pg_catalog", "pg_temp"],
                namespace_usage_oids=[203],
                execute_oids=[203],
            )
        )
    common = dict(**identity, run_id=raw["run_id"], epoch=1, phase=phase)
    read = dict(
        protocol=c.PREFIX + "read-request.v1",
        **common,
        owner_subject="owner",
        expected_fence_revision=c.PHASES[phase][0],
        reconcile=None,
        close=None,
    )
    if phase != "INSTALLED":
        read["reconcile"] = dict(issuer_operation_id=uid(30), request_sha256=H)
    if phase == "CLOSED":
        read["close"] = dict(issuer_operation_id=uid(31), request_sha256=H)
    census = dict(protocol=c.PREFIX + "census-request.v1", **common, challenge=raw["challenge"])
    barrier = dict(
        instance_id=uid(12),
        generation=1,
        supervisor_pid=100,
        supervisor_start_unix_us=100,
        supervisor_uid=1000,
        network_namespace_inode=123,
        source_sha256=H,
        host_observation_sha256=H,
        opened_at_ms=1000,
        deadline_ms=6000,
    )
    p = dict(
        protocol=c.PREFIX + "management-projection.v1",
        **common,
        read_request_sha256=digest(canonical(read)),
        read_result_sha256=H,
        census_request_sha256=digest(canonical(census)),
        raw_database_observation_sha256=H,
        barrier=barrier,
        connections=[],
        partition=[],
    )
    db = raw["payload"]
    db.update(
        server_version_num=160005,
        session_user="db_observer",
        current_user="stats_owner",
        sessions=[],
        routes=[],
        generations=[],
        prepared_transactions=[],
        roles=[],
        memberships=[deepcopy(m["statistics_membership"])],
        in_flight_admission_barrier_sha256=digest(canonical(barrier)),
    )
    for name, oid in [(r["name"], r["oid"]) for r in roles.values()] + [
        ("pg_read_all_stats", 205),
        ("grantor", 206),
    ]:
        row = deepcopy(SAMPLES["RoleObservation"])
        row.update(
            name=name,
            oid=oid,
            owned=False,
            can_login=oid in (203, 204),
            superuser=False,
            create_role=False,
            create_db=False,
            replication=False,
            bypass_rls=False,
            inherit=True,
            generation_id=None,
        )
        db["roles"].append(row)
    raw.update(epoch=1, issued_at_ms=1000, expires_at_ms=6000, observed_revision=c.PHASES[phase][0])
    db["fence"].update(epoch=1, revision=c.PHASES[phase][0], state=c.PHASES[phase][1])
    for i, purpose in enumerate(c.PURPOSES):
        connection = dict(
            purpose=purpose,
            process_pid=101 + i,
            process_start_unix_us=1001 + i,
            process_uid=1001 + i,
            source_sha256=H,
            channel_nonce=("A" * 42 + "A") if i == 0 else ("B" * 42 + "A"),
            connection_id=uid(40 + i),
            backend_pid=301 + i,
            backend_start_unix_us=2001 + i,
            database_oid=db["database_binding"]["database_oid"],
            login_oid=203 + i,
            current_role_oid=202 if i == 0 else 204,
            state="ACTIVE" if i == 0 else "IDLE",
            identity_origin="IN_FUNCTION" if i == 0 else "QUIESCENT_HANDOFF",
        )
        p["connections"].append(connection)
        route = dict(
            route_id=f"route-{i}",
            purpose=purpose,
            kind="CERTIFICATE",
            frontend_identity_sha256=H,
            network_binding_sha256=H,
            qualified_adapter_sha256=H,
        )
        m["routes"].append(route)
        db["routes"].append(
            {k: v for k, v in route.items() if k != "purpose"}
            | dict(
                enabled=True,
                generation_id=None,
                backend_login_oids=[203 + i],
                in_flight_admissions=0,
                admission_barrier_sha256=digest(canonical(barrier)),
            )
        )
        db["sessions"].append(
            dict(
                pid=301 + i,
                backend_start_unix_us=2001 + i,
                login_oid=203 + i,
                current_role_oid=connection["current_role_oid"],
                database_oid=connection["database_oid"],
                generation_id=None,
                owned=False,
                state=connection["state"],
                transaction_start_ms=None,
                route_id=f"route-{i}",
            )
        )
        p["partition"].extend(
            [
                dict(
                    kind="SESSION",
                    identity=dict(backend_pid=301 + i, backend_start_unix_us=2001 + i),
                    classification="QUALIFIED_MANAGEMENT",
                    purpose=purpose,
                ),
                dict(
                    kind="ROUTE",
                    identity=dict(route_id=f"route-{i}"),
                    classification="QUALIFIED_MANAGEMENT",
                    purpose=purpose,
                ),
            ]
        )
    return m, read, census, p, raw


def check(v, now=1001):
    m, read, census, p, raw = v
    p["raw_database_observation_sha256"] = digest(canonical(raw))
    return c.check_projection(
        c.Description.create("management-projection", p),
        manifest=c.Description.create("manifest", m),
        read_request=c.Description.create("read-request", read),
        census_request=c.Description.create("census-request", census),
        raw_database_observation=canonical(raw),
        now_ms=now,
    )


@pytest.mark.parametrize("phase", c.PHASES)
def test_structural_positive_returns_no_authority_and_preserves_raw(phase):
    v = vectors(phase)
    original = canonical(v[-1])
    assert check(v) is None
    assert canonical(v[-1]) == original


@pytest.mark.parametrize(
    "kind,index", [("manifest", 0), ("read-request", 1), ("census-request", 2), ("management-projection", 3)]
)
@pytest.mark.parametrize("attack", ["extra", "missing", "noncanonical", "duplicate", "float", "oversize"])
def test_closed_canonical_records(kind, index, attack):
    value = vectors()[index]
    if attack == "extra":
        value["caller_authorized"] = True
    if attack == "missing":
        del value["scope"]
    if attack == "float":
        value["installation_id"] = 1.5
    raw = canonical(value) if attack != "float" else json.dumps(value).encode()
    if attack == "noncanonical":
        raw = b" " + raw
    if attack == "duplicate":
        raw = raw[:-1] + b',"protocol":"duplicate"}'
    if attack == "oversize":
        raw = b" " * 1_048_577
    with pytest.raises(Refusal):
        c.Description(kind, raw)


@pytest.mark.parametrize(
    "attack",
    [
        "wrong-sql",
        "native-counts",
        "member-admin",
        "member-set",
        "member-noinherit",
        "extra-execute",
        "stats-owner-as-observer",
        "wrong-owner",
        "extra-helper",
        "shadow-schema",
        "search-path",
        "public-execute",
        "same-namespace-oid",
        "duplicate-route",
        "unknown-route-kind",
    ],
)
def test_manifest_allocations_refuse(attack):
    m = vectors()[0]
    if attack == "wrong-sql":
        m["original_sql_sha256"] = "b" * 64
    if attack == "native-counts":
        m.update(d_table_count=6, d_function_count=19)
    if attack == "member-admin":
        m["statistics_membership"]["admin_option"] = True
    if attack == "member-set":
        m["statistics_membership"]["set_option"] = True
    if attack == "member-noinherit":
        m["statistics_membership"]["inherit_option"] = False
    if attack == "extra-execute":
        m["helpers"][0]["execute_oids"].append(204)
    if attack == "stats-owner-as-observer":
        m["roles"]["STATS_OWNER"] = m["roles"]["DB_OBSERVER"]
    if attack == "wrong-owner":
        m["helpers"][0]["owner_oid"] = 202
    if attack == "extra-helper":
        m["helpers"].append(deepcopy(m["helpers"][0]))
    if attack == "shadow-schema":
        m["helpers"][0]["namespace_oid"] = m["scope"]["database_binding"]["schema_oid"]
    if attack == "search-path":
        m["helpers"][0]["search_path"].insert(0, "public")
    if attack == "public-execute":
        m["helpers"][0]["execute_oids"] = [0, 203]
    if attack == "same-namespace-oid":
        m["helpers"][1]["namespace_oid"] = m["helpers"][0]["namespace_oid"]
    if attack == "duplicate-route":
        m["routes"].append(deepcopy(m["routes"][0]))
    if attack == "unknown-route-kind":
        m["routes"][0]["kind"] = "POOLER"
    with pytest.raises(Refusal):
        c.Description.create("manifest", m)


@pytest.mark.parametrize(
    "attack",
    [
        "foreign-scope",
        "bad-phase",
        "wrong-fence",
        "repeat-operation",
        "absent-close",
        "boolean-epoch",
    ],
)
def test_phase_read_relationships(attack):
    v = vectors("CLOSED")
    read = v[1]
    if attack == "foreign-scope":
        read["scope"]["tenant"] = "foreign"
    if attack == "bad-phase":
        read["phase"] = "OPEN"
    if attack == "wrong-fence":
        read["expected_fence_revision"] = 4
    if attack == "repeat-operation":
        read["close"] = deepcopy(read["reconcile"])
    if attack == "absent-close":
        read["close"] = None
    if attack == "boolean-epoch":
        read["epoch"] = True
    with pytest.raises(Refusal):
        check(v)


@pytest.mark.parametrize(
    "attack",
    [
        "shared-pid",
        "shared-uid",
        "shared-backend",
        "shared-channel",
        "shared-login",
        "observer-login-current",
        "issuer-active",
        "issuer-effective-role",
        "process-supervisor",
        "extended-barrier",
        "zero-barrier",
        "restarted-barrier",
        "session-closed",
        "missing-partition",
        "duplicate-partition",
        "hidden-session",
        "hidden-route",
        "prepared",
        "raw-current-role",
        "raw-issuer-transaction",
        "raw-login-enabled-owner",
        "raw-admin-owner",
        "stats-noinherit",
        "raw-membership",
        "raw-extra-owner-member",
        "wrong-observer-name",
        "wrong-route-login",
        "route-enabled-closed",
        "route-in-flight",
        "route-unqualified",
        "wrong-source",
        "wrong-barrier",
        "wrong-database",
        "wrong-phase-fence",
        "old-observation",
        "foreign-challenge",
    ],
)
def test_raw_partition_and_custody_description_controls(attack):
    v = vectors()
    m, read, census, p, raw = v
    db = raw["payload"]
    a, b = p["connections"]
    field = {
        "shared-pid": "process_pid",
        "shared-uid": "process_uid",
        "shared-backend": "backend_pid",
        "shared-channel": "channel_nonce",
        "shared-login": "login_oid",
    }.get(attack)
    if field:
        b[field] = a[field]
    if attack == "observer-login-current":
        a["current_role_oid"] = a["login_oid"]
    if attack == "issuer-active":
        b["state"] = "ACTIVE"
    if attack == "issuer-effective-role":
        b["current_role_oid"] = 202
    if attack == "process-supervisor":
        a["process_pid"] = p["barrier"]["supervisor_pid"]
    if attack == "extended-barrier":
        p["barrier"]["deadline_ms"] += 1
    if attack == "zero-barrier":
        p["barrier"]["deadline_ms"] = p["barrier"]["opened_at_ms"]
    if attack == "restarted-barrier":
        p["barrier"]["generation"] = 2
    if attack == "session-closed":
        p["partition"][0].update(classification="CLOSED", purpose=None)
    if attack == "missing-partition":
        p["partition"].pop()
    if attack == "duplicate-partition":
        p["partition"].append(deepcopy(p["partition"][0]))
    if attack == "hidden-session":
        db["sessions"].append(deepcopy(db["sessions"][0]) | {"pid": 999})
    if attack == "hidden-route":
        db["routes"].append(deepcopy(db["routes"][0]) | {"route_id": "hidden"})
    if attack == "prepared":
        db["prepared_transactions"] = [
            dict(
                transaction_gid_sha256=H,
                login_oid=204,
                database_oid=db["database_binding"]["database_oid"],
                prepared_at_ms=1000,
                generation_id=None,
                owned=False,
            )
        ]
    if attack == "raw-current-role":
        db["sessions"][0]["current_role_oid"] = 203
    if attack == "raw-issuer-transaction":
        db["sessions"][1]["transaction_start_ms"] = 999
    if attack == "raw-login-enabled-owner":
        db["roles"][0]["can_login"] = True
    if attack == "raw-admin-owner":
        db["roles"][0]["superuser"] = True
    if attack == "stats-noinherit":
        db["roles"][1]["inherit"] = False
    if attack == "raw-membership":
        db["memberships"][0]["set_option"] = True
    if attack == "raw-extra-owner-member":
        db["memberships"].append(
            dict(
                member_oid=204,
                role_oid=201,
                grantor_oid=206,
                admin_option=False,
                inherit_option=True,
                set_option=False,
            )
        )
    if attack == "wrong-observer-name":
        db["session_user"] = "issuer"
    if attack == "wrong-route-login":
        db["routes"][0]["backend_login_oids"] = [204]
    if attack == "route-enabled-closed":
        p["partition"][1].update(classification="CLOSED", purpose=None)
    if attack == "route-in-flight":
        db["routes"][0]["in_flight_admissions"] = 1
    if attack == "route-unqualified":
        db["routes"][0]["qualified_adapter_sha256"] = None
    if attack == "wrong-source":
        a["source_sha256"] = "b" * 64
    if attack == "wrong-barrier":
        db["in_flight_admission_barrier_sha256"] = "b" * 64
    if attack == "wrong-database":
        a["database_oid"] += 1
    if attack == "wrong-phase-fence":
        db["fence"]["revision"] = 2
        raw["observed_revision"] = 2
    if attack == "old-observation":
        raw["issued_at_ms"] = 999
    if attack == "foreign-challenge":
        raw["challenge"] = "C" * 42 + "A"
    with pytest.raises(Refusal):
        check(v)


@pytest.mark.parametrize("now", [999, 6000, 6001, True])
def test_expired_or_not_yet_open(now):
    with pytest.raises(Refusal):
        check(vectors(), now)


def test_disabled_route_is_retained_and_fully_partitioned():
    v = vectors()
    p, raw = v[3:]
    raw["payload"]["routes"].append(
        deepcopy(raw["payload"]["routes"][0]) | dict(route_id="closed", enabled=False)
    )
    p["partition"].append(
        dict(kind="ROUTE", identity=dict(route_id="closed"), classification="CLOSED", purpose=None)
    )
    assert check(v) is None


def test_no_opaque_result_can_be_constructed():
    for kind in ("read-result", "census-result", "admission"):
        with pytest.raises(Refusal):
            c.Description.create(kind, {"protocol": c.PREFIX + kind + ".v1"})


def test_immutable_descriptions_do_not_retain_callers_mutable_objects():
    v = vectors()[0]
    d = c.Description.create("manifest", v)
    v["roles"]["ISSUER"]["oid"] = 900
    detached = d.value()
    detached["roles"]["ISSUER"]["oid"] = 901
    assert d.value()["roles"]["ISSUER"]["oid"] == 204


def test_original_d_bytes_and_actual_counts_remain_pinned():
    sql = (
        ROOT / "src/maezo/portal/engine/java/src/main/resources/provisioning-schema-postgres.sql"
    ).read_bytes()
    manifest = (ROOT / "deploy/cibseven/secured/migrations/d7-control-0001.json").read_bytes()
    assert hashlib.sha256(sql).hexdigest() == c.SQL_SHA
    assert hashlib.sha256(manifest).hexdigest() == c.MANIFEST_SHA
    assert len(re.findall(rb"CREATE FUNCTION", sql)) == 57
    assert len(re.findall(rb"CREATE TABLE", sql)) == 7
