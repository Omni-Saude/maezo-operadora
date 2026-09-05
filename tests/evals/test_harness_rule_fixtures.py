"""Guardas de `RuleAwareFakeDmnTransport`/`register_dmn_fixture` (RAF-06, RAF-01, 2026-09-04).

`_harness.py`'s own docstrings document two loud, fail-closed guards for the `__rules__`
conditional-fixture shape (see `RuleAwareFakeDmnTransport` and `register_dmn_fixture`):

    (i)  a `dmn_fixture` value carrying `__rules__` handed to a PLAIN `FakeDmnTransport`
         raises `TypeError` instead of silently degrading to a static row;
    (ii) a `RuleAwareFakeDmnTransport` whose `__rules__` have no catch-all and whose `when`
         clauses match nothing raises `AssertionError` (a fixture bug) instead of a silent
         fail-safe.

Both were documented (README.md's `dmn_fixture` section) but untested before this file — the
VERIFY-RAF01 REVISE (F2). These two tests close that gap; each is proven non-vacuous by a
mutation probe in the accompanying report (neutralize the guard, confirm the test fails,
`git checkout --` to restore).
"""

from __future__ import annotations

import pytest

from maezo.tools.workers.dmn_transport import FakeDmnTransport

from ._harness import RuleAwareFakeDmnTransport, register_dmn_fixture


@pytest.mark.eval
def test_register_dmn_fixture_rejects_rules_on_plain_fake_transport() -> None:
    """(i) A `__rules__` fixture handed to a plain `FakeDmnTransport` (not rule-aware) raises
    `TypeError` naming `__rules__` — never silently registers the catch-all as a static row."""
    with pytest.raises(TypeError, match="__rules__"):
        register_dmn_fixture(
            FakeDmnTransport(),
            {
                "auth_auto_approval": {
                    "__rules__": [
                        {
                            "when": {"auto_criteria_verificado": True},
                            "then": {"recomendacao": "AUTO_APROVAR", "motivo": "r1"},
                        },
                        {"then": {"recomendacao": "ANALISE_HUMANA", "motivo": "r99_catch_all"}},
                    ]
                }
            },
        )


@pytest.mark.eval
async def test_rule_aware_transport_raises_when_no_rule_matches_and_no_catch_all() -> None:
    """(ii) A `__rules__` fixture with NO catch-all, evaluated against variables that match no
    `when`, raises `AssertionError` naming the fixture bug — never falls back to a silent
    default. Mirrors `_harness.py`'s own worked example, minus the catch-all rule."""
    dmn = RuleAwareFakeDmnTransport()
    register_dmn_fixture(
        dmn,
        {
            "auth_auto_approval": {
                "__rules__": [
                    {
                        "when": {"auto_criteria_verificado": True, "criterio_tecnico_ok": True},
                        "then": {"recomendacao": "AUTO_APROVAR", "motivo": "r1"},
                    }
                ]
            }
        },
    )
    with pytest.raises(AssertionError, match="bug de fixture"):
        await dmn.evaluate(
            "auth_auto_approval",
            {"auto_criteria_verificado": False, "criterio_tecnico_ok": False},
        )
