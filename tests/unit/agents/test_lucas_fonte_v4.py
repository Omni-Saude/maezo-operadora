"""Lucas v4 (DL-0114): os adaptadores dos contratos novos da AMH, a fonte e a credencial de pagamento.

Tudo com FAKES: os contratos billing-status 0.4.0 e billing-segunda-via ainda estao UNPUBLISHED na AMH e o
executor real do gateway nao existe. O que se prova aqui e' o lado do Maezo:
  - cada resposta e' conferida CAMPO A CAMPO contra a forma do contrato (forma errada = `CONTRACT_VIOLATION`,
    nunca um dado consertado; rotulo com nome nao passa);
  - a pergunta vai com a finalidade fixa e, na 2a via, com o `X-Amh-Request-Ref` opaco e SEM a referencia de
    consentimento; o pagador so' recebe valor/codigo (quem nao e' recebe so' a relacao);
  - o codigo de barras e' CREDENCIAL: `repr` redigido, nao serializavel, nunca nos fatos que vao ao grafo;
  - o SEGUNDO SINAL (`fonte_detalhamento_habilitada`) so' liga com os dois blocos no pin.
"""

from __future__ import annotations

import asyncio
import copy
import json
import pickle
import re
from pathlib import Path
from typing import Any

import pytest

from maezo.agents.lucas.fonte_v4 import (
    ARTIFACT_DETALHAMENTO,
    ARTIFACT_SEGUNDA_VIA,
    LOCK_KEY_DETALHAMENTO,
    LOCK_KEY_SEGUNDA_VIA,
    MARCADOR_CODIGO,
    OPERATION_DETALHAMENTO,
    OPERATION_SEGUNDA_VIA,
    AdaptadorDetalhamento,
    AdaptadorSegundaVia,
    BillingDetalhamentoPort,
    BillingSegundaViaPort,
    BoletoSegundaVia,
    CredenciaisDoTurno,
    ExecutorGovernado,
    FatosV4,
    FonteV4,
    PedidoGovernado,
    RespostaGovernada,
    contratos_v4_pinados,
    fonte_detalhamento_habilitada,
)
from maezo.agents.lucas.graph import _CALLER_INPUT_FIELDS, TEXTO_SEM_CODIGO_V4, new_lucas_state
from maezo.ports.errors import PortFailureReason as Reason
from maezo.ports.errors import PortResult

REF = "amh:psr:v1:1b2f3a4c-5d6e-4f70-8a9b-0c1d2e3f4a5b"
OUTRA_REF = "amh:psr:v1:2b2f3a4c-5d6e-4f70-8a9b-0c1d2e3f4a5b"
REQUEST_REF = "hk1_0123456789abcdef"
LINHA = "00191.23454 67890.123457 67890.123457 7 10000000012345"
CODIGO = "00197100000000123451234567890123456789012345"


# --- dubles -------------------------------------------------------------------------------------------
class _Executor(ExecutorGovernado):
    def __init__(
        self,
        *,
        status: int = 200,
        corpo: Any = None,
        resposta: Any = None,
        levanta: Exception | None = None,
        demora_s: float = 0.0,
        operacoes: frozenset[str] | None = None,
    ) -> None:
        self.registered_operations = (
            operacoes if operacoes is not None else frozenset({OPERATION_DETALHAMENTO, OPERATION_SEGUNDA_VIA})
        )
        self._status = status
        self._corpo = corpo
        self._resposta = resposta
        self._levanta = levanta
        self._demora = demora_s
        self.pedidos: list[PedidoGovernado] = []

    async def execute(self, request: PedidoGovernado) -> Any:
        self.pedidos.append(request)
        if self._demora:
            await asyncio.sleep(self._demora)
        if self._levanta is not None:
            raise self._levanta
        if self._resposta is not None:
            return self._resposta
        corpo = self._corpo if isinstance(self._corpo, bytes) else json.dumps(self._corpo).encode()
        return PortResult.ok(RespostaGovernada(status_code=self._status, body=corpo))


def _beneficiario(rotulo: str = "você", relacao: str = "titular", **valores: Any) -> dict[str, Any]:
    base = {
        "rotulo": rotulo,
        "relacao": relacao,
        "e_voce": rotulo == "você",
        "valor_pre_estabelecido": "400.00",
        "valor_coparticipacao": "20.00",
        "valor_adicionais": "0.00",
        "valor_outros": "0.00",
        "valor_total": "420.00",
    }
    return {**base, **valores}


