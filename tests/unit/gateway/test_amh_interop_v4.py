"""Executor real do Lucas v4 (`AmhV4Executor`, DL-0114): detalhamento do pagador e 2a via, sem rede
(`httpx.MockTransport`).

Prende as decisoes do dono de 15-16/10/2026 e do DPO para o dev: as duas rotas passam pelo MESMO caminho
governado dos outros
executores (gate em sombra, Zona Geral, auditoria duravel ANTES do despacho, sem retry/redirect, recusa
fechada); a 2a via
(credencial de pagamento) usa o token do cliente DEDICADO (`interop/billing.boleto`), leva o
`X-Amh-Request-Ref` opaco, exige
`Cache-Control: no-store` na resposta e NUNCA deixa o corpo (o codigo) em log, auditoria nem repr. Os
codigos sao SINTETICOS.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import httpx
import pytest
import structlog

from maezo.agents.lucas.fonte_v4 import (
    ARTIFACT_DETALHAMENTO,
    ARTIFACT_SEGUNDA_VIA,
    OPERATION_DETALHAMENTO,
    OPERATION_SEGUNDA_VIA,
    AdaptadorDetalhamento,
    AdaptadorSegundaVia,
    PedidoGovernado,
)
from maezo.gateway.amh_interop import (
    SCOPE_BY_OPERATION,
    SCOPE_SEGUNDA_VIA,
    AmhInteropCompositionError,
    AmhV4Executor,
    CognitoClientCredentials,
    conferir_artefatos_v4,
)
from maezo.gateway.tool_registry import build_amh_interop
from maezo.ports.errors import PortFailureReason as Reason
from tests.unit.gateway.test_amh_interop import (
    ACCESS_TOKEN,
    SECRET,
    TOKEN_URL,
    Audit,
    Servidor,
    _comum,
    _seam,
    _settings,
)
from tests.unit.gateway.test_amh_interop import relogio as relogio  # noqa: F401  (fixture)
from tests.unit.gateway.test_amh_interop import servidor as servidor  # noqa: F401  (fixture)
from tests.unit.gateway.test_amh_interop import tokens as tokens  # noqa: F401  (fixture)

pytestmark = pytest.mark.anyio

_REPO = Path(__file__).resolve().parents[3]
_OPENAPI = _REPO / "config" / "integrations" / "amh" / "openapi"
REF = "amh:psr:v1:1b2f3a4c-5d6e-4f70-8a9b-0c1d2e3f4a5b"
PATH_STATUS = f"/interop/billing-status/v1/subjects/{REF.replace(':', '%3A')}/billing/status"
PATH_2VIA = f"/interop/billing-status/v1/subjects/{REF.replace(':', '%3A')}/billing/segunda-via"
ATENDIMENTO = "atendimento_whatsapp"
SOLICITACAO = "req_7f3a9c21d4"
BASE_LEGAL = "base-legal:execucao-de-contrato"
TOKEN_BOLETO = "eyJ.token.DO.BOLETO.SINTETICO"
SECRET_BOLETO = "segredo-do-cliente-do-boleto-SINTETICO"
LINHA = "00191.23454 67890.123457 67890.123457 7 10000000012345"
CODIGO_44 = "00197100000000123451234567890123456789012345"


class ServidorV4(Servidor):
    """O mesmo `Servidor`, com um token DIFERENTE para o cliente da 2a via (e o Cache-Control como o
    contrato manda)."""

    def __init__(self) -> None:
        super().__init__()
        self.no_store = True
        self.corpo_2via: dict[str, Any] = {
            "portable_subject_ref": REF,
            "boletos": [
                {
                    "competencia": "2026-10",
                    "vencimento": "2026-10-10",
                    "situacao": "vencida",
                    "valor_titulo": "123.45",
                    "disponivel": True,
                    "indisponivel_motivo": None,
                    "linha_digitavel": LINHA,
                    "codigo_barras": CODIGO_44,
                }
            ],
            "pdf": {"disponivel": False, "orientacao": "pelo_aplicativo"},
            "fonte_atualizada_em": "2026-10-15T12:00:00Z",
        }

    def _responder(self, request: httpx.Request) -> httpx.Response:
        if str(request.url) == TOKEN_URL and b"billing.boleto" in request.content:
            self.pedidos.append(request)
            return httpx.Response(
                200, json={"access_token": TOKEN_BOLETO, "token_type": "Bearer", "expires_in": 3600}
            )
        if request.url.path.endswith("/billing/segunda-via") and request.url.path not in self.respostas:
            self.pedidos.append(request)
            cabecalhos = {"cache-control": "no-store"} if self.no_store else {}
            return httpx.Response(200, json=self.corpo_2via, headers=cabecalhos)
        return super()._responder(request)


@pytest.fixture
def servidor_v4() -> ServidorV4:
    return ServidorV4()


@pytest.fixture
def tokens_boleto(servidor_v4: ServidorV4) -> CognitoClientCredentials:
    return CognitoClientCredentials(
        token_url=TOKEN_URL,
        client_id="maezo-operadora-interop-boleto",
        client_secret=SECRET_BOLETO,
        scopes=(SCOPE_SEGUNDA_VIA,),
        transport_factory=servidor_v4.factory,
    )


@pytest.fixture
def tokens_cobranca(servidor_v4: ServidorV4) -> CognitoClientCredentials:
    return CognitoClientCredentials(
        token_url=TOKEN_URL,
        client_id="maezo-operadora-interop",
        client_secret=SECRET,
        scopes=("interop/billing.read", "interop/subject.resolve", "interop/profile.read"),
        transport_factory=servidor_v4.factory,
    )


def _executor(
    servidor_v4: ServidorV4, tokens_cobranca: Any, tokens_boleto: Any, audit: Audit, **sobre: Any
) -> AmhV4Executor:
    comum = _comum(servidor_v4, tokens_cobranca, audit, purpose_of_use=ATENDIMENTO, **sobre)
    return AmhV4Executor(tokens_segunda_via=tokens_boleto, **comum)


def _detalhamento(**sobre: Any) -> PedidoGovernado:
    base: dict[str, Any] = {
        "operation": OPERATION_DETALHAMENTO,
        "path": PATH_STATUS,
        "query": (
            ("purpose_of_use", ATENDIMENTO),
            ("incluir_contratacao", "true"),
            ("incluir_detalhamento", "true"),
            ("somente_pagador", "true"),
        ),
        "consent_decision_ref": BASE_LEGAL,
        "request_ref": None,
        "timeout_seconds": 5.0,
    }
    base.update(sobre)
    return PedidoGovernado(**base)


def _segunda_via(**sobre: Any) -> PedidoGovernado:
    base: dict[str, Any] = {
        "operation": OPERATION_SEGUNDA_VIA,
        "path": PATH_2VIA,
        "query": (("purpose_of_use", ATENDIMENTO),),
        "consent_decision_ref": None,
        "request_ref": SOLICITACAO,
        "timeout_seconds": 5.0,
    }
    base.update(sobre)
    return PedidoGovernado(**base)


# --- escopos e registro ---------------------------------------------------------------------------------- --
def test_as_duas_operacoes_estao_registradas_e_pedem_o_escopo_certo() -> None:
    assert AmhV4Executor.registered_operations == frozenset({OPERATION_DETALHAMENTO, OPERATION_SEGUNDA_VIA})
    assert SCOPE_BY_OPERATION[OPERATION_DETALHAMENTO] == "interop/billing.read"
    assert SCOPE_BY_OPERATION[OPERATION_SEGUNDA_VIA] == SCOPE_SEGUNDA_VIA == "interop/billing.boleto"


async def test_o_token_da_2a_via_so_pode_ter_o_escopo_do_boleto(
    servidor_v4: ServidorV4, tokens_cobranca: Any
) -> None:
    com_escopo_demais = CognitoClientCredentials(
        token_url=TOKEN_URL,
        client_id="c",
        client_secret="s",
        scopes=(SCOPE_SEGUNDA_VIA, "interop/billing.read"),
        transport_factory=servidor_v4.factory,
    )
    with pytest.raises(AmhInteropCompositionError):
        _executor(servidor_v4, tokens_cobranca, com_escopo_demais, Audit())


def test_finalidade_diferente_de_atendimento_recusa_a_composicao(
    servidor_v4: ServidorV4, tokens_cobranca: Any, tokens_boleto: Any
) -> None:
    comum = _comum(servidor_v4, tokens_cobranca, Audit(), purpose_of_use="sharing_amh_internal")
    with pytest.raises(AmhInteropCompositionError):
        AmhV4Executor(tokens_segunda_via=tokens_boleto, **comum)


# --- detalhamento ---------------------------------------------------------------------------------------- --
async def test_detalhamento_audita_antes_e_usa_o_token_de_cobranca(
    servidor_v4: ServidorV4, tokens_cobranca: Any, tokens_boleto: Any
) -> None:
    audit = Audit(servidor_v4)
    resultado = await _executor(servidor_v4, tokens_cobranca, tokens_boleto, audit).execute(_detalhamento())
    assert resultado.succeeded, resultado.failure
    [pedido] = servidor_v4.ao_servico()
    assert pedido.method == "GET" and pedido.url.path.endswith("/billing/status")
    query = dict(pedido.url.params)
    assert query["purpose_of_use"] == ATENDIMENTO
    assert query["incluir_detalhamento"] == "true" and query["somente_pagador"] == "true"
    assert pedido.headers["authorization"] == f"Bearer {ACCESS_TOKEN}"
    assert "x-amh-request-ref" not in pedido.headers
    assert audit.pedidos_no_emit == [0]  # auditoria ANTES do despacho
    [registro] = audit.registros
    assert registro.action == OPERATION_DETALHAMENTO and registro.agent_id == "lucas"
    assert "base-legal" not in json.dumps(registro.details) and "request_ref_sha256" not in registro.details


@pytest.mark.parametrize(
    "sobre",
    [
        {
            "query": (("purpose_of_use", ATENDIMENTO), ("incluir_detalhamento", "true"))
        },  # sem `somente_pagador`
        {
            "query": (("purpose_of_use", ATENDIMENTO), ("somente_pagador", "true"))
        },  # sem `incluir_detalhamento`
        {
            "query": (
                ("purpose_of_use", ATENDIMENTO),
                ("incluir_detalhamento", "true"),
                ("somente_pagador", "false"),
            )
        },
        {
            "query": (
                ("purpose_of_use", "sharing_amh_internal"),
                ("incluir_detalhamento", "true"),
                ("somente_pagador", "true"),
            )
        },
        {"path": PATH_2VIA},  # rota de outra operacao
        {"path": "/interop/billing-status/v1/subjects/../admin/billing/status"},
        {"consent_decision_ref": " "},
        {"consent_decision_ref": None},
        {"request_ref": SOLICITACAO},  # o detalhamento nao leva id de solicitacao
        {"timeout_seconds": 999.0},
        {"operation": "amh.get_billing_status"},
    ],
)
async def test_pedido_do_detalhamento_fora_da_forma_nunca_e_despachado(
    servidor_v4: ServidorV4, tokens_cobranca: Any, tokens_boleto: Any, sobre: dict[str, Any]
) -> None:
    audit = Audit()
    resultado = await _executor(servidor_v4, tokens_cobranca, tokens_boleto, audit).execute(
        _detalhamento(**sobre)
    )
    assert resultado.failure is not None and resultado.failure.reason is Reason.INVALID_REQUEST
    assert servidor_v4.ao_servico() == [] and audit.registros == []


# --- 2a via ---------------------------------------------------------------------------------------------- --
async def test_segunda_via_usa_o_token_do_cliente_dedicado_e_o_request_ref(
    servidor_v4: ServidorV4, tokens_cobranca: Any, tokens_boleto: Any
) -> None:
    audit = Audit(servidor_v4)
    resultado = await _executor(servidor_v4, tokens_cobranca, tokens_boleto, audit).execute(_segunda_via())
    assert resultado.succeeded, resultado.failure
    [pedido] = servidor_v4.ao_servico()
    assert pedido.url.path.endswith("/billing/segunda-via")
    assert dict(pedido.url.params) == {"purpose_of_use": ATENDIMENTO}
    assert pedido.headers["authorization"] == f"Bearer {TOKEN_BOLETO}"  # NUNCA o token de cobranca
    assert pedido.headers["x-amh-request-ref"] == SOLICITACAO
    assert audit.pedidos_no_emit == [0]
    # O pedido de token do cliente da 2a via carrega SO' o escopo do boleto.
    pedidos_token = [p for p in servidor_v4.pedidos if str(p.url) == TOKEN_URL]
    assert len(pedidos_token) == 1 and b"interop%2Fbilling.boleto" in pedidos_token[0].content
    assert b"billing.read" not in pedidos_token[0].content


async def test_segunda_via_a_auditoria_nunca_tem_o_codigo_nem_o_corpo(
    servidor_v4: ServidorV4, tokens_cobranca: Any, tokens_boleto: Any
) -> None:
    audit = Audit()
    resultado = await _executor(servidor_v4, tokens_cobranca, tokens_boleto, audit).execute(_segunda_via())
    assert resultado.succeeded
    [registro] = audit.registros
    texto = json.dumps(registro.details)
    for proibido in (LINHA, CODIGO_44, "123.45", SOLICITACAO, REF, TOKEN_BOLETO):
        assert proibido not in texto
    assert registro.action == OPERATION_SEGUNDA_VIA and "request_ref_sha256" in registro.details


async def test_o_codigo_de_barras_nunca_vai_para_log_nem_repr(
    servidor_v4: ServidorV4, tokens_cobranca: Any, tokens_boleto: Any, caplog: pytest.LogCaptureFixture
) -> None:
    with structlog.testing.capture_logs() as estruturado, caplog.at_level(logging.INFO):
        resultado = await _executor(servidor_v4, tokens_cobranca, tokens_boleto, Audit()).execute(
            _segunda_via()
        )
    assert resultado.succeeded and resultado.value is not None
    for texto in (
        repr(estruturado),
        caplog.text,
        repr(resultado),
        repr(resultado.value),
        repr(tokens_boleto),
    ):
        for proibido in (LINHA, CODIGO_44, TOKEN_BOLETO, SECRET_BOLETO):
            assert proibido not in texto
    assert LINHA.encode() in resultado.value.body  # o corpo chega ao adaptador (e so' la)


async def test_com_o_httpcore_em_debug_a_2a_via_e_recusada_antes_de_qualquer_trafego(
    servidor_v4: ServidorV4, tokens_cobranca: Any, tokens_boleto: Any, caplog: pytest.LogCaptureFixture
) -> None:
    """Os traces DEBUG do httpcore trazem cabecalhos crus (`Authorization`): o executor recusa em vez de
    vaza-los."""
    with caplog.at_level(logging.DEBUG):
        resultado = await _executor(servidor_v4, tokens_cobranca, tokens_boleto, Audit()).execute(
            _segunda_via()
        )
    assert resultado.failure is not None and resultado.failure.reason is Reason.NOT_AUTHENTICATED
    assert servidor_v4.ao_servico() == []
    for proibido in (LINHA, CODIGO_44, TOKEN_BOLETO, SECRET_BOLETO):
        assert proibido not in caplog.text


async def test_segunda_via_sem_no_store_na_resposta_e_recusada_como_violacao_de_contrato(
    servidor_v4: ServidorV4, tokens_cobranca: Any, tokens_boleto: Any
) -> None:
    servidor_v4.no_store = False
    resultado = await _executor(servidor_v4, tokens_cobranca, tokens_boleto, Audit()).execute(_segunda_via())
    assert resultado.failure is not None and resultado.failure.reason is Reason.CONTRACT_VIOLATION


@pytest.mark.parametrize(
    "sobre",
    [
        {"request_ref": None},
        {"request_ref": "curto"},
        {"request_ref": "tem espaco no meio 1"},
        {"consent_decision_ref": BASE_LEGAL},  # a 2a via nao leva consentimento
        {"query": (("purpose_of_use", ATENDIMENTO), ("incluir_detalhamento", "true"))},
        {"query": (("purpose_of_use", ATENDIMENTO), ("competencia", "2026-13"))},
        {"path": PATH_STATUS},
    ],
)
async def test_pedido_da_2a_via_fora_da_forma_nunca_e_despachado(
    servidor_v4: ServidorV4, tokens_cobranca: Any, tokens_boleto: Any, sobre: dict[str, Any]
) -> None:
    audit = Audit()
    resultado = await _executor(servidor_v4, tokens_cobranca, tokens_boleto, audit).execute(
        _segunda_via(**sobre)
    )
    assert resultado.failure is not None and resultado.failure.reason is Reason.INVALID_REQUEST
    assert servidor_v4.ao_servico() == [] and audit.registros == []


async def test_competencia_valida_vai_na_query_da_2a_via(
    servidor_v4: ServidorV4, tokens_cobranca: Any, tokens_boleto: Any
) -> None:
    pedido = _segunda_via(query=(("purpose_of_use", ATENDIMENTO), ("competencia", "2026-10")))
    assert (await _executor(servidor_v4, tokens_cobranca, tokens_boleto, Audit()).execute(pedido)).succeeded
    assert dict(servidor_v4.ao_servico()[0].url.params)["competencia"] == "2026-10"


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        (404, Reason.NOT_FOUND),  # nao pagador / sem boleto / rota desligada: o mesmo 404
        (429, Reason.RATE_LIMITED),
        (500, Reason.UPSTREAM_UNAVAILABLE),
        (401, Reason.NOT_AUTHENTICATED),
    ],
)
async def test_status_da_2a_via_vira_recusa_fechada_sem_vazar_corpo(
    servidor_v4: ServidorV4, tokens_cobranca: Any, tokens_boleto: Any, status: int, reason: Reason
) -> None:
    servidor_v4.respostas[PATH_2VIA.replace("%3A", ":")] = httpx.Response(status, json={"detalhe": LINHA})
    resultado = await _executor(servidor_v4, tokens_cobranca, tokens_boleto, Audit()).execute(_segunda_via())
    assert resultado.failure is not None and resultado.failure.reason is reason
    assert LINHA not in repr(resultado)


async def test_401_da_2a_via_invalida_so_o_token_do_boleto(
    servidor_v4: ServidorV4, tokens_cobranca: Any, tokens_boleto: Any
) -> None:
    servidor_v4.respostas[PATH_2VIA.replace("%3A", ":")] = httpx.Response(401)
    executor = _executor(servidor_v4, tokens_cobranca, tokens_boleto, Audit())
    assert await tokens_cobranca.token() == ACCESS_TOKEN
    await executor.execute(_segunda_via())
    pedidos_token_antes = len([p for p in servidor_v4.pedidos if str(p.url) == TOKEN_URL])
    await tokens_cobranca.token()  # cache do token de cobranca intacto: nenhum POST novo
    assert len([p for p in servidor_v4.pedidos if str(p.url) == TOKEN_URL]) == pedidos_token_antes


async def test_sem_token_do_boleto_a_chamada_nem_chega_ao_servico(
    servidor_v4: ServidorV4, tokens_cobranca: Any, tokens_boleto: Any
) -> None:
    servidor_v4.token_status = 500
    # o `ServidorV4` responde o token do boleto sozinho: derruba-o trocando o escopo do pedido
    await tokens_boleto.aclose()
    resultado = await _executor(servidor_v4, tokens_cobranca, tokens_boleto, Audit()).execute(_segunda_via())
    assert resultado.failure is not None and resultado.failure.reason is Reason.NOT_AUTHENTICATED
    assert servidor_v4.ao_servico() == []


# --- de ponta a ponta com os adaptadores do Lucas (a forma do contrato e' conferida campo a campo) ------- --
async def test_a_2a_via_chega_ao_adaptador_do_lucas_com_o_codigo(
    servidor_v4: ServidorV4, tokens_cobranca: Any, tokens_boleto: Any
) -> None:
    adaptador = AdaptadorSegundaVia(_executor(servidor_v4, tokens_cobranca, tokens_boleto, Audit()))
    leitura = await adaptador.get_segunda_via(REF, purpose_of_use=ATENDIMENTO, request_ref=SOLICITACAO)
    assert leitura.succeeded and leitura.value is not None
    [boleto] = leitura.value.boletos
    assert boleto.disponivel and boleto.linha_digitavel == LINHA and boleto.codigo_barras == CODIGO_44
    assert LINHA not in repr(leitura.value) and CODIGO_44 not in repr(leitura.value)


async def test_o_detalhamento_do_nao_pagador_chega_sem_valor(
    servidor_v4: ServidorV4, tokens_cobranca: Any, tokens_boleto: Any
) -> None:
    servidor_v4.respostas[PATH_STATUS.replace("%3A", ":")] = httpx.Response(
        200,
        json={
            "portable_subject_ref": REF,
            "resumo": {"responsavel_financeiro": "outra_pessoa", "financeiro_liberado": False},
            "competencias": [],
        },
    )
    adaptador = AdaptadorDetalhamento(_executor(servidor_v4, tokens_cobranca, tokens_boleto, Audit()))
    leitura = await adaptador.get_billing_detalhamento(
        REF, purpose_of_use=ATENDIMENTO, consent_decision_ref=BASE_LEGAL
    )
    assert leitura.succeeded and leitura.value is not None
    assert leitura.value.financeiro_liberado is False and leitura.value.competencias == ()


# --- composicao ------------------------------------------------------------------------------------------ --
def _settings_v4(tmp_path: Path, **sobre: Any) -> Any:
    base = {
        "amh_billing_status_detalhamento_openapi_path": str(
            _OPENAPI / "billing-status-detalhamento.openapi.yaml"
        ),
        "amh_billing_segunda_via_openapi_path": str(_OPENAPI / "billing-segunda-via.openapi.yaml"),
        "amh_interop_boleto_client_id": "maezo-operadora-interop-boleto",
        "amh_interop_boleto_client_secret": SECRET_BOLETO,
    }
    base.update(sobre)
    return _settings(tmp_path, **base)


def _sem_adaptadores_reais(monkeypatch: pytest.MonkeyPatch) -> None:
    from maezo.adapters.amh import billing_status as bs
    from maezo.adapters.amh import subject_resolution as sr

    class _Adapter:
        def __init__(self, *_: Any, **__: Any) -> None:
            pass

    monkeypatch.setattr(bs, "AmhBillingStatusAdapter", _Adapter)
    monkeypatch.setattr(sr, "AmhSubjectResolutionAdapter", _Adapter)


def test_os_openapi_vendorizados_batem_com_o_pin() -> None:
    conferir_artefatos_v4(
        detalhamento=(_OPENAPI / "billing-status-detalhamento.openapi.yaml").read_bytes(),
        segunda_via=(_OPENAPI / "billing-segunda-via.openapi.yaml").read_bytes(),
    )
    assert ARTIFACT_DETALHAMENTO.endswith("billing-status-detalhamento.openapi.yaml")
    assert ARTIFACT_SEGUNDA_VIA.endswith("billing-segunda-via.openapi.yaml")


def test_bytes_fora_do_digest_do_pin_recusam_sem_ecoar_nada() -> None:
    bom = (_OPENAPI / "billing-segunda-via.openapi.yaml").read_bytes()
    det = (_OPENAPI / "billing-status-detalhamento.openapi.yaml").read_bytes()
    for detalhamento, segunda_via in ((det + b"\n#", bom), (det, bom + b" "), (bom, det)):
        with pytest.raises(AmhInteropCompositionError) as exc:
            conferir_artefatos_v4(detalhamento=detalhamento, segunda_via=segunda_via)
        assert str(exc.value) == "amh_interop_v4_artefato_fora_do_pin"


def test_composicao_v4_sobe_com_o_executor_e_so_o_escopo_do_boleto_no_segundo_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _sem_adaptadores_reais(monkeypatch)
    interop = build_amh_interop(
        settings=_settings_v4(tmp_path),
        seam=_seam(),
        audit=Audit(),
        agent_version="lucas@v0",
        incluir_v4=True,
    )
    assert isinstance(interop.executor_v4, AmhV4Executor)
    segundo_token = interop.executor_v4._tokens_segunda_via  # type: ignore[attr-defined]
    assert segundo_token.scopes == (SCOPE_SEGUNDA_VIA,)
    assert interop.executor_v4 in interop._executors


def test_composicao_sem_v4_nao_tem_executor_nem_le_nada_do_v4(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _sem_adaptadores_reais(monkeypatch)
    interop = build_amh_interop(
        settings=_settings(tmp_path), seam=_seam(), audit=Audit(), agent_version="lucas@v0"
    )
    assert interop.executor_v4 is None and len(interop._executors) == 2


@pytest.mark.parametrize(
    "ausente",
    [
        "amh_billing_status_detalhamento_openapi_path",
        "amh_billing_segunda_via_openapi_path",
        "amh_interop_boleto_client_id",
        "amh_interop_boleto_client_secret",
    ],
)
def test_composicao_v4_sem_peca_recusa_com_o_nome_e_sem_valor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ausente: str
) -> None:
    _sem_adaptadores_reais(monkeypatch)
    with pytest.raises(AmhInteropCompositionError) as exc:
        build_amh_interop(
            settings=_settings_v4(tmp_path, **{ausente: None}),
            seam=_seam(),
            audit=Audit(),
            agent_version="lucas@v0",
            incluir_v4=True,
        )
    assert ausente in str(exc.value)
    assert SECRET not in str(exc.value) and SECRET_BOLETO not in str(exc.value)


def test_composicao_v4_sem_o_escopo_de_cobranca_recusa(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _sem_adaptadores_reais(monkeypatch)
    with pytest.raises(AmhInteropCompositionError, match="escopos"):
        build_amh_interop(
            settings=_settings_v4(
                tmp_path, amh_interop_scopes="interop/subject.resolve interop/profile.read"
            ),
            seam=_seam(),
            audit=Audit(),
            agent_version="lucas@v0",
            incluir_v4=True,
        )


def test_composicao_v4_com_openapi_fora_do_pin_recusa(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _sem_adaptadores_reais(monkeypatch)
    adulterado = tmp_path / "det.yaml"
    adulterado.write_bytes((_OPENAPI / "billing-status-detalhamento.openapi.yaml").read_bytes() + b"\n# x")
    with pytest.raises(AmhInteropCompositionError, match="fora_do_pin"):
        build_amh_interop(
            settings=_settings_v4(tmp_path, amh_billing_status_detalhamento_openapi_path=str(adulterado)),
            seam=_seam(),
            audit=Audit(),
            agent_version="lucas@v0",
            incluir_v4=True,
        )


async def test_aclose_fecha_o_token_da_2a_via(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _sem_adaptadores_reais(monkeypatch)
    interop = build_amh_interop(
        settings=_settings_v4(tmp_path),
        seam=_seam(),
        audit=Audit(),
        agent_version="lucas@v0",
        incluir_v4=True,
    )
    await interop.aclose()
    assert await interop.executor_v4._tokens_segunda_via.token() is None  # type: ignore[union-attr]


def test_o_segredo_do_boleto_so_chega_pelo_cofre_do_agente() -> None:
    from maezo.gateway.tool_registry import AGENT_CREDENTIAL_FIELDS

    assert AGENT_CREDENTIAL_FIELDS["amh_interop_boleto_client_secret"] == "amh_interop_boleto_client_secret"
    from maezo.platform.webhooks.whatsapp.settings import WhatsAppWebhookSettings

    campo = WhatsAppWebhookSettings.model_fields["amh_interop_boleto_client_secret"]
    assert campo.repr is False and campo.exclude is True
    assert "MAEZO_AMH_INTEROP_BOLETO_CLIENT_SECRET" in str(campo.validation_alias)
