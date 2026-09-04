"""Rafael Nogueira — Analista de Autorizacao Previa Agent (Phase 1, AUTH, T1.11/defect B6).

Journey (mirrors the v1 donor's structure, READ-ONLY reference `Maezo-Healthcare-Plan
src/maezo/agents/rafael/graph.py`, adapted to v2's flatter seam set — same rationale as
`agents/helena/graph.py`'s module docstring):

    receive -> gather -> assess -> {auto_approve | human_auditor} -> start_process -> complete

`assess` evaluates `auth_admissibility` (FIRST hit policy; no denial output by design — the
contract's own invariant) and, when it resolves `SEGUE_ANALISE`, `auth_auto_approval` (also no
denial output — only `AUTO_APROVAR` | `ANALISE_HUMANA`). `auth_sla` is evaluated unconditionally
and is PURELY informative (feeds the dossier; never affects routing). Rafael then starts
SP-OP-AUTH-001 idempotently (business key `AUTH-{tenant_id}-{numero_guia_tiss}`) with the
DMN-derived route recorded as a process variable — the BPMN's OWN `businessRuleTask`s
(`BRT_Admissibilidade`/`BRT_AutoApproval`) independently re-evaluate the same tables from the
process variables Rafael supplied, and it is THAT engine-side gateway
(`GW_AutoAprovacao`, condition `${{auto_aprovacao.recomendacao == 'AUTO_APROVAR'}}`) that
actually drives the BPMN to either the automatic-issuance path or `UT_AnaliseMedicoAuditor`
(candidate group `medico-auditor`, declared statically in the BPMN — Rafael's `start_process`
node does nothing beyond POSTing the start with the right variables; it never explicitly
creates/assigns a task).

L0 HARD INVARIANT (ADR-0005/0008, contract SP-OP-AUTH-001 §Invariante L0 hard): Rafael NEVER
denies coverage and NEVER makes the coverage decision. A denial is born EXCLUSIVELY in the
human User Task (`UT_AnaliseMedicoAuditor`). Enforced structurally here:
  - `Route` admits only `{"auto_approve", "human_auditor"}` — no deny variant exists in the type.
  - The assembled dossier's `decisao_cobertura` field is ALWAYS `None` (`_build_dossier`) — a
    guardrail making it explicit the decision belongs to the auditor, never to this code.
  - `dentro_teto_l2`/`dut_atendida`/`rede_credenciada`/`carencia_cumprida`/`beneficiario_ativo`/
    `documentacao_completa` all arrive PRE-RESOLVED by a deterministic worker upstream (contract
    SP-OP-AUTH-001's own variable table) — this graph CONSUMES them, never computes them (the
    `dentro_teto_l2` tenant-ceiling comparison in particular is `tools/workers/ceilings.py`'s job,
    explicitly FORBIDDEN territory for this change).

PHI discipline: Rafael's `security_zone` is `phi` end-to-end (`spec/agents/rafael/agent.yaml`) —
the one LLM call (`_build_dossier`'s narrative) passes `phi=True` (ADR-0006/ADR-0017/T1.7).

LABELED BOUNDARIES (this build, disclosed — never fabricated):
- `gather` uses v2's generic `FhirServer` (`tools/mcp_fhir/server.py`: `read_resource`/
  `search_resources`) via a thin `FhirReader` seam — NOT a dedicated `read_patient`/
  `search_coverage` tool like the v1 donor's (a real shape drift, disclosed, not hidden).
  CORRECTED (`grep -n '"rafael"' gateway/tool_registry.py`, `_FHIR_ADAPTER_BY_AGENT`): this seam
  IS PEP-gated — `gateway/tool_registry.py::build_agent_seams` wraps Rafael's `FhirServerReader.
  read_patient` in `gateway/seams/fhir.py::GatedFhirReader`, and both live composition roots
  (`runtime/agent_runtime/service.py::_build_tool_deps`, `platform/webhooks/service.py`) build
  Rafael's `fhir` dependency through it — the prior "v2 has no `ToolRegistry`/PEP gateway wiring
  for agent tool calls yet" claim is false today. `gather` is best-effort and NEVER blocks
  routing on a FHIR failure (mirrors the donor exactly) — a missing/unreachable FHIR endpoint
  degrades to a dossier gap note, never a fabricated fact. When no `fhir` dependency is injected
  at all (`config.get("fhir")` is `None`), `gather` records an explicit gap note rather than
  silently producing empty facts that look like "no findings".
- No episodic memory write (ADR-0002) — same rationale as Helena's graph.
- Cross-agent A2A delegation (Helena -> Rafael `authorization.analyze`) IS wired and LIVE in
  this build. CORRECTED (CC-04, fleet audit) — the prior text here claimed v2's `a2a/` package
  had no `DelegationEnvelope`/`DelegationDispatcher`; both exist and are fully built/tested
  (`a2a/delegation.py::DelegationEnvelope`, `a2a/dispatcher.py::DelegationDispatcher`, exported
  from `maezo.a2a`). Rafael is in fact the platform's proof-of-life edge
  (`docs/design/A2A-dispatcher-card-signing.md` §9.2): `agents/rafael/delegation.py`'s
  `make_rafael_handler` is the TARGET handler assembled by `runtime/agent_runtime/
  a2a_composition.py::build_auth_delegation_dispatcher` (`_EDGE_AGENT_IDS = ("helena",
  "rafael")`, `handlers={"rafael": handler}`), and Helena originates the envelope via
  `agents/helena/delegation.py`'s `DelegationDispatcher.delegate(envelope)`. Rafael's graph is
  ALSO invoked directly with an already-assembled auth-request state in the integration tests
  (both paths exist; neither is a disclosed gap anymore).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final, Literal, Protocol, TypedDict, cast

import structlog
from langgraph.graph import END, START, StateGraph

from maezo.platform.privacy.dossier_zone import dossier_narrative_requires_phi_zone
from maezo.runtime.inference import InferenceProvider
from maezo.tools.mcp_cibseven.transport import (
    AgentDecisionProvenance,
    AuditStartSink,
    CibSevenError,
    CibSevenTransport,
    start_process_idempotent,
)
from maezo.tools.workers.dmn_transport import (
    DmnEvaluationError,
    DmnNoResultError,
    DmnTransport,
    first_row,
)

from .prompts import DOSSIER_PROMPT_VERSION, SYSTEM_PROMPT_VERSION, dossier_prompt

PROCESS_KEY = "SP-OP-AUTH-001"

Route = Literal["auto_approve", "human_auditor"]
Admissibilidade = Literal["NAO_REQUER", "PENDENTE_DOCUMENTACAO", "SEGUE_ANALISE"]
Recomendacao = Literal["AUTO_APROVAR", "ANALISE_HUMANA"]
MotivoAuditor = Literal["dmn_analise_humana", "documentacao_pendente", "dmn_indisponivel", "outro"]
CategoriaProcedimento = Literal[
    "consulta", "exame_simples", "exame_especial", "terapia", "internacao", "opme", "alta_complexidade"
]

#: O que `categoria_procedimento` vale quando o chamador NAO informa — UM lugar, de proposito.
#:
#: Havia quatro sitios com dois valores diferentes: tres assumiam `"consulta"` e o que alimenta
#: o dossie assumia `""`. A divergencia produzia o sintoma exato que o time de automacoes
#: reportou em 27/08 — o dossie dizia "categoria nao informada" enquanto a DMN de prazo era
#: avaliada como consulta e devolvia 5 dias uteis. Dois textos sobre o mesmo caso, os dois
#: gerados pelo mesmo turno, discordando entre si.
#:
#: O valor escolhido e' VAZIO, e nao `"consulta"`. Assumir consulta e' afirmar uma categoria
#: que ninguem informou, e a categoria decide o prazo legal; vazio cai na linha fail-safe da
#: tabela de SLA ("prazo conservador na ausencia de classificacao"), que e' o que a ausencia
#: merece. A rota de ingresso ja' RECUSA o campo vazio desde 25/08, entao este default so' e'
#: alcancavel pela delegacao A2A — e la' tambem e' melhor cair no conservador.
CATEGORIA_PROCEDIMENTO_AUSENTE: Final[str] = ""

DMN_ADMISSIBILITY = "auth_admissibility"
DMN_AUTO_APPROVAL = "auth_auto_approval"
DMN_SLA = "auth_sla"

logger = structlog.get_logger(__name__)


class FhirReader(Protocol):
    """Best-effort FHIR read seam (`gather`). See module docstring's labeled boundary."""

    async def read_patient(self, patient_id: str) -> dict[str, Any]: ...

    async def search_coverage(self, patient_id: str) -> Any: ...


