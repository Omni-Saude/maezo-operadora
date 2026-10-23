"""O agente de entrada em servico proprio (ADR-0067 fase 4b), de ponta a ponta e sem rede real.

O receptor (despachante + rota interna de envio) e o servico do agente sao apps FastAPI no mesmo
processo, ligados por `httpx.ASGITransport`. O grafo da Helena e' trocado por um duble (`graph.build`),
como nos testes do despachante em processo, e o mesmo turno roda nos dois modos para provar que o
resultado e' o MESMO.
"""

from __future__ import annotations

import os
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import ValidationError

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
    ler_resposta,
)
from maezo.platform.agente_remoto.rota_de_envio import registrar_rota_de_envio
from maezo.platform.agente_remoto.servidor import ServicoDoAgente, criar_app
from maezo.platform.agente_remoto.settings import AgenteRemotoSettings
from maezo.platform.webhooks import service as svc
from maezo.platform.webhooks.whatsapp.app import create_app
from maezo.platform.webhooks.whatsapp.dispatch import (
    AGENTE_INDISPONIVEL_TEXT,
    HelenaDispatcher,
    InboundMessage,
)
from maezo.platform.webhooks.whatsapp.settings import WhatsAppWebhookSettings
from maezo.runtime.checkpoint import Checkpointer
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport
from maezo.tools.workers.dmn_transport import FakeDmnTransport
from tests.support.audit_fakes import FakeStartAuditSink

