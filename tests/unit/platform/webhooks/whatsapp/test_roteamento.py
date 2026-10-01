"""A maquina de estados do numero unico (ADR-0062; plano `lucas-numero-unico.md` §2.2) e o CAS.

A TABELA DE VERDADE abaixo cruza os cinco estados de partida possiveis (linha ausente, Helena
viva, Lucas vivo, Helena vencida, Lucas vencido) com todos os eventos que a maquina distingue,
mais as combinacoes que testam a ORDEM de precedencia. Cada celula e' declarada explicitamente:
mudar o comportamento de uma celula exige editar esta tabela.

Depois dela vem a propriedade estrutural de §2.3 (so' um `handoff` tipado deste turno leva ao
Lucas; nada fica no Lucas por omissao), checada sobre a grade inteira de entradas, e o
compare-and-set: duas escritas concorrentes, uma vence, a outra rele' e recalcula.
"""

from __future__ import annotations

import asyncio
import dataclasses
import itertools
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from maezo.platform.webhooks.whatsapp import pre_roteamento
from maezo.platform.webhooks.whatsapp.roteamento import (
    COBRANCA_SUBTIPOS,
    TRANSICOES,
    ConflitoDeRevisaoError,
    ConversaRouter,
    Decisao,
    EventoDoTurno,
    LinhaAgenteAtivo,
    PedidoDeHandoff,
    PostgresAgenteAtivoStore,
    RoteamentoError,
    decidir_transicao,
    evento_do_resultado_da_helena,
)
from tests.support.roteamento_fakes import FakeAgenteAtivoStore

_AGORA = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
_CONVERSA = "wa:amh:hk1_0123abcd"
_MIGRATION = (
    Path(__file__).resolve().parents[5]
    / "src/maezo/platform/migrations/versions/0017_conversa_agente_ativo.py"
)


def _linha(agente: str, motivo: str, *, vencida: bool = False, revisao: int = 3) -> LinhaAgenteAtivo:
    lucas = agente == "lucas"
    return LinhaAgenteAtivo(
        conversation_id=_CONVERSA,
        agente_ativo=agente,  # type: ignore[arg-type]
        revisao=revisao,
        transicao_motivo=motivo,  # type: ignore[arg-type]
        lucas_cobranca_subtipo="boleto_2via" if lucas else None,
        lucas_competencia="2026-09" if lucas else None,
        ativo_desde=_AGORA - timedelta(hours=3),
        ultimo_turno_em=_AGORA - timedelta(hours=2 if vencida else 0, minutes=10),
        expira_em=_AGORA - timedelta(minutes=1) if vencida else _AGORA + timedelta(minutes=50),
    )


#: Os cinco estados de partida. A Helena viva parte de `retorno_saude` para provar que, sem
#: evento, o motivo de partida e' PRESERVADO (nao reescrito para `inicio`).
ESTADOS: dict[str, LinhaAgenteAtivo | None] = {
    "ausente": None,
    "helena_viva": _linha("helena", "retorno_saude"),
    "lucas_vivo": _linha("lucas", "handoff_cobranca"),
    "helena_vencida": _linha("helena", "retorno_falha", vencida=True),
    "lucas_vencido": _linha("lucas", "continua_lucas", vencida=True),
}

_HANDOFF = PedidoDeHandoff(cobranca_subtipo="contestacao", competencia="2026-08")

