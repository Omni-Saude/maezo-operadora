"""Perspective fence — provider-perspective vocabulary in payer (operadora) specs.

The process owner of every artifact under `spec/` is the **operadora** (the
payer). It receives guias, lotes and recursos; it emits demonstrativos,
autorizacoes, negativas, glosas and respostas de recurso. An artifact that
tells the platform to *file* an appeal, to *wait for the operadora's answer*,
or to *reconcile money it received* has the perspective inverted — the spec
describes the prestador, not the payer.

This module is the regression gate for that inversion. It is deliberately
**not** a perspective validator: see "Declared limit" below.

Design of record
----------------
`docs/audits/maezo-deep-audit/remediation/REDESIGN-SP-OP-CONTAS-001.md` §6
("Gate de CI"), and ADR-0040 D7. Two surfaces, three rule classes:

* **Tier A** — raw text of `spec/processes/bpmn/*.bpmn` and
  `spec/processes/dmn/*.dmn`, restricted to the declared surfaces (§6.3):
  the `id`/`name`/`errorCode`/`camunda:topic`/`camunda:decisionRef`/
  `camunda:resultVariable`/`bpmnElement` attributes, the text of
  `documentation`/`description`/`conditionExpression`/`text`/
  `camunda:inputParameter`/`camunda:outputParameter` (CDATA included), **and
  XML comments**. Classes applied: R1 + R2-CORE + R2-CTX.
* **Tier B** — the *parsed* nodes (keys and string values, recursively) of
  `spec/processes/dmn/*.yaml`, `spec/agents/*/agent.yaml` and
  `spec/policies/autonomy/*.yaml`. Classes applied: R1 + R2-CORE only; the
  ±40-character window of R2-CTX loses meaning inside multi-line block
  scalars, so R2-CTX is not run there.

The asymmetry "an XML comment counts, a YAML comment does not" is structural,
not a per-file favour: a BPMN comment is design documentation *inside the
process definition*, while a YAML comment in a spec manifest is the audit
narrative of a finding — it exists precisely to quote what an artifact used
to say. What governs behaviour in YAML are keys and values, and those are
exactly what Tier B reads.

Fail-closed
-----------
Every hit is a `Report.error` (blocking; `Report` has no downgradeable
"warning"). An unreadable or malformed file is an error, never a silent skip.
**There is no exception mechanism**: no allowlist file, no inline waiver, no
`historico:` block. The only disambiguations are lexical (the ±40-character
object requirement and actor cue of R2-CTX) and structural (which surface
each tier reads) — and both apply identically to every file.

Where historical references go
------------------------------
"No exception mechanism" has a corollary that is a **convention, not a
marker**: the narrative of a deletion — "this element used to route to
`RECORRER`", "the `recurso_recovery_rate` KPI was dropped because it is a
creditor's metric" — never lives in a BPMN/DMN **XML comment**. Tier A reads
those comments, so recording a removal there re-introduces the vocabulary the
removal took out, and turns a correct edit into a red build. Two places carry
it instead:

* the **docs narrative** — the process contract and test spec under
  `docs/processes/`, `docs/review-queue.md`, `docs/evidence-ledger.md`, the
  ADR, the audit/redesign documents — which is where a reader looks for *why*
  an artifact changed; and
* a **YAML comment** in a spec manifest, whose purpose is precisely to quote
  what an artifact used to say (`spec/agents/marina/agent.yaml` is the live
  example: the deleted KPI survives only in the comment that records the
  deletion). Tier B walks parsed nodes, so a comment is not a node.

There is deliberately **no** `<!-- historico: … -->` or
`<!-- HISTORICAL-REFERENCE -->` escape that would exempt an XML comment from
Tier A. Such a marker *is* the exception mechanism the section above refuses
and that ADR-0040 D7 rules out by name («Nao ha allowlist de excecoes, nem por
arquivo nem por bloco `historico:`» — the hypothesis was evaluated and
refused, D7 and REDESIGN-SP-OP-CONTAS-001 §2.4/§6.2); introducing one is an
owner decision on the ADR, never an implementation choice made here. What a
BPMN/DMN element used to be is recorded by git history and by the docs above.
`TestNoExceptionMechanism` pins the convention as a measured fact: a marked
comment still scores exactly one Tier A hit, and the identical sentence as a
YAML comment scores zero under Tier B.

Declared limit (ADR-0040 D7, §6.8)
----------------------------------
This is a **vocabulary regression gate**, not a perspective validator. It
guarantees that the constructions enumerated below cannot come back without a
red build. It does **not** catch an inversion renamed with vocabulary the
lexicon has never seen — the adversarial gate review proved that with a third
register (`convenio` / `plano de saude` / `repasse` / `defesa` /
`faturamento`), and `test_declared_limit_*` pins that limit as a measured
fact rather than a claim. The primary control remains the human perspective
test (ADR-0040 D2); this module is the backstop.

Measured against `main 35cffd3` (PR-1, unwired)
-----------------------------------------------
A hit is one `(line, rule class, rule id, matched text)` per file; the same
token matched twice on one line counts once.

Tier A — **295 hits in 7 files**, all of them CONTAS/RECURSO chain artifacts.
The other **71** BPMN/DMN under `spec/processes/`: **0**.

    SP-OP-RECURSO-001_Recurso_Glosa.bpmn ............ 196
    SP-OP-CONTAS-001_Processamento_Contas_Glosa.bpmn . 52
    recurso_eligibility.dmn .......................... 18
    recurso_admissibility.dmn ........................ 11
    glosa_triage.dmn ................................. 10
    recurso_sla.dmn ................................... 7
    contas_sla.dmn .................................... 1

Tier B — **7 hits in 3 files**; every other YAML under `spec/`: **0**.

    action-approvals.yaml ........ 4  (:259, :368, :649, :667 — the M5
                                       hanging surfaces, exactly)
    the glosa-triage shadow manifest . 2  (:337 R1, :338 C11)
    marina/agent.yaml ............ 1  (:34 `recurso_recovery_rate`)

The four `action-approvals.yaml` hits are precisely the surfaces finding M5
leaves hanging, so the gate and the finding converge on the same delta — a
claim that only becomes true with `start_recurso` in R1 (design gate delta
D-m3).

(The shadow-candidate manifest is named only in the test's pin, never here:
`tests/unit/spec/test_shadow_candidates_common.py` proves the W4 wave is
unwired by asserting that no file under `src/` names one of those manifests,
and that proof is worth more than a filename in a comment.)

`test_two_chains_hit_count_is_pinned` pins those numbers. The pin
**descends** in PR-3 (RECURSO chain) and reaches **0** in PR-4, which is also
the PR that wires this module into `cli.py`. Until then the module exists and
is measured but is **not** called by `make validate-artifacts` — see
`test_fence_is_not_wired_into_the_cli_yet`.

Forward note for PR-4 (m6, verifier gate review)
-------------------------------------------------
PR-4 is also expected to add `test_r1_tokens_absent_from_src_and_process_docs`
— a sweep of R1 + R2-CORE over `src/maezo/**.py` and `docs/processes/**.md`.
That sweep **must exclude this file** (`perspective.py`). Measured on the live
tree: of 453 matches across 19 files in that scope, **96 are this module's own
lexicon** (`R1_FAMILIES`, `R1_BOUNDS`, docstrings quoting the tokens they
explain) — without the exclusion the new test is unwritable-green by
construction, not because the rest of the codebase carries the vocabulary.
"""

