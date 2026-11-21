"""`make test` nao seleciona caso `llm_live`; `make evals`, o noturno e a execucao manual seguem com os 21.

Por que (21/11/2026)
--------------------
`make test` (`pytest tests/ -m "not integration"`) incluia os casos `llm_live`: com uma chave Anthropic, ou
um `MAEZO_INFERENCE_PROVIDER` real nomeado (ex.: `bedrock_br` com sessao AWS do dev), o alvo "rapido, sem
engine" fazia chamadas REAIS a modelo. Agora `make test` exclui `llm_live`; os casos ao vivo ficam no
`make evals` (golden datasets, Tier A + Tier B) e no Tier B do noturno (`ci.yml`, `-m "eval and llm_live"`,
a mesma selecao da execucao manual na zona PHI do dev).

Como prova
----------
As expressoes `-m` sao LIDAS do Makefile, do ci.yml e do script manual (`scripts/dev/tier_b_manual.py`,
o comando que ele de fato roda) — nao copiadas — e o pytest COLETA de verdade com
`-m "(<expressao>) and llm_live"`: a coleta nao depende de chave nem do opt-in (o pulo e' em tempo de
execucao).
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

from scripts.dev import tier_b_manual as manual

_RAIZ = Path(__file__).resolve().parents[3]
_MAKEFILE = _RAIZ / "Makefile"
_CI = _RAIZ / ".github" / "workflows" / "ci.yml"
#: Os casos ao vivo do Tier B hoje (tests/evals/README.md). Piso, nao teto: caso novo nao quebra o teste.
_TIER_B_HOJE = 21


def _selecao_do_alvo(alvo: str) -> tuple[list[str], str]:
    """(caminhos, expressao -m) do pytest na receita `alvo:` do Makefile."""
    receita = re.search(rf"^{re.escape(alvo)}:.*\n\t(.+)$", _MAKEFILE.read_text(encoding="utf-8"), re.M)
    assert receita, f"alvo `{alvo}` sumiu do Makefile"
    return _selecao(receita.group(1))


def _selecao(linha: str) -> tuple[list[str], str]:
    comando = shlex.split(linha.split("||")[0])
    assert comando[1:3] == ["-m", "pytest"], comando
    args = comando[3:]
    expressao = args[args.index("-m") + 1]
    caminhos = [a for a in args if not a.startswith("-") and a != expressao]
    return caminhos, expressao


def _vivos_coletados(caminhos: list[str], expressao: str) -> list[str]:
    """Ids que o pytest COLETA com `-m "(expressao) and llm_live"` (sem rodar nada)."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PYTEST_", "PYTHON"))}
    r = subprocess.run(
        [sys.executable, "-m", "pytest", *caminhos, "--collect-only", "-q", "-p", "no:cacheprovider",
         "-m", f"({expressao}) and llm_live"],
        cwd=_RAIZ, env=env, capture_output=True, text=True, check=False, timeout=300,
    )  # fmt: skip
    assert r.returncode in (0, 5), r.stdout[-3000:] + r.stderr[-3000:]  # 5 = nenhum coletado
    return [linha for linha in r.stdout.splitlines() if "::" in linha]


def test_make_test_selects_no_llm_live_case() -> None:
    caminhos, expressao = _selecao_do_alvo("test")
    assert caminhos == ["tests/"], caminhos
    assert _vivos_coletados(caminhos, expressao) == []


def test_make_evals_the_nightly_and_the_manual_run_still_select_the_live_cases(tmp_path: Path) -> None:
    noturno = re.search(
        r'pytest tests/evals [^\n]*-m "eval and llm_live"[^\n]*', _CI.read_text(encoding="utf-8")
    )
    assert noturno, "o passo do Tier B no evals-nightly mudou de forma"
    tier_b = _vivos_coletados(*_selecao(".venv/bin/python -m " + noturno.group(0)))
    assert len(tier_b) >= _TIER_B_HOJE, tier_b
    evals = _vivos_coletados(*_selecao_do_alvo("evals"))
    assert sorted(evals) == sorted(tier_b)
    _, suite, _ = manual.comandos(tmp_path / "j.xml")  # o comando pytest que a execucao manual roda
    manual_vivos = _vivos_coletados(*_selecao(" ".join(shlex.quote(a) for a in ["python", *suite[1:]])))
    assert sorted(manual_vivos) == sorted(tier_b)


def test_the_selection_parser_reads_the_real_recipes() -> None:
    assert _selecao_do_alvo("test") == (["tests/"], "not integration and not llm_live")
    assert _selecao_do_alvo("evals") == (["tests/evals"], "eval")
