"""Policy Enforcement Point — evaluates the autonomy matrix before every tool call.

ADR-0005, ADR-0008, ADR-0025 (PEP ↔ policy unification, T1.8), DL-0005.

The PEP is the structural enforcement of the HITL guarantees. It resolves each
action against the autonomy matrix (levels L0-L3) loaded from
``spec/policies/autonomy/*.yaml`` and returns ``ALLOW``, ``DENY``, or
``REQUIRE_HUMAN``.

Design of record (ADR-0025):

* **Single vocabulary — English.** The canonical action names are those declared
  in ``spec/policies/autonomy/L0-core.yaml`` (and mirrored by every
  ``agent.yaml`` allowlist and ``_hard_frozen.yaml``). The former Portuguese
  ``pep.py`` dict is retired; any retired name is now an *unknown* action and
  fail-closes to ``DENY`` (there is no PT↔EN alias layer — an alias map is a
  second vocabulary and a fail-open surface).
* **The matrix is loaded from YAML, not hard-coded.** ``load_matrix`` parses
  ``L0-core.yaml`` (+ an optional per-tenant overlay), enforcing the invariants
  in code — never trusting the YAML. The spec directory is resolved through the
  single T0.3 mechanism (``maezo.agents.resolve_spec_dir`` / ``MAEZO_SPEC_DIR``);
  there is no second resolution scheme.
* **Fail-closed everywhere (constraint 2).** Unknown action → ``DENY``. A missing
  file (``FileNotFoundError``), unparseable YAML (``yaml.YAMLError``) or a
  structurally invalid policy (``PolicyError``) all normalise, at the
  :func:`build_pep` factory boundary, into a refuse-to-start ``PolicyError`` — a
  PEP that cannot load its matrix must not construct, so the process never boots
  in a state where it cannot gate anything.
* **Hardness is code-frozen.** :data:`HARD_ACTIONS` (the frozen 5) is the source
  of truth for "untouchable". Even if a YAML ``hard: true`` were dropped, the
  code re-imposes it and a core missing a hard item refuses to load. Overlays can
  never touch a hard item.

Scope note (ADR-0025 D6 / Q4): ``evaluate`` is kept **synchronous** for T1.8 to
minimise blast radius; the v1 donor's async ``PolicyEnforcementPoint`` also does
audited-refusal recording and L2 review sampling (depends on ``audit.py`` /
``l2_sampling.py``). Porting those is a flagged follow-up, not part of the T1.8
acceptance (YAML loading + single vocabulary + complete hard set). Ceiling
enforcement of the L2 ``max_value_brl`` params is T1.9 (``CeilingResolver``) and
is deliberately not implemented here.
"""

from __future__ import annotations

import enum
from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import structlog
import yaml

from maezo.agents import resolve_spec_dir

logger = structlog.get_logger(__name__)

# L0-hard actions — the frozen 5, English canonical (ADR-0025 D3). Immutable,
# code-frozen, never YAML-configurable: this frozenset is the source of truth for
# "untouchable". It is cross-checked against `_hard_frozen.yaml` at load time
# (defense-in-depth) but can never be *lowered* by any YAML/flag/overlay.
#
# - clinical_decision:    diagnostico/conduta by an agent is prohibited (never).
# - authorization_denial: coverage denial requires a medical auditor (RN 259).
# - nip_manter_negativa:  MANTER_NEGATIVA embeds a coverage denial
#                         (authorization_denial-class) — frozen in PARITY with
#                         authorization_denial (GAP-NIP-3/GAP-XHITL-3).
# - fraud_accusation:     "never accuses on its own" — structural evidence + human.
# - contract_termination: contract rescission is never performed by an agent.
HARD_ACTIONS: frozenset[str] = frozenset(
    {
        "clinical_decision",
        "authorization_denial",
        "nip_manter_negativa",
        "fraud_accusation",
        "contract_termination",
    }
)

# Restriction order: L0 (most restrictive) → L3 (least). "Raise restriction" =
# move to a smaller index; "loosen" = move to a larger index (rejected in overlays).
_LEVEL_ORDER: tuple[str, ...] = ("L0", "L1", "L2", "L3")
_LEVEL_RANK: dict[str, int] = {lvl: i for i, lvl in enumerate(_LEVEL_ORDER)}

_FROZEN_FILENAME = "_hard_frozen.yaml"


class Level(enum.StrEnum):
    """Autonomy level of an action (ADR-0008)."""

    L0 = "L0"
    L1 = "L1"
    L2 = "L2"
    L3 = "L3"


class Decision(enum.Enum):
    """PEP decision for a tool call."""

    ALLOW = "ALLOW"
    DENY = "DENY"
    REQUIRE_HUMAN = "REQUIRE_HUMAN"


