"""MCP FHIR server — FHIR R4 Client for HAPI FHIR.

Provides read_resource(resource_type, id) and search_resources(resource_type, params).
Uses httpx.AsyncClient for async HTTP calls to the HAPI FHIR server.

Per ADR-0006, FHIR resources containing PHI must only be read through
the ToolRegistry gateway (PEP + audit + scrub-PHI).

Tools:
- read_resource(resource_type, id) -> resource_dict
- search_resources(resource_type, params) -> bundle_dict
"""

from __future__ import annotations

import base64
import time
from typing import Any, ClassVar

import httpx
import structlog
from pydantic_settings import BaseSettings

logger = structlog.get_logger(__name__)


class FhirSettings(BaseSettings):
    """Configuration for the HAPI FHIR client.

    Environment variables prefixed with FHIR_ (default).

    AUTENTICACAO (19/08/2026). O `base_url` sozinho nao alcanca dado clinico neste
    ambiente, e a razao apareceu na ordem exata em que os portoes existem:

        GET /fhir/metadata            -> 200  (o servidor responde)
        GET /fhir/Patient             -> 400  ("does not know how to handle")
        GET /fhir/omni/Patient        -> 401  ("Missing or malformed Authorization header")
        GET /fhir/omni/Patient + JWT  -> 403  ("JWT sem tenant: client_id nao mapeado")

    Sao TRES portoes, e nenhum deles e' acidente: particao na URL (multitenancy do
    HAPI), token M2M do Cognito, e o `client_id` mapeado a UMA empresa no
    interceptor. O ultimo e' o isolamento: o token M2M nao carrega claim de tenant
    de proposito — "um agente que escolhe o proprio tenant nao tem fronteira"
    (cognito.tf do amh-data-platform). O segredo E' a fronteira, e o raio de um
    vazamento e' uma empresa.

    Portanto `base_url` DEVE incluir a particao (`.../fhir/omni`) e as credenciais
    abaixo devem ser as do client daquela empresa.
    """

    model_config = {"env_prefix": "FHIR_", "extra": "ignore"}

    base_url: str = "http://localhost:8081/fhir"

    #: Endpoint de token do Cognito. Vazio = sem autenticacao (o HAPI do compose
    #: local nao exige). NAO e' fallback silencioso em producao: ver `_Autenticador`.
    token_url: str = ""
    client_id: str = ""
    client_secret: str = ""
    #: Escopos pedidos. Pedir um escopo que o client NAO tem faz o Cognito recusar o
    #: TOKEN INTEIRO com 400, antes de chegar ao servidor clinico — o agente perde o
    #: turno descobrindo isso. Mantenha alinhado com o app client.
    scope: str = "fhir/Patient.read fhir/Encounter.read"


class FhirAuthError(RuntimeError):
    """Credencial FHIR configurada mas indisponivel. NUNCA degrada para anonimo."""


class _Autenticador:
    """Token M2M do Cognito, buscado sob demanda e reaproveitado ate perto de expirar.

    FALHA FECHADO, e essa e' a decisao que importa: se as credenciais estao
    configuradas e o token nao vem, `cabecalhos()` LEVANTA. A alternativa — seguir
    sem cabecalho — produziria um 401/403 do servidor clinico que o `gather` do
    Rafael, sendo best-effort, registraria como "cobertura FHIR indisponivel". O
    sintoma seria um dossie pior, nao uma falha visivel: exatamente o modo como o
    Security Group fechado passou meses sem ninguem notar.

    Sem `client_id` configurado nao ha autenticacao nenhuma e `cabecalhos()` devolve
    vazio — e' o caminho do compose local, onde o HAPI nao exige token.
    """

    #: Margem antes do vencimento. O token do pool vale 3600s; 120s de folga evitam
    #: a corrida entre "ainda valido quando pedi" e "expirado quando chegou".
    MARGEM_S: ClassVar[float] = 120.0

    def __init__(self, settings: FhirSettings) -> None:
        self._s = settings
        self._token: str | None = None
        self._expira_em: float = 0.0

    @property
    def configurado(self) -> bool:
        return bool(self._s.token_url and self._s.client_id and self._s.client_secret)

    async def cabecalhos(self) -> dict[str, str]:
        if not self.configurado:
            return {}
        agora = time.monotonic()
        if self._token is None or agora >= self._expira_em:
            await self._renovar()
        return {"Authorization": f"Bearer {self._token}"}

    async def _renovar(self) -> None:
        credencial = base64.b64encode(f"{self._s.client_id}:{self._s.client_secret}".encode()).decode()
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                r = await client.post(
                    self._s.token_url,
                    data={"grant_type": "client_credentials", "scope": self._s.scope},
                    headers={
                        "Authorization": f"Basic {credencial}",
                        "Content-Type": "application/x-www-form-urlencoded",
                    },
                )
            if r.status_code != 200:
                # O corpo do Cognito nomeia o escopo recusado; sem ele o operador fica
                # adivinhando qual dos dez escopos o client nao tem.
                raise FhirAuthError(
                    f"Cognito recusou o token: HTTP {r.status_code} — {r.text[:300]}. "
                    f"client_id={self._s.client_id} scope={self._s.scope!r}"
                )
            corpo = r.json()
        except FhirAuthError:
            raise
        except Exception as exc:  # qualquer falha de rede e' fail-closed
            raise FhirAuthError(
                f"nao foi possivel obter token do Cognito ({type(exc).__name__}: {exc}). "
                "Recusando chamada FHIR sem credencial — um 401 do servidor clinico "
                "vira 'cobertura indisponivel' no dossie, que e' silencioso."
            ) from exc

        self._token = str(corpo["access_token"])
        # `expires_in` e' segundos; a margem sai daqui, nao do consumidor.
        self._expira_em = time.monotonic() + float(corpo.get("expires_in", 3600)) - self.MARGEM_S
        logger.info(
            "fhir_token_obtido",
            client_id=self._s.client_id,
            expira_em_s=int(self._expira_em - time.monotonic()),
            scope=self._s.scope,
        )


