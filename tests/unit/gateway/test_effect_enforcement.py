"""Behavioural proofs for the ADDITIVE half of the effect chokepoint (Onda 1, design §7 / §5.7).

Four claims:

  (a) §7.3'S FOUR PRESERVED PROPERTIES STILL HOLD. Making enforcement two-dimensional is only
      safe if none of the existing fences moved: a global `shadow` still means NOTHING enforces, a
      typo in EITHER field still resolves to a non-enforcing state, `status: DRAFT` still makes
      every ALLOW unreachable, and approval is still all three domains by SET EQUALITY.
  (b) THE TWO NAMESPACES CANNOT SHADOW EACH OTHER. A ref declared in both `mapeamento_topicos` and
      `mapeamento_acoes` refuses the WHOLE manifest — YAML's duplicate-key guard cannot see this
      shape, and "last section wins" would silently re-route a class a reviewer already signed.
  (c) THE SPEC-DIR BYPASS (A-6) IS CLOSED, NOT PINNED. `MAEZO_SPEC_DIR` substitutes the entire
      policy plane in one variable, invisibly to the `MAEZO_ACTION_APPROVALS_PATH` fence. Q-6 was
      ratified FAIL-CLOSED by the owner on 2026-08-12 (PLANS §0.8), so production now REFUSES the
      variable outright at `maezo.agents.resolve_spec_dir()` and this loader inherits the refusal
      instead of previewing a substituted plane. The WEAK, evaluate-only form that used to live in
      `_parse` — and the `MAEZO_ACTION_APPROVALS_ALLOW_OVERRIDE_ENFORCEMENT` companion that lifted
      it — are GONE; the tests below pin their absence, not just the new behaviour.
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

from maezo import agents
from maezo.agents import AgentLoader, SpecDirOverrideRefusedError, resolve_spec_agents_dir
from maezo.gateway import action_execution, effect_classes, pep
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
    RUNTIME_MODE_ENV,
    RUNTIME_MODE_ENVS,
    STATUS_RATIFIED,
    TOPIC_MAP_KEY,
    ActionExecutionGateway,
    combined_policy_digest,
    load_action_approvals,
    policy_artifact_digests,
)
from maezo.platform.deploy import engine_deploy

# NOTE: `maezo.platform.privacy.__init__` re-exports a FUNCTION named `phi_key_policy`, which
# shadows the submodule of the same name under BOTH `from … import …` and `import … as …` — so the
# helper is imported by name instead of through the module object.
from maezo.platform.privacy.phi_key_policy import (
    _manifest_default_path as _phi_key_policy_manifest_path,
)
from maezo.tools.workers import adequacao_shadow, auth_criteria, ceilings, tiss_schema, tiss_schema_pin

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
    comprehension (pre-existing behaviour, byte-unchanged reviewed lines). New data, new
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
# (c) The MAEZO_SPEC_DIR bypass (A-6), CLOSED by Q-6 (owner ratification 2026-08-12, PLANS §0.8)
#
# Verbatim intent: "produção deve recusar `MAEZO_SPEC_DIR`, permitindo-o apenas em runtime local
# explícito; remova o companion bypass e aceite futuras exceções somente por bundle imutável com
# digest permitido."
#
# WHAT MOVED. The closure now lives UPSTREAM, at `maezo.agents.resolve_spec_dir()` — the single
# T0.3 chokepoint every policy loader routes through — so it is by CONSTRUCTION rather than
# per-root opt-in. This file's job is therefore two things: prove THIS loader inherits it (it is
# the MZO-040 governance record, the highest-value target of A-6), and prove the two things the
# owner asked to be REMOVED are actually gone. The discriminator's own truth table lives with the
# refusal, in `tests/unit/agents/test_init.py::TestSpecDirRefusedOutsideExplicitLocalRuntime`.
# ---------------------------------------------------------------------------------------------

#: The literal an operator must declare to keep the override working. HARDCODED, never imported
#: from the module under test (provenance: `src/maezo/agents/__init__.py::LOCAL_RUNTIME_MODE`).
_LOCAL = "local"


def _spec_tree(tmp_path: Path, manifest: dict[str, Any]) -> Path:
    autonomy = tmp_path / "spec" / "policies" / "autonomy"
    autonomy.mkdir(parents=True)
    _write(autonomy, manifest)
    return tmp_path / "spec"


def test_a_spec_dir_substituted_manifest_is_refused_outright_in_production(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A-6, CLOSED. One variable swaps action-approvals.yaml, L0-core.yaml, _hard_frozen.yaml AND
    every agent.yaml, and the path-override fence never fires on it — so a forged
    `RATIFICADO` + `enforcing` tree would have ALLOWED and ENFORCED every class.

    Under the WEAK form this test asserted `mode == shadow_override` and `allow is True`: the
    forged tree was still PARSED and still ALLOWED, only `enforced` was withheld. Q-6 replaced
    that with a refusal, so the forged manifest is never read at all.
    """
    monkeypatch.setenv("MAEZO_SPEC_DIR", str(_spec_tree(tmp_path, _manifest())))
    monkeypatch.setenv(RUNTIME_MODE_ENV, "kubernetes")

    with pytest.raises(SpecDirOverrideRefusedError, match="refusing to resolve the policy plane"):
        load_action_approvals()


