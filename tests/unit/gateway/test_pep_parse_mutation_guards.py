"""Mutation guards for the PEP policy parser (forensic audit R-117 / mutmut survivors F-187).

Each test kills a mutant that survived the existing suite: the effective `hard` is the UNION of
the YAML flag and the code-frozen set; malformed core / frozen entries fail closed; and a
`_hard_frozen.yaml` that diverges from the code is refused.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from maezo.gateway.pep import HARD_ACTIONS, PolicyError, _assert_hard_frozen_matches, _parse_core


def _core(extra: dict[str, Any] | None = None) -> dict[str, Any]:
    actions: dict[str, Any] = {name: {"level": "L0", "hard": True} for name in HARD_ACTIONS}
    actions.update(extra or {})
    return {"actions": actions}


def test_hard_from_code_even_if_yaml_omits_the_flag() -> None:
    name = sorted(HARD_ACTIONS)[0]
    core = _core()
    core["actions"][name] = {"level": "L0"}  # YAML "forgets" hard
    assert _parse_core(core)[name].hard is True


def test_hard_from_yaml_for_an_action_outside_the_frozen_set() -> None:
    parsed = _parse_core(_core({"acao_extra_hard": {"level": "L0", "hard": True}}))
    assert parsed["acao_extra_hard"].hard is True


def test_non_hard_action_stays_non_hard() -> None:
    parsed = _parse_core(_core({"acao_comum": {"level": "L2"}}))
    assert parsed["acao_comum"].hard is False


@pytest.mark.parametrize("spec", ["L0", ["L0"], {"hard": True}, None])
def test_malformed_core_entry_fails_closed(spec: Any) -> None:
    with pytest.raises(PolicyError):
        _parse_core(_core({"acao_ruim": spec}))


def _write_frozen(tmp_path: Path, body: str) -> Path:
    core = tmp_path / "L0-core.yaml"
    core.write_text("actions: {}\n", encoding="utf-8")
    (tmp_path / "_hard_frozen.yaml").write_text(body, encoding="utf-8")
    return core


@pytest.mark.parametrize("entry", ["- just-a-string", "- {nome: x}"])
def test_malformed_frozen_entry_fails_closed(tmp_path: Path, entry: str) -> None:
    core = _write_frozen(tmp_path, f"hard_items:\n  {entry}\n")
    with pytest.raises(PolicyError):
        _assert_hard_frozen_matches(core)


def test_frozen_file_diverging_from_code_is_refused(tmp_path: Path) -> None:
    items = "".join(f"  - action: {n}\n" for n in sorted(HARD_ACTIONS)[1:])  # one missing
    core = _write_frozen(tmp_path, "hard_items:\n" + items + "  - action: acao_so_no_yaml\n")
    with pytest.raises(PolicyError) as exc:
        _assert_hard_frozen_matches(core)
    assert "acao_so_no_yaml" in str(exc.value)
    assert sorted(HARD_ACTIONS)[0] in str(exc.value)


def test_frozen_file_missing_a_code_hard_item_is_refused(tmp_path: Path) -> None:
    """A `_hard_frozen.yaml` that is a strict SUBSET of the code set is refused (equality, not inclusion)."""
    dropped = sorted(HARD_ACTIONS)[0]
    items = "".join(f"  - action: {n}\n" for n in sorted(HARD_ACTIONS) if n != dropped)
    core = _write_frozen(tmp_path, "hard_items:\n" + items)
    with pytest.raises(PolicyError) as exc:
        _assert_hard_frozen_matches(core)
    assert dropped in str(exc.value)


def test_frozen_file_matching_code_is_accepted(tmp_path: Path) -> None:
    items = "".join(f"  - action: {n}\n" for n in sorted(HARD_ACTIONS))
    _assert_hard_frozen_matches(_write_frozen(tmp_path, "hard_items:\n" + items))
