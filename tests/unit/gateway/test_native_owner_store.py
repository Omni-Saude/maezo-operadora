"""Finite DBAPI protocol mechanics; no real PG, owner or D admission claim."""

from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

import pytest

from maezo.gateway import native_owner_store as store
from maezo.platform.engine_bootstrap import controller_storage as s
from maezo.platform.engine_bootstrap import native_owner_contracts as n
from tests.unit.platform.engine_bootstrap.test_native_owner_contracts import record_vector

ROOT = Path(__file__).parents[3]
_spec = importlib.util.spec_from_file_location(
    "native_owner_installer_tested",
    ROOT / "deploy/cibseven/secured/migrations/native_owner_qualification_install.py",
)
assert _spec and _spec.loader
installer = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(installer)
PACKAGE = (ROOT / "deploy/cibseven/secured/migrations/native-owner-qualification-0001.json").read_bytes()
TEMPLATE = (
    ROOT / "src/maezo/portal/engine/java/src/main/resources/native-owner-qualification-v1-postgres.sql"
).read_bytes()
U = "00000000-0000-4000-8000-000000000001"


def pins(**changes):
    return store.StorePins(
        **(
            dict(
                installation_id=U,
                owner_login_name="migration",
                owner_login_oid=999,
                schema_owner_name="native_owner",
                schema_owner_oid=8,
                database_oid=6,
                protected_role_oids=(2, 3, 4, 5),
                source_manifest_sha256=s.digest(PACKAGE),
                ddl_sha256=s.digest(TEMPLATE.replace(b"__SCHEMA_OWNER__", b'"native_owner"')),
            )
            | changes
        )
    )


def catalog():
    return dict(
        namespace={"oid": 7, "owner": 8, "acl": "owner"},
        relations=[
            dict(name=name, owner=8, kind="r", rls=False, force_rls=False)
            for name in ("installation", "principal_qualification_receipt")
        ],
        functions=[dict(name=name, owner=8) for name in ("deny_mutation", "stamp_creator")],
        default_acls=[],
    )


class StoreCursor:
    def __init__(self):
        self.calls = []
        self.session = (
            "migration",
            "migration",
            999,
            6,
            160004,
            "read committed",
            "off",
            False,
            False,
            False,
            False,
            False,
            True,
        )
        self.owner_role = ("native_owner", False, False, False, False, False, False)
        self.members = (4, True)
        self.catalog = catalog()
        self.retained_catalog = s.canonical(self.catalog)
        self.acl = (True, True)
        self.operation = None
        self.rows = iter(())

    def execute(self, query, params=()):
        self.calls.append((query, params))
        if query == store.SESSION_SQL:
            row = self.session
        elif query == store.CATALOG_SQL:
            row = (self.catalog,)
        elif query == store.ACL_SQL:
            row = self.acl
        elif query == store.INSTALLATION_SQL:
            p = pins()
            row = (
                p.installation_id,
                p.source_manifest_sha256,
                p.ddl_sha256,
                6,
                7,
                8,
                999,
                "migration",
                self.retained_catalog,
                s.digest(self.retained_catalog),
            )
        elif query.startswith("SELECT rolname,"):
            row = self.owner_role
        elif query.startswith("SELECT count(*)"):
            row = self.members
        elif query == store.OPERATION_SQL:
            row = self.operation
        elif query.startswith("INSERT INTO maezo_native_owner_v1.principal_qualification_receipt"):
            row = None
        else:
            raise AssertionError("unexpected query")
        self.rows = iter(()) if row is None else iter((row,))

    def fetchone(self):
        return next(self.rows, None)


def test_actual_enlisted_store_read_checks_authenticated_session_and_retained_catalogue():
    cursor = StoreCursor()
    assert store.read_installation(cursor, pins()) == cursor.retained_catalog
    assert cursor.calls[0][0] == store.SESSION_SQL
    assert cursor.calls[-1][0] == store.ACL_SQL


