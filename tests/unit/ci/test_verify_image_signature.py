"""Portão SBOM/assinatura (portal.md, entrada `image_digest`): prova de BLOQUEIO fail-closed.

O portão é `scripts/ci/verify_image_signature.sh` — o MESMO script que o job
`verificar` de `.github/workflows/supply-chain.yml` roda em pull_request sobre o
digest que `portal.auto.tfvars` declara. Até este arquivo existir, o bloqueio
só estava implícito: nenhum teste falhava se o portão deixasse passar artefato
sem SBOM, sem assinatura, assinado por outra chave ou de outro digest.

COMO ESTE TESTE INJETA AS VIOLAÇÕES. O script roda de verdade (subprocess); o
`cosign` é substituído por um replayer que devolve transcripts REAIS, capturados
em 2026-10-27 com o cosign v3.1.4 contra um `registry:2` local — verbatim, com
proveniência e sha256 em `tests/fixtures/cosign_transcripts/PROVENANCE.md`. O
que está sob teste é a DECISÃO do guarda (a leitura do marcador e o não-zero),
não o cosign; fingir que o cosign falhou seria esconder o portão, não testá-lo.

O CANÁRIO (primeiro teste) prova o contrapeso: com assinatura + SBOM verdes a
partir do MESMO cenário, o guarda aceita — então cada recusa abaixo falha SÓ
pela violação injetada.
"""

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts/ci/verify_image_signature.sh"
TRANSCRIPTS = ROOT / "tests/fixtures/cosign_transcripts"

APP_DIGEST = "sha256:" + "365a051f12e05767b598e643676f14a450fb678a75ccf2beb0052c95d5c73b83"
OTHER_DIGEST = "sha256:" + "ef0e6d90f53490bdc90c440aa6cacf8da8d2509caf3f67b858263dbcdea2f685"
APP_REF = f"127.0.0.1:5000/w7/red@{APP_DIGEST}"
OTHER_REF = f"127.0.0.1:5000/w7/other@{OTHER_DIGEST}"

pytestmark = pytest.mark.skipif(
    sys.platform == "win32" or shutil.which("bash") is None, reason="POSIX bash fixture"
)

COSIGN_REPLAYER = """#!/bin/sh
# Replayer: devolve o transcript REAL do caso (W7_SCENARIO_DIR), verbatim.
mode=sig
for a in "$@"; do
  case "$a" in
    verify-attestation) mode=att ;;
  esac
done
printf '%s\\n' "$*" >> "$W7_SHIM_LOG"
dir="$W7_SCENARIO_DIR"
[ -f "$dir/$mode.out" ] && cat "$dir/$mode.out"
cat "$dir/$mode.err" >&2
exit "$(cat "$dir/$mode.rc")"
"""

# `timeout` é GNU coreutils: existe no runner do CI (ubuntu) e falta no macOS.
TIMEOUT_SHIM = """#!/usr/bin/env python3
\"\"\"Substituto mínimo do `timeout -s INT N cmd...` — só o que o script usa.\"\"\"
import os, signal, subprocess, sys

args = sys.argv[1:]
assert args and args[0] == "-s"
args = args[2:]  # descarta "-s INT"
n = int(args[0])
cmd = args[1:]
fired = []

p = subprocess.Popen(cmd, start_new_session=True)

def _on_alarm(sig, frame):
    fired.append(True)
    os.killpg(os.getpgid(p.pid), signal.SIGINT)

signal.signal(signal.SIGALRM, _on_alarm)
signal.alarm(n)
rc = p.wait()
signal.alarm(0)
sys.exit(124 if fired else rc)
"""


def _build_bin(tmp_path: Path, cosign_body: str | None) -> Path:
    fake = tmp_path / "bin"
    fake.mkdir()
    if cosign_body is not None:
        (fake / "cosign").write_text(cosign_body)
        (fake / "cosign").chmod(0o755)
    if shutil.which("timeout") is None:
        (fake / "timeout").write_text(TIMEOUT_SHIM)
        (fake / "timeout").chmod(0o755)
    return fake


def _run(
    tmp_path: Path,
    scenario: str | Path | None,
    image_ref: str | None = None,
    cosign_body: str | None = COSIGN_REPLAYER,
    timeout_s: str | None = None,
) -> tuple[int, str, Path]:
    fake = _build_bin(tmp_path, cosign_body)
    shim_log = tmp_path / "cosign-shim.log"
    shim_log.write_text("")
    env = dict(os.environ)
    env["PATH"] = f"{fake}:{env['PATH']}"
    env["W7_SHIM_LOG"] = str(shim_log)
    env["KMS_KEY_URI"] = "awskms:///alias/maezo-operadora-dev-image-signing"
    if scenario is not None:
        scenario_dir = scenario if isinstance(scenario, Path) else TRANSCRIPTS / scenario
        env["W7_SCENARIO_DIR"] = str(scenario_dir)
    if timeout_s is not None:
        env["TIMEOUT_S"] = timeout_s
    argv = ["bash", str(SCRIPT)]
    if image_ref is not None:
        argv.append(image_ref)
    result = subprocess.run(
        argv, env=env, capture_output=True, text=True, timeout=120, check=False
    )
    return result.returncode, result.stdout + result.stderr, shim_log


