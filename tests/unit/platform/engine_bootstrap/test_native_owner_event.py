"""Finite native event port/cursor/replay controls; no DB or admission authority."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from tests.unit.platform.engine_bootstrap.test_d7_control_migration_contract import (
    PACKAGE,
    SQL,
    catalog,
    owner,
)
from tests.unit.platform.engine_bootstrap.test_native_owner_contracts import record_vector

from maezo.platform.engine_bootstrap import controller_storage as s
from maezo.platform.engine_bootstrap import native_catalog_transition as transition
from maezo.platform.engine_bootstrap import native_owner_contracts as n


def data():
    value = record_vector()
    native = s.parse_wire(n.qualification_from_receipt(s.canonical(value)))
    birth_wire = s.decode64(value["installed_record"]["preparation_receipt_bytes"])
    old = s.Document("OwnerPreparationResult", birth_wire).value()
    event = dict(
        protocol="maezo.d7-owner-preparation-event.v1",
        installation_id=old["installation_id"],
        owner_operation_id=old["owner_operation_id"],
        preparation_id=old["preparation_id"],
        event="NATIVE_QUALIFIED",
        expected_previous_event_sha256=s.digest(birth_wire),
        native_qualification=native,
        terminal_proof_bytes=None,
        terminal_proof_sha256=None,
    )
    f = dict(
        database_binding_sha256=old["database_binding_sha256"],
        mode="CLOSED",
        current_generation_id=None,
        restore_state="RECONCILED",
    )
    g = dict(
        status="PREPARED",
        phase="UNADMITTED",
        opened_at_ms=None,
        retired_at_ms=None,
        activation_decision_sha256=native["decision_sha256"],
    )
    return event, old, birth_wire, f, g


class PortCursor:
    def __init__(self, birth):
        self.birth = birth
        self.calls = []

    def execute(self, sql, params=()):
        self.calls.append(sql)

    def fetchone(self):
        return self.birth, s.digest(self.birth), "a" * 64


def test_native_port_receives_exact_enlisted_cursor_and_must_return_actual_catalog(monkeypatch):
    event, old, birth, f, g = data()
    event["scope"] = old["scope"]
    cursor = PortCursor(birth)
    observed = []
    before = s.canonical(catalog())
    monkeypatch.setattr(transition, "retained_manifest", lambda cursor, version: before)

    class NativePort:
        def verify(self, **kwargs):
            observed.append(kwargs)
            return kwargs["actual_catalog_wire"]

    class Installer(owner.OwnerInstaller):
        def _catalog(self, *args):
            return s.parse_wire(before)

    installed = Installer(SimpleNamespace(), PACKAGE, SQL.encode(), SimpleNamespace(), NativePort())
    version = dict(installation_id=old["installation_id"])
    session = ("migration", "migration", 999)
    assert installed._native_transition(cursor, event, session, f, version, g, old, birth) == before
    assert observed[0]["cursor"] is cursor and observed[0]["birth_wire"] == birth
    assert observed[0]["session"] is session and observed[0]["actual_catalog_wire"] == before
    installed.native_authority = SimpleNamespace(verify=lambda **kwargs: None)
    with pytest.raises(s.Refusal):
        installed._native_transition(cursor, event, session, f, version, g, old, birth)


def test_native_replay_checks_current_native_authority_before_any_replay_return():
    event, old, birth, f, g = data()
    cursor = PortCursor(birth)
    calls = []

    class Installer(owner.OwnerInstaller):
        def _owner_context(self, *args, **kwargs):
            assert kwargs == {"native_transition": True}
            return f, {}, g

        def _native_transition(self, *args):
            calls.append("current-native")
            return b"{}"

        def _replay(self, *args):
            calls.append("retained-replay")
            return b"{}"

    authority = SimpleNamespace(replay=lambda **kwargs: calls.append("current-external"))
    installed = Installer(SimpleNamespace(), PACKAGE, SQL.encode(), authority, SimpleNamespace())
    assert installed._event(cursor, s.canonical(event), ("migration", "migration", 999)) == b"{}"
    assert calls == ["current-native", "retained-replay", "current-external"]
    assert cursor.calls[-1] == "SELECT maezo_d7_control._assert_catalog()"

    def refuse(*args):
        raise s.Refusal("AUTH_REFUSED")

    installed._native_transition = refuse
    calls.clear()
    with pytest.raises(s.Refusal):
        installed._event(cursor, s.canonical(event), ("migration", "migration", 999))
    assert calls == []
