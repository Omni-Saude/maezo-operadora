"""Contrato do roteador de habilidades da Helena (ADR-0066, DL-0134): forma, travas, dono das pendências."""

from __future__ import annotations

import copy
from typing import Any

import pytest
import yaml

from maezo.agents.helena import roteador_contrato as rc
from maezo.agents.helena.cobertura_assistida import PEND_EQUIPE, PEND_QUEM
from maezo.agents.helena.cobertura_fluxo import PEND_RESPONDIDO
from maezo.agents.helena.cobertura_rol import PENDENTE_COBERTURA_PARA_QUEM, PREFIXO_COBERTURA_QUEM
from maezo.agents.helena.graph import PENDENTE_ENCERRAR_CASO
from maezo.agents.helena.helena_v4 import PREFIXO_PENDENTE
from maezo.agents.helena.triagem_v2 import PREFIXO_PERGUNTA_DA_FICHA


def _bruto() -> dict[str, Any]:
    with rc.caminho_padrao().open(encoding="utf-8") as f:
        return dict(yaml.safe_load(f))


def test_o_contrato_do_repositorio_carrega() -> None:
    contrato = rc.carregar()
    assert tuple(h.id for h in contrato.habilidades) == rc.HABILIDADES
    assert contrato.travas_de_entrada == rc.TRAVAS_DE_ENTRADA
    assert contrato.travas_de_saida == rc.TRAVAS_DE_SAIDA
    assert contrato.maximo_de_mensagens >= 4


@pytest.mark.parametrize(
    ("codigo", "dona"),
    [
        (PEND_QUEM, "cobertura"),
        (PEND_EQUIPE, "cobertura"),
        (PEND_RESPONDIDO, "cobertura"),
        (PENDENTE_COBERTURA_PARA_QUEM, "cobertura"),
        (f"{PREFIXO_COBERTURA_QUEM}2", "cobertura"),
        (f"{PREFIXO_PENDENTE}ALERTA:GERAL:adult", "sintoma"),
        (f"{PREFIXO_PERGUNTA_DA_FICHA}IDADE", "sintoma"),
        (PENDENTE_ENCERRAR_CASO, "equipe"),
        ("", None),
        ("OUTRA_COISA", None),
    ],
)
def test_toda_pendencia_de_hoje_tem_uma_habilidade_dona(codigo: str, dona: str | None) -> None:
    assert rc.carregar().dona_da_pendencia(codigo) == dona


def test_ordem_das_travas_de_entrada_e_fixa() -> None:
    bruto = _bruto()
    bruto["travas_de_entrada"] = list(reversed(bruto["travas_de_entrada"]))
    with pytest.raises(rc.ContratoInvalidoError, match="travas_de_entrada"):
        rc.validar(bruto)


def test_chave_nasce_desligada() -> None:
    bruto = _bruto()
    bruto["chave"] = {**bruto["chave"], "padrao": "ativo"}
    with pytest.raises(rc.ContratoInvalidoError, match="chave"):
        rc.validar(bruto)


def test_roteador_so_na_zona_phi_regional() -> None:
    bruto = _bruto()
    bruto["modelo"] = {**bruto["modelo"], "zona": "global"}
    with pytest.raises(rc.ContratoInvalidoError, match="zona PHI"):
        rc.validar(bruto)


def test_pendencia_com_dois_donos_reprova() -> None:
    bruto = copy.deepcopy(_bruto())
    bruto["habilidades"][1]["pendencias"] = ["COB2:QUEM"]
    with pytest.raises(rc.ContratoInvalidoError, match="mais de um dono"):
        rc.validar(bruto)


def test_habilidade_sem_exemplos_suficientes_reprova() -> None:
    bruto = copy.deepcopy(_bruto())
    bruto["habilidades"][0]["exemplos"] = ["so um"]
    with pytest.raises(rc.ContratoInvalidoError, match="exemplos"):
        rc.validar(bruto)


def test_habilidade_fora_da_lista_reprova() -> None:
    bruto = copy.deepcopy(_bruto())
    bruto["habilidades"].append({**bruto["habilidades"][0], "id": "reembolso"})
    with pytest.raises(rc.ContratoInvalidoError, match="habilidades"):
        rc.validar(bruto)


def test_texto_da_duvida_tem_as_duas_opcoes() -> None:
    texto = rc.carregar().texto_da_duvida.format(x="cobertura", y="mensalidade")
    assert "cobertura" in texto and "mensalidade" in texto


def test_habilidades_pausadas_nao_estao_no_contrato() -> None:
    ids = {h.id for h in rc.carregar().habilidades}
    assert not ids & set(_bruto()["pausadas_ate_f4"])
