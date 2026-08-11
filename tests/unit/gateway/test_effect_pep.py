"""Behavioural proofs for the effect chokepoint's decision core (Onda 1, W1/P0).

Six claims, in the order they matter:

  (a) THE LADDER IS TOTAL. Every layer's DENY is reachable and carries a bounded reason AND the
      layer that produced it; `decide` never raises, including when EVERY layer's injected seam
      throws. A gateway that can crash a care path is worse than no gateway (design I-4).
  (b) MOST-RESTRICTIVE-WINS. A call denied at several layers reports the most STRUCTURAL one, so
      an approver can tell "nobody signed this class" from "this agent never had this capability".
  (c) `enforced` IS INDEPENDENT OF `allow`. All four combinations of the two enforcement
      dimensions are proved, because §7.3's whole point is evaluating a class long before it can
      block.
  (d) `EffectCall` CANNOT CARRY PHI. Not "filtered" — structurally rejected at construction, for
      every field, with the PHI shapes this repo actually handles (phone numbers, names, MRNs).
  (e) THE CATALOGUE IS CLOSED, and R-1 IS CLOSED. Unknown operation denies; the per-agent
      `process_keys` ∩ the ADR-0016 universe finally bites.
  (f) THE INERT LEGS ARE HONESTLY INERT. Consent (Q-5) and the money teto are present, tested,
      and provably not firing on any real operation today.

Sibling of `test_effect_enforcement.py`, which proves the loader/manifest half.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from maezo.gateway import action_execution, effect_classes, effect_pep, pep
from maezo.gateway.action_execution import (
    APPROVER_DOMAINS,
    ENFORCEMENT_ENFORCING,
    ENFORCEMENT_SHADOW,
    MODE_ENFORCING,
    MODE_SHADOW,
    REASON_APPROVAL_PENDING,
    REASON_APPROVED,
    STATUS_RATIFIED,
)
from maezo.gateway.effect_classes import ActionClassSpec, OperationSpec
from maezo.gateway.effect_pep import (
    EFFECT_REASONS,
    KNOWN_PROCESS_KEYS,
    REASON_AUTONOMY_DENIED,
    REASON_CAPABILITIES_UNAVAILABLE,
    REASON_CEILING_EXCEEDED,
    REASON_CONSENT_MISSING,
    REASON_HUMAN_REQUIRED,
    REASON_INTERNAL_ERROR,
    REASON_INVALID_CALL,
    REASON_OPERATION_UNKNOWN,
    REASON_POLICY_UNAVAILABLE,
    REASON_PROCESS_KEY_FORBIDDEN,
    REASON_TOOL_UNDECLARED,
    REASON_VOCABULARY_PENDING,
    AgentCapabilities,
    DecisionContext,
    EffectCall,
    EffectCallError,
    EffectLayer,
    decide,
    decide_effect,
    log_effect_decision,
)

_TENANT = "amh"
_PRINCIPAL = "rafael"
_PHI_OP = "fhir.read_patient"
_PHI_TOOL = "mcp-fhir.read_patient"
_PHI_CLASS = "leitura_phi_clinica"
_START_OP = "cibseven.start_process"
_START_TOOL = "mcp-cibseven.start_process"


# ---------------------------------------------------------------------------------------------
# Fixtures — manifests built here, never copied from spec/, so a shipped-file edit cannot
# silently turn these behavioural proofs green (the discipline of the sibling gateway suite).
# ---------------------------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_manifest_cache() -> Any:
    action_execution._load_cached.cache_clear()
    yield
    action_execution._load_cached.cache_clear()


@pytest.fixture(autouse=True)
def _no_ambient_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """No deployment surface may leak into these proofs, in either direction."""
    monkeypatch.delenv(action_execution.MANIFEST_PATH_ENV, raising=False)
    monkeypatch.delenv(action_execution.OVERRIDE_ENFORCEMENT_ENV, raising=False)
    # BOTH spellings of the runtime-mode discriminator (`RUNTIME_MODE_ENVS`): clearing only one
    # left a developer with the other exported able to flip these proofs.
    for name in action_execution.RUNTIME_MODE_ENVS:
        monkeypatch.delenv(name, raising=False)


def _manifest(
    *,
    status: str = "DRAFT",
    modo: str = MODE_SHADOW,
    approved: frozenset[str] = frozenset(),
    enforcement: str = ENFORCEMENT_SHADOW,
    classes: tuple[str, ...] = (_PHI_CLASS, "inicio_processo_regulatorio"),
) -> dict[str, Any]:
    def _block(is_approved: bool) -> dict[str, Any]:
        if not is_approved:
            return {
                "aprovado": False,
                "aprovador": "PENDENTE",
                "data": "PENDENTE",
                "evidencia_ref": "PENDENTE",
            }
        return {
            "aprovado": True,
            "aprovador": "Fixture Approver, fixture role",
            "data": "2026-01-01",
            "evidencia_ref": "fixture://evidence",
        }

    return {
        "version": 1,
        "status": status,
        "modo": modo,
        "acoes": {
            name: {
                "descricao": f"fixture {name}",
                "enforcement": enforcement,
                "dominios_exigidos": sorted(APPROVER_DOMAINS),
                "aprovacoes": {d: _block(name in approved) for d in sorted(APPROVER_DOMAINS)},
            }
            for name in classes
        },
    }


def _write(tmp_path: Path, manifest: dict[str, Any], name: str = "action-approvals.yaml") -> Path:
    path = tmp_path / name
    path.write_text(yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


class _StubPep:
    """A `PepEvaluator` under full test control — raises, or returns whatever it is told to."""

    def __init__(self, verdict: Any = pep.Decision.ALLOW, *, boom: bool = False) -> None:
        self.verdict = verdict
        self.boom = boom
        self.calls: list[str] = []

    def evaluate(self, action: str, agent_context: dict[str, Any] | None = None) -> Any:
        self.calls.append(action)
        if self.boom:
            raise RuntimeError("injected PEP failure")
        return self.verdict


def _caps(*, tools: tuple[str, ...] = (_PHI_TOOL,), keys: tuple[str, ...] = ()) -> AgentCapabilities:
    return AgentCapabilities.of(principal=_PRINCIPAL, tools=tools, process_keys=keys)


def _ctx(
    tmp_path: Path,
    manifest: dict[str, Any] | None = None,
    *,
    name: str = "action-approvals.yaml",
    **kwargs: Any,
) -> DecisionContext:
    """`name` exists because `action_approvals` is lru_cached ON THE PATH: two manifests written
    to the same file inside one test would silently reuse the first load."""
    path = _write(tmp_path, manifest if manifest is not None else _manifest(), name)
    return DecisionContext(approvals_path=path, **kwargs)


# ---------------------------------------------------------------------------------------------
# (a) Ladder totality: every layer's DENY, and the never-raises property
# ---------------------------------------------------------------------------------------------


def test_unknown_operation_denies_at_the_catalogue(tmp_path: Path) -> None:
    """L-0. The catalogue is CLOSED: an operation nobody classified is a DENY, not a pass."""
    decision = decide_effect(
        tenant=_TENANT, principal=_PRINCIPAL, operation="cibseven.delete_everything", ctx=_ctx(tmp_path)
    )
    assert (decision.allow, decision.reason, decision.layer) == (
        False,
        REASON_OPERATION_UNKNOWN,
        EffectLayer.CATALOGO.value,
    )
    assert decision.action_class is None
    assert decision.denial_shape is None, "an unclassifiable call has no declared refusal shape"


def test_absent_capabilities_deny_at_l1(tmp_path: Path) -> None:
    """L-1. Absence of a capability view is never 'allowed' — there is nothing to authorise against."""
    decision = decide_effect(tenant=_TENANT, principal=_PRINCIPAL, operation=_PHI_OP, ctx=_ctx(tmp_path))
    assert (decision.allow, decision.reason, decision.layer) == (
        False,
        REASON_CAPABILITIES_UNAVAILABLE,
        EffectLayer.CAPACIDADE.value,
    )


def test_undeclared_tool_denies_at_l1(tmp_path: Path) -> None:
    """L-1. `agent.yaml`'s `tools:` list becomes load-bearing for the first time."""
    decision = decide_effect(
        tenant=_TENANT,
        principal=_PRINCIPAL,
        operation="whatsapp.send_message",
        ctx=_ctx(tmp_path, capabilities=_caps()),
    )
    assert (decision.allow, decision.reason, decision.layer) == (
        False,
        REASON_TOOL_UNDECLARED,
        EffectLayer.CAPACIDADE.value,
    )