def test_the_refusal_propagates_instead_of_degrading_to_unresolved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """THE DELIBERATE EXCEPTION to this loader's never-raises contract, stated as behaviour.

    `load_action_approvals` swallows every OTHER failure into `mode=unresolved` + zero approved
    classes, because a raise on a worker path becomes an incident and an incident stalls a care
    request. Degrading the Q-6 refusal the same way would still be fail-closed in EFFECT (nothing
    is approved either way) but it would be QUIET, and the owner ratified a LOUD refusal: an
    operator who substituted the whole policy plane must get the named error, not a service that
    comes up denying everything for reasons that read like a missing file.

    This is the row that catches a "harmonising" edit which folds the refusal back under the
    module's broad `except Exception` — the swallow would leave every other assertion here green.
    """
    monkeypatch.setenv("MAEZO_SPEC_DIR", str(_spec_tree(tmp_path, _manifest())))
    monkeypatch.setenv(RUNTIME_MODE_ENV, "kubernetes")

    with pytest.raises(SpecDirOverrideRefusedError):
        load_action_approvals()
    # And it is not merely "some exception": the named type survives the call boundary.
    assert issubclass(SpecDirOverrideRefusedError, RuntimeError)


def test_an_absent_runtime_mode_refuses_too(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """THE FAIL-CLOSED FLIP the weak pin did not have (`unset => local`, disclosed residual).

    A production pod that forgets to inject a mode variable used to leave the fence DISARMED while
    running an entirely substituted policy plane. Absent is now PRODUCTION, matching the ADR-0039
    Q7 / Train-F default. The autouse `_no_ambient_env` fixture already clears both names, so this
    row asserts the default directly.
    """
    monkeypatch.setenv("MAEZO_SPEC_DIR", str(_spec_tree(tmp_path, _manifest())))

    with pytest.raises(SpecDirOverrideRefusedError):
        load_action_approvals()


@pytest.mark.parametrize("env_name", ["RUNTIME_MODE", "AGENT_RUNTIME_MODE"])
def test_declaring_local_under_either_spelling_keeps_the_override_working(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, env_name: str
) -> None:
    """ "...permitindo-o apenas em runtime local explícito", under BOTH deployment spellings.

    One deployment vocabulary, two spellings. A dev box that declares `RUNTIME_MODE=local` and a
    dev box that declares `AGENT_RUNTIME_MODE=local` must get the same answer — and it must be the
    FULL pre-Q-6 behaviour, `enforcing` included, not a degraded one: the point of the ratification
    is that local is UNCHANGED, so a substituted tree that reads `enforcing` still enforces here.
    """
    monkeypatch.setenv("MAEZO_SPEC_DIR", str(_spec_tree(tmp_path, _manifest())))
    monkeypatch.setenv(env_name, _LOCAL)

    approvals = load_action_approvals()
    assert approvals.mode == MODE_ENFORCING
    assert ActionExecutionGateway(approvals).evaluate(_CLASS).enforced is True


def test_production_without_the_variable_reads_the_shipped_plane_normally(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """THE NON-REGRESSION CONTROL, and the one every deployed daemon depends on.

    Q-6 refuses an OVERRIDE; it does not refuse production. Without this row the suite would still
    be green if `resolve_spec_dir()` simply raised whenever the runtime is production — which would
    take down every pod. Asserts against the SHIPPED record: `status: DRAFT` / `modo: shadow`, so
    it also re-confirms the tree is still inert.
    """
    monkeypatch.delenv("MAEZO_SPEC_DIR", raising=False)
    monkeypatch.setenv(RUNTIME_MODE_ENV, "kubernetes")

    approvals = load_action_approvals()
    assert approvals.degraded is False
    assert approvals.mode == MODE_SHADOW


def test_the_companion_flag_does_not_lift_the_spec_dir_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """THE COMPANION-BYPASS REMOVAL ("remova o companion bypass"), stated as behaviour.

    The weak form resolved a spec-dir-sourced `enforcing` manifest to `shadow_override` UNLESS
    `MAEZO_ACTION_APPROVALS_ALLOW_OVERRIDE_ENFORCEMENT=1` was also set — i.e. one extra variable
    re-opened a total policy-plane substitution, and the previous version of this file had a test
    named `test_the_spec_dir_pin_is_lifted_by_the_same_companion_flag` asserting exactly that.
    Setting the flag now changes nothing.
    """
    monkeypatch.setenv("MAEZO_SPEC_DIR", str(_spec_tree(tmp_path, _manifest())))
    monkeypatch.setenv(RUNTIME_MODE_ENV, "kubernetes")
    monkeypatch.setenv(OVERRIDE_ENFORCEMENT_ENV, OVERRIDE_ENFORCEMENT_ENABLED)

    with pytest.raises(SpecDirOverrideRefusedError):
        load_action_approvals()


def test_the_companion_flag_still_governs_the_path_override_it_was_built_for(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SCOPE CONTROL: Q-6 removed a COUPLING, not the `MANIFEST_PATH_ENV` fence.

    Without this row, "delete the companion flag entirely" would pass the test above and silently
    take the path-override fence's staged-rollout escape with it. `MAEZO_ACTION_APPROVALS_PATH`
    governs ONE file a reviewer can diff, Q-6 did not reach it, and the design cites it as the
    working precedent — so it must still resolve to `shadow_override` without the flag and to
    `enforcing` with it.
    """
    manifest_path = str(_write(tmp_path, _manifest()))
    monkeypatch.delenv("MAEZO_SPEC_DIR", raising=False)
    monkeypatch.setenv(action_execution.MANIFEST_PATH_ENV, manifest_path)

    assert load_action_approvals().mode == MODE_SHADOW_OVERRIDE

    monkeypatch.setenv(OVERRIDE_ENFORCEMENT_ENV, OVERRIDE_ENFORCEMENT_ENABLED)
    action_execution._load_cached.cache_clear()
    assert load_action_approvals().mode == MODE_ENFORCING


def test_no_spec_dir_enforcement_refusal_line_survives_anywhere(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The WEAK form's telemetry is gone with the WEAK form — a dead event name is a lie.

    `action_approvals_spec_dir_enforcement_refused` said "enforcement is WITHHELD until
    MAEZO_ACTION_APPROVALS_ALLOW_OVERRIDE_ENFORCEMENT=1 is also set". Leaving it emittable would
    keep advertising the removed escape hatch to whoever greps the logs.
    """
    monkeypatch.setenv("MAEZO_SPEC_DIR", str(_spec_tree(tmp_path, _manifest())))
    monkeypatch.setenv(RUNTIME_MODE_ENV, _LOCAL)

    events = {e["event"] for e in _load_and_capture(tmp_path)}
    assert "action_approvals_spec_dir_enforcement_refused" not in events


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


@pytest.mark.parametrize("modo", [MODE_ENFORCING, MODE_SHADOW])
@pytest.mark.parametrize("env_name", ["RUNTIME_MODE", "AGENT_RUNTIME_MODE"])
def test_the_spec_dir_provenance_line_is_still_emitted_on_every_local_load(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, modo: str, env_name: str
) -> None:
    """§5.7 item 1 SURVIVES the Q-6 hardening: provenance is still recorded, at `info`.

    WHERE the policy came from and WHETHER it is allowed are two different questions. Production
    now answers the second with a raise, which leaves LOCAL as the only reader of this line — and
    it is still worth writing there, because a local tree that quietly differs from the governed
    one is how a green local suite comes to disagree with CI. `info`, not `error`: every dev box
    sets this variable, and an `error` per load trains people to filter the line away.
    """
    monkeypatch.setenv("MAEZO_SPEC_DIR", str(_spec_tree(tmp_path, _manifest(modo=modo))))
    monkeypatch.setenv(env_name, _LOCAL)

    lines = [e for e in _load_and_capture(tmp_path) if e["event"] == "action_approvals_spec_dir_overridden"]
    assert len(lines) == 1, "the provenance line must be emitted exactly once per load"
    assert lines[0]["log_level"] == "info"
    assert lines[0]["override_env"] == "MAEZO_SPEC_DIR"
    assert lines[0]["explicit_local_runtime"] is True


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

    Both names are now RE-EXPORTS of `maezo.agents`'s single definition rather than a private copy
    this module maintains — so the second assertion below is what proves the re-export did not
    quietly change which spelling `RUNTIME_MODE_ENV` refers to.
    """
    assert RUNTIME_MODE_ENVS == ("RUNTIME_MODE", "AGENT_RUNTIME_MODE")
    assert RUNTIME_MODE_ENV == "AGENT_RUNTIME_MODE"
    assert RUNTIME_MODE_ENVS is agents.RUNTIME_MODE_ENVS, "a second definition has reappeared"


@pytest.mark.parametrize(
    ("runtime_mode", "agent_runtime_mode", "refused"),
    [
        # THE ROW THAT MADE THE TWO-NAME READ A FINDING, and it survives the hardening. A pod
        # inheriting a base-image `RUNTIME_MODE=local` and then labelled by Helm lands here;
        # first-set-wins would read `local` and hand over the entire policy plane.
        (_LOCAL, "kubernetes", True),
        ("kubernetes", _LOCAL, True),  # the mirror image, for the same reason
        ("kubernetes", "kubernetes", True),
        # EXPLICIT LOCAL — the only combination that keeps the override.
        (_LOCAL, _LOCAL, False),
        (_LOCAL, None, False),
        (None, _LOCAL, False),
        # THE FLIP: nothing declared is PRODUCTION now, where the weak pin read it as local.
        (None, None, True),
        # An empty string is UNDECLARED, exactly as `key_scrubber`'s `or` chain treats it.
        ("", "kubernetes", True),
        ("", "", True),
        ("", None, True),
        ("", _LOCAL, False),
        # NO NORMALISATION: a mistyped mode is PRODUCTION and the override is refused.
        (" Local ", None, True),
        ("Local", None, True),
        (None, "LOCAL", True),
    ],
)
def test_the_loader_inherits_the_full_refusal_truth_table(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    runtime_mode: str | None,
    agent_runtime_mode: str | None,
    refused: bool,
) -> None:
    """The discriminator is a DISJUNCTION over declared values, not a first-set-wins lookup.

    Re-asserted THROUGH THIS LOADER rather than only against `is_explicit_local_runtime()`, because
    the property that matters to MZO-040 is what the governance record does, not what a helper
    returns. `key_scrubber.py:117` may legitimately use `or` — it must pick ONE mode to configure a
    pseudonymizer with, so it needs a winner. This decides whether a fence APPLIES, and for that
    the fail-closed reading is "any declaration that is not local means production".
    """
    monkeypatch.setenv("MAEZO_SPEC_DIR", str(_spec_tree(tmp_path, _manifest())))
    for name, value in (("RUNTIME_MODE", runtime_mode), (RUNTIME_MODE_ENV, agent_runtime_mode)):
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)

    if refused:
        with pytest.raises(SpecDirOverrideRefusedError):
            load_action_approvals()
    else:
        assert load_action_approvals().mode == MODE_ENFORCING


def test_the_refusal_never_loses_coverage_relative_to_the_weak_pin_it_replaces(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """THE NON-REGRESSION PROPERTY, stated directly: hardening may not cost coverage.

    Every `(RUNTIME_MODE, AGENT_RUNTIME_MODE)` combination that ARMED the weak pin — i.e. any
    declared non-local value under either name — must now REFUSE. A hardening that traded a
    covered combination for a stronger consequence on the rest would be a net loss, and a truth
    table is easy to re-typo, so the property gets its own assertion rather than relying on the
    rows above surviving a future edit.
    """
    monkeypatch.setenv("MAEZO_SPEC_DIR", str(_spec_tree(tmp_path, _manifest())))
    for other in (None, "", _LOCAL, "kubernetes"):
        for name, value in (("RUNTIME_MODE", other), (RUNTIME_MODE_ENV, "kubernetes")):
            if value is None:
                monkeypatch.delenv(name, raising=False)
            else:
                monkeypatch.setenv(name, value)
        action_execution._load_cached.cache_clear()
        with pytest.raises(SpecDirOverrideRefusedError):
            load_action_approvals()


@pytest.mark.parametrize(
    ("root", "resolve"),
    [
        # THE POLICY PLANE ITSELF — the four loaders an enforcing MZO-040 depends on.
        ("gateway.action_execution.load_action_approvals", load_action_approvals),
        ("gateway.pep._default_autonomy_dir", lambda: pep._default_autonomy_dir()),
        ("tools.workers.ceilings._default_core_path", lambda: ceilings._default_core_path()),
        (
            "tools.workers.auth_criteria._manifest_default_path",
            lambda: auth_criteria._manifest_default_path(),
        ),
        # AGENT CONTRACTS — the agent-runtime boot path (`service.py::_load_definition`), and the
        # reason the agent runtime is covered without this file editing it.
        ("agents.resolve_spec_agents_dir", resolve_spec_agents_dir),
        ("agents.AgentLoader.load_by_id", lambda: AgentLoader().load_by_id("helena")),
        # WORKER-RUNTIME POLICY DATA — reached from worker task handlers, not from this gateway.
        ("tools.workers.tiss_schema._default_schema_root", lambda: tiss_schema._default_schema_root()),
        (
            "tools.workers.tiss_schema_pin._manifest_default_path",
            lambda: tiss_schema_pin._manifest_default_path(),
        ),
        (
            "tools.workers.adequacao_shadow._manifest_default_path",
            lambda: adequacao_shadow._manifest_default_path(),
        ),
        (
            "platform.privacy.phi_key_policy._manifest_default_path",
            _phi_key_policy_manifest_path,
        ),
        # THE OPERATOR TOOL, with NO explicit --spec-dir: it inherits the refusal like everything
        # else. Its sanctioned override is the flag, proven by the row after this table.
        (
            "platform.deploy.engine_deploy.resolve_spec_processes_dir",
            lambda: engine_deploy.resolve_spec_processes_dir(),
        ),
    ],
)
def test_every_production_spec_consumer_refuses(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    root: str,
    resolve: Any,
) -> None:
    """THE COVERAGE CLAIM, per consumer: the closure is by CONSTRUCTION, not per-root opt-in.

    The refusal lives in `resolve_spec_dir()` — the single T0.3 mechanism — rather than being
    wired into each composition root, precisely so a future production entrypoint cannot forget to
    opt in. This table is the evidence for that claim, enumerated from
    `grep -rn "resolve_spec_dir\\|resolve_spec_agents_dir" src/`, and it spans BOTH runtime
    surfaces: the agent runtime (agent contracts + the autonomy plane) and the worker runtime
    (ceilings, TISS pin, DMN ratification, PHI key policy), plus the operator tool.
    """
    monkeypatch.setenv("MAEZO_SPEC_DIR", str(_spec_tree(tmp_path, _manifest())))
    monkeypatch.setenv(RUNTIME_MODE_ENV, "kubernetes")

    with pytest.raises(SpecDirOverrideRefusedError):
        resolve()


def test_the_deploy_tools_explicit_flag_is_the_sanctioned_operator_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """THE OPERATOR-TOOL DISPOSITION, and it is not an exemption.

    A BPMN/DMN deploy is a deliberate act by a human at a CLI, so pointing it at a candidate tree
    is legitimate — but the sanctioned way to say so is `--spec-dir`, which reaches
    `resolve_spec_processes_dir(spec_dir=…)` and never touches `resolve_spec_dir()`. An override a
    reviewer can see in the shell history of the person who ran it is a different artefact from
    one a pod inherits from its environment, and the tool gets only the former: the AMBIENT
    variable is refused here too (row above), even in production, even for this tool.
    """
    explicit = tmp_path / "explicit"
    (explicit / "processes").mkdir(parents=True)
    monkeypatch.setenv("MAEZO_SPEC_DIR", str(_spec_tree(tmp_path, _manifest())))
    monkeypatch.setenv(RUNTIME_MODE_ENV, "kubernetes")

    assert engine_deploy.resolve_spec_processes_dir(explicit) == explicit / "processes"


def test_the_weak_pin_and_its_companion_bypass_are_absent_from_the_source() -> None:
    """SOURCE-LEVEL PROOF OF REMOVAL, because "we deleted it" is a claim, not evidence.

    The two named things the owner asked to be removed are the WEAK evaluate-only leg
    (`spec_dir_sourced`, resolving to `MODE_SHADOW_OVERRIDE`) and the companion flag's coupling to
    it. A behavioural test cannot distinguish "removed" from "still there but currently
    unreachable" — dead code that a later refactor re-arms is exactly how a closed finding
    re-opens — so the absence is asserted against the module text itself.
    """
    import ast
    import inspect

    # THE PARAMETER IS GONE FROM THE API, asserted against the signature rather than the module
    # text: the text still NAMES `spec_dir_sourced`, deliberately, in the comment that records what
    # was removed and why. A grep-based proof would have forced that record to be deleted too,
    # which is how a closed finding loses its provenance.
    assert set(inspect.signature(action_execution._parse).parameters) == {
        "raw_text",
        "manifest_path",
        "override_sourced",
    }, "the weak evaluate-only pin's `spec_dir_sourced` parameter survives"

    source = Path(action_execution.__file__).read_text(encoding="utf-8")
    assert "action_approvals_spec_dir_enforcement_refused" not in source
    # The companion flag survives ONLY for the path override it was built for. Counted over the
    # AST rather than the text, because the text mentions the name in prose too — and a substring
    # count would have been satisfied by a third CALL introduced alongside a deleted comment.
    calls = [
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_override_enforcement_permitted"
    ]
    assert len(calls) == 2, (
        "expected exactly TWO call sites, both on the MANIFEST_PATH_ENV path: the enforcement "
        "fence in `_parse` and the `enforcement_permitted` field of the path-override log line. "
        f"found {len(calls)} at lines {[c.lineno for c in calls]}"
    )
    # And the refusal it was replaced by is imported here rather than reimplemented.
    assert "SpecDirOverrideRefusedError" in source


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


#: The Onda-1 classes whose surfaces the B2 leg actually wired, and therefore flipped to
#: `choked: true`. `leitura_populacional` is ABSENT on purpose — see the assertion below.
_ONDA1_CLASSES_NOW_CHOKED = frozenset(
    {"avaliacao_dmn", "consulta_processo", "correlacao_processo", "inferencia_llm"}
)


def test_the_onda1_classes_are_declared_unapproved_and_choked_matches_reality(
    shipped: dict[str, Any],
) -> None:
    """Declaring a class is NOT approving it — and `choked` must match the WIRING, not the hope.

    This test carried a second claim when B1 landed the data model: `choked` was false everywhere,
    because the seams were not wired yet. B2 wired them, so half of that claim is now FALSE and
    keeping it would make this file assert the opposite of the truth.

    The half that must NEVER weaken is the approval half: five classes DECLARED, all three domains
    required, every block `aprovado: false` with `aprovador: PENDENTE`, and enforcement `shadow`.
    Declaring a class is a code+data act an agent may perform; approving one is not
    (`action-approvals.yaml`: "NO AGENT MAY FILL ANY BLOCK BELOW").

    The half that changed is `choked`, and it changed to a FACT with a stated boundary (§7.2 /
    I-10): true exactly where the B2 wiring chokes the surface, false where it demonstrably does
    not. `leitura_populacional` is the whole reason this is written per-class instead of "all
    true" — its wrapper exists and is tested, but the lake client is PORT-PENDING (WB.4), so
    nothing is injected, nothing is observed, and flipping it would claim shadow evidence that
    does not exist. A class with zero shadow lines is not approvable.

    The per-SURFACE inventory (including the fourth non-Onda-1 exception, the agent-initiated
    process start) lives in `tests/unit/gateway/seams/test_manifest_choked_surfaces.py`, which
    also proves each `choked: true` resolves to a catalogued operation whose construction site
    routes through the registry. This test covers only the Onda-1-declared classes.
    """
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
        # -- UNAPPROVED (unchanged, and must stay unchanged) --------------------------------
        assert set(entry["dominios_exigidos"]) == set(APPROVER_DOMAINS), name
        for domain, block in entry["aprovacoes"].items():
            assert block["aprovado"] is False, f"{name}.{domain}"
            assert block["aprovador"] == "PENDENTE", f"{name}.{domain}"
        assert entry[CLASS_ENFORCEMENT_FIELD] == ENFORCEMENT_SHADOW, name
        # -- CHOKED == reality (B2) ---------------------------------------------------------
        assert entry["superficies"], name
        expected = name in _ONDA1_CLASSES_NOW_CHOKED
        assert all(s["choked"] is expected for s in entry["superficies"]), (
            f"{name}: expected choked={expected} on every surface. A class is either wired (all "
            "its surfaces produce shadow evidence) or it is not — a half-wired class would give "
            "an approver partial evidence they could mistake for complete."
        )


def test_leitura_populacional_stays_unchoked_for_the_recorded_reason(shipped: dict[str, Any]) -> None:
    """The exception above, asserted positively so it cannot be flipped by inertia.

    Truth over completeness (§7.2): the `GatedPopulationFeatureClient` ships and is unit-tested,
    but `spec/agents/andre/agent.yaml` declares no `mcp-datalake` id and no concrete client
    exists (PORT-PENDING WB.4), so `build_agent_seams` produces no `population` key and Andre's
    `build(config)` keeps treating the seam's absence as a disclosed gap note. Flipping this
    would be an evidence claim with nothing behind it.
    """
    surfaces = shipped["acoes"]["leitura_populacional"]["superficies"]
    assert len(surfaces) == 2
    assert all(s["choked"] is False for s in surfaces)
    assert all("PORT-PENDING" in s["detalhe"] or "mesma situação" in s["detalhe"] for s in surfaces), (
        "the reason a surface stays unchoked must be written where an approver reads it"
    )


def test_mapeamento_topicos_is_byte_unchanged_in_content(shipped: dict[str, Any]) -> None:
    """§5.8 / the brief's hard limit: none touched, none reclassified.

    30 entries as of ADR-0040 (was 26), in two halves, one per PR:
      RECURSO (PR-3): REMOVED `operadora.recurso.submit_appeal` (the topic itself was deleted —
        interpor um recurso nao e ato da operadora); ADDED `registrar_indeferimento` +
        `comunicar_resposta` (`negativa_notificacao`) and `handoff_pagamento`
        (`inicio_processo_regulatorio`).
      CONTAS (PR-4): REMOVED `operadora.contas.start_recurso` (same reason, other chain); ADDED
        `registrar_glosa` + `emitir_demonstrativo` (`negativa_notificacao`) and
        `handoff_pagamento` (`inicio_processo_regulatorio`).
    No EXISTING entry was touched or reclassified, and no class was invented.
    """
    assert len(shipped[TOPIC_MAP_KEY]) == 30
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


# DISCLOSED GAP (PR-A, dark landing of the AMH clinical context). The four `mcp-amh.*` operations
# are catalogued (`gateway/effect_classes.py`) and gated (`spec/policies/autonomy/action-approvals.yaml`
# `agente.amh.*` -> `leitura_phi_clinica`; `gateway/amh.py`) BEFORE any agent consumes them: no agent
# graph binds `clinical_context` and no `spec/agents/*/agent.yaml` declares an `mcp-amh.*` tool.
# Landing the gate before the capability is the only safe order (the reverse would be a capability
# without an approval row). Recorded here so the omission is a decision, not an oversight, and
# fail-closed BOTH ways: the day an agent declares one of these ids, `..._gap_is_recorded...`
# below fails and this set must shrink.
CATALOGUED_AMH_IDS_AWAITING_CONSUMER: frozenset[str] = frozenset(
    {
        "mcp-amh.get_subject_context",
        "mcp-amh.list_subject_encounters",
        "mcp-amh.list_subject_conditions",
        "mcp-amh.get_subject_coverage",
    }
)


def _tool_ids_declared_by_agents() -> set[str]:
    declared: set[str] = set()
    for path in sorted((_REPO_ROOT / "spec" / "agents").glob("*/agent.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        declared.update(data.get("tools") or [])
    return declared


def test_every_catalogued_tool_id_is_declared_by_some_agent() -> None:
    """The catalogue must describe the tree, not an aspiration: each `mcp-*` id it names is real —
    except the recorded AMH gap above, which is asserted in the opposite direction right below."""
    declared = _tool_ids_declared_by_agents()
    assert declared >= (effect_classes.CATALOGUED_TOOL_IDS - CATALOGUED_AMH_IDS_AWAITING_CONSUMER)


def test_the_amh_context_gap_is_recorded_not_silently_catalogued() -> None:
    """Two-way fence for the disclosed gap: the ids ARE catalogued (so the gate exists), NO agent
    declares them (so the exception is still needed) and NO agent graph binds `clinical_context`
    (so nothing can reach them). Any of the three changing means this set must shrink."""
    assert CATALOGUED_AMH_IDS_AWAITING_CONSUMER <= effect_classes.CATALOGUED_TOOL_IDS
    declared = _tool_ids_declared_by_agents()
    assert declared.isdisjoint(CATALOGUED_AMH_IDS_AWAITING_CONSUMER), sorted(
        declared & CATALOGUED_AMH_IDS_AWAITING_CONSUMER
    )
    consumers = [
        path
        for path in sorted((_REPO_ROOT / "src" / "maezo" / "agents").rglob("*.py"))
        if "clinical_context" in path.read_text(encoding="utf-8")
    ]
    assert consumers == [], [str(p.relative_to(_REPO_ROOT)) for p in consumers]


def test_the_memory_tool_gap_is_recorded_not_silently_catalogued() -> None:
    """DISCLOSED GAP. `mcp-memory.read_write` is declared by every agent and has NO class in design
    §6.1. Classifying it is the same human act the manifest reserves for the ~80 unmapped worker
    topics, so the catalogue omits it and it DENIES with `OPERACAO_DESCONHECIDA`. This test exists
    so the omission is a recorded decision rather than an oversight — and so §8.5 item 2's fence
    (every declared tool id resolves to an operation) knows it must declare this exception."""
    assert "mcp-memory.read_write" not in effect_classes.CATALOGUED_TOOL_IDS
    assert effect_classes.lookup_operation("memory.read_write") is None
