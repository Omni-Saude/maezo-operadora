"""Fleet-wide parity fence: an Agent Card that ANNOUNCES a task type must have a handler for it.

WHY THIS FILE EXISTS (audit finding CC-02, and the reason it kept reappearing per agent). Five
`agent.yaml` files declared `a2a.accepted_task_types` — the Card contract the dispatcher validates
against (`maezo.a2a.card.AgentCard.accepts`) — while `agents/<id>/delegation.py` simply did not
exist (marina, beatriz, gustavo, valentina) or existed only as an origin (rafael's
`delegate_auth_analysis` lived in tests alone). A Card that announces `glosa.analyze` and an
absent handler are indistinguishable from a wired edge FROM THE YAML, which is exactly how the
divergence survived four separate per-agent reviews. Valentina's own yaml comment even asserted
that `make_valentina_handler` "ja o consome" — a claim about a file that did not exist.

WHAT THIS FENCE ASSERTS, and what it deliberately does not:
  1. Every agent whose `accepted_task_types` is NON-EMPTY has an importable
     `maezo.agents.<id>.delegation` exposing a callable `make_<id>_handler`.
  2. Every task type a delegation module DECLARES (its module-level `TASK_TYPE*` constants) is in
     that agent's own `accepted_task_types` — no phantom type a Card would refuse.
  3. Conversely, an `accepted_task_types` entry that NO constant in the module declares is a
     documented, enumerated divergence (`_UNDECLARED_TASK_TYPES`) or a failure. There is no
     silent skip and no `xfail`.
  4. REGISTRATION is a DIFFERENT question, and this file records rather than requires it: an
     agent may only be registered in the composition root if it HAS a handler (assert), and the
     agents that have a handler but are NOT registered are enumerated in `_UNREGISTERED_HANDLERS`
     below with the reason. That set is not an allowlist of shame — it is the executable
     statement of an OWNER DECISION (gap `FERNANDO-DELEGATION-CALL-SITE`, "o registro em
     `a2a_composition` e o call site de origem sao decisao do dono"), so that the day the owner
     wires one, this file is what tells the next engineer to update the record.
It does NOT assert that any edge is LIVE: no origin worker calls any of these builders today, and
this fence would stay green if none ever did. Liveness is the origin call site's question, and
that call site is owner-gated.

WP A2A-YAML-DISCLOSURE (CC-02 residual / NEW-B2 / NEW-B3 / NEW-B4) adds two more assertions this
docstring's original four did not cover:
  5. `spec/agents/<id>/agent.yaml`'s `a2a.handler_status` (closed vocabulary: `registrado` /
     `pronto_sem_registro` / `ausente`) must equal the TRUTH derived from (a) whether
     `agents/<id>/delegation.py` exists and exposes a callable `make_<id>_handler`, and (b)
     whether `<id>` is a key of a `handlers={...}` registration in the composition root. This is
     the fence CC-02's prose disclosures could not be: the yaml comments that used to deny,
     omit, or duplicate this fact are now checked against the same ground truth a validator
     enforces the SHAPE of (`maezo.platform.validation.agent_def`).
  6. `_registered_agents()` itself is now fail-CLOSED rather than silently partial: it accepts
     only the two statically-resolvable `handlers=` shapes this file actually uses today (a dict
     literal with string-constant keys, and a `dict(...)` call built from keyword arguments or a
     single dict-literal argument) and RAISES on anything else — a `**` spread, a dict
     comprehension, or a bare name reference used to under-report silently before, which is the
     dangerous direction for a security-relevant registration record.
"""

from __future__ import annotations

import ast
import importlib
import importlib.util
from pathlib import Path
from typing import Any, Final

import pytest
import yaml

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[3]
_SPEC_AGENTS: Final[Path] = _REPO_ROOT / "spec" / "agents"
_SRC_AGENTS: Final[Path] = _REPO_ROOT / "src" / "maezo" / "agents"
_COMPOSITION_ROOT: Final[Path] = (
    _REPO_ROOT / "src" / "maezo" / "runtime" / "agent_runtime" / "a2a_composition.py"
)

