"""TRAVAS DE SAIDA de 25/10/2026: contagem vazada e convite a mandar midia.

- Contagem: o modelo obedeceu a regra de tamanho do prompt ESCREVENDO a contagem na resposta (uma frase
  seguida de "(N palavras)"). A instrucao que mandava contar saiu do prompt e a trava tira a contagem.
- Midia: a Helena so' le' texto, mas a redacao do modelo convidou a pessoa a mandar a foto de um pedido.
  O prompt passou a proibir isso e a trava troca a frase pelo aviso de que aqui so' se le' texto.

Frases escritas para estes testes (nenhuma copiada de roteiro do simulador ou do conjunto oculto)."""

from __future__ import annotations

import re
from typing import Any

import pytest

from maezo.agents.helena import prompts
from maezo.agents.helena import travas_saida as ts
from maezo.agents.helena.habilidades.conversa import assuntos
from maezo.agents.helena.textos import PADRAO as TEXTOS_PADRAO
from maezo.platform.webhooks.whatsapp.dispatch import NON_TEXT_ACK_TEXT
from tests.unit.agents.test_helena_saude_gerativa import _INFO, _Conversa

# --- contagem ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        ("Entendi que a consulta foi ontem. (7 palavras)", "Entendi que a consulta foi ontem."),
        (
            "Seu boleto vence dia 10 (12 words). Posso ajudar em algo mais? (6 palavras)",
            "Seu boleto vence dia 10. Posso ajudar em algo mais?",
        ),
        (
            "Resumo do que voce me contou [3 frases]:\nfebre desde ontem.",
            "Resumo do que voce me contou:\nfebre desde ontem.",
        ),
        ("O aplicativo mostra a carteirinha. (~9 palavras)", "O aplicativo mostra a carteirinha."),
        ("Pode me dizer o nome do exame? [40 caracteres]", "Pode me dizer o nome do exame?"),
        ("Tudo certo por aqui (palavras: 4).", "Tudo certo por aqui."),
        ("Linha um. (3 frases)\nLinha dois (1 sentence).", "Linha um.\nLinha dois."),
    ],
)
def test_contagem_vazada_sai(texto: str, esperado: str) -> None:
    assert ts.sem_contagem(texto) == esperado
    assert ts.na_saida(texto) == esperado


@pytest.mark.parametrize(
    "texto",
    [
        "Tome cuidado: o prazo é de 3 dias úteis (pela ANS).",
        "Você tem 2 dependentes (filho e cônjuge) no plano.",
        "Sua coparticipação foi de R$ 25,00 (consulta de 12/10).",
        "Use a senha de 6 dígitos do aplicativo.",
        "A carteirinha tem 17 caracteres, sem espaço.",
    ],
)
def test_controle_negativo_numero_entre_parenteses_que_nao_e_contagem_fica(texto: str) -> None:
    assert ts.sem_contagem(texto) is texto
    assert ts.na_saida(texto) is texto


# --- oferta de midia ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "texto",
    [
        "Você pode enviar a foto do pedido médico por aqui mesmo. 😊",
        "Se preferir, me mande um áudio contando o que sentiu.",
        "Pode anexar o PDF do resultado aqui na conversa.",
        "Manda um print da tela que eu te ajudo.",
        "É só encaminhar o arquivo da guia por aqui.",
        "A imagem do exame você pode mandar aqui.",
    ],
)
def test_convite_a_mandar_midia_vira_o_aviso_de_so_texto(texto: str) -> None:
    saida = ts.sem_oferta_de_midia(texto)
    assert saida == ts.AVISO_SO_TEXTO
    assert ts.na_saida(texto) == ts.AVISO_SO_TEXTO


def test_convite_no_meio_da_resposta_troca_so_a_frase() -> None:
    texto = (
        "Você pode enviar a foto do pedido médico por aqui mesmo. 😊\n\n"
        "Sou Helena, uma assistente virtual. Se quiser falar com uma pessoa da equipe, é só me pedir."
    )
    saida = ts.na_saida(texto)
    assert saida.startswith(ts.AVISO_SO_TEXTO + "\n\nSou Helena, uma assistente virtual.")
    assert "foto" not in saida and "😊" not in saida
    # Dois convites na mesma resposta: o aviso sai uma vez so'.
    dois = "Pode mandar a foto. Ou, se preferir, envie um áudio. Qual é o exame?"
    assert ts.na_saida(dois) == f"{ts.AVISO_SO_TEXTO} Qual é o exame?"


