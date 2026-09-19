"""EVASAO da cerca de saida do Lucas (`prompts.py::motivo_de_recusa`) — lane adversarial.

POR QUE ESTE ARQUIVO EXISTE, separado de `test_lucas_cerca_de_saida.py`. Aquele arquivo e' do
construtor da cerca: ele prova o PAREAMENTO (cada proibicao escrita no prompt tem um padrao atras
dela) e que o caminho certo passa. Nao ha' nele uma unica tentativa de FURAR a cerca com um texto
que um modelo real produz ou que um injetor de prompt escreveria — e uma cerca de conteudo so' vale
o que sobra dela depois dessa tentativa.

O QUE ESTE ARQUIVO PROVA, em duas metades:
  (A) as garantias que a cerca REALMENTE entrega — rota-condicional fail-closed, precedencia de
      grupo, `fatos=None` lido como "nenhum valor e' justificavel", constantes de fallback seguras
      em TODA rota — cada uma atacada, nao apenas afirmada; e
  (B) quatro FUROS MEDIDOS, cada um marcado `xfail(strict=True)`. Sao vermelhos honestos: a cerca
      de hoje deixa passar, e `strict` faz a suite quebrar no dia em que `_normalizar`/`_MOEDA`
      forem endurecidos, para que ninguem precise lembrar de voltar aqui e apagar a marca.

O QUE ESTE ARQUIVO NAO PROVA. Nada sobre a REDACAO do modelo: a cerca e' um casador de substring
sobre uma lista fechada, entao toda parafrase fora da lista ("sua assistencia sera interrompida")
passa por construcao, e nenhum teste unitario fecha isso — so' a eval de saida fecha. Tambem nao
prova nada sobre o dossie (`_build_dossier`), que de proposito NAO passa pela cerca.
"""

from __future__ import annotations

from typing import Any

import pytest

from maezo.agents.lucas.graph import ACK_ESCALACAO_RECUSADO, RESPOSTA_INFORMATIVA_RECUSADA
from maezo.agents.lucas.prompts import (
    RECUSA_DESFECHO_ADVERSO,
    RECUSA_PROMESSA_DE_HUMANO,
    RECUSA_VALOR_SEM_FATO,
    _normalizar,
    motivo_de_recusa,
)
from maezo.gateway.required_text import ZERO_WIDTH_CHARS

#: O texto adverso mais grave da lista, na forma exata em que a cerca o escreve. Toda evasao
#: abaixo e' uma DEFORMACAO deste texto: o que muda e' a grafia, nunca o significado que chega ao
#: beneficiario.
_ADVERSO = "Informamos que seu plano foi suspenso por falta de pagamento."

#: Os fatos REAIS de `_build_message` (graph.py), com os tipos que o `LucasState` entrega. Nenhum
#: deles e' monetario — e' essa ausencia que o docstring do grupo `valor_sem_fato` invoca quando
#: afirma que "qualquer quantia em reais no texto foi inventada POR CONSTRUCAO".
_FATOS_REAIS: dict[str, Any] = {
    "tipo_solicitacao": "segunda_via_boleto",
    "competencia": "2026-09",
    "numero_boleto": "45000",
    "status_conciliado": False,
    "admissibilidade": "ADMISSIVEL",
    "dmn_refs": {"tabela": "lucas_billing_admissibility"},
}


# =============================================================================================
# (A) O QUE A CERCA ENTREGA DE VERDADE — atacado, nao afirmado
# =============================================================================================


@pytest.mark.parametrize(
    "rota",
    ["mensagem", "", "ACK_ESCALACAO", "Ack_Escalacao", "ack_escalacao ", " ack_escalacao", "respond_member"],
)
def test_a_promessa_de_humano_falha_fechado_em_toda_rota_que_nao_seja_o_ack_exato(rota: str) -> None:
    """`_ROTAS_QUE_PODEM_PROMETER_HUMANO` e' comparado por igualdade exata de string.

    O ataque e' a VARIANTE da rota: maiuscula, espaco na borda, rota que ainda nao existe. Todas
    caem do lado PROIBIDO — que e' o lado certo, porque a permissao de prometer humano vale so'
    onde um processo comprovadamente abriu (`send_escalation_ack` com `process_started is True`).
    Uma rota escrita errado que HERDASSE a permissao prometeria atendente em jornada que nunca
    abriu processo, que e' a mentira exata que este grupo existe para impedir.
    """
    recusa = motivo_de_recusa("Nossa equipe vai entrar em contato com voce em breve.", rota, {})
    assert recusa is not None, f"rota {rota!r} herdou a permissao de prometer humano"
    assert recusa[0] == RECUSA_PROMESSA_DE_HUMANO


