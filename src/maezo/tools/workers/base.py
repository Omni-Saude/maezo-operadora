"""WorkerBase and WorkerRegistry — foundation for BPMN external task workers.

Provides:
- WorkerBase: base class with structlog logger, error handling, retry
- WorkerRegistry: registration/retrieval by external task topic
- FunctionWorker: adapter wrapping dict-first/typed-I/O entry functions (T1.2/ADR-0026)
- reclassify_coded_exception: the shared coded-exception -> ValueError reclassification rule
  (ADR-0026 §5) — `FunctionWorker.execute` and any raw-handler wrapper needing the SAME rule
  (e.g. `programa._call_guarded`, GK-w5 finding 3) both call this ONE implementation
- pick_fields: fail-closed explicit field selection for dict->dataclass marshalling
- ERR_*_NOT_HUMAN: guard constants preventing automatic adverse actions

Per ADR-0008 (autonomy levels): L0 hard actions (negativa, fraude accusation)
must NEVER be performed automatically by workers. The ERR_*_NOT_HUMAN guards
enforce this at the code level.
"""

from __future__ import annotations

import dataclasses
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any, ClassVar

import structlog

# ---------------------------------------------------------------------------
# Guard constants — ERR_*_NOT_HUMAN
# ---------------------------------------------------------------------------

ERR_DENIAL_NOT_HUMAN: str = "ERR_DENIAL_NOT_HUMAN"
"""Guard: automatic denials are FORBIDDEN. Only human auditors may deny."""

ERR_AUTH_DENIAL_INCOMPLETE: str = "ERR_AUTH_DENIAL_INCOMPLETE"
"""Guard: a formal denial notice with INCOMPLETE fundamentacao must never be transmitted.

Modeled in SP-OP-AUTH-001 (`bpmn:error@errorCode="ERR_AUTH_DENIAL_INCOMPLETE"`,
`Error_AuthDenialIncompleta`) with a matching boundary event `BE_NegativaIncompleta` on
`ST_EnviarNegativaFormal` routing to the neutral terminal `End_FundamentacaoIncompletaBloqueada`.
Raised (as `WorkerBpmnError`) by `SendDenialNoticeWorker` when a NEGAR decision reaches the worker
without every ANS-required grounding field (justificativa_clinica / cid10_referencia /
fundamentacao_dut). Because it is spec-modeled, it is registered in the auth harness's
`bpmn_error_allowlist` (`AUTH_BPMN_ERROR_ALLOWLIST`) so the harness dispatches it as a real
`bpmnError` instead of demoting it to a fail-closed incident (harness.py `_bpmn_error_allowlist`)."""

