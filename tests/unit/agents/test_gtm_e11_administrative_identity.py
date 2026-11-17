"""GTM-E-11: identidade do consumidor administrativo parametrizada, sem efeito para o Lucas."""

from __future__ import annotations

import ast
import json
from pathlib import Path
from types import MappingProxyType
from typing import Any

import pytest

import maezo.agents.lucas.administrative as package
from maezo.agents.lucas.administrative.graph import (
    OPERATIONS_BY_TASK,
    AdministrativeConsumer,
    compras_consumer,
)
from maezo.agents.lucas.administrative.handoff import AdministrativeInputError, parse_handoff
from maezo.agents.lucas.administrative.identity import (
    LUCAS_ADMINISTRATIVE_IDENTITY,
    AdministrativeIdentity,
)
from maezo.agents.lucas.administrative.runtime import (
    FULL_RUNTIME_GRAPH_VERSION,
    AdministrativeJourneyRuntime,
)
from maezo.agents.lucas.administrative.state import administrative_checkpoint_config
from maezo.gateway.capabilities.models import CapabilityEnvelope, CapabilityOutcome
from maezo.gateway.pseudonymizer import Pseudonymizer
from maezo.platform.webhooks.whatsapp.security import hash_message_id
from tests.unit.agents.test_lucas_administrative_graph import (
    RecordingCapabilityService,
)
from tests.unit.agents.test_lucas_administrative_graph import (
    values as graph_input,
)
from tests.unit.agents.test_lucas_administrative_runtime import (
    assembled,
    turn_input,
)

PACKAGE_DIR = Path(package.__file__).parent
SCANNED = ("graph.py", "runtime.py", "state.py", "handoff.py", "journey.py")
IDENTITY_ERROR = "administrative_identity_contract_mismatch"

COMMON = frozenset(
    {
        "access.resolve",
        "case.open_or_update",
        "external_wait.settle",
        "notice.prepare_or_send",
        "milestone.publish",
    }
)
COMPRAS = COMMON | frozenset(
    {
        "offer.compose",
        "acceptance.record",
        "enrollment.request",
        "reservation.command",
        "fulfillment.observe",
    }
)
SUPORTE = COMMON | frozenset({"feedback.record"})


def probe_identity(**overrides: Any) -> AdministrativeIdentity:
    values: dict[str, Any] = {
        "agent_id": "probe_admin",
        "checkpoint_material_version": "probe-administrative.v1",
        "checkpoint_thread_prefix": "probe:administrative",
        "full_runtime_graph_version": "probe-administrative-full.v1",
        "full_runtime_thread_prefix": "probe:administrative:full",
        "operations_by_task": {"journey.compras.step": frozenset({"external_wait.settle"})},
    }
    values.update(overrides)
    return AdministrativeIdentity(**values)


def _docstring_nodes(tree: ast.AST) -> set[int]:
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                ids.add(id(body[0].value))
    return ids


def test_administrative_identity_parametrizada() -> None:
    violations: list[str] = []
    for name in SCANNED:
        tree = ast.parse((PACKAGE_DIR / name).read_text(encoding="utf-8"))
        docs = _docstring_nodes(tree)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and id(node) not in docs
                and "lucas" in node.value.lower()
            ):
                violations.append(f"{name}:{node.lineno} literal")
            if isinstance(node, ast.Call):
                for keyword in node.keywords:
                    if (
                        keyword.arg == "agent"
                        and isinstance(keyword.value, ast.Constant)
                        and isinstance(keyword.value.value, str)
                    ):
                        violations.append(f"{name}:{keyword.value.lineno} agent=literal")
    assert not violations, f"HARDCODED_AGENT_IDENTITY: {violations}"


def test_identidade_lucas_byte_identica() -> None:
    lucas = LUCAS_ADMINISTRATIVE_IDENTITY
    assert (
        lucas.agent_id,
        lucas.checkpoint_material_version,
        lucas.checkpoint_thread_prefix,
        lucas.full_runtime_graph_version,
        lucas.full_runtime_thread_prefix,
    ) == (
        "lucas",
        "lucas-administrative.proposed.v1",
        "lucas:administrative",
        "lucas-administrative-full-driver.proposed.v1",
        "lucas:administrative:full",
    )
    assert FULL_RUNTIME_GRAPH_VERSION == "lucas-administrative-full-driver.proposed.v1"
    assert isinstance(lucas.operations_by_task, MappingProxyType)
    assert dict(lucas.operations_by_task) == {
        "journey.compras.step": COMPRAS,
        "journey.suporte.step": SUPORTE,
    }
    assert OPERATIONS_BY_TASK is lucas.operations_by_task

    pseudonymizer = Pseudonymizer(key=b"unit-e11-key")
    for task in ("journey.compras.step", "journey.suporte.step"):
        default = administrative_checkpoint_config(
            task_type=task,
            tenant_ref="unit-tenant-a",
            journey_ref="unit-journey-a",
            pseudonymizer=pseudonymizer,
        )
        explicit = administrative_checkpoint_config(
            task_type=task,
            tenant_ref="unit-tenant-a",
            journey_ref="unit-journey-a",
            pseudonymizer=pseudonymizer,
            identity=lucas,
        )
        material = json.dumps(
            ["lucas-administrative.proposed.v1", task, "unit-tenant-a", "unit-journey-a"],
            ensure_ascii=True,
            separators=(",", ":"),
        )
        pseudonym = hash_message_id(material, "unit-tenant-a", pseudonymizer)
        assert default == explicit
        assert default["configurable"]["thread_id"] == f"lucas:administrative:{pseudonym}"
        assert default["configurable"]["thread_id"].startswith("lucas:administrative:")


