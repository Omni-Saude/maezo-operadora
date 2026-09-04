"""The shared eval runner (T3.2 wave B0). Owned exclusively by wave B0.

Every family test module (`test_classifier_evals.py`, `test_dossier_adverse_evals.py`,
`test_dossier_admin_evals.py`) drives its golden cases through this module's `run_case` and
asserts the result with `assert_expect`/`assert_no_leak`/`score_live` — never hand-rolls graph
wiring or JSON parsing. Keeping ALL of that logic here (not per-family-file) is what makes the
44-eval matrix a genuine regression baseline rather than 44 independent, subtly-different
reimplementations of the same plumbing.

Pass-criterion types (T3.2 design §5/§6), each backed by one assertion helper below:
    RT  (exact route)             -> `assert_expect(state, {"next_kind": ...})`
    SF  (structured-field match)  -> `assert_expect(state, {"fields": {...}})`
    ABS (canary-absent)           -> `assert_no_leak(state, leak_canaries)`
    TH  (live threshold >= 0.9)   -> `score_live(...)` + `assert_live_score(...)` (Tier B only)
`assert_expect`'s `expect` mapping may carry `next_kind` and/or `fields` together, so a single
call covers RT+SF combined cases (e.g. EVL-HELENA-01: RT+SF).

CL  (clarity/legibility, WP-EVALS gap 10.3) -> `score_clarity(...)` + `assert_clarity(...)`.
A NEW pass-criterion type added by WP-EVALS (gaps 10.3/11.5) alongside the five above — kept
here rather than in a family test module because it is generic, agent-agnostic text scoring
(sentence length / forbidden jargon / mandatory disclaimers) on whatever field a golden names,
exactly the shared plumbing this module exists to hold. It judges ONLY the clarity of the
wording a graph emits to a beneficiary (e.g. Helena's `response_text`) — never the clinical
content of any SME-gated DMN table.

`RuleAwareFakeDmnTransport` / the `__rules__` branch of `register_dmn_fixture` (RAF-06, RAF-01,
2026-09-04) similarly ADD to this module — see their own docstrings below for the why (a static
`dmn_fixture` row is vacuous for a routing assertion) and the shape. Unlike WP-EVALS' change
above, RAF-06 is not purely additive: it also EDITS the single existing line in `run_case` that
builds the fake DMN transport (`FakeDmnTransport()` -> `RuleAwareFakeDmnTransport()`), because a
conditional (`__rules__`) fixture cannot be served by the base fake and `run_case` has no way to
know in advance which cases will need it. This is the README's second documented exception to
"nobody but B0 edits `_harness.py`". No existing eval's behavior changes: a case without
`__rules__` in its `dmn_fixture` falls through `RuleAwareFakeDmnTransport.evaluate` to
`FakeDmnTransport.evaluate`'s identical static-row path.
"""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from maezo.tools.mcp_cibseven.transport import CibSevenError, FakeCibSevenTransport, ProcessInstance
from maezo.tools.workers.dmn_transport import FakeDmnTransport
from tests.support.audit_fakes import FakeStartAuditSink

from .conftest import (
    ReplayExhaustedError,
    ReplayInferenceProvider,
    ReplayUnconsumedResponsesError,
    load_golden,
)

# Re-exported so family test modules need only `from tests.evals._harness import ...`.
__all__ = [
    "ClarityReport",
    "FailingStartCibSevenTransport",
    "ReplayExhaustedError",
    "ReplayUnconsumedResponsesError",
    "RULES_KEY",
    "RuleAwareFakeDmnTransport",
    "RunResult",
    "assert_clarity",
    "assert_expect",
    "assert_live_score",
    "assert_no_leak",
    "load_golden",
    "mutate_expected_route",
    "mutate_extend_last_sentence",
    "mutate_plant_canary",
    "mutate_replace_last_response",
    "register_dmn_fixture",
    "run_case",
    "run_mutation_check",
    "score_clarity",
    "score_live",
]


# ---------------------------------------------------------------------------
# run_case — build the compiled graph with injected fakes, drive one turn, return everything a
# case's assertions might need.
# ---------------------------------------------------------------------------


@dataclass
class RunResult:
    """Everything a case's assertions might need after driving one turn.

    `state` is the final graph output (what `assert_expect`/`assert_no_leak` check against by
    default). The fakes are exposed too — a PL-class eval on a dossier agent, for instance, may
    need to inspect `cibseven`'s recorded start-process variables (the narrative/decision_basis
    live there, not in the returned state), mirroring how
    `tests/unit/agents/test_helena.py::test_cpf_bearing_field_value_never_reaches_engine_variables`
    inspects a recording CibSeven double directly.
    """

    state: dict[str, Any]
    inference: ReplayInferenceProvider
    dmn: FakeDmnTransport
    cibseven: FakeCibSevenTransport
    audit_sink: FakeStartAuditSink
    extra: dict[str, Any] = field(default_factory=dict)


#: Marcador de fixture CONDICIONAL num `dmn_fixture` de golden (ver `RuleAwareFakeDmnTransport`).
RULES_KEY = "__rules__"


