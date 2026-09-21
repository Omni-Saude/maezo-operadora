"""Os canais existem, tem nome, e a Helena so' pode citar esses tres — F7 de 21/09/2026.

O QUE FOI MEDIDO. *"O aplicativo do plano"*, *"o portal"* e *"a central de atendimento"*
apareceram em CINCO casos da bateria (`A3`, `D2`, `E2`, `E4`, `E5`). O texto integral do `E4`:

    "Este canal pode te orientar sobre como consultar o valor da mensalidade. Voce pode verificar
     pelo aplicativo do plano, no portal da operadora ou entrando em contato com a central de
     atendimento."

Tres nomes, e nenhum deles e' o nome de nada. Mandar alguem para um canal que ninguem confirmou e'
mandar para o vazio — e o relatorio do diretor classificou isso como promessa de CAPACIDADE, a
categoria que ja' existe na cerca de saida (`PROMESSA_DE_CAPACIDADE_PROIBIDA`) e que estava
passando por aqui.

A DECISAO DO DONO (21/09/2026): existem exatamente TRES canais, com estes nomes. O aplicativo
*Austa Clinicas* e *o portal do plano* resolvem as mesmas coisas — boleto/mensalidade, carteirinha
digital, rede credenciada e historico de consultas/exames — e *a central de atendimento do plano*
resolve qualquer duvida administrativa, SEM numero de telefone no texto.

POR QUE UM GRUPO IRMAO, e nao mais padroes na lista de capacidade. O grupo vai para o CONTADOR e o
padrao vai para o LOG (a decisao esta no comentario de `prompts.py`): misturar canal inventado com
"eu emito seu boleto" faria as duas coisas somarem no mesmo numero, e sao problemas diferentes —
uma e' a Helena prometendo fazer o que nao faz, a outra e' ela mandando a pessoa a um lugar que nao
existe. E ha' uma razao mecanica: os textos legitimos que a cerca de capacidade ja' aprova
("...fica disponivel no aplicativo e no portal do beneficiario") sao exatamente os que o canal
reprova, porque aquele nome nao e' o nome confirmado.

POR QUE A CERCA E' PARTE SUBSTRING E PARTE PALAVRA INTEIRA. "aplicativo do plano" pode ser casado
cru; "site" nao, porque e' substring de "visite", e "app" e' substring de "whatsapp" — que e' o
canal em que a Helena literalmente fala. Casar os dois por substring reprovaria texto correto, e
uma cerca que reprova o certo e' desligada na semana seguinte.

WIRING: a funcao pura `motivo_de_canal_nao_confirmado` e' consumida no UNICO ponto por onde passa
todo texto que chega ao beneficiario (`graph.py::_respond_llm`, ao lado de `motivo_de_recusa`).
Ver a pendencia declarada no relatorio desta entrega — `graph.py` pertence a outra frente em voo
(F1/F2, que muda a assinatura de `motivo_de_recusa` nessa mesma linha).
"""

from __future__ import annotations

import pytest

from maezo.agents.helena.prompts import (
    CANAIS_CONFIRMADOS,
    RECUSA_CANAL_NAO_CONFIRMADO,
    RECUSA_DE_SAIDA_VERSION,
    RECUSA_NEGATIVA_CLINICA,
    RECUSA_PROMESSA_DE_CAPACIDADE,
    RECUSA_PROMESSA_DE_HUMANO,
    motivo_de_canal_nao_confirmado,
    response_prompt,
)

#: O TEXTO REAL do caso `E4`, medido em 21/09/2026. Nao e' exemplo inventado.
E4_MEDIDO = (
    "Este canal pode te orientar sobre como consultar o valor da mensalidade. Você pode "
    "verificar pelo aplicativo do plano, no portal da operadora ou entrando em contato com a "
    "central de atendimento."
)
#: O texto que passa a valer no `E4` — os mesmos canais, com os nomes que existem. Escrito COM
#: acento de proposito: a normalizacao da cerca e' o que faz "Austa Clinicas" casar com
#: "Austa Clínicas", e um teste sem acento nunca exercitaria isso.
E4_CONFORME = (
    "O valor da mensalidade, o vencimento e a segunda via do boleto ficam no aplicativo Austa "
    "Clínicas e no portal do plano. Se preferir resolver com uma pessoa, a central de "
    "atendimento do plano também atende essa dúvida."
)
#: O caso `E5` — historico de consultas. O canal resolve, e a Helena nao consulta.
E5_CONFORME = (
    "O histórico de consultas e exames fica no aplicativo Austa Clínicas e no portal do plano — "
    "é lá que aparecem os atendimentos anteriores. Por aqui eu não consigo consultar esse "
    "histórico."
)


