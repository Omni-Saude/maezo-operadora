"""Authorization workers — SP-OP-AUTH-001.

Provides BPMN external task handlers for the Autorizacao Previa process.

Workers:
- ValidateAutoCriteriaWorker: portao de criterios da aprovacao automatica (GAP-AUTH-4)
- AnalyzeRequestWorker: convoca Rafael, monta dossie de analise
- RequestDocumentsWorker: pendencia ao prestador
- IssueAuthorizationWorker: emite autorizacao TISS
- SendDenialNoticeWorker: negativa formal (guard: ERR_AUTH_DENIAL_NOT_HUMAN)
- NotifySlaRiskWorker: alerta coordenacao
- ConveneJuntaWorker: convoca junta medica

CRITICAL (ADR-0008, L0 hard):
- Negativa de cobertura SO nasce nas User Tasks humanas.
- Workers with ERR_AUTH_DENIAL_NOT_HUMAN guard: send_denial_notice,
  issue_authorization (when decisao=NEGAR).
- Auto-approval (L2) exists only with DMN favorable + teto do tenant.

PORTAO DE CRITERIOS (GAP-AUTH-4 — fechamento ESTRUTURAL). Aquela ultima linha era DECORATIVA na
rota automatica: `BRT_AutoApproval` decidia `AUTO_APROVAR` sobre `dut_atendida`/`dentro_teto_l2`/
`rede_credenciada` SEMEADOS no payload de start, sem nenhum codigo computando qualquer um deles
antes da gateway (o unico chamador de `CeilingResolver` em AUTH, `AnalyzeRequestWorker` em
`ST_PrepararDossie`, esta na perna HUMANA, DEPOIS do gateway). A patologia raiz: **ausencia de
validacao lia-se como PASS implicito.**

`ValidateAutoCriteriaWorker` (`ST_ValidateAutoApprovalCriteria`, entre `BRT_SlaAnalise` e
`BRT_AutoApproval` — espelha `BRT_Calculo → ST_CalculateAmount → BRT_AutoApproval` de
SP-OP-REEMBOLSO-001) inverte isso: COMPUTA quatro criterios — tecnico (DUT/ROL), financeiro
(teto do tenant), regulatorio (carencia/CPT) e contratual (milestones/regras/KPI) — e escreve
`auto_criteria_verificado`, que a regra r1 da DMN reescrita EXIGE. Pular o validador faz o token
cair no catch-all -> `ANALISE_HUMANA`. As tabelas clinicas sao SINTETICAS: um criterio so pode
contribuir com um PASS se sua fonte estiver RATIFICADA
(`spec/processes/dmn/auth-criteria-ratification.yaml`) — ver a docstring da classe.

`IssueAuthorizationWorker` mantem, em defesa-em-profundidade, a verificacao de teto no PONTO DE
EMISSAO (`ERR_AUTH_AUTO_CEILING_NOT_AUTHORIZED`, retornado — nunca lancado), exclusivamente no
canal automatico; o canal humano segue intocado por design.

HUMAN-PROVENANCE DERIVATION (item-9 bucket-3 Class-C — the `human_approved` threading fix,
T3.1 R2 finding `_ACTION_WORKER_KAFKA_GAP_REASON`): the v2 action-worker guards demanded a bare
`human_approved is True` process variable that NOTHING in the model ever sets (no BPMN
inputParameter/expression — `grep human_approved SP-OP-AUTH-001_*.bpmn` = 0 hits), so the guards
were UNSATISFIABLE: the modeled auto-L2 issuance and every genuinely-human APROVAR/JUNTA/NEGAR
route dead-ended in a blocked_by_guard record and `numero_autorizacao` was never populated.
The guards now derive human provenance from the ENGINE-VISIBLE human-decision evidence — the
same idiom every sibling family's gated worker uses (cred/pagto/recurso ground their guards in
the decision literal + accountability fields, never a phantom flag):
- `decisao_auditor` is set ONLY by the three human User Tasks (formData enum on
  UT_AnaliseMedicoAuditor / UT_CoordenacaoAssume / UT_RegistrarParecerJunta;
  GW_DecisaoAuditor's default fail-closes invalid/absent to End_ErrDecisaoInvalida) — it IS the
  human decision.
- the L2 auto-issuance route is sanctioned by BRT_AutoApproval's OWN result — the exact term
  Flow_GW_AutoAprovar's condition reads (`auto_aprovacao.recomendacao == 'AUTO_APROVAR'`),
  delivered to the worker FLATTENED as the task-local String `auto_aprovacao_recomendacao`
  (item-9 auto-L2 fix — the raw `singleResult` Map does not survive `fetchAndLock`; see
  `_auto_approval_sanctioned`); issuing a favorable authorization on that modeled route is L2 by
  design (ADR-0008), not an adverse act.
- an explicit `human_approved is True` remains accepted as the strongest signal (unchanged
  fail-closed pin: ONLY the boolean True — never truthy junk).
The resolved provenance is threaded back into the issuing/denial outputs as an engine-visible
`human_approved` variable; `convene_junta` deliberately does NOT write it (it runs BEFORE
UT_RegistrarParecerJunta — a worker-persisted True would poison the downstream denial guard).
"""

from __future__ import annotations

import math
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import structlog

from maezo.gateway.required_text import ZERO_WIDTH_CHARS, ZERO_WIDTH_TRANSLATION, is_blank
from maezo.platform.observability import record_worker_error
from maezo.tools.workers.auth_criteria import CriteriaSources, criteria_sources
from maezo.tools.workers.base import (
    ERR_AUTH_AUTO_CEILING_NOT_AUTHORIZED,
    ERR_AUTH_DENIAL_INCOMPLETE,
    ERR_DENIAL_NOT_HUMAN,
    WorkerBase,
)
from maezo.tools.workers.ceilings import CeilingResolver
from maezo.tools.workers.dmn_transport import DmnTransport, evaluate_sync, first_row
from maezo.tools.workers.harness import WorkerBpmnError
from maezo.tools.workers.phi_vars import redact_phi_vars

_MODULE_LOGGER = structlog.get_logger(__name__)

# Governance ceiling for AUTH L2 auto-approval (design T1.9 §2.4). The teto VALUE lives in
# the autonomy matrix (`authorization_approval.max_value_brl`, L0-core.yaml:22 +
# tenants-amh.yaml overlay), resolved via the SAME loader the PEP uses.
_CEILING_ACTION = "authorization_approval"
_CEILING_PARAM = "max_value_brl"

# Bounded, non-PHI reason tokens written back as engine-visible evidence when the AUTOMATIC
# issuance channel is refused by the ceiling gate (`IssueAuthorizationWorker`). They tell an
# auditor reading process history WHICH fail-closed vector fired, without any free text.
_CEILING_BLOCK_TENANT: str = "TENANT_AUSENTE"
_CEILING_BLOCK_VALOR: str = "VALOR_AUSENTE_OU_INVALIDO"
_CEILING_BLOCK_TETO: str = "TETO_NAO_AUTORIZA"
_CEILING_BLOCK_RESOLVER: str = "RESOLVER_INDISPONIVEL"

# ANS-required grounding fields for a *negativa fundamentada* (RN 395 art. 10). These are exactly
# the three fields SP-OP-AUTH-001 marks `requiredIf="decisao_auditor == NEGAR"` +
# `enforcedBy="worker send_denial_notice guard ERR_AUTH_DENIAL_INCOMPLETE"` on each of its three
# human decision tasks (UT_AnaliseMedicoAuditor / UT_CoordenacaoAssume / UT_RegistrarParecerJunta
# — 9 formField annotations, 3 distinct fields). The BPMN is the source of truth for WHAT must be
# present; this list mirrors it exactly (and the donor's own `send_denial_notice` completeness
# check). All three are PHI-named (see `phi_vars.PHI_PROCESS_VARS`).
_REQUIRED_DENIAL_FIELDS: tuple[str, ...] = (
    "justificativa_clinica",
    "cid10_referencia",
    "fundamentacao_dut",
)

# Spec-modeled BPMN error codes the auth workers raise. Registered in the auth harness's
# `bpmn_error_allowlist` so the harness dispatches them as real `bpmnError`s (caught by the BPMN's
# own boundary events) instead of demoting them to fail-closed incidents (harness.py §9). Only
# `ERR_AUTH_DENIAL_INCOMPLETE` is here — it is the ONE auth code with a matching
# `bpmn:error@errorCode` + boundary event (`BE_NegativaIncompleta` on `ST_EnviarNegativaFormal`)
# in SP-OP-AUTH-001. `ERR_DENIAL_NOT_HUMAN` is deliberately NOT allowlisted: the BPMN declares no
# boundary for it, so it must stay a fail-closed incident, never a silently-ended scope.
# `ERR_AUTH_AUTO_CEILING_NOT_AUTHORIZED` is likewise NOT allowlisted, and is never RAISED at all:
# `ST_EmitirAutorizacaoAuto` (the only task the ceiling gate can fire on) declares NO boundary
# event — the file's only boundary is `BE_NegativaIncompleta` on `ST_EnviarNegativaFormal` — so a
# `bpmnError` there would silently end the process scope (ADR-0030 live-verified hazard). The
# ceiling refusal is a RETURNED `blocked_by_guard` record, like `ERR_DENIAL_NOT_HUMAN`.
AUTH_BPMN_ERROR_ALLOWLIST: frozenset[str] = frozenset({ERR_AUTH_DENIAL_INCOMPLETE})

if TYPE_CHECKING:
    from maezo.tools.workers.harness import KafkaPublisher, WorkerHarness


def _norm_decision(value: Any) -> str:
    """Normalize `decisao_auditor` for guard checks — FAIL-CLOSED.

    Mirrors `pagto._norm_str`/`credenciamento._norm_str`: a `str` normalizes to `.strip()`
    (whitespace-only becomes "" — no decision); a NON-string (None, int, bool, list, dict —
    engine variables arrive untyped) normalizes to "" (never a truthy pass-through, never an
    AttributeError incident). Exact literal comparison downstream — no case folding.
    """
    if isinstance(value, str):
        return value.strip()
    return ""


#: The ONE favorable literal `auth_auto_approval` can emit. `ANALISE_HUMANA` is the DMN's own
#: catch-all; negativa is not a possible output of that table at all (L0 hard).
_AUTO_SANCTION_LITERAL = "AUTO_APROVAR"

#: The FLATTENED auto-L2 sanction: a plain String, TASK-LOCAL to `ST_EmitirAutorizacaoAuto`,
#: produced by that task's `camunda:inputParameter auto_aprovacao_recomendacao =
#: ${auto_aprovacao.recomendacao}` — the exact term `Flow_GW_AutoAprovar`'s condition just
#: evaluated. Mirrors SP-OP-REEMBOLSO-001's `ST_IssuePaymentAuto`
#: (`valor_reembolso_aprovado_cents = ${calculo.valor_calculado_tabela_cents}`), the repo's
#: existing idiom for handing a DMN `singleResult` field to an external worker.
_AUTO_SANCTION_FLAT_VAR = "auto_aprovacao_recomendacao"


def _auto_approval_sanctioned(process_vars: dict[str, Any]) -> bool:
    """True iff BRT_AutoApproval's DMN result sanctions the modeled L2 auto route — FAIL-CLOSED.

    ROOT CAUSE THIS CLOSES (item-9, live-caught on CIB Seven 2.1.0): `auto_aprovacao` is
    BRT_AutoApproval's `camunda:resultVariable` under `mapDecisionResult="singleResult"` — a
    JAVA Map, stored as an `Object`-typed variable. `fetchAndLock`'s per-topic
    `deserializeValues` defaults to FALSE and `CibSevenWorkerTransport.fetch_and_lock` sends no
    such flag (harness.py `fetch_and_lock`), while `_from_camunda_var` re-decodes ONLY
    `type == "Json"` — so the Map reaches this worker as an opaque, non-`Mapping` value. The
    engine's own `GW_AutoAprovacao` had already routed on
    `${auto_aprovacao.recomendacao == 'AUTO_APROVAR'}` (JUEL, evaluated engine-side on the LIVE
    object), so the DMN HAD sanctioned — yet this guard refused, `numero_autorizacao` was never
    written, and the process still published `auth.completed desfecho=aprovada_automatica`.

    THE CHANNEL, in order:
      1. `auto_aprovacao_recomendacao` — the flattened String the model now delivers
         (`_AUTO_SANCTION_FLAT_VAR`). This is the channel that actually works on a real engine.
      2. `auto_aprovacao` as a real `Mapping` — belt-and-braces, kept verbatim for the in-process
         fixtures and for any engine/config that DOES deserialize the Map.
    Both require exactly the literal `AUTO_APROVAR` (surrounding whitespace tolerated — the
    pre-existing Mapping-branch semantics, applied identically to the flat branch; no case
    folding, no prefix match). Anything else — absent, blank, wrong literal, wrong type,
    `ANALISE_HUMANA` — is NO sanction. The accepted value set is UNCHANGED; only the delivery
    shape widened.

    NOT SPOOFABLE AT START: `auto_aprovacao_recomendacao` is an activity-LOCAL variable written
    by the input mapping on every entry into `ST_EmitirAutorizacaoAuto`, from `auto_aprovacao`,
    which BRT_AutoApproval (the ONLY predecessor of `GW_AutoAprovacao`) always overwrites first.
    A local variable shadows any process-scope homonym a `start_process` payload could seed, so
    the auto route reads the engine's own freshly-computed sanction and nothing else. The only
    other task on this topic — `ST_EmitirAutorizacaoAuditor` — declares no input mapping and is
    reachable only behind `${decisao_auditor == 'APROVAR'}`, which already sanctions issuance
    through the human channel, so a seeded homonym cannot open any path that gate did not.
    """
    flat = process_vars.get(_AUTO_SANCTION_FLAT_VAR)
    if isinstance(flat, str) and flat.strip() == _AUTO_SANCTION_LITERAL:
        return True
    auto = process_vars.get("auto_aprovacao")
    if not isinstance(auto, Mapping):
        return False
    recomendacao = auto.get("recomendacao")
    return isinstance(recomendacao, str) and recomendacao.strip() == _AUTO_SANCTION_LITERAL


