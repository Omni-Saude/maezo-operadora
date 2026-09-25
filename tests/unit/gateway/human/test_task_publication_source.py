"""H2 (D-N) unit: the real `resource` source against the shared Java vector, and the job's task loop.

The provenance derivation is checked against `tests/fixtures/portal_read/jcs-resource-vector.json`
(the vector the Java provider reads in `HumanAdmissionTest`/`ResourceQualificationJarIT`). The
native rows are a fake of `NativeTaskSource` (its SQL runs in C1 against the real engine).
Synthetic data only.
"""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from tests.unit.gateway.human.test_membership_publication_job import (
    PUBLISHER,
    RECEIPT_DIGEST,
    Clock,
    FakePublisher,
    RowStore,
    _handshake,
)

from maezo.gateway.human.membership_publication_job import (
    MembershipPublicationJob,
    PublicationLedger,
    StaffCatalogConfig,
    StaffCatalogPublicationSource,
    TaskPublication,
)
from maezo.gateway.human.queue import ReadRefusalError
from maezo.gateway.human.read_profile import ResourceProjection, digest, parse_model, wire
from maezo.gateway.human.task_publication_source import (
    EvidenceRow,
    LiveTask,
    TaskAdmission,
    TaskResourceSource,
    catalog_entries,
    evidence_command,
    evidence_ref,
    grants_for,
    resource_projection,
    resource_provenance,
)
from maezo.portal.api.records import MembershipRecord
from maezo.portal.engine.profile import canonicalize, strict_loads

VECTOR = Path(__file__).resolve().parents[3] / "fixtures" / "portal_read" / "jcs-resource-vector.json"
NOW = datetime(2026, 9, 25, 12, tzinfo=UTC)
TENANT = "amh"
PREFIX = "portal-resource:amh:task:"
PROCESS = "SP-OP-ESCALATION-001:1:4"
TASK_KEY = "UT_TratarEscalonamento"
UUID_A = "0f9c2d4e-1a2b-4c3d-8e9f-000000000001"
UUID_B = "0f9c2d4e-1a2b-4c3d-8e9f-000000000002"


def _vector() -> dict[str, Any]:
    return json.loads(VECTOR.read_text(encoding="utf-8"))


def _artifact(name: str) -> dict[str, str]:
    raw = f"SYN {name}".encode()
    return dict(
        artifact_ref=f"SYN-{name}",
        digest=hashlib.sha256(raw).hexdigest(),
        bytes_base64=base64.b64encode(raw).decode(),
    )


def _pin(name: str) -> dict[str, str]:
    a = _artifact(name)
    return dict(artifact_ref=a["artifact_ref"], digest=a["digest"])


POLICIES = (
    "subject-policy",
    "consent-policy",
    "resource-policy",
    "disclosure-policy",
    "opaque-task-id-policy",
)
GROUPS = ["atendimento-humano", "enfermagem-triagem", "plantao-clinico"]


def _catalog() -> bytes:
    form = dict(_artifact("form-escalation"), artifact_ref="escalation")
    entry = dict(
        process_definition_id=PROCESS,
        process_definition_key="SP-OP-ESCALATION-001",
        process_definition_version="1",
        process_definition_digest="1" * 64,
        task_definition_key=TASK_KEY,
        form_key="escalation",
        form_version="1",
        form_digest=form["digest"],
        form_source_status="BPMN_FORMDATA",
        allowed_inputs=["resultado", "notas_resolucao"],
        required_roles=["atendente"],
        subject_policy=_pin("subject-policy"),
        consent_policy=_pin("consent-policy"),
        resource_policy=_pin("resource-policy"),
        disclosure_policy=_pin("disclosure-policy"),
        opaque_task_id_policy=_pin("opaque-task-id-policy"),
        group_domain=dict(
            kind="static",
            groups=GROUPS,
            dmn_definition_id=None,
            dmn_definition_key=None,
            dmn_definition_version=None,
            dmn_resource_digest=None,
        ),
    )
    return canonicalize(
        dict(
            schema="portal-read-catalog.v1",
            catalog_ref="catalog-staff",
            publisher_ref=PUBLISHER,
            entries=[entry],
            policies=[_artifact(p) for p in POLICIES],
            forms=[form],
            deployment_receipt_ref="deployment-1",
            deployment_receipt_digest=RECEIPT_DIGEST,
        )
    )


