"""ADVERSARIAL (21/09/2026, QUARTA RODADA) — os achados do code-reviewer sobre o delta da rodada 3,
cada um com o texto MEDIDO por ele.

Por que um arquivo proprio, pelo mesmo argumento da rodada 3: cada `test_helena_adv_*.py` carrega
o raciocinio da SUA rodada. E os achados desta tem uma assinatura comum, que vale escrever antes
dos testes: TODOS OS TRES BLOQUEANTES SAO ERROS DE DISTANCIA. A cerca olhava a coisa certa no lugar
errado —

  1. [CRITICO] o nome de canal exigido era procurado no TEXTO INTEIRO, nao na OCORRENCIA julgada:
     um nome inventado passava por vizinhanca do nome certo ("Baixe o aplicativo Austa Saude; o
     aplicativo Austa Clinicas tem a mesma informacao");
  2. [IMPORTANTE] a pista de telefone incluia `numero`, que e' pista de NUMERO — a categoria
     inteira que a pista da rodada 3 existia para nao recusar ("O numero do protocolo e 2026
     0921");
  3. [IMPORTANTE] `memoria_clinica.confirmada` acendia quando a frase era GERADA, nao quando era
     ENVIADA: com a troca integral do rascunho num `ja_ativo`, a pessoa nunca lia a pergunta e ela
     nunca mais era feita;
  4. [DESEJAVEL] a janela sujeito->acao da cerca de mencao atravessava a negacao que nega a propria
     acao ("A equipe nao vai assumir o seu caso" contava como mencao);
  5. [DESEJAVEL] "a pessoa de 1 anos" / "a gestacao de 1 semanas" — o irmao do "bebe de 1 meses"
     consertado na rodada 3, nos dois ramos que o comentario daquela rodada declarava impossiveis;
  6. [DESEJAVEL] o `response_prompt` nao mandava REPETIR o nome do canal em cada mencao, que e' a
     forma que a cerca aprova e esta' a uma palavra de distancia.

O item 7 do reviewer (completude do corpus por prefixo de nome) mora em
`test_helena_corpus_cercas_de_saida.py`, junto da cerca que ele estende.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

import pytest

from maezo.agents.helena.graph import (
    _MEMORIA_CONFIRMADA,
    RESPOSTA_HANDOFF_JA_ABERTO,
    START_DESFECHO_JA_ATIVO,
    HelenaGraph,
    HelenaState,
    _confirmacao_chegou,
    _frase_de_confirmacao,
)
from maezo.agents.helena.prompts import (
    RECUSA_CANAL_NAO_CONFIRMADO,
    RESPONSE_PROMPT_VERSION,
    menciona_encaminhamento,
    motivo_de_canal_nao_confirmado,
    motivo_de_recusa,
    response_prompt,
)
from maezo.runtime.inference import InferenceProvider
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport
from maezo.tools.workers.dmn_transport import FakeDmnTransport
from tests.support.audit_fakes import FakeStartAuditSink

_SEM_BANDEIRA = [{"red_flag": False, "prioridade": "-", "conduta": "CONTINUE", "motivo": "Sem criterio"}]


class _InferenciaFixa:
    def __init__(self, texto: str) -> None:
        self.texto = texto
        self.model_id = "fake"

    async def generate(self, prompt: str, **_: Any) -> str:
        return self.texto


class _WhatsApp:
    def __init__(self) -> None:
        self.enviados: list[tuple[str, str]] = []

    async def send(self, to_hash: str, text: str) -> dict[str, Any]:
        self.enviados.append((to_hash, text))
        return {"ok": True}


def _graph(
    texto: str = "irrelevante",
    *,
    whatsapp: _WhatsApp | None = None,
    dmn: FakeDmnTransport | None = None,
) -> HelenaGraph:
    return HelenaGraph(
        inference=cast(InferenceProvider, _InferenciaFixa(texto)),
        dmn=dmn or FakeDmnTransport(),
        cibseven=FakeCibSevenTransport(),
        audit_sink=FakeStartAuditSink(),
        whatsapp=cast(Any, whatsapp or _WhatsApp()),
        memoria_clinica_enabled=True,
    )


def _estado(**extra: Any) -> HelenaState:
    s: HelenaState = {
        "tenant_id": "amh",
        "conversation_id": "wa:amh:adv_r4",
        "canal": "whatsapp",
        "beneficiario_pseudo_id": "PSEUDO-R4",
        "message_body": "ele esta com febre",
    }
    s.update(extra)  # type: ignore[typeddict-item]
    return s


# =================================================================================================
# 1. [CRITICO] o nome exigido tem de COMECAR NA OCORRENCIA, nao aparecer em algum lugar do texto
# =================================================================================================

#: Os CINCO textos MEDIDOS pelo reviewer, todos PASSANDO antes do conserto. Cada um nomeia um canal
#: que nao existe E cita, em outro ponto, o nome confirmado da MESMA familia — que era o alibi.
NOMES_INVENTADOS_AO_LADO_DO_NOME_CERTO: tuple[str, ...] = (
    "Baixe o aplicativo Austa Saude; o aplicativo Austa Clinicas tem a mesma informacao.",
    "Veja no portal Unimed e tambem no portal do plano.",
    "A carteirinha fica no aplicativo Austa Clinicas e no aplicativo Meu Convenio.",
    "Voce pode usar o app Austa Saude ou o aplicativo Austa Clinicas.",
    "Ligue na central Austa 24h ou na central de atendimento do plano.",
)

#: A METADE QUE NAO PODE REGREDIR. Todos passam hoje e tem de continuar passando: o termo NU
#: apontando para um nome dado em outro lugar (a referencia de volta, que vale SO' ali) e o termo
#: qualificado pelo proprio nome da familia.
TEXTOS_CORRETOS_QUE_CONTINUAM_PASSANDO: tuple[str, ...] = (
    "O valor da mensalidade fica no portal do plano — voce tambem consegue pelo aplicativo.",
    "Baixe o aplicativo Austa Clinicas. No aplicativo voce ve a carteirinha digital.",
    "A central de atendimento do plano tambem atende; no aplicativo voce resolve sozinho.",
    "A rede credenciada esta no aplicativo Austa Clinicas; o portal do plano mostra a mesma lista.",
    "Visite a rede credenciada no aplicativo Austa Clinicas.",
    "A central de atendimento do plano tambem atende essa duvida.",
)


@pytest.mark.parametrize("texto", NOMES_INVENTADOS_AO_LADO_DO_NOME_CERTO)
def test_o_nome_inventado_nao_se_legitima_pela_vizinhanca_do_nome_certo(texto: str) -> None:
    """REPROVA antes do conserto — 5/5 passando.

    A rodada 3 recortou a REFERENCIA DE VOLTA (`QUALQUER_CANAL_CONFIRMADO`) para valer so' no termo
    NU, justamente para o termo qualificado nao se legitimar por vizinhanca. Mas o ramo qualificado
    continuou perguntando `termo.exige in plano` — isto e', procurando o nome da familia no TEXTO
    INTEIRO. O recorte fechou a referencia GENERICA e deixou aberta a especifica: bastava o texto
    citar, em qualquer outro ponto, o nome da propria familia.

    O dano e' o F7 na sua pior forma. Nao e' um texto vago: e' um nome de aplicativo que nao existe
    num texto que parece MAIS confiavel por citar ao lado um canal que existe. A pessoa vai a loja
    e procura "Austa Saude".
    """
    achado = motivo_de_canal_nao_confirmado(texto)

    assert achado is not None, f"nome inventado passou por vizinhanca: {texto!r}"
    assert achado[0] == RECUSA_CANAL_NAO_CONFIRMADO


@pytest.mark.parametrize("texto", TEXTOS_CORRETOS_QUE_CONTINUAM_PASSANDO)
def test_o_texto_que_nomeia_certo_continua_passando(texto: str) -> None:
    """O RED do teste acima, e a metade que custa caro quando quebra.

    Em `inform` uma recusa nao produz outro texto: produz `_start_escalation(motivo=
    "falha_tecnica")`, ou seja fila de gente. Um conserto de cerca que reprove orientacao correta
    troca um defeito por outro mais caro — e' por isso que estes seis rodam ao lado dos cinco.
    """
    assert motivo_de_canal_nao_confirmado(texto) is None, f"texto correto recusado: {texto!r}"


def test_o_termo_qualificado_por_verbo_e_recusado_mesmo_com_o_nome_em_outro_lugar() -> None:
    """A TROCA DE VEREDITO DECLARADA desta rodada, e a declaracao e' o ponto.

    `_termo_generico_esta_nu` decide pela palavra seguinte contra uma lista FECHADA de palavras
    funcionais; "mostra" nao esta' nela, entao "o portal mostra" e' QUALIFICADO. Ate' a rodada 3 o
    texto abaixo passava, porque "portal do plano" aparecia na frase anterior; agora o nome tem de
    comecar NA ocorrencia, e ele nao comeca.

    E' EXATAMENTE A CLASSE JA DECLARADA RECUSADA em `test_helena_canais_confirmados.py::
    test_o_termo_generico_seguido_de_verbo_conta_como_qualificado` — o que muda e' que a
    declaracao deixa de depender de o nome nao aparecer em outro lugar do texto. O conserto
    continua a uma palavra, e e' a forma que o `response-v9` manda escrever.
    """
    recusado = "A rede credenciada esta no portal do plano; o portal mostra a mesma lista."
    com_o_nome_repetido = (
        "A rede credenciada esta no portal do plano; o portal do plano mostra a mesma lista."
    )

    achado = motivo_de_canal_nao_confirmado(recusado)
    assert achado is not None and achado[0] == RECUSA_CANAL_NAO_CONFIRMADO
    assert motivo_de_canal_nao_confirmado(com_o_nome_repetido) is None, (
        "repetir o nome do canal e' o conserto que o prompt manda fazer, e ele tem de passar"
    )


# =================================================================================================
# 2. [IMPORTANTE] `numero` nao e' pista de TELEFONE — e' pista de NUMERO
# =================================================================================================

#: Os TRES textos MEDIDOS pelo reviewer, todos RECUSADOS antes do conserto. Nenhum e' telefone, e
#: os tres sao duvida administrativa legitima de um plano de saude.
NUMEROS_ADMINISTRATIVOS_COM_A_PALAVRA_NUMERO: tuple[str, ...] = (
    "O numero do protocolo e 2026 0921.",
    "O numero do pedido e 31602096, e a autorizacao sai em 5 dias uteis.",
    "O numero do procedimento e 31602096.",
)

#: A NAO-VACUIDADE do conserto: telefone com pista de VERDADE continua recusado. Os tres primeiros
#: sao os que o reviewer pediu explicitamente; os oito de
#: `test_helena_adv_rodada3.py::TELEFONES_QUE_CONTINUAM_RECUSADOS` rodam la', e nenhum deles
#: dependia de `numero`.
TELEFONES_QUE_CONTINUAM_RECUSADOS_NA_R4: tuple[str, ...] = (
    "O telefone e 3003 1234.",
    "Atendimento 0800 123 4567.",
    "Chame no whatsapp 99999 8888.",
    "Se preferir, ligue para 3222-1234.",
    "Voce pode falar com a central no (17) 3222-1234.",
)


@pytest.mark.parametrize("texto", NUMEROS_ADMINISTRATIVOS_COM_A_PALAVRA_NUMERO)
def test_o_numero_do_protocolo_nao_e_telefone(texto: str) -> None:
    """REPROVA antes do conserto — 3/3 recusados.

    A pista da rodada 3 existia para separar telefone de numero, e `numero` na alternancia
    reintroduzia pela porta da frente a classe que ela fechava: em portugues administrativo "o
    numero do ..." introduz protocolo, pedido, guia, procedimento e carteirinha muito mais vezes do
    que telefone. O `\\b\\d{4,5}[\\s.-]?\\d{4}\\b` casa faixa de ano, codigo TUSS de 8 digitos e
    protocolo, entao a pista era a UNICA coisa entre esses numeros e uma recusa.

    E em `inform` recusar e' `falha_tecnica`: a pessoa perguntou o numero do protocolo e entrou
    numa fila humana.
    """
    assert motivo_de_canal_nao_confirmado(texto) is None, f"numero administrativo recusado: {texto!r}"


@pytest.mark.parametrize("texto", TELEFONES_QUE_CONTINUAM_RECUSADOS_NA_R4)
def test_o_telefone_com_pista_de_verdade_continua_recusado(texto: str) -> None:
    """O RED do teste acima: as palavras que sobraram na pista sao ACAO ou APARELHO de telefonia.

    A decisao do dono e' que a central e' citada pelo NOME, sem numero — "a Helena nao sabe qual
    numero atende o contrato de quem esta do outro lado, e um numero errado e' pior que nenhum".
    """
    achado = motivo_de_canal_nao_confirmado(texto)

    assert achado is not None, f"telefone passou: {texto!r}"
    assert achado[0] == RECUSA_CANAL_NAO_CONFIRMADO


def test_o_residual_do_telefone_sem_pista_esta_declarado() -> None:
    """O RESIDUAL, fixado como teste para que ele seja uma DECISAO e nao um esquecimento.

    Numero de telefone sem nenhuma palavra-pista na vizinhanca PASSA. Fechar isso exige voltar ao
    padrao sem pista, que recusa "2024-2025" e "31602096"; a escolha e' pelo lado que nao
    transforma duvida administrativa legitima em escalonamento. A primeira barreira continua sendo
    a instrucao do `response_prompt`, e ela e' categorica.

    O DIA EM QUE ESTE TESTE FICAR VERMELHO nao e' um defeito: e' alguem tendo fechado o residual,
    e o teste a ser apagado e' este.
    """
    assert motivo_de_canal_nao_confirmado("Nosso atendimento e pelo 3003 1234.") is None
    assert "numero de telefone" in response_prompt(), (
        "o residual conta com a instrucao do prompt como primeira barreira — se ela sair, o "
        "residual deixa de ser aceitavel"
    )


# =================================================================================================
# 3. [IMPORTANTE] `memoria_clinica.confirmada` acende no ENVIO, nao na GERACAO
# =================================================================================================

#: O rascunho que a cerca TEXTO x FATO descarta INTEIRO num `ja_ativo`: ele afirma assuncao, e num
#: turno em que nada foi aberto essa afirmacao e' falsa.
RASCUNHO_QUE_ANUNCIA_HANDOFF = (
    "entendi que voce esta falando sobre seu bebe de 8 meses, certo? "
    "Um atendente vai assumir o seu caso agora."
)


def _memoria_do_bebe(*, confirmada: bool = False) -> dict[str, Any]:
    # `now`, e NAO uma data fixa: `receive` valida a memoria contra o RELOGIO (janela de
    # `MEMORIA_CLINICA_JANELA_HORAS`), e um carimbo fixo faz este teste apagar em silencio o que
    # ele existe para medir — a memoria e' descartada e nao ha' nada a confirmar.
    return {
        "gravado_em": datetime.now(UTC).isoformat(),
        "population": "pediatric",
        "idade_meses": 8,
        "confirmada": confirmada,
    }


def _extracao_sem_a_idade() -> str:
    return (
        '{"intent": "symptom", "population": "none", "psychosocial_risk": false, '
        '"sintoma_codigo": "febre", "intensidade": "leve", "idade_meses": null}'
    )


async def _classificar(graph: HelenaGraph, memoria: dict[str, Any] | None) -> dict[str, Any]:
    estado: dict[str, Any] = dict(_estado(memoria_clinica=memoria))
    estado.update(await graph.receive(cast(HelenaState, estado)))
    estado.update(await graph.classify(cast(HelenaState, estado)))
    return estado


def _dmn_pediatrico() -> FakeDmnTransport:
    dmn = FakeDmnTransport()
    dmn.register("triage_redflag_pediatric", _SEM_BANDEIRA)
    return dmn


async def test_a_pergunta_descartada_pela_cerca_volta_no_turno_seguinte() -> None:
    """REPROVA antes do conserto — ponta a ponta, tres turnos.

    `classify` gravava `confirmada=ja_confirmada or confirmar_agora`, ou seja o flag acendia quando
    a frase era GERADA. Depois disso vem `_texto_bate_com_o_fato`, e num `ja_ativo` cujo rascunho
    afirma assuncao a troca e' INTEGRAL: o que sai e' `RESPOSTA_HANDOFF_JA_ABERTO` e nada mais.

    Somados: a pessoa nunca le' a pergunta, `confirmada` fica `True` para sempre, e
    `confirmar_agora` (que exige `not ja_confirmada`) nunca mais dispara. O dado lembrado segue
    escolhendo a tabela de red flag — que e' o que ele faz — sem que ninguem possa corrigi-lo, e
    "MOSTRAR ANTES DE USAR" existe exatamente para isso nao acontecer.
    """
    whatsapp = _WhatsApp()
    graph = _graph(_extracao_sem_a_idade(), whatsapp=whatsapp, dmn=_dmn_pediatrico())

    turno1 = await _classificar(graph, _memoria_do_bebe())
    assert turno1["memoria_a_confirmar"], "o dado lembrado entrou na decisao clinica sem ser mostrado"
    assert turno1["memoria_clinica"][_MEMORIA_CONFIRMADA] is False, (
        "a confirmacao foi dada por feita no turno em que a frase foi apenas GERADA"
    )

    saida = await graph.respond(
        _estado(
            response_text=RASCUNHO_QUE_ANUNCIA_HANDOFF,
            response_kind="escalate",
            start_desfecho=START_DESFECHO_JA_ATIVO,
            memoria_clinica=turno1["memoria_clinica"],
            memoria_a_confirmar=turno1["memoria_a_confirmar"],
        )
    )

    assert whatsapp.enviados[0][1] == RESPOSTA_HANDOFF_JA_ABERTO, (
        "premissa deste teste: num `ja_ativo` o rascunho que anuncia handoff e' trocado INTEIRO"
    )
    memoria_depois_do_envio = saida.get("memoria_clinica", turno1["memoria_clinica"])
    assert memoria_depois_do_envio[_MEMORIA_CONFIRMADA] is False, (
        "a pergunta nunca chegou a pessoa e a conversa deu a confirmacao por feita"
    )

    turno2 = await _classificar(graph, memoria_depois_do_envio)
    assert turno2["memoria_a_confirmar"], (
        "a pergunta que a cerca descartou nunca mais foi feita — o dado lembrado passou a decidir "
        "a tabela sem chance de correcao"
    )


async def test_a_pergunta_que_chega_a_pessoa_nao_volta_no_turno_seguinte() -> None:
    """O RED do teste acima, e a metade que nao pode regredir: "UMA vez por conversa".

    Um `memoria_a_confirmar` em toda mensagem viraria ruido e a pessoa pararia de ler justamente a
    frase que existe para ela corrigir. O criterio muda de "foi gerada" para "foi enviada"; o
    limite de uma vez fica.
    """
    whatsapp = _WhatsApp()
    graph = _graph(_extracao_sem_a_idade(), whatsapp=whatsapp, dmn=_dmn_pediatrico())

    turno1 = await _classificar(graph, _memoria_do_bebe())
    frase = turno1["memoria_a_confirmar"]

    saida = await graph.respond(
        _estado(
            response_text=f"{frase} Por aqui eu posso te orientar sobre os sinais de alerta.",
            response_kind="inform",
            memoria_clinica=turno1["memoria_clinica"],
            memoria_a_confirmar=frase,
        )
    )

    assert frase in whatsapp.enviados[0][1], "premissa: a frase saiu no texto enviado"
    assert saida["memoria_clinica"][_MEMORIA_CONFIRMADA] is True

    turno2 = await _classificar(graph, saida["memoria_clinica"])
    assert turno2["memoria_a_confirmar"] is None, (
        "a confirmacao e' UMA vez por conversa, contada do turno em que ela SAIU"
    )


def test_a_marca_da_confirmacao_e_normalizada_e_fail_closed() -> None:
    """`_confirmacao_chegou` nas bordas: sem frase, frase vazia, e caixa/acento/invisivel.

    A normalizacao e' a mesma das cercas de saida (`prompts._normalizar`), pelo mesmo motivo de
    `_apresentou_se`: caixa, acento e caractere de formato nao podem decidir se a Helena volta a
    confirmar o dado que escolhe a tabela de red flag.
    """
    frase = _frase_de_confirmacao({"idade_meses": 8})

    assert _confirmacao_chegou(f"Oi! {frase} Posso te orientar.", frase) is True
    caixa_e_acento = "ENTENDI QUE VOCÊ ESTÁ FALANDO SOBRE SEU BEBÊ DE 8 MESES, CERTO?"
    assert _confirmacao_chegou(caixa_e_acento, frase) is True
    assert _confirmacao_chegou(RESPOSTA_HANDOFF_JA_ABERTO, frase) is False
    assert _confirmacao_chegou(f"Oi! {frase}", None) is False
    assert _confirmacao_chegou(f"Oi! {frase}", "") is False
    assert _confirmacao_chegou(f"Oi! {frase}", 42) is False


# =================================================================================================
# 4. [DESEJAVEL] a negacao INTERPOSTA entre o sujeito e a acao
# =================================================================================================

#: Os DOIS textos MEDIDOS pelo reviewer, `menciona=True` antes do conserto. Os dois NEGAM
#: exatamente a acao de assuncao que o padrao procura.
NEGACOES_INTERPOSTAS: tuple[str, ...] = (
    "A equipe nao vai assumir o seu caso.",
    "Nao, um atendente nao vai te ligar hoje.",
)

#: O QUE NAO PODE REGREDIR: o C1 da bateria do diretor e a familia dele. O `nao` esta' FORA da
#: janela sujeito->acao, entao a cerca continua disparando.
ANUNCIOS_COM_NEGACAO_FORA_DA_JANELA: tuple[str, ...] = (
    "Sem mais detalhes eu nao consigo avaliar, entao a equipe vai te ligar ainda hoje.",
    "Ninguem precisa repetir isso: um profissional vai retornar para voce em breve.",
    "Nao tenho acesso ao seu historico, mas encaminhamos seu relato para a enfermagem.",
    "Nao consigo adiantar mais, mas um profissional vai assumir o seu caso agora",
)


@pytest.mark.parametrize("texto", NEGACOES_INTERPOSTAS)
def test_a_negacao_entre_o_sujeito_e_a_acao_nao_conta_como_mencao(texto: str) -> None:
    """REPROVA antes do conserto — 2/2 com `menciona=True`.

    A janela era `[^.;!?]{0,40}` crua, e por isso atravessava a negacao que nega a propria acao. O
    custo e' dos dois lados e nos dois esta' errado: com `start_aconteceu=False` a cerca recusa uma
    frase HONESTA (e em `inform` recusar e' fila humana); com o start ACONTECIDO ela considera
    avisado um beneficiario que leu o contrario.
    """
    assert menciona_encaminhamento(texto) is False, f"negacao interposta contou como mencao: {texto!r}"
    assert motivo_de_recusa(texto, "inform", start_aconteceu=False) is None, (
        "um texto que NEGA o handoff foi recusado num turno sem handoff — a recusa troca a frase "
        "honesta por uma constante, e em `inform` ela e' `falha_tecnica`"
    )


@pytest.mark.parametrize("texto", ANUNCIOS_COM_NEGACAO_FORA_DA_JANELA)
def test_a_negacao_fora_da_janela_nao_desliga_a_cerca(texto: str) -> None:
    """O RED do teste acima, e e' o C1 da bateria: o conserto nao pode reabrir o CRITICO da rodada 3.

    Foi um filtro de negacao por ORACAO que deixou estes passarem, e por isso o conserto de agora e'
    por CARACTERE, dentro da janela sujeito->acao. `nao` antes do sujeito ou depois da acao nao
    encosta na cerca.
    """
    assert menciona_encaminhamento(texto) is True, f"o conserto reabriu o C1: {texto!r}"


def test_a_negacao_antes_do_sujeito_e_residual_declarado() -> None:
    """O RESIDUAL, fixado como teste para ser uma DECISAO e nao um esquecimento.

    "Nenhum atendente vai te ligar" continua contando como mencao. Cobri-lo exige olhar A ESQUERDA
    do sujeito, e e' de la' que vem a classe de falso negativo que custou o C1 ("Sem mais detalhes
    eu nao consigo avaliar, entao a equipe vai te ligar") — o mesmo `nao` a esquerda aparece nas
    frases honestas e nas mentirosas.

    E a direcao do erro e' a segura: o residual FAZ a cerca disparar, e disparar troca o texto por
    uma constante verdadeira. O avesso (nao disparar) e' a promessa sem lastro.
    """
    assert menciona_encaminhamento("Nenhum atendente vai te ligar.") is True
    assert menciona_encaminhamento("Ninguem da equipe vai assumir o seu caso.") is True


# =================================================================================================
# 5. [DESEJAVEL] "a pessoa de 1 anos" e "a gestacao de 1 semanas"
# =================================================================================================


@pytest.mark.parametrize(
    ("lembrados", "esperado"),
    [
        ({"idade_anos": 1}, "a pessoa de 1 ano,"),
        ({"idade_gestacional_semanas": 1}, "a gestacao de 1 semana,"),
        ({"idade_anos": 2}, "a pessoa de 2 anos,"),
        ({"idade_gestacional_semanas": 33}, "a gestacao de 33 semanas,"),
        ({"idade_meses": 1}, "seu bebe de 1 mes,"),
    ],
)
def test_o_singular_da_frase_de_confirmacao(lembrados: dict[str, Any], esperado: str) -> None:
    """REPROVA nos dois primeiros — e o comentario da rodada 3 dizia que eles eram impossiveis.

    Aquele comentario afirmava que "o mes e' o unico numero desta funcao que chega a 1, porque
    `anos` vem de divisao inteira sobre >24". A divisao inteira explica APENAS o ramo
    `meses > 24`; `idade_anos` e `idade_gestacional_semanas` vem da EXTRACAO, e "minha filha tem 1
    ano" / "estou de 1 semana" sao mensagens comuns.

    A frase existe PARA a pessoa corrigir o dado que escolheu a tabela de red flag. Uma frase que
    soa a sistema quebrado e' uma frase que ela para de ler — e o mesmo argumento vale nos tres
    ramos, nao so' no que foi consertado antes.
    """
    assert esperado in _frase_de_confirmacao(lembrados)


# =================================================================================================
# 6. [DESEJAVEL] o prompt manda REPETIR o nome do canal em cada mencao
# =================================================================================================


def test_o_prompt_manda_repetir_o_nome_do_canal_em_cada_mencao() -> None:
    """A contraparte de INSTRUCAO de um veredito de cerca que NAO muda.

    "No portal do plano voce baixa o boleto. O aplicativo faz o mesmo." e' recusado, porque "o
    aplicativo FAZ" conta como QUALIFICADO e o nome nao comeca ali. Separar verbo lexical de nome
    de marca exigiria dicionario; sem ele a escolha e' entre recusar "o aplicativo faz" e aprovar
    "o aplicativo Meu Convenio", e o segundo e' pessoa indo para um lugar que nao existe.

    Entao a cerca fica, e o que faltava era o prompt MANDAR escrever a forma que ela aprova — que
    custa uma palavra. Em `inform` uma recusa nao vira outro texto, vira `falha_tecnica`: reduzir a
    chance de o modelo escrever a forma recusada e' o que sobra de barato.
    """
    texto = response_prompt()

    assert "REPITA O NOME DO CANAL EM CADA MENCAO" in texto
    assert "o portal do plano mostra a mesma lista" in texto, (
        "a instrucao mostra a forma CERTA e a errada lado a lado — sem o exemplo ela e' abstrata"
    )
    assert RESPONSE_PROMPT_VERSION == "response-v9", (
        "o texto do prompt mudou: a versao sobe no MESMO commit, e `agent.yaml` com ela "
        "(test_helena_dono_declarado.py compara os dois)"
    )


def test_o_texto_que_o_prompt_manda_escrever_passa_na_cerca() -> None:
    """A instrucao e a cerca nao podem divergir: e' o defeito que as duas listas de canal moram
    lado a lado para evitar.

    O par abaixo e' o exemplo LITERAL do prompt, nos dois lados: a forma que ele proibe e' recusada
    e a forma que ele manda escrever passa. Uma instrucao que mandasse escrever algo que a cerca
    recusa seria um gerador de `falha_tecnica`.
    """
    assert motivo_de_canal_nao_confirmado("O portal do plano mostra a mesma lista.") is None
    assert motivo_de_canal_nao_confirmado("O portal mostra a mesma lista.") is not None
    assert motivo_de_canal_nao_confirmado("No aplicativo Austa Clinicas voce ve a carteirinha.") is None