@pytest.mark.parametrize(
    ("declared_keys", "requested"),
    [
        ((), "SP-OP-AUTH-001"),  # the agent declares no key at all
        (("SP-OP-AUTH-001",), "SP-OP-NIP-001"),  # a real key, but not THIS agent's
        (("SP-OP-AUTH-001",), None),  # a start with no key named at all
    ],
)
def test_the_process_key_layer_bites_and_closes_r1(
    tmp_path: Path, declared_keys: tuple[str, ...], requested: str | None
) -> None:
    """L-1 / design R-1. `ProcessAllowlist` had ZERO production importers; now the data enforces.

    Including the "no key at all" case: an engine start with an unnamed process is unauthorizable,
    and treating a missing key as "no restriction" is the fail-open this layer exists to close.
    """
    decision = decide_effect(
        tenant=_TENANT,
        principal=_PRINCIPAL,
        operation=_START_OP,
        process_key=requested,
        ctx=_ctx(tmp_path, capabilities=_caps(tools=(_START_TOOL,), keys=declared_keys)),
    )
    assert (decision.allow, decision.reason, decision.layer) == (
        False,
        REASON_PROCESS_KEY_FORBIDDEN,
        EffectLayer.CAPACIDADE.value,
    )


def test_a_key_outside_the_adr0016_universe_is_dropped_at_capability_construction() -> None:
    """R-1's other half: an `agent.yaml` that invents a key does not thereby grant it."""
    caps = AgentCapabilities.of(
        principal=_PRINCIPAL, tools=(), process_keys=("SP-OP-AUTH-001", "SP-OP-INVENTADO-999", "junk")
    )
    assert caps.process_keys == frozenset({"SP-OP-AUTH-001"})
    assert caps.allows_process_key("SP-OP-INVENTADO-999") is False


def test_nameless_seams_deny_with_vocabulario_pendente(tmp_path: Path) -> None:
    """L-2 / Q-4. Three seams have NO ratified name; the honest record is a would-deny.

    Inventing a name here would be a second vocabulary and a fail-open surface (`pep.py:12-18`),
    and adding one to `L0-core.yaml` is an ADR-0008/0025 act for a human. The PEP is not even
    consulted — there is nothing to ask it about.
    """
    stub = _StubPep()
    ctx = _ctx(tmp_path, capabilities=_caps(tools=()), autonomy=stub)
    for operation in (
        "inference.generate",
        "inference.generate_phi",
        "a2a.delegate",
        "population.actuarial_risk",
        "population.population_metrics",
    ):
        decision = decide_effect(tenant=_TENANT, principal=_PRINCIPAL, operation=operation, ctx=ctx)
        assert (decision.reason, decision.layer) == (
            REASON_VOCABULARY_PENDING,
            EffectLayer.AUTONOMIA.value,
        ), operation
        assert decision.allow is False, operation
    assert stub.calls == [], "the PEP must not be asked about an action that has no ratified name"


def test_the_three_nameless_seams_are_exactly_the_ones_the_design_names() -> None:
    """Q-4's scope is pinned: if a fourth entry loses its name, this test says so."""
    nameless = {
        spec.action_class for spec in effect_classes.OPERATIONS.values() if spec.autonomy_action is None
    }
    assert nameless == {"inferencia_llm", "delegacao_a2a", "leitura_populacional"}


def test_missing_pep_denies_at_l2(tmp_path: Path) -> None:
    """L-2. `build_pep` refuses to start on a broken matrix; a seam built anyway must not allow."""
    decision = decide_effect(
        tenant=_TENANT, principal=_PRINCIPAL, operation=_PHI_OP, ctx=_ctx(tmp_path, capabilities=_caps())
    )
    assert (decision.reason, decision.layer) == (REASON_POLICY_UNAVAILABLE, EffectLayer.AUTONOMIA.value)