EVENTOS: dict[str, EventoDoTurno] = {
    # linha 2 de §2.2 — saude, em cada forma que a maquina reconhece
    "red_flag": EventoDoTurno(
        intent="symptom", escalation_motivo="red_flag_clinico", sintoma_codigo="dor_toracica"
    ),
    "risco_psicossocial": EventoDoTurno(
        intent="symptom", escalation_motivo="risco_psicossocial", psychosocial_risk=True
    ),
    "pergunta_clinica": EventoDoTurno(intent="clinical_question", escalation_motivo="intencao_clinica"),
    "coleta": EventoDoTurno(intent="symptom", abriu_coleta=True),
    "sintoma_informado": EventoDoTurno(intent="symptom", sintoma_codigo="cefaleia"),
    "lexico_saude": EventoDoTurno(intent="information", sinal_saude_lexico=True),
    # linha 3 — pedido de pessoa
    "pedido_humano": EventoDoTurno(intent="human_request", escalation_motivo="solicitacao_humano"),
    "lexico_humano": EventoDoTurno(intent="information", pedido_humano_lexico=True),
    # linha 4 — falha tecnica
    "falha_tecnica": EventoDoTurno(escalation_motivo="falha_tecnica", falha_tecnica=True),
    # linhas 5 e 6 — handoff tipado
    "handoff": EventoDoTurno(handoff=_HANDOFF),
    "handoff_lucas_escalou": EventoDoTurno(handoff=_HANDOFF, lucas_escalou=True),
    # linhas 7 a 9 — o Lucas ativo
    "greeting": EventoDoTurno(intent="greeting"),
    "information": EventoDoTurno(intent="information"),
    "information_lucas_escalou": EventoDoTurno(intent="information", lucas_escalou=True),
    "outside_channel": EventoDoTurno(intent="outside_channel"),
    "scheduling": EventoDoTurno(intent="scheduling"),
    "sem_intencao": EventoDoTurno(),
    # PRECEDENCIA: o primeiro que casar vence
    "saude_e_humano": EventoDoTurno(
        intent="symptom",
        escalation_motivo="risco_psicossocial",
        psychosocial_risk=True,
        pedido_humano_lexico=True,
    ),
    "handoff_com_lexico_saude": EventoDoTurno(handoff=_HANDOFF, sinal_saude_lexico=True),
    "handoff_com_lexico_humano": EventoDoTurno(handoff=_HANDOFF, pedido_humano_lexico=True),
    "handoff_com_falha": EventoDoTurno(handoff=_HANDOFF, falha_tecnica=True),
}

H = "helena"
L = "lucas"


def _todos(agente: str, motivo: str) -> dict[str, tuple[str, str, bool]]:
    return dict.fromkeys(ESTADOS, (agente, motivo, False))


#: (agente, transicao, enviar_frase) por (evento, estado).
ESPERADO: dict[str, dict[str, tuple[str, str, bool]]] = {
    "red_flag": _todos(H, "retorno_saude"),
    "risco_psicossocial": _todos(H, "retorno_saude"),
    "pergunta_clinica": _todos(H, "retorno_saude"),
    "coleta": _todos(H, "retorno_saude"),
    "sintoma_informado": _todos(H, "retorno_saude"),
    "lexico_saude": _todos(H, "retorno_saude"),
    "pedido_humano": _todos(H, "retorno_pedido_humano"),
    "lexico_humano": _todos(H, "retorno_pedido_humano"),
    "falha_tecnica": _todos(H, "retorno_falha"),
    "handoff": {
        "ausente": (L, "handoff_cobranca", True),
        "helena_viva": (L, "handoff_cobranca", True),
        "lucas_vivo": (L, "handoff_cobranca", False),
        "helena_vencida": (L, "handoff_cobranca", True),
        "lucas_vencido": (L, "handoff_cobranca", True),
    },
    "handoff_lucas_escalou": _todos(H, "lucas_encerrou"),
    "greeting": {
        "ausente": (H, "inicio", False),
        "helena_viva": (H, "retorno_saude", False),
        "lucas_vivo": (L, "continua_lucas", False),
        "helena_vencida": (H, "retorno_inatividade", False),
        "lucas_vencido": (H, "retorno_inatividade", False),
    },
    "information": {
        "ausente": (H, "inicio", False),
        "helena_viva": (H, "retorno_saude", False),
        "lucas_vivo": (L, "continua_lucas", False),
        "helena_vencida": (H, "retorno_inatividade", False),
        "lucas_vencido": (H, "retorno_inatividade", False),
    },
    "information_lucas_escalou": {
        "ausente": (H, "inicio", False),
        "helena_viva": (H, "retorno_saude", False),
        "lucas_vivo": (H, "lucas_encerrou", False),
        "helena_vencida": (H, "retorno_inatividade", False),
        "lucas_vencido": (H, "retorno_inatividade", False),
    },
    "outside_channel": {
        "ausente": (H, "inicio", False),
        "helena_viva": (H, "retorno_saude", False),
        "lucas_vivo": (H, "retorno_fora_do_canal", False),
        "helena_vencida": (H, "retorno_inatividade", False),
        "lucas_vencido": (H, "retorno_inatividade", False),
    },
    "scheduling": {
        "ausente": (H, "inicio", False),
        "helena_viva": (H, "retorno_saude", False),
        "lucas_vivo": (H, "retorno_fora_do_canal", False),
        "helena_vencida": (H, "retorno_inatividade", False),
        "lucas_vencido": (H, "retorno_inatividade", False),
    },
    "sem_intencao": {
        "ausente": (H, "inicio", False),
        "helena_viva": (H, "retorno_saude", False),
        "lucas_vivo": (H, "retorno_falha", False),
        "helena_vencida": (H, "retorno_inatividade", False),
        "lucas_vencido": (H, "retorno_inatividade", False),
    },
    "saude_e_humano": _todos(H, "retorno_saude"),
    "handoff_com_lexico_saude": _todos(H, "retorno_saude"),
    "handoff_com_lexico_humano": _todos(H, "retorno_pedido_humano"),
    "handoff_com_falha": _todos(H, "retorno_falha"),
}


