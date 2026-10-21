"""Pin REAL do manifest v1.14 da AMH (tina-procedimentos; PUBLISHED em 19/10/2026, AMH #271;
commit 1e7ab258).

A rota `procedimentos` (CATALOGO dos procedimentos que a cobertura resolve por codigo; decisao do dono de
19/10/2026, DL-0128) alimenta a cobertura assistida da Helena (allowlist da saude gerativa). Fixa o digest do
OpenAPI e do manifest, a evidencia XRG-2, o piso de run (posterior ao v1.14), o OpenAPI vendorizado e a guarda
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
ARTEFATO = "schemas/openapi/maezo/v1/tina-procedimentos.openapi.yaml"
DIGEST_DO_CONTRATO = "bcfefaba302ef668fd24b058cf149feb5dbedb2e03366cbe0df39caee5491013"
DIGEST_DO_MANIFEST = "75c36aadd4a52c08c06974a5c4b0da9f2870ac81d748a6c72677609413e5a126"
RUN = "39334179928"
VERIFY = {"repo_root": REPO_ROOT, "check_vendored": False}


def _lock() -> dict[str, Any]:
    parsed: dict[str, Any] = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    return parsed


def _codes(violations: list[Any]) -> set[str]:
    return {v.code for v in violations}


def test_lock_real_tem_o_bloco_publicado() -> None:
    bloco = _lock()["manifest_v1_14"]
    assert bloco["provenance"]["status"] == "PUBLISHED"
    assert bloco["provenance"]["evidence_id"] == f"XRG2-AMH-DEV-GHA-{RUN}"
    assert bloco["provenance"]["amh_commit_sha"] == "e7abdf33f347177c5aae43a879b8ba53641d2d04"
    assert bloco["provenance"]["amh_manifest_commit_sha"] == "1e7ab258f121066a322c83c0762ac9cddebc848c"
    assert bloco["publication"]["publication_run_id"] == RUN
    assert bloco["compatibility_report"]["result"] == "PASSED"
    assert bloco["compatibility_report"]["dry_run_run_id"] == "39334081681"
    assert bloco["manifest_pin"]["path"] == "schemas/contracts/maezo/v1.14/contract-manifest.yaml"
    assert bloco["manifest_pin"]["sha256"] == DIGEST_DO_MANIFEST
    assert bloco["manifest_pin"]["sha256"] != bloco["manifest_pin"]["prepublication_sha256"]
    assert bloco["artifacts"] == [{"path": ARTEFATO, "sha256": DIGEST_DO_CONTRATO}]


def test_verificador_aceita_o_lock_real() -> None:
    lock = _lock()
    assert gate.check_manifest_v1_14(lock) == []
    assert gate.verify_lock(lock, **VERIFY) == []


def test_run_posterior_ao_v1_13_e_evidencia_distinta() -> None:
    lock = _lock()
    assert int(lock["manifest_v1_14"]["publication"]["publication_run_id"]) > int(
        lock["manifest_v1_13"]["publication"]["publication_run_id"]
    )
    evidencias = [b["provenance"]["evidence_id"] for k, b in lock.items() if k.startswith("manifest_v1_")]
    assert len(set(evidencias)) == len(evidencias)


def test_constantes_iguais_no_loader_e_no_verificador() -> None:
    spec = gate.ADDITIVE_BY_VERSION["v1.14"]
    assert spec.artifact_paths == gate.V1_14_ARTIFACT_PATHS == (ARTEFATO,)
    assert tuple(loader.V1_14_ARTIFACT_PATHS) == spec.artifact_paths
    assert gate.V1_14_MIN_PUBLICATION_RUN_ID == loader.V1_14_MIN_PUBLICATION_RUN_ID == 39261110429
    assert gate.V1_14_LOCK_KEY == loader.V1_14_LOCK_KEY == "manifest_v1_14"
    assert spec.predecessors == ()


def test_loader_expoe_o_digest_do_openapi_e_o_do_manifest() -> None:
    pin = load_contract_pin(LOCK_PATH)
    assert pin.artifact_digests[ARTEFATO] == DIGEST_DO_CONTRATO
    assert pin.manifest_v1_14_digest == DIGEST_DO_MANIFEST


def test_openapi_vendorizado_bate_com_o_pin() -> None:
    copia = REPO_ROOT / "config/integrations/amh/openapi/tina-procedimentos.openapi.yaml"  # vai na imagem
    assert b"\r" not in copia.read_bytes()
    assert hashlib.sha256(copia.read_bytes()).hexdigest() == DIGEST_DO_CONTRATO


def test_bloco_invalido_e_recusado() -> None:
    lock = _lock()
    lock["manifest_v1_14"]["publication"]["publication_run_id"] = "39261110429"
    assert "v1-14-run-regression" in _codes(gate.verify_lock(lock, **VERIFY))
    lock = _lock()
    lock["manifest_v1_14"]["artifacts"][0]["path"] = "schemas/openapi/maezo/v1/outro.yaml"
    assert "v1-14-artifact-unknown" in _codes(gate.verify_lock(lock, **VERIFY))


def test_candidate_recusa_trocar_o_digest_e_derrubar_o_bloco() -> None:
    atual = _lock()
    trocado = copy.deepcopy(atual)
    trocado["manifest_v1_14"]["artifacts"][0]["sha256"] = "0" * 64
    assert "candidate-v1-14-mutated" in _codes(gate.verify_candidate(atual, trocado))
    sem = {k: v for k, v in atual.items() if k != "manifest_v1_14"}
    assert "candidate-v1-14-removed" in _codes(gate.verify_candidate(atual, sem))
    assert gate.verify_candidate(sem, atual) == []