class RuleAwareFakeDmnTransport(FakeDmnTransport):
    """`FakeDmnTransport` que tambem entende fixture CONDICIONAL (`__rules__`), FIRST-hit.

    POR QUE ISTO EXISTE (RAF-01/RAF-06, 04/09/2026). Uma fixture estatica registra a MESMA linha
    para qualquer entrada, e isso torna vacuo todo golden cuja pergunta seja "esta tabela PODE
    dizer isso a partir do que o grafo de fato lhe manda?". Foi o caso de EVL-RAFAEL-02/-03: as
    duas registravam `auth_auto_approval -> AUTO_APROVAR` incondicional, e faziam a rota
    `auto_approve` de Rafael PARECER alcancavel — quando, pelo seam tipado, ela nao e' (os cinco
    booleanos da r1 v0.2.0 nao sao campos de entrada do agente). Um golden que so' prova
    "o grafo repassa o que a DMN disser" nao prova nada sobre a rota.

    A fixture condicional escreve a tabela como ela e', em vez de escrever a resposta desejada::

        "auth_auto_approval": {
          "__rules__": [
            {"when": {"auto_criteria_verificado": true, "criterio_tecnico_ok": true},
             "then": {"recomendacao": "AUTO_APROVAR", "motivo": "r1"}},
            {"then": {"recomendacao": "ANALISE_HUMANA", "motivo": "r99_catch_all"}}
          ]
        }

    Semantica, deliberadamente minima e igual a das tabelas que ela espelha: FIRST-hit na ordem
    escrita; `when` ausente/vazio e' catch-all; uma regra casa quando TODO par de `when` e' igual
    (`==`) ao valor recebido — chave ausente no input NAO casa. Sem operadores, sem ranges: uma
    linguagem de regra aqui viraria uma segunda implementacao de DMN dentro dos testes, que e'
    exatamente o que ADR-0012 mantem fora do Python.

    NENHUMA regra casou = erro ALTO (`AssertionError`, que os grafos nao capturam — eles so'
    tratam `DmnEvaluationError`/`DmnNoResultError`), nunca um fail-safe silencioso: uma tabela
    real tem catch-all, entao uma fixture sem catch-all e' bug de fixture, nao "DMN indisponivel".
    """

    def __init__(self) -> None:
        super().__init__()
        self._rules: dict[str, list[Mapping[str, Any]]] = {}

    def register_rules(self, decision_key: str, rules: Sequence[Mapping[str, Any]]) -> None:
        """Registra as regras FIRST-hit de `decision_key` (a `DmnVersion` vem de `register`)."""
        self._rules[decision_key] = list(rules)

    async def evaluate(
        self,
        decision_key: str,
        variables: dict[str, Any],
        *,
        tenant: str | None = None,
    ) -> tuple[list[dict[str, Any]], Any]:
        if decision_key not in self._rules:
            return await super().evaluate(decision_key, variables, tenant=tenant)
        self.calls.append((decision_key, dict(variables)))
        _, version = self._responses[decision_key]
        for rule in self._rules[decision_key]:
            when = rule.get("when") or {}
            if all(variables.get(k) == v for k, v in when.items()):
                return [dict(rule["then"])], version
        raise AssertionError(
            f"bug de fixture: nenhuma regra `__rules__` de `{decision_key}` casou com "
            f"{variables!r} e nao ha' catch-all. Uma tabela DMN real sempre tem um; "
            "adicione uma regra sem `when`."
        )


def register_dmn_fixture(dmn: FakeDmnTransport, fixture: Mapping[str, Any] | None) -> None:
    """Register every `decision_key -> row(s)` pair from a golden case's `dmn_fixture`.

    A fixture value may be a single row (dict, the design doc's own example shape) or an
    already-list-wrapped set of rows — both are accepted so an eval author never has to remember
    which shape a particular decision key expects. Public (not `run_case`-only) because a Tier-B
    live variant typically drives a single node directly (e.g. `HelenaGraph.classify`) rather
    than the full compiled graph, but still needs the same DMN fixture wired onto its own
    `FakeDmnTransport` instance.

    A value carrying the `__rules__` key is a CONDITIONAL fixture and requires a
    `RuleAwareFakeDmnTransport` — see that class for the why and the shape. Handing one to a
    plain `FakeDmnTransport` raises loudly instead of silently degrading to a static row, which
    would reintroduce exactly the vacuity the conditional fixture exists to remove.
    """
    for decision_key, value in (fixture or {}).items():
        if isinstance(value, Mapping) and RULES_KEY in value:
            if not isinstance(dmn, RuleAwareFakeDmnTransport):
                raise TypeError(
                    f"`dmn_fixture[{decision_key!r}]` usa `{RULES_KEY}` (fixture condicional) "
                    "mas o transporte e' um FakeDmnTransport simples — use "
                    "RuleAwareFakeDmnTransport (o que `run_case` ja' faz)."
                )
            rules = list(value[RULES_KEY])
            # A `DmnVersion` (usada por `dmn_refs`) sai de `register`; as LINHAS saem das regras.
            # A linha registrada aqui e' o `then` da ULTIMA regra (o catch-all) — nunca lida
            # enquanto houver regras, e o default honesto se alguem remover o bloco `__rules__`.
            dmn.register(decision_key, [dict(rules[-1]["then"])])
            dmn.register_rules(decision_key, rules)
            continue
        rows = value if isinstance(value, list) else [value]
        dmn.register(decision_key, rows)


