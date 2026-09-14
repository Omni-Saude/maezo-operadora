"""Low-level SQL/TLS doubles only; native PostgreSQL proof lives in marked integration tests."""

import copy
import ssl
from datetime import timedelta
from pathlib import Path

import pytest
from tests.unit.gateway.human.test_decision_binding_qualification import END, NOW, SCOPE, fixture, sign

from maezo.gateway.human import decision_binding as module
from maezo.gateway.human.decision_binding import (
    RELATIONS,
    BindingConnection,
    BindingDatabase,
    DecisionBindingInstaller,
    PostgresDecisionBindingSource,
    RelationPin,
)
from maezo.gateway.human.decision_binding_qualification import (
    BindingUnavailableError,
    Revocation,
    SignedAuthorities,
    SignedReceipt,
    canonical,
    sha,
)
from maezo.gateway.human.transport import HumanTLSIdentity

pytestmark = pytest.mark.asyncio


def database():
    return BindingDatabase(
        scope=SCOPE,
        installation_id="test-installation",
        engine_name="cib-seven",
        database_incarnation="incarnation-1",
        resource_publisher_fingerprint="e" * 64,
        host="127.0.0.1",
        port=5432,
        database="cib",
        database_oid=42,
        schema_name="engine",
        schema_oid=43,
        owner_role="owner",
        installer_role="installer",
        reader_role="reader",
        engine_role="engine_runtime",
        installer_certificate_digest="1" * 64,
        reader_certificate_digest="2" * 64,
        relations=tuple(RelationPin(name=n, oid=i + 100, owner="owner") for i, n in enumerate(RELATIONS)),
    )


class Transaction:
    def __init__(self, c, readonly):
        self.c, self.readonly = c, readonly

    async def __aenter__(self):
        self.before = copy.deepcopy(self.c.state.rows)
        self.c.readonly = self.readonly
        return self

    async def __aexit__(self, typ, value, tb):
        if typ:
            self.c.state.rows = self.before
        elif self.c.state.lose_commit:
            self.c.state.lose_commit = False
            raise ConnectionError("SYNTHETIC lost acknowledgement after commit")


class State:
    def __init__(self, d):
        self.d = d
        self.rows = {"revision": 0, "authorities": {}, "qualification": {}, "bindings": {}, "receipts": {}}
        self.lose_commit = False
        self.bad_catalog = None
        self.bad_acl = None
        self.connections = []
        self.at = NOW
        self.after_query = None
        self.material = None

    async def connect(self, **kwargs):
        c = Connection(self, kwargs)
        self.connections.append(c)
        return c


