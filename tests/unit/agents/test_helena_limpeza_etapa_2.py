"""LIMPEZA DA SAUDE, ETAPA 2 (dono, 02/11/2026; DPO, 27/10/2026): TODAS as conversas pela porta unica.

Com a rota global para todas as conversas (`MAEZO_HELENA_ROTA_GLOBAL_TODAS`) e SEM lista nenhuma:

- uma conversa que nao esta' em lista nenhuma passa pela porta (o modelo so' LE, a tabela DMN decide);
- a saude nunca e' decidida pelo `classify` antigo: nem com dado pessoal na mensagem (trava), nem com o
  roteador sem decisao, nem numa habilidade fora da saude. As camadas antigas (base, v4, alarme por palavra,
  sinal claro, neonatal, triagem, gerativa, protocolos, alarme do modelo) LEVANTAM se chamadas;
- o que nao e' saude (boleto, carteirinha) segue igual;
- com a chave desligada e sem listas, nada muda.

Tabelas DMN REAIS (FIRST-hit), modelo e motor falsos; nenhuma rede. Sem dado pessoal real."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest

from maezo.agents.helena import graph as graph_module
from maezo.agents.helena import modelo_allowlist as ma
from maezo.agents.helena import roteador as rt
from maezo.agents.helena import travas_entrada as te
from maezo.agents.helena.graph import (
    RESPOSTA_ESCALONAMENTO_URGENTE,
    RESPOSTA_HANDOFF_SAUDE,
    HelenaGraph,
)
from maezo.agents.helena.habilidades.saude_original import fluxo as so
from maezo.runtime.inference.rota_global import RotaGlobal
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport
from tests.support.audit_fakes import FakeStartAuditSink
from tests.support.roteador_v7_fake import decisao_v7
from tests.unit.agents import test_helena_saude_original as so_tests
from tests.unit.agents.test_helena_rodada_3_simulador import _entrada, _WhatsApp
from tests.unit.agents.test_helena_saude_gerativa import _Conversa

#: Uma conversa keyed que NAO esta' em lista nenhuma.
_FORA_DAS_LISTAS = "wa:amh:hk1_" + "7" * 64
_CPF_FALSO = "123.456.789-09"  # numero inventado (sem dado real)


def _classify(**campos: Any) -> str:
    dados: dict[str, Any] = {
        "intent": "information",
        "population": "none",
        "psychosocial_risk": False,
        "sintoma_codigo": None,
        "intensidade": "desconhecida",
    }
    return json.dumps(dados | campos)


class _Modelo:
    """O roteador e o classificador de sempre (GLM), FALSOS: decisoes do roteador e extracoes em fila."""

    def __init__(self, decisoes: list[str] | None = None, extracoes: list[str] | None = None) -> None:
        self.decisoes = list(decisoes or [])
        self.extracoes = list(extracoes or [])
        self.chamadas_do_roteador = 0
        self.chamadas_do_classify = 0
        self.model_id = "fake"

    async def generate(self, prompt: str, **_: Any) -> str:
        if "roteador de um assistente" in prompt:
            self.chamadas_do_roteador += 1
            return self.decisoes.pop(0) if self.decisoes else ""
        if '"psychosocial_risk"' in prompt:
            self.chamadas_do_classify += 1
            return self.extracoes.pop(0) if self.extracoes else _classify()
        return "Resumo do caso para a equipe."


def _grafo(
    modelo: _Modelo, leitor: so_tests._Leitor, *, todas: bool = True
) -> tuple[Any, _WhatsApp, FakeCibSevenTransport]:
    """O dev de hoje (v4, base, historico, roteador ativo, porta ligada), mas SEM lista de conversas: so' a
    chave `todas` da rota global."""
    whatsapp, cib = _WhatsApp(), FakeCibSevenTransport()
    grafo = HelenaGraph(
        inference=modelo,  # type: ignore[arg-type]
        dmn=so_tests._DmnReal(),  # type: ignore[arg-type]
        cibseven=cib,
        audit_sink=FakeStartAuditSink(),
        whatsapp=whatsapp,
        roteador_lucas_enabled=True,
        historico_enabled=True,
        helena_v4_enabled=True,
        helena_base_saude_enabled=True,
        base_saude=_Conversa().base,
        relogio=lambda: datetime(2026, 11, 2, 15, 0, tzinfo=UTC),
        helena_roteador="ativo",
        helena_roteador_conversas=frozenset(),
        helena_saude_original_enabled=True,
        rota_global=RotaGlobal(transporte=leitor, todas=todas),
    )
    return grafo.compile_graph().compile(), whatsapp, cib


def _de_fora(mensagem: str) -> dict[str, Any]:
    return {**_entrada(mensagem), "conversation_id": _FORA_DAS_LISTAS}


def _casos(cib: FakeCibSevenTransport) -> list[dict[str, Any]]:
    return [cib._variables[k] for k in cib._history]


@pytest.fixture(autouse=True)
def _camadas_antigas_nunca(monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest) -> None:
    """Nenhuma camada antiga de saude pode decidir (fora o teste da chave desligada)."""
    if "chave_desligada" in request.node.name:
        return

    async def proibido(*_: Any, **__: Any) -> Any:
        raise AssertionError("camada antiga de saude chamada com a porta unica para todas as conversas")

    def proibido_sincrono(*_: Any, **__: Any) -> Any:
        raise AssertionError("trava antiga de saude chamada com a porta unica para todas as conversas")

    for nome in (
        "_base_antes_dos_gatilhos",
        "_v4_antes_dos_gatilhos",
        "_triar_red_flag",
        "_avaliar_suficiencia",
        "_gerativa_informar",
        "_triagem_v2_decidir",
    ):
        monkeypatch.setattr(HelenaGraph, nome, proibido)
    for nome in (
        "_v4_sintoma_leve",
        "_base_dor_sem_alarme",
        "_saude_resposta_a_oferta",
        "_caso_mantido_se_alarme_ja_tratado",
        "_sinal_claro_pelo_texto",
        "_roteador_alarme",
    ):
        monkeypatch.setattr(HelenaGraph, nome, proibido_sincrono)
    monkeypatch.setattr(te, "alarme_afirmado", proibido_sincrono)
    monkeypatch.setattr(te, "sinal_claro_afirmado", proibido_sincrono)
    monkeypatch.setattr(graph_module, "_alerta_neonatal", proibido_sincrono)
    monkeypatch.setattr(rt, "_alarme_do_modelo", proibido_sincrono)
    monkeypatch.setattr(rt, "_escalar_alarme_do_modelo", proibido)
    for nome in ("conduzir", "responder_alerta", "sem_roteador", "sem_alerta_geral"):
        monkeypatch.setattr(rt._protocolo, nome, proibido)


# =================================================================================================
# A chave
# =================================================================================================


def test_a_chave_autoriza_toda_conversa_keyed_e_nunca_um_id_fora_do_formato() -> None:
    todas = RotaGlobal(transporte=object(), todas=True)
    assert todas.autorizada(_FORA_DAS_LISTAS)
    assert not todas.autorizada("wa:amh:hk1_simulador3")  # fora do formato keyed: nunca
    assert not todas.autorizada(None)
    lista = RotaGlobal(transporte=object(), conversas=frozenset({_FORA_DAS_LISTAS}))
    assert lista.autorizada(_FORA_DAS_LISTAS) and not lista.autorizada("wa:amh:hk1_" + "8" * 64)


def test_a_chave_vem_desligada_nas_settings_e_liga_a_rota_sem_lista() -> None:
    from maezo.platform.webhooks.service import _rota_global
    from maezo.platform.webhooks.whatsapp.settings import WhatsAppWebhookSettings

    campo = WhatsAppWebhookSettings.model_fields["helena_rota_global_todas"]
    assert campo.default is False
    base = {"WHATSAPP_APP_SECRET": "s", "WHATSAPP_VERIFY_TOKEN": "v"}
    assert _rota_global(WhatsAppWebhookSettings(**base)) is None  # type: ignore[arg-type]
    ligada = WhatsAppWebhookSettings(**base, MAEZO_HELENA_ROTA_GLOBAL_TODAS="true")  # type: ignore[arg-type]
    rota = _rota_global(ligada)
    assert isinstance(rota, RotaGlobal) and rota.todas and rota.conversas == frozenset()


# =================================================================================================
# (i)-(iii) Conversa fora das listas pela porta
# =================================================================================================


async def test_conversa_fora_das_listas_passa_pela_porta() -> None:
    leitor = so_tests._Leitor(so_tests._leitura(idade_anos=40))
    modelo = _Modelo([decisao_v7("sintoma")])
    grafo, whatsapp, cib = _grafo(modelo, leitor)
    await grafo.ainvoke(_de_fora("estou com dor de cabeca desde ontem"))
    assert modelo.chamadas_do_roteador == 1 and leitor.chamadas == 1
    assert [c["motivo_categoria"] for c in _casos(cib)] == ["intencao_clinica"]
    assert whatsapp.enviados[-1] == RESPOSTA_HANDOFF_SAUDE


async def test_dor_no_peito_e_falta_de_ar_e_192_e_caso_grave_pela_tabela() -> None:
    leitor = so_tests._Leitor(
        so_tests._leitura(sintoma_codigo="dor_toracica", intensidade="moderada", idade_anos=50)
    )
    grafo, whatsapp, cib = _grafo(_Modelo([decisao_v7("saude")]), leitor)
    estado = await grafo.ainvoke(_de_fora("estou com dor no peito e falta de ar"))
    assert whatsapp.enviados[-1] == RESPOSTA_ESCALONAMENTO_URGENTE
    assert [(c["motivo_categoria"], c["severidade"]) for c in _casos(cib)] == [("red_flag_clinico", "grave")]
    assert str(estado.get("dmn_table")) == "triage_redflag_adult"


async def test_o_que_e_psa_vai_para_a_equipe_sem_texto_da_base() -> None:
    leitor = so_tests._Leitor(so_tests._leitura(assunto="duvida_saude"))
    modelo = _Modelo([decisao_v7("saude")])
    grafo, whatsapp, cib = _grafo(modelo, leitor)
    await grafo.ainvoke(_de_fora("o que é PSA?"))
    assert whatsapp.enviados[-1] == RESPOSTA_HANDOFF_SAUDE
    assert "psa" not in whatsapp.enviados[-1].lower()
    assert [(c["motivo_categoria"], c["severidade"]) for c in _casos(cib)] == [
        ("intencao_clinica", "moderada")
    ]
    assert modelo.chamadas_do_classify == 0


# =================================================================================================
# (iv) Dado pessoal na mensagem (trava): a porta, nunca o classify antigo
# =================================================================================================


async def test_cpf_com_sintoma_vai_a_porta_e_nao_ao_classify_antigo() -> None:
    leitor = so_tests._Leitor(
        so_tests._leitura(sintoma_codigo="dor_toracica", intensidade="grave", idade_anos=55)
    )
    modelo = _Modelo()
    grafo, whatsapp, cib = _grafo(modelo, leitor)
    await grafo.ainvoke(_de_fora(f"meu cpf e {_CPF_FALSO} e estou com dor no peito"))
    assert modelo.chamadas_do_roteador == 0, "a trava de dados pessoais nao chama o roteador"
    assert modelo.chamadas_do_classify == 0
    assert whatsapp.enviados[-1] == RESPOSTA_ESCALONAMENTO_URGENTE
    assert [c["severidade"] for c in _casos(cib)] == ["grave"]


async def test_cpf_com_sintoma_e_leitura_que_falha_vai_para_a_equipe_sem_ler_de_novo() -> None:
    leitor = so_tests._Leitor("nao sei", "de novo nao")
    modelo = _Modelo(
        extracoes=[_classify(intent="symptom", population="adult", sintoma_codigo="dor_toracica")]
    )
    grafo, whatsapp, cib = _grafo(modelo, leitor)
    await grafo.ainvoke(_de_fora(f"meu cpf e {_CPF_FALSO} e estou com dor no peito"))
    assert leitor.chamadas == 2, "uma leitura (duas tentativas) no turno inteiro"
    assert [(c["motivo_categoria"], c["severidade"]) for c in _casos(cib)] == [
        ("intencao_clinica", "moderada")
    ]
    assert whatsapp.enviados[-1] == RESPOSTA_HANDOFF_SAUDE


# =================================================================================================
# (v) Roteador sem decisao
# =================================================================================================


async def test_roteador_sem_decisao_e_mensagem_de_saude_termina_na_porta() -> None:
    leitor = so_tests._Leitor(
        so_tests._leitura(sintoma_codigo="dispneia", intensidade="grave", idade_anos=30)
    )
    grafo, whatsapp, cib = _grafo(_Modelo(["nao e json"]), leitor)
    await grafo.ainvoke(_de_fora("estou com muita falta de ar agora"))
    assert whatsapp.enviados[-1] == RESPOSTA_ESCALONAMENTO_URGENTE
    assert [c["severidade"] for c in _casos(cib)] == ["grave"]


async def test_roteador_sem_decisao_leitura_outro_e_classify_de_saude_e_uma_pessoa() -> None:
    # A leitura disse "nao e' saude" e o classificador afirma sintoma, sem o roteador: na duvida, uma pessoa
    # (caso para a equipe pela porta, sem 192 e sem nenhuma regra antiga).
    leitor = so_tests._Leitor(so_tests._leitura(assunto="outro"))
    modelo = _Modelo(
        ["nao e json"], [_classify(intent="symptom", population="adult", sintoma_codigo="febre")]
    )
    grafo, whatsapp, cib = _grafo(modelo, leitor)
    await grafo.ainvoke(_de_fora("to com febre e calafrio"))
    assert leitor.chamadas == 1 and modelo.chamadas_do_classify == 1
    assert [(c["motivo_categoria"], c["severidade"]) for c in _casos(cib)] == [
        ("intencao_clinica", "moderada")
    ]
    assert whatsapp.enviados[-1] == RESPOSTA_HANDOFF_SAUDE


# =================================================================================================
# (vi) Fora da saude: igual
# =================================================================================================


async def test_boleto_segue_para_o_lucas_sem_leitura_da_porta() -> None:
    leitor = so_tests._Leitor()
    modelo = _Modelo([decisao_v7("cobranca")], [_classify(intent="cobranca", cobranca_subtipo="valor")])
    grafo, _whatsapp, cib = _grafo(modelo, leitor)
    estado = await grafo.ainvoke(_de_fora("qual o valor do meu boleto?"))
    assert leitor.chamadas == 0 and cib._history == {}
    assert estado.get("intent") == "cobranca"


async def test_carteirinha_segue_no_plano_sem_leitura_da_porta() -> None:
    leitor = so_tests._Leitor()
    modelo = _Modelo([decisao_v7("plano")], [_classify()])
    grafo, _whatsapp, cib = _grafo(modelo, leitor)
    estado = await grafo.ainvoke(_de_fora("minha carteirinha"))
    assert leitor.chamadas == 0 and cib._history == {}
    assert estado.get("escalation_motivo") is None


async def test_habilidade_fora_da_saude_com_classify_de_sintoma_vale_a_leitura_da_porta() -> None:
    # O roteador disse `plano` e a leitura disse "nao e' saude": o sintoma que o classificador trouxe (do
    # historico) nao abre caso nem passa por regra antiga.
    leitor = so_tests._Leitor(so_tests._leitura(assunto="outro"))
    modelo = _Modelo([decisao_v7("plano")], [_classify(intent="symptom", sintoma_codigo="febre")])
    grafo, _whatsapp, cib = _grafo(modelo, leitor)
    await grafo.ainvoke(_de_fora("qual o tipo do meu plano?"))
    assert leitor.chamadas == 1 and cib._history == {}


# =================================================================================================
# (vii) Chave desligada e sem listas: a Helena de antes
# =================================================================================================


async def test_chave_desligada_e_sem_listas_nada_muda() -> None:
    leitor = so_tests._Leitor()
    modelo = _Modelo(
        extracoes=[_classify(intent="symptom", population="adult", sintoma_codigo="dor_toracica")]
    )
    grafo, whatsapp, _cib = _grafo(modelo, leitor, todas=False)
    await grafo.ainvoke(_de_fora("estou com dor no peito forte agora"))
    assert leitor.chamadas == 0, "fora da lista: o roteador so' em sombra, sem porta"
    assert "192" in whatsapp.enviados[-1]


def test_chave_desligada_nao_muda_o_predicado_da_allowlist() -> None:
    grafo = HelenaGraph(
        inference=_Modelo(),  # type: ignore[arg-type]
        dmn=so_tests._DmnReal(),  # type: ignore[arg-type]
        cibseven=FakeCibSevenTransport(),
        audit_sink=FakeStartAuditSink(),
        whatsapp=_WhatsApp(),
        helena_roteador="ativo",
        rota_global=RotaGlobal(transporte=object()),
    )
    assert not ma.na_allowlist(grafo, {"conversation_id": _FORA_DAS_LISTAS})
    assert not so.assume(grafo, {"conversation_id": _FORA_DAS_LISTAS})
