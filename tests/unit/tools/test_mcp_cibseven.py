"""Package-level invariants for `maezo.tools.mcp_cibseven` (T2.4 co-requisite).

The dead, un-audited `CibSevenServer.start_process` (a raw httpx POST to
`/process-definition/key/{key}/start` — no audit emit, no provenance/audit_sink fence, no
idempotency/businessKey) has been REMOVED entirely, along with its `register_tools`/`list_tools`
sibling and `CibSevenSettings`. Grep-confirmed unreferenced anywhere in `src/**`: `register_tools`
was never called (no `ToolRegistry`/`tool_wiring.py`/`build_tool_invoker` exists in this v2 tree —
ADR-0022's claim that `register_tools` is load-bearing describes that unbuilt mechanism, not v2's
actual `src/`). Left in place, it would become a LIVE un-audited process-start the moment a future
tool registry wired it up directly instead of the canonical chokepoint. The SOLE agent-side
process-start effect is now `maezo.tools.mcp_cibseven.transport.start_process_idempotent`
(ADR-0007-audited, T-C2 fence — see `test_start_process_fence.py`).

This module asserts two things stay true over time:
(a) the dead class/settings never quietly come back onto the package's public surface, and
(b) no OTHER file under `src/maezo` hardcodes the raw process-start REST path outside that one
    fenced chokepoint — a structural (AST) guard, durable even against a future un-audited caller
    that uses a different HTTP client/helper, since it keys on the literal wire path rather than a
    specific call shape or import.
"""

from __future__ import annotations

import ast
from pathlib import Path

import maezo.tools.mcp_cibseven as mcp_cibseven_pkg

# The literal CIB Seven / Camunda 7 REST path segment for "start a process instance by
# definition key" (POST /process-definition/key/{key}/start). Distinctive enough that its
# presence anywhere under src/ signals a process-start call site — present in both plain string
# literals and f-string constant segments (an f-string's static text lowers to `ast.Constant`
# nodes inside the parsed `JoinedStr`, so this catches `f"{base}/process-definition/key/..."`
# just as well as a bare literal).
_RAW_START_PATH_MARKER = "process-definition/key"

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SRC_ROOT = _REPO_ROOT / "src" / "maezo"

# The ONLY file allowed to reference the raw start path: the audited, idempotent chokepoint
# (`start_process_idempotent` / `CibSevenHttpTransport.start_process_instance`, ADR-0007 T-C2).
_ALLOWED_FILE = Path("src/maezo/tools/mcp_cibseven/transport.py")


def test_cibseven_server_dead_class_removed_from_public_surface() -> None:
    """The dead, un-audited CibSevenServer/CibSevenSettings must not be importable/exported."""
    assert not hasattr(mcp_cibseven_pkg, "CibSevenServer")
    assert "CibSevenServer" not in mcp_cibseven_pkg.__all__
    assert not (_SRC_ROOT / "tools" / "mcp_cibseven" / "server.py").exists()


def test_no_raw_process_start_path_outside_the_fenced_chokepoint() -> None:
    """Only transport.py's fenced chokepoint may reference the raw process-start REST path.

    Scans every .py file under src/maezo for the `process-definition/key` wire-path marker and
    asserts the ONLY file containing it is the allowlisted chokepoint — proving no second
    un-audited start path exists anywhere in src/ (not just that the deleted server.py is gone).
    """
    offending: dict[str, list[int]] = {}
    allowed_hit = False

    for path in sorted(_SRC_ROOT.rglob("*.py")):
        rel = path.relative_to(_REPO_ROOT)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        hit_lines = [
            node.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and _RAW_START_PATH_MARKER in node.value
        ]
        if not hit_lines:
            continue
        if rel == _ALLOWED_FILE:
            allowed_hit = True
            continue
        offending[str(rel)] = hit_lines

    assert not offending, (
        f"raw un-fenced process-start path found outside the chokepoint: {offending} "
        f"(only {_ALLOWED_FILE} may build this URL — route through "
        "maezo.tools.mcp_cibseven.transport.start_process_idempotent instead)"
    )
    assert allowed_hit, (
        f"expected the fenced chokepoint {_ALLOWED_FILE} to reference the raw start path at "
        "least once — this allowlist may be stale (file moved/renamed?)"
    )
