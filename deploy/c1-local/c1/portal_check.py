"""Passos `portal-init` e `portal`: o BFF de producao com `capabilities=identity,staff_cases`.

`portal-init` (servico `portal-init`): o `materialize` da task ECS, com o pacote do `assemble` e os pins
do aprovador de TESTE -> `/run/maezo-staff-materials/current` (0500, dono 1000).

`portal` (servico `portal`, volume read-only + scratch 0700): `create_production_app()` de verdade, com
UMA substituicao, declarada: `CognitoAuthenticator` troca por um autenticador de harness que devolve a
identidade verificada do codigo `c1-code-<principal>` (nao ha Cognito local). Sessao, membership,
lock de sessao (`portal_identity.lock_external_session`), witness nativo, assinatura e o cliente mTLS
8443 sao os de producao. Mede:
1. o lifespan (`staff_runtime`: carga do material, `_qualify_login` dos dois logins pelo TLS do banco);
2. login -> callback -> `GET /api/v1/portal/cases` para o principal do grupo e para o de outro grupo.
"""

from __future__ import annotations

import asyncio
import json
import re
import os
import sys
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import httpx

from .common import ADMIN, ASSEMBLED, TENANT, read_text, state, step

PUBLIC_ORIGIN = "https://portal.c1.invalid"


def _identity_env() -> dict[str, str]:
    from urllib.parse import quote

    from .seed import BFF_LOGIN, ISSUER

    secret = read_text(ADMIN / f"{BFF_LOGIN}-password")
    return {
        "MAEZO_PORTAL_TENANT": TENANT,
        "MAEZO_PORTAL_ISSUER": ISSUER,
        "MAEZO_PORTAL_COGNITO_ORIGIN": "https://c1-local.auth.sa-east-1.amazoncognito.com",
        "MAEZO_PORTAL_CLIENT_ID": "c1portalclient",
        "MAEZO_PORTAL_CLIENT_PURPOSE": "dedicated-human-code-pkce",
        "MAEZO_PORTAL_MACHINE_CLIENT_ID": "c1machineclient",
        "MAEZO_PORTAL_PUBLIC_ORIGIN": PUBLIC_ORIGIN,
        "MAEZO_PORTAL_DATABASE_URL": f"postgresql+asyncpg://{BFF_LOGIN}:{quote(secret, safe='')}@postgres:5432/maezo",
        "MAEZO_PORTAL_MODE": "production",
    }


def _staff_env() -> dict[str, str]:
    pins = state("assemble")["pins"]
    return {f"MAEZO_PORTAL_{k.upper()}": v if isinstance(v, str) else json.dumps(v) for k, v in pins.items()}


def init() -> None:
    from maezo.gateway.staff_cases.materialize import materialize
    from maezo.gateway.staff_cases.production_config import PortalProductionSettings

    os.environ.update(_staff_env())
    try:
        materialize((ASSEMBLED / "bundle.json").read_bytes(), PortalProductionSettings())  # type: ignore[call-arg]
        step("portal-init", True, "pacote staff materializado em /run/maezo-staff-materials/current pelo materialize da task")
    except Exception as failure:
        step("portal-init", False, f"materialize recusou ({type(failure).__name__})")
        raise SystemExit(1) from None


class HarnessAuthenticator:
    """So a troca de codigo do Cognito; a identidade e a da membership semeada (`seed`)."""

    def __init__(self, settings, client) -> None:  # type: ignore[no-untyped-def]
        self._subjects = {f"c1-code-{p}": s for p, (s, _) in state("seed")["principals"].items()}
        self._issuer = state("seed")["issuer"]

    async def exchange(self, code, transaction):  # type: ignore[no-untyped-def]
        from maezo.gateway.oidc import AuthenticationError, VerifiedIdentity

        subject = self._subjects.get(code)
        if subject is None:
            raise AuthenticationError()
        now = datetime.now(UTC)
        return VerifiedIdentity(issuer=self._issuer, subject=subject, authenticated_at=now,
                                expires_at=now + timedelta(hours=1))

    async def close(self) -> None:
        return None


async def _cases(client: httpx.AsyncClient, principal: str) -> tuple[int, str]:
    client.cookies.clear()
    login = await client.get("/api/v1/portal/auth/login")
    if login.status_code != 303:
        return login.status_code, "login " + login.text[:120]
    state_value = parse_qs(urlsplit(login.headers["location"]).query)["state"][0]
    callback = await client.get("/api/v1/portal/auth/callback", params={"state": state_value, "code": f"c1-code-{principal}"})
    if callback.status_code != 303:
        return callback.status_code, "callback " + callback.text[:120]
    response = await client.get("/api/v1/portal/cases")
    return response.status_code, response.text[:200]


async def run() -> None:
    os.environ.update(_identity_env())
    os.environ.update(_staff_env())
    os.environ["MAEZO_PORTAL_CAPABILITIES"] = "identity,staff_cases"
    # O contexto TLS padrao do store de identidade confia na CA do sistema; aqui, na CA local do banco.
    os.environ["SSL_CERT_FILE"] = "/c1/pgtls/ca.pem"
    import maezo.gateway.portal_identity as identity

    identity.CognitoAuthenticator = HarnessAuthenticator  # type: ignore[misc,assignment]
    from maezo.portal.api.production import create_production_app

    app = create_production_app()
    results: dict[str, tuple[int, str]] = {}
    try:
        async with app.router.lifespan_context(app):
            lifespan = "staff_runtime subiu (material + 2 logins qualificados)"
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url=PUBLIC_ORIGIN, follow_redirects=False) as client:
                for principal in state("seed")["principals"]:
                    results[principal] = await _cases(client, principal)
    except Exception as failure:  # noqa: BLE001
        step("portal", False, f"lifespan/BFF recusou antes das requisicoes ({type(failure).__name__}: {failure})")
        raise SystemExit(1) from None
    inside, outside = results["staff-c1-no-grupo"], results["staff-c1-outro-grupo"]
    # Criterio C1: o grupo ve o caso; o de fora e NEGADO. Numa LISTA a negacao e nao ver nada (200 com
    # `items: []`) ou uma recusa; o que reprova e o caso do grupo aparecer para quem esta fora.
    def cases(body: str) -> list[str]:
        # o corpo guardado e truncado para o log: le os case_ref pelo texto, nao por json.loads
        return re.findall(r'"case_ref":"([^"]+)"', body)
    seen = cases(inside[1])
    ok = inside[0] == 200 and len(seen) >= 1 and (outside[0] != 200 or not set(seen) & set(cases(outside[1])))         and (outside[0] != 200 or cases(outside[1]) == [])
    step("portal", ok, f"{lifespan}; /cases no grupo -> {inside[0]} {inside[1]!r}; outro grupo -> {outside[0]} {outside[1]!r}")


def main() -> None:
    if sys.argv[1] == "portal-init":
        init()
    else:
        asyncio.run(run())
