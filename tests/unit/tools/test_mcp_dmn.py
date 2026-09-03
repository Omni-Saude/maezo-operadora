"""Deprecation-contract tests for maezo.tools.mcp_dmn (T1.5, ADR-0028 §"Fate of mcp_dmn").

The former behavior tests (evaluate auth_auto_approval etc.) were REMOVED with the demotion:
the in-process XML evaluator is deprecated — it fails OPEN on no-match and cannot evaluate FEEL
comparison/range/list tests — and runtime evaluation is engine-side only
(`maezo.tools.workers.dmn_transport`, covered by `tests/unit/tools/workers/test_dmn_transport.py`
and the live golden-parity suite `tests/integration/dmn/test_dmn_golden_parity.py`). Testing the
deprecated evaluator's decision behavior would keep asserting semantics ADR-0028 explicitly
rejects (a second, drifting FEEL engine).

What must stay true until the module is deleted outright:
1. importing the package emits a loud `DeprecationWarning` (no warning-free import path);
2. no production module imports it (the deprecation is not being silently swallowed anywhere).
"""

from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_importing_mcp_dmn_emits_deprecation_warning() -> None:
    """ADR-0028 §"Fate": the fail-open evaluator must NOT be importable without loud deprecation.

    Runs the import in a FRESH interpreter (not this test process) because `warnings.warn` at
    module scope fires only on first import — an earlier import anywhere in the test session
    (collection, another test) would make an in-process `pytest.warns` assertion flaky.
    """
    code = (
        "import warnings\n"
        "with warnings.catch_warnings(record=True) as caught:\n"
        "    warnings.simplefilter('always')\n"
        "    import maezo.tools.mcp_dmn  # noqa: F401\n"
        "deprecations = [w for w in caught if issubclass(w.category, DeprecationWarning)]\n"
        "assert deprecations, 'no DeprecationWarning on import'\n"
        "assert 'ADR-0028' in str(deprecations[0].message)\n"
        "assert 'dmn_transport' in str(deprecations[0].message)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        check=False,
    )
    assert result.returncode == 0, f"deprecation-warning probe failed:\n{result.stderr}"


def test_submodule_import_cannot_bypass_the_package_warning() -> None:
    """`import maezo.tools.mcp_dmn.server` executes the package `__init__` first (Python import
    semantics), so the direct-submodule path fires the same warning — no bypass."""
    code = (
        "import warnings\n"
        "with warnings.catch_warnings(record=True) as caught:\n"
        "    warnings.simplefilter('always')\n"
        "    import maezo.tools.mcp_dmn.server  # noqa: F401\n"
        "assert any(issubclass(w.category, DeprecationWarning) for w in caught)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        check=False,
    )
    assert result.returncode == 0, f"submodule bypass probe failed:\n{result.stderr}"


def test_no_production_module_imports_mcp_dmn() -> None:
    """Import-fence: zero `src/` modules IMPORT the deprecated evaluator (its only sanctioned
    remaining import sites are its own package files). A new production import would silently
    resurrect the fail-open evaluator — this test makes that a red build instead. Scans actual
    `import`/`from` statements via AST (a docstring MENTIONING mcp_dmn — e.g. dmn_transport.py's
    own "this replaces mcp_dmn" note — is documentation, not a dependency)."""
    import ast

    offenders: list[str] = []
    for path in (REPO_ROOT / "src").rglob("*.py"):
        if path.parent.name == "mcp_dmn":
            continue  # the deprecated package itself
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                if any("mcp_dmn" in alias.name for alias in node.names):
                    offenders.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}")
            elif isinstance(node, ast.ImportFrom) and node.module and "mcp_dmn" in node.module:
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}")
    assert offenders == [], (
        "production code IMPORTS the DEPRECATED maezo.tools.mcp_dmn evaluator "
        f"(runtime DMN evaluation is engine-side only, ADR-0028): {offenders}"
    )


def test_deprecated_server_still_importable_for_the_deprecation_cycle() -> None:
    """The module stays importable (loudly) until removal — an out-of-tree consumer gets the
    warning + a working import, not a bare ImportError mid-cycle."""
    module = importlib.import_module("maezo.tools.mcp_dmn")
    assert hasattr(module, "DmnServer")


def test_no_match_fails_closed_not_open(tmp_path: Path) -> None:
    """AF-08: the donor's original no-match tail was `return {}` — a silent fail-OPEN non-decision
    (ADR-0028 §3 requires raise/route-to-human). This is NOT a decision-behavior test (the class
    of test this module's docstring says was deliberately removed): it asserts the deprecation
    SAFETY CONTRACT — if this dead evaluator is ever re-adopted by mistake, it refuses to fail
    open — the same kind of fence as `test_no_production_module_imports_mcp_dmn` above, not a
    second FEEL engine under test.
    """
    from maezo.tools.mcp_dmn.server import DmnServer, DmnSettings

    dmn_file = tmp_path / "test_no_match.dmn"
    dmn_file.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<definitions xmlns="https://www.omg.org/spec/DMN/20191111/MODEL/"
             id="defs_test_no_match" name="Test No Match"
             namespace="http://maezo.health/dmn/test">
  <decision id="test_no_match" name="Test No Match">
    <decisionTable id="dt_test_no_match" hitPolicy="FIRST">
      <input id="i1" label="flag">
        <inputExpression id="ie1" typeRef="boolean">
          <text>flag</text>
        </inputExpression>
      </input>
      <output id="o1" label="result" name="result" />
      <rule id="r1">
        <inputEntry id="ie_r1"><text>true</text></inputEntry>
        <outputEntry id="oe_r1"><text>"matched"</text></outputEntry>
      </rule>
    </decisionTable>
  </decision>
</definitions>
""",
        encoding="utf-8",
    )
    server = DmnServer(DmnSettings(dmn_dir=str(tmp_path)))
    with pytest.raises(RuntimeError, match="refuses to fail open"):
        server.evaluate_decision("test_no_match", {"flag": False})