def _grupo(**mudancas: Any) -> dict[str, Any]:
    base = {
        "total_mensalidade": "720.00",
        "total_pre_estabelecido": "700.00",
        "total_coparticipacao": "20.00",
        "total_adicionais": "0.00",
        "qt_beneficiarios": 2,
        "detalhamento_fecha": True,
        "beneficiarios": [
            _beneficiario(),
            _beneficiario(
                "dependente 1",
                "dependente",
                valor_pre_estabelecido="300.00",
                valor_total="300.00",
                valor_coparticipacao="0.00",
                e_voce=False,
            ),
        ],
    }
    return {**base, **mudancas}


def _corpo_detalhamento(**resumo: Any) -> dict[str, Any]:
    return {
        "portable_subject_ref": REF,
        "resumo": {"responsavel_financeiro": "voce", "financeiro_liberado": True, **resumo},
        "competencias": [{"competencia": "2026-10", "detalhamento_pagador": [_grupo()]}],
    }


def _boleto(**mudancas: Any) -> dict[str, Any]:
    base = {
        "competencia": "2026-10",
        "vencimento": "2026-10-10",
        "situacao": "vencida",
        "valor_titulo": "123.45",
        "disponivel": True,
        "indisponivel_motivo": None,
        "linha_digitavel": LINHA,
        "codigo_barras": CODIGO,
    }
    return {**base, **mudancas}


def _corpo_segunda_via(*boletos: dict[str, Any]) -> dict[str, Any]:
    return {
        "portable_subject_ref": REF,
        "boletos": list(boletos) or [_boleto()],
        "pdf": {"disponivel": False, "orientacao": "pelo_aplicativo"},
        "fonte_atualizada_em": "2026-10-15T12:00:00Z",
    }


_ARGS_DET = {
    "purpose_of_use": "atendimento_whatsapp",
    "consent_decision_ref": "base-legal:execucao-de-contrato",
}
_ARGS_VIA = {"purpose_of_use": "atendimento_whatsapp", "request_ref": REQUEST_REF}


# --- 1. detalhamento: o adaptador ---------------------------------------------------------------------
async def test_detalhamento_aceita_a_resposta_do_contrato_e_pergunta_so_o_necessario() -> None:
    executor = _Executor(corpo=_corpo_detalhamento())
    adaptador = AdaptadorDetalhamento(executor)
    assert isinstance(adaptador, BillingDetalhamentoPort)
    resultado = await adaptador.get_billing_detalhamento(REF, competencia="2026-10", **_ARGS_DET)
    assert resultado.succeeded and resultado.value is not None
    visao = resultado.value
    assert (visao.responsavel_financeiro, visao.financeiro_liberado) == ("voce", True)
    (competencia,) = visao.competencias
    assert competencia.competencia == "2026-10" and competencia.grupos is not None
    (grupo,) = competencia.grupos
    assert grupo.total_mensalidade == "720.00" and grupo.detalhamento_fecha is True
    assert [b.rotulo for b in grupo.beneficiarios] == ["você", "dependente 1"]
    (pedido,) = executor.pedidos
    assert pedido.operation == OPERATION_DETALHAMENTO and pedido.method == "GET"
    assert pedido.path == f"/interop/billing-status/v1/subjects/{REF}/billing/status"
    assert dict(pedido.query) == {
        "purpose_of_use": "atendimento_whatsapp",
        "incluir_contratacao": "true",
        "incluir_detalhamento": "true",
        "somente_pagador": "true",
        "competencia": "2026-10",
    }
    # a referencia de consentimento e' metadado de auditoria, nunca query; nenhum request_ref nesta rota
    assert pedido.consent_decision_ref == "base-legal:execucao-de-contrato" and pedido.request_ref is None
    assert "consent" not in json.dumps(pedido.query)


async def test_detalhamento_sem_competencia_nao_manda_o_parametro() -> None:
    executor = _Executor(corpo=_corpo_detalhamento())
    await AdaptadorDetalhamento(executor).get_billing_detalhamento(REF, **_ARGS_DET)
    assert "competencia" not in dict(executor.pedidos[0].query)


async def test_quem_nao_e_o_pagador_recebe_so_a_relacao() -> None:
    corpo = {
        "portable_subject_ref": REF,
        "resumo": {"responsavel_financeiro": "outra_pessoa", "financeiro_liberado": False},
        "competencias": [],
    }
    resultado = await AdaptadorDetalhamento(_Executor(corpo=corpo)).get_billing_detalhamento(REF, **_ARGS_DET)
    assert resultado.value is not None
    assert (resultado.value.responsavel_financeiro, resultado.value.financeiro_liberado) == (
        "outra_pessoa",
        False,
    )
    assert resultado.value.competencias == ()