class PolicyError(ValueError):
    """A malformed policy, or an attempt to override a frozen/hard item, was detected."""


@dataclass(frozen=True, slots=True)
class ActionPolicy:
    """Resolved level of an action + whether it is ``hard`` (non-lowerable) + params."""

    action: str
    level: Level
    hard: bool
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AutonomyMatrix:
    """A resolved matrix (L0-core + optional tenant overlay), ready for evaluation."""

    tenant: str
    actions: Mapping[str, ActionPolicy]

    def policy_for(self, action: str) -> ActionPolicy | None:
        return self.actions.get(action)


# --- Loading and merging the matrix --------------------------------------------------


def _load_yaml(path: Path) -> dict[str, Any]:
    """Read + parse one policy YAML file.

    Raises (raw, distinct failure classes — ADR-0025 D5):
        FileNotFoundError: the file is missing (from ``Path.read_text``).
        yaml.YAMLError: the file exists but is not syntactically valid YAML.
        PolicyError: the file parses but its root is not a mapping (incl. an
            *empty* file, which parses to ``None``).
    """
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise PolicyError(f"invalid policy YAML (root is not a mapping): {path}")
    return data


def _parse_core(core_raw: dict[str, Any]) -> dict[str, ActionPolicy]:
    """Parse the L0-core mapping into resolved ActionPolicy entries.

    Enforces, in code (never trusting the YAML):
    * every entry under ``actions:`` carries a ``level`` in {L0, L1, L2, L3};
    * effective ``hard`` = YAML ``hard`` ∪ code-frozen :data:`HARD_ACTIONS`;
    * defense-in-depth: every :data:`HARD_ACTIONS` member must be present AND L0.
    """
    actions_raw = core_raw.get("actions")
    if not isinstance(actions_raw, dict):
        raise PolicyError("L0-core has no `actions` mapping")
    out: dict[str, ActionPolicy] = {}
    for name, spec in actions_raw.items():
        if not isinstance(spec, dict) or "level" not in spec:
            raise PolicyError(f"action `{name}` has no `level`")
        try:
            level = Level(str(spec["level"]))
        except ValueError as exc:
            raise PolicyError(f"action `{name}` has invalid level `{spec['level']}`") from exc
        # Effective `hard` = union of the YAML declaration with the code-frozen set.
        # A HARD_ACTIONS member is hard even if the YAML "forgets" it — code wins.
        hard = bool(spec.get("hard", False)) or name in HARD_ACTIONS
        params = dict(spec.get("params") or {})
        out[str(name)] = ActionPolicy(action=str(name), level=level, hard=hard, params=params)
    # Defense-in-depth: every code-frozen hard item must exist in the core and be L0.
    for hard_name in HARD_ACTIONS:
        pol = out.get(hard_name)
        if pol is None:
            raise PolicyError(f"hard item `{hard_name}` missing from L0-core")
        if pol.level is not Level.L0:
            raise PolicyError(f"hard item `{hard_name}` is not L0 in the core")
    return out


def _load_frozen_actions(frozen_path: Path) -> frozenset[str]:
    """Parse ``_hard_frozen.yaml`` into the set of frozen action names.

    Raises:
        PolicyError: the file is structurally invalid (non-mapping root, or a
            missing / non-list ``hard_items``, or an entry without ``action``).
        yaml.YAMLError: the file is not syntactically valid YAML.
        FileNotFoundError: the file does not exist.
    """
    data = _load_yaml(frozen_path)
    items = data.get("hard_items")
    if not isinstance(items, list):
        raise PolicyError(f"`hard_items` missing or not a list in {frozen_path}")
    names: set[str] = set()
    for entry in items:
        if not isinstance(entry, dict) or "action" not in entry:
            raise PolicyError(f"invalid hard_items entry in {frozen_path}: {entry!r}")
        names.add(str(entry["action"]))
    return frozenset(names)


def _assert_hard_frozen_matches(core_path: Path) -> None:
    """Assert the code-frozen HARD_ACTIONS equals the ``_hard_frozen.yaml`` set.

    Only runs when a ``_hard_frozen.yaml`` sits beside the core file (always true
    for the real spec; synthetic test cores without it fall back to the
    ``_parse_core`` defense-in-depth check). A mismatch is a governance/compliance
    regression → ``PolicyError`` (refuse to load).
    """
    frozen_path = core_path.parent / _FROZEN_FILENAME
    if not frozen_path.exists():
        return
    frozen = _load_frozen_actions(frozen_path)
    if frozen != HARD_ACTIONS:
        missing_in_code = frozen - HARD_ACTIONS
        missing_in_yaml = HARD_ACTIONS - frozen
        raise PolicyError(
            "code-frozen HARD_ACTIONS does not match "
            f"{frozen_path.name}: only-in-yaml={sorted(missing_in_code)} "
            f"only-in-code={sorted(missing_in_yaml)}"
        )


