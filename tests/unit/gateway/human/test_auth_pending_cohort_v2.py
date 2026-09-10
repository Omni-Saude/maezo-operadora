"""Synthetic qualification/SQL collaborators; never production source/owner evidence."""

import base64
import copy
from datetime import timedelta

import pytest
from pydantic import ValidationError
from tests.unit.gateway.human.test_decision_binding import native_context, setup
from tests.unit.gateway.human.test_decision_binding_qualification import (
    END,
    NOW,
    SCOPE,
    artifact,
    fixture,
    sign,
)

from maezo.gateway.human.decision_binding import (
    BindingConnection,
    DecisionBindingInstaller,
    InstallationReceiptV2,
    PostgresDecisionBindingSource,
    stored_receipt,
)
from maezo.gateway.human.decision_binding_qualification import (
    COHORT_CONTRACT,
    BindingMaterial,
    BindingUnavailableError,
    CohortManifest,
    ConsumerQualificationV2,
    QualificationPacketV2,
    QualificationVerifier,
    ReceiptV2,
    SignedAuthorities,
    SignedReceiptV2,
    batch_member,
    canonical,
    decode_packet,
    member_order,
    sha,
)
from maezo.gateway.human.read_profile import wire
from maezo.portal.engine.decision import _LEGACY_BINDINGS
from maezo.portal.engine.profile import canonicalize

PENDING = ("SP-OP-AUTH-001", "UT_DecidirPendenciaExpirada")


def seven_fixture(*, database=None, keys=None):
    verifier, auth, old, keys = fixture(
        keys=keys, **({"installation_digest": sha(canonical(database))} if database else {})
    )
    materials = [fixture(pair, keys=keys)[2].material for pair in _LEGACY_BINDINGS]
    form = artifact("auth_pendencia", b"synthetic closed expiry enum form")
    m = materials[0]
    entry = m.entry.model_copy(
        update=dict(
            task_definition_key=PENDING[1],
            form_key="auth_pendencia",
            form_digest=form.digest,
            form_source_status="BPMN_TASK_DOCUMENTATION_DRAFT_VERIFY",
            allowed_inputs=("decisao_pendencia",),
        )
    )
    classification = m.classification.model_copy(
        update=dict(input_fields=entry.allowed_inputs, outcome_field="decisao_pendencia", phi_fields=())
    )
    consumer = ConsumerQualificationV2(
        **{
            **m.consumer.model_dump(),
            "mode": "classified_outcome_only",
            "hydration_contract": artifact(
                "no-hydration-boundary",
                b"synthetic qualified enum-only native/custody boundary; no downstream PHI hydration",
            ),
        }
    )
    materials.append(
        BindingMaterial(
            entry=entry,
            process=m.process,
            group_dmn=None,
            form=form,
            classification=classification,
            consumer=consumer,
            required_group="medico-auditor",
        )
    )
    scope = dict(
        tenant=SCOPE.tenant,
        environment=SCOPE.environment,
        engine_name=database.engine_name if database else "engine-test",
        database_incarnation=database.database_incarnation if database else "database-test",
        database_binding_digest="d" * 64,
    )
    cohort = CohortManifest(
        schema="human-decision-cohort.v2",
        scope=scope,
        members=tuple(sorted((batch_member(m) for m in materials), key=lambda m: member_order(m, v2=True))),
    )
    root = verifier.root.model_copy(update={"freeze_contract_digest": COHORT_CONTRACT})
    signers = tuple(s.model_copy(update={"contract_digest": COHORT_CONTRACT}) for s in auth.document.signers)
    document = auth.document.model_copy(update={"signers": signers})
    auth = SignedAuthorities(document=document, signature=sign(document, "root", keys["root"]))
    packets = []
    for m in materials:
        sources = (
            sha(canonical(m.entry) + canonical(m.process) + canonical(m.form)),
            sha(canonical(m.classification)),
            sha(canonical(m.consumer)),
        )
        receipts = []
        for signer, source in zip(signers, sources, strict=False):
            r = ReceiptV2(
                **{
                    **old.receipts[0].receipt.model_dump(by_alias=True),
                    "schema": "human-decision-qualification-receipt.v2",
                    "cohort_digest": cohort.digest,
                    "batch": cohort.members,
                    "material_digest": sha(canonical(m)),
                    "purpose": signer.purpose,
                    "issuer": signer.issuer,
                    "source_ref": signer.purpose + "-source",
                    "source_digest": source,
                    "receipt_ref": signer.purpose + "-receipt",
                    "contract_ref": signer.contract_ref,
                    "contract_digest": signer.contract_digest,
                    "installed_authority_ref": signer.installed_authority_ref,
                }
            )
            receipts.append(SignedReceiptV2(receipt=r, signature=sign(r, signer.key_id, keys[signer.key_id])))
        digests = tuple(sha(canonical(r)) for r in receipts)
        signer = signers[-1]
        f = ReceiptV2(
            **{
                **receipts[0].receipt.model_dump(by_alias=True),
                "purpose": "freeze",
                "issuer": signer.issuer,
                "source_ref": "freeze-source",
                "source_digest": sha(canonicalize(list(digests))),
                "receipt_digests": digests,
                "receipt_ref": "freeze-receipt",
                "contract_ref": signer.contract_ref,
                "installed_authority_ref": signer.installed_authority_ref,
            }
        )
        packets.append(
            QualificationPacketV2(
                schema="human-decision-qualification-packet.v2",
                material=m,
                receipts=tuple(receipts),
                freeze=SignedReceiptV2(receipt=f, signature=sign(f, "freeze", keys["freeze"])),
                cohort=cohort,
            )
        )
    return QualificationVerifier(root), auth, tuple(packets), keys


