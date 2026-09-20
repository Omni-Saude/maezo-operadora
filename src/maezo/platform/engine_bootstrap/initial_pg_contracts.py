"""ADR-0057 inert initial PG contracts: structural consistency, never authority.

No SQL, connection, signature, installer, credential or effect operation exists here.
Read/census results deliberately remain unimplemented until their full row ABI exists.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from . import controller_contracts as c
from .controller_storage import canonical, digest, exact, parse_wire, require, scalar

PREFIX = "maezo.d7-initial-pg."
SQL_SHA = "c857042bd26626dbd14f4e7c6b82280ddcafd156183f3ddb56f18fba1b210606"
MANIFEST_SHA = "fcf569db7344f677ef543fc892d0b1665ee34aa245471bf92535da2626b4f7a1"
IDENTITY = "installation_id control_scope_id scope"
PHASE_IDENTITY = IDENTITY + " run_id epoch phase"
PHASES = {"INSTALLED": (1, "CLOSED"), "RECONCILED": (2, "RECOVERY_REQUIRED"), "CLOSED": (3, "CLOSED")}
PURPOSES = ("DB_OBSERVER", "ISSUER")
ROLE_NAMES = ("D_OWNER", "STATS_OWNER", *PURPOSES)
BARRIER_FIELDS = (
    "instance_id generation supervisor_pid supervisor_start_unix_us supervisor_uid "
    "network_namespace_inode source_sha256 host_observation_sha256 opened_at_ms deadline_ms"
)
CONNECTION_FIELDS = (
    "purpose process_pid process_start_unix_us process_uid source_sha256 channel_nonce "
    "connection_id backend_pid backend_start_unix_us database_oid login_oid current_role_oid "
    "state identity_origin"
)
HELPER_FIELDS = (
    "purpose namespace namespace_oid function function_oid owner_oid definition_sha256 "
    "security_definer volatility parallel search_path namespace_usage_oids execute_oids"
)
ROUTE_FIELDS = (
    "route_id purpose kind frontend_identity_sha256 network_binding_sha256 qualified_adapter_sha256"
)
MANIFEST_FIELDS = (
    "protocol " + IDENTITY + " original_sql_sha256 original_manifest_sha256 d_table_count "
    "d_function_count roles statistics_membership helpers external_acl_sha256 host_profile_sha256 "
    "supervisor_source_sha256 observer_source_sha256 issuer_source_sha256 routes"
)


def _identity(v: dict[str, Any], *, phase: bool = True) -> None:
    for key in ("installation_id", "control_scope_id"):
        scalar("Uuid", v[key])
    scalar("Scope", v["scope"])
    if phase:
        scalar("Uuid", v["run_id"])
        require(type(v["epoch"]) is int and v["epoch"] == 1)
        require(type(v["phase"]) is str and v["phase"] in PHASES)


def _manifest(v: dict[str, Any]) -> None:
    exact(v, MANIFEST_FIELDS)
    _identity(v, phase=False)
    require(v["original_sql_sha256"] == SQL_SHA and v["original_manifest_sha256"] == MANIFEST_SHA)
    require(type(v["d_table_count"]) is int and v["d_table_count"] == 7)
    require(type(v["d_function_count"]) is int and v["d_function_count"] == 57)
    roles = exact(v["roles"], ROLE_NAMES)
    for role in roles.values():
        exact(role, "name oid")
        scalar("Id", role["name"])
        scalar("Oid", role["oid"])
    require(len({r["oid"] for r in roles.values()}) == 4)
    require(len({r["name"] for r in roles.values()}) == 4)
    membership = exact(
        v["statistics_membership"],
        "member_oid role_oid grantor_oid admin_option inherit_option set_option",
    )
    for key in ("member_oid", "role_oid", "grantor_oid"):
        scalar("Oid", membership[key])
    require(membership["member_oid"] == roles["STATS_OWNER"]["oid"])
    require(membership["role_oid"] not in {r["oid"] for r in roles.values()})
    require(membership["admin_option"] is False and membership["inherit_option"] is True)
    require(membership["set_option"] is False)
    helpers = v["helpers"]
    require(type(helpers) is list and len(helpers) == 2)
    for helper, purpose, namespace, function, owner in zip(
        helpers,
        ("READBACK", "STATS"),
        ("maezo_d7_initial_readback_v1", "maezo_d7_initial_stats_v1"),
        ("read_initial_state", "read_initial_census"),
        ("D_OWNER", "STATS_OWNER"),
        strict=True,
    ):
        exact(helper, HELPER_FIELDS)
        require(helper["purpose"] == purpose and helper["namespace"] == namespace)
        require(helper["function"] == function and helper["owner_oid"] == roles[owner]["oid"])
        for key in ("namespace_oid", "function_oid", "owner_oid"):
            scalar("Oid", helper[key])
        require(helper["namespace_oid"] != v["scope"]["database_binding"]["schema_oid"])
        require(helper["namespace"] != v["scope"]["database_binding"]["schema_name"])
        scalar("Sha256", helper["definition_sha256"])
        require(helper["security_definer"] is True and helper["volatility"] == "VOLATILE")
        require(helper["parallel"] == "UNSAFE" and helper["search_path"] == ["pg_catalog", "pg_temp"])
        for key in ("namespace_usage_oids", "execute_oids"):
            require(helper[key] == [roles["DB_OBSERVER"]["oid"]])
            scalar("Oid", helper[key][0])
    for key in ("namespace_oid", "function_oid"):
        require(helpers[0][key] != helpers[1][key])
    for key in (
        "external_acl_sha256",
        "host_profile_sha256",
        "supervisor_source_sha256",
        "observer_source_sha256",
        "issuer_source_sha256",
    ):
        scalar("Sha256", v[key])
    routes = v["routes"]
    require(type(routes) is list and 2 <= len(routes) <= 1024)
    for route in routes:
        exact(route, ROUTE_FIELDS)
        scalar("Id", route["route_id"])
        require(route["purpose"] in PURPOSES and route["kind"] in {"CERTIFICATE", "SECURITY_DEFINER"})
        for key in ROUTE_FIELDS.split()[3:]:
            scalar("Sha256", route[key])
    ids = [r["route_id"] for r in routes]
    require(ids == sorted(set(ids)))
    require({r["purpose"] for r in routes} == set(PURPOSES))


def _read_request(v: dict[str, Any]) -> None:
    exact(v, "protocol " + PHASE_IDENTITY + " owner_subject expected_fence_revision reconcile close")
    _identity(v)
    scalar("Id", v["owner_subject"])
    scalar("Positive", v["expected_fence_revision"])
    require(v["expected_fence_revision"] == PHASES[v["phase"]][0])
    for key, present in (("reconcile", v["phase"] != "INSTALLED"), ("close", v["phase"] == "CLOSED")):
        if present:
            selector = exact(v[key], "issuer_operation_id request_sha256")
            scalar("Uuid", selector["issuer_operation_id"])
            scalar("Sha256", selector["request_sha256"])
        else:
            require(v[key] is None)
    if v["phase"] == "CLOSED":
        require(v["reconcile"]["issuer_operation_id"] != v["close"]["issuer_operation_id"])


def _census_request(v: dict[str, Any]) -> None:
    exact(v, "protocol " + PHASE_IDENTITY + " challenge")
    _identity(v)
    scalar("Challenge", v["challenge"])


def _projection(v: dict[str, Any]) -> None:
    exact(
        v,
        "protocol " + PHASE_IDENTITY + " read_request_sha256 read_result_sha256 "
        "census_request_sha256 raw_database_observation_sha256 barrier connections partition",
    )
    _identity(v)
    for key in (
        "read_request_sha256",
        "read_result_sha256",
        "census_request_sha256",
        "raw_database_observation_sha256",
    ):
        scalar("Sha256", v[key])
    barrier = exact(v["barrier"], BARRIER_FIELDS)
    scalar("Uuid", barrier["instance_id"])
    require(type(barrier["generation"]) is int and barrier["generation"] == 1)
    for key in ("supervisor_pid", "supervisor_start_unix_us", "network_namespace_inode"):
        scalar("Positive", barrier[key])
    for key in ("supervisor_uid", "opened_at_ms", "deadline_ms"):
        scalar("UInt", barrier[key])
    for key in ("source_sha256", "host_observation_sha256"):
        scalar("Sha256", barrier[key])
    require(barrier["opened_at_ms"] < barrier["deadline_ms"] <= barrier["opened_at_ms"] + 5000)
    connections = v["connections"]
    require(type(connections) is list and len(connections) == 2)
    for row, purpose in zip(connections, PURPOSES, strict=True):
        exact(row, CONNECTION_FIELDS)
        require(row["purpose"] == purpose)
        for key in ("process_pid", "process_start_unix_us", "backend_pid", "backend_start_unix_us"):
            scalar("Positive", row[key])
        scalar("UInt", row["process_uid"])
        for key in ("database_oid", "login_oid", "current_role_oid"):
            scalar("Oid", row[key])
        scalar("Sha256", row["source_sha256"])
        scalar("Challenge", row["channel_nonce"])
        scalar("Uuid", row["connection_id"])
        require(row["process_pid"] != barrier["supervisor_pid"])
        if purpose == "DB_OBSERVER":
            require(row["state"] == "ACTIVE" and row["identity_origin"] == "IN_FUNCTION")
            require(row["current_role_oid"] != row["login_oid"])
        else:
            require(row["state"] == "IDLE" and row["identity_origin"] == "QUIESCENT_HANDOFF")
            require(row["current_role_oid"] == row["login_oid"])
    for key in ("process_pid", "process_uid", "backend_pid", "login_oid", "channel_nonce", "connection_id"):
        require(connections[0][key] != connections[1][key])
    entries = v["partition"]
    require(type(entries) is list and 4 <= len(entries) <= 1024)
    seen: set[bytes] = set()
    for entry in entries:
        exact(entry, "kind identity classification purpose")
        require(entry["kind"] in {"SESSION", "ROUTE"})
        if entry["kind"] == "SESSION":
            identity = exact(entry["identity"], "backend_pid backend_start_unix_us")
            for value in identity.values():
                scalar("Positive", value)
            require(entry["classification"] == "QUALIFIED_MANAGEMENT")
        else:
            identity = exact(entry["identity"], "route_id")
            scalar("Id", identity["route_id"])
        require(entry["classification"] in {"CLOSED", "QUALIFIED_MANAGEMENT"})
        require(
            entry["purpose"] is None if entry["classification"] == "CLOSED" else entry["purpose"] in PURPOSES
        )
        identity_key = canonical([entry["kind"], identity])
        require(identity_key not in seen)
        seen.add(identity_key)


VALIDATORS = {
    "manifest": _manifest,
    "read-request": _read_request,
    "census-request": _census_request,
    "management-projection": _projection,
}


@dataclass(frozen=True, slots=True)
class Description:
    """Retained immutable untrusted bytes; construction confers no admission rights."""

    kind: str
    wire: bytes

    def __post_init__(self) -> None:
        require(type(self.kind) is str and self.kind in VALIDATORS)
        require(type(self.wire) is bytes and 0 < len(self.wire) <= 1_048_576)
        value = parse_wire(self.wire)
        require(self.wire == canonical(value))
        try:
            require(type(value) is dict and value.get("protocol") == PREFIX + self.kind + ".v1")
            VALIDATORS[self.kind](value)
        except c.ContractError:
            raise
        except (KeyError, TypeError, AttributeError, ValueError):
            raise c.ContractError("INVALID_BODY") from None

    @classmethod
    def create(cls, kind: str, value: Any) -> Description:
        return cls(kind, canonical(value))

    def value(self) -> dict[str, Any]:
        return cast(dict[str, Any], parse_wire(self.wire))


def check_projection(
    projection: Description,
    *,
    manifest: Description,
    read_request: Description,
    census_request: Description,
    raw_database_observation: bytes,
    now_ms: int,
) -> None:
    """Check relationships only. No signature, commit, clock or host authentication."""
    for document, kind in (
        (projection, "management-projection"),
        (manifest, "manifest"),
        (read_request, "read-request"),
        (census_request, "census-request"),
    ):
        require(type(document) is Description and document.kind == kind)
        Description(kind, document.wire)
    p, m, read, census = (d.value() for d in (projection, manifest, read_request, census_request))
    for other, keys in ((m, IDENTITY), (read, PHASE_IDENTITY), (census, PHASE_IDENTITY)):
        require(all(p[k] == other[k] for k in keys.split()), "SCOPE_REFUSED")
    require(p["read_request_sha256"] == digest(read_request.wire))
    require(p["census_request_sha256"] == digest(census_request.wire))
    require(p["raw_database_observation_sha256"] == digest(raw_database_observation))
    observation = c.parse("DatabaseObservation", raw_database_observation)
    raw = parse_wire(observation.canonical_bytes())
    require(raw_database_observation == observation.canonical_bytes())
    require(all(p[k] == raw[k] for k in ("scope", "run_id", "epoch")))
    require(raw["challenge"] == census["challenge"])
    db = raw["payload"]
    require(db["database_binding"] == m["scope"]["database_binding"])
    require(db["server_version_num"] // 10000 == 16)
    require(db["fence"]["revision"] == PHASES[p["phase"]][0])
    require(db["fence"]["state"] == PHASES[p["phase"]][1])
    require(not db["prepared_transactions"] and not db["generations"])
    barrier = p["barrier"]
    scalar("UInt", now_ms)
    require(
        barrier["opened_at_ms"]
        <= raw["issued_at_ms"]
        <= now_ms
        < raw["expires_at_ms"]
        <= barrier["deadline_ms"]
    )
    require(barrier["source_sha256"] == m["supervisor_source_sha256"])
    require(db["in_flight_admission_barrier_sha256"] == digest(canonical(barrier)))
    roles = {r["oid"]: r for r in db["roles"]}
    for purpose, expected in m["roles"].items():
        require(expected["oid"] in roles)
        actual = roles[expected["oid"]]
        require(
            actual["name"] == expected["name"] and not actual["owned"] and actual["generation_id"] is None
        )
        require(actual["can_login"] == (purpose in PURPOSES))
        require(
            not any(actual[k] for k in ("superuser", "create_role", "create_db", "replication", "bypass_rls"))
        )
    stats_oid = m["roles"]["STATS_OWNER"]["oid"]
    require(roles[stats_oid]["inherit"] is True)
    require([r for r in db["memberships"] if r["member_oid"] == stats_oid] == [m["statistics_membership"]])
    stats_group = roles[m["statistics_membership"]["role_oid"]]
    require(stats_group["name"] == "pg_read_all_stats")
    require(not any(r["role_oid"] in {stats_oid, m["roles"]["D_OWNER"]["oid"]} for r in db["memberships"]))
    require(db["session_user"] == m["roles"]["DB_OBSERVER"]["name"])
    require(db["current_user"] == m["roles"]["STATS_OWNER"]["name"])
    connections = {r["purpose"]: r for r in p["connections"]}
    for purpose, row in connections.items():
        require(row["login_oid"] == m["roles"][purpose]["oid"])
        require(row["database_oid"] == db["database_binding"]["database_oid"])
        require(
            row["source_sha256"]
            == m["observer_source_sha256" if purpose == "DB_OBSERVER" else "issuer_source_sha256"]
        )
    require(connections["DB_OBSERVER"]["current_role_oid"] == stats_oid)
    sessions = {(r["pid"], r["backend_start_unix_us"]): r for r in db["sessions"]}
    require(len(sessions) == 2)
    routes = {r["route_id"]: r for r in db["routes"]}
    require(len(routes) <= 1024)
    allocated = {r["route_id"]: r for r in m["routes"]}
    require(set(allocated) <= set(routes))
    seen_sessions: set[tuple[int, int]] = set()
    seen_routes: set[str] = set()
    for entry in p["partition"]:
        if entry["kind"] == "SESSION":
            key = (entry["identity"]["backend_pid"], entry["identity"]["backend_start_unix_us"])
            require(key in sessions)
            session, connection = sessions[key], connections[entry["purpose"]]
            require(key == (connection["backend_pid"], connection["backend_start_unix_us"]))
            require(
                all(
                    session[k] == connection[k]
                    for k in ("login_oid", "current_role_oid", "database_oid", "state")
                )
            )
            require(not session["owned"] and session["generation_id"] is None)
            require(session["state"] != "IDLE" or session["transaction_start_ms"] is None)
            require(
                session["route_id"] in allocated
                and allocated[session["route_id"]]["purpose"] == entry["purpose"]
            )
            seen_sessions.add(key)
        else:
            route_id = entry["identity"]["route_id"]
            require(route_id in routes)
            route = routes[route_id]
            require(route["in_flight_admissions"] == 0 and route["generation_id"] is None)
            require(route["admission_barrier_sha256"] == digest(canonical(barrier)))
            if entry["classification"] == "CLOSED":
                require(not route["enabled"] and route_id not in allocated)
            else:
                require(route_id in allocated and route["enabled"])
                allocation = allocated[route_id]
                require(all(route[k] == allocation[k] for k in ROUTE_FIELDS.split() if k != "purpose"))
                require(entry["purpose"] == allocation["purpose"])
                require(route["backend_login_oids"] == [connections[entry["purpose"]]["login_oid"]])
            seen_routes.add(route_id)
    require(seen_sessions == set(sessions) and seen_routes == set(routes))