class Connection:
    def __init__(self, state, kwargs):
        self.state, self.kwargs = state, kwargs

    def transaction(self, *, isolation, readonly):
        assert isolation == "read_committed"
        return Transaction(self, readonly)

    async def close(self):
        pass

    def step(self, sql):
        if self.state.after_query:
            self.state.after_query(sql)

    async def fetchrow(self, sql, *args):
        self.step(sql)
        d, rows = self.state.d, self.state.rows
        if "FROM pg_stat_ssl" in sql:
            result = dict(
                database=d.database,
                database_oid=d.database_oid,
                role=self.kwargs["user"],
                session_role=self.kwargs["user"],
                ssl=True,
                client_certificate=True,
                schema_oid=d.schema_oid,
                owner=d.owner_role,
            )
            result.update(self.state.bad_catalog or {})
            return result
        if "FROM pg_roles" in sql:
            r = dict.fromkeys(
                (
                    "rolsuper",
                    "rolcreaterole",
                    "rolcreatedb",
                    "rolreplication",
                    "rolbypassrls",
                    "memberships",
                    "schema_create",
                    "database_create",
                ),
                False,
            )
            if self.state.bad_acl == "membership":
                r["memberships"] = True
            return r
        if "FROM pg_class" in sql:
            pin = next(p for p in d.relations if p.name == args[1])
            return dict(
                oid=pin.oid,
                owner=pin.owner,
                relkind="r",
                can_select=True,
                writes=self.state.bad_acl == "reader-write",
            )
        if "AS engine_select" in sql:
            return dict(
                engine_select=True,
                engine_writes=self.state.bad_acl == "engine-column-write",
                installer_select=True,
                installer_insert=True,
                installer_mutates=self.state.bad_acl == "installer-update",
            )
        if "SELECT document_,digest_,generation_" in sql:
            if not rows["authorities"]:
                return None
            g = max(rows["authorities"])
            raw, digest = rows["authorities"][g]
            return dict(document_=raw, digest_=digest, generation_=g)
        if "SELECT * FROM" in sql and "install_receipt" in sql:
            return rows["receipts"].get(args[2])
        if "SELECT packet_,digest_" in sql:
            return rows["qualification"].get((args[2], args[3]))
        if "SELECT q.packet_" in sql:
            bindings = [
                r
                for r in rows["bindings"].values()
                if r["authority_rev_"] == args[2] and r["process_"] == args[4] and r["task_key_"] == args[5]
            ]
            return (
                rows["qualification"].get((args[2], bindings[0]["binding_digest_"]))
                if len(bindings) == 1
                else None
            )
        if "SELECT t.rev_" in sql:
            return self.state.native
        if "SELECT r.payload_" in sql:
            return self.state.resource
        raise AssertionError(sql)

    async def fetchval(self, sql, *args):
        self.step(sql)
        rows = self.state.rows
        if "SELECT rev_" in sql:
            return rows["revision"]
        if "SELECT max(generation_)" in sql:
            return max(rows["authorities"], default=None)
        if "UPDATE " in sql:
            assert not self.readonly
            if rows["revision"] != args[1]:
                return None
            rows["revision"] += 1
            return rows["revision"]
        raise AssertionError(sql)

    async def fetch(self, sql, *args):
        self.step(sql)
        if "SELECT packet_,digest_" in sql:
            assert args[:2] == (SCOPE.tenant, self.state.d.installation_id)
            assert "ORDER BY digest_ LIMIT 7" in sql
            return sorted(
                (r for (revision, _), r in self.state.rows["qualification"].items() if revision == args[2]),
                key=lambda r: r["digest_"],
            )[:7]
        if "SELECT binding_digest_" in sql:
            assert args[:2] == (SCOPE.tenant, SCOPE.environment)
            assert "ORDER BY binding_digest_ LIMIT 7" in sql
            return sorted(
                (r for r in self.state.rows["bindings"].values() if r["authority_rev_"] == args[2]),
                key=lambda r: r["binding_digest_"],
            )[:7]
        if "SELECT p.id_" in sql:
            materials = (
                self.state.material if isinstance(self.state.material, tuple) else (self.state.material,)
            )
            from maezo.gateway.human.decision_binding_qualification import artifact_bytes

            if "act_re_decision_def" in sql:
                m = next(m for m in materials if m.group_dmn and m.group_dmn.artifact_ref == args[0])
                domain = m.entry.group_domain
                return [
                    dict(
                        id_=domain.dmn_definition_id,
                        key_=domain.dmn_definition_key,
                        version_=domain.dmn_definition_version,
                        tenant_id_=SCOPE.tenant,
                        bytes_=artifact_bytes(m.group_dmn),
                    )
                ]
            m = next(m for m in materials if m.entry.process_definition_id == args[0])
            return [
                dict(
                    id_=m.entry.process_definition_id,
                    key_=m.entry.process_definition_key,
                    version_=m.entry.process_definition_version,
                    tenant_id_=SCOPE.tenant,
                    bytes_=artifact_bytes(m.process),
                )
            ]
        if "SELECT group_id_" in sql:
            return [{"group_id_": self.state.required_group}]
        if "SELECT * FROM" in sql and "decision_binding" in sql:
            return [
                r
                for r in self.state.rows["bindings"].values()
                if (r["tenant_"], r["environment_"], r["process_"], r["task_key_"], r["authority_rev_"])
                == args
            ]
        raise AssertionError(sql)

    async def execute(self, sql, *args):
        self.step(sql)
        assert not self.readonly
        rows = self.state.rows
        if "INSERT INTO" in sql and "decision_authority" in sql:
            assert args[2] not in rows["authorities"]
            rows["authorities"][args[2]] = (args[3], args[4])
        elif "INSERT INTO" in sql and "decision_qualification" in sql:
            assert (args[2], args[4]) not in rows["qualification"]
            rows["qualification"][(args[2], args[4])] = dict(packet_=args[3], digest_=args[4])
        elif "INSERT INTO" in sql and "install_receipt" in sql:
            assert args[2] not in rows["receipts"]
            rows["receipts"][args[2]] = dict(
                tenant_=args[0],
                installation_=args[1],
                operation_=args[2],
                kind_=args[3],
                request_digest_=args[4],
                expected_rev_=args[5],
                resulting_rev_=args[6],
                packet_digests_=args[7],
                recorded_at_=NOW,
            )
        elif "INSERT INTO" in sql and "decision_binding" in sql:
            names = sql.split("(", 1)[1].split(")", 1)[0].split(",")
            row = dict(zip(names, args, strict=True))
            key = (row["authority_rev_"], row["process_"], row["task_key_"])
            assert key not in rows["bindings"]
            rows["bindings"][key] = row
        else:
            raise AssertionError(sql)


