"""Mecanica do contrato `tina` (fatos do plano) com um schema SINTETICO minusculo (nao e' copia do canonico).

O contrato real (manifest aditivo v1.2) ainda e' DRAFT na AMH e nao esta' pinado: o teste que fecha isso
e' `test_sem_pin_v1_2_o_construtor_recusa`.
"""

import asyncio
import hashlib
import json
from dataclasses import replace

import pytest
import yaml

from maezo.adapters.amh import tina
from maezo.adapters.amh.contract import load_contract_pin
from maezo.ports.errors import PortFailureReason as Reason
from maezo.ports.errors import PortResult
from maezo.ports.tina import CarenciasView, ElegibilidadeView, RequisicoesView, TinaPort

REF = "subject1"
_NS = {"type": "string", "nullable": True}
_ND = {"type": "string", "format": "date", "nullable": True}


def _eleg(**sobre):
    corpo = {
        "portable_subject_ref": REF,
        "ativo": True,
        "vinculos": [
            {
                "carteirinha_mascarada": "******4567",
                "plano": "PLANO SINTETICO",
                "segmentacao": "Ambulatorial + Hospitalar",
                "acomodacao": None,
                "vigencia_inicio": "2024-03-01",
                "cancelamento": None,
                "atendimento_liberado": True,
                "titular": True,
            }
        ],
        "fonte_atualizada_em": "2026-10-06T10:00:00Z",
        "qualidade": {"campos_ausentes": ["acomodacao"]},
    }
    corpo.update(sobre)
    return corpo


def _carencias():
    return {
        "portable_subject_ref": REF,
        "pendentes": 1,
        "carencias": [
            {
                "carencia": "PARTO",
                "inicio": "2026-01-01",
                "dias": 300,
                "validade": "2026-10-28",
                "cumprida": False,
            }
        ],
        "fonte_atualizada_em": "2026-10-06T10:00:00Z",
    }


def _requisicoes():
    return {
        "portable_subject_ref": REF,
        "requisicoes": [
            {
                "solicitacao": 123456,
                "solicitada_em": "2026-10-01T09:00:00Z",
                "status": "Em análise",
                "medico_solicitante": "DR SINTETICO",
                "senha_mascarada": "********42",
                "senha_validade": None,
                "senha_vigente": None,
                "sla_dias": 5,
                "liberacao_prevista": "2026-10-08",
            }
        ],
        "fonte_atualizada_em": "2026-10-06T10:00:00Z",
    }


class Executor(tina.GovernedTinaExecutor):
    registered_operations = tina.OPERATIONS

    def __init__(self):
        self.calls = []
        self.status = 200
        self.bodies = {
            tina.OP_ELEGIBILIDADE: _eleg(),
            tina.OP_CARENCIAS: _carencias(),
            tina.OP_REQUISICOES: _requisicoes(),
        }
        self.failure = None
        self.refusal = None
        self.delay = 0

    async def execute(self, request):
        self.calls.append(request)
        await asyncio.sleep(self.delay)
        if self.failure:
            raise self.failure
        if self.refusal is not None:
            return PortResult.refused(self.refusal)
        corpo = self.bodies[request.operation]
        return PortResult.ok(tina.GovernedTinaResponse(self.status, json.dumps(corpo).encode()))


