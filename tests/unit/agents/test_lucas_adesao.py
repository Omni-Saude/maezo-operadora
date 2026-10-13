"""Coletivo por ADESAO no Lucas (bateria 4 do simulador, 13/10/2026).

Beneficiario de plano de adesao ("ADESAO MEDIDA CERTA 50 STD") pediu a 2a via e ouviu "sua mensalidade e'
paga pela empresa contratante... fica com o RH". No Tasy o pagador da adesao e' a ADMINISTRADORA/ENTIDADE
(CNPJ), com um titulo coletivo; quem paga a mensalidade e' o beneficiario, a ela. Desde a AMH #236 o
billing-status devolve `pagador_tipo=desconhecido` nesse caso (nem PF nem "a empresa paga"), e a fonte do
Lucas confirma a adesao pelo vinculo do contrato TINA. Aqui se prova:

  - fonte: `desconhecido` + vinculo ATIVO de adesao -> `cobranca_administradora`; sem TINA, falha, vinculo
    cancelado ou de outro tipo -> nada muda; PJ nao consulta a TINA;
  - grafo: a parcela da pessoa e "o boleto e' emitido pela administradora" (2a via, vencimento e
    pagamento com ela); nunca "empresa contratante"/RH; o atraso do titulo da administradora nao escala;
  - empresarial continua "paga pela empresa contratante".
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from maezo.agents.lucas.fonte_cobranca import FatosCobranca
from maezo.agents.lucas.fonte_cobranca_amh import FonteCobrancaAmh
from maezo.agents.lucas.graph import (
    PROMPT_VERSIONS,
    TEXTO_COBRANCA_ADMINISTRADORA,
    LucasGraph,
    LucasState,
    cobranca_administradora,
    new_lucas_state,
    titulo_de_terceiro,
)
from maezo.agents.lucas.prompts import COBRANCA_ADMINISTRADORA_TEXTO_VERSION
from maezo.ports.billing_status import BillingStatusView, BillingSummary, CompetenciaBilling
from maezo.ports.errors import PortFailureReason, PortResult
from maezo.ports.tina import ElegibilidadeView, VinculoPlano
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport
from tests.evals.lucas.programa import DmnDraftLocal
from tests.support.audit_fakes import FakeStartAuditSink

_COMPETENCIAS: list[dict[str, Any]] = [
    {
        "competencia": "2026-10",
        "situacao": "em_aberto",
        "vencimento": "2026-10-10",
        "valor_total": "412.90",
        "valor_coparticipacao": "18.40",
        "valor_saldo": "412.90",
    },
    {
        "competencia": "2026-09",
        "situacao": "paga",
        "vencimento": "2026-09-10",
        "valor_total": "412.90",
        "valor_coparticipacao": "0.00",
        "valor_saldo": "0.00",
        "liquidado_em": "2026-09-09",
    },
]

#: O titulo da administradora com atraso: nunca e' o atraso do beneficiario.
_FATOS_ADESAO: dict[str, Any] = {
    "status_conciliado": False,
    "ciclos_sem_conciliacao": 2,
    "numero_boleto": "",
    "cnab_ref": "amh-billing:2026-10-12",
    "valor_em_aberto": "412.90",
    "dias_atraso_max": 3,
    "vencimento_referencia": "2026-10-10",
    "competencias_cobranca": _COMPETENCIAS,
    "cobranca_administradora": True,
}

_TIPOS_DE_COBRANCA = ("consulta_valores", "boleto", "2a_via", "vencimento", "status_pagamento")


class _Inferencia:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def generate(self, prompt: str, **kw: Any) -> str:
        del kw
        self.prompts.append(prompt)
        return "Narrativa para o humano."


class _Envio:
    def __init__(self) -> None:
        self.textos: list[str] = []

    async def send(self, to_hash: str, text: str, *, idempotency_key: str) -> dict[str, Any]:
        del to_hash, idempotency_key
        self.textos.append(text)
        return {"ok": True}


def _estado(tipo: str, *, intencao: str = "cobranca_info", **extra: Any) -> LucasState:
    return new_lucas_state(
        {
            "tenant_id": "amh",
            "conversation_id": "wa:amh:hk1_adesao",
            "canal": "whatsapp",
            "beneficiario_pseudo_id": "pseudo-adesao",
            "to_hash": "hash-adesao",
            "intencao": intencao,
            "tipo_solicitacao": tipo,
            **_FATOS_ADESAO,
            **extra,
        }
    )


async def _turno(estado: LucasState) -> tuple[dict[str, Any], _Envio, _Inferencia]:
    envio = _Envio()
    inferencia = _Inferencia()
    grafo = LucasGraph(
        inference=inferencia,  # type: ignore[arg-type]
        dmn=DmnDraftLocal(),
        cibseven=FakeCibSevenTransport(),
        audit_sink=FakeStartAuditSink(),
        whatsapp=envio,
        hoje=lambda: date(2026, 10, 13),
    )
    final = dict(await grafo.compile_graph().compile().ainvoke(estado))
    return final, envio, inferencia


# --- Grafo real ----------------------------------------------------------------------------------


@pytest.mark.parametrize("tipo", ["boleto", "2a_via", "vencimento", "status_pagamento"])
async def test_adesao_ouve_que_o_boleto_vem_da_administradora_com_a_parcela_dela(tipo: str) -> None:
    final, envio, inferencia = await _turno(_estado(tipo))

    assert final["route"] == "respond_member"
    assert final["process_started"] is False
    assert envio.textos == [
        f"{TEXTO_COBRANCA_ADMINISTRADORA} Sua mensalidade de outubro de 2026 é de R$ 412,90. (dados de 12/10)"
    ]
    assert final["mensagem"]["prompt_version"] == COBRANCA_ADMINISTRADORA_TEXTO_VERSION
    assert final["mensagem"]["recusa_de_saida"] is False
    assert inferencia.prompts == []


@pytest.mark.parametrize("tipo", _TIPOS_DE_COBRANCA)
async def test_adesao_nunca_ouve_empresa_contratante_nem_rh_nem_atraso(tipo: str) -> None:
    final, envio, _ = await _turno(_estado(tipo))
    [texto] = envio.textos
    for proibido in ("empresa contratante", "RH", "vencid", "atraso", "em aberto", "paga em"):
        assert proibido not in texto, proibido
    assert final.get("motivo_humano") is None or final["route"] == "respond_member"


async def test_adesao_pergunta_de_valor_comeca_pela_parcela() -> None:
    _, envio, _ = await _turno(_estado("consulta_valores", competencia="2026-09"))
    assert envio.textos == [
        "Sua mensalidade de setembro de 2026 é de R$ 412,90. "
        f"{TEXTO_COBRANCA_ADMINISTRADORA} (dados de 12/10)"
    ]


async def test_adesao_coparticipacao_e_da_pessoa() -> None:
    final, envio, _ = await _turno(_estado("consulta_valores", cobranca_foco="coparticipacao"))
    [texto] = envio.textos
    assert texto.startswith("Em outubro de 2026 sua coparticipação foi de R$ 18,40.")
    assert final["mensagem"]["fatos"]["competencias"] == [
        {"competencia": "2026-10", "valor_total": "412.90", "valor_coparticipacao": "18.40"}
    ]


async def test_adesao_inadimplencia_do_titulo_da_administradora_nao_escala() -> None:
    final, envio, _ = await _turno(_estado("status_pagamento", intencao="inadimplencia"))
    assert final["route"] == "respond_member"
    assert envio.textos and TEXTO_COBRANCA_ADMINISTRADORA in envio.textos[0]


async def test_empresarial_continua_paga_pela_empresa() -> None:
    _, envio, _ = await _turno(
        _estado("2a_via", pagador_tipo="pessoa_juridica", cobranca_administradora=False)
    )
    [texto] = envio.textos
    assert "empresa contratante" in texto
    assert "administradora" not in texto


def test_empresa_vence_a_marca_de_administradora_e_so_true_conta() -> None:
    marcado: Any = {"cobranca_administradora": True}
    texto: Any = {"cobranca_administradora": "true"}
    empresa: Any = {"cobranca_administradora": True, "pagador_tipo": "pessoa_juridica"}
    vazio: Any = {}
    assert cobranca_administradora(marcado) is True
    assert cobranca_administradora(texto) is False
    assert cobranca_administradora(empresa) is False
    assert titulo_de_terceiro(empresa) is True
    assert titulo_de_terceiro(vazio) is False


def test_a_versao_do_texto_esta_exportada() -> None:
    assert PROMPT_VERSIONS["cobranca_administradora"] == COBRANCA_ADMINISTRADORA_TEXTO_VERSION


# --- Fonte AMH + TINA ----------------------------------------------------------------------------

REF = "amh:psr:v1:1b2f3a4c-5d6e-4f70-8a9b-0c1d2e3f4a5b"


def _view(pagador: str) -> BillingStatusView:
    return BillingStatusView(
        portable_subject_ref=REF,
        as_of="2026-10-13T12:00:00Z",
        fonte_atualizada_em="2026-10-12T09:00:00Z",
        resumo=BillingSummary(
            status_conciliado=True,
            ciclos_sem_conciliacao=0,
            valor_em_aberto="412.90",
            dias_atraso_max=0,
            pagador_tipo=pagador,
            criterio_conciliacao="situacao_paga_ou_liquidada",
        ),
        competencias=(
            CompetenciaBilling(
                competencia="2026-10",
                parcela=10,
                vencimento="2026-10-10",
                situacao="em_aberto",
                valor_total="412.90",
                valor_coparticipacao="18.40",
                valor_saldo="412.90",
                liquidado_em=None,
                boleto_numero_mascarado="****4821",
                boleto_disponivel_online=None,
            ),
        ),
        campos_ausentes=frozenset(),
    )


def _vinculo(tipo: str | None, cancelamento: str | None = None) -> VinculoPlano:
    return VinculoPlano(
        carteirinha_mascarada=None,
        plano="ADESAO SINTETICO",
        registro_ans_plano=None,
        segmentacao=None,
        acomodacao=None,
        tipo_contratacao=tipo,
        vigencia_inicio="2024-01-01",
        cancelamento=cancelamento,
        atendimento_liberado=True,
        situacao_vinculo=None,
        situacao_contrato=None,
        titular=True,
        relacao_dependencia=None,
        titular_ref=None,
    )


class _Billing:
    def __init__(self, view: BillingStatusView) -> None:
        self.view = view

    async def get_billing_status(self, ref: str, **kw: Any) -> PortResult[BillingStatusView]:
        del ref, kw
        return PortResult.ok(self.view)


class _Tina:
    def __init__(self, resultado: PortResult[ElegibilidadeView] | BaseException) -> None:
        self.resultado = resultado
        self.chamadas: list[dict[str, Any]] = []

    async def get_elegibilidade(self, ref: str, **kw: Any) -> PortResult[ElegibilidadeView]:
        self.chamadas.append({"ref": ref, **kw})
        if isinstance(self.resultado, BaseException):
            raise self.resultado
        return self.resultado


def _elegibilidade(*vinculos: VinculoPlano, ref: str = REF) -> PortResult[ElegibilidadeView]:
    return PortResult.ok(
        ElegibilidadeView(
            portable_subject_ref=ref,
            ativo=True,
            vinculos=vinculos,
            fonte_atualizada_em="2026-10-12T09:00:00Z",
            campos_ausentes=frozenset(),
        )
    )


class _Resolvedor:
    identidade_verificada = True

    async def portable_ref(self, pseudo_id: str, *, phone_hash: str | None) -> str | None:
        del pseudo_id, phone_hash
        return REF


class _Consentimento:
    async def decisao(self, portable_ref: str, purpose_of_use: str) -> str | None:
        del portable_ref, purpose_of_use
        return "consent-1"


def _fonte(pagador: str, tina: Any, *, valores: bool = True) -> FonteCobrancaAmh:
    billing: Any = _Billing(_view(pagador))
    return FonteCobrancaAmh(
        billing=billing,
        resolvedor=_Resolvedor(),
        consentimento=_Consentimento(),
        purpose_of_use="atendimento_whatsapp",
        fatos_de_valor=valores,
        tina=tina,
        tina_purpose_of_use="sharing_amh_internal" if tina is not None else None,
    )


@pytest.mark.parametrize("valores", [True, False])
async def test_fonte_desconhecido_com_vinculo_de_adesao_e_cobranca_da_administradora(valores: bool) -> None:
    tina = _Tina(_elegibilidade(_vinculo("Coletivo por adesão")))
    fatos = await _fonte("desconhecido", tina, valores=valores).fatos("p", None)
    assert isinstance(fatos, FatosCobranca)
    entrada = fatos.como_entrada_lucas()
    assert entrada["cobranca_administradora"] is True
    assert entrada["numero_boleto"] == ""
    assert "pagador_tipo" not in entrada
    assert tina.chamadas[0]["purpose_of_use"] == "sharing_amh_internal"
    assert tina.chamadas[0]["consent_decision_ref"] == "base-legal:execucao-de-contrato"


@pytest.mark.parametrize(
    "resultado",
    [
        _elegibilidade(_vinculo("Coletivo empresarial")),
        _elegibilidade(_vinculo("Coletivo")),
        _elegibilidade(_vinculo("Coletivo por adesão", cancelamento="2025-12-31")),
        _elegibilidade(_vinculo("Coletivo por adesão"), ref="amh:psr:v1:outro"),
        _elegibilidade(_vinculo(None)),
        PortResult.refused(PortFailureReason.CONSENT_REQUIRED),
        TimeoutError(),
    ],
)
async def test_fonte_sem_adesao_confirmada_nao_muda_nada(resultado: Any) -> None:
    fatos = await _fonte("desconhecido", _Tina(resultado)).fatos("p", None)
    assert isinstance(fatos, FatosCobranca)
    entrada = fatos.como_entrada_lucas()
    assert "cobranca_administradora" not in entrada
    assert entrada["numero_boleto"] == "****4821"


@pytest.mark.parametrize("pagador", ["pessoa_juridica", "pessoa_fisica"])
async def test_fonte_com_pagador_conhecido_nao_consulta_a_tina(pagador: str) -> None:
    tina = _Tina(_elegibilidade(_vinculo("Coletivo por adesão")))
    fatos = await _fonte(pagador, tina).fatos("p", None)
    assert isinstance(fatos, FatosCobranca)
    assert tina.chamadas == []
    assert fatos.cobranca_administradora is False


async def test_fonte_sem_tina_e_o_comportamento_de_antes() -> None:
    fatos = await _fonte("desconhecido", None).fatos("p", None)
    assert isinstance(fatos, FatosCobranca)
    assert fatos.cobranca_administradora is False
