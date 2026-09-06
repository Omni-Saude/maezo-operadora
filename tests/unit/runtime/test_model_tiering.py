"""AF-12: `agent.yaml`'s declared model tiers reach the inference seam, and cannot bend PHI zoning.

THE GAP. `spec/agents/*/agent.yaml` has declared `model: {task_default: {tier: fast}, reasoning:
{tier: frontier}}` for all ten agents plus `_template` since Phase 0. ADR-0009 §2 mandates the
routing ("classificacao -> modelo rapido/barato; raciocinio critico -> fronteira; lote -> batch
tier") and §3 says the config lives "por tenant+agente na Agent Definition".
`AgentDefinition.model` was parsed by the loader and read by NOTHING;
`InferenceProvider.generate()` had no `task_kind` parameter at all. Declared config with no
consumer.

WHAT THIS SLICE ACTUALLY DELIVERS, stated so the tests are not read as claiming more:
  * the parameter exists, is threaded from the agent's declaration through the composition root
    to the provider, and is validated FAIL-CLOSED at construction against the ADR-0009 vocabulary;
  * every resolution is logged and counted on `maezo_llm_tier_resolution_total`;
  * it CANNOT change which provider serves a call, and therefore cannot bend ADR-0006 zoning.
It does NOT route to different models, because this repo configures exactly ONE model and no
per-tier map exists. That is the honest fail-closed resolution AF-12 asks for, and the per-tier
model VALUES are an owner decision recorded in `docs/review-queue.md`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Final

import pytest

from maezo.agents import AgentLoader
from maezo.platform.observability import get_metrics_collector
from maezo.runtime.inference import (
    DEFAULT_TASK_KIND,
    MODEL_TASK_KINDS,
    MODEL_TIERS,
    TIER_NO_MAP,
    TIER_RESOLUTIONS,
    TIER_UNDECLARED,
    InferenceConfigError,
    InferenceProvider,
    InferenceSettings,
    PhiZoneRoutingError,
)

_SPEC_AGENTS: Final[Path] = Path(__file__).resolve().parents[3] / "spec" / "agents"

_HELENA_TIERS: Final[dict[str, str]] = {"task_default": "fast", "reasoning": "frontier"}


def _tier_count(task_kind: str, tier: str, resolution: str = "modelo_unico") -> float:
    value = get_metrics_collector().registry.get_sample_value(
        "maezo_llm_tier_resolution_total",
        {"task_kind": task_kind, "tier": tier, "resolution": resolution},
    )
    return float(value or 0.0)


# =================================================================================================
# The spec side: `AgentDefinition.model` is CONSUMED, not merely parsed
# =================================================================================================


def test_every_shipped_agent_declares_both_task_kinds_and_they_parse() -> None:
    """Non-vacuity: the field this slice consumes is genuinely populated across the whole spec."""
    loader = AgentLoader()
    seen: dict[str, dict[str, str]] = {}
    for agent_dir in sorted(_SPEC_AGENTS.iterdir()):
        if not (agent_dir / "agent.yaml").is_file():
            continue
        seen[agent_dir.name] = loader.load(agent_dir / "agent.yaml").model_tiers()

    assert len(seen) >= 11, sorted(seen)  # 10 named agents + `_template`
    for agent_id, tiers in seen.items():
        assert tiers == _HELENA_TIERS, (agent_id, tiers)


def test_model_tiers_skips_a_malformed_block_rather_than_refusing_to_load_the_agent() -> None:
    """A cosmetic YAML slip must not make an agent unloadable — it becomes an UNDECLARED kind.

    Refusing here would turn a telemetry-grade defect into "this agent does not exist"; the
    provider is where an unusable tier fails closed.
    """
    definition = AgentLoader().load_from_dict(
        {
            "id": "probe",
            "name": "Probe",
            "role": "probe",
            "model": {
                "task_default": {"tier": "fast"},
                "reasoning": "frontier",  # malformed: a string, not a `{tier: ...}` mapping
                "batch": {"tier": ""},  # malformed: empty tier
            },
        }
    )
    assert definition.model_tiers() == {"task_default": "fast"}


# =================================================================================================
# Fail-closed at CONSTRUCTION — the same moment/type as a missing credential
# =================================================================================================


def test_an_unknown_tier_refuses_to_construct_the_provider() -> None:
    with pytest.raises(InferenceConfigError) as excinfo:
        InferenceProvider(model_tiers={"task_default": "turbo"})
    assert "turbo" in str(excinfo.value)
    assert "fast" in str(excinfo.value)  # names the vocabulary it will accept


def test_an_unknown_task_kind_refuses_to_construct_the_provider() -> None:
    with pytest.raises(InferenceConfigError) as excinfo:
        InferenceProvider(model_tiers={"triagem": "fast"})
    assert "triagem" in str(excinfo.value)


def test_no_tier_map_is_not_an_error() -> None:
    """Most constructions legitimately have no agent definition; they are counted, not refused."""
    provider = InferenceProvider()
    assert provider.provider_name == "noop"


def test_the_vocabularies_are_the_adr_0009_ones() -> None:
    assert {"fast", "frontier", "batch"} == MODEL_TIERS
    assert {"task_default", "reasoning", "batch"} == MODEL_TASK_KINDS
    assert DEFAULT_TASK_KIND == "task_default"
    assert {"modelo_unico", "modelo_por_tier"} == TIER_RESOLUTIONS


# =================================================================================================
# Resolution is explicit — never a silent default to a different model
# =================================================================================================


@pytest.mark.asyncio
async def test_a_declared_task_kind_resolves_to_its_declared_tier_and_is_counted() -> None:
    provider = InferenceProvider(model_tiers=dict(_HELENA_TIERS))

    before = _tier_count("reasoning", "frontier")
    await provider.generate("prompt", task_kind="reasoning")
    after = _tier_count("reasoning", "frontier")

    assert after == before + 1.0, (before, after)


@pytest.mark.asyncio
async def test_a_call_naming_no_task_kind_is_counted_as_the_declared_default() -> None:
    """`None` means `task_default` — which is what every `agent.yaml` declares as its default.

    Picking any other default here would silently contradict the spec this slice exists to honour.
    """
    provider = InferenceProvider(model_tiers=dict(_HELENA_TIERS))

    before = _tier_count("task_default", "fast")
    await provider.generate("prompt")
    after = _tier_count("task_default", "fast")

    assert after == before + 1.0, (before, after)


@pytest.mark.asyncio
async def test_a_provider_with_no_tier_map_is_counted_under_the_sem_mapa_sentinel() -> None:
    """ "No agent definition was wired here" must be visible, not indistinguishable from `fast`."""
    provider = InferenceProvider()

    before = _tier_count("task_default", TIER_NO_MAP)
    await provider.generate("prompt")
    after = _tier_count("task_default", TIER_NO_MAP)

    assert after == before + 1.0, (before, after)


@pytest.mark.asyncio
async def test_a_task_kind_the_agent_did_not_declare_is_counted_as_undeclared() -> None:
    """A graph asking for a kind its `agent.yaml` omits is a spec/code disagreement — named, not
    silently folded into the default."""
    provider = InferenceProvider(model_tiers={"task_default": "fast"})

    before = _tier_count("reasoning", TIER_UNDECLARED)
    await provider.generate("prompt", task_kind="reasoning")
    after = _tier_count("reasoning", TIER_UNDECLARED)

    assert after == before + 1.0, (before, after)


@pytest.mark.asyncio
async def test_the_tier_never_selects_a_model_other_than_the_configured_one() -> None:
    """The core fail-closed property: no tier may cause a DIFFERENT model to be used.

    With one configured model and no per-tier map, both tiers must resolve to that same id. A
    future per-tier map is an owner decision; a silent divergence here would be the defect AF-12
    names ("never a silent default of a different model").
    """
    settings = InferenceSettings(provider="noop", model="modelo-configurado")
    provider = InferenceProvider(settings, model_tiers=dict(_HELENA_TIERS))

    assert provider._resolve_task_model("task_default") == "modelo-configurado"
    assert provider._resolve_task_model("reasoning") == "modelo-configurado"
    assert provider.model_id == "modelo-configurado"


# =================================================================================================
# I-6: tiering cannot bend ADR-0006 PHI zoning
# =================================================================================================


@pytest.mark.parametrize("task_kind", [None, "task_default", "reasoning", "batch"])
@pytest.mark.asyncio
async def test_no_tier_can_route_a_phi_call_to_a_non_phi_provider(task_kind: str | None) -> None:
    """Every tier, same refusal. The zone check runs BEFORE any tier is resolved, and reads only
    `phi` against the active provider's `phi_capable` — a tier is not an input to it."""
    provider = InferenceProvider(model_tiers={"task_default": "fast", "reasoning": "frontier"})

    with pytest.raises(PhiZoneRoutingError):
        await provider.generate("prompt", phi=True, task_kind=task_kind)


