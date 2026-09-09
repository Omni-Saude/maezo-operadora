"""PERSP-ESCALATION-VOCAB-b — o que esta cerca pina DEPOIS de 09/09/2026.

Ate 09/09 ela pinava "rename BLOQUEADO por R-034". Nesse dia o diretor DECIDIU o rename dos
identificadores tecnicos, com base num fato medido no motor: a lista branca do CIB Seven e'
`[a-zA-Z0-9]+`, entao os nomes com hifen NAO PODIAM existir como grupo e toda tarefa de
escalonamento apontava para fila vazia (HTTP 404 nos tres). Os 4 grupos passaram a camelCase
(`plantaoClinico`, `enfermagemTriagem`, `atendimentoHumano`, `supervisaoAtendimento`) em TODOS os
sitios (DMN, BPMN, worker, agentes, contrato, test-specs) e foram criados no motor.

O que continua em aberto — e a cerca continua pinando — e' o NOME DE NEGOCIO (R-034 /
`ORG-TAXONOMY-TABLE.md`): as 4 linhas seguem `DRAFT/verify`. Esta cerca prova (1) que os 4
identificadores do contrato sao EXATAMENTE os que a DMN emite (sem drift entre doc e spec), (2) que
a marca `DRAFT/verify` + ponteiro continuam em cada linha, e (3) que o contrato DECLARA o rename
como feito e datado, em vez de deixar o leitor deduzir.
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).parents[3]
_CONTRACT = _REPO_ROOT / "docs" / "processes" / "contracts" / "SP-OP-ESCALATION-001.md"
_DMN = _REPO_ROOT / "spec" / "processes" / "dmn" / "escalation_routing.dmn"


def _papeis_table_groups() -> list[str]:
    """Tree-derived: every `Grupo` cell (first column) in the Papeis humanos table, parsed
    straight from the table rows rather than hardcoded."""
    text = _CONTRACT.read_text(encoding="utf-8")
    section_start = text.index("## Papeis humanos")
    section_end = text.find("\n## ", section_start + 1)
    section = text[section_start : section_end if section_end != -1 else None]
    rows = re.findall(r"^\|\s*`([A-Za-z0-9]+)`\s*\|", section, re.MULTILINE)
    assert rows, "no candidateGroup rows found in the Papeis humanos table"
    return rows


#: `supervisaoAtendimento` is reached via the SLA-breach escalation path (`UT_SupervisorAssume`),
#: never as a `grupo_atendimento` DMN output value -- it is deliberately excluded from the
#: DMN-presence check below, not an oversight.
_NOT_A_DMN_OUTPUT = frozenset({"supervisaoAtendimento"})


def test_every_papeis_table_group_still_resolves_in_the_live_dmn() -> None:
    """Os grupos do contrato (que SAO saidas da DMN) sao os mesmos que a DMN emite hoje — contrato
    e spec renomeados JUNTOS, sem drift."""
    dmn_text = _DMN.read_text(encoding="utf-8")
    for group in _papeis_table_groups():
        if group in _NOT_A_DMN_OUTPUT:
            continue
        assert group in dmn_text, f"{group!r} not found in escalation_routing.dmn (renamed?)"


def test_every_papeis_table_role_is_marked_draft_verify_with_r034_pointer() -> None:
    """The load-bearing fence: every role row in the Papeis humanos table must carry the
    `DRAFT/verify` marker with an `ORG-TAXONOMY-TABLE.md` pointer. Revert any one row and this
    goes RED."""
    text = _CONTRACT.read_text(encoding="utf-8")
    section_start = text.index("## Papeis humanos")
    section_end = text.find("\n## ", section_start + 1)
    section = text[section_start : section_end if section_end != -1 else None]
    rows = [line for line in section.splitlines() if re.match(r"^\|\s*`[A-Za-z0-9]+`\s*\|", line)]
    assert len(rows) == 4, f"expected 4 role rows, found {len(rows)}: {rows}"
    missing_marker = [r for r in rows if "DRAFT/verify" not in r]
    missing_pointer = [r for r in rows if "ORG-TAXONOMY-TABLE.md" not in r]
    assert not missing_marker, f"row(s) missing DRAFT/verify: {missing_marker}"
    assert not missing_pointer, f"row(s) missing ORG-TAXONOMY-TABLE.md pointer: {missing_pointer}"


def test_the_four_groups_are_the_engine_safe_identifiers_decided_on_2026_09_09() -> None:
    """Os 4 identificadores sao os camelCase decididos em 09/09 — e NENHUM contem hifen, que o motor
    recusa (`[a-zA-Z0-9]+`). Um hifen voltando aqui recria a fila vazia que este rename fechou."""
    groups = set(_papeis_table_groups())
    expected = {"plantaoClinico", "enfermagemTriagem", "atendimentoHumano", "supervisaoAtendimento"}
    assert groups == expected, f"candidateGroups divergem da decisao de 09/09: {sorted(groups)}"
    assert all(re.fullmatch(r"[a-zA-Z0-9]+", g) for g in groups), (
        "identificador fora da lista branca do motor"
    )


def test_contract_declares_the_rename_done_and_dated_and_business_names_still_draft() -> None:
    """O contrato DIZ que o rename tecnico foi feito, quando e por que — e que o nome de negocio
    continua em R-034. Nao deixa o leitor deduzir nem uma coisa nem a outra."""
    text = _CONTRACT.read_text(encoding="utf-8")
    assert "PERSP-ESCALATION-VOCAB-b" in text
    assert "identificadores renomeados em 09/09/2026" in text
    assert "[a-zA-Z0-9]+" in text
    assert "nomes de negocio seguem DRAFT/verify" in text
    assert "parcial — rename bloqueado por R-034" not in text
