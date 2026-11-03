"""O prazo do provedor do roteador e' rotulado como PRAZO (teste do dono de 25/10/2026, item 8): so' o nome
da classe do erro decide. Limpeza da saude, etapa 3 (02/11/2026): os testes do sintoma (perguntas de alerta
da v4, arquivos da base) sairam com as camadas; fica o rotulo do prazo, que e' do roteador.
"""

from __future__ import annotations

import pytest

from maezo.agents.helena import roteador as rt


class ReadTimeoutError(Exception):
    pass


class ConnectTimeoutError(Exception):
    pass


@pytest.mark.parametrize("erro", [ReadTimeoutError("x"), ConnectTimeoutError("x"), TimeoutError("x")])
def test_prazo_do_provedor_e_rotulado_como_prazo(erro: Exception) -> None:
    assert rt._e_erro_de_prazo(type(erro).__name__)


@pytest.mark.parametrize("erro", [ConnectionError("recusada"), ValueError("json"), RuntimeError("provedor")])
def test_erro_que_nao_e_prazo_continua_modelo(erro: Exception) -> None:
    assert not rt._e_erro_de_prazo(type(erro).__name__)
