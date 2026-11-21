"""`evals-nightly` (ci.yml) partido em Tier A (bloqueante) e Tier B (ao vivo, com resumo proprio).

Cercas pedidas pela ROOT (21/11/2026):
* a selecao do passo A + passo B e' EXATAMENTE a do alvo `make evals` lido do Makefile (se o Makefile mudar
  a selecao, isto falha); as duas sao disjuntas;
* o Tier B nunca e' "verde por pular": o resumo diz se rodou e quantos casos; erro de coleta ou zero casos
  falham nos dois modos; reprovacao so' bloqueia com TIER_B_BLOQUEANTE=true;
* o job `evals` de PR continua intocado (`make evals`, sem a chave nova)."""

from __future__ import annotations

import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml
from scripts.ci import tier_b_resumo as tb

_RAIZ = Path(__file__).resolve().parents[3]
_CI = _RAIZ / ".github" / "workflows" / "ci.yml"
_MAKEFILE = _RAIZ / "Makefile"


def _jobs() -> dict[str, Any]:
    jobs: dict[str, Any] = yaml.safe_load(_CI.read_text(encoding="utf-8"))["jobs"]
    return jobs


def _passo(nome_prefixo: str) -> dict[str, Any]:
    return next(
        s for s in _jobs()["evals-nightly"]["steps"] if str(s.get("name", "")).startswith(nome_prefixo)
    )


def _pytest_args(script: str) -> tuple[str, str]:
    """(caminho, expressao -m) da linha `python -m pytest` do script do passo."""
    linha = next(x for x in script.splitlines() if "-m pytest" in x)
    tokens = shlex.split(linha.split("||")[0])
    i = tokens.index("pytest")
    caminho = tokens[i + 1]
    return caminho, tokens[tokens.index("-m", i) + 1]


def _selecao_do_make() -> tuple[str, str]:
    alvo = re.search(r"^evals:.*\n\t(.+)$", _MAKEFILE.read_text(encoding="utf-8"), re.M)
    assert alvo, "alvo `evals:` sumiu do Makefile"
    return _pytest_args(alvo.group(1))


def _coletar(caminho: str, marcador: str) -> set[str]:
    r = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            caminho,
            "--collect-only",
            "-q",
            "-m",
            marcador,
            "-p",
            "no:cacheprovider",
        ],
        cwd=_RAIZ,
        capture_output=True,
        text=True,
        check=False,
    )
    assert r.returncode in (0, 5), r.stdout[-2000:] + r.stderr[-2000:]
    return {linha.strip() for linha in r.stdout.splitlines() if "::" in linha}


def test_tier_a_plus_tier_b_selection_equals_make_evals() -> None:
    caminho_make, marcador_make = _selecao_do_make()
    caminho_a, marcador_a = _pytest_args(_passo("Nightly eval run — Tier A")["run"])
    caminho_b, marcador_b = _pytest_args(_passo("Nightly eval run — Tier B")["run"])
    assert caminho_a == caminho_b == caminho_make
    make = _coletar(caminho_make, marcador_make)
    a, b = _coletar(caminho_a, marcador_a), _coletar(caminho_b, marcador_b)
    assert make, "o make evals nao coleta nada: a cerca perderia o sentido"
    assert a.isdisjoint(b), sorted(a & b)[:5]
    assert a | b == make, {"faltam": sorted(make - (a | b))[:5], "sobram": sorted((a | b) - make)[:5]}


def test_tier_b_step_wiring() -> None:
    b = _passo("Nightly eval run — Tier B")
    env = b["env"]
    assert env["MAEZO_ANTHROPIC_API_KEY"] == "${{ secrets.MAEZO_ANTHROPIC_API_KEY }}"
    assert env["TIER_B_BLOQUEANTE"] in ("false", "true")
    assert "continue-on-error" not in b
    assert "scripts/ci/tier_b_resumo.py" in b["run"] and "--junitxml=tier-b-junit.xml" in b["run"]
    a = _passo("Nightly eval run — Tier A")
    assert "continue-on-error" not in a and "MAEZO_ANTHROPIC_API_KEY" not in a.get("env", {})
    upload = _passo("Upload nightly eval results")
    assert "tier-b-junit.xml" in upload["with"]["path"]


