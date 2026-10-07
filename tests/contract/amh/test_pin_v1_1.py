"""Testes do pin ADITIVO v1.1 (billing-status + subject-resolution) e do gerador do bloco.

Hermeticos: o manifest "publicado" e' SINTETICO (montado aqui), o lock real e' copiado para tmp_path.
O lock real NAO tem o bloco `manifest_v1_1` (manifest ainda UNPUBLISHED na AMH): o pin so-v1 tem de
continuar valido e os adaptadores de cobranca/identidade seguem fail-closed.
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
    V1_1_ARTIFACT_PATHS,
    V1_1_LOCK_KEY,
    V1_1_MIN_PUBLICATION_RUN_ID,
    main,
    verify_candidate,
    verify_lock,
    verify_manifest,
    verify_manifest_v1_1,
)

from maezo.adapters.amh.contract import (
    V1_1_ARTIFACT_PATHS as LOADER_V1_1_ARTIFACT_PATHS,
)
from maezo.adapters.amh.contract import (
    AmhContractPinError,
    load_contract_pin,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
LOCK_PATH = REPO_ROOT / DEFAULT_LOCK_PATH

V1_MANIFEST_SHA = json.loads(LOCK_PATH.read_text(encoding="utf-8"))["manifest_pin"]["sha256"]
COMMIT_PUB = "a" * 40
COMMIT_MANIFEST = "b" * 40
RUN_PUB = "31500000001"
BILLING_SHA = "1" * 64
SUBJECT_SHA = "2" * 64
PREPUB_SHA = "3" * 64


def manifest_publicado(**over: str) -> bytes:
    """Manifest v1.1 sintetico no formato publicado (campos preenchidos, 1 ocorrencia por chave)."""
    v = {
        "status": "PUBLISHED",
        "amh_commit_sha": COMMIT_PUB,
        "evidence_id": f"XRG2-AMH-DEV-GHA-{RUN_PUB}",
        "publication_run_id": RUN_PUB,
        "published_at_utc": "2026-10-07T12:00:00Z",
        "prepub": PREPUB_SHA,
        **over,
    }
    return (
        "manifest_version: 1.1.0\n"
        f"status: {v['status']}\n"
        "contract_name: amh-maezo-boundary\n"
        f'amh_commit_sha: "{v["amh_commit_sha"]}"\n'
        "compatibility_mode: BACKWARD\n"
        "base_manifest:\n"
        "  base_path: schemas/contracts/maezo/v1/contract-manifest.yaml\n"
        f"  base_pinned_sha256: {V1_MANIFEST_SHA}\n"
        "artifacts:\n"
        f"  - path: {V1_1_ARTIFACT_PATHS[0]}\n    kind: openapi\n    sha256: {BILLING_SHA}\n"
        f"  - path: {V1_1_ARTIFACT_PATHS[1]}\n    kind: openapi\n    sha256: {SUBJECT_SHA}\n"
        "  - path: schemas/contracts/maezo/v1.1/contract-manifest.yaml\n"
        "    kind: contract-manifest\n    sha256: SELF-AT-PUBLICATION\n"
        "compatibility_report:\n"
        "  result: PASSED\n"
        '  provider_contract_tests: "100 passed"\n'
        '  dry_run_id: "github-actions-run-31499999999"\n'
        '  evidence_artifact_id: "9000000001"\n'
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
        verified_at_utc="2026-10-07T13:00:00Z",
    )


def lock_com_v1_1(**block_over: Any) -> dict[str, Any]:
    lock = copy.deepcopy(json.loads(LOCK_PATH.read_text(encoding="utf-8")))
    b = bloco()
    for dotted, value in block_over.items():
        node = b
        parts = dotted.split("__")
        for part in parts[:-1]:
            node = node[part]
        node[parts[-1]] = value
    lock[V1_1_LOCK_KEY] = b
    return lock


def escreve(tmp_path: Path, lock: dict[str, Any], name: str = "lock.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(lock, indent=2), encoding="utf-8")
    return path


def codes(violations: list[Any]) -> set[str]:
    return {v.code for v in violations}


# ---------------------------------------------------------------------------
# Pin so-v1 (estado real de hoje) segue valido; v1.1 e' opcional
# ---------------------------------------------------------------------------


def test_lock_real_sem_bloco_v1_1_segue_valido_e_adaptadores_sem_digest() -> None:
    lock = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    assert V1_1_LOCK_KEY not in lock
    assert verify_lock(lock, repo_root=REPO_ROOT, check_vendored=False) == []
    pin = load_contract_pin(LOCK_PATH)
    assert pin.manifest_v1_1_digest is None
    for path in V1_1_ARTIFACT_PATHS:
        assert path not in pin.artifact_digests  # billing_status/subject_resolution recusam


def test_caminhos_v1_1_iguais_no_loader_e_no_verificador() -> None:
    assert tuple(LOADER_V1_1_ARTIFACT_PATHS) == V1_1_ARTIFACT_PATHS


# ---------------------------------------------------------------------------
# Lock com bloco v1.1 valido
# ---------------------------------------------------------------------------


def test_lock_com_bloco_v1_1_passa_sem_ids_glue(tmp_path: Path) -> None:
    lock = lock_com_v1_1()
    assert "schema_version_ids" not in json.dumps(lock[V1_1_LOCK_KEY])
    assert "schema_version_status" not in json.dumps(lock[V1_1_LOCK_KEY])
    assert verify_lock(lock, repo_root=REPO_ROOT, check_vendored=False) == []

    pin = load_contract_pin(escreve(tmp_path, lock))
    assert pin.artifact_digests[V1_1_ARTIFACT_PATHS[0]] == BILLING_SHA
    assert pin.artifact_digests[V1_1_ARTIFACT_PATHS[1]] == SUBJECT_SHA
    assert pin.manifest_v1_1_digest == lock[V1_1_LOCK_KEY]["manifest_pin"]["sha256"]
    # os 5 do v1 continuam ali
    assert len(pin.artifact_digests) == 7


@pytest.mark.parametrize(
    ("override", "code"),
    [
        ({"provenance__evidence_id": "XRG2-AMH-DEV-GHA-30991849241"}, "v1-1-evidence-reuse"),
        ({"publication__publication_run_id": "30991849240"}, "v1-1-run-regression"),
        ({"publication__publication_run_id": str(V1_1_MIN_PUBLICATION_RUN_ID)}, "v1-1-run-regression"),
        ({"manifest_pin__sha256": V1_MANIFEST_SHA}, "v1-1-digest-reuse"),
        ({"manifest_pin__sha256": PREPUB_SHA}, "v1-1-digest-degenerate"),
        ({"manifest_pin__path": "schemas/contracts/maezo/v1/contract-manifest.yaml"}, "v1-1-frozen-value"),
        ({"provenance__status": "UNPUBLISHED"}, "v1-1-frozen-value"),
        ({"compatibility_report__result": "FAILED"}, "v1-1-frozen-value"),
        ({"artifacts": []}, "v1-1-artifact-count"),
    ],
)
def test_bloco_v1_1_invalido_e_recusado(override: dict[str, Any], code: str) -> None:
    violations = verify_lock(lock_com_v1_1(**override), repo_root=REPO_ROOT, check_vendored=False)
    assert code in codes(violations)


def test_placeholder_no_bloco_v1_1_e_recusado(tmp_path: Path) -> None:
    lock = lock_com_v1_1(xrg3_verification__verified_by="<SET-AT-PUBLICATION>")
    assert "placeholder" in codes(verify_lock(lock, repo_root=REPO_ROOT, check_vendored=False))
    with pytest.raises(AmhContractPinError):
        load_contract_pin(escreve(tmp_path, lock))


def test_loader_recusa_bloco_v1_1_com_artefato_extra_ou_run_antigo(tmp_path: Path) -> None:
    lock = lock_com_v1_1()
    lock[V1_1_LOCK_KEY]["artifacts"].append(
        {"path": "schemas/openapi/maezo/v1/outro.yaml", "sha256": "4" * 64}
    )
    with pytest.raises(AmhContractPinError):
        load_contract_pin(escreve(tmp_path, lock))
    lock = lock_com_v1_1(publication__publication_run_id="30991849241")
    with pytest.raises(AmhContractPinError):
        load_contract_pin(escreve(tmp_path, lock, "b.json"))


# ---------------------------------------------------------------------------
# --manifest v1.1
# ---------------------------------------------------------------------------


def test_manifest_v1_1_publicado_confere_com_o_bloco(tmp_path: Path) -> None:
    raw = manifest_publicado()
    path = tmp_path / "m.yaml"
    path.write_bytes(raw)
    assert verify_manifest_v1_1(lock_com_v1_1(), path) == []


def test_manifest_v1_1_adulterado_e_recusado(tmp_path: Path) -> None:
    path = tmp_path / "m.yaml"
    path.write_bytes(manifest_publicado().replace(BILLING_SHA.encode(), b"9" * 64))
    found = codes(verify_manifest_v1_1(lock_com_v1_1(), path))
    assert {"manifest-digest-mismatch", "manifest-artifact-mismatch"} <= found


def test_manifest_v1_1_sem_bloco_no_lock_explica_o_proximo_passo(tmp_path: Path) -> None:
    path = tmp_path / "m.yaml"
    path.write_bytes(manifest_publicado())
    lock = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    assert codes(verify_manifest_v1_1(lock, path)) == {"v1-1-pin-absent"}


def test_verify_manifest_v1_continua_recusando_bytes_do_v1_1(tmp_path: Path) -> None:
    path = tmp_path / "m.yaml"
    path.write_bytes(manifest_publicado())
    assert "manifest-digest-mismatch" in codes(verify_manifest(lock_com_v1_1(), path))


def test_cli_manifest_version_v1_1(tmp_path: Path) -> None:
    lock_path = escreve(tmp_path, lock_com_v1_1())
    manifest = tmp_path / "m.yaml"
    manifest.write_bytes(manifest_publicado())
    argv = ["--lock", str(lock_path), "--manifest", str(manifest), "--manifest-version", "v1.1"]
    assert main(argv) == 0
    manifest.write_bytes(manifest_publicado() + b"# x\n")
    assert main(argv) == 1


# ---------------------------------------------------------------------------
# --candidate
# ---------------------------------------------------------------------------


def test_candidate_com_v1_1_valido_passa() -> None:
    current = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    assert verify_candidate(current, lock_com_v1_1()) == []


def test_candidate_recusa_evidence_reutilizado_e_run_antigo() -> None:
    current = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    reuse = lock_com_v1_1(provenance__evidence_id=current["provenance"]["evidence_id"])
    assert "candidate-v1-1-evidence-reuse" in codes(verify_candidate(current, reuse))
    old = lock_com_v1_1(publication__publication_run_id="30991849240")
    assert "candidate-v1-1-run-regression" in codes(verify_candidate(current, old))


def test_candidate_recusa_trocar_artefato_sob_o_mesmo_manifest() -> None:
    """Achado P1 do #676: mesmo manifest_pin.sha256, evidence_id e run; so' um digest de artefato muda."""
    current = lock_com_v1_1()
    trocado = copy.deepcopy(current)
    trocado[V1_1_LOCK_KEY]["artifacts"][0]["sha256"] = "0" * 64
    assert "candidate-v1-1-mutated" in codes(verify_candidate(current, trocado))


