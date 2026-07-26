"""Helena ORIGINATES the prior-authorization-analysis delegation (Helena->Rafael, ADR-0003).

Ported ~verbatim from the donor `Maezo-Healthcare-Plan` reference implementation
(`src/maezo/agents/helena/delegation.py`) as part of the T2.4 A2A W3 (Helena->Rafael proof edge)
build wave — see `docs/design/A2A-dispatcher-card-signing.md` §9 for the full port rationale. The
donor's imports (`from maezo.a2a import Budget, DelegationEnvelope`, plus `DelegationDispatcher`/
`DelegationResult` under `TYPE_CHECKING`) already resolve against W2's real public surface
(`maezo.a2a.__init__`), so this is a straight port — English docstrings/comments, PT-BR domain
terms (guia TISS, beneficiario, prestador, cobertura, ...) preserved, same as the rest of this repo.

This is the ONE additive, cross-agent capability allowed for Helena in Phase 1: besides
navigating/triaging (Phase 0), she may ORIGINATE a prior-authorization-analysis sub-task and
delegate it to Rafael via `DelegationDispatcher.delegate(envelope)`. **Helena's triage graph
(`agents/helena/graph.py`) is UNCHANGED** — this module is a sibling helper a harness / an
authorization journey calls when there is a TISS guide to analyze, not a node in her graph.
`spec/agents/helena/agent.yaml`'s `accepted_task_types: []` confirms she is not a delegation
TARGET in Phase 1; this module only originates, never receives.

GUARDS (ADR-0003, all structural — enforced by `maezo.a2a.delegation`, not by convention here):
- `task_id` idempotency: re-delegating the same `task_id` NEVER re-executes (dispatcher Guard 4).
  The `task_id` is derived deterministically from the guide: `auth-{tenant}-{guia}`.
- `delegation_chain` acyclic + `max_hops=3` + per-hop budget (applied in `DelegationEnvelope.root`).
- NO raw PHI (ADR-0006): `payload_ref` is a FHIR reference (coverage/beneficiary), and
  `payload_meta` carries only non-PHI identifiers (pseudo ids, guide number, category, value) plus
  the pre-resolved worker booleans. Never CPF/name/CNS.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from maezo.a2a import Budget, DelegationEnvelope

if TYPE_CHECKING:
    from maezo.a2a import DelegationDispatcher, DelegationResult

# Task type delegated to Rafael (must match his agent.yaml's accepted_task_types).
TASK_TYPE_AUTH_ANALYSIS = "authorization.analyze"

ORIGIN_AGENT = "helena"
TARGET_AGENT = "rafael"

# Default budget for an authorization delegation chain (cumulative, decrements per hop).
_DEFAULT_BUDGET = Budget(tokens=64, time_ms=60_000, cost_per_hop=1)

# Pre-resolved-by-worker boolean keys forwarded to Rafael (never PHI).
_BOOLEAN_KEYS = (
    "requer_autorizacao",
    "documentacao_completa",
    "beneficiario_ativo",
    "carencia_cumprida",
    "dut_atendida",
    "dentro_teto_l2",
    "rede_credenciada",
)


def auth_task_id(tenant: str, numero_guia_tiss: str) -> str:
    """Idempotent `task_id` derived from the guide: `auth-{tenant}-{guia}` (dispatcher Guard 4)."""
    return f"auth-{tenant}-{numero_guia_tiss}"


def build_auth_analysis_envelope(
    *,
    tenant: str,
    numero_guia_tiss: str,
    coverage_ref: str,
    case_meta: dict[str, Any],
    budget: Budget | None = None,
) -> DelegationEnvelope:
    """Build the Helena->Rafael root envelope for prior-authorization analysis.

    - `coverage_ref`: a FHIR reference (e.g. `fhir://Coverage/abc`) — goes as `payload_ref`. The
      envelope constructor rejects a `payload_ref` that looks like raw PHI (CPF/CNS/CNPJ).
    - `case_meta`: non-PHI case data (pseudo ids, procedure, category, care type, value) plus the
      pre-resolved-by-worker booleans. Converted into `payload_meta` (`Mapping[str, str]`).

    Applies the anti-loop guards at the root itself (origin != target, chain <= max_hops, budget).
    """
    payload_meta = _build_payload_meta(numero_guia_tiss, case_meta)
    return DelegationEnvelope.root(
        task_id=auth_task_id(tenant, numero_guia_tiss),
        task_type=TASK_TYPE_AUTH_ANALYSIS,
        origin=ORIGIN_AGENT,
        target=TARGET_AGENT,
        tenant=tenant,
        budget=budget or _DEFAULT_BUDGET,
        payload_ref=coverage_ref,
        payload_meta=payload_meta,
    )


def _build_payload_meta(numero_guia_tiss: str, case_meta: dict[str, Any]) -> dict[str, str]:
    """Serialize the case into `payload_meta` (`Mapping[str, str]`, never raw PHI)."""
    meta: dict[str, str] = {"numero_guia_tiss": numero_guia_tiss}
    for key in (
        "beneficiario_pseudo_id",
        "prestador_id",
        "codigo_procedimento_tuss",
        "categoria_procedimento",
        "carater_atendimento",
        "cid10",
        "patient_ref",
    ):
        if case_meta.get(key) is not None:
            meta[key] = str(case_meta[key])
    if case_meta.get("valor_estimado_brl") is not None:
        meta["valor_estimado_brl"] = str(case_meta["valor_estimado_brl"])
    for key in _BOOLEAN_KEYS:
        if key in case_meta:
            meta[key] = "true" if bool(case_meta[key]) else "false"
    return meta


async def delegate_auth_analysis(
    dispatcher: DelegationDispatcher,
    *,
    tenant: str,
    numero_guia_tiss: str,
    coverage_ref: str,
    case_meta: dict[str, Any],
    budget: Budget | None = None,
) -> DelegationResult:
    """Originate and dispatch the Helena->Rafael delegation. Idempotent by `task_id`.

    Returns the dispatcher's structured `DelegationResult` (success with `output_ref` = the
    started process's reference, or a structured rejection — never a raise to the caller). On
    re-delivery of the same `task_id`, `idempotent_replay=True` and the handler does NOT run again.
    """
    envelope = build_auth_analysis_envelope(
        tenant=tenant,
        numero_guia_tiss=numero_guia_tiss,
        coverage_ref=coverage_ref,
        case_meta=case_meta,
        budget=budget,
    )
    return await dispatcher.delegate(envelope)
