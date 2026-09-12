"""R-173 / Wave 3: integer centavos remain exact at all three serializer seams.

These are local mapper tests, without an engine or a claim of reachable bad production input.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from copy import deepcopy
from typing import Any

import pytest

from maezo.tools.mcp_cibseven.transport import _to_camunda_vars as _process_vars
from maezo.tools.workers.dmn_transport import _to_camunda_vars as _dmn_vars
from maezo.tools.workers.harness import _to_camunda_var

_NAME = "total_glosado_candidato_centavos"
Mapper = Callable[[str, Any], dict[str, Any]]


@pytest.fixture(params=["worker", "process", "dmn"])
def mapper(request: pytest.FixtureRequest) -> Mapper:
    if request.param == "worker":
        return lambda name, value: _to_camunda_var(value, name=name)
    if request.param == "process":
        return lambda name, value: _process_vars({name: value})[name]
    return lambda name, value: _dmn_vars({name: value})[name]


@pytest.mark.parametrize("value", [0, 17, 2**31 - 1, 2**31, 2**53 + 1, 2**63 - 1, -(2**63)])
@pytest.mark.parametrize("shape", ["raw", "untyped", "Integer", "Long"])
def test_exact_int64_centavos_and_metadata(mapper: Mapper, value: int, shape: str) -> None:
    entry: Any = value
    expected: dict[str, Any] = {"value": value, "type": "Long"}
    if shape != "raw":
        entry = {"value": value, "valueInfo": {"transient": True}}
        expected["valueInfo"] = {"transient": True}
        if shape != "untyped":
            entry["type"] = shape
    original = deepcopy(entry)
    result = mapper(_NAME, entry)
    assert result == expected
    assert type(result["value"]) is int
    assert json.loads(json.dumps(result)) == expected
    assert entry == original
    assert mapper(_NAME, result) == expected


@pytest.mark.parametrize(
    "value",
    [
        True,
        False,
        0.25,
        17.0,
        float("nan"),
        float("inf"),
        "17",
        "17.0",
        "x",
        "",
        None,
        2**63,
        -(2**63) - 1,
        {},
        [],
        {"value": {"value": 17}},
    ],
    ids=[
        "true",
        "false",
        "fraction",
        "float-int",
        "nan",
        "inf",
        "digits",
        "decimal",
        "text",
        "blank",
        "null",
        "overflow",
        "underflow",
        "dict",
        "list",
        "nested",
    ],
)
@pytest.mark.parametrize("shape", ["raw", "Long", "Integer", "Double"])
def test_invalid_centavos_fail_closed(mapper: Mapper, value: Any, shape: str) -> None:
    entry = value if shape == "raw" else {"value": value, "type": shape}
    with pytest.raises(ValueError, match="integer centavos within int64"):
        mapper(_NAME, entry)


@pytest.mark.parametrize("wire_type", ["Double", "String", "Boolean", "Json", None, "long", 7])
def test_noninteger_wire_type_is_not_silently_coerced(mapper: Mapper, wire_type: Any) -> None:
    with pytest.raises(ValueError, match="integer centavos within int64"):
        mapper(_NAME, {"value": 17, "type": wire_type})


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (17, {"value": 17, "type": "Integer"}),
        (2**31, {"value": 2**31, "type": "Long"}),
        (True, {"value": True, "type": "Boolean"}),
        (0.25, {"value": 0.25, "type": "Double"}),
        ("17", {"value": "17", "type": "String"}),
        (None, {"value": None, "type": "String"}),
        (["ação", 17], {"value": '["ação", 17]', "type": "Json"}),
    ],
)
def test_other_fields_keep_existing_transport(mapper: Mapper, value: Any, expected: dict[str, Any]) -> None:
    assert mapper("outro_campo", value) == expected


@pytest.mark.parametrize(
    "entry",
    [
        {"value": 17, "type": "Integer"},
        {"value": True, "type": "Long"},
        {"value": 0.25, "type": "Double", "valueInfo": {"transient": True}},
        {"value": '{"n": 17}', "type": "Json"},
    ],
)
def test_other_preshaped_fields_are_preserved(mapper: Mapper, entry: dict[str, Any]) -> None:
    assert mapper("outro_campo", entry) is entry
