"""O roteador no despachante: desligado = o caminho de hoje; ligado (onda c) = SOMBRA.

ADR-0062, plano `docs/plans/lucas-numero-unico.md` §6(c). A propriedade que esta suite fixa e' a
que torna a onda mergeavel sozinha: com o roteador ligado, a RESPOSTA ao beneficiario e o estado
devolvido pelo turno sao identicos aos do roteador desligado, caso a caso. O que muda e' so' o que
fica gravado em `conversa_agente_ativo` e o que aparece no log (booleanos, nunca texto).
"""

from __future__ import annotations

import dataclasses
from datetime import timedelta
from typing import Any

import pytest
import structlog

from maezo.gateway.pseudonymizer import Pseudonymizer
from maezo.platform.webhooks.whatsapp import pre_roteamento
from maezo.platform.webhooks.whatsapp.dispatch import HelenaDispatcher, InboundMessage, InboundNonTextMessage
from maezo.platform.webhooks.whatsapp.limite import LimitadorDeVolume
from maezo.platform.webhooks.whatsapp.roteamento import ConversaRouter, LinhaAgenteAtivo
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport
from maezo.tools.workers.dmn_transport import FakeDmnTransport
from tests.support.audit_fakes import FakeStartAuditSink
from tests.support.roteamento_fakes import FakeAgenteAtivoStore

_TELEFONE = "5511900000123"  # faixa sintetica de teste


class _FakeInference:
    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)

    async def generate(
        self,
        prompt: str,
        *,
        phi: bool = False,
        agent_id: str | None = None,
        tenant_id: str | None = None,
        task_kind: str | None = None,
    ) -> str:
        return self._responses.pop(0) if self._responses else ""


class _FakeWhatsAppClient:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    async def send_message(self, to: str, text: str, *, idempotency_key: str | None = None) -> dict[str, Any]:
        self.sent.append((to, text))
        return {"messages": [{"id": "wamid.reply.1"}]}


#: Cada cenario: (respostas do modelo, tabelas DMN, texto do beneficiario, transicao esperada).
_INFO = '{"intent": "information", "population": "none", "psychosocial_risk": false}'
_SINTOMA_GRAVE = (
    '{"intent": "symptom", "population": "adult", "sintoma_codigo": "dor_toracica", '
    '"intensidade": "grave", "psychosocial_risk": false}'
)
_HUMANO = '{"intent": "human_request", "population": "none", "psychosocial_risk": false}'
CENARIOS: dict[str, tuple[list[str], dict[str, list[dict[str, Any]]], str, str]] = {
    "informacao": (
        [_INFO, "resposta"],
        {"triage_redflag_adult": [{"red_flag": False, "conduta": "CONTINUE"}]},
        "qual o horario da central?",
        "inicio",
    ),
    "red_flag": (
        [_SINTOMA_GRAVE, "resumo", "um humano vai continuar"],
        {"triage_redflag_adult": [{"red_flag": True, "conduta": "PS_IMEDIATO", "severidade": "grave"}]},
        "dor forte no peito",
        "retorno_saude",
    ),
    "pedido_de_pessoa": (
        [_HUMANO, "resumo", "um humano vai continuar"],
        {},
        "quero falar com um atendente",
        "retorno_pedido_humano",
    ),
    "classify_falhou": (
        ["isto nao e json", "resumo", "desculpe"],
        {},
        "oi",
        "retorno_falha",
    ),
}


def _dispatcher(
    cenario: str, *, roteador: ConversaRouter | None, client: _FakeWhatsAppClient, **extra: Any
) -> HelenaDispatcher:
    respostas, tabelas, _, _ = CENARIOS[cenario]
    dmn = FakeDmnTransport()
    for chave, linhas in tabelas.items():
        dmn.register(chave, linhas)
    return HelenaDispatcher(
        tenant_id="amh",
        inference=_FakeInference(respostas),  # type: ignore[arg-type]
        dmn=dmn,
        cibseven=FakeCibSevenTransport(),
        whatsapp_client=client,  # type: ignore[arg-type]
        pseudonymizer=Pseudonymizer(),
        audit_sink=FakeStartAuditSink(),
        roteador=roteador,
        **extra,
    )


def _sem_relogio(valor: Any) -> Any:
    """O estado do turno, sem os carimbos de relogio de parede (`memoria_clinica.gravado_em`):
    dois turnos identicos rodados um depois do outro diferem so' nisso."""
    if isinstance(valor, dict):
        return {k: _sem_relogio(v) for k, v in valor.items() if k != "gravado_em"}
    if isinstance(valor, list):
        return [_sem_relogio(v) for v in valor]
    return valor


def _roteador(store: FakeAgenteAtivoStore) -> ConversaRouter:
    return ConversaRouter(
        tenant_id="amh", store=store, lexicos=pre_roteamento.carregar(), inatividade=timedelta(minutes=60)
    )


def test_o_default_do_despachante_e_sem_roteador() -> None:
    campo = next(f for f in dataclasses.fields(HelenaDispatcher) if f.name == "roteador")
    assert campo.default is None


