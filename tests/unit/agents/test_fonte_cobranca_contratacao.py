"""`FonteCobrancaAmh` com o billing-status 0.3.0: tipo de contratacao, beneficiario = pagador, boleto.

Decisao do dono de 14/10/2026. A fonte so' LE os tres campos para `FatosCobranca`; o grafo do Lucas e' de
outro PR (`como_entrada_lucas` nao os leva ainda). Nada e' inventado: ausente continua ausente.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from maezo.agents.lucas.fonte_cobranca import FatosCobranca, Indisponivel
from maezo.agents.lucas.fonte_cobranca_amh import FonteCobrancaAmh
from maezo.ports.billing_status import BillingStatusView, BillingSummary, CompetenciaBilling
from maezo.ports.errors import PortResult

REF = "amh:psr:v1:1b2f3a4c-5d6e-4f70-8a9b-0c1d2e3f4a5b"


def _competencia(competencia: str, situacao: str, valor_boleto: str | None) -> CompetenciaBilling:
    return CompetenciaBilling(
        competencia=competencia,
        parcela=1,
        vencimento="2026-08-10",
        situacao=situacao,
        valor_total="320.25",
        valor_coparticipacao="0.00",
        valor_saldo="320.25",
        liquidado_em=None,
        boleto_numero_mascarado="****0002",
        boleto_disponivel_online=None,
        valor_boleto_pagador=valor_boleto,
    )


def _view(
    *,
    pagador: str = "pessoa_fisica",
    tipo: str | None = "individual_familiar",
    eh_pagador: bool | None = True,
    valor: str | None = "1280.40",
) -> BillingStatusView:
    return BillingStatusView(
        portable_subject_ref=REF,
        as_of="2026-10-14T12:00:00Z",
        fonte_atualizada_em="2026-10-13T09:00:00Z",
        resumo=BillingSummary(
            status_conciliado=False,
            ciclos_sem_conciliacao=2,
            valor_em_aberto="640.50",
            dias_atraso_max=61,
            pagador_tipo=pagador,
            criterio_conciliacao="situacao_paga_ou_liquidada",
            tipo_contratacao=tipo,
            eh_o_pagador=eh_pagador,
        ),
        competencias=(_competencia("2026-08", "vencida", valor), _competencia("2026-07", "vencida", valor)),
        campos_ausentes=frozenset(),
    )


class _Billing:
    def __init__(self, view: BillingStatusView) -> None:
        self.view = view

    async def get_billing_status(self, ref: str, **kw: Any) -> PortResult[BillingStatusView]:
        return PortResult.ok(self.view)


class _Resolvedor:
    def __init__(self, verificado: bool) -> None:
        self.identidade_verificada = verificado

    async def portable_ref(self, pseudo_id: str, *, phone_hash: str | None) -> str | None:
        return REF


class _Consentimento:
    async def decisao(self, portable_ref: str, purpose_of_use: str) -> str | None:
        return "consent-1"


def _fonte(view: BillingStatusView, *, valores: bool = True) -> FonteCobrancaAmh:
    return FonteCobrancaAmh(
        billing=_Billing(view),
        resolvedor=_Resolvedor(verificado=valores),
        consentimento=_Consentimento(),
        purpose_of_use="atendimento_whatsapp",
        fatos_de_valor=valores,
    )


async def _fatos(view: BillingStatusView, *, valores: bool = True) -> FatosCobranca:
    fatos = await _fonte(view, valores=valores).fatos("pseudo-1", None)
    assert isinstance(fatos, FatosCobranca), fatos
    return fatos


async def test_pagador_pf_traz_tipo_flag_e_o_total_do_boleto() -> None:
    fatos = await _fatos(_view())
    assert fatos.tipo_contratacao == "individual_familiar"
    assert fatos.beneficiario_e_pagador is True
    assert fatos.valor_boleto_pagador == "1280.40"


async def test_adesao_vira_coletivo_adesao_sem_valor_de_boleto() -> None:
    """Adesao: a AMH entrega pagador `desconhecido`, flag nula e nenhum valor; a fonte nao inventa."""
    fatos = await _fatos(_view(pagador="desconhecido", tipo="coletivo_adesao", eh_pagador=None, valor=None))
    assert fatos.tipo_contratacao == "coletivo_adesao"
    assert fatos.beneficiario_e_pagador is None
    assert fatos.valor_boleto_pagador is None


async def test_empresarial_pj_nunca_mostra_o_valor_da_fatura_da_empresa() -> None:
    fatos = await _fatos(
        _view(pagador="pessoa_juridica", tipo="coletivo_empresarial", eh_pagador=None, valor="107655.44")
    )
    assert fatos.tipo_contratacao == "coletivo_empresarial"
    assert fatos.beneficiario_e_pagador is None
    assert fatos.valor_boleto_pagador is None  # defesa em profundidade, mesmo que a AMH o mandasse


@pytest.mark.parametrize(
    ("pagador", "eh_pagador"),
    [("pessoa_fisica", False), ("pessoa_fisica", None), ("desconhecido", True), ("pessoa_juridica", True)],
)
async def test_valor_so_com_pagador_pf_e_beneficiario_igual_ao_pagador(
    pagador: str, eh_pagador: bool | None
) -> None:
    fatos = await _fatos(_view(pagador=pagador, eh_pagador=eh_pagador, valor="1280.40"))
    assert fatos.valor_boleto_pagador is None


async def test_dependente_de_pagador_pf_tem_flag_falsa_e_nenhum_valor() -> None:
    fatos = await _fatos(_view(eh_pagador=False, valor=None))
    assert fatos.beneficiario_e_pagador is False
    assert fatos.valor_boleto_pagador is None


@pytest.mark.parametrize("tipo", [None, "desconhecido", "Coletivo", "", 3])
async def test_tipo_ausente_desconhecido_ou_fora_do_enum_fica_ausente(tipo: Any) -> None:
    fatos = await _fatos(_view(tipo=tipo))
    assert fatos.tipo_contratacao is None


@pytest.mark.parametrize("valor", ["1280.4", "R$ 1280,40", "1280", "-5.00", "", "abc", None])
async def test_valor_fora_da_forma_do_contrato_vira_ausente(valor: str | None) -> None:
    fatos = await _fatos(_view(valor=valor))
    assert fatos.valor_boleto_pagador is None


async def test_valor_e_o_da_competencia_de_referencia() -> None:
    base = _view()
    view = replace(
        base,
        competencias=(
            _competencia("2026-08", "vencida", "1280.40"),
            _competencia("2026-07", "vencida", "999.99"),
        ),
    )
    fonte = _fonte(view)
    fatos = await fonte.fatos("pseudo-1", "2026-07")
    assert isinstance(fatos, FatosCobranca)
    assert fatos.valor_boleto_pagador == "999.99"


async def test_sem_fatos_de_valor_o_total_do_boleto_e_o_flag_nao_saem_mas_o_tipo_sim() -> None:
    """Sem a verificacao do beneficiario so' o rotulo do plano passa; valor e pagador ficam ausentes."""
    fatos = await _fatos(_view(), valores=False)
    assert fatos.tipo_contratacao == "individual_familiar"
    assert fatos.beneficiario_e_pagador is None
    assert fatos.valor_boleto_pagador is None
    assert fatos.competencias == ()