class RafaelState(TypedDict, total=False):
    """Case state. Pre-resolved booleans arrive from a deterministic upstream worker — Rafael
    CONSUMES them, never computes them (module docstring's L0-hard invariant)."""

    # Runtime identifiers.
    tenant_id: str
    numero_guia_tiss: str
    beneficiario_pseudo_id: str
    prestador_id: str
    canal: str  # a2a | portal_tiss

    # Request data (contract SP-OP-AUTH-001 input variables).
    codigo_procedimento_tuss: str
    categoria_procedimento: CategoriaProcedimento
    carater_atendimento: str  # urgencia | eletivo
    valor_estimado_brl: float
    cid10: str
    documentos_refs: list[dict[str, Any]]

    # Pre-resolved booleans (deterministic worker upstream) — CONSUMED, never computed here.
    requer_autorizacao: bool
    documentacao_completa: bool
    beneficiario_ativo: bool
    carencia_cumprida: bool
    dut_atendida: bool
    dentro_teto_l2: bool
    rede_credenciada: bool

    # FHIR references for `gather` (never raw PHI).
    coverage_ref: str
    patient_ref: str

    # Filled by `gather`.
    gathered: bool
    coverage_facts: dict[str, Any] | list[Any]
    patient_facts: dict[str, Any]
    gather_notes: list[str]

    # Filled by `assess`.
    admissibilidade: Admissibilidade
    recomendacao_auto: Recomendacao
    sla_analise: str
    sla_alerta: str
    dmn_refs: dict[str, str]
    dmn_error: str
    route: Route
    motivo_auditor: MotivoAuditor | None

    # Filled by `auto_approve`/`human_auditor`.
    dossier: dict[str, Any]

    # Filled by `start_process`.
    process_started: bool
    business_key: str
    process_ref: dict[str, Any]

    # Output.
    desfecho: str  # encaminhado_auditor | aprovacao_automatica_solicitada | nao_requer
    error: str


