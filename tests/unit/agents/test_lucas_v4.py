"""Lucas do catalogo v4 (DL-0114, chave `MAEZO_LUCAS_V4`): o grafo.

O que este arquivo prova, contra o grafo REAL e a DMN DRAFT lida do XML:
  - DESLIGADA e' a de antes, byte a byte (mesmo com os fatos novos plantados no estado);
  - o PF que NAO e' o pagador ouve que o responsavel financeiro do contrato e' o titular (sem nome), sem
    valor, em toda pergunta que so' o pagador faz, e o atraso do titulo dele nunca abre caso;
  - o PAGADOR ouve o valor TOTAL primeiro e, com o billing-status 0.4.0, o detalhe por beneficiario SEM nome;
    o detalhamento que nao fecha vira so' o total;
  - a 2a via do pagador leva o MARCADOR do codigo de barras (o codigo nunca esta' no estado), sai sempre
    inteira, e sem codigo diz que nao encontrou; empresa -> RH segue igual;
  - os textos novos respeitam a regua de tom (frases de ate' 20 palavras) e passam na cerca de saida.
"""

from __future__ import annotations

import json
import re
from datetime import date
from typing import Any

import pytest

from maezo.agents.lucas.fonte_v4 import MARCADOR_CODIGO
from maezo.agents.lucas.graph import (
    SEGUNDA_VIA_CANAIS,
    TEXTO_DETALHE_NO_APP_V4,
    TEXTO_NAO_PAGADOR_V4,
    TEXTO_NAO_PAGADOR_V4_CURTO,
    TEXTO_OUTROS_BOLETOS_V4,
    TEXTO_PAGADOR_NAO_CONFIRMADO_V4,
    TEXTO_PAGADOR_NAO_CONFIRMADO_V4_CURTO,
    TEXTO_PDF_V4,
    TEXTO_SEM_CODIGO_V4,
    TEXTO_TOTAL_SOMA_V4,
    LucasGraph,
    LucasState,
    _boleto_da_segunda_via_v4,
    build,
    ref_de_resposta,
    texto_segunda_via_com_codigo_v4,
)
from maezo.agents.lucas.prompts import motivo_de_recusa
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport
from tests.evals.lucas.programa import DmnDraftLocal
from tests.support.audit_fakes import FakeStartAuditSink
from tests.unit.agents.test_lucas_adesao_e_boleto_da_familia import (
    _competencias,
    _Envio,
    _estado,
    _Inferencia,
)

_HOJE = date(2026, 10, 13)
_PAGADOR_PF = {"pagador_tipo": "pessoa_fisica", "beneficiario_e_pagador": True}
_NAO_PAGADOR = {"pagador_tipo": "pessoa_fisica", "beneficiario_e_pagador": False}
_EMPRESA = {"pagador_tipo": "pessoa_juridica"}
_TIPOS = ["consulta_valores", "vencimento", "boleto", "2a_via", "status_pagamento"]

_BOLETO = {
    "competencia": "2026-10",
    "vencimento": "2026-10-20",
    "situacao": "em_aberto",
    "valor_titulo": "1280.40",
    "outros": 0,
}
_DETALHE = {
    "competencia": "2026-10",
    "total_mensalidade": "720.00",
    "fecha": True,
    "beneficiarios": [
        {
            "rotulo": "você",
            "valor_pre_estabelecido": "400.00",
            "valor_coparticipacao": "20.00",
            "valor_adicionais": "0.00",
            "valor_outros": "0.00",
            "valor_total": "420.00",
        },
        {
            "rotulo": "dependente 1",
            "valor_pre_estabelecido": "300.00",
            "valor_coparticipacao": "0.00",
            "valor_adicionais": "0.00",
            "valor_outros": "0.00",
            "valor_total": "300.00",
        },
    ],
}
#: Os fatos novos que, DESLIGADA a chave, nao podem mudar nada.
_PLANTADOS: dict[str, Any] = {
    "responsavel_financeiro": "voce",
    "detalhamento_pagador": [_DETALHE],
    "segunda_via_estado": "com_codigo",
    "segunda_via_boletos": [_BOLETO],
}


