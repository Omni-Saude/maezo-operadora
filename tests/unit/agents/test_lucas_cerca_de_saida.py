"""A cerca de saida do Lucas — o que o beneficiario le' antes de qualquer humano ter decidido.

O QUE ESTE ARQUIVO CERCA. Ate' 18/09/2026 o Lucas tinha TRES proibicoes escritas em maiusculas
nos prompts — "NUNCA ameaca suspensao ou cancelamento" (`SYSTEM_PROMPT`), "NUNCA comunique uma
negativa, suspensao ou cancelamento" (`message_prompt`), "NUNCA revele um desfecho adverso"
(`escalation_ack_prompt`) — e NADA atras delas. `grep -c motivo_de_recusa` no `graph.py` dele dava
zero enquanto a Helena tinha tres pontos de aplicacao.

UMA DIFERENCA DE HONESTIDADE EM RELACAO A' CERCA DA HELENA, e ela e' importante para quem ler este
arquivo depois. A cerca da Helena nasceu de TEXTOS MEDIDOS: em 12-13/09/2026 o modelo produziu, ao
vivo, "nao ha sinais de alerta que exijam encaminhamento imediato" e "eu encaminho sua
solicitacao", e as listas de padroes dela sao a transcricao desses vazamentos. **Nao ha nada
equivalente para o Lucas** — ninguem rodou uma bateria ao vivo contra ele. Os textos deste arquivo
sao DERIVADOS DAS PROIBICOES QUE OS PROPRIOS PROMPTS DELE ESCREVEM, frase por frase, e nao
evidencia de defeito observado.

POR QUE ISSO NAO ENFRAQUECE A CERCA. O que foi medido na Helena nao foi "este padrao especifico
vaza": foi que **a proibicao em prosa nao se sustenta** — o `response-v3` tinha o raciocinio
inteiro escrito e o modelo passou por cima dele duas vezes na mesma conversa. Esse achado e' sobre
a FORMA da protecao, nao sobre a agente, e o Lucas tinha exatamente a mesma forma.

E A APOSTA DELE E' DE OUTRA ORDEM. O que a cerca ausente deixava passar nao era uma frase
imprecisa: era um beneficiario cujo contrato esta' em analise sendo informado por um robo de que
foi suspenso ou cancelado ANTES de qualquer humano ter decidido — a decisao nasce numa User Task
(SP-OP-ESCALATION-001 -> SP-OP-CANCEL-001, RESCINDIR/MANTER/SUSPENDER).

O QUE ESTES TESTES PROVAM E O QUE ELES NAO PROVAM:

1. `test_cada_proibicao_do_prompt_tem_cerca_atras` — PAREAMENTO. Para cada proibicao escrita nos
   prompts, um texto que a viola e' BARRADO. E' o teste que impede a cerca de ficar para tras de
   um prompt que ganhou uma proibicao nova.
2. `test_o_caminho_certo_passa` — NAO-VACUIDADE NO OUTRO SENTIDO. Uma cerca que reprova tudo e'
   tao inutil quanto uma que nao reprova nada, e os padroes daqui foram escolhidos justamente
   para deixar passar "recebi seu pedido de cancelamento" e "voce pode entrar em contato com a
   central".
3. `test_as_constantes_seguras_passam_na_propria_cerca` — a saida de fallback e' segura POR
   CONSTRUCAO, nao por sorte da redacao.
4. Os testes de grafo — que a cerca esta' LIGADA nos dois pontos que chegam ao WhatsApp.
5. O que NENHUM deles prova: que o modelo obedece. Isso so' um turno ao vivo mostra — e a razao
   de a cerca existir e' precisamente que ele nao obedece so' porque pediram.
"""

from __future__ import annotations

from typing import Any

import pytest