def test_seven_acyclic_material_packet_digests_and_legacy_roundtrip():
    v, a, packets, _ = seven_fixture()
    assert len(packets) == 7 and len({p.cohort.digest for p in packets}) == 1
    for p in packets:
        q = v.verify(decode_packet(canonical(p)), a, NOW)
        assert q.binding_digest == sha(canonical(p))
        assert p.cohort.members.count(batch_member(p.material)) == 1
    old_v, old_a, old, _ = fixture()
    assert decode_packet(
        canonical(old)
    ) == old and b'"schema":"human-decision-qualification-packet' not in canonical(old)
    assert old_v.verify(old, old_a, NOW).binding_digest == sha(canonical(old))
    with pytest.raises(BindingUnavailableError):
        old_v.verify(packets[-1], old_a, NOW)


@pytest.mark.parametrize(
    "change", ["missing", "duplicate", "uncompiled", "digest", "foreign-scope", "no-owner-signature"]
)
def test_closed_cohort_cannot_be_widened_or_changed_without_matching_signatures(change):
    v, a, packets, _ = seven_fixture()
    data = wire(packets[-1])
    members = data["cohort"]["members"]
    if change == "missing":
        members.pop()
    elif change == "duplicate":
        members[-1] = members[0]
    elif change == "uncompiled":
        members[-1]["task_definition_key"] = "UT_Arbitrary"
    elif change == "digest":
        members[-1]["material_digest"] = "0" * 64
    elif change == "foreign-scope":
        data["cohort"]["scope"]["tenant"] = "other"
    else:
        data["freeze"]["signature"]["value"] = base64.b64encode(bytes(64)).decode()
    with pytest.raises((BindingUnavailableError, ValidationError)):
        v.verify(decode_packet(canonicalize(data)), a, NOW)


def test_outcome_only_mode_cannot_qualify_narrative_or_legacy_packet():
    v, a, packets, _ = seven_fixture()
    data = wire(packets[0])
    data["material"]["consumer"]["mode"] = "classified_outcome_only"
    with pytest.raises((BindingUnavailableError, ValidationError)):
        v.verify(decode_packet(canonicalize(data)), a, NOW)
    oldv, olda, old, _ = fixture()
    data = wire(old)
    data["material"] = wire(packets[-1].material)
    with pytest.raises((BindingUnavailableError, ValidationError)):
        oldv.verify(decode_packet(canonicalize(data)), olda, NOW)