async def _turno(estado: LucasState, v4: bool | None = True) -> tuple[dict[str, Any], list[str]]:
    """`v4=None` = o grafo construido SEM o argumento novo (o codigo de antes)."""
    envio = _Envio()
    extra: dict[str, Any] = {} if v4 is None else {"lucas_v4": v4}
    grafo = LucasGraph(
        inference=_Inferencia(),  # type: ignore[arg-type]
        dmn=DmnDraftLocal(),
        cibseven=FakeCibSevenTransport(),
        audit_sink=FakeStartAuditSink(),
        whatsapp=envio,
        hoje=lambda: _HOJE,
        **extra,
    )
    final = dict(await grafo.compile_graph().compile().ainvoke(estado))
    return final, envio.textos


def _frases(texto: str) -> list[str]:
    return [f for f in re.split(r"(?<=[.!?])\s+|\n", texto) if f.strip()]


# --- 1. DESLIGADA = a de antes ---------------------------------------------------------------------------
@pytest.mark.parametrize("extra", [_PAGADOR_PF, _NAO_PAGADOR, _EMPRESA, {}])
@pytest.mark.parametrize("tipo", _TIPOS)
async def test_desligada_e_byte_a_byte_a_de_antes_mesmo_com_os_fatos_novos_no_estado(
    tipo: str, extra: dict[str, Any]
) -> None:
    final_antes, antes = await _turno(_estado(tipo, **extra), v4=None)
    final_off, desligada = await _turno(_estado(tipo, **extra, **_PLANTADOS), v4=False)
    assert desligada == antes
    assert (final_off["route"], final_off["desfecho"]) == (final_antes["route"], final_antes["desfecho"])
    assert all(MARCADOR_CODIGO not in t for t in desligada)


@pytest.mark.parametrize(
    ("valor", "ligada"), [(True, True), (False, False), ("true", False), (1, False), (None, False)]
)
async def test_a_fabrica_so_liga_com_true_literal(valor: Any, ligada: bool) -> None:
    envio = _Envio()
    grafo = build(
        {
            "inference": _Inferencia(),
            "dmn": DmnDraftLocal(),
            "cibseven": FakeCibSevenTransport(),
            "whatsapp": envio,
            "audit_sink": FakeStartAuditSink(),
            "lucas_v4": valor,
        }
    )
    await grafo.compile().ainvoke(_estado("consulta_valores", **_NAO_PAGADOR))
    assert (envio.textos == [TEXTO_NAO_PAGADOR_V4]) is ligada


# --- 2. quem NAO e' o pagador ---------------------------------------------------------------------------
@pytest.mark.parametrize("tipo", _TIPOS)
async def test_nao_pagador_ouve_o_responsavel_financeiro_sem_valor_nem_nome(tipo: str) -> None:
    final, textos = await _turno(_estado(tipo, **_NAO_PAGADOR))
    assert final["route"] == "respond_member" and final["process_started"] is False
    assert textos == [TEXTO_NAO_PAGADOR_V4]
    assert "R$" not in textos[0]


async def test_nao_pagador_perguntando_se_ja_pagou_tambem_ouve_o_responsavel() -> None:
    _, textos = await _turno(_estado("consulta_valores", cobranca_foco="ja_paguei", **_NAO_PAGADOR))
    assert textos == [TEXTO_NAO_PAGADOR_V4]


@pytest.mark.parametrize("tipo", _TIPOS)
async def test_o_atraso_do_titulo_do_responsavel_nunca_abre_caso_para_quem_nao_e_o_pagador(tipo: str) -> None:
    atraso = [
        {
            "competencia": "2026-08",
            "situacao": "vencida",
            "vencimento": "2026-08-10",
            "valor_total": "320.25",
            "valor_saldo": "320.25",
        },
        {
            "competencia": "2026-09",
            "situacao": "vencida",
            "vencimento": "2026-09-10",
            "valor_total": "320.25",
            "valor_saldo": "320.25",
        },
    ]
    final, textos = await _turno(
        _estado(tipo, competencias=atraso, ciclos_sem_conciliacao=2, dias_atraso_max=61, **_NAO_PAGADOR)
    )
    assert final["route"] == "respond_member" and final["process_started"] is False
    assert textos == [TEXTO_NAO_PAGADOR_V4]


