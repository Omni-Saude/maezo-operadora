"""E-SF-07 (EP-REG-09a): depois do corte, so' recusa e encaminha a pessoa; nada decide."""

from __future__ import annotations

import ast
import inspect
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from maezo.gateway import commercial_pos_dia_25 as pd
from maezo.gateway.commercial_pos_dia_25 import PosCorteRecusado, recusar_acao_adversa
from maezo.gateway.commercial_t0_ledger import EventoCandidato
from maezo.tools.workers.rn413_contracting_clock import EstadoDoRelogio, RelogioRecusado, estado_do_relogio

D = "3" * 64
INICIO = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
_RAIZ = Path(pd.__file__).resolve().parents[3]


def _estado(dia: int) -> EstadoDoRelogio:
    return estado_do_relogio(
        [EventoCandidato(D, "prospect_save_com_valores", INICIO)],
        proponente_digest=D,
        agora=INICIO + timedelta(dia),
    )


def test_no_autonomous_termination_after_day25() -> None:
    with pytest.raises(PosCorteRecusado) as e:
        recusar_acao_adversa(_estado(26), acao="rescisao")
    assert e.value.code == "rescisao_autonoma_recusada" and e.value.encaminhamento == "pessoa"


def test_no_dlp_allegation_after_day25() -> None:
    with pytest.raises(PosCorteRecusado) as e:
        recusar_acao_adversa(_estado(25), acao="alegacao_dlp")
    assert e.value.code == "dlp_barred_rn413_5_6"


@pytest.mark.parametrize("acao", ["bloqueio_de_uso", "cobranca_retroativa"])
def test_auto_effectivated_contract_never_blocked_for_payment(acao: str) -> None:
    with pytest.raises(PosCorteRecusado) as e:
        recusar_acao_adversa(_estado(30), acao=acao)
    assert e.value.code == "human_review_required"


def test_antes_do_corte_nao_recusa_e_nao_autoriza() -> None:
    assert recusar_acao_adversa(_estado(24), acao="alegacao_dlp") is None


def test_estado_de_outro_relogio_recusado() -> None:
    falso = EstadoDoRelogio(D, INICIO, 30, (15, 20, 23), True, True, 0, object())
    with pytest.raises(RelogioRecusado) as e:
        recusar_acao_adversa(falso, acao="rescisao")
    assert e.value.code == "SECOND_CLOCK_FORBIDDEN"


def test_acao_desconhecida_recusa() -> None:
    with pytest.raises(PosCorteRecusado) as e:
        recusar_acao_adversa(_estado(1), acao="rescindir_agora")
    assert e.value.code == "pos_corte_acao_desconhecida"
    assert "rescindir_agora" not in str(e.value)


def test_redacao_hipotese_nunca_conclusao() -> None:
    for fonte in (
        inspect.getsource(pd),
        (_RAIZ / "src/maezo/tools/workers/rn413_contracting_clock.py").read_text(),
    ):
        baixo = fonte.lower()
        for conclusao in ("contrato esta vigente", "contrato vigente.", "entrou em vigor", "esta em vigor"):
            assert conclusao not in baixo
    arvore = ast.parse(inspect.getsource(pd))
    funcoes = {n.name for n in ast.walk(arvore) if isinstance(n, ast.FunctionDef)}
    assert funcoes == {"__init__", "recusar_acao_adversa"}


def test_sem_importador_em_src() -> None:
    src = _RAIZ / "src" / "maezo"
    usos = [
        str(p.relative_to(_RAIZ))
        for p in src.rglob("*.py")
        if p.resolve() != Path(pd.__file__).resolve()
        and "commercial_pos_dia_25" in p.read_text(encoding="utf-8")
    ]
    assert usos == []