def _raise_if_exhausted(
    inference: ReplayInferenceProvider, *, swallowed_state: dict[str, Any] | None
) -> None:
    """Raise `ReplayExhaustedError` if `inference` recorded ANY exhaustion this turn, regardless
    of whether that error propagated out of the graph or was caught internally by an agent's own
    fail-safe fallback (`ReplayInferenceProvider.exhausted_calls` is appended to BEFORE the
    original error is raised, so it survives either way). This is the un-swallowable enforcement
    point (EVAL-REPLAY-EXHAUSTION-SWALLOWED) — see `conftest.py`'s module docstring.

    No-op when `inference.exhausted_calls` is empty (the overwhelmingly common case).
    """
    events = inference.exhausted_calls
    if not events:
        return
    first, *rest = events
    raise ReplayExhaustedError(
        calls_made=first.calls_made,
        responses_provided=first.responses_provided,
        prompt=first.prompt,
        detected_post_turn=True,
        additional_events=len(rest),
        swallowed_state=swallowed_state,
    )


def _raise_if_unconsumed(inference: ReplayInferenceProvider, case: Mapping[str, Any]) -> None:
    """Raise `ReplayUnconsumedResponsesError` if `case["recorded_llm"]` had entries left over
    after the turn — the symmetric counterpart of `_raise_if_exhausted` (see
    `ReplayUnconsumedResponsesError`'s docstring / `conftest.py`'s module docstring). No-op when
    every scripted response was consumed (the overwhelmingly common case).
    """
    remaining = inference.remaining_responses
    if not remaining:
        return
    raise ReplayUnconsumedResponsesError(
        case_id=str(case.get("id", "<unknown>")),
        recorded_total=len(case["recorded_llm"]),
        calls_made=len(inference.calls),
        unconsumed=remaining,
    )


class FailingStartCibSevenTransport(FakeCibSevenTransport):
    """CibSeven double whose ENGINE START always raises `CibSevenError` (CC-01/CC-08).

    WHY THE HARNESS NEEDED AN EXTENSION AT ALL. Until CC-01 every eval ran against a
    `FakeCibSevenTransport` whose `start_process_instance` ALWAYS succeeds, so the entire
    engine-unavailable branch of every agent graph — the branch CC-01 proved was fabricating a
    success desfecho and losing the case in silence — was structurally unreachable from the golden
    dataset. A golden that cannot express "the engine refused" cannot regress-test the fix.

    Only `start_process_instance` is overridden: `find_active_instance`/`find_any_instance` keep
    the base fake's honest behaviour, so `start_process_idempotent` still runs its full
    audit-before-effect, gate and idempotency sequence and raises from the SAME place a live
    outage would (`transport.start_process_instance`, `transport.py`'s step 4), re-raised
    unchanged by the chokepoint. Nothing is mocked past the seam that really fails.
    """

    async def start_process_instance(
        self, process_key: str, business_key: str, variables: dict[str, Any]
    ) -> ProcessInstance:
        raise CibSevenError(
            f"engine indisponivel (eval fixture): POST /process-definition/key/{process_key}/start"
        )


def _cibseven_for(case: Mapping[str, Any]) -> FakeCibSevenTransport:
    """Pick the CibSeven double for a case from its OPTIONAL top-level `"cibseven"` block.

    `{"cibseven": {"start_fails": true}}` -> `FailingStartCibSevenTransport`; anything else (and
    the absent key, which is every pre-CC-01 golden) -> the unchanged `FakeCibSevenTransport`. The
    key is optional in `conftest.load_golden`'s schema check (`_REQUIRED_CASE_KEYS`), so no
    existing golden is touched by this branch.
    """
    if bool((case.get("cibseven") or {}).get("start_fails")):
        return FailingStartCibSevenTransport()
    return FakeCibSevenTransport()


def _phi_capable_for(case: Mapping[str, Any]) -> bool:
    """Read a case's OPTIONAL top-level `"inference"` block (CC-08, 2026-09-04): `{"inference":
    {"phi_capable": false}}` makes EVERY `generate(phi=True, ...)` call this turn raise
    `PhiZoneRoutingError` (`conftest.py::ReplayInferenceProvider.generate`), simulating the
    PHI-zone routing fail-closed path (I-6) instead of the DMN/CibSeven-down paths the other two
    optional blocks simulate. Absent (every pre-CC-08 golden) -> `True`, the unchanged default —
    `ReplayInferenceProvider(case["recorded_llm"])` behaves exactly as before.

    WHY THE RUNNER NEEDED THIS: `ReplayInferenceProvider` already accepts `phi_capable=False`
    (built for exactly this purpose per its own docstring — "a case that wants to exercise the
    fail-closed PHI-routing path itself constructs `ReplayInferenceProvider(responses,
    phi_capable=False)`"), but `run_case` hard-wired the default-`True` constructor call, so no
    GOLDEN JSON file could reach that branch — only a hand-rolled test function could. CC-08
    needs a golden (not a bespoke test) to prove an agent graph never fails OPEN when its
    inference seam is PHI-zone-blocked, mirroring the `cibseven`/DMN-omission mechanisms already
    in this module.
    """
    return bool((case.get("inference") or {}).get("phi_capable", True))