def _business_key(state: RafaelState) -> str:
    """Idempotent business key per contract: `AUTH-{tenant_id}-{numero_guia_tiss}`."""
    return f"AUTH-{state.get('tenant_id', '')}-{state.get('numero_guia_tiss', '')}"


# --- Input/output field split + input-boundary gate (T1.11 caller-planted read-through fix) ---
#
# RafaelState carries TWO disjoint classes of key:
#   * INPUT-ONLY  (`RAFAEL_INPUT_FIELDS`): the ONLY keys a caller/upstream (A2A delegation, the
#     portal-TISS worker) may set — request data + the PRE-RESOLVED worker booleans this graph
#     legitimately CONSUMES (never computes; see module docstring's L0-hard invariant).
#   * OUTPUT-ONLY (`_RAFAEL_NEUTRAL_OUTPUTS`): keys OWNED by this graph's nodes (route,
#     admissibilidade, recomendacao_auto, sla_*, dmn_refs, dossier, process_*, ...). A caller
#     must NEVER set one — a planted output field is an injection.
#
# TWO defenses, both fail-closed (mirrors `agents/helena/graph.py`):
#   1. Per-graph entry sanitization — `receive` resets EVERY output-only field to its neutral
#      default before any downstream node runs. This closes the admissibility-DMN-down early
#      return that omits `sla_analise`/`recomendacao_auto` (a planted value would otherwise
#      survive into the dossier via `_build_dossier`).
#   2. Input-boundary gate — a construction seam assembles state ONLY through the typed
#      `new_rafael_state` constructor or the `gate_inbound_state` allowlist filter, so an
#      output-only key can never enter the state dict. Rafael has NO live A2A/delegation seam in
#      this build (module docstring's labeled boundary); the gate is provided HERE so it is
#      enforced the moment that seam lands.
#
# The completeness guard below fails at import time if a newly added RafaelState field is not
# classified into exactly one of the two sets — "any missed key is a hole".

RAFAEL_INPUT_FIELDS: frozenset[str] = frozenset(
    {
        "tenant_id",
        "numero_guia_tiss",
        "beneficiario_pseudo_id",
        "prestador_id",
        "canal",
        "codigo_procedimento_tuss",
        "categoria_procedimento",
        "carater_atendimento",
        "valor_estimado_brl",
        "cid10",
        "documentos_refs",
        "requer_autorizacao",
        "documentacao_completa",
        "beneficiario_ativo",
        "carencia_cumprida",
        "dut_atendida",
        "dentro_teto_l2",
        "rede_credenciada",
        "coverage_ref",
        "patient_ref",
    }
)

# Neutral default for every OUTPUT-ONLY field. `receive` writes a copy of this over the incoming
# state. Container-typed outputs use `None` (they are ALWAYS overwritten by `gather`/`assess`
# before any read); the string SLA fields default to `""` and `recomendacao_auto` to `None` —
# the exact values `_build_dossier` reads on the admissibility-DMN-down path, so a planted value
# is replaced by the correct neutral rather than merely dropped.
_RAFAEL_NEUTRAL_OUTPUTS: dict[str, Any] = {
    "gathered": False,
    "coverage_facts": None,
    "patient_facts": None,
    "gather_notes": None,
    "admissibilidade": None,
    "recomendacao_auto": None,
    "sla_analise": "",
    "sla_alerta": "",
    "dmn_refs": None,
    "dmn_error": None,
    "route": None,
    "motivo_auditor": None,
    "dossier": None,
    "process_started": False,
    "business_key": None,
    "process_ref": None,
    "desfecho": None,
    "error": None,
}

_RAFAEL_ALL_FIELDS = RAFAEL_INPUT_FIELDS | frozenset(_RAFAEL_NEUTRAL_OUTPUTS)
if frozenset(RafaelState.__annotations__) != _RAFAEL_ALL_FIELDS:
    _missing = frozenset(RafaelState.__annotations__) - _RAFAEL_ALL_FIELDS
    _extra = _RAFAEL_ALL_FIELDS - frozenset(RafaelState.__annotations__)
    raise RuntimeError(
        "RafaelState input/output field split is incomplete (T1.11 input-boundary gate): "
        f"unclassified fields={sorted(_missing)} stale entries={sorted(_extra)} — every "
        "RafaelState key MUST be either an INPUT field or carry a neutral output default."
    )


def new_rafael_state(raw: Mapping[str, Any]) -> RafaelState:
    """Typed input-boundary constructor for a fresh Rafael case (T1.11).

    Accepts a raw mapping (the shape an A2A `authorization.analyze` delegation envelope or the
    portal-TISS worker would hand over) and returns a `RafaelState` containing ONLY
    `RAFAEL_INPUT_FIELDS` keys. An unknown key is a hard error — unlike the webhook edge, a
    delegation seam is an internal contract, so a stray key means a producer bug and must fail
    closed, LOUDLY, rather than be silently tolerated.
    """
    unknown = sorted(k for k in raw if k not in RAFAEL_INPUT_FIELDS)
    if unknown:
        raise ValueError(
            "new_rafael_state received non-input keys (T1.11 input-boundary gate): "
            f"{unknown} — only RAFAEL_INPUT_FIELDS may be set by a caller/delegation seam; "
            "output-only fields are owned by Rafael's graph nodes."
        )
    return cast(RafaelState, {k: raw[k] for k in RAFAEL_INPUT_FIELDS if k in raw})