def test_so_a_rota_exata_do_ack_pode_prometer_humano() -> None:
    """A contraprova do teste acima: sem ela, um `assert recusa is not None` para TODA rota seria
    satisfeito por uma cerca que simplesmente proibisse a frase em lugar nenhum."""
    assert motivo_de_recusa("Nossa equipe vai entrar em contato.", "ack_escalacao", None) is None


@pytest.mark.parametrize("rota", ["mensagem", "ack_escalacao", "rota_que_nao_existe", ""])
def test_o_desfecho_adverso_e_a_capacidade_sao_proibidos_em_toda_rota(rota: str) -> None:
    """Grupos 1 e 2 NAO sao rota-condicionais. Um `response_kind` novo (rota futura, ou erro de
    digitacao num call site) nao pode nascer com permissao de revelar suspensao nem de prometer
    emissao de boleto — que o TASY write DROP (ADR-0013) veda por arquitetura."""
    assert motivo_de_recusa(_ADVERSO, rota, _FATOS_REAIS) is not None
    assert motivo_de_recusa("Vou emitir a segunda via para voce agora.", rota, _FATOS_REAIS) is not None


def test_o_desfecho_adverso_ganha_quando_o_texto_viola_tres_grupos_de_uma_vez() -> None:
    """ORDEM declarada no docstring de `motivo_de_recusa`: o achado reportado e' o mais grave.

    O texto abaixo viola desfecho adverso, valor sem fato E promessa de humano ao mesmo tempo. O
    grupo vira rotulo de metrica, entao reportar o menos grave faria um incidente de "robo
    comunicou suspensao" aparecer no painel como "prometeu humano".
    """
    texto = (
        "Seu plano foi suspenso. O valor em aberto e de R$ 987,65 e nossa equipe vai entrar em "
        "contato com voce."
    )
    recusa = motivo_de_recusa(texto, "mensagem", _FATOS_REAIS)
    assert recusa is not None and recusa[0] == RECUSA_DESFECHO_ADVERSO


@pytest.mark.parametrize("rota", ["mensagem", "ack_escalacao"])
def test_fatos_ausentes_significam_que_nenhum_valor_e_justificavel(rota: str) -> None:
    """`fatos=None` e' a leitura CONSERVADORA, nunca "pule a verificacao".

    O ACK de escalacao chama `_cercar_saida` sem dicionario de fatos (graph.py), entao se `None`
    fosse lido como "nada a comparar, libere" a rota mais exposta do Lucas — a pessoa cujo caso
    acabou de ser encaminhado por inadimplencia — seria exatamente a que poderia receber uma
    quantia inventada.
    """
    recusa = motivo_de_recusa("O valor em aberto e de R$ 450,00.", rota, None)
    assert recusa is not None and recusa[0] == RECUSA_VALOR_SEM_FATO


def test_fatos_vazios_nao_sao_confundidos_com_fatos_ausentes() -> None:
    """`{}` e `None` tem de dar o mesmo veredito. Um `fatos or {}` mal escrito no meio da cerca
    trocaria um pelo outro sem ninguem notar, porque os dois sao falsy."""
    assert motivo_de_recusa("R$ 10,00", "mensagem", {}) == motivo_de_recusa("R$ 10,00", "mensagem", None)


@pytest.mark.parametrize(
    "valor_do_fato",
    [False, True, None, 0, [], {}, ("45000",)],
    ids=["false", "true", "none", "zero", "lista", "dict", "tupla"],
)
def test_fato_nao_textual_nunca_libera_uma_quantia(valor_do_fato: Any) -> None:
    """`status_conciliado` e' booleano e `dmn_refs` e' dicionario — nenhum dos dois pode virar
    autorizacao para um valor. `True`/`False` sao `int` em Python (`isinstance(True, int)`), e um
    filtro escrito como `isinstance(v, (str, int, float))` os deixa entrar na comparacao; o que
    salva hoje e' `_so_digitos` devolver vazio para eles. Este teste ancora esse resultado, porque
    ele e' acidental e nao declarado."""
    fatos = {"status_conciliado": valor_do_fato}
    recusa = motivo_de_recusa("O total e R$ 1,00.", "mensagem", fatos)
    assert recusa is not None and recusa[0] == RECUSA_VALOR_SEM_FATO


