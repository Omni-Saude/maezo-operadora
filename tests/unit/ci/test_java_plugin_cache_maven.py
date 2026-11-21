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
