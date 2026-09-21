"""Exact finite allocation controls; observed ACL tuples never define authority."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from maezo.gateway import native_owner_allocation as a
from maezo.platform.engine_bootstrap import controller_storage as s
from maezo.platform.engine_bootstrap.native_catalog_transition import exact_initial_grant_union
from maezo.platform.engine_bootstrap.native_owner_contracts import ACT_SELECT_COLUMNS


def vector():
    root = Path(__file__).parents[3]
    manifest = json.loads(
        (
            root / "src/maezo/portal/engine/java/src/main/resources/native-acquisition-v2-migration.json"
        ).read_bytes()
    )
    names = frozenset(row["name"] for row in manifest["functions"])
    catalog = dict(
        namespace=dict(oid=20, owner=21),
        relations=[
            dict(name=name, oid=30 + i, owner=21, kind="r") for i, name in enumerate(sorted(a.TABLES))
        ],
        functions=[
            dict(name=name, oid=50 + i, owner=22, arguments="") for i, name in enumerate(sorted(names))
        ],
    )
    binding = dict(
        schema_owner_oid=21,
        function_owner_oid=22,
        issuer_role_oid=23,
        runtime_role_oid=24,
        act_schema="act",
        act_schema_oid=25,
    )
    return catalog, binding, names


def allocation():
    catalog, binding, names = vector()
    return a.source_allocation(
        catalog, binding, names, act_relation_oid=90, act_schema_owner_oid=91, act_relation_owner_oid=92
    )


def test_exact_act_schema_and_eleven_individual_columns_are_required():
    grants = allocation()
    act = [g for g in grants if g["schema_name"] == "act"]
    assert len(act) == 12
    schema = [g for g in act if g["object_class"] == "schema"]
    assert len(schema) == 1 and schema[0]["privilege"] == "USAGE" and schema[0]["grantor_oid"] == 91
    columns = [g for g in act if g["object_class"] == "column"]
    assert {tuple(g["column_names"]) for g in columns} == {(column,) for column in ACT_SELECT_COLUMNS}
    assert all(
        g["object_oid"] == 90
        and g["grantor_oid"] == 92
        and g["grantee_oid"] == 22
        and g["privilege"] == "SELECT"
        and g["grantable"] is False
        for g in columns
    )
    assert not any(g["object_class"] == "table" for g in act)


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema_name", "other"),
        ("object_oid", 999),
        ("column_names", ["id_", "secret"]),
        ("grantor_oid", 99),
        ("grantee_oid", 24),
        ("privilege", "UPDATE"),
        ("grantable", True),
    ],
)
def test_act_tamper_refused_even_with_unchanged_native_catalogue(field, value):
    grants = allocation()
    actual = [dict(g) for g in grants]
    target = next(g for g in actual if g["schema_name"] == "act" and g["object_class"] == "column")
    target[field] = value
    with pytest.raises(s.Refusal):
        exact_initial_grant_union([], actual, grants)


def test_missing_extra_native_objects_and_wrong_owners_cannot_define_allocation():
    catalog, binding, names = vector()
    catalog["functions"][0]["owner"] = 999
    with pytest.raises(s.Refusal):
        a.source_allocation(
            catalog, binding, names, act_relation_oid=90, act_schema_owner_oid=91, act_relation_owner_oid=92
        )
