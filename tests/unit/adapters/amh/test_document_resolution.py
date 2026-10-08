"""Adaptador da resolucao pelo DOCUMENTO (DL-0084) com schema SINTETICO minusculo (nao e' copia).

A rota mora no mesmo artefato da conferencia (`subject-verification.openapi.yaml`, pin v1.3, AINDA NAO
pinado): sem o bloco no lock o construtor recusa. A forma e' a pedida a AMH: `{cpf_hash, cpfdob_hash,
hash_scheme, purpose_of_use, amh_tenant}` -> `{resultado, portable_subject_ref?, fonte_atualizada_em}`.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace

import pytest
import yaml

from maezo.adapters.amh import document_resolution as dr
from maezo.adapters.amh.contract import load_contract_pin
from maezo.ports.document_resolution import DocumentResolutionPort
from maezo.ports.errors import PortFailureReason as Reason
from maezo.ports.errors import PortResult

H_CPF = "a" * 64
H_DOB = "b" * 64
ARGS = {
    "document_hash": H_CPF,
    "document_dob_hash": H_DOB,
    "hash_scheme": "amh-subject-verify-v1",
    "purpose_of_use": "sharing_amh_internal",
    "amh_tenant": "austa",
}
FONTE = "2026-10-05T03:12:00Z"


def _openapi(*, com_rota: bool = True) -> bytes:
    paths: dict[str, object] = {}
    if com_rota:
        paths["/resolve-by-document"] = {
            "post": {
                "requestBody": {
                    "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Pedido"}}}
                },
                "responses": {
                    "200": {
                        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Resposta"}}}
                    }
                },
            }
        }
    return yaml.safe_dump(
        {
            "openapi": "3.0.3",
            "servers": [{"url": "/interop/identity/v1"}],
            "paths": paths,
            "components": {
                "schemas": {
                    "Pedido": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": [
                            "cpf_hash",
                            "cpfdob_hash",
                            "hash_scheme",
                            "purpose_of_use",
                            "amh_tenant",
                        ],
                        "properties": {
                            "cpf_hash": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                            "cpfdob_hash": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                            "hash_scheme": {"type": "string", "enum": ["amh-subject-verify-v1"]},
                            "purpose_of_use": {"type": "string"},
                            "amh_tenant": {"type": "string"},
                        },
                    },
                    "Resposta": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["resultado", "fonte_atualizada_em"],
                        "properties": {
                            "resultado": {"type": "string", "enum": ["unico", "nenhum"]},
                            "portable_subject_ref": {"type": "string", "nullable": True},
                            "fonte_atualizada_em": {
                                "type": "string",
                                "format": "date-time",
                                "nullable": True,
                            },
                        },
                    },
                }
            },
        }
    ).encode()


class Exec(dr.GovernedDocumentResolutionExecutor):
    registered_operations = frozenset({dr.OP_RESOLVE_DOCUMENT})

    def __init__(self) -> None:
        self.calls: list[dr.GovernedDocumentResolutionRequest] = []
        self.status = 200
        self.corpo: object = {
            "resultado": "unico",
            "portable_subject_ref": "subject1",
            "fonte_atualizada_em": FONTE,
        }
        self.refusal: Reason | None = None

    async def execute(self, request):  # type: ignore[no-untyped-def]
        self.calls.append(request)
        if self.refusal is not None:
            return PortResult.refused(self.refusal)
        return PortResult.ok(
            dr.GovernedDocumentResolutionResponse(self.status, json.dumps(self.corpo).encode())
        )


def _pinar(monkeypatch: pytest.MonkeyPatch, raw: bytes) -> None:
    real = load_contract_pin()
    monkeypatch.setattr(
        dr,
        "load_contract_pin",
        lambda path=None: replace(real, artifact_digests={dr.ARTIFACT: hashlib.sha256(raw).hexdigest()}),
    )


@pytest.fixture
def documento(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    raw = _openapi()
    _pinar(monkeypatch, raw)
    ex = Exec()
    return dr.AmhDocumentResolutionAdapter(raw, executor=ex), ex


def test_sem_pin_v1_3_recusa_e_mora_no_artefato_da_conferencia() -> None:
    assert dr.ARTIFACT == "schemas/openapi/maezo/v1/subject-verification.openapi.yaml"
    assert dr.ARTIFACT not in load_contract_pin().artifact_digests
    with pytest.raises(dr.DocumentResolutionContractError):
        dr.AmhDocumentResolutionAdapter(_openapi(), executor=Exec())


def test_artefato_pinado_sem_a_rota_recusa(monkeypatch: pytest.MonkeyPatch) -> None:
    raw = _openapi(com_rota=False)
    _pinar(monkeypatch, raw)
    with pytest.raises(dr.DocumentResolutionContractError):
        dr.AmhDocumentResolutionAdapter(raw, executor=Exec())


async def test_unico_devolve_a_ref_e_o_corpo_leva_so_hashes(documento) -> None:  # type: ignore[no-untyped-def]
    adapter, ex = documento
    assert isinstance(adapter, DocumentResolutionPort)
    r = await adapter.resolve_by_document(**ARGS)
    assert r.succeeded and r.value is not None
    assert r.value.resultado == "unico" and r.value.portable_subject_ref == "subject1"
    [req] = ex.calls
    assert req.method == "POST" and req.path == "/interop/identity/v1/resolve-by-document"
    assert json.loads(req.body) == {
        "cpf_hash": H_CPF,
        "cpfdob_hash": H_DOB,
        "hash_scheme": "amh-subject-verify-v1",
        "purpose_of_use": "sharing_amh_internal",
        "amh_tenant": "austa",
    }
    assert H_CPF not in repr(req)


@pytest.mark.parametrize(
    "corpo",
    [
        {"resultado": "nenhum", "fonte_atualizada_em": FONTE},
        {"resultado": "nenhum", "portable_subject_ref": None, "fonte_atualizada_em": FONTE},
    ],
)
async def test_nenhum(documento, corpo) -> None:  # type: ignore[no-untyped-def]
    adapter, ex = documento
    ex.corpo = corpo
    r = await adapter.resolve_by_document(**ARGS)
    assert r.succeeded and r.value is not None and r.value.resultado == "nenhum"
    assert r.value.portable_subject_ref is None


@pytest.mark.parametrize("resultado", ["unico", "nenhum"])
async def test_fonte_nula_e_indisponivel_nunca_resultado(documento, resultado) -> None:  # type: ignore[no-untyped-def]
    adapter, ex = documento
    ex.corpo = {"resultado": resultado, "portable_subject_ref": "subject1", "fonte_atualizada_em": None}
    r = await adapter.resolve_by_document(**ARGS)
    assert r.failure is not None and r.failure.reason is Reason.UPSTREAM_UNAVAILABLE


@pytest.mark.parametrize(
    "corpo",
    [
        {"resultado": "unico", "fonte_atualizada_em": FONTE},
        {"resultado": "unico", "portable_subject_ref": "", "fonte_atualizada_em": FONTE},
        {"resultado": "unico", "portable_subject_ref": "a/b", "fonte_atualizada_em": FONTE},
        {"resultado": "nenhum", "portable_subject_ref": "subject1", "fonte_atualizada_em": FONTE},
        {"resultado": "multiplos", "fonte_atualizada_em": FONTE},
        {"resultado": "unico", "portable_subject_ref": "subject1"},
        {"resultado": "unico", "portable_subject_ref": "subject1", "fonte_atualizada_em": FONTE, "cpf": "x"},
    ],
)
async def test_resposta_fora_do_contrato(documento, corpo) -> None:  # type: ignore[no-untyped-def]
    adapter, ex = documento
    ex.corpo = corpo
    r = await adapter.resolve_by_document(**ARGS)
    assert r.failure is not None and r.failure.reason is Reason.CONTRACT_VIOLATION


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        (429, Reason.RATE_LIMITED),
        (400, Reason.INVALID_REQUEST),
        (401, Reason.NOT_AUTHENTICATED),
        (500, Reason.UPSTREAM_UNAVAILABLE),
        (503, Reason.UPSTREAM_UNAVAILABLE),
        (302, Reason.CONTRACT_VIOLATION),
    ],
)
async def test_status_vira_recusa_fechada(documento, status, reason) -> None:  # type: ignore[no-untyped-def]
    adapter, ex = documento
    ex.status = status
    ex.corpo = {"detalhe": "nao vaza"}
    r = await adapter.resolve_by_document(**ARGS)
    assert r.failure is not None and r.failure.reason is reason


@pytest.mark.parametrize(
    "sobre",
    [
        {"document_hash": "nao-e-hash"},
        {"document_dob_hash": H_CPF},
        {"hash_scheme": "amh-phone-lookup-v1"},
        {"timeout_seconds": 0},
    ],
)
async def test_entrada_invalida_nao_chega_ao_executor(documento, sobre) -> None:  # type: ignore[no-untyped-def]
    adapter, ex = documento
    r = await adapter.resolve_by_document(**{**ARGS, **sobre})
    assert r.failure is not None and r.failure.reason is Reason.INVALID_REQUEST
    assert ex.calls == []


async def test_recusa_do_executor_e_propagada(documento) -> None:  # type: ignore[no-untyped-def]
    adapter, ex = documento
    ex.refusal = Reason.TIMEOUT
    assert (await adapter.resolve_by_document(**ARGS)).failure.reason is Reason.TIMEOUT  # type: ignore[union-attr]
