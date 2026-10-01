"""Onda (e) do numero unico no despachante: `handoff` da Helena -> `LucasTurno` -> UMA escrita CAS.

ADR-0062, plano `docs/plans/lucas-numero-unico.md` §2.2-§2.5 e §6(e). Contra o grafo REAL da
Helena (inferencia roteirizada) e o grafo REAL do Lucas (DMN DRAFT local, fonte simulada), com o
store de dedup em memoria fazendo a perna de saida de verdade. O que se prova:

  - so' com roteador E Lucas presentes a Helena recebe as entradas do roteador e passa adiante;
    roteador sem Lucas e' a sombra da onda (c) e o desligado e' o de hoje;
  - a frase de passagem sai UMA vez por passagem, e a reentrega da mesma mensagem nao repete nem
    a frase nem o envio do Lucas;
  - uma passagem grava UMA linha `lucas`/`handoff_cobranca`; o Lucas escalando grava
    `helena`/`lucas_encerrou`; o Lucas caindo nao grava nada;
  - o que chega ao Lucas e' so' o `handoff` tipado — nunca o texto do beneficiario.

Nenhum telefone real: a faixa sintetica de teste, e nada dela vai para asserts de log.
"""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

import pytest

from maezo.agents.helena.graph import FRASE_PASSAGEM_COBRANCA
from maezo.gateway.pseudonymizer import Pseudonymizer
from maezo.gateway.tool_registry import build_agent_seam_context
from maezo.platform.webhooks.whatsapp import lucas_turno as lt
from maezo.platform.webhooks.whatsapp import pre_roteamento
from maezo.platform.webhooks.whatsapp.dedup import WhatsAppDedupGuard
from maezo.platform.webhooks.whatsapp.dispatch import HelenaDispatcher, InboundMessage
from maezo.platform.webhooks.whatsapp.roteamento import ConversaRouter
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport
from maezo.tools.workers.dmn_transport import FakeDmnTransport
from tests.evals.lucas.programa import DmnDraftLocal, InferenciaRoteirizada
from tests.support.audit_fakes import FakeStartAuditSink
from tests.support.dedup_fakes import FakeDedupRegistry
from tests.support.roteamento_fakes import FakeAgenteAtivoStore

TENANT = "amh"
_TELEFONE = "5511900000177"  # faixa sintetica de teste
_RESPOSTA_DO_LUCAS = "Sua segunda via esta disponivel no aplicativo Austa Clinicas."


def _classify(**campos: Any) -> str:
    base: dict[str, Any] = {
        "intent": "cobranca",
        "population": "none",
        "psychosocial_risk": False,
        "sintoma_codigo": None,
        "intensidade": "desconhecida",
        "cobranca_subtipo": "boleto_2via",
        # Com a competencia, a DMN DRAFT do Lucas admite a 2a via (J1); sem ela, o catch-all escala.
        "competencia": "2026-09",
    }
    base.update(campos)
    return json.dumps(base)


class _Inferencia:
    """A Helena: devolve a classificacao roteirizada; qualquer outra chamada recebe um rascunho."""

    def __init__(self, classificacoes: list[str]) -> None:
        self._classificacoes = list(classificacoes)
        self.prompts: list[str] = []

    async def generate(self, prompt: str, **_: Any) -> str:
        self.prompts.append(prompt)
        if "Tarefa: leia a mensagem do beneficiario" in prompt and self._classificacoes:
            return self._classificacoes.pop(0)
        return "Recebemos sua mensagem e encaminhamos seu caso para a nossa equipe de saude."


class _Cliente:
    """`WhatsAppServer.send_message` com a perna de saida do dedup: reclama, suprime, sela."""

    def __init__(self, registry: FakeDedupRegistry) -> None:
        self.registry = registry
        self.enviados: list[tuple[str, str]] = []

    async def send_message(self, to: str, text: str, *, idempotency_key: str | None = None) -> dict[str, Any]:
        if idempotency_key is not None and not await self.registry.claim(idempotency_key):
            return {"suppressed_duplicate": True}
        self.enviados.append((to, text))
        if idempotency_key is not None:
            await self.registry.mark_processed(idempotency_key)
        return {"messages": [{"id": f"out-{len(self.enviados)}"}]}

    @property
    def textos(self) -> list[str]:
        return [t for _, t in self.enviados]


