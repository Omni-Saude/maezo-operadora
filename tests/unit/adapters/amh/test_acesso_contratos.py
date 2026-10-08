"""Adaptadores do acesso do beneficiario (DL-0083) com schemas SINTETICOS minusculos (nao e' copia).

Os contratos reais (`subject-verification` e `consent-record`, manifest v1.3) estao sendo escritos na AMH
e NAO estao pinados: `test_sem_pin_v1_3_os_construtores_recusam` fecha isso, e `test_pin_v1_3_*` prova que o
carregador do pin sabe ler o bloco quando ele existir (sem tocar o lock real).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace

import pytest
import yaml

from maezo.adapters.amh import consent_record as cr
from maezo.adapters.amh import subject_verification as sv
from maezo.adapters.amh.contract import (
    V1_3_ARTIFACT_PATHS,
    V1_3_LOCK_KEY,
    AmhContractPinError,
    load_contract_pin,
)
from maezo.ports.consent_record import ConsentRecordPort
from maezo.ports.errors import PortFailureReason as Reason
from maezo.ports.errors import PortResult
from maezo.ports.subject_verification import SubjectVerificationPort

HASH = "a" * 64


def _openapi_verificacao(parametro: str = "ref") -> bytes:
    """A forma CONFERE do #212: `POST /interop/identity/v1/subjects/{ref}/verify` -> `{portable_subject_ref,
    confere}`. O nome do parametro da rota nao e' assumido pelo adaptador (`parametro`)."""
    return yaml.safe_dump(
        {
            "openapi": "3.0.3",
            "servers": [{"url": "/interop/identity/v1"}],
            "paths": {
                "/subjects/{" + parametro + "}/verify": {
                    "post": {
                        "parameters": [
                            {"name": parametro, "in": "path", "required": True, "schema": {"type": "string"}}
                        ],
                        "requestBody": {
                            "content": {
                                "application/json": {"schema": {"$ref": "#/components/schemas/Pedido"}}
                            }
                        },
                        "responses": {
                            "200": {
                                "content": {
                                    "application/json": {"schema": {"$ref": "#/components/schemas/Resposta"}}
                                }
                            }
                        },
                    }
                }
            },
            "components": {
                "schemas": {
                    "Pedido": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["fator", "verification_hash", "hash_scheme", "purpose_of_use"],
                        "properties": {
                            "fator": {"type": "string", "enum": ["cpf", "cpfdob"]},
                            "verification_hash": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                            "hash_scheme": {"type": "string", "enum": ["amh-subject-verify-v1"]},
                            "purpose_of_use": {"type": "string"},
                        },
                    },
                    "Resposta": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["portable_subject_ref", "confere", "fonte_atualizada_em"],
                        "properties": {
                            "portable_subject_ref": {"type": "string"},
                            "confere": {"type": "boolean"},
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


def _openapi_consentimento() -> bytes:
    campos = [
        "amh_tenant", "portable_subject_ref", "scope", "decision", "consent_text_version",
        "consent_text_sha256", "decided_at", "channel", "idempotency_key", "purpose_of_use",
    ]  # fmt: skip
    return yaml.safe_dump(
        {
            "openapi": "3.0.3",
            "servers": [{"url": "/interop/consent/v1"}],
            "paths": {
                "/consents": {
                    "post": {
                        "requestBody": {
                            "content": {
                                "application/json": {"schema": {"$ref": "#/components/schemas/Pedido"}}
                            }
                        },
                        "responses": {
                            "200": {
                                "content": {
                                    "application/json": {"schema": {"$ref": "#/components/schemas/Resposta"}}
                                }
                            }
                        },
                    }
                }
            },
            "components": {
                "schemas": {
                    "Pedido": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": campos,
                        "properties": {
                            **{c: {"type": "string"} for c in campos},
                            "decision": {"type": "string", "enum": ["granted", "revoked"]},
                        },
                    },
                    "Resposta": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["consent_ref", "recorded"],
                        "properties": {"consent_ref": {"type": "string"}, "recorded": {"type": "boolean"}},
                    },
                }
            },
        }
    ).encode()


class ExecVerificacao(sv.GovernedSubjectVerificationExecutor):
    registered_operations = frozenset({sv.OP_VERIFY})

    def __init__(self) -> None:
        self.calls: list[sv.GovernedSubjectVerificationRequest] = []
        self.status = 200
        self.corpo: object = {**_RESP, "confere": True}
        self.refusal: Reason | None = None

    async def execute(self, request):  # type: ignore[no-untyped-def]
        self.calls.append(request)
        if self.refusal is not None:
            return PortResult.refused(self.refusal)
        return PortResult.ok(
            sv.GovernedSubjectVerificationResponse(self.status, json.dumps(self.corpo).encode())
        )


class ExecConsentimento(cr.GovernedConsentRecordExecutor):
    registered_operations = frozenset({cr.OP_RECORD})

    def __init__(self) -> None:
        self.calls: list[cr.GovernedConsentRecordRequest] = []
        self.status = 200
        self.corpo: object = {"consent_ref": "consent-77", "recorded": True}

    async def execute(self, request):  # type: ignore[no-untyped-def]
        self.calls.append(request)
        return PortResult.ok(cr.GovernedConsentRecordResponse(self.status, json.dumps(self.corpo).encode()))


@pytest.fixture
def verificacao(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    raw = _openapi_verificacao()
    real = load_contract_pin()
    monkeypatch.setattr(
        sv,
        "load_contract_pin",
        lambda path=None: replace(real, artifact_digests={sv.ARTIFACT: hashlib.sha256(raw).hexdigest()}),
    )
    ex = ExecVerificacao()
    return sv.AmhSubjectVerificationAdapter(raw, executor=ex), ex, raw


@pytest.fixture
def consentimento(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    raw = _openapi_consentimento()
    real = load_contract_pin()
    monkeypatch.setattr(
        cr,
        "load_contract_pin",
        lambda path=None: replace(real, artifact_digests={cr.ARTIFACT: hashlib.sha256(raw).hexdigest()}),
    )
    ex = ExecConsentimento()
    return cr.AmhConsentRecordAdapter(raw, executor=ex), ex, raw


REF = "subject1"
_RESP: dict[str, object] = {"portable_subject_ref": REF, "fonte_atualizada_em": "2026-10-05T03:12:00Z"}
_V = {
    "fator": "cpf",
    "verification_hash": HASH,
    "hash_scheme": "amh-subject-verify-v1",
    "purpose_of_use": "sharing_amh_internal",
}
_C = {
    "amh_tenant": "austa",
    "scope": "atendimento_whatsapp",
    "decision": "granted",
    "consent_text_version": "wa-consent-v1",
    "consent_text_sha256": "b" * 64,
    "decided_at": "2026-10-08T12:00:00Z",
    "channel": "whatsapp",
    "idempotency_key": "c" * 64,
    "purpose_of_use": "sharing_amh_internal",
}


def test_sem_pin_v1_3_os_construtores_recusam() -> None:
    """O lock real NAO tem `manifest_v1_3`: nenhum byte e' aceito (fail-closed por construcao)."""
    pin = load_contract_pin()
    assert sv.ARTIFACT not in pin.artifact_digests and cr.ARTIFACT not in pin.artifact_digests
    assert pin.manifest_v1_3_digest is None
    with pytest.raises(sv.SubjectVerificationContractError):
        sv.AmhSubjectVerificationAdapter(_openapi_verificacao(), executor=ExecVerificacao())
    with pytest.raises(cr.ConsentRecordContractError):
        cr.AmhConsentRecordAdapter(_openapi_consentimento(), executor=ExecConsentimento())


def test_bytes_adulterados_sao_recusados(verificacao, consentimento) -> None:  # type: ignore[no-untyped-def]
    _, ex, raw = verificacao
    with pytest.raises(sv.SubjectVerificationContractError):
        sv.AmhSubjectVerificationAdapter(raw + b"\n#x", executor=ex)
    _, ex2, raw2 = consentimento
    with pytest.raises(cr.ConsentRecordContractError):
        cr.AmhConsentRecordAdapter(raw2 + b"\n#x", executor=ex2)


async def test_verificacao_confere_manda_a_ref_na_rota_e_o_hash_no_corpo(verificacao) -> None:  # type: ignore[no-untyped-def]
    adapter, ex, _ = verificacao
    assert isinstance(adapter, SubjectVerificationPort)
    r = await adapter.verify(REF, **_V)
    assert r.succeeded and r.value is not None and r.value.confere and r.value.portable_subject_ref == REF
    [req] = ex.calls
    assert req.method == "POST" and req.path == "/interop/identity/v1/subjects/subject1/verify"
    corpo = json.loads(req.body)
    assert corpo == {**_V}  # so' fator, hash, esquema e proposito: nenhum tenant, nenhuma ref no corpo
    assert HASH not in req.path and HASH not in repr(req)


async def test_verificacao_nao_confere(verificacao) -> None:  # type: ignore[no-untyped-def]
    adapter, ex, _ = verificacao
    ex.corpo = {**_RESP, "confere": False}
    r = await adapter.verify(REF, **{**_V, "fator": "cpfdob"})
    assert r.succeeded and r.value is not None and not r.value.confere
    assert json.loads(ex.calls[0].body)["fator"] == "cpfdob"


@pytest.mark.parametrize("confere", [True, False])
async def test_indice_da_amh_nao_carregado_e_indisponivel_nunca_nao_confere(verificacao, confere) -> None:  # type: ignore[no-untyped-def]
    """#212: `fonte_atualizada_em: null` = indice fora; o `confere` dai' nao vale (nao gasta tentativa)."""
    adapter, ex, _ = verificacao
    ex.corpo = {**_RESP, "confere": confere, "fonte_atualizada_em": None}
    r = await adapter.verify(REF, **_V)
    assert r.failure is not None and r.failure.reason is Reason.UPSTREAM_UNAVAILABLE


async def test_verificacao_nome_do_parametro_da_rota_nao_e_assumido(monkeypatch: pytest.MonkeyPatch) -> None:
    raw = _openapi_verificacao("portable_subject_ref")
    real = load_contract_pin()
    monkeypatch.setattr(
        sv,
        "load_contract_pin",
        lambda path=None: replace(real, artifact_digests={sv.ARTIFACT: hashlib.sha256(raw).hexdigest()}),
    )
    ex = ExecVerificacao()
    r = await sv.AmhSubjectVerificationAdapter(raw, executor=ex).verify(REF, **_V)
    assert r.succeeded and ex.calls[0].path == "/interop/identity/v1/subjects/subject1/verify"


@pytest.mark.parametrize(
    "corpo",
    [
        # CONFERE, NAO RESOLVE: o eco de OUTRA referencia nunca vira "e' outra pessoa".
        {**_RESP, "confere": True, "portable_subject_ref": "subject2"},
        {**_RESP, "confere": False, "portable_subject_ref": "subject2"},
        {"confere": True, "fonte_atualizada_em": None},
        {**_RESP, "confere": "sim"},
        {**_RESP},
        {**_RESP, "confere": True, "extra": 1},
        {"confere": True, "portable_subject_ref": REF},
        {**_RESP, "verificado": True},
    ],
)
async def test_verificacao_resposta_fora_do_contrato(verificacao, corpo) -> None:  # type: ignore[no-untyped-def]
    adapter, ex, _ = verificacao
    ex.corpo = corpo
    r = await adapter.verify(REF, **_V)
    assert r.failure is not None and r.failure.reason is Reason.CONTRACT_VIOLATION


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        (400, Reason.INVALID_REQUEST),
        (401, Reason.NOT_AUTHENTICATED),
        (429, Reason.RATE_LIMITED),
        (500, Reason.UPSTREAM_UNAVAILABLE),
        (302, Reason.CONTRACT_VIOLATION),
    ],
)
async def test_verificacao_status_vira_recusa_fechada(verificacao, status, reason) -> None:  # type: ignore[no-untyped-def]
    adapter, ex, _ = verificacao
    ex.status = status
    ex.corpo = {"detalhe": "nao vaza"}
    r = await adapter.verify(REF, **_V)
    assert r.failure is not None and r.failure.reason is reason


@pytest.mark.parametrize(
    ("ref", "sobre"),
    [
        (REF, {"verification_hash": "nao-e-hash"}),
        (REF, {"fator": "nome"}),
        (REF, {"fator": "cpf_nascimento"}),
        (REF, {"timeout_seconds": 0}),
        ("", {}),
        ("../x", {}),
        ("a/b", {}),
        ("ref com espaco", {}),
    ],
)
async def test_verificacao_entrada_invalida_nao_chega_ao_executor(verificacao, ref, sobre) -> None:  # type: ignore[no-untyped-def]
    adapter, ex, _ = verificacao
    r = await adapter.verify(ref, **{**_V, **sobre})
    assert r.failure is not None and r.failure.reason is Reason.INVALID_REQUEST
    assert ex.calls == []


async def test_verificacao_recusa_do_executor_e_propagada(verificacao) -> None:  # type: ignore[no-untyped-def]
    adapter, ex, _ = verificacao
    ex.refusal = Reason.TIMEOUT
    assert (await adapter.verify(REF, **_V)).failure.reason is Reason.TIMEOUT  # type: ignore[union-attr]


async def test_consentimento_ok_e_idempotente(consentimento) -> None:  # type: ignore[no-untyped-def]
    adapter, ex, _ = consentimento
    assert isinstance(adapter, ConsentRecordPort)
    r = await adapter.record("subject1", **_C)
    assert r.succeeded and r.value is not None and r.value.consent_ref == "consent-77" and r.value.recorded
    [req] = ex.calls
    assert req.path == "/interop/consent/v1/consents" and json.loads(req.body)["decision"] == "granted"
    assert json.loads(req.body)["idempotency_key"] == "c" * 64


async def test_consentimento_decisao_invalida_e_resposta_ruim(consentimento) -> None:  # type: ignore[no-untyped-def]
    adapter, ex, _ = consentimento
    assert (
        await adapter.record("subject1", **{**_C, "decision": "talvez"})
    ).failure.reason is Reason.INVALID_REQUEST  # type: ignore[union-attr]
    ex.corpo = {"consent_ref": "", "recorded": True}
    assert (await adapter.record("subject1", **_C)).failure.reason is Reason.CONTRACT_VIOLATION  # type: ignore[union-attr]
    ex.status = 403
    ex.corpo = {"reason": "consent_denied"}
    assert (await adapter.record("subject1", **_C)).failure.reason is Reason.CONSENT_REQUIRED  # type: ignore[union-attr]


# --- o carregador do pin sabe ler o bloco v1.3 ------------------------------------------------------
def _pin_bruto_com_v1_3(**sobre: object) -> dict[str, object]:
    raw = json.loads(load_contract_pin().source_path.read_text(encoding="utf-8"))
    bloco = {
        "provenance": {
            "status": "PUBLISHED",
            "amh_commit_sha": "deadbeef",
            "evidence_id": "XRG2-AMH-DEV-GHA-99999999999",
        },
        "manifest_pin": {"path": "schemas/contracts/maezo/v1.3/contract-manifest.yaml", "sha256": "e" * 64},
        "publication": {"publication_run_id": "99999999999"},
        "compatibility_report": {"result": "PASSED"},
        "xrg3_verification": {"verified_by": "tester"},
        "artifacts": [{"path": p, "sha256": "f" * 64} for p in V1_3_ARTIFACT_PATHS],
    }
    bloco.update(sobre)  # type: ignore[arg-type]
    raw[V1_3_LOCK_KEY] = bloco
    return raw


def test_pin_v1_3_e_lido_quando_presente_e_completo(tmp_path) -> None:  # type: ignore[no-untyped-def]
    caminho = tmp_path / "lock.json"
    caminho.write_text(json.dumps(_pin_bruto_com_v1_3()), encoding="utf-8")
    pin = load_contract_pin(caminho)
    assert pin.manifest_v1_3_digest == "e" * 64
    assert set(V1_3_ARTIFACT_PATHS) <= set(pin.artifact_digests)


def test_pin_v1_3_incompleto_ou_reusando_evidencia_e_recusado(tmp_path) -> None:  # type: ignore[no-untyped-def]
    caminho = tmp_path / "lock.json"
    caminho.write_text(json.dumps(_pin_bruto_com_v1_3(artifacts=[])), encoding="utf-8")
    with pytest.raises(AmhContractPinError):
        load_contract_pin(caminho)
    base = load_contract_pin()
    caminho.write_text(
        json.dumps(
            _pin_bruto_com_v1_3(
                provenance={"status": "PUBLISHED", "amh_commit_sha": "x", "evidence_id": base.evidence_id}
            )
        ),
        encoding="utf-8",
    )
    with pytest.raises(AmhContractPinError):
        load_contract_pin(caminho)
