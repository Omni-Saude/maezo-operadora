"""Cerca: todo motivo de escalonamento que o roteiro de aceite exige e' um `motivo_categoria` do contrato
do SP-OP-ESCALATION-001 — e, portanto, um dos que `tests/integration/processes/
test_aceite_motivos_no_motor.py` leva ao motor real ate' `UT_TratarEscalonamento`.

Os motivos sao LIDOS de `aceite.ROTEIROS` (`tests/support/aceite_motivos.py`); o dominio e' a
constante do worker (`escalation._MOTIVOS_CONTRATUAIS`, re-derivada do contrato por outra cerca). Um
roteiro que ganhe um motivo fora do dominio faz este teste cair: o aceite pediria um escalonamento
que o worker degradaria (`motivo_categoria` omitido da notificacao) e que nenhuma prova no motor cobre.
O rotulo `cancelamento` NAO e' motivo de escalonamento: e' a abertura de SP-OP-CANCEL-001 (coberta por
`tests/integration/processes/test_sp_op_cancel_001.py`).
"""

from __future__ import annotations

from typing import Any

from maezo.qa.simulador.aceite import Passo, Segmento
from maezo.tools.workers.escalation import _MOTIVOS_CONTRATUAIS
from tests.support.aceite_motivos import (
    motivos_de_escalonamento,
    motivos_dos_roteiros,
    rotulo_do_cancelamento,
)


def _sem_cobertura(roteiros: Any = None) -> set[str]:
    return set(motivos_de_escalonamento(roteiros)) - set(_MOTIVOS_CONTRATUAIS)


def test_a_leitura_nao_e_vazia_e_inclui_o_rotulo_do_cancelamento() -> None:
    lidos = motivos_dos_roteiros()
    assert len(motivos_de_escalonamento()) >= 3, lidos
    assert rotulo_do_cancelamento() in lidos  # o aceite exige o pedido de cancelamento
    assert rotulo_do_cancelamento() not in _MOTIVOS_CONTRATUAIS


def test_todo_motivo_de_escalonamento_do_aceite_e_do_contrato() -> None:
    assert _sem_cobertura() == set(), "motivo do roteiro de aceite sem cobertura no motor"


def test_a_cerca_cai_com_um_motivo_ficticio_num_roteiro() -> None:
    roteiros = {
        "ficticio": (
            Segmento(nome="ficticio", passos=(Passo(texto="x", escalacao="motivo_ficticio_d5"),)),
            Segmento(nome="ficticio-2", passos=(Passo(texto="y", escalacao_um_de=("outro_ficticio",)),)),
        )
    }
    assert _sem_cobertura(roteiros) == {"motivo_ficticio_d5", "outro_ficticio"}
