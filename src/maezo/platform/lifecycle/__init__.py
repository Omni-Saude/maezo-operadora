"""Intentional fail-closed refusal entrypoint for `python -m maezo.platform.lifecycle` [T2.8].

This package exists to make a currently-DANGEROUS gap LOUD and EXPLICIT instead of
either (a) crashing inscrutably with `ModuleNotFoundError`, or (b) one day silently
succeeding — and running a destructive, unlawful audit-chain prune — the moment
someone fills the module in.

Background (all grounded on `main`):

- The Helm CronJob `lifecycle-audit-retention`
  (`deploy/helm/maezo-tenant/templates/cronjob-lifecycle.yaml` +
  `deploy/helm/maezo-tenant/values.yaml`, schedule "0 5 1 * *") invokes
  `python -m maezo.platform.lifecycle audit-retention` monthly. Two sibling jobs
  (`expurgo-working`, `verify-erasure`) invoke the same module with different
  subcommands. NONE of the three is implemented.
- Before this module, `maezo.platform.lifecycle` did not exist. The only thing
  keeping the destructive path closed was the `ModuleNotFoundError` the CronJob pod
  hit at start — an accident, not a designed guard
  (`docs/compliance/ADR-0020-amendment-draft.md` §3).
- `maezo.platform.retention.RetentionManager.retention_query()`
  (`src/maezo/platform/retention.py:132-143`) builds an UNCONDITIONAL
  `DELETE FROM audit_chain WHERE ts < cutoff` with:
    * NO legal-hold predicate (there is no legal-hold registry anywhere in the
      tree) — deleting evidence under an active hold is a spoliation risk
      (ADR-0020 item 6; `ADR-0020-amendment-draft` §1-§2).
    * NO chain re-anchoring — deleting the genesis-anchored prefix severs
      `verify_chain()`'s contiguity, so BOTH verifiers report the surviving chain
      as corrupted (`src/maezo/gateway/audit.py:310-344` fails at index 0;
      `src/maezo/gateway/audit_postgres.py:380-406` reports every survivor
      "unreachable from genesis"). See `ADR-0020-amendment-draft` §3-bis and ADR-0029.

Per the ADR-0020 amendment draft, NO prune may run without BOTH (1) a legal-hold
registry and (2) a signed-checkpoint re-anchor mechanism (ADR-0029). Neither exists.
Therefore every invocation of this entrypoint REFUSES and exits non-zero. That is the
deliverable: fail-closed by design, not by accident.

DESIGN NOTES
------------
- This module deliberately does NOT import `RetentionManager` / `retention_query`
  and builds NO `DELETE` statement. Importing this subpackage runs the parent
  `maezo.platform.__init__`, which binds `RetentionManager` for OTHER consumers —
  that binding is not a caller of the destructive query and is unrelated to this
  refusal path. `retention_query()` has zero production callers, a property locked in
  CI by `tests/unit/platform/test_lifecycle.py`.
- It DOES import `legal_bases_matrix` (this package, `legal_bases_matrix.py`) — a
  completely different concern: a typed loader for the DPO's per-category
  legal-bases/retention matrix (PLANS.md §0.5 item 6). `expurgo-working` and
  `verify-erasure` attempt a real load of that matrix so their refusal message can
  distinguish "matrix absent" from "matrix present but the downstream execution
  mechanism (ErasureManager's per-layer deletion, the working-layer TTL sweep) is
  simply unbuilt". Loading the matrix is read-only and never touches
  `retention.py`/audit_chain — it has nothing to do with the audit-retention blocker.
- Importing this module has NO side effect (no work at import time) — matches the
  `gateway` / `webhooks` / `*_runtime` `__main__` convention.
- Implementing `audit-retention` is BLOCKED until BOTH
  `docs/compliance/ADR-0020-amendment-draft.md` and
  `docs/adr/0029-audit-chain-pruning-reanchor.md` are ratified. Implementing
  `expurgo-working`/`verify-erasure` additionally requires a ratified DPO
  legal-bases/retention matrix (see `legal_bases_matrix.py`) deployed at
  `MAEZO_RETENTION_MATRIX_PATH`; `verify-erasure` further needs the
  thread_id→fhir_patient_id mapping design gap (`erasure.py`) closed. Blocked != done.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence

import structlog

from .legal_bases_matrix import (
    MATRIX_PATH_ENV,
    RetentionMatrixUnavailableError,
    load_retention_matrix,
)

logger = structlog.get_logger(__name__)

MODULE_NAME = "maezo.platform.lifecycle"

# sysexits.h EX_CONFIG (78): "something was found in an unconfigured or misconfigured
# state" — precisely this case: the prerequisites for any lifecycle command are absent.
# A stable, non-zero, non-1 code so operators can tell a DELIBERATE refusal apart from a
# generic crash (exit 1) or a Python import failure.
REFUSAL_EXIT_CODE = 78

# The three subcommands the CronJobs invoke (deploy/helm/maezo-tenant/values.yaml
# `lifecycle.jobs`). All are refused: NONE is implemented on `main`. `audit-retention`
# is refused with a specific, ADR-grounded blocker; the others are refused as
# not-implemented fail-closed stubs.
SUBCMD_AUDIT_RETENTION = "audit-retention"
SUBCMD_EXPURGO_WORKING = "expurgo-working"
SUBCMD_VERIFY_ERASURE = "verify-erasure"
KNOWN_SUBCOMMANDS = (
    SUBCMD_EXPURGO_WORKING,
    SUBCMD_VERIFY_ERASURE,
    SUBCMD_AUDIT_RETENTION,
)

# Exact operator-facing blocker for the destructive path (charter-specified verbatim).
AUDIT_RETENTION_REFUSAL = (
    "audit-retention refused: prerequisites absent — legal-hold registry "
    "(ADR-0020 amendment) + chain re-anchor mechanism (ADR-0029); "
    "see docs/compliance/ADR-0020-amendment-draft.md"
)

_ADR_POINTERS = (
    "see docs/adr/0029-audit-chain-pruning-reanchor.md and docs/compliance/ADR-0020-amendment-draft.md"
)

# Schema-only, UNRATIFIED-marked template showing the shape a real matrix must have —
# pointed at by the matrix-gated refusal messages below so an operator knows where to
# look, never treated as a usable matrix itself (the loader refuses it explicitly).
_MATRIX_TEMPLATE_PATH = "spec/policies/retention/UNRATIFIED-retention-matrix.template.yaml"

# `verify-erasure` has a second, independent blocker beyond the matrix: erasure.py's
# per-layer `_erase_*`/`_verify_*` helpers key on `thread_id`, not `fhir_patient_id` —
# there is no mapping from one to the other today (erasure.py:196-206). This is a code
# gap, not a human-ratification gap, and stays true regardless of matrix state.
_VERIFY_ERASURE_EXTRA = (
    " verify-erasure is additionally blocked, independent of the matrix, on the "
    "thread_id→fhir_patient_id mapping design gap in ErasureManager (see erasure.py)."
)


def _matrix_gated_refusal(subcommand: str, mechanism: str, extra: str = "") -> str:
    """Build the refusal message for a subcommand gated on the DPO retention matrix.

    Attempts a REAL load of the matrix first (via `legal_bases_matrix.py`) so the
    message states the precise blocker instead of a single undifferentiated stub
    notice:
      - matrix unavailable (absent env var, missing file, malformed YAML, invalid
        schema, empty, or the UNRATIFIED placeholder template) — states the exact
        `reason`/`detail` from `RetentionMatrixUnavailableError`; or
      - matrix loads successfully — states that a present matrix does NOT by itself
        unblock this command, because `mechanism` (the actual execution code) is not
        implemented either.
    Either branch still returns a refusal string; `main()` always exits 78 regardless
    of which branch is taken — a present, valid matrix is never a success path here.
    """
    base = (
        f"{subcommand} refused: the {MODULE_NAME} entrypoint is an intentional "
        "fail-closed refusal stub (T2.8) — no lifecycle command is implemented. "
    )
    try:
        matrix = load_retention_matrix()
    except RetentionMatrixUnavailableError as exc:
        return (
            f"{base}Blocked on the DPO legal-bases/retention matrix: unavailable "
            f"({exc.reason}): {exc.detail} Set {MATRIX_PATH_ENV} to a ratified matrix "
            f"once one exists (schema template: {_MATRIX_TEMPLATE_PATH})."
            f"{extra}"
        )
    return (
        f"{base}The DPO legal-bases/retention matrix loaded successfully "
        f"({len(matrix)} categoria(s) from {MATRIX_PATH_ENV}), but {mechanism} is not "
        f"implemented — a present matrix does not by itself unblock this command."
        f"{extra}"
    )


def _refusal_message(subcommand: str | None) -> str:
    """Return the operator-facing refusal string for a given (or missing) subcommand."""
    if subcommand == SUBCMD_AUDIT_RETENTION:
        return AUDIT_RETENTION_REFUSAL
    if subcommand == SUBCMD_EXPURGO_WORKING:
        return _matrix_gated_refusal(
            subcommand,
            mechanism="the working-layer TTL sweep execution",
        )
    if subcommand == SUBCMD_VERIFY_ERASURE:
        return _matrix_gated_refusal(
            subcommand,
            mechanism="ErasureManager.erase()/verify()'s per-layer deletion",
            extra=_VERIFY_ERASURE_EXTRA,
        )
    if subcommand is None:
        return (
            f"{MODULE_NAME} refused: no subcommand given. This entrypoint is an "
            "intentional fail-closed refusal stub (T2.8) — no lifecycle command is "
            f"implemented (known: {', '.join(KNOWN_SUBCOMMANDS)}). {_ADR_POINTERS}"
        )
    return (
        f"{MODULE_NAME} refused: unknown subcommand {subcommand!r}. This entrypoint is "
        "an intentional fail-closed refusal stub (T2.8) — no lifecycle command is "
        f"implemented (known: {', '.join(KNOWN_SUBCOMMANDS)}). {_ADR_POINTERS}"
    )


def _blocked_on(subcommand: str | None) -> list[str]:
    """Return the doc/module pointers relevant to this subcommand's precise blocker."""
    if subcommand == SUBCMD_AUDIT_RETENTION:
        return [
            "docs/compliance/ADR-0020-amendment-draft.md",
            "docs/adr/0029-audit-chain-pruning-reanchor.md",
        ]
    if subcommand == SUBCMD_EXPURGO_WORKING:
        return [_MATRIX_TEMPLATE_PATH]
    if subcommand == SUBCMD_VERIFY_ERASURE:
        return [_MATRIX_TEMPLATE_PATH, "src/maezo/platform/erasure.py"]
    return [
        "docs/compliance/ADR-0020-amendment-draft.md",
        "docs/adr/0029-audit-chain-pruning-reanchor.md",
    ]


