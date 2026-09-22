"""Bateria adversarial da QUINTA rodada — os dois erros de DISTANCIA que sobraram nas cercas.

O que esta rodada mediu, e que as quatro anteriores nao tinham medido:

  1. A cerca de mencao tratava a negacao pela JANELA (`(?:(?!\\bnao\\b)[^.;!?]){0,40}` entre sujeito
     e acao). Uma janela que recusa `nao` em QUALQUER ponto nao distingue *"a equipe NAO VAI
     assumir"* (nega a acao) de *"a equipe NAO TEM previsao, mas VAI te ligar"* (nega outra coisa e
     promete). O segundo saia INTACTO num turno sem start — o C1 da bateria do diretor pela
     terceira forma. A negacao passou a ser testada ONDE ela nega: `(?<!nao )` colado na acao.

  2. A pista de telefone perdeu a palavra `numero` inteira na quarta rodada, para parar de recusar
     "o numero do protocolo". Com isso a fraseologia MAIS provavel de um telefone inventado
     ("nosso numero e 3003 1234") passou a sair. O recorte certo nao e' a palavra, e' o GENITIVO
     administrativo — e `de` NAO faz parte dele: "numero DE atendimento" e' contato.

Os dois testes de baixo sao os que faltavam no L1 e que fizeram o defeito chegar ao L5: a CLASSE
"negacao DENTRO da janela em texto que ainda promete" e a CLASSE "`numero` seguido de algo que nao
e' genitivo". Os vereditos individuais estao congelados em `corpus_cercas_de_saida.json`; aqui o
que se prova e' a forma, com um par minimo/maximo por classe.
"""

from __future__ import annotations

import pytest

from maezo.agents.helena.prompts import (
    menciona_encaminhamento,
    motivo_de_canal_nao_confirmado,
    motivo_de_recusa,
)

# --- 1. negacao DENTRO da janela, em texto que AINDA promete -----------------------------------

#: A classe que faltava: ha um `nao` entre o sujeito e a acao, e ele nao nega a acao.
PROMESSAS_COM_NEGACAO_INTERPOSTA_QUE_NAO_NEGA = (
    "A equipe nao tem previsao exata, mas vai te ligar hoje.",
    "A equipe nao consegue avaliar agora, mas vai te ligar mais tarde.",
    "O atendente nao esta livre agora e vai te ligar em seguida.",
    "A equipe nao vai demorar: vai te ligar ainda hoje.",
    "Uma profissional nao esta disponivel neste minuto e vai lhe retornar em breve.",
)

#: O par minimo da mesma classe: o `nao` NEGA a acao, e ai nao ha mencao nenhuma.
NEGACOES_DA_PROPRIA_ACAO = (
    "A equipe nao vai assumir o seu caso.",
    "Nao, um atendente nao vai te ligar hoje.",
    "Nenhum profissional vai assumir isso por aqui.",  # negacao ANTES do sujeito: residual DECLARADO
)


@pytest.mark.parametrize("texto", PROMESSAS_COM_NEGACAO_INTERPOSTA_QUE_NAO_NEGA)
def test_negacao_que_nao_nega_a_acao_continua_sendo_mencao(texto: str) -> None:
    """Se o texto promete, a cerca tem de ver a promessa — mesmo com um `nao` no caminho."""
    assert menciona_encaminhamento(texto) is True


@pytest.mark.parametrize("texto", PROMESSAS_COM_NEGACAO_INTERPOSTA_QUE_NAO_NEGA)
def test_a_promessa_com_negacao_interposta_e_recusada_sem_start(texto: str) -> None:
    """O invariante do F1: prometeu humano e nenhum start aconteceu -> o texto NAO sai."""
    recusa = motivo_de_recusa(texto, "escalate", start_aconteceu=False)

    assert recusa is not None, "texto que promete humano saiu intacto num turno sem start"
    assert recusa[0] == "promessa_sem_start"


@pytest.mark.parametrize("texto", NEGACOES_DA_PROPRIA_ACAO)
def test_a_negacao_da_propria_acao_nao_e_mencao(texto: str) -> None:
    """O par minimo: sem isto o lookbehind seria so' um `if True`.

    O terceiro caso e' o RESIDUAL DECLARADO (negacao ANTES do sujeito): ele conta como mencao, e a
    direcao do erro e' deliberada — a cerca dispara e troca o texto por uma constante verdadeira.
    """
    esperado = texto.startswith("Nenhum")  # o residual declarado
    assert menciona_encaminhamento(texto) is esperado


def test_a_negacao_interposta_nao_reabre_o_c1() -> None:
    """Nao-vacuidade na outra direcao: o texto do C1 (negacao FORA da janela) segue mencionando."""
    c1 = "Sem mais detalhes eu nao consigo avaliar, entao a equipe vai te ligar."
    assert menciona_encaminhamento(c1) is True


# --- 2. `numero` e' pista de telefone, menos no genitivo administrativo ------------------------

NUMEROS_DE_CONTATO = (
    "Nosso numero e 3003 1234.",
    "O numero para contato e 3003 1234.",
    "O numero de atendimento e 4004 5678.",
    "O numero de telefone e 3003 1234.",
)

NUMEROS_ADMINISTRATIVOS = (
    "O numero do protocolo e 2026 0921.",
    "O numero do procedimento e 31602096.",
    "O numero da guia e 2026 0921.",
    "O numero dos pedidos anteriores e 31602096.",
    "O numero das autorizacoes e 2026 0921.",
)


@pytest.mark.parametrize("texto", NUMEROS_DE_CONTATO)
def test_numero_de_contato_com_telefone_e_recusado(texto: str) -> None:
    """A Helena nao sabe qual numero atende o contrato de quem esta do outro lado."""
    motivo = motivo_de_canal_nao_confirmado(texto)

    assert motivo is not None, "telefone inventado saiu ao beneficiario"
    assert motivo[0] == "canal_nao_confirmado"


@pytest.mark.parametrize("texto", NUMEROS_ADMINISTRATIVOS)
def test_numero_no_genitivo_administrativo_passa(texto: str) -> None:
    """O par maximo: recusar isto manda duvida administrativa CORRETA para a fila de gente."""
    assert motivo_de_canal_nao_confirmado(texto) is None


def test_o_recorte_e_do_genitivo_e_nao_da_palavra() -> None:
    """A diferenca medida entre `d[oa]s?` e `d[eoa]s?`, que e' o erro que esta rodada corrigiu.

    `de` fica FORA do recorte: "numero de atendimento" e "numero de telefone" sao contato. Com o
    `d[eoa]s?` proposto no review, os dois passariam — e um deles era, no proprio review, um dos
    quatro textos medidos como telefone a recusar.
    """
    assert motivo_de_canal_nao_confirmado("O numero de atendimento e 4004 5678.") is not None
    assert motivo_de_canal_nao_confirmado("O numero do atendimento anterior e 4004 5678.") is None