@pytest.mark.parametrize(
    ("verdict", "expected"),
    [
        (pep.Decision.DENY, REASON_AUTONOMY_DENIED),
        (pep.Decision.REQUIRE_HUMAN, REASON_HUMAN_REQUIRED),
        ("ALLOW", REASON_AUTONOMY_DENIED),  # a truthy string is NOT permission
        (True, REASON_AUTONOMY_DENIED),  # nor is a truthy bool
        (None, REASON_AUTONOMY_DENIED),
    ],
)
def test_only_the_allow_identity_passes_l2(tmp_path: Path, verdict: Any, expected: str) -> None:
    """L-2. Identity against `Decision.ALLOW`, never a truthiness test.

    `REQUIRE_HUMAN` is a DENY here on purpose: the agent may not proceed autonomously, and
    silently reading a human gate as permission is precisely the fail-open the PEP exists for.
    """
    ctx = _ctx(tmp_path, capabilities=_caps(), autonomy=_StubPep(verdict))
    decision = decide_effect(tenant=_TENANT, principal=_PRINCIPAL, operation=_PHI_OP, ctx=ctx)
    assert (decision.allow, decision.reason, decision.layer) == (
        False,
        expected,
        EffectLayer.AUTONOMIA.value,
    )


# -- L-3: no real operation declares a money teto, so the layer is exercised through a synthetic
# catalogue entry. That is the honest way to prove a leg that is INERT by data today.

_CEILING_OP = OperationSpec(
    operation="fixture.pay",
    action_class="pagamento_emissao",
    autonomy_action="high_value_payment",
    tool_id="mcp-fixture.pay",
    ceiling_action="high_value_payment",
    ceiling_param="threshold_brl",
)


class _StubCeilings:
    def __init__(self, within: Any = True, *, boom: bool = False) -> None:
        self.within = within
        self.boom = boom

    def within_l2_ceiling(self, *, tenant: str, action: str, param: str, value_cents: int) -> bool:
        if self.boom:
            raise RuntimeError("injected ceiling failure")
        return bool(self.within) if isinstance(self.within, bool) else self.within  # type: ignore[return-value]