@pytest.mark.asyncio
async def test_durable_v2_lost_ack_reconstruction_exact_replay_and_native_staging(monkeypatch):
    old, state, _, _, keys = setup(monkeypatch)
    v, a, packets, _ = seven_fixture(database=state.d, keys=keys)
    state.material = tuple(p.material for p in packets)

    def connect(mode):
        return BindingConnection(
            database=state.d, identity=old._db.identity, verifier=v, mode=mode, clock=lambda: state.at
        )

    installer = DecisionBindingInstaller(connect("installer"))
    await installer.designate(a)
    with pytest.raises(BindingUnavailableError):
        await installer.install_batch(packets[:-1])
    assert not state.rows["bindings"] and state.rows["revision"] == 1
    state.lose_commit = True
    with pytest.raises(BindingUnavailableError):
        await installer.install_batch(packets)
    assert state.rows["revision"] == 2 and len(state.rows["bindings"]) == 7
    row = state.rows["receipts"]["install-1"]
    assert '"schema":"human-decision-install-record.v2"' in row["packet_digests_"]
    durable = stored_receipt(copy.deepcopy(row))
    assert isinstance(durable, InstallationReceiptV2)
    reconstructed = DecisionBindingInstaller(connect("installer"))
    assert await reconstructed.reconcile_batch(tuple(reversed(packets))) == durable
    assert state.rows["revision"] == 2
    # Installed gateway material cannot admit while actual native cohort has not been qualified.
    reader = PostgresDecisionBindingSource(connect("reader"))
    with pytest.raises(BindingUnavailableError):
        await reader.qualify(*native_context(state, packets[-1]))
    changed = dict(row)
    changed["packet_digests_"] = canonicalize(list(durable.packet_digests)).decode()
    with pytest.raises((ValidationError, BindingUnavailableError)):
        stored_receipt(changed)
    state.at = END + timedelta(seconds=1)
    with pytest.raises(BindingUnavailableError):
        await reconstructed.reconcile_batch(packets)


def native_read_fixture(monkeypatch):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from maezo.gateway.human.decision_binding import native_object

    old, state, _, _, keys = setup(monkeypatch)
    v, a, packets, _ = seven_fixture(database=state.d, keys=keys)
    qualified = tuple(v.verify(p, a, NOW) for p in packets)
    signer = Ed25519PrivateKey.generate()
    url = lambda b: base64.urlsafe_b64encode(b).rstrip(b"=").decode()
    start = str(int(NOW.timestamp() * 1000))
    end = str(int(END.timestamp() * 1000))
    designation = dict(
        schema="phi-consumer-edge-trust.v1",
        issuer="synthetic-native-owner",
        key_id="native-owner-key",
        public_key=url(signer.public_key().public_bytes_raw()),
        purpose="native-consumer-edge-installation",
        authority_ref="native-owner-designation",
        contract_digest=COHORT_CONTRACT,
        source_freeze_contract_digest="b" * 64,
        valid_from_ms=start,
        valid_until_ms=end,
    )
    cohort = packets[0].cohort
    targets = []
    for q in sorted(
        qualified,
        key=lambda q: (
            q.packet.material.entry.process_definition_key,
            q.packet.material.entry.task_definition_key,
        ),
    ):
        e = q.packet.material.entry
        edges = []
        if e.form_key == "auth_decisao":
            edges.append(
                dict(
                    outcome="JUNTA_MEDICA",
                    activity_id="ST_ConvocarJunta",
                    topic="operadora.auth.convene_junta",
                    consumer_kind="auth_junta_forward",
                )
            )
        if e.form_key in ("auth_decisao", "auth_junta"):
            edges.append(
                dict(
                    outcome="NEGAR",
                    activity_id="ST_EnviarNegativaFormal",
                    topic="operadora.auth.send_denial_notice",
                    consumer_kind="auth_denial_record",
                )
            )
        if e.form_key == "pagto_admissibilidade":
            edges.append(
                dict(
                    outcome="DEVOLVER",
                    activity_id="ST_RegisterPaymentRefusal",
                    topic="operadora.pagto.register_payment_refusal",
                    consumer_kind="pagto_admissibility_return",
                )
            )
        targets.append(
            dict(
                process_definition_id=e.process_definition_id,
                process_key=e.process_definition_key,
                task_key=e.task_definition_key,
                binding_digest=q.binding_digest,
                consumer_digest=q.packet.material.consumer.deployed_consumer.digest,
                process_digest=e.process_definition_digest,
                material_digest=sha(canonical(q.packet.material)),
                edges=edges,
            )
        )
    body = dict(
        schema="phi-consumer-edge-qualification.v2",
        scope=wire(cohort.scope),
        qualification_ref="native-install-1",
        expected_generation="0",
        expected_authority_revision="2",
        native_build_digest="1" * 64,
        phi_build_digest="2" * 64,
        source_freeze=dict(
            issuer_contract_digest="b" * 64,
            authority_ref=designation["authority_ref"],
            source_commit="3" * 40,
            source_tree="4" * 40,
            native_build_digest="1" * 64,
            phi_build_digest="2" * 64,
            source_artifacts_digest="5" * 64,
        ),
        targets=targets,
        valid_from_ms=start,
        valid_until_ms=end,
        cohort=wire(cohort),
        cohort_digest=cohort.digest,
    )
    envelope = dict(
        schema="phi-consumer-edge-qualification-envelope.v1",
        body=body,
        **{k: designation[k] for k in ("issuer", "key_id", "purpose", "authority_ref", "contract_digest")},
    )

    def signed():
        return {**envelope, "signature": url(signer.sign(canonicalize(envelope)))}

    d = state.d
    binding = dict(
        schema="phi-consumer-native-database.v1",
        scope=wire(cohort.scope),
        database_name=d.database,
        database_oid=str(d.database_oid),
        schema_name=d.schema_name,
        schema_oid=str(d.schema_oid),
        owner_role=d.owner_role,
        runtime_role=d.engine_role,
    )
    config = NativeCohortReadConfiguration(
        scope=cohort.scope,
        binding_database_digest=sha(canonical(d)),
        designation_digest=sha(canonicalize(designation)),
        native_build_digest="1" * 64,
        phi_build_digest="2" * 64,
        relations=tuple(
            RelationPin(name=name, oid=800 + i, owner=d.owner_role)
            for i, name in enumerate(NATIVE_COHORT_RELATIONS)
        ),
    )
    conn = BindingConnection(
        database=d,
        identity=old._db.identity,
        verifier=v,
        mode="reader",
        clock=lambda: state.at,
        native_cohort_read=config,
    )

    class NativeRows:
        generation = 1
        revoked = False

        async def fetchrow(self, sql, *args):
            if "SELECT h.generation_" in sql:
                return dict(
                    generation_=self.generation,
                    qualification_generation_=1,
                    authority_rev_=2,
                    qualification_="native-install-1",
                    envelope_=canonicalize(signed()).decode(),
                    binding_=canonicalize(binding).decode(),
                )
            if "SELECT designation_" in sql:
                return dict(designation_=canonicalize(designation).decode())
            if "SELECT key_id_" in sql:
                return dict(key_id_="native-owner-key") if self.revoked else None
            raise AssertionError(sql)

    assert native_object(canonicalize(signed()).decode())["body"] == body
    return conn, NativeRows(), qualified, body, state