def setup(monkeypatch):
    d = database()
    verifier, auth, packet, keys = fixture(installation_digest=sha(canonical(d)))
    state = State(d)
    state.material = packet.material
    monkeypatch.setattr(module, "_pinned_tls_context", lambda *args: ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT))
    monkeypatch.setattr(module.asyncpg, "connect", state.connect)
    identity = HumanTLSIdentity(SCOPE, Path("/synthetic/ca"), Path("/synthetic/cert"), Path("/synthetic/key"))
    connection = BindingConnection(
        database=d, identity=identity, verifier=verifier, mode="installer", clock=lambda: state.at
    )
    return DecisionBindingInstaller(connection), state, auth, packet, keys


async def test_install_exact_readback_and_reconcile_lost_commit(monkeypatch):
    installer, state, auth, packet, _ = setup(monkeypatch)
    await installer.designate(auth)
    state.lose_commit = True
    with pytest.raises(BindingUnavailableError):
        await installer.install(packet)
    assert state.rows["revision"] == 2
    receipt = await installer.reconcile(packet)
    assert receipt.kind == "install" and receipt.resulting_revision == 2
    assert len(state.rows["bindings"]) == 1 and len(state.connections) == 3
    for c in state.connections:
        assert c.kwargs["password"] == "" and c.kwargs["passfile"] == "/dev/null"
        assert c.kwargs["server_settings"]["search_path"] == "pg_catalog"


async def test_absent_reconcile_only_retries_original_revision(monkeypatch):
    installer, state, auth, packet, _ = setup(monkeypatch)
    await installer.designate(auth)
    state.rows["revision"] = 3
    with pytest.raises(BindingUnavailableError):
        await installer.reconcile(packet)
    assert not state.rows["bindings"]


@pytest.mark.parametrize("bad", ["reader-write", "engine-column-write", "installer-update", "membership"])
async def test_actual_acl_checks_refuse_effective_write_or_role_inheritance(monkeypatch, bad):
    installer, state, auth, _, _ = setup(monkeypatch)
    state.bad_acl = bad
    with pytest.raises(BindingUnavailableError):
        await installer.designate(auth)
    assert state.rows["revision"] == 0


@pytest.mark.parametrize(
    "key,value",
    [
        ("database_oid", 999),
        ("schema_oid", 999),
        ("role", "owner"),
        ("ssl", False),
        ("client_certificate", False),
    ],
)
async def test_catalog_and_session_identity_refuse_before_write(monkeypatch, key, value):
    installer, state, auth, _, _ = setup(monkeypatch)
    state.bad_catalog = {key: value}
    with pytest.raises(BindingUnavailableError):
        await installer.designate(auth)
    assert not state.rows["authorities"]


async def test_authority_removal_revokes_prior_packet_and_cannot_roll_back_generation(monkeypatch):
    installer, state, auth, packet, keys = setup(monkeypatch)
    await installer.designate(auth)
    await installer.install(packet)
    doc = auth.document.model_copy(
        update={
            "generation": 1,
            "expected_tenant_revision": 2,
            "operation_id": "revoke-signers",
            "signers": (),
        }
    )
    revoked = SignedAuthorities(document=doc, signature=sign(doc, "root", keys["root"]))
    await installer.designate(revoked)
    with pytest.raises(BindingUnavailableError):
        await installer.reconcile(packet)
    with pytest.raises(BindingUnavailableError):
        await installer.designate(auth)
    assert len(state.rows["bindings"]) == 1 and state.rows["revision"] == 3


async def test_expiry_during_final_catalog_recheck_rolls_back(monkeypatch):
    installer, state, auth, packet, _ = setup(monkeypatch)
    await installer.designate(auth)

    def advance(sql):
        if "SELECT * FROM" in sql and "decision_binding" in sql:
            state.at = END

    state.after_query = advance
    with pytest.raises(BindingUnavailableError):
        await installer.install(packet)
    assert state.rows["revision"] == 1 and not state.rows["bindings"]


async def test_readback_tampering_and_conflicting_operation_refuse(monkeypatch):
    installer, state, auth, packet, _ = setup(monkeypatch)
    await installer.designate(auth)
    await installer.install(packet)
    next(iter(state.rows["bindings"].values()))["consumer_digest_"] = "0" * 64
    with pytest.raises(BindingUnavailableError):
        await installer.reconcile(packet)
    assert len(state.rows["bindings"]) == 1


async def test_revocation_bumps_revision_without_rewriting_evidence(monkeypatch):
    installer, state, auth, packet, keys = setup(monkeypatch)
    await installer.designate(auth)
    await installer.install(packet)
    before = copy.deepcopy(state.rows["bindings"])
    r = packet.freeze.receipt.model_copy(
        update={
            "operation_id": "revoke-1",
            "expected_tenant_revision": 2,
            "source_ref": "decision-binding-revocation",
            "receipt_digests": (),
            "source_digest": packet.freeze.receipt.material_digest,
        }
    )
    value = Revocation(receipt=r, signature=sign(r, "freeze", keys["freeze"]))
    receipt = await installer.revoke(value)
    assert receipt.resulting_revision == 3 and state.rows["bindings"] == before
    with pytest.raises(BindingUnavailableError):
        await installer.reconcile(packet)


