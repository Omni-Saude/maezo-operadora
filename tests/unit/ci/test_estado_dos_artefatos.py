"""`estado_dos_artefatos.sh`: o que decide o que FALTA publicar não pode mentir.

O script responde se as tags `sha256-<digest>.sig`/`.att` já existem no ECR; as
saídas `falta_assinatura`/`falta_atestacao` governam os passos de assinar/atesta
de `.github/workflows/supply-chain.yml`. Duas formas de mentir estão cobertas
aqui como vermelho: dizer "já está publicado" quando não está (prefixo de
digest errado é o caso `divergent_digest` do portão) e engolir erro da AWS —
credencial vencida virando "lista vazia" faria o job tentar reescrever tag
imutável (`TAG_INVALID`, medido no run 35666402636 citado no próprio script).

O `aws` é um doble que só sabe listar tags (mesmo padrão do
`test_resolve_image_digest.py`); o script roda inteiro, de verdade.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts/ci/estado_dos_artefatos.sh"
APP = "a" * 64
OTHER = "e" * 64

pytestmark = pytest.mark.skipif(
    sys.platform == "win32" or shutil.which("bash") is None, reason="POSIX bash fixture"
)


def _run(tmp_path: Path, tags: list[str], aws_fails: bool = False) -> tuple[int, dict[str, str], str]:
    fake = tmp_path / "bin"
    fake.mkdir()
    if aws_fails:
        body = (
            '#!/bin/sh\necho "ExpiredToken: the security token included in the request is invalid" >&2\n'
            "exit 254\n"
        )
    else:
        body = (
            '#!/bin/sh\nprintf \'%s\\t\\n\' $(cat "$KNOWN") | tr \'\\t\' \'\\n\'\nexit 0\n'
        )
    (fake / "aws").write_text(body)
    (fake / "aws").chmod(0o755)
    (tmp_path / "known").write_text("".join(t + "\n" for t in tags))
    out = tmp_path / "github_output"
    out.write_text("")
    result = subprocess.run(
        ["bash", str(SCRIPT)],
        env={
            "PATH": f"{fake}:{os.environ['PATH']}",
            "KNOWN": str(tmp_path / "known"),
            "AWS_REGION": "sa-east-1",
            "ECR_REPOSITORY": "amh/maezo-operadora",
            "DIGEST_HEX": APP,
            "GITHUB_OUTPUT": str(out),
        },
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    outputs = dict(
        line.split("=", 1) for line in out.read_text().splitlines() if "=" in line
    )
    return result.returncode, outputs, result.stdout + result.stderr


def test_signed_and_attested_reports_nothing_missing(tmp_path: Path) -> None:
    """Canário: `.sig` e `.att` do digest presentes → nada a publicar."""
    rc, out, log = _run(tmp_path, [f"sha256-{APP}.sig", f"sha256-{APP}.att"])
    assert rc == 0, log
    assert out["falta_assinatura"] == "false"
    assert out["falta_atestacao"] == "false"


def test_signature_without_sbom_reports_missing_attestation(tmp_path: Path) -> None:
    """VIOLAÇÃO: só `.sig` — o SBOM ausente é REPORTADO, nunca assumido publicado."""
    rc, out, log = _run(tmp_path, [f"sha256-{APP}.sig"])
    assert rc == 0, log
    assert out["falta_assinatura"] == "false"
    assert out["falta_atestacao"] == "true"


def test_unsigned_digest_reports_everything_missing(tmp_path: Path) -> None:
    """VIOLAÇÃO: nenhum artefato do digest — as duas faltas são reportadas."""
    rc, out, log = _run(tmp_path, [])
    assert rc == 0, log
    assert out["falta_assinatura"] == "true"
    assert out["falta_atestacao"] == "true"
    assert "(nenhum)" in log  # e o bloco tags_existentes sai vazio


def test_another_digests_artifacts_never_count_as_ours(tmp_path: Path) -> None:
    """VIOLAÇÃO: digest divergente — a prova do OUTRO digest não zera a minha falta.

    O registry pode ter `sha256-<outro>.sig`/`.att` completos: o prefixo é
    `sha256-<DIGEST_HEX>`, e assinatura de vizinho não é assinatura.
    """
    rc, out, log = _run(
        tmp_path, [f"sha256-{OTHER}.sig", f"sha256-{OTHER}.att"]
    )
    assert rc == 0, log
    assert out["falta_assinatura"] == "true"
    assert out["falta_atestacao"] == "true"
    # e as tags do outro digest nem entram na lista: o escopo é o prefixo do digest
    assert OTHER not in log


def test_aws_failure_fails_closed_instead_of_empty_list(tmp_path: Path) -> None:
    """VIOLAÇÃO: a AWS falha (credencial vencida, rc 254) — exit 1, nunca 'nada'."""
    rc, out, log = _run(tmp_path, [], aws_fails=True)
    assert rc == 1, log
    assert "Nao foi possivel LER o ECR" in log
    assert "falta_assinatura" not in out  # nenhuma saída é emitida sob erro
    assert "falta_atestacao" not in out
