"""Behavioural proofs for the ADDITIVE half of the effect chokepoint (Onda 1, design §7 / §5.7).

Four claims:

  (a) §7.3'S FOUR PRESERVED PROPERTIES STILL HOLD. Making enforcement two-dimensional is only
      safe if none of the existing fences moved: a global `shadow` still means NOTHING enforces, a
      typo in EITHER field still resolves to a non-enforcing state, `status: DRAFT` still makes
      every ALLOW unreachable, and approval is still all three domains by SET EQUALITY.
  (b) THE TWO NAMESPACES CANNOT SHADOW EACH OTHER. A ref declared in both `mapeamento_topicos` and
      `mapeamento_acoes` refuses the WHOLE manifest — YAML's duplicate-key guard cannot see this
      shape, and "last section wins" would silently re-route a class a reviewer already signed.
  (c) THE SPEC-DIR BYPASS (A-6) IS PINNED. `MAEZO_SPEC_DIR` substitutes the entire policy plane in
      one variable, invisibly to the `MAEZO_ACTION_APPROVALS_PATH` fence. In production it now
      says so loudly and cannot enforce. PROVISIONAL pending Q-6 — see the module comment.
  (d) THE SHIPPED RECORD AND THE CODE CATALOGUE AGREE. Every catalogued class is declared, every
      catalogued operation is routed, nothing is approved, nothing enforces, and the digest is
      stable.

Sibling of `test_effect_pep.py` (the decision core) and of
`tests/unit/sec/test_action_execution_fence.py` (the shipped-manifest fence).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from maezo.gateway import action_execution, effect_classes
from maezo.gateway.action_execution import (
    ACTION_MAP_KEY,
    APPROVER_DOMAINS,
    CLASS_ENFORCEMENT_FIELD,
    DEFAULT_ENFORCEMENT_KEY,
    ENFORCEMENT_ENFORCING,
    ENFORCEMENT_SHADOW,
    MODE_ENFORCING,
    MODE_SHADOW,
    MODE_SHADOW_OVERRIDE,
    MODE_UNRESOLVED,
    OVERRIDE_ENFORCEMENT_ENABLED,
    OVERRIDE_ENFORCEMENT_ENV,
    REASON_ACTION_UNMAPPED,
    REASON_APPROVED,
    RUNTIME_MODE_ENV,
    RUNTIME_MODE_ENVS,
    STATUS_RATIFIED,
    TOPIC_MAP_KEY,
    ActionExecutionGateway,
    combined_policy_digest,
    load_action_approvals,
    policy_artifact_digests,
)

_CLASS = "leitura_phi_clinica"
_OTHER = "inicio_processo_regulatorio"
_TOPIC = "operadora.contas.start_recurso"
_REF = "agente.fhir.read_patient"
_REPO_ROOT = Path(__file__).resolve().parents[3]
_SHIPPED = _REPO_ROOT / "spec" / "policies" / "autonomy" / "action-approvals.yaml"


@pytest.fixture(autouse=True)
def _clear_manifest_cache() -> Any:
    action_execution._load_cached.cache_clear()
    yield
    action_execution._load_cached.cache_clear()


@pytest.fixture(autouse=True)
def _no_ambient_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(action_execution.MANIFEST_PATH_ENV, raising=False)
    monkeypatch.delenv(OVERRIDE_ENFORCEMENT_ENV, raising=False)
    # BOTH spellings of the discriminator. Clearing only `AGENT_RUNTIME_MODE` left a developer
    # with `RUNTIME_MODE` exported (the name `key_scrubber` reads FIRST) able to flip section (c).
    for name in RUNTIME_MODE_ENVS:
        monkeypatch.delenv(name, raising=False)


def _approval(approved: bool) -> dict[str, Any]:
    if not approved:
        return {"aprovado": False, "aprovador": "PENDENTE", "data": "PENDENTE", "evidencia_ref": "PENDENTE"}
    return {
        "aprovado": True,
        "aprovador": "Fixture Approver, fixture role",
        "data": "2026-01-01",
        "evidencia_ref": "fixture://evidence",
    }


def _manifest(
    *,
    status: str = STATUS_RATIFIED,
    modo: str = MODE_ENFORCING,
    enforcement: Any = ENFORCEMENT_ENFORCING,
    default_enforcement: Any = None,
    approved: frozenset[str] = frozenset({_CLASS, _OTHER}),
    topic_map: dict[str, str] | None = None,
    action_map: dict[str, str] | None = None,
    domains: list[str] | None = None,
) -> dict[str, Any]:
    def _acao(name: str) -> dict[str, Any]:
        entry: dict[str, Any] = {
            "descricao": f"fixture {name}",
            "dominios_exigidos": domains if domains is not None else sorted(APPROVER_DOMAINS),
            "aprovacoes": {d: _approval(name in approved) for d in sorted(APPROVER_DOMAINS)},
        }
        if enforcement is not None:
            entry[CLASS_ENFORCEMENT_FIELD] = enforcement
        return entry

    manifest: dict[str, Any] = {
        "version": 1,
        "status": status,
        "modo": modo,
        "acoes": {_CLASS: _acao(_CLASS), _OTHER: _acao(_OTHER)},
        TOPIC_MAP_KEY: topic_map if topic_map is not None else {_TOPIC: _OTHER},
    }
    if default_enforcement is not None:
        manifest[DEFAULT_ENFORCEMENT_KEY] = default_enforcement
    if action_map is not None:
        manifest[ACTION_MAP_KEY] = action_map
    return manifest


def _write(tmp_path: Path, manifest: dict[str, Any], name: str = "action-approvals.yaml") -> Path:
    path = tmp_path / name
    path.write_text(yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


def _gateway(
    tmp_path: Path, manifest: dict[str, Any], name: str = "action-approvals.yaml"
) -> ActionExecutionGateway:
    return ActionExecutionGateway(load_action_approvals(_write(tmp_path, manifest, name)))


# ---------------------------------------------------------------------------------------------
# (a) §7.3's four preserved properties
# ---------------------------------------------------------------------------------------------


def test_a_global_shadow_still_means_nothing_enforces(tmp_path: Path) -> None:
    """Property 1. `modo` remains the CEILING: no per-class flip can climb above it.

    This is the panic switch the whole rollout depends on (§9.5): one line, no code change,
    disables enforcement for every class at once.
    """
    gateway = _gateway(tmp_path, _manifest(modo=MODE_SHADOW, enforcement=ENFORCEMENT_ENFORCING))
    for name in (_CLASS, _OTHER):
        decision = gateway.evaluate(name)
        assert decision.allow is True, "the fixture approves both classes"
        assert decision.enforced is False, f"{name} enforced under a global shadow — the ceiling leaked"
    assert gateway.evaluate("classe_inexistente").enforced is False


@pytest.mark.parametrize(
    "typo", ["enforcing ", "Enforcing", "ENFORCING", "enforce", True, 1, None, ["enforcing"], ""]
)
def test_every_per_class_typo_resolves_to_non_enforcing(tmp_path: Path, typo: Any) -> None:
    """Property 2a. The fail-closed pin idiom, applied to the new field.

    Same direction as `modo`'s existing warning: a failed flip looks exactly like a working shadow
    deployment from the outside, which is why the telemetry line now emits BOTH dimensions.
    """
    gateway = _gateway(tmp_path, _manifest(enforcement=typo))
    assert gateway.evaluate(_CLASS).enforced is False


@pytest.mark.parametrize("typo", ["enforcing ", "Enforcing", "enforce", True, ""])
def test_every_root_default_typo_resolves_to_non_enforcing(tmp_path: Path, typo: Any) -> None:
    """Property 2b. The same guard on a root key that IS PRESENT and carries a mistyped VALUE.

    `None` is NOT in this list, and its absence is the point: `_manifest` OMITS the key entirely
    when it is `None`, so that parameter was silently testing the ABSENT case under the label
    "typo" — and asserting the wrong answer for it (see the test below). A present-but-mistyped
    value still resolves non-enforcing, because the loader cannot tell which value was meant and
    guessing `enforcing` off a stray capital turns denials into a platform outage.
    """
    gateway = _gateway(tmp_path, _manifest(default_enforcement=typo))
    assert gateway.evaluate("nao_declarada").enforced is False


def test_an_absent_root_default_key_resolves_to_the_xrd09_literal(tmp_path: Path) -> None:
    """ABSENT root key => `enforcing`. XRD-09: "ação/política desconhecida … negam" (`0037-…:154`).

    The Q-2 ramp deviation is `shadow` for unmapped refs, and a deviation from a ratified clause
    has to be WRITTEN DOWN to exist. A manifest that never heard of
    `enforcement_padrao_nao_mapeado` — an older record, a hand-rolled staging copy, a file whose
    key someone deleted — was inheriting the exception for free and silently DISARMING enforcement
    for every unmapped topic and every uncatalogued agent operation. Absent is not consent.

    The shipped record is untouched by this: it DECLARES `shadow` explicitly (Q-2, with its own
    comment block saying so), which `test_the_shipped_manifest_still_enforces_nothing` pins.
    """
    manifest = _manifest(modo=MODE_ENFORCING, default_enforcement=None)
    assert DEFAULT_ENFORCEMENT_KEY not in manifest, "the fixture must OMIT the key, not blank it"

    approvals = load_action_approvals(_write(tmp_path, manifest))
    assert approvals.default_enforcement == ENFORCEMENT_ENFORCING
    assert approvals.enforcement_for(None) == ENFORCEMENT_ENFORCING
    assert approvals.enforcement_for("nao_declarada") == ENFORCEMENT_ENFORCING
    assert ActionExecutionGateway(approvals).evaluate("nao_declarada").enforced is True


def test_an_unmapped_topic_blocks_once_the_ramp_reaches_its_terminal_act(tmp_path: Path) -> None:
    """§9.4 step 7, from the WORKER end: the flip that restores XRD-09 literally, as DATA.

    `modo: enforcing` + `enforcement_padrao_nao_mapeado: enforcing` must make a topic nobody
    classified actually BLOCK — otherwise the terminal act of the rollout is a no-op and the ~80
    unmapped topics the manifest reserves for a human stay permanently exempt. The agent-side
    mirror of this is `test_effect_pep.py::test_an_uncatalogued_operation_takes_the_unmapped_
    enforcement_default`; the worker side had no equivalent.
    """
    gateway = _gateway(tmp_path, _manifest(modo=MODE_ENFORCING, default_enforcement=ENFORCEMENT_ENFORCING))
    unmapped = gateway.classify("operadora.jamais.mapeado")
    assert unmapped is None, "the fixture must leave this topic genuinely unmapped"

    decision = gateway.evaluate(unmapped)
    assert (decision.allow, decision.reason) == (False, REASON_ACTION_UNMAPPED)
    assert decision.enforcement == ENFORCEMENT_ENFORCING
    assert decision.enforced is True

    # …and the ramp's declared `shadow` is what holds it back today — one data line, nothing else.
    ramped = _gateway(
        tmp_path,
        _manifest(modo=MODE_ENFORCING, default_enforcement=ENFORCEMENT_SHADOW),
        name="ramp.yaml",
    )
    assert ramped.evaluate(ramped.classify("operadora.jamais.mapeado")).enforced is False


def test_an_absent_per_class_field_resolves_to_shadow(tmp_path: Path) -> None:
    """§7.3's stated default. Absent is the ramp's starting point, never 'no restriction'."""
    gateway = _gateway(tmp_path, _manifest(enforcement=None))
    assert gateway.approvals.enforcement_for(_CLASS) == ENFORCEMENT_SHADOW
    assert gateway.evaluate(_CLASS).enforced is False


def test_status_draft_still_makes_every_allow_unreachable(tmp_path: Path) -> None:
    """Property 3, including the forgery shape: enforcing + per-class enforcing + every block true."""
    gateway = _gateway(tmp_path, _manifest(status="DRAFT"))
    for name in (_CLASS, _OTHER):
        decision = gateway.evaluate(name)
        assert decision.allow is False, f"a DRAFT file ALLOWED {name} — the outer fence broke"
        assert decision.reason == action_execution.REASON_MANIFEST_DRAFT
        assert decision.enforced is True, "the DENY still blocks — DRAFT closes ALLOW, not enforcement"


@pytest.mark.parametrize(
    "domains",
    [["medica"], ["medica", "medica", "medica"], ["medica", "ans"], [], ["medica", "ans", "dpo"]],
)
def test_approval_is_still_all_three_domains_by_set_equality(tmp_path: Path, domains: list[str]) -> None:
    """Property 4. The per-class enforcement field must not become a way around the domain gate."""
    approvals = load_action_approvals(_write(tmp_path, _manifest(domains=domains)))
    assert approvals.approved == frozenset()


def test_enforcement_is_not_a_substitute_for_approval(tmp_path: Path) -> None:
    """A class flipped to `enforcing` without the three blocks only makes its own DENY BLOCK."""
    gateway = _gateway(tmp_path, _manifest(approved=frozenset()))
    decision = gateway.evaluate(_CLASS)
    assert (decision.allow, decision.enforced) == (False, True)


def test_a_single_class_can_be_flipped_without_touching_the_others(tmp_path: Path) -> None:
    """The POINT of §7.3: progressive enforcement was impossible in the previous data model."""
    manifest = _manifest(enforcement=ENFORCEMENT_SHADOW)
    manifest["acoes"][_OTHER][CLASS_ENFORCEMENT_FIELD] = ENFORCEMENT_ENFORCING
    gateway = _gateway(tmp_path, manifest)
    assert gateway.evaluate(_CLASS).enforced is False
    assert gateway.evaluate(_OTHER).enforced is True


# ---------------------------------------------------------------------------------------------
# (b) The two namespaces cannot shadow each other
# ---------------------------------------------------------------------------------------------


def test_a_ref_declared_in_both_maps_refuses_the_whole_manifest(tmp_path: Path) -> None:
    """§7.1. YAML's duplicate-key guard is blind to this shape; the loader must not be.

    "Last section wins" would let a second declaration silently re-route a class a reviewer
    already signed under the first — the same class of defect `_RefusingDuplicatesLoader` exists
    for, one level up. Refusing the WHOLE record is the only honest answer: a partially-honoured
    governance map is worse than none.
    """
    approvals = load_action_approvals(
        _write(tmp_path, _manifest(topic_map={_TOPIC: _OTHER}, action_map={_TOPIC: _CLASS}))
    )
    assert approvals.degraded is True
    assert approvals.approved == frozenset()
    assert approvals.mode == MODE_UNRESOLVED
    assert approvals.classify(_TOPIC) is None


def test_the_two_maps_coexist_and_both_classify(tmp_path: Path) -> None:
    approvals = load_action_approvals(
        _write(tmp_path, _manifest(topic_map={_TOPIC: _OTHER}, action_map={_REF: _CLASS}))
    )
    assert approvals.degraded is False
    assert approvals.classify(_TOPIC) == _OTHER
    assert approvals.classify(_REF) == _CLASS
    assert approvals.topic_to_class == {_TOPIC: _OTHER}, "mapeamento_topicos keeps its own meaning"


def test_a_malformed_action_map_refuses_rather_than_partially_loading(tmp_path: Path) -> None:
    manifest = _manifest()
    manifest[ACTION_MAP_KEY] = ["agente.fhir.read_patient"]
    approvals = load_action_approvals(_write(tmp_path, manifest))
    assert approvals.degraded is True


@pytest.mark.parametrize(
    "value",
    [
        {"classe": _CLASS},  # a nested mapping where a class name belongs
        [_CLASS],  # a list — the "one ref, many classes" edit somebody started
        None,  # `agente.fhir.read_patient:` with nothing after the colon
        7,  # an int
        True,  # a bool
        "",  # a blank string
        "   ",  # whitespace only
    ],
)
def test_a_malformed_action_map_value_refuses_rather_than_silently_dropping(
    tmp_path: Path, value: Any
) -> None:
    """A garbled routing line must REFUSE the record, not vanish into "unmapped".

    Same posture as the collision case above, one level down: `_string_map`'s comprehension
    silently DROPS an entry whose value is not a non-blank string, so an unfinished edit left the
    ref unmapped while the reviewed diff still showed a routing line that reads fine. Under
    `modo: enforcing` with XRD-09's root default that ref then BLOCKS — a governance record must
    not be able to disable a class by looking correct.

    ASYMMETRY, deliberate and out of scope here: `mapeamento_topicos` keeps the legacy dropping
    comprehension (pre-existing behaviour, 26 byte-unchanged reviewed lines). New data, new
    contract — see the note at the refusal site in `action_execution._parse`.
    """
    manifest = _manifest(action_map={_REF: value})
    approvals = load_action_approvals(_write(tmp_path, manifest))
    assert approvals.degraded is True, f"{value!r} was silently dropped instead of refusing"
    assert approvals.approved == frozenset()
    assert approvals.mode == MODE_UNRESOLVED
    assert approvals.classify(_REF) is None


@pytest.mark.parametrize("key", [None, 7, ""])
def test_a_malformed_action_map_key_refuses_too(tmp_path: Path, key: Any) -> None:
    """The other half: a non-string / blank REF is just as unusable as a non-string class."""
    manifest = _manifest(action_map={key: _CLASS})
    assert load_action_approvals(_write(tmp_path, manifest)).degraded is True


def test_the_legacy_topic_map_keeps_its_dropping_comprehension(tmp_path: Path) -> None:
    """The asymmetry above, ASSERTED rather than described — so it is a recorded decision.

    If someone later tightens `mapeamento_topicos` to the same contract (a reasonable follow-up,
    on its own review), this test fails and says so, instead of the change landing unnoticed
    inside a record whose 26 lines the brief pins as byte-unchanged.
    """
    manifest = _manifest(topic_map={_TOPIC: _OTHER, "operadora.garbled.topic": None})
    approvals = load_action_approvals(_write(tmp_path, manifest))
    assert approvals.degraded is False, "legacy behaviour: the topic map DROPS, it does not refuse"
    assert approvals.classify(_TOPIC) == _OTHER
    assert approvals.classify("operadora.garbled.topic") is None


def test_an_absent_action_map_is_not_an_error(tmp_path: Path) -> None:
    """Additive means additive: a manifest predating Onda 1 still loads exactly as before."""
    approvals = load_action_approvals(_write(tmp_path, _manifest()))
    assert approvals.degraded is False
    assert dict(approvals.action_ref_to_class) == {}


# ---------------------------------------------------------------------------------------------
# (c) The MAEZO_SPEC_DIR bypass (A-6), provisionally closed pending Q-6
# ---------------------------------------------------------------------------------------------


def _spec_tree(tmp_path: Path, manifest: dict[str, Any]) -> Path:
    autonomy = tmp_path / "spec" / "policies" / "autonomy"
    autonomy.mkdir(parents=True)
    _write(autonomy, manifest)
    return tmp_path / "spec"


def test_a_spec_dir_substituted_manifest_cannot_enforce_in_production(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A-6. ONE variable swaps action-approvals.yaml, L0-core.yaml, _hard_frozen.yaml AND every
    agent.yaml. The existing path-override fence never fires on it, so a forged
    `RATIFICADO` + `enforcing` tree would have ALLOWED and ENFORCED every class."""
    monkeypatch.setenv("MAEZO_SPEC_DIR", str(_spec_tree(tmp_path, _manifest())))
    monkeypatch.setenv(RUNTIME_MODE_ENV, "kubernetes")
    approvals = load_action_approvals()
    assert approvals.mode == MODE_SHADOW_OVERRIDE
    decision = ActionExecutionGateway(approvals).evaluate(_CLASS)
    assert decision.allow is True, "evaluation still RUNS — previewing a candidate record is legitimate"
    assert decision.enforced is False, "a single env var turned enforcement ON — the bypass is open"
    assert decision.reason == action_execution.REASON_OVERRIDE_NOT_ENFORCEABLE


