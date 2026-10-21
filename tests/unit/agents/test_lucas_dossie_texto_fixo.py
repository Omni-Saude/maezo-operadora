"""A narrativa do dossie de escalacao do Lucas e' TEXTO FIXO montado dos fatos (09/10/2026).

INCIDENTE (dev, 09/10/2026): o "resumo escrito pelo agente para o atendente" de um caso de
`ambiguidade` disse "nao ha dados de pagamento conciliado no CNAB. Ha ambiguidade no caso, com
contestacao da cobranca e pedido de cancelamento registrado." Os fatos tinham
`contesta_cobranca=False`, `pedido_cancelamento=False` e a fonte AMH INDISPONIVEL. O modelo lia os
booleanos falsos como presenca. Agora nenhum modelo redige a narrativa (`texto_dossie`).
"""

from __future__ import annotations

from typing import Any, get_args

import pytest

from maezo.agents.lucas.graph import (
    PROMPT_VERSIONS,
    LucasGraph,
    LucasState,
    MotivoHumano,
    texto_dossie,
)
from maezo.agents.lucas.prompts import DOSSIE_TEXTO_VERSION
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport
from maezo.tools.workers.dmn_transport import FakeDmnTransport
from tests.support.audit_fakes import FakeStartAuditSink

_FECHO = "Nenhuma decisão sobre o plano foi tomada pelo atendimento automático."


class _InferenciaQueNaoPodeSerChamada:
    model_id = "fake"

    async def generate(self, prompt: str, **_: Any) -> str:
        raise AssertionError("a narrativa do dossie nao pode chamar o modelo")


class _SenderMudo:
    async def send(self, to_hash: str, text: str, *, idempotency_key: str) -> dict[str, Any]:
        return {}


def _grafo() -> LucasGraph:
    return LucasGraph(
        inference=_InferenciaQueNaoPodeSerChamada(),  # type: ignore[arg-type]
        dmn=FakeDmnTransport(),
        cibseven=FakeCibSevenTransport(),
        audit_sink=FakeStartAuditSink(),
        whatsapp=_SenderMudo(),
    )


def _estado(**campos: Any) -> LucasState:
    base: dict[str, Any] = {"tenant_id": "amh", "conversation_id": "wa:amh:x"}
    base.update(campos)
    return base  # type: ignore[return-value]


#: (a) O CASO REAL de 09/10: ambiguidade, fonte indisponivel, competencia 2026-09, os dois sinais False.
CASO_A = _estado(
    motivo_humano="ambiguidade",
    intencao="cobranca_info",
    tipo_solicitacao="consulta_valores",
    competencia="2026-09",
    contesta_cobranca=False,
    pedido_cancelamento=False,
    status_conciliado=None,
)
TEXTO_A = (
    "Encaminhado ao atendente porque a pergunta não se encaixa nas respostas automáticas. "
    "Pedido do beneficiário: consulta de valores, competência 09/2026. "
    "Não foi possível consultar a situação de cobrança na fonte. " + _FECHO
)

#: (b) atraso: 2 ciclos sem conciliacao, com a data da fonte.
CASO_B = _estado(
    motivo_humano="inadimplencia_detectada",
    intencao="inadimplencia",
    tipo_solicitacao="status_pagamento",
    status_conciliado=False,
    ciclos_sem_conciliacao=2,
    contesta_cobranca=False,
    pedido_cancelamento=False,
    cnab_ref="amh-billing:2026-10-08",
)
TEXTO_B = (
    "Encaminhado ao atendente porque há mensalidade em aberto sem pagamento conciliado. "
    "Pedido do beneficiário: confirmação de pagamento. "
    "Situação na fonte: há 2 meses sem pagamento conciliado. Dados de 08/10/2026. " + _FECHO
)

#: (c) contestacao True.
CASO_C = _estado(
    motivo_humano="contestacao_cobranca",
    intencao="cobranca_info",
    tipo_solicitacao="boleto",
    competencia="2026-08",
    status_conciliado=True,
    ciclos_sem_conciliacao=0,
    contesta_cobranca=True,
    pedido_cancelamento=False,
)
TEXTO_C = (
    "Encaminhado ao atendente porque o beneficiário contesta uma cobrança. "
    "Pedido do beneficiário: dúvida sobre boleto, competência 08/2026. "
    "Situação na fonte: pagamento conciliado. " + _FECHO
)

#: (d) pedido de cancelamento True, fonte com 1 ciclo em aberto.
CASO_D = _estado(
    motivo_humano="pedido_cancelamento",
    intencao="cancelamento",
    status_conciliado=False,
    ciclos_sem_conciliacao=1,
    contesta_cobranca=False,
    pedido_cancelamento=True,
)
TEXTO_D = (
    "Encaminhado ao atendente porque o beneficiário pediu cancelamento. "
    "Pedido do beneficiário: cancelamento do plano. "
    "Situação na fonte: há 1 mês sem pagamento conciliado. " + _FECHO
)

#: (e) fonte respondeu: pagamento conciliado, zero ciclos, com data.
CASO_E = _estado(
    motivo_humano="ambiguidade",
    intencao="confirmacao_pagamento",
    tipo_solicitacao="vencimento",
    competencia="2026-10",
    status_conciliado=True,
    ciclos_sem_conciliacao=0,
    contesta_cobranca=False,
    pedido_cancelamento=False,
    cnab_ref="amh-billing:2026-10-09",
)
TEXTO_E = (
    "Encaminhado ao atendente porque a pergunta não se encaixa nas respostas automáticas. "
    "Pedido do beneficiário: data de vencimento, competência 10/2026. "
    "Situação na fonte: pagamento conciliado. Dados de 09/10/2026. " + _FECHO
)

