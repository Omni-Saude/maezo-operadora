"""PHI-completeness fence — every process variable the specs declare, classified (GAP-DU-07).

The problem this closes
-----------------------
The platform's PHI control is **name-anchored**: `redact_phi_vars`
(`src/maezo/tools/workers/phi_vars.py`) replaces a value only when its KEY is
one of the eight names in `PHI_PROCESS_VARS`, and `LogScrubber` only when its
key is one of the four in `gateway.pseudonymizer.PHI_FIELDS`. Both sets are
frozen and CI-enforced, and both were inherited from the donor codebase
(`phi_vars.py` docstring: "Kept aligned with the donor's `PHI_PROCESS_VARS`").
Nothing ever proved those twelve names COVER the process variables the 16 BPMN
and 62 DMN under `spec/processes/` actually declare and read. A name-anchored
control with an unproven name list is a control whose coverage is a belief.

Audit gap DU-07 (report D8, amended by gateway `gvr-d08`) recorded exactly that.
Its reproducer swept `main` and found no acute leak — the variables carrying
clinical free text today ARE in `PHI_PROCESS_VARS`. What was missing is the
MECHANISM: a gate that re-derives the process-variable universe from the live
artifacts on every build, so the twelfth name cannot become a thirteenth
silently.

What this module does
---------------------
1. **Extracts** every process-variable NAME any spec artifact declares or reads,
   with `file:line` provenance (see `SURFACES`).
2. **Classifies** each name into three buckets:
   * `LISTED` — the name is in `PHI_PROCESS_VARS` or `PHI_FIELDS`;
   * `SHAPE_SUSPECT` — in neither set, and either the name matches the PHI-SHAPE
     heuristic (`SHAPE_TOKENS`, every token sourced from this repo's own
     vocabulary) or one of its occurrences carries the STRUCTURAL free-text
     signal, which does not read the name at all (see below);
   * `CLEAN` — neither.
3. **Fails closed** on any `SHAPE_SUSPECT` name that has no entry in
   `DISPOSITIONS`. A new PHI-shaped variable added to any BPMN/DMN/manifest
   turns the build red until a human writes its disposition down.

What this module deliberately does NOT do
-----------------------------------------
It never decides whether a name IS PHI. That decision belongs to the DPO
(`docs/review-queue.md`), and every `DISPOSITIONS` entry therefore carries
`status = DRAFT/verify (DPO)` — engineering's *recommendation with evidence*,
never a ratification. `DISPOSITIONS` is **not an allowlist that makes a finding
go away**: an entry does not add the name to any PHI set, does not change one
byte of runtime redaction, and does not close the DPO question. It only records
that the question has been ASKED, in writing, with a recommendation attached.

`DISPOSITIONS` lives inside this module rather than in
`spec/policies/privacy/*.yaml` for one reason: that directory is CODEOWNED by
the DPO/security reviewers (`.github/CODEOWNERS:116` —
`/spec/policies/privacy/ @rodaquino-OMNI @Omni-Saude/security-team
@lucasreisEvah`), and a table of UNRATIFIED engineering recommendations must not
be laundered into a DPO-owned policy path by the same commit that writes it.
(`spec/policies/privacy/phi-business-key-remediation.yaml:48-49` states the same
fact but names `@rodrigotaquino` / `@Omni-Saude/security`, two handles the
CODEOWNERS audit header proved do not exist — `.github/CODEOWNERS:8-20`. The
fact is true by the live line, not by that quote; and CODEOWNERS is advisory
today, `require_code_owner_review` being count=0.) `docs/review-queue.md`
carries the migration item: once the DPO ratifies, the table becomes a
CODEOWNED privacy manifest and this constant is replaced by its loader. That is
an owner act, not an engineering one.

The PHI-SHAPE heuristic (§ `SHAPE_TOKENS`)
------------------------------------------
Every token is sourced from a place this repo ALREADY treats as PHI vocabulary;
`SHAPE_TOKENS` records the citation next to each one, and
`test_every_shape_token_cites_a_live_source` re-reads those citations so a
token whose source moved or vanished fails the build. The tokens marked
`ATTESTED_NOWHERE` are declared extrapolations, not repo facts, labelled as such
rather than dressed up with a plausible-looking citation, and each carries an
inline comment giving the basis of the extrapolation.

Matching is on SEGMENTS of the name (split on `_`, `.`, `-`, and camelCase
boundaries), never on substrings, so `nome` matches `nome_mae` and `nomeMae`
but not `sobrenome_fantasia`; and `cid` matches `cid10_referencia` (the digits
are stripped from a segment before comparison) but not `decidir`. A token is
either `SEGMENT_MATCH` (the segment must equal it) or `STEM_MATCH` (the segment
must START with it) — the stems exist to fold plural and gendered forms, which
are otherwise one-character evasions: `laudo`/`laudos`, `resumo`/`resumos`,
`justificativa`/`justificativas`, `diagnostico`/`diagnostica`. Tokens whose
prefix would collide with unrelated domain words stay `SEGMENT_MATCH` on
purpose: `sus` (else it swallows `suspeita`, which the corpus really uses),
`cid` (else `cidade`/`cidadao`), `rg`, `cpf`, `nome`, `cep`.

Two signals, and only one of them is anchored in the name
---------------------------------------------------------
The first adversarial review of this fence made the point that mattered: a token
list is the same species of control DU-07 indicts, one level up, and its recall
was neither measured nor declared. Both halves are answered here.

**(a) A STRUCTURAL signal that never reads the name.** A `camunda:formField`
whose type is not in `BOUNDED_FORM_FIELD_TYPES` and which declares no
`camunda:value` domain is an unbounded box a human types prose into, whatever it
is called. Every such occurrence is marked `VarRef.free_text`, and a name with
one is SHAPE-SUSPECT independently of `SHAPE_TOKENS`. Measured on the live
corpus: 14 occurrences over 5 distinct names, of which 4 are already LISTED and
exactly ONE — `auditor_id` — is new. That single name is the entire noise cost
of the signal today, and it is dispositioned below rather than special-cased
away. The same mark is applied to a `VARIAVEIS DE ENTRADA` roll entry whose own
parenthesised annotation says the variable is free text
(`FREE_TEXT_MARKERS`); that arm fires ZERO times on today's corpus and
`test_no_roll_entry_declares_itself_free_text_today` pins the zero, so the day a
roll reads `foo (texto livre)` the name is suspect without anyone adding a token.

**(b) MEASURED recall, declared instead of implied.** `TestClassificationRecall`
runs the adversarial battery this fence failed the first time — the 11-name
attack, the 11 extra probes and the plural/one-letter evasions — as named tests:
every PHI-shaped name is caught and every one of the five deliberately clean
names (`numero_lote_tiss`, `prazo_dias`, `codigo_tuss`, `valor_cents`,
`cnpj_prestador`) stays clean. That is a MEASUREMENT of this battery, not a
claim of completeness; the next limit says what is still uncaught by name.

Declared limits (measured, not assumed)
---------------------------------------
* **Shape, not content — and the token list is NOT exhaustive.** A variable
  whose name carries no PHI vocabulary and which is not a free-text form field
  is invisible here, exactly as it is invisible to `redact_phi_vars`. Named,
  measured residue from the adversarial battery: `descricao_procedimento`,
  `sexo` and `gestante` stay CLEAN by name today. `idade` is left out
  DELIBERATELY and with its cost measured: adding it would move
  `idade_anos`, `idade_meses` and `idade_gestacional_semanas` — three DUT
  criteria columns — into the DPO queue, a scope decision this work package was
  not asked to take. This fence proves the NAME universe, which is the universe
  the runtime control keys on; it does not read values.
* **Worker-written process variables are OUT OF SCOPE (declared non-goal).**
  The sweep's input is a `spec/processes` tree, so a variable that no artifact
  declares and a worker invents at runtime is invisible by construction:
  `contas.py:330` returns `total_glosado_candidato_brl` and `:334`
  `impacto_percentual`, neither declared anywhere under `spec/`. They are NOT
  outside the QUESTION DU-07 asks (they travel the same worker->engine edge that
  `redact_phi_vars` keys on, and a name that set does not carry is not redacted
  there either) — they are outside this fence's INPUT. Extending the sweep to
  the `return {...}` keys of `src/maezo/tools/workers/*.py` was considered and
  declined: it would need an AST pass over 31 modules with its own
  false-positive story, and it would change this fence's contract from "the
  artifacts declare X" to "the artifacts and the code declare X" — a different
  gate with a different owner. `docs/review-queue.md` (GAP-DU-07) carries it as
  a named follow-up rather than a silent gap.
* **No structured message payloads exist to read.** The task brief asks for
  "message/signal payload docs if structured". Measured on `main`: all 22
  `bpmn:message` elements carry only `id`/`name` (`msg.auth.docs_received`
  &c.), there are zero `bpmn:signal` elements, and zero `camunda:in`/
  `camunda:out` call-activity variable mappings. There is no structured payload
  surface to extract, and `test_no_structured_message_payload_surface_exists`
  pins that as a measured fact so the day one appears, this limit fails loudly
  instead of staying silently true.
* **A JUEL path segment is not a process variable.** `${admissibilidade.roteamento}`
  reads ONE process variable (`admissibilidade`, a DMN result map) and one field
  inside it. Both are collected: the root under surface `juel_root`, the
  non-root segments under `juel_segment`. The segments are collected because a
  PHI-shaped leaf inside a result map (`${paciente.cpf}`) would otherwise be
  invisible to a name-anchored sweep — fail-closed over precise. Buckets and
  fence rules treat both surfaces identically.
* **The prose roll can only UNDER-collect, and the loss is named.**
  `declared_input_names` stops at the first chunk that is not a bare
  snake_case identifier, so it can never invent a name and it can lose the last
  ones. One real loss, measured: the CONTAS roll stops one word before
  `total_glosado_candidato_centavos`
  (`SP-OP-CONTAS-001_Processamento_Contas_Glosa.bpmn:48`), a variable
  `contas.py:263` computes and `contas.py:331` returns. It is CLEAN either way,
  so it changes no bucket today — but the under-collection is real and
  `test_the_declared_input_roll_is_pinned_per_file` pins the per-file yield so a
  prose edit that shortens a roll further is visible.
* **Two extraction paths are fail-OPEN, and both are pinned at measured zero.**
  (a) a `dmn:inputExpression/text` that is not a bare identifier is dropped
  (`_IDENTIFIER`), so a FEEL expression like `<text>paciente.cpf</text>` outside
  a `${...}` would leave the universe; (b) `<dmn:variable name="...">` is not a
  surface. Both are ZERO on the live corpus, and — following the same discipline
  as `test_no_structured_message_payload_surface_exists` —
  `test_no_non_identifier_input_expression_exists_today` and
  `test_no_dmn_variable_element_exists_today` pin the zeros, so the day the
  first one appears the limit fails loudly instead of staying silently true.
* **Not wired into `cli.py` yet.** Like PR-1's `perspective.py`, this module is
  built and measured before it is enforced;
  `test_fence_is_not_wired_into_the_cli_yet` pins that, and the flip is a
  deliberate later change to a file another PR is currently editing.
"""