@pytest.mark.parametrize(
    "mudanca",
    [
        lambda c: c.update(portable_subject_ref=OUTRA_REF),  # resposta de OUTRO sujeito
        lambda c: c["resumo"].update(responsavel_financeiro="Maria da Silva"),
        lambda c: c["resumo"].update(responsavel_financeiro=["voce"]),
        lambda c: c["resumo"].update(financeiro_liberado="sim"),
        lambda c: c["competencias"][0]["detalhamento_pagador"][0]["beneficiarios"][0].update(rotulo="Maria"),
        lambda c: c["competencias"][0]["detalhamento_pagador"][0]["beneficiarios"][0].update(relacao="irmao"),
        lambda c: c["competencias"][0]["detalhamento_pagador"][0]["beneficiarios"][0].update(valor_total="1"),
        lambda c: c["competencias"][0]["detalhamento_pagador"][0].update(beneficiarios="x"),
        lambda c: c["competencias"][0]["detalhamento_pagador"][0].update(
            beneficiarios=[_beneficiario()] * 51
        ),
        lambda c: c["competencias"][0]["detalhamento_pagador"][0].update(qt_beneficiarios=-1),
        lambda c: c["competencias"][0].update(competencia="2026-13"),
        lambda c: c["competencias"][0].update(detalhamento_pagador=[_grupo()] * 6),
        lambda c: c.update(resumo="x"),
        lambda c: c.update(competencias={}),
    ],
)
async def test_detalhamento_fora_da_forma_do_contrato_e_violacao(mudanca: Any) -> None:
    corpo = copy.deepcopy(_corpo_detalhamento())
    mudanca(corpo)
    resultado = await AdaptadorDetalhamento(_Executor(corpo=corpo)).get_billing_detalhamento(REF, **_ARGS_DET)
    assert not resultado.succeeded and resultado.failure is not None
    assert resultado.failure.reason == Reason.CONTRACT_VIOLATION


@pytest.mark.parametrize("corpo", [b"nao e json", b"[]", b"\xff\xfe"])
async def test_detalhamento_com_corpo_ilegivel_e_violacao(corpo: bytes) -> None:
    resultado = await AdaptadorDetalhamento(_Executor(corpo=corpo)).get_billing_detalhamento(REF, **_ARGS_DET)
    assert resultado.failure is not None and resultado.failure.reason == Reason.CONTRACT_VIOLATION


@pytest.mark.parametrize(
    "mudanca",
    [
        {"portable_subject_ref": "cpf-12345678901"},
        {"purpose_of_use": "analytics"},
        {"consent_decision_ref": "  "},
        {"competencia": "outubro"},
        {"timeout_seconds": 0},
        {"timeout_seconds": float("nan")},
    ],
)
async def test_detalhamento_pedido_invalido_nem_chega_ao_executor(mudanca: dict[str, Any]) -> None:
    executor = _Executor(corpo=_corpo_detalhamento())
    argumentos = {"portable_subject_ref": REF, **_ARGS_DET, **mudanca}
    ref = argumentos.pop("portable_subject_ref")
    resultado = await AdaptadorDetalhamento(executor).get_billing_detalhamento(ref, **argumentos)
    assert resultado.failure is not None and resultado.failure.reason == Reason.INVALID_REQUEST
    assert executor.pedidos == []


# --- 2. os dois adaptadores: transporte e status -------------------------------------------------------
_STATUS = {
    400: Reason.INVALID_REQUEST,
    401: Reason.NOT_AUTHENTICATED,
    403: Reason.SCOPE_NOT_SUPPORTED,
    404: Reason.NOT_FOUND,
    429: Reason.RATE_LIMITED,
    500: Reason.UPSTREAM_UNAVAILABLE,
    418: Reason.CONTRACT_VIOLATION,
}


@pytest.mark.parametrize(("status", "motivo"), list(_STATUS.items()))
async def test_status_http_vira_recusa_fechada_nas_duas_rotas(status: int, motivo: Reason) -> None:
    corpo = {"error_code": "x", "message": "sem detalhe", "trace_id": "0" * 32}
    det = await AdaptadorDetalhamento(_Executor(status=status, corpo=corpo)).get_billing_detalhamento(
        REF, **_ARGS_DET
    )
    via = await AdaptadorSegundaVia(_Executor(status=status, corpo=corpo)).get_segunda_via(REF, **_ARGS_VIA)
    assert det.failure is not None and det.failure.reason == motivo
    assert via.failure is not None and via.failure.reason == motivo


async def test_operacao_nao_registrada_no_executor_e_escopo_nao_suportado() -> None:
    executor = _Executor(corpo=_corpo_detalhamento(), operacoes=frozenset({OPERATION_SEGUNDA_VIA}))
    resultado = await AdaptadorDetalhamento(executor).get_billing_detalhamento(REF, **_ARGS_DET)
    assert resultado.failure is not None and resultado.failure.reason == Reason.SCOPE_NOT_SUPPORTED
    assert executor.pedidos == []