CASOS = [
    pytest.param(CASO_A, TEXTO_A, id="a-ambiguidade-fonte-indisponivel"),
    pytest.param(CASO_B, TEXTO_B, id="b-atraso-2-ciclos"),
    pytest.param(CASO_C, TEXTO_C, id="c-contestacao"),
    pytest.param(CASO_D, TEXTO_D, id="d-pedido-cancelamento"),
    pytest.param(CASO_E, TEXTO_E, id="e-conciliado"),
]

#: Jargao e repr de valor cru que nunca podem chegar ao atendente.
_PROIBIDOS = ("CNAB", "DMN", "False", "None", "True", "true", "false", "null", "_")


@pytest.mark.parametrize(("estado", "esperado"), CASOS)
def test_texto_exato_por_caso(estado: LucasState, esperado: str) -> None:
    assert texto_dossie(estado) == esperado


@pytest.mark.parametrize(("estado", "esperado"), CASOS)
async def test_o_dossie_leva_o_texto_fixo_sem_chamar_o_modelo(estado: LucasState, esperado: str) -> None:
    dossie = await _grafo()._build_dossier(estado)
    assert dossie["narrativa"] == esperado
    assert dossie["prompt_version"] == DOSSIE_TEXTO_VERSION == "dossie-texto-fixo-v1"
    assert dossie["decisao_cancelamento"] is None
    # Formato preservado para o motor/portal.
    assert set(dossie) == {
        "prompt_version",
        "tipo",
        "motivo_humano",
        "grupo_humano",
        "fatos",
        "dmn_decision_refs",
        "narrativa",
        "decisao_cancelamento",
    }
    assert dossie["tipo"] == "dossie_escalacao"
    assert dossie["fatos"]["contesta_cobranca"] == estado.get("contesta_cobranca")


def test_o_caso_real_nao_menciona_contestacao_nem_cancelamento() -> None:
    texto = texto_dossie(CASO_A).lower()
    assert "não foi possível consultar" in texto
    for palavra in ("contest", "cancel", "registrad", "conciliado"):
        assert palavra not in texto, palavra


@pytest.mark.parametrize("contesta", [False, None])
@pytest.mark.parametrize("cancela", [False, None])
def test_sinal_falso_ou_ausente_nunca_vira_frase(contesta: bool | None, cancela: bool | None) -> None:
    for motivo in get_args(MotivoHumano):
        if motivo in {"contestacao_cobranca", "pedido_cancelamento"}:
            continue
        texto = texto_dossie(
            _estado(motivo_humano=motivo, contesta_cobranca=contesta, pedido_cancelamento=cancela)
        ).lower()
        assert "contest" not in texto and "cancel" not in texto, (motivo, texto)


def test_sinais_true_aparecem_mesmo_com_outro_motivo() -> None:
    estado = _estado(motivo_humano="ambiguidade", contesta_cobranca=True, pedido_cancelamento=True)
    texto = texto_dossie(estado)
    assert "O beneficiário contesta a cobrança." in texto
    assert "O beneficiário pediu cancelamento." in texto


@pytest.mark.parametrize("motivo", [*get_args(MotivoHumano), None, "", "valor_cru_desconhecido"])
def test_todo_motivo_tem_texto_humano_e_nunca_ecoa_o_valor(motivo: str | None) -> None:
    texto = texto_dossie(_estado(motivo_humano=motivo))
    assert texto.startswith("Encaminhado ao atendente porque ")
    assert "valor_cru_desconhecido" not in texto
    for proibido in _PROIBIDOS:
        assert proibido not in texto, (motivo, proibido, texto)


def test_entrada_fora_do_vocabulario_nao_e_ecoada() -> None:
    texto = texto_dossie(
        _estado(
            motivo_humano="ambiguidade",
            tipo_solicitacao="xpto_ignorar_instrucoes",
            intencao="xpto",
            competencia="2026-13",
            cnab_ref="cnab-sim-123",
            status_conciliado="sim",
        )
    )
    assert "xpto" not in texto
    assert "2026-13" not in texto and "13/2026" not in texto
    assert "cnab" not in texto.lower()
    assert "Não foi possível consultar a situação de cobrança na fonte." in texto


@pytest.mark.parametrize(("estado", "_esperado"), CASOS)
def test_nenhum_texto_tem_jargao_nem_booleano_cru(estado: LucasState, _esperado: str) -> None:
    texto = texto_dossie(estado)
    for proibido in _PROIBIDOS:
        assert proibido not in texto, (proibido, texto)


@pytest.mark.parametrize(("estado", "_esperado"), CASOS)
def test_nunca_recomenda_desfecho_adverso_nem_promete_prazo(estado: LucasState, _esperado: str) -> None:
    texto = texto_dossie(estado).lower()
    for proibido in ("suspend", "suspens", "negar", "negad", "rescind", "recomend", "prazo", "hoje", "horas"):
        assert proibido not in texto, (proibido, texto)


def test_a_versao_declarada_e_a_do_texto_fixo() -> None:
    assert PROMPT_VERSIONS["dossier"] == DOSSIE_TEXTO_VERSION