async def test_a_pergunta_repetida_do_nao_pagador_nao_repete_o_mesmo_texto() -> None:
    final, primeira = await _turno(_estado("consulta_valores", **_NAO_PAGADOR))
    _, segunda = await _turno(
        _estado("consulta_valores", resposta_anterior_ref=final["resposta_memoria_ref"], **_NAO_PAGADOR)
    )
    assert primeira == [TEXTO_NAO_PAGADOR_V4]
    assert segunda == [TEXTO_NAO_PAGADOR_V4_CURTO]


# --- 2b. FALHA FECHADA: sem o sinal de que a pessoa E o pagador, ninguem recebe valor -----------------------
# Achado do aceite de 16/10/2026: o dev lia o billing-status 0.2.0 (sem `beneficiario_e_pagador`) e um
# dependente ouviu valor, vencimento e "ja' paga" como se fosse o pagador.
_SEM_SINAL = {"pagador_tipo": "pessoa_fisica"}
_SINAL_NULO = {"pagador_tipo": "pessoa_fisica", "beneficiario_e_pagador": None}
_SINAL_NAO_BOOLEANO = {"pagador_tipo": "pessoa_fisica", "beneficiario_e_pagador": "true"}
_PAGADOR_DESCONHECIDO = {"pagador_tipo": "desconhecido", "beneficiario_e_pagador": True}
_SEM_NADA: dict[str, Any] = {}


@pytest.mark.parametrize(
    "extra", [_SEM_SINAL, _SINAL_NULO, _SINAL_NAO_BOOLEANO, _PAGADOR_DESCONHECIDO, _SEM_NADA]
)
@pytest.mark.parametrize("tipo", _TIPOS)
async def test_sem_o_sinal_do_pagador_nao_ha_valor_nem_boleto_e_nao_abre_caso(
    tipo: str, extra: dict[str, Any]
) -> None:
    final, textos = await _turno(_estado(tipo, **extra))
    assert final["route"] == "respond_member" and final["process_started"] is False
    assert textos == [TEXTO_PAGADOR_NAO_CONFIRMADO_V4]
    assert "R$" not in textos[0] and MARCADOR_CODIGO not in textos[0]


@pytest.mark.parametrize("extra", [_SEM_SINAL, _SINAL_NULO, _SEM_NADA])
async def test_sem_o_sinal_o_atraso_do_titulo_nunca_abre_caso_nem_mostra_valor(extra: dict[str, Any]) -> None:
    atraso = [
        {
            "competencia": "2026-08",
            "situacao": "vencida",
            "vencimento": "2026-08-10",
            "valor_total": "320.25",
            "valor_saldo": "320.25",
        },
        {
            "competencia": "2026-09",
            "situacao": "vencida",
            "vencimento": "2026-09-10",
            "valor_total": "320.25",
            "valor_saldo": "320.25",
        },
    ]
    final, textos = await _turno(
        _estado(
            "consulta_valores", competencias=atraso, ciclos_sem_conciliacao=2, dias_atraso_max=61, **extra
        )
    )
    assert final["route"] == "respond_member" and final["process_started"] is False
    assert textos == [TEXTO_PAGADOR_NAO_CONFIRMADO_V4]


@pytest.mark.parametrize("foco", ["ja_paguei", "ultima", "esclarecimento"])
async def test_sem_o_sinal_os_focos_de_acompanhamento_tambem_nao_mostram_valor(foco: str) -> None:
    _, textos = await _turno(_estado("consulta_valores", cobranca_foco=foco, **_SEM_SINAL))
    assert textos == [TEXTO_PAGADOR_NAO_CONFIRMADO_V4]


async def test_sem_o_sinal_a_pergunta_repetida_sai_curta() -> None:
    final, primeira = await _turno(_estado("consulta_valores", **_SEM_SINAL))
    _, segunda = await _turno(
        _estado("consulta_valores", resposta_anterior_ref=final["resposta_memoria_ref"], **_SEM_SINAL)
    )
    assert primeira == [TEXTO_PAGADOR_NAO_CONFIRMADO_V4]
    assert segunda == [TEXTO_PAGADOR_NAO_CONFIRMADO_V4_CURTO]


