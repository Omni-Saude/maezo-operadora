"""Tenant ids interpolated into SQL / env-var names must match the WHOLE string (forensic audit F-129).

`re.match` with `^...$` accepts a trailing newline (`$` matches before a final `\\n`), so
`"amh\\n"` used to pass `schema_for_tenant` and the A2A per-tenant key-name derivation.
"""

from __future__ import annotations

import pytest

from maezo.a2a.keyset import per_tenant_key_env_var, per_tenant_prior_key_env_var
from maezo.gateway.audit_postgres import schema_for_tenant

_BAD = ["amh\n", "amh\n\n", "amh ", "\namh", "AMH", "1amh", "", "amh;drop"]


@pytest.mark.parametrize("tenant", _BAD)
def test_schema_for_tenant_refuses_anything_but_a_full_identifier(tenant: str) -> None:
    with pytest.raises(ValueError):
        schema_for_tenant(tenant)


@pytest.mark.parametrize("tenant", _BAD)
def test_a2a_key_env_var_refuses_anything_but_a_full_identifier(tenant: str) -> None:
    with pytest.raises(ValueError):
        per_tenant_key_env_var(tenant)
    with pytest.raises(ValueError):
        per_tenant_prior_key_env_var(tenant)


def test_valid_tenant_ids_still_pass() -> None:
    assert schema_for_tenant("amh") == "amh"
    assert per_tenant_key_env_var("amh").endswith("AMH")
    assert per_tenant_prior_key_env_var("tenant_2").endswith("TENANT_2")
