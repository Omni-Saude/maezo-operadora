"""Mecanica do contrato `subject-resolution` com um schema SINTETICO minusculo (nao e' copia do canonico).

O contrato real ainda nao esta publicado nem pinado: o teste que fecha isso e'
`test_sem_pin_o_construtor_recusa`.
"""

import asyncio
import hashlib
import json
from dataclasses import replace

import pytest
import yaml

from maezo.adapters.amh import subject_resolution as sr
from maezo.adapters.amh.contract import load_contract_pin
from maezo.ports.errors import PortFailureReason as Reason
from maezo.ports.errors import PortResult
from maezo.ports.subject_resolution import SubjectResolutionPort

REF = "subject1"
OUTRO = "subject2"
HASH = "hk1_abcdef0123456789"


def _candidato(ref=REF, relacao="titular"):
    return {"portable_subject_ref": ref, "relacao": relacao, "vigencia_ativa": True}


def _resolucao(candidatos=None, resultado=None):
    candidatos = [_candidato()] if candidatos is None else candidatos
    esperado = {0: "nenhum", 1: "unico"}.get(len(candidatos), "multiplos")
    return {
        "resultado": resultado or esperado,
        "candidatos": candidatos,
        "qualidade": {"campos_ausentes": []},
    }


def _perfil(**sobre):
    corpo = {
        "portable_subject_ref": REF,
        "idade_anos": 54,
        "idade_meses": None,
        "plano": {
            "ativo": True,
            "vigencia_inicio": "2025-01-01",
            "vigencia_fim": None,
            "carencia_vigente": False,
        },
        "titular_ref": None,
        "fonte_atualizada_em": "2026-08-05T17:17:30Z",
        "qualidade": {"campos_ausentes": []},
    }
    corpo.update(sobre)
    return corpo


class Executor(sr.GovernedSubjectResolutionExecutor):
    registered_operations = frozenset({sr.OP_RESOLVE, sr.OP_PROFILE})

    def __init__(self):
        self.calls = []
        self.status = 200
        self.body = _resolucao()
        self.failure = None
        self.delay = 0

    async def execute(self, request):
        self.calls.append(request)
        await asyncio.sleep(self.delay)
        if self.failure:
            raise self.failure
        return PortResult.ok(
            sr.GovernedSubjectResolutionResponse(self.status, json.dumps(self.body).encode())
        )


def _contrato():
    ns = {"type": "string", "nullable": True}
    ref = {"type": "string", "pattern": "^subject[0-9]+$"}
    qualidade = {
        "type": "object",
        "additionalProperties": False,
        "required": ["campos_ausentes"],
        "properties": {"campos_ausentes": {"type": "array", "items": {"type": "string"}}},
    }
    schemas = {
        "ResolveByPhoneRequest": {
            "type": "object",
            "additionalProperties": False,
            "required": ["amh_tenant", "phone_hash", "hash_scheme", "purpose_of_use"],
            "properties": {
                "amh_tenant": {"type": "string"},
                "phone_hash": {"type": "string", "pattern": "^[A-Za-z0-9_\\-]{8,128}$"},
                "hash_scheme": {"type": "string", "enum": ["maezo-hk1"]},
                "purpose_of_use": {"type": "string", "enum": ["purpose1"]},
            },
        },
        "ResolveByPhoneResponse": {
            "type": "object",
            "additionalProperties": False,
            "required": ["resultado", "candidatos", "qualidade"],
            "properties": {
                "resultado": {"type": "string", "enum": ["unico", "multiplos", "nenhum"]},
                "candidatos": {
                    "type": "array",
                    "maxItems": 10,
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["portable_subject_ref", "relacao", "vigencia_ativa"],
                        "properties": {
                            "portable_subject_ref": ref,
                            "relacao": {"type": "string", "enum": ["titular", "dependente", "desconhecida"]},
                            "vigencia_ativa": {"type": "boolean", "nullable": True},
                        },
                    },
                },
                "qualidade": qualidade,
            },
        },
        "SubjectProfile": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "portable_subject_ref",
                "idade_anos",
                "idade_meses",
                "plano",
                "titular_ref",
                "fonte_atualizada_em",
                "qualidade",
            ],
            "properties": {
                "portable_subject_ref": ref,
                "idade_anos": {"type": "integer", "minimum": 0, "nullable": True},
                "idade_meses": {"type": "integer", "minimum": 0, "nullable": True},
                "plano": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["ativo", "vigencia_inicio", "vigencia_fim", "carencia_vigente"],
                    "properties": {
                        "ativo": {"type": "boolean", "nullable": True},
                        "vigencia_inicio": ns,
                        "vigencia_fim": ns,
                        "carencia_vigente": {"type": "boolean", "nullable": True},
                    },
                },
                "titular_ref": ns,
                "fonte_atualizada_em": {"type": "string", "format": "date-time"},
                "qualidade": qualidade,
            },
        },
    }
    params = {
        "PortableSubjectRef": {
            "name": "portable_subject_ref",
            "in": "path",
            "required": True,
            "schema": ref,
        },
        "PurposeOfUse": {
            "name": "purpose_of_use",
            "in": "query",
            "required": True,
            "schema": {"type": "string", "enum": ["purpose1"]},
        },
    }

    def json_ref(nome):
        return {"content": {"application/json": {"schema": {"$ref": "#/components/schemas/" + nome}}}}

    return yaml.safe_dump(
        {
            "openapi": "3.0.3",
            "servers": [{"url": "/interop/subject-resolution/v1"}],
            "paths": {
                "/subjects/resolve-by-phone": {
                    "post": {
                        "requestBody": {"required": True, **json_ref("ResolveByPhoneRequest")},
                        "responses": {"200": json_ref("ResolveByPhoneResponse")},
                    }
                },
                "/subjects/{portable_subject_ref}/profile": {
                    "get": {
                        "parameters": [
                            {"$ref": "#/components/parameters/PortableSubjectRef"},
                            {"$ref": "#/components/parameters/PurposeOfUse"},
                        ],
                        "responses": {"200": json_ref("SubjectProfile")},
                    }
                },
            },
            "components": {"schemas": schemas, "parameters": params},
        }
    ).encode()


