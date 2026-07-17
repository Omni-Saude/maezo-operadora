"""Architecture test — no code path ORIGINATES the ceiling fact except the resolver.

This is the T1.9 acceptance-criterion test ("no code path can set ``dentro_teto_l2=True``
except policy(resolver); architecture test proves it") and the design's §5.6 defence: an
AST scan of the three financial workers (reembolso, auth, pagto) that FAILS if any of them
manufactures the within-ceiling fact from an untrusted source.

ORIGINATE vs PROPAGATE rule (design T1.9 §5.6, Rev 3). For assignments / keyword args /
dict values whose *target* is one of the ceiling-fact names
(``dentro_teto`` / ``dentro_teto_l2`` / ``teto_ok``):

  FORBIDDEN (origination from an untrusted source):
    * RHS is the bare constant ``True`` — the old ``dentro_teto = True`` manufacture
      (reembolso.py:230);
    * RHS reads the inbound payload — ``process_vars.get("dentro_teto_l2", ...)``
      (old auth.py:66), ``variables.get("dentro_teto_l2", ...)`` (old pagto.py:123), or an
      inbound attribute read ``input_data.dentro_teto_l2`` (old reembolso.py:227);
    * RHS is a boolean expression NOT derived from the resolver (e.g. ``dentro_tabela and
      ...``).

  PERMITTED (propagation / declaration):
    * RHS is a ``CeilingResolver.within_l2_ceiling(...)`` call (incl. the ``valor_cents is
      None -> False`` fail-closed guard, whose ``False`` is a safe default);
    * forwarding an already-resolver-derived value (a plain ``Name``, or a ``.dentro_teto_l2``
      read off a COMPUTED result object such as ``calculo``) into a result object / dict /
      log;
    * dataclass field defaults ``dentro_teto_l2: bool = False`` (declarations, not
      decisions).

The scanner is proven to BITE (fails on the exact pre-fix snippets) and proven NOT to
false-positive (passes on the legit propagation snippets), then asserted clean against the
real, imported post-fix modules.
"""

from __future__ import annotations

import ast
from pathlib import Path

from maezo.tools.workers import auth, pagto, reembolso

# The ceiling-fact names, as assignment targets / keyword args / dict keys.
_FACT_NAMES: frozenset[str] = frozenset({"dentro_teto", "dentro_teto_l2", "teto_ok"})
# Names of the INBOUND payload objects a worker receives — reading a ceiling fact off one
# of these is origination-from-untrusted-input. A read off any OTHER object (e.g. a
# computed `calculo` result) is propagation of an already-resolved fact.
_INBOUND_OBJECTS: frozenset[str] = frozenset({"input_data", "process_vars", "variables"})
# The single trusted origin of the fact.
_RESOLVER_METHOD = "within_l2_ceiling"


def _contains_resolver_call(node: ast.AST) -> bool:
    """True iff the expression contains a ``*.within_l2_ceiling(...)`` call."""
    for child in ast.walk(node):
        if (
            isinstance(child, ast.Call)
            and isinstance(child.func, ast.Attribute)
            and child.func.attr == _RESOLVER_METHOD
        ):
            return True
    return False


def _forbidden_reason(value: ast.AST) -> str | None:
    """Return a reason string if ``value`` ORIGINATES the fact from an untrusted source, else None."""
    # 1) bare constant `True` — manufactured within-ceiling fact.
    if isinstance(value, ast.Constant) and value.value is True:
        return "originates ceiling fact from constant `True`"

    # 2) inbound payload read via `.get("<fact>", ...)`.
    if (
        isinstance(value, ast.Call)
        and isinstance(value.func, ast.Attribute)
        and value.func.attr == "get"
        and value.args
        and isinstance(value.args[0], ast.Constant)
        and value.args[0].value in _FACT_NAMES
    ):
        return f"originates ceiling fact from inbound payload read `.get({value.args[0].value!r})`"

    # 3) inbound attribute read `<inbound_object>.dentro_teto_l2`.
    if (
        isinstance(value, ast.Attribute)
        and value.attr in _FACT_NAMES
        and isinstance(value.value, ast.Name)
        and value.value.id in _INBOUND_OBJECTS
    ):
        return f"originates ceiling fact from inbound attribute read `{value.value.id}.{value.attr}`"

    # 4) boolean expression NOT derived from the resolver (e.g. `dentro_tabela and ...`).
    if isinstance(value, ast.BoolOp) and not _contains_resolver_call(value):
        return "originates ceiling fact from a non-resolver boolean expression"

    return None


