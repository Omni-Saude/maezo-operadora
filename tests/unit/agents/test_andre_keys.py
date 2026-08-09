"""M-8: Andre's two business-key families have ONE strict composer each, and it cannot collide.

Before this, each family had TWO composers that silently disagreed:

  ADEQ   `delegation.adequacao_task_id` neither stripped nor dropped empty segments;
         `graph._business_key` did both. Dropping made the key AMBIGUOUS — a positional 3-tuple
         collapsed to a 2-segment string, so two DIFFERENT cells composed to ONE key.
  PAGTO  `delegation.pagto_task_id` normalised every input and raised on a degenerate case;
         `graph._business_key` normalised nothing and happily minted `PAGTO-{tenant}--`.

Two divergent composers for one key is a duplicate-instance defect by construction: whichever
side performs the idempotent lookup cannot find the instance the other side keyed.

These tests pin (1) the collision vectors, (2) the normalisation divergence — including the
int/str/whitespace cases — and (3) CROSS-SITE PARITY: the delegation `task_id` and the graph
anchor must be byte-identical for the same case.
"""

from __future__ import annotations

from typing import Any

import pytest

from maezo.agents.andre.delegation import adequacao_task_id, pagto_task_id
from maezo.agents.andre.graph import _business_key
from maezo.agents.andre.keys import (
    adequacao_business_key,
    is_blank,
    key_segment,
    pagto_business_key,
)

# ---------------------------------------------------------------------------
# THE COLLISION (the reason empty segments must be an ERROR, not a drop)
# ---------------------------------------------------------------------------


def test_the_dropped_segment_collision_is_now_structurally_impossible() -> None:
    """The exact M-8 vector. Under segment-dropping BOTH of these composed to
    `ADEQ-amh-R-001-2026-Q3`: one cell whose especialidade happened to look like a cycle, and one
    with no especialidade at all. Two distinct cells, one anchor.

    Position-preserving composition cannot express either shape: a blank required segment raises,
    so neither input can reach a key at all — let alone the SAME key.
    """
    with pytest.raises(ValueError):
        adequacao_business_key("amh", "R-001", "2026-Q3", "")  # ciclo blank -> would drop
    with pytest.raises(ValueError):
        adequacao_business_key("amh", "R-001", "", "2026-Q3")  # especialidade blank -> would drop


def test_a_cycle_only_distinguishes_cells_that_are_otherwise_identical() -> None:
    """Positive side of the same property: the ciclo segment still does its job, and the 3- and
    4-segment forms are never confusable."""
    without = adequacao_business_key("amh", "R-001", "cardiologia")
    with_cycle = adequacao_business_key("amh", "R-001", "cardiologia", "2026-Q3")

    assert without == "ADEQ-amh-R-001-cardiologia"
    assert with_cycle == "ADEQ-amh-R-001-cardiologia-2026-Q3"
    assert without != with_cycle
    # `None` means "no cycle" and composes the 3-segment key; a blank STRING is a caller error.
    assert adequacao_business_key("amh", "R-001", "cardiologia", None) == without


@pytest.mark.parametrize(
    ("a", "b"),
    [
        # A segment whose own value contains the delimiter can impersonate a segment boundary.
        (("amh", "R-001", "cardio", "Q3"), ("amh", "R-001", "cardio-Q3", None)),
        (("amh", "R", "001-cardio", None), ("amh", "R-001", "cardio", None)),
    ],
)
def test_residual_delimiter_ambiguity_is_disclosed_not_fixed(a: tuple[Any, ...], b: tuple[Any, ...]) -> None:
    """CHARACTERIZATION (a known limitation, deliberately NOT claimed as fixed).

    M-8 removed the collision the composer ITSELF manufactured by dropping empty segments. It did
    NOT — and cannot, without changing the contract — remove the ambiguity inherent to a
    `-`-delimited key whose SEGMENT VALUES may themselves contain `-`: a region code `R-001` with
    speciality `cardio` composes the same string as region `R` with speciality `001-cardio`.

    This test asserts the collision STILL EXISTS so the residual risk is visible and measured
    rather than assumed away. Closing it needs a contract-level decision (a delimiter that cannot
    occur in a segment, or per-segment escaping) affecting the key format SP-OP-ADEQUACAO-001 and
    the engine both use — out of scope for an idempotency fix, and not an engineering agent's call.
    """
    assert a != b
    assert adequacao_business_key(*a) == adequacao_business_key(*b)


