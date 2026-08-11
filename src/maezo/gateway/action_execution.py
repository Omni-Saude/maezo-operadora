"""ActionExecutionGateway — the per-call chokepoint of ADR-0037 XRD-09 (MZO-040).

WHAT THIS IS. ADR-0034 ratified the DESCOPE of the per-tool-call PEP chokepoint, with ONE
explicit revisit precondition: "a amostragem L2 so volta a ser root-cause-correct SE e QUANDO o
v2 introduzir um chokepoint de dispatch PEP por-tool-call em runtime" (`0034-...:78-85`).
ADR-0037's XRD-09 clause is the ratification vehicle that precondition demands — and it names its
own effectivity condition: the clause "só produz efeito com a aceitação deste ADR + evidência
MZO-040 entregue" (`0037-...:151-157, 241-244`).

WHY IT SHIPS DENYING. `PLANS.md` §0.6 records MZO-040 as blocked on three human approvals —
Médica, ANS e Security — none granted; DL-0042 unloaded the DPO/Legal gate for MZO-020 and says
in terms that it does NOT reach these three. Building this gateway "after approval" would mean
asking three approvers to sign a project description. So it is built, wired in SHADOW, and every
action class DENIES in enforcement terms until a human writes an approval record. The approvers
then ratify against the shadow telemetry the running system produces.

THE RECORD IS DATA. `spec/policies/autonomy/action-approvals.yaml` — CODEOWNERS-LISTED, which is
NOT the same as gated: server-side branch protection is not currently active on `main` (protection
404 + rulesets empty), so a CODEOWNERS entry requests a reviewer without being able to require one.
That owner finding is already recorded in evidence-ledger row `mzo-000`; restoring protection is
the owner's decision, not this module's. Ratifying is a data change: fill the per-domain blocks,
complete the topic map, set `status: RATIFICADO`, set `modo: enforcing`. No Python change, no
redeploy of this module.

PRECEDENTS MIRRORED — both live in this repo, and this module follows both deliberately:

  * `maezo.platform.lifecycle.legal_bases_matrix.load_retention_matrix` — the DPO retention
    matrix that REFUSES its own `unratified: true` placeholder template rather than "succeeding"
    on placeholder data. The same root guard is implemented below.
  * `maezo.tools.workers.auth_criteria.load_criteria_sources` — the GAP-AUTH-4 ratification gate,
    which SWALLOWS every load failure into "nothing is ratified" instead of raising, because a
    raise on a worker path becomes an external-task incident and an incident STALLS a care
    request. This loader takes the same posture, for a stronger reason: it sits in front of every
    external effect, and it is wired in SHADOW today, where a raise would be a behaviour change.

FAIL-CLOSED IN BOTH DIRECTIONS. A load failure yields zero approved classes AND an UNRESOLVED
mode. `evaluate()` then returns `allow=False` (a DENY in enforcement terms) with a precise
reason, while `enforced` is False — so a broken manifest cannot silently *enable* enforcement
either. The residual that this leaves (deleting the deployed file after the flip downgrades
enforcement to shadow) is closed in-repo by
`tests/unit/sec/test_action_execution_fence.py::test_shipped_manifest_exists_and_is_wellformed`:
it is the FENCE TEST that fails CI on a deletion — `.github/workflows/ci.yml:61` runs
`pytest tests/` on every PR — not CODEOWNERS, which here only requests a reviewer. The residual is
disclosed, with its named runtime remedy, in `docs/reviews/mzo-040-approval-packet.md`.

THE DEPLOYMENT OVERRIDE IS NOT AN ENFORCEMENT SURFACE. `MAEZO_ACTION_APPROVALS_PATH` can point
this loader at a manifest that no CODEOWNER ever saw. That is legitimate for staging a candidate
record, and it must never be a way to turn live enforcement on: an override-sourced manifest
reading `modo: enforcing` resolves to `shadow_override` (never enforces) UNLESS the operator also
sets `MAEZO_ACTION_APPROVALS_ALLOW_OVERRIDE_ENFORCEMENT=1`, which is the deliberate staged-rollout
act. Every override-sourced load emits an `error` line naming the resolved path and whether
enforcement was permitted.

NO PHI. Nothing in this module reads, logs or stores a business key, a payload variable, or any
free text. The only values that reach telemetry are the topic, the action class, a bounded reason
token from the closed enum below, the mode, and the tenant — every one of them a bounded,
non-PHI token guarded by `_is_bounded_token`.
"""

from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final

import structlog
import yaml
from yaml.nodes import MappingNode

from maezo.agents import MAEZO_SPEC_DIR_ENV, resolve_spec_dir

logger = structlog.get_logger(__name__)

#: Narrow env override for the manifest path — a DEPLOYMENT-ONLY escape hatch, mirroring
#: `auth_criteria.MANIFEST_PATH_ENV`. When unset the default resolves through the single T0.3
#: mechanism (`resolve_spec_dir`), the SAME resolution `pep.py` uses for the autonomy matrix.
#: Tests do NOT use it — they pass the `path` argument — so this stays a deployment surface only.
MANIFEST_PATH_ENV = "MAEZO_ACTION_APPROVALS_PATH"

#: The companion flag WITHOUT which an override-sourced manifest may never enforce. Set to the
#: exact literal "1" (the fail-closed pin idiom: truthy junk is refused). Its whole purpose is to
#: make "swap the manifest and turn enforcement on" TWO deliberate, separately auditable acts
#: instead of one env var — because the path override bypasses the CODEOWNERS-listed file entirely
#: and, with `main` unprotected, that listing is advisory anyway.
OVERRIDE_ENFORCEMENT_ENV = "MAEZO_ACTION_APPROVALS_ALLOW_OVERRIDE_ENFORCEMENT"
OVERRIDE_ENFORCEMENT_ENABLED = "1"

#: Runtime-mode discriminator, mirroring `runtime/agent_runtime/service.py:84`
#: (`_LOCAL_RUNTIME_MODE`) and `a2a_composition.py`'s identically-named one: anything OTHER than
#: the literal "local" (Helm injects "kubernetes") is PRODUCTION. Restated here rather than
#: imported for the same layering reason `_TOKEN_RE` is restated: `maezo.gateway` must not depend
#: on `maezo.runtime`. Disclosed residual: an unset variable reads as "local", so a production
#: deployment that FORGETS to inject it does not arm the §5.7 spec-dir pin — the same residual the
#: checkpointer fail-closed gate already carries, and the reason the pin is a defense-in-depth
#: layer rather than the primary control.
RUNTIME_MODE_ENV = "AGENT_RUNTIME_MODE"
#: BOTH names the SAME discriminator answers to, in `key_scrubber.py:117`'s order
#: (`RUNTIME_MODE or AGENT_RUNTIME_MODE`). Reading only one of the two was a real gap: a
#: deployment standardised on `RUNTIME_MODE` — which the PHI egress pseudonymizer already treats as
#: authoritative — left the §5.7 spec-dir pin DISARMED while believing it had declared production.
#: One deployment vocabulary, two spellings, both honoured.
#:
#: DELIBERATE DIVERGENCE from `key_scrubber`, in the tightening direction, twice over: (1) it
#: `.strip().lower()`s the value, so `" Local "` reads as local there and as PRODUCTION here —
#: guessing at a mistyped mode is exactly what the fail-closed pin idiom refuses; (2) it treats an
#: UNSET variable as production, which cannot be adopted here without turning every dev box and
#: every test run into a `shadow_override` (the residual documented above is the accepted cost).
RUNTIME_MODE_ENVS: Final[tuple[str, str]] = ("RUNTIME_MODE", RUNTIME_MODE_ENV)
LOCAL_RUNTIME_MODE = "local"

