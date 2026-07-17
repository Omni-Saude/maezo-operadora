"""Deterministic financial-ceiling resolution for the alcada/auto-approval workers.

T1.9 — closes the "decorative ceiling" defect (B3/B4 family, absorbing the AUTH /
REEMBOLSO / PAGTO auto-approval ceilings): the tenant governance ceilings —
``authorization_approval.max_value_brl``, ``reembolso_auto_approval.max_value_brl``
and ``high_value_payment.threshold_brl`` — were read by ZERO Python. ``dentro_teto_l2``
was defaulted/echoed from an INBOUND process variable, so an upstream that seeded
``dentro_teto_l2=true`` bypassed the ceiling entirely (reembolso.py:227-230, auth.py:66,
pagto.py:123).

:class:`CeilingResolver` loads the SAME autonomy matrix the PEP evaluates
(:func:`maezo.gateway.pep.load_matrix` — L0-core + the tenant overlay) and RECOMPUTES
``dentro_teto_l2`` deterministically by comparing the request value (in centavos)
against the resolved ceiling. The COMPUTED boolean is what the auto-approval DMN
receives as an input — never the inbound boolean.

FAIL-CLOSED invariants (ADR-0005/0008/0018, defense-in-depth — a config problem NEVER
auto-approves):
  - a resolved ceiling of 0 (or absent, or negative, or non-numeric, or a bool) forces
    ``within_l2_ceiling == False``. This is the D-07 state for AUTH/REEMBOLSO
    (``max_value_brl=0`` in ``L0-core.yaml`` and ``tenants-amh.yaml`` until the diretoria
    sets a real teto): every AUTO_APROVAR route falls through to ANALISE_HUMANA;
  - if the matrix cannot be loaded (missing/malformed file, overlay referencing an
    unknown action, unresolvable ``spec/`` directory), the resolver resolves ceiling 0
    → False. Nothing auto-releases when governance config is unavailable. This is the
    resolver's deliberate "swallow-to-False" fail-closed posture, distinct from the PEP
    factory (:func:`maezo.gateway.pep.build_pep`) which *refuses to start* on the same
    input — a PEP that cannot gate must not boot, but a worker that cannot resolve a
    ceiling must still route safely to human review (ADR-0025 D5 / design T1.9 §5.8).

The comparison is ``value_cents <= ceiling_brl * 100`` (inclusive teto — value AT the
ceiling is within; mirrors the DMN row ``<= 10000000``). Money is centavos as an int
(``long`` at the engine boundary), never ``number``/float (ADR-0018 parte 2); ``int`` in
Python is arbitrary-precision so high-value centavos never overflow.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import structlog

from maezo.agents import resolve_spec_dir
from maezo.gateway.pep import AutonomyMatrix, load_matrix

logger = structlog.get_logger(__name__)

#: Narrow env override for the core file, retained only as a test-pinning hook
#: (design T1.9 §2.2). When unset, the default is resolved through the single T0.3
#: mechanism (``resolve_spec_dir``) — the SAME resolution the PEP factory uses, never a
#: second scheme.
_AUTONOMY_CORE_PATH_ENV = "AUTONOMY_CORE_PATH"


def _default_core_path() -> str:
    """Resolve ``<spec>/policies/autonomy/L0-core.yaml`` via the single T0.3 mechanism.

    Reuses :func:`maezo.agents.resolve_spec_dir` (honours ``MAEZO_SPEC_DIR``,
    cwd-independent, fail-closed) — there is no second resolution scheme.
    """
    return str(resolve_spec_dir() / "policies" / "autonomy" / "L0-core.yaml")


@lru_cache(maxsize=64)
def _load_matrix_cached(core_path: str, overlay_path: str | None, tenant: str) -> AutonomyMatrix | None:
    """Load + merge the autonomy matrix (cached by resolved paths + tenant). None on any load error.

    The matrix files are static config; caching avoids re-reading YAML on every external
    task. A load failure (missing/malformed file, overlay tenant mismatch, overlay
    referencing an unknown action) is swallowed and cached as None — the caller then fails
    CLOSED (ceiling 0). Config is static, so a persisted None reflects reality. Exceptions
    are not cached by ``lru_cache``, but a persisted ``None`` return value is; a config that
    is later fixed on disk requires a process restart (acceptable for static governance
    policy, and strictly fail-closed in the interim).
    """
    try:
        return load_matrix(core_path, tenant=tenant, overlay_path=overlay_path)
    except Exception as exc:  # noqa: BLE001 - fail-closed: a config problem never auto-approves
        logger.warning(
            "ceiling_matrix_load_failed",
            core_path=core_path,
            overlay_path=overlay_path,
            tenant=tenant,
            error=str(exc),
        )
        return None


class CeilingResolver:
    """Resolves the tenant governance ceiling for an action and computes the ``dentro_teto_l2`` fact.

    Stateless besides the configured paths; safe to build once per worker registration and
    reuse across tasks (the underlying matrix load is cached). ``overlay_path=None``
    (default) auto-discovers the tenant overlay per task from the ``tenant_id`` carried on
    the process variables.
    """

    def __init__(
        self,
        *,
        core_path: str | Path | None = None,
        overlay_path: str | Path | None = None,
    ) -> None:
        # An explicit core_path pins the file (tests). Otherwise the default is resolved
        # LAZILY (in `_matrix_for`) so importing this module — or constructing a resolver
        # at module scope — never triggers `resolve_spec_dir()` (which fails closed by
        # raising when spec/ is absent). The env override remains a narrow test hook.
        self._core_path_override: str | None = str(core_path) if core_path else None
        # Explicit overlay pins the file (tests); None => per-tenant auto-discovery.
        self._overlay_path: str | None = str(overlay_path) if overlay_path else None

    def _resolved_core_path(self) -> str:
        if self._core_path_override:
            return self._core_path_override
        env_override = os.environ.get(_AUTONOMY_CORE_PATH_ENV)
        if env_override:
            return env_override
        return _default_core_path()

    def _discover_overlay(self, tenant: str, core_path: str) -> str | None:
        """Auto-discover ``<core-dir>/tenants-<tenant>.yaml`` (repo convention). None if absent/no tenant."""
        if not tenant:
            return None
        candidate = Path(core_path).parent / f"tenants-{tenant}.yaml"
        return str(candidate) if candidate.exists() else None

    def _matrix_for(self, tenant: str) -> AutonomyMatrix | None:
        # Resolving the default core path can itself fail closed (resolve_spec_dir raises
        # FileNotFoundError when spec/ is absent). Catch it here so an unresolvable config
        # routes to human review (ceiling 0 -> False) rather than crashing the worker.
        try:
            core_path = self._resolved_core_path()
            overlay = (
                self._overlay_path
                if self._overlay_path is not None
                else self._discover_overlay(tenant, core_path)
            )
        except Exception as exc:  # noqa: BLE001 - fail-closed: unresolvable config never auto-approves
            logger.warning("ceiling_core_path_unresolved", tenant=tenant, error=str(exc))
            return None
        return _load_matrix_cached(core_path, overlay, tenant)

    def ceiling_brl(self, *, tenant: str, action: str, param: str) -> int:
        """Resolved ceiling in whole BRL for ``action.params[param]``. 0 (fail-closed) when absent/invalid.

        ``param`` is ``max_value_brl`` for authorization_approval/reembolso_auto_approval
        and ``threshold_brl`` for high_value_payment.
        """
        matrix = self._matrix_for(tenant)
        if matrix is None:
            return 0
        policy = matrix.policy_for(action)
        if policy is None:
            return 0
        raw = policy.params.get(param)
        # bool is a subclass of int — a YAML `max_value_brl: true` is NOT a valid teto
        # (fail-closed). Reject it before the int() coercion would silently accept it.
        if raw is None or isinstance(raw, bool):
            return 0
        try:
            # Clamp: a negative teto is invalid config — normalised to 0 (fail-closed), so
            # every caller sees "no teto configured". `max()` (not a raise) because this is
            # a config-VALIDITY guard, not a business threshold (ADR-0012).
            return max(0, int(raw))
        except (TypeError, ValueError):
            return 0

    def within_l2_ceiling(self, *, tenant: str, action: str, param: str, value_cents: int) -> bool:
        """True iff ``value_cents`` is within the resolved teto (``value_cents <= ceiling_brl * 100``).

        FAIL-CLOSED: ceiling 0 (absent / D-07 not set / invalid, already normalised by
        :meth:`ceiling_brl`) => False, even for ``value_cents == 0``. The COMPUTED boolean
        is fed to the auto-approval DMN as ``dentro_teto_l2``, overriding any inbound value.
        The teto VALUE comes from the governance matrix (never hard-coded); the RULE that
        consumes ``dentro_teto_l2`` lives in the DMN (ADR-0012) — here we resolve only the
        FACT.
        """
        ceiling_brl = self.ceiling_brl(tenant=tenant, action=action, param=param)
        if ceiling_brl == 0:
            return False
        return value_cents <= ceiling_brl * 100