async def test_executor_que_levanta_ou_demora_vira_recusa_sem_excecao() -> None:
    caiu = await AdaptadorDetalhamento(
        _Executor(levanta=RuntimeError("segredo no erro"))
    ).get_billing_detalhamento(REF, **_ARGS_DET)
    assert caiu.failure is not None and caiu.failure.reason == Reason.UPSTREAM_UNAVAILABLE
    assert caiu.failure.detail is None or "segredo" not in caiu.failure.detail
    lento = await AdaptadorSegundaVia(_Executor(corpo=_corpo_segunda_via(), demora_s=0.5)).get_segunda_via(
        REF, timeout_seconds=0.01, **_ARGS_VIA
    )
    assert lento.failure is not None and lento.failure.reason == Reason.TIMEOUT


async def test_erro_de_programacao_do_executor_nao_vira_recusa() -> None:
    with pytest.raises(TypeError):
        await AdaptadorSegundaVia(_Executor(levanta=TypeError("bug"))).get_segunda_via(REF, **_ARGS_VIA)


@pytest.mark.parametrize("resposta", ["x", PortResult.ok("nao e' uma resposta governada")])
async def test_envelope_fora_do_tipo_e_violacao(resposta: Any) -> None:
    resultado = await AdaptadorSegundaVia(_Executor(resposta=resposta)).get_segunda_via(REF, **_ARGS_VIA)
    assert resultado.failure is not None and resultado.failure.reason == Reason.CONTRACT_VIOLATION


async def test_recusa_do_executor_passa_so_o_motivo() -> None:
    recusa = PortResult.refused(Reason.RATE_LIMITED, detail="cpf 123.456.789-09 no detalhe")
    resultado = await AdaptadorSegundaVia(_Executor(resposta=recusa)).get_segunda_via(REF, **_ARGS_VIA)
    assert resultado.failure is not None and resultado.failure.reason == Reason.RATE_LIMITED
    assert resultado.failure.detail is None


def test_o_executor_nao_tem_implementacao_padrao() -> None:
    with pytest.raises(TypeError):
        ExecutorGovernado()  # type: ignore[abstract]
    with pytest.raises(TypeError):
        AdaptadorDetalhamento(object())  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        AdaptadorSegundaVia(object())  # type: ignore[arg-type]


# --- 3. 2a via: o adaptador ---------------------------------------------------------------------------
async def test_segunda_via_aceita_o_contrato_e_manda_o_request_ref_sem_consentimento() -> None:
    executor = _Executor(corpo=_corpo_segunda_via(_boleto(), _boleto(competencia="2026-09")))
    adaptador = AdaptadorSegundaVia(executor)
    assert isinstance(adaptador, BillingSegundaViaPort)
    resultado = await adaptador.get_segunda_via(REF, competencia="2026-10", **_ARGS_VIA)
    assert resultado.succeeded and resultado.value is not None
    primeiro, segundo = resultado.value.boletos
    assert (primeiro.competencia, primeiro.situacao, primeiro.valor_titulo) == (
        "2026-10",
        "vencida",
        "123.45",
    )
    assert primeiro.linha_digitavel == LINHA and primeiro.codigo_barras == CODIGO and segundo.disponivel
    (pedido,) = executor.pedidos
    assert pedido.operation == OPERATION_SEGUNDA_VIA
    assert pedido.path == f"/interop/billing-status/v1/subjects/{REF}/billing/segunda-via"
    assert dict(pedido.query) == {"purpose_of_use": "atendimento_whatsapp", "competencia": "2026-10"}
    assert pedido.request_ref == REQUEST_REF and pedido.consent_decision_ref is None


async def test_boleto_indisponivel_traz_o_motivo_e_nenhuma_credencial() -> None:
    boleto = _boleto(
        disponivel=False, indisponivel_motivo="valor_diverge", linha_digitavel=None, codigo_barras=None
    )
    resultado = await AdaptadorSegundaVia(_Executor(corpo=_corpo_segunda_via(boleto))).get_segunda_via(
        REF, **_ARGS_VIA
    )
    assert resultado.value is not None
    (b,) = resultado.value.boletos
    assert b.disponivel is False and b.indisponivel_motivo == "valor_diverge" and b.linha_digitavel is None