ERR_AUTH_AUTO_CEILING_NOT_AUTHORIZED: str = "ERR_AUTH_AUTO_CEILING_NOT_AUTHORIZED"
"""Guard: an AUTOMATIC authorization issuance whose value the tenant ceiling does not authorize.

Returned (never raised — see below) by `IssueAuthorizationWorker` on the AUTOMATIC channel only
(`auto_aprovacao.recomendacao == AUTO_APROVAR` and NO human decision), when
`CeilingResolver.within_l2_ceiling(action="authorization_approval", param="max_value_brl")` does
not admit `valor_estimado_brl` for the tenant — including every fail-closed vector (blank tenant,
missing/non-numeric/negative/non-finite value, resolver unavailable). It makes the governance
ceiling load-bearing at the ISSUANCE chokepoint, where until now it was decorative on that route.
CAVEAT (GK-ceiling finding 1) — this is a MITIGATION, not a fence: the auto channel is
discriminated by `auto_sanctioned and not human_approved`, and nothing in the AUTH BPMN ever
sets `human_approved`. There is no start-variable allowlist, so a caller able to make the DMN
sanction the auto route (since #198, that means genuinely satisfying the computed criteria —
`ST_ValidateAutoApprovalCriteria` overwrites `dut_atendida`/`dentro_teto_l2`/`rede_credenciada`
seeding on every path, so it is no longer as simple as seeding those three booleans) can still
seed `human_approved: true` or `decisao_auditor: 'APROVAR'` and skip this check entirely. It
grants nothing beyond the pre-mitigation baseline (which issued unconditionally on the auto
sanction), and discriminating on the activity-local variable instead would risk ceiling-gating a
HUMAN-approved issuance — care denial, the worse error. The MZO-040 pre-gateway worker now EXISTS
(`ST_ValidateAutoApprovalCriteria`,
spec/processes/bpmn/SP-OP-AUTH-001_Autorizacao_Previa.bpmn:205) and closes GAP-AUTH-4
structurally (the DMN decides on computed facts, not seeded ones). This residual
`human_approved`-seeding bypass is a SEPARATE risk that narrowing DEFAULT_ALLOWED_PROCESS_KEYS
would still mitigate — an open owner/SME question (`process_allowlist.py`), not decided here.

DISTINCT FROM `ERR_DENIAL_NOT_HUMAN` on purpose: that code means "no modeled sanction at all";
this one means "the route was sanctioned by the DMN but the tenant's autonomy ceiling does not
authorize an AUTOMATIC issuance of this value" — a different governance fact, so log/audit
readers can tell them apart.

The HUMAN channel is NEVER gated by this code: `decisao_auditor == 'APROVAR'` (and the junta
route) issues regardless of the ceiling — exceeding the automatic ceiling is precisely what human
review exists for (ADR-0008 L2 vs human decision).

NOT a BPMN error and deliberately NOT in any `bpmn_error_allowlist`: `ST_EmitirAutorizacaoAuto`
declares NO error boundary event, and an unmodeled `bpmnError` silently ends the process scope on
CIB Seven 2.1.0 (ADR-0030 hazard). The refusal is therefore a RETURNED
`{"status": "blocked_by_guard", "error_code": ...}` record, exactly like `ERR_DENIAL_NOT_HUMAN`
in the same worker."""

ERR_ESCALATION_NOT_HUMAN: str = "ERR_ESCALATION_NOT_HUMAN"
"""Guard: automatic escalation closing is FORBIDDEN. Only human supervisors may resolve."""

ERR_FRAUD_ACCUSATION_NOT_HUMAN: str = "ERR_FRAUD_ACCUSATION_NOT_HUMAN"
"""Guard: automatic fraud accusation is FORBIDDEN. Only human review may accuse."""


# ---------------------------------------------------------------------------
# WorkerBase
# ---------------------------------------------------------------------------