async def test_so_o_sinal_falso_afirma_quem_e_o_responsavel() -> None:
    _, falso = await _turno(_estado("consulta_valores", **_NAO_PAGADOR))
    _, ausente = await _turno(_estado("consulta_valores", **_SEM_SINAL))
    assert falso == [TEXTO_NAO_PAGADOR_V4] and "titular" in falso[0]
    assert "titular" not in ausente[0] and ausente != falso


@pytest.mark.parametrize("tipo", _TIPOS)
async def test_adesao_e_empresa_nao_mudam_com_ou_sem_o_sinal(tipo: str) -> None:
    for extra in (_EMPRESA, {**_EMPRESA, "beneficiario_e_pagador": None}):
        _, antes = await _turno(_estado(tipo, **extra), v4=None)
        _, ligada = await _turno(_estado(tipo, **extra), v4=True)
        assert ligada == antes
    _, adm_antes = await _turno(_estado(tipo, cobranca_administradora=True, **_SEM_SINAL), v4=None)
    _, adm = await _turno(_estado(tipo, cobranca_administradora=True, **_SEM_SINAL))
    assert adm == adm_antes and TEXTO_PAGADOR_NAO_CONFIRMADO_V4 not in adm


@pytest.mark.parametrize("tipo", _TIPOS)
async def test_o_pagador_confirmado_continua_recebendo_a_resposta_de_antes(tipo: str) -> None:
    _, antes = await _turno(_estado(tipo, **_PAGADOR_PF), v4=None)
    _, ligada = await _turno(_estado(tipo, **_PAGADOR_PF), v4=True)
    assert TEXTO_PAGADOR_NAO_CONFIRMADO_V4 not in ligada and TEXTO_NAO_PAGADOR_V4 not in ligada
    if tipo in ("vencimento", "boleto", "2a_via"):
        assert ligada == antes
    if tipo == "consulta_valores":
        assert "R$" in ligada[0]


@pytest.mark.parametrize("tipo", _TIPOS)
async def test_empresa_segue_para_o_rh_igual_ao_de_antes(tipo: str) -> None:
    _, antes = await _turno(_estado(tipo, **_EMPRESA), v4=None)
    _, ligada = await _turno(_estado(tipo, **_EMPRESA), v4=True)
    assert ligada == antes
    assert "empresa contratante" in ligada[0] and "R$" not in ligada[0]


# --- 3. o PAGADOR: total primeiro ----------------------------------------------------------------------
async def test_pagador_ouve_o_total_primeiro_e_so_depois_a_parte() -> None:
    final, textos = await _turno(_estado(**_PAGADOR_PF))
    assert final["route"] == "respond_member" and final["process_started"] is False
    assert textos == [
        "O valor total da mensalidade de outubro de 2026 é de R$ 1.280,40, com vencimento em 20/10. "
        f"{TEXTO_TOTAL_SOMA_V4} A sua parte é de R$ 320,25. (dados de 12/10)"
    ]


async def test_pagador_com_o_mes_pago_diz_no_passado() -> None:
    _, textos = await _turno(_estado(competencias=_competencias("paga"), **_PAGADOR_PF))
    assert textos == [
        "O valor total da mensalidade de outubro de 2026 foi de R$ 1.280,40 e já está paga "
        f"(você pagou em 08/10). {TEXTO_TOTAL_SOMA_V4} A sua parte foi de R$ 320,25. (dados de 12/10)"
    ]


async def test_pagador_sem_o_total_na_fonte_segue_com_a_frase_de_sempre() -> None:
    _, textos = await _turno(_estado(competencias=_competencias(total=None), **_PAGADOR_PF))
    assert "valor total" not in textos[0] and "R$ 320,25" in textos[0]


async def test_pagador_com_total_igual_a_parte_nao_repete_a_parte() -> None:
    _, textos = await _turno(_estado(competencias=_competencias(total="320.25"), **_PAGADOR_PF))
    assert "A sua parte" not in textos[0] and "R$ 320,25" in textos[0]