class _LucasEspiao:
    """Embrulha o `LucasTurno` real e guarda O QUE chegou a ele (o handoff), para provar que o
    texto do beneficiario nao chega."""

    def __init__(self, real: lt.LucasTurno, *, falhar: bool = False) -> None:
        self._real = real
        self._falhar = falhar
        self.handoffs: list[dict[str, Any]] = []

    async def executar(self, handoff: Any, conversa: Any, sender: Any) -> dict[str, Any]:
        self.handoffs.append(dict(handoff))
        if self._falhar:
            raise RuntimeError("motor do lucas fora")
        return await self._real.executar(handoff, conversa, sender)


def _montar(
    classificacoes: list[str],
    *,
    com_roteador: bool = True,
    com_lucas: bool = True,
    rascunho_lucas: str | None = _RESPOSTA_DO_LUCAS,
    lucas_falha: bool = False,
    store: FakeAgenteAtivoStore | None = None,
    registry: FakeDedupRegistry | None = None,
) -> tuple[HelenaDispatcher, _Cliente, FakeAgenteAtivoStore, _LucasEspiao | None, _Inferencia]:
    registry = registry or FakeDedupRegistry()
    pseudo = Pseudonymizer()
    dedup = WhatsAppDedupGuard(registry=registry, pseudonymizer=pseudo, tenant=TENANT)
    cliente = _Cliente(registry)
    store = store if store is not None else FakeAgenteAtivoStore()
    roteador = (
        ConversaRouter(
            tenant_id=TENANT,
            store=store,
            lexicos=pre_roteamento.carregar(),
            inatividade=timedelta(minutes=60),
            lucas_disponivel=True,
        )
        if com_roteador
        else None
    )
    espiao = None
    if com_lucas:
        real = lt.LucasTurno(
            tenant_id=TENANT,
            inference=InferenciaRoteirizada(rascunho_lucas, rota_padrao="respond_member"),  # type: ignore[arg-type]
            dmn=DmnDraftLocal(),
            cibseven=FakeCibSevenTransport(),
            audit_sink=FakeStartAuditSink(),
            seam_context=build_agent_seam_context(tenant=TENANT, agent_id="lucas"),
            dedup=dedup,
            fonte=_fonte(),
        )
        espiao = _LucasEspiao(real, falhar=lucas_falha)
    inferencia = _Inferencia(classificacoes)
    dispatcher = HelenaDispatcher(
        tenant_id=TENANT,
        inference=inferencia,  # type: ignore[arg-type]
        dmn=FakeDmnTransport(),
        cibseven=FakeCibSevenTransport(),
        whatsapp_client=cliente,  # type: ignore[arg-type]
        pseudonymizer=pseudo,
        audit_sink=FakeStartAuditSink(),
        dedup=dedup,
        roteador=roteador,
        lucas_turno=espiao,  # type: ignore[arg-type]
    )
    return dispatcher, cliente, store, espiao, inferencia


class _FonteConciliada:
    """Fonte fixa: pagamento conciliado (perfil `conciliado` do corpus da onda a), para a 2a via
    cair na J1 sem depender do hash do pseudonimo que a fonte simulada usa."""

    async def fatos(self, pseudo_id: str, competencia: str | None) -> Any:
        from maezo.agents.lucas.fonte_cobranca import FatosCobranca

        del pseudo_id, competencia
        return FatosCobranca(
            status_conciliado=True,
            ciclos_sem_conciliacao=0,
            numero_boleto="00000-SINTETICO",
            cnab_ref="CNAB-SINT",
        )


def _fonte() -> Any:
    return _FonteConciliada()


def _msg(texto: str = "preciso da segunda via do boleto", wamid: str = "wamid.SINTETICO-1") -> InboundMessage:
    return InboundMessage(from_number=_TELEFONE, text=texto, message_id=wamid)


def _linha(store: FakeAgenteAtivoStore) -> Any:
    assert len(store.linhas) == 1
    return next(iter(store.linhas.values()))