async def test_production_constructor_cannot_accept_arbitrary_pool_or_role(monkeypatch):
    installer, state, _, _, _ = setup(monkeypatch)
    with pytest.raises(BindingUnavailableError):
        DecisionBindingInstaller(object())
    with pytest.raises(BindingUnavailableError):
        PostgresDecisionBindingSource(installer._db)
    d = state.d.model_copy(update={"database_oid": 999})
    with pytest.raises(BindingUnavailableError):
        BindingConnection(
            database=d, identity=installer._db.identity, verifier=installer._db.verifier, mode="reader"
        )


def native_context(state, packet):
    from maezo.gateway.human.models import AuthoritativeTask, CurrentTaskAuthority
    from maezo.gateway.human.read_profile import (
        FullTaskClassification,
        ResourceIdentityGrant,
        ResourceProjection,
        SourceProvenance,
    )
    from maezo.gateway.human.read_publisher import PublicationReceipt
    from maezo.portal.contracts.models import HumanPrincipal, MembershipBinding, TaskSnapshot

    e = packet.material.entry
    state.required_group = packet.material.required_group
    from maezo.portal.contracts.models import PagtoAdmissibilityEvidence

    readonly = (
        PagtoAdmissibilityEvidence(
            kind="pagto_admissibilidade",
            valor_pagamento_cents="100",
            dados_pagamento_validos=False,
            lastro_confirmado=False,
            duplicidade_suspeita=True,
        )
        if e.form_key == "pagto_admissibilidade"
        else None
    )
    principal = HumanPrincipal(
        schema_version=1,
        principal_ref="human-1",
        issuer="https://issuer.test",
        subject="subject-1",
        tenant=SCOPE.tenant,
        membership_revision=4,
        memberships=(
            MembershipBinding(
                membership_ref="membership-1", roles=e.required_roles, groups=(state.required_group,)
            ),
        ),
        session_ref="session-1",
        authenticated_at=NOW,
        subject_bindings=(),
    )
    pins = {
        k: getattr(e, k)
        for k in (
            "process_definition_id",
            "process_definition_key",
            "process_definition_version",
            "process_definition_digest",
            "task_definition_key",
            "form_key",
            "form_version",
            "form_digest",
        )
    }
    snapshot = TaskSnapshot(
        schema_version=1,
        snapshot_at=NOW,
        task_id="task-1",
        **pins,
        form_source_status=e.form_source_status,
        task_revision=5,
        assignee_ref=principal.principal_ref,
        eligible_candidate_groups=(state.required_group,),
        evidence_revision=6,
        evidence_digest="b" * 64,
        engine_due_at=None,
        allowed_actions=("decision",),
        allowed_inputs=e.allowed_inputs,
        read_only_evidence=readonly,
    )
    task = AuthoritativeTask(
        tenant=SCOPE.tenant,
        snapshot=snapshot,
        active=True,
        authority_revision=2,
        required_roles=e.required_roles,
        required_subject_bindings=(),
        required_consent_scopes=(),
        valid_until=END,
    )
    authority = CurrentTaskAuthority(
        tenant=SCOPE.tenant,
        task_id="task-1",
        **pins,
        issuer=principal.issuer,
        subject=principal.subject,
        principal_ref=principal.principal_ref,
        membership_revision=4,
        authority_revision=2,
        task_revision=5,
        evidence_revision=6,
        evidence_digest="b" * 64,
        read_permitted=True,
        permitted_operations=("decision",),
        consent_scopes=(),
        valid_until=END,
    )
    state.native = dict(
        rev_=5,
        assignee_="human-1",
        proc_def_id_=e.process_definition_id,
        task_def_key_=e.task_definition_key,
        tenant_id_=SCOPE.tenant,
        suspension_state_=1,
        issuer_=principal.issuer,
        subject_=principal.subject,
        membership_revision=4,
        active_=True,
        principal_until=int(END.timestamp()),
        groups_='["' + state.required_group + '"]',
        ref_="evidence-1",
        evidence_revision=6,
        digest_="b" * 64,
        evidence_until=int(END.timestamp()),
        process_=e.process_definition_id,
    )
    resource = ResourceProjection(
        task_id="task-1",
        process_definition_id=e.process_definition_id,
        process_definition_digest=e.process_definition_digest,
        observed_task_revision=5,
        evidence_ref="evidence-1",
        evidence_revision=6,
        evidence_digest="b" * 64,
        resource_ref="resource-1",
        resource_revision=1,
        resource_digest="c" * 64,
        resource_policy=e.resource_policy,
        classification=FullTaskClassification(
            classification_ref="classification-1",
            classification_digest="d" * 64,
            policy_ref=e.disclosure_policy.artifact_ref,
            policy_digest=e.disclosure_policy.digest,
            projection="full_task_detail.v1",
            fields_digest="f" * 64,
            valid_until=END,
        ),
        required_subject_bindings=(),
        required_consent_scopes=(),
        positive_grants=(
            ResourceIdentityGrant(
                issuer=principal.issuer,
                subject=principal.subject,
                principal_ref=principal.principal_ref,
                membership_revision=4,
                consent_scopes=(),
                decision_receipt_ref="resource-policy-receipt",
                decision_digest="e" * 64,
                valid_until=END,
            ),
        ),
        read_only_evidence=readonly,
        state="complete",
        valid_until=END,
    )
    source = SourceProvenance(
        publisher_ref="qualified-publisher",
        source_ref="resource-source",
        source_revision=1,
        source_digest="c" * 64,
        receipt_ref="resource-source-receipt",
        observed_at=NOW,
        valid_until=END,
    )
    proof = PublicationReceipt(
        schema="portal-read-publication-receipt.v1",
        publication_id="resource-publication",
        request_digest="a" * 64,
        authority_revision=2,
        kind="resource",
        record_digest=sha(canonical(resource)),
    )
    state.resource = dict(
        payload_=canonical(resource).decode(),
        source_=canonical(source).decode(),
        publication_="resource-publication",
        receipt_=canonical(proof).decode(),
        digest_="a" * 64,
        key_fingerprint_="e" * 64,
    )
    return principal, task, authority


