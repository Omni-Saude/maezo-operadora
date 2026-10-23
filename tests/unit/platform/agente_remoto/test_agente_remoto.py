"""O agente de entrada em servico proprio (ADR-0067 fase 4b), de ponta a ponta e sem rede real.

O receptor (despachante + rota interna de envio) e o servico do agente sao dois apps FastAPI no mesmo
processo,
ligados por `httpx.ASGITransport`. O grafo da Helena e' trocado por um duble (`graph.build`), como nos testes
do despachante em processo, e o mesmo turno roda nos dois modos para provar que o resultado e' o MESMO.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver

from maezo.agents import carregar_agente_de_entrada
from maezo.agents.helena import graph as helena_graph_module
from maezo.contratos import conversa_remota as fio
from maezo.contratos.conversa import RespostaDeCadastro
from maezo.gateway.pseudonymizer import Pseudonymizer
from maezo.platform.agente_remoto.cliente import (
    AgenteDeEntradaRemoto,
    AgenteRemotoIndisponivelError,
    FalhaNoAgenteRemotoError,
    RegistroDeRemetentes,
    RemetenteRemoto,
)
from maezo.platform.agente_remoto.rota_de_envio import registrar_rota_de_envio
from maezo.platform.agente_remoto.servidor import ServicoDoAgente, criar_app
from maezo.platform.webhooks.whatsapp.app import create_app
from maezo.platform.webhooks.whatsapp.dispatch import HelenaDispatcher, InboundMessage
from maezo.platform.webhooks.whatsapp.settings import WhatsAppWebhookSettings
from maezo.runtime.checkpoint import Checkpointer
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport
from maezo.tools.workers.dmn_transport import FakeDmnTransport
from tests.support.audit_fakes import FakeStartAuditSink

CHAVE = fio.derivar_chave("chave-phi-de-teste")
NUMERO = "5511999999999"


class _Inferencia:
    async def generate(
        self,
        prompt: str,
        *,
        phi: bool = False,
        agent_id: str | None = None,
        tenant_id: str | None = None,
        task_kind: str | None = None,
    ) -> str:
        return ""


class _ClienteWhatsApp:
    def __init__(self) -> None:
        self.enviados: list[tuple[str, str]] = []

    async def send_message(self, to: str, text: str) -> dict[str, Any]:
        self.enviados.append((to, text))
        return {"messages": [{"id": f"wamid.{len(self.enviados)}"}]}


def _despachante(cliente: _ClienteWhatsApp, *, checkpointer: Checkpointer | None = None) -> HelenaDispatcher:
    return HelenaDispatcher(
        tenant_id="amh",
        inference=_Inferencia(),  # type: ignore[arg-type]
        dmn=FakeDmnTransport(),
        cibseven=FakeCibSevenTransport(),
        whatsapp_client=cliente,  # type: ignore[arg-type]
        pseudonymizer=Pseudonymizer(),
        audit_sink=FakeStartAuditSink(),
        checkpointer=checkpointer,
    )


@dataclass
class _Grafo:
    """Duble do grafo da Helena: responde pelo remetente do `config` e devolve tipos que o JSON perde."""

    config: Mapping[str, Any]
    estados: list[dict[str, Any]] = field(default_factory=list)
    atualizacoes: list[tuple[Any, dict[str, Any], str | None]] = field(default_factory=list)
    falhar_envio: bool = False

    def compile(self, checkpointer: Any = None) -> _Grafo:
        return self

    async def ainvoke(self, state: dict[str, Any], config: Any = None) -> dict[str, Any]:
        self.estados.append(dict(state))
        to_hash = state["conversation_id"].rsplit(":", 1)[-1]
        envio = await self.config["whatsapp"].send(to_hash, f"resposta: {state['message_body']}")
        return {
            "next_kind": "inform",
            "response_text": f"resposta: {state['message_body']}",
            "conversation_id": state["conversation_id"],
            "consulta_literais": ("a", "b"),
            "conjunto": {"x", "y"},
            "quando": datetime(2026, 10, 23, 12, 0, tzinfo=UTC),
            "envio": envio,
            "roteador_lucas_enabled": self.config["roteador_lucas_enabled"],
            "agent_version": self.config["agent_version"],
        }

    async def aget_state(self, config: Any) -> Any:
        @dataclass
        class _Retrato:
            values: dict[str, Any]
            next: tuple[str, ...] = ()

        return _Retrato(values={"historico_conversa": [{"quem": "pessoa"}]})

    async def aupdate_state(self, config: Any, values: dict[str, Any], as_node: str | None = None) -> Any:
        self.atualizacoes.append((config, dict(values), as_node))
        return {"configurable": {"thread_id": config["configurable"]["thread_id"], "checkpoint_id": "c1"}}


@dataclass
class _Montagem:
    receptor: HelenaDispatcher
    cliente_receptor: _ClienteWhatsApp
    cliente_agente: _ClienteWhatsApp
    remoto: AgenteDeEntradaRemoto
    app_do_agente: FastAPI
    app_do_receptor: FastAPI
    grafos: list[_Grafo]


def _montar(
    monkeypatch: pytest.MonkeyPatch,
    *,
    registro: RegistroDeRemetentes | None = None,
    entrada: Any = None,
    saver: InMemorySaver | None = None,
) -> _Montagem:
    grafos: list[_Grafo] = []

    def _build(config: Mapping[str, Any]) -> _Grafo:
        grafos.append(_Grafo(config))
        return grafos[-1]

    monkeypatch.setattr(helena_graph_module, "build", _build)
    monkeypatch.delenv("MAEZO_AGENTE_DE_ENTRADA_URL", raising=False)
    cliente_receptor, cliente_agente = _ClienteWhatsApp(), _ClienteWhatsApp()
    receptor = _despachante(cliente_receptor, checkpointer=Checkpointer(saver=saver) if saver else None)
    portador = _despachante(cliente_agente, checkpointer=Checkpointer(saver=saver) if saver else None)
    app_do_receptor = FastAPI()
    servico = ServicoDoAgente(
        entrada=entrada if entrada is not None else carregar_agente_de_entrada("helena"),
        dependencias=portador,
        chave=CHAVE,
        receptor=httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app_do_receptor), base_url="http://receptor"
        ),
    )
    app_do_agente = criar_app(servico, chave=CHAVE)
    remoto = AgenteDeEntradaRemoto(
        "http://agente",
        "helena",
        chave=CHAVE,
        http=httpx.AsyncClient(transport=httpx.ASGITransport(app=app_do_agente), base_url="http://agente"),
        registro=registro,
    )
    registrar_rota_de_envio(app_do_receptor, remoto)
    receptor._entrada = remoto
    return _Montagem(
        receptor, cliente_receptor, cliente_agente, remoto, app_do_agente, app_do_receptor, grafos
    )


# --------------------------------------------------------------------------------------------------------
# O mesmo turno, em processo e remoto
# --------------------------------------------------------------------------------------------------------


async def test_despachante_remoto_produz_o_mesmo_resultado_que_em_processo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    m = _montar(monkeypatch)
    mensagem = InboundMessage(from_number=NUMERO, text="quero saber da carteirinha", message_id="wamid.1")
    remoto = await m.receptor.dispatch(mensagem)
    estado_remoto = m.grafos[-1].estados[-1]

    cliente_local = _ClienteWhatsApp()
    local = await _despachante(cliente_local).dispatch(mensagem)
    estado_local = m.grafos[-1].estados[-1]

    assert remoto == local
    assert isinstance(remoto["consulta_literais"], tuple)
    assert isinstance(remoto["conjunto"], set)
    assert isinstance(remoto["quando"], datetime)
    # O estado montado pelo servico (marcador adiado) e' o mesmo que o despachante em processo monta.
    assert estado_remoto == estado_local
    # O envio do turno remoto chegou ao numero cru DESTE turno pelo remetente do RECEPTOR, e so' a ele.
    assert (
        m.cliente_receptor.enviados
        == cliente_local.enviados
        == [(NUMERO, "resposta: quero saber da carteirinha")]
    )
    assert m.cliente_agente.enviados == []


async def test_o_config_do_grafo_remoto_e_composto_pelo_servico(monkeypatch: pytest.MonkeyPatch) -> None:
    m = _montar(monkeypatch)
    await m.receptor.dispatch(InboundMessage(from_number=NUMERO, text="oi", message_id="wamid.2"))
    config = m.grafos[-1].config
    # O remetente do servico e' o de volta ao receptor; inferencia/DMN/motor sao os do portador do servico.
    assert isinstance(config["whatsapp"], RemetenteRemoto)
    assert config["roteador_lucas_enabled"] is False
    assert config["agent_version"] == "helena@v0"
    assert config["dmn"] is not m.receptor.dmn


async def test_registro_guarda_o_remetente_por_token_e_nao_o_numero(monkeypatch: pytest.MonkeyPatch) -> None:
    registro = RegistroDeRemetentes()
    m = _montar(monkeypatch, registro=registro)
    await m.receptor.dispatch(InboundMessage(from_number=NUMERO, text="oi", message_id="wamid.3"))
    assert len(registro) == 1
    (token,) = registro._itens
    assert len(token) >= 22  # 16 bytes aleatorios em base64url
    assert NUMERO not in token


# --------------------------------------------------------------------------------------------------------
# O envio volta so' pelo token do turno
# --------------------------------------------------------------------------------------------------------


class _Relogio:
    def __init__(self) -> None:
        self.agora = 1000.0

    def __call__(self) -> float:
        return self.agora


async def test_token_expirado_recusa_o_envio_e_o_turno_falha(monkeypatch: pytest.MonkeyPatch) -> None:
    relogio = _Relogio()
    m = _montar(monkeypatch, registro=RegistroDeRemetentes(ttl_s=10.0, relogio=relogio))
    original = _Grafo.ainvoke

    async def _ainvoke_depois_do_prazo(
        self: _Grafo, state: dict[str, Any], config: Any = None
    ) -> dict[str, Any]:
        relogio.agora += 11.0
        return await original(self, state, config)

    monkeypatch.setattr(_Grafo, "ainvoke", _ainvoke_depois_do_prazo)
    with pytest.raises(FalhaNoAgenteRemotoError) as erro:
        await m.receptor.dispatch(InboundMessage(from_number=NUMERO, text="oi", message_id="wamid.4"))
    assert erro.value.tipo == "AgenteRemotoIndisponivelError"
    assert m.cliente_receptor.enviados == []
    assert m.cliente_agente.enviados == []


class _RemetenteGravador:
    def __init__(self) -> None:
        self.enviados: list[tuple[str, str]] = []

    async def send(self, to_hash: str, text: str) -> dict[str, Any]:
        self.enviados.append((to_hash, text))
        return {"ok": True}


async def test_rota_de_envio_entrega_so_ao_remetente_do_token() -> None:
    remoto = AgenteDeEntradaRemoto("http://agente", "helena", chave=CHAVE)
    app = FastAPI()
    registrar_rota_de_envio(app, remoto)
    a, b = _RemetenteGravador(), _RemetenteGravador()
    token_a = remoto.registro.registrar(a)
    remoto.registro.registrar(b)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://receptor") as http:
        resultado = await RemetenteRemoto(http=http, chave=CHAVE, token=token_a).send("hk1_x", "texto")
    assert resultado == {"ok": True}
    assert a.enviados == [("hk1_x", "texto")]
    assert b.enviados == []


@pytest.mark.parametrize(
    ("assinatura", "token", "status"),
    [(None, "real", 401), ("errada", "real", 401), ("certa", "desconhecido", 404)],
)
async def test_rota_de_envio_recusa_sem_detalhe(assinatura: str | None, token: str, status: int) -> None:
    remoto = AgenteDeEntradaRemoto("http://agente", "helena", chave=CHAVE)
    app = FastAPI()
    registrar_rota_de_envio(app, remoto)
    gravador = _RemetenteGravador()
    real = remoto.registro.registrar(gravador)
    corpo = fio.codificar({"token": real if token == "real" else token, "to_hash": "hk1_x", "text": "t"})
    cabecalhos = {}
    if assinatura == "certa":
        cabecalhos[fio.CABECALHO_ASSINATURA] = fio.assinar(CHAVE, corpo)
    elif assinatura == "errada":
        cabecalhos[fio.CABECALHO_ASSINATURA] = fio.assinar(fio.derivar_chave("outra"), corpo)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://receptor") as http:
        resposta = await http.post(fio.ROTA_ENVIO, content=corpo, headers=cabecalhos)
    assert resposta.status_code == status
    assert resposta.content == b""
    assert gravador.enviados == []


async def test_servico_do_agente_recusa_pedido_sem_assinatura(monkeypatch: pytest.MonkeyPatch) -> None:
    m = _montar(monkeypatch)
    corpo = fio.codificar({"texto": "quero falar com um atendente"})
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=m.app_do_agente), base_url="http://a"
    ) as http:
        sem = await http.post(fio.ROTA_PEDIDO_DE_PESSOA, content=corpo)
        errada = await http.post(
            fio.ROTA_PEDIDO_DE_PESSOA, content=corpo, headers={fio.CABECALHO_ASSINATURA: "00" * 32}
        )
        saude = await http.get("/healthz")
    assert (sem.status_code, sem.content) == (401, b"")
    assert (errada.status_code, errada.content) == (401, b"")
    assert saude.status_code == 200


async def test_resposta_sem_assinatura_do_servico_e_falha_externa() -> None:
    falso = FastAPI()

    @falso.post(fio.ROTA_PEDIDO_DE_PESSOA)
    async def _sem_assinatura() -> dict[str, Any]:
        return {"resultado": True}

    remoto = AgenteDeEntradaRemoto(
        "http://agente",
        "helena",
        chave=CHAVE,
        http=httpx.AsyncClient(transport=httpx.ASGITransport(app=falso), base_url="http://agente"),
    )
    with pytest.raises(AgenteRemotoIndisponivelError):
        await remoto.postar(fio.ROTA_PEDIDO_DE_PESSOA, {"texto": "x"})


async def test_rede_fora_vira_falha_externa() -> None:
    def _cai(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("sem rota", request=request)

    remoto = AgenteDeEntradaRemoto(
        "http://agente",
        "helena",
        chave=CHAVE,
        http=httpx.AsyncClient(transport=httpx.MockTransport(_cai), base_url="http://agente"),
    )
    with pytest.raises(AgenteRemotoIndisponivelError) as erro:
        await remoto.construir_grafo({"whatsapp": None}).compile().ainvoke(remoto.estado_inicial())
    assert isinstance(erro.value, RuntimeError)


# --------------------------------------------------------------------------------------------------------
# Estado, memoria, pedido de pessoa, erros
# --------------------------------------------------------------------------------------------------------


async def test_campo_forjado_no_estado_inicial_levanta_type_error_do_outro_lado(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    m = _montar(monkeypatch)
    compilado = m.remoto.construir_grafo({"whatsapp": None}).compile()
    with pytest.raises(TypeError):
        await compilado.ainvoke(m.remoto.estado_inicial(tenant_id="amh", next_kind="escalate"))


async def test_esquecer_memoria_remoto_le_e_grava_a_memoria_neutra_do_agente(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    saver = InMemorySaver()
    m = _montar(monkeypatch, saver=saver)
    conversa = "wa:amh:hk1_" + "a" * 64
    await m.receptor._esquecer_memoria_da_conversa(conversa, _RemetenteGravador())
    (config, valores, as_node) = m.grafos[-1].atualizacoes[-1]
    assert valores == carregar_agente_de_entrada("helena").memoria_neutra()
    assert as_node == "respond"
    assert config["configurable"]["thread_id"] == conversa


async def test_thread_de_outro_tenant_e_recusada(monkeypatch: pytest.MonkeyPatch) -> None:
    m = _montar(monkeypatch, saver=InMemorySaver())
    compilado = m.remoto.construir_grafo({"whatsapp": None}).compile(checkpointer=object())
    with pytest.raises(PermissionError):
        await compilado.aget_state({"configurable": {"thread_id": "wa:outro:hk1_" + "a" * 64}})


async def test_persistencia_divergente_e_recusada(monkeypatch: pytest.MonkeyPatch) -> None:
    m = _montar(monkeypatch)  # servico sem checkpointer
    compilado = m.remoto.construir_grafo({"whatsapp": None}).compile(checkpointer=object())
    with pytest.raises(FalhaNoAgenteRemotoError):
        await compilado.ainvoke(m.remoto.estado_inicial(tenant_id="amh"))


async def test_pedido_de_pessoa_vem_do_turno_sem_chamada_sincrona(monkeypatch: pytest.MonkeyPatch) -> None:
    m = _montar(monkeypatch)
    texto = "quero falar com um atendente"
    await m.receptor.dispatch(InboundMessage(from_number=NUMERO, text=texto, message_id="wamid.5"))
    assert m.remoto._http_sincrono is None
    esperado = carregar_agente_de_entrada("helena").eh_pedido_explicito_de_pessoa(texto)
    assert m.remoto.eh_pedido_explicito_de_pessoa(texto) is esperado
    assert m.remoto._http_sincrono is None  # veio do cache do turno


def test_pedido_de_pessoa_fora_do_cache_pergunta_ao_servico(monkeypatch: pytest.MonkeyPatch) -> None:
    m = _montar(monkeypatch)
    helena = carregar_agente_de_entrada("helena")
    with TestClient(m.app_do_agente) as sincrono:
        m.remoto._http_sincrono = sincrono
        for texto in ("quero falar com um atendente", "qual o valor do boleto"):
            assert m.remoto.eh_pedido_explicito_de_pessoa(texto) is helena.eh_pedido_explicito_de_pessoa(
                texto
            )


# --------------------------------------------------------------------------------------------------------
# Os nos fora do grafo e a resposta de cadastro, com um agente duble do lado do servico
# --------------------------------------------------------------------------------------------------------


class _AgenteDuble:
    agent_id = "helena"
    agent_version = "helena@duble"

    def __init__(self) -> None:
        self.chamadas: list[tuple[str, Any]] = []

    async def escalar_identidade(
        self, dependencias: Mapping[str, Any], estado: dict[str, Any], *, motivo: str
    ) -> Mapping[str, Any]:
        self.chamadas.append(("identidade", (dict(dependencias), dict(estado), motivo)))
        await dependencias["whatsapp"].send(
            estado["conversation_id"].rsplit(":", 1)[-1], "um atendente vai falar"
        )
        return {"escalation_started": True, "start_desfecho": "iniciado"}

    async def escalar_e_responder(
        self, dependencias: Mapping[str, Any], estado: dict[str, Any]
    ) -> dict[str, Any]:
        self.chamadas.append(("escalar", dict(estado)))
        return {**estado, "response_text": "escalado", "escalation_started": True}

    async def resposta_de_cadastro(
        self,
        texto: str,
        *,
        consultas_ligadas: bool,
        fonte_ligada: bool,
        sinal_de_emergencia: Callable[[str], bool],
        resolver_identidade: Callable[[], Awaitable[dict[str, Any] | None]] | None,
    ) -> RespostaDeCadastro | None:
        if sinal_de_emergencia(texto) or texto == "nada":
            return None
        if texto == "meu nome" or resolver_identidade is None:
            return RespostaDeCadastro(tipo="nome", texto="fixo", com_dados=False)
        identidade = await resolver_identidade()
        return RespostaDeCadastro(tipo="plano", texto=f"plano {identidade}", com_dados=identidade is not None)

    def eh_pedido_explicito_de_pessoa(self, texto: str) -> bool:
        return False


async def test_escalar_identidade_remoto_envia_pelo_remetente_do_turno(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    duble = _AgenteDuble()
    m = _montar(monkeypatch, entrada=duble)
    gravador = _RemetenteGravador()
    conversa = "wa:amh:hk1_" + "b" * 64
    resultado = await m.remoto.escalar_identidade(
        {"whatsapp": gravador, "roteador_lucas_enabled": True, "inference": object()},
        {"tenant_id": "amh", "conversation_id": conversa, "canal": "whatsapp"},
        motivo="identidade_nao_verificada",
    )
    assert resultado == {"escalation_started": True, "start_desfecho": "iniciado"}
    dependencias, estado, motivo = duble.chamadas[0][1]
    assert motivo == "identidade_nao_verificada"
    assert estado["conversation_id"] == conversa
    assert dependencias["roteador_lucas_enabled"] is True
    assert dependencias["inference"] is not None and isinstance(dependencias["whatsapp"], RemetenteRemoto)
    assert gravador.enviados == [("hk1_" + "b" * 64, "um atendente vai falar")]


async def test_estado_de_outro_tenant_e_recusado_nos_nos(monkeypatch: pytest.MonkeyPatch) -> None:
    duble = _AgenteDuble()
    m = _montar(monkeypatch, entrada=duble)
    with pytest.raises(PermissionError):
        await m.remoto.escalar_e_responder({"whatsapp": None}, {"tenant_id": "outro"})
    assert duble.chamadas == []


async def test_escalar_e_responder_atualiza_o_estado_do_chamador(monkeypatch: pytest.MonkeyPatch) -> None:
    m = _montar(monkeypatch, entrada=_AgenteDuble())
    estado: dict[str, Any] = {"tenant_id": "amh", "next_kind": "escalate", "tupla": ("x",)}
    devolvido = await m.remoto.escalar_e_responder({"whatsapp": None}, estado)
    assert devolvido is estado
    assert estado["response_text"] == "escalado"
    assert estado["tupla"] == ("x",)


async def test_resposta_de_cadastro_so_resolve_o_perfil_quando_o_agente_pede(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    m = _montar(monkeypatch, entrada=_AgenteDuble())
    resolucoes: list[int] = []

    async def _resolver() -> dict[str, Any] | None:
        resolucoes.append(1)
        return {"plano": "ouro"}

    def _sem_emergencia(_texto: str) -> bool:
        return False

    kw: dict[str, Any] = {
        "consultas_ligadas": False,
        "fonte_ligada": True,
        "sinal_de_emergencia": _sem_emergencia,
    }
    fixo = await m.remoto.resposta_de_cadastro("meu nome", resolver_identidade=_resolver, **kw)
    assert fixo == RespostaDeCadastro(tipo="nome", texto="fixo", com_dados=False)
    assert resolucoes == []
    com_perfil = await m.remoto.resposta_de_cadastro("meu plano", resolver_identidade=_resolver, **kw)
    assert com_perfil == RespostaDeCadastro(tipo="plano", texto="plano {'plano': 'ouro'}", com_dados=True)
    assert resolucoes == [1]
    assert await m.remoto.resposta_de_cadastro("nada", resolver_identidade=_resolver, **kw) is None
    emergencia = await m.remoto.resposta_de_cadastro(
        "meu plano", resolver_identidade=_resolver, **{**kw, "sinal_de_emergencia": lambda _t: True}
    )
    assert emergencia is None
    assert resolucoes == [1]


# --------------------------------------------------------------------------------------------------------
# A chave: sem a env, nada muda
# --------------------------------------------------------------------------------------------------------


def _settings() -> WhatsAppWebhookSettings:
    return WhatsAppWebhookSettings(app_secret="s", verify_token="v", tenant_id="amh")


def test_sem_a_env_o_agente_e_o_de_processo_e_a_rota_interna_nao_existe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MAEZO_AGENTE_DE_ENTRADA_URL", raising=False)
    despachante = _despachante(_ClienteWhatsApp())
    assert despachante.agente_de_entrada is carregar_agente_de_entrada("helena")
    caminhos = {getattr(r, "path", None) for r in create_app(_settings(), dispatcher=despachante).routes}
    assert fio.ROTA_ENVIO not in caminhos


def test_com_a_env_o_agente_e_remoto_e_a_rota_interna_existe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAEZO_AGENTE_DE_ENTRADA_URL", "http://agent-helena:8000")
    monkeypatch.setenv("PHI_HMAC_KEY", "chave-phi-de-teste")
    despachante = _despachante(_ClienteWhatsApp())
    assert isinstance(despachante.agente_de_entrada, AgenteDeEntradaRemoto)
    caminhos = {getattr(r, "path", None) for r in create_app(_settings(), dispatcher=despachante).routes}
    assert fio.ROTA_ENVIO in caminhos


def test_com_a_env_e_sem_phi_hmac_key_o_despachante_nao_e_construido(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAEZO_AGENTE_DE_ENTRADA_URL", "http://agent-helena:8000")
    monkeypatch.delenv("PHI_HMAC_KEY", raising=False)
    with pytest.raises(ValueError):
        _despachante(_ClienteWhatsApp())