def _classification() -> dict[str, str]:
    return dict(
        classification_ref="SYN-classification-escalation",
        classification_digest="4" * 64,
        policy_ref=_pin("disclosure-policy")["artifact_ref"],
        policy_digest=_pin("disclosure-policy")["digest"],
        projection="full_task_detail.v1",
        fields_digest="5" * 64,
    )


def _admission(catalog: bytes, **changes: Any) -> bytes:
    value = dict(
        schema="portal-read-admission.v1",
        admission_ref="admission-amh",
        admission_revision="2",
        scope=dict(tenant=TENANT, environment="dev", workload_ref=PUBLISHER),
        catalog=dict(
            catalog_ref="catalog-staff",
            publisher_ref=PUBLISHER,
            catalog_digest=hashlib.sha256(catalog).hexdigest(),
        ),
        publishers=[
            dict(
                kind="membership",
                publisher_ref=PUBLISHER,
                source_ref_prefix="portal-identity:amh:membership:",
            ),
            dict(
                kind="catalog-designate",
                publisher_ref=PUBLISHER,
                source_ref_prefix="portal-read-catalog:amh:",
            ),
            dict(kind="resource", publisher_ref=PUBLISHER, source_ref_prefix=PREFIX),
        ],
        human=dict(
            entries=[
                dict(
                    process_definition_id=PROCESS,
                    task_definition_key=TASK_KEY,
                    classification=_classification(),
                    identity_policy=_pin("opaque-task-id-policy"),
                    task_id_format="uuid",
                    candidate_groups=GROUPS,
                    user_candidates="refused",
                )
            ]
        ),
        observation_seconds="600",
        valid_until="2026-10-01T00:00:00.000000Z",
    )
    value.update(changes)
    return canonicalize(value)


def _member(name: str, group: str, **changes: Any) -> MembershipRecord:
    value = dict(
        tenant=TENANT,
        issuer="https://issuer.example",
        subject=f"subject-{name}",
        principal_ref=f"staff-{name}",
        revision=1,
        audience="staff",
        memberships=({"membership_ref": f"{name}-m1", "roles": ("atendente",), "groups": (group,)},),
        subject_bindings=(),
        reviewed_until=NOW + timedelta(days=7),
    )
    value.update(changes)
    return MembershipRecord.model_validate(value)


# --- contract with the Java provider (the vector) -----------------------------------------


@pytest.mark.parametrize("name", ["complete-no-grant", "complete-with-grant", "revoked"])
def test_provenance_is_derived_exactly_as_the_java_provider_expects(name: str) -> None:
    vector = _vector()
    case = next(c for c in vector["cases"] if c["name"] == name)
    payload = parse_model(ResourceProjection, strict_loads(case["payload_jcs"].encode()))
    assert canonicalize(wire(payload)).decode() == case["payload_jcs"]  # payload = JCS(wire(...))
    admission = TaskAdmission(
        tenant=vector["tenant"],
        publisher_ref="portal-staff",
        source_ref_prefix=vector["source_ref_prefix"],
        observation_seconds=int(vector["observation_seconds"]),
        valid_until=NOW + timedelta(days=1),
        catalog_digest="0" * 64,
        entries={},
    )
    observed = datetime.fromisoformat(case["source"]["observed_at"].replace("Z", "+00:00"))
    got = wire(
        resource_provenance(payload, tenant=vector["tenant"], admission=admission, observed_at=observed)
    )
    assert {k: got[k] for k in case["source"]} == case["source"]
    assert digest(payload) == case["source_digest"]


