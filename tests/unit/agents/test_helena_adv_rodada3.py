"""ADVERSARIAL (21/09/2026, TERCEIRA RODADA) — os achados do code-reviewer sobre o delta
`0d63fa59..c7d77d5b`, cada um com o texto MEDIDO por ele.

Por que um arquivo proprio e nao mais casos nos adversariais existentes: os tres arquivos
`test_helena_adv_*.py` foram escritos para atacar a cerca DA SUA rodada, e cada um carrega o
raciocinio daquela rodada no docstring. Os achados aqui sao de uma classe diferente — todos
saem do MESMO defeito de metodo: uma cerca que passou a ler POLARIDADE (a rodada 2) e uma que
passou a aceitar REFERENCIA DE VOLTA (idem) ficaram mais largas do que a intencao, e nos dois
casos o custo caiu do lado que a entrega dizia estar protegendo.

O que cada bloco cobra, em uma linha:

  1. `menciona_encaminhamento` — uma negacao em qualquer lugar da oracao desligava a cerca, e o
     clitico ("vai TE ligar") escapava do padrao de assuncao;
  2. `motivo_de_canal_nao_confirmado` — com `QUALQUER_CANAL_CONFIRMADO` no termo QUALIFICADO, um
     nome INVENTADO passava de graca ao lado de um nome confirmado;
  3. a cerca TEXTO x FATO no `ja_ativo` — o anuncio de handoff NOVO sobrevivia como sufixo;
  4. o padrao de telefone — separador opcional reprovava numero que nao e' telefone;
  5. `MARCAS_DE_APRESENTACAO` — duas frases literais nunca acendiam com a parafrase do modelo;
  6. o teto de idade — existia so' na memoria, e a extracao do turno entregava 1200 meses;
  7. a frase de confirmacao — "seu bebe de 1 meses";
  8. o no' `collect` — recusa por NEGATIVA CLINICA virava pergunta generica;
  9. `docs/review-queue.md` — as duas pendencias clinicas que a prosa diz estarem registradas.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, cast

import pytest

from maezo.agents.helena.graph import (
    RESPOSTA_FALHA_DE_REDACAO,
    RESPOSTA_FALHA_TECNICA_START,
    RESPOSTA_HANDOFF_JA_ABERTO,
    RESPOSTA_HANDOFF_RECUSADA,
    RESPOSTA_SEM_ENCAMINHAMENTO,
    START_DESFECHO_JA_ATIVO,
    HelenaGraph,
    HelenaState,
    _apresentou_se,
    _coerce_age,
    _frase_de_confirmacao,
    _validate_extraction,
)
from maezo.agents.helena.prompts import (
    RECUSA_CANAL_NAO_CONFIRMADO,
    RECUSA_PROMESSA_SEM_START,
    RESPOSTA_NAO_CONSIGO_IDENTIFICAR,
    RESPOSTA_SOU_ASSISTENTE_VIRTUAL,
    menciona_encaminhamento,
    motivo_de_canal_nao_confirmado,
    motivo_de_recusa,
)
from maezo.runtime.inference import InferenceProvider
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport
from maezo.tools.workers.dmn_transport import FakeDmnTransport
from tests.support.audit_fakes import FakeStartAuditSink

_REPO_ROOT = Path(__file__).parent.parent.parent.parent


class _InferenciaFixa:
    def __init__(self, texto: str) -> None:
        self.texto = texto
        self.chamadas = 0
        self.model_id = "fake"

    async def generate(self, prompt: str, **_: Any) -> str:
        self.chamadas += 1
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
    coleta_enabled: bool = False,
) -> HelenaGraph:
    return HelenaGraph(
        inference=cast(InferenceProvider, _InferenciaFixa(texto)),
        dmn=dmn or FakeDmnTransport(),
        cibseven=FakeCibSevenTransport(),
        audit_sink=FakeStartAuditSink(),
        whatsapp=cast(Any, whatsapp or _WhatsApp()),
        coleta_enabled=coleta_enabled,
    )


def _estado(**extra: Any) -> HelenaState:
    s: HelenaState = {
        "tenant_id": "amh",
        "conversation_id": "wa:amh:adv_r3",
        "canal": "whatsapp",
        "beneficiario_pseudo_id": "PSEUDO-R3",
        "message_body": "estou com dor de cabeca",
    }
    s.update(extra)  # type: ignore[typeddict-item]
    return s


def _classify_json(**overrides: Any) -> str:
    base: dict[str, Any] = {
        "intent": "symptom",
        "population": "pediatric",
        "psychosocial_risk": False,
        "sintoma_codigo": "febre",
        "intensidade": "leve",
    }
    base.update(overrides)
    return json.dumps(base)


# =================================================================================================
# 1. [CRITICO] `menciona_encaminhamento`: a negacao por ORACAO e o clitico
# =================================================================================================

#: Os textos MEDIDOS pelo reviewer, passando SEM start (turno `inform`, zero processos). Cada um
#: tem um marcador de negacao em algum lugar da oracao e ANUNCIA um humano na mesma oracao.
TEXTOS_COM_NEGACAO_QUE_ANUNCIAM_HUMANO: tuple[str, ...] = (
    "Sem mais detalhes eu nao consigo avaliar, entao a equipe vai te ligar ainda hoje.",
    "Ninguem precisa repetir isso: um profissional vai retornar para voce em breve.",
    "Nao tenho acesso ao seu historico, mas encaminhamos seu relato para a enfermagem.",
)

#: O CLITICO entre o auxiliar e o verbo. Portugues brasileiro escrito por modelo usa isto o tempo
#: todo, e `(?:vai|vao|ira|irao)\s+(?:verbo)` exige adjacencia.
TEXTOS_COM_CLITICO: tuple[str, ...] = (
    "Uma profissional vai lhe retornar em breve",
    "Um atendente vai te ligar ainda hoje.",
    "As enfermeiras vao te avaliar em seguida.",
    "A equipe vai te atender agora.",
    "Um medico vai lhe falar sobre o seu caso.",
)

#: Os CINCO textos que motivaram o filtro de negacao. Eles continuam `menciona=False` SEM ele,
#: porque os padroes sujeito+acao ja' resolvem polaridade por construcao: nenhum tem a acao de
#: assuncao (`vai assumir`, `entrara em contato`, `encaminhamos`) — tem "foi acionado", "nao
#: consegui registrar", "nao abri". E' isso que torna o filtro removivel em vez de ajustavel.
TEXTOS_QUE_O_FILTRO_PROTEGIA: tuple[str, ...] = (
    "Nao acionei nenhum atendente",
    "Por isso nenhum atendente foi acionado ainda",
    RESPOSTA_FALHA_TECNICA_START,
    RESPOSTA_FALHA_DE_REDACAO,
    RESPOSTA_SEM_ENCAMINHAMENTO,
)

#: As duas constantes cuja mencao e' OBRIGATORIA: elas SAO a frase honesta de "um humano esta com
#: o seu caso", e a cerca TEXTO x FATO as usa como substituto. Se uma delas deixar de mencionar, a
#: substituicao passa a violar o invariante que ela existe para restaurar.
CONSTANTES_QUE_MENCIONAM: tuple[str, ...] = (RESPOSTA_HANDOFF_JA_ABERTO, RESPOSTA_HANDOFF_RECUSADA)


@pytest.mark.parametrize("texto", TEXTOS_COM_NEGACAO_QUE_ANUNCIAM_HUMANO)
def test_uma_negacao_na_oracao_nao_desliga_a_cerca_de_mencao(texto: str) -> None:
    """REPROVA antes do conserto — o filtro descartava a ORACAO INTEIRA.

    `_FIM_DE_ORACAO` e' `[.;!?\\n]+`: virgula e dois-pontos NAO separam oracao, de proposito (a
    distancia sujeito->acao atravessa aposto). Somados, os dois fazem "qualquer frase com uma
    negacao em qualquer lugar" perder a cerca — e a negacao esta' no comeco de metade das frases
    honestas que um modelo escreve ("Sem mais detalhes...", "Nao tenho acesso...").

    O custo e' o proprio C1 da bateria do diretor: a pessoa le' que a equipe vai ligar e ninguem
    liga, porque `motivo_de_recusa(..., start_aconteceu=False)` consulta esta funcao.
    """
    assert menciona_encaminhamento(texto) is True, f"negacao na oracao desligou a cerca: {texto!r}"

    achado = motivo_de_recusa(texto, "inform", start_aconteceu=False)
    assert achado is not None, "o texto anuncia um humano num turno sem start e saiu inteiro"
    assert achado[0] == RECUSA_PROMESSA_SEM_START


@pytest.mark.parametrize("marcador", ["Nao", "Nenhum", "Nenhuma", "Ninguem", "Sem"])
def test_nenhum_marcador_de_negacao_derruba_a_oracao_inteira(marcador: str) -> None:
    """A CLASSE, e nao os tres textos: o filtro era um `continue` por PALAVRA presente na oracao.

    Um `continue` assim e' um interruptor que o modelo aciona sem querer: basta uma palavra de
    negacao em qualquer ponto da mesma oracao (e oracao aqui vai de ponto a ponto) para a frase
    seguinte sair sem cerca.
    """
    texto = f"{marcador} posso adiantar mais, mas um profissional vai assumir o seu caso agora"

    assert menciona_encaminhamento(texto) is True, f"o marcador {marcador!r} desligou a cerca"


@pytest.mark.parametrize("texto", TEXTOS_COM_CLITICO)
def test_o_clitico_entre_o_auxiliar_e_o_verbo_nao_escapa(texto: str) -> None:
    """REPROVA antes do conserto — `vai\\s+retornar` nao ve' "vai LHE retornar".

    O pronome oblicuo e' opcional em portugues e o modelo o usa sem padrao: "vai te ligar", "vai
    lhe retornar", "vao te avaliar". Sem ele no padrao, a forma MAIS natural de anunciar um
    handoff e' justamente a que escapa.
    """
    assert menciona_encaminhamento(texto) is True, f"clitico escapou da cerca: {texto!r}"


@pytest.mark.parametrize("texto", TEXTOS_QUE_O_FILTRO_PROTEGIA)
def test_os_textos_que_motivaram_o_filtro_seguem_sem_mencao_sem_ele(texto: str) -> None:
    """A prova de que o filtro nao era load-bearing — a condicao para remove-lo em vez de
    estreita-lo.

    Se um destes passasse a mencionar, a cerca TEXTO x FATO entraria em contradicao consigo
    mesma: `RESPOSTA_FALHA_TECNICA_START` e `RESPOSTA_SEM_ENCAMINHAMENTO` sao os textos que ela
    envia PARA dizer que ninguem foi acionado.
    """
    assert menciona_encaminhamento(texto) is False, f"texto honesto lido como anuncio: {texto!r}"


@pytest.mark.parametrize("texto", CONSTANTES_QUE_MENCIONAM)
def test_as_constantes_de_mencao_obrigatoria_continuam_mencionando(texto: str) -> None:
    """O outro lado da remocao: as duas constantes que a cerca ENVIA como frase honesta de
    handoff tem de continuar sendo reconhecidas como mencao."""
    assert menciona_encaminhamento(texto) is True


# =================================================================================================
# 2. [CRITICO] canal: o nome INVENTADO ao lado de um nome confirmado
# =================================================================================================

#: Os textos MEDIDOS pelo reviewer no par antigo->novo (RECUSADO -> PASSOU). Em cada um, um nome
#: que NAO existe ("aplicativo Austa Saude", "portal Unimed", "aplicativo Meu Convenio") viaja de
#: graca porque alguma outra parte do texto nomeia um canal confirmado.
TEXTOS_COM_NOME_INVENTADO: tuple[str, ...] = (
    "Baixe o aplicativo Austa Saude para ver o boleto, ou fale com a central de atendimento do plano.",
    "Voce encontra no portal Unimed, e tambem no aplicativo Austa Clinicas.",
    "Consulte no aplicativo Meu Convenio; o portal do plano tem a mesma informacao.",
)

#: O FALSO POSITIVO que motivou a referencia de volta, e que precisa continuar passando: o termo
#: generico aparece NU (seguido de pontuacao) num texto que ja' disse o nome de um canal.
FALSO_POSITIVO_DO_TERMO_NU: str = (
    "O valor da mensalidade fica no portal do plano — voce tambem consegue pelo aplicativo."
)


@pytest.mark.parametrize("texto", TEXTOS_COM_NOME_INVENTADO)
def test_o_nome_inventado_nao_passa_por_vizinhanca(texto: str) -> None:
    """REPROVA antes do conserto — `QUALQUER_CANAL_CONFIRMADO` no termo QUALIFICADO.

    A referencia de volta foi criada para o termo NU ("...e tambem pelo aplicativo."), e foi
    aplicada ao termo com QUALIFICADOR. O efeito e' que "aplicativo Austa Saude" — um nome que
    ninguem confirmou — passa a ser legitimo desde que o texto cite qualquer canal confirmado em
    qualquer outro lugar, que e' precisamente o F7 reaberto: a pessoa baixa um aplicativo que nao
    existe.
    """
    achado = motivo_de_canal_nao_confirmado(texto)

    assert achado is not None, f"nome de canal INVENTADO passou: {texto!r}"
    assert achado[0] == RECUSA_CANAL_NAO_CONFIRMADO


@pytest.mark.parametrize(
    "texto",
    [
        "baixe o aplicativo austa saude para ver o boleto, ou fale com a central de atendimento do plano.",
        "voce encontra no portal unimed, e tambem no aplicativo austa clinicas.",
    ],
)
def test_o_qualificador_e_lexical_e_nao_depende_de_maiuscula(texto: str) -> None:
    """`_normalizar` minuscula o texto ANTES de qualquer padrao rodar, entao "Austa Saude" e
    "austa saude" sao a mesma coisa para a cerca. Um teste de qualificador que dependesse de
    caixa seria vacuo aqui — e o modelo escreve nome de marca em minuscula com frequencia."""
    assert motivo_de_canal_nao_confirmado(texto) is not None, f"passou em minuscula: {texto!r}"


def test_o_termo_nu_com_um_nome_confirmado_no_texto_continua_passando() -> None:
    """O RED do teste acima, na direcao do FALSO POSITIVO: em `inform` uma recusa nao vira outro
    texto, vira `_start_escalation(motivo="falha_tecnica")` — fila de gente. O texto que motivou
    a referencia de volta tem de continuar passando."""
    assert motivo_de_canal_nao_confirmado(FALSO_POSITIVO_DO_TERMO_NU) is None


@pytest.mark.parametrize(
    "texto",
    [
        "Isso voce resolve no aplicativo.",
        "Da' para ver no portal.",
        "Veja nos aplicativos.",
        "Baixe o nosso app para consultar.",
    ],
)
def test_o_termo_nu_sem_nome_nenhum_continua_recusado(texto: str) -> None:
    """A referencia de volta exige um nome NO TEXTO: "no aplicativo" sozinho nao e' menos vago
    que "aplicativo do plano"."""
    assert motivo_de_canal_nao_confirmado(texto) is not None, f"passou sem nome nenhum: {texto!r}"


def test_toda_ocorrencia_do_termo_e_julgada_e_nao_so_a_primeira() -> None:
    """O buraco MECANICO que o recorte NU/QUALIFICADO abriria com `re.search`.

    Com o veredito dependendo de o termo estar nu ou qualificado, julgar so' a PRIMEIRA ocorrencia
    deixa a segunda de graca: aqui a primeira ("no aplicativo.") e' nua e passa pela referencia de
    volta, e a segunda nomeia um aplicativo que nao existe. `re.finditer` e' o que fecha isso, e
    este teste e' a razao de ele estar la'.
    """
    texto = "Veja no portal do plano ou no aplicativo. Baixe o aplicativo Unimed para o resto."

    achado = motivo_de_canal_nao_confirmado(texto)

    assert achado is not None, "a segunda ocorrencia do termo generico nao foi julgada"
    assert achado[0] == RECUSA_CANAL_NAO_CONFIRMADO


#: Os VENENOS, fora do `parametrize` para o id do teste nao carregar 400 KB de texto. O padrao de
#: telefone virou uma ALTERNANCIA com duas janelas de 40 (`pista...numero` e `numero...pista`), e
#: os dois lados entram; o terceiro caso ataca a varredura por `re.finditer`, que substituiu o
#: `re.search` de cada termo.
_VENENOS: dict[str, str] = {
    "pista antes": ("ligue " + "1 " * 40) * 5000,
    "pista depois": ("1234 5678 " * 5000) + "telefone",
    "termo nu repetido": "aplicativo e " * 20000,
}


@pytest.mark.parametrize("rotulo", sorted(_VENENOS))
def test_a_cerca_de_canal_continua_sem_backtracking_catastrofico(rotulo: str) -> None:
    """A cerca roda em TODO texto que vai ao beneficiario, dentro do turno — e agora roda
    `re.finditer` por termo, nao `re.search`. Um padrao com backtracking exponencial (ou uma
    varredura quadratica) transformaria uma resposta longa numa indisponibilidade da agente.

    O limite e' generoso de proposito: o que se procura e' a diferenca entre milissegundos e
    minutos, nao uma medicao de desempenho.
    """
    veneno = _VENENOS[rotulo]

    inicio = time.perf_counter()
    motivo_de_canal_nao_confirmado(veneno)
    decorrido = time.perf_counter() - inicio

    assert decorrido < 5.0, f"{rotulo}: a cerca levou {decorrido:.2f}s em {len(veneno)} caracteres"


# =================================================================================================
# 3. [IMPORTANTE] `ja_ativo`: o anuncio de handoff NOVO sobrevivia como sufixo
# =================================================================================================

#: O rascunho MEDIDO pelo reviewer. Com o filtro de negacao valendo, "Nao se preocupe, ..." era
#: uma oracao com `nao` -> descartada -> `menciona_encaminhamento` False -> caminho do PREFIXO, e
#: o texto enviado dizia as duas coisas ao mesmo tempo.
RASCUNHO_QUE_CONTRADIZ_O_JA_ATIVO: str = "Nao se preocupe, um atendente vai assumir o seu caso agora."


async def test_o_ja_ativo_nao_deixa_o_anuncio_novo_sobreviver_como_sufixo() -> None:
    """REPROVA antes do conserto — o texto enviado se contradizia dentro de si.

        "Recebemos sua mensagem. Seu atendimento (...) ja esta aberto (...) Por isso nao abri
         outro atendimento. (...) Nao se preocupe, um atendente vai assumir o seu caso agora."

    O prefixo diz que nada foi aberto; o sufixo anuncia que alguem vai assumir agora. Num
    `ja_ativo` o que e' falso e' exatamente o anuncio NOVO — e ele e' a parte que sobreviveu.
    """
    whatsapp = _WhatsApp()
    graph = _graph(whatsapp=whatsapp)

    saida = await graph.respond(
        _estado(
            response_text=RASCUNHO_QUE_CONTRADIZ_O_JA_ATIVO,
            response_kind="escalate",
            start_desfecho=START_DESFECHO_JA_ATIVO,
        )
    )

    enviado = whatsapp.enviados[0][1]
    assert enviado == RESPOSTA_HANDOFF_JA_ABERTO, (
        "o anuncio de um handoff NOVO sobreviveu num turno em que nada foi aberto"
    )
    assert "vai assumir" not in enviado
    assert saida["response_text"] == RESPOSTA_HANDOFF_JA_ABERTO


async def test_o_ja_ativo_continua_preservando_a_orientacao_que_nao_anuncia_nada() -> None:
    """A metade que NAO pode regredir: o prefixo existe para nao jogar fora a `memoria_a_confirmar`
    (a pergunta que o `response_prompt` obriga antes de usar um dado LEMBRADO) nem a orientacao do
    turno. Um rascunho que nao faz afirmacao nenhuma sobre humano segue atras da constante."""
    orientacao = "Entendi que voce esta falando sobre seu bebe de 8 meses, certo?"
    whatsapp = _WhatsApp()

    await _graph(whatsapp=whatsapp).respond(
        _estado(
            response_text=orientacao,
            response_kind="escalate",
            start_desfecho=START_DESFECHO_JA_ATIVO,
        )
    )

    enviado = whatsapp.enviados[0][1]
    assert enviado.startswith(RESPOSTA_HANDOFF_JA_ABERTO)
    assert orientacao in enviado


# =================================================================================================
# 4. [IMPORTANTE] telefone: o separador opcional reprova o que nao e' telefone
# =================================================================================================

#: Os QUATRO textos medidos pelo reviewer, todos recusados hoje (4/4). Nenhum e' telefone, e a
#: consequencia de recusar e' pior que o defeito que o padrao fecha: uma duvida administrativa
#: legitima vira fila humana.
NUMEROS_QUE_NAO_SAO_TELEFONE: tuple[str, ...] = (
    "O Rol de Procedimentos 2024-2025 da ANS lista esse exame.",
    "O codigo do procedimento e 31602096.",
    "A carencia e de 1080 1200 dias contados da assinatura do contrato.",
    "Seu plano cobre desde 2023-2024.",
)

#: Os telefones MEDIDOS na rodada 2. Continuam recusados — e' a nao-vacuidade do padrao.
TELEFONES_QUE_CONTINUAM_RECUSADOS: tuple[str, ...] = (
    "ligue 4004 4000",
    "ligue 4004.4000",
    "ligue 32114000",
    "ligue 11 3211-4000",
    "ligue +55 11 3211 4000",
    "ligue (11) 4004-4000",
    "Voce pode falar com a central no (17) 3222-1234.",
    "Se preferir, ligue para 3222-1234.",
)


@pytest.mark.parametrize("texto", NUMEROS_QUE_NAO_SAO_TELEFONE)
def test_numero_que_nao_e_telefone_nao_vira_fila_humana(texto: str) -> None:
    """REPROVA antes do conserto — 4/4 recusados.

    `\\b\\d{4,5}[\\s.-]?\\d{4}\\b` com separador OPCIONAL casa faixa de ano ("2024-2025"), codigo
    de procedimento TUSS de 8 digitos e dois numeros de prazo lado a lado. O `\\b` nas duas pontas
    separava "3211 4000" de "24 horas", mas nao separa nada disso.
    """
    assert motivo_de_canal_nao_confirmado(texto) is None, f"numero que nao e' telefone recusado: {texto!r}"


@pytest.mark.parametrize("texto", TELEFONES_QUE_CONTINUAM_RECUSADOS)
def test_o_telefone_de_verdade_continua_recusado(texto: str) -> None:
    """Nao-vacuidade do conserto: a decisao do dono e' que a central e' citada pelo NOME, sem
    numero — "a Helena nao sabe qual numero atende o contrato de quem esta do outro lado, e um
    numero errado e' pior que nenhum"."""
    achado = motivo_de_canal_nao_confirmado(texto)

    assert achado is not None, f"telefone passou: {texto!r}"
    assert achado[0] == RECUSA_CANAL_NAO_CONFIRMADO


