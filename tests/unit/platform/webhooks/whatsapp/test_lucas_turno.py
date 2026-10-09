"""`LucasTurno` — a entrada de mensagens no Lucas (onda d de `docs/plans/lucas-numero-unico.md`).

O que este arquivo prova, contra o grafo REAL do Lucas e as DMN DRAFT lidas do XML:
  - cada subtipo do handoff cai na jornada esperada (tabela de §2.5 = corpus da onda a);
  - reentrega do mesmo `message_id` nao reenvia (chave `lucas:{message_id}` no store de dedup);
  - o estado do Lucas nunca tem chave de texto (varredura das chaves) e o handoff recusa chave a mais;
  - o envio passa pelo gate do principal `lucas`, e o grafo compila sem checkpointer;
  - §3: agente que nao seja `security_zone: general` e' recusado no boot;
  - as metricas do turno saem com `agent_id="lucas"`.

Nenhum telefone aparece aqui: o destino cru do remetente e' um token sintetico.
"""

from __future__ import annotations

import dataclasses
import hashlib
import re
from collections.abc import Mapping
from typing import Any, cast

import pytest

from maezo.agents.lucas.fonte_cobranca import FatosCobranca, FonteCobrancaSimulada, Indisponivel
from maezo.agents.lucas.graph import _CALLER_INPUT_FIELDS, LucasState
from maezo.gateway.pseudonymizer import Pseudonymizer
from maezo.gateway.seams import SeamContext
from maezo.gateway.tool_registry import build_agent_seam_context
from maezo.platform.webhooks.whatsapp import lucas_turno as lt
from maezo.platform.webhooks.whatsapp.dedup import WhatsAppDedupGuard
from maezo.platform.webhooks.whatsapp.roteamento import COBRANCA_SUBTIPOS
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport
from tests.evals.lucas.programa import DmnDraftLocal, InferenciaRoteirizada, carregar_casos
from tests.support.audit_fakes import FakeStartAuditSink
from tests.support.dedup_fakes import FakeDedupRegistry

TENANT = "amh"
#: Destino cru SINTETICO (nao e' telefone): o remetente do turno fecha sobre ele.
DESTINO_SINTETICO = "destino-cru-sintetico"
#: O corpus da onda (a): cada caso com `subtipo_handoff` (fora os fail-safe) vira um handoff.
CASOS = carregar_casos()
CASOS_COM_HANDOFF = [c for c in CASOS if c.get("subtipo_handoff") and c["jornada"] != "failsafe"]


# --- Dubles ---------------------------------------------------------------------------------------


class _ClienteComDedup:
    """O protocolo de `WhatsAppServer.send_message` com dedup: reclama a chave antes do envio,
    suprime a repeticao e sela depois. Os envios reais ficam em `enviados`."""

    def __init__(self, registry: FakeDedupRegistry) -> None:
        self.registry = registry
        self.enviados: list[tuple[str, str, str]] = []

    async def send_message(self, to: str, text: str, *, idempotency_key: str) -> dict[str, Any]:
        if not await self.registry.claim(idempotency_key):
            return {"suppressed_duplicate": True}
        self.enviados.append((to, text, idempotency_key))
        await self.registry.mark_processed(idempotency_key)
        return {"messages": [{"id": f"out-{len(self.enviados)}"}]}


class _RemetenteDoTurno:
    """O remetente de UM turno, como o `_ScopedWhatsAppSender`: fecha sobre o destino cru e confere
    que o grafo so' envia para o hash deste turno."""

    def __init__(self, cliente: _ClienteComDedup, *, hash_esperado: str) -> None:
        self._cliente = cliente
        self._hash = hash_esperado

    async def send(self, to_hash: str, text: str, *, idempotency_key: str) -> dict[str, Any]:
        assert to_hash == self._hash
        return await self._cliente.send_message(DESTINO_SINTETICO, text, idempotency_key=idempotency_key)


class _FonteFixa:
    def __init__(self, resultado: FatosCobranca | Indisponivel) -> None:
        self.resultado = resultado
        self.pedidos: list[tuple[str, str | None]] = []

    async def fatos(
        self, pseudo_id: str, competencia: str | None, *, phone_hash: str | None = None
    ) -> FatosCobranca | Indisponivel:
        self.pedidos.append((pseudo_id, competencia))
        return self.resultado


def _hex(semente: str) -> str:
    return hashlib.sha256(f"test-lucas-turno:{semente}".encode()).hexdigest()


