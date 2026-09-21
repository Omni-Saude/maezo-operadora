"""A abertura e' uma conversa, nao um cartao — F6 da bateria de 21/09/2026.

O QUE FOI MEDIDO, nos casos `A1`, `A2` e `A3` (tres turnos cada):

    "tudo bem?"                         -> repetiu o cartao, nao respondeu
    "queria saber uma coisa"            -> repetiu o cartao
    "quem e voce?"                      -> respondeu bem
    "voce e uma pessoa ou um robo?"     -> NAO RESPONDEU, repetiu a apresentacao
    "meu nome e Maria Souza"            -> ignorou o nome
    "voce sabe quem eu sou?"            -> NAO RESPONDEU

E, em quase todos os turnos, *"Este canal pode te orientar sobre... Se quiser, descreva melhor o
que esta sentindo"* — a mesma frase, de novo.

DOIS DOS SEIS NAO SAO ESTILO. Nao responder "voce e uma pessoa ou um robo?" num canal de saude e'
CONFORMIDADE: e' a primeira coisa que uma revisao de direito do consumidor pergunta, e a pessoa tem
o direito de saber com o que esta falando na primeira vez que pergunta. E dizer *"do seu plano"*
para quem a Helena nao tem como identificar e' afirmar um vinculo que ela nao verificou — junto com
"voce sabe quem eu sou?" sem resposta, a conversa fica incoerente.

AS FRASES SAO DECISAO DO DONO (21/09/2026), nao escolha de redacao: elas moram em constantes e vao
LITERALMENTE para o prompt, porque uma resposta de conformidade que o modelo reformula a cada turno
nao e' uma resposta de conformidade.

O QUE ESTE ARQUIVO NAO CONSEGUE PROVAR, e esta' declarado no relatorio: que o cartao aparece no
MAXIMO uma vez por conversa. A instrucao esta no prompt e depende de um sinal de estado
(`apresentacao_ja_feita`) que so' `graph.py` pode acender — e `graph.py` pertence a outra frente em
voo. Enquanto ele nao acender, o que o prompt garante e' o mais importante dos dois: responder a
pergunta ANTES de se apresentar, que e' o que fez tres turnos seguidos sairem iguais.
"""

from __future__ import annotations

import pytest

from maezo.agents.helena.prompts import (
    NEGATIVA_CLINICA_PROIBIDA,
    PROMESSA_DE_CAPACIDADE_PROIBIDA,
    PROMESSA_DE_HUMANO_PROIBIDA,
    RESPOSTA_NAO_CONSIGO_IDENTIFICAR,
    RESPOSTA_SOU_ASSISTENTE_VIRTUAL,
    _normalizar,
    motivo_de_canal_nao_confirmado,
    response_prompt,
)

#: A frase do dono, ao pe' da letra. Ela NAO e' a que foi publicada, e o teste abaixo diz por que.
FRASE_DO_DONO_AO_PE_DA_LETRA = (
    "Sou um assistente virtual, um sistema automatizado — não sou uma pessoa. "
    "Se preferir falar com alguém, eu encaminho."
)


# =================================================================================================
# 1. As duas frases que o dono fixou
# =================================================================================================


def test_a_frase_do_robo_assume_que_e_um_sistema_sem_rodeio() -> None:
    frase = RESPOSTA_SOU_ASSISTENTE_VIRTUAL.lower()

    assert "assistente virtual" in frase
    assert "sistema automatizado" in frase
    assert "não sou uma pessoa" in frase
    # A saida oferecida junto: a pessoa nao fica presa no robo depois de saber que e' um robo.
    assert "atendente humano" in frase


def test_a_frase_de_identificacao_e_um_nao_direto() -> None:
    """*"Voce sabe quem eu sou?"* -> "Nao". A resposta honesta e' mais curta que o rodeio."""
    frase = RESPOSTA_NAO_CONSIGO_IDENTIFICAR.lower()

    assert frase.startswith("não")
    assert "identificar" in frase
    assert len(RESPOSTA_NAO_CONSIGO_IDENTIFICAR) < 80, "um nao direto nao precisa de paragrafo"


