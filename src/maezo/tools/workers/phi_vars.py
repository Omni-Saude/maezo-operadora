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

CC-06 (2026-09-04) promotes that pattern net out of the exception-message backstop into
`redact_free_text`, and adds `redact_free_text_vars` — the AGENT -> ENGINE leg's scrub, run by
`mcp_cibseven/transport.py::start_process_idempotent` over the process-start `variables` that
edge previously shipped VERBATIM (an LLM-drafted `resumo_contexto` / dossier `narrativa`). Two
deliberate departures from this module's older contract, both documented at their definitions:
`redact_free_text_vars` MAY RAISE (its only caller must refuse the start, never pass through
raw), and it scrubs SUBSTRINGS under a named-key allowlist instead of nuking whole values —
the handoff summary is contractually required to survive, pseudonimizado (SP-OP-ESCALATION-001).

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
# Sibling of `a2a.delegation._looks_like_phi`'s CPF/CNPJ net (deliberately NOT imported — that
# module's copies are private to its own payload_ref guard, a different call site with a different
# false-positive tradeoff: rejecting a legitimate structural reference is COSTLY there, so it
# accepts only the canonical punctuation). HERE over-redaction is the safe failure mode — this
# backstop guards an engine-visible INCIDENT message, not a routing field — so the net is WIDER
# (T3.4 R2 gatekeeper finding F5-1: canonical-only patterns let dash-only `123-456-789-01`,
# space-separated `123 456 789 01`, and dots-without-final-dash `123.456.789.01` CPFs — plus the
# analogous CNPJ separator variants — through unredacted). One pattern FAMILY per identifier
# instead of enumerated styles: the CPF (3-3-3-2) / CNPJ (2-3-3-4-2) digit-group shapes with each
# separator slot independently any of `[ .\-/]` (mixed styles match too). The `(?<!\d)`/`(?!\d)`
# digit-boundary lookarounds stop the family from partially matching INSIDE a longer digit
# sequence (an IP octet like `192.168.001.001`, an all-numeric UUID segment) — those must fall
# through to the bare-digit-run rule (or pass through) on their own merits. A bare run of >=11
# digits is the final, broadest arm (bare CPF/CNS/CNPJ, or any long numeric id in free text).
_SEP = r"[ .\-/]"  # one separator between digit groups; slots are independent (mixed styles ok)
_CPF_FORMATTED_RE = re.compile(rf"(?<!\d)\d{{3}}{_SEP}\d{{3}}{_SEP}\d{{3}}{_SEP}\d{{2}}(?!\d)")
_CNPJ_FORMATTED_RE = re.compile(rf"(?<!\d)\d{{2}}{_SEP}\d{{3}}{_SEP}\d{{3}}{_SEP}\d{{4}}{_SEP}\d{{2}}(?!\d)")
_DIGIT_RUN_RE = re.compile(r"\d{11,}")

# CC-06 / HEL-05 — the two identifier families the CPF/CNPJ/digit-run net measurably does NOT
# catch, added when this net was promoted from "exception messages only" to the shared free-text
# scrubber (`redact_free_text`) the agent -> engine start chokepoint runs on every process
# variable. Both are direct beneficiary identifiers under ADR-0006, and both survive the digit
# arms above: an e-mail carries no digit run at all, and a BR phone written with separators
# (`(11) 98765-4321`) is only 10-11 digits SPLIT by punctuation, so `_DIGIT_RUN_RE` never sees an
# 11-run.
#
# E-MAIL: the ordinary `local@domain.tld` shape. No lookarounds needed — an `@` between two
# label runs does not occur in any structured field this edge carries (`process://` refs, TUSS/
# CID codes, pseudo-ids, business keys, ISO dates).
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")

