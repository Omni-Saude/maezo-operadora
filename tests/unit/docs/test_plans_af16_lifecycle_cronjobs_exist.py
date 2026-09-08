"""AF-16 residual (R-106 half): `PLANS.md` §0 backlog item (6) used to say the lifecycle CronJobs
(`expurgo-working`/`verify-erasure`) were still-to-build ("construir o seam completo fail-closed").
They exist — the Helm templates landed via commit `04eb4ecf` (R-040/SC-07) — and fail-closed BY
DESIGN pending `AF-07`'s retention matrix (`OWNER-DECISIONS-REGISTER` R-106, option B). This fence
pins item (6)'s corrected text against the real Helm template and the real fail-closed module, so
neither can drift without this test catching it — no counts are asserted (AF-06 lesson).
"""

from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).parents[3]
_PLANS = _REPO_ROOT / "PLANS.md"
_CRONJOB_TEMPLATE = _REPO_ROOT / "deploy" / "helm" / "maezo-tenant" / "templates" / "cronjob-lifecycle.yaml"
_LIFECYCLE_MODULE = _REPO_ROOT / "src" / "maezo" / "platform" / "lifecycle" / "__init__.py"


def _item_6_clause() -> str:
    text = _PLANS.read_text(encoding="utf-8")
    marker = "(6) CronJobs de retenção/expurgo"
    assert marker in text, f"item (6) marker not found in {_PLANS}"
    start = text.index(marker)
    end = text.index("; (7)", start)
    return text[start:end]


def test_the_cronjob_helm_template_really_exists_and_declares_the_three_jobs() -> None:
    """Ground truth: the seam PLANS.md now claims exists really does, on disk, today."""
    assert _CRONJOB_TEMPLATE.is_file()
    text = _CRONJOB_TEMPLATE.read_text(encoding="utf-8")
    assert "expurgo-working" in text
    assert "verify-erasure" in text
    assert "audit-retention" in text
    assert "maezo.io/expected-fail-until" in text


def test_the_lifecycle_module_still_fails_closed_with_nothing_implemented() -> None:
    """Ground truth: item (6)'s claim that the CronJobs 'falham POR DESENHO' is still true — the
    module refuses rather than silently succeeding."""
    assert _LIFECYCLE_MODULE.is_file()
    text = _LIFECYCLE_MODULE.read_text(encoding="utf-8")
    assert "fail-closed" in text.lower() or "fail closed" in text.lower()
    assert "NONE of the three is implemented" in text


def test_plans_item_6_no_longer_claims_the_cronjobs_are_unbuilt() -> None:
    """AF-16 residual: revert item (6) back to 'construir o seam completo fail-closed' and this
    goes RED — that phrasing claims the seam does not exist, which is false."""
    clause = _item_6_clause()
    assert "construir o seam completo fail-closed" not in clause
    assert "JÁ EXISTEM" in clause
    assert "AF-07" in clause
    assert "R-106" in clause