async def run_case(
    build_fn: Callable[[dict[str, Any]], Any],
    case: Mapping[str, Any],
    *,
    extra_config: Mapping[str, Any] | None = None,
) -> RunResult:
    """Build `build_fn`'s agent graph with fakes seeded from `case`, invoke it, return the result.

    `build_fn` is an agent module's `build(config)` contract (`maezo.agents.<id>.graph.build`) —
    the SAME contract `AgentLoader`/the harness use in production, so an eval exercises the real
    build path, not a bespoke test-only wiring. `extra_config` supplies whatever additional
    keyword `build_fn` needs beyond the four seams every graph shares (`inference`/`dmn`/
    `cibseven`/`audit_sink`) — e.g. `{"whatsapp": FakeWhatsAppSender()}` for the classifier-family
    agents (helena/fernando/lucas).

    UN-SWALLOWABLE REPLAY-EXHAUSTION CONTRACT (EVAL-REPLAY-EXHAUSTION-SWALLOWED): production agent
    graphs legitimately wrap their LLM calls in `except Exception` fail-safe fallbacks, which would
    otherwise silently catch a too-short golden's `ReplayExhaustedError` and let the turn complete
    "successfully" on fallback text. `run_case` closes that hole from OUTSIDE the graph: after the
    turn (whether `compiled.ainvoke` returned normally or raised), it inspects
    `inference.exhausted_calls` and raises `ReplayExhaustedError` itself if it is non-empty —
    regardless of what the agent returned. Symmetrically, if every scripted response was consumed
    but some are left over (`inference.remaining_responses`), it raises
    `ReplayUnconsumedResponsesError` — `recorded_llm` must match the turn's real call count
    EXACTLY, per README.md's documented schema (never a ceiling the turn merely stays under).
    """
    inference = ReplayInferenceProvider(case["recorded_llm"], phi_capable=_phi_capable_for(case))
    dmn = RuleAwareFakeDmnTransport()
    register_dmn_fixture(dmn, case.get("dmn_fixture"))
    cibseven = _cibseven_for(case)
    audit_sink = FakeStartAuditSink()

    config: dict[str, Any] = {
        "inference": inference,
        "dmn": dmn,
        "cibseven": cibseven,
        "audit_sink": audit_sink,
    }
    if extra_config:
        config.update(extra_config)

    graph = build_fn(config)
    compiled = graph.compile()
    input_state = dict(case["input"]["state"])
    try:
        raw_result = await compiled.ainvoke(input_state)
    except Exception:
        # The turn itself raised (possibly `ReplayExhaustedError` propagating unswallowed,
        # possibly something else entirely). Either way, an exhaustion that WAS also recorded
        # takes priority as the more actionable diagnosis — `raise ... from None` isn't used here
        # so the original exception is preserved as `__context__` for debugging, but the
        # un-swallowable summary is what the test sees and asserts on.
        _raise_if_exhausted(inference, swallowed_state=None)
        raise

    result_state = dict(raw_result)
    # The turn returned NORMALLY. If it only did so because an agent's own fail-safe fallback
    # swallowed an exhaustion, `exhausted_calls` is non-empty and this still fails the case loudly
    # — `result_state` (the fallback-produced state) is attached so a test can prove the swallow
    # actually happened, not just that SOME exception fired.
    _raise_if_exhausted(inference, swallowed_state=result_state)
    _raise_if_unconsumed(inference, case)

    return RunResult(
        state=result_state,
        inference=inference,
        dmn=dmn,
        cibseven=cibseven,
        audit_sink=audit_sink,
    )


# ---------------------------------------------------------------------------
# Pass-criterion assertions
# ---------------------------------------------------------------------------


def assert_expect(state: Mapping[str, Any], expect: Mapping[str, Any]) -> None:
    """RT + SF: assert `state["next_kind"]` (if given) and every `expect["fields"]` entry.

    A single golden `expect` block may carry both — `{"next_kind": "escalate", "fields":
    {"escalation_motivo": "red_flag_clinico"}}` is one RT check plus one SF check, matching the
    T3.2 design's `RT+SF` pass-criterion combination (e.g. EVL-HELENA-01).
    """
    if "next_kind" in expect:
        assert state.get("next_kind") == expect["next_kind"], (
            f"RT mismatch: expected next_kind={expect['next_kind']!r}, "
            f"got {state.get('next_kind')!r} (full state={state!r})"
        )
    for field_name, expected_value in (expect.get("fields") or {}).items():
        assert state.get(field_name) == expected_value, (
            f"SF mismatch: expected {field_name}={expected_value!r}, "
            f"got {state.get(field_name)!r} (full state={state!r})"
        )