@pytest.fixture
def _ceiling_catalogue(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(effect_classes, "OPERATIONS", {_CEILING_OP.operation: _CEILING_OP})


@pytest.mark.usefixtures("_ceiling_catalogue")
@pytest.mark.parametrize(
    ("ceilings", "value_cents"),
    [
        (None, 100),  # a declared teto with NO resolver: "cannot check" is never "within"
        (_StubCeilings(True), None),  # a declared teto with no amount to compare
        (_StubCeilings(False), 100),  # over the teto
        (_StubCeilings("yes"), 100),  # a truthy non-bool is not a within-teto answer
    ],
)
def test_l3_fails_closed_on_every_unprovable_ceiling(
    tmp_path: Path, ceilings: Any, value_cents: int | None
) -> None:
    ctx = _ctx(
        tmp_path,
        _manifest(classes=("pagamento_emissao",)),
        capabilities=AgentCapabilities.of(principal=_PRINCIPAL, tools=("mcp-fixture.pay",)),
        autonomy=_StubPep(),
        ceilings=ceilings,
    )
    decision = decide_effect(
        tenant=_TENANT,
        principal=_PRINCIPAL,
        operation=_CEILING_OP.operation,
        value_cents=value_cents,
        ctx=ctx,
    )
    assert (decision.allow, decision.reason, decision.layer) == (
        False,
        REASON_CEILING_EXCEEDED,
        EffectLayer.TETO.value,
    )


def test_no_catalogued_operation_carries_a_money_ceiling_today() -> None:
    """The L-3 leg is INERT by DATA, and that inertness is asserted rather than assumed."""
    with_ceilings = [s.operation for s in effect_classes.OPERATIONS.values() if s.ceiling_action]
    assert with_ceilings == []


# -- L-4: consent, declared and unwired (Q-5).


def test_no_class_requires_consent_so_l4_is_inert(tmp_path: Path) -> None:
    """Q-5 is a human decision. Flagging a class would DENY it until an adapter exists."""
    flagged = [s.name for s in effect_classes.ACTION_CLASSES.values() if s.consentimento_exigido]
    assert flagged == [], "a consent flag was set without an adapter — every call of that class denies"


def test_l4_denies_a_flagged_class_when_no_consent_source_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The MECHANISM works, proven on a synthetic flag — so shipping Q-5 is a data act, not a build."""
    flagged = ActionClassSpec(
        name=_PHI_CLASS,
        rung=effect_classes.RUNG_C2_PHI_OU_MODELO,
        denial_shape=effect_classes.SHAPE_LACUNA_DECLARADA,
        consentimento_exigido=True,
    )
    monkeypatch.setattr(effect_classes, "ACTION_CLASSES", {_PHI_CLASS: flagged})
    ctx = _ctx(tmp_path, capabilities=_caps(), autonomy=_StubPep())
    decision = decide_effect(tenant=_TENANT, principal=_PRINCIPAL, operation=_PHI_OP, ctx=ctx)
    assert (decision.allow, decision.reason, decision.layer) == (
        False,
        REASON_CONSENT_MISSING,
        EffectLayer.CONSENTIMENTO.value,
    )


def test_l5_passes_the_manifests_own_reason_through_unchanged(tmp_path: Path) -> None:
    """L-5. The approver's vocabulary is preserved verbatim; the agent leg invents no synonym."""
    ctx = _ctx(tmp_path, _manifest(status=STATUS_RATIFIED), capabilities=_caps(), autonomy=_StubPep())
    decision = decide_effect(tenant=_TENANT, principal=_PRINCIPAL, operation=_PHI_OP, ctx=ctx)
    assert (decision.allow, decision.reason, decision.layer) == (
        False,
        REASON_APPROVAL_PENDING,
        EffectLayer.RATIFICACAO.value,
    )
    assert decision.denial_shape == effect_classes.SHAPE_LACUNA_DECLARADA


def test_a_fully_approved_class_reaches_allow_and_mints_a_sample_key(tmp_path: Path) -> None:
    """The ALLOW branch exists and is reachable — otherwise every DENY above proves nothing."""
    ctx = _ctx(
        tmp_path,
        _manifest(status=STATUS_RATIFIED, approved=frozenset({_PHI_CLASS})),
        capabilities=_caps(),
        autonomy=_StubPep(),
    )
    decision = decide_effect(tenant=_TENANT, principal=_PRINCIPAL, operation=_PHI_OP, ctx=ctx)
    assert (decision.allow, decision.reason) == (True, REASON_APPROVED)
    assert decision.denial_shape is None
    assert decision.sample_key is not None and len(decision.sample_key) == 32


# -- The never-raises property, per layer.


def _boom(*_args: Any, **_kwargs: Any) -> Any:
    raise RuntimeError("injected failure")


class _BoomCaps:
    principal = _PRINCIPAL

    def allows_tool(self, tool_id: str) -> bool:
        raise RuntimeError("injected capability failure")

    def allows_process_key(self, process_key: str) -> bool:
        raise RuntimeError("injected capability failure")


class _BoomConsent:
    def has_consent(self, *, tenant: str, principal: str, action_class: str) -> bool:
        raise RuntimeError("injected consent failure")


def test_an_exception_at_any_layer_denies_with_a_bounded_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """I-4. A bug in ANY layer is a bounded DENY attributed to that layer — never an exception.

    Per layer: the catalogue lookup, the capability view, the injected PEP, the injected ceiling
    resolver, the injected consent source, and the ratification gateway. Attribution matters as
    much as containment: an operator must know WHICH seam broke, not just that "the gateway" did.
    """
    approved = _manifest(status=STATUS_RATIFIED, approved=frozenset({_PHI_CLASS}))

    # L-0
    with monkeypatch.context() as mp:
        mp.setattr(effect_classes, "lookup_operation", _boom)
        d = decide_effect(tenant=_TENANT, principal=_PRINCIPAL, operation=_PHI_OP, ctx=_ctx(tmp_path))
        assert (d.allow, d.reason, d.layer) == (False, REASON_INTERNAL_ERROR, EffectLayer.CATALOGO.value)

    # L-1
    ctx = _ctx(tmp_path, approved, capabilities=_BoomCaps())
    d = decide_effect(tenant=_TENANT, principal=_PRINCIPAL, operation=_PHI_OP, ctx=ctx)
    assert (d.allow, d.reason, d.layer) == (False, REASON_INTERNAL_ERROR, EffectLayer.CAPACIDADE.value)

    # L-2
    ctx = _ctx(tmp_path, approved, capabilities=_caps(), autonomy=_StubPep(boom=True))
    d = decide_effect(tenant=_TENANT, principal=_PRINCIPAL, operation=_PHI_OP, ctx=ctx)
    assert (d.allow, d.reason, d.layer) == (False, REASON_INTERNAL_ERROR, EffectLayer.AUTONOMIA.value)

    # L-3 (synthetic ceiling-bearing operation, resolver throws)
    with monkeypatch.context() as mp:
        mp.setattr(effect_classes, "OPERATIONS", {_CEILING_OP.operation: _CEILING_OP})
        ctx = _ctx(
            tmp_path,
            _manifest(
                status=STATUS_RATIFIED,
                approved=frozenset({"pagamento_emissao"}),
                classes=("pagamento_emissao",),
            ),
            capabilities=AgentCapabilities.of(principal=_PRINCIPAL, tools=("mcp-fixture.pay",)),
            autonomy=_StubPep(),
            ceilings=_StubCeilings(boom=True),
        )
        d = decide_effect(
            tenant=_TENANT, principal=_PRINCIPAL, operation=_CEILING_OP.operation, value_cents=1, ctx=ctx
        )
        assert (d.allow, d.reason, d.layer) == (False, REASON_INTERNAL_ERROR, EffectLayer.TETO.value)

    # L-4 (synthetic consent flag, source throws)
    with monkeypatch.context() as mp:
        mp.setattr(
            effect_classes,
            "ACTION_CLASSES",
            {
                _PHI_CLASS: ActionClassSpec(
                    name=_PHI_CLASS,
                    rung=effect_classes.RUNG_C2_PHI_OU_MODELO,
                    denial_shape=effect_classes.SHAPE_LACUNA_DECLARADA,
                    consentimento_exigido=True,
                )
            },
        )
        ctx = _ctx(tmp_path, approved, capabilities=_caps(), autonomy=_StubPep(), consent=_BoomConsent())
        d = decide_effect(tenant=_TENANT, principal=_PRINCIPAL, operation=_PHI_OP, ctx=ctx)
        assert (d.allow, d.reason, d.layer) == (
            False,
            REASON_INTERNAL_ERROR,
            EffectLayer.CONSENTIMENTO.value,
        )

    # L-5
    with monkeypatch.context() as mp:
        mp.setattr(action_execution.ActionExecutionGateway, "evaluate", _boom)
        ctx = _ctx(tmp_path, approved, capabilities=_caps(), autonomy=_StubPep())
        d = decide_effect(tenant=_TENANT, principal=_PRINCIPAL, operation=_PHI_OP, ctx=ctx)
        assert (d.allow, d.reason, d.layer) == (
            False,
            REASON_INTERNAL_ERROR,
            EffectLayer.RATIFICACAO.value,
        )


def test_an_unreadable_manifest_denies_rather_than_raising(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The L-5 input is guarded too: a loader that throws degrades to 'nothing is approved'."""
    monkeypatch.setattr(action_execution, "action_approvals", _boom)
    d = decide_effect(
        tenant=_TENANT,
        principal=_PRINCIPAL,
        operation=_PHI_OP,
        ctx=DecisionContext(capabilities=_caps(), autonomy=_StubPep()),
    )
    assert d.allow is False
    assert d.enforced is False, "an unresolvable record must never claim enforcement"
    assert d.reason == action_execution.REASON_MANIFEST_UNAVAILABLE


def test_a_raising_sampler_cannot_change_or_escape_the_decision(tmp_path: Path) -> None:
    """L-6 is best-effort by ADR-0034's own terms: it may never block and never fail the call."""
    seen: list[str] = []

    def sampler(*, tenant: str, principal: str, operation: str, action_class: str, sample_key: str) -> None:
        seen.append(sample_key)
        raise RuntimeError("injected sampler failure")

    ctx = _ctx(
        tmp_path,
        _manifest(status=STATUS_RATIFIED, approved=frozenset({_PHI_CLASS})),
        capabilities=_caps(),
        autonomy=_StubPep(),
        sampler=sampler,
    )
    decision = decide_effect(tenant=_TENANT, principal=_PRINCIPAL, operation=_PHI_OP, ctx=ctx)
    assert decision.allow is True
    assert seen and seen[0] == decision.sample_key


def test_the_sample_key_is_derived_from_bounded_tokens_only(tmp_path: Path) -> None:
    """§5.6. The key must not become a NEW identifier surface: nothing PHI-shaped goes into it."""
    call = EffectCall(
        tenant=_TENANT, principal=_PRINCIPAL, operation=_PHI_OP, action_ref="agente.fhir.read_patient"
    )
    first = effect_pep._sample_key(call, _PHI_CLASS)
    assert first == effect_pep._sample_key(call, _PHI_CLASS), "the key must be stable"
    other = EffectCall(
        tenant="outro", principal=_PRINCIPAL, operation=_PHI_OP, action_ref="agente.fhir.read_patient"
    )
    assert effect_pep._sample_key(other, _PHI_CLASS) != first, "tenants must not collide"


# ---------------------------------------------------------------------------------------------
# (b) Most-restrictive-wins
# ---------------------------------------------------------------------------------------------


def test_a_call_denied_at_several_layers_reports_the_most_structural_one(tmp_path: Path) -> None:
    """§5.3. An approver must tell 'nobody signed this class' from 'this agent never had this'.

    This call fails L-1 (tool not declared), L-2 (the stub PEP denies) AND L-5 (nothing approved).
    The reported layer is L-1, because no later layer may widen — or reword — an earlier refusal.
    """
    ctx = _ctx(tmp_path, capabilities=_caps(tools=()), autonomy=_StubPep(pep.Decision.DENY))
    decision = decide_effect(tenant=_TENANT, principal=_PRINCIPAL, operation=_PHI_OP, ctx=ctx)
    assert (decision.reason, decision.layer) == (REASON_TOOL_UNDECLARED, EffectLayer.CAPACIDADE.value)


def test_removing_the_earlier_denial_reveals_the_next_one(tmp_path: Path) -> None:
    """The counterpart: short-circuiting is real ordering, not an accident of the fixture."""
    ctx = _ctx(tmp_path, capabilities=_caps(), autonomy=_StubPep(pep.Decision.DENY))
    decision = decide_effect(tenant=_TENANT, principal=_PRINCIPAL, operation=_PHI_OP, ctx=ctx)
    assert (decision.reason, decision.layer) == (REASON_AUTONOMY_DENIED, EffectLayer.AUTONOMIA.value)


# ---------------------------------------------------------------------------------------------
# (c) `enforced` is independent of `allow`
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("modo", "enforcement", "expected"),
    [
        (MODE_SHADOW, ENFORCEMENT_SHADOW, False),
        (MODE_SHADOW, ENFORCEMENT_ENFORCING, False),  # the global ceiling still wins
        (MODE_ENFORCING, ENFORCEMENT_SHADOW, False),  # the class has not been flipped
        (MODE_ENFORCING, ENFORCEMENT_ENFORCING, True),
    ],
)
def test_enforced_is_the_conjunction_of_both_dimensions(
    tmp_path: Path, modo: str, enforcement: str, expected: bool
) -> None:
    """§7.3. Progressive enforcement is impossible unless these two dimensions are independent."""
    ctx = _ctx(tmp_path, _manifest(modo=modo, enforcement=enforcement), capabilities=_caps())
    decision = decide_effect(tenant=_TENANT, principal=_PRINCIPAL, operation=_PHI_OP, ctx=ctx)
    assert decision.allow is False, "the fixture approves nothing — `allow` must not move"
    assert decision.enforced is expected


def test_enforced_is_computed_even_on_an_allow(tmp_path: Path) -> None:
    """`allow` and `enforced` answer different questions, and both are answered on every path."""
    ctx = _ctx(
        tmp_path,
        _manifest(
            status=STATUS_RATIFIED,
            modo=MODE_ENFORCING,
            enforcement=ENFORCEMENT_ENFORCING,
            approved=frozenset({_PHI_CLASS}),
        ),
        capabilities=_caps(),
        autonomy=_StubPep(),
    )
    decision = decide_effect(tenant=_TENANT, principal=_PRINCIPAL, operation=_PHI_OP, ctx=ctx)
    assert (decision.allow, decision.enforced) == (True, True)


def test_an_uncatalogued_operation_takes_the_unmapped_enforcement_default(tmp_path: Path) -> None:
    """An operation with no class is an UNMAPPED ref, and takes the root default — never a class's."""
    manifest = _manifest(modo=MODE_ENFORCING, enforcement=ENFORCEMENT_ENFORCING)
    manifest[action_execution.DEFAULT_ENFORCEMENT_KEY] = ENFORCEMENT_SHADOW
    decision = decide_effect(
        tenant=_TENANT, principal=_PRINCIPAL, operation="nada.disso", ctx=_ctx(tmp_path, manifest)
    )
    assert (decision.reason, decision.enforced) == (REASON_OPERATION_UNKNOWN, False)

    manifest[action_execution.DEFAULT_ENFORCEMENT_KEY] = ENFORCEMENT_ENFORCING
    decision = decide_effect(
        tenant=_TENANT,
        principal=_PRINCIPAL,
        operation="nada.disso",
        ctx=_ctx(tmp_path, manifest, name="terminal.yaml"),
    )
    assert (decision.reason, decision.enforced) == (REASON_OPERATION_UNKNOWN, True), (
        "the terminal act of the rollout (§9.4 step 7) must make an unknown ref actually block"
    )


# ---------------------------------------------------------------------------------------------
# (d) `EffectCall` cannot carry PHI
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("tenant", "+55 11 99999-8888"),  # a phone number
        ("tenant", "Maria da Silva"),  # a name
        ("tenant", ""),
        ("tenant", None),
        ("principal", "paciente:123456789"),  # an MRN-shaped id
        ("principal", "a" * 64),  # unbounded length
        ("operation", "fhir.read_patient?patient=123"),  # a query string smuggling an id
        ("operation", "fhir.read_patient/Patient/abc-123"),  # a resource path
        ("operation", "select * from beneficiarios"),
        ("action_ref", "agente.fhir.read_patient#cpf=12345678900"),
        ("autonomy_action", "read phi data"),
        ("process_key", "SP-OP-АUTH-001"),  # Cyrillic А — the homoglyph vector ADR-0016 names
        ("process_key", "sp-op-auth-001"),
        # `\Z`, not `$`: Python's `$` also matches just before a TRAILING NEWLINE, so each of
        # these was accepted as a bounded token and the newline travelled into the telemetry line
        # — a log-injection primitive on the one type whose whole contract is "no free text fits".
        ("tenant", "acme\n"),
        ("principal", "rafael\n"),
        ("operation", "fhir.read_patient\n"),
        ("action_ref", "agente.fhir.read_patient\n"),
        ("autonomy_action", "read_phi_data\n"),
        ("process_key", "SP-OP-AUTH-001\n"),
        ("phi_zone", "confidencial"),
        # A set/dict/list is UNHASHABLE, so the bare `in frozenset` test raised `TypeError` —
        # NOT the `EffectCallError` the class contracts to raise, and therefore invisible to
        # `decide_effect`'s `except EffectCallError` (it landed as GATEWAY_ERRO_INTERNO instead).
        ("phi_zone", {"general"}),
        ("phi_zone", {"zone": "general"}),
        ("phi_zone", ["general"]),
        ("phi_zone", None),
        ("value_cents", -1),
        ("value_cents", True),  # bool is an int in Python; `True` as 1 centavo is the money defect
        ("value_cents", 100.0),
        ("value_cents", "10000"),
    ],
)
def test_effectcall_refuses_every_unbounded_or_phi_shaped_value(field_name: str, value: Any) -> None:
    """I-3 is STRUCTURAL: there is no field where free text fits, and every guard is exercised."""
    fields: dict[str, Any] = {
        "tenant": _TENANT,
        "principal": _PRINCIPAL,
        "operation": _PHI_OP,
        "action_ref": "agente.fhir.read_patient",
    }
    fields[field_name] = value
    with pytest.raises(EffectCallError):
        EffectCall(**fields)