@pytest.fixture
def consumer(monkeypatch):
    raw = _contrato()
    real_pin = load_contract_pin()
    monkeypatch.setattr(
        sr,
        "load_contract_pin",
        lambda path=None: replace(real_pin, artifact_digests={sr.ARTIFACT: hashlib.sha256(raw).hexdigest()}),
    )
    executor = Executor()
    return sr.AmhSubjectResolutionAdapter(raw, executor=executor), executor, raw


async def _resolver(adapter, **kw):
    args = {"amh_tenant": "omni", "hash_scheme": "maezo-hk1", "purpose_of_use": "purpose1", **kw}
    return await adapter.resolve_by_phone(HASH, **args)


async def _perfil_de(adapter, **kw):
    args = {"purpose_of_use": "purpose1", "consent_decision_ref": "consent1", **kw}
    return await adapter.get_profile(REF, **args)


# --- resolve_by_phone ----------------------------------------------------------------------------


async def test_o_hash_vai_no_corpo_e_nunca_na_url_nem_no_repr(consumer):
    adapter, executor, _ = consumer
    assert isinstance(adapter, SubjectResolutionPort)
    result = await _resolver(adapter)
    assert result.succeeded
    request = executor.calls[0]
    assert (request.method, request.operation) == ("POST", "amh.resolve_subject_by_phone")
    assert request.path == "/interop/subject-resolution/v1/subjects/resolve-by-phone"
    assert request.query == ()
    assert json.loads(request.body) == {
        "amh_tenant": "omni",
        "phone_hash": HASH,
        "hash_scheme": "maezo-hk1",
        "purpose_of_use": "purpose1",
    }
    assert request.consent_decision_ref is None
    assert HASH not in request.path and HASH not in repr(request)


async def test_unico_multiplos_e_nenhum_chegam_como_dados(consumer):
    adapter, executor, _ = consumer
    unico = (await _resolver(adapter)).value
    assert (unico.resultado, len(unico.candidatos)) == ("unico", 1)
    assert unico.candidatos[0].relacao == "titular"

    executor.body = _resolucao([_candidato(REF), _candidato(OUTRO, "dependente")])
    multiplos = (await _resolver(adapter)).value
    assert (multiplos.resultado, len(multiplos.candidatos)) == ("multiplos", 2)

    executor.body = _resolucao([])
    nenhum = (await _resolver(adapter)).value
    assert (nenhum.resultado, nenhum.candidatos) == ("nenhum", ())


async def test_rotulo_que_nao_diz_quantos_candidatos_vieram_e_violacao(consumer):
    adapter, executor, _ = consumer
    executor.body = _resolucao([_candidato(REF), _candidato(OUTRO)], resultado="unico")
    assert (await _resolver(adapter)).failure.reason is Reason.CONTRACT_VIOLATION
    executor.body = _resolucao([], resultado="unico")
    assert (await _resolver(adapter)).failure.reason is Reason.CONTRACT_VIOLATION


@pytest.mark.parametrize(
    "kwargs",
    [
        {"hash_scheme": "outro"},
        {"purpose_of_use": "errado"},
        {"timeout_seconds": 0},
        {"timeout_seconds": float("nan")},
    ],
)
async def test_pedido_de_resolucao_invalido_nunca_e_despachado(consumer, kwargs):
    adapter, executor, _ = consumer
    result = await _resolver(adapter, **kwargs)
    assert result.failure.reason is Reason.INVALID_REQUEST
    assert executor.calls == []


