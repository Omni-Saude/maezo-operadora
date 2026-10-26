"""Teste do dono de 25/10/2026 (noite, 19:59-20:07 UTC): a recusa e o caso aberto depois de um P1 falso.

(a) A RECUSA nunca vira pedido da equipe: "nao quero que ninguem entre em contato", "encerre"/"EU QUERO QUE
    ENCERRE", "nao autorizo", "nao quero falar com ninguem" e "para de me mandar mensagem" nunca abrem, mantem
    nem confirmam caso, e nunca recebem "seu pedido ja' esta' com a equipe" — nem quando o roteador le'
    `equipe` sem `refusal_of_team` (o log da noite), nem com o roteador fora. Controle: o pedido explicito
    de pessoa continua abrindo o caso.
(b) P1 aberto por engano (decisao B do dono, opcao 1): o caso FICA para a equipe confirmar, a conversa volta
    ao normal e o aviso "nao posso encerrar"/"mantive o seu caso" sai no MAXIMO uma vez por conversa.
(c) O caso aberto nao contamina as outras respostas (plano, consulta, saude, especialista, cortesia), a menos
    que a pessoa pergunte do caso.
Seguranca: o sinal NOVO de alarme depois do 192 continua escalando.

Frases escritas para estes testes (a conversa do dono aparece parafraseada, nunca copiada)."""

from __future__ import annotations

from typing import Any

import pytest
from structlog.testing import capture_logs

from maezo.agents.helena.graph import RESPOSTA_CASO_GRAVE_NAO_ENCERRA
from maezo.agents.helena.habilidades.atendimento_humano import oferta as of
from maezo.agents.helena.habilidades.atendimento_humano import recusa as rc
from maezo.agents.helena.habilidades.atendimento_humano.textos import (
    TEXTO_RECUSA_JA_RESPONDIDA,
    TEXTO_SEM_SINAL_NOVO,
)
from maezo.agents.helena.habilidades.conversa import leitura_curta as lc
from maezo.agents.helena.habilidades.sintoma.textos import TEXTO_CASO_MANTIDO
from tests.support.roteador_v7_fake import decisao_v7
from tests.unit.agents.test_helena_roteador_v7 import _ConversaAtiva
from tests.unit.agents.test_helena_saude_gerativa import _DOR_NO_PEITO, _INFO, _PESSOA, _SINTOMA, _texto

# Os cinco jeitos de recusar da conversa do dono, cada um com 3 ou mais frases.
_RECUSA_DE_CONTATO = (
    "não quero que ninguém entre em contato",
    "não preciso que me liguem, obrigada",
    "ninguém precisa me ligar",
)
_ENCERRE = (
    "encerre",
    "EU QUERO QUE ENCERRE ISSO AGORA",
    "pode encerrar o atendimento",
    "finaliza esse atendimento",
)
_NAO_AUTORIZO = ("não autorizo", "eu não autorizo nada disso", "não dou autorização")
_NINGUEM = (
    "não quero falar com ninguém",
    "não quero conversar com nenhum atendente",
    "não preciso de atendente",
)
_PARAR = (
    "para de me mandar mensagem",
    "pare de me mandar mensagens!",
    "não me mande mais mensagem",
    "me deixa em paz",
)
_TODAS = (*_RECUSA_DE_CONTATO, *_ENCERRE, *_NAO_AUTORIZO, *_NINGUEM, *_PARAR)

_PEDIDOS_DE_PESSOA = (
    "quero falar com um atendente",
    "me passa para uma pessoa da equipe, por favor",
    "preciso falar com alguém da equipe",
    "não quero falar com robô, quero um atendente",
)
_NAO_E_RECUSA = (
    "quero que alguém entre em contato comigo",
    "não encerre, por favor",
    "não quero que encerre ainda",
    "quero encerrar o meu plano",
    "quando encerra a minha carência?",
    "não autorizo o débito automático da mensalidade",
    "para de me mandar boleto por e-mail",
    "não autorizo o uso dos meus dados",
    "qual é o meu plano?",
    "obrigada pela ajuda",
)