def _apply_overlay(core: dict[str, ActionPolicy], overlay_raw: dict[str, Any]) -> dict[str, ActionPolicy]:
    """Apply a tenant overlay: it may only RAISE restriction on a non-hard action.

    Rules enforced in code (we never trust the YAML):
    * ``hard`` item: the overlay may not touch level, ``hard``, or ``params`` —
      any such key → ``PolicyError`` (L0 hard is not overridable, full stop).
    * non-hard item: the overlay may only move to a *more* restrictive level
      (smaller rank); loosening (larger rank) → ``PolicyError``. ``params`` may
      be refined; removing ``hard`` → ``PolicyError``.
    * an override referencing an action absent from the core → ``PolicyError``
      (guards against a typo'd overlay silently configuring nothing).
    """
    overrides = overlay_raw.get("overrides") or {}
    if not isinstance(overrides, dict):
        raise PolicyError("overlay `overrides` is malformed")

    merged = dict(core)
    for name, spec in overrides.items():
        base = core.get(name)
        if base is None:
            raise PolicyError(f"overlay references unknown action `{name}`")
        if not isinstance(spec, dict):
            raise PolicyError(f"override of `{name}` is malformed")

        if base.hard:
            # Nothing in an overlay may alter a hard item.
            if "level" in spec or "hard" in spec or "params" in spec:
                raise PolicyError(f"overlay tried to modify hard item `{name}` — forbidden (ADR-0008)")
            continue

        new_level = base.level
        if "level" in spec:
            try:
                candidate = Level(str(spec["level"]))
            except ValueError as exc:
                raise PolicyError(f"override of `{name}` has invalid level `{spec['level']}`") from exc
            if _LEVEL_RANK[candidate] > _LEVEL_RANK[base.level]:
                raise PolicyError(
                    f"overlay tried to LOOSEN `{name}` "
                    f"({base.level} → {candidate}) — an overlay may only raise restriction"
                )
            new_level = candidate

        if "hard" in spec and bool(spec["hard"]) is False:
            raise PolicyError(f"overlay tried to remove hard from `{name}`")

        new_params = dict(base.params)
        if "params" in spec:
            if not isinstance(spec["params"], dict):
                raise PolicyError(f"params of `{name}` is malformed")
            new_params.update(spec["params"])

        merged[name] = ActionPolicy(
            action=name,
            level=new_level,
            hard=base.hard or bool(spec.get("hard", False)),
            params=new_params,
        )
    return merged


def load_matrix(
    core_path: str | Path,
    *,
    tenant: str,
    overlay_path: str | Path | None = None,
) -> AutonomyMatrix:
    """Load L0-core (+ optional tenant overlay) into a resolved matrix.

    Invariants are enforced in code: the code-frozen hard set must match
    ``_hard_frozen.yaml`` (when present beside the core), every hard item must be
    present and L0, and the overlay may never lower a hard item nor loosen a
    non-hard one.

    Exceptions are **raw** here (each distinct failure class is observable — see
    :func:`_load_yaml`); the :func:`build_pep` factory is responsible for
    normalising them into a single refuse-to-start error.
    """
    core_path = Path(core_path)
    core = _parse_core(_load_yaml(core_path))
    _assert_hard_frozen_matches(core_path)
    if overlay_path is not None:
        overlay_raw = _load_yaml(Path(overlay_path))
        declared = overlay_raw.get("tenant")
        if declared is not None and str(declared) != tenant:
            raise PolicyError(f"overlay is for tenant `{declared}`, expected `{tenant}`")
        core = _apply_overlay(core, overlay_raw)
    return AutonomyMatrix(tenant=tenant, actions=core)


@lru_cache(maxsize=64)
def _load_matrix_cached(core_path: str, tenant: str, overlay_path: str | None) -> AutonomyMatrix:
    """Process-wide cache of resolved matrices, keyed by (core, tenant, overlay).

    ``load_matrix`` reads static config; caching avoids re-parsing per PEP
    construction. Exceptions are not cached by ``lru_cache``, so a failing load
    re-raises on every call (fail-closed).
    """
    return load_matrix(core_path, tenant=tenant, overlay_path=overlay_path)


def _default_autonomy_dir() -> Path:
    """Resolve ``<spec>/policies/autonomy`` via the single T0.3 mechanism.

    Reuses ``maezo.agents.resolve_spec_dir`` (honours ``MAEZO_SPEC_DIR``,
    cwd-independent, fail-closed) — there is no second resolution scheme.
    """
    return resolve_spec_dir() / "policies" / "autonomy"