@pytest.mark.parametrize("cenario", list(CENARIOS))
async def test_sombra_e_identica_ao_desligado_caso_a_caso(cenario: str) -> None:
    texto = CENARIOS[cenario][2]
    mensagem = InboundMessage(from_number=_TELEFONE, text=texto, message_id="wamid.in.1")

    desligado_client = _FakeWhatsAppClient()
    desligado = await _dispatcher(cenario, roteador=None, client=desligado_client).dispatch(mensagem)

    store = FakeAgenteAtivoStore()
    sombra_client = _FakeWhatsAppClient()
    sombra = await _dispatcher(cenario, roteador=_roteador(store), client=sombra_client).dispatch(mensagem)

    assert _sem_relogio(sombra) == _sem_relogio(desligado)
    assert sombra_client.sent == desligado_client.sent
    linha = store.linhas[desligado["conversation_id"]]
    assert (linha.agente_ativo, linha.transicao_motivo) == ("helena", CENARIOS[cenario][3])


async def test_sombra_loga_so_booleanos_nunca_o_texto_nem_o_telefone() -> None:
    texto = "estou com dor no peito e quero um atendente"
    store = FakeAgenteAtivoStore()
    with structlog.testing.capture_logs() as logs:
        await _dispatcher("informacao", roteador=_roteador(store), client=_FakeWhatsAppClient()).dispatch(
            InboundMessage(from_number=_TELEFONE, text=texto, message_id="wamid.in.2")
        )
    sinais = [e for e in logs if e["event"] == "roteador_sinais_lexicos"]
    assert len(sinais) == 1
    assert sinais[0]["pedido_humano"] is True and sinais[0]["sinal_saude"] is True
    decidiu = [e for e in logs if e["event"] == "roteador_sombra_decidiu"]
    assert len(decidiu) == 1 and decidiu[0]["agente"] == "helena"
    for evento in logs:
        if str(evento["event"]).startswith("roteador_"):
            despejo = repr(evento)
            assert _TELEFONE not in despejo
            assert "peito" not in despejo and "atendente" not in despejo


async def test_desligado_nao_produz_nenhum_log_do_roteador() -> None:
    with structlog.testing.capture_logs() as logs:
        await _dispatcher("informacao", roteador=None, client=_FakeWhatsAppClient()).dispatch(
            InboundMessage(from_number=_TELEFONE, text="oi", message_id="wamid.in.3")
        )
    assert not [e for e in logs if str(e["event"]).startswith("roteador_")]


async def test_falha_do_roteador_nao_derruba_o_turno_que_ja_respondeu() -> None:
    class _Quebrada(FakeAgenteAtivoStore):
        async def ler(self, conversation_id: str) -> LinhaAgenteAtivo | None:
            raise OSError("banco fora")

    mensagem = InboundMessage(from_number=_TELEFONE, text="oi", message_id="wamid.in.4")
    desligado = await _dispatcher("informacao", roteador=None, client=_FakeWhatsAppClient()).dispatch(
        mensagem
    )
    client = _FakeWhatsAppClient()
    with structlog.testing.capture_logs() as logs:
        resultado = await _dispatcher("informacao", roteador=_roteador(_Quebrada()), client=client).dispatch(
            mensagem
        )
    assert _sem_relogio(resultado) == _sem_relogio(desligado)
    assert len(client.sent) == 1
    falhas = [e for e in logs if e["event"] == "roteador_registro_falhou"]
    assert falhas and falhas[0]["error_type"] == "OSError"


async def test_turno_que_cai_nao_grava_nada(monkeypatch: pytest.MonkeyPatch) -> None:
    """§2.2: se o turno cai, nada e' gravado e a reentrega recalcula."""

    class _GrafoQueCai:
        async def ainvoke(self, state: Any, config: Any = None) -> Any:
            raise RuntimeError("turno caiu")

    store = FakeAgenteAtivoStore()
    dispatcher = _dispatcher("informacao", roteador=_roteador(store), client=_FakeWhatsAppClient())
    monkeypatch.setattr(dispatcher, "_compile_turn_graph", lambda sender: (_GrafoQueCai(), None))
    with pytest.raises(RuntimeError):
        await dispatcher.dispatch(InboundMessage(from_number=_TELEFONE, text="oi", message_id="wamid.in.5"))
    assert store.linhas == {} and store.gravacoes == 0


async def test_mensagem_limitada_e_nao_texto_nao_passam_pelo_roteador() -> None:
    store = FakeAgenteAtivoStore()
    client = _FakeWhatsAppClient()
    limitado = _dispatcher(
        "informacao",
        roteador=_roteador(store),
        client=client,
        limitador=LimitadorDeVolume(por_conversa=1, por_tenant=0),
    )
    await limitado.dispatch(InboundMessage(from_number=_TELEFONE, text="oi", message_id="wamid.in.6"))
    linhas_depois_do_turno = dict(store.linhas)
    await limitado.dispatch(InboundMessage(from_number=_TELEFONE, text="oi", message_id="wamid.in.7"))
    assert store.linhas == linhas_depois_do_turno  # a segunda foi recusada pelo teto: sem escrita

    await limitado.acknowledge_non_text(
        InboundNonTextMessage(from_number="5511900000124", message_type="audio", message_id="wamid.in.8")
    )
    assert len(store.linhas) == 1
