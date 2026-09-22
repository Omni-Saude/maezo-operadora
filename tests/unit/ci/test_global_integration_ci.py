"""Behavioral fence for the unit/integration marker partition in CI."""

from __future__ import annotations

import shlex
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[3]
_WORKFLOW = _REPO_ROOT / ".github" / "workflows" / "ci.yml"


def _steps(job: str) -> list[dict[str, Any]]:
    workflow = yaml.safe_load(_WORKFLOW.read_text())
    return list(workflow["jobs"][job]["steps"])


def _step(job: str, name: str) -> dict[str, Any]:
    return next(step for step in _steps(job) if step.get("name") == name)


def _selector(job: str, step_name: str) -> tuple[str, str]:
    logical_commands = str(_step(job, step_name)["run"]).replace("\\\n", " ")
    for line in logical_commands.splitlines():
        # `_tokens` (tolerante) e nao `shlex.split`: desde 22/09/2026 os passos da lane de motor
        # real tambem carregam um comentario com apostrofo, e uma linha que nao tokeniza nunca e'
        # o comando do pytest — ignora-la e' a leitura certa, estourar ValueError nao e'.
        tokens = _tokens(line.split("|", 1)[0])
        if "pytest" in tokens:
            pytest_args = tokens[tokens.index("pytest") + 1 :]
        elif "scripts/ci/run_live_pytest.py" in tokens and "--" in tokens:
            pytest_args = tokens[tokens.index("--") + 1 :]
        else:
            continue
        roots = [token for token in pytest_args if not token.startswith("-")]
        marker_at = pytest_args.index("-m")
        assert roots, f"no collection root in {line!r}"
        return roots[0], pytest_args[marker_at + 1]
    raise AssertionError(f"no pytest selector found in {job}/{step_name}")


def _collect(root: str, marker: str) -> set[str]:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", root, "--collect-only", "-q", "-m", marker],
        cwd=_REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return {line for line in result.stdout.splitlines() if "::" in line}


def _shards(job: str = "unit") -> list[tuple[str, list[str]]]:
    workflow = yaml.safe_load(_WORKFLOW.read_text())
    return [
        (item["shard"], shlex.split(item["alvo"]))
        for item in workflow["jobs"][job]["strategy"]["matrix"]["include"]
    ]


def _tokens(linha: str) -> list[str]:
    """`shlex.split` tolerante: uma linha de COMENTARIO do passo com apostrofo ("e' uma lista")
    nao tokeniza e derrubava a cerca inteira com ValueError no CI (run 35694139974). Linha que
    nao tokeniza nao e' o comando do pytest — e' ignorada, nao e' erro."""
    try:
        return shlex.split(linha)
    except ValueError:
        return []


def _selector_do_shard(alvo: list[str]) -> tuple[list[str], str]:
    """O passo do shard escreve `${{ matrix.alvo }}` no lugar da raiz; aqui a raiz e' o `alvo`
    do proprio shard, e o marcador continua sendo lido do comando de verdade."""
    run = str(_step("unit", "Unit tests (shard) + coverage data")["run"]).replace("\\\n", " ")
    linha = next(line for line in run.splitlines() if "pytest" in _tokens(line.split("|", 1)[0]))
    tokens = shlex.split(linha)
    assert "${{" in linha and "matrix.alvo" in linha, linha
    # So' os argumentos DEPOIS de `pytest`: o comando e' `python -m pytest ...`, e o primeiro
    # `-m` da linha e' o do interpretador — le-lo devolvia "pytest" como marcador, e um
    # `-m pytest` nao coleta nada. O `_selector` original ja' fazia este recorte.
    pytest_args = tokens[tokens.index("pytest") + 1 :]
    return alvo, pytest_args[pytest_args.index("-m") + 1]


