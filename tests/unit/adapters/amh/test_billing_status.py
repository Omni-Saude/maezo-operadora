"""Mecanica do contrato `billing-status` com um schema SINTETICO minusculo (nao e' copia do canonico).

O contrato real ainda nao esta publicado nem pinado: o teste que fecha isso e'
`test_sem_pin_o_construtor_recusa`.
"""

import asyncio
import hashlib
import json
from dataclasses import replace

import pytest
import yaml

from maezo.adapters.amh import billing_status as bs
from maezo.adapters.amh.contract import load_contract_pin
from maezo.ports.billing_status import BillingStatusPort
from maezo.ports.errors import PortFailureReason as Reason
from maezo.ports.errors import PortResult

REF = "subject1"


def _corpo(**sobre):
    corpo = {
        "portable_subject_ref": REF,
        "as_of": "2026-10-05T12:00:00Z",
        "fonte_atualizada_em": "2026-08-05T17:17:30Z",
        "resumo": {
            "status_conciliado": False,
            "ciclos_sem_conciliacao": 2,
            "valor_em_aberto": "640.50",
            "dias_atraso_max": 61,
            "pagador_tipo": "pessoa_fisica",
            "criterio_conciliacao": "situacao_paga_ou_liquidada",
        },
        "competencias": [
            {
                "competencia": "2026-08",
                "parcela": 8,
                "vencimento": "2026-08-10",
                "situacao": "vencida",
                "valor_total": "320.25",
                "valor_coparticipacao": "0.00",
                "valor_saldo": "320.25",
                "liquidado_em": None,
                "boleto": {"numero_mascarado": "****4821", "disponivel_online": True},
            }
        ],
        "qualidade": {"campos_ausentes": []},
    }
    corpo.update(sobre)
    return corpo


class Executor(bs.GovernedBillingStatusExecutor):
    registered_operations = frozenset({bs.OPERATION})

    def __init__(self):
        self.calls = []
        self.status = 200
        self.body = _corpo()
        self.failure = None
        self.delay = 0

    async def execute(self, request):
        self.calls.append(request)
        await asyncio.sleep(self.delay)
        if self.failure:
            raise self.failure
        return PortResult.ok(bs.GovernedBillingStatusResponse(self.status, json.dumps(self.body).encode()))


