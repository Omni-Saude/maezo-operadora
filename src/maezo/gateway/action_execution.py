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

THE RECORD IS DATA. `spec/policies/autonomy/action-approvals.yaml` (CODEOWNERS-gated). Ratifying
is a data change: fill the per-domain blocks, complete the topic map, set `status: RATIFICADO`,
set `modo: enforcing`. No Python change, no redeploy of this module.

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
`tests/unit/sec/test_action_execution_fence.py::test_shipped_manifest_exists_and_is_wellformed`
(a CODEOWNERS-gated deletion fails CI) and is disclosed, with its named runtime remedy, in
`docs/reviews/mzo-040-approval-packet.md`.

NO PHI. Nothing in this module reads, logs or stores a business key, a payload variable, or any
free text. The only values that reach telemetry are the topic, the action class, a bounded reason
token from the closed enum below, the mode, and the tenant — every one of them a bounded,
non-PHI token guarded by `_is_bounded_token`.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType
from typing import Any

import structlog
import yaml

from maezo.agents import resolve_spec_dir

logger = structlog.get_logger(__name__)

#: Narrow env override for the manifest path — a test-pinning hook and an operator escape hatch,
#: mirroring `auth_criteria.MANIFEST_PATH_ENV`. When unset the default resolves through the single
#: T0.3 mechanism (`resolve_spec_dir`), the SAME resolution `pep.py` uses for the autonomy matrix.
MANIFEST_PATH_ENV = "MAEZO_ACTION_APPROVALS_PATH"

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

# -- Reason vocabulary: a CLOSED enum of bounded, non-PHI tokens (design mirror of the
# `_ENUM_TOKEN_RE` discipline in `tools/workers/harness.py`). Every one is safe in the clear.
REASON_APPROVED = "APROVADO"
REASON_MANIFEST_UNAVAILABLE = "MANIFESTO_INDISPONIVEL"
REASON_MANIFEST_DRAFT = "MANIFESTO_NAO_RATIFICADO"
REASON_ACTION_UNMAPPED = "ACAO_NAO_MAPEADA"
REASON_ACTION_UNDECLARED = "ACAO_NAO_DECLARADA"
REASON_DOMAINS_EMPTY = "DOMINIOS_EXIGIDOS_VAZIO"
REASON_DOMAIN_UNKNOWN = "DOMINIO_DESCONHECIDO"
REASON_APPROVAL_PENDING = "APROVACAO_PENDENTE"
REASON_APPROVAL_INCOMPLETE = "APROVACAO_INCOMPLETA"
REASON_INTERNAL_ERROR = "GATEWAY_ERRO_INTERNO"