#: A frase do caso aberto de carona e o "seu pedido ja' esta' com a equipe".
_FRASES_DE_CASO = ("com a nossa equipe", "com o seu caso", "já está com a equipe")


def _sem_caso_de_carona(texto: str) -> bool:
    baixo = texto.lower()
    return not any(f in baixo for f in _FRASES_DE_CASO)


def _e_aviso(texto: str) -> bool:
    baixo = lc.normalizar(texto)
    return "nao posso encerra" in baixo or "mantive o seu caso" in baixo


async def _turno(
    conversa: _ConversaAtiva, mensagem: str, roteador: str, extracao: str = _INFO, *extras: str
) -> str:
    """Um turno com as filas do modelo falso limpas (cada turno le' so' o que lhe foi dado)."""
    conversa.modelo.respostas.clear()
    conversa.modelo.roteador.clear()  # type: ignore[attr-defined]
    conversa.modelo.roteador.append(roteador)  # type: ignore[attr-defined]
    return _texto(await conversa.diz(mensagem, extracao, *extras))


async def _com_p1(conversa: _ConversaAtiva) -> None:
    """O alarme (aqui pelo modelo, validado na tabela) abre o P1 com 192."""
    texto = await _turno(
        conversa,
        "sinto uma dor no peito e falta de ar",
        decisao_v7("sintoma", sinais=["dor no peito"], alarme="clear"),
        _DOR_NO_PEITO,
    )
    assert "192" in texto and conversa.motivos == ["red_flag_clinico"]


# =================================================================================================
# 1. O leitor da recusa (puro + pedido de pessoa)
# =================================================================================================


@pytest.mark.parametrize("frase", _TODAS)
def test_as_recusas_do_dono_sao_lidas_no_codigo(frase: str) -> None:
    assert rc.e_recusa_da_equipe(frase)


@pytest.mark.parametrize("frase", _PEDIDOS_DE_PESSOA + _NAO_E_RECUSA)
def test_pedido_de_pessoa_e_outras_falas_nao_sao_recusa(frase: str) -> None:
    """Controle negativo: o pedido explicito de pessoa, o "nao encerre" e o encerramento do PLANO."""
    assert not rc.e_recusa_da_equipe(frase)


@pytest.mark.parametrize(
    "frase",
    [
        "não quero que ninguém me ligue, me passa pra uma pessoa aqui no chat",
        "não quero falar com ninguém por telefone, quero falar com um atendente aqui",
        "para de me mandar mensagem automática e me passa para um atendente",
    ],
)
def test_recusa_junto_de_pedido_de_pessoa_o_pedido_vence(frase: str) -> None:
    assert not rc.e_recusa_da_equipe(frase)


@pytest.mark.parametrize("frase", ["não autorizo", "para de me mandar mensagem", "EU QUERO QUE ENCERRE"])
def test_a_recusa_responde_nao_a_oferta_da_equipe(frase: str) -> None:
    assert of.resposta_a_oferta(frase) == "nao"


# =================================================================================================
# 2. (a) A recusa nunca abre, mantem nem confirma caso
# =================================================================================================


@pytest.mark.parametrize("frase", _TODAS)
async def test_recusa_lida_como_equipe_pelo_roteador_nao_abre_caso(frase: str) -> None:
    """O log da noite: o roteador leu `equipe` sem `refusal_of_team` e o classificador, pedido de pessoa."""
    conversa = _ConversaAtiva()
    with capture_logs() as logs:
        texto = await _turno(conversa, frase, decisao_v7("equipe"), _PESSOA)
    assert conversa.motivos == [], "a recusa nunca abre caso"
    assert _sem_caso_de_carona(texto) and "encaminh" not in texto.lower()
    assert any(e.get("event") == "helena_recusa_lida_no_codigo" for e in logs)


@pytest.mark.parametrize("frase", [*_RECUSA_DE_CONTATO, *_ENCERRE[:3], *_NAO_AUTORIZO, *_PARAR[:3]])
async def test_recusa_com_o_roteador_fora_tambem_nao_abre_caso(frase: str) -> None:
    """O roteador sem decisao (JSON ruim): o caminho de sempre com o classificador lendo pedido de pessoa."""
    conversa = _ConversaAtiva()
    texto = await _turno(conversa, frase, "", _PESSOA)
    assert conversa.motivos == []
    assert _sem_caso_de_carona(texto)