from __future__ import annotations

import bisect
import re
import unicodedata
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

import yaml

from ._loaders import ParseError, load_xml
from .result import Report

TIER_A = "A"
TIER_B = "B"

R1 = "R1"
R2_CORE = "R2-CORE"
R2_CTX = "R2-CTX"

#: Proximity window, in characters, for every rule that needs an object or is
#: absolved by an actor cue (§6.4). Measured on the same inspected line.
PROXIMITY_WINDOW = 40


# ---------------------------------------------------------------------------
# Findings
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Hit:
    """One rule firing, pinned to file + line + rule + the text that matched."""

    path: Path
    line: int
    tier: str
    rule_class: str
    rule_id: str
    matched: str
    context: str

    def render(self) -> str:
        return (
            f"provider-perspective vocabulary at line {self.line} "
            f"[tier {self.tier} / {self.rule_class} {self.rule_id}]: {self.matched!r} "
            f"— {self.context}"
        )


# ---------------------------------------------------------------------------
# Accent folding (length-preserving, so match offsets map back 1:1)
# ---------------------------------------------------------------------------


def _fold_char(char: str) -> str:
    stripped = "".join(c for c in unicodedata.normalize("NFD", char) if unicodedata.category(c) != "Mn")
    folded = stripped.lower()
    return folded if len(folded) == 1 else char


def fold(text: str) -> str:
    """Lowercase and strip accents, preserving length so offsets stay aligned."""
    return "".join(_fold_char(c) for c in text)


# ---------------------------------------------------------------------------
# R1 — tokens (identifiers and decision values), case-sensitive, with boundary
# ---------------------------------------------------------------------------

WHOLE = "whole"
SEGMENT = "segment"
TAIL = "tail"
#: A token matches only where its own segments sit inside one *undelimited*
#: run of the identifier — i.e. no literal `_`/`.`/`-` of the identifier's own
#: spelling falls between them, only a CamelCase transition. See `m1` at
#: `R1_BOUNDS["StartRecurso"]` for why this exists and what it does not close.
GROUP = "group"

_IDENT_RUN = re.compile(r"[A-Za-z0-9_.\-]+")
_SEGMENT_SPLIT = re.compile(r"[_.\-]+")
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def segments_with_groups(identifier: str) -> tuple[tuple[str, int], ...]:
    """Like `segments`, but each output segment is tagged with the index of
    the `_`/`.`/`-`-delimited part it came from (before CamelCase splitting).

    Two segments share a group only when nothing in the identifier's own
    literal delimiters separates them — a CamelCase transition alone does not
    start a new group. `ST_StartRecursoGlosa` -> `[('ST', 0), ('Start', 1),
    ('Recurso', 1), ('Glosa', 1)]` (one delimiter, so groups 0 and 1);
    `Start_RecursoSolicitado` -> `[('Start', 0), ('Recurso', 1),
    ('Solicitado', 1)]` — `Start` and `Recurso` are in *different* groups
    because the identifier itself put a `_` between them.
    """
    out: list[tuple[str, int]] = []
    for group_index, part in enumerate(_SEGMENT_SPLIT.split(identifier)):
        if not part:
            continue
        for piece in _CAMEL_BOUNDARY.split(part):
            if piece:
                out.append((piece, group_index))
    return tuple(out)


