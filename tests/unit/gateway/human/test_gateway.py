"""D4 port/unit security proofs; no real engine, outbox or cloud execution claimed."""

from datetime import UTC, datetime, timedelta

import pytest
from tests.unit.portal.test_human_session import config, membership

from maezo.gateway.human import (
    AssignmentCommand,
    AuthoritativeTask,
    AuthorityProjection,
    BoundHumanPorts,
    CurrentTaskAuthority,
    DedicatedHumanCredential,
    DurableAdmission,
    GatewayRefusalError,
    HumanCommandCredentialPartition,
    HumanGateway,
    HumanTaskTransport,
    PendingAdmission,
    Scope,
    create_production_gateway,
)
from maezo.portal.api.auth import digest
from maezo.portal.api.records import SessionRecord
from maezo.portal.api.session import HumanSessionResolver
from maezo.portal.api.store import LocalTestIdentityStore
from maezo.portal.contracts.models import SubjectBinding, TaskDecision, TaskSnapshot

pytestmark = pytest.mark.asyncio
SECRET = "s" * 43
CSRF = "c" * 43
SCOPE = Scope(tenant="test-tenant", environment="test", workload_ref="human-gateway")


def snapshot(*, fixture_at: datetime | None = None, **changes):
    fixture_at = fixture_at or datetime.now(UTC)
    data = dict(
        schema_version=1,
        snapshot_at=fixture_at,
        task_id="task-1",
        process_definition_key="SP-OP-AUTH-001",
        process_definition_version=1,
        process_definition_id="auth:1:id",
        process_definition_digest="a" * 64,
        task_definition_key="UT_AnaliseMedicoAuditor",
        form_key="auth_decisao",
        form_version=1,
        form_digest="b" * 64,
        form_source_status="BPMN_FORMDATA",
        task_revision=2,
        assignee_ref=None,
        eligible_candidate_groups=("medico-auditor",),
        evidence_revision=3,
        evidence_digest="e" * 64,
        engine_due_at=None,
        allowed_actions=("claim", "release", "decision"),
        allowed_inputs=("decisao_auditor", "justificativa_clinica", "cid10_referencia", "fundamentacao_dut"),
        read_only_evidence=None,
    )
    data.update(changes)
    return TaskSnapshot(**data)


def command(snap=None, **changes):
    s = snap or snapshot()
    data = dict(
        schema_version=1,
        command_id="cmd-1",
        operation="claim",
        task_id=s.task_id,
        process_definition_key=s.process_definition_key,
        process_definition_version=s.process_definition_version,
        process_definition_id=s.process_definition_id,
        process_definition_digest=s.process_definition_digest,
        task_definition_key=s.task_definition_key,
        form_key=s.form_key,
        form_version=s.form_version,
        form_digest=s.form_digest,
        expected_task_revision=s.task_revision,
        expected_evidence_revision=s.evidence_revision,
        expected_evidence_digest=s.evidence_digest,
        expected_membership_revision=1,
        expected_authority_revision=7,
    )
    data.update(changes)
    return AssignmentCommand(**data)


class Transport(HumanTaskTransport):
    def __init__(self, task):
        self.scope = SCOPE
        self.task = task
        self.fail = False
        self.calls = 0

    async def read_task(self, task_id):
        self.calls += 1
        if self.fail:
            raise RuntimeError("PRIVATE upstream narrative")
        return self.task


class Authorization(AuthorityProjection):
    def __init__(self, authority):
        self.scope = SCOPE
        self.authority = authority
        self.fail = False

    async def current_authority(self, principal, task):
        if self.fail:
            raise RuntimeError("PRIVATE auth text")
        return self.authority


class Admission(DurableAdmission):
    def __init__(self, committed_at):
        self.scope = SCOPE
        self.calls = []
        self.receipts = []
        self.committed_at = committed_at
        self.fail = False
        self.wrong = False

    async def admit(self, command):
        self.calls.append(command)
        if self.fail:
            raise RuntimeError("PRIVATE audit details")
        receipt = PendingAdmission(
            schema_version=1,
            tenant="wrong" if self.wrong else SCOPE.tenant,
            task_id=command.snapshot.task_id,
            command_id=command.command.command_id,
            principal_ref=command.principal.principal_ref,
            workload_ref=SCOPE.workload_ref,
            audit_intent_ref="intent-1",
            outbox_ref="outbox-1",
            transaction_ref="tx-1",
            committed_at=self.committed_at,
        )
        self.receipts.append(receipt)
        return receipt


