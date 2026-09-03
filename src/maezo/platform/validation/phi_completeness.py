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
   * `SHAPE_SUSPECT` — the name matches the PHI-SHAPE heuristic (`SHAPE_TOKENS`,
     every token sourced from this repo's own vocabulary) but is in neither set;
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
the DPO/security reviewers (`.github/CODEOWNERS`, quoted in
`spec/policies/privacy/phi-business-key-remediation.yaml:48-51`), and a table of
UNRATIFIED engineering recommendations must not be laundered into a
DPO-owned policy path by the same commit that writes it. `docs/review-queue.md`
carries the migration item: once the DPO ratifies, the table becomes a
CODEOWNED privacy manifest and this constant is replaced by its loader. That is
an owner act, not an engineering one.

The PHI-SHAPE heuristic (§ `SHAPE_TOKENS`)
------------------------------------------
Every token is sourced from a place this repo ALREADY treats as PHI vocabulary;
`SHAPE_TOKENS` records the citation next to each one, and
`test_every_shape_token_cites_a_live_source` re-reads those citations so a
token whose source moved or vanished fails the build. Two tokens are marked
`ATTESTED_NOWHERE` — they are declared extrapolations, not repo facts, and are
labelled as such rather than dressed up with a plausible-looking citation.

Matching is on SEGMENTS of the name (split on `_`, `.`, `-`, and camelCase
boundaries), never on substrings, so `nome` matches `nome_mae` and `nomeMae`
but not `sobrenome_fantasia`; and `cid` matches `cid10_referencia` (the digits
are stripped from a segment before comparison) but not `decidir`.

Declared limits (measured, not assumed)
---------------------------------------
* **Shape, not content.** A variable named `observacao_interna` that carries a
  clinical note is invisible here, exactly as it is invisible to
  `redact_phi_vars`. This fence proves the NAME universe, which is the universe
  the runtime control keys on; it does not read values.
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
    """One occurrence of a process-variable name, pinned to where it was found."""

    name: str
    path: Path
    line: int
    surface: str

    def render(self) -> str:
        return f"{self.path}:{self.line} ({self.surface})"


# ---------------------------------------------------------------------------
# The PHI-SHAPE heuristic
# ---------------------------------------------------------------------------

#: Marker for a token that this repo's vocabulary does NOT attest. Kept explicit
#: rather than paired with a plausible-looking citation: an unsourced token is a
#: judgement call, and a reader deserves to see which ones are.
ATTESTED_NOWHERE = "ATTESTED_NOWHERE — declared extrapolation, no repo attestation"


@dataclass(frozen=True, slots=True)
class ShapeToken:
    """One PHI-shape token and the repo location that attests it as PHI vocabulary."""

    token: str
    source: str
    quote: str


#: The heuristic's vocabulary. Each entry's `source` is a `file:line` in THIS
#: repo that already treats the token as PHI, and `quote` is a substring that
#: must still be present at that location — `test_every_shape_token_cites_a_live_source`
#: re-reads every one, so a citation that rots fails the build instead of
#: decaying into folklore.
SHAPE_TOKENS: tuple[ShapeToken, ...] = (
    # --- gateway.pseudonymizer.PHI_FIELDS (the canonical four) ---
    ShapeToken("cpf", "src/maezo/gateway/pseudonymizer.py:43", '"cpf"'),
    ShapeToken("nome", "src/maezo/gateway/pseudonymizer.py:44", '"nome"'),
    ShapeToken("telefone", "src/maezo/gateway/pseudonymizer.py:45", '"telefone"'),
    ShapeToken("email", "src/maezo/gateway/pseudonymizer.py:46", '"email"'),
    # --- tools.workers.phi_vars.PHI_PROCESS_VARS (the clinical-free-text eight) ---
    ShapeToken("justificativa", "src/maezo/tools/workers/phi_vars.py:54", '"justificativa_clinica"'),
    ShapeToken("cid", "src/maezo/tools/workers/phi_vars.py:55", '"cid10_referencia"'),
    ShapeToken("fundamentacao", "src/maezo/tools/workers/phi_vars.py:56", '"fundamentacao_dut"'),
    ShapeToken("notas", "src/maezo/tools/workers/phi_vars.py:57", '"notas_resolucao"'),
    ShapeToken("resumo", "src/maezo/tools/workers/phi_vars.py:58", '"resumo_contexto"'),
    ShapeToken("matricula", "src/maezo/tools/workers/phi_vars.py:59", '"matricula_beneficiario"'),
    ShapeToken("laudo", "src/maezo/tools/workers/phi_vars.py:60", '"laudo"'),
    ShapeToken("diagnostico", "src/maezo/tools/workers/phi_vars.py:61", '"diagnostico"'),
    # --- beatriz's raw-PHI refusal set (an item carrying any of these is refused whole) ---
    ShapeToken("cns", "src/maezo/agents/beatriz/graph.py:143", '"cns"'),
    ShapeToken("rg", "src/maezo/agents/beatriz/graph.py:143", '"rg"'),
    ShapeToken("endereco", "src/maezo/agents/beatriz/graph.py:143", '"endereco"'),
    # --- fraude's evidence-reference PHI markers ---
    ShapeToken("social", "src/maezo/tools/workers/fraude.py:65", '"nome_social"'),
    # --- the WhatsApp payload force-tokenization vocabulary (review-queue, res-xphi-raw-dict-payloads) ---
    ShapeToken("nascimento", "docs/review-queue.md:436", "data_nascimento"),
    ShapeToken("cep", "docs/review-queue.md:436", "cep"),
    # --- clinical-record vocabulary named as PHI-in-a-bounded-token by the harness fence ---
    ShapeToken("prontuario", "src/maezo/tools/workers/harness.py:282", "prontuario number"),
    # --- LGPD DSR free-text request detail, named PHI in the worker that refuses to forward it ---
    ShapeToken("detalhes", "src/maezo/tools/workers/lgpd.py:483", "detalhes_requisicao (free-text PHI)"),
    # --- fraud-dossier free narrative written by an LLM over case facts ---
    ShapeToken("narrativa", "src/maezo/agents/beatriz/prompts.py:61", "narrativa"),
    # --- declared extrapolations (no repo attestation; named so a reader can see the seam) ---
    ShapeToken("mae", ATTESTED_NOWHERE, ""),
    ShapeToken("anamnese", ATTESTED_NOWHERE, ""),
)

#: The token strings, for fast membership tests.
SHAPE_TOKEN_NAMES: frozenset[str] = frozenset(t.token for t in SHAPE_TOKENS)

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
    """True iff any segment of `name` is a PHI-shape token (see `SHAPE_TOKENS`)."""
    return any(segment in SHAPE_TOKEN_NAMES for segment in segments(name))


def classify(name: str) -> str:
    """`LISTED`, `SHAPE_SUSPECT` or `CLEAN` for one process-variable name."""
    if name in PHI_LISTED_NAMES:
        return LISTED
    if matches_phi_shape(name):
        return SHAPE_SUSPECT
    return CLEAN


# ---------------------------------------------------------------------------
# The disposition table — questions asked, never answers given
# ---------------------------------------------------------------------------

#: The ONLY status a `Disposition` may carry. There is no `RATIFICADO` value: a
#: ratification is a DPO act recorded in a CODEOWNED privacy manifest, and this
#: module has no vocabulary for one on purpose.
DRAFT_VERIFY = "DRAFT/verify (DPO)"


@dataclass(frozen=True, slots=True)
class Disposition:
    """An OPEN DPO question about one PHI-shaped, unlisted process-variable name.

    `recommendation` is engineering's reading of the evidence — a proposal for
    the DPO to accept or reject, never a decision. `status` is always
    `DRAFT_VERIFY`; `__post_init__` refuses anything else, so the table cannot
    quietly acquire a ratified-looking row.
    """

    name: str
    evidence: str
    recommendation: str
    status: str = DRAFT_VERIFY

    def __post_init__(self) -> None:
        if self.status != DRAFT_VERIFY:
            raise ValueError(
                f"Disposition({self.name!r}): status must be {DRAFT_VERIFY!r} — a ratified "
                "classification is a DPO act recorded in a CODEOWNED privacy manifest, "
                "never a constant in this module"
            )


DISPOSITIONS: Mapping[str, Disposition] = {
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
                "GW_GuardFundamentacao conditionExpression that gates a reasoned denial)."
            ),
            recommendation=(
                "RECOMMEND treating it as the same class as its listed sibling "
                "`fundamentacao_dut` (phi_vars.py:56): both are free text a human writes to "
                "justify an adverse outcome about one identified subject, and free text is "
                "exactly the shape `redact_phi_vars` exists to stop leaving a worker. The "
                "BPMN's own prose expects clinical grounding in it ('ex.: retencao "
                "obrigatoria de prontuario', :195). Counter-evidence the DPO must weigh: the "
                "values in spec/processes/dmn/lgpd_dsr_routing.dmn are statutory citations, "
                "not narrative — but nothing MECHANICALLY holds a free-text field to those."
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
                "content the set exists to cover, and ADR-0006 puts diagnosis in the PHI "
                "zone. Cost of listing, measured: ZERO decisions read it — no DMN "
                "inputExpression in spec/processes/dmn/ evaluates `cid10` (verified by sweep), "
                "so redacting it at worker egress removes no routing fact. Counter-evidence: "
                "a CID-10 code is a bounded code, not free text; if the DPO reads bounded "
                "codes as out of scope, then `cid10_referencia`'s own listing is the "
                "inconsistency to revisit — the two must not be classified differently."
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
                "identifies whose diagnosis it is. Cost of listing, measured: none for the "
                "decision — DMN evaluation is engine-side (ADR-0028) and `redact_phi_vars` "
                "runs only at worker EGRESS toward the general zone, so listing redacts the "
                "Kafka/notification copy and leaves the engine's evaluation untouched. "
                "Counter-evidence: a redacted boolean in an audit payload loses a fact an "
                "operator may need to explain a denial."
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
    )
}


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

    def add(name: str, offset: int, surface: str) -> None:
        cleaned = name.strip()
        if cleaned:
            refs.append(VarRef(cleaned, path, _line_at(starts, offset), surface))

    for event in scan_xml(source):
        if isinstance(event, _Start):
            for attr in event.attrs:
                surface = _ATTR_SURFACES.get((event.local, attr.local))
                if surface is None:
                    surface = _ANY_ELEMENT_ATTR_SURFACES.get(attr.local)
                if surface is not None:
                    add(attr.value, attr.offset, surface)
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
                for name, offset in declared_input_names(event.text):
                    add(name, event.offset + offset, "bpmn_declared_input")
            for name, juel_surface, offset in juel_names(event.text):
                add(name, event.offset + offset, juel_surface)

    return refs