def _conversa(
    semente: str = "c1", *, pseudo_id: str = "pseudo-sintetico", message_id: str = ""
) -> lt.ConversaDoTurno:
    hx = _hex(semente)
    return lt.ConversaDoTurno(
        conversation_id=f"wa:{TENANT}:hk1_{hx[:24]}",
        beneficiario_pseudo_id=pseudo_id,
        to_hash=hx[24:48],
        message_id=message_id or f"wamid.SINTETICO-{semente}",
    )


#: Marcador: `_rodar` o troca pelo `message_ref` REAL da entrega do turno (o pseudonimo keyed do
#: `message_id`), que e' o unico que o `LucasTurno` aceita desde a onda (f).
_REF_DESTA_MENSAGEM = "msg-ref-sintetico"


def _handoff(
    subtipo: str = "boleto_2via", competencia: str | None = "2026-09", **extra: Any
) -> dict[str, Any]:
    return {
        "para": "lucas",
        "cobranca_subtipo": subtipo,
        "competencia": competencia,
        "message_ref": _REF_DESTA_MENSAGEM,
        **extra,
    }


@pytest.fixture(scope="module")
def seam_lucas() -> SeamContext:
    return build_agent_seam_context(tenant=TENANT, agent_id="lucas")


def _turno(
    seam: SeamContext,
    *,
    registry: FakeDedupRegistry | None = None,
    fonte: Any = None,
    rascunho: str | None = None,
    rota_padrao: str = "respond_member",
    cibseven: FakeCibSevenTransport | None = None,
) -> tuple[lt.LucasTurno, FakeDedupRegistry, FakeCibSevenTransport]:
    registry = registry or FakeDedupRegistry()
    cibseven = cibseven or FakeCibSevenTransport()
    turno = lt.LucasTurno(
        tenant_id=TENANT,
        inference=InferenciaRoteirizada(rascunho, rota_padrao=rota_padrao),  # type: ignore[arg-type]
        dmn=DmnDraftLocal(),
        cibseven=cibseven,
        audit_sink=FakeStartAuditSink(),
        seam_context=seam,
        dedup=WhatsAppDedupGuard(registry=registry, pseudonymizer=Pseudonymizer(), tenant=TENANT),
        fonte=fonte or FonteCobrancaSimulada(),
    )
    return turno, registry, cibseven


async def _rodar(
    turno: lt.LucasTurno, conversa: lt.ConversaDoTurno, handoff: Mapping[str, Any], cliente: _ClienteComDedup
) -> dict[str, Any]:
    if isinstance(handoff, Mapping) and handoff.get("message_ref") == _REF_DESTA_MENSAGEM:
        handoff = {**handoff, "message_ref": turno.dedup.pseudonym(conversa.message_id)}
    return await turno.executar(handoff, conversa, _RemetenteDoTurno(cliente, hash_esperado=conversa.to_hash))


# --- §2.5: a tabela -------------------------------------------------------------------------------


def test_tabela_cobre_exatamente_o_dominio_do_subtipo() -> None:
    assert set(lt.ENTRADA_POR_SUBTIPO) == set(COBRANCA_SUBTIPOS)
    for linha in lt.ENTRADA_POR_SUBTIPO.values():
        assert set(linha) <= _CALLER_INPUT_FIELDS


def test_jornada_por_subtipo_e_a_do_plano() -> None:
    """§2.5 linha a linha: J3 sempre leva a flag (ou a intencao) que o grafo escala sem DMN.
    `cobranca_recebida` (DL-0082) nao e' J3: a intencao `inadimplencia` e' so' dica e o
    `tipo_solicitacao` e' `status_pagamento`, para a DMN decidir pelos fatos."""
    t = lt.ENTRADA_POR_SUBTIPO
    assert {s for s, linha in t.items() if linha["intencao"] == "cobranca_info"} == {
        "boleto_2via",
        "vencimento",
        "outro",
        "contestacao",
        "consulta_valores",
    }
    # DL-0086: pergunta de valor e' J1 com o `tipo_solicitacao` da linha nova da DMN.
    assert t["consulta_valores"] == {"intencao": "cobranca_info", "tipo_solicitacao": "consulta_valores"}
    assert t["confirmacao_pagamento"]["intencao"] == "confirmacao_pagamento"
    assert t["contestacao"]["contesta_cobranca"] is True
    assert t["cobranca_recebida"] == {"intencao": "inadimplencia", "tipo_solicitacao": "status_pagamento"}
    assert t["cancelamento"] == {
        "intencao": "cancelamento",
        "tipo_solicitacao": "",
        "pedido_cancelamento": True,
    }
    assert t["outro"]["tipo_solicitacao"] == ""
    # So' a contestacao e o cancelamento levam flag; nenhum J1/J2 leva.
    com_flag = {
        s for s, linha in t.items() if linha.get("contesta_cobranca") or linha.get("pedido_cancelamento")
    }
    assert com_flag == {"contestacao", "cancelamento"}


