"""Wave 2 do Lucas (14/10/2026): adesao pelo `tipo_contratacao` e o boleto da familia.

- ADESAO: `tipo_contratacao == coletivo_adesao` (billing-status 0.3.0) liga o texto da administradora sem
  consultar a TINA; a TINA fica como reserva SO' quando o tipo e' `desconhecido`/ausente;
- BOLETO DA FAMILIA: pagador PF que e' a propria pessoa ouve a parte dela E o total do boleto; quem nao
  e' o pagador (dependente) ouve de quem e' o boleto, sem valor; PJ, adesao e `desconhecido` nunca veem
  outro total. Tudo texto fixo dos fatos, pela cerca de saida.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

import pytest

from maezo.agents.lucas.fonte_cobranca import FatosCobranca
from maezo.agents.lucas.fonte_cobranca_amh import FonteCobrancaAmh
from maezo.agents.lucas.graph import (
    TEXTO_BOLETO_DO_TITULAR,
    LucasGraph,
    LucasState,
    _competencias_dos_fatos,
    dependente_do_titular,
    mostra_boleto_da_familia,
    new_lucas_state,
)
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport
from tests.evals.lucas.programa import DmnDraftLocal
from tests.support.audit_fakes import FakeStartAuditSink
from tests.unit.agents.test_fonte_cobranca_contratacao import (
    _Billing,
    _Consentimento,
    _Resolvedor,
    _view,
)
from tests.unit.agents.test_lucas_adesao import _elegibilidade, _Tina, _vinculo


def _fonte(view: Any, tina: Any = None) -> FonteCobrancaAmh:
    return FonteCobrancaAmh(
        billing=_Billing(view),
        resolvedor=_Resolvedor(verificado=True),
        consentimento=_Consentimento(),
        purpose_of_use="atendimento_whatsapp",
        fatos_de_valor=True,
        tina=tina,
        tina_purpose_of_use="sharing_amh_internal" if tina is not None else None,
    )


async def _fatos(view: Any, tina: Any = None) -> FatosCobranca:
    fatos = await _fonte(view, tina).fatos("p", None)
    assert isinstance(fatos, FatosCobranca), fatos
    return fatos


# --- 1. ADESAO pelo tipo de contratacao --------------------------------------------------------------
@pytest.mark.parametrize("pagador", ["desconhecido", "pessoa_fisica"])
async def test_coletivo_adesao_liga_a_administradora_sem_consultar_a_tina(pagador: str) -> None:
    tina = _Tina(_elegibilidade(_vinculo("Coletivo")))
    fatos = await _fatos(_view(pagador=pagador, tipo="coletivo_adesao", eh_pagador=True), tina)
    assert fatos.cobranca_administradora is True
    assert fatos.numero_boleto == "" and fatos.beneficiario_e_pagador is None
    assert all(c.valor_boleto_pagador is None for c in fatos.competencias)
    assert tina.chamadas == []
    assert fatos.como_entrada_lucas()["cobranca_administradora"] is True


@pytest.mark.parametrize("tipo", ["coletivo_empresarial", "individual_familiar"])
async def test_outro_tipo_conhecido_nao_e_adesao_mesmo_que_a_tina_diga(tipo: str) -> None:
    tina = _Tina(_elegibilidade(_vinculo("Coletivo por adesão")))
    fatos = await _fatos(_view(pagador="desconhecido", tipo=tipo, eh_pagador=None), tina)
    assert fatos.cobranca_administradora is False
    assert tina.chamadas == []


@pytest.mark.parametrize("tipo", [None, "desconhecido"])
async def test_tipo_desconhecido_cai_na_tina_como_reserva(tipo: str | None) -> None:
    tina = _Tina(_elegibilidade(_vinculo("Coletivo por adesão")))
    fatos = await _fatos(_view(pagador="desconhecido", tipo=tipo, eh_pagador=None), tina)
    assert fatos.cobranca_administradora is True and len(tina.chamadas) == 1
    sem_vinculo = _Tina(_elegibilidade(_vinculo("Coletivo")))
    fatos = await _fatos(_view(pagador="desconhecido", tipo=tipo, eh_pagador=None), sem_vinculo)
    assert fatos.cobranca_administradora is False


# --- 2. BOLETO DA FAMILIA: o grafo --------------------------------------------------------------------
def _competencias(situacao: str = "em_aberto", total: str | None = "1280.40") -> list[dict[str, Any]]:
    atual: dict[str, Any] = {
        "competencia": "2026-10",
        "situacao": situacao,
        "vencimento": "2026-10-20",
        "valor_total": "320.25",
        "valor_coparticipacao": "0.00",
        "valor_saldo": "320.25" if situacao == "em_aberto" else "0.00",
    }
    if situacao == "paga":
        atual["liquidado_em"] = "2026-10-08"
    if total is not None:
        atual["valor_boleto_pagador"] = total
    return [atual]


def _estado(tipo: str = "consulta_valores", *, competencias: Any = None, **extra: Any) -> LucasState:
    return new_lucas_state(
        {
            "tenant_id": "amh",
            "conversation_id": "wa:amh:hk1_familia",
            "canal": "whatsapp",
            "beneficiario_pseudo_id": "pseudo-familia",
            "to_hash": "hash-familia",
            "intencao": "cobranca_info",
            "tipo_solicitacao": tipo,
            "status_conciliado": False,
            "ciclos_sem_conciliacao": 0,
            "numero_boleto": "",
            "cnab_ref": "amh-billing:2026-10-12",
            "valor_em_aberto": "320.25",
            "dias_atraso_max": 0,
            "vencimento_referencia": "2026-10-20",
            "competencias_cobranca": competencias if competencias is not None else _competencias(),
            **extra,
        }
    )


class _Inferencia:
    async def generate(self, prompt: str, **kw: Any) -> str:
        del prompt, kw
        return "Narrativa para o humano."


class _Envio:
    def __init__(self) -> None:
        self.textos: list[str] = []

    async def send(self, to_hash: str, text: str, *, idempotency_key: str) -> dict[str, Any]:
        del to_hash, idempotency_key
        self.textos.append(text)
        return {"ok": True}


async def _turno(estado: LucasState) -> tuple[dict[str, Any], list[str]]:
    envio = _Envio()
    grafo = LucasGraph(
        inference=_Inferencia(),  # type: ignore[arg-type]
        dmn=DmnDraftLocal(),
        cibseven=FakeCibSevenTransport(),
        audit_sink=FakeStartAuditSink(),
        whatsapp=envio,
        hoje=lambda: date(2026, 10, 13),
    )
    final = dict(await grafo.compile_graph().compile().ainvoke(estado))
    return final, envio.textos


_PAGADOR_PF = {"pagador_tipo": "pessoa_fisica", "beneficiario_e_pagador": True}


async def test_pagador_pf_ouve_o_total_do_boleto_primeiro_e_depois_a_parte() -> None:
    """FASE 4 (18/10/2026): o valor principal e' o do BOLETO (o mesmo da lista e da 2a via), tambem com a
    chave v4 desligada; a parte da pessoa vem depois."""
    final, textos = await _turno(_estado(**_PAGADOR_PF))
    assert final["route"] == "respond_member" and final["process_started"] is False
    assert textos[0] == (
        "O valor total da mensalidade de outubro de 2026 é de R$ 1.280,40, com vencimento em 20/10. "
        "Esse total soma todos os beneficiários do contrato. A sua parte é de R$ 320,25. (dados de 12/10)"
    )


async def test_pagador_pf_com_o_mes_pago_diz_no_passado() -> None:
    _, textos = await _turno(_estado(competencias=_competencias("paga"), **_PAGADOR_PF))
    assert textos[0] == (
        "O valor total da mensalidade de outubro de 2026 foi de R$ 1.280,40 e está paga "
        "(você pagou em 08/10). "
        "Esse total soma todos os beneficiários do contrato. A sua parte foi de R$ 320,25. (dados de 12/10)"
    )


async def test_sem_o_total_na_fonte_a_resposta_e_a_de_sempre() -> None:
    _, textos = await _turno(_estado(competencias=_competencias(total=None), **_PAGADOR_PF))
    assert "valor total" not in textos[0] and "R$ 320,25" in textos[0]


@pytest.mark.parametrize(
    "extra",
    [
        {"pagador_tipo": "pessoa_juridica", "beneficiario_e_pagador": True},
        {"pagador_tipo": "pessoa_fisica", "beneficiario_e_pagador": True, "cobranca_administradora": True},
        {"pagador_tipo": "pessoa_fisica"},
        {"beneficiario_e_pagador": True},
        {"pagador_tipo": "pessoa_fisica", "beneficiario_e_pagador": False},
    ],
)
@pytest.mark.parametrize("tipo", ["consulta_valores", "vencimento", "boleto", "2a_via", "status_pagamento"])
async def test_nenhum_outro_caso_mostra_o_total_do_boleto(tipo: str, extra: dict[str, Any]) -> None:
    _, textos = await _turno(_estado(tipo, **extra))
    assert all("1.280,40" not in t and "1280" not in t for t in textos)
    assert all("soma todos os beneficiários" not in t for t in textos)


@pytest.mark.parametrize("tipo", ["vencimento", "boleto", "2a_via"])
async def test_dependente_ouve_de_quem_e_o_boleto_sem_valor(tipo: str) -> None:
    final, textos = await _turno(_estado(tipo, pagador_tipo="pessoa_fisica", beneficiario_e_pagador=False))
    assert final["route"] == "respond_member"
    assert textos == [TEXTO_BOLETO_DO_TITULAR]


async def test_dependente_perguntando_valor_ouve_a_propria_parte_e_o_titular() -> None:
    _, textos = await _turno(_estado(pagador_tipo="pessoa_fisica", beneficiario_e_pagador=False))
    assert "R$ 320,25" in textos[0] and TEXTO_BOLETO_DO_TITULAR in textos[0]
    assert "1.280,40" not in textos[0]


async def test_adesao_segue_com_o_texto_da_administradora_e_nunca_o_total() -> None:
    _, textos = await _turno(
        _estado(
            "2a_via", pagador_tipo="pessoa_fisica", beneficiario_e_pagador=True, cobranca_administradora=True
        )
    )
    assert "administradora" in textos[0] and "1.280,40" not in textos[0]


def test_predicados_do_boleto_da_familia() -> None:
    assert mostra_boleto_da_familia(_estado(**_PAGADOR_PF)) is True
    assert dependente_do_titular(_estado(**_PAGADOR_PF)) is False
    dep = _estado(pagador_tipo="pessoa_fisica", beneficiario_e_pagador=False)
    assert dependente_do_titular(dep) is True and mostra_boleto_da_familia(dep) is False
    assert not mostra_boleto_da_familia(_estado(pagador_tipo="pessoa_juridica", beneficiario_e_pagador=True))


def test_o_total_nao_atravessa_o_estado_fora_do_pagador_pf() -> None:
    assert _competencias_dos_fatos(_estado(**_PAGADOR_PF))[0]["valor_boleto_pagador"] == "1280.40"
    assert "valor_boleto_pagador" not in _competencias_dos_fatos(_estado(pagador_tipo="pessoa_juridica"))[0]
    assert "valor_boleto_pagador" not in _competencias_dos_fatos(_estado())[0]


@pytest.mark.parametrize("situacao", ["em_aberto", "paga"])
def test_frases_do_boleto_da_familia_tem_no_maximo_vinte_palavras_e_o_mes_com_ano(situacao: str) -> None:
    from maezo.agents.lucas.graph import _frase_de_valor_do_pagador_v4

    competencia = _competencias(situacao)[0]
    texto = str(_frase_de_valor_do_pagador_v4(competencia, date(2026, 10, 13)))
    assert "outubro de 2026" in texto
    for frase in re.split(r"(?<=[.!?])\s+", texto):
        assert len(frase.split()) <= 20, frase


def test_o_texto_do_titular_tem_frases_curtas() -> None:
    for frase in re.split(r"(?<=[.!?])\s+", TEXTO_BOLETO_DO_TITULAR):
        assert len(frase.split()) <= 20, frase