def segments(identifier: str) -> tuple[str, ...]:
    """Split an identifier on `_ . -` and on lowercase->uppercase transitions.

    `ST_ReconcilePaymentDeferido` -> `('ST', 'Reconcile', 'Payment', 'Deferido`,
    so `ReconcilePayment` matches with `SEGMENT` boundary while `Reconcile`
    does **not** match inside `Reconciliacao` (a single segment).
    """
    return tuple(piece for piece, _ in segments_with_groups(identifier))


@dataclass(frozen=True, slots=True)
class TokenRule:
    """One R1 token: which family it belongs to and how its boundary is read."""

    family: str
    token: str
    bound: str

    @property
    def rule_id(self) -> str:
        return f"{self.family}:{self.token}"


#: R1 families. A family — not a literal list of the strings that happen to be
#: in the tree today — is the unit, so that a *synonym* of an inverted concept
#: does not walk past the gate. Extensions beyond §6.4's table are marked
#: `[PR-1]` and are additions to families that already exist; they were
#: measured to add zero hits to `main 35cffd3` (see the module docstring).
R1_FAMILIES: dict[str, tuple[str, ...]] = {
    # The appellant's verbs. The *noun* `recurso` is always legitimate — the
    # operadora receives, judges, admits, grants and denies recursos.
    "verbo-recorrente": (
        "RECORRER",
        "Recorrer",
        "NAO_RECORRER",
        "NaoRecorrer",
        "CONTESTAR",
        "Contestar",
        "IMPUGNAR",
        "PROTESTAR",
        "PLEITEAR",
        "RECURSAR",
        "RECLAMAR",  # [PR-1] gate attack NFN2
        "Reclamar",  # [PR-1]
    ),
    # Acquiescence: the appellant giving up, or accepting the loss.
    "aquiescencia": (
        "ACEITAR_GLOSA",
        "AceitarGlosa",
        "ACATAR_GLOSA",
        "AcatarGlosa",
        "ACATAR",
        "CONFORMAR_GLOSA",
        "DESISTIR",
        "Desistir",
        "DESISTENCIA",
        "RENUNCIAR_RECURSO",
        "register_desistencia",
        "RegisterDesistencia",
        "justificativa_desistencia",
        "valor_glosa_aceito",
        "register_glosa_accept",
        "RegisterGlosaAccept",
        "GlosaAcceptNotHuman",
        "DesistenciaNotHuman",
        "ERR_GLOSA_ACCEPT_NOT_HUMAN",
        "ERR_DESISTENCIA_NOT_HUMAN",
        "ABSORVER_PERDA",  # [PR-1] gate attack NFN2
        "AbsorverPerda",  # [PR-1]
        "PERDA_ACEITA",  # [PR-1] gate attack NFN5
        "PerdaAceita",  # [PR-1]
    ),
    # Re-filing what the payer only ever receives.
    "reapresentacao": (
        "REENVIAR",
        "Reenviar",
        "REAPRESENTAR",
        "Reapresentar",
        "RETRANSMITIR",
        "REFATURAR",
        "REFAZER_GUIA",  # [PR-1] gate attack NFN2
        "RefazerGuia",  # [PR-1]
    ),
    # "Appealable" as a property the platform asserts about its own decision.
    "recorrivel": (
        "RECORRIVEL",
        "NAO_RECORRIVEL",
        "CONTESTAVEL",
        "NAO_CONTESTAVEL",
        "IMPUGNAVEL",
        "PASSIVEL_DE_RECURSO",
    ),
    # Keeping / partially keeping an appeal — the appellant's move, not the judge's.
    "manutencao": (
        "MANTER_RECURSO",
        "ManterRecurso",
        "RECURSO_PARCIAL",
        "NAO_INTERPOSTO",
        "nao_interposto",
        "NaoInterposto",
    ),
    # A third party's answer: only an outsider names the operadora's decision
    # as an inbound variable.
    "resposta-terceiro": (
        "resposta_operadora",
        "decisao_operadora",
        "retorno_operadora",
        "resposta_fonte_pagadora",
    ),
    "ciclo-recorrente": (
        "submit_appeal",
        "SubmitAppeal",
        "track_status",
        "TrackStatus",
        "RECAPPEAL",
        "AguardarResposta",
        "reconcile_payment",
        "ReconcilePayment",
        "protocolo_recurso",
        # D-m3 (design gate delta): the payer never *starts* an appeal against
        # its own glosa. Without these two, the Tier-B hits of
        # `action-approvals.yaml` do not converge with the surface finding M5
        # deletes, which is the claim §6.7 makes about this gate.
        "start_recurso",
        "StartRecurso",
        # [PR-1] gate attack NFN-E / verifier m5: repository ids are
        # predominantly pt-BR (`ST_SubmeterRespostaConceder`,
        # `Start_RecursoSolicitado`), so the English `TrackStatus` this family
        # already carries is the exception, not the rule — the pt-BR spelling
        # is the more likely regression. Measured to add 0 hits to `main`.
        "acompanhar_recurso",
        "AcompanharRecurso",
        "acompanhar_status",
        "AcompanharStatus",
    ),
    "ancora-kpi": ("data_ciencia_glosa", "recurso_recovery_rate"),
    "espera-resposta": ("resposta_recebida", "RespostaRecebida"),
}

