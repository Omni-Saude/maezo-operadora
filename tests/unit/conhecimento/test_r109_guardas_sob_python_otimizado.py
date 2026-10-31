"""R-109 (F-150): guardas do catalogo de procedimentos sob `python -O`.

`python -O` remove `assert`. Cada caso roda o sitio convertido num interpretador `-O` de verdade e
confere que a guarda explicita AINDA dispara, com o mesmo tipo de excecao de antes (as invariantes
internas seguem `AssertionError`, que `PROGRAMMING_ERRORS` re-levanta; trocar por `RuntimeError` a
reclassificaria como falha de dependencia externa — ver `maezo.runtime.dependency_failures`).
"""

from __future__ import annotations

import subprocess
import sys
import textwrap


def _sob_python_otimizado(codigo: str) -> str:
    resultado = subprocess.run(
        [sys.executable, "-O", "-c", textwrap.dedent(codigo)],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert resultado.returncode == 0, resultado.stderr
    # So' a ultima linha: o import pode logar no stdout (structlog) antes do `print`.
    return resultado.stdout.strip().splitlines()[-1]


def test_o_interpretador_do_teste_remove_assert() -> None:
    """Nao-vacuidade: sob `-O` um `assert False` NAO dispara — e' isso que a conversao cobre."""
    assert _sob_python_otimizado("assert False\nprint('removido')") == "removido"


def test_lote_sem_transporte_dispara_sob_otimizacao() -> None:
    saida = _sob_python_otimizado(
        """
        import asyncio
        from types import SimpleNamespace
        from maezo.conhecimento.catalogo_procedimentos import BuscaDeProcedimentos

        try:
            asyncio.run(BuscaDeProcedimentos._lote_com_tentativas(SimpleNamespace(transporte=None), ["x"]))
        except Exception as exc:
            print(type(exc).__name__)
        """
    )
    assert saida == "AssertionError"
