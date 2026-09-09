"""Offline proposal controls; no engine, grants or acquisition effects are simulated."""

from __future__ import annotations

import asyncio
import json
import xml.etree.ElementTree as ET
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from maezo.gateway.engine_contracts import (
    EngineCapabilityError,
    EngineOperation,
    FieldOrigin,
    ValueKind,
)
from maezo.gateway.engine_programa_schemas import (
    PROGRAMA_SCHEMA_PROPOSALS,
    PROGRAMA_WORKER_SCHEMAS,
    programa_schema_proposal,
)
from maezo.gateway.engine_schemas import registered_schema, schema_by_id, worker_lifecycle_schema
from maezo.tools.workers import programa
from maezo.tools.workers.harness import WorkerBpmnError

ROOT = Path(__file__).resolve().parents[3]
PREFIX = "operadora.programa."
TOPICS = {
    PREFIX + name
    for name in (
        "check_consent",
        "build_care_plan",
        "register_program_discharge",
        "stratify_risk",
        "stop_processing",
        "proactive_contact",
        "notify_sla_risk",
    )
}
LIFECYCLE = (
    EngineOperation.FETCH_LOCK,
    EngineOperation.FAILURE,
    EngineOperation.EXTEND_LOCK,
    EngineOperation.UNLOCK,
)


def proposal(name):
    return next(w for w in PROGRAMA_WORKER_SCHEMAS if w.complete.topic == PREFIX + name)


def test_real_registration_and_bpmn_consumers_are_exactly_the_finite_family():
    class RegistrationRecorder:
        def __init__(self):
            self.topics = []

        def register(self, topic, handler):
            self.topics.append(topic)

        def register_worker(self, worker):
            self.topics.append(worker.topic)

    recorder = RegistrationRecorder()
    programa.register_programa_workers(recorder)
    assert len(recorder.topics) == len(set(recorder.topics)) == 7
    assert set(recorder.topics) == TOPICS
    tree = ET.parse(ROOT / "spec/processes/bpmn/SP-OP-PROGRAMA-001_Programas_Cuidado.bpmn")
    attr = "{http://camunda.org/schema/1.0/bpmn}topic"
    tasks = [node for node in tree.iter() if node.get(attr, "").startswith(PREFIX)]
    assert {node.attrib[attr] for node in tasks} == TOPICS
    assert len(tasks) == 8  # build_care_plan is consumed by two actual activities.
    for node in tasks:
        row = programa_schema_proposal(node.attrib[attr], EngineOperation.COMPLETE)
        assert any(source.endswith("#" + node.attrib["id"]) for source in row.sources)


def test_bpmn_error_is_exactly_the_caught_check_consent_error():
    tree = ET.parse(ROOT / "spec/processes/bpmn/SP-OP-PROGRAMA-001_Programas_Cuidado.bpmn")
    ns = {"b": "http://www.omg.org/spec/BPMN/20100524/MODEL"}
    errors = {e.attrib["id"]: e.attrib["errorCode"] for e in tree.findall("b:error", ns)}
    boundaries = tree.findall(".//b:boundaryEvent", ns)
    caught = [
        errors[e.attrib["errorRef"]]
        for b in boundaries
        if b.get("attachedToRef") == "ST_CheckConsent"
        for e in b.findall("b:errorEventDefinition", ns)
    ]
    assert caught == ["ERR_PROGRAMA_NO_CONSENT"]
    row = programa_schema_proposal(PREFIX + "check_consent", EngineOperation.BPMN_ERROR, error_code=caught[0])
    assert row.error_codes == tuple(caught)
    assert row.fields == ()
    row.validate({})
    with pytest.raises(EngineCapabilityError):
        row.validate({"consent_scope": "untrusted error text"})
    with pytest.raises(WorkerBpmnError):
        programa.check_consent({})