# =================================================================================================
# 5. [IMPORTANTE] `MARCAS_DE_APRESENTACAO`: a parafrase nunca acendia a flag
# =================================================================================================

PARAFRASES_DE_APRESENTACAO: tuple[str, ...] = (
    "Oi, aqui e a Helena. Posso te orientar sobre o plano.",
    "Eu me chamo Helena e cuido da navegacao por aqui.",
    "Helena falando: recebi a sua mensagem.",
)

TEXTOS_SEM_APRESENTACAO: tuple[str, ...] = (
    "Recebemos sua mensagem e vamos te orientar.",
    "O valor da mensalidade fica no portal do plano.",
    RESPOSTA_HANDOFF_JA_ABERTO,
    RESPOSTA_HANDOFF_RECUSADA,
    RESPOSTA_SEM_ENCAMINHAMENTO,
    RESPOSTA_FALHA_TECNICA_START,
    RESPOSTA_FALHA_DE_REDACAO,
    RESPOSTA_SOU_ASSISTENTE_VIRTUAL,
    RESPOSTA_NAO_CONSIGO_IDENTIFICAR,
)


@pytest.mark.parametrize("texto", PARAFRASES_DE_APRESENTACAO)
def test_a_parafrase_da_apresentacao_acende_a_flag(texto: str) -> None:
    """REPROVA antes do conserto — a lista tinha DUAS frases literais ("sou helena" / "sou a
    helena") e o cartao e' redigido pelo modelo.

    Com a flag apagada, `apresentacao_ja_feita` nunca acende e o F6 volta sem teto: a Helena
    repete o cartao a cada turno, que foi o defeito medido (tres turnos seguidos iguais).
    """
    assert _apresentou_se(texto) is True, f"parafrase nao acendeu a flag: {texto!r}"


