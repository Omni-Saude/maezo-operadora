"""Gateway-owned identity credential composition, ADR-0005/0006 and ADR-0049 D4.

Only explicit local-test mode admits injected stores/HTTP clients. Production constructs
its dedicated identity database and Cognito adapter here; neither raw credential nor
client is returned to BFF. BFF lifespan owns closing the returned typed resources.
"""

from __future__ import annotations

import ssl
from dataclasses import dataclass

import httpx
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from maezo.portal.api.config import PortalSettings
from maezo.portal.api.postgres import PostgresIdentityStore
from maezo.portal.api.store import IdentityStore

from .oidc import CognitoAuthenticator, HumanAuthenticator


@dataclass(frozen=True)
class HumanIdentityAdapters:
    store: IdentityStore
    authenticator: HumanAuthenticator


def build_human_identity_adapters(
    config: PortalSettings,
    *,
    store: IdentityStore | None = None,
    oidc_client: httpx.AsyncClient | None = None,
) -> HumanIdentityAdapters:
    """Compose fixed Cognito operations and tenant DB; no generic production injection."""
    if config.mode == "production" and (store is not None or oidc_client is not None):
        raise ValueError("production dependency overrides prohibited")
    if store is None:
        if config.database_url is None:
            raise ValueError("identity database required; local test store must be explicit")
        database_url = make_url(config.database_url.get_secret_value())
        if database_url.drivername != "postgresql+asyncpg" or not database_url.host:
            raise ValueError("dedicated PostgreSQL asyncpg connection required")
        engine = create_async_engine(
            database_url, hide_parameters=True, echo=False, connect_args={"ssl": ssl.create_default_context()}
        )
        store = PostgresIdentityStore(config.tenant, engine)
    client = oidc_client or httpx.AsyncClient(
        verify=True, trust_env=False, follow_redirects=False, timeout=10.0
    )
    return HumanIdentityAdapters(store, CognitoAuthenticator(config, client))
