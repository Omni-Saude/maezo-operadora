"""Unit tests for maezo.a2a.anti_loop — AntiLoopGuard (ADR-0003, ADR-0015).

TDD London School: tests written BEFORE implementation.
"""

import pytest

from maezo.a2a.anti_loop import AntiLoopGuard, CyclicDelegationError, MaxDepthExceededError


def test_validate_chain_empty() -> None:
    """An empty delegation chain must pass validation."""
    guard = AntiLoopGuard(max_depth=3)
    # Must not raise
    guard.validate_chain([])


def test_validate_chain_single_hop() -> None:
    """A single-hop delegation chain must pass validation."""
    guard = AntiLoopGuard(max_depth=3)
    guard.validate_chain(["helena"])


def test_validate_chain_valid_three_hops() -> None:
    """A 3-hop chain (helena → rafael → beatriz) must pass when max_depth=3."""
    guard = AntiLoopGuard(max_depth=3)
    guard.validate_chain(["helena", "rafael", "beatriz"])


def test_max_depth_exceeded() -> None:
    """A chain exceeding max_depth must raise MaxDepthExceededError."""
    guard = AntiLoopGuard(max_depth=3)

    with pytest.raises(MaxDepthExceededError, match="max depth"):
        guard.validate_chain(["helena", "rafael", "beatriz", "carla"])


def test_max_depth_custom() -> None:
    """max_depth can be configured and must be enforced accordingly."""
    guard = AntiLoopGuard(max_depth=2)

    # 2 hops: ok
    guard.validate_chain(["helena", "rafael"])

    # 3 hops: exceeds
    with pytest.raises(MaxDepthExceededError):
        guard.validate_chain(["helena", "rafael", "beatriz"])


def test_cycle_detected_direct() -> None:
    """Direct cycle (A → B → A) must raise CyclicDelegationError."""
    guard = AntiLoopGuard(max_depth=5)

    with pytest.raises(CyclicDelegationError, match="cycle"):
        guard.validate_chain(["helena", "rafael", "helena"])


def test_cycle_detected_self_loop() -> None:
    """Self-loop (A → A) must raise CyclicDelegationError."""
    guard = AntiLoopGuard(max_depth=5)

    with pytest.raises(CyclicDelegationError, match="cycle"):
        guard.validate_chain(["helena", "helena"])


def test_cycle_detected_long_chain() -> None:
    """Cycle in a longer chain (A → B → C → D → B) must be detected."""
    guard = AntiLoopGuard(max_depth=5)

    with pytest.raises(CyclicDelegationError, match="cycle"):
        guard.validate_chain(["helena", "rafael", "beatriz", "carla", "rafael"])


def test_max_depth_before_cycle() -> None:
    """max_depth is checked BEFORE cycle detection (first guard wins).
    If a chain exceeds max_depth AND contains a cycle, MaxDepthExceededError is raised first.
    """
    guard = AntiLoopGuard(max_depth=3)

    # 4 hops with cycle: max_depth=3 is exceeded first
    with pytest.raises(MaxDepthExceededError):
        guard.validate_chain(["helena", "rafael", "beatriz", "helena"])


def test_default_max_depth() -> None:
    """Default max_depth must be 3 (per ADR-0003)."""
    guard = AntiLoopGuard()
    assert guard.max_depth == 3


def test_cycle_in_middle_of_chain() -> None:
    """Cycle in the middle of the chain (not at the end) must be detected."""
    guard = AntiLoopGuard(max_depth=10)

    with pytest.raises(CyclicDelegationError, match="cycle"):
        guard.validate_chain(["a", "b", "c", "b", "d"])