def _ceiling_valor_cents(value: Any) -> int | None:
    """Centavos (`int`) from `valor_estimado_brl` for the ceiling check — FAIL-CLOSED, or None.

    None means "this value cannot be trusted as a money amount" and the ONLY safe reading of that
    at the issuance chokepoint is REFUSE. Rejected: absent/None; `bool` (a subclass of `int` —
    `True` would otherwise coerce to R$1,00); anything that is not an `int`/`float`/`str` (the
    engine's only money-carrying wire types are Long/Double/String — a `bytes`, a list or a dict
    is not a value this gate may guess at, even where `float()` happens to accept it); an
    unparseable string; NaN and +/-inf (and any value whose centavos overflow to inf); and any
    NEGATIVE amount (nonsense input that would trivially sit "within" every positive teto).

    Shared by every ceiling-consuming call site — `_criterio_financeiro`,
    `AnalyzeRequestWorker.execute` (`ST_PrepararDossie`), and
    `IssueAuthorizationWorker._auto_ceiling_verdict` (`ST_EmitirAutorizacaoAuto`) — rather than
    re-derived per site. `AnalyzeRequestWorker.execute` used to have its own laxer, ad hoc
    derivation (`round()` instead of `ceil()`, no bool/NaN/inf/negative rejection, and an
    uncaught `OverflowError` on `valor_estimado_brl="inf"`); that was a second place for the
    GK-ceiling rounding defect (finding 2, below) to recur in, so it now reuses this function
    too. Safe to share even though one consumer only routes to human review on rejection (dossier
    path, fail-closed to `teto_ok=False`) while another would otherwise MINT an authorization
    number: this function's rejection set is a strict superset of what either call site needs, so
    the stricter shared behaviour never opens a path either consumer would have kept closed.
    """
    # Portal AUTH uses the independently installed exact native money serializer.
    # Only its explicit hydration type selects this additive integer arithmetic path.
    from .auth_exact_amount import AuthExactAmount

    if type(value) is AuthExactAmount:
        return value.cents
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        return None
    try:
        cents = float(value) * 100.0
    except (TypeError, ValueError):
        return None
    if not math.isfinite(cents) or cents < 0:
        return None
    # GK-ceiling finding 2: CEIL, never round. `round()` fails in the PERMISSIVE direction across
    # the teto boundary — at a R$500 teto, 500.001/500.004/500.005 all round to exactly 50000 and
    # would ISSUE. Tiny in money (<= R$0.005) but it is the wrong direction for a function whose
    # posture is "never issue on uncertainty". `ceil` can never deny a legitimate value: it only
    # moves amounts already STRICTLY above an integer centavo up to the next one.
    return math.ceil(cents)


# ===========================================================================
# ValidateAutoCriteriaWorker — the GAP-AUTH-4 criteria gate
# ===========================================================================

#: DMN decision keys the criteria gate evaluates through the ADR-0028 `dmn=` worker seam.
#: NONE of these is a BPMN businessRuleTask decisionRef (they stay listed in
#: `spec/processes/dmn/orphans-allowlist.yaml` as PERMANENT, worker-consumed orphans — the same
#: shape the 7 `fraude_scoring/*` tables already use).
_DMN_DUT_ROL_COVERAGE = "dut_rol_coverage"
_DMN_CARENCIA_CHECK = "carencia_check"
_DMN_CRITERIA_CONTRATUAL = "auth_criteria_contratual"

#: Bounded, non-PHI FAILURE tokens. Closed enum — free text NEVER appears in
#: `auto_criteria_falhas`. Every one matches `harness._ENUM_TOKEN_RE`
#: (`^[A-Za-z][A-Za-z0-9_]{0,39}$`) so the primary one can travel in the clear into the durable
#: ADR-0007 audit chain via `motivo_bloqueio_criterios`.
#: FINANCEIRO — source is the governance autonomy matrix (already ratified, CODEOWNERS-gated).
_FIN_TENANT_AUSENTE = "FINANCEIRO_TENANT_AUSENTE"
_FIN_VALOR_INVALIDO = "FINANCEIRO_VALOR_INVALIDO"
#: Valor ZERO — distinto de invalido, e o motivo esta medido no lago (25/08/2026).
#:
#: `vl_procedimento` em `amh_omni_gold.guias_procedimento`: 16.865.850 itens, dos quais apenas
#: 113.031 (0,67%) tem valor maior que zero. Medido tambem por status do item (0,3% a 4,4%) e
#: por data de liberacao (0,64% sem, 0,70% com) — ou seja, o valor NAO aparece depois, ele
#: simplesmente nao e' registrado.
#:
#: Consequencia que so' aparece quando o teto sai do zero: `within_l2_ceiling` compara
#: `valor <= teto`, entao um zero satisfaz QUALQUER teto positivo. Com o teto em R$ 500, 99,33%
#: das guias passariam o criterio financeiro por AUSENCIA DE DADO — nao por serem baratas. O
#: portao pareceria avaliado e nao estaria.
#:
#: POR QUE AQUI E NAO EM `_ceiling_valor_cents`: aquela primitiva declara, com teste explicito
#: (`test_ceiling_valor_cents_matrix`, entrada `(0, 0)`), que "a zero value is TRUSTWORTHY;
#: whether it issues is the teto's call". E' decisao deliberada e compartilhada com pagto,
#: reembolso e o PEP de efeitos. Muda-la aqui alteraria tres dominios de uma vez para resolver
#: um problema de UM. A leitura de que zero significa "nao precificado" e' conhecimento da
#: autorizacao previa, e e' onde ela mora.
_FIN_VALOR_ZERO = "FINANCEIRO_VALOR_NAO_PRECIFICADO"
_FIN_RESOLVER_INDISPONIVEL = "FINANCEIRO_RESOLVER_INDISPONIVEL"
_FIN_TETO_NAO_AUTORIZA = "FINANCEIRO_TETO_NAO_AUTORIZA"
#: TECNICO — DUT/ROL coverage + the procedure-specific clinical criteria table.
_TEC_FONTE_NAO_RATIFICADA = "TECNICO_FONTE_NAO_RATIFICADA"
_TEC_ENTRADA_AUSENTE = "TECNICO_ENTRADA_AUSENTE"
_TEC_TABELA_INDISPONIVEL = "TECNICO_TABELA_INDISPONIVEL"
_TEC_PROCEDIMENTO_NAO_MAPEADO = "TECNICO_PROCEDIMENTO_NAO_MAPEADO"
_TEC_FORA_DO_ROL = "TECNICO_FORA_DO_ROL"
_TEC_DUT_NAO_ATENDIDA = "TECNICO_DUT_NAO_ATENDIDA"
#: REGULATORIO — carencia/CPT.
_REG_FONTE_NAO_RATIFICADA = "REGULATORIO_FONTE_NAO_RATIFICADA"
_REG_ENTRADA_AUSENTE = "REGULATORIO_ENTRADA_AUSENTE"
_REG_TABELA_INDISPONIVEL = "REGULATORIO_TABELA_INDISPONIVEL"
_REG_CARENCIA_NAO_CUMPRIDA = "REGULATORIO_CARENCIA_NAO_CUMPRIDA"
#: CONTRATUAL — milestones/regras/KPI.
_CON_FONTE_NAO_RATIFICADA = "CONTRATUAL_FONTE_NAO_RATIFICADA"
_CON_TABELA_INDISPONIVEL = "CONTRATUAL_TABELA_INDISPONIVEL"
_CON_SEM_FONTE = "CONTRATUAL_SEM_FONTE"
#: DEGRADATION — the validator itself could not run to completion (design §6).
_VALIDADOR_INDISPONIVEL = "VALIDADOR_INDISPONIVEL"

#: Bounded, non-PHI SHADOW tokens: what an UNRATIFIED table WOULD have decided, recorded as
#: ratification evidence for the medico-auditor/ANS reviewer WITHOUT ever influencing the
#: verdict (design §3). Never emitted for a ratified source — once ratified the table's verdict
#: IS the verdict, so a shadow of it would be noise.
_SOMBRA_APROVARIA = "_SOMBRA_APROVARIA"
_SOMBRA_REPROVARIA = "_SOMBRA_REPROVARIA"
_SOMBRA_INDETERMINADO = "_SOMBRA_INDETERMINADO"

#: Deterministic scan order for `motivo_bloqueio_criterios` — the SINGLE bounded token that
#: reaches the durable audit chain (`harness._SAFE_DECISION_BASIS_KEYS` admits scalars only, so
#: the `auto_criteria_falhas` LIST cannot travel there; this mirrors `motivo_bloqueio_teto`).
_CRITERIA_ORDER: tuple[str, ...] = ("tecnico", "financeiro", "regulatorio", "contratual")

#: Failure tokens that mean "a mechanism was unavailable", as opposed to "the rule said no".
#: These — and ONLY these — also increment the `maezo_worker_error_count_total` metric
#: (`platform.observability.record_worker_error`), so a validator/engine/config outage is
#: VISIBLE in monitoring instead of silently degrading every request to human review. The
#: label set is this closed enum, so metric cardinality stays bounded.
_UNAVAILABILITY_TOKENS: frozenset[str] = frozenset(
    {
        _VALIDADOR_INDISPONIVEL,
        _FIN_RESOLVER_INDISPONIVEL,
        _TEC_TABELA_INDISPONIVEL,
        _REG_TABELA_INDISPONIVEL,
        _CON_TABELA_INDISPONIVEL,
    }
)


def _as_bool(value: Any) -> bool | None:
    """A DMN boolean output as a real `bool`, or None (=> indeterminate => fail closed).

    FAIL-CLOSED and deliberately strict: only the Python literals `True`/`False` count. A
    string `"true"`, an int `1`, `None`, or any other shape means the table did not give this
    gate a boolean it may act on — the caller treats that as "criterion not met", never as a
    truthy pass-through. Mirrors the `human_approved is True` pin idiom used throughout auth.
    """
    return value if isinstance(value, bool) else None


def _nonblank_str(value: Any) -> str | None:
    """`value.strip()` when it is a non-blank `str`, else None. Engine variables arrive untyped."""
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


@dataclass(frozen=True)
class _CriterionOutcome:
    """One criterion's verdict plus its bounded evidence.

    Attributes:
        ok: True ONLY when the criterion is satisfied AND its rule source is ratified.
        falhas: bounded failure tokens explaining an `ok=False` (empty when ok).
        sombra: bounded shadow tokens — what an UNRATIFIED table would have decided. Recorded
            as evidence only; `ok` is computed without ever consulting it.
    """

    ok: bool
    falhas: tuple[str, ...] = ()
    sombra: tuple[str, ...] = ()