#: The three approver domains, CODE-FROZEN. `PLANS.md` §0.6 and DL-0042 both state the MZO-040
#: gate as Médica + ANS + Security; this set is not editable from the manifest, mirroring
#: `pep.HARD_ACTIONS` (code-frozen, cross-checked against data). A `dominios_exigidos` entry
#: outside this set makes its class UNAPPROVABLE rather than silently ignored.
APPROVER_DOMAINS: frozenset[str] = frozenset({"medica", "ans", "seguranca"})

#: The literal `status` value that permits any ALLOW at all. Anything else — DRAFT, absent,
#: mistyped, a non-string — means nothing below it can be approved, whatever the blocks say.
STATUS_RATIFIED = "RATIFICADO"

#: Machine-detectable placeholder. An accountability field left as this literal (or blank, or
#: null, or a non-string) is NOT filled, even alongside `aprovado: true` — a partial fill reads
#: as an unfinished edit, never as a ratification (the `auth_criteria` rule, same words).
PENDING_PLACEHOLDER = "PENDENTE"

#: Fields every approval block must carry, all non-blank and none equal to `PENDENTE`.
_APPROVED_FLAG = "aprovado"
_ACCOUNTABILITY_FIELDS: tuple[str, str, str] = ("aprovador", "data", "evidencia_ref")

MODE_SHADOW = "shadow"
MODE_ENFORCING = "enforcing"
#: The mode of a manifest that could not be loaded or whose `modo` is not one of the two literals.
#: Never enforces (a broken file must not silently switch enforcement ON) and never allows.
MODE_UNRESOLVED = "unresolved"
#: The mode an ENFORCING manifest resolves to when it was sourced from `MANIFEST_PATH_ENV` and the
#: `OVERRIDE_ENFORCEMENT_ENV` companion flag is absent. Evaluation still runs (so the override
#: keeps its legitimate use — previewing what a candidate record WOULD decide), but `enforced` is
#: False, so a runtime env var alone can never start blocking calls.
#:
#: ONDA 1 (§5.7, A-6): the SAME resolution now also covers a manifest reached through a
#: `MAEZO_SPEC_DIR` override while the runtime mode is production. See `_parse`.
MODE_SHADOW_OVERRIDE = "shadow_override"

# -- ONDA 1 §7.3: per-class enforcement. `modo` above stays the GLOBAL CEILING; these two data
# fields add a second, per-class dimension so a flip can be PROGRESSIVE (C0 -> C4, design §9.4)
# instead of all-or-nothing. Both are additive and both default to the safe direction.
ENFORCEMENT_SHADOW = "shadow"
ENFORCEMENT_ENFORCING = "enforcing"
#: Per-class key under `acoes.<class>`. Absent, mistyped, or non-string => `shadow`.
CLASS_ENFORCEMENT_FIELD = "enforcement"
#: Root key applied to any action ref that resolves to NO class (an unmapped topic, an
#: uncatalogued agent operation).
#:
#: THE TWO CASES ARE DELIBERATELY DIFFERENT, and the difference is the whole governance point:
#:   * ABSENT root key => `enforcing`. XRD-09's literal is "ação/política desconhecida … negam"
#:     (`0037-…:154`). Nobody wrote the Q-2 deviation down, so the ratified default applies. A
#:     manifest that simply never heard of this key cannot silently inherit the ramp's exception.
#:   * PRESENT but mistyped / non-string => `shadow`. Someone TRIED to write a value and the
#:     loader cannot tell which one they meant; guessing `enforcing` off a typo would block a
#:     platform on a stray capital. The fail-closed pin idiom, in the only direction that is
#:     fail-closed HERE: a wrong guess turns denials into outages, not permissions.
#: `shadow` during the ramp therefore has to be WRITTEN DOWN, explicitly, as the shipped manifest
#: does — which is exactly the time-boxed deviation the design puts to the humans as Q-2, and
#: which the terminal act of the rollout (§9.4 step 7) deletes or flips back to `enforcing`.
DEFAULT_ENFORCEMENT_KEY = "enforcement_padrao_nao_mapeado"
#: New manifest section (design §7.1): agent-side `action_ref` -> class, sibling of
#: `mapeamento_topicos`, which stays byte-unchanged. Merged into ONE map at load; a key present in
#: BOTH refuses the whole manifest (a governance record may not be silently shadowed).
ACTION_MAP_KEY = "mapeamento_acoes"
TOPIC_MAP_KEY = "mapeamento_topicos"

# -- Reason vocabulary: a CLOSED enum of bounded, non-PHI tokens (design mirror of the
# `_ENUM_TOKEN_RE` discipline in `tools/workers/harness.py`). Every one is safe in the clear.
REASON_APPROVED = "APROVADO"
REASON_MANIFEST_UNAVAILABLE = "MANIFESTO_INDISPONIVEL"
REASON_MANIFEST_DRAFT = "MANIFESTO_NAO_RATIFICADO"
REASON_ACTION_UNMAPPED = "ACAO_NAO_MAPEADA"
REASON_ACTION_UNDECLARED = "ACAO_NAO_DECLARADA"
REASON_DOMAINS_EMPTY = "DOMINIOS_EXIGIDOS_VAZIO"
REASON_DOMAIN_UNKNOWN = "DOMINIO_DESCONHECIDO"
#: `dominios_exigidos` is a subset of the code-frozen set rather than the whole of it — vocabulary
#: is right, CARDINALITY is wrong. `[medica]`, or `[medica, medica, medica]`, is not three
#: attestations; without this the class would be approved by one domain's signature.
REASON_DOMAINS_INCOMPLETE = "DOMINIOS_INCOMPLETOS"
REASON_APPROVAL_PENDING = "APROVACAO_PENDENTE"
REASON_APPROVAL_INCOMPLETE = "APROVACAO_INCOMPLETA"
REASON_INTERNAL_ERROR = "GATEWAY_ERRO_INTERNO"
#: Carried INSTEAD OF `APROVADO` by an ALLOW read out of an override-sourced manifest whose
#: enforcement was refused. The verdict is still "would allow", but the operator must not read it
#: as a live enforcement decision — that is exactly the confusion this token exists to prevent.
REASON_OVERRIDE_NOT_ENFORCEABLE = "OVERRIDE_NAO_ENFORCAVEL"

#: Same shape as `harness._ENUM_TOKEN_RE`, restated locally rather than imported: `maezo.gateway`
#: must not depend on `maezo.tools` (the dependency runs the other way — the harness imports
#: `gateway.audit`). Duplicating one regex keeps the layering clean.
#:
#: `\Z`, NOT `$` (ONDA 1 GK nit, fail-closed tightening). Python's `$` also matches immediately
#: BEFORE a trailing newline, so `"APROVADO\n"` passed `_is_bounded_token` and could have carried
#: a line break into a structured log line — a log-injection primitive, and a value that is not
#: the bounded token it claims to be. `\Z` anchors at the true end of the string. Strictly
#: narrowing: every value that matched before still matches, minus the trailing-newline forms.
_TOKEN_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,39}\Z")


def _is_bounded_token(value: Any) -> bool:
    """True iff `value` is a bounded, non-PHI token safe to emit in the clear."""
    return isinstance(value, str) and bool(_TOKEN_RE.match(value))


