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
from scripts.ci import tier_b_preflight as pf
from scripts.ci import tier_b_resumo as tb
from scripts.dev import tier_b_manual as manual

from tests.evals import _vivo

_RAIZ = Path(__file__).resolve().parents[3]
_CI = _RAIZ / ".github" / "workflows" / "ci.yml"
_MAKEFILE = _RAIZ / "Makefile"

Selecao = tuple[str, str]  # (caminho, expressao -m)


@pytest.fixture(autouse=True)
def _sem_provedor_do_ambiente(monkeypatch: pytest.MonkeyPatch) -> None:
    # `tests/evals/_vivo.py` le MAEZO_INFERENCE_PROVIDER (como o runtime): um valor herdado
    # da maquina (`.env`, sessao do dev) mudaria o desfecho ou faria chamada real; quem precisa, define.
    monkeypatch.delenv("MAEZO_INFERENCE_PROVIDER", raising=False)
    # O opt-in de execucao do job noturno (como no ci.yml); quem testa o caminho SEM opt-in, remove.
    monkeypatch.setenv("TIER_B_EXECUCAO", "noturno")


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


def _tokens_do_pytest(script: str) -> list[str]:
    linha = next(x for x in script.splitlines() if "-m pytest" in x)
    tokens = shlex.split(linha.split("||")[0])
    return tokens[tokens.index("pytest") + 1 :]


#: Unicos tokens que os passos A/B podem ter ALEM da forma do `make evals` (caminho, -q, -m <expr>).
_EXTRAS_PERMITIDOS = {"A": set(), "B": {"--tb=no", "-rN", "--junitxml=${RUNNER_TEMP}/tier-b-junit.xml"}}


def _forma(tokens: list[str], extras: set[str]) -> list[str]:
    return [t for t in tokens if t not in extras]


def _forma_confere(tokens: list[str], passo: str, caminho_make: str, expr: str) -> bool:
    """A cerca de forma: o passo e' o `make evals` (caminho, -q, -m <expr>) mais SO' os extras permitidos."""
    return _forma(tokens, _EXTRAS_PERMITIDOS[passo]) == [caminho_make, "-q", "-m", expr]


def test_steps_have_exactly_the_make_evals_shape_plus_allowed_extras() -> None:
    # `-k`, `--deselect`, segundo caminho, `-x`/`--maxfail` encolheriam a selecao sem mudar o `-m`.
    alvo = re.search(r"^evals:.*\n\t(.+)$", _MAKEFILE.read_text(encoding="utf-8"), re.M)
    assert alvo
    make = _tokens_do_pytest(alvo.group(1))
    assert len(make) == 4 and make[1:3] == ["-q", "-m"], make
    a = _tokens_do_pytest(_passo("Nightly eval run — Tier A")["run"])
    b = _tokens_do_pytest(_passo("Nightly eval run — Tier B")["run"])
    assert _forma_confere(a, "A", make[0], "eval and not llm_live"), a
    assert _forma_confere(b, "B", make[0], "eval and llm_live"), b
    assert make[3] == "eval", "a selecao do make evals mudou: reveja a particao A/B"


@pytest.mark.parametrize(
    "intruso", ["-x", "--maxfail=1", "-k helena", "--deselect tests/evals/a.py::t", "tests/unit"]
)
def test_shape_fence_catches_extra_selection_tokens(intruso: str) -> None:
    alvo = re.search(r"^evals:.*\n\t(.+)$", _MAKEFILE.read_text(encoding="utf-8"), re.M)
    assert alvo
    caminho_make = _tokens_do_pytest(alvo.group(1))[0]
    b = _tokens_do_pytest(_passo("Nightly eval run — Tier B")["run"])
    assert _forma_confere(b, "B", caminho_make, "eval and llm_live"), "o passo real deveria conferir"
    assert not _forma_confere(b + shlex.split(intruso), "B", caminho_make, "eval and llm_live")


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
            "estado_com_frase": '<failure message="AssertionError: SF mismatch full state={&quot;nota&quot;: '
            '&quot;TH violation: live score 0.1 abaixo do limiar&quot;}"/>',
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
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
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
    assert tb.MANCHETE_NAO_RODOU_CI in resumo and "21 caso(s) pulado(s)" in resumo
    assert "nao roda no CI" in resumo and "zona PHI" in resumo and tb.ROTEIRO_MANUAL in resumo
    # O estado normal nao convida a criar justamente o segredo que nao deve ser criado.
    assert "segredo" not in resumo and "MAEZO_ANTHROPIC_API_KEY" not in resumo


@pytest.mark.parametrize("bloqueante", ["false", "true"])
def test_key_present_and_nothing_ran_is_broken(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, bloqueante: str
) -> None:
    # F1: pulo total COM chave nao e' "nao rodou" — e' raia quebrada.
    saida, resumo, _ = _rodar(monkeypatch, tmp_path, ["pulado"] * 21, 0, chave=True, bloqueante=bloqueante)
    assert saida == 1 and "QUEBRADO" in resumo and "credencial presente e nenhum dos 21" in resumo


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
        # assercao qualquer cuja mensagem CARREGA o texto do limiar nao e' rebaixada a "limiar"
        (["aprovado", "limiar", "estado_com_frase"], 1, "nao e' de limiar"),
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


# ------------------------------------------------------------------------- preflight de vivacidade (B)


class _SdkStatusError(Exception):
    status_code = 401


