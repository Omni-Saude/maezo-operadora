"""E-SF-20 (EP-CORE-15a) — the web guards render the manifest the BFF actually serves.

`LegitimacyScreenPanel.labels-verbatim.test.tsx` and its siblings read the checklist from
`src/maezo/portal/web/src/test/legitimacyChecklist.fixture.json`. This pin keeps that file
equal to `CHECKLIST` (the `GET …/legitimacy-screens/checklist` body), so a label changed on
the server without the web fixture — or the reverse — fails here.
"""

from __future__ import annotations

import json
from pathlib import Path

from maezo.portal.api.legitimacy_screen import CHECKLIST

_FIXTURE = (
    Path(__file__).resolve().parents[3]
    / "src"
    / "maezo"
    / "portal"
    / "web"
    / "src"
    / "test"
    / "legitimacyChecklist.fixture.json"
)


def test_web_fixture_is_the_served_checklist() -> None:
    assert json.loads(_FIXTURE.read_text(encoding="utf-8")) == json.loads(CHECKLIST.model_dump_json())


def test_the_served_checklist_keeps_art29_par1_as_the_only_structural_criterion() -> None:
    structural = [criterion for criterion in CHECKLIST.criterios if criterion.estrutural]
    assert [criterion.norm_anchor for criterion in structural] == ["RN 557/2022, art. 29, § 1º"]
    assert structural[0].campos == () and structural[0].opcoes == ()