# ---------------------------------------------------------------------------
# Collection — the YAML manifests
# ---------------------------------------------------------------------------

#: Mapping keys under which a `spec/processes/dmn/*.yaml` manifest declares the
#: variables of a candidate decision table. `entradas`/`saidas` is the shadow
#: candidates' own vocabulary (e.g. `carencia-check-shadow-candidate.yaml:295`).
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

    def bucket(self, name: str) -> str:
        return classify(name)

    def by_bucket(self) -> dict[str, tuple[str, ...]]:
        out: dict[str, list[str]] = {LISTED: [], SHAPE_SUSPECT: [], CLEAN: []}
        for name in self.names:
            out[classify(name)].append(name)
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
        if name in DISPOSITIONS:
            continue
        where = ", ".join(ref.render() for ref in sweep.provenance(name)[:5])
        report.error(
            sweep.provenance(name)[0].path,
            f"process variable '{name}' matches the PHI-SHAPE heuristic (segments "
            f"{segments(name)}) but is in neither PHI_PROCESS_VARS nor PHI_FIELDS, and has no "
            f"entry in phi_completeness.DISPOSITIONS. Seen at: {where}. Add a Disposition "
            "recording the DPO question and engineering's recommendation (status is always "
            f"'{DRAFT_VERIFY}') — never add the name to a PHI set to make this pass.",
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
            suffix = f" [{disposition.status}]" if disposition else ""
            lines.append(f"  {name} — {first.render()}{suffix}")
        lines.append("")
    return "\n".join(lines)


def iter_all_refs(sweep: Sweep, names: Iterable[str]) -> Iterator[VarRef]:
    """Every `VarRef` for each name in `names`, in file order."""
    wanted = set(names)
    for ref in sweep.refs:
        if ref.name in wanted:
            yield ref