async def test_a_pergunta_de_vencimento_do_pagador_nao_muda() -> None:
    _, antes = await _turno(_estado("vencimento", **_PAGADOR_PF), v4=None)
    _, ligada = await _turno(_estado("vencimento", **_PAGADOR_PF), v4=True)
    assert ligada == antes


# --- 4. o PAGADOR: detalhe por beneficiario (billing-status 0.4.0) -------------------------------------
async def test_pagador_ouve_o_total_e_o_detalhe_por_beneficiario_sem_nome() -> None:
    final, textos = await _turno(_estado(detalhamento_pagador=[_DETALHE], **_PAGADOR_PF))
    assert final["route"] == "respond_member" and final["mensagem"]["recusa_de_saida"] is False
    assert textos == [
        "\n".join(
            [
                "O valor total da mensalidade de outubro de 2026 é de R$ 720,00, com vencimento em 20/10. "
                + TEXTO_TOTAL_SOMA_V4,
                "Detalhe por beneficiário:",
                "• Você: pré-estabelecido R$ 400,00 + coparticipação R$ 20,00 = R$ 420,00",
                "• Dependente 1: pré-estabelecido R$ 300,00 + coparticipação R$ 0,00 = R$ 300,00",
                "(dados de 12/10)",
            ]
        )
    ]


async def test_o_detalhe_mostra_outros_quando_ha_adicionais() -> None:
    grupo = json.loads(json.dumps(_DETALHE))
    grupo["total_mensalidade"] = "425.00"
    grupo["beneficiarios"] = [
        {
            "rotulo": "titular",
            "valor_pre_estabelecido": "400.00",
            "valor_coparticipacao": "20.00",
            "valor_adicionais": "5.00",
            "valor_outros": "0.00",
            "valor_total": "425.00",
        }
    ]
    _, textos = await _turno(_estado(detalhamento_pagador=[grupo], **_PAGADOR_PF))
    assert (
        "• Titular: pré-estabelecido R$ 400,00 + coparticipação R$ 20,00 (+ outros R$ 5,00) = R$ 425,00"
        in textos[0]
    )


@pytest.mark.parametrize(
    "mudanca",
    [
        {"fecha": False},
        {"beneficiarios": []},
        {"beneficiarios": [{**_DETALHE["beneficiarios"][0], "valor_outros": "3.00"}]},
        {"beneficiarios": [{**_DETALHE["beneficiarios"][0], "valor_pre_estabelecido": None}]},
        {"beneficiarios": [{**_DETALHE["beneficiarios"][0], "rotulo": "Maria"}]},
    ],
)
async def test_detalhamento_que_nao_fecha_ou_com_nome_vira_so_o_total(mudanca: dict[str, Any]) -> None:
    grupo = {**_DETALHE, **mudanca}
    _, textos = await _turno(_estado(detalhamento_pagador=[grupo], **_PAGADOR_PF))
    assert "•" not in textos[0] and "Maria" not in textos[0]
    assert "R$ 720,00" in textos[0] and TEXTO_DETALHE_NO_APP_V4 in textos[0]


async def test_mais_de_dez_beneficiarios_nao_vira_lista() -> None:
    grupo = {**_DETALHE, "beneficiarios": [_DETALHE["beneficiarios"][1]] * 11}
    _, textos = await _turno(_estado(detalhamento_pagador=[grupo], **_PAGADOR_PF))
    assert "•" not in textos[0] and TEXTO_DETALHE_NO_APP_V4 in textos[0]


async def test_detalhamento_de_outra_competencia_nao_e_usado() -> None:
    grupo = {**_DETALHE, "competencia": "2026-09"}
    _, textos = await _turno(_estado(detalhamento_pagador=[grupo], **_PAGADOR_PF))
    assert "•" not in textos[0] and "R$ 1.280,40" in textos[0]


async def test_o_detalhamento_so_vale_para_o_pagador() -> None:
    for extra in (_NAO_PAGADOR, _EMPRESA):
        _, textos = await _turno(_estado(detalhamento_pagador=[_DETALHE], **extra))
        assert all("•" not in t and "720,00" not in t for t in textos)