class _Provedor:
    def __init__(self, resposta: object = "ok", erro: BaseException | None = None) -> None:
        self.resposta, self.erro, self.chamadas = resposta, erro, 0
        self.phi: list[bool] = []

    # A assinatura inteira do Protocol real (tests/unit/runtime/test_inference_fakes_match_protocol.py).
    async def generate(
        self,
        prompt: str,
        *,
        phi: bool = False,
        agent_id: str | None = None,
        tenant_id: str | None = None,
        task_kind: str | None = None,
    ) -> object:
        self.chamadas += 1
        self.phi.append(phi)
        if self.erro is not None:
            raise self.erro
        return self.resposta


def _erro_do_provedor() -> BaseException:
    # Como o `AnthropicInferenceProvider` faz: InferenceProviderError com a mensagem do SDK (que traz o
    # corpo da resposta) e o erro do SDK, com status, como causa.
    from maezo.runtime.inference.errors import InferenceProviderError

    try:
        raise _SdkStatusError("CORPO-SECRETO-DA-RESPOSTA")
    except _SdkStatusError as sdk:
        erro = InferenceProviderError(
            "anthropic", "API error (401): CORPO-SECRETO-DA-RESPOSTA", retryable=False
        )
        erro.__cause__ = sdk
        return erro


def test_preflight_without_key_makes_no_call(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MAEZO_ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    p = _Provedor()
    assert pf.main([], fabrica=lambda: p) == 0 and p.chamadas == 0


def test_preflight_ok_and_calls_with_phi_true(monkeypatch: pytest.MonkeyPatch) -> None:
    # (b) a MESMA rota da suite: os grafos chamam `generate(..., phi=True)`.
    monkeypatch.setenv("MAEZO_ANTHROPIC_API_KEY", "chave-de-teste")
    p = _Provedor("ok")
    assert pf.main([], fabrica=lambda: p) == 0 and p.chamadas == 1 and p.phi == [True]


@pytest.mark.parametrize("resposta", ["", "   ", None])
def test_preflight_empty_answer_is_broken(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, resposta: object
) -> None:
    monkeypatch.setenv("MAEZO_ANTHROPIC_API_KEY", "chave-de-teste")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "s.md"))
    assert pf.main([], fabrica=lambda: _Provedor(resposta)) == 1
    assert "QUEBRADO: preflight sem resposta" in (tmp_path / "s.md").read_text(encoding="utf-8")


def test_preflight_failure_prints_only_class_and_status(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("MAEZO_ANTHROPIC_API_KEY", "chave-de-teste")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "s.md"))
    assert pf.main([], fabrica=lambda: _Provedor(erro=_erro_do_provedor())) == 1
    saida = capsys.readouterr().out + (tmp_path / "s.md").read_text(encoding="utf-8")
    assert "QUEBRADO: preflight falhou (InferenceProviderError, status 401)" in saida
    assert "CORPO-SECRETO" not in saida and "chave-de-teste" not in saida


def test_preflight_uses_the_evals_provider_factory_not_a_copy() -> None:
    # (b) a configuracao vem dos testes (tests/evals/_vivo.py), nao e' duplicada no script.
    from tests.evals import _vivo

    assert pf._provedor_dos_evals() is _vivo.provedor_vivo


def test_preflight_with_the_current_provider_reports_the_phi_zone(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Prova real, sem rede: o provedor dos evals (`anthropic`, nao phi_capable) com phi=True e' recusado
    # pela fachada (ADR-0006). O resumo diz a linha estavel da zona PHI e que a noite nao conta.
    monkeypatch.setenv("MAEZO_ANTHROPIC_API_KEY", "chave-de-teste")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "s.md"))
    monkeypatch.setenv("TIER_B_NOITES_CONTAM", "false")
    assert pf.main([]) == 1
    saida = capsys.readouterr().out + (tmp_path / "s.md").read_text(encoding="utf-8")
    assert "QUEBRADO: preflight falhou (PhiZoneRoutingError" in saida
    assert tb.LINHA_ZONA_PHI in saida and tb.LINHA_NOITES_NAO_CONTAM in saida
    assert "chave-de-teste" not in saida


