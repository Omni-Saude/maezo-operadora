"""PHI egress redaction for the worker -> engine/Kafka edge (GAP-XPHI-1, ADR-0006).

This is the ONE-WAY variant of the donor's `phi_vars.scrub_phi_vars`
(`Maezo-Healthcare-Plan`, READ-ONLY), ported for the v2 external-task workers (T3.1).

## The invariant (ADR-0006, "Zona Geral" vs "Zona PHI")

A domain event or notice a worker emits toward the GENERAL ZONE — Kafka `agents.events.*` /
`operadora.notifications.internal`, and any payload written back to engine process variables via
`complete` — NEVER carries raw clinical free text. The raw clinical justification legitimately
lives in the PHI zone (the secure denial channel to the prestador receives it out-of-band); it
must not leak into a general-zone payload just because a worker copied a process variable into
its output dict.

`PHI_PROCESS_VARS` is the INVERSE of the safe `*_pseudo_id` convention: these are the
process-variable NAMES that carry clinical free text (auditor justificativa, CID-10, DUT/ROL
grounding, laudo, diagnostico) which the gateway never tokenized. A value under any of these keys
must pass through `redact_phi_vars` before it leaves a worker.

## One-way, at this edge (deliberate divergence from the donor)

The donor's `scrub_phi_vars` takes a tenant `Pseudonymizer` and, WHEN one is injected, tokenizes
embedded identifiers reversibly (the gateway's surrogate-store seam). That reversible path
belongs to the gateway / Pseudonymizer DRIVER zone. This module is the ENGINE/KAFKA-FACING edge:
there is no reversible driver here, so redaction is ONE-WAY only — every non-empty PHI-named
value is replaced by the class token `REDACTED_PHI`. There is deliberately no pseudonymizer
parameter: a worker daemon at this edge prefers to drop the field's content (fail-closed) over
ever emitting raw clinical text into a general-zone variable. Class-token redaction only.

Stdlib-only by design (mirrors the donor): keep `tools.workers` free of a `gateway`/`runtime`
import cycle.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

# Process-variable NAMES that carry clinical / human free text (Zona PHI, ADR-0006) — the inverse
# of the safe `*_pseudo_id` convention. A value under any of these keys must never leave a worker
# for a general-zone payload without passing through `redact_phi_vars`. Kept aligned with the
# donor's `PHI_PROCESS_VARS` so the invariant's coverage does not drift between codebases.
PHI_PROCESS_VARS: frozenset[str] = frozenset(
    {
        "justificativa_clinica",
        "cid10_referencia",
        "fundamentacao_dut",
        "notas_resolucao",
        "resumo_contexto",
        "matricula_beneficiario",
        "laudo",
        "diagnostico",
    }
)

# Class-token sentinel that replaces a PHI-named value at this one-way edge. Visible enough in the
# audit trail to signal "a clinical field was redacted before egress" — never reveals the value.
REDACTED_PHI: str = "[REDACTED_PHI]"


def redact_phi_vars(values: Mapping[str, Any]) -> dict[str, Any]:
    """Return a COPY of `values` with every non-empty PHI-named value replaced by `REDACTED_PHI`.

    Fail-closed, one-way (module docstring):
      - a key in `PHI_PROCESS_VARS` with a non-empty value -> `REDACTED_PHI` (class token).
      - empty values (`None` / `""` / whitespace-only) pass through unchanged (nothing to redact).
      - non-PHI keys pass through unchanged (correlation identifiers a fact MUST carry).

    NEVER raises — it is a backstop on the worker's egress hot path. Non-string PHI values are
    still redacted (a clinical field arriving as a non-string is unexpected and treated
    fail-closed as content to remove), except the empty sentinels above.
    """
    redacted: dict[str, Any] = {}
    for key, value in values.items():
        if key in PHI_PROCESS_VARS and not _is_empty(value):
            redacted[key] = REDACTED_PHI
        else:
            redacted[key] = value
    return redacted


def _is_empty(value: Any) -> bool:
    """True for `None` or a string that is empty / whitespace-only (nothing to redact)."""
    if value is None:
        return True
    return isinstance(value, str) and not value.strip()