def assert_no_leak(blob: Any, canaries: Sequence[str]) -> None:
    """ABS: assert none of `canaries` appear anywhere in `blob`'s serialized form.

    `blob` is typically a `RunResult.state` dict, but any JSON-serializable value works (e.g. a
    dossier agent's recorded CibSeven variables, `RunResult.cibseven.get_process_status(...)`'s
    result) — mirrors `tests/unit/agents/test_rafael_input_hardening.py`'s
    `_SLA_SENTINEL not in blob` pattern, generalized to a list of synthetic-PHI canaries.
    """
    if not canaries:
        return
    serialized = json.dumps(blob, ensure_ascii=False, default=str)
    for canary in canaries:
        assert canary not in serialized, f"ABS violation: leak canary {canary!r} found in emitted output"


def score_live(
    result_fields: Mapping[str, Any], baseline_fields: Mapping[str, Any], match: Sequence[str]
) -> float:
    """TH: fraction of `match` fields where the live run's value equals the golden baseline's.

    `baseline_fields` is normally `json.loads(case["recorded_llm"][0])` (the classify-call
    baseline the Tier-A replay would have returned) — Tier B re-runs the SAME node against a
    real LLM and diffs the result against that baseline, never against a hand-maintained second
    dataset. An empty `match` list scores 1.0 (nothing to check is vacuously satisfied, matching
    `assert_no_leak`'s empty-canaries no-op).
    """
    if not match:
        return 1.0
    hits = sum(1 for f in match if result_fields.get(f) == baseline_fields.get(f))
    return hits / len(match)


def assert_live_score(score: float, threshold: float) -> None:
    """TH: assert a `score_live` result meets the golden's `live.threshold` (design default 0.9)."""
    assert score >= threshold, f"TH violation: live score {score:.3f} below threshold {threshold:.3f}"


# ---------------------------------------------------------------------------
# CL — clarity/legibility (WP-EVALS gap 10.3): an objective, reproducible, deterministic check
# on the wording a graph emits to a beneficiary. No LLM-as-judge, no learned/constant score —
# every field on `ClarityReport` is computed straight from the actual text a run produced, so a
# report can never "pass" without the text actually satisfying every rule.
#
# A golden case opts in with an OPTIONAL top-level `"clarity"` block (`load_golden`'s
# `_REQUIRED_CASE_KEYS` does not require it, so every existing golden that omits it is
# unaffected):
#
#     "clarity": {
#       "field": "response_text",
#       "max_words_per_sentence": 20,
#       "forbidden_jargon": ["red_flag", "DMN", "P1", "sintoma_codigo"],
#       "required_disclaimers": [["profissional", "humano", "atendente"], ["emergencia"]]
#     }
#
# `required_disclaimers` is a list of alternative-phrase GROUPS: each group needs AT LEAST ONE
# of its alternatives present (case-insensitive, word/phrase-boundary match — see
# `_phrase_present` below) — a group with none present is a missing mandatory disclaimer.
#
# Hardening note (REP-EVALS repair, post VER-EVALS review of this branch): the original
# `score_clarity` had three reproducible robustness gaps in this shared, reusable primitive,
# fixed here at the root (VERIFY-WP-EVALS.md §2a/§2b/§2c):
#   1. an EMPTY `response_text` passed vacuously whenever a golden's `required_disclaimers` was
#      `[]` (CLAREZA-02's own shape) — fixed with a `min_words` floor (default 3), surfaced on
#      `ClarityReport.word_count`/`.min_words` and enforced by `assert_clarity`.
#   2. the `.`-based sentence splitter was fooled by PT-BR title abbreviations ("Dra.", "Sr.",
#      "Sra.", "Dr.", "p. ex.", "etc.") — fixed by protecting a documented abbreviation list
#      (plus digit-dot-digit decimals) before splitting, so a genuinely long utterance can no
#      longer hide under the word cap by fragmenting at a title abbreviation.
#   3. the disclaimer check matched ANY substring, so "sobre-humano" satisfied the "humano"
#      alternative — fixed with a word/phrase-boundary match (`_phrase_present`) that treats a
#      hyphen as word-joining, so a disclaimer alternative only counts when it appears as its
#      own standalone word or phrase, never as a fragment of a larger/hyphenated word.
# ---------------------------------------------------------------------------

#: PT-BR title/etc. abbreviations whose trailing "." must NOT be read as a sentence boundary
#: (e.g. "a Dra. Fernanda vai..." is one clause, not two sentences). Matched case-insensitively
#: at a word boundary, immediately before the period. Deliberately errs toward UNDER-splitting —
#: treating the abbreviation's period as non-terminal always, even in the rare case it truly
#: does end a sentence — because under-splitting is the fail-closed direction for this module's
#: purpose: a merged sentence can only make the word-count cap MORE likely to trip, never less.
_ABBREVIATIONS: tuple[str, ...] = ("dr", "dra", "sr", "sra", "srta", "prof", "profa", "etc")
_ABBREVIATION_PERIOD_RE = re.compile(r"\b(?:" + "|".join(_ABBREVIATIONS) + r")\.", re.IGNORECASE)

#: "p. ex." (PT-BR "por exemplo") has TWO internal periods to protect (after "p" and after
#: "ex"); handled as its own two-token phrase rather than via `_ABBREVIATIONS` because "ex" alone
#: is not abbreviation-only — it can legitimately end a real sentence on its own.
_P_EX_RE = re.compile(r"\bp\.\s*ex\.", re.IGNORECASE)