async def test_a_cerca_de_saida_recusa_um_valor_do_detalhe_que_nao_esta_nos_fatos() -> None:
    """O texto sai da cerca com os valores DO detalhamento; um valor solto continua recusado."""
    fatos = {"detalhamento": _DETALHE, "competencias": [{"competencia": "2026-10", "situacao": "em_aberto"}]}
    assert motivo_de_recusa("O total é de R$ 720,00.", "mensagem", fatos) is None
    assert motivo_de_recusa("O total é de R$ 721,00.", "mensagem", fatos) is not None


# --- 5. a 2a via do pagador ----------------------------------------------------------------------------
_TEXTO_COM_CODIGO = "\n".join(
    [
        "O boleto da mensalidade de outubro de 2026 (R$ 1.280,40, vence em 20/10) está em aberto.",
        "Código de barras (copie e cole no aplicativo do seu banco):",
        MARCADOR_CODIGO,
        TEXTO_PDF_V4,
    ]
)


@pytest.mark.parametrize("tipo", ["boleto", "2a_via"])
async def test_2a_via_do_pagador_leva_o_marcador_e_nunca_o_codigo(tipo: str) -> None:
    final, textos = await _turno(
        _estado(tipo, segunda_via_estado="com_codigo", segunda_via_boletos=[_BOLETO], **_PAGADOR_PF)
    )
    assert final["route"] == "respond_member" and final["mensagem"]["recusa_de_saida"] is False
    assert textos == [_TEXTO_COM_CODIGO]
    # a memoria do turno e' o hash do texto COM o marcador e a resposta nunca e' a escada de repeticao
    assert final["resposta_memoria_ref"] == ref_de_resposta(_TEXTO_COM_CODIGO)
    assert not re.search(r"\d{20}", json.dumps(final, default=str))


async def test_2a_via_pedida_de_novo_sai_inteira_e_nao_diz_que_nao_envia() -> None:
    _, textos = await _turno(
        _estado(
            "2a_via",
            segunda_via_estado="com_codigo",
            segunda_via_boletos=[_BOLETO],
            assunto_anterior="boleto_2via",
            resposta_anterior_ref=ref_de_resposta(_TEXTO_COM_CODIGO),
            **_PAGADOR_PF,
        )
    )
    assert textos == [_TEXTO_COM_CODIGO]
    assert "não consigo enviar" not in textos[0].lower() and "não envio" not in textos[0].lower()


async def test_2a_via_vencida_diz_o_fato_sem_ameaca_e_passa_na_cerca() -> None:
    boleto = {**_BOLETO, "situacao": "vencida", "vencimento": "2026-10-05", "outros": 2}
    vencida = [
        {
            "competencia": "2026-10",
            "situacao": "vencida",
            "vencimento": "2026-10-05",
            "valor_total": "320.25",
            "valor_saldo": "320.25",
            "valor_boleto_pagador": "1280.40",
        }
    ]
    final, textos = await _turno(
        _estado(
            "2a_via",
            competencias=vencida,
            ciclos_sem_conciliacao=1,
            dias_atraso_max=8,
            segunda_via_estado="com_codigo",
            segunda_via_boletos=[boleto],
            **_PAGADOR_PF,
        )
    )
    assert final["route"] == "respond_member" and final["mensagem"]["recusa_de_saida"] is False
    texto = textos[0]
    assert "venceu em 05/10" in texto and MARCADOR_CODIGO in texto
    assert "valor a pagar pode ser diferente" in texto and TEXTO_OUTROS_BOLETOS_V4 in texto
    assert not re.search(r"multa|juros|inadimpl|suspens|negativ", texto.lower())


async def test_2a_via_sem_codigo_diz_que_nao_encontrou_e_da_os_canais() -> None:
    final, textos = await _turno(_estado("2a_via", segunda_via_estado="sem_codigo", **_PAGADOR_PF))
    assert final["route"] == "respond_member"
    assert textos == [TEXTO_SEM_CODIGO_V4]
    assert "aplicativo Austa Clínicas" in textos[0] and MARCADOR_CODIGO not in textos[0]


