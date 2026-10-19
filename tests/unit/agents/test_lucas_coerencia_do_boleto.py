"""FASE 4 (18/10/2026): o MESMO mes com o MESMO valor em toda resposta do pagador PF.

O defeito (dev, beneficiaria que consentiu com os testes; plano individual familiar, ela e' a pagadora PF, 1
dependente): "Suas ultimas 3 mensalidades" mostrou R$ 391,02 para outubro de 2026 e a 2a via, R$ 566,72. Nao
era coparticipacao nem franquia: R$ 391,02 e' a PARTE da titular (R$ 354,52 de mensalidade + R$ 36,50 de
coparticipacao); o boleto (R$ 566,72) soma a mensalidade do dependente (R$ 175,70). O dado da AMH estava
coerente; o texto e' que escolhia campos diferentes.

Agora o valor principal de um mes e' o do BOLETO (`valor_boleto_pagador`; na 2a via, `valor_titulo`) e, quando
o detalhamento da competencia FECHA com ele, a composicao curta. Sem detalhamento que feche, so' o boleto.
Empresa, adesao e quem nao e' o pagador: como antes. Tudo contra o grafo REAL e a DMN DRAFT.
"""

from __future__ import annotations

import copy
import re
from datetime import date
from typing import Any

import pytest

from maezo.agents.lucas.fonte_v4 import MARCADOR_CODIGO
from maezo.agents.lucas.graph import (
    TEXTO_LISTA_DO_BOLETO,
    TEXTO_NAO_PAGADOR_V4,
    LucasGraph,
    LucasState,
    composicao_do_mes,
    new_lucas_state,
)
from maezo.agents.lucas.prompts import motivo_de_recusa
from maezo.runtime.cobranca_lista import PedidoDeLista
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport
from tests.evals.lucas.programa import DmnDraftLocal
from tests.support.audit_fakes import FakeStartAuditSink

_HOJE = date(2026, 10, 17)

#: Os VALORES reais da competencia (billing-status v1.10 / 2a via v1.11), sem nenhum dado pessoal. A
#: SITUACAO e o vencimento sao sinteticos (outubro ja' consta pago na AMH): nada depende do boleto real.
_OUTUBRO: dict[str, str] = {
    "competencia": "2026-10",
    "situacao": "em_aberto",
    "vencimento": "2026-10-20",
    "valor_total": "391.02",
    "valor_coparticipacao": "36.50",
    "valor_saldo": "566.72",
    "valor_boleto_pagador": "566.72",
}
_SETEMBRO: dict[str, str] = {
    "competencia": "2026-09",
    "situacao": "paga",
    "vencimento": "2026-09-20",
    "valor_total": "354.52",
    "valor_coparticipacao": "0.00",
    "valor_saldo": "0.00",
    "liquidado_em": "2026-09-15",
    "valor_boleto_pagador": "530.22",
}
_AGOSTO: dict[str, str] = {
    "competencia": "2026-08",
    "situacao": "paga",
    "vencimento": "2026-08-20",
    "valor_total": "389.52",
    "valor_coparticipacao": "35.00",
    "valor_saldo": "0.00",
    "liquidado_em": "2026-08-14",
    "valor_boleto_pagador": "565.22",
}
#: `detalhamento_pagador[0]` de outubro, na forma de `FatosV4.como_entrada_lucas` (so' a relacao, sem nome).
_DETALHE_OUTUBRO: dict[str, Any] = {
    "competencia": "2026-10",
    "total_mensalidade": "566.72",
    "total_pre_estabelecido": "530.22",
    "total_coparticipacao": "36.50",
    "total_adicionais": "0.00",
    "fecha": True,
    "beneficiarios": [
        {
            "rotulo": "você",
            "valor_pre_estabelecido": "354.52",
            "valor_coparticipacao": "36.50",
            "valor_adicionais": "0.00",
            "valor_outros": "0.00",
            "valor_total": "391.02",
        },
        {
            "rotulo": "dependente 1",
            "valor_pre_estabelecido": "175.70",
            "valor_coparticipacao": "0.00",
            "valor_adicionais": "0.00",
            "valor_outros": "0.00",
            "valor_total": "175.70",
        },
    ],
}
_BOLETO_OUTUBRO: dict[str, Any] = {
    "competencia": "2026-10",
    "vencimento": "2026-10-20",
    "situacao": "em_aberto",
    "valor_titulo": "566.72",
    "outros": 0,
}
_COMPOSICAO = (
    "você: R$ 391,02, sendo R$ 354,52 de mensalidade e R$ 36,50 de coparticipação; dependente 1: R$ 175,70"
)
_PAGADOR_PF = {"pagador_tipo": "pessoa_fisica", "beneficiario_e_pagador": True}
_ULTIMAS_3 = PedidoDeLista(3).como_handoff()


