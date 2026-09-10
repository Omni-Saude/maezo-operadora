"""AUTH-Q1: actual catalog and signed cohort reads, synthetic SQL rows only."""

import pytest
from tests.unit.gateway.human.test_auth_pending_cohort_v2 import native_read_fixture
from tests.unit.gateway.human.test_decision_binding import Connection

from maezo.gateway.human.decision_binding import NATIVE_COHORT_RELATIONS
from maezo.gateway.human.decision_binding_qualification import BindingUnavailableError


@pytest.mark.asyncio
async def test_native_catalog_refuses_hidden_revocation_and_source_guarantee_drift(monkeypatch):
    reader, native_rows, qualified, _, state = native_read_fixture(monkeypatch)
    legacy_rows = Connection(state, {"user": state.d.reader_role})
    pins = {p.name: p for p in reader.native_cohort_read.relations}
    rls = set()
    bad_acl = {}
    bad_trigger = {}
    queried_triggers = set()

    class CatalogRows:
        async def fetchrow(self, sql, *args):
            if "FROM pg_class" in sql and args[1] in pins:
                assert "c.relrowsecurity" in sql
                pin = pins[args[1]]
                # Only RLS changes: OID, owner, ordinary-table kind and ACL stay pinned.
                return dict(
                    oid=pin.oid,
                    owner=pin.owner,
                    relkind="r",
                    relrowsecurity=pin.name in rls,
                    can_select=True,
                    writes=False,
                )
            if "AS native_engine_select" in sql:
                assert args[0] == state.d.engine_role
                assert "has_any_column_privilege" in sql
                return {
                    "native_engine_select": True,
                    "native_engine_column_select": True,
                    "native_engine_writes": False,
                    **bad_acl,
                }
            if "FROM pg_trigger" in sql:
                queried_triggers.add(args[0])
                assert args[1] == state.d.schema_name
                assert "NOT t.tgisinternal" in sql and "count(*) OVER ()" in sql
                return {
                    "tgenabled": "O",
                    "tgtype": 58,
                    "prosrc": "BEGIN RAISE EXCEPTION 'immutable consumer evidence'; END",
                    "prosecdef": False,
                    "trigger_count": 1,
                    **bad_trigger,
                }
            if "SELECT key_id_" in sql and "mzo_human_consumer_revoked" in rls:
                # An extant revocation filtered by RLS: ordinary SELECT sees no row.
                assert native_rows.revoked
                return None
            if (
                "FROM pg_stat_ssl" in sql
                or "FROM pg_roles" in sql
                or "FROM pg_class" in sql
                or "AS engine_select" in sql
            ):
                return await legacy_rows.fetchrow(sql, *args)
            return await native_rows.fetchrow(sql, *args)

    rows = CatalogRows()

    async def current():
        # Same actual catalog/read/recheck boundary used by qualification delivery.
        await reader.catalog(rows)
        first = await reader.native_cohort_current(rows, qualified, 2)
        await reader.catalog(rows)
        assert await reader.native_cohort_current(rows, qualified, 2) == first
        return first

    expected = await current()
    assert expected is not None
    assert queried_triggers == {pin.oid for name, pin in pins.items() if name != "mzo_human_consumer_head"}
    native_rows.revoked = True
    with pytest.raises(BindingUnavailableError):
        await current()
    rls.add("mzo_human_consumer_revoked")
    # This is the precise source-visibility witness: signed read alone is fooled,
    # but the real qualification catalog boundary refuses the same source state.
    assert await reader.native_cohort_current(rows, qualified, 2) == expected
    with pytest.raises(BindingUnavailableError):
        await current()
    rls.clear()
    native_rows.revoked = False
    for name in NATIVE_COHORT_RELATIONS:
        rls.add(name)
        with pytest.raises(BindingUnavailableError):
            await current()
        rls.clear()
    for field, value in (
        ("native_engine_select", False),
        ("native_engine_column_select", False),
        ("native_engine_writes", True),
    ):
        bad_acl[field] = value
        with pytest.raises(BindingUnavailableError):
            await current()
        bad_acl.clear()
    for field, value in (
        ("tgenabled", "D"),
        ("tgtype", 50),
        ("prosrc", "BEGIN RETURN NEW; END"),
        ("prosecdef", True),
        ("trigger_count", 0),
        ("trigger_count", 2),
    ):
        bad_trigger[field] = value
        with pytest.raises(BindingUnavailableError):
            await current()
        bad_trigger.clear()
    assert await current() == expected