def test_a_tabela_de_verdade_cobre_todo_estado_e_todo_evento() -> None:
    assert set(ESPERADO) == set(EVENTOS)
    for evento, por_estado in ESPERADO.items():
        assert set(por_estado) == set(ESTADOS), evento
    motivos_cobertos = {motivo for por_estado in ESPERADO.values() for _, motivo, _ in por_estado.values()}
    assert motivos_cobertos == set(TRANSICOES), "todo token de transicao tem pelo menos uma celula"


@pytest.mark.parametrize(("evento", "estado"), list(itertools.product(EVENTOS, ESTADOS)))
def test_tabela_de_verdade(evento: str, estado: str) -> None:
    decisao = decidir_transicao(ESTADOS[estado], EVENTOS[evento], agora=_AGORA)
    assert (decisao.agente_ativo, decisao.transicao_motivo, decisao.enviar_frase_passagem) == ESPERADO[
        evento
    ][estado]


def test_handoff_leva_subtipo_e_competencia_do_handoff() -> None:
    decisao = decidir_transicao(None, EVENTOS["handoff"], agora=_AGORA)
    assert (decisao.lucas_cobranca_subtipo, decisao.lucas_competencia) == ("contestacao", "2026-08")


def test_continua_lucas_preserva_as_colunas_do_lucas() -> None:
    decisao = decidir_transicao(ESTADOS["lucas_vivo"], EVENTOS["greeting"], agora=_AGORA)
    assert (decisao.lucas_cobranca_subtipo, decisao.lucas_competencia) == ("boleto_2via", "2026-09")


def test_toda_decisao_para_a_helena_zera_as_colunas_do_lucas() -> None:
    for evento, estado in itertools.product(EVENTOS.values(), ESTADOS.values()):
        decisao = decidir_transicao(estado, evento, agora=_AGORA)
        if decisao.agente_ativo == "helena":
            assert decisao.lucas_cobranca_subtipo is None and decisao.lucas_competencia is None


def test_vencimento_e_estrito_expira_em_igual_a_agora_ainda_vale() -> None:
    linha = ESTADOS["lucas_vivo"]
    assert linha is not None
    no_limite = _substituir(linha, expira_em=_AGORA)
    assert (
        decidir_transicao(no_limite, EVENTOS["greeting"], agora=_AGORA).transicao_motivo == "continua_lucas"
    )
    vencida = _substituir(linha, expira_em=_AGORA - timedelta(microseconds=1))
    assert (
        decidir_transicao(vencida, EVENTOS["greeting"], agora=_AGORA).transicao_motivo
        == "retorno_inatividade"
    )


def _substituir(linha: LinhaAgenteAtivo, **mudancas: Any) -> LinhaAgenteAtivo:
    return dataclasses.replace(linha, **mudancas)


# ---------------------------------------------------------------------------------------
# A regra estrutural de §2.3 sobre a grade inteira de entradas.
# ---------------------------------------------------------------------------------------
_INTENCOES = (
    None,
    "symptom",
    "scheduling",
    "information",
    "human_request",
    "clinical_question",
    "greeting",
    "outside_channel",
)
_MOTIVOS = (
    None,
    "red_flag_clinico",
    "risco_psicossocial",
    "intencao_clinica",
    "solicitacao_humano",
    "falha_tecnica",
)


def _grade() -> list[EventoDoTurno]:
    eventos = []
    for (
        intent,
        motivo,
        handoff,
        coleta,
        falha,
        risco,
        sintoma,
        lex_h,
        lex_s,
        lucas_escalou,
    ) in itertools.product(
        _INTENCOES,
        _MOTIVOS,
        (None, _HANDOFF),
        (False, True),
        (False, True),
        (False, True),
        (None, "dor_toracica"),
        (False, True),
        (False, True),
        (False, True),
    ):
        eventos.append(
            EventoDoTurno(
                intent=intent,
                escalation_motivo=motivo,
                abriu_coleta=coleta,
                falha_tecnica=falha,
                psychosocial_risk=risco,
                sintoma_codigo=sintoma,
                pedido_humano_lexico=lex_h,
                sinal_saude_lexico=lex_s,
                handoff=handoff,
                lucas_escalou=lucas_escalou,
            )
        )
    return eventos


