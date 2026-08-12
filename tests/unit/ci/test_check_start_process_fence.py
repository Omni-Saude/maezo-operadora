"""Unit tests for the repo-wide start-process fence gate (T3.4 F1).

Two layers, mirroring `test_check_bpmn_error_allowlist.py`:

1. **Synthetic-tree** tests build a throwaway `tmp_path` source tree and drive `scan_tree`
   directly — this is the injected-violation self-check the T3.4 F1 task requires ("inject a
   violation in a scratch copy -> gate goes RED; revert"), done properly as a permanent regression
   test rather than a one-off manual step.
2. **Real-tree** tests run the full scan against `src/maezo` and assert the gate PASSES today,
   with the exact allowlist and a non-vacuity proof (the fence IS actually called somewhere, so a
   passing gate isn't just "found nothing to look at").
"""

from __future__ import annotations

from pathlib import Path

from scripts.ci.check_start_process_fence import (
    FENCED_CHOKEPOINT_CALL,
    FORBIDDEN_PATH_SUBSTRING,
    SANCTIONED_RELATIVE_PATHS,
    scan_tree,
)

# tests/unit/ci/<file> -> parents[3] == repo root.
_REPO_ROOT = Path(__file__).resolve().parents[3]
_SRC_DIR = _REPO_ROOT / "src" / "maezo"


# ---------------------------------------------------------------------------
# Real-tree: the gate passes today, with the exact pinned allowlist
# ---------------------------------------------------------------------------


def test_real_tree_passes() -> None:
    result = scan_tree(_SRC_DIR)
    assert result.ok, result.render()


def test_real_tree_is_not_vacuous_the_fence_is_actually_called() -> None:
    """A passing gate that scanned zero real fence-callers would be worthless — prove the scan
    actually found `start_process_idempotent` call sites (the 10 agent graphs + inadimplencia.py
    + contas.py + fraude.py + notification_bridge.py, per the module docstring's sweep)."""
    result = scan_tree(_SRC_DIR)
    assert len(result.fence_called_in) >= 10
    assert result.scanned_files > 100  # sanity: the scan covers the real tree, not a stub


def test_real_tree_allowlist_is_exactly_the_three_transport_decorator_files() -> None:
    """Pin the allowlist explicitly (task requirement: "enumerate them by grep and pin the
    allowlist explicitly") — every file must exist, and the set must not silently grow.

    ONDA 1 B2 added the THIRD entry, `gateway/seams/cibseven.py`, on the same grounds as
    `tools/workers/cibseven_engine.py`: a Protocol-preserving `CibSevenTransport` decorator whose
    `start_process_instance` is a pure pass-through (no decision, no audit, no client of its own),
    so it wraps the existing start path rather than creating a second one. The next test asserts
    that pass-through property structurally, so the entry cannot quietly become a real start site.
    """
    assert (
        frozenset(
            {
                "tools/mcp_cibseven/transport.py",
                "tools/workers/cibseven_engine.py",
                "gateway/seams/cibseven.py",
            }
        )
        == SANCTIONED_RELATIVE_PATHS
    )
    for rel in SANCTIONED_RELATIVE_PATHS:
        assert (_SRC_DIR / rel).is_file(), f"allowlisted path {rel} does not exist under {_SRC_DIR}"


def test_gated_transport_start_is_a_pure_passthrough_not_a_second_start_path() -> None:
    """The price of the third allowlist entry, charged structurally.

    `GatedCibSevenTransport.start_process_instance` is exempted from the fence because it does
    NOTHING but forward to the inner transport. If a future edit gives it a decision, an audit
    emit, a retry, or a client of its own, that exemption stops being honest — so assert the
    property directly on the AST instead of trusting the comment: the body must be exactly one
    assignment whose value is an `await self._inner.start_process_instance(...)`, plus the return.
    """
    import ast

    source = (_SRC_DIR / "gateway/seams/cibseven.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    fn = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "start_process_instance"
    )
    # Strip the docstring, then require: `x = await self._inner.start_process_instance(...)`; `return x`.
    body = [
        stmt for stmt in fn.body if not (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant))
    ]
    assert len(body) == 2, f"expected a 2-statement pass-through, got {len(body)} statements"
    assign, ret = body
    assert isinstance(assign, ast.AnnAssign | ast.Assign)
    awaited = assign.value
    assert isinstance(awaited, ast.Await), "the pass-through must delegate, not compute"
    call = awaited.value
    assert isinstance(call, ast.Call)
    assert isinstance(call.func, ast.Attribute)
    assert call.func.attr == "start_process_instance"
    assert isinstance(call.func.value, ast.Attribute) and call.func.value.attr == "_inner"
    assert isinstance(ret, ast.Return)
    # And no `gate(...)` call anywhere inside it — gating here would land AFTER the durable claim.
    assert not [
        node
        for node in ast.walk(fn)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "gate"
    ], "start_process_instance must NOT be gated — a denial there wedges a strict business key"


