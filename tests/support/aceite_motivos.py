"""Os motivos de escalonamento que o roteiro de aceite exige, LIDOS da fonte (nunca digitados).

Test-only. Dois fatos, ambos extraidos do codigo:

* `motivos_dos_roteiros` — todo `escalacao=` / `escalacao_um_de=` dos `Passo`s de
  `maezo.qa.simulador.aceite.ROTEIROS` (menos `ESCALACAO_NENHUMA`).
* `rotulo_do_cancelamento` — o rotulo que o simulador grava quando a conversa abre
  SP-OP-CANCEL-001 em vez de SP-OP-ESCALATION-001 (`qa/simulador/composicao.py`, o literal do ramo
  `e_cancelamento` em `EscalonamentoSimulado(motivo=...)`). Esse motivo NAO e' um `motivo_categoria`
  do escalonamento: e' o processo de cancelamento. Lido por AST para que a troca do literal la' nao
  passe despercebida aqui.
"""

from __future__ import annotations

import ast
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import maezo.qa.simulador.composicao as _composicao
from maezo.qa.simulador.aceite import ESCALACAO_NENHUMA, ROTEIROS


def motivos_dos_roteiros(roteiros: Mapping[str, Sequence[Any]] | None = None) -> frozenset[str]:
    motivos: set[str] = set()
    for segmentos in (ROTEIROS if roteiros is None else roteiros).values():
        for segmento in segmentos:
            for passo in segmento.passos:
                if passo.escalacao and passo.escalacao != ESCALACAO_NENHUMA:
                    motivos.add(passo.escalacao)
                motivos.update(passo.escalacao_um_de)
    return frozenset(motivos)


def rotulo_do_cancelamento() -> str:
    arvore = ast.parse(Path(_composicao.__file__).read_text(encoding="utf-8"))
    achados = [
        no.value.body.value
        for no in ast.walk(arvore)
        if isinstance(no, ast.keyword)
        and no.arg == "motivo"
        and isinstance(no.value, ast.IfExp)
        and isinstance(no.value.test, ast.Name)
        and no.value.test.id == "e_cancelamento"
        and isinstance(no.value.body, ast.Constant)
        and isinstance(no.value.body.value, str)
    ]
    assert len(achados) == 1, f"esperava UM rotulo de cancelamento em composicao.py, achei {achados}"
    return str(achados[0])


def motivos_de_escalonamento(roteiros: Mapping[str, Sequence[Any]] | None = None) -> frozenset[str]:
    """Os motivos dos roteiros que abrem SP-OP-ESCALATION-001 (todos menos o rotulo do cancelamento)."""
    return motivos_dos_roteiros(roteiros) - {rotulo_do_cancelamento()}