#: A "." between two digits is a decimal separator ("37.5"), never a sentence boundary.
_DECIMAL_PERIOD_RE = re.compile(r"(?<=\d)\.(?=\d)")

#: Placeholder swapped in for a protected "." — a Unicode Private Use Area code point that
#: legitimate beneficiary-facing prose never produces — so the sentence-boundary regex below can
#: never mistake it for a terminator; swapped back to "." before a sentence is returned to the
#: caller.
_PROTECTED_PERIOD = ""

#: Sentence boundary: split right after a `.`/`!`/`?`/`…` followed by whitespace (this
#: intentionally covers line breaks too, since `\s` matches `\n`) — the tail after the last
#: boundary becomes the final sentence regardless of whether it ends in punctuation, so "end of
#: string" needs no separate regex alternative. A text with NO terminal punctuation at all (e.g.
#: a line-break-joined run-on) is correctly treated as ONE sentence spanning every line — the
#: fail-closed direction, since it can only make a too-long sentence MORE likely to be flagged,
#: never less. This is a reproducible PROXY for reading difficulty (long sentences are hard to
#: parse), not a full readability formula (a syllable-counting formula like Flesch would need
#: Portuguese hyphenation rules this module does not attempt to get right). Actual splitting
#: happens in `_split_sentences`, which protects abbreviations/decimals first — never call this
#: regex directly on unprotected text.
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?…])\s+")

#: A "word" for counting purposes: a run of non-digit, non-underscore word characters. Unicode
#: word matching is the `str`-pattern default in Python's `re`, so accented Portuguese letters
#: (á, ç, ã, ...) count correctly without an explicit flag.
_WORD_RE = re.compile(r"[^\W\d_]+")

#: A character that "joins" a word for disclaimer-boundary purposes: a Unicode letter OR a
#: hyphen. Including the hyphen is what makes a hyphenated compound like "sobre-humano" NOT
#: satisfy a bare "humano" alternative — a plain regex `\b` word boundary alone would still treat
#: "humano" inside "sobre-humano" as a standalone word, since "-" is already a non-word character
#: to `re`'s default `\w`.
_DISCLAIMER_BOUNDARY_CHAR = r"(?:[^\W\d_]|-)"


def _protect_non_terminal_periods(text: str) -> str:
    """Swap every "." that is part of a known abbreviation, "p. ex.", or a decimal number for
    `_PROTECTED_PERIOD`, so `_SENTENCE_SPLIT_RE` never treats it as a sentence boundary. Restored
    verbatim by `_split_sentences` after splitting.
    """
    text = _ABBREVIATION_PERIOD_RE.sub(lambda m: m.group(0)[:-1] + _PROTECTED_PERIOD, text)
    text = _P_EX_RE.sub(lambda m: m.group(0).replace(".", _PROTECTED_PERIOD), text)
    text = _DECIMAL_PERIOD_RE.sub(_PROTECTED_PERIOD, text)
    return text


def _split_sentences(text: str) -> list[str]:
    """Split `text` into sentences, immune to PT-BR title abbreviations, "p. ex.", and decimal
    numbers (see `_protect_non_terminal_periods`) — the correct replacement for a bare
    `_SENTENCE_SPLIT_RE.split(text)`, which a title abbreviation could fool into a false
    boundary (VERIFY-WP-EVALS.md §2b)."""
    protected = _protect_non_terminal_periods(text.strip())
    return [
        s.strip().replace(_PROTECTED_PERIOD, ".") for s in _SENTENCE_SPLIT_RE.split(protected) if s.strip()
    ]


def _phrase_present(text: str, phrase: str) -> bool:
    """True if `phrase` appears in `text` as a standalone word/phrase, case-insensitively —
    never as a fragment of a larger word or of a hyphenated compound
    (`_DISCLAIMER_BOUNDARY_CHAR`). So "humano" matches "Um atendente humano vai continuar" but
    NOT "esforco sobre-humano" (VERIFY-WP-EVALS.md §2c).

    Deliberately accent-SENSITIVE, not accent-folded: the goldens that need both spellings
    already list each accented/unaccented form as its own alternative (e.g.
    `["emergencia", "emergência"]`), so folding accents here would only add risk (two distinct
    PT-BR words can differ by accent alone) for no gain on the goldens this repo actually ships.

    `phrase` may be multi-word (e.g. "fale com um atendente") — matched literally as one phrase;
    the boundary check applies only at the phrase's own start/end, never between its internal
    words.
    """
    if not phrase:
        return False
    pattern = re.compile(
        rf"(?<!{_DISCLAIMER_BOUNDARY_CHAR}){re.escape(phrase)}(?!{_DISCLAIMER_BOUNDARY_CHAR})",
        re.IGNORECASE,
    )
    return pattern.search(text) is not None


@dataclass
class ClarityReport:
    """Everything `assert_clarity` needs to explain a failure precisely — no bare True/False."""

    text: str
    sentences: list[str]
    long_sentences: list[tuple[str, int]]
    jargon_hits: list[str]
    missing_disclaimer_groups: list[tuple[str, ...]]
    word_count: int
    min_words: int


