"""Authorization workers — SP-OP-AUTH-001.

Provides BPMN external task handlers for the Autorizacao Previa process.

Workers:
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

import uuid
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from maezo.tools.workers.base import (
    ERR_AUTH_DENIAL_INCOMPLETE,
    ERR_DENIAL_NOT_HUMAN,
    WorkerBase,
)
from maezo.tools.workers.ceilings import CeilingResolver
from maezo.tools.workers.harness import WorkerBpmnError
from maezo.tools.workers.phi_vars import redact_phi_vars

# Governance ceiling for AUTH L2 auto-approval (design T1.9 §2.4). The teto VALUE lives in
# the autonomy matrix (`authorization_approval.max_value_brl`, L0-core.yaml:22 +
# tenants-amh.yaml overlay), resolved via the SAME loader the PEP uses.
_CEILING_ACTION = "authorization_approval"
_CEILING_PARAM = "max_value_brl"

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
        # inbound `dentro_teto_l2`. FAIL-CLOSED: a missing/None/non-numeric
        # `valor_estimado_brl` yields teto_ok=False (route to human) — never a default 0
        # that would read as "within ceiling" under a positive teto (fail-OPEN). This is an
        # intentional hardening divergence from v1's `_as_float` default-0.0.
        raw_valor = process_vars.get("valor_estimado_brl")
        try:
            valor_cents = round(float(raw_valor) * 100) if raw_valor is not None else None
        except (TypeError, ValueError):
            valor_cents = None
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

        self.logger.info(
            "auth_documents_pended",
            tenant_id=tenant_id,
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
    """

    def __init__(self) -> None:
        super().__init__(topic="operadora.auth.issue_authorization")

    def execute(self, process_vars: dict[str, Any]) -> dict[str, Any]:
        """Issue a TISS authorization.

        Guard: human channel (decisao_auditor == APROVAR, or the explicit `human_approved is
        True` signal) OR the modeled L2 auto sanction (auto_aprovacao.recomendacao ==
        AUTO_APROVAR). NEVER issues for NEGAR.

        Args:
            process_vars: Must include numero_guia_tiss and the route's sanction evidence
                          (decisao_auditor / human_approved / auto_aprovacao).

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

        # Issue authorization
        auth_number = f"AUTH-{tenant_id}-{guia}-{uuid.uuid4().hex[:8]}"

        self.logger.info(
            "auth_issued",
            tenant_id=tenant_id,
            guia=guia,
            numero_autorizacao=auth_number,
            human_approved=human_approved,
            auto_sanctioned=auto_sanctioned,
        )

        return {
            "status": "authorized",
            "numero_autorizacao": auth_number,
            "error_code": None,
            "event": "agents.events.auth.completed",
            # Threaded provenance (engine-visible, ADR-0007): True SO no canal humano; a emissao
            # auto-L2 registra False — verdadeiro (nenhum humano decidiu) e nunca fabricado.
            "human_approved": human_approved,
        }


# ---------------------------------------------------------------------------
# SendDenialNoticeWorker
# ---------------------------------------------------------------------------


# Zero-width / BOM code points that carry NO visible content but which `str.strip()` does NOT
# remove (their `str.isspace()` is False): zero-width space, ZWNJ, ZWJ, word joiner, BOM /
# zero-width no-break space. A grounding field made only of these is empty for completeness.
_ZERO_WIDTH_CHARS = "\u200b\u200c\u200d\u2060\ufeff"  # ZWSP, ZWNJ, ZWJ, word-joiner, BOM/ZWNBSP
_ZERO_WIDTH_TRANSLATION = dict.fromkeys(map(ord, _ZERO_WIDTH_CHARS))


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
    if not isinstance(value, str):
        return True
    return not value.translate(_ZERO_WIDTH_TRANSLATION).strip()


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
            Dict with notice status and optional error code (clinical fields redacted).

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

            # Transmissao: monta a notificacao formal (carrega os campos clinicos) e REDIGE o PHI
            # (redacao unidirecional) antes de qualquer variavel deixar o worker. Nunca loga o
            # conteudo clinico — apenas o registro de transmissao.
            self.logger.info("auth_denial_sent", tenant_id=tenant_id)
            notice = {
                "status": "notice_sent",
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
            # variavel de engine. Chaves estruturais (status/event/...) passam intactas.
            return redact_phi_vars(notice)

        # APROVAR ou outro — envia aviso de aprovacao (sem campos clinicos).
        self.logger.info(
            "auth_approval_notice_sent",
            tenant_id=tenant_id,
        )
        return {
            "status": "notice_sent",
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
        """Notify coordinator about SLA risk.

        Args:
            process_vars: Must include sla_percent and sla_analise.

        Returns:
            Dict with notification status.
        """
        tenant_id = process_vars.get("tenant_id", "")
        sla_percent = process_vars.get("sla_percent", 0)
        sla_analise = process_vars.get("sla_analise", "")

        self.logger.warning(
            "auth_sla_risk_notified",
            tenant_id=tenant_id,
            sla_percent=sla_percent,
            sla_analise=sla_analise,
        )

        return {
            "status": "risk_notified",
            "alert_to": "coordenacao-auditoria-medica",
            "sla_percent": sla_percent,
            "sla_analise": sla_analise,
            "event": "agents.events.auth.sla_breached",
        }


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
# directly, one WorkerBase() instance per topic. auth.py's 6 classes take no
# constructor args (none declares a Kafka dependency today) — `kafka`/`seams`
# are accepted for signature parity with the other 15 register_<domain>_workers
# bootstraps and `register_all_workers` (ADR-0026 Decisao §4), unused here.
# ---------------------------------------------------------------------------


def register_auth_workers(
    harness: WorkerHarness,
    kafka: KafkaPublisher | None = None,
    **seams: Any,
) -> None:
    """Register the 6 SP-OP-AUTH-001 `WorkerBase` workers on `harness`."""
    del kafka, seams  # unused — no auth.py worker declares a Kafka/other seam dependency
    for worker_cls in (
        AnalyzeRequestWorker,
        RequestDocumentsWorker,
        IssueAuthorizationWorker,
        SendDenialNoticeWorker,
        NotifySlaRiskWorker,
        ConveneJuntaWorker,
    ):
        harness.register_worker(worker_cls())