from __future__ import annotations

import bisect
import re
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import final

from maezo.gateway.pseudonymizer import PHI_FIELDS
from maezo.tools.workers.phi_vars import PHI_PROCESS_VARS

from ._loaders import ParseError, load_xml, load_yaml
from .result import Report

# ---------------------------------------------------------------------------
# Buckets
# ---------------------------------------------------------------------------

LISTED = "LISTED"
SHAPE_SUSPECT = "SHAPE-SUSPECT-UNLISTED"
CLEAN = "CLEAN"

#: The union the runtime name-anchored controls key on. `PHI_PROCESS_VARS` is
#: the worker->engine/Kafka edge (`redact_phi_vars`); `PHI_FIELDS` is the
#: gateway/log edge (`LogScrubber`, `Pseudonymizer.pseudonymize`). A name in
#: either one is already covered somewhere, so it is not a DPO question.
PHI_LISTED_NAMES: frozenset[str] = PHI_PROCESS_VARS | PHI_FIELDS


# ---------------------------------------------------------------------------
# Extraction surfaces
# ---------------------------------------------------------------------------

#: Every surface this fence reads, with what it means. A `VarRef.surface` is
#: always one of these keys; `test_every_surface_is_exercised` asserts each one
#: fires at least once against the live `spec/`, so a silently-dead extraction
#: path cannot masquerade as coverage.
SURFACES: Mapping[str, str] = {
    "bpmn_form_field": "camunda:formField id — a User Task field written into process scope",
    "bpmn_input_parameter": "camunda:inputParameter name — a local variable handed to a delegate",
    "bpmn_output_parameter": "camunda:outputParameter name — a variable written back to the process",
    "bpmn_result_variable": "camunda:resultVariable — the DMN result map a businessRuleTask writes",
    "bpmn_declared_input": "the 'VARIAVEIS DE ENTRADA:' roll in a bpmn:documentation (see below)",
    "juel_root": "root identifier of a ${...} expression — the process variable actually read",
    "juel_segment": "non-root segment of a ${a.b} path — a field inside a result map (see limits)",
    "dmn_input_expression": "dmn:inputExpression/text — the variable a decision column reads",
    "dmn_output_name": "dmn:output name — the variable a decision writes",
    "yaml_manifest_variable": "entradas:/saidas: key in a spec/processes/dmn/*.yaml manifest",
}


@dataclass(frozen=True, slots=True)
class VarRef:
    """One occurrence of a process-variable name, pinned to where it was found.

    `free_text` is the STRUCTURAL signal: True when the ARTIFACT ITSELF says this
    occurrence is an unbounded box a human types prose into — a
    `camunda:formField` with no closed `camunda:value` domain, or a
    `VARIAVEIS DE ENTRADA` roll entry annotated `(texto livre)`. It is set
    without reading the name, and it is the only classification input in this
    module that a rename cannot evade.
    """

    name: str
    path: Path
    line: int
    surface: str
    free_text: bool = False

    def render(self) -> str:
        free = " free-text" if self.free_text else ""
        return f"{self.path}:{self.line} ({self.surface}{free})"


# ---------------------------------------------------------------------------
# The PHI-SHAPE heuristic
# ---------------------------------------------------------------------------

#: Marker for a token that this repo's vocabulary does NOT attest. Kept explicit
#: rather than paired with a plausible-looking citation: an unsourced token is a
#: judgement call, and a reader deserves to see which ones are.
ATTESTED_NOWHERE = "ATTESTED_NOWHERE — declared extrapolation, no repo attestation"


#: How a token is compared against one segment of a name.
SEGMENT_MATCH = "segment"  # the segment must EQUAL the token (after digit folding)
STEM_MATCH = "stem"  # the segment must START WITH the token (folds plural/gendered forms)


@dataclass(frozen=True, slots=True)
class ShapeToken:
    """One PHI-shape token and the repo location that attests it as PHI vocabulary."""

    token: str
    source: str
    quote: str
    match: str = SEGMENT_MATCH


