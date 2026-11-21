"""Migracao PARCIAL para arm64 (custo de Actions, 18/11/2026): cerca para ninguem migrar job por engano.

Decisao da ROOT apos a prova #1331: seis jobs do `ci.yml` rodam em `ubuntu-24.04-arm` no PR e no push, e
em x64 no noturno (`schedule`), que cobre a arquitetura do deploy (task definitions `X86_64` em
`deploy/aws-ecs/envs/*`) todo dia, no canario que o `red-main-alarm.yml` le. A integracao, o `java-plugin` e
todo o `security.yml` ficam em x64. Mudar isso exige decisao nova, nao um ajuste de linha."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
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


_TERNARIO = re.compile(r"^\$\{\{ github\.event_name == '(\w+)' && '([^']+)' \|\| '([^']+)' \}\}$")


def _avaliar_runs_on(valor: str, evento: str) -> str:
    """Avalia o `runs-on` como o Actions faria para `evento`: literal, ou o ternario `a && 'x' || 'y'`.
    Qualquer outra expressao e' recusada (nao da' para provar o runner sem rodar)."""
    if "${{" not in valor:
        return valor
    m = _TERNARIO.match(valor)
    assert m, f"runs-on com expressao nao suportada pela cerca: {valor!r}"
    alvo, se_sim, se_nao = m.groups()
    return se_sim if evento == alvo else se_nao


@pytest.mark.parametrize(
    ("evento", "esperado"),
    [
        ("schedule", "ubuntu-latest"),
        ("pull_request", "ubuntu-24.04-arm"),
        ("push", "ubuntu-24.04-arm"),
        ("workflow_dispatch", "ubuntu-24.04-arm"),
    ],
)
def test_the_runner_each_migrated_job_gets_per_event(evento: str, esperado: str) -> None:
    # Avalia o valor LIDO do ci.yml: o noturno (matriz inteira) cai em x64; todo o resto, em arm64.
    jobs = _jobs("ci.yml")
    for nome in _JOBS_ARM:
        assert _avaliar_runs_on(jobs[nome]["runs-on"], evento) == esperado, (nome, evento)
    for nome in _JOBS_X64_FIXOS:
        assert _avaliar_runs_on(jobs[nome]["runs-on"], evento) == "ubuntu-latest", (nome, evento)


def test_no_indirect_runner_anywhere() -> None:
    # `matrix.os`, `vars.*`, `inputs.*` ou lista/rotulo de grupo esconderiam o runner da cerca.
    for caminho in sorted(_WF.glob("*.y*ml")):
        for nome, job in (yaml.safe_load(caminho.read_text(encoding="utf-8")).get("jobs") or {}).items():
            if "uses" in job and "runs-on" not in job:
                continue  # job que chama workflow reutilizavel: o runner vem do arquivo chamado
            valor = job.get("runs-on")
            assert isinstance(valor, str), f"{caminho.name}:{nome}: runs-on nao e' um rotulo unico"
            permitido = caminho.name == "ci.yml" and nome in _JOBS_ARM and valor == _EXPR_ARM_PARCIAL
            assert "${{" not in valor or permitido, f"{caminho.name}:{nome}: runs-on indireto {valor!r}"
            for evento in ("schedule", "pull_request", "push"):
                _avaliar_runs_on(valor, evento)


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