def test_effectcall_has_no_field_that_could_hold_a_payload() -> None:
    """The type itself is the fence: a future field named `payload` fails this test, not a review."""
    assert set(EffectCall.__dataclass_fields__) == {
        "tenant",
        "principal",
        "operation",
        "action_ref",
        "autonomy_action",
        "process_key",
        "value_cents",
        "phi_zone",
    }


def test_a_malformed_call_is_a_bounded_deny_not_an_exception(tmp_path: Path) -> None:
    """`decide_effect` is the wrappers' entry point precisely so this never reaches a care path."""
    decision = decide_effect(
        tenant="Maria da Silva", principal=_PRINCIPAL, operation=_PHI_OP, ctx=_ctx(tmp_path)
    )
    assert (decision.allow, decision.reason, decision.layer) == (
        False,
        REASON_INVALID_CALL,
        EffectLayer.ENTRADA.value,
    )


def test_decide_never_raises_for_any_shaped_input(tmp_path: Path) -> None:
    """The blunt version of I-4, asserted rather than argued — through `decide_effect`."""
    ctx = _ctx(tmp_path)
    for operation in ("", "x", "fhir.read_patient", "nao.existe", "a" * 200):
        for process_key in (None, "SP-OP-AUTH-001", "lixo"):
            decision = decide_effect(
                tenant=_TENANT,
                principal=_PRINCIPAL,
                operation=operation,
                process_key=process_key,
                ctx=ctx,
            )
            assert decision.allow is False
            assert decision.reason in EFFECT_REASONS or decision.reason.isupper()