@dataclass(frozen=True)
class Decision:
    """The gateway's verdict for one call.

    Attributes:
        action_class: The resolved action class, or None when the call could not be classified
            (an unmapped topic). Never PHI — a class name from the manifest.
        allow: The ENFORCEMENT-TERMS verdict. False means "this call would be blocked". It is
            False for every class today, because nothing is approved.
        reason: One bounded token from the closed `REASON_*` enum above. Never free text.
        mode: `shadow` | `enforcing` | `unresolved` | `shadow_override`, as resolved from the
            manifest AND from how the manifest was sourced.
        enforcement: the PER-CLASS enforcement dimension added by Onda 1 §7.3 — `shadow` |
            `enforcing`. The LOADER resolves it from the manifest, where ABSENT means `shadow`
            (`ActionApprovals.enforcement_for`); the dataclass default here means "no per-class
            restriction was resolved for this Decision", so a `Decision` built directly in code
            keeps `enforced == (mode == enforcing)` exactly as before. Every production
            construction site passes it explicitly (pinned by a fence test) — the governance
            property lives in the loader, which is where §7.3 puts it.
    """

    action_class: str | None
    allow: bool
    reason: str
    mode: str
    enforcement: str = ENFORCEMENT_ENFORCING

    @property
    def enforced(self) -> bool:
        """True iff a DENY must actually block.

        False in shadow, on an unresolved manifest, and on `shadow_override` — the ONE literal
        that grants enforcement is `enforcing`, and only the loader can resolve to it.

        ONDA 1 §7.3: enforcement is now the CONJUNCTION of the global ceiling and the per-class
        field. A global `shadow` still means NOTHING enforces (the safe direction), and a typo in
        either field still resolves to a non-enforcing state.
        """
        return self.mode == MODE_ENFORCING and self.enforcement == ENFORCEMENT_ENFORCING

    @property
    def telemetry_decision(self) -> str:
        """The bounded token a shadow log line carries: WOULD_ALLOW | WOULD_DENY."""
        return "WOULD_ALLOW" if self.allow else "WOULD_DENY"


@dataclass(frozen=True)
class ActionApprovals:
    """An immutable, already-validated view of the approval manifest.

    Attributes:
        mode: `shadow` | `enforcing` | `unresolved` | `shadow_override`.
        approved: action classes whose EVERY required domain carries a complete approval block.
            EMPTY whenever `status` is not exactly `RATIFICADO`, and empty on any load failure.
        declared: action classes declared in `acoes`, approved or not. Used to tell
            `ACAO_NAO_DECLARADA` (nobody classified this) apart from `APROVACAO_PENDENTE`
            (classified, awaiting a human) — an approver needs that distinction.
        topic_to_class: external-task topic -> action class. Human-declared routing, never
            guessed in code; an unmapped topic fails closed.
        denial_reasons: declared-but-unapproved class -> the PRECISE bounded reason it is denied
            (`MANIFESTO_NAO_RATIFICADO` when the whole file is DRAFT; otherwise
            `APROVACAO_INCOMPLETA` / `DOMINIOS_EXIGIDOS_VAZIO` / `DOMINIO_DESCONHECIDO` /
            `DOMINIOS_INCOMPLETOS` / `APROVACAO_PENDENTE`). Computed once at load; this is what an
            approver reads in the shadow telemetry, so "nobody signed yet" never looks like "the
            record is broken".
        degraded: True when the manifest could not be loaded/parsed at all.
        action_ref_to_class: ONDA 1 §7.1 — agent-side `action_ref` (`agente.<operation>`) ->
            action class, from the new `mapeamento_acoes` section. Kept SEPARATE from
            `topic_to_class` so the two namespaces stay legible to an approver; `classify` reads
            both, and the loader refuses the whole manifest if a key appears in both.
        class_enforcement: ONDA 1 §7.3 — declared class -> `shadow` | `enforcing`. Every declared
            class has an entry; absent/mistyped in the data resolves to `shadow`.
        default_enforcement: ONDA 1 §7.3 — the value applied to a ref that resolves to NO class.
            The dataclass default is `shadow` because it describes a view built WITHOUT parsing a
            manifest (`_EMPTY_APPROVALS`, the `effect_pep` degraded view), which always carries
            `mode=unresolved` and therefore cannot enforce in either direction. The PARSED default
            is resolved in `_parse` and follows :data:`DEFAULT_ENFORCEMENT_KEY`'s absent-vs-typo
            rule instead.
        artifact_digests: ONDA 1 §5.7 — artifact name -> sha256 (lowercase hex) of the policy
            files this view was resolved against, or `AUSENTE` for one that is not on disk. Lets
            an operator compare DEPLOYED policy against the reviewed commit.
        policy_digest: sha256 over the sorted `artifact_digests` pairs — one value to compare.
    """

    mode: str
    approved: frozenset[str]
    declared: frozenset[str]
    topic_to_class: Mapping[str, str]
    denial_reasons: Mapping[str, str] = MappingProxyType({})
    degraded: bool = False
    action_ref_to_class: Mapping[str, str] = MappingProxyType({})
    class_enforcement: Mapping[str, str] = MappingProxyType({})
    default_enforcement: str = ENFORCEMENT_SHADOW
    artifact_digests: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))
    policy_digest: str = ""

    def classify(self, topic: Any) -> str | None:
        """The action class declared for a topic OR an agent action ref, or None (fail closed).

        None means `ACAO_NAO_MAPEADA`. The two namespaces are collision-refused at load, so the
        lookup order cannot change any answer.
        """
        if not isinstance(topic, str) or not topic.strip():
            return None
        ref = topic.strip()
        mapped = self.topic_to_class.get(ref)
        return mapped if mapped is not None else self.action_ref_to_class.get(ref)

    def enforcement_for(self, action_class: Any) -> str:
        """The per-class enforcement value for `action_class` (§7.3). Never guesses.

        `None`, a non-string, an unknown class and an unmapped ref all resolve to
        `default_enforcement` (the manifest's `enforcement_padrao_nao_mapeado` — `enforcing` when
        the root key is ABSENT, per XRD-09's literal, and `shadow` only when a human wrote that
        deviation down; see :data:`DEFAULT_ENFORCEMENT_KEY`). A declared class with no
        `enforcement` key resolved to `shadow` at load, so it is present in `class_enforcement`
        and answers `shadow` here.

        FAIL-CLOSED is still the net posture: `default_enforcement` only ever controls whether a
        DENY BLOCKS. It can never turn a DENY into an ALLOW, and the global `modo` ceiling
        (`shadow` in the shipped record) still gates it.
        """
        if not isinstance(action_class, str) or not action_class.strip():
            return self.default_enforcement
        return self.class_enforcement.get(action_class.strip(), self.default_enforcement)


#: The value every failure mode resolves to: nothing approved, nothing declared, nothing mapped,
#: and a mode that neither enforces nor allows.
_EMPTY_APPROVALS = ActionApprovals(
    mode=MODE_UNRESOLVED,
    approved=frozenset(),
    declared=frozenset(),
    topic_to_class=MappingProxyType({}),
    degraded=True,
)


def _refuse(reason: str, detail: str) -> ActionApprovals:
    """Log the refusal and return the empty (fail-closed) approvals. NEVER raises."""
    logger.error("action_approvals_manifest_unavailable", reason=reason, detail=detail)
    return _EMPTY_APPROVALS


def _is_filled(value: Any) -> bool:
    """True iff an accountability field is genuinely filled (non-blank and not the placeholder)."""
    return isinstance(value, str) and bool(value.strip()) and value.strip() != PENDING_PLACEHOLDER


def _block_is_approved(action_class: str, domain: str, block: Any) -> bool:
    """True iff one (class, domain) block carries a complete, accountable approval.

    Requires ALL of: `aprovado is True` (the boolean literal — a string `"true"`, `1`, or any
    other truthy value is REFUSED, the repo's fail-closed pin idiom), plus `aprovador`, `data`
    and `evidencia_ref` each non-blank and not the `PENDENTE` placeholder.
    """
    if not isinstance(block, dict):
        logger.warning("action_approval_block_malformed", action_class=action_class, domain=domain)
        return False
    if block.get(_APPROVED_FLAG) is not True:
        return False
    missing = [f for f in _ACCOUNTABILITY_FIELDS if not _is_filled(block.get(f))]
    if missing:
        logger.warning(
            "action_approval_incomplete",
            action_class=action_class,
            domain=domain,
            missing_fields=missing,
            detail="aprovado=true but the accountability fields are absent/blank/PENDENTE — NOT approved",
        )
        return False
    return True