class ValidateAutoCriteriaWorker(WorkerBase):
    """External task: `operadora.auth.validate_auto_criteria` (`ST_ValidateAutoApprovalCriteria`).

    THE DEFECT THIS CLOSES (GAP-AUTH-4). `BRT_AutoApproval` used to grant `AUTO_APROVAR` on
    `dut_atendida ∧ dentro_teto_l2 ∧ rede_credenciada` — three booleans that arrived SEEDED in
    the start payload with **no code computing any of them on the automatic route**. The only
    AUTH caller of `CeilingResolver` (`AnalyzeRequestWorker`, `ST_PrepararDossie`) sits on the
    HUMAN leg, *after* `GW_AutoAprovacao`. The root pathology was that **absence of validation
    read as an implicit PASS**. This worker inverts that: it runs BETWEEN `BRT_SlaAnalise` and
    `BRT_AutoApproval` (mirroring SP-OP-REEMBOLSO-001's `BRT_Calculo → ST_CalculateAmount →
    BRT_AutoApproval`, GAP-REEMBOLSO-5) and COMPUTES four criteria the rewired DMN now requires:

      - `criterio_tecnico_ok`      — `dut_rol_coverage` (+ the procedure-specific
                                     `dut_criteria_*` table when the coverage row demands a DUT)
      - `criterio_financeiro_ok`   — `CeilingResolver.within_l2_ceiling`
                                     (`authorization_approval.max_value_brl`)
      - `criterio_regulatorio_ok`  — `carencia_check` (carencia/CPT)
      - `criterio_contratual_ok`   — `auth_criteria_contratual` (milestones/regras/KPI)

    plus `auto_criteria_verificado: True` — PROOF OF EXECUTION, which rule r1 of the rewired
    `auth_auto_approval.dmn` requires. That flag is the fence GAP-AUTH-4 lacked: skip this task
    and the token falls to the DMN catch-all -> `ANALISE_HUMANA` -> human review.

    THE RATIFICATION GATE (the safety crux). Three of the four criteria are decided by DMN
    tables whose clinical/regulatory/contractual content is SYNTHETIC and DRAFT. Wiring them
    naively would let synthetic rules grant REAL authorizations — strictly worse than the
    unverified seeds, because it would *look* validated. So: **a criterion may contribute a PASS
    only if its rule source is RATIFIED** in `spec/processes/dmn/auth-criteria-ratification.yaml`
    (`auth_criteria.py`, mirroring the DPO retention-matrix loader that refuses its own
    unratified template). A DRAFT source yields `false` with `*_FONTE_NAO_RATIFICADA`,
    **regardless of what the table computes**.

    SHADOW MODE. Unratified tables are still EVALUATED, and what they *would* have decided is
    recorded in `auto_criteria_shadow` as bounded, non-PHI tokens — so the medico-auditor/ANS
    reviewer ratifies against real outcome data instead of reviewing rules in the abstract.
    Shadow output is written AFTER every verdict is computed and is never read back:
    `_CriterionOutcome.ok` is derived without consulting `.sombra` anywhere.

    OBSERVABLE BEHAVIOUR TODAY — stated plainly. Decision D-07 CLOSED on 2026-08-25:
    `authorization_approval.max_value_brl` now carries a real, positive ceiling in
    `tenants-amh.yaml`, so `criterio_financeiro_ok` can legitimately return `True` for a request
    priced within it (see `auth.py:484-490`). What still keeps **every request routing to human
    review** is the three remaining criteria — technical/regulatory/contractual read from
    clinical tables that are DRAFT/unratified, and an unratified source returns `false`
    regardless of what the table computes. NOTHING auto-approves while they stay that way. That
    is the same safe outcome as before this worker existed — but
    now for explicit, auditable, per-criterion reasons instead of an unverified seed, and each
    criterion switches on independently as its source is ratified/populated, with NO code change.

    INVARIANTS.
      1. NEVER auto-denies. This gate chooses auto-approve vs human review; no deny output
         exists here or downstream in `auth_auto_approval.dmn` (L0 hard, ADR-0005/0008).
      2. FAIL-CLOSED on unknown/unverifiable/unratified — always toward human review.
      3. DETERMINISTIC, never an LLM. Rafael keeps consuming DMN results, never computing them.
      4. NEVER invents a clinical/regulatory/contractual VALUE. Ceilings, DUT/ROL, carencia,
         plan terms and KPI targets stay SME/owner data; the dut_ref -> criteria-table routing
         is SME-declared in the manifest, not guessed here.
      5. COMPUTED OVERWRITES SEEDED: all five criteria variables are written on EVERY path
         (including degradation), so a start-payload homonym is always destroyed.
      6. NEVER raises a `WorkerBpmnError`. `ST_ValidateAutoApprovalCriteria` declares NO error
         boundary event (the AUTH file's only boundary is `BE_NegativaIncompleta` on
         `ST_EnviarNegativaFormal`), and an unmodeled `bpmnError` silently ENDS the process
         scope on CIB Seven 2.1.0 (ADR-0030, live-verified). This worker only RETURNS.

    DEGRADATION (design §6) — fail-safe AND visible. An unexpected error anywhere returns all
    four criteria false plus `VALIDADOR_INDISPONIVEL`, with an `error` log line, a
    `maezo_worker_error_count_total` increment and an audit token. It is deliberately NOT an
    incident: an incident would STALL a care-authorization request, which is worse for the
    beneficiary than routing it to a human auditor. Same reasoning `ceilings.py` documents for
    `CeilingResolver` swallowing a config failure to `False`.
    """

    def __init__(
        self,
        resolver: CeilingResolver | None = None,
        dmn: DmnTransport | None = None,
        sources: CriteriaSources | None = None,
    ) -> None:
        # max_retries=1: every branch here is DETERMINISTIC (a DMN no-match, an unratified
        # source, a missing input) — an in-process retry with sleeps changes nothing and only
        # delays the request. Transient engine faults degrade to human review by design (above),
        # so there is nothing for the retry loop to rescue either.
        super().__init__(topic="operadora.auth.validate_auto_criteria", max_retries=1)
        # Same injection seam as `AnalyzeRequestWorker`/`IssueAuthorizationWorker`: the default
        # resolves the REAL `spec/policies/autonomy` matrix through the same loader the PEP uses.
        self._resolver = resolver if resolver is not None else CeilingResolver()
        # ADR-0028 `dmn=` seam, threaded from `register_auth_workers(**seams)`. Deliberately NOT
        # guarded with `require_dmn`: that raises `DmnEvaluationError` -> transient -> engine
        # retry -> incident, and an incident here stalls the authorization. An unwired seam
        # instead fails each DMN-backed criterion closed with its own `*_TABELA_INDISPONIVEL`.
        self._dmn = dmn
        # Injectable for tests; production reads the cached manifest (`criteria_sources()`).
        self._sources_override = sources

    # -- rule sources -------------------------------------------------------------------

    def _sources(self) -> CriteriaSources:
        return self._sources_override if self._sources_override is not None else criteria_sources()

    def _evaluate_dmn(self, decision_key: str, variables: dict[str, Any]) -> dict[str, Any]:
        """Evaluate one decision table engine-side. Raises on ANY failure — callers fail closed.

        Deliberately thin: `evaluate_sync` records the authoritative `DmnVersion` into the audit
        collector (ADR-0028 §5) and `first_row` turns an empty result into `DmnNoResultError`
        rather than the fail-OPEN `{}` the dead local evaluator used to return.
        """
        if self._dmn is None:
            raise RuntimeError(f"dmn transport seam not wired for `{self.topic}` (ADR-0028)")
        rows, _version = evaluate_sync(self._dmn, decision_key, variables)
        return first_row(rows, decision_key, variables)

    # -- criterion: FINANCEIRO ----------------------------------------------------------

    def _criterio_financeiro(self, process_vars: dict[str, Any]) -> _CriterionOutcome:
        """Tenant governance ceiling — the ONE criterion whose source is already ratified.

        `authorization_approval.max_value_brl` lives in the CODEOWNERS-gated autonomy matrix
        (`spec/policies/autonomy/L0-core.yaml` + `tenants-amh.yaml`), resolved through the SAME
        `CeilingResolver.within_l2_ceiling(action, param)` call `AnalyzeRequestWorker` already
        makes — so there is no DMN table to ratify and no shadow to record.

        TETO DEFINIDO EM 25/08/2026: R$ 500 (`tenants-amh.yaml`), fechando a decisao D-07 que
        estava aberta. Antes disso o teto era 0 e `within_l2_ceiling` fail-closava tudo, inclusive
        um pedido de valor 0 — nada era financeiramente elegivel, por construcao.

        DUAS GUARDAS, e a segunda so' passou a ser necessaria quando o teto saiu do zero:
          - valor nao confiavel (ausente, bool, nao numerico, NaN/inf, negativo) -> REFUSE, via
            `_ceiling_valor_cents`;
          - valor ZERO -> REFUSE, aqui, porque no lago 99,33% das guias nao tem valor e um zero
            satisfaz qualquer teto positivo. Ver `_FIN_VALOR_ZERO` para os numeros medidos.
        Sem a segunda, subir o teto para R$ 500 teria convertido o criterio financeiro de
        "reprova tudo" para "aprova quase tudo", sobre dado que nao existe.

        Centavos come from `_ceiling_valor_cents` — the STRICT derivation already in this module
        (rejects bool, non-numeric, unparseable, NaN/inf, negative; CEILs rather than rounds, so
        it can never fail OPEN across the teto boundary). Reused deliberately: a second
        derivation would be a second place for the rounding defect GK-ceiling found.
        """
        tenant = _nonblank_str(process_vars.get("tenant_id"))
        if tenant is None:
            return _CriterionOutcome(ok=False, falhas=(_FIN_TENANT_AUSENTE,))

        valor_cents = _ceiling_valor_cents(process_vars.get("valor_estimado_brl"))
        if valor_cents is None:
            return _CriterionOutcome(ok=False, falhas=(_FIN_VALOR_INVALIDO,))
        if valor_cents == 0:
            # Um pedido sem preco nao e' um pedido barato — ver `_FIN_VALOR_ZERO`. Sem esta
            # linha, o teto de R$ 500 aprovaria 99,33% das guias sem olhar dinheiro nenhum.
            # Vai para o auditor, que e' a leitura conservadora e a que o resto da repo adota.
            return _CriterionOutcome(ok=False, falhas=(_FIN_VALOR_ZERO,))

        try:
            within = self._resolver.within_l2_ceiling(
                tenant=tenant,
                action=_CEILING_ACTION,
                param=_CEILING_PARAM,
                value_cents=valor_cents,
            )
        except Exception as exc:  # fail-closed: resolver down never auto-approves
            self.logger.error("auth_auto_criteria_resolver_failed", tenant_id=tenant, error=str(exc))
            return _CriterionOutcome(ok=False, falhas=(_FIN_RESOLVER_INDISPONIVEL,))

        # `is not True` (not `not within`): a stub/resolver returning a truthy non-bool must not
        # open an automatic approval — the same pin `IssueAuthorizationWorker` applies.
        if within is not True:
            return _CriterionOutcome(ok=False, falhas=(_FIN_TETO_NAO_AUTORIZA,))
        return _CriterionOutcome(ok=True)

    # -- criterion: TECNICO -------------------------------------------------------------

    def _criterio_tecnico(self, process_vars: dict[str, Any], sources: CriteriaSources) -> _CriterionOutcome:
        """DUT/ROL coverage, plus the procedure-specific clinical criteria table when required.

        Steps, each failing closed:
          1. `codigo_procedimento_tuss` must be a non-blank string (the coverage table's key).
          2. evaluate `dut_rol_coverage` -> `no_rol` / `requer_dut` / `dut_ref`.
          3. `no_rol=true` (outside the ANS Rol) -> not technically eligible for AUTOMATIC
             approval. This is NOT a denial: the request goes to the medico auditor, who may
             still approve it (the table itself carries the same L0-hard invariant).
          4. `requer_dut=true` -> resolve the procedure's `dut_criteria_*` table through the
             SME-declared `mapeamento_dut_criteria`. An unmapped ref fails closed
             (`TECNICO_PROCEDIMENTO_NAO_MAPEADO`) — guessing which clinical criteria table
             applies to a DUT would be inventing a clinical judgement.
          5. RATIFICATION LAST: the verdict from steps 3-4 becomes the criterion ONLY if EVERY
             table consulted is ratified. Otherwise the criterion is false with
             `TECNICO_FONTE_NAO_RATIFICADA` and the would-be verdict is recorded as shadow.
        """
        codigo = _nonblank_str(process_vars.get("codigo_procedimento_tuss"))
        if codigo is None:
            return _CriterionOutcome(ok=False, falhas=(_TEC_ENTRADA_AUSENTE,))
        categoria = _nonblank_str(process_vars.get("categoria_procedimento")) or ""

        try:
            cobertura = self._evaluate_dmn(
                _DMN_DUT_ROL_COVERAGE,
                {"codigo_procedimento_tuss": codigo, "categoria_procedimento": categoria},
            )
        except Exception as exc:  # fail-closed: unreachable table never approves
            self.logger.error("auth_auto_criteria_dmn_failed", decision=_DMN_DUT_ROL_COVERAGE, error=str(exc))
            return _CriterionOutcome(ok=False, falhas=(_TEC_TABELA_INDISPONIVEL,))

        consulted = [_DMN_DUT_ROL_COVERAGE]
        no_rol = _as_bool(cobertura.get("no_rol"))
        requer_dut = _as_bool(cobertura.get("requer_dut"))

        # The would-be verdict, computed WITHOUT any reference to ratification.
        would: bool | None
        motivo: tuple[str, ...]
        if no_rol is None or requer_dut is None:
            would, motivo = None, (_TEC_TABELA_INDISPONIVEL,)
        elif no_rol:
            would, motivo = False, (_TEC_FORA_DO_ROL,)
        elif not requer_dut:
            would, motivo = True, ()
        else:
            criteria_key = sources.criteria_table_for(cobertura.get("dut_ref"))
            if criteria_key is None:
                would, motivo = None, (_TEC_PROCEDIMENTO_NAO_MAPEADO,)
            else:
                consulted.append(criteria_key)
                try:
                    # The clinical inputs are whatever the specific table declares; they reach
                    # this worker as ordinary process variables. Passing the whole variable map
                    # is NOT an option (PHI egress + wrong-type coercion, dmn_transport's
                    # `_to_camunda_vars` caveat), so only the table's own declared inputs are
                    # forwarded — resolved from the table itself, never a hardcoded list here.
                    criteria_row = self._evaluate_dmn(
                        criteria_key, _dut_criteria_inputs(criteria_key, process_vars)
                    )
                except Exception as exc:  # fail-closed
                    self.logger.error("auth_auto_criteria_dmn_failed", decision=criteria_key, error=str(exc))
                    return _CriterionOutcome(ok=False, falhas=(_TEC_TABELA_INDISPONIVEL,))
                atendida = _as_bool(criteria_row.get("dut_atendida"))
                if atendida is None:
                    would, motivo = None, (_TEC_TABELA_INDISPONIVEL,)
                elif atendida:
                    would, motivo = True, ()
                else:
                    would, motivo = False, (_TEC_DUT_NAO_ATENDIDA,)

        return _gate_on_ratification(
            would=would,
            motivo=motivo,
            consulted=consulted,
            sources=sources,
            unratified_token=_TEC_FONTE_NAO_RATIFICADA,
            shadow_prefix="TECNICO",
        )

    # -- criterion: REGULATORIO ---------------------------------------------------------

    def _criterio_regulatorio(
        self, process_vars: dict[str, Any], sources: CriteriaSources
    ) -> _CriterionOutcome:
        """Carencia / CPT via `carencia_check`.

        INPUTS THAT DO NOT EXIST YET — stated plainly. `carencia_check` declares
        `tipo_procedimento`, `dias_desde_adesao` and `cpt_declarada`, and its own description
        says they are "pre-computados por worker deterministico". NONE of the three is in
        SP-OP-AUTH-001's start contract today: `dias_desde_adesao` needs the beneficiary's
        contract start date from cadastro data behind the AMH boundary (MZO-050b, blocked), and
        deriving `tipo_procedimento` from `carater_atendimento`/`categoria_procedimento` is a
        REGULATORY mapping (which carencia period applies, including `parto`, which cannot be
        derived from either field) that `docs/review-queue.md` explicitly flags for SME
        validation. Inventing it here would violate "never invent a regulatory value".

        So this criterion reads the three inputs directly and fails closed with
        `REGULATORIO_ENTRADA_AUSENTE` when they are absent — which is the state today. That is
        a correct outcome, not a stub: the seam is real, the table is really evaluated the
        moment the inputs exist, and until then the request routes to a human.

        The inbound `carencia_cumprida` seed is deliberately NOT consulted and NOT overwritten:
        it is a DIFFERENT variable with a different (unverified) provenance, consumed upstream
        by `auth_admissibility`. This gate publishes its own unambiguous `criterio_regulatorio_ok`
        rather than silently redefining a homonym that other readers already interpret.
        """
        tipo = _nonblank_str(process_vars.get("tipo_procedimento"))
        dias_raw = process_vars.get("dias_desde_adesao")
        # bool is a subclass of int — `True` must never coerce to "1 day since enrolment".
        dias = dias_raw if isinstance(dias_raw, int) and not isinstance(dias_raw, bool) else None
        cpt = _as_bool(process_vars.get("cpt_declarada"))
        if tipo is None or dias is None or cpt is None or dias < 0:
            return _CriterionOutcome(ok=False, falhas=(_REG_ENTRADA_AUSENTE,))

        try:
            row = self._evaluate_dmn(
                _DMN_CARENCIA_CHECK,
                {"tipo_procedimento": tipo, "dias_desde_adesao": dias, "cpt_declarada": cpt},
            )
        except Exception as exc:  # fail-closed
            self.logger.error("auth_auto_criteria_dmn_failed", decision=_DMN_CARENCIA_CHECK, error=str(exc))
            return _CriterionOutcome(ok=False, falhas=(_REG_TABELA_INDISPONIVEL,))

        cumprida = _as_bool(row.get("carencia_cumprida"))
        would: bool | None
        motivo: tuple[str, ...]
        if cumprida is None:
            would, motivo = None, (_REG_TABELA_INDISPONIVEL,)
        elif cumprida:
            would, motivo = True, ()
        else:
            would, motivo = False, (_REG_CARENCIA_NAO_CUMPRIDA,)

        return _gate_on_ratification(
            would=would,
            motivo=motivo,
            consulted=[_DMN_CARENCIA_CHECK],
            sources=sources,
            unratified_token=_REG_FONTE_NAO_RATIFICADA,
            shadow_prefix="REGULATORIO",
        )

    # -- criterion: CONTRATUAL ----------------------------------------------------------

    def _criterio_contratual(
        self, process_vars: dict[str, Any], sources: CriteriaSources
    ) -> _CriterionOutcome:
        """Contractual milestones / rules / KPIs via `auth_criteria_contratual`.

        That table is an honest EMPTY seam today: ONE catch-all rule returning
        `criterio_contratual_ok=false` / `motivo="SEM_REGRA_RATIFICADA"`. No contractual
        milestone, rule or KPI target was invented — none exists anywhere in the repo to read
        (SP-OP-AUTH-001 carries no plan/product/contract-terms variable at all, and the KPIs in
        `spec/agents/rafael/agent.yaml` are aspirational prose with zero computing code). When
        the table reports that it has no ratified rule, `CONTRATUAL_SEM_FONTE` records exactly
        that — distinguishable in the audit trail from "a contractual rule was evaluated and
        said no".
        """
        tenant = _nonblank_str(process_vars.get("tenant_id")) or ""
        categoria = _nonblank_str(process_vars.get("categoria_procedimento")) or ""
        try:
            row = self._evaluate_dmn(
                _DMN_CRITERIA_CONTRATUAL,
                {"tenant_id": tenant, "categoria_procedimento": categoria},
            )
        except Exception as exc:  # fail-closed
            self.logger.error(
                "auth_auto_criteria_dmn_failed", decision=_DMN_CRITERIA_CONTRATUAL, error=str(exc)
            )
            return _CriterionOutcome(ok=False, falhas=(_CON_TABELA_INDISPONIVEL,))

        ok_raw = _as_bool(row.get("criterio_contratual_ok"))
        sem_fonte = _nonblank_str(row.get("motivo")) == "SEM_REGRA_RATIFICADA"
        would: bool | None
        motivo: tuple[str, ...]
        if ok_raw is None:
            would, motivo = None, (_CON_TABELA_INDISPONIVEL,)
        elif ok_raw:
            would, motivo = True, ()
        else:
            would, motivo = False, ((_CON_SEM_FONTE,) if sem_fonte else ())

        return _gate_on_ratification(
            would=would,
            motivo=motivo,
            consulted=[_DMN_CRITERIA_CONTRATUAL],
            sources=sources,
            unratified_token=_CON_FONTE_NAO_RATIFICADA,
            shadow_prefix="CONTRATUAL",
        )

    # -- entry point --------------------------------------------------------------------

    def execute(self, process_vars: dict[str, Any]) -> dict[str, Any]:
        """Compute the four auto-approval criteria and the proof-of-execution flag.

        Args:
            process_vars: SP-OP-AUTH-001 variables. Reads `tenant_id`, `valor_estimado_brl`
                (financeiro); `codigo_procedimento_tuss`, `categoria_procedimento` (+ the
                clinical inputs the selected `dut_criteria_*` table declares) (tecnico);
                `tipo_procedimento`, `dias_desde_adesao`, `cpt_declarada` (regulatorio).

        Returns:
            The four `criterio_*_ok` booleans, `auto_criteria_verificado=True`, the bounded
            `auto_criteria_falhas` / `auto_criteria_shadow` lists and the single bounded
            `motivo_bloqueio_criterios` audit token. NEVER raises, NEVER emits a `bpmnError`,
            and NEVER produces a denial.
        """
        try:
            return self._evaluate_criteria(process_vars)
        except Exception as exc:  # degradation (design §6): fail-safe AND visible
            # Fail-safe, not an incident: an incident stalls a care-authorization request, which
            # is worse for the beneficiary than routing it to a human auditor (mirrors DL-0037's
            # fail-neutral-with-disclosed-gap). Logged at error, metered, and audited.
            self.logger.error(
                "auth_auto_criteria_validator_unavailable",
                tenant_id=process_vars.get("tenant_id", ""),
                guia=process_vars.get("numero_guia_tiss", "unknown"),
                error=str(exc),
                error_type=type(exc).__name__,
                reason=(
                    "validador de criterios indisponivel — todos os criterios FALSE, "
                    "encaminha para analise humana (nunca negativa automatica)"
                ),
            )
            self._record_unavailability(_VALIDADOR_INDISPONIVEL)
            return _criteria_result(
                tecnico=_CriterionOutcome(ok=False, falhas=(_VALIDADOR_INDISPONIVEL,)),
                financeiro=_CriterionOutcome(ok=False, falhas=(_VALIDADOR_INDISPONIVEL,)),
                regulatorio=_CriterionOutcome(ok=False, falhas=(_VALIDADOR_INDISPONIVEL,)),
                contratual=_CriterionOutcome(ok=False, falhas=(_VALIDADOR_INDISPONIVEL,)),
                status="validator_unavailable",
            )

    def _record_unavailability(self, token: str) -> None:
        """Increment the worker-error metric for a MECHANISM failure (never a business "no").

        Swallows its own failure: a metrics backend problem must not turn into the incident this
        whole degradation path exists to avoid.
        """
        try:
            record_worker_error(type(self).__name__, self.topic, token)
        except Exception as exc:  # observability must never break the gate
            self.logger.warning("auth_auto_criteria_metric_failed", error=str(exc))

    def _evaluate_criteria(self, process_vars: dict[str, Any]) -> dict[str, Any]:
        sources = self._sources()
        tecnico = self._criterio_tecnico(process_vars, sources)
        financeiro = self._criterio_financeiro(process_vars)
        regulatorio = self._criterio_regulatorio(process_vars, sources)
        contratual = self._criterio_contratual(process_vars, sources)

        result = _criteria_result(
            tecnico=tecnico,
            financeiro=financeiro,
            regulatorio=regulatorio,
            contratual=contratual,
            status="criteria_validated",
        )
        for token in result["auto_criteria_falhas"]:
            if token in _UNAVAILABILITY_TOKENS:
                self._record_unavailability(token)

        # Non-PHI by construction: only bounded tokens and booleans are logged — never a TUSS
        # code, a value, or any clinical input (ADR-0006).
        log = self.logger.info if result["auto_criteria_falhas"] == [] else self.logger.warning
        log(
            "auth_auto_criteria_validated",
            tenant_id=process_vars.get("tenant_id", ""),
            guia=process_vars.get("numero_guia_tiss", "unknown"),
            criterio_tecnico_ok=tecnico.ok,
            criterio_financeiro_ok=financeiro.ok,
            criterio_regulatorio_ok=regulatorio.ok,
            criterio_contratual_ok=contratual.ok,
            falhas=result["auto_criteria_falhas"],
            sombra=result["auto_criteria_shadow"],
        )
        return result


