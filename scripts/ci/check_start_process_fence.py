#!/usr/bin/env python3
"""CI gate: no direct `start_process_instance` call outside the fenced transport layer (T3.4 F1).

Purpose
-------
`start_process_idempotent` (`maezo.tools.mcp_cibseven.transport:560`) is the SOLE, ADR-0007-audited
process-start chokepoint (module docstring, `tools/mcp_cibseven/__init__.py`): it ALWAYS calls
`find_active_instance` before `start_process_instance`, and durably emits the ADR-0007 audit row
BEFORE the engine effect (T-C2 emit-before-effect). A caller that reaches for
`transport.start_process_instance(...)` directly skips BOTH — a business-key-idempotent guarantee
and the non-repudiation audit row — silently double-starting a process or leaving an unaudited
engine effect.

Nothing before T3.4 F1 mechanically prevented that: the ONLY existing static guard
(`tests/unit/platform/test_notification_bridge_a3_certification.py`'s "PIN 3", itself descended
from the PR #104 / t1.10-te AST-guard lineage — see `tests/unit/tools/workers/
test_harness_audited_refusal.py`'s `_handle_except_handlers`/`_calls_audit_guard_refusal` pattern)
is scoped to exactly 3 files (`notification_bridge.py`, `contas.py`, `fraude.py`). Every other
caller of the fence — all 10 agent graphs (`agents/*/graph.py`), `inadimplencia.py` — had NO
static protection against a future direct `transport.start_process_instance(...)` call creeping in.

This gate extends the SAME AST pattern (forbidden call name + forbidden raw-REST-path string
literal) repo-wide over `src/maezo/`, with an explicit, narrow allowlist for the 2 modules that
legitimately implement (not bypass) the call:

  - `tools/mcp_cibseven/transport.py` — the fence's OWN module. `start_process_idempotent` itself
    calls `transport.start_process_instance(...)` (transport.py:615) — that IS the fence,
    definitionally sanctioned. (`CibSevenHttpTransport`/`FakeCibSevenTransport` also DEFINE
    `start_process_instance` here — a `def`, not a `Call`, so it never trips this scan anyway.)
  - `tools/workers/cibseven_engine.py` — `FreshClientCibSevenTransport`, a `CibSevenTransport`
    Protocol DECORATOR (fresh-client-per-call transport wrapper, T1.10 T-D) that delegates to its
    OWN inner transport's `start_process_instance` (cibseven_engine.py:98). This is transport-layer
    plumbing, not a business-logic caller reaching around the fence — structurally identical to
    `CibSevenHttpTransport` itself implementing the method.

Repo-wide sweep (T3.4 F1) confirms these are the ONLY 2 call sites of `start_process_instance(`
anywhere in `src/maezo/` (`grep -rn 'start_process_instance(' src | grep -v 'def '`) — every other
caller already goes through `start_process_idempotent`. A future direct call anywhere else now
fails this gate loudly, pointing at the fence.

Design
------
Mirrors `scripts/ci/check_bpmn_error_allowlist.py`: a pure, dependency-light core (`scan_tree`)
plus a thin CLI wrapper (`main`), so the core is unit-testable against synthetic fixtures.

Usage (CI / local)
-------------------
    python3 scripts/ci/check_start_process_fence.py
    python3 scripts/ci/check_start_process_fence.py --src-dir src/maezo
"""

from __future__ import annotations

import argparse
import ast
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

#: The ONE sanctioned start-effect call anywhere in the tree. A future edit adding a second,
#: unfenced start path (a raw `transport.start_process_instance(...)` call, or a hand-rolled
#: `/process-definition/key/{key}/start` POST) outside the allowlist below must fail this gate.
FENCED_CHOKEPOINT_CALL = "start_process_idempotent"

#: Forbidden raw-start call/attribute names — if any of these appear as a Call's function name
#: (or attribute) in a non-allowlisted module, the fence is bypassed.
FORBIDDEN_CALL_NAMES: frozenset[str] = frozenset({"start_process_instance"})

#: Forbidden literal substring — a hand-rolled raw REST path never appears as a string literal
#: outside the allowlisted transport module (mirrors the PIN 3 lineage's own check).
FORBIDDEN_PATH_SUBSTRING = "process-definition/key"

