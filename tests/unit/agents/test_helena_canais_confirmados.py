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

WIRING (LIGADO, e o comentario anterior envelheceu no merge): a funcao pura
`motivo_de_canal_nao_confirmado` e' consumida em `graph.py::_cercar_saida`, ao lado de
`motivo_de_recusa` — o ponto por onde passa todo texto que chega ao beneficiario, hoje incluindo a
rota `collect`. A pendencia que morava aqui ("graph.py pertence a outra frente em voo, F1/F2") foi
fechada pelo merge `3e6e1620`, e a prova esta em
`tests/unit/agents/test_helena_wiring_canal_e_apresentacao.py` (o chokepoint) e em
`test_helena_adv_canal.py` (a rota de coleta). Este arquivo segue cobrindo a LISTA e a FUNCAO.

SEGUNDA RODADA DE 21/09/2026 (blocos 5 em diante): auto-consistencia (todo nome confirmado passa na
propria cerca, sozinho), a cerca de nome da CENTRAL, telefone em qualquer forma, plural do termo
generico, caractere invisivel e a REFERENCIA DE VOLTA (o termo generico passa com qualquer nome
confirmado no texto, nao so' o pareado).

TERCEIRA RODADA DE 21/09/2026: a referencia de volta foi RECORTADA para o termo NU. No termo
QUALIFICADO ela legitimava nome INVENTADO por vizinhanca ("Baixe o aplicativo Austa Saude ... ou
fale com a central de atendimento do plano" passava), entao `exige` voltou a ser o nome da FAMILIA
e `exige_nu` ficou com a referencia de volta. Um dos quatro textos da referencia de volta mudou de
veredito por causa disso, e a troca esta' declarada num teste proprio; o corpus de nao-regressao
(`test_helena_corpus_cercas_de_saida.py`) e' o que impede a proxima troca de passar sem declaracao.
"""

from __future__ import annotations

import pytest

from maezo.agents.helena.graph import (
    RESPOSTA_FALHA_DE_REDACAO,
    RESPOSTA_FALHA_TECNICA_START,
    RESPOSTA_HANDOFF_JA_ABERTO,
    RESPOSTA_HANDOFF_RECUSADA,
    RESPOSTA_SEM_ENCAMINHAMENTO,
)
from maezo.agents.helena.prompts import (
    _NOMES_CONFIRMADOS,
    CANAIS_CONFIRMADOS,
    CANAL_TERMO_QUE_EXIGE_O_NOME,
    QUALQUER_CANAL_CONFIRMADO,
    RECUSA_CANAL_NAO_CONFIRMADO,
    RECUSA_DE_SAIDA_VERSION,
    RECUSA_NEGATIVA_CLINICA,
    RECUSA_PROMESSA_DE_CAPACIDADE,
    RECUSA_PROMESSA_DE_HUMANO,
    RESPOSTA_NAO_CONSIGO_IDENTIFICAR,
    RESPOSTA_SOU_ASSISTENTE_VIRTUAL,
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


# =================================================================================================
# 5. AUTO-CONSISTENCIA: a lista e a cerca sao o MESMO artefato (21/09/2026, segunda rodada)
# =================================================================================================


def test_todo_nome_confirmado_passa_na_cerca_sozinho() -> None:
    """A cerca de auto-consistencia que faltava, e a diferenca para o teste do bloco 3.

    `test_os_textos_com_os_nomes_certos_passam` usa FRASES em que o nome aparece junto de contexto;
    aqui o nome vai SOZINHO, que e' a forma minima em que ele pode chegar ao beneficiario. E' o que
    pega um par `(padrao, exige)` novo escrito ao contrario: um termo generico cujo `exige` nao
    esteja contido no proprio nome confirmado tornaria o prompt e a cerca inimigos, e o turno
    CORRETO viraria escalonamento humano (`graph.py::inform` ->
    `_start_escalation(motivo="falha_tecnica")`).
    """
    for canal in CANAIS_CONFIRMADOS:
        assert motivo_de_canal_nao_confirmado(canal.nome) is None, (
            f"a cerca reprova o nome confirmado, sozinho: {canal.nome!r}"
        )


def test_as_constantes_que_o_grafo_envia_passam_na_cerca_de_canal() -> None:
    """Os textos do modulo que chegam ao beneficiario SEM passar por modelo nenhum.

    A cerca de canal so' e' chamada no chokepoint de redacao (`_respond_llm`/`_cercar_saida`), e as
    substituicoes da cerca TEXTO x FATO acontecem DEPOIS dele, em `respond`. Ou seja: um canal
    inventado dentro de uma destas constantes sairia sem ninguem olhar. Vale tambem para as duas
    frases fixas de conformidade, que vao LITERALMENTE ao prompt e portanto ao beneficiario.
    """
    for constante in (
        RESPOSTA_HANDOFF_RECUSADA,
        RESPOSTA_HANDOFF_JA_ABERTO,
        RESPOSTA_SEM_ENCAMINHAMENTO,
        RESPOSTA_FALHA_TECNICA_START,
        RESPOSTA_FALHA_DE_REDACAO,
        RESPOSTA_SOU_ASSISTENTE_VIRTUAL,
        RESPOSTA_NAO_CONSIGO_IDENTIFICAR,
    ):
        assert motivo_de_canal_nao_confirmado(constante) is None, f"canal nao confirmado em {constante!r}"


@pytest.mark.parametrize(
    "texto",
    [
        "fale com a central de atendimento da operadora",
        "fale com a central de atendimento do convenio",
        "fale com a central do beneficiario",
        "ligue na central",
        "a central resolve isso",
    ],
)
def test_a_central_tambem_tem_cerca_de_nome(texto: str) -> None:
    """O TERCEIRO canal confirmado ficou sem cerca nenhuma ate' 21/09/2026 (segunda rodada).

    O aplicativo e o portal tinham as duas (nome errado proibido + termo generico exigindo o nome);
    a central, nenhuma. Entao "a central de atendimento da operadora" — exatamente a familia de
    nomes que o F7 mediu em CINCO casos — saia intacta ao lado de "portal da operadora", que era
    recusado. E' o descompasso que a propria lista existe para tornar visivel.
    """
    achado = motivo_de_canal_nao_confirmado(texto)

    assert achado is not None, f"nome de central que ninguem confirmou passou: {texto!r}"
    assert achado[0] == RECUSA_CANAL_NAO_CONFIRMADO


def test_a_central_com_o_nome_do_dono_continua_passando() -> None:
    """O RED do teste acima: a cerca da central nao pode proibir a palavra, so' o dono errado."""
    assert motivo_de_canal_nao_confirmado("A central de atendimento do plano atende essa duvida.") is None


@pytest.mark.parametrize(
    "texto",
    [
        "ligue 4004 4000",
        "ligue 4004.4000",
        "ligue 32114000",
        "ligue 11 3211-4000",
        "ligue +55 11 3211 4000",
        "baixe um dos nossos apps",
        "acesse nosso website",
        "acesse nossos websites",
    ],
)
def test_o_telefone_em_qualquer_forma_e_o_plural_do_termo_generico(texto: str) -> None:
    """ "Qualquer forma" era a INTENCAO do comentario; os padroes cobriam duas.

    Telefone com espaco, com ponto ou sem separador nenhum saia inteiro, contra um `response_prompt`
    categorico. E a assimetria do plural estava visivel na propria lista — `aplicativos?` tinha o
    `s?` e `app` nao, entao "apps" passava; "website" passava sendo o MESMO canal que "site"
    proibe.
    """
    achado = motivo_de_canal_nao_confirmado(texto)

    assert achado is not None, f"passou: {texto!r}"
    assert achado[0] == RECUSA_CANAL_NAO_CONFIRMADO


@pytest.mark.parametrize(
    "texto",
    [
        "O prazo de resposta e' de 24 horas.",
        "Voce esta de 33 semanas.",
        "Sao 3 dias uteis.",
        "A carencia e' de 180 dias.",
        "Pode continuar por aqui mesmo, no WhatsApp.",
        "Se quiser, visite uma unidade proxima.",
    ],
)
def test_o_numero_que_nao_e_telefone_continua_passando(texto: str) -> None:
    """O preco do padrao de telefone mais largo: cada falso positivo aqui e' uma pessoa do
    atendimento recebendo um caso que nao existe. O `\\b` nas duas pontas e' o que separa
    "3211 4000" de "24 horas"."""
    assert motivo_de_canal_nao_confirmado(texto) is None, f"texto legitimo reprovado: {texto!r}"


@pytest.mark.parametrize(
    "texto",
    [
        # O nome confirmado e o termo generico em oracoes diferentes: a REFERENCIA DE VOLTA.
        "O valor da mensalidade fica no portal do plano — voce tambem consegue pelo aplicativo.",
        "Baixe o aplicativo Austa Clinicas. No aplicativo voce ve a carteirinha digital.",
        "A central de atendimento do plano tambem atende; no aplicativo voce resolve sozinho.",
    ],
)
def test_o_termo_generico_passa_com_qualquer_nome_confirmado_no_texto(texto: str) -> None:
    """A segunda passagem REPROVAVA texto correto, e o preco era fila de gente.

    Ela exigia o nome PAREADO: `\\baplicativos?\\b` cobrava "austa clinicas" na mesma frase, entao
    um texto que nomeava o PORTAL DO PLANO e depois dizia "no aplicativo" era recusado — e em
    `inform` uma recusa nao vira outro texto, vira `_start_escalation(motivo="falha_tecnica")`.
    O que a cerca precisa garantir e' que a pessoa saiba para onde ir; um texto que nomeia um canal
    confirmado nao deixa ninguem perdido.

    O RECORTE DE 21/09/2026 (TERCEIRA RODADA): a referencia de volta vale SO' para o termo NU
    (seguido de pontuacao ou de palavra funcional). Aplicada tambem ao termo QUALIFICADO, ela
    legitimava um nome INVENTADO por vizinhanca — "Baixe o aplicativo Austa Saude ... ou fale com a
    central de atendimento do plano" passava, que e' o F7 reaberto. Um dos quatro textos desta
    lista mudou de veredito por causa do recorte e esta' declarado no teste abaixo.
    """
    assert motivo_de_canal_nao_confirmado(texto) is None, f"texto correto reprovado: {texto!r}"


def test_todo_nome_exigido_pela_cerca_existe_na_lista_do_dono() -> None:
    """A COPIA QUE NINGUEM CONFERE, fechada por cerca (21/09/2026, terceira rodada).

    `termo.exige` voltou a ser o nome da FAMILIA, e esse nome esta' escrito A MAO no padrao — uma
    segunda copia do que `CANAIS_CONFIRMADOS` declara. Se o dono renomear um canal, a copia fica
    parada e a cerca passa a cobrar um nome que nao existe mais: TODO texto com "aplicativo" viraria
    `falha_tecnica`, ou seja a cerca se transformaria num gerador de fila humana em silencio.

    A licao das Frentes 6 e 3 (citada no docstring do modulo) e' que "uma copia que ninguem confere
    deriva em dois dias". Derivar o valor de `CANAIS_CONFIRMADOS` dentro do padrao nao e' possivel
    sem inverter a ordem de declaracao do modulo (`_normalizar` mora depois); esta cerca e' o preco
    disso, e ela falha no minuto do rename.
    """
    exigidos = {
        exigencia
        for termo in CANAL_TERMO_QUE_EXIGE_O_NOME
        for exigencia in (termo.exige, termo.exige_nu)
        if exigencia is not None and exigencia != QUALQUER_CANAL_CONFIRMADO
    }

    assert exigidos, "nenhum termo exige nome — a cerca ficaria vacua"

    fora_da_lista = sorted(e for e in exigidos if e not in _NOMES_CONFIRMADOS)
    assert not fora_da_lista, (
        "a cerca de canal exige um nome que NAO esta em `CANAIS_CONFIRMADOS` (a lista do dono): "
        f"{fora_da_lista} — nomes declarados: {sorted(_NOMES_CONFIRMADOS)}"
    )


def test_o_termo_generico_seguido_de_verbo_conta_como_qualificado() -> None:
    """TROCA DE VEREDITO DECLARADA (21/09/2026, terceira rodada) — este texto passava e agora e'
    recusado, e a declaracao e' o ponto.

    `_termo_generico_esta_nu` decide pela palavra seguinte, contra uma lista FECHADA de palavras
    funcionais; "mostra" nao esta' nela (nem podia estar sem um dicionario que separe verbo lexical
    de nome de marca), entao "o portal mostra" conta como QUALIFICADO e a cerca cobra "portal do
    plano".

    POR QUE ESTE LADO: a alternativa e' admitir "o portal Unimed" pela mesma regra. Reprovar um
    texto correto custa um escalonamento; aprovar um canal inventado custa uma pessoa indo para um
    lugar que nao existe — e aqui o nome da familia esta' a uma palavra de distancia ("o portal do
    plano mostra a mesma lista"), que e' exatamente o que o `response_prompt` manda escrever (e
    manda com todas as letras desde o `response-v9`: "REPITA O NOME DO CANAL EM CADA MENCAO").

    21/09/2026, QUARTA RODADA — A DECLARACAO FICOU MAIS LARGA, e o terceiro caso e' a novidade.
    Ate' aqui a recusa dependia de o nome da familia nao aparecer em nenhum outro ponto do texto:
    o ramo qualificado procurava `termo.exige` com `in`, no texto INTEIRO. Isso era o CRITICO da
    quarta rodada (nome INVENTADO passando por vizinhanca do nome certo), e o conserto foi exigir
    que o nome COMECE na ocorrencia julgada. Consequencia direta, declarada aqui e no corpus: o
    termo qualificado por verbo e' recusado MESMO com o nome da familia na frase anterior.
    """
    com_verbo = "A rede credenciada esta no aplicativo Austa Clinicas; o portal mostra a mesma lista."
    com_o_nome = (
        "A rede credenciada esta no aplicativo Austa Clinicas; o portal do plano mostra a mesma lista."
    )
    com_o_nome_so_na_frase_anterior = (
        "A rede credenciada esta no portal do plano; o portal mostra a mesma lista."
    )

    achado = motivo_de_canal_nao_confirmado(com_verbo)
    assert achado is not None and achado[0] == RECUSA_CANAL_NAO_CONFIRMADO
    assert motivo_de_canal_nao_confirmado(com_o_nome) is None, (
        "o conserto do texto e' uma palavra, e ele tem de passar"
    )

    vizinhanca = motivo_de_canal_nao_confirmado(com_o_nome_so_na_frase_anterior)
    assert vizinhanca is not None and vizinhanca[0] == RECUSA_CANAL_NAO_CONFIRMADO, (
        "o nome da familia em outro ponto do texto nao legitima a ocorrencia qualificada — foi por "
        "essa porta que cinco nomes INVENTADOS passaram na terceira rodada"
    )


def test_o_termo_generico_sozinho_continua_sendo_recusado() -> None:
    """O RED do teste acima: a referencia de volta exige um nome NO TEXTO, nao a boa vontade."""
    for texto in ("Isso voce resolve no aplicativo.", "Da' para ver no portal.", "Veja nos aplicativos."):
        achado = motivo_de_canal_nao_confirmado(texto)
        assert achado is not None, f"passou sem nome nenhum: {texto!r}"


@pytest.mark.parametrize(
    ("rotulo", "texto"),
    [
        ("zero width space", "apli​cativo do plano"),
        ("zero width non-joiner", "apli‌cativo do plano"),
        ("word joiner", "por⁠tal da operadora"),
        ("soft hyphen", "app­ do plano"),
        ("RLM", "aplicativo‏ do plano"),
    ],
)
def test_um_caractere_invisivel_nao_desliga_mais_a_cerca(rotulo: str, texto: str) -> None:
    """`_normalizar` passou a descartar categoria `Cf` (21/09/2026, segunda rodada).

    `NFKD` + descarte de combinantes resolvia caixa e acento e nao tocava em caractere de FORMATO:
    um unico `\\u200b` no meio de "aplicativo" derrubava as DUAS passagens (a substring nao casava e
    o `\\b...\\b` tambem nao). O texto cercado e' saida de MODELO sobre a mensagem do beneficiario,
    que o proprio grafo trata como conteudo de terceiro — a cerca e' a ultima linha, e nao pode ser
    a mais fragil.
    """
    achado = motivo_de_canal_nao_confirmado(texto)

    assert achado is not None, f"{rotulo} atravessou a normalizacao e desligou a cerca"


def test_o_homoglifo_continua_passando_e_esta_declarado() -> None:
    """LIMITE CONHECIDO, medido em vez de suposto: um "a" cirilico em "aplicativo" atravessa.

    `NFKD` nao converte homoglifo, e nenhuma normalizacao padrao o faz — fechar isso exige tabela
    de confundiveis, que e' outra decisao e outro custo de falso positivo. Este teste existe para
    o limite estar EXECUTAVEL: se alguem fechar o homoglifo, ele fica vermelho e a declaracao em
    `_normalizar` e' atualizada junto.
    """
    assert motivo_de_canal_nao_confirmado("аplicativo do plano") is None
