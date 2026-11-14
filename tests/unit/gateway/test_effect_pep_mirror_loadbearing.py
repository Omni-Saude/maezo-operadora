"""W7-7.3 fence — the MIRRORED ADR-0016 allowlist in `maezo.gateway.effect_pep` is load-bearing.

The 1:1 mirror check ALREADY exists and stays the authority for mirror-vs-source drift:

- `tests/unit/gateway/test_effect_pep.py:982-990`
  (`test_the_mirrored_process_key_constants_match_the_adr0016_source`): set equality of
  `KNOWN_PROCESS_KEYS` and pattern equality of `_PROCESS_KEY_RE` against
  `maezo.tools.process_allowlist` (`:21-42` / `:63`);
- `tests/unit/gateway/test_effect_pep.py:993-1022`
  (`test_every_mirrored_pattern_anchors_at_the_true_end_of_the_string`): the `\\Z` (never `$`)
  discipline on BOTH sides of every mirror.

What the equality tests CANNOT see is the mirror becoming DECORATIVE: if a future edit replaces
the module-global reference at the enforcement point with an inlined literal copy (same 15 keys,
same pattern), every existing test stays green — the mirror would keep matching the source while
no longer being what the gate consults, and the NEXT source-side key would silently not reach
L-1. This file pins the load-bearing direction:

- L-1 intersection consults the MODULE GLOBAL (`effect_pep.py:487`,
  `declared_keys & KNOWN_PROCESS_KEYS` — the R-1 closure);
- `EffectCall.__post_init__` validates `process_key` through the MODULE GLOBAL
  (`effect_pep.py:322-329`).

Each probe carries its NEGATIVE CONTROL (the unpatched constant admits the same input) so the
fence cannot pass vacuously. A legitimate move away from the module-global idiom makes these red
ON PURPOSE: update the fence in the SAME PR, with the new enforcement point named.
"""

from __future__ import annotations

import re

import pytest

from maezo.gateway import effect_pep
from maezo.gateway.effect_pep import AgentCapabilities, EffectCall, EffectCallError

_TENANT = "amh"
_PRINCIPAL = "rafael"
_OPERATION = "cibseven.start_process"
_ACTION_REF = "agente.cibseven.start_process"
_KEY = "SP-OP-AUTH-001"  # a real ADR-0016 key: admitted by the untouched mirror


def _call() -> EffectCall:
    return EffectCall(
        tenant=_TENANT,
        principal=_PRINCIPAL,
        operation=_OPERATION,
        action_ref=_ACTION_REF,
        process_key=_KEY,
    )


def test_l1_intersects_with_the_mirrored_universe_not_a_local_copy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Negative control: with the real mirror, the declared key survives `AgentCapabilities.of`
    (`effect_pep.py:487`). Probe: EMPTYING the module global must collapse the allowed set —
    possible only if the intersection reads the global. An inlined `frozenset({...})` copy at
    the enforcement point would keep the second assertion red."""
    caps = AgentCapabilities.of(principal=_PRINCIPAL, process_keys=[_KEY])
    assert caps.process_keys == frozenset({_KEY})

    monkeypatch.setattr(effect_pep, "KNOWN_PROCESS_KEYS", frozenset())
    caps_after = AgentCapabilities.of(principal=_PRINCIPAL, process_keys=[_KEY])
    assert caps_after.process_keys == frozenset()


def test_effect_call_validates_the_process_key_through_the_mirrored_pattern(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Negative control: the untouched mirror admits a well-formed ADR-0016 key
    (`effect_pep.py:322-329`). Probe: neutering the module-global pattern must make the SAME
    construction raise `EffectCallError` — possible only if `__post_init__` consults the
    global."""
    assert _call().process_key == _KEY

    monkeypatch.setattr(effect_pep, "_PROCESS_KEY_RE", re.compile(r"(?!x)x"))  # matches nothing
    with pytest.raises(EffectCallError, match="process_key must match"):
        _call()
