"""DEC-30 D19 (#1084, 2026-11-02): a concessao C0 `consulta_processo` ao `worker_runtime` fica MINIMA.

O dono concedeu ao daemon de workers a leitura de historico que o PAGTO PR-D (`resolve_lastro`) usa,
antes de qualquer virada de enforcement. Estes testes sao a guarda NEGATIVA dessa concessao no
manifesto `spec/policies/autonomy/action-approvals.yaml`:

* so um principal (`worker_runtime`), so uma classe (`consulta_processo`), so leitura, so a operacao
  `cibseven.resolve_historic_instance`, so os dois processos de origem do lastro;
* a operacao concedida e de fato da classe concedida no catalogo fechado (`effect_classes`);
* a concessao NAO aprova nada: os tres blocos de aprovacao da classe seguem `PENDENTE`, o arquivo
  segue `DRAFT`, e a fiacao de runtime segue declarada `PENDENTE` (o loader ainda nao le o bloco).
Alargar qualquer um desses pontos e decisao nova do dono, nao edicao de agente.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Final

import yaml

from maezo.gateway.effect_classes import OPERATIONS

_MANIFEST: Final[Path] = (
    Path(__file__).resolve().parents[3] / "spec" / "policies" / "autonomy" / "action-approvals.yaml"
)
_DEC30: Final[str] = "https://github.com/Omni-Saude/maezo-operadora/issues/1084#issuecomment-6575240466"


def _manifest() -> dict[str, Any]:
    data = yaml.safe_load(_MANIFEST.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def _concessao() -> dict[str, Any]:
    concessoes = _manifest()["concessoes_principal"]
    assert set(concessoes) == {"worker_runtime"}, f"principal alem do D19: {sorted(concessoes)}"
    por_classe = concessoes["worker_runtime"]
    assert set(por_classe) == {"consulta_processo"}, f"classe alem do D19: {sorted(por_classe)}"
    concessao = por_classe["consulta_processo"]
    assert isinstance(concessao, dict)
    return concessao


def test_concessao_e_so_leitura_de_historico_nos_processos_do_lastro() -> None:
    concessao = _concessao()
    assert concessao["somente_leitura"] is True
    assert concessao["operacoes"] == ["cibseven.resolve_historic_instance"]
    assert concessao["process_keys"] == ["SP-OP-CONTAS-001", "SP-OP-RECURSO-001"]


def test_operacao_concedida_e_da_classe_concedida_no_catalogo_fechado() -> None:
    for operacao in _concessao()["operacoes"]:
        assert operacao in OPERATIONS, f"{operacao!r} fora do catalogo fechado"
        assert OPERATIONS[operacao].action_class == "consulta_processo"


def test_concessao_cita_a_decisao_do_dono_e_declara_fiacao_pendente() -> None:
    concessao = _concessao()
    assert concessao["decidido_por"] == "dono (rodaquino-OMNI)"
    assert concessao["decidido_em"] == "2026-11-02"
    assert "DEC-30 D19" in concessao["decisao_ref"] and _DEC30 in concessao["decisao_ref"]
    assert concessao["fiacao_runtime"] == "PENDENTE"


def test_concessao_nao_aprova_a_classe_nem_ratifica_o_arquivo() -> None:
    data = _manifest()
    assert data["status"] == "DRAFT"
    classe = data["acoes"]["consulta_processo"]
    assert classe["enforcement"] == "shadow"
    for dominio, bloco in classe["aprovacoes"].items():
        assert bloco["aprovado"] is False, f"consulta_processo.{dominio} aprovado por agente"
        for campo in ("aprovador", "data", "evidencia_ref"):
            assert bloco[campo] == "PENDENTE", f"consulta_processo.{dominio}.{campo} preenchido"
