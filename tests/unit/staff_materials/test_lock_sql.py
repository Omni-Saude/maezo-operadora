"""T1.4 D-D: `portal-identity-lock.sql.tmpl` e o bloco canonico, diferindo SO no schema (F7 do C1)."""

from __future__ import annotations

import re

import pytest
from tools.staff_materials.__main__ import main
from tools.staff_materials.lock_sql import CANONICAL, TEMPLATE, render
from tools.staff_materials.secure_io import MaterialError

ARGS = dict(
    tenant="amh",
    tenant_schema="amh",
    session_lock_login="portal_staff_lock_amh",
    witness_login="portal_staff_witness_amh",
)


def _canonical_block() -> str:
    text = CANONICAL.read_text(encoding="utf-8")
    block = re.search(
        r"/\* BEGIN IDENTITY DATABASE INSTALLATION.*?\n\n(.*?)\nEND IDENTITY DATABASE INSTALLATION \*/", text, re.S
    )
    assert block is not None
    return block.group(1)


def _template_block() -> str:
    text = TEMPLATE.read_text(encoding="ascii")
    block = re.search(r"-- BEGIN CANONICAL IDENTITY BLOCK\n(.*?)\n-- END CANONICAL IDENTITY BLOCK", text, re.S)
    assert block is not None
    return block.group(1)


def test_template_is_the_canonical_block_with_only_the_schema_changed() -> None:
    canonical = _canonical_block()
    assert "public.portal_sessions" in canonical and "public.portal_memberships" in canonical
    expected = canonical.replace("public.portal_", "{{TENANT_SCHEMA}}.portal_")
    assert _template_block() == expected


def test_template_bytes_are_ascii_lf() -> None:
    raw = TEMPLATE.read_bytes()
    assert b"\r" not in raw
    raw.decode("ascii")


def test_render_binds_the_lock_login_to_its_tenant() -> None:
    sql = render(**ARGS)
    assert "{{" not in sql and "public.portal_" not in sql
    assert "FROM amh.portal_sessions" in sql and "ON amh.portal_sessions,amh.portal_memberships" in sql
    assert "GRANT USAGE ON SCHEMA amh TO portal_external_identity_reader;" in sql
    assert (
        "GRANT EXECUTE ON FUNCTION portal_identity.lock_external_session(text) TO portal_staff_lock_amh;" in sql
    )
    assert "VALUES('portal_staff_lock_amh','amh')" in sql
    assert "ARRAY['portal_staff_lock_amh','portal_staff_witness_amh']" in sql
    # O witness nao executa o lock.
    assert "lock_external_session(text) TO portal_staff_witness_amh" not in sql


@pytest.mark.parametrize(
    "change",
    [
        {"tenant_schema": "public"},
        {"tenant": "amh'; DROP"},
        {"session_lock_login": "Portal"},
        {"witness_login": "portal_staff_lock_amh"},
    ],
)
def test_render_refuses_anything_but_distinct_identifiers(change: dict[str, str]) -> None:
    with pytest.raises(MaterialError):
        render(**{**ARGS, **change})


def test_cli_prints_the_rendered_sql(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(
        [
            "lock-sql",
            "--tenant",
            "amh",
            "--tenant-schema",
            "amh",
            "--session-lock-login",
            "portal_staff_lock_amh",
            "--witness-login",
            "portal_staff_witness_amh",
        ]
    )
    assert code == 0
    assert capsys.readouterr().out == render(**ARGS)
