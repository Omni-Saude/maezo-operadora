"""FALHAS AVULSAS (Plano de ajustes da Helena v2, etapa 10; DL-0154): nenhum link sai (a fonte fica pelo
nome); a dor nas costas nao credita "Infarto"; o profissional do caminho vai pela idade; audio, foto e
figurinha recebem o aviso de que o canal le' so' texto, com o caminho para uma pessoa.

Frases escritas para estes testes (nenhuma copiada de roteiro do simulador)."""

from __future__ import annotations

from pathlib import Path

import pytest

from maezo.agents.helena.travas_saida import sem_links
from maezo.conhecimento import base_saude as b
from maezo.platform.webhooks.whatsapp.dispatch import NON_TEXT_ACK_TEXT

_BASE = Path(__file__).resolve().parents[3] / "spec" / "conhecimento" / "saude"


@pytest.mark.parametrize(
    ("texto", "fica"),
    [
        (
            "A dengue causa febre alta. Fonte: Saúde de A a Z (Ministério da Saúde). Para ler mais: "
            "https://www.gov.br/saude/pt-br/assuntos/saude-de-a-a-z/d/dengue",
            "Fonte: Saúde de A a Z (Ministério da Saúde).",
        ),
        ("Veja a bula em www.anvisa.gov.br/bulario antes de usar.", "Veja a bula em antes de usar."),
        ("Mais detalhes no site da ANS (ans.gov.br).", "Mais detalhes no site da ANS."),
        ("Link:\nhttp://exemplo.com/pagina\nQualquer dúvida, me chame.", "Qualquer dúvida, me chame."),
    ],
)
def test_nenhum_link_sai_e_a_fonte_fica_pelo_nome(texto: str, fica: str) -> None:
    saida = sem_links(texto)
    assert "http" not in saida and "www." not in saida and ".gov.br" not in saida and ".com" not in saida
    assert fica in saida


@pytest.mark.parametrize(
    "texto",
    [
        "Seu plano está ativo. Posso ajudar em mais alguma coisa?",
        "A mensalidade vence dia 10. O valor é R$ 1.234,56.",
        "Consulta com clínico geral: até 7 dias úteis pela ANS.",
    ],
)
def test_texto_sem_link_sai_igual(texto: str) -> None:
    assert sem_links(texto) is texto


def test_dor_nas_costas_nao_credita_infarto() -> None:
    base = b.carregar_base(_BASE)
    costas = base.get("sin-dor-nas-costas")
    assert costas is not None
    assert "Infarto" not in b.linha_de_credito(costas)
    assert all("infarto" not in f.nome.lower() for f in costas.fontes)


@pytest.mark.parametrize(
    ("populacao", "profissional"), [("adult", "clínico geral"), ("pediatric", "pediatra")]
)
def test_o_caminho_do_sintoma_vai_pela_idade(populacao: str, profissional: str) -> None:
    base = b.carregar_base(_BASE)
    costas = base.get("sin-dor-nas-costas")
    assert costas is not None
    texto = b.texto_final_do_sintoma_com_orientacao("dor nas costas", costas, populacao)
    assert f"consulta com {profissional}" in texto
    if populacao == "adult":
        assert "pediatra" not in texto


def test_aviso_de_audio_foto_e_figurinha() -> None:
    assert NON_TEXT_ACK_TEXT.startswith("Por aqui eu consigo ler só mensagens de texto.")
    assert "falar com uma pessoa" in NON_TEXT_ACK_TEXT
    assert sem_links(NON_TEXT_ACK_TEXT) is NON_TEXT_ACK_TEXT