def _class_is_approved(action_class: str, entry: Any) -> bool:
    """True iff EVERY domain in the class's `dominios_exigidos` is approved. FAIL-CLOSED."""
    if not isinstance(entry, dict):
        return False
    required = entry.get("dominios_exigidos")
    if not isinstance(required, list) or not required:
        # An empty/absent requirement list is NOT "no requirements" — it is an unusable record.
        # Closing this is what makes "approve by emptying the list" unreachable.
        return False
    if any(not isinstance(d, str) or d not in APPROVER_DOMAINS for d in required):
        logger.warning("action_approval_domain_unknown", action_class=action_class, required=required)
        return False
    # CARDINALITY, not just vocabulary. Every entry being a KNOWN domain is not the same as every
    # REQUIRED domain being present: `dominios_exigidos: [medica]` would have approved the class on
    # one signature, and `[medica, medica, medica]` would have looked like three. The gate is
    # Médica + ANS + Security (`PLANS.md` §0.6, DL-0042) — set equality is what states that. A
    # no-op for the shipped file, which declares all three for every class (and the fence test
    # `test_every_class_requires_all_three_approver_domains` already asserts it).
    if set(required) != APPROVER_DOMAINS:
        logger.warning(
            "action_approval_domains_incomplete",
            action_class=action_class,
            required=sorted({d for d in required if isinstance(d, str)}),
            detail="`dominios_exigidos` must be exactly the three code-frozen domains — a subset "
            "(or a repeat of one) is not coverage",
        )
        return False
    blocks = entry.get("aprovacoes")
    if not isinstance(blocks, dict):
        return False
    return all(_block_is_approved(action_class, domain, blocks.get(domain)) for domain in required)


def _class_denial_reason(action_class: str, entry: Any) -> str:
    """The precise DENY reason for a declared-but-unapproved class (telemetry only)."""
    if not isinstance(entry, dict):
        return REASON_APPROVAL_PENDING
    required = entry.get("dominios_exigidos")
    if not isinstance(required, list) or not required:
        return REASON_DOMAINS_EMPTY
    if any(not isinstance(d, str) or d not in APPROVER_DOMAINS for d in required):
        return REASON_DOMAIN_UNKNOWN
    if set(required) != APPROVER_DOMAINS:
        return REASON_DOMAINS_INCOMPLETE
    blocks = entry.get("aprovacoes") if isinstance(entry.get("aprovacoes"), dict) else {}
    for domain in required:
        block = blocks.get(domain) if isinstance(blocks, dict) else None
        if isinstance(block, dict) and block.get(_APPROVED_FLAG) is True:
            # The flag is flipped but the record is not accountable — a distinct, louder state
            # than "nobody has approved yet", and the one an auditor most needs to see.
            return REASON_APPROVAL_INCOMPLETE
    return REASON_APPROVAL_PENDING


def _manifest_default_path() -> str:
    """`<spec>/policies/autonomy/action-approvals.yaml` via the single T0.3 mechanism."""
    return str(resolve_spec_dir() / "policies" / "autonomy" / "action-approvals.yaml")


class DuplicateManifestKeyError(yaml.YAMLError):
    """A mapping key is declared twice in the manifest."""


#: The tag PyYAML's resolver gives a plain `<<` key (`Resolver.add_implicit_resolver`), tested
#: below as a SUBSTRING of `exc.problem` to tell a merge-related `ConstructorError` apart from any
#: other unsupported-tag one (see `_RefusingDuplicatesLoader`'s docstring and `_parse`'s dedicated
#: catch) — narrowed by tag substring, not proven exclusive to a genuine `<<` key.
_MERGE_KEY_TAG: Final[str] = "tag:yaml.org,2002:merge"


class _RefusingDuplicatesLoader(yaml.SafeLoader):
    """`SafeLoader` that REFUSES duplicate mapping keys instead of silently last-one-wins.

    YAML's default is that the LAST duplicate wins, so a manifest carrying `status: DRAFT` …
    `status: RATIFICADO`, or a second `aprovacoes:` block further down, would parse cleanly and
    read as ratified while the reviewed diff still shows the honest first value. For a record whose
    whole security property is "what the reviewer saw is what the loader sees", silent shadowing is
    unacceptable: refuse instead.

    SCOPE: the same CLASS of finding applies to the `auth_criteria` ratification manifest
    (`yaml.safe_load` there is still last-one-wins), but that loader is deliberately NOT changed
    here — widening this is its own decision, on its own review. This class is private to this
    module for exactly that reason.

    SIDE EFFECT (W5 GK finding, MINOR): a YAML merge key (`<<: *anchor`) is REFUSED too, though not
    on purpose and not for the duplicate-key reason. `construct_mapping` below walks `node.value`
    and calls `construct_object` on every raw key node BEFORE `SafeConstructor.construct_mapping`
    gets to run its own `flatten_mapping` step, which is what normally resolves `<<` — so
    `construct_object` reaches the merge key's OWN node first and finds no registered constructor
    for its `tag:yaml.org,2002:merge` tag. The document is well-formed YAML, not malformed, so
    `_parse` gives this its own `merge_key_unsupported` reason (never the generic `invalid_yaml`)
    — an honest message for a future ratifier reaching for an anchor. Fail-closed either way:
    refusing (rather than teaching this loader to special-case `<<`) keeps the duplicate-key guard
    simple and keeps a manifest author from combining the two features in a way nobody has
    reviewed the interaction of.

    DISCRIMINATOR PRECISION (V3 GK REVISE): `_parse`'s catch tells this case apart from any other
    unsupported-tag `ConstructorError` by a SUBSTRING test on `exc.problem` (`_MERGE_KEY_TAG in
    exc.problem`) — narrowed by tag substring, not proven exclusive to `<<`. A value explicitly
    tagged `!!merge` (never used as a `<<` key) carries the identical tag and so ALSO reports
    `merge_key_unsupported`. Fail-closed identical in both cases, so nothing observable regresses
    — a documentation-precision gap (the "distinguishes a merge key" framing overstated it), not a
    behavioral one; pinned by a dedicated collision test in test_action_execution_gateway.py.
    """

    def construct_mapping(self, node: MappingNode, deep: bool = False) -> dict[Any, Any]:
        seen: set[Any] = set()
        for key_node, _value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            try:
                duplicated = key in seen
            except TypeError:  # unhashable key — the base constructor rejects it below
                continue
            if duplicated:
                raise DuplicateManifestKeyError(
                    f"duplicate key {key!r} at line {key_node.start_mark.line + 1} — YAML would "
                    "silently keep the LAST one; a governance record may not be shadowed"
                )
            seen.add(key)
        return super().construct_mapping(node, deep=deep)


def _override_enforcement_permitted() -> bool:
    """True iff the operator explicitly authorised an OVERRIDE-sourced manifest to enforce."""
    return os.environ.get(OVERRIDE_ENFORCEMENT_ENV) == OVERRIDE_ENFORCEMENT_ENABLED


def _is_production_runtime() -> bool:
    """True iff this process is NOT in local mode — the `_LOCAL_RUNTIME_MODE` discriminator.

    Reads BOTH spellings (:data:`RUNTIME_MODE_ENVS`), first-set-wins, in `key_scrubber.py:117`'s
    order: a deployment that declared production under either name arms the §5.7 pin. Neither set
    still reads as local, which is the disclosed residual of :data:`RUNTIME_MODE_ENV`.
    """
    for name in RUNTIME_MODE_ENVS:
        raw = os.environ.get(name)
        if raw:
            return raw != LOCAL_RUNTIME_MODE
    return False