#: `accepted_task_types` entries no constant in that agent's `delegation.py` declares, each with
#: the reason. DOCUMENTED DIVERGENCE, never a skip — the assertion below still runs, it is just
#: satisfied by this named entry instead of by a constant.
#:
#: * andre / `analytics.actuarial` — his `delegation.py` declares only the SHARED
#:   `TASK_TYPE_POPULATION_ANALYTICS = "analytics.population"` and routes by ORIGIN, not by task
#:   type (`_flow_for`), so his handler serves BOTH types while naming one. Adding the second
#:   constant is a one-line edit inside HIS work package (AND-01/AND-03), not this one's.
_UNDECLARED_TASK_TYPES: Final[dict[str, frozenset[str]]] = {
    "andre": frozenset({"analytics.actuarial"}),
}

#: Agents that HAVE an inbound handler but are NOT registered with any dispatcher in the
#: composition root. This is the CC-02 residual this work package deliberately stops in front of:
#: registering a handler wires a live delegation surface, and gap `FERNANDO-DELEGATION-CALL-SITE`
#: classifies that as an OWNER DECISION. Recorded here as an executable fact so it cannot rot into
#: prose — see this module's docstring, point 4.
#:
#: MEMBERSHIP IS NOT THE ONLY GATE. Removing an entry is legal only once that agent's OWN
#: registration pre-conditions are met; they live in its `make_<id>_handler` docstring. Beatriz's
#: two (mirrored here so the record is executable-adjacent, `agents/beatriz/delegation.py`):
#:   1. the originating call site must carry `evidencia_refs` over a list-capable channel (or
#:      Beatriz must read it from the process variables) BEFORE any registration — otherwise a
#:      delegated `assemble_dossier` returns `desfecho="dossie_instruido"` over an EMPTY corpus;
#:   2. `gather_evidence` and `assemble_dossier` share one task_type AND one `task_id`
#:      (`FRAUDE-{tenant}-{caso}`), so the SECOND hop is a Guard-4 REPLAY of the first, never a
#:      second run. A caller needing the hops as distinct units must carry a per-hop key, which
#:      SP-OP-FRAUDE-001 does not define — recorded rather than invented (same structural note as
#:      valentina's `care.stratify`/`care.enroll`).
#:
#: `fernando` LEFT this set on 2026-09-05 (owner decision R-081, gap
#: `FERNANDO-DELEGATION-CALL-SITE`, approved 2026-09-04: "SIM — ligar o call site em
#: `inadimplencia.py::prepare_dossier` e registrar `make_fernando_handler` em
#: `a2a_composition.py`, com a chamada fail-neutral"). BOTH halves landed: his handler is in
#: `build_dossier_delegation_dispatcher`'s `handlers={...}` (REACHABLE) and the ORIGIN call site
#: exists too — `tools/workers/inadimplencia.py::make_prepare_dossier_handler`, the raw-async
#: form of `operadora.inadimplencia.prepare_dossier`, calls `delegate_arrears_followup`
#: fail-neutrally. So fernando is not merely registered, he is REACHED: registration and liveness
#: (this file's docstring's DIFFERENT question) are both true for him, which is why he is out of
#: this set for good rather than parked in it.
_UNREGISTERED_HANDLERS: Final[frozenset[str]] = frozenset({"marina", "beatriz", "gustavo", "valentina"})


def _accepted_task_types() -> dict[str, frozenset[str]]:
    """agent id -> its Card's `accepted_task_types`, for every agent that declares a non-empty one."""
    declared: dict[str, frozenset[str]] = {}
    for path in sorted(_SPEC_AGENTS.glob("*/agent.yaml")):
        definition: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
        a2a = (definition or {}).get("a2a") or {}
        types = a2a.get("accepted_task_types") or []
        if types:
            declared[str(definition["id"])] = frozenset(str(t) for t in types)
    return declared


_DECLARED_TARGETS: Final[dict[str, frozenset[str]]] = _accepted_task_types()


