"""Escalation workers — SP-OP-ESCALATION-001.

Provides BPMN external task handlers for the Escalonamento Humano Universal process.

Handlers (DL-0034 — RAW ASYNC KAFKA HANDLERS, the #55 R-B / events.py sanctioned form, NOT
`WorkerBase`; see `register_escalation_workers`):
- notify_team: notifies the human group the `escalation_routing` DMN routed to.
- notify_supervisor: supervisor alert on SLA breach (also serves `ST_NotificarFallback` — the
  BPMN's fallback-channel task publishes to this SAME topic,
  `operadora.escalation.notify_supervisor`; there is no separate `notify_fallback` topic in the
  BPMN, `spec/processes/bpmn/SP-OP-ESCALATION-001_*.bpmn:112,203`).

CRITICAL: handlers NEVER make adverse decisions (L0 hard). They only notify. Escalation
resolution is always a human decision. They do not ROUTE either — routing is the
`escalation_routing` DMN's, evaluated engine-side by `BRT_RotearEscalonamento` (ADR-0028); these
handlers only READ its output.

GAP-ESC-SEVERITY-GROUP (root cause, fixed here): both handlers used to read the ENGLISH variable
`severity` (`v.get("severity", "leve")`) while the BPMN, the DMN and the FINAL contract all
declare `severidade` (contract `docs/processes/contracts/SP-OP-ESCALATION-001.md:27,:57`; BPMN
`:49,:66,:85,:188,:243`). `severity` is set by NOBODY — SP-OP-ESCALATION-001 has TWO starters, not
one (MINOR-1, VERIFY-WP-ESC.md corrects an earlier "only starter" claim here): `helena/graph.py:651`
AND `agents/lucas/graph.py:137,639` (`PROCESS_KEY`/`start_process_idempotent`), and BOTH send the
Portuguese `severidade` — Lucas's is typed `Literal["grave","moderada","leve"]`
(`lucas/graph.py:202,714`), so it structurally cannot carry `severity` — so the read ALWAYS missed
and ALWAYS fell to the `"leve"` default; a private `_SEVERITY_TO_GROUP` table then mapped that
phantom `leve` to `atendimento-humano` and shipped both wrong values inside the internal
notification. A P1 `red_flag_clinico`/`grave` escalation under the PT5M art. 35-C SLA was therefore
announced to the clinical team as `severity=leve, group=atendimento-humano`. The private table ALSO
competed with the DMN, which routes primarily on `motivo_categoria` (only `r1` reads `severidade`):
`risco_psicossocial`+`leve` is `plantao-clinico`/P1 in the DMN (`escalation_routing.dmn:37-45`) and
`atendimento-humano` in the deleted table. The fix: consume `severidade` (process variable) and
`grupo_atendimento` (the BPMN's `camunda:inputParameter` fed from
`${roteamento.grupo_atendimento}`, `:96,:115,:206`), never re-derive routing, never default a
clinical severity, and FAIL CLOSED when either is missing or outside its contractual domain (see
`_exigir_severidade`/`_exigir_grupo_atendimento`). §Delta-3 narrowed exactly ONE hole in that
posture — the contract's own declared `severidade`-is-`null` case for
`motivo_categoria=falha_tecnica` (`_MOTIVO_SEM_SEVERIDADE`), where refusing was un-paging the very
cases this process exists to hand to a human; `_exigir_severidade`'s docstring carries the measured
regression and the DMN rule (`r6`) that makes accepting `null` cost no routing authority.
Deploy-window cost (MINOR-4, disclosed here, not
narrowed): an instance whose notify task completed under the pre-fix worker carries
`group`/`severity` in process scope, and its NEXT notify task refuses under
`_ALIASES_INGLES_PROIBIDOS` below — non-adverse (HITL and the breach event are unaffected) but it
does drop one supervisor page for that in-flight case; see the MIGRATION NOTE on that constant.

WHY RAW ASYNC HANDLERS (DL-0034, ratified by orchestrator 2026-07-26, built in t5): for
escalation, NOTIFYING *is* the business effect (there is no domain computation beyond
route+notify), and the BPMN models these as their OWN topics
(`operadora.escalation.notify_team`/`notify_supervisor`), not the generic
`operadora.events.publish`. A `WorkerBase.execute(dict)` boundary has NO async Kafka seam
(ADR-0026 §2), so the prior `NotifyTeamWorker`/`NotifySupervisorWorker` `WorkerBase` classes NEVER
actually published — they returned a `{"status": ...}` dict and the notification silently never
happened (live-confirmed on CIB Seven 2.1.0: `tests/integration/processes/
test_sp_op_escalation_001.py`'s `_NOTIFY_KAFKA_GAP_REASON` — `probe.notified_teams`/
`notified_supervisors` never observed an execution). This mirrors EXACTLY the #55 R-B precedent
(`operadora.lgpd.request_additional_proof`, `lgpd.py:449-510`) / `events.py`: a raw handler
registered via `harness.register(topic, handler)` with the async Kafka seam.

kafka=None (fail-closed, same decision as `events.py` / #55 R-B): the task MUST still complete —
the BPMN flow continues UNCONDITIONALLY past the notify task (`Flow_Notificar_UT` ->
`UT_TratarEscalonamento`); a notification-producer gap must never HANG the escalation. Log LOUDLY,
never fabricate a publish. NO adverse effect either way.

ERR_ESC_NOTIFY_FAILED (ADR-0030 Tier-1, t8-escalation-boundary — the change DL-0034 deferred): a
`kafka.publish` FAILURE in either notify handler now raises the MODELED
`WorkerBpmnError(ERR_ESC_NOTIFY_FAILED)` instead of propagating the raw exception, so
SP-OP-ESCALATION-001's error boundaries actually fire: `BE_FalhaNotificacao` (on ST_NotificarTime)
-> supervisor fallback (ST_NotificarFallback) -> UT_TratarEscalonamento; `BE_NotifFallbackFailed`
(on ST_NotificarFallback) -> UT_TratarEscalonamento even if BOTH channels fail; and
`BE_NotifSupervisorFailed` (on ST_NotificarSupervisor) -> End_SupervisorAlertado. A notify failure
therefore fail-SAFEs to a supervisor + the mandatory HITL user task (ADR-0005) — the escalation is
never silently dropped and never stalls on an incident. ADR-0030 §4 classifies this a NON-adverse
technical fail-safe (G2-fs), so it is NOT T-E-gated and is enabled in production now
(`ESCALATION_BPMN_ERROR_ALLOWLIST`, unioned into `worker_runtime/service.py`'s
`PRODUCTION_BPMN_ERROR_ALLOWLIST`; the boundary-proof gate proves it consumption-covered). The
`kafka=None` path is UNCHANGED (loud log + complete so the flow still reaches the HITL) — only a
real publish ATTEMPT that raises drives the boundary. The harness still reports the code as a real
`bpmnError` only when it is in its `bpmn_error_allowlist`; an un-allowlisted code demotes to a loud
incident, never a silent scope-end.

NON-HOLLOW DISCIPLINE (t8-escalation-boundary v2 — the crux the first cut missed): both notify
handlers publish to `operadora.notifications.internal`, which the REAL producer
(`events_kafka_producer.AioKafkaEventsProducer`) lists in `BEST_EFFORT_TOPICS`. Under the producer's
DEFAULT (topic-based) posture a broker-down failure on that topic is SWALLOWED one layer below, so
`kafka.publish` would NEVER raise and this try/except would be dead code (the handler would return
success while a grave clinical notification silently vanished). The fix passes `best_effort=False`
on every notify publish, forcing the producer to PROPAGATE — that is what makes the boundary raise
above real (not just green against a fake that unconditionally raises).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, NoReturn

import structlog

from maezo.platform.integrations.partition_key import partition_key_for_task
from maezo.tools.workers.harness import WorkerBpmnError

if TYPE_CHECKING:
    from maezo.tools.workers.harness import ExternalTask, KafkaPublisher, TaskHandler, WorkerHarness

logger = structlog.get_logger(__name__)
# Stdlib logger mirror (events.py convention): the kafka=None loud log must surface even where the
# structlog chain is not wired.
_stdlib_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Contractual domains — VALIDATORS of what the engine hands us, NEVER a routing table.
#
# GAP-ESC-SEVERITY-GROUP: the deleted `_SEVERITY_TO_GROUP`/`_DEFAULT_GROUP` pair was a PRIVATE
# severidade->grupo map competing with `escalation_routing.dmn`, which is the single source of
# routing truth (ADR-0012 deterministic rules outside the LLM; ADR-0028 engine-side evaluation).
# What survives here is only the CLOSED DOMAIN of each field, used to fail closed on values the
# engine could not have produced — no fallback, no default, no re-derivation.
#
# FOUR domains total: `grupo_atendimento` is REQUIRED (fail-closed if absent); `severidade` is
# REQUIRED for every `motivo_categoria` except the one the contract itself declares `null`
# (`_MOTIVO_SEM_SEVERIDADE`, §Delta-3) — and REQUIRED-IN-DOMAIN always, i.e. a PRESENT value outside
# the domain fails closed on every motivo.
# `prioridade` is OPTIONAL but, since MINOR-2 (VERIFY-WP-ESC.md), a PRESENT out-of-domain value
# fails closed too (it is the DMN's OWN output — out-of-domain means the engine is corrupted). A
# probe put `'P9-LIVRE <script>'` straight through the old unchecked `.strip()`-only path
# (`_rotulo_opcional`, below `_recusar`). `motivo_categoria` is OPTIONAL and validated too, but
# since ESC-D1-MOTIVO-STRICTER-THAN-R7 a PRESENT out-of-domain value DEGRADES (omit + loud log,
# `_rotulo_opcional_tolerante`) instead of failing closed — the `escalation_routing` DMN's own
# catch-all (`r7`) already tolerates an unknown motivo and routes it safely; refusing the
# notification for a case the DMN already routed was stricter than the routing authority itself.
# None of the four is hand-typed alone: `test_contract_domains_match_the_artifacts`
# (`tests/unit/tools/workers/test_escalation_notify_team.py`) PARSES `escalation_routing.dmn` (via
# `tests.support.dmn_first_hit.read_live_table`, the repo's existing live-DMN-XML reader — see
# `tests/unit/spec/test_glosa_triage_shadow_candidate.py` for the same idiom) and the contract
# markdown directly, so any DMN/contract edit turns that test red before it could silently diverge
# from these constants (MAJOR-1, VERIFY-WP-ESC.md).
# ---------------------------------------------------------------------------

#: `severidade` domain — contract `docs/processes/contracts/SP-OP-ESCALATION-001.md:27,:57`
#: (input variable, obligatory except under `_MOTIVO_SEM_SEVERIDADE`) and
#: `escalation_routing.dmn:21-23` (the DMN's 2nd input). The domain itself is unconditional: it
#: constrains a PRESENT value on every motivo.
_SEVERIDADES_CONTRATUAIS: frozenset[str] = frozenset({"grave", "moderada", "leve"})

#: `grupo_atendimento` domain — the `escalation_routing` DMN's `out_grupo` output values
#: (`spec/processes/dmn/escalation_routing.dmn:33,42,51,60,69,78,87`), ratified by the contract's
#: §Papeis humanos table (`:59`, `:69-71`). `supervisao-atendimento` (`:72`) is NOT here: it is
#: the alert TARGET of `UT_SupervisorAssume`, never a DMN routing output.
_GRUPOS_ATENDIMENTO_DMN: frozenset[str] = frozenset(
    {"plantao-clinico", "enfermagem-triagem", "atendimento-humano"}
)

#: `prioridade` domain — the SAME `escalation_routing` DMN's `out_prioridade` output values
#: (`spec/processes/dmn/escalation_routing.dmn:32,41,50,59,68,77,86`), ratified by the contract's
#: DMN-reference table (`:58`). Optional field (MINOR-2): validated when present, never required.
_PRIORIDADES_DMN: frozenset[str] = frozenset({"P1", "P2", "P3"})

#: `motivo_categoria` domain — the contract's input-variable table (`SP-OP-ESCALATION-001.md:26`).
#: Optional at the notify tasks (MINOR-2): validated when present, never required — the
#: `escalation_routing` DMN already consumed it upstream of these tasks. An out-of-domain PRESENT
#: value DEGRADES rather than fails closed (ESC-D1-MOTIVO-STRICTER-THAN-R7, `_rotulo_opcional_
#: tolerante`) — the DMN's own catch-all (`r7`) already tolerates an unknown motivo.
_MOTIVOS_CONTRATUAIS: frozenset[str] = frozenset(
    {
        "red_flag_clinico",
        "risco_psicossocial",
        "intencao_clinica",
        "solicitacao_humano",
        "falha_tecnica",
        "outro",
    }
)

#: The ONE `motivo_categoria` whose escalation may legitimately carry NO `severidade`
#: (§Delta-3 HELENA-INPUT-BOUNDARY, regressao P-12 — see `_exigir_severidade`). Declared by the
#: contract's own §`severidade` quando `motivo_categoria = falha_tecnica` section AND by the
#: "Obrigatoria" cell of its `severidade` input-variable row, which now names this exception
#: instead of a bare `sim`; both halves are re-derived from the markdown by
#: `test_a_excecao_de_severidade_e_exatamente_a_que_o_contrato_declara`, so this constant can never
#: drift away from the document it implements. Its ROUTING legitimacy comes from the
#: `escalation_routing` DMN itself: rule `r6` matches this motivo with the `severidade` column at
#: the `-` wildcard (any value, `null` included) -> P3 / `atendimento-humano` / PT4H / PT24H, so
#: severidade was never a routing input here — pinned by
#: `test_a_dmn_roteia_falha_tecnica_com_severidade_nula_pela_regra_r6`.
_MOTIVO_SEM_SEVERIDADE: str = "falha_tecnica"

#: English aliases of contract variables. NONE of these is ever set by SP-OP-ESCALATION-001 —
#: their presence in a task's variables means either the pre-fix worker wrote them back into
#: process scope (`{"group": ..., "severity": ...}` in its completion payload) or a caller is
#: speaking a vocabulary the contract does not define. Either way the clinical severity of the
#: case is AMBIGUOUS, and a clinical severity is never guessed: refuse, fail-safe to the
#: supervisor/HITL, and make the drift loud instead of silently authoritative.
#: MIGRATION NOTE: an instance started under the pre-fix worker carries `group`/`severity` in
#: process scope; its next notify task refuses here. That refusal is NON-adverse — it routes to
#: `ST_NotificarFallback` -> `UT_TratarEscalonamento` (or, on the non-interruptive ack branch, to
#: `End_SupervisorAlertado` AFTER `ST_PublishAckBreach` already published the breach event), so
#: nothing is lost silently. New instances never carry these keys.
_ALIASES_INGLES_PROIBIDOS: tuple[str, ...] = ("severity", "group", "priority")

#: The supervisor group alerted on SLA breach / channel fallback (contract `:72`).
_GRUPO_SUPERVISAO: str = "supervisao-atendimento"

# Topics + notification channel/types (mirrors lgpd.py / recurso.py's per-worker notification idiom;
# the notification `type` is what the integration probe's `notified_teams`/`notified_supervisors`
# match on — test_sp_op_escalation_001.py:198,202).
_NOTIFICATIONS_TOPIC = "operadora.notifications.internal"
_NOTIFY_TEAM_TOPIC = "operadora.escalation.notify_team"
_NOTIFY_SUPERVISOR_TOPIC = "operadora.escalation.notify_supervisor"
_NOTIFY_TEAM_NOTIFICATION_TYPE = "escalation.notify_team"
_NOTIFY_SUPERVISOR_NOTIFICATION_TYPE = "escalation.notify_supervisor"

# ADR-0030 Tier-1 (G2-fs technical fail-safe): the modeled BPMN error a notify handler raises when
# the notification channel (`kafka.publish`) fails. SP-OP-ESCALATION-001 declares a matching
# `bpmn:error@errorCode="ERR_ESC_NOTIFY_FAILED"` boundary on every task on the two notify topics
# (`operadora.escalation.notify_team` / `notify_supervisor`), so it is consumption-covered by the
# boundary-proof gate's simple rule (both topics are single-family). A notify failure fail-SAFEs to
# a supervisor + the mandatory HITL user task — never a silent drop, never an incident that stalls.
_ERR_ESC_NOTIFY_FAILED = "ERR_ESC_NOTIFY_FAILED"

# ADR-0030 §4: NON-adverse technical fail-safe (routes to fallback/HITL, not a regulated/denial
# outcome) — NOT T-E-gated, so it enables in production directly (Tier-1). Mirrors `events.py`'s
# `EVENTS_BPMN_ERROR_ALLOWLIST` / auth's `AUTH_BPMN_ERROR_ALLOWLIST`; the boundary-proof gate
# (`scripts/ci/check_bpmn_error_allowlist.py`) — not this list — is the source of truth, and
# `worker_runtime/service.py` unions this into `PRODUCTION_BPMN_ERROR_ALLOWLIST`.
ESCALATION_BPMN_ERROR_ALLOWLIST: frozenset[str] = frozenset({_ERR_ESC_NOTIFY_FAILED})


def _recusar(task: ExternalTask, motivo: str, detalhe: str) -> NoReturn:
    """Fail closed on unusable escalation-routing input (GAP-ESC-SEVERITY-GROUP).

    Raises the ALREADY-MODELED `ERR_ESC_NOTIFY_FAILED` (ADR-0030 Tier-1, G2-fs). Semantics hold
    exactly: a notification we cannot address correctly is a notification of the human team that
    FAILED (`bpmn:error@name="Falha na notificacao do time humano"`, BPMN `:13`). No new error
    code is invented, so the boundary-proof gate's consumption coverage is unchanged.

    Why a modeled BPMN error and NOT a raw exception: a raw exception becomes a harness incident,
    and `Flow_Notificar_UT` is the ONLY edge from `ST_NotificarTime` to `UT_TratarEscalonamento` —
    an incident there would HANG the escalation short of its mandatory HITL (ADR-0005), the one
    outcome the module forbids. The modeled error instead drives `BE_FalhaNotificacao` ->
    `ST_NotificarFallback` -> (and, if that refuses too) `BE_NotifFallbackFailed` ->
    `UT_TratarEscalonamento`. Fail CLOSED on the payload, fail SAFE on the flow.

    ADR-0006: callers supply only static diagnostics and closed-domain field names/values.
    Never interpolate the refused input into `detalhe`: it also reaches exception rendering.
    """
    logger.error(
        "escalation_routing_input_recusado",
        motivo=motivo,
        detalhe=detalhe,
        topic=task.topic,
        business_key=task.business_key,
        process_instance_id=task.process_instance_id,
    )
    _stdlib_logger.error(
        "escalation_routing_input_recusado business_key=%s topic=%s motivo=%s — %s; recusa "
        "fail-closed (ERR_ESC_NOTIFY_FAILED), NENHUMA notificacao publicada com dado inventado",
        task.business_key,
        task.topic,
        motivo,
        detalhe,
    )
    raise WorkerBpmnError(
        _ERR_ESC_NOTIFY_FAILED,
        f"Entrada de roteamento inutilizavel ({motivo}): {detalhe}",
    )


def _recusar_aliases_ingles(task: ExternalTask, v: Mapping[str, Any]) -> None:
    """Refuse a task whose variables carry an English alias of a contract variable.

    GAP-ESC-SEVERITY-GROUP: `severity`/`group`/`priority` are the exact names the pre-fix worker
    read and wrote. Accepting them silently is what let a phantom `leve` look authoritative for
    months. This is a hard refusal, not a warning: the contract's vocabulary is Portuguese
    (`severidade`, `grupo_atendimento`, `prioridade`) and there is no legitimate producer of the
    English names anywhere in the chain — BOTH of SP-OP-ESCALATION-001's starters
    (`helena/graph.py:651`, `agents/lucas/graph.py:137,639`; MINOR-1, VERIFY-WP-ESC.md: there are
    two starters, not one) send `severidade`, never `severity`: `grep -rn 'severity' src/
    --include='*.py'` returns only this module plus 6 unrelated PROSE hits (docstrings/comments in
    `adequacao*.py`, `platform/validation/result.py`, `agents/helena/*`,
    `agents/fernando/graph.py`) — zero variable writes. `grep -rn 'severity' spec/` returns 4 hits,
    all English PROSE inside two `triage-redflag-*-shadow-candidate.yaml` comment blocks — no
    BPMN/DMN element is named it.
    """
    presentes = [alias for alias in _ALIASES_INGLES_PROIBIDOS if alias in v]
    if presentes:
        _recusar(
            task,
            "alias_ingles_proibido",
            f"variaveis {presentes} usam vocabulario ingles; o contrato declara "
            "severidade/grupo_atendimento/prioridade (SP-OP-ESCALATION-001.md:27,:57,:58,:59)",
        )


def _exigir_severidade(task: ExternalTask, v: Mapping[str, Any]) -> str | None:
    """Read the CONTRACT variable `severidade` — never defaulted, never guessed, and `None` ONLY
    where the contract itself declares the variable absent.

    A clinical severity that is absent or outside `{grave, moderada, leve}` is not `leve`: it is
    unknown, and an unknown clinical severity must never be announced as the mildest one. That is
    still the rule for every `motivo_categoria` but ONE.

    §Delta-3 (regressao P-12, HELENA-INPUT-BOUNDARY). The exception is `motivo_categoria =
    falha_tecnica` (`_MOTIVO_SEM_SEVERIDADE`), where the contract DECLARES `severidade` `null` for
    a classifier failure: there was no extraction at all to derive one from. Until this fix the
    contract contradicted itself — the input-variable row said `obrigatoria: sim` while its own
    HEL-04 section declared `null` — and this function implemented the required row.
    The Fleet historical tests reported `no user task ever appeared`, but their fixture
    had no BPMN error allowlist and no Kafka producer: they did not prove both production
    notification channels failed. `notify_team` and `notify_supervisor` are distinct topics
    sharing this check. Production wires ESCALATION_BPMN_ERROR_ALLOWLIST; the integrated
    fixture now does likewise. A `kafka=None` completion still does not mean delivery.

    Accepting `null` for that ONE motivo costs no routing authority: `escalation_routing`'s rule
    `r6` matches it with the `severidade` column at `-`, so the group, the priority and both SLAs
    are the DMN's regardless. The two fail-closed limits stay: a PRESENT value outside the
    contractual domain refuses here too (absence is the declared truth; corruption is not), and no
    other motivo — including an ABSENT one — buys the exception. `leve` is never fabricated on any
    path.
    """
    bruta = v.get("severidade")
    # AUSENTE = chave inexistente, `null` vindo do motor (`_from_camunda_var` devolve `None`), ou
    # texto vazio/so-espacos. Um valor PRESENTE que nao e' texto e' CORRUPCAO, nao ausencia, e cai
    # no `_recusar` logo abaixo — nenhum motivo, nem o isento, compra a excecao com ele.
    if bruta is None or (isinstance(bruta, str) and not bruta.strip()):
        if _rotulo_opcional(v, "motivo_categoria") == _MOTIVO_SEM_SEVERIDADE:
            logger.info(
                "escalation_severidade_ausente_declarada_pelo_contrato",
                motivo_categoria=_MOTIVO_SEM_SEVERIDADE,
                business_key=task.business_key,
                topic=task.topic,
                process_instance_id=task.process_instance_id,
            )
            _stdlib_logger.info(
                "escalation_severidade_ausente_declarada_pelo_contrato business_key=%s topic=%s "
                "motivo_categoria=%s — severidade viaja como `null` (excecao declarada em "
                "SP-OP-ESCALATION-001.md, linha `severidade` + secao HEL-04; DMN "
                "escalation_routing r6 roteia este motivo com severidade `-`); a notificacao SAI e "
                "o caso segue para UT_TratarEscalonamento. NENHUM `leve` e' fabricado",
                task.business_key,
                task.topic,
                _MOTIVO_SEM_SEVERIDADE,
            )
            return None
        _recusar(
            task,
            "severidade_ausente",
            "variavel de contrato `severidade` ausente/vazia (obrigatoria fora de "
            f"`motivo_categoria={_MOTIVO_SEM_SEVERIDADE}`, SP-OP-ESCALATION-001.md:27) — "
            "severidade clinica NUNCA e' assumida como `leve`",
        )
    if not isinstance(bruta, str):
        _recusar(
            task,
            "severidade_tipo_invalido",
            f"`severidade` chegou como {type(bruta).__name__} ({bruta!r}), nao texto — o contrato "
            "a declara `string` (SP-OP-ESCALATION-001.md:27); um valor que o motor nao poderia ter "
            f"produzido e' corrupcao, nunca a ausencia declarada de `motivo_categoria="
            f"{_MOTIVO_SEM_SEVERIDADE}`",
        )
    severidade = bruta.strip()
    if severidade not in _SEVERIDADES_CONTRATUAIS:
        _recusar(
            task,
            "severidade_fora_do_dominio",
            "`severidade` fora do dominio contratual "
            f"{sorted(_SEVERIDADES_CONTRATUAIS)} (SP-OP-ESCALATION-001.md:27,:57)",
        )
    return severidade


def _exigir_grupo_atendimento(task: ExternalTask, v: Mapping[str, Any]) -> str:
    """Read the DMN-derived `grupo_atendimento` the BPMN injects — never re-derive it.

    `ST_NotificarTime` (`:96`), `ST_NotificarFallback` (`:115`) and `ST_NotificarSupervisor`
    (`:206`) each declare `<camunda:inputParameter name="grupo_atendimento">
    ${roteamento.grupo_atendimento}</camunda:inputParameter>`, i.e. the `escalation_routing` DMN
    output that ALSO sets `UT_TratarEscalonamento`'s `candidateGroups` (`:134`). Reading it is the
    only way the notification can be guaranteed not to contradict who is actually paged.
    """
    bruto = v.get("grupo_atendimento")
    if not isinstance(bruto, str) or not bruto.strip():
        _recusar(
            task,
            "grupo_atendimento_ausente",
            "`grupo_atendimento` ausente/vazio — a DMN escalation_routing e' a UNICA fonte de "
            "roteamento (BPMN :96,:115,:206 <- ${roteamento.grupo_atendimento}); sem ela a "
            "notificacao contradiria o candidateGroups da User Task (:134)",
        )
    grupo = bruto.strip()
    if grupo not in _GRUPOS_ATENDIMENTO_DMN:
        _recusar(
            task,
            "grupo_atendimento_fora_do_dominio",
            "`grupo_atendimento` fora do dominio da DMN "
            f"{sorted(_GRUPOS_ATENDIMENTO_DMN)} (escalation_routing.dmn:33,42,51,60,69,78,87; "
            "contrato :59)",
        )
    return grupo


def _rotulo_opcional(v: Mapping[str, Any], nome: str) -> str | None:
    """Return a bounded routing label the BPMN may set, or `None` — never a fabricated value.

    Used for `prioridade`/`motivo_categoria`/`motivo`/`motivo_fallback`: when the engine did not
    provide one, the notification OMITS the field rather than inventing a domain value (the
    `motivo_categoria="outro"` / `sla_status="unknown"` class of dishonesty).
    """
    bruto = v.get(nome)
    if not isinstance(bruto, str) or not bruto.strip():
        return None
    return bruto.strip()


def _rotulo_opcional_validado(
    task: ExternalTask, v: Mapping[str, Any], nome: str, dominio: frozenset[str]
) -> str | None:
    """`_rotulo_opcional`, plus a closed-domain check on the value when one IS present (MINOR-2,
    VERIFY-WP-ESC.md).

    Absent/blank still returns `None` — the field stays OPTIONAL, omitted rather than fabricated,
    exactly like `_rotulo_opcional`. A PRESENT but out-of-domain value fails closed via the SAME
    modeled `ERR_ESC_NOTIFY_FAILED` boundary as `severidade`/`grupo_atendimento`: a bounded routing
    label the engine could not have produced is exactly as untrustworthy as a missing required
    one. Before this, `prioridade`/`motivo_categoria` were only `.strip()`-ed and forwarded
    verbatim — a probe put `'P9-LIVRE <script>'` and a fake-CPF string straight into the published
    notification through this gap, underneath a comment that claimed both were validated.

    ESC-D1-MOTIVO-STRICTER-THAN-R7: used ONLY for `prioridade` now — it is the `escalation_routing`
    DMN's OWN output (`_PRIORIDADES_DMN`), so a value outside it means the ENGINE produced garbage,
    not that an upstream category was unrecognized; that failure class stays fail-closed. See
    `_rotulo_opcional_tolerante` for `motivo_categoria`, which degrades instead.
    """
    rotulo = _rotulo_opcional(v, nome)
    if rotulo is not None and rotulo not in dominio:
        _recusar(
            task,
            f"{nome}_fora_do_dominio",
            f"`{nome}` fora do dominio {sorted(dominio)}",
        )
    return rotulo


def _rotulo_opcional_tolerante(
    task: ExternalTask, v: Mapping[str, Any], nome: str, dominio: frozenset[str]
) -> str | None:
    """`_rotulo_opcional`, but a PRESENT out-of-domain value DEGRADES (omit + loud log) instead
    of failing closed (ESC-D1-MOTIVO-STRICTER-THAN-R7, residual D-1 of GAP-ESC-SEVERITY-GROUP
    PR #272).

    Used ONLY for `motivo_categoria`. The `escalation_routing` DMN's own catch-all (`r7`,
    `spec/processes/dmn/escalation_routing.dmn:82-89`) is an EXPLICIT fail-SAFE for an unknown
    motivo: it still ROUTES the case (`P2`/`atendimento-humano`/`PT30M`/`PT4H`) rather than
    refusing anything — "Catch-all FAIL-SAFE: motivo desconhecido nunca recebe prioridade baixa".
    Before this fix, `make_notify_team_handler` refused the NOTIFICATION (via
    `_rotulo_opcional_validado`) for a case the DMN had already safely routed — stricter than the
    routing authority it defers to, and for no safety gain: `grupo_atendimento`/`prioridade` are
    read from the DMN's OWN output (never re-derived here), so they are ALREADY guaranteed valid
    regardless of what `motivo_categoria` says. Dropping just the label and still notifying the
    (correctly DMN-routed) team is strictly safer than refusing the whole notification.

    `prioridade` stays on `_rotulo_opcional_validado` (fail-closed): it is the DMN's OWN output,
    not an upstream-supplied category, so an out-of-domain value there means the engine itself is
    corrupted — a different failure class that must NOT be tolerated the same way.

    PHI (ESC-TOLERANT-LOG-RAW-VALUE, ADR-0006): reject the ENTIRE untrusted label from
    diagnostics too. A heuristic redactor cannot recognize arbitrary clinical narrative.
    Keep only the field, contractual domain and existing correlation metadata in both logs.
    """
    rotulo = _rotulo_opcional(v, nome)
    if rotulo is not None and rotulo not in dominio:
        logger.warning(
            "escalation_rotulo_fora_do_dominio_tolerado",
            campo=nome,
            dominio=sorted(dominio),
            business_key=task.business_key,
            topic=task.topic,
            process_instance_id=task.process_instance_id,
        )
        _stdlib_logger.warning(
            "escalation_rotulo_fora_do_dominio_tolerado business_key=%s topic=%s campo=%s "
            "fora do dominio %s — OMITIDO da notificacao (a DMN escalation_routing ja' "
            "tolera motivo desconhecido via seu catch-all r7; isto NAO e' uma recusa)",
            task.business_key,
            task.topic,
            nome,
            sorted(dominio),
        )
        return None
    return rotulo


# ---------------------------------------------------------------------------
# notify_team (DL-0034 — raw async Kafka handler, mirrors #55 R-B)
# ---------------------------------------------------------------------------


def make_notify_team_handler(kafka: KafkaPublisher | None) -> TaskHandler:
    """Create the handler for `operadora.escalation.notify_team` (DL-0034; mirrors #55 R-B).

    Serves `ST_NotificarTime`. Emits the internal notification (`type=escalation.notify_team`) to
    the group the `escalation_routing` DMN already chose. It NEVER decides the escalation OUTCOME
    and — since GAP-ESC-SEVERITY-GROUP — it never decides the ROUTE either: `grupo_atendimento`
    comes from the BPMN's `camunda:inputParameter` (`:96` <- `${roteamento.grupo_atendimento}`),
    the SAME expression that sets `UT_TratarEscalonamento`'s `candidateGroups` (`:134`), so the
    notification is structurally incapable of contradicting who is paged. `severidade` is the
    contract's own process variable (`:27`), read verbatim, never defaulted — including when the
    contract's `falha_tecnica` exception makes it `null` (§Delta-3), which the notification carries
    as `null` rather than as a fabricated domain value.

    Variables consumed (all set by SP-OP-ESCALATION-001, none invented here):
      - `severidade`         — process variable, contract `:27`; REQUIRED and fail-closed, EXCEPT
        under `motivo_categoria=falha_tecnica`, where the contract declares it `null` and it is
        forwarded as `None` (§Delta-3, `_exigir_severidade`). A PRESENT out-of-domain value fails
        closed on every motivo.
      - `grupo_atendimento`  — inputParameter BPMN `:96`, DMN output; REQUIRED, fail-closed.
      - `prioridade`         — inputParameter BPMN `:97`, DMN output `{P1,P2,P3}`; optional,
        validated against that domain when present (MINOR-2) and FAILS CLOSED if out of it — it
        is the DMN's own output, so out-of-domain means the engine is corrupted.
      - `motivo_categoria`   — process variable, contract `:26`, 6-value domain; optional,
        validated against that domain when present, but (ESC-D1-MOTIVO-STRICTER-THAN-R7) an
        out-of-domain value DEGRADES (omitted + logged) rather than failing closed — the
        `escalation_routing` DMN's own catch-all (`r7`) already tolerates an unknown motivo.
      - `tenant_id`          — process variable, contract `:20`.
    """

    async def handler(task: ExternalTask) -> Mapping[str, Any]:
        v = task.variables
        _recusar_aliases_ingles(task, v)
        # §Delta-3: `None` ONLY under the contract's declared `falha_tecnica` exception; it rides
        # verbatim into the completion payload and the notification, never coerced to a domain value.
        severidade: str | None = _exigir_severidade(task, v)
        grupo = _exigir_grupo_atendimento(task, v)
        prioridade = _rotulo_opcional_validado(task, v, "prioridade", _PRIORIDADES_DMN)
        motivo = _rotulo_opcional_tolerante(task, v, "motivo_categoria", _MOTIVOS_CONTRATUAIS)
        tenant_id = v.get("tenant_id", "")
        result: dict[str, Any] = {
            "grupo_atendimento": grupo,
            "severidade": severidade,
            "event": "agents.events.escalation.requested",
        }
        if prioridade is not None:
            result["prioridade"] = prioridade
        if motivo is not None:
            result["motivo_categoria"] = motivo

        if kafka is None:
            # No producer wired yet (T1.2/ADR-0026 gap, same reality as events.py). Log LOUDLY and
            # complete anyway — the task MUST complete so the flow reaches UT_TratarEscalonamento
            # (else the escalation HANGS in prod). NEVER fabricate a publish, and (MINOR-3,
            # VERIFY-WP-ESC.md) never claim `teams_notified` for zero publishes either — no BPMN
            # element reads this worker's `status` (`grep -n 'status' spec/processes/bpmn/
            # SP-OP-ESCALATION-001_*.bpmn` = 0 hits), so this label change moves no flow.
            logger.warning(
                "escalation_notify_team_no_producer",
                tenant_id=tenant_id,
                severidade=severidade,
                grupo_atendimento=grupo,
                prioridade=prioridade,
                business_key=task.business_key,
            )
            _stdlib_logger.warning(
                "escalation_notify_team_no_producer business_key=%s — kafka=None (no producer "
                "wired); notification NOT published, completing so the flow reaches "
                "UT_TratarEscalonamento",
                task.business_key,
            )
            result["status"] = "teams_notification_skipped_no_producer"
            return result

        # No PHI: motivo_categoria/severidade/grupo_atendimento/prioridade are bounded routing
        # labels validated against their contractual/DMN domains above WHEN PRESENT —
        # grupo_atendimento is REQUIRED and fail-closed; severidade is too, except under the
        # contract's declared `falha_tecnica` exception, where it rides as `None` (§Delta-3);
        # prioridade is OPTIONAL but
        # fails closed too on an out-of-domain value (MINOR-2, VERIFY-WP-ESC.md — it is the DMN's
        # own output); motivo_categoria is OPTIONAL and an out-of-domain value there DEGRADES
        # (omitted + logged, ESC-D1-MOTIVO-STRICTER-THAN-R7) rather than failing closed, so none
        # of the four rides through UNCHECKED even though they no longer all fail the same way;
        # beneficiario_pseudo_id is a pseudonym and free-text fields are never copied into the
        # notification (ADR-0006).
        notification: dict[str, Any] = {
            "type": _NOTIFY_TEAM_NOTIFICATION_TYPE,
            "tenant_id": tenant_id,
            "severidade": severidade,
            "grupo_atendimento": grupo,
        }
        if prioridade is not None:
            notification["prioridade"] = prioridade
        if motivo is not None:
            notification["motivo_categoria"] = motivo
        # GAP-SC-04-a: the partition key is resolved HERE, deliberately ABOVE the `try`. Two
        # reasons, both load-bearing. (1) `task.business_key or None` degraded a blank business key
        # into an UNKEYED publish — round-robin across the topic's 3 default partitions, so two
        # notifications about the SAME escalation could reorder once the bridge is scaled past one
        # replica. (2) Under a ratified DL-0043 `scrub_only` with no provisioned `PHI_HMAC_KEY`,
        # `egress_message_key` raises `PseudonymizerKeyMissingError` — a CONFIGURATION fault. Inside
        # the `try` below it would be caught and converted into `ERR_ESC_NOTIFY_FAILED`, routing a
        # key-provisioning problem to the notify-fallback boundary under a channel-failure
        # diagnosis, forever. Hoisted, it propagates raw to the harness ladder with its own type
        # intact — the same hoist `events.py:326-344` documents for the same raise.
        # MERGE NOTE (this branch x GAP-ESC-SEVERITY-GROUP): the derivation is deliberately the
        # LAST statement before the `try`, i.e. AFTER the optional `prioridade`/`motivo_*`
        # enrichment above, so the key is always derived from the payload as it is actually
        # PUBLISHED. Today the two orders are equivalent (`escalation` is not an `ENTITY_ANCHORS`
        # family, so arm (3) reads nothing but `type`/`tenant_id`, both set unconditionally); the
        # order is pinned so that adding an escalation anchor group later cannot silently derive a
        # key from a payload that is missing fields the message carries.
        message_key = partition_key_for_task(task, _NOTIFICATIONS_TOPIC, notification)
        publish_failed = False
        try:
            # best_effort=False (t8-escalation-boundary ROOT-CAUSE fix): _NOTIFICATIONS_TOPIC is in
            # the producer's BEST_EFFORT_TOPICS, so the DEFAULT posture would SWALLOW a broker-down
            # publish failure one layer below and this try/except would never see it (the hollow
            # bug). Forcing best_effort=False makes the real producer PROPAGATE, so the failure
            # reaches the except below and the modeled boundary actually fires.
            await kafka.publish(_NOTIFICATIONS_TOPIC, notification, key=message_key, best_effort=False)
        except Exception:
            # ADR-0030 Tier-1 (G2-fs): a notify-channel failure is a MODELED fail-safe, NOT an
            # incident. Raise ERR_ESC_NOTIFY_FAILED so `BE_FalhaNotificacao` (attached to
            # ST_NotificarTime) routes to the supervisor fallback (ST_NotificarFallback) and STILL
            # reaches UT_TratarEscalonamento — the mandatory HITL (ADR-0005) is never lost to a
            # channel glitch. Mirrors `events.py`'s ERR_EVENT_PUBLISH_FAILED raise; the harness
            # reports it as a real bpmnError only when the code is in its `bpmn_error_allowlist`
            # (gate-proven — else demoted to a loud incident, never a silent scope-end).
            logger.error(
                "escalation_notify_team_failed",
                tenant_id=tenant_id,
                severidade=severidade,
                grupo_atendimento=grupo,
                prioridade=prioridade,
                business_key=task.business_key,
            )
            publish_failed = True
        if publish_failed:
            # ADR-0006: discard broker text and leave the except scope before raising,
            # so neither rendered traces nor __context__ retain the broker exception.
            raise WorkerBpmnError(
                _ERR_ESC_NOTIFY_FAILED,
                f"Falha ao notificar time humano ({grupo})",
            ) from None
        logger.info(
            "escalation_notify_team_sent",
            tenant_id=tenant_id,
            severidade=severidade,
            grupo_atendimento=grupo,
            prioridade=prioridade,
            business_key=task.business_key,
        )
        result["status"] = "teams_notified"
        return result

    return handler


# ---------------------------------------------------------------------------
# notify_supervisor (DL-0034 — raw async Kafka handler, mirrors #55 R-B)
# ---------------------------------------------------------------------------


def make_notify_supervisor_handler(kafka: KafkaPublisher | None) -> TaskHandler:
    """Create the handler for `operadora.escalation.notify_supervisor` (DL-0034; mirrors #55 R-B).

    Serves BOTH `ST_NotificarSupervisor` (SLA breach — ack/resolution timer expired) and
    `ST_NotificarFallback` (channel fallback when `notify_team` fails) — the BPMN wires both tasks
    to this SAME topic (`:112,203`); there is no separate `notify_fallback` topic anywhere in
    spec/. Alerts the supervisor (`type=escalation.notify_supervisor`). NEVER makes any decision
    about the case — the supervisor (human) decides the next action.

    Variables consumed (GAP-ESC-SEVERITY-GROUP — every one of them is actually SET by the BPMN):
      - `severidade`        — process variable, contract `:27`; REQUIRED and fail-closed, EXCEPT
        under `motivo_categoria=falha_tecnica` (§Delta-3, `_exigir_severidade`), where it is
        forwarded as `None`. A PRESENT out-of-domain value fails closed on every motivo.
      - `grupo_atendimento` — inputParameter BPMN `:115` (fallback) / `:206` (SLA breach), from
        `${roteamento.grupo_atendimento}`; REQUIRED, fail-closed. It tells the supervisor WHICH
        team is (or was) on the hook — it is never the supervisor's own group.
      - `prioridade`        — inputParameter BPMN `:116`/`:207`, DMN output `{P1,P2,P3}`;
        optional, validated against that domain when present (MINOR-2), omitted if absent.
      - `motivo` (`:208` = `sla_ack_breached`) / `motivo_fallback` (`:117` =
        `notificacao_primaria_falhou`) — the alert reason, reported as `motivo_alerta`. This
        REPLACES the removed `sla_status`, which read a variable NO BPMN task ever sets
        (`grep -n sla_status spec/` -> 0 hits) and therefore reported the literal `"unknown"` on
        every single supervisor alert ever raised.
    """

    async def handler(task: ExternalTask) -> Mapping[str, Any]:
        v = task.variables
        _recusar_aliases_ingles(task, v)
        # §Delta-3: `None` ONLY under the contract's declared `falha_tecnica` exception; it rides
        # verbatim into the completion payload and the notification, never coerced to a domain value.
        severidade: str | None = _exigir_severidade(task, v)
        grupo = _exigir_grupo_atendimento(task, v)
        prioridade = _rotulo_opcional_validado(task, v, "prioridade", _PRIORIDADES_DMN)
        motivo_alerta = _rotulo_opcional(v, "motivo") or _rotulo_opcional(v, "motivo_fallback")
        tenant_id = v.get("tenant_id", "")
        result: dict[str, Any] = {
            "severidade": severidade,
            "grupo_atendimento": grupo,
            "alert_to": _GRUPO_SUPERVISAO,
            "require_human_resolution": True,
            "event": "agents.events.escalation.sla_breached",
        }
        if prioridade is not None:
            result["prioridade"] = prioridade
        if motivo_alerta is not None:
            result["motivo_alerta"] = motivo_alerta

        if kafka is None:
            logger.warning(
                "escalation_supervisor_notify_no_producer",
                tenant_id=tenant_id,
                severidade=severidade,
                grupo_atendimento=grupo,
                motivo_alerta=motivo_alerta,
                business_key=task.business_key,
            )
            _stdlib_logger.warning(
                "escalation_supervisor_notify_no_producer business_key=%s — kafka=None (no "
                "producer wired); notification NOT published, completing so the escalation is not "
                "stalled",
                task.business_key,
            )
            result["status"] = "supervisor_notification_skipped_no_producer"
            return result

        notification: dict[str, Any] = {
            "type": _NOTIFY_SUPERVISOR_NOTIFICATION_TYPE,
            "tenant_id": tenant_id,
            "severidade": severidade,
            "grupo_atendimento": grupo,
            "alert_to": _GRUPO_SUPERVISAO,
        }
        if prioridade is not None:
            notification["prioridade"] = prioridade
        if motivo_alerta is not None:
            notification["motivo_alerta"] = motivo_alerta
        # GAP-SC-04-a: the partition key is resolved HERE, deliberately ABOVE the `try`. Two
        # reasons, both load-bearing. (1) `task.business_key or None` degraded a blank business key
        # into an UNKEYED publish — round-robin across the topic's 3 default partitions, so two
        # notifications about the SAME escalation could reorder once the bridge is scaled past one
        # replica. (2) Under a ratified DL-0043 `scrub_only` with no provisioned `PHI_HMAC_KEY`,
        # `egress_message_key` raises `PseudonymizerKeyMissingError` — a CONFIGURATION fault. Inside
        # the `try` below it would be caught and converted into `ERR_ESC_NOTIFY_FAILED`, routing a
        # key-provisioning problem to the notify-fallback boundary under a channel-failure
        # diagnosis, forever. Hoisted, it propagates raw to the harness ladder with its own type
        # intact — the same hoist `events.py:326-344` documents for the same raise.
        # MERGE NOTE (this branch x GAP-ESC-SEVERITY-GROUP): the derivation is deliberately the
        # LAST statement before the `try`, i.e. AFTER the optional `prioridade`/`motivo_*`
        # enrichment above, so the key is always derived from the payload as it is actually
        # PUBLISHED. Today the two orders are equivalent (`escalation` is not an `ENTITY_ANCHORS`
        # family, so arm (3) reads nothing but `type`/`tenant_id`, both set unconditionally); the
        # order is pinned so that adding an escalation anchor group later cannot silently derive a
        # key from a payload that is missing fields the message carries.
        message_key = partition_key_for_task(task, _NOTIFICATIONS_TOPIC, notification)
        publish_failed = False
        try:
            # best_effort=False (t8-escalation-boundary ROOT-CAUSE fix): force the real producer to
            # PROPAGATE a broker-down failure on _NOTIFICATIONS_TOPIC (otherwise topic-default
            # best-effort would swallow it below and this except would never fire).
            await kafka.publish(_NOTIFICATIONS_TOPIC, notification, key=message_key, best_effort=False)
        except Exception:
            # ADR-0030 Tier-1 (G2-fs): this handler serves BOTH ST_NotificarSupervisor (SLA breach)
            # AND ST_NotificarFallback (the notify_team channel fallback). On a publish failure raise
            # ERR_ESC_NOTIFY_FAILED so the attached boundary continues the fail-safe route:
            # `BE_NotifFallbackFailed` -> UT_TratarEscalonamento (the HITL still exists even if BOTH
            # channels fail); `BE_NotifSupervisorFailed` -> End_SupervisorAlertado (the
            # non-interruptive branch still ends cleanly; the main case stays open in
            # UT_TratarEscalonamento). Never an incident that stalls the escalation.
            logger.error(
                "escalation_supervisor_notify_failed",
                tenant_id=tenant_id,
                severidade=severidade,
                grupo_atendimento=grupo,
                motivo_alerta=motivo_alerta,
                business_key=task.business_key,
            )
            publish_failed = True
        if publish_failed:
            # ADR-0006: no arbitrary broker diagnostics or retained exception chain.
            # The modeled failure still reaches the same mandatory human fallback.
            raise WorkerBpmnError(
                _ERR_ESC_NOTIFY_FAILED,
                "Falha ao notificar supervisor",
            ) from None
        logger.warning(
            "escalation_supervisor_notified",
            tenant_id=tenant_id,
            severidade=severidade,
            grupo_atendimento=grupo,
            motivo_alerta=motivo_alerta,
            business_key=task.business_key,
        )
        result["status"] = "supervisor_notified"
        return result

    return handler


# ---------------------------------------------------------------------------
# Bootstrap — donor contract (T1.2/ADR-0026 Decisao §3).
#
# t2.5-p2b-round2: the dead `NotifyFallbackWorker` (topic
# `operadora.escalation.notify_fallback`) was removed — `grep -rn notify_fallback spec/` (zero
# hits) confirmed no BPMN task ever declared that topic; `ST_NotificarFallback` actually declares
# `camunda:topic="operadora.escalation.notify_supervisor"` (BPMN :112), the SAME topic as
# `ST_NotificarSupervisor`. Both tasks are served by the single `notify_supervisor` handler.
# ---------------------------------------------------------------------------


def register_escalation_workers(
    harness: WorkerHarness,
    kafka: KafkaPublisher | None = None,
    **seams: Any,
) -> None:
    """Register the 2 SP-OP-ESCALATION-001 raw async Kafka handlers on `harness` (DL-0034).

    RAW `harness.register()` handlers (NOT `register_worker`) — DL-0034 (ratified by orchestrator
    2026-07-26, built in t5): emitting the escalation notification needs the async Kafka seam a
    `WorkerBase.execute` boundary cannot reach; this is the #55 R-B / `events.publish` sanctioned
    form, NOT an exception to ADR-0026 §2's dict-first purity (for this class of task, notifying IS
    the business effect and the BPMN gives it its OWN topic). `kafka` is threaded into BOTH
    handlers; production wires a real producer later (T1.2/ADR-0026), and both fail closed to
    "complete + loud log" while it is `None`. Because these are raw handlers, they populate the
    harness `_handlers` table but NOT the `WorkerRegistry` (see `test_bootstrap_registration.py`'s
    `raw_handler_topics`).
    """
    del seams  # unused — no dmn/other seam is needed by these notify handlers
    harness.register(_NOTIFY_TEAM_TOPIC, make_notify_team_handler(kafka))
    harness.register(_NOTIFY_SUPERVISOR_TOPIC, make_notify_supervisor_handler(kafka))