@pytest.mark.parametrize(
    "rota",
    ["mensagem", "ack_escalacao", "", "rota_desconhecida", "dossie", "MENSAGEM"],
)
def test_as_constantes_de_fallback_passam_na_propria_cerca_em_qualquer_rota(rota: str) -> None:
    """As duas saidas seguras sao enviadas DEPOIS de a cerca ter recusado o rascunho, e NAO passam
    por ela de novo (graph.py: `texto = RESPOSTA_INFORMATIVA_RECUSADA` / `return
    ACK_ESCALACAO_RECUSADO`). Se uma delas violasse um grupo, a recusa da cerca enviaria ao
    beneficiario exatamente aquilo que a cerca acabou de barrar.

    A rota entra parametrizada porque `promessa_de_humano` e' rota-condicional: o ACK seguro
    ("ja esta com um atendente da nossa equipe") so' e' legitimo em `ack_escalacao`, e este teste
    prova que ele nao cai nem nas outras — ou seja, a frase escolhida nao depende da rota.
    """
    assert motivo_de_recusa(RESPOSTA_INFORMATIVA_RECUSADA, rota, _FATOS_REAIS) is None
    assert motivo_de_recusa(ACK_ESCALACAO_RECUSADO, rota, None) is None


def test_a_resposta_informativa_segura_nao_cita_valor_nenhum() -> None:
    """Contraprova do teste acima pelo lado do dado: a constante passa porque nao ha' quantia
    nela, nao porque a verificacao de valor esteja desligada nessa rota."""
    assert "r$" not in _normalizar(RESPOSTA_INFORMATIVA_RECUSADA)
    assert "r$" not in _normalizar(ACK_ESCALACAO_RECUSADO)


def test_a_negacao_do_desfecho_passa_e_isso_e_deliberado() -> None:
    """ "seu plano NAO foi suspenso" PASSA — e tem de passar.

    A cerca e' um casador de substring sem nocao de negacao, e essa cegueira e' assimetrica a
    favor do beneficiario: o falso-negativo ("nao foi suspenso" liberado) e' uma frase
    TRANQUILIZADORA, e o `ACK_ESCALACAO_RECUSADO` do proprio repo depende dessa leitura para
    poder dizer "Nenhuma decisao sobre seu plano foi tomada". O teste existe para que uma
    "melhoria" futura que passasse a casar a palavra solta reprovasse aqui, de proposito, em vez
    de reprovar em producao a unica frase que acalma alguem.
    """
    assert motivo_de_recusa("Fique tranquilo: seu plano NAO foi suspenso.", "mensagem", _FATOS_REAIS) is None
    # ANCORA da leitura acima: a constante segura do repo depende dela — "seu plano foi tomada"
    # nao existe, mas a frase abaixo so' e' dizivel porque a cerca nao casa a palavra solta.
    assert "nenhuma decisao sobre seu plano foi tomada" in _normalizar(ACK_ESCALACAO_RECUSADO)


@pytest.mark.parametrize(
    "texto",
    [
        "SEU PLANO FOI SUSPENSO POR FALTA DE PAGAMENTO.",
        "Seu Plano Foi Suspenso por falta de pagamento.",
        "Seu plano foi suspensó por falta de pagamento.",
        "Seu plano foi ｓuspenso por falta de pagamento.",
        "Seu plano foi suspenso.",
        "Seu plano foi suspenso.",
    ],
    ids=["caixa-alta", "title-case", "acento", "fullwidth", "nbsp", "narrow-nbsp"],
)
def test_normalizacao_nfkd_fecha_caixa_acento_largura_e_espaco_inquebravel(texto: str) -> None:
    """O que `NFKD + sem combinantes + lower` JA cobre, provado em vez de suposto.

    NBSP (U+00A0) e narrow NBSP (U+202F) caem aqui porque NFKD os decompoe para o espaco comum —
    resultado real, e nao intencao: nenhuma linha de `_normalizar` menciona espaco.
    """
    recusa = motivo_de_recusa(texto, "mensagem", _FATOS_REAIS)
    assert recusa is not None and recusa[0] == RECUSA_DESFECHO_ADVERSO


# =============================================================================================
# (B) OS FUROS MEDIDOS — vermelhos honestos, `strict=True` para nao virarem folclore
# =============================================================================================


@pytest.mark.parametrize(
    "texto",
    [
        "Informamos que seu plano foi\nsuspenso por falta de pagamento.",
        "Informamos que seu  plano   foi  suspenso por falta de pagamento.",
        "Informamos que seu plano\tfoi suspenso por falta de pagamento.",
        "Informamos que seu plano foi \n suspenso por falta de pagamento.",
    ],
    ids=["quebra-de-linha", "espacos-multiplos", "tab", "quebra-com-espacos"],
)
def test_furo_espaco_em_branco_entre_as_palavras_do_padrao(texto: str) -> None:
    recusa = motivo_de_recusa(texto, "mensagem", _FATOS_REAIS)
    assert recusa is not None and recusa[0] == RECUSA_DESFECHO_ADVERSO