async def test_revisao_antiga_sem_os_campos_deixa_tudo_ausente() -> None:
    """O 0.1.0/0.2.0 nao traz os campos: a visao os tem como `None` e a fonte nao os completa."""
    view = _view(tipo=None, eh_pagador=None, valor=None)
    fatos = await _fatos(view)
    assert fatos.tipo_contratacao is None
    assert fatos.beneficiario_e_pagador is None
    assert fatos.valor_boleto_pagador is None


async def test_novos_campos_nao_entram_na_entrada_do_lucas_ainda() -> None:
    """O grafo e' de outro PR: `como_entrada_lucas` segue com as mesmas chaves de antes."""
    fatos = await _fatos(_view())
    entrada = fatos.como_entrada_lucas()
    for chave in ("tipo_contratacao", "beneficiario_e_pagador", "valor_boleto_pagador"):
        assert chave not in entrada


async def test_fatos_de_antes_nao_mudam() -> None:
    fatos = await _fatos(_view())
    assert fatos.status_conciliado is False and fatos.ciclos_sem_conciliacao == 2
    assert fatos.valor_em_aberto == "640.50" and fatos.pagador_tipo == "pessoa_fisica"
    assert not isinstance(await _fonte(_view()).fatos("", None), FatosCobranca)
    assert isinstance(await _fonte(_view()).fatos("", None), Indisponivel)