def build_pep(
    *,
    tenant: str = "core",
    core_path: str | Path | None = None,
    overlay_path: str | Path | None = None,
) -> PEP:
    """Construct a PEP with an eagerly-loaded, cached autonomy matrix.

    This is the fail-closed factory (ADR-0025 D5). It resolves the policy
    directory through ``resolve_spec_dir()``, auto-discovers a per-tenant overlay
    (``tenants-<tenant>.yaml`` beside the core, when present), loads the matrix,
    and normalises **all three** load failure classes —
    ``FileNotFoundError`` (missing file), ``yaml.YAMLError`` (syntax error), and
    ``PolicyError`` (structural error) — into a single refuse-to-start
    ``PolicyError``. A PEP with no valid matrix must not construct.

    Args:
        tenant: Tenant whose overlay to apply. The default ``"core"`` has no
            overlay file, so the bare L0-core matrix is loaded.
        core_path: Override for the core file (defaults to the resolved
            ``<spec>/policies/autonomy/L0-core.yaml``). Narrow test-pinning hook.
        overlay_path: Explicit overlay path, bypassing auto-discovery. Pass to
            pin a specific overlay in tests.

    Raises:
        PolicyError: if the matrix cannot be loaded for any reason (missing,
            unparseable, or structurally invalid) — refuse to start.
    """
    try:
        autonomy_dir = _default_autonomy_dir()
        resolved_core = Path(core_path) if core_path is not None else autonomy_dir / "L0-core.yaml"

        resolved_overlay: Path | None
        if overlay_path is not None:
            resolved_overlay = Path(overlay_path)
        else:
            candidate = autonomy_dir / f"tenants-{tenant}.yaml"
            resolved_overlay = candidate if candidate.exists() else None

        matrix = _load_matrix_cached(
            str(resolved_core),
            tenant,
            str(resolved_overlay) if resolved_overlay is not None else None,
        )
    except PolicyError:
        raise
    except (FileNotFoundError, yaml.YAMLError) as exc:
        # Normalise the two non-PolicyError load classes into refuse-to-start.
        raise PolicyError(f"PEP refused to start — autonomy matrix could not be loaded: {exc}") from exc
    return PEP(matrix)


# --- The Policy Enforcement Point ----------------------------------------------------


class PEP:
    """Policy Enforcement Point — resolves an action against a loaded autonomy matrix.

    Level → decision (fail-closed order, ADR-0025 D5):

    * L0-hard        → ``DENY`` (agent may never perform it under any circumstance)
    * L0-non-hard/L1 → ``REQUIRE_HUMAN`` (agent prepares a dossier / proposes; human executes/approves)
    * L2/L3          → ``ALLOW`` (agent executes autonomously within bounds)
    * unknown action → ``DENY`` (fail-closed)

    The matrix is **constructor-injected** (ADR-0025 D2); use :func:`build_pep`
    to obtain a PEP whose matrix is resolved from ``spec/policies/autonomy/``.
    """

    def __init__(self, matrix: AutonomyMatrix) -> None:
        self._matrix = matrix

    @property
    def matrix(self) -> AutonomyMatrix:
        return self._matrix

    def evaluate(self, action: str, agent_context: dict[str, Any] | None = None) -> Decision:
        """Evaluate whether ``action`` is allowed for the given agent context.

        Args:
            action: The canonical (English) action name being requested
                (e.g. ``"authorization_denial"``, ``"triage_and_routing"``).
            agent_context: Optional dict with agent_id, level, tenant, etc. Used
                only for audit logging; it does not influence the decision.

        Returns:
            ``Decision.ALLOW``, ``Decision.DENY``, or ``Decision.REQUIRE_HUMAN``.
            Never returns ``None`` and never raises for an unknown action.
        """
        # 1) L0-hard actions are structurally denied — defense-in-depth: the code
        #    frozenset denies even if a matrix somehow lost the `hard` flag.
        if action in HARD_ACTIONS:
            logger.warning("pep_deny_l0_hard", action=action, agent_context=agent_context)
            return Decision.DENY

        # 2) Resolve the action against the loaded matrix.
        policy = self._matrix.policy_for(action)
        if policy is None:
            # Unknown action (incl. any retired PT name): fail-closed → DENY.
            logger.warning("pep_deny_unknown_action", action=action, agent_context=agent_context)
            return Decision.DENY

        # 3) Level → decision.
        if policy.level is Level.L0 and policy.hard:
            logger.warning("pep_deny_l0_hard", action=action, agent_context=agent_context)
            return Decision.DENY
        if policy.level in (Level.L0, Level.L1):
            logger.info(
                "pep_require_human", action=action, level=policy.level.value, agent_context=agent_context
            )
            return Decision.REQUIRE_HUMAN

        # L2 / L3 → ALLOW. (L2 ceiling enforcement of `params.max_value_brl` is T1.9.)
        logger.info("pep_allow", action=action, level=policy.level.value, agent_context=agent_context)
        return Decision.ALLOW