async def setup(snap=None, member=None, *, fixture_at=None, **task_changes):
    fixture_at = fixture_at or (snap.snapshot_at if snap else datetime.now(UTC))
    store = LocalTestIdentityStore("test-tenant")
    m = member or membership()
    store.memberships[(m.issuer, m.subject)] = m
    await store.put_session(
        SessionRecord(
            secret_hash=digest(SECRET),
            session_ref="session-1",
            csrf_token=CSRF,
            issuer=m.issuer,
            subject=m.subject,
            principal_ref=m.principal_ref,
            membership_revision=m.revision,
            authenticated_at=fixture_at,
            expires_at=fixture_at + timedelta(hours=1),
        ),
        None,
    )
    resolver = HumanSessionResolver(config(), store)
    data = dict(
        tenant=SCOPE.tenant,
        snapshot=snap or snapshot(fixture_at=fixture_at),
        active=True,
        authority_revision=7,
        valid_until=fixture_at + timedelta(minutes=5),
        required_roles=("staff",),
        required_subject_bindings=(),
        required_consent_scopes=(),
    )
    data.update(task_changes)
    task = AuthoritativeTask(**data)
    authorization = CurrentTaskAuthority(
        **task.snapshot.model_dump(
            include=set(
                (
                    "process_definition_key",
                    "process_definition_version",
                    "process_definition_id",
                    "process_definition_digest",
                    "task_definition_key",
                    "form_key",
                    "form_version",
                    "form_digest",
                )
            )
        ),
        tenant=SCOPE.tenant,
        task_id=task.snapshot.task_id,
        issuer=m.issuer,
        subject=m.subject,
        principal_ref=m.principal_ref,
        membership_revision=m.revision,
        authority_revision=7,
        task_revision=task.snapshot.task_revision,
        evidence_revision=task.snapshot.evidence_revision,
        evidence_digest=task.snapshot.evidence_digest,
        read_permitted=True,
        permitted_operations=("claim", "release", "decision"),
        consent_scopes=(),
        valid_until=fixture_at + timedelta(minutes=5),
    )
    transport = Transport(task)
    authority = Authorization(authorization)
    admission = Admission(fixture_at)
    partition = HumanCommandCredentialPartition(SCOPE)
    partition.install(DedicatedHumanCredential(scope=SCOPE, key_id="human-key", purpose="human-command"))
    gateway = HumanGateway(
        resolver=resolver,
        scope=SCOPE,
        credentials=partition,
        ports=BoundHumanPorts(transport, authority, admission),
    )
    return gateway, store, transport, authority, admission


async def submit(gateway, cmd=None, **kw):
    return await gateway.submit_assignment(
        session_secret=SECRET, csrf_token=CSRF, origin=config().public_origin, command=cmd or command(), **kw
    )


async def test_claim_reads_live_authority_and_returns_only_adapter_acknowledgement():
    g, _, t, _, a = await setup()
    result = await submit(g)
    assert result.outbox_ref == "outbox-1" and result.status == "pending"
    assert t.calls == 1 and len(a.calls) == 1
    assert a.calls[0].principal.subject == membership().subject
    assert a.calls[0].workload_ref != a.calls[0].principal.principal_ref
    assert not hasattr(result, "engine_receipt_ref")


async def test_snapshot_scoped_and_dated():
    g, _, transport, _, _ = await setup()
    expected = transport.task.snapshot.model_copy(update={"allowed_actions": ("claim",)})
    assert await g.read_task(session_secret=SECRET, task_id="task-1") == expected


async def test_fixture_clock_is_sampled_after_a_six_minute_collection_delay():
    real_datetime = datetime
    collected_at = real_datetime.now(UTC) - timedelta(minutes=6)

    class AdvancingClock(real_datetime):
        current = collected_at

        @classmethod
        def now(cls, tz=None):
            return cls.current if tz is not None else cls.current.replace(tzinfo=None)

    with pytest.MonkeyPatch.context() as clock:
        clock.setitem(globals(), "datetime", AdvancingClock)
        AdvancingClock.current += timedelta(minutes=6)
        fixture_at = sampling_started_at = AdvancingClock.now(UTC)
        g, store, transport, authority, admission = await setup()
        sampling_finished_at = AdvancingClock.now(UTC)

        session = await store.get_session(digest(SECRET), fixture_at)
        assert fixture_at - collected_at > timedelta(minutes=5)
        assert sampling_started_at <= fixture_at <= sampling_finished_at
        assert session is not None
        assert session.authenticated_at == fixture_at
        assert session.expires_at == fixture_at + timedelta(hours=1)
        assert transport.task.snapshot.snapshot_at == fixture_at
        assert transport.task.valid_until == fixture_at + timedelta(minutes=5)
        assert authority.authority.valid_until == fixture_at + timedelta(minutes=5)

        await submit(g)
        assert admission.receipts[0].committed_at == fixture_at


@pytest.mark.parametrize(
    "field,value",
    [
        ("tenant", "other"),
        ("active", False),
        ("valid_until", -timedelta(seconds=1)),
        ("required_roles", ("admin",)),
        ("required_subject_bindings", (SubjectBinding(kind="beneficiary", resource_ref="other"),)),
        ("required_consent_scopes", ("clinical-review",)),
    ],
)
async def test_task_authority_failures(field, value):
    fixture_at = datetime.now(UTC)
    if field == "valid_until":
        value = fixture_at + value
    g, _, _, _, a = await setup(fixture_at=fixture_at, **{field: value})
    with pytest.raises(GatewayRefusalError):
        await submit(g)
    assert not a.calls