async def test_hash_fora_do_padrao_nunca_e_despachado(consumer):
    adapter, executor, _ = consumer
    result = await adapter.resolve_by_phone(
        "+55 11 90000-0000", amh_tenant="omni", hash_scheme="maezo-hk1", purpose_of_use="purpose1"
    )
    assert result.failure.reason is Reason.INVALID_REQUEST
    assert executor.calls == []


@pytest.mark.parametrize(
    "status,body,reason",
    [
        (403, {"reason": "consent_denied"}, Reason.CONSENT_REQUIRED),
        (403, {"reason": "purpose_not_permitted"}, Reason.PURPOSE_DENIED),
        (400, {}, Reason.INVALID_REQUEST),
        (401, {}, Reason.NOT_AUTHENTICATED),
        (429, {}, Reason.RATE_LIMITED),
        (500, {}, Reason.UPSTREAM_UNAVAILABLE),
        (418, {}, Reason.CONTRACT_VIOLATION),
    ],
)
async def test_status_http_vira_recusa_fechada(consumer, status, body, reason):
    adapter, executor, _ = consumer
    executor.status, executor.body = status, body
    assert (await _resolver(adapter)).failure.reason is reason


async def test_candidato_fora_do_padrao_de_referencia_e_violacao(consumer):
    adapter, executor, _ = consumer
    executor.body = _resolucao([_candidato("ref-invalida")])
    assert (await _resolver(adapter)).failure.reason is Reason.CONTRACT_VIOLATION


# --- get_profile ---------------------------------------------------------------------------------


async def test_perfil_ok_e_consentimento_so_como_metadado(consumer):
    adapter, executor, _ = consumer
    executor.body = _perfil()
    result = await _perfil_de(adapter)
    assert result.succeeded
    assert (result.value.idade_anos, result.value.plano.ativo) == (54, True)
    request = executor.calls[0]
    assert (request.method, request.operation) == ("GET", "amh.get_subject_profile")
    assert request.path == "/interop/subject-resolution/v1/subjects/subject1/profile"
    assert dict(request.query) == {"purpose_of_use": "purpose1"}
    assert request.consent_decision_ref == "consent1" and request.body is None


async def test_perfil_com_campos_ausentes_chega_como_none(consumer):
    adapter, executor, _ = consumer
    executor.body = _perfil(idade_anos=None, qualidade={"campos_ausentes": ["idade_anos"]})
    perfil = (await _perfil_de(adapter)).value
    assert perfil.idade_anos is None
    assert perfil.campos_ausentes == {"idade_anos"}


async def test_perfil_de_outro_sujeito_e_violacao(consumer):
    adapter, executor, _ = consumer
    executor.body = _perfil(portable_subject_ref=OUTRO)
    result = await _perfil_de(adapter)
    assert result.failure.reason is Reason.CONTRACT_VIOLATION
    assert result.failure.detail is None


@pytest.mark.parametrize(
    "kwargs", [{"consent_decision_ref": ""}, {"purpose_of_use": "errado"}, {"timeout_seconds": 0}]
)
async def test_pedido_de_perfil_invalido_nunca_e_despachado(consumer, kwargs):
    adapter, executor, _ = consumer
    assert (await _perfil_de(adapter, **kwargs)).failure.reason is Reason.INVALID_REQUEST
    assert executor.calls == []


# --- executor e contrato -------------------------------------------------------------------------


async def test_operacao_nao_registrada_recusa_sem_despachar(consumer):
    adapter, executor, _ = consumer
    executor.registered_operations = frozenset()
    assert (await _resolver(adapter)).failure.reason is Reason.SCOPE_NOT_SUPPORTED
    assert (await _perfil_de(adapter)).failure.reason is Reason.SCOPE_NOT_SUPPORTED
    assert executor.calls == []


async def test_executor_que_levanta_vira_upstream_sem_vazar_a_excecao(consumer):
    adapter, executor, _ = consumer
    executor.failure = RuntimeError("telefone +5511900000000 no texto da excecao")
    result = await _resolver(adapter)
    assert result.failure.reason is Reason.UPSTREAM_UNAVAILABLE
    assert result.failure.detail is None


async def test_demora_alem_do_prazo_vira_timeout(consumer):
    adapter, executor, _ = consumer
    executor.delay = 0.2
    assert (await _resolver(adapter, timeout_seconds=0.01)).failure.reason is Reason.TIMEOUT


def test_sem_pin_o_construtor_recusa():
    """O artefato ainda nao foi publicado: nao ha digest no pin, entao nenhum byte e aceito."""
    with pytest.raises(sr.SubjectResolutionContractError):
        sr.AmhSubjectResolutionAdapter(b"openapi: 3.0.3", executor=Executor())


def test_bytes_que_nao_batem_com_o_digest_pinado_sao_recusados(consumer):
    _, executor, raw = consumer
    with pytest.raises(sr.SubjectResolutionContractError):
        sr.AmhSubjectResolutionAdapter(raw + b"\n# adulterado", executor=executor)
