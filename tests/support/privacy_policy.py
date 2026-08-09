"""Test support: drive the DL-0043 PHI-in-business-keys remediation flag.

The flag is a governance ARTIFACT, not an env var, precisely so that activating it is a
reviewed data change. Tests therefore activate it the same way production would: by writing a
manifest and pointing `MAEZO_PHI_KEY_POLICY_PATH` at it. `phi_key_mode` does that and restores
everything (env var + both module caches) on exit, so a test that activates a mode cannot leak
it into the next test.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from maezo.platform.privacy.key_scrubber import reset_egress_pseudonymizer_cache
from maezo.platform.privacy.phi_key_policy import (
    PHI_KEY_POLICY_PATH_ENV,
    reset_phi_key_policy_cache,
)

#: A fully-ratified manifest body. `status` and `modo` are substituted; the ratification block is
#: complete, which is what makes the declared mode take effect.
_RATIFIED_TEMPLATE = """
version: 1
status: "{status}"
modo: "{modo}"
ratificacao:
  ratificado: {ratificado}
  revisor: "teste-{modo}"
  ratificado_em: "2026-01-01"
"""


def write_manifest(
    path: Path,
    *,
    modo: str,
    status: str = "RATIFICADO",
    ratificado: str = "true",
) -> Path:
    """Write a remediation manifest at `path` and return it."""
    path.write_text(
        _RATIFIED_TEMPLATE.format(status=status, modo=modo, ratificado=ratificado),
        encoding="utf-8",
    )
    return path


@contextmanager
def phi_key_policy_path(path: Path | str | None) -> Iterator[None]:
    """Point the policy loader at `path` (or unset the override when None) for the block.

    Clears both caches on entry AND exit — the policy cache and the egress pseudonymizer, which
    would otherwise keep a stale keyed instance across a mode change.
    """
    previous = os.environ.get(PHI_KEY_POLICY_PATH_ENV)
    if path is None:
        os.environ.pop(PHI_KEY_POLICY_PATH_ENV, None)
    else:
        os.environ[PHI_KEY_POLICY_PATH_ENV] = str(path)
    reset_phi_key_policy_cache()
    reset_egress_pseudonymizer_cache()
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(PHI_KEY_POLICY_PATH_ENV, None)
        else:
            os.environ[PHI_KEY_POLICY_PATH_ENV] = previous
        reset_phi_key_policy_cache()
        reset_egress_pseudonymizer_cache()


@contextmanager
def phi_key_mode(tmp_path: Path, modo: str) -> Iterator[None]:
    """Activate `modo` (off | scrub_only | pseudo_keys) via a fully-ratified manifest."""
    manifest = write_manifest(tmp_path / f"phi-key-policy-{modo}.yaml", modo=modo)
    with phi_key_policy_path(manifest):
        yield
