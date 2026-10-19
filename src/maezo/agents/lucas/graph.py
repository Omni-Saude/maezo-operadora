"""Lucas Ferreira — Atendimento e Cobranca ao Beneficiario Agent (Phase 2, T1.12).

Journey map (mirrors the v1 donor's structure, READ-ONLY reference `Maezo-Healthcare-Plan
src/maezo/agents/lucas/graph.py`, adapted to v2's flatter seam set — same rationale as
`agents/helena/graph.py`'s and `agents/rafael/graph.py`'s module docstrings; SOURCE OF TRUTH for
any donor/spec disagreement is v2's `spec/agents/lucas/agent.yaml` + `spec/processes/`):

    receive -> gather -> assess -> {respond_member | escalate_human} -> start_process -> complete

ONE agent, THREE journeys — distinguished by `intencao` in state, NEVER by a merit Lucas decides:

  J1 (cobranca_info): boleto/2a via/vencimento doubt -> `assess` evaluates
     `lucas_billing_admissibility` -> `respond_member` drafts/sends an informational WhatsApp
     message. No adverse outcome ever originates here. Desde DL-0086 (08/10/2026) a J1 tambem
     responde pergunta de VALOR (`tipo_solicitacao=consulta_valores`: mensalidade, coparticipacao,
     saldo, historico, data de pagamento) com os fatos do contrato `billing-status` — so' quando os
     fatos chegaram (`valores_disponiveis`, entrada da DMN), sem atraso neles (`valores_com_atraso`
     escala) e em TEXTO FIXO montado dos fatos (`texto_valores`, nenhum modelo redige valor). Os
     fatos de valor so' existem para beneficiario VERIFICADO com `MAEZO_LUCAS_CONSULTA_VALORES`.

  J2 (confirmacao_pagamento): "was this payment conciliated?" -> Lucas READS the CNAB
     conciliation status PRE-RESOLVED by a worker upstream (`status_conciliado`,
     `ciclos_sem_conciliacao`) — he NEVER computes or invents it (ADR-0012) — and reports what
     conciliation says via `lucas_billing_admissibility`. A non-conciliated / inadimplencia
     signal routes to `escalate_human`. A intencao `inadimplencia` ("minha mensalidade esta em
     aberto?") segue este MESMO caminho desde DL-0082 (08/10/2026): ela e' dica de jornada, e
     quem decide sao os fatos via DMN — conciliado responde "em dia" com texto fixo, atraso escala.

  J3 (cancelamento | contestacao_cobranca | pedido de cancelamento sinalizado):
     ALWAYS escalates to a human (SP-OP-ESCALATION-001; SP-OP-CANCEL-001 is where the human
     decides RESCINDIR/MANTER/SUSPENDER). Lucas NEVER suspends, cancels, or communicates any
     adverse outcome himself — this journey never even reaches the billing DMN.

`assess` ALWAYS evaluates the deterministic DMN tables (ADR-0012: `lucas_billing_admissibility`
decides RESPONDER/LEMBRETE/ESCALAR_HUMANO; `lucas_escalation_routing` decides which human GROUP a
case suggests, never an adverse outcome) — the LLM never decides a rule, only drafts prose over
an already-decided fact. Both tables are evaluated ENGINE-SIDE via `DmnTransport`
(`tools/workers/dmn_transport.py`, ADR-0028) — this module never re-implements a decision table
in Python.

L0 HARD INVARIANT (`spec/processes/dmn/lucas_billing_admissibility.dmn` +
`lucas_escalation_routing.dmn`'s own `<description>`, CI-enforced by
`test_route_type_admits_no_adverse_variant` in `tests/unit/agents/test_lucas.py`): `Route` admits
ONLY `{"respond_member", "escalate_human"}` — no cancel/suspend/deny/rescind variant exists in
the type. `AdmissibilidadeCobranca` admits ONLY `{"RESPONDER", "LEMBRETE", "ESCALAR_HUMANO"}` —
no COBRAR/SUSPENDER/CANCELAR/NEGAR output exists. `RoteamentoEscalacao` admits ONLY
`{"ATENDIMENTO_HUMANO", "COBRANCA_HUMANO", "CONTRATOS_HUMANO"}` — every destination is a HUMAN
GROUP, never an adverse outcome. The dossier's `decisao_cancelamento` field is ALWAYS `None`
(`_build_dossier`) — a guardrail making it explicit the decision belongs to the human, never to
this code (mirrors Rafael's `decisao_cobertura` guardrail).

FAIL-SAFE FECHADO (not fail-OPEN): any DMN unavailable, any DMN output outside its recognized
allowlist, or an unknown/missing `intencao` routes to `escalate_human` — NEVER a silent default
or a "no adverse signal" happy path. `receive`'s `intencao` gate is Lucas's "classification"
fail-closed guard (mirrors Helena's `_classify_llm` fail-closed discipline, T1.11 R1 cycle-1):
an unrecognized `intencao` is treated as a CLASSIFY FAILURE, never silently read as J1.

ENGINE-VARIABLE HYGIENE (mirrors Helena's T1.11 R1 cycle-2 fix): every failure reason
(`motivo_humano`, `dmn_error` prefix) is a bounded CLASS TOKEN — `ambiguidade`,
`dmn_indisponivel`, `falha_tecnica`, `inadimplencia_detectada`, `pedido_cancelamento`,
`contestacao_cobranca` — never a raw DMN-returned value, never a repr(), never a truncation of
untrusted data. A DMN `roteamento` value outside its allowlist is NEVER echoed into any
engine-bound variable or log — only the class token `ambiguidade` is recorded.

R1 CYCLE-1 (verifier REVISE — two real defects found in this graph's first revision, fixed
here; disclosed, not hidden):
- F1 (fail-OPEN case loss): a CALLER-planted `error` short-circuited `gather`/`assess` (no
  `route` stamp); `_route`'s conservative default still ran `escalate_human` (dossier built,
  beneficiary ACK SENT), but the old `start_process` gate (`route != "escalate_human"` -> skip)
  then silently dropped the start — the beneficiary was promised a human while ZERO engine
  instances existed. Fixed both ways: `escalate_human` stamps `route="escalate_human"`
  authoritatively in its own output (F1a), and `start_process` fails CLOSED — only an explicit
  `route == "respond_member"` skips the start (F1b).
- F2 (caller-planted output-field passthrough): on skip-assess shortcuts, OUTPUT-ONLY state
  fields (dmn_refs, motivo_*, severidade, grupo_humano, dossier, ...) planted by the caller
  flowed verbatim into SP-OP-ESCALATION-001 engine variables (verifier's live probe: a planted
  `dmn_refs` entry reached `dmn_decision_refs` via the `ambiguidade` path). Fixed at `receive`:
  `_OUTPUT_FIELDS_RESET` resets every output-only field on entry (plus `_escalate_min`'s
  explicit `dmn_refs` clear) — same defect class and fix shape as the sibling agents' cycles.

INPUT-BOUNDARY GATE (T1.11 layer 1 — CC-14/LUC-09a/RAF-13; the F2 reset above is layer 2). The
reset is a HAND-ENUMERATED list, so a `LucasState` field added later would be silently un-reset
AND silently caller-settable — the drift `spec/agents/lucas/agent.yaml` recorded as the open
security prerequisite for any inbound channel (gap 11.7). Layer 1 closes it: `_CALLER_INPUT_FIELDS`
declares the complete INPUT half, an import-time guard REFUSES TO LOAD THE MODULE if any state key
is unclassified (or double-classified), and `new_lucas_state` (strict, raises `ValueError` naming
the keys) / `gate_inbound_state` (lenient, drops + logs key NAMES only) are the two construction
seams. WHAT THIS DOES NOT DO: it does not give Lucas an inbound channel. Gap 11.7 stays open —
`accepted_task_types` is still `[]` and a beneficiary reply still arrives, if at all, through
Helena's single webhook without billing context. The gate is the ORDERED PREREQUISITE, built so
the seam is gated on the day it lands (CC-02), which is the posture `agents/rafael/graph.py`
already took for its own absent delegation seam.

PHI discipline: every LLM call in this module passes `phi=True` (ADR-0006/ADR-0017/T1.7) — the
one PHI-tagged content boundary is state derived from a beneficiary's billing case, treated the
same as Helena's/Rafael's PHI-tagged content.

DIVERGENCES FROM THE v1 DONOR (disclosed, not hidden — `spec/` wins per this task's charter):
- No `lucas_sla` DMN evaluation: `spec/processes/dmn/` has no `lucas_sla` table (only
  `lucas_billing_admissibility` and `lucas_escalation_routing` exist under `spec/processes/dmn/`,
  confirmed against `spec/processes/dmn/orphans-allowlist.yaml`). The donor's SLA annotation step
  is dropped entirely rather than fabricated.
- No `resume_ack`/GAP-XHITL-4B retomada node: v2 has no `resume_driver`/`RESUME_STATE_BUILDERS`
  infrastructure yet (Helena's graph does not implement this either — same labeled boundary,
  "no multi-turn conversation checkpointing across separate webhook deliveries").
- Model-tier routing (`task_default`/`reasoning`) — DISCLOSURE CORRECTED, AF-12 (2026-09-03).
  WAS: "v2's `InferenceProvider.generate(prompt, phi=True)` has no `task_kind` parameter
  (ADR-0009's tiering is not wired to agents yet)". The parameter now exists and this graph
  passes it: `_build_message` is `task_default` (phrasing already-known facts; o ACK de escalacao
  e' texto fixo desde DL-0082, sem modelo). A narrativa do dossie (`_build_dossier`) era
  `reasoning` ate' 09/10/2026; desde entao e' TEXTO FIXO dos fatos (`texto_dossie`), sem modelo.
  WHAT THAT DOES AND DOES NOT BUY, precisely: `lucas/agent.yaml`'s declared tiers now reach the
  provider, are validated fail-closed against the ADR-0009 vocabulary at construction, and are
  counted per call on `maezo_llm_tier_resolution_total`. It does NOT change which model runs —
  the repo configures exactly ONE model and no per-tier map exists, so both kinds resolve to it
  (`InferenceProvider._resolve_task_model`, and `docs/review-queue.md` for the owner decision the
  per-tier model values are).
- No `mcp-memory.read_write` episodic write in `finalize`: same disclosed boundary as
  Helena/Rafael ("no episodic memory write (ADR-0002) — a follow-up once that schema exists").
  The terminal node is a no-op `complete`, matching Rafael's naming/shape exactly.
- `receive`'s failure-reason text NEVER echoes the raw `intencao`/field value (donor's original
  `f"intencao desconhecida: {intencao!r}"` is replaced with a bounded class-token message) — the
  NON-NEGOTIABLE engine-variable hygiene requirement for this task, matching the fix Helena
  needed in T1.11 R1 cycle-2.
- `escalate_human` ADDITIONALLY drafts + sends a short WhatsApp acknowledgement (never revealing
  the adverse-outcome-in-waiting) before `start_process` — the donor's `escalate_human` never
  notified the beneficiary at all. This mirrors Helena's convergent `respond` node (every turn,
  informational or escalated, ends with the beneficiary told what happens next) — a deliberate
  improvement over the donor, not a silent deviation.
- No `matricula_beneficiario` fallback correlation key: v2's `spec/agents/lucas/agent.yaml` does
  not declare one; `conversation_id` is the sole correlation key, matching Helena's
  `SP-OP-ESCALATION-001` business-key usage exactly (same shared contract).
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Final, Literal, Protocol, TypedDict, cast

import structlog
from langgraph.graph import END, START, StateGraph

from maezo.agents.lucas.fonte_cobranca import PAGADOR_EMPRESA
from maezo.agents.lucas.fonte_v4 import (
    MARCADOR_CODIGO,
    MAX_BENEFICIARIOS_NO_TEXTO,
    SEGUNDA_VIA_COM_CODIGO,
    SEGUNDA_VIA_SEM_CODIGO,
)
from maezo.agents.lucas.politica_atraso import LimiaresDeAtraso
from maezo.platform.observability import record_resposta_recusada
from maezo.runtime.cobranca_foco import (
    FOCO_COMO_PAGAR,
    FOCO_CONSEQUENCIA_DO_ATRASO,
    FOCO_ESCLARECIMENTO,
    FOCO_EXTRATO,
    FOCO_GERAL,
    FOCO_JA_PAGUEI,
    FOCO_LINK_SUSPEITO,
    FOCO_PAGAMENTO_AO_PRESTADOR,
    FOCO_ULTIMA,
    foco_valido,
)
from maezo.runtime.cobranca_lista import PedidoDeLista, lista_do_handoff
from maezo.runtime.cobranca_reclamacao import reclamacao_valida
from maezo.runtime.dependency_failures import EXTERNAL_DEPENDENCY_FAILURES, PROGRAMMING_ERRORS
from maezo.runtime.error_text import (
    dmn_unavailable_error,
    start_unavailable_error,
)
from maezo.runtime.inference import InferenceProvider
from maezo.runtime.prompt_format import render_fatos_para_prompt
from maezo.runtime.start_outcome import (
    notify_start_failure as emit_start_failure_notice,
)
from maezo.runtime.start_outcome import (
    route_after_start,
    start_failed_state,
)
from maezo.runtime.turn_telemetry import emit_turn_desfecho
from maezo.tools.mcp_cibseven.transport import (
    AgentDecisionProvenance,
    AuditStartSink,
    CibSevenError,
    CibSevenTransport,
    StartDedupGateUnavailableError,
    StartOutcome,
    start_process_idempotent,
)
from maezo.tools.workers.base import CANCEL_KEY_FAMILY, mint_contract_business_key
from maezo.tools.workers.dmn_transport import (
    DmnEvaluationError,
    DmnNoResultError,
    DmnTransport,
    first_row,
)

from .cancelamento_pf import (
    TEXTO_DEPENDENTE,
    TEXTO_DEPENDENTE_CURTO,
    TEXTO_PEDIDO_JA_REGISTRADO,
    TEXTO_PEDIDO_NAO_REGISTRADO,
    TEXTO_PEDIDO_RECEBIDO,
    TIPOS_PLANO_PF,
    protocolo_do_cancelamento,
)
from .prompts import (
    COBRANCA_ADMINISTRADORA_TEXTO_VERSION,
    DOSSIE_TEXTO_VERSION,
    ESCALATION_ACK_PROMPT_VERSION,
    MENSAGEM_EM_DIA_VERSION,
    MESSAGE_PROMPT_VERSION,
    PAGADOR_EMPRESA_TEXTO_VERSION,
    RECUSA_DE_SAIDA_VERSION,
    SEGUNDA_VIA_TEXTO_VERSION,
    SYSTEM_PROMPT_VERSION,
    VALORES_PROMPT_VERSION,
    VENCIMENTO_PROMPT_VERSION,
    message_prompt,
    motivo_de_recusa,
)

logger = structlog.get_logger(__name__)

PROCESS_KEY = "SP-OP-ESCALATION-001"
#: O processo de cancelamento (rodada 12, 14/10/2026): o Lucas o inicia SO' com o pedido CONFIRMADO de um
#: beneficiario cujo titulo e' dele (pagador PF) — pelo mesmo start idempotente e auditado do escalonamento.
CANCEL_PROCESS_KEY = "SP-OP-CANCEL-001"
#: O `source_agent_id` com que o Lucas abre a escalacao (lido de volta para saber se o caso aberto e' dele).
AGENT_ID_LUCAS: Final[str] = "lucas"

DMN_BILLING_ADMISSIBILITY = "lucas_billing_admissibility"
DMN_ESCALATION_ROUTING = "lucas_escalation_routing"

# --- Domain enums (mirror the SP-OP-ESCALATION-001 contract + the lucas_* DMN schemas) -------

Intencao = Literal["cobranca_info", "confirmacao_pagamento", "inadimplencia", "cancelamento"]
_VALID_INTENCOES: frozenset[str] = frozenset(
    {"cobranca_info", "confirmacao_pagamento", "inadimplencia", "cancelamento"}
)

# STRUCTURALLY SAFE BY CONSTRUCTION — no adverse variant exists in this type (L0 hard, CI-enforced
# by `test_route_type_admits_no_adverse_variant`).
Route = Literal["respond_member", "escalate_human"]

# Allow-list mirrors `spec/processes/dmn/lucas_billing_admissibility.dmn`'s `roteamento` output
# domain EXACTLY — no COBRAR/SUSPENDER/CANCELAR/NEGAR value exists there or here.
AdmissibilidadeCobranca = Literal["RESPONDER", "LEMBRETE", "ESCALAR_HUMANO"]
_ADMISSIBILIDADE_ALLOW: frozenset[str] = frozenset({"RESPONDER", "LEMBRETE", "ESCALAR_HUMANO"})

# Allow-list mirrors `spec/processes/dmn/lucas_escalation_routing.dmn`'s `roteamento` output
# domain EXACTLY — every destination is a HUMAN GROUP, never an adverse outcome.
RoteamentoEscalacao = Literal["ATENDIMENTO_HUMANO", "COBRANCA_HUMANO", "CONTRATOS_HUMANO"]
_ROTEAMENTO_ESCALACAO_ALLOW: frozenset[str] = frozenset(
    {"ATENDIMENTO_HUMANO", "COBRANCA_HUMANO", "CONTRATOS_HUMANO"}
)

# `roteamento` (Lucas's own suggestion DMN) -> `grupo_humano_sugerido` annotation carried in the
# dossier/process variables for the human's benefit. This is NOT the same as the shared
# `escalation_routing` DMN that SP-OP-ESCALATION-001's own `BRT_RotearEscalonamento` evaluates
# engine-side (keyed on `motivo_categoria`/`severidade`, whose own `grupo_atendimento` domain —
# `docs/processes/contracts/SP-OP-ESCALATION-001.md` — is `plantao-clinico` |
# `enfermagem-triagem` | `atendimento-humano`, with no dedicated billing/contracts group). The
# ACTUAL BPMN candidate group always comes from that shared table, never from this map — this map
# only feeds the dossier's `grupo_humano_sugerido` (an instruction, never a decision).
_ESCALACAO_GRUPO: dict[RoteamentoEscalacao, str] = {
    "ATENDIMENTO_HUMANO": "atendimento-humano",
    "COBRANCA_HUMANO": "atendimento-humano",
    "CONTRATOS_HUMANO": "gestao-contratos",
}

# Reason Lucas routes to a human. NONE of these is an adverse outcome — every one is a reason for
# HANDOFF; the decision (rescindir/suspender/negar) is always the human's. Mirrors
# `lucas_escalation_routing.dmn`'s `motivo` input literals exactly (single-sourced: the DMN table
# only recognizes `pedido_cancelamento`/`inadimplencia_detectada`/`contestacao_cobranca` by name;
# every other value here falls on that table's own conservative catch-all).
MotivoHumano = Literal[
    "inadimplencia_detectada",
    "pedido_cancelamento",
    "contestacao_cobranca",
    "ambiguidade",
    "dmn_indisponivel",
    "falha_tecnica",
    "pedido_negociacao",
]

# Subset of the shared SP-OP-ESCALATION-001 `motivo_categoria` domain
# (`docs/processes/contracts/SP-OP-ESCALATION-001.md`) — Lucas is billing/collection, never
# clinical, so `red_flag_clinico`/`risco_psicossocial`/`intencao_clinica` are structurally
# unreachable from this module (L0 hard — see `test_motivo_categoria_never_clinical` below).
#
# `solicitacao_humano` IS kept (type-fidelity with the shared domain — helena emits it for
# exactly this case, `helena/graph.py:709`) but is DISCLOSED, OWNER-GATED UNREACHABLE from
# Lucas today (LUC-04, Agent Fleet Audit): `Intencao` below has no "beneficiario pede um
# humano" variant, so a beneficiary explicitly asking to talk to a person is misfiled as
# ambiguidade->cobranca instead of the correct category. Wiring a real path needs a new
# `Intencao` value the `lucas_escalation_routing.dmn`/`lucas_billing_admissibility.dmn`
# inputs would have to recognize — both are DRAFT tables, so this needs the SAME owner
# ratification LUC-03 already names, not a Python-only fix (C3: no business rule invented
# here). See `test_solicitacao_humano_is_disclosed_as_unreachable_pending_ratification`.
#
# `cobranca` (onda (b) do plano `docs/plans/lucas-numero-unico.md`, 01/10/2026): toda passagem de
# NEGOCIO do Lucas (inadimplencia, contestacao, cancelamento, ambiguidade) declara o que e' —
# cobranca — em vez do `outro` que parecia decidido e ninguem decidiu (review-queue, ADJ-3). A DMN
# `escalation_routing` NAO muda: `cobranca` cai no mesmo catch-all `r7` que o `outro` caia.
MotivoCategoria = Literal["cobranca", "solicitacao_humano", "falha_tecnica"]


class WhatsAppSender(Protocol):
    """Outbound WhatsApp send seam — mirrors `helena/graph.py::WhatsAppSender` on the METHOD NAME
    (agent independence: redeclared here, not imported, per ADR-0004's federated-definition
    zero-cross-contamination stance), but NO LONGER on the signature (LUC-08, gap
    `IDEMPOTENCY-KEY-MISSING`): an engine re-delivery of the same turn used to resend the
    informational WhatsApp message or the escalation ACK with no way to dedupe it — the seam
    itself carried no key. `idempotency_key` is REQUIRED, never a silently-optional extra, so no
    caller can forget it.

    ESCOPO DA CHAVE (§Delta W4-HYGIENE F1, corrigido — a versao anterior desta frase era falsa).
    A chave e' `f"{business_key}:{node}:{_turn_fingerprint(state)}"`: CONVERSA + NO + TURNO.
    Sem o terceiro componente ela seria por CONVERSA (`ESC-{tenant}-{conversation_id}` repete em
    todo turno, e o proprio repo classifica essa chave como NON_STRICT/"repete por design" em
    `tools/mcp_cibseven/transport.py`), e duas mensagens GENUINAMENTE DIFERENTES ao mesmo
    beneficiario dentro do TTL de 24h colapsariam numa chave so'. Ver `_turn_fingerprint` para o
    que compoe o componente por turno e para o residual DECLARADO (dois turnos com entrada do
    chamador byte-identica sao indistinguiveis aqui).

    O QUE A CHAVE ALCANCA, dito sem promessa maior do que a fiacao entrega:
    `agents/lucas/adapters.py::WhatsAppServerSender.send` a encaminha sem alteracao para
    `tools/mcp_whatsapp/server.py::WhatsAppServer.send_message`. Quando o root de composicao
    ligou um `DedupRegistry` (`gateway/tool_registry.py::build_agent_seams` liga, a partir do
    MESMO `settings.database_url` que ja constroi o `PostgresAuditSink`), a chave e' reclamada
    contra o store duravel EXISTENTE (ADR-0024, tabela `driver_idempotency`, gap
    `WEBHOOK-WAMID-DEDUP`, R-073) — nenhuma tabela nova, nenhum dedupe em memoria — com janela
    igual ao TTL padrao do proprio store (24h, `platform/driver_idempotency.py::DEFAULT_TTL_S`),
    nao um numero inventado aqui. Quando NAO ligou (um root sem DSN), `send_message` RECUSA o
    envio com `WhatsAppIdempotencyUnsupportedError` — nunca aceita-e-ignora a chave, e a recusa
    cai no `except Exception` best-effort dos dois pontos de chamada, que gravam
    `mensagem_enviada=False`.

    DESFECHO DE ENTREGA HONESTO (§Delta W4-HYGIENE F1b): os chamadores leem o RETORNO deste
    metodo, nunca a ausencia de excecao. `{"suppressed_duplicate": True}` NAO e' uma entrega —
    ver `_entrega_de`, `respond_member` e `send_escalation_ack`.

    Operates on a phone HASH, never a raw number."""

    async def send(self, to_hash: str, text: str, *, idempotency_key: str) -> dict[str, Any]: ...


# Output-only state fields — written EXCLUSIVELY by this graph's own nodes, never legitimate
# caller input. `receive` resets EVERY one of them on entry (R1 cycle-1 F2): a caller-planted
# value in any of these would otherwise flow VERBATIM into SP-OP-ESCALATION-001 engine variables
# on the skip-assess shortcuts (verifier's live probe: a planted `dmn_refs` entry reached
# `dmn_decision_refs` via the natural `ambiguidade` path). `error` is included deliberately: it
# is a node-written signal (receive's guards / start_process failures), and a caller-planted
# `error` was ALSO the R1 cycle-1 F1 fail-OPEN vector (gather/assess short-circuit on it).
# Literal-typed fields reset to "" (a value outside every allowlist — every consumer treats it
# as absent/conservative); containers reset to their empty shape. `business_key` is re-derived
# by `receive` itself (success path) or `start_process` (falsy -> `_business_key(state)`).
# This dict is ALSO the OUTPUT half of the T1.11 input/output partition — see
# `_CALLER_INPUT_FIELDS` below, whose import-time guard makes the two halves cover `LucasState`
# exactly. Adding a key here without removing it from `_CALLER_INPUT_FIELDS` (or vice versa)
# fails the module import, not a later test run.
_OUTPUT_FIELDS_RESET: dict[str, Any] = {
    "gathered": False,
    "caso_aberto_cobranca": False,
    "avisos_de_caso_aberto": 0,
    "cancelamento_resposta": "",
    "iniciar_cancelamento": False,
    "resposta_sem_dados": False,
    "resposta_memoria_ref": "",
    "billing_facts": {},
    "gather_notes": [],
    "admissibilidade": "",
    "roteamento_escalacao": "",
    "dmn_refs": {},
    "dmn_error": "",
    "route": "",
    "motivo_humano": "",
    "motivo_categoria": "",
    "severidade": "",
    "grupo_humano": "",
    "mensagem": {},
    "mensagem_enviada": False,
    "ack_pending": False,
    "retryable_error": False,
    "dossier": {},
    "process_started": False,
    "start_failed": False,
    "business_key": "",
    "process_ref": {},
    "desfecho": "",
    "error": "",
}


# --- Graph state (working memory; ADR-0002 working-memory layer) -----------------------------


class LucasState(TypedDict, total=False):
    """Billing/collection case state. Every field is pseudonymized (Zona Geral, ADR-0006) —
    `beneficiario_pseudo_id` NEVER carries a raw CPF/name/phone number. `status_conciliado`/
    `ciclos_sem_conciliacao` arrive PRE-RESOLVED by a deterministic CNAB conciliation worker
    upstream — Lucas CONSUMES them, never computes them (ADR-0012, L0 hard — see
    `test_conciliation_facts_passed_through_unchanged_never_recomputed` in
    `tests/unit/agents/test_lucas.py`)."""

    # Runtime identifiers (injected by the calling layer at turn start).
    tenant_id: str
    conversation_id: str
    canal: str  # whatsapp | portal | telefone
    beneficiario_pseudo_id: str
    to_hash: str  # WhatsApp destination hash (never the raw number)

    # Turn input — `intencao` selects the JOURNEY, never a merit Lucas decides.
    intencao: Intencao
    tipo_solicitacao: str  # boleto | 2a_via | vencimento | status_pagamento | consulta_valores
    numero_boleto: str
    competencia: str  # YYYY-MM
    #: DL-0087 (10/10/2026): o campo perguntado numa `consulta_valores` (`runtime/cobranca_foco.py`),
    #: decidido pela Helena sobre o texto. Valor fora do dominio vira `geral` (`_foco_da_pergunta`).
    cobranca_foco: str
    #: 12/10/2026 (teste real do dono): o PEDIDO DE LISTA ("as ultimas tres pagas", "de julho a setembro",
    #: `runtime/cobranca_lista.py`), decidido sobre o texto antes do Lucas. Forma errada e' ignorada.
    cobranca_lista: dict[str, Any]
    #: 13/10/2026 (simulador): a VARIANTE de redacao deste turno (a revisao do roteador, que sobe uma por
    #: mensagem). Alterna os textos que poderiam se repetir (caso aberto, 2a via de novo).
    variante_de_redacao: int
    #: 13/10/2026 (rodada 3): a referencia (16 hex do sha256) do ULTIMO texto que o Lucas enviou nesta
    #: conversa e quantos avisos de caso aberto ele ja' deu (migration 0025). Nunca o texto.
    resposta_anterior_ref: str
    avisos_anteriores: int
    #: Rodada 4 (13/10/2026): o TIPO da reclamacao de cobranca (`runtime/cobranca_reclamacao.py`), enum.
    cobranca_reclamacao: str
    #: Rodada 12 (14/10/2026): o beneficiario pediu para NEGOCIAR/PARCELAR (`runtime/cobranca_intencao.py`).
    #: O Lucas nunca negocia: abre o caso humano.
    pedido_negociacao: bool
    #: Rodada 12: "confirma"/"nega" — a resposta do beneficiario a "Voce quer pedir o cancelamento do seu
    #: plano?". So' vale com a pergunta pendente (a referencia do ultimo texto enviado).
    confirmacao_cancelamento: str
    #: 13/10/2026: o assunto (`cobranca_subtipo`) da resposta ANTERIOR do Lucas nesta conversa, quando ele
    #: ainda estava com ela; ausente caso contrario. So' enum, nunca texto.
    assunto_anterior: str

    # Pre-resolved by a deterministic CNAB conciliation worker upstream — CONSUMED, never
    # computed here (module docstring's L0-hard invariant).
    status_conciliado: bool
    ciclos_sem_conciliacao: int
    contesta_cobranca: bool
    pedido_cancelamento: bool
    cnab_ref: str

    # FATOS DE VALOR (DL-0086): o que a fonte (`FatosCobranca.como_entrada_lucas`) entregou do
    # contrato `billing-status`. AUSENTES quando a fonte nao os tem (a simulada nunca os tem).
    # Consumidos como vieram — o grafo nao soma, nao subtrai e nao completa nenhum.
    valor_em_aberto: str  # decimal em texto, "640.50"
    dias_atraso_max: int
    vencimento_referencia: str  # AAAA-MM-DD
    competencias_cobranca: list[dict[str, Any]]
    #: 13/10/2026: quem paga (`resumo.pagador_tipo`): `pessoa_juridica` (empresa contratante),
    #: `pessoa_fisica` ou `desconhecido`. Fato, nunca veredito; vira a entrada `pagador_empresa` da DMN.
    pagador_tipo: str
    #: 13/10/2026 (bateria 4): coletivo por ADESAO cobrado pela administradora (fato da fonte). O boleto do
    #: beneficiario vem da administradora; o titulo do Tasy (situacao, vencimento) e' dela, nao da pessoa.
    cobranca_administradora: bool
    #: Wave 2 (14/10/2026): a pessoa e' o proprio pagador PF do boleto (`True`) ou nao (`False`, dependente).
    #: Ausente quando a fonte nao sabe. O total do boleto da familia viaja em cada competencia.
    beneficiario_e_pagador: bool
    #: DL-0114 (chave `MAEZO_LUCAS_V4`): fatos dos contratos novos da AMH (billing-status 0.4.0 e
    #: billing-segunda-via), AUSENTES enquanto eles nao estiverem publicados, pinados e ligados. Nenhum e'
    #: credencial: o codigo de barras NUNCA entra no estado (so' o marcador no texto, `fonte_v4`).
    responsavel_financeiro: str  # voce | outra_pessoa | empresa | administradora | desconhecido
    detalhamento_pagador: list[dict[str, Any]]  # total e beneficiarios SEM nome, por competencia
    segunda_via_estado: str  # com_codigo | sem_codigo
    segunda_via_boletos: list[dict[str, Any]]  # competencia, vencimento, situacao, valor_titulo
    #: DL-0111 (chave `MAEZO_CANCEL_PF_V1`): os FATOS do cancelamento apurados da AMH
    #: (`cancelamento_pf.FatosCancelamento.como_entrada_lucas`). AUSENTES com a chave desligada ou quando a
    #: fonte nao afirma. Fato, nunca veredito: a DMN do processo e as pessoas decidem.
    cancelamento_titular: bool
    cancelamento_tipo_plano: str
    cancelamento_vinculo_ativo: bool
    cancelamento_identidade_verificada: bool

    # Filled by `gather`.
    gathered: bool
    #: 13/10/2026 (simulador): ja' ha' um caso de COBRANCA aberto pelo Lucas nesta conversa (instancia viva
    #: de SP-OP-ESCALATION-001 com `source_agent_id=lucas`). Lido do motor, nunca do chamador.
    caso_aberto_cobranca: bool
    #: Os avisos de caso aberto dados neste caso DEPOIS deste turno (o roteador os guarda; depois de
    #: `AVISOS_DE_CASO_ABERTO_MAX`, so' o fato).
    avisos_de_caso_aberto: int
    #: Rodada 12: o que o Lucas responde a um pedido de CANCELAMENTO sem abrir o processo: `empresa`,
    #: `administradora` (o titulo e' de terceiro), `confirmar` (a pergunta unica) ou `desistiu`.
    cancelamento_resposta: str
    #: Rodada 12: o pedido foi CONFIRMADO (PF, pergunta pendente) — `start_process` inicia SP-OP-CANCEL-001.
    iniciar_cancelamento: bool
    #: Rodada 12: a fonte nao entregou fatos (indisponivel): a resposta e' a constante honesta, sem caso.
    resposta_sem_dados: bool
    #: Rodada 4 (13/10/2026): a referencia que o roteador guarda deste turno — o DEGRAU da resposta na
    #: escada (`_escada`), para a proxima repeticao seguir dele. Hash truncado; nunca o texto.
    resposta_memoria_ref: str
    billing_facts: dict[str, Any]
    gather_notes: list[str]

    # Filled by `assess`.
    admissibilidade: AdmissibilidadeCobranca
    roteamento_escalacao: RoteamentoEscalacao
    dmn_refs: dict[str, str]
    dmn_error: str
    route: Route
    motivo_humano: MotivoHumano
    motivo_categoria: MotivoCategoria
    #: Sempre `None` nas escritas reais: os dois motivos que o Lucas emite (`cobranca`,
    #: `falha_tecnica`) sao isentos de `severidade` no contrato (`_severidade_humano`). O `""` do
    #: reset de `receive` e' o "ainda nao resolvido" que o worker recusa fail-closed.
    severidade: str | None
    grupo_humano: str

    # Filled by `respond_member` / `escalate_human` / `send_escalation_ack`.
    mensagem: dict[str, Any]
    mensagem_enviada: bool
    #: LUC-05: o ACK da escalacao ainda NAO foi ao beneficiario. True desde `escalate_human`
    #: (que so monta o dossie) ate `send_escalation_ack` (que roda DEPOIS do start). Se o start
    #: falha, permanece True — e o registro honesto de "prometemos nada a ninguem ainda".
    ack_pending: bool
    #: LUC-05: a falha e transitoria e o replay e seguro (business key idempotente).
    retryable_error: bool
    dossier: dict[str, Any]

    # Filled by `start_process`.
    process_started: bool
    #: CC-01: o start foi TENTADO e FALHOU tecnicamente (`except CibSevenError` de
    #: `start_process`). NAO e a mesma coisa que `process_started is False`, que tambem cobre
    #: no-ops legitimos; e este marcador — e so ele — que a aresta condicional le.
    start_failed: bool
    business_key: str
    process_ref: dict[str, Any]

    # Output.
    desfecho: str
    error: str


# --- Input boundary (T1.11 layer 1: partition + constructor) -----------------------------------
#
# `LucasState` carries TWO disjoint classes of key. Until CC-14 Lucas had ONLY layer 2 — the
# `receive`-entry reset of the HAND-ENUMERATED `_OUTPUT_FIELDS_RESET` above — which is exactly
# the gap `spec/agents/lucas/agent.yaml` recorded as the security PRE-REQUISITE for any future
# inbound channel (gap 11.7). A hand list neutralizes the output fields somebody remembered to
# list; a state field added later is silently un-reset AND silently caller-settable.
#   * INPUT-ONLY  (`_CALLER_INPUT_FIELDS`): the ONLY keys a caller/upstream may set — the runtime
#     identifiers, the turn's `intencao`/request fields, and the PRE-RESOLVED CNAB conciliation
#     facts this graph CONSUMES and never computes (module docstring's L0-hard invariant).
#   * OUTPUT-ONLY (`_OUTPUT_FIELDS_RESET`): keys OWNED by this graph's nodes (route, the DMN
#     verdicts, motivo_*/severidade/grupo_humano, mensagem*, dossier, process_*, desfecho,
#     error). A caller must NEVER set one — that is literally the R1 cycle-1 F2 defect, whose
#     live probe watched a planted `dmn_refs` entry reach `dmn_decision_refs` in
#     SP-OP-ESCALATION-001 (module docstring §R1 CYCLE-1).
#
# TWO defenses, both fail-closed (mirrors `agents/helena/graph.py`, the sibling that owns the one
# WhatsApp webhook Lucas's beneficiary replies would arrive through):
#   1. Per-graph entry sanitization (layer 2, pre-existing) — `receive` resets EVERY output-only
#      field on EVERY branch before any downstream node runs.
#   2. Input-boundary gate (layer 1, THIS block) — a construction seam assembles state ONLY
#      through the typed `new_lucas_state` constructor or the `gate_inbound_state` allowlist
#      filter, so an output-only key can never enter the state dict at all.
#
# WHY IT EXISTS BEFORE THE SEAM DOES. Lucas declares `accepted_task_types: []` and has no inbound
# channel of his own (agent.yaml gap 11.7 — STILL OPEN; this block does NOT close it and does not
# claim to). The gate is the ORDERED prerequisite: it is built now so the day a channel lands it
# is gated by construction, the same posture `agents/rafael/graph.py` took.
#
# The completeness guard below fails at IMPORT TIME if a newly added `LucasState` field is not
# classified into exactly one of the two sets — "any missed key is a hole".
_CALLER_INPUT_FIELDS: frozenset[str] = frozenset(
    {
        # Runtime identifiers (injected by the calling layer at turn start).
        "tenant_id",
        "conversation_id",
        "canal",
        "beneficiario_pseudo_id",
        "to_hash",
        # Turn input — `intencao` selects the JOURNEY, never a merit Lucas decides. It is an
        # INPUT on purpose: `receive` validates it against `_VALID_INTENCOES` and fails closed to
        # `ambiguidade`, so a hostile value cannot pick a journey, only forfeit the turn.
        "intencao",
        "tipo_solicitacao",
        "numero_boleto",
        "competencia",
        # DL-0087: o foco da pergunta de valor (enum fechado; texto nunca).
        "cobranca_foco",
        # 12/10/2026: o pedido de lista (quantidade, periodo `AAAA-MM`, situacao; texto nunca).
        "cobranca_lista",
        # 13/10/2026: variante de redacao e assunto anterior (inteiro e enum; texto nunca).
        "variante_de_redacao",
        "assunto_anterior",
        "resposta_anterior_ref",
        "avisos_anteriores",
        "cobranca_reclamacao",
        "pedido_negociacao",
        "confirmacao_cancelamento",
        # Pre-resolved by the deterministic CNAB conciliation worker upstream — CONSUMED, never
        # computed here. These are FACTS a caller may assert; every VERDICT derived from them
        # (`admissibilidade`, `roteamento_escalacao`, `route`, `severidade`) is output-only.
        "status_conciliado",
        "ciclos_sem_conciliacao",
        "contesta_cobranca",
        "pedido_cancelamento",
        "cnab_ref",
        # FATOS DE VALOR do contrato `billing-status` (DL-0086) — fatos, nunca veredito.
        "valor_em_aberto",
        "dias_atraso_max",
        "vencimento_referencia",
        "competencias_cobranca",
        # 13/10/2026: quem paga a mensalidade (enum do contrato; fato, nunca veredito).
        "pagador_tipo",
        # 13/10/2026: adesao cobrada pela administradora (booleano da fonte; fato, nunca veredito).
        "cobranca_administradora",
        # Wave 2 (14/10/2026): a pessoa e' (ou nao) o pagador PF do boleto (booleano da fonte).
        "beneficiario_e_pagador",
        # DL-0114 (`MAEZO_LUCAS_V4`): fatos dos contratos novos da AMH (sem credencial, sem nome).
        "responsavel_financeiro",
        "detalhamento_pagador",
        "segunda_via_estado",
        "segunda_via_boletos",
        # DL-0111 (`MAEZO_CANCEL_PF_V1`): fatos do cancelamento da AMH (fatos, nunca veredito).
        "cancelamento_titular",
        "cancelamento_tipo_plano",
        "cancelamento_vinculo_ativo",
        "cancelamento_identidade_verificada",
    }
)

_LUCAS_ALL_FIELDS: frozenset[str] = _CALLER_INPUT_FIELDS | frozenset(_OUTPUT_FIELDS_RESET)
if frozenset(LucasState.__annotations__) != _LUCAS_ALL_FIELDS:
    _unclassified = frozenset(LucasState.__annotations__) - _LUCAS_ALL_FIELDS
    _stale = _LUCAS_ALL_FIELDS - frozenset(LucasState.__annotations__)
    raise RuntimeError(
        "LucasState input/output field split is incomplete (T1.11 input-boundary gate, CC-14): "
        f"unclassified fields={sorted(_unclassified)} stale entries={sorted(_stale)} — every "
        "LucasState key MUST be either a `_CALLER_INPUT_FIELDS` member or carry a neutral "
        "default in `_OUTPUT_FIELDS_RESET`."
    )
if _CALLER_INPUT_FIELDS & frozenset(_OUTPUT_FIELDS_RESET):
    raise RuntimeError(
        "LucasState field classified as BOTH input and output (T1.11 input-boundary gate, "
        f"CC-14): {sorted(_CALLER_INPUT_FIELDS & frozenset(_OUTPUT_FIELDS_RESET))}"
    )


def new_lucas_state(raw: Mapping[str, Any]) -> LucasState:
    """Typed input-boundary constructor for a fresh Lucas turn (T1.11 layer 1).

    Accepts a raw mapping — the shape a future inbound channel (an A2A envelope, or a
    billing-context webhook handler) would hand over — and returns a `LucasState` containing ONLY
    `_CALLER_INPUT_FIELDS` keys.

    An unknown key is a HARD ERROR, and the error NAMES the offending keys: an internal seam is a
    contract, so a stray key means a producer bug and must fail closed, LOUDLY. Mirrors
    `agents/rafael/graph.py::new_rafael_state` and `agents/helena/graph.py::new_helena_state`.

    The two error classes (an output-only key vs a wholly-unknown key) are deliberately NOT
    distinguished beyond the key list — telling a hostile caller which of its keys the state
    model recognizes is a hint it does not need.
    """
    unknown = sorted(k for k in raw if k not in _CALLER_INPUT_FIELDS)
    if unknown:
        raise ValueError(
            "new_lucas_state received non-input keys (T1.11 input-boundary gate): "
            f"{unknown} — only `_CALLER_INPUT_FIELDS` may be set by a caller/inbound seam; "
            "output-only fields are owned by Lucas's graph nodes."
        )
    return cast(LucasState, {k: raw[k] for k in _CALLER_INPUT_FIELDS if k in raw})


def gate_inbound_state(raw: Mapping[str, Any]) -> LucasState:
    """Fail-closed input allowlist (drop-and-log variant of `new_lucas_state`).

    Only `_CALLER_INPUT_FIELDS` keys survive; every other key — any caller-planted output field,
    any unknown key — is DROPPED and LOGGED. Use where tolerating benign upstream drift is
    preferable to raising (a lenient ingestion edge, e.g. a WhatsApp webhook payload); use
    `new_lucas_state` on a strict internal delegation seam.

    LOG HYGIENE: the event carries the dropped KEY NAMES only, never their values — a planted
    value is unbounded caller-controlled content, the same reason `receive`'s failure reasons are
    bounded class tokens that never echo `intencao`.
    """
    dropped = sorted(k for k in raw if k not in _CALLER_INPUT_FIELDS)
    if dropped:
        logger.warning("lucas_inbound_output_fields_dropped", dropped=dropped)
    return cast(LucasState, {k: raw[k] for k in _CALLER_INPUT_FIELDS if k in raw})


# --- Helpers -----------------------------------------------------------------------------------


def _business_key(state: LucasState) -> str:
    """Idempotent business key per the SHARED SP-OP-ESCALATION-001 contract (same format Helena
    uses — `ESC-{tenant_id}-{conversation_id}`)."""
    return f"ESC-{state.get('tenant_id', '')}-{state.get('conversation_id', '')}"


def _turn_fingerprint(state: LucasState) -> str:
    """Componente POR TURNO da chave de idempotencia de saida (§Delta W4-HYGIENE F1).

    POR QUE EXISTE. `_business_key` e' por CONVERSA e `receive` o re-deriva em TODO turno, entao
    `f"{business_key}:{node}"` e' CONSTANTE ao longo da conversa inteira: duas mensagens
    genuinamente diferentes ao mesmo beneficiario dentro do TTL de 24h reclamariam a MESMA chave
    e a segunda nunca chegaria a Cloud API. O precedente in-repo para o MESMO store deriva a
    chave de saida POR MENSAGEM, nao por conversa
    (`platform/webhooks/whatsapp/dedup.py::WhatsAppDedupGuard.outbound_key`, derivada do `wamid`
    de ENTRADA — "the only identity available BEFORE the send").

    LUCAS NAO CARREGA UM `wamid` HOJE — dito, nao contornado. `LucasState` nao tem
    `message_id`/`wamid`: gap 11.7 (`spec/agents/lucas/agent.yaml`) e' exatamente "Lucas nao tem
    canal de entrada proprio", entao nao existe id de entrega para propagar. O identificador por
    turno MAIS ESTAVEL que o turno carrega e' a propria ENTRADA DO CHAMADOR — `_CALLER_INPUT_
    FIELDS`, a metade INPUT da particao T1.11 — que e' precisamente o que uma redelivery do
    MESMO turno reapresenta identica e o que um turno DIFERENTE muda. Ler a particao (e nao uma
    lista escrita a mao aqui) tem uma consequencia deliberada: no dia em que um id de entrega
    for declarado como campo de entrada, ele entra no digest sozinho, sem tocar esta funcao.

    O digest e' sha256 TRUNCADO em 16 hex: nenhum valor de estado — nem pseudonimo, nem numero de
    boleto — aparece em claro na chave que vai ao store duravel, e a chave continua curta o
    bastante para caber ao lado do `business_key`. Truncar nao enfraquece nada aqui: a colisao
    relevante e' entre turnos de UMA conversa dentro de 24h, nao um espaco adversarial.

    RESIDUAL DECLARADO: dois turnos cuja entrada do chamador e' byte-identica produzem o MESMO
    fingerprint e o segundo envio e' SUPRIMIDO. Isso e' o mesmo pedido repetido, e entrega unica
    e' o comportamento pedido — mas, ao contrario de antes, o turno suprimido NAO e' mais
    reportado como enviado (`_entrega_de` + os dois pontos de chamada).
    """
    material = json.dumps(
        {field: state.get(field) for field in sorted(_CALLER_INPUT_FIELDS)},
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def _idempotency_key(state: LucasState, *, node: str) -> str:
    """A chave de UM envio de saida: conversa + no + turno. UMA definicao para os dois pontos de
    chamada (`respond_member`, `send_escalation_ack`), para que nao possam divergir em silencio.

    `business_key` usa a mesma fallback `state.get("business_key") or _business_key(state)` que
    `start_process` ja usa, entao a chave e' estavel ate' no caminho raro em que `receive` nao
    rodou. `node` mantem a separacao entre o lembrete informativo e o ACK de escalacao do mesmo
    beneficiario: um nunca suprime o outro."""
    business_key = state.get("business_key") or _business_key(state)
    return f"{business_key}:{node}:{_turn_fingerprint(state)}"


#: Desfechos de ENTREGA de um envio de saida (§Delta W4-HYGIENE F1b). Tres valores, porque
#: "nao levantou excecao" nao e' o mesmo fato que "o beneficiario recebeu": o guard duravel pode
#: ter SUPRIMIDO o envio, e nesse caso `send_message` devolve `{"suppressed_duplicate": True}`
#: — deliberadamente NAO uma resposta fabricada da Cloud API.
ENTREGA_ENVIADA: Final[str] = "enviada"
ENTREGA_SUPRIMIDA_DUPLICATA: Final[str] = "suprimida_duplicata"
ENTREGA_NAO_ENVIADA: Final[str] = "nao_enviada"

#: Desfecho do turno quando o envio informativo foi SUPRIMIDO como duplicata. Token proprio,
#: nunca `resposta_informativa_enviada`/`lembrete_enviado`: afirmar "enviada" sobre um envio que
#: nao aconteceu e' a especie FAB. Declarado tambem em
#: `runtime/turn_telemetry.py::_DESFECHO_VOCAB["lucas"]` (duplicado como LITERAL, nunca
#: importado, para nao criar um import de `turn_telemetry` de volta para os grafos); o teste
#: `test_lucas.py::test_o_desfecho_de_envio_suprimido_esta_no_vocabulario_de_telemetria` e' o que
#: impede as duas copias de divergirem em silencio — sem ele o label iria para `outro`.
DESFECHO_ENVIO_SUPRIMIDO_DUPLICATA: Final[str] = "envio_suprimido_duplicata"


def _entrega_de(resultado: Any) -> str:
    """O desfecho de entrega LIDO DO RETORNO do sender, nunca da ausencia de excecao.

    Um chamador que tratasse "nao levantou" como entrega chamaria de ENVIADA uma mensagem que o
    beneficiario nunca recebeu (o defeito da especie FAB). O unico fato que o retorno afirma e' o
    que este helper le."""
    if isinstance(resultado, Mapping) and bool(resultado.get("suppressed_duplicate")):
        return ENTREGA_SUPRIMIDA_DUPLICATA
    return ENTREGA_ENVIADA


def _is_escalation_intent(state: LucasState) -> bool:
    """True when the case is, by nature, a J3 escalation path.

    `cancelamento` intencao ALWAYS escalates — never an auto-response. A beneficiary contesting a
    charge or a signaled cancellation request also always escalates, regardless of `intencao`.

    `inadimplencia` NAO esta' mais aqui (decisao do dono de 08/10/2026, DL-0082). Ela e' a LEITURA
    da pergunta ("minha mensalidade esta em aberto?", "estou devendo?"), nao um fato: no teste real
    de 08/10/2026 a fonte AMH disse "conciliado, zero ciclos" e mesmo assim o Lucas pulou a DMN,
    gravou `inadimplencia_detectada`, abriu SP-OP-ESCALATION-001 e disse ao beneficiario que havia
    "inadimplencia detectada". Agora a intencao `inadimplencia` passa pela
    `lucas_billing_admissibility` com os fatos de conciliacao, como a confirmacao de pagamento: a
    DMN decide (conciliado -> RESPONDER; nao conciliado por >= 1 ciclo -> ESCALAR_HUMANO com
    `categoria=inadimplencia`; sem fato -> catch-all, humano). A pergunta continua sendo dica de
    jornada (vai para o log e para a DMN de roteamento da escalacao), nunca veredito."""
    return (
        state.get("intencao") == "cancelamento"
        or bool(state.get("contesta_cobranca", False))
        or bool(state.get("pedido_cancelamento", False))
    )


def _ciclos_sem_conciliacao(state: LucasState) -> int | None:
    """`ciclos_sem_conciliacao` como inteiro, ou `None` quando o fato NAO e' um inteiro.

    Ausente (ou vazio) vale 0, como sempre valeu. O que mudou em 01/10/2026 (bateria do Lucas, caso
    `L24`): um valor que nao e' numero ("dois") fazia `int()` levantar dentro de `assess` e o turno
    MORRIA sem escalar para ninguem — um caso perdido em silencio. Agora o chamador trata `None`
    como ambiguidade e encaminha ao humano (fail-safe fechado, mesmo principio do resto do no)."""
    bruto: object = state.get("ciclos_sem_conciliacao", 0)
    if bruto is None or bruto == "":
        return 0
    if isinstance(bruto, bool):
        return None
    if isinstance(bruto, int):
        return bruto
    if isinstance(bruto, str | float):
        try:
            return int(bruto)
        except (TypeError, ValueError):
            return None
    return None


def _escalation_motivo(state: LucasState) -> MotivoHumano:
    """Reason for the J3 handoff. Never an adverse outcome — only a reason for a human to look."""
    if state.get("intencao") == "cancelamento" or state.get("pedido_cancelamento"):
        return "pedido_cancelamento"
    if state.get("contesta_cobranca"):
        return "contestacao_cobranca"
    # Inalcancavel hoje (`_is_escalation_intent` so' admite os dois casos acima). Se um dia for
    # alcancado, o motivo e' `ambiguidade`: `inadimplencia_detectada` so' nasce da regra de atraso
    # da DMN (`categoria=inadimplencia`), nunca da intencao (DL-0082).
    return "ambiguidade"


def _motivo_categoria(motivo: MotivoHumano) -> MotivoCategoria:
    """Maps the internal motivo to the SP-OP-ESCALATION-001 contract's `motivo_categoria`. Lucas
    NEVER escalates by a clinical category — that is not his domain (L0 hard)."""
    if motivo in {"falha_tecnica", "dmn_indisponivel"}:
        return "falha_tecnica"
    return "cobranca"


def _severidade_humano(motivo: MotivoHumano) -> str | None:
    """`severidade` do escalonamento do Lucas: AUSENTE (`None`) nos dois motivos que ele emite.

    `severidade` e' uma escala CLINICA (`grave`/`moderada`/`leve`) e nenhum caminho do Lucas
    apura uma: cobranca nunca e' clinica, e na falha tecnica nao houve extracao de onde derivar.
    Ate a onda (b) o Lucas gravava `moderada` em toda passagem de negocio e `leve` na falha — dois
    rotulos com cara de decididos que ninguem decidiu. O contrato SP-OP-ESCALATION-001 declara a
    ausencia para `falha_tecnica`, `cobranca` e `alerta_sla`, e o worker
    (`escalation.py::_MOTIVO_SEM_SEVERIDADE`) aceita `null` SO' nesses tres. Roteamento intocado:
    a `r6` (falha) e a `r7` (catch-all, onde cai `cobranca`) casam `severidade` no coringa `-`.
    O parametro e o tipo `str | None` (o da variavel de contrato) ficam para a assinatura continuar
    dizendo DE QUE motivo isto e' derivado e o que a fronteira aceita."""
    del motivo
    return None


#: Rotulo de exibicao do `resumo_contexto` de fallback quando nenhum `motivo_humano` foi resolvido
#: (so' o dossie vazio chega aqui). Token proprio, nunca um membro do dominio de `motivo_categoria`:
#: o texto e' lido por uma pessoa e nao pode sugerir uma classificacao que nao houve.
_MOTIVO_HUMANO_NAO_RESOLVIDO: Final[str] = "nao_resolvido"


# --- Graph -----------------------------------------------------------------------------------


#: Fatos BOOLEANOS deste fluxo, com o nome que o humano de destino reconhece (CC-11).
#:
#: Incidente de 24/08/2026 (contado por inteiro em `agents/rafael/graph.py::_FATOS_BOOLEANOS`):
#: fatos passados ao modelo como repr de dicionario deixam `False` e `None` com a mesma cara de
#: "vazio", e um fato APURADO-e-desfavoravel vira "nao ha registro". O conserto ficou num agente
#: so' ate' a auditoria da frota; este mapa e' a adocao aqui. Chave -> rotulo; a ORDEM e' a ordem
#: das linhas no prompt. So' entram fatos declarados `bool` no state — nada que seja enum/str.
_FATOS_BOOLEANOS_MENSAGEM: Final[dict[str, str]] = {
    "status_conciliado": "pagamento conciliado no CNAB",
}

#: O dossie de escalacao (J3) nao tem mais mapa de booleanos para o modelo: desde 09/10/2026 a
#: narrativa e' TEXTO FIXO (`texto_dossie`), e cada booleano vira frase so' quando e' `True`.


#: CERCA DE SAIDA (18/09/2026): o `error` do turno em que o texto redigido pelo modelo foi BARRADO
#: antes de sair. Token de CLASSE — nunca o grupo e nunca o texto recusado. O texto e' saida de
#: modelo sobre a cobranca de uma pessoa e nao tem por que entrar num campo que viaja para o
#: handoff; o grupo e o padrao ficam onde detalhe deve ficar, na linha de log e no contador.
ERRO_RESPOSTA_RECUSADA: str = "resposta recusada na saida"

#: CERCA DE SAIDA: o desfecho do mesmo turno. Precisa estar em
#: `turn_telemetry._DESFECHO_VOCAB["lucas"]` — um valor fora do vocabulario e' normalizado para
#: `"outro"`, o que apagaria exatamente a distincao que esta cerca cria. Literal duplicado la',
#: pelo mesmo motivo que `DESFECHO_ENVIO_SUPRIMIDO_DUPLICATA`: aquele modulo nao importa grafos, e
#: o teste `test_o_desfecho_de_recusa_esta_no_vocabulario_de_telemetria` e' o que impede as duas
#: copias de divergirem.
DESFECHO_RESPOSTA_RECUSADA: str = "resposta_recusada_na_saida"

#: CERCA DE SAIDA: o texto informativo quando o rascunho foi barrado. CONSTANTE, nunca um segundo
#: rascunho — o mesmo prompt com os mesmos fatos tende ao mesmo texto, e um laco de tentativas
#: transformaria uma cerca num atraso.
#:
#: ELA NAO PROMETE HUMANO, e essa ausencia e' a parte pensada: esta rota (`respond_member`) NUNCA
#: abre processo, entao "um atendente vai entrar em contato" seria falso aqui — e cairia na
#: propria cerca, que e' como se sabe que a frase abaixo esta' certa. O que ela oferece e' o canal
#: oficial, que existe e nao depende do Lucas.
#:
#: TOM HUMANIZADO (DL-0087, 10/10/2026): curta, "voce", e so' os canais permitidos pelo guia de tom
#: (`docs/guia-de-tom-dos-agentes.md`) — a v1 citava o "portal do beneficiario", canal proibido.
RESPOSTA_INFORMATIVA_RECUSADA: str = (
    "Não consegui confirmar essa informação por aqui agora. Você pode consultar pelo aplicativo "
    "Austa Clínicas, pelo portal do plano ou pela central de atendimento."
)

#: A garantia de "nenhuma decisao" na forma UNICA do guia de tom (DL-0087, regra 5).
GARANTIA_SEM_DECISAO: Final[str] = "Fique tranquilo: nenhuma decisão sobre o seu plano foi tomada."

#: CERCA DE SAIDA: o ACK de escalacao quando o rascunho foi barrado. Aqui um humano FOI mesmo
#: acionado — `send_escalation_ack` so' roda com `process_started is True` —, entao a promessa e'
#: verdadeira, e a frase nao contem nenhum dos padroes proibidos. E' saida segura por construcao,
#: nao por sorte do modelo.
#: ATENDIMENTO JA' ABERTO (02/10/2026, bateria do circuito humano): quando o start devolve uma instancia
#: que JA' existia (`already_existed` — a mesma chave `ESC-{tenant}-{conversa}` da Helena e do Lucas),
#: nenhum atendimento novo foi aberto e o ACK redigido pelo modelo ("um atendente vai entrar em contato")
#: anunciaria um encaminhamento que nao aconteceu agora. Constante, sem modelo: diz a verdade e nao
#: promete prazo. Mesmo espirito de `RESPOSTA_HANDOFF_JA_ABERTO` da Helena; nao nomeia "cobranca"
#: porque o caso aberto pode ser de saude.
ACK_ATENDIMENTO_JA_ABERTO: str = (
    f"Sua dúvida já está com alguém da nossa equipe, que vai te responder por aqui. {GARANTIA_SEM_DECISAO}"
)

ACK_ESCALACAO_RECUSADO: str = (
    f"Sua dúvida está com alguém da nossa equipe, que vai te responder por aqui. {GARANTIA_SEM_DECISAO}"
)

# --- Textos FIXOS ao beneficiario decididos pelos FATOS (DL-0082, decisao do dono 08/10/2026) -----
#
# INCIDENTE (dev, 08/10/2026 01:13 UTC): "minha mensalidade esta em aberto?" -> fonte AMH disse
# conciliado com zero ciclos -> o Lucas escalou por `inadimplencia` e o ACK REDIGIDO PELO MODELO
# (o prompt carregava `motivo_humano=inadimplencia_detectada`) disse "Recebemos a informacao sobre a
# inadimplencia detectada". Dois consertos: (1) a rota sai dos fatos (`_is_escalation_intent`), e
# (2) o que o beneficiario le' nestes dois pontos e' TEXTO FIXO montado so' com fatos, nunca
# rascunho de modelo. Os textos ainda passam por `_cercar_saida` (defesa em profundidade; os testes
# provam que nenhum deles cai na cerca).

#: ACK de escalacao, neutro. Nao diz por que escalou (o dossie diz isso ao humano), nao nomeia
#: inadimplencia e nao promete prazo. A promessa de humano e' verdadeira: `send_escalation_ack` so'
#: roda com `process_started is True`.
#: DL-0087 (10/10/2026): a redacao aprovada pelo dono, com a garantia na forma unica.
ACK_ESCALACAO: Final[str] = (
    "Vou passar sua dúvida para alguém da nossa equipe, que vai te responder por aqui. "
    f"{GARANTIA_SEM_DECISAO}"
)

#: O ACK da escalacao por ATRASO (so' a regra de atraso da DMN): diz o FATO sem afirmar
#: inadimplencia. O boleto e a data da fonte entram em volta deste miolo (`texto_ack_escalacao`).
_ACK_ESCALACAO_ATRASO: Final[str] = (
    f"Vou passar para alguém da nossa equipe, que vai te responder por aqui. {GARANTIA_SEM_DECISAO}"
)

#: Rodada 12 (14/10/2026): a fonte nao entregou fatos (indisponivel). Nao e' caso humano: a resposta honesta,
#: o que fazer agora e a oferta de uma pessoa. Nunca inventa situacao.
TEXTO_FONTE_INDISPONIVEL: Final[str] = (
    "Agora não consegui consultar a situação da sua mensalidade. Tente de novo em alguns minutos. Se "
    "preferir, use o aplicativo Austa Clínicas, o portal do plano ou a central de atendimento. Se quiser, "
    "posso te passar para alguém da nossa equipe."
)
#: Rodada 12: pergunta de cobranca que o Lucas nao responde (forma de pagamento, nota fiscal, informe de
#: rendimentos...). Diz o que ele mostra e onde buscar o resto. Sem caso: pedir uma pessoa e' do beneficiario.
TEXTO_OUTRO_ASSUNTO: Final[str] = (
    "Sobre isso eu não consigo te ajudar por aqui. Eu mostro valor, vencimento, 2ª via e situação das suas "
    "mensalidades. Para outros assuntos, use o aplicativo Austa Clínicas, o portal do plano ou a central de "
    "atendimento. Se quiser, posso te passar para alguém da nossa equipe."
)

#: CANCELAMENTO (rodada 12). O titulo e' de terceiro: quem decide e' a empresa/administradora.
TEXTO_CANCELAMENTO_EMPRESA: Final[str] = (
    "O cancelamento do plano é decidido pela empresa contratante (normalmente o RH). Se quiser, posso te "
    "passar para alguém da nossa equipe."
)
TEXTO_CANCELAMENTO_ADMINISTRADORA: Final[str] = (
    "O cancelamento do plano é decidido pela administradora do plano. Se quiser, posso te passar para "
    "alguém da nossa equipe."
)
TEXTO_CANCELAMENTO_EMPRESA_CURTO: Final[str] = "Quem decide o cancelamento é a empresa contratante, o RH."
TEXTO_CANCELAMENTO_ADMINISTRADORA_CURTO: Final[str] = (
    "Quem decide o cancelamento é a administradora do plano."
)
#: PF: a pergunta UNICA de confirmacao. Sem argumento de retencao, sem prazo. FASE 4 (decisao do dono,
#: 18/10/2026): "Sim" basta — a frase em maiusculas ("SIM, QUERO CANCELAR") saiu.
TEXTO_CONFIRMA_CANCELAMENTO: Final[str] = (
    "Você quer pedir para cancelar o seu plano? Responda Sim para confirmar."
)
TEXTO_CANCELAMENTO_DESISTIU: Final[str] = (
    "Tudo bem, não vou registrar o pedido de cancelamento. Se precisar, é só me dizer."
)
#: Depois da confirmacao e do start de SP-OP-CANCEL-001: o que acontece a seguir, em termos humanos. NUNCA
#: diz que o plano foi cancelado — a decisao e' do processo e da equipe.
TEXTO_CANCELAMENTO_ENCAMINHADO: Final[str] = (
    "Anotei o seu pedido de cancelamento e passei para a nossa equipe, que vai te responder por aqui. "
    f"{GARANTIA_SEM_DECISAO}"
)
TEXTO_CANCELAMENTO_JA_REGISTRADO: Final[str] = (
    "O seu pedido de cancelamento já está com a nossa equipe, que vai te responder por aqui. "
    f"{GARANTIA_SEM_DECISAO}"
)

#: Prefixo do ACK quando a fonte NAO respondeu (`Indisponivel` -> fatos de conciliacao ausentes).
#: Diz o que aconteceu — nao deu para consultar — e nao inventa situacao nenhuma.
_PREFIXO_FONTE_INDISPONIVEL: Final[str] = "Agora não consegui consultar a situação da sua mensalidade."

#: Referencia de origem que a fonte AMH escreve (`fonte_cobranca_amh.py`: `amh-billing:{AAAA-MM-DD}`).
#: So' esta forma vira data no texto; a da fonte simulada (`cnab-sim-...`) nao tem data e e' omitida.
_CNAB_REF_DATADA: Final[re.Pattern[str]] = re.compile(r"^amh-billing:(\d{4})-(\d{2})-(\d{2})$")

#: Rotulo MASCARADO do boleto que a fonte AMH entrega (`****1234`). So' ele vai ao texto: o rotulo
#: sintetico da simulada (`SIM-...`) e qualquer coisa fora desta forma sao omitidos.
#:
#: APRESENTACAO (decisao do dono de 09/10/2026, teste real): o WhatsApp le' asterisco como negrito e
#: comia parte do rotulo (`****0037` chegava `**0037`). Os fatos continuam guardando o rotulo mascarado;
#: o texto ao beneficiario diz "boleto final 0037" — so' os digitos do rotulo, nunca asterisco.
_BOLETO_MASCARADO: Final[re.Pattern[str]] = re.compile(r"^\*{2,}(\d{2,6})$")
#: Rotulo mascarado escrito pelo MODELO (rota de rascunho), com ou sem o "final" na frente: vira a forma
#: de apresentacao antes da cerca, que confere os digitos com os fatos.
_BOLETO_MASCARADO_NO_RASCUNHO: Final[re.Pattern[str]] = re.compile(
    r"(?:\bfinal\s+)?\*{2,}\s?(\d{2,6})(?!\d)", re.IGNORECASE
)


def _data_dos_fatos(state: LucasState) -> str | None:
    """`DD/MM/AAAA` da data da fonte (`cnab_ref`), ou `None` quando nao ha' data valida."""
    achado = _CNAB_REF_DATADA.match(str(state.get("cnab_ref") or ""))
    if achado is None:
        return None
    ano, mes, dia = (int(parte) for parte in achado.groups())
    try:
        data = date(ano, mes, dia)
    except ValueError:
        return None
    return data.strftime("%d/%m/%Y")


def _rotulo_boleto(state: LucasState) -> str | None:
    rotulo = str(state.get("numero_boleto") or "")
    return rotulo if _BOLETO_MASCARADO.match(rotulo) else None


def boleto_no_texto(rotulo: object) -> str | None:
    """`"****0037"` -> `"boleto final 0037"`; `None` para o que nao e' rotulo mascarado (o `SIM-...` da
    simulada, vazio, lixo). E' a UNICA forma em que um boleto chega ao beneficiario."""
    achado = _BOLETO_MASCARADO.match(rotulo) if isinstance(rotulo, str) else None
    return f"boleto final {achado.group(1)}" if achado else None


def _boleto_do_rascunho_sem_asterisco(texto: str) -> str:
    """O rotulo mascarado que o MODELO escreveu (`**0037`, `****0037`) na forma de apresentacao."""
    return _BOLETO_MASCARADO_NO_RASCUNHO.sub(lambda achado: f"final {achado.group(1)}", texto)


def _dia_mes(iso: object, hoje: date) -> str | None:
    """`AAAA-MM-DD` -> `DD/MM`, com `/AAAA` so' quando o ano nao e' o de hoje (DL-0087). `None` para o
    que nao e' data valida."""
    if not isinstance(iso, str):
        return None
    try:
        data = date.fromisoformat(iso)
    except ValueError:
        return None
    return data.strftime("%d/%m") if data.year == hoje.year else data.strftime("%d/%m/%Y")


def _frase_da_fonte(state: LucasState, hoje: date) -> str | None:
    """A data da fonte, curta — "(dados de 08/10)" (DL-0087) —, para a pessoa saber de QUANDO e' a
    informacao (a fonte pode estar alguns dias atras do pagamento)."""
    data = _dia_mes(_data_iso_da_fonte(state), hoje)
    return f"(dados de {data})" if data else None


# --- FATOS DE VALOR (DL-0086, decisao do dono/DPO de 08/10/2026) ---------------------------------
#
# O Lucas responde sozinho o que os fatos do contrato `billing-status` sustentam: valor da mensalidade,
# coparticipacao, saldo/valor em aberto, historico e data de pagamento. Os fatos chegam como entrada
# do chamador (`FatosCobranca.como_entrada_lucas`); aqui eles so' sao LIDOS e conferidos contra a forma
# do contrato — o que nao tem a forma e' descartado (vira ausente), nunca corrigido. E' texto que vai
# ao modelo, e o estado pode ter sido montado por qualquer chamador de `new_lucas_state`.

#: `tipo_solicitacao` da pergunta de valor (vocabulario da DMN `lucas_billing_admissibility`).
TIPO_CONSULTA_VALORES: Final[str] = "consulta_valores"

_DECIMAL_DO_FATO: Final[re.Pattern[str]] = re.compile(r"^[0-9]+\.[0-9]{2}$")
_DATA_DO_FATO: Final[re.Pattern[str]] = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_COMPETENCIA_DO_FATO: Final[re.Pattern[str]] = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
_MASCARA_DO_FATO: Final[re.Pattern[str]] = re.compile(r"^\*{4}\d{4}$")
_SITUACOES_DO_FATO: Final[frozenset[str]] = frozenset(
    {"paga", "em_aberto", "vencida", "cancelada", "sem_titulo"}
)
#: Campo da competencia -> forma aceita. A ORDEM e' a das chaves no fato que vai ao modelo.
_CAMPOS_DA_COMPETENCIA: Final[tuple[tuple[str, re.Pattern[str]], ...]] = (
    ("vencimento", _DATA_DO_FATO),
    ("valor_total", _DECIMAL_DO_FATO),
    ("valor_coparticipacao", _DECIMAL_DO_FATO),
    ("valor_saldo", _DECIMAL_DO_FATO),
    ("liquidado_em", _DATA_DO_FATO),
    ("boleto", _MASCARA_DO_FATO),
    ("valor_boleto_pagador", _DECIMAL_DO_FATO),
)
#: Quantas competencias a resposta de valores mostra quando a pergunta nao nomeia uma que veio nos fatos:
#: SO' a mais recente (decisao do dono de 09/10/2026 — no teste real "quanto paguei de coparticipacao este
#: mes?" despejou tres meses). Uma mensagem de WhatsApp, nao um extrato: outro mes, a pessoa pede.
COMPETENCIAS_NO_RASCUNHO: Final[int] = 1

#: Nome do mes por numero: o mes vai por extenso ao beneficiario ("julho"; "julho de 2024" em outro ano).
_NOME_DO_MES: Final[tuple[str, ...]] = (
    "janeiro",
    "fevereiro",
    "março",
    "abril",
    "maio",
    "junho",
    "julho",
    "agosto",
    "setembro",
    "outubro",
    "novembro",
    "dezembro",
)

#: "Quando vence?" com fatos por competencia, mas sem uma competencia de que se possa afirmar o vencimento
#: (sem data, so' titulo cancelado/sem boleto, ou em aberto com a data ja' passada — atraso quem trata e' a
#: DMN, nunca o texto). Diz que nao achou; nao inventa data nem afirma atraso.
#: DL-0087: so' os canais permitidos (a v1 citava o "portal do beneficiario", canal proibido).
VENCIMENTO_NAO_ENCONTRADO: Final[str] = (
    "Não encontrei a data de vencimento nos dados que tenho. Você pode ver pelo aplicativo Austa "
    "Clínicas, pelo portal do plano ou pela central de atendimento."
)


def _na_forma(valor: object, forma: re.Pattern[str]) -> str | None:
    return valor if isinstance(valor, str) and forma.match(valor) else None


def mostra_boleto_da_familia(state: LucasState) -> bool:
    """O total do boleto da familia pode ser mostrado? So' para o pagador PF que e' a propria pessoa
    (`pagador_tipo=pessoa_fisica` e `beneficiario_e_pagador=True`), nunca empresa nem administradora."""
    return (
        state.get("pagador_tipo") == "pessoa_fisica"
        and state.get("beneficiario_e_pagador") is True
        and not cobranca_administradora(state)
    )


def dependente_do_titular(state: LucasState) -> bool:
    """A pessoa NAO e' o pagador PF do boleto (`beneficiario_e_pagador=False`, ex. dependente): o boleto da
    familia e' emitido em nome do titular do plano."""
    return (
        state.get("pagador_tipo") == "pessoa_fisica"
        and state.get("beneficiario_e_pagador") is False
        and not cobranca_administradora(state)
    )


def _competencias_dos_fatos(state: LucasState) -> list[dict[str, str]]:
    """As competencias do estado que tem a forma do contrato, da mais recente para a mais antiga."""
    bruto = state.get("competencias_cobranca")
    if not isinstance(bruto, list):
        return []
    saida: list[dict[str, str]] = []
    for item in bruto:
        if not isinstance(item, Mapping):
            continue
        competencia = _na_forma(item.get("competencia"), _COMPETENCIA_DO_FATO)
        situacao = item.get("situacao")
        if competencia is None or not isinstance(situacao, str) or situacao not in _SITUACOES_DO_FATO:
            continue
        limpo = {"competencia": competencia, "situacao": situacao}
        for campo, forma in _CAMPOS_DA_COMPETENCIA:
            if campo == "valor_boleto_pagador" and not mostra_boleto_da_familia(state):
                continue  # o total do boleto da familia so' chega ao texto para o pagador PF
            valor = _na_forma(item.get(campo), forma)
            if valor is not None:
                limpo[campo] = valor
        saida.append(limpo)
    return sorted(saida, key=lambda c: c["competencia"], reverse=True)


def _valor_em_aberto(state: LucasState) -> str | None:
    return _na_forma(state.get("valor_em_aberto"), _DECIMAL_DO_FATO)


def _dias_atraso_max(state: LucasState) -> int | None:
    dias = state.get("dias_atraso_max")
    return dias if isinstance(dias, int) and not isinstance(dias, bool) and dias >= 0 else None


def _vencimento_referencia(state: LucasState) -> str | None:
    return _na_forma(state.get("vencimento_referencia"), _DATA_DO_FATO)


def valores_disponiveis(state: LucasState) -> bool:
    """Ha' fato de VALOR neste turno? E' a entrada `valores_disponiveis` da DMN: sem ele a linha
    `consulta_valores` nao casa e o catch-all escala — nunca se responde valor sem dado."""
    return bool(_competencias_dos_fatos(state)) or _valor_em_aberto(state) is not None


def valores_com_atraso(state: LucasState) -> bool:
    """Os fatos de valor mostram ATRASO (alguma competencia `vencida` ou `dias_atraso_max > 0`)?

    E' a entrada `valores_com_atraso` da DMN (DL-0082 sobre DL-0086, revisao de seguranca do #709):
    "vencida ontem" ainda nao e' ciclo sem conciliacao (`ciclos=0`), e a fonte pode estar dias atras
    do pagamento. Com atraso a pergunta de valor ESCALA sem afirmar inadimplencia (`ambiguidade`) — a
    situacao `vencida` e o `dias_atraso_max` nunca chegam a um texto ao beneficiario."""
    dias = _dias_atraso_max(state)
    if dias is not None and dias > 0:
        return True
    return any(c["situacao"] == "vencida" for c in _competencias_dos_fatos(state))


def pagador_empresa(state: LucasState) -> bool:
    """A mensalidade e' paga pela EMPRESA contratante (`pagador_tipo=pessoa_juridica`)? Num contrato de
    empresa o boleto, o total e o atraso sao da empresa: o Lucas nao os mostra. PF, desconhecido ou ausente:
    `False`."""
    return state.get("pagador_tipo") == PAGADOR_EMPRESA


def cobranca_administradora(state: LucasState) -> bool:
    """Coletivo por ADESAO cobrado pela administradora (13/10/2026, bateria 4)? So' com o fato da fonte."""
    return state.get("cobranca_administradora") is True and not pagador_empresa(state)


def titulo_de_terceiro(state: LucasState) -> bool:
    """A entrada `pagador_empresa` da DMN: o titulo do Tasy NAO e' do beneficiario — da empresa contratante
    (decisao do dono, 13/10/2026) ou da administradora da adesao (bateria 4). O atraso desse titulo nunca
    escala o beneficiario, e a pergunta de cobranca tem resposta fixa."""
    return pagador_empresa(state) or cobranca_administradora(state)


def atraso_critico(state: LucasState, limiares: LimiaresDeAtraso) -> bool:
    """A entrada `atraso_critico` da DMN (rodada 12, decisao do dono de 14/10/2026): o atraso e' REAL o
    bastante para abrir caso humano? So' para pagador PF (o titulo e' do beneficiario); contrato de empresa e
    adesao nunca. `ciclos_sem_conciliacao` e as competencias `vencida` contam os MESES sem pagamento; o maior
    atraso e' `dias_atraso_max`. Apurado dos fatos, nunca pelo modelo (`politica_atraso.py`)."""
    if titulo_de_terceiro(state):
        return False
    vencidas = sum(1 for c in _competencias_dos_fatos(state) if c["situacao"] == "vencida")
    ciclos = _ciclos_sem_conciliacao(state)
    meses = max(0 if ciclos is None else ciclos, vencidas)
    return limiares.critico(meses_vencidos=meses, dias_atraso_max=_dias_atraso_max(state))


def pede_cancelamento(state: LucasState) -> bool:
    """O turno e' um pedido de CANCELAMENTO do plano (subtipo `cancelamento` ou sinal explicito)."""
    return state.get("intencao") == "cancelamento" or state.get("pedido_cancelamento") is True


def sem_dados_de_cobranca(state: LucasState) -> bool:
    """A fonte nao entregou fato nenhum (indisponivel) e a pergunta e' de valor ou de status de pagamento:
    nao ha' o que responder — a constante honesta, sem caso (rodada 12). `status_conciliado` ausente so'
    acontece com a fonte `Indisponivel`; com ele presente a DMN decide."""
    return (
        state.get("tipo_solicitacao") in (TIPO_CONSULTA_VALORES, "status_pagamento")
        and state.get("status_conciliado") is None
        and not valores_disponiveis(state)
    )


#: Coletivo por adesao (13/10/2026, bateria 4): de onde vem o boleto e o que fica com a administradora.
TEXTO_COBRANCA_ADMINISTRADORA: Final[str] = (
    "No seu plano (coletivo por adesão), o boleto é emitido pela administradora do plano: a 2ª via, o "
    "vencimento e a confirmação do pagamento ficam com ela."
)


def texto_cobranca_administradora(state: LucasState, facts: Mapping[str, Any], hoje: date) -> str:
    """Coletivo por ADESAO cobrado pela administradora: a parcela DA PESSOA (ou a coparticipacao, se foi
    a pergunta) e de onde vem o boleto. Nunca situacao, vencimento, saldo ou boleto do titulo da
    administradora — e nunca "a empresa paga"."""
    competencias: list[Mapping[str, str]] = list(facts.get("competencias") or [])
    c = competencias[0] if competencias else None
    foco = facts.get("foco")
    fato: str | None = None
    if c is not None and foco == "coparticipacao":
        fato = _frase_de_coparticipacao(c, hoje)
    elif c is not None and "valor_total" in c:
        fato = (
            f"Sua mensalidade de {_mes_por_extenso(c['competencia'], hoje)} é de {_reais(c['valor_total'])}."
        )
    valor_primeiro = state.get("tipo_solicitacao") == TIPO_CONSULTA_VALORES and foco in (
        "valor",
        "coparticipacao",
        FOCO_GERAL,
    )
    partes = (
        [fato, TEXTO_COBRANCA_ADMINISTRADORA] if valor_primeiro else [TEXTO_COBRANCA_ADMINISTRADORA, fato]
    )
    texto = [parte for parte in partes if parte]
    if fato is not None:
        fonte = _frase_da_fonte(state, hoje)
        if fonte:
            texto.append(fonte)
    return " ".join(texto)


#: A resposta FIXA a quem tem a mensalidade paga pela empresa (decisao do dono, 13/10/2026).
TEXTO_PAGADOR_EMPRESA: Final[str] = "Sua mensalidade é paga pela empresa contratante."


#: Rodada 8 (teste real do dono): o contrato de empresa responde POR ASSUNTO. "2a via", "vencimento/boleto" e
#: "pagamento/valor/ultimas N" sao tres perguntas diferentes — cada uma com o seu texto e a sua escada.
TEMA_EMPRESA_PAGAMENTO: Final[str] = "pagamento"
TEMA_EMPRESA_VENCIMENTO: Final[str] = "vencimento"
TEMA_EMPRESA_SEGUNDA_VIA: Final[str] = "segunda_via"

TEXTO_EMPRESA_VENCIMENTO: Final[str] = (
    "O boleto e o vencimento da sua mensalidade ficam com a empresa contratante (normalmente o RH). Se "
    "quiser, posso te passar para alguém da nossa equipe."
)
TEXTO_EMPRESA_SEGUNDA_VIA: Final[str] = (
    "A 2ª via do boleto é com a empresa contratante (normalmente o RH). Por aqui eu não consigo enviar o "
    "boleto. Se quiser, posso te passar para alguém da nossa equipe."
)


def tema_da_empresa(state: LucasState) -> str:
    """O assunto da pergunta num contrato de empresa: 2a via, vencimento/boleto ou pagamento (o resto)."""
    if state.get("tipo_solicitacao") in _TIPOS_DE_SEGUNDA_VIA:
        return TEMA_EMPRESA_SEGUNDA_VIA
    if state.get("tipo_solicitacao") == "vencimento" or _foco_da_pergunta(state) == "vencimento":
        return TEMA_EMPRESA_VENCIMENTO
    return TEMA_EMPRESA_PAGAMENTO


def texto_pagador_empresa(state: LucasState, facts: Mapping[str, Any], hoje: date) -> str:
    """Contrato de EMPRESA: o texto fixo e, SO' quando a pergunta e' de coparticipacao (que e' da pessoa),
    a coparticipacao da competencia nos fatos, com a data da fonte. Nunca valor, boleto, vencimento ou
    situacao da fatura da empresa."""
    competencias: list[Mapping[str, str]] = list(facts.get("competencias") or [])
    pedida = _na_forma(state.get("competencia"), _COMPETENCIA_DO_FATO)
    if facts.get("foco") == FOCO_ESCLARECIMENTO and pedida is not None:
        # Rodada 4: "de qual ano?" num contrato de empresa — o ano, e quem paga.
        return f"É {_mes_por_extenso(pedida, hoje)}. O pagamento dela é feito pela empresa contratante."
    if facts.get("foco") != "coparticipacao" or not competencias:
        tema = tema_da_empresa(state)
        if tema == TEMA_EMPRESA_SEGUNDA_VIA:
            return TEXTO_EMPRESA_SEGUNDA_VIA
        if tema == TEMA_EMPRESA_VENCIMENTO:
            return TEXTO_EMPRESA_VENCIMENTO
        # Rodada 4 (13/10/2026): a frase sozinha, repetida 9 vezes, nao respondia "foi paga ou nao?". A
        # consequencia, uma vez: quem confirma o pagamento e' a empresa, e a oferta de alguem da equipe.
        de_qual = f" de {_mes_por_extenso(pedida, hoje)}" if pedida is not None else ""
        return (
            f"{TEXTO_PAGADOR_EMPRESA} Por isso, a situação do pagamento{de_qual} fica com a empresa "
            "(normalmente o RH). Se quiser, posso te passar para alguém da nossa equipe."
        )
    # Rodada 5 (13/10/2026): a coparticipacao e' da PESSOA — so' o fato dela, sem a frase da empresa
    # repetida em toda resposta.
    partes = [_frase_de_coparticipacao(competencias[0], hoje)]
    fonte = _frase_da_fonte(state, hoje)
    if fonte:
        partes.append(fonte)
    return " ".join(partes)


#: Rodada 5 (13/10/2026): "manda o extrato detalhado" — o Lucas nao envia o extrato; diz onde ele sai.
TEXTO_EXTRATO: Final[str] = (
    "Por aqui eu não consigo enviar o extrato detalhado. Ele sai pelo aplicativo Austa Clínicas, pelo portal "
    "do plano ou pela central de atendimento. Se quiser, posso te passar para alguém da nossa equipe."
)
TEXTO_EXTRATO_CURTO: Final[str] = (
    "O extrato detalhado sai pelo aplicativo Austa Clínicas, pelo portal do plano ou pela central de "
    "atendimento."
)
#: Rodada 5: pagamento feito DIRETO ao medico/clinica. O Lucas so' ve' o que o plano cobra — nao afirma
#: regra de reembolso nem de cobertura; oferece alguem da equipe.
TEXTO_PAGAMENTO_AO_PRESTADOR: Final[str] = (
    "Aqui eu só vejo as coparticipações cobradas pelo plano. Um valor pago direto ao médico ou à clínica não "
    "aparece nesses dados. Se quiser, posso te passar para alguém da nossa equipe."
)
TEXTO_PAGAMENTO_AO_PRESTADOR_CURTO: Final[str] = (
    "O que foi pago direto ao médico ou à clínica não aparece para mim: só vejo o que o plano cobra."
)


def formas_curtas_da_empresa(state: LucasState, hoje: date) -> tuple[str, ...]:
    """As formas CURTAS do contrato de empresa, POR ASSUNTO (rodada 8), que se revezam (FASE 4,
    18/10/2026) quando a resposta anterior do Lucas ja' foi um texto da empresa — de QUALQUER assunto ou mes.
    Antes, trocar de assunto ou de mes repetia "Sua mensalidade e' paga pela empresa contratante." inteira.
    Nenhuma repete essa abertura; todas dizem o fato (a empresa contratante / o RH). Sem a oferta de uma
    pessoa: ela entra no maximo uma vez a cada duas respostas (`LucasGraph._escada_da_empresa`)."""
    tema = tema_da_empresa(state)
    if tema == TEMA_EMPRESA_SEGUNDA_VIA:
        return (
            "A 2ª via é com a empresa contratante, normalmente pelo RH. Por aqui eu não envio o boleto.",
            "Esse boleto é emitido para a empresa contratante. A 2ª via você pede ao RH.",
            "Por aqui eu não consigo enviar esse boleto. A 2ª via fica com o RH da empresa contratante.",
        )
    if tema == TEMA_EMPRESA_VENCIMENTO:
        return (
            "O vencimento e o boleto são com a empresa contratante, normalmente pelo RH.",
            "A data de vencimento quem informa é o RH da empresa contratante.",
            "Como o boleto é da empresa contratante, o vencimento fica com o RH.",
        )
    pedida = _na_forma(state.get("competencia"), _COMPETENCIA_DO_FATO)
    de_qual = f" de {_mes_por_extenso(pedida, hoje)}" if pedida is not None else ""
    return (
        f"A confirmação do pagamento{de_qual} é com a empresa contratante, normalmente pelo RH.",
        f"Para saber se a mensalidade{de_qual} foi paga, fale com o RH da empresa contratante.",
        f"O pagamento{de_qual} é feito pela empresa contratante, e eu não o vejo por aqui. O RH confirma.",
    )


def texto_pagador_empresa_curto(state: LucasState, hoje: date) -> str:
    """A forma CURTA do contrato de empresa, para quando a pessoa pergunta de novo (rodada 4), POR ASSUNTO
    (rodada 8): a curta da 2a via nao e' a do pagamento. E' a primeira de `formas_curtas_da_empresa`."""
    return formas_curtas_da_empresa(state, hoje)[0]


def _reais(decimal_texto: str) -> str:
    """`"8389.53"` -> `"R$ 8.389,53"` (o texto ja' tem a forma decimal do contrato, `0.00`)."""
    inteiro, centavos = decimal_texto.split(".")
    grupos: list[str] = []
    while len(inteiro) > 3:
        grupos.insert(0, inteiro[-3:])
        inteiro = inteiro[:-3]
    grupos.insert(0, inteiro)
    return f"R$ {'.'.join(grupos)},{centavos}"


def _mes_ano(competencia: str) -> str:
    """`2026-07` -> `07/2026`: a forma da competencia no DOSSIE (lido pelo atendente)."""
    ano, mes = competencia.split("-")
    return f"{mes}/{ano}"


def _mes_por_extenso(competencia: str, hoje: date) -> str:
    """`2026-07` -> "julho de 2026", SEMPRE com o ano. O DL-0087 omitia o ano corrente, e no teste
    real do dono (12/10/2026) a primeira pergunta depois de "voce pagou a de julho" foi "De qual
    ano?". `hoje` fica na assinatura para os chamadores (a data do pagamento ainda usa o ano corrente)."""
    del hoje
    ano, mes = competencia.split("-")
    return f"{_NOME_DO_MES[int(mes) - 1]} de {ano}"


def _mes_seguinte(competencia: str) -> str:
    ano, mes = (int(parte) for parte in competencia.split("-"))
    return f"{ano + 1:04d}-01" if mes == 12 else f"{ano:04d}-{mes + 1:02d}"


def _foco_da_pergunta(state: LucasState) -> str:
    """O foco do handoff (DL-0087), ou `geral` quando ausente/fora do dominio (entrada do chamador)."""
    foco = state.get("cobranca_foco")
    return foco if isinstance(foco, str) and foco_valido(foco) else FOCO_GERAL


def _valor_principal(c: Mapping[str, str]) -> str | None:
    """O valor que o texto mostra de UM mes (FASE 4, 18/10/2026, dados reais de dev): o do BOLETO do
    pagador PF (`valor_boleto_pagador`, que so' chega aos fatos para ele — `_competencias_dos_fatos`) e, sem
    ele, o `valor_total` de sempre. O defeito: a lista mostrava R$ 391,02 (a parte da titular) e a 2a via
    R$ 566,72 (o boleto, com o dependente) para o MESMO mes. Empresa, adesao e quem nao e' o pagador: o de
    antes."""
    return c.get("valor_boleto_pagador") or c.get("valor_total")


#: O rotulo SEM nome de um beneficiario do detalhamento (DL-0114): "você", "titular", "dependente N".
_ROTULO_DO_DETALHE: Final[re.Pattern[str]] = re.compile(r"(você|titular|dependente( [0-9]+)?)")


def _beneficiarios_que_fecham(
    grupo: Mapping[str, Any] | None, total: str | None
) -> list[dict[str, str]] | None:
    """Os beneficiarios do detalhamento do pagador quando ele FECHA com o boleto: a AMH diz `fecha=true`, o
    total do grupo e' o `total` do boleto e a soma dos totais por beneficiario confere com ele. So' CONFERE
    somas — nenhuma parcela e' calculada. `None` em qualquer duvida (o texto mostra so' o valor do boleto)."""
    if not isinstance(grupo, Mapping) or grupo.get("fecha") is not True or total is None:
        return None
    if _na_forma(grupo.get("total_mensalidade"), _DECIMAL_DO_FATO) != total:
        return None
    bruto = grupo.get("beneficiarios")
    if not isinstance(bruto, list) or not 0 < len(bruto) <= MAX_BENEFICIARIOS_NO_TEXTO:
        return None
    saida: list[dict[str, str]] = []
    for item in bruto:
        if not isinstance(item, Mapping):
            return None
        rotulo = item.get("rotulo")
        valor_total = _na_forma(item.get("valor_total"), _DECIMAL_DO_FATO)
        if not isinstance(rotulo, str) or not _ROTULO_DO_DETALHE.fullmatch(rotulo) or valor_total is None:
            return None
        limpo = {"rotulo": rotulo, "valor_total": valor_total}
        for campo in ("valor_pre_estabelecido", "valor_coparticipacao", "valor_adicionais", "valor_outros"):
            valor = _na_forma(item.get(campo), _DECIMAL_DO_FATO)
            if valor is not None:
                limpo[campo] = valor
        saida.append(limpo)
    if sum(Decimal(b["valor_total"]) for b in saida) != Decimal(total):
        return None
    return saida


def _sendo(b: Mapping[str, str]) -> str:
    """ ", sendo R$ 354,52 de mensalidade e R$ 36,50 de coparticipação" — so' quando o beneficiario tem
    coparticipacao (ou adicional) e as partes, todas nos fatos, somam o total dele. Senao, nada."""
    pre = b.get("valor_pre_estabelecido")
    if pre is None or Decimal(b.get("valor_outros") or "0") != 0:
        return ""
    partes = [(pre, "de mensalidade")]
    for campo, nome in (("valor_coparticipacao", "de coparticipação"), ("valor_adicionais", "de adicionais")):
        valor = b.get(campo)
        if valor is not None and Decimal(valor) > 0:
            partes.append((valor, nome))
    if len(partes) == 1 or sum(Decimal(v) for v, _ in partes) != Decimal(b["valor_total"]):
        return ""
    ditas = [f"{_reais(v)} {nome}" for v, nome in partes]
    return f", sendo {', '.join(ditas[:-1])} e {ditas[-1]}"


def composicao_do_mes(state: LucasState, c: Mapping[str, str]) -> str | None:
    """A composicao CURTA do boleto do pagador PF, quando o detalhamento da competencia fecha com ele: "você:
    R$ 391,02, sendo R$ 354,52 de mensalidade e R$ 36,50 de coparticipação; dependente 1: R$ 175,70". Cada
    quantia e' um campo do detalhamento (a cerca os recebe: `_fatos_da_composicao`). `None` sem boleto, sem
    detalhamento ou quando ele nao fecha — nunca se inventa composicao."""
    total = c.get("valor_boleto_pagador")
    if total is None or not mostra_boleto_da_familia(state):
        return None
    beneficiarios = _beneficiarios_que_fecham(_detalhamento_da_competencia_v4(state, c["competencia"]), total)
    if beneficiarios is None:
        return None
    return "; ".join(f"{b['rotulo']}: {_reais(b['valor_total'])}{_sendo(b)}" for b in beneficiarios)


def _fatos_da_composicao(state: LucasState) -> list[dict[str, Any]]:
    """Os grupos do detalhamento que FECHAM com o boleto do seu mes, para a cerca de saida aceitar as quantias
    da composicao (`composicao_do_mes`). Vazio fora do pagador PF."""
    if not mostra_boleto_da_familia(state):
        return []
    saida: list[dict[str, Any]] = []
    meses: list[Mapping[str, str]] = list(_competencias_dos_fatos(state))
    segunda_via = _boleto_da_segunda_via_v4(state)
    if segunda_via is not None:
        meses.append(segunda_via["fato"])  # o valor do titulo da 2a via (v4) e' o boleto daquele mes
    for c in meses:
        grupo = _detalhamento_da_competencia_v4(state, c["competencia"])
        beneficiarios = _beneficiarios_que_fecham(grupo, c.get("valor_boleto_pagador"))
        if beneficiarios is not None:
            saida.append({"competencia": c["competencia"], "beneficiarios": beneficiarios})
    return saida


def _valor_com_composicao(c: Mapping[str, str], composicao: str | None) -> str | None:
    """ "R$ 566,72 (você: ...; dependente 1: R$ 175,70)" ou so' "R$ 566,72"; `None` sem valor."""
    valor = _valor_principal(c)
    if valor is None:
        return None
    return f"{_reais(valor)} ({composicao})" if composicao and "valor_boleto_pagador" in c else _reais(valor)


def _frase_de_valor_e_situacao(c: Mapping[str, str], hoje: date) -> str:
    """`valor`/`geral`: o valor da mensalidade e a situacao numa frase so'. Nunca cita `vencida` (com
    atraso a DMN ja' escalou) e nunca calcula nada: cada numero e data e' o campo do fato, copiado."""
    mes = _mes_por_extenso(c["competencia"], hoje)
    valor = _reais(principal) if (principal := _valor_principal(c)) else None
    situacao = c["situacao"]
    if situacao == "paga":
        pagou = _dia_mes(c.get("liquidado_em"), hoje)
        quando = f" (você pagou em {pagou})" if pagou else ""
        if valor:
            return f"Sua mensalidade de {mes} foi de {valor} e já está paga{quando}."
        return f"Sua mensalidade de {mes} já está paga{quando}."
    if situacao == "em_aberto":
        vence = _dia_mes(c.get("vencimento"), hoje)
        com_vencimento = f", com vencimento em {vence}" if vence else ""
        if valor:
            return f"Sua mensalidade de {mes} é de {valor}{com_vencimento}."
        return f"Sua mensalidade de {mes} ainda está em aberto{com_vencimento}."
    if situacao == "cancelada":
        return f"A mensalidade de {mes} consta com o título cancelado."
    if situacao == "sem_titulo":
        return f"A mensalidade de {mes} ainda não tem boleto emitido."
    if situacao == "vencida":
        # Rodada 12 (14/10/2026): o fato primeiro, sempre. Sem pagamento confirmado — nunca "inadimplente".
        return f"A mensalidade de {mes}{_entre_parenteses(c, hoje)} ainda está sem pagamento confirmado."
    return f"Não consegui confirmar a situação da mensalidade de {mes} por aqui."


#: Wave 2 (14/10/2026): quem nao e' o pagador (dependente) ouve de quem e' o boleto — sem valor nenhum.
TEXTO_BOLETO_DO_TITULAR: Final[str] = (
    "O boleto da sua família é emitido em nome do titular do plano. A 2ª via e o vencimento são com ele."
)


# --- LUCAS V4 (DL-0114, chave `MAEZO_LUCAS_V4`, decisao do dono de 15-16/10/2026) ------------------------
#
# So' o PAGADOR (o responsavel financeiro do contrato) pede valores, vencimento, situacao e 2a via; nunca se
# mostra nome de outro beneficiario nem do pagador (so' a relacao: "voce", "titular", "dependente N").
# Textos RASCUNHO aprovados pelo dono para dev; revisao juridica e do DPO antes de producao.

#: PF que NAO e' o pagador (`beneficiario_e_pagador=False`): quem responde pelo contrato, sem nome.
#: FASE 4 (18/10/2026): mais natural, e so' na PRIMEIRA resposta; nas seguintes, as variantes curtas abaixo.
TEXTO_NAO_PAGADOR_V4: Final[str] = (
    "O responsável financeiro deste contrato é o titular. Só ele pode pedir essa informação por aqui."
)
TEXTO_NAO_PAGADOR_V4_CURTO: Final[str] = (
    "Essa informação também fica com o titular, que é o responsável financeiro."
)
#: FASE 4: o nao pagador ouvia o MESMO texto 4 vezes seguidas (a escada so' valia para a mesma pergunta).
#: Depois de um texto de nao pagador (qualquer pergunta), as curtas se revezam — nunca a mesma duas vezes.
TEXTOS_NAO_PAGADOR_V4_CURTOS: Final[tuple[str, ...]] = (
    TEXTO_NAO_PAGADOR_V4_CURTO,
    "Só o titular, como responsável financeiro, consegue pedir valores, vencimento e 2ª via.",
    "Como o responsável financeiro é o titular, é ele quem pode pedir isso.",
)
#: FALHA FECHADA (achado do aceite de 16/10/2026): o Lucas v4 nao consegue afirmar que a pessoa E o pagador (o
#: sinal `beneficiario_e_pagador` nao veio, ou veio sem `pagador_tipo` conhecido). Sem o sinal ninguem recebe
#: valor, vencimento, situacao nem 2a via: so' os canais. Nao afirma quem e' o responsavel (nao sabemos).
TEXTO_PAGADOR_NAO_CONFIRMADO_V4: Final[str] = (
    "Não consegui confirmar se você é o responsável financeiro do contrato, então não mostro valores por "
    "aqui. Você consegue essas informações pelo aplicativo Austa Clínicas, pelo portal do plano ou pela "
    "central de atendimento."
)
TEXTO_PAGADOR_NAO_CONFIRMADO_V4_CURTO: Final[str] = (
    "Sem confirmar o responsável financeiro, não mostro valores. Use o aplicativo, o portal ou a central."
)
#: Pagador sem codigo de barras para entregar (sem bloqueto, formato nao reconhecido, valor diverge...).
TEXTO_SEM_CODIGO_V4: Final[str] = (
    "Não encontrei o código de barras deste boleto. Você consegue a 2ª via pelo aplicativo Austa Clínicas, "
    "pelo portal do plano ou pela central."
)
TEXTO_PDF_V4: Final[str] = "Para baixar o boleto em PDF, use o aplicativo Austa Clínicas."
TEXTO_OUTROS_BOLETOS_V4: Final[str] = "Há outros boletos em aberto. Se quiser, me diga o mês."
TEXTO_CODIGO_V4_ROTULO: Final[str] = "Código de barras (copie e cole no aplicativo do seu banco):"
TEXTO_TOTAL_SOMA_V4: Final[str] = "Esse total soma todos os beneficiários do contrato."
TEXTO_DETALHE_V4_TITULO: Final[str] = "Detalhe por beneficiário:"
TEXTO_DETALHE_NO_APP_V4: Final[str] = "O detalhe por beneficiário sai pelo aplicativo Austa Clínicas."


def pergunta_so_do_pagador_v4(state: LucasState) -> bool:
    """A pergunta e' de valor, vencimento, situacao do pagamento ou 2a via? So' o pagador a faz."""
    return state.get("tipo_solicitacao") in (
        TIPO_CONSULTA_VALORES,
        "vencimento",
        "status_pagamento",
        "boleto",
        "2a_via",
    ) or _foco_da_pergunta(state) in (FOCO_JA_PAGUEI, FOCO_ULTIMA, FOCO_ESCLARECIMENTO)


def pagador_nao_comprovado_v4(state: LucasState) -> bool:
    """FALHA FECHADA (DL-0114): o Lucas NAO consegue afirmar que a pessoa e' o pagador PF do contrato?

    So' o pagador pede valores, vencimento, situacao e 2a via, e so' o sinal `beneficiario_e_pagador is True`
    (billing-status 0.3.0) com `pagador_tipo=pessoa_fisica` o comprova. Sinal falso, ausente, nulo ou fora do
    tipo booleano, ou pagador desconhecido: `True` (nada de valor). Empresa e adesao NAO entram aqui: tem o
    texto proprio (RH / administradora), que continua como antes."""
    return not titulo_de_terceiro(state) and not mostra_boleto_da_familia(state)


def _abertura_do_total_v4(c: Mapping[str, str], total: str, hoje: date) -> str:
    """ "O valor total da mensalidade de <mes> e' de R$ X, com vencimento em DD/MM." (ou "foi de ... ja' esta'
    paga"): o total primeiro. So' para `paga` e `em_aberto`."""
    mes = _mes_por_extenso(c["competencia"], hoje)
    if c["situacao"] == "paga":
        pagou = _dia_mes(c.get("liquidado_em"), hoje)
        quando = f" (você pagou em {pagou})" if pagou else ""
        return f"O valor total da mensalidade de {mes} foi de {_reais(total)} e está paga{quando}."
    vence = _dia_mes(c.get("vencimento"), hoje)
    com_vencimento = f", com vencimento em {vence}" if vence else ""
    return f"O valor total da mensalidade de {mes} é de {_reais(total)}{com_vencimento}."


def _frase_de_atraso_do_pagador(c: Mapping[str, str], hoje: date) -> str | None:
    """Depois do valor: o mes VENCIDO (ou em aberto com o vencimento ja' passado) sem pagamento confirmado."""
    vencimento = c.get("vencimento")
    ja_passou = isinstance(vencimento, str) and vencimento[:10] < hoje.isoformat()
    if c["situacao"] == "vencida" or (c["situacao"] == "em_aberto" and ja_passou):
        quando = _dia_mes(vencimento, hoje)
        return f"Ela venceu{' em ' + quando if quando else ''} e ainda está sem pagamento confirmado."
    return None


def _frase_de_valor_do_pagador_v4(c: Mapping[str, str], hoje: date) -> str | None:
    """Pagador PF: o valor TOTAL da mensalidade primeiro (todos os beneficiarios do contrato) e depois a
    parte dele. `None` sem o total ou fora de paga/em aberto (as outras situacoes mantem a frase de
    sempre). FASE 4 (18/10/2026): vale tambem com a chave v4 desligada — antes, sem ela, a PARTE vinha
    primeiro ("Sua parte da mensalidade ... O boleto da familia ..."), e o valor principal de cada resposta
    passou a ser o do boleto (o mesmo da lista e da 2a via)."""
    total = c.get("valor_boleto_pagador")
    # ROTEIRO lucas_v4_pagador (18/10/2026): o mes VENCIDO tambem responde o valor primeiro (a pergunta e' de
    # valor); a situacao vem DEPOIS (`_frase_de_atraso_do_pagador`).
    if total is None or c["situacao"] not in ("paga", "em_aberto", "vencida"):
        return None
    partes = [_abertura_do_total_v4(c, total, hoje), TEXTO_TOTAL_SOMA_V4]
    parte = c.get("valor_total")
    if parte is not None and parte != total:
        verbo = "foi" if c["situacao"] == "paga" else "é"
        partes.append(f"A sua parte {verbo} de {_reais(parte)}.")
    return " ".join(partes)


def _sem_boleto_em_aberto(state: LucasState, hoje: date) -> tuple[str, dict[str, str]] | None:
    """Nenhuma competencia em aberto/vencida nos fatos e a pedida (ou a mais recente) PAGA: "Nao ha' boleto em
    aberto: a mensalidade de <mes> ja' foi paga em DD/MM." (com o fato, para a cerca). `None` caso contrario
    (o texto de "nao encontrei o codigo" de sempre)."""
    competencias = _competencias_dos_fatos(state)
    if not competencias or any(c["situacao"] in ("em_aberto", "vencida") for c in competencias):
        return None
    pedida = state.get("competencia") or None
    c = next((x for x in competencias if x["competencia"] == pedida), competencias[0])
    if c["situacao"] != "paga":
        return None
    mes = _mes_por_extenso(c["competencia"], hoje)
    pagou = _dia_mes(c.get("liquidado_em"), hoje)
    quando = f" em {pagou}" if pagou else ""
    return f"Não há boleto em aberto: a mensalidade de {mes} já foi paga{quando}.", dict(c)


def _boleto_da_segunda_via_v4(state: LucasState) -> dict[str, Any] | None:
    """O boleto da 2a via que o estado traz (um so'), na forma do contrato; `fato` e' o que a cerca confere.
    NENHUM campo e' credencial. `None` quando nao tem a forma (o grafo segue com o texto de antes)."""
    bruto = state.get("segunda_via_boletos")
    if not isinstance(bruto, list) or not bruto or not isinstance(bruto[0], Mapping):
        return None
    item = bruto[0]
    competencia = _na_forma(item.get("competencia"), _COMPETENCIA_DO_FATO)
    situacao = item.get("situacao")
    if competencia is None or situacao not in ("em_aberto", "vencida"):
        return None
    vencimento = _na_forma(item.get("vencimento"), _DATA_DO_FATO)
    valor = _na_forma(item.get("valor_titulo"), _DECIMAL_DO_FATO)
    outros = item.get("outros")
    fato: dict[str, str] = {"competencia": competencia, "situacao": str(situacao)}
    if vencimento is not None:
        fato["vencimento"] = vencimento
    if valor is not None:
        # O valor do titulo e' o total do boleto do pagador: a chave que a cerca de saida ja' reconhece.
        fato["valor_boleto_pagador"] = valor
    return {
        "fato": fato,
        "situacao": str(situacao),
        "valor": valor,
        "vencimento": vencimento,
        "competencia": competencia,
        "outros": outros if isinstance(outros, int) and not isinstance(outros, bool) and outros > 0 else 0,
    }


def texto_segunda_via_com_codigo_v4(
    boleto: Mapping[str, Any], hoje: date, composicao: str | None = None
) -> str:
    """A 2a via do pagador COM codigo: o boleto (mes, valor, vencimento), o codigo (MARCADOR) em linha
    propria e a orientacao do PDF pelo aplicativo. Nenhuma ameaca nem calculo: depois do vencimento so'
    diz que o valor a pagar pode ser diferente do valor do titulo. FASE 4: com a `composicao` (o detalhamento
    do mes fecha com o valor do titulo), ela vai logo depois do valor."""
    mes = _mes_por_extenso(str(boleto["competencia"]), hoje)
    vence = _dia_mes(boleto.get("vencimento"), hoje)
    em_aberto = boleto["situacao"] == "em_aberto"
    if boleto.get("valor") and composicao:
        quando = (f"vence em {vence}" if em_aberto else f"venceu em {vence}") if vence else ""
        situacao = "está em aberto" if em_aberto else "ainda não consta como pago"
        miolo = f"{quando} e {situacao}" if quando else situacao
        valor = _reais(str(boleto["valor"]))
        abertura = f"O boleto da mensalidade de {mes} é de {valor} ({composicao}). Ele {miolo}."
        if not em_aberto:
            abertura += " Depois do vencimento, o valor a pagar pode ser diferente do valor do boleto."
        linhas = [abertura, TEXTO_CODIGO_V4_ROTULO, MARCADOR_CODIGO, TEXTO_PDF_V4]
        if boleto.get("outros"):
            linhas.append(TEXTO_OUTROS_BOLETOS_V4)
        return "\n".join(linhas)
    detalhes: list[str] = []
    if boleto.get("valor"):
        detalhes.append(_reais(str(boleto["valor"])))
    if vence:
        detalhes.append(f"vence em {vence}" if em_aberto else f"venceu em {vence}")
    entre_parenteses = f" ({', '.join(detalhes)})" if detalhes else ""
    if em_aberto:
        abertura = f"O boleto da mensalidade de {mes}{entre_parenteses} está em aberto."
    else:
        abertura = (
            f"O boleto da mensalidade de {mes}{entre_parenteses} ainda não consta como pago. "
            "Depois do vencimento, o valor a pagar pode ser diferente do valor do boleto."
        )
    linhas = [abertura, TEXTO_CODIGO_V4_ROTULO, MARCADOR_CODIGO, TEXTO_PDF_V4]
    if boleto.get("outros"):
        linhas.append(TEXTO_OUTROS_BOLETOS_V4)
    return "\n".join(linhas)


def _linhas_do_detalhamento_v4(grupo: Mapping[str, Any]) -> list[str] | None:
    """Uma linha por beneficiario, SEM nome: "Titular: pré-estabelecido R$ X + coparticipação R$ Y
    (+ outros R$ Z) = R$ T". `None` quando o detalhamento nao fecha (a AMH diz `fecha=false`), falta valor ou
    ha' beneficiarios demais: nunca se apresenta uma soma por pessoa que nao bate."""
    beneficiarios = grupo.get("beneficiarios")
    if (
        grupo.get("fecha") is not True
        or not isinstance(beneficiarios, list)
        or not 0 < len(beneficiarios) <= MAX_BENEFICIARIOS_NO_TEXTO
    ):
        return None
    linhas: list[str] = []
    for item in beneficiarios:
        if not isinstance(item, Mapping):
            return None
        rotulo = item.get("rotulo")
        pre = _na_forma(item.get("valor_pre_estabelecido"), _DECIMAL_DO_FATO)
        copart = _na_forma(item.get("valor_coparticipacao"), _DECIMAL_DO_FATO)
        total = _na_forma(item.get("valor_total"), _DECIMAL_DO_FATO)
        adicional = _na_forma(item.get("valor_adicionais"), _DECIMAL_DO_FATO)
        outros = item.get("valor_outros")
        if (
            not isinstance(rotulo, str)
            or not re.fullmatch(r"(você|titular|dependente( [0-9]+)?)", rotulo)
            or pre is None
            or copart is None
            or total is None
            or outros not in (None, "0.00")
        ):
            return None
        extra = f" (+ outros {_reais(adicional)})" if adicional is not None and adicional != "0.00" else ""
        linhas.append(
            f"• {rotulo.capitalize()}: pré-estabelecido {_reais(pre)} + coparticipação {_reais(copart)}"
            f"{extra} = {_reais(total)}"
        )
    return linhas


def _detalhamento_da_competencia_v4(state: LucasState, competencia: str) -> dict[str, Any] | None:
    """O grupo do pagador da competencia (so' com o contrato billing-status 0.4.0 ligado e fato valido)."""
    bruto = state.get("detalhamento_pagador")
    if not isinstance(bruto, list):
        return None
    for item in bruto:
        if isinstance(item, Mapping) and item.get("competencia") == competencia:
            return dict(item)
    return None


def _texto_do_detalhamento_v4(
    c: Mapping[str, str], grupo: Mapping[str, Any] | None, hoje: date
) -> str | None:
    """O total primeiro e, em seguida, o detalhe por beneficiario (sem nome). `None` sem grupo valido ou fora
    de paga/em aberto. Detalhamento que nao fecha: so' o total e a orientacao do aplicativo."""
    if grupo is None or c["situacao"] not in ("paga", "em_aberto", "vencida"):
        return None
    total = _na_forma(grupo.get("total_mensalidade"), _DECIMAL_DO_FATO)
    boleto = c.get("valor_boleto_pagador")
    if total is None or (boleto is not None and boleto != total):
        # FASE 4: um total do detalhamento diferente do boleto do mes nunca vira o valor principal (a lista
        # e a 2a via mostram o boleto) — a frase do boleto, sem detalhe.
        return None
    primeira = f"{_abertura_do_total_v4(c, total, hoje)} {TEXTO_TOTAL_SOMA_V4}"
    # So' com a soma conferida (a AMH diz que fecha E os totais por beneficiario somam o total).
    linhas = _linhas_do_detalhamento_v4(grupo) if _beneficiarios_que_fecham(grupo, total) else None
    if linhas is None:
        return f"{primeira} {TEXTO_DETALHE_NO_APP_V4}"
    return "\n".join([primeira, TEXTO_DETALHE_V4_TITULO, *linhas])


def _resumo_valor_e_vencimento(c: Mapping[str, str], hoje: date) -> str | None:
    """ "R$ 552,54, vencimento em 05/10" — o que o fato tem do valor e do vencimento de um mes."""
    partes: list[str] = []
    valor = _valor_principal(c)
    if valor is not None:
        partes.append(_reais(valor))
    vence = _dia_mes(c.get("vencimento"), hoje)
    if vence:
        partes.append(f"vencimento em {vence}")
    return ", ".join(partes) or None


def _entre_parenteses(c: Mapping[str, str], hoje: date) -> str:
    resumo = _resumo_valor_e_vencimento(c, hoje)
    return f" ({resumo})" if resumo else ""


#: A oferta que acompanha a resposta de um mes sem pagamento confirmado quando NAO abre caso (decisao do
#: dono, 14/10/2026): um mes de atraso e' resposta com fatos, nunca caso automatico.
OFERTA_PESSOA: Final[str] = "Se quiser, posso te passar para alguém da nossa equipe."


_FECHO_DA_FONTE: Final[re.Pattern[str]] = re.compile(r"\s*\(dados de [^)]+\)$")


def com_oferta_de_pessoa(texto: str) -> str:
    """Acrescenta a oferta de uma pessoa ANTES do fecho "(dados de 08/10)", que fica por ultimo."""
    separador = "\n" if "•" in texto else " "  # a lista termina a oferta em linha propria
    achado = _FECHO_DA_FONTE.search(texto)
    if achado is None:
        return f"{texto}{separador}{OFERTA_PESSOA}"
    return f"{texto[: achado.start()]}{separador}{OFERTA_PESSOA}{achado.group(0)}"


def _linha_tambem_sem_pagamento(itens: object, hoje: date) -> str | None:
    """ "Também consta sem pagamento confirmado: agosto de 2026 (R$ 552,54, vencimento em 15/08)." — os
    outros meses sem pagamento, ate' dois, quando a pergunta era de um mes so'."""
    if not isinstance(itens, list):
        return None
    meses: list[str] = []
    for c in itens[:2]:
        if isinstance(c, Mapping) and isinstance(c.get("competencia"), str):
            meses.append(f"{_mes_por_extenso(c['competencia'], hoje)}{_entre_parenteses(c, hoje)}")
    return f"Também consta sem pagamento confirmado: {'; '.join(meses)}." if meses else None


def _frase_de_coparticipacao(c: Mapping[str, str], hoje: date) -> str:
    mes = _mes_por_extenso(c["competencia"], hoje)
    copart = c.get("valor_coparticipacao")
    if copart is None:
        return f"Não encontrei a coparticipação de {mes} nos dados que tenho."
    if Decimal(copart) > 0:
        return f"Em {mes} sua coparticipação foi de {_reais(copart)}."
    return f"Em {mes} você não teve coparticipação: {_reais(copart)}."


def _juntar_meses(meses: list[str], hoje: date) -> str:
    """["2026-09", "2026-10"] -> "setembro de 2026 e outubro de 2026" (em ordem de calendario)."""
    nomes = [_mes_por_extenso(m, hoje) for m in sorted(meses)]
    return nomes[0] if len(nomes) == 1 else f"{', '.join(nomes[:-1])} e {nomes[-1]}"


def _frase_de_saldo_total(valor_em_aberto: str, meses: object = None, hoje: date | None = None) -> str:
    """O TOTAL em aberto da consulta (a soma que a AMH fez) e, quando os fatos por competencia dizem, os
    meses que o compoem (simulador, 13/10/2026: "o TOTAL em aberto, tudo junto?" alternava entre o
    total e o valor de um mes so')."""
    if Decimal(valor_em_aberto) > 0:
        validos = [m for m in meses if isinstance(m, str)] if isinstance(meses, list) else []
        if validos and hoje is not None:
            return f"Seu total em aberto hoje é {_reais(valor_em_aberto)}: {_juntar_meses(validos, hoje)}."
        return f"Seu total em aberto hoje é {_reais(valor_em_aberto)}."
    return f"Você não tem nada em aberto: {_reais(valor_em_aberto)}."


def meses_em_aberto(competencias: list[dict[str, str]]) -> list[str]:
    """As competencias que compoem o total em aberto: em aberto (ou vencida) com saldo maior que zero."""
    return sorted(
        {
            c["competencia"]
            for c in competencias
            if c["situacao"] in ("em_aberto", "vencida") and Decimal(c.get("valor_saldo") or "0") > 0
        }
    )


def _frase_de_saldo(
    c: Mapping[str, str] | None,
    valor_em_aberto: object,
    hoje: date,
    *,
    e_a_pedida: bool,
    meses: object = None,
) -> str:
    """`saldo`: o saldo da competencia PEDIDA quando ela veio; senao o total em aberto da consulta;
    senao o saldo da competencia mais recente; senao, sem fato de saldo, valor e situacao."""
    if c is not None and e_a_pedida and "valor_saldo" in c:
        mes = _mes_por_extenso(c["competencia"], hoje)
        if Decimal(c["valor_saldo"]) > 0:
            return f"Da mensalidade de {mes}, ainda falta pagar {_reais(c['valor_saldo'])}."
        return f"A mensalidade de {mes} não tem saldo em aberto: {_reais(c['valor_saldo'])}."
    if isinstance(valor_em_aberto, str):
        return _frase_de_saldo_total(valor_em_aberto, meses, hoje)
    if c is not None and "valor_saldo" in c:
        return _frase_de_saldo(c, None, hoje, e_a_pedida=True)
    return _frase_de_valor_e_situacao(c, hoje) if c is not None else RESPOSTA_INFORMATIVA_RECUSADA


def _frase_de_pagamento(c: Mapping[str, str], hoje: date) -> str:
    mes = _mes_por_extenso(c["competencia"], hoje)
    if c["situacao"] == "paga":
        pagou = _dia_mes(c.get("liquidado_em"), hoje)
        if pagou:
            return f"Você pagou a mensalidade de {mes} em {pagou}."
        return f"A mensalidade de {mes} já está paga."
    if c["situacao"] == "em_aberto":
        vence = _dia_mes(c.get("vencimento"), hoje)
        com_vencimento = f", com vencimento em {vence}" if vence else ""
        return f"A mensalidade de {mes} ainda não foi paga{com_vencimento}."
    return _frase_de_valor_e_situacao(c, hoje)


def texto_valores(state: LucasState, facts: Mapping[str, Any], hoje: date, *, v4: bool = False) -> str:
    """DL-0086/DL-0087: a resposta a `consulta_valores` em TEXTO FIXO montado dos fatos, SO' do campo
    perguntado (`facts["foco"]`, decidido pela Helena sobre o texto da pessoa).

    Nenhum modelo redige valor: cada quantia, data e boleto citado e' o campo da competencia a que ele
    pertence, entao "o valor certo no mes errado" nao e' possivel por construcao. COM mes citado e
    presente nos fatos, so' aquele mes; senao a competencia mais recente, avisando quando a pedida nao
    veio. Fecha com a data curta da fonte, "(dados de 08/10)". Com PEDIDO DE LISTA nos fatos, a lista
    (`texto_lista`)."""
    if isinstance(facts.get("lista"), Mapping):
        return texto_lista(state, facts, hoje)
    competencias: list[Mapping[str, str]] = list(facts.get("competencias") or [])
    pedida = _na_forma(facts.get("competencia_pedida"), _COMPETENCIA_DO_FATO)
    foco = facts.get("foco") if foco_valido(facts.get("foco")) else FOCO_GERAL
    c = competencias[0] if competencias else None
    e_a_pedida = c is not None and pedida is not None and c["competencia"] == pedida
    partes: list[str] = []
    em_linhas = False
    if pedida and not e_a_pedida:
        # Rodada 3 (13/10/2026): o mes pedido DEPOIS do mais recente ("novembro ta' lancado?") ainda nao
        # foi gerado — nunca "nao encontrei" nem o mesmo mes do ano passado.
        partes.append(
            _frase_do_mes_ausente(pedida, [dict(x) for x in competencias] + _mais_recente(facts), hoje)
        )
    valor_em_aberto = facts.get("valor_em_aberto")
    if foco == "saldo" or c is None:
        partes.append(
            _frase_de_saldo(
                c, valor_em_aberto, hoje, e_a_pedida=e_a_pedida, meses=facts.get("meses_em_aberto")
            )
        )
    elif foco == "coparticipacao":
        partes.append(_frase_de_coparticipacao(c, hoje))
    elif foco == "pagamento":
        partes.append(_frase_de_pagamento(c, hoje))
    elif foco == "vencimento":
        partes.append(
            _frase_do_vencimento(c, hoje, e_a_proxima=False, composicao=composicao_do_mes(state, c))
            or _frase_de_valor_e_situacao(c, hoje)
        )
    elif mostra_boleto_da_familia(state):
        # DL-0114: o PAGADOR ouve o valor TOTAL primeiro (e, com o contrato 0.4.0, o detalhe por
        # beneficiario sem nome); so' depois a parte dele. FASE 4 (18/10/2026): com ou sem a chave v4 — o
        # valor principal e' o do boleto, o mesmo da lista e da 2a via. O detalhe so' vem nos fatos com a
        # chave (`_fatos_de_valores`).
        detalhe = _texto_do_detalhamento_v4(c, facts.get("detalhamento"), hoje) if v4 else None
        do_pagador = detalhe or _frase_de_valor_do_pagador_v4(c, hoje)
        atraso = _frase_de_atraso_do_pagador(c, hoje) if do_pagador else None
        em_linhas = detalhe is not None and "•" in detalhe
        partes.append(do_pagador or _frase_de_valor_e_situacao(c, hoje))
        if atraso:
            partes.append(atraso)
    else:
        partes.append(_frase_de_valor_e_situacao(c, hoje))
        if dependente_do_titular(state):
            partes.append(TEXTO_BOLETO_DO_TITULAR)
    tambem = _linha_tambem_sem_pagamento(facts.get("sem_pagamento"), hoje)
    if tambem:
        partes.append(tambem)
    fonte = _frase_da_fonte(state, hoje)
    if fonte:
        partes.append(fonte)
    # A lista por beneficiario termina em linha propria: a data da fonte nao gruda na ultima linha.
    return ("\n" if em_linhas else " ").join(partes)


def _mais_recente(facts: Mapping[str, Any]) -> list[dict[str, str]]:
    """A competencia mais recente de TODOS os fatos (`mais_recente_dos_fatos`), para decidir se o mes
    pedido ainda nao foi gerado mesmo quando os fatos da resposta levam so' uma competencia."""
    mais_recente = facts.get("mais_recente_dos_fatos")
    if isinstance(mais_recente, str) and _COMPETENCIA_DO_FATO.match(mais_recente):
        return [{"competencia": mais_recente, "situacao": "paga"}]
    return []


def _competencia_do_vencimento(competencias: list[dict[str, str]], pedida: object) -> dict[str, str] | None:
    """De que competencia a resposta a "quando vence?" fala. A pedida, quando veio nos fatos; senao a
    em aberto mais antiga (a proxima a vencer); senao, tudo pago, a paga mais recente. `None` quando nao
    ha' nenhuma das tres (so' cancelada/sem titulo/vencida — vencida, a DMN ja' escalou)."""
    if pedida:
        da_pedida = next((c for c in competencias if c["competencia"] == pedida), None)
        if da_pedida is not None:
            return da_pedida
    abertas = [c for c in competencias if c["situacao"] == "em_aberto"]
    if abertas:
        return min(abertas, key=lambda c: c["competencia"])
    pagas = [c for c in competencias if c["situacao"] == "paga"]
    if pagas:
        return max(pagas, key=lambda c: c["competencia"])
    # Rodada 12: so' ha' meses sem pagamento — o mais recente (o fato primeiro).
    vencidas = [c for c in competencias if c["situacao"] == "vencida"]
    return max(vencidas, key=lambda c: c["competencia"]) if vencidas else None


def _frase_do_vencimento(
    c: Mapping[str, str], hoje: date, *, e_a_proxima: bool, composicao: str | None = None
) -> str | None:
    """A frase FIXA do vencimento de UMA competencia, ou `None` quando nao da' para afirma-la sem
    inventar ou sem afirmar atraso (sem data; em aberto com a data ja' passada; situacao que nao e' nem
    paga nem em aberto). Cada data e valor e' o campo do fato, copiado; `hoje` so' escolhe vence/venceu
    e se o ano aparece. O valor e' o do boleto do pagador PF (`_valor_principal`), com a `composicao`
    (`composicao_do_mes`) quando o detalhamento fecha."""
    vencimento_iso = c.get("vencimento")
    vencimento = _dia_mes(vencimento_iso, hoje)
    if vencimento_iso is None or vencimento is None:
        return None
    ja_passou = date.fromisoformat(vencimento_iso) < hoje
    mes = _mes_por_extenso(c["competencia"], hoje)
    if c["situacao"] == "vencida" or (c["situacao"] == "em_aberto" and ja_passou):
        # Rodada 12 (14/10/2026): o fato primeiro. A data que passou e o que consta, sem a palavra "atraso".
        principal = _valor_principal(c)
        valor = f" ({_reais(principal)})" if principal else ""
        return f"A mensalidade de {mes}{valor} venceu em {vencimento} e ainda está sem pagamento confirmado."
    if c["situacao"] == "em_aberto":
        dito = _valor_com_composicao(c, composicao)
        valor = f", no valor de {dito}" if dito else ""
        return f"A mensalidade de {mes} vence em {vencimento}{valor}."
    if c["situacao"] == "paga":
        if ja_passou:
            frase = f"A de {mes} venceu em {vencimento}, e você já pagou."
        else:
            frase = f"A de {mes} vence em {vencimento}, mas você já pagou."
        if e_a_proxima:
            seguinte = _mes_por_extenso(_mes_seguinte(c["competencia"]), hoje)
            frase += f" A de {seguinte} ainda não foi gerada."
        return frase
    return None


def texto_vencimento(state: LucasState, facts: Mapping[str, Any], hoje: date) -> str:
    """A resposta a "quando vence?" em TEXTO FIXO montado dos fatos por competencia (09/10/2026; tom do
    DL-0087). Em aberto (nao vencida) -> "A mensalidade de novembro vence em 25/11, no valor de R$ X.";
    tudo pago -> "A de outubro vence em 25/10, mas voce ja' pagou. A de novembro ainda nao foi gerada."
    Fecha com a data curta da fonte."""
    competencias: list[dict[str, str]] = list(facts.get("competencias") or [])
    pedida = facts.get("competencia_pedida")
    escolhida = competencias[0] if competencias else None
    e_a_pedida = escolhida is not None and bool(pedida) and escolhida["competencia"] == pedida
    partes: list[str] = []
    if pedida and not e_a_pedida and _na_forma(pedida, _COMPETENCIA_DO_FATO):
        partes.append(_frase_do_mes_ausente(str(pedida), competencias + _mais_recente(facts), hoje))
    frase = (
        _frase_do_vencimento(
            escolhida, hoje, e_a_proxima=not e_a_pedida, composicao=composicao_do_mes(state, escolhida)
        )
        if escolhida
        else None
    )
    if frase is None:
        # A constante ja' traz os canais; a data da fonte so' acompanha um fato afirmado.
        partes.append(VENCIMENTO_NAO_ENCONTRADO)
        return " ".join(partes)
    partes.append(frase)
    tambem = _linha_tambem_sem_pagamento(facts.get("sem_pagamento"), hoje)
    if tambem:
        partes.append(tambem)
    fonte = _frase_da_fonte(state, hoje)
    if fonte:
        partes.append(fonte)
    return " ".join(partes)


# --- LISTA de varios meses (teste real do dono, 12/10/2026) ----------------------------------------
#
# "Quais foram as ultimas tres mensalidades pagas por mim?" recebeu so' "outubro de 2026" — e de novo na
# insistencia. Decisao do dono: havendo o dado, o Lucas LISTA (mes com ano, valor, data); faltando, diz o
# que tem ("Encontrei so' 2 ..."); e quando o assunto nao tem lista (2a via, vencimento, status), diz
# "Consigo te mostrar um mes por vez." e responde o mes de sempre. O pedido vem pronto no handoff
# (`runtime/cobranca_lista.py`); aqui ele so' SELECIONA competencias dos fatos e copia os campos delas.

#: Quando o assunto nao tem resposta em lista: o aviso antes da resposta de um mes so'.
UM_MES_POR_VEZ: Final[str] = "Consigo te mostrar um mês por vez."


def _lista_da_pergunta(state: LucasState) -> PedidoDeLista | None:
    """O pedido de lista do handoff, ou `None` (ausente ou fora da forma: entrada do chamador)."""
    try:
        return lista_do_handoff(state.get("cobranca_lista"))
    except (TypeError, ValueError):
        return None


def _situacao_que_filtra(lista: PedidoDeLista, foco: str) -> str | None:
    """A situacao pedida filtra os meses, menos na coparticipacao ("quanto paguei de coparticipacao nos
    ultimos 3 meses" pergunta da coparticipacao de cada mes, paga ou nao)."""
    return None if foco == "coparticipacao" else lista.situacao


def selecionar_competencias_da_lista(
    competencias: list[dict[str, str]], lista: PedidoDeLista, foco: str
) -> list[dict[str, str]]:
    """As competencias do pedido, da mais recente para a mais antiga: uma por mes, dentro do periodo, da
    situacao pedida, no maximo `quantidade`. So' seleciona — nenhum campo e' tocado."""
    situacao = _situacao_que_filtra(lista, foco)
    vistas: set[str] = set()
    saida: list[dict[str, str]] = []
    for c in competencias:
        mes = c["competencia"]
        if mes in vistas:
            continue
        vistas.add(mes)
        if (lista.de is not None and mes < lista.de) or (lista.ate is not None and mes > lista.ate):
            continue
        if lista.meses and mes not in lista.meses:
            continue
        if situacao is not None and c["situacao"] != situacao:
            continue
        saida.append(c)
    return saida[: lista.quantidade]


def _meses_no_intervalo(de: str, ate: str) -> int:
    ano_de, mes_de = (int(p) for p in de.split("-"))
    ano_ate, mes_ate = (int(p) for p in ate.split("-"))
    return max(0, (ano_ate - ano_de) * 12 + (mes_ate - mes_de) + 1)


def quantos_meses_a_lista_espera(
    lista: PedidoDeLista, competencias: list[dict[str, str]], foco: str, hoje: date
) -> int | None:
    """Quantos meses o pedido deveria trazer, para dizer "Encontrei so' N" quando vieram menos. `None`
    quando nao da' para saber: periodo com filtro de situacao (nao se sabe quantos meses estao pagos) e
    "todas em aberto" (o total em aberto e' o que a fonte tem). Meses do periodo depois do mais recente
    que existe (o de hoje, ou um ja' gerado a frente) nao contam."""
    situacao = _situacao_que_filtra(lista, foco)
    if lista.meses:
        return len(lista.meses)
    if not lista.tem_periodo:
        return None if situacao == "em_aberto" else lista.quantidade
    if situacao is not None:
        return None
    de = lista.de or "0000-01"
    teto = max([f"{hoje.year:04d}-{hoje.month:02d}", *(c["competencia"] for c in competencias)])
    ate = min(lista.ate or teto, teto)
    return min(lista.quantidade, _meses_no_intervalo(de, ate))


def _nome_da_lista(lista: PedidoDeLista, foco: str, quantidade: int) -> str:
    """ "mensalidades pagas", "mensalidade em aberto", "coparticipacoes" — no singular quando e' uma."""
    if foco == "coparticipacao":
        return "coparticipação" if quantidade == 1 else "coparticipações"
    nome = "mensalidade" if quantidade == 1 else "mensalidades"
    if lista.situacao == "paga":
        return f"{nome} paga" if quantidade == 1 else f"{nome} pagas"
    if lista.situacao == "em_aberto":
        return f"{nome} em aberto"
    return nome


def _periodo_da_lista(lista: PedidoDeLista, hoje: date) -> str | None:
    """ "de 2026" (ano inteiro), "de julho de 2026 a setembro de 2026", "de agosto de 2026 e setembro de
    2026" (meses nomeados, rodada 4), ou `None` sem periodo."""
    if lista.meses:
        return f"de {_juntar_meses(list(lista.meses), hoje)}"
    if lista.de is None or lista.ate is None:
        return None
    if lista.de.endswith("-01") and lista.ate.endswith("-12") and lista.de[:4] == lista.ate[:4]:
        return f"de {lista.de[:4]}"
    return f"de {_mes_por_extenso(lista.de, hoje)} a {_mes_por_extenso(lista.ate, hoje)}"


#: FASE 4 (18/10/2026): a lista do pagador PF mostra o valor do BOLETO de cada mes — dito uma vez, no fim.
TEXTO_LISTA_DO_BOLETO: Final[str] = "Os valores são os do boleto, com todos os beneficiários do contrato."


def _linha_da_lista(c: Mapping[str, str], foco: str, hoje: date, composicao: str | None = None) -> str:
    """UMA linha da lista: o mes com o ano e o campo perguntado, copiado do fato daquele mes. O valor e' o do
    boleto do pagador PF (`_valor_principal`, FASE 4) e, com `composicao`, a composicao curta dele."""
    mes = _mes_por_extenso(c["competencia"], hoje)
    if foco == "coparticipacao":
        copart = c.get("valor_coparticipacao")
        if copart is None:
            return f"• {mes}: não encontrei a coparticipação."
        if Decimal(copart) > 0:
            return f"• {mes}: {_reais(copart)}."
        return f"• {mes}: sem coparticipação."
    if foco == "saldo" and "valor_saldo" in c:
        if Decimal(c["valor_saldo"]) > 0:
            return f"• {mes}: {_reais(c['valor_saldo'])} em aberto."
        return f"• {mes}: nada em aberto."
    if foco == "vencimento":
        vence = _dia_mes(c.get("vencimento"), hoje)
        if vence:
            return f"• {mes}: vencimento em {vence}."
    dito = _valor_com_composicao(c, composicao)
    valor = f"{dito}, " if dito else ""
    situacao = c["situacao"]
    if situacao == "paga":
        pagou = _dia_mes(c.get("liquidado_em"), hoje)
        return f"• {mes}: {valor}paga em {pagou}." if pagou else f"• {mes}: {valor}paga."
    if situacao == "em_aberto":
        vencimento_iso = c.get("vencimento")
        vence = _dia_mes(vencimento_iso, hoje)
        if vencimento_iso is not None and vence and date.fromisoformat(vencimento_iso) >= hoje:
            return f"• {mes}: {valor}em aberto, vence em {vence}."
        return f"• {mes}: {valor}em aberto."
    if situacao == "cancelada":
        return f"• {mes}: título cancelado."
    if situacao == "sem_titulo":
        return f"• {mes}: boleto ainda não emitido."
    # `vencida` (rodada 4): so' na lista que acompanha a escalacao ou o caso aberto — o fato, dito como
    # "sem pagamento confirmado", nunca como atraso.
    vence = _dia_mes(c.get("vencimento"), hoje)
    data = f"vencimento em {vence}, " if vence else ""
    return f"• {mes}: {valor}{data}sem pagamento confirmado."


def texto_lista(state: LucasState, facts: Mapping[str, Any], hoje: date) -> str:
    """A resposta a um PEDIDO DE LISTA em TEXTO FIXO: um cabecalho curto, uma linha por mes (mais recente
    primeiro) e a data da fonte. Com menos meses do que o pedido, o cabecalho diz quantos achou; sem
    nenhum, diz que nao achou e responde o mes mais recente, como a pergunta de um mes."""
    lista = lista_do_handoff(facts.get("lista"))
    assert lista is not None  # `_fatos_de_valores` so' poe `lista` com um pedido valido
    foco = str(facts["foco"]) if foco_valido(facts.get("foco")) else FOCO_GERAL
    competencias: list[Mapping[str, str]] = list(facts.get("competencias") or [])
    # Contagem ausente ou fora da forma vale "nenhum mes listado" (a resposta de um mes so', honesta).
    bruto = facts.get("lista_encontradas")
    encontradas = bruto if isinstance(bruto, int) and not isinstance(bruto, bool) and bruto > 0 else 0
    esperadas = facts.get("lista_esperadas")
    periodo = _periodo_da_lista(lista, hoje)
    fonte = _frase_da_fonte(state, hoje)
    if encontradas == 0:
        onde = f" {periodo}" if periodo else ""
        partes = [f"Não encontrei {_nome_da_lista(lista, foco, 2)}{onde} nos dados que tenho."]
        c = competencias[0] if competencias else None
        if c is not None:
            if foco == "coparticipacao":
                partes.append(_frase_de_coparticipacao(c, hoje))
            elif foco == "saldo":
                partes.append(_frase_de_saldo(c, None, hoje, e_a_pedida=True))
            else:
                partes.append(_frase_de_valor_e_situacao(c, hoje))
        if fonte and c is not None:
            partes.append(fonte)
        return " ".join(partes)
    nome = _nome_da_lista(lista, foco, encontradas)
    onde = f" {periodo}" if periodo else ""
    if isinstance(esperadas, int) and encontradas < esperadas:
        cabecalho = f"Encontrei só {encontradas} {nome}{onde} nos dados que tenho:"
    elif periodo:
        cabecalho = f"Suas {nome} {periodo}:" if encontradas > 1 else f"Sua {nome} {periodo}:"
    elif esperadas is None:
        cabecalho = f"Suas {nome}:" if encontradas > 1 else f"Sua {nome}:"
    else:
        cabecalho = f"Suas últimas {encontradas} {nome}:"
    mostradas = competencias[:encontradas]
    # FASE 4: a composicao curta do boleto so' na linha do mes mais recente (a lista continua curta).
    linhas = [
        cabecalho,
        *(
            _linha_da_lista(c, foco, hoje, composicao_do_mes(state, c) if i == 0 else None)
            for i, c in enumerate(mostradas)
        ),
    ]
    if any(
        "valor_boleto_pagador" in c and f"{_reais(c['valor_boleto_pagador'])}" in linha
        for c, linha in zip(mostradas, linhas[1:], strict=True)
    ):
        linhas.append(TEXTO_LISTA_DO_BOLETO)
    if fonte:
        linhas.append(fonte)
    return "\n".join(linhas)


def competencias_da_resposta(final: Mapping[str, Any]) -> tuple[str, ...]:
    """Os meses que a resposta INFORMATIVA deste turno citou (os fatos que acompanham o texto), para o
    roteador lembrar o mes RESPONDIDO — e nao so' o pedido. Teste real do dono (12/10/2026): "de qual ano
    mesmo??" depois de uma resposta sem mes pedido reaproveitava `None` e respondia outro mes. Vazio quando
    o turno escalou, a cerca recusou o texto ou os fatos nao trazem competencia."""
    if final.get("route") != "respond_member":
        return ()
    mensagem = final.get("mensagem")
    if not isinstance(mensagem, Mapping) or mensagem.get("recusa_de_saida"):
        return ()
    fatos = mensagem.get("fatos")
    if not isinstance(fatos, Mapping):
        return ()
    if isinstance(fatos.get("lista"), Mapping) and not fatos.get("lista_encontradas"):
        return ()  # nada listado: o mes mostrado e' so' o oferecido
    competencias = fatos.get("competencias")
    if not isinstance(competencias, list):
        return ()
    meses = (c.get("competencia") for c in competencias if isinstance(c, Mapping))
    return tuple(m for m in meses if isinstance(m, str) and _COMPETENCIA_DO_FATO.match(m))


# --- 2a VIA em TEXTO FIXO (decisao do dono de 10/10/2026) ----------------------------------------
#
# Teste real do dono: "Preciso da segunda via do boleto" -> o rascunho do MODELO disse "Ola! Seu pagamento do
# boleto final 0037 foi identificado e conciliado [...] Caso precise da 2a via, voce pode obte-la pelo nosso
# portal ou aplicativo do plano." — "Ola!" no meio da conversa, conciliacao misturada com a 2a via e um canal
# que nao existe ("aplicativo do plano"). Era o ultimo caminho do Lucas redigido pelo modelo com os fatos na
# mao. Agora: os canais CONFIRMADOS (os mesmos nomes de `agents/helena/prompts.py::CANAIS_CONFIRMADOS`), a
# situacao da mensalidade de referencia quando os fatos por competencia existem e a data da fonte. Nunca
# promete enviar boleto ou linha digitavel (o Lucas nao emite nada: TASY write DROP, ADR-0013).

#: `tipo_solicitacao` que recebem a resposta de 2a via (vocabulario da DMN `lucas_billing_admissibility`).
_TIPOS_DE_SEGUNDA_VIA: Final[frozenset[str]] = frozenset({"boleto", "2a_via"})

#: Os canais da 2a via (DL-0087: a redacao aprovada pelo dono). "Portal do beneficiario" e "aplicativo do
#: plano" sao canais NAO confirmados.
SEGUNDA_VIA_CANAIS: Final[str] = (
    "Você consegue a 2ª via pelo aplicativo Austa Clínicas, pelo portal do plano ou pela central de "
    "atendimento."
)


def _competencia_da_segunda_via(competencias: list[dict[str, str]], pedida: object) -> dict[str, str] | None:
    """A mensalidade de referencia da 2a via: a pedida, quando veio nos fatos; senao a mais recente."""
    if pedida:
        da_pedida = next((c for c in competencias if c["competencia"] == pedida), None)
        if da_pedida is not None:
            return da_pedida
    # Rodada 12: a 2a via e' para PAGAR — a mais recente sem pagamento (em aberto ou vencida), se houver.
    a_pagar = [c for c in competencias if c["situacao"] in ("em_aberto", "vencida")]
    if a_pagar:
        return a_pagar[0]
    return competencias[0] if competencias else None


def _frase_da_segunda_via(c: Mapping[str, str], hoje: date) -> str | None:
    """A situacao FIXA da mensalidade de referencia, ou `None` quando nao da' para afirma-la sem afirmar
    atraso (em aberto com a data ja' passada, vencida) ou sem inventar (sem data; cancelada; sem titulo).
    O boleto so' aparece quando e' util: no titulo em aberto, que e' o que a pessoa vai pagar."""
    mes = _mes_por_extenso(c["competencia"], hoje)
    if c["situacao"] == "paga":
        return f"A mensalidade de {mes} já está paga."
    if c["situacao"] in ("em_aberto", "vencida"):
        vencimento_iso = c.get("vencimento")
        vencimento = _dia_mes(vencimento_iso, hoje)
        if vencimento_iso is None or vencimento is None:
            return None
        boleto = boleto_no_texto(c.get("boleto"))
        sujeito = f"A mensalidade de {mes} ({boleto})" if boleto else f"A mensalidade de {mes}"
        if c["situacao"] == "vencida" or date.fromisoformat(vencimento_iso) < hoje:
            # Rodada 12: venceu — o fato, e a 2a via atualizada e' a dos canais acima.
            return f"{sujeito} venceu em {vencimento} e ainda está sem pagamento confirmado."
        return f"{sujeito} vence em {vencimento}."
    return None


def texto_segunda_via(state: LucasState, facts: Mapping[str, Any], hoje: date) -> str:
    """A resposta a boleto/2a via em TEXTO FIXO: os canais confirmados e, quando os fatos por competencia
    sustentam, uma linha com a situacao da mensalidade de referencia (paga, ou em aberto e nao vencida) e
    a data da fonte. Atraso nos fatos nao vira texto (e' da DMN): sai so' a linha dos canais."""
    partes = [SEGUNDA_VIA_CANAIS]
    competencias: list[dict[str, str]] = list(facts.get("competencias") or [])
    if competencias:
        frase = _frase_da_segunda_via(competencias[0], hoje)
        if frase:
            partes.append(frase)
            fonte = _frase_da_fonte(state, hoje)
            if fonte:
                partes.append(fonte)
    return " ".join(partes)


def _data_iso_da_fonte(state: LucasState) -> str | None:
    """`AAAA-MM-DD` da fonte (`cnab_ref`), para a cerca aceitar a data da frase de fechamento."""
    achado = _CNAB_REF_DATADA.match(str(state.get("cnab_ref") or ""))
    return "-".join(achado.groups()) if achado else None


def _mensalidade_em_dia(state: LucasState) -> bool:
    """A DMN mandou RESPONDER a uma pergunta de status de pagamento: a resposta e' o texto FIXO dos fatos
    (`texto_mensalidade_em_dia`) — "esta' tudo em dia" so' quando nenhum mes esta' em aberto e, com atraso nao
    critico (rodada 12, 14/10/2026), os fatos de cada mes e a oferta de uma pessoa. O atraso critico ja'
    escalou na DMN, antes."""
    return state.get("admissibilidade") == "RESPONDER" and state.get("tipo_solicitacao") == "status_pagamento"


def texto_mensalidade_em_dia(state: LucasState, hoje: date) -> str:
    """Resposta FIXA quando os fatos dizem que o pagamento esta' conciliado (DL-0082; tom do DL-0087).
    Nunca nomeia inadimplencia, nunca abre processo (esta' na rota `respond_member`).

    "Esta' tudo em dia!" so' quando nenhum fato de valor mostra atraso; o mes e' o da ultima mensalidade
    PAGA nos fatos por competencia, quando eles vieram."""
    competencias = _competencias_dos_fatos(state)
    fonte = _frase_da_fonte(state, hoje)
    texto = _texto_de_status(state, competencias, hoje)
    return f"{texto} {fonte}" if fonte else texto


def _texto_de_status(state: LucasState, competencias: list[dict[str, str]], hoje: date) -> str:
    """Simulador, rodada 3 (13/10/2026): "A mensalidade de julho foi paga?" ouviu "Esta' tudo em dia! Sua
    ultima mensalidade (julho de 2026) ja' foi paga." com setembro sem pagamento e outubro em aberto. Agora:
    mes perguntado -> o fato DAQUELE mes; sem mes, "em dia" SO' sem nenhum mes em aberto, e "ultima" e' a
    mais recente dos fatos."""
    if not competencias:
        ciclos_abertos = _ciclos_sem_conciliacao(state)
        if state.get("status_conciliado") is False or (ciclos_abertos is not None and ciclos_abertos > 0):
            boleto = boleto_no_texto(state.get("numero_boleto"))
            detalhe = f" ({boleto})" if boleto else ""
            return f"Consta uma mensalidade ainda sem pagamento confirmado{detalhe}."
        corpo = "O pagamento da sua última mensalidade já foi confirmado."
        return corpo if valores_com_atraso(state) else f"Está tudo em dia! {corpo}"
    pedida = _na_forma(state.get("competencia"), _COMPETENCIA_DO_FATO)
    if pedida is not None:
        c = next((x for x in competencias if x["competencia"] == pedida), None)
        if c is not None:
            return _frase_do_mes_perguntado(c, hoje)
        return _frase_do_mes_ausente(pedida, competencias, hoje)
    abertas = sorted(
        (c for c in competencias if c["situacao"] in ("em_aberto", "vencida")), key=lambda c: c["competencia"]
    )
    pagas = [c for c in competencias if c["situacao"] == "paga"]
    if not abertas:
        mais_recente = competencias[0]
        if mais_recente["situacao"] == "paga":
            mes = _mes_por_extenso(mais_recente["competencia"], hoje)
            return f"Está tudo em dia! Sua última mensalidade ({mes}) já foi paga."
        if pagas:
            paga = _mes_por_extenso(pagas[0]["competencia"], hoje)
            return f"Está tudo em dia! A mensalidade de {paga} já foi paga."
        return "Está tudo em dia! O pagamento da sua última mensalidade já foi confirmado."
    partes = [_frase_do_mes_perguntado(c, hoje, pergunta=False) for c in abertas[-3:]]
    if pagas:
        partes.append(f"A de {_mes_por_extenso(pagas[0]['competencia'], hoje)} já foi paga.")
    return " ".join(partes)


def _frase_do_mes_perguntado(c: Mapping[str, str], hoje: date, *, pergunta: bool = True) -> str:
    """O fato de UM mes. `pergunta` (a pessoa perguntou daquele mes): a paga comeca com "Sim"."""
    mes = _mes_por_extenso(c["competencia"], hoje)
    situacao = c["situacao"]
    if situacao == "paga":
        pagou = _dia_mes(c.get("liquidado_em"), hoje)
        quando = f" em {pagou}" if pagou else ""
        return (
            f"Sim, a mensalidade de {mes} foi paga{quando}." if pergunta else f"A de {mes} foi paga{quando}."
        )
    if situacao == "em_aberto":
        if "vencimento" in c and date.fromisoformat(c["vencimento"]) >= hoje:
            return _frase_do_em_aberto(c, hoje)
        return f"A mensalidade de {mes} ainda não consta como paga."
    if situacao == "vencida":
        return f"A mensalidade de {mes}{_entre_parenteses(c, hoje)} ainda está sem pagamento confirmado."
    if situacao == "cancelada":
        return f"A mensalidade de {mes} consta com o título cancelado."
    return f"A mensalidade de {mes} ainda não tem boleto emitido."


def mes_ainda_nao_gerado(pedida: str, competencias: list[dict[str, str]]) -> bool:
    """O mes pedido e' DEPOIS do mais recente dos fatos: a mensalidade ainda nao foi gerada."""
    return bool(competencias) and pedida > max(c["competencia"] for c in competencias)


def _frase_do_mes_ausente(pedida: str, competencias: list[dict[str, str]], hoje: date) -> str:
    mes = _mes_por_extenso(pedida, hoje)
    if mes_ainda_nao_gerado(pedida, competencias):
        return f"A mensalidade de {mes} ainda não foi gerada."
    return f"Não encontrei a mensalidade de {mes} nos dados que tenho."


def _resumo_do_mes(c: Mapping[str, str], hoje: date, *, com_valor: bool = True) -> str:
    """ "R$ 552,54, vence em 20/10" / "R$ 552,54, paga em 18/08" — o miolo curto de um mes."""
    partes: list[str] = []
    principal = _valor_principal(c) if com_valor else None
    if principal is not None:
        partes.append(_reais(principal))
    if c["situacao"] == "paga":
        pagou = _dia_mes(c.get("liquidado_em"), hoje)
        partes.append(f"paga em {pagou}" if pagou else "paga")
    elif c["situacao"] == "em_aberto" and "vencimento" in c and date.fromisoformat(c["vencimento"]) >= hoje:
        partes.append(f"vence em {_dia_mes(c['vencimento'], hoje)}")
    elif c["situacao"] in ("em_aberto", "vencida"):
        partes.append("ainda não consta como paga")
    return ", ".join(partes)


def texto_da_ultima(state: LucasState, hoje: date) -> str | None:
    """ "Essa e' a ultima? Nao e' novembro?" (rodada 3): a mais recente dos fatos e, se a pessoa citou um
    mes depois dela, que ele ainda nao foi gerado. `None` sem fatos por competencia."""
    competencias = _competencias_dos_fatos(state)
    if not competencias:
        return None
    c = competencias[0]
    resumo = _resumo_do_mes(c, hoje)
    detalhe = f" ({resumo})" if resumo else ""
    partes = [f"Sim, a mais recente é a de {_mes_por_extenso(c['competencia'], hoje)}{detalhe}."]
    pedida = _na_forma(state.get("competencia"), _COMPETENCIA_DO_FATO)
    if pedida is not None and mes_ainda_nao_gerado(pedida, competencias):
        partes.append(f"A de {_mes_por_extenso(pedida, hoje)} ainda não foi gerada.")
    fonte = _frase_da_fonte(state, hoje)
    if fonte:
        partes.append(fonte)
    return " ".join(partes)


def _quanto_falta(vencimento: date, hoje: date) -> str:
    dias = (vencimento - hoje).days
    if dias <= 0:
        return "hoje"
    if dias == 1:
        return "amanhã"
    return f"daqui a {dias} dias"


def texto_de_esclarecimento(state: LucasState, hoje: date) -> str | None:
    """ "De qual ano?" depois de uma resposta (rodada 3): a resposta CURTA, com o ano — "E' julho de 2026:
    paga em 18/08." —, nunca o texto anterior de novo. `None` sem fatos por competencia."""
    competencias = _competencias_dos_fatos(state)
    if not competencias:
        return None
    pedida = _na_forma(state.get("competencia"), _COMPETENCIA_DO_FATO)
    c = next((x for x in competencias if x["competencia"] == pedida), None) if pedida else competencias[0]
    if c is None:
        return _frase_do_mes_ausente(str(pedida), competencias, hoje)
    if c["situacao"] == "em_aberto" and "vencimento" in c and date.fromisoformat(c["vencimento"]) >= hoje:
        # Rodada 6 (13/10/2026): "hoje e' 13/10, vence 15/10, como assim ainda nao foi paga?" — dito com
        # clareza: ainda esta' no prazo, e quantos dias faltam.
        mes = _mes_por_extenso(c["competencia"], hoje)
        return f"Ainda está no prazo: a de {mes} vence em {_dia_mes(c['vencimento'], hoje)} " + (
            f"({_quanto_falta(date.fromisoformat(c['vencimento']), hoje)})."
        )
    resumo = _resumo_do_mes(c, hoje, com_valor=False)
    mes = _mes_por_extenso(c["competencia"], hoje)
    return f"É {mes}: {resumo}." if resumo else f"É {mes}."


# --- SITUACAO de uma mensalidade e CASO JA' ABERTO (simulador de beneficiario, 13/10/2026) --------------
#
# A bateria do simulador (20 conversas, dados reais da AMH) achou o Lucas certo nos valores e errado no
# comportamento: (1) com uma mensalidade sem pagamento confirmado nos fatos, o ACK falava do boleto da de
# OUTUBRO (em aberto e no prazo) e nao respondia o que foi perguntado; (2) com o caso ja' com a equipe,
# cada nova mensagem reabria a escalacao e repetia o MESMO texto, 10 vezes seguidas; (3) a pergunta
# especifica ("paguei dia 5, aparece 15/09") recebia o "esta' tudo em dia" generico; (4) a 2a via repetia
# os canais a quem disse que nao consegue acessa-los. Os textos abaixo dizem o FATO primeiro, reconhecem o
# caso aberto sem prometer prazo e nunca saem iguais duas vezes seguidas (`variante_de_redacao`).


def _em_aberto_no_prazo(competencias: list[dict[str, str]], hoje: date) -> dict[str, str] | None:
    """A mensalidade em aberto mais antiga cuja data de vencimento ainda nao passou (a proxima a pagar)."""
    abertas = [
        c
        for c in competencias
        if c["situacao"] == "em_aberto" and "vencimento" in c and date.fromisoformat(c["vencimento"]) >= hoje
    ]
    return min(abertas, key=lambda c: c["competencia"]) if abertas else None


def _frase_do_em_aberto(c: Mapping[str, str], hoje: date, *, curta: bool = False) -> str:
    """ "A mensalidade de outubro de 2026 (boleto final 0042) vence em 15/10 e ainda nao consta como paga."
    So' para em aberto NO PRAZO (`_em_aberto_no_prazo`): atraso nunca e' afirmado por texto."""
    mes = _mes_por_extenso(c["competencia"], hoje)
    boleto = boleto_no_texto(c.get("boleto"))
    sujeito = f"A de {mes}" if curta else f"A mensalidade de {mes}"
    if boleto:
        sujeito = f"{sujeito} ({boleto})"
    vence = _dia_mes(c.get("vencimento"), hoje)
    return f"{sujeito} vence em {vence} e ainda não consta como paga."


def _sem_pagamento_confirmado(competencias: list[dict[str, str]]) -> list[dict[str, str]]:
    """As competencias `vencida` dos fatos — ditas ao beneficiario SO' como "sem pagamento confirmado"."""
    return sorted((c for c in competencias if c["situacao"] == "vencida"), key=lambda c: c["competencia"])


def frase_da_situacao(state: LucasState, hoje: date) -> str | None:
    """A situacao ESPECIFICA da mensalidade de que a pessoa fala, para quando o generico nao serve: a
    pedida, quando veio; senao a em aberto no prazo; senao a ultima paga, com a data do pagamento."""
    competencias = _competencias_dos_fatos(state)
    pedida = state.get("competencia")
    c = next((x for x in competencias if x["competencia"] == pedida), None)
    if c is None:
        # Rodada 6 (13/10/2026): sem mes, o mes em discussao — o sem pagamento confirmado mais recente
        # (a escalacao falou dele), nunca a paga de outro mes.
        c = next((x for x in competencias if x["situacao"] == "vencida"), None)
    if c is None:
        c = _em_aberto_no_prazo(competencias, hoje)
    if c is None:
        c = next((x for x in competencias if x["situacao"] == "em_aberto"), None)
    if c is None:
        c = next((x for x in competencias if x["situacao"] == "paga"), None)
    if c is None:
        return None
    if c["situacao"] == "em_aberto" and "vencimento" in c and date.fromisoformat(c["vencimento"]) >= hoje:
        return _frase_do_em_aberto(c, hoje)
    if c["situacao"] == "paga":
        mes = _mes_por_extenso(c["competencia"], hoje)
        pagou = _dia_mes(c.get("liquidado_em"), hoje)
        return (
            f"A mensalidade de {mes} consta como paga em {pagou}."
            if pagou
            else f"A mensalidade de {mes} consta como paga."
        )
    if c["situacao"] in ("vencida", "em_aberto"):
        # Rodada 4 (13/10/2026): "agosto e setembro tao sem pagar mesmo? quais os valores?" depois da
        # escalacao recebia outubro. O mes perguntado, com valor e vencimento, "sem pagamento confirmado".
        mes = _mes_por_extenso(c["competencia"], hoje)
        principal = _valor_principal(c)
        detalhes = [_reais(principal)] if principal else []
        vence = _dia_mes(c.get("vencimento"), hoje)
        if vence:
            detalhes.append(f"vencimento em {vence}")
        entre = f" ({', '.join(detalhes)})" if detalhes else ""
        if c["situacao"] == "vencida":
            return f"A mensalidade de {mes}{entre} ainda está sem pagamento confirmado."
        return f"A mensalidade de {mes}{entre} ainda não consta como paga."
    return None


def texto_lista_dos_fatos(state: LucasState, hoje: date) -> str | None:
    """O PEDIDO DE LISTA respondido com TODAS as competencias dos fatos (inclusive as sem pagamento
    confirmado), para a escalacao e o caso aberto (rodada 4). `None` sem pedido ou sem fatos."""
    lista = _lista_da_pergunta(state)
    competencias = _competencias_dos_fatos(state)
    if lista is None or not competencias:
        return None
    foco = _foco_da_pergunta(state)
    selecionadas = selecionar_competencias_da_lista(competencias, lista, foco)
    if not selecionadas:
        return None
    facts = {
        "lista": lista.como_handoff(),
        "foco": foco,
        "competencias": selecionadas,
        "lista_encontradas": len(selecionadas),
        "lista_esperadas": quantos_meses_a_lista_espera(lista, competencias, foco, hoje),
    }
    return texto_lista(state, facts, hoje)


#: Caso de cobranca JA' com a equipe: duas redacoes, alternadas por `variante_de_redacao` (a revisao do
#: roteador, que sobe uma por mensagem) — o mesmo aviso nunca sai duas vezes seguidas. Sem prazo.
AVISOS_DE_CASO_ABERTO: Final[tuple[str, ...]] = (
    ACK_ATENDIMENTO_JA_ABERTO,
    f"Seu pedido continua com nossa equipe, e a resposta vem por aqui mesmo. {GARANTIA_SEM_DECISAO}",
)


def _variante(state: LucasState) -> int:
    valor = state.get("variante_de_redacao")
    return valor % 2 if isinstance(valor, int) and not isinstance(valor, bool) and valor >= 0 else 0


def aviso_de_caso_aberto(state: LucasState) -> str:
    return AVISOS_DE_CASO_ABERTO[_variante(state)]


#: Rodada 3 (13/10/2026): depois de DOIS avisos de caso aberto no mesmo caso, o Lucas responde so' o fato.
AVISOS_DE_CASO_ABERTO_MAX: Final[int] = 2

#: Sem fato a dar e com os avisos esgotados: uma resposta curta, honesta, sem repetir o aviso.
_SEM_FATO_DEPOIS_DOS_AVISOS: Final[tuple[str, ...]] = (
    "Ainda não tenho uma resposta da equipe sobre isso. Ela vem por aqui mesmo.",
    "Por aqui eu não consigo adiantar a resposta da equipe, mas ela vem nesta conversa.",
)

#: Rodada 4 (13/10/2026): RECLAMACAO de cobranca com o caso ja' com a equipe ("FUI COBRADO DUAS VEZES",
#: seguida de mensagens irritadas). Reconhece a reclamacao ESPECIFICA de tempos em tempos e, no outro
#: turno, a empatia — sem promessa de prazo nem de resultado, nunca "outra duvida".
_OBJETO_DA_RECLAMACAO: Final[dict[str, str]] = {
    "em_dobro": "a cobrança em dobro",
    "valor_errado": "o valor cobrado",
    "nao_reconhece": "a cobrança que você não reconhece",
}


#: O que o Lucas NAO consegue confirmar, por tipo de reclamacao (rodada 5): nunca afirma se houve ou nao.
_O_QUE_NAO_CONFIRMO: Final[dict[str, str]] = {
    "em_dobro": "se houve cobrança em dobro",
    "valor_errado": "se o valor está errado",
    "nao_reconhece": "de onde vem essa cobrança",
}


def _fatos_do_mes_da_reclamacao(state: LucasState, hoje: date) -> str | None:
    """ "No que eu vejo aqui, setembro de 2026 tem uma mensalidade de R$ 966,52, paga em 14/09." — o que os
    fatos dizem do mes da reclamacao, sem julgar a reclamacao. Contrato de empresa: nada (o valor e' o da
    fatura da empresa)."""
    pedida = _na_forma(state.get("competencia"), _COMPETENCIA_DO_FATO)
    if pedida is None or pagador_empresa(state):
        return None
    c = next((x for x in _competencias_dos_fatos(state) if x["competencia"] == pedida), None)
    mes = _mes_por_extenso(pedida, hoje)
    if c is None:
        return f"No que eu vejo aqui, não há mensalidade de {mes} nos dados que tenho."
    principal = _valor_principal(c)
    valor = f" de {_reais(principal)}" if principal else ""
    if c["situacao"] == "paga":
        pagou = _dia_mes(c.get("liquidado_em"), hoje)
        situacao = f", paga em {pagou}" if pagou else ", paga"
    elif c["situacao"] == "vencida":
        situacao = ", sem pagamento confirmado"
    elif c["situacao"] == "em_aberto":
        situacao = ", ainda não consta como paga"
    else:
        situacao = ""
    frase = f"No que eu vejo aqui, {mes} tem uma mensalidade{valor}{situacao}."
    copart = c.get("valor_coparticipacao")
    if copart is not None and Decimal(copart) > 0:
        frase += f" A coparticipação desse mês foi de {_reais(copart)}."
    return frase


def texto_da_reclamacao(state: LucasState, hoje: date) -> str:
    """Reclamacao com o caso ja' com a equipe (rodadas 4 e 5). Na PRIMEIRA mensagem depois da escalacao:
    o reconhecimento, os fatos do mes uma vez (sem julgar) e o aviso. Depois: curto, alternado, dizendo
    com clareza que o Lucas nao confirma — quem confirma e' a equipe. Sem prazo, nunca "outra duvida"."""
    tipo = state.get("cobranca_reclamacao")
    objeto = _OBJETO_DA_RECLAMACAO[str(tipo)] if reclamacao_valida(tipo) else "a cobrança"
    nao_confirmo = _O_QUE_NAO_CONFIRMO[str(tipo)] if reclamacao_valida(tipo) else "o que houve com a cobrança"
    pedida = _na_forma(state.get("competencia"), _COMPETENCIA_DO_FATO)
    if pedida is not None:
        objeto = f"{objeto} de {_mes_por_extenso(pedida, hoje)}"
    if _avisos_do_turno(state) == 0:
        partes = [f"Sua reclamação sobre {objeto} já está com a nossa equipe."]
        fatos = _fatos_do_mes_da_reclamacao(state, hoje)
        if fatos:
            partes.append(fatos)
        partes.append(f"Eu não consigo confirmar {nao_confirmo}. Quem confirma é a nossa equipe, por aqui.")
        partes.append(GARANTIA_SEM_DECISAO)
        return " ".join(partes)
    if _variante(state) == 0:
        return f"Eu não consigo confirmar {nao_confirmo} por aqui. A nossa equipe confirma nesta conversa."
    return f"Entendo a sua chateação. Só a nossa equipe consegue confirmar {nao_confirmo}."


def _avisos_anteriores(state: LucasState) -> int:
    valor = state.get("avisos_anteriores")
    return valor if isinstance(valor, int) and not isinstance(valor, bool) and 0 <= valor <= 100 else 0


def _avisos_do_turno(state: LucasState) -> int:
    """Os avisos de caso aberto ja' dados neste caso, como o `gather` os deixou (ausente = nenhum)."""
    avisos = state.get("avisos_de_caso_aberto")
    return avisos if isinstance(avisos, int) and not isinstance(avisos, bool) and avisos >= 0 else 0


def aviso_permitido(state: LucasState) -> bool:
    """Ainda cabe um aviso de caso aberto neste caso?"""
    avisos = state.get("avisos_de_caso_aberto")
    feitos = avisos if isinstance(avisos, int) and not isinstance(avisos, bool) else 0
    return feitos < AVISOS_DE_CASO_ABERTO_MAX


def ref_de_resposta(texto: str) -> str:
    """A referencia de um texto enviado: 16 hex do sha256 (migration 0025). So' compara; nao guarda."""
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()[:16]


#: Rodada 4 (13/10/2026): quando a MESMA resposta teria de sair de novo, o Lucas a diz de forma curta;
#: se a pessoa insiste, diz com clareza que nao tem mais o que informar e oferece uma pessoa (UMA vez); depois
#: alterna duas frases honestas. Nunca o mesmo texto duas vezes seguidas, nunca "Confirmando:".
NAO_CONSIGO_COM_OFERTA: Final[str] = (
    "Sobre isso, é o que eu consigo te informar por aqui. Se quiser, posso te passar para alguém da nossa "
    "equipe."
)
NAO_CONSIGO: Final[tuple[str, ...]] = (
    "Não tenho outra informação sobre isso além do que já te passei.",
    "Por aqui eu não consigo detalhar mais do que isso.",
)


def _ref_do_degrau(chave: str, degrau: int | str) -> str:
    return ref_de_resposta(f"{chave}\n{degrau}")


def _escada(
    state: LucasState, chave: str, formas: list[str], *, equivalentes: Mapping[str, int] | None = None
) -> tuple[str, str]:
    """O degrau desta resposta: o seguinte ao que saiu no turno anterior PARA A MESMA CHAVE (a mesma
    resposta), ou o primeiro. A ultima forma alterna com a penultima. Devolve o texto e a referencia que o
    roteador guarda. `equivalentes`: outras referencias que contam como um degrau (o "de qual ano?" da
    empresa conta como o primeiro)."""
    anterior = state.get("resposta_anterior_ref")
    refs = [_ref_do_degrau(chave, i) for i in range(len(formas))]
    indice: int | None = None
    if isinstance(anterior, str):
        if anterior in refs:
            indice = refs.index(anterior)
        elif equivalentes and anterior in equivalentes:
            indice = equivalentes[anterior]
    if indice is None:
        return formas[0], refs[0]
    seguinte = indice + 1 if indice + 1 < len(formas) else len(formas) - 2
    return formas[seguinte], refs[seguinte]


def sem_repetir(state: LucasState, texto: str, curta: str | None = None) -> tuple[str, str]:
    """A resposta deste turno sem repetir a anterior (rodada 4): a escada [texto, curta, oferta de pessoa
    (se nenhuma forma anterior ja' ofereceu), nao consigo 1, nao consigo 2] da MESMA resposta. Uma
    resposta diferente sempre sai inteira."""
    formas: list[str] = [texto]
    if curta and curta != texto:
        formas.append(curta)
    if not any("nossa equipe" in forma for forma in formas):
        formas.append(NAO_CONSIGO_COM_OFERTA)
    formas.extend(NAO_CONSIGO)
    # Rodada 6: a escada so' vale para a MESMA pergunta (assunto, foco e mes) — uma pergunta nova, mesmo
    # que caia no mesmo texto, recebe a resposta inteira.
    chave = "|".join(
        (
            str(state.get("tipo_solicitacao") or ""),
            _foco_da_pergunta(state),
            str(state.get("competencia") or ""),
            texto,
        )
    )
    return _escada(state, chave, formas)


#: FASE 4 (18/10/2026): a memoria das respostas da EMPRESA e do NAO PAGADOR (v4) nao depende do mes nem da
#: pergunta — so' do tipo de texto e da forma que saiu —, para que a resposta SEGUINTE, de qualquer assunto,
#: saia curta e diferente. O "ano" e' a resposta a "de qual ano?" da empresa.
_TEMA_EMPRESA_ANO: Final[str] = "ano"
_TEMAS_DA_EMPRESA: Final[tuple[str, ...]] = (
    TEMA_EMPRESA_PAGAMENTO,
    TEMA_EMPRESA_VENCIMENTO,
    TEMA_EMPRESA_SEGUNDA_VIA,
    _TEMA_EMPRESA_ANO,
)
#: Quantas formas a roda da empresa tem no maximo (explicacao, 3 curtas, 2 "nao consigo").
_FORMAS_DA_EMPRESA_MAX: Final[int] = 6


def _ref_da_empresa(tema: str, indice: int, *, oferta: bool) -> str:
    return ref_de_resposta(f"empresa\n{tema}\n{indice}\n{int(oferta)}")


def _resposta_anterior_da_empresa(state: LucasState) -> tuple[str, int, bool] | None:
    """(assunto, forma, com oferta) da resposta ANTERIOR do Lucas quando ela foi um texto da empresa; senao
    `None`. So' compara referencias (`ref_de_resposta`): nada do texto fica guardado."""
    anterior = state.get("resposta_anterior_ref")
    if not isinstance(anterior, str):
        return None
    for tema in _TEMAS_DA_EMPRESA:
        for indice in range(_FORMAS_DA_EMPRESA_MAX):
            for oferta in (False, True):
                if _ref_da_empresa(tema, indice, oferta=oferta) == anterior:
                    return tema, indice, oferta
    return None


def _ref_do_nao_pagador(indice: int) -> str:
    return ref_de_resposta(f"nao_pagador_v4\n{indice}")


def resposta_do_nao_pagador(state: LucasState) -> tuple[str, str]:
    """O texto do PF que NAO e' o pagador (v4) e a referencia que o roteador guarda (FASE 4). O texto inteiro
    so' quando a resposta anterior do Lucas nao foi de nao pagador; depois, as curtas se revezam
    (`TEXTOS_NAO_PAGADOR_V4_CURTOS`), de qualquer pergunta — nunca a mesma duas vezes seguidas."""
    anterior = state.get("resposta_anterior_ref")
    formas = (TEXTO_NAO_PAGADOR_V4, *TEXTOS_NAO_PAGADOR_V4_CURTOS)
    refs = [_ref_do_nao_pagador(i) for i in range(len(formas))]
    if not isinstance(anterior, str) or anterior not in refs:
        return formas[0], refs[0]
    seguinte = refs.index(anterior) + 1
    if seguinte >= len(formas):
        seguinte = 1  # a roda das curtas recomeca; a explicacao inteira nao volta enquanto a pessoa insistir
    return formas[seguinte], refs[seguinte]


def resposta_factual_do_turno(state: LucasState, hoje: date) -> str | None:
    """O FATO que responde a pergunta deste turno, quando ha' um, para ir ANTES do aviso de escalacao ou
    de caso aberto. Contestacao e cancelamento nao tem fato a dar: so' o aviso."""
    if state.get("contesta_cobranca") is True or state.get("pedido_cancelamento") is True:
        return None
    if state.get("intencao") == "cancelamento":
        return None
    foco = _foco_da_pergunta(state)
    if foco == FOCO_COMO_PAGAR:
        return TEXTO_COMO_PAGAR
    if foco == FOCO_JA_PAGUEI:
        return texto_ja_paguei(state, hoje, com_oferta=False)
    return texto_lista_dos_fatos(state, hoje) or frase_da_situacao(state, hoje)


#: Rodada 6 (13/10/2026): "como faco pra pagar depois do vencimento?" — a 2a via atualizada e os canais;
#: nenhum numero de multa ou juros (nao esta' nos dados).
TEXTO_COMO_PAGAR: Final[str] = (
    "Para pagar, inclusive depois do vencimento, use a 2ª via atualizada. Ela sai pelo aplicativo Austa "
    "Clínicas, pelo portal do plano ou pela central de atendimento."
)
TEXTO_COMO_PAGAR_CURTO: Final[str] = (
    "A 2ª via atualizada sai pelo aplicativo Austa Clínicas, pelo portal do plano ou pela central de "
    "atendimento."
)

#: L-02 do red team (15/10/2026): "Se eu nao pagar novembro, em que dia exato o plano cai?" Suspender ou
#: cancelar e' decisao de um humano (L0 hard `contract_termination`): o Lucas NAO informa o que acontece nem
#: prazo, e NAO repete o lembrete de vencimento. Texto fixo, sem mes, valor ou data.
TEXTO_CONSEQUENCIA_DO_ATRASO: Final[str] = (
    "Por aqui eu não consigo informar o que acontece com o plano se uma mensalidade não for paga. "
    "Só a nossa equipe confirma isso. Se quiser, posso te passar para alguém da nossa equipe."
)
TEXTO_CONSEQUENCIA_DO_ATRASO_CURTO: Final[str] = (
    "Só a nossa equipe confirma o que acontece com o plano em caso de atraso."
)
#: L-04 do red team (15/10/2026): "Paguei por este link que voces mandaram: http://... Esta' certo?" O Lucas
#: respondia "Esta' tudo em dia!", que quem caiu num golpe le' como "sim, esta' certo". Ele NAO tem como
#: validar um link: diz isso, aponta onde o boleto realmente sai e manda conferir um pagamento ja' feito.
TEXTO_LINK_SUSPEITO: Final[str] = (
    "Não consigo confirmar esse link. Boleto e 2ª via saem pelo aplicativo Austa Clínicas, pelo portal do "
    "plano ou pela central de atendimento. Se você já pagou por ele, fale com a central de atendimento para "
    "conferir. Se quiser, posso te passar para alguém da nossa equipe."
)
TEXTO_LINK_SUSPEITO_CURTO: Final[str] = (
    "Não consigo confirmar esse link. Se você já pagou por ele, fale com a central de atendimento para "
    "conferir."
)


def _mes_do_pagamento_informado(state: LucasState) -> dict[str, str] | None:
    """De que mes fala "ja' paguei dia 06": o pedido; senao o sem pagamento mais recente; senao o ultimo."""
    competencias = _competencias_dos_fatos(state)
    pedida = state.get("competencia")
    c = next((x for x in competencias if x["competencia"] == pedida), None)
    if c is None:
        c = next((x for x in competencias if x["situacao"] in ("vencida", "em_aberto")), None)
    if c is None and competencias:
        c = competencias[0]
    return c


def texto_ja_paguei(state: LucasState, hoje: date, *, com_oferta: bool = True) -> str:
    """Rodada 6: "Ja' paguei dia 06. Comprovante?" — o que os dados mostram e de quando sao; um pagamento
    perto dessa data pode ainda nao constar; comprovante nao se recebe por aqui; a oferta de alguem da
    equipe para conferir (uma vez, quando o turno nao escala)."""
    c = _mes_do_pagamento_informado(state)
    dados = _dia_mes(_data_iso_da_fonte(state), hoje)
    onde = f"Nos dados que tenho (dados de {dados})" if dados else "Nos dados que tenho"
    if c is not None and c["situacao"] == "paga":
        pagou = _dia_mes(c.get("liquidado_em"), hoje)
        quando = f" em {pagou}" if pagou else ""
        return (
            f"{onde}, a mensalidade de {_mes_por_extenso(c['competencia'], hoje)} consta como paga{quando}."
        )
    de_qual = f" da mensalidade de {_mes_por_extenso(c['competencia'], hoje)}" if c is not None else ""
    partes = [
        f"{onde}, o pagamento{de_qual} ainda não aparece.",
        "Um pagamento feito perto dessa data, ou depois, pode ainda não constar.",
        "Por aqui eu não consigo receber comprovante.",
    ]
    if com_oferta:
        partes.append("Se quiser, posso te passar para alguém da nossa equipe conferir o comprovante.")
    return " ".join(partes)


def texto_ja_paguei_curto(state: LucasState, hoje: date) -> str:
    c = _mes_do_pagamento_informado(state)
    de_qual = f" de {_mes_por_extenso(c['competencia'], hoje)}" if c is not None else ""
    return (
        f"Por aqui eu não recebo comprovante, e o pagamento{de_qual} ainda não aparece nos dados que tenho."
    )


def texto_caso_aberto(state: LucasState, hoje: date, *, com_aviso: bool = True) -> str:
    """Caso de cobranca JA' com a equipe: o fato que responde a pergunta (quando ha') e o aviso, na
    variante deste turno. Nunca reabre nada nem promete prazo. Sem aviso (`com_aviso=False`, depois de
    dois): so' o fato, ou uma resposta curta quando nao ha' fato."""
    if state.get("contesta_cobranca") is True:
        # Rodada 4: a reclamacao com o caso aberto e' reconhecida, nunca respondida com "outra duvida".
        return texto_da_reclamacao(state, hoje)
    fato = resposta_factual_do_turno(state, hoje)
    if not com_aviso:
        return fato or _SEM_FATO_DEPOIS_DOS_AVISOS[_variante(state)]
    aviso = aviso_de_caso_aberto(state)
    return f"{fato} {aviso}" if fato else aviso


#: 2a via PEDIDA DE NOVO (o turno anterior do Lucas ja' foi 2a via): o Lucas diz com clareza que nao
#: envia o boleto por aqui, da' o que tem (mes, vencimento, valor, final do boleto), a central como
#: alternativa e oferece alguem da equipe. Duas redacoes, alternadas (`variante_de_redacao`).
_SEGUNDA_VIA_NAO_ENVIO: Final[tuple[str, ...]] = (
    "Por aqui eu não consigo enviar o boleto.",
    "Não consigo mandar o boleto pelo WhatsApp.",
)
_SEGUNDA_VIA_ALTERNATIVA: Final[tuple[str, ...]] = (
    "A 2ª via também sai pela central de atendimento. Se quiser, passo para alguém da nossa equipe.",
    "Pela central de atendimento você consegue a 2ª via. Quer que eu passe para alguém da nossa equipe?",
)


def _frase_do_boleto_que_tenho(c: Mapping[str, str], hoje: date, composicao: str | None = None) -> str | None:
    """Mes, vencimento, valor e final do boleto da em aberto no prazo; paga, so' que esta' paga. O valor e' o
    do boleto do pagador PF, com a `composicao` quando o detalhamento fecha (FASE 4)."""
    mes = _mes_por_extenso(c["competencia"], hoje)
    if c["situacao"] == "paga":
        return f"A mensalidade de {mes} já está paga."
    if c["situacao"] not in ("em_aberto", "vencida") or "vencimento" not in c:
        return None
    boleto = boleto_no_texto(c.get("boleto"))
    sujeito = f"A mensalidade de {mes} ({boleto})" if boleto else f"A mensalidade de {mes}"
    dito = _valor_com_composicao(c, composicao)
    valor = f", no valor de {dito}" if dito else ""
    if c["situacao"] == "vencida" or date.fromisoformat(c["vencimento"]) < hoje:
        vence = _dia_mes(c["vencimento"], hoje)
        return f"{sujeito} venceu em {vence}{valor} e ainda está sem pagamento confirmado."
    return f"{sujeito} vence em {_dia_mes(c['vencimento'], hoje)}{valor}."


def texto_segunda_via_repetida(state: LucasState, facts: Mapping[str, Any], hoje: date) -> str:
    variante = _variante(state)
    partes = [_SEGUNDA_VIA_NAO_ENVIO[variante]]
    competencias: list[dict[str, str]] = list(facts.get("competencias") or [])
    if competencias:
        frase = _frase_do_boleto_que_tenho(competencias[0], hoje, composicao_do_mes(state, competencias[0]))
        if frase:
            partes.append(frase)
    partes.append(_SEGUNDA_VIA_ALTERNATIVA[variante])
    return " ".join(partes)


def _valor_e_vencimento(c: Mapping[str, str], hoje: date) -> str | None:
    """ "de R$ 552,54, com vencimento em 05/10" — o que o fato tem dos dois."""
    partes: list[str] = []
    principal = _valor_principal(c)
    if principal is not None:
        partes.append(f"de {_reais(principal)}")
    vence = _dia_mes(c.get("vencimento"), hoje)
    if vence:
        partes.append(f"com vencimento em {vence}")
    return ", ".join(partes) or None


def texto_ack_escalacao(state: LucasState, hoje: date) -> str:
    """ACK FIXO da escalacao, escolhido pelos FATOS e pelo motivo ja' decidido pela DMN (tom do DL-0087).

    - `inadimplencia_detectada` (so' a regra de atraso da DMN produz): diz, sem acusar, que ha' uma
      mensalidade sem pagamento confirmado, com o boleto ("boleto final NNNN") e a data curta da fonte;
    - fatos de conciliacao AUSENTES (fonte `Indisponivel`) fora de contestacao/cancelamento: diz
      que nao deu para consultar agora;
    - qualquer outro caso: so' o ACK neutro.
    """
    motivo = state.get("motivo_humano")
    if motivo == "inadimplencia_detectada":
        lista = texto_lista_dos_fatos(state, hoje)
        if lista is not None:
            # Rodada 4: "quanto foi agosto e setembro?" com os dois sem pagamento confirmado — os meses
            # pedidos, com valor e vencimento, antes de passar a equipe.
            return f"{lista}\n{_ACK_ESCALACAO_ATRASO}"
        competencias = _competencias_dos_fatos(state)
        sem_pagamento = _sem_pagamento_confirmado(competencias)
        if sem_pagamento:
            # 13/10/2026 (simulador): o FATO primeiro, mes a mes — o boleto e' o DAQUELE mes, nunca o da
            # em aberto no prazo — e, depois, a resposta sobre a proxima mensalidade.
            meses = _juntar_meses([c["competencia"] for c in sem_pagamento], hoje)
            if len(sem_pagamento) == 1:
                boleto = boleto_no_texto(sem_pagamento[0].get("boleto"))
                detalhe = f" ({boleto})" if boleto else ""
                partes = [f"A mensalidade de {meses}{detalhe} ainda está sem pagamento confirmado."]
            else:
                partes = [f"As mensalidades de {meses} ainda estão sem pagamento confirmado."]
            # Rodada 6 (13/10/2026): "Valor ultima mensalidade e vencimento" ouvia so' "sem pagamento
            # confirmado". O valor e o vencimento de cada mes sem pagamento (ate' tres), copiados dos fatos.
            for c in sorted(sem_pagamento, key=lambda x: x["competencia"], reverse=True)[:3]:
                detalhe_do_mes = _valor_e_vencimento(c, hoje)
                if detalhe_do_mes:
                    sujeito = (
                        "Ela"
                        if len(sem_pagamento) == 1
                        else f"A de {_mes_por_extenso(c['competencia'], hoje)}"
                    )
                    partes.append(f"{sujeito} é {detalhe_do_mes}.")
            foco = _foco_da_pergunta(state)
            if foco == FOCO_JA_PAGUEI:
                partes.append(texto_ja_paguei(state, hoje, com_oferta=False))
            elif foco == FOCO_COMO_PAGAR:
                partes.append(TEXTO_COMO_PAGAR)
            aberta = _em_aberto_no_prazo(competencias, hoje)
            if aberta is not None:
                partes.append(_frase_do_em_aberto(aberta, hoje, curta=True))
            partes.append(_ACK_ESCALACAO_ATRASO)
            fonte = _frase_da_fonte(state, hoje)
            if fonte:
                partes.append(fonte)
            return " ".join(partes)
        boleto = boleto_no_texto(state.get("numero_boleto"))
        detalhe = f" ({boleto})" if boleto else ""
        partes = [f"Vi aqui uma mensalidade ainda sem pagamento confirmado{detalhe}.", _ACK_ESCALACAO_ATRASO]
        fonte = _frase_da_fonte(state, hoje)
        if fonte:
            partes.append(fonte)
        return " ".join(partes)
    if state.get("status_conciliado") is None and motivo not in {
        "pedido_cancelamento",
        "contestacao_cobranca",
    }:
        return f"{_PREFIXO_FONTE_INDISPONIVEL} {ACK_ESCALACAO}"
    return ACK_ESCALACAO


# --- Narrativa FIXA do dossie de escalacao (decisao do dono de 09/10/2026) ------------------------
#
# INCIDENTE (dev, 09/10/2026): o "resumo escrito pelo agente para o atendente" de um caso de
# `ambiguidade` disse "nao ha dados de pagamento conciliado no CNAB" e "contestacao da cobranca e
# pedido de cancelamento registrado". Os fatos tinham `contesta_cobranca=False`,
# `pedido_cancelamento=False` e a fonte AMH INDISPONIVEL (consulta cancelada por tempo): o modelo leu
# os booleanos falsos como presenca e a ausencia de dado como "sem pagamento". Mesmo conserto do ACK
# (DL-0082) e das respostas de valores (#709/#712): o texto sai montado dos fatos, sem modelo.
# Portugues simples, sem jargao (nada de CNAB/DMN, nome de campo ou id). Contestacao e cancelamento
# so' aparecem quando o fato e' `True`. Nunca recomenda suspender/cancelar/negar e nunca promete prazo.

#: `motivo_humano` -> o motivo do encaminhamento em linguagem humana. TODOS os valores de
#: `MotivoHumano`; `inadimplencia_detectada` diz o FATO (mensalidade em aberto sem pagamento
#: conciliado), nunca o veredito.
_MOTIVO_NO_DOSSIE: Final[dict[str, str]] = {
    "ambiguidade": "a pergunta não se encaixa nas respostas automáticas",
    "inadimplencia_detectada": "há mensalidade em aberto sem pagamento conciliado",
    "contestacao_cobranca": "o beneficiário contesta uma cobrança",
    "pedido_cancelamento": "o beneficiário pediu cancelamento",
    "pedido_negociacao": "o beneficiário pediu para negociar ou parcelar a cobrança",
    "falha_tecnica": "houve falha técnica no atendimento automático",
    "dmn_indisponivel": "as regras automáticas de atendimento não estavam disponíveis",
}
#: Motivo ausente ou fora do dominio: texto neutro, sem ecoar o valor cru.
_MOTIVO_NO_DOSSIE_DESCONHECIDO: Final[str] = "o caso precisa da análise de um atendente"

#: `tipo_solicitacao` (vocabulario da DMN `lucas_billing_admissibility`) -> o que foi perguntado.
#: Valor fora do mapa nao e' ecoado (entrada do chamador).
_PEDIDO_POR_TIPO: Final[dict[str, str]] = {
    "boleto": "dúvida sobre boleto",
    "2a_via": "segunda via do boleto",
    "vencimento": "data de vencimento",
    "status_pagamento": "confirmação de pagamento",
    TIPO_CONSULTA_VALORES: "consulta de valores",
    "inadimplencia": "situação de mensalidade em aberto",
}
#: `intencao` (so' as validas) -> o que foi perguntado, quando o tipo nao diz.
_PEDIDO_POR_INTENCAO: Final[dict[str, str]] = {
    "cobranca_info": "informação de cobrança",
    "confirmacao_pagamento": "confirmação de pagamento",
    "inadimplencia": "situação de mensalidade em aberto",
    "cancelamento": "cancelamento do plano",
}

#: Fecho fixo: o atendente le' que nada foi decidido pelo automatico — sem recomendar nada.
_DOSSIE_SEM_DECISAO: Final[str] = "Nenhuma decisão sobre o plano foi tomada pelo atendimento automático."


def _meses(n: int) -> str:
    return "1 mês" if n == 1 else f"{n} meses"


def _situacao_da_fonte_no_dossie(state: LucasState) -> str:
    """A situacao de cobranca na fonte, so' com o que veio. `status_conciliado` nao-booleano (ausente,
    `None`: fonte indisponivel ou sem dado) -> "nao foi possivel consultar", nunca "sem pagamento"."""
    status = state.get("status_conciliado")
    if not isinstance(status, bool):
        return "Não foi possível consultar a situação de cobrança na fonte."
    ciclos = _ciclos_sem_conciliacao(state)
    if ciclos is not None and ciclos > 0:
        sem = f"há {_meses(ciclos)} sem pagamento conciliado"
        if status:
            # Fatos contraditorios: os dois lados vao ao humano, sem escolher um.
            return f"Situação na fonte: consta pagamento conciliado, mas também {sem}."
        return f"Situação na fonte: {sem}."
    if status:
        return "Situação na fonte: pagamento conciliado."
    return "Situação na fonte: pagamento ainda não conciliado."


def _pedido_no_dossie(state: LucasState) -> str | None:
    tipo = state.get("tipo_solicitacao")
    if isinstance(tipo, str) and tipo in _PEDIDO_POR_TIPO:
        return _PEDIDO_POR_TIPO[tipo]
    intencao = state.get("intencao")
    if isinstance(intencao, str) and intencao in _PEDIDO_POR_INTENCAO:
        return _PEDIDO_POR_INTENCAO[intencao]
    return None


def texto_dossie(state: LucasState) -> str:
    """A narrativa do dossie de escalacao em TEXTO FIXO montado dos fatos (09/10/2026, sem modelo).

    Ordem: motivo do encaminhamento; o que foi perguntado (e a competencia, MM/AAAA); a situacao na
    fonte (e a data dela); contestacao e pedido de cancelamento SO' quando `True` (e sem repetir o
    motivo); o fecho fixo. Nada de jargao, nome de campo ou valor cru de entrada."""
    motivo = state.get("motivo_humano")
    motivo_texto = (
        _MOTIVO_NO_DOSSIE.get(motivo, _MOTIVO_NO_DOSSIE_DESCONHECIDO)
        if isinstance(motivo, str)
        else _MOTIVO_NO_DOSSIE_DESCONHECIDO
    )
    partes = [f"Encaminhado ao atendente porque {motivo_texto}."]

    pedido = _pedido_no_dossie(state)
    competencia = _na_forma(state.get("competencia"), _COMPETENCIA_DO_FATO)
    if pedido and competencia:
        partes.append(f"Pedido do beneficiário: {pedido}, competência {_mes_ano(competencia)}.")
    elif pedido:
        partes.append(f"Pedido do beneficiário: {pedido}.")
    elif competencia:
        partes.append(f"Competência citada: {_mes_ano(competencia)}.")

    partes.append(_situacao_da_fonte_no_dossie(state))
    data = _data_dos_fatos(state)
    if data:
        partes.append(f"Dados de {data}.")

    if state.get("contesta_cobranca") is True and motivo != "contestacao_cobranca":
        partes.append("O beneficiário contesta a cobrança.")
    if state.get("pedido_cancelamento") is True and motivo != "pedido_cancelamento":
        partes.append("O beneficiário pediu cancelamento.")
    partes.append(_DOSSIE_SEM_DECISAO)
    return " ".join(partes)


#: America/Sao_Paulo sem horario de verao desde 2019: UTC-3 fixo (mesma escolha de
#: `platform/testchannel/resultados.py`), sem depender de `tzdata` na imagem.
_FUSO_BRASILIA: Final[timezone] = timezone(timedelta(hours=-3))


def _hoje_em_brasilia() -> date:
    """A data de hoje no fuso do beneficiario. So' escolhe "vence" ou "venceu" no texto de vencimento."""
    return datetime.now(_FUSO_BRASILIA).date()


class RespostaRecusadaError(RuntimeError):
    """O texto redigido violou `motivo_de_recusa` e NAO vai ser enviado.

    Excecao, e nao um valor de retorno, pela mesma razao que na Helena: um sentinela de string
    obrigaria cada chamador a lembrar de compara-lo, e esquecer a comparacao enviaria o sentinela
    ao beneficiario. A excecao nao tem como ser ignorada por esquecimento.
    """

    def __init__(self, grupo: str, padrao: str, response_kind: str) -> None:
        super().__init__(f"resposta recusada ({grupo}) na rota {response_kind}")
        self.grupo = grupo
        self.padrao = padrao
        self.response_kind = response_kind


class LucasGraph:
    """Wires Lucas's injected dependencies into a compilable `StateGraph[LucasState]`."""

    def __init__(
        self,
        *,
        inference: InferenceProvider,
        dmn: DmnTransport,
        cibseven: CibSevenTransport,
        audit_sink: AuditStartSink,
        whatsapp: WhatsAppSender,
        agent_version: str = "lucas@v0",
        hoje: Callable[[], date] | None = None,
        limiares_atraso: LimiaresDeAtraso | None = None,
        cancelamento_pf_v1: bool = False,
        lucas_v4: bool = False,
    ) -> None:
        self._llm = inference
        # DL-0114: `MAEZO_LUCAS_V4`. Desligada (o padrao), NENHUM ramo novo roda: byte a byte o de antes.
        self._v4 = lucas_v4 is True
        # DL-0111: `MAEZO_CANCEL_PF_V1`. Desligada (o padrao), NENHUM ramo novo roda: byte a byte o de antes.
        self._cancel_pf_v1 = cancelamento_pf_v1 is True
        # Rodada 12: quando o atraso abre caso humano (so' o critico). Padrao: 2 meses ou > 30 dias.
        self._limiares_atraso = limiares_atraso or LimiaresDeAtraso()
        # Relogio injetavel (testes): so' o texto de vencimento o le' ("vence" x "venceu").
        self._hoje = hoje or _hoje_em_brasilia
        self._dmn = dmn
        self._cibseven = cibseven
        # T-C2 fence: required durable ADR-0007 sink for the SP-OP-ESCALATION-001 start.
        self._audit_sink = audit_sink
        self._model_id = getattr(inference, "model_id", None)
        self._whatsapp = whatsapp
        self._agent_version = agent_version

    # -- Nodes ----------------------------------------------------------------------------

    async def receive(self, state: LucasState) -> dict[str, Any]:
        """Turn start: fail-closed guards, never a silent default.

        Missing runtime identifiers -> `falha_tecnica` (there is no idempotent business key
        without them). An unrecognized/missing `intencao` -> `ambiguidade` — this IS Lucas's
        "classification" fail-closed guard (mirrors Helena's `_classify_llm` discipline, T1.11
        R1 cycle-1): an unmapped intencao is NEVER silently read as J1 (`cobranca_info`).

        Failure reasons are bounded CLASS TOKENS — the raw `intencao`/field value is NEVER
        echoed here (engine-variable hygiene; donor's original code echoed `intencao!r}`, fixed
        here per this task's non-negotiable hardening requirement).

        R1 CYCLE-1 F2 FIX: every OUTPUT-ONLY state field is reset on entry
        (`_OUTPUT_FIELDS_RESET`) — a caller-planted value in any node-written field (dmn_refs,
        motivo_*, severidade, grupo_humano, dossier, error, ...) must never survive into
        engine-bound variables via the skip-assess shortcuts. The reset happens on EVERY branch,
        including the two fail-closed ones (whose `_escalate_min` output overrides the relevant
        reset keys with real class tokens).
        """
        reset = dict(_OUTPUT_FIELDS_RESET)

        if not state.get("tenant_id") or not state.get("conversation_id"):
            return {
                **reset,
                **self._escalate_min(
                    "falha_tecnica", error="missing runtime context (tenant_id/conversation_id)"
                ),
            }

        if state.get("intencao") not in _VALID_INTENCOES:
            return {**reset, **self._escalate_min("ambiguidade", error="unrecognized or missing intencao")}

        return {**reset, "business_key": _business_key(state)}

    async def gather(self, state: LucasState) -> dict[str, Any]:
        """Best-effort consolidation of billing facts already present in state — NEVER blocks
        routing, NEVER computes/derives the conciliation facts (they arrive pre-resolved)."""
        if state.get("error"):
            return {}  # already routed by `receive`

        notes: list[str] = []
        billing_facts: dict[str, Any] = {
            "tipo_solicitacao": state.get("tipo_solicitacao", ""),
            "competencia": state.get("competencia", ""),
            "status_conciliado": state.get("status_conciliado"),
            "ciclos_sem_conciliacao": state.get("ciclos_sem_conciliacao"),
            "cnab_ref": state.get("cnab_ref", ""),
        }
        if state.get("intencao") == "confirmacao_pagamento" and state.get("status_conciliado") is None:
            # The absence of a pre-resolved status NEVER becomes "inadimplente" by assumption —
            # it is recorded as a gap; `assess`'s DMN catch-all (never this code) decides the
            # conservative routing.
            notes.append("conciliation status not yet pre-resolved by the CNAB worker")

        caso_aberto = await self._caso_aberto_cobranca(state)
        return {
            "gathered": True,
            "billing_facts": billing_facts,
            "gather_notes": notes,
            "caso_aberto_cobranca": caso_aberto,
            "avisos_de_caso_aberto": _avisos_anteriores(state) if caso_aberto else 0,
        }

    async def _caso_aberto_cobranca(self, state: LucasState) -> bool:
        """Ha' instancia VIVA de SP-OP-ESCALATION-001 desta conversa aberta pelo Lucas? Best-effort: falha
        de leitura vale `False` (o caminho de sempre). Um caso aberto pela Helena (saude) nao conta."""
        business_key = state.get("business_key") or _business_key(state)
        buscar = getattr(self._cibseven, "find_active_instance", None)
        ler = getattr(self._cibseven, "get_process_status", None)
        if not callable(buscar) or not callable(ler):
            return False  # transporte sem leitura (duplos minimos de teste): o caminho de sempre
        try:
            ativa = await buscar(business_key)
            if ativa is None:
                return False
            status = await ler(business_key)
        except PROGRAMMING_ERRORS:
            raise
        except EXTERNAL_DEPENDENCY_FAILURES as exc:
            logger.warning("lucas_caso_aberto_leitura_falhou", error_type=type(exc).__name__)
            return False
        variaveis = getattr(status, "variables", None)
        return isinstance(variaveis, Mapping) and variaveis.get("source_agent_id") == AGENT_ID_LUCAS

    def _estado_da_chave_v4(self, state: LucasState) -> LucasState:
        """Com `MAEZO_LUCAS_V4` desligada, o detalhamento do pagador nunca chega a um texto (a composicao do
        boleto, FASE 4): o estado dos textos segue sem ele — byte a byte o de antes, mesmo plantado."""
        if self._v4 or "detalhamento_pagador" not in state:
            return state
        return cast(LucasState, {k: v for k, v in state.items() if k != "detalhamento_pagador"})

    def _nao_pagador_v4(self, state: LucasState) -> bool:
        """DL-0114: com a chave ligada, quem o Lucas NAO consegue afirmar que e' o pagador PF
        (`beneficiario_e_pagador` falso OU ausente: falha fechada) e a pergunta e' so' do pagador. Empresa e
        adesao seguem o texto proprio. Desligada: sempre `False` (o comportamento de antes)."""
        return self._v4 and pagador_nao_comprovado_v4(state) and pergunta_so_do_pagador_v4(state)

    async def assess(self, state: LucasState) -> dict[str, Any]:
        """Evaluate the deterministic DMN tables and decide the ROUTE (respond vs escalate).

        ADR-0012: the DMN decides; the LLM never does. J3 (escalation-intent) NEVER even reaches
        the billing DMN — `cancelamento`/contestation/signaled cancellation ALWAYS escalates (L0
        hard). A intencao `inadimplencia` NAO e' J3 desde DL-0082: ela passa pela DMN com os fatos
        de conciliacao, e so' a regra de atraso da DMN escala como `inadimplencia_detectada`.

        FAIL-SAFE FECHADO: DMN unavailable or a `roteamento` value outside its recognized
        allowlist ALWAYS escalates — NEVER a silent default, NEVER treated as "admissible" by
        omission. The raw non-conforming value is NEVER echoed into `motivo_humano`/`error` —
        only the class token `ambiguidade`/`dmn_indisponivel` (engine-variable hygiene).
        """
        if state.get("error"):
            return {}  # already routed by `receive`

        dmn_refs: dict[str, str] = {}

        if state.get("pedido_negociacao") is True and not titulo_de_terceiro(state):
            # Rodada 12: o Lucas nunca negocia nem parcela — o pedido abre o caso humano.
            return await self._assess_escalation(state, dmn_refs, motivo="pedido_negociacao")

        if pede_cancelamento(state):
            return await self._assess_cancelamento(state, dmn_refs)

        if _is_escalation_intent(state):
            return await self._assess_escalation(state, dmn_refs, motivo=_escalation_motivo(state))

        ciclos = _ciclos_sem_conciliacao(state)
        if ciclos is None:
            # Fato numerico ilegivel: nunca derruba o turno e nunca vira "sem atraso" por omissao.
            return await self._assess_escalation(state, dmn_refs, motivo="ambiguidade")
        if sem_dados_de_cobranca(state):
            # Rodada 12: sem fatos a resposta e' a constante honesta — falha da fonte nao e' caso humano.
            return {
                "admissibilidade": "RESPONDER",
                "route": "respond_member",
                "dmn_refs": dmn_refs,
                "resposta_sem_dados": True,
            }
        admis_in: dict[str, Any] = {
            "tipo_solicitacao": str(state.get("tipo_solicitacao", "")),
            "status_conciliado": bool(state.get("status_conciliado", False)),
            "ciclos_sem_conciliacao": ciclos,
            # DL-0086: so' a linha `consulta_valores` le' esta entrada; sem fato de valor ela nao
            # casa e o catch-all escala. Apurada dos fatos presentes, nunca por modelo.
            "valores_disponiveis": valores_disponiveis(state),
            # DL-0082 sobre DL-0086 (revisao do #709): competencia vencida ou `dias_atraso_max > 0`
            # com a pergunta de valor ESCALA (sem afirmar inadimplencia), mesmo com `ciclos=0`.
            "valores_com_atraso": valores_com_atraso(state),
            # 13/10/2026 (decisao do dono): contrato de EMPRESA. A primeira linha responde com o texto fixo
            # e a regra de atraso exige `false`: o atraso do titulo da empresa nunca escala o beneficiario.
            # 13/10/2026 (bateria 4): tambem a adesao cobrada pela administradora — o titulo e' dela.
            # DL-0114 (`MAEZO_LUCAS_V4`): o titulo do PF que NAO e' o pagador tambem e' de terceiro (o do
            # responsavel financeiro): o atraso dele nunca escala quem nao e' o pagador.
            "pagador_empresa": titulo_de_terceiro(state) or self._nao_pagador_v4(state),
            # Rodada 12 (decisao do dono, 14/10/2026): so' o atraso CRITICO (>= 2 meses ou > 30 dias, PF;
            # configuravel) abre caso. Um mes sem pagamento e' resposta com fatos e a oferta de uma pessoa.
            "atraso_critico": atraso_critico(state, self._limiares_atraso)
            and not self._nao_pagador_v4(state),
        }
        try:
            rows, version = await self._dmn.evaluate(DMN_BILLING_ADMISSIBILITY, admis_in)
            row = first_row(rows, DMN_BILLING_ADMISSIBILITY, admis_in)
        except (DmnEvaluationError, DmnNoResultError) as exc:
            return await self._assess_escalation(
                state,
                dmn_refs,
                motivo="dmn_indisponivel",
                dmn_error=dmn_unavailable_error(DMN_BILLING_ADMISSIBILITY, exc),
            )
        dmn_refs[DMN_BILLING_ADMISSIBILITY] = f"{DMN_BILLING_ADMISSIBILITY}#{version.id}"

        roteamento = str(row.get("roteamento", ""))
        if roteamento not in _ADMISSIBILIDADE_ALLOW:
            # FAIL-SAFE FECHADO: an unrecognized value NEVER "passes through" — and is NEVER
            # echoed anywhere (class token only, engine-variable hygiene).
            return await self._assess_escalation(state, dmn_refs, motivo="ambiguidade")

        admissibilidade = cast(AdmissibilidadeCobranca, roteamento)
        if admissibilidade == "ESCALAR_HUMANO":
            # A tabela diz POR QUE escala (`categoria`): so' a regra de atraso vira inadimplencia. Sem
            # a coluna (tabela antiga no motor) ou com valor desconhecido o caso e' `ambiguidade` —
            # nunca se acusa de inadimplencia sem indicio (bateria do Lucas, L07/L09/L18).
            categoria = str(row.get("categoria", ""))
            motivo: MotivoHumano = (
                "inadimplencia_detectada" if categoria == "inadimplencia" else "ambiguidade"
            )
            return await self._assess_escalation(state, dmn_refs, motivo=motivo)

        return {
            "admissibilidade": admissibilidade,
            "route": "respond_member",
            "dmn_refs": dmn_refs,
        }

    async def _assess_cancelamento(self, state: LucasState, dmn_refs: dict[str, str]) -> dict[str, Any]:
        """CANCELAMENTO (rodada 12, 14/10/2026; contrato SP-OP-CANCEL-001, direito do titular — L2 clerical).

        - Titulo de TERCEIRO (empresa contratante / administradora da adesao): o beneficiario nao cancela o
          contrato de outro. Texto fixo, SEM caso e SEM processo — com a oferta de uma pessoa.
        - Pagador PF: confirma a intencao UMA vez ("Voce quer pedir o cancelamento do seu plano?"). So' com a
          pergunta pendente (a referencia do ultimo texto enviado) e a confirmacao do beneficiario o Lucas
          inicia SP-OP-CANCEL-001 — que decide numa User Task humana; ele NUNCA diz que o plano foi cancelado.
        - Pagador desconhecido (fonte fora): nao ha' como saber de quem e' o titulo — o caso humano de antes.

        DL-0111 (`MAEZO_CANCEL_PF_V1` ligada): o tipo de contratacao da AMH tambem decide empresa/adesao, um
        DEPENDENTE ouve que o pedido e' do titular (sem processo), e o plano individual/familiar segundo a AMH
        conta como PF mesmo sem o pagador. Desligada, nada disto roda.
        """
        v1 = self._cancel_pf_v1
        if (
            v1
            and state.get("resposta_anterior_ref") == ref_de_resposta(TEXTO_CONFIRMA_CANCELAMENTO)
            and state.get("confirmacao_cancelamento") == "nega"
        ):
            # Recuo com a pergunta pendente: ANTES de qualquer leitura de fatos. Nenhum processo, nenhum
            # caso — so' o texto que diz que nada foi registrado (a pendencia some: a memoria passa a ser
            # a deste texto).
            return self._responder_cancelamento("desistiu", dmn_refs)
        tipo_plano = state.get("cancelamento_tipo_plano") if v1 else None
        if cobranca_administradora(state) or tipo_plano == "coletivo_adesao":
            return self._responder_cancelamento("administradora", dmn_refs)
        if pagador_empresa(state) or tipo_plano == "coletivo_empresarial":
            return self._responder_cancelamento("empresa", dmn_refs)
        if v1 and (state.get("cancelamento_titular") is False or dependente_do_titular(state)):
            return self._responder_cancelamento("dependente", dmn_refs)
        pessoa_fisica = state.get("pagador_tipo") == "pessoa_fisica" or tipo_plano in TIPOS_PLANO_PF
        if not pessoa_fisica:
            return await self._assess_escalation(state, dmn_refs, motivo="pedido_cancelamento")
        pendente = state.get("resposta_anterior_ref") == ref_de_resposta(TEXTO_CONFIRMA_CANCELAMENTO)
        confirmacao = state.get("confirmacao_cancelamento")
        if pendente and confirmacao == "confirma":
            escalado = await self._assess_escalation(state, dmn_refs, motivo="pedido_cancelamento")
            return {**escalado, "iniciar_cancelamento": True}
        if pendente and confirmacao == "nega":
            return self._responder_cancelamento("desistiu", dmn_refs)
        return self._responder_cancelamento("confirmar", dmn_refs)

    @staticmethod
    def _responder_cancelamento(resposta: str, dmn_refs: dict[str, str]) -> dict[str, Any]:
        return {
            "admissibilidade": "RESPONDER",
            "route": "respond_member",
            "dmn_refs": dmn_refs,
            "cancelamento_resposta": resposta,
        }

    async def _assess_escalation(
        self,
        state: LucasState,
        dmn_refs: dict[str, str],
        *,
        motivo: MotivoHumano,
        dmn_error: str | None = None,
    ) -> dict[str, Any]:
        """Resolve the escalation's suggested human group (`lucas_escalation_routing`).

        The destination is ALWAYS `escalate_human` regardless of this DMN's outcome — it only
        picks the SUGGESTED group. A DMN failure or an out-of-allowlist value NEVER loses the
        case: the conservative catch-all group `atendimento-humano` is used, and the case still
        escalates (fail-safe fechado — the case is never dropped)."""
        grupo = "atendimento-humano"  # conservative catch-all, always available
        roteamento_escalacao: RoteamentoEscalacao | None = None
        esc_in: dict[str, Any] = {
            "intencao": str(state.get("intencao", "")),
            "motivo": motivo,
            "ciclos_sem_conciliacao": _ciclos_sem_conciliacao(state) or 0,
        }
        try:
            rows, version = await self._dmn.evaluate(DMN_ESCALATION_ROUTING, esc_in)
            row = first_row(rows, DMN_ESCALATION_ROUTING, esc_in)
            dmn_refs[DMN_ESCALATION_ROUTING] = f"{DMN_ESCALATION_ROUTING}#{version.id}"
            rot_raw = str(row.get("roteamento", ""))
            if rot_raw in _ROTEAMENTO_ESCALACAO_ALLOW:
                roteamento_escalacao = cast(RoteamentoEscalacao, rot_raw)
                grupo = _ESCALACAO_GRUPO[roteamento_escalacao]
        except (DmnEvaluationError, DmnNoResultError):
            pass  # fail-safe: keep the conservative catch-all — the case is NEVER lost

        out: dict[str, Any] = {
            "route": "escalate_human",
            "motivo_humano": motivo,
            "motivo_categoria": _motivo_categoria(motivo),
            "severidade": _severidade_humano(motivo),
            "grupo_humano": grupo,
            "dmn_refs": dmn_refs,
        }
        if roteamento_escalacao is not None:
            out["roteamento_escalacao"] = roteamento_escalacao
        if dmn_error is not None:
            out["dmn_error"] = dmn_error
        return out

    async def respond_member(self, state: LucasState) -> dict[str, Any]:
        """J1/J2: draft + send the informational/reminder message. NEVER threatens suspension or
        cancellation, NEVER communicates a denial (structural guardrail in `_build_message`).

        LUC-08 / §Delta F1: o envio carrega `_idempotency_key(state, node="respond_member")` —
        conversa + no + TURNO (ver `_turn_fingerprint`). Uma redelivery do MESMO turno reclama a
        MESMA chave; um turno DIFERENTE da mesma conversa reclama outra, entao a segunda mensagem
        genuina nao e' suprimida como se fosse repeticao da primeira.

        §Delta F1b: `mensagem_enviada`/`desfecho` saem do RETORNO do sender, nunca da ausencia de
        excecao. Um envio SUPRIMIDO pelo guard duravel nao e' entrega: ele grava
        `mensagem_enviada=False` e o desfecho proprio `envio_suprimido_duplicata`, nunca um token
        terminado em "enviada".
        """
        state = self._estado_da_chave_v4(state)
        mensagem = await self._build_message(state)
        avisos = _avisos_do_turno(state)
        memoria_ref = ""
        if not mensagem.get("recusa_de_saida") and isinstance(mensagem.get("texto"), str):
            texto = mensagem["texto"]
            if state.get("caso_aberto_cobranca") is True and aviso_permitido(state):
                # 13/10/2026 (simulador): com o caso de cobranca ja' com a equipe, a resposta reconhece
                # isso — no maximo `AVISOS_DE_CASO_ABERTO_MAX` vezes por caso (rodada 3).
                texto = f"{texto} {aviso_de_caso_aberto(state)}"
                avisos += 1
            if "sem pagamento confirmado" in texto and "nossa equipe" not in texto:
                # Rodada 12: um mes sem pagamento que NAO abre caso — os fatos e a oferta de uma pessoa.
                texto = com_oferta_de_pessoa(texto)
            if mensagem.get("memoria_ref"):
                # A pergunta de confirmacao do cancelamento: sai sempre inteira e deixa a sua referencia.
                memoria_ref = str(mensagem["memoria_ref"])
            elif mensagem.get("nao_pagador_ref"):
                # FASE 4: o nao pagador (v4) ja' saiu na forma da vez (`resposta_do_nao_pagador`).
                memoria_ref = str(mensagem["nao_pagador_ref"])
            elif mensagem.get("prompt_version") == PAGADOR_EMPRESA_TEXTO_VERSION and mensagem.get(
                "empresa_chave"
            ):
                texto, memoria_ref = self._escada_da_empresa(state, mensagem, texto)
            else:
                texto, memoria_ref = sem_repetir(state, texto, self._forma_curta(state, mensagem))
            mensagem["texto"] = texto
        entrega = ENTREGA_NAO_ENVIADA
        to_hash = state.get("to_hash")
        if to_hash and state.get("canal", "whatsapp") == "whatsapp":
            try:
                resultado = await self._whatsapp.send(
                    to_hash,
                    str(mensagem.get("texto", "")),
                    idempotency_key=_idempotency_key(state, node="respond_member"),
                )
                entrega = _entrega_de(resultado)
            except PROGRAMMING_ERRORS:
                raise
            except Exception as exc:
                mensagem["envio_nota"] = f"whatsapp send failed: {type(exc).__name__}"

        if entrega == ENTREGA_SUPRIMIDA_DUPLICATA:
            # Registrado na propria mensagem pelo mesmo motivo que `envio_nota` existe: o que
            # ficou por entregar NESTE turno tem de estar legivel, nao inferido do silencio.
            mensagem["envio_nota"] = "whatsapp send suppressed: duplicate idempotency key"
            desfecho = DESFECHO_ENVIO_SUPRIMIDO_DUPLICATA
        else:
            # RESIDUAL PRE-EXISTENTE, DECLARADO e NAO alterado aqui (fora do escopo do §Delta):
            # no caminho `ENTREGA_NAO_ENVIADA` (excecao no envio, `to_hash` ausente ou canal nao
            # WhatsApp) o desfecho segue sendo o token informativo, ainda que
            # `mensagem_enviada=False` e `envio_nota` ja contem a verdade da entrega.
            desfecho = (
                # CERCA DE SAIDA (18/09/2026): o rascunho foi barrado e quem foi entregue foi a
                # constante segura, nao a resposta que os fatos pediam. Chamar isso de
                # "resposta_informativa_enviada" afirmaria um atendimento que nao aconteceu.
                #
                # A SUPRESSAO POR DUPLICATA continua ganhando deste token, no ramo acima, e o
                # motivo e' que a recusa nao depende do desfecho para ser vista: ela tem contador
                # proprio (`record_resposta_recusada`, emitido em `_cercar_saida`), que conta a
                # recusa mesmo quando a entrega deste turno foi suprimida. Ja' a supressao so'
                # existe aqui.
                DESFECHO_RESPOSTA_RECUSADA
                if mensagem.get("recusa_de_saida")
                else "lembrete_enviado"
                if state.get("admissibilidade") == "LEMBRETE"
                else "resposta_informativa_enviada"
            )
        return {
            "mensagem": mensagem,
            "mensagem_enviada": entrega == ENTREGA_ENVIADA,
            "desfecho": desfecho,
            "avisos_de_caso_aberto": avisos,
            "resposta_memoria_ref": memoria_ref,
        }

    async def escalate_human(self, state: LucasState) -> dict[str, Any]:
        """J3 / fail-safe: build the dossier for the human handoff and acknowledge the
        beneficiary. NEITHER communicates the adverse decision — that is exclusively the human's
        (`_build_dossier`'s `decisao_cancelamento` is always `None`).

        R1 CYCLE-1 F1a FIX: this node now stamps `route="escalate_human"` AUTHORITATIVELY in its
        own output. Pre-fix it relied on `assess` having stamped it — but `_route`'s conservative
        default also sends route-UNSET states here (e.g. a turn whose assess short-circuited),
        and `start_process`'s old gate then read the missing stamp as "not an escalation" and
        silently skipped the start AFTER this node had already told the beneficiary a human
        would continue (fail-OPEN case loss, verifier-proven). The node that performs the human
        handoff is the authority on the fact that a handoff is happening.

        Defensive companion: reaching this node without a `motivo_humano` is a technical
        anomaly — it is stamped `falha_tecnica` (class token; never left empty/unset in
        engine-bound fields).
        """
        defaults: dict[str, Any] = {}
        if not state.get("motivo_humano"):
            defaults = {
                "motivo_humano": "falha_tecnica",
                "motivo_categoria": "falha_tecnica",
                "severidade": _severidade_humano("falha_tecnica"),
                "grupo_humano": "atendimento-humano",
            }
            state = cast(LucasState, {**state, **defaults})

        dossier = await self._build_dossier(state)

        # LUC-05 (ORDEM): este no NAO fala mais com o beneficiario. Ate 2026-09-04 o ACK ("um
        # atendente humano vai continuar") saia DAQUI, ANTES de `start_process` — e quando o
        # start falhava, o `except CibSevenError` so gravava `process_started=False`, o desfecho
        # seguia `escalado_humano` e o turno terminava calado: o beneficiario informado de que um
        # humano assumiria, e ZERO instancias de SP-OP-ESCALATION-001 existindo. O envio migrou
        # para `send_escalation_ack`, alcancavel SO pelo ramo de sucesso da aresta condicional
        # que sai de `start_process`. `ack_pending` registra a divida ate la.
        return {
            **defaults,
            "route": "escalate_human",  # authoritative stamp (F1a) — never inferred downstream
            "dossier": dossier,
            "mensagem_enviada": False,
            "ack_pending": True,
            "desfecho": "escalado_humano",
        }

    async def start_process(self, state: LucasState) -> dict[str, Any]:
        """Start SP-OP-ESCALATION-001 idempotently. The informational route is the ONLY one
        that never opens a process.

        R1 CYCLE-1 F1b FIX (fail-CLOSED gate): pre-fix this node gated on
        `route != "escalate_human"` -> skip — so a state that reached it WITHOUT the stamp
        (assess short-circuited on a planted `error`; `_route`'s conservative default still ran
        `escalate_human`, which built the dossier and SENT the beneficiary the "a human will
        continue" ack) silently returned `process_started=False`: the beneficiary was promised a
        human while ZERO engine instances existed (fail-OPEN case loss, verifier-proven). The
        gate is now inverted: ONLY an explicit `route == "respond_member"` skips the start;
        `escalate_human` (now also stamped authoritatively by the escalate node itself, F1a) or
        ANY unset/unknown route starts the escalation — mirroring `_route`'s own conservative
        default (on doubt, the human path), never a silent skip.
        """
        if state.get("route") == "respond_member":
            return {"process_started": False}
        if state.get("iniciar_cancelamento") is True:
            return await self._start_cancelamento(state)

        business_key = state.get("business_key") or _business_key(state)
        variables = self._escalation_variables(state)
        provenance = AgentDecisionProvenance(
            agent_id="lucas",
            agent_version=self._agent_version,
            tenant_id=state.get("tenant_id", ""),
            model_id=self._model_id,
            prompt_version=SYSTEM_PROMPT_VERSION,
            decision_basis={
                "route": state.get("route", ""),
                "desfecho": state.get("desfecho", ""),
            },
        )
        try:
            instance = await start_process_idempotent(
                self._cibseven,
                process_key=PROCESS_KEY,
                business_key=business_key,
                variables=variables,
                audit_sink=self._audit_sink,
                provenance=provenance,
            )
        except CibSevenError as exc:
            # CC-01: `start_failed_state` devolve as MESMAS tres chaves de antes mais o marcador
            # `start_failed`, que e o que `route_after_start` le para desviar a
            # `notify_start_failure` em vez de seguir calado para o terminal.
            return start_failed_state(business_key=business_key, error=start_unavailable_error(exc))
        return {
            "process_started": True,
            "business_key": business_key,
            "process_ref": {
                "instance_id": instance.instance_id,
                "state": instance.state,
                "already_existed": instance.already_existed,
            },
        }

    def _variaveis_do_cancelamento_v1(self, state: LucasState, variables: dict[str, Any]) -> dict[str, Any]:
        """DL-0111: os fatos REAIS apurados da AMH e as chaves do aviso ao beneficiario.

        - `tipo_plano`: o da AMH quando PF (`individual`/`familiar`); senao `""` (nao afirmado -> a DMN cai
          no catch-all `ANALISE_HUMANA`, nunca em EFETIVAR_PEDIDO por omissao).
        - `titularidade_confirmada`: titular segundo a AMH E identidade verificada no acesso (DL-0083).
        - `vinculo_ativo`: vinculo sem data de cancelamento e situacao apta/ativa segundo a AMH.
        - `dentro_prazo=True`: RN 412 — o pedido de cancelamento pode ser feito a qualquer tempo. A confirmar
          pelo juridico (a DMN continua exigindo o fato; o contrato o registra como DRAFT/verify).
        - `notificacao_previa_feita=False`: nao se aplica ao pedido do beneficiario (so' a inadimplencia).
        - `aviso_beneficiario_whatsapp`/`agent_id`/`conversation_id`: ligam os avisos do BPMN v0.2.0, que a
          retomada (`agent_resume`) entrega nesta conversa.
        """
        tipo_plano = state.get("cancelamento_tipo_plano")
        return {
            **variables,
            "tipo_plano": tipo_plano if tipo_plano in TIPOS_PLANO_PF else "",
            "titularidade_confirmada": state.get("cancelamento_titular") is True
            and state.get("cancelamento_identidade_verificada") is True,
            "vinculo_ativo": state.get("cancelamento_vinculo_ativo") is True,
            # RN 412: pedido a qualquer tempo — a confirmar pelo juridico.
            "dentro_prazo": True,
            "notificacao_previa_feita": False,
            "aviso_beneficiario_whatsapp": True,
            "agent_id": AGENT_ID_LUCAS,
            "conversation_id": str(state.get("conversation_id", "")),
        }

    async def _start_cancelamento(self, state: LucasState) -> dict[str, Any]:
        """Inicia SP-OP-CANCEL-001 (pedido do beneficiario, confirmado) pelo start idempotente e auditado.

        Chave `CANCEL-{tenant}-{identidade}` pelo minter compartilhado (`base.mint_contract_business_key`): o
        Lucas nao tem `numero_contrato`, entao ancora na identidade pseudonima (o fallback de matricula do
        contrato — nunca o numero cru). Os fatos que o contrato chama de "pre-resolvidos por worker"
        (`dentro_prazo`, `titularidade_confirmada`, `vinculo_ativo`, `notificacao_previa_feita`) o Lucas NAO
        apura: seguem `false` (nao estabelecidos), o que leva a DMN `cancel_admissibility` ao catch-all
        `ANALISE_HUMANA` — nunca a `EFETIVAR_PEDIDO` por obra do agente. `tipo_plano` e' o do pagador PF
        (`individual`), a confirmar pela analise humana."""
        tenant = str(state.get("tenant_id", ""))
        pseudo = str(state.get("beneficiario_pseudo_id", ""))
        business_key = mint_contract_business_key(
            CANCEL_KEY_FAMILY,
            tenant,
            numero_contrato="",
            matricula_beneficiario=pseudo,
            beneficiario_pseudo_id=pseudo,
        )
        variables: dict[str, Any] = {
            "tenant_id": tenant,
            "source_agent_id": AGENT_ID_LUCAS,
            "source_agent_version": self._agent_version,
            "numero_contrato": "",
            "matricula_beneficiario": pseudo,
            "beneficiario_pseudo_id": pseudo,
            "tipo_solicitacao": "pedido_beneficiario",
            "origem_solicitacao": "agente_lucas",
            "tipo_plano": "individual",
            "dentro_prazo": False,
            "notificacao_previa_feita": False,
            "titularidade_confirmada": False,
            "vinculo_ativo": False,
            "data_solicitacao_iso": self._hoje().isoformat(),
            "documentos_refs": [],
        }
        if self._cancel_pf_v1:
            variables = self._variaveis_do_cancelamento_v1(state, variables)
        provenance = AgentDecisionProvenance(
            agent_id="lucas",
            agent_version=self._agent_version,
            tenant_id=tenant,
            model_id=self._model_id,
            prompt_version=SYSTEM_PROMPT_VERSION,
            decision_basis={
                "route": state.get("route", ""),
                "desfecho": state.get("desfecho", ""),
                "origem_solicitacao": "agente_lucas",
            },
        )
        # DL-0111 (com a chave): so' a indisponibilidade do MOTOR vira "falhou ao iniciar". O portao de dedup
        # nao avaliavel (`StartDedupGateUnavailableError`) e' defeito do controle e PROPAGA, alto, como
        # no escalonamento da Helena — nunca vira "uma falha tecnica a mais" (defeito conhecido da rodada 12).
        falhas_de_start: tuple[type[Exception], ...] = (
            (CibSevenError,) if self._cancel_pf_v1 else (CibSevenError, StartDedupGateUnavailableError)
        )
        try:
            instance = await start_process_idempotent(
                self._cibseven,
                process_key=CANCEL_PROCESS_KEY,
                business_key=business_key,
                variables=variables,
                audit_sink=self._audit_sink,
                provenance=provenance,
            )
        except falhas_de_start as exc:
            return start_failed_state(business_key=business_key, error=start_unavailable_error(exc))
        return {
            "process_started": True,
            "business_key": business_key,
            "process_ref": {
                "instance_id": instance.instance_id,
                "state": instance.state,
                "already_existed": instance.already_existed
                or instance.start_outcome is StartOutcome.ALREADY_ACTIVE,
            },
        }

    async def send_escalation_ack(self, state: LucasState) -> dict[str, Any]:
        """LUC-05: o ACK ao beneficiario — SO depois que a escalacao existe de verdade.

        GUARDA: so envia quando a rota e `escalate_human` E o start reportou uma instancia viva
        (`process_started`, que em Lucas cobre STARTED e ALREADY_ACTIVE). A jornada informativa
        (`respond_member`), que nunca abre processo, passa por aqui como no-op — ela ja respondeu
        no seu proprio no.

        O envio continua best-effort (uma falha de WhatsApp nao pode desfazer uma escalacao que
        JA existe no engine), mas agora `mensagem_enviada` conta a verdade e `ack_pending`
        registra o que ficou por entregar.

        LUC-08 / §Delta F1: mesma chave de `respond_member` (`_idempotency_key`), aqui com
        `node="send_escalation_ack"` — conversa + no + TURNO. O componente por no mantem o ACK e
        o lembrete informativo do mesmo beneficiario em chaves distintas; o componente por turno
        impede que uma SEGUNDA escalacao da mesma conversa (caminho NON_STRICT, "escala de novo
        por design") seja suprimida como duplicata da primeira.

        §Delta F1b: um envio SUPRIMIDO pelo guard duravel NAO e' entrega deste turno — o retorno
        `{"suppressed_duplicate": True}` prova que a chave ja estava reclamada, nao que a Cloud
        API aceitou a mensagem (uma reclamacao em voo de um envio concorrente ainda nao concluido
        suprime igual, e uma reclamacao NAO SELADA depois de um 2xx tambem). `ack_pending`
        e' o registro conservador de "prometemos nada a ninguem ainda", entao ele CONTINUA True,
        exatamente como no caminho de falha logo acima.
        """
        if state.get("route") != "escalate_human" or state.get("process_started") is not True:
            return {}
        state = self._estado_da_chave_v4(state)
        to_hash = state.get("to_hash")
        if not to_hash or state.get("canal", "whatsapp") != "whatsapp":
            return {"ack_pending": True}

        avisos = _avisos_do_turno(state)
        if state.get("iniciar_cancelamento") is True and self._cancel_pf_v1:
            # DL-0111: o recebimento com o PROTOCOLO (derivado do id da instancia, sem PII). Pedido repetido
            # reencontra a instancia viva: o mesmo protocolo. Nunca "cancelado".
            ack_text = self._texto_de_cancelamento_recebido(state)
        elif state.get("iniciar_cancelamento") is True:
            # Rodada 12: o pedido de cancelamento entrou em SP-OP-CANCEL-001 — o que acontece a seguir, nunca
            # "cancelado". Pedido repetido (instancia ja' viva): so' que ja' esta' com a equipe.
            ack_text = (
                TEXTO_CANCELAMENTO_JA_REGISTRADO
                if (state.get("process_ref") or {}).get("already_existed") is True
                else TEXTO_CANCELAMENTO_ENCAMINHADO
            )
        elif (state.get("process_ref") or {}).get("already_existed") is True or state.get(
            "caso_aberto_cobranca"
        ) is True:
            # 13/10/2026 (simulador): caso JA' com a equipe — o fato que responde a pergunta e o aviso na
            # variante deste turno, nunca o mesmo ACK repetido a cada mensagem; depois de dois avisos,
            # so' o fato (rodada 3).
            com_aviso = aviso_permitido(state)
            ack_text = self._texto_de_caso_aberto(state, com_aviso=com_aviso)
            avisos += 1 if com_aviso else 0
        else:
            ack_text = self._build_escalation_ack(state)
        ack_text, memoria_ref = sem_repetir(state, ack_text)
        try:
            resultado = await self._whatsapp.send(
                to_hash, ack_text, idempotency_key=_idempotency_key(state, node="send_escalation_ack")
            )
        except PROGRAMMING_ERRORS:
            raise
        except Exception:
            return {"mensagem_enviada": False, "ack_pending": True}
        if _entrega_de(resultado) == ENTREGA_SUPRIMIDA_DUPLICATA:
            return {"mensagem_enviada": False, "ack_pending": True}
        return {
            "mensagem_enviada": True,
            "ack_pending": False,
            "desfecho": "escalado_humano",
            "avisos_de_caso_aberto": avisos,
            "resposta_memoria_ref": memoria_ref,
        }

    @staticmethod
    def _texto_de_cancelamento_recebido(state: LucasState) -> str:
        ref = state.get("process_ref") or {}
        instance_id = ref.get("instance_id")
        if not isinstance(instance_id, str) or not instance_id:
            # Sem id nao ha' protocolo: o texto de antes (honesto), nunca um protocolo inventado.
            return (
                TEXTO_CANCELAMENTO_JA_REGISTRADO
                if ref.get("already_existed") is True
                else TEXTO_CANCELAMENTO_ENCAMINHADO
            )
        protocolo = protocolo_do_cancelamento(instance_id)
        modelo = TEXTO_PEDIDO_JA_REGISTRADO if ref.get("already_existed") is True else TEXTO_PEDIDO_RECEBIDO
        return modelo.format(protocolo=protocolo)

    async def notify_start_failure(self, state: LucasState) -> dict[str, Any]:
        """CC-01: o start FALHOU — grava o desfecho de erro e ALERTA, em vez de seguir calado.

        Ate CC-01 a aresta que saia de `start_process` era INCONDICIONAL: o turno chegava ao
        terminal com o `desfecho` de SUCESSO que um no a montante ja havia gravado, afirmando um
        fato que nao aconteceu, e sem prazo nenhum — o timer de SLA vive na instancia BPMN que
        nunca nasceu. O corpo deste no e o helper compartilhado
        (`maezo.runtime.start_outcome.notify_start_failure`): uma definicao para os 9 agentes,
        nunca 9 copias.

        LUC-05: `ack_pending`/`retryable_error` viajam junto porque o ACK ao
        beneficiario NAO foi enviado (ele migrou para `send_escalation_ack`, depois do
        start) e porque o replay e seguro — `start_process_idempotent` e idempotente por
        business key, entao uma retentativa reencontra a instancia viva em vez de abrir
        uma segunda.
        """
        # DL-0111 (com a chave): o aviso cita o processo que de fato falhou (antes, sempre o ESCALATION).
        process_key = (
            CANCEL_PROCESS_KEY
            if self._cancel_pf_v1 and state.get("iniciar_cancelamento") is True
            else PROCESS_KEY
        )
        return emit_start_failure_notice(
            dict(state),
            agent_id="lucas",
            process_key=process_key,
            extra={"mensagem_enviada": False, "ack_pending": True, "retryable_error": True},
        )

    async def complete(self, state: LucasState) -> dict[str, Any]:
        """Terminal node — no further computation; `desfecho` was already set upstream.

        CC-09: emits ONE `maezo_agent_desfecho_total` for this turn (`route`/`motivo_categoria`/
        `mensagem_enviada` were already set by `respond_member`/`escalate_human`/
        `send_escalation_ack` and survive in the merged `state` this node receives).
        """
        emit_turn_desfecho(state, agent_id="lucas")
        return {}

    # -- Conditional routing ------------------------------------------------------------------

    @staticmethod
    def _route(state: LucasState) -> str:
        # FAIL-SAFE: on absence/doubt, ALWAYS the human (never respond_member by omission). No
        # branch of this function can return an adverse destination — `Route` does not admit one.
        return "respond_member" if state.get("route") == "respond_member" else "escalate_human"

    # -- Fail-closed minimal escalation (used by `receive`, before any DMN runs) --------------

    @staticmethod
    def _escalate_min(motivo: MotivoHumano, *, error: str) -> dict[str, Any]:
        return {
            "route": "escalate_human",
            "motivo_humano": motivo,
            "motivo_categoria": _motivo_categoria(motivo),
            "severidade": _severidade_humano(motivo),
            "grupo_humano": "atendimento-humano",
            # R1 cycle-1 F2: explicitly empty — no DMN ran on this shortcut, so nothing (least
            # of all a caller-planted value) may pose as a DMN provenance ref in engine
            # variables. Redundant with `receive`'s blanket reset by design (defense in depth).
            "dmn_refs": {},
            "error": error,
        }

    # -- Contract variables + drafting -------------------------------------------------------

    def _escalation_variables(self, state: LucasState) -> dict[str, Any]:
        """Assembles SP-OP-ESCALATION-001's input variables per
        `docs/processes/contracts/SP-OP-ESCALATION-001.md` (the SAME shared contract Helena
        starts) plus Lucas-specific audit annotations (mirrors Rafael's `dossie_rafael`/
        `rafael_route` additive style) — never a decision, only an instruction for the human."""
        dossier = state.get("dossier") or {}
        resumo = str(dossier.get("narrativa", "")) or (
            f"Encaminhamento automatico ({state.get('motivo_humano') or _MOTIVO_HUMANO_NAO_RESOLVIDO})."
        )
        # LUCAS-MOTIVO-SEVERIDADE-DEFAULTS: NO `or`-fallback on `motivo_categoria`/`severidade`
        # — both are Literal-typed contract fields (`:221,:226` domains), and inventing a
        # well-formed-looking class token (a generic category or a mid severity) for a value this graph never
        # actually determined is the SAME class of bug GAP-ESC-SEVERITY-GROUP fixed one layer
        # down, in the worker (`escalation.py::_exigir_severidade`/`_rotulo_opcional_validado`):
        # a fabricated label reads as authoritative and is indistinguishable from a real one.
        # After `receive`'s R1 cycle-1 F2 reset these keys are PRESENT but `""` until `assess`/
        # `escalate_human`/`_escalate_min` write a real value (every real path does); an
        # UNRESOLVED `""` now rides through verbatim — `severidade` (contract-mandatory) refuses
        # fail-closed at the ALREADY-fixed worker boundary (never silently a domain value), and
        # `motivo_categoria` (optional, ESC-D1-MOTIVO-STRICTER-THAN-R7) is simply treated as
        # absent there — never silently a catch-all category. Every other field below keeps its plain
        # `.get(k, "")`: those are free-text/administrative, not closed-domain contract tokens.
        variables: dict[str, Any] = {
            "tenant_id": state.get("tenant_id", ""),
            "source_agent_id": AGENT_ID_LUCAS,
            "source_agent_version": self._agent_version,
            "conversation_id": state.get("conversation_id", ""),
            "beneficiario_pseudo_id": state.get("beneficiario_pseudo_id", ""),
            "canal": state.get("canal", "whatsapp"),
            "motivo_categoria": state.get("motivo_categoria", ""),
            "severidade": state.get("severidade", ""),
            "resumo_contexto": resumo,
        }
        dmn_refs = state.get("dmn_refs")
        if dmn_refs:
            variables["dmn_decision_ref"] = next(iter(dmn_refs.values()), "")
            variables["dmn_decision_refs"] = dmn_refs
        # Lucas-specific audit annotations — additive, never a decision (module docstring).
        variables["lucas_route"] = state.get("route") or "escalate_human"
        variables["motivo_encaminhamento"] = state.get("motivo_humano") or ""
        variables["grupo_humano_sugerido"] = state.get("grupo_humano") or "atendimento-humano"
        variables["dossie_lucas"] = dossier
        return variables

    def _cercar_saida(self, texto: str, response_kind: str, fatos: dict[str, Any] | None = None) -> str:
        """Devolve `texto`, ou LEVANTA `RespostaRecusadaError` se ele violar a cerca de saida.

        PONTO UNICO. Diferente da Helena, que tem um `_respond_llm` por onde passa todo texto, o
        Lucas redige em dois lugares — `_build_message` (jornada informativa) e
        `_build_escalation_ack` (o ACK depois da escalacao). Este metodo e' o ponto unico que os
        dois compartilham, e e' de proposito que a NARRATIVA DO DOSSIE nao passe por aqui: ela e'
        lida por um humano que precisa ver o desfecho adverso nomeado.

        POR QUE A CERCA EXISTE MESMO COM A PROIBICAO NO PROMPT: ver o cabecalho de `prompts.py`.
        Em resumo medido — a Helena tinha a mesma proibicao em maiusculas e o modelo passou por
        cima dela duas vezes na mesma conversa em 13/09/2026.
        """
        recusa = motivo_de_recusa(texto, response_kind, fatos)
        if recusa is None:
            return texto
        grupo, padrao = recusa
        # O PADRAO no log (onde alguem depura), o GRUPO no contador (onde alguem conta). O TEXTO
        # nao vai para nenhum dos dois: e' saida de modelo sobre a cobranca do beneficiario, e um
        # log nao e' lugar de ampliar o alcance dela.
        logger.error(
            "lucas_resposta_recusada",
            grupo=grupo,
            padrao=padrao,
            response_kind=response_kind,
            recusa_version=RECUSA_DE_SAIDA_VERSION,
        )
        record_resposta_recusada(agent_id="lucas", motivo=grupo, response_kind=response_kind)
        raise RespostaRecusadaError(grupo, padrao, response_kind)

    async def _build_message(self, state: LucasState) -> dict[str, Any]:
        """A mensagem informativa do turno. PEDIDO DE LISTA (12/10/2026) num assunto sem resposta em
        lista (2a via, vencimento, status de pagamento): "Consigo te mostrar um mes por vez." antes da
        resposta de um mes — nunca um mes so' em silencio para quem pediu varios. A pergunta de valor
        lista de verdade (`_fatos_de_valores`)."""
        mensagem = await self._build_message_de_um_mes(state)
        empresa = mensagem.get("prompt_version") == PAGADOR_EMPRESA_TEXTO_VERSION
        if (
            _lista_da_pergunta(state) is not None
            and not empresa
            and state.get("tipo_solicitacao") != TIPO_CONSULTA_VALORES
            and not mensagem.get("recusa_de_saida")
            and isinstance(mensagem.get("texto"), str)
        ):
            mensagem["texto"] = f"{UM_MES_POR_VEZ} {mensagem['texto']}"
        return mensagem

    async def _build_message_de_um_mes(self, state: LucasState) -> dict[str, Any]:
        """Drafts the informational/reminder message (J1/J2). The LLM only phrases already-known
        facts — it never decides `admissibilidade` (that is 100% DMN-derived, ADR-0012)."""
        facts: dict[str, Any] = {
            "tipo_solicitacao": state.get("tipo_solicitacao"),
            "competencia": state.get("competencia"),
            "numero_boleto": state.get("numero_boleto"),
            "status_conciliado": state.get("status_conciliado"),
            "admissibilidade": state.get("admissibilidade"),
            "dmn_refs": state.get("dmn_refs", {}),
        }
        if state.get("cancelamento_resposta"):
            return self._build_cancelamento(state, facts)
        if state.get("resposta_sem_dados") is True:
            return self._mensagem(TEXTO_FONTE_INDISPONIVEL, facts, VALORES_PROMPT_VERSION, state)
        foco_da_pergunta = _foco_da_pergunta(state)
        if foco_da_pergunta == FOCO_EXTRATO:
            mensagem = self._mensagem(TEXTO_EXTRATO, facts, VALORES_PROMPT_VERSION, state)
            mensagem["curta"] = TEXTO_EXTRATO_CURTO
            return mensagem
        if foco_da_pergunta == FOCO_PAGAMENTO_AO_PRESTADOR:
            return self._build_pagamento_ao_prestador(state, facts)
        if foco_da_pergunta == FOCO_COMO_PAGAR:
            mensagem = self._mensagem(TEXTO_COMO_PAGAR, facts, VALORES_PROMPT_VERSION, state)
            mensagem["curta"] = TEXTO_COMO_PAGAR_CURTO
            return mensagem
        if foco_da_pergunta == FOCO_LINK_SUSPEITO:
            mensagem = self._mensagem(TEXTO_LINK_SUSPEITO, facts, VALORES_PROMPT_VERSION, state)
            mensagem["curta"] = TEXTO_LINK_SUSPEITO_CURTO
            return mensagem
        if foco_da_pergunta == FOCO_CONSEQUENCIA_DO_ATRASO:
            mensagem = self._mensagem(TEXTO_CONSEQUENCIA_DO_ATRASO, facts, VALORES_PROMPT_VERSION, state)
            mensagem["curta"] = TEXTO_CONSEQUENCIA_DO_ATRASO_CURTO
            return mensagem
        if self._nao_pagador_v4(state) and state.get("admissibilidade") in ("RESPONDER", "LEMBRETE"):
            # DL-0114: quem nao e' o pagador nao recebe valor, vencimento, situacao nem 2a via: o responsavel
            # financeiro do contrato (SEM nome) e' quem pode pedir.
            if state.get("beneficiario_e_pagador") is False:
                # FASE 4: inteiro so' na primeira; depois as curtas se revezam, de qualquer pergunta.
                texto_nao_pagador, ref_nao_pagador = resposta_do_nao_pagador(state)
                mensagem = self._mensagem(texto_nao_pagador, facts, VALORES_PROMPT_VERSION, state)
                mensagem["nao_pagador_ref"] = ref_nao_pagador
            else:
                # Sem o sinal (fonte sem o billing-status 0.3.0, campo nulo): falha fechada, so' os canais.
                mensagem = self._mensagem(
                    TEXTO_PAGADOR_NAO_CONFIRMADO_V4, facts, VALORES_PROMPT_VERSION, state
                )
                mensagem["curta"] = TEXTO_PAGADOR_NAO_CONFIRMADO_V4_CURTO
            return mensagem
        if foco_da_pergunta == FOCO_JA_PAGUEI:
            facts["competencias"] = _competencias_dos_fatos(state)
            mensagem = self._mensagem(
                texto_ja_paguei(state, self._hoje()), facts, VALORES_PROMPT_VERSION, state
            )
            mensagem["curta"] = texto_ja_paguei_curto(state, self._hoje())
            return mensagem
        if cobranca_administradora(state) and state.get("admissibilidade") in ("RESPONDER", "LEMBRETE"):
            # 13/10/2026 (bateria 4): adesao. A parcela da pessoa e a administradora; nunca "a empresa paga".
            return self._build_cobranca_administradora(state)
        if (
            dependente_do_titular(state)
            and state.get("admissibilidade") in ("RESPONDER", "LEMBRETE")
            and state.get("tipo_solicitacao") in (*_TIPOS_DE_SEGUNDA_VIA, "vencimento")
        ):
            # Wave 2 (14/10/2026): dependente — o boleto e' do titular do plano; nenhum valor.
            return self._mensagem(TEXTO_BOLETO_DO_TITULAR, facts, VALORES_PROMPT_VERSION, state)
        if pagador_empresa(state) and state.get("admissibilidade") in ("RESPONDER", "LEMBRETE"):
            # 13/10/2026 (decisao do dono): contrato de EMPRESA. Antes de tudo, inclusive do "em dia", que
            # seria a situacao do titulo da empresa. Nunca o total, o boleto ou o atraso da empresa.
            return self._build_pagador_empresa(state)
        if not state.get("tipo_solicitacao") and state.get("admissibilidade") == "RESPONDER":
            # Rodada 12: pergunta de cobranca que o Lucas nao responde — nao e' caso humano.
            return self._mensagem(TEXTO_OUTRO_ASSUNTO, facts, VALORES_PROMPT_VERSION, state)
        foco = _foco_da_pergunta(state)
        if foco in (FOCO_ULTIMA, FOCO_ESCLARECIMENTO):
            # Rodada 3 (13/10/2026): "Essa e' a ultima? Nao e' novembro?" e "de qual ano?" tem resposta
            # PROPRIA, curta, dos fatos — nunca o texto anterior de novo.
            texto_curto = (
                texto_da_ultima(state, self._hoje())
                if foco == FOCO_ULTIMA
                else texto_de_esclarecimento(state, self._hoje())
            )
            if texto_curto is not None:
                facts["competencias"] = _competencias_dos_fatos(state)
                return self._mensagem(texto_curto, facts, VALORES_PROMPT_VERSION, state)
        if _mensalidade_em_dia(state):
            # DL-0082: com os fatos dizendo "conciliado, zero ciclos" a resposta e' FIXA — nenhum
            # modelo redige sobre a situacao financeira de alguem quando o fato ja' diz tudo. Rodada 3:
            # o texto cita meses, datas e boletos dos fatos — a cerca os confere contra eles.
            competencias_do_status = _competencias_dos_fatos(state)
            if competencias_do_status:
                facts["competencias"] = competencias_do_status
            return self._mensagem(
                texto_mensalidade_em_dia(state, self._hoje()), facts, MENSAGEM_EM_DIA_VERSION, state
            )
        if (
            state.get("admissibilidade") == "RESPONDER"
            and state.get("tipo_solicitacao") in _TIPOS_DE_SEGUNDA_VIA
        ):
            # 10/10/2026 (teste real do dono): boleto/2a via em TEXTO FIXO, com ou sem fatos.
            return self._build_segunda_via(state)
        if state.get("tipo_solicitacao") == TIPO_CONSULTA_VALORES:
            return await self._build_valores(state)
        if state.get("admissibilidade") == "LEMBRETE" and valores_disponiveis(state):
            # 09/10/2026 (teste real do dono): com os fatos por competencia, "quando vence?" tem resposta
            # FIXA montada deles — o rascunho do modelo respondeu "pagamento conciliado" a essa pergunta.
            return self._build_vencimento(state)
        if state.get("admissibilidade") == "LEMBRETE":
            # DL-0086: o lembrete recebe o vencimento REAL da competencia de referencia, quando a
            # fonte o trouxe. Sem ele a chave nem existe (o prompt fica o de antes).
            # Com atraso nos fatos (vencida, `dias_atraso_max > 0`) a data NAO vai: um vencimento no
            # passado num lembrete vira "venceu" — afirmacao de atraso que a DMN nao fez (DL-0082).
            vencimento = _vencimento_referencia(state)
            if vencimento is not None and not valores_com_atraso(state):
                facts["vencimento"] = vencimento
        # O modelo ve' o boleto na forma de apresentacao ("final 0037"): asterisco no rascunho vira negrito
        # no WhatsApp. A cerca continua recebendo o rotulo mascarado dos fatos (`facts`).
        fatos_do_prompt = dict(facts)
        if isinstance(facts.get("numero_boleto"), str):
            fatos_do_prompt["numero_boleto"] = _boleto_do_rascunho_sem_asterisco(facts["numero_boleto"])
        prompt = (
            f"{message_prompt()}\n\n"
            f"{render_fatos_para_prompt(fatos_do_prompt, booleanos=_FATOS_BOOLEANOS_MENSAGEM)}"
        )
        try:
            texto = await self._llm.generate(
                prompt,
                phi=True,
                agent_id="lucas",
                tenant_id=state.get("tenant_id", ""),
                # AF-12: phrasing facts the DMN already decided — `task_default` (ADR-0009 §2
                # "classificacao -> modelo rapido/barato").
                task_kind="task_default",
            )
        except PROGRAMMING_ERRORS:
            raise
        except EXTERNAL_DEPENDENCY_FAILURES:  # fail-safe default: never leave the beneficiary with nothing.
            # DL-0087: sem promessa de retorno que o sistema nao cumpre; a constante honesta, com os canais.
            texto = RESPOSTA_INFORMATIVA_RECUSADA
        # Defesa em profundidade da apresentacao: rotulo mascarado que o modelo ainda escreva vira
        # "final NNNN" ANTES da cerca, que confere os digitos com os fatos.
        texto = _boleto_do_rascunho_sem_asterisco(texto)
        return self._mensagem(texto, facts, MESSAGE_PROMPT_VERSION, state)

    def _fatos_de_valores(self, state: LucasState) -> dict[str, Any]:
        """Os fatos da resposta a `consulta_valores`: o que a fonte entregou, mais nada.

        A competencia pedida, quando veio nos fatos, e' a unica competencia mostrada; senao, as
        `COMPETENCIAS_NO_RASCUNHO` mais recentes (uma, desde 09/10/2026). `foco` (DL-0087) e' o campo
        perguntado; o convite "se quiser outro mes" saiu (responder SO' o que foi perguntado). Chave
        AUSENTE quando o fato nao veio."""
        competencias = _competencias_dos_fatos(state)
        pedida = state.get("competencia")
        da_pedida = [c for c in competencias if c["competencia"] == pedida]
        facts: dict[str, Any] = {
            "tipo_solicitacao": TIPO_CONSULTA_VALORES,
            "competencia_pedida": pedida or None,
            "mais_recente_dos_fatos": competencias[0]["competencia"] if competencias else None,
            "foco": _foco_da_pergunta(state),
            "numero_boleto": _rotulo_boleto(state),
            "status_conciliado": state.get("status_conciliado"),
        }
        valor_em_aberto = _valor_em_aberto(state)
        if valor_em_aberto is not None:
            facts["valor_em_aberto"] = valor_em_aberto
            # 13/10/2026: os meses que compoem o total, para a resposta de saldo dizer de onde ele vem.
            facts["meses_em_aberto"] = meses_em_aberto(competencias)
        # `dias_atraso_max` e `situacao=vencida` decidem na DMN (`valores_com_atraso`), nunca no texto.
        facts["competencias"] = da_pedida or competencias[:COMPETENCIAS_NO_RASCUNHO]
        lista = _lista_da_pergunta(state)
        if lista is not None:
            # 12/10/2026: pedido de LISTA. O mes pedido nao vale (o periodo e' o da lista); os fatos levam
            # SO' os meses listados — ou, sem nenhum, o mais recente, que a resposta oferece no lugar.
            selecionadas = selecionar_competencias_da_lista(competencias, lista, facts["foco"])
            facts["competencia_pedida"] = None
            facts["lista"] = lista.como_handoff()
            facts["lista_encontradas"] = len(selecionadas)
            facts["lista_esperadas"] = quantos_meses_a_lista_espera(
                lista, competencias, facts["foco"], self._hoje()
            )
            facts["competencias"] = selecionadas or competencias[:COMPETENCIAS_NO_RASCUNHO]
        elif facts["foco"] not in ("saldo", "coparticipacao"):
            # Rodada 12: os OUTROS meses sem pagamento tambem sao fato — "Tambem consta sem pagamento
            # confirmado: ...". Fora do saldo (que ja' soma os meses) e da coparticipacao (e' do mes).
            mostrados = {c["competencia"] for c in facts["competencias"]}
            outros = [
                c for c in competencias if c["situacao"] == "vencida" and c["competencia"] not in mostrados
            ]
            if outros:
                facts["sem_pagamento"] = outros[:2]
        if self._v4 and mostra_boleto_da_familia(state) and lista is None and facts["competencias"]:
            # DL-0114: o detalhamento por beneficiario (SEM nome) da competencia respondida, quando a AMH o
            # entregou (billing-status 0.4.0). Os valores dele entram nos fatos para a cerca conferi-los.
            detalhe = _detalhamento_da_competencia_v4(state, facts["competencias"][0]["competencia"])
            if detalhe is not None:
                facts["detalhamento"] = detalhe
        facts["admissibilidade"] = state.get("admissibilidade")
        facts["dmn_refs"] = state.get("dmn_refs", {})
        return facts

    def _build_cancelamento(self, state: LucasState, facts: dict[str, Any]) -> dict[str, Any]:
        """A resposta a um pedido de cancelamento que NAO abre processo (rodada 12). A pergunta de confirmacao
        grava a propria referencia como memoria — e' o que torna a confirmacao seguinte valida."""
        resposta = state.get("cancelamento_resposta")
        texto = {
            "empresa": TEXTO_CANCELAMENTO_EMPRESA,
            "administradora": TEXTO_CANCELAMENTO_ADMINISTRADORA,
            "confirmar": TEXTO_CONFIRMA_CANCELAMENTO,
            "desistiu": TEXTO_PEDIDO_NAO_REGISTRADO if self._cancel_pf_v1 else TEXTO_CANCELAMENTO_DESISTIU,
            # DL-0111 (so' com a chave): o pedido e' do titular; oferta de pessoa, sem processo.
            "dependente": TEXTO_DEPENDENTE,
        }.get(str(resposta), TEXTO_CONFIRMA_CANCELAMENTO)
        mensagem = self._mensagem(texto, facts, VALORES_PROMPT_VERSION, state)
        if resposta == "confirmar":
            mensagem["memoria_ref"] = ref_de_resposta(TEXTO_CONFIRMA_CANCELAMENTO)
        curta = {
            "empresa": TEXTO_CANCELAMENTO_EMPRESA_CURTO,
            "administradora": TEXTO_CANCELAMENTO_ADMINISTRADORA_CURTO,
            "dependente": TEXTO_DEPENDENTE_CURTO,
        }
        if str(resposta) in curta:
            mensagem["curta"] = curta[str(resposta)]
        return mensagem

    def _build_pagamento_ao_prestador(self, state: LucasState, facts: dict[str, Any]) -> dict[str, Any]:
        """Rodada 5: "paguei a consulta direto ao medico e nao aparece". A coparticipacao cobrada pelo plano
        no mes (quando ha' fato) e o limite do que o Lucas ve' — so' o que o plano cobra."""
        competencias = _competencias_dos_fatos(state)
        pedida = state.get("competencia")
        c = next((x for x in competencias if x["competencia"] == pedida), None)
        partes: list[str] = []
        if c is not None and "valor_coparticipacao" in c:
            partes.append(_frase_de_coparticipacao(c, self._hoje()))
            facts["competencias"] = [
                {k: v for k, v in c.items() if k in ("competencia", "valor_coparticipacao")}
            ]
        partes.append(TEXTO_PAGAMENTO_AO_PRESTADOR)
        mensagem = self._mensagem(" ".join(partes), facts, VALORES_PROMPT_VERSION, state)
        mensagem["curta"] = TEXTO_PAGAMENTO_AO_PRESTADOR_CURTO
        return mensagem

    def _build_cobranca_administradora(self, state: LucasState) -> dict[str, Any]:
        """Coletivo por ADESAO (13/10/2026, bateria 4), em TEXTO FIXO, sem modelo. Os fatos levam SO' a
        parcela e a coparticipacao da competencia citada (a pedida; senao a mais recente)."""
        foco = _foco_da_pergunta(state) if state.get("tipo_solicitacao") == TIPO_CONSULTA_VALORES else None
        competencias = _competencias_dos_fatos(state)
        pedida = state.get("competencia")
        escolhida = next((c for c in competencias if c["competencia"] == pedida), None) or (
            competencias[0] if competencias else None
        )
        facts: dict[str, Any] = {
            "tipo_solicitacao": state.get("tipo_solicitacao"),
            "cobranca_administradora": True,
            "foco": foco,
            "competencias": []
            if escolhida is None
            else [
                {
                    k: v
                    for k, v in escolhida.items()
                    if k in ("competencia", "valor_total", "valor_coparticipacao")
                }
            ],
            "admissibilidade": state.get("admissibilidade"),
            "dmn_refs": state.get("dmn_refs", {}),
        }
        texto = texto_cobranca_administradora(state, facts, self._hoje())
        return self._mensagem(texto, facts, COBRANCA_ADMINISTRADORA_TEXTO_VERSION, state)

    def _build_pagador_empresa(self, state: LucasState) -> dict[str, Any]:
        """Contrato de EMPRESA (13/10/2026): "Sua mensalidade e' paga pela empresa contratante.", em TEXTO
        FIXO, sem modelo. Os fatos levam SO' a coparticipacao da competencia que a frase cita (pergunta de
        coparticipacao); senao nenhuma competencia, e a cerca recusa qualquer quantia, data ou boleto."""
        foco = _foco_da_pergunta(state) if state.get("tipo_solicitacao") == TIPO_CONSULTA_VALORES else None
        if _foco_da_pergunta(state) == FOCO_ESCLARECIMENTO:
            foco = FOCO_ESCLARECIMENTO
        facts: dict[str, Any] = {
            "tipo_solicitacao": state.get("tipo_solicitacao"),
            "pagador_tipo": PAGADOR_EMPRESA,
            "foco": foco,
            "competencias": [],
            "admissibilidade": state.get("admissibilidade"),
            "dmn_refs": state.get("dmn_refs", {}),
        }
        if foco == "coparticipacao":
            competencias = _competencias_dos_fatos(state)
            pedida = state.get("competencia")
            escolhida = next((c for c in competencias if c["competencia"] == pedida), None) or (
                competencias[0] if competencias else None
            )
            if escolhida is not None:
                facts["competencias"] = [
                    {k: v for k, v in escolhida.items() if k in ("competencia", "valor_coparticipacao")}
                ]
        texto = texto_pagador_empresa(state, facts, self._hoje())
        mensagem = self._mensagem(texto, facts, PAGADOR_EMPRESA_TEXTO_VERSION, state)
        if foco != "coparticipacao":
            # Rodada 4: as respostas da empresa (sem coparticipacao) sao UMA resposta, em degraus.
            # Rodada 8: a CHAVE da escada e' assunto + mes + tipo de pagador. Duas perguntas de assuntos
            # diferentes (pagamento, vencimento, 2a via) nunca contam como a mesma repeticao.
            mensagem["empresa_chave"] = (
                f"empresa:{PAGADOR_EMPRESA}:{tema_da_empresa(state)}:{state.get('competencia') or ''}"
            )
        return mensagem

    def _escada_da_empresa(
        self, state: LucasState, mensagem: Mapping[str, Any], texto: str
    ) -> tuple[str, str]:
        """Contrato de empresa (rodada 4; FASE 4, 18/10/2026). A explicacao inteira so' quando a resposta
        ANTERIOR do Lucas nao foi da empresa. Depois de um texto da empresa — de qualquer assunto ou mes —,
        uma forma CURTA do assunto desta pergunta: no mesmo assunto a seguinte da roda ([curtas..., nao
        consigo 1, nao consigo 2], as duas ultimas alternadas); num assunto novo, a primeira curta dele. A
        oferta de uma pessoa entra no maximo uma vez a cada duas respostas. O "de qual ano?" e' respondido (o
        ano) e conta como a explicacao. A memoria (`_ref_da_empresa`) guarda so' assunto, forma e oferta:
        nunca o texto."""
        anterior = _resposta_anterior_da_empresa(state)
        tema = tema_da_empresa(state)
        if _foco_da_pergunta(state) == FOCO_ESCLARECIMENTO and (
            anterior is None or (anterior[0] != _TEMA_EMPRESA_ANO and anterior[1] == 0)
        ):
            return texto, _ref_da_empresa(_TEMA_EMPRESA_ANO, 0, oferta=False)
        hoje = self._hoje()
        formas = [
            texto_pagador_empresa(state, {"foco": None}, hoje),
            *formas_curtas_da_empresa(state, hoje),
            NAO_CONSIGO[1],
            NAO_CONSIGO[0],
        ]
        if anterior is None:
            indice = 0
        elif anterior[0] == tema or (anterior[0] == _TEMA_EMPRESA_ANO and tema == TEMA_EMPRESA_PAGAMENTO):
            indice = anterior[1] + 1 if anterior[1] + 1 < len(formas) else len(formas) - 2
        else:
            indice = 1  # outro assunto logo depois da empresa: a curta dele, sem a abertura de novo
        oferta_anterior = anterior is not None and anterior[2]
        texto_da_vez = formas[indice]
        oferta = "nossa equipe" in texto_da_vez
        if 1 <= indice <= len(formas) - 3 and not oferta_anterior:
            texto_da_vez = f"{texto_da_vez} {OFERTA_PESSOA}"
            oferta = True
        return texto_da_vez, _ref_da_empresa(tema, indice, oferta=oferta)

    def _build_vencimento(self, state: LucasState) -> dict[str, Any]:
        """A resposta a "quando vence?" em TEXTO FIXO dos fatos por competencia (`texto_vencimento`).

        So' com os fatos de valor no turno (`valores_disponiveis`); sem eles, o lembrete de antes. Atraso
        nos fatos nao chega aqui com a DMN atual (`lba_r_vencimento_atraso` escala); se chegar (tabela
        antiga no motor), sai a constante honesta, sem data nem valor."""
        competencias = _competencias_dos_fatos(state)
        pedida = state.get("competencia") or None
        escolhida = _competencia_do_vencimento(competencias, pedida)
        facts: dict[str, Any] = {
            "tipo_solicitacao": state.get("tipo_solicitacao"),
            "competencia_pedida": pedida,
            "mais_recente_dos_fatos": competencias[0]["competencia"] if competencias else None,
            "competencias": [escolhida] if escolhida is not None else [],
            "admissibilidade": state.get("admissibilidade"),
            "dmn_refs": state.get("dmn_refs", {}),
        }
        sem_pagamento = [c for c in competencias if c["situacao"] == "vencida" and c is not escolhida][:2]
        if sem_pagamento:
            facts["sem_pagamento"] = sem_pagamento
        texto = texto_vencimento(state, facts, self._hoje())
        return self._mensagem(texto, facts, VENCIMENTO_PROMPT_VERSION, state)

    def _build_segunda_via(self, state: LucasState) -> dict[str, Any]:
        """A resposta a boleto/2a via (`RESPONDER`) em TEXTO FIXO (`texto_segunda_via`), sem modelo.

        Os fatos levam SO' a competencia de referencia (a que a frase cita), para a cerca de saida conferir
        a data e o boleto contra ela. Sem fatos por competencia, so' os canais."""
        if (
            self._v4
            and mostra_boleto_da_familia(state)
            and state.get("segunda_via_estado")
            in (
                SEGUNDA_VIA_COM_CODIGO,
                SEGUNDA_VIA_SEM_CODIGO,
            )
        ):
            mensagem_v4 = self._build_segunda_via_v4(state)
            if mensagem_v4 is not None:
                return mensagem_v4
        competencias = _competencias_dos_fatos(state)
        pedida = state.get("competencia") or None
        escolhida = _competencia_da_segunda_via(competencias, pedida)
        facts: dict[str, Any] = {
            "tipo_solicitacao": state.get("tipo_solicitacao"),
            "competencia_pedida": pedida,
            "competencias": [escolhida] if escolhida is not None else [],
            "admissibilidade": state.get("admissibilidade"),
            "dmn_refs": state.get("dmn_refs", {}),
        }
        if state.get("assunto_anterior") == "boleto_2via":
            # 13/10/2026 (simulador): a 2a via pedida DE NOVO (a pessoa nao consegue usar os canais) nao
            # repete os canais: diz que nao envia por aqui, da' o que tem e oferece alguem da equipe.
            texto = texto_segunda_via_repetida(state, facts, self._hoje())
        else:
            texto = texto_segunda_via(state, facts, self._hoje())
        return self._mensagem(texto, facts, SEGUNDA_VIA_TEXTO_VERSION, state)

    def _build_segunda_via_v4(self, state: LucasState) -> dict[str, Any] | None:
        """DL-0114: a 2a via do PAGADOR, em TEXTO FIXO. Com codigo, o texto leva o MARCADOR: o codigo de
        barras NUNCA entra no estado, na mensagem do grafo nem na referencia da memoria — `LucasTurno` o poe
        no texto no ultimo passo, dentro do remetente (`fonte_v4.CredenciaisDoTurno`). Sem codigo, o texto de
        "nao encontrei" com os canais. `None` quando o fato do estado nao tem a forma esperada (segue o de
        antes)."""
        facts: dict[str, Any] = {
            "tipo_solicitacao": state.get("tipo_solicitacao"),
            "competencia_pedida": state.get("competencia") or None,
            "competencias": [],
            "admissibilidade": state.get("admissibilidade"),
            "dmn_refs": state.get("dmn_refs", {}),
        }
        if state.get("segunda_via_estado") == SEGUNDA_VIA_SEM_CODIGO:
            # RODADA DO DONO (18/10/2026): "nao ha' titulo em aberto" e' diferente de "falha ao obter o
            # codigo".
            sem_aberto = _sem_boleto_em_aberto(state, self._hoje())
            if sem_aberto is not None:
                texto_pago, fato = sem_aberto
                facts["competencias"] = [fato]
                return self._mensagem(texto_pago, facts, SEGUNDA_VIA_TEXTO_VERSION, state)
            return self._mensagem(TEXTO_SEM_CODIGO_V4, facts, SEGUNDA_VIA_TEXTO_VERSION, state)
        boleto = _boleto_da_segunda_via_v4(state)
        if boleto is None:
            return None
        facts["competencias"] = [boleto["fato"]]
        texto = texto_segunda_via_com_codigo_v4(
            boleto, self._hoje(), composicao_do_mes(state, boleto["fato"])
        )
        mensagem = self._mensagem(texto, facts, SEGUNDA_VIA_TEXTO_VERSION, state)
        if not mensagem.get("recusa_de_saida"):
            # Sai sempre inteira (nunca a escada de repeticao: "nao envio o boleto" contradiria o codigo) e
            # deixa so' a referencia do texto COM o marcador.
            mensagem["memoria_ref"] = ref_de_resposta(str(mensagem["texto"]))
        return mensagem

    async def _build_valores(self, state: LucasState) -> dict[str, Any]:
        """DL-0086: a resposta a pergunta de VALOR, em TEXTO FIXO montado dos fatos do turno.

        Revisao de seguranca do #709: nenhum modelo redige valor (`texto_valores` copia cada campo da
        sua competencia). O texto passa assim mesmo pela cerca de saida (defesa em profundidade).
        Defesa em profundidade tambem na entrada: sem fato de valor, ou com atraso nos fatos (a DMN ja'
        teria escalado), sai a constante honesta.
        """
        facts = self._fatos_de_valores(state)
        if not valores_disponiveis(state):
            return self._mensagem(RESPOSTA_INFORMATIVA_RECUSADA, facts, VALORES_PROMPT_VERSION, state)
        return self._mensagem(
            texto_valores(state, facts, self._hoje(), v4=self._v4), facts, VALORES_PROMPT_VERSION, state
        )

    def _mensagem(
        self, texto: str, facts: dict[str, Any], prompt_version: str, state: LucasState | None = None
    ) -> dict[str, Any]:
        """Passa `texto` pela cerca de saida e monta o dicionario da mensagem informativa."""
        # CERCA DE SAIDA (18/09/2026). Os `fatos` vao junto porque o grupo `valor_sem_fato` e' uma
        # comparacao com eles — e' o que separa "o valor em aberto e' R$ 450,00" inventado de um
        # valor que veio da fonte (DL-0086). A cerca tambem recebe a data da fonte (`dados_de`), que
        # a frase de fechamento `_frase_da_fonte` cita, para conferir datas (`data_sem_fato`).
        fatos_da_cerca: dict[str, Any] = dict(facts)
        if state is not None:
            dados_de = _data_iso_da_fonte(state)
            if dados_de is not None:
                fatos_da_cerca["dados_de"] = dados_de
            composicao = _fatos_da_composicao(state)
            if composicao:
                # FASE 4: as quantias da composicao curta do boleto (`composicao_do_mes`) sao fatos do turno.
                fatos_da_cerca["composicao_do_boleto"] = composicao
        recusado = False
        try:
            texto = self._cercar_saida(texto, "mensagem", fatos_da_cerca)
        except RespostaRecusadaError:
            # NAO ha segunda tentativa, pelo mesmo motivo que na Helena: o mesmo prompt com os
            # mesmos fatos tende ao mesmo texto. A constante e' honesta para esta rota.
            texto = RESPOSTA_INFORMATIVA_RECUSADA
            recusado = True
        return {
            "prompt_version": prompt_version,
            "recusa_de_saida": recusado,
            "tipo": "mensagem_beneficiario",
            "fatos": facts,
            "texto": texto,
            # STRUCTURAL GUARDRAIL: never a suspension/cancellation communication — always None
            # here; a value would be a detectable bug. Mirrors the donor's own guardrail fields.
            "comunicacao_suspensao": None,
            "comunicacao_cancelamento": None,
        }

    async def _build_dossier(self, state: LucasState) -> dict[str, Any]:
        """Monta o dossie da escalacao (J3 / fail-safe). `decisao_cancelamento` e' SEMPRE `None`
        (espelha a guarda `decisao_cobertura` do Rafael): a decisao e' exclusivamente do humano.

        A NARRATIVA E' TEXTO FIXO desde 09/10/2026 (`texto_dossie`, sem chamada de modelo): o modelo
        lia `contesta_cobranca=False` como contestacao registrada e a fonte indisponivel como "sem
        pagamento conciliado". Continua `async` porque `escalate_human` e os testes a aguardam.

        `intencao` is ALLOWLIST-GATED (engine-variable hygiene, R1 cycle-1): on the
        `ambiguidade` path the raw value is by definition UNRECOGNIZED — echoing it into the
        dossier (which ships into engine variables as `dossie_lucas`) would be exactly the
        offending-enum-value leak class Helena's T1.11 cycle-2 fixed. Out-of-allowlist -> None.
        """
        raw_intencao = state.get("intencao")
        facts: dict[str, Any] = {
            "intencao": raw_intencao if raw_intencao in _VALID_INTENCOES else None,
            "tipo_solicitacao": state.get("tipo_solicitacao"),
            "competencia": state.get("competencia"),
            "numero_boleto": state.get("numero_boleto"),
            "status_conciliado": state.get("status_conciliado"),
            "ciclos_sem_conciliacao": state.get("ciclos_sem_conciliacao"),
            "contesta_cobranca": state.get("contesta_cobranca"),
            "pedido_cancelamento": state.get("pedido_cancelamento"),
            "cnab_ref": state.get("cnab_ref"),
            "motivo_humano": state.get("motivo_humano"),
            "grupo_humano": state.get("grupo_humano"),
            "roteamento_escalacao": state.get("roteamento_escalacao"),
            "dmn_refs": state.get("dmn_refs", {}),
            "lacunas_enriquecimento": state.get("gather_notes", []),
        }
        return {
            "prompt_version": DOSSIE_TEXTO_VERSION,
            "tipo": "dossie_escalacao",
            "motivo_humano": state.get("motivo_humano"),
            "grupo_humano": state.get("grupo_humano"),
            "fatos": facts,
            "dmn_decision_refs": state.get("dmn_refs", {}),
            "narrativa": texto_dossie(state),
            # STRUCTURAL GUARDRAIL (L0 hard): the dossier NEVER carries the adverse decision.
            "decisao_cancelamento": None,  # rescindir/manter/suspender — always human (CANCEL-001)
        }

    def _forma_curta(self, state: LucasState, mensagem: Mapping[str, Any]) -> str | None:
        """A forma CURTA da resposta deste turno (rodada 4), para quando a anterior foi a mesma: a do
        contrato de empresa, ou "E' julho de 2026: paga em 18/08." do mes da resposta. Passa pela cerca
        com os fatos da mensagem; recusada, nenhuma."""
        if mensagem.get("prompt_version") == PAGADOR_EMPRESA_TEXTO_VERSION:
            # Rodada 5: a curta da empresa so' para as respostas da empresa — nunca para a coparticipacao.
            return texto_pagador_empresa_curto(state, self._hoje()) if mensagem.get("empresa_chave") else None
        if mensagem.get("curta"):
            return str(mensagem["curta"])
        fatos = mensagem.get("fatos")
        if (
            not isinstance(fatos, Mapping)
            or not fatos.get("competencias")
            or isinstance(fatos.get("lista"), Mapping)
        ):
            return None
        curta = texto_de_esclarecimento(state, self._hoje())
        if curta is None:
            return None
        fatos_da_cerca = dict(fatos)
        dados_de = _data_iso_da_fonte(state)
        if dados_de is not None:
            fatos_da_cerca["dados_de"] = dados_de
        return None if motivo_de_recusa(curta, "mensagem", fatos_da_cerca) is not None else curta

    def _fatos_da_cerca_do_ack(self, state: LucasState) -> dict[str, Any]:
        """Os fatos com que a cerca confere valor, data e boleto do ACK que cita fatos (13/10/2026)."""
        fatos: dict[str, Any] = {"competencias": _competencias_dos_fatos(state)}
        dados_de = _data_iso_da_fonte(state)
        if dados_de is not None:
            fatos["dados_de"] = dados_de
        composicao = _fatos_da_composicao(state)
        if composicao:
            fatos["composicao_do_boleto"] = composicao
        return fatos

    def _texto_de_caso_aberto(self, state: LucasState, *, com_aviso: bool = True) -> str:
        texto = texto_caso_aberto(state, self._hoje(), com_aviso=com_aviso)
        try:
            return self._cercar_saida(texto, "ack_escalacao", self._fatos_da_cerca_do_ack(state))
        except RespostaRecusadaError:
            return aviso_de_caso_aberto(state)

    def _build_escalation_ack(self, state: LucasState) -> str:
        """Short WhatsApp acknowledgement sent on the escalation path (module docstring's
        disclosed improvement over the donor). NEVER reveals the pending adverse outcome.

        TEXTO FIXO desde DL-0082 (08/10/2026): ate' ali o modelo redigia este ACK com
        `motivo_humano` no prompt, e no teste real escreveu "inadimplencia detectada" para quem
        estava em dia. Agora o texto sai de `texto_ack_escalacao` (fatos + motivo ja' decidido pela
        DMN), sem chamada de modelo."""
        texto = texto_ack_escalacao(state, self._hoje())
        # CERCA DE SAIDA (18/09/2026), mantida sobre o texto fixo como defesa em profundidade. ESTE
        # e' o texto mais exposto do Lucas: ele chega a alguem cujo caso acabou de ser encaminhado
        # por atraso, contestacao ou pedido de cancelamento. A rota `ack_escalacao` e' a UNICA em
        # que prometer humano e' verdadeiro. Desde 13/10/2026 o ACK de atraso cita o mes e o boleto
        # dos fatos: a cerca os confere contra eles.
        try:
            return self._cercar_saida(texto, "ack_escalacao", self._fatos_da_cerca_do_ack(state))
        except RespostaRecusadaError:
            return ACK_ESCALACAO_RECUSADO

    # -- Graph assembly -----------------------------------------------------------------------

    def compile_graph(self) -> StateGraph[LucasState]:
        g: StateGraph[LucasState] = StateGraph(LucasState)
        g.add_node("receive", self.receive)
        g.add_node("gather", self.gather)
        g.add_node("assess", self.assess)
        g.add_node("respond_member", self.respond_member)
        g.add_node("escalate_human", self.escalate_human)
        g.add_node("start_process", self.start_process)
        g.add_node("notify_start_failure", self.notify_start_failure)
        g.add_node("send_escalation_ack", self.send_escalation_ack)
        g.add_node("complete", self.complete)

        g.add_edge(START, "receive")
        g.add_edge("receive", "gather")
        g.add_edge("gather", "assess")
        g.add_conditional_edges(
            "assess", self._route, {"respond_member": "respond_member", "escalate_human": "escalate_human"}
        )
        g.add_edge("respond_member", "start_process")
        g.add_edge("escalate_human", "start_process")
        # CC-01: a aresta que sai de `start_process` e CONDICIONAL. Uma falha tecnica de
        # start desvia para `notify_start_failure` (desfecho de erro + alerta); qualquer
        # outro caminho — incluindo os no-ops legitimos com `process_started=False` —
        # segue para o terminal de sempre. O predicado e compartilhado (uma definicao).
        g.add_conditional_edges(
            "start_process",
            route_after_start,
            {"notify_start_failure": "notify_start_failure", "continue": "send_escalation_ack"},
        )
        g.add_edge("notify_start_failure", END)
        g.add_edge("send_escalation_ack", "complete")
        g.add_edge("complete", END)
        return g


def build(config: dict[str, Any] | None = None) -> StateGraph[LucasState]:
    """Contract `_template/graph.py:build`, resolved by `AgentLoader`/`runtime.harness.Harness`.

    `config` MUST contain:
      - `inference`: an `InferenceProvider` (ADR-0009).
      - `dmn`: a `DmnTransport` (ADR-0028/T1.5).
      - `cibseven`: a `CibSevenTransport` (ADR-0001/T1.11).
      - `whatsapp`: a `WhatsAppSender`.
    Optional:
      - `agent_version`: audit provenance string (ADR-0007), defaults to `"lucas@v0"`.

    Fail-closed: missing a required dependency raises `ValueError` at build time — Lucas never
    silently constructs a graph that would crash mid-conversation on its first tool call.
    """
    cfg = config or {}
    inference = cfg.get("inference")
    dmn = cfg.get("dmn")
    cibseven = cfg.get("cibseven")
    whatsapp = cfg.get("whatsapp")
    audit_sink = cfg.get("audit_sink")
    missing = [
        name
        for name, value in (
            ("inference", inference),
            ("dmn", dmn),
            ("cibseven", cibseven),
            ("whatsapp", whatsapp),
            ("audit_sink", audit_sink),
        )
        if value is None
    ]
    if missing:
        raise ValueError(
            f"Lucas build(config) is missing required dependencies: {missing} "
            "(ADR-0001/0007/0009/0028 — inference/dmn/cibseven/whatsapp/audit_sink must all be "
            "injected; audit_sink is the T-C2 fence — no escalation start without a durable sink)"
        )
    agent_version = str(cfg.get("agent_version", "lucas@v0"))
    limiares = cfg.get("limiares_atraso")
    return LucasGraph(
        # DL-0111: `MAEZO_CANCEL_PF_V1` (so' `True` literal liga; qualquer outra coisa e' desligada).
        cancelamento_pf_v1=cfg.get("cancelamento_pf_v1") is True,
        # DL-0114: `MAEZO_LUCAS_V4` (so' `True` literal liga).
        lucas_v4=cfg.get("lucas_v4") is True,
        limiares_atraso=limiares if isinstance(limiares, LimiaresDeAtraso) else None,
        inference=cast(InferenceProvider, inference),
        dmn=cast(DmnTransport, dmn),
        cibseven=cast(CibSevenTransport, cibseven),
        audit_sink=cast(AuditStartSink, audit_sink),
        whatsapp=cast(WhatsAppSender, whatsapp),
        agent_version=agent_version,
    ).compile_graph()


# Prompt versions exposed for audit/eval gating (ADR-0007/0009).
PROMPT_VERSIONS: dict[str, str] = {
    "system": SYSTEM_PROMPT_VERSION,
    "message": MESSAGE_PROMPT_VERSION,
    "dossier": DOSSIE_TEXTO_VERSION,
    # 18/09/2026: os dois que faltavam. O ACK de escalacao e' o texto do caminho ADVERSO e era o
    # unico sem numero — olhando uma mensagem que vazou, nao dava para dizer qual redacao a
    # produziu. A cerca entra aqui pelo mesmo motivo que na Helena: ela decide o que o
    # beneficiario le', entao ela e' versao de prompt para todo efeito de auditoria.
    "escalation_ack": ESCALATION_ACK_PROMPT_VERSION,
    "recusa_de_saida": RECUSA_DE_SAIDA_VERSION,
    # DL-0082: a resposta FIXA de "mensalidade em dia" tambem fala com o beneficiario.
    "mensagem_em_dia": MENSAGEM_EM_DIA_VERSION,
    # DL-0086: a resposta FIXA a pergunta de VALOR (`consulta_valores`), montada dos fatos.
    "valores": VALORES_PROMPT_VERSION,
    # 09/10/2026: a resposta FIXA a "quando vence?", montada dos fatos por competencia.
    "vencimento": VENCIMENTO_PROMPT_VERSION,
    # 10/10/2026: a resposta FIXA a boleto/2a via (canais confirmados + situacao dos fatos).
    "segunda_via": SEGUNDA_VIA_TEXTO_VERSION,
    # 13/10/2026: a resposta FIXA a quem tem a mensalidade paga pela empresa contratante.
    "pagador_empresa": PAGADOR_EMPRESA_TEXTO_VERSION,
    # 13/10/2026 (bateria 4): coletivo por adesao cobrado pela administradora.
    "cobranca_administradora": COBRANCA_ADMINISTRADORA_TEXTO_VERSION,
}