def test_projection_is_the_admitted_classification_with_unique_refs_per_task() -> None:
    catalog = _catalog()
    admission = TaskAdmission.from_record(_admission(catalog))
    entry = catalog_entries(catalog, admission)[(PROCESS, TASK_KEY)]
    human = admission.entries[(PROCESS, TASK_KEY)]
    until = NOW + timedelta(hours=6)
    members = [_member("a", "atendimento-humano"), _member("b", "enfermagem-triagem")]
    payloads = []
    for task_id in (UUID_A, UUID_B):
        task = LiveTask(task_id, 3, PROCESS, TASK_KEY)
        evidence = EvidenceRow(9, evidence_ref(TENANT, task_id), "e" * 64, int(until.timestamp()), PROCESS)
        grants = grants_for(members, entry, tenant=TENANT, task_id=task_id, now=NOW, valid_until=until)
        payloads.append(
            resource_projection(
                tenant=TENANT,
                task=task,
                entry=entry,
                human=human,
                evidence=evidence,
                grants=grants,
                resource_revision=1,
                valid_until=until,
            )
        )
    a, b = payloads
    assert wire(a.classification) == dict(_classification(), valid_until="2026-09-25T18:00:00.000000Z")
    assert parse_model(ResourceProjection, wire(a)) == a  # closed profile round trip
    # unique per task: evidence ref and every decision receipt (continuity ceilings, H1 README)
    assert a.evidence_ref != b.evidence_ref
    refs_a = {g.decision_receipt_ref for g in a.positive_grants}
    refs_b = {g.decision_receipt_ref for g in b.positive_grants}
    assert len(refs_a) == 2 and not refs_a & refs_b


def test_grants_only_live_staff_with_a_required_role_and_a_domain_group() -> None:
    catalog = _catalog()
    admission = TaskAdmission.from_record(_admission(catalog))
    entry = catalog_entries(catalog, admission)[(PROCESS, TASK_KEY)]
    until = NOW + timedelta(hours=6)
    records = [
        _member("in", "atendimento-humano"),
        _member("other-group-in-domain", "plantao-clinico"),
        _member("outside-domain", "financeiro"),
        _member("revoked", "atendimento-humano", revoked=True),
        _member("expired", "atendimento-humano", reviewed_until=NOW - timedelta(seconds=1)),
        _member("foreign", "atendimento-humano", tenant="outro"),
        _member(
            "no-role",
            "atendimento-humano",
            memberships=({"membership_ref": "m", "roles": ("leitor",), "groups": ("atendimento-humano",)},),
        ),
    ]
    grants = grants_for(records, entry, tenant=TENANT, task_id=UUID_A, now=NOW, valid_until=until)
    assert [g.principal_ref for g in grants] == ["staff-in", "staff-other-group-in-domain"]
    assert all(g.valid_until == until and g.consent_scopes == () for g in grants)


def test_evidence_command_is_the_authority_shape_the_engine_accepts() -> None:
    catalog = _catalog()
    admission = TaskAdmission.from_record(_admission(catalog))
    entry = catalog_entries(catalog, admission)[(PROCESS, TASK_KEY)]
    raw = evidence_command(
        tenant=TENANT,
        workload_ref=PUBLISHER,
        expected_revision=4,
        task=LiveTask(UUID_A, 1, PROCESS, TASK_KEY),
        entry=entry,
        catalog_digest=admission.catalog_digest,
        valid_until=NOW + timedelta(hours=6),
    )
    value = strict_loads(raw)
    # `AuthorityCommand.java` `case "evidence"`: exactly these keys (Jcs.keys)
    assert set(value) == {
        "schema",
        "tenant",
        "workload_ref",
        "operation",
        "expected_revision",
        "task_id",
        "process_definition_id",
        "evidence_ref",
        "evidence_digest",
        "valid_until",
    }
    assert value["operation"] == "evidence" and value["expected_revision"] == "4"
    assert value["evidence_ref"] == f"portal-evidence:amh:task:{UUID_A}"