def _declared_task_type_constants(agent_id: str) -> frozenset[str]:
    """Every string a module-level `TASK_TYPE*` assignment in `<agent>/delegation.py` binds.

    Read off the AST rather than by import so that a module that fails to import produces the
    handler-existence failure below, not a confusing collection error here. Both shapes count: a
    plain `TASK_TYPE_X = "a.b"` and a set/frozenset/tuple/list literal (`TASK_TYPES = {...}`).
    """
    path = _SRC_AGENTS / agent_id / "delegation.py"
    if not path.exists():
        return frozenset()
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in tree.body:
        if not isinstance(node, ast.Assign | ast.AnnAssign):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        names = [t.id for t in targets if isinstance(t, ast.Name)]
        if not any(name.startswith("TASK_TYPE") for name in names):
            continue
        found |= _string_constants(node.value)
    return frozenset(found)


def _string_constants(value: ast.expr | None) -> set[str]:
    """The string literals directly inside `value` (a constant, or one collection literal)."""
    if isinstance(value, ast.Constant) and isinstance(value.value, str):
        return {value.value}
    elements: list[ast.expr] = []
    if isinstance(value, ast.Set | ast.Tuple | ast.List):
        elements = list(value.elts)
    elif isinstance(value, ast.Call) and value.args:
        # `frozenset({...})` — look through the one call argument.
        return _string_constants(value.args[0])
    return {e.value for e in elements if isinstance(e, ast.Constant) and isinstance(e.value, str)}


def _registered_agents() -> frozenset[str]:
    """Agent ids appearing as keys of a `handlers={...}` keyword in the composition root."""
    tree = ast.parse(_COMPOSITION_ROOT.read_text(encoding="utf-8"))
    registered: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for keyword in node.keywords:
            if keyword.arg != "handlers" or not isinstance(keyword.value, ast.Dict):
                continue
            registered |= {
                key.value
                for key in keyword.value.keys
                if isinstance(key, ast.Constant) and isinstance(key.value, str)
            }
    return frozenset(registered)


# =================================================================================================
# 1 + 2 + 3 — Card contract <-> handler module
# =================================================================================================


def test_the_declared_target_set_is_not_vacuous() -> None:
    """A fence derived from a glob that stopped matching passes everything. Anchor it."""
    assert set(_DECLARED_TARGETS) >= {
        "rafael",
        "carolina",
        "andre",
        "fernando",
        "marina",
        "beatriz",
        "gustavo",
        "valentina",
    }, (
        f"agents declaring accepted_task_types: {sorted(_DECLARED_TARGETS)} — either a Card "
        "contract was genuinely removed (a reviewed edit here) or the spec walk stopped seeing "
        "the yaml files, which would make every assertion below vacuous."
    )


@pytest.mark.parametrize("agent_id", sorted(_DECLARED_TARGETS))
def test_every_declared_delegation_target_has_an_inbound_handler(agent_id: str) -> None:
    """CC-02: a Card that ANNOUNCES a task type must have something that can serve it.

    `spec/agents/<id>/agent.yaml` declaring `accepted_task_types` is the contract the dispatcher
    enforces (`AgentCard.accepts`); without `make_<id>_handler` the announcement is fiction, and
    from the yaml alone it looks exactly like a wired edge.
    """
    module_name = f"maezo.agents.{agent_id}.delegation"
    assert importlib.util.find_spec(module_name) is not None, (
        f"`spec/agents/{agent_id}/agent.yaml` announces accepted_task_types "
        f"{sorted(_DECLARED_TARGETS[agent_id])} but `src/maezo/agents/{agent_id}/delegation.py` "
        "does not exist — the Card advertises a delegation surface nothing can serve (CC-02)."
    )
    module = importlib.import_module(module_name)
    factory = getattr(module, f"make_{agent_id}_handler", None)
    assert callable(factory), (
        f"{module_name} exists but exposes no callable `make_{agent_id}_handler` — the "
        "composition root has nothing to register, and the Card's announcement stays fiction."
    )