class WorkerBase(ABC):
    """Base class for all BPMN external task workers.

    Features:
    - structlog logger (per ADR-0007 audit requirements)
    - Retry with backoff on failure
    - Synchronous and async execution paths
    - Topic-based routing in WorkerRegistry

    Subclasses must implement execute() and may override topic, max_retries,
    and retry_backoff.

    WorkerBase.execute() is synchronous by design (London School TDD):
    workers are pure functions that transform process_vars -> result dict.
    Async I/O is handled by the engine/message layer, not by the worker logic.
    """

    # Default topic derived from class name (snake_case)
    _DEFAULT_TOPIC: ClassVar[str | None] = None  # overridden in __init_subclass__

    def __init__(
        self,
        topic: str | None = None,
        max_retries: int = 3,
        retry_backoff: float = 1.0,
    ) -> None:
        """Initialize the worker.

        Args:
            topic: External task topic for this worker. Defaults to
                   class name in snake_case (e.g. NotifyTeamWorker -> "notify_team_worker").
            max_retries: Maximum retry attempts on failure (default: 3).
            retry_backoff: Backoff multiplier in seconds between retries (default: 1.0).
        """
        self._topic = topic or self._derive_topic()
        self.max_retries = max_retries
        self.retry_backoff = retry_backoff
        self.logger = structlog.get_logger(f"maezo.workers.{self._topic}")

    @property
    def topic(self) -> str:
        """External task topic this worker consumes."""
        return self._topic

    def _derive_topic(self) -> str:
        """Derive a default topic from the class name (CamelCase -> snake_case)."""
        import re

        name = type(self).__name__
        # Insert underscore before uppercase letters, convert to lowercase
        s1 = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
        return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s1).lower()

    @abstractmethod
    def execute(self, process_vars: dict[str, Any]) -> dict[str, Any]:
        """Execute the worker's business logic.

        Args:
            process_vars: Variables from the BPMN process instance.

        Returns:
            Result dictionary to be merged back into process variables.

        This is the ONLY method subclasses need to implement.
        """
        ...

    def run(self, process_vars: dict[str, Any]) -> dict[str, Any]:
        """Execute the worker with retry logic.

        Args:
            process_vars: Variables from the BPMN process instance.

        Returns:
            Result dictionary.

        Raises:
            Exception: If all retry attempts fail.

        M11: Emits worker_execution_time_seconds on success and
        worker_error_count_total on failure via the observability layer.
        """
        import time as time_module

        last_error: Exception | None = None
        start_time = time_module.monotonic()

        for attempt in range(1, self.max_retries + 1):
            try:
                self.logger.debug(
                    "worker_executing",
                    topic=self._topic,
                    attempt=attempt,
                )
                result = self.execute(process_vars)
                self.logger.debug(
                    "worker_completed",
                    topic=self._topic,
                    attempt=attempt,
                )

                # M11: Record successful execution time
                duration = time_module.monotonic() - start_time
                try:
                    from maezo.platform.observability import record_worker_execution

                    record_worker_execution(
                        worker_name=type(self).__name__,
                        topic=self._topic,
                        duration_seconds=duration,
                    )
                except Exception:
                    pass  # Metrics are best-effort; never break worker execution

                return result
            except Exception as e:
                last_error = e
                self.logger.warning(
                    "worker_failed",
                    topic=self._topic,
                    attempt=attempt,
                    max_retries=self.max_retries,
                    error=str(e),
                )

                # M11: Record error count on each failure
                try:
                    from maezo.platform.observability import record_worker_error

                    record_worker_error(
                        worker_name=type(self).__name__,
                        topic=self._topic,
                        error_type=type(e).__name__,
                    )
                except Exception:
                    pass  # Metrics are best-effort

                if attempt < self.max_retries:
                    time.sleep(self.retry_backoff * attempt)

        self.logger.error(
            "worker_exhausted_retries",
            topic=self._topic,
            max_retries=self.max_retries,
        )
        raise last_error  # type: ignore[misc]

    def execute_sync(self, process_vars: dict[str, Any]) -> dict[str, Any]:
        """Synchronous wrapper around execute() — alias for run().

        Provided for clarity when using workers synchronously in tests.
        """
        return self.run(process_vars)

    async def run_async(self, process_vars: dict[str, Any]) -> dict[str, Any]:
        """Async wrapper around run().

        Currently delegates to the synchronous run() method. Override in
        subclasses that need true async I/O.

        Args:
            process_vars: Variables from the BPMN process instance.

        Returns:
            Result dictionary.
        """
        return self.run(process_vars)


# ---------------------------------------------------------------------------
# FunctionWorker — adapter wrapping dict-first / typed-I/O entry functions
# ---------------------------------------------------------------------------

#: Exception types the harness (`tools/workers/harness.py:_handle`) already classifies
#: correctly on its own: `PermissionError` family -> `failure(retries=0)` (L0 guard, never
#: retried); `ValueError` family -> `failure(retries=0)` (bad/immutable input); the transient
#: infra family -> engine-side computed retry. `reclassify_coded_exception` never intercepts these.
_HARNESS_CLASSIFIED: tuple[type[Exception], ...] = (
    PermissionError,
    ValueError,
    RuntimeError,
    OSError,
    TimeoutError,
    ConnectionError,
)