# --- admission and catalog ------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        lambda c: _admission(c).replace(b",", b", ", 1),  # not JCS
        lambda c: _admission(c, human=dict(entries=[])),
        lambda c: _admission(c, publishers=[]),
        lambda c: _admission(c, schema="portal-read-admission.v2"),
    ],
)
def test_admission_without_an_admitted_human_block_refuses(raw: Any) -> None:
    with pytest.raises(ReadRefusalError):
        TaskAdmission.from_record(raw(_catalog()))


def test_task_id_format_is_the_admitted_one() -> None:
    admission = TaskAdmission.from_record(_admission(_catalog()))
    human = admission.entries[(PROCESS, TASK_KEY)]
    assert admission.task_id_ok(human, UUID_A)
    assert not admission.task_id_ok(human, "10001")  # the standalone IT format, not the dev engine's


def test_catalog_must_be_the_admitted_one_with_exactly_the_admitted_tasks() -> None:
    catalog = _catalog()
    admission = TaskAdmission.from_record(_admission(catalog))
    with pytest.raises(ReadRefusalError):
        catalog_entries(catalog + b" ", admission)
    other = json.loads(_admission(catalog))
    other["human"]["entries"][0]["task_definition_key"] = "UT_Outra"
    with pytest.raises(ReadRefusalError):
        catalog_entries(catalog, TaskAdmission.from_record(canonicalize(other)))


@pytest.mark.asyncio
async def test_staged_snapshot_is_read_exactly_once_and_dies_with_its_window() -> None:
    clock = Clock()
    clock.now = NOW
    catalog = _catalog()
    admission = TaskAdmission.from_record(_admission(catalog))
    entry = catalog_entries(catalog, admission)[(PROCESS, TASK_KEY)]
    until = NOW + timedelta(hours=1)
    payload = resource_projection(
        tenant=TENANT,
        task=LiveTask(UUID_A, 1, PROCESS, TASK_KEY),
        entry=entry,
        human=admission.entries[(PROCESS, TASK_KEY)],
        evidence=EvidenceRow(1, evidence_ref(TENANT, UUID_A), "e" * 64, int(until.timestamp()), PROCESS),
        grants=(),
        resource_revision=1,
        valid_until=until,
    )
    source = TaskResourceSource(clock=clock)
    source.stage(resource_provenance(payload, tenant=TENANT, admission=admission, observed_at=NOW), payload)
    snapshot = await source.read(UUID_A)
    snapshot.lease.live()
    with pytest.raises(ReadRefusalError):
        await source.read(UUID_A)  # never replayed
    clock.now = NOW + timedelta(seconds=600)
    with pytest.raises(ReadRefusalError):
        snapshot.lease.live()


# --- the job's task loop ------------------------------------------------------------------


class FakeNative:
    """`NativeTaskSource` over dicts: the engine bumps the task revision on every publication."""

    def __init__(self, publisher: TaskPublisher) -> None:
        self.publisher = publisher
        self.tasks: dict[str, LiveTask] = {}
        self.evidence_rows: dict[str, EvidenceRow] = {}

    async def authority_revision(self) -> int:
        return self.publisher.revision

    async def live_tasks(self, keys: Any) -> list[LiveTask]:
        return [t for _, t in sorted(self.tasks.items()) if t.task_definition_key in set(keys)]

    async def task(self, task_id: str) -> LiveTask | None:
        return self.tasks.get(task_id)

    async def evidence(self, task_id: str) -> EvidenceRow | None:
        return self.evidence_rows.get(task_id)

    def bump(self, task_id: str) -> None:
        t = self.tasks[task_id]
        self.tasks[task_id] = LiveTask(
            t.task_id, t.revision + 1, t.process_definition_id, t.task_definition_key
        )


