"""Unit tests for maezo.tools.process_allowlist — Process Key Allowlist (ADR-0016).

TDD London School: tests written BEFORE implementation.
"""

import pytest

from maezo.tools.process_allowlist import (
    KNOWN_PROCESS_KEYS,
    ProcessAllowlist,
    ProcessKeyNotAllowedError,
    ensure_allowed,
)


def test_allowlist_allows_known_key() -> None:
    """ensure_allowed must accept any key in KNOWN_PROCESS_KEYS."""
    for key in KNOWN_PROCESS_KEYS:
        # Must not raise
        ensure_allowed(key)


def test_allowlist_blocks_unknown_key() -> None:
    """ensure_allowed must raise ProcessKeyNotAllowedError for unknown keys."""
    with pytest.raises(ProcessKeyNotAllowedError):
        ensure_allowed("SP-OP-INEXISTENTE-999")


def test_allowlist_blocks_invalid_format() -> None:
    """Keys not matching the SP-OP-<DOMAIN>-<NNN> format must be blocked."""
    invalid_keys = [
        "SP-OP-AUTH",  # missing NNN
        "SP-OP-auth-001",  # lowercase
        "sp-op-auth-001",  # wrong prefix
        "AUTH-001",  # missing SP-OP prefix
        "",  # empty
        "random_string",
    ]

    for key in invalid_keys:
        with pytest.raises(ProcessKeyNotAllowedError):
            ensure_allowed(key)


def test_allowlist_known_process_keys_is_frozenset() -> None:
    """KNOWN_PROCESS_KEYS must be an immutable frozenset (CI-enforced)."""
    assert isinstance(KNOWN_PROCESS_KEYS, frozenset)


def test_allowlist_known_process_keys_count() -> None:
    """KNOWN_PROCESS_KEYS must contain exactly 15 SP-OP-* keys (ADR-0016)."""
    assert len(KNOWN_PROCESS_KEYS) == 15


def test_allowlist_process_key_not_allowed_error() -> None:
    """ProcessKeyNotAllowedError must be a subclass of PermissionError."""
    assert issubclass(ProcessKeyNotAllowedError, PermissionError)


def test_allowlist_custom_tenant_allowlist() -> None:
    """ProcessAllowlist with a custom tenant config must accept additional keys
    that are within the KNOWN_PROCESS_KEYS universe."""
    # Create an allowlist that adds one extra known key
    extra = {"SP-OP-AUTH-001"}
    allowlist = ProcessAllowlist(tenant_allowed_keys=extra)

    # Must allow the extra key
    allowlist.ensure_allowed("SP-OP-AUTH-001")

    # Must still block unknown keys
    with pytest.raises(ProcessKeyNotAllowedError):
        allowlist.ensure_allowed("SP-OP-INEXISTENTE-999")


def test_allowlist_blocks_non_sp_op_format() -> None:
    """Keys with homoglyphs or encoding tricks must be blocked by the format check."""
    tricky = "SP\u2010OP\u2010AUTH\u2010001"  # non-ASCII hyphens
    with pytest.raises(ProcessKeyNotAllowedError):
        ensure_allowed(tricky)
