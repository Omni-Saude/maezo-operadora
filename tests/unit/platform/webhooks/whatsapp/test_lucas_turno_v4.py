"""`LucasTurno` com a chave `MAEZO_LUCAS_V4` (DL-0114): quem recebe o codigo de barras e onde ele vive.

Contra o grafo REAL do Lucas, a DMN DRAFT lida do XML e a fonte v4 com PORTAS FAKE (os contratos novos da AMH
ainda nao estao publicados). O que se prova:
  - o codigo de barras so' vai ao PAGADOR PF verificado, so' na 2a via, e so' com a chave E a fonte v4;
  - ele entra no texto no ULTIMO passo (remetente do turno): nao esta' no estado final, na referencia da
    memoria (hash do texto COM o marcador) nem no que o turno devolve ao despachante;
  - quem nao e' o pagador nem chega a consultar a fonte; falha da fonte = o texto de antes;
  - o detalhamento por beneficiario so' vale para o pagador, na pergunta de valor, sem lista.
"""

from __future__ import annotations

import json
import re
from typing import Any

import pytest

from maezo.agents.lucas.fonte_cobranca import CompetenciaCobranca, FatosCobranca
from maezo.agents.lucas.fonte_v4 import (
    MARCADOR_CODIGO,
    BeneficiarioDetalhe,
    BillingDetalhamentoView,
    BillingSegundaViaView,
    BoletoSegundaVia,
    CompetenciaDetalhada,
    CredenciaisDoTurno,
    FatosV4,
    FonteV4,
    GrupoPagador,
)
from maezo.agents.lucas.graph import (
    SEGUNDA_VIA_CANAIS,
    TEXTO_NAO_PAGADOR_V4,
    TEXTO_SEM_CODIGO_V4,
    ref_de_resposta,
)
from maezo.gateway.pseudonymizer import Pseudonymizer
from maezo.gateway.seams import SeamContext
from maezo.gateway.tool_registry import build_agent_seam_context
from maezo.platform.webhooks.whatsapp import lucas_turno as lt
from maezo.platform.webhooks.whatsapp.dedup import WhatsAppDedupGuard
from maezo.ports.errors import PortFailureReason as Reason
from maezo.ports.errors import PortResult
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport
from tests.evals.lucas.programa import DmnDraftLocal, InferenciaRoteirizada
from tests.support.audit_fakes import FakeStartAuditSink
from tests.support.dedup_fakes import FakeDedupRegistry
from tests.unit.platform.webhooks.whatsapp.test_lucas_turno import (
    TENANT,
    _ClienteComDedup,
    _conversa,
    _FonteFixa,
    _handoff,
    _rodar,
)

REF = "amh:psr:v1:1b2f3a4c-5d6e-4f70-8a9b-0c1d2e3f4a5b"
LINHA = "00191.23454 67890.123457 67890.123457 7 10000000012345"
CODIGO = "00197100000000123451234567890123456789012345"


@pytest.fixture(scope="module")
def seam() -> SeamContext:
    return build_agent_seam_context(tenant=TENANT, agent_id="lucas")


def _fatos(
    *, pagador: str = "pessoa_fisica", e_pagador: bool | None = True, adm: bool = False
) -> FatosCobranca:
    competencia = CompetenciaCobranca(
        competencia="2026-10",
        situacao="em_aberto",
        vencimento="2026-10-20",
        valor_total="320.25",
        valor_coparticipacao="0.00",
        valor_saldo="320.25",
        valor_boleto_pagador="1280.40" if e_pagador and pagador == "pessoa_fisica" else None,
    )
    return FatosCobranca(
        status_conciliado=False,
        ciclos_sem_conciliacao=0,
        numero_boleto="",
        cnab_ref="amh-billing:2026-10-12",
        valor_em_aberto="320.25",
        dias_atraso_max=0,
        vencimento_referencia="2026-10-20",
        competencias=(competencia,),
        pagador_tipo=pagador,
        cobranca_administradora=adm,
        tipo_contratacao="individual_familiar",
        beneficiario_e_pagador=e_pagador,
        valor_boleto_pagador="1280.40" if e_pagador and pagador == "pessoa_fisica" else None,
    )