# ---------------------------------------------------------------------------
# Normalisation divergence: int / str / whitespace
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("OP-001", "OP-001"),
        ("  OP-001  ", "OP-001"),  # the graph copy kept the padding, delegation stripped it
        (123, "123"),  # int id: only one side used str()
        (None, ""),  # never the literal "None" (the `non_blank` defect class)
        (0, ""),  # falsy int reads as absent on BOTH sides
        ("", ""),
        ("   ", ""),
    ],
)
def test_key_segment_normalises_identically_for_every_input_shape(value: Any, expected: str) -> None:
    assert key_segment(value) == expected
    assert is_blank(value) is (expected == "")


def test_whitespace_padded_ordem_no_longer_composes_two_different_payment_keys() -> None:
    """The concrete PAGTO divergence: `graph._business_key` interpolated the raw value, so a
    padded `ordem_pagamento_id` anchored `PAGTO-amh- OP-001 ` while the delegation `task_id` said
    `PAGTO-amh-OP-001`. The idempotent start would then MISS its own instance."""
    from_delegation = pagto_task_id("amh", ordem_pagamento_id="  OP-001  ")
    from_graph = _business_key(
        {"flow": "pagto_dossier", "tenant_id": "amh", "ordem_pagamento_id": "  OP-001  "}  # type: ignore[arg-type]
    )

    assert from_delegation == from_graph == "PAGTO-amh-OP-001"


def test_int_ordem_composes_the_same_key_on_both_sides() -> None:
    assert (
        pagto_task_id("amh", ordem_pagamento_id=123)
        == _business_key({"flow": "pagto_dossier", "tenant_id": "amh", "ordem_pagamento_id": 123})  # type: ignore[arg-type]
        == "PAGTO-amh-123"
    )


def test_int_zero_ordem_is_absent_on_both_sides_and_refuses_rather_than_degenerating() -> None:
    """`0` is falsy, so both sides treated it as "no ordem" — but only ONE of them then refused.
    The graph fell through to `PAGTO-{tenant}--`, a degenerate key that would happily anchor a
    payment. Both now raise."""
    with pytest.raises(ValueError, match="pagto business key requires"):
        pagto_task_id("amh", ordem_pagamento_id=0)
    with pytest.raises(ValueError, match="pagto business key requires"):
        _business_key({"flow": "pagto_dossier", "tenant_id": "amh", "ordem_pagamento_id": 0})  # type: ignore[arg-type]


def test_the_degenerate_pagto_key_is_structurally_unreachable_from_the_graph() -> None:
    """`PAGTO-amh--` was mintable from the graph side for a case with no identifiers at all."""
    with pytest.raises(ValueError):
        _business_key({"flow": "pagto_dossier", "tenant_id": "amh"})  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# CROSS-SITE PARITY — the property the false docstring used to assert
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "cell",
    [
        {"regiao_saude": "R-001", "especialidade": "cardiologia"},
        {"regiao_saude": "R-001", "especialidade": "cardiologia", "ciclo_avaliacao": "2026-Q3"},
        {"regiao_saude": "  R-001 ", "especialidade": " cardiologia", "ciclo_avaliacao": " 2026-Q3 "},
    ],
)
def test_adequacao_delegation_task_id_equals_the_graph_anchor(cell: dict[str, str]) -> None:
    """`adequacao_task_id`'s docstring USED to claim "the SAME cell format `andre.graph.
    _business_key` anchors" while composing a different string. Now it is literally the same
    function, and this test is the executable form of that claim."""
    task_id = adequacao_task_id(
        "amh", cell["regiao_saude"], cell["especialidade"], cell.get("ciclo_avaliacao")
    )
    anchor = _business_key({"flow": "adequacao_dossier", "tenant_id": "amh", **cell})  # type: ignore[arg-type]

    assert task_id == anchor


