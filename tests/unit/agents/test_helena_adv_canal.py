"""ADVERSARIAL (21/09/2026) — a cerca de CANAL (F7) atacada pelos dois lados.

A cerca nova (`motivo_de_canal_nao_confirmado`) tem um custo assimetrico que e' o que torna este
arquivo necessario:

  * FALSO POSITIVO custa um ESCALONAMENTO HUMANO. Em `inform`, um texto recusado nao vira outro
    texto — vira `_start_escalation(motivo="falha_tecnica")`, ou seja fila de gente
    (`graph.py::inform`). Uma cerca que reprova texto correto cria trabalho para o atendimento e
    e' desligada na semana seguinte.
  * FALSO NEGATIVO custa o proprio F7: o beneficiario e' mandado para um canal que nao existe.

Entao o arquivo tem tres blocos: o que a cerca PRECISA reprovar (variacoes de caixa, espaco,
acento e forma unicode dos nomes medidos), o que ela NAO pode reprovar (os canais confirmados,
"whatsapp", "visite" e as constantes do proprio modulo) e os BURACOS reais que sobraram.

`test_helena_canais_confirmados.py` cobre a lista e o wiring. Aqui se ataca a NORMALIZACAO e a
COBERTURA dos padroes — o que nenhuma das duas mede.
"""

from __future__ import annotations

import time
from typing import Any, cast

import pytest

from maezo.agents.helena.graph import (
    RESPOSTA_FALHA_TECNICA_START,
    RESPOSTA_HANDOFF_JA_ABERTO,
    RESPOSTA_HANDOFF_RECUSADA,
    RESPOSTA_SEM_ENCAMINHAMENTO,
    HelenaGraph,
    HelenaState,
)
from maezo.agents.helena.prompts import (
    CANAIS_CONFIRMADOS,
    RECUSA_CANAL_NAO_CONFIRMADO,
    RESPOSTA_NAO_CONSIGO_IDENTIFICAR,
    RESPOSTA_SOU_ASSISTENTE_VIRTUAL,
    motivo_de_canal_nao_confirmado,
)
from maezo.runtime.inference import InferenceProvider
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport
from maezo.tools.workers.dmn_transport import FakeDmnTransport
from tests.support.audit_fakes import FakeStartAuditSink


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


def _graph(texto: str, *, whatsapp: _WhatsApp | None = None) -> HelenaGraph:
    return HelenaGraph(
        inference=cast(InferenceProvider, _InferenciaFixa(texto)),
        dmn=FakeDmnTransport(),
        cibseven=FakeCibSevenTransport(),
        audit_sink=FakeStartAuditSink(),
        whatsapp=cast(Any, whatsapp or _WhatsApp()),
    )


def _estado(**extra: Any) -> HelenaState:
    s: HelenaState = {
        "tenant_id": "amh",
        "conversation_id": "wa:amh:adv_canal",
        "canal": "whatsapp",
        "beneficiario_pseudo_id": "PSEUDO-ADV",
        "message_body": "onde vejo o valor da mensalidade?",
    }
    s.update(extra)  # type: ignore[typeddict-item]
    return s


# =================================================================================================
# 1. O que a cerca PRECISA reprovar — variacoes de forma do MESMO nome inventado
# =================================================================================================


@pytest.mark.parametrize(
    "texto",
    [
        "voce ve no aplicativo do plano",
        "voce ve no Aplicativo do Plano",
        "voce ve no APLICATIVO DO PLANO",
        "voce ve no aplicativo do plano.",
        # Espaco duplo: a primeira passagem (substring) NAO casa, e e' a segunda (termo generico
        # sem o nome confirmado) que precisa pegar. Sem ela, um espaco a mais desligaria a cerca.
        "voce ve no app  do plano",
        "voce ve no portal  da  operadora",
        "voce ve no pórtal da operadora",  # acento sobrando, forma NFC
        "voce ve na área do cliente",
        "voce ve na área do cliente",  # a + combining acute, forma NFD
        "voce ve no ａｐｐ do plano",  # fullwidth "app" (NFKD compoe para ascii)
        "ligue 0800 123 4567",
        "ligue para o 0800-123-4567",
        "ligue (11) 4004-4000",
        "ligue 3211-4000",
        "acesse o site",
        "acesse nosso Site.",
        "baixe o aplicativo",
        "veja no portal",
        "veja nos aplicativos",
    ],
)
def test_toda_forma_do_canal_inventado_e_recusada(texto: str) -> None:
    """A normalizacao (`_normalizar`: NFKD + sem combinantes + minuscula) tem de tornar caixa,
    acento e forma unicode IRRELEVANTES — senao a cerca depende de o modelo escrever exatamente
    como a lista, o que e' a premissa que a bateria de 13/09 derrubou duas vezes.
    """
    achado = motivo_de_canal_nao_confirmado(texto)

    assert achado is not None, f"canal nao confirmado passou: {texto!r}"
    assert achado[0] == RECUSA_CANAL_NAO_CONFIRMADO


