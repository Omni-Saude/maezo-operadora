"""R-109 (F-150): guardas e `except` triados do gateway sob `python -O`.

`python -O` remove `assert`. Cada caso roda o sitio convertido num interpretador `-O` de verdade e
confere que a guarda explicita AINDA dispara, com o mesmo tipo de excecao de antes (as invariantes
internas seguem `AssertionError`, que `PROGRAMMING_ERRORS` re-levanta; trocar por `RuntimeError` a
reclassificaria como falha de dependencia externa — ver `maezo.runtime.dependency_failures`).
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
import textwrap
from typing import Any

import pytest
import structlog

from maezo.gateway.capabilities.models import CapabilityContractError, CapabilityEnvelope, _parse
from maezo.gateway.external_cases.models import ExternalCaseError, Scope, parse
from maezo.gateway.native_installation_readback import ReadbackError, _one


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


def test_dimensao_ausente_dispara_sob_otimizacao() -> None:
    saida = _sob_python_otimizado(
        """
        from maezo.gateway.human.vendor_certification import DimensionInput, _s1

        try:
            _s1(DimensionInput())
        except Exception as exc:
            print(type(exc).__name__)
        """
    )
    assert saida == "AssertionError"


def test_vendor_submissions_sem_trilha_de_auditoria_e_recusa_tipada_sob_otimizacao() -> None:
    """Guarda `require`+`assert` fundida num `if` explicito: mesma recusa, mesmo motivo."""
    saida = _sob_python_otimizado(
        """
        import asyncio
        from types import SimpleNamespace
        from maezo.gateway.vendor_submissions import (
            VendorSubmissionError,
            VendorSubmissionMachine,
            VendorSubmissionRefusal,
        )

        async def _rodar():
            sem_trilha = SimpleNamespace(audit=None)
            await VendorSubmissionMachine._emit(sem_trilha, "t", None, decision="d", reason="r")

        try:
            asyncio.run(_rodar())
        except VendorSubmissionError as exc:
            print(type(exc).__name__, exc.args[0] == VendorSubmissionRefusal.AUDIT_UNAVAILABLE)
        """
    )
    assert saida == "VendorSubmissionError True"


class _Cursor:
    def __init__(self, *linhas: Any) -> None:
        self._linhas = list(linhas)

    def execute(self, query: str, params: tuple[Any, ...] = ()) -> None:
        return None

    def fetchone(self) -> Any:
        return self._linhas.pop(0) if self._linhas else None


def test_readback_exige_exatamente_uma_linha() -> None:
    assert _one(_Cursor((1,)), "q") == (1,)
    with pytest.raises(ReadbackError):
        _one(_Cursor(), "q")
    with pytest.raises(ReadbackError):
        _one(_Cursor((1,), (2,)), "q")


def test_parse_de_caso_externo_recusa_sem_contexto_do_fornecedor() -> None:
    """B110 triado: o defeito segue engolido por contrato, e a recusa sai sem `__context__`."""
    with pytest.raises(ExternalCaseError) as caught:
        parse(Scope, b'{"tenant": 1}')
    assert caught.value.code == "invalid"
    assert caught.value.__context__ is None
    assert caught.value.__cause__ is None


def test_parse_de_capacidade_recusa_sem_contexto_do_fornecedor() -> None:
    with pytest.raises(CapabilityContractError) as caught:
        _parse(CapabilityEnvelope, b"nao-e-json", None)
    assert caught.value.__context__ is None
    assert caught.value.__cause__ is None


def test_defeito_nao_declarado_da_postura_vai_ao_log_so_com_o_tipo() -> None:
    from maezo.gateway.commercial_legitimacy_guard import LegitimacyScreenGateway
    from tests.unit.gateway.test_commercial_legitimacy_guard import _execute, _flag_off, _gateway, _Store

    class _DefeitoMudo(LegitimacyScreenGateway):
        async def _posture(self, command: Any, scope: Any) -> None:
            raise RuntimeError("segredo-que-nao-pode-ir-ao-log")

    bugado, _, _ = _gateway(_DefeitoMudo, _Store())  # type: ignore[arg-type]
    off, _, _ = _flag_off()
    with structlog.testing.capture_logs() as logs:
        resultado = asyncio.run(_execute(bugado))
    assert resultado.wire_bytes == asyncio.run(_execute(off)).wire_bytes
    defeitos = [e for e in logs if e.get("event") == "commercial_legitimacy_posture_defect"]
    assert defeitos == [
        {
            "event": "commercial_legitimacy_posture_defect",
            "error_type": "RuntimeError",
            "log_level": "warning",
        }
    ]
    assert "segredo" not in repr(logs)