async def prepared_source(monkeypatch):
    installer, state, auth, packet, keys = setup(monkeypatch)
    await installer.designate(auth)
    await installer.install(packet)
    context = native_context(state, packet)
    reader = BindingConnection(
        database=state.d,
        identity=installer._db.identity,
        verifier=installer._db.verifier,
        mode="reader",
        clock=lambda: state.at,
    )
    return PostgresDecisionBindingSource(reader), state, context, installer, auth, packet, keys


@pytest.mark.parametrize("microsecond", [0, 1, 999999])
async def test_native_binding_deadline_floors_to_seconds_without_extension(microsecond):
    verifier, authority, packet, _ = fixture()
    qualified = verifier.verify(packet, authority, NOW)
    deadline = END.replace(microsecond=microsecond)
    qualified = qualified.model_copy(update={"valid_until": deadline})
    row = module.binding_columns(qualified, SCOPE)
    assert row["valid_until_"] == int(END.timestamp())
    assert type(row["valid_until_"]) is int


@pytest.mark.parametrize(
    "fields", [("principal_until",), ("evidence_until",), ("principal_until", "evidence_until")]
)
@pytest.mark.parametrize("remaining", [-1, 0, 10])
async def test_native_principal_and_evidence_seconds_bound_current_read(monkeypatch, fields, remaining):
    source, state, context, *_ = await prepared_source(monkeypatch)
    deadline = NOW + timedelta(seconds=remaining)
    for field in fields:
        state.native[field] = int(deadline.timestamp())
    if remaining <= 0:
        with pytest.raises(BindingUnavailableError):
            await source.qualify(*context)
    else:
        value = await source.qualify(*context)
        assert value.valid_until == deadline


async def test_read_source_checks_actual_native_rows_and_returns_existing_reference(monkeypatch):
    source, state, context, _, _, packet, _ = await prepared_source(monkeypatch)
    value = await source.qualify(*context)
    assert value.evidence_ref == "evidence-1" and value.binding_digest == sha(canonical(packet))
    assert value.principal == context[0] and value.task == context[1] and value.authority == context[2]
    assert state.connections[-1].readonly is True


@pytest.mark.parametrize(
    "field,value",
    [
        ("assignee_", "other"),
        ("tenant_id_", "other"),
        ("membership_revision", 99),
        ("issuer_", "https://wrong.test"),
        ("evidence_revision", 99),
        ("digest_", "0" * 64),
        ("process_", "other"),
        ("groups_", '["admin"]'),
        ("rev_", 99),
        ("active_", False),
        ("principal_until", 1),
        ("evidence_until", 1),
    ],
)
async def test_read_source_refuses_native_drift(monkeypatch, field, value):
    source, state, context, *_ = await prepared_source(monkeypatch)
    state.native[field] = value
    with pytest.raises(BindingUnavailableError):
        await source.qualify(*context)


