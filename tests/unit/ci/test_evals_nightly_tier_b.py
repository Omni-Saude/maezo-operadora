"""`evals-nightly` (ci.yml) partido em Tier A (bloqueante) e Tier B (ao vivo, com resumo proprio).

Cercas pedidas pela ROOT (21/11/2026):
* a selecao do passo A + passo B e' EXATAMENTE a do alvo `make evals` lido do Makefile (se o Makefile mudar
  a selecao, isto falha); as duas sao disjuntas — e as provas negativas sao testes, nao execucao manual;
* o Tier B nunca e' "verde por pular": o resumo diz se rodou e N de M casos; com a chave presente e nenhum
  caso rodado, erro de fixture, falha que nao e' de limiar, 100% reprovado ou codigo fora de {0,1}, falha
  nos dois modos; so' reprovacao de limiar em parte dos casos vira aviso no modo observacao;
* o job `evals` de PR continua intocado (`make evals`, sem a chave nova)."""

from __future__ import annotations

import re
import shlex
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml
from scripts.ci import tier_b_resumo as tb

_RAIZ = Path(__file__).resolve().parents[3]
_CI = _RAIZ / ".github" / "workflows" / "ci.yml"
_MAKEFILE = _RAIZ / "Makefile"

Selecao = tuple[str, str]  # (caminho, expressao -m)


def _jobs() -> dict[str, Any]:
    jobs: dict[str, Any] = yaml.safe_load(_CI.read_text(encoding="utf-8"))["jobs"]
    return jobs


def _passo(prefixo: str) -> dict[str, Any]:
    return next(s for s in _jobs()["evals-nightly"]["steps"] if str(s.get("name", "")).startswith(prefixo))


def _pytest_args(script: str) -> Selecao:
    """(caminho, expressao -m) da linha `python -m pytest` de um script de passo ou receita do Makefile."""
    linha = next(x for x in script.splitlines() if "-m pytest" in x)
    tokens = shlex.split(linha.split("||")[0])
    i = tokens.index("pytest")
    return tokens[i + 1], tokens[tokens.index("-m", i) + 1]


def _selecao_do_make(texto_makefile: str) -> Selecao:
    alvo = re.search(r"^evals:.*\n\t(.+)$", texto_makefile, re.M)
    assert alvo, "alvo `evals:` sumiu do Makefile"
    return _pytest_args(alvo.group(1))


def _coletar(sel: Selecao) -> set[str]:
    caminho, marcador = sel
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


def _problemas_da_particao(
    make: Selecao, a: Selecao, b: Selecao, coletar: Callable[[Selecao], set[str]]
) -> list[str]:
    """Vazio se A e B sao disjuntos e A | B == make. Separado para as provas negativas."""
    problemas = []
    if not (a[0] == b[0] == make[0]):
        problemas.append(f"caminhos diferentes: {a[0]!r} {b[0]!r} {make[0]!r}")
    m, ca, cb = coletar(make), coletar(a), coletar(b)
    if not m:
        problemas.append("make evals nao coleta nada")
    if ca & cb:
        problemas.append(f"A e B se sobrepoem: {sorted(ca & cb)[:3]}")
    if ca | cb != m:
        problemas.append(f"faltam {sorted(m - (ca | cb))[:3]} / sobram {sorted((ca | cb) - m)[:3]}")
    return problemas


def _selecoes_reais() -> tuple[Selecao, Selecao, Selecao]:
    return (
        _selecao_do_make(_MAKEFILE.read_text(encoding="utf-8")),
        _pytest_args(_passo("Nightly eval run — Tier A")["run"]),
        _pytest_args(_passo("Nightly eval run — Tier B")["run"]),
    )


def test_tier_a_plus_tier_b_selection_equals_make_evals() -> None:
    make, a, b = _selecoes_reais()
    assert _problemas_da_particao(make, a, b, _coletar) == []


@pytest.mark.parametrize(
    ("mutacao", "esperado"),
    [
        # o Makefile muda a selecao: A + B deixa de cobrir exatamente o make evals
        ("makefile", "sobram"),
        # o passo B troca o marcador: casos vivos somem e/ou duplicam
        ("marcador_b", "faltam"),
        # o passo A deixa de excluir o vivo: A e B se sobrepoem
        ("marcador_a", "se sobrepoem"),
    ],
)
def test_partition_fence_catches_mutations(mutacao: str, esperado: str) -> None:
    make, a, b = _selecoes_reais()
    if mutacao == "makefile":
        texto = _MAKEFILE.read_text(encoding="utf-8").replace("-m eval ", '-m "eval and not llm_live" ', 1)
        assert texto != _MAKEFILE.read_text(encoding="utf-8"), "a receita do make evals mudou de forma"
        make = _selecao_do_make(texto)
    elif mutacao == "marcador_b":
        b = (b[0], "llm_live and not eval")
    else:
        a = (a[0], "eval")
    problemas = _problemas_da_particao(make, a, b, _coletar)
    assert any(esperado in p for p in problemas), problemas


