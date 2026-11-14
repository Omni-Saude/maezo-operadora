"""OP21 PR 3/5 — prova 6 do desenho (sha256 `5639f41d…` §7): flag OFF = stub de hoje.

Shard `restante`. O que esta unidade prova sobre a COMPOSIÇÃO (as rotas staff 403/503 do
default já são provadas em `test_legitimacy_screen_endpoint.py`):

- flag OFF (shipped): o objeto que chega a `create_app(commercial_gateway=…)` é o MESMO
  que pousa em `app.state.commercial_gateway` — nenhuma instância do guard existe na
  composição, o caminho é byte a byte o de hoje, e a fábrica do guard (sem default) é
  IGNORADA quando a flag está OFF;
- flag ON: o gateway montado é o wrapped — ainda um `CommercialGateway` (o `isinstance`
  do router congelado permanece verdadeiro sem editá-lo) e exatamente a classe
  `LegitimacyScreenShadow` (a postura enforcing nunca é a montada — prova 4);
- flag ON sem a fábrica do guard: recusa de BOOT (dois portões — virar a chave sem o bind
  explícito não é shadow silenciosa);
- o env `MAEZO_PORTAL_COLLECTIVE_LEGITIMACY_SCREEN` segue registrado na cerca W5 5.1
  (8 flags do programa, #1028) e `deploy/**` continua sem nomeá-lo — a cerca de flags-OFF
  do PR 2 já cobre este PR, que não introduz env novo.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest
from scripts.ci.check_program_dark_flags import ENV_FLAGS_PROGRAMA, checar_deploy_sem_mencao
from tests.unit.portal.test_human_session import SignedTestIdP, config
from tests.unit.test_commercial_gateway import UnitCodec

from maezo.gateway.commercial import CommercialGateway
from maezo.gateway.commercial_legitimacy_guard import LegitimacyScreenShadow
from maezo.gateway.commercial_legitimacy_screen import LegitimacyScreenStoreError
from maezo.portal.api.app import create_app
from maezo.portal.api.store import LocalTestIdentityStore

_REPO_ROOT = Path(__file__).resolve().parents[3]
_IDP = SignedTestIdP()


class _EmptyStore:
    """Port double vazio: a composição nunca o toca (nenhuma rota do guard lê venda)."""

    async def save(self, record: object) -> object:  # pragma: no cover - nunca chamado
        raise LegitimacyScreenStoreError("source_unavailable")

    async def read(self, tenant: str, screen_ref: str, screen_revision: int) -> object | None:
        return None

    async def latest_for_opportunity(self, tenant: str, opportunity_ref: str) -> object | None:
        return None

    async def latest_for_legal_entity(self, tenant: str, legal_entity_ref: str) -> object | None:
        return None


def _gateway() -> CommercialGateway:
    return CommercialGateway(codec=UnitCodec())


def _guard_factory(inner: CommercialGateway) -> LegitimacyScreenShadow:
    return LegitimacyScreenShadow(
        store=_EmptyStore(),  # type: ignore[arg-type]
        tenant="test-tenant",
        codec=inner.codec,
        admission=inner.admission,
        source=inner.source,
        timeout_seconds=inner.timeout_seconds,
    )


def _app(*, flag: bool, factory: Any = None, gateway: CommercialGateway | None = None) -> Any:
    return create_app(
        config(collective_legitimacy_screen=flag),
        store=LocalTestIdentityStore("test-tenant"),
        oidc_client=httpx.AsyncClient(transport=httpx.MockTransport(_IDP.handle)),
        commercial_gateway=gateway,
        legitimacy_screen_guard_factory=factory,
    )


def test_prova6_flag_off_o_mesmo_objeto_pousa_no_app_state_sem_guard_na_composição() -> None:
    gateway = _gateway()
    app = _app(flag=False, gateway=gateway)
    assert app.state.commercial_gateway is gateway  # identidade: o stub byte a byte de hoje
    assert type(app.state.commercial_gateway) is CommercialGateway
    com_fábrica = _app(flag=False, factory=_guard_factory, gateway=gateway)
    assert com_fábrica.state.commercial_gateway is gateway  # fábrica ignorada com a flag OFF


def test_prova6_flag_on_monta_o_wrapped_shadow_e_o_isinstance_do_router_permanece() -> None:
    gateway = _gateway()
    app = _app(flag=True, factory=_guard_factory, gateway=gateway)
    montado = app.state.commercial_gateway
    assert montado is not gateway
    assert isinstance(montado, CommercialGateway)  # portal/api/commercial.py:51, sem editá-lo
    assert type(montado) is LegitimacyScreenShadow  # a postura montada é a SHADOW, só ela


def test_prova6_flag_on_sem_fábrica_é_recusa_de_boot() -> None:
    with pytest.raises(ValueError, match="legitimacy_screen_guard_unbound"):
        _app(flag=True, factory=None, gateway=_gateway())


def test_prova6_env_do_op21_está_registrado_na_cerca_do_programa_e_deploy_calado() -> None:
    assert "MAEZO_PORTAL_COLLECTIVE_LEGITIMACY_SCREEN" in ENV_FLAGS_PROGRAMA
    # A cerca W5 5.1 (#1028) já cobre o PR 3 — nenhum `deploy/**` nomeia o env na árvore
    # viva; este PR não introduz env novo (a postura nunca é knob de ambiente).
    assert checar_deploy_sem_mencao(_REPO_ROOT) == []