@pytest.mark.parametrize("agent_id", sorted(_DECLARED_TARGETS))
def test_a_delegation_module_declares_no_task_type_its_card_would_refuse(agent_id: str) -> None:
    """A `TASK_TYPE*` constant outside `accepted_task_types` is an envelope the dispatcher would
    reject at Guard `AgentCard.accepts` — a builder that can only ever produce rejections."""
    phantom = _declared_task_type_constants(agent_id) - _DECLARED_TARGETS[agent_id]
    assert not phantom, (
        f"`agents/{agent_id}/delegation.py` declares task type(s) {sorted(phantom)} that "
        f"`spec/agents/{agent_id}/agent.yaml` does not accept — the dispatcher would refuse every "
        "envelope built with them. Spec-first: add them to the Card, or drop the constants."
    )


@pytest.mark.parametrize("agent_id", sorted(_DECLARED_TARGETS))
def test_every_accepted_task_type_is_declared_by_the_handler_module(agent_id: str) -> None:
    """The other direction: an announced task type nothing in the module names is the CC-02
    divergence in miniature. Permitted ONLY as a named entry in `_UNDECLARED_TASK_TYPES`."""
    undeclared = _DECLARED_TARGETS[agent_id] - _declared_task_type_constants(agent_id)
    documented = _UNDECLARED_TASK_TYPES.get(agent_id, frozenset())
    assert undeclared <= documented, (
        f"`spec/agents/{agent_id}/agent.yaml` accepts {sorted(undeclared - documented)} but "
        f"`agents/{agent_id}/delegation.py` declares no constant for it. Declare it (a "
        "`TASK_TYPE_*` constant), or record the divergence with its reason in "
        "`_UNDECLARED_TASK_TYPES`."
    )


def test_the_undeclared_task_type_record_has_no_stale_entries() -> None:
    """An allowlist entry the module now DECLARES a constant for is stale, and `<=` above cannot
    see it: the assertion is satisfied whether or not the entry is still needed, so a divergence
    that was CLOSED keeps its documented exemption forever and the next real divergence for that
    agent slips in under it. Every documented entry must therefore still be undeclared.
    """
    for agent_id, documented in sorted(_UNDECLARED_TASK_TYPES.items()):
        stale = documented & _declared_task_type_constants(agent_id)
        assert not stale, (
            f"`_UNDECLARED_TASK_TYPES[{agent_id!r}]` still exempts {sorted(stale)}, but "
            f"`agents/{agent_id}/delegation.py` now declares a `TASK_TYPE_*` constant for it — "
            "the divergence is closed. Delete the entry (and the agent key if it empties), so "
            "the allowlist keeps covering only what is genuinely still undeclared."
        )


def test_the_undeclared_task_type_record_names_only_real_agents() -> None:
    """A documented divergence for an agent that no longer declares Cards is dead weight that
    would quietly widen the fence."""
    assert set(_UNDECLARED_TASK_TYPES) <= set(_DECLARED_TARGETS)


# =================================================================================================
# 4 — Registration in the composition root: RECORDED, not required (owner decision)
# =================================================================================================


def test_the_composition_root_never_registers_a_handler_that_does_not_exist() -> None:
    """The direction that IS an invariant: registering `handlers={"x": ...}` for an agent with no
    `delegation.py` would fail at import, loudly, in production composition."""
    registered = _registered_agents()
    assert registered, "no `handlers={...}` mapping found in the composition root — walk broke"
    for agent_id in sorted(registered):
        assert importlib.util.find_spec(f"maezo.agents.{agent_id}.delegation") is not None, (
            f"`a2a_composition` registers a handler for {agent_id!r}, which has no delegation.py"
        )


