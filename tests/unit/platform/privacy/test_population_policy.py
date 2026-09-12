"""Offline R117/R228 boundary controls. Ratified data/verifier are SYNTHETIC ONLY."""

from __future__ import annotations

import ast
import hashlib
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import yaml

from maezo.agents.andre.graph import CohortAggregate
from maezo.gateway.seams import population
from maezo.gateway.seams._base import SeamContext
from maezo.platform.privacy import population_policy as policy
from maezo.platform.privacy.program_publication import PROGRAM_EVENT_FIELDS
from maezo.tools.workers import events, programa
from maezo.tools.workers.harness import ExternalTask

ROOT = Path(__file__).resolve().parents[4]


def document():
    return dict(
        schema_version=1,
        policy_id=policy.POLICY_ID,
        status="RATIFICADO",
        k_min=7,
        allowed_metrics=["synthetic_metric"],
        ratificacao=dict(
            ratificado=True,
            revisor="synthetic-reviewer",
            ratificado_em="2026-09-09",
            evidence_ref="fixture-only",
        ),
    )


def raw_document():
    return yaml.safe_dump(document()).encode()


def fixture_verifier(identity, digest, evidence):
    return (identity, digest, evidence) == (
        policy.POLICY_ID,
        hashlib.sha256(raw_document()).hexdigest(),
        "fixture-only",
    )


def authorized():
    return policy.parse_verified_policy(raw_document(), fixture_verifier)


def task(variables):
    return ExternalTask(
        task_id="test",
        topic="operadora.events.publish",
        process_instance_id="pi",
        business_key="PROG-tenant-program-pseudo-cycle",
        worker_id="unit",
        variables=variables,
        # All this helper's publications use the completed activity contract.
        process_definition_key="SP-OP-PROGRAMA-001",
        activity_id="ST_PublishCompleted",
    )


@pytest.mark.parametrize("key", ["coorte_metricas", "resultados", "utilizacao", "arbitrary"])
async def test_opaque_aggregate_never_reaches_sink(key):
    sink = SimpleNamespace(publish=AsyncMock(return_value=True))
    with pytest.raises(ValueError):
        await events.make_publish_event_handler(sink)(
            task(
                dict(
                    event_topic="agents.events.programa.completed",
                    event_payload_vars=key,
                    **{key: {"cohort_size": 1, "k_anonymity": 1}},
                    documentation="k_min maezo.population-egress.v1",
                    k_min=999,
                )
            )
        )
    sink.publish.assert_not_awaited()


@pytest.mark.parametrize("key", ["tenant_id", "desfecho", "programa_id"])
async def test_nested_aggregate_cannot_hide_in_existing_slot(key):
    sink = SimpleNamespace(publish=AsyncMock(return_value=True))
    with pytest.raises(ValueError):
        await events.make_publish_event_handler(sink)(
            task(
                dict(
                    event_topic="agents.events.programa.completed",
                    event_payload_vars=key,
                    **{key: {"resultados": 1}},
                )
            )
        )
    sink.publish.assert_not_awaited()


async def test_ordinary_program_event_unchanged():
    sink = SimpleNamespace(publish=AsyncMock(return_value=True))
    result = await events.make_publish_event_handler(sink)(
        task(
            dict(
                event_topic="agents.events.programa.completed",
                event_payload_vars="tenant_id,contato_gap",
                tenant_id="tenant",
                contato_gap="contato_beneficiario_nao_ligado",
                event_desfecho="enrollment_realizado",
            )
        )
    )
    assert result["event_published"] is True
    assert sink.publish.call_args.args[1]["contato_gap"] == "contato_beneficiario_nao_ligado"


