"""R-109 (F-150): guardas dos agentes sob `python -O`.

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


def test_texto_lista_sem_pedido_dispara_sob_otimizacao() -> None:
    saida = _sob_python_otimizado(
        """
        from datetime import date
        from maezo.agents.lucas.graph import texto_lista

        try:
            texto_lista({}, {}, date(2026, 10, 30))
        except Exception as exc:
            print(type(exc).__name__)
        """
    )
    assert saida == "AssertionError"


def test_invariantes_de_import_da_helena_seguem_valendo_sob_otimizacao() -> None:
    """As duas invariantes de modulo (DL-0080 e saidas neutras) carregam sob `-O` sem disparar."""
    saida = _sob_python_otimizado(
        """
        from maezo.agents.helena import graph

        print(graph.HISTORICO_JANELA_HORAS == graph.MEMORIA_CLINICA_JANELA_HORAS)
        """
    )
    assert saida == "True"