def _boleto(**mudancas: Any) -> BoletoSegundaVia:
    base: dict[str, Any] = {
        "competencia": "2026-10",
        "vencimento": "2026-10-20",
        "situacao": "em_aberto",
        "valor_titulo": "1280.40",
        "disponivel": True,
        "indisponivel_motivo": None,
        "linha_digitavel": LINHA,
        "codigo_barras": CODIGO,
    }
    return BoletoSegundaVia(**{**base, **mudancas})


class _PortaSegundaVia:
    def __init__(self, resultado: Any) -> None:
        self.resultado = resultado
        self.chamadas: list[dict[str, Any]] = []

    async def get_segunda_via(self, ref: str, **kw: Any) -> Any:
        self.chamadas.append({"ref": ref, **kw})
        if isinstance(self.resultado, Exception):
            raise self.resultado
        return self.resultado


class _PortaDetalhamento:
    def __init__(self, resultado: Any) -> None:
        self.resultado = resultado
        self.chamadas: list[dict[str, Any]] = []

    async def get_billing_detalhamento(self, ref: str, **kw: Any) -> Any:
        self.chamadas.append({"ref": ref, **kw})
        return self.resultado


class _Resolvedor:
    identidade_verificada = True

    async def portable_ref(self, pseudo_id: str, *, phone_hash: str | None) -> str | None:
        return REF


class _Consentimento:
    async def decisao(self, portable_ref: str, purpose_of_use: str) -> str | None:
        return "consent-1"


def _via(*boletos: BoletoSegundaVia) -> PortResult[BillingSegundaViaView]:
    return PortResult.ok(
        BillingSegundaViaView(
            portable_subject_ref=REF,
            boletos=boletos or (_boleto(),),
            fonte_atualizada_em="2026-10-15T12:00:00Z",
        )
    )


def _detalhamento() -> PortResult[BillingDetalhamentoView]:
    def pessoa(rotulo: str, relacao: str, pre: str, copart: str, total: str) -> BeneficiarioDetalhe:
        return BeneficiarioDetalhe(
            rotulo=rotulo,
            relacao=relacao,
            e_voce=rotulo == "você",
            valor_pre_estabelecido=pre,
            valor_coparticipacao=copart,
            valor_adicionais="0.00",
            valor_outros="0.00",
            valor_total=total,
        )

    # FASE 4 (18/10/2026): o detalhe so' aparece quando FECHA com o boleto do mes (R$ 1.280,40 em `_fatos`).
    grupo = GrupoPagador(
        total_mensalidade="1280.40",
        total_pre_estabelecido="1260.40",
        total_coparticipacao="20.00",
        total_adicionais="0.00",
        qt_beneficiarios=2,
        detalhamento_fecha=True,
        beneficiarios=(
            pessoa("você", "titular", "300.25", "20.00", "320.25"),
            pessoa("dependente 1", "dependente", "960.15", "0.00", "960.15"),
        ),
    )
    return PortResult.ok(
        BillingDetalhamentoView(
            portable_subject_ref=REF,
            responsavel_financeiro="voce",
            financeiro_liberado=True,
            competencias=(CompetenciaDetalhada("2026-10", (grupo,)),),
        )
    )


def _turno(
    seam: SeamContext,
    *,
    fatos: FatosCobranca,
    v4: bool = True,
    via: Any = None,
    detalhamento: Any = None,
    sem_fonte_v4: bool = False,
) -> lt.LucasTurno:
    fonte_v4 = (
        None
        if sem_fonte_v4
        else FonteV4(
            resolvedor=_Resolvedor(),
            consentimento=_Consentimento(),
            segunda_via=via,
            detalhamento=detalhamento,
            timeout_seconds=5.0,
        )
    )
    return lt.LucasTurno(
        tenant_id=TENANT,
        inference=InferenciaRoteirizada(None, rota_padrao="respond_member"),  # type: ignore[arg-type]
        dmn=DmnDraftLocal(),
        cibseven=FakeCibSevenTransport(),
        audit_sink=FakeStartAuditSink(),
        seam_context=seam,
        dedup=WhatsAppDedupGuard(registry=FakeDedupRegistry(), pseudonymizer=Pseudonymizer(), tenant=TENANT),
        fonte=_FonteFixa(fatos),
        lucas_v4=v4,
        fonte_v4=fonte_v4,
    )