# =================================================================================================
# 1. A lista unica: tres canais, com nome e com o que cada um resolve
# =================================================================================================


def test_sao_exatamente_tres_canais() -> None:
    """A decisao do dono e' um numero fechado. Um quarto canal entra por decisao, nao por PR."""
    assert len(CANAIS_CONFIRMADOS) == 3


def test_os_nomes_sao_os_confirmados_pelo_dono() -> None:
    assert [c.nome for c in CANAIS_CONFIRMADOS] == [
        "o aplicativo Austa Clinicas",
        "o portal do plano",
        "a central de atendimento do plano",
    ]


def test_todo_canal_declara_o_que_resolve() -> None:
    """Sem isso a lista responde "pode citar?" e nao responde "para que?".

    O `E4` estava certo em citar o app para mensalidade e erraria em citar o app para autorizacao
    de exame — e a diferenca e' esta coluna.
    """
    for canal in CANAIS_CONFIRMADOS:
        assert canal.resolve, f"{canal.nome}: nao diz o que resolve"
        for item in canal.resolve:
            assert len(item.strip()) > 3


def test_o_app_e_o_portal_resolvem_as_mesmas_coisas() -> None:
    """A decisao do dono e' explicita: os dois resolvem boleto, carteirinha, rede e historico.

    Se um dia divergirem, o prompt tem de parar de oferecer os dois como alternativas da mesma
    frase — e essa divergencia comeca aqui, nao no texto.
    """
    app, portal, central = CANAIS_CONFIRMADOS

    assert app.resolve == portal.resolve
    assert central.resolve != app.resolve


def test_o_que_o_app_e_o_portal_resolvem_cobre_os_casos_medidos() -> None:
    """`E4` (mensalidade) e `E5` (historico) sao os dois casos da bateria que este canal atende."""
    resolve = " ".join(CANAIS_CONFIRMADOS[0].resolve).lower()

    assert "mensalidade" in resolve
    assert "boleto" in resolve
    assert "carteirinha" in resolve
    assert "rede credenciada" in resolve
    assert "hist" in resolve  # historico de consultas e exames


# =================================================================================================
# 2. O prompt cita esses nomes, e nomeia os que nao existem
# =================================================================================================


def test_o_prompt_de_resposta_e_gerado_da_lista() -> None:
    """Se o prompt puder divergir da constante, a "lista unica" nao e' unica."""
    texto = response_prompt()

    for canal in CANAIS_CONFIRMADOS:
        assert canal.nome in texto, f"{canal.nome!r} nao aparece no prompt de resposta"
        for item in canal.resolve:
            assert item in texto, f"o que {canal.nome!r} resolve ({item!r}) nao aparece no prompt"


def test_o_prompt_de_resposta_nomeia_os_canais_que_nao_existem() -> None:
    """Proibir "canal nao confirmado" em abstrato nao ensina nada ao modelo. Os nomes errados
    medidos na bateria vao no texto, porque sao os que ele produziu."""
    texto = response_prompt()

    for errado in ("aplicativo do plano", "portal da operadora", "site"):
        assert errado in texto, f"o prompt nao diz que {errado!r} nao existe"


def test_o_prompt_de_resposta_proibe_numero_de_telefone() -> None:
    """A decisao do dono: a central e' citada pelo nome, SEM numero — a Helena nao tem como saber
    qual numero atende o contrato de quem esta do outro lado."""
    texto = response_prompt()

    assert "0800" in texto
    assert "numero de telefone" in texto


# =================================================================================================
# 3. A cerca: o que estava passando passa a ser recusado
# =================================================================================================


def test_o_texto_medido_no_e4_e_recusado() -> None:
    """A prova de nao-vacuidade da categoria inteira: dois nomes inventados numa frase."""
    achado = motivo_de_canal_nao_confirmado(E4_MEDIDO)

    assert achado is not None, "o texto medido em 21/09 passou pela cerca nova"
    assert achado[0] == RECUSA_CANAL_NAO_CONFIRMADO


