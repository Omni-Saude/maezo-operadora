"""E-SF-08 / EP-REG-20b — o relogio de supressao (SP-OP-SUPP-001, §4c) pula feriado NACIONAL.

Calendario = `maezo.platform.calendario` (R-005/DEC-02), sem UF: feriado estadual/municipal, Sexta-feira da
Paixao e ponto facultativo contam como uteis — vencimento nunca posterior ao de um calendario completo.
Voltar ao calendario seg-sex deixa `test_supp_deadline_skips_national_holiday` vermelho.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from maezo.gateway.vendor_suppression import EscalationClock, add_business_days
from maezo.platform.calendario import FUSO_NEGOCIO, eh_dia_util


def _utc(ano: int, mes: int, dia: int) -> datetime:
    return datetime(ano, mes, dia, 10, 0, tzinfo=UTC)


def test_supp_deadline_skips_national_holiday() -> None:
    # Sexta 09/10/2026 + 1 dia util: segunda 12/10 e' Nossa Senhora Aparecida (Lei 6.802/1980) -> terca 13/10.
    assert add_business_days(_utc(2026, 10, 9), 1) == _utc(2026, 10, 13)
    # Registro nascido quarta 07/10/2026: com seg-sex o 15o dia util seria 28/10; com o feriado, 29/10.
    clock = EscalationClock.compute(_utc(2026, 10, 7))
    assert clock.t_deadline == _utc(2026, 10, 29)
    assert clock.t_escalonado == _utc(2026, 10, 26)
    assert clock.t_lembrete == _utc(2026, 10, 19)
    # Quinta 19/11/2026 + 1: sexta 20/11 e' Consciencia Negra (Lei 14.759/2023) -> segunda 23/11.
    assert add_business_days(_utc(2026, 11, 19), 1) == _utc(2026, 11, 23)


def test_supp_calendar_is_national_only_and_conservative() -> None:
    assert add_business_days(_utc(2026, 7, 8), 1) == _utc(2026, 7, 9)  # 09/07 e' so' estadual (SP): conta
    assert add_business_days(_utc(2026, 4, 2), 1) == _utc(2026, 4, 3)  # Sexta-feira da Paixao: municipal
    assert add_business_days(_utc(2026, 2, 13), 1) == _utc(2026, 2, 16)  # Carnaval: ponto facultativo


def test_supp_clock_never_lands_on_a_non_business_day() -> None:
    inicio = _utc(2026, 1, 1)
    for n in range(0, 730, 3):
        clock = EscalationClock.compute(inicio + timedelta(days=n))
        for marco in (clock.t_lembrete, clock.t_escalonado, clock.t_deadline):
            assert eh_dia_util(marco.astimezone(FUSO_NEGOCIO).date())