#: Tokens whose boundary is not `SEGMENT`. `recurso_recovery_rate` is a whole
#: identifier (a KPI name); `resposta_recebida` must be a *tail* so that
#: `resposta_recebida_ans` — a legitimate name for an answer from the **ANS** —
#: is not rejected.
#:
#: `StartRecurso` (m1, verifier gate review): a plain `segment` boundary also
#: matches `Start_RecursoRecebido`/`Start_RecursoSolicitado` — the *inbound*
#: start event the redesign gives RECURSO-001, and one of the gate's own
#: payer-legitimate sentences (`Start_RecursoSolicitado` is live today at
#: `SP-OP-RECURSO-001_Recurso_Glosa.bpmn:86`). A first attempt used `tail`
#: instead, but that over-corrected: it also released *every* suffixed
#: provider-perspective id in the same family — `ST_StartRecursoGlosa`,
#: `ST_StartRecursoDeGlosa` — while the `segment`-bound siblings of the same
#: family (`SubmitAppeal`, `TrackStatus`) correctly caught their own suffixed
#: forms. `GROUP` is the narrower fix: it matches wherever `Start` and
#: `Recurso` sit inside one CamelCase run with **no** literal `_`/`.`/`-` of
#: the identifier's own spelling between them (`ST_StartRecursoGlosa`,
#: `ST_StartRecurso`), and releases exactly the shape where the identifier
#: itself puts a delimiter between `Start` and `Recurso`
#: (`Start_RecursoSolicitado`, `Start_RecursoRecebido`).
#:
#: Declared residual (still open, by construction): an id of the *exact*
#: shape `Start_Recurso<Capitalized>` — e.g. a hypothetical
#: `Start_RecursoGlosa` — is structurally indistinguishable from the
#: legitimate rename and also escapes. Closing that requires reading what the
#: suffix means, which is outside a vocabulary gate (ADR-0040 D7); it is
#: pinned as a known miss by
#: `test_start_recurso_group_boundary_declared_residual`, not silently
#: accepted.
#:
#: `start_recurso` (lowercase) needs no such carve-out: unlike the CamelCase
#: BPMN element id, there is no legitimate lowercase analogue anywhere in the
#: tree — every occurrence names the inbound-glosa payer action
#: (`operadora.contas.start_recurso`) — so it takes the plain `segment`
#: boundary (the default; not listed below) and is caught with or without a
#: suffix (`operadora.contas.start_recurso_glosa`, m1 EV-1).
R1_BOUNDS: dict[str, str] = {
    "recurso_recovery_rate": WHOLE,
    "resposta_recebida": TAIL,
    "RespostaRecebida": TAIL,
    "StartRecurso": GROUP,
}


def _build_r1() -> tuple[TokenRule, ...]:
    rules: list[TokenRule] = []
    for family, tokens in R1_FAMILIES.items():
        for token in tokens:
            rules.append(TokenRule(family, token, R1_BOUNDS.get(token, SEGMENT)))
    return tuple(rules)


R1_TOKENS: tuple[TokenRule, ...] = _build_r1()

_R1_COMPILED: tuple[tuple[TokenRule, tuple[str, ...]], ...] = tuple(
    (rule, segments(rule.token)) for rule in R1_TOKENS
)


def _subsequence_starts(haystack: Sequence[str], needle: Sequence[str]) -> list[int]:
    size = len(needle)
    if size == 0 or size > len(haystack):
        return []
    return [i for i in range(len(haystack) - size + 1) if tuple(haystack[i : i + size]) == tuple(needle)]


def _scan_tokens(text: str) -> Iterator[tuple[str, str, str]]:
    """Yield `(rule_class, rule_id, matched_run)` for every R1 token in `text`."""
    for match in _IDENT_RUN.finditer(text):
        run = match.group(0)
        run_grouped = segments_with_groups(run)
        run_segments = tuple(piece for piece, _ in run_grouped)
        run_groups = tuple(group for _, group in run_grouped)
        for rule, token_segments in _R1_COMPILED:
            if rule.bound == WHOLE:
                if run == rule.token:
                    yield R1, rule.rule_id, run
                continue
            starts = _subsequence_starts(run_segments, token_segments)
            if not starts:
                continue
            if rule.bound == TAIL and not any(
                start + len(token_segments) == len(run_segments) for start in starts
            ):
                continue
            if rule.bound == GROUP and not any(
                len(set(run_groups[start : start + len(token_segments)])) == 1 for start in starts
            ):
                continue
            yield R1, rule.rule_id, run