class FhirServer:
    """MCP server for FHIR R4 — in-process (ADR-0022).

    Wraps the HAPI FHIR REST API with async httpx calls.
    Tools are registered via register_tools() for integration
    with the ToolRegistry (ADR-0016).

    Usage:
        server = FhirServer()
        patient = await server.read_resource("Patient", "123")
        results = await server.search_resources("Patient", {"name": "Teste"})
    """

    def __init__(self, settings: FhirSettings | None = None) -> None:
        """Initialize the FHIR server.

        Args:
            settings: Optional FhirSettings; defaults to localhost:8081/fhir.
        """
        self._settings = settings or FhirSettings()
        self._auth = _Autenticador(self._settings)
        logger.info(
            "fhir_server_initialized",
            base_url=self._settings.base_url,
            autenticado=self._auth.configurado,
        )

    def list_tools(self) -> list[dict[str, str]]:
        """Return tool definitions for registration with ToolRegistry.

        Returns:
            List of tool dicts with 'name' and 'description' keys.
        """
        return [
            {
                "name": "read_resource",
                "description": "Read a FHIR R4 resource by type and ID.",
            },
            {
                "name": "search_resources",
                "description": "Search FHIR R4 resources with query parameters.",
            },
        ]

    def register_tools(self, registry: Any) -> None:
        """Register FHIR tools with the given ToolRegistry (ADR-0022, ADR-0016).

        Args:
            registry: A ToolRegistry instance that accepts register(name, handler).
        """
        registry.register("read_resource", self.read_resource)
        registry.register("search_resources", self.search_resources)
        logger.info("fhir_tools_registered", count=2)

    async def read_resource(
        self,
        resource_type: str,
        resource_id: str,
    ) -> dict[str, Any]:
        """Read a FHIR R4 resource by type and ID.

        GET /fhir/{resource_type}/{resource_id}

        Args:
            resource_type: The FHIR resource type (e.g., 'Patient', 'Observation').
            resource_id: The logical ID of the resource.

        Returns:
            The FHIR resource as a dict.

        Raises:
            httpx.HTTPStatusError: If the HAPI FHIR server returns an error.
        """
        url = f"{self._settings.base_url}/{resource_type}/{resource_id}"

        logger.info("fhir_read_resource", resource_type=resource_type, resource_id=resource_id)

        cabecalhos = await self._auth.cabecalhos()
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, headers=cabecalhos)
            response.raise_for_status()
            data: dict[str, Any] = response.json()

        return data

    async def search_resources(
        self,
        resource_type: str,
        params: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Search FHIR R4 resources with query parameters.

        GET /fhir/{resource_type}?param1=value1&param2=value2

        Args:
            resource_type: The FHIR resource type to search.
            params: Optional query parameters (e.g., {'name': 'Teste'}).

        Returns:
            A FHIR Bundle as a dict.

        Raises:
            httpx.HTTPStatusError: If the HAPI FHIR server returns an error.
        """
        url = f"{self._settings.base_url}/{resource_type}"

        logger.info("fhir_search_resources", resource_type=resource_type, params=params)

        cabecalhos = await self._auth.cabecalhos()
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, params=params or {}, headers=cabecalhos)
            response.raise_for_status()
            data: dict[str, Any] = response.json()

        return data