def test_allowlisted_transport_module_is_exempted_despite_calling_start_process_instance() -> None:
    """Sanity: the fence's own module DOES contain a `start_process_instance(` call (that's
    exactly what `start_process_idempotent` calls) — proving the allowlist is load-bearing, not
    accidentally exempting an empty/irrelevant file."""
    from scripts.ci.check_start_process_fence import scan_module

    transport_path = _SRC_DIR / "tools/mcp_cibseven/transport.py"
    violations, calls_fence = scan_module(transport_path)
    # The raw scan (ignoring the allowlist) DOES find a forbidden-name call here — this is what
    # the allowlist in `scan_tree` suppresses for this specific file.
    assert violations, "expected transport.py to contain a start_process_instance( call"
    assert not calls_fence  # transport.py DEFINES start_process_idempotent, doesn't call it


# ---------------------------------------------------------------------------
# Synthetic tree: inject a violation -> gate goes RED (the F1 self-verification step)
# ---------------------------------------------------------------------------


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_gate_is_green_on_a_clean_synthetic_tree(tmp_path: Path) -> None:
    _write(
        tmp_path / "agents" / "example" / "graph.py",
        "from maezo.tools.mcp_cibseven.transport import start_process_idempotent\n\n"
        "async def build(config):\n"
        "    await start_process_idempotent(transport, 'KEY', 'bk', {}, audit_sink=s, provenance=p)\n",
    )
    result = scan_tree(tmp_path)
    assert result.ok, result.render()
    assert "agents/example/graph.py" in result.fence_called_in


def test_gate_goes_red_on_injected_direct_start_process_instance_call(tmp_path: Path) -> None:
    """THE self-verification the F1 task requires: inject a raw, unfenced
    `transport.start_process_instance(...)` call into a scratch module outside the allowlist —
    the gate MUST fail (RED), pointing at the fence."""
    offending = tmp_path / "agents" / "example" / "graph.py"
    _write(
        offending,
        "async def build(config):\n"
        "    # BYPASS (injected for this test): skips start_process_idempotent entirely.\n"
        "    return await transport.start_process_instance('KEY', 'bk', {})\n",
    )

    result = scan_tree(tmp_path)

    assert not result.ok
    assert any("start_process_instance" in v and "bypasses" in v for v in result.violations)
    assert any(str(offending) in v for v in result.violations)


def test_gate_goes_red_on_injected_raw_rest_path_literal(tmp_path: Path) -> None:
    """The second forbidden-pattern arm: a hand-rolled raw REST start path string literal
    (bypassing the transport method entirely) must also fail the gate."""
    offending = tmp_path / "agents" / "example" / "graph.py"
    _write(
        offending,
        "async def build(config):\n"
        f"    path = f'{FORBIDDEN_PATH_SUBSTRING}/{{process_key}}/start'\n"
        "    return await client.post(path)\n",
    )

    result = scan_tree(tmp_path)

    assert not result.ok
    assert any(FORBIDDEN_PATH_SUBSTRING in v for v in result.violations)


def test_gate_exempts_only_the_pinned_allowlisted_relative_path(tmp_path: Path) -> None:
    """The SAME offending call, at the exact pinned relative path, is exempted — but anywhere
    else (even a differently-named file implementing the same idea) is not."""
    sanctioned_rel = next(iter(SANCTIONED_RELATIVE_PATHS))
    _write(
        tmp_path / sanctioned_rel,
        "async def start_process_instance(self, *a, **k):\n"
        "    return await self._client.post('/process-definition/key/x/start')\n",
    )
    _write(
        tmp_path / "tools" / "workers" / "not_sanctioned.py",
        "async def start_process_instance(self, *a, **k):\n"
        "    return await self._client.post('/process-definition/key/x/start')\n",
    )

    result = scan_tree(tmp_path)

    assert not result.ok
    offending_files = {v.split(":", 1)[0] for v in result.violations}
    assert str(tmp_path / "tools" / "workers" / "not_sanctioned.py") in offending_files
    assert str(tmp_path / sanctioned_rel) not in offending_files


def test_gate_fails_closed_on_unparseable_file(tmp_path: Path) -> None:
    """A syntax error is a violation, never a silent skip — ambiguity is never a pass."""
    _write(tmp_path / "broken.py", "def build(:\n    this is not valid python\n")
    result = scan_tree(tmp_path)
    assert not result.ok
    assert any("could not parse" in v for v in result.violations)


def test_fenced_chokepoint_constant_matches_the_real_function_name() -> None:
    """Sanity: the constant this gate treats as "the fence" is the real function name — a typo
    here would silently make every file look like it calls something that doesn't exist."""
    from maezo.tools.mcp_cibseven.transport import start_process_idempotent

    assert start_process_idempotent.__name__ == FENCED_CHOKEPOINT_CALL