def _selector_do_shard_da_lane(alvo: list[str]) -> tuple[list[str], str]:
    """Idem `_selector_do_shard`, para os shards do job `integration` (22/09/2026).

    A raiz de colecao do passo e' `${{ matrix.alvo }}` — o workflow so' a resolve no runner, entao
    quem sabe a raiz de cada shard e' a MATRIZ. O marcador, esse, continua vindo do comando de
    verdade: se alguem trocar `integration and not chaos` por outra coisa no passo, esta cerca
    passa a medir a coisa nova, e a particao tem de continuar valendo para ela.

    O recorte e' depois do `--` (nao depois de "pytest") pelo mesmo motivo que levou o
    `_selector_do_shard` a recortar depois de "pytest": aqui o comando e'
    `... run_live_pytest.py run --expected ... -- <alvo> -q -m "<marcador>"`, e so' o que vem
    depois do `--` sao argumentos do pytest.
    """
    run = str(_step("integration", "Integration tests")["run"]).replace("\\\n", " ")
    linha = next(
        line for line in run.splitlines() if "scripts/ci/run_live_pytest.py" in _tokens(line.split("|", 1)[0])
    )
    tokens = shlex.split(linha)
    assert "${{" in linha and "matrix.alvo" in linha, linha
    pytest_args = tokens[tokens.index("--") + 1 :]
    return alvo, pytest_args[pytest_args.index("-m") + 1]


def _collect_args(args: list[str], marker: str) -> set[str]:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", *args, "--collect-only", "-q", "-m", marker],
        cwd=_REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return {line for line in result.stdout.splitlines() if "::" in line}


def _por_funcao(casos: set[str]) -> Counter[str]:
    """Conta casos por FUNCAO (`arquivo::teste`, sem o `[...]` do parametrize).

    Por medicao, nao por gosto: `test_wire_framing.py` gera ids de parametrize com bytes
    ALEATORIOS, diferentes a cada coleta — comparar ids exatos entre a coleta em serie e a dos
    shards fazia a cerca piscar por 3 casos que sao o mesmo teste. Contar por funcao detecta as
    duas coisas que importam do mesmo jeito: um diretorio que nenhum shard cobre (falta) e um
    arquivo em dois shards (a soma passa da serie).
    """
    return Counter(caso.split("[", 1)[0] for caso in casos)


def test_unit_shards_collect_no_integration_case_and_partition_the_serial_suite() -> None:
    """Os shards sao uma PARTICAO da suite em serie: completos, disjuntos, e sem caso de integracao.

    Antes de 22/09/2026 o job `quality` rodava `pytest tests/ -m "not integration"` num processo
    so'. Fatiar em shards cria um defeito novo possivel — um diretorio que nenhum shard cobre, ou
    dois shards cobrindo o mesmo — que a colecao em serie nunca teve. Este teste soma a colecao
    dos shards, funcao a funcao, e exige que a soma seja EXATAMENTE a colecao em serie.
    """
    integration_cases = _collect("tests", "integration")
    assert integration_cases
    marcador = None
    soma: Counter[str] = Counter()
    for shard, alvo in _shards():
        args, marcador = _selector_do_shard(alvo)
        casos = _collect_args(args, marcador)
        assert casos, f"shard {shard} nao coleta nada"
        assert casos.isdisjoint(integration_cases), f"shard {shard} coleta caso de integracao"
        soma += _por_funcao(casos)
    assert marcador is not None
    serie = _por_funcao(_collect("tests", marcador))
    faltando = serie - soma
    sobrando = soma - serie
    assert not faltando, f"funcoes que NENHUM shard cobre: {sorted(faltando)[:5]}"
    assert not sobrando, f"funcoes cobertas por MAIS de um shard (ou a mais): {sorted(sobrando)[:5]}"