def test_candidate_recusa_mudar_proveniencia_sob_o_mesmo_manifest() -> None:
    current = lock_com_v1_1()
    for campo in ("provenance", "publication", "xrg3_verification"):
        mexido = copy.deepcopy(current)
        bloco_v11 = mexido[V1_1_LOCK_KEY]
        chave = next(iter(bloco_v11[campo]))
        bloco_v11[campo][chave] = str(bloco_v11[campo][chave]) + "x"
        assert "candidate-v1-1-mutated" in codes(verify_candidate(current, mexido)), campo


def test_candidate_com_o_mesmo_bloco_passa() -> None:
    current = lock_com_v1_1()
    assert verify_candidate(current, copy.deepcopy(current)) == []


def test_candidate_recusa_remover_ou_regredir_o_bloco() -> None:
    current = lock_com_v1_1()
    stripped = copy.deepcopy(current)
    del stripped[V1_1_LOCK_KEY]
    assert "candidate-v1-1-removed" in codes(verify_candidate(current, stripped))
    older = lock_com_v1_1(publication__publication_run_id="31400000000")
    assert "candidate-v1-1-run-regression" in codes(verify_candidate(current, older))
    swapped = lock_com_v1_1(manifest_pin__sha256="5" * 64)
    swapped[V1_1_LOCK_KEY]["provenance"]["evidence_id"] = current[V1_1_LOCK_KEY]["provenance"]["evidence_id"]
    assert "candidate-v1-1-evidence-reuse" in codes(verify_candidate(current, swapped))