# --- a passagem ---------------------------------------------------------------------------------


async def test_ligado_a_passagem_envia_a_frase_roda_o_lucas_e_grava_uma_vez() -> None:
    dispatcher, cliente, store, espiao, inferencia = _montar([_classify()])

    resultado = await dispatcher.dispatch(_msg())

    assert resultado["handoff"]["cobranca_subtipo"] == "boleto_2via"
    assert cliente.textos[0] == FRASE_PASSAGEM_COBRANCA
    assert cliente.textos.count(FRASE_PASSAGEM_COBRANCA) == 1
    assert len(cliente.textos) == 2  # a frase da Helena + a resposta do Lucas
    assert espiao is not None and len(espiao.handoffs) == 1
    assert store.gravacoes == 1
    linha = _linha(store)
    assert (linha.agente_ativo, linha.transicao_motivo) == ("lucas", "handoff_cobranca")
    assert linha.lucas_cobranca_subtipo == "boleto_2via"
    # A Helena recebeu o classify-v6 (so' existe com o roteamento completo).
    assert '"cobranca"]' in inferencia.prompts[0]


async def test_o_lucas_recebe_so_o_handoff_tipado_nunca_o_texto() -> None:
    texto = "preciso da segunda via do boleto, meu nome e Fulano"
    dispatcher, _, _, espiao, _ = _montar([_classify()])
    await dispatcher.dispatch(_msg(texto))
    assert espiao is not None
    (handoff,) = espiao.handoffs
    assert set(handoff) == {"para", "cobranca_subtipo", "competencia", "message_ref"}
    assert "Fulano" not in json.dumps(handoff)
    assert handoff["message_ref"].startswith("hk1_")  # pseudonimo keyed, nunca o wamid cru
    assert "wamid" not in handoff["message_ref"]


async def test_reentrega_da_mesma_mensagem_nao_repete_frase_nem_resposta_do_lucas() -> None:
    registry = FakeDedupRegistry()
    store = FakeAgenteAtivoStore()
    d1, c1, _, _, _ = _montar([_classify()], store=store, registry=registry)
    await d1.dispatch(_msg())
    enviados_na_primeira = list(c1.textos)

    d2, c2, _, espiao2, _ = _montar([_classify()], store=store, registry=registry)
    await d2.dispatch(_msg())

    assert len(enviados_na_primeira) == 2
    assert c2.textos == []  # frase suprimida (Lucas ja' ativo) e envio do Lucas suprimido pelo dedup
    assert espiao2 is not None and len(espiao2.handoffs) == 1
    assert _linha(store).agente_ativo == "lucas"


async def test_segunda_pergunta_de_cobranca_com_o_lucas_ativo_nao_repete_a_frase() -> None:
    store = FakeAgenteAtivoStore()
    registry = FakeDedupRegistry()
    d1, _, _, _, _ = _montar([_classify()], store=store, registry=registry)
    await d1.dispatch(_msg())

    d2, c2, _, _, _ = _montar([_classify(cobranca_subtipo="vencimento")], store=store, registry=registry)
    resultado = await d2.dispatch(_msg("e quando vence?", wamid="wamid.SINTETICO-2"))

    assert FRASE_PASSAGEM_COBRANCA not in c2.textos
    assert len(c2.textos) == 1  # so' o Lucas responde
    assert resultado["desfecho"] == "passagem_cobranca_sem_frase"
    linha = _linha(store)
    assert (linha.agente_ativo, linha.transicao_motivo, linha.lucas_cobranca_subtipo) == (
        "lucas",
        "handoff_cobranca",
        "vencimento",
    )
    assert store.gravacoes == 2  # uma escrita por mensagem


async def test_lucas_escalando_devolve_a_conversa_para_a_helena() -> None:
    dispatcher, _, store, _, _ = _montar([_classify(cobranca_subtipo="contestacao")])
    await dispatcher.dispatch(_msg("estao me cobrando um valor errado"))
    linha = _linha(store)
    assert (linha.agente_ativo, linha.transicao_motivo) == ("helena", "lucas_encerrou")