def test_preflight_imports_under_python_isolated_mode() -> None:
    # (d4) `python -I` (como no passo do job) acha `maezo` e `tests.evals._vivo`; sem chave, sai 0.
    env = {
        k: v
        for k, v in __import__("os").environ.items()
        if "ANTHROPIC_API_KEY" not in k and k != "MAEZO_INFERENCE_PROVIDER"
    }
    r = subprocess.run(
        [sys.executable, "-I", "scripts/ci/tier_b_preflight.py"],
        cwd=_RAIZ,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert r.returncode == 0, r.stderr[-2000:]
    assert "nenhuma chamada feita" in r.stdout and tb.MANCHETE_NAO_RODOU_CI in r.stdout
    assert "segredo" not in r.stdout and "MAEZO_ANTHROPIC_API_KEY" not in r.stdout


def test_preflight_runs_before_the_suite_and_fails_the_step() -> None:
    run = _passo("Nightly eval run — Tier B")["run"]
    i_pf, i_set, i_pytest = run.index("tier_b_preflight.py"), run.index("set +e"), run.index("-m pytest")
    assert run.index("set -e") < i_pf < i_set < i_pytest, "o preflight roda sob `set -e`, antes da suite"


def test_summary_says_observation_nights_are_suspended(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    assert _passo("Nightly eval run — Tier B")["env"]["TIER_B_NOITES_CONTAM"] == "false"
    monkeypatch.setenv("TIER_B_NOITES_CONTAM", "false")
    _, resumo, _ = _rodar(monkeypatch, tmp_path, ["aprovado"], 0, chave=True, bloqueante="false")
    assert tb.LINHA_NOITES_NAO_CONTAM in resumo
    assert "suspensas ate existir provedor na zona PHI no CI" in tb.LINHA_NOITES_NAO_CONTAM
    monkeypatch.setenv("TIER_B_NOITES_CONTAM", "true")
    (tmp_path / "summary.md").unlink()
    _, resumo, _ = _rodar(monkeypatch, tmp_path, ["aprovado"], 0, chave=True, bloqueante="false")
    assert tb.LINHA_NOITES_NAO_CONTAM not in resumo


# ------------------------------------------------------------------------- gravador de chamadas (a)


def test_recorder_records_the_real_phi_refusal_and_reraises(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    from maezo.runtime.inference.errors import PhiZoneRoutingError

    monkeypatch.setenv("MAEZO_ANTHROPIC_API_KEY", "chave-de-teste")
    g = _vivo.provedor_vivo()
    with pytest.raises(PhiZoneRoutingError):
        asyncio.run(g.generate("x", phi=True))
    assert g.chamadas == ["PhiZoneRoutingError"]
    with pytest.raises(AssertionError, match=r"^LIVE CALL FAILED: PhiZoneRoutingError$"):
        _vivo.exigir_chamada_viva(g)


def test_recorder_ok_call_and_zero_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    from maezo.runtime.inference import InferenceProvider

    async def _ok(self: object, prompt: str, **kwargs: object) -> str:
        return "resposta"

    monkeypatch.setenv("MAEZO_ANTHROPIC_API_KEY", "chave-de-teste")
    monkeypatch.setattr(InferenceProvider, "generate", _ok)
    g = _vivo.provedor_vivo()
    with pytest.raises(AssertionError, match=r"^LIVE CALL FAILED: NenhumaChamadaAoProvedor$"):
        _vivo.exigir_chamada_viva(g)
    assert asyncio.run(g.generate("x", phi=True)) == "resposta" and g.chamadas == [None]
    _vivo.exigir_chamada_viva(g)


@pytest.mark.parametrize("dossie", [None, {}, {"narrativa": ""}, {"narrativa": "   "}, {"narrativa": None}])
def test_leak_checks_require_a_generated_narrative(dossie: dict[str, object] | None) -> None:
    with pytest.raises(AssertionError, match=r"^NARRATIVA AUSENTE"):
        _vivo.exigir_narrativa(dossie)
    _vivo.exigir_narrativa({"narrativa": "texto gerado"})


def test_every_live_eval_goes_through_the_recorder() -> None:
    # Nenhum teste vivo constroi o provedor por fora do gravador; todos exigem a chamada antes de pontuar.
    raiz = _RAIZ / "tests" / "evals"
    for arquivo in sorted(raiz.glob("test_*.py")):
        texto = arquivo.read_text(encoding="utf-8")
        assert 'InferenceProvider(settings=InferenceSettings(provider="anthropic"))' not in texto, (
            arquivo.name
        )
        if "@pytest.mark.llm_live" in texto:
            assert "exigir_chamada_viva(" in texto, arquivo.name


# --------------------------------------------- regex do resumo contra a saida REAL do pytest (d2)

_TESTE_FALSO = """
from types import SimpleNamespace
from tests.evals._harness import assert_live_score
from tests.evals._vivo import exigir_chamada_viva

def test_limiar_do_harness():
    assert_live_score(0.5, 0.9)

def test_limiar_da_extracao():
    agregada = 0.5
    assert agregada >= 0.9, (
        f"extracao em {agregada:.3f}, abaixo do limiar de partida 0.90. LEIA O RELATORIO ACIMA."
    )

def test_chamada_viva_falhou():
    exigir_chamada_viva(SimpleNamespace(chamadas=["PhiZoneRoutingError"]))

def test_outra_assercao_com_o_texto():
    assert {"nota": "TH violation: live score 0.1 below threshold 0.9"} == {}
"""


def test_summary_regexes_match_the_real_pytest_junit(tmp_path: Path) -> None:
    alvo = tmp_path / "test_falso_tier_b.py"
    alvo.write_text(_TESTE_FALSO, encoding="utf-8")
    junit = tmp_path / "j.xml"
    import os as _os

    env = dict(_os.environ, PYTHONPATH=str(_RAIZ))
    r = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            str(alvo),
            "-q",
            "--tb=no",
            "-rN",
            "-p",
            "no:cacheprovider",
            f"--junitxml={junit}",
            f"--rootdir={tmp_path}",
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert r.returncode == 1, r.stdout[-2000:] + r.stderr[-2000:]
    c = tb.ler_junit(junit)
    resultado = {ident.split("::")[-1]: res for ident, res in c.casos}
    assert resultado == {
        "test_limiar_do_harness": tb.LIMIAR,
        "test_limiar_da_extracao": tb.LIMIAR,
        "test_chamada_viva_falhou": tb.VIVA,
        "test_outra_assercao_com_o_texto": tb.FALHA,
    }, resultado
    assert c.classes_vivas == {"PhiZoneRoutingError"}


def test_summary_with_live_call_failures_says_phi_zone_and_is_broken(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    resumo_arq = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(resumo_arq))
    monkeypatch.setenv("TIER_B_BLOQUEANTE", "false")
    monkeypatch.delenv("MAEZO_ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "chave-de-teste")  # (d1) o outro nome tambem conta
    junit = tmp_path / "j.xml"
    casos = "".join(
        f'<testcase classname="t" name="c{i}"><failure message="AssertionError: LIVE CALL FAILED: '
        f'PhiZoneRoutingError"/></testcase>'
        for i in range(3)
    )
    junit.write_text(f"<testsuites><testsuite>{casos}</testsuite></testsuites>", encoding="utf-8")
    assert tb.main(["--junit", str(junit), "--codigo", "1"]) == 1
    resumo = resumo_arq.read_text(encoding="utf-8")
    assert "QUEBRADO: 3 caso(s) com LIVE CALL FAILED (PhiZoneRoutingError)" in resumo
    assert tb.LINHA_ZONA_PHI in resumo


def test_key_under_either_name_counts_as_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MAEZO_ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert tb.chave_presente() is False
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    assert tb.chave_presente() is True


# ------------------------------------------- provedor do ambiente (f), execucao manual (g), linha (h)


@pytest.mark.parametrize(
    ("valor", "esperado"),
    [(None, "anthropic"), ("", "anthropic"), ("bedrock_br", "bedrock_br"), ("anthropic", "anthropic")],
)
def test_live_provider_comes_from_the_runtime_variable(
    monkeypatch: pytest.MonkeyPatch, valor: str | None, esperado: str
) -> None:
    if valor is None:
        monkeypatch.delenv("MAEZO_INFERENCE_PROVIDER", raising=False)
    else:
        monkeypatch.setenv("MAEZO_INFERENCE_PROVIDER", valor)
    assert _vivo.configuracao_viva().provider == esperado


@pytest.mark.parametrize("mock", ["noop", "phi_zone_mock", "qualquer"])
def test_a_test_double_provider_is_refused_never_scored(monkeypatch: pytest.MonkeyPatch, mock: str) -> None:
    monkeypatch.setenv("MAEZO_INFERENCE_PROVIDER", mock)
    with pytest.raises(AssertionError, match=r"^LIVE CALL FAILED: ProvedorNaoVivo"):
        _vivo.configuracao_viva()


def test_phi_zone_provider_counts_as_live_credential_without_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    for nome in ("MAEZO_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY", "MAEZO_INFERENCE_PROVIDER"):
        monkeypatch.delenv(nome, raising=False)
    assert _vivo.credencial_viva_presente() is False and tb.chave_presente() is False
    monkeypatch.setenv("MAEZO_INFERENCE_PROVIDER", "bedrock_br")
    assert _vivo.credencial_viva_presente() is True and tb.chave_presente() is True


def test_conftest_gate_runs_tier_b_under_the_phi_zone_provider() -> None:
    # O portao `live_key_skip` aceita o provedor da zona PHI (SigV4, sem chave de API).
    import os as _os

    base = {
        k: v
        for k, v in _os.environ.items()
        if "ANTHROPIC_API_KEY" not in k and k != "MAEZO_INFERENCE_PROVIDER"
    }
    base.pop("TIER_B_EXECUCAO", None)
    codigo = "import tests.evals.conftest as c; print(c._MOTIVO_DO_PULO is None)"
    for execucao, provedor, roda in (
        ("noturno", "", "False"),
        ("noturno", "bedrock_br", "True"),
        ("manual", "bedrock_br", "True"),
        ("noturno", "anthropic", "False"),
        ("", "bedrock_br", "False"),  # sem opt-in: pula, qualquer que seja o provedor
    ):
        env = dict(base, MAEZO_INFERENCE_PROVIDER=provedor, PYTHONPATH=str(_RAIZ))
        if execucao:
            env["TIER_B_EXECUCAO"] = execucao
        r = subprocess.run([sys.executable, "-c", codigo], cwd=_RAIZ, env=env, capture_output=True, text=True)
        assert r.stdout.strip().splitlines()[-1] == roda, (execucao, provedor, r.stderr[-1000:])


def test_manual_run_refuses_without_gates_and_never_prints_values() -> None:
    env = {"MAEZO_INFERENCE_PROVIDER": "anthropic", "MAEZO_PHI_ENDPOINT_URL": "VALOR-SECRETO-DO-TFVARS"}
    problemas = manual.problemas_do_ambiente(env, credencial_aws=False)
    texto = "\n".join(problemas)
    assert "MAEZO_INFERENCE_PROVIDER deve ser vazio ou bedrock_br" in texto
    assert "falta MAEZO_PHI_VENDOR_DPA_REF" in texto and "falta MAEZO_INFERENCE_MODEL" in texto
    assert "falta MAEZO_PHI_ENDPOINT_URL" not in texto and "sem credencial AWS" in texto
    assert "VALOR-SECRETO" not in texto
    completo = {nome: "x" for nome in manual.PORTOES}
    assert manual.problemas_do_ambiente(completo, credencial_aws=True) == []


def test_manual_run_uses_the_nightly_preflight_selection_and_summary(tmp_path: Path) -> None:
    preflight, suite, veredito = manual.comandos(tmp_path / "j.xml")
    assert preflight[1:] == ["-I", "scripts/ci/tier_b_preflight.py"]
    assert veredito[1:3] == ["-I", "scripts/ci/tier_b_resumo.py"]
    # A MESMA selecao e as MESMAS opcoes do passo B do noturno (so' o caminho do junit muda).
    noturno = _tokens_do_pytest(_passo("Nightly eval run — Tier B")["run"])
    manual_tokens = suite[suite.index("pytest") + 1 :]
    sem_junit = [t for t in noturno if not t.startswith("--junitxml=")]
    assert [t for t in manual_tokens if not t.startswith("--junitxml=")] == sem_junit
    env = manual.ambiente_da_execucao({"MAEZO_INFERENCE_PROVIDER": ""}, tmp_path / "r.md")
    assert env["MAEZO_INFERENCE_PROVIDER"] == "bedrock_br" and env["TIER_B_EXECUCAO"] == "manual"
    assert env["GITHUB_STEP_SUMMARY"] == str(tmp_path / "r.md") and env["TIER_B_BLOQUEANTE"] == "false"


def test_manual_summary_says_manual_and_drops_the_nights_line(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("TIER_B_EXECUCAO", "manual")
    _, resumo, _ = _rodar(monkeypatch, tmp_path, ["aprovado"], 0, chave=True, bloqueante="false")
    assert tb.LINHA_EXECUCAO_MANUAL in resumo and "NAO contam" not in resumo


def test_phi_zone_line_points_to_an_existing_readme_section() -> None:
    # (h) a linha do noturno diz que o Tier B nao roda no CI e aponta para o roteiro manual — que existe.
    assert tb.LINHA_ZONA_PHI.startswith("Tier B nao roda no CI: exige provedor na zona PHI")
    arquivo, ancora = tb.ROTEIRO_MANUAL.split("#")
    titulos = [
        linha[3:].strip()
        for linha in (_RAIZ / arquivo).read_text(encoding="utf-8").splitlines()
        if linha.startswith("## ")
    ]
    ancoras = {re.sub(r"[^a-z0-9 -]", "", t.lower()).replace(" ", "-") for t in titulos}
    assert ancora in ancoras, sorted(ancoras)


@pytest.mark.parametrize(
    ("provedor", "conta"),
    [
        ("bedrock", True),
        ("br_resident", True),
        ("bedrok_br", True),  # nome desconhecido (digitacao): quem nomeou quis rodar => roda e falha
        ("noop", False),
        ("phi_zone_mock", False),
    ],
)
def test_a_named_real_cloud_provider_is_a_credential_a_test_double_is_not(
    monkeypatch: pytest.MonkeyPatch, provedor: str, conta: bool
) -> None:
    # 2a revisao: `bedrock` (zona geral, nao phi_capable) sem chave RODA e falha visivel
    # (LIVE CALL FAILED: PhiZoneRoutingError), em vez de "NAO RODOU"; um `noop` de `.env` local segue pulado.
    for nome in ("MAEZO_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(nome, raising=False)
    monkeypatch.setenv("MAEZO_INFERENCE_PROVIDER", provedor)
    assert _vivo.credencial_viva_presente() is conta and tb.chave_presente() is conta


# ------------- regra de ROOT (21/11): "NAO RODOU" so' sem chave E sem provedor nomeado; provedor real
# nomeado (nao anthropic) sem credencial utilizavel => LIVE CALL FAILED / QUEBRADO, nunca "NAO RODOU".

#: O que torna uma credencial "utilizavel": chave Anthropic, portoes da zona PHI, qualquer credencial AWS.
_CREDENCIAIS = (
    "MAEZO_ANTHROPIC_API_KEY",
    "ANTHROPIC_API_KEY",
    *manual.PORTOES,
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "AWS_PROFILE",
    "AWS_DEFAULT_PROFILE",
    "AWS_WEB_IDENTITY_TOKEN_FILE",
    "AWS_ROLE_ARN",
    "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI",
    "AWS_CONTAINER_CREDENTIALS_FULL_URI",
)


def _sem_credencial_nenhuma(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import os as _os

    # Tambem a configuracao do runtime herdada da maquina (ex.: MAEZO_INFERENCE_PHI_ZONE_REQUIRED=true
    # mudaria a classe do erro); quem precisa do provedor, define depois.
    herdadas = [k for k in _os.environ if k.startswith(("MAEZO_INFERENCE_", "MAEZO_BEDROCK_", "MAEZO_PHI_"))]
    for nome in (*_CREDENCIAIS, *herdadas):
        monkeypatch.delenv(nome, raising=False)
    monkeypatch.setenv("AWS_CONFIG_FILE", str(tmp_path / "sem-config"))
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(tmp_path / "sem-credenciais"))
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")


@pytest.mark.parametrize("provedor", ["bedrock_br", "br_resident"])
def test_named_phi_provider_without_usable_credential_is_live_call_failed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, provedor: str
) -> None:
    _sem_credencial_nenhuma(monkeypatch, tmp_path)
    monkeypatch.setenv("MAEZO_INFERENCE_PROVIDER", provedor)
    assert _vivo.credencial_viva_presente() is True and tb.chave_presente() is True  # nao e' pulado
    with pytest.raises(AssertionError, match=r"^LIVE CALL FAILED: InferenceConfigError$") as falha:
        _vivo.provedor_vivo()
    assert type(falha.value.__cause__).__name__ == "InferenceConfigError"


def test_named_general_zone_provider_without_key_is_live_call_failed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import asyncio

    from maezo.runtime.inference.errors import PhiZoneRoutingError

    _sem_credencial_nenhuma(monkeypatch, tmp_path)
    monkeypatch.setenv("MAEZO_INFERENCE_PROVIDER", "bedrock")
    g = _vivo.provedor_vivo()
    with pytest.raises(PhiZoneRoutingError):  # recusado pela fachada antes da rede (ADR-0006)
        asyncio.run(g.generate("x", phi=True))
    with pytest.raises(AssertionError, match=r"^LIVE CALL FAILED: PhiZoneRoutingError$"):
        _vivo.exigir_chamada_viva(g)


def test_preflight_with_named_phi_provider_without_gates_is_broken(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _sem_credencial_nenhuma(monkeypatch, tmp_path)
    monkeypatch.setenv("MAEZO_INFERENCE_PROVIDER", "bedrock_br")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "s.md"))
    assert pf.main([]) == 1
    saida = capsys.readouterr().out + (tmp_path / "s.md").read_text(encoding="utf-8")
    assert "Tier B QUEBRADO: preflight falhou (InferenceConfigError, status sem status)" in saida
    assert "NAO RODOU" not in saida and "nenhuma chamada feita" not in saida


@pytest.mark.parametrize(
    ("provedor", "classe"),
    [("bedrock_br", "InferenceConfigError"), ("bedrok_br", "ProvedorNaoVivo")],
)
def test_preflight_names_the_live_call_failed_class(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    provedor: str,
    classe: str,
) -> None:
    # A MESMA classe que o resumo le no junit (`LIVE CALL FAILED: <Classe>`), nao "AssertionError".
    _sem_credencial_nenhuma(monkeypatch, tmp_path)
    monkeypatch.setenv("MAEZO_INFERENCE_PROVIDER", provedor)
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "s.md"))
    assert pf.main([]) == 1
    saida = capsys.readouterr().out + (tmp_path / "s.md").read_text(encoding="utf-8")
    assert f"Tier B QUEBRADO: preflight falhou ({classe}, status sem status)" in saida, saida


def test_preflight_with_key_and_a_test_double_provider_is_broken(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _sem_credencial_nenhuma(monkeypatch, tmp_path)
    monkeypatch.setenv("MAEZO_ANTHROPIC_API_KEY", "chave-de-teste")
    monkeypatch.setenv("MAEZO_INFERENCE_PROVIDER", "noop")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "s.md"))
    assert pf.main([]) == 1
    saida = capsys.readouterr().out + (tmp_path / "s.md").read_text(encoding="utf-8")
    assert "Tier B QUEBRADO: preflight falhou (ProvedorNaoVivo, status sem status)" in saida, saida
    assert "chave-de-teste" not in saida


def _tier_b_de_verdade(tmp_path: Path, provedor: str) -> tuple[int, str]:
    """A selecao REAL do noturno + o resumo REAL, sem credencial nenhuma; sem rede (falha antes)."""
    import os as _os

    env = {
        k: v
        for k, v in _os.environ.items()
        if k not in _CREDENCIAIS
        and not k.startswith(("PYTHON", "PYTEST_", "MAEZO_INFERENCE_", "MAEZO_BEDROCK_", "MAEZO_PHI_"))
    }
    env.update(
        AWS_CONFIG_FILE=str(tmp_path / "sem-config"),
        AWS_SHARED_CREDENTIALS_FILE=str(tmp_path / "sem-credenciais"),
        AWS_EC2_METADATA_DISABLED="true",
        NIGHTLY_EVAL="1",
        TIER_B_BLOQUEANTE="false",
        GITHUB_STEP_SUMMARY=str(tmp_path / "s.md"),
    )
    if provedor:
        env["MAEZO_INFERENCE_PROVIDER"] = provedor
    junit = tmp_path / "j.xml"
    suite = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/evals", "-q", "--tb=no", "-rN", "-p", "no:cacheprovider",
         "-m", "eval and llm_live", f"--junitxml={junit}"],
        cwd=_RAIZ, env=env, capture_output=True, text=True, check=False, timeout=300,
    )  # fmt: skip
    resumo = subprocess.run(
        [sys.executable, "-I", "scripts/ci/tier_b_resumo.py", "--junit", str(junit), "--codigo",
         str(suite.returncode)],
        cwd=_RAIZ, env=env, capture_output=True, text=True, check=False, timeout=120,
    )  # fmt: skip
    return resumo.returncode, (tmp_path / "s.md").read_text(encoding="utf-8") + suite.stdout[-2000:]