def _resolve_enforcement(raw: Any) -> str:
    """`enforcing` iff `raw` is EXACTLY that literal; everything else is `shadow` (§7.3).

    The same fail-closed pin idiom as `modo`: a trailing space, a capital, a bool, a None and an
    absent key all resolve to the non-enforcing value. There is no third state — unlike `modo`,
    which needs `unresolved` to keep a broken FILE from claiming a mode, a per-class typo has a
    safe default that is also the ramp's starting point.
    """
    return ENFORCEMENT_ENFORCING if raw == ENFORCEMENT_ENFORCING else ENFORCEMENT_SHADOW


def _string_map(raw: Any) -> dict[str, str]:
    """Normalise a manifest mapping section into `{stripped str: stripped str}`, dropping junk.

    LEGACY, and deliberately left alone: `mapeamento_topicos` has shipped with this
    silently-dropping comprehension since MZO-040, and its 26 entries are byte-unchanged. The NEW
    `mapeamento_acoes` section is held to the stricter contract in :func:`_malformed_map_entries`
    instead — see the ASYMMETRY note at its call site in `_parse`.
    """
    if not isinstance(raw, dict):
        return {}
    return {
        str(key).strip(): str(value).strip()
        for key, value in raw.items()
        if isinstance(key, str) and key.strip() and isinstance(value, str) and value.strip()
    }


def _describe_key(key: Any) -> str:
    """A bounded, log-safe rendering of a manifest mapping key (never free text of any length)."""
    return key.strip()[:60] if isinstance(key, str) else f"<{type(key).__name__}>"


def _malformed_map_entries(raw: Mapping[Any, Any]) -> list[str]:
    """The keys of `raw` whose key OR value is not a non-blank string. Refusal input, not a filter.

    A `mapeamento_acoes` entry whose VALUE is a dict, a list, `null` or an int is not a class name
    — it is an unfinished or garbled edit of a governance record. Dropping it silently (what
    :func:`_string_map` does) would leave the ref UNMAPPED, which under `modo: enforcing` and the
    XRD-09 root default means it BLOCKS while the reviewed diff shows a routing line that reads
    fine. Same posture as the collision case immediately below it in `_parse`: refuse the WHOLE
    record rather than honour part of a map.
    """
    return sorted(
        _describe_key(key)
        for key, value in raw.items()
        if not isinstance(key, str) or not key.strip() or not isinstance(value, str) or not value.strip()
    )


#: The policy artefacts whose content digest is recorded at load (design §5.7 item 2). Resolved as
#: siblings of the manifest, which is where the shipped layout puts them
#: (`spec/policies/autonomy/`). A file that is not on disk records `AUSENTE` rather than aborting:
#: the digest is an OPERATOR-VISIBILITY artefact, and `pep.build_pep` is already the fail-closed
#: gate for a missing `L0-core.yaml`.
_DIGESTED_SIBLINGS: Final[tuple[str, ...]] = ("L0-core.yaml", "_hard_frozen.yaml")
_DIGEST_ABSENT: Final[str] = "AUSENTE"


def _sha256_of(path: Path) -> str:
    """sha256 (lowercase hex) of a file, or `AUSENTE`/`ILEGIVEL`. NEVER raises.

    Same shape as `platform/integrations/amh_inbox.migration_digest` (`:329-337`), the in-repo
    precedent for pinning a governed artefact by content rather than by trust.
    """
    try:
        if not path.is_file():
            return _DIGEST_ABSENT
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return "ILEGIVEL"


def policy_artifact_digests(manifest_path: Path) -> Mapping[str, str]:
    """sha256 of every effect-policy artefact resolved beside `manifest_path`. NEVER raises.

    Covers the manifest itself, `L0-core.yaml`, `_hard_frozen.yaml` and every `tenants-*.yaml`
    overlay found in the same directory (the overlay set is data, so it is enumerated rather than
    hard-coded). Keys are file names — bounded, non-PHI, and directly comparable to a git tree.
    """
    digests: dict[str, str] = {manifest_path.name: _sha256_of(manifest_path)}
    directory = manifest_path.parent
    for sibling in _DIGESTED_SIBLINGS:
        digests[sibling] = _sha256_of(directory / sibling)
    try:
        overlays = sorted(p.name for p in directory.glob("tenants-*.yaml"))
    except OSError:
        overlays = []
    for name in overlays:
        digests[name] = _sha256_of(directory / name)
    return MappingProxyType(digests)


