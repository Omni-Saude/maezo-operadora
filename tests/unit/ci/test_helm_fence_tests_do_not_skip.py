"""Guard: the AF-01/DU-02/SC-01 helm-shelling test modules must never gain a `pytest.skip`
tied to helm's availability (VERIFY-A1-HELM cross-check).

Why this exists
----------------
`validate-helm` (the CI job that actually runs `check_helm_entrypoints.py`/
`check_chart_env_reconciliation.py` as CLI steps) is NOT a required status check in the branch
protection ruleset (`main-protection`, ruleset 20775655, lists only `lint / type / unit`,
`gitleaks / secrets`, `validate-artifacts`, `release-capability-floor`) — a red `validate-helm`
step does not block a merge. The durability of AF-01/DU-02/SC-01 therefore rests ENTIRELY on the
unit tests in `tests/unit/ci/test_check_helm_entrypoints.py`,
`tests/unit/ci/test_check_chart_env_reconciliation.py` and
`tests/unit/platform/test_a2a_outbox_relay_deployment.py` — which run inside `lint / type / unit`,
which IS required.

`tests/unit/platform/test_networkpolicy.py` already has precedent for silently disarming exactly
this kind of gate: `if not shutil.which("helm"): pytest.skip("helm not available")`. If a future
reviewer applies that same precedent to any of the three modules above "to make CI green when helm
isn't installed", the entire fence family stops being merge-blocking without anyone deciding that
on purpose — a `skip` looks identical to "nothing to check" in a CI summary.

This test is a static source-scan, not a behavioral one: it does not simulate helm's absence (doing
so would mean mutating `PATH` process-wide mid-suite, an outsized blast radius for one guard test).
It instead asserts, by inspecting each module's own source text, that no `pytest.skip`/
`pytest.mark.skip(if)` call exists anywhere in it — so if `helm` really is missing from the
runner (verified separately: GitHub's `ubuntu-latest` image ships it, and CI run `33918521448` on
`d56474a` shows the pre-existing helm-shelling tests in `test_networkpolicy.py` ran for real, zero
extra skips), these three modules fail LOUDLY (`FileNotFoundError` from `subprocess.run`), never
silently green.
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]

#: The modules whose durability the module docstring above depends on — every one shells out to
#: `helm template` as part of proving AF-01/DU-02/SC-01.
_GUARDED_MODULES: tuple[Path, ...] = (
    _REPO_ROOT / "tests" / "unit" / "ci" / "test_check_helm_entrypoints.py",
    _REPO_ROOT / "tests" / "unit" / "ci" / "test_check_chart_env_reconciliation.py",
    _REPO_ROOT / "tests" / "unit" / "platform" / "test_a2a_outbox_relay_deployment.py",
)

#: `pytest.skip(...)`, `pytest.mark.skip(...)`, `pytest.mark.skipif(...)`, or the bare `@skip`/
#: `@skipif` forms reachable via a `from pytest import skip` — catches the precedent shape in
#: `test_networkpolicy.py` (`import pytest` + `pytest.skip(...)`) and its common variants.
_SKIP_RE = re.compile(r"\bskip(?:if)?\s*\(")


def test_every_guarded_module_exists() -> None:
    """Non-vacuity: if a module gets renamed/moved, this scan must not silently start checking
    nothing."""
    for path in _GUARDED_MODULES:
        assert path.is_file(), f"guarded module missing: {path}"


def test_none_of_the_guarded_modules_contain_a_skip_call() -> None:
    """The proof: none of the three modules this fence family's durability depends on may contain
    ANY `skip(...)`/`skipif(...)` call — helm's absence must raise, never quietly skip.

    Mutation probe (run by hand, not asserted here — it would defeat its own purpose to encode
    "must contain a skip" as a passing state): add `if not shutil.which("helm"): pytest.skip(...)`
    to any guarded module and this test goes RED, naming the offending file.
    """
    offenders: list[str] = []
    for path in _GUARDED_MODULES:
        text = path.read_text(encoding="utf-8")
        if _SKIP_RE.search(text):
            offenders.append(str(path.relative_to(_REPO_ROOT)))
    assert offenders == [], (
        f"guarded module(s) contain a skip() call, which would silently disarm the "
        f"AF-01/DU-02/SC-01 fence family the day helm is unavailable on the runner: {offenders}"
    )


def test_the_skip_detector_would_catch_the_networkpolicy_precedent() -> None:
    """Negative control: prove `_SKIP_RE` actually matches the exact shape it exists to forbid,
    using the real precedent file this repo already has — otherwise the guard above could be
    passing because the detector never matches anything."""
    precedent = _REPO_ROOT / "tests" / "unit" / "platform" / "test_networkpolicy.py"
    assert precedent.is_file()
    text = precedent.read_text(encoding="utf-8")
    assert _SKIP_RE.search(text), (
        'sanity: test_networkpolicy.py\'s own `pytest.skip("helm not available")` must match '
        "_SKIP_RE, or the detector above proves nothing"
    )