CHAVE = fio.derivar_chave("chave-phi-de-teste")
NUMERO = "5511999999999"
RETORNO = "http://receptor"


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
    """Devolve o formato CRU da Cloud API (com o telefone em `contacts` e o `wamid`)."""

    def __init__(self) -> None:
        self.enviados: list[tuple[str, str]] = []

    async def send_message(self, to: str, text: str) -> dict[str, Any]:
        self.enviados.append((to, text))
        return {
            "messaging_product": "whatsapp",
            "contacts": [{"input": to, "wa_id": to}],
            "messages": [{"id": f"wamid.HBgN{to}{len(self.enviados)}"}],
        }


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
    recibos: list[Any] = field(default_factory=list)

    def compile(self, checkpointer: Any = None) -> _Grafo:
        return self

    async def ainvoke(self, state: dict[str, Any], config: Any = None) -> dict[str, Any]:
        self.estados.append(dict(state))
        to_hash = state["conversation_id"].rsplit(":", 1)[-1]
        self.recibos.append(await self.config["whatsapp"].send(to_hash, f"resposta: {state['message_body']}"))
        return {
            "next_kind": "inform",
            "response_text": f"resposta: {state['message_body']}",
            "conversation_id": state["conversation_id"],
            "consulta_literais": ("a", "b"),
            "conjunto": {"x", "y"},
            "quando": datetime(2026, 10, 23, 12, 0, tzinfo=UTC),
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


def _remoto(app: FastAPI, **kw: Any) -> AgenteDeEntradaRemoto:
    kw.setdefault("retorno", RETORNO)
    return AgenteDeEntradaRemoto(
        "http://agente",
        "helena",
        chave=CHAVE,
        http=httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://agente"),
        **kw,
    )


def _patch_grafo(monkeypatch: pytest.MonkeyPatch) -> list[_Grafo]:
    grafos: list[_Grafo] = []

    def _build(config: Mapping[str, Any]) -> _Grafo:
        grafos.append(_Grafo(config))
        return grafos[-1]

    monkeypatch.setattr(helena_graph_module, "build", _build)
    monkeypatch.delenv("MAEZO_AGENTE_DE_ENTRADA_URL", raising=False)
    return grafos


def _montar(
    monkeypatch: pytest.MonkeyPatch,
    *,
    registro: RegistroDeRemetentes | None = None,
    entrada: Any = None,
    saver: InMemorySaver | None = None,
) -> _Montagem:
    grafos = _patch_grafo(monkeypatch)
    cliente_receptor, cliente_agente = _ClienteWhatsApp(), _ClienteWhatsApp()
    receptor = _despachante(cliente_receptor, checkpointer=Checkpointer(saver=saver) if saver else None)
    portador = _despachante(cliente_agente, checkpointer=Checkpointer(saver=saver) if saver else None)
    app_do_receptor = FastAPI()
    servico = ServicoDoAgente(
        entrada=entrada if entrada is not None else carregar_agente_de_entrada("helena"),
        dependencias=portador,
        chave=CHAVE,
        receptor=httpx.AsyncClient(transport=httpx.ASGITransport(app=app_do_receptor)),
    )
    app_do_agente = criar_app(servico, chave=CHAVE)
    remoto = _remoto(app_do_agente, registro=registro)
    registrar_rota_de_envio(app_do_receptor, remoto)
    receptor._entrada = remoto
    return _Montagem(
        receptor, cliente_receptor, cliente_agente, remoto, app_do_agente, app_do_receptor, grafos
    )


async def _post(app: FastAPI, rota: str, corpo: bytes, cabecalhos: Mapping[str, str]) -> httpx.Response:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://x") as http:
        return await http.post(rota, content=corpo, headers=dict(cabecalhos))


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
    assert estado_remoto == estado_local
    # O envio do turno remoto chegou ao numero cru DESTE turno pelo remetente do RECEPTOR, e so' a ele.
    esperado = [(NUMERO, "resposta: quero saber da carteirinha")]
    assert m.cliente_receptor.enviados == cliente_local.enviados == esperado
    assert m.cliente_agente.enviados == []


async def test_o_config_do_grafo_remoto_e_composto_pelo_servico(monkeypatch: pytest.MonkeyPatch) -> None:
    m = _montar(monkeypatch)
    await m.receptor.dispatch(InboundMessage(from_number=NUMERO, text="oi", message_id="wamid.2"))
    config = m.grafos[-1].config
    assert isinstance(config["whatsapp"], RemetenteRemoto)
    assert config["roteador_lucas_enabled"] is False
    assert config["agent_version"] == "helena@v0"
    assert config["dmn"] is not m.receptor.dmn


async def test_o_envio_devolve_so_booleanos_nunca_o_numero_nem_o_wamid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    m = _montar(monkeypatch)
    await m.receptor.dispatch(InboundMessage(from_number=NUMERO, text="oi", message_id="wamid.3"))
    assert m.grafos[-1].recibos == [{"enviada": True}]
    # E no corpo HTTP cru da rota de envio, nada de `contacts`, `messages`, `wamid` ou numero.
    corpos: list[bytes] = []

    async def _gravar(resposta: httpx.Response) -> None:
        await resposta.aread()
        corpos.append(resposta.content)

    remetente = _ClienteWhatsApp()
    gravador = _RemetenteCru(remetente)
    token = m.remoto.registro.registrar(gravador)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=m.app_do_receptor), event_hooks={"response": [_gravar]}
    ) as http:
        await RemetenteRemoto(http=http, chave=CHAVE, token=token, retorno=RETORNO).send("hk1_x", "t")
    assert len(corpos) == 1
    for proibido in (b"contacts", b"messages", b"wamid", b"wa_id", NUMERO.encode()):
        assert proibido not in corpos[0]


class _RemetenteCru:
    """Devolve o formato cru da Cloud API com o numero (o que a rota NAO pode repassar)."""

    def __init__(self, cliente: _ClienteWhatsApp) -> None:
        self._cliente = cliente

    async def send(self, to_hash: str, text: str) -> dict[str, Any]:
        return await self._cliente.send_message(NUMERO, text)


async def test_token_e_revogado_no_fim_do_turno(monkeypatch: pytest.MonkeyPatch) -> None:
    registro = RegistroDeRemetentes()
    m = _montar(monkeypatch, registro=registro)
    await m.receptor.dispatch(InboundMessage(from_number=NUMERO, text="oi", message_id="wamid.4"))
    assert m.cliente_receptor.enviados  # o envio do turno saiu enquanto o token valia
    assert len(registro) == 0


