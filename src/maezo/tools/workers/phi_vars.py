"""PHI egress redaction for the worker -> engine/Kafka edge (GAP-XPHI-1, ADR-0006).

This is the ONE-WAY variant of the donor's `phi_vars.scrub_phi_vars`
(`Maezo-Healthcare-Plan`, READ-ONLY), ported for the v2 external-task workers (T3.1).

`redact_error_message` (T3.4 F5) is a SIBLING backstop for a different shape of leak: not a
named PHI process variable, but PHI-shaped substrings (a CPF, a long digit run) that end up
INSIDE a raw exception message forwarded to the engine's Cockpit-visible incident store
(`WorkerTransport.handle_failure`/`.handle_bpmn_error`'s `error_message`). `redact_phi_vars`
cannot help there — it redacts whole values by KEY, not patterns embedded in free text. Same
one-way, class-token, never-raises philosophy as `redact_phi_vars`; see that function's
docstring for the shared invariant.

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

import re
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


# --------------------------------------------------------------------------------------------
# Exception-message redaction backstop (T3.4 F5) — module docstring for the "why".
# --------------------------------------------------------------------------------------------
#
# Scoped narrowly, same rationale as `a2a.delegation._looks_like_phi`'s CPF/CNPJ regexes (which
# these mirror rather than import — that module's copies are deliberately private to its own
# payload_ref guard, a different call site with a different false-positive tradeoff): a
# canonically-punctuated CPF/CNPJ substring essentially never occurs by chance in prose, so it is
# always safe to redact. A bare run of >=11 digits is a broader net (it also catches e.g. a long
# numeric FHIR/process id embedded in a message) but that tradeoff is correct HERE — this backstop
# guards an engine-visible INCIDENT message, not a structural routing field, so over-redaction
# (losing a numeric id from free text) is the safe failure mode, not under-redaction (leaking a
# CPF).
_CPF_FORMATTED_RE = re.compile(r"\d{3}\.\d{3}\.\d{3}-\d{2}")
_CNPJ_FORMATTED_RE = re.compile(r"\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}")
_DIGIT_RUN_RE = re.compile(r"\d{11,}")

#: Class token substituted for a redacted PHI-shaped substring. Distinct from `REDACTED_PHI`
#: (whole-value, key-based redaction) so an ops reader can tell the two backstops apart in a log.
REDACTED_DIGITS: str = "[REDACTED_DIGITS]"

#: Cap on the redacted message forwarded to the engine's incident store. Mirrors the existing
#: `resp.text[:500]` truncation convention (`platform/deploy/engine_deploy.py`) used elsewhere in
#: this codebase for bounding untrusted text before it lands in an error message.
_ERROR_MESSAGE_MAX_CHARS: int = 500
_TRUNCATION_MARKER: str = "...[TRUNCATED]"


def redact_error_message(error: BaseException | str) -> str:
    """Redact PHI-shaped substrings from an exception message before it reaches an
    engine-visible incident store (`WorkerTransport.handle_failure` / `.handle_bpmn_error`'s
    `error_message`, T3.4 F5).

    Fail-closed, one-way, NEVER raises (mirrors `redact_phi_vars`'s backstop contract — this
    sits on the worker's failure-reporting hot path, and a defect in the scrubber must never
    itself crash the failure report):
      - a canonically-punctuated CPF (`123.456.789-01`) or CNPJ (`12.345.678/0001-90`) substring
        -> `REDACTED_DIGITS`.
      - any remaining run of 11+ contiguous digits (bare CPF/CNS/CNPJ, or any other long numeric
        identifier) -> `REDACTED_DIGITS`.
      - the result is capped at `_ERROR_MESSAGE_MAX_CHARS`, truncated with `_TRUNCATION_MARKER` —
        bounds an unbounded/adversarial message length regardless of content.

    The error CLASS is preserved as a stable `"{ClassName}: "` prefix when `error` is an
    exception instance (plain strings, e.g. static harness messages with no exception, are
    returned without a prefix) — ops can still diagnose WHAT kind of failure occurred from the
    Cockpit incident view even though the message body may have been scrubbed.
    """
    try:
        error_class = type(error).__name__ if isinstance(error, BaseException) else None
        raw = str(error)
        scrubbed = _CPF_FORMATTED_RE.sub(REDACTED_DIGITS, raw)
        scrubbed = _CNPJ_FORMATTED_RE.sub(REDACTED_DIGITS, scrubbed)
        scrubbed = _DIGIT_RUN_RE.sub(REDACTED_DIGITS, scrubbed)
        if len(scrubbed) > _ERROR_MESSAGE_MAX_CHARS:
            scrubbed = scrubbed[:_ERROR_MESSAGE_MAX_CHARS] + _TRUNCATION_MARKER
        return f"{error_class}: {scrubbed}" if error_class else scrubbed
    except Exception:  # noqa: BLE001 — backstop must never itself raise onto the failure path.
        return "[REDACTED_ERROR]"