def _openapi() -> bytes:
    vinculo = {
        "type": "object",
        "additionalProperties": False,
        "required": ["carteirinha_mascarada", "plano", "vigencia_inicio", "cancelamento", "titular"],
        "properties": {
            "carteirinha_mascarada": _NS,
            "plano": _NS,
            "segmentacao": _NS,
            "acomodacao": _NS,
            "vigencia_inicio": _ND,
            "cancelamento": _ND,
            "atendimento_liberado": {"type": "boolean", "nullable": True},
            "titular": {"type": "boolean", "nullable": True},
        },
    }
    schemas = {
        "Elegibilidade": {
            "type": "object",
            "additionalProperties": False,
            "required": ["portable_subject_ref", "ativo", "vinculos", "fonte_atualizada_em", "qualidade"],
            "properties": {
                "portable_subject_ref": {"type": "string"},
                "ativo": {"type": "boolean"},
                "vinculos": {"type": "array", "items": {"$ref": "#/components/schemas/Vinculo"}},
                "fonte_atualizada_em": {"type": "string", "format": "date-time"},
                "qualidade": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["campos_ausentes"],
                    "properties": {"campos_ausentes": {"type": "array", "items": {"type": "string"}}},
                },
            },
        },
        "Vinculo": vinculo,
        "Carencias": {
            "type": "object",
            "additionalProperties": False,
            "required": ["portable_subject_ref", "pendentes", "carencias", "fonte_atualizada_em"],
            "properties": {
                "portable_subject_ref": {"type": "string"},
                "pendentes": {"type": "integer"},
                "carencias": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["carencia", "inicio", "dias", "validade", "cumprida"],
                        "properties": {
                            "carencia": _NS,
                            "inicio": _ND,
                            "dias": {"type": "integer", "nullable": True},
                            "validade": _ND,
                            "cumprida": {"type": "boolean", "nullable": True},
                        },
                    },
                },
                "fonte_atualizada_em": {"type": "string", "format": "date-time"},
            },
        },
        "Requisicoes": {
            "type": "object",
            "additionalProperties": False,
            "required": ["portable_subject_ref", "requisicoes", "fonte_atualizada_em"],
            "properties": {
                "portable_subject_ref": {"type": "string"},
                "requisicoes": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["solicitacao", "solicitada_em", "status"],
                        "properties": {
                            "solicitacao": {"type": "integer", "nullable": True},
                            "solicitada_em": {"type": "string", "format": "date-time", "nullable": True},
                            "status": _NS,
                            "medico_solicitante": _NS,
                            "senha_mascarada": _NS,
                            "senha_validade": _ND,
                            "senha_vigente": {"type": "boolean", "nullable": True},
                            "sla_dias": {"type": "integer", "nullable": True},
                            "liberacao_prevista": _ND,
                        },
                    },
                },
                "fonte_atualizada_em": {"type": "string", "format": "date-time"},
            },
        },
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
        "Limite": {
            "name": "limite",
            "in": "query",
            "required": False,
            "schema": {"type": "integer", "minimum": 1, "maximum": 50, "default": 20},
        },
    }
    base = [
        {"$ref": "#/components/parameters/PortableSubjectRef"},
        {"$ref": "#/components/parameters/PurposeOfUse"},
    ]

    def op(nome, extra=()):
        return {
            "get": {
                "parameters": [*base, *extra],
                "responses": {
                    "200": {
                        "content": {"application/json": {"schema": {"$ref": f"#/components/schemas/{nome}"}}}
                    }
                },
            }
        }

    pendentes = {
        "name": "pendentes",
        "in": "query",
        "required": False,
        "schema": {"type": "string", "enum": ["true", "false"]},
    }
    limite = {"$ref": "#/components/parameters/Limite"}
    return yaml.safe_dump(
        {
            "openapi": "3.0.3",
            "servers": [{"url": "/interop/tina/v1"}],
            "paths": {
                "/subjects/{portable_subject_ref}/elegibilidade": op("Elegibilidade"),
                "/subjects/{portable_subject_ref}/carencias": op("Carencias", (limite, pendentes)),
                "/subjects/{portable_subject_ref}/requisicoes": op("Requisicoes", (limite,)),
            },
            "components": {"schemas": schemas, "parameters": params},
        }
    ).encode()


@pytest.fixture
def consumer(monkeypatch):
    raw = _openapi()
    real_pin = load_contract_pin()
    monkeypatch.setattr(
        tina,
        "load_contract_pin",
        lambda path=None: replace(
            real_pin, artifact_digests={tina.ARTIFACT: hashlib.sha256(raw).hexdigest()}
        ),
    )
    executor = Executor()
    return tina.AmhTinaAdapter(raw, executor=executor), executor, raw


_ARGS = {"purpose_of_use": "purpose1", "consent_decision_ref": "base-legal:execucao-de-contrato"}


async def test_elegibilidade_ok_mapeia_para_o_tipo_do_port(consumer):
    adapter, executor, _ = consumer
    assert isinstance(adapter, TinaPort)
    result = await adapter.get_elegibilidade(REF, **_ARGS)
    assert result.succeeded
    view = result.value
    assert isinstance(view, ElegibilidadeView)
    assert view.ativo is True
    assert view.vinculos[0].carteirinha_mascarada == "******4567"
    assert view.vinculos[0].acomodacao is None
    assert view.campos_ausentes == frozenset({"acomodacao"})
    pedido = executor.calls[0]
    assert pedido.operation == tina.OP_ELEGIBILIDADE
    assert pedido.path == "/interop/tina/v1/subjects/subject1/elegibilidade"
    assert pedido.query == (("purpose_of_use", "purpose1"),)
    # A base legal e' metadado de auditoria do executor, nunca query.
    assert "base-legal" not in json.dumps(pedido.query)


