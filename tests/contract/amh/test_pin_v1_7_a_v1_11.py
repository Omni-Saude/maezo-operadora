"""Pin REAL dos manifests v1.7 a v1.11 da AMH (PUBLISHED em 16/10/2026, AMH #253; commit f7fa458a).

Os cinco blocos entram no lock so' como RECONHECIMENTO: nenhuma rota, flag ou executor nasce aqui.
Fixa, por manifest, o digest do OpenAPI, a evidencia XRG-2, o piso de run crescente, o OpenAPI
vendorizado e a guarda de imutabilidade. O v1.10 sucede o billing-status 0.3.0 (v1.6), intocado.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from scripts.ci import verify_amh_contract_pin as gate

from maezo.adapters.amh import contract as loader
from maezo.adapters.amh.contract import AmhContractPinError, load_contract_pin

REPO_ROOT = Path(__file__).resolve().parents[3]
LOCK_PATH = REPO_ROOT / gate.DEFAULT_LOCK_PATH

# versao -> (artefato, sha256 do OpenAPI publicado, run de publicacao, piso de run)
CASOS: dict[int, tuple[str, str, str, int]] = {
    7: (
        "tina-identificacao-cadastro",
        "5a1afe0d123b5af99ed512e029a1c05b2b759c51d1a634c5ad74c78ec5e42f4c",
        "38866054259",
        38501293989,
    ),
    8: (
        "tina-rede-credenciada",
        "2516bb1126b389fdc379c4c6e9731771089b4f6794fb1da61273105f7a510966",
        "38866138790",
        38866054259,
    ),
    9: (
        "tina-cobertura",
        "2965ab181ee84ca291a83e1714f245d0c2edae276c340f8d059f84e295cca6f9",
        "38866212057",
        38866138790,
    ),
    10: (
        "billing-status-detalhamento",
        "8458328ad0faafdd17f130b8f09dfbf2861ec58e75d709a14a9ee4d16dc61a22",
        "38866305412",
        38866212057,
    ),
    11: (
        "billing-segunda-via",
        "59804441c9c0ed46830c996233ade7af1e2fad495b5955a645aacf95aedaf9a1",
        "38866403032",
        38866305412,
    ),
}
VERSOES = sorted(CASOS)
VERIFY = {"repo_root": REPO_ROOT, "check_vendored": False}


def _lock() -> dict[str, Any]:
    parsed: dict[str, Any] = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    return parsed


def _codes(violations: list[Any]) -> set[str]:
    return {v.code for v in violations}


def _path(nome: str) -> str:
    return f"schemas/openapi/maezo/v1/{nome}.openapi.yaml"


@pytest.mark.parametrize("n", VERSOES)
def test_lock_real_tem_o_bloco_publicado(n: int) -> None:
    nome, sha, run, _ = CASOS[n]
    bloco = _lock()[f"manifest_v1_{n}"]
    assert bloco["provenance"]["status"] == "PUBLISHED"
    assert bloco["provenance"]["evidence_id"] == f"XRG2-AMH-DEV-GHA-{run}"
    assert bloco["provenance"]["amh_commit_sha"] == "0458ac31289ba564da037b38bf4a67ec4d8750ef"
    assert bloco["provenance"]["amh_manifest_commit_sha"] == "f7fa458ae08892270923346c2d526d9e32769d1a"
    assert bloco["publication"]["publication_run_id"] == run
    assert bloco["compatibility_report"]["result"] == "PASSED"
    assert bloco["compatibility_report"]["dry_run_run_id"] == run
    assert bloco["manifest_pin"]["path"] == f"schemas/contracts/maezo/v1.{n}/contract-manifest.yaml"
    assert bloco["manifest_pin"]["sha256"] != bloco["manifest_pin"]["prepublication_sha256"]
    assert bloco["artifacts"] == [{"path": _path(nome), "sha256": sha}]


def test_verificador_aceita_o_lock_real_com_v1_7_a_v1_11() -> None:
    lock = _lock()
    for n in VERSOES:
        assert getattr(gate, f"check_manifest_v1_{n}")(lock) == []
    assert gate.verify_lock(lock, **VERIFY) == []


def test_runs_de_publicacao_crescentes_e_evidencias_distintas() -> None:
    lock = _lock()
    sequencia = [int(lock[f"manifest_v1_{n}"]["publication"]["publication_run_id"]) for n in range(6, 12)]
    assert sequencia == sorted(sequencia)
    assert len(set(sequencia)) == len(sequencia)
    evidencias = [b["provenance"]["evidence_id"] for k, b in lock.items() if k.startswith("manifest_v1_")]
    assert len(set(evidencias)) == len(evidencias)


@pytest.mark.parametrize("n", VERSOES)
def test_constantes_iguais_no_loader_e_no_verificador(n: int) -> None:
    nome, _, _, piso = CASOS[n]
    spec = gate.ADDITIVE_BY_VERSION[f"v1.{n}"]
    assert spec.artifact_paths == getattr(gate, f"V1_{n}_ARTIFACT_PATHS") == (_path(nome),)
    assert tuple(getattr(loader, f"V1_{n}_ARTIFACT_PATHS")) == spec.artifact_paths
    assert getattr(gate, f"V1_{n}_MIN_PUBLICATION_RUN_ID") == piso
    assert getattr(loader, f"V1_{n}_MIN_PUBLICATION_RUN_ID") == piso
    assert getattr(gate, f"V1_{n}_LOCK_KEY") == getattr(loader, f"V1_{n}_LOCK_KEY") == f"manifest_v1_{n}"


def test_v1_10_sucede_o_billing_status_0_3_0_e_os_demais_sao_independentes() -> None:
    contratacao = "schemas/openapi/maezo/v1/billing-status-contratacao.openapi.yaml"
    assert gate.V1_10.predecessors == (gate.V1_6_LOCK_KEY,)
    assert gate.V1_10.supersedes == ((contratacao, gate.V1_6_LOCK_KEY),)
    for n in (7, 8, 9, 11):
        assert gate.ADDITIVE_BY_VERSION[f"v1.{n}"].predecessors == ()
    # o 0.3.0 do v1.6 segue pinado e intacto
    pin = load_contract_pin(LOCK_PATH)
    assert (
        pin.artifact_digests[contratacao]
        == "b78936da13f0ee81f946a696911df313dd8d096abcaa2d1288d297f543000c5a"
    )


@pytest.mark.parametrize("n", VERSOES)
def test_loader_expoe_o_digest_do_openapi_e_o_do_manifest(n: int) -> None:
    nome, sha, _, _ = CASOS[n]
    pin = load_contract_pin(LOCK_PATH)
    assert pin.artifact_digests[_path(nome)] == sha
    assert getattr(pin, f"manifest_v1_{n}_digest") == _lock()[f"manifest_v1_{n}"]["manifest_pin"]["sha256"]


@pytest.mark.parametrize("n", VERSOES)
def test_openapi_vendorizado_bate_com_o_pin(n: int) -> None:
    nome, sha, _, _ = CASOS[n]
    copia = REPO_ROOT / "config/integrations/amh/openapi" / f"{nome}.openapi.yaml"  # vai na imagem
    assert b"\r" not in copia.read_bytes()
    assert hashlib.sha256(copia.read_bytes()).hexdigest() == sha


def test_v1_10_sem_o_v1_6_pinado_e_recusado_no_gate_e_no_loader(tmp_path: Path) -> None:
    lock = _lock()
    del lock[gate.V1_6_LOCK_KEY]
    assert "v1-10-predecessor-absent" in _codes(gate.verify_lock(lock, **VERIFY))
    caminho = tmp_path / "lock.json"
    caminho.write_text(json.dumps(lock, indent=2), encoding="utf-8")
    with pytest.raises(AmhContractPinError):
        load_contract_pin(caminho)


@pytest.mark.parametrize("n", VERSOES)
def test_bloco_invalido_e_recusado(n: int) -> None:
    piso = CASOS[n][3]
    chave = f"manifest_v1_{n}"
    lock = _lock()
    lock[chave]["publication"]["publication_run_id"] = str(piso)
    assert f"v1-{n}-run-regression" in _codes(gate.verify_lock(lock, **VERIFY))
    lock = _lock()
    lock[chave]["artifacts"][0]["path"] = "schemas/openapi/maezo/v1/outro.yaml"
    assert f"v1-{n}-artifact-unknown" in _codes(gate.verify_lock(lock, **VERIFY))


@pytest.mark.parametrize("n", VERSOES)
def test_candidate_recusa_trocar_o_digest_sob_o_mesmo_manifest(n: int) -> None:
    atual = _lock()
    trocado = copy.deepcopy(atual)
    trocado[f"manifest_v1_{n}"]["artifacts"][0]["sha256"] = "0" * 64
    assert f"candidate-v1-{n}-mutated" in _codes(gate.verify_candidate(atual, trocado))
    assert gate.verify_candidate(atual, copy.deepcopy(atual)) == []


def test_candidate_aceita_o_lock_novo_sobre_o_anterior_e_recusa_derrubar_um_bloco() -> None:
    novo = _lock()
    anterior = {k: v for k, v in novo.items() if k not in {f"manifest_v1_{n}" for n in VERSOES}}
    assert gate.verify_candidate(anterior, novo) == []
    sem_v1_9 = {k: v for k, v in novo.items() if k != "manifest_v1_9"}
    assert "candidate-v1-9-removed" in _codes(gate.verify_candidate(novo, sem_v1_9))