@pytest.mark.parametrize(
    "position,value",
    [
        (0, "foreign"),
        (1, "assumed"),
        (2, 998),
        (3, 5),
        (4, 150010),
        (5, "repeatable read"),
        (6, "on"),
        (7, True),
        (8, True),
        (9, True),
        (10, True),
        (11, True),
        (12, False),
    ],
)
def test_each_actual_session_invariant_is_checked(position, value):
    cursor = StoreCursor()
    row = list(cursor.session)
    row[position] = value
    cursor.session = tuple(row)
    with pytest.raises(s.Refusal):
        store.read_installation(cursor, pins())
    assert not any(query.startswith("INSERT") for query, _ in cursor.calls)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda c: c.catalog["namespace"].update(owner=99),
        lambda c: c.catalog["relations"].append(
            dict(name="extra", owner=8, kind="r", rls=False, force_rls=False)
        ),
        lambda c: c.catalog["relations"][0].update(rls=True),
        lambda c: c.catalog["functions"][0].update(owner=99),
        lambda c: c.catalog.update(default_acls=[{}]),
        lambda c: setattr(c, "acl", (False, True)),
        lambda c: setattr(c, "acl", (True, False)),
        lambda c: setattr(c, "members", (4, False)),
        lambda c: setattr(c, "members", (3, True)),
    ],
)
def test_store_custody_rejects_catalogue_and_inherited_privilege_drift(mutation):
    cursor = StoreCursor()
    mutation(cursor)
    with pytest.raises(s.Refusal):
        store.read_installation(cursor, pins())


def retained_row(value):
    raw = s.canonical(value)
    qraw = s.decode64(value["request_bytes"])
    binding = n.qualification_from_receipt(raw)
    q = n.request(qraw)
    return (
        qraw,
        s.digest(qraw),
        raw,
        s.digest(raw),
        binding,
        s.digest(binding),
        True,
        q["owner_installation_id"],
        q["native_installation_receipt_sha256"],
        q["d_preparation_id"],
    )


def test_retained_operation_keeps_exact_bytes_and_rejects_corruption():
    cursor = StoreCursor()
    value = record_vector()
    cursor.operation = retained_row(value)
    operation_id = n.request(s.decode64(value["request_bytes"]))["native_owner_operation_id"]
    retained = store.read_operation(cursor, operation_id)
    assert retained == (cursor.operation[0], cursor.operation[2], cursor.operation[4])
    corrupt = list(cursor.operation)
    corrupt[3] = "f" * 64
    cursor.operation = tuple(corrupt)
    with pytest.raises(s.Refusal):
        store.read_operation(cursor, operation_id)


def test_operation_projection_is_persisted_once_exact_replay_has_no_second_insert():
    value = record_vector()
    q = n.request(s.decode64(value["request_bytes"]))
    p = pins(installation_id=q["owner_installation_id"])
    cursor = StoreCursor()
    raw = s.canonical(value)
    binding = store.append_operation(cursor, p, raw)
    assert binding == n.qualification_from_receipt(raw)
    assert sum(query.startswith("INSERT") for query, _ in cursor.calls) == 1
    cursor.calls.clear()
    cursor.operation = retained_row(value)
    assert store.append_operation(cursor, p, raw) == binding
    assert not any(query.startswith("INSERT") for query, _ in cursor.calls)
    changed = copy.deepcopy(value)
    changed["recorded_before_commit_at_ms"] += 1
    with pytest.raises(s.Refusal, match="REQUEST_CONFLICT"):
        store.append_operation(cursor, p, s.canonical(changed))


def test_renderer_requires_both_pinned_source_and_exact_rendered_ddl():
    sql = installer.render(PACKAGE, TEMPLATE, pins())
    assert b"__SCHEMA_OWNER__" not in sql and b'"native_owner"' in sql
    with pytest.raises(s.Refusal):
        installer.render(PACKAGE, TEMPLATE + b"\n", pins())
    with pytest.raises(s.Refusal):
        installer.render(PACKAGE, TEMPLATE, pins(ddl_sha256="f" * 64))
    malformed = json.loads(PACKAGE)
    malformed["execution_authorized"] = True
    wire = s.canonical(malformed)
    with pytest.raises(s.Refusal):
        installer.render(wire, TEMPLATE, pins(source_manifest_sha256=s.digest(wire)))


def test_store_source_has_no_commit_or_role_repair():
    import ast

    tree = ast.parse(Path(store.__file__).read_text())
    assert not [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"commit", "connect"}
    ]
    sql = installer.render(PACKAGE, TEMPLATE, pins())
    assert b"CREATE ROLE" not in sql and b"GRANT " not in sql


def test_d_readback_cannot_accept_a_row_inserted_by_its_own_uncommitted_transaction():
    cursor = StoreCursor()
    row = list(retained_row(record_vector()))
    row[6] = False
    cursor.operation = tuple(row)
    operation_id = n.request(row[0])["native_owner_operation_id"]
    with pytest.raises(s.Refusal):
        store.read_operation(cursor, operation_id)


@pytest.mark.parametrize("column", [7, 8, 9])
def test_physical_owner_operation_identity_matches_its_closed_retained_request(column):
    cursor = StoreCursor()
    row = list(retained_row(record_vector()))
    row[column] = "mismatched"
    cursor.operation = tuple(row)
    with pytest.raises(s.Refusal):
        store.read_operation(cursor, n.request(row[0])["native_owner_operation_id"])