from maezo.agents.lucas.graph import (
    ACK_ESCALACAO_RECUSADO,
    DESFECHO_RESPOSTA_RECUSADA,
    RESPOSTA_INFORMATIVA_RECUSADA,
    LucasGraph,
    LucasState,
    RespostaRecusadaError,
)
from maezo.agents.lucas.prompts import (
    ESCALATION_ACK_PROMPT_VERSION,
    PROMPT_VERSIONS,
    RECUSA_DE_SAIDA_VERSION,
    RECUSA_DESFECHO_ADVERSO,
    RECUSA_PROMESSA_DE_CAPACIDADE,
    RECUSA_PROMESSA_DE_HUMANO,
    RECUSA_VALOR_SEM_FATO,
    SYSTEM_PROMPT,
    escalation_ack_prompt,
    message_prompt,
    motivo_de_recusa,
)
from maezo.runtime.turn_telemetry import _DESFECHO_VOCAB
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport
from maezo.tools.workers.dmn_transport import FakeDmnTransport
from tests.support.audit_fakes import FakeStartAuditSink


class _InferenciaFixa:
    """Devolve sempre o MESMO texto — que e' o ponto: a cerca decide sobre o texto, e um teste que
    precisasse do modelo real nao provaria nada sobre ela."""

    def __init__(self, texto: str) -> None:
        self._texto = texto

    async def generate(self, prompt: str, **kwargs: Any) -> str:
        return self._texto


class _SenderSilencioso:
    def __init__(self) -> None:
        self.enviados: list[tuple[str, str]] = []

    async def send(self, to_hash: str, text: str, *, idempotency_key: str | None = None) -> Any:
        self.enviados.append((to_hash, text))
        return {"message_id": "wamid.fake"}


def _graph(texto_do_modelo: str, sender: Any | None = None) -> LucasGraph:
    return LucasGraph(
        inference=_InferenciaFixa(texto_do_modelo),
        dmn=FakeDmnTransport(),
        cibseven=FakeCibSevenTransport(),
        audit_sink=FakeStartAuditSink(),
        whatsapp=sender or _SenderSilencioso(),
    )


# =============================================================================================
# 1. PAREAMENTO — cada proibicao escrita nos prompts tem cerca atras dela
# =============================================================================================

#: `(proibicao literal no prompt, texto que a viola, grupo esperado, rota)`.
#:
#: A PRIMEIRA COLUNA E' O PONTO: ela e' um trecho LITERAL de `prompts.py`, verificado pelo teste
#: contra o texto do prompt. Se alguem reescrever a proibicao, este teste fica vermelho antes de a
#: cerca ficar desalinhada em silencio — que e' o modo de falha que uma lista de padroes solta
#: tem, e que ninguem percebe ate' o dia em que ela deixa passar.
PAREAMENTO: list[tuple[str, str, str, str]] = [
    (
        "NUNCA ameace suspensao ou cancelamento",
        "Lembramos que seu boleto vence hoje. Seu plano sera suspenso caso o pagamento nao seja "
        "identificado ate' amanha.",
        RECUSA_DESFECHO_ADVERSO,
        "mensagem",
    ),
    (
        "NUNCA comunique uma negativa, suspensao ou cancelamento",
        "Informamos que seu contrato foi cancelado por inadimplencia.",
        RECUSA_DESFECHO_ADVERSO,
        "mensagem",
    ),
    (
        "NUNCA revele um desfecho adverso",
        "Seu caso esta com um atendente. Seu plano foi suspenso enquanto isso.",
        RECUSA_DESFECHO_ADVERSO,
        "ack_escalacao",
    ),
    (
        "NUNCA afirme um status que nao esteja no fato fornecido",
        "Ja dei baixa no seu pagamento, esta tudo certo.",
        RECUSA_PROMESSA_DE_CAPACIDADE,
        "mensagem",
    ),
    (
        "NAO invente numero de boleto/valor que nao esteja nos fatos",
        "O valor em aberto e de R$ 1.284,90, com vencimento em 20/09.",
        RECUSA_VALOR_SEM_FATO,
        "mensagem",
    ),
    (
        "NAO prometa um prazo que voce nao controla",
        "Um atendente vai entrar em contato com voce ainda hoje.",
        RECUSA_PROMESSA_DE_HUMANO,
        "mensagem",
    ),
]


