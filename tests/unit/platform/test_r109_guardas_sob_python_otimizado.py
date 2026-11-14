"""R-109 (F-150): guardas de plataforma sob `python -O`.

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


def test_arn_invalido_e_recusa_tipada_sob_otimizacao() -> None:
    saida = _sob_python_otimizado(
        """
        from maezo.platform.engine_bootstrap.owner_local_v2_contracts import role_parts

        try:
            role_parts("arn:aws:iam::nao-e-conta:user/x")
        except Exception as exc:
            print(type(exc).__name__, exc.args[0])
        """
    )
    assert saida == "ContractError INVALID_BODY"


def test_arn_valido_segue_aceito() -> None:
    from maezo.platform.engine_bootstrap.owner_local_v2_contracts import role_parts

    conta, _, _ = role_parts("arn:aws:iam::123456789012:role/svc/maezo-role")
    assert conta == "123456789012"