@pytest.mark.parametrize("audience", ["beneficiary", "provider"])
async def test_public_audience_never_completes_internal_tasks(audience):
    m = membership(audience=audience, subject_bindings=(SubjectBinding(kind=audience, resource_ref="own"),))
    g, _, t, _, a = await setup(member=m)
    with pytest.raises(GatewayRefusalError):
        await submit(g)
    with pytest.raises(GatewayRefusalError):
        await g.read_task(session_secret=SECRET, task_id="task-1")
    assert not a.calls and t.calls == 0


@pytest.mark.parametrize(
    "field,value",
    [
        ("process_definition_key", "SP-OP-ESCALATION-001"),
        ("process_definition_version", 2),
        ("process_definition_id", "auth:2:other"),
        ("process_definition_digest", "c" * 64),
        ("task_definition_key", "UT_CoordenacaoAssume"),
        ("form_key", "escalation"),
        ("form_version", 2),
        ("form_digest", "d" * 64),
        ("expected_task_revision", 3),
        ("expected_evidence_revision", 4),
        ("expected_evidence_digest", "f" * 64),
        ("expected_membership_revision", 2),
        ("expected_authority_revision", 8),
        ("task_id", "task-2"),
    ],
)
async def test_stale_and_rebound_command(field, value):
    g, _, _, _, a = await setup()
    with pytest.raises(GatewayRefusalError):
        await submit(g, command(**{field: value}))
    assert not a.calls


@pytest.mark.parametrize(
    "field,value",
    [
        ("tenant", "other"),
        ("task_id", "other"),
        ("issuer", "https://other.test"),
        ("subject", "other"),
        ("principal_ref", "other"),
        ("membership_revision", 9),
        ("authority_revision", 8),
        ("task_revision", 99),
        ("evidence_revision", 99),
        ("evidence_digest", "f" * 64),
        ("permitted_operations", ()),
        ("valid_until", -timedelta(seconds=1)),
    ],
)
async def test_authoritative_projection_is_bound(field, value):
    fixture_at = datetime.now(UTC)
    if field == "valid_until":
        value = fixture_at + value
    g, _, _, p, a = await setup(fixture_at=fixture_at)
    p.authority = p.authority.model_copy(update={field: value})
    with pytest.raises(GatewayRefusalError):
        await submit(g)
    assert not a.calls


@pytest.mark.parametrize(
    "assignee,operation,allowed",
    [
        (None, "claim", True),
        ("other", "claim", False),
        ("human-internal-1", "claim", False),
        (None, "release", False),
        ("other", "release", False),
        ("human-internal-1", "release", True),
    ],
)
async def test_assignment(assignee, operation, allowed):
    s = snapshot(assignee_ref=assignee)
    g, _, _, _, a = await setup(snap=s)
    if allowed:
        await submit(g, command(s, operation=operation))
    else:
        with pytest.raises(GatewayRefusalError):
            await submit(g, command(s, operation=operation))
        assert not a.calls


async def test_membership_revoked_after_first_read():
    g, store, _, _, a = await setup()
    await g.read_task(session_secret=SECRET, task_id="task-1")
    store.memberships[(membership().issuer, membership().subject)] = membership(revoked=True)
    with pytest.raises(GatewayRefusalError):
        await submit(g)
    assert not a.calls


@pytest.mark.parametrize("dependency", ["task", "authority", "audit"])
async def test_dependencies_fail_closed_without_private_error(dependency):
    g, _, t, p, a = await setup()
    {"task": t, "authority": p, "audit": a}[dependency].fail = True
    with pytest.raises(GatewayRefusalError) as error:
        await submit(g)
    assert "PRIVATE" not in str(error.value)
    assert error.value.__suppress_context__
    if dependency != "audit":
        assert not a.calls


async def test_wrong_admission_not_presented_as_durable_success():
    g, _, _, _, a = await setup()
    a.wrong = True
    with pytest.raises(GatewayRefusalError):
        await submit(g)


@pytest.mark.parametrize(
    "origin,csrf,secret",
    [
        ("https://evil.test", CSRF, SECRET),
        (config().public_origin, "bad", SECRET),
        (config().public_origin, CSRF, "fake"),
    ],
)
async def test_csrf_origin_session(origin, csrf, secret):
    g, _, t, _, a = await setup()
    with pytest.raises(GatewayRefusalError):
        await g.submit_assignment(session_secret=secret, csrf_token=csrf, origin=origin, command=command())
    assert not a.calls and t.calls == 0