@pytest.mark.parametrize(("proibicao", "texto", "grupo", "rota"), PAREAMENTO)
def test_cada_proibicao_do_prompt_tem_cerca_atras(proibicao: str, texto: str, grupo: str, rota: str) -> None:
    # ESPACO COLAPSADO de proposito: os prompts sao triple-quoted e quebram linha no meio das
    # frases ("NUNCA ameace\n  suspensao ou cancelamento"). Comparar cru faria este teste falhar
    # por formatacao — um vermelho que nao e' sobre a cerca ensina a gente a ignora-lo.
    prompts_juntos = " ".join(f"{SYSTEM_PROMPT}\n{message_prompt()}\n{escalation_ack_prompt()}".split())
    assert proibicao in prompts_juntos, (
        f"a proibicao {proibicao!r} nao esta mais escrita nos prompts — se ela foi removida de "
        "proposito, o padrao correspondente da cerca sai junto; se foi so' reescrita, este "
        "pareamento precisa acompanhar a nova redacao"
    )
    # `fatos={}` e' o estado real de `_build_message`: o dicionario de fatos nao tem campo de
    # valor monetario, entao nenhuma quantia e' justificavel.
    recusa = motivo_de_recusa(texto, rota, {})
    assert recusa is not None, f"texto que viola {proibicao!r} PASSOU pela cerca"
    assert recusa[0] == grupo


# =============================================================================================
# 2. NAO-VACUIDADE NO OUTRO SENTIDO — o caminho certo passa
# =============================================================================================

#: Cada um destes e' uma resposta que o Lucas DEVE poder dar, e cada um exercita a fronteira de um
#: grupo. Sao eles que justificam os padroes serem verbo+objeto e sujeito+verbo, em vez da palavra
#: solta — uma cerca escrita sobre "cancelamento" ou sobre "entre em contato" reprovaria os dois
#: primeiros, que sao exatamente o atendimento correto.
LEGITIMOS: list[tuple[str, str, dict[str, Any] | None]] = [
    (
        "Recebi seu pedido de cancelamento e ja encaminhei para analise da nossa equipe.",
        "ack_escalacao",
        None,
    ),
    (
        "Voce pode entrar em contato com a central de atendimento pelo 0800 para obter a 2a via.",
        "mensagem",
        {},
    ),
    (
        "Seu boleto da competencia 2026-09 consta como conciliado. Obrigado!",
        "mensagem",
        {"competencia": "2026-09", "status_conciliado": True},
    ),
    (
        "Recebemos sua solicitacao e nossa equipe vai entrar em contato para dar continuidade.",
        "ack_escalacao",
        None,
    ),
    (
        "A 2a via pode ser emitida no portal do beneficiario, na area Financeiro.",
        "mensagem",
        {},
    ),
    # A FRONTEIRA DO GRUPO DE VALOR: a quantia esta' NOS FATOS, entao ela nao foi inventada. E' o
    # caso que prova que `valor_sem_fato` e' uma comparacao com os fatos e nao uma proibicao cega
    # da string "R$" — no dia em que a conciliacao trouxer valor, este caminho continua aberto.
    (
        "O valor em aberto e de R$ 450,00, referente a competencia 2026-09.",
        "mensagem",
        {"valor_em_aberto": "450,00", "competencia": "2026-09"},
    ),
]


@pytest.mark.parametrize(("texto", "rota", "fatos"), LEGITIMOS)
def test_o_caminho_certo_passa(texto: str, rota: str, fatos: dict[str, Any] | None) -> None:
    assert motivo_de_recusa(texto, rota, fatos) is None, (
        "a cerca reprovou um atendimento CORRETO — uma cerca que barra o caminho certo e' tao "
        "inutil quanto uma que nao barra nada, porque ela sera' desligada"
    )


def test_a_promessa_de_humano_e_rota_condicional() -> None:
    """A parte delicada da cerca, e a mesma da Helena: a MESMA frase e' falsa numa rota e
    obrigatoria na outra.

    `respond_member` NUNCA abre processo (`start_process` pula em `route == "respond_member"`),
    entao prometer humano la' e' mentira. Ja' `send_escalation_ack` so' roda com
    `process_started is True` — o humano FOI acionado, e o proprio `escalation_ack_prompt` manda
    avisar. Uma cerca incondicional reprovaria justamente o caminho certo.
    """
    frase = "Nossa equipe vai entrar em contato em breve."
    assert motivo_de_recusa(frase, "mensagem", {}) == (
        RECUSA_PROMESSA_DE_HUMANO,
        "nossa equipe vai entrar em contato",
    )
    assert motivo_de_recusa(frase, "ack_escalacao", None) is None