@pytest.mark.parametrize("caso", CASOS_COM_HANDOFF, ids=lambda c: str(c["id"]))
def test_tabela_do_subtipo_e_a_do_corpus_da_onda_a(caso: dict[str, Any]) -> None:
    """A tabela usa o vocabulario da DMN, o mesmo que o corpus da onda (a) ja' fixa por caso.
    `boleto`/`2a_via` caem na mesma linha da DMN (`lba_r_boleto_2via`)."""
    linha = dict(lt.ENTRADA_POR_SUBTIPO[caso["subtipo_handoff"]])
    entrada = {k: v for k, v in caso["entrada"].items() if k != "competencia"}
    if caso["subtipo_handoff"] == "boleto_2via":
        assert entrada.pop("tipo_solicitacao") in {"boleto", "2a_via"}
        linha.pop("tipo_solicitacao")
    assert linha == entrada


@pytest.mark.parametrize("caso", CASOS_COM_HANDOFF, ids=lambda c: str(c["id"]))
async def test_cada_subtipo_cai_na_jornada_esperada(caso: dict[str, Any], seam_lucas: SeamContext) -> None:
    """Ponta a ponta: handoff -> `LucasTurno` -> grafo real + DMN DRAFT + fonte simulada, contra o
    `esperado` do corpus (rota, admissibilidade, motivo, processo e desfecho)."""
    esperado = caso["esperado"]
    turno, _, _ = _turno(
        seam_lucas, rascunho=caso.get("rascunho_llm"), rota_padrao=str(esperado.get("route", ""))
    )
    cliente = _ClienteComDedup(FakeDedupRegistry())
    conversa = _conversa(str(caso["id"]), pseudo_id=str(caso["pseudo_id"]))
    final = await _rodar(turno, conversa, _handoff(caso["subtipo_handoff"], "2026-09"), cliente)

    for campo in (
        "route",
        "admissibilidade",
        "motivo_humano",
        "motivo_categoria",
        "desfecho",
        "process_started",
    ):
        if campo in esperado:
            assert final.get(campo, "") == esperado[campo], campo
    if caso["jornada"] == "J3":
        assert final["route"] == "escalate_human"
        assert final["process_started"] is True
        assert (final.get("dossier") or {}).get("decisao_cancelamento") is None
    assert len(cliente.enviados) <= 1


# --- Reentrega ------------------------------------------------------------------------------------


@pytest.mark.parametrize("subtipo", ["boleto_2via", "cancelamento"])
async def test_reentrega_do_mesmo_message_id_nao_reenvia(subtipo: str, seam_lucas: SeamContext) -> None:
    conciliado = FatosCobranca(
        status_conciliado=True, ciclos_sem_conciliacao=0, numero_boleto="SIM-X", cnab_ref="c"
    )
    rota = "escalate_human" if subtipo == "cancelamento" else "respond_member"
    turno, registry, cibseven = _turno(seam_lucas, fonte=_FonteFixa(conciliado), rota_padrao=rota)
    cliente = _ClienteComDedup(registry)
    conversa = _conversa("reentrega", message_id="wamid.SINTETICO-REENTREGA")

    primeiro = await _rodar(turno, conversa, _handoff(subtipo), cliente)
    segundo = await _rodar(turno, conversa, _handoff(subtipo), cliente)

    assert len(cliente.enviados) == 1
    assert primeiro["mensagem_enviada"] is True
    assert segundo["mensagem_enviada"] is False
    if subtipo == "cancelamento":
        # o start e' idempotente pela business key: a reentrega reencontra a instancia viva
        assert segundo["process_ref"]["instance_id"] == primeiro["process_ref"]["instance_id"]
        assert segundo["ack_pending"] is True
    else:
        assert segundo["desfecho"] == "envio_suprimido_duplicata"
    assert len({k for _, _, k in cliente.enviados}) == 1
    del cibseven


