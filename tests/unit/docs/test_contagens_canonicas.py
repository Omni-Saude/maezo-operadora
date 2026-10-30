"""R-073 / F-111 / PEND-551 — AGENTS.md e README.md nao podem voltar a afirmar contagens estaleis.

Duas camadas, no molde de `tests/unit/ci/test_check_plans_counts.py`:

1. Nucleo puro: `verificar_agents`/`verificar_readme` e `extrair_registros` contra texto sintetico —
   VERMELHO para alegacao estale/ausente e para relatorio malformado, VERDE para a reconciliada.
2. Arvore real: as contagens sao recomputadas do sistema de arquivos e confrontadas com o AGENTS.md
   e o README.md versionados; o `docs/reports/predeploy-findings.json` versionado tem de ser
   byte-identico ao que o gerador produz a partir de `docs/reports/predeploy-audit-report.md`.

Ao adicionar um ADR, um DL, um contrato SP-OP, um BPMN/DMN ou um item `hard`, este teste falha
de proposito: atualize a frase correspondente em AGENTS.md/README.md no mesmo PR.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
from scripts.docs.contagens_canonicas import Contagens, contar, verificar_agents, verificar_readme
from scripts.docs.gerar_predeploy_findings import (
    SAIDA_REL,
    FonteInvalidaError,
    extrair_registros,
    gerar,
    main,
)

_REPO_ROOT = Path(__file__).resolve().parents[3]

_C = Contagens(
    adrs=3,
    adr_primeiro=1,
    adr_ultimo=3,
    adrs_aceitos=2,
    adrs_propostos=1,
    dls=4,
    dl_primeiro=1,
    dl_ultimo=4,
    contratos=2,
    bpmn=2,
    dmn=5,
    itens_hard=("clinical_decision", "fraud_accusation"),
    predeploy_registros=3,
    predeploy_confirmados=2,
    predeploy_refutados=1,
)

_AGENTS_OK = (
    '| ADRs | 14/24 "Proposed" | 3 numerados (0001–0003): 2 "Accepted", 1 "Proposed"/"Proposto" |\n'
    "7. os 2 itens congelados: `clinical_decision`, `fraud_accusation`.\n"
    "8. Os 3 registros da auditoria pré-deploy (2 confirmados + 1 refutados) — "
    "`docs/reports/predeploy-findings.json`.\n"
    "9. As 4 entradas DL (`DL-0001`–`DL-0004`) são padrões.\n"
    "- Carregar todos os 3 ADRs de uma vez\n"
    "- Carregar todos os 2 contratos SP-OP de uma vez\n"
)

_README_OK = (
    "2 processos BPMN e 5 arquivos DMN\n"
    "| Processos BPMN SP-OP | 2 |\n"
    "| Arquivos de decisão DMN (`.dmn`) | 5 |\n"
    "| ADRs | 3 (0001–0003; 2 Accepted, 1 Proposed/Proposto) |\n"
    "| Decision log | 4 decisões registradas (DL-0001–DL-0004) |\n"
    "| docs/adr | 3 ADRs numerados (0001–0003; verificado) |\n"
    "| contracts | 2 contratos SP-OP |\n"
    "| log | 4 decisões operacionais (DL-0001–DL-0004) |\n"
)


# =================================================================================================
# Nucleo puro — AGENTS.md
# =================================================================================================


class TestVerificarAgents:
    def test_texto_reconciliado_passa(self) -> None:
        assert verificar_agents(_AGENTS_OK, _C) == []

    @pytest.mark.parametrize(
        ("de", "para"),
        [
            ("todos os 3 ADRs", "todos os 24 ADRs"),
            ("todos os 2 contratos SP-OP", "todos os 16 contratos SP-OP"),
            ("As 4 entradas DL (`DL-0001`–`DL-0004`)", "As 34 entradas DL (`DL-0001`–`DL-0034`)"),
            ("os 2 itens congelados", "os 3 itens congelados"),
            ('3 numerados (0001–0003): 2 "Accepted"', '3 numerados (0001–0003): 3 "Accepted"'),
            ("Os 3 registros da auditoria pré-deploy (2", "Os 82 registros da auditoria pré-deploy (2"),
        ],
    )
    def test_contagem_estale_e_vermelha(self, de: str, para: str) -> None:
        assert de in _AGENTS_OK
        erros = verificar_agents(_AGENTS_OK.replace(de, para), _C)
        assert erros, f"alegacao estale {para!r} passou"

    def test_alegacao_removida_e_vermelha(self) -> None:
        sem = _AGENTS_OK.replace("- Carregar todos os 3 ADRs de uma vez\n", "")
        assert any("ausente" in e for e in verificar_agents(sem, _C))

    def test_item_hard_novo_na_arvore_nao_citado_e_vermelho(self) -> None:
        c = replace(_C, itens_hard=(*_C.itens_hard, "contract_termination"))
        erros = verificar_agents(_AGENTS_OK.replace("os 2 itens", "os 3 itens"), c)
        assert any("contract_termination" in e for e in erros)

    @pytest.mark.parametrize(
        "frase", ["8. **82 findings da auditoria são checklist", "9. **34 DL entries são padrões"]
    )
    def test_frase_antiga_e_vermelha(self, frase: str) -> None:
        assert verificar_agents(_AGENTS_OK + frase + "\n", _C)

    def test_cita_json_inexistente_e_vermelho(self) -> None:
        c = replace(_C, predeploy_registros=-1, predeploy_confirmados=-1, predeploy_refutados=-1)
        assert any("nao existe" in e for e in verificar_agents(_AGENTS_OK, c))


# =================================================================================================
# Nucleo puro — README.md
# =================================================================================================


class TestVerificarReadme:
    def test_texto_reconciliado_passa(self) -> None:
        assert verificar_readme(_README_OK, _C) == []

    @pytest.mark.parametrize(
        ("de", "para"),
        [
            ("2 processos BPMN", "16 processos BPMN"),
            ("| Processos BPMN SP-OP | 2 |", "| Processos BPMN SP-OP | 16 |"),
            ("5 arquivos DMN", "63 arquivos DMN"),
            ("| ADRs | 3 (0001–0003;", "| ADRs | 59 (0001–0059;"),
            ("3 ADRs numerados (0001–0003", "59 ADRs numerados (0001–0059"),
            ("2 contratos SP-OP", "16 contratos SP-OP"),
            ("4 decisões operacionais (DL-0001–DL-0004)", "49 decisões operacionais (DL-0001–DL-0049)"),
        ],
    )
    def test_contagem_estale_e_vermelha(self, de: str, para: str) -> None:
        assert de in _README_OK
        assert verificar_readme(_README_OK.replace(de, para), _C)

    @pytest.mark.parametrize("frase", ["| Tabelas de decisão DMN | 63 |", "em 63 tabelas DMN"])
    def test_contagem_de_tabelas_dmn_antiga_e_vermelha(self, frase: str) -> None:
        assert verificar_readme(_README_OK + frase + "\n", _C)

    def test_rotulo_sem_contagem_de_tabelas_dmn_passa(self) -> None:
        assert verificar_readme(_README_OK + 'EN["processos BPMN + tabelas DMN"]\n', _C) == []


# =================================================================================================
# Nucleo puro — gerador do checklist pre-deploy
# =================================================================================================

_RELATORIO_OK = """# R

