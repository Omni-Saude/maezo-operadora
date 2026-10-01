"""Os lexicos deterministicos do pre-roteamento (ADR-0062; plano `lucas-numero-unico.md` §2.3)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from maezo.platform.webhooks.whatsapp import pre_roteamento
from maezo.platform.webhooks.whatsapp.pre_roteamento import LexicoInvalidoError, normalizar


@pytest.fixture(scope="module")
def lexicos() -> pre_roteamento.PreRoteamento:
    return pre_roteamento.carregar()


def test_o_arquivo_versionado_carrega_como_draft(lexicos: pre_roteamento.PreRoteamento) -> None:
    assert lexicos.status == "DRAFT"
    assert lexicos.versao == "pre-roteamento-v1"
    assert lexicos.pedido_humano_lexico and lexicos.sinal_saude_lexico


def test_os_dois_lexicos_sao_disjuntos(lexicos: pre_roteamento.PreRoteamento) -> None:
    assert not set(lexicos.pedido_humano_lexico) & set(lexicos.sinal_saude_lexico)


@pytest.mark.parametrize(
    "texto",
    [
        "Quero falar com um ATENDENTE",
        "tem algum humano ai?",
        "preciso falar com alguém!",
        "Pessoa de verdade, por favor",
    ],
)
def test_pedido_de_pessoa_casa(lexicos: pre_roteamento.PreRoteamento, texto: str) -> None:
    sinais = lexicos.avaliar(texto)
    assert sinais.pedido_humano and not sinais.sinal_saude


@pytest.mark.parametrize(
    "texto",
    [
        "o boleto venceu e estou com DOR NO PEITO",
        "estou com falta de ar",
        "quero cancelar porque não aguento mais viver",
        "Não consigo respirar",
        "acho que vou me matar",
        "minha mãe teve um AVC",
    ],
)
def test_sinal_forte_de_saude_casa_sem_acento_e_sem_caixa(
    lexicos: pre_roteamento.PreRoteamento, texto: str
) -> None:
    assert lexicos.avaliar(texto).sinal_saude


@pytest.mark.parametrize(
    "texto",
    [
        "quero a segunda via do boleto",
        "isso e desumano",  # "humano" so' casa como palavra inteira
        "avcx",
        "o peito do frango",
        "",
    ],
)
def test_texto_comum_nao_casa_nada(lexicos: pre_roteamento.PreRoteamento, texto: str) -> None:
    sinais = lexicos.avaliar(texto)
    assert not sinais.pedido_humano and not sinais.sinal_saude
    assert sinais.termos_pedido_humano == sinais.termos_sinal_saude == 0


def test_sinais_nao_carregam_texto_nem_termo(lexicos: pre_roteamento.PreRoteamento) -> None:
    sinais = lexicos.avaliar("estou com dor no peito, quero um atendente")
    valores = [getattr(sinais, campo) for campo in type(sinais).__slots__]
    assert all(isinstance(v, bool | int) or v == lexicos.versao for v in valores)
    assert (sinais.termos_pedido_humano, sinais.termos_sinal_saude) == (1, 1)


def test_normalizar() -> None:
    assert normalizar("  Não, ATENDENTE!!  já ") == "nao atendente ja"


def _mapa(**sobrescrever: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "status": "DRAFT",
        "versao": "v-teste",
        "pedido_humano_lexico": ["atendente"],
        "sinal_saude_lexico": ["dor no peito"],
    }
    base.update(sobrescrever)
    return base


@pytest.mark.parametrize(
    "dados",
    [
        [],
        _mapa(status="APROVADO"),
        _mapa(versao=""),
        _mapa(pedido_humano_lexico=[]),
        _mapa(sinal_saude_lexico=None),
        _mapa(pedido_humano_lexico=["Atendente"]),
        _mapa(sinal_saude_lexico=["falta de  ar"]),
        _mapa(sinal_saude_lexico=["coração"]),
        _mapa(pedido_humano_lexico=["atendente", "atendente"]),
        _mapa(pedido_humano_lexico=[3]),
    ],
)
def test_lexico_invalido_recusa_no_carregamento(dados: Any) -> None:
    with pytest.raises(LexicoInvalidoError):
        pre_roteamento.de_mapa(dados)


def test_arquivo_ausente_recusa(tmp_path: Path) -> None:
    with pytest.raises(LexicoInvalidoError):
        pre_roteamento.carregar(tmp_path / "nao-existe.yaml")