def test_the_handlers_not_registered_in_the_composition_root_are_the_documented_owner_gap() -> None:
    """CC-02's REMAINING half, stated as an executable fact rather than prose.

    Every agent in `_UNREGISTERED_HANDLERS` HAS a working inbound handler and is NOT reachable by
    any dispatcher: `runtime.agent_runtime.a2a_composition` wires only rafael (auth edge) and
    carolina+andre+fernando (dossier edge). That is deliberate — gap `FERNANDO-DELEGATION-CALL-SITE`
    classified both the registration and the origin call site as an OWNER DECISION; the owner then
    approved fernando's (R-081) and BOTH halves landed, which is exactly why he is no longer here.
    The four that remain have no owner decision authorizing an edge.

    If this test fails saying an agent LEFT the set, the owner registered it: delete the entry
    (and check the origin call site landed with it). If it fails saying one JOINED, a new handler
    was built without registration — record it here with its gap id.
    """
    with_handler = {
        agent_id
        for agent_id in _DECLARED_TARGETS
        if importlib.util.find_spec(f"maezo.agents.{agent_id}.delegation") is not None
    }
    unregistered = frozenset(with_handler) - _registered_agents()
    assert unregistered == _UNREGISTERED_HANDLERS, (
        f"agents with an inbound handler but no dispatcher registration are {sorted(unregistered)}, "
        f"not the documented {sorted(_UNREGISTERED_HANDLERS)}. Update this record together with "
        "the composition-root change (gap FERNANDO-DELEGATION-CALL-SITE, owner decision)."
    )


# =================================================================================================
# 5 — `a2a.handler_status`/`a2a.handler_symbol`: the structured field must equal the truth
# (WP A2A-YAML-DISCLOSURE, CC-02 residual / NEW-B2 / NEW-B3 / NEW-B4)
# =================================================================================================

_HANDLER_STATUS_VALUES: Final[frozenset[str]] = frozenset(
    {"registrado", "pronto_sem_registro", "ausente"}
)


def _agent_ids() -> frozenset[str]:
    """Every non-template agent id under `spec/agents/` (directory name == its `id:`)."""
    return frozenset(
        p.parent.name for p in _SPEC_AGENTS.glob("*/agent.yaml") if p.parent.name != "_template"
    )


def _declared_handler_disclosure() -> dict[str, tuple[Any, Any]]:
    """agent id -> `(a2a.handler_status, a2a.handler_symbol)` exactly as the yaml declares them
    (no defaulting — an absent key comes back `None`, which is itself a fact this fence checks)."""
    declared: dict[str, tuple[Any, Any]] = {}
    for agent_id in _agent_ids():
        definition: Any = yaml.safe_load(
            (_SPEC_AGENTS / agent_id / "agent.yaml").read_text(encoding="utf-8")
        )
        a2a = (definition or {}).get("a2a") or {}
        declared[agent_id] = (a2a.get("handler_status"), a2a.get("handler_symbol"))
    return declared


def _handler_module_and_factory(agent_id: str) -> tuple[Any, Any] | None:
    """`(module, make_<id>_handler)` iff BOTH the module imports and the factory is callable."""
    module_name = f"maezo.agents.{agent_id}.delegation"
    if importlib.util.find_spec(module_name) is None:
        return None
    module = importlib.import_module(module_name)
    factory = getattr(module, f"make_{agent_id}_handler", None)
    if not callable(factory):
        return None
    return module, factory


def _expected_handler_status(agent_id: str) -> str:
    """The TRUTH: `ausente` (no importable `make_<id>_handler`), else `registrado` (agent_id is a
    `handlers={...}` key in the composition root) or `pronto_sem_registro` (it is not)."""
    if _handler_module_and_factory(agent_id) is None:
        return "ausente"
    return "registrado" if agent_id in _registered_agents() else "pronto_sem_registro"


def test_every_real_agent_declares_a_handler_status() -> None:
    """Non-vacuity anchor + closed-vocabulary check, over ALL 10 real agents (not just the 8 that
    declare `accepted_task_types`) — `helena`/`lucas` must disclose `ausente` too, since a missing
    field is indistinguishable from an agent nobody ever finished disclosing."""
    declared = _declared_handler_disclosure()
    assert set(declared) == _agent_ids(), (
        f"expected every real agent to declare a2a.handler_status, got {sorted(declared)} vs "
        f"agent ids {sorted(_agent_ids())} — the spec walk stopped seeing yaml files"
    )
    for agent_id, (status, _symbol) in sorted(declared.items()):
        assert status in _HANDLER_STATUS_VALUES, (
            f"spec/agents/{agent_id}/agent.yaml a2a.handler_status={status!r} is not one of "
            f"{sorted(_HANDLER_STATUS_VALUES)} (CC-02: the field must be a closed vocabulary)"
        )