@pytest.mark.parametrize(
    "mudanca",
    [
        lambda c: c["boletos"][0].update(linha_digitavel="123"),
        lambda c: c["boletos"][0].update(codigo_barras="123"),
        lambda c: c["boletos"][0].update(situacao="paga"),  # boleto pago nunca e' reexposto
        lambda c: c["boletos"][0].update(situacao=["vencida"]),
        lambda c: c["boletos"][0].update(indisponivel_motivo="inventado"),
        lambda c: c["boletos"][0].update(valor_titulo="123"),
        lambda c: c["boletos"][0].update(vencimento="10/10/2026"),
        lambda c: c["boletos"][0].update(linha_digitavel=None, codigo_barras=None),  # diz que ha' e nao traz
        lambda c: c["boletos"][0].update(disponivel=False),  # traz credencial que diz indisponivel
        lambda c: c.update(boletos=[]),
        lambda c: c.update(boletos=[_boleto()] * 4),
        lambda c: c.update(boletos="x"),
        lambda c: c["pdf"].update(disponivel=True),
        lambda c: c["pdf"].update(orientacao="enviar_aqui"),
        lambda c: c.update(pdf=None),
        lambda c: c.update(fonte_atualizada_em=""),
        lambda c: c.update(portable_subject_ref=OUTRA_REF),
    ],
)
async def test_segunda_via_fora_da_forma_do_contrato_e_violacao(mudanca: Any) -> None:
    corpo = copy.deepcopy(_corpo_segunda_via())
    mudanca(corpo)
    resultado = await AdaptadorSegundaVia(_Executor(corpo=corpo)).get_segunda_via(REF, **_ARGS_VIA)
    assert resultado.failure is not None and resultado.failure.reason == Reason.CONTRACT_VIOLATION
    assert resultado.value is None


@pytest.mark.parametrize(
    "mudanca",
    [
        {"request_ref": "curto"},
        {"request_ref": "tem espaco no meio do identificador"},
        {"request_ref": "x" * 65},
        {"purpose_of_use": "analytics"},
        {"competencia": "2026-1"},
        {"timeout_seconds": -1},
    ],
)
async def test_segunda_via_pedido_invalido_nem_chega_ao_executor(mudanca: dict[str, Any]) -> None:
    executor = _Executor(corpo=_corpo_segunda_via())
    resultado = await AdaptadorSegundaVia(executor).get_segunda_via(REF, **{**_ARGS_VIA, **mudanca})
    assert resultado.failure is not None and resultado.failure.reason == Reason.INVALID_REQUEST
    assert executor.pedidos == []


# --- 4. a CREDENCIAL ---------------------------------------------------------------------------------
async def test_a_credencial_nunca_aparece_em_repr_nem_em_str_dos_objetos() -> None:
    resultado = await AdaptadorSegundaVia(_Executor(corpo=_corpo_segunda_via())).get_segunda_via(
        REF, **_ARGS_VIA
    )
    assert resultado.value is not None
    for objeto in (resultado.value, resultado.value.boletos[0], resultado):
        texto = f"{objeto!r}|{objeto}"
        assert LINHA not in texto and CODIGO not in texto and "10000000012345" not in texto
    pedido = PedidoGovernado(
        operation=OPERATION_SEGUNDA_VIA,
        path="/x",
        query=(),
        consent_decision_ref=None,
        request_ref=REQUEST_REF,
        timeout_seconds=1.0,
    )
    resposta = RespostaGovernada(status_code=200, body=json.dumps(_corpo_segunda_via()).encode())
    assert CODIGO not in repr(resposta) and CODIGO not in str(resposta)
    assert pedido.__repr__().startswith("<")  # `repr=False`: o descritor nem lista os campos


def test_credencial_do_turno_e_opaca_e_nao_serializavel() -> None:
    credencial = CredenciaisDoTurno(LINHA)
    assert repr(credencial) == str(credencial) == "CredenciaisDoTurno(<redigido>)"
    assert LINHA not in f"{credencial!r}{credencial}{[credencial]}{ {'k': credencial} }"
    for serializar in (pickle.dumps, copy.copy, copy.deepcopy):
        with pytest.raises(TypeError):
            serializar(credencial)
    with pytest.raises(TypeError):
        json.dumps({"c": credencial})


def test_o_marcador_e_trocado_pelo_codigo_e_so_ele() -> None:
    texto = f"Antes\n{MARCADOR_CODIGO}\nDepois"
    assert CredenciaisDoTurno(LINHA).preencher(texto, sem_codigo="X") == f"Antes\n{LINHA}\nDepois"
    assert CredenciaisDoTurno(LINHA).preencher("sem marcador", sem_codigo="X") == "sem marcador"
    # sem credencial, o marcador cru NUNCA vai ao beneficiario: sai o texto honesto
    assert CredenciaisDoTurno(None).preencher(texto, sem_codigo=TEXTO_SEM_CODIGO_V4) == TEXTO_SEM_CODIGO_V4
    assert CredenciaisDoTurno("").tem_codigo is False and CredenciaisDoTurno(LINHA).tem_codigo is True