def test_service_lanes_are_a_disjoint_complete_union_of_global_integration_cases() -> None:
    """As lanes de servico continuam uma particao completa e disjunta — agora com a lane de
    motor real em SHARDS (22/09/2026).

    Antes, `integration` era um job so' e a colecao dele era, por construcao, a lane inteira.
    Fatiar cria dois defeitos novos possiveis que a lane em serie nunca teve — um arquivo que
    NENHUM shard coleta (some da cobertura em silencio) e um arquivo em DOIS shards (roda duas
    vezes, contra dois motores) — e nenhum dos dois aparece numa uniao de conjuntos. Por isso a
    completude/disjuncao entre shards e' medida por CONTAGEM por funcao, como na cerca dos shards
    unitarios; a relacao com a lane `chaos` continua sendo de conjunto.
    """
    marcador = None
    soma: Counter[str] = Counter()
    normal: set[str] = set()
    for shard, alvo in _shards("integration"):
        args, marcador = _selector_do_shard_da_lane(alvo)
        casos = _collect_args(args, marcador)
        assert casos, f"shard {shard} da lane de motor real nao coleta nada"
        soma += _por_funcao(casos)
        normal |= casos
    assert marcador is not None
    serie = _por_funcao(_collect("tests", marcador))
    faltando = serie - soma
    sobrando = soma - serie
    assert not faltando, f"funcoes que NENHUM shard da lane coleta: {sorted(faltando)[:5]}"
    assert not sobrando, f"funcoes coletadas por MAIS de um shard da lane: {sorted(sobrando)[:5]}"

    chaos_root, chaos_marker = _selector("chaos", "Chaos tests (seam-level fault injection)")
    all_integration = _collect("tests", "integration")
    chaos = _collect(chaos_root, chaos_marker)

    assert normal
    assert chaos
    assert normal.isdisjoint(chaos)
    assert normal | chaos == all_integration

    outside_old_path = {case for case in all_integration if not case.startswith("tests/integration/")}
    assert outside_old_path, (
        "the regression proof became vacuous: no integration cases live outside the old path"
    )


def test_live_lane_publishes_junit_collection_and_checks_infrastructure_skips() -> None:
    run = str(_step("integration", "Integration tests")["run"])
    logical_run = " ".join(run.replace("\\\n", " ").split())
    assert "run_live_pytest.py run" in logical_run
    assert "--expected integration-collection.json" in logical_run
    assert "--evidence integration-execution.json" in logical_run
    assert "--junit integration-junit.xml" in logical_run
    assert "--validation integration-validation.json" in logical_run
    assert "-ra" in run

    guard = str(_step("integration", "Check for collectable integration tests")["run"])
    assert "run_live_pytest.py collect" in guard
    assert "integration-collection.json" in guard
    assert _selector("integration", "Check for collectable integration tests") == _selector(
        "integration", "Integration tests"
    )
    # A lane roda em shards: as DUAS etapas tem de coletar o mesmo `alvo` da matriz (senao o
    # manifesto da coleta nao bate com a execucao e o wrapper falha fechado), e o artefato tem
    # de carregar o shard no nome — com um nome so' por `github.sha` o upload de um shard
    # sobrescreveria/perderia o JUnit dos outros, que e' a unica evidencia legivel do vermelho.
    assert "${{ matrix.alvo }}" in logical_run, logical_run
    assert "${{ matrix.alvo }}" in " ".join(guard.split()), guard

    upload = _step("integration", "Upload integration test results")
    assert "${{ matrix.shard }}" in str(upload["with"]["name"])
    uploaded = str(upload["with"]["path"])
    assert "integration-junit.xml" in uploaded
    assert "integration-collection.json" in uploaded
    assert "integration-execution.json" in uploaded
    assert "integration-validation.json" in uploaded

    chaos_run = " ".join(
        str(_step("chaos", "Chaos tests (seam-level fault injection)")["run"]).replace("\\\n", " ").split()
    )
    assert "run_live_pytest.py run" in chaos_run
    assert "--expected chaos-collection.json" in chaos_run
    assert "--evidence chaos-execution.json" in chaos_run
    assert "--junit chaos-junit.xml" in chaos_run
    assert "--validation chaos-validation.json" in chaos_run
    assert _selector("chaos", "Check for collectable chaos tests") == _selector(
        "chaos", "Chaos tests (seam-level fault injection)"
    )
    chaos_upload = str(_step("chaos", "Upload chaos test results")["with"]["path"])
    assert "chaos-junit.xml" in chaos_upload
    assert "chaos-collection.json" in chaos_upload
    assert "chaos-execution.json" in chaos_upload
    assert "chaos-validation.json" in chaos_upload