@pytest.mark.asyncio
async def test_a_refused_phi_call_never_even_resolves_a_tier() -> None:
    """Ordering, asserted rather than assumed: the refusal pre-empts the tier resolution entirely,
    so a tier can never participate in (or be blamed for) a zoning decision."""
    provider = InferenceProvider(model_tiers=dict(_HELENA_TIERS))

    before = _tier_count("reasoning", "frontier")
    with pytest.raises(PhiZoneRoutingError):
        await provider.generate("prompt", phi=True, task_kind="reasoning")
    after = _tier_count("reasoning", "frontier")

    assert after == before, (before, after)


@pytest.mark.asyncio
async def test_the_gated_seam_forwards_task_kind_unchanged_and_never_reads_it() -> None:
    """The chokepoint may only SUBTRACT permission: `task_kind` must not change which catalogue
    token a call records under (that is chosen from `phi` alone)."""
    from maezo.gateway.seams._base import SeamContext
    from maezo.gateway.seams.inference import gate_inference

    seen: list[tuple[bool, str | None]] = []

    class _Inner:
        async def generate(
            self,
            prompt: str,
            *,
            phi: bool = False,
            agent_id: str | None = None,
            tenant_id: str | None = None,
            task_kind: str | None = None,
        ) -> str:
            seen.append((phi, task_kind))
            return "ok"

    gated = gate_inference(_Inner(), SeamContext(tenant="amh", principal="lucas"))
    await gated.generate("p", phi=False, task_kind="reasoning")
    await gated.generate("p", phi=True, task_kind="task_default")

    assert seen == [(False, "reasoning"), (True, "task_default")]