class _RaisingLogger:
    """A `structlog` logger whose every emit raises — the shape a broken processor chain has."""

    def error(self, *_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("injected logger failure")

    def warning(self, *_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("injected logger failure")

    def info(self, *_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("injected logger failure")


@pytest.mark.parametrize("path_name", ["logger", "denial_shape", "enforcement"])
def test_decide_itself_never_raises_on_the_three_proven_raising_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, path_name: str
) -> None:
    """I-4 asserted against `decide` DIRECTLY, not against the `decide_effect` wrapper around it.

    The module docstring claimed "`decide` has no raising path reachable by a caller — asserted
    directly". It was not: every never-raises proof in this file went through `decide_effect`,
    which had its own outer guard, so the claim held for the ENTRY POINT and not for the function.
    B2 wires the seam wrappers onto `decide`, so the distinction stops being academic.

    The three paths are not hypothetical; each escaped the per-layer guards for a different
    structural reason:

      * `logger` — the guard's OWN `logger.error(...)` runs after it catches, so a raising logger
        re-raises past the very guard that contained the layer. (This also pins that the outermost
        handler's own log is suppressed-guarded; without that it would re-raise identically.)
      * `denial_shape_for` — called inside `_deny`, i.e. AFTER the layer guard has returned.
      * `enforcement_for` — called at the top of the ladder, BEFORE any guard exists at all.
    """
    call = EffectCall(
        tenant=_TENANT, principal=_PRINCIPAL, operation=_PHI_OP, action_ref="agente.fhir.read_patient"
    )
    ctx = _ctx(tmp_path, capabilities=_BoomCaps())

    if path_name == "logger":
        # A layer must ALSO fail, so the guard reaches its `logger.error` — hence `_BoomCaps`.
        monkeypatch.setattr(effect_pep, "logger", _RaisingLogger())
    elif path_name == "denial_shape":
        monkeypatch.setattr(effect_classes, "denial_shape_for", _boom)
    else:
        monkeypatch.setattr(action_execution.ActionApprovals, "enforcement_for", _boom)

    decision = decide(call, ctx)  # <- `decide`, NOT `decide_effect`

    assert (decision.allow, decision.reason, decision.layer) == (
        False,
        REASON_INTERNAL_ERROR,
        EffectLayer.ENTRADA.value,
    )
    assert decision.action_class is None and decision.denial_shape is None
    assert decision.enforced is False, "a shadow manifest may never enforce, not even on a crash"


#: The FOUR unanticipated-failure guards a wrapper can land in, one per entry point/fault pair.
#: `decide` has one (its outermost); `decide_effect` sits in FRONT of it and has three of its own,
#: catching faults `decide` never sees. Each is `(id, entry_point, how to inject the fault)`.
_GUARD_PATHS: tuple[str, ...] = (
    "decide:outermost",  # a layer AND the logger fail -> `decide`'s own outermost guard
    "decide_effect:lookup",  # `lookup_operation` raises before the call is even built (guard 1)
    "decide_effect:action_ref",  # `agent_action_ref` raises inside the construction (guard 3)
    "decide_effect:decide_escape",  # `decide` itself escapes despite being total (guard 4)
)


@pytest.mark.parametrize("modo", [MODE_SHADOW, MODE_ENFORCING])
@pytest.mark.parametrize("guard", _GUARD_PATHS)
def test_the_outermost_guard_still_blocks_under_a_live_global_enforcement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, modo: str, guard: str
) -> None:
    """The deviation-8 posture, on EVERY unanticipated-failure guard of BOTH entry points.

    `_outermost_enforcement` re-reads the GLOBAL ceiling from the cached manifest and deliberately
    does NOT apply `enforcement_padrao_nao_mapeado` — a decision about unclassified traffic must
    not downgrade a gateway FAILURE. The fixture pins that root key to `shadow` explicitly, which
    is what makes the assertion discriminating.

    PARAMETRIZED OVER BOTH ENTRY POINTS because they were not agreeing. `decide_effect`'s own three
    guards hardcoded `enforced=False`, so the SAME fault under the SAME live `modo: enforcing`
    blocked or did not block purely by which function a wrapper happened to call — `decide` said
    ENFORCED, `decide_effect` said not. B2's wrappers call `decide_effect` while the incident-shape
    proofs (§9.3) are written against `decide`, so a disagreement here is a disagreement between
    what gets proved and what gets shipped.
    """
    manifest = _manifest(modo=modo, enforcement=ENFORCEMENT_ENFORCING)
    manifest[action_execution.DEFAULT_ENFORCEMENT_KEY] = ENFORCEMENT_SHADOW
    ctx = _ctx(
        tmp_path,
        manifest,
        name=f"outermost-{modo}-{guard.replace(':', '-')}.yaml",
        capabilities=_BoomCaps(),
    )

    if guard == "decide:outermost":
        # A layer must fail AND the guard's own `logger.error` must re-raise past it.
        monkeypatch.setattr(effect_pep, "logger", _RaisingLogger())
        call = EffectCall(
            tenant=_TENANT, principal=_PRINCIPAL, operation=_PHI_OP, action_ref="agente.fhir.read_patient"
        )
        decision = decide(call, ctx)
        expected_layer = EffectLayer.ENTRADA.value
    else:
        if guard == "decide_effect:lookup":
            monkeypatch.setattr(effect_classes, "lookup_operation", _boom)
            expected_layer = EffectLayer.CATALOGO.value
        elif guard == "decide_effect:action_ref":
            monkeypatch.setattr(effect_classes, "agent_action_ref", _boom)
            expected_layer = EffectLayer.ENTRADA.value
        else:
            # `decide_effect` resolves `decide` as a module global at call time, so this reaches
            # the guard that exists for "the total function was not total after all".
            monkeypatch.setattr(effect_pep, "decide", _boom)
            expected_layer = EffectLayer.ENTRADA.value
        decision = decide_effect(tenant=_TENANT, principal=_PRINCIPAL, operation=_PHI_OP, ctx=ctx)

    assert (decision.allow, decision.reason, decision.layer) == (
        False,
        REASON_INTERNAL_ERROR,
        expected_layer,
    )
    assert decision.mode == modo, f"{guard}: the GLOBAL mode must be re-read from the manifest"
    assert decision.enforced is (modo == MODE_ENFORCING), (
        f"{guard}: a gateway failure must block under a live global enforcement, on every guard "
        "and through either entry point"
    )
    assert decision.enforcement == (
        ENFORCEMENT_ENFORCING if modo == MODE_ENFORCING else ENFORCEMENT_SHADOW
    ), f"{guard}: the per-class dimension must not be applied where no class was resolved"


def test_both_entry_points_agree_on_an_unanticipated_fault(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The property the test above proves per-row, stated once as an equality.

    A wrapper author must never have to know which entry point yields the safer verdict.
    """
    manifest = _manifest(modo=MODE_ENFORCING, enforcement=ENFORCEMENT_ENFORCING)
    manifest[action_execution.DEFAULT_ENFORCEMENT_KEY] = ENFORCEMENT_SHADOW

    verdicts: dict[str, tuple[bool, str, str]] = {}
    for index, guard in enumerate(("decide", "decide_effect")):
        with monkeypatch.context() as mp:
            ctx = _ctx(tmp_path, manifest, name=f"agree-{index}.yaml", capabilities=_BoomCaps())
            if guard == "decide":
                mp.setattr(effect_pep, "logger", _RaisingLogger())
                call = EffectCall(
                    tenant=_TENANT,
                    principal=_PRINCIPAL,
                    operation=_PHI_OP,
                    action_ref="agente.fhir.read_patient",
                )
                d = decide(call, ctx)
            else:
                mp.setattr(effect_classes, "agent_action_ref", _boom)
                d = decide_effect(tenant=_TENANT, principal=_PRINCIPAL, operation=_PHI_OP, ctx=ctx)
            verdicts[guard] = (d.enforced, d.mode, d.enforcement)

    assert verdicts["decide"] == verdicts["decide_effect"] == (True, MODE_ENFORCING, ENFORCEMENT_ENFORCING)


def test_the_outermost_guard_refuses_to_claim_enforcement_when_nothing_loads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail-closed in BOTH directions: no readable record means `unresolved`, never `enforcing`."""
    monkeypatch.setattr(action_execution.ActionApprovals, "enforcement_for", _boom)
    monkeypatch.setattr(action_execution, "action_approvals", _boom)
    call = EffectCall(
        tenant=_TENANT, principal=_PRINCIPAL, operation=_PHI_OP, action_ref="agente.fhir.read_patient"
    )
    decision = decide(call, DecisionContext(capabilities=_caps(), autonomy=_StubPep()))
    assert (decision.reason, decision.mode, decision.enforced) == (
        REASON_INTERNAL_ERROR,
        action_execution.MODE_UNRESOLVED,
        False,
    )


# ---------------------------------------------------------------------------------------------
# Vocabulary, telemetry, and the mirrored constants
# ---------------------------------------------------------------------------------------------


def test_every_reason_token_is_bounded_and_non_phi() -> None:
    """The same discipline the worker leg's enum already carries: safe in the clear, always."""
    for reason in EFFECT_REASONS:
        assert action_execution._is_bounded_token(reason), reason


def test_every_layer_token_is_bounded() -> None:
    for layer in EffectLayer:
        assert action_execution._is_bounded_token(layer.value), layer


def test_the_mirrored_process_key_constants_match_the_adr0016_source() -> None:
    """The duplication is deliberate (layering); this is what keeps it from becoming a SECOND
    vocabulary. Same technique as `pep.HARD_ACTIONS` vs `_hard_frozen.yaml`."""
    from maezo.tools import process_allowlist

    assert KNOWN_PROCESS_KEYS == process_allowlist.KNOWN_PROCESS_KEYS
    assert effect_pep._PROCESS_KEY_RE.pattern == process_allowlist._PROCESS_KEY_PATTERN.pattern


def test_the_mirrored_token_regex_matches_the_gateways_own() -> None:
    assert effect_pep._TOKEN_RE.pattern == action_execution._TOKEN_RE.pattern


def test_every_mirrored_pattern_anchors_at_the_true_end_of_the_string() -> None:
    """ONDA 1 GK nit. `$` is a trailing-newline hole; `\\Z` is not, and BOTH sides were tightened.

    The two cross-check tests above only prove the patterns are IDENTICAL — two identical holes
    pass them. This asserts the property itself, on all three, so a future edit that reintroduces
    `$` on either side of a mirror fails here rather than passing the equality check in lockstep.
    """
    for pattern in (
        effect_pep._TOKEN_RE,
        effect_pep._DOTTED_TOKEN_RE,
        effect_pep._PROCESS_KEY_RE,
        action_execution._TOKEN_RE,
    ):
        assert pattern.pattern.endswith(r"\Z"), pattern.pattern
        assert not pattern.pattern.endswith("$"), pattern.pattern

    from maezo.tools import process_allowlist

    assert process_allowlist._PROCESS_KEY_PATTERN.pattern.endswith(r"\Z")

    # …and the tightening is STRICTLY NARROWING: every real value still validates.
    assert (
        EffectCall(
            tenant=_TENANT,
            principal=_PRINCIPAL,
            operation=_START_OP,
            action_ref="agente.cibseven.start_process",
            autonomy_action="start_compliance_process",
            process_key="SP-OP-AUTH-001",
        ).process_key
        == "SP-OP-AUTH-001"
    )


def test_the_ceiling_protocol_is_satisfied_by_the_real_resolver() -> None:
    """The Protocol is not a fiction: `CeilingResolver` satisfies it with no edit to `ceilings.py`."""
    from maezo.tools.workers.ceilings import CeilingResolver

    resolver: effect_pep.CeilingEvaluator = CeilingResolver(core_path="/nonexistent/L0-core.yaml")
    assert (
        resolver.within_l2_ceiling(
            tenant="amh", action="high_value_payment", param="threshold_brl", value_cents=1
        )
        is False
    )


def test_the_pep_protocol_is_satisfied_by_the_real_pep() -> None:
    matrix = pep.AutonomyMatrix(tenant="core", actions={})
    evaluator: effect_pep.PepEvaluator = pep.PEP(matrix)
    assert evaluator.evaluate("nao_existe") is pep.Decision.DENY


def test_the_telemetry_line_carries_only_bounded_tokens(tmp_path: Path) -> None:
    """§9.2 counts lines by decision × reason × tenant; every dimension must be safe in the clear."""
    import structlog

    cap = structlog.testing.LogCapture()
    structlog.configure(processors=[cap])
    try:
        call = EffectCall(
            tenant=_TENANT, principal=_PRINCIPAL, operation=_PHI_OP, action_ref="agente.fhir.read_patient"
        )
        decision = decide(call, _ctx(tmp_path, capabilities=_caps(), autonomy=_StubPep()))
        log_effect_decision(decision, call)
    finally:
        structlog.reset_defaults()
    entry = cap.entries[-1]
    assert entry["event"] == action_execution.EVENT_SHADOW
    for key in ("operation", "action_ref", "principal", "action_class", "reason", "layer", "tenant"):
        assert action_execution._is_bounded_topic(entry[key]), (key, entry[key])


@pytest.mark.parametrize(
    ("modo", "enforcement", "expected_event", "expected_enforced"),
    [
        (MODE_SHADOW, ENFORCEMENT_SHADOW, action_execution.EVENT_SHADOW, False),
        (MODE_SHADOW, ENFORCEMENT_ENFORCING, action_execution.EVENT_SHADOW, False),
        (MODE_ENFORCING, ENFORCEMENT_SHADOW, action_execution.EVENT_SHADOW, False),
        (MODE_ENFORCING, ENFORCEMENT_ENFORCING, action_execution.EVENT_ENFORCED, True),
    ],
)
def test_the_agent_leg_telemetry_event_name_tracks_the_mode(
    tmp_path: Path, modo: str, enforcement: str, expected_event: str, expected_enforced: bool
) -> None:
    """The worker leg's `test_the_telemetry_event_name_tracks_the_mode`, mirrored onto this leg.

    A shadow observation and a call that actually blocked are different EVENTS, not one event with
    a field: log routing, alerting and dashboards key on the event name long before anything
    parses `mode=`, so emitting `..._shadow` for a call that was really refused would make the
    first enforced denial in production invisible to every alert built on the shadow rollout. The
    worker leg pinned this; the agent leg's `log_effect_decision` did not, and the two emit into
    the SAME event family on purpose (§9.2 counts the lines together).

    Both fields are still emitted, so nothing that filtered on them stops working — and all four
    corners of §7.3's conjunction are driven through real manifests, not a hand-built decision, so
    the event name is pinned to `enforced` and to the DATA that produces it at the same time.
    """
    import structlog

    ctx = _ctx(
        tmp_path,
        _manifest(modo=modo, enforcement=enforcement),
        name=f"telemetry-{modo}-{enforcement}.yaml",
        capabilities=_caps(),
        autonomy=_StubPep(),
    )
    call = EffectCall(
        tenant=_TENANT, principal=_PRINCIPAL, operation=_PHI_OP, action_ref="agente.fhir.read_patient"
    )
    decision = decide(call, ctx)
    assert decision.enforced is expected_enforced
    assert decision.allow is False, "the fixture approves nothing — only the EVENT NAME is at issue"

    cap = structlog.testing.LogCapture()
    structlog.configure(processors=[cap])
    try:
        log_effect_decision(decision, call)
    finally:
        structlog.reset_defaults()

    lines = [
        e
        for e in cap.entries
        if e["event"] in (action_execution.EVENT_SHADOW, action_execution.EVENT_ENFORCED)
    ]
    assert [e["event"] for e in lines] == [expected_event]
    assert lines[0]["mode"] == modo, "the `mode` field must survive alongside the event name"
    assert lines[0]["enforcement"] == enforcement, "§9.4 step 5: BOTH dimensions are emitted"
    assert lines[0]["decision"] == "WOULD_DENY"