def test_real_tier_b_without_key_and_without_provider_does_not_run(tmp_path: Path) -> None:
    codigo, texto = _tier_b_de_verdade(tmp_path, "")
    assert codigo == 0, texto
    assert re.search(r"Tier B NAO RODOU: .* \(\d+ caso\(s\) pulado\(s\)\)", texto), texto
    assert "LIVE CALL FAILED" not in texto and "QUEBRADO" not in texto


def test_real_tier_b_with_named_phi_provider_without_credential_is_broken(tmp_path: Path) -> None:
    codigo, texto = _tier_b_de_verdade(tmp_path, "bedrock_br")
    assert codigo == 1, texto
    assert re.search(
        r"Tier B QUEBRADO: \d+ caso\(s\) com LIVE CALL FAILED \(InferenceConfigError\)", texto
    ), texto
    assert "NAO RODOU" not in texto and "pulados 0" in texto


# ------------- opt-in de execucao (ROOT, 21/11): caso vivo so' roda com TIER_B_EXECUCAO=noturno|manual

#: Plugin de pytest que CONTA cada construcao e chamada de `InferenceProvider` (o gravador e' subclasse).
_CONTADOR = """
import os
from maezo.runtime import inference as _inf

def _anota(o):
    with open(os.environ["CONTADOR_CHAMADAS"], "a", encoding="utf-8") as f:
        f.write(o + "\\n")

_init, _gen = _inf.InferenceProvider.__init__, _inf.InferenceProvider.generate

def __init__(self, *a, **k):
    _anota("construcao")
    _init(self, *a, **k)

async def generate(self, *a, **k):
    _anota("chamada")
    return await _gen(self, *a, **k)

_inf.InferenceProvider.__init__ = __init__
_inf.InferenceProvider.generate = generate
"""