#: The heuristic's vocabulary. Each entry's `source` is a `file:line` in THIS
#: repo that already treats the token as PHI, and `quote` is a substring that
#: must still be present at that location — `test_every_shape_token_cites_a_live_source`
#: re-reads every one, so a citation that rots fails the build instead of
#: decaying into folklore.
SHAPE_TOKENS: tuple[ShapeToken, ...] = (
    # --- gateway.pseudonymizer.PHI_FIELDS (the canonical four) ---
    ShapeToken("cpf", "src/maezo/gateway/pseudonymizer.py:43", '"cpf"'),
    ShapeToken("nome", "src/maezo/gateway/pseudonymizer.py:44", '"nome"'),
    ShapeToken("telefon", "src/maezo/gateway/pseudonymizer.py:45", '"telefone"', STEM_MATCH),
    ShapeToken("email", "src/maezo/gateway/pseudonymizer.py:46", '"email"'),
    # `e_mail` splits into segments ("e", "mail"), so the attested `email` never fires on it.
    # Same attestation, folded to the segment the evasion actually produces.
    ShapeToken("mail", "src/maezo/gateway/pseudonymizer.py:46", '"email"'),
    # --- tools.workers.phi_vars.PHI_PROCESS_VARS (the clinical-free-text eight) ---
    # Stems, not whole segments: `justificativas`/`laudos`/`resumos`/`diagnostica` are
    # one-character evasions of the very names the runtime control is keyed on.
    ShapeToken(
        "justificativ", "src/maezo/tools/workers/phi_vars.py:54", '"justificativa_clinica"', STEM_MATCH
    ),
    # `cid` stays SEGMENT_MATCH: as a stem it would swallow `cidade`/`cidadao`, and the
    # digit fold already carries `cid10` -> `cid`.
    ShapeToken("cid", "src/maezo/tools/workers/phi_vars.py:55", '"cid10_referencia"'),
    ShapeToken("fundamentac", "src/maezo/tools/workers/phi_vars.py:56", '"fundamentacao_dut"', STEM_MATCH),
    ShapeToken("nota", "src/maezo/tools/workers/phi_vars.py:57", '"notas_resolucao"', STEM_MATCH),
    ShapeToken("resumo", "src/maezo/tools/workers/phi_vars.py:58", '"resumo_contexto"', STEM_MATCH),
    ShapeToken("matricula", "src/maezo/tools/workers/phi_vars.py:59", '"matricula_beneficiario"', STEM_MATCH),
    ShapeToken("laudo", "src/maezo/tools/workers/phi_vars.py:60", '"laudo"', STEM_MATCH),
    ShapeToken("diagnostic", "src/maezo/tools/workers/phi_vars.py:61", '"diagnostico"', STEM_MATCH),
    # --- beatriz's raw-PHI refusal set (an item carrying any of these is refused whole) ---
    ShapeToken("cns", "src/maezo/agents/beatriz/graph.py:143", '"cns"'),
    ShapeToken("rg", "src/maezo/agents/beatriz/graph.py:143", '"rg"'),
    ShapeToken("endereco", "src/maezo/agents/beatriz/graph.py:143", '"endereco"', STEM_MATCH),
    # --- fraude's evidence-reference PHI markers ---
    ShapeToken("social", "src/maezo/tools/workers/fraude.py:65", '"nome_social"'),
    # --- the WhatsApp payload force-tokenization vocabulary (review-queue, res-xphi-raw-dict-payloads) ---
    # `nasc` rather than `nascimento`: the abbreviation `dt_nasc` is the form an intake
    # field actually takes, and the stem covers the spelled-out name too.
    ShapeToken("nasc", "docs/review-queue.md:436", "data_nascimento", STEM_MATCH),
    ShapeToken("cep", "docs/review-queue.md:436", "cep"),
    # --- clinical-record vocabulary named as PHI-in-a-bounded-token by the harness fence ---
    ShapeToken("prontuario", "src/maezo/tools/workers/harness.py:282", "prontuario number", STEM_MATCH),
    # --- LGPD DSR free-text request detail, named PHI in the worker that refuses to forward it ---
    ShapeToken(
        "detalhe", "src/maezo/tools/workers/lgpd.py:483", "detalhes_requisicao (free-text PHI)", STEM_MATCH
    ),
    # --- fraud-dossier free narrative written by an LLM over case facts ---
    ShapeToken("narrativ", "src/maezo/agents/beatriz/prompts.py:61", "narrativa", STEM_MATCH),
    # --- "texto livre" named PHI verbatim by the process that carries it ---
    ShapeToken(
        "livre",
        "spec/processes/bpmn/SP-OP-ESCALATION-001_Escalonamento_Humano_Universal.bpmn:145",
        "texto livre PHI",
        STEM_MATCH,
    ),
    # --- "dado de paciente" as the category the dossier-zone ratification is ABOUT ---
    ShapeToken(
        "paciente", "src/maezo/platform/privacy/dossier_zone.py:43", "dado de paciente real", STEM_MATCH
    ),
    # --- declared extrapolations (no repo attestation; named so a reader can see the seam) ---
    # Each carries the basis of the extrapolation inline; none is dressed up with a
    # plausible-looking citation. They exist because the adversarial battery in
    # `TestClassificationRecall` walked straight through the attested vocabulary.
    ShapeToken("mae", ATTESTED_NOWHERE, ""),  # filiation, the classic identifying attribute
    ShapeToken("anamnese", ATTESTED_NOWHERE, "", STEM_MATCH),  # the clinical narrative by its own name
    # Second spellings of the attested `cns` (Cartao Nacional de Saude). The repo names
    # `cns`; it never names the card the beneficiary actually carries, which intake forms
    # spell `cartao_sus` / `numero_carteirinha`. `sus` is SEGMENT_MATCH on purpose: as a
    # stem it would swallow `suspeita`, a word the live corpus really uses.
    ShapeToken("sus", ATTESTED_NOWHERE, ""),
    ShapeToken("cartao", ATTESTED_NOWHERE, "", STEM_MATCH),
    ShapeToken("carteirinha", ATTESTED_NOWHERE, "", STEM_MATCH),
    # Components of the attested `endereco`. A control keyed on `endereco` alone does not
    # see the address taken apart into its fields, which is how a form collects it.
    # `municipio` carries a counter-fact the DPO must weigh if it ever fires:
    # `SP-OP-ADEQUACAO-001_Adequacao_Rede.bpmn:80` declares region/municipality (IBGE)
    # granularity as the APPROVED non-PHI form, opposed to "endereco cru de beneficiario".
    ShapeToken("logradouro", ATTESTED_NOWHERE, "", STEM_MATCH),
    ShapeToken("bairro", ATTESTED_NOWHERE, "", STEM_MATCH),
    ShapeToken("municipio", ATTESTED_NOWHERE, "", STEM_MATCH),
    # Clinical-record vocabulary the repo's PHI sets never spell out. `phi_vars.py:24`
    # names "auditor justificativa, CID-10, DUT/ROL grounding, laudo, diagnostico" as the
    # content the set exists to cover; these are the same category of content under the
    # words an intake form or a clinical note uses.
    ShapeToken("sintoma", ATTESTED_NOWHERE, "", STEM_MATCH),
    ShapeToken("hipotese", ATTESTED_NOWHERE, "", STEM_MATCH),
    ShapeToken("historic", ATTESTED_NOWHERE, "", STEM_MATCH),
    ShapeToken("alergia", ATTESTED_NOWHERE, "", STEM_MATCH),
    ShapeToken("exame", ATTESTED_NOWHERE, "", STEM_MATCH),
    ShapeToken("biopsia", ATTESTED_NOWHERE, "", STEM_MATCH),
    ShapeToken("medicament", ATTESTED_NOWHERE, "", STEM_MATCH),
    # `observacao_interna` was this module's own example of a name it could not see.
    ShapeToken("observac", ATTESTED_NOWHERE, "", STEM_MATCH),
    # `relato` is SEGMENT_MATCH: as a stem it would swallow `relatorio`, an ordinary
    # operational word.
    ShapeToken("relato", ATTESTED_NOWHERE, ""),
    # LGPD art. 5, II names racial/ethnic origin a sensitive datum in the same breath as health.
    ShapeToken("raca", ATTESTED_NOWHERE, "", STEM_MATCH),
    ShapeToken("etnia", ATTESTED_NOWHERE, "", STEM_MATCH),
)

#: The token strings, for fast membership tests.
SHAPE_TOKEN_NAMES: frozenset[str] = frozenset(t.token for t in SHAPE_TOKENS)

#: Split by comparison rule, precomputed once.
SEGMENT_TOKENS: frozenset[str] = frozenset(t.token for t in SHAPE_TOKENS if t.match == SEGMENT_MATCH)
STEM_TOKENS: tuple[str, ...] = tuple(sorted(t.token for t in SHAPE_TOKENS if t.match == STEM_MATCH))

_SEGMENT_SPLIT = re.compile(r"[_.\-]+")
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_TRAILING_DIGITS = re.compile(r"\d+$")


def segments(name: str) -> tuple[str, ...]:
    """Lowercased segments of `name`, split on `_ . -` and camelCase boundaries.

    Trailing digits are stripped from each segment so `cid10` folds to `cid`
    (the repo spells the same concept both ways: `cid10_referencia` in
    `PHI_PROCESS_VARS`, `cid10` in the CONTAS/RECURSO redesign's OQ-11).
    """
    out: list[str] = []
    for part in _SEGMENT_SPLIT.split(name):
        if not part:
            continue
        for piece in _CAMEL_BOUNDARY.split(part):
            folded = _TRAILING_DIGITS.sub("", piece.lower())
            if folded:
                out.append(folded)
    return tuple(out)


def matches_phi_shape(name: str) -> bool:
    """True iff any segment of `name` matches a PHI-shape token (see `SHAPE_TOKENS`).

    A `SEGMENT_MATCH` token must EQUAL the (digit-folded) segment; a `STEM_MATCH`
    token need only be its prefix, which is what folds `laudos` onto `laudo` and
    `diagnostica_confirmado` onto `diagnostico`.
    """
    for segment in segments(name):
        if segment in SEGMENT_TOKENS:
            return True
        if segment.startswith(STEM_TOKENS):
            return True
    return False


def classify(name: str, *, free_text: bool = False) -> str:
    """`LISTED`, `SHAPE_SUSPECT` or `CLEAN` for one process-variable name.

    `free_text` is the STRUCTURAL signal (`VarRef.free_text`): pass True when the
    artifact itself declares one of this name's occurrences an unbounded free-text
    field. It makes the name SHAPE-SUSPECT without the name being read at all,
    which is the only arm of this classification a rename cannot evade.
    """
    if name in PHI_LISTED_NAMES:
        return LISTED
    if free_text or matches_phi_shape(name):
        return SHAPE_SUSPECT
    return CLEAN


# ---------------------------------------------------------------------------
# The disposition table — questions asked, never answers given
# ---------------------------------------------------------------------------