async def test_missing_resource_producer_and_wrong_publisher_stay_refusal(monkeypatch):
    source, state, context, *_ = await prepared_source(monkeypatch)
    old = state.resource
    state.resource = None
    with pytest.raises(BindingUnavailableError):
        await source.qualify(*context)
    state.resource = old | {"key_fingerprint_": "0" * 64}
    with pytest.raises(BindingUnavailableError):
        await source.qualify(*context)


async def test_q2_read_only_context_is_not_mutation_authority(monkeypatch):
    source, _, context, *_ = await prepared_source(monkeypatch)
    principal, task, authority = context
    with pytest.raises(BindingUnavailableError):
        await source.qualify(principal, task, authority.model_copy(update={"permitted_operations": ()}))
    with pytest.raises(BindingUnavailableError):
        await source.qualify(
            principal,
            task.model_copy(update={"snapshot": task.snapshot.model_copy(update={"allowed_actions": ()})}),
            authority,
        )


async def test_read_rechecks_root_generation_after_native_reads(monkeypatch):
    source, state, context, _, auth, _, keys = await prepared_source(monkeypatch)

    def revoke_on_read(sql):
        if "SELECT r.payload_" in sql:
            d = auth.document.model_copy(update={"generation": 1, "signers": ()})
            a = SignedAuthorities(document=d, signature=sign(d, "root", keys["root"]))
            state.rows["authorities"][1] = (canonical(a).decode(), sha(canonical(a)))

    state.after_query = revoke_on_read
    with pytest.raises(BindingUnavailableError):
        await source.qualify(*context)


def six_packets(state, keys):
    from maezo.gateway.human.decision_binding_qualification import batch_member
    from maezo.portal.engine.decision import _LEGACY_BINDINGS as _BINDINGS

    materials = tuple(fixture(pair, keys=keys)[2].material for pair in _BINDINGS)
    batch = tuple(
        sorted(
            (batch_member(m) for m in materials),
            key=lambda m: (m.process_definition_id, m.task_definition_key),
        )
    )
    packets = tuple(
        fixture(pair, material=m, keys=keys, batch=batch)[2]
        for pair, m in zip(_BINDINGS, materials, strict=True)
    )
    state.material = materials
    return packets


async def test_all_six_coexist_at_one_revision_and_each_read_source_qualifies(monkeypatch):
    installer, state, auth, _, keys = setup(monkeypatch)
    await installer.designate(auth)
    packets = six_packets(state, keys)
    receipt = await installer.install_batch(packets)
    assert state.rows["revision"] == 2 and len(state.rows["bindings"]) == 6
    assert {r["authority_rev_"] for r in state.rows["bindings"].values()} == {2}
    assert len(receipt.packet_digests) == 6
    reader = PostgresDecisionBindingSource(
        BindingConnection(
            database=state.d,
            identity=installer._db.identity,
            verifier=installer._db.verifier,
            mode="reader",
            clock=lambda: state.at,
        )
    )
    for packet in packets:
        context = native_context(state, packet)
        result = await reader.qualify(*context)
        assert result.binding_digest == sha(canonical(packet))
    assert await installer.reconcile_batch(tuple(reversed(packets))) == receipt


@pytest.mark.parametrize("case", ["omission", "duplicate", "seven"])
async def test_signed_batch_cannot_omit_duplicate_or_add_seventh(monkeypatch, case):
    installer, state, auth, _, keys = setup(monkeypatch)
    await installer.designate(auth)
    packets = six_packets(state, keys)
    bad = (
        packets[:-1]
        if case == "omission"
        else packets[:-1] + (packets[0],)
        if case == "duplicate"
        else packets + (packets[0],)
    )
    with pytest.raises(BindingUnavailableError):
        await installer.install_batch(bad)
    assert state.rows["revision"] == 1 and not state.rows["bindings"]


async def test_successive_single_installs_invalidate_old_authority_without_carry_forward(monkeypatch):
    from maezo.portal.engine.decision import _LEGACY_BINDINGS as _BINDINGS

    installer, state, auth, first, keys = setup(monkeypatch)
    await installer.designate(auth)
    await installer.install(first)
    second = fixture(list(_BINDINGS)[1], keys=keys, expected_revision=2, operation="install-2")[2]
    state.material = (first.material, second.material)
    await installer.install(second)
    assert {r["authority_rev_"] for r in state.rows["bindings"].values()} == {2, 3}
    with pytest.raises(BindingUnavailableError):
        await installer.reconcile(first)
    assert len(state.rows["bindings"]) == 2


async def test_freeze_expiry_before_commit_refuses_while_root_remains_current(monkeypatch):
    from datetime import timedelta

    installer, state, auth, packet, keys = setup(monkeypatch)
    await installer.designate(auth)
    frozen = packet.freeze.receipt.model_copy(update={"valid_until": NOW + timedelta(seconds=1)})
    packet = packet.model_copy(
        update={"freeze": SignedReceipt(receipt=frozen, signature=sign(frozen, "freeze", keys["freeze"]))}
    )

    def advance(sql):
        if "SELECT * FROM" in sql and "decision_binding" in sql:
            state.at = NOW + timedelta(seconds=2)

    state.after_query = advance
    with pytest.raises(BindingUnavailableError):
        await installer.install(packet)
    assert state.rows["revision"] == 1 and not state.rows["bindings"]