def test_shipped_human_fields_are_blank():
    data = yaml.safe_load(policy.CANONICAL_PATH.read_bytes())
    assert data["policy_id"] == "maezo.population-egress.v1"
    assert data["status"] == "DRAFT"
    assert data["k_min"] is None and data["allowed_metrics"] is None
    assert data["ratificacao"] == dict(ratificado=False, revisor=None, ratificado_em=None, evidence_ref=None)
    with pytest.raises(policy.PopulationPolicyUnavailableError):
        policy.load_population_policy()


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.update(k_min=None),
        lambda d: d.update(k_min=True),
        lambda d: d.update(k_min="7"),
        lambda d: d.update(k_min=0),
        lambda d: d.update(status="DRAFT"),
        lambda d: d.update(policy_id="unowned.v1"),
        lambda d: d["ratificacao"].update(ratificado=False),
        lambda d: d["ratificacao"].update(revisor=""),
        lambda d: d["ratificacao"].update(ratificado_em="2026-02-30"),
        lambda d: d["ratificacao"].update(evidence_ref=None),
        lambda d: d.update(allowed_metrics=None),
    ],
)
def test_invalid_policy_refused_before_authentication(mutate):
    data = document()
    mutate(data)
    calls = []
    with pytest.raises(policy.PopulationPolicyUnavailableError):
        policy.parse_verified_policy(yaml.safe_dump(data).encode(), lambda *a: calls.append(a) or True)
    assert calls == []


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"{",
        b"min_cell_size: 7",
        b"k_min: 7\nk_min: 8",
        b"\xff",
        raw_document() + b"k_min: 8\n",
        raw_document().replace(b"  ratificado: true", b"  ratificado: false\n  ratificado: true"),
    ],
)
def test_malformed_unsigned_duplicate_documents_refused(raw):
    with pytest.raises(policy.PopulationPolicyUnavailableError):
        policy.parse_verified_policy(raw, fixture_verifier)


def test_metadata_never_authenticates_human_and_digest_is_bound():
    with pytest.raises(policy.PopulationPolicyUnavailableError):
        policy.parse_verified_policy(raw_document(), None)
    with pytest.raises(policy.PopulationPolicyUnavailableError):
        policy.parse_verified_policy(raw_document() + b"# changed bytes", fixture_verifier)
    assert authorized().k_min == 7


@pytest.mark.parametrize("method", ["population_metrics", "actuarial_risk"])
async def test_unratified_population_makes_zero_provider_calls(method):
    inner = SimpleNamespace(**{method: AsyncMock()})
    client = population.gate_population(inner, SeamContext(tenant="tenant", principal="andre"))
    with pytest.raises(policy.PopulationPolicyUnavailableError):
        await getattr(client, method)("cohort-test", features=["synthetic_metric"])
    getattr(inner, method).assert_not_awaited()


@pytest.mark.parametrize("method", ["population_metrics", "actuarial_risk"])
@pytest.mark.parametrize("k", [6, 7])
async def test_authorized_population_checks_return_before_consumer(monkeypatch, method, k):
    monkeypatch.setattr(population, "load_population_policy", authorized)
    result = CohortAggregate(
        cohort_id="cohort-test",
        dataset_ref="lake://synthetic",
        metrics={"synthetic_metric": 2.0},
        cohort_size=7,
        k_anonymity=k,
    )
    inner = SimpleNamespace(**{method: AsyncMock(return_value=result)})
    consumer = AsyncMock()
    client = population.gate_population(inner, SeamContext(tenant="tenant", principal="andre"))
    if k == 6:
        with pytest.raises(policy.PopulationPolicyUnavailableError):
            await consumer(await getattr(client, method)("cohort-test", features=["synthetic_metric"]))
        consumer.assert_not_awaited()
    else:
        await consumer(await getattr(client, method)("cohort-test", features=["synthetic_metric"]))
        consumer.assert_awaited_once_with(result)


def test_event_inventory_is_exact_bpmn_contract():
    ns = {"b": "http://www.omg.org/spec/BPMN/20100524/MODEL", "c": "http://camunda.org/schema/1.0/bpmn"}
    model = ET.parse(ROOT / "spec/processes/bpmn/SP-OP-PROGRAMA-001_Programas_Cuidado.bpmn")
    observed = {}
    for node in model.findall(".//b:serviceTask", ns):
        params = {p.attrib["name"]: p.text for p in node.findall(".//c:inputParameter", ns)}
        if "event_payload_vars" in params:
            fields = set(params["event_payload_vars"].split(","))
            if "event_desfecho" in params:
                fields.add("desfecho")
            observed[params["event_topic"]] = fields
    assert observed == PROGRAM_EVENT_FIELDS


def test_all_five_actual_program_sink_calls_are_bound():
    # Supplement to behavioral tests: deleting a notification guard cannot hide behind prose.
    for module, expected in [(events, 1), (programa, 4)]:
        tree = ast.parse(Path(module.__file__).read_text())
        guarded = 0
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "handler":
                guards = [
                    x
                    for x in ast.walk(node)
                    if isinstance(x, ast.Call)
                    and isinstance(x.func, ast.Name)
                    and x.func.id == "validate_program_publication"
                ]
                guarded += len(guards)
        assert guarded == expected


