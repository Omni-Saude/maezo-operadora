"""EP-CORE-09a (E-SF-33; CORE-09) — guarda de proveniencia omni -> Maezo.

Casos negativos injetam a violacao em `tmp_path`: um conteudo SINTETICO escrito aqui (nao e conteudo do
omni) cujo hash e forcado a constar de um manifesto de fixture sob o caminho que a SPEC cita
(`src/server/intelligence/comparison.ts`). Nada do omni e copiado para o repo Maezo.

Casos sobre a arvore real: o manifesto versionado tem a forma esperada, `docs/governance/licensing/` esta
vazio (estado fechado por construcao) e as recusas sobre `git ls-files` sao EXATAMENTE o passivo declarado
(`omni_first_pending_adjudication`) -- qualquer copia nova falha.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from scripts.docs.omni_provenance import (
    COMPARTILHADOS_REL,
    LICENSING_REL,
    MANIFESTO_REL,
    MIN_NORMALIZADO,
    OMNI_COMMIT,
    Manifesto,
    ProvenanceRefusal,
    arquivos_rastreados,
    carregar_manifesto,
    normalizar,
    origem_maezo,
    sha256,
    verificar_arquivo,
    verificar_arvore,
)

_REPO = Path(__file__).resolve().parents[2]
_OMNI_PATH = "src/server/intelligence/comparison.ts"
_ALVO = "src/maezo/portal/web/src/comparison.ts"

# Conteudo sintetico escrito para este teste (nao vem do omni).
_SINTETICO = (
    b"export function sinteticoDeTeste(a: number, b: number): number {\n"
    b"  const soma = a + b;\n"
    b"  return soma * 2;\n"
    b"}\n"
)


def _cabecalho(ato: str | None) -> bytes:
    # Montado por concatenacao para que ESTE arquivo nao contenha um cabecalho real em inicio de linha.
    linhas = ["// " + "Provenance" + ": Omni-Saude/omni_sales_platform@" + OMNI_COMMIT + ":" + _OMNI_PATH]
    if ato is not None:
        linhas.append("// " + "License-Act" + ": " + ato)
    return ("\n".join(linhas) + "\n").encode()


@pytest.fixture
def manifesto(tmp_path: Path) -> Manifesto:
    arq = tmp_path / "fixture.blobs.json"
    norm = normalizar(_SINTETICO)
    arq.write_text(
        json.dumps(
            {
                "source": {"repository": "Omni-Saude/omni_sales_platform", "commit": OMNI_COMMIT},
                "blobs": [
                    {
                        "path": _OMNI_PATH,
                        "sha256_raw": sha256(_SINTETICO),
                        "normalized_size": len(norm),
                        "sha256_normalized": sha256(norm),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return carregar_manifesto(arq)


def _arvore(raiz: Path, conteudo: bytes) -> list[str]:
    alvo = raiz / _ALVO
    alvo.parent.mkdir(parents=True)
    alvo.write_bytes(conteudo)
    return [_ALVO]


def test_unlicensed_omni_blob_refused(tmp_path: Path, manifesto: Manifesto) -> None:
    raiz = tmp_path / "maezo"
    recusas = verificar_arvore(raiz, _arvore(raiz, _SINTETICO), manifesto, raiz / LICENSING_REL)
    assert [(r.motivo, r.caminho) for r in recusas] == [(ProvenanceRefusal.UNLICENSED_OMNI_ORIGIN, _ALVO)]
    assert "bruto" in recusas[0].detalhe and _OMNI_PATH in recusas[0].detalhe


def test_whitespace_normalized_copy_refused(tmp_path: Path, manifesto: Manifesto) -> None:
    reindentado = _SINTETICO.replace(b"  ", b"\t\t").replace(b"\n", b"\r\n").replace(b"(a", b"( a")
    assert sha256(reindentado) != sha256(_SINTETICO)
    raiz = tmp_path / "maezo"
    recusas = verificar_arvore(raiz, _arvore(raiz, reindentado), manifesto, raiz / LICENSING_REL)
    assert [r.motivo for r in recusas] == [ProvenanceRefusal.UNLICENSED_OMNI_ORIGIN]
    assert "normalizado" in recusas[0].detalhe


def test_provenance_header_without_act_refused(tmp_path: Path, manifesto: Manifesto) -> None:
    raiz = tmp_path / "maezo"
    # Ato inexistente, contra o diretorio de licencas REAL do repo (vazio) e contra um vazio em tmp.
    for licensing in (_REPO / LICENSING_REL, raiz / LICENSING_REL):
        recusa = verificar_arquivo(_ALVO, _cabecalho("OMNI-LA-999") + _SINTETICO, manifesto, licensing)
        assert recusa is not None
        assert recusa.motivo is ProvenanceRefusal.LICENSE_ACT_NOT_FOUND
        assert "OMNI-LA-999" in recusa.detalhe
    # Cabecalho de proveniencia sem linha License-Act tambem e recusado.
    sem_ato = verificar_arquivo(_ALVO, _cabecalho(None) + _SINTETICO, manifesto, raiz / LICENSING_REL)
    assert sem_ato is not None and sem_ato.motivo is ProvenanceRefusal.LICENSE_ACT_NOT_FOUND


def test_resolved_act_is_accepted_control(tmp_path: Path, manifesto: Manifesto) -> None:
    """Controle positivo: a guarda nao recusa tudo; um ato que resolve libera o arquivo com cabecalho."""
    licensing = tmp_path / "licensing"
    licensing.mkdir()
    (licensing / "OMNI-LA-TESTE.json").write_text(json.dumps({"act_id": "OMNI-LA-TESTE"}), encoding="utf-8")
    assert verificar_arquivo(_ALVO, _cabecalho("OMNI-LA-TESTE") + _SINTETICO, manifesto, licensing) is None
    # registro com act_id divergente nao resolve
    (licensing / "OMNI-LA-OUTRO.json").write_text(json.dumps({"act_id": "OMNI-LA-X"}), encoding="utf-8")
    recusa = verificar_arquivo(_ALVO, _cabecalho("OMNI-LA-OUTRO") + _SINTETICO, manifesto, licensing)
    assert recusa is not None and recusa.motivo is ProvenanceRefusal.LICENSE_ACT_NOT_FOUND


def test_unrelated_and_trivial_files_are_not_refused(manifesto: Manifesto, tmp_path: Path) -> None:
    assert verificar_arquivo("x.ts", b"export const outro = 1;\n", manifesto, tmp_path) is None
    assert len(normalizar(b"{}\n")) < MIN_NORMALIZADO


def test_licensing_directory_is_empty_closed_by_construction() -> None:
    rastreados = [p for p in arquivos_rastreados(_REPO) if p.startswith(LICENSING_REL + "/")]
    assert rastreados == []
    pasta = _REPO / LICENSING_REL
    assert not pasta.exists() or not any(pasta.iterdir())


def test_versioned_manifest_shape_has_no_omni_content() -> None:
    dados = json.loads((_REPO / MANIFESTO_REL).read_text(encoding="utf-8"))
    assert dados["source"] == {"repository": "Omni-Saude/omni_sales_platform", "commit": OMNI_COMMIT}
    blobs = dados["blobs"]
    assert dados["blob_count"] == len(blobs) == 1959
    chaves = {"path", "mode", "git_blob", "size", "sha256_raw", "normalized_size", "sha256_normalized"}
    hexa = re.compile(r"[0-9a-f]{64}")
    caminhos = [b["path"] for b in blobs]
    assert caminhos == sorted(set(caminhos))
    for b in blobs:
        assert set(b) == chaves
        assert hexa.fullmatch(b["sha256_raw"]) and hexa.fullmatch(b["sha256_normalized"])
        assert re.fullmatch(r"[0-9a-f]{40}", b["git_blob"])
        assert 0 <= b["normalized_size"] <= b["size"]


def test_real_tree_refusals_are_exactly_the_declared_pending_set() -> None:
    compartilhados = json.loads((_REPO / COMPARTILHADOS_REL).read_text(encoding="utf-8"))
    no_manifesto = {
        b["sha256_raw"] for b in json.loads((_REPO / MANIFESTO_REL).read_text(encoding="utf-8"))["blobs"]
    }
    pendentes = {
        (e["maezo_path"], e["sha256_raw"])
        for e in compartilhados["entries"]
        if e["class"] == "omni_first_pending_adjudication"
    }
    for e in compartilhados["entries"]:
        assert e["sha256_raw"] in no_manifesto
        assert e["class"] in {"maezo_origin", "omni_first_pending_adjudication"}
    manifesto = carregar_manifesto(_REPO / MANIFESTO_REL, origem_maezo(_REPO / COMPARTILHADOS_REL))
    recusas = verificar_arvore(_REPO, arquivos_rastreados(_REPO), manifesto, _REPO / LICENSING_REL)
    assert {r.motivo for r in recusas} <= {ProvenanceRefusal.UNLICENSED_OMNI_ORIGIN}
    achados = {(r.caminho, sha256((_REPO / r.caminho).read_bytes())) for r in recusas}
    assert achados == pendentes
    assert len(pendentes) == 9
