"""Pin REAL do manifest v1.13 da AMH (tina-dependentes-nome; PUBLISHED em 19/10/2026, AMH #266;
commit 050c1522).

A rota `dependentes/nomes` (nome completo dos dependentes ATIVOS do PROPRIO titular; decisao do dono e DPO de
19/10/2026, DL-0127) e' consumida pela Helena so' com `MAEZO_HELENA_DEPENDENTES_NOME` ligada. Fixa o digest do
OpenAPI e do manifest, a evidencia XRG-2, o piso de run (posterior ao v1.13), o OpenAPI vendorizado e a guarda
de imutabilidade.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

from scripts.ci import verify_amh_contract_pin as gate

from maezo.adapters.amh import contract as loader
from maezo.adapters.amh.contract import load_contract_pin

REPO_ROOT = Path(__file__).resolve().parents[3]
LOCK_PATH = REPO_ROOT / gate.DEFAULT_LOCK_PATH
ARTEFATO = "schemas/openapi/maezo/v1/tina-dependentes-nome.openapi.yaml"
DIGEST_DO_CONTRATO = "67824f345dd4599654b1686f3697edaf3615719ca989c28b36804504de152e4c"
DIGEST_DO_MANIFEST = "ac1cb804cb978fd49951d45bd77b06a13f2b9c36d207c1a391129142492c5fc1"
RUN = "39261110429"
VERIFY = {"repo_root": REPO_ROOT, "check_vendored": False}


def _lock() -> dict[str, Any]:
    parsed: dict[str, Any] = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    return parsed


def _codes(violations: list[Any]) -> set[str]:
    return {v.code for v in violations}


def test_lock_real_tem_o_bloco_publicado() -> None:
    bloco = _lock()["manifest_v1_13"]
    assert bloco["provenance"]["status"] == "PUBLISHED"
    assert bloco["provenance"]["evidence_id"] == f"XRG2-AMH-DEV-GHA-{RUN}"
    assert bloco["provenance"]["amh_commit_sha"] == "e8f8b33cfe329d6da037decec6b4659b2e87c0eb"
    assert bloco["provenance"]["amh_manifest_commit_sha"] == "050c1522f304c8d5441ad7554360277bfa9b13f2"
    assert bloco["publication"]["publication_run_id"] == RUN
    assert bloco["compatibility_report"]["result"] == "PASSED"
    assert bloco["compatibility_report"]["dry_run_run_id"] == "39260924419"
    assert bloco["manifest_pin"]["path"] == "schemas/contracts/maezo/v1.13/contract-manifest.yaml"
    assert bloco["manifest_pin"]["sha256"] == DIGEST_DO_MANIFEST
    assert bloco["manifest_pin"]["sha256"] != bloco["manifest_pin"]["prepublication_sha256"]
    assert bloco["artifacts"] == [{"path": ARTEFATO, "sha256": DIGEST_DO_CONTRATO}]


def test_verificador_aceita_o_lock_real() -> None:
    lock = _lock()
    assert gate.check_manifest_v1_13(lock) == []
    assert gate.verify_lock(lock, **VERIFY) == []


def test_run_posterior_ao_v1_13_e_evidencia_distinta() -> None:
    lock = _lock()
    assert int(lock["manifest_v1_13"]["publication"]["publication_run_id"]) > int(
        lock["manifest_v1_12"]["publication"]["publication_run_id"]
    )
    evidencias = [b["provenance"]["evidence_id"] for k, b in lock.items() if k.startswith("manifest_v1_")]
    assert len(set(evidencias)) == len(evidencias)


def test_constantes_iguais_no_loader_e_no_verificador() -> None:
    spec = gate.ADDITIVE_BY_VERSION["v1.13"]
    assert spec.artifact_paths == gate.V1_13_ARTIFACT_PATHS == (ARTEFATO,)
    assert tuple(loader.V1_13_ARTIFACT_PATHS) == spec.artifact_paths
    assert gate.V1_13_MIN_PUBLICATION_RUN_ID == loader.V1_13_MIN_PUBLICATION_RUN_ID == 39041030170
    assert gate.V1_13_LOCK_KEY == loader.V1_13_LOCK_KEY == "manifest_v1_13"
    assert spec.predecessors == ()


def test_loader_expoe_o_digest_do_openapi_e_o_do_manifest() -> None:
    pin = load_contract_pin(LOCK_PATH)
    assert pin.artifact_digests[ARTEFATO] == DIGEST_DO_CONTRATO
    assert pin.manifest_v1_13_digest == DIGEST_DO_MANIFEST


def test_openapi_vendorizado_bate_com_o_pin() -> None:
    copia = REPO_ROOT / "config/integrations/amh/openapi/tina-dependentes-nome.openapi.yaml"  # vai na imagem
    assert b"\r" not in copia.read_bytes()
    assert hashlib.sha256(copia.read_bytes()).hexdigest() == DIGEST_DO_CONTRATO


def test_bloco_invalido_e_recusado() -> None:
    lock = _lock()
    lock["manifest_v1_13"]["publication"]["publication_run_id"] = "39041030170"
    assert "v1-13-run-regression" in _codes(gate.verify_lock(lock, **VERIFY))
    lock = _lock()
    lock["manifest_v1_13"]["artifacts"][0]["path"] = "schemas/openapi/maezo/v1/outro.yaml"
    assert "v1-13-artifact-unknown" in _codes(gate.verify_lock(lock, **VERIFY))


def test_candidate_recusa_trocar_o_digest_e_derrubar_o_bloco() -> None:
    atual = _lock()
    trocado = copy.deepcopy(atual)
    trocado["manifest_v1_13"]["artifacts"][0]["sha256"] = "0" * 64
    assert "candidate-v1-13-mutated" in _codes(gate.verify_candidate(atual, trocado))
    sem = {k: v for k, v in atual.items() if k != "manifest_v1_13"}
    assert "candidate-v1-13-removed" in _codes(gate.verify_candidate(atual, sem))
    assert gate.verify_candidate(sem, atual) == []
