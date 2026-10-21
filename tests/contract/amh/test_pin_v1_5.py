"""Pin REAL do manifest v1.5 da AMH (TINA: dependentes do titular).

O lock real tem o bloco `manifest_v1_5` publicado (XRG-2 run 38489298542, AMH #237/#238): estes testes fixam o
digest do OpenAPI `tina-dependentes`, a evidencia, o v1.2 como predecessor e os bytes vendorizados na imagem.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

from scripts.ci.verify_amh_contract_pin import (
    DEFAULT_LOCK_PATH,
    V1_2_LOCK_KEY,
    V1_5,
    V1_5_ARTIFACT_PATHS,
    V1_5_LOCK_KEY,
    V1_5_MIN_PUBLICATION_RUN_ID,
    check_manifest_v1_5,
    verify_candidate,
    verify_lock,
)

from maezo.adapters.amh.contract import V1_5_ARTIFACT_PATHS as LOADER_V1_5_ARTIFACT_PATHS
from maezo.adapters.amh.contract import V1_5_MIN_PUBLICATION_RUN_ID as LOADER_V1_5_MIN_RUN
from maezo.adapters.amh.contract import AmhContractPinError, load_contract_pin

REPO_ROOT = Path(__file__).resolve().parents[3]
LOCK_PATH = REPO_ROOT / DEFAULT_LOCK_PATH

DEPENDENTES_PATH = "schemas/openapi/maezo/v1/tina-dependentes.openapi.yaml"
DEPENDENTES_SHA = "0056a326dfc2eba27bfce5c0e6e81edb43e43b7297c8b1dfc2a8ad7f9e4d6dd0"
TINA_SHA = "320ab30360bbbe382220ba0c2cc8ee9d3dc6f8270a4e067c55b2c14a8c778a59"


def _lock() -> dict[str, Any]:
    parsed: dict[str, Any] = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    return parsed


def _codes(violations: list[Any]) -> set[str]:
    return {v.code for v in violations}


def test_lock_real_tem_o_bloco_v1_5_publicado() -> None:
    bloco = _lock()[V1_5_LOCK_KEY]
    assert bloco["provenance"]["status"] == "PUBLISHED"
    assert bloco["provenance"]["evidence_id"] == "XRG2-AMH-DEV-GHA-38489298542"
    assert bloco["publication"]["publication_run_id"] == "38489298542"
    assert bloco["compatibility_report"]["result"] == "PASSED"
    assert bloco["artifacts"] == [{"path": DEPENDENTES_PATH, "sha256": DEPENDENTES_SHA}]


def test_verificador_aceita_o_lock_real_com_v1_5() -> None:
    lock = _lock()
    assert check_manifest_v1_5(lock) == []
    assert verify_lock(lock, repo_root=REPO_ROOT, check_vendored=False) == []


def test_constantes_v1_5_iguais_no_loader_e_no_verificador() -> None:
    assert tuple(LOADER_V1_5_ARTIFACT_PATHS) == V1_5_ARTIFACT_PATHS == (DEPENDENTES_PATH,)
    assert LOADER_V1_5_MIN_RUN == V1_5_MIN_PUBLICATION_RUN_ID
    # o piso do v1.5 e' o run de publicacao do v1.2 pinado
    assert str(V1_5_MIN_PUBLICATION_RUN_ID) == _lock()[V1_2_LOCK_KEY]["publication"]["publication_run_id"]
    assert V1_5.predecessors == (V1_2_LOCK_KEY,)


def test_loader_expoe_o_digest_de_dependentes_e_o_do_manifest_v1_5() -> None:
    pin = load_contract_pin(LOCK_PATH)
    assert pin.artifact_digests[DEPENDENTES_PATH] == DEPENDENTES_SHA
    assert pin.manifest_v1_5_digest == _lock()[V1_5_LOCK_KEY]["manifest_pin"]["sha256"]
    # o tina.openapi.yaml do v1.2 segue pinado e intacto
    assert pin.artifact_digests["schemas/openapi/maezo/v1/tina.openapi.yaml"] == TINA_SHA


def test_openapi_dependentes_vendorizado_bate_com_o_pin() -> None:
    copia = REPO_ROOT / "config/integrations/amh/openapi" / Path(DEPENDENTES_PATH).name  # vai na imagem
    assert b"\r" not in copia.read_bytes()
    assert hashlib.sha256(copia.read_bytes()).hexdigest() == DEPENDENTES_SHA


def test_v1_5_sem_o_v1_2_pinado_e_recusado_no_gate_e_no_loader(tmp_path: Path) -> None:
    lock = _lock()
    del lock[V1_2_LOCK_KEY]
    assert "v1-5-predecessor-absent" in _codes(verify_lock(lock, repo_root=REPO_ROOT, check_vendored=False))
    caminho = tmp_path / "lock.json"
    caminho.write_text(json.dumps(lock, indent=2), encoding="utf-8")
    try:
        load_contract_pin(caminho)
    except AmhContractPinError:
        pass
    else:  # pragma: no cover
        raise AssertionError("o loader aceitou o v1.5 sem o v1.2")


def test_bloco_v1_5_invalido_e_recusado() -> None:
    for campo, valor, codigo in (
        (
            "provenance.evidence_id",
            _lock()[V1_2_LOCK_KEY]["provenance"]["evidence_id"],
            "v1-5-evidence-reuse",
        ),
        ("publication.publication_run_id", str(V1_5_MIN_PUBLICATION_RUN_ID), "v1-5-run-regression"),
    ):
        lock = _lock()
        no, _, folha = campo.rpartition(".")
        alvo = lock[V1_5_LOCK_KEY]
        for parte in no.split("."):
            alvo = alvo[parte]
        alvo[folha] = valor
        assert codigo in _codes(verify_lock(lock, repo_root=REPO_ROOT, check_vendored=False)), campo
    lock = _lock()
    lock[V1_5_LOCK_KEY]["artifacts"][0]["path"] = "schemas/openapi/maezo/v1/outro.yaml"
    assert "v1-5-artifact-unknown" in _codes(verify_lock(lock, repo_root=REPO_ROOT, check_vendored=False))


def test_candidate_recusa_trocar_o_digest_sob_o_mesmo_manifest() -> None:
    atual = _lock()
    trocado = copy.deepcopy(atual)
    trocado[V1_5_LOCK_KEY]["artifacts"][0]["sha256"] = "0" * 64
    assert "candidate-v1-5-mutated" in _codes(verify_candidate(atual, trocado))
    assert verify_candidate(atual, copy.deepcopy(atual)) == []
