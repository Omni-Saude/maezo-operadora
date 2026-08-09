"""SHARED, STRICT business-key composers for Andre's two key families (M-8).

WHY THIS MODULE EXISTS. Both of Andre's key families had TWO divergent composers each, and the
divergence was silent — the two sites produced DIFFERENT keys for the same case, so an idempotent
start/delegation keyed by one could not find the instance keyed by the other:

  ADEQ   `agents/andre/delegation.py:adequacao_task_id` did NOT strip and did NOT drop empty
         segments, while `agents/andre/graph.py:_business_key` did BOTH. Worse, dropping empty
         segments made the key AMBIGUOUS: a positional 3-tuple collapsed to a 2-segment key, so
         `(regiao="R-001", especialidade="2026-Q3", ciclo="")` and
         `(regiao="R-001", especialidade="", ciclo="2026-Q3")` composed to the IDENTICAL
         `ADEQ-{tenant}-R-001-2026-Q3` — two different cells sharing one anchor.
  PAGTO  `delegation.pagto_task_id` normalised every input (`str(x or "").strip()`) and RAISED on
         a blank tenant / a missing ordem+lote+prestador; `graph._business_key` did neither, so a
         whitespace-padded `ordem_pagamento_id` produced `PAGTO-{t}- 123 ` against the other
         site's `PAGTO-{t}-123`, and a fully blank case minted the degenerate `PAGTO-{t}--`
         instead of refusing.

THE RULE THIS MODULE ENFORCES — POSITION-PRESERVING, NEVER SEGMENT-DROPPING. Every segment of a
composed key holds a FIXED position, and an empty segment is an ERROR, never something silently
removed. That is what kills the ADEQ collision: `ciclo_avaliacao` is either wholly ABSENT (a
3-segment key) or PRESENT AND NON-BLANK (a 4-segment key) — there is no way to reach a 4-segment
shape with an empty middle, so no two distinct cells can ever compose to the same string.

Refusing (rather than minting a degenerate key) is the house discipline already applied at
`tools/workers/base.py:non_blank` and by the pre-existing `pagto_task_id` guard (EB-4 R1): callers
wrap the composer and turn a `ValueError` into a DISCLOSED gap that still opens the human User
Task (`tools/workers/adequacao.py:565-583`, DL-0037) — never a silent bad key.

Leaf module by design: it imports nothing from `graph.py` or `delegation.py` (delegation.py
already imports graph.py, so anything shared must sit below both).
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "ADEQ_PREFIX",
    "PAGTO_PREFIX",
    "adequacao_business_key",
    "is_blank",
    "key_segment",
    "pagto_business_key",
]

ADEQ_PREFIX = "ADEQ"
PAGTO_PREFIX = "PAGTO"


def key_segment(value: Any) -> str:
    """Normalise ANY value into a business-key segment: `str(value or "").strip()`.

    Single normalisation for BOTH composers — the M-8 divergence was precisely that one site
    normalised and the other did not. `None` and `0` and `""` all normalise to `""` (an explicit
    `None` must never become the literal `"None"`, the defect `tools/workers/base.py:non_blank`
    documents), and every other value goes through `str()` so an `int` id composes identically no
    matter which site holds it.
    """
    return str(value or "").strip()


def is_blank(value: Any) -> bool:
    """True iff `value` is absent / empty / whitespace-only after `key_segment` normalisation."""
    return not key_segment(value)


def adequacao_business_key(
    tenant: Any,
    regiao_saude: Any,
    especialidade: Any,
    ciclo_avaliacao: Any = None,
) -> str:
    """`ADEQ-{tenant}-{regiao}-{especialidade}` (+ `-{ciclo}` when a cycle is in scope). STRICT.

    The adequacao CELL key SP-OP-ADEQUACAO-001 itself uses, and the single source both
    `andre.delegation.adequacao_task_id` (the A2A `task_id` / dispatcher Guard 4) and
    `andre.graph._business_key` (the dossier ANCHOR) compose through — so the delegation and the
    anchor can no longer disagree.

    STRICT, POSITION-PRESERVING (the M-8 collision fix): `tenant`, `regiao_saude` and
    `especialidade` are REQUIRED and must be non-blank; `ciclo_avaliacao` is OPTIONAL but, when
    supplied, must ALSO be non-blank. An empty required segment RAISES `ValueError` instead of
    being dropped — dropping is what let `(R-001, "2026-Q3", "")` and `(R-001, "", "2026-Q3")`
    compose to the same key.

    Raises:
        ValueError: any required segment blank, or `ciclo_avaliacao` supplied but blank.
    """
    tenant_s = key_segment(tenant)
    regiao_s = key_segment(regiao_saude)
    especialidade_s = key_segment(especialidade)
    if not tenant_s:
        raise ValueError("adequacao business key requires a non-blank tenant (ADR-0004 tenant scope)")
    if not regiao_s or not especialidade_s:
        raise ValueError(
            "adequacao business key requires non-blank regiao_saude AND especialidade "
            "(the cell identity) — no degenerate/ambiguous ADEQ key"
        )
    base = f"{ADEQ_PREFIX}-{tenant_s}-{regiao_s}-{especialidade_s}"
    if ciclo_avaliacao is None:
        return base
    ciclo_s = key_segment(ciclo_avaliacao)
    if not ciclo_s:
        # Explicitly supplied but blank. Dropping it here would make a 4-tuple indistinguishable
        # from a 3-tuple — exactly the collision this composer exists to prevent. Callers that
        # legitimately have no cycle pass `None` (or omit the argument).
        raise ValueError(
            "adequacao business key: ciclo_avaliacao was supplied but is blank — pass None to "
            "compose the 3-segment cell key, never an empty segment"
        )
    return f"{base}-{ciclo_s}"


def pagto_business_key(
    tenant: Any,
    *,
    ordem_pagamento_id: Any = "",
    numero_lote_tiss: Any = "",
    prestador_id: Any = "",
    business_key: Any = "",
) -> str:
    """`PAGTO-{tenant}-{ordem}` or `PAGTO-{tenant}-{lote}-{prestador}` — the SP-OP-PAGTO-001 key.

    The single source both `andre.delegation.pagto_task_id` (the A2A `task_id`) and
    `andre.graph._business_key` (the idempotent-start key) compose through.

    ENGINE KEY FIRST (GK-dossier finding 1a): a threaded `business_key` is the running instance's
    OWN authoritative key and is honoured VERBATIM — but ONLY when it carries this tenant's
    `PAGTO-{tenant}-` prefix, so a planted/foreign key can never anchor another tenant's case
    (ADR-0004). Otherwise DERIVED ordem-first, falling back to the CONTAS-001-adjudicated variant
    `PAGTO-{tenant}-{numero_lote_tiss}-{prestador_id}`.

    STRICT (EB-4 R1 `non_blank` discipline, now applied at BOTH sites): a blank tenant, or no
    ordem AND no COMPLETE lote+prestador pair, RAISES `ValueError` rather than minting a
    degenerate key such as `PAGTO-amh--`. Every input is normalised through `key_segment`, so a
    whitespace-padded or non-`str` id composes identically wherever it is held — the M-8
    int/str/whitespace divergence.

    Raises:
        ValueError: blank tenant, or neither a usable ordem nor a complete lote+prestador pair.
    """
    tenant_s = key_segment(tenant)
    if not tenant_s:
        raise ValueError("pagto business key requires a non-blank tenant (ADR-0004 tenant scope)")
    engine_key = key_segment(business_key)
    if engine_key and engine_key.startswith(f"{PAGTO_PREFIX}-{tenant_s}-"):
        return engine_key
    ordem = key_segment(ordem_pagamento_id)
    if ordem:
        return f"{PAGTO_PREFIX}-{tenant_s}-{ordem}"
    lote = key_segment(numero_lote_tiss)
    prestador = key_segment(prestador_id)
    if not (lote and prestador):
        raise ValueError(
            "pagto business key requires a non-blank ordem_pagamento_id, or a complete "
            "numero_lote_tiss + prestador_id pair (no degenerate PAGTO business key)"
        )
    return f"{PAGTO_PREFIX}-{tenant_s}-{lote}-{prestador}"
