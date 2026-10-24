"""Roteador de habilidades da Helena em SOMBRA (ADR-0066, DL-0134, F1): só registra, nunca muda a resposta."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Any

import pytest
from structlog.testing import capture_logs

from maezo.agents.helena import roteador as rt
from maezo.agents.helena import roteador_contrato as rc
from maezo.agents.helena import roteador_v7 as rv7
from maezo.agents.helena.graph import PROMPT_VERSIONS, HelenaGraph
from maezo.runtime.prompt_format import render_untrusted_block
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport
from maezo.tools.workers.dmn_transport import FakeDmnTransport
from tests.support.audit_fakes import FakeStartAuditSink
from tests.support.roteador_v7_fake import decisao_v7
from tests.unit.agents.test_helena_rodada_3_simulador import _classify, _entrada, _Fonte, _WhatsApp

_CONTRATO = rc.carregar()


def _json(**campos: Any) -> str:
    return json.dumps(campos)


# --- leitura da decisao (contrato v7, etapa 7) -----
def test_decisao_v7_no_vocabulario_interno() -> None:
    v7, ruins = rv7.ler_decisao(decisao_v7("sintoma", responde=True, sim_nao="sim"))
    assert v7 is not None and ruins == []
    d = rt.decisao_do_v7(v7, "sim_nao", "sintoma")
    assert d.habilidade == "sintoma" and d.responde_a_pendencia and d.v7 is v7
    cobertura, _ = rv7.ler_decisao(decisao_v7("cobertura"))
    assert cobertura is not None and rt.decisao_do_v7(cobertura, None, None).habilidade == "cobertura"


def test_dois_pedidos_que_precisam_de_pergunta_viram_a_duvida() -> None:
    v7, _ = rv7.ler_decisao(decisao_v7("duvida", habilidade_provavel="plano", alternativa="cobranca"))
    assert v7 is not None
    d = rt.decisao_do_v7(v7, None, None)
    assert d.pergunta_na_duvida and d.opcoes_da_duvida() == ("plano", "cobranca")


@pytest.mark.parametrize(
    "bruto",
    [
        "não sei",
        decisao_v7("conversa").replace('"conversa"', '"reembolso"', 1),
        decisao_v7("conversa").replace('"uncertain"', '"certeza"', 1),
        decisao_v7("conversa").replace('"nao_se_aplica"', '"talvez"', 1),
    ],
)
def test_decisao_invalida_nunca_e_adivinhada(bruto: str) -> None:
    decisao, ruins = rv7.ler_decisao(bruto)
    assert decisao is None and ruins


# --- prompt -----
def test_prompt_traz_todas_as_habilidades_a_pendencia_e_os_blocos_nao_confiaveis() -> None:
    prompt = rv7.montar_prompt(
        instrucoes=rv7.INSTRUCOES_PADRAO,
        bloco_conversa=render_untrusted_block("historico_conversa", "pessoa: to com dor nas costas"),
        bloco_mensagens=render_untrusted_block("message_body", "pode ser"),
        tipo_pendente="sim_nao",
        texto_pendente="Neste momento, existe algum destes sinais?",
        fatos={"identificado": True, "alarme_ja_tratado": False, "caso_aberto": "nenhum"},
    )
    for h in rv7.SKILLS:
        assert f"- {h}:" in prompt
    assert 'PENDENTE: (tipo sim_nao) "Neste momento, existe algum destes sinais?"' in prompt
    assert prompt.count("historico_conversa") >= 1 and "pode ser" in prompt


def test_prompt_sem_historico_e_sem_pendencia() -> None:
    prompt = rv7.montar_prompt(
        instrucoes=rv7.INSTRUCOES_PADRAO,
        bloco_conversa="",
        bloco_mensagens=render_untrusted_block("message_body", "oi"),
        tipo_pendente=None,
        texto_pendente=None,
        fatos={},
    )
    assert "PENDENTE: nenhuma" in prompt and "(vazia)" in prompt


# --- travas -----
def test_travas_na_ordem_do_contrato() -> None:
    sim, nao = (lambda _t: True), (lambda _t: False)
    assert rt.trava_de_entrada("x", alarme=sim, risco=sim) == rt.TRAVA_ALARME
    assert rt.trava_de_entrada("x", alarme=nao, risco=sim) == rt.TRAVA_RISCO
    assert rt.trava_de_entrada("meu cpf é 123.456.789-09", alarme=nao, risco=nao) == rt.TRAVA_DADOS
    assert rt.trava_de_entrada("bom dia", alarme=nao, risco=nao) is None


# --- decisao da Helena atual -----
def _atual(final: dict[str, Any], antes: str | None = None) -> frozenset[str]:
    return rt.habilidades_da_helena_atual(
        final, pendente_antes=antes, dona_da_pendencia=_CONTRATO.dona_da_pendencia
    )


@pytest.mark.parametrize(
    ("final", "antes", "esperado"),
    [
        ({"escalation_motivo": "red_flag_clinico"}, None, {rt.TRAVA_ALARME}),
        ({"escalation_motivo": "solicitacao_humano"}, "COB2:QUEM", {"equipe"}),
        ({"intent": "cobranca", "cobranca_subtipo": "cancelamento"}, None, {"pedidos"}),
        ({"intent": "cobranca", "cobranca_subtipo": "boleto_2via"}, None, {"cobranca"}),
        ({"intent": "consulta_plano", "coleta_pendente": "COB2:RESPONDIDO"}, None, {"cobertura"}),
        ({"intent": "consulta_plano", "consulta_subtipo": "dependentes"}, None, {"plano", "cobertura"}),
        ({"intent": "symptom", "coleta_pendente": "V4_ALERTA:GERAL:adult"}, None, {"sintoma"}),
        ({"intent": "information"}, "COB2:RESPONDIDO", {"cobertura", "conversa"}),
        ({"intent": "greeting"}, None, {"conversa"}),
    ],
)
def test_decisao_atual_no_vocabulario_das_habilidades(
    final: dict[str, Any], antes: str | None, esperado: set[str]
) -> None:
    assert _atual(final, antes) == frozenset(esperado)


def test_campos_do_log_so_vocabulario_fechado() -> None:
    decisao = rt.DecisaoDoRoteador("cobertura", "alta", None, True)
    campos = rt.campos_do_log(
        decisao,
        falha=None,
        trava=None,
        atual=frozenset({"cobertura"}),
        pendente=rt.PendenteDoRoteador("cobertura", "COB2:QUEM"),
        ms=812,
        conversa=rt.rotulo_da_conversa("wa:amh:hk1_x"),
    )
    assert campos["concorda"] is True and campos["pendencia"] == "cobertura" and len(campos["conversa"]) == 10
    assert set(campos) == {
        "habilidade", "confianca", "alternativa", "responde_a_pendencia", "trava", "atual", "concorda",
        "pendencia", "falha", "ms", "conversa",
    }  # fmt: skip


def test_a_trava_vale_como_escolha_na_concordancia() -> None:
    campos = rt.campos_do_log(
        rt.DecisaoDoRoteador("sintoma", "alta", None, False),
        falha=None,
        trava=rt.TRAVA_ALARME,
        atual=frozenset({rt.TRAVA_ALARME}),
        pendente=None,
        ms=1,
        conversa="x",
    )
    assert campos["concorda"] is True


def test_prompt_versionado() -> None:
    assert PROMPT_VERSIONS["roteador"] == rt.ROTEADOR_PROMPT_VERSION


# --- no grafo -----
class _ModeloComRoteador:
    """O roteador recebe a resposta fixa dele; o resto das chamadas segue a fila de sempre."""

    def __init__(self, respostas: list[str], roteador: str) -> None:
        self._respostas = list(respostas)
        self._roteador = roteador
        self.do_roteador = 0
        self.model_id = "fake"

    async def generate(self, prompt: str, **_: Any) -> str:
        if "roteador de um assistente" in prompt:
            self.do_roteador += 1
            await asyncio.sleep(0)
            return self._roteador
        return self._respostas.pop(0) if self._respostas else ""


def _grafo(modo: str, modelo: Any) -> tuple[Any, _WhatsApp]:
    whatsapp = _WhatsApp()
    grafo = HelenaGraph(
        inference=modelo,
        dmn=FakeDmnTransport(),
        cibseven=FakeCibSevenTransport(),
        audit_sink=FakeStartAuditSink(),
        whatsapp=whatsapp,
        roteador_lucas_enabled=True,
        historico_enabled=True,
        consultas_plano=_Fonte(),
        relogio=lambda: datetime(2026, 10, 21, 15, 0, tzinfo=UTC),
        helena_roteador=modo,
    )
    return grafo.compile_graph().compile(), whatsapp


async def _turno(
    modo: str, mensagem: str, classificacao: str
) -> tuple[dict[str, Any], _WhatsApp, Any, list[Any]]:
    modelo = _ModeloComRoteador([classificacao], decisao_v7("conversa"))
    grafo, whatsapp = _grafo(modo, modelo)
    with capture_logs() as logs:
        resultado = await grafo.ainvoke(_entrada(mensagem))
        await asyncio.sleep(0.01)
    return resultado, whatsapp, modelo, logs


async def test_sombra_nao_muda_a_resposta_e_registra_a_decisao() -> None:
    desligado, zap_d, modelo_d, logs_d = await _turno("desligado", "bom dia", _classify(intent="greeting"))
    sombra, zap_s, modelo_s, logs_s = await _turno("sombra", "bom dia", _classify(intent="greeting"))
    assert zap_s.enviados == zap_d.enviados
    assert sombra.get("response_text") == desligado.get("response_text")
    assert modelo_d.do_roteador == 0 and modelo_s.do_roteador == 1
    assert not [e for e in logs_d if e.get("event") == "helena_roteador_sombra"]
    [evento] = [e for e in logs_s if e.get("event") == "helena_roteador_sombra"]
    assert evento["habilidade"] == "conversa" and evento["concorda"] is True and evento["falha"] is None
    assert "bom dia" not in json.dumps(evento, default=str)


async def test_roteador_que_falha_so_vira_campo_no_log() -> None:
    class _Quebrado(_ModeloComRoteador):
        async def generate(self, prompt: str, **kw: Any) -> str:
            if "roteador de um assistente" in prompt:
                raise TimeoutError
            return await super().generate(prompt, **kw)

    modelo = _Quebrado([_classify(intent="greeting")], "")
    grafo, whatsapp = _grafo("sombra", modelo)
    with capture_logs() as logs:
        await grafo.ainvoke(_entrada("bom dia"))
        await asyncio.sleep(0.01)
    assert whatsapp.enviados
    [evento] = [e for e in logs if e.get("event") == "helena_roteador_sombra"]
    assert evento["falha"] == rt.FALHA_PRAZO and evento["habilidade"] is None


# --- orcamento (21/10/2026) -----
async def test_roteador_usa_o_provedor_proprio_quando_ha() -> None:
    conversa = _ModeloComRoteador([_classify(intent="greeting")], decisao_v7("conversa"))
    proprio = _ModeloComRoteador([], decisao_v7("conversa"))
    whatsapp = _WhatsApp()
    grafo = (
        HelenaGraph(
            inference=conversa,  # type: ignore[arg-type]
            dmn=FakeDmnTransport(),
            cibseven=FakeCibSevenTransport(),
            audit_sink=FakeStartAuditSink(),
            whatsapp=whatsapp,
            roteador_lucas_enabled=True,
            historico_enabled=True,
            consultas_plano=_Fonte(),
            relogio=lambda: datetime(2026, 10, 21, 15, 0, tzinfo=UTC),
            helena_roteador="sombra",
            roteador_inference=proprio,  # type: ignore[arg-type]
        )
        .compile_graph()
        .compile()
    )
    await grafo.ainvoke(_entrada("bom dia"))
    await asyncio.sleep(0.01)
    assert proprio.do_roteador == 1 and conversa.do_roteador == 0


async def test_limite_de_concorrencia_cheio_segue_sem_o_roteador() -> None:
    grafo = HelenaGraph(
        inference=_ModeloComRoteador([], ""),  # type: ignore[arg-type]
        dmn=FakeDmnTransport(),
        cibseven=FakeCibSevenTransport(),
        audit_sink=FakeStartAuditSink(),
        whatsapp=_WhatsApp(),
        helena_roteador="sombra",
        roteador_concorrencia=1,
    )
    grafo._roteador_em_curso = 1
    decisao, falha, ms = await grafo._roteador_decidir({"message_body": "oi"}, None)
    assert decisao is None and falha == rt.FALHA_OCUPADO and ms == 0