@pytest.mark.parametrize("texto", [E4_CONFORME, E5_CONFORME])
def test_os_textos_com_os_nomes_certos_passam(texto: str) -> None:
    """A outra metade: uma cerca que reprova tudo faz a Helena parar de orientar ninguem."""
    assert motivo_de_canal_nao_confirmado(texto) is None


@pytest.mark.parametrize(
    "texto",
    [
        "Você pode verificar pelo aplicativo do plano.",
        "Isso fica no app do convênio.",
        "Acesse o portal da operadora.",
        "Entre no portal do beneficiário.",
        "A segunda via sai no nosso site.",
        "Ligue no 0800 11 2222 para resolver.",
        "Você pode falar com a central no (17) 3222-1234.",
        "Se preferir, ligue para 3222-1234.",
        "Isso você resolve no aplicativo.",
        "Dá para ver no portal.",
        "Baixe o nosso app para consultar.",
    ],
)
def test_canal_fora_da_lista_e_recusado(texto: str) -> None:
    """Os nomes que a bateria produziu, mais as variacoes obvias da mesma familia.

    O termo GENERICO sem o nome confirmado ao lado ("no aplicativo", "no portal") entra aqui de
    proposito: "no aplicativo" nao e' menos vago que "aplicativo do plano" — a pessoa nao sabe
    qual baixar, e foi isso que o `E4` fez.
    """
    achado = motivo_de_canal_nao_confirmado(texto)

    assert achado is not None, f"passou: {texto!r}"
    assert achado[0] == RECUSA_CANAL_NAO_CONFIRMADO


@pytest.mark.parametrize(
    "texto",
    [
        "Pode continuar por aqui mesmo, no WhatsApp.",
        "Visite a rede credenciada no aplicativo Austa Clinicas.",
        "A central de atendimento do plano também atende essa dúvida.",
        "Recebi sua mensagem e vou encaminhar seu relato para nossa equipe de saúde.",
    ],
)
def test_o_que_e_legitimo_continua_passando(texto: str) -> None:
    """Os falsos positivos que quebrariam o caminho certo.

    "WhatsApp" contem "app" e "visite" contem "site" — casar por substring reprovaria o canal em
    que a Helena fala e uma frase correta sobre a rede credenciada. E um `escalate` legitimo nao
    cita canal nenhum: a cerca nao pode ter opiniao sobre ele.
    """
    assert motivo_de_canal_nao_confirmado(texto) is None


def test_a_cerca_ignora_texto_vazio() -> None:
    assert motivo_de_canal_nao_confirmado("") is None


# =================================================================================================
# 4. O grupo e' irmao — mesma forma de retorno, contador proprio
# =================================================================================================


def test_o_retorno_tem_a_mesma_forma_da_cerca_irma() -> None:
    """`(grupo, padrao)`, como `motivo_de_recusa` — e' o que faz o wiring ser uma linha.

    O ponto de chamada e' `graph.py::_respond_llm`, ao lado da cerca de recusa; a forma igual
    significa que o log (`padrao`) e o contador (`grupo`) daquele bloco servem as duas sem
    nenhum ramo novo.
    """
    achado = motivo_de_canal_nao_confirmado(E4_MEDIDO)

    assert isinstance(achado, tuple) and len(achado) == 2
    assert all(isinstance(parte, str) and parte for parte in achado)


def test_os_quatro_grupos_sao_rotulos_distintos() -> None:
    """Rotulo repetido soma no mesmo contador e apaga a diferenca entre dois defeitos."""
    grupos = (
        RECUSA_NEGATIVA_CLINICA,
        RECUSA_PROMESSA_DE_HUMANO,
        RECUSA_PROMESSA_DE_CAPACIDADE,
        RECUSA_CANAL_NAO_CONFIRMADO,
    )

    assert len(set(grupos)) == 4


def test_a_versao_da_cerca_subiu_com_a_categoria_nova() -> None:
    """`RECUSA_DE_SAIDA_VERSION` e' o numero que diz QUAL cerca estava valendo quando um texto foi
    recusado (ou deixado passar). Uma categoria nova sem bump aponta para a cerca antiga."""
    assert RECUSA_DE_SAIDA_VERSION.startswith("recusa-v")
    assert RECUSA_DE_SAIDA_VERSION != "recusa-v3", "a categoria de canal entrou; a versao tem de subir"