@pytest.fixture
def consumer(monkeypatch):
    nullable_str = {"type": "string", "nullable": True}
    schemas = {
        "BillingStatus": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "portable_subject_ref",
                "as_of",
                "fonte_atualizada_em",
                "resumo",
                "competencias",
                "qualidade",
            ],
            "properties": {
                "portable_subject_ref": {"type": "string"},
                "as_of": {"type": "string", "format": "date-time"},
                "fonte_atualizada_em": {"type": "string", "format": "date-time"},
                "resumo": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "status_conciliado",
                        "ciclos_sem_conciliacao",
                        "valor_em_aberto",
                        "dias_atraso_max",
                        "pagador_tipo",
                        "criterio_conciliacao",
                    ],
                    "properties": {
                        "status_conciliado": {"type": "boolean", "nullable": True},
                        "ciclos_sem_conciliacao": {"type": "integer", "minimum": 0, "nullable": True},
                        "valor_em_aberto": nullable_str,
                        "dias_atraso_max": {"type": "integer", "minimum": 0, "nullable": True},
                        "pagador_tipo": {
                            "type": "string",
                            "enum": ["pessoa_fisica", "pessoa_juridica", "desconhecido"],
                        },
                        "criterio_conciliacao": {"type": "string"},
                    },
                },
                "competencias": {
                    "type": "array",
                    "maxItems": 36,
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": [
                            "competencia",
                            "parcela",
                            "vencimento",
                            "situacao",
                            "valor_total",
                            "valor_coparticipacao",
                            "valor_saldo",
                            "liquidado_em",
                            "boleto",
                        ],
                        "properties": {
                            "competencia": {"type": "string"},
                            "parcela": {"type": "integer", "nullable": True},
                            "vencimento": nullable_str,
                            "situacao": {
                                "type": "string",
                                "enum": ["paga", "em_aberto", "vencida", "cancelada", "sem_titulo"],
                            },
                            "valor_total": nullable_str,
                            "valor_coparticipacao": nullable_str,
                            "valor_saldo": nullable_str,
                            "liquidado_em": nullable_str,
                            "boleto": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["numero_mascarado", "disponivel_online"],
                                "properties": {
                                    "numero_mascarado": nullable_str,
                                    "disponivel_online": {"type": "boolean", "nullable": True},
                                },
                            },
                        },
                    },
                },
                "qualidade": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["campos_ausentes"],
                    "properties": {"campos_ausentes": {"type": "array", "items": {"type": "string"}}},
                },
            },
        }
    }
    params = {
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
            "schema": {"type": "string", "enum": ["purpose1"]},
        },
    }
    inline = [
        {
            "name": "competencia",
            "in": "query",
            "required": False,
            "schema": {"type": "string", "pattern": "^[0-9]{4}-(0[1-9]|1[0-2])$"},
        },
        {
            "name": "janela_meses",
            "in": "query",
            "required": False,
            "schema": {"type": "integer", "minimum": 1, "maximum": 36, "default": 12},
        },
    ]
    raw = yaml.safe_dump(
        {
            "openapi": "3.0.3",
            "servers": [{"url": "/interop/billing-status/v1"}],
            "paths": {
                "/subjects/{portable_subject_ref}/billing/status": {
                    "get": {
                        "parameters": [
                            {"$ref": "#/components/parameters/PortableSubjectRef"},
                            {"$ref": "#/components/parameters/PurposeOfUse"},
                            *inline,
                        ],
                        "responses": {
                            "200": {
                                "content": {
                                    "application/json": {
                                        "schema": {"$ref": "#/components/schemas/BillingStatus"}
                                    }
                                }
                            }
                        },
                    }
                }
            },
            "components": {"schemas": schemas, "parameters": params},
        }
    ).encode()
    real_pin = load_contract_pin()
    monkeypatch.setattr(
        bs,
        "load_contract_pin",
        lambda path=None: replace(real_pin, artifact_digests={bs.ARTIFACT: hashlib.sha256(raw).hexdigest()}),
    )
    executor = Executor()
    return bs.AmhBillingStatusAdapter(raw, executor=executor), executor, raw


async def _ler(adapter, **kw):
    args = {"purpose_of_use": "purpose1", "consent_decision_ref": "consent1", **kw}
    return await adapter.get_billing_status(REF, **args)


async def test_leitura_ok_mapeia_para_o_tipo_do_port(consumer):
    adapter, executor, _ = consumer
    assert isinstance(adapter, BillingStatusPort)
    result = await _ler(adapter)
    assert result.succeeded
    view = result.value
    assert view.resumo.status_conciliado is False
    assert view.resumo.ciclos_sem_conciliacao == 2
    assert view.resumo.valor_em_aberto == "640.50"
    assert view.competencias[0].boleto_numero_mascarado == "****4821"
    assert view.campos_ausentes == frozenset()


async def test_pedido_leva_proposito_e_janela_e_nunca_o_consentimento(consumer):
    adapter, executor, _ = consumer
    await _ler(adapter, competencia="2026-08", janela_meses=6)
    request = executor.calls[0]
    assert request.operation == "amh.get_billing_status"
    assert request.path == "/interop/billing-status/v1/subjects/subject1/billing/status"
    assert dict(request.query) == {"purpose_of_use": "purpose1", "competencia": "2026-08", "janela_meses": 6}
    assert request.consent_decision_ref == "consent1"
    assert "subject1" not in repr(request)


async def test_campos_ausentes_chegam_como_none_e_como_nome(consumer):
    adapter, executor, _ = consumer
    body = _corpo()
    body["resumo"].update(status_conciliado=None, ciclos_sem_conciliacao=None, valor_em_aberto=None)
    body["qualidade"] = {"campos_ausentes": ["status_conciliado", "ciclos_sem_conciliacao"]}
    executor.body = body
    view = (await _ler(adapter)).value
    assert view.resumo.status_conciliado is None
    assert view.resumo.ciclos_sem_conciliacao is None
    assert view.campos_ausentes == {"status_conciliado", "ciclos_sem_conciliacao"}