def main(argv: Sequence[str] | None = None) -> int:
    """Refuse every invocation and return a non-zero exit code.

    This is the whole contract: there is NO success path. Whatever subcommand is passed
    (including none, or an unknown one), the entrypoint logs the precise blocker, writes
    it to stderr so it is visible in the CronJob pod's logs, and returns
    `REFUSAL_EXIT_CODE` (never 0). The destructive `retention_query()` is never imported
    or called.

    Args:
        argv: Arguments excluding the program name. Defaults to `sys.argv[1:]`.

    Returns:
        `REFUSAL_EXIT_CODE` (78) — always non-zero.
    """
    args = list(sys.argv[1:] if argv is None else argv)
    subcommand = args[0] if args else None

    message = _refusal_message(subcommand)

    logger.error(
        "lifecycle_entrypoint_refused",
        subcommand=subcommand,
        exit_code=REFUSAL_EXIT_CODE,
        reason=message,
        blocked_on=_blocked_on(subcommand),
    )
    # structlog output is configuration-dependent and this entrypoint never configures
    # it; write to stderr unconditionally so the blocker is legible in `kubectl logs`
    # regardless of handler setup.
    print(message, file=sys.stderr)  # noqa: T201 — operator-facing CLI output, not logging

    return REFUSAL_EXIT_CODE
