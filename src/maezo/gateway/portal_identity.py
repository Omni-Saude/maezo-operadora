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
    authenticator: HumanAuthenticator | None = None,
) -> HumanIdentityAdapters:
    """Compose fixed Cognito operations and tenant DB; no generic production injection."""
    if config.mode == "production" and (
        store is not None or oidc_client is not None or authenticator is not None
    ):
        raise ValueError("production dependency overrides prohibited")
    if authenticator is not None:
        # Local-test stub authenticator (WP-J1-10): an explicit store is required so the
        # returned identity resolves against a membership somebody deliberately seeded,
        # and no Cognito HTTP client is constructed (the stub never opens one).
        if store is None:
            raise ValueError("local-test authenticator requires an explicit identity store")
        return HumanIdentityAdapters(store, authenticator)
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
        if config.auth_lifecycle_binding_path is not None:
            from .intake.native_authority import load_auth_lifecycle

            composition = load_auth_lifecycle(
                config.auth_lifecycle_binding_path, tenant=config.tenant, identity_writer=engine
            )
            store.bind_auth_lifecycle(composition)
    client = oidc_client or httpx.AsyncClient(
        verify=True, trust_env=False, follow_redirects=False, timeout=10.0
    )
    return HumanIdentityAdapters(store, CognitoAuthenticator(config, client))
