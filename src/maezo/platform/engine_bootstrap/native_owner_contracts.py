"""ADR-0054 closed initial native-owner records; values never authenticate an owner."""

from __future__ import annotations

import json
from typing import Any

from maezo.platform.engine_bootstrap.controller_storage import (
    Document,
    canonical,
    decode64,
    digest,
    encode64,
    exact,
    parse_wire,
    require,
    scalar,
    validate_native_qualification,
)

INSTALLED_FIELDS = (
    "migration_key manifest_digest act_schema act_schema_oid database_oid database_binding_sha256 "
    "function_owner issuer_role d_helper_oid abi_digest migration_receipt preparation_binding "
    "preparation_receipt_bytes catalogue_digest"
)
SELECTOR_FIELDS = (
    "tenant environment engine_name account region database_incarnation generation_id epoch run_id "
    "purpose preparation_id receipt_sha256 generation_core_sha256 decision_sha256 "
    "native_function_owner_oid native_issuer_oid login_name login_oid d_schema_owner "
    "d_schema_owner_oid database_binding_sha256"
)
REQUEST_FIELDS = (
    "protocol native_owner_operation_id owner_installation_id d_installation_id d_preparation_id "
    "native_installation_receipt_sha256 scope generation_id purpose login_name login_oid "
    "generation_core_sha256 decision_sha256 database_binding_sha256 before_catalog_sha256"
)
RECEIPT_FIELDS = (
    "protocol request_bytes request_sha256 installed_record installed_record_sha256 "
    "native_catalog_bytes native_catalog_sha256 d_before_catalog_bytes d_before_catalog_sha256 "
    "d_after_catalog_bytes d_after_catalog_sha256 owner_store_catalog_sha256 "
    "owner_session_user owner_login_oid owner_effective_user owner_effective_oid "
    "recorded_before_commit_at_ms"
)
ABI_SHA256 = "b0c5e39a02becb61903559a58dceecd440d7102efcb6c66ef3878876632dd71d"
ACT_SELECT_COLUMNS = (
    "id_",
    "tenant_id_",
    "proc_def_id_",
    "proc_inst_id_",
    "execution_id_",
    "topic_name_",
    "worker_id_",
    "lock_exp_time_",
    "suspension_state_",
    "retries_",
    "rev_",
)


def _id(value: Any) -> None:
    scalar("Id", value)
    require(len(value.encode("utf8")) <= 63)


def _pair(value: dict[str, Any], key: str, checksum: str) -> bytes:
    raw = decode64(value[key])
    require(digest(raw) == value[checksum])
    parse_wire(raw)
    return raw


def installed_record(value: dict[str, Any]) -> bytes:
    """Canonical complete observed schema_version, not a historical COMMIT claim."""
    exact(value, INSTALLED_FIELDS)
    require(value["migration_key"] == "native-acquisition-v2")
    for name in ("manifest_digest", "database_binding_sha256", "abi_digest", "catalogue_digest"):
        scalar("Sha256", value[name])
    require(value["abi_digest"] == ABI_SHA256)
    _id(value["act_schema"])
    scalar("Id", value["migration_receipt"])
    for name in ("act_schema_oid", "database_oid", "function_owner", "issuer_role", "d_helper_oid"):
        scalar("Oid", value[name])
    selector = exact(value["preparation_binding"], SELECTOR_FIELDS)
    scope = {key: selector[key] for key in ("tenant", "environment", "engine_name", "account", "region")}
    for name in ("database_incarnation", "run_id", "preparation_id"):
        scalar("Uuid", selector[name])
    for name in ("generation_id", "epoch"):
        scalar("Positive", selector[name])
    require(selector["purpose"] in {"candidate", "runtime"})
    for name in ("receipt_sha256", "generation_core_sha256", "decision_sha256", "database_binding_sha256"):
        scalar("Sha256", selector[name])
    for name in ("native_function_owner_oid", "native_issuer_oid", "login_oid", "d_schema_owner_oid"):
        scalar("Oid", selector[name])
    _id(selector["login_name"])
    _id(selector["d_schema_owner"])
    require(selector["native_function_owner_oid"] == value["function_owner"])
    require(selector["native_issuer_oid"] == value["issuer_role"])
    require(selector["database_binding_sha256"] == value["database_binding_sha256"])
    raw = decode64(value["preparation_receipt_bytes"])
    require(digest(raw) == selector["receipt_sha256"])
    birth = Document("OwnerPreparationResult", raw).value()
    require(birth["event"] == "PREPARED" and birth["preparation_id"] == selector["preparation_id"])
    require(
        all(birth["scope"][key] == scope[key] for key in scope)
        and birth["database_binding_sha256"] == value["database_binding_sha256"]
    )
    binding = birth["scope"]["database_binding"]
    require(digest(canonical(binding)) == value["database_binding_sha256"])
    require(binding["database_incarnation"] == selector["database_incarnation"])
    require(
        binding["database_oid"] == value["database_oid"]
        and binding["schema_oid"] == value["act_schema_oid"]
        and binding["schema_name"] == value["act_schema"]
    )
    principal = birth["principal"]
    require(principal["kind"] == "generation")
    require(
        all(
            principal[key] == selector[key] for key in ("generation_id", "purpose", "login_name", "login_oid")
        )
    )
    require(digest(canonical(birth["generation_core"])) == selector["generation_core_sha256"])
    require(birth["generation_core"]["epoch"] == selector["epoch"])
    return canonical(value)


