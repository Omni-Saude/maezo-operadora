"""Autonomy-policy artifact validation (`spec/policies/autonomy/*.yaml` — ADR-0008).

Ported from the v1 donor repo's `platform/validation/autonomy.py` (frozen-set
gate), adapted to this repo's `Report`/`_loaders` and to the real shape of
`spec/policies/autonomy/` (`L0-core.yaml`, `_hard_frozen.yaml`,
`tenants-amh.yaml`).

Checks:
- schema: every entry under `actions:` in a `*-core.yaml` file has a `level`
  in {L0, L1, L2, L3};
- `hard: true` actions must be `level: L0` (ADR-0008: the immutable floor is
  always L0);
- immutability guardrail: `_hard_frozen.yaml` must match, item-for-item and
  level-for-level, the set of `hard: true` actions actually declared across
  the `*-core.yaml` files. Any drift — an item removed, added, or re-leveled
  in only one of the two places — is a compliance regression and FAILS the
  gate; it is never a warning;
- tenant override files (`tenants-*.yaml`) may never touch (add/modify) a
  hard-frozen action.
"""

from __future__ import annotations

from pathlib import Path

from ._loaders import ParseError, load_yaml
from .result import Report

VALID_LEVELS = frozenset({"L0", "L1", "L2", "L3"})
FROZEN_FILENAME = "_hard_frozen.yaml"


def _as_dict(value: object) -> dict[str, object] | None:
    return value if isinstance(value, dict) else None


def load_frozen_hard(path: Path, report: Report) -> dict[str, str] | None:
    """Load `_hard_frozen.yaml` -> {action: level}. Returns None if invalid."""
    if not path.exists():
        report.error(path, f"frozen hard-item list {FROZEN_FILENAME} is missing (compliance guardrail)")
        return None
    try:
        data = _as_dict(load_yaml(path))
    except ParseError as exc:
        report.error(path, str(exc))
        return None
    if data is None:
        report.error(path, "root is not a mapping")
        return None
    items = data.get("hard_items")
    if not isinstance(items, list):
        report.error(path, "'hard_items' is missing or is not a list")
        return None
    frozen: dict[str, str] = {}
    for entry in items:
        entry_dict = _as_dict(entry)
        if entry_dict is None or "action" not in entry_dict:
            report.error(path, f"invalid hard_items entry: {entry!r}")
            continue
        frozen[str(entry_dict["action"])] = str(entry_dict.get("level", "L0"))
    return frozen


def validate_core_file(path: Path, report: Report) -> dict[str, str]:
    """Validate one core-layer file (`L*-core.yaml`); returns its {action: level} hard items."""
    hard_found: dict[str, str] = {}
    try:
        data = _as_dict(load_yaml(path))
    except ParseError as exc:
        report.error(path, str(exc))
        return hard_found
    if data is None:
        report.error(path, "root is not a mapping")
        return hard_found

    actions = _as_dict(data.get("actions"))
    if actions is None:
        report.error(path, "'actions' is missing or is not a mapping")
        return hard_found

    for name, spec in actions.items():
        spec_dict = _as_dict(spec)
        if spec_dict is None:
            report.error(path, f"action '{name}' is not a mapping")
            continue
        level = spec_dict.get("level")
        if level not in VALID_LEVELS:
            report.error(path, f"action '{name}' has invalid level: {level!r} (expected one of L0-L3)")
        if spec_dict.get("hard") is True:
            if level != "L0":
                report.error(path, f"hard action '{name}' must be level L0, not {level!r}")
            hard_found[str(name)] = str(level)
    return hard_found


def validate_tenant_file(path: Path, frozen: dict[str, str], report: Report) -> None:
    """Validate a tenant override file: it must never touch a hard-frozen action."""
    try:
        data = _as_dict(load_yaml(path))
    except ParseError as exc:
        report.error(path, str(exc))
        return
    if data is None:
        report.error(path, "root is not a mapping")
        return
    overrides = _as_dict(data.get("overrides")) or {}
    for name, spec in overrides.items():
        if name in frozen:
            report.error(
                path,
                f"tenant override touches hard-frozen action '{name}' — forbidden (ADR-0008)",
            )
            continue
        spec_dict = _as_dict(spec)
        if spec_dict is not None and "level" in spec_dict and spec_dict["level"] not in VALID_LEVELS:
            report.error(path, f"override '{name}' has invalid level: {spec_dict['level']!r}")


def reconcile_frozen(
    frozen: dict[str, str], hard_found: dict[str, str], frozen_path: Path, report: Report
) -> None:
    """Compare the frozen list against the hard items actually found in the core layers."""
    missing = set(frozen) - set(hard_found)  # frozen but gone from core => removed/downgraded
    extra = set(hard_found) - set(frozen)  # new hard item in core, not registered in the frozen list
    for action in sorted(missing):
        report.error(
            frozen_path,
            f"frozen hard item '{action}' is no longer present/hard in the core layers "
            "(removal or downgrade of an immutable rule — blocked)",
        )
    for action in sorted(extra):
        report.error(
            frozen_path,
            f"new hard item '{action}' in the core layers is not registered in {FROZEN_FILENAME} "
            "(add it to the frozen list in the same change)",
        )
    for action in sorted(set(frozen) & set(hard_found)):
        if frozen[action] != hard_found[action]:
            report.error(
                frozen_path,
                f"hard item '{action}': level {hard_found[action]!r} differs from frozen "
                f"{frozen[action]!r} (change to an immutable rule — blocked)",
            )


def validate_dir(root: Path, report: Report) -> None:
    """Validate the whole `policies/autonomy/` directory (core + tenants + frozen)."""
    if not root.exists() or not root.is_dir():
        report.error(root, "autonomy policy directory does not exist")
        return

    yaml_files = sorted(p for p in root.glob("*.yaml") if p.name != FROZEN_FILENAME)
    if not yaml_files:
        report.error(root, "no autonomy policy YAML files found")

    frozen_path = root / FROZEN_FILENAME
    frozen = load_frozen_hard(frozen_path, report)

    hard_found: dict[str, str] = {}
    core_files = [p for p in yaml_files if p.name.endswith("-core.yaml")]
    if not core_files:
        report.error(root, "no '*-core.yaml' autonomy policy file found")
    for path in core_files:
        hard_found.update(validate_core_file(path, report))

    if frozen is not None:
        for path in yaml_files:
            if path.name.startswith("tenants-"):
                validate_tenant_file(path, frozen, report)
        reconcile_frozen(frozen, hard_found, frozen_path, report)
