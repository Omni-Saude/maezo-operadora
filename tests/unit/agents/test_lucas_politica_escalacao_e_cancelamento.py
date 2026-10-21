"""Rodada 12 (decisao do dono, sessao de 14/10/2026): politica de escalacao do Lucas e cancelamento.

Politica: o Lucas responde os FATOS primeiro, sempre. So' abre caso humano para contestacao, negociacao/
parcelamento, cancelamento (PF confirmado), pedido explicito de pessoa ou atraso real (pagador PF com
>= 2 meses sem pagamento ou > 30 dias de atraso, limiares configuraveis). Contrato de empresa e adesao
nunca escalam pelo atraso da empresa/administradora. Fonte fora do ar e assunto sem tipo: texto fixo.

Cancelamento: empresa/administradora -> texto fixo; PF -> confirma UMA vez e so' entao inicia SP-OP-CANCEL-001
(motor FALSO — nenhuma instancia real); nunca diz que o plano foi cancelado.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from maezo.agents.lucas.graph import (
    OFERTA_PESSOA,
    TEXTO_CANCELAMENTO_ADMINISTRADORA,
    TEXTO_CANCELAMENTO_DESISTIU,
    TEXTO_CANCELAMENTO_EMPRESA,
    TEXTO_CANCELAMENTO_ENCAMINHADO,
    TEXTO_CANCELAMENTO_JA_REGISTRADO,
    TEXTO_CONFIRMA_CANCELAMENTO,
    TEXTO_FONTE_INDISPONIVEL,
    TEXTO_OUTRO_ASSUNTO,
    LucasGraph,
    LucasState,
    new_lucas_state,
    ref_de_resposta,
)
from maezo.agents.lucas.politica_atraso import LimiaresDeAtraso
from maezo.runtime.cobranca_intencao import confirmacao_valida, decidir_confirmacao, pede_negociacao
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport
from tests.evals.lucas.programa import DmnDraftLocal
from tests.support.audit_fakes import FakeStartAuditSink


def _competencias(vencidas: int) -> list[dict[str, Any]]:
    lista: list[dict[str, Any]] = []
    for i, mes in enumerate(["2026-10", "2026-09", "2026-08"]):
        base = {
            "competencia": mes,
            "vencimento": f"{mes}-10",
            "valor_total": "320.25",
            "valor_coparticipacao": "0.00",
        }
        if i < vencidas:
            lista.append({**base, "situacao": "vencida", "valor_saldo": "320.25"})
        else:
            lista.append({**base, "situacao": "paga", "valor_saldo": "0.00", "liquidado_em": f"{mes}-08"})
    return lista


def _fatos(vencidas: int, dias: int, pagador: str = "pessoa_fisica") -> dict[str, Any]:
    return {
        "status_conciliado": vencidas == 0,
        "ciclos_sem_conciliacao": vencidas,
        "numero_boleto": "",
        "cnab_ref": "amh-billing:2026-10-13",
        "valor_em_aberto": "320.25" if vencidas else "0.00",
        "dias_atraso_max": dias,
        "vencimento_referencia": "2026-10-10",
        "competencias_cobranca": _competencias(vencidas),
        "pagador_tipo": "pessoa_fisica" if pagador == "administradora" else pagador,
        **({"cobranca_administradora": True} if pagador == "administradora" else {}),
    }


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


def _estado(tipo: str, intencao: str = "cobranca_info", **extra: Any) -> LucasState:
    return new_lucas_state(
        {
            "tenant_id": "amh",
            "conversation_id": "wa:amh:hk1_r12",
            "canal": "whatsapp",
            "beneficiario_pseudo_id": "pseudo-r12",
            "to_hash": "hash-r12",
            "intencao": intencao,
            "tipo_solicitacao": tipo,
            **extra,
        }
    )


async def _turno(
    estado: LucasState,
    *,
    limiares: LimiaresDeAtraso | None = None,
    motor: FakeCibSevenTransport | None = None,
) -> tuple[dict[str, Any], _Envio, FakeCibSevenTransport]:
    envio = _Envio()
    motor = FakeCibSevenTransport() if motor is None else motor
    grafo = LucasGraph(
        inference=_Inferencia(),  # type: ignore[arg-type]
        dmn=DmnDraftLocal(),
        cibseven=motor,
        audit_sink=FakeStartAuditSink(),
        whatsapp=envio,
        hoje=lambda: date(2026, 10, 13),
        limiares_atraso=limiares or LimiaresDeAtraso(),
    )
    final = dict(await grafo.compile_graph().compile().ainvoke(estado))
    return final, envio, motor


# --- politica de atraso: casos do dono ------------------------------------------------------------
@pytest.mark.parametrize("tipo", ["consulta_valores", "vencimento", "status_pagamento"])
@pytest.mark.parametrize(("vencidas", "dias"), [(1, 28), (1, 23), (1, 30)])
async def test_um_mes_ate_30_dias_responde_os_fatos_com_a_oferta_e_sem_caso(
    tipo: str, vencidas: int, dias: int
) -> None:
    final, envio, motor = await _turno(_estado(tipo, **_fatos(vencidas, dias)))
    assert final["route"] == "respond_member" and final["process_started"] is False
    assert len(envio.textos) == 1 and OFERTA_PESSOA in envio.textos[0]
    assert not motor._history


@pytest.mark.parametrize("tipo", ["consulta_valores", "vencimento", "status_pagamento"])
@pytest.mark.parametrize(("vencidas", "dias"), [(1, 54), (1, 31), (2, 28), (2, 54), (3, 90)])
async def test_dois_meses_ou_mais_de_30_dias_pessoa_fisica_abre_caso(
    tipo: str, vencidas: int, dias: int
) -> None:
    final, _, _ = await _turno(_estado(tipo, **_fatos(vencidas, dias)))
    assert final["route"] == "escalate_human" and final["process_started"] is True
    assert final["motivo_humano"] == "inadimplencia_detectada"


@pytest.mark.parametrize("pagador", ["pessoa_juridica", "administradora"])
@pytest.mark.parametrize("tipo", ["consulta_valores", "vencimento", "status_pagamento"])
async def test_empresa_e_adesao_nunca_escalam_pelo_atraso_do_titulo_de_terceiro(
    tipo: str, pagador: str
) -> None:
    final, envio, _ = await _turno(_estado(tipo, **_fatos(3, 90, pagador)))
    assert final["route"] == "respond_member" and final["process_started"] is False
    assert len(envio.textos) == 1


async def test_limiares_configuraveis_mudam_quando_o_atraso_e_critico() -> None:
    final, _, _ = await _turno(_estado("consulta_valores", **_fatos(1, 28)), limiares=LimiaresDeAtraso(1, 30))
    assert final["route"] == "escalate_human"
    final, _, _ = await _turno(_estado("consulta_valores", **_fatos(1, 40)), limiares=LimiaresDeAtraso(2, 60))
    assert final["route"] == "respond_member"


@pytest.mark.parametrize(
    ("meses", "dias", "esperado"),
    [(0, None, False), (1, 30, False), (1, 31, True), (2, 0, True), (0, 31, True), (1, None, False)],
)
def test_limiares_padrao_dois_meses_ou_mais_de_trinta_dias(
    meses: int, dias: int | None, esperado: bool
) -> None:
    assert LimiaresDeAtraso().critico(meses_vencidos=meses, dias_atraso_max=dias) is esperado


@pytest.mark.parametrize("args", [(0, 30), (25, 30), (2, 0), (2, 366), (True, 30)])
def test_limiares_fora_da_faixa_sao_recusados(args: tuple[Any, Any]) -> None:
    with pytest.raises(ValueError, match="limiar"):
        LimiaresDeAtraso(*args)


async def test_fonte_indisponivel_responde_texto_fixo_sem_caso() -> None:
    final, envio, _ = await _turno(_estado("consulta_valores", pagador_tipo="pessoa_fisica"))
    assert final["route"] == "respond_member" and final["process_started"] is False
    assert envio.textos == [TEXTO_FONTE_INDISPONIVEL]


async def test_assunto_fora_do_vocabulario_responde_texto_fixo_sem_caso() -> None:
    final, envio, _ = await _turno(_estado("", **_fatos(0, 0)))
    assert final["route"] == "respond_member" and final["process_started"] is False
    assert envio.textos == [TEXTO_OUTRO_ASSUNTO]


async def test_pedido_de_negociacao_abre_caso_mesmo_sem_atraso() -> None:
    final, _, _ = await _turno(_estado("consulta_valores", pedido_negociacao=True, **_fatos(1, 5)))
    assert final["route"] == "escalate_human" and final["motivo_humano"] == "pedido_negociacao"


async def test_pedido_de_negociacao_de_empresa_nao_escala() -> None:
    final, _, _ = await _turno(
        _estado("consulta_valores", pedido_negociacao=True, **_fatos(2, 60, "pessoa_juridica"))
    )
    assert final["route"] == "respond_member"


# --- cancelamento ---------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("pagador", "texto"),
    [("pessoa_juridica", TEXTO_CANCELAMENTO_EMPRESA), ("administradora", TEXTO_CANCELAMENTO_ADMINISTRADORA)],
)
async def test_cancelamento_de_titulo_de_terceiro_texto_fixo_sem_caso_nem_processo(
    pagador: str, texto: str
) -> None:
    final, envio, motor = await _turno(_estado("", "cancelamento", **_fatos(0, 0, pagador)))
    assert final["route"] == "respond_member" and final["process_started"] is False
    assert envio.textos == [texto] and OFERTA_PESSOA in texto
    assert not motor._history


async def test_cancelamento_pessoa_fisica_pergunta_uma_vez_sem_abrir_nada() -> None:
    final, envio, motor = await _turno(_estado("", "cancelamento", **_fatos(0, 0)))
    assert final["route"] == "respond_member" and final["process_started"] is False
    assert envio.textos == [TEXTO_CONFIRMA_CANCELAMENTO]
    assert final["resposta_memoria_ref"] == ref_de_resposta(TEXTO_CONFIRMA_CANCELAMENTO)
    assert not motor._history


def _confirmacao(valor: str | None, *, pendente: bool = True) -> LucasState:
    extra: dict[str, Any] = {"confirmacao_cancelamento": valor}
    if pendente:
        extra["resposta_anterior_ref"] = ref_de_resposta(TEXTO_CONFIRMA_CANCELAMENTO)
    return _estado("", "cancelamento", **_fatos(0, 0), **extra)


async def test_confirmacao_inicia_o_processo_de_cancelamento_com_o_contrato_e_nunca_diz_cancelado() -> None:
    final, envio, motor = await _turno(_confirmacao("confirma"))
    assert final["process_started"] is True
    chave = final["business_key"]
    assert chave.startswith("CANCEL-amh-")
    instancia = await motor.find_active_instance(chave)
    assert instancia is not None and instancia.process_key == "SP-OP-CANCEL-001"
    variaveis = motor._variables[chave]
    assert variaveis["tipo_solicitacao"] == "pedido_beneficiario"
    assert variaveis["origem_solicitacao"] == "agente_lucas"
    assert variaveis["source_agent_id"] == "lucas"
    assert variaveis["beneficiario_pseudo_id"] == "pseudo-r12"
    # O que o Lucas nao apura segue "nao estabelecido": a DMN do processo vai a analise humana.
    for campo in ("dentro_prazo", "titularidade_confirmada", "vinculo_ativo", "notificacao_previa_feita"):
        assert variaveis[campo] is False
    assert envio.textos == [TEXTO_CANCELAMENTO_ENCAMINHADO]
    assert "cancelado" not in envio.textos[0].lower()


async def test_confirmacao_repetida_diz_que_o_pedido_ja_esta_com_a_equipe() -> None:
    motor = FakeCibSevenTransport()
    await _turno(_confirmacao("confirma"), motor=motor)
    final, envio, _ = await _turno(_confirmacao("confirma"), motor=motor)
    assert final["process_ref"]["already_existed"] is True
    assert envio.textos == [TEXTO_CANCELAMENTO_JA_REGISTRADO]


async def test_confirmacao_sem_pergunta_pendente_nao_inicia_nada_e_pergunta() -> None:
    final, envio, motor = await _turno(_confirmacao("confirma", pendente=False))
    assert final["process_started"] is False and not motor._history
    assert envio.textos == [TEXTO_CONFIRMA_CANCELAMENTO]


async def test_desistencia_nao_inicia_nada() -> None:
    final, envio, motor = await _turno(_confirmacao("nega"))
    assert final["process_started"] is False and not motor._history
    assert envio.textos == [TEXTO_CANCELAMENTO_DESISTIU]


async def test_cancelamento_com_pagador_desconhecido_segue_o_caso_humano_de_antes() -> None:
    final, _, _ = await _turno(_estado("", "cancelamento"))
    assert final["route"] == "escalate_human" and final["motivo_humano"] == "pedido_cancelamento"


# --- lexico das intencoes --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "texto",
    ["Quero parcelar a mensalidade", "posso negociar a dívida?", "Tem acordo?", "quero renegociar"],
)
def test_pede_negociacao(texto: str) -> None:
    assert pede_negociacao(texto) is True


@pytest.mark.parametrize("texto", ["Qual o valor de setembro?", "Quando vence?", "", None, 3])
def test_nao_pede_negociacao(texto: object) -> None:
    assert pede_negociacao(texto) is False


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        ("SIM, QUERO CANCELAR", "confirma"),
        ("sim", "confirma"),
        ("Pode cancelar", "confirma"),
        ("não, deixa pra lá", "nega"),
        ("Não quero cancelar", "nega"),
        ("não", "nega"),
        ("desisti", "nega"),
        ("e o valor de setembro?", None),
        (None, None),
    ],
)
def test_decidir_confirmacao(texto: object, esperado: str | None) -> None:
    assert decidir_confirmacao(texto) == esperado


def test_confirmacao_valida_so_aceita_os_dois_valores() -> None:
    assert confirmacao_valida("confirma") and confirmacao_valida("nega")
    assert not confirmacao_valida("talvez") and not confirmacao_valida(None)