async def test_mensagens_diferentes_tem_chaves_diferentes_e_espaco_proprio(seam_lucas: SeamContext) -> None:
    turno, registry, _ = _turno(seam_lucas)
    cliente = _ClienteComDedup(registry)
    a = _conversa("k", message_id="wamid.SINTETICO-A")
    b = dataclasses.replace(a, message_id="wamid.SINTETICO-B")
    await _rodar(turno, a, _handoff("vencimento"), cliente)
    await _rodar(turno, b, _handoff("vencimento"), cliente)
    assert len(cliente.enviados) == 2
    chaves = [k for _, _, k in cliente.enviados]
    guard = turno.dedup
    assert chaves == [
        guard.outbound_key("lucas:wamid.SINTETICO-A", occurrence=1),
        guard.outbound_key("lucas:wamid.SINTETICO-B", occurrence=1),
    ]
    # Nunca a mesma chave que a Helena reclamaria pela mesma entrega.
    assert guard.outbound_key("wamid.SINTETICO-A", occurrence=1) not in chaves
    # E o id de entrega cru nunca aparece na chave (ele embute o telefone da contraparte).
    assert not any("SINTETICO" in k for k in chaves)


# --- §3: o Lucas nao ve texto ---------------------------------------------------------------------

_CHAVE_DE_TEXTO = re.compile(r"body|texto|text|mensagem_recebida|inbound|conteudo|message|corpo|fala")


def test_estado_do_lucas_nunca_tem_chave_de_texto() -> None:
    """Varredura das chaves: a entrada do Lucas, a tabela de §2.5, os fatos da fonte, o handoff e
    os identificadores do turno nao tem onde carregar texto do beneficiario."""
    assert [k for k in _CALLER_INPUT_FIELDS if _CHAVE_DE_TEXTO.search(k)] == []
    for linha in lt.ENTRADA_POR_SUBTIPO.values():
        assert [k for k in linha if _CHAVE_DE_TEXTO.search(k)] == []
    fatos = FatosCobranca(
        status_conciliado=True, ciclos_sem_conciliacao=0, numero_boleto="SIM-X", cnab_ref="c"
    )
    assert [k for k in fatos.como_entrada_lucas() if _CHAVE_DE_TEXTO.search(k)] == []
    # `message_ref` e' o pseudonimo de log da entrega, nao texto; e' a unica chave com "message"
    # e ela nao entra no estado do Lucas.
    assert [k for k in lt.CHAVES_DO_HANDOFF if _CHAVE_DE_TEXTO.search(k)] == ["message_ref"]
    campos_conversa = {f.name for f in dataclasses.fields(lt.ConversaDoTurno)}
    # `phone_hash_amh` (fonte AMH, decisao do dono 06/10/2026) e' um hash HMAC, nunca texto, e nao
    # entra no estado do Lucas (so' a fonte o le') — ver o teste de estado abaixo.
    assert campos_conversa == {
        "conversation_id",
        "beneficiario_pseudo_id",
        "to_hash",
        "message_id",
        "phone_hash_amh",
    }


@pytest.mark.parametrize("subtipo", sorted(COBRANCA_SUBTIPOS))
async def test_estado_montado_tem_so_entrada_e_nenhum_valor_do_turno_alem_dos_ids(subtipo: str) -> None:
    conversa = _conversa("estado")
    fatos = await FonteCobrancaSimulada().fatos("pseudo-sintetico", "2026-09")
    estado: LucasState = lt.entrada_do_lucas(
        lt.validar_handoff(_handoff(subtipo)),
        conversa,
        tenant_id=TENANT,
        fatos=fatos if isinstance(fatos, FatosCobranca) else None,
    )
    assert set(estado) <= _CALLER_INPUT_FIELDS
    assert [k for k in estado if _CHAVE_DE_TEXTO.search(k)] == []
    assert conversa.message_id not in {str(v) for v in estado.values()}
    assert "msg-ref-sintetico" not in {str(v) for v in estado.values()}


@pytest.mark.parametrize("chave", ["message_body", "texto", "intencao", "route", "error"])
def test_handoff_com_chave_a_mais_e_recusado_sem_ecoar_valor(chave: str) -> None:
    with pytest.raises(lt.HandoffInvalidoError) as exc:
        lt.validar_handoff(_handoff(**{chave: "VALOR-PLANTADO"}))
    assert "VALOR-PLANTADO" not in str(exc.value)
    assert chave in str(exc.value)