@pytest.mark.parametrize(
    "texto",
    [
        NON_TEXT_ACK_TEXT,
        "Não precisa mandar foto: me diga só o nome do exame.",
        "O médico pediu um exame de imagem, e quem marca é a central do plano.",
        "Você pode enviar os documentos do reembolso pelo aplicativo Austa Clínicas.",
        "Me envie seu CPF (só os números).",
        "Pode me escrever o nome do exame?",
        "A tomografia é um exame de imagem.",
    ],
)
def test_controle_negativo_aviso_negacao_outro_canal_e_exame_de_imagem_ficam(texto: str) -> None:
    assert ts.sem_oferta_de_midia(texto) is texto
    assert ts.na_saida(texto) is texto


def test_aviso_de_audio_e_foto_continua_e_bate_com_a_trava() -> None:
    assert NON_TEXT_ACK_TEXT.startswith("Por aqui eu consigo ler só mensagens de texto.")
    assert ts.AVISO_SO_TEXTO.startswith("Por aqui eu consigo ler só mensagens de texto")


# --- as fontes ---------------------------------------------------------------------------------------


def test_o_prompt_de_resposta_nao_manda_mais_contar_e_proibe_midia() -> None:
    p = prompts.response_prompt()
    assert "CONTE AS PALAVRAS" not in p
    assert "nunca escreva contagem de palavras" in p
    assert "VOCE SO' LE' TEXTO" in p
    assert "foto" in p and "ESCREVA aqui" in p


@pytest.mark.parametrize("chave", sorted(TEXTOS_PADRAO))
def test_nenhum_texto_fixo_da_helena_convida_midia_nem_traz_contagem(chave: str) -> None:
    texto = TEXTOS_PADRAO[chave]
    if isinstance(texto, str):
        assert ts.na_saida(texto) == ts.sem_links(texto), chave


def test_textos_do_pacote_nao_convidam_midia() -> None:
    from pathlib import Path

    import yaml

    raiz = Path(__file__).resolve().parents[3] / "spec" / "agents" / "helena" / "pacote" / "textos.yaml"
    dados = yaml.safe_load(raiz.read_text(encoding="utf-8")) or {}
    textos = [v for v in _folhas(dados) if isinstance(v, str)]
    assert textos
    for t in textos:
        assert ts.sem_oferta_de_midia(t) is t
        assert ts.sem_contagem(t) is t


def _folhas(no: Any) -> list[Any]:
    if isinstance(no, dict):
        return [f for v in no.values() for f in _folhas(v)]
    if isinstance(no, list):
        return [f for v in no for f in _folhas(v)]
    return [no]


def test_retomada_de_assunto_passa_pela_trava() -> None:
    assert assuntos.texto_de_retomada("Sobre {assunto}, ficou alguma dúvida? (6 palavras)", "saude") == (
        "Sobre o assunto de saúde, ficou alguma dúvida?"
    )
    assert assuntos.texto_de_retomada(assuntos.TEXTO_RETOMADA, "plano") == (
        "Sobre os dados do seu plano, ficou alguma dúvida?"
    )


# --- o envio de verdade --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("redacao", "nao_sai"),
    [
        (
            "Entendi que voce quer saber do plano. (8 palavras)\nPosso ajudar com mais algo? (5 palavras)",
            "palavras)",
        ),
        ("Você pode me mandar a foto da carteirinha por aqui.", "foto"),
        ("Envie um áudio se for mais fácil. Pelo aplicativo voce ve os dados do plano.", "áudio"),
    ],
)
async def test_resposta_do_turno_sai_com_as_travas(redacao: str, nao_sai: str) -> None:
    conversa = _Conversa(gerativa=False)
    await conversa.diz("queria tirar uma duvida sobre o plano", _INFO, redacao)
    assert conversa.whatsapp.enviados, "nada saiu"
    enviado = "\n".join(conversa.whatsapp.enviados)
    assert nao_sai not in enviado
    assert not re.search(r"\(\s*\d+\s*palavras\s*\)", enviado)


async def test_controle_negativo_resposta_limpa_sai_igual() -> None:
    redacao = "Pelo aplicativo Austa Clínicas você vê os dados do plano. Posso ajudar com mais algo?"
    conversa = _Conversa(gerativa=False)
    await conversa.diz("queria tirar uma duvida sobre o plano", _INFO, redacao)
    assert any(redacao in e for e in conversa.whatsapp.enviados)