async def test_narrative_decision_refused_without_copying_or_dropping_required_phi():
    s = snapshot(assignee_ref="human-internal-1")
    g, _, _, _, a = await setup(snap=s)
    values = command(s).model_dump(exclude={"operation", "expected_authority_revision"})
    values["inputs"] = dict(
        kind="auth_decisao",
        decisao_auditor="NEGAR",
        justificativa_clinica="PRIVATE justification",
        cid10_referencia="PRIVATE cid",
        fundamentacao_dut="PRIVATE basis",
    )
    decision = TaskDecision(**values)
    before = decision.model_dump()
    with pytest.raises(GatewayRefusalError, match="form_projection_unavailable"):
        await g.submit_decision(
            session_secret=SECRET,
            csrf_token=CSRF,
            origin=config().public_origin,
            decision=decision,
            expected_authority_revision=7,
        )
    assert decision.model_dump() == before and not a.calls


async def test_production_factory_cannot_activate_with_fake_ports():
    g, *_ = await setup()
    with pytest.raises(GatewayRefusalError, match="production_capabilities_unavailable"):
        create_production_gateway(resolver=g._resolver, scope=SCOPE)


async def test_dedicated_credential_scope_and_agent_separation():
    partition = HumanCommandCredentialPartition(SCOPE)
    credential = DedicatedHumanCredential(scope=SCOPE, key_id="kms-human-1", purpose="human-command")
    partition.install(credential)
    assert partition.for_workload(SCOPE) == credential
    with pytest.raises(GatewayRefusalError):
        partition.for_workload(SCOPE.model_copy(update={"tenant": "other"}))
    assert not hasattr(partition, "get_agent_view")
    assert not hasattr(credential, "secret")


@pytest.mark.parametrize(
    "field,value", [("tenant", "other"), ("environment", "production"), ("workload_ref", "agent")]
)
async def test_every_port_scope_rechecked_before_any_call(field, value):
    for port_index in (2, 3, 4):
        parts = await setup()
        g = parts[0]
        port = parts[port_index]
        port.scope = SCOPE.model_copy(update={field: value})
        with pytest.raises(GatewayRefusalError):
            await submit(g)
        assert not parts[4].calls


@pytest.mark.parametrize(
    "field,value",
    [
        ("process_definition_key", "other"),
        ("process_definition_version", 2),
        ("process_definition_id", "other"),
        ("process_definition_digest", "f" * 64),
        ("task_definition_key", "other"),
        ("form_key", "escalation"),
        ("form_version", 2),
        ("form_digest", "f" * 64),
    ],
)
async def test_projection_pins_cannot_rebind_live_task(field, value):
    g, _, _, p, a = await setup()
    p.authority = p.authority.model_copy(update={field: value})
    with pytest.raises(GatewayRefusalError):
        await submit(g)
    assert not a.calls


async def test_membership_revocation_during_remote_authority_fetch():
    g, store, _, p, a = await setup()
    original = p.current_authority

    async def revoked(principal, task):
        store.memberships[(membership().issuer, membership().subject)] = membership(revoked=True)
        return await original(principal, task)

    p.current_authority = revoked
    with pytest.raises(GatewayRefusalError):
        await submit(g)
    assert not a.calls


async def test_role_and_group_cannot_be_combined_from_different_memberships():
    from maezo.portal.contracts.models import MembershipBinding

    m = membership(
        memberships=(
            MembershipBinding(membership_ref="one", roles=("staff",), groups=("other",)),
            MembershipBinding(membership_ref="two", roles=("observer",), groups=("medico-auditor",)),
        )
    )
    g, _, _, _, a = await setup(member=m)
    with pytest.raises(GatewayRefusalError):
        await submit(g)
    assert not a.calls


@pytest.mark.parametrize("groups", [(), ("other",), ("${grupo_humano}",), ("#{expr}",)])
async def test_missing_ineligible_unresolved_groups(groups):
    g, _, t, _, a = await setup()
    t.task = t.task.model_copy(
        update={"snapshot": t.task.snapshot.model_copy(update={"eligible_candidate_groups": groups})}
    )
    with pytest.raises(GatewayRefusalError):
        await submit(g)
    assert not a.calls


async def test_dynamic_resolved_group_is_not_artificially_static_allowlisted():
    from maezo.portal.contracts.models import MembershipBinding

    dynamic = "contract-owned-team-42"
    m = membership(
        memberships=(MembershipBinding(membership_ref="one", roles=("staff",), groups=(dynamic,)),)
    )
    g, _, _, _, a = await setup(member=m, snap=snapshot(eligible_candidate_groups=(dynamic,)))
    await submit(g)
    assert len(a.calls) == 1


async def test_subject_and_consent_match_grants_only_their_exact_scopes():
    binding = SubjectBinding(kind="beneficiary", resource_ref="person-1")
    g, _, _, p, a = await setup(
        member=membership(subject_bindings=(binding,)),
        required_subject_bindings=(binding,),
        required_consent_scopes=("case-review:1",),
    )
    p.authority = p.authority.model_copy(update={"consent_scopes": ("case-review:2",)})
    with pytest.raises(GatewayRefusalError):
        await submit(g)
    assert not a.calls
    p.authority = p.authority.model_copy(update={"consent_scopes": ("case-review:1",)})
    await submit(g)
    assert len(a.calls) == 1