async def test_lucas_caindo_derruba_o_turno_e_nada_e_gravado() -> None:
    dispatcher, _, store, _, _ = _montar([_classify()], lucas_falha=True)
    with pytest.raises(RuntimeError):
        await dispatcher.dispatch(_msg())
    assert store.gravacoes == 0


# --- o que NAO passa ------------------------------------------------------------------------------


async def test_cobranca_com_pedido_de_pessoa_no_lexico_escala_e_nao_roda_o_lucas() -> None:
    dispatcher, cliente, store, espiao, _ = _montar([_classify()])
    resultado = await dispatcher.dispatch(_msg("quero falar com um atendente sobre o boleto"))
    assert resultado["escalation_motivo"] == "solicitacao_humano"
    assert espiao is not None and espiao.handoffs == []
    assert FRASE_PASSAGEM_COBRANCA not in cliente.textos
    assert _linha(store).transicao_motivo == "retorno_pedido_humano"


async def test_cobranca_com_sinal_de_saude_no_lexico_nunca_vai_ao_lucas() -> None:
    dispatcher, cliente, store, espiao, _ = _montar([_classify()])
    resultado = await dispatcher.dispatch(_msg("o boleto venceu e estou com falta de ar"))
    assert resultado.get("handoff") is None
    assert espiao is not None and espiao.handoffs == []
    assert FRASE_PASSAGEM_COBRANCA not in cliente.textos
    assert _linha(store).agente_ativo == "helena"


async def test_classify_falho_nunca_vai_ao_lucas() -> None:
    dispatcher, _, store, espiao, _ = _montar(["isto nao e json"])
    resultado = await dispatcher.dispatch(_msg())
    assert resultado["escalation_motivo"] == "falha_tecnica"
    assert espiao is not None and espiao.handoffs == []
    assert _linha(store).transicao_motivo == "retorno_falha"


# --- desligado e sombra --------------------------------------------------------------------------


async def test_roteador_sem_lucas_e_a_sombra_a_helena_nao_conhece_cobranca() -> None:
    dispatcher, cliente, store, _, inferencia = _montar([_classify()], com_lucas=False)
    resultado = await dispatcher.dispatch(_msg())
    assert '"cobranca"]' not in inferencia.prompts[0]  # classify-v5
    assert resultado.get("handoff") is None
    assert "invalid_intent" in str(resultado.get("error"))
    assert FRASE_PASSAGEM_COBRANCA not in cliente.textos
    assert _linha(store).agente_ativo == "helena"


async def test_desligado_e_o_caminho_de_hoje() -> None:
    dispatcher, cliente, store, _, inferencia = _montar([_classify()], com_roteador=False, com_lucas=False)
    resultado = await dispatcher.dispatch(_msg())
    assert '"cobranca"]' not in inferencia.prompts[0]
    assert resultado.get("handoff") is None
    assert FRASE_PASSAGEM_COBRANCA not in cliente.textos
    assert store.gravacoes == 0


async def test_lucas_sem_roteador_nao_liga_nada() -> None:
    dispatcher, cliente, _, espiao, inferencia = _montar([_classify()], com_roteador=False)
    await dispatcher.dispatch(_msg())
    assert '"cobranca"]' not in inferencia.prompts[0]
    assert espiao is not None and espiao.handoffs == []
    assert FRASE_PASSAGEM_COBRANCA not in cliente.textos


# --- o remetente com chave propria (pendencia da onda d) -----------------------------------------


async def test_o_remetente_do_turno_aceita_a_chave_de_quem_chama() -> None:
    from maezo.platform.webhooks.whatsapp.dispatch import _ScopedWhatsAppSender

    registry = FakeDedupRegistry()
    cliente = _Cliente(registry)
    remetente = _ScopedWhatsAppSender(
        raw_to=_TELEFONE,
        expected_hash="hk1_x",
        client=cliente,  # type: ignore[arg-type]
        idempotency_key_for=lambda n: f"fabrica:{n}",
    )
    await remetente.send("hk1_x", "um", idempotency_key="propria:1")
    await remetente.send("hk1_x", "dois")
    assert cliente.textos == ["um", "dois"]
    assert await registry.claim("propria:1") is False
    assert await registry.claim("fabrica:2") is False