#: The LITERAL prefix every legal status must carry, checked INDEPENDENTLY of
#: `DRAFT_VERIFY` below. The first adversarial review of this module found the
#: real hole: both guard tests compared a status against the `DRAFT_VERIFY`
#: SYMBOL, so editing that one constant to `"RATIFICADO pelo DPO"` made all six
#: rows render as ratified with the whole suite green — the exact laundering
#: this module claims to make impossible. Two independent anchors close it:
#: `DRAFT_VERIFY` is DERIVED from this prefix (so an edit to it fails
#: `__post_init__` at import time, reddening everything), the prefix check does
#: not consult `DRAFT_VERIFY` at all, and
#: `test_the_draft_status_literals_are_pinned` pins BOTH strings by literal.
DRAFT_STATUS_PREFIX = "DRAFT/verify"

#: The ONLY status a `Disposition` may carry. There is no `RATIFICADO` value: a
#: ratification is a DPO act recorded in a CODEOWNED privacy manifest, and this
#: module has no vocabulary for one on purpose.
DRAFT_VERIFY = f"{DRAFT_STATUS_PREFIX} (DPO)"


@final
@dataclass(frozen=True, slots=True)
class Disposition:
    """An OPEN DPO question about one PHI-shaped, unlisted process-variable name.

    `recommendation` is engineering's reading of the evidence — a proposal for
    the DPO to accept or reject, never a decision.

    How hard `status` is held, stated exactly rather than generously:

    * the constructor refuses any status that is not `DRAFT_VERIFY` AND does not
      start with the hard-coded `DRAFT_STATUS_PREFIX` — the second check is what
      survives an edit to the first constant;
    * the class is `@final` and refuses subclassing, so a subclass cannot
      override `__post_init__` to construct freely;
    * ordinary attribute assignment raises `FrozenInstanceError`.

    What is NOT claimed: `object.__setattr__` can still write the slot of a
    frozen dataclass — CPython leaves that door open by design and no class-level
    guard closes it. That is why `assert_draft_status` re-validates at the point
    of USE (`render_buckets`, `_check_shape_suspects`) instead of trusting
    construction, and why the claim here is "a mutated row cannot RENDER as
    ratified" rather than "a row cannot be mutated".
    """

    name: str
    evidence: str
    recommendation: str
    status: str = DRAFT_VERIFY

    def __init_subclass__(cls, **kwargs: object) -> None:  # pragma: no cover - refuses at import
        raise TypeError(
            "Disposition may not be subclassed: a subclass overriding __post_init__ would "
            "construct a row with any status it liked, which is the laundering this table exists "
            "to prevent"
        )

    def __post_init__(self) -> None:
        if self.status != DRAFT_VERIFY or not self.status.startswith(DRAFT_STATUS_PREFIX):
            raise ValueError(
                f"Disposition({self.name!r}): status must be {DRAFT_VERIFY!r} and must start with "
                f"{DRAFT_STATUS_PREFIX!r} — a ratified classification is a DPO act recorded in a "
                "CODEOWNED privacy manifest, never a constant in this module"
            )


def assert_draft_status(item: Disposition) -> None:
    """Re-check one row's status at the point of USE, not of construction.

    `object.__setattr__(row, "status", "RATIFICADO")` succeeds on a frozen
    dataclass, so construction-time validation alone cannot carry the invariant
    this module advertises. Every place that RENDERS or ACTS on a status calls
    this first, and the literal prefix is checked independently of
    `DRAFT_VERIFY` so a one-line edit to that constant cannot make a ratified
    string legal.
    """
    if item.status != DRAFT_VERIFY or not item.status.startswith(DRAFT_STATUS_PREFIX):
        raise ValueError(
            f"Disposition({item.name!r}) carries status {item.status!r}, which is not "
            f"{DRAFT_VERIFY!r} and/or does not start with {DRAFT_STATUS_PREFIX!r}. A ratified "
            "classification is a DPO act recorded in a CODEOWNED privacy manifest; this table "
            "records questions, never answers."
        )