def test_the_spec_dir_pin_is_narrow_and_does_not_fire_locally(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Deliberately narrow: only PRODUCTION mode. Every test run and every dev box sets the
    variable, and turning those into `shadow_override` would be noise, not a control."""
    monkeypatch.setenv("MAEZO_SPEC_DIR", str(_spec_tree(tmp_path, _manifest())))
    monkeypatch.delenv(RUNTIME_MODE_ENV, raising=False)
    assert load_action_approvals().mode == MODE_ENFORCING


def test_the_spec_dir_pin_is_lifted_by_the_same_companion_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One staged-rollout act, not two vocabularies: the existing companion flag governs both."""
    monkeypatch.setenv("MAEZO_SPEC_DIR", str(_spec_tree(tmp_path, _manifest())))
    monkeypatch.setenv(RUNTIME_MODE_ENV, "kubernetes")
    monkeypatch.setenv(OVERRIDE_ENFORCEMENT_ENV, OVERRIDE_ENFORCEMENT_ENABLED)
    assert load_action_approvals().mode == MODE_ENFORCING


def test_a_spec_dir_shadow_manifest_is_untouched(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The pin withholds ENFORCEMENT only. A shadow tree behaves identically with and without it."""
    monkeypatch.setenv("MAEZO_SPEC_DIR", str(_spec_tree(tmp_path, _manifest(modo=MODE_SHADOW))))
    monkeypatch.setenv(RUNTIME_MODE_ENV, "kubernetes")
    approvals = load_action_approvals()
    assert approvals.mode == MODE_SHADOW
    assert ActionExecutionGateway(approvals).evaluate(_CLASS).reason == REASON_APPROVED


def _load_and_capture(manifest_dir_owner: Path) -> list[dict[str, Any]]:
    """Load through the ambient env and return the captured structlog entries."""
    import structlog

    del manifest_dir_owner
    cap = structlog.testing.LogCapture()
    structlog.configure(processors=[cap])
    try:
        load_action_approvals()
    finally:
        structlog.reset_defaults()
    return list(cap.entries)


def test_the_spec_dir_load_emits_a_loud_error_line(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """§5.7 item 1: the same posture as the path override — an operator swapping the governed
    policy plane must leave a trace legible WITHOUT reading the manifest."""
    monkeypatch.setenv("MAEZO_SPEC_DIR", str(_spec_tree(tmp_path, _manifest())))
    monkeypatch.setenv(RUNTIME_MODE_ENV, "kubernetes")
    events = {(e["event"], e["log_level"]) for e in _load_and_capture(tmp_path)}
    assert ("action_approvals_spec_dir_overridden", "error") in events
    assert ("action_approvals_spec_dir_enforcement_refused", "error") in events


@pytest.mark.parametrize(
    ("runtime_mode", "modo", "expected_level"),
    [
        # PRODUCTION: `error`, whether or not the ENFORCEMENT leg has anything to withhold.
        ("kubernetes", MODE_ENFORCING, "error"),
        ("kubernetes", MODE_SHADOW, "error"),
        # LOCAL / unset: still RECORDED, at `info` — provenance is a fact on every load.
        (None, MODE_ENFORCING, "info"),
        (None, MODE_SHADOW, "info"),
        ("local", MODE_SHADOW, "info"),
    ],
)
def test_the_spec_dir_provenance_line_is_emitted_whenever_the_variable_is_set(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    runtime_mode: str | None,
    modo: str,
    expected_level: str,
) -> None:
    """WHERE the policy came from and WHETHER enforcement is withheld are two different questions.

    The earlier form conflated them: the provenance line was gated on the SAME condition as the
    enforcement refusal (production AND `modo: enforcing`), so a staging pod, a mis-labelled
    deployment, or a production tree in `shadow` running an entirely substituted policy plane left
    NO trace that the CODEOWNERS-listed tree was bypassed. Given that §5.7's whole claim is
    "an operator swapping the governed policy plane must leave a trace legible without reading the
    manifest", suppressing the trace in exactly the cases nobody is watching for is the defect.

    Level, not presence, is what production changes: `error` there (log routing pages on it),
    `info` otherwise (every dev box and every test run sets the variable, and an `error` per load
    would train people to filter the line away).
    """
    monkeypatch.setenv("MAEZO_SPEC_DIR", str(_spec_tree(tmp_path, _manifest(modo=modo))))
    if runtime_mode is None:
        monkeypatch.delenv(RUNTIME_MODE_ENV, raising=False)
    else:
        monkeypatch.setenv(RUNTIME_MODE_ENV, runtime_mode)

    lines = [e for e in _load_and_capture(tmp_path) if e["event"] == "action_approvals_spec_dir_overridden"]
    assert len(lines) == 1, "the provenance line must be emitted exactly once per load"
    assert lines[0]["log_level"] == expected_level
    assert lines[0]["override_env"] == "MAEZO_SPEC_DIR"
    assert lines[0]["production_runtime"] is (expected_level == "error")


def test_no_provenance_line_is_emitted_when_the_variable_is_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The control: "always" means "whenever the override is in play", not "on every load"."""
    monkeypatch.delenv("MAEZO_SPEC_DIR", raising=False)
    monkeypatch.setenv(action_execution.MANIFEST_PATH_ENV, str(_write(tmp_path, _manifest())))
    events = {e["event"] for e in _load_and_capture(tmp_path)}
    assert "action_approvals_spec_dir_overridden" not in events


def test_the_runtime_mode_discriminator_reads_both_declared_spellings() -> None:
    """The NAMES are pinned as literals, in `key_scrubber.py:117`'s order.

    Deliberately not written as `assert RUNTIME_MODE_ENVS == RUNTIME_MODE_ENVS`-shaped tautology,
    and deliberately not parametrized off the constant either: a test that derives its own matrix
    from the value under test SHRINKS silently when someone shortens that value, and reports green.
    """
    assert RUNTIME_MODE_ENVS == ("RUNTIME_MODE", "AGENT_RUNTIME_MODE")
    assert RUNTIME_MODE_ENV == "AGENT_RUNTIME_MODE"


@pytest.mark.parametrize("env_name", ["RUNTIME_MODE", "AGENT_RUNTIME_MODE"])
def test_either_runtime_mode_variable_arms_the_spec_dir_pin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, env_name: str
) -> None:
    """The pin read ONE name; the repo declares production under TWO.

    `key_scrubber.py:117` — the PHI-egress pseudonymizer, i.e. a control nobody would call
    optional — resolves `RUNTIME_MODE or AGENT_RUNTIME_MODE`. This module read only the second, so
    a deployment standardised on `RUNTIME_MODE` (arming the pseudonymizer, believing it had
    declared production) left the §5.7 spec-dir pin DISARMED: one variable substitutes the entire
    policy plane, and the fence that exists for exactly that never fired. One deployment
    vocabulary, two spellings, both honoured.
    """
    monkeypatch.setenv("MAEZO_SPEC_DIR", str(_spec_tree(tmp_path, _manifest())))
    for name in RUNTIME_MODE_ENVS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(env_name, "kubernetes")

    approvals = load_action_approvals()
    assert approvals.mode == MODE_SHADOW_OVERRIDE, f"{env_name} did not arm the pin"
    assert ActionExecutionGateway(approvals).evaluate(_CLASS).enforced is False


def test_either_runtime_mode_variable_can_also_declare_local(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """First-set-wins, in `key_scrubber`'s order — and `local` under EITHER name is still local.

    Deliberate divergence from `key_scrubber`, in the tightening direction: it `.strip().lower()`s
    the value, so `" Local "` reads as local there and as PRODUCTION here. Guessing at a mistyped
    runtime mode is the same class of fail-open the `modo` and `enforcement` pins already refuse.
    """
    monkeypatch.setenv("MAEZO_SPEC_DIR", str(_spec_tree(tmp_path, _manifest())))
    monkeypatch.setenv("RUNTIME_MODE", "local")
    monkeypatch.setenv(RUNTIME_MODE_ENV, "kubernetes")
    assert load_action_approvals().mode == MODE_ENFORCING, "the FIRST name set must win"

    monkeypatch.setenv("RUNTIME_MODE", " Local ")
    action_execution._load_cached.cache_clear()
    assert load_action_approvals().mode == MODE_SHADOW_OVERRIDE, (
        "a mistyped runtime mode must read as PRODUCTION here — never guessed into `local`"
    )


# -- The digest (§5.7 item 2)


def test_the_policy_digest_is_stable_and_content_addressed(tmp_path: Path) -> None:
    """An operator compares this against the reviewed commit; it must move ONLY with content."""
    path = _write(tmp_path, _manifest())
    first = policy_artifact_digests(path)
    assert combined_policy_digest(first) == combined_policy_digest(policy_artifact_digests(path))

    path.write_text(path.read_text(encoding="utf-8") + "\n# a comment\n", encoding="utf-8")
    assert combined_policy_digest(policy_artifact_digests(path)) != combined_policy_digest(first)


def test_the_digest_records_absent_artefacts_instead_of_failing(tmp_path: Path) -> None:
    """The digest is OPERATOR VISIBILITY, not a gate — `build_pep` is already the fail-closed
    gate for a missing `L0-core.yaml`, and duplicating that here would just add a second one."""
    digests = policy_artifact_digests(_write(tmp_path, _manifest()))
    assert digests["L0-core.yaml"] == "AUSENTE"
    assert digests["action-approvals.yaml"] != "AUSENTE"


def test_the_shipped_policy_digest_covers_all_four_artefact_families() -> None:
    digests = policy_artifact_digests(_SHIPPED)
    assert set(digests) == {"action-approvals.yaml", "L0-core.yaml", "_hard_frozen.yaml", "tenants-amh.yaml"}
    assert all(value not in {"AUSENTE", "ILEGIVEL"} for value in digests.values())
    assert load_action_approvals(_SHIPPED).policy_digest == combined_policy_digest(digests)


# ---------------------------------------------------------------------------------------------
# (d) The shipped record and the code catalogue agree
# ---------------------------------------------------------------------------------------------


@pytest.fixture
def shipped() -> dict[str, Any]:
    return yaml.safe_load(_SHIPPED.read_text(encoding="utf-8"))


def test_every_catalogued_class_is_declared_in_the_shipped_manifest(shipped: dict[str, Any]) -> None:
    """§8.5 item 3. A class the loader has never heard of can never be approved — so a catalogue
    entry pointing at one would be a silently unapprovable surface."""
    assert set(effect_classes.ACTION_CLASSES) == set(shipped["acoes"])


def test_every_catalogued_operation_is_routed_in_mapeamento_acoes(shipped: dict[str, Any]) -> None:
    expected = {
        effect_classes.agent_action_ref(op): spec.action_class
        for op, spec in effect_classes.OPERATIONS.items()
    }
    assert shipped[ACTION_MAP_KEY] == expected


def test_the_shipped_manifest_still_enforces_nothing(shipped: dict[str, Any]) -> None:
    """The whole build lands INERT (I-7). Both dimensions, asserted on the real record."""
    assert shipped["modo"] == MODE_SHADOW
    assert shipped[DEFAULT_ENFORCEMENT_KEY] == ENFORCEMENT_SHADOW
    for name, entry in shipped["acoes"].items():
        assert entry[CLASS_ENFORCEMENT_FIELD] == ENFORCEMENT_SHADOW, name
    approvals = load_action_approvals(_SHIPPED)
    assert set(approvals.class_enforcement.values()) == {ENFORCEMENT_SHADOW}
    assert approvals.default_enforcement == ENFORCEMENT_SHADOW


def test_the_onda1_classes_are_declared_unapproved_and_unchoked(shipped: dict[str, Any]) -> None:
    """Declaring a class is NOT approving it, and `choked` stays false until the wiring is a FACT
    (§7.2 / I-10) — a class with zero shadow lines is not approvable."""
    novos = sorted(name for name, spec in effect_classes.ACTION_CLASSES.items() if spec.novo)
    assert novos == [
        "avaliacao_dmn",
        "consulta_processo",
        "correlacao_processo",
        "inferencia_llm",
        "leitura_populacional",
    ]
    for name in novos:
        entry = shipped["acoes"][name]
        assert set(entry["dominios_exigidos"]) == set(APPROVER_DOMAINS), name
        for domain, block in entry["aprovacoes"].items():
            assert block["aprovado"] is False, f"{name}.{domain}"
            assert block["aprovador"] == "PENDENTE", f"{name}.{domain}"
        assert entry["superficies"], name
        assert all(s["choked"] is False for s in entry["superficies"]), name


def test_mapeamento_topicos_is_byte_unchanged_in_content(shipped: dict[str, Any]) -> None:
    """§5.8 / the brief's hard limit: 26 entries, none touched, none reclassified."""
    assert len(shipped[TOPIC_MAP_KEY]) == 26
    assert not set(shipped[TOPIC_MAP_KEY]) & set(shipped[ACTION_MAP_KEY])


def test_every_denial_shape_is_declared_and_bounded() -> None:
    """§8.5 item 4's precondition: a class with no declared refusal shape cannot be flipped."""
    for name, spec in effect_classes.ACTION_CLASSES.items():
        assert spec.denial_shape in effect_classes.DENIAL_SHAPES, name
        assert action_execution._is_bounded_token(spec.denial_shape), name


#: Design §6.1's ladder table, transcribed row by row: (class, rung, denial shape). The design
#: states the shape in PROSE per row; the token each phrase maps to is quoted beside it, so a
#: reviewer can check the transcription against the document without reading `effect_classes`.
#:
#: WHY THIS EXISTS AS DATA. `test_every_denial_shape_is_declared_and_bounded` above only proves
#: each class declares SOME shape from the closed set — swapping two classes' shapes passes it
#: untouched. That is not a cosmetic gap: design §2 A-12 is the inverse adversary (the PEP harming
#: the patient), and the shape IS the harm control. Giving `consulta_processo` the
#: `LACUNA_DECLARADA` shape would tell a wrapper to degrade the anti-dupla-terminação query to a
#: "disclosed gap" — the design calls that row "the highest-risk C0 item" precisely because a
#: denied read that reads as "no active instance" can double-terminate a contract. Likewise
#: `comunicacao_beneficiario` and `inicio_processo_regulatorio` relabelled as `LACUNA_DECLARADA`
#: would silently drop a beneficiary contact and turn a refused engine start into a gap note
#: instead of the audited fail-closed incident §9.3 requires.
_DESIGN_6_1_LADDER: tuple[tuple[str, str, str], ...] = (
    # -- C0 — inert / internal read ---------------------------------------------------------------
    # §6.1 C0 row 1: "the node takes its declared DMN-unavailable path (ADR-0028 fail-closed →
    # human), never a fabricated favourable outcome".
    ("avaliacao_dmn", effect_classes.RUNG_C0_LEITURA_INTERNA, effect_classes.SHAPE_ROTA_DMN_INDISPONIVEL),
    # §6.1 C0 row 2: "Denied read must **not** be readable as 'no active instance' — the
    # anti-dupla-terminação query must fail closed (skip) … the highest-risk C0 item".
    ("consulta_processo", effect_classes.RUNG_C0_LEITURA_INTERNA, effect_classes.SHAPE_LEITURA_INCONCLUSIVA),
    # -- C1 — outbound notification ---------------------------------------------------------------
    # §6.1 C1: "Denied send → the turn ends on its declared escalation/HITL path, the beneficiary
    # is not silently dropped, and no raw recipient appears in any log line (I-3)".
    (
        "comunicacao_beneficiario",
        effect_classes.RUNG_C1_NOTIFICACAO,
        effect_classes.SHAPE_ESCALONAMENTO_HUMANO,
    ),
    # -- C2 — PHI read / model egress ---------------------------------------------------------------
    # §6.1 C2 row 1: "Denied read degrades to the **disclosed gap note** every graph already
    # documents (`rafael/graph.py:44-48`) — never a fabricated fact".
    ("leitura_phi_clinica", effect_classes.RUNG_C2_PHI_OU_MODELO, effect_classes.SHAPE_LACUNA_DECLARADA),
    # §6.1 C2 row 2: "Denied → cohort dossier records an explicit gap; k-suppression unchanged".
    ("leitura_populacional", effect_classes.RUNG_C2_PHI_OU_MODELO, effect_classes.SHAPE_LACUNA_DECLARADA),
    # §6.1 C2 row 3: "Denied → the node's existing LLM-unavailable path".
    ("inferencia_llm", effect_classes.RUNG_C2_PHI_OU_MODELO, effect_classes.SHAPE_ROTA_LLM_INDISPONIVEL),
    # -- C3 — engine mutation -----------------------------------------------------------------------
    # §6.1 C3 row 1: "Denied → no instance exists in the engine, an audited refusal row exists, and
    # the external task lands as an incident with retries=0".
    (
        "inicio_processo_regulatorio",
        effect_classes.RUNG_C3_MUTACAO_ENGINE,
        effect_classes.SHAPE_INCIDENTE_FALHA_FECHADA,
    ),
    # §6.1 C3 row 2: "Denied → no message correlated; the process waits at its receive task;
    # incident visible".
    (
        "correlacao_processo",
        effect_classes.RUNG_C3_MUTACAO_ENGINE,
        effect_classes.SHAPE_INCIDENTE_FALHA_FECHADA,
    ),
    # §6.1 C3 row 3: "Denied → the delegating side degrades exactly as it does when the dossier
    # seam is absent … never a fabricated dossier".
    ("delegacao_a2a", effect_classes.RUNG_C3_MUTACAO_ENGINE, effect_classes.SHAPE_DEGRADACAO_SEM_DOSSIE),
    # -- C4 — adverse / money / regulatory ----------------------------------------------------------
    # §6.1 C4 (one row, six classes): "(a) Denied → audited refusal + fail-closed incident + human
    # route. (b) PEP neutralized → the L0-hard NOT_HUMAN guard still refuses (I-6)."
    ("autorizacao_emissao", effect_classes.RUNG_C4_ADVERSO, effect_classes.SHAPE_INCIDENTE_FALHA_FECHADA),
    ("negativa_notificacao", effect_classes.RUNG_C4_ADVERSO, effect_classes.SHAPE_INCIDENTE_FALHA_FECHADA),
    (
        "submissao_regulatoria_ans",
        effect_classes.RUNG_C4_ADVERSO,
        effect_classes.SHAPE_INCIDENTE_FALHA_FECHADA,
    ),
    ("pagamento_emissao", effect_classes.RUNG_C4_ADVERSO, effect_classes.SHAPE_INCIDENTE_FALHA_FECHADA),
    (
        "vinculo_contratual_mudanca",
        effect_classes.RUNG_C4_ADVERSO,
        effect_classes.SHAPE_INCIDENTE_FALHA_FECHADA,
    ),
    (
        "acusacao_fraude_registro",
        effect_classes.RUNG_C4_ADVERSO,
        effect_classes.SHAPE_INCIDENTE_FALHA_FECHADA,
    ),
)


def test_every_class_carries_the_exact_rung_and_denial_shape_design_6_1_assigns() -> None:
    """§6.1's ladder, pinned as a TABLE: all 15 classes, both columns, in both directions.

    Dict equality rather than a per-row loop on purpose — it fails on a swapped shape, a changed
    rung, a class added to the catalogue without a design row, AND a class quietly dropped from
    it. A per-row loop over `ACTION_CLASSES` would miss the last one.
    """
    assert {name: (spec.rung, spec.denial_shape) for name, spec in effect_classes.ACTION_CLASSES.items()} == {
        name: (rung, shape) for name, rung, shape in _DESIGN_6_1_LADDER
    }
    assert len(_DESIGN_6_1_LADDER) == 15, "design §6.1 declares fifteen classes across five rungs"
    assert {rung for _, rung, _ in _DESIGN_6_1_LADDER} == {
        effect_classes.RUNG_C0_LEITURA_INTERNA,
        effect_classes.RUNG_C1_NOTIFICACAO,
        effect_classes.RUNG_C2_PHI_OU_MODELO,
        effect_classes.RUNG_C3_MUTACAO_ENGINE,
        effect_classes.RUNG_C4_ADVERSO,
    }, "every rung of the ramp must be populated — §9.4 flips them C0 -> C4"


def test_no_class_declares_a_pre_effect_audit_yet() -> None:
    """Q-9: `audita_antes: true` adds a durable write on the engine path and needs SRE sign-off."""
    assert [s.name for s in effect_classes.ACTION_CLASSES.values() if s.audita_antes] == []


def test_every_catalogued_tool_id_is_declared_by_some_agent() -> None:
    """The catalogue must describe the tree, not an aspiration: each `mcp-*` id it names is real."""
    declared: set[str] = set()
    for path in sorted((_REPO_ROOT / "spec" / "agents").glob("*/agent.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        declared.update(data.get("tools") or [])
    assert declared >= effect_classes.CATALOGUED_TOOL_IDS


def test_the_memory_tool_gap_is_recorded_not_silently_catalogued() -> None:
    """DISCLOSED GAP. `mcp-memory.read_write` is declared by every agent and has NO class in design
    §6.1. Classifying it is the same human act the manifest reserves for the ~80 unmapped worker
    topics, so the catalogue omits it and it DENIES with `OPERACAO_DESCONHECIDA`. This test exists
    so the omission is a recorded decision rather than an oversight — and so §8.5 item 2's fence
    (every declared tool id resolves to an operation) knows it must declare this exception."""
    assert "mcp-memory.read_write" not in effect_classes.CATALOGUED_TOOL_IDS
    assert effect_classes.lookup_operation("memory.read_write") is None