def _dut_criteria_inputs(criteria_key: str, process_vars: dict[str, Any]) -> dict[str, Any]:
    """Forward ONLY the inputs the selected `dut_criteria_*` table declares.

    Explicit per-table input lists, transcribed 1:1 from each table's own `inputExpression`
    `<text>` elements (`spec/processes/dmn/dut_criteria_*.dmn`). Two reasons this is an
    allowlist rather than "pass the whole variable map":

      - PHI egress (ADR-0006): the AUTH variable map carries `justificativa_clinica`,
        `cid10_referencia`, `fundamentacao_dut` and `beneficiario_pseudo_id`; none of them is a
        declared input of any criteria table and none may be shipped to the DMN endpoint.
      - wrong-type coercion: `dmn_transport._to_camunda_vars` types by PYTHON runtime type and
        does not validate against the declared `typeRef`, so forwarding unrelated variables
        risks a rule matching on a wrong-typed value (that function's own documented caveat).

    A value absent from `process_vars` is simply not forwarded; the DMN then evaluates it as
    null, which every one of these FIRST-hit-policy tables resolves through its conservative
    catch-all -> `dut_atendida=false` -> human review. Fail-closed by construction.
    """
    # GK-criteria finding 5: um `dut_ref` mapeado para uma tabela AUSENTE deste dict
    # encaminharia {} silenciosamente para a DMN. O manifesto e vendido ao SME como
    # "mudanca de dados", entao a exigencia das DUAS edicoes tem de ser ruidosa.
    if criteria_key not in _DUT_CRITERIA_DECLARED_INPUTS:
        _MODULE_LOGGER.warning(
            "auth_dut_criteria_inputs_nao_declarados",
            criteria_key=criteria_key,
            motivo="mapeamento no manifesto sem entrada em _DUT_CRITERIA_DECLARED_INPUTS",
        )
    declared = _DUT_CRITERIA_DECLARED_INPUTS.get(criteria_key, ())
    return {name: process_vars[name] for name in declared if name in process_vars}