async def test_o_canal_inventado_nao_chega_ao_beneficiario_pelo_no_inform() -> None:
    """A cerca ligada no no' real: um `inform` com canal inventado nao entrega aquele texto.

    Aqui nao se testa "qual rota o turno vira" (isso e'
    `test_helena_wiring_canal_e_apresentacao.py`), e sim o unico fato que o beneficiario
    observa: o nome que nao existe NAO e' enviado.
    """
    whatsapp = _WhatsApp()
    graph = _graph("Voce consulta isso no aplicativo do plano.", whatsapp=whatsapp)

    do_inform = await graph.inform(_estado())
    await graph.respond(_estado(**do_inform))

    assert whatsapp.enviados, "o turno tem de dizer alguma coisa"
    assert "aplicativo do plano" not in whatsapp.enviados[0][1].lower()


# =================================================================================================
# 2. O que a cerca NAO pode reprovar — falso positivo custa fila de gente
# =================================================================================================


@pytest.mark.parametrize(
    "texto",
    [
        # Os tres canais confirmados, escritos como o prompt manda.
        "Voce consulta no aplicativo Austa Clinicas.",
        "Voce consulta no portal do plano.",
        "Fale com a central de atendimento do plano.",
        "Da' para ver no aplicativo Austa Clinicas ou no portal do plano.",
        "Voce consulta no aplicativo Austa Clínicas.",  # com acento, como a Helena escreve
        # Palavras que CONTEM os padroes e nao sao canais — os dois casos que a propria
        # declaracao de `TermoDeCanal` cita como razao de usar palavra inteira.
        "Pode me mandar por aqui mesmo, no WhatsApp.",
        "Se quiser, visite uma unidade proxima.",
        # Texto administrativo honesto, sem canal nenhum.
        "Este canal pode te orientar sobre onde consultar o valor da mensalidade.",
        "Nao consigo emitir boleto por aqui.",
        # As duas frases fixas de conformidade (F6) vao LITERALMENTE ao beneficiario.
        RESPOSTA_SOU_ASSISTENTE_VIRTUAL,
        RESPOSTA_NAO_CONSIGO_IDENTIFICAR,
        # As quatro constantes que a cerca TEXTO x FATO envia no lugar do rascunho.
        RESPOSTA_HANDOFF_RECUSADA,
        RESPOSTA_HANDOFF_JA_ABERTO,
        RESPOSTA_SEM_ENCAMINHAMENTO,
        RESPOSTA_FALHA_TECNICA_START,
        # Numeros que NAO sao telefone: prazo, semana de gestacao, quantidade.
        "O prazo de resposta e' de 24 horas.",
        "Voce esta de 33 semanas.",
        "Sao 3 dias uteis.",
        "",
        "   ",
    ],
)
def test_o_texto_legitimo_nunca_e_reprovado(texto: str) -> None:
    """NAO-VACUIDADE, e com preco: em `inform` uma recusa vira escalonamento humano
    (`graph.py::inform` -> `_start_escalation(motivo="falha_tecnica")`). Cada falso positivo aqui
    e' uma pessoa do atendimento recebendo um caso que nao existe.
    """
    assert motivo_de_canal_nao_confirmado(texto) is None, f"texto legitimo reprovado: {texto!r}"


def test_todo_canal_confirmado_passa_na_propria_cerca() -> None:
    """A lista e a cerca sao o MESMO artefato em dois lugares: um nome novo em
    `CANAIS_CONFIRMADOS` que a cerca reprove tornaria o prompt e a cerca inimigos, e o turno
    correto viraria escalonamento.
    """
    for canal in CANAIS_CONFIRMADOS:
        frase = f"Voce resolve isso em {canal.nome}."
        assert motivo_de_canal_nao_confirmado(frase) is None, (
            f"a cerca reprova um canal CONFIRMADO: {canal.nome!r}"
        )