@pytest.mark.parametrize(
    "frase", ["não quero que ninguém entre em contato", "EU QUERO QUE ENCERRE", "não autorizo"]
)
async def test_recusa_com_p1_aberto_nunca_vira_seu_pedido_ja_esta_com_a_equipe(frase: str) -> None:
    conversa = _ConversaAtiva()
    await _com_p1(conversa)
    texto = await _turno(conversa, frase, decisao_v7("equipe"), _PESSOA)
    assert conversa.motivos == ["red_flag_clinico"], "nenhum caso novo, nenhum pedido de pessoa"
    assert "seu pedido" not in texto.lower()
    assert texto == RESPOSTA_CASO_GRAVE_NAO_ENCERRA, "o P1 fica para a equipe confirmar (aviso uma vez)"


@pytest.mark.parametrize("pedido", _PEDIDOS_DE_PESSOA[:3])
async def test_pedido_explicito_de_pessoa_continua_abrindo_o_caso(pedido: str) -> None:
    """Controle negativo do item (a)."""
    conversa = _ConversaAtiva()
    await _turno(conversa, pedido, decisao_v7("equipe"), _PESSOA)
    assert conversa.motivos == ["solicitacao_humano"]


# =================================================================================================
# 3. (b) O aviso do caso UMA vez por conversa
# =================================================================================================


@pytest.mark.parametrize(
    "recusas",
    [
        ("não quero que ninguém entre em contato", "EU QUERO QUE ENCERRE", "não autorizo"),
        ("encerre", "para de me mandar mensagem", "não quero falar com ninguém"),
        ("não autorizo", "não autorizo", "me deixa em paz"),
    ],
)
async def test_recusas_seguidas_com_p1_o_aviso_sai_uma_vez(recusas: tuple[str, ...]) -> None:
    conversa = _ConversaAtiva()
    await _com_p1(conversa)
    textos = [await _turno(conversa, r, decisao_v7("equipe"), _PESSOA) for r in recusas]
    assert sum(_e_aviso(t) for t in textos) == 1, textos
    assert textos[1:] == [TEXTO_RECUSA_JA_RESPONDIDA] * (len(recusas) - 1)
    assert conversa.motivos == ["red_flag_clinico"]


@pytest.mark.parametrize(
    "falas",
    [
        ("ainda sinto um pouco de dor", "a dor continua do mesmo jeito", "tá doendo um pouco ainda"),
        ("a dor continua do mesmo jeito", "ainda sinto um pouco de dor", "ainda sinto um pouco de dor"),
        ("tá doendo um pouco ainda", "a dor continua do mesmo jeito", "ainda sinto um pouco de dor"),
    ],
)
async def test_fala_do_mesmo_alarme_sem_sinal_novo_o_mantive_sai_uma_vez(falas: tuple[str, ...]) -> None:
    conversa = _ConversaAtiva()
    await _com_p1(conversa)
    textos = [await _turno(conversa, f, decisao_v7("sintoma"), _SINTOMA) for f in falas]
    assert textos[0] == TEXTO_CASO_MANTIDO
    assert textos[1:] == [TEXTO_SEM_SINAL_NOVO] * (len(falas) - 1), "sem repetir, com a linha do 192"
    assert conversa.motivos == ["red_flag_clinico"]


@pytest.mark.parametrize(
    ("fala", "sinal"),
    [
        ("agora ele desmaiou", "desmaio"),
        ("ela desmaiou agora", "desmaio"),
        ("tô vomitando sangue", "vômito com sangue"),
    ],
)
async def test_sinal_novo_depois_da_recusa_continua_dando_192(fala: str, sinal: str) -> None:
    """Seguranca: a recusa e o aviso uma vez nunca calam um alarme NOVO."""
    conversa = _ConversaAtiva()
    await _com_p1(conversa)
    await _turno(conversa, "não quero que ninguém entre em contato", decisao_v7("equipe"), _PESSOA)
    texto = await _turno(conversa, fala, decisao_v7("sintoma", sinais=[sinal], alarme="clear"), _SINTOMA)
    assert texto.startswith("Ligue agora para o 192")


