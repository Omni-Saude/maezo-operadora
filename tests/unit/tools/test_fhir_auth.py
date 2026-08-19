"""Autenticacao do cliente FHIR: o portao que faltava para o dossie ter fato clinico.

Estes testes cobrem a POSTURA, nao o protocolo OAuth (isso e' do Cognito). O que precisa
estar travado e' o comportamento em falha: com credencial configurada e token indisponivel,
a chamada RECUSA. Se degradasse para anonimo, o servidor clinico devolveria 401/403 e o
`gather` do Rafael — best-effort por desenho — registraria "cobertura FHIR indisponivel".
O sintoma seria um dossie pior, nao uma falha visivel. E' exatamente assim que o Security
Group fechado passou meses sem ninguem notar.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from maezo.tools.mcp_fhir.server import FhirAuthError, FhirServer, FhirSettings, _Autenticador

TOKEN_FALSO = "eyJhbGciOiJSUzI1NiJ9.fake.assinatura"


def _settings_com_credencial(**extra: Any) -> FhirSettings:
    return FhirSettings(
        base_url="http://hapi.interno/fhir/omni",
        token_url="https://pool.auth.sa-east-1.amazoncognito.com/oauth2/token",
        client_id="agent-rafael-omni",
        client_secret="segredo",
        **extra,
    )


def test_sem_credencial_nao_manda_cabecalho() -> None:
    """O compose local nao exige token — e nao deve receber um `Bearer None`."""
    auth = _Autenticador(FhirSettings(base_url="http://localhost:8081/fhir"))
    assert auth.configurado is False


@pytest.mark.asyncio
async def test_sem_credencial_cabecalhos_vazios() -> None:
    auth = _Autenticador(FhirSettings(base_url="http://localhost:8081/fhir"))
    assert await auth.cabecalhos() == {}


@pytest.mark.asyncio
async def test_token_e_reaproveitado_entre_chamadas(monkeypatch: pytest.MonkeyPatch) -> None:
    """Uma chamada de token por hora, nao uma por requisicao FHIR."""
    chamadas = {"n": 0}

    class _RespostaOk:
        status_code = 200

        @staticmethod
        def json() -> dict[str, Any]:
            return {"access_token": TOKEN_FALSO, "expires_in": 3600}

    class _ClientFalso:
        async def __aenter__(self) -> _ClientFalso:
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def post(self, *_: object, **__: object) -> _RespostaOk:
            chamadas["n"] += 1
            return _RespostaOk()

    monkeypatch.setattr(httpx, "AsyncClient", lambda **_: _ClientFalso())

    auth = _Autenticador(_settings_com_credencial())
    primeiro = await auth.cabecalhos()
    segundo = await auth.cabecalhos()

    assert primeiro == {"Authorization": f"Bearer {TOKEN_FALSO}"}
    assert segundo == primeiro
    assert chamadas["n"] == 1, "pediu token duas vezes — o cache nao esta funcionando"


@pytest.mark.asyncio
async def test_cognito_recusando_o_token_levanta_com_o_corpo(monkeypatch: pytest.MonkeyPatch) -> None:
    """O corpo do Cognito nomeia o escopo recusado; engoli-lo custa uma investigacao."""

    class _Resposta400:
        status_code = 400
        text = '{"error":"invalid_scope"}'

    class _ClientFalso:
        async def __aenter__(self) -> _ClientFalso:
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def post(self, *_: object, **__: object) -> _Resposta400:
            return _Resposta400()

    monkeypatch.setattr(httpx, "AsyncClient", lambda **_: _ClientFalso())

    auth = _Autenticador(_settings_com_credencial(scope="fhir/Coverage.read"))
    with pytest.raises(FhirAuthError) as exc:
        await auth.cabecalhos()

    assert "invalid_scope" in str(exc.value)
    assert "fhir/Coverage.read" in str(exc.value), "a mensagem tem de nomear o escopo pedido"


@pytest.mark.asyncio
async def test_falha_de_rede_no_token_recusa_em_vez_de_seguir_anonimo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """O ponto central: NAO degrada para anonimo."""

    class _ClientFalso:
        async def __aenter__(self) -> _ClientFalso:
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def post(self, *_: object, **__: object) -> None:
            raise httpx.ConnectTimeout("timed out")

    monkeypatch.setattr(httpx, "AsyncClient", lambda **_: _ClientFalso())

    auth = _Autenticador(_settings_com_credencial())
    with pytest.raises(FhirAuthError) as exc:
        await auth.cabecalhos()

    assert "ConnectTimeout" in str(exc.value)
    assert "sem credencial" in str(exc.value)


def test_a_particao_faz_parte_do_base_url() -> None:
    """O HAPI e' multitenant por URL: sem particao, a busca devolve 400.

    Medido em 19/08/2026: `/fhir/Patient` -> 400 "does not know how to handle",
    `/fhir/omni/Patient` -> 401/403 conforme a credencial. A particao nao e' opcional.
    """
    s = _settings_com_credencial()
    assert s.base_url.rstrip("/").split("/")[-1] == "omni"


def test_servidor_registra_se_esta_autenticado() -> None:
    """`fhir_server_initialized` passa a dizer se ha credencial — sem isso, um ambiente
    sem token parece identico a um com token no log de boot."""
    servidor = FhirServer(_settings_com_credencial())
    assert servidor._auth.configurado is True

    anonimo = FhirServer(FhirSettings(base_url="http://localhost:8081/fhir"))
    assert anonimo._auth.configurado is False