def _inline_scenario(
    tmp_path: Path, sig_rc: int, att_rc: int, sig_err: str = "", att_err: str = ""
) -> Path:
    case = tmp_path / "inline"
    case.mkdir()
    (case / "sig.rc").write_text(f"{sig_rc}\n")
    (case / "att.rc").write_text(f"{att_rc}\n")
    (case / "sig.err").write_text(sig_err)
    (case / "att.err").write_text(att_err)
    (case / "sig.out").write_text("")
    (case / "att.out").write_text("")
    return case


def test_artifact_with_signature_and_sbom_is_accepted(tmp_path: Path) -> None:
    """Canário do caminho feliz: com as DUAS provas verdes, o portão abre."""
    rc, log, shim_log = _run(tmp_path, "ok", APP_REF)
    assert rc == 0, log
    assert f"VERIFICADO: {APP_REF}" in log
    calls = [line for line in shim_log.read_text().splitlines() if line]
    assert len(calls) == 2  # cosign verify + cosign verify-attestation
    # e ambas as pernas verificaram O digest pedido (não um primo dele)
    assert all(APP_REF in call for call in calls)


def test_unsigned_image_is_blocked(tmp_path: Path) -> None:
    """VIOLAÇÃO: artefato SEM assinatura — o portão recusa com não-zero."""
    rc, log, _ = _run(tmp_path, "no_signature", APP_REF)
    assert rc == 1, log
    assert "cosign verify (assinatura) REPROVOU" in log
    assert "no signatures found" in log
    assert "VERIFICADO" not in log


def test_image_without_sbom_is_blocked(tmp_path: Path) -> None:
    """VIOLAÇÃO: assinatura ok, SBOM ausente — a perna de atestação reprova sozinha."""
    rc, log, _ = _run(tmp_path, "no_sbom", APP_REF)
    assert rc == 1, log
    # a assinatura passou; é o SBOM que bloqueia
    assert "-> cosign verify (assinatura): OK" in log
    assert "cosign verify-attestation (SBOM SPDX) REPROVOU" in log
    assert "no matching attestations" in log
    assert "VERIFICADO" not in log


def test_wrong_signing_key_is_blocked(tmp_path: Path) -> None:
    """VIOLAÇÃO: assinatura existe, mas é de OUTRA chave — recusa."""
    rc, log, _ = _run(tmp_path, "wrong_key", APP_REF)
    assert rc == 1, log
    assert "cosign verify (assinatura) REPROVOU" in log
    assert "invalid signature when validating ASN.1 encoded signature" in log
    assert "VERIFICADO" not in log


def test_divergent_digest_is_blocked(tmp_path: Path) -> None:
    """VIOLAÇÃO: digest divergente — outro digest, mesmo registry com artefato assinado.

    O digest assinado (APP) e o que o portal consome (OTHER) moram no mesmo
    registry; a assinatura do APP não vaza para o OTHER: o guarda verifica o
    digest PEDIDO e não encontra prova dele.
    """
    rc, log, shim_log = _run(tmp_path, "divergent_digest", OTHER_REF)
    assert rc == 1, log
    assert "no signatures found" in log
    assert OTHER_REF in log  # o que foi verificado é o digest declarado
    assert APP_REF not in log  # a prova do outro digest não conta
    assert "VERIFICADO" not in log


def test_missing_cosign_fails_closed(tmp_path: Path) -> None:
    """VIOLAÇÃO: a ferramenta de verificação não está no PATH — recusa, nunca passa."""
    rc, log, _ = _run(tmp_path, "ok", APP_REF, cosign_body=None)
    assert rc == 1, log
    assert "REPROVOU" in log
    assert "VERIFICADO" not in log


def test_zero_exit_without_cosign_marker_is_refused(tmp_path: Path) -> None:
    """O veredito é o MARCADOR, não o código de saída.

    Injeção sintética (contrato do guarda, não transcript do cosign): um cosign
    que encerra 0 sem imprimir o bloco de verificação — a resposta para
    "e se a saída mudar de formato?" tem de ser bloqueio.
    """
    case = _inline_scenario(tmp_path, 0, 0)
    rc, log, _ = _run(tmp_path, case, APP_REF)
    assert rc == 1, log
    assert "REPROVOU" in log
    assert "VERIFICADO" not in log


def test_hanging_cosign_is_killed_and_refused(tmp_path: Path) -> None:
    """O cosign que verifica e nunca encerra (bug conhecido, runbook §3) reprova.

    Sem marcador até o TIMEOUT_S, o `timeout -s INT` mata e o veredito segue
    sendo REPROVA — o hang não vira verde nem falso-verde.
    """
    started = time.monotonic()
    rc, log, _ = _run(
        tmp_path, None, APP_REF, cosign_body="#!/bin/sh\nsleep 60\n", timeout_s="2"
    )
    elapsed = time.monotonic() - started
    assert rc == 1, log
    assert "REPROVOU" in log
    assert elapsed < 60  # o teste é acotado pelo TIMEOUT_S, não pelo sleep


def test_tag_is_refused_before_any_cosign_call(tmp_path: Path) -> None:
    """Verificar por tag é verificar alvo móvel — recusa ANTES de chamar o cosign."""
    rc, log, shim_log = _run(tmp_path, "ok", "127.0.0.1:5000/w7/red:v1")
    assert rc == 2, log
    assert "nunca por tag" in log
    assert shim_log.read_text() == ""  # cosign sequer foi invocado


def test_missing_argument_prints_usage(tmp_path: Path) -> None:
    rc, log, _ = _run(tmp_path, "ok", image_ref=None)
    assert rc == 2, log
    assert "uso:" in log
