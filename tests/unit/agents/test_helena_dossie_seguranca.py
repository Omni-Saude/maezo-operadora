"""Correcoes do DOSSIE DE SEGURANCA da fase 5 (18/10/2026) que ficam depois da limpeza da saude, etapa 3
(02/11/2026; DL-0123, DL-0151): termo de risco no texto vence o classificador e traz o CVV 188 (com o 192 e o
caso); os textos de alarme e de risco; o preambulo de reescrita sai do texto final. O alarme por palavra, o
acolhimento por palavra e a checagem da redacao gerativa sairam do codigo com os testes deles.
"""

from __future__ import annotations

import pytest

from maezo.agents.helena.graph import RESPOSTA_ESCALONAMENTO_PSICOSSOCIAL, RESPOSTA_ESCALONAMENTO_URGENTE
from maezo.domain.comunicacao import lexico
from tests.support.helena_conversa import _OUTRA, _classify, _Conversa

_SEM_RISCO = _classify(intent="symptom", population="adult", psychosocial_risk=False)


@pytest.mark.parametrize(
    "mensagem",
    [
        "Tenho um plano pra acabar com tudo hoje.",
        "Ando pensando em sumir de vez, nao aguento mais.",
        "Me machuquei de proposito ontem, me cortei.",
    ],
)
async def test_risco_pelo_texto_vence_o_classificador_e_traz_o_188(mensagem: str) -> None:
    conversa = _Conversa(conversa=_OUTRA)
    await conversa.diz(mensagem, _SEM_RISCO)
    assert conversa.motivos == ["risco_psicossocial"]
    enviado = conversa.whatsapp.enviados[-1]
    assert "188" in enviado and "192" in enviado


def test_os_textos_de_alarme_e_de_risco() -> None:
    assert RESPOSTA_ESCALONAMENTO_URGENTE.startswith("Ligue 192 (SAMU) agora ou vá ao pronto-socorro.")
    assert "se for uma emergência" not in RESPOSTA_ESCALONAMENTO_URGENTE.lower()
    assert "CVV, no 188 (ligação gratuita, 24h)" in RESPOSTA_ESCALONAMENTO_PSICOSSOCIAL
    assert "192" in RESPOSTA_ESCALONAMENTO_PSICOSSOCIAL


def test_preambulo_de_reescrita_sai() -> None:
    assert lexico.sem_abertura_repetida(
        "Claro, vou reescrever sem essas palavras. A dipirona serve para dor."
    ) == ("A dipirona serve para dor.")
    assert lexico.sem_abertura_repetida("Aqui está a versão reescrita: Texto.") == "Texto."