# PHONE, BR, WITH SEPARATORS — deliberately TWO narrow arms instead of one broad one, because
# over-redaction is NOT free here (unlike in an incident message): this scrubber also runs over a
# clinical handoff summary, where destroying a legitimate short number (a date, a BRL amount, a
# TUSS/CID/CEP code, a version string) would degrade the human handoff the contract requires.
#   * `_PHONE_BR_DDD_RE` — anything carrying a DDD (or `+55`): optional `+55`, a 2-digit DDD bare
#     or parenthesized, then the 4-5 + 4 digit-group shape. The DDD is what makes it a phone and
#     not a numeric range: a bare `1234-5678` does NOT match this arm.
#   * `_PHONE_BR_MOBILE_RE` — the DDD-less MOBILE shape only (`9` + 4 digits, separator, 4
#     digits). Anchored on the mandatory Brazilian mobile `9` prefix precisely so that a bare
#     4+4 numeric range (`1000-2000`, `2026-2027`) and a CEP (`12345-678`, 5+3) cannot match.
# `/` is deliberately NOT a separator slot here (unlike the CPF/CNPJ family): `26/07/2026` and
# `12/2026` are dates, and admitting `/` would redact them.
# MEASURED, DISCLOSED GAP (not fixed here, so the boundary is not fabricated): a DDD-less
# 8-digit LANDLINE (`3456-7890`) is indistinguishable from a numeric range by shape alone and is
# NOT redacted; neither is a bare CEP.
_PHONE_SEP = r"[ .\-]"
_PHONE_BR_DDD_RE = re.compile(
    rf"(?<!\d)(?:\+55{_PHONE_SEP}?)?(?:\(\d{{2}}\)|\d{{2}}){_PHONE_SEP}?9?\d{{4}}{_PHONE_SEP}\d{{4}}(?!\d)"
)
_PHONE_BR_MOBILE_RE = re.compile(rf"(?<!\d)9\d{{4}}{_PHONE_SEP}\d{{4}}(?!\d)")

#: Class token substituted for a redacted PHI-shaped substring. Distinct from `REDACTED_PHI`
#: (whole-value, key-based redaction) so an ops reader can tell the two backstops apart in a log.
REDACTED_DIGITS: str = "[REDACTED_DIGITS]"

#: Class tokens for the two families added by CC-06. Distinct from `REDACTED_DIGITS` so an ops
#: reader (or a leak test) can tell WHICH family fired without seeing the value.
REDACTED_EMAIL: str = "[REDACTED_EMAIL]"
REDACTED_PHONE: str = "[REDACTED_PHONE]"

#: Cap on the redacted message forwarded to the engine's incident store. Mirrors the existing
#: `resp.text[:500]` truncation convention (`platform/deploy/engine_deploy.py`) used elsewhere in
#: this codebase for bounding untrusted text before it lands in an error message.
_ERROR_MESSAGE_MAX_CHARS: int = 500
_TRUNCATION_MARKER: str = "...[TRUNCATED]"


def redact_free_text(text: str, *, max_chars: int = _ERROR_MESSAGE_MAX_CHARS) -> str:
    """Redact PHI-shaped substrings from ONE free-text string, one-way, and cap its length.

    THE shared free-text net of this module (CC-06). `redact_error_message` is now a thin caller
    of it (exception messages), and so is the agent -> engine start chokepoint
    (`mcp_cibseven/transport.py::start_process_idempotent`), which had NO free-text scrub at all
    before CC-06: an LLM-drafted `resumo_contexto` / dossier `narrativa` reached
    `start_process_instance` verbatim, so a beneficiary-typed CPF the model copied into its
    summary landed in the engine's process variables in the clear.

    Redacted families, in the order applied (each to its own class token, so a reader can tell
    them apart and a leak test can assert WHICH one fired):
      1. e-mail (`REDACTED_EMAIL`) — first, so an address containing digit groups is removed as
         one unit instead of being partly rewritten by the digit arms below.
      2. separated CPF / CNPJ in any separator style, `[ .\\-/]` per slot (`REDACTED_DIGITS`).
      3. BR phone with separators, DDD/`+55`-bearing or the DDD-less mobile shape
         (`REDACTED_PHONE`).
      4. any remaining run of 11+ contiguous digits (`REDACTED_DIGITS`) — bare CPF/CNS/CNPJ, or
         any other long numeric identifier.
    Then the result is capped at `max_chars` with `_TRUNCATION_MARKER`.

    WHY THIS IS NOT `redact_phi_vars`. That sibling redacts a WHOLE value by KEY name, which is
    right for a general-zone fact payload and WRONG here: `resumo_contexto` is the handoff summary
    a human attendant reads, and SP-OP-ESCALATION-001 §Variáveis requires it "pseudonimizado",
    not absent. This function removes the identifiers and keeps the sentence.

    NOT a general PHI classifier, and never claimed to be: it is a deterministic pattern net over
    the identifier families named above. Clinical narrative content (a diagnosis in prose, a rare
    condition that is identifying in itself) is NOT removed — that is what the `phi=True` routing
    (ADR-0006/ADR-0017) and the prompt's own "NAO inclua dado identificavel" instruction are for.
    """
    scrubbed = _EMAIL_RE.sub(REDACTED_EMAIL, text)
    scrubbed = _CPF_FORMATTED_RE.sub(REDACTED_DIGITS, scrubbed)
    scrubbed = _CNPJ_FORMATTED_RE.sub(REDACTED_DIGITS, scrubbed)
    scrubbed = _PHONE_BR_DDD_RE.sub(REDACTED_PHONE, scrubbed)
    scrubbed = _PHONE_BR_MOBILE_RE.sub(REDACTED_PHONE, scrubbed)
    scrubbed = _DIGIT_RUN_RE.sub(REDACTED_DIGITS, scrubbed)
    if len(scrubbed) > max_chars:
        scrubbed = scrubbed[:max_chars] + _TRUNCATION_MARKER
    return scrubbed