def test_a_frase_do_dono_ao_pe_da_letra_seria_barrada_pela_cerca_de_capacidade() -> None:
    """O DESVIO DECLARADO desta entrega, e a evidencia dele em codigo.

    A frase ditada termina em "eu encaminho", que e' o PRIMEIRO padrao de
    `PROMESSA_DE_CAPACIDADE_PROIBIDA` — proibido em TODA rota desde 13/09, quando a Helena
    respondeu sobre segunda via de boleto que "eu encaminho sua solicitacao". Publicar a frase ao
    pe' da letra faria a cerca de saida barrar TODA resposta a "voce e um robo?" e o turno cair em
    escalonamento por texto recusado — o contrario do que o dono pediu.

    A frase publicada muda uma palavra ("eu TE encaminho para um atendente humano"), o que mantem
    o sentido, mantem a promessa verdadeira (handoff humano a Helena FAZ, e' o trabalho dela) e
    passa a cerca. O padrao existente nao foi afrouxado.
    """
    assert "eu encaminho" in PROMESSA_DE_CAPACIDADE_PROIBIDA
    assert "eu encaminho" in _normalizar(FRASE_DO_DONO_AO_PE_DA_LETRA)
    assert "eu encaminho" not in _normalizar(RESPOSTA_SOU_ASSISTENTE_VIRTUAL)


@pytest.mark.parametrize("frase", [RESPOSTA_SOU_ASSISTENTE_VIRTUAL, RESPOSTA_NAO_CONSIGO_IDENTIFICAR])
def test_as_frases_publicadas_passam_por_todas_as_listas_da_cerca_de_saida(frase: str) -> None:
    """Uma frase FIXA que a cerca recusa e' um turno que morre sempre, nao as vezes.

    Por isso a verificacao e' contra as LISTAS e nao contra `motivo_de_recusa`: aqui interessa que
    nenhum padrao case, em rota nenhuma — inclusive as de promessa de humano, que so' seriam
    liberadas em `escalate`.
    """
    plano = _normalizar(frase)
    for lista in (NEGATIVA_CLINICA_PROIBIDA, PROMESSA_DE_CAPACIDADE_PROIBIDA, PROMESSA_DE_HUMANO_PROIBIDA):
        casados = [padrao for padrao in lista if padrao in plano]
        assert not casados, f"{frase!r} casa {casados}"
    assert motivo_de_canal_nao_confirmado(frase) is None


# =================================================================================================
# 2. As frases estao no texto que o modelo le'
# =================================================================================================


def test_o_prompt_de_resposta_carrega_as_duas_frases_ao_pe_da_letra() -> None:
    """Gerado das constantes: se o prompt puder reescrever a frase, a decisao do dono vira
    sugestao — e a resposta de conformidade muda de forma a cada turno."""
    texto = response_prompt()

    assert RESPOSTA_SOU_ASSISTENTE_VIRTUAL in texto
    assert RESPOSTA_NAO_CONSIGO_IDENTIFICAR in texto


def test_o_prompt_de_resposta_diz_que_nao_responder_e_conformidade() -> None:
    """A justificativa viaja junto com a regra. Sem ela, a proxima edicao do prompt corta a regra
    por parecer verbosa — foi o que aconteceu com a negativa clinica entre o v2 e o v3."""
    texto = response_prompt()

    assert "CONFORMIDADE" in texto
    assert "robo" in texto


def test_o_prompt_de_resposta_manda_responder_antes_de_se_apresentar() -> None:
    """O defeito medido: tres turnos, tres cartoes iguais, nenhuma resposta."""
    texto = response_prompt()

    assert "RESPONDA A PERGUNTA ANTES DE SE APRESENTAR" in texto
    assert "tudo bem?" in texto


def test_o_prompt_de_resposta_limita_a_apresentacao_a_uma_vez_por_conversa() -> None:
    texto = response_prompt()

    assert "no MAXIMO UMA vez por conversa" in texto
    # O sinal de estado que `graph.py` acende quando o cartao ja' saiu (pendencia declarada).
    assert "apresentacao_ja_feita" in texto


def test_o_prompt_de_resposta_proibe_afirmar_vinculo_que_nao_foi_verificado() -> None:
    """*"Do seu plano"* para quem a Helena nao identifica. A mensagem chega pseudonimizada: ela
    nao sabe o nome, nao sabe o contrato, e nao sabe nem se a pessoa e' beneficiaria."""
    texto = response_prompt()

    assert "VOCE NAO IDENTIFICA NINGUEM NESTE CANAL" in texto
    assert "do seu plano" in texto
    assert "pseudonimizada" in texto


def test_o_prompt_de_resposta_diz_o_que_fazer_quando_a_pessoa_da_o_nome() -> None:
    """`A3` turno 1: "Oi, meu nome e Maria Souza" — ignorado.

    E ignorar era meio certo: repetir o nome seria reidentificar num canal que nao identifica. O
    que faltava era dizer isso a pessoa em vez de nao dizer nada.
    """
    texto = response_prompt()

    assert "nome dela" in texto
    assert "cadastro" in texto