#: `MappingProxyType`, not a bare `dict`: the annotation said `Mapping` while the
#: object was mutable at runtime, so `DISPOSITIONS[x] = ...` silently worked.
DISPOSITIONS: Mapping[str, Disposition] = MappingProxyType(
    {
        d.name: d
        for d in (
            Disposition(
                name="detalhes_requisicao",
                evidence=(
                    "spec/processes/bpmn/SP-OP-LGPD-DSR-001_Direitos_do_Titular.bpmn:55 "
                    "(VARIAVEIS DE ENTRADA roll) — free-text detail of a data-subject request; "
                    "src/maezo/tools/workers/lgpd.py:483 calls it '(free-text PHI)' in a comment "
                    "and hand-omits it from the request-proof notification payload."
                ),
                recommendation=(
                    "RECOMMEND adding it to PHI_PROCESS_VARS. The strongest case in this table: "
                    "the repo's OWN code already names it free-text PHI (lgpd.py:483) and excludes "
                    "it from ONE egress by hand, which means today's protection is a manual "
                    "omission at a single call site instead of the name-anchored control. Any "
                    "other worker that copies process variables into an output dict emits it raw. "
                    "No counter-evidence found: no DMN reads it, so listing it costs no decision."
                ),
            ),
            Disposition(
                name="fundamentacao_legal",
                evidence=(
                    "spec/processes/bpmn/SP-OP-LGPD-DSR-001_Direitos_do_Titular.bpmn:78 "
                    '(camunda:outputParameter, initialised to "") and :378 (the '
                    "GW_GuardFundamentacao conditionExpression that gates a reasoned denial). "
                    "Those two BPMN surfaces are its WHOLE provenance: no DMN under "
                    "spec/processes/dmn/ declares an output by this name, so nothing bounds its "
                    "value but the prose at :195."
                ),
                recommendation=(
                    "RECOMMEND treating it as the same class as its listed sibling "
                    "`fundamentacao_dut` (phi_vars.py:56): both are free text a human writes to "
                    "justify an adverse outcome about one identified subject, and free text is "
                    "exactly the shape `redact_phi_vars` exists to stop leaving a worker. It is "
                    "unbounded by construction — no enum, no camunda:value domain, no decision "
                    "table writes it — and UT_RevisaoDpo's own prose expects CLINICAL grounding "
                    "inside it ('NEGAR_FUNDAMENTADO exige fundamentacao_legal (ex.: retencao "
                    "obrigatoria de prontuario)', :195). Counter-evidence the DPO must weigh, at "
                    "its real strength: that prose line is the ONLY thing suggesting what a "
                    "reviewer types, and nothing MECHANICALLY holds the field to it. "
                    "WITHDRAWN counter-evidence, recorded because an earlier draft of this row "
                    "put it in front of the DPO: it said the values in "
                    "spec/processes/dmn/lgpd_dsr_routing.dmn are statutory citations rather than "
                    "narrative. That is FALSE — that table's outputs are fluxo/grupo_revisor/"
                    'sla_resposta/sla_alerta (:40-43) carrying "EXPORTACAO"/"dpo"/"P15D"/"P7D" '
                    "(:48-51); its statutory citations are prose in its <description> (:11-19) "
                    "about routing and SLA, and it emits no fundamentacao_legal at all."
                ),
            ),
            Disposition(
                name="cid10",
                evidence=(
                    "spec/processes/bpmn/SP-OP-AUTH-001_Autorizacao_Previa.bpmn:59, "
                    "SP-OP-RECURSO-001_Recurso_Glosa.bpmn:69 and "
                    "SP-OP-REEMBOLSO-001_Reembolso_Beneficiario.bpmn:61 — all three "
                    "VARIAVEIS DE ENTRADA rolls, i.e. a variable the CALLER sets at start."
                ),
                recommendation=(
                    "RECOMMEND listing. `cid10_referencia` IS in PHI_PROCESS_VARS "
                    "(phi_vars.py:55) and the bare `cid10` is the SAME datum under a second "
                    "spelling — the exact failure mode DU-07 names, since a name-anchored control "
                    "does not fold spellings. phi_vars.py:24 names CID-10 among the clinical "
                    "content the set exists to cover, and that is the whole repo attestation this "
                    "row rests on. Cost of listing, measured: ZERO decisions read it — no DMN "
                    "inputExpression in spec/processes/dmn/ evaluates `cid10` (verified by sweep), "
                    "so redacting it at worker egress removes no routing fact. Counter-evidence: "
                    "a CID-10 code is a bounded code, not free text; if the DPO reads bounded "
                    "codes as out of scope, then `cid10_referencia`'s own listing is the "
                    "inconsistency to revisit — the two must not be classified differently. "
                    "WITHDRAWN support, recorded because an earlier draft put it in front of the "
                    "DPO: it said 'ADR-0006 puts diagnosis in the PHI zone'. It does not — "
                    "docs/adr/0006-phi-two-zones.md partitions by AGENT ('Zona PHI/Financeira "
                    "(Rafael, Marina, Beatriz...)', :12) and never names diagnosis, laudo or CID. "
                    "That was an inference presented as a ratified statement."
                ),
            ),
            Disposition(
                name="diagnostico_oncologico_confirmado",
                evidence=(
                    "spec/processes/dmn/dut_criteria_oncologia_pet_ct.dmn:56 "
                    '(dmn:inputExpression, typeRef="boolean" at :55).'
                ),
                recommendation=(
                    "RECOMMEND listing. The value is a boolean, not free text, so it is not what "
                    "`redact_phi_vars` was written for — but 'this beneficiary has a confirmed "
                    "oncology diagnosis' is health data about an identified subject (LGPD art. 5 "
                    "II, sensitive), and the boolean is carried alongside the pseudo-id that "
                    "identifies whose diagnosis it is. It is also the SAME argument the `cid10` "
                    "row makes about spellings, one word over: bare `diagnostico` IS already in "
                    "PHI_PROCESS_VARS (phi_vars.py:61), and a name-anchored control does not fold "
                    "`diagnostico` onto `diagnostico_oncologico_confirmado`. Cost of listing, "
                    "measured: none for the decision — DMN evaluation is engine-side (ADR-0028) "
                    "and `redact_phi_vars` runs only at worker EGRESS toward the general zone, so "
                    "listing redacts the Kafka/notification copy and leaves the engine's "
                    "evaluation untouched. Counter-evidence: a redacted boolean in an audit "
                    "payload loses a fact an operator may need to explain a denial."
                ),
            ),
            Disposition(
                name="diagnostico_tea_ou_neurodesenvolvimento",
                evidence=(
                    "spec/processes/dmn/dut_criteria_terapias_especiais.dmn:62 "
                    '(dmn:inputExpression, typeRef="boolean" at :61).'
                ),
                recommendation=(
                    "SAME reading as `diagnostico_oncologico_confirmado` above — a boolean "
                    "assertion of a diagnosis, here a neurodevelopmental one (F84.0 per the "
                    "table's own prose at :26). If anything the case is stronger: a "
                    "neurodevelopmental diagnosis of a minor is the archetypal sensitive datum. "
                    "Same measured cost (engine-side evaluation untouched) and same "
                    "counter-evidence (audit-payload legibility)."
                ),
            ),
            Disposition(
                name="has_cid10_codes",
                evidence=(
                    "spec/processes/dmn/phantom_no_diagnosis.dmn:30 "
                    '(dmn:inputExpression, typeRef="boolean", inline on the same line).'
                ),
                recommendation=(
                    "RECOMMEND recording it as NOT PHI — the one entry here where engineering's "
                    "reading is that the heuristic fired on the token `cid` alone. The value is a "
                    "presence flag over a billing submission ('this conta documents CID-10 codes "
                    "at all'), scored as one phantom-billing INDICATOR for SP-OP-FRAUDE-001 "
                    "(:20, 'NUNCA um veredito'); it carries no code and no diagnosis. The DPO "
                    "still has to say so: this fence records the question, not the answer."
                ),
            ),
            Disposition(
                name="exames_convencionais_inconclusivos",
                evidence=(
                    "spec/processes/dmn/dut_criteria_oncologia_pet_ct.dmn:74 "
                    '(dmn:inputExpression, typeRef="boolean" at :73). The table\'s own prose '
                    "describes the datum as 'TC/RM/cintilografia convencionais realizados e "
                    "inconclusivos para a finalidade solicitada' (:34-36)."
                ),
                recommendation=(
                    "RECOMMEND listing, on the SAME reading as the two `diagnostico_*` booleans "
                    "above and for the same measured cost. 'Conventional imaging was performed on "
                    "this beneficiary and came back inconclusive' asserts both a care event and "
                    "its result about an identified subject — health data under LGPD art. 5 II — "
                    "and it travels beside the pseudo-id that says whose. Cost of listing, "
                    "measured: none for the decision (engine-side DMN evaluation, ADR-0028; "
                    "`redact_phi_vars` acts only at worker egress). Counter-evidence the DPO must "
                    "weigh: it is a boolean DUT criterion, not free text, and the DUT criteria in "
                    "this table are the basis a denial has to be explained from — redacting it "
                    "from the audit payload costs an operator that explanation. NEW in this "
                    "revision: the name was CLEAN until the shape vocabulary grew a stem for "
                    "`exame`; it is a question that was always there and was not being asked."
                ),
            ),
            Disposition(
                name="sintoma_codigo",
                evidence=(
                    "spec/processes/dmn/triage_redflag_adult.dmn:26, "
                    "triage_redflag_gestante.dmn:19, triage_redflag_pediatric.dmn:19 and "
                    "triage_redflag_mental_health.dmn:22 — four dmn:inputExpression, "
                    'typeRef="string". The family schema is declared once at '
                    "triage_redflag_adult.dmn:16-20 as 'inputs {sintoma_codigo: string, "
                    "intensidade: string [leve|moderada|grave|desconhecida]}'."
                ),
                recommendation=(
                    "RECOMMEND recording the question and the tension, not a listing — this is "
                    "the row where engineering's reading is least settled. FOR treating it as "
                    "health data: a normalized symptom of an identified beneficiary is health "
                    "data under LGPD art. 5 II regardless of how bounded the code is, and the "
                    "four tables that read it decide whether to escalate a clinical red flag. "
                    "AGAINST: it is a CLOSED normalized code, never narrative — the schema line "
                    "above bounds `intensidade` explicitly and the adult table's fail-safe treats "
                    "an unmapped `sintoma_codigo` as a routing miss, not as text; and the repo "
                    "deliberately puts the agent that produces it (Helena, "
                    "src/maezo/agents/helena/graph.py:12-13, ADR-0012) in the ZONA GERAL, where "
                    "every field is pseudonymized end to end (graph.py:157). Listing it would "
                    "therefore contradict a live architectural choice, not merely add a name — "
                    "which is exactly why it is a DPO question and not an engineering edit. Cost "
                    "if listed, measured: engine-side DMN evaluation is untouched (ADR-0028); the "
                    "redaction would land on the worker-egress copy only."
                ),
            ),
            Disposition(
                name="auditor_id",
                evidence=(
                    "spec/processes/bpmn/SP-OP-AUTH-001_Autorizacao_Previa.bpmn:324, :409 and "
                    ':547 — camunda:formField type="string" with no camunda:value domain, i.e. '
                    "an unbounded box a human types into. Flagged by the STRUCTURAL free-text "
                    "signal, NOT by any name token: no segment of `auditor_id` matches an entry "
                    "in SHAPE_TOKENS."
                ),
                recommendation=(
                    "RECOMMEND recording it as NOT PHI, and recording WHY it is in this table at "
                    "all. The artifact's own label is 'Id do medico auditor responsavel "
                    "(obrigatorio se NEGAR)' (:324), and the BPMN requires it precisely so an "
                    "adverse decision carries the identity of the human who took it (audit trail, "
                    "ADR-0007, :272-273). It identifies OPERADORA STAFF, not a beneficiary, and "
                    "carries no clinical content. It is also the ENTIRE measured noise cost of "
                    "the structural free-text signal on today's corpus — 14 unbounded-field "
                    "occurrences over 5 names, the other 4 already LISTED — and it is "
                    "dispositioned rather than carved out of the heuristic, because a rule that "
                    "excludes its own false positives stops being auditable. Counter-evidence the "
                    "DPO must weigh: an operator identifier is still personal data under LGPD "
                    "art. 5 I even when it is not health data, and the field being unbounded is "
                    "the point — nothing structurally stops a reviewer typing a name into it."
                ),
            ),
        )
    }
)


# ---------------------------------------------------------------------------
# XML surface — a line-preserving scanner
# ---------------------------------------------------------------------------

_TAG_NAME = re.compile(r"[A-Za-z_][\w.\-]*(?::[A-Za-z_][\w.\-]*)?")
_ATTRIBUTE = re.compile(
    r"(?P<name>[A-Za-z_][\w.\-]*(?::[A-Za-z_][\w.\-]*)?)\s*=\s*(?P<q>[\"'])(?P<value>.*?)(?P=q)",
    re.DOTALL,
)

#: `camunda:formField id` is the User Task's process-variable name; the rest are
#: attribute surfaces keyed by their LOCAL name (namespace prefix stripped).
_ATTR_SURFACES: Mapping[tuple[str, str], str] = {
    ("formField", "id"): "bpmn_form_field",
    ("inputParameter", "name"): "bpmn_input_parameter",
    ("outputParameter", "name"): "bpmn_output_parameter",
    ("output", "name"): "dmn_output_name",
}

#: Attributes carrying a variable name regardless of the element they sit on.
_ANY_ELEMENT_ATTR_SURFACES: Mapping[str, str] = {
    "resultVariable": "bpmn_result_variable",
}


def _local(qname: str) -> str:
    return qname.rsplit(":", 1)[-1]


def _line_index(source: str) -> list[int]:
    return [0, *(m.end() for m in re.finditer("\n", source))]


def _line_at(starts: Sequence[int], offset: int) -> int:
    return bisect.bisect_right(starts, offset)