def test_a_cerca_nao_tem_backtracking_catastrofico_em_texto_longo() -> None:
    """ReDoS: a cerca roda em TODO texto que vai ao beneficiario, dentro do turno. Um padrao com
    backtracking exponencial transformaria uma resposta longa (ou um `message_body` que o modelo
    ecoa) numa indisponibilidade do agente.

    O limite e' generoso de proposito — o que se procura aqui e' a diferenca entre milissegundos
    e minutos, nao uma medicao de desempenho.
    """
    veneno = ("aplicativ " * 20_000) + ("(1 " * 20_000) + ("1234-" * 20_000)

    inicio = time.perf_counter()
    motivo_de_canal_nao_confirmado(veneno)
    decorrido = time.perf_counter() - inicio

    assert decorrido < 5.0, f"a cerca levou {decorrido:.2f}s em {len(veneno)} caracteres"


# =================================================================================================
# 3. OS BURACOS — o que a cerca deixa passar hoje
# =================================================================================================


def test_bug_um_caractere_invisivel_desliga_a_cerca_de_canal() -> None:
    """REPROVA — bug real: `_normalizar` remove ACENTO, nao remove caractere de FORMATO.

    `unicodedata.normalize("NFKD", t)` + descarte de combinantes resolve caixa e acento e nao
    toca em `Cf` (zero-width space/joiner, soft hyphen, RLM). Um unico `\\u200b` no meio de
    "aplicativo" derruba as DUAS passagens: a substring nao casa e o `\\b...\\b` tambem nao.

    POR QUE ISSO IMPORTA NUM AGENTE, e nao e' paranoia de unicode: o texto cercado e' SAIDA DE
    MODELO redigida sobre a mensagem do beneficiario, que o proprio modulo trata como conteudo de
    TERCEIRO (HEL-06, `render_untrusted_block`). A cerca e' a ultima linha entre aquele conteudo e
    o beneficiario; se ela e' a unica defesa, ela nao pode ser a mais fragil.

    Conserto de UMA linha em `prompts.py::_normalizar`: descartar tambem
    `unicodedata.category(c) == "Cf"`.
    """
    invisiveis = {
        "zero width space": "apli​cativo do plano",
        "zero width non-joiner": "apli‌cativo do plano",
        "word joiner": "por⁠tal da operadora",
        "soft hyphen": "app­ do plano",
    }

    escapam = {nome: t for nome, t in invisiveis.items() if motivo_de_canal_nao_confirmado(t) is None}

    assert not escapam, (
        "caracteres de formato atravessam `_normalizar` (prompts.py:745) e desligam a cerca: "
        f"{sorted(escapam)}"
    )


def test_bug_numero_de_telefone_em_qualquer_forma_nao_e_qualquer_forma() -> None:
    """REPROVA — bug real: a declaracao promete mais do que os padroes cobrem.

    `CANAL_TERMO_QUE_EXIGE_O_NOME` (prompts.py:706) diz, em comentario:

        "Numero de telefone em qualquer forma: DDD entre parenteses ou numero local com hifen."

    A primeira metade e' a intencao, a segunda e' a implementacao — e as duas nao sao a mesma
    coisa. O `response_prompt` e' categorico ("NUNCA escreva ... nem numero de telefone (nem
    0800)"), e um numero com espaco, com ponto ou sem separador nenhum sai inteiro. O dano e' o
    declarado pelo dono: "a Helena nao sabe qual numero atende o contrato de quem esta do outro
    lado, e um numero errado e' pior que nenhum".

    Conserto: o separador vira opcional/classe (`\\b\\d{4,5}[\\s.-]?\\d{4}\\b`), e nao dois
    literais.
    """
    formas = [
        "ligue 4004 4000",
        "ligue 4004.4000",
        "ligue 32114000",
        "ligue 11 3211-4000",
        "ligue +55 11 3211 4000",
    ]

    escapam = [t for t in formas if motivo_de_canal_nao_confirmado(t) is None]

    assert not escapam, f"numero de telefone que a cerca nao ve: {escapam}"