@pytest.mark.parametrize(
    "handoff",
    [
        {**_handoff(), "para": "helena"},
        {**_handoff(), "cobranca_subtipo": "boleto"},
        {**_handoff(), "competencia": "2026-13"},
        {**_handoff(), "competencia": "setembro"},
        {**_handoff(), "message_ref": ""},
        {k: v for k, v in _handoff().items() if k != "message_ref"},
    ],
)
async def test_handoff_malformado_nao_vira_turno(handoff: dict[str, Any], seam_lucas: SeamContext) -> None:
    turno, registry, cibseven = _turno(seam_lucas)
    cliente = _ClienteComDedup(registry)
    with pytest.raises(lt.HandoffInvalidoError):
        await _rodar(turno, _conversa(), handoff, cliente)
    assert cliente.enviados == []
    assert registry.calls == []


@pytest.mark.parametrize(
    "conversa",
    [
        dataclasses.replace(_conversa(), conversation_id="wa:outro:hk1_abc"),
        dataclasses.replace(_conversa(), conversation_id="wa:amh:sha_abc"),
        dataclasses.replace(_conversa(), message_id=""),
        dataclasses.replace(_conversa(), to_hash=""),
    ],
)
async def test_conversa_fora_do_formato_e_recusada_antes_de_efeito(
    conversa: lt.ConversaDoTurno, seam_lucas: SeamContext
) -> None:
    turno, registry, _ = _turno(seam_lucas)
    cliente = _ClienteComDedup(registry)
    with pytest.raises(ValueError):
        await _rodar(turno, conversa, _handoff(), cliente)
    assert cliente.enviados == []


# --- Onda (f): o handoff e' DESTA mensagem -------------------------------------------------------


class _FonteQueConta:
    def __init__(self) -> None:
        self.pedidos = 0

    async def fatos(self, pseudo_id: str, competencia: str | None, *, phone_hash: str | None = None) -> Any:
        del pseudo_id, competencia
        self.pedidos += 1
        return FatosCobranca(
            status_conciliado=True, ciclos_sem_conciliacao=0, numero_boleto="SIM-X", cnab_ref="c"
        )


@pytest.mark.parametrize(
    "ref_do_handoff",
    [
        "de-outra-entrega",  # a referencia de outra mensagem da mesma conversa
        "<wamid-sem-pseudonimo>",  # o marcador constante de `log_safe_message_id` sem pseudonimizador
        "hk1_0000",  # forma de pseudonimo, valor errado
    ],
)
async def test_handoff_de_outra_mensagem_e_recusado_antes_de_qualquer_efeito(
    ref_do_handoff: str, seam_lucas: SeamContext
) -> None:
    fonte = _FonteQueConta()
    turno, registry, cibseven = _turno(seam_lucas, fonte=fonte)
    cliente = _ClienteComDedup(registry)
    with pytest.raises(lt.HandoffDeOutraMensagemError) as exc:
        await _rodar(turno, _conversa("velho"), _handoff(message_ref=ref_do_handoff), cliente)
    assert ref_do_handoff not in str(exc.value)
    assert cliente.enviados == []
    assert registry.calls == []
    assert fonte.pedidos == 0
    del cibseven


async def test_handoff_velho_nao_se_reaproveita_na_mensagem_seguinte(seam_lucas: SeamContext) -> None:
    """O handoff emitido para a mensagem A nao abre um turno do Lucas na mensagem B."""
    turno, registry, _ = _turno(seam_lucas)
    cliente = _ClienteComDedup(registry)
    a = _conversa("seq", message_id="wamid.SINTETICO-SEQ-A")
    b = dataclasses.replace(a, message_id="wamid.SINTETICO-SEQ-B")
    handoff_de_a = _handoff(message_ref=turno.dedup.pseudonym(a.message_id))
    await _rodar(turno, a, handoff_de_a, cliente)
    assert len(cliente.enviados) == 1
    with pytest.raises(lt.HandoffDeOutraMensagemError):
        await _rodar(turno, b, handoff_de_a, cliente)
    assert len(cliente.enviados) == 1


def test_recusa_de_outra_mensagem_e_um_handoff_invalido() -> None:
    assert issubclass(lt.HandoffDeOutraMensagemError, lt.HandoffInvalidoError)


# --- Fonte de cobranca ----------------------------------------------------------------------------


async def test_fonte_indisponivel_nao_inventa_fato_e_escala(seam_lucas: SeamContext) -> None:
    fonte = _FonteFixa(Indisponivel("fonte_indisponivel"))
    turno, registry, _ = _turno(seam_lucas, fonte=fonte, rota_padrao="escalate_human")
    final = await _rodar(
        turno,
        _conversa("ind", pseudo_id="p-x"),
        _handoff("confirmacao_pagamento"),
        _ClienteComDedup(registry),
    )
    assert fonte.pedidos == [("p-x", "2026-09")]
    assert final["route"] == "escalate_human"
    assert final["billing_facts"]["status_conciliado"] is None