def request(raw: bytes) -> dict[str, Any]:
    value = exact(parse_wire(raw), REQUEST_FIELDS)
    require(canonical(value) == raw)
    require(value["protocol"] == "maezo.native-owner-initial-qualification-request.v1")
    for name in (
        "native_owner_operation_id",
        "owner_installation_id",
        "d_installation_id",
        "d_preparation_id",
    ):
        scalar("Uuid", value[name])
    for name in (
        "native_installation_receipt_sha256",
        "generation_core_sha256",
        "decision_sha256",
        "database_binding_sha256",
        "before_catalog_sha256",
    ):
        scalar("Sha256", value[name])
    scalar("Scope", value["scope"])
    scalar("Positive", value["generation_id"])
    scalar("Oid", value["login_oid"])
    _id(value["login_name"])
    require(value["purpose"] in {"candidate", "runtime"})
    return value


def receipt(raw: bytes) -> dict[str, Any]:
    value = exact(parse_wire(raw), RECEIPT_FIELDS)
    require(canonical(value) == raw)
    require(value["protocol"] == "maezo.native-owner-initial-qualification-receipt.v1")
    q = request(_pair(value, "request_bytes", "request_sha256"))
    installed = value["installed_record"]
    require(
        digest(installed_record(installed))
        == value["installed_record_sha256"]
        == q["native_installation_receipt_sha256"]
    )
    selector = installed["preparation_binding"]
    require(q["d_preparation_id"] == selector["preparation_id"])
    for key in (
        "generation_id",
        "purpose",
        "login_name",
        "login_oid",
        "generation_core_sha256",
        "decision_sha256",
        "database_binding_sha256",
    ):
        require(q[key] == selector[key])
    require(
        all(
            q["scope"][key] == selector[key]
            for key in ("account", "region", "tenant", "environment", "engine_name")
        )
    )
    birth = Document("OwnerPreparationResult", decode64(installed["preparation_receipt_bytes"])).value()
    require(q["d_installation_id"] == birth["installation_id"] and q["scope"] == birth["scope"])
    # Native catalogue hash is PostgreSQL jsonb::text SHA, not Python canonical SHA.
    native_raw = decode64(value["native_catalog_bytes"])
    require(
        digest(native_raw)
        == value["native_catalog_sha256"]
        == installed["catalogue_digest"]
        == q["before_catalog_sha256"]
    )
    try:
        # PostgreSQL jsonb::text spacing is part of the frozen native digest.
        parse_wire(canonical(json.loads(native_raw)))
    except (ValueError, TypeError, RecursionError):
        require(False)
    for prefix in ("d_before_catalog", "d_after_catalog"):
        _pair(value, prefix + "_bytes", prefix + "_sha256")
    scalar("Sha256", value["owner_store_catalog_sha256"])
    _id(value["owner_session_user"])
    _id(value["owner_effective_user"])
    scalar("Oid", value["owner_login_oid"])
    scalar("Oid", value["owner_effective_oid"])
    require(value["owner_session_user"] == value["owner_effective_user"])
    require(value["owner_login_oid"] == value["owner_effective_oid"])
    scalar("UInt", value["recorded_before_commit_at_ms"])
    return value


def qualification_from_receipt(raw: bytes) -> bytes:
    """Pure projection only; caller must establish committed owner-store authority."""
    value = receipt(raw)
    q = request(decode64(value["request_bytes"]))
    outer = {
        key: q[key]
        for key in (
            "native_installation_receipt_sha256",
            "native_owner_operation_id",
            "d_preparation_id",
            "scope",
            "generation_id",
            "purpose",
            "generation_core_sha256",
            "decision_sha256",
            "login_name",
            "login_oid",
            "database_binding_sha256",
            "before_catalog_sha256",
        )
    }
    outer.update(
        protocol="maezo.d7-native-principal-qualification-binding.v1",
        helper_abi_sha256=ABI_SHA256,
        after_catalog_sha256=q["before_catalog_sha256"],
        native_receipt_bytes=encode64(raw),
        native_receipt_sha256=digest(raw),
    )
    validate_native_qualification(outer)
    return canonical(outer)