def _estado(tipo: str = "consulta_valores", **extra: Any) -> LucasState:
    return new_lucas_state(
        {
            "tenant_id": "amh",
            "conversation_id": "wa:amh:hk1_coerencia",
            "canal": "whatsapp",
            "beneficiario_pseudo_id": "pseudo-coerencia",
            "to_hash": "hash-coerencia",
            "intencao": "cobranca_info",
            "tipo_solicitacao": tipo,
            "status_conciliado": False,
            "ciclos_sem_conciliacao": 0,
            "numero_boleto": "",
            "cnab_ref": "amh-billing:2026-10-16",
            "valor_em_aberto": "566.72",
            "dias_atraso_max": 0,
            "vencimento_referencia": "2026-10-20",
            "competencias_cobranca": [dict(_OUTUBRO), dict(_SETEMBRO), dict(_AGOSTO)],
            # A pagadora PF (o caso do defeito), a menos que o teste diga outro pagador.
            **({} if "pagador_tipo" in extra else _PAGADOR_PF),
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


async def _turno(estado: LucasState, *, v4: bool = True) -> tuple[dict[str, Any], str]:
    envio = _Envio()
    grafo = LucasGraph(
        inference=_Inferencia(),  # type: ignore[arg-type]
        dmn=DmnDraftLocal(),
        cibseven=FakeCibSevenTransport(),
        audit_sink=FakeStartAuditSink(),
        whatsapp=envio,
        hoje=lambda: _HOJE,
        lucas_v4=v4,
    )
    final = dict(await grafo.compile_graph().compile().ainvoke(estado))
    [texto] = envio.textos
    assert final["route"] == "respond_member"
    assert final["mensagem"]["recusa_de_saida"] is False, texto  # a cerca de quantias aceitou
    return final, texto


def _valores_do_mes(texto: str, mes: str) -> list[str]:
    """As quantias da linha/frase que cita `mes` ANTES de qualquer parentese (o valor principal)."""
    for trecho in re.split(r"\n|(?<=\.)\s", texto):
        if mes in trecho:
            principal = trecho.split("(", 1)[0]
            return re.findall(r"R\$ [\d.]+,\d{2}", principal)
    return []


# --- 1. a lista e a 2a via falam do MESMO valor para outubro ---------------------------------------------
async def test_a_lista_das_ultimas_3_mostra_o_valor_do_boleto_de_cada_mes() -> None:
    _, texto = await _turno(_estado(cobranca_lista=_ULTIMAS_3))
    assert texto == "\n".join(
        [
            "Suas últimas 3 mensalidades:",
            "• outubro de 2026: R$ 566,72, em aberto, vence em 20/10.",
            "• setembro de 2026: R$ 530,22, paga em 15/09.",
            "• agosto de 2026: R$ 565,22, paga em 14/08.",
            TEXTO_LISTA_DO_BOLETO,
            "(dados de 16/10)",
        ]
    )
    assert "391,02" not in texto and "354,52" not in texto and "389,52" not in texto


async def test_a_lista_com_o_detalhamento_que_fecha_traz_a_composicao_no_mes_mais_recente() -> None:
    _, texto = await _turno(_estado(cobranca_lista=_ULTIMAS_3, detalhamento_pagador=[_DETALHE_OUTUBRO]))
    linhas = texto.split("\n")
    assert linhas[1] == f"• outubro de 2026: R$ 566,72 ({_COMPOSICAO}), em aberto, vence em 20/10."
    assert linhas[2] == "• setembro de 2026: R$ 530,22, paga em 15/09."


async def test_a_2a_via_e_a_lista_dizem_o_mesmo_valor_de_outubro() -> None:
    _, lista = await _turno(_estado(cobranca_lista=_ULTIMAS_3))
    _, segunda_via = await _turno(
        _estado(
            "2a_via", segunda_via_estado="com_codigo", segunda_via_boletos=[_BOLETO_OUTUBRO], **_PAGADOR_PF
        )
    )
    assert segunda_via.startswith("O boleto da mensalidade de outubro de 2026 (R$ 566,72, vence em 20/10)")
    assert MARCADOR_CODIGO in segunda_via.split("\n")
    assert _valores_do_mes(lista, "outubro de 2026") == ["R$ 566,72"]
    assert "R$ 566,72" in segunda_via and "391,02" not in segunda_via


async def test_a_2a_via_com_o_detalhamento_que_fecha_traz_a_composicao() -> None:
    _, texto = await _turno(
        _estado(
            "2a_via",
            segunda_via_estado="com_codigo",
            segunda_via_boletos=[_BOLETO_OUTUBRO],
            detalhamento_pagador=[_DETALHE_OUTUBRO],
            **_PAGADOR_PF,
        )
    )
    assert texto.split("\n")[0] == (
        f"O boleto da mensalidade de outubro de 2026 é de R$ 566,72 ({_COMPOSICAO}). "
        "Ele vence em 20/10 e está em aberto."
    )
    assert MARCADOR_CODIGO in texto.split("\n")


# --- 2. as outras respostas de um mes: o boleto primeiro ----------------------------------------------------
async def test_valor_do_mes_com_o_detalhamento_mostra_o_boleto_e_o_detalhe() -> None:
    _, texto = await _turno(_estado(competencia="2026-10", detalhamento_pagador=[_DETALHE_OUTUBRO]))
    assert texto.startswith(
        "O valor total da mensalidade de outubro de 2026 é de R$ 566,72, com vencimento em 20/10."
    )
    assert "• Você: pré-estabelecido R$ 354,52 + coparticipação R$ 36,50 = R$ 391,02" in texto
    assert "• Dependente 1: pré-estabelecido R$ 175,70 + coparticipação R$ 0,00 = R$ 175,70" in texto


async def test_valor_do_mes_sem_o_detalhamento_mostra_o_boleto_e_a_parte() -> None:
    _, texto = await _turno(_estado(competencia="2026-10"))
    assert texto == (
        "O valor total da mensalidade de outubro de 2026 é de R$ 566,72, com vencimento em 20/10. "
        "Esse total soma todos os beneficiários do contrato. A sua parte é de R$ 391,02. (dados de 16/10)"
    )


async def test_vencimento_mostra_o_boleto_com_a_composicao() -> None:
    _, texto = await _turno(
        _estado("vencimento", competencia="2026-10", detalhamento_pagador=[_DETALHE_OUTUBRO], **_PAGADOR_PF)
    )
    assert texto == (
        f"A mensalidade de outubro de 2026 vence em 20/10, no valor de R$ 566,72 ({_COMPOSICAO}). "
        "(dados de 16/10)"
    )


async def test_vencimento_sem_o_detalhamento_mostra_so_o_boleto() -> None:
    _, texto = await _turno(_estado("vencimento", competencia="2026-10", **_PAGADOR_PF))
    assert texto == "A mensalidade de outubro de 2026 vence em 20/10, no valor de R$ 566,72. (dados de 16/10)"


async def test_o_boleto_que_tenho_mostra_o_boleto_com_a_composicao() -> None:
    _, texto = await _turno(
        _estado(
            "2a_via",
            competencia="2026-10",
            assunto_anterior="boleto_2via",
            detalhamento_pagador=[_DETALHE_OUTUBRO],
            variante_de_redacao=0,
            **_PAGADOR_PF,
        ),
        v4=True,
    )
    assert f"A mensalidade de outubro de 2026 vence em 20/10, no valor de R$ 566,72 ({_COMPOSICAO})." in texto


# --- 3. a composicao SO' quando fecha: nunca se inventa ---------------------------------------------------
def _com_detalhe(grupo: dict[str, Any]) -> LucasState:
    return _estado(detalhamento_pagador=[grupo], **_PAGADOR_PF)


def test_a_composicao_dos_numeros_reais() -> None:
    assert composicao_do_mes(_com_detalhe(_DETALHE_OUTUBRO), _OUTUBRO) == _COMPOSICAO


def _mudado(**mudanca: Any) -> dict[str, Any]:
    grupo = copy.deepcopy(_DETALHE_OUTUBRO)
    grupo.update(mudanca)
    return grupo


def _beneficiario_mudado(indice: int, **mudanca: Any) -> dict[str, Any]:
    grupo = copy.deepcopy(_DETALHE_OUTUBRO)
    grupo["beneficiarios"][indice].update(mudanca)
    return grupo


@pytest.mark.parametrize(
    "grupo",
    [
        _mudado(fecha=False),
        _mudado(fecha="true"),
        _mudado(total_mensalidade="566.73"),  # o total do detalhamento nao e' o do boleto
        _beneficiario_mudado(1, valor_total="175.71"),  # a soma nao confere com o boleto
        _beneficiario_mudado(0, rotulo="Maria"),  # nome nunca
        _beneficiario_mudado(0, valor_total=None),
        _mudado(beneficiarios=[]),
        _mudado(competencia="2026-09"),
    ],
    ids=[
        "nao_fecha",
        "fecha_texto",
        "total_diferente",
        "soma_diferente",
        "nome",
        "sem_total",
        "vazio",
        "outro_mes",
    ],
)
def test_sem_detalhamento_que_feche_nao_ha_composicao(grupo: dict[str, Any]) -> None:
    assert composicao_do_mes(_com_detalhe(grupo), _OUTUBRO) is None


def test_partes_que_nao_somam_o_total_do_beneficiario_ficam_so_no_total() -> None:
    grupo = _beneficiario_mudado(0, valor_pre_estabelecido="350.00")
    assert composicao_do_mes(_com_detalhe(grupo), _OUTUBRO) == ("você: R$ 391,02; dependente 1: R$ 175,70")


@pytest.mark.parametrize(
    "extra",
    [
        {"pagador_tipo": "pessoa_juridica", "beneficiario_e_pagador": True},
        {"pagador_tipo": "pessoa_fisica", "beneficiario_e_pagador": False},
        {"pagador_tipo": "pessoa_fisica", "beneficiario_e_pagador": True, "cobranca_administradora": True},
        {"pagador_tipo": "pessoa_fisica"},
    ],
)
def test_so_o_pagador_pf_tem_composicao(extra: dict[str, Any]) -> None:
    estado = _estado(detalhamento_pagador=[_DETALHE_OUTUBRO], **extra)
    assert composicao_do_mes(estado, _OUTUBRO) is None


async def test_lista_com_detalhamento_que_nao_fecha_mostra_so_o_boleto() -> None:
    _, texto = await _turno(_estado(cobranca_lista=_ULTIMAS_3, detalhamento_pagador=[_mudado(fecha=False)]))
    assert "• outubro de 2026: R$ 566,72, em aberto, vence em 20/10." in texto
    assert "391,02" not in texto


async def test_com_a_chave_v4_desligada_a_composicao_nunca_aparece() -> None:
    _, desligada = await _turno(
        _estado(cobranca_lista=_ULTIMAS_3, detalhamento_pagador=[_DETALHE_OUTUBRO]), v4=False
    )
    _, sem_detalhe = await _turno(_estado(cobranca_lista=_ULTIMAS_3), v4=False)
    assert desligada == sem_detalhe and "você:" not in desligada


# --- 4. a cerca de quantias continua valendo ------------------------------------------------------------
def test_a_cerca_recusa_uma_composicao_que_nao_veio_dos_fatos() -> None:
    fatos = {"competencias": [_OUTUBRO], "dados_de": "2026-10-16"}
    inventada = "• outubro de 2026: R$ 566,72 (você: R$ 391,02; dependente 1: R$ 175,71)."
    assert motivo_de_recusa(inventada, "mensagem", fatos) is not None


# --- 5. quem nao e' o pagador PF: nada muda ----------------------------------------------------------------
async def test_a_lista_de_quem_nao_e_o_pagador_continua_com_o_valor_de_sempre() -> None:
    _, texto = await _turno(
        _estado(cobranca_lista=_ULTIMAS_3, pagador_tipo="pessoa_fisica", beneficiario_e_pagador=False),
        v4=False,
    )
    assert "• outubro de 2026: R$ 391,02, em aberto, vence em 20/10." in texto
    assert "566,72" not in texto and TEXTO_LISTA_DO_BOLETO not in texto


async def test_o_nao_pagador_v4_nao_ve_valor_nenhum() -> None:
    _, texto = await _turno(
        _estado(
            cobranca_lista=_ULTIMAS_3,
            detalhamento_pagador=[_DETALHE_OUTUBRO],
            pagador_tipo="pessoa_fisica",
            beneficiario_e_pagador=False,
        )
    )
    assert texto == TEXTO_NAO_PAGADOR_V4


async def test_a_empresa_continua_sem_valor() -> None:
    _, texto = await _turno(
        _estado(
            cobranca_lista=_ULTIMAS_3, detalhamento_pagador=[_DETALHE_OUTUBRO], pagador_tipo="pessoa_juridica"
        )
    )
    assert "R$" not in texto and "RH" in texto


def test_mes_vencido_responde_o_valor_do_boleto_primeiro_e_o_atraso_depois() -> None:
    """Roteiro lucas_v4_pagador (18/10/2026): "Quanto esta' minha mensalidade?" com o mes VENCIDO respondia
    so' a situacao ("... ainda esta' sem pagamento confirmado"). A pergunta e' de valor: o boleto primeiro, a
    parte da pessoa, e o atraso DEPOIS."""
    from maezo.agents.lucas.graph import _frase_de_atraso_do_pagador, _frase_de_valor_do_pagador_v4

    vencido = {**_OUTUBRO, "situacao": "vencida", "vencimento": "2026-10-15"}
    hoje = date(2026, 10, 18)
    valor = _frase_de_valor_do_pagador_v4(vencido, hoje)
    assert valor is not None and valor.startswith("O valor total da mensalidade de outubro")
    assert "R$ 566,72" in valor and "A sua parte é de R$ 391,02" in valor
    assert (
        _frase_de_atraso_do_pagador(vencido, hoje)
        == "Ela venceu em 15/10 e ainda está sem pagamento confirmado."
    )
    assert _frase_de_atraso_do_pagador(_OUTUBRO, date(2026, 10, 17)) is None


def test_segunda_via_sem_boleto_em_aberto_diz_que_ja_foi_paga() -> None:
    """Rodada do dono (18/10/2026): outubro pago e a 2a via sem titulo — "nao ha' boleto em aberto", nunca
    "nao encontrei o codigo de barras" (falha ao obter o codigo e' outra coisa)."""
    from maezo.agents.lucas.graph import _sem_boleto_em_aberto

    pago = {**_OUTUBRO, "situacao": "paga", "liquidado_em": "2026-10-09"}
    estado: Any = {"competencias_cobranca": [pago, _SETEMBRO]}
    achado = _sem_boleto_em_aberto(estado, date(2026, 10, 18))
    assert achado is not None
    assert achado[0] == "Não há boleto em aberto: a mensalidade de outubro de 2026 já foi paga em 09/10."
    com_aberto: Any = {"competencias_cobranca": [_OUTUBRO, _SETEMBRO]}
    assert _sem_boleto_em_aberto(com_aberto, date(2026, 10, 18)) is None