@dataclass(frozen=True, slots=True)
class _Attr:
    local: str
    value: str
    offset: int


@dataclass(frozen=True, slots=True)
class _Start:
    local: str
    parents: tuple[str, ...]
    attrs: tuple[_Attr, ...]
    offset: int


@dataclass(frozen=True, slots=True)
class _Text:
    parents: tuple[str, ...]
    text: str
    offset: int


def scan_xml(source: str) -> Iterator[_Start | _Text]:
    """Walk raw XML, yielding start tags (with attribute offsets) and character data.

    Hand-rolled rather than `ElementTree`-based for one reason: `xml.etree` does
    not carry line numbers, and a finding without `file:line` is not evidence.
    Well-formedness is guaranteed separately by `load_xml` before this runs, so
    this scanner may stay tolerant. Comments, CDATA, PIs and doctypes are
    skipped — a BPMN comment is prose ABOUT variables, not a declaration OF one.
    """
    stack: list[str] = []
    index = 0
    size = len(source)
    while index < size:
        lt = source.find("<", index)
        if lt == -1:
            if stack:
                yield _Text(tuple(stack), source[index:], index)
            break
        if lt > index and stack:
            yield _Text(tuple(stack), source[index:lt], index)

        if source.startswith("<!--", lt):
            end = source.find("-->", lt + 4)
            index = size if end == -1 else end + 3
            continue
        if source.startswith("<![CDATA[", lt):
            end = source.find("]]>", lt + 9)
            body_end = size if end == -1 else end
            if stack:
                yield _Text(tuple(stack), source[lt + 9 : body_end], lt + 9)
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
            attrs = tuple(
                _Attr(_local(m.group("name")), m.group("value"), lt + m.start("value"))
                for m in _ATTRIBUTE.finditer(tag_text)
            )
            yield _Start(local, tuple(stack), attrs, lt)
            if not self_closing:
                stack.append(local)
        index = cursor


# ---------------------------------------------------------------------------
# JUEL
# ---------------------------------------------------------------------------

_JUEL_BLOCK = re.compile(r"\$\{(?P<body>[^}]*)\}", re.DOTALL)
_JUEL_STRING = re.compile(r"'[^']*'|\"[^\"]*\"")
_JUEL_PATH = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\s*\.\s*[A-Za-z_][A-Za-z0-9_]*)*")

#: XML entity references, blanked to SPACES OF THE SAME LENGTH before any scan.
#: Length-preserving because every `file:line` in this module is computed from a
#: byte offset into the ORIGINAL source, and a shortening substitution would move
#: every offset after it. Blanking (rather than decoding) is exact here: the only
#: three entities the corpus uses are `&amp;`, `&gt;` and `&lt;`
#: (`test_only_three_entities_are_used_in_the_corpus` pins that), none of which
#: decodes to a quote — so no string literal can be hidden behind one, and the
#: characters they decode to (`&`, `<`, `>`) are not identifier characters
#: anyway. Without this, `${a &amp;&amp; b}` yields a phantom variable `amp`.
_ENTITY = re.compile(r"&(?:#\d+|#x[0-9A-Fa-f]+|[A-Za-z][A-Za-z0-9]*);")


def blank_entities(text: str) -> str:
    """Replace every XML entity reference with spaces of the same length."""
    return _ENTITY.sub(lambda m: " " * len(m.group(0)), text)


#: Identifiers a JUEL body may contain that are NOT process variables: EL
#: literals and operators, and the Camunda-supplied context objects. Anything
#: not listed here is treated as a process variable — fail-closed, so an
#: unrecognized identifier becomes a name to classify rather than a silent drop.
JUEL_NON_VARIABLES: frozenset[str] = frozenset(
    {
        "true",
        "false",
        "null",
        "empty",
        "and",
        "or",
        "not",
        "div",
        "mod",
        "eq",
        "ne",
        "lt",
        "gt",
        "le",
        "ge",
        "instanceof",
        # Camunda/CIB Seven context objects injected into every EL scope.
        "execution",
        "task",
        "caseExecution",
        "authenticatedUserId",
        "dateTime",
        "now",
        "currentUser",
        "S",
        "T",
        "XML",
        "JSON",
        "spin",
    }
)

#: EL method names reached through a context object or a variable — `getVariable`
#: in `${execution.getVariable('x')}` is a call, not a field. Only ever consulted
#: for NON-root path segments.
JUEL_METHOD_SEGMENTS: frozenset[str] = frozenset(
    {
        "getVariable",
        "hasVariable",
        "getVariables",
        "setVariable",
        "getProcessInstanceId",
        "getBusinessKey",
        "getId",
        "contains",
        "isEmpty",
        "size",
        "length",
        "toString",
        "prop",
        "elements",
        "value",
    }
)


def juel_names(text: str) -> list[tuple[str, str, int]]:
    """`(name, surface, offset)` for every process variable a `${...}` body reads.

    `offset` is relative to `text` and points at the token itself, so a
    multi-line expression pins each name to its own line. A dotted path yields
    its ROOT under `juel_root` (the process variable the engine resolves) plus
    each non-root segment under `juel_segment` (a field inside a result map —
    see the module's declared limits). String literals and XML entities are
    blanked LENGTH-PRESERVINGLY first, so `${x == 'nome'}` never yields `nome`,
    `${a &amp;&amp; b}` never yields `amp`, and every offset stays exact.
    """
    found: list[tuple[str, str, int]] = []
    seen: set[tuple[str, str]] = set()
    for block in _JUEL_BLOCK.finditer(blank_entities(text)):
        body_start = block.start("body")
        # String literals are blanked INSIDE the ${...} body only: an apostrophe
        # in surrounding pt-BR prose is punctuation, not a quote, and blanking on
        # the whole text could swallow a whole expression between two of them.
        body = _JUEL_STRING.sub(lambda m: " " * len(m.group(0)), block.group("body"))
        for path in _JUEL_PATH.finditer(body):
            cursor = body_start + path.start()
            for position, part in enumerate(path.group(0).split(".")):
                stripped = part.strip()
                offset = cursor + part.index(stripped) if stripped else cursor
                cursor += len(part) + 1
                if not stripped:
                    continue
                if position == 0:
                    if stripped in JUEL_NON_VARIABLES:
                        break
                    surface = "juel_root"
                else:
                    if stripped in JUEL_METHOD_SEGMENTS:
                        continue
                    surface = "juel_segment"
                if (stripped, surface) not in seen:
                    seen.add((stripped, surface))
                    found.append((stripped, surface, offset))
    return found


# ---------------------------------------------------------------------------
# Collection — BPMN / DMN
# ---------------------------------------------------------------------------

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

#: `camunda:formField` types whose value space is bounded by the TYPE itself. A
#: field of any other type — including a field that declares no type at all,
#: which is the fail-closed reading — is an unbounded box unless it carries a
#: `camunda:value` domain. Measured on the live corpus: 19 form fields, 5 `enum`
#: (all with `camunda:value` children) and 14 `string` (none with any).
BOUNDED_FORM_FIELD_TYPES: frozenset[str] = frozenset({"boolean", "long", "date", "enum"})


def free_text_form_field_offsets(source: str) -> frozenset[int]:
    """Offsets of the `id` value of every UNBOUNDED `camunda:formField` in `source`.

    Unbounded = the type is not in `BOUNDED_FORM_FIELD_TYPES` AND the element
    declares no `camunda:value` child. The offsets are the ones
    `collect_xml_refs_from_source` already computes for the `bpmn_form_field`
    surface, so the two agree by construction.

    Driven off `scan_xml`'s element stack rather than a `formField ... /formField`
    block regex, for the reason `annotated_phi_fields` documents: a non-greedy
    block regex stops at the FIRST `/>` (a self-closing `camunda:value`) and
    would report every field unbounded — the most dangerous way to be wrong,
    because it is the direction that looks like more coverage.
    """
    out: set[int] = set()
    pending: tuple[int, bool] | None = None  # (id offset, type is bounded)
    saw_value = False

    def flush() -> None:
        nonlocal pending, saw_value
        if pending is not None:
            offset, bounded = pending
            if not bounded and not saw_value:
                out.add(offset)
        pending = None
        saw_value = False

    for event in scan_xml(source):
        opening_field = isinstance(event, _Start) and event.local == "formField"
        if pending is not None and (opening_field or "formField" not in event.parents):
            flush()
        if opening_field:
            assert isinstance(event, _Start)  # narrowed by `opening_field`
            attrs = {a.local: a for a in event.attrs}
            field_id = attrs.get("id")
            if field_id is not None:
                field_type = attrs["type"].value.strip() if "type" in attrs else ""
                pending = (field_id.offset, field_type in BOUNDED_FORM_FIELD_TYPES)
        elif pending is not None and isinstance(event, _Start) and event.local == "value":
            saw_value = True
    flush()
    return frozenset(out)


