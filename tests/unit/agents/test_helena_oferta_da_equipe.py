"""A OFERTA da equipe e a resposta a ela (avaliacao independente r5 de 25/10/2026).

- item 10: caso aberto SEM pedido, como `falha_tecnica` — o gatilho de loop (DL-0147) disparava com o aviso
  fixo da cobertura repetido (depois de uma recusa do assunto e numa pergunta de remedio). Agora a recusa do
  assunto e' so' reconhecida, o aviso da cobertura nunca e' loop e o loop OFERECE a equipe (ver
  `test_helena_escalar_por_repeticao.py`);
- item 11: o "sim" a oferta da equipe ("quero, pode passar") era ignorado — a habilidade `equipe` fechava a
  intencao do classificador em `information`. Agora abre o caso `solicitacao_humano`;
- item 13b: o paragrafo longo da recusa saia sem oferta nem caso. Agora a recusa curta so' com oferta
  pendente; sem oferta e sem caso, so' o reconhecimento.

Frases escritas para estes testes (nenhuma copiada do conjunto da avaliacao)."""

from __future__ import annotations

from typing import Any

import pytest

from maezo.agents.helena.habilidades.atendimento_humano import oferta as of
from maezo.agents.helena.habilidades.atendimento_humano.textos import (
    PENDENTE_OFERTA_EQUIPE,
    RESPOSTA_RECUSA_DE_EQUIPE,
    TEXTO_CONFIRMA_PASSAR,
    TEXTO_OFERTA_POR_REPETICAO,
)
from maezo.agents.helena.habilidades.cobertura_fora import TEXTO_COBERTURA_FORA
from maezo.agents.helena.habilidades.conversa import leitura_curta as _lc
from tests.support.roteador_v7_fake import decisao_v7
from tests.unit.agents.test_helena_roteador_v7 import _ConversaAtiva
from tests.unit.agents.test_helena_saude_gerativa import _PESSOA, _texto

_CONVITE = "O seu plano é coletivo empresarial. Se quiser falar com uma pessoa da equipe, é só me pedir."
_PARAGRAFO_LONGO = "respeito sua escolha"


def _estado(*falas: str, pendente: str | None = None) -> dict[str, Any]:
    historico: list[dict[str, str]] = []
    for fala in falas:
        historico.append({"papel": "beneficiario", "texto": "oi", "em": "2026-10-25T12:00:00+00:00"})
        historico.append({"papel": "helena", "texto": fala, "em": "2026-10-25T12:00:00+00:00"})
    return {"historico_conversa": historico, "coleta_pendente": pendente}


# =================================================================================================
# 1. A leitura da resposta (pura)
# =================================================================================================


@pytest.mark.parametrize(
    "sim", ["sim", "quero", "quero, pode passar", "pode sim", "quero sim, pode encaminhar", "pode passar sim"]
)
def test_o_sim_a_oferta_em_varias_formas(sim: str) -> None:
    assert of.resposta_a_oferta(sim) == "sim"


@pytest.mark.parametrize(
    "nao",
    ["não precisa", "agora não", "não, obrigada", "esquece, não precisa mais", "não quero falar com ninguém"],
)
def test_o_nao_a_oferta_em_varias_formas(nao: str) -> None:
    assert of.resposta_a_oferta(nao) == "nao"


@pytest.mark.parametrize("outra", ["ok", "beleza", "valeu", "e a minha carteirinha?", "obrigada pela ajuda"])
def test_cortesia_e_pedido_novo_nao_sao_sim(outra: str) -> None:
    """Controle negativo: cortesia nunca abre caso, pedido novo nao e' resposta."""
    assert of.resposta_a_oferta(outra) is None


@pytest.mark.parametrize(
    ("estado", "esperado"),
    [
        (_estado("Algo.", pendente=PENDENTE_OFERTA_EQUIPE), True),
        (_estado(TEXTO_OFERTA_POR_REPETICAO), True),
        (_estado(TEXTO_CONFIRMA_PASSAR), True),
        (_estado(_CONVITE), True),
        # o convite junto de OUTRA pergunta: o "sim" podia ser a ela
        (_estado(_CONVITE + " Quer saber da carência também?"), False),
        # a pergunta pendente de outra habilidade (sintoma) e' dela
        (_estado(_CONVITE, pendente="V4_ALERTA:geral:adult"), False),
        (_estado(TEXTO_COBERTURA_FORA), False),
        (_estado(), False),
    ],
    ids=["pendencia", "oferta-do-loop", "confirmacao", "convite", "convite-com-pergunta", "outra-dona",
         "aviso-cobertura", "sem-historico"],
)  # fmt: skip
def test_quando_ha_oferta_pendente(estado: dict[str, Any], esperado: bool) -> None:
    assert of.oferta_pendente(estado) is esperado


