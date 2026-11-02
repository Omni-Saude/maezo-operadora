"""LIMPEZA DA SAUDE, ETAPA 1 (dono, 02/11/2026): o que FICA funciona com a base de saude e a saude gerativa
DESLIGADAS.

- o caso a pedido (encerrar com confirmacao, a lista de casos abertos na recusa) vale sem a chave da base;
- a porta unica, o roteador ativo e o Haiku da allowlist dependem so' da ROTA GLOBAL (`rota_global`), que a
  composicao monta sozinha a partir da lista de conversas;
- a carteirinha nao depende da v4.

Modelo e motor falsos; nenhuma rede. Sem dado pessoal."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from maezo.agents.helena import graph as graph_module
from maezo.agents.helena import modelo_allowlist as ma
from maezo.agents.helena import roteador as rt
from maezo.agents.helena.graph import (
    RESPOSTA_CASO_ENCERRADO,
    RESPOSTA_CONFIRMA_ENCERRAMENTO,
    RESPOSTA_ESCALONAMENTO_URGENTE,
    HelenaGraph,
)
from maezo.runtime.inference.rota_global import RotaGlobal, conversas_autorizadas
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport
from tests.support.audit_fakes import FakeStartAuditSink
from tests.support.roteador_v7_fake import decisao_v7
from tests.unit.agents import test_helena_saude_original as so_tests
from tests.unit.agents.test_helena_base_saude import _BK, _INFO, _PESSOA, _Conversa, _Motor, _texto
from tests.unit.agents.test_helena_rodada_3_simulador import _CONVERSA, _entrada, _WhatsApp

# =================================================================================================
# 1. O caso a pedido sem a base
# =================================================================================================


async def test_recusa_com_caso_aberto_pede_confirmacao_e_encerra_sem_a_base() -> None:
    conversa = _Conversa(chave=False)
    await conversa.diz("Quero falar com uma pessoa", _PESSOA)
    assert conversa.motivos == ["solicitacao_humano"]

    pergunta = await conversa.diz("Não quero falar com ninguém", _INFO)
    assert _texto(pergunta) == RESPOSTA_CONFIRMA_ENCERRAMENTO
    assert conversa.memoria.get("coleta_pendente") == graph_module.PENDENTE_ENCERRAR_CASO

    fim = await conversa.diz("SIM", _INFO)
    assert _texto(fim) == RESPOSTA_CASO_ENCERRADO
    assert conversa.whatsapp.enviados[-1] == RESPOSTA_CASO_ENCERRADO, "o texto fixo passa sem o modelo"
    assert await conversa.cib.find_active_instance(_BK) is None


async def test_pedido_de_encerrar_com_caso_aberto_sem_a_base() -> None:
    motor = _Motor()
    motor.abrir(_BK, motivo_categoria="solicitacao_humano", severidade="leve")
    conversa = _Conversa(chave=False, motor=motor)
    pergunta = await conversa.diz("Pode cancelar o atendimento", _INFO)
    assert _texto(pergunta) == RESPOSTA_CONFIRMA_ENCERRAMENTO
    await conversa.diz("sim", _INFO)
    assert await motor.find_active_instance(_BK) is None


def _grafo_sem_camadas(motor: FakeCibSevenTransport, **extra: Any) -> HelenaGraph:
    """Sem base, sem gerativa, sem v4 e sem coleta: so' o que fica."""
    return HelenaGraph(
        inference=so_tests._Modelo(),  # type: ignore[arg-type]
        dmn=so_tests._DmnReal(),  # type: ignore[arg-type]
        cibseven=motor,
        audit_sink=FakeStartAuditSink(),
        whatsapp=_WhatsApp(),
        **extra,
    )


def test_a_pendencia_de_encerramento_vale_sem_nenhuma_chave() -> None:
    grafo = _grafo_sem_camadas(_Motor())
    estado = dict(_entrada("sim"))
    estado["coleta_pendente"] = graph_module.PENDENTE_ENCERRAR_CASO
    reset = asyncio.run(grafo.receive(estado))  # type: ignore[arg-type]
    assert reset["coleta_pendente"] == graph_module.PENDENTE_ENCERRAR_CASO


async def test_recusa_lida_pelo_roteador_lista_e_encerra_o_caso_sem_a_base() -> None:
    motor = _Motor()
    bk = graph_module.chave_do_escalonamento("amh", _CONVERSA)
    motor.abrir(bk, motivo_categoria="intencao_clinica", severidade="moderada")
    grafo = _grafo_sem_camadas(motor)
    assert grafo._base_saude is False
    saida = await rt._recusa_da_equipe(grafo, _entrada("nao quero falar com ninguem"))  # type: ignore[arg-type]
    assert saida["response_text"] == RESPOSTA_CASO_ENCERRADO
    assert await motor.find_active_instance(bk) is None


# =================================================================================================
# 2. Porta unica, roteador ativo e Haiku so' com a rota global
# =================================================================================================


