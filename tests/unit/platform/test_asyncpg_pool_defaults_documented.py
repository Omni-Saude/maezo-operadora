"""D4-02 / R-108 (OWNER-DECISIONS-REGISTER, opcao B): the four `asyncpg.create_pool` sites in
this tree share the SAME `min_size=1`/`max_size=10` default, and each carries the SAME
anchor comment declaring that default DELIBERATE (not forgotten) pending SC-05's aggregated
connection arithmetic.

Why a test instead of trusting the comment: the whole point of R-108 is that these four numbers
must move TOGETHER — a silent drift at one site (someone bumps `max_size` on the site they are
touching without checking the other three) reopens exactly the "nobody dimensioned this"
ambiguity D4-02 diagnosed, just at a different number. This test goes RED the moment any of the
four sites disagrees, or the anchor comment is deleted from one of them without the others.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]

#: The four `create_pool` sites D4-02/R-108 named explicitly.
_POOL_SITES: tuple[str, ...] = (
    "src/maezo/a2a/idempotency.py",
    "src/maezo/a2a/outbox.py",
    "src/maezo/platform/integrations/amh_inbox.py",
    "src/maezo/gateway/audit_postgres.py",
)

_ANCHOR_MARKER = "D4-02 / R-108"


def _source(rel_path: str) -> str:
    return (_REPO_ROOT / rel_path).read_text(encoding="utf-8")


@pytest.mark.parametrize("rel_path", _POOL_SITES)
def test_site_carries_the_d4_02_anchor_comment(rel_path: str) -> None:
    assert _ANCHOR_MARKER in _source(rel_path), (
        f"{rel_path} lost the D4-02/R-108 anchor comment — the deliberate-default declaration "
        "must stay next to the numbers it documents"
    )


@pytest.mark.parametrize("rel_path", _POOL_SITES)
def test_site_still_uses_the_documented_default(rel_path: str) -> None:
    source = _source(rel_path)
    assert re.search(r"min_size\s*=\s*1\b", source), f"{rel_path}: min_size default changed"
    assert re.search(r"max_size\s*=\s*10\b", source), f"{rel_path}: max_size default changed"


def test_all_four_sites_agree_on_the_same_min_and_max() -> None:
    """RED proof: if a future edit bumps `max_size` at exactly one site (the classic drift this
    anchor exists to prevent), this fails instead of the four pools silently disagreeing."""
    mins: set[str] = set()
    maxs: set[str] = set()
    for rel_path in _POOL_SITES:
        source = _source(rel_path)
        min_match = re.search(r"min_size\s*=\s*(\d+)", source)
        max_match = re.search(r"max_size\s*=\s*(\d+)", source)
        assert min_match and max_match, f"{rel_path}: create_pool min_size/max_size not found"
        mins.add(min_match.group(1))
        maxs.add(max_match.group(1))
    assert mins == {"1"}, f"min_size disagrees across the four D4-02 sites: {mins}"
    assert maxs == {"10"}, f"max_size disagrees across the four D4-02 sites: {maxs}"
