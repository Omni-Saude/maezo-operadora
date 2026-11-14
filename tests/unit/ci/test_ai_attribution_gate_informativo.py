"""O `ai-attribution-gate` e' informativo (DL-0166): reporta trailer de IA, nunca reprova.

Duas camadas:

1. **Forma**: o job `ai-attribution-gate` continua existindo com esse nome (contexto obrigatorio do
   ruleset, ADR-0069 decisao 2) e o seu script nao tem `exit 1` nem `::error::`.
2. **Execucao**: o script `run:` do passo, extraido do proprio `ci.yml`, roda em bash contra um
   repositorio git temporario com e sem commit assinado por IA; sai 0 nos dois casos, emite
   `::warning::` so' quando ha trailer e escreve no `GITHUB_STEP_SUMMARY`.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
CI_YML = REPO_ROOT / ".github" / "workflows" / "ci.yml"
JOB = "ai-attribution-gate"


def _job() -> dict[str, Any]:
    doc = yaml.safe_load(CI_YML.read_text(encoding="utf-8"))
    job: dict[str, Any] = doc["jobs"][JOB]
    return job


def _script() -> str:
    runs = [step["run"] for step in _job()["steps"] if "run" in step]
    assert len(runs) == 1, f"{JOB}: esperado um passo com run:, achei {len(runs)}"
    return str(runs[0])


def test_job_mantem_nome_do_contexto_obrigatorio() -> None:
    assert _job()["name"] == JOB


def test_script_nao_reprova() -> None:
    script = _script()
    assert "exit 1" not in script
    assert "::error::" not in script
    assert "::warning::" in script
    assert "GITHUB_STEP_SUMMARY" in script


def _git(repo: Path, *args: str) -> None:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.invalid",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.invalid",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
    }
    subprocess.run(["git", *args], cwd=repo, env=env, check=True, capture_output=True)


def _rodar(tmp_path: Path, mensagem: str) -> tuple[subprocess.CompletedProcess[str], str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "base")
    _git(repo, "commit", "-q", "--allow-empty", "-m", mensagem)
    summary = tmp_path / "summary.md"
    env = {"PATH": os.environ["PATH"], "BASE_REF": "", "GITHUB_STEP_SUMMARY": str(summary)}
    bash = shutil.which("bash")
    assert bash is not None
    proc = subprocess.run(
        [bash, "-c", _script()], cwd=repo, env=env, capture_output=True, text=True, check=False
    )
    return proc, summary.read_text(encoding="utf-8") if summary.exists() else ""


@pytest.mark.skipif(shutil.which("git") is None, reason="git ausente")
def test_commit_assinado_por_ia_avisa_e_sai_zero(tmp_path: Path) -> None:
    trailer = "Co-" + "Authored-By: Claude <noreply@anthropic.com>"
    proc, summary = _rodar(tmp_path, f"feat: x\n\n{trailer}\n")
    assert proc.returncode == 0, proc.stderr
    assert "::warning::1 trailer(s)" in proc.stdout
    assert "DL-0166" in summary


@pytest.mark.skipif(shutil.which("git") is None, reason="git ausente")
def test_commit_sem_trailer_sai_zero_sem_aviso(tmp_path: Path) -> None:
    proc, summary = _rodar(tmp_path, "feat: x\n\nCo-" + "authored-by: Pessoa <p@example.invalid>\n")
    assert proc.returncode == 0, proc.stderr
    assert "::warning::" not in proc.stdout
    assert "nenhum trailer de IA" in proc.stdout
    assert "nenhum trailer de IA" in summary