@pytest.mark.parametrize("forged", ["principal", "actor", "tenant", "tier", "variables", "human_approved"])
async def test_browser_authority_fields_never_accepted(forged):
    from pydantic import ValidationError

    values = command().model_dump()
    values[forged] = "forged"
    with pytest.raises(ValidationError):
        AssignmentCommand.model_validate(values)


async def test_null_csrf_and_origin_are_not_read_mode():
    g, _, t, _, a = await setup()
    with pytest.raises(GatewayRefusalError):
        await g.submit_assignment(session_secret=SECRET, csrf_token=None, origin=None, command=command())
    assert not a.calls and not t.calls


@pytest.mark.parametrize("task_id", ["../task", "task?secret=private", "task#private", "", None])
async def test_read_reference_cannot_be_a_rest_route(task_id):
    g, _, t, _, _ = await setup()
    with pytest.raises(GatewayRefusalError):
        await g.read_task(session_secret=SECRET, task_id=task_id)
    assert t.calls == 0


@pytest.mark.parametrize("purpose", ["agent", "admin", "oidc", "PHI_HMAC_KEY", "a2a", ""])
async def test_credential_purpose_cannot_reuse_other_partitions(purpose):
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        DedicatedHumanCredential(scope=SCOPE, key_id="key-1", purpose=purpose)


async def test_credential_missing_or_changed_scope_fails_closed():
    g, _, t, _, a = await setup()
    g._credentials = HumanCommandCredentialPartition(SCOPE)
    with pytest.raises(GatewayRefusalError):
        await submit(g)
    assert not t.calls and not a.calls
    with pytest.raises(GatewayRefusalError):
        g._credentials.install(
            DedicatedHumanCredential(
                scope=SCOPE.model_copy(update={"tenant": "other"}), key_id="key", purpose="human-command"
            )
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("tenant", "other"),
        ("task_id", "other"),
        ("command_id", "other"),
        ("principal_ref", "other"),
        ("workload_ref", "other"),
        ("committed_at", timedelta(days=1)),
    ],
)
async def test_acknowledgement_identity_is_exact(field, value):
    fixture_at = datetime.now(UTC)
    if field == "committed_at":
        value = fixture_at + value
    g, _, _, _, a = await setup(fixture_at=fixture_at)
    original = a.admit

    async def altered(command):
        return (await original(command)).model_copy(update={field: value})

    a.admit = altered
    with pytest.raises(GatewayRefusalError):
        await submit(g)