## 3. Findings table — all 2 confirmed, grouped by dimension

### 3.1 bpmn-dmn (1 confirmed)

| ID | Sev. | Claim | Evidence | Disposition |
|---|---|---|---|---|
| `a-b` | post-deploy | gated on `int\\|float` | `x.py:1` | ledgered |

### 3.2 phi-perimeter (1 confirmed)

| ID | Sev. | Claim | Evidence | Disposition |
|---|---|---|---|---|
| `c-d` | cosmetic | claim | `y.py:2` | ledgered |

---

## 4. Killed-findings table (1) — evidence

intro

| Dim | ID | Killed by | One-line reason |
|---|---|---|---|
| bpmn-dmn | `e-f` | verifier-1 | stale |

---

## 5. Coverage
"""


class TestExtrairRegistros:
    def test_extrai_literal_e_desescapa_pipe(self) -> None:
        d = extrair_registros(_RELATORIO_OK)
        assert d["totais"] == {"registros": 3, "confirmados": 2, "refutados": 1}
        assert [r["id"] for r in d["registros"]] == ["a-b", "c-d", "e-f"]
        assert d["registros"][0]["afirmacao"] == "gated on `int|float`"
        assert d["registros"][0]["dimensao"] == "bpmn-dmn"
        assert d["registros"][2] == {
            "id": "e-f",
            "dimensao": "bpmn-dmn",
            "status": "refutado",
            "refutado_por": "verifier-1",
            "motivo": "stale",
        }

    def test_titulo_da_subsecao_divergente_e_vermelho(self) -> None:
        with pytest.raises(FonteInvalidaError, match="declara 2 confirmados"):
            extrair_registros(
                _RELATORIO_OK.replace("3.1 bpmn-dmn (1 confirmed)", "3.1 bpmn-dmn (2 confirmed)")
            )

    def test_total_da_secao_3_divergente_e_vermelho(self) -> None:
        with pytest.raises(FonteInvalidaError, match="secao 3 declara 82"):
            extrair_registros(_RELATORIO_OK.replace("all 2 confirmed", "all 82 confirmed"))

    def test_total_da_secao_4_divergente_e_vermelho(self) -> None:
        with pytest.raises(FonteInvalidaError, match="secao 4 declara 25"):
            extrair_registros(
                _RELATORIO_OK.replace("Killed-findings table (1)", "Killed-findings table (25)")
            )

    def test_id_duplicado_e_vermelho(self) -> None:
        with pytest.raises(FonteInvalidaError, match="duplicados"):
            extrair_registros(_RELATORIO_OK.replace("`e-f`", "`a-b`"))

    def test_secoes_ausentes_sao_vermelhas(self) -> None:
        with pytest.raises(FonteInvalidaError):
            extrair_registros("# vazio\n")


# =================================================================================================
# Arvore real
# =================================================================================================


class TestArvoreReal:
    def test_agents_md_confere_com_a_arvore(self) -> None:
        c = contar(_REPO_ROOT)
        assert verificar_agents((_REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8"), c) == []

    def test_readme_confere_com_a_arvore(self) -> None:
        c = contar(_REPO_ROOT)
        assert verificar_readme((_REPO_ROOT / "README.md").read_text(encoding="utf-8"), c) == []

    def test_contagens_reais_nao_vacuas(self) -> None:
        c = contar(_REPO_ROOT)
        assert c.adrs > 0 and c.adrs == c.adr_ultimo - c.adr_primeiro + 1
        assert c.dls > 0 and c.dls == c.dl_ultimo - c.dl_primeiro + 1
        assert c.contratos > 0 and c.bpmn > 0 and c.dmn > 0 and c.itens_hard
        assert c.adrs_aceitos + c.adrs_propostos == c.adrs

    def test_checklist_versionado_e_identico_ao_gerado(self) -> None:
        versionado = (_REPO_ROOT / SAIDA_REL).read_text(encoding="utf-8")
        assert versionado == gerar(_REPO_ROOT), (
            "docs/reports/predeploy-findings.json divergiu do relatorio; "
            "rode `python -m scripts.docs.gerar_predeploy_findings`"
        )
        dados = json.loads(versionado)
        t = dados["totais"]
        assert t["registros"] == t["confirmados"] + t["refutados"] == len(dados["registros"])

    def test_cli_check_passa_na_arvore_real(self) -> None:
        assert main(["--check", "--root", str(_REPO_ROOT)]) == 0

    def test_cli_check_falha_com_json_ausente(self, tmp_path: Path) -> None:
        fonte = tmp_path / "docs" / "reports" / "predeploy-audit-report.md"
        fonte.parent.mkdir(parents=True)
        fonte.write_text(_RELATORIO_OK, encoding="utf-8")
        assert main(["--check", "--root", str(tmp_path)]) == 1
