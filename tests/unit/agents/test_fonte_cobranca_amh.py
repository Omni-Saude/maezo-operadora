"""`FonteCobrancaAmh`: a fonte real nunca inventa fato. Cada falta vira `Indisponivel` com motivo fechado."""

from __future__ import annotations

from typing import Any

import pytest

from maezo.agents.lucas.fonte_cobranca import (
    CompetenciaCobranca,
    FatosCobranca,
    FonteCobranca,
    FonteCobrancaSimulada,
    Indisponivel,
)
from maezo.agents.lucas.fonte_cobranca_amh import FonteCobrancaAmh
from maezo.agents.lucas.graph import _CALLER_INPUT_FIELDS
from maezo.ports.billing_status import BillingStatusView, BillingSummary, CompetenciaBilling
from maezo.ports.errors import PortFailureReason, PortResult

REF = "amh:psr:v1:1b2f3a4c-5d6e-4f70-8a9b-0c1d2e3f4a5b"


def _competencia(competencia: str, situacao: str, boleto: str | None) -> CompetenciaBilling:
    return CompetenciaBilling(
        competencia=competencia,
        parcela=1,
        vencimento="2026-08-10",
        situacao=situacao,
        valor_total="100.00",
        valor_coparticipacao="0.00",
        valor_saldo="100.00",
        liquidado_em=None,
        boleto_numero_mascarado=boleto,
        boleto_disponivel_online=True,
    )


def _view(
    *, conciliado: bool | None = False, ciclos: int | None = 2, competencias: Any = None
) -> BillingStatusView:
    return BillingStatusView(
        portable_subject_ref=REF,
        as_of="2026-10-05T12:00:00Z",
        fonte_atualizada_em="2026-08-05T17:17:30Z",
        resumo=BillingSummary(
            status_conciliado=conciliado,
            ciclos_sem_conciliacao=ciclos,
            valor_em_aberto="200.00",
            dias_atraso_max=61,
            pagador_tipo="pessoa_fisica",
            criterio_conciliacao="situacao_paga_ou_liquidada",
        ),
        competencias=tuple(
            competencias
            if competencias is not None
            else (
                _competencia("2026-08", "vencida", "****0002"),
                _competencia("2026-07", "vencida", "****0001"),
                _competencia("2026-06", "paga", "****0000"),
            )
        ),
        campos_ausentes=frozenset(),
    )


class _Billing:
    def __init__(self, resultado: PortResult[BillingStatusView]) -> None:
        self.resultado = resultado
        self.chamadas: list[dict[str, Any]] = []

    async def get_billing_status(self, ref: str, **kw: Any) -> PortResult[BillingStatusView]:
        self.chamadas.append({"ref": ref, **kw})
        return self.resultado


class _Resolvedor:
    def __init__(self, ref: str | None = REF, erro: BaseException | None = None) -> None:
        self.ref, self.erro = ref, erro

    async def portable_ref(self, pseudo_id: str, *, phone_hash: str | None) -> str | None:
        if self.erro:
            raise self.erro
        return self.ref


class _Consentimento:
    def __init__(self, decisao: str | None = "consent-1") -> None:
        self.valor = decisao

    async def decisao(self, portable_ref: str, purpose_of_use: str) -> str | None:
        return self.valor


def _fonte(billing: Any, resolvedor: Any = None, consentimento: Any = None) -> FonteCobrancaAmh:
    return FonteCobrancaAmh(
        billing=billing,
        resolvedor=resolvedor or _Resolvedor(),
        consentimento=consentimento or _Consentimento(),
        purpose_of_use="purpose1",
    )


async def test_caminho_feliz_traduz_para_os_fatos_da_dmn() -> None:
    billing = _Billing(PortResult.ok(_view()))
    fonte = _fonte(billing)
    assert isinstance(fonte, FonteCobranca)

    fatos = await fonte.fatos("pseudo-1", None)

    assert isinstance(fatos, FatosCobranca)
    # Os quatro fatos de sempre, com a semantica de sempre (DMN e textos fixos).
    assert (fatos.status_conciliado, fatos.ciclos_sem_conciliacao, fatos.numero_boleto, fatos.cnab_ref) == (
        False,
        2,
        "****0002",  # a competencia pendente mais recente
        "amh-billing:2026-08-05",
    )
    chamada = billing.chamadas[0]
    assert chamada["ref"] == REF
    assert chamada["purpose_of_use"] == "purpose1"
    assert chamada["consent_decision_ref"] == "consent-1"
    assert chamada["competencia"] is None


