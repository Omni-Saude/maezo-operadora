"""The four canonical L0 action NAMES (owner decision R-114, executing PLANS.md §0.8 Q-4).

SPEC-level assertions against the REAL shipped `spec/policies/autonomy/action-approvals.yaml` —
never a fixture copy of it. Sibling of `test_action_execution_fence.py`, same posture.

WHAT THE OWNER DECIDED. Q-4 ("quatro ações canônicas, SEM aliases") was ratified without the
strings; R-114 fixes the strings as DATA in the manifest's `vocabulario_l0_canonico` block. The
whole point of the decision is that naming is CHEAP — it buys the vocabulary for the PRs that come
after, and it buys nothing else. These tests are what makes "and nothing else" checkable.

The claims pinned here:

  1. the block names EXACTLY the four canonical actions, and its `operacoes` coverage is exactly
     the set of catalogued operations that have NO ratified autonomy name — recomputed from
     `effect_classes.OPERATIONS`, never from a second hand-kept list;
  2. inference is split BY ZONE into two distinct names (ADR-0006), which is the "×2" half of the
     decision and the half a single shared name would silently undo;
  3. NAMING IS NOT INSTALLING: no canonical name is in `L0-core.yaml` or `_hard_frozen.yaml`,
     every entry says `instalado_em_l0_core: false`, and every ratification field is `PENDENTE`.
     Installing a name in the matrix is an ADR-0008/0025 act for a human, with the
     `_hard_frozen.yaml` cross-check — an agent writing one here would be forging it;
  4. no canonical name ALIASES an existing matrix action (Q-4's actual prohibition, `pep.py`:
     "an alias map is a second vocabulary and a fail-open surface");
  5. the shipped manifest stays `status: DRAFT` + `modo: shadow` — naming changed neither;
  6. THE FLOOR, proved at its strongest point: on a FORGED copy of the real manifest — `status:
     RATIFICADO`, `modo: enforcing`, every domain block filled `aprovado: true`, every class
     `enforcement: enforcing` — every one of the named operations STILL denies, at L-2, with
     `VOCABULARIO_PENDENTE`. Naming opens no ALLOW path anywhere, and cannot be made to.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from maezo.gateway import action_execution, effect_classes, pep
from maezo.gateway.action_execution import (
    APPROVER_DOMAINS,
    MODE_SHADOW,
    REASON_APPROVED,
    STATUS_RATIFIED,
)
from maezo.gateway.effect_pep import (
    REASON_VOCABULARY_PENDING,
    AgentCapabilities,
    DecisionContext,
    EffectLayer,
    decide_effect,
)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_AUTONOMY_DIR = _REPO_ROOT / "spec" / "policies" / "autonomy"
_SHIPPED = _AUTONOMY_DIR / "action-approvals.yaml"
_L0_CORE = _AUTONOMY_DIR / "L0-core.yaml"
_HARD_FROZEN = _AUTONOMY_DIR / "_hard_frozen.yaml"

#: The block this file exists for.
_BLOCK = "vocabulario_l0_canonico"

#: The machine-detectable placeholder the manifest header defines: "The literal string
#: `PENDENTE` is the machine-detectable placeholder: a field left as `PENDENTE` (or blank, or
#: null) means NOT approved, even alongside `aprovado: true`."
_PENDING = "PENDENTE"

_TENANT = "amh"
_PRINCIPAL = "rafael"


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
    for name in action_execution.RUNTIME_MODE_ENVS:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def shipped() -> dict[str, Any]:
    return yaml.safe_load(_SHIPPED.read_text(encoding="utf-8"))


@pytest.fixture
def block(shipped: dict[str, Any]) -> dict[str, Any]:
    declared = shipped.get(_BLOCK)
    assert isinstance(declared, dict), f"{_SHIPPED} must declare the '{_BLOCK}' mapping"
    return declared


def _nameless_operations() -> frozenset[str]:
    """The operations with NO ratified autonomy name, recomputed from the closed catalogue."""
    return frozenset(
        operation for operation, spec in effect_classes.OPERATIONS.items() if spec.autonomy_action is None
    )


class _AllowPep:
    """A `PepEvaluator` that ALLOWS everything — the positive control's autonomy leg.

    Used only to prove the forged manifest is NOT vacuous: a named operation must sail past L-2
    on it. The nameless ones never reach the PEP at all, which is itself asserted.
    """

    def __init__(self) -> None:
        self.calls: list[str] = []

    def evaluate(self, action: str, agent_context: dict[str, Any] | None = None) -> Any:
        self.calls.append(action)
        return pep.Decision.ALLOW


# -------------------------------------------------------------------------------------------------
# 1-2. What the block names, and the per-zone split
# -------------------------------------------------------------------------------------------------


def test_the_block_names_exactly_the_four_canonical_l0_actions(block: dict[str, Any]) -> None:
    """R-114 / Q-4. Four names, no more and no fewer, each a distinct string."""
    assert sorted(block) == [
        "delegate_agent_task",
        "generate_model_inference",
        "generate_model_inference_phi",
        "read_population_aggregate",
    ]


def test_the_named_operations_are_exactly_the_ones_with_no_ratified_name(
    block: dict[str, Any],
) -> None:
    """Coverage is DERIVED, not transcribed: the tree decides what needs a name.

    An operation that loses (or gains) `autonomy_action` in `effect_classes.OPERATIONS` without a
    matching edit here turns this red. A hand-kept second list would have gone quietly stale
    instead — which is the failure mode this repo keeps finding in its own registers.
    """
    named: list[str] = []
    for canonical, entry in block.items():
        operations = entry["operacoes"]
        assert isinstance(operations, list) and operations, canonical
        named.extend(operations)

    assert len(named) == len(set(named)), f"an operation is named twice: {sorted(named)}"
    assert frozenset(named) == _nameless_operations()


def test_each_entry_points_at_a_declared_class_the_catalogue_agrees_with(
    shipped: dict[str, Any], block: dict[str, Any]
) -> None:
    """A name belongs to the class its own operations belong to — checked against the catalogue."""
    for canonical, entry in block.items():
        action_class = entry["classe"]
        assert action_class in shipped["acoes"], f"{canonical}: undeclared class {action_class!r}"
        for operation in entry["operacoes"]:
            assert effect_classes.OPERATIONS[operation].action_class == action_class, (
                f"{canonical}: {operation} belongs to "
                f"{effect_classes.OPERATIONS[operation].action_class!r}, not {action_class!r}"
            )


def test_inference_is_named_per_zone_with_two_distinct_names(block: dict[str, Any]) -> None:
    """The "×2" half of the decision, asserted positively so it cannot collapse by inertia.

    ADR-0006's two zones are the reason the catalogue splits `inference.generate` from
    `inference.generate_phi` at all. One shared name would re-merge them in the vocabulary while
    the catalogue still split them — the exact drift `sem alias` was decided against.
    """
    zoned = {name: entry["zona"] for name, entry in block.items() if "zona" in entry}
    assert sorted(zoned) == ["generate_model_inference", "generate_model_inference_phi"]
    assert sorted(zoned.values()) == ["geral", "phi"]
    assert block["generate_model_inference"]["operacoes"] == ["inference.generate"]
    assert block["generate_model_inference_phi"]["operacoes"] == ["inference.generate_phi"]


# -------------------------------------------------------------------------------------------------
# 3-4. Naming is not installing, and no name aliases an existing one
# -------------------------------------------------------------------------------------------------


def test_no_canonical_name_is_installed_in_the_autonomy_matrix(block: dict[str, Any]) -> None:
    """Naming is a DONO act with no effect; installing is an ADR-0008/0025 act for a human.

    While this holds, `effect_classes` keeps `autonomy_action=None` for these operations and L-2
    denies with `VOCABULARIO_PENDENTE`. An agent that "helpfully" added a name to `L0-core.yaml`
    would be performing the human vocabulary act — and skipping the `_hard_frozen.yaml`
    cross-check that act requires.
    """
    matrix = yaml.safe_load(_L0_CORE.read_text(encoding="utf-8"))["actions"]
    frozen = {
        item["action"] for item in yaml.safe_load(_HARD_FROZEN.read_text(encoding="utf-8"))["hard_items"]
    }
    for canonical, entry in block.items():
        assert canonical not in matrix, f"{canonical} is installed in L0-core.yaml — human act"
        assert canonical not in frozen, f"{canonical} is in _hard_frozen.yaml — human act"
        assert entry["instalado_em_l0_core"] is False, canonical

    for operation in _nameless_operations():
        assert effect_classes.OPERATIONS[operation].autonomy_action is None, operation


def test_no_canonical_name_aliases_an_existing_matrix_action(block: dict[str, Any]) -> None:
    """Q-4's actual prohibition: a name that reuses an existing one is an alias, not a name.

    `read_phi_data` is the concrete trap the manifest calls out by name — it is the nearest
    existing action to `leitura_populacional` and it is WRONG, because a k-anonymous aggregate is
    not PHI (ADR-0042). Reusing it would have been the cheap edit and the fail-open one.
    """
    matrix = set(yaml.safe_load(_L0_CORE.read_text(encoding="utf-8"))["actions"])
    assert matrix.isdisjoint(set(block)), (
        f"canonical name(s) collide with the matrix: {sorted(matrix & set(block))} — "
        "an alias map is a second vocabulary and a fail-open surface (pep.py)"
    )


#: The closed key set the block itself declares, TRANSCRIBED by hand from the shipped block —
#: a literal by design, NOT derived. Deriving this set from the very file the fence checks
#: would make the fence vacuous (it would accept any key the file happens to carry); it proves
#: something only because the literal is independent of the data. Every entry has exactly
#: these keys, `zona` present only on the two inference names; every `ratificacao` sub-block
#: has exactly these four accountability keys.
_ENTRY_KEYS = {"classe", "zona", "operacoes", "descricao", "instalado_em_l0_core", "ratificacao"}
_RATIFICACAO_KEYS = {"ratificado", "ratificador", "data", "evidencia_ref"}


def test_every_ratification_field_is_the_pending_placeholder(block: dict[str, Any]) -> None:
    """The header's "NO AGENT MAY FILL ANY BLOCK BELOW." reaches this block in the same letter.

    Closed-key fence (VER-R114 F1): an unknown key — an `aprovado` smuggled into an entry, or a
    stray field inside `ratificacao` — would be invisible to the assertions below unless the key
    SET itself is pinned first. `<=` (not `==`) on the entry because `zona` is legitimately absent
    from the two non-inference entries (`test_inference_is_named_per_zone_with_two_distinct_names`
    already pins which entries carry it).
    """
    for canonical, entry in block.items():
        assert set(entry) <= _ENTRY_KEYS, f"{canonical}: unknown key(s) {set(entry) - _ENTRY_KEYS}"
        ratification = entry["ratificacao"]
        assert set(ratification) == _RATIFICACAO_KEYS, f"{canonical}.ratificacao: {set(ratification)}"
        assert ratification["ratificado"] is False, canonical
        for field in ("ratificador", "data", "evidencia_ref"):
            assert ratification[field] == _PENDING, f"{canonical}.{field}"


# -------------------------------------------------------------------------------------------------
# 5-6. The floor: DRAFT/shadow unchanged, and no ALLOW even on a forged ratified copy
# -------------------------------------------------------------------------------------------------


def test_naming_left_the_manifest_draft_and_shadow(shipped: dict[str, Any]) -> None:
    """The outer fence is untouched by the naming act — asserted here, not assumed."""
    assert shipped["status"] != STATUS_RATIFIED
    assert shipped["status"] == "DRAFT"
    assert shipped["modo"] == MODE_SHADOW


def _forged_ratified_copy(tmp_path: Path) -> Path:
    """The REAL manifest, forged as hard as a forger could: ratified, enforcing, all approved."""
    manifest = yaml.safe_load(_SHIPPED.read_text(encoding="utf-8"))
    manifest["status"] = STATUS_RATIFIED
    manifest["modo"] = "enforcing"
    for entry in manifest["acoes"].values():
        entry["enforcement"] = "enforcing"
        entry["aprovacoes"] = {
            domain: {
                "aprovado": True,
                "aprovador": "Forged Approver, forged role",
                "data": "2026-01-01",
                "evidencia_ref": "forged://evidence",
            }
            for domain in sorted(APPROVER_DOMAINS)
        }
    path = tmp_path / "forged-action-approvals.yaml"
    path.write_text(yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


def test_a_forged_ratified_manifest_still_denies_every_named_operation(tmp_path: Path) -> None:
    """THE FLOOR. Naming buys vocabulary, never an ALLOW — not even under a total forgery.

    The forgery is the strongest one the data model permits: the file says RATIFICADO, the mode
    says enforcing, and all three domains signed every class. The named operations still deny at
    L-2, because the name is not IN the matrix — and the PEP is not even consulted, so there is no
    seam to stub into an ALLOW either.
    """
    forged = _forged_ratified_copy(tmp_path)
    stub = _AllowPep()
    ctx = DecisionContext(
        approvals_path=forged,
        capabilities=AgentCapabilities.of(principal=_PRINCIPAL, tools=("mcp-fhir.read_patient",)),
        autonomy=stub,
    )

    for operation in sorted(_nameless_operations()):
        decision = decide_effect(tenant=_TENANT, principal=_PRINCIPAL, operation=operation, ctx=ctx)
        assert decision.allow is False, operation
        assert (decision.reason, decision.layer) == (
            REASON_VOCABULARY_PENDING,
            EffectLayer.AUTONOMIA.value,
        ), operation
    assert stub.calls == [], "a nameless action must never be asked of the PEP"

    # POSITIVE CONTROL — without this the assertions above would also pass on a manifest that
    # simply failed to load. A NAMED operation of an approved class reaches ALLOW on this very
    # copy, so the forgery is real and the denials above are about the missing NAME, nothing else.
    control = decide_effect(tenant=_TENANT, principal=_PRINCIPAL, operation="fhir.read_patient", ctx=ctx)
    assert (control.allow, control.reason) == (True, REASON_APPROVED)
    assert stub.calls == ["read_phi_data"]