def test_o_desfecho_adverso_e_proibido_ate_no_ack() -> None:
    """A diferenca entre os grupos 1 e 3, e a razao de o grupo 1 nao ser rota-condicional: no ACK
    um humano foi acionado (entao prometer humano e' verdade), mas NENHUMA decisao foi tomada —
    ela nasce depois, na User Task. Revelar desfecho continua proibido exatamente onde o
    beneficiario esta' mais vulneravel a ouvi-lo."""
    assert motivo_de_recusa("Seu contrato foi rescindido.", "ack_escalacao", None) == (
        RECUSA_DESFECHO_ADVERSO,
        "seu contrato foi rescindido",
    )


def test_o_desfecho_adverso_ganha_da_promessa_de_humano() -> None:
    """Quando um texto viola mais de um grupo, o achado reportado e' o MAIS GRAVE — e o desfecho
    adverso e' o unico que fala sobre o CONTRATO de alguem, que e' o acesso dessa pessoa a
    saude. O grupo vira rotulo de metrica, entao a ordem decide o que um operador ve' no painel.
    """
    texto = "Seu plano foi cancelado. Nossa equipe vai entrar em contato."
    assert motivo_de_recusa(texto, "mensagem", {})[0] == RECUSA_DESFECHO_ADVERSO


# =============================================================================================
# 3. AS SAIDAS SEGURAS SAO SEGURAS POR CONSTRUCAO
# =============================================================================================


def test_as_constantes_seguras_passam_na_propria_cerca() -> None:
    """Uma constante de fallback que violasse a cerca seria o pior defeito possivel: ela e' o que
    o beneficiario recebe JUSTAMENTE quando o modelo ja' errou uma vez. Este teste e' o que
    transforma "eu reli e esta' certo" em cerca.

    Note a assimetria deliberada: a informativa e' testada na rota `mensagem`, onde promessa de
    humano e' PROIBIDA — e ela passa porque nao promete nenhum, ja' que essa rota nunca abre
    processo.
    """
    assert motivo_de_recusa(RESPOSTA_INFORMATIVA_RECUSADA, "mensagem", {}) is None
    assert motivo_de_recusa(ACK_ESCALACAO_RECUSADO, "ack_escalacao", None) is None


def test_a_resposta_informativa_de_fallback_nao_promete_humano() -> None:
    """Mais forte que o teste acima e por uma razao propria: a constante informativa tem de
    continuar segura mesmo se alguem, um dia, tornar a promessa de humano incondicional."""
    assert motivo_de_recusa(RESPOSTA_INFORMATIVA_RECUSADA, "mensagem", {}) is None
    assert motivo_de_recusa(RESPOSTA_INFORMATIVA_RECUSADA, "rota_inexistente", {}) is None


# =============================================================================================
# 4. A CERCA ESTA LIGADA — nos dois pontos que chegam ao WhatsApp, e so' neles
# =============================================================================================


@pytest.mark.asyncio
async def test_a_mensagem_informativa_barrada_vira_a_constante_segura() -> None:
    g = _graph("Seu plano sera suspenso caso nao pague ate' amanha.")
    state: LucasState = {
        "tenant_id": "t1",
        "tipo_solicitacao": "boleto",
        "competencia": "2026-09",
        "admissibilidade": "LEMBRETE",
    }  # type: ignore[typeddict-item]
    mensagem = await g._build_message(state)
    assert mensagem["texto"] == RESPOSTA_INFORMATIVA_RECUSADA
    assert mensagem["recusa_de_saida"] is True