def test_tier_b_step_wiring() -> None:
    b = _passo("Nightly eval run — Tier B")
    env = b["env"]
    assert env["MAEZO_ANTHROPIC_API_KEY"] == "${{ secrets.MAEZO_ANTHROPIC_API_KEY }}"
    assert env["TIER_B_BLOQUEANTE"] in ("false", "true")
    assert "continue-on-error" not in b
    assert "scripts/ci/tier_b_resumo.py" in b["run"]
    # F7: nada do conteudo dos casos vai para log ou artefato — sem `-rA`, `--tb=no`, junit no RUNNER_TEMP.
    assert "-rA" not in b["run"] and "--tb=no" in b["run"]
    assert '--junitxml="${RUNNER_TEMP}/tier-b-junit.xml"' in b["run"]
    assert "tier-b-junit" not in str(_passo("Upload nightly eval results")["with"]["path"])
    a = _passo("Nightly eval run — Tier A")
    assert a.get("id") == "tier_a"
    assert "continue-on-error" not in a and "MAEZO_ANTHROPIC_API_KEY" not in a.get("env", {})


def test_tier_a_failure_is_reported_as_tier_b_not_run() -> None:
    # F8: Tier A vermelho pula o passo B; o resumo diz, para a noite nao contar na observacao.
    aviso = _passo("TIER B NAO RODOU — Tier A falhou")
    assert (
        aviso["if"] == "failure() && steps.guard.outputs.run == 'true' && steps.tier_a.outcome == 'failure'"
    )
    assert "GITHUB_STEP_SUMMARY" in aviso["run"] and "nao conta" in aviso["run"]
    nomes = [str(s.get("name", "")) for s in _jobs()["evals-nightly"]["steps"]]
    assert nomes.index("Nightly eval run — Tier B (ao vivo)") < nomes.index(aviso["name"])


def test_pull_request_evals_job_is_untouched() -> None:
    texto = yaml.safe_dump(_jobs()["evals"])
    assert "make evals" in texto
    assert "MAEZO_ANTHROPIC_API_KEY" not in texto and "TIER_B_BLOQUEANTE" not in texto


# ------------------------------------------------------------------------- veredito do script do passo

_TH = "AssertionError: TH violation: live score 0.500 below threshold 0.900"


def _junit(tmp_path: Path, casos: list[str]) -> Path:
    corpo = []
    for i, r in enumerate(casos):
        filho = {
            "aprovado": "",
            "pulado": "<skipped/>",
            "limiar": f'<failure message="{_TH}"/>',
            "api": '<failure message="anthropic.AuthenticationError: 401 invalid x-api-key"/>',
            "erro": '<error message="fixture boom"/>',
        }[r]
        corpo.append(f'<testcase classname="tests.evals.t" name="caso_{i}">{filho}</testcase>')
    p = tmp_path / "j.xml"
    p.write_text(f'<testsuites><testsuite name="pytest">{"".join(corpo)}</testsuite></testsuites>', "utf-8")
    return p


def _rodar(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    casos: list[str] | None,
    codigo: int,
    *,
    chave: bool,
    bloqueante: str | None,
) -> tuple[int, str, str]:
    resumo = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(resumo))
    if bloqueante is None:
        monkeypatch.delenv("TIER_B_BLOQUEANTE", raising=False)
    else:
        monkeypatch.setenv("TIER_B_BLOQUEANTE", bloqueante)
    if chave:
        monkeypatch.setenv("MAEZO_ANTHROPIC_API_KEY", "chave-de-teste")
    else:
        monkeypatch.delenv("MAEZO_ANTHROPIC_API_KEY", raising=False)
    junit = _junit(tmp_path, casos) if casos is not None else tmp_path / "nao-existe.xml"
    saida = tb.main(["--junit", str(junit), "--codigo", str(codigo)])
    return saida, resumo.read_text(encoding="utf-8"), junit.name


@pytest.fixture
def capsys_texto(capsys: pytest.CaptureFixture[str]) -> Callable[[], str]:
    return lambda: capsys.readouterr().out


@pytest.mark.parametrize("bloqueante", ["false", "true"])
def test_without_key_says_not_run_and_passes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, bloqueante: str
) -> None:
    saida, resumo, _ = _rodar(monkeypatch, tmp_path, ["pulado"] * 21, 0, chave=False, bloqueante=bloqueante)
    assert saida == 0
    assert (
        "NAO RODOU" in resumo
        and "MAEZO_ANTHROPIC_API_KEY ausente" in resumo
        and "21 caso(s) pulado(s)" in resumo
    )


@pytest.mark.parametrize("bloqueante", ["false", "true"])
def test_key_present_and_nothing_ran_is_broken(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, bloqueante: str
) -> None:
    # F1: pulo total COM chave nao e' "nao rodou" — e' raia quebrada.
    saida, resumo, _ = _rodar(monkeypatch, tmp_path, ["pulado"] * 21, 0, chave=True, bloqueante=bloqueante)
    assert saida == 1 and "QUEBRADO" in resumo and "chave presente e nenhum dos 21" in resumo


