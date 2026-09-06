"""Unit tests for `EngineRest._to_camunda_vars` — the process-integration var mapper.

`tests/integration/processes/engine_rest.py` is a self-contained httpx wrapper (no maezo
import, no live engine required to import it), so its Python-value -> Camunda REST variable
mapper is exercised here as a PURE unit test — no docker-compose stack. ADR-0011 governs the
*integration* lane (real engine, no engine mock); this is only the mapper's typing logic, which
is deterministic and engine-free.

Canonical parity (T3.1): this mapper matches the orchestrator-designated canonical single-value
mapper `maezo.tools.workers.harness._to_camunda_var` (T1.1 §5) branch-for-branch, WITH ONE
DECLARED EXCEPTION recorded below:

  * float          -> {"value": v, "type": "Double"}          (NOT stringified)
  * raw dict|list  -> {"value": json.dumps(v, ensure_ascii=False, default=str), "type": "Json"}
  * bool           -> {"value": v, "type": "Boolean"}         (BEFORE the int branch:
                                                               `isinstance(True, int)` is True)
  * int            -> Integer within java int32, else Long     (ADR-0018 part 2 boundary)
  * None           -> {"value": None, "type": "String"}
  * {"value": ..}  -> passed through UNCHANGED                 (caller-forced explicit type)

THE EXCEPTION — the int branch, by NAME (owner decision R-173, 2026-09-04). The canonical mapper
now consults `maezo.tools.workers.engine_var_types.camunda_int_type(name, value)`, which types
the names in `LONG_TYPED_ENGINE_VARS` as `Long` for EVERY value; the magnitude boundary above
still governs every other name. This mapper is NAME-BLIND: the key `k` is in scope in
`_to_camunda_vars`, but the int branch decides on magnitude alone and never consults it. So for
a declared name the two mappers disagree, and the parity claim above holds only for names
OUTSIDE the declaration. That is deliberate, not drift: `EngineRest` is test infra that imports
nothing from `maezo` by design, so it cannot consume the declaration without losing that
property. (That zero-import property is true today — measured — but only DECLARED, in the module
docstring of `test_engine_rest_ausencia_e_proveniencia.py`; no test fences it.) It is also inert for the only
declared name today — `total_glosado_candidato_centavos` is a computed fact the SP-OP-CONTAS-001
contract declares is never seeded or echoed, and it appears in no `.dmn` and nowhere under
`tests/integration/` (measured). The tests below pass because `_map()` passes no name.

The last branch is the backward-compat contract with the unmerged phase2-land suites, whose
`_double_var()`/`_json_var()` helpers emit a PRE-SHAPED `{"value": .., "type": ..}` dict: it must
pass through verbatim, and a `_json_var(...)`-wrapped payload must NEVER be double-encoded by the
new raw-dict/list branch. These tests pin every branch and that backward-compat.
"""

from __future__ import annotations

import json
from typing import Any

from tests.integration.processes.engine_rest import EngineRest

# Java int32 (java.lang.Integer) upper bound — the Integer/Long boundary the mapper pins
# (ADR-0018 part 2). Mirrored here from the mapper under test, not imported, to assert it.
_INT32_MAX = 2**31 - 1


def _map(value: Any) -> dict[str, Any]:
    """Map one value through the mapper; tests index the sole `"k"` entry off the result."""
    return EngineRest._to_camunda_vars({"k": value})


# ---------------------------------------------------------------------------
# The two branches this PR adds (the defect: float -> String, list/dict -> repr String)
# ---------------------------------------------------------------------------


def test_float_is_double() -> None:
    # THE regression this fixes: a float was previously typed "String", so a numeric `<= 0`
    # guard downstream received a str and raised TypeError on the comparison.
    assert _map(3.14)["k"] == {"value": 3.14, "type": "Double"}


def test_list_is_json_exact_json_dumps() -> None:
    # Raw list -> Json using the harness's EXACT json.dumps signature, never Python repr().
    payload = [1, 2, 3]
    assert _map(payload)["k"] == {
        "value": json.dumps(payload, ensure_ascii=False, default=str),
        "type": "Json",
    }


