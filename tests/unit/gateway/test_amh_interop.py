"""Executores reais da fonte AMH do Lucas (`gateway/amh_interop.py`), sem rede: `httpx.MockTransport`.

Prende as decisoes do dono de 06/10/2026: sombra (gate ordinario), Zona Geral, base legal so' como
metadado de auditoria, hash `amh-phone-lookup-v1` e o caminho fail-closed da composicao.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
import structlog

from maezo.adapters.amh import billing_status as bs
from maezo.adapters.amh import subject_resolution as sr
from maezo.adapters.amh.billing_status import GovernedBillingStatusRequest
from maezo.adapters.amh.contract import load_contract_pin
from maezo.adapters.amh.subject_resolution import GovernedSubjectResolutionRequest
from maezo.gateway import amh_interop
from maezo.gateway.amh_interop import (
    AmhBillingStatusExecutor,
    AmhInteropCompositionError,
    AmhPhoneLookupHasher,
    AmhSubjectResolutionExecutor,
    CognitoClientCredentials,
    validar_origem,
)
from maezo.gateway.effect_pep import EffectDecision
from maezo.gateway.seams import SeamContext
from maezo.gateway.seams._base import LacunaDeclaradaDeniedError
from maezo.gateway.tool_registry import build_amh_interop
from maezo.ports.errors import PortFailureReason as Reason
from tests.unit.adapters.amh.test_billing_status import _corpo as _corpo_billing
from tests.unit.adapters.amh.test_billing_status import consumer as billing_consumer  # noqa: F401
from tests.unit.adapters.amh.test_subject_resolution import _contrato as _contrato_sr

pytestmark = pytest.mark.anyio

TOKEN_URL = "https://amh-maezo-bpm-dev.auth.sa-east-1.amazoncognito.com/oauth2/token"
ORIGIN = "http://internal-amh-interop.sa-east-1.elb.amazonaws.com"
SECRET = "segredo-do-cliente-SINTETICO"
ACCESS_TOKEN = "eyJ.token.SINTETICO"
PURPOSE = "sharing_amh_internal"
PHONE_KEY = "chave-dedicada-do-telefone-SINTETICA"
BILLING_PATH = "/interop/billing-status/v1/subjects/subject1/billing/status"
PROFILE_PATH = "/interop/subject-resolution/v1/subjects/subject1/profile"
RESOLVE_PATH = "/interop/subject-resolution/v1/subjects/resolve-by-phone"


class Servidor:
    """O Cognito e o servico interop num `MockTransport` so', gravando cada pedido recebido."""

    def __init__(self) -> None:
        self.pedidos: list[httpx.Request] = []
        self.token_status = 200
        self.expires_in = 3600
        self.respostas: dict[str, httpx.Response] = {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        # O transporte real devolve o corpo como STREAM ainda nao lido; o executor le' em pedacos.
        pronta = self._responder(request)
        return httpx.Response(
            pronta.status_code, headers=pronta.headers, stream=httpx.ByteStream(pronta.content)
        )

    def _responder(self, request: httpx.Request) -> httpx.Response:
        self.pedidos.append(request)
        if str(request.url) == TOKEN_URL:
            if self.token_status != 200:
                return httpx.Response(self.token_status)
            return httpx.Response(
                200,
                json={"access_token": ACCESS_TOKEN, "token_type": "Bearer", "expires_in": self.expires_in},
            )
        resposta = self.respostas.get(request.url.path)
        if resposta is not None:
            return resposta
        return httpx.Response(200, json={"ok": True})

    def factory(self) -> httpx.AsyncBaseTransport:
        return httpx.MockTransport(self)

    def ao_servico(self) -> list[httpx.Request]:
        return [p for p in self.pedidos if str(p.url) != TOKEN_URL]


class Audit:
    def __init__(self, servidor: Servidor | None = None) -> None:
        self.registros: list[Any] = []
        self.pedidos_no_emit: list[int] = []
        self._servidor = servidor
        self.receipt = "a" * 64

    async def emit(self, record: Any) -> str:
        self.registros.append(record)
        if self._servidor is not None:
            self.pedidos_no_emit.append(len(self._servidor.ao_servico()))
        return self.receipt


def _seam(zona: str = "general") -> SeamContext:
    return SeamContext(tenant="amh", principal="lucas", phi_zone=zona)


@pytest.fixture
def servidor() -> Servidor:
    return Servidor()


@pytest.fixture
def relogio() -> list[float]:
    return [1000.0]


@pytest.fixture
def tokens(servidor: Servidor, relogio: list[float]) -> CognitoClientCredentials:
    return CognitoClientCredentials(
        token_url=TOKEN_URL,
        client_id="maezo-operadora-interop",
        client_secret=SECRET,
        scopes=("interop/billing.read", "interop/subject.resolve", "interop/profile.read"),
        transport_factory=servidor.factory,
        clock=lambda: relogio[0],
    )


def _comum(
    servidor: Servidor, tokens: CognitoClientCredentials, audit: Audit, **sobre: Any
) -> dict[str, Any]:
    base: dict[str, Any] = {
        "seam": _seam(),
        "origin": ORIGIN,
        "tokens": tokens,
        "audit": audit,
        "purpose_of_use": PURPOSE,
        "agent_version": "lucas@v0",
        "transport_factory": servidor.factory,
    }
    base.update(sobre)
    return base


def _pedido_billing(**sobre: Any) -> GovernedBillingStatusRequest:
    base: dict[str, Any] = {
        "operation": "amh.get_billing_status",
        "path": BILLING_PATH,
        "query": (("purpose_of_use", PURPOSE), ("janela_meses", 12)),
        "consent_decision_ref": "base-legal:execucao-de-contrato",
        "timeout_seconds": 5.0,
    }
    base.update(sobre)
    return GovernedBillingStatusRequest(**base)


def _corpo_resolve(**sobre: Any) -> bytes:
    corpo = {
        "amh_tenant": "omni",
        "phone_hash": "b" * 64,
        "hash_scheme": "amh-phone-lookup-v1",
        "purpose_of_use": PURPOSE,
    }
    corpo.update(sobre)
    return json.dumps(corpo).encode()


def _pedido_resolve(**sobre: Any) -> GovernedSubjectResolutionRequest:
    base: dict[str, Any] = {
        "operation": "amh.resolve_subject_by_phone",
        "method": "POST",
        "path": RESOLVE_PATH,
        "query": (),
        "body": _corpo_resolve(),
        "consent_decision_ref": None,
        "timeout_seconds": 5.0,
    }
    base.update(sobre)
    return GovernedSubjectResolutionRequest(**base)


# --- token do Cognito ---------------------------------------------------------------------------


async def test_token_client_credentials_com_basic_e_cache_ate_expirar_menos_120s(
    servidor: Servidor, tokens: CognitoClientCredentials, relogio: list[float]
) -> None:
    assert await tokens.token() == ACCESS_TOKEN
    pedido = servidor.pedidos[0]
    assert pedido.method == "POST"
    assert pedido.headers["content-type"] == "application/x-www-form-urlencoded"
    basic = pedido.headers["authorization"].removeprefix("Basic ")
    import base64

    assert base64.b64decode(basic).decode() == f"maezo-operadora-interop:{SECRET}"
    corpo = pedido.content.decode()
    assert corpo.startswith("grant_type=client_credentials&scope=")
    assert "interop%2Fbilling.read%20interop%2Fsubject.resolve" in corpo
    # cache: nenhum POST novo antes de expires_in - 120s
    relogio[0] += 3600 - 121
    assert await tokens.token() == ACCESS_TOKEN
    assert len(servidor.pedidos) == 1
    relogio[0] += 2
    assert await tokens.token() == ACCESS_TOKEN
    assert len(servidor.pedidos) == 2


@pytest.mark.parametrize("status", [400, 401, 500])
async def test_token_indisponivel_e_none_sem_vazar_segredo(
    servidor: Servidor, tokens: CognitoClientCredentials, status: int
) -> None:
    servidor.token_status = status
    with structlog.testing.capture_logs() as logs:
        assert await tokens.token() is None
    assert SECRET not in repr(logs)
    assert SECRET not in repr(tokens)
    assert repr(tokens) == "CognitoClientCredentials(<redacted>)"


@pytest.mark.parametrize(
    "url",
    [
        "http://cognito.example/oauth2/token",  # segredo nunca em texto claro
        "https://user:pw@cognito.example/oauth2/token",
        "https://cognito.example/oauth2/token?x=1",
        "https://cognito.example",
    ],
)
def test_token_url_tem_de_ser_https_sem_credencial_nem_query(url: str) -> None:
    with pytest.raises(AmhInteropCompositionError):
        CognitoClientCredentials(token_url=url, client_id="c", client_secret="s", scopes=("a/b",))


# --- origem -------------------------------------------------------------------------------------


def test_origem_aceita_http_interno_e_https_sem_caminho() -> None:
    assert validar_origem(ORIGIN) == ORIGIN
    assert validar_origem(ORIGIN + "/") == ORIGIN
    assert validar_origem("https://interop.amh.internal:8443") == "https://interop.amh.internal:8443"


@pytest.mark.parametrize(
    "origem",
    [
        "ftp://interop",
        "http://user:pw@interop",
        "http://interop/interop/billing-status",
        "http://interop?x=1",
        "http://interop#f",
        "interop",
        "http://",
    ],
)
def test_origem_com_caminho_query_ou_credencial_e_recusada(origem: str) -> None:
    with pytest.raises(AmhInteropCompositionError):
        validar_origem(origem)


# --- executor de cobranca -----------------------------------------------------------------------


async def test_leitura_de_cobranca_em_sombra_audita_antes_e_despacha_na_rota_fixa(
    servidor: Servidor, tokens: CognitoClientCredentials
) -> None:
    audit = Audit(servidor)
    executor = AmhBillingStatusExecutor(**_comum(servidor, tokens, audit))
    with structlog.testing.capture_logs() as logs:
        resultado = await executor.execute(_pedido_billing())
    assert resultado.succeeded, resultado.failure
    assert resultado.value is not None and resultado.value.status_code == 200
    # decisao A: o gate rodou (linha de sombra) e, mesmo com o manifesto DRAFT (would-deny), seguiu
    sombra = [e for e in logs if e.get("operation") == "amh.get_billing_status" and "decision" in e]
    assert sombra and sombra[0]["phi_zone"] == "general"
    [pedido] = servidor.ao_servico()
    assert str(pedido.url) == ORIGIN + BILLING_PATH + "?purpose_of_use=sharing_amh_internal&janela_meses=12"
    assert pedido.headers["authorization"] == "Bearer " + ACCESS_TOKEN
    # auditoria duravel ANTES do despacho, com a base legal so' como hash
    assert audit.pedidos_no_emit == [0]
    [registro] = audit.registros
    assert registro.agent_id == "lucas" and registro.action == "amh.get_billing_status"
    assert "base-legal" not in json.dumps(registro.details)
    assert registro.details["pep_allow"] is False  # sombra: registrado, nao bloqueado
    assert ACCESS_TOKEN not in repr(logs) and SECRET not in repr(logs)


async def test_negacao_enforced_recusa_sem_token_sem_auditoria_e_sem_rede(
    servidor: Servidor, tokens: CognitoClientCredentials, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _nega(seam: SeamContext, operation: str, **_: Any) -> EffectDecision:
        raise LacunaDeclaradaDeniedError(
            EffectDecision(allow=False, enforced=True, reason="X", layer="L5", operation=operation)
        )

    monkeypatch.setattr(amh_interop, "gate", _nega)
    audit = Audit()
    executor = AmhBillingStatusExecutor(**_comum(servidor, tokens, audit))
    resultado = await executor.execute(_pedido_billing())
    assert resultado.failure is not None and resultado.failure.reason is Reason.SCOPE_NOT_SUPPORTED
    assert servidor.pedidos == [] and audit.registros == []


def test_zona_phi_nao_compoe_este_executor(servidor: Servidor, tokens: CognitoClientCredentials) -> None:
    with pytest.raises(AmhInteropCompositionError):
        AmhBillingStatusExecutor(**_comum(servidor, tokens, Audit(), seam=_seam("phi")))


@pytest.mark.parametrize(
    "sobre",
    [
        {"path": "/interop/billing-status/v1/subjects/subject1/billing/status/../x"},
        {"path": "/interop/billing-status/v1/subjects/a%2Fb/billing/status"},
        {"path": "http://evil/interop/billing-status/v1/subjects/subject1/billing/status"},
        {"operation": "amh.get_subject_profile"},
        {"query": (("purpose_of_use", "outro_proposito"),)},
        {"query": (("purpose_of_use", PURPOSE), ("x", "1"))},
        {"query": (("purpose_of_use", PURPOSE), ("purpose_of_use", PURPOSE))},
        {"query": (("purpose_of_use", PURPOSE), ("competencia", "2026-13"))},
        {"query": (("purpose_of_use", PURPOSE), ("janela_meses", 99))},
        {"consent_decision_ref": "  "},
        {"timeout_seconds": 0},
        {"timeout_seconds": 31.0},
    ],
)
async def test_pedido_fora_da_forma_nunca_e_despachado(
    servidor: Servidor, tokens: CognitoClientCredentials, sobre: dict[str, Any]
) -> None:
    audit = Audit()
    executor = AmhBillingStatusExecutor(**_comum(servidor, tokens, audit))
    resultado = await executor.execute(_pedido_billing(**sobre))
    assert resultado.failure is not None and resultado.failure.reason is Reason.INVALID_REQUEST
    assert servidor.pedidos == [] and audit.registros == []


@pytest.mark.parametrize(
    ("resposta", "motivo"),
    [
        (httpx.Response(302, headers={"location": "http://evil/"}), Reason.UPSTREAM_UNAVAILABLE),
        (httpx.Response(404), Reason.NOT_FOUND),
        (httpx.Response(429), Reason.RATE_LIMITED),
        (httpx.Response(503), Reason.UPSTREAM_UNAVAILABLE),
        (
            httpx.Response(200, text="<html/>", headers={"content-type": "text/html"}),
            Reason.CONTRACT_VIOLATION,
        ),
        (
            httpx.Response(
                200, content=b"{" + b" " * 1_048_577 + b"}", headers={"content-type": "application/json"}
            ),
            Reason.CONTRACT_VIOLATION,
        ),
    ],
)
async def test_resposta_vira_recusa_fechada_e_redirect_nunca_e_seguido(
    servidor: Servidor, tokens: CognitoClientCredentials, resposta: httpx.Response, motivo: Reason
) -> None:
    servidor.respostas[BILLING_PATH] = resposta
    executor = AmhBillingStatusExecutor(**_comum(servidor, tokens, Audit()))
    resultado = await executor.execute(_pedido_billing())
    assert resultado.failure is not None and resultado.failure.reason is motivo
    assert [p.url.host for p in servidor.ao_servico()] == [httpx.URL(ORIGIN).host]


async def test_403_passa_ao_adaptador_e_401_invalida_o_token(
    servidor: Servidor, tokens: CognitoClientCredentials
) -> None:
    executor = AmhBillingStatusExecutor(**_comum(servidor, tokens, Audit()))
    servidor.respostas[BILLING_PATH] = httpx.Response(403, json={"reason": "consent_denied"})
    proibido = await executor.execute(_pedido_billing())
    assert proibido.succeeded and proibido.value is not None and proibido.value.status_code == 403
    servidor.respostas[BILLING_PATH] = httpx.Response(401)
    nao_autenticado = await executor.execute(_pedido_billing())
    assert nao_autenticado.failure is not None and nao_autenticado.failure.reason is Reason.NOT_AUTHENTICATED
    tokens_pedidos = [p for p in servidor.pedidos if str(p.url) == TOKEN_URL]
    del servidor.respostas[BILLING_PATH]
    assert (await executor.execute(_pedido_billing())).succeeded
    # o 401 derrubou o cache: o terceiro pedido buscou um token novo
    assert len([p for p in servidor.pedidos if str(p.url) == TOKEN_URL]) == len(tokens_pedidos) + 1


async def test_sem_token_ou_recibo_de_auditoria_invalido_nada_e_despachado(
    servidor: Servidor, tokens: CognitoClientCredentials
) -> None:
    servidor.token_status = 500
    executor = AmhBillingStatusExecutor(**_comum(servidor, tokens, Audit()))
    sem_token = await executor.execute(_pedido_billing())
    assert sem_token.failure is not None and sem_token.failure.reason is Reason.NOT_AUTHENTICATED
    servidor.token_status = 200
    audit = Audit()
    audit.receipt = "nao-e-hash"
    executor = AmhBillingStatusExecutor(**_comum(servidor, tokens, audit))
    sem_recibo = await executor.execute(_pedido_billing())
    assert sem_recibo.failure is not None and sem_recibo.failure.reason is Reason.UPSTREAM_UNAVAILABLE
    assert servidor.ao_servico() == []


async def test_httpcore_em_debug_recusa_e_fechado_recusa(
    servidor: Servidor, tokens: CognitoClientCredentials
) -> None:
    executor = AmhBillingStatusExecutor(**_comum(servidor, tokens, Audit()))
    alvo = logging.getLogger("httpcore.http11")
    nivel = alvo.level
    alvo.setLevel(logging.DEBUG)
    try:
        resultado = await executor.execute(_pedido_billing())
    finally:
        alvo.setLevel(nivel)
    # Nem o Cognito e' chamado: o POST do token levaria o segredo nos cabecalhos que o DEBUG loga.
    assert resultado.failure is not None and resultado.failure.reason is Reason.NOT_AUTHENTICATED
    assert servidor.pedidos == []
    await executor.aclose()
    fechado = await executor.execute(_pedido_billing())
    assert fechado.failure is not None and fechado.failure.reason is Reason.UPSTREAM_UNAVAILABLE


# --- executor de resolucao ----------------------------------------------------------------------


async def test_resolucao_e_post_com_hash_so_no_corpo_e_nunca_na_auditoria(
    servidor: Servidor, tokens: CognitoClientCredentials
) -> None:
    audit = Audit(servidor)
    executor = AmhSubjectResolutionExecutor(amh_tenant="omni", **_comum(servidor, tokens, audit))
    with structlog.testing.capture_logs() as logs:
        resultado = await executor.execute(_pedido_resolve())
    assert resultado.succeeded, resultado.failure
    [pedido] = servidor.ao_servico()
    assert pedido.method == "POST" and str(pedido.url) == ORIGIN + RESOLVE_PATH
    assert pedido.headers["content-type"] == "application/json"
    assert json.loads(pedido.content)["phone_hash"] == "b" * 64
    assert "b" * 64 not in json.dumps(audit.registros[0].details)
    assert "b" * 64 not in repr(logs)


@pytest.mark.parametrize(
    "sobre",
    [
        {"body": _corpo_resolve(amh_tenant="outro")},
        {"body": _corpo_resolve(hash_scheme="maezo-hk1")},
        {"body": _corpo_resolve(purpose_of_use="outro")},
        {"body": _corpo_resolve(phone_hash="hk1_abc")},
        {"body": b"nao-json"},
        {"method": "GET"},
        {"query": (("purpose_of_use", PURPOSE),)},
        {"consent_decision_ref": "x"},
        {"path": RESOLVE_PATH + "/x"},
    ],
)
async def test_resolucao_fora_da_forma_nunca_e_despachada(
    servidor: Servidor, tokens: CognitoClientCredentials, sobre: dict[str, Any]
) -> None:
    executor = AmhSubjectResolutionExecutor(amh_tenant="omni", **_comum(servidor, tokens, Audit()))
    resultado = await executor.execute(_pedido_resolve(**sobre))
    assert resultado.failure is not None and resultado.failure.reason is Reason.INVALID_REQUEST
    assert servidor.pedidos == []


async def test_perfil_e_get_na_rota_fixa(servidor: Servidor, tokens: CognitoClientCredentials) -> None:
    executor = AmhSubjectResolutionExecutor(amh_tenant="omni", **_comum(servidor, tokens, Audit()))
    resultado = await executor.execute(
        GovernedSubjectResolutionRequest(
            operation="amh.get_subject_profile",
            method="GET",
            path=PROFILE_PATH,
            query=(("purpose_of_use", PURPOSE),),
            body=None,
            consent_decision_ref="base-legal:execucao-de-contrato",
            timeout_seconds=5.0,
        )
    )
    assert resultado.succeeded, resultado.failure
    assert str(servidor.ao_servico()[0].url) == ORIGIN + PROFILE_PATH + "?purpose_of_use=sharing_amh_internal"


# --- hash do telefone (decisao D) ---------------------------------------------------------------


def test_hash_amh_phone_lookup_v1_e_hmac_do_tenant_e_digitos() -> None:
    hasher = AmhPhoneLookupHasher(key=PHONE_KEY, amh_tenant="omni")
    esperado = hmac.new(PHONE_KEY.encode(), b"omni:5511987654321", hashlib.sha256).hexdigest()
    assert hasher("5511987654321") == esperado
    assert hasher("+5511987654321") == esperado
    assert hasher("551133334444") is not None  # fixo, 8 digitos
    assert PHONE_KEY not in repr(hasher)


def test_celular_sem_o_nono_digito_tem_o_mesmo_hash_do_indice_da_amh() -> None:
    """O WhatsApp entrega muitos celulares sem o 9 (`551187654321`); o indice da AMH so' tem a forma com
    o 9. As duas formas precisam dar o MESMO hash, senao a pessoa nunca e' reconhecida."""
    hasher = AmhPhoneLookupHasher(key=PHONE_KEY, amh_tenant="austa_operadora")
    com_9 = hmac.new(PHONE_KEY.encode(), b"austa_operadora:5511987654321", hashlib.sha256).hexdigest()
    assert hasher("551187654321") == com_9
    assert hasher("+551187654321") == com_9
    assert hasher("5511987654321") == com_9
    for inicio in "6789":
        assert hasher(f"5511{inicio}7654321") == hasher(f"55119{inicio}7654321")


def test_fixo_de_8_digitos_nao_ganha_o_nono_digito() -> None:
    hasher = AmhPhoneLookupHasher(key=PHONE_KEY, amh_tenant="austa_operadora")
    fixo = hmac.new(PHONE_KEY.encode(), b"austa_operadora:551133334444", hashlib.sha256).hexdigest()
    assert hasher("551133334444") == fixo
    assert hasher("551133334444") != hasher("5511933334444")


@pytest.mark.parametrize(
    "numero", ["14155550100", "5511", "55011987654321", "55 11 98765-4321", "", "５５11987654321"]
)
def test_numero_fora_do_padrao_brasileiro_nao_vira_hash(numero: str) -> None:
    assert AmhPhoneLookupHasher(key=PHONE_KEY, amh_tenant="omni")(numero) is None


def test_chave_curta_ou_tenant_invalido_recusa() -> None:
    with pytest.raises(AmhInteropCompositionError):
        AmhPhoneLookupHasher(key="curta", amh_tenant="omni")
    with pytest.raises(AmhInteropCompositionError):
        AmhPhoneLookupHasher(key=PHONE_KEY, amh_tenant="omni tenant")


# --- composicao (tool_registry.build_amh_interop) -----------------------------------------------


def _settings(tmp_path: Any, **sobre: Any) -> SimpleNamespace:
    billing = tmp_path / "billing.yaml"
    subjects = tmp_path / "subjects.yaml"
    billing.write_bytes(b"x")
    subjects.write_bytes(b"y")
    base: dict[str, Any] = {
        "tenant_id": "amh",
        "amh_interop_base_url": ORIGIN,
        "amh_interop_token_url": TOKEN_URL,
        "amh_interop_client_id": "maezo-operadora-interop",
        "amh_interop_scopes": "interop/billing.read interop/subject.resolve interop/profile.read",
        "amh_interop_client_secret": SECRET,
        "amh_phone_lookup_key": PHONE_KEY,
        "amh_interop_tenant": "omni",
        "amh_interop_purpose_of_use": PURPOSE,
        "amh_billing_status_openapi_path": str(billing),
        "amh_subject_resolution_openapi_path": str(subjects),
    }
    base.update(sobre)
    return SimpleNamespace(**base)


@pytest.mark.parametrize(
    "ausente",
    [
        "amh_interop_base_url",
        "amh_interop_client_secret",
        "amh_phone_lookup_key",
        "amh_billing_status_openapi_path",
    ],
)
def test_composicao_sem_peca_recusa_com_o_nome_e_sem_valor(tmp_path: Any, ausente: str) -> None:
    with pytest.raises(AmhInteropCompositionError) as exc:
        build_amh_interop(
            settings=_settings(tmp_path, **{ausente: None}),
            seam=_seam(),
            audit=Audit(),
            agent_version="lucas@v0",
        )
    assert ausente in str(exc.value)
    assert SECRET not in str(exc.value) and PHONE_KEY not in str(exc.value)


def test_composicao_sem_auditoria_recusa(tmp_path: Any) -> None:
    with pytest.raises(AmhInteropCompositionError, match="audit_sink"):
        build_amh_interop(settings=_settings(tmp_path), seam=_seam(), audit=None, agent_version="lucas@v0")


def test_composicao_com_bytes_fora_do_pin_recusa_pelo_contrato(tmp_path: Any) -> None:
    # Hoje nao ha' digest pinado para os dois artefatos: qualquer byte recusa (XRG-2/XRG-3).
    with pytest.raises(bs.BillingStatusContractError):
        build_amh_interop(settings=_settings(tmp_path), seam=_seam(), audit=Audit(), agent_version="lucas@v0")


def test_composicao_sem_escopo_de_cobranca_recusa(tmp_path: Any) -> None:
    with pytest.raises(AmhInteropCompositionError, match="escopos"):
        build_amh_interop(
            settings=_settings(tmp_path, amh_interop_scopes="interop/profile.read"),
            seam=_seam(),
            audit=Audit(),
            agent_version="lucas@v0",
        )


async def test_ponta_a_ponta_com_contratos_sinteticos_pinados(
    billing_consumer: Any,  # noqa: F811
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
    servidor: Servidor,
) -> None:
    """Adaptadores pinados (schemas SINTETICOS dos testes dos adaptadores) sobre os executores reais."""
    _, _, billing_raw = billing_consumer
    sr_raw = _contrato_sr().replace(b"maezo-hk1", b"amh-phone-lookup-v1")
    real_pin = load_contract_pin()
    monkeypatch.setattr(
        sr,
        "load_contract_pin",
        lambda path=None: replace(
            real_pin, artifact_digests={sr.ARTIFACT: hashlib.sha256(sr_raw).hexdigest()}
        ),
    )
    (tmp_path / "b.yaml").write_bytes(billing_raw)
    (tmp_path / "s.yaml").write_bytes(sr_raw)
    settings = _settings(
        tmp_path,
        amh_interop_purpose_of_use="purpose1",
        amh_billing_status_openapi_path=str(tmp_path / "b.yaml"),
        amh_subject_resolution_openapi_path=str(tmp_path / "s.yaml"),
    )
    servidor.respostas[BILLING_PATH] = httpx.Response(200, json=_corpo_billing())
    servidor.respostas[RESOLVE_PATH] = httpx.Response(
        200,
        json={
            "resultado": "unico",
            "candidatos": [
                {"portable_subject_ref": "subject1", "relacao": "titular", "vigencia_ativa": True}
            ],
            "qualidade": {"campos_ausentes": []},
        },
    )
    audit = Audit()
    interop = build_amh_interop(
        settings=settings,
        seam=_seam(),
        audit=audit,
        agent_version="lucas@v0",
        transport_factory=servidor.factory,
    )
    from maezo.agents.lucas.fonte_cobranca import FatosCobranca
    from maezo.agents.lucas.fonte_cobranca_amh import FonteCobrancaAmh
    from maezo.agents.lucas.identidade_amh import BaseLegalExecucaoDeContrato, ResolvedorDeSujeitoAmh

    fonte = FonteCobrancaAmh(
        billing=interop.billing,
        resolvedor=ResolvedorDeSujeitoAmh(
            port=interop.subjects,
            amh_tenant=interop.amh_tenant,
            hash_scheme=interop.hash_scheme,
            purpose_of_use=interop.purpose_of_use,
        ),
        consentimento=BaseLegalExecucaoDeContrato(),
        purpose_of_use=interop.purpose_of_use,
    )
    phone_hash = interop.phone_hasher("5511987654321")
    fatos = await fonte.fatos("pseudo-x", None, phone_hash=phone_hash)
    assert isinstance(fatos, FatosCobranca), fatos
    assert fatos.status_conciliado is False and fatos.ciclos_sem_conciliacao == 2
    assert fatos.numero_boleto == "****4821"
    resolve, billing = servidor.ao_servico()
    assert json.loads(resolve.content)["phone_hash"] == phone_hash
    assert billing.url.path == BILLING_PATH
    assert [r.action for r in audit.registros] == ["amh.resolve_subject_by_phone", "amh.get_billing_status"]
    await interop.aclose()
    fechado = await fonte.fatos("pseudo-x", None, phone_hash=phone_hash)
    assert not isinstance(fechado, FatosCobranca)


# --- governanca: catalogo, acao L0 e agent.yaml ---------------------------------------------------


def test_operacoes_da_fonte_amh_no_catalogo_e_acao_l0_l3_nao_hard() -> None:
    from pathlib import Path

    import yaml

    from maezo.gateway.effect_classes import OPERATIONS
    from maezo.gateway.pep import HARD_ACTIONS, PEP, Decision, Level, load_matrix

    raiz = Path(__file__).resolve().parents[3]
    for operacao in ("amh.get_billing_status", "amh.resolve_subject_by_phone", "amh.get_subject_profile"):
        spec = OPERATIONS[operacao]
        assert spec.action_class == "leitura_phi_clinica"
        assert spec.autonomy_action == "read_member_billing_identity"
        assert spec.tool_id is None
    matriz = load_matrix(raiz / "spec/policies/autonomy/L0-core.yaml", tenant="lucas-amh-unit")
    politica = matriz.policy_for("read_member_billing_identity")
    assert politica is not None and politica.level is Level.L3 and politica.hard is False
    assert "read_member_billing_identity" not in HARD_ACTIONS
    assert PEP(matriz).evaluate("read_member_billing_identity") is Decision.ALLOW
    congelados = yaml.safe_load(
        (raiz / "spec/policies/autonomy/_hard_frozen.yaml").read_text(encoding="utf-8")
    )
    assert "read_member_billing_identity" not in {item["action"] for item in congelados["hard_items"]}
    lucas = yaml.safe_load((raiz / "spec/agents/lucas/agent.yaml").read_text(encoding="utf-8"))
    assert "read_member_billing_identity" in lucas["autonomy_actions"]
    assert lucas["security_zone"] == "general"