#: Declared inputs per clinical criteria table — transcribed from the tables' own
#: `inputExpression` texts. Adding a table means adding its row here AND a
#: `mapeamento_dut_criteria` entry in the ratification manifest.
_DUT_CRITERIA_DECLARED_INPUTS: dict[str, tuple[str, ...]] = {
    "dut_criteria_bariatrica": (
        "imc",
        "imc_acima_35_com_comorbidade",
        "imc_acima_40",
        "tentativas_previas_tratamento_clinico",
        "sem_contraindicacao_cirurgica",
        "avaliacao_multidisciplinar_completa",
    ),
    "dut_criteria_oncologia_pet_ct": (
        "diagnostico_oncologico_confirmado",
        "finalidade_pet_ct",
        "tipo_neoplasia_elegivel",
        "exames_convencionais_inconclusivos",
        "solicita_oncologista_ou_nucleo",
    ),
    "dut_criteria_terapias_especiais": (
        "diagnostico_tea_ou_neurodesenvolvimento",
        "tipo_terapia",
        "avaliacao_multidisciplinar_completa",
        "solicitante_habilitado",
        "sessoes_mensais_solicitadas",
        "plano_terapeutico_documentado",
    ),
}


def _gate_on_ratification(
    *,
    would: bool | None,
    motivo: tuple[str, ...],
    consulted: list[str],
    sources: CriteriaSources,
    unratified_token: str,
    shadow_prefix: str,
) -> _CriterionOutcome:
    """THE RATIFICATION GATE — the single place a DMN verdict becomes (or fails to become) a PASS.

    `would` is what the table(s) computed, derived with ZERO reference to ratification. This
    function is the only thing that turns it into a criterion:

      - every consulted table RATIFIED  -> the criterion IS `would` (None => false, plus the
        structural token that produced the None); no shadow (a ratified table's verdict is the
        verdict, so shadowing it would be noise).
      - ANY consulted table UNRATIFIED  -> the criterion is FALSE with `*_FONTE_NAO_RATIFICADA`,
        **regardless of what `would` says** — including `would=True`. The would-be verdict is
        preserved as a bounded shadow token instead. This is the whole point: a synthetic table
        must never be able to grant a real authorization.

    Structural tokens (`*_TABELA_INDISPONIVEL`, `*_PROCEDIMENTO_NAO_MAPEADO`) are kept alongside
    `*_FONTE_NAO_RATIFICADA` because they describe a DIFFERENT problem (a mechanism gap the SME
    must fix in the manifest or the ops team in the engine) from an unratified rule.
    """
    ratified = all(sources.is_ratified(key) for key in consulted)
    if ratified:
        return _CriterionOutcome(ok=bool(would), falhas=() if would else motivo)

    if would is True:
        sombra = (f"{shadow_prefix}{_SOMBRA_APROVARIA}",)
    elif would is False:
        sombra = (f"{shadow_prefix}{_SOMBRA_REPROVARIA}",)
    else:
        sombra = (f"{shadow_prefix}{_SOMBRA_INDETERMINADO}",)
    return _CriterionOutcome(ok=False, falhas=(unratified_token, *motivo), sombra=sombra)


def _criteria_result(
    *,
    tecnico: _CriterionOutcome,
    financeiro: _CriterionOutcome,
    regulatorio: _CriterionOutcome,
    contratual: _CriterionOutcome,
    status: str,
) -> dict[str, Any]:
    """Assemble the engine-visible output. The ONLY writer of the five criteria variables.

    `auto_criteria_verificado` is `True` on EVERY path — including degradation. It attests
    "the validator ran", not "the criteria passed"; conflating the two would let an outage
    quietly satisfy the DMN's execution fence.
    """
    outcomes = {
        "tecnico": tecnico,
        "financeiro": financeiro,
        "regulatorio": regulatorio,
        "contratual": contratual,
    }
    falhas: list[str] = []
    sombra: list[str] = []
    for name in _CRITERIA_ORDER:
        # Order-preserving DEDUP: the degradation path assigns the SAME
        # `VALIDADOR_INDISPONIVEL` token to all four criteria, and a list repeating it four
        # times reads as four distinct problems to whoever inspects process history. Order is
        # `_CRITERIA_ORDER`, so `falhas[0]` (the audit token) stays deterministic.
        falhas.extend(t for t in outcomes[name].falhas if t not in falhas)
        sombra.extend(t for t in outcomes[name].sombra if t not in sombra)
    return {
        "status": status,
        "criterio_tecnico_ok": tecnico.ok,
        "criterio_financeiro_ok": financeiro.ok,
        "criterio_regulatorio_ok": regulatorio.ok,
        "criterio_contratual_ok": contratual.ok,
        # PROOF OF EXECUTION — rule r1 of `auth_auto_approval.dmn` requires it true.
        "auto_criteria_verificado": True,
        "auto_criteria_falhas": falhas,
        "auto_criteria_shadow": sombra,
        # The single bounded token that reaches the durable ADR-0007 chain (lists cannot —
        # `harness._is_bounded_token` admits scalars only). First failure in `_CRITERIA_ORDER`;
        # "" when everything passed.
        "motivo_bloqueio_criterios": falhas[0] if falhas else "",
    }


# ---------------------------------------------------------------------------
# AnalyzeRequestWorker
# ---------------------------------------------------------------------------


class AnalyzeRequestWorker(WorkerBase):
    """External task: operadora.auth.analyze_request

    Convoca Rafael (medico auditor) e monta o dossie de analise.
    Pode auto-aprovar (L2) quando todas as condicoes favoraveis sao atendidas.

    Auto-approval conditions (ADR-0008 L2):
    - dut_atendida == True
    - dentro_teto_l2 == True
    - rede_credenciada == True

    Ineligibilidade aparente (beneficiario_ativo=False, carencia_nao_cumprida)
    SEMPRE roteia para analise humana — NUNCA resulta em negativa automatica.
    """

    def __init__(self, resolver: CeilingResolver | None = None) -> None:
        super().__init__(topic="operadora.auth.analyze_request")
        # Ceiling resolver (defect B3): computes `dentro_teto_l2` from the tenant governance
        # matrix, overriding any inbound value. Injectable for tests; the default resolves
        # from the real `spec/policies/autonomy` matrix.
        self._resolver = resolver if resolver is not None else CeilingResolver()

    def execute(self, process_vars: dict[str, Any]) -> dict[str, Any]:
        """Analyze the authorization request.

        Creates a dossier for Rafael. May auto-approve if L2 conditions met.

        Args:
            process_vars: BPMN authorization variables.

        Returns:
            Dict with analysis result and recommendation.
        """
        tenant_id = process_vars.get("tenant_id", "")
        guia = process_vars.get("numero_guia_tiss", "unknown")

        # Check L2 auto-approval conditions
        dut_ok = process_vars.get("dut_atendida", False)
        # COMPUTE the ceiling fact from policy (design T1.9 §2.4, defect B3). NEVER read the
        # inbound `dentro_teto_l2`. FAIL-CLOSED via the shared `_ceiling_valor_cents` (rejects
        # missing/bool/non-numeric/unparseable/NaN/+-inf/negative `valor_estimado_brl`, and CEILs
        # rather than rounds) — never a default 0 that would read as "within ceiling" under a
        # positive teto (fail-OPEN), and never the permissive `round()` this used to do locally
        # (a second place for the GK-ceiling rounding defect to recur in, and one that could raise
        # an uncaught `OverflowError` on `valor_estimado_brl="inf"`). This is an intentional
        # hardening divergence from v1's `_as_float` default-0.0. Any rejection here only ever
        # routes the request to human review (safe) — never denies, never crashes.
        raw_valor = process_vars.get("valor_estimado_brl")
        valor_cents = _ceiling_valor_cents(raw_valor)
        if valor_cents is None:
            teto_ok = False
        else:
            teto_ok = self._resolver.within_l2_ceiling(
                tenant=tenant_id,
                action=_CEILING_ACTION,
                param=_CEILING_PARAM,
                value_cents=valor_cents,
            )
        rede_ok = process_vars.get("rede_credenciada", False)
        beneficiario_ativo = process_vars.get("beneficiario_ativo", False)
        carencia_ok = process_vars.get("carencia_cumprida", False)
        docs_completa = process_vars.get("documentacao_completa", False)

        dossier_ref = f"dossier-{uuid.uuid4().hex[:12]}"

        # L2 auto-approval: all conditions favorable
        if all([dut_ok, teto_ok, rede_ok, beneficiario_ativo, carencia_ok, docs_completa]):
            self.logger.info(
                "auth_auto_approved",
                tenant_id=tenant_id,
                guia=guia,
                dossier_ref=dossier_ref,
            )
            return {
                "status": "auto_approved",
                "recommendation": "AUTO_APROVAR",
                "dossier_ref": dossier_ref,
                "assigned_to": "rafael",
                "event": "agents.events.auth.received",
                # Write the COMPUTED fact back so the DMN auth_auto_approval receives it —
                # never the inbound boolean (design T1.9 §2.4).
                "dentro_teto_l2": teto_ok,
            }

        # Default: route to human analysis (Rafael)
        self.logger.info(
            "auth_dossier_created",
            tenant_id=tenant_id,
            guia=guia,
            recommendation="ANALISE_HUMANA",
            dossier_ref=dossier_ref,
        )

        return {
            "status": "dossier_created",
            "recommendation": "ANALISE_HUMANA",
            "dossier_ref": dossier_ref,
            "assigned_to": "rafael",
            "event": "agents.events.auth.received",
            "dentro_teto_l2": teto_ok,
        }


# ---------------------------------------------------------------------------
# RequestDocumentsWorker
# ---------------------------------------------------------------------------


class RequestDocumentsWorker(WorkerBase):
    """External task: operadora.auth.request_documents

    Cria pendencia de documentacao para o prestador.
    Publica evento agents.events.auth.pended.
    """

    def __init__(self) -> None:
        super().__init__(topic="operadora.auth.request_documents")

    def execute(self, process_vars: dict[str, Any]) -> dict[str, Any]:
        """Create a documentation pendecy.

        Args:
            process_vars: Must include prestador_id and missing_docs.

        Returns:
            Dict with pe™ndency status.
        """
        tenant_id = process_vars.get("tenant_id", "")
        prestador_id = process_vars.get("prestador_id", "")
        missing = process_vars.get("missing_docs", [])
        # `guia` no registro: este era o UNICO marcador do fluxo AUTH que nao gravava a guia
        # (os outros cinco deste modulo gravam — 750, 802, 998, 1242, 1603). A consequencia
        # nao e' cosmetica: quem filtra o log por numero de guia para reconstruir o que
        # aconteceu com um caso NAO encontrava o pedido de documento, e o caso aparecia como
        # se tivesse pulado direto da admissibilidade para a espera.
        guia = process_vars.get("numero_guia_tiss", "unknown")

        self.logger.info(
            "auth_documents_pended",
            tenant_id=tenant_id,
            guia=guia,
            prestador_id=prestador_id,
            missing_docs=missing,
        )

        return {
            "status": "pended",
            "prestador_id": prestador_id,
            "missing_docs": missing,
            "event": "agents.events.auth.pended",
        }


# ---------------------------------------------------------------------------
# IssueAuthorizationWorker
# ---------------------------------------------------------------------------