# --- 5. a fonte ---------------------------------------------------------------------------------------
class _Resolvedor:
    def __init__(self, ref: str | None = REF, *, verificado: bool = True) -> None:
        self.ref = ref
        self.identidade_verificada = verificado

    async def portable_ref(self, pseudo_id: str, *, phone_hash: str | None) -> str | None:
        return self.ref


class _Consentimento:
    def __init__(self, decisao: str | None = "consent-1") -> None:
        self.decisao_dada = decisao

    async def decisao(self, portable_ref: str, purpose_of_use: str) -> str | None:
        return self.decisao_dada


class _PortaDetalhamento:
    def __init__(self, resultado: Any) -> None:
        self.resultado = resultado
        self.chamadas: list[dict[str, Any]] = []

    async def get_billing_detalhamento(self, ref: str, **kw: Any) -> Any:
        self.chamadas.append({"ref": ref, **kw})
        return self.resultado


class _PortaSegundaVia:
    def __init__(self, resultado: Any) -> None:
        self.resultado = resultado
        self.chamadas: list[dict[str, Any]] = []

    async def get_segunda_via(self, ref: str, **kw: Any) -> Any:
        self.chamadas.append({"ref": ref, **kw})
        return self.resultado


async def _visao_detalhamento(corpo: dict[str, Any] | None = None) -> Any:
    resultado = await AdaptadorDetalhamento(
        _Executor(corpo=corpo or _corpo_detalhamento())
    ).get_billing_detalhamento(REF, **_ARGS_DET)
    return resultado


async def _visao_segunda_via(*boletos: dict[str, Any]) -> Any:
    return await AdaptadorSegundaVia(_Executor(corpo=_corpo_segunda_via(*boletos))).get_segunda_via(
        REF, **_ARGS_VIA
    )


def _fonte(
    *, detalhamento: Any = None, segunda_via: Any = None, resolvedor: Any = None, consentimento: Any = None
) -> FonteV4:
    return FonteV4(
        resolvedor=resolvedor or _Resolvedor(),
        consentimento=consentimento or _Consentimento(),
        detalhamento=detalhamento,
        segunda_via=segunda_via,
        timeout_seconds=5.0,
    )


def test_a_fonte_exige_identidade_verificada_e_prazo_valido() -> None:
    with pytest.raises(ValueError, match="identidade verificada"):
        _fonte(resolvedor=_Resolvedor(verificado=False))
    with pytest.raises(ValueError, match="timeout"):
        FonteV4(resolvedor=_Resolvedor(), consentimento=_Consentimento(), timeout_seconds=0)
    with pytest.raises(ValueError, match="timeout"):
        FonteV4(resolvedor=_Resolvedor(), consentimento=_Consentimento(), timeout_seconds=float("inf"))


async def test_pagador_recebe_o_total_e_o_detalhamento_sem_nome() -> None:
    porta = _PortaDetalhamento(await _visao_detalhamento())
    fatos = await _fonte(detalhamento=porta).detalhamento("pseudo", "2026-10", phone_hash=None)
    assert fatos is not None and fatos.responsavel_financeiro == "voce"
    ((competencia, grupo),) = fatos.detalhamento
    assert competencia == "2026-10" and grupo.total_mensalidade == "720.00"
    assert porta.chamadas == [
        {
            "ref": REF,
            "purpose_of_use": "atendimento_whatsapp",
            "consent_decision_ref": "consent-1",
            "competencia": "2026-10",
            "timeout_seconds": 5.0,
        }
    ]
    entrada = fatos.como_entrada_lucas()
    assert (
        set(entrada) == {"responsavel_financeiro", "detalhamento_pagador"}
        and set(entrada) <= _CALLER_INPUT_FIELDS
    )
    assert [b["rotulo"] for b in entrada["detalhamento_pagador"][0]["beneficiarios"]] == [
        "você",
        "dependente 1",
    ]
    new_lucas_state({"tenant_id": "amh", **entrada})  # a borda do grafo aceita exatamente estas chaves


async def test_quem_nao_e_o_pagador_nunca_leva_valor_adiante() -> None:
    corpo = {
        "portable_subject_ref": REF,
        "resumo": {"responsavel_financeiro": "outra_pessoa", "financeiro_liberado": False},
        "competencias": [
            {"competencia": "2026-10", "detalhamento_pagador": [_grupo()]}
        ],  # o servidor reteria
    }
    fatos = await _fonte(detalhamento=_PortaDetalhamento(await _visao_detalhamento(corpo))).detalhamento(
        "pseudo", "2026-10"
    )
    assert fatos is not None and fatos.detalhamento == ()
    assert fatos.como_entrada_lucas() == {"responsavel_financeiro": "outra_pessoa"}