def gate_inbound_state(raw: Mapping[str, Any]) -> RafaelState:
    """Fail-closed input allowlist (drop-and-log variant of `new_rafael_state`).

    Only `RAFAEL_INPUT_FIELDS` keys survive; every other key — any caller-planted output field —
    is DROPPED and logged. Use where tolerating benign upstream drift is preferable to raising
    (e.g. a lenient ingestion edge); use `new_rafael_state` on a strict internal delegation seam.
    """
    dropped = sorted(k for k in raw if k not in RAFAEL_INPUT_FIELDS)
    if dropped:
        logger.warning("rafael_inbound_output_fields_dropped", dropped=dropped)
    return cast(RafaelState, {k: raw[k] for k in RAFAEL_INPUT_FIELDS if k in raw})


#: Fatos BOOLEANOS do caso, com o nome que o medico-auditor reconhece.
#:
#: POR QUE ESTE MAPA EXISTE (achado de 24/08/2026, caso com prestador fora da rede)
#:
#: O dossie escreveu "Nao ha registro de verificacao de teto L2 ou rede credenciada" para um
#: caso em que a rede FOI verificada e deu FALSO. Verificado-e-desfavoravel virou
#: nao-verificado, e o texto apagou a unica informacao contraria do caso.
#:
#: A causa NAO era descarte na montagem — `_build_dossier` sempre passou `False` e `None`
#: adiante, os dois. A distincao morria na PASSAGEM PARA O MODELO: os fatos iam para o prompt
#: como repr de dicionario Python (`'rede_credenciada': False, 'dentro_teto_l2': None`) e nada
#: dizia ao modelo que um deles e fato apurado desfavoravel e o outro e ausencia de apuracao.
#: Num repr os dois parecem a mesma coisa: um valor "vazio".
#:
#: Entao o conserto e de RENDERIZACAO, nao de montagem. `_build_dossier` continua devolvendo o
#: dicionario `fatos` intacto — a procedencia de auditoria (ADR-0007) nao muda; muda o que o
#: modelo LE. Quem for procurar o "descarte" no codigo nao vai achar, porque ele nunca existiu.
_FATOS_BOOLEANOS: Final[dict[str, str]] = {
    "beneficiario_ativo": "beneficiario com plano ativo",
    "carencia_cumprida": "carencia cumprida",
    "dut_atendida": "diretriz de utilizacao (DUT) atendida",
    "rede_credenciada": "prestador na rede credenciada",
    "documentacao_completa": "documentacao completa",
}


def render_fatos_para_prompt(facts: dict[str, Any]) -> str:
    """Serializa os fatos NOMEANDO o estado de cada booleano, em vez de despejar o dicionario.

    Tres estados, tres formas visualmente distintas — e so o desfavoravel carrega instrucao,
    porque foi exatamente esse que sumiu do texto quando os tres colapsavam num `repr()`.

    O `is True` / `is False` e deliberado: `1`, `"nao"` e `[]` NAO sao fatos apurados, e um
    `bool()` os converteria em afirmacao. Qualquer coisa que nao seja booleano cai em NAO
    VERIFICADO, que e a leitura segura.
    """
    linhas: list[str] = []
    for chave, rotulo in _FATOS_BOOLEANOS.items():
        valor = facts.get(chave)
        # A marcacao e' curta e SEM instrucao embutida, e isso e' conserto de 25/08/2026:
        # a versao anterior escrevia `[FATO DESFAVORAVEL: cite nomeando]` no fim da linha, e o
        # modelo COPIAVA a frase em caixa alta para a narrativa. Medido em 4 casos: vazou em 1
        # ("FATO DESFAVORAVEL: beneficiario sem plano ativo"). Marcacao que parece frase pronta
        # convida a ser reproduzida; token curto nao. A regra de como tratar cada estado mora
        # no prompt, que tambem proibe copiar a marcacao.
        if valor is True:
            linhas.append(f"  SIM       {rotulo}")
        elif valor is False:
            linhas.append(f"  NAO       {rotulo}")
        else:
            linhas.append(f"  SEM DADO  {rotulo}")

    # O TETO NAO E' FATO DO AGENTE, e por isso nao esta em `_FATOS_BOOLEANOS`.
    #
    # C-02, reportado pelo time de automacoes em 27/08: o dossie dizia "nao foi verificado se o
    # valor esta dentro do teto" enquanto o motor tinha verificado e aprovado — e era o unico
    # criterio que passava. As duas afirmacoes eram verdadeiras em momentos diferentes, e e' ai'
    # que estava o defeito: o dossie e' escrito ANTES de `ST_ValidateAutoApprovalCriteria`
    # rodar, e o motor COMPUTA `dentro_teto_l2` por conta propria (`ceilings.py`), sobrescrevendo
    # qualquer valor de entrada. O docstring deste modulo ja' declarava o teto como territorio
    # PROIBIDO para este grafo.
    #
    # Entao o agente para de afirmar sobre ele. A linha continua no dossie, porque o auditor
    # precisa saber que o criterio existe — mas dizendo de quem e' a apuracao.
    linhas.append("  APURADO PELO MOTOR  valor dentro do teto de aprovacao automatica")

    contexto = {k: v for k, v in facts.items() if k not in _FATOS_BOOLEANOS}
    corpo = "\n".join(linhas)
    return f"fatos apurados:\n{corpo}\n\ndemais dados do caso: {contexto}"