def _suite_contada(
    tmp_path: Path, *, execucao: str, portoes: bool
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    """A selecao REAL do Tier B com `bedrock_br` e credenciais AWS SIMULADAS; conta provedores e chamadas."""
    import os as _os

    (tmp_path / "plugin").mkdir()
    (tmp_path / "plugin" / "contador_de_chamadas.py").write_text(_CONTADOR, encoding="utf-8")
    contador = tmp_path / "chamadas.txt"
    env = {
        k: v
        for k, v in _os.environ.items()
        if k not in _CREDENCIAIS
        and not k.startswith(
            ("PYTHON", "PYTEST_", "MAEZO_INFERENCE_", "MAEZO_BEDROCK_", "MAEZO_PHI_", "TIER_B_")
        )
    }
    env.update(
        PYTHONPATH=str(tmp_path / "plugin"),
        CONTADOR_CHAMADAS=str(contador),
        MAEZO_INFERENCE_PROVIDER="bedrock_br",
        AWS_ACCESS_KEY_ID="AKIASIMULADA0000000",  # credenciais SIMULADAS
        AWS_SECRET_ACCESS_KEY="segredo-simulado",
        AWS_CONFIG_FILE=str(tmp_path / "sem-config"),
        AWS_SHARED_CREDENTIALS_FILE=str(tmp_path / "sem-credenciais"),
        AWS_EC2_METADATA_DISABLED="true",
        # Rede de seguranca: se algo escapasse, iria para um proxy local inexistente, nunca para a AWS.
        HTTPS_PROXY="http://127.0.0.1:9",
        HTTP_PROXY="http://127.0.0.1:9",
        NO_PROXY="",
    )
    if portoes:
        env.update(
            MAEZO_PHI_ENDPOINT_URL="https://bedrock-runtime.sa-east-1.amazonaws.com",
            MAEZO_PHI_VENDOR_DPA_REF="dpa-simulado",
            MAEZO_INFERENCE_MODEL="modelo-simulado",
        )
    if execucao:
        env["TIER_B_EXECUCAO"] = execucao
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/evals", "-q", "-rs", "--tb=no", "-p", "no:cacheprovider",
         "-p", "contador_de_chamadas", "-m", "eval and llm_live"],
        cwd=_RAIZ, env=env, capture_output=True, text=True, check=False, timeout=300,
    )  # fmt: skip
    eventos = contador.read_text(encoding="utf-8").split() if contador.exists() else []
    return r, eventos