def _scan_source(source: str, label: str) -> list[str]:
    """Return ``"<label>:<line>: <reason>"`` for every forbidden origination in ``source``."""
    tree = ast.parse(source)
    offenders: list[str] = []

    def _check(value: ast.AST, lineno: int) -> None:
        reason = _forbidden_reason(value)
        if reason is not None:
            offenders.append(f"{label}:{lineno}: {reason}")

    for node in ast.walk(tree):
        # direct assignments: `dentro_teto = <rhs>`
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in _FACT_NAMES:
                    _check(node.value, node.lineno)
        # annotated assignments / dataclass fields: `dentro_teto_l2: bool = <rhs>`
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.target.id in _FACT_NAMES and node.value is not None:
                _check(node.value, node.lineno)
        # keyword args: `Foo(dentro_teto_l2=<rhs>)`, `log(dentro_teto_l2=<rhs>)`
        elif isinstance(node, ast.keyword):
            if node.arg in _FACT_NAMES:
                _check(node.value, node.lineno)
        # dict values: `{"dentro_teto_l2": <rhs>}`
        elif isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values, strict=False):
                if isinstance(key, ast.Constant) and key.value in _FACT_NAMES:
                    _check(value, getattr(value, "lineno", node.lineno))

    return offenders


def _scan_module(module: object) -> list[str]:
    path = Path(module.__file__)  # type: ignore[attr-defined]
    return _scan_source(path.read_text(encoding="utf-8"), str(path))


# ---------------------------------------------------------------------------
# 1) The scanner BITES — it flags the exact pre-fix bypass patterns.
# ---------------------------------------------------------------------------

# The four verified pre-fix origination sites (design VERIFIED DEFECT MAP).
_PRE_FIX_BYPASSES: dict[str, str] = {
    "reembolso_inbound_attr_echo (old reembolso.py:227)": "dentro_teto = input_data.dentro_teto_l2\n",
    "reembolso_constant_true_manufacture (old reembolso.py:229-230)": (
        "if dentro_tabela and valor_calculado > 0:\n    dentro_teto = True\n"
    ),
    "auth_inbound_get_echo (old auth.py:66)": 'teto_ok = process_vars.get("dentro_teto_l2", False)\n',
    "pagto_inbound_get_echo (old pagto.py:123)": 'dentro_teto = variables.get("dentro_teto_l2", False)\n',
}


def test_architecture_scanner_bites_known_bypass_patterns() -> None:
    """Each verified pre-fix origination site is flagged — proves the scanner is not a no-op."""
    for label, snippet in _PRE_FIX_BYPASSES.items():
        offenders = _scan_source(snippet, label)
        assert offenders, f"scanner FAILED to flag known bypass: {label!r}\n{snippet}"


# ---------------------------------------------------------------------------
# 2) The scanner does NOT false-positive on the legit post-fix propagation/declaration.
# ---------------------------------------------------------------------------

_PERMITTED_SNIPPET = """
dentro_teto = resolver.within_l2_ceiling(tenant=t, action=a, param=p, value_cents=v)
teto_ok = self._resolver.within_l2_ceiling(tenant=t, action=a, param=p, value_cents=v)
teto_ok = False
result = ReembolsoCalculoResult(dentro_teto_l2=dentro_teto)
log("evt", dentro_teto_l2=calculo.dentro_teto_l2)
payload = {"dentro_teto_l2": teto_ok}


class _Fields:
    dentro_teto_l2: bool = False
"""


def test_architecture_scanner_permits_resolver_propagation_and_defaults() -> None:
    """Resolver calls, `False` defaults, and forwarding a computed value are NOT flagged."""
    offenders = _scan_source(_PERMITTED_SNIPPET, "permitted-snippet")
    assert offenders == [], f"scanner false-positived on legit propagation:\n{offenders}"


# ---------------------------------------------------------------------------
# 3) The real, imported post-fix modules are CLEAN.
# ---------------------------------------------------------------------------


def test_no_code_path_originates_dentro_teto_except_resolver() -> None:
    """reembolso/auth/pagto never originate the ceiling fact from a constant or inbound payload.

    The only permitted origin is ``CeilingResolver.within_l2_ceiling`` (plus its ``False``
    fail-closed guard and pure propagation). Scans the ACTUAL imported modules, so a
    regression re-introducing any echo-through fails this security canary.
    """
    offenders: list[str] = []
    for module in (reembolso, auth, pagto):
        offenders.extend(_scan_module(module))
    assert offenders == [], (
        "A financial worker ORIGINATES `dentro_teto_l2`/`teto_ok` from a constant or the "
        "inbound payload (ceiling bypass — CRITICAL, defect B3):\n" + "\n".join(offenders)
    )