async def _executar(
    turno: lt.LucasTurno, handoff: dict[str, Any], semente: str = "v4"
) -> tuple[dict[str, Any], list[str]]:
    cliente = _ClienteComDedup(FakeDedupRegistry())
    final = await _rodar(turno, _conversa(semente), handoff, cliente)
    return final, [texto for _, texto, _ in cliente.enviados]


# --- 1. a 2a via do pagador --------------------------------------------------------------------------
async def test_pagador_recebe_o_codigo_no_texto_enviado_e_ele_nao_esta_em_nenhum_outro_lugar(
    seam: SeamContext,
) -> None:
    via = _PortaSegundaVia(_via())
    final, enviados = await _executar(_turno(seam, fatos=_fatos(), via=via), _handoff("boleto_2via", None))
    (enviado,) = enviados
    assert LINHA in enviado and MARCADOR_CODIGO not in enviado
    assert "Código de barras" in enviado and "aplicativo Austa Clínicas" in enviado
    # o estado final que o turno devolve ao despachante nunca teve o codigo (so' o marcador no texto do grafo)
    serializado = json.dumps(final, default=str)
    assert LINHA not in serializado and CODIGO not in serializado and "10000000012345" not in serializado
    assert final["mensagem"]["texto"] == enviado.replace(LINHA, MARCADOR_CODIGO)
    # a referencia da memoria e a da resposta sao o hash do texto COM o marcador
    esperado = ref_de_resposta(enviado.replace(LINHA, MARCADOR_CODIGO))
    assert final["resposta_memoria_ref"] == esperado and final["resposta_enviada_ref"] == esperado
    assert final["route"] == "respond_member" and final["process_started"] is False


async def test_a_fonte_recebe_um_request_ref_opaco_e_a_competencia_do_pedido(seam: SeamContext) -> None:
    via = _PortaSegundaVia(_via())
    await _executar(_turno(seam, fatos=_fatos(), via=via), _handoff("boleto_2via", "2026-09"))
    (chamada,) = via.chamadas
    assert chamada["competencia"] == "2026-09" and chamada["purpose_of_use"] == "atendimento_whatsapp"
    assert re.fullmatch(r"[A-Za-z0-9_.:-]{8,64}", chamada["request_ref"])
    assert chamada["request_ref"].startswith("hk1_") and "SINTETICO" not in chamada["request_ref"]
    assert "consent_decision_ref" not in chamada


async def test_reentrega_do_mesmo_message_id_nao_reenvia_o_codigo(seam: SeamContext) -> None:
    turno = _turno(seam, fatos=_fatos(), via=_PortaSegundaVia(_via()))
    cliente = _ClienteComDedup(FakeDedupRegistry())
    handoff = _handoff("boleto_2via", None)
    await _rodar(turno, _conversa("v4"), handoff, cliente)
    await _rodar(turno, _conversa("v4"), handoff, cliente)
    assert len(cliente.enviados) == 1


async def test_sem_codigo_disponivel_o_texto_e_o_de_nao_encontrei(seam: SeamContext) -> None:
    sem_bloqueto = _boleto(
        disponivel=False, indisponivel_motivo="sem_bloqueto", linha_digitavel=None, codigo_barras=None
    )
    for resultado in (_via(sem_bloqueto), PortResult.refused(Reason.NOT_FOUND)):
        _, enviados = await _executar(
            _turno(seam, fatos=_fatos(), via=_PortaSegundaVia(resultado)), _handoff("boleto_2via", None)
        )
        assert enviados == [TEXTO_SEM_CODIGO_V4]


@pytest.mark.parametrize(
    "resultado",
    [
        PortResult.refused(Reason.UPSTREAM_UNAVAILABLE),
        PortResult.refused(Reason.TIMEOUT),
        RuntimeError("fora"),
    ],
)
async def test_falha_da_fonte_deixa_o_texto_de_antes_e_nunca_derruba_o_turno(
    seam: SeamContext, resultado: Any
) -> None:
    final, enviados = await _executar(
        _turno(seam, fatos=_fatos(), via=_PortaSegundaVia(resultado)), _handoff("boleto_2via", None)
    )
    assert final["route"] == "respond_member"
    assert len(enviados) == 1 and SEGUNDA_VIA_CANAIS in enviados[0] and MARCADOR_CODIGO not in enviados[0]


