"""Synthetic independent signing fixtures; no production authority or consumer is asserted."""

import base64
import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import ValidationError

from maezo.gateway.human.decision_binding_qualification import (
    FREEZE_CONTRACT,
    Artifact,
    AuthorityDocument,
    BindingMaterial,
    BindingUnavailableError,
    Classification,
    ConsumerQualification,
    QualificationPacket,
    QualificationVerifier,
    Receipt,
    RootDesignation,
    Signature,
    SignedAuthorities,
    SignedReceipt,
    SignerDesignation,
    batch_member,
    canonical,
    decode,
    sha,
)
from maezo.gateway.human.models import Scope
from maezo.gateway.human.read_profile import ArtifactPin, GroupDomain, ReadCatalogEntry
from maezo.portal.contracts.models import _BINDINGS, _INPUTS_BY_FORM
from maezo.portal.engine.decision import _BINDINGS as SIX
from maezo.portal.engine.decision import _OUTCOMES
from maezo.portal.engine.profile import canonicalize

NOW = datetime(2026, 9, 10, tzinfo=UTC)
END = NOW + timedelta(hours=1)
SCOPE = Scope(tenant="test-tenant", environment="test", workload_ref="human-gateway")


def artifact(ref, raw):
    return Artifact(artifact_ref=ref, digest=sha(raw), bytes_base64=base64.b64encode(raw).decode())


def sign(value, key_id, key):
    return Signature(key_id=key_id, value=base64.b64encode(key.sign(canonical(value))).decode())


