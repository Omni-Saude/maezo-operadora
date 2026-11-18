"""`scripts/ci/arvore_verificada.py` (L1a): o push em main so' deixa de repetir a suite quando a arvore
e' a MESMA que uma run verde do PR verificou. Toda outra situacao cai em "roda tudo" — e nunca vira check
vermelho."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml
from scripts.ci import arvore_verificada as av

_RAIZ = Path(__file__).resolve().parents[3]
_CI = _RAIZ / ".github" / "workflows" / "ci.yml"
_SEC = _RAIZ / ".github" / "workflows" / "security.yml"
_ALARME = _RAIZ / ".github" / "workflows" / "red-main-alarm.yml"

SHA = "a" * 40
HEAD = "b" * 40
ARVORE = "c" * 40
OUTRA = "d" * 40


def _pr(**extra: Any) -> dict[str, Any]:
    base = {
        "number": 7,
        "merged_at": "2026-11-18T10:00:00Z",
        "merge_commit_sha": SHA,
        "base": {"ref": "main"},
    }
    base["head"] = {"sha": HEAD}
    base.update(extra)
    return base


def _run(run_id: int, conclusion: str | None = "success", status: str = "completed") -> dict[str, Any]:
    return {"id": run_id, "status": status, "conclusion": conclusion}


def _decidir(
    prs: list[dict[str, Any]] | None = None,
    runs: list[dict[str, Any]] | None = None,
    arvores: dict[int, str | None] | None = None,
    arvore_push: str = ARVORE,
) -> av.Decisao:
    return av.decidir(
        sha_push=SHA,
        arvore_push=arvore_push,
        prs=[_pr()] if prs is None else prs,
        runs_do_head=lambda head: (runs if runs is not None else [_run(10)]) if head == HEAD else [],
        arvore_da_run=lambda run_id: (arvores if arvores is not None else {10: ARVORE}).get(run_id),
    )


def test_identical_tree_of_the_latest_green_run_is_verified() -> None:
    d = _decidir()
    assert d.verificado is True
    assert d.run_id == 10
    assert d.pr == 7


@pytest.mark.parametrize(
    ("caso", "kwargs"),
    [
        ("PR nao achado", {"prs": []}),
        ("dois PRs mesclados com o mesmo merge commit", {"prs": [_pr(), _pr(number=8)]}),
        ("PR nao mesclado", {"prs": [_pr(merged_at=None)]}),
        ("PR mesclado em outra base", {"prs": [_pr(base={"ref": "release"})]}),
        ("merge_commit_sha de outro commit", {"prs": [_pr(merge_commit_sha=OUTRA)]}),
        ("PR sem head", {"prs": [_pr(head={})]}),
        ("nenhuma run do head", {"runs": []}),
        ("run mais recente falhou", {"runs": [_run(10), _run(11, "failure")]}),
        ("run mais recente cancelada", {"runs": [_run(10), _run(11, "cancelled")]}),
        ("run mais recente em andamento", {"runs": [_run(10), _run(11, None, "in_progress")]}),
        ("artefato ausente", {"arvores": {}}),
        ("artefato com lixo", {"arvores": {10: "nao-e-um-hash"}}),
        ("arvore diferente (a main andou)", {"arvores": {10: OUTRA}}),
        ("arvore do push ilegivel", {"arvore_push": ""}),
    ],
)
def test_every_doubt_falls_back_to_running_everything(caso: str, kwargs: dict[str, Any]) -> None:
    d = _decidir(**kwargs)
    assert d.verificado is False, caso
    assert d.motivo


def test_an_older_green_run_never_vouches_for_a_newer_red_one() -> None:
    # A verde antiga tem a arvore igual; a mais recente (que e' a que vale) falhou.
    d = _decidir(runs=[_run(10), _run(11, "failure")], arvores={10: ARVORE, 11: ARVORE})
    assert d.verificado is False
    assert d.run_id == 11


def test_query_errors_become_run_everything_never_an_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    monkeypatch.setenv("GITHUB_SHA", SHA)
    monkeypatch.setattr(av, "_arvore_local", lambda: ARVORE)

    def explode(caminho: str) -> Any:
        raise subprocess.CalledProcessError(1, ["gh", "api", caminho])

    monkeypatch.setattr(av, "_gh_json", explode)
    d = av._decidir_no_runner("ci.yml", "arvore-testada-ci")
    assert d.verificado is False
    assert "erro ao consultar" in d.motivo


def test_decide_mode_always_writes_false_and_exits_zero_on_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    saida = tmp_path / "out"
    monkeypatch.setenv("GITHUB_OUTPUT", str(saida))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "summary"))
    monkeypatch.setattr(av, "_decidir_no_runner", lambda wf, art: av.Decisao(False, "teste"))
    assert av.main(["--decidir", "--workflow", "ci.yml", "--artefato", "x"]) == 0
    assert "verificado=false" in saida.read_text(encoding="utf-8")


def test_record_mode_writes_the_tree_of_head(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false"]
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "f").write_text("x", encoding="utf-8")
    subprocess.run([*git, "-C", str(repo), "add", "f"], check=True)
    subprocess.run([*git, "-C", str(repo), "commit", "-qm", "c"], check=True)
    esperado = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD^{tree}"], check=True, capture_output=True, text=True
    ).stdout.strip()
    monkeypatch.chdir(repo)
    assert av.main(["--gravar", str(tmp_path / "out")]) == 0
    assert (tmp_path / "out" / av.ARQUIVO_ARVORE).read_text(encoding="ascii").strip() == esperado


# ------------------------------------------------------------------------------ cercas sobre os workflows

_PESADOS_DIRETOS = {"unit", "portal-web", "validate-terraform", "java-plugin", "validate-helm"}


def _wf(caminho: Path) -> dict[str, Any]:
    return yaml.safe_load(caminho.read_text(encoding="utf-8"))


def test_ci_decision_output_is_true_only_on_push() -> None:
    trilha = _wf(_CI)["jobs"]["trilha"]
    expr = trilha["outputs"]["verificado"]
    assert "github.event_name == 'push'" in expr
    passo = next(s for s in trilha["steps"] if s.get("id") == "arvore")
    assert "github.event_name == 'push'" in passo["if"]
    assert "refs/heads/main" in passo["if"]


def test_only_the_heavy_lanes_are_gated_and_never_on_pull_request() -> None:
    jobs = _wf(_CI)["jobs"]
    gated = {nome for nome, job in jobs.items() if "verificado" in str(job.get("if", ""))}
    assert gated == _PESADOS_DIRETOS
    # O que depende delas so' pula por consequencia — sem `always()`/`!cancelled()`, que as faria
    # rodar sozinhas.
    for nome in ("quality", "release-floor", "chaos", "fhir-sync-simulator", "evals"):
        cond = str(jobs[nome].get("if", ""))
        assert "always()" not in cond and "cancelled()" not in cond, nome


def test_required_contexts_are_untouched_on_pull_request() -> None:
    jobs = _wf(_CI)["jobs"]
    for nome in ("artifact-validation", "ai-attribution-gate"):
        assert "verificado" not in str(jobs[nome].get("if", "")), nome
    portao = jobs["portao"]["steps"][0]["run"]
    # A aceitacao da suite pulada exige PUSH + verificado + run verificadora; o resto do portao e' o de
    # sempre.
    assert '[ "${EVENTO}" = "push" ] && [ "${VERIFICADO}" = "true" ]' in portao
    assert '[ -n "${RUN_VERIFICADORA}" ]' in portao
    sec = _wf(_SEC)["jobs"]
    for nome in ("gitleaks", "osv-scan", "trivy-iac", "dependency-review"):
        assert "arvore" not in str(sec[nome]), nome


def test_codeql_steps_after_the_decision_are_gated_and_the_decision_only_runs_on_push() -> None:
    sec = _wf(_SEC)["jobs"]
    for nome in ("codeql", "codeql-java"):
        passos = sec[nome]["steps"]
        i = next(k for k, s in enumerate(passos) if s.get("id") == "arvore")
        assert "github.event_name == 'push'" in passos[i]["if"]
        depois = [s for s in passos[i + 1 :] if "SCAN NAO REPETIDO" not in str(s.get("name", ""))]
        assert depois and all(s.get("if") == "steps.arvore.outputs.verificado != 'true'" for s in depois), (
            nome
        )


def test_red_main_alarm_reads_a_verified_push_run_as_green() -> None:
    """Prova da leitura do alarme: ele so' acusa vermelho por `failure`/`timed_out`. Num push verificado
    os jobs pesados terminam `skipped` e o `portao` sai 0, entao a run conclui `success` — verde no tip,
    como antes."""
    script = "\n".join(s.get("run", "") for s in _wf(_ALARME)["jobs"]["red-main-alarm"]["steps"])
    vermelhos = set(re.findall(r'\.conclusion == "(\w+)"', script))
    assert vermelhos == {"failure", "timed_out"}
    portao = _wf(_CI)["jobs"]["portao"]["steps"][0]["run"]
    ramo = portao.split('[ "${VERIFICADO}" = "true" ]', 1)[1].split("\nfi\n", 1)[0]
    assert "exit 0" in ramo