async def test_competencia_ausente_chega_como_ausente(seam_lucas: SeamContext) -> None:
    fonte = _FonteFixa(Indisponivel("fonte_indisponivel"))
    turno, registry, _ = _turno(seam_lucas, fonte=fonte, rota_padrao="escalate_human")
    final = await _rodar(turno, _conversa("sem"), _handoff("outro", None), _ClienteComDedup(registry))
    assert fonte.pedidos == [("pseudo-sintetico", None)]
    assert final["billing_facts"]["competencia"] == ""


# --- DL-0082: o Lucas decide pelos FATOS, nunca pela pergunta ---------------------------------------
#
# Reproducao do teste real de 08/10/2026 01:13 UTC: "minha mensalidade esta em aberto?" ->
# `cobranca_recebida` -> a fonte AMH disse conciliado com zero ciclos -> o Lucas escalou por
# inadimplencia e o ACK do modelo falou em "inadimplencia detectada". O rascunho do modelo abaixo e'
# a frase daquele dia: ele nao pode chegar ao beneficiario em nenhum dos tres casos.

_RASCUNHO_DO_INCIDENTE = (
    "Olá! Recebemos a informação sobre a inadimplência detectada e um de nossos atendentes "
    "especializados entrará em contato."
)


def _sem_acento(texto: str) -> str:
    import unicodedata

    decomposto = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in decomposto if not unicodedata.combining(c)).lower()


async def test_cobranca_recebida_com_fato_conciliado_responde_em_dia_sem_processo(
    seam_lucas: SeamContext,
) -> None:
    from maezo.agents.lucas.graph import texto_mensalidade_em_dia

    fonte = _FonteFixa(
        FatosCobranca(
            status_conciliado=True,
            ciclos_sem_conciliacao=0,
            numero_boleto="****7869",
            cnab_ref="amh-billing:2026-07-28",
        )
    )
    turno, registry, _ = _turno(
        seam_lucas, fonte=fonte, rascunho=_RASCUNHO_DO_INCIDENTE, rota_padrao="escalate_human"
    )
    cliente = _ClienteComDedup(registry)
    final = await _rodar(turno, _conversa("dl82-ok"), _handoff("cobranca_recebida"), cliente)

    assert final["route"] == "respond_member"
    assert final["admissibilidade"] == "RESPONDER"
    assert final["process_started"] is False
    assert final["motivo_humano"] == ""
    assert final["desfecho"] == "resposta_informativa_enviada"
    assert [texto for _, texto, _ in cliente.enviados] == [
        "Consultei aqui: o pagamento da sua mensalidade (boleto final 7869) consta como conciliado, então "
        "ela está em dia. Essa informação é conforme os dados de 28/07/2026."
    ]
    assert cliente.enviados[0][1] == texto_mensalidade_em_dia(cast(LucasState, final))
    assert "inadimpl" not in _sem_acento(cliente.enviados[0][1])


async def test_cobranca_recebida_com_ciclos_em_aberto_segue_a_escalacao_de_sempre(
    seam_lucas: SeamContext,
) -> None:
    from maezo.agents.lucas.graph import ACK_ESCALACAO

    fonte = _FonteFixa(
        FatosCobranca(
            status_conciliado=False,
            ciclos_sem_conciliacao=2,
            numero_boleto="****7869",
            cnab_ref="amh-billing:2026-07-28",
        )
    )
    turno, registry, _ = _turno(
        seam_lucas, fonte=fonte, rascunho=_RASCUNHO_DO_INCIDENTE, rota_padrao="escalate_human"
    )
    cliente = _ClienteComDedup(registry)
    final = await _rodar(turno, _conversa("dl82-atraso"), _handoff("cobranca_recebida"), cliente)

    assert final["route"] == "escalate_human"
    assert final["motivo_humano"] == "inadimplencia_detectada"  # nome interno, so' para o humano
    assert final["roteamento_escalacao"] == "COBRANCA_HUMANO"
    assert final["process_started"] is True
    assert [texto for _, texto, _ in cliente.enviados] == [
        "Consultei aqui e consta uma mensalidade em aberto, ainda sem pagamento conciliado (boleto final "
        "7869). Essa informação é conforme os dados de 28/07/2026. " + ACK_ESCALACAO
    ]
    assert "inadimpl" not in _sem_acento(cliente.enviados[0][1])