def combined_policy_digest(digests: Mapping[str, str]) -> str:
    """One sha256 over the sorted `(name, digest)` pairs — the value an operator compares."""
    joined = "\n".join(f"{name}={digests[name]}" for name in sorted(digests))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def _parse(
    raw_text: str,
    manifest_path: Path,
    *,
    override_sourced: bool = False,
    spec_dir_sourced: bool = False,
) -> ActionApprovals:
    """Parse an already-read manifest. NEVER raises; refuses into `_EMPTY_APPROVALS` instead.

    Args:
        raw_text: the manifest bytes, already decoded.
        manifest_path: where they came from (telemetry + digest resolution).
        override_sourced: the path came from `MANIFEST_PATH_ENV` (the existing override fence).
        spec_dir_sourced: the DEFAULT path was resolved through a `MAEZO_SPEC_DIR` override
            (design §5.7 / A-6). Treated exactly like `override_sourced` for the enforcement leg.
    """
    try:
        data = yaml.load(raw_text, Loader=_RefusingDuplicatesLoader)  # noqa: S506 - hardened SafeLoader
    except DuplicateManifestKeyError as exc:
        return _refuse("duplicate_key", f"{manifest_path}: {exc}")
    except yaml.constructor.ConstructorError as exc:
        if exc.problem is not None and _MERGE_KEY_TAG in exc.problem:
            # See `_RefusingDuplicatesLoader`'s docstring (W5 GK finding, MINOR): a `<<` merge key
            # trips the SAME "no constructor for this tag" error PyYAML raises for a genuinely
            # unsupported tag, but the document itself is well-formed — so this gets its own
            # honest reason instead of the generic `invalid_yaml` a ratifier would have to
            # puzzle over (there is no syntax error to find).
            return _refuse(
                "merge_key_unsupported",
                f"{manifest_path}: YAML merge keys ('<<: *anchor') are not supported by the "
                f"duplicate-key-refusing manifest loader — inline the values instead: {exc}",
            )
        return _refuse("invalid_yaml", f"malformed YAML in {manifest_path}: {exc}")
    except yaml.YAMLError as exc:
        return _refuse("invalid_yaml", f"malformed YAML in {manifest_path}: {exc}")

    if not isinstance(data, dict):
        return _refuse(
            "invalid_schema", f"{manifest_path}: root must be a mapping, got {type(data).__name__}"
        )

    # Template guard, mirrored from `load_retention_matrix`: a copy marked `unratified: true` is
    # refused WHOLESALE, so pointing the path at a placeholder by mistake also fails closed.
    if data.get("unratified") is True:
        return _refuse(
            "unratified_template",
            f"{manifest_path} is marked 'unratified: true' (placeholder template) — a "
            "human-ratified manifest must replace it; approving nothing",
        )

    raw_mode = data.get("modo")
    mode = raw_mode if raw_mode in (MODE_SHADOW, MODE_ENFORCING) else MODE_UNRESOLVED
    if mode is MODE_UNRESOLVED:
        logger.error(
            "action_approvals_mode_unresolved",
            path=str(manifest_path),
            detail="'modo' must be exactly 'shadow' or 'enforcing' — refusing to guess; nothing enforces",
        )
    # THE OVERRIDE FENCE. A manifest reached through `MANIFEST_PATH_ENV` never passed the reviewer
    # the shipped file's CODEOWNERS listing asks for, so it may not switch enforcement on by
    # itself. Evaluation still runs (that is the override's legitimate use — previewing a candidate
    # record against real traffic); only the ENFORCEMENT leg is withheld, and only a second,
    # explicit env act restores it. Deliberately narrow: an override in `shadow` is untouched.
    if override_sourced and mode == MODE_ENFORCING and not _override_enforcement_permitted():
        mode = MODE_SHADOW_OVERRIDE
        logger.error(
            "action_approvals_override_enforcement_refused",
            path=str(manifest_path),
            override_env=MANIFEST_PATH_ENV,
            companion_env=OVERRIDE_ENFORCEMENT_ENV,
            detail="manifest came from the path override and declares 'enforcing'; enforcement is "
            f"WITHHELD (mode={MODE_SHADOW_OVERRIDE}) until {OVERRIDE_ENFORCEMENT_ENV}=1 is also set",
        )
    # THE SPEC-DIR FENCE (ONDA 1 §5.7, closes adversary A-6). `MAEZO_SPEC_DIR` swaps
    # `action-approvals.yaml`, `L0-core.yaml`, `_hard_frozen.yaml` AND every `agent.yaml` in ONE
    # variable, and `_manifest_default_path()` honours it with `override_sourced=False` — so the
    # existing path-override fence never fires and a forged `RATIFICADO` + `enforcing` manifest
    # would ALLOW every class. Inert today (nothing enforces), a single-variable total
    # authorization bypass the instant the agent side enforces.
    #
    # PROVISIONAL, pending Q-6. This implements the WEAKER of the design's two options: mirror
    # `MODE_SHADOW_OVERRIDE` (evaluate, but never enforce), not refuse-to-load. It is chosen
    # provisionally BECAUSE the global `modo: shadow` makes it a no-op today, so shipping the
    # weaker form costs nothing and breaks no deployment; Q-6 may STRENGTHEN it to refuse-to-load,
    # which is a security decision with a deployment blast radius and belongs to the owner. The
    # same companion flag governs the escape hatch, deliberately: one staged-rollout act, not two
    # vocabularies. Narrow by construction — only PRODUCTION mode, only a manifest that already
    # reads `enforcing`; a spec-dir override in shadow is untouched, which is every test run.
    if spec_dir_sourced and mode == MODE_ENFORCING and not _override_enforcement_permitted():
        mode = MODE_SHADOW_OVERRIDE
        logger.error(
            "action_approvals_spec_dir_enforcement_refused",
            path=str(manifest_path),
            override_env=MAEZO_SPEC_DIR_ENV,
            companion_env=OVERRIDE_ENFORCEMENT_ENV,
            enforcement_permitted=False,
            detail="the policy plane was resolved through the spec-dir override in a PRODUCTION "
            f"runtime and declares 'enforcing'; enforcement is WITHHELD (mode={MODE_SHADOW_OVERRIDE}) "
            f"until {OVERRIDE_ENFORCEMENT_ENV}=1 is also set — see design §5.7 / Q-6",
        )

    raw_acoes = data.get("acoes")
    if raw_acoes is None:
        raw_acoes = {}
    if not isinstance(raw_acoes, dict):
        return _refuse("invalid_schema", f"{manifest_path}: 'acoes' must be a mapping")

    declared = frozenset(k for k in raw_acoes if isinstance(k, str) and k.strip())

    # THE OUTER FENCE. A file whose `status` is not exactly `RATIFICADO` approves NOTHING, no
    # matter what the blocks below it say — this is what makes a forged DRAFT (every `aprovado`
    # flipped to true) incapable of ever producing an ALLOW, in any mode.
    status = data.get("status")
    if status != STATUS_RATIFIED:
        approved: frozenset[str] = frozenset()
        # THE forgery shape this fence exists for: every declared class denies with the reason
        # that names the real problem, even if every `aprovado` below reads `true`.
        denial_reasons = dict.fromkeys(declared, REASON_MANIFEST_DRAFT)
        logger.info(
            "action_approvals_manifest_not_ratified",
            path=str(manifest_path),
            status=status if _is_bounded_token(status) else "INVALIDO",
            mode=mode,
            declared_count=len(declared),
            detail=f"status is not '{STATUS_RATIFIED}' — every action class denies",
        )
    else:
        approved = frozenset(name for name in declared if _class_is_approved(name, raw_acoes.get(name)))
        denial_reasons = {
            name: _class_denial_reason(name, raw_acoes.get(name)) for name in declared if name not in approved
        }

    raw_map = data.get(TOPIC_MAP_KEY)
    if raw_map is None:
        raw_map = {}
    if not isinstance(raw_map, dict):
        return _refuse("invalid_schema", f"{manifest_path}: '{TOPIC_MAP_KEY}' must be a mapping")
    topic_map = _string_map(raw_map)

    # ONDA 1 §7.1 — the agent-side sibling. `mapeamento_topicos` above stays byte-unchanged.
    raw_action_map = data.get(ACTION_MAP_KEY)
    if raw_action_map is None:
        raw_action_map = {}
    if not isinstance(raw_action_map, dict):
        return _refuse("invalid_schema", f"{manifest_path}: '{ACTION_MAP_KEY}' must be a mapping")
    # ASYMMETRY, deliberate and disclosed: the NEW section refuses a malformed ENTRY; the legacy
    # `mapeamento_topicos` above keeps `_string_map`'s silently-dropping comprehension. Tightening
    # the legacy section is a behaviour change to a record 26 human-reviewed lines long and
    # belongs to its own review, not to this one — `mapeamento_topicos` is byte-unchanged by the
    # brief's hard limit. New data, new contract; the old data keeps the contract it shipped with.
    malformed = _malformed_map_entries(raw_action_map)
    if malformed:
        return _refuse(
            "invalid_action_map_entry",
            f"{manifest_path}: {len(malformed)} entry/entries in '{ACTION_MAP_KEY}' do not map a "
            f"non-blank string ref to a non-blank string class — {malformed[:5]}; a garbled "
            "routing line must not be silently DROPPED into 'unmapped'",
        )
    action_map = _string_map(raw_action_map)

    # COLLISION REFUSAL, the `_RefusingDuplicatesLoader` posture applied ACROSS the two sections
    # rather than within one mapping: YAML's duplicate guard cannot see a ref declared once in
    # each map, and "last section wins" would let a second declaration silently re-route a class
    # that a reviewer signed under the first. Refuse the WHOLE manifest — a governance record may
    # not be shadowed, and a partially-honoured map is worse than no map.
    collisions = sorted(set(topic_map) & set(action_map))
    if collisions:
        return _refuse(
            "mapping_collision",
            f"{manifest_path}: {len(collisions)} action ref(s) declared in BOTH '{TOPIC_MAP_KEY}' "
            f"and '{ACTION_MAP_KEY}' — {collisions[:5]}; one ref, one class, one reviewed line",
        )

    # ONDA 1 §7.3 — per-class enforcement. EVERY declared class gets an entry so `enforcement_for`
    # never has to guess; absent/mistyped in the data resolved to `shadow` right here.
    class_enforcement = {
        name: _resolve_enforcement(
            raw_acoes[name].get(CLASS_ENFORCEMENT_FIELD) if isinstance(raw_acoes.get(name), dict) else None
        )
        for name in declared
    }
    # ABSENT root key => the XRD-09 literal (`enforcing`); PRESENT-but-typo'd => `shadow`. See
    # `DEFAULT_ENFORCEMENT_KEY`'s comment for why those two are not the same case.
    default_enforcement = (
        ENFORCEMENT_ENFORCING
        if DEFAULT_ENFORCEMENT_KEY not in data
        else _resolve_enforcement(data[DEFAULT_ENFORCEMENT_KEY])
    )
    enforcing_classes = sorted(n for n, v in class_enforcement.items() if v == ENFORCEMENT_ENFORCING)

    digests = policy_artifact_digests(manifest_path)
    digest = combined_policy_digest(digests)

    logger.info(
        "action_approvals_manifest_loaded",
        path=str(manifest_path),
        mode=mode,
        declared_count=len(declared),
        approved_count=len(approved),
        approved=sorted(approved),
        topic_mappings=len(topic_map),
        action_ref_mappings=len(action_map),
        default_enforcement=default_enforcement,
        enforcing_classes=enforcing_classes,
        policy_digest=digest,
        artifact_digests=dict(digests),
    )
    return ActionApprovals(
        mode=mode,
        approved=approved,
        declared=declared,
        topic_to_class=MappingProxyType(topic_map),
        denial_reasons=MappingProxyType(denial_reasons),
        action_ref_to_class=MappingProxyType(action_map),
        class_enforcement=MappingProxyType(class_enforcement),
        default_enforcement=default_enforcement,
        artifact_digests=digests,
        policy_digest=digest,
    )