@pytest.mark.asyncio
async def test_nova_instancia_emite_id_e_checkpoint_proprios(monkeypatch: pytest.MonkeyPatch) -> None:
    identity = probe_identity()
    pseudonymizer = Pseudonymizer(key=b"unit-e11-key")
    config = administrative_checkpoint_config(
        task_type="journey.compras.step",
        tenant_ref="unit-tenant-a",
        journey_ref="unit-journey-a",
        pseudonymizer=pseudonymizer,
        identity=identity,
    )
    thread = config["configurable"]["thread_id"]
    assert thread.startswith("probe:administrative:") and "lucas" not in thread

    calls: list[str] = []
    monkeypatch.setattr(
        "maezo.platform.observability.record_agent_error",
        lambda *, agent, error_type: calls.append(agent),
    )

    class FailingService(RecordingCapabilityService):
        async def execute(self, envelope: CapabilityEnvelope, payload: object) -> CapabilityOutcome:
            raise RuntimeError("unit-service-failure")

    consumer = compras_consumer(
        tenant_ref="unit-tenant-a",
        service=FailingService("journey.compras.step"),
        enabled=True,
        identity=identity,
    )
    assert consumer.identity is identity
    with pytest.raises(RuntimeError):
        await consumer.invoke(graph_input())
    assert calls == ["probe_admin"]

    # Runtime: mesma falha de grafo, id e chave de checkpoint próprios.
    calls.clear()
    b, _, driver, _, base = assembled()

    async def fail() -> None:
        raise RuntimeError("unit-driver-failure")

    driver.on_call = fail
    runtime = AdministrativeJourneyRuntime(
        binding=b,
        driver=driver,
        currentness=driver.currentness,
        checkpointer=base._AdministrativeJourneyRuntime__checkpointer,
        pseudonymizer=Pseudonymizer(key=b"unit-runtime-fixture-key"),
        enabled=True,
        identity=identity,
    )
    await runtime.accept_turn(turn_input(b))
    assert calls == ["probe_admin"]
    key = runtime._config()["configurable"]["thread_id"]
    assert key.startswith("probe:administrative:full:") and "lucas" not in key
    assert base._config()["configurable"]["thread_id"].startswith("lucas:administrative:full:")


@pytest.mark.parametrize(
    "overrides",
    [
        {"agent_id": "Lucas"},
        {"agent_id": "has space"},
        {"agent_id": ""},
        {"agent_id": "a" * 33},
        {"agent_id": 7},
        {"checkpoint_thread_prefix": ""},
        {"checkpoint_material_version": "com espaco"},
        {"full_runtime_graph_version": "acentuação"},
        {"full_runtime_thread_prefix": 3},
        {"operations_by_task": {}},
        {"operations_by_task": {"journey.outra.step": frozenset({"x"})}},
        {"operations_by_task": {"journey.compras.step": frozenset()}},
        {"operations_by_task": {"journey.compras.step": ["a"]}},
        {"operations_by_task": {"journey.compras.step": frozenset({1})}},
    ],
)
def test_identidade_invalida_recusa_sem_eco(overrides: dict[str, Any]) -> None:
    with pytest.raises(AdministrativeInputError) as raised:
        probe_identity(**overrides)
    assert str(raised.value) == IDENTITY_ERROR


def test_identidade_de_tipo_errado_e_task_fora_do_conjunto_recusadas_sem_eco() -> None:
    service = RecordingCapabilityService("journey.suporte.step")
    marker = "journey.suporte.step"
    for bad in (object(), {"agent_id": "lucas"}, "lucas"):
        with pytest.raises(AdministrativeInputError) as raised:
            AdministrativeConsumer(
                task_type="journey.compras.step",
                tenant_ref="unit-tenant-a",
                service=service,
                identity=bad,  # type: ignore[arg-type]
            )
        assert str(raised.value) == "administrative_consumer_contract_mismatch"
        assert "lucas" not in str(raised.value)
    with pytest.raises(AdministrativeInputError) as raised:
        AdministrativeConsumer(
            task_type=marker,  # type: ignore[arg-type]
            tenant_ref="unit-tenant-a",
            service=service,
            identity=probe_identity(),
        )
    assert str(raised.value) == "administrative_consumer_contract_mismatch"
    assert marker not in str(raised.value)


@pytest.mark.parametrize("field", ["body", "symptom", "texto"])
def test_handoff_sem_corpo(field: str) -> None:
    marker = "SYNTHETIC_FREE_TEXT_dor no peito"
    payload = {
        "schema_version": "v21-administrative-handoff.proposed.v1",
        "task_type": "journey.compras.step",
        "tenant_ref": "unit-tenant-a",
        "journey_ref": "unit-journey-a",
        "message_ref": "hk1_" + "a" * 64,
        field: marker,
    }
    with pytest.raises(AdministrativeInputError) as raised:
        parse_handoff(payload)
    assert str(raised.value) == "administrative_handoff_contract_mismatch"
    assert marker not in str(raised.value)