# ---------------------------------------------------------------------------
# R2 — prose. Matched against accent-folded, lowercased text.
# ---------------------------------------------------------------------------

#: The object a contextual (R2-CTX) rule needs within `PROXIMITY_WINDOW` for
#: an otherwise ambiguous verb to be an inversion.
OBJETO_RECURSAL = (
    r"\b(recursos?|glosas?|contas?|guias?|lote|decisao|negativa|indeferimento|demonstrativo"
    # `pleito` (PR-4, PR-1 gatekeeper residual): X4 already names the noun, but X4 is
    # absolved by `DECISAO_PAGADOR`, so a sentence that pairs a payer decision verb with an
    # appellant verb — "Julgar o pleito que interpusemos contra a glosa" — cleared X4 and had
    # no other rule to catch it. Adding `pleito` here gives X1 the object it needs, so the
    # appellant VERB is caught on its own terms while the payer's decision verb keeps
    # absolving X4. The one live occurrence (`SP-OP-NIP-001_Resposta_NIP.bpmn:482`,
    # "conceder o pleito") still passes: it carries no X1 verb at all.
    r"|pleito)\b"
)

#: The actor cue that *absolves* an R2-CTX match: the sentence is describing
#: what the prestador/beneficiario does, which a payer spec may say freely.
#:
#: `nip` (a domain instrument, not a party) used to be in this set, and it was
#: wrong to put it there (m2/m3, verifier gate review): isolating the two
#: corrections it was said to justify shows they are **not** both explained by
#: it. Removing only `nip` reopens `SP-OP-NIP-001_Resposta_NIP.bpmn:482`
#: ("conceder o pleito"); removing only the adjacent-negation rule (`NEGACAO`
#: below) reopens `nip_classification.dmn:60` ("sem contestacao de
#: negativa") — the two fixes are one-for-one with the two lines, not
#: two-for-two, because at `:60` the token `NIP` sits past `PROXIMITY_WINDOW`
#: from `contestacao` and was never actually in range. Worse, as an
#: *instrument* rather than a party, `nip` absolved all four R2-CTX rules
#: anywhere within the window regardless of who the subject was — laundering
#: real inversions such as "Registrar o protocolo NIP e reapresentar a conta
#: glosada". `:482` is cleared instead by `DECISAO_PAGADOR` below, anchored on
#: the payer's own decision verb, which is what the line's text actually is
#: ("conceder o pleito" — the operadora conceding, not the beneficiary
#: filing).
PISTA_DE_ATOR = r"\b(prestador(es)?|beneficiario(s)?|titular|contratante|credenciado|recorrente|requerente)\b"

#: Absolves X4 specifically: `pleito` next to the payer's own decision verb is
#: the operadora ruling on a request, not the appellant naming one. Narrower
#: than the actor cue on purpose — it names the payer's act instead of naming
#: an instrument (`nip`) that has no consistent subject. Clears
#: `SP-OP-NIP-001_Resposta_NIP.bpmn:482` ("conceder o pleito"); does **not**
#: clear a bare `indeferido`/`deferido` participle standing alone with no
#: verb, so `test_flags_fonte_pagadora_framing`'s "Pleito indeferido pela
#: fonte pagadora" is untouched.
DECISAO_PAGADOR = r"\b(conceder|deferir|indeferir|julgar|responder)\b"


@dataclass(frozen=True, slots=True)
class ProseRule:
    """One R2 rule: a folded-text pattern, an optional object, an optional out."""

    rule_id: str
    rule_class: str
    pattern: re.Pattern[str]
    near: re.Pattern[str] | None = None
    actor_absolves: bool = False
    decision_absolves: re.Pattern[str] | None = None


def _rule(
    rule_id: str,
    rule_class: str,
    pattern: str,
    *,
    near: str | None = None,
    actor_absolves: bool = False,
    decision_absolves: str | None = None,
) -> ProseRule:
    return ProseRule(
        rule_id=rule_id,
        rule_class=rule_class,
        pattern=re.compile(pattern),
        near=re.compile(near) if near is not None else None,
        actor_absolves=actor_absolves,
        decision_absolves=re.compile(decision_absolves) if decision_absolves is not None else None,
    )


#: R2-CORE — prose with no legitimate payer reading, whatever the subject is.
#: No actor cue saves these: "aceitar a glosa" is the appellant's act even in a
#: sentence that names the prestador.
R2_CORE_RULES: tuple[ProseRule, ...] = (
    _rule("C1", R2_CORE, r"resposta d[ae] operadora|decisao d[ae] operadora|retorno d[ae] operadora"),
    _rule("C2", R2_CORE, r"resposta de terceiro|decisao de terceiro|decisao alheia|resposta alheia"),
    _rule("C3", R2_CORE, r"operadora externa"),
    _rule("C4", R2_CORE, r"fonte pagadora"),
    _rule("C5", R2_CORE, r"\bnoss[oa]s?\b"),
    _rule("C6", R2_CORE, r"\baguardar\s+(a\s+)?(resposta|decisao|retorno)\s+d[ae]\b"),
    _rule(
        "C7",
        R2_CORE,
        r"(junto a|perante|contra|dirigid[oa] a|enderecad[oa] a)\s+(a\s+)?operadora",
    ),
    _rule("C8", R2_CORE, r"\bprotocolar\s+(o\s+|a\s+)?(recurso|conta|pleito|guia)"),
    _rule(
        "C9",
        R2_CORE,
        r"aceitar a glosa|aceite de glosa|aceitacao de (uma )?glosa|glosa aceita"
        r"|acatar a glosa|\bdesisten|\bdesistir\b",
    ),
    _rule(
        "C10",
        R2_CORE,
        r"\bconcilia(r|cao|ndo)\b",
        near=r"(re-?pagamento|credito recebid|pagamento recebid|recebiment)",
    ),
    _rule(
        "C11",
        R2_CORE,
        r"peticao de recurso|carta de recurso|prestador recorrente|autor do recurso"
        r"|candidata a recurso",
    ),
    _rule("C12", R2_CORE, r"acompanhar (o )?(status|andamento)"),
)