def test_so_handoff_tipado_ou_lucas_vivo_continuando_levam_ao_lucas() -> None:
    """§2.3: a camada deterministica so' puxa para a Helena; o Lucas exige o handoff DESTE turno
    (ou o Lucas ja' vivo e a conversa seguindo nele). Saude, pedido de pessoa e falha nunca vao
    para o Lucas, em nenhuma combinacao."""
    for evento in _grade():
        for nome, estado in ESTADOS.items():
            decisao = decidir_transicao(estado, evento, agora=_AGORA)
            assert decisao.transicao_motivo in TRANSICOES
            if decisao.agente_ativo != "lucas":
                continue
            assert not evento.tem_sinal_de_saude, (nome, evento)
            assert not evento.tem_pedido_humano, (nome, evento)
            assert not evento.falha_tecnica, (nome, evento)
            assert not evento.lucas_escalou, (nome, evento)
            assert evento.handoff is not None or (
                nome == "lucas_vivo" and evento.intent in {"greeting", "information"}
            ), (nome, evento)


def test_a_frase_de_passagem_so_sai_quando_a_conversa_entra_no_lucas() -> None:
    for evento in _grade():
        for nome, estado in ESTADOS.items():
            decisao = decidir_transicao(estado, evento, agora=_AGORA)
            if decisao.enviar_frase_passagem:
                assert decisao.agente_ativo == "lucas" and nome != "lucas_vivo"


# ---------------------------------------------------------------------------------------
# Tipos e traducao do resultado da Helena.
# ---------------------------------------------------------------------------------------
def test_os_dominios_do_codigo_sao_os_mesmos_dos_checks_da_migration() -> None:
    fonte = _MIGRATION.read_text(encoding="utf-8")
    bloco_transicao = re.search(r"transicao_motivo IN \((.*?)\)\)", fonte, re.DOTALL)
    bloco_subtipo = re.search(r"lucas_cobranca_subtipo IN \((.*?)\)\)", fonte, re.DOTALL)
    assert bloco_transicao and bloco_subtipo
    assert tuple(re.findall(r"'([a-z_0-9]+)'", bloco_transicao.group(1))) == TRANSICOES
    assert tuple(re.findall(r"'([a-z_0-9]+)'", bloco_subtipo.group(1))) == COBRANCA_SUBTIPOS


@pytest.mark.parametrize("competencia", ["2026-13", "2026-00", "26-01", "2026-1", "setembro"])
def test_handoff_recusa_competencia_fora_do_formato(competencia: str) -> None:
    with pytest.raises(ValueError):
        PedidoDeHandoff(cobranca_subtipo="vencimento", competencia=competencia)


def test_handoff_recusa_subtipo_fora_do_dominio() -> None:
    with pytest.raises(ValueError):
        PedidoDeHandoff(cobranca_subtipo="estorno")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("resultado", "campo", "esperado"),
    [
        ({"escalation_motivo": "falha_tecnica"}, "falha_tecnica", True),
        ({"start_failed": True}, "falha_tecnica", True),
        ({"response_kind": "falha_tecnica_start"}, "falha_tecnica", True),
        ({"error": "recusa:promessa_de_humano", "response_kind": "inform"}, "falha_tecnica", False),
        ({"response_kind": "collect"}, "abriu_coleta", True),
        ({"psychosocial_risk": True}, "psychosocial_risk", True),
        ({"sintoma_codigo": ""}, "sintoma_codigo", None),
        ({"intent": 7}, "intent", None),
    ],
)
def test_evento_le_so_chaves_de_saida_tipadas(resultado: dict[str, Any], campo: str, esperado: Any) -> None:
    assert getattr(evento_do_resultado_da_helena(resultado, None), campo) == esperado


def test_evento_nunca_le_o_texto() -> None:
    """O texto da mensagem e o da resposta podem estar no resultado; o evento nao os carrega."""
    resultado = {
        "message_body": "dor no peito",
        "response_text": "procure um atendente",
        "intent": "greeting",
    }
    evento = evento_do_resultado_da_helena(resultado, None)
    assert not evento.tem_sinal_de_saude and not evento.tem_pedido_humano


