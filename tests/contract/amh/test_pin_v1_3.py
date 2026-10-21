"""Pin REAL do manifest v1.3 da AMH (subject-verification, subject-document-resolution, consent-record).

O lock real tem o bloco `manifest_v1_3` publicado: estes testes fixam os 3 digests OpenAPI do manifest
publicado pela AMH, a evidencia, e que o verificador e o carregador aceitam o lock como esta.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from scripts.ci.verify_amh_contract_pin import (
    DEFAULT_LOCK_PATH,
    V1_3_ARTIFACT_PATHS,
    V1_3_LOCK_KEY,
    check_manifest_v1_3,
    verify_lock,
)

from maezo.adapters.amh.contract import V1_3_ARTIFACT_PATHS as LOADER_V1_3_ARTIFACT_PATHS
from maezo.adapters.amh.contract import load_contract_pin

REPO_ROOT = Path(__file__).resolve().parents[3]
LOCK_PATH = REPO_ROOT / DEFAULT_LOCK_PATH

EXPECTED_DIGESTS = {
    "schemas/openapi/maezo/v1/subject-verification.openapi.yaml": (
        "d2f90e087fdbce1a31d9ecd1d5c4f82e755c62c370bf719def7cd11eeed05d76"
    ),
    "schemas/openapi/maezo/v1/subject-document-resolution.openapi.yaml": (
        "a87a67a73038bab6def06cb76acd22d2b8fa2566a95940db8efdad094562b0b9"
    ),
    "schemas/openapi/maezo/v1/consent-record.openapi.yaml": (
        "157d403c47cc14a9e3e096a6654567a51508ad7b3bf8ed1c7dc66c7c78319590"
    ),
}


def _lock() -> dict[str, Any]:
    parsed: dict[str, Any] = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    return parsed


def test_lock_real_tem_o_bloco_v1_3_publicado() -> None:
    bloco = _lock()[V1_3_LOCK_KEY]
    assert bloco["provenance"]["status"] == "PUBLISHED"
    assert bloco["provenance"]["evidence_id"] == "XRG2-AMH-DEV-GHA-37843212443"
    assert bloco["compatibility_report"]["result"] == "PASSED"
    assert {a["path"]: a["sha256"] for a in bloco["artifacts"]} == EXPECTED_DIGESTS


def test_verificador_aceita_o_lock_real_com_v1_3() -> None:
    lock = _lock()
    assert check_manifest_v1_3(lock) == []
    assert verify_lock(lock, repo_root=REPO_ROOT, check_vendored=False) == []


def test_caminhos_v1_3_iguais_no_loader_e_no_verificador() -> None:
    assert tuple(LOADER_V1_3_ARTIFACT_PATHS) == V1_3_ARTIFACT_PATHS
    assert set(V1_3_ARTIFACT_PATHS) == set(EXPECTED_DIGESTS)


def test_loader_expoe_os_3_digests_e_o_digest_do_manifest_v1_3() -> None:
    pin = load_contract_pin(LOCK_PATH)
    for path, sha in EXPECTED_DIGESTS.items():
        assert pin.artifact_digests[path] == sha
    assert pin.manifest_v1_3_digest == _lock()[V1_3_LOCK_KEY]["manifest_pin"]["sha256"]


def test_os_3_openapi_vendorizados_batem_com_o_pin() -> None:
    for path, sha in EXPECTED_DIGESTS.items():
        copia = REPO_ROOT / "config/integrations/amh/openapi" / Path(path).name  # vai na imagem
        assert hashlib.sha256(copia.read_bytes()).hexdigest() == sha
