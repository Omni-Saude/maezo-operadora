"""Corpus fence: a DMN may not declare an input column it never reads (GAP-PERSP-DMN-DEAD-INPUTS).

An `<input>` whose `<inputEntry>` is `-` (wildcard) in EVERY rule contributes nothing to matching.
If, in addition, no `<outputEntry>` FEEL expression reads the variable, the column is DEAD: the
table advertises that it considers a variable and then never looks at it. That is not a cosmetic
defect — a reviewer reading the table's signature (or an operator wiring a worker to feed it)
believes a fact is being weighed when it is not. Precedent in this repo: GAP-CANCEL-2
(`cancel_admissibility.dmn`'s `dentro_prazo`, `docs/reports/business-logic-audit-improvement-plan.md:134`)
and the ACHADO-3 note on `glosa_triage.dmn`'s `tipo_item` (`docs/review-queue.md`).

TWO CLASSES, and only one of them is the defect
-----------------------------------------------
`contas_sla.dmn` documents the honest case in its own `<description>`: `data_recebimento_lote` is
wildcarded in the matching columns *on purpose* because `hitPolicy=FIRST` discriminates by
`tipo_lote`/`valor`, while the OUTPUT FEEL expressions read the variable to compute the absolute
deadline. Such a column is declared to document a real dependency, and it IS read. So this fence
derives the distinction instead of hardcoding it: a wildcard column whose variable appears in an
`outputEntry` FEEL expression is `CLASS A` (read), everything else is `CLASS B` (dead).

Quoted string literals are stripped before that search: `ans_calendar.dmn` mentions `competencia`
inside a `motivo` message (`"REVISAO_HUMANA — report_type/competencia desconhecidos..."`), which is
prose, not a variable read. Counting prose as a read would let any dead column be laundered by
naming it in an output string.

WHY BOTH REGISTRIES ARE FROZEN LISTS
------------------------------------
This fence does NOT assert "zero dead inputs" — that is false today (21 of them, listed below) and
fixing them is per-table SME work, not a sweep: several are the *interesting* kind (see the report
rows). What it does assert is a RATCHET in both directions:

  * a dead input that is not on the baseline FAILS — no new one can be added silently;
  * a baseline entry that no longer trips FAILS — a fix must DELETE its row, so the list can never
    rot into a stale alibi and always states the true, current count.

The same shape applies to `CLASS A`: a new "wildcard but read in an output" column must be added
deliberately, so nobody can move a column into that class without saying so.

The baseline is a DISCLOSURE, not a ratification: no entry below has been reviewed for whether the
rules SHOULD read the variable. That is exactly the question `docs/review-queue.md` carries.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

DMN_DIR = Path(__file__).resolve().parents[3] / "spec" / "processes" / "dmn"

#: FEEL string literals are prose (a `motivo` message), never a variable read.
_STRING_LITERAL = re.compile(r'"[^"]*"')

#: CLASS B — wildcard in every rule AND read by no output expression. Frozen disclosure of the
#: corpus as it stands; NOT a judgement that any of these is acceptable. Fixing one means deleting
#: its row here (the ratchet below enforces that).
#:
#: The two columns GAP-PERSP-DMN-DEAD-INPUTS actually removed are deliberately ABSENT:
#: `frequency_zscore_threshold.in_encounter_class` and `unbundling_partial_bundles.in_tuss_codes`.
KNOWN_DEAD_INPUTS: frozenset[tuple[str, str, str]] = frozenset(
    {
        ("ans_calendar.dmn", "in_competencia", "competencia"),
        ("auth_auto_approval.dmn", "in_carater", "carater_atendimento"),
        ("auth_criteria_contratual.dmn", "in_tenant", "tenant_id"),
        ("auth_criteria_contratual.dmn", "in_categoria", "categoria_procedimento"),
        ("cancel_routing.dmn", "in_tipo_plano", "tipo_plano"),
        ("cancel_sla.dmn", "in_tipo_plano", "tipo_plano"),
        ("cred_admissibility.dmn", "in_tipo_prestador", "tipo_prestador"),
        ("cred_route.dmn", "in_tipo_prestador", "tipo_prestador"),
        ("cred_route.dmn", "in_origem_solicitacao", "origem_solicitacao"),
        ("fraude_indicadores.dmn", "in_indicadores_presentes", "indicadores_presentes"),
        ("fraude_routing.dmn", "in_origem_encaminhamento", "origem_encaminhamento"),
        ("fraude_sla.dmn", "in_entidade_tipo", "entidade_tipo"),
        ("fraude_sla.dmn", "in_origem_encaminhamento", "origem_encaminhamento"),
        ("glosa_triage.dmn", "in_tipo_item", "tipo_item"),
        ("inadimplencia_status.dmn", "in_meses_inadimplencia", "meses_inadimplencia"),
        ("lucas_escalation_routing.dmn", "ler_in_ciclos", "ciclos_sem_conciliacao"),
        ("pagto_alcada.dmn", "in_tipo_pagamento", "tipo_pagamento"),
        ("pagto_sla.dmn", "in_tipo_pagamento", "tipo_pagamento"),
        ("recurso_eligibility.dmn", "in_reason_code", "glosa_reason_code"),
        ("recurso_eligibility.dmn", "in_valor", "valor_glosado_brl"),
        ("reembolso_calculo.dmn", "in_tipo", "tipo_reembolso"),
    }
)

#: CLASS A — wildcard in matching, but genuinely read by an `outputEntry` FEEL expression. All
#: three are SLA tables computing an absolute deadline from the regulatory anchor date.
KNOWN_OUTPUT_READ_INPUTS: frozenset[tuple[str, str, str]] = frozenset(
    {
        ("contas_sla.dmn", "in_data_recebimento", "data_recebimento_lote"),
        ("nip_sla.dmn", "in_data_recebimento", "data_recebimento_nip_iso"),
        ("recurso_sla.dmn", "in_data_recebimento", "data_recebimento_recurso_iso"),
    }
)

#: The tables GAP-PERSP-DMN-DEAD-INPUTS cleaned. Named so a regression says which one regressed.
CLEANED_BY_THIS_GAP = ("frequency_zscore_threshold.dmn", "unbundling_partial_bundles.dmn")

Column = tuple[str, str, str]


def _local(tag: str) -> str:
    return tag.rpartition("}")[2]


def _text_of(element: ET.Element) -> str:
    for child in element:
        if _local(child.tag) == "text":
            return (child.text or "").strip()
    return ""


def _scan() -> tuple[set[Column], set[Column]]:
    """Return `(dead, output_read)` over every `spec/processes/dmn/*.dmn` — read from disk now."""
    dead: set[Column] = set()
    output_read: set[Column] = set()
    for path in sorted(DMN_DIR.glob("*.dmn")):
        root = ET.parse(path).getroot()
        for table in (el for el in root.iter() if _local(el.tag) == "decisionTable"):
            inputs = [el for el in table if _local(el.tag) == "input"]
            rules = [el for el in table if _local(el.tag) == "rule"]
            if not rules:
                continue
            feel_outputs = " ".join(
                _STRING_LITERAL.sub(" ", _text_of(entry))
                for rule in rules
                for entry in rule
                if _local(entry.tag) == "outputEntry"
            )
            for index, declared in enumerate(inputs):
                expression = ""
                for child in declared:
                    if _local(child.tag) == "inputExpression":
                        expression = _text_of(child)
                cells = []
                for rule in rules:
                    entries = [el for el in rule if _local(el.tag) == "inputEntry"]
                    cells.append(_text_of(entries[index]) if index < len(entries) else "")
                if not cells or any(cell != "-" for cell in cells):
                    continue
                column: Column = (path.name, declared.get("id") or "", expression)
                if expression and re.search(rf"\b{re.escape(expression)}\b", feel_outputs):
                    output_read.add(column)
                else:
                    dead.add(column)
    return dead, output_read


@pytest.fixture(scope="module")
def scan() -> tuple[set[Column], set[Column]]:
    return _scan()


def test_the_corpus_is_actually_being_scanned() -> None:
    """Guard against a silently empty sweep — an XML/glob regression would make every fence below
    pass vacuously. 62 `.dmn` files ship today; the floor only ever grows."""
    files = sorted(DMN_DIR.glob("*.dmn"))
    assert len(files) >= 62, f"expected at least 62 .dmn files, found {len(files)}"


def test_no_dmn_declares_a_dead_input_outside_the_frozen_baseline(
    scan: tuple[set[Column], set[Column]],
) -> None:
    """THE FENCE. A NEW input that is `-` in every rule and read by no output expression fails.

    To add one deliberately you must add its row to `KNOWN_DEAD_INPUTS` — which is a disclosure a
    reviewer sees in the diff, not a silent default.
    """
    dead, _ = scan
    assert dead - KNOWN_DEAD_INPUTS == set(), (
        "new DEAD DMN input(s) — declared as a column, wildcarded in every rule, and read by no "
        f"output expression: {sorted(dead - KNOWN_DEAD_INPUTS)}"
    )


def test_the_dead_input_baseline_has_no_stale_rows(scan: tuple[set[Column], set[Column]]) -> None:
    """THE RATCHET. A baseline row that no longer trips must be DELETED, not left behind.

    Without this the list would quietly become a claim about a corpus that no longer exists, and
    the count reported to reviewers would drift upward from reality.
    """
    dead, _ = scan
    assert KNOWN_DEAD_INPUTS - dead == set(), (
        "these rows no longer describe a dead input — delete them from KNOWN_DEAD_INPUTS: "
        f"{sorted(KNOWN_DEAD_INPUTS - dead)}"
    )


def test_output_read_wildcard_columns_are_the_declared_three(
    scan: tuple[set[Column], set[Column]],
) -> None:
    """The CLASS A carve-out is itself frozen, in both directions.

    A wildcard column that names its variable in an output expression is exempt from the dead-input
    fence, so the exemption must be declared, never discovered. `contas_sla.dmn`'s description is
    the worked example of why this class is legitimate.
    """
    _, output_read = scan
    assert output_read == KNOWN_OUTPUT_READ_INPUTS


@pytest.mark.parametrize("filename", CLEANED_BY_THIS_GAP)
def test_the_two_tables_this_gap_cleaned_have_no_dead_input(
    filename: str, scan: tuple[set[Column], set[Column]]
) -> None:
    """GAP-PERSP-DMN-DEAD-INPUTS regression, per table.

    `frequency_zscore_threshold` declared `encounter_class` (donor label "Encounter Class") and
    wildcarded it in all 5 rules; `unbundling_partial_bundles` declared `tuss_codes` and wildcarded
    it in all 4 — the latter dead on both ends, since the worker had already stopped sending it in
    T1.5. Both columns were removed, so both tables must scan clean forever.
    """
    dead, _ = scan
    assert [column for column in dead if column[0] == filename] == []
    assert [column for column in KNOWN_DEAD_INPUTS if column[0] == filename] == []


def test_every_first_policy_table_still_ends_in_a_bare_catch_all_row() -> None:
    """Removing a column must never cost a table its fail-closed last row (ADR-0028 §3).

    Stated over the whole corpus rather than the two edited files: the cheapest way to "fix" a dead
    input is to delete rules until none is left wildcarded, and this is what makes that visible.

    Scoped to `hitPolicy="FIRST"` (62 of the 63 decision tables) because a bare `-` row is only
    legal there. `programa_sla.dmn` is the single `UNIQUE` table, and its own description explains
    why its catch-all is the complementary negative set `not("pre_natal","cronicos")` instead: under
    UNIQUE a bare `-` row would collide with every specific row at runtime. It is named here so the
    exception is declared rather than silently skipped.
    """
    offenders: list[str] = []
    unique_tables: list[str] = []
    for path in sorted(DMN_DIR.glob("*.dmn")):
        root = ET.parse(path).getroot()
        for table in (el for el in root.iter() if _local(el.tag) == "decisionTable"):
            rules = [el for el in table if _local(el.tag) == "rule"]
            if not rules:
                continue
            name = f"{path.name}:{table.get('id')}"
            if (table.get("hitPolicy") or "UNIQUE") != "FIRST":
                unique_tables.append(name)
                continue
            last = [el for el in rules[-1] if _local(el.tag) == "inputEntry"]
            if any(_text_of(entry) != "-" for entry in last):
                offenders.append(name)
    assert offenders == [], f"FIRST-policy tables whose LAST row is not a catch-all: {offenders}"
    assert unique_tables == ["programa_sla.dmn:dt_programa_sla"], (
        "a new non-FIRST decision table appeared — confirm its catch-all covers the remaining "
        f"domain before adding it here: {unique_tables}"
    )
