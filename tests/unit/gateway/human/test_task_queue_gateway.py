"""Adversarial Q1 unit proof. Doubles are not qualified engine/cursor providers."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from tests.unit.gateway.human.test_gateway import SCOPE, SECRET, setup, snapshot
from tests.unit.portal.test_human_session import membership

from maezo.gateway.human import gateway as gateway_module
from maezo.gateway.human.queue import (
    CandidateWindow,
    CatalogExpectation,
    CatalogExpectationSource,
    CatalogTrustAnchor,
    CursorCustody,
    CursorGrant,
    HumanTaskQuery,
    QueueBinding,
    ReadRefusalError,
    TaskDisclosureGrant,
    TaskDisclosureSource,
)
from maezo.portal.contracts.models import MembershipBinding, SubjectBinding
from maezo.portal.contracts.queues import TaskQueueRequest

pytestmark = pytest.mark.asyncio
NOW = datetime.now(UTC)


class Clock:
    value = NOW

    @classmethod
    def now(cls, tz=None):
        return cls.value


class Catalog(CatalogExpectationSource):
    def __init__(self):
        self.calls = 0
        self.hook = lambda n: None
        self.values = [
            CatalogExpectation(
                anchor=CatalogTrustAnchor(scope=SCOPE, catalog_ref="catalog", publisher_ref="publisher"),
                catalog_revision=7,
                catalog_digest="a" * 64,
                source_observed_at=NOW,
                valid_until=NOW + timedelta(minutes=3),
            )
        ] * 2

    async def current_catalog(self, *, anchor):
        self.calls += 1
        self.hook(self.calls)
        return self.values[min(self.calls - 1, 1)]


class Discovery(HumanTaskQuery):
    scope = SCOPE

    def __init__(self, ids):
        self.ids = ids
        self.calls = 0
        self.change = lambda value: value

    async def discover(self, principal, request, *, expected_catalog):
        self.calls += 1
        return self.change(
            CandidateWindow(
                binding=QueueBinding(
                    scope=SCOPE,
                    principal=principal,
                    queue=request.queue,
                    limit=request.limit,
                    catalog_revision=expected_catalog.catalog_revision,
                    catalog_ref=expected_catalog.anchor.catalog_ref,
                    publisher_ref=expected_catalog.anchor.publisher_ref,
                    catalog_digest=expected_catalog.catalog_digest,
                ),
                task_ids=self.ids,
                source_observed_at=NOW,
                valid_until=NOW + timedelta(minutes=4),
                next_cursor="candidate" if len(self.ids) > request.limit else None,
            )
        )


class Custody(CursorCustody):
    scope = SCOPE

    def __init__(self):
        self.incoming = None
        self.issued = None
        self.change = lambda value: value

    async def resolve(self, cursor, *, binding):
        if self.incoming is None:
            raise ReadRefusalError("invalid_request")
        return self.incoming

    def finalize(self, cursor, *, binding, after_task_id, valid_until):
        self.issued = self.change(
            CursorGrant(
                cursor="sealed",
                binding=binding,
                after_task_id=after_task_id,
                valid_until=valid_until,
            )
        )
        return self.issued


class Disclosure(TaskDisclosureSource):
    scope = SCOPE

    def __init__(self):
        self.change = lambda value: value

    async def classify(self, principal, task, authority):
        return self.change(
            TaskDisclosureGrant(
                scope=SCOPE,
                principal=principal,
                snapshot=task.snapshot,
                authority_revision=authority.authority_revision,
                classification_ref="class-1",
                classification_digest="c" * 64,
                projection="full_task_detail.v1",
                valid_until=NOW + timedelta(minutes=4),
            )
        )


async def harness(monkeypatch, *, ids=("task-1",), member=None, **changes):
    Clock.value = NOW
    monkeypatch.setattr(gateway_module, "datetime", Clock)
    g, store, transport, authority, admission = await setup(
        snap=snapshot(fixture_at=NOW),
        member=member,
        **changes,
    )
    g._query = Discovery(ids)
    g._catalog_source = Catalog()
    g._catalog_anchor = g._catalog_source.values[0].anchor
    g._cursor_custody = Custody()
    g._disclosure_source = Disclosure()
    original = await g._resolver.resolve(SECRET)
    g.identity = [original, original]
    g.session_calls = 0
    g.session_hook = lambda n: None

    async def resolve(secret):
        g.session_calls += 1
        g.session_hook(g.session_calls)
        if secret != SECRET:
            raise ValueError("private session")
        return g.identity[min(g.session_calls - 1, 1)]

    monkeypatch.setattr(g._resolver, "resolve", resolve)
    g.rows = {
        name: transport.task.model_copy(update={"snapshot": snapshot(fixture_at=NOW, task_id=name)})
        for name in ids or ("task-1",)
    }
    g.authorities = {name: authority.authority.model_copy(update={"task_id": name}) for name in g.rows}
    g.row_calls = []
    g.row_hook = lambda name: None

    async def read(name):
        g.row_calls.append(name)
        g.row_hook(name)
        return g.rows[name]

    async def authorize(principal, task):
        return g.authorities[task.snapshot.task_id]

    monkeypatch.setattr(transport, "read_task", read)
    monkeypatch.setattr(authority, "current_authority", authorize)
    return g


async def page(g, *, queue="team", limit=25, cursor=None, render=lambda x: x):
    return await g.list_tasks(
        session_secret=SECRET,
        request=TaskQueueRequest(queue=queue, limit=limit, cursor=cursor),
        render=render,
    )


async def detail(g, *, render=lambda x: x):
    return await g.read_task_envelope(session_secret=SECRET, task_id="task-1", render=render)


def changed_identity(session, field, deadline):
    owner = "record" if field == "expires_at" else "membership"
    return replace(session, **{owner: getattr(session, owner).model_copy(update={field: deadline})})


@pytest.mark.parametrize("empty", [True, False])
async def test_verified_page_and_minimum_are_real_composition(monkeypatch, empty):
    g = await harness(monkeypatch, ids=() if empty else ("task-1",))
    value = await page(g)
    assert len(value.items) == (0 if empty else 1)
    assert value.freshness.valid_until == NOW + timedelta(minutes=3)
    assert g.session_calls == g._catalog_source.calls == 2
    assert g.row_calls == ([] if empty else ["task-1"])
    assert set(value.model_dump(by_alias=True)) == {"schema", "queue", "items", "next_cursor", "freshness"}


@pytest.mark.parametrize("mode", ["empty", "page", "detail"])
@pytest.mark.parametrize("field", ["expires_at", "reviewed_until"])
@pytest.mark.parametrize("which", [0, 1])
async def test_all_identity_bounds_survive_renewal(monkeypatch, mode, field, which):
    g = await harness(monkeypatch, ids=() if mode == "empty" else ("task-1",))
    deadline = NOW + timedelta(seconds=2)
    g.identity[which] = changed_identity(g.identity[which], field, deadline)
    result = await (detail(g) if mode == "detail" else page(g))
    assert result.freshness.valid_until == deadline


@pytest.mark.parametrize("mode", ["empty", "page", "detail"])
@pytest.mark.parametrize("field", ["expires_at", "reviewed_until"])
@pytest.mark.parametrize("phase", ["final_io", "render"])
@pytest.mark.parametrize("offset", [0, 1])
async def test_identity_expiry_during_last_io_or_response_discards_all(
    monkeypatch, mode, field, phase, offset
):
    g = await harness(monkeypatch, ids=() if mode == "empty" else ("task-1",))
    deadline = NOW + timedelta(seconds=2)
    g.identity[1] = changed_identity(g.identity[1], field, deadline)

    def advance():
        Clock.value = deadline + timedelta(seconds=offset)

    def render(value):
        value.model_dump_json(by_alias=True)
        advance()
        return value

    if phase == "final_io":
        g.session_hook = lambda n: advance() if n == 2 else None

        def render(value):
            return value

    with pytest.raises(ReadRefusalError, match="^session_unavailable$"):
        await (detail(g, render=render) if mode == "detail" else page(g, render=render))


@pytest.mark.parametrize("field", ["expires_at", "reviewed_until"])
async def test_naive_identity_is_not_a_principal_expiry_envelope(monkeypatch, field):
    g = await harness(monkeypatch)
    g.identity[1] = changed_identity(g.identity[1], field, NOW.replace(tzinfo=None))
    with pytest.raises(ReadRefusalError, match="session_unavailable"):
        await page(g)


@pytest.mark.parametrize("empty", [True, False])
@pytest.mark.parametrize("phase", ["window", "e1"])
async def test_independent_revision_change_requires_new_traversal(monkeypatch, empty, phase):
    g = await harness(monkeypatch, ids=() if empty else ("task-1",))
    if phase == "window":
        g._query.change = lambda w: w.model_copy(
            update={"binding": w.binding.model_copy(update={"catalog_revision": 8})}
        )
    else:
        g._catalog_source.values[1] = g._catalog_source.values[1].model_copy(update={"catalog_revision": 8})
    with pytest.raises(ReadRefusalError, match="refresh_required"):
        await page(g)
    assert g._cursor_custody.issued is None
    # A new wholly current observation, not an in-request restart, permits recovery.
    g._query.change = lambda w: w
    g._catalog_source.values = [g._catalog_source.values[0].model_copy(update={"catalog_revision": 8})] * 2
    assert (await page(g)).freshness.state == "current"


@pytest.mark.parametrize("where", ["window", "e0", "e1"])
@pytest.mark.parametrize(
    "field,value",
    [
        ("catalog_ref", "foreign"),
        ("publisher_ref", "foreign"),
        ("catalog_digest", "b" * 64),
        ("scope", SCOPE.model_copy(update={"tenant": "foreign"})),
    ],
)
async def test_independent_provenance_not_discovery_self_assertion(monkeypatch, where, field, value):
    g = await harness(monkeypatch)
    if where == "window":
        g._query.change = lambda w: w.model_copy(
            update={"binding": w.binding.model_copy(update={field: value})}
        )
    else:
        index = 0 if where == "e0" else 1
        current = g._catalog_source.values[index]
        if field == "catalog_digest":
            # E0 may establish any authenticated digest; same revision equivocation at E1 fails.
            current = current.model_copy(update={field: value})
        else:
            current = current.model_copy(update={"anchor": current.anchor.model_copy(update={field: value})})
        g._catalog_source.values[index] = current
    with pytest.raises(ReadRefusalError, match="read_dependency_unavailable"):
        await page(g)


@pytest.mark.parametrize("index", [0, 1])
@pytest.mark.parametrize(
    "field,value",
    [
        ("catalog_revision", True),
        ("catalog_revision", -1),
        ("catalog_revision", "7"),
        ("catalog_digest", "broken"),
        ("source_observed_at", NOW + timedelta(seconds=1)),
        ("source_observed_at", NOW.replace(tzinfo=None)),
        ("valid_until", NOW),
    ],
)
async def test_untrusted_catalog_values_never_become_empty(monkeypatch, index, field, value):
    g = await harness(monkeypatch, ids=())
    g._catalog_source.values[index] = g._catalog_source.values[index].model_copy(update={field: value})
    with pytest.raises(ReadRefusalError, match="read_dependency_unavailable"):
        await page(g)


async def test_catalog_observation_cannot_go_backwards(monkeypatch):
    g = await harness(monkeypatch, ids=())
    g._catalog_source.values[1] = g._catalog_source.values[1].model_copy(
        update={"source_observed_at": NOW - timedelta(seconds=1)}
    )
    with pytest.raises(ReadRefusalError, match="read_dependency_unavailable"):
        await page(g)


@pytest.mark.parametrize("empty", [True, False])
@pytest.mark.parametrize("index", [0, 1])
@pytest.mark.parametrize("phase", ["e1", "session", "render"])
async def test_both_catalog_grants_retained_across_final_io_and_render(monkeypatch, empty, index, phase):
    g = await harness(monkeypatch, ids=() if empty else ("task-1",))
    deadline = NOW + timedelta(seconds=1)
    g._catalog_source.values[index] = g._catalog_source.values[index].model_copy(
        update={"valid_until": deadline}
    )

    def advance():
        Clock.value = deadline

    if phase == "e1":
        g._catalog_source.hook = lambda n: advance() if n == 2 else None
    if phase == "session":
        g.session_hook = lambda n: advance() if n == 2 else None

    def render(value):
        value.model_dump_json()
        if phase == "render":
            advance()
        return value

    with pytest.raises(ReadRefusalError, match="read_dependency_unavailable"):
        await page(g, render=render)


@pytest.mark.parametrize("row", ["task-1", "task-2", "task-3"])
@pytest.mark.parametrize("grant", ["task", "authority"])
@pytest.mark.parametrize("phase", ["later_row", "session", "render"])
async def test_every_row_and_lookahead_retained(monkeypatch, row, grant, phase):
    g = await harness(monkeypatch, ids=("task-1", "task-2", "task-3"))
    deadline = NOW + timedelta(seconds=1)
    target = g.rows if grant == "task" else g.authorities
    target[row] = target[row].model_copy(update={"valid_until": deadline})

    def advance():
        Clock.value = deadline

    if phase == "later_row":
        g.row_hook = lambda name: advance() if name == "task-3" else None
    if phase == "session":
        g.session_hook = lambda n: advance() if n == 2 else None

    def render(value):
        if phase == "render":
            advance()
        return value

    with pytest.raises(ReadRefusalError, match="read_dependency_unavailable"):
        await page(g, limit=2, render=render)


@pytest.mark.parametrize(
    "assignee,ownership", [(None, "unassigned"), ("someone-else", "other"), ("human-internal-1", "self")]
)
async def test_team_includes_readable_assigned_other_without_actions(monkeypatch, assignee, ownership):
    g = await harness(monkeypatch)
    g.rows["task-1"] = g.rows["task-1"].model_copy(
        update={"snapshot": snapshot(fixture_at=NOW, assignee_ref=assignee)}
    )
    result = await page(g)
    assert result.items[0].ownership == ownership
    assert set(result.items[0].model_dump()) == {
        "task_id",
        "process_definition_key",
        "task_definition_key",
        "task_revision",
        "ownership",
        "engine_due_at",
        "snapshot_at",
    }
    if ownership != "self":
        with pytest.raises(ReadRefusalError, match="refresh_required"):
            await page(g, queue="mine")
    else:
        assert len((await page(g, queue="mine")).items) == 1


@pytest.mark.parametrize(
    "memberships",
    [
        (MembershipBinding(membership_ref="admin", roles=("admin",), groups=("medico-auditor",)),),
        (
            MembershipBinding(membership_ref="role", roles=("staff",), groups=("other",)),
            MembershipBinding(membership_ref="group", roles=("other",), groups=("medico-auditor",)),
        ),
    ],
)
async def test_ownership_admin_and_split_membership_do_not_authorize(monkeypatch, memberships):
    g = await harness(monkeypatch, member=membership(memberships=memberships))
    g.rows["task-1"] = g.rows["task-1"].model_copy(
        update={"snapshot": snapshot(fixture_at=NOW, assignee_ref="human-internal-1")}
    )
    with pytest.raises(ReadRefusalError, match="refresh_required"):
        await page(g, queue="mine")
    with pytest.raises(ReadRefusalError, match="resource_unavailable"):
        await detail(g)


@pytest.mark.parametrize("audience", ["beneficiary", "provider"])
async def test_nonstaff_has_no_discovery(monkeypatch, audience):
    g = await harness(
        monkeypatch,
        member=membership(
            audience=audience, subject_bindings=(SubjectBinding(kind=audience, resource_ref="resource"),)
        ),
    )
    with pytest.raises(ReadRefusalError, match="employee_access_required"):
        await page(g)
    assert g._query.calls == 0


@pytest.mark.parametrize("part", ["query", "catalog_source", "catalog_anchor", "cursor_custody"])
async def test_missing_read_dependency_is_not_verified_empty(monkeypatch, part):
    g = await harness(monkeypatch, ids=())
    setattr(g, "_" + part, None)
    with pytest.raises(ReadRefusalError, match="read_dependency_unavailable"):
        await page(g)


@pytest.mark.parametrize("part", ["tenant", "environment", "workload_ref"])
async def test_read_port_scope_mismatch(monkeypatch, part):
    g = await harness(monkeypatch)
    g._query.scope = SCOPE.model_copy(update={part: "foreign"})
    with pytest.raises(ReadRefusalError, match="read_dependency_unavailable"):
        await page(g)


@pytest.mark.parametrize(
    "field,value",
    [
        ("task_ids", ("task-1", "task-1")),
        ("task_ids", ("task-2", "task-1")),
        ("task_ids", ("task-1", "task-2", "task-3")),
        ("next_cursor", "unexpected"),
        ("valid_until", NOW),
        ("source_observed_at", NOW + timedelta(seconds=1)),
    ],
)
async def test_invalid_partial_window_never_success(monkeypatch, field, value):
    g = await harness(monkeypatch)
    g._query.change = lambda w: w.model_copy(update={field: value})
    with pytest.raises(ReadRefusalError, match="read_dependency_unavailable"):
        await page(g, limit=1)


async def test_lookahead_requires_cursor_and_is_authorized(monkeypatch):
    g = await harness(monkeypatch, ids=("task-1", "task-2"))
    result = await page(g, limit=1)
    assert g.row_calls == ["task-1", "task-2"]
    assert result.next_cursor == "sealed"
    assert g._cursor_custody.issued.after_task_id == "task-1"
    assert g._cursor_custody.issued.valid_until == result.freshness.valid_until
    g._query.change = lambda w: w.model_copy(update={"next_cursor": None})
    with pytest.raises(ReadRefusalError, match="read_dependency_unavailable"):
        await page(g, limit=1)


@pytest.mark.parametrize(
    "field,value",
    [
        ("after_task_id", "lookahead"),
        ("valid_until", NOW + timedelta(hours=1)),
    ],
)
async def test_finalized_cursor_cannot_outlive_or_move_boundary(monkeypatch, field, value):
    g = await harness(monkeypatch, ids=("task-1", "task-2"))
    g._cursor_custody.change = lambda c: c.model_copy(update={field: value})
    with pytest.raises(ReadRefusalError, match="read_dependency_unavailable"):
        await page(g, limit=1)


@pytest.mark.parametrize(
    "field,value",
    [
        ("scope", SCOPE.model_copy(update={"tenant": "foreign"})),
        ("queue", "mine"),
        ("limit", 99),
        ("catalog_revision", 8),
        ("catalog_ref", "other"),
        ("publisher_ref", "other"),
        ("catalog_digest", "b" * 64),
    ],
)
async def test_cursor_cannot_replay_other_bindings(monkeypatch, field, value):
    g = await harness(monkeypatch, ids=("task-2",))
    binding = g._binding(g.identity[0], TaskQueueRequest(queue="team"), g._catalog_source.values[0])
    g._cursor_custody.incoming = CursorGrant(
        cursor="token",
        binding=binding.model_copy(update={field: value}),
        after_task_id="task-1",
        valid_until=NOW + timedelta(seconds=10),
    )
    with pytest.raises(ReadRefusalError, match="refresh_required"):
        await page(g, cursor="token")
    assert g._query.calls == 0


@pytest.mark.parametrize(
    "field,value",
    [
        ("session_ref", "other-session"),
        ("principal_ref", "other-person"),
        ("subject", "other-subject"),
        ("issuer", "https://other.test"),
        ("membership_revision", 8),
    ],
)
async def test_cursor_and_final_resolution_compare_complete_principal(monkeypatch, field, value):
    g = await harness(monkeypatch, ids=("task-2",))
    binding = g._binding(g.identity[0], TaskQueueRequest(queue="team"), g._catalog_source.values[0])
    other = binding.principal.model_copy(update={field: value})
    g._cursor_custody.incoming = CursorGrant(
        cursor="token",
        binding=binding.model_copy(update={"principal": other}),
        after_task_id="task-1",
        valid_until=NOW + timedelta(seconds=10),
    )
    with pytest.raises(ReadRefusalError, match="refresh_required"):
        await page(g, cursor="token")
    g.session_calls = 0
    g.identity[1] = replace(g.identity[1], principal=other)
    with pytest.raises(ReadRefusalError, match="refresh_required"):
        await page(g)


async def test_detail_is_explicit_read_projection_and_classification_bounds_freshness(monkeypatch):
    g = await harness(monkeypatch)
    before = g.rows["task-1"].snapshot.model_dump()
    g._disclosure_source.change = lambda d: d.model_copy(update={"valid_until": NOW + timedelta(seconds=2)})
    result = await detail(g)
    assert result.task.allowed_actions == ()
    assert result.task.task_revision == "2"
    assert result.freshness.valid_until == NOW + timedelta(seconds=2)
    assert g.rows["task-1"].snapshot.model_dump() == before
    assert g._catalog_source.calls == g._query.calls == 0


@pytest.mark.parametrize(
    "field,value",
    [
        ("scope", SCOPE.model_copy(update={"tenant": "foreign"})),
        ("authority_revision", 999),
        ("valid_until", NOW),
        ("classification_digest", "invalid"),
    ],
)
async def test_detail_requires_closed_current_classified_provenance(monkeypatch, field, value):
    g = await harness(monkeypatch)
    g._disclosure_source.change = lambda d: d.model_copy(update={field: value})
    with pytest.raises(ReadRefusalError, match="read_dependency_unavailable"):
        await detail(g)


async def test_missing_classification_refuses_detail(monkeypatch):
    g = await harness(monkeypatch)
    g._disclosure_source = None
    with pytest.raises(ReadRefusalError, match="read_dependency_unavailable"):
        await detail(g)


@pytest.mark.parametrize(
    "field,value",
    [
        ("tenant", "foreign"),
        ("active", False),
        ("required_consent_scopes", ("clinical",)),
        ("required_subject_bindings", (SubjectBinding(kind="beneficiary", resource_ref="missing"),)),
    ],
)
async def test_task_resource_requirements_are_not_replaced_by_discovery(monkeypatch, field, value):
    g = await harness(monkeypatch)
    g.rows["task-1"] = g.rows["task-1"].model_copy(update={field: value})
    code = "refresh_required" if field == "required_subject_bindings" else "read_dependency_unavailable"
    with pytest.raises(ReadRefusalError, match=code):
        await page(g)


@pytest.mark.parametrize(
    "field,value",
    [
        ("task_revision", 999),
        ("evidence_revision", 999),
        ("evidence_digest", "f" * 64),
        ("authority_revision", 999),
        ("membership_revision", 999),
        ("process_definition_key", "foreign"),
        ("process_definition_version", 999),
        ("process_definition_id", "foreign"),
        ("process_definition_digest", "f" * 64),
        ("task_definition_key", "foreign"),
        ("form_key", "escalation"),
        ("form_version", 999),
        ("form_digest", "f" * 64),
        ("tenant", "foreign"),
        ("issuer", "https://foreign.test"),
        ("subject", "foreign"),
        ("principal_ref", "foreign"),
        ("read_permitted", False),
    ],
)
async def test_task_authority_pins_and_identity_are_revalidated(monkeypatch, field, value):
    g = await harness(monkeypatch)
    g.authorities["task-1"] = g.authorities["task-1"].model_copy(update={field: value})
    with pytest.raises(ReadRefusalError, match="read_dependency_unavailable"):
        await page(g)
    with pytest.raises(ReadRefusalError, match="read_dependency_unavailable"):
        await detail(g)


@pytest.mark.parametrize(
    "field,value",
    [
        ("eligible_candidate_groups", ("${grupo_aprovador}",)),
        ("task_definition_key", "UT_Unsupported"),
        ("form_key", "escalation"),
        ("snapshot_at", NOW + timedelta(seconds=1)),
        ("task_id", "other"),
    ],
)
async def test_unresolved_groups_and_unsupported_form_refuse(monkeypatch, field, value):
    g = await harness(monkeypatch)
    row = g.rows["task-1"]
    g.rows["task-1"] = row.model_copy(update={"snapshot": row.snapshot.model_copy(update={field: value})})
    with pytest.raises(ReadRefusalError, match="read_dependency_unavailable"):
        await page(g)


@pytest.mark.parametrize("which", ["query", "catalog"])
async def test_unknown_or_failed_source_cannot_supply_empty(monkeypatch, which):
    g = await harness(monkeypatch, ids=())

    async def unknown(*args, **kwargs):
        return None

    if which == "query":
        monkeypatch.setattr(g._query, "discover", unknown)
    else:
        monkeypatch.setattr(g._catalog_source, "current_catalog", unknown)
    with pytest.raises(ReadRefusalError, match="read_dependency_unavailable"):
        await page(g)


@pytest.mark.parametrize("mode", ["page", "empty", "detail"])
async def test_final_session_revocation_or_uncertainty_discards_result(monkeypatch, mode):
    g = await harness(monkeypatch, ids=() if mode == "empty" else ("task-1",))

    def revoked(n):
        if n == 2:
            raise RuntimeError("private revoked record")

    g.session_hook = revoked
    with pytest.raises(ReadRefusalError, match="session_unavailable"):
        await (detail(g) if mode == "detail" else page(g))


@pytest.mark.parametrize("grant", ["window", "disclosure", "incoming_cursor"])
async def test_nonrow_grants_expire_after_response_construction(monkeypatch, grant):
    g = await harness(monkeypatch, ids=("task-2",) if grant == "incoming_cursor" else ("task-1",))
    deadline = NOW + timedelta(seconds=1)
    cursor = None
    if grant == "window":
        g._query.change = lambda w: w.model_copy(update={"valid_until": deadline})
    elif grant == "disclosure":
        g._disclosure_source.change = lambda d: d.model_copy(update={"valid_until": deadline})
    else:
        cursor = "token"
        binding = g._binding(g.identity[0], TaskQueueRequest(queue="team"), g._catalog_source.values[0])
        g._cursor_custody.incoming = CursorGrant(
            cursor=cursor, binding=binding, after_task_id="task-1", valid_until=deadline
        )

    def render(value):
        value.model_dump_json()
        Clock.value = deadline
        return value

    with pytest.raises(
        ReadRefusalError, match="refresh_required" if cursor else "read_dependency_unavailable"
    ):
        await (detail(g, render=render) if grant == "disclosure" else page(g, cursor=cursor, render=render))


@pytest.mark.parametrize("field", ["expires_at", "reviewed_until"])
@pytest.mark.parametrize("mode", ["empty", "page", "detail"])
async def test_real_resolver_last_membership_io_does_not_hide_identity_deadline(monkeypatch, field, mode):
    # Exercise the real resolver's session-before-membership ordering, not only a fabricated principal.
    from maezo.portal.api import session as session_module
    from maezo.portal.api.auth import digest
    from maezo.portal.api.session import HumanSessionResolver

    g = await harness(monkeypatch, ids=() if mode == "empty" else ("task-1",))
    store = g._resolver.store
    g._resolver = HumanSessionResolver(g._resolver.settings, store)
    monkeypatch.setattr(session_module, "datetime", Clock)
    deadline = NOW + timedelta(seconds=1)
    if field == "expires_at":
        record = await store.get_session(digest(SECRET), NOW)
        await store.put_session(record.model_copy(update={field: deadline}), None)
    else:
        key = next(iter(store.memberships))
        store.memberships[key] = store.memberships[key].model_copy(update={field: deadline})
    original = store.get_membership
    calls = 0

    async def final_lookup(issuer, subject):
        nonlocal calls
        calls += 1
        result = await original(issuer, subject)
        if calls == 2:
            Clock.value = deadline
        return result

    monkeypatch.setattr(store, "get_membership", final_lookup)
    with pytest.raises(ReadRefusalError, match="session_unavailable"):
        await (detail(g) if mode == "detail" else page(g))


@pytest.mark.parametrize("provider", ["task", "authority"])
@pytest.mark.parametrize(
    "code", ["resource_unavailable", "refresh_required", "read_dependency_unavailable", "invalid_request"]
)
async def test_explicit_native_read_outcome_survives_only_additive_taxonomy(monkeypatch, provider, code):
    from maezo.gateway.human.errors import GatewayRefusalError

    g = await harness(monkeypatch)

    async def refused(*args, **kwargs):
        raise ReadRefusalError(code)

    target = g._ports.task if provider == "task" else g._ports.authority
    method = "read_task" if provider == "task" else "current_authority"
    monkeypatch.setattr(target, method, refused)
    detail_code = "read_dependency_unavailable" if code == "invalid_request" else code
    queue_code = "refresh_required" if detail_code == "resource_unavailable" else detail_code
    with pytest.raises(ReadRefusalError, match="^" + queue_code + "$"):
        await page(g)
    with pytest.raises(ReadRefusalError, match="^" + detail_code + "$"):
        await detail(g)
    # Existing read and command path keep their original conservative source taxonomy.
    legacy = "task_unavailable" if provider == "task" else "authority_unavailable"
    with pytest.raises(GatewayRefusalError, match="^" + legacy + "$"):
        await g.read_task(session_secret=SECRET, task_id="task-1")