def _grafo_da_porta(leitor: Any, modo: str = ma.MODO_GLM5) -> tuple[Any, _WhatsApp, FakeCibSevenTransport]:
    whatsapp, cib = _WhatsApp(), FakeCibSevenTransport()
    grafo = HelenaGraph(
        inference=so_tests._Modelo(decisao_v7("sintoma")),  # type: ignore[arg-type]
        dmn=so_tests._DmnReal(),  # type: ignore[arg-type]
        cibseven=cib,
        audit_sink=FakeStartAuditSink(),
        whatsapp=whatsapp,
        roteador_lucas_enabled=True,
        historico_enabled=True,
        relogio=lambda: datetime(2026, 11, 2, 15, 0, tzinfo=UTC),
        rota_global=RotaGlobal(transporte=leitor, conversas=frozenset({_CONVERSA})),
        helena_roteador="ativo",
        helena_roteador_conversas=frozenset({_CONVERSA}),
        helena_saude_original_enabled=True,
        modelo_allowlist=modo,
    )
    assert grafo._base_saude is False and grafo._gerativa is None
    return grafo, whatsapp, cib


async def test_porta_unica_le_e_a_tabela_decide_sem_base_e_sem_gerativa() -> None:
    leitor = so_tests._Leitor(
        so_tests._leitura(sintoma_codigo="dor_toracica", intensidade="moderada", idade_anos=50)
    )
    grafo, whatsapp, cib = _grafo_da_porta(leitor)
    await grafo.compile_graph().compile().ainvoke(_entrada("estou com dor no peito desde cedo"))
    assert leitor.chamadas == 1, "a porta leu pela rota global"
    assert whatsapp.enviados[-1] == RESPOSTA_ESCALONAMENTO_URGENTE
    assert [cib._variables[k]["severidade"] for k in cib._history] == ["grave"]


def test_allowlist_do_modelo_e_o_haiku_so_com_a_rota_global() -> None:
    leitor = so_tests._Leitor()
    grafo, _w, _c = _grafo_da_porta(leitor, ma.MODO_HAIKU)
    dentro = dict(_entrada("oi"))
    fora = dict(_entrada("oi")) | {"conversation_id": "wa:amh:hk1_" + "0" * 64}
    assert grafo._conversa_autorizada(dentro) and ma.na_allowlist(grafo, dentro)
    assert not ma.na_allowlist(grafo, fora)
    haiku = ma.provedor_para(grafo, dentro, ma.PAPEL_CLASSIFY)
    assert isinstance(haiku, ma.ProvedorHaikuGlobal)
    assert ma.provedor_para(grafo, fora, ma.PAPEL_CLASSIFY) is grafo._llm


def test_sem_rota_global_a_allowlist_fica_vazia() -> None:
    grafo = _grafo_sem_camadas(_Motor(), helena_roteador_conversas=frozenset({_CONVERSA}))
    estado = dict(_entrada("oi"))
    assert grafo._rota_global is None
    assert not grafo._conversa_autorizada(estado) and not ma.na_allowlist(grafo, estado)


# =================================================================================================
# 3. A composicao monta a rota sozinha
# =================================================================================================


class _Settings:
    def __init__(self, conversas: str, *, gerativa: bool = False, base: bool = False) -> None:
        self.helena_saude_gerativa_conversas = conversas
        self.helena_saude_gerativa = gerativa
        self.helena_base_saude = base
        self.helena_busca_semantica = False


def test_composicao_monta_a_rota_global_sem_a_base_e_sem_a_gerativa() -> None:
    from maezo.platform.webhooks.service import _rota_global, _saude_gerativa

    conversa = "wa:amh:hk1_" + "a" * 64
    assert _rota_global(_Settings("")) is None  # type: ignore[arg-type]
    rota = _rota_global(_Settings(f"{conversa},lixo"))  # type: ignore[arg-type]
    assert isinstance(rota, RotaGlobal)
    assert rota.conversas == frozenset({conversa}) == conversas_autorizadas(f"{conversa} lixo")
    assert _saude_gerativa(_Settings(conversa), rota) is None  # type: ignore[arg-type]
    # Com as chaves ligadas (o dev de hoje), o redator usa o MESMO transporte da rota.
    redator = _saude_gerativa(_Settings(conversa, gerativa=True, base=True), rota)  # type: ignore[arg-type]
    assert redator.transporte is rota.transporte and redator.conversas == rota.conversas


def test_o_grafo_construido_direto_com_o_redator_herda_a_rota_dele() -> None:
    from maezo.conhecimento import base_saude as bs
    from maezo.conhecimento import saude_gerativa as sg

    transporte = so_tests._Leitor()
    redator = sg.RedatorDeSaude(transporte=transporte, conversas=frozenset({_CONVERSA}))  # type: ignore[arg-type]
    com_base = _grafo_sem_camadas(
        _Motor(), saude_gerativa=redator, helena_base_saude_enabled=True, base_saude=bs.BASE_VAZIA
    )
    assert com_base._rota_global == RotaGlobal(transporte, frozenset({_CONVERSA}))
    # Sem a base o redator nao vale (como antes) e, sem rota injetada, nao ha' rota.
    sem_base = _grafo_sem_camadas(_Motor(), saude_gerativa=redator)
    assert sem_base._gerativa is None and sem_base._rota_global is None
