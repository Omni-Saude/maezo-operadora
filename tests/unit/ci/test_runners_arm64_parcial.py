"""Migracao PARCIAL para arm64 (custo de Actions, 18/11/2026): cerca para ninguem migrar job por engano.

Decisao da ROOT apos a prova #1331: seis jobs do `ci.yml` rodam em `ubuntu-24.04-arm` no PR e no push, e
em x64 no noturno (`schedule`), que cobre a arquitetura do deploy (task definitions `X86_64` em
`deploy/aws-ecs/envs/*`) todo dia, no canario que o `red-main-alarm.yml` le. A integracao, o `java-plugin` e
todo o `security.yml` ficam em x64. Mudar isso exige decisao nova, nao um ajuste de linha."""

from __future__ import annotations

import copy
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
_EVENTOS = ("schedule", "pull_request", "push", "workflow_dispatch")


def _avaliar_runs_on(valor: object, evento: str) -> str | None:
    """Avalia o `runs-on` como o Actions faria para `evento`: rotulo literal, ou o ternario `a && 'x' || 'y'`.
    Qualquer outra forma (expressao com matrix/vars/inputs, lista, mapa) devolve None: nao da' para provar
    o runner sem rodar."""
    if not isinstance(valor, str):
        return None
    if "${{" not in valor:
        return valor
    m = _TERNARIO.match(valor)
    if not m:
        return None
    alvo, se_sim, se_nao = m.groups()
    return se_sim if evento == alvo else se_nao


def _violacoes_por_evento(ci_jobs: dict[str, Any]) -> list[str]:
    """O noturno (matriz inteira) cai em x64; todo o resto, em arm64 — nos 6 jobs; os fixos, sempre x64."""
    erros = []
    for evento in _EVENTOS:
        esperado_arm = "ubuntu-latest" if evento == "schedule" else "ubuntu-24.04-arm"
        for nome in sorted(_JOBS_ARM):
            obtido = _avaliar_runs_on(ci_jobs[nome].get("runs-on"), evento)
            if obtido != esperado_arm:
                erros.append(f"{nome}@{evento}: {obtido!r} != {esperado_arm!r}")
        for nome in sorted(_JOBS_X64_FIXOS):
            obtido = _avaliar_runs_on(ci_jobs[nome].get("runs-on"), evento)
            if obtido != "ubuntu-latest":
                erros.append(f"{nome}@{evento}: {obtido!r} != 'ubuntu-latest'")
    return erros


def _violacoes_indiretas(workflows: dict[str, dict[str, Any]]) -> list[str]:
    """`matrix.os`, `vars.*`, `inputs.*`, lista ou mapa esconderiam o runner da cerca."""
    erros = []
    for arquivo, jobs in sorted(workflows.items()):
        for nome, job in jobs.items():
            if "uses" in job and "runs-on" not in job:
                continue  # job que chama workflow reutilizavel: o runner vem do arquivo chamado
            valor = job.get("runs-on")
            permitido = arquivo == "ci.yml" and nome in _JOBS_ARM and valor == _EXPR_ARM_PARCIAL
            if not isinstance(valor, str) or ("${{" in valor and not permitido):
                erros.append(f"{arquivo}:{nome}: runs-on indireto {valor!r}")
            elif any(_avaliar_runs_on(valor, e) is None for e in _EVENTOS):
                erros.append(f"{arquivo}:{nome}: runs-on nao avaliavel {valor!r}")
    return erros


def _todos_os_workflows() -> dict[str, dict[str, Any]]:
    return {
        c.name: yaml.safe_load(c.read_text(encoding="utf-8")).get("jobs") or {}
        for c in sorted(_WF.glob("*.y*ml"))
    }


def test_the_runner_each_migrated_job_gets_per_event() -> None:
    # Avalia o valor LIDO do ci.yml para cada evento, nao a constante do teste.
    assert _violacoes_por_evento(_jobs("ci.yml")) == []


def test_no_indirect_runner_anywhere() -> None:
    assert _violacoes_indiretas(_todos_os_workflows()) == []


@pytest.mark.parametrize(
    ("job", "valor", "cerca"),
    [
        ("java-plugin", "${{ matrix.os }}", "indireto"),
        ("integration", "${{ vars.RUNNER }}", "indireto"),
        ("java-plugin", ["self-hosted", "linux"], "indireto"),
        ("integration", "${{ inputs.runner || 'ubuntu-latest' }}", "indireto"),
        ("unit", "${{ github.event_name == 'schedule' && 'ubuntu-24.04-arm' || 'ubuntu-latest' }}", "evento"),
        ("quality", "ubuntu-24.04-arm", "evento"),
        ("java-plugin", "ubuntu-24.04-arm", "evento"),
    ],
)
def test_fences_catch_mutated_workflows(job: str, valor: object, cerca: str) -> None:
    # Provas negativas como teste: a mesma cerca, sobre o ci.yml real com UM job mutado.
    workflows = copy.deepcopy(_todos_os_workflows())
    workflows["ci.yml"][job]["runs-on"] = valor
    erros = (
        _violacoes_indiretas(workflows) if cerca == "indireto" else _violacoes_por_evento(workflows["ci.yml"])
    )
    assert any(job in e for e in erros), erros


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
