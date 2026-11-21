"""`tests/support/materials_builder.py` com o relogio em SEGUNDO CHEIO (microssegundo 0).

Falha intermitente de 21/11/2026 (run 44864346394, `unit (gateway-platform)`): `_iso` usava `isoformat()` sem
`timespec`, que OMITE a fracao quando o microssegundo e' 0 (`...:12Z`); o leitor estrito do produto
(`read_profile.instant`, que exige a forma de `read_profile.utc()`, sempre 6 casas) recusava o registro com
`ProfileError: invalid closed read record`. Estes testes congelam o relogio nesse instante — sem eles o
defeito volta calado, porque so' aparece ~1 vez em 10^6 chamadas."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, tzinfo
from pathlib import Path

import pytest
from tests.support import materials_builder as mb

from maezo.gateway.human.read_profile import instant, utc

_SEGUNDO_CHEIO = datetime(2026, 11, 21, 4, 6, 12, 0, tzinfo=UTC)


def test_iso_keeps_six_fraction_digits_on_a_whole_second() -> None:
    texto = mb._iso(_SEGUNDO_CHEIO)
    assert texto == "2026-11-21T04:06:12.000000Z"
    assert texto == utc(_SEGUNDO_CHEIO)
    assert instant(texto) == _SEGUNDO_CHEIO  # o leitor estrito aceita


def test_build_bundle_with_a_whole_second_window_is_accepted(tmp_path: Path) -> None:
    janela = (_SEGUNDO_CHEIO - timedelta(minutes=5), _SEGUNDO_CHEIO + timedelta(hours=6))
    assert all(t.microsecond == 0 for t in janela)
    manifest, _ = mb.build_bundle(directory=tmp_path / "b", window=janela)
    assert manifest is not None


class _RelogioCongelado(datetime):
    @classmethod
    def now(cls, tz: tzinfo | None = None) -> datetime:  # type: ignore[override]
        return _SEGUNDO_CHEIO if tz is None else _SEGUNDO_CHEIO.astimezone(tz)


def test_build_bundle_default_clock_on_a_whole_second_is_accepted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # O caminho padrao (sem `window`) le `datetime.now(UTC)` do proprio modulo: congela-o no segundo cheio.
    monkeypatch.setattr(mb, "datetime", _RelogioCongelado)
    assert _RelogioCongelado.now(UTC).microsecond == 0
    manifest, _ = mb.build_bundle(directory=tmp_path / "b")
    assert manifest is not None