def score_clarity(
    text: str,
    *,
    max_words_per_sentence: int,
    forbidden_jargon: Sequence[str] = (),
    required_disclaimers: Sequence[Sequence[str]] = (),
    min_words: int = 3,
) -> ClarityReport:
    """Compute an objective, reproducible clarity report for `text` (e.g. Helena's
    `response_text` — the free text a graph drafts for the beneficiary, never the SME-gated DMN
    table content itself).

    Four independent, deterministic checks, each a pure function of `text`:

    0. Minimum length — `text` must contain at least `min_words` words (default 3, unicode-aware
       count via `_WORD_RE`). An empty or near-empty beneficiary-facing reply is never clear,
       regardless of what the other checks say — this closes the vacuous pass a golden with
       `required_disclaimers=[]` would otherwise allow on an empty string
       (VERIFY-WP-EVALS.md §2a). Surfaced as `ClarityReport.word_count`/`.min_words`; enforced by
       `assert_clarity`.
    1. Sentence length — any sentence (via `_split_sentences`, which protects PT-BR title
       abbreviations, "p. ex.", and decimal numbers from being misread as sentence boundaries —
       VERIFY-WP-EVALS.md §2b) whose WORD count exceeds `max_words_per_sentence` is flagged in
       `long_sentences`.
    2. Forbidden jargon — any `forbidden_jargon` term found in `text` (case-insensitive
       substring) is flagged in `jargon_hits`: internal/engine vocabulary (DMN table names, raw
       `sintoma_codigo` values, `motivo_categoria` tokens, severity codes like `P1`) must never
       leak verbatim into a beneficiary-facing message. (Deliberately still substring-based,
       unlike the disclaimer check below — a leaked engine token is a real leak wherever it
       appears, even mid-word.)
    3. Mandatory disclaimers — each `required_disclaimers` group needs >=1 alternative present
       as a standalone word/phrase (`_phrase_present`, case-insensitive, hyphen-aware boundary —
       VERIFY-WP-EVALS.md §2c); an unsatisfied group is flagged in `missing_disclaimer_groups`.
    """
    sentences = _split_sentences(text)
    long_sentences: list[tuple[str, int]] = []
    for sentence in sentences:
        word_count = len(_WORD_RE.findall(sentence))
        if word_count > max_words_per_sentence:
            long_sentences.append((sentence, word_count))

    lowered = text.lower()
    jargon_hits = [term for term in forbidden_jargon if term.lower() in lowered]

    missing_disclaimer_groups = [
        tuple(group) for group in required_disclaimers if not any(_phrase_present(text, alt) for alt in group)
    ]

    return ClarityReport(
        text=text,
        sentences=sentences,
        long_sentences=long_sentences,
        jargon_hits=jargon_hits,
        missing_disclaimer_groups=missing_disclaimer_groups,
        word_count=len(_WORD_RE.findall(text)),
        min_words=min_words,
    )


def assert_clarity(report: ClarityReport) -> None:
    """CL: raise with EVERY violation `score_clarity` found — never a bare pass/fail, so a
    failure names exactly which sentence/term/disclaimer group is the problem."""
    problems: list[str] = []
    if report.word_count < report.min_words:
        problems.append(
            f"beneficiary-facing text is empty or too short ({report.word_count} word(s), "
            f"need >= {report.min_words})"
        )
    if report.long_sentences:
        detail = "; ".join(f"{count} words: {sentence!r}" for sentence, count in report.long_sentences)
        problems.append(f"sentence(s) exceed the max-words-per-sentence limit ({detail})")
    if report.jargon_hits:
        problems.append(f"forbidden jargon leaked into beneficiary-facing text: {report.jargon_hits!r}")
    if report.missing_disclaimer_groups:
        problems.append(
            f"missing mandatory disclaimer (need >=1 phrase per group): {report.missing_disclaimer_groups!r}"
        )
    assert not problems, f"CLARITY violation(s) in {report.text!r}: " + " | ".join(problems)


# ---------------------------------------------------------------------------
# Mutation-check helper (T3.2 design §7.1 — the non-vacuousness proof).
#
# A verifier (or a family builder proving their own golden isn't a rubber stamp) perturbs a
# golden's OWN pass criterion and asserts the SAME harness call now fails. An eval that still
# passes under its own deliberately-wrong expectation is vacuous: it would never catch a real
# prompt/graph/model regression, defeating the entire point of ADR-0009's promotion gate.
# ---------------------------------------------------------------------------

#: The three route literals every classifier-family agent's `next_kind`-shaped `expect` can take
#: (helena: inform/schedule/escalate; fernando/lucas use a 2-literal `Route` — `mutate_expected_route`
#: picks the first alternate that differs from the case's own value, so it still works for a
#: 2-literal route, it just never proposes the third).
_ROUTE_ALTERNATES: tuple[str, ...] = ("inform", "schedule", "escalate")