# ---------------------------------------------------------------------------------------
# O roteador sobre o dublê: CAS, sombra e purga.
# ---------------------------------------------------------------------------------------
def _lexicos() -> pre_roteamento.PreRoteamento:
    return pre_roteamento.carregar()


def _roteador(store: FakeAgenteAtivoStore, **extra: Any) -> ConversaRouter:
    relogio = extra.pop("relogio", lambda: _AGORA)
    return ConversaRouter(
        tenant_id="amh",
        store=store,
        lexicos=_lexicos(),
        inatividade=timedelta(minutes=60),
        relogio=relogio,
        **extra,
    )


async def test_primeira_mensagem_grava_helena_inicio_com_expira_em() -> None:
    store = FakeAgenteAtivoStore()
    decisao = await _roteador(store).registrar_turno(
        conversation_id=_CONVERSA, sinais=None, resultado_helena={"intent": "greeting"}
    )
    assert decisao == Decisao(agente_ativo="helena", transicao_motivo="inicio")
    linha = store.linhas[_CONVERSA]
    assert (linha.revisao, linha.ativo_desde, linha.ultimo_turno_em) == (0, _AGORA, _AGORA)
    assert linha.expira_em == _AGORA + timedelta(minutes=60)


async def test_segunda_mensagem_avanca_a_revisao_e_preserva_ativo_desde() -> None:
    store = FakeAgenteAtivoStore()
    instantes = iter([_AGORA, _AGORA + timedelta(minutes=5)])
    roteador = _roteador(store, relogio=lambda: next(instantes))
    for _ in range(2):
        await roteador.registrar_turno(
            conversation_id=_CONVERSA, sinais=None, resultado_helena={"intent": "greeting"}
        )
    linha = store.linhas[_CONVERSA]
    assert linha.revisao == 1
    assert linha.ativo_desde == _AGORA
    assert linha.ultimo_turno_em == _AGORA + timedelta(minutes=5)


async def test_cas_concorrente_uma_escrita_vence_a_outra_rele_e_recalcula() -> None:
    """Duas replicas recebem mensagens da MESMA conversa ao mesmo tempo. As duas leem a linha
    ausente antes de qualquer uma gravar; so' um INSERT vence, e a outra rele' a linha recem
    gravada e grava por cima com a revisao certa. Nenhuma escrita se perde em silencio."""
    store = FakeAgenteAtivoStore()
    store.barreira_de_leitura = asyncio.Barrier(2)
    a, b = _roteador(store), _roteador(store)
    resultados = await asyncio.gather(
        a.registrar_turno(conversation_id=_CONVERSA, sinais=None, resultado_helena={"intent": "greeting"}),
        b.registrar_turno(
            conversation_id=_CONVERSA,
            sinais=None,
            resultado_helena={"intent": "symptom", "response_kind": "collect"},
        ),
    )
    assert store.cas_perdidos == 1
    assert store.gravacoes == 2
    assert store.linhas[_CONVERSA].revisao == 1
    # Qualquer que seja a ordem, o perdedor recalculou SOBRE a linha do vencedor: se a saudacao
    # venceu, a coleta grava `retorno_saude` por cima; se a coleta venceu, a saudacao rele' a
    # Helena viva em `retorno_saude` e PRESERVA o motivo (nunca volta para `inicio`).
    assert store.linhas[_CONVERSA].transicao_motivo == "retorno_saude"
    assert {r.transicao_motivo for r in resultados} <= {"inicio", "retorno_saude"}
    assert "retorno_saude" in {r.transicao_motivo for r in resultados}


async def test_cas_perdido_em_toda_tentativa_levanta_sem_gravar() -> None:
    class _SempreConcorrida(FakeAgenteAtivoStore):
        async def gravar(self, linha: LinhaAgenteAtivo, *, revisao_esperada: int | None) -> bool:
            self.cas_perdidos += 1
            return False

    store = _SempreConcorrida()
    with pytest.raises(ConflitoDeRevisaoError):
        await _roteador(store, max_tentativas=3).registrar_turno(
            conversation_id=_CONVERSA, sinais=None, resultado_helena={}
        )
    assert store.cas_perdidos == 3 and store.linhas == {}