def test_pull_request_evals_job_is_untouched() -> None:
    job = _jobs()["evals"]
    texto = yaml.safe_dump(job)
    assert "make evals" in texto
    assert "MAEZO_ANTHROPIC_API_KEY" not in texto and "TIER_B_BLOQUEANTE" not in texto


# ------------------------------------------------------------------------- veredito do script do passo


def _junit(tmp_path: Path, casos: list[str]) -> Path:
    corpo = []
    for i, r in enumerate(casos):
        filho = {"aprovado": "", "pulado": "<skipped/>", "reprovado": "<failure message='abaixo'/>"}[r]
        corpo.append(f'<testcase classname="tests.evals.t" name="caso_{i}">{filho}</testcase>')
    p = tmp_path / "j.xml"
    p.write_text(f'<testsuites><testsuite name="pytest">{"".join(corpo)}</testsuite></testsuites>', "utf-8")
    return p


def _rodar(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    casos: list[str],
    codigo: int,
    *,
    chave: bool,
    bloqueante: str,
) -> tuple[int, str]:
    resumo = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(resumo))
    monkeypatch.setenv("TIER_B_BLOQUEANTE", bloqueante)
    if chave:
        monkeypatch.setenv("MAEZO_ANTHROPIC_API_KEY", "chave-de-teste")
    else:
        monkeypatch.delenv("MAEZO_ANTHROPIC_API_KEY", raising=False)
    junit = _junit(tmp_path, casos) if casos is not None else tmp_path / "ausente.xml"
    saida = tb.main(["--junit", str(junit), "--codigo", str(codigo)])
    return saida, resumo.read_text(encoding="utf-8")


@pytest.mark.parametrize("bloqueante", ["false", "true"])
def test_without_key_says_not_run_and_passes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, bloqueante: str
) -> None:
    saida, resumo = _rodar(monkeypatch, tmp_path, ["pulado"] * 21, 0, chave=False, bloqueante=bloqueante)
    assert saida == 0
    assert (
        "NAO RODOU" in resumo
        and "MAEZO_ANTHROPIC_API_KEY ausente" in resumo
        and "21 caso(s) pulado(s)" in resumo
    )
    assert "chave-de-teste" not in resumo


def test_with_key_all_green(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    saida, resumo = _rodar(monkeypatch, tmp_path, ["aprovado"] * 3, 0, chave=True, bloqueante="false")
    assert saida == 0 and "RODOU: 3 caso(s), todos aprovados" in resumo
    assert resumo.count("| aprovado |") == 3 and "chave-de-teste" not in resumo


def test_below_threshold_is_observed_not_blocking(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    saida, resumo = _rodar(
        monkeypatch, tmp_path, ["aprovado", "reprovado"], 1, chave=True, bloqueante="false"
    )
    assert saida == 0
    assert (
        "1 de 2 caso(s) abaixo do limiar" in resumo and "observacao" in resumo and "| reprovado |" in resumo
    )


def test_below_threshold_blocks_when_blocking(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    saida, resumo = _rodar(monkeypatch, tmp_path, ["aprovado", "reprovado"], 1, chave=True, bloqueante="true")
    assert saida == 1 and "BLOQUEANTE" in resumo


@pytest.mark.parametrize("bloqueante", ["false", "true"])
@pytest.mark.parametrize(
    ("casos", "codigo"), [(["aprovado"], 2), ([], 5), ([], 0), (["pulado"], 3), (["aprovado"], 4)]
)
def test_broken_lane_always_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, casos: list[str], codigo: int, bloqueante: str
) -> None:
    saida, resumo = _rodar(monkeypatch, tmp_path, casos, codigo, chave=True, bloqueante=bloqueante)
    assert saida == 1 and "QUEBRADO" in resumo


def test_missing_junit_always_fails(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    resumo = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(resumo))
    monkeypatch.setenv("TIER_B_BLOQUEANTE", "false")
    assert tb.main(["--junit", str(tmp_path / "nao-existe.xml"), "--codigo", "0"]) == 1
    assert "QUEBRADO" in resumo.read_text(encoding="utf-8")


def test_unset_mode_defaults_to_blocking(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TIER_B_BLOQUEANTE", raising=False)
    c = tb.Contagem(casos=[("x", "reprovado")])
    assert tb.decidir(1, c, chave_presente=True, bloqueante=True)[0] == 1