@pytest.mark.parametrize("worker", PROGRAMA_WORKER_SCHEMAS, ids=lambda w: w.complete.topic)
def test_structural_inputs_preserve_absence_and_reject_extra_and_wrong_types(worker):
    worker.validate_inputs({})  # Guard execution/disposition remains a separate consumer gate.
    data = {
        f.name: json.loads(f.fixed_json)
        if f.fixed_json is not None
        else False
        if f.kind is ValueKind.BOOLEAN
        else "synthetic"
        for f in worker.input_fields
    }
    worker.validate_inputs(data)
    for field in worker.input_fields:
        with pytest.raises(EngineCapabilityError):
            worker.validate_inputs({**data, field.name: {"value": "synthetic", "type": "String"}})
        with pytest.raises(EngineCapabilityError):
            worker.validate_inputs({**data, field.name: None})
    for key in ("cpf", "nome", "clinical_notes", "worker_id", "acquisition_ref", "*"):
        with pytest.raises(EngineCapabilityError):
            worker.validate_inputs({**data, key: "synthetic"})


@pytest.mark.parametrize("worker", PROGRAMA_WORKER_SCHEMAS, ids=lambda w: w.complete.topic)
@pytest.mark.parametrize("operation", LIFECYCLE)
def test_lifecycle_is_same_topic_and_projection_without_writes_or_error_authority(worker, operation):
    row = programa_schema_proposal(worker.complete.topic, operation)
    assert row.process_key == "SP-OP-PROGRAMA-001"
    assert row.read_projection == tuple(f.name for f in worker.input_fields)
    assert row.fields == row.error_codes == ()
    row.validate({})
    with pytest.raises(EngineCapabilityError):
        row.validate({"consentimento_ativo": True})
    with pytest.raises(EngineCapabilityError):
        programa_schema_proposal(worker.complete.topic, operation, error_code="ERR_PROGRAMA_NO_CONSENT")


@pytest.mark.parametrize("name", ["stratify_risk", "proactive_contact", "register_program_discharge"])
def test_unmodeled_guard_stays_incident_not_bpmn_error(name):
    if name == "register_program_discharge":
        with pytest.raises(programa.ProgramaError):
            programa.register_program_discharge({})
    else:
        factory = getattr(programa, f"make_{name}_handler")
        with pytest.raises(ValueError):
            asyncio.run(factory(None)(SimpleNamespace(variables={})))
    with pytest.raises(EngineCapabilityError):
        programa_schema_proposal(
            PREFIX + name, EngineOperation.BPMN_ERROR, error_code="ERR_PROGRAMA_NO_CONSENT"
        )
    with pytest.raises(EngineCapabilityError):
        programa_schema_proposal(
            PREFIX + name, EngineOperation.BPMN_ERROR, error_code="ERR_PROGRAM_DISCHARGE_NOT_HUMAN"
        )


@pytest.mark.parametrize(
    "name,function,data",
    [
        ("check_consent", programa.check_consent, {"consentimento_ativo": True, "consent_checked": True}),
        ("build_care_plan", programa.enroll_beneficiario, {}),
        ("stratify_risk", programa.stratify_risk, {"consentimento_ativo": True, "consent_checked": True}),
        ("stop_processing", programa.stop_processing, {}),
        (
            "proactive_contact",
            programa.proactive_contact,
            {"consentimento_ativo": True, "consent_checked": True},
        ),
        ("notify_sla_risk", programa.notify_sla_risk, {}),
        (
            "register_program_discharge",
            programa.register_program_discharge,
            {
                "decisao_programa": "DESLIGAR_CLINICO",
                "motivo_desligamento_clinico": "synthetic",
                "referencia_clinica": "synthetic",
                "responsavel_clinico_id": "synthetic",
            },
        ),
    ],
)
def test_actual_worker_return_is_preserved_structurally(name, function, data):
    worker = proposal(name)
    worker.validate_inputs(data)
    result = function(data)
    worker.complete.validate(result)
    with pytest.raises(EngineCapabilityError):
        worker.complete.validate({**result, "enrollment_realizado": True})
    with pytest.raises(EngineCapabilityError):
        worker.complete.validate({**result, "decisao_programa": "DESLIGAR_CLINICO"})
    for field in worker.complete.fields:
        if field.required:
            with pytest.raises(EngineCapabilityError):
                worker.complete.validate({k: v for k, v in result.items() if k != field.name})
        if field.fixed_json is not None:
            with pytest.raises(EngineCapabilityError):
                worker.complete.validate(
                    {**result, field.name: False if field.kind is ValueKind.BOOLEAN else "invented"}
                )


