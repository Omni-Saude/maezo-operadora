"""EP-CORE-10a (E-SF-33; CORE-10 degrau T2) — recibo de confianca reproduzido do omni.

Consumidor (`scripts/docs/omni_trust_receipt.py`): cada caso negativo parte de um recibo valido de fixture e
quebra UMA propriedade. Produtor (`.github/workflows/omni-trust-receipt.yml`): o teste fixa que ele so roda
por `workflow_dispatch`, le o omni so para leitura, usa o runtime pinado e actions pinadas por SHA.
Estado real: nenhum recibo publicado e nenhum arquivo com cabecalho de proveniencia no head.
"""

from __future__ import annotations

import copy
import json
import re
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml
from scripts.docs.omni_provenance import OMNI_COMMIT, arquivos_rastreados
from scripts.docs.omni_trust_receipt import (
    NODE_PIN,
    NPM_PIN,
    PASSOS,
    TRUST_REL,
    VALIDADE_DIAS,
    WORKFLOW_REL,
    TrustRefusal,
    avaliar_recibo,
    exigir_recibo,
    nome_recibo,
    recibos_publicados,
    verificar_consumo,
)

_REPO = Path(__file__).resolve().parents[2]
_HOJE = date(2026, 11, 16)
_OUTRO_SHA = "0" * 39 + "1"


def _recibo(sha: str = OMNI_COMMIT, gerado: date = _HOJE) -> dict[str, Any]:
    return {
        "schema_version": "1.0.0",
        "omni_repository": "Omni-Saude/omni_sales_platform",
        "omni_sha": sha,
        "generated_at": f"{gerado.isoformat()}T12:00:00+00:00",
        "runtime": {"node": NODE_PIN, "npm": NPM_PIN},
        "steps": {p: {"exit": 0} for p in PASSOS},
        "tests_executed": 687,
    }


def _copia_com_cabecalho(raiz: Path, sha: str) -> list[str]:
    rel = "src/maezo/portal/web/src/copiado.ts"
    alvo = raiz / rel
    alvo.parent.mkdir(parents=True)
    # Concatenado para que ESTE arquivo nao contenha um cabecalho real em inicio de linha.
    cab = "// " + "Provenance" + ": Omni-Saude/omni_sales_platform@" + sha + ":src/x.ts\n"
    alvo.write_text(cab + "// " + "License-Act" + ": OMNI-LA-TESTE\nexport const x = 1;\n", encoding="utf-8")
    return [rel]


def test_valid_receipt_is_accepted_control() -> None:
    assert avaliar_recibo(_recibo(), OMNI_COMMIT, _HOJE) is None


def test_copy_without_receipt_refused(tmp_path: Path) -> None:
    raiz = tmp_path / "maezo"
    arquivos = _copia_com_cabecalho(raiz, OMNI_COMMIT)
    trust = raiz / TRUST_REL
    recusas = verificar_consumo(raiz, arquivos, trust, _HOJE)
    assert [(r.motivo, r.alvo) for r in recusas] == [(TrustRefusal.RECEIPT_MISSING, arquivos[0])]
    # com o recibo do mesmo sha publicado, o consumo passa (controle)
    trust.mkdir(parents=True)
    (trust / nome_recibo(OMNI_COMMIT)).write_text(json.dumps(_recibo()), encoding="utf-8")
    assert verificar_consumo(raiz, arquivos, trust, _HOJE) == []


def test_receipt_audit_red_refused() -> None:
    recibo = _recibo()
    recibo["steps"]["audit"] = {"exit": 1}
    recusa = avaliar_recibo(recibo, OMNI_COMMIT, _HOJE)
    assert recusa is not None and recusa.motivo is TrustRefusal.AUDIT_FAILED


def test_receipt_node_mismatch_refused() -> None:
    recibo = _recibo()
    recibo["runtime"]["node"] = "v22.23.4"
    recusa = avaliar_recibo(recibo, OMNI_COMMIT, _HOJE)
    assert recusa is not None and recusa.motivo is TrustRefusal.RUNTIME_NOT_PINNED
    recibo = _recibo()
    recibo["runtime"]["npm"] = "10.9.0"
    recusa = avaliar_recibo(recibo, OMNI_COMMIT, _HOJE)
    assert recusa is not None and recusa.motivo is TrustRefusal.RUNTIME_NOT_PINNED