class IssueAuthorizationWorker(WorkerBase):
    """External task: operadora.auth.issue_authorization

    Emite autorizacao TISS com numero unico — um ato FAVORAVEL (concessao), nunca adverso.
    Serve AMBAS as service tasks modeladas: `ST_EmitirAutorizacaoAuto` (rota L2 auto,
    engine-gated por `Flow_GW_AutoAprovar`) e `ST_EmitirAutorizacaoAuditor` (rota humana,
    engine-gated por `Flow_GWDec_Aprovar`, `${decisao_auditor == 'APROVAR'}`).

    Guard (human_approved threading, item-9 bucket-3 Class-C — module docstring): emite SOMENTE
    com evidencia de sancao em UM dos dois canais modelados:
    - canal HUMANO: `decisao_auditor == 'APROVAR'` (setado SO nas User Tasks humanas) OU o sinal
      explicito `human_approved is True` (pin fail-closed inalterado — apenas o boolean True);
    - canal AUTO (L2, ADR-0008): a sancao do proprio DMN `auth_auto_approval` que roteou a
      instancia ate aqui, entregue ACHATADA pelo `camunda:inputParameter` LOCAL de
      `ST_EmitirAutorizacaoAuto` (`auto_aprovacao_recomendacao = ${auto_aprovacao.recomendacao}`
      — mesmo termo da condicao de `Flow_GW_AutoAprovar`); parse fail-closed, ver
      `_auto_approval_sanctioned`.
    Defesa-em-profundidade: `decisao_auditor == 'NEGAR'` NUNCA emite — nem com human_approved
    explicito, nem com sancao auto residual (o worker jamais converte uma negativa em concessao).
    A proveniencia resolvida e' THREADED de volta como variavel engine-visivel `human_approved`
    (True no canal humano; False na emissao auto-L2 — verdadeiro e auditavel, ADR-0007).

    PORTAO DE TETO (GAP-AUTH-4, mitigacao no PONTO DE EMISSAO) — **SO no canal AUTOMATICO**:
    quando a sancao vem do DMN e NAO ha decisao humana (`auto_sanctioned and not human_approved`),
    o worker ainda verifica o teto de autonomia do tenant
    (`CeilingResolver.within_l2_ceiling(action=authorization_approval, param=max_value_brl)` — a
    MESMA chamada que `AnalyzeRequestWorker` ja faz em `ST_PrepararDossie`) e RECUSA emitir se ele
    nao autorizar. Fail-closed em todo vetor: tenant em branco, `valor_estimado_brl`
    ausente/nao-numerico/negativo/nao-finito, resolver indisponivel — todos RECUSAM
    (`ERR_AUTH_AUTO_CEILING_NOT_AUTHORIZED`). O fato COMPUTADO (`dentro_teto_l2`) e o token de
    motivo (`motivo_bloqueio_teto`) voltam como variaveis engine-visiveis, para que a recusa seja
    legivel no historico do processo (idioma de `AnalyzeRequestWorker`: escrever de volta o fato
    computado, nunca o inbound).

    O canal HUMANO NAO E' AFETADO: um `decisao_auditor == 'APROVAR'` (e a rota da junta) emite
    independentemente do teto — exceder o teto AUTOMATICO e' exatamente para o que a analise
    humana existe (ADR-0008). Gatear a perna humana quebraria a entrega assistencial.

    GAP-AUTH-4 — FECHADO ESTRUTURALMENTE (#198 `auth-auto-criteria-gate`, 2026-08-07;
    `docs/evidence-ledger.md` row `auth-auto-criteria-gate`): `BRT_AutoApproval` NAO decide mais
    sobre `dut_atendida`/`dentro_teto_l2`/`rede_credenciada` semeados no payload de start — esses
    tres booleanos nao sao mais lidos por esta DMN. `ST_ValidateAutoApprovalCriteria` (topico
    `operadora.auth.validate_auto_criteria`), inserido entre `BRT_SlaAnalise` e
    `BRT_AutoApproval`, computa quatro criterios deterministicos (tecnico/DUT-ROL,
    financeiro/teto do tenant via este MESMO `CeilingResolver`, regulatorio/carencia,
    contratual/milestones-KPI) e escreve `auto_criteria_verificado` em TODO caminho de execucao,
    inclusive na degradacao; `auth_auto_approval.dmn` v0.2.0 exige o quinteto inteiro
    (`auto_criteria_verificado` + os quatro `criterio_*_ok`) na UNICA regra favoravel — pular o
    validador cai no catch-all fail-safe -> ANALISE_HUMANA (ver
    `spec/processes/dmn/auth_auto_approval.dmn:58-101`; module docstring `auth.py:17-42`).

    DESFECHO OBSERVAVEL HOJE (governanca pendente, nao mais um gap estrutural): os criterios
    tecnico/regulatorio/contratual leem de tabelas SINTETICAS/DRAFT (portao de RATIFICACAO em
    `spec/processes/dmn/auth-criteria-ratification.yaml` — fonte nao-ratificada devolve `false`
    independente do que a tabela computou); o criterio financeiro ja NAO depende disso — a
    decisao D-07 fechou em 25/08/2026 e `authorization_approval.max_value_brl` carrega hoje um
    teto real e positivo em `tenants-amh.yaml` (ver `auth.py:484-490`). Logo o que ainda barra o
    auto-aprova sao os tres criterios nao-ratificados, e NENHUM pedido auto-aprova enquanto eles
    seguirem DRAFT — o MESMO desfecho seguro de antes, agora por motivos explicitos e auditaveis
    (`auto_criteria_falhas`) em vez de uma checagem ausente. Quando o SME ratificar as tres fontes
    restantes, a emissao automatica passa a funcionar, ainda limitada pela verificacao de teto no
    PONTO DE EMISSAO acima (defesa-em-profundidade, inalterada por esta nota).
    """

    def __init__(self, resolver: CeilingResolver | None = None) -> None:
        super().__init__(topic="operadora.auth.issue_authorization")
        # Ceiling resolver — mesma costura de injecao de `AnalyzeRequestWorker` (construtor com
        # default). Injetavel para testes; o default resolve a matriz real de
        # `spec/policies/autonomy` (L0-core + overlay do tenant), via o MESMO loader do PEP.
        self._resolver = resolver if resolver is not None else CeilingResolver()

    def _auto_ceiling_verdict(self, process_vars: dict[str, Any]) -> tuple[bool, str]:
        """`(autoriza, motivo_token)` do teto para o canal AUTOMATICO — FAIL-CLOSED.

        Recusa (False) em: tenant ausente/branco/nao-string; `valor_estimado_brl` que
        `_ceiling_valor_cents` nao consegue confiar; qualquer excecao do resolver; e o veredito
        negativo do proprio teto (que ja e fail-closed: teto 0 => False, mesmo para valor 0).
        Aceita SO um `True` booleano do resolver — um stub que devolva "truthy" nao abre emissao.
        """
        tenant = process_vars.get("tenant_id")
        tenant = tenant.strip() if isinstance(tenant, str) else ""
        if not tenant:
            return False, _CEILING_BLOCK_TENANT

        valor_cents = _ceiling_valor_cents(process_vars.get("valor_estimado_brl"))
        if valor_cents is None:
            return False, _CEILING_BLOCK_VALOR

        try:
            within = self._resolver.within_l2_ceiling(
                tenant=tenant,
                action=_CEILING_ACTION,
                param=_CEILING_PARAM,
                value_cents=valor_cents,
            )
        except Exception as exc:  # fail-closed: resolver indisponivel nunca emite
            self.logger.error(
                "auth_issue_ceiling_resolver_failed",
                tenant_id=tenant,
                error=str(exc),
            )
            return False, _CEILING_BLOCK_RESOLVER

        if within is not True:
            return False, _CEILING_BLOCK_TETO
        return True, ""

    def execute(self, process_vars: dict[str, Any]) -> dict[str, Any]:
        """Issue a TISS authorization.

        Guard order: (1) `decisao_auditor == NEGAR` hard-blocks BEFORE any channel is consulted;
        (2) a modeled sanction is required — human channel (decisao_auditor == APROVAR, or the
        explicit `human_approved is True` signal) OR the L2 auto sanction
        (auto_aprovacao.recomendacao == AUTO_APROVAR); (3) on the AUTOMATIC channel ONLY
        (sanctioned by the DMN with no human decision), the tenant governance ceiling must also
        admit `valor_estimado_brl`, else the issuance is refused with
        `ERR_AUTH_AUTO_CEILING_NOT_AUTHORIZED`. The human channel never reaches (3).

        Args:
            process_vars: Must include numero_guia_tiss and the route's sanction evidence
                          (decisao_auditor / human_approved / auto_aprovacao). On the automatic
                          channel it must ALSO carry `tenant_id` and a trustworthy
                          `valor_estimado_brl` (both fail-closed).

        Returns:
            Dict with authorization number (+ threaded human_approved provenance) or guard block.
        """
        tenant_id = process_vars.get("tenant_id", "")
        guia = process_vars.get("numero_guia_tiss", "unknown")
        decisao = _norm_decision(process_vars.get("decisao_auditor"))
        # FAIL-CLOSED (T3.1, mirrors lgpd.ValidateIdentityWorker / ADR-0031): sinal explicito SO
        # com `human_approved is True`. Ausente/False/lixo (string truthy como "true"/" ", int 1,
        # list/dict) -> nunca lido como aprovacao humana.
        human_approved = process_vars.get("human_approved") is True or decisao == "APROVAR"
        auto_sanctioned = _auto_approval_sanctioned(process_vars)

        # Defense-in-depth: uma decisao humana NEGAR jamais vira emissao — bloqueia ANTES de
        # qualquer canal de sancao (nem o sinal explicito nem uma sancao auto residual passam).
        if decisao == "NEGAR":
            self.logger.warning(
                "auth_issue_blocked_by_guard",
                tenant_id=tenant_id,
                guia=guia,
                reason="decisao_auditor=NEGAR nunca emite autorizacao (L0 hard)",
            )
            return {
                "status": "blocked_by_guard",
                "error_code": ERR_DENIAL_NOT_HUMAN,
                "mensagem": "Autorizacao jamais emitida sobre decisao NEGAR (L0 hard)",
            }

        # Guard: exige o canal humano OU a sancao do DMN da rota auto-L2 modelada.
        if not (human_approved or auto_sanctioned):
            self.logger.warning(
                "auth_issue_blocked_by_guard",
                tenant_id=tenant_id,
                guia=guia,
                reason="sem evidencia de decisao humana (decisao_auditor/human_approved) nem sancao auto-L2",
            )
            return {
                "status": "blocked_by_guard",
                "error_code": ERR_DENIAL_NOT_HUMAN,
                "mensagem": "Autorizacao requer aprovacao humana ou sancao auto-L2 modelada (L0 hard)",
            }

        # PORTAO DE TETO — canal AUTOMATICO **apenas** (GAP-AUTH-4 fechado estruturalmente por #198
        # `auth-auto-criteria-gate`; ver module docstring acima). `auto_sanctioned and not
        # human_approved` E' a definicao do canal auto: com decisao humana (APROVAR/junta) o teto
        # NAO se aplica — exceder o teto automatico e' exatamente o que a analise humana resolve.
        # Rodar aqui, no ponto de emissao, e' DEFESA-EM-PROFUNDIDADE: `criterio_financeiro_ok` ja
        # gateia o teto ANTES da BRT via este MESMO `CeilingResolver` (ST_ValidateAutoApprovalCriteria);
        # esta segunda checagem torna o teto LOAD-BEARING tambem no unico ponto onde uma
        # autorizacao efetivamente nasce.
        auto_channel = auto_sanctioned and not human_approved
        dentro_teto: bool | None = None
        if auto_channel:
            dentro_teto, motivo_teto = self._auto_ceiling_verdict(process_vars)
            if not dentro_teto:
                self.logger.warning(
                    "auth_issue_blocked_by_ceiling",
                    tenant_id=tenant_id,
                    guia=guia,
                    motivo=motivo_teto,
                    reason=(
                        "emissao AUTOMATICA nao autorizada pelo teto do tenant "
                        f"({_CEILING_ACTION}.{_CEILING_PARAM}) — canal humano nao e' afetado"
                    ),
                )
                return {
                    "status": "blocked_by_guard",
                    "error_code": ERR_AUTH_AUTO_CEILING_NOT_AUTHORIZED,
                    "mensagem": (
                        "Emissao automatica recusada: o teto de autonomia do tenant "
                        f"({_CEILING_ACTION}.{_CEILING_PARAM}) nao autoriza este valor. "
                        "A analise humana permanece disponivel e nao e' limitada por este teto."
                    ),
                    # Evidencia engine-visivel (idioma de AnalyzeRequestWorker: escrever de volta
                    # o FATO computado) — o auditor le no historico POR QUE nao houve emissao.
                    "dentro_teto_l2": False,
                    "motivo_bloqueio_teto": motivo_teto,
                }

        # Issue authorization
        auth_number = f"AUTH-{tenant_id}-{guia}-{uuid.uuid4().hex[:8]}"

        self.logger.info(
            "auth_issued",
            tenant_id=tenant_id,
            guia=guia,
            numero_autorizacao=auth_number,
            human_approved=human_approved,
            # GK finding 3: um `auto_aprovacao_recomendacao` semeado no start sobrevive na perna
            # HUMANA (o input mapping so existe na task auto), o que fazia este campo de auditoria
            # ler True numa emissao decidida por humano. Nao ha alargamento de emissao (a task
            # humana so e alcancavel por `decisao_auditor == 'APROVAR'`, que ja poe human_approved),
            # mas a TRILHA precisa ser verdadeira: sancao-auto so e reportada quando NAO ha decisao
            # humana.
            auto_sanctioned=auto_sanctioned and not human_approved,
        )

        issued: dict[str, Any] = {
            "status": "authorized",
            "numero_autorizacao": auth_number,
            "error_code": None,
            "event": "agents.events.auth.completed",
            # Threaded provenance (engine-visible, ADR-0007): True SO no canal humano; a emissao
            # auto-L2 registra False — verdadeiro (nenhum humano decidiu) e nunca fabricado.
            "human_approved": human_approved,
        }
        if dentro_teto is not None:
            # SO o canal automatico computou o teto — escrever o fato na perna humana seria
            # fabricar uma verificacao que nao aconteceu (o teto nao se aplica la).
            issued["dentro_teto_l2"] = dentro_teto
        return issued