# ---------------------------------------------------------------------------
# gerar_pin_v1_1
# ---------------------------------------------------------------------------


def test_bloco_gerado_usa_o_esquema_do_pin_v1() -> None:
    raw = manifest_publicado()
    b = bloco(raw)
    assert b["manifest_pin"]["sha256"] == hashlib.sha256(raw).hexdigest()
    assert b["manifest_pin"]["byte_size"] == len(raw)
    assert b["manifest_pin"]["prepublication_sha256"] == PREPUB_SHA
    assert b["manifest_pin"]["git_blob_sha"] == hashlib.sha1(b"blob %d\0" % len(raw) + raw).hexdigest()
    assert b["provenance"]["amh_manifest_commit_sha"] == COMMIT_MANIFEST
    assert b["compatibility_report"]["dry_run_run_id"] == "31499999999"
    assert b["publication"]["publication_run_id"] == RUN_PUB
    assert [a["path"] for a in b["artifacts"]] == list(V1_1_ARTIFACT_PATHS)


def test_git_blob_sha_bate_com_git_hash_object() -> None:
    # vetor conhecido: `printf 'hello\n' | git hash-object --stdin`
    assert gerar.git_blob_sha(b"hello\n") == "ce013625030ba8dba906f756967f9e9ca394464a"


