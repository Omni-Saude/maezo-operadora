"""Executor da resolucao pelo DOCUMENTO do telefone sem cadastro (DL-0084) — sem rede.

Rota fixa, corpo fechado (dois hashes, esquema, proposito e o tenant DA composicao), auditoria antes do
despacho sem os hashes, sem redirect; e a composicao do acesso exige o adaptador (fail-closed sem pin).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from maezo.adapters.amh.document_resolution import (
    OP_RESOLVE_DOCUMENT,
    PATH,
    DocumentResolutionContractError,
    GovernedDocumentResolutionRequest,
)
from maezo.gateway.amh_interop import (
    HASH_SCHEME_VERIFICACAO,
    SCOPE_BY_OPERATION,
    SCOPE_VERIFY,
    AmhDocumentResolutionExecutor,
)
from maezo.gateway.effect_classes import OPERATIONS
from maezo.gateway.tool_registry import build_amh_interop
from maezo.ports.errors import PortFailureReason as Reason
from tests.unit.gateway.test_amh_interop import ORIGIN, PURPOSE, Audit, Servidor, _comum, _seam
from tests.unit.gateway.test_amh_interop import relogio as relogio  # noqa: F401  (fixture)
from tests.unit.gateway.test_amh_interop import servidor as servidor  # noqa: F401  (fixture)
from tests.unit.gateway.test_amh_interop import tokens as tokens  # noqa: F401  (fixture)
from tests.unit.gateway.test_amh_interop_acesso import _settings_acesso

pytestmark = pytest.mark.anyio

H_CPF = "a" * 64
H_CPFDOB = "b" * 64


def _corpo(**sobre: Any) -> dict[str, Any]:
    corpo: dict[str, Any] = {
        "cpf_hash": H_CPF,
        "cpfdob_hash": H_CPFDOB,
        "hash_scheme": HASH_SCHEME_VERIFICACAO,
        "purpose_of_use": PURPOSE,
        "amh_tenant": "omni",
    }
    corpo.update(sobre)
    return corpo


def _pedido(corpo: dict[str, Any] | None = None, **sobre: Any) -> GovernedDocumentResolutionRequest:
    base: dict[str, Any] = {
        "operation": OP_RESOLVE_DOCUMENT,
        "method": "POST",
        "path": PATH,
        "body": json.dumps(corpo or _corpo()).encode(),
        "timeout_seconds": 5.0,
    }
    base.update(sobre)
    return GovernedDocumentResolutionRequest(**base)


def test_operacao_catalogada_com_o_escopo_da_conferencia() -> None:
    assert PATH == "/interop/identity/v1/resolve-by-document"
    assert SCOPE_BY_OPERATION[OP_RESOLVE_DOCUMENT] == SCOPE_VERIFY
    assert OPERATIONS[OP_RESOLVE_DOCUMENT].action_class == "leitura_phi_clinica"


async def test_despacha_post_na_rota_fixa_audita_antes_e_nao_audita_os_hashes(
    servidor: Servidor, tokens: Any
) -> None:
    audit = Audit(servidor)
    executor = AmhDocumentResolutionExecutor(amh_tenant="omni", **_comum(servidor, tokens, audit))
    servidor.respostas[PATH] = httpx.Response(200, json={"resultado": "nenhum", "fonte_atualizada_em": None})
    resultado = await executor.execute(_pedido())
    assert resultado.succeeded, resultado.failure
    [pedido] = servidor.ao_servico()
    assert pedido.method == "POST" and str(pedido.url) == ORIGIN + PATH
    assert H_CPF not in str(pedido.url) and json.loads(pedido.content) == _corpo()
    assert audit.pedidos_no_emit == [0]
    rendido = json.dumps([r.details for r in audit.registros], default=str)
    assert H_CPF not in rendido and H_CPFDOB not in rendido
    assert audit.registros[0].action == OP_RESOLVE_DOCUMENT


@pytest.mark.parametrize(
    "corpo",
    [
        {"cpf_hash": "ZZ"},
        {"cpfdob_hash": "A" * 64},
        {"cpfdob_hash": H_CPF},  # os dois iguais nao sao os dois fatores
        {"hash_scheme": "amh-phone-lookup-v1"},
        {"amh_tenant": "outro"},
        {"purpose_of_use": "outro"},
        {"cpf": "52998224725"},
    ],
)
async def test_fora_da_forma_nunca_e_despachado(
    servidor: Servidor, tokens: Any, corpo: dict[str, Any]
) -> None:
    audit = Audit()
    executor = AmhDocumentResolutionExecutor(amh_tenant="omni", **_comum(servidor, tokens, audit))
    resultado = await executor.execute(_pedido(_corpo(**corpo)))
    assert resultado.failure is not None and resultado.failure.reason is Reason.INVALID_REQUEST
    assert servidor.ao_servico() == [] and audit.registros == []


@pytest.mark.parametrize(
    "sobre",
    [
        {"path": "/interop/identity/v1/resolve-by-document/x"},
        {"path": "/interop/identity/v1/subjects/resolve-by-document"},
        {"method": "GET"},
        {"operation": "amh.verify_subject"},
        {"timeout_seconds": 999.0},
    ],
)
async def test_rota_metodo_e_prazo_sao_fixos(servidor: Servidor, tokens: Any, sobre: dict[str, Any]) -> None:
    executor = AmhDocumentResolutionExecutor(amh_tenant="omni", **_comum(servidor, tokens, Audit()))
    resultado = await executor.execute(_pedido(**sobre))
    assert resultado.failure is not None and resultado.failure.reason is Reason.INVALID_REQUEST


async def test_429_e_redirect_viram_recusa(servidor: Servidor, tokens: Any) -> None:
    executor = AmhDocumentResolutionExecutor(amh_tenant="omni", **_comum(servidor, tokens, Audit()))
    servidor.respostas[PATH] = httpx.Response(302, headers={"location": "http://evil/"})
    r = await executor.execute(_pedido())
    assert r.failure is not None and r.failure.reason is Reason.UPSTREAM_UNAVAILABLE
    assert all(p.url.host != "evil" for p in servidor.pedidos)
    servidor.respostas[PATH] = httpx.Response(429, json={"detalhe": "limite"})
    r = await executor.execute(_pedido())
    assert r.failure is not None and r.failure.reason is Reason.RATE_LIMITED


def test_composicao_com_acesso_exige_a_rota_de_documento_no_artefato_pinado(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Com a conferencia e o consentimento sobindo, o adaptador de documento recusa sem pin: e o boot cai."""
    from maezo.adapters.amh import billing_status as bs
    from maezo.adapters.amh import consent_record as cr
    from maezo.adapters.amh import subject_resolution as sr
    from maezo.adapters.amh import subject_verification as sv

    class _Adapter:
        def __init__(self, *_: Any, **__: Any) -> None:
            pass

    for modulo, nome in (
        (bs, "AmhBillingStatusAdapter"),
        (sr, "AmhSubjectResolutionAdapter"),
        (sv, "AmhSubjectVerificationAdapter"),
        (cr, "AmhConsentRecordAdapter"),
    ):
        monkeypatch.setattr(modulo, nome, _Adapter)
    with pytest.raises(DocumentResolutionContractError):
        build_amh_interop(
            settings=_settings_acesso(tmp_path),
            seam=_seam(),
            audit=Audit(),
            agent_version="helena@v0",
            incluir_acesso=True,
        )