def _assert_pool_construction_only(source: str) -> None:
    """WP-J1-00: the composition root may build the application-owned pool, nothing else.

    It constructs `httpx.AsyncHTTPTransport` (the pool) and annotates it as
    `httpx.AsyncBaseTransport`. Any client construction or request call here would
    escape the fenced transport modules, so both are refused.
    """
    import ast

    tree = ast.parse(source)
    imports = [
        node
        for node in ast.walk(tree)
        if (isinstance(node, ast.Import) and any(alias.name.split(".")[0] == "httpx" for alias in node.names))
        or (isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == "httpx")
    ]
    assert len(imports) == 1
    imported = imports[0]
    assert imported in tree.body
    assert isinstance(imported, ast.Import)
    assert len(imported.names) == 1
    assert imported.names[0].name == "httpx" and imported.names[0].asname is None
    attributes = sorted(
        parent.attr
        for parent in ast.walk(tree)
        if isinstance(parent, ast.Attribute)
        and isinstance(parent.value, ast.Name)
        and parent.value.id == "httpx"
    )
    # Every mention of the module must BE one of those attribute accesses. Counting
    # attributes alone let `alternate_http = httpx` through, which rebinds the whole
    # module and reopens everything this fence closes (found by the mutant suite
    # below; the sibling `_assert_engine_read_pool_type_only` already counted uses).
    uses = [node for node in ast.walk(tree) if isinstance(node, ast.Name) and node.id == "httpx"]
    parents = {id(child): parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    assert len(uses) == len(attributes), [use.lineno for use in uses]
    assert all(isinstance(parents[id(use)], ast.Attribute) for use in uses)
    assert attributes == ["AsyncBaseTransport", "AsyncHTTPTransport"], attributes


@pytest.mark.parametrize(
    "mutation",
    [
        "additional_alias",
        "replace_alias",
        "from_import",
        "submodule_alias",
        "module_assignment",
        "direct_request",
        "client_construction",
        "timeout_construction",
        "second_pool",
        "duplicate_import",
        "nested_import",
        "mixed_import",
        "aliased_import",
        "import_below_module_body",
        "annotation_only",
    ],
)
async def test_pool_construction_only_rejects_aliases_clients_and_extra_uses(mutation):
    """WP-J1-00 repair (V10 MINOR-6) — the guard that guards `production.py`.

    `httpx.AsyncHTTPTransport` is outside the CI chokepoint fence's scope
    (`scripts/ci/check_effect_chokepoint_fence.py` `_HTTPX_CLIENT_ATTRS` is
    `{"AsyncClient", "Client"}`), so `_assert_pool_construction_only` is the ONLY
    thing standing between the composition root and an unfenced HTTP surface. An
    unmutated assertion is not evidence; this is the mirror of
    `test_read_pool_type_only_rejects_aliases_requests_and_relocations`.
    """
    source = (
        "import httpx\n"
        "def compose():\n"
        "    pool: httpx.AsyncBaseTransport = httpx.AsyncHTTPTransport(verify=None, trust_env=False)\n"
        "    return pool\n"
    )
    _assert_pool_construction_only(source)
    suffixes = {
        "additional_alias": "import httpx as alternate_http\ndef request(): return alternate_http.get('https://invalid')\n",
        "from_import": "from httpx import AsyncClient\n",
        "submodule_alias": "import httpx._client as alternate_http\n",
        "module_assignment": "alternate_http = httpx\n",
        "direct_request": "def request(): return httpx.get('https://invalid')\n",
        "client_construction": "def client(): return httpx.AsyncClient(verify=None)\n",
        "timeout_construction": "def budget(): return httpx.Timeout(5)\n",
        "second_pool": "def extra(): return httpx.AsyncHTTPTransport(verify=None)\n",
        "duplicate_import": "import httpx\n",
        "nested_import": "def request():\n    import httpx as alternate_http\n",
        "annotation_only": "def typed(pool: httpx.AsyncBaseTransport): pass\n",
    }
    replacements = {
        "replace_alias": ("import httpx", "import httpx as alternate_http"),
        "mixed_import": ("import httpx", "import httpx, os"),
        "aliased_import": ("import httpx\n", "import httpx\nimport httpx as h\n"),
        "import_below_module_body": (
            "import httpx\ndef compose():",
            "def compose():\n    import httpx\ndef _compose():",
        ),
    }
    if mutation in suffixes:
        source += suffixes[mutation]
    else:
        source = source.replace(*replacements[mutation])
    with pytest.raises(AssertionError):
        _assert_pool_construction_only(source)


def _assert_engine_read_pool_type_only(source: str) -> None:
    import ast

    tree = ast.parse(source)
    imports = [
        node
        for node in ast.walk(tree)
        if (isinstance(node, ast.Import) and any(alias.name.split(".")[0] == "httpx" for alias in node.names))
        or (isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == "httpx")
    ]
    assert len(imports) == 1
    imported = imports[0]
    assert imported in tree.body
    assert isinstance(imported, ast.Import)
    assert len(imported.names) == 1
    assert imported.names[0].name == "httpx" and imported.names[0].asname is None
    uses = [node for node in ast.walk(tree) if isinstance(node, ast.Name) and node.id == "httpx"]
    assert len(uses) == 1
    parents = {id(child): parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    attribute = parents[id(uses[0])]
    assert isinstance(attribute, ast.Attribute) and attribute.attr == "AsyncBaseTransport"
    argument = parents[id(attribute)]
    assert isinstance(argument, ast.arg) and argument.annotation is attribute
    assert argument.arg == "transport_pool"
    arguments = parents[id(argument)]
    assert isinstance(arguments, ast.arguments) and argument in arguments.kwonlyargs
    constructor = parents[id(arguments)]
    assert isinstance(constructor, ast.FunctionDef) and constructor.name == "__init__"
    owner = parents[id(constructor)]
    assert isinstance(owner, ast.ClassDef) and owner.name == "EngineReadComposition"
    assert parents[id(owner)] is tree


@pytest.mark.parametrize(
    "mutation",
    [
        "additional_alias",
        "replace_alias",
        "from_import",
        "submodule_alias",
        "module_assignment",
        "direct_request",
        "annotation_assignment",
        "duplicate_import",
        "nested_import",
        "mixed_import",
        "wrong_owner",
        "wrong_constructor",
        "wrong_argument",
        "positional_argument",
        "second_type_use",
    ],
)
async def test_read_pool_type_only_rejects_aliases_requests_and_relocations(mutation):
    source = (
        "import httpx\n"
        "class EngineReadComposition:\n"
        "    def __init__(self, *, transport_pool: httpx.AsyncBaseTransport): pass\n"
    )
    _assert_engine_read_pool_type_only(source)
    suffixes = {
        "additional_alias": "import httpx as alternate_http\ndef request(): return alternate_http.get('https://invalid')\n",
        "from_import": "from httpx import get as request\n",
        "submodule_alias": "import httpx._client as alternate_http\n",
        "module_assignment": "alternate_http = httpx\n",
        "direct_request": "def request(): return httpx.get('https://invalid')\n",
        "annotation_assignment": "Pool = httpx.AsyncBaseTransport\n",
        "duplicate_import": "import httpx\n",
        "nested_import": "def request():\n    import httpx as alternate_http\n",
        "second_type_use": "def extra(pool: httpx.AsyncBaseTransport): pass\n",
    }
    replacements = {
        "replace_alias": ("import httpx", "import httpx as alternate_http"),
        "mixed_import": ("import httpx", "import httpx, os"),
        "wrong_owner": ("class EngineReadComposition:", "class OtherComposition:"),
        "wrong_constructor": ("def __init__", "def request"),
        "wrong_argument": ("transport_pool:", "other_pool:"),
        "positional_argument": ("self, *, transport_pool", "self, transport_pool"),
    }
    if mutation in suffixes:
        source += suffixes[mutation]
    else:
        source = source.replace(*replacements[mutation])
    with pytest.raises(AssertionError):
        _assert_engine_read_pool_type_only(source)


async def test_no_shadow_policy_flip_generic_rest_or_signing_fallback():
    import ast
    import inspect
    from pathlib import Path

    import maezo.gateway.human.gateway as module

    assert "shadow" not in inspect.signature(HumanGateway).parameters
    assert set(n for n in dir(HumanTaskTransport) if not n.startswith("_")) == {"read_task"}
    assert set(n for n in dir(DurableAdmission) if not n.startswith("_")) == {"admit"}
    source = "\n".join(p.read_text() for p in Path(module.__file__).parent.glob("*.py"))
    # D6 command and Q2 read transports own HTTP. Exact constructor scopes/options
    # remain fenced by test_portal_credential_boundary; authorization/admission do not.
    http_owners = {
        p.name
        for p in Path(module.__file__).parent.glob("*.py")
        if any(
            (isinstance(node, ast.Import) and any(a.name.split(".")[0] == "httpx" for a in node.names))
            or (isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == "httpx")
            for node in ast.walk(ast.parse(p.read_text()))
        )
    }
    # PR-A landing repair: the E03 assignment publication client and the E04 native AUTH client
    # also own httpx; both constructor scopes are pinned byte-exactly in
    # scripts/ci/check_effect_chokepoint_fence.py::_HTTPX_SCOPED_SEAMS (AssignmentPrivateTransport,
    # AuthNativeClient). The train shipped them without updating this owner set.
    # WP-J1-00: the production composition root owns the Q2 connection pool, which
    # `EngineReadComposition` documents as having application lifespan and requires
    # to be application-owned (`_owns_transport` must be False on the per-request
    # client). Constructing it is the ONLY httpx use in production.py: it builds no
    # client and issues no request — both remain fenced in the transport modules.
    assert http_owners == {
        "transport.py",
        "read_transport.py",
        "engine_reads.py",
        "assignment_transport.py",
        "auth_transport.py",
        "production.py",
    }
    _assert_pool_construction_only((Path(module.__file__).parent / "production.py").read_text())
    # Q2 composition only annotates its borrowed application-owned pool here.
    # It gets no constructor/request exemption from the two transport owners.
    _assert_engine_read_pool_type_only((Path(module.__file__).parent / "engine_reads.py").read_text())
    for forbidden in (
        "import requests",
        "import anthropic",
        "ActionExecutionGateway(",
        "get_agent_view(",
        "getenv(",
        "HumanCommandReceipt(",
    ):
        assert forbidden not in source


@pytest.mark.parametrize(
    "task_key,form_key,process,inputs,allowed_inputs,status",
    [
        (
            "UT_AnaliseMedicoAuditor",
            "auth_decisao",
            "SP-OP-AUTH-001",
            {"kind": "auth_decisao", "decisao_auditor": "APROVAR"},
            ("decisao_auditor", "justificativa_clinica", "cid10_referencia", "fundamentacao_dut"),
            "BPMN_FORMDATA",
        ),
        (
            "UT_CoordenacaoAssume",
            "auth_decisao",
            "SP-OP-AUTH-001",
            {"kind": "auth_decisao", "decisao_auditor": "SOLICITAR_INFO"},
            ("decisao_auditor", "justificativa_clinica", "cid10_referencia", "fundamentacao_dut"),
            "BPMN_FORMDATA",
        ),
        (
            "UT_RegistrarParecerJunta",
            "auth_junta",
            "SP-OP-AUTH-001",
            {"kind": "auth_junta", "decisao_auditor": "APROVAR"},
            ("decisao_auditor", "justificativa_clinica", "cid10_referencia", "fundamentacao_dut"),
            "BPMN_FORMDATA",
        ),
        (
            "UT_TratarEscalonamento",
            "escalation",
            "SP-OP-ESCALATION-001",
            {
                "kind": "escalation",
                "resultado": "devolvido_agente",
                "notas_resolucao": "PRIVATE instructions",
            },
            ("resultado", "notas_resolucao"),
            "BPMN_FORMDATA",
        ),
        (
            "UT_SupervisorAssume",
            "escalation",
            "SP-OP-ESCALATION-001",
            {"kind": "escalation", "resultado": "resolvido_humano", "notas_resolucao": "PRIVATE notes"},
            ("resultado", "notas_resolucao"),
            "BPMN_FORMDATA",
        ),
        (
            "UT_AnaliseAdmissibilidade",
            "pagto_admissibilidade",
            "SP-OP-PAGTO-001",
            {"kind": "pagto_admissibilidade", "decisao_admissibilidade": "PROSSEGUIR"},
            ("decisao_admissibilidade", "justificativa_recusa"),
            "BPMN_TASK_DOCUMENTATION_DRAFT_VERIFY",
        ),
    ],
)
async def test_six_current_bindings_stay_closed_pending_contract_projection(
    task_key, form_key, process, inputs, allowed_inputs, status
):
    from maezo.portal.contracts.models import Centavos, PagtoAdmissibilityEvidence

    evidence = None
    if form_key == "pagto_admissibilidade":
        evidence = PagtoAdmissibilityEvidence(
            kind=form_key,
            valor_pagamento_cents=Centavos("100"),
            dados_pagamento_validos=True,
            lastro_confirmado=False,
            duplicidade_suspeita=False,
        )
    s = snapshot(
        task_definition_key=task_key,
        form_key=form_key,
        process_definition_key=process,
        assignee_ref="human-internal-1",
        allowed_inputs=allowed_inputs,
        form_source_status=status,
        read_only_evidence=evidence,
    )
    g, _, _, _, a = await setup(snap=s)
    values = command(s).model_dump(exclude={"operation", "expected_authority_revision"})
    decision = TaskDecision(**values, inputs=inputs)
    before = decision.model_dump()
    with pytest.raises(
        GatewayRefusalError,
        match=("form_contract_unavailable" if evidence else "form_projection_unavailable"),
    ):
        await g.submit_decision(
            session_secret=SECRET,
            csrf_token=CSRF,
            origin=config().public_origin,
            decision=decision,
            expected_authority_revision=7,
        )
    assert before == decision.model_dump() and not a.calls
    projection = await g.read_task(session_secret=SECRET, task_id=s.task_id)
    assert "decision" not in projection.allowed_actions
    if evidence:
        assert projection.allowed_actions == () and not evidence.lastro_confirmado
        with pytest.raises(GatewayRefusalError, match="form_contract_unavailable"):
            await submit(g, command(s, operation="release"))
        assert not a.calls


async def test_unknown_form_cannot_be_activated_by_trusted_shape_alone():
    g, _, t, _, a = await setup()
    t.task = t.task.model_copy(
        update={"snapshot": t.task.snapshot.model_copy(update={"task_definition_key": "UT_Unknown"})}
    )
    with pytest.raises(GatewayRefusalError):
        await submit(g)
    assert not a.calls


async def test_task_operation_allowlist_blocks_even_with_authority_grant():
    g, _, _, _, a = await setup(snap=snapshot(allowed_actions=("decision",)))
    with pytest.raises(GatewayRefusalError):
        await submit(g)
    assert not a.calls


async def test_dto_principal_is_not_accepted_as_session_authentication():
    g, _, t, _, a = await setup()
    resolved = await g._resolver.resolve(SECRET)
    with pytest.raises(GatewayRefusalError):
        await g.submit_assignment(
            session_secret=resolved.principal,
            csrf_token=CSRF,
            origin=config().public_origin,
            command=command(),
        )
    assert not t.calls and not a.calls


async def test_auth_store_outage_never_reads_task_or_admits_command():
    g, store, t, _, a = await setup()

    async def unavailable(*args):
        raise RuntimeError("PRIVATE storage path")

    store.get_membership = unavailable
    with pytest.raises(GatewayRefusalError) as e:
        await submit(g)
    assert str(e.value) == "authentication_unavailable" and not t.calls and not a.calls


async def test_dedicated_key_locator_is_derived_from_partition_never_arbitrary_secret_path():
    from pydantic import ValidationError

    credential = DedicatedHumanCredential(scope=SCOPE, key_id="key-1", purpose="human-command")
    assert credential.key_ref == "human-command/test/test-tenant/human-gateway/key-1"
    with pytest.raises(ValidationError):
        DedicatedHumanCredential(scope=SCOPE, key_id="key-1", purpose="human-command", key_ref="PHI_HMAC_KEY")


async def test_explicit_authority_denies_read_even_when_role_and_group_match():
    g, _, _, p, a = await setup()
    p.authority = p.authority.model_copy(update={"read_permitted": False})
    with pytest.raises(GatewayRefusalError):
        await g.read_task(session_secret=SECRET, task_id="task-1")
    with pytest.raises(GatewayRefusalError):
        await submit(g)
    assert not a.calls