async def test_native_group_object_cannot_masquerade_as_group_array(monkeypatch):
    source, state, context, *_ = await prepared_source(monkeypatch)
    state.native["groups_"] = '{"medico-auditor":false}'
    with pytest.raises(BindingUnavailableError):
        await source.qualify(*context)


async def test_native_deployed_bytes_cannot_be_replaced_by_a_packet_digest(monkeypatch):
    from tests.unit.gateway.human.test_decision_binding_qualification import artifact

    installer, state, auth, packet, _ = setup(monkeypatch)
    await installer.designate(auth)
    # Source receipt still pins the original actual BPMN; native bytes now differ.
    different = artifact(packet.material.process.artifact_ref, b"<changed native deployment/>")
    state.material = packet.material.model_copy(update={"process": different})
    with pytest.raises(BindingUnavailableError):
        await installer.install(packet)
    assert state.rows["revision"] == 1 and not state.rows["bindings"]


async def test_decision_operations_cannot_override_denied_current_read_authority(monkeypatch):
    source, _, context, *_ = await prepared_source(monkeypatch)
    principal, task, authority = context
    with pytest.raises(BindingUnavailableError):
        await source.qualify(principal, task, authority.model_copy(update={"read_permitted": False}))


@pytest.mark.parametrize("count", range(1, 7))
async def test_runtime_readback_complete_batches_one_through_six(monkeypatch, count):
    from maezo.gateway.human.decision_binding_qualification import batch_member

    installer, state, auth, _, keys = setup(monkeypatch)
    packets = six_packets(state, keys)[:count]
    batch = tuple(
        sorted(
            (batch_member(p.material) for p in packets),
            key=lambda m: (m.process_definition_id, m.task_definition_key),
        )
    )
    packets = tuple(
        fixture(
            (p.material.entry.process_definition_key, p.material.entry.task_definition_key),
            material=p.material,
            keys=keys,
            batch=batch,
        )[2]
        for p in packets
    )
    await installer.designate(auth)
    receipt = await installer.install_batch(packets)
    reader = PostgresDecisionBindingSource(
        BindingConnection(
            database=state.d,
            identity=installer._db.identity,
            verifier=installer._db.verifier,
            mode="reader",
            clock=lambda: state.at,
        )
    )
    for packet in packets:
        assert (await reader.qualify(*native_context(state, packet))).binding_digest == sha(canonical(packet))
    assert len(receipt.packet_digests) == count