async def test_cobranca_recebida_sem_fato_diz_que_nao_conseguiu_consultar(seam_lucas: SeamContext) -> None:
    from maezo.agents.lucas.graph import ACK_ESCALACAO

    fonte = _FonteFixa(Indisponivel("sujeito_nao_resolvido"))
    turno, registry, _ = _turno(
        seam_lucas, fonte=fonte, rascunho=_RASCUNHO_DO_INCIDENTE, rota_padrao="escalate_human"
    )
    cliente = _ClienteComDedup(registry)
    final = await _rodar(turno, _conversa("dl82-ind"), _handoff("cobranca_recebida"), cliente)

    assert final["route"] == "escalate_human"
    assert final["motivo_humano"] == "ambiguidade"  # nunca `inadimplencia_detectada` sem fato
    assert final["process_started"] is True
    assert [texto for _, texto, _ in cliente.enviados] == [
        "No momento não consegui consultar a situação da sua mensalidade. " + ACK_ESCALACAO
    ]


# --- DL-0086: pergunta de VALOR, respondida pelos fatos do `billing-status` ---------------------------


def _fatos_com_valores(*, conciliado: bool = True, ciclos: int = 0) -> FatosCobranca:
    from maezo.agents.lucas.fonte_cobranca import CompetenciaCobranca

    return FatosCobranca(
        status_conciliado=conciliado,
        ciclos_sem_conciliacao=ciclos,
        numero_boleto="****4821",
        cnab_ref="amh-billing:2026-09-30",
        valor_em_aberto="0.00",
        dias_atraso_max=0,
        vencimento_referencia="2026-09-10",
        competencias=(
            CompetenciaCobranca(
                competencia="2026-09",
                situacao="paga",
                vencimento="2026-09-10",
                valor_total="8389.53",
                valor_coparticipacao="120.00",
                valor_saldo="0.00",
                liquidado_em="2026-09-08",
                boleto="****4821",
            ),
        ),
    )


async def test_consulta_valores_com_fatos_responde_pelos_fatos_sem_processo(seam_lucas: SeamContext) -> None:
    # Revisao de seguranca do #709: TEXTO FIXO montado dos fatos; o rascunho do modelo nao e' usado.
    # 09/10/2026: boleto como "final NNNN", sem total zerado e sem o "Boleto de referencia" repetido.
    texto_fixo = (
        "Consultei aqui os valores do seu plano. Competência 09/2026: mensalidade de R$ 8.389,53, "
        "coparticipação de R$ 120,00, saldo de R$ 0,00, vencimento em 10/09/2026, paga em 08/09/2026, "
        "boleto final 4821."
    )
    turno, registry, _ = _turno(
        seam_lucas, fonte=_FonteFixa(_fatos_com_valores()), rascunho="Sua mensalidade e' de R$ 1,00."
    )
    cliente = _ClienteComDedup(registry)
    final = await _rodar(turno, _conversa("dl86-ok"), _handoff("consulta_valores"), cliente)

    assert final["route"] == "respond_member"
    assert final["admissibilidade"] == "RESPONDER"
    assert final["process_started"] is False
    assert final["mensagem"]["recusa_de_saida"] is False
    assert [texto for _, texto, _ in cliente.enviados] == [
        f"{texto_fixo} Essa informação é conforme os dados de 30/09/2026."
    ]


async def test_consulta_valores_sem_fatos_escala_e_nao_responde_valor(seam_lucas: SeamContext) -> None:
    turno, registry, _ = _turno(
        seam_lucas, fonte=_FonteFixa(Indisponivel("fonte_indisponivel")), rota_padrao="escalate_human"
    )
    cliente = _ClienteComDedup(registry)
    final = await _rodar(turno, _conversa("dl86-ind"), _handoff("consulta_valores"), cliente)

    assert final["route"] == "escalate_human"
    assert final["motivo_humano"] == "ambiguidade"
    assert final["process_started"] is True
    assert all("R$" not in texto for _, texto, _ in cliente.enviados)


async def test_consulta_valores_da_fonte_simulada_escala_porque_ela_nao_tem_valores(
    seam_lucas: SeamContext,
) -> None:
    turno, registry, _ = _turno(seam_lucas, rota_padrao="escalate_human")
    cliente = _ClienteComDedup(registry)
    final = await _rodar(turno, _conversa("dl86-sim"), _handoff("consulta_valores"), cliente)
    assert final["route"] == "escalate_human"