@pytest.mark.parametrize("texto", TEXTOS_SEM_APRESENTACAO)
def test_o_texto_sem_o_nome_nao_acende_a_flag(texto: str) -> None:
    """O falso positivo e' o lado CARO: ele cala a Helena para sempre naquela conversa. As
    constantes que a cerca TEXTO x FATO envia no lugar do rascunho entram aqui de proposito —
    foi exatamente esse o defeito que criou a marca (`apresentacao_ja_feita` acendia em turno que
    nao mostrou cartao nenhum)."""
    assert _apresentou_se(texto) is False, f"texto sem apresentacao acendeu a flag: {texto!r}"


# =================================================================================================
# 6. [IMPORTANTE] o teto de idade existia so' na MEMORIA
# =================================================================================================


@pytest.mark.parametrize(
    ("campo", "valor"),
    [
        ("idade_meses", 289),
        ("idade_meses", 1200),
        ("idade_anos", 121),
        ("idade_anos", 130),
        ("idade_anos", 900),
    ],
)
def test_a_extracao_recusa_idade_acima_do_teto_sano(campo: str, valor: int) -> None:
    """REPROVA antes do conserto — o teto morava so' na fronteira da MEMORIA.

    A extracao entrega o dado que a pessoa disse NESTE turno, e era ela que alimentava a DMN:
    `idade_meses=1200` ia para a tabela PEDIATRICA (que le' `idade_meses`) como se fosse um
    lactente de 100 anos. A memoria recusava o mesmo valor — duas fronteiras julgando o mesmo
    conceito com reguas diferentes, e a mais permissiva era a que decide.
    """
    razao = _validate_extraction(
        {
            "intent": "symptom",
            "population": "pediatric" if campo == "idade_meses" else "adult",
            "psychosocial_risk": False,
            "sintoma_codigo": "febre",
            "intensidade": "leve",
            campo: valor,
        }
    )

    assert razao == "invalid_age", f"{campo}={valor} passou pela validacao da extracao"