class TaskPublisher(FakePublisher):
    def __init__(self) -> None:
        super().__init__()
        self.native: FakeNative | None = None
        self.source: TaskResourceSource | None = None
        self.resources: list[dict[str, Any]] = []

    async def publish_evidence(self, raw):
        command = json.loads(raw)
        assert command["operation"] == "evidence"
        if int(command["expected_revision"]) != self.revision:
            raise ReadRefusalError("read_dependency_unavailable")
        self.revision += 1
        self.calls.append(("evidence", command["task_id"]))
        assert self.native is not None
        self.native.evidence_rows[command["task_id"]] = EvidenceRow(
            self.revision,
            command["evidence_ref"],
            command["evidence_digest"],
            int(command["valid_until"]),
            command["process_definition_id"],
        )
        self.native.bump(command["task_id"])
        return {
            "schema": "human-authority-receipt.v1",
            "tenant": command["tenant"],
            "revision": str(self.revision),
            "digest": hashlib.sha256(raw).hexdigest(),
        }

    async def publish_resource(self, task_id, *, expected_revision):
        assert self.source is not None and self.native is not None
        snapshot = await self.source.read(task_id)
        snapshot.lease.live()
        live = self.native.tasks[task_id]
        # `PortalReadPublication` `case "resource"`: exact revision, evidence and definition
        if (
            snapshot.payload.observed_task_revision != live.revision
            or snapshot.payload.evidence_revision != self.native.evidence_rows[task_id].revision
        ):
            raise ReadRefusalError("read_dependency_unavailable")
        self.resources.append(dict(source=wire(snapshot.source), payload=wire(snapshot.payload)))
        self.calls.append(("resource", task_id))
        self.native.bump(task_id)
        return self._receipt("resource", expected_revision)


def _task_job(rows: RowStore, publisher: TaskPublisher, path: Path, clock: Clock) -> MembershipPublicationJob:
    catalog = _catalog()
    admission = TaskAdmission.from_record(_admission(catalog))
    catalog_file = path.parent / "catalog.json"
    catalog_file.write_bytes(catalog)
    config = StaffCatalogConfig.model_validate(
        dict(
            catalog_ref="catalog-staff",
            catalog_revision=1,
            admitted_catalog_digest=admission.catalog_digest,
            deployment_receipt_ref="deployment-1",
            deployment_receipt_digest=RECEIPT_DIGEST,
            source_ref_prefix="portal-read-catalog:amh:",
            valid_seconds=86400,
            artifact_file=str(catalog_file),
        )
    )
    staff_catalog = StaffCatalogPublicationSource(config=config, publisher_ref=PUBLISHER, clock=clock)
    if publisher.native is None:
        publisher.native = FakeNative(publisher)
    publisher.source = TaskResourceSource(clock=clock)
    tasks = TaskPublication(
        native=publisher.native,  # type: ignore[arg-type]
        admission=admission,
        entries=catalog_entries(staff_catalog.raw, admission),
        source=publisher.source,
    )
    job = MembershipPublicationJob(
        publisher=publisher,
        ledger=PublicationLedger(path, "amh"),
        catalog=staff_catalog,
        handshake=_handshake(rows.store, clock=clock, seconds=600),
        store=rows.store,
        engine=None,  # type: ignore[arg-type]
        workload_ref=PUBLISHER,
        clock=clock,
        counter=publisher.native.authority_revision,
        tasks=tasks,
    )

    async def principals():
        return sorted(rows.rows)

    job._principals = principals  # type: ignore[method-assign]
    return job


def _rows() -> RowStore:
    return RowStore(_member("a", "atendimento-humano"), _member("b", "enfermagem-triagem"))