def test_without_opt_in_bedrock_br_with_simulated_credentials_makes_zero_provider_calls(
    tmp_path: Path,
) -> None:
    r, eventos = _suite_contada(tmp_path, execucao="", portoes=True)
    assert eventos == [], eventos  # nenhum provedor construido, nenhuma chamada
    assert r.returncode == 0, r.stdout[-3000:]
    assert re.search(r"\b21 skipped\b", r.stdout), r.stdout[-2000:]
    assert "Tier B so' roda no noturno ou na execucao manual" in r.stdout


def test_with_opt_in_the_same_counter_sees_the_provider(tmp_path: Path) -> None:
    # Controle: o contador funciona — com opt-in, o provedor e' construido (e falha sem os portoes, sem rede).
    r, eventos = _suite_contada(tmp_path, execucao="noturno", portoes=False)
    assert eventos and set(eventos) == {"construcao"}, eventos
    assert re.search(r"\b21 failed\b", r.stdout), r.stdout[-2000:]


def test_live_provider_refuses_before_construction_without_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    from maezo.runtime.inference import InferenceProvider

    construcoes: list[object] = []
    original = InferenceProvider.__init__

    def _contar(self: InferenceProvider, *a: Any, **k: Any) -> None:
        construcoes.append(self)
        original(self, *a, **k)

    monkeypatch.setattr(InferenceProvider, "__init__", _contar)
    monkeypatch.delenv("TIER_B_EXECUCAO")
    monkeypatch.setenv("MAEZO_ANTHROPIC_API_KEY", "chave-de-teste")
    monkeypatch.setenv("MAEZO_INFERENCE_PROVIDER", "bedrock_br")
    with pytest.raises(AssertionError, match=r"^LIVE CALL FAILED: SemOptInDeExecucao"):
        _vivo.provedor_vivo()
    assert construcoes == []


