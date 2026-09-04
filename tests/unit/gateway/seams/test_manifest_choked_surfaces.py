"""`superficies[].choked` is an OBSERVED FACT, and this file is what makes it one (design §7.2).

Until Onda 1 the field was pure prose: the manifest's own header called it "DOCUMENTATION, not
policy — the loader never reads it". That is exactly the shape of the defect design finding R-1
and R-6 both describe — the documentation of the effect-authorization plane running ahead of the
code — and it is load-bearing here, because I-10 makes `choked` the thing an approver trusts when
they ask "will shadow telemetry actually give me evidence for this class?". A `choked: true` that
nobody checks is a promise of evidence that may not exist.

So every `choked: true` surface must survive three assertions:

  1. it maps to an operation that EXISTS in the closed catalogue (`effect_classes.OPERATIONS`),
     and that operation's `action_class` is the class the surface is declared under;
  2. its construction site routes through `gateway.tool_registry` — proven by AST-scanning the
     five composition roots for direct construction of the raw class (adversary A-11 / §8.1);
  3. the wrapper that gates it really is a gated instance (not just a name in a table).

And, symmetrically, every `choked: false` surface must have a RECORDED REASON — the three that
stayed false are enumerated by hand below, so "we forgot to flip it" and "we deliberately did not"
can never look the same from the outside.

The worker-topic surfaces are choked by the OTHER chokepoint (`WorkerHarness._handle`, keyed by
external-task topic, untouched by this wave), so they are classified separately: asserting they
route through the agent-seam registry would be false.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest
import yaml

from maezo.gateway import effect_classes
from maezo.gateway.effect_pep import DecisionContext
from maezo.gateway.seams import SeamContext, is_gated_seam

_REPO_ROOT = Path(__file__).resolve().parents[4]
_SRC = _REPO_ROOT / "src" / "maezo"
_MANIFEST = _REPO_ROOT / "spec" / "policies" / "autonomy" / "action-approvals.yaml"

#: The FIVE composition roots §5.5 names. A raw effect-class construction in any of them is the
#: A-11 drift this wave exists to close.
_COMPOSITION_ROOTS = (
    "runtime/agent_runtime/service.py",
    "runtime/agent_runtime/a2a_composition.py",
    "platform/webhooks/service.py",
    "platform/integrations/notifications_bridge.py",
    "runtime/worker_runtime/service.py",
)

#: Raw effect classes that may only be constructed inside `gateway/tool_registry.py` (§8.1's list,
#: narrowed to the ones this leg actually wires — a name is here only if the registry builds it).
_RAW_EFFECT_CLASSES = frozenset(
    {
        "CibSevenHttpTransport",
        "FreshClientCibSevenTransport",
        "CibSevenDmnTransport",
        "FhirServer",
        "FhirServerReader",
        "ValentinaFhirServerReader",
        "WhatsAppServerSender",
        "LucasWhatsAppServerSender",
    }
)

#: Surface `referencia` -> the catalogue operation(s) that surface is choked BY. Hand-maintained,
#: on purpose: this table IS the claim `choked: true` makes, and a claim a script infers from the
#: code it is checking would prove nothing.
_SEAM_CHOKED_SURFACES: dict[str, tuple[str, ...]] = {
    "src/maezo/tools/mcp_fhir/server.py:88": ("fhir.read_patient", "fhir.read_patient_summary"),
    "src/maezo/tools/mcp_fhir/server.py:118": ("fhir.search_coverage",),
    "src/maezo/tools/mcp_whatsapp/server.py:111": ("whatsapp.send_message",),
    "src/maezo/tools/mcp_cibseven/transport.py:278": ("cibseven.find_active_instance",),
    "src/maezo/tools/mcp_cibseven/transport.py:307": ("cibseven.find_any_instance",),
    "src/maezo/tools/mcp_cibseven/transport.py:387": ("cibseven.correlate_message",),
    "src/maezo/tools/mcp_cibseven/transport.py:420": ("cibseven.get_process_status",),
    "src/maezo/agents/rafael/graph.py:548": ("dmn.evaluate",),
    "src/maezo/agents/marina/graph.py:806": ("dmn.evaluate",),
    "src/maezo/tools/workers/dmn_transport.py:345": ("dmn.evaluate",),
    "src/maezo/runtime/inference/__init__.py:624": ("inference.generate", "inference.generate_phi"),
    "src/maezo/a2a/dispatcher.py:277": ("a2a.delegate",),
}

#: The surfaces that deliberately stayed `false`, each with the reason recorded HERE as well as in
#: the manifest — so a future flip has to delete a line from this table and explain itself.
_DELIBERATELY_UNCHOKED: dict[str, str] = {
    "src/maezo/tools/mcp_cibseven/transport.py:1069": (
        "the agent-initiated process start. `start_process_instance` is only reached from INSIDE "
        "`start_process_idempotent`, after the durable claim; a denial there wedges a strict "
        "business key. Gating it means changing the fenced function's internals — §5.8 / I-6."
    ),
    "src/maezo/agents/andre/graph.py:708": (
        "the population/actuarial lake client is PORT-PENDING (WB.4). The wrapper exists and is "
        "tested; nothing is injected, so nothing is observed, so there is no evidence to claim."
    ),
    "src/maezo/agents/andre/graph.py:720": ("same as :708 — the second `PopulationFeatureClient` operation."),
}


def _manifest() -> dict[str, Any]:
    return yaml.safe_load(_MANIFEST.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def _surfaces() -> list[tuple[str, str, bool]]:
    """`(action_class, referencia, choked)` for every declared surface."""
    return [
        (name, surface["referencia"], bool(surface["choked"]))
        for name, spec in _manifest()["acoes"].items()
        for surface in spec["superficies"]
    ]


# --- (1) every choked:true agent-seam surface resolves to a catalogued operation ---------------


def test_every_seam_choked_surface_maps_to_a_catalogued_operation() -> None:
    for action_class, referencia, choked in _surfaces():
        if referencia not in _SEAM_CHOKED_SURFACES:
            continue
        assert choked, f"{referencia} is in the seam-choked table but the manifest says false"
        for operation in _SEAM_CHOKED_SURFACES[referencia]:
            spec = effect_classes.lookup_operation(operation)
            assert spec is not None, f"{referencia} claims operation {operation!r}, not catalogued"
            assert spec.action_class == action_class, (
                f"{referencia} is declared under class {action_class!r} but the catalogue puts "
                f"{operation!r} under {spec.action_class!r} — the manifest and the catalogue disagree"
            )


def test_the_choked_true_and_choked_false_partitions_are_both_accounted_for() -> None:
    """No surface may be silently uncategorised — the whole point of making the field verifiable.

    Every `choked: true` surface is either an agent-seam surface (this wave's table) or a
    worker-topic surface choked by `WorkerHarness._handle`; every `choked: false` surface must
    appear in the deliberately-unchoked table with a written reason.
    """
    for _action_class, referencia, choked in _surfaces():
        if choked:
            is_seam = referencia in _SEAM_CHOKED_SURFACES
            is_worker_topic = referencia.startswith("src/maezo/tools/workers/") and not referencia.startswith(
                "src/maezo/tools/workers/dmn_transport.py"
            )
            assert is_seam or is_worker_topic, (
                f"{referencia} is choked:true but belongs to neither chokepoint's inventory — "
                "either it is not really choked, or this test has gone stale"
            )
        else:
            assert referencia in _DELIBERATELY_UNCHOKED, (
                f"{referencia} is choked:false with no recorded reason. Either wire it, or record "
                "why it cannot be wired — 'we forgot' and 'we decided not to' must never look alike"
            )
            assert _DELIBERATELY_UNCHOKED[referencia].strip()


def test_the_deliberately_unchoked_table_has_no_stale_entries() -> None:
    """Revert-RED: if a surface gets wired and flipped, its excuse must be deleted, not left."""
    declared = {referencia for _c, referencia, choked in _surfaces() if not choked}
    assert declared == set(_DELIBERATELY_UNCHOKED), (
        "the deliberately-unchoked table and the manifest disagree: "
        f"manifest-only={declared - set(_DELIBERATELY_UNCHOKED)}, "
        f"table-only={set(_DELIBERATELY_UNCHOKED) - declared}"
    )


# --- (2) the construction site routes through the registry -------------------------------------


def _constructed_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                names.add(func.id)
            elif isinstance(func, ast.Attribute):
                names.add(func.attr)
    return names


@pytest.mark.parametrize("root", _COMPOSITION_ROOTS)
def test_no_composition_root_constructs_a_raw_effect_class(root: str) -> None:
    """A-11 / §8.1, enforced where it matters: the roots ASK the registry, they do not build.

    This is the static half of the `choked: true` claim. Without it, "the surface routes through
    the registry" would be a statement about the code as it is today rather than a property of it.
    """
    offenders = _constructed_names(_SRC / root) & _RAW_EFFECT_CLASSES
    assert not offenders, (
        f"{root} constructs raw effect class(es) {sorted(offenders)} instead of resolving them "
        "through `maezo.gateway.tool_registry` — that is exactly the drift adversary A-11 names"
    )


def test_the_registry_is_where_the_raw_classes_are_actually_constructed() -> None:
    """Non-vacuity: the previous test would also pass if NOBODY constructed these classes.

    Mirrors `check_start_process_fence.py`'s own `fence_called_in` counter — a gate that passes
    because it found nothing to look at is not a gate.
    """
    built = _constructed_names(_SRC / "gateway" / "tool_registry.py") & _RAW_EFFECT_CLASSES
    missing = _RAW_EFFECT_CLASSES - built
    assert not missing, (
        f"the registry does not construct {sorted(missing)} — either the seam is unwired (and its "
        "surface must not be choked:true) or the raw-class inventory here is stale"
    )


# --- (3) the wrappers really are gated instances ------------------------------------------------


def test_every_seam_factory_returns_a_gated_instance() -> None:
    """The table in (1) names operations; this proves the objects behind them are actually gated."""
    from maezo.gateway import tool_registry

    seam = SeamContext(tenant="amh", principal="rafael", decision=DecisionContext())
    inner = object()
    built = [
        tool_registry.build_fhir_seam(seam=seam, base_url="http://fhir.invalid"),
        tool_registry.build_dmn_seam(seam=seam, base_url="http://engine.invalid"),
        tool_registry.build_cibseven_seam(seam=seam, base_url="http://engine.invalid"),
        tool_registry.build_cibseven_seam(seam=seam, base_url="http://engine.invalid", fresh_client=True),
        tool_registry.build_whatsapp_seam(seam=seam),
        tool_registry.build_inference_seam(seam=seam),
        tool_registry.build_population_seam(seam=seam, inner=inner),
        tool_registry.build_a2a_seam(seam=seam, inner=inner),
    ]
    for obj in built:
        assert is_gated_seam(obj), f"{type(obj).__name__} is not a gated seam"