async def test_2a_via_sem_o_fato_novo_e_a_de_antes_com_a_chave_ligada() -> None:
    _, antes = await _turno(_estado("2a_via", **_PAGADOR_PF), v4=None)
    _, ligada = await _turno(_estado("2a_via", **_PAGADOR_PF), v4=True)
    assert ligada == antes and SEGUNDA_VIA_CANAIS in ligada[0]


@pytest.mark.parametrize("extra", [_NAO_PAGADOR, _EMPRESA, {"cobranca_administradora": True, **_PAGADOR_PF}])
async def test_so_o_pagador_recebe_o_marcador(extra: dict[str, Any]) -> None:
    _, textos = await _turno(
        _estado("2a_via", segunda_via_estado="com_codigo", segunda_via_boletos=[_BOLETO], **extra)
    )
    assert all(MARCADOR_CODIGO not in t and "Código de barras" not in t for t in textos)


@pytest.mark.parametrize(
    "boletos",
    [
        [],
        [{**_BOLETO, "situacao": "paga"}],
        [{**_BOLETO, "competencia": "outubro"}],
        ["x"],
    ],
)
async def test_fato_da_2a_via_sem_a_forma_do_contrato_segue_com_o_texto_de_antes(boletos: Any) -> None:
    _, antes = await _turno(_estado("2a_via", **_PAGADOR_PF), v4=None)
    _, ligada = await _turno(
        _estado("2a_via", segunda_via_estado="com_codigo", segunda_via_boletos=boletos, **_PAGADOR_PF)
    )
    assert ligada == antes


def test_o_boleto_do_estado_nao_tem_nenhum_campo_de_credencial() -> None:
    boleto = _boleto_da_segunda_via_v4(
        _estado("2a_via", segunda_via_boletos=[{**_BOLETO, "linha_digitavel": "1" * 47}], **_PAGADOR_PF)
    )
    assert boleto is not None
    assert "linha_digitavel" not in json.dumps(boleto) and "1" * 20 not in json.dumps(boleto)


# --- 6. regua de tom e cerca nos textos novos ----------------------------------------------------------
_TEXTOS_FIXOS = [
    TEXTO_NAO_PAGADOR_V4,
    TEXTO_NAO_PAGADOR_V4_CURTO,
    TEXTO_PAGADOR_NAO_CONFIRMADO_V4,
    TEXTO_PAGADOR_NAO_CONFIRMADO_V4_CURTO,
    TEXTO_SEM_CODIGO_V4,
    TEXTO_PDF_V4,
    TEXTO_OUTROS_BOLETOS_V4,
    TEXTO_TOTAL_SOMA_V4,
    TEXTO_DETALHE_NO_APP_V4,
]


@pytest.mark.parametrize("texto", _TEXTOS_FIXOS)
def test_textos_fixos_novos_tem_frases_curtas_e_passam_na_cerca(texto: str) -> None:
    for frase in _frases(texto):
        assert len(frase.split()) <= 20, frase
    assert motivo_de_recusa(texto, "mensagem", {}) is None
    assert not re.search(r"multa|juros|inadimpl|suspens|negativ|portal do benefici", texto.lower())


@pytest.mark.parametrize("situacao", ["em_aberto", "vencida"])
def test_texto_da_2a_via_com_codigo_tem_frases_curtas_e_o_marcador_em_linha_propria(situacao: str) -> None:
    boleto = _boleto_da_segunda_via_v4(
        _estado("2a_via", segunda_via_boletos=[{**_BOLETO, "situacao": situacao, "outros": 1}], **_PAGADOR_PF)
    )
    assert boleto is not None
    texto = texto_segunda_via_com_codigo_v4(boleto, _HOJE)
    assert MARCADOR_CODIGO in texto.split("\n")
    for frase in _frases(texto):
        assert len(frase.split()) <= 20, frase
    assert "outubro de 2026" in texto


def test_o_texto_nao_pagador_e_o_do_dono() -> None:
    assert TEXTO_NAO_PAGADOR_V4 == (
        "O responsável financeiro pelo contrato é o titular do contrato. "
        "Somente ele pode solicitar essa informação."
    )
    assert TEXTO_SEM_CODIGO_V4.startswith("Não encontrei o código de barras deste boleto.")