@pytest.mark.parametrize("execucao", ["", "outro", "NOTURNO-NAO"])
def test_skip_reason_without_opt_in_is_stable_whatever_provider_or_key(
    monkeypatch: pytest.MonkeyPatch, execucao: str
) -> None:
    from tests.evals import _regra_viva

    monkeypatch.setenv("TIER_B_EXECUCAO", execucao)
    for chave, provedor in (("", ""), ("chave-de-teste", "anthropic"), ("", "bedrock_br")):
        monkeypatch.setenv("MAEZO_ANTHROPIC_API_KEY", chave)
        monkeypatch.setenv("MAEZO_INFERENCE_PROVIDER", provedor)
        assert _regra_viva.motivo_do_pulo() == _regra_viva.MOTIVO_SEM_OPT_IN


def test_ci_nightly_tier_b_step_declares_the_opt_in() -> None:
    assert _passo("Nightly eval run — Tier B")["env"]["TIER_B_EXECUCAO"] == "noturno"
    assert "TIER_B_EXECUCAO" not in (_passo("Nightly eval run — Tier A").get("env") or {})


def test_preflight_without_opt_in_is_broken_and_calls_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("TIER_B_EXECUCAO")
    monkeypatch.setenv("MAEZO_ANTHROPIC_API_KEY", "chave-de-teste")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "s.md"))
    p = _Provedor()
    assert pf.main([], fabrica=lambda: p) == 1 and p.chamadas == 0
    saida = capsys.readouterr().out + (tmp_path / "s.md").read_text(encoding="utf-8")
    assert "QUEBRADO: preflight sem opt-in de execucao" in saida