# =================================================================================================
# 2. Item 11 — o "sim" no contexto abre o caso (roteador ativo)
# =================================================================================================


@pytest.mark.parametrize("pedido", ["quero, pode passar", "pode passar sim", "quero sim, pode encaminhar"])
async def test_pedido_de_passar_depois_do_aviso_abre_o_caso(pedido: str) -> None:
    conversa = _ConversaAtiva()
    aviso = await conversa.fala("o plano cobre fisioterapia?", decisao_v7("cobertura"))
    assert _texto(aviso) == TEXTO_COBERTURA_FORA
    await conversa.fala(pedido, decisao_v7("equipe"))
    assert conversa.motivos == ["solicitacao_humano"]


@pytest.mark.parametrize("sim_solto", ["quero", "pode sim", "sim"])
async def test_sim_solto_sem_oferta_confirma_antes_e_o_sim_seguinte_abre(sim_solto: str) -> None:
    conversa = _ConversaAtiva()
    await conversa.fala("o plano cobre fisioterapia?", decisao_v7("cobertura"))
    confirma = await conversa.fala(sim_solto, decisao_v7("equipe"))
    assert _texto(confirma) == TEXTO_CONFIRMA_PASSAR
    assert conversa.motivos == []
    await conversa.fala("sim, pode", decisao_v7("equipe", responde=True, sim_nao="sim"))
    assert conversa.motivos == ["solicitacao_humano"]


@pytest.mark.parametrize("nao", ["não precisa", "agora não", "não, obrigado"])
async def test_nao_depois_da_confirmacao_recebe_a_recusa_curta_sem_caso(nao: str) -> None:
    conversa = _ConversaAtiva()
    await conversa.fala("o plano cobre fisioterapia?", decisao_v7("cobertura"))
    await conversa.fala("quero", decisao_v7("equipe"))
    fim = await conversa.fala(nao, decisao_v7("equipe", responde=True, sim_nao="nao", recusa=True))
    assert _texto(fim) == RESPOSTA_RECUSA_DE_EQUIPE
    assert conversa.motivos == []


async def test_pedido_explicito_de_pessoa_continua_abrindo_como_sempre() -> None:
    """Controle: o pedido explicito nao depende da oferta."""
    conversa = _ConversaAtiva()
    await conversa.fala("quero falar com um atendente", decisao_v7("equipe"), _PESSOA)
    assert conversa.motivos == ["solicitacao_humano"]


# =================================================================================================
# 3. Item 13b — a recusa sem oferta e sem caso e' so' reconhecida
# =================================================================================================


@pytest.mark.parametrize(
    "recusa", ["não precisa da equipe", "não quero falar com ninguém", "deixa, não precisa"]
)
async def test_recusa_sem_oferta_nem_caso_nunca_e_o_paragrafo_longo(recusa: str) -> None:
    conversa = _ConversaAtiva()
    await conversa.fala("o plano cobre fisioterapia?", decisao_v7("cobertura"))
    fim = await conversa.fala(recusa, decisao_v7("equipe", recusa=True))
    assert _texto(fim) == _lc.TEXTO_COMBINADO
    assert _PARAGRAFO_LONGO not in _texto(fim).lower()
    assert conversa.motivos == []


# =================================================================================================
# 4. Item 10 — nenhum caso sem pedido no assunto da cobertura
# =================================================================================================


@pytest.mark.parametrize("encerra", ["esquece, não precisa mais", "deixa pra lá", "não, deixa assim"])
async def test_recusa_do_assunto_roteada_para_a_cobertura_e_so_reconhecida(encerra: str) -> None:
    conversa = _ConversaAtiva()
    await conversa.fala("o convênio paga a ressonância?", decisao_v7("cobertura"))
    await conversa.fala("sim, quero que vejam", decisao_v7("cobertura"))
    fim = await conversa.fala(encerra, decisao_v7("cobertura"))
    assert _texto(fim) == _lc.TEXTO_COMBINADO
    assert conversa.motivos == [], "nenhum caso sem pedido (antes: falha_tecnica pelo loop)"


async def test_tres_perguntas_de_cobertura_seguidas_nunca_abrem_caso() -> None:
    conversa = _ConversaAtiva()
    for pergunta in ("cobre implante?", "e a harmonização?", "e o remédio de pressão, o plano dá?"):
        resposta = await conversa.fala(pergunta, decisao_v7("cobertura"))
        assert _texto(resposta) == TEXTO_COBERTURA_FORA
    assert conversa.motivos == []
