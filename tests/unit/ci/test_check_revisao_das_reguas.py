"""A cerca da cadencia de revisao — e a prova de que ela consegue ficar vermelha (Frente 8.3).

Uma cerca de prazo que nunca foi vista reprovando e' indistinguivel de uma cerca que nao funciona:
as duas ficam verdes todo dia ate' o dia em que deveriam reprovar. Estes testes exercitam o
vencimento, o documento apagado e o piso de reguas obrigatorias.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest
from scripts.ci.check_revisao_das_reguas import _REGUAS_OBRIGATORIAS, carregar, main, verificar

_HOJE = date(2026, 9, 15)


def _regua(**over: Any) -> dict[str, Any]:
    base = {
        "id": "extracao",
        "documento": "README.md",
        "corpus": "spec",
        "dono": "medico auditor",
        "revisar_ate": "2026-12-15",
    }
    base.update(over)
    return base


def _completo(**over: Any) -> list[dict[str, Any]]:
    return [_regua(**over), _regua(id="regras-clinicas")]


def test_cadencia_em_dia_passa() -> None:
    assert verificar(_completo(), hoje=_HOJE) == []


def test_cadencia_vencida_reprova() -> None:
    """O RED que da sentido ao arquivo inteiro."""
    achados = verificar(_completo(revisar_ate="2026-09-14"), hoje=_HOJE)
    assert any("venceu em 2026-09-14" in a for a in achados)


def test_a_mensagem_diz_que_renovar_a_data_sozinha_nao_vale() -> None:
    """A cerca existe para PROVOCAR a leitura.

    Sem esta frase, o reflexo de quem ve a esteira vermelha e' empurrar a data — que e' a
    normalizacao do alarme com passos extras.
    """
    achados = verificar(_completo(revisar_ate="2026-01-01"), hoje=_HOJE)
    assert any("sem a revisao" in a for a in achados)


def test_documento_apagado_reprova() -> None:
    """Uma cadencia sobre um documento que nao existe passa verde sobre o vazio."""
    achados = verificar(_completo(documento="docs/nao-existe.md"), hoje=_HOJE)
    assert any("NAO existe na arvore" in a for a in achados)


def test_data_ausente_ou_malformada_reprova() -> None:
    assert any("sem `revisar_ate`" in a for a in verificar(_completo(revisar_ate=None), hoje=_HOJE))
    assert any("nao e' uma data" in a for a in verificar(_completo(revisar_ate="dezembro"), hoje=_HOJE))


def test_apagar_uma_regua_obrigatoria_nao_cala_a_cerca() -> None:
    """O modo de falha de toda allowlist sem piso: o jeito mais barato de ficar verde e' apagar a
    linha que reprova."""
    achados = verificar([_regua()], hoje=_HOJE)  # so' `extracao`
    assert any("obrigatoria" in a for a in achados)


def test_regua_sem_dono_reprova() -> None:
    achados = verificar(_completo(dono=""), hoje=_HOJE)
    assert any("sem `dono`" in a for a in achados)


def test_o_arquivo_real_do_repo_declara_as_duas_reguas_e_esta_em_dia() -> None:
    """Prova de nao-vacuidade: os testes acima rodam sobre dicionarios sinteticos.

    Sem este, o YAML de verdade poderia estar vencido ou vazio com a suite inteira verde.
    """
    reguas = carregar()
    assert {str(r["id"]) for r in reguas} >= _REGUAS_OBRIGATORIAS
    assert verificar(reguas, hoje=date.today()) == []


def test_o_comando_devolve_zero_hoje_e_um_no_futuro(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 0
    assert main(["--hoje", "2099-01-01"]) == 1