async def test_chave_desligada_nem_consulta_a_fonte_e_o_texto_e_o_de_antes(seam: SeamContext) -> None:
    via = _PortaSegundaVia(_via())
    _, enviados = await _executar(
        _turno(seam, fatos=_fatos(), via=via, v4=False), _handoff("boleto_2via", None)
    )
    assert via.chamadas == [] and SEGUNDA_VIA_CANAIS in enviados[0] and LINHA not in enviados[0]


async def test_sem_a_fonte_v4_so_ha_o_que_a_rota_030_entrega(seam: SeamContext) -> None:
    _, enviados = await _executar(
        _turno(seam, fatos=_fatos(), sem_fonte_v4=True), _handoff("boleto_2via", None)
    )
    assert SEGUNDA_VIA_CANAIS in enviados[0] and LINHA not in enviados[0]


async def test_quem_nao_e_o_pagador_nem_chega_a_consultar_a_fonte(seam: SeamContext) -> None:
    via, det = _PortaSegundaVia(_via()), _PortaDetalhamento(_detalhamento())
    for subtipo in ("boleto_2via", "consulta_valores", "vencimento"):
        _, enviados = await _executar(
            _turno(seam, fatos=_fatos(e_pagador=False), via=via, detalhamento=det),
            _handoff(subtipo, None),
            semente=f"np-{subtipo}",
        )
        assert enviados == [TEXTO_NAO_PAGADOR_V4]
    assert via.chamadas == [] and det.chamadas == []


@pytest.mark.parametrize(
    "fatos",
    [
        _fatos(pagador="pessoa_juridica", e_pagador=None),
        _fatos(adm=True),
        _fatos(e_pagador=None),
    ],
)
async def test_empresa_adesao_e_pagador_desconhecido_nao_recebem_codigo(
    seam: SeamContext, fatos: FatosCobranca
) -> None:
    via = _PortaSegundaVia(_via())
    _, enviados = await _executar(_turno(seam, fatos=fatos, via=via), _handoff("boleto_2via", None))
    assert via.chamadas == [] and all(LINHA not in t and MARCADOR_CODIGO not in t for t in enviados)


async def test_a_mensagem_do_beneficiario_nunca_vira_chave_da_fonte(seam: SeamContext) -> None:
    """O handoff nao tem texto; a fonte v4 so' recebe o pseudonimo, a competencia e o request_ref."""
    via = _PortaSegundaVia(_via())
    await _executar(_turno(seam, fatos=_fatos(), via=via), _handoff("boleto_2via", None))
    assert set(via.chamadas[0]) == {"ref", "purpose_of_use", "request_ref", "competencia", "timeout_seconds"}


# --- 2. o detalhamento do pagador --------------------------------------------------------------------
async def test_pagador_perguntando_o_valor_ouve_o_total_e_o_detalhe_sem_nome(seam: SeamContext) -> None:
    det = _PortaDetalhamento(_detalhamento())
    final, enviados = await _executar(
        _turno(seam, fatos=_fatos(), detalhamento=det), _handoff("consulta_valores", None)
    )
    (chamada,) = det.chamadas
    assert chamada["competencia"] == "2026-10" and chamada["purpose_of_use"] == "atendimento_whatsapp"
    (texto,) = enviados
    assert texto.startswith("O valor total da mensalidade de outubro de 2026 é de R$ 1.280,40")
    assert "Detalhe por beneficiário:" in texto
    assert "• Você: pré-estabelecido R$ 300,25 + coparticipação R$ 20,00 = R$ 320,25" in texto
    assert "• Dependente 1: pré-estabelecido R$ 960,15 + coparticipação R$ 0,00 = R$ 960,15" in texto
    assert final["mensagem"]["recusa_de_saida"] is False


async def test_pagador_com_competencia_pedida_consulta_essa_competencia(seam: SeamContext) -> None:
    det = _PortaDetalhamento(_detalhamento())
    await _executar(_turno(seam, fatos=_fatos(), detalhamento=det), _handoff("consulta_valores", "2026-10"))
    assert det.chamadas[0]["competencia"] == "2026-10"


async def test_sem_o_detalhamento_o_pagador_ouve_o_total_e_a_parte(seam: SeamContext) -> None:
    for det in (_PortaDetalhamento(PortResult.refused(Reason.UPSTREAM_UNAVAILABLE)), None):
        _, enviados = await _executar(
            _turno(seam, fatos=_fatos(), detalhamento=det), _handoff("consulta_valores", None)
        )
        assert enviados[0].startswith("O valor total da mensalidade de outubro de 2026 é de R$ 1.280,40")
        assert "•" not in enviados[0] and "A sua parte é de R$ 320,25" in enviados[0]


