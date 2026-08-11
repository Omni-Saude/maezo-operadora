"""The CLOSED operation catalogue of the effect chokepoint (Onda 1, design §6.1 / §5.1).

WHAT THIS IS. Pure data: the finite, code-frozen map from a seam OPERATION token
(`fhir.read_patient`, `whatsapp.send_message`, …) to the governance facts the decision core
(`maezo.gateway.effect_pep.decide`) needs — the ratified autonomy action, the action class, and,
per class, the DECLARED DENIAL SHAPE the seam wrappers must produce. Nothing here performs I/O,
imports a transport, or decides anything; `effect_pep` reads it, and the (later) seam wrappers
read the denial shape out of it rather than inventing a refusal at the call site (design §5.4).

CLOSED, NOT OPEN. An operation absent from :data:`OPERATIONS` is not "unclassified and therefore
fine" — it is `OPERACAO_DESCONHECIDA`, a DENY at L-0 (design §5.3, ADR-0037 XRD-09: "ação/política
desconhecida … negam"). Adding an operation is a reviewed code change in this file, deliberately:
the catalogue is the enumeration an approver reads to know what the chokepoint covers.

WHY DENIAL SHAPE IS PER CLASS AND LIVES HERE. Design §2 A-12 is the inverse adversary — the PEP
harming the patient. A DENY on a read that a node treats as best-effort degrades to a disclosed
gap note; the same DENY on a required seam stalls a care request. So each class declares, as a
BOUNDED TOKEN, what a refusal must look like, and §8.5's fence requires a mutation proof per
class. A wrapper that invents its own refusal text is the defect this field exists to prevent.

THREE THINGS THIS FILE DELIBERATELY DOES NOT DO — each is a human decision, recorded not hidden:

  * **No new autonomy vocabulary (Q-4).** LLM inference, A2A delegation and population/actuarial
    reads have NO ratified name in `spec/policies/autonomy/L0-core.yaml`. Their entries carry
    `autonomy_action=None`, and `decide()` records `VOCABULARIO_PENDENTE` at L-2 with `allow=False`
    — an honest would-deny in shadow telemetry. Inventing a name here would be a second vocabulary
    and a fail-open surface (`gateway/pep.py:12-18`), and adding one to `L0-core.yaml` is an
    ADR-0008/0025 change requiring the `_hard_frozen.yaml` cross-check. NOT DONE HERE.
  * **No consent flag (Q-5).** Every class declares `consentimento_exigido=False`, so the L-4 leg
    is present-but-inert. `ConsentDecisionSource` exists only as a port (`src/maezo/ports/
    consent.py`) with no adapter; flagging a class true would DENY it until one lands, which is a
    fail-closed but load-bearing product decision.
  * **No pre-effect audit flag (Q-9).** Every class declares `audita_antes=False`. A `true` adds a
    durable write in front of an engine-path call and needs the SRE latency sign-off design §I-9
    demands. The existing audit-before-effect of `start_process_idempotent`
    (`tools/mcp_cibseven/transport.py:1069`) is UNCHANGED and independent of this flag.

KNOWN GAP, DISCLOSED (not a silent omission). `mcp-memory.read_write` is declared by 11 of the 11
`spec/agents/*/agent.yaml` files and maps to the ratified `read_write_memory` (L3), but design
§6.1's rung table declares NO action class for memory. Classifying it is the same kind of human
act the manifest already reserves for the ~80 unmapped worker topics
(`spec/policies/autonomy/action-approvals.yaml:350-353`: "decisão humana, não inferência de
agente"), so no `memory.*` operation is catalogued here. Consequence, stated plainly: a memory
seam routed through the chokepoint would DENY with `OPERACAO_DESCONHECIDA` — fail-closed, and
inert today because nothing enforces. Design §8.5 item 2 (every declared `mcp-<server>.<action>`
id resolves to a catalogue operation) therefore cannot pass until a human classifies memory; the
CI-fence leg must record that as a declared exception rather than "fix" it by inventing a class.

SECOND DEVIATION, DISCLOSED. Design §6.1's C1 row names both `send_beneficiary_message` (L3) and
`send_beneficiary_template` (L2). `tools/mcp_whatsapp/server.py` registers exactly two tools —
`send_message` and `verify_webhook` (`:85-86`) — and no `agent.yaml` declares a template id. A
`whatsapp.send_template` operation would be a catalogue entry with no runtime surface, which is
precisely what `action-approvals.yaml:104-106` forbids ("no class was invented to round out a
taxonomy"). It is omitted; it becomes catalogueable the day the surface exists.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Final

#: Prefix of the agent-side `action_ref` namespace. Worker refs stay the external-task topic
#: verbatim (`operadora.auth.issue_authorization`); agent refs are `agente.<operation>`. Both
#: namespaces satisfy the dotted-token shape telemetry already accepts
#: (`action_execution._is_bounded_topic`), so no telemetry change is needed (design §7.1).
AGENT_ACTION_REF_PREFIX: Final[str] = "agente."

# -- Rungs (design §6.1): increasing harm-on-denial AND harm-on-permit. Ordering metadata for the
# progressive ramp (§9.4 flips C0 -> C4); the decision core does not read them.
RUNG_C0_LEITURA_INTERNA: Final[str] = "C0"
RUNG_C1_NOTIFICACAO: Final[str] = "C1"
RUNG_C2_PHI_OU_MODELO: Final[str] = "C2"
RUNG_C3_MUTACAO_ENGINE: Final[str] = "C3"
RUNG_C4_ADVERSO: Final[str] = "C4"

# -- Denial shapes: a CLOSED enum of bounded, non-PHI tokens (same discipline as
# `action_execution`'s REASON_* vocabulary). The seam wrappers INTERPRET these; they never emit
# free text. Each maps 1:1 to a "Denial-mutation proof shape" cell of design §6.1.

#: The node takes its declared DMN-unavailable path (ADR-0028 fail-closed -> human). Never a
#: fabricated favourable outcome.
SHAPE_ROTA_DMN_INDISPONIVEL: Final[str] = "ROTA_DMN_INDISPONIVEL"
#: A denied engine READ must NOT be readable as "no active instance" — the anti-dupla-terminação
#: query fails closed (skip), matching today's missing-engine behaviour. Highest-risk C0 item.
SHAPE_LEITURA_INCONCLUSIVA: Final[str] = "LEITURA_INCONCLUSIVA"
#: The turn ends on its declared escalation/HITL path; the beneficiary is not silently dropped and
#: no raw recipient appears in any log line (I-3).
SHAPE_ESCALONAMENTO_HUMANO: Final[str] = "ESCALONAMENTO_HUMANO"
#: Degrades to the disclosed gap note every graph already documents (`agents/rafael/graph.py:44-48`)
#: — never a fabricated fact, never a blocked routing decision.
SHAPE_LACUNA_DECLARADA: Final[str] = "LACUNA_DECLARADA"
#: The node's existing LLM-unavailable path. The `phi=True` zone routing must keep fail-closing
#: INDEPENDENTLY of the PEP (I-6, defense in depth).
SHAPE_ROTA_LLM_INDISPONIVEL: Final[str] = "ROTA_LLM_INDISPONIVEL"
#: No effect happened, an audited refusal row exists, and the external task lands as an incident
#: with `retries=0` (`tools/workers/harness.py:1635-1651`). The C3/C4 shape.
SHAPE_INCIDENTE_FALHA_FECHADA: Final[str] = "INCIDENTE_FALHA_FECHADA"
#: The delegating side degrades exactly as it does when the dossier seam is absent — never a
#: fabricated dossier.
SHAPE_DEGRADACAO_SEM_DOSSIE: Final[str] = "DEGRADACAO_SEM_DOSSIE"

DENIAL_SHAPES: Final[frozenset[str]] = frozenset(
    {
        SHAPE_ROTA_DMN_INDISPONIVEL,
        SHAPE_LEITURA_INCONCLUSIVA,
        SHAPE_ESCALONAMENTO_HUMANO,
        SHAPE_LACUNA_DECLARADA,
        SHAPE_ROTA_LLM_INDISPONIVEL,
        SHAPE_INCIDENTE_FALHA_FECHADA,
        SHAPE_DEGRADACAO_SEM_DOSSIE,
    }
)


@dataclass(frozen=True, slots=True)
class ActionClassSpec:
    """The governance facts of ONE action class. Pure data, code-frozen.

    Attributes:
        name: the manifest class name (`spec/policies/autonomy/action-approvals.yaml` `acoes`).
        rung: `C0`..`C4` — the progressive-enforcement rung (design §6.1). Metadata for the ramp.
        denial_shape: the bounded token the seam wrappers interpret on an ENFORCED deny (§5.4).
        audita_antes: emit an approval-provenance record through the audit sink BEFORE delegating
            (I-1). False for every class today — Q-9 (SRE latency sign-off) is human.
        consentimento_exigido: L-4 applies to this class. False for every class today — Q-5 is
            human, and `ConsentDecisionSource` has no adapter, so a `true` here would DENY the
            class outright.
        novo: declared by Onda 1 (`True`) vs already present in the shipped manifest (`False`).
            Read by the tests that assert the additive-only posture; never by the decision core.
    """

    name: str
    rung: str
    denial_shape: str
    audita_antes: bool = False
    consentimento_exigido: bool = False
    novo: bool = False


def _classes(*specs: ActionClassSpec) -> MappingProxyType[str, ActionClassSpec]:
    return MappingProxyType({spec.name: spec for spec in specs})


#: The CLOSED action-class registry (design §6.1). Ten classes already declared in the shipped
#: manifest, five declared by Onda 1 as UNAPPROVED shadow entries. Every name here must exist in
#: `action-approvals.yaml` `acoes` (asserted by the tests), and no name may be added without the
#: matching manifest declaration — a class the loader has never heard of can never be approved.
ACTION_CLASSES: Final[MappingProxyType[str, ActionClassSpec]] = _classes(
    # -- C0: inert / internal read -------------------------------------------------------------
    ActionClassSpec(
        name="avaliacao_dmn",
        rung=RUNG_C0_LEITURA_INTERNA,
        denial_shape=SHAPE_ROTA_DMN_INDISPONIVEL,
        novo=True,
    ),
    ActionClassSpec(
        name="consulta_processo",
        rung=RUNG_C0_LEITURA_INTERNA,
        denial_shape=SHAPE_LEITURA_INCONCLUSIVA,
        novo=True,
    ),
    # -- C1: outbound notification ---------------------------------------------------------------
    ActionClassSpec(
        name="comunicacao_beneficiario",
        rung=RUNG_C1_NOTIFICACAO,
        denial_shape=SHAPE_ESCALONAMENTO_HUMANO,
    ),
    # -- C2: PHI read / model egress -------------------------------------------------------------
    ActionClassSpec(
        name="leitura_phi_clinica",
        rung=RUNG_C2_PHI_OU_MODELO,
        denial_shape=SHAPE_LACUNA_DECLARADA,
    ),
    ActionClassSpec(
        name="leitura_populacional",
        rung=RUNG_C2_PHI_OU_MODELO,
        denial_shape=SHAPE_LACUNA_DECLARADA,
        novo=True,
    ),
    ActionClassSpec(
        name="inferencia_llm",
        rung=RUNG_C2_PHI_OU_MODELO,
        denial_shape=SHAPE_ROTA_LLM_INDISPONIVEL,
        novo=True,
    ),
    # -- C3: engine mutation ---------------------------------------------------------------------
    ActionClassSpec(
        name="inicio_processo_regulatorio",
        rung=RUNG_C3_MUTACAO_ENGINE,
        denial_shape=SHAPE_INCIDENTE_FALHA_FECHADA,
    ),
    ActionClassSpec(
        name="correlacao_processo",
        rung=RUNG_C3_MUTACAO_ENGINE,
        denial_shape=SHAPE_INCIDENTE_FALHA_FECHADA,
        novo=True,
    ),
    ActionClassSpec(
        name="delegacao_a2a",
        rung=RUNG_C3_MUTACAO_ENGINE,
        denial_shape=SHAPE_DEGRADACAO_SEM_DOSSIE,
    ),
    # -- C4: adverse / money / regulatory — worker-topic classes, no agent-side operation today.
    # They are registered so the denial-shape contract and the §8.5 completeness assertions cover
    # every class the manifest declares, not only the ones an agent seam can reach.
    ActionClassSpec(
        name="autorizacao_emissao",
        rung=RUNG_C4_ADVERSO,
        denial_shape=SHAPE_INCIDENTE_FALHA_FECHADA,
    ),
    ActionClassSpec(
        name="negativa_notificacao",
        rung=RUNG_C4_ADVERSO,
        denial_shape=SHAPE_INCIDENTE_FALHA_FECHADA,
    ),
    ActionClassSpec(
        name="submissao_regulatoria_ans",
        rung=RUNG_C4_ADVERSO,
        denial_shape=SHAPE_INCIDENTE_FALHA_FECHADA,
    ),
    ActionClassSpec(
        name="pagamento_emissao",
        rung=RUNG_C4_ADVERSO,
        denial_shape=SHAPE_INCIDENTE_FALHA_FECHADA,
    ),
    ActionClassSpec(
        name="vinculo_contratual_mudanca",
        rung=RUNG_C4_ADVERSO,
        denial_shape=SHAPE_INCIDENTE_FALHA_FECHADA,
    ),
    ActionClassSpec(
        name="acusacao_fraude_registro",
        rung=RUNG_C4_ADVERSO,
        denial_shape=SHAPE_INCIDENTE_FALHA_FECHADA,
    ),
)


@dataclass(frozen=True, slots=True)
class OperationSpec:
    """One catalogued seam operation. Pure data, code-frozen.

    Attributes:
        operation: the dotted operation token — the `EffectCall.operation` value and the suffix of
            the agent-side `action_ref` (`agente.<operation>`).
        action_class: the class this operation belongs to; a key of :data:`ACTION_CLASSES`.
        autonomy_action: the ratified `L0-core.yaml` action name evaluated at L-2, or None when NO
            ratified name exists (Q-4) — in which case L-2 records `VOCABULARIO_PENDENTE` and
            denies, rather than inventing vocabulary.
        tool_id: the `mcp-<server>.<action>` id an `agent.yaml` `tools:` list can declare, or None
            for a seam that is NOT an MCP tool (the LLM provider, the A2A dispatcher, the
            population client, and the two engine history reads that no tool id names). L-1's
            capability check applies ONLY when this is set — asking "did the agent declare this
            tool?" of something that is not a tool would deny on a category error rather than on
            policy. Those seams are not thereby unguarded: they still traverse L-2 (where the
            three nameless ones deny with `VOCABULARIO_PENDENTE`) and L-5 (where no class is
            approved), so every one of them is a would-DENY today.
        requires_process_key: L-1 additionally checks the call's `process_key` against the agent's
            declared `process_keys` ∩ the ADR-0016 known universe. True only for the engine start
            — this is the R-1 closure (`ProcessAllowlist` had zero production importers).
        ceiling_action: the `L0-core.yaml` action whose `params` carry the money teto for L-3, or
            None (every catalogued operation today: no agent seam moves money).
        ceiling_param: the params key (`max_value_brl` / `threshold_brl`). Set iff
            `ceiling_action` is set.
        phi_egress: this operation can carry PHI OUT of the platform boundary. Documentation for
            the approvers and the reason `inference.generate` is split in two (design §6.1 C2);
            the decision core does not gate on it — ADR-0006 zone routing is INDEPENDENT (I-6).
    """

    operation: str
    action_class: str
    autonomy_action: str | None
    tool_id: str | None = None
    requires_process_key: bool = False
    ceiling_action: str | None = None
    ceiling_param: str | None = None
    phi_egress: bool = False


def _operations(*specs: OperationSpec) -> MappingProxyType[str, OperationSpec]:
    return MappingProxyType({spec.operation: spec for spec in specs})


#: The CLOSED operation catalogue (design §6.1 + Appendix A, every surface re-derived against the
#: tree). Unknown operation => L-0 DENY `OPERACAO_DESCONHECIDA`.
OPERATIONS: Final[MappingProxyType[str, OperationSpec]] = _operations(
    # -- FHIR reads (C2). Leaves `tools/mcp_fhir/server.py:88` (read_resource) and `:118`
    # (search_resources); agents reach them through per-agent adapter Protocols.
    OperationSpec(
        operation="fhir.read_patient",
        action_class="leitura_phi_clinica",
        autonomy_action="read_phi_data",
        tool_id="mcp-fhir.read_patient",
    ),
    OperationSpec(
        operation="fhir.read_patient_summary",
        action_class="leitura_phi_clinica",
        autonomy_action="read_phi_data",
        tool_id="mcp-fhir.read_patient_summary",
    ),
    OperationSpec(
        operation="fhir.read_coverage",
        action_class="leitura_phi_clinica",
        autonomy_action="read_phi_data",
        tool_id="mcp-fhir.read_coverage",
    ),
    OperationSpec(
        operation="fhir.search_coverage",
        action_class="leitura_phi_clinica",
        autonomy_action="read_phi_data",
        tool_id="mcp-fhir.search_coverage",
    ),
    # -- WhatsApp send (C1). Leaf `tools/mcp_whatsapp/server.py:111`.
    OperationSpec(
        operation="whatsapp.send_message",
        action_class="comunicacao_beneficiario",
        autonomy_action="send_beneficiary_message",
        tool_id="mcp-whatsapp.send_message",
        phi_egress=True,
    ),
    # -- Engine mutation (C3). `start_process_idempotent` at `tools/mcp_cibseven/transport.py:1069`
    # keeps its own fence and its own audit-before-effect claim — UNCHANGED (I-6, §5.8).
    OperationSpec(
        operation="cibseven.start_process",
        action_class="inicio_processo_regulatorio",
        autonomy_action="start_compliance_process",
        tool_id="mcp-cibseven.start_process",
        requires_process_key=True,
    ),
    OperationSpec(
        operation="cibseven.correlate_message",
        action_class="correlacao_processo",
        autonomy_action="correlate_process_message",
        tool_id="mcp-cibseven.correlate_process_message",
    ),
    # -- Engine reads (C0). `get_process_status` `transport.py:420`; `find_active_instance` `:278`;
    # `find_any_instance` `:307` (the `HistoryQueryingTransport` leg). The latter two carry no MCP
    # tool id: no `agent.yaml` names them, they are internal reads of the same transport.
    OperationSpec(
        operation="cibseven.get_process_status",
        action_class="consulta_processo",
        autonomy_action="query_process_status",
        tool_id="mcp-cibseven.get_process_status",
    ),
    OperationSpec(
        operation="cibseven.find_active_instance",
        action_class="consulta_processo",
        autonomy_action="query_process_status",
    ),
    OperationSpec(
        operation="cibseven.find_any_instance",
        action_class="consulta_processo",
        autonomy_action="query_process_status",
    ),
    # -- DMN (C0). ONE `self._dmn.evaluate` transport call site per graph, behind a private
    # `_evaluate_dmn` helper — rafael `graph.py:546,:548`, marina `:804,:806` (design R-7).
    OperationSpec(
        operation="dmn.evaluate",
        action_class="avaliacao_dmn",
        autonomy_action="query_decision_engine",
        tool_id="mcp-dmn.evaluate",
    ),
    # -- LLM inference (C2), SPLIT BY ZONE per design §6.1: the provider routes on `phi=`
    # (`runtime/inference.py:616` `generate`, `PhiZoneRoutingError`). Two operation tokens so the
    # shadow telemetry can tell a PHI-bearing generation from a general one WITHOUT carrying the
    # prompt. No ratified autonomy name exists for either (Q-4).
    OperationSpec(
        operation="inference.generate",
        action_class="inferencia_llm",
        autonomy_action=None,
    ),
    OperationSpec(
        operation="inference.generate_phi",
        action_class="inferencia_llm",
        autonomy_action=None,
        phi_egress=True,
    ),
    # -- Population / actuarial aggregates (C2). andre `graph.py:708` (actuarial_risk), `:720`
    # (population_metrics); Protocol `PopulationFeatureClient` at `:316`. k-anon aggregates are
    # not PHI, which is exactly why `read_phi_data` is the WRONG name for them (Q-4).
    OperationSpec(
        operation="population.actuarial_risk",
        action_class="leitura_populacional",
        autonomy_action=None,
    ),
    OperationSpec(
        operation="population.population_metrics",
        action_class="leitura_populacional",
        autonomy_action=None,
    ),
    # -- A2A delegation (C3). `a2a/dispatcher.py:277` `delegate`. No ratified name (Q-4).
    OperationSpec(
        operation="a2a.delegate",
        action_class="delegacao_a2a",
        autonomy_action=None,
    ),
)


#: Every `mcp-<server>.<action>` id the catalogue resolves. The (later) CI fence compares this
#: against the union of every `spec/agents/*/agent.yaml` `tools:` list — design §8.5 item 2. See
#: the module docstring's KNOWN GAP for `mcp-memory.read_write`.
CATALOGUED_TOOL_IDS: Final[frozenset[str]] = frozenset(
    spec.tool_id for spec in OPERATIONS.values() if spec.tool_id is not None
)


def lookup_operation(operation: object) -> OperationSpec | None:
    """The catalogue entry for `operation`, or None (=> L-0 DENY `OPERACAO_DESCONHECIDA`).

    Total: a non-string, an unhashable, an empty string and an unknown token all return None.
    """
    if not isinstance(operation, str) or not operation:
        return None
    return OPERATIONS.get(operation)


def lookup_class(action_class: object) -> ActionClassSpec | None:
    """The class entry for `action_class`, or None when the class is not in the closed registry."""
    if not isinstance(action_class, str) or not action_class:
        return None
    return ACTION_CLASSES.get(action_class)


def denial_shape_for(action_class: object) -> str | None:
    """The declared denial shape of `action_class`, or None when the class is unknown.

    A wrapper that gets None here must NOT improvise a refusal: an unknown class means the
    catalogue and the manifest disagree, which is a fail-closed condition, not a formatting choice.
    """
    spec = lookup_class(action_class)
    return None if spec is None else spec.denial_shape


def agent_action_ref(operation: str) -> str:
    """`agente.<operation>` — the agent-side `action_ref` for `mapeamento_acoes` (design §7.1)."""
    return f"{AGENT_ACTION_REF_PREFIX}{operation}"


__all__ = [
    "ACTION_CLASSES",
    "AGENT_ACTION_REF_PREFIX",
    "CATALOGUED_TOOL_IDS",
    "DENIAL_SHAPES",
    "OPERATIONS",
    "RUNG_C0_LEITURA_INTERNA",
    "RUNG_C1_NOTIFICACAO",
    "RUNG_C2_PHI_OU_MODELO",
    "RUNG_C3_MUTACAO_ENGINE",
    "RUNG_C4_ADVERSO",
    "SHAPE_DEGRADACAO_SEM_DOSSIE",
    "SHAPE_ESCALONAMENTO_HUMANO",
    "SHAPE_INCIDENTE_FALHA_FECHADA",
    "SHAPE_LACUNA_DECLARADA",
    "SHAPE_LEITURA_INCONCLUSIVA",
    "SHAPE_ROTA_DMN_INDISPONIVEL",
    "SHAPE_ROTA_LLM_INDISPONIVEL",
    "ActionClassSpec",
    "OperationSpec",
    "agent_action_ref",
    "denial_shape_for",
    "lookup_class",
    "lookup_operation",
]