@pytest.mark.asyncio
async def test_actual_native_signed_cohort_readback_and_reconstructed_reader(monkeypatch):
    conn, rows, qualified, body, state = native_read_fixture(monkeypatch)
    first = await conn.native_cohort_current(rows, qualified, 2)
    reconstructed = BindingConnection(
        database=conn.database,
        identity=conn.identity,
        verifier=conn.verifier,
        mode="reader",
        clock=lambda: state.at,
        native_cohort_read=conn.native_cohort_read,
    )
    assert await reconstructed.native_cohort_current(rows, qualified, 2) == first
    assert first[1] == END
    assert next(t for t in body["targets"] if t["task_key"] == PENDING[1])["edges"] == []
    rows.generation = 2
    with pytest.raises(BindingUnavailableError):
        await reconstructed.native_cohort_current(rows, qualified, 2)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        "extra-target",
        "missing-target",
        "wrong-binding",
        "wrong-material",
        "false-hydration",
        "revoked",
        "expired",
        "foreign-generation",
    ],
)
async def test_even_validly_signed_native_body_cannot_override_current_cohort(monkeypatch, change):
    conn, rows, qualified, body, state = native_read_fixture(monkeypatch)
    if change == "extra-target":
        body["targets"].append(body["targets"][0])
    elif change == "missing-target":
        body["targets"].pop()
    elif change == "wrong-binding":
        body["targets"][0]["binding_digest"] = "0" * 64
    elif change == "wrong-material":
        body["targets"][0]["material_digest"] = "0" * 64
    elif change == "false-hydration":
        next(t for t in body["targets"] if t["task_key"] == PENDING[1])["edges"] = [
            body["targets"][0]["edges"][0]
        ]
    elif change == "revoked":
        rows.revoked = True
    elif change == "expired":
        state.at = END
    else:
        body["expected_generation"] = "1"
    with pytest.raises(BindingUnavailableError):
        await conn.native_cohort_current(rows, qualified, 2)