# ---------------------------------------------------------------------------
# SendDenialNoticeWorker
# ---------------------------------------------------------------------------


# Zero-width / BOM code points that carry NO visible content but which `str.strip()` does NOT
# remove (their `str.isspace()` is False): zero-width space, ZWNJ, ZWJ, word joiner, BOM /
# zero-width no-break space. A grounding field made only of these is empty for completeness.
# The table and the emptiness rule now live in ONE place (`gateway/required_text.py`) because a
# second denial guard (the portal-decision owner, WP-J1-06) depends on the identical rule and a
# duplicated normalization is a normalization that eventually diverges. Names kept for the
# docstring below and for anything that reads them.
_ZERO_WIDTH_CHARS = ZERO_WIDTH_CHARS
_ZERO_WIDTH_TRANSLATION = ZERO_WIDTH_TRANSLATION


def _is_blank(value: Any) -> bool:
    """Fail-closed emptiness for a required denial-grounding field.

    A field is blank (=> incomplete => DENY the send) when it is NOT a non-empty grounding STRING:
      - any non-``str`` value (``None``, ``int``, ``list``, ``dict``, ``bool``, ``0``, ``False`` …)
        is blank — a required ANS grounding field that is not textual is unusable (the BPMN types
        these fields ``string``; "cannot decide completeness = incomplete = DENY the send"). This is
        stricter than the donor's ``not v`` (which admits e.g. a non-empty list as present).
      - a ``str`` that is empty / whitespace-only after zero-width & BOM characters are removed. The
        zero-width strip closes a gap where ``"\\u200b"`` alone (isspace() is False) would otherwise
        survive ``.strip()`` and read as present.
    """
    return is_blank(value)


class SendDenialNoticeWorker(WorkerBase):
    """External task: operadora.auth.send_denial_notice

    Transmite a negativa FORMAL por escrito, em nome do medico auditor HUMANO. Este worker NUNCA
    decide — apenas transmite/registra uma negativa JA decidida por uma User Task humana
    (UT_AnaliseMedicoAuditor / UT_CoordenacaoAssume / UT_RegistrarParecerJunta; L0 hard,
    ADR-0005/ADR-0008).

    Guards (fail-closed, defesa-em-profundidade):

    1. COMPLETUDE (ERR_AUTH_DENIAL_INCOMPLETE, T3.1) — uma NEGAR cujo dossie nao carrega TODAS as
       fundamentacoes obrigatorias da ANS (`_REQUIRED_DENIAL_FIELDS`: justificativa_clinica,
       cid10_referencia, fundamentacao_dut; RN 395 art. 10) NUNCA e transmitida. Levanta o
       WorkerBpmnError MODELADO `ERR_AUTH_DENIAL_INCOMPLETE`, capturado pelo boundary
       `BE_NegativaIncompleta` (ST_EnviarNegativaFormal) -> `End_FundamentacaoIncompletaBloqueada`
       (terminal NEUTRO: nada foi enviado ao beneficiario). Checado ANTES do guard `human_approved`
       porque um dossie incompleto deve ser barrado em QUALQUER circunstancia (a impossibilidade de
       decidir completude = incompleto = negar a transmissao). O codigo esta em
       `AUTH_BPMN_ERROR_ALLOWLIST` para o harness o despachar como `bpmnError` (nao incidente).

    2. NEGATIVA-NAO-HUMANA (ERR_DENIAL_NOT_HUMAN) — human_approved threading (item-9 bucket-3
       Class-C, module docstring): uma NEGAR sem PROVENIENCIA HUMANA e bloqueada. Evidencia
       aceita (fail-closed, qualquer UMA): o sinal explicito `human_approved is True` (pin
       inalterado — apenas o boolean True) OU um `auditor_id` nao-vazio (o campo de
       accountability ADR-0007 que as UTs humanas carregam na decisao NEGAR — espelha o idioma
       responsavel_id/aprovador_id dos guards adversos irmaos cred/pagto/recurso; whitespace-only/
       non-string normaliza para "" e refusa). O guard v2 original exigia um `human_approved` que
       NADA no modelo seta (unsatisfiable — a negativa genuinamente humana tambem bloqueava);
       a derivacao ancora o guard na evidencia real da decisao humana sem NUNCA abrir caminho
       automatizado (um fluxo sem UT humana nao tem auditor_id nem o sinal explicito). RETORNA um
       registro (nao levanta) — o BPMN nao declara boundary para este codigo, entao ele nao pode
       virar um `bpmnError`.

    EGRESS PHI (ADR-0006): a negativa carrega texto livre clinico (justificativa_clinica /
    cid10_referencia / fundamentacao_dut — nomes-PHI, `phi_vars.PHI_PROCESS_VARS`). Antes de
    qualquer variavel deixar o worker (engine/Kafka = Zona Geral), o payload passa por
    `redact_phi_vars` (redacao UNIDIRECIONAL, classe-token `REDACTED_PHI`): nenhum texto clinico cru
    entra em variavel de engine. O canal seguro real ao prestador recebe o texto integral fora
    desta costura (Fase 1).

    AUTH-SEND-DENIAL-NOTICE-STATUS-LITERAL (WP-FATOS-FABRICADOS slice 5). Os DOIS caminhos de
    sucesso deste worker devolviam, incondicionalmente, `"status": "notice_sent"` — uma afirmacao
    de TRANSMISSAO. O paragrafo acima ja divulgava que o canal seguro real e' de Fase 1: este
    worker e' SINCRONO (`WorkerBase.execute`), nao tem seam de Kafka nenhum, nao abre canal
    nenhum, e o unico efeito que produz e' COMPOR o registro da negativa e devolve-lo ao engine.
    `notice_sent` nomeava um ato que nada aqui executa; agora a chave `status` simplesmente NAO e'
    escrita nos caminhos de sucesso. O registro do guard (`status="blocked_by_guard"` +
    `ERR_DENIAL_NOT_HUMAN`) e' o UNICO `status` honesto deste worker — um fato interno sobre a
    propria recusa — e permanece byte-identico, assim como o `ERR_AUTH_DENIAL_INCOMPLETE` e o seu
    boundary `BE_NegativaIncompleta`. Nada mais mudou: o dossie redigido, `notice_type`,
    `error_code` e a proveniencia `human_approved` sao dados REAIS (ver `execute`).
    """

    def __init__(self) -> None:
        # max_retries=1: os guards deste worker sao DETERMINISTICOS, nao transitorios. Um
        # WorkerBpmnError (completude) NUNCA deve ser re-tentado pelo retry in-process do
        # WorkerBase — o engine e' dono do retry duravel (T1.1 design §9). Sem isto o
        # WorkerBpmnError seria re-tentado (com sleeps) antes de propagar ao harness.
        super().__init__(topic="operadora.auth.send_denial_notice", max_retries=1)

    def execute(self, process_vars: dict[str, Any]) -> dict[str, Any]:
        """Send denial (or approval) notice.

        Guards (NEGAR path, in order): completeness (raises `ERR_AUTH_DENIAL_INCOMPLETE`), then
        the human-provenance guard (explicit `human_approved is True` OR non-blank `auditor_id` —
        the ADR-0007 accountability evidence). Clinical PHI in the emitted denial payload is
        redacted one-way before return.

        Args:
            process_vars: Must include decisao_auditor; for NEGAR, the three grounding fields
                          (justificativa_clinica, cid10_referencia, fundamentacao_dut) and the
                          human provenance (human_approved or auditor_id).

        Returns:
            O REGISTRO da negativa (ou do aviso de aprovacao), com os campos clinicos redigidos —
            NUNCA um `status` de transmissao (AUTH-SEND-DENIAL-NOTICE-STATUS-LITERAL). Cada chave
            devolvida no caminho de sucesso e' um fato verificavel:
              - `notice_type` ({"denial","approval"}): a NATUREZA do registro composto aqui, nao
                um ato de envio;
              - `error_code: None`: nenhum guard disparou nesta entrega (o registro de recusa e'
                que carrega `ERR_DENIAL_NOT_HUMAN`);
              - `event`: o topico que `ST_PublishNegada` publica logo adiante, no UNICO fluxo de
                saida de `ST_EnviarNegativaFormal` (`Flow_Negativa_Pub` ->
                `event_topic=agents.events.auth.completed`) — o idioma irmao dos demais retornos
                `agents.events.*` deste modulo, cujo publicador e' a `ST_Publish*` do proprio BPMN;
              - `human_approved: True` (so no ramo NEGAR): a proveniencia humana RESOLVIDA acima,
                derivada de `human_approved is True` OU de um `auditor_id` nao-vazio — nunca uma
                constante decorativa (a integracao le esta variavel do historico do engine para
                provar que a negativa transmitida passou pelo GUARD 2);
              - os tres campos clinicos, ja passados por `redact_phi_vars` (ADR-0006).
            O ramo `blocked_by_guard` e' o unico que escreve `status`, e escreve um fato sobre a
            propria recusa.

        Raises:
            WorkerBpmnError: ERR_AUTH_DENIAL_INCOMPLETE when a NEGAR lacks any grounding field.
        """
        tenant_id = process_vars.get("tenant_id", "")
        decisao = process_vars.get("decisao_auditor", "")
        # PROVENIENCIA HUMANA (human_approved threading, item-9 bucket-3 Class-C): o sinal
        # explicito `human_approved is True` (pin fail-closed inalterado — apenas o boolean True)
        # OU o campo de accountability `auditor_id` nao-vazio (ADR-0007; setado SO nos payloads
        # NEGAR das UTs humanas — whitespace-only/non-string normaliza para "" e refusa).
        auditor_id = process_vars.get("auditor_id")
        auditor_id = auditor_id.strip() if isinstance(auditor_id, str) else ""
        human_approved = process_vars.get("human_approved") is True or bool(auditor_id)

        if decisao == "NEGAR":
            # GUARD 1 — COMPLETUDE (fail-closed, checado PRIMEIRO). None/""/whitespace = ausente.
            missing = [field for field in _REQUIRED_DENIAL_FIELDS if _is_blank(process_vars.get(field))]
            if missing:
                # Loga apenas os NOMES dos campos ausentes — nunca valores (PHI, ADR-0006).
                self.logger.error(
                    "auth_denial_blocked_incomplete",
                    tenant_id=tenant_id,
                    missing_fields=missing,
                    reason="negativa formal exige fundamentacao completa (RN 395 art. 10, L0 hard)",
                )
                raise WorkerBpmnError(
                    ERR_AUTH_DENIAL_INCOMPLETE,
                    f"send_denial_notice: fundamentacao incompleta, campos ausentes: {missing} — "
                    "negativa formal NAO transmitida (RN 395 art. 10, L0 hard ADR-0005).",
                )

            # GUARD 2 — proveniencia humana (derivada acima; retorna registro, nao levanta).
            if not human_approved:
                self.logger.error(
                    "auth_denial_blocked_by_guard",
                    tenant_id=tenant_id,
                    reason="NEGAR sem aprovacao humana (sem human_approved e sem auditor_id — L0 hard)",
                )
                return {
                    "status": "blocked_by_guard",
                    "error_code": ERR_DENIAL_NOT_HUMAN,
                    "mensagem": "Negativa automatica PROIBIDA. Requer decisao de medico auditor humano.",
                }

            # COMPOSICAO do registro formal (carrega os campos clinicos), REDIGIDO (redacao
            # unidirecional) antes de qualquer variavel deixar o worker. Nunca loga o conteudo
            # clinico — apenas o registro. `notice_composed_asserted_transmission=False` deixa
            # explicito na trilha que esta etapa COMPOE o registro e nao transmite nada: o canal
            # seguro ao prestador e' de Fase 1 (AUTH-SEND-DENIAL-NOTICE-STATUS-LITERAL).
            self.logger.info(
                "auth_denial_notice_composed",
                tenant_id=tenant_id,
                notice_composed_asserted_transmission=False,
            )
            notice = {
                "notice_type": "denial",
                "error_code": None,
                "event": "agents.events.auth.completed",
                # Threaded provenance (engine-visible, ADR-0007): uma negativa transmitida carrega
                # a proveniencia humana resolvida — este ramo so e' alcancavel com evidencia humana.
                "human_approved": True,
                "justificativa_clinica": process_vars.get("justificativa_clinica", ""),
                "cid10_referencia": process_vars.get("cid10_referencia", ""),
                "fundamentacao_dut": process_vars.get("fundamentacao_dut", ""),
            }
            # ENFORCEMENT POINT (PHI egress, ADR-0006/GAP-XPHI-1): nenhum campo clinico cru sai em
            # variavel de engine. As chaves estruturais que restam (notice_type/error_code/event/
            # human_approved) passam intactas — `status` nao esta mais entre elas
            # (AUTH-SEND-DENIAL-NOTICE-STATUS-LITERAL).
            return redact_phi_vars(notice)

        # APROVAR ou outro — compoe o aviso de aprovacao (sem campos clinicos). Ramo DEFENSIVO:
        # `ST_EnviarNegativaFormal` e' a UNICA task no topico `operadora.auth.send_denial_notice`
        # e o seu unico fluxo de entrada e' `Flow_GWDec_Negar` (`${decisao_auditor == 'NEGAR'}`),
        # entao o modelo nao alcanca este ramo; ele existe para nao explodir se um `decisao_auditor`
        # inesperado chegar. Mesmo aqui nada e' transmitido — so um registro e' composto.
        self.logger.info(
            "auth_approval_notice_composed",
            tenant_id=tenant_id,
            notice_composed_asserted_transmission=False,
        )
        return {
            "notice_type": "approval",
            "error_code": None,
            "event": "agents.events.auth.completed",
        }