@pytest.mark.parametrize(("campo", "valor"), [("idade_meses", 288), ("idade_anos", 120)])
def test_o_valor_no_teto_continua_valido_nas_duas_fronteiras(campo: str, valor: int) -> None:
    """O teto e' INCLUSIVO, e o mesmo dict (`_TETO_DE_IDADE`) serve as duas fronteiras — e' o que
    mantem "memoria ⊆ extracao" por construcao em vez de por coincidencia."""
    dados: dict[str, Any] = {
        "intent": "symptom",
        "population": "pediatric" if campo == "idade_meses" else "adult",
        "psychosocial_risk": False,
        "sintoma_codigo": "febre",
        "intensidade": "leve",
        campo: valor,
    }

    assert _validate_extraction(dados) is None
    assert _coerce_age(valor) == (True, valor), "`_coerce_age` continua generico, sem teto por campo"


def test_a_idade_gestacional_continua_sem_teto_na_extracao() -> None:
    """A ausencia e' DECLARADA, nao esquecida: qual semana deixa de ser uma gestacao possivel e'
    julgamento clinico (pos-termo existe), e inventar o numero aqui seria a unica afirmacao
    clinica do bloco. Fica na fila de revisao (`docs/review-queue.md`)."""
    razao = _validate_extraction(
        {
            "intent": "symptom",
            "population": "gestante",
            "psychosocial_risk": False,
            "sintoma_codigo": "sangramento_vaginal",
            "intensidade": "leve",
            "idade_gestacional_semanas": 900,
        }
    )

    assert razao is None