async def test_mais_de_um_grupo_de_pagador_nunca_e_somado() -> None:
    corpo = _corpo_detalhamento()
    corpo["competencias"][0]["detalhamento_pagador"] = [_grupo(), _grupo(total_mensalidade="10.00")]
    fatos = await _fonte(detalhamento=_PortaDetalhamento(await _visao_detalhamento(corpo))).detalhamento(
        "pseudo", None
    )
    assert fatos is not None and fatos.detalhamento == ()


@pytest.mark.parametrize(
    "resolvedor",
    [_Resolvedor(None)],
)
async def test_sem_sujeito_ou_sem_consentimento_nao_ha_fato(resolvedor: Any) -> None:
    porta = _PortaDetalhamento(await _visao_detalhamento())
    assert await _fonte(detalhamento=porta, resolvedor=resolvedor).detalhamento("pseudo", None) is None
    assert (
        await _fonte(detalhamento=porta, consentimento=_Consentimento(None)).detalhamento("pseudo", None)
        is None
    )
    assert await _fonte(detalhamento=porta).detalhamento("", None) is None
    assert porta.chamadas == []


async def test_recusa_da_porta_ou_porta_ausente_nao_ha_fato() -> None:
    recusa = PortResult.refused(Reason.UPSTREAM_UNAVAILABLE)
    assert await _fonte(detalhamento=_PortaDetalhamento(recusa)).detalhamento("pseudo", None) is None
    assert await _fonte().detalhamento("pseudo", None) is None
    assert await _fonte().segunda_via("pseudo", None, request_ref=REQUEST_REF) == (None, None)


async def test_segunda_via_entrega_os_fatos_sem_credencial_e_a_credencial_a_parte() -> None:
    sem_codigo = _boleto(
        competencia="2026-08",
        disponivel=False,
        indisponivel_motivo="sem_bloqueto",
        linha_digitavel=None,
        codigo_barras=None,
    )
    porta = _PortaSegundaVia(await _visao_segunda_via(sem_codigo, _boleto(), _boleto(competencia="2026-09")))
    fatos, credenciais = await _fonte(segunda_via=porta).segunda_via(
        "pseudo", "2026-10", phone_hash=None, request_ref=REQUEST_REF
    )
    assert fatos is not None and credenciais is not None
    assert fatos.segunda_via_estado == "com_codigo" and fatos.segunda_via_outros == 1
    assert [b.competencia for b in fatos.segunda_via_boletos] == ["2026-10"]
    assert porta.chamadas == [
        {
            "ref": REF,
            "purpose_of_use": "atendimento_whatsapp",
            "request_ref": REQUEST_REF,
            "competencia": "2026-10",
            "timeout_seconds": 5.0,
        }
    ]
    entrada = fatos.como_entrada_lucas()
    assert (
        set(entrada) == {"segunda_via_estado", "segunda_via_boletos"} and set(entrada) <= _CALLER_INPUT_FIELDS
    )
    assert entrada["segunda_via_boletos"] == [
        {
            "competencia": "2026-10",
            "vencimento": "2026-10-10",
            "situacao": "vencida",
            "valor_titulo": "123.45",
            "outros": 1,
        }
    ]
    # NENHUMA credencial nos fatos (nem como pedaco de digitos) — so' em `CredenciaisDoTurno`
    serializado = json.dumps(entrada)
    assert LINHA not in serializado and CODIGO not in serializado and not re.search(r"\d{15}", serializado)
    assert credenciais.preencher(MARCADOR_CODIGO, sem_codigo="X") == LINHA


async def test_sem_a_linha_digitavel_vale_o_codigo_de_44_digitos() -> None:
    boleto = _boleto(linha_digitavel=None)
    porta = _PortaSegundaVia(await _visao_segunda_via(boleto))
    _, credenciais = await _fonte(segunda_via=porta).segunda_via("p", None, request_ref=REQUEST_REF)
    assert credenciais is not None and credenciais.preencher(MARCADOR_CODIGO, sem_codigo="X") == CODIGO


async def test_pagador_sem_codigo_disponivel_ou_sem_boleto_e_o_estado_sem_codigo() -> None:
    indisponivel = _boleto(
        disponivel=False, indisponivel_motivo="dv_invalido", linha_digitavel=None, codigo_barras=None
    )
    for porta in (
        _PortaSegundaVia(await _visao_segunda_via(indisponivel)),
        _PortaSegundaVia(
            PortResult.refused(Reason.NOT_FOUND)
        ),  # 404: igual para "sem boleto" e "nao e' o pagador"
    ):
        fatos, credenciais = await _fonte(segunda_via=porta).segunda_via("p", None, request_ref=REQUEST_REF)
        assert fatos is not None and fatos.segunda_via_estado == "sem_codigo"
        assert fatos.segunda_via_boletos == () and credenciais is not None and not credenciais.tem_codigo
        assert fatos.como_entrada_lucas()["segunda_via_boletos"] == []