@pytest.mark.parametrize(
    "raw",
    [
        manifest_publicado(status="UNPUBLISHED"),
        manifest_publicado(amh_commit_sha="<SET-AT-PUBLICATION>"),
        manifest_publicado().replace(b"manifest_version: 1.1.0", b"manifest_version: 1.0.0"),
        manifest_publicado() + b"status: PUBLISHED\nstatus: DRAFT\n",
        b"\xff\xfe",
    ],
)
def test_gerador_recusa_manifest_nao_publicado_ou_malformado(raw: bytes) -> None:
    with pytest.raises(gerar.GerarPinError):
        gerar.montar_bloco(
            raw, manifest_commit_sha=COMMIT_MANIFEST, verified_by="t", verified_at_utc="2026-10-07T13:00:00Z"
        )


def test_gerador_write_grava_valida_e_nao_sobrescreve(tmp_path: Path) -> None:
    lock_path = tmp_path / "lock.json"
    lock_path.write_bytes(LOCK_PATH.read_bytes())
    manifest = tmp_path / "m.yaml"
    manifest.write_bytes(manifest_publicado())
    argv = [
        "--manifest", str(manifest), "--manifest-commit-sha", COMMIT_MANIFEST,
        "--verified-by", "teste", "--lock", str(lock_path), "--write",
    ]  # fmt: skip
    assert gerar.main(argv) == 0
    gravado = json.loads(lock_path.read_text(encoding="utf-8"))
    assert (
        gravado[V1_1_LOCK_KEY]["manifest_pin"]["sha256"] == hashlib.sha256(manifest.read_bytes()).hexdigest()
    )
    # o resto do lock (v1) ficou intacto
    original = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    assert {k: v for k, v in gravado.items() if k != V1_1_LOCK_KEY} == original
    assert verify_lock(gravado, repo_root=REPO_ROOT, check_vendored=False) == []
    assert main(["--lock", str(lock_path), "--manifest", str(manifest), "--manifest-version", "v1.1"]) == 0
    # segunda gravacao: recusada (pin imutavel)
    assert gerar.main(argv) == 1


def test_gerador_sem_commit_do_manifest_e_erro_de_uso(tmp_path: Path) -> None:
    manifest = tmp_path / "m.yaml"
    manifest.write_bytes(manifest_publicado())
    assert gerar.main(["--manifest", str(manifest), "--verified-by", "t"]) == 1


def test_gerador_via_gh_confere_blob_sha_e_resolve_commit() -> None:
    raw = manifest_publicado()
    blob = gerar.git_blob_sha(raw)

    class Done:
        def __init__(self, out: bytes) -> None:
            self.stdout = out

    def runner(cmd: list[str], **_: Any) -> Done:
        joined = " ".join(cmd)
        if "/commits/" in joined:
            return Done(COMMIT_MANIFEST.encode() + b"\n")
        if "raw+json" in joined:
            return Done(raw)
        return Done(json.dumps({"sha": blob}).encode())

    got, commit = gerar.baixar_do_github("o/r", "main", runner)
    assert (got, commit) == (raw, COMMIT_MANIFEST)

    def runner_ruim(cmd: list[str], **kw: Any) -> Done:
        done = runner(cmd, **kw)
        return (
            Done(json.dumps({"sha": "0" * 40}).encode())
            if "raw+json" not in " ".join(cmd) and "/commits/" not in " ".join(cmd)
            else done
        )

    with pytest.raises(gerar.GerarPinError):
        gerar.baixar_do_github("o/r", "main", runner_ruim)