@pytest.mark.parametrize("foco", ["coparticipacao", "vencimento", "saldo"])
async def test_so_o_foco_geral_ou_valor_pede_o_detalhamento(seam: SeamContext, foco: str) -> None:
    det = _PortaDetalhamento(_detalhamento())
    await _executar(
        _turno(seam, fatos=_fatos(), detalhamento=det),
        {**_handoff("consulta_valores", None), "cobranca_foco": foco},
    )
    assert det.chamadas == []


# --- 3. o remetente do turno e a entrada do grafo ----------------------------------------------------
class _Envio:
    def __init__(self) -> None:
        self.enviados: list[str] = []

    async def send(self, to_hash: str, text: str, *, idempotency_key: str) -> dict[str, Any]:
        self.enviados.append(text)
        return {"ok": True}


async def test_o_remetente_troca_o_marcador_e_guarda_so_o_texto_com_o_marcador() -> None:
    dedup = WhatsAppDedupGuard(registry=FakeDedupRegistry(), pseudonymizer=Pseudonymizer(), tenant=TENANT)
    inner = _Envio()
    texto = f"Antes\n{MARCADOR_CODIGO}\nDepois"
    remetente = lt._RemetenteDoLucas(
        inner=inner, dedup=dedup, message_id="wamid.X", credenciais=CredenciaisDoTurno(LINHA)
    )
    await remetente.send("h", texto, idempotency_key="ignorada")
    assert inner.enviados == [f"Antes\n{LINHA}\nDepois"]
    assert remetente.ultimo_texto == texto and LINHA not in remetente.ultimo_texto


@pytest.mark.parametrize("credenciais", [None, CredenciaisDoTurno(None)])
async def test_marcador_sem_credencial_nunca_chega_cru_ao_beneficiario(credenciais: Any) -> None:
    dedup = WhatsAppDedupGuard(registry=FakeDedupRegistry(), pseudonymizer=Pseudonymizer(), tenant=TENANT)
    inner = _Envio()
    remetente = lt._RemetenteDoLucas(inner=inner, dedup=dedup, message_id="wamid.Y", credenciais=credenciais)
    await remetente.send("h", f"x {MARCADOR_CODIGO}", idempotency_key="i")
    assert inner.enviados == [TEXTO_SEM_CODIGO_V4]


async def test_texto_sem_marcador_sai_como_sempre_com_ou_sem_credencial() -> None:
    dedup = WhatsAppDedupGuard(registry=FakeDedupRegistry(), pseudonymizer=Pseudonymizer(), tenant=TENANT)
    inner = _Envio()
    remetente = lt._RemetenteDoLucas(
        inner=inner, dedup=dedup, message_id="wamid.Z", credenciais=CredenciaisDoTurno(LINHA)
    )
    await remetente.send("h", "Texto comum.", idempotency_key="i")
    assert inner.enviados == ["Texto comum."]


def test_a_entrada_do_grafo_recebe_os_fatos_v4_e_nenhuma_credencial() -> None:
    conversa = _conversa("entrada")
    validado = lt.validar_handoff({**_handoff("boleto_2via", None), "message_ref": "ref"})
    fatos_v4 = FatosV4(
        responsavel_financeiro="voce",
        segunda_via_estado="com_codigo",
        segunda_via_boletos=(_boleto(),),
    )
    estado = lt.entrada_do_lucas(validado, conversa, tenant_id=TENANT, fatos=_fatos(), fatos_v4=fatos_v4)
    assert estado["segunda_via_estado"] == "com_codigo" and estado["responsavel_financeiro"] == "voce"
    assert LINHA not in json.dumps(estado, default=str) and CODIGO not in json.dumps(estado, default=str)
    sem = lt.entrada_do_lucas(validado, conversa, tenant_id=TENANT, fatos=_fatos())
    assert "segunda_via_estado" not in sem and "detalhamento_pagador" not in sem


def test_o_turno_desligado_por_padrao() -> None:
    campos = lt.LucasTurno.__dataclass_fields__
    assert campos["lucas_v4"].default is False and campos["fonte_v4"].default is None
    assert campos["fonte_v4"].repr is False  # a fonte (com a credencial em transito) nunca no repr do turno