def test_bug_o_plural_e_o_composto_escapam_do_termo_generico() -> None:
    """REPROVA — bug real de cobertura: `\\baplicativos?\\b` tem o plural, `\\bapp\\b` nao.

    A assimetria e' visivel no proprio `CANAL_TERMO_QUE_EXIGE_O_NOME`: dois dos quatro termos de
    texto levam `s?` e dois nao. "apps", "website" e "websites" sao palavras que um modelo escreve
    sem esforco, e as tres passam — inclusive "website", que e' o MESMO canal que `site` proibe
    incondicionalmente (`exige=None`).

    Conserto: `\\bapps?\\b` e `\\b(web)?sites?\\b`.
    """
    formas = [
        "baixe um dos nossos apps",
        "acesse nosso website",
        "acesse nossos websites",
    ]

    escapam = [t for t in formas if motivo_de_canal_nao_confirmado(t) is None]

    assert not escapam, f"termo generico de canal que a cerca nao ve: {escapam}"


def test_bug_a_central_de_atendimento_nao_tem_cerca_de_nome() -> None:
    """REPROVA — bug real: o TERCEIRO canal confirmado ficou sem cerca nenhuma.

    `CANAIS_CONFIRMADOS` declara "a central de atendimento DO PLANO". O aplicativo e o portal tem
    as duas cercas (nome errado proibido + termo generico exigindo o nome); a central nao tem
    nenhuma das duas. Entao "a central de atendimento da operadora" e "a central do convenio" —
    exatamente a familia de nomes que o F7 mediu em CINCO casos — saem intactas, ao lado de
    "portal da operadora", que e' recusado.

    E' o descompasso que a propria entrega diz existir para tornar visivel: "ESTA LISTA E' A FONTE
    UNICA ... editar um sem o outro e' o descompasso que as duas pecas existem para tornar
    visivel" (prompts.py:340).

    Conserto: "central de atendimento" entra em `CANAL_TERMO_QUE_EXIGE_O_NOME` com
    `exige="central de atendimento do plano"`, e as variacoes de dono ("da operadora", "do
    convenio") entram na lista de nomes proibidos.
    """
    formas = [
        "fale com a central de atendimento da operadora",
        "fale com a central de atendimento do convenio",
        "fale com a central do beneficiario",
    ]

    escapam = [t for t in formas if motivo_de_canal_nao_confirmado(t) is None]

    assert not escapam, f"nome de central que ninguem confirmou e a cerca nao ve: {escapam}"


async def test_bug_a_cerca_de_canal_nao_alcanca_a_rota_collect() -> None:
    """REPROVA — bug real de WIRING (latente: `coleta_enabled=False` por default).

    `_respond_llm` declara ser o ponto unico de passagem:

        "ESTE e' o unico ponto por onde passa todo texto que chega ao beneficiario — `inform`
         (1x), `_start_escalation` (escalate e schedule) e o fallback de `respond`."

    A enumeracao nao tem `collect`, e o no' `collect` (graph.py:1815) monta prompt proprio, chama
    `self._llm.generate` direto e devolve `response_text` SEM passar por cerca nenhuma. Em
    `respond`, a unica cerca do turno e' a TEXTO x FATO — que nao chama
    `motivo_de_canal_nao_confirmado`. Resultado: na rota de coleta, o canal inventado chega ao
    beneficiario.

    Alcance honesto: hoje a rota so' e' escolhida com `coleta_enabled=True` (default `False`,
    dependente da tabela `triage_sufficiency` no motor). O no' JA EXISTE e ja' e' chamavel, e a
    cerca e' de uma entrega posterior a ele — quando a feature ligar, o F7 volta por esta porta.
    """
    whatsapp = _WhatsApp()
    graph = _graph(
        "Antes de continuar: a dor esta' leve, moderada ou forte? Se preferir, veja no aplicativo do plano.",
        whatsapp=whatsapp,
    )

    do_collect = await graph.collect(_estado(coleta_pergunta="PERGUNTAR_INTENSIDADE", coleta_rodadas=1))
    await graph.respond(_estado(**do_collect))

    assert whatsapp.enviados, "o turno de coleta tem de perguntar alguma coisa"
    assert "aplicativo do plano" not in whatsapp.enviados[0][1].lower(), (
        "a rota `collect` entrega o canal nao confirmado — nenhuma das duas cercas de saida roda "
        "nela (graph.py:1815 `collect`, graph.py:2587 o unico ponto de chamada da cerca de canal)"
    )