# --------------------------------------------------------------------------------------------------------
# O callback chega na replica certa
# --------------------------------------------------------------------------------------------------------


async def test_com_duas_replicas_o_envio_volta_a_que_abriu_o_turno(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_grafo(monkeypatch)
    replicas: dict[str, tuple[HelenaDispatcher, _ClienteWhatsApp, FastAPI]] = {}
    for nome in ("a", "b"):
        cliente = _ClienteWhatsApp()
        replicas[nome] = (_despachante(cliente), cliente, FastAPI())
    rotas = {f"http://replica-{n}": httpx.ASGITransport(app=app) for n, (_, _, app) in replicas.items()}
    servico = ServicoDoAgente(
        entrada=carregar_agente_de_entrada("helena"),
        dependencias=_despachante(_ClienteWhatsApp()),
        chave=CHAVE,
        receptor=httpx.AsyncClient(mounts=rotas),
    )
    app_do_agente = criar_app(servico, chave=CHAVE)
    for nome, (despachante, _cliente, app) in replicas.items():
        remoto = _remoto(app_do_agente, retorno=f"http://replica-{nome}")
        registrar_rota_de_envio(app, remoto)
        despachante._entrada = remoto

    await replicas["b"][0].dispatch(InboundMessage(from_number=NUMERO, text="oi", message_id="wamid.5"))
    assert replicas["b"][1].enviados == [(NUMERO, "resposta: oi")]
    assert replicas["a"][1].enviados == []


async def test_retorno_do_ecs_vem_do_metadata_da_task() -> None:
    def _metadata(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "http://169.254.170.2/v4/abc"
        return httpx.Response(200, json={"Networks": [{"IPv4Addresses": ["10.0.3.17"]}]})

    remoto = AgenteDeEntradaRemoto(
        "http://agente",
        "helena",
        chave=CHAVE,
        http=httpx.AsyncClient(transport=httpx.MockTransport(_metadata)),
        metadata_ecs="http://169.254.170.2/v4/abc",
    )
    assert await remoto.endereco_de_retorno() == "http://10.0.3.17:8080"


def test_sem_endereco_de_retorno_o_cliente_nao_e_construido() -> None:
    with pytest.raises(ValueError):
        AgenteDeEntradaRemoto("http://agente", "helena", chave=CHAVE)


# --------------------------------------------------------------------------------------------------------
# Agente fora do ar: o beneficiario recebe resposta
# --------------------------------------------------------------------------------------------------------


async def test_agente_fora_do_ar_gera_resposta_ao_beneficiario(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_grafo(monkeypatch)

    def _cai(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("sem rota", request=request)

    cliente = _ClienteWhatsApp()
    despachante = _despachante(cliente)
    despachante._entrada = AgenteDeEntradaRemoto(
        "http://agente",
        "helena",
        chave=CHAVE,
        http=httpx.AsyncClient(transport=httpx.MockTransport(_cai), base_url="http://agente"),
        retorno=RETORNO,
    )
    resultado = await despachante.dispatch(
        InboundMessage(from_number=NUMERO, text="oi", message_id="wamid.6")
    )
    assert cliente.enviados == [(NUMERO, AGENTE_INDISPONIVEL_TEXT)]
    assert resultado["agente_indisponivel"] is True
    assert "192" in AGENTE_INDISPONIVEL_TEXT


async def test_token_expirado_no_meio_do_turno_tambem_gera_resposta(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Relogio:
        agora = 1000.0

        def __call__(self) -> float:
            return self.agora

    relogio = _Relogio()
    m = _montar(monkeypatch, registro=RegistroDeRemetentes(ttl_s=10.0, relogio=relogio))
    original = _Grafo.ainvoke

    async def _depois_do_prazo(self: _Grafo, state: dict[str, Any], config: Any = None) -> dict[str, Any]:
        relogio.agora += 11.0
        return await original(self, state, config)

    monkeypatch.setattr(_Grafo, "ainvoke", _depois_do_prazo)
    resultado = await m.receptor.dispatch(InboundMessage(from_number=NUMERO, text="oi", message_id="wamid.7"))
    # A resposta do turno (pelo token vencido) nao saiu; o texto fixo saiu pelo remetente do RECEPTOR.
    assert m.cliente_receptor.enviados == [(NUMERO, AGENTE_INDISPONIVEL_TEXT)]
    assert m.cliente_agente.enviados == []
    assert resultado["agente_indisponivel"] is True


# --------------------------------------------------------------------------------------------------------
# Anti-replay e assinatura (pedido e resposta), nos dois sentidos
# --------------------------------------------------------------------------------------------------------


class _RemetenteGravador:
    def __init__(self) -> None:
        self.enviados: list[tuple[str, str]] = []

    async def send(self, to_hash: str, text: str) -> dict[str, Any]:
        self.enviados.append((to_hash, text))
        return {"ok": True}


def _rota_de_envio() -> tuple[AgenteDeEntradaRemoto, FastAPI, _RemetenteGravador, str]:
    remoto = AgenteDeEntradaRemoto("http://agente", "helena", chave=CHAVE, retorno=RETORNO)
    app = FastAPI()
    registrar_rota_de_envio(app, remoto)
    gravador = _RemetenteGravador()
    return remoto, app, gravador, remoto.registro.registrar(gravador)


async def test_rota_de_envio_entrega_so_ao_remetente_do_token() -> None:
    remoto, app, a, token_a = _rota_de_envio()
    b = _RemetenteGravador()
    remoto.registro.registrar(b)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app)) as http:
        recibo = await RemetenteRemoto(http=http, chave=CHAVE, token=token_a, retorno=RETORNO).send(
            "hk1_x", "t"
        )
    assert recibo == {"enviada": True}
    assert a.enviados == [("hk1_x", "t")]
    assert b.enviados == []


@pytest.mark.parametrize("caso", ["sem_assinatura", "chave_errada", "token_desconhecido", "versao_errada"])
async def test_rota_de_envio_recusa_sem_detalhe(caso: str) -> None:
    _remoto_, app, gravador, token = _rota_de_envio()
    corpo = fio.codificar(
        {"token": "outro" if caso == "token_desconhecido" else token, "to_hash": "h", "text": "t"}
    )
    chave = fio.derivar_chave("outra") if caso == "chave_errada" else CHAVE
    cabecalhos = fio.cabecalhos_do_pedido(chave, fio.ROTA_ENVIO, corpo)
    if caso == "sem_assinatura":
        del cabecalhos[fio.CABECALHO_ASSINATURA]
    if caso == "versao_errada":
        cabecalhos[fio.CABECALHO_VERSAO] = "maezo-agente-remoto-v0"
    resposta = await _post(app, fio.ROTA_ENVIO, corpo, cabecalhos)
    assert resposta.status_code == (404 if caso == "token_desconhecido" else 401)
    assert resposta.content == b""
    assert gravador.enviados == []


async def test_replay_e_recusado_na_rota_de_envio_do_receptor() -> None:
    _remoto_, app, gravador, token = _rota_de_envio()
    corpo = fio.codificar({"token": token, "to_hash": "h", "text": "t"})
    cabecalhos = fio.cabecalhos_do_pedido(CHAVE, fio.ROTA_ENVIO, corpo)
    primeiro = await _post(app, fio.ROTA_ENVIO, corpo, cabecalhos)
    replay = await _post(app, fio.ROTA_ENVIO, corpo, cabecalhos)
    assert (primeiro.status_code, replay.status_code) == (200, 401)
    assert gravador.enviados == [("h", "t")]


async def test_replay_e_recusado_no_servico_do_agente(monkeypatch: pytest.MonkeyPatch) -> None:
    m = _montar(monkeypatch)
    corpo = fio.codificar({"texto": "quero falar com um atendente"})
    cabecalhos = fio.cabecalhos_do_pedido(CHAVE, fio.ROTA_PEDIDO_DE_PESSOA, corpo)
    primeiro = await _post(m.app_do_agente, fio.ROTA_PEDIDO_DE_PESSOA, corpo, cabecalhos)
    replay = await _post(m.app_do_agente, fio.ROTA_PEDIDO_DE_PESSOA, corpo, cabecalhos)
    assert (primeiro.status_code, replay.status_code) == (200, 401)


async def test_carimbo_velho_e_recusado(monkeypatch: pytest.MonkeyPatch) -> None:
    m = _montar(monkeypatch)
    corpo = fio.codificar({"texto": "x"})
    velho = fio.cabecalhos_do_pedido(
        CHAVE, fio.ROTA_PEDIDO_DE_PESSOA, corpo, relogio=lambda: time.time() - 61
    )
    futuro = fio.cabecalhos_do_pedido(
        CHAVE, fio.ROTA_PEDIDO_DE_PESSOA, corpo, relogio=lambda: time.time() + 61
    )
    for cabecalhos in (velho, futuro):
        resposta = await _post(m.app_do_agente, fio.ROTA_PEDIDO_DE_PESSOA, corpo, cabecalhos)
        assert (resposta.status_code, resposta.content) == (401, b"")


async def test_rota_trocada_e_recusada(monkeypatch: pytest.MonkeyPatch) -> None:
    m = _montar(monkeypatch)
    corpo = fio.codificar({"texto": "meu nome"})
    assinado_para_outra = fio.cabecalhos_do_pedido(CHAVE, fio.ROTA_PEDIDO_DE_PESSOA, corpo)
    resposta = await _post(m.app_do_agente, fio.ROTA_RESPOSTA_DE_CADASTRO, corpo, assinado_para_outra)
    assert (resposta.status_code, resposta.content) == (401, b"")


async def test_resposta_de_outro_pedido_e_recusada(monkeypatch: pytest.MonkeyPatch) -> None:
    m = _montar(monkeypatch)
    corpo = fio.codificar({"texto": "x"})
    pedido_a = fio.cabecalhos_do_pedido(CHAVE, fio.ROTA_PEDIDO_DE_PESSOA, corpo)
    pedido_b = fio.cabecalhos_do_pedido(CHAVE, fio.ROTA_PEDIDO_DE_PESSOA, corpo)
    resposta_a = await _post(m.app_do_agente, fio.ROTA_PEDIDO_DE_PESSOA, corpo, pedido_a)
    assert (
        ler_resposta(resposta_a, CHAVE, fio.ROTA_PEDIDO_DE_PESSOA, pedido_a[fio.CABECALHO_ASSINATURA])
        is False
    )
    with pytest.raises(AgenteRemotoIndisponivelError):
        ler_resposta(resposta_a, CHAVE, fio.ROTA_PEDIDO_DE_PESSOA, pedido_b[fio.CABECALHO_ASSINATURA])


async def test_servico_do_agente_recusa_pedido_sem_assinatura(monkeypatch: pytest.MonkeyPatch) -> None:
    m = _montar(monkeypatch)
    corpo = fio.codificar({"texto": "quero falar com um atendente"})
    sem = await _post(m.app_do_agente, fio.ROTA_PEDIDO_DE_PESSOA, corpo, {})
    assert (sem.status_code, sem.content) == (401, b"")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=m.app_do_agente), base_url="http://a"
    ) as http:
        assert (await http.get("/healthz")).status_code == 200


async def test_resposta_sem_assinatura_do_servico_e_falha_externa() -> None:
    falso = FastAPI()

    @falso.post(fio.ROTA_PEDIDO_DE_PESSOA)
    async def _sem_assinatura() -> dict[str, Any]:
        return {"resultado": True}

    with pytest.raises(AgenteRemotoIndisponivelError):
        await _remoto(falso).postar(fio.ROTA_PEDIDO_DE_PESSOA, {"texto": "x"})


async def test_rede_fora_vira_falha_externa() -> None:
    def _cai(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("sem rota", request=request)

    remoto = AgenteDeEntradaRemoto(
        "http://agente",
        "helena",
        chave=CHAVE,
        http=httpx.AsyncClient(transport=httpx.MockTransport(_cai), base_url="http://agente"),
        retorno=RETORNO,
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
    m = _montar(monkeypatch, saver=InMemorySaver())
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


async def test_pedido_de_pessoa_vem_do_turno_sem_outra_chamada(monkeypatch: pytest.MonkeyPatch) -> None:
    m = _montar(monkeypatch)
    texto = "quero falar com um atendente"
    await m.receptor.dispatch(InboundMessage(from_number=NUMERO, text=texto, message_id="wamid.8"))
    esperado = carregar_agente_de_entrada("helena").eh_pedido_explicito_de_pessoa(texto)
    assert m.remoto.eh_pedido_explicito_de_pessoa(texto) is esperado


async def test_pedido_de_pessoa_fora_do_cache_e_antecipado_de_forma_assincrona(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    m = _montar(monkeypatch)
    helena = carregar_agente_de_entrada("helena")
    for texto in ("quero falar com um atendente", "qual o valor do boleto"):
        # Sem antecipar: o metodo sincrono nunca faz I/O; levanta (erro de fiacao, alto).
        with pytest.raises(AgenteRemotoIndisponivelError):
            m.remoto.eh_pedido_explicito_de_pessoa(texto)
        await m.remoto.antecipar_pedido_de_pessoa(texto)
        assert m.remoto.eh_pedido_explicito_de_pessoa(texto) is helena.eh_pedido_explicito_de_pessoa(texto)


# --------------------------------------------------------------------------------------------------------
# Os nos fora do grafo e a resposta de cadastro, com um agente duble do lado do servico
# --------------------------------------------------------------------------------------------------------


class _AgenteDuble:
    agent_id = "helena"
    agent_version = "helena@duble"

    def __init__(self, *, resultado_sem_fio: bool = False) -> None:
        self.chamadas: list[tuple[str, Any]] = []
        self._resultado_sem_fio = resultado_sem_fio

    async def escalar_identidade(
        self, dependencias: Mapping[str, Any], estado: dict[str, Any], *, motivo: str
    ) -> Mapping[str, Any]:
        self.chamadas.append(("identidade", (dict(dependencias), dict(estado), motivo)))
        if self._resultado_sem_fio:
            return {"objeto": object()}
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


_CONVERSA = "wa:amh:hk1_" + "b" * 64


async def test_escalar_identidade_remoto_envia_pelo_remetente_do_turno(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    duble = _AgenteDuble()
    m = _montar(monkeypatch, entrada=duble)
    gravador = _RemetenteGravador()
    resultado = await m.remoto.escalar_identidade(
        {"whatsapp": gravador, "roteador_lucas_enabled": True, "inference": object()},
        {"tenant_id": "amh", "conversation_id": _CONVERSA, "canal": "whatsapp"},
        motivo="identidade_nao_verificada",
    )
    assert resultado == {"escalation_started": True, "start_desfecho": "iniciado"}
    dependencias, estado, motivo = duble.chamadas[0][1]
    assert motivo == "identidade_nao_verificada"
    assert estado["conversation_id"] == _CONVERSA
    assert dependencias["roteador_lucas_enabled"] is True
    assert isinstance(dependencias["whatsapp"], RemetenteRemoto)
    assert gravador.enviados == [("hk1_" + "b" * 64, "um atendente vai falar")]
    assert len(m.remoto.registro) == 0


@pytest.mark.parametrize("estado", [{"tenant_id": "outro"}, {"conversation_id": _CONVERSA}])
async def test_estado_sem_tenant_ou_de_outro_tenant_e_recusado(
    monkeypatch: pytest.MonkeyPatch, estado: dict[str, Any]
) -> None:
    duble = _AgenteDuble()
    m = _montar(monkeypatch, entrada=duble)
    with pytest.raises(PermissionError):
        await m.remoto.escalar_e_responder({"whatsapp": None}, estado)
    assert duble.chamadas == []


async def test_resultado_sem_representacao_no_fio_volta_como_erro_do_protocolo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    m = _montar(monkeypatch, entrada=_AgenteDuble(resultado_sem_fio=True))
    with pytest.raises(TypeError):
        await m.remoto.escalar_identidade({"whatsapp": None}, {"tenant_id": "amh"}, motivo="x")


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
    monkeypatch.setenv("MAEZO_RECEPTOR_URL", "http://receptor:8080")
    monkeypatch.delenv("ECS_CONTAINER_METADATA_URI_V4", raising=False)
    despachante = _despachante(_ClienteWhatsApp())
    assert isinstance(despachante.agente_de_entrada, AgenteDeEntradaRemoto)
    caminhos = {getattr(r, "path", None) for r in create_app(_settings(), dispatcher=despachante).routes}
    assert fio.ROTA_ENVIO in caminhos


@pytest.mark.parametrize("falta", ["PHI_HMAC_KEY", "retorno"])
def test_com_a_env_e_sem_chave_ou_retorno_o_despachante_nao_e_construido(
    monkeypatch: pytest.MonkeyPatch, falta: str
) -> None:
    monkeypatch.setenv("MAEZO_AGENTE_DE_ENTRADA_URL", "http://agent-helena:8000")
    monkeypatch.setenv("PHI_HMAC_KEY", "chave-phi-de-teste")
    monkeypatch.setenv("MAEZO_RECEPTOR_URL", "http://receptor:8080")
    monkeypatch.delenv("ECS_CONTAINER_METADATA_URI_V4", raising=False)
    if falta == "PHI_HMAC_KEY":
        monkeypatch.delenv("PHI_HMAC_KEY")
    else:
        monkeypatch.delenv("MAEZO_RECEPTOR_URL")
    with pytest.raises(ValueError):
        _despachante(_ClienteWhatsApp())


# --------------------------------------------------------------------------------------------------------
# Settings do servico do agente: sem os segredos do webhook nem a chave A2A
# --------------------------------------------------------------------------------------------------------


def _sem_segredos_do_webhook(monkeypatch: pytest.MonkeyPatch) -> None:
    for nome in list(os.environ):
        if nome.startswith("MAEZO_A2A_CARD_SIGNING_KEY") or nome in (
            "WHATSAPP_APP_SECRET",
            "WHATSAPP_VERIFY_TOKEN",
        ):
            monkeypatch.delenv(nome)


async def test_servico_do_agente_compoe_sem_os_segredos_do_webhook_nem_a2a(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _sem_segredos_do_webhook(monkeypatch)

    async def _ok(conn_string: str) -> Checkpointer:
        return Checkpointer(saver=InMemorySaver())

    monkeypatch.setattr(Checkpointer, "connect_and_setup", classmethod(lambda cls, dsn: _ok(dsn)))
    settings = AgenteRemotoSettings(
        tenant_id="amh",
        phi_hmac_key="chave-phi-de-teste",
        database_url="postgresql+asyncpg://user:pw@localhost:5432/maezo",
        runtime_mode="local",
    )
    estado = await svc.build_agent_dependencies(settings)
    assert estado.dispatcher is not None, estado.dispatcher_error
    assert estado.lucas_turno is None and estado.acesso is None


def test_servico_do_agente_descarta_segredo_do_webhook_vazado(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WHATSAPP_APP_SECRET", "vazou")
    monkeypatch.setenv("WHATSAPP_VERIFY_TOKEN", "vazou")
    settings = AgenteRemotoSettings(tenant_id="amh")
    assert settings.app_secret == "" and settings.verify_token == ""


def test_receptor_continua_recusando_subir_sem_os_segredos_do_webhook(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _sem_segredos_do_webhook(monkeypatch)
    with pytest.raises(ValidationError):
        WhatsAppWebhookSettings(tenant_id="amh")  # type: ignore[call-arg]