@pytest.mark.parametrize("agent_id", sorted(_agent_ids()))
def test_declared_handler_status_matches_the_delegation_and_registration_truth(agent_id: str) -> None:
    """The core CC-02 fix: prose used to deny (andre/carolina), omit (marina/gustavo), or claim
    (beatriz's yaml said the wiring was deferred while a handler already existed) this fact. The
    structured field must equal what `agents/<id>/delegation.py` and `a2a_composition.py` actually
    say, not what the yaml's author believed when they wrote the comment."""
    declared_status, _symbol = _declared_handler_disclosure()[agent_id]
    expected = _expected_handler_status(agent_id)
    assert declared_status == expected, (
        f"spec/agents/{agent_id}/agent.yaml declares a2a.handler_status={declared_status!r} but "
        f"the real state (agents/{agent_id}/delegation.py existence + make_{agent_id}_handler + "
        f"a2a_composition.py registration) is {expected!r}"
    )


@pytest.mark.parametrize("agent_id", sorted(_agent_ids()))
def test_declared_handler_symbol_matches_when_a_handler_exists(agent_id: str) -> None:
    """The companion fact: when a handler exists, the yaml must name the EXACT symbol (not just
    disclose that one exists); when none exists, no symbol may be named (a symbol on an `ausente`
    entry is the same claim-and-deny contradiction this fence exists to close)."""
    _status, symbol = _declared_handler_disclosure()[agent_id]
    resolved = _handler_module_and_factory(agent_id)
    if resolved is None:
        assert symbol is None, (
            f"spec/agents/{agent_id}/agent.yaml declares a2a.handler_symbol={symbol!r} but has no "
            f"importable, callable make_{agent_id}_handler"
        )
        return
    expected_symbol = f"maezo.agents.{agent_id}.delegation::make_{agent_id}_handler"
    assert symbol == expected_symbol, (
        f"spec/agents/{agent_id}/agent.yaml a2a.handler_symbol should be {expected_symbol!r}, "
        f"got {symbol!r}"
    )


# =================================================================================================
# 6 — `_registered_agents()` must fail CLOSED on anything it cannot statically resolve
# =================================================================================================


def test_registered_agents_reads_the_real_composition_root_non_vacuously() -> None:
    """A hardened parser that stopped matching the real file would pass every test below
    vacuously — anchor it against the known-live registrations."""
    assert _registered_agents() >= {"rafael", "carolina", "andre", "fernando"}


def test_registered_agents_accepts_a_dict_call_with_keyword_arguments() -> None:
    source = "build(handlers=dict(x=handler, y=handler2))\n"
    assert _registered_agents(source=source) == frozenset({"x", "y"})


def test_registered_agents_accepts_a_dict_call_with_a_single_dict_literal_argument() -> None:
    source = 'build(handlers=dict({"x": handler}))\n'
    assert _registered_agents(source=source) == frozenset({"x"})


def test_registered_agents_fails_closed_on_a_double_star_spread_in_a_dict_literal() -> None:
    source = 'build(handlers={**base, "x": handler})\n'
    with pytest.raises(AssertionError, match="literal"):
        _registered_agents(source=source)


def test_registered_agents_fails_closed_on_a_double_star_spread_in_a_dict_call() -> None:
    source = "build(handlers=dict(**base))\n"
    with pytest.raises(AssertionError, match="literal"):
        _registered_agents(source=source)


def test_registered_agents_fails_closed_on_a_dict_comprehension() -> None:
    source = "build(handlers={k: v for k, v in pairs})\n"
    with pytest.raises(AssertionError, match="literal"):
        _registered_agents(source=source)


def test_registered_agents_fails_closed_on_a_bare_name_reference() -> None:
    source = "build(handlers=SOME_PREBUILT_DICT)\n"
    with pytest.raises(AssertionError, match="literal"):
        _registered_agents(source=source)


def test_registered_agents_fails_closed_on_a_non_string_constant_key() -> None:
    source = "build(handlers={AGENT_ID: handler})\n"
    with pytest.raises(AssertionError, match="literal"):
        _registered_agents(source=source)