@pytest.mark.parametrize(
    "motivo", [Reason.UPSTREAM_UNAVAILABLE, Reason.TIMEOUT, Reason.RATE_LIMITED, Reason.SCOPE_NOT_SUPPORTED]
)
async def test_falha_da_rota_nao_vira_nao_encontrei(motivo: Reason) -> None:
    porta = _PortaSegundaVia(PortResult.refused(motivo))
    assert await _fonte(segunda_via=porta).segunda_via("p", None, request_ref=REQUEST_REF) == (None, None)


async def test_o_repr_dos_fatos_v4_nao_tem_credencial() -> None:
    porta = _PortaSegundaVia(await _visao_segunda_via())
    fatos, credenciais = await _fonte(segunda_via=porta).segunda_via("p", None, request_ref=REQUEST_REF)
    assert fatos is not None and credenciais is not None
    for objeto in (fatos, credenciais, (fatos, credenciais), BoletoSegundaVia):
        texto = f"{objeto!r}"
        assert LINHA not in texto and CODIGO not in texto


def test_fatos_vazios_nao_criam_nenhuma_chave() -> None:
    assert FatosV4().como_entrada_lucas() == {}
    assert FatosV4(responsavel_financeiro="Maria").como_entrada_lucas() == {}  # fora do vocabulario


# --- 6. o SEGUNDO SINAL -------------------------------------------------------------------------------
def _pin(tmp_path: Path, *, detalhamento: bool = True, segunda_via: bool = True, sha: str = "a" * 64) -> Path:
    bruto: dict[str, Any] = {"manifest_v1_6": {"artifacts": []}}
    if detalhamento:
        bruto[LOCK_KEY_DETALHAMENTO] = {"artifacts": [{"path": ARTIFACT_DETALHAMENTO, "sha256": sha}]}
    if segunda_via:
        bruto[LOCK_KEY_SEGUNDA_VIA] = {"artifacts": [{"path": ARTIFACT_SEGUNDA_VIA, "sha256": sha}]}
    caminho = tmp_path / "contracts.lock.json"
    caminho.write_text(json.dumps(bruto), encoding="utf-8")
    return caminho


def test_o_sinal_so_liga_com_a_chave_e_os_dois_contratos_pinados(tmp_path: Path) -> None:
    ok = _pin(tmp_path)
    assert contratos_v4_pinados(ok) is True
    assert fonte_detalhamento_habilitada(chave_ligada=True, pin_path=ok) is True
    assert fonte_detalhamento_habilitada(chave_ligada=False, pin_path=ok) is False


@pytest.mark.parametrize(
    "faltando",
    [
        {"detalhamento": False},
        {"segunda_via": False},
        {"detalhamento": False, "segunda_via": False},
        {"sha": ""},
    ],
)
def test_sem_os_dois_blocos_o_sinal_fica_desligado(tmp_path: Path, faltando: dict[str, Any]) -> None:
    pin = _pin(tmp_path, **faltando)
    assert contratos_v4_pinados(pin) is False
    assert fonte_detalhamento_habilitada(chave_ligada=True, pin_path=pin) is False


def test_pin_ausente_ilegivel_ou_de_outro_formato_nunca_levanta_e_desliga(tmp_path: Path) -> None:
    assert contratos_v4_pinados(None) is False
    assert contratos_v4_pinados(tmp_path / "nao-existe.json") is False
    lixo = tmp_path / "lixo.json"
    lixo.write_text("nao e json", encoding="utf-8")
    assert contratos_v4_pinados(lixo) is False
    lista = tmp_path / "lista.json"
    lista.write_text("[]", encoding="utf-8")
    assert contratos_v4_pinados(lista) is False
    errado = tmp_path / "errado.json"
    errado.write_text(
        json.dumps({LOCK_KEY_DETALHAMENTO: "x", LOCK_KEY_SEGUNDA_VIA: {"artifacts": "y"}}), encoding="utf-8"
    )
    assert contratos_v4_pinados(errado) is False


def test_o_pin_real_do_repositorio_e_lido_sem_levantar() -> None:
    raiz = Path(__file__).resolve().parents[3]
    pin = raiz / "config/integrations/amh/contracts.lock.json"
    assert isinstance(contratos_v4_pinados(pin), bool)
    # sem a chave, o sinal nunca liga, qualquer que seja o pin
    assert fonte_detalhamento_habilitada(chave_ligada=False, pin_path=pin) is False