def reclassify_coded_exception(fn: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    """Call `fn()`, reclassifying a "coded" guard exception into `ValueError` (ADR-0026 §5).

    GK-w5 finding 3 (single source of truth): this is the ONE shared implementation of the
    reclassification rule — `FunctionWorker.execute` (below) and `programa._call_guarded`
    (`tools/workers/programa.py`, item A/B raw-handler Kafka seam) both call THIS function rather
    than each hand-rolling their own copy of `_HARNESS_CLASSIFIED` + the duck-typed `.code`/
    `.message` check. A future edit to the classified tuple or the reclassify rule now changes
    behavior for every caller identically, with no risk of silent divergence.

    Six of the 16 worker modules (adequacao/credenciamento/fraude/inadimplencia/pagto/programa)
    raise a bespoke `Exception` subclass with a `.code`/`.message` pair for their GATED (human-
    decision or custody-integrity) failures — e.g. `AdequacaoError(ERR_FALLBACK_COMMITMENT_NOT_
    HUMAN, ...)` — rather than subclassing `PermissionError`/`ValueError` (ADR-0026 census,
    "Heterogeneous error classes"). Left alone, the harness's generic `except Exception` branch
    would treat these as *unclassified* and apply the **transient, engine-retried** outcome —
    retrying an L0 guard could drive an adverse action (ADR-0008), so this is a fail-closed defect
    on the registry path. Any such "coded" exception (duck-typed: not already one of
    `_HARNESS_CLASSIFIED`, but exposing string `.code` and `.message` attributes) is re-raised as a
    `ValueError(f"{code}: {message}")` (chained via `from exc`), which the harness's EXISTING
    classification already routes to `failure(retries=0)` — an engine-guaranteed, human-visible
    incident, never retried. Already-`_HARNESS_CLASSIFIED` exceptions (and anything else lacking
    the `.code`/`.message` shape, e.g. `WorkerBpmnError` — it carries `.error_code`, not `.code`)
    pass through UNCHANGED via the trailing bare `raise`.
    """
    try:
        return fn()
    except _HARNESS_CLASSIFIED:
        raise
    except Exception as exc:  # noqa: BLE001 — reclassified below, see docstring.
        code = getattr(exc, "code", None)
        message = getattr(exc, "message", None)
        if isinstance(code, str) and isinstance(message, str):
            raise ValueError(f"{code}: {message}") from exc
        raise


class FunctionWorker(WorkerBase):
    """Adapter wrapping a dict-first callable `fn(variables: dict) -> dict` as a `WorkerBase`.

    ADR-0026 Decisao §1/§2. `fn` is either:
    - one of the 42 dict-first module functions (`fn(variables) -> dict`), wrapped directly, or
    - a typed-I/O module's dict-boundary **entry function** (ADR-0026 §2b) — explicit field
      selection -> typed dataclass -> the UNCHANGED typed function -> `dataclasses.asdict`.

    `max_retries` defaults to **1** (execute once) — per T1.1 design §9 the ENGINE owns durable
    retry; `WorkerBase`'s in-process retry stays opt-in per worker for provably-idempotent
    transient faults only (pass `max_retries>1` explicitly to opt in).

    Error reclassification (ADR-0026 Decisao §5 — the `classify_worker_error` provision,
    implemented here rather than in the harness so no harness/dispatch code changes): `execute()`
    delegates to the module-level `reclassify_coded_exception` (its own docstring has the full
    rationale) — the metrics `error_type` label reports `ValueError` for a reclassified coded
    exception, a deliberate, documented trade-off for correctness. The module's own exception
    type/tests are untouched (this only affects the FunctionWorker/harness dispatch path).
    """

    def __init__(
        self,
        topic: str,
        fn: Callable[[dict[str, Any]], dict[str, Any]],
        *,
        max_retries: int = 1,
        retry_backoff: float = 1.0,
    ) -> None:
        super().__init__(topic=topic, max_retries=max_retries, retry_backoff=retry_backoff)
        self._fn = fn

    def execute(self, process_vars: dict[str, Any]) -> dict[str, Any]:
        return reclassify_coded_exception(lambda: self._fn(process_vars))


def non_blank(value: Any) -> bool:
    """True iff `value` is a present, non-blank business-key anchor field (fail-closed).

    Single source of truth for anchor-field validation, shared by the NotificationBridge rule
    predicates (`platform/notification_bridge.py`) and the fenced-start handoff workers
    (`contas.start_recurso`, `fraude.start_credenciamento`/`start_contratual`) — EB-4 R1 finding:
    the workers' plain `bool(str(variables.get(k, "")))` truthiness let WHITESPACE-ONLY and
    EXPLICIT-`None` anchors slip through (`str(None) == "None"` is truthy; `"   "` is truthy),
    producing degenerate business keys like `CRED-{tenant}-None` / `RECURSO-{t}-G-   ` instead of
    the intended refusal.

    Fail-closed semantics: `None`, absent, empty, and whitespace-only are all rejected the same
    way; any other value round-trips through `str(...).strip()` and passes iff non-empty.
    """
    if value is None:
        return False
    return bool(str(value).strip())


def resolve_fraude_numero_caso(variables: dict[str, Any]) -> str:
    """Resolve the FRAUDE-001 `numero_caso` business-key anchor for a CONTAS→FRAUDE handoff
    (contract `SP-OP-FRAUDE-001.md` "Business key (idempotencia)"): use an already-assigned
    `numero_caso` when present, else fall back to `prestador_id` (the entity under
    investigation) — so repeated referrals of the SAME prestador converge on the SAME
    FRAUDE-001 instance instead of minting a fresh, non-deterministic case id on every forward.

    Single source of truth — shared by BOTH derivation sites, so they are byte-identical for
    EVERY input type (not just the string case):
    - `contas.start_fraude`'s in-flow worker (`contas._fraude_numero_caso_for_handoff`);
    - `notification_bridge`'s CONTAS→FRAUDE Kafka-mirror rule
      (`notification_bridge._fraude_numero_caso_for_contas_handoff`).

    Before this extraction the two sites re-implemented the SAME derivation independently and
    had drifted: the worker used the shared `non_blank` (accepts any type whose stringified form
    is non-blank, e.g. an int/float/bool `numero_caso`) while the bridge required
    `isinstance(numero_caso, str)` — so a non-string `numero_caso` would derive a DIFFERENT
    business key on each path (worker: `str(numero_caso)`; bridge: the `prestador_id` fallback),
    a latent divergent-double-start hazard. `numero_caso` is not a CONTAS process variable today
    (never reachable), but this single implementation makes that class of drift structurally
    impossible rather than relying on two docstrings staying in sync.

    Uses the SAME `non_blank` anchor-validation semantics as every other business-key field in
    this repo (fail-closed on `None`/blank/whitespace-only, via the stringified value) — an
    already-assigned `numero_caso` of ANY type is accepted and stringified; only a blank/None
    value falls back to `prestador_id`.
    """
    numero_caso = variables.get("numero_caso")
    if non_blank(numero_caso):
        return str(numero_caso)
    return str(variables.get("prestador_id", ""))


# ---------------------------------------------------------------------------
# Contract-anchored business keys — the SINGLE composer for the CANCEL / INAD families
# (B-2 anti-dupla-terminacao fix + DL-0043 leg (c) flag seam)
# ---------------------------------------------------------------------------

#: Family prefix of SP-OP-CANCEL-001's business key (`CANCEL-{tenant}-{contract identity}`).
CANCEL_KEY_FAMILY: str = "CANCEL"

#: Family prefix of SP-OP-INADIMPLENCIA-001's business key — DISTINCT from CANCEL over the SAME
#: contract identity (the two processes coordinate via topology + a runtime active-instance
#: check, not a shared key; `docs/processes/harmonization-inadimplencia-cancel.md` §1).
INADIMPLENCIA_KEY_FAMILY: str = "INAD"

#: Bounded anchor tokens for the DL-0043 shadow counter. `matricula` is the PHI-bearing one.
_ANCHOR_CONTRATO = "contrato"
_ANCHOR_MATRICULA = "matricula"
_ANCHOR_PSEUDO = "pseudo"


def contract_business_key(family: str, tenant_id: str, contrato: str) -> str:
    """`{family}-{tenant_id}-{contrato}` — the ONE place a contract-anchored key is formatted.

    Byte-identical to the four f-strings it replaces (`inadimplencia._cancel_business_key`,
    `fraude._cancel_business_key`/`_inadimplencia_business_key`,
    `notification_bridge._cancel_business_key`/`_inadimplencia_business_key`,
    `fernando.graph._business_key`). Extracted for the same reason `resolve_fraude_numero_caso`
    was: two sites that re-implement one derivation eventually drift, and a drifted business key
    is a divergent double start / an invisible active instance.
    """
    return f"{family}-{tenant_id}-{contrato}"


def _record_key_mint(family: str, modo: str, anchor: str) -> None:
    """Emit the DL-0043 shadow counter. NEVER raises into a key mint (telemetry is not the job).

    Same defensive posture as `harness._emit_worker_task_outcome`: a metrics/registry problem
    must never be able to fail — or worse, silently alter — a business-key derivation.
    """
    try:
        from maezo.platform.observability import record_phi_business_key_mint  # noqa: PLC0415

        record_phi_business_key_mint(family=family, modo=modo, anchor=anchor)
    except Exception:  # noqa: BLE001 — telemetry is best-effort; a key mint must never fail on it
        structlog.get_logger(__name__).debug(
            "phi_business_key_mint_metric_failed", family=family, anchor=anchor
        )


def resolve_contract_identity(
    *,
    numero_contrato: str,
    matricula_beneficiario: str,
    beneficiario_pseudo_id: str = "",
) -> tuple[str, str]:
    """Resolve the identity segment a contract-anchored key is minted FROM, plus its anchor token.

    Precedence, and why:

    1. ``numero_contrato`` — the contract's own identifier, not a person's. Always preferred;
       unchanged from day one, and unaffected by any policy mode.
    2. ``beneficiario_pseudo_id`` — ONLY under a RATIFIED `modo: pseudo_keys`
       (`spec/policies/privacy/phi-business-key-remediation.yaml`). This is the house's own
       correct precedent (`PROG-{tenant}-{programa}-{beneficiario_pseudo_id}-{ciclo}`,
       `agents/valentina/graph.py`; `DSR-...-{titular_pseudo_id}`).
    3. ``matricula_beneficiario`` — today's fallback for individual/familiar plans. It is a
       `PHI_PROCESS_VARS` name (`tools/workers/phi_vars.py`), which is precisely the finding
       DL-0043 leg (c) records: the house classifies this value as Zona PHI and still mints it
       raw into a durable, egressing business key.

    Step 2 is INERT until a human ratifies the manifest, and step 2 falls through to step 3
    whenever the pseudo id is absent — a blank `beneficiario_pseudo_id` never mints a degenerate
    `CANCEL-{tenant}-` key, it keeps the legacy anchor. So under the shipped policy this function
    returns exactly what `numero_contrato or matricula_beneficiario` returned before it existed.

    PLAIN TRUTHINESS, NOT `non_blank`, is deliberate here. `non_blank` would additionally reject
    a whitespace-only anchor — a genuine improvement in isolation, but it would make the GUARD
    query a different set of keys than the MINTERS mint (they all used `or`), which is the exact
    class of drift this extraction exists to remove. Tightening the anchor rule is a separate,
    visible change to make once, in this one function, for mint and query together.
    """
    if numero_contrato:
        return numero_contrato, _ANCHOR_CONTRATO

    from maezo.platform.privacy.phi_key_policy import phi_key_policy  # noqa: PLC0415 — lazy

    if phi_key_policy().pseudo_keys_enabled and beneficiario_pseudo_id:
        return beneficiario_pseudo_id, _ANCHOR_PSEUDO
    return matricula_beneficiario, _ANCHOR_MATRICULA


def mint_contract_business_key(
    family: str,
    tenant_id: str,
    *,
    numero_contrato: str,
    matricula_beneficiario: str,
    beneficiario_pseudo_id: str = "",
) -> str:
    """Mint a contract-anchored business key AND record the DL-0043 shadow counter.

    The single mint path for the CANCEL and INAD families. Under the shipped (`off`) policy the
    returned string is byte-identical to the pre-existing
    ``f"{family}-{tenant}-{numero_contrato or matricula_beneficiario}"``; the only added
    behaviour is one content-free counter increment recording WHICH anchor was used.

    "SINGLE MINT PATH" IS AN ENUMERATED CLAIM, NOT A SLOGAN. Six sites mint a CANCEL/INAD key and
    all six call this function: `inadimplencia._cancel_business_key`,
    `fernando.graph._business_key`, `fraude._cancel_business_key`,
    `fraude._inadimplencia_business_key`, `notification_bridge._cancel_business_key`,
    `notification_bridge._inadimplencia_business_key`. The last four reached
    `contract_business_key` directly until the DL-0043 repair, which is exactly how the
    `anchor="contrato"` series came to under-count. `contract_business_key_forms` below still
    calls the bare formatter on purpose: it QUERIES, it does not mint, and counting a query would
    corrupt the owner's evidence. Pinned by `test_phi_key_flag_behavior.py::
    test_no_cancel_inad_key_is_minted_outside_the_shared_mint_composer`.
    """
    contrato, anchor = resolve_contract_identity(
        numero_contrato=numero_contrato,
        matricula_beneficiario=matricula_beneficiario,
        beneficiario_pseudo_id=beneficiario_pseudo_id,
    )
    from maezo.platform.privacy.phi_key_policy import phi_key_policy  # noqa: PLC0415 — lazy

    _record_key_mint(family, phi_key_policy().modo.value, anchor)
    return contract_business_key(family, tenant_id, contrato)


def contract_business_key_forms(
    family: str,
    tenant_id: str,
    *,
    numero_contrato: str,
    matricula_beneficiario: str,
    beneficiario_pseudo_id: str = "",
) -> tuple[str, ...]:
    """EVERY business key form under which an active instance for this contract could exist.

    THIS IS THE B-2 FIX (anti-dupla-terminacao, confirmed blocker). The CANCEL business key is
    minted by THREE composers that do NOT agree on the anchor:

      * `inadimplencia._cancel_business_key`      -> `CANCEL-{t}-{numero_contrato OR matricula}`
      * `fraude._cancel_business_key`             -> `CANCEL-{t}-{numero_contrato}` (no fallback)
      * `notification_bridge._cancel_business_key`-> `CANCEL-{t}-{numero_contrato}` (no fallback)

    while `inadimplencia._query_ja_em_rescisao_cancel` queried ONLY the first form. For a contract
    carrying BOTH a `numero_contrato` and a `matricula_beneficiario`, a CANCEL-001 instance
    started by fraude/the bridge under the contract form is invisible to a query keyed on the
    matricula form, and vice versa — so the guard that exists to stop a double termination
    returns "no active rescisao" while one is live, and the independent suspension proceeds.

    The fix is to query the FULL set of derivable forms and treat a hit on ANY of them as an
    active rescisao. Finding MORE instances is strictly the conservative direction: it can only
    BLOCK an adverse effect that would otherwise have proceeded, never permit one.

    Returned forms, in stable order (deduped, blank anchors dropped):
      1. the `numero_contrato` form  — what fraude / the bridge mint;
      2. the `matricula_beneficiario` form — the individual/familiar-plan fallback;
      3. the `beneficiario_pseudo_id` form — ONLY under a ratified `modo: pseudo_keys`, which is
         the DUAL-READ half of the migration window: new mints move to the pseudo form while
         this guard keeps seeing instances that are still live under the legacy forms.

    THE SCOPE OF THAT DUAL-READ, STATED HONESTLY. This function is the repo's ONLY dual-reading
    key consumer, and it has exactly ONE caller:
    `inadimplencia._query_ja_em_rescisao_cancel`, the CANCEL anti-dupla-terminacao correlation
    guard. Nothing else dual-reads. In particular the START-side idempotency does NOT:
    `mcp_cibseven.transport.start_process_idempotent` takes a SINGLE `business_key` and uses it
    for both `find_active_instance` and `start_dedup_key`, so under `pseudo_keys` an instance
    already live under the legacy (matricula) form is invisible to a start keyed on the pseudo
    form, and its dedup key changes too (no ``ALREADY_AUDITED``). That is a real double-start
    exposure for SP-OP-INADIMPLENCIA-001 (`fernando.graph.start_process`) and SP-OP-CANCEL-001
    (`inadimplencia.handoff_rescisao`), and it is why the manifest lists start-side dual-read as
    an UNMET pre-requisite of `pseudo_keys` rather than as something this function already
    covers. Under `scrub_only` and `off` the exposure does not exist (no mint changes form).

    Returns an EMPTY tuple when no anchor at all is derivable — callers MUST treat that as
    "cannot rule out a rescisao" and fail closed, exactly as before. Anchor truthiness uses the
    SAME plain-`or` rule the minters use (see `resolve_contract_identity`): the guard must query
    the keys that are actually mintable, not a tidier set.
    """
    anchors = [numero_contrato, matricula_beneficiario]

    from maezo.platform.privacy.phi_key_policy import phi_key_policy  # noqa: PLC0415 — lazy

    if phi_key_policy().pseudo_keys_enabled:
        anchors.append(beneficiario_pseudo_id)

    forms: list[str] = []
    for anchor in anchors:
        if not anchor:
            continue
        key = contract_business_key(family, tenant_id, anchor)
        if key not in forms:
            forms.append(key)
    return tuple(forms)


def pick_fields(variables: dict[str, Any], cls: type) -> dict[str, Any]:
    """Explicit field selection: keep only the keys `cls` (a dataclass) actually declares.

    ADR-0026 §2b marshalling rule for the six typed-I/O modules — inbound `variables` (the flat
    BPMN process-variable dict, which also carries fields unrelated to this dataclass) maps to a
    typed input dataclass by EXPLICIT selection, never `**variables` (which would raise
    `TypeError: unexpected keyword argument` the moment an unrelated process variable is present,
    or silently accept extras a naive constructor tolerates). Missing keys are simply omitted —
    the dataclass's own defaults (or absence thereof) decide whether that is fail-closed; a
    dataclass field with NO default left unset raises `TypeError` at construction, which entry
    functions must translate into the module's own `*Invalido*Error` (ADR-0026 §2b: "a
    missing/invalid required field raises the module's own *Invalido*Error").
    """
    names = {f.name for f in dataclasses.fields(cls)}
    return {k: v for k, v in variables.items() if k in names}


# ---------------------------------------------------------------------------
# WorkerRegistry
# ---------------------------------------------------------------------------


class WorkerRegistry:
    """Registry mapping external task topics to WorkerBase instances.

    The Camunda/CIB Seven external task client uses this registry to
    dispatch tasks to the correct worker.

    Usage:
        registry = WorkerRegistry()
        registry.register("operadora.escalation.notify_team", NotifyTeamWorker())
        worker = registry.get("operadora.escalation.notify_team")
    """

    def __init__(self) -> None:
        """Initialize an empty registry."""
        self._workers: dict[str, WorkerBase] = {}
        self._logger = structlog.get_logger("maezo.workers.registry")

    def register(self, topic: str, worker: WorkerBase) -> None:
        """Register a worker for a topic.

        Args:
            topic: The external task topic (e.g. 'operadora.escalation.notify_team').
            worker: The WorkerBase instance that handles this topic.

        If a worker is already registered for this topic, it is replaced
        (with a warning log).
        """
        if topic in self._workers:
            self._logger.warning(
                "worker_registry_replace",
                topic=topic,
                previous=type(self._workers[topic]).__name__,
                new=type(worker).__name__,
            )
        self._workers[topic] = worker
        self._logger.debug(
            "worker_registered",
            topic=topic,
            worker_type=type(worker).__name__,
        )

    def get(self, topic: str) -> WorkerBase | None:
        """Retrieve a worker by topic.

        Args:
            topic: The external task topic.

        Returns:
            The WorkerBase instance, or None if not registered.
        """
        return self._workers.get(topic)

    def list_topics(self) -> list[str]:
        """Return all registered topic names."""
        return list(self._workers.keys())

    def count(self) -> int:
        """Return the number of registered workers."""
        return len(self._workers)

    def clear(self) -> None:
        """Remove all registered workers (useful for testing)."""
        self._workers.clear()
        self._logger.debug("worker_registry_cleared")


# Singleton instance for the default registry
worker_registry = WorkerRegistry()