def test_summary_without_opt_in_is_broken(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # Um noturno que esqueceu TIER_B_EXECUCAO nao pode virar "NAO RODOU" verde.
    monkeypatch.delenv("TIER_B_EXECUCAO")
    saida, resumo, _ = _rodar(monkeypatch, tmp_path, ["pulado"] * 21, 0, chave=False, bloqueante="false")
    assert saida == 1 and "QUEBRADO: sem opt-in de execucao" in resumo and "NAO RODOU" not in resumo


def test_manual_not_run_headline_is_coherent() -> None:
    c = tb.Contagem(casos=[("t::c", tb.PULADO)])
    v = tb.decidir(0, c, chave_presente=False, bloqueante=False, execucao="manual")
    assert v.saida == 0 and v.manchete.startswith("Tier B NAO RODOU na execucao manual")
    assert "nao roda no CI" not in v.manchete


def test_live_call_failures_do_not_swallow_other_failures(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "summary.md"))
    monkeypatch.setenv("MAEZO_ANTHROPIC_API_KEY", "chave-de-teste")
    junit = tmp_path / "j.xml"
    casos = (
        "".join(
            f'<testcase classname="t" name="vivo{i}"><failure message="AssertionError: LIVE CALL FAILED: '
            f'InferenceConfigError"/></testcase>'
            for i in range(2)
        )
        + '<testcase classname="t" name="vazou">'
        + '<failure message="AssertionError: vazou CPF na narrativa"/></testcase>'
    )
    junit.write_text(f"<testsuites><testsuite>{casos}</testsuite></testsuites>", encoding="utf-8")
    assert tb.main(["--junit", str(junit), "--codigo", "1"]) == 1
    resumo = (tmp_path / "summary.md").read_text(encoding="utf-8")
    assert (
        "QUEBRADO: 2 caso(s) com LIVE CALL FAILED (InferenceConfigError) e 1 caso(s) com outra falha"
        in resumo
    )
    assert "| `t::vazou` | falha |" in resumo and "| `t::vivo0` | LIVE CALL FAILED |" in resumo
    assert "live call failed 2 · falha 1" in resumo


def test_summary_rule_is_imported_not_copied_and_needs_no_maezo() -> None:
    from tests.evals import _regra_viva

    assert tb.chave_presente is _regra_viva.credencial_viva_presente
    assert _vivo.credencial_viva_presente is _regra_viva.credencial_viva_presente
    assert "PROVEDORES_DE_TESTE" not in (_RAIZ / "scripts/ci/tier_b_resumo.py").read_text(encoding="utf-8")
    codigo = (
        f"import sys; sys.path.insert(0, {str(_RAIZ)!r}); sys.modules['maezo'] = None; "
        "import scripts.ci.tier_b_resumo as t; print(t.chave_presente() in (True, False))"
    )
    r = subprocess.run(
        [sys.executable, "-I", "-c", codigo], cwd=_RAIZ, capture_output=True, text=True, timeout=60
    )
    assert r.returncode == 0 and r.stdout.strip() == "True", r.stderr[-2000:]


def test_manual_run_refuses_an_output_inside_the_repository(tmp_path: Path) -> None:
    assert manual.saida_recusada(_RAIZ / "saida-tier-b") is not None
    assert manual.saida_recusada(_RAIZ) is not None
    assert manual.saida_recusada(tmp_path / "fora") is None


def test_manual_run_drops_inherited_uv_variables(tmp_path: Path) -> None:
    env = manual.ambiente_da_execucao(
        {"UV_PROJECT_ENVIRONMENT": "/x", "UV_INDEX_URL": "y", "HOME": "/h"}, tmp_path
    )
    assert not [k for k in env if k.startswith("UV_")] and env["HOME"] == "/h"
    assert env["TIER_B_EXECUCAO"] == "manual"


def test_manual_run_states_the_effective_region(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from maezo.runtime.inference.excecao_regional import ENV_PHI_EXCECAO_REGIONAL, ROTA_EXCECAO_US

    monkeypatch.delenv(ENV_PHI_EXCECAO_REGIONAL, raising=False)
    assert manual.regiao_efetiva() == "sa-east-1"
    monkeypatch.setenv(ENV_PHI_EXCECAO_REGIONAL, ROTA_EXCECAO_US)
    assert manual.regiao_efetiva().startswith("us-east-1 (EXCECAO REGIONAL")

    class _Feito:
        returncode = 0

    monkeypatch.setattr(manual, "problemas_do_ambiente", lambda env, *, credencial_aws: [])
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Feito())
    assert manual.main(["--saida", str(tmp_path / "saida")]) == 0
    resumo = (tmp_path / "saida" / "resumo.md").read_text(encoding="utf-8")
    assert resumo.startswith("## Tier B — execucao manual — regiao efetiva da zona PHI: us-east-1 (EXCECAO")


def test_manual_run_drops_inherited_python_and_pytest_variables(tmp_path: Path) -> None:
    env = manual.ambiente_da_execucao(
        {"PYTEST_ADDOPTS": "-rA -s", "PYTHONPATH": "/x", "PYTHONSTARTUP": "/y", "HOME": "/h"},
        tmp_path / "r.md",
    )
    assert not any(k.startswith(("PYTHON", "PYTEST_")) for k in env) and env["HOME"] == "/h"
