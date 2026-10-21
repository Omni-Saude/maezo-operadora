"""Executores do acesso do beneficiario (DL-0083): verificacao por fator, registro de consentimento, hasher,
escopos e composicao fail-closed — sem rede.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from maezo.adapters.amh.consent_record import OP_RECORD, GovernedConsentRecordRequest
from maezo.adapters.amh.subject_verification import OP_VERIFY, GovernedSubjectVerificationRequest
from maezo.gateway.amh_interop import (
    HASH_SCHEME_VERIFICACAO,
    SCOPE_BY_OPERATION,
    SCOPE_CONSENT_WRITE,
    SCOPE_VERIFY,
    AmhConsentRecordExecutor,
    AmhInteropCompositionError,
    AmhSubjectVerificationExecutor,
    AmhSubjectVerifyHasher,
)
from maezo.gateway.tool_registry import build_amh_interop
from maezo.ports.errors import PortFailureReason as Reason
from tests.unit.gateway.test_amh_interop import (
    ORIGIN,
    PURPOSE,
    SECRET,
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

#: CONFERE, NAO RESOLVE (AMH #212): a referencia candidata vai na rota.
VERIFY_PATH = "/interop/identity/v1/subjects/s1/verify"
CONSENT_PATH = "/interop/consent/v1/consents"
HASH = "a" * 64
CHAVE = "chave-de-verificacao-sintetica"


def _corpo_verificacao(**sobre: Any) -> dict[str, Any]:
    corpo: dict[str, Any] = {
        "hash_scheme": HASH_SCHEME_VERIFICACAO,
        "fator": "cpf",
        "verification_hash": HASH,
        "purpose_of_use": PURPOSE,
    }
    corpo.update(sobre)
    return corpo


def _pedido_verificacao(
    corpo: dict[str, Any] | None = None, **sobre: Any
) -> GovernedSubjectVerificationRequest:
    base: dict[str, Any] = {
        "operation": OP_VERIFY,
        "method": "POST",
        "path": VERIFY_PATH,
        "body": json.dumps(corpo or _corpo_verificacao()).encode(),
        "timeout_seconds": 5.0,
    }
    base.update(sobre)
    return GovernedSubjectVerificationRequest(**base)


def _corpo_consentimento(**sobre: Any) -> dict[str, Any]:
    corpo: dict[str, Any] = {
        "amh_tenant": "omni",
        "portable_subject_ref": "subject1",
        "scope": "atendimento_whatsapp",
        "decision": "granted",
        "consent_text_version": "wa-consent-v1",
        "consent_text_sha256": "b" * 64,
        "decided_at": "2026-10-08T12:00:00Z",
        "channel": "whatsapp",
        "idempotency_key": "c" * 64,
        "purpose_of_use": PURPOSE,
    }
    corpo.update(sobre)
    return corpo


def _pedido_consentimento(corpo: dict[str, Any] | None = None, **sobre: Any) -> GovernedConsentRecordRequest:
    base: dict[str, Any] = {
        "operation": OP_RECORD,
        "method": "POST",
        "path": CONSENT_PATH,
        "body": json.dumps(corpo or _corpo_consentimento()).encode(),
        "timeout_seconds": 5.0,
    }
    base.update(sobre)
    return GovernedConsentRecordRequest(**base)


def test_escopos_do_acesso() -> None:
    assert SCOPE_VERIFY == "interop/subject.verify" and SCOPE_CONSENT_WRITE == "interop/consent.write"
    assert SCOPE_BY_OPERATION[OP_VERIFY] == SCOPE_VERIFY
    assert SCOPE_BY_OPERATION[OP_RECORD] == SCOPE_CONSENT_WRITE
    assert HASH_SCHEME_VERIFICACAO == "amh-subject-verify-v1"


def test_hasher_do_esquema_amh_subject_verify_v1() -> None:
    h = AmhSubjectVerifyHasher(key=CHAVE, amh_tenant="austa")
    esperado_cpf = hmac.new(CHAVE.encode(), b"austa:cpf:52998224725", hashlib.sha256).hexdigest()
    esperado_dob = hmac.new(CHAVE.encode(), b"austa:cpfdob:52998224725:19850307", hashlib.sha256).hexdigest()
    assert h.hash_cpf("52998224725") == esperado_cpf
    assert h.hash_cpf_nascimento("52998224725", "19850307") == esperado_dob
    assert CHAVE not in repr(h) and CHAVE not in str(h.__slots__)
    for ruim in ("5299822472", "529.982.247-25", "abcdefghijk", "", "５２９９８２２４７２５"):
        assert h.hash_cpf(ruim) is None
    assert h.hash_cpf_nascimento("52998224725", "1985-03-07") is None


async def test_verificacao_despacha_post_na_rota_fixa_audita_antes_e_nao_audita_o_hash(
    servidor: Servidor, tokens: Any
) -> None:
    audit = Audit(servidor)
    executor = AmhSubjectVerificationExecutor(amh_tenant="omni", **_comum(servidor, tokens, audit))
    servidor.respostas[VERIFY_PATH] = httpx.Response(
        200, json={"confere": True, "portable_subject_ref": "s1"}
    )
    resultado = await executor.execute(_pedido_verificacao())
    assert resultado.succeeded, resultado.failure
    [pedido] = servidor.ao_servico()
    assert pedido.method == "POST" and str(pedido.url) == ORIGIN + VERIFY_PATH and HASH not in str(pedido.url)
    assert json.loads(pedido.content)["verification_hash"] == HASH
    assert audit.pedidos_no_emit == [0]
    assert HASH not in json.dumps([r.details for r in audit.registros], default=str)
    assert audit.registros[0].action == OP_VERIFY and audit.registros[0].details["phase"].startswith("READ")


@pytest.mark.parametrize(
    "corpo",
    [
        {"fator": "nome"},
        {"fator": "cpf_nascimento"},
        {"hash_scheme": "amh-phone-lookup-v1"},
        {"verification_hash": "ZZ"},
        {"verification_hash": "A" * 64},
        {"amh_tenant": "outro"},
        {"purpose_of_use": "outro"},
        {"extra": 1},
    ],
)
async def test_verificacao_fora_da_forma_nunca_e_despachada(
    servidor: Servidor, tokens: Any, corpo: dict[str, Any]
) -> None:
    audit = Audit()
    executor = AmhSubjectVerificationExecutor(amh_tenant="omni", **_comum(servidor, tokens, audit))
    resultado = await executor.execute(_pedido_verificacao(_corpo_verificacao(**corpo)))
    assert resultado.failure is not None and resultado.failure.reason is Reason.INVALID_REQUEST
    assert servidor.ao_servico() == [] and audit.registros == []


@pytest.mark.parametrize(
    "sobre",
    [
        {"path": "/interop/identity/v1/subjects/s1/verify/x"},
        {"path": "/interop/subject-verification/v1/subjects/verify"},
        {"path": "/interop/identity/v1/subjects//verify"},
        {"path": "/interop/identity/v1/subjects/s1%2F..%2Fx/verify"},
        {"path": "/interop/identity/v1/subjects/../verify"},
        {"method": "GET"},
        {"operation": "amh.x"},
        {"timeout_seconds": 999.0},
    ],
)
async def test_verificacao_rota_metodo_e_prazo_sao_fixos(
    servidor: Servidor, tokens: Any, sobre: dict[str, Any]
) -> None:
    executor = AmhSubjectVerificationExecutor(amh_tenant="omni", **_comum(servidor, tokens, Audit()))
    resultado = await executor.execute(_pedido_verificacao(**sobre))
    assert resultado.failure is not None and resultado.failure.reason is Reason.INVALID_REQUEST


async def test_verificacao_nao_segue_redirect_e_status_vira_recusa(servidor: Servidor, tokens: Any) -> None:
    executor = AmhSubjectVerificationExecutor(amh_tenant="omni", **_comum(servidor, tokens, Audit()))
    for status, reason in (
        (302, Reason.UPSTREAM_UNAVAILABLE),
        (500, Reason.UPSTREAM_UNAVAILABLE),
        (404, Reason.NOT_FOUND),
    ):
        servidor.respostas[VERIFY_PATH] = httpx.Response(status, headers={"location": "http://evil/"})
        r = await executor.execute(_pedido_verificacao())
        assert r.failure is not None and r.failure.reason is reason
    assert all(p.url.host != "evil" for p in servidor.pedidos)


async def test_consentimento_despacha_post_e_marca_a_fase_de_escrita(servidor: Servidor, tokens: Any) -> None:
    audit = Audit(servidor)
    executor = AmhConsentRecordExecutor(amh_tenant="omni", **_comum(servidor, tokens, audit))
    servidor.respostas[CONSENT_PATH] = httpx.Response(200, json={"consent_ref": "c1", "recorded": True})
    resultado = await executor.execute(_pedido_consentimento())
    assert resultado.succeeded, resultado.failure
    [pedido] = servidor.ao_servico()
    assert pedido.method == "POST" and str(pedido.url) == ORIGIN + CONSENT_PATH
    assert audit.pedidos_no_emit == [0]
    assert audit.registros[0].details["phase"] == "WRITE_DISPATCHED_UNDER_SHADOW_GATE"
    assert "subject1" not in json.dumps(audit.registros[0].details, default=str)


@pytest.mark.parametrize(
    "corpo",
    [
        {"scope": "outro"},
        {"channel": "sms"},
        {"decision": "talvez"},
        {"consent_text_sha256": "x"},
        {"idempotency_key": "curta"},
        {"decided_at": "ontem"},
        {"portable_subject_ref": "../x"},
        {"purpose_of_use": "outro"},
        {"extra": 1},
    ],
)
async def test_consentimento_fora_da_forma_nunca_e_despachado(
    servidor: Servidor, tokens: Any, corpo: dict[str, Any]
) -> None:
    audit = Audit()
    executor = AmhConsentRecordExecutor(amh_tenant="omni", **_comum(servidor, tokens, audit))
    resultado = await executor.execute(_pedido_consentimento(_corpo_consentimento(**corpo)))
    assert resultado.failure is not None and resultado.failure.reason is Reason.INVALID_REQUEST
    assert servidor.ao_servico() == [] and audit.registros == []


async def test_zona_geral_e_exigida() -> None:
    from maezo.gateway.amh_interop import CognitoClientCredentials

    with pytest.raises(AmhInteropCompositionError):
        AmhSubjectVerificationExecutor(
            amh_tenant="omni",
            seam=_seam("phi"),
            origin=ORIGIN,
            tokens=CognitoClientCredentials(
                token_url="https://c.example/oauth2/token",
                client_id="c",
                client_secret="s" * 8,
                scopes=("interop/subject.verify",),
            ),
            audit=Audit(),
            purpose_of_use=PURPOSE,
            agent_version="helena@v0",
        )


# --- composicao ------------------------------------------------------------------------------------
_ESCOPOS = (
    "interop/billing.read interop/subject.resolve interop/profile.read "
    "interop/subject.verify interop/consent.write"
)


def _settings_acesso(tmp_path: Path, **sobre: Any) -> Any:
    (tmp_path / "ver.yaml").write_bytes(b"v")
    (tmp_path / "con.yaml").write_bytes(b"c")
    (tmp_path / "doc.yaml").write_bytes(b"d")
    base = {
        "amh_interop_scopes": _ESCOPOS,
        "amh_subject_verify_key": CHAVE,
        "amh_subject_verification_openapi_path": str(tmp_path / "ver.yaml"),
        "amh_consent_record_openapi_path": str(tmp_path / "con.yaml"),
        "amh_document_resolution_openapi_path": str(tmp_path / "doc.yaml"),
    }
    base.update(sobre)
    return _settings(tmp_path, **base)


@pytest.mark.parametrize(
    ("sobre", "esperado"),
    [
        ({"amh_subject_verify_key": None}, "amh_subject_verify_key"),
        ({"amh_subject_verification_openapi_path": None}, "amh_subject_verification_openapi_path"),
        ({"amh_consent_record_openapi_path": None}, "amh_consent_record_openapi_path"),
        ({"amh_document_resolution_openapi_path": None}, "amh_document_resolution_openapi_path"),
        (
            {"amh_interop_scopes": "interop/billing.read interop/subject.resolve interop/profile.read"},
            "escopos",
        ),
        (
            {"amh_interop_scopes": "interop/billing.read interop/subject.resolve interop/subject.verify"},
            "escopos",
        ),
    ],
)
def test_composicao_do_acesso_recusa_sem_cada_peca(
    tmp_path: Path, sobre: dict[str, Any], esperado: str
) -> None:
    with pytest.raises(AmhInteropCompositionError, match=esperado) as exc:
        build_amh_interop(
            settings=_settings_acesso(tmp_path, **sobre),
            seam=_seam(),
            audit=Audit(),
            agent_version="helena@v0",
            incluir_acesso=True,
        )
    assert SECRET not in str(exc.value) and CHAVE not in str(exc.value)


def test_composicao_sem_acesso_nao_le_nada_do_acesso(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from maezo.adapters.amh import billing_status as bs
    from maezo.adapters.amh import subject_resolution as sr

    class _Adapter:
        def __init__(self, *_: Any, **__: Any) -> None:
            pass

    monkeypatch.setattr(bs, "AmhBillingStatusAdapter", _Adapter)
    monkeypatch.setattr(sr, "AmhSubjectResolutionAdapter", _Adapter)
    interop = build_amh_interop(
        settings=_settings(tmp_path), seam=_seam(), audit=Audit(), agent_version="x@v0"
    )
    assert interop.verification is None and interop.consents is None and interop.verify_hasher is None
    assert len(interop._executors) == 2


def test_composicao_com_acesso_sem_pin_v1_3_recusa_pelo_contrato(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """O lock real NAO tem `manifest_v1_3`: com tudo configurado, o adaptador recusa — e com ele o boot."""
    from maezo.adapters.amh import billing_status as bs
    from maezo.adapters.amh import subject_resolution as sr
    from maezo.adapters.amh.subject_verification import SubjectVerificationContractError

    class _Adapter:
        def __init__(self, *_: Any, **__: Any) -> None:
            pass

    monkeypatch.setattr(bs, "AmhBillingStatusAdapter", _Adapter)
    monkeypatch.setattr(sr, "AmhSubjectResolutionAdapter", _Adapter)
    with pytest.raises(SubjectVerificationContractError):
        build_amh_interop(
            settings=_settings_acesso(tmp_path),
            seam=_seam(),
            audit=Audit(),
            agent_version="helena@v0",
            incluir_acesso=True,
        )