def collect_xml_refs(path: Path, report: Report) -> list[VarRef]:
    """Every `VarRef` one `.bpmn`/`.dmn` declares or reads. A parse failure is an error."""
    try:
        load_xml(path)
        source = path.read_text(encoding="utf-8")
    except ParseError as exc:
        report.error(path, str(exc))
        return []
    except OSError as exc:
        report.error(path, f"could not read file: {exc}")
        return []
    return collect_xml_refs_from_source(path, source)


def collect_xml_refs_from_source(path: Path, source: str) -> list[VarRef]:
    """`collect_xml_refs` over an in-memory document (the unit-testable half)."""
    starts = _line_index(source)
    refs: list[VarRef] = []
    unbounded_fields = free_text_form_field_offsets(source)

    def add(name: str, offset: int, surface: str, *, free_text: bool = False) -> None:
        cleaned = name.strip()
        if cleaned:
            refs.append(VarRef(cleaned, path, _line_at(starts, offset), surface, free_text))

    for event in scan_xml(source):
        if isinstance(event, _Start):
            for attr in event.attrs:
                surface = _ATTR_SURFACES.get((event.local, attr.local))
                if surface is None:
                    surface = _ANY_ELEMENT_ATTR_SURFACES.get(attr.local)
                if surface is not None:
                    add(attr.value, attr.offset, surface, free_text=attr.offset in unbounded_fields)
                for name, juel_surface, offset in juel_names(attr.value):
                    add(name, attr.offset + offset, juel_surface)
        else:
            # `dmn:inputExpression/text` holds the variable a column reads. It is
            # a FEEL expression, so only a bare identifier is taken as a variable
            # name; anything richer falls through to the `${...}` sweep below.
            if event.parents[-1:] == ("text",) and "inputExpression" in event.parents:
                candidate = event.text.strip()
                if _IDENTIFIER.match(candidate):
                    add(candidate, event.offset, "dmn_input_expression")
            if event.parents[-1:] == ("documentation",):
                self_declared = declared_input_free_text_names(event.text)
                for name, offset in declared_input_names(event.text):
                    add(
                        name,
                        event.offset + offset,
                        "bpmn_declared_input",
                        free_text=name in self_declared,
                    )
            for name, juel_surface, offset in juel_names(event.text):
                add(name, event.offset + offset, juel_surface)

    return refs


# ---------------------------------------------------------------------------
# Collection — the YAML manifests
# ---------------------------------------------------------------------------

#: Mapping keys under which a `spec/processes/dmn/*.yaml` manifest declares the
#: variables of a candidate decision table. `entradas`/`saidas` is the shadow
#: candidates' own vocabulary. The manifests are deliberately NOT named here:
#: `tests/unit/spec/test_shadow_candidates_common.py::test_no_src_consumer_of_the_candidate_manifests`
#: proves the W4 wave is unwired by asserting that no file under `src/` names
#: one of them, and that proof is worth more than an example filename in a
#: comment (the same discipline PR-1's `perspective.py` records). The
#: fence-side pin lives in this module's unit test instead.
YAML_VARIABLE_BLOCKS: frozenset[str] = frozenset({"entradas", "saidas"})


def collect_yaml_refs(path: Path, report: Report) -> list[VarRef]:
    """Every variable name a spec manifest declares under `entradas:`/`saidas:`.

    Line provenance comes from a second, textual pass: PyYAML's `safe_load`
    discards positions, and re-parsing with the node API to recover them would
    duplicate the loader this package already standardises on. The textual pass
    only ever runs over keys the parsed document already proved are there.
    """
    try:
        data = load_yaml(path)
    except ParseError as exc:
        report.error(path, str(exc))
        return []

    names: set[str] = set()
    _walk_yaml(data, in_block=False, out=names)
    if not names:
        return []

    source = path.read_text(encoding="utf-8")
    refs: list[VarRef] = []
    for lineno, line in enumerate(source.splitlines(), start=1):
        stripped = line.strip()
        key = stripped.split(":", 1)[0].strip() if ":" in stripped else ""
        if key and key in names:
            refs.append(VarRef(key, path, lineno, "yaml_manifest_variable"))
    return refs


def _walk_yaml(node: object, *, in_block: bool, out: set[str]) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            key_text = str(key)
            if in_block and _IDENTIFIER.match(key_text):
                out.add(key_text)
            _walk_yaml(value, in_block=key_text in YAML_VARIABLE_BLOCKS, out=out)
    elif isinstance(node, list):
        for item in node:
            _walk_yaml(item, in_block=in_block, out=out)


# ---------------------------------------------------------------------------
# The artifact-declared PHI annotation (an independent cross-check)
# ---------------------------------------------------------------------------

#: The `zona` property's value, when it opens with `PHI`, is the artifact's own
#: declaration that the field it annotates lives in the PHI zone (ADR-0006).
ZONA_PROPERTY = "zona"
ZONA_PHI_PREFIX = "PHI"


def annotated_phi_fields(path: Path, source: str) -> list[VarRef]:
    """Form fields the ARTIFACT ITSELF marks `camunda:property name="zona" value="PHI ..."`.

    A second, independent statement of the same fact: the BPMN author wrote
    "this field is Zona PHI" into the process definition (e.g.
    `SP-OP-AUTH-001_Autorizacao_Previa.bpmn:309`). If such a field is not in a
    PHI set, the artifact and the runtime control contradict each other — which
    `_check_annotations` reports as an error, independently of the shape
    heuristic (an annotation is a human's declaration; a shape match is a guess).

    Driven off `scan_xml`'s element stack rather than a `formField ... /formField`
    block regex: a `formField` body contains self-closing children
    (`camunda:value`, `camunda:constraint`), so a non-greedy block regex
    terminates at the FIRST `/>` and never reaches the `zona` property — it would
    report zero hits on a corpus that has five, which is the most dangerous way
    for a fence to be wrong.
    """
    starts = _line_index(source)
    out: list[VarRef] = []
    open_field: tuple[str, int] | None = None
    for event in scan_xml(source):
        if not isinstance(event, _Start):
            continue
        if event.local == "formField":
            field_id = next((a for a in event.attrs if a.local == "id"), None)
            open_field = (field_id.value.strip(), field_id.offset) if field_id else None
            continue
        if event.local != "property" or open_field is None or "formField" not in event.parents:
            continue
        attrs = {a.local: a.value for a in event.attrs}
        if attrs.get("name") == ZONA_PROPERTY and attrs.get("value", "").strip().startswith(ZONA_PHI_PREFIX):
            name, offset = open_field
            if name:
                out.append(VarRef(name, path, _line_at(starts, offset), "bpmn_form_field"))
    return out


# ---------------------------------------------------------------------------
# The prose roll of start variables (`VARIAVEIS DE ENTRADA:`)
# ---------------------------------------------------------------------------

#: 15 of the 16 BPMN (all but the timer-started `SP-OP-ANS-CRON-001`) declare
#: their START variables in a `bpmn:documentation` roll headed by this marker —
#: e.g. `SP-OP-AUTH-001_Autorizacao_Previa.bpmn:57`. Those variables are set by
#: the CALLER (`start_process_idempotent`), so they appear in no `formField`, no
#: `inputParameter` and no `${...}`: without this surface the sweep misses the
#: highest-PHI-risk names in the corpus, including `matricula_beneficiario` and
#: `resumo_contexto` (both already in `PHI_PROCESS_VARS`) and the bare `cid10`.
DECLARED_INPUT_MARKER = "VARIAVEIS DE ENTRADA"

#: Parenthesised spans in the roll are ANNOTATIONS, not variables — a type
#: (`(bool, pre-resolvido)`), an ADR reference (`(ADR-0006)`), an optionality
#: marker (`(opc)`) or an enum domain (`(credenciamento | descredenciamento)`),
#: any of which may span lines. Blanked length-preservingly before splitting —
#: and NEWLINES INSIDE THE SPAN ARE KEPT, because the roll is split on `[,\n]`:
#: flattening a multi-line annotation into spaces would weld the name that
#: follows it to the sentence on the next line and truncate the roll there
#: (measured: `SP-OP-CRED-001` lost `especialidade` exactly that way).
_PAREN_SPAN = re.compile(r"\([^()]*\)", re.DOTALL)
_NON_NEWLINE = re.compile(r"[^\n]")
_ROLL_SPLIT = re.compile(r"[,\n]")
_ROLL_LEADING = re.compile(r"[a-z][a-z0-9_]*")


def _blank_span(match: re.Match[str]) -> str:
    return _NON_NEWLINE.sub(" ", match.group(0))