def mutate_expected_route(case: Mapping[str, Any]) -> dict[str, Any]:
    """Return a deep copy of `case` with `expect["next_kind"]` flipped to a DIFFERENT value.

    For an RT-class eval: proves `assert_expect` actually reads the graph's real route rather
    than trivially agreeing with whatever the golden says.
    """
    mutated = copy.deepcopy(dict(case))
    expect = dict(mutated.get("expect") or {})
    original = expect.get("next_kind")
    wrong = next((r for r in _ROUTE_ALTERNATES if r != original), None)
    if wrong is None:
        raise ValueError(f"case {case.get('id')!r} has no `expect.next_kind` to mutate")
    expect["next_kind"] = wrong
    mutated["expect"] = expect
    return mutated


def mutate_plant_canary(case: Mapping[str, Any], canary: str) -> dict[str, Any]:
    """Return a deep copy of `case` with `canary` appended to the last `recorded_llm` response.

    For a PL-class eval: simulates the LLM leaking the canary into its own free-text output
    (mirrors the live-proven CPF-leak regression `test_helena.py::
    test_cpf_bearing_field_value_never_reaches_engine_variables` guards against) and adds
    `canary` to `leak_canaries` so `assert_no_leak` is exercised against it. Requires the harness
    to actually route the mutated LLM text somewhere `assert_no_leak`'s serialized blob covers —
    if the mutated run still shows no leak, `run_mutation_check` raises (vacuous ABS eval).
    """
    mutated = copy.deepcopy(dict(case))
    responses = list(mutated.get("recorded_llm") or [])
    if not responses:
        raise ValueError(f"case {case.get('id')!r} has no `recorded_llm` entries to plant a canary into")
    responses[-1] = f"{responses[-1]} {canary}"
    mutated["recorded_llm"] = responses
    mutated["leak_canaries"] = [*list(mutated.get("leak_canaries") or []), canary]
    return mutated


def mutate_extend_last_sentence(case: Mapping[str, Any], extra_words: int) -> dict[str, Any]:
    """Return a deep copy of `case` with `extra_words` filler words appended to the LAST
    `recorded_llm` response's FINAL sentence (same sentence — the trailing `.`/`!`/`?` is
    removed and re-appended after the filler, so no new sentence boundary is introduced).

    For a CL-class (clarity) golden: non-vacuousness proof for `score_clarity`'s long-sentence
    check — pushes that sentence's word count past the case's own
    `clarity.max_words_per_sentence` and confirms `assert_clarity` now fails. Mirrors
    `mutate_plant_canary`'s shape (same last-response target, same deep-copy discipline).
    """
    mutated = copy.deepcopy(dict(case))
    responses = list(mutated.get("recorded_llm") or [])
    if not responses:
        raise ValueError(f"case {case.get('id')!r} has no `recorded_llm` entries to extend")
    last = responses[-1].rstrip()
    trailing_punct = last[-1] if last and last[-1] in ".!?" else ""
    base = last[:-1] if trailing_punct else last
    filler = " ".join(["adicional"] * extra_words)
    responses[-1] = f"{base} {filler}{trailing_punct or '.'}"
    mutated["recorded_llm"] = responses
    return mutated


def mutate_replace_last_response(case: Mapping[str, Any], replacement: str) -> dict[str, Any]:
    """Return a deep copy of `case` with the LAST `recorded_llm` entry replaced VERBATIM by
    `replacement`.

    For a CL-class (clarity) golden: non-vacuousness proof for `score_clarity`'s
    mandatory-disclaimer check — swap in a plausible-looking reply that omits every disclaimer
    group and confirm `assert_clarity` now flags it as missing.
    """
    mutated = copy.deepcopy(dict(case))
    responses = list(mutated.get("recorded_llm") or [])
    if not responses:
        raise ValueError(f"case {case.get('id')!r} has no `recorded_llm` entries to replace")
    responses[-1] = replacement
    mutated["recorded_llm"] = responses
    return mutated


async def run_mutation_check(
    build_fn: Callable[[dict[str, Any]], Any],
    case: Mapping[str, Any],
    *,
    mutation: Callable[[Mapping[str, Any]], dict[str, Any]],
    extra_config: Mapping[str, Any] | None = None,
) -> None:
    """Run a deliberately-corrupted `case` through the harness and assert it FAILS its own check.

    Raises `AssertionError` (build rejected, per the design's ratification checklist) if the
    mutated golden still passes — i.e. if `assert_expect`/`assert_no_leak` against the mutated
    `expect`/`leak_canaries` do NOT raise. Swallows the expected internal `AssertionError` from
    the (correctly failing) mutated check and returns normally — that's the passing case for
    THIS function (the eval is proven non-vacuous).
    """
    mutated = mutation(case)
    result = await run_case(build_fn, mutated, extra_config=extra_config)
    try:
        assert_expect(result.state, mutated.get("expect") or {})
        assert_no_leak(result.state, mutated.get("leak_canaries") or [])
    except AssertionError:
        return  # expected: the corrupted golden failed its own assertion -> eval is non-vacuous
    raise AssertionError(
        f"MUTATION CHECK FAILED for {mutated.get('id', '<unknown>')}: the deliberately-corrupted "
        "golden still PASSED its own assertions — this eval is vacuous (it would never catch a "
        "real prompt/graph/model regression) and the build must be rejected (T3.2 design §7.1)."
    )