# =================================================================================================
# The wire: composition root -> provider, and one agent that actually names its kinds
# =================================================================================================


def test_the_agent_runtime_root_passes_the_declared_tiers_to_the_provider() -> None:
    from maezo.runtime.agent_runtime.service import _declared_model_tiers

    definition = AgentLoader().load_by_id("helena")
    assert _declared_model_tiers(definition) == _HELENA_TIERS
    # Total when the definition failed to load — reported by `agent_definition_loaded`, not twice.
    assert _declared_model_tiers(None) == {}


@pytest.mark.asyncio
async def test_lucas_routes_the_dossier_narrative_to_reasoning_and_the_message_to_default() -> None:
    """The disclosure in `agents/lucas/graph.py` claimed no tiering existed. It must now be true
    that the graph names its kinds — otherwise removing the disclosure would be the fabrication."""
    from tests.unit.agents.test_lucas import _FakeInference, _graph

    inference = _FakeInference(["texto", "narrativa", "ack"])
    graph = _graph(inference=inference)
    state: Any = {"tenant_id": "amh", "tipo_solicitacao": "boleto", "motivo_humano": "outro"}
    await graph._build_message(state)
    await graph._build_dossier(state)
    await graph._build_escalation_ack(state)

    assert inference.task_kinds == ["task_default", "reasoning", "task_default"]
