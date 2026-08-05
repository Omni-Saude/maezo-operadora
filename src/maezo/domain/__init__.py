"""`maezo.domain` — the payer core's own value vocabulary (ADR-0037).

Leaf domain, like `maezo.ports`: standard library only, no adapter, no client, no vendor type, and no
dependency on any other `maezo` package. Where `maezo.ports` declares the SEAMS the payer core talks
through, `maezo.domain` declares the VALUES it talks about.

    maezo.domain.integration.identity   portable identity + tenant value objects (XRD-05, MZO-020)

Deliberately NOT a re-export surface: subpackages are imported by their own path, so a new value
family cannot become part of `maezo.domain`'s public shape without a deliberate edit here.
"""

from __future__ import annotations

__all__: list[str] = []