async def test_a_idade_impossivel_da_extracao_vira_falha_tecnica_no_no_real() -> None:
    """Ponta a ponta pelo no' `classify`: a idade impossivel nao chega a DMN, e o turno vira
    gatilho 4 (`escalate` / `falha_tecnica`) em vez de uma triagem pediatrica silenciosa."""
    dmn = FakeDmnTransport()
    dmn.register("triage_redflag_pediatric", [{"red_flag": False, "conduta": "CONTINUE"}])
    graph = _graph(_classify_json(idade_meses=1200), dmn=dmn)

    resultado = await graph.classify(_estado(message_body="meu bebe esta com febre"))

    assert resultado["next_kind"] == "escalate"
    assert resultado["escalation_motivo"] == "falha_tecnica"
    assert "invalid_age" in str(resultado["error"])
    assert dmn.calls == [], "a idade impossivel chegou a tabela de red flag"


# =================================================================================================
# 7. [DESEJAVEL] a frase de confirmacao no singular
# =================================================================================================


@pytest.mark.parametrize(
    ("meses", "esperado"),
    [
        (0, "seu recem-nascido"),
        (1, "seu bebe de 1 mes"),
        (2, "seu bebe de 2 meses"),
        (24, "seu bebe de 24 meses"),
        (30, "sua crianca de 2 anos"),
    ],
)
def test_a_frase_de_confirmacao_concorda_em_numero(meses: int, esperado: str) -> None:
    """Esta frase existe PARA a pessoa corrigir o dado que decidiu a tabela. "seu bebe de 1 meses"
    soa a sistema quebrado, e uma frase que soa quebrada e' uma frase que ela para de ler — o
    mesmo argumento que ja' tirou "seu bebe de 36 meses" e "seu bebe de 0 meses"."""
    frase = _frase_de_confirmacao({"idade_meses": meses})

    assert esperado in frase, f"{meses} meses -> {frase!r}"
    if meses == 1:
        assert "1 meses" not in frase


