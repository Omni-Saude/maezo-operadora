"""E-SF-17 / GTM-E-10: o ledger de Compras sem agente de cobranca/saude como lead, sem tocar BPMN.

Guardas nomeadas no plano: `test_ledger_compras_lead_nao_cobranca` (`lead = lucas` em C2 ->
`LEDGER_PURPOSE_RELABEL`) e `test_ca1_bpmn_inalterado_por_gtm01` (qualquer edicao do `.bpmn` de
Compras -> `BPMN_OUT_OF_SCOPE`). Mais: GUIA:551 liga "Consultor comercial" ao papel com dono declarado.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest
import yaml

_REPO = Path(__file__).resolve().parents[3]
_LEDGER_MD = _REPO / "docs" / "design" / "v21-capabilities" / "administrative-contracts.md"
_LEDGER_JSON = _REPO / "docs" / "design" / "v21-capabilities" / "contracts.json"
_GUIA = _REPO / "docs" / "processes" / "New_BPMN" / "Adapted_BPMN" / "GUIA-EQUIPE-OPERACAO.md"
_ADAPTED = _REPO / "docs" / "processes" / "New_BPMN" / "Adapted_BPMN"
_CA1 = _ADAPTED / "CA_1_COMPRA (com suporte)_v4_adaptado.bpmn"

_COMPRAS = (
    "Process_CA_COMPRA",
    "Process_CA_COMPRA_C1_EntenderRecomendar",
    "Process_CA_COMPRA_C2_DecidirContratar",
    "Process_CA_COMPRA_C3_HabilitarDireito",
)

#: sha256 do `.bpmn` de Compras em 211c6ae. GTM-01 nao muda BPMN (as lanes ja' existem); uma
#: mudanca legitima vem de outro epico (E-SF-17r/ferramental BPMN), que atualiza este pino.
SHA256_CA1_BPMN = "cd571d6dc8e5bdfec2ff731315792cd8e74437c007e4f3ebec3afbe3252d8f79"


def _agentes_de_finalidade_alheia() -> frozenset[str]:
    """Agentes cuja finalidade e' cobranca/inadimplencia ou saude: nunca leads de venda."""
    proibidos: set[str] = set()
    for path in (_REPO / "spec" / "agents").glob("*/agent.yaml"):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        papel = str(data.get("role", "")).lower()
        if re.search(r"cobran|inadimpl|health navigator|triagem", papel):
            proibidos.add(str(data["id"]))
    return frozenset(proibidos)


def _leads_md() -> dict[str, list[str]]:
    leads: dict[str, list[str]] = {}
    for linha in _LEDGER_MD.read_text(encoding="utf-8").splitlines():
        m = re.match(r"\| `(Process_CA_[A-Za-z0-9_]+)` \| [^|]+ \| [^|]+ \| ([^|]+) \|", linha)
        if m:
            leads[m.group(1)] = [x.strip() for x in m.group(2).split("/")]
    return leads


def _leads_json() -> dict[str, list[str]]:
    leads: dict[str, list[str]] = {}

    def walk(o: object) -> None:
        if isinstance(o, dict):
            if "process_id" in o and "lead_proposed" in o:
                leads[str(o["process_id"])] = [str(x) for x in o["lead_proposed"]]
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(json.loads(_LEDGER_JSON.read_text(encoding="utf-8")))
    return leads


def ledger_violations(leads: dict[str, list[str]], proibidos: frozenset[str]) -> list[str]:
    erros = []
    for processo in _COMPRAS:
        if processo not in leads:
            erros.append(f"LEDGER_ROW_MISSING: {processo}")
            continue
        for lead in leads[processo]:
            if lead in proibidos:
                erros.append(f"LEDGER_PURPOSE_RELABEL: {processo} lead={lead}")
    return erros


def test_universo_de_finalidade_alheia_nao_e_vazio() -> None:
    assert {"lucas", "helena"} <= _agentes_de_finalidade_alheia()


@pytest.mark.parametrize("fonte", ["md", "json"])
def test_ledger_compras_lead_nao_cobranca(fonte: str) -> None:
    leads = _leads_md() if fonte == "md" else _leads_json()
    proibidos = _agentes_de_finalidade_alheia()
    assert ledger_violations(leads, proibidos) == []
    for processo in _COMPRAS:
        assert leads[processo] == ["especialista_comercial"], processo
    # Mutacao: `lead = lucas` em C2 volta -> recusa nomeada.
    mutado = {**leads, "Process_CA_COMPRA_C2_DecidirContratar": ["lucas"]}
    assert ledger_violations(mutado, proibidos) == [
        "LEDGER_PURPOSE_RELABEL: Process_CA_COMPRA_C2_DecidirContratar lead=lucas"
    ]


def test_md_e_json_concordam_nas_linhas_de_compras() -> None:
    md, js = _leads_md(), _leads_json()
    for processo in _COMPRAS:
        assert md[processo] == js[processo], processo


def test_ca1_bpmn_inalterado_por_gtm01() -> None:
    atual = hashlib.sha256(_CA1.read_bytes()).hexdigest()
    assert atual == SHA256_CA1_BPMN, f"BPMN_OUT_OF_SCOPE: {_CA1.name} mudou (GTM-01 nao edita BPMN)"


def test_guia_551_liga_consultor_ao_papel_com_dono_declarado() -> None:
    linhas = _GUIA.read_text(encoding="utf-8").splitlines()
    linha = next(x for x in linhas if x.startswith("| **Consultor comercial** |"))
    assert "Especialista comercial — Faixa Assistida" in linha
    assert "duas pessoas" in linha
    assert "GAP-PERSONA-NAME" in linha and "GAP-HEADCOUNT" in linha