async def test_resposta_de_outro_sujeito_e_violacao_de_contrato(consumer):
    adapter, executor, _ = consumer
    executor.body = _corpo(portable_subject_ref="subject2")
    result = await _ler(adapter)
    assert not result.succeeded
    assert result.failure.reason is Reason.CONTRACT_VIOLATION
    assert result.failure.detail is None


async def test_corpo_fora_do_schema_e_violacao_de_contrato(consumer):
    adapter, executor, _ = consumer
    executor.body = _corpo(campo_inventado=1)
    assert (await _ler(adapter)).failure.reason is Reason.CONTRACT_VIOLATION
    executor.body = _corpo()
    executor.body["resumo"]["pagador_tipo"] = "outro"
    assert (await _ler(adapter)).failure.reason is Reason.CONTRACT_VIOLATION


@pytest.mark.parametrize(
    "status,body,reason",
    [
        (403, {"reason": "consent_denied"}, Reason.CONSENT_REQUIRED),
        (403, {"reason": "purpose_not_permitted"}, Reason.PURPOSE_DENIED),
        (403, {"reason": "scope_not_supported"}, Reason.SCOPE_NOT_SUPPORTED),
        (403, {"reason": "outra"}, Reason.CONTRACT_VIOLATION),
        (400, {}, Reason.INVALID_REQUEST),
        (401, {}, Reason.NOT_AUTHENTICATED),
        (404, {}, Reason.NOT_FOUND),
        (429, {}, Reason.RATE_LIMITED),
        (500, {}, Reason.UPSTREAM_UNAVAILABLE),
        (418, {}, Reason.CONTRACT_VIOLATION),
    ],
)
async def test_status_http_vira_recusa_fechada(consumer, status, body, reason):
    adapter, executor, _ = consumer
    executor.status, executor.body = status, body
    result = await _ler(adapter)
    assert not result.succeeded
    assert result.failure.reason is reason


@pytest.mark.parametrize(
    "kwargs",
    [
        {"purpose_of_use": "errado"},
        {"consent_decision_ref": ""},
        {"competencia": "2026-13"},
        {"janela_meses": 0},
        {"janela_meses": 37},
        {"janela_meses": True},
        {"timeout_seconds": 0},
        {"timeout_seconds": float("nan")},
    ],
)
async def test_pedido_invalido_nunca_e_despachado(consumer, kwargs):
    adapter, executor, _ = consumer
    result = await _ler(adapter, **kwargs)
    assert result.failure.reason is Reason.INVALID_REQUEST
    assert executor.calls == []


async def test_sujeito_fora_do_padrao_nunca_e_despachado(consumer):
    adapter, executor, _ = consumer
    result = await adapter.get_billing_status("../x", purpose_of_use="purpose1", consent_decision_ref="c")
    assert result.failure.reason is Reason.INVALID_REQUEST
    assert executor.calls == []


async def test_operacao_nao_registrada_recusa_sem_despachar(consumer):
    adapter, executor, _ = consumer
    executor.registered_operations = frozenset()
    result = await _ler(adapter)
    assert result.failure.reason is Reason.SCOPE_NOT_SUPPORTED
    assert executor.calls == []


async def test_executor_que_levanta_vira_upstream_sem_vazar_a_excecao(consumer):
    adapter, executor, _ = consumer
    executor.failure = RuntimeError("CPF 123.456.789-00 no texto da excecao")
    result = await _ler(adapter)
    assert result.failure.reason is Reason.UPSTREAM_UNAVAILABLE
    assert result.failure.detail is None


async def test_demora_alem_do_prazo_vira_timeout(consumer):
    adapter, executor, _ = consumer
    executor.delay = 0.2
    result = await _ler(adapter, timeout_seconds=0.01)
    assert result.failure.reason is Reason.TIMEOUT


def test_sem_pin_o_construtor_recusa():
    """O artefato ainda nao foi publicado: nao ha digest no pin, entao nenhum byte e aceito."""
    with pytest.raises(bs.BillingStatusContractError):
        bs.AmhBillingStatusAdapter(b"openapi: 3.0.3", executor=Executor())


def test_bytes_que_nao_batem_com_o_digest_pinado_sao_recusados(consumer):
    _, executor, raw = consumer
    with pytest.raises(bs.BillingStatusContractError):
        bs.AmhBillingStatusAdapter(raw + b"\n# adulterado", executor=executor)