# =================================================================================================
# 4. (c) O caso aberto nao vai de carona
# =================================================================================================

_CARONA = "A nossa equipe de saúde já está com o seu caso."


@pytest.mark.parametrize(
    ("mensagem", "rascunho"),
    [
        ("dá pra marcar uma consulta por aqui?", "Você pode marcar pelo aplicativo Austa Clínicas."),
        ("qual especialista cuida de enxaqueca?", "Quem costuma cuidar de enxaqueca é o neurologista."),
        ("o que é colesterol?", "Colesterol é uma gordura que o corpo usa."),
        ("como funciona a coparticipação?", "A coparticipação é a parte que você paga no uso."),
    ],
)
def test_resposta_de_outro_assunto_sai_sem_o_caso(mensagem: str, rascunho: str) -> None:
    estado: dict[str, Any] = {"response_kind": "inform", "message_body": mensagem, "historico_conversa": []}
    assert rc.texto_final(estado, f"{rascunho} {_CARONA}") == rascunho
    assert rc.texto_final(estado, f"{_CARONA} {rascunho}") == rascunho


@pytest.mark.parametrize(
    "pergunta",
    ["alguma novidade do meu caso?", "quanto tempo a equipe demora?", "a equipe vai me retornar quando?"],
)
def test_quando_a_pessoa_pergunta_do_caso_o_caso_e_a_resposta(pergunta: str) -> None:
    """Controle negativo do item (c)."""
    estado: dict[str, Any] = {"response_kind": "inform", "message_body": pergunta, "historico_conversa": []}
    texto = f"Entendi. {_CARONA}"
    assert rc.texto_final(estado, texto) == texto


@pytest.mark.parametrize("cortesia", ["obrigado", "valeu, era isso", "ok, beleza"])
async def test_cortesia_com_p1_aberto_sai_sem_o_caso(cortesia: str) -> None:
    conversa = _ConversaAtiva()
    await _com_p1(conversa)
    texto = await _turno(conversa, cortesia, decisao_v7("conversa"), _INFO)
    assert texto and _sem_caso_de_carona(texto)


# =================================================================================================
# 5. A conversa do dono, parafraseada
# =================================================================================================


async def test_replay_da_noite_do_dono_recusa_insistencia_plano_e_consulta() -> None:
    """P1 falso -> recusa -> insistencia em maiusculas -> pergunta do plano -> pergunta de consulta: nenhum
    caso novo, nenhum "seu pedido ja' esta' com a equipe", o aviso do caso uma vez e nenhuma resposta
    contaminada."""
    conversa = _ConversaAtiva()
    await _com_p1(conversa)
    textos = [
        await _turno(
            conversa, "não, não quero que ninguém me ligue sobre isso", decisao_v7("equipe"), _PESSOA
        ),
        await _turno(conversa, "EU QUERO QUE VOCÊ ENCERRE ESSE ATENDIMENTO", decisao_v7("equipe"), _PESSOA),
        await _turno(conversa, "qual é o meu plano?", decisao_v7("plano"), _INFO),
        await _turno(
            conversa,
            "consigo agendar uma consulta por aqui?",
            decisao_v7("plano"),
            _INFO,
            f"Por aqui eu não marco, mas o aplicativo Austa Clínicas mostra a rede para agendar. {_CARONA}",
        ),
    ]
    assert conversa.motivos == ["red_flag_clinico"], "so' o P1; a recusa nunca abriu caso"
    assert sum(_e_aviso(t) for t in textos) == 1, textos
    assert textos[1] == TEXTO_RECUSA_JA_RESPONDIDA
    assert all("seu pedido" not in t.lower() for t in textos)
    assert all(_sem_caso_de_carona(t) for t in textos[2:]), textos[2:]
    assert "aplicativo Austa Clínicas" in textos[3]