@pytest.mark.parametrize(
    "corruption",
    [
        "receipt-subset",
        "missing-qualification",
        "missing-binding",
        "extra-qualification",
        "extra-binding",
        "duplicate-binding",
        "sibling-bytes",
        "sibling-binding",
        "sibling-operation",
        "sibling-freeze",
        "sibling-batch",
        "sibling-expired",
    ],
)
async def test_complete_batch_corruption_refuses_without_effects(monkeypatch, corruption):
    from datetime import timedelta

    from tests.unit.gateway.human.test_decision_binding_qualification import resign_sources

    from maezo.portal.engine.profile import canonicalize

    installer, state, auth, _, keys = setup(monkeypatch)
    packets = six_packets(state, keys)
    await installer.designate(auth)
    receipt = await installer.install_batch(packets)
    reader = PostgresDecisionBindingSource(
        BindingConnection(
            database=state.d,
            identity=installer._db.identity,
            verifier=installer._db.verifier,
            mode="reader",
            clock=lambda: state.at,
        )
    )
    context = native_context(state, packets[0])
    assert (await reader.qualify(*context)).binding_digest == sha(canonical(packets[0]))
    sibling = packets[1]
    digest = sha(canonical(sibling))
    key = next(k for k, r in state.rows["bindings"].items() if r["binding_digest_"] == digest)
    if corruption == "receipt-subset":
        digests = [sha(canonical(packets[0]))]
        stored = state.rows["receipts"][receipt.operation_id]
        stored["packet_digests_"] = canonicalize(digests).decode()
        stored["request_digest_"] = sha(canonicalize(digests))
    elif corruption == "missing-qualification":
        state.rows["qualification"].pop((2, digest))
    elif corruption == "missing-binding":
        state.rows["bindings"].pop(key)
    elif corruption == "extra-qualification":
        state.rows["qualification"][(2, "0" * 64)] = dict(packet_="{}", digest_="0" * 64)
    elif corruption in ("extra-binding", "duplicate-binding"):
        row = dict(state.rows["bindings"][key])
        if corruption == "extra-binding":
            row["binding_digest_"] = "0" * 64
        state.rows["bindings"]["extra"] = row
    elif corruption == "sibling-bytes":
        state.rows["qualification"][(2, digest)]["packet_"] = "{}"
    elif corruption == "sibling-binding":
        state.rows["bindings"][key]["consumer_digest_"] = "0" * 64
    else:
        updates = {
            "sibling-operation": {"operation_id": "other-install"},
            "sibling-freeze": {"freeze_epoch": 3},
            "sibling-batch": {"batch": sibling.freeze.receipt.batch[:1]},
            "sibling-expired": {"valid_until": NOW + timedelta(seconds=1)},
        }[corruption]
        altered = resign_sources(
            sibling, keys, source_updates={i: updates for i in range(3)}, freeze_updates=updates
        )
        # Recompute every durable digest/column/receipt: only the signed relation is wrong.
        q = installer._db.verifier.verify(altered, auth, NOW) if corruption != "sibling-batch" else None
        changed = sha(canonical(altered))
        state.rows["qualification"].pop((2, digest))
        state.rows["qualification"][(2, changed)] = dict(packet_=canonical(altered).decode(), digest_=changed)
        if q is not None:
            state.rows["bindings"][key] = module.binding_columns(q, SCOPE)
        else:
            state.rows["bindings"][key]["binding_digest_"] = changed
        digests = sorted(changed if d == digest else d for d in receipt.packet_digests)
        stored = state.rows["receipts"][receipt.operation_id]
        stored["packet_digests_"] = canonicalize(digests).decode()
        stored["request_digest_"] = sha(canonicalize(digests))
        if corruption == "sibling-expired":

            def expire_on_native(sql):
                if "SELECT r.payload_" in sql:
                    state.at = NOW + timedelta(seconds=2)

            state.after_query = expire_on_native
    before = copy.deepcopy(state.rows)
    with pytest.raises(BindingUnavailableError, match="^decision binding unavailable$"):
        await reader.qualify(*context)
    assert state.rows == before


@pytest.mark.parametrize("field", ["policy_ref", "policy_digest"])
async def test_classification_policy_matches_disclosure_not_resource_policy(monkeypatch, field):
    from maezo.portal.engine.profile import canonicalize, strict_loads

    source, state, context, *_ = await prepared_source(monkeypatch)
    assert (await source.qualify(*context)).evidence_ref == "evidence-1"
    resource = strict_loads(state.resource["payload_"].encode())
    resource["classification"][field] = "foreign-policy" if field == "policy_ref" else "0" * 64
    proof = strict_loads(state.resource["receipt_"].encode())
    proof["record_digest"] = sha(canonicalize(resource))
    state.resource["payload_"] = canonicalize(resource).decode()
    state.resource["receipt_"] = canonicalize(proof).decode()
    before = copy.deepcopy(state.rows)
    with pytest.raises(BindingUnavailableError):
        await source.qualify(*context)
    assert state.rows == before


async def test_distinct_disclosure_policy_positive_and_resource_policy_is_not_substitute(monkeypatch):
    from maezo.gateway.human.read_profile import ArtifactPin
    from maezo.portal.engine.profile import canonicalize, strict_loads

    installer, state, auth, packet, keys = setup(monkeypatch)
    material = packet.material.model_copy(
        update={
            "entry": packet.material.entry.model_copy(
                update={
                    "disclosure_policy": ArtifactPin(
                        artifact_ref="distinct-disclosure-policy", digest="9" * 64
                    ),
                }
            ),
        }
    )
    packet = fixture(material=material, keys=keys)[2]
    state.material = material
    await installer.designate(auth)
    await installer.install(packet)
    reader = PostgresDecisionBindingSource(
        BindingConnection(
            database=state.d,
            identity=installer._db.identity,
            verifier=installer._db.verifier,
            mode="reader",
            clock=lambda: state.at,
        )
    )
    context = native_context(state, packet)
    assert (await reader.qualify(*context)).binding_digest == sha(canonical(packet))
    resource = strict_loads(state.resource["payload_"].encode())
    assert resource["classification"]["policy_digest"] != resource["resource_policy"]["digest"]
    resource["classification"]["policy_ref"] = material.entry.resource_policy.artifact_ref
    resource["classification"]["policy_digest"] = material.entry.resource_policy.digest
    proof = strict_loads(state.resource["receipt_"].encode())
    proof["record_digest"] = sha(canonicalize(resource))
    state.resource["payload_"] = canonicalize(resource).decode()
    state.resource["receipt_"] = canonicalize(proof).decode()
    with pytest.raises(BindingUnavailableError):
        await reader.qualify(*context)
