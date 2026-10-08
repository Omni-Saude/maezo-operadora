"""Testes do pin ADITIVO v1.2 (TINA: fatos do plano) e do gerador generalizado.

Hermeticos: o manifest v1.2 "publicado" e' SINTETICO (montado aqui) e o lock real e' copiado para
tmp_path. O lock real NAO tem o bloco `manifest_v1_2` (o manifest v1.2 ainda e' DRAFT na AMH em
07/10/2026, a publicacao depende de atestacao humana): o pin atual tem de continuar valido e o
adaptador TINA segue fail-closed. O rigor e' o MESMO do v1.1, inclusive a imutabilidade do bloco sob o
mesmo manifest (`candidate-v1-2-mutated`), mais o v1.1 como predecessor obrigatorio.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from scripts.ci import gerar_pin_v1_1 as gerar
from scripts.ci.verify_amh_contract_pin import (
    DEFAULT_LOCK_PATH,
    V1_1_LOCK_KEY,
    V1_2,
    V1_2_ARTIFACT_PATHS,
    V1_2_LOCK_KEY,
    V1_2_MIN_PUBLICATION_RUN_ID,
    check_manifest_v1_1,
    main,
    verify_candidate,
    verify_lock,
    verify_manifest_v1_2,
)

from maezo.adapters.amh.contract import (
    V1_2_ARTIFACT_PATHS as LOADER_V1_2_ARTIFACT_PATHS,
)
from maezo.adapters.amh.contract import (
    V1_2_MIN_PUBLICATION_RUN_ID as LOADER_V1_2_MIN_RUN,
)
from maezo.adapters.amh.contract import (
    AmhContractPinError,
    load_contract_pin,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
LOCK_PATH = REPO_ROOT / DEFAULT_LOCK_PATH
LOCK_REAL = json.loads(LOCK_PATH.read_text(encoding="utf-8"))

V1_MANIFEST_SHA = LOCK_REAL["manifest_pin"]["sha256"]
V1_1_MANIFEST_SHA = LOCK_REAL[V1_1_LOCK_KEY]["manifest_pin"]["sha256"]
V1_1_EVIDENCE = LOCK_REAL[V1_1_LOCK_KEY]["provenance"]["evidence_id"]
COMMIT_PUB = "c" * 40
COMMIT_MANIFEST = "d" * 40
RUN_PUB = "37600000001"
TINA_SHA = "6" * 64
PREPUB_SHA = "7" * 64


def manifest_publicado(**over: str) -> bytes:
    """Manifest v1.2 sintetico no formato publicado (campos preenchidos, 1 ocorrencia por chave)."""
    v = {
        "status": "PUBLISHED",
        "amh_commit_sha": COMMIT_PUB,
        "evidence_id": f"XRG2-AMH-DEV-GHA-{RUN_PUB}",
        "publication_run_id": RUN_PUB,
        "published_at_utc": "2026-10-08T12:00:00Z",
        "prepub": PREPUB_SHA,
        "base": V1_MANIFEST_SHA,
        **over,
    }
    return (
        "manifest_version: 1.2.0\n"
        f"status: {v['status']}\n"
        "contract_name: amh-maezo-boundary\n"
        f'amh_commit_sha: "{v["amh_commit_sha"]}"\n'
        "compatibility_mode: BACKWARD\n"
        "base_manifest:\n"
        "  base_path: schemas/contracts/maezo/v1/contract-manifest.yaml\n"
        f"  base_pinned_sha256: {v['base']}\n"
        "artifacts:\n"
        f"  - path: {V1_2_ARTIFACT_PATHS[0]}\n    kind: openapi\n    sha256: {TINA_SHA}\n"
        "  - path: schemas/contracts/maezo/v1.2/contract-manifest.yaml\n"
        "    kind: contract-manifest\n    sha256: SELF-AT-PUBLICATION\n"
        "compatibility_report:\n"
        "  result: PASSED\n"
        '  provider_contract_tests: "120 passed"\n'
        '  dry_run_id: "github-actions-run-37599999999"\n'
        '  evidence_artifact_id: "12000000001"\n'
        f'  prepublication_manifest_sha256: "{v["prepub"]}"\n'
        "glue_registration:\n"
        "  environment: dev\n"
        f'  publication_run_id: "{v["publication_run_id"]}"\n'
        f'  published_at_utc: "{v["published_at_utc"]}"\n'
        f'evidence_id: "{v["evidence_id"]}"\n'
    ).encode()


def bloco(raw: bytes | None = None) -> dict[str, Any]:
    return gerar.montar_bloco(
        raw or manifest_publicado(),
        manifest_commit_sha=COMMIT_MANIFEST,
        verified_by="teste",
        verified_at_utc="2026-10-08T13:00:00Z",
        spec=V1_2,
    )


def lock_com_v1_2(**block_over: Any) -> dict[str, Any]:
    lock = copy.deepcopy(LOCK_REAL)
    b = bloco()
    for dotted, value in block_over.items():
        node = b
        parts = dotted.split("__")
        for part in parts[:-1]:
            node = node[part]
        node[parts[-1]] = value
    lock[V1_2_LOCK_KEY] = b
    return lock


def escreve(tmp_path: Path, lock: dict[str, Any], name: str = "lock.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(lock, indent=2), encoding="utf-8")
    return path


def codes(violations: list[Any]) -> set[str]:
    return {v.code for v in violations}


# ---------------------------------------------------------------------------
# Estado real: sem bloco v1.2 (DRAFT) -> pin valido e adaptador TINA sem digest
# ---------------------------------------------------------------------------


def test_lock_real_sem_v1_2_segue_valido_e_tina_sem_digest() -> None:
    assert V1_2_LOCK_KEY not in LOCK_REAL
    assert verify_lock(LOCK_REAL, repo_root=REPO_ROOT, check_vendored=False) == []
    pin = load_contract_pin(LOCK_PATH)
    assert pin.manifest_v1_2_digest is None
    assert V1_2_ARTIFACT_PATHS[0] not in pin.artifact_digests


def test_constantes_v1_2_iguais_no_loader_e_no_verificador() -> None:
    assert tuple(LOADER_V1_2_ARTIFACT_PATHS) == V1_2_ARTIFACT_PATHS
    assert LOADER_V1_2_MIN_RUN == V1_2_MIN_PUBLICATION_RUN_ID
    # o piso do v1.2 e' o run de publicacao do v1.1 pinado
    assert str(V1_2_MIN_PUBLICATION_RUN_ID) == LOCK_REAL[V1_1_LOCK_KEY]["publication"]["publication_run_id"]


# ---------------------------------------------------------------------------
# Lock com bloco v1.2 valido
# ---------------------------------------------------------------------------


def test_lock_com_bloco_v1_2_passa_e_expoe_o_digest_tina(tmp_path: Path) -> None:
    lock = lock_com_v1_2()
    assert verify_lock(lock, repo_root=REPO_ROOT, check_vendored=False) == []
    pin = load_contract_pin(escreve(tmp_path, lock))
    assert pin.artifact_digests[V1_2_ARTIFACT_PATHS[0]] == TINA_SHA
    assert pin.manifest_v1_2_digest == lock[V1_2_LOCK_KEY]["manifest_pin"]["sha256"]
    # v1 (5) + v1.1 (2) + v1.2 (1)
    assert len(pin.artifact_digests) == 8
    # o v1.1 nao mudou de codigo de violacao com a generalizacao
    assert check_manifest_v1_1(lock) == []


@pytest.mark.parametrize(
    ("override", "code"),
    [
        ({"provenance__evidence_id": "XRG2-AMH-DEV-GHA-30991849241"}, "v1-2-evidence-reuse"),
        ({"provenance__evidence_id": V1_1_EVIDENCE}, "v1-2-evidence-reuse"),
        ({"publication__publication_run_id": str(V1_2_MIN_PUBLICATION_RUN_ID)}, "v1-2-run-regression"),
        ({"publication__publication_run_id": "31500000001"}, "v1-2-run-regression"),
        ({"manifest_pin__sha256": V1_MANIFEST_SHA}, "v1-2-digest-reuse"),
        ({"manifest_pin__sha256": V1_1_MANIFEST_SHA}, "v1-2-digest-reuse"),
        ({"manifest_pin__sha256": PREPUB_SHA}, "v1-2-digest-degenerate"),
        ({"manifest_pin__path": "schemas/contracts/maezo/v1.1/contract-manifest.yaml"}, "v1-2-frozen-value"),
        ({"provenance__status": "DRAFT"}, "v1-2-frozen-value"),
        ({"compatibility_report__result": "FAILED"}, "v1-2-frozen-value"),
        ({"artifacts": []}, "v1-2-artifact-count"),
        (
            {"artifacts": [{"path": "schemas/openapi/maezo/v1/outro.yaml", "sha256": "4" * 64}]},
            "v1-2-artifact-unknown",
        ),
    ],
)
def test_bloco_v1_2_invalido_e_recusado(override: dict[str, Any], code: str) -> None:
    violations = verify_lock(lock_com_v1_2(**override), repo_root=REPO_ROOT, check_vendored=False)
    assert code in codes(violations)


def test_v1_2_sem_o_v1_1_pinado_e_recusado_no_gate_e_no_loader(tmp_path: Path) -> None:
    lock = lock_com_v1_2()
    del lock[V1_1_LOCK_KEY]
    assert "v1-2-predecessor-absent" in codes(verify_lock(lock, repo_root=REPO_ROOT, check_vendored=False))
    with pytest.raises(AmhContractPinError):
        load_contract_pin(escreve(tmp_path, lock))


def test_loader_recusa_v1_2_com_run_antigo_ou_evidencia_do_v1_1(tmp_path: Path) -> None:
    with pytest.raises(AmhContractPinError):
        load_contract_pin(escreve(tmp_path, lock_com_v1_2(publication__publication_run_id="37555121279")))
    with pytest.raises(AmhContractPinError):
        load_contract_pin(escreve(tmp_path, lock_com_v1_2(provenance__evidence_id=V1_1_EVIDENCE), "b.json"))


def test_placeholder_no_bloco_v1_2_e_recusado(tmp_path: Path) -> None:
    lock = lock_com_v1_2(xrg3_verification__verified_by="<SET-AT-PUBLICATION>")
    assert "placeholder" in codes(verify_lock(lock, repo_root=REPO_ROOT, check_vendored=False))
    with pytest.raises(AmhContractPinError):
        load_contract_pin(escreve(tmp_path, lock))


# ---------------------------------------------------------------------------
# --candidate: imutabilidade do bloco sob o mesmo manifest
# ---------------------------------------------------------------------------


def test_candidate_que_acrescenta_v1_2_valido_passa() -> None:
    assert verify_candidate(copy.deepcopy(LOCK_REAL), lock_com_v1_2()) == []


def test_candidate_recusa_trocar_o_digest_tina_sob_o_mesmo_manifest() -> None:
    current = lock_com_v1_2()
    trocado = copy.deepcopy(current)
    trocado[V1_2_LOCK_KEY]["artifacts"][0]["sha256"] = "0" * 64
    assert "candidate-v1-2-mutated" in codes(verify_candidate(current, trocado))


def test_candidate_recusa_mudar_proveniencia_sob_o_mesmo_manifest() -> None:
    current = lock_com_v1_2()
    for campo in ("provenance", "publication", "xrg3_verification"):
        mexido = copy.deepcopy(current)
        bloco_v12 = mexido[V1_2_LOCK_KEY]
        chave = next(iter(bloco_v12[campo]))
        bloco_v12[campo][chave] = str(bloco_v12[campo][chave]) + "x"
        assert "candidate-v1-2-mutated" in codes(verify_candidate(current, mexido)), campo


def test_candidate_recusa_remover_regredir_ou_reusar_evidencia() -> None:
    current = lock_com_v1_2()
    stripped = copy.deepcopy(current)
    del stripped[V1_2_LOCK_KEY]
    assert "candidate-v1-2-removed" in codes(verify_candidate(current, stripped))
    older = lock_com_v1_2(publication__publication_run_id="37590000000")
    assert "candidate-v1-2-run-regression" in codes(verify_candidate(current, older))
    reuso = lock_com_v1_2(provenance__evidence_id=V1_1_EVIDENCE)
    assert "candidate-v1-2-evidence-reuse" in codes(verify_candidate(copy.deepcopy(LOCK_REAL), reuso))


def test_candidate_com_o_mesmo_bloco_v1_2_passa() -> None:
    current = lock_com_v1_2()
    assert verify_candidate(current, copy.deepcopy(current)) == []


# ---------------------------------------------------------------------------
# --manifest v1.2
# ---------------------------------------------------------------------------


def test_manifest_v1_2_publicado_confere_com_o_bloco(tmp_path: Path) -> None:
    path = tmp_path / "m.yaml"
    path.write_bytes(manifest_publicado())
    assert verify_manifest_v1_2(lock_com_v1_2(), path) == []


def test_manifest_v1_2_pode_declarar_o_v1_1_como_base(tmp_path: Path) -> None:
    raw = manifest_publicado(base=V1_1_MANIFEST_SHA)
    lock = copy.deepcopy(LOCK_REAL)
    lock[V1_2_LOCK_KEY] = bloco(raw)
    path = tmp_path / "m.yaml"
    path.write_bytes(raw)
    assert verify_manifest_v1_2(lock, path) == []
    raw_ruim = manifest_publicado(base="8" * 64)
    lock[V1_2_LOCK_KEY] = bloco(raw_ruim)
    path.write_bytes(raw_ruim)
    assert "manifest-mismatch" in codes(verify_manifest_v1_2(lock, path))


def test_manifest_v1_2_adulterado_e_recusado(tmp_path: Path) -> None:
    path = tmp_path / "m.yaml"
    path.write_bytes(manifest_publicado().replace(TINA_SHA.encode(), b"9" * 64))
    found = codes(verify_manifest_v1_2(lock_com_v1_2(), path))
    assert {"manifest-digest-mismatch", "manifest-artifact-mismatch"} <= found


def test_manifest_v1_2_sem_bloco_no_lock_explica_o_proximo_passo(tmp_path: Path) -> None:
    path = tmp_path / "m.yaml"
    path.write_bytes(manifest_publicado())
    assert codes(verify_manifest_v1_2(copy.deepcopy(LOCK_REAL), path)) == {"v1-2-pin-absent"}


def test_cli_manifest_version_v1_2(tmp_path: Path) -> None:
    lock_path = escreve(tmp_path, lock_com_v1_2())
    manifest = tmp_path / "m.yaml"
    manifest.write_bytes(manifest_publicado())
    argv = ["--lock", str(lock_path), "--manifest", str(manifest), "--manifest-version", "v1.2"]
    assert main(argv) == 0
    manifest.write_bytes(manifest_publicado() + b"# x\n")
    assert main(argv) == 1


# ---------------------------------------------------------------------------
# gerador com --manifest-version v1.2
# ---------------------------------------------------------------------------


def test_bloco_v1_2_gerado_tem_so_o_artefato_tina() -> None:
    raw = manifest_publicado()
    b = bloco(raw)
    assert b["manifest_pin"]["path"] == "schemas/contracts/maezo/v1.2/contract-manifest.yaml"
    assert b["manifest_pin"]["sha256"] == hashlib.sha256(raw).hexdigest()
    assert [a["path"] for a in b["artifacts"]] == list(V1_2_ARTIFACT_PATHS)
    assert "v1.2" in b["xrg3_verification"]["verification_method"][0]


def test_gerador_v1_2_recusa_manifest_de_outra_versao() -> None:
    raw = manifest_publicado().replace(b"manifest_version: 1.2.0", b"manifest_version: 1.1.0")
    with pytest.raises(gerar.GerarPinError):
        bloco(raw)


def test_gerador_write_v1_2_grava_valida_e_nao_sobrescreve(tmp_path: Path) -> None:
    lock_path = tmp_path / "lock.json"
    lock_path.write_text(json.dumps(LOCK_REAL, indent=2), encoding="utf-8")
    manifest = tmp_path / "m.yaml"
    manifest.write_bytes(manifest_publicado())
    argv = [
        "--manifest", str(manifest), "--manifest-commit-sha", COMMIT_MANIFEST,
        "--verified-by", "teste", "--lock", str(lock_path), "--write", "--manifest-version", "v1.2",
    ]  # fmt: skip
    assert gerar.main(argv) == 0
    gravado = json.loads(lock_path.read_text(encoding="utf-8"))
    assert {k: v for k, v in gravado.items() if k != V1_2_LOCK_KEY} == LOCK_REAL
    assert verify_lock(gravado, repo_root=REPO_ROOT, check_vendored=False) == []
    assert main(["--lock", str(lock_path), "--manifest", str(manifest), "--manifest-version", "v1.2"]) == 0
    assert gerar.main(argv) == 1  # pin imutavel


def test_gerador_write_v1_2_sem_v1_1_no_lock_e_recusado(tmp_path: Path) -> None:
    lock = copy.deepcopy(LOCK_REAL)
    del lock[V1_1_LOCK_KEY]
    lock_path = escreve(tmp_path, lock)
    manifest = tmp_path / "m.yaml"
    manifest.write_bytes(manifest_publicado())
    argv = [
        "--manifest", str(manifest), "--manifest-commit-sha", COMMIT_MANIFEST,
        "--verified-by", "teste", "--lock", str(lock_path), "--write", "--manifest-version", "v1.2",
    ]  # fmt: skip
    assert gerar.main(argv) == 1
    assert V1_2_LOCK_KEY not in json.loads(lock_path.read_text(encoding="utf-8"))