def test_receipt_sha_mismatch_refused(tmp_path: Path) -> None:
    recusa = avaliar_recibo(_recibo(OMNI_COMMIT), _OUTRO_SHA, _HOJE)
    assert recusa is not None and recusa.motivo is TrustRefusal.SHA_MISMATCH
    # e via arquivo: recibo de b3de4b4 gravado com o nome de outro sha
    trust = tmp_path / "trust"
    trust.mkdir()
    (trust / nome_recibo(_OUTRO_SHA)).write_text(json.dumps(_recibo(OMNI_COMMIT)), encoding="utf-8")
    recusa = exigir_recibo(_OUTRO_SHA, trust, _HOJE, "x")
    assert recusa is not None and recusa.motivo is TrustRefusal.SHA_MISMATCH


def test_receipt_older_than_n_days_refused_provisional_n() -> None:
    assert VALIDADE_DIAS == 7  # PROVISORIO: sugestao do plano (EP-CORE-10a), N nao decidido
    assert avaliar_recibo(_recibo(gerado=_HOJE - timedelta(days=VALIDADE_DIAS)), OMNI_COMMIT, _HOJE) is None
    recusa = avaliar_recibo(_recibo(gerado=_HOJE - timedelta(days=VALIDADE_DIAS + 1)), OMNI_COMMIT, _HOJE)
    assert recusa is not None and recusa.motivo is TrustRefusal.RECEIPT_STALE
    futuro = avaliar_recibo(_recibo(gerado=_HOJE + timedelta(days=1)), OMNI_COMMIT, _HOJE)
    assert futuro is not None and futuro.motivo is TrustRefusal.RECEIPT_STALE


@pytest.mark.parametrize("passo", [p for p in PASSOS if p != "audit"])
def test_receipt_other_step_red_or_missing_refused(passo: str) -> None:
    vermelho = _recibo()
    vermelho["steps"][passo] = {"exit": 2}
    ausente = _recibo()
    del ausente["steps"][passo]
    for recibo in (vermelho, ausente):
        recusa = avaliar_recibo(recibo, OMNI_COMMIT, _HOJE)
        assert recusa is not None and recusa.motivo is TrustRefusal.STEP_FAILED


@pytest.mark.parametrize("campo", ["omni_sha", "generated_at", "runtime", "steps", "tests_executed"])
def test_malformed_receipt_refused(campo: str) -> None:
    recibo = copy.deepcopy(_recibo())
    del recibo[campo]
    recusa = avaliar_recibo(recibo, OMNI_COMMIT, _HOJE)
    assert recusa is not None and recusa.motivo is TrustRefusal.RECEIPT_MALFORMED


def test_real_state_no_receipt_and_no_consumption() -> None:
    assert recibos_publicados(_REPO / TRUST_REL) == []
    assert verificar_consumo(_REPO, arquivos_rastreados(_REPO), _REPO / TRUST_REL, date.today()) == []


def _workflow() -> dict[Any, Any]:
    dados = yaml.safe_load((_REPO / WORKFLOW_REL).read_text(encoding="utf-8"))
    assert isinstance(dados, dict)
    return dados


def test_workflow_runs_only_on_dispatch() -> None:
    wf = _workflow()
    gatilhos = wf.get("on", wf.get(True))  # PyYAML le a chave `on` como True
    assert isinstance(gatilhos, dict) and set(gatilhos) == {"workflow_dispatch"}
    assert gatilhos["workflow_dispatch"]["inputs"]["omni_sha"]["required"] is True
    assert wf["permissions"] == {"contents": "read"}


def test_workflow_reads_omni_read_only_with_pinned_runtime_and_actions() -> None:
    wf = _workflow()
    texto = (_REPO / WORKFLOW_REL).read_text(encoding="utf-8")
    for uso in re.findall(r"uses:\s*(\S+)", texto):
        assert re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", uso), uso
    (job,) = wf["jobs"].values()
    assert job["env"]["NODE_PIN"] == NODE_PIN and job["env"]["NPM_PIN"] == NPM_PIN
    checkouts = [s for s in job["steps"] if str(s.get("uses", "")).startswith("actions/checkout@")]
    assert len(checkouts) == 1
    com = checkouts[0]["with"]
    assert com["repository"] == "Omni-Saude/omni_sales_platform"
    assert com["ref"] == "${{ inputs.omni_sha }}"
    assert com["persist-credentials"] is False
    assert com["token"] == "${{ secrets.OMNI_SALES_READ_TOKEN }}"
    setup = [s for s in job["steps"] if str(s.get("uses", "")).startswith("actions/setup-node@")]
    assert [s["with"]["node-version"] for s in setup] == [NODE_PIN.removeprefix("v")]
    # nenhum passo escreve no repo: sem push, sem gh pr, sem permissao de escrita
    assert "git push" not in texto and "contents: write" not in texto
