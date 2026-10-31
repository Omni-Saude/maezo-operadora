"""Cerca do ADR-0073 (pacote W0-F, 2026-10-31): desfecho de start (F-148) e errata da RN 259.

Parte A (desenho, sem runtime): a tabela A.3 do ADR tem de nomear todo membro de
`StartOutcome` que o chokepoint produz. Se um valor novo entrar no enum sem entrar na tabela,
a trilha de auditoria desenhada deixa de cobrir o que o código devolve, e este teste fica vermelho.

Parte B (errata): a RN 259/2011 foi revogada pela RN 566/2022, art. 16, I. A política L0 e os
contratos SP-OP não podem voltar a citá-la sem a menção à revogação, e a errata não pode
relaxar a regra: `authorization_denial` segue L0 + hard.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from maezo.tools.mcp_cibseven.transport import StartOutcome

_REPO = Path(__file__).parents[3]
_ADR = _REPO / "docs" / "adr" / "0073-desfecho-de-start-e-errata-rn259.md"
_L0_CORE = _REPO / "spec" / "policies" / "autonomy" / "L0-core.yaml"
_CONTRACTS = _REPO / "docs" / "processes" / "contracts"

#: "RN 259" sem dígito colado depois (não casa "RN 2590"). A forma "RN259" também conta.
_RN259 = re.compile(r"RN ?259(?!\d)")


def _adr_text() -> str:
    assert _ADR.is_file(), f"ADR ausente: {_ADR}"
    return _ADR.read_text(encoding="utf-8")


def _section(text: str, start: str, end: str) -> str:
    i = text.index(start)
    return text[i : text.index(end, i)]


def _stale_line_numbers(text: str) -> list[int]:
    """Linhas que citam a RN 259 sem a revogação (sem 'revogad') e sem a sucessora (sem '566')."""
    return [
        n
        for n, line in enumerate(text.splitlines(), start=1)
        if _RN259.search(line) and "566" not in line and "revogad" not in line
    ]


def _stale_rn259_lines(path: Path) -> list[str]:
    rel = path.relative_to(_REPO)
    return [f"{rel}:{n}" for n in _stale_line_numbers(path.read_text(encoding="utf-8"))]


def test_adr_tabela_a3_cobre_todo_start_outcome_produzido_pelo_chokepoint() -> None:
    tabela = _section(_adr_text(), "**A.3", "**A.4")
    produzidos = [m.value for m in StartOutcome if m is not StartOutcome.UNREPORTED]
    assert produzidos, "StartOutcome sem membros produzidos: a cerca ficaria vazia"
    faltando = [v for v in produzidos if f"`StartOutcome.{v}`" not in tabela]
    assert not faltando, f"StartOutcome sem linha na tabela A.3 do ADR-0073: {faltando}"


def test_adr_declara_os_desfechos_de_falha_e_o_par_com_o_adr_0045() -> None:
    tabela = _section(_adr_text(), "**A.3", "**A.4")
    for literal in ("`FAILED_BEFORE_EFFECT`", "`EFFECT_UNKNOWN`", "`NOT_STARTED`"):
        assert f"\n| {literal} |" in tabela, f"tabela A.3 sem a linha de {literal}"
    assert "`erro_inicio_processo`" in tabela, "tabela A.3 sem `erro_inicio_processo`"


def test_adr_errata_traz_as_ancoras_aprovadas_pelo_dono() -> None:
    errata = _section(_adr_text(), "## Parte B", "## Supersedes")
    for ancora in (
        "RN 566/2022, art. 16, I",
        "Lei 12.842/2013, art. 5º, II",
        "RN 424/2017",
        "RN 623/2024, art. 14",
        "art. 16 — reanálise pela Ouvidoria",
        "LGPD (Lei 13.709/2018), art. 20",
        "ADR-0005 (política interna",
    ):
        assert ancora in errata, f"errata do ADR-0073 sem a âncora {ancora!r}"


def test_l0_core_nao_cita_rn259_sem_a_revogacao() -> None:
    assert not _stale_rn259_lines(_L0_CORE)
    linha = next(x for x in _L0_CORE.read_text(encoding="utf-8").splitlines() if "authorization_denial:" in x)
    assert "ADR-0005" in linha and "ADR-0073" in linha, linha


def test_contratos_nao_citam_rn259_sem_a_revogacao() -> None:
    contratos = sorted(_CONTRACTS.glob("SP-OP-*.md"))
    assert contratos, "nenhum contrato SP-OP encontrado: a cerca ficaria vazia"
    stale = [hit for path in contratos for hit in _stale_rn259_lines(path)]
    assert not stale, f"RN 259 citada sem a revogação/RN 566 (errata ADR-0073 §B): {stale}"


def test_detector_pega_citacao_nua_da_rn259() -> None:
    """Negativo da própria cerca: uma citação nua é detectada; a forma com revogação, não."""
    amostra = "a) prazo (RN 259)\nb) RN 259 revogada\nc) RN 566 (ex-RN 259)\nd) RN 2590\ne) RN259\n"
    assert _stale_line_numbers(amostra) == [1, 5]


def test_errata_nao_relaxa_a_negativa_l0_hard() -> None:
    acoes = yaml.safe_load(_L0_CORE.read_text(encoding="utf-8"))["actions"]
    assert acoes["authorization_denial"] == {"level": "L0", "hard": True}
    assert acoes["nip_manter_negativa"] == {"level": "L0", "hard": True}
    assert acoes["clinical_decision"] == {"level": "L0", "hard": True}
