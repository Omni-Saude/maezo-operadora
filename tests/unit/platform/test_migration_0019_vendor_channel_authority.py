"""Structural tests for migration `0019_vendor_channel_authority` — the VW1-P0 spine DDL.

These run without Postgres (the live upgrade/downgrade/upgrade proof is the integration lane).
They are the ANTI-REGRESSION half — the properties that must stay true about this DDL, expressed
so a later edit which quietly breaks one fails CI instead of failing a human in a queue.

Invariants:

1.  **Chain integrity.** `0019` revises `0018`, is the head, and no other migration claims either
    slot. A forked chain is the failure mode where `alembic upgrade head` applies one branch while
    an operator believes it applied the other.
2.  **The channel is unique per tenant and addressed by opaque refs only.** The primary key is
    `(tenant, channel_ref)`; there is no second identity feed and no business identifier smuggled
    into the key.
3.  **CAS revision.** The store only makes sense as a compare-and-swap token holder: `revision`
    is a non-negative counter, never a version label that resets.
4.  **Closed status.** `status` admits exactly `active`/`revoked` — the provider-plane semantics —
    so no third state can be invented by a caller.
5.  **No cipher, no retention.** ZERO cifras and ZERO retention deadline belong to the DPO
    (RATIFY-LATER); this migration must not invent either.
6.  **`downgrade()` is honest.** It refuses a populated store (disposition belongs to a qualified
    owner), drops exactly what `upgrade()` created and touches no table from 0001..0018.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import maezo

# `versions/` is a namespace package (no `__init__.py`), so `__file__` is None there — resolve
# from the installed package root, the way the 0015 structural test does.
_VERSIONS_DIR = Path(maezo.__file__).parent / "platform" / "migrations" / "versions"
_MIGRATION = _VERSIONS_DIR / "0019_vendor_channel_authority.py"
_SOURCE = _MIGRATION.read_text(encoding="utf-8")
_CODE = _SOURCE.split('"""', 2)[2]

_TABLE = "portal_vendor_channels"


def test_a_migracao_existe_e_encadeia_em_0018() -> None:
    assert _MIGRATION.is_file()
    assert re.search(r'^revision: str = "0019"$', _SOURCE, re.MULTILINE)
    assert re.search(r'^down_revision: str \| None = "0018"$', _SOURCE, re.MULTILINE)


def test_a_cadeia_nao_bifurca() -> None:
    """Exactly one migration claims revision `0019`, and exactly one revises `0018`."""
    claims_0019: list[str] = []
    revises_0018: list[str] = []
    for path in sorted(_VERSIONS_DIR.glob("0*.py")):
        text = path.read_text(encoding="utf-8")
        if re.search(r'^revision: str = "0019"$', text, re.MULTILINE):
            claims_0019.append(path.name)
        if re.search(r'^down_revision: str \| None = "0018"$', text, re.MULTILINE):
            revises_0018.append(path.name)

    assert claims_0019 == ["0019_vendor_channel_authority.py"]
    assert revises_0018 == ["0019_vendor_channel_authority.py"]


def test_0019_e_revisada_so_pela_proxima_quando_ela_existir() -> None:
    """A later migration must be 0019's ONLY child — VW1-P4's submission store (OP16)."""
    revising = [
        path.name
        for path in sorted(_VERSIONS_DIR.glob("0*.py"))
        if re.search(r'^down_revision: str \| None = "0019"$', path.read_text(encoding="utf-8"), re.MULTILINE)
    ]
    assert revising == ["0020_vendor_channel_submissions.py"]


def test_o_canal_e_unico_por_tenant_e_enderecado_de_forma_opaca() -> None:
    assert f"CREATE TABLE {_TABLE}" in _CODE
    assert "PRIMARY KEY (tenant, channel_ref)" in _CODE
    # Opaque addressing: the channel ref is a non-empty text, never a structured business key.
    assert "channel_ref text NOT NULL CHECK (channel_ref <> '')" in _CODE
    assert "tenant text NOT NULL CHECK (tenant <> '')" in _CODE


def test_a_revisao_e_um_token_cas_nao_negativo() -> None:
    assert "revision bigint NOT NULL CHECK (revision >= 0)" in _CODE


def test_o_status_e_um_texto_fechado_espelhando_o_plano_provider() -> None:
    assert "status text NOT NULL CHECK (status IN ('active', 'revoked'))" in _CODE


def test_os_selos_de_tempo_sao_tz_aware() -> None:
    assert "created_at timestamptz NOT NULL" in _CODE
    assert "updated_at timestamptz NOT NULL" in _CODE


@pytest.mark.parametrize(
    "inventado",
    [
        "ciphertext",
        "wrapped_key",
        "nonce",
        "kms",
        "aes",
        "expires_at",
        "INTERVAL '",
        "pg_cron",
        "DELETE FROM",
        "DROP PARTITION",
    ],
)
def test_nenhuma_cifra_ou_retencao_e_inventada(inventado: str) -> None:
    """Cipher material and retention deadlines are RATIFY-LATER DPO decisions."""
    assert inventado.lower() not in _CODE.lower(), (
        f"`{inventado}` must never appear in the vendor channel store: the 0019 spine stores "
        "accreditation state only and invents neither a cipher nor a retention deadline"
    )


def test_a_tabela_nao_e_legivel_para_o_publico() -> None:
    assert f"REVOKE ALL ON TABLE {_TABLE} FROM PUBLIC" in _CODE


def test_o_downgrade_e_honesto_e_recusa_store_populado() -> None:
    downgrade = _SOURCE.split("def downgrade()", 1)[1]
    assert "IF EXISTS (SELECT 1 FROM portal_vendor_channels) THEN" in downgrade
    assert "RAISE EXCEPTION 'populated vendor channel authority" in downgrade
    assert f"DROP TABLE {_TABLE}" in downgrade
    for foreign in (
        "portal_memberships",
        "portal_sessions",
        "beneficiario_contato_retomada",
        "v21_journey_journal",
        "amh_inbox",
    ):
        assert foreign not in _CODE