def redact_error_message(error: BaseException | str) -> str:
    """Redact PHI-shaped substrings from an exception message before it reaches an
    engine-visible incident store (`WorkerTransport.handle_failure` / `.handle_bpmn_error`'s
    `error_message`, T3.4 F5).

    Fail-closed, one-way, NEVER raises (mirrors `redact_phi_vars`'s backstop contract — this
    sits on the worker's failure-reporting hot path, and a defect in the scrubber must never
    itself crash the failure report):
      - a separated CPF/CNPJ substring in ANY common separator style — canonical
        (`123.456.789-01`, `12.345.678/0001-90`), dash-only (`123-456-789-01`), space-separated
        (`123 456 789 01`), dots-only (`123.456.789.01`), or mixed — -> `REDACTED_DIGITS`
        (pattern family: the CPF/CNPJ digit-group shapes with `[ .\\-/]` per separator slot,
        digit-boundary-anchored; widened from canonical-only per T3.4 R2 finding F5-1).
      - any remaining run of 11+ contiguous digits (bare CPF/CNS/CNPJ, or any other long numeric
        identifier) -> `REDACTED_DIGITS`.
      - CC-06: an e-mail address -> `REDACTED_EMAIL` and a separator-bearing BR phone ->
        `REDACTED_PHONE`. This function no longer owns the net — it DELEGATES to
        `redact_free_text`, which is the same net the agent -> engine start chokepoint runs, so
        the two edges cannot drift apart. Strictly WIDER than before (two families added, none
        removed), which is the safe direction for an incident message.
      - the result is capped at `_ERROR_MESSAGE_MAX_CHARS`, truncated with `_TRUNCATION_MARKER` —
        bounds an unbounded/adversarial message length regardless of content.

    The error CLASS is preserved as a stable `"{ClassName}: "` prefix when `error` is an
    exception instance (plain strings, e.g. static harness messages with no exception, are
    returned without a prefix) — ops can still diagnose WHAT kind of failure occurred from the
    Cockpit incident view even though the message body may have been scrubbed.

    Keeps its OWN never-raises wrapper (`redact_free_text` may raise on a pathological input):
    this sits on the worker's failure-reporting hot path, where a defect in the scrubber must
    never itself crash the failure report. The chokepoint caller deliberately does NOT swallow —
    there, a scrub failure must refuse the start (`StartVariableRedactionError`).
    """
    try:
        error_class = type(error).__name__ if isinstance(error, BaseException) else None
        scrubbed = redact_free_text(str(error), max_chars=_ERROR_MESSAGE_MAX_CHARS)
        return f"{error_class}: {scrubbed}" if error_class else scrubbed
    except Exception:  # noqa: BLE001 — backstop must never itself raise onto the failure path.
        return "[REDACTED_ERROR]"


# --------------------------------------------------------------------------------------------
# CC-06 — the AGENT -> ENGINE start edge: free-text scrub of process-start variables.
# --------------------------------------------------------------------------------------------
#
# THE GAP THIS CLOSES. `redact_phi_vars` above guards the WORKER -> engine/Kafka leg and redacts
# a whole value by key. `build_start_audit_record` runs it over `provenance.decision_basis` only,
# and binds the start `variables` by a one-way `input_sha256`. Nothing ran over the VARIABLES
# THEMSELVES, so the agent -> engine leg (`start_process_idempotent` ->
# `transport.start_process_instance(process_key, business_key, variables)`) shipped LLM-drafted
# free text to the engine verbatim: Helena's/Lucas's/Fernando's `resumo_contexto`, and the
# `narrativa` nested in every `dossie_<agent>` dict.
#
# WHY A NAMED-KEY ALLOWLIST AND NOT "SCRUB EVERY STRING". These variables are a BPMN process's
# input contract. Business keys, `*_pseudo_id`s, `process://`/`*_ref` pointers, TUSS/CID codes,
# enum tokens, ISO dates and BRL amounts are STRUCTURED values a worker and a DMN read by shape;
# running an identifier net over all of them would corrupt the process (a `numero_guia_tiss` or a
# `matricula_beneficiario` is an 11+ digit run BY CONSTRUCTION). The scrub is therefore applied
# ONLY to the variable names that carry human/LLM free text — the same discipline
# `PHI_PROCESS_VARS` encodes, minus the two names in it that are structured IDENTIFIERS rather
# than prose (`matricula_beneficiario`, `cid10_referencia`), plus the dossier's `narrativa`.
#
# DISCLOSED, NOT FIXED HERE: `matricula_beneficiario` and `cid10_referencia` therefore pass
# through this edge unchanged. Neither is emitted as a start variable by any agent in this tree
# (`grep -rn '"matricula_beneficiario"' src/maezo/agents/` -> only `fernando/delegation.py`'s A2A
# envelope, a different edge with its own guard), and the right control for a whole-value
# identifier is `redact_phi_vars`, not a substring net.