def test_with_key_all_green(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys_texto: Callable[[], str]
) -> None:
    saida, resumo, _ = _rodar(monkeypatch, tmp_path, ["aprovado"] * 3, 0, chave=True, bloqueante="false")
    assert saida == 0 and "RODOU: 3 de 3 rodaram, todos aprovados" in resumo
    assert resumo.count("| aprovado |") == 3
    assert "chave-de-teste" not in resumo + capsys_texto()


def test_partial_threshold_failure_is_observed_not_blocking(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys_texto: Callable[[], str]
) -> None:
    saida, resumo, _ = _rodar(
        monkeypatch, tmp_path, ["aprovado", "limiar"], 1, chave=True, bloqueante="false"
    )
    assert saida == 0
    assert "1 abaixo do limiar — observacao" in resumo and "| abaixo do limiar |" in resumo
    assert "::warning::Tier B: 1 caso(s) abaixo do limiar" in capsys_texto()
    assert _TH not in resumo, "a mensagem da falha (saida do modelo) nao vai para o resumo"


def test_partial_threshold_failure_blocks_when_blocking(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    saida, resumo, _ = _rodar(monkeypatch, tmp_path, ["aprovado", "limiar"], 1, chave=True, bloqueante="true")
    assert saida == 1 and "BLOQUEANTE" in resumo


@pytest.mark.parametrize("bloqueante", ["false", "true"])
@pytest.mark.parametrize(
    ("casos", "codigo", "trecho"),
    [
        # F2: erro de fixture/setup
        (["aprovado", "erro"], 1, "erro de fixture"),
        # F2: falha que nao e' de limiar (API 401, rede, credencial) mesmo com o resto verde
        (["aprovado", "api"], 1, "nao e' de limiar"),
        # F2: 100% dos rodados reprovando no limiar = falha sistemica
        (["limiar", "limiar", "pulado"], 1, "todos os 2 caso(s) rodados reprovaram"),
        # F3: codigo fora de {0, 1}
        (["aprovado"], 2, "pytest exit 2"),
        (["aprovado"], 3, "pytest exit 3"),
        (["aprovado"], 4, "pytest exit 4"),
        ([], 5, "pytest exit 5"),
        (["aprovado"], 137, "pytest exit 137"),
        (["aprovado"], -9, "pytest exit -9"),
        # zero casos coletados com exit 0
        ([], 0, "nenhum caso coletado"),
        # exit 1 sem reprovacao no junit
        (["aprovado"], 1, "sem reprovacao no junit"),
    ],
)
def test_broken_lane_always_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    casos: list[str],
    codigo: int,
    trecho: str,
    bloqueante: str,
) -> None:
    saida, resumo, _ = _rodar(monkeypatch, tmp_path, casos, codigo, chave=True, bloqueante=bloqueante)
    assert saida == 1 and "QUEBRADO" in resumo and trecho in resumo, resumo


def test_partial_skip_with_key_is_reported_and_warned(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys_texto: Callable[[], str]
) -> None:
    # F4: com chave, N de M rodaram; N < M vira aviso explicito.
    saida, resumo, _ = _rodar(
        monkeypatch, tmp_path, ["aprovado", "aprovado", "pulado"], 0, chave=True, bloqueante="false"
    )
    assert saida == 0
    assert "2 de 3 rodaram" in resumo and "pulo parcial: 2 de 3" in resumo
    assert "::warning::Tier B: pulo parcial: 2 de 3 caso(s) rodaram" in capsys_texto()


def test_unset_mode_defaults_to_blocking(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # F5: pela main(), com a variavel AUSENTE.
    saida, resumo, _ = _rodar(monkeypatch, tmp_path, ["aprovado", "limiar"], 1, chave=True, bloqueante=None)
    assert saida == 1 and "modo: bloqueante" in resumo and "BLOQUEANTE" in resumo


@pytest.mark.parametrize("conteudo", ["", "<testsuites><testsuite>", "nao e' xml", "\x00\x01"])
@pytest.mark.parametrize("bloqueante", ["false", "true"])
def test_corrupt_or_missing_junit_always_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, conteudo: str, bloqueante: str
) -> None:
    # F5: junit corrompido (e ausente, no caso vazio de arquivo) => quebrado, nos dois modos.
    resumo = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(resumo))
    monkeypatch.setenv("TIER_B_BLOQUEANTE", bloqueante)
    monkeypatch.setenv("MAEZO_ANTHROPIC_API_KEY", "chave-de-teste")
    junit = tmp_path / "corrompido.xml"
    junit.write_text(conteudo, encoding="utf-8")
    assert tb.main(["--junit", str(junit), "--codigo", "0"]) == 1
    assert "QUEBRADO" in resumo.read_text(encoding="utf-8")
    assert tb.main(["--junit", str(tmp_path / "nao-existe.xml"), "--codigo", "0"]) == 1