async def test_em_sombra_uma_decisao_para_o_lucas_vira_helena() -> None:
    """Onda (c): o Lucas nao tem entrada. Gravar `lucas` seria um fato inventado na tabela."""
    store = FakeAgenteAtivoStore()
    decisao = await _roteador(store).registrar_turno(
        conversation_id=_CONVERSA, sinais=None, resultado_helena={}, handoff=_HANDOFF
    )
    assert decisao == Decisao(agente_ativo="helena", transicao_motivo="retorno_falha")
    assert store.linhas[_CONVERSA].agente_ativo == "helena"


async def test_com_o_lucas_disponivel_o_handoff_grava_lucas() -> None:
    store = FakeAgenteAtivoStore()
    decisao = await _roteador(store, lucas_disponivel=True).registrar_turno(
        conversation_id=_CONVERSA, sinais=None, resultado_helena={}, handoff=_HANDOFF
    )
    assert (decisao.agente_ativo, decisao.enviar_frase_passagem) == ("lucas", True)
    assert store.linhas[_CONVERSA].lucas_cobranca_subtipo == "contestacao"


async def test_os_sinais_lexicos_entram_no_evento() -> None:
    store = FakeAgenteAtivoStore()
    roteador = _roteador(store)
    sinais = roteador.pre_rotear("quero falar com um atendente agora")
    decisao = await roteador.registrar_turno(
        conversation_id=_CONVERSA, sinais=sinais, resultado_helena={"intent": "information"}
    )
    assert decisao.transicao_motivo == "retorno_pedido_humano"


async def test_purga_no_maximo_uma_vez_por_intervalo_com_lote_e_idade() -> None:
    store = FakeAgenteAtivoStore()
    velha = _substituir(
        _linha("helena", "inicio"),
        conversation_id="wa:amh:hk1_ffff",
        ultimo_turno_em=_AGORA - timedelta(days=31),
        expira_em=_AGORA - timedelta(days=31) + timedelta(minutes=60),
    )
    store.linhas[velha.conversation_id] = velha
    marcas = iter([0.0, 300.0, 601.0])
    roteador = _roteador(store, relogio_monotonico=lambda: next(marcas))
    for _ in range(3):
        await roteador.registrar_turno(conversation_id=_CONVERSA, sinais=None, resultado_helena={})
    assert store.purgas == [(_AGORA - timedelta(days=30), 100), (_AGORA - timedelta(days=30), 100)]
    assert "wa:amh:hk1_ffff" not in store.linhas
    assert _CONVERSA in store.linhas


async def test_purga_que_falha_nao_derruba_o_registro() -> None:
    class _PurgaQuebrada(FakeAgenteAtivoStore):
        async def purgar_antigas(self, *, antes_de: datetime, limite: int) -> int:
            raise OSError("banco fora")

    store = _PurgaQuebrada()
    decisao = await _roteador(store).registrar_turno(
        conversation_id=_CONVERSA, sinais=None, resultado_helena={}
    )
    assert decisao.agente_ativo == "helena" and _CONVERSA in store.linhas


@pytest.mark.parametrize("inatividade", [timedelta(0), timedelta(minutes=-1)])
def test_roteador_recusa_inatividade_nao_positiva(inatividade: timedelta) -> None:
    with pytest.raises(ValueError):
        ConversaRouter(
            tenant_id="amh", store=FakeAgenteAtivoStore(), lexicos=_lexicos(), inatividade=inatividade
        )


# ---------------------------------------------------------------------------------------
# O adaptador Postgres recusa conversa de outro tenant ANTES de qualquer SQL.
# ---------------------------------------------------------------------------------------
class _PoolQueExplode:
    def acquire(self) -> Any:
        raise AssertionError("nao devia chegar ao banco")


async def test_store_postgres_recusa_conversa_de_outro_tenant_sem_tocar_no_banco() -> None:
    store = PostgresAgenteAtivoStore(dsn="postgresql://x:y@localhost/z", tenant="amh", pool=_PoolQueExplode())
    with pytest.raises(RoteamentoError):
        await store.ler("wa:outro:hk1_0123")
    linha = _substituir(_linha("helena", "inicio"), conversation_id="wa:outro:hk1_0123")
    with pytest.raises(RoteamentoError):
        await store.gravar(linha, revisao_esperada=None)


def test_store_postgres_recusa_tenant_que_nao_e_identificador() -> None:
    with pytest.raises(ValueError):
        PostgresAgenteAtivoStore(dsn="postgresql://x:y@localhost/z", tenant='amh"; drop')
