"""E-SF-07 (TB-E-14-a): alarme de estouro le' so' o relogio canonico, com teto nomeado e grupo responsavel."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from maezo.gateway.commercial_t0_ledger import EventoCandidato
from maezo.tools.workers.rn413_contracting_clock import (
    EstadoDoRelogio,
    RelogioRecusado,
    estado_do_relogio,
    itens_de_escalonamento,
)

D = "4" * 64
INICIO = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)


def _estado() -> EstadoDoRelogio:
    return estado_do_relogio(
        [EventoCandidato(D, "party_submit", INICIO)], proponente_digest=D, agora=INICIO + timedelta(days=21)
    )


@pytest.mark.parametrize("estagio", ["pagamento", "ativacao", "5", "", None])
def test_overrun_requires_named_ceiling(estagio: object) -> None:
    with pytest.raises(RelogioRecusado) as e:
        itens_de_escalonamento(_estado(), estagio=estagio, grupo_responsavel="especialista_comercial")  # type: ignore[arg-type]
    assert e.value.code == "OVERRUN_WITHOUT_NAMED_CEILING"


@pytest.mark.parametrize("grupo", ["", None, "Especialista", "maria silva", "grupo-com-hifen", "x" * 65])
def test_overrun_requires_responsible_group(grupo: object) -> None:
    with pytest.raises(RelogioRecusado) as e:
        itens_de_escalonamento(_estado(), estagio="cadastro", grupo_responsavel=grupo)  # type: ignore[arg-type]
    assert e.value.code == "RESPONSIBLE_QUEUE_REQUIRED"


def test_overrun_alarm_reads_canonical_clock() -> None:
    proprio = EstadoDoRelogio(D, INICIO, 21, (15, 20), False, True, 0, object())
    with pytest.raises(RelogioRecusado) as e:
        itens_de_escalonamento(proprio, estagio="cadastro", grupo_responsavel="especialista_comercial")
    assert e.value.code == "SECOND_CLOCK_FORBIDDEN"
    itens = itens_de_escalonamento(_estado(), estagio="cadastro", grupo_responsavel="especialista_comercial")
    assert [i.dia for i in itens] == [15, 20]
    assert all(i.grupo_responsavel == "especialista_comercial" for i in itens)