async def test_consulta_valores_com_atraso_continua_escalando_para_cobranca_humano(
    seam_lucas: SeamContext,
) -> None:
    fonte = _FonteFixa(_fatos_com_valores(conciliado=False, ciclos=1))
    turno, registry, _ = _turno(seam_lucas, fonte=fonte, rota_padrao="escalate_human")
    cliente = _ClienteComDedup(registry)
    final = await _rodar(turno, _conversa("dl86-atraso"), _handoff("consulta_valores"), cliente)

    assert final["route"] == "escalate_human"
    assert final["motivo_humano"] == "inadimplencia_detectada"
    assert final["roteamento_escalacao"] == "COBRANCA_HUMANO"
    assert final["process_started"] is True


# --- Gate, checkpointer, metricas ------------------------------------------------------------------


async def test_envio_passa_pelo_gate_do_principal_lucas(
    seam_lucas: SeamContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    vistos: list[SeamContext] = []
    original = lt.gate_whatsapp

    def _espia(inner: Any, seam: SeamContext) -> Any:
        vistos.append(seam)
        return original(inner, seam)

    monkeypatch.setattr(lt, "gate_whatsapp", _espia)
    turno, registry, _ = _turno(seam_lucas)
    await _rodar(turno, _conversa("gate"), _handoff("vencimento"), _ClienteComDedup(registry))
    assert [s.principal for s in vistos] == ["lucas"]
    assert vistos[0] is seam_lucas


def test_grafo_compila_sem_checkpointer(seam_lucas: SeamContext) -> None:
    turno, _, _ = _turno(seam_lucas)
    compilado = turno._compilar(object())
    assert compilado.checkpointer is None


async def test_metricas_do_turno_sao_do_lucas(
    seam_lucas: SeamContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    primeiras: list[str] = []
    erros: list[str] = []
    monkeypatch.setattr(
        lt, "record_agent_first_response", lambda *, agent_id, seconds: primeiras.append(agent_id)
    )
    import maezo.platform.observability as obs

    monkeypatch.setattr(obs, "record_agent_error", lambda *, agent, error_type: erros.append(agent))
    turno, registry, _ = _turno(seam_lucas)
    await _rodar(turno, _conversa("m"), _handoff("vencimento"), _ClienteComDedup(registry))
    assert primeiras == ["lucas"]

    class _Quebra:
        async def evaluate(self, *_a: Any, **_k: Any) -> Any:
            raise RuntimeError("bug de programacao")

        async def close(self) -> None:
            return None

    quebrado = dataclasses.replace(turno, dmn=_Quebra())  # type: ignore[arg-type]
    with pytest.raises(RuntimeError):
        await _rodar(quebrado, _conversa("m2"), _handoff("vencimento"), _ClienteComDedup(registry))
    assert erros == ["lucas"]


# --- §3: zona -------------------------------------------------------------------------------------


def test_lucas_declara_zona_geral() -> None:
    lt.exigir_zona_geral("lucas")


def test_agente_fora_da_zona_geral_e_recusado_no_boot(monkeypatch: pytest.MonkeyPatch) -> None:
    from maezo.agents import AgentLoader

    original = AgentLoader.load_by_id

    def _phi(self: AgentLoader, agent_id: str) -> Any:
        definicao = original(self, agent_id)
        return definicao.model_copy(update={"security_zone": "phi"})

    monkeypatch.setattr(AgentLoader, "load_by_id", _phi)
    with pytest.raises(lt.ZonaDeSegurancaError):
        lt.exigir_zona_geral("lucas")


def test_definicao_que_nao_carrega_recusa_em_vez_de_passar_por_geral(monkeypatch: pytest.MonkeyPatch) -> None:
    from maezo.agents import AgentLoader

    def _quebra(self: AgentLoader, agent_id: str) -> Any:
        raise FileNotFoundError(agent_id)

    monkeypatch.setattr(AgentLoader, "load_by_id", _quebra)
    with pytest.raises(lt.ZonaDeSegurancaError):
        lt.exigir_zona_geral("lucas")


def test_turno_recusa_seam_context_de_outro_principal() -> None:
    seam_helena = build_agent_seam_context(tenant=TENANT, agent_id="helena")
    with pytest.raises(ValueError):
        _turno(seam_helena)


def test_construir_o_turno_exige_a_zona(seam_lucas: SeamContext, monkeypatch: pytest.MonkeyPatch) -> None:
    def _recusa(agent_id: str) -> None:
        raise lt.ZonaDeSegurancaError(agent_id)

    monkeypatch.setattr(lt, "exigir_zona_geral", _recusa)
    with pytest.raises(lt.ZonaDeSegurancaError):
        _turno(seam_lucas)