class RafaelGraph:
    """Wires Rafael's injected dependencies into a compilable `StateGraph[RafaelState]`."""

    def __init__(
        self,
        *,
        inference: InferenceProvider,
        dmn: DmnTransport,
        cibseven: CibSevenTransport,
        audit_sink: AuditStartSink,
        fhir: FhirReader | None = None,
        agent_version: str = "rafael@v0",
    ) -> None:
        self._llm = inference
        self._dmn = dmn
        self._cibseven = cibseven
        # T-C2 fence: required durable ADR-0007 sink for the SP-OP-AUTH-001 start (audit-before-effect).
        self._audit_sink = audit_sink
        self._model_id = getattr(inference, "model_id", None)
        self._fhir = fhir
        self._agent_version = agent_version

    # -- Nodes ----------------------------------------------------------------------------

    async def receive(self, state: RafaelState) -> dict[str, Any]:
        """Turn start: (1) ENTRY SANITIZATION — reset EVERY output-only field to its neutral
        default so no caller/upstream-planted value can be read by a downstream node (T1.11,
        layer 1); (2) assign the idempotent business key up front (contract SP-OP-AUTH-001).

        In particular this closes the admissibility-DMN-down early return in `assess`, which does
        not set `sla_analise`/`recomendacao_auto`: post-reset those carry their neutral defaults
        (`""`/`None`) instead of a planted value, so `_build_dossier`/`_contract_variables` can
        never ship a forged SLA or auto-recommendation into the engine dossier.
        """
        reset: dict[str, Any] = dict(_RAFAEL_NEUTRAL_OUTPUTS)
        reset["business_key"] = _business_key(state)
        return reset

    async def gather(self, state: RafaelState) -> dict[str, Any]:
        """Best-effort FHIR enrichment — NEVER blocks routing (module docstring)."""
        notes: list[str] = []
        coverage_facts: dict[str, Any] | list[Any] = {}
        patient_facts: dict[str, Any] = {}

        if self._fhir is None:
            notes.append(
                "FHIR reader not configured for this build (labeled boundary — see graph.py "
                "module docstring); dossier proceeds with pre-resolved worker facts only."
            )
            return {
                "gathered": True,
                "coverage_facts": coverage_facts,
                "patient_facts": patient_facts,
                "gather_notes": notes,
            }

        coverage_ref = state.get("coverage_ref") or state.get("beneficiario_pseudo_id", "")
        try:
            coverage_facts = await self._fhir.search_coverage(coverage_ref)
        except Exception as exc:  # noqa: BLE001 — best-effort enrichment, never fatal.
            # CLASS TOKEN ONLY (CC-10): `str(exc)` from a FHIR client typically echoes the URL /
            # id it failed on — i.e. the `coverage_ref` argument — and this note is copied into
            # the dossier prompt AND into the engine-sealed `dossie_rafael` (general zone,
            # ADR-0006/ADR-0007). The full trace stays in the structured log, the diagnostic
            # channel, never in the note.
            logger.warning("rafael_fhir_cobertura_indisponivel", exc_info=True)
            notes.append(f"cobertura FHIR indisponivel: {type(exc).__name__}")

        patient_ref = state.get("patient_ref")
        if patient_ref:
            try:
                patient_facts = await self._fhir.read_patient(patient_ref)
            except Exception as exc:  # noqa: BLE001 — best-effort enrichment, never fatal.
                # CLASS TOKEN ONLY (CC-10) — same rationale as the coverage note above; here the
                # leaked argument would be `patient_ref` itself.
                logger.warning("rafael_fhir_beneficiario_indisponivel", exc_info=True)
                notes.append(f"beneficiario FHIR indisponivel: {type(exc).__name__}")

        return {
            "gathered": True,
            "coverage_facts": coverage_facts,
            "patient_facts": patient_facts,
            "gather_notes": notes,
        }

    async def assess(self, state: RafaelState) -> dict[str, Any]:
        """Evaluate `auth_admissibility` -> (route to human OR) `auth_auto_approval`.

        `auth_sla` is evaluated unconditionally and is PURELY informative (never affects
        `route`). Neither DMN has a denial output by contract design — this method never
        produces anything but `{"auto_approve", "human_auditor"}` for `route`.
        """
        dmn_refs: dict[str, str] = {}

        admis_in = {
            "requer_autorizacao": bool(state.get("requer_autorizacao", True)),
            "documentacao_completa": bool(state.get("documentacao_completa", False)),
            "beneficiario_ativo": bool(state.get("beneficiario_ativo", False)),
            "carencia_cumprida": bool(state.get("carencia_cumprida", False)),
        }
        admis_result = await self._evaluate_dmn(DMN_ADMISSIBILITY, admis_in)
        if admis_result.get("error"):
            return {
                "route": "human_auditor",
                "motivo_auditor": "dmn_indisponivel",
                "admissibilidade": "SEGUE_ANALISE",
                "dmn_refs": dmn_refs,
                "dmn_error": admis_result["error"],
                "desfecho": "encaminhado_auditor",
            }
        admissibilidade = cast(Admissibilidade, str(admis_result["row"].get("resultado", "SEGUE_ANALISE")))
        dmn_refs[DMN_ADMISSIBILITY] = admis_result["ref"]

        sla_result = await self._evaluate_dmn(
            DMN_SLA,
            {
                "carater_atendimento": str(state.get("carater_atendimento", "eletivo")),
                "categoria_procedimento": str(
                    state.get("categoria_procedimento", CATEGORIA_PROCEDIMENTO_AUSENTE)
                ),
            },
        )
        sla_row = sla_result.get("row", {}) if not sla_result.get("error") else {}
        if sla_result.get("ref"):
            dmn_refs[DMN_SLA] = sla_result["ref"]
        base: dict[str, Any] = {
            "admissibilidade": admissibilidade,
            "dmn_refs": dmn_refs,
            "sla_analise": str(sla_row.get("sla_analise", "")),
            "sla_alerta": str(sla_row.get("sla_alerta", "")),
        }

        if admissibilidade == "PENDENTE_DOCUMENTACAO":
            return {
                **base,
                "route": "human_auditor",
                "motivo_auditor": "documentacao_pendente",
                "desfecho": "encaminhado_auditor",
            }
        if admissibilidade == "NAO_REQUER":
            # Still routes through the human path's dossier/start_process — the PROCESS itself
            # (not this code) resolves "does not require authorization" as a terminal outcome.
            return {
                **base,
                "route": "human_auditor",
                "motivo_auditor": "outro",
                "recomendacao_auto": "ANALISE_HUMANA",
                "desfecho": "nao_requer",
            }

        # GAP-AUTH-4: `auth_auto_approval` v0.2.0 no longer reads `dut_atendida`/
        # `dentro_teto_l2`/`rede_credenciada`; it reads the four criteria a DETERMINISTIC worker
        # (`operadora.auth.validate_auto_criteria`, `ST_ValidateAutoApprovalCriteria`) computes,
        # plus `auto_criteria_verificado` — proof that the worker RAN. This agent does not run
        # that worker and MUST NOT: computing coverage/ceiling/carencia facts is exactly the
        # deterministic work ADR-0005/ADR-0018 keep out of an LLM graph (Rafael consumes DMN
        # results, never computes the facts they consume).
        #
        # Consequence, stated plainly: unless the deterministic worker has already written these
        # variables into the state Rafael was handed, `auto_criteria_verificado` is False and the
        # table's catch-all resolves to ANALISE_HUMANA. **The execution fence applies to the
        # agent route too** — Rafael cannot recommend an automatic approval on facts nobody
        # verified. That is the intended behaviour, not a regression; the previous shape let this
        # graph feed the table three unverified booleans straight from its own state.
        auto_in = {
            "auto_criteria_verificado": state.get("auto_criteria_verificado") is True,
            "criterio_tecnico_ok": state.get("criterio_tecnico_ok") is True,
            "criterio_financeiro_ok": state.get("criterio_financeiro_ok") is True,
            "criterio_regulatorio_ok": state.get("criterio_regulatorio_ok") is True,
            "criterio_contratual_ok": state.get("criterio_contratual_ok") is True,
            "carater_atendimento": str(state.get("carater_atendimento", "eletivo")),
        }
        auto_result = await self._evaluate_dmn(DMN_AUTO_APPROVAL, auto_in)
        if auto_result.get("error"):
            return {
                **base,
                "route": "human_auditor",
                "motivo_auditor": "dmn_indisponivel",
                "dmn_error": auto_result["error"],
                "desfecho": "encaminhado_auditor",
            }
        dmn_refs[DMN_AUTO_APPROVAL] = auto_result["ref"]
        base["dmn_refs"] = dmn_refs
        recomendacao = cast(Recomendacao, str(auto_result["row"].get("recomendacao", "ANALISE_HUMANA")))
        base["recomendacao_auto"] = recomendacao

        if recomendacao == "AUTO_APROVAR":
            # The ONLY L2 path. If the DMN did not say AUTO_APROVAR, this branch is never taken.
            return {
                **base,
                "route": "auto_approve",
                "motivo_auditor": None,
                "desfecho": "aprovacao_automatica_solicitada",
            }
        return {
            **base,
            "route": "human_auditor",
            "motivo_auditor": "dmn_analise_humana",
            "desfecho": "encaminhado_auditor",
        }

    async def auto_approve(self, state: RafaelState) -> dict[str, Any]:
        """The L2 automatic path. Rafael does NOT issue the authorization itself — he starts
        SP-OP-AUTH-001, whose automatic path (engine + `operadora.auth.issue_authorization`
        worker) emits the TISS guide. This node only finishes assembling the dossier."""
        return {"dossier": await self._build_dossier(state)}

    async def human_auditor(self, state: RafaelState) -> dict[str, Any]:
        """Routes to the medico-auditor. NONE of these paths is a denial — a denial is born
        SOLELY in the `UT_AnaliseMedicoAuditor` User Task; Rafael only instructs the case."""
        return {"dossier": await self._build_dossier(state)}

    async def start_process(self, state: RafaelState) -> dict[str, Any]:
        """Start SP-OP-AUTH-001 idempotently (business key `AUTH-{tenant}-{guia}`)."""
        business_key = state.get("business_key") or _business_key(state)
        variables = self._contract_variables(state)
        provenance = AgentDecisionProvenance(
            agent_id="rafael",
            agent_version=self._agent_version,
            tenant_id=state.get("tenant_id", ""),
            model_id=self._model_id,
            prompt_version=SYSTEM_PROMPT_VERSION,
            decision_basis={
                "route": state.get("route", ""),
                "recomendacao_auto": state.get("recomendacao_auto", ""),
                "desfecho": state.get("desfecho", ""),
                "motivo_auditor": state.get("motivo_auditor") or "",
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
            return {
                "process_started": False,
                "business_key": business_key,
                "error": f"start_process indisponivel: {exc}",
            }
        return {
            "process_started": True,
            "business_key": business_key,
            "process_ref": {
                "instance_id": instance.instance_id,
                "state": instance.state,
                "already_existed": instance.already_existed,
            },
        }

    async def complete(self, state: RafaelState) -> dict[str, Any]:
        """Terminal node — no further computation; `desfecho` was already set by `assess`."""
        return {}

    # -- Conditional routing ------------------------------------------------------------------

    @staticmethod
    def _route(state: RafaelState) -> str:
        # FAIL-SAFE: on absence/doubt, ALWAYS human (never auto_approve by omission).
        return "auto_approve" if state.get("route") == "auto_approve" else "human_auditor"

    # -- DMN (auth_admissibility / auth_sla / auth_auto_approval; none has a denial output) ----

    async def _evaluate_dmn(self, table: str, dmn_input: dict[str, Any]) -> dict[str, Any]:
        try:
            rows, version = await self._dmn.evaluate(table, dmn_input)
            row = first_row(rows, table, dmn_input)
        except (DmnEvaluationError, DmnNoResultError) as exc:
            return {"error": f"DMN `{table}` indisponivel: {exc}"}
        # See `helena/graph.py::_evaluate_dmn` for why this cites the decision-definition id
        # (ADR-0028 §2) rather than a rule id the engine's evaluate response never returns.
        return {"row": row, "ref": f"{table}#{version.id}"}

    # -- Dossier assembly (ADR-0007 audit provenance; L0-hard structural guardrail) ------------

    async def _build_dossier(self, state: RafaelState) -> dict[str, Any]:
        route = state.get("route", "human_auditor")
        motivo_auditor = state.get("motivo_auditor") if route == "human_auditor" else None
        facts = {
            "numero_guia_tiss": state.get("numero_guia_tiss", ""),
            "codigo_procedimento_tuss": state.get("codigo_procedimento_tuss", ""),
            "categoria_procedimento": state.get("categoria_procedimento", CATEGORIA_PROCEDIMENTO_AUSENTE),
            "carater_atendimento": state.get("carater_atendimento", ""),
            "valor_estimado_brl": state.get("valor_estimado_brl", 0.0),
            "cid10": state.get("cid10"),
            "beneficiario_ativo": state.get("beneficiario_ativo"),
            "carencia_cumprida": state.get("carencia_cumprida"),
            "dut_atendida": state.get("dut_atendida"),
            "dentro_teto_l2": state.get("dentro_teto_l2"),
            "rede_credenciada": state.get("rede_credenciada"),
            "documentacao_completa": state.get("documentacao_completa"),
            "admissibilidade": state.get("admissibilidade"),
            "recomendacao_auto": state.get("recomendacao_auto"),
            "dmn_refs": state.get("dmn_refs", {}),
            "sla_analise": state.get("sla_analise", ""),
            "lacunas_enriquecimento": state.get("gather_notes", []),
        }
        # `render_fatos_para_prompt` no lugar de `fatos={facts}`: ver `_FATOS_BOOLEANOS`.
        # O dicionario `facts` segue intacto no dossie devolvido logo abaixo.
        prompt = (
            f"{dossier_prompt()}\n\nroute={route} motivo_auditor={motivo_auditor}\n"
            f"{render_fatos_para_prompt(facts)}"
        )
        try:
            # `phi` NAO e' mais literal aqui. A pergunta "a narrativa do dossie e'
            # dado da zona PHI ou dado pseudonimizado da zona geral?" e' juridica e
            # clinica, e a resposta mora num artefato que o DPO e o medico auditor
            # ratificam (`spec/policies/phi/dossier-narrative-zone.yaml`).
            #
            # Fail-closed: sem ratificacao valida a funcao devolve True e este e'
            # exatamente o `phi=True` de antes. Ativar NAO exige editar Python.
            #
            # Cuidado ao ler o `except` abaixo: com `phi=True` e um provedor de zona
            # geral, `PhiZoneRoutingError` cai nele e a narrativa vira "" EM SILENCIO.
            # Foi por isso que o interruptor precisou ser explicito: apontar o Rafael
            # para o Bedrock nao daria erro, daria dossie com narrativa vazia.
            narrativa = await self._llm.generate(
                prompt,
                phi=dossier_narrative_requires_phi_zone(),
                agent_id="rafael",
                tenant_id=state.get("tenant_id", ""),
            )
        except Exception:  # noqa: BLE001 — LLM failure never blocks the human/auto route.
            narrativa = ""
        return {
            "prompt_version": DOSSIER_PROMPT_VERSION,
            "route": route,
            "motivo_auditor": motivo_auditor,
            "fatos": facts,
            "dmn_decision_refs": state.get("dmn_refs", {}),
            "narrativa": narrativa,
            "documentos_refs": state.get("documentos_refs") or [],
            # STRUCTURAL GUARDRAIL (L0 hard): the dossier NEVER carries a coverage decision.
            "decisao_cobertura": None,
        }

    def _contract_variables(self, state: RafaelState) -> dict[str, Any]:
        variables: dict[str, Any] = {
            "tenant_id": state.get("tenant_id", ""),
            "numero_guia_tiss": state.get("numero_guia_tiss", ""),
            "beneficiario_pseudo_id": state.get("beneficiario_pseudo_id", ""),
            "prestador_id": state.get("prestador_id", ""),
            "codigo_procedimento_tuss": state.get("codigo_procedimento_tuss", ""),
            "categoria_procedimento": str(
                state.get("categoria_procedimento", CATEGORIA_PROCEDIMENTO_AUSENTE)
            ),
            "carater_atendimento": str(state.get("carater_atendimento", "eletivo")),
            "valor_estimado_brl": float(state.get("valor_estimado_brl", 0.0)),
            "documentos_refs": state.get("documentos_refs") or [],
            "requer_autorizacao": bool(state.get("requer_autorizacao", True)),
            "documentacao_completa": bool(state.get("documentacao_completa", False)),
            "beneficiario_ativo": bool(state.get("beneficiario_ativo", False)),
            "carencia_cumprida": bool(state.get("carencia_cumprida", False)),
            "dut_atendida": bool(state.get("dut_atendida", False)),
            "dentro_teto_l2": bool(state.get("dentro_teto_l2", False)),
            "rede_credenciada": bool(state.get("rede_credenciada", False)),
            "source_agent_id": "rafael",
            "source_agent_version": self._agent_version,
            "dossie_rafael": state.get("dossier") or {},
            "rafael_route": state.get("route", "human_auditor"),
        }
        cid10 = state.get("cid10")
        if cid10:
            variables["cid10"] = cid10
        if state.get("route") == "human_auditor":
            variables["motivo_encaminhamento"] = state.get("motivo_auditor") or "outro"
        dmn_refs = state.get("dmn_refs")
        if dmn_refs:
            variables["dmn_decision_refs"] = dmn_refs
        return variables

    # -- Graph assembly -----------------------------------------------------------------------

    def compile_graph(self) -> StateGraph[RafaelState]:
        g: StateGraph[RafaelState] = StateGraph(RafaelState)
        g.add_node("receive", self.receive)
        g.add_node("gather", self.gather)
        g.add_node("assess", self.assess)
        g.add_node("auto_approve", self.auto_approve)
        g.add_node("human_auditor", self.human_auditor)
        g.add_node("start_process", self.start_process)
        g.add_node("complete", self.complete)

        g.add_edge(START, "receive")
        g.add_edge("receive", "gather")
        g.add_edge("gather", "assess")
        g.add_conditional_edges(
            "assess", self._route, {"auto_approve": "auto_approve", "human_auditor": "human_auditor"}
        )
        g.add_edge("auto_approve", "start_process")
        g.add_edge("human_auditor", "start_process")
        g.add_edge("start_process", "complete")
        g.add_edge("complete", END)
        return g


def build(config: dict[str, Any] | None = None) -> StateGraph[RafaelState]:
    """Contract `_template/graph.py:build`, resolved by `AgentLoader`/`runtime.harness.Harness`.

    `config` MUST contain `inference` (ADR-0009), `dmn` (ADR-0028/T1.5), `cibseven`
    (ADR-0001/T1.11). `fhir` is OPTIONAL (see module docstring's labeled boundary) — its absence
    never fails the build, only degrades `gather` to a disclosed gap note.

    Fail-closed: missing a REQUIRED dependency raises `ValueError` at build time.
    """
    cfg = config or {}
    inference = cfg.get("inference")
    dmn = cfg.get("dmn")
    cibseven = cfg.get("cibseven")
    audit_sink = cfg.get("audit_sink")
    missing = [
        name
        for name, value in (
            ("inference", inference),
            ("dmn", dmn),
            ("cibseven", cibseven),
            ("audit_sink", audit_sink),
        )
        if value is None
    ]
    if missing:
        raise ValueError(
            f"Rafael build(config) is missing required dependencies: {missing} "
            "(ADR-0001/0007/0009/0028 — inference/dmn/cibseven/audit_sink must all be injected; "
            "audit_sink is the T-C2 fence — no process start without a durable sink)"
        )
    agent_version = str(cfg.get("agent_version", "rafael@v0"))
    return RafaelGraph(
        inference=cast(InferenceProvider, inference),
        dmn=cast(DmnTransport, dmn),
        cibseven=cast(CibSevenTransport, cibseven),
        audit_sink=cast(AuditStartSink, audit_sink),
        fhir=cast("FhirReader | None", cfg.get("fhir")),
        agent_version=agent_version,
    ).compile_graph()


# Prompt versions exposed for audit/eval gating (ADR-0007/0009).
PROMPT_VERSIONS: dict[str, str] = {
    "system": SYSTEM_PROMPT_VERSION,
    "dossier": DOSSIER_PROMPT_VERSION,
}