def test_dict_is_json_exact_json_dumps() -> None:
    payload = {"a": 1, "b": [1, 2, 3]}
    assert _map(payload)["k"] == {
        "value": json.dumps(payload, ensure_ascii=False, default=str),
        "type": "Json",
    }
    # Byte-for-byte parity with harness.py's own dict example (`{"a": 1}` -> `'{"a": 1}'`).
    assert _map({"a": 1})["k"]["value"] == '{"a": 1}'


def test_json_branch_uses_ensure_ascii_false() -> None:
    # `ensure_ascii=False` is load-bearing parity with the harness: non-ASCII (PT-BR) content
    # stays literal, never `\uXXXX`-escaped — this is what distinguishes the raw-value branch
    # from a bare `json.dumps(v)`.
    assert _map({"motivo": "Não coberto"})["k"]["value"] == '{"motivo": "Não coberto"}'


def test_json_round_trips_through_json_loads() -> None:
    # The outbound Json string is the exact counterpart the inbound decode (`_from_camunda_var`
    # in harness/dmn seams) reads back with `json.loads` — prove the round-trip, not just the tag.
    payload = {"linhas_conta_refs": [{"ref": "L1"}, {"ref": "L2"}], "valor_centavos": 1000}
    wire = _map(payload)["k"]
    assert wire["type"] == "Json"
    assert json.loads(wire["value"]) == payload


# ---------------------------------------------------------------------------
# Pre-existing branches — pinned so the added branches did not perturb ordering/typing
# ---------------------------------------------------------------------------


def test_bool_is_boolean_not_integer() -> None:
    # `isinstance(True, int)` is True, so the bool branch MUST precede the int branch — else a
    # bool would be mistyped Integer.
    assert _map(True)["k"] == {"value": True, "type": "Boolean"}
    assert _map(False)["k"] == {"value": False, "type": "Boolean"}


def test_small_int_is_integer() -> None:
    assert _map(42)["k"] == {"value": 42, "type": "Integer"}


def test_int32_max_is_integer_at_boundary() -> None:
    # Exactly int32-max still fits -> Integer (the inclusive upper edge of the boundary).
    assert _map(_INT32_MAX)["k"] == {"value": _INT32_MAX, "type": "Integer"}


def test_over_int32_is_long() -> None:
    # One past int32-max overflows java.lang.Integer -> Long (e.g. R$50MM = 5e9 cents).
    over = _INT32_MAX + 1
    assert _map(over)["k"] == {"value": over, "type": "Long"}


def test_str_is_string() -> None:
    assert _map("GUIA_INCOMPLETA")["k"] == {"value": "GUIA_INCOMPLETA", "type": "String"}


def test_none_is_string_null() -> None:
    assert _map(None)["k"] == {"value": None, "type": "String"}


# ---------------------------------------------------------------------------
# Backward-compat: phase2-land `_double_var()`/`_json_var()` workarounds MUST keep working.
# Those helpers emit a PRE-SHAPED `{"value": .., "type": ..}` dict; the passthrough branch
# (checked FIRST) must return it UNCHANGED, and the new raw-dict/list branch must NOT re-wrap it.
# ---------------------------------------------------------------------------


def test_double_var_shape_passes_through_unchanged() -> None:
    # Exactly what phase2-land `_double_var(900.00)` produces: {"value": 900.0, "type": "Double"}.
    double_var = {"value": 900.0, "type": "Double"}
    assert _map(double_var)["k"] == double_var


def test_json_var_shape_passes_through_not_double_encoded() -> None:
    # Exactly what phase2-land `_json_var([...])` produces (plain json.dumps there). The mapper
    # must return it verbatim — NOT re-encode the already-`Json` string into a nested Json.
    inner = json.dumps([{"ref": "L1"}, {"ref": "L2"}])
    json_var = {"value": inner, "type": "Json"}
    result = _map(json_var)["k"]
    assert result == json_var
    # The inner value is still the ORIGINAL json string, never doubly-encoded.
    assert result["value"] == '[{"ref": "L1"}, {"ref": "L2"}]'


def test_explicit_long_override_shape_passes_through() -> None:
    # The engine_rest docstring's documented passthrough use: caller forces an explicit type.
    long_var = {"value": 5_000_000_000, "type": "Long"}
    assert _map(long_var)["k"] == long_var