def declared_input_names(text: str) -> list[tuple[str, int]]:
    """`(name, offset)` for each variable in a `VARIAVEIS DE ENTRADA:` roll.

    The roll is prose, so the parse is deliberately conservative and stops at the
    FIRST chunk that is not a bare snake_case identifier — the sentence that
    follows the enumeration ("Contrato completo: ...", "SAIDA: ...", "* pre-
    resolvidos ..."). Consequence, stated rather than hidden: this surface can
    only UNDER-collect. An early stop loses names (weakening coverage) and can
    never invent one (which would red the build for a variable that does not
    exist). `test_declared_input_roll_is_pinned_per_file` pins what each of the
    15 rolls yields today, so a prose edit that shortens a roll is visible.
    """
    start = text.upper().find(DECLARED_INPUT_MARKER)
    if start == -1:
        return []
    blanked = text
    while True:  # nested annotations exist; blank innermost-out to a fixed point
        collapsed = _PAREN_SPAN.sub(_blank_span, blanked)
        if collapsed == blanked:
            break
        blanked = collapsed
    colon = blanked.find(":", start)
    if colon == -1:
        return []

    out: list[tuple[str, int]] = []
    cursor = colon + 1
    for chunk in _ROLL_SPLIT.split(blanked[colon + 1 :]):
        candidate = chunk.strip()
        if not candidate:
            cursor += len(chunk) + 1
            continue
        leading = _ROLL_LEADING.match(candidate)
        if leading is None:
            break  # the sentence after the roll ("Contrato completo: ...")
        name = leading.group(0)
        out.append((name, cursor + chunk.index(name)))
        cursor += len(chunk) + 1
        # A chunk that is MORE than the bare name (plus `.`/`*` markers) is the
        # LAST roll entry with the following sentence welded to it — take the
        # name, then stop rather than walking into prose.
        if candidate[leading.end() :].strip(" .*"):
            break
    return out


#: Words a roll annotation uses to say "this variable holds prose a human wrote".
#: Each one is attested by a line in this repo that uses it about a field the repo
#: already treats as PHI: "texto livre PHI"
#: (`SP-OP-ESCALATION-001_Escalonamento_Humano_Universal.bpmn:145`), "(free-text
#: PHI)" (`src/maezo/tools/workers/lgpd.py:483`), "narrativa"
#: (`src/maezo/agents/beatriz/prompts.py:61`).
FREE_TEXT_MARKERS: tuple[str, ...] = (
    "texto livre",
    "texto-livre",
    "free text",
    "free-text",
    "narrativa",
)

#: The parenthesised annotation that FOLLOWS a name in a roll, one nesting level
#: deep (the rolls really do nest, e.g. `(bool, opc — so descredenciamento)`).
_ANNOTATION_AFTER_NAME = re.compile(r"[\s.*]*\((?P<body>[^()]*(?:\([^()]*\)[^()]*)*)\)", re.DOTALL)


def declared_input_free_text_names(text: str) -> frozenset[str]:
    """Names in a `VARIAVEIS DE ENTRADA` roll whose OWN annotation says free text.

    The second arm of the structural signal, and the one that reads a human's
    declaration rather than an XSD type: `detalhes_requisicao (texto livre)` is
    suspect because the BPMN author wrote that it is, whatever the name looks
    like. Measured on the live corpus: ZERO roll entries carry such an
    annotation today, and `test_no_roll_entry_declares_itself_free_text_today`
    pins the zero so this arm cannot rot into a silently-true limit.
    """
    out: set[str] = set()
    for name, offset in declared_input_names(text):
        annotation = _ANNOTATION_AFTER_NAME.match(text, offset + len(name))
        if annotation is None:
            continue
        body = annotation.group("body").lower()
        if any(marker in body for marker in FREE_TEXT_MARKERS):
            out.add(name)
    return frozenset(out)


# ---------------------------------------------------------------------------
# The sweep
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Sweep:
    """The result of reading one `spec/processes` tree: refs, buckets, annotations."""

    refs: tuple[VarRef, ...]
    annotated: tuple[VarRef, ...]

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(sorted({ref.name for ref in self.refs}))

    @property
    def free_text_names(self) -> frozenset[str]:
        """Names with at least one occurrence the ARTIFACT declares free text."""
        return frozenset(ref.name for ref in self.refs if ref.free_text)

    def bucket(self, name: str) -> str:
        return classify(name, free_text=name in self.free_text_names)

    def by_bucket(self) -> dict[str, tuple[str, ...]]:
        free_text = self.free_text_names
        out: dict[str, list[str]] = {LISTED: [], SHAPE_SUSPECT: [], CLEAN: []}
        for name in self.names:
            out[classify(name, free_text=name in free_text)].append(name)
        return {key: tuple(value) for key, value in out.items()}

    def provenance(self, name: str) -> tuple[VarRef, ...]:
        return tuple(ref for ref in self.refs if ref.name == name)


def sweep_processes_root(root: Path, report: Report) -> Sweep:
    """Collect every `VarRef` under a `spec/processes`-shaped tree (`bpmn/` + `dmn/`)."""
    refs: list[VarRef] = []
    annotated: list[VarRef] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix in {".bpmn", ".dmn"}:
            refs.extend(collect_xml_refs(path, report))
            try:
                annotated.extend(annotated_phi_fields(path, path.read_text(encoding="utf-8")))
            except OSError as exc:  # pragma: no cover - collect_xml_refs already reported it
                report.error(path, f"could not read file: {exc}")
        elif path.suffix in {".yaml", ".yml"}:
            refs.extend(collect_yaml_refs(path, report))
    return Sweep(tuple(refs), tuple(annotated))


# ---------------------------------------------------------------------------
# The fence
# ---------------------------------------------------------------------------


def check_sweep(sweep: Sweep, report: Report) -> None:
    """Fail closed on an undisposed PHI-shaped name, and on a contradicted annotation."""
    _check_shape_suspects(sweep, report)
    _check_annotations(sweep, report)


def _check_shape_suspects(sweep: Sweep, report: Report) -> None:
    for name in sweep.by_bucket()[SHAPE_SUSPECT]:
        disposition = DISPOSITIONS.get(name)
        if disposition is not None:
            # A disposition only silences the fence while it is still a QUESTION.
            assert_draft_status(disposition)
            continue
        free_text = any(ref.free_text for ref in sweep.provenance(name))
        why = (
            "is declared FREE TEXT by the artifact itself (an unbounded camunda:formField, or a "
            "roll entry annotated as such)"
            if free_text
            else f"matches the PHI-SHAPE heuristic (segments {segments(name)})"
        )
        where = ", ".join(ref.render() for ref in sweep.provenance(name)[:5])
        report.error(
            sweep.provenance(name)[0].path,
            f"process variable '{name}' {why} but is in neither PHI_PROCESS_VARS nor "
            "PHI_FIELDS, and has no entry in phi_completeness.DISPOSITIONS. Seen at: "
            f"{where}. Add a Disposition recording the DPO question and engineering's "
            f"recommendation (status is always '{DRAFT_VERIFY}') — never add the name to a "
            "PHI set to make this pass.",
        )


def _check_annotations(sweep: Sweep, report: Report) -> None:
    for ref in sweep.annotated:
        if ref.name in PHI_LISTED_NAMES:
            continue
        report.error(
            ref.path,
            f"form field '{ref.name}' at line {ref.line} is annotated "
            'camunda:property name="zona" value="PHI ..." by the artifact itself, but the '
            "name is in neither PHI_PROCESS_VARS nor PHI_FIELDS — the process definition and "
            "the name-anchored runtime control contradict each other (ADR-0006).",
        )


def check_processes_root(root: Path, report: Report) -> Sweep:
    """Sweep a `spec/processes` tree and apply the fence. Returns the sweep for reporting."""
    sweep = sweep_processes_root(root, report)
    check_sweep(sweep, report)
    return sweep


# ---------------------------------------------------------------------------
# Reporting helpers (used by the unit test and by hand when answering DU-07)
# ---------------------------------------------------------------------------


def render_buckets(sweep: Sweep) -> str:
    """A human-readable three-bucket report with `file:line` provenance."""
    buckets = sweep.by_bucket()
    lines: list[str] = []
    for bucket in (LISTED, SHAPE_SUSPECT, CLEAN):
        names = buckets[bucket]
        lines.append(f"## {bucket} ({len(names)})")
        for name in names:
            first = sweep.provenance(name)[0]
            disposition = DISPOSITIONS.get(name)
            suffix = ""
            if disposition is not None:
                # Re-validated HERE, not trusted from construction: this is the one
                # place a status becomes a sentence a human reads.
                assert_draft_status(disposition)
                suffix = f" [{disposition.status}]"
            lines.append(f"  {name} — {first.render()}{suffix}")
        lines.append("")
    return "\n".join(lines)


def iter_all_refs(sweep: Sweep, names: Iterable[str]) -> Iterator[VarRef]:
    """Every `VarRef` for each name in `names`, in file order."""
    wanted = set(names)
    for ref in sweep.refs:
        if ref.name in wanted:
            yield ref