@pytest.mark.asyncio
async def test_job_publishes_evidence_then_resource_and_is_idempotent_by_content(tmp_path) -> None:
    clock, publisher, rows, path = Clock(), TaskPublisher(), _rows(), tmp_path / "l.json"
    clock.now = NOW
    first_job = _task_job(rows, publisher, path, clock)
    publisher.native.tasks[UUID_A] = LiveTask(UUID_A, 1, PROCESS, TASK_KEY)  # type: ignore[union-attr]
    publisher.native.tasks["10001"] = LiveTask("10001", 1, PROCESS, TASK_KEY)  # type: ignore[union-attr]
    first = await first_job.run()
    assert (first.evidence_published, first.tasks_published) == (1, 1)  # the decimal id is not admitted
    assert publisher.calls[-2:] == [("evidence", UUID_A), ("resource", UUID_A)]
    published = publisher.resources[-1]
    assert published["source"]["source_ref"] == PREFIX + UUID_A
    assert (
        published["source"]["receipt_ref"]
        == f"portal-resource:amh:task:{UUID_A}@{published['payload']['resource_revision']}"
    )
    assert [g["principal_ref"] for g in published["payload"]["positive_grants"]] == ["staff-a", "staff-b"]
    # the resource bumped the task revision; nothing else changed: nothing is republished
    clock.now = NOW + timedelta(seconds=60)
    second = await _task_job(rows, publisher, path, clock).run()
    assert (second.evidence_published, second.tasks_published, second.tasks_unchanged) == (0, 0, 1)


@pytest.mark.asyncio
async def test_job_renews_the_resource_at_half_its_observation_with_a_higher_revision(tmp_path) -> None:
    clock, publisher, rows, path = Clock(), TaskPublisher(), _rows(), tmp_path / "l.json"
    clock.now = NOW
    job = _task_job(rows, publisher, path, clock)
    publisher.native.tasks[UUID_A] = LiveTask(UUID_A, 1, PROCESS, TASK_KEY)  # type: ignore[union-attr]
    await job.run()
    before = int(publisher.resources[-1]["payload"]["resource_revision"])
    clock.now = NOW + timedelta(seconds=300)
    renewed = await _task_job(rows, publisher, path, clock).run()
    assert renewed.tasks_published == 1
    assert int(publisher.resources[-1]["payload"]["resource_revision"]) > before


@pytest.mark.asyncio
async def test_a_membership_change_republishes_the_resource_grants(tmp_path) -> None:
    clock, publisher, rows, path = Clock(), TaskPublisher(), _rows(), tmp_path / "l.json"
    clock.now = NOW
    job = _task_job(rows, publisher, path, clock)
    publisher.native.tasks[UUID_A] = LiveTask(UUID_A, 1, PROCESS, TASK_KEY)  # type: ignore[union-attr]
    await job.run()
    b = _member("b", "enfermagem-triagem")
    rows.rows[(b.issuer, b.subject)] = b.model_copy(update=dict(revoked=True, revision=2))
    clock.now = NOW + timedelta(seconds=30)
    again = await _task_job(rows, publisher, path, clock).run()
    assert again.tasks_published == 1
    assert [g["principal_ref"] for g in publisher.resources[-1]["payload"]["positive_grants"]] == ["staff-a"]


@pytest.mark.asyncio
async def test_tasks_need_the_counter_and_the_admitted_catalog(tmp_path) -> None:
    publisher, rows, path, clock = TaskPublisher(), _rows(), tmp_path / "l.json", Clock()
    job = _task_job(rows, publisher, path, clock)
    with pytest.raises(ReadRefusalError):
        MembershipPublicationJob(
            publisher=publisher,
            ledger=PublicationLedger(path, "amh"),
            catalog=job._catalog,
            handshake=job._handshake,
            store=rows.store,
            engine=None,  # type: ignore[arg-type]
            workload_ref=PUBLISHER,
            tasks=job._tasks,  # without `counter`
        )
