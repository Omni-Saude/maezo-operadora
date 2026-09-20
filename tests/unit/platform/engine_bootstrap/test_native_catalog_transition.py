"""Finite SQL response controls for every ADR-0054 D guard equivalence family."""

from __future__ import annotations

import copy

import pytest
from tests.unit.platform.engine_bootstrap.test_controller_storage import H, U
from tests.unit.platform.engine_bootstrap.test_d7_control_migration_contract import catalog

from maezo.platform.engine_bootstrap import controller_storage as s
from maezo.platform.engine_bootstrap import native_catalog_transition as t


def manifests():
    before = catalog()
    role = copy.deepcopy(before["roles"][0])
    role.update(role_class="generation login", role_name="candidate", role_oid=17, rolcanlogin=False)
    before["roles"].append(role)
    return before, copy.deepcopy(before)


class GuardCursor:
    def __init__(self, before):
        self.calls = []
        self.rows = iter(())
        self.before = before
        self.function = (100, H)
        self.counts = (1, 7)
        self.generation = None
        self.permit = None
        self.connect = False
        self.overlays = []
        self.births = []
        self.result_hash = None

    def execute(self, query, params=()):
        self.calls.append((query, params))
        if "SELECT result_bytes,result_sha256" in query:
            raw = s.canonical(
                dict(
                    installation_id=U,
                    owner_catalog_bytes=s.encode64(s.canonical(self.before)),
                    owner_catalog_sha256=s.digest(s.canonical(self.before)),
                )
            )
            row = (raw, self.result_hash or s.digest(raw))
        elif "pg_get_functiondef" in query:
            row = self.function
        elif "SELECT count(*)" in query:
            row = self.counts
        elif "SELECT g.status" in query:
            row = self.generation
        elif "SELECT p.state" in query:
            row = self.permit
        elif "has_database_privilege" in query:
            row = (self.connect,)
        elif "jsonb_agg(login_oid" in query:
            row = (self.overlays,)
        elif "jsonb_build_object('oid',b.login_oid" in query:
            row = (self.births,)
        else:
            raise AssertionError("unexpected query")
        self.rows = iter(()) if row is None else iter((row,))

    def fetchone(self):
        return next(self.rows, None)


def version():
    wire = s.canonical(
        {"objects": [dict(object_class="function", object_oid=22, owner_oid=100, definition_sha256=H)]}
    )
    return dict(
        installation_id=U,
        object_manifest_bytes="\\x" + wire.hex(),
        object_manifest_sha256=s.digest(wire),
        control_schema_oid=9,
        database_oid=6,
        database_name="synthetic",
    )


def verify(cursor, before, actual):
    return t.verify_non_grant_invariants(cursor, version(), s.canonical(before), s.canonical(actual))


def test_finite_unchanged_catalog_invariants_return_previous_grants_only():
    before, actual = manifests()
    cursor = GuardCursor(before)
    assert verify(cursor, before, actual) == []
    assert not any("_assert_catalog" in query or query.startswith("INSERT") for query, _ in cursor.calls)


@pytest.mark.parametrize(
    "field,value",
    [
        ("role_name", "reused"),
        ("role_oid", 18),
        ("rolcanlogin", True),
        ("rolsuper", True),
        ("rolcreaterole", True),
        ("rolcreatedb", True),
        ("rolreplication", True),
        ("rolbypassrls", True),
        ("rolinherit", True),
    ],
)
def test_each_target_role_attribute_cannot_be_excluded(field, value):
    before, actual = manifests()
    actual["roles"][-1][field] = value
    with pytest.raises(s.Refusal):
        verify(GuardCursor(before), before, actual)


@pytest.mark.parametrize(
    "field,value",
    [
        ("role_oid", 1),
        ("member_oid", 2),
        ("grantor_oid", 3),
        ("admin_option", True),
        ("inherit_option", True),
        ("set_option", True),
    ],
)
def test_complete_membership_edge_and_options_must_match(field, value):
    before, actual = manifests()
    edge = dict(
        role_oid=100,
        member_oid=999,
        grantor_oid=100,
        admin_option=False,
        inherit_option=False,
        set_option=False,
    )
    before["memberships"] = [edge]
    actual["memberships"] = [dict(edge, **{field: value})]
    with pytest.raises(s.Refusal):
        verify(GuardCursor(before), before, actual)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda c: setattr(c, "function", (99, H)),
        lambda c: setattr(c, "function", (100, "f" * 64)),
        lambda c: setattr(c, "counts", (2, 7)),
        lambda c: setattr(c, "counts", (1, 8)),
        lambda c: setattr(c, "result_hash", "f" * 64),
    ],
)
def test_object_source_owner_count_and_retained_result_hash_are_checked(mutation):
    before, actual = manifests()
    cursor = GuardCursor(before)
    mutation(cursor)
    with pytest.raises(s.Refusal):
        verify(cursor, before, actual)


@pytest.mark.parametrize(
    "generation,permit,connect",
    [(("OPEN", False), None, True), (None, ("REVOKED", False), False), (None, None, True)],
)
def test_login_connect_overlay_requires_matching_receipt(generation, permit, connect):
    before, actual = manifests()
    cursor = GuardCursor(before)
    cursor.generation = generation
    cursor.permit = permit
    cursor.connect = connect
    with pytest.raises(s.Refusal):
        verify(cursor, before, actual)


def test_genuine_finite_receipt_backed_open_overlay_is_exact_and_grantor_pinned():
    before, actual = manifests()
    actual["roles"][-1]["rolcanlogin"] = True
    cursor = GuardCursor(before)
    cursor.generation = ("OPEN", True)
    cursor.connect = True
    cursor.overlays = [17]
    grants = verify(cursor, before, actual)
    assert grants == [
        dict(
            object_class="database",
            schema_name="",
            object_name="synthetic",
            object_oid=6,
            function_argument_types=[],
            column_names=[],
            grantee_oid=17,
            grantor_oid=102,
            privilege="CONNECT",
            grantable=False,
        )
    ]


@pytest.mark.parametrize(
    "birth",
    [
        dict(oid=17, name="candidate", removed=True, live=True),
        dict(oid=88, name="absent", removed=False, live=True),
    ],
)
def test_prepared_removed_birth_existence_and_nonreuse_are_preserved(birth):
    before, actual = manifests()
    cursor = GuardCursor(before)
    cursor.births = [birth]
    with pytest.raises(s.Refusal):
        verify(cursor, before, actual)


def test_public_grant_refused_even_before_source_allocation_comparison():
    before, actual = manifests()
    actual["object_grants"] = [dict(grantee_oid=0)]
    with pytest.raises(s.Refusal):
        verify(GuardCursor(before), before, actual)


def test_union_preserves_each_original_tuple_and_rejects_duplicate_actual_entries():
    previous = [dict(object_oid=1, privilege="USAGE")]
    allocation = [dict(object_oid=2, privilege="SELECT")]
    t.exact_initial_grant_union(previous, previous + allocation, allocation)
    for bad in (
        allocation,
        previous,
        previous + allocation + allocation,
        [dict(object_oid=1, privilege="CREATE")] + allocation,
    ):
        with pytest.raises(s.Refusal):
            t.exact_initial_grant_union(previous, bad, allocation)