#: R2-CTX — prose that is an inversion only when it has a recursal object and
#: no actor cue nearby. This is what lets a payer spec state the prestador's
#: deadline to appeal without the gate rejecting correct prose.
R2_CTX_RULES: tuple[ProseRule, ...] = (
    _rule(
        "X1",
        R2_CTX,
        # `interpus[a-z]*` (PR-4, PR-1 gatekeeper residual): the alternation carried only the
        # infinitive/gerund/participle of `interpor`, so the PRETERITE — and in particular the
        # first-person plural the platform would use about itself, "o pleito que interpusemos" —
        # had no rule at all. It is the same verb, in the tense a self-describing inversion
        # actually uses. Measured: 0 new hits on the live tree.
        r"\b(recorrer|recorrendo|recorrivel|interpor|interpoe|interpondo|interposto"
        r"|interposicao|interpus[a-z]*|impugnar|contestar|contestacao|contestavel|reapresentar"
        r"|reenviar|retransmitir|pleitear)\b",
        near=OBJETO_RECURSAL,
        actor_absolves=True,
    ),
    _rule(
        "X2",
        R2_CTX,
        r"\b(identificar|apurar|conferir|analisar|ler|verificar|receber"
        r"|recebiment[a-z]*|recebid[oa]s?)\b",
        near=r"\bdemonstrativo\b",
        actor_absolves=True,
    ),
    _rule("X3", R2_CTX, r"\bregistra(r)? reenvio\b", actor_absolves=True),
    _rule("X4", R2_CTX, r"\bpleito\b", actor_absolves=True, decision_absolves=DECISAO_PAGADOR),
)

_ACTOR_CUE = re.compile(PISTA_DE_ATOR)

#: A negation *immediately* before an R2-CTX match absolves it: "sem
#: contestacao de negativa" and "verificavel sem recorrer a nenhuma fonte
#: externa" describe the **absence** of an appellant act, which is a fact a
#: payer spec states about what it received — not an act the platform
#: performs. The anchor is `$`, so this is adjacency, not a window: it cannot
#: be used to launder a real inversion elsewhere on the line, and R1 (which
#: has no absolution at all) is untouched by it.
NEGACAO = re.compile(r"\b(sem|ausencia de|inexistencia de)\s+(a\s+|o\s+|uma\s+|um\s+)?$")


def _has_near(folded: str, match: re.Match[str], pattern: re.Pattern[str]) -> bool:
    start = max(0, match.start() - PROXIMITY_WINDOW)
    end = min(len(folded), match.end() + PROXIMITY_WINDOW)
    return pattern.search(folded, start, end) is not None


def _is_negated(folded: str, match: re.Match[str]) -> bool:
    return NEGACAO.search(folded, 0, match.start()) is not None


def _scan_prose(text: str, rules: Iterable[ProseRule]) -> Iterator[tuple[str, str, str]]:
    """Yield `(rule_class, rule_id, matched_text)` for every R2 rule in `text`."""
    folded = fold(text)
    for rule in rules:
        for match in rule.pattern.finditer(folded):
            if rule.near is not None and not _has_near(folded, match, rule.near):
                continue
            if rule.actor_absolves and _has_near(folded, match, _ACTOR_CUE):
                continue
            if rule.actor_absolves and _is_negated(folded, match):
                continue
            if rule.decision_absolves is not None and _has_near(folded, match, rule.decision_absolves):
                continue
            yield rule.rule_class, rule.rule_id, text[match.start() : match.end()]


# ---------------------------------------------------------------------------
# The scanner over one piece of inspectable text
# ---------------------------------------------------------------------------


def scan_line(text: str, *, tier: str) -> list[tuple[str, str, str]]:
    """Scan one inspected line/scalar; return deduplicated `(class, id, matched)`.

    Tier A runs R1 + R2-CORE + R2-CTX; Tier B runs R1 + R2-CORE only (§6.3).
    """
    rules: tuple[ProseRule, ...] = R2_CORE_RULES if tier == TIER_B else R2_CORE_RULES + R2_CTX_RULES
    found: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for item in (*_scan_tokens(text), *_scan_prose(text, rules)):
        if item not in seen:
            seen.add(item)
            found.append(item)
    return found


