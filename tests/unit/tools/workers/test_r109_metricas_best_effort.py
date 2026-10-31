"""R-109 (F-150): as falhas de metrica do `WorkerBase` deixam de ser engolidas em silencio (B110).

Seguem best-effort (nunca derrubam a execucao nem disparam retry), mas vao ao log em `debug` so'
com o tipo do erro — nunca variaveis de processo (ADR-0006).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
import structlog

from maezo.tools.workers.base import WorkerBase


class _Contador(WorkerBase):
    def __init__(self, falhar: bool = False) -> None:
        super().__init__(topic="r109-metricas", max_retries=1, retry_backoff=0.0)
        self.chamadas = 0
        self.falhar = falhar

    def execute(self, process_vars: dict[str, Any]) -> dict[str, Any]:
        self.chamadas += 1
        if self.falhar:
            raise ValueError("cpf-12345678900")
        return {"ok": True}


def _falhas_de_metrica(logs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [e for e in logs if e.get("event") == "worker_metric_record_failed"]


def test_metrica_de_sucesso_que_falha_vai_ao_log_e_nao_reexecuta() -> None:
    worker = _Contador()
    with (
        patch("maezo.platform.observability.record_worker_execution", side_effect=RuntimeError("pii-x")),
        structlog.testing.capture_logs() as logs,
    ):
        assert worker.run({"beneficiario": "pii-y"}) == {"ok": True}
    assert worker.chamadas == 1
    assert _falhas_de_metrica(logs) == [
        {
            "event": "worker_metric_record_failed",
            "topic": "r109-metricas",
            "error_type": "RuntimeError",
            "log_level": "debug",
        }
    ]
    assert "pii-x" not in repr(_falhas_de_metrica(logs))


def test_metrica_de_erro_que_falha_vai_ao_log_e_o_erro_original_segue() -> None:
    worker = _Contador(falhar=True)
    with (
        patch("maezo.platform.observability.record_worker_error", side_effect=OSError("pii-z")),
        structlog.testing.capture_logs() as logs,
        pytest.raises(Exception) as caught,
    ):
        worker.run({})
    assert "cpf-12345678900" in str(caught.value) or isinstance(caught.value, ValueError)
    falhas = _falhas_de_metrica(logs)
    assert [f["error_type"] for f in falhas] == ["OSError"]
    assert "pii-z" not in repr(falhas)