@pytest.mark.parametrize(
    "name,key", [("build_care_plan", "enrollment_gap"), ("proactive_contact", "contato_gap")]
)
def test_gap_optionality_is_preserved_without_inventing_success(name, key):
    row = proposal(name).complete
    row.validate({})
    with pytest.raises(EngineCapabilityError):
        row.validate({key: None})


def test_proposals_cannot_be_promoted_through_the_existing_registry():
    assert len(PROGRAMA_SCHEMA_PROPOSALS) == 36
    assert len({r.schema_id for r in PROGRAMA_SCHEMA_PROPOSALS}) == 36
    assert len({(r.topic, r.operation) for r in PROGRAMA_SCHEMA_PROPOSALS}) == 36
    for row in PROGRAMA_SCHEMA_PROPOSALS:
        assert not registered_schema(row)
        with pytest.raises(EngineCapabilityError):
            schema_by_id(row.schema_id)
        with pytest.raises(EngineCapabilityError):
            worker_lifecycle_schema(row, EngineOperation.FETCH_LOCK)
    with pytest.raises(FrozenInstanceError):
        PROGRAMA_WORKER_SCHEMAS[0].input_fields = ()


@pytest.mark.parametrize(
    "topic,operation,error",
    [
        (PREFIX + "monitor_programa", EngineOperation.COMPLETE, ""),
        ("operadora.events.publish", EngineOperation.COMPLETE, ""),
        (PREFIX + "*", EngineOperation.FETCH_LOCK, ""),
        (PREFIX + "check_consent", EngineOperation.START, ""),
        (PREFIX + "check_consent", EngineOperation.BPMN_ERROR, "ERR_PROGRAM_DISCHARGE_NOT_HUMAN"),
        (PREFIX + "check_consent", "external_complete", ""),
        (None, EngineOperation.COMPLETE, ""),
        (PREFIX + "check_consent", EngineOperation.COMPLETE, None),
    ],
)
def test_unknown_or_malformed_selection_refuses(topic, operation, error):
    with pytest.raises(EngineCapabilityError):
        programa_schema_proposal(topic, operation, error_code=error)


def test_clinical_inputs_and_undeclared_outputs_remain_explicitly_unqualified():
    worker = proposal("register_program_discharge")
    names = {f.name for f in worker.input_fields if f.origin is FieldOrigin.PRIOR_HUMAN}
    assert names == {
        "decisao_programa",
        "motivo_desligamento_clinico",
        "referencia_clinica",
        "responsavel_clinico_id",
    }
    assert names <= {name for name, reason in worker.unqualified_fields if reason}
    assert "risco_estratificado_origem" in dict(proposal("stratify_risk").unqualified_fields)
    assert {"motivo", "processamento_parado"} <= dict(proposal("stop_processing").unqualified_fields).keys()
    # Origin metadata neither authenticates input nor grants a clinical write.
    assert not registered_schema(replace(worker.complete, fields=worker.input_fields))


@pytest.mark.parametrize("value", ["true", 1, 0, [], {}, None])
def test_consent_boolean_never_coerces_truthy_values(value):
    with pytest.raises(EngineCapabilityError):
        proposal("check_consent").validate_inputs({"consent_checked": value})


def test_scope_is_only_the_contract_scope_and_not_arbitrary_text():
    worker = proposal("check_consent")
    worker.validate_inputs({"consent_scope": "programa_cuidado"})
    with pytest.raises(EngineCapabilityError):
        worker.validate_inputs({"consent_scope": "another_scope"})