async def test_com_competencia_o_boleto_e_o_dessa_competencia() -> None:
    fonte = _fonte(_Billing(PortResult.ok(_view())))
    fatos = await fonte.fatos("pseudo-1", "2026-06")
    assert isinstance(fatos, FatosCobranca)
    assert fatos.numero_boleto == "****0000"


async def test_competencia_pedida_que_nao_veio_nao_inventa_boleto() -> None:
    fonte = _fonte(_Billing(PortResult.ok(_view())))
    fatos = await fonte.fatos("pseudo-1", "2025-01")
    assert isinstance(fatos, FatosCobranca)
    assert fatos.numero_boleto == ""


async def test_pagamento_conciliado_sem_atraso() -> None:
    pago = _view(conciliado=True, ciclos=0, competencias=[_competencia("2026-08", "paga", "****9999")])
    fonte = _fonte(_Billing(PortResult.ok(pago)))
    fatos = await fonte.fatos("pseudo-1", None)
    assert isinstance(fatos, FatosCobranca)
    assert (fatos.status_conciliado, fatos.ciclos_sem_conciliacao) == (True, 0)


@pytest.mark.parametrize(
    "pseudo,competencia,motivo",
    [
        ("", None, "pseudo_id_ausente"),
        ("p", "2026-13", "competencia_invalida"),
        ("p", "202609", "competencia_invalida"),
    ],
)
async def test_entrada_invalida_nao_chega_a_fonte(pseudo: str, competencia: str | None, motivo: str) -> None:
    billing = _Billing(PortResult.ok(_view()))
    assert await _fonte(billing).fatos(pseudo, competencia) == Indisponivel(motivo)  # type: ignore[arg-type]
    assert billing.chamadas == []


async def test_pessoa_nao_resolvida_nao_le_a_cobranca() -> None:
    billing = _Billing(PortResult.ok(_view()))
    assert await _fonte(billing, _Resolvedor(None)).fatos("p", None) == Indisponivel("sujeito_nao_resolvido")
    assert billing.chamadas == []


async def test_sem_consentimento_nao_le_a_cobranca() -> None:
    billing = _Billing(PortResult.ok(_view()))
    assert await _fonte(billing, consentimento=_Consentimento(None)).fatos("p", None) == Indisponivel(
        "sem_consentimento"
    )
    assert billing.chamadas == []


@pytest.mark.parametrize("razao", list(PortFailureReason))
async def test_toda_recusa_do_port_vira_fonte_indisponivel(razao: PortFailureReason) -> None:
    fonte = _fonte(_Billing(PortResult.refused(razao)))
    assert await fonte.fatos("p", None) == Indisponivel("fonte_indisponivel")


@pytest.mark.parametrize("conciliado,ciclos", [(None, 2), (False, None), (None, None)])
async def test_fato_ausente_nao_e_completado(conciliado: bool | None, ciclos: int | None) -> None:
    fonte = _fonte(_Billing(PortResult.ok(_view(conciliado=conciliado, ciclos=ciclos))))
    assert await fonte.fatos("p", None) == Indisponivel("fato_ausente")


async def test_falha_externa_do_resolvedor_vira_indisponivel() -> None:
    fonte = _fonte(_Billing(PortResult.ok(_view())), _Resolvedor(erro=TimeoutError()))
    assert await fonte.fatos("p", None) == Indisponivel("fonte_indisponivel")


async def test_erro_de_programacao_nao_e_engolido() -> None:
    fonte = _fonte(_Billing(PortResult.ok(_view())), _Resolvedor(erro=TypeError("bug")))
    with pytest.raises(TypeError):
        await fonte.fatos("p", None)


# --- DL-0086: os FATOS DE VALOR do contrato chegam ao Lucas, so' com o que a AMH entregou ---------


async def test_fatos_de_valor_do_resumo_e_de_cada_competencia() -> None:
    fatos = await _fonte(_Billing(PortResult.ok(_view()))).fatos("p", None)
    assert isinstance(fatos, FatosCobranca)
    assert fatos.valor_em_aberto == "200.00"
    assert fatos.dias_atraso_max == 61
    assert fatos.vencimento_referencia == "2026-08-10"
    assert [c.competencia for c in fatos.competencias] == ["2026-08", "2026-07", "2026-06"]
    assert fatos.competencias[0] == CompetenciaCobranca(
        competencia="2026-08",
        situacao="vencida",
        vencimento="2026-08-10",
        valor_total="100.00",
        valor_coparticipacao="0.00",
        valor_saldo="100.00",
        liquidado_em=None,
        boleto="****0002",
    )