@pytest.mark.parametrize(
    "body",
    [
        None,
        b"",
        b"{",
        b"k_min: 7",
        raw_document(),
        raw_document() + b"k_min: 8\n",
        raw_document().replace(b"maezo.population-egress.v1", b"wrong"),
    ],
)
async def test_every_untrusted_canonical_state_stops_before_provider(monkeypatch, tmp_path, body):
    canonical = tmp_path / "canonical.yaml"
    if body is not None:
        canonical.write_bytes(body)
    monkeypatch.setattr(policy, "CANONICAL_PATH", canonical)
    # An unrelated positive override never supplies authority or a floor.
    arbitrary = tmp_path / "unowned.yaml"
    arbitrary.write_bytes(raw_document())
    monkeypatch.setenv("POPULATION_POLICY_PATH", str(arbitrary))
    monkeypatch.setenv("MIN_K_ANONYMITY", "999")
    inner = SimpleNamespace(population_metrics=AsyncMock())
    client = population.gate_population(inner, SeamContext(tenant="tenant", principal="andre"))
    with pytest.raises(policy.PopulationPolicyUnavailableError):
        await client.population_metrics("cohort", features=["synthetic_metric"])
    inner.population_metrics.assert_not_awaited()


@pytest.mark.parametrize(
    "method",
    [
        "make_stratify_risk_handler",
        "make_stop_processing_handler",
        "make_proactive_contact_handler",
        "make_notify_sla_risk_handler",
    ],
)
async def test_notification_nested_instance_slot_refuses_before_sink(method):
    sink = SimpleNamespace(publish=AsyncMock(return_value=True))
    with pytest.raises(ValueError):
        await getattr(programa, method)(sink)(
            task(
                dict(
                    tenant_id="tenant",
                    programa_id={"resultados": {"cohort_size": 1}},
                    beneficiario_pseudo_id="pseudo",
                    consentimento_ativo=True,
                    consent_checked=True,
                )
            )
        )
    sink.publish.assert_not_awaited()


def test_lexical_fence_never_authorizes_prose():
    import importlib.util
    import sys

    path = ROOT / "tests/unit/spec/test_programa_population_aggregate_fence.py"
    spec = importlib.util.spec_from_file_location("r228_fence", path)
    fence = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = fence
    spec.loader.exec_module(fence)
    floor = fence.KFloor("k_min", 7, "maezo.population-egress.v1")
    for prose in ["# k_min", "description: k_min", "<documentation>k_min</documentation>", "not_k_min"]:
        consumer = fence.Consumer("bpmn", "consumer", "fixture", "population", prose)
        assert fence.evaluate_gate(floor, [consumer])


@pytest.mark.parametrize(
    "changes",
    [
        {"cohort_size": 6},
        {"k_anonymity": True},
        {"cohort_size": "7"},
        {"metrics": {"unratified": 1.0}},
        {"metrics": {"synthetic_metric": float("nan")}},
        {"metrics": {"synthetic_metric": True}},
        {"suppressed": ("unratified",)},
        {"dataset_ref": "Patient/synthetic"},
    ],
)
async def test_malformed_aggregate_has_zero_consumer_calls(monkeypatch, changes):
    monkeypatch.setattr(population, "load_population_policy", authorized)
    values = dict(
        cohort_id="cohort-test",
        dataset_ref="lake://synthetic",
        metrics={"synthetic_metric": 2.0},
        cohort_size=7,
        k_anonymity=7,
    )
    values.update(changes)
    inner = SimpleNamespace(population_metrics=AsyncMock(return_value=CohortAggregate(**values)))
    consumer = AsyncMock()
    client = population.gate_population(inner, SeamContext(tenant="tenant", principal="andre"))
    with pytest.raises(policy.PopulationPolicyUnavailableError):
        await consumer(await client.population_metrics("cohort-test", features=["synthetic_metric"]))
    consumer.assert_not_awaited()


async def test_uncontracted_new_program_topic_has_zero_sink_calls():
    sink = SimpleNamespace(publish=AsyncMock(return_value=True))
    with pytest.raises(policy.PopulationPolicyUnavailableError):
        await events.make_publish_event_handler(sink)(
            task(
                dict(
                    event_topic="agents.events.programa.resultados",
                    event_payload_vars="tenant_id",
                    tenant_id="tenant",
                )
            )
        )
    sink.publish.assert_not_awaited()


async def test_unratified_feature_request_has_zero_provider_calls(monkeypatch):
    monkeypatch.setattr(population, "load_population_policy", authorized)
    inner = SimpleNamespace(population_metrics=AsyncMock())
    client = population.gate_population(inner, SeamContext(tenant="tenant", principal="andre"))
    with pytest.raises(policy.PopulationPolicyUnavailableError):
        await client.population_metrics("cohort-test", features=["unratified"])
    inner.population_metrics.assert_not_awaited()
