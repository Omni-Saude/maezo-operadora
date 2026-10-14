"""Pin REAL do manifest v1.6 da AMH (billing-status 0.3.0: tipo de contratacao e boleto do pagador).

O lock real tem o bloco `manifest_v1_6` publicado (XRG-2 run 38501293989, AMH #240/#241): estes testes
fixam o digest do OpenAPI `billing-status-contratacao`, a evidencia, o v1.4 como predecessor (o 0.2.0 que ele
sucede) e os bytes vendorizados na imagem.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

from scripts.ci.verify_amh_contract_pin import (
    DEFAULT_LOCK_PATH,
    V1_4_LOCK_KEY,
    V1_6,
    V1_6_ARTIFACT_PATHS,
    V1_6_LOCK_KEY,
    V1_6_MIN_PUBLICATION_RUN_ID,
    check_manifest_v1_6,
    verify_candidate,
    verify_lock,
)

from maezo.adapters.amh.contract import V1_6_ARTIFACT_PATHS as LOADER_V1_6_ARTIFACT_PATHS
from maezo.adapters.amh.contract import V1_6_MIN_PUBLICATION_RUN_ID as LOADER_V1_6_MIN_RUN
from maezo.adapters.amh.contract import AmhContractPinError, load_contract_pin

REPO_ROOT = Path(__file__).resolve().parents[3]
LOCK_PATH = REPO_ROOT / DEFAULT_LOCK_PATH

CONTRATACAO_PATH = "schemas/openapi/maezo/v1/billing-status-contratacao.openapi.yaml"
CONTRATACAO_SHA = "b78936da13f0ee81f946a696911df313dd8d096abcaa2d1288d297f543000c5a"
ATENDIMENTO_SHA = "8ffe1cee82bd2cd72ee4cb40a9a6bf83a8367b49afeb358f4fac5154ef88130b"


def _lock() -> dict[str, Any]:
    parsed: dict[str, Any] = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    return parsed


def _codes(violations: list[Any]) -> set[str]:
    return {v.code for v in violations}


def test_lock_real_tem_o_bloco_v1_6_publicado() -> None:
    bloco = _lock()[V1_6_LOCK_KEY]
    assert bloco["provenance"]["status"] == "PUBLISHED"
    assert bloco["provenance"]["evidence_id"] == "XRG2-AMH-DEV-GHA-38501293989"
    assert bloco["publication"]["publication_run_id"] == "38501293989"
    assert bloco["compatibility_report"]["result"] == "PASSED"
    assert bloco["artifacts"] == [{"path": CONTRATACAO_PATH, "sha256": CONTRATACAO_SHA}]


def test_verificador_aceita_o_lock_real_com_v1_6() -> None:
    lock = _lock()
    assert check_manifest_v1_6(lock) == []
    assert verify_lock(lock, repo_root=REPO_ROOT, check_vendored=False) == []


def test_constantes_v1_6_iguais_no_loader_e_no_verificador() -> None:
    assert tuple(LOADER_V1_6_ARTIFACT_PATHS) == V1_6_ARTIFACT_PATHS == (CONTRATACAO_PATH,)
    assert LOADER_V1_6_MIN_RUN == V1_6_MIN_PUBLICATION_RUN_ID
    # o piso do v1.6 e' o run de publicacao do v1.5 pinado
    assert str(V1_6_MIN_PUBLICATION_RUN_ID) == _lock()["manifest_v1_5"]["publication"]["publication_run_id"]
    assert V1_6.predecessors == (V1_4_LOCK_KEY,)
    assert V1_6.supersedes == (
        ("schemas/openapi/maezo/v1/billing-status-atendimento.openapi.yaml", V1_4_LOCK_KEY),
    )


def test_loader_expoe_o_digest_do_0_3_0_e_o_do_manifest_v1_6() -> None:
    pin = load_contract_pin(LOCK_PATH)
    assert pin.artifact_digests[CONTRATACAO_PATH] == CONTRATACAO_SHA
    assert pin.manifest_v1_6_digest == _lock()[V1_6_LOCK_KEY]["manifest_pin"]["sha256"]
    # o 0.2.0 do v1.4 segue pinado e intacto
    assert (
        pin.artifact_digests["schemas/openapi/maezo/v1/billing-status-atendimento.openapi.yaml"]
        == ATENDIMENTO_SHA
    )


def test_openapi_vendorizado_bate_com_o_pin() -> None:
    copia = REPO_ROOT / "config/integrations/amh/openapi" / Path(CONTRATACAO_PATH).name  # vai na imagem
    assert b"\r" not in copia.read_bytes()
    assert hashlib.sha256(copia.read_bytes()).hexdigest() == CONTRATACAO_SHA


def test_v1_6_sem_o_v1_4_pinado_e_recusado_no_gate_e_no_loader(tmp_path: Path) -> None:
    lock = _lock()
    del lock[V1_4_LOCK_KEY]
    assert "v1-6-predecessor-absent" in _codes(verify_lock(lock, repo_root=REPO_ROOT, check_vendored=False))
    caminho = tmp_path / "lock.json"
    caminho.write_text(json.dumps(lock, indent=2), encoding="utf-8")
    try:
        load_contract_pin(caminho)
    except AmhContractPinError:
        pass
    else:  # pragma: no cover
        raise AssertionError("o loader aceitou o v1.6 sem o v1.4")


def test_bloco_v1_6_invalido_e_recusado() -> None:
    for campo, valor, codigo in (
        (
            "provenance.evidence_id",
            _lock()[V1_4_LOCK_KEY]["provenance"]["evidence_id"],
            "v1-6-evidence-reuse",
        ),
        ("publication.publication_run_id", str(V1_6_MIN_PUBLICATION_RUN_ID), "v1-6-run-regression"),
    ):
        lock = _lock()
        no, _, folha = campo.rpartition(".")
        alvo = lock[V1_6_LOCK_KEY]
        for parte in no.split("."):
            alvo = alvo[parte]
        alvo[folha] = valor
        assert codigo in _codes(verify_lock(lock, repo_root=REPO_ROOT, check_vendored=False)), campo
    lock = _lock()
    lock[V1_6_LOCK_KEY]["artifacts"][0]["path"] = "schemas/openapi/maezo/v1/outro.yaml"
    assert "v1-6-artifact-unknown" in _codes(verify_lock(lock, repo_root=REPO_ROOT, check_vendored=False))


def test_candidate_recusa_trocar_o_digest_sob_o_mesmo_manifest() -> None:
    atual = _lock()
    trocado = copy.deepcopy(atual)
    trocado[V1_6_LOCK_KEY]["artifacts"][0]["sha256"] = "0" * 64
    assert "candidate-v1-6-mutated" in _codes(verify_candidate(atual, trocado))
    assert verify_candidate(atual, copy.deepcopy(atual)) == []