#: Process-variable / dossier-field NAMES whose value is human or LLM-drafted FREE TEXT.
PHI_FREE_TEXT_VARS: frozenset[str] = frozenset(
    {
        # Names shared with `PHI_PROCESS_VARS` (prose fields only — see the block above).
        "justificativa_clinica",
        "fundamentacao_dut",
        "notas_resolucao",
        "resumo_contexto",
        "laudo",
        "diagnostico",
        # The one free-text field EVERY `<agent>/graph.py::_build_dossier` produces, nested inside
        # the `dossie_<agent>` process variable (andre, carolina, fernando, gustavo, lucas,
        # marina, rafael, valentina — and beatriz, which starts no process).
        "narrativa",
    }
)

#: Prefix of the process-variable names carrying an agent DOSSIER (a nested mapping built by
#: `<agent>/graph.py::_build_dossier`). Only these mappings are WALKED; every other structured
#: variable is passed through untouched.
_DOSSIER_VAR_PREFIX: str = "dossie_"

#: Recursion bound for the dossier walk. A dossier is a small, hand-assembled dict (depth 3 at
#: most: `dossie_x.fatos.dmn_refs`), so exceeding this means a malformed or self-referential
#: payload — fail-closed (raise) rather than silently stopping the scrub mid-tree.
_MAX_DOSSIER_DEPTH: int = 8


def redact_free_text_vars(values: Mapping[str, Any]) -> dict[str, Any]:
    """Return a COPY of process-start `values` with every FREE-TEXT field passed through
    `redact_free_text` (CC-06). Structured values are returned unchanged, by construction.

    Scope, precisely:
      - a top-level key in `PHI_FREE_TEXT_VARS` -> its string (or list-of-strings) value is
        scrubbed.
      - a top-level `dossie_*` key whose value is a Mapping -> WALKED recursively (through nested
        mappings and lists of mappings); inside it, the same `PHI_FREE_TEXT_VARS` names are
        scrubbed and everything else passes through.
      - every other key -> passed through UNCHANGED (ids, business keys, enums, `process://`
        refs, pseudo-ids, DMN refs, booleans, amounts).

    MAY RAISE (deliberately, unlike `redact_phi_vars`/`redact_error_message`): the sole caller is
    the start chokepoint, where a scrub that cannot be completed must REFUSE the start rather
    than fall through to a raw passthrough. `ValueError` on a payload deeper than
    `_MAX_DOSSIER_DEPTH` (malformed or self-referential).
    """
    return {key: _redact_var(key, value, depth=0) for key, value in values.items()}


def _redact_var(key: str, value: Any, *, depth: int) -> Any:
    """One (key, value) pair of the start-variable tree. `depth` bounds the dossier walk."""
    if key in PHI_FREE_TEXT_VARS:
        return _redact_free_text_value(value)
    if depth == 0 and not key.startswith(_DOSSIER_VAR_PREFIX):
        # Top level: only a `dossie_*` variable is walked. Everything else is a structured
        # process variable and is returned as-is.
        return value
    return _walk(value, depth=depth)


def _walk(value: Any, *, depth: int) -> Any:
    """Recurse through mappings / lists inside a dossier, scrubbing free-text-named leaves."""
    if depth > _MAX_DOSSIER_DEPTH:
        raise ValueError(
            f"start variables nested deeper than {_MAX_DOSSIER_DEPTH} levels — refusing to scrub "
            "a malformed or self-referential payload"
        )
    if isinstance(value, Mapping):
        return {k: _redact_var(k, v, depth=depth + 1) for k, v in value.items()}
    if isinstance(value, list):
        return [_walk(item, depth=depth + 1) for item in value]
    return value


def _redact_free_text_value(value: Any) -> Any:
    """Scrub a free-text-named value: a string, or a list of them. Anything else (a `None`, a
    number, a nested structure under a free-text name) is left alone — `redact_free_text` is a
    string net, and silently stringifying a non-string here would corrupt the variable's type."""
    if isinstance(value, str):
        return redact_free_text(value)
    if isinstance(value, list):
        return [redact_free_text(item) if isinstance(item, str) else item for item in value]
    return value