#: Same shape as `harness._ENUM_TOKEN_RE`, restated locally rather than imported: `maezo.gateway`
#: must not depend on `maezo.tools` (the dependency runs the other way — the harness imports
#: `gateway.audit`). Duplicating one regex keeps the layering clean.
_TOKEN_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,39}$")


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
        mode: `shadow` | `enforcing` | `unresolved`, as resolved from the manifest.
    """

    action_class: str | None
    allow: bool
    reason: str
    mode: str

    @property
    def enforced(self) -> bool:
        """True iff a DENY must actually block. False in shadow and on an unresolved manifest."""
        return self.mode == MODE_ENFORCING

    @property
    def telemetry_decision(self) -> str:
        """The bounded token a shadow log line carries: WOULD_ALLOW | WOULD_DENY."""
        return "WOULD_ALLOW" if self.allow else "WOULD_DENY"


@dataclass(frozen=True)
class ActionApprovals:
    """An immutable, already-validated view of the approval manifest.

    Attributes:
        mode: `shadow` | `enforcing` | `unresolved`.
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
            `APROVACAO_PENDENTE`). Computed once at load; this is what an approver reads in the
            shadow telemetry, so "nobody signed yet" never looks like "the record is broken".
        degraded: True when the manifest could not be loaded/parsed at all.
    """

    mode: str
    approved: frozenset[str]
    declared: frozenset[str]
    topic_to_class: Mapping[str, str]
    denial_reasons: Mapping[str, str] = MappingProxyType({})
    degraded: bool = False

    def classify(self, topic: Any) -> str | None:
        """The action class declared for `topic`, or None (=> `ACAO_NAO_MAPEADA`, fail closed)."""
        if not isinstance(topic, str) or not topic.strip():
            return None
        return self.topic_to_class.get(topic.strip())


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


def _parse(raw_text: str, manifest_path: Path) -> ActionApprovals:
    """Parse an already-read manifest. NEVER raises; refuses into `_EMPTY_APPROVALS` instead."""
    try:
        data = yaml.safe_load(raw_text)
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

    raw_map = data.get("mapeamento_topicos")
    if raw_map is None:
        raw_map = {}
    if not isinstance(raw_map, dict):
        return _refuse("invalid_schema", f"{manifest_path}: 'mapeamento_topicos' must be a mapping")
    topic_map = {
        str(topic).strip(): str(name).strip()
        for topic, name in raw_map.items()
        if isinstance(topic, str) and topic.strip() and isinstance(name, str) and name.strip()
    }

    logger.info(
        "action_approvals_manifest_loaded",
        path=str(manifest_path),
        mode=mode,
        declared_count=len(declared),
        approved_count=len(approved),
        approved=sorted(approved),
        topic_mappings=len(topic_map),
    )
    return ActionApprovals(
        mode=mode,
        approved=approved,
        declared=declared,
        topic_to_class=MappingProxyType(topic_map),
        denial_reasons=MappingProxyType(denial_reasons),
    )


def load_action_approvals(path: str | Path | None = None) -> ActionApprovals:
    """Load the approval manifest. FAILS CLOSED — never raises, never defaults to approved.

    Path resolution: the explicit `path` argument > `MAEZO_ACTION_APPROVALS_PATH` >
    `<spec>/policies/autonomy/action-approvals.yaml` (via `resolve_spec_dir`).

    Every failure mode (unresolvable spec dir, missing file, non-file path, unreadable, non-UTF-8,
    malformed YAML, wrong schema, `unratified: true` marker) returns an `ActionApprovals` with
    zero approved classes and `mode=unresolved` — which neither allows anything nor turns
    enforcement on — plus one `error` log line. See the module docstring for why this swallows
    where the retention-matrix precedent raises.
    """
    raw_path = path if path is not None else os.environ.get(MANIFEST_PATH_ENV)
    if not raw_path:
        try:
            raw_path = _manifest_default_path()
        except Exception as exc:  # noqa: BLE001 - fail-closed: unresolvable spec/ approves nothing
            return _refuse("path_unresolved", f"could not resolve the default manifest path: {exc}")

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

    return _parse(raw_text, manifest_path)


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

        if approvals.degraded:
            return Decision(None, allow=False, reason=REASON_MANIFEST_UNAVAILABLE, mode=mode)
        if not isinstance(action_class, str) or not action_class.strip():
            return Decision(None, allow=False, reason=REASON_ACTION_UNMAPPED, mode=mode)

        name = action_class.strip()
        if name in approvals.approved:
            return Decision(name, allow=True, reason=REASON_APPROVED, mode=mode)
        if name not in approvals.declared:
            return Decision(name, allow=False, reason=REASON_ACTION_UNDECLARED, mode=mode)
        reason = approvals.denial_reasons.get(name, REASON_APPROVAL_PENDING)
        return Decision(name, allow=False, reason=reason, mode=mode)


def _fail_closed_decision(mode: str) -> Decision:
    """The decision a caller must use when the gateway itself misbehaved (belt-and-suspenders)."""
    return Decision(None, allow=False, reason=REASON_INTERNAL_ERROR, mode=mode)


def evaluate_worker_task(*, topic: str, tenant: str = "unknown") -> Decision:
    """Evaluate one external-task dispatch and emit the SHADOW telemetry. NEVER raises.

    This is the single entry point the worker chokepoint calls. It classifies the topic, decides,
    and emits exactly one structured log line per call carrying ONLY bounded, non-PHI tokens:
    `topic`, `action_class`, `decision` (WOULD_ALLOW/WOULD_DENY), `reason`, `mode`, `tenant`.
    No business key, no payload variable, no free text ever reaches this line — the deliberate
    contrast with DL-0043's finding that business keys ARE logged elsewhere in this repo.

    Returns a `Decision` whose `enforced` is True only when a human has set `modo: enforcing` in
    the manifest. In shadow the caller must ignore `allow` entirely.
    """
    try:
        approvals = action_approvals()
        gateway = ActionExecutionGateway(approvals)
        action_class = gateway.classify(topic)
        decision = gateway.evaluate(action_class, {"tenant": tenant})
        _log_shadow(decision, topic=topic, tenant=tenant)
    except Exception:  # noqa: BLE001 — a gateway bug must never crash dispatch; see below.
        # Fail closed on the VERDICT while resolving the mode from the already-cached manifest, so
        # an internal error cannot fail OPEN once a human has flipped to `enforcing`. The cached
        # accessor is a dict read after the first successful load, so this second call is safe.
        try:
            mode = action_approvals().mode
        except Exception:  # noqa: BLE001 — nothing left to trust; refuse to claim enforcement.
            mode = MODE_UNRESOLVED
        logger.error("action_execution_gateway_internal_error", topic=topic, mode=mode, exc_info=True)
        return _fail_closed_decision(mode)
    return decision


def _log_shadow(decision: Decision, *, topic: str, tenant: str) -> None:
    """Emit the one telemetry line. Every field is a bounded, non-PHI token or is dropped."""
    logger.info(
        "action_execution_gateway_shadow",
        topic=topic if _is_bounded_topic(topic) else "TOPICO_INVALIDO",
        action_class=decision.action_class or "NAO_MAPEADA",
        decision=decision.telemetry_decision,
        reason=decision.reason,
        mode=decision.mode,
        tenant=tenant if _is_bounded_token(tenant) else "INVALIDO",
    )


def _is_bounded_topic(value: Any) -> bool:
    """Topics are dotted tokens (`operadora.auth.issue_authorization`) — bounded, non-PHI."""
    return isinstance(value, str) and bool(re.fullmatch(r"[A-Za-z][A-Za-z0-9_.]{0,79}", value))