@pytest.mark.parametrize(
    "case",
    [
        {"ordem_pagamento_id": "OP-001"},
        {"numero_lote_tiss": "L-9", "prestador_id": "P-3"},
        {"ordem_pagamento_id": "OP-001", "numero_lote_tiss": "L-9", "prestador_id": "P-3"},
        {"ordem_pagamento_id": " OP-001 ", "numero_lote_tiss": " ", "prestador_id": None},
    ],
)
def test_pagto_delegation_task_id_equals_the_graph_business_key(case: dict[str, Any]) -> None:
    task_id = pagto_task_id(
        "amh",
        ordem_pagamento_id=case.get("ordem_pagamento_id") or "",
        numero_lote_tiss=case.get("numero_lote_tiss") or "",
        prestador_id=case.get("prestador_id") or "",
    )
    anchor = _business_key({"flow": "pagto_dossier", "tenant_id": "amh", **case})  # type: ignore[arg-type]

    assert task_id == anchor


def test_ordem_first_derivation_is_preserved_on_both_sides() -> None:
    """GK-dossier finding 1a's ordem-FIRST rule is unchanged by the refactor."""
    assert pagto_task_id("amh", ordem_pagamento_id="OP-1", numero_lote_tiss="L", prestador_id="P") == (
        "PAGTO-amh-OP-1"
    )


# ---------------------------------------------------------------------------
# Engine-key passthrough + tenant scope (ADR-0004) survive the consolidation
# ---------------------------------------------------------------------------


def test_threaded_engine_key_is_honoured_verbatim_when_tenant_scoped() -> None:
    assert (
        pagto_business_key("amh", business_key="PAGTO-amh-L9-P3", ordem_pagamento_id="OP-001")
        == "PAGTO-amh-L9-P3"
    )


def test_foreign_tenant_engine_key_is_rejected_and_falls_back_to_the_derivation() -> None:
    """A planted key from another tenant can never anchor this case (ADR-0004)."""
    assert (
        pagto_business_key("amh", business_key="PAGTO-other-L9-P3", ordem_pagamento_id="OP-001")
        == "PAGTO-amh-OP-001"
    )


def test_blank_tenant_refuses_on_both_families() -> None:
    with pytest.raises(ValueError, match="non-blank tenant"):
        pagto_business_key("   ", ordem_pagamento_id="OP-001")
    with pytest.raises(ValueError, match="non-blank tenant"):
        adequacao_business_key("   ", "R-001", "cardiologia")


# ---------------------------------------------------------------------------
# One composer per family — no third copy may reappear
# ---------------------------------------------------------------------------


def test_delegation_and_graph_both_route_through_the_shared_module() -> None:
    """Arch guard: the two former copies must not grow back. Any `-{...}` f-string composing an
    `ADEQ-`/`PAGTO-` key outside `keys.py` is a new divergence."""
    import ast
    from pathlib import Path

    import maezo.agents.andre as andre_pkg

    pkg = Path(andre_pkg.__file__).parent
    for module in ("delegation.py", "graph.py"):
        source = (pkg / module).read_text(encoding="utf-8")
        tree = ast.parse(source, filename=module)
        literals = [
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        ]
        composing = [
            lit for lit in literals if lit.startswith(("ADEQ-{", "PAGTO-{")) or lit in {"ADEQ-", "PAGTO-"}
        ]
        assert not composing, (
            f"{module} re-introduced a local business-key composer ({composing!r}) — both families "
            "must compose through `maezo.agents.andre.keys` or they will diverge again (M-8)."
        )
