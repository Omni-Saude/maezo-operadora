"""Pin REAL do manifest v1.4 da AMH (billing-status 0.2.0: finalidade `atendimento_whatsapp`).

O lock real tem o bloco `manifest_v1_4` publicado (XRG-2 run 37929182045, AMH #225): estes testes fixam o
digest do OpenAPI novo, a evidencia, o v1.1 como predecessor (o billing-status 0.1.0 sucedido continua
pinado e intocado) e que o verificador e o carregador aceitam o lock como esta. A verificacao dos BYTES do
manifest (com `supersedes`) usa um manifest SINTETICO coerente com o lock real, montado aqui.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from scripts.ci.gerar_pin_v1_1 import montar_bloco
from scripts.ci.verify_amh_contract_pin import (
    ADDITIVE_BY_VERSION,
    DEFAULT_LOCK_PATH,
    V1_1_LOCK_KEY,
    V1_4,
    V1_4_ARTIFACT_PATHS,
    V1_4_LOCK_KEY,
    check_manifest_v1_4,
    verify_lock,
    verify_manifest_v1_4,
)

from maezo.adapters.amh.contract import V1_4_ARTIFACT_PATHS as LOADER_V1_4_ARTIFACT_PATHS
from maezo.adapters.amh.contract import load_contract_pin

REPO_ROOT = Path(__file__).resolve().parents[3]
LOCK_PATH = REPO_ROOT / DEFAULT_LOCK_PATH

ATENDIMENTO = "schemas/openapi/maezo/v1/billing-status-atendimento.openapi.yaml"
ATENDIMENTO_SHA = "8ffe1cee82bd2cd72ee4cb40a9a6bf83a8367b49afeb358f4fac5154ef88130b"
BILLING_0_1_0 = "schemas/openapi/maezo/v1/billing-status.openapi.yaml"
BILLING_0_1_0_SHA = "c8064be55f722fe2db1c62de512fe97877911e35e8a9e8660e99fda74adb48fe"


def _lock() -> dict[str, Any]:
    parsed: dict[str, Any] = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    return parsed


def test_lock_real_tem_o_bloco_v1_4_publicado() -> None:
    bloco = _lock()[V1_4_LOCK_KEY]
    assert bloco["provenance"]["status"] == "PUBLISHED"
    assert bloco["provenance"]["evidence_id"] == "XRG2-AMH-DEV-GHA-37929182045"
    assert bloco["provenance"]["amh_manifest_commit_sha"] == "f94292fcf453a1480d045d8aec7a5b50b36c9617"
    assert bloco["publication"]["publication_run_id"] == "37929182045"
    assert bloco["compatibility_report"]["result"] == "PASSED"
    assert {a["path"]: a["sha256"] for a in bloco["artifacts"]} == {ATENDIMENTO: ATENDIMENTO_SHA}


def test_o_v1_1_continua_pinado_e_intocado() -> None:
    """O 0.2.0 SUCEDE o 0.1.0 sem substitui-lo: o bloco v1.1 segue com o digest de sempre."""
    v1_1 = {a["path"]: a["sha256"] for a in _lock()[V1_1_LOCK_KEY]["artifacts"]}
    assert v1_1[BILLING_0_1_0] == BILLING_0_1_0_SHA
    pin = load_contract_pin(LOCK_PATH)
    assert pin.artifact_digests[BILLING_0_1_0] == BILLING_0_1_0_SHA
    assert pin.artifact_digests[ATENDIMENTO] == ATENDIMENTO_SHA


def test_verificador_aceita_o_lock_real_com_v1_4() -> None:
    lock = _lock()
    assert check_manifest_v1_4(lock) == []
    assert verify_lock(lock, repo_root=REPO_ROOT, check_vendored=False) == []


def test_v1_4_exige_o_v1_1_como_predecessor() -> None:
    lock = copy.deepcopy(_lock())
    lock.pop(V1_1_LOCK_KEY)
    codigos = {v.code for v in check_manifest_v1_4(lock)}
    assert "v1-4-predecessor-absent" in codigos


def test_caminhos_v1_4_iguais_no_loader_e_no_verificador_e_no_gerador() -> None:
    assert tuple(LOADER_V1_4_ARTIFACT_PATHS) == V1_4_ARTIFACT_PATHS == (ATENDIMENTO,)
    assert ADDITIVE_BY_VERSION["v1.4"] is V1_4


def test_loader_expoe_o_digest_do_manifest_v1_4() -> None:
    pin = load_contract_pin(LOCK_PATH)
    assert pin.manifest_v1_4_digest == _lock()[V1_4_LOCK_KEY]["manifest_pin"]["sha256"]
    assert pin.manifest_v1_4_digest == "0e8cb441b8cc8e2fe82a124cc20cd139a192c634254904287f9031144ebf7701"


def test_openapi_vendorizado_bate_com_o_pin() -> None:
    copia = REPO_ROOT / "config/integrations/amh/openapi/billing-status-atendimento.openapi.yaml"
    raw = copia.read_bytes()
    assert b"\r\n" not in raw  # LF, os bytes publicados
    assert hashlib.sha256(raw).hexdigest() == ATENDIMENTO_SHA


# --- Bytes do manifest (verify --manifest) com `supersedes` ----------------------------------------


def _manifest(bloco: dict[str, Any], lock: dict[str, Any], *, sucedido_sha: str = BILLING_0_1_0_SHA) -> str:
    """Manifest SINTETICO na forma do publicado, coerente com o bloco v1.4 e com o v1 do lock."""
    return f"""manifest_version: 1.4.0
