"""E-SF-18 / G-35: no DMN table may output commission, acceptance or enrollment.

Authority separation (classificar-rotear-nunca-decidir): commission is an owner
act (A8 stays refused), acceptance is the buyer's and enrollment is the source's.
A decision table that emitted any of them would mint that authority outside its
owner. The sweep is read-only over `spec/processes/dmn/*.dmn` and inspects the
declared outputs (name and label) and their output values, never prose in descriptions.
"""

from __future__ import annotations

import re
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

DMN_DIR = Path(__file__).resolve().parents[3] / "spec" / "processes" / "dmn"

# commission / remuneration / acceptance / enrollment, in the repo's pt-BR and en vocabularies.
_FORBIDDEN_OUTPUT = re.compile(
    r"comiss|commission|remunera|bonif|incentiv|aceite|aceito|aceit[ao]cao|accept|matricul|enroll"
    r"|pagament|pagar|payout|repasse|corretor|broker|intermedi",
    re.IGNORECASE,
)

# Output VALUES are swept with the vocabulary minus the provider-payment verbs (`"PAGAR"` in
# glosa_triage.dmn and `valor_pagamento_cents` in pagto_alcada.dmn are SP-OP-PAGTO-001, not commission).
_FORBIDDEN_VALUE = re.compile(
    r"comiss|commission|remunera|bonif|aceit|accept|matricul|enroll|repasse|payout|corretor|broker|intermedi",
    re.IGNORECASE,
)


def declared_outputs(root: ET.Element) -> list[str]:
    names: list[str] = []
    for element in root.iter():
        tag = element.tag.rsplit("}", 1)[-1]
        if tag == "output":
            names.extend(value for value in (element.get("name"), element.get("label")) if value)
        elif tag in {"outputEntry", "outputValues"}:
            names.extend(
                t.text
                for t in element.iter()
                if t.tag.rsplit("}", 1)[-1] == "text" and t.text and _FORBIDDEN_VALUE.search(t.text)
            )
    return names


def forbidden_outputs(xml: str) -> list[str]:
    return [name for name in declared_outputs(ET.fromstring(xml)) if _FORBIDDEN_OUTPUT.search(name)]


def test_the_sweep_covers_the_real_dmn_set() -> None:
    files = sorted(DMN_DIR.glob("*.dmn"))
    assert len(files) >= 50
    assert sum(len(declared_outputs(ET.parse(path).getroot())) for path in files) > 100


@pytest.mark.parametrize("path", sorted(DMN_DIR.glob("*.dmn")), ids=lambda p: p.name)
def test_no_dmn_declares_a_commission_acceptance_or_enrollment_output(path: Path) -> None:
    assert forbidden_outputs(path.read_text(encoding="utf-8")) == []


@pytest.mark.parametrize(
    "name", ["comissao_corretor", "commission_amount", "aceite_proposta", "matricula_ok"]
)
def test_the_sweep_flags_an_injected_forbidden_output(name: str) -> None:
    # Negative with teeth: a table that grew such an output is caught by the same predicate.
    xml = (
        '<definitions xmlns="https://www.omg.org/spec/DMN/20191111/MODEL/"><decision id="d">'
        f'<decisionTable><output id="o" label="{name}" name="{name}" typeRef="string"/>'
        "</decisionTable></decision></definitions>"
    )
    assert forbidden_outputs(xml) == [name, name]