def fixture(
    pair=None,
    *,
    installation_digest="a" * 64,
    material=None,
    expected_revision=1,
    generation=0,
    operation="install-1",
    keys=None,
    batch=None,
):
    pair = pair or next(iter(SIX))
    kind = SIX[pair]
    keys = keys or {
        p: Ed25519PrivateKey.generate()
        for p in ("root", "deployment", "classification", "consumer", "freeze")
    }
    root = RootDesignation(
        scope=SCOPE,
        installation_id="test-installation",
        key_id="root",
        public_key=base64.b64encode(keys["root"].public_key().public_bytes_raw()).decode(),
        installation_digest=installation_digest,
        freeze_contract_digest="f" * 64,
        freeze_installed_authority_ref="freeze-service-installation-1",
        observed_at=NOW,
        valid_until=END,
    )
    signers = tuple(
        SignerDesignation(
            issuer=p + "-issuer",
            key_id=p,
            public_key=base64.b64encode(keys[p].public_key().public_bytes_raw()).decode(),
            purpose=p,
            contract_ref=FREEZE_CONTRACT if p == "freeze" else p + "-contract",
            contract_digest="f" * 64 if p == "freeze" else "e" * 64,
            installed_authority_ref="freeze-service-installation-1" if p == "freeze" else p + "-installation",
            observed_at=NOW,
            valid_until=END,
        )
        for p in ("deployment", "classification", "consumer", "freeze")
    )
    doc = AuthorityDocument(
        schema="human-decision-authorities.v1",
        scope=SCOPE,
        installation_id=root.installation_id,
        generation=generation,
        expected_tenant_revision=0,
        operation_id="designate-" + str(generation),
        observed_at=NOW,
        valid_until=END,
        signers=signers,
    )
    auth = SignedAuthorities(document=doc, signature=sign(doc, "root", keys["root"]))
    if material is None:
        repo = Path(__file__).resolve().parents[4]
        process_path = next((repo / "spec/processes/bpmn").glob(pair[0] + "_*.bpmn"))
        proc = artifact(pair[0] + ":1:id", process_path.read_bytes())
        task = next(
            t
            for t in ET.fromstring(process_path.read_bytes()).iter()
            if t.tag.endswith("}userTask") and t.get("id") == pair[1]
        )
        candidate = task.get("{http://camunda.org/schema/1.0/bpmn}candidateGroups")
        dmn = None
        if candidate.startswith("${"):
            dmnraw = (repo / "spec/processes/dmn/escalation_routing.dmn").read_bytes()
            dmn = artifact("escalation-routing:1:id", dmnraw)
            groups = ("plantao-clinico", "enfermagem-triagem", "atendimento-humano")
            domain = GroupDomain(
                kind="dmn",
                groups=groups,
                dmn_definition_id=dmn.artifact_ref,
                dmn_definition_key="escalation_routing",
                dmn_definition_version=1,
                dmn_resource_digest=dmn.digest,
            )
        else:
            groups = tuple(candidate.split(","))
            domain = GroupDomain(
                kind="static",
                groups=groups,
                dmn_definition_id=None,
                dmn_definition_key=None,
                dmn_definition_version=None,
                dmn_resource_digest=None,
            )
        form = artifact(kind, b"synthetic qualified form")
        pin = ArtifactPin(artifact_ref="policy", digest="c" * 64)
        entry = ReadCatalogEntry(
            process_definition_id=proc.artifact_ref,
            process_definition_key=pair[0],
            process_definition_version=1,
            process_definition_digest=proc.digest,
            task_definition_key=pair[1],
            form_key=kind,
            form_version=1,
            form_digest=form.digest,
            form_source_status=_BINDINGS[pair][1],
            allowed_inputs=_INPUTS_BY_FORM[kind],
            required_roles=("auditor",),
            subject_policy=pin,
            consent_policy=pin,
            resource_policy=pin,
            disclosure_policy=pin,
            opaque_task_id_policy=pin,
            group_domain=domain,
        )
        outcome = _OUTCOMES[kind][0]
        material = BindingMaterial(
            entry=entry,
            process=proc,
            group_dmn=dmn,
            form=form,
            classification=Classification(
                contract=artifact("classification", b"synthetic classification"),
                input_fields=entry.allowed_inputs,
                outcome_field=outcome,
                phi_fields=tuple(f for f in entry.allowed_inputs if f != outcome),
            ),
            consumer=ConsumerQualification(
                deployed_consumer=artifact("consumer", b"synthetic consumer bytes"),
                deployment_receipt=artifact(
                    "consumer-deployment-receipt", b"synthetic deployed consumer receipt"
                ),
                hydration_contract=artifact("hydration", b"synthetic exact hydration contract"),
                custody_schema="phi-human-decision-custody.v1",
                mode="phi_case_reader" if kind == "escalation" else "worker_hydration",
            ),
            required_group=groups[0],
        )
    digests = (
        sha(canonical(material.entry) + canonical(material.process) + canonical(material.form)),
        sha(canonical(material.classification)),
        sha(canonical(material.consumer)),
    )
    receipts = []
    for s, digest in zip(signers, digests, strict=False):
        r = Receipt(
            schema="human-decision-qualification-receipt.v1",
            purpose=s.purpose,
            scope=SCOPE,
            installation_id=root.installation_id,
            authority_generation=generation,
            operation_id=operation,
            expected_tenant_revision=expected_revision,
            issuer=s.issuer,
            source_ref=s.purpose + "-source",
            source_revision=4,
            source_digest=digest,
            receipt_ref=s.purpose + "-receipt",
            contract_ref=s.contract_ref,
            contract_digest=s.contract_digest,
            installed_authority_ref=s.installed_authority_ref,
            material_digest=sha(canonical(material)),
            batch=batch or (batch_member(material),),
            receipt_digests=(),
            freeze_epoch=2,
            observed_at=NOW,
            valid_until=END,
        )
        receipts.append(SignedReceipt(receipt=r, signature=sign(r, s.key_id, keys[s.key_id])))
    s = signers[-1]
    ds = tuple(sha(canonical(r)) for r in receipts)
    r = receipts[0].receipt.model_copy(
        update=dict(
            purpose="freeze",
            issuer=s.issuer,
            source_ref="freeze-source",
            source_digest=sha(canonicalize(list(ds))),
            receipt_digests=ds,
            receipt_ref="freeze-receipt",
            contract_ref=s.contract_ref,
            contract_digest=s.contract_digest,
            installed_authority_ref=s.installed_authority_ref,
        )
    )
    freeze = SignedReceipt(receipt=r, signature=sign(r, "freeze", keys["freeze"]))
    packet = QualificationPacket(material=material, receipts=tuple(receipts), freeze=freeze)
    return QualificationVerifier(root), auth, packet, keys


@pytest.mark.parametrize("pair", list(SIX))
def test_six_complete_distinct_signatures(pair):
    verifier, auth, packet, _ = fixture(pair)
    result = verifier.verify(packet, auth, NOW)
    assert result.binding_digest == sha(canonical(packet))
    assert result.expected_tenant_revision == 1
    assert decode(QualificationPacket, canonical(packet)) == packet