status: PUBLISHED
contract_name: {lock["provenance"]["contract_name"]}
amh_commit_sha: {bloco["provenance"]["amh_commit_sha"]}
base_manifest:
  base_pinned_sha256: {lock["manifest_pin"]["sha256"]}
supersedes:
  superseded_artifact: {BILLING_0_1_0}
  superseded_artifact_sha256: {sucedido_sha}
compatibility_mode: {lock["provenance"]["compatibility_mode"]}
artifacts:
  - path: {ATENDIMENTO}
    kind: openapi
    sha256: {ATENDIMENTO_SHA}
  - path: schemas/contracts/maezo/v1.4/contract-manifest.yaml
    kind: contract-manifest
    sha256: SELF-AT-PUBLICATION
compatibility_report:
  result: PASSED
  provider_contract_tests: "13 passed"
  dry_run_id: "github-actions-run-37929182045"
  evidence_artifact_id: "11615666348"
  prepublication_manifest_sha256: {bloco["manifest_pin"]["prepublication_sha256"]}
glue_registration:
  environment: dev
  publication_run_id: "{bloco["publication"]["publication_run_id"]}"
  published_at_utc: "{bloco["publication"]["published_at_utc"]}"
evidence_id: {bloco["provenance"]["evidence_id"]}
"""


def _lock_para(raw: bytes) -> dict[str, Any]:
    lock = copy.deepcopy(_lock())
    bloco = lock[V1_4_LOCK_KEY]
    bloco["manifest_pin"]["sha256"] = hashlib.sha256(raw).hexdigest()
    bloco["manifest_pin"]["byte_size"] = len(raw)
    return lock


def test_bytes_coerentes_com_supersedes_passam(tmp_path: Path) -> None:
    lock = _lock()
    raw = _manifest(lock[V1_4_LOCK_KEY], lock).encode()
    (tmp_path / "m.yaml").write_bytes(raw)
    assert verify_manifest_v1_4(_lock_para(raw), tmp_path / "m.yaml") == []


def test_supersedes_com_digest_que_nao_e_o_do_v1_1_reprova(tmp_path: Path) -> None:
    lock = _lock()
    raw = _manifest(lock[V1_4_LOCK_KEY], lock, sucedido_sha="0" * 64).encode()
    (tmp_path / "m.yaml").write_bytes(raw)
    codigos = {v.code for v in verify_manifest_v1_4(_lock_para(raw), tmp_path / "m.yaml")}
    assert "manifest-supersedes-mismatch" in codigos


def test_manifest_sem_supersedes_reprova(tmp_path: Path) -> None:
    lock = _lock()
    texto = _manifest(lock[V1_4_LOCK_KEY], lock)
    raw = "\n".join(linha for linha in texto.splitlines() if "superseded" not in linha).encode()
    (tmp_path / "m.yaml").write_bytes(raw)
    codigos = {v.code for v in verify_manifest_v1_4(_lock_para(raw), tmp_path / "m.yaml")}
    assert "manifest-key-missing" in codigos


def test_gerador_monta_o_bloco_v1_4_igual_ao_do_lock() -> None:
    """O bloco do lock e' o que o gerador produz a partir dos bytes (sem o carimbo de verificacao)."""
    lock = _lock()
    bloco = lock[V1_4_LOCK_KEY]
    raw = _manifest(bloco, lock).encode()
    gerado = montar_bloco(
        raw,
        manifest_commit_sha=bloco["provenance"]["amh_manifest_commit_sha"],
        verified_by="x",
        verified_at_utc="2026-10-09T00:00:00Z",
        spec=V1_4,
    )
    assert gerado["artifacts"] == bloco["artifacts"]
    assert gerado["provenance"] == bloco["provenance"]
    assert gerado["publication"] == bloco["publication"]


@pytest.mark.parametrize("campo", ["manifest_v1_4"])
def test_gerador_nao_sobrescreve_o_bloco_existente(campo: str, tmp_path: Path) -> None:
    from scripts.ci.gerar_pin_v1_1 import GerarPinError, gravar_no_lock

    destino = tmp_path / "contracts.lock.json"
    destino.write_bytes(LOCK_PATH.read_bytes())
    with pytest.raises(GerarPinError, match=campo):
        gravar_no_lock(destino, _lock()[campo], spec=V1_4)
