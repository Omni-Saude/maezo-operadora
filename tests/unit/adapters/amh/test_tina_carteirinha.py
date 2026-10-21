"""Carteirinha COMPLETA (contrato `tina-carteirinha`, manifest aditivo v1.12; decisao do dono de 17/10/2026).

O artefato ainda NAO esta' pinado (a AMH publica o v1.12 e o pin XRG-3 vem depois): estes testes usam um
OpenAPI SINTETICO com a MESMA forma do contrato (rota `/subjects/{ref}/carteirinha`, `purpose_of_use` fixo,
cabecalho `X-Amh-Request-Ref`, schema `Carteirinhas`) e um pin de teste com o digest dele. O que fica preso:

  * sem os bytes, `get_carteirinha` recusa `SCOPE_NOT_SUPPORTED` e o adaptador de sempre nao muda;
  * com os bytes e SEM o digest no pin (o lock real de hoje), o construtor RECUSA (fail-closed);
  * o pedido leva o id opaco no cabecalho (nunca na query), a finalidade fixa e a rota do proprio sujeito;
  * resposta de outro sujeito, fora do schema ou 404 vira recusa fechada; o numero nao aparece no `repr`;
  * o executor do gateway exige finalidade `atendimento_whatsapp`, o escopo proprio, o id opaco no padrao do
    contrato e `Cache-Control: no-store` na resposta 200.

Numeros 100% sinteticos.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml

from maezo.adapters.amh import tina
from maezo.adapters.amh.contract import load_contract_pin
from maezo.gateway.amh_interop import (
    SCOPE_BY_OPERATION,
    SCOPE_TINA_CARTEIRINHA,
    AmhInteropCompositionError,
    AmhTinaCarteirinhaExecutor,
    AmhTinaExecutor,
    CognitoClientCredentials,
)
from maezo.ports.errors import PortFailureReason as Reason
from maezo.ports.errors import PortResult
from maezo.ports.tina import CarteirinhasView, TinaCarteirinhaPort
from tests.unit.adapters.amh.test_tina import Executor, _openapi
from tests.unit.gateway.test_amh_interop import (
    ORIGIN,
    SECRET,
    TOKEN_URL,
    Audit,
    Servidor,
    _comum,
)
from tests.unit.gateway.test_amh_interop import relogio as relogio  # noqa: F401  (fixture)
from tests.unit.gateway.test_amh_interop import servidor as servidor  # noqa: F401  (fixture)

pytestmark = pytest.mark.anyio

REF = "subject1"
NUMERO = "900000000000017"
MASCARA = "***********0017"
REQUEST_REF = "hk1_msg_carteirinha_01"


def _openapi_carteirinha() -> bytes:
    """A MESMA forma do `tina-carteirinha.openapi.yaml` da AMH, com o sujeito sintetico dos testes TINA."""
    documento = {
        "openapi": "3.0.3",
        "info": {"title": "sintetico", "version": "0.1.0"},
        "servers": [{"url": tina.SERVER}],
        "paths": {
            "/subjects/{portable_subject_ref}/carteirinha": {
                "get": {
                    "parameters": [
                        {"$ref": "#/components/parameters/PortableSubjectRef"},
                        {"$ref": "#/components/parameters/PurposeOfUse"},
                        {"$ref": "#/components/parameters/RequestRef"},
                    ],
                    "responses": {
                        "200": {
                            "content": {
                                "application/json": {"schema": {"$ref": "#/components/schemas/Carteirinhas"}}
                            }
                        }
                    },
                }
            }
        },
        "components": {
            "parameters": {
                "PortableSubjectRef": {
                    "name": "portable_subject_ref",
                    "in": "path",
                    "required": True,
                    "schema": {"type": "string", "pattern": "^subject[0-9]+$"},
                },
                "PurposeOfUse": {
                    "name": "purpose_of_use",
                    "in": "query",
                    "required": True,
                    "schema": {"type": "string", "enum": ["atendimento_whatsapp"]},
                },
                "RequestRef": {
                    "name": "X-Amh-Request-Ref",
                    "in": "header",
                    "required": True,
                    "schema": {"type": "string", "pattern": "^[A-Za-z0-9_.:-]{8,64}$"},
                },
            },
            "schemas": {
                "Carteirinhas": {
                    "type": "object",
                    "required": ["portable_subject_ref", "carteirinhas", "fonte_atualizada_em"],
                    "additionalProperties": False,
                    "properties": {
                        "portable_subject_ref": {"type": "string"},
                        "carteirinhas": {
                            "type": "array",
                            "minItems": 1,
                            "maxItems": 10,
                            "items": {"$ref": "#/components/schemas/Carteirinha"},
                        },
                        "fonte_atualizada_em": {"type": "string", "format": "date-time"},
                    },
                },
                "Carteirinha": {
                    "type": "object",
                    "required": ["carteirinha_numero", "carteirinha_mascarada", "vinculo_principal"],
                    "additionalProperties": False,
                    "properties": {
                        "carteirinha_numero": {"type": "string", "pattern": "^[0-9A-Za-z]{8,24}$"},
                        "carteirinha_mascarada": {"type": "string", "pattern": "^\\*{4,20}[0-9A-Za-z]{0,4}$"},
                        "vinculo_principal": {"type": "boolean"},
                    },
                },
            },
        },
    }
    return yaml.safe_dump(documento, sort_keys=False).encode()


CARTEIRINHA_BYTES = _openapi_carteirinha()


def _corpo(**sobre: Any) -> dict[str, Any]:
    corpo: dict[str, Any] = {
        "portable_subject_ref": REF,
        "carteirinhas": [
            {"carteirinha_numero": NUMERO, "carteirinha_mascarada": MASCARA, "vinculo_principal": True}
        ],
        "fonte_atualizada_em": "2026-10-17T12:00:00Z",
    }
    corpo.update(sobre)
    return corpo


@pytest.fixture
def pin_com_carteirinha(monkeypatch: pytest.MonkeyPatch) -> None:
    """O pin de TESTE: o lock real + os digests dos OpenAPI SINTETICOS deste arquivo (o real esta' pinado no
    bloco `manifest_v1_12`)."""
    real = load_contract_pin()
    digests = {
        **real.artifact_digests,
        tina.ARTIFACT: hashlib.sha256(_openapi()).hexdigest(),
        tina.ARTIFACT_CARTEIRINHA: hashlib.sha256(CARTEIRINHA_BYTES).hexdigest(),
    }
    monkeypatch.setattr(tina, "load_contract_pin", lambda _p=None: replace(real, artifact_digests=digests))


def _montar(corpo: dict[str, Any] | None = None) -> tuple[tina.AmhTinaAdapter, Executor]:
    executor = Executor()
    executor.bodies[tina.OP_CARTEIRINHA] = corpo if corpo is not None else _corpo()
    adapter = tina.AmhTinaAdapter(_openapi(), executor=executor, carteirinha_openapi_bytes=CARTEIRINHA_BYTES)
    return adapter, executor


async def _ler(adapter: tina.AmhTinaAdapter, **sobre: Any) -> PortResult[CarteirinhasView]:
    args: dict[str, Any] = {
        "purpose_of_use": tina.PURPOSE_CARTEIRINHA,
        "consent_decision_ref": "base-legal:execucao-de-contrato",
        "request_ref": REQUEST_REF,
    }
    args.update(sobre)
    return await adapter.get_carteirinha(REF, **args)


def test_o_lock_real_tem_o_digest_do_v1_12_e_o_construtor_aceita_os_bytes_pinados() -> None:
    """Pin XRG-3 do v1.12 (17/10/2026): digest do lock real = o dos bytes pinados; o adaptador sobe."""
    pin = load_contract_pin()
    vendorizados = Path(__file__).resolve().parents[4] / "config/integrations/amh/openapi"
    pinados = (vendorizados / "tina-carteirinha.openapi.yaml").read_bytes()
    tina_v1_2 = (vendorizados / "tina.openapi.yaml").read_bytes()
    assert pin.manifest_v1_12_digest == "c9088c0f1dbb1c9720ba51674f85076e81936c5d862a8d8cca203647f76035a6"
    assert pin.artifact_digests[tina.ARTIFACT_CARTEIRINHA] == hashlib.sha256(pinados).hexdigest()
    tina.AmhTinaAdapter(tina_v1_2, executor=Executor(), carteirinha_openapi_bytes=pinados)
    with pytest.raises(tina.TinaContractError):
        tina.AmhTinaAdapter(tina_v1_2, executor=Executor(), carteirinha_openapi_bytes=CARTEIRINHA_BYTES)


async def test_sem_os_bytes_a_carteirinha_recusa_e_o_adaptador_de_sempre_nao_muda(
    pin_com_carteirinha: None,
) -> None:
    executor = Executor()
    adapter = tina.AmhTinaAdapter(_openapi(), executor=executor)
    assert isinstance(adapter, TinaCarteirinhaPort)
    resultado = await _ler(adapter)
    assert resultado.failure is not None and resultado.failure.reason is Reason.SCOPE_NOT_SUPPORTED
    assert executor.calls == []


async def test_o_pedido_leva_o_id_opaco_no_cabecalho_e_a_finalidade_fixa(pin_com_carteirinha: None) -> None:
    adapter, executor = _montar()
    resultado = await _ler(adapter)
    assert resultado.succeeded and resultado.value is not None
    [pedido] = executor.calls
    assert pedido.operation == tina.OP_CARTEIRINHA
    assert pedido.path == f"{tina.SERVER}/subjects/{REF}/carteirinha"
    assert pedido.query == (("purpose_of_use", "atendimento_whatsapp"),)
    assert pedido.request_ref == REQUEST_REF
    [item] = resultado.value.carteirinhas
    assert (item.carteirinha_numero, item.carteirinha_mascarada, item.vinculo_principal) == (
        NUMERO,
        MASCARA,
        True,
    )
    # O numero completo nunca aparece no `repr` (log por acidente) nem no do pedido.
    assert NUMERO not in repr(resultado.value) and NUMERO not in repr(item)


@pytest.mark.parametrize(
    "sobre",
    [
        {"request_ref": "curto"},  # fora do padrao do contrato
        {"request_ref": "id com espaco 123"},
        {"purpose_of_use": "sharing_amh_internal"},  # a rota so' aceita `atendimento_whatsapp`
        {"consent_decision_ref": " "},
    ],
)
async def test_pedido_fora_do_contrato_nunca_sai(pin_com_carteirinha: None, sobre: dict[str, Any]) -> None:
    adapter, executor = _montar()
    resultado = await _ler(adapter, **sobre)
    assert resultado.failure is not None and resultado.failure.reason is Reason.INVALID_REQUEST
    assert executor.calls == []


@pytest.mark.parametrize(
    "corpo",
    [
        _corpo(portable_subject_ref="subject2"),  # OUTRO sujeito: nunca um fato
        _corpo(carteirinhas=[]),  # o contrato exige 1..10
        _corpo(
            carteirinhas=[
                {"carteirinha_numero": "9" * 30, "carteirinha_mascarada": MASCARA, "vinculo_principal": True}
            ]
        ),
        _corpo(
            carteirinhas=[
                {"carteirinha_numero": NUMERO, "carteirinha_mascarada": "0017", "vinculo_principal": True}
            ]
        ),
        _corpo(extra="x"),
    ],
)
async def test_resposta_fora_do_contrato_vira_recusa_fechada(
    pin_com_carteirinha: None, corpo: dict[str, Any]
) -> None:
    adapter, _ = _montar(corpo)
    resultado = await _ler(adapter)
    assert resultado.failure is not None and resultado.failure.reason is Reason.CONTRACT_VIOLATION


async def test_404_sem_vinculo_ativo_vira_not_found(pin_com_carteirinha: None) -> None:
    adapter, executor = _montar()
    executor.status = 404
    resultado = await _ler(adapter)
    assert resultado.failure is not None and resultado.failure.reason is Reason.NOT_FOUND


async def test_um_executor_proprio_carrega_so_a_carteirinha(pin_com_carteirinha: None) -> None:
    principal = Executor()
    proprio = Executor()
    proprio.bodies[tina.OP_CARTEIRINHA] = _corpo()
    adapter = tina.AmhTinaAdapter(
        _openapi(),
        executor=principal,
        carteirinha_openapi_bytes=CARTEIRINHA_BYTES,
        carteirinha_executor=proprio,
    )
    assert (await _ler(adapter)).succeeded
    assert (
        await adapter.get_elegibilidade(REF, purpose_of_use="purpose1", consent_decision_ref="base-legal:x")
    ).succeeded
    assert [c.operation for c in proprio.calls] == [tina.OP_CARTEIRINHA]
    assert [c.operation for c in principal.calls] == [tina.OP_ELEGIBILIDADE]
    assert all(c.request_ref is None for c in principal.calls)


# --- executor real do gateway (sem rede) -------------------------------------------------------------

_PATH = "/interop/tina/v1/subjects/subject1/carteirinha"


def _tokens(servidor: Servidor, relogio: list[float], *escopos: str) -> CognitoClientCredentials:
    return CognitoClientCredentials(
        token_url=TOKEN_URL,
        client_id="maezo-operadora-interop",
        client_secret=SECRET,
        scopes=escopos or ("interop/tina.read", SCOPE_TINA_CARTEIRINHA),
        transport_factory=servidor.factory,
        clock=lambda: relogio[0],
    )


def _pedido(**sobre: Any) -> tina.GovernedTinaRequest:
    base: dict[str, Any] = {
        "operation": tina.OP_CARTEIRINHA,
        "path": _PATH,
        "query": (("purpose_of_use", "atendimento_whatsapp"),),
        "consent_decision_ref": "base-legal:execucao-de-contrato",
        "timeout_seconds": 5.0,
        "request_ref": REQUEST_REF,
    }
    base.update(sobre)
    return tina.GovernedTinaRequest(**base)


def test_escopo_proprio_e_composicao_so_com_a_finalidade_e_o_escopo(
    servidor: Servidor, relogio: list[float]
) -> None:
    assert SCOPE_BY_OPERATION[tina.OP_CARTEIRINHA] == "interop/tina.carteirinha"
    assert tina.OP_CARTEIRINHA not in AmhTinaExecutor.registered_operations
    assert AmhTinaCarteirinhaExecutor.registered_operations == frozenset({tina.OP_CARTEIRINHA})
    tokens = _tokens(servidor, relogio)
    with pytest.raises(AmhInteropCompositionError):  # finalidade de sempre: recusado
        AmhTinaCarteirinhaExecutor(**_comum(servidor, tokens, Audit()))
    with pytest.raises(AmhInteropCompositionError):  # token sem o escopo proprio: recusado
        AmhTinaCarteirinhaExecutor(
            **_comum(
                servidor,
                _tokens(servidor, relogio, "interop/tina.read"),
                Audit(),
                purpose_of_use="atendimento_whatsapp",
            )
        )
    AmhTinaCarteirinhaExecutor(**_comum(servidor, tokens, Audit(), purpose_of_use="atendimento_whatsapp"))


def _no_store(corpo: dict[str, Any], *, no_store: bool = True) -> httpx.Response:
    headers = {"content-type": "application/json"}
    if no_store:
        headers["cache-control"] = "no-store"
    return httpx.Response(200, headers=headers, content=json.dumps(corpo).encode())


async def test_executor_manda_o_id_opaco_no_cabecalho_e_audita_so_o_hash(
    servidor: Servidor, relogio: list[float]
) -> None:
    servidor.respostas[_PATH] = _no_store(_corpo())
    audit = Audit(servidor)
    executor = AmhTinaCarteirinhaExecutor(
        **_comum(servidor, _tokens(servidor, relogio), audit, purpose_of_use="atendimento_whatsapp")
    )
    resultado = await executor.execute(_pedido())
    assert resultado.succeeded, resultado.failure
    [pedido] = servidor.ao_servico()
    assert str(pedido.url) == ORIGIN + _PATH + "?purpose_of_use=atendimento_whatsapp"
    assert pedido.headers["X-Amh-Request-Ref"] == REQUEST_REF
    assert audit.pedidos_no_emit == [0]  # auditoria ANTES do despacho
    [registro] = audit.registros
    detalhes = json.dumps(registro.details)
    assert "request_ref_sha256" in registro.details and REQUEST_REF not in detalhes
    assert NUMERO not in detalhes
    token_req = next(p for p in servidor.pedidos if str(p.url) == TOKEN_URL)
    assert "interop%2Ftina.carteirinha" in token_req.content.decode()


async def test_executor_recusa_a_resposta_sem_no_store(servidor: Servidor, relogio: list[float]) -> None:
    servidor.respostas[_PATH] = _no_store(_corpo(), no_store=False)
    executor = AmhTinaCarteirinhaExecutor(
        **_comum(servidor, _tokens(servidor, relogio), Audit(), purpose_of_use="atendimento_whatsapp")
    )
    resultado = await executor.execute(_pedido())
    assert resultado.failure is not None and resultado.failure.reason is Reason.CONTRACT_VIOLATION


@pytest.mark.parametrize(
    "sobre",
    [
        {"request_ref": None},
        {"request_ref": "curto"},
        {"query": (("purpose_of_use", "sharing_amh_internal"),)},
        {"path": "/interop/tina/v1/subjects/subject1/elegibilidade"},
        {"operation": tina.OP_ELEGIBILIDADE},
    ],
)
async def test_executor_nunca_despacha_pedido_fora_da_forma(
    servidor: Servidor, relogio: list[float], sobre: dict[str, Any]
) -> None:
    audit = Audit()
    executor = AmhTinaCarteirinhaExecutor(
        **_comum(servidor, _tokens(servidor, relogio), audit, purpose_of_use="atendimento_whatsapp")
    )
    resultado = await executor.execute(_pedido(**sobre))
    assert resultado.failure is not None and resultado.failure.reason is Reason.INVALID_REQUEST
    assert servidor.ao_servico() == [] and audit.registros == []


async def test_o_executor_tina_de_sempre_recusa_o_id_da_solicitacao(
    servidor: Servidor, relogio: list[float]
) -> None:
    """So' a rota da carteirinha leva o `X-Amh-Request-Ref`: as outras leituras TINA continuam sem ele."""
    executor = AmhTinaExecutor(**_comum(servidor, _tokens(servidor, relogio), Audit()))
    pedido = _pedido(
        operation=tina.OP_ELEGIBILIDADE,
        path="/interop/tina/v1/subjects/subject1/elegibilidade",
        query=(("purpose_of_use", "sharing_amh_internal"),),
    )
    resultado = await executor.execute(pedido)
    assert resultado.failure is not None and resultado.failure.reason is Reason.INVALID_REQUEST
