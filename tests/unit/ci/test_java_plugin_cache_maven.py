"""O cache Maven do job `java-plugin` (ci.yml) muda com os poms E com a versao do Maven do runner.

Por que (21/11/2026)
--------------------
O `maven-install-plugin` (e os demais plugins sem versao nos poms) vem do binding padrao do Maven do runner:
3.9.12 e anteriores ligam o 3.1.2; 3.9.13+ ligam o 3.1.4. O `setup-java` com `cache: maven` chaveava so' pelos
poms e, num hit, nunca regrava o cache ("not saving cache"). Com o cache salvo pelo Maven antigo, TODO run
baixava da Central o install-plugin 3.1.4 e as dependencias dele (10 artefatos, medido localmente com
3.9.11 -> 3.9.14) — e a main ficou vermelha com HTTP 429 da Central (run 44882928416) e com "could not find
artifact" (run 44871521357), os dois no maven-install-plugin:3.1.4.

O `install` e' de fato usado: o primeiro passo roda `mvn ... install` para o `read-provider` resolver
`br.com.maezo:human-command-engine:1.0.0` (escopo provided) do repositorio local.

Contrato fixado aqui: a chave inclui os dois poms e SO' o numero da versao do Maven (Java/SO/locale/caminho
nao mudam a chave); o arquivo e' gerado ANTES do setup-java, dentro de um `target/` ignorado pelo git (nunca
versionado, nao suja o workspace); nenhum `-o`/`--offline` (dependencia nova continua resolvida, ou falha
clara, nunca mascarada).
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

_RAIZ = Path(__file__).resolve().parents[3]
_CI = _RAIZ / ".github" / "workflows" / "ci.yml"
_ARQUIVO_VERSAO = "src/maezo/portal/engine/java/target/maven-versao-ci.txt"
_POMS = ("src/maezo/portal/engine/java/pom.xml", "src/maezo/portal/engine/read-provider/pom.xml")


def _passos() -> list[dict[str, Any]]:
    jobs: dict[str, Any] = yaml.safe_load(_CI.read_text(encoding="utf-8"))["jobs"]
    passos: list[dict[str, Any]] = jobs["java-plugin"]["steps"]
    return passos


def _indice_setup_java(passos: list[dict[str, Any]]) -> int:
    indices = [i for i, p in enumerate(passos) if str(p.get("uses", "")).startswith("actions/setup-java@")]
    assert len(indices) == 1, indices
    return indices[0]


def test_maven_cache_key_covers_both_poms_and_the_runner_maven_version() -> None:
    passos = _passos()
    setup = passos[_indice_setup_java(passos)]["with"]
    assert setup["cache"] == "maven"
    caminhos = [c.strip() for c in str(setup["cache-dependency-path"]).splitlines() if c.strip()]
    assert sorted(caminhos) == sorted([*_POMS, _ARQUIVO_VERSAO]), caminhos
    for pom in _POMS:
        assert (_RAIZ / pom).is_file(), pom


def test_the_maven_version_file_is_written_before_setup_java_and_checked() -> None:
    passos = _passos()
    i_setup = _indice_setup_java(passos)
    geradores = [i for i, p in enumerate(passos) if _ARQUIVO_VERSAO in str(p.get("run", ""))]
    assert len(geradores) == 1 and geradores[0] < i_setup, (geradores, i_setup)
    run = str(passos[geradores[0]]["run"])
    assert "mvn -v" in run and "set -o pipefail" in run
    assert f'"$GITHUB_WORKSPACE/{_ARQUIVO_VERSAO}"' in run  # relativo ao workspace, como o setup-java le
    assert "{print $3}" in run  # so' o numero da versao, nao a linha inteira do `mvn -v`
    assert r"grep -qE '^[0-9]+\.[0-9]+\.[0-9]+$'" in run, run  # versao vazia/invalida falha o passo


def test_the_version_file_is_ignored_by_git_and_never_tracked() -> None:
    ignorado = subprocess.run(["git", "check-ignore", "-q", _ARQUIVO_VERSAO], cwd=_RAIZ, check=False)
    assert ignorado.returncode == 0, "o arquivo gerado tem de cair num caminho ignorado pelo git"
    rastreado = subprocess.run(
        ["git", "ls-files", "--", _ARQUIVO_VERSAO], cwd=_RAIZ, capture_output=True, text=True, check=True
    )
    assert rastreado.stdout.strip() == ""


def test_java_plugin_maven_commands_never_run_offline() -> None:
    comandos = [str(p.get("run", "")) for p in _passos()]
    assert any("mvn -B" in c for c in comandos)
    for c in comandos:
        for linha in c.replace("\\\n", " ").splitlines():  # junta continuacoes `\` (flag na linha seguinte)
            if re.search(r"\bmvn\b", linha):
                # `-o`, `--offline` e flags curtas combinadas (`-Bo`, `-oB`); `-Dx=...`/`-Px` nao contam.
                assert not re.search(r"(^|\s)(--offline|-(?![DP])[A-Za-z]*o[A-Za-z]*)(\s|$|[\"'])", linha), (
                    linha
                )


# ------------------- contagem do que veio da Central (so' leitura, `if: always()`, nunca falha o job)

_PASSO_CONTAGEM = "Artefatos Maven baixados neste run (so' leitura)"
_PASSO_MARCADOR = "Marcador de inicio (contagem de artefatos baixados)"


def _indice(passos: list[dict[str, Any]], nome: str) -> int:
    indices = [i for i, p in enumerate(passos) if p.get("name") == nome]
    assert len(indices) == 1, (nome, indices)
    return indices[0]


def test_marker_is_after_cache_restore_and_before_the_first_maven_command() -> None:
    passos = _passos()
    i_marcador = _indice(passos, _PASSO_MARCADOR)
    primeiro_mvn = min(i for i, p in enumerate(passos) if re.search(r"\bmvn -B\b", str(p.get("run", ""))))
    assert _indice_setup_java(passos) < i_marcador < primeiro_mvn


def test_count_step_is_last_read_only_and_never_fails_the_job() -> None:
    passos = _passos()
    passo = passos[_indice(passos, _PASSO_CONTAGEM)]
    assert passos[-1] is passo
    assert passo["if"] == "always()" and passo["continue-on-error"] is True
    run = str(passo["run"])
    assert "-cnewer" in run and "-newer " not in run  # ctime: o Maven grava o mtime com o Last-Modified
    assert '-not -path "$repo/br/com/maezo/*"' in run
    assert run.count("head -n 30") == 2
    assert not re.search(r"\b(rm|mv|cp|mvn|curl|wget)\b", run)  # so' leitura
    resumo = run[
        run.index("\n{\n") : run.index('\n} >> "$GITHUB_STEP_SUMMARY"')
    ]  # o bloco que vai ao summary
    assert "echo" in resumo
    assert "$repo" not in resumo and "$HOME" not in resumo and "$RUNNER_TEMP" not in resumo


def _find_gnu() -> bool:
    r = subprocess.run(["find", "--version"], capture_output=True, text=True, check=False)
    return r.returncode == 0 and "GNU" in r.stdout


_SO_FIND_GNU = pytest.mark.skipif(
    not _find_gnu(), reason="o passo usa `find -printf` (GNU), como no runner Linux"
)


@_SO_FIND_GNU
def test_count_step_counts_only_what_this_run_downloaded(tmp_path: Path) -> None:
    import os
    import time

    run = str(_passos()[_indice(_passos(), _PASSO_CONTAGEM)]["run"])
    repo = tmp_path / "home" / ".m2" / "repository"

    def artefato(rel: str) -> None:
        caminho = repo / rel
        caminho.parent.mkdir(parents=True, exist_ok=True)
        caminho.write_text("x", encoding="utf-8")
        os.utime(caminho, (1_600_000_000, 1_600_000_000))  # mtime antigo, como o Last-Modified da Central

    artefato("org/apache/maven/plugins/maven-install-plugin/3.1.2/maven-install-plugin-3.1.2.jar")  # do cache
    time.sleep(0.05)
    (tmp_path / "rt").mkdir()
    (tmp_path / "rt" / "maven-inicio.marker").touch()
    time.sleep(0.05)
    artefato("org/apache/maven/plugins/maven-install-plugin/3.1.4/maven-install-plugin-3.1.4.jar")
    artefato("org/apache/maven/plugins/maven-install-plugin/3.1.4/maven-install-plugin-3.1.4.pom")
    artefato("org/apache/maven/maven-parent/43/maven-parent-43.pom")
    artefato("br/com/maezo/human-command-engine/1.0.0/human-command-engine-1.0.0.jar")  # o proprio projeto
    resumo = tmp_path / "summary.md"
    env = {"PATH": os.environ["PATH"], "HOME": str(tmp_path / "home"), "RUNNER_TEMP": str(tmp_path / "rt")}
    r = subprocess.run(
        ["bash", "-e", "-c", run],
        env={**env, "GITHUB_STEP_SUMMARY": str(resumo)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert r.returncode == 0, r.stderr
    texto = resumo.read_text(encoding="utf-8")
    assert "baixadas da Central neste run: 2" in texto, texto
    assert "- org.apache.maven.plugins:maven-install-plugin:3.1.4" in texto
    assert "- org.apache.maven:maven-parent:43" in texto
    assert "3.1.2" not in texto and "br.com.maezo" not in texto and str(tmp_path) not in texto


@_SO_FIND_GNU
def test_count_step_without_local_repository_says_so_and_succeeds(tmp_path: Path) -> None:
    import os

    run = str(_passos()[_indice(_passos(), _PASSO_CONTAGEM)]["run"])
    env = {"PATH": os.environ["PATH"], "HOME": str(tmp_path), "RUNNER_TEMP": str(tmp_path)}
    r = subprocess.run(
        ["bash", "-e", "-c", run],
        env={**env, "GITHUB_STEP_SUMMARY": str(tmp_path / "s.md")},
        capture_output=True,
        text=True,
        check=False,
    )
    assert r.returncode == 0 and "contagem indisponivel" in r.stdout
