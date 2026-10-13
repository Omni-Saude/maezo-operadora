"""Composicao do simulador: as cinco trocas sobre o despachante, com dubles (nenhuma rede)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from maezo.agents.lucas.fonte_cobranca import FatosCobranca, Indisponivel
from maezo.gateway.effect_pep import DecisionContext
from maezo.gateway.pseudonymizer import Pseudonymizer
from maezo.gateway.seams import GatedSeam, SeamContext
from maezo.gateway.seams.cibseven import gate_cibseven
from maezo.gateway.seams.dmn import gate_dmn
from maezo.gateway.seams.inference import gate_inference
from maezo.platform.webhooks.whatsapp import acesso as acesso_mod
from maezo.platform.webhooks.whatsapp.dispatch import HelenaDispatcher, InboundMessage, _ScopedWhatsAppSender
from maezo.ports.document_resolution import DocumentResolution
from maezo.ports.errors import PortResult
from maezo.qa.simulador import composicao as comp
from maezo.qa.simulador.registro import Turno
from maezo.tools.mcp_cibseven.transport import CibSevenError, FakeCibSevenTransport, ProcessNotFoundError
from maezo.tools.workers.dmn_transport import FakeDmnTransport
from tests.support.audit_fakes import FakeStartAuditSink

from .apoio_simulador import CPF_SINTETICO, sujeito

_TELEFONE = "5511900000500"
_CID = "wa:amh:hk1_abc"


def _seam(principal: str = "helena") -> SeamContext:
    return SeamContext(tenant="amh", principal=principal, decision=DecisionContext())


class _Dedup:
    def outbound_key(self, message_id: str, *, occurrence: int = 1) -> str:
        return f"out:{message_id}:{occurrence}"


class _Fonte:
    def __init__(self, resultado: Any) -> None:
        self.resultado = resultado
        self.extra = "delegado"

    async def fatos(self, pseudo_id: str, competencia: str | None, *, phone_hash: str | None = None) -> Any:
        return self.resultado


class _Identidade:
    def __init__(self) -> None:
        self.invalidadas: list[str] = []

    async def resolver_com_desfecho(self, numero: str, *, conversation_id: str, pseudo_id: str) -> Any:
        return SimpleNamespace(identidade=None, desfecho="reconhecido")

    def invalidar(self, conversation_id: str) -> None:
        self.invalidadas.append(conversation_id)

    async def aclose(self) -> None:
        return None


class _Consentimentos:
    def __init__(self) -> None:
        self.gravados: list[str] = []

    async def record(self, ref: str, **kwargs: Any) -> Any:
        self.gravados.append(ref)
        return PortResult.ok(SimpleNamespace(consent_ref="c-1", recorded=True))


class _Documentos:
    def __init__(self, resultado: DocumentResolution | None) -> None:
        self.resultado = resultado
        self.chamadas: list[dict[str, Any]] = []

    async def resolve_by_document(self, **kwargs: Any) -> Any:
        self.chamadas.append(kwargs)
        if self.resultado is None:
            return PortResult.refused(comp.PortFailureReason.UPSTREAM_UNAVAILABLE)
        return PortResult.ok(self.resultado)


class _Hasher:
    def hash_cpf(self, cpf: str) -> str:
        return f"h:{cpf}"

    def hash_cpf_nascimento(self, cpf: str, nascimento: str) -> str:
        return f"h:{cpf}:{nascimento}"


def _acesso(documentos: _Documentos | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        _consents=_Consentimentos(),
        _documentos=documentos
        if documentos is not None
        else _Documentos(DocumentResolution("unico", "ref-1")),
        _hasher=_Hasher(),
        _hash_scheme="amh-subject-verify-v1",
        _purpose="atendimento_whatsapp",
        _tenant_amh="austa_operadora",
        _prazo=8.0,
    )


def _estado(*, dmn: Any = None, acesso: Any = None, com_lucas: bool = True) -> SimpleNamespace:
    dispatcher = SimpleNamespace(
        tenant_id="amh",
        dedup=_Dedup(),
        whatsapp_client=object(),
        recipient_vault=object(),
        cibseven=gate_cibseven(FakeCibSevenTransport(), _seam()),
        dmn=dmn if dmn is not None else gate_dmn(FakeDmnTransport(), _seam()),
        inference=gate_inference(object(), _seam()),
        identidade=_Identidade(),
        acesso=acesso,
        checkpointer=None,
    )
    lucas = (
        SimpleNamespace(
            cibseven=gate_cibseven(FakeCibSevenTransport(), _seam("lucas")),
            dmn=gate_dmn(FakeDmnTransport(), _seam("lucas")),
            inference=gate_inference(object(), _seam("lucas")),
            fonte=_Fonte(Indisponivel("fonte_indisponivel")),
        )
        if com_lucas
        else None
    )
    return SimpleNamespace(dispatcher=dispatcher, lucas_turno=lucas, dispatcher_error=None)


def _trocar(estado: SimpleNamespace, **kwargs: Any) -> comp.Composicao:
    return comp.trocar_bordas(estado, modo_consentimento="somente-leitura", **kwargs)  # type: ignore[arg-type]


def _contexto(message_id: str = "m1") -> comp.ContextoDoTurno:
    return comp.ContextoDoTurno(
        turno=Turno(1, "persona", "oi", "outro"), message_id=message_id, conversation_id=_CID
    )


# --- as trocas ------------------------------------------------------------------------------------
def test_trocar_bordas_faz_as_cinco_trocas_e_mantem_o_gate() -> None:
    estado = _estado(acesso=_acesso())
    composicao = _trocar(estado)
    d = estado.dispatcher
    assert isinstance(d.whatsapp_client, comp.CapturaDeEnvios)
    assert d.recipient_vault is None
    for motor, principal in ((d.cibseven, "helena"), (estado.lucas_turno.cibseven, "lucas")):
        assert isinstance(motor, GatedSeam) and isinstance(motor.inner, comp.InterceptorDeEscalonamento)
        assert motor.seam_context.principal == principal
    assert isinstance(estado.lucas_turno.fonte, comp.FonteGravada)
    assert isinstance(d.identidade, comp.IdentidadeGravada)
    assert isinstance(d.acesso._consents, comp.ConsentimentoControlado)
    assert composicao.resolver is not None and composicao.consentimento is not None
    assert composicao.motor_real is not d.cibseven


def test_sem_despachante_recusa() -> None:
    with pytest.raises(comp.ComposicaoInvalidaError):
        comp.trocar_bordas(
            SimpleNamespace(dispatcher=None, dispatcher_error="x"),  # type: ignore[arg-type]
            modo_consentimento="somente-leitura",
        )


def test_costura_sem_gate_recusa() -> None:
    with pytest.raises(comp.ComposicaoInvalidaError):
        _trocar(_estado(dmn=FakeDmnTransport()))


def test_sem_lucas_nem_acesso_ainda_compoe() -> None:
    composicao = _trocar(_estado(com_lucas=False))
    assert composicao.resolver is None and composicao.consentimento is None


def test_inferencia_crua_e_o_inner_do_gate() -> None:
    composicao = _trocar(_estado())
    assert comp.inferencia_crua(composicao) is composicao.dispatcher.inference.inner


# --- 1. captura -----------------------------------------------------------------------------------
async def test_captura_marca_helena_lucas_e_acesso() -> None:
    captura = comp.CapturaDeEnvios(chave_de_saida=lambda mid, n: f"out:{mid}:{n}")
    contexto = _contexto("m9")
    with comp.no_turno(contexto):
        await captura.send_message(_TELEFONE, "oi da helena", idempotency_key="out:m9:1")
        await captura.send_message(_TELEFONE, "fatos do lucas", idempotency_key="out:lucas:m9:2")
        await captura.send_message(_TELEFONE, acesso_mod.PEDIDO_CPF, idempotency_key="out:m9:2")
    assert [(m.agente, m.texto) for m in contexto.turno.mensagens] == [
        ("helena", "oi da helena"),
        ("lucas", "fatos do lucas"),
        ("acesso", acesso_mod.PEDIDO_CPF),
    ]


async def test_captura_recusa_destino_real_e_conta_envio_fora_de_turno() -> None:
    captura = comp.CapturaDeEnvios(chave_de_saida=None)
    with pytest.raises(comp.DestinoForaDaFaixaError):
        await captura.send_message("5511987654321", "x")
    resposta = await captura.send_message(_TELEFONE, "x")
    assert captura.fora_de_turno == 1 and resposta["simulado"] is True


async def test_o_remetente_real_do_turno_entrega_na_captura() -> None:
    captura = comp.CapturaDeEnvios(chave_de_saida=lambda mid, n: f"out:{mid}:{n}")
    remetente = _ScopedWhatsAppSender(
        raw_to=_TELEFONE,
        expected_hash="hk1_x",
        client=captura,  # type: ignore[arg-type]
        idempotency_key_for=lambda n: f"out:m1:{n}",
    )
    contexto = _contexto()
    with comp.no_turno(contexto):
        await remetente.send("hk1_x", "resposta")
    assert contexto.turno.textos("helena") == ["resposta"]


async def test_despachante_real_responde_pela_captura() -> None:
    """O despachante REAL da Helena (dubles nas portas) com a captura no lugar da Meta."""

    class _Inferencia:
        def __init__(self) -> None:
            self.respostas = [
                '{"intent": "information", "population": "none", "psychosocial_risk": false}',
                "ok",
            ]

        async def generate(self, prompt: str, **_kwargs: Any) -> str:
            return self.respostas.pop(0) if self.respostas else "fallback"

    captura = comp.CapturaDeEnvios(chave_de_saida=None)
    dispatcher = HelenaDispatcher(
        tenant_id="amh",
        inference=_Inferencia(),  # type: ignore[arg-type]
        dmn=FakeDmnTransport(),
        cibseven=FakeCibSevenTransport(),
        whatsapp_client=captura,  # type: ignore[arg-type]
        pseudonymizer=Pseudonymizer(),
        audit_sink=FakeStartAuditSink(),
        seam_context=_seam(),
    )
    contexto = _contexto()
    with comp.no_turno(contexto):
        await dispatcher.dispatch(InboundMessage(from_number=_TELEFONE, text="oi", message_id="m1"))
    assert contexto.turno.mensagens, "a resposta da Helena tem de chegar a captura"
    assert {m.agente for m in contexto.turno.mensagens} <= {"helena", "acesso"}
    assert captura.fora_de_turno == 0


# --- 2. interceptador -----------------------------------------------------------------------------
async def test_interceptador_finge_so_o_escalonamento() -> None:
    registro = comp.RegistroDeEscalonamentos()
    real = FakeCibSevenTransport()
    motor = gate_cibseven(comp.InterceptorDeEscalonamento(real, registro), _seam())
    chave = f"ESC-amh-{_CID}"
    contexto = _contexto()
    with comp.no_turno(contexto):
        instancia = await motor.start_process_instance(
            comp.PROCESSO_ESCALONAMENTO,
            chave,
            {"source_agent_id": "helena", "motivo_categoria": "solicitacao_humano", "resumo_contexto": "r"},
        )
    assert instancia.instance_id == "simulador-1" and registro.total == 1
    (esc,) = contexto.turno.escalonamentos
    assert (esc.agente, esc.motivo, esc.business_key) == ("helena", "solicitacao_humano", chave)
    interceptador = motor.inner
    assert await interceptador.find_active_instance(chave) == instancia
    assert await interceptador.find_any_instance(chave) == instancia
    status = await interceptador.get_process_status(chave)
    assert status.variables["motivo_categoria"] == "solicitacao_humano"
    assert await interceptador.read_historic_variables("simulador-1", ("x",)) is None
    await interceptador.correlate_message("msg", chave, {})
    assert real.correlate_calls == []
    # o motor real nunca viu nada
    assert await real.find_active_instance(chave) is None


async def test_interceptador_recusa_qualquer_outro_inicio_e_passa_leituras() -> None:
    real = FakeCibSevenTransport()
    interceptador = comp.InterceptorDeEscalonamento(real, comp.RegistroDeEscalonamentos())
    with pytest.raises(CibSevenError):
        await interceptador.start_process_instance("SP-OP-PAGTO-001", "PAG-1", {})
    with pytest.raises(CibSevenError):
        await interceptador.start_process_instance(comp.PROCESSO_ESCALONAMENTO, "OUTRA-1", {})
    with pytest.raises(ProcessNotFoundError):
        await interceptador.get_process_status("ESC-amh-nada")
    assert await interceptador.find_active_instance("OUTRA-1") is None  # foi ao transporte real
    await interceptador.close()


def test_registro_esquece_os_casos_da_conversa() -> None:
    registro = comp.RegistroDeEscalonamentos()
    registro.registrar(f"ESC-amh-{_CID}", {})
    registro.registrar(f"ESC-amh-{_CID}-clin", {})
    registro.esquecer("amh", _CID)
    assert registro.ativa(f"ESC-amh-{_CID}") is None and registro.ativa(f"ESC-amh-{_CID}-clin") is None


# --- 3. fonte -------------------------------------------------------------------------------------
async def test_fonte_gravada_guarda_o_gabarito_e_devolve_o_mesmo_objeto() -> None:
    fatos = FatosCobranca(
        status_conciliado=True, ciclos_sem_conciliacao=0, numero_boleto="****1", cnab_ref="x"
    )
    fonte = comp.FonteGravada(_Fonte(fatos))
    contexto = _contexto()
    with comp.no_turno(contexto):
        assert await fonte.fatos("p", "2026-07", phone_hash=None) is fatos
    (gravado,) = contexto.turno.fatos
    assert gravado["competencia_pedida"] == "2026-07" and gravado["competencias"] == []
    assert fonte.extra == "delegado"
    indisponivel = comp.FonteGravada(_Fonte(Indisponivel("sem_consentimento")))
    with comp.no_turno(contexto):
        await indisponivel.fatos("p", None)
    assert contexto.turno.fonte_indisponivel == ["sem_consentimento"]


# --- 4. identidade --------------------------------------------------------------------------------
async def test_identidade_gravada_registra_e_delega() -> None:
    inner = _Identidade()
    identidade = comp.IdentidadeGravada(inner)
    contexto = _contexto()
    with comp.no_turno(contexto):
        await identidade.resolver_com_desfecho(_TELEFONE, conversation_id=_CID, pseudo_id="p")
    identidade.invalidar(_CID)
    await identidade.aclose()
    assert contexto.turno.identidade == ["reconhecido"] and inner.invalidadas == [_CID]


# --- 5. consentimento -----------------------------------------------------------------------------
async def test_somente_leitura_so_grava_o_dono() -> None:
    inner = _Consentimentos()
    controlado = comp.ConsentimentoControlado(
        inner, modo="somente-leitura", refs_completas=frozenset({"dono"})
    )
    recusa = await controlado.record("terceiro", decision="granted")
    assert not recusa.succeeded and inner.gravados == []
    assert (await controlado.record("dono", decision="granted")).succeeded
    assert inner.gravados == ["dono"]
    assert (controlado.contagem.gravados, controlado.contagem.nao_gravados) == (1, 1)


async def test_completo_grava_todos_e_modo_invalido_recusa() -> None:
    inner = _Consentimentos()
    controlado = comp.ConsentimentoControlado(inner, modo="completo", refs_completas=frozenset())
    await controlado.record("terceiro")
    assert inner.gravados == ["terceiro"]
    with pytest.raises(ValueError):
        comp.ConsentimentoControlado(inner, modo="tudo", refs_completas=frozenset())  # type: ignore[arg-type]


def test_autorizar_consentimento_depois_do_preflight() -> None:
    composicao = _trocar(_estado(acesso=_acesso()))
    comp.autorizar_consentimento(composicao, frozenset({"ref-dono"}))
    assert composicao.consentimento is not None and composicao.consentimento.permite("ref-dono")
    assert not composicao.consentimento.permite("ref-outro")


# --- preflight ------------------------------------------------------------------------------------
async def test_resolvedor_de_documento_usa_o_contrato_do_acesso() -> None:
    documentos = _Documentos(DocumentResolution("unico", "ref-1"))
    resolver = comp.resolvedor_de_documento(_acesso(documentos))
    assert await resolver(sujeito()) == "ref-1"
    (chamada,) = documentos.chamadas
    assert chamada["document_hash"] == f"h:{CPF_SINTETICO}"
    assert chamada["document_dob_hash"] == f"h:{CPF_SINTETICO}:19800314"
    assert chamada["purpose_of_use"] == "atendimento_whatsapp"


async def test_resolvedor_de_documento_nenhum_recusa_e_sem_porta() -> None:
    assert (
        await comp.resolvedor_de_documento(_acesso(_Documentos(DocumentResolution("nenhum"))))(sujeito())
        is None
    )
    assert await comp.resolvedor_de_documento(_acesso(_Documentos(None)))(sujeito()) is None
    sem_porta = _acesso()
    sem_porta._documentos = None
    with pytest.raises(comp.ComposicaoInvalidaError):
        comp.resolvedor_de_documento(sem_porta)