async def test_carencias_e_requisicoes_mandam_limite_e_pendentes_pelo_contrato(consumer):
    adapter, executor, _ = consumer
    car = await adapter.get_carencias(REF, limite=10, pendentes=True, **_ARGS)
    req = await adapter.get_requisicoes(REF, limite=5, **_ARGS)
    assert isinstance(car.value, CarenciasView) and car.value.carencias[0].cumprida is False
    assert isinstance(req.value, RequisicoesView) and req.value.requisicoes[0].senha_mascarada == "********42"
    assert executor.calls[0].query == (("purpose_of_use", "purpose1"), ("limite", 10), ("pendentes", "true"))
    assert executor.calls[1].query == (("purpose_of_use", "purpose1"), ("limite", 5))


@pytest.mark.parametrize("limite", [0, 51, True])
async def test_limite_fora_do_contrato_e_recusado_sem_chamar(consumer, limite):
    adapter, executor, _ = consumer
    result = await adapter.get_requisicoes(REF, limite=limite, **_ARGS)
    assert result.failure.reason is Reason.INVALID_REQUEST
    assert executor.calls == []


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        (404, Reason.NOT_FOUND),
        (401, Reason.NOT_AUTHENTICATED),
        (500, Reason.UPSTREAM_UNAVAILABLE),
        (418, Reason.CONTRACT_VIOLATION),
    ],
)
async def test_status_http_vira_recusa_fechada(consumer, status, reason):
    adapter, executor, _ = consumer
    executor.status = status
    result = await adapter.get_elegibilidade(REF, **_ARGS)
    assert not result.succeeded
    assert result.failure.reason is reason


async def test_404_do_executor_e_nao_encontrado(consumer):
    adapter, executor, _ = consumer
    executor.refusal = Reason.NOT_FOUND
    result = await adapter.get_carencias(REF, **_ARGS)
    assert result.failure.reason is Reason.NOT_FOUND


async def test_403_com_motivo_de_proposito(consumer):
    adapter, executor, _ = consumer
    executor.status = 403
    executor.bodies[tina.OP_ELEGIBILIDADE] = {
        "error_code": "x",
        "message": "y",
        "trace_id": "t",
        "reason": "purpose_not_permitted",
    }
    result = await adapter.get_elegibilidade(REF, **_ARGS)
    assert result.failure.reason is Reason.PURPOSE_DENIED


async def test_timeout_e_recusa_de_prazo(consumer):
    adapter, executor, _ = consumer
    executor.delay = 0.2
    result = await adapter.get_elegibilidade(REF, timeout_seconds=0.01, **_ARGS)
    assert result.failure.reason is Reason.TIMEOUT


@pytest.mark.parametrize(
    "corpo",
    [
        _eleg(extra="campo fora do contrato"),
        _eleg(ativo="sim"),
        _eleg(fonte_atualizada_em="ontem"),
        _eleg(portable_subject_ref="subject2"),  # fato de OUTRO sujeito
    ],
)
async def test_contrato_violado_e_recusado(consumer, corpo):
    adapter, executor, _ = consumer
    executor.bodies[tina.OP_ELEGIBILIDADE] = corpo
    result = await adapter.get_elegibilidade(REF, **_ARGS)
    assert result.failure.reason is Reason.CONTRACT_VIOLATION


async def test_excecao_do_executor_nao_atravessa(consumer):
    adapter, executor, _ = consumer
    executor.failure = RuntimeError("detalhe com PHI que nao pode vazar")
    result = await adapter.get_requisicoes(REF, **_ARGS)
    assert result.failure.reason is Reason.UPSTREAM_UNAVAILABLE
    assert "PHI" not in repr(result)


async def test_operacao_nao_registrada_no_executor_e_recusada(consumer):
    adapter, executor, _ = consumer
    executor.registered_operations = frozenset({tina.OP_ELEGIBILIDADE})
    result = await adapter.get_carencias(REF, **_ARGS)
    assert result.failure.reason is Reason.SCOPE_NOT_SUPPORTED


def test_sem_pin_v1_2_o_construtor_recusa():
    """O manifest v1.2 ainda e' DRAFT: o lock real nao tem o bloco, entao nenhum byte e' aceito."""
    assert tina.ARTIFACT not in load_contract_pin().artifact_digests
    with pytest.raises(tina.TinaContractError):
        tina.AmhTinaAdapter(_openapi(), executor=Executor())


def test_bytes_que_nao_batem_com_o_digest_pinado_sao_recusados(consumer):
    _, executor, raw = consumer
    with pytest.raises(tina.TinaContractError):
        tina.AmhTinaAdapter(raw + b"\n# adulterado", executor=executor)