# ---------------------------------------------------------------------------
# Tier A — the XML surface
# ---------------------------------------------------------------------------

#: Attributes whose value is inspected, by local name (namespace prefix
#: stripped): `camunda:topic` is `topic`, `camunda:decisionRef` is
#: `decisionRef`. `label` is the DMN spelling of `name` — it titles an
#: `input`/`output` column, and §6.7's own sample table depends on it
#: (`recurso_sla.dmn:52` carries `data_ciencia_glosa` in a `label`).
#: `sourceRef`/`targetRef` are deliberately **absent** — they only repeat an
#: id that is itself inspected where it is declared, so including them would
#: double-count without adding coverage.
INSPECTED_ATTRIBUTES = frozenset(
    {"id", "name", "label", "errorCode", "topic", "decisionRef", "resultVariable", "bpmnElement"}
)

#: Elements whose character data is inspected, by local name.
INSPECTED_TEXT_ELEMENTS = frozenset(
    {"documentation", "description", "conditionExpression", "text", "inputParameter", "outputParameter"}
)

_TAG_NAME = re.compile(r"[A-Za-z_][\w.\-]*(?::[A-Za-z_][\w.\-]*)?")
_ATTRIBUTE = re.compile(
    r"(?P<name>[A-Za-z_][\w.\-]*(?::[A-Za-z_][\w.\-]*)?)\s*=\s*(?P<q>[\"'])(?P<value>.*?)(?P=q)",
    re.DOTALL,
)


def _local(qname: str) -> str:
    return qname.rsplit(":", 1)[-1]


def inspectable_xml_lines(source: str) -> dict[int, str]:
    """Map line number -> the inspectable text of that line (§6.3, Tier A).

    Everything outside the declared surfaces (tag names, namespace
    declarations, `xsi:type`, `sourceRef`/`targetRef`, DI geometry) is dropped;
    XML comments are kept, because in a BPMN/DMN they are design documentation
    inside the process definition.
    """
    fragments: dict[int, list[str]] = {}
    line_starts = [0, *(m.end() for m in re.finditer("\n", source))]

    def line_at(pos: int) -> int:
        return bisect.bisect_right(line_starts, pos)

    def add(start: int, blob: str) -> None:
        first = line_at(start)
        for offset, piece in enumerate(blob.split("\n")):
            stripped = piece.strip()
            if stripped:
                fragments.setdefault(first + offset, []).append(stripped)

    stack: list[str] = []
    index = 0
    size = len(source)
    while index < size:
        lt = source.find("<", index)
        if lt == -1:
            if stack and stack[-1] in INSPECTED_TEXT_ELEMENTS:
                add(index, source[index:])
            break
        if lt > index and stack and stack[-1] in INSPECTED_TEXT_ELEMENTS:
            add(index, source[index:lt])

        if source.startswith("<!--", lt):
            end = source.find("-->", lt + 4)
            body_end = size if end == -1 else end
            add(lt + 4, source[lt + 4 : body_end])
            index = size if end == -1 else end + 3
            continue
        if source.startswith("<![CDATA[", lt):
            end = source.find("]]>", lt + 9)
            body_end = size if end == -1 else end
            if stack and stack[-1] in INSPECTED_TEXT_ELEMENTS:
                add(lt + 9, source[lt + 9 : body_end])
            index = size if end == -1 else end + 3
            continue
        if source.startswith("<?", lt) or source.startswith("<!", lt):
            end = source.find(">", lt)
            index = size if end == -1 else end + 1
            continue

        closing = source.startswith("</", lt)
        name_match = _TAG_NAME.match(source, lt + (2 if closing else 1))
        if name_match is None:
            index = lt + 1
            continue
        local = _local(name_match.group(0))

        cursor = name_match.end()
        while cursor < size:
            char = source[cursor]
            if char in "\"'":
                close = source.find(char, cursor + 1)
                cursor = size if close == -1 else close + 1
                continue
            if char == ">":
                cursor += 1
                break
            cursor += 1
        tag_text = source[lt:cursor]
        self_closing = tag_text.rstrip().endswith("/>")

        if closing:
            if local in stack:
                while stack and stack.pop() != local:
                    pass
        else:
            for attribute in _ATTRIBUTE.finditer(tag_text):
                if _local(attribute.group("name")) in INSPECTED_ATTRIBUTES:
                    add(lt + attribute.start("value"), attribute.group("value"))
            if not self_closing:
                stack.append(local)
        index = cursor

    return {line: " ".join(parts) for line, parts in sorted(fragments.items())}


def scan_xml_file(path: Path, report: Report) -> list[Hit]:
    """Scan one `.bpmn`/`.dmn` file. A parse failure is an error, not a skip."""
    try:
        load_xml(path)
        source = path.read_text(encoding="utf-8")
    except ParseError as exc:
        report.error(path, str(exc))
        return []
    except OSError as exc:
        report.error(path, f"could not read file: {exc}")
        return []
    except UnicodeDecodeError as exc:
        report.error(path, f"could not decode file as UTF-8: {exc}")
        return []

    hits: list[Hit] = []
    for line, text in inspectable_xml_lines(source).items():
        for rule_class, rule_id, matched in scan_line(text, tier=TIER_A):
            hits.append(
                Hit(
                    path=path,
                    line=line,
                    tier=TIER_A,
                    rule_class=rule_class,
                    rule_id=rule_id,
                    matched=matched,
                    context=_clip(text),
                )
            )
    return hits


