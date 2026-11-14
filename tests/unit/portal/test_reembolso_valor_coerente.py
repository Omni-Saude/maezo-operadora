"""Teste do dono (02/11/2026): "Aprovar" paga EXATAMENTE o valor pedido; para pagar menos, so'
"Aprovar parcial" (0 < valor < pedido). O valor pedido vem do motor (contexto da tarefa), nunca do
navegador."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from maezo.portal.api import tasks

PEDIDO = 25000


class _Servico:
    def __init__(self, pedido: int | None, *, le: bool = True) -> None:
        self._pedido = pedido
        self._le = le

    async def read_reembolso_context(self, *, session_secret: str, task_id: str, render: Any) -> Any:
        if not self._le:  # leitura recusada (tarefa de outro, motor fora): render nunca chamado
            return None
        valor = None if self._pedido is None else SimpleNamespace(as_int=lambda: self._pedido)
        return render(SimpleNamespace(valor_solicitado_cents=valor))


@pytest.mark.parametrize(
    ("decisao", "valor", "esperado"),
    [
        ("APROVAR", PEDIDO, True),
        ("APROVAR", 20000, False),  # o caso do teste: aprovar com menos virava aprovacao integral
        ("APROVAR", 30000, False),
        ("APROVAR_PARCIAL", 20000, True),
        ("APROVAR_PARCIAL", PEDIDO, False),
        ("APROVAR_PARCIAL", 30000, False),
        ("APROVAR_PARCIAL", 0, False),
        ("APROVAR", True, False),
        ("APROVAR", "25000", False),
        ("APROVAR", None, False),
    ],
)
async def test_aprovar_paga_o_pedido_e_parcial_paga_menos(
    decisao: str, valor: object, esperado: bool
) -> None:
    assert await tasks._valor_coerente(_Servico(PEDIDO), "s", "t", decisao, valor) is esperado


@pytest.mark.parametrize("servico", [_Servico(None), _Servico(PEDIDO, le=False)])
async def test_sem_o_valor_pedido_do_motor_recusa(servico: _Servico) -> None:
    assert await tasks._valor_coerente(servico, "s", "t", "APROVAR", PEDIDO) is False