#: Explicitly pinned, narrow allowlist (paths relative to `--src-dir`, POSIX-separated) — the
#: fence's own module and the ONE transport-layer decorator that legitimately delegates to it.
#: Enumerated by exhaustive grep (module docstring); extending this set is a deliberate, reviewed
#: act, never a silent workaround for a gate failure.
SANCTIONED_RELATIVE_PATHS: frozenset[str] = frozenset(
    {
        "tools/mcp_cibseven/transport.py",
        "tools/workers/cibseven_engine.py",
    }
)


@dataclass(frozen=True)
class Violation:
    """One offending call/literal found outside the sanctioned modules."""

    path: Path
    lineno: int
    detail: str

    def render(self) -> str:
        return f"{self.path}:{self.lineno}: {self.detail}"


def _call_func_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def scan_module(path: Path) -> tuple[list[Violation], bool]:
    """AST-scan one file. Returns `(violations, calls_the_fence)`.

    A file that cannot be parsed is a hard, fail-closed violation (never silently skipped) — an
    unparseable file is exactly the kind of thing a static gate must never wave through.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (SyntaxError, OSError) as exc:
        detail = f"could not parse ({exc}) — fail-closed, cannot prove absence of a bypass"
        return [Violation(path, 0, detail)], False

    violations: list[Violation] = []
    calls_fence = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = _call_func_name(node)
            if name == FENCED_CHOKEPOINT_CALL:
                calls_fence = True
            elif name in FORBIDDEN_CALL_NAMES:
                violations.append(
                    Violation(
                        path,
                        node.lineno,
                        f"direct call to {name}(...) bypasses the {FENCED_CHOKEPOINT_CALL} fence "
                        "(ADR-0007/T-C2 emit-before-effect + business-key idempotency) — route "
                        f"through {FENCED_CHOKEPOINT_CALL} instead",
                    )
                )
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if FORBIDDEN_PATH_SUBSTRING in node.value:
                violations.append(
                    Violation(
                        path,
                        node.lineno,
                        f"raw REST start-path literal {node.value!r} — bypasses the fenced "
                        f"{FENCED_CHOKEPOINT_CALL} chokepoint",
                    )
                )
    return violations, calls_fence


@dataclass(frozen=True)
class GateResult:
    """Outcome of the repo-wide start-process fence gate."""

    ok: bool
    violations: tuple[str, ...] = ()
    scanned_files: int = 0
    fence_called_in: tuple[str, ...] = ()  # relative paths that call the fence (non-vacuity proof)

    def render(self) -> str:
        status = "PASS" if self.ok else "FAIL"
        lines = [f"[start-process-fence] {status} ({self.scanned_files} files scanned)"]
        if self.violations:
            lines.append(f"  violations ({len(self.violations)}):")
            lines.extend(f"    - {v}" for v in self.violations)
        lines.append(f"  fence called in {len(self.fence_called_in)} file(s) (non-vacuity check).")
        return "\n".join(lines)


def scan_tree(src_dir: Path) -> GateResult:
    """Scan every `*.py` under `src_dir`, skipping the pinned allowlist. Pure — no process exit."""
    violations: list[str] = []
    scanned = 0
    fence_called_in: list[str] = []

    for path in sorted(src_dir.rglob("*.py")):
        rel = path.relative_to(src_dir).as_posix()
        scanned += 1
        file_violations, calls_fence = scan_module(path)
        if calls_fence:
            fence_called_in.append(rel)
        if rel in SANCTIONED_RELATIVE_PATHS:
            continue  # allowlisted: legitimately implements, never bypasses, the fence.
        violations.extend(v.render() for v in file_violations)

    return GateResult(
        ok=not violations,
        violations=tuple(violations),
        scanned_files=scanned,
        fence_called_in=tuple(fence_called_in),
    )


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="check_start_process_fence",
        description=(
            "CI gate (T3.4 F1): every `start_process_instance(...)` call in src/maezo/ must be "
            "inside the pinned transport-layer allowlist — everything else must route through "
            "start_process_idempotent."
        ),
    )
    parser.add_argument(
        "--src-dir",
        default="src/maezo",
        help="Source tree to AST-scan (default: src/maezo).",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point. Returns 0 (pass) or 1 (fail); never raises on ordinary input."""
    args = build_arg_parser().parse_args(argv)
    src_dir = Path(args.src_dir)

    if not src_dir.is_dir():
        print(f"[start-process-fence] FAIL: src dir not found: {src_dir}", file=sys.stderr)
        return 1

    result = scan_tree(src_dir)
    print(result.render())
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