def load_action_approvals(path: str | Path | None = None) -> ActionApprovals:
    """Load the approval manifest. FAILS CLOSED — never raises, never defaults to approved.

    Path resolution: the explicit `path` argument > `MAEZO_ACTION_APPROVALS_PATH` >
    `<spec>/policies/autonomy/action-approvals.yaml` (via `resolve_spec_dir`).

    ONLY the middle one is an "override" for enforcement purposes. An explicit `path` is a
    composition-root/test seam chosen in code; the env var is a runtime string that no reviewer
    saw, so a manifest loaded from it cannot enforce without `OVERRIDE_ENFORCEMENT_ENV=1` as well,
    and its every load is logged at `error` level with the resolved path and that verdict.

    Every failure mode (unresolvable spec dir, missing file, non-file path, unreadable, non-UTF-8,
    malformed YAML, duplicate mapping key, wrong schema, `unratified: true` marker) returns an
    `ActionApprovals` with zero approved classes and `mode=unresolved` — which neither allows
    anything nor turns enforcement on — plus one `error` log line. See the module docstring for
    why this swallows where the retention-matrix precedent raises.
    """
    raw_path = path
    override_sourced = False
    spec_dir_sourced = False
    if raw_path is None:
        env_path = os.environ.get(MANIFEST_PATH_ENV)
        if env_path:
            raw_path = env_path
            override_sourced = True
            # EVERY override-sourced load says so, loudly, naming the file and whether it was
            # allowed to enforce — an operator swapping the governed record must leave a trace
            # that is legible without reading the manifest.
            logger.error(
                "action_approvals_manifest_path_overridden",
                path=env_path,
                override_env=MANIFEST_PATH_ENV,
                enforcement_permitted=_override_enforcement_permitted(),
                detail="the CODEOWNERS-listed manifest was NOT used; this path was supplied by the "
                "runtime environment",
            )
    if not raw_path:
        # ONDA 1 §5.7 / A-6: the DEFAULT path resolves through `resolve_spec_dir()`, which honours
        # `MAEZO_SPEC_DIR` as authoritative. That substitutes the ENTIRE policy plane in one
        # variable, invisibly to the `MANIFEST_PATH_ENV` fence.
        #
        # PROVENANCE AND ENFORCEMENT ARE TWO DIFFERENT QUESTIONS, and conflating them suppressed
        # the record: the earlier form emitted this line only when the pin ALSO bit (production
        # AND `modo: enforcing`), so a staging or mis-labelled pod running an entirely substituted
        # policy plane left no trace that the governed tree was bypassed. WHERE THE POLICY CAME
        # FROM is a fact worth recording on every load; WHETHER ENFORCEMENT IS WITHHELD is the
        # production-only consequence, and stays gated in `_parse`.
        spec_dir_override = bool(os.environ.get(MAEZO_SPEC_DIR_ENV))
        spec_dir_sourced = spec_dir_override and _is_production_runtime()
        try:
            raw_path = _manifest_default_path()
        except Exception as exc:  # noqa: BLE001 - fail-closed: unresolvable spec/ approves nothing
            return _refuse("path_unresolved", f"could not resolve the default manifest path: {exc}")
        if spec_dir_override:
            # `error` in production (an operator must be paged by their own log routing);
            # `info` otherwise, because every dev box and every test run sets this variable and
            # an `error` per load would be noise that teaches people to filter the line out.
            emit = logger.error if spec_dir_sourced else logger.info
            emit(
                "action_approvals_spec_dir_overridden",
                path=str(raw_path),
                override_env=MAEZO_SPEC_DIR_ENV,
                production_runtime=spec_dir_sourced,
                enforcement_permitted=_override_enforcement_permitted(),
                detail="the CODEOWNERS-listed policy plane was NOT used; the whole spec/ tree "
                "(action-approvals.yaml, L0-core.yaml, _hard_frozen.yaml, every agent.yaml) was "
                "supplied by the runtime environment",
            )

    manifest_path = Path(raw_path)
    if not manifest_path.is_file():
        return _refuse(
            "file_not_found", f"no readable manifest file at {manifest_path} — nothing is approved"
        )

    try:
        raw_text = manifest_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        # NOT covered by OSError (UnicodeDecodeError subclasses ValueError) — the same explicit
        # catch `legal_bases_matrix.py` documents for cp1252 files with accented characters.
        return _refuse("invalid_encoding", f"{manifest_path} is not valid UTF-8: {exc}")
    except OSError as exc:
        return _refuse("unreadable", f"could not read {manifest_path}: {exc}")

    return _parse(
        raw_text,
        manifest_path,
        override_sourced=override_sourced,
        spec_dir_sourced=spec_dir_sourced,
    )


@lru_cache(maxsize=8)
def _load_cached(resolved_path: str | None) -> ActionApprovals:
    return load_action_approvals(resolved_path)


def action_approvals(path: str | Path | None = None) -> ActionApprovals:
    """Cached `load_action_approvals` — the accessor the per-call chokepoint uses.

    The manifest is static governance config, so caching it avoids re-reading YAML on every
    external task (same reasoning, and the same restart-to-refresh trade-off, as
    `auth_criteria.criteria_sources` and `ceilings._load_matrix_cached`). A ratification — or a
    flip to `enforcing` — therefore takes effect on the next daemon restart. That is acceptable
    for an artifact whose whole point is a deliberate human act, and strictly fail-closed in the
    interim; it is also disclosed in the approval packet so nobody expects a live flip.
    """
    return _load_cached(str(path) if path is not None else None)