# =================================================================================================
# 8. [DESEJAVEL] `collect`: a recusa por NEGATIVA CLINICA escala
# =================================================================================================


async def test_a_negativa_clinica_no_collect_escala_em_vez_de_perguntar() -> None:
    """A assimetria por GRUPO, e nao por rota.

    A pergunta generica e' uma saida honesta para "o modelo citou um canal que nao existe" ou
    "prometeu o que o canal nao faz": o turno de coleta pode simplesmente perguntar de novo. Nao
    e' saida honesta para a NEGATIVA CLINICA — o unico grupo que fala sobre o CORPO de alguem. Um
    texto que afirma "nao ha sinais de alerta" nao e' um turno de coleta que errou a redacao; e' o
    modelo saindo do roteiro para dar parecer clinico, e a pergunta generica descartaria isso em
    silencio.
    """
    graph = _graph("Pelo que voce contou, nao ha sinais de alerta. A dor esta leve ou forte?")

    saida = await graph.collect(_estado(coleta_pergunta="PERGUNTAR_INTENSIDADE", coleta_rodadas=1))

    assert saida["response_kind"] == "escalate", "a negativa clinica no `collect` virou pergunta generica"
    assert saida.get("escalation_motivo") == "falha_tecnica"


async def test_os_outros_grupos_no_collect_continuam_perguntando() -> None:
    """O RED do teste acima: escalar CADA texto barrado transformaria um defeito de redacao em
    fila humana, e a coleta existe justamente para nao escalar antes de saber."""
    graph = _graph("Antes de continuar: a dor esta leve ou forte? Se preferir, veja no aplicativo do plano.")

    saida = await graph.collect(_estado(coleta_pergunta="PERGUNTAR_INTENSIDADE", coleta_rodadas=1))

    assert saida["response_kind"] == "collect"
    assert "aplicativo do plano" not in str(saida["response_text"]).lower()


# =================================================================================================
# 9. [DESEJAVEL] a fila de revisao que a prosa promete
# =================================================================================================


@pytest.mark.parametrize("ancora", ["SINTOMA_EM_PALAVRAS", "_TETO_DE_IDADE"])
def test_a_pendencia_clinica_esta_registrada_na_fila_de_revisao(ancora: str) -> None:
    """Duas prosas deste modulo dizem que a decisao "e' do dono clinico (docs/review-queue.md)" e
    que "a ausencia fica registrada para a fila de revisao" — e o arquivo nao tinha nenhuma das
    duas linhas. Uma fila de revisao que o codigo cita e que nao contem a pendencia e' pior que
    nenhuma: ela transforma um pendente em um resolvido aos olhos de quem audita."""
    fila = (_REPO_ROOT / "docs" / "review-queue.md").read_text(encoding="utf-8")

    assert ancora in fila, f"{ancora} nao esta registrado em docs/review-queue.md"