# ---------------------------------------------------------------------------
# NotifySlaRiskWorker
# ---------------------------------------------------------------------------


class NotifySlaRiskWorker(WorkerBase):
    """External task: operadora.auth.notify_sla_risk

    Alerta coordenacao de auditoria medica sobre risco de SLA.
    Timer nao-interruptivo (50-70% do SLA total).
    """

    def __init__(self) -> None:
        super().__init__(topic="operadora.auth.notify_sla_risk")

    def execute(self, process_vars: dict[str, Any]) -> dict[str, Any]:
        """Registra que a ETAPA de alerta de risco de SLA rodou. Retorna `{}` — NAO afirma nada.

        Serve `ST_NotificarRiscoSla` (`SP-OP-AUTH-001_Autorizacao_Previa.bpmn:344-348`, topico
        `operadora.auth.notify_sla_risk`), alimentado SO pelo boundary NAO-interruptivo
        `BT_AlertaSla` (`cancelActivity="false"`, `:338-343`) em `UT_AnaliseMedicoAuditor`, em
        `${sla.sla_alerta}`. O ramo termina em `End_RiscoSlaNotificado` — informativo e jamais
        adverso: a User Task segue aberta e a negativa so nasce da decisao humana.

        FAB-SLA-RISK-NOTIFIED-SLICE4 (o motivo desta docstring). O retorno era, em toda entrega e
        sem calcular nada::

            {"status": "risk_notified", "alert_to": "coordenacao-auditoria-medica",
             "sla_percent": ..., "sla_analise": ...,
             "event": "agents.events.auth.sla_breached"}

        Este worker e SINCRONO e NAO TEM seam de Kafka nenhum (`WorkerBase.execute`) — ele nao
        podia ter publicado nada. Ainda assim afirmava DUAS coisas: um `status` de notificacao e
        um TOPICO DE EVENTO, como se tivesse emitido. Pior: `agents.events.auth.sla_breached` e
        publicado por `ST_PublishSlaBreach` (`:360-364`), que fica no ramo do boundary
        INTERRUPTIVO `BT_SlaAnalise` — outro ramo, que este alerta nunca alcanca. O nome do evento
        estava errado alem de nao ter sido emitido.

        ZERO CONSUMIDORES (mapa refeito antes de editar): a BPMN do AUTH nao tem nenhum
        `conditionExpression` sobre `status`, nenhuma DMN de auth le `status`/`alert_to`/
        `sla_percent`, e `ST_NotificarRiscoSla` nao declara `camunda:inputOutput` — logo o dict
        inteiro ia para o escopo do processo, com `status` num nome generico e sem dono, e
        `sla_analise` FLAT (o timer usa o aninhado `${sla.sla_analise}`, nunca este). A
        observabilidade da etapa fica no `logger.warning` abaixo, que declara
        `notified_asserted=False`.

        Args:
            process_vars: le `tenant_id`, `sla_percent` e `sla_analise` apenas para a trilha.

        Returns:
            `{}` — nenhuma variavel de processo e escrita por esta etapa.
        """
        tenant_id = process_vars.get("tenant_id", "")
        sla_percent = process_vars.get("sla_percent", 0)
        sla_analise = process_vars.get("sla_analise", "")

        self.logger.warning(
            "auth_sla_risk_notified",
            tenant_id=tenant_id,
            sla_percent=sla_percent,
            sla_analise=sla_analise,
            notified_asserted=False,
        )

        return {}


# ---------------------------------------------------------------------------
# ConveneJuntaWorker
# ---------------------------------------------------------------------------


class ConveneJuntaWorker(WorkerBase):
    """External task: operadora.auth.convene_junta

    Convoca junta medica (RN 424) — ato PROCEDIMENTAL (abre a User Task humana
    UT_RegistrarParecerJunta), nunca adverso. Alcancado SO via `Flow_GWDec_Junta`
    (`${decisao_auditor == 'JUNTA_MEDICA'}` — decisao humana da UT do auditor/coordenacao).

    Guard (human_approved threading, item-9 bucket-3 Class-C — module docstring): convoca com a
    evidencia da decisao humana `decisao_auditor == 'JUNTA_MEDICA'` (o proprio literal que a
    gateway humana exigiu) OU o sinal explicito `human_approved is True` (pin fail-closed
    inalterado). O guard v2 original exigia um `human_approved` que nada seta (unsatisfiable).

    NUNCA escreve `human_approved` na saida: este worker roda ANTES de
    UT_RegistrarParecerJunta — um True persistido por worker envenenaria o canal do sinal
    explicito do guard da negativa a jusante (uma junta-NEGAR sem auditor_id passaria pelo flag
    stale). Proveniencia e' derivada onde consumida, jamais fabricada por worker em ramo
    nao-terminal.
    """

    def __init__(self) -> None:
        super().__init__(topic="operadora.auth.convene_junta")

    def execute(self, process_vars: dict[str, Any]) -> dict[str, Any]:
        """Convokes a medical board.

        Guard: decisao_auditor == JUNTA_MEDICA (the human decision that routed here) or the
        explicit `human_approved is True` signal.

        Args:
            process_vars: Must include decisao_auditor (or human_approved).

        Returns:
            Dict with junta status or guard block (never writes human_approved back).
        """
        tenant_id = process_vars.get("tenant_id", "")
        guia = process_vars.get("numero_guia_tiss", "unknown")
        decisao = _norm_decision(process_vars.get("decisao_auditor"))
        # PROVENIENCIA HUMANA (fail-closed): o literal exato JUNTA_MEDICA (setado SO nas UTs
        # humanas; formData enum + gateway fail-closed) OU o sinal explicito `human_approved is
        # True`. Lixo (string truthy, int, list, dict) em qualquer canal -> bloqueia.
        human_approved = process_vars.get("human_approved") is True or decisao == "JUNTA_MEDICA"

        # Guard: require human decision evidence
        if not human_approved:
            self.logger.warning(
                "auth_junta_blocked_by_guard",
                tenant_id=tenant_id,
                guia=guia,
                reason="sem evidencia de decisao humana (nem JUNTA_MEDICA nem human_approved)",
            )
            return {
                "status": "blocked_by_guard",
                "error_code": ERR_DENIAL_NOT_HUMAN,
                "mensagem": "Convocacao de junta medica requer aprovacao humana (L0 hard)",
            }

        self.logger.info(
            "auth_junta_convened",
            tenant_id=tenant_id,
            guia=guia,
            decisao=decisao,
        )

        return {
            "status": "junta_convened",
            "junta_group": "junta-medica",
            "fundamentacao": "RN 424/2017",
            "event": "agents.events.auth.completed",
        }


# ---------------------------------------------------------------------------
# Bootstrap — donor contract (T1.2/ADR-0026 Decisao §3): class modules register
# directly, one WorkerBase() instance per topic. `kafka` stays accepted for
# signature parity with the other 15 register_<domain>_workers bootstraps and
# `register_all_workers` (ADR-0026 Decisao §4) and is unused here; `seams` is no
# longer discarded — `ValidateAutoCriteriaWorker` consumes the `dmn=` transport
# (ADR-0028), threaded from `worker_runtime/service.py`'s `register_all_workers(
# ..., dmn=dmn)`.
# ---------------------------------------------------------------------------


def register_auth_workers(
    harness: WorkerHarness,
    kafka: KafkaPublisher | None = None,
    **seams: Any,
) -> None:
    """Register the 7 SP-OP-AUTH-001 `WorkerBase` workers on `harness`.

    `ValidateAutoCriteriaWorker` takes the ADR-0028 `dmn=` seam. An ABSENT seam is NOT a
    registration failure: the worker fails each DMN-backed criterion closed with its own
    `*_TABELA_INDISPONIVEL` token (-> human review). Refusing to register, or raising here,
    would leave `ST_ValidateAutoApprovalCriteria` unserved and STALL every authorization
    request — the outcome design §6 exists to prevent.

    `denial_notice_host` (WP-J1-06) is the opposite kind of seam: it names ANOTHER owner
    for `operadora.auth.send_denial_notice`, so `SendDenialNoticeWorker` must NOT be
    registered alongside it. Two workers on one topic race for the same external task and
    the loser's guard never runs — one owner per topic, decided here, at the only place
    that registers the generic one. Absent the seam nothing changes: the generic worker
    stays the owner and reads its grounding from process variables as it always has.
    """
    del kafka  # unused — no auth.py worker declares a Kafka dependency
    host = seams.get("denial_notice_host")
    workers: list[type[WorkerBase]] = [
        AnalyzeRequestWorker,
        IssueAuthorizationWorker,
        NotifySlaRiskWorker,
        ConveneJuntaWorker,
    ]
    # WP-J1-03 — EXCLUSIVIDADE DE TOPICO. `operadora.auth.request_documents` tem dois
    # consumidores possiveis e EXATAMENTE UM pode estar registrado:
    #   (a) este `RequestDocumentsWorker`, que apenas registra em log e NAO entrega nada
    #       (nenhuma linha de caixa de entrada, nenhum corpo sob custodia PHI); e
    #   (b) a ponte dedicada `DocumentRequestHost` (gateway/document_requests), que entrega
    #       de verdade ao prestador E ao beneficiario (decisao #18 do dono).
    # Se os dois estiverem registrados, os dois fazem fetch-and-lock da MESMA tarefa externa
    # e quem ganhar a corrida decide se o pedido foi entregue ou apenas logado — o pior
    # resultado possivel, porque o processo segue para GW_AguardarDocs nos dois casos e a
    # falta de entrega so aparece quando o prazo P5D expira. Por isso a escolha e' explicita
    # (`MAEZO_AUTH_DOCUMENT_REQUEST_HOST`), nunca inferida, e a ponte se recusa a instalar
    # enquanto este topico ainda estiver no conjunto do harness generico
    # (`DocumentRequestHost.assert_exclusive`). A ausencia do seam mantem o comportamento
    # historico byte-a-byte: o worker generico registra.
    if not seams.get("document_request_host_installed", False):
        workers.insert(1, RequestDocumentsWorker)
    # WP-J1-06 — A MESMA REGRA, SEGUNDO TOPICO. `operadora.auth.send_denial_notice`
    # tambem tem dois consumidores possiveis e exatamente um pode estar registrado:
    #   (a) este `SendDenialNoticeWorker`, que le a fundamentacao das variaveis de
    #       processo; e
    #   (b) o dono nativo do portal (`DenialNoticeHost`, instalado so quando
    #       `MAEZO_DENIAL_NOTICE_OWNER` esta ligado), que le a base da decisao humana em
    #       custodia PHI.
    # Dois workers num topico correm pela mesma tarefa externa e o guard do perdedor
    # nunca corre. A ausencia do seam mantem o comportamento historico byte-a-byte.
    # A ordem de registo e' indiferente (topicos distintos), por isso um `append` basta
    # e evita aritmetica de indices que dependa do bloco acima.
    if host is None:
        workers.append(SendDenialNoticeWorker)
    for worker_cls in workers:
        harness.register_worker(worker_cls())
    harness.register_worker(ValidateAutoCriteriaWorker(dmn=seams.get("dmn")))
    if host is not None:
        # Re-assert exclusivity AFTER registration: the seam's own installation check
        # cannot see workers registered later on the same harness.
        owner = host.worker()
        # Re-installing the SAME owner is a no-op, not a conflict: `register_all_workers`
        # is documented idempotent, and `assert_exclusive` only sees topic NAMES, so on a
        # second pass it would otherwise refuse the owner its own topic. A topic held by
        # anyone else still goes through the exclusivity check below and is refused.
        if type(harness.registry.get(owner.topic)) is not type(owner):
            host.assert_exclusive(harness.registered_topics)
            harness.register_worker(owner)
            # And make it durable (V14 MINOR-5): re-asserting at one moment in time let
            # a LATER `register_all_workers(harness)` without the seam silently hand the
            # topic back to the generic worker, because the registry warns and overwrites
            # on a duplicate topic. The seal keeps this idempotent for the same owner and
            # refuses anything else. Sealed from the host's own worker, so the seal can
            # never name a type the host does not actually install.
            harness.seal_topic(owner.topic, type(owner))