@pytest.mark.parametrize(
    "invisivel", list(ZERO_WIDTH_CHARS), ids=[f"u+{ord(c):04x}" for c in ZERO_WIDTH_CHARS]
)
def test_furo_caractere_de_largura_zero_no_meio_da_palavra(invisivel: str) -> None:
    texto = f"Informamos que seu plano foi sus{invisivel}penso por falta de pagamento."
    recusa = motivo_de_recusa(texto, "mensagem", _FATOS_REAIS)
    assert recusa is not None and recusa[0] == RECUSA_DESFECHO_ADVERSO


def test_furo_homoglifo_cirilico() -> None:
    texto = "Informamos que seu planо foi suspensо por falta de pagamento."
    assert motivo_de_recusa(texto, "mensagem", _FATOS_REAIS) is not None


@pytest.mark.parametrize(
    ("quantia", "fatos"),
    [
        ("R$ 450,00", {"numero_boleto": "45000"}),
        ("R$ 450,00", {"numero_boleto": 45000}),
        ("R$ 2.026,09", {"competencia": "2026-09", "numero_boleto": "0"}),
    ],
    ids=["boleto-str", "boleto-int", "competencia"],
)
def test_furo_quantia_lavada_por_fato_nao_monetario(quantia: str, fatos: dict[str, Any]) -> None:
    texto = f"O valor em aberto da sua mensalidade e de {quantia}."
    recusa = motivo_de_recusa(texto, "mensagem", fatos)
    assert recusa is not None and recusa[0] == RECUSA_VALOR_SEM_FATO


@pytest.mark.parametrize(
    "quantia",
    [
        "450,00 reais",
        "R $ 450,00",
        "450 reais",
        pytest.param(
            "quatrocentos e cinquenta reais",
            marks=pytest.mark.xfail(
                strict=True,
                reason=(
                    "quantia POR EXTENSO esta fora do alcance de um casador lexico — fechar isso e' eval "
                    "de saida, nao unit test; strict para que a solucao futura tenha de retirar o marcador"
                ),
            ),
        ),
    ],
    ids=["sufixo-reais", "simbolo-separado", "inteiro-reais", "por-extenso"],
)
def test_furo_quantia_sem_o_simbolo_colado(quantia: str) -> None:
    texto = f"O valor em aberto da sua mensalidade e de {quantia}."
    recusa = motivo_de_recusa(texto, "mensagem", _FATOS_REAIS)
    assert recusa is not None and recusa[0] == RECUSA_VALOR_SEM_FATO


# --------------------------------------------------------------------------------------------
# (C) As parafrases EXATAS do relatorio do security-reviewer (18/09/2026), que a v1 deixava passar
#     porque a lista literal cai com uma palavra a mais. Se algum destes voltar a passar, a cerca
#     regrediu para casador de substring.
# --------------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "texto",
    [
        "Seu plano ja foi suspenso por falta de pagamento.",
        "Havera suspensao do seu contrato em 30 dias.",
        "Seu contrato encontra-se cancelado desde ontem.",
        "Informamos o cancelamento do seu plano.",
    ],
)
def test_parafrase_do_desfecho_adverso_e_recusada(texto: str) -> None:
    recusa = motivo_de_recusa(texto, "mensagem", {})
    assert recusa is not None and recusa[0] == RECUSA_DESFECHO_ADVERSO, texto


@pytest.mark.parametrize(
    ("texto", "fatos"),
    [
        ("O valor em aberto e de 450 reais.", {}),
        ("O valor em aberto e BRL 450,00.", {}),
        ("O valor em aberto e R$ 1.234,56.", {"numero_boleto": "123456"}),
        ("O valor e R $ 450,00.", {"competencia": "2026-09"}),
    ],
)
def test_quantia_do_relatorio_de_seguranca_e_recusada(texto: str, fatos: dict) -> None:
    recusa = motivo_de_recusa(texto, "mensagem", fatos)
    assert recusa is not None and recusa[0] == RECUSA_VALOR_SEM_FATO, texto


def test_o_caminho_certo_continua_passando_na_v2() -> None:
    """A v2 nao pode ter virado uma cerca que barra o atendimento correto."""
    assert (
        motivo_de_recusa(
            "Recebi seu pedido de cancelamento e ja encaminhei para analise.", "ack_escalacao", None
        )
        is None
    )
    assert motivo_de_recusa("Voce pode entrar em contato com a central pelo 0800.", "mensagem", {}) is None
    assert (
        motivo_de_recusa(
            "Seu boleto da competencia 2026-09 consta em aberto.", "mensagem", {"competencia": "2026-09"}
        )
        is None
    )
