"""ADR-0054 exact frozen native/ACT initial ACL allocation from actual object OIDs."""

from __future__ import annotations

from typing import Any

from maezo.platform.engine_bootstrap.controller_storage import require, scalar
from maezo.platform.engine_bootstrap.native_owner_contracts import ACT_SELECT_COLUMNS

RUNTIME_FUNCTIONS = frozenset(
    {
        "guard_runtime_v2",
        "validate_guard_v2",
        "read_receipt_v2",
        "read_acquisitions_v2",
        "insert_acquisition_v2",
        "renew_acquisition_v2",
        "close_acquisition_v2",
        "append_receipt_v2",
    }
)
ISSUER_FUNCTIONS = frozenset(
    {"register_admission_v2", "refresh_admission_v2", "retire_admission_v2", "set_receipt_history_v2"}
)
TABLES = frozenset(
    {"schema_version", "admission", "capability", "acquisition", "operation_receipt", "receipt_history"}
)


def source_allocation(
    native_catalog: dict[str, Any],
    binding: dict[str, Any],
    function_names: frozenset[str],
    *,
    act_relation_oid: int,
    act_schema_owner_oid: int,
    act_relation_owner_oid: int,
) -> list[dict[str, Any]]:
    """Caller independently verifies source/catalogue and actual inherited ACT owner.

    Grantors are actual pinned object owners, not copied from observed ACL tuples.
    A migration route using another grantor cannot silently qualify this profile.
    """
    for oid in (act_relation_oid, act_schema_owner_oid, act_relation_owner_oid):
        scalar("Oid", oid)
    namespace = native_catalog["namespace"]
    require(namespace["owner"] == binding["schema_owner_oid"], "AUTH_REFUSED")
    relations = native_catalog["relations"]
    # catalogue_v2 includes indexes as relations; D _grant_catalog only includes
    # actual tables. Index definitions/custody are authenticated by native digest.
    tables = {r["name"]: r for r in relations if r["kind"] == "r"}
    require(set(tables) == TABLES and sum(r["kind"] == "r" for r in relations) == 6, "PRECONDITION_MISMATCH")
    require(not any(r["kind"] not in {"r", "i"} for r in relations), "PRECONDITION_MISMATCH")
    functions = {f["name"]: f for f in native_catalog["functions"]}
    require(
        set(functions) == function_names and len(native_catalog["functions"]) == len(function_names) == 19,
        "PRECONDITION_MISMATCH",
    )
    require(function_names >= RUNTIME_FUNCTIONS | ISSUER_FUNCTIONS, "PRECONDITION_MISMATCH")
    grants: list[dict[str, Any]] = []

    def add(
        kind: str,
        schema: str,
        name: str,
        oid: int,
        grantee: int,
        grantor: int,
        privilege: str,
        *,
        columns: list[str] | None = None,
        arguments: list[str] | None = None,
    ) -> None:
        grants.append(
            dict(
                object_class=kind,
                schema_name=schema,
                object_name=name,
                object_oid=oid,
                function_argument_types=[] if arguments is None else arguments,
                column_names=[] if columns is None else columns,
                grantee_oid=grantee,
                grantor_oid=grantor,
                privilege=privilege,
                grantable=False,
            )
        )

    schema = "maezo_native_v2"
    owner = binding["schema_owner_oid"]
    function_owner = binding["function_owner_oid"]
    for privilege in ("USAGE", "CREATE"):
        add("schema", schema, schema, namespace["oid"], owner, owner, privilege)
    for role in ("function_owner_oid", "issuer_role_oid", "runtime_role_oid"):
        add("schema", schema, schema, namespace["oid"], binding[role], owner, "USAGE")
    for name, table in tables.items():
        require(table["owner"] == owner, "AUTH_REFUSED")
        for privilege in ("INSERT", "SELECT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"):
            add("table", schema, name, table["oid"], owner, owner, privilege)
        privileges = (
            ("SELECT",)
            if name == "schema_version"
            else ("SELECT", "INSERT")
            if name == "operation_receipt"
            else ("SELECT", "INSERT", "UPDATE")
        )
        for privilege in privileges:
            add("table", schema, name, table["oid"], function_owner, owner, privilege)
        if name == "operation_receipt":
            add("column", schema, name, table["oid"], function_owner, owner, "UPDATE", columns=["command_id"])
    for name, function in functions.items():
        require(function["owner"] == function_owner, "AUTH_REFUSED")
        arguments = function["arguments"].split(", ") if function["arguments"] else []
        add(
            "function",
            schema,
            name,
            function["oid"],
            function_owner,
            function_owner,
            "EXECUTE",
            arguments=arguments,
        )
        function_role = (
            "runtime_role_oid"
            if name in RUNTIME_FUNCTIONS
            else "issuer_role_oid"
            if name in ISSUER_FUNCTIONS
            else None
        )
        if function_role is not None:
            add(
                "function",
                schema,
                name,
                function["oid"],
                binding[function_role],
                function_owner,
                "EXECUTE",
                arguments=arguments,
            )
    add(
        "schema",
        binding["act_schema"],
        binding["act_schema"],
        binding["act_schema_oid"],
        function_owner,
        act_schema_owner_oid,
        "USAGE",
    )
    for column in ACT_SELECT_COLUMNS:
        add(
            "column",
            binding["act_schema"],
            "act_ru_ext_task",
            act_relation_oid,
            function_owner,
            act_relation_owner_oid,
            "SELECT",
            columns=[column],
        )
    return grants