@pytest.mark.parametrize(
    "field,value",
    [
        ("purpose", "classification"),
        ("issuer", "other"),
        ("source_digest", "0" * 64),
        ("material_digest", "0" * 64),
        ("installation_id", "other"),
        ("authority_generation", 2),
        ("expected_tenant_revision", 3),
        ("operation_id", "other"),
        ("freeze_epoch", 3),
        ("observed_at", END),
        ("valid_until", NOW),
        ("contract_digest", "0" * 64),
        ("installed_authority_ref", "other"),
    ],
)
def test_tampered_signed_receipt_refuses(field, value):
    verifier, auth, packet, _ = fixture()
    first = packet.receipts[0]
    changed = first.model_copy(update={"receipt": first.receipt.model_copy(update={field: value})})
    packet = packet.model_copy(update={"receipts": (changed, *packet.receipts[1:])})
    with pytest.raises(BindingUnavailableError):
        verifier.verify(packet, auth, NOW)


@pytest.mark.parametrize("index", range(3))
def test_even_authenticated_wrong_source_digest_refuses(index):
    verifier, auth, packet, keys = fixture()
    values = list(packet.receipts)
    r = values[index].receipt.model_copy(update={"source_digest": "0" * 64})
    values[index] = SignedReceipt(receipt=r, signature=sign(r, r.purpose, keys[r.purpose]))
    packet = packet.model_copy(update={"receipts": tuple(values)})
    with pytest.raises(BindingUnavailableError):
        verifier.verify(packet, auth, NOW)


@pytest.mark.parametrize(
    "change",
    [
        {"contract_ref": "timestamp-only"},
        {"contract_digest": "0" * 64},
        {"installed_authority_ref": "other-installed-authority"},
        {"receipt_digests": ()},
    ],
)
def test_signed_freeze_requires_exact_designated_contract_and_receipts(change):
    verifier, auth, packet, keys = fixture()
    r = packet.freeze.receipt.model_copy(update=change)
    packet = packet.model_copy(
        update={"freeze": SignedReceipt(receipt=r, signature=sign(r, "freeze", keys["freeze"]))}
    )
    with pytest.raises(BindingUnavailableError):
        verifier.verify(packet, auth, NOW)


def test_latest_root_designation_removing_signer_revokes_existing_packet():
    verifier, auth, packet, keys = fixture()
    doc = auth.document.model_copy(update={"generation": 1, "signers": auth.document.signers[:-1]})
    latest = SignedAuthorities(document=doc, signature=sign(doc, "root", keys["root"]))
    with pytest.raises(BindingUnavailableError):
        verifier.verify(packet, latest, NOW)


def test_self_signed_root_and_reused_authority_key_refuse():
    verifier, auth, packet, keys = fixture()
    bad = auth.model_copy(update={"signature": sign(auth.document, "root", keys["freeze"])})
    with pytest.raises(BindingUnavailableError):
        verifier.verify(packet, bad, NOW)
    with pytest.raises(ValidationError):
        AuthorityDocument.model_validate(
            auth.document.model_copy(update={"signers": (auth.document.signers[0],) * 2})
        )


@pytest.mark.parametrize("at", [NOW - timedelta(microseconds=1), END, END + timedelta(seconds=1)])
def test_freshness_is_checked_at_each_verification(at):
    verifier, auth, packet, _ = fixture()
    with pytest.raises(BindingUnavailableError):
        verifier.verify(packet, auth, at)


@pytest.mark.parametrize(
    "change",
    [
        {"phi_fields": ()},
        {"outcome_field": "justificativa_clinica"},
        {"input_fields": ()},
    ],
)
def test_cannot_drop_classified_required_basis(change):
    _, _, packet, _ = fixture()
    with pytest.raises(ValidationError):
        BindingMaterial.model_validate(
            packet.material.model_copy(
                update={"classification": packet.material.classification.model_copy(update=change)}
            )
        )


def test_seventh_binding_and_missing_consumer_evidence_refuse():
    _, _, packet, _ = fixture()
    with pytest.raises(ValidationError):
        BindingMaterial.model_validate(
            packet.material.model_copy(update={"required_group": "unqualified-group"})
        )
    raw = canonical(packet).replace(b"UT_AnaliseMedicoAuditor", b"UT_DecidirPendenciaExpirada")
    with pytest.raises(BindingUnavailableError):
        decode(QualificationPacket, raw)
    raw = canonical(packet).replace(b'"deployment_receipt":', b'"missing_deployment_receipt":')
    with pytest.raises(BindingUnavailableError):
        decode(QualificationPacket, raw)
