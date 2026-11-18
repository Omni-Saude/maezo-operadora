"""Migracao PARCIAL para arm64 (custo de Actions, 18/11/2026): cerca para ninguem migrar job por engano.

Decisao da ROOT apos a prova #1331: seis jobs do `ci.yml` rodam em `ubuntu-24.04-arm` no PR e no push, e
em x64 no noturno (`schedule`), que cobre a arquitetura do deploy (task definitions `X86_64` em
`deploy/aws-ecs/envs/*`) todo dia, no canario que o `red-main-alarm.yml` le. A integracao, o `java-plugin` e
todo o `security.yml` ficam em x64. Mudar isso exige decisao nova, nao um ajuste de linha."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

_WF = Path(__file__).resolve().parents[3] / ".github" / "workflows"
_EXPR_ARM_PARCIAL = "${{ github.event_name == 'schedule' && 'ubuntu-latest' || 'ubuntu-24.04-arm' }}"
_JOBS_ARM = {"unit", "quality", "release-floor", "portal-web", "validate-terraform", "validate-helm"}
_JOBS_X64_FIXOS = {"integration", "java-plugin"}
# arm64 ja' existente antes desta migracao, fora do `ci.yml` (nao faz parte da decisao de 18/11).
_ARM_PREEXISTENTE = {("n2-lane.yml", "lane")}


def _jobs(arquivo: str) -> dict[str, Any]:
    jobs: dict[str, Any] = yaml.safe_load((_WF / arquivo).read_text(encoding="utf-8"))["jobs"]
    return jobs


def test_exactly_the_six_decided_jobs_use_the_event_conditional_runner() -> None:
    jobs = _jobs("ci.yml")
    com_expr = {nome for nome, job in jobs.items() if job.get("runs-on") == _EXPR_ARM_PARCIAL}
    assert com_expr == _JOBS_ARM


def test_nightly_keeps_x64_and_pull_request_and_push_get_arm64() -> None:
    # A expressao so' tem dois ramos: o noturno (x64, matriz inteira) e todo o resto (arm64).
    assert "github.event_name == 'schedule' && 'ubuntu-latest'" in _EXPR_ARM_PARCIAL
    assert _EXPR_ARM_PARCIAL.rstrip(" }").endswith("|| 'ubuntu-24.04-arm'")


def test_no_other_job_in_any_workflow_mentions_an_arm_runner() -> None:
    for caminho in sorted(_WF.glob("*.y*ml")):
        for nome, job in (yaml.safe_load(caminho.read_text(encoding="utf-8")).get("jobs") or {}).items():
            if (caminho.name == "ci.yml" and nome in _JOBS_ARM) or (caminho.name, nome) in _ARM_PREEXISTENTE:
                continue
            assert "arm" not in str(job.get("runs-on", "")), f"{caminho.name}:{nome}"


def test_integration_java_plugin_and_security_stay_on_ubuntu_latest() -> None:
    ci = _jobs("ci.yml")
    for nome in _JOBS_X64_FIXOS:
        assert ci[nome]["runs-on"] == "ubuntu-latest", nome
    for nome, job in _jobs("security.yml").items():
        assert job["runs-on"] == "ubuntu-latest", f"security.yml:{nome}"
