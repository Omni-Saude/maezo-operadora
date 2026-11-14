"""CORRECOES DA RODADA DE ROTEIROS de 24/10/2026 (Plano de ajustes da Helena v2, depois da etapa 10).

- urgencia que PIORA depois do 192 ("piorou, ele esta' sem ar") e' sinal NOVO: o 192 sai de novo (antes
  saia "mantive o seu caso", errado do lado perigoso);
- a recusa da equipe sem caso aberto e sem oferta e' so' reconhecida (avaliacao r5 de 25/10/2026: o paragrafo
  longo da recusa saia fora de contexto; a recusa curta so' com oferta pendente).

Frases escritas para estes testes (nenhuma copiada de roteiro do simulador)."""

from __future__ import annotations

import pytest

from maezo.agents.helena.graph import RESPOSTA_RECUSA_DE_EQUIPE
from maezo.agents.helena.habilidades.atendimento_humano.textos import TEXTO_CASO_MANTIDO
from maezo.agents.helena.habilidades.conversa import leitura_curta as _lc
from tests.support.helena_conversa import _DOR_NO_PEITO, _INFO, _PESSOA, _classify, _Conversa, _texto
from tests.support.roteador_v7_fake import decisao_v7
from tests.unit.agents.test_helena_roteador_v7 import _ConversaAtiva

_SEM_AR = _classify(intent="symptom", population="adult", sintoma_codigo="dispneia", intensidade="grave")


async def _alarme_tratado(conversa: _Conversa) -> None:
    urgente = await conversa.diz("ando com uma dor forte no peito desde cedo", _DOR_NO_PEITO)
    assert "192" in _texto(urgente) and conversa.motivos == ["red_flag_clinico"]


@pytest.mark.parametrize(
    "piora", ["piorou, ele está sem ar", "piorou muito agora", "agora ficou sem ar, está roxo"]
)
async def test_piora_depois_do_192_da_192_de_novo(piora: str) -> None:
    conversa = _Conversa()
    await _alarme_tratado(conversa)
    # Limpeza da saude, etapa 3: sem a lista de palavras, a piora e' extraida pelo classificador e a tabela
    # DMN da' o P1 de novo.
    resposta = await conversa.diz(piora, _SEM_AR)
    assert _texto(resposta) != TEXTO_CASO_MANTIDO
    assert "192" in _texto(resposta)


@pytest.mark.parametrize("recusa", ["não quero falar com ninguém", "não precisa de equipe", "deixa, desisto"])
async def test_recusa_sem_caso_nem_oferta_e_so_reconhecida(recusa: str) -> None:
    conversa = _ConversaAtiva()
    await conversa.fala("bom dia", decisao_v7("conversa"), _INFO)
    fim = await conversa.fala(recusa, decisao_v7("conversa", recusa=True))
    assert _texto(fim) == _lc.TEXTO_COMBINADO
    assert _texto(fim) != RESPOSTA_RECUSA_DE_EQUIPE
    assert conversa.cib.inicios == []


async def test_recusa_com_caso_de_pessoa_aberto_encerra() -> None:
    conversa = _ConversaAtiva()
    await conversa.fala("quero falar com alguém", decisao_v7("equipe"), _PESSOA)
    fim = await conversa.fala("não, deixa, desisto", decisao_v7("conversa", recusa=True))
    assert "encerr" in _texto(fim).lower(), "a recusa tentou encerrar o caso P2/P3"