@pytest.mark.asyncio
async def test_a_mensagem_barrada_nao_e_contada_como_resposta_enviada() -> None:
    """O desfecho e' o que um operador conta. Chamar isto de `resposta_informativa_enviada`
    afirmaria um atendimento que nao aconteceu — o beneficiario recebeu a constante, nao a
    resposta que os fatos pediam."""
    sender = _SenderSilencioso()
    g = _graph("Informamos que seu contrato foi cancelado por inadimplencia.", sender)
    state: LucasState = {
        "tenant_id": "t1",
        "to_hash": "hk1_abc",
        "canal": "whatsapp",
        "conversation_id": "wa:t1:hk1_abc",
        "admissibilidade": "LEMBRETE",
    }  # type: ignore[typeddict-item]
    saida = await g.respond_member(state)
    assert saida["desfecho"] == DESFECHO_RESPOSTA_RECUSADA
    assert sender.enviados and sender.enviados[0][1] == RESPOSTA_INFORMATIVA_RECUSADA


@pytest.mark.asyncio
async def test_o_ack_de_escalacao_barrado_vira_a_constante_segura() -> None:
    g = _graph("Recebemos seu caso. Seu plano foi suspenso enquanto analisamos.")
    state: LucasState = {"tenant_id": "t1", "motivo_humano": "inadimplencia"}  # type: ignore[typeddict-item]
    assert await g._build_escalation_ack(state) == ACK_ESCALACAO_RECUSADO


@pytest.mark.asyncio
async def test_o_dossie_do_humano_nao_e_cercado() -> None:
    """DECISAO DELIBERADA, e o teste existe para que ela seja uma decisao e nao um esquecimento.

    A narrativa do dossie e' lida por um ATENDENTE HUMANO que precisa ver a inadimplencia e o
    pedido de cancelamento NOMEADOS para decidir. Aplicar a cerca do beneficiario ali apagaria
    exatamente o que ele tem de saber. O que protege o humano de receber uma DECISAO pronta e'
    outra cerca, estrutural e ja' existente: `decisao_cancelamento` e' sempre `None`.
    """
    narrativa = "Beneficiario inadimplente ha 3 ciclos; pediu cancelamento do contrato."
    g = _graph(narrativa)
    state: LucasState = {"tenant_id": "t1", "motivo_humano": "inadimplencia"}  # type: ignore[typeddict-item]
    dossie = await g._build_dossier(state)
    assert dossie["narrativa"] == narrativa
    assert dossie["decisao_cancelamento"] is None


# =============================================================================================
# 5. OS LITERAIS DUPLICADOS E AS VERSOES
# =============================================================================================


def test_o_desfecho_de_recusa_esta_no_vocabulario_de_telemetria() -> None:
    """`turn_telemetry` nao importa grafos, entao o token vive em duas copias. Um valor fora do
    vocabulario e' normalizado para `"outro"` — o que apagaria justamente a distincao que a cerca
    cria. Este teste e' o unico que impede as duas copias de divergirem em silencio."""
    assert DESFECHO_RESPOSTA_RECUSADA in _DESFECHO_VOCAB["lucas"]


def test_as_versoes_de_prompt_declaram_o_ack_e_a_cerca() -> None:
    """O ACK de escalacao — o texto do caminho ADVERSO — era o unico do Lucas sem constante de
    versao ate' 18/09/2026. Olhando uma mensagem que vazasse, nao havia como dizer qual redacao a
    produziu. A cerca entra pelo mesmo motivo: ela decide o que o beneficiario le'."""
    assert PROMPT_VERSIONS["escalation_ack"] == ESCALATION_ACK_PROMPT_VERSION
    assert PROMPT_VERSIONS["recusa_de_saida"] == RECUSA_DE_SAIDA_VERSION


def test_a_excecao_carrega_o_grupo_a_rota_e_o_padrao_mas_nunca_o_texto() -> None:
    """O texto recusado e' saida de modelo sobre a cobranca de uma pessoa. Ele nao entra no log,
    nao entra no contador e nao entra na excecao — que viaja para campos de estado. O que viaja e'
    o GRUPO (fechado, vira rotulo de metrica) e o PADRAO (fechado, vira linha de log)."""
    with pytest.raises(RespostaRecusadaError) as exc:
        LucasGraph._cercar_saida(_graph("x"), "Seu plano foi cancelado por inadimplencia.", "mensagem", {})
    assert exc.value.grupo == RECUSA_DESFECHO_ADVERSO
    assert exc.value.response_kind == "mensagem"
    assert "inadimplencia" not in str(exc.value)