class ActionExecutionGateway:
    """The per-call chokepoint. Pure and total: `evaluate` never raises and never mutates.

    Construct with an explicit `approvals` in tests; the module-level `evaluate_worker_task`
    helper builds one from the cached manifest for the production call path.
    """

    def __init__(self, approvals: ActionApprovals) -> None:
        self._approvals = approvals

    @property
    def approvals(self) -> ActionApprovals:
        """The loaded manifest view backing this gateway."""
        return self._approvals

    @property
    def mode(self) -> str:
        """`shadow` | `enforcing` | `unresolved`."""
        return self._approvals.mode

    def classify(self, topic: Any) -> str | None:
        """The action class declared for an external-task `topic`, or None (=> fail closed)."""
        return self._approvals.classify(topic)

    def evaluate(self, action_class: str | None, context: Mapping[str, Any] | None = None) -> Decision:
        """Decide one call. DENIES unless `action_class` is fully approved in a RATIFIED manifest.

        Total function — every input, including None, a non-string, or an unknown class, maps to a
        `Decision` carrying a bounded reason token. It has no side effects and cannot raise, which
        is what lets the SHADOW wiring be provably non-behavioural.

        Args:
            action_class: The class to decide, normally from `classify(topic)`. None means the
                call could not be classified -> `ACAO_NAO_MAPEADA`.
            context: Reserved, non-PHI call context. Only `tenant` is read today, and only as a
                telemetry dimension — no field of `context` influences the verdict. XRD-09's
                consent/ceiling/audit composition is NOT implemented here and is named as deferred
                in `docs/reviews/mzo-040-approval-packet.md`; this parameter is the seam it will
                use, never a silent partial implementation of it.

        Returns:
            A `Decision`. `allow` is the enforcement-terms verdict; `enforced` says whether a DENY
            actually blocks (False in shadow — today, always).
        """
        del context  # read only by `evaluate_worker_task` for the telemetry dimension; see above.
        approvals = self._approvals
        mode = approvals.mode
        # ONDA 1 §7.3: resolved ONCE, from the loaded manifest, for whatever class this call
        # lands on — including "no class", which takes `enforcement_padrao_nao_mapeado`.
        unmapped = approvals.enforcement_for(None)

        if approvals.degraded:
            return Decision(
                None, allow=False, reason=REASON_MANIFEST_UNAVAILABLE, mode=mode, enforcement=unmapped
            )
        if not isinstance(action_class, str) or not action_class.strip():
            return Decision(None, allow=False, reason=REASON_ACTION_UNMAPPED, mode=mode, enforcement=unmapped)

        name = action_class.strip()
        enforcement = approvals.enforcement_for(name)
        if name in approvals.approved:
            # An ALLOW read out of an override-sourced manifest whose enforcement was withheld is
            # still "would allow" — but it is NOT the same fact as an approved class in the
            # governed record, and the telemetry must not let the two look alike.
            reason = REASON_OVERRIDE_NOT_ENFORCEABLE if mode == MODE_SHADOW_OVERRIDE else REASON_APPROVED
            return Decision(name, allow=True, reason=reason, mode=mode, enforcement=enforcement)
        if name not in approvals.declared:
            return Decision(
                name, allow=False, reason=REASON_ACTION_UNDECLARED, mode=mode, enforcement=enforcement
            )
        reason = approvals.denial_reasons.get(name, REASON_APPROVAL_PENDING)
        return Decision(name, allow=False, reason=reason, mode=mode, enforcement=enforcement)


def _fail_closed_decision(mode: str, enforcement: str = ENFORCEMENT_ENFORCING) -> Decision:
    """The decision a caller must use when the gateway itself misbehaved (belt-and-suspenders).

    `enforcement` defaults to `enforcing` so the CONJUNCTION in `Decision.enforced` reduces to the
    pre-Onda-1 `mode == enforcing`: an internal error must still BLOCK under a live global
    enforcement rather than be quietly downgraded by a per-class dimension nobody could resolve.

    NO CALLER OVERRIDES THAT DEFAULT, and that is the invariant, not an accident: reaching this
    function means the class could NOT be resolved, so there is no honest per-class value to pass.
    The parameter exists so a future caller that genuinely resolved a class can say so explicitly —
    and so this docstring, rather than a silent positional argument at one call site, is where that
    decision has to be argued. `evaluate_worker_task`'s error path calls `_fail_closed_decision(mode)`.
    """
    return Decision(None, allow=False, reason=REASON_INTERNAL_ERROR, mode=mode, enforcement=enforcement)


#: Telemetry event names, chosen by MODE rather than only carried as a field. A shadow line and a
#: line that actually blocked a care-affecting call are different events for an operator: log
#: routing, alerting and dashboards key on the event name long before anyone parses `mode=`.
#: Both names are kept stable, and `mode=` is still emitted, so nothing that filtered on the field
#: stops working.
EVENT_SHADOW = "action_execution_gateway_shadow"
EVENT_ENFORCED = "action_execution_gateway_enforced"


def evaluate_worker_task(*, topic: str, tenant: str = "unknown", path: str | Path | None = None) -> Decision:
    """Evaluate one external-task dispatch and emit the telemetry line. NEVER raises.

    This is the single entry point the worker chokepoint calls. It classifies the topic, decides,
    and emits exactly one structured log line per call carrying ONLY bounded, non-PHI tokens:
    `topic`, `action_class`, `decision` (WOULD_ALLOW/WOULD_DENY), `reason`, `mode`, `tenant`.
    No business key, no payload variable, no free text ever reaches this line — the deliberate
    contrast with DL-0043's finding that business keys ARE logged elsewhere in this repo.

    Args:
        topic: the external-task topic being dispatched.
        tenant: telemetry dimension only; never influences the verdict.
        path: explicit manifest path — the composition-root/test seam, mirroring
            `load_action_approvals(path)`. Production passes None and resolves the shipped file.
            NOT the env override, which is a deployment surface and cannot enforce on its own.

    Returns a `Decision` whose `enforced` is True only when a human has set `modo: enforcing` in
    the manifest. In shadow the caller must ignore `allow` entirely.
    """
    try:
        approvals = action_approvals(path)
        gateway = ActionExecutionGateway(approvals)
        action_class = gateway.classify(topic)
        decision = gateway.evaluate(action_class, {"tenant": tenant})
        _log_decision(decision, topic=topic, tenant=tenant)
    except Exception:  # noqa: BLE001 — a gateway bug must never crash dispatch; see below.
        # Fail closed on the VERDICT while resolving the mode from the already-cached manifest, so
        # an internal error cannot fail OPEN once a human has flipped to `enforcing`. The cached
        # accessor is a dict read after the first successful load, so this second call is safe.
        try:
            mode = action_approvals(path).mode
        except Exception:  # noqa: BLE001 — nothing left to trust; refuse to claim enforcement.
            mode = MODE_UNRESOLVED
        # ONDA 1 §7.3, AND THE ONE PLACE §7.3 MUST NOT REACH. The per-class dimension is NOT
        # applied here. An internal error left us with no resolved class, and reading that as "an
        # unmapped ref, so take `enforcement_padrao_nao_mapeado`" would let the ramp's explicit
        # `shadow` deviation — a decision about UNCLASSIFIED TRAFFIC — silently downgrade a
        # GATEWAY FAILURE to a log line under a live global `modo: enforcing`. That is a fail-open
        # on the one path that exists because something already went wrong. `_fail_closed_decision`
        # declares `enforcing` as its default for exactly this reason; the call site must let that
        # default stand, so `Decision.enforced` reduces to the pre-Onda-1 `mode == enforcing`.
        fail_closed = _fail_closed_decision(mode)
        logger.error(
            "action_execution_gateway_internal_error",
            topic=topic,
            mode=mode,
            enforcement=fail_closed.enforcement,
            enforced=fail_closed.enforced,
            exc_info=True,
        )
        return fail_closed
    return decision


def _log_decision(decision: Decision, *, topic: str, tenant: str) -> None:
    """Emit the one telemetry line. Every field is a bounded, non-PHI token or is dropped."""
    logger.info(
        EVENT_ENFORCED if decision.enforced else EVENT_SHADOW,
        topic=topic if _is_bounded_topic(topic) else "TOPICO_INVALIDO",
        action_class=decision.action_class or "NAO_MAPEADA",
        decision=decision.telemetry_decision,
        reason=decision.reason,
        mode=decision.mode,
        # ONDA 1 §7.3 / §9.4 step 5: `mode` alone no longer tells an operator whether a flip took.
        # Both dimensions are emitted so a failed per-class flip cannot look like a working one.
        enforcement=decision.enforcement,
        tenant=tenant if _is_bounded_token(tenant) else "INVALIDO",
    )


def _is_bounded_topic(value: Any) -> bool:
    """Topics are dotted tokens (`operadora.auth.issue_authorization`) — bounded, non-PHI."""
    return isinstance(value, str) and bool(re.fullmatch(r"[A-Za-z][A-Za-z0-9_.]{0,79}", value))