async def test_como_entrada_lucas_leva_os_fatos_de_valor_sem_chave_vazia() -> None:
    fatos = await _fonte(_Billing(PortResult.ok(_view()))).fatos("p", None)
    assert isinstance(fatos, FatosCobranca)
    entrada = fatos.como_entrada_lucas()
    assert entrada["valor_em_aberto"] == "200.00"
    assert entrada["dias_atraso_max"] == 61
    assert entrada["vencimento_referencia"] == "2026-08-10"
    primeira = entrada["competencias_cobranca"][0]
    assert primeira == {
        "competencia": "2026-08",
        "situacao": "vencida",
        "vencimento": "2026-08-10",
        "valor_total": "100.00",
        "valor_coparticipacao": "0.00",
        "valor_saldo": "100.00",
        "boleto": "****0002",
    }
    assert "liquidado_em" not in primeira, "ausente e' ausente: nenhuma chave com None"
    assert set(entrada) <= _CALLER_INPUT_FIELDS


async def test_fato_de_valor_ausente_fica_ausente_e_os_quatro_de_sempre_seguem() -> None:
    sem_valores = BillingStatusView(
        portable_subject_ref=REF,
        as_of="2026-10-05T12:00:00Z",
        fonte_atualizada_em="2026-08-05T17:17:30Z",
        resumo=BillingSummary(
            status_conciliado=True,
            ciclos_sem_conciliacao=0,
            valor_em_aberto=None,
            dias_atraso_max=None,
            pagador_tipo="desconhecido",
            criterio_conciliacao="situacao_paga_ou_liquidada",
        ),
        competencias=(),
        campos_ausentes=frozenset({"valor_em_aberto", "dias_atraso_max"}),
    )
    fatos = await _fonte(_Billing(PortResult.ok(sem_valores))).fatos("p", None)
    assert isinstance(fatos, FatosCobranca)
    assert fatos.como_entrada_lucas() == {
        "status_conciliado": True,
        "ciclos_sem_conciliacao": 0,
        "numero_boleto": "",
        "cnab_ref": "amh-billing:2026-08-05",
    }


async def test_valor_fora_da_forma_do_contrato_vira_ausente_nunca_corrigido() -> None:
    torta = CompetenciaBilling(
        competencia="2026-09",
        parcela=None,
        vencimento="10/09/2026",  # fora de AAAA-MM-DD
        situacao="em_aberto",
        valor_total="R$ 320,25",  # fora de 0.00
        valor_coparticipacao="12.5",  # duas casas, sempre
        valor_saldo="320.25",
        liquidado_em=None,
        boleto_numero_mascarado="23790123456",  # numero cru: nunca vai adiante
        boleto_disponivel_online=None,
    )
    estranha = CompetenciaBilling(
        competencia="2026-13",
        parcela=None,
        vencimento=None,
        situacao="paga",
        valor_total="1.00",
        valor_coparticipacao=None,
        valor_saldo=None,
        liquidado_em=None,
        boleto_numero_mascarado=None,
        boleto_disponivel_online=None,
    )
    fonte = _fonte(_Billing(PortResult.ok(_view(competencias=[torta, estranha]))))
    fatos = await fonte.fatos("p", None)
    assert isinstance(fatos, FatosCobranca)
    assert fatos.competencias == (
        CompetenciaCobranca(competencia="2026-09", situacao="em_aberto", valor_saldo="320.25"),
    )
    assert fatos.vencimento_referencia is None


async def test_a_fonte_simulada_continua_sem_fatos_de_valor() -> None:
    respostas = [await FonteCobrancaSimulada().fatos(f"pseudo-sim-{i}", "2026-09") for i in range(10)]
    com_fatos = [f for f in respostas if isinstance(f, FatosCobranca)]
    assert com_fatos
    for fatos in com_fatos:
        assert set(fatos.como_entrada_lucas()) == {
            "status_conciliado",
            "ciclos_sem_conciliacao",
            "numero_boleto",
            "cnab_ref",
        }
        assert fatos.competencias == () and fatos.valor_em_aberto is None


async def test_o_numero_do_boleto_nunca_e_mais_que_o_rotulo_mascarado() -> None:
    fonte = _fonte(_Billing(PortResult.ok(_view())))
    fatos = await fonte.fatos("p", None)
    assert isinstance(fatos, FatosCobranca)
    assert fatos.numero_boleto.startswith("****") and len(fatos.numero_boleto) == 8
