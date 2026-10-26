"""O modelo da allowlist (dono e DPO, 26/10/2026, opcao A; DL-0155): roteador, passos do protocolo e classify
vao ao Haiku 5.5 pela rota global SO' nas conversas da allowlist; o resto segue no GLM-5 como antes. Opcao B
(`glm5_com_reserva_haiku`): GLM-5 primeiro, Haiku quando o GLM-5 estoura o prazo ou cai.

Transportes falsos; nenhuma rede. Frases de exemplo sem dado pessoal."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Any

import pytest
import yaml
from structlog.testing import capture_logs

from maezo.agents.helena import modelo_allowlist as ma
from maezo.agents.helena import pacote as pc
from maezo.agents.helena.graph import HelenaGraph
from maezo.conhecimento import base_saude as bs
from maezo.conhecimento import saude_gerativa as sg
from maezo.runtime.inference.experimento_global import (
    MODELO_HAIKU_GLOBAL,
    ROTA_DE_RESIDENCIA,
    FalhaDaRotaGlobalError,
    RespostaDaRotaGlobal,
)
from maezo.runtime.pacote_do_agente import PacoteDoAgente, PacoteInvalidoError
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport
from tests.support.audit_fakes import FakeStartAuditSink
from tests.support.roteador_v7_fake import decisao_v7
from tests.unit.agents.test_helena_rodada_3_simulador import _CONVERSA, _classify, _entrada, _WhatsApp
from tests.unit.agents.test_helena_v4 import _DmnReal

_OUTRA = "wa:amh:hk1_" + "b" * 64
_SO_GERATIVA = "wa:amh:hk1_" + "c" * 64
_FRASES = (
    "a minha filha esta com febre desde ontem",
    "estou com dor de cabeca faz dois dias",
    "meu pai caiu e machucou o joelho",
)


class _Glm:
    """O GLM-5 falso (o provedor de hoje): devolve o JSON pedido, ou dorme/levanta quando mandado."""

    modelo = "zai.glm-5"

    def __init__(self, resposta: str = "", *, dorme_s: float = 0.0, erro: Exception | None = None) -> None:
        self.resposta = resposta
        self.dorme_s = dorme_s
        self.erro = erro
        self.prompts: list[str] = []

    async def generate(self, prompt: str, **_: Any) -> str:
        self.prompts.append(prompt)
        if self.dorme_s:
            await asyncio.sleep(self.dorme_s)
        if self.erro is not None:
            raise self.erro
        return self.resposta


class _Haiku:
    """A rota global falsa (mesma forma do `TransporteGlobalExperimental`)."""

    rota = ROTA_DE_RESIDENCIA
    modelo = MODELO_HAIKU_GLOBAL

    def __init__(self, resposta: str = "", *, dorme_s: float = 0.0, erro: Exception | None = None) -> None:
        self.resposta = resposta
        self.dorme_s = dorme_s
        self.erro = erro
        self.enviados: list[str] = []

    async def conversar(self, sistema: str, mensagens: Any, *, max_tokens: int) -> RespostaDaRotaGlobal:
        self.enviados.append("\n".join(str(c["text"]) for m in mensagens for c in m["content"]))
        if self.dorme_s:
            await asyncio.sleep(self.dorme_s)
        if self.erro is not None:
            raise self.erro
        return RespostaDaRotaGlobal(texto=self.resposta, parada="end_turn", entrada=1, saida=1, latencia_ms=1)


def _grafo(
    glm: _Glm, haiku: _Haiku, *, modo: str | None = ma.MODO_HAIKU, prazo_s: float = 8.0
) -> HelenaGraph:
    return HelenaGraph(
        inference=glm,  # type: ignore[arg-type]
        dmn=_DmnReal(),  # type: ignore[arg-type]
        cibseven=FakeCibSevenTransport(),
        audit_sink=FakeStartAuditSink(),
        whatsapp=_WhatsApp(),
        roteador_lucas_enabled=True,
        historico_enabled=True,
        coleta_enabled=True,
        helena_v4_enabled=True,
        relogio=lambda: datetime(2026, 10, 26, 18, 0, tzinfo=UTC),
        saude_gerativa=sg.RedatorDeSaude(transporte=haiku, conversas=frozenset({_CONVERSA, _SO_GERATIVA})),
        helena_base_saude_enabled=True,
        base_saude=bs.BASE_VAZIA,
        helena_roteador="ativo",
        helena_roteador_conversas=frozenset({_CONVERSA, _OUTRA}),
        roteador_prazo_s=prazo_s,
        modelo_allowlist=modo,
    )


def _estado(frase: str, conversa: str = _CONVERSA) -> dict[str, Any]:
    estado = _entrada(frase)
    estado["conversation_id"] = conversa
    return estado


def _eventos(logs: list[dict[str, Any]], nome: str) -> list[dict[str, Any]]:
    return [e for e in logs if e.get("event") == nome]


# --- O interruptor -----------------------------------------------------------------------------------------
def test_padrao_do_codigo_e_haiku_e_o_pacote_vence_a_env() -> None:
    assert ma.modo_escolhido(None, {}) == ma.MODO_HAIKU
    assert ma.modo_escolhido(None, {ma.ENV_MODO: "glm5"}) == ma.MODO_GLM5
    assert ma.modo_escolhido(None, {ma.ENV_MODO: " GLM5_COM_RESERVA_HAIKU "}) == ma.MODO_GLM5_COM_RESERVA
    assert ma.modo_escolhido("glm5", {ma.ENV_MODO: "haiku"}) == ma.MODO_GLM5
    # Valor estranho na env (ou no pacote) nao inventa modo: vale o padrao.
    assert ma.modo_escolhido("gpt", {ma.ENV_MODO: "qualquer"}) == ma.MODO_HAIKU


def test_construido_direto_sem_modo_e_o_de_antes() -> None:
    glm, haiku = _Glm(decisao_v7("sintoma")), _Haiku(decisao_v7("sintoma"))
    grafo = _grafo(glm, haiku, modo=None)
    assert ma.provedor_para(grafo, _estado(_FRASES[0]), ma.PAPEL_ROTEADOR) is grafo._roteador_llm


def _pacote(roteador_extra: dict[str, Any]) -> PacoteDoAgente:
    configuracao = {
        "formato": pc.FORMATO_CONFIGURACAO,
        "roteador": {"modo": "ativo", "conversas": ["wa:amh:hk1_" + "d" * 64], **roteador_extra},
    }
    return PacoteDoAgente(
        agente="helena",
        versao="teste",
        sha256="0" * 64,
        arquivos={pc.ARQUIVO_CONFIGURACAO: yaml.safe_dump(configuracao)},
    )


@pytest.mark.parametrize("modo", sorted(ma.MODOS))
def test_pacote_aceita_a_chave_opcional_e_a_passa_ao_grafo(modo: str) -> None:
    pacote = _pacote({"modelo_allowlist": modo})
    assert pc.configuracao(pacote)["roteador"]["modelo_allowlist"] == modo

    class _Carregador:
        def atual(self) -> PacoteDoAgente:
            return pacote

    saida = pc.PacoteDaHelena(_Carregador()).aplicar_no_grafo({})  # type: ignore[arg-type]
    assert saida["helena_modelo_allowlist"] == modo


def test_pacote_sem_a_chave_continua_valido_e_nao_passa_nada() -> None:
    pacote = _pacote({})
    pc.configuracao(pacote)

    class _Carregador:
        def atual(self) -> PacoteDoAgente:
            return pacote

    assert "helena_modelo_allowlist" not in pc.PacoteDaHelena(_Carregador()).aplicar_no_grafo({})  # type: ignore[arg-type]


@pytest.mark.parametrize("valor", ["gpt", ["haiku"], 1])
def test_pacote_recusa_valor_fora_do_dominio(valor: object) -> None:
    with pytest.raises(PacoteInvalidoError, match="modelo_allowlist"):
        pc.configuracao(_pacote({"modelo_allowlist": valor}))


# --- Opcao A: a allowlist usa o Haiku; fora dela, o GLM-5 ------------------------------------------------
@pytest.mark.parametrize("frase", _FRASES)
async def test_allowlist_roteador_vai_ao_haiku(frase: str) -> None:
    glm, haiku = _Glm(decisao_v7("conversa")), _Haiku(decisao_v7("sintoma"))
    grafo = _grafo(glm, haiku)
    with capture_logs() as logs:
        decisao, falha, _ms = await grafo._roteador_decidir(_estado(frase), None)
    assert falha is None and decisao is not None
    assert len(haiku.enviados) == 1 and glm.prompts == []
    chamada = _eventos(logs, "helena_modelo_allowlist_chamada")
    assert chamada and chamada[0]["papel"] == "roteador" and chamada[0]["resultado"] == "ok"
    contador = _eventos(logs, "inferencia_chamada")
    assert contador and contador[0]["modelo"] == MODELO_HAIKU_GLOBAL and contador[0]["regiao"] == "global"


@pytest.mark.parametrize("frase", _FRASES)
async def test_allowlist_passo_do_protocolo_vai_ao_haiku(frase: str) -> None:
    from maezo.agents.helena.habilidades.protocolo import fluxo

    escolha = json.dumps({"publico": "adulto", "faixa_crianca": "nao_se_aplica", "protocolo": "x"})
    glm, haiku = _Glm("{}"), _Haiku(escolha)
    grafo = _grafo(glm, haiku)
    with capture_logs() as logs:
        lido = await fluxo._chamar(grafo, _estado(frase), "Voce escolhe o PROTOCOLO", _ler, "escolha")
    assert lido == {"publico": "adulto", "faixa_crianca": "nao_se_aplica", "protocolo": "x"}
    assert len(haiku.enviados) == 1 and glm.prompts == []
    assert _eventos(logs, "helena_modelo_allowlist_chamada")[0]["papel"] == "protocolo"


def _ler(bruto: str) -> tuple[Any, list[str]]:
    try:
        return json.loads(bruto), []
    except ValueError:
        return None, ["json"]


@pytest.mark.parametrize("frase", _FRASES)
async def test_allowlist_classify_vai_ao_haiku(frase: str) -> None:
    glm = _Glm(_classify(intent="information"))
    haiku = _Haiku("```json\n" + _classify(intent="symptom", intensidade="moderada") + "\n```")
    grafo = _grafo(glm, haiku)
    with capture_logs() as logs:
        extracao, falha = await grafo._classify_llm(_estado(frase))  # type: ignore[arg-type]
    assert falha is None and extracao is not None and extracao["intent"] == "symptom"
    assert len(haiku.enviados) == 1 and glm.prompts == []
    assert _eventos(logs, "helena_modelo_allowlist_chamada")[0]["papel"] == "classify"


@pytest.mark.parametrize("conversa", [_OUTRA, _SO_GERATIVA, "wa:amh:hk1_" + "e" * 64])
async def test_fora_da_allowlist_tudo_segue_no_glm5(conversa: str) -> None:
    """So' na lista do roteador, so' na da saude gerativa, ou em nenhuma: o GLM-5 de sempre (um predicado)."""
    from maezo.agents.helena.habilidades.protocolo import fluxo

    glm, haiku = _Glm(decisao_v7("sintoma")), _Haiku(decisao_v7("conversa"))
    grafo = _grafo(glm, haiku)
    estado = _estado(_FRASES[0], conversa)
    assert ma.na_allowlist(grafo, estado) is False
    await grafo._roteador_decidir(estado, None)
    await fluxo._chamar(grafo, estado, "Voce escolhe o PROTOCOLO", _ler, "escolha")
    glm.resposta = _classify(intent="symptom")
    await grafo._classify_llm(estado)  # type: ignore[arg-type]
    assert len(glm.prompts) == 3 and haiku.enviados == []


async def test_modo_glm5_desliga_o_haiku_tambem_na_allowlist() -> None:
    glm, haiku = _Glm(decisao_v7("sintoma")), _Haiku(decisao_v7("conversa"))
    grafo = _grafo(glm, haiku, modo=ma.MODO_GLM5)
    await grafo._roteador_decidir(_estado(_FRASES[1]), None)
    assert len(glm.prompts) == 1 and haiku.enviados == []


async def test_numero_longo_nao_sai_pela_rota_global() -> None:
    glm, haiku = _Glm(), _Haiku(_classify(intent="information"))
    grafo = _grafo(glm, haiku)
    await grafo._classify_llm(_estado("meu documento e 123.456.789-09 e o telefone 11 98888 7777"))  # type: ignore[arg-type]
    enviado = haiku.enviados[0]
    assert "789" not in enviado and "7777" not in enviado and ma.DADO_REMOVIDO in enviado


# --- Falha do Haiku: medida e no caminho seguro de hoje --------------------------------------------------
async def test_haiku_no_prazo_vira_falha_de_prazo_registrada() -> None:
    provedor = ma.ProvedorHaikuGlobal(_Haiku("{}", dorme_s=1.0), papel=ma.PAPEL_ROTEADOR, prazo_s=0.2)
    with capture_logs() as logs, pytest.raises(ma.ModeloAllowlistTimeoutError):
        await provedor.generate("x")
    assert _eventos(logs, "inferencia_chamada")[0]["resultado"] == "prazo"
    assert _eventos(logs, "helena_modelo_allowlist_chamada")[0]["resultado"] == "prazo"


@pytest.mark.parametrize(
    "erro", [FalhaDaRotaGlobalError("servico:ThrottlingException"), FalhaDaRotaGlobalError("resposta_vazia")]
)
async def test_falha_do_haiku_no_classify_e_registrada_sem_texto(erro: Exception) -> None:
    glm, haiku = _Glm(), _Haiku(erro=erro)
    grafo = _grafo(glm, haiku)
    with capture_logs() as logs:
        extracao, falha = await grafo._classify_llm(_estado(_FRASES[2]))  # type: ignore[arg-type]
    assert extracao is None and falha is not None and glm.prompts == []
    linha = _eventos(logs, "inferencia_chamada")[0]
    assert linha["resultado"] == "falha" and linha["erro"] == "FalhaDaRotaGlobalError"


async def test_resposta_vazia_do_haiku_e_falha() -> None:
    provedor = ma.ProvedorHaikuGlobal(_Haiku("   "), papel=ma.PAPEL_PROTOCOLO, prazo_s=2.0)
    with capture_logs() as logs, pytest.raises(FalhaDaRotaGlobalError):
        await provedor.generate("x")
    assert _eventos(logs, "inferencia_chamada")[0]["erro"] == "resposta_vazia"


async def test_roteador_com_haiku_no_prazo_cai_no_caminho_de_sempre_com_falha_prazo() -> None:
    glm, haiku = _Glm(), _Haiku(decisao_v7("sintoma"), dorme_s=8.0)
    grafo = _grafo(glm, haiku, prazo_s=0.6)
    decisao, falha, _ms = await grafo._roteador_decidir(_estado(_FRASES[0]), None)
    assert decisao is None and falha == "prazo" and glm.prompts == []


# --- Opcao B: GLM-5 primeiro, Haiku de reserva --------------------------------------------------------------
@pytest.mark.parametrize("frase", _FRASES)
async def test_opcao_b_glm5_no_prazo_cai_no_haiku(frase: str) -> None:
    glm = _Glm(decisao_v7("conversa"), dorme_s=5.0)
    haiku = _Haiku(decisao_v7("sintoma"))
    grafo = _grafo(glm, haiku, modo=ma.MODO_GLM5_COM_RESERVA, prazo_s=3.6)  # GLM-5 tem 0,6 s; reserva 3 s
    with capture_logs() as logs:
        decisao, falha, ms = await grafo._roteador_decidir(_estado(frase), None)
    assert falha is None and decisao is not None and ms < 3600
    assert len(glm.prompts) == 1 and len(haiku.enviados) == 1
    reserva = _eventos(logs, "helena_modelo_allowlist_reserva")
    assert reserva and reserva[0]["causa"] == "TimeoutError"
    linhas = _eventos(logs, "helena_modelo_allowlist_chamada")
    assert [(x["modelo"], x["resultado"], x["reserva"]) for x in linhas] == [
        ("zai.glm-5", "prazo", False),
        (MODELO_HAIKU_GLOBAL, "ok", True),
    ]
    # O GLM-5 ja' conta a propria chamada no `br_regional`: a reserva nao a conta de novo.
    assert [x["modelo"] for x in _eventos(logs, "inferencia_chamada")] == [MODELO_HAIKU_GLOBAL]


async def test_opcao_b_glm5_fora_do_ar_cai_no_haiku_no_classify() -> None:
    glm = _Glm(erro=RuntimeError("indisponivel"))
    haiku = _Haiku(_classify(intent="symptom"))
    grafo = _grafo(glm, haiku, modo=ma.MODO_GLM5_COM_RESERVA)
    extracao, falha = await grafo._classify_llm(_estado(_FRASES[0]))  # type: ignore[arg-type]
    assert falha is None and extracao is not None and extracao["intent"] == "symptom"
    assert len(glm.prompts) == 1 and len(haiku.enviados) == 1


async def test_opcao_b_glm5_bom_nao_chama_o_haiku() -> None:
    glm, haiku = _Glm(decisao_v7("sintoma")), _Haiku(decisao_v7("conversa"))
    grafo = _grafo(glm, haiku, modo=ma.MODO_GLM5_COM_RESERVA)
    decisao, falha, _ms = await grafo._roteador_decidir(_estado(_FRASES[1]), None)
    assert falha is None and decisao is not None
    assert len(glm.prompts) == 1 and haiku.enviados == []


async def test_opcao_b_fora_da_allowlist_nao_tem_reserva() -> None:
    glm, haiku = _Glm(erro=RuntimeError("indisponivel")), _Haiku(decisao_v7("sintoma"))
    grafo = _grafo(glm, haiku, modo=ma.MODO_GLM5_COM_RESERVA)
    _decisao, falha, _ms = await grafo._roteador_decidir(_estado(_FRASES[1], _OUTRA), None)
    assert falha is not None and haiku.enviados == []