# ---------------------------------------------------------------------------
# Tier B — the parsed-YAML surface
# ---------------------------------------------------------------------------

_YAML_STR_TAG = "tag:yaml.org,2002:str"


def _walk_yaml(node: yaml.Node, path: str, seen: set[int], out: list[tuple[int, str, str]]) -> None:
    if isinstance(node, yaml.ScalarNode):
        if node.tag == _YAML_STR_TAG:
            out.append((node.start_mark.line + 1, path or ".", node.value))
        return
    if id(node) in seen:  # anchors/aliases resolve to the same node object
        return
    seen.add(id(node))
    if isinstance(node, yaml.MappingNode):
        for key, value in node.value:
            key_path = f"{path}.{key.value}" if isinstance(key, yaml.ScalarNode) else path
            _walk_yaml(key, key_path, seen, out)
            _walk_yaml(value, key_path, seen, out)
    elif isinstance(node, yaml.SequenceNode):
        for position, item in enumerate(node.value):
            _walk_yaml(item, f"{path}[{position}]", seen, out)


def yaml_scalars(source: str) -> list[tuple[int, str, str]]:
    """Return `(line, node_path, value)` for every string key/value in `source`.

    Reads the *composed node tree*, so YAML comments are not a surface — only
    what actually governs behaviour is.
    """
    out: list[tuple[int, str, str]] = []
    try:
        documents = list(yaml.compose_all(source, Loader=yaml.SafeLoader))
    except yaml.YAMLError as exc:
        raise ParseError(f"malformed YAML: {exc}") from exc
    for document in documents:
        if document is not None:
            _walk_yaml(document, "", set(), out)
    return out


def scan_yaml_file(path: Path, report: Report) -> list[Hit]:
    """Scan one spec YAML. A parse failure is an error, not a skip."""
    try:
        source = path.read_text(encoding="utf-8")
        scalars = yaml_scalars(source)
    except ParseError as exc:
        report.error(path, str(exc))
        return []
    except OSError as exc:
        report.error(path, f"could not read file: {exc}")
        return []
    except UnicodeDecodeError as exc:
        report.error(path, f"could not decode file as UTF-8: {exc}")
        return []

    hits: list[Hit] = []
    seen: set[tuple[int, str, str, str]] = set()
    for line, node_path, value in scalars:
        for rule_class, rule_id, matched in scan_line(value, tier=TIER_B):
            key = (line, rule_class, rule_id, matched)
            if key in seen:
                continue
            seen.add(key)
            hits.append(
                Hit(
                    path=path,
                    line=line,
                    tier=TIER_B,
                    rule_class=rule_class,
                    rule_id=rule_id,
                    matched=matched,
                    context=f"node {node_path}",
                )
            )
    return hits


def _clip(text: str, limit: int = 120) -> str:
    collapsed = " ".join(text.split())
    return collapsed if len(collapsed) <= limit else collapsed[: limit - 1] + "…"


# ---------------------------------------------------------------------------
# Entry points (the CLI wiring lands in PR-4 — see the module docstring)
# ---------------------------------------------------------------------------


def check_processes(
    bpmn_files: Sequence[Path],
    dmn_files: Sequence[Path],
    yaml_files: Sequence[Path],
    report: Report,
) -> None:
    """Tier A over the BPMN/DMN, Tier B over the `spec/processes/dmn/*.yaml`."""
    for path in (*bpmn_files, *dmn_files):
        _report_hits(scan_xml_file(path, report), report)
    for path in yaml_files:
        _report_hits(scan_yaml_file(path, report), report)


def check_yaml_defs(paths: Sequence[Path], report: Report) -> None:
    """Tier B over `spec/agents/*/agent.yaml` and `spec/policies/autonomy/*.yaml`."""
    for path in paths:
        _report_hits(scan_yaml_file(path, report), report)


def check_processes_root(root: Path, report: Report) -> None:
    """Run the fence over a whole `spec/processes` root (both tiers)."""
    bpmn_dir = root / "bpmn"
    dmn_dir = root / "dmn"
    check_processes(
        sorted(bpmn_dir.glob("*.bpmn")) if bpmn_dir.is_dir() else [],
        sorted(dmn_dir.glob("*.dmn")) if dmn_dir.is_dir() else [],
        sorted(dmn_dir.glob("*.yaml")) if dmn_dir.is_dir() else [],
        report,
    )


def check_agents_root(root: Path, report: Report) -> None:
    """Run Tier B over a whole `spec/agents` root."""
    check_yaml_defs(sorted(root.glob("*/agent.yaml")), report)


def check_policies_root(root: Path, report: Report) -> None:
    """Run Tier B over a whole `spec/policies` root."""
    check_yaml_defs(sorted((root / "autonomy").glob("*.yaml")), report)


def _report_hits(hits: Sequence[Hit], report: Report) -> None:
    for hit in hits:
        report.error(hit.path, hit.render())
