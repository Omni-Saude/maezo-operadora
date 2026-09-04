"""Carolina — Analista de Credenciamento e Gestao de Rede Agent (Phase 3, CRED, T1.12).

Journey (Carolina is explicitly the Rafael/Marina analog, READ-ONLY reference
`Maezo-Healthcare-Plan src/maezo/agents/carolina/graph.py`, cloning the same proven structure and
adapted to v2's flatter seam set — same rationale as `agents/rafael/graph.py`'s and
`agents/helena/graph.py`'s module docstrings):

    receive -> gather -> assess -> {auto_route | human_review} -> start_process -> finalize

Processo: SP-OP-CRED-001 ((Des)credenciamento de Prestador / Rede).

DUAS DIRECOES ADVERSAS, UM PROCESSO (contrato SP-OP-CRED-001 §invariante L1):
- direcao A (`descredenciamento`): descredenciar um prestador ja credenciado e EFEITO ADVERSO.
- direcao B (`credenciamento`): negar um pedido de credenciamento e EFEITO ADVERSO.
`direcao` no estado ORIENTA o ramo de roteamento — NUNCA decide o desfecho adverso. Credenciar um
NOVO prestador (direcao favoravel) NAO e adverso contra ninguem e PODE ser clerical (DMN
`cred_admissibility=CLERICAL_CREDENCIAR`) — a UNICA saida automatica que existe.

`assess` SEMPRE consulta as DMN deterministicas (ADR-0012). O LLM RACIOCINA sobre os resultados
(monta o dossie de analise) — NUNCA os substitui nem decide a regra. Sequencia das DMN (espelha o
BPMN `spec/processes/bpmn/SP-OP-CRED-001_Descredenciamento.bpmn`): `cred_admissibility` ->
(se SEGUE_ANALISE/ANALISE_HUMANA) `cred_route` -> (unconditional once `cred_route` is reached)
`cred_sla` (informative only) -> (so quando `cred_route` resolve ANALISE_DESCREDENCIAMENTO OU o
catch-all ANALISE_HUMANA — GAP-CRED-4/6, ver "Divergencias do donor" abaixo) `cred_prior_notice`
(RN 567, informative only).

L1 HARD INVARIANT (ADR-0008/0018, contrato SP-OP-CRED-001 §invariante L1, CI-enforced pendente
sign-off arquitetura+compliance): Carolina NUNCA descredencia um prestador, NUNCA nega um pedido
de credenciamento, NUNCA decide clinicamente/regulatoriamente o merito, NUNCA acusa fraude. O
tipo `Route` NAO TEM variante adversa — so `auto_route` (roteamento NEUTRO: credenciamento
clerical favoravel) e `human_review` (fail-safe sempre disponivel, cobre AMBAS as direcoes
adversas). NENHUMA saida automatica nega/descredencia: a negativa de credenciamento SO nasce na
User Task `UT_AnaliseCredenciamento` (`decisao_cred=NEGAR_CREDENCIAMENTO`) e o descredenciamento
SO na `UT_AnaliseDescredenciamento` (`decisao_cred=DESCREDENCIAR`, apos cure-window de
notificacao previa RN 567). Documentacao incompleta, licenca aparentemente irregular, fora de
criterios de rede, indicio de irregularidade, ambiguidade, DMN indisponivel ou prazo aparentemente
expirado TODOS fail-safe para a revisao humana. Invariante de KPI: `false_decredentialing_rate ==
0`. Os workers adversos (`operadora.cred.register_descredenciamento` / `register_cred_denial`,
`tools/workers/credenciamento.py`, T1.5 cutover) sao a ULTIMA linha de defesa
(`ERR_DECRED_NOT_HUMAN` / `ERR_CRED_DENIAL_NOT_HUMAN`) — este grafo nunca os invoca diretamente;
eles sao exercidos pelo ENGINE, do lado dos workers, apos a User Task humana.

R1 CYCLE-1 FIX (caller-planted assess-output fields — same defect class found on fernando):
`receive` (o UNICO no de entrada do grafo — START tem exatamente uma aresta, para `receive`;
regression-tested) sobrescreve TODO campo output-only de `CarolinaState` com seu default vazio
(`_sanitized_output_fields`) ANTES de gather/assess rodarem. Pre-fix, um caller plantando
`error` + `route="auto_route"` fazia gather/assess bailarem cedo (ZERO chamadas de DMN) e o
start_process embarcava a rota forjada nas variaveis do engine (contradizendo "assess SEMPRE
consulta as DMN"); `dmn_refs` plantado alcancava a trilha de auditoria ADR-0007
(`dmn_decision_refs`) e o dossie; campos plantados de assess (admissibilidade/roteamento/
exige_*/prazo/sla_*) alcancavam `dossie_carolina` nos atalhos clerical/fail-closed. Post-fix o
plantio e limpo na entrada: o gate DMN sempre roda de fato, e so valores produzidos pelos
proprios nos alcancam `_cred_facts`/`_contract_variables`. Os guards de bail
(`if state.get("error"): return {}`) sao seguros porque o unico `error` possivel quando eles
rodam e o do proprio `receive`.

PHI discipline: Carolina opera na Zona PHI (donor `agent.yaml`: `security_zone: phi`) — o unico
LLM call (`_build_dossier`'s narrativa) passa `phi=True` (ADR-0006/ADR-0017/T1.7), assim como
todo LLM call em `helena/graph.py` e `rafael/graph.py`.

Os nos sao curtos e idempotentes; checkpoint e ENTRE nos (ADR-0002). Toda acao externa via os
transports injetados (`DmnTransport`/`CibSevenTransport`), nunca SDK direto (mirrors
rafael/helena — v2 nao tem `ToolInvoker`/PEP-gateway wiring para chamadas de tool de agente
ainda, T2.4 gap). Os dados chegam pseudonimizados (ADR-0006) e o grafo nunca reverte isso.
**TASY write DROP** (ADR-0013): este grafo nunca escreve no Tasy — so consome fatos ja
pre-resolvidos por workers deterministicos upstream. O processo e iniciado de forma idempotente
pela business key `CRED-{tenant_id}-{prestador_id}` (variante `-{protocolo_cred}`; o start
consulta antes de criar).

DIVERGENCIAS DO DONOR (disclosed, per charter "where donor and v2 spec disagree, spec wins"):

1. **Indicio de irregularidade — sem bypass hand-coded.** O donor verifica
   `indicio_irregularidade_sinalizado` ANTES de chamar qualquer DMN e desvia direto para
   `human_review` sem nunca avaliar `cred_admissibility`/`cred_route`. As DMN v2 deployadas
   (`spec/processes/dmn/cred_admissibility.dmn` regra `r_indicio_segue`,
   `cred_route.dmn` regra `r_indicio_descred`) ja codificam essa precedencia como a PRIMEIRA
   regra (hitPolicy FIRST) das proprias tabelas — evaluar SEMPRE as DMN (nunca hand-forkar a
   precedencia em Python) e o que ADR-0012 exige e o que este grafo faz: `indicio_irregularidade_
   sinalizado=true` sempre entra como input de `cred_admissibility` (que retorna SEGUE_ANALISE
   via `r_indicio_segue`, nunca CLERICAL_CREDENCIAR) e de `cred_route` (que retorna
   ANALISE_DESCREDENCIAMENTO via `r_indicio_descred`, independente de `direcao`) — o mesmo
   resultado final do donor, so que decidido PELA DMN, nao por um `if` hand-coded que poderia
   divergir da tabela deployada.
2. **Catch-all `ANALISE_HUMANA` de `cred_route` roteia SEMPRE para o ramo de co-review
   (descredenciamento), nunca condicionado a `direcao`.** O donor condiciona o motivo/grupo do
   catch-all a `direcao` (`"analise_descredenciamento" if direcao == "descredenciamento" else
   "analise_credenciamento"`). O BPMN real (`GW_Natureza`, nota GAP-CRED-4/GAP-CRED-6 no XML)
   fixa o catch-all `ANALISE_HUMANA` para SEMPRE seguir o ramo de descredenciamento/co-review
   (candidate groups `gestao-rede,juridico-rede` em `UT_AnaliseDescredenciamento`, com
   `cred_prior_notice` avaliada) — o ramo mais conservador dos dois, e o unico que passa pela
   obrigacao de notificacao previa RN 567. Este grafo segue o BPMN corrigido, nao o donor.
3. **`cred_sla` e `cred_prior_notice` seguem exatamente a sequencia do BPMN**, nao a do donor:
   `cred_sla` e avaliada incondicionalmente uma vez que `cred_route` e alcancada (BPMN
   `BRT_CredSla`, ANTES de `GW_Natureza`) — nunca para `CLERICAL_CREDENCIAR`/
   `PENDENTE_DOCUMENTACAO` (que terminam antes de `BRT_Route`); `cred_prior_notice` e avaliada
   SO quando `cred_route` resolve `ANALISE_DESCREDENCIAMENTO` ou o catch-all `ANALISE_HUMANA`
   (BPMN `Flow_GWNat_Descredenciamento`), nunca para `ANALISE_CREDENCIAMENTO` puro. O donor
   avalia `cred_prior_notice` apenas por `direcao == "descredenciamento"`, nunca para o catch-all.
4. **`regiao_saude`/`especialidade` sao campos novos** (ausentes no donor) — exigidos pelo
   contrato v2 (`docs/processes/contracts/SP-OP-CRED-001.md` §Variaveis de entrada) para a
   choreografia CRED->ADEQUACAO (GAP-XPROC-2, `network_change_bridge`); passthrough quando
   presentes no estado.
5. **`finalize` NAO escreve memoria episodica** (donor's `finalize` chama
   `mcp-memory.read_write`). v2 nao tem `MemoryServer`/schema pgvector wired para grafos de
   agente ainda — mesmo labeled boundary de `helena/graph.py`/`rafael/graph.py`.
6. **`gather` usa um `SummaryReader` Protocol minimo** (best-effort, opcional) em vez do donor's
   `ToolInvoker`/PEP `mcp-fhir.read_patient_summary` — v2 nao tem esse tool dedicado ainda
   (mesmo labeled boundary do `FhirReader` de `rafael/graph.py`); reusa o adapter generico
   `agents.rafael.adapters.FhirServerReader` (`read_patient`) quando injetado pelo runtime.
7. **`spec/agents/carolina/agent.yaml` estava desalinhado e FOI CORRIGIDO neste mesmo PR**
   (correcao autorada pelo R1 spec-audit, aplicada aqui): a versao pre-T1.12 descrevia um
   "Analista de Revenue Cycle / Pagamentos" para `SP-OP-PAGTO-001` — duplicando a ownership de
   PAGTO do andre e deixando `SP-OP-CRED-001` orfao de agente (o audit confirmou: unica
   definicao mis-mapeada, 10/11 corretas). O arquivo agora e o port verbatim do donor
   (`Maezo-Healthcare-Plan .../carolina/agent.yaml`, READ-ONLY) + os 3 campos de scaffolding v2
   (`phase: 3`, `autonomy_level: L2`, `process_keys: [SP-OP-CRED-001]`) — mesma convencao de
   port verbatim dos demais Phase-3 (andre/beatriz/valentina/fernando, todos byte-identicos ao
   donor). PERSONA segue DRAFT (R-PERSONA-MAP — PO sign-off pendente, header do proprio yaml).

LABELED BOUNDARIES (this build, disclosed — never fabricated):
- No episodic memory write (ADR-0002) — see divergence #5 above.
- No cross-agent A2A delegation (`operadora.cred.prepare_dossier` -> Carolina
  `credentialing.analyze`) is wired in this build: v2's `a2a/` package has no
  `DelegationEnvelope`/`DelegationDispatcher` yet (same gap disclosed by `rafael/graph.py`).
  Carolina's graph is invoked directly with an already-assembled state (as the unit/integration
  tests do), not via a live engine-originated delegation.
- `gather`'s FHIR summary read is best-effort and OPTIONAL (see divergence #6) — its absence
  never blocks routing, only degrades the dossier with a disclosed gap note (mirrors rafael).
"""

from __future__ import annotations

from typing import Any, Final, Literal, Protocol, TypedDict, cast

from langgraph.graph import END, START, StateGraph

from maezo.runtime.inference import InferenceProvider
from maezo.runtime.prompt_format import render_fatos_para_prompt
from maezo.runtime.start_outcome import (
    notify_start_failure as emit_start_failure_notice,
)
from maezo.runtime.start_outcome import (
    route_after_start,
    start_failed_state,
)
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

PROCESS_KEY = "SP-OP-CRED-001"

# Direcao do pedido: orienta o ramo, NAO decide o desfecho adverso (module docstring).
Direcao = Literal["credenciamento", "descredenciamento"]

# Roteamento do grafo. ESTRUTURALMENTE SEM VARIANTE ADVERSA: nao existe um valor que negue um
# credenciamento nem descredencie um prestador.
#   - auto_route   : roteamento NEUTRO determinado pela DMN (CLERICAL_CREDENCIAR — credenciar um
#                    NOVO prestador, direcao favoravel) — NUNCA um efeito adverso.
#   - human_review : fail-safe — qualquer ambiguidade/DMN-indisponivel/licenca-irregular/fora-de-
#                    criterios/pendencia/indicio-de-irregularidade/descredenciamento vai ao
#                    humano. NUNCA uma negativa nem um descredenciamento.
Route = Literal["auto_route", "human_review"]

# Saidas da DMN cred_admissibility — SEM saida NEGAR/DESCREDENCIAR por design.
Admissibilidade = Literal[
    "CLERICAL_CREDENCIAR",
    "SEGUE_ANALISE",
    "PENDENTE_DOCUMENTACAO",
    "ANALISE_HUMANA",
]
# Saidas da DMN cred_route — SEM variante NEGAR/DESCREDENCIAR/AUTO_APROVAR_ADVERSO por design.
RoteamentoNatureza = Literal[
    "ANALISE_CREDENCIAMENTO",
    "ANALISE_DESCREDENCIAMENTO",
    "ANALISE_HUMANA",
]

# Motivo pelo qual o caso foi para a revisao humana. NENHUM destes e uma negativa/descredenciamento.
MotivoHumano = Literal[
    "analise_credenciamento",
    "analise_descredenciamento",
    "documentacao_pendente",
    "dmn_indisponivel",
    "outro",
]

DMN_CRED_ADMISSIBILITY = "cred_admissibility"
DMN_CRED_ROUTE = "cred_route"
DMN_CRED_PRIOR_NOTICE = "cred_prior_notice"
DMN_CRED_SLA = "cred_sla"


class SummaryReader(Protocol):
    """Best-effort provider/beneficiary-summary read seam (`gather`). See module docstring's
    divergence #6 — a minimal Protocol satisfied structurally by
    `agents.rafael.adapters.FhirServerReader.read_patient` when the runtime injects it."""

    async def read_patient(self, patient_id: str) -> dict[str, Any]: ...


class CarolinaState(TypedDict, total=False):
    """Estado da analise de (des)credenciamento. Campos preenchidos pelos nos.

    Os dados ja chegam pseudonimizados (Zona PHI, ADR-0006). Prestador (`prestador_id`) e dado
    cadastral PJ/PF, nao PHI de beneficiario; quando ha referencia a beneficiarios vinculados ela
    viaja pseudonimizada. As variaveis booleanas (`licenca_valida`, `documentacao_completa`,
    `dentro_criterios_rede`, `notificacao_previa_feita`, ...) chegam PRE-RESOLVIDAS por workers
    deterministicos (`tools/workers/credenciamento.py`, T1.5 cutover) — Carolina as CONSOME,
    nunca as calcula (ADR-0012), e NUNCA decide negar/descredenciar a partir delas.
    """

    # Identificadores de runtime / origem da task.
    tenant_id: str
    canal: str  # a2a | portal
    prestador_id: str  # chave de negocio (dado cadastral, nao PHI)
    protocolo_cred: str  # protocolo do ciclo (variante da business key)

    # --- Entradas do contrato (SP-OP-CRED-001) ---
    direcao: Direcao
    tipo_prestador: str  # pessoa_fisica | clinica | hospital | laboratorio | sadt | opme
    origem_solicitacao: str  # prestador | operadora | agente_carolina | auditoria_qualidade | juridico
    motivo_informado: str  # texto livre (contratualmente sem PHI de beneficiario)
    data_solicitacao_iso: str  # YYYY-MM-DD
    documentos_refs: list[dict[str, Any]]
    regiao_saude: str  # cadastral — compoe o fato network_changed (GAP-XPROC-2)
    especialidade: str  # cadastral (taxonomia TUSS/CBO) — idem

    # Pre-resolvidas por worker (FATOS — input determinista das DMN; NUNCA decidem negar).
    licenca_valida: bool
    documentacao_completa: bool
    dentro_criterios_rede: bool
    notificacao_previa_feita: bool  # so descredenciamento (RN 567)
    substituto_equivalente_identificado: bool  # so descredenciamento (RN 567)
    tem_beneficiarios_vinculados: bool  # entra na cred_prior_notice
    indicio_irregularidade_sinalizado: bool  # sinal INFORMATIVO (NUNCA decide) — so roteia a humano

    # Referencia para o resumo (gather). Nunca PHI cru.
    patient_summary_ref: str

    # Preenchidos por gather.
    gathered: bool
    summary_facts: dict[str, Any]
    gather_notes: list[str]

    # Preenchidos por assess (resultados DMN).
    admissibilidade: Admissibilidade
    roteamento_natureza: RoteamentoNatureza
    exige_notificacao_previa: bool
    exige_substituto_equivalente: bool
    prazo_notificacao: str
    fonte_regulatoria: str
    sla_analise: str
    sla_alerta: str
    dmn_refs: dict[str, str]
    dmn_error: str

    # Preenchido por auto_route/human_review.
    dossier: dict[str, Any]

    # Roteamento (NUNCA uma negativa/descredenciamento — so neutro ou humano).
    route: Route
    motivo_humano: MotivoHumano
    grupo_humano: str

    # Inicio do processo (SP-OP-CRED-001).
    process_started: bool
    #: CC-01: o start foi TENTADO e FALHOU tecnicamente (`except CibSevenError` de
    #: `start_process`). NAO e a mesma coisa que `process_started is False`, que tambem cobre
    #: no-ops legitimos; e este marcador — e so ele — que a aresta condicional le.
    start_failed: bool
    business_key: str
    process_ref: dict[str, Any]

    # Saida / desfecho (roteamento, NUNCA um efeito adverso consumado).
    desfecho: str
    error: str


# --- Helpers -----------------------------------------------------------------------------------


def _direcao(state: CarolinaState) -> Direcao:
    return state.get("direcao", "credenciamento")


def _business_key(state: CarolinaState) -> str:
    """Business key idempotente do contrato (§business key SP-OP-CRED-001).

    `CRED-{tenant}-{prestador}` (uma instancia ativa por prestador) ou, quando ha protocolo de
    ciclo, `CRED-{tenant}-{prestador}-{protocolo}` (variante por pedido).
    """
    tenant = state.get("tenant_id", "")
    prestador = state.get("prestador_id", "")
    protocolo = state.get("protocolo_cred", "")
    if protocolo:
        return f"CRED-{tenant}-{prestador}-{protocolo}"
    return f"CRED-{tenant}-{prestador}"


# --- Saneamento de estado (R1 cycle-1 fix — fecha a classe "caller-planted output fields") -----

#: The ONLY fields a caller may legitimately seed on the initial state (runtime identifiers +
#: SP-OP-CRED-001 contract inputs + worker-pre-resolved facts + the gather reference). Everything
#: else in `CarolinaState` is OUTPUT-ONLY: produced exclusively by this graph's own nodes.
#: Single-sourced against `CarolinaState` by the partition-completeness regression test
#: (`test_output_field_partition_is_complete`) — a new state field MUST be classified into
#: exactly one of the two sets or that test fails, so the sanitization below can never silently
#: drift out of date.
_CALLER_INPUT_FIELDS: frozenset[str] = frozenset(
    {
        "tenant_id",
        "canal",
        "prestador_id",
        "protocolo_cred",
        "direcao",
        "tipo_prestador",
        "origem_solicitacao",
        "motivo_informado",
        "data_solicitacao_iso",
        "documentos_refs",
        "regiao_saude",
        "especialidade",
        "licenca_valida",
        "documentacao_completa",
        "dentro_criterios_rede",
        "notificacao_previa_feita",
        "substituto_equivalente_identificado",
        "tem_beneficiarios_vinculados",
        "indicio_irregularidade_sinalizado",
        "patient_summary_ref",
    }
)


def _sanitized_output_fields() -> dict[str, Any]:
    """Fresh (never-shared) empty defaults for EVERY output-only `CarolinaState` field.

    R1 CYCLE-1 FIX (caller-planted assess-output fields — the verifier's DMN-gate-bypass /
    audit-forgery probes): `receive` overwrites all of these BEFORE gather/assess run, so a
    caller planting e.g. `error` + `route="auto_route"` (pre-fix: bailed gather/assess with ZERO
    DMN calls and shipped the forged route into engine variables) or a forged `dmn_refs`
    (pre-fix: reached the ADR-0007 `dmn_decision_refs` audit trail + dossier) is cleared at the
    graph's single entry point. Only node-produced values can reach `_cred_facts` /
    `_contract_variables` afterwards. Built fresh per call (function, not module constant) so
    the mutable `{}`/`[]` defaults are never shared across graph invocations.

    `route` deliberately sanitizes to `"human_review"` (the fail-safe `_route` default), never
    to a cleared/absent value — even a hypothetical path that skipped `assess` entirely could
    not auto-route off the sanitized state.
    """
    return {
        # gather outputs
        "gathered": False,
        "summary_facts": {},
        "gather_notes": [],
        # assess outputs (DMN results — only the DMNs, via assess, may fill these)
        "admissibilidade": None,
        "roteamento_natureza": None,
        "exige_notificacao_previa": False,
        "exige_substituto_equivalente": False,
        "prazo_notificacao": "",
        "fonte_regulatoria": "",
        "sla_analise": "",
        "sla_alerta": "",
        "dmn_refs": {},
        "dmn_error": "",
        # auto_route/human_review outputs
        "dossier": {},
        # routing outputs
        "route": "human_review",
        "motivo_humano": None,
        "grupo_humano": "",
        # start_process outputs
        "process_started": False,
        "start_failed": False,
        "business_key": "",
        "process_ref": {},
        # terminal outputs
        "desfecho": "",
        "error": "",
    }


#: Fatos BOOLEANOS deste fluxo, com o nome que o humano de destino reconhece (CC-11).
#:
#: Incidente de 24/08/2026 (contado por inteiro em `agents/rafael/graph.py::_FATOS_BOOLEANOS`):
#: fatos passados ao modelo como repr de dicionario deixam `False` e `None` com a mesma cara de
#: "vazio", e um fato APURADO-e-desfavoravel vira "nao ha registro". O conserto ficou num agente
#: so' ate' a auditoria da frota; este mapa e' a adocao aqui. Chave -> rotulo; a ORDEM e' a ordem
#: das linhas no prompt. So' entram fatos declarados `bool` no state — nada que seja enum/str.
_FATOS_BOOLEANOS: Final[dict[str, str]] = {
    "licenca_valida": "licenca do prestador valida",
    "documentacao_completa": "documentacao completa",
    "dentro_criterios_rede": "dentro dos criterios de rede",
    "notificacao_previa_feita": "notificacao previa ao prestador feita",
    "substituto_equivalente_identificado": "prestador substituto equivalente identificado",
    "tem_beneficiarios_vinculados": "ha beneficiarios vinculados ao prestador",
    "indicio_irregularidade_sinalizado": "indicio de irregularidade sinalizado",
    # Saidas de DMN (`cred_prior_notice`), e nao fatos apurados por worker — entram aqui porque
    # sofrem o mesmo colapso de repr: "nao exige notificacao previa" e "nao se avaliou se exige"
    # levam o juridico de rede a acoes opostas.
    "exige_notificacao_previa": "exige notificacao previa (saida de DMN)",
    "exige_substituto_equivalente": "exige substituto equivalente (saida de DMN)",
}


class CarolinaGraph:
    """Wires Carolina's injected dependencies into a compilable `StateGraph[CarolinaState]`."""

    def __init__(
        self,
        *,
        inference: InferenceProvider,
        dmn: DmnTransport,
        cibseven: CibSevenTransport,
        audit_sink: AuditStartSink,
        fhir: SummaryReader | None = None,
        agent_version: str = "carolina@v0",
    ) -> None:
        self._llm = inference
        self._dmn = dmn
        self._cibseven = cibseven
        # T-C2 fence: required durable ADR-0007 sink for the SP-OP-CRED-001 start.
        self._audit_sink = audit_sink
        self._model_id = getattr(inference, "model_id", None)
        self._fhir = fhir
        self._agent_version = agent_version

    # -- Nodes ----------------------------------------------------------------------------

    async def receive(self, state: CarolinaState) -> dict[str, Any]:
        """Sanitize output-only fields, then assign the idempotent business key (SP-OP-CRED-001).

        R1 CYCLE-1 FIX (caller-planted output fields): EVERY output-only state field is
        overwritten with its empty default (`_sanitized_output_fields`) on BOTH paths of this
        node, BEFORE gather/assess run — closing the DMN-gate-bypass / audit-forgery class at
        the graph's entry (see `_sanitized_output_fields`'s docstring for the verifier's exact
        reproductions). This covers the downstream early-bail guards
        (`if state.get("error"): return {}` in gather/assess) because `receive` is the graph's
        SINGLE entry node — `compile_graph` wires exactly one edge out of START, into `receive`
        (structurally regression-tested by `test_receive_is_the_single_graph_entry_node`), so
        the only `error` that can exist when those guards run is the one this node itself set.

        Fail-safe: never processes without the minimum contract identifiers (tenant + prestador).
        Without them there is no idempotent business key; routes to human by safety (never an
        adverse effect).
        """
        sanitized = _sanitized_output_fields()
        if not state.get("tenant_id") or not state.get("prestador_id"):
            return {
                **sanitized,
                "route": "human_review",
                "motivo_humano": "outro",
                "grupo_humano": self._default_human_group(_direcao(state)),
                "desfecho": "analise_humana",
                "error": "contexto de runtime ausente (tenant_id/prestador_id)",
            }
        return {**sanitized, "business_key": _business_key(state)}

    async def gather(self, state: CarolinaState) -> dict[str, Any]:
        """Best-effort summary enrichment — NEVER blocks routing (module docstring)."""
        if state.get("error"):
            return {}  # already routed by a prior guard failure

        notes: list[str] = []
        summary_facts: dict[str, Any] = {}

        if self._fhir is None:
            notes.append(
                "leitor de resumo nao configurado para este build (labeled boundary — ver "
                "docstring do modulo graph.py); dossie prossegue so com os fatos pre-resolvidos "
                "por worker."
            )
            return {"gathered": True, "summary_facts": summary_facts, "gather_notes": notes}

        summary_ref = state.get("patient_summary_ref", "")
        if summary_ref:
            try:
                summary_facts = await self._fhir.read_patient(summary_ref)
            except Exception as exc:  # noqa: BLE001 — best-effort enrichment, never fatal.
                notes.append(f"resumo indisponivel: {exc}")

        return {"gathered": True, "summary_facts": summary_facts, "gather_notes": notes}

    async def assess(self, state: CarolinaState) -> dict[str, Any]:
        """Evaluate `cred_admissibility` -> (se SEGUE_ANALISE/ANALISE_HUMANA) `cred_route` ->
        (uma vez alcancada) `cred_sla` -> (so ANALISE_DESCREDENCIAMENTO/catch-all) `cred_prior_
        notice`. Sequencia espelha exatamente o BPMN (module docstring's divergences #1-#3).

        FAIL-SAFE: DMN indisponivel NUNCA vira desfecho adverso — sempre human_review. NENHUM
        caminho aqui produz uma negativa de credenciamento nem um descredenciamento.
        """
        if state.get("error"):
            return {}

        direcao = _direcao(state)
        dmn_refs: dict[str, str] = {}

        admis_result = await self._evaluate_dmn(
            DMN_CRED_ADMISSIBILITY,
            {
                "direcao": direcao,
                "tipo_prestador": str(state.get("tipo_prestador", "")),
                "documentacao_completa": bool(state.get("documentacao_completa", False)),
                "licenca_valida": bool(state.get("licenca_valida", False)),
                "dentro_criterios_rede": bool(state.get("dentro_criterios_rede", False)),
                "indicio_irregularidade_sinalizado": bool(
                    state.get("indicio_irregularidade_sinalizado", False)
                ),
            },
        )
        if admis_result.get("error"):
            return {
                **self._route_human("dmn_indisponivel", dmn_refs, self._default_human_group(direcao)),
                "dmn_error": admis_result["error"],
                "desfecho": "analise_humana",
            }
        admissibilidade = cast(Admissibilidade, str(admis_result["row"].get("roteamento", "ANALISE_HUMANA")))
        dmn_refs[DMN_CRED_ADMISSIBILITY] = admis_result["ref"]
        base: dict[str, Any] = {"admissibilidade": admissibilidade}

        # PENDENTE_DOCUMENTACAO -> humano (NUNCA negativa automatica por documentacao incompleta).
        # Terminates BEFORE cred_route/cred_sla (BPMN: Flow_GW_PendenteDocumentacao bypasses
        # BRT_Route entirely).
        if admissibilidade == "PENDENTE_DOCUMENTACAO":
            return {
                **base,
                **self._route_human("documentacao_pendente", dmn_refs, self._default_human_group(direcao)),
                "desfecho": "documentacao_pendente",
            }

        # CLERICAL_CREDENCIAR -> roteamento NEUTRO favoravel. A UNICA direcao automatica; NUNCA um
        # efeito adverso. So alcancavel em direcao=credenciamento (defense-in-depth: a propria DMN
        # ja nunca emite CLERICAL_CREDENCIAR para descredenciamento — regra r_descred_segue
        # precede r_cred_clerical, hitPolicy FIRST — mas o codigo confere de novo). Terminates
        # BEFORE cred_route/cred_sla (BPMN: Flow_GW_ClericalCredenciar bypasses BRT_Route).
        if admissibilidade == "CLERICAL_CREDENCIAR" and direcao == "credenciamento":
            return {
                **base,
                "route": "auto_route",
                "dmn_refs": dmn_refs,
                "desfecho": "credenciamento_clerical",
            }

        # FAIL-SAFE (allowlist FECHADA): qualquer outro valor (SEGUE_ANALISE, ANALISE_HUMANA, ou
        # um CLERICAL_CREDENCIAR incoerente em direcao=descredenciamento) segue para cred_route —
        # o coracao adverso-like que roteia a natureza humana.
        rota_result = await self._evaluate_dmn(
            DMN_CRED_ROUTE,
            {
                "direcao": direcao,
                "tipo_prestador": str(state.get("tipo_prestador", "")),
                "origem_solicitacao": str(state.get("origem_solicitacao", "")),
                "indicio_irregularidade_sinalizado": bool(
                    state.get("indicio_irregularidade_sinalizado", False)
                ),
            },
        )
        if rota_result.get("error"):
            return {
                **base,
                **self._route_human("dmn_indisponivel", dmn_refs, self._default_human_group(direcao)),
                "dmn_error": rota_result["error"],
                "desfecho": "analise_humana",
            }
        roteamento = cast(RoteamentoNatureza, str(rota_result["row"].get("roteamento", "ANALISE_HUMANA")))
        dmn_refs[DMN_CRED_ROUTE] = rota_result["ref"]
        base["roteamento_natureza"] = roteamento

        # cred_sla: unconditional once cred_route is reached (BPMN BRT_CredSla, before
        # GW_Natureza) — best-effort/informative only, NEVER blocks routing (mirrors auth_sla).
        sla_result = await self._evaluate_dmn(
            DMN_CRED_SLA, {"direcao": direcao, "tipo_prestador": str(state.get("tipo_prestador", ""))}
        )
        if not sla_result.get("error"):
            sla_row = sla_result["row"]
            base["sla_analise"] = str(sla_row.get("sla_analise", ""))
            base["sla_alerta"] = str(sla_row.get("sla_alerta", ""))
            if sla_row.get("fonte_regulatoria"):
                base["fonte_regulatoria"] = str(sla_row["fonte_regulatoria"])
            dmn_refs[DMN_CRED_SLA] = sla_result["ref"]

        # cred_prior_notice (RN 567): so no ramo de descredenciamento/co-review (BPMN
        # Flow_GWNat_Descredenciamento — ANALISE_DESCREDENCIAMENTO OU o catch-all ANALISE_HUMANA,
        # GAP-CRED-4/6, module docstring divergence #2). Best-effort/informative only.
        if roteamento in ("ANALISE_DESCREDENCIAMENTO", "ANALISE_HUMANA"):
            notice_result = await self._evaluate_dmn(
                DMN_CRED_PRIOR_NOTICE,
                {
                    "tipo_prestador": str(state.get("tipo_prestador", "")),
                    "tem_beneficiarios_vinculados": bool(state.get("tem_beneficiarios_vinculados", False)),
                },
            )
            if not notice_result.get("error"):
                notice_row = notice_result["row"]
                base["exige_notificacao_previa"] = bool(notice_row.get("exige_notificacao_previa", True))
                base["exige_substituto_equivalente"] = bool(
                    notice_row.get("exige_substituto_equivalente", False)
                )
                base["prazo_notificacao"] = str(notice_row.get("prazo_notificacao", ""))
                if notice_row.get("fonte_regulatoria"):
                    base["fonte_regulatoria"] = str(notice_row["fonte_regulatoria"])
                dmn_refs[DMN_CRED_PRIOR_NOTICE] = notice_result["ref"]

        base["dmn_refs"] = dmn_refs

        # FAIL-SAFE (allowlist FECHADA): TODAS as saidas de cred_route vao ao humano. NENHUMA e
        # auto_route — a negativa de credenciamento e o descredenciamento SO nascem na User Task
        # humana (invariante L1).
        if roteamento == "ANALISE_CREDENCIAMENTO":
            return {**base, **self._route_human("analise_credenciamento", dmn_refs, "gestao-rede")}
        # ANALISE_DESCREDENCIAMENTO and the ANALISE_HUMANA catch-all both follow the co-review
        # branch (divergence #2) — juridico-rede is the primary group; the dossier + contract
        # variables still carry `roteamento_natureza`/`indicio_irregularidade_sinalizado` so the
        # human sees the full signal even for the catch-all.
        return {**base, **self._route_human("analise_descredenciamento", dmn_refs, "juridico-rede")}

    async def auto_route(self, state: CarolinaState) -> dict[str, Any]:
        """Roteamento NEUTRO (credenciamento clerical favoravel). NUNCA produz uma negativa de
        credenciamento nem um descredenciamento — so monta o dossie; `start_process` abre a
        instancia, que efetiva o credenciamento neutro (worker `register_credenciamento`,
        engine-side)."""
        return {"dossier": await self._build_dossier(state, route="auto_route")}

    async def human_review(self, state: CarolinaState) -> dict[str, Any]:
        """Prepara o dossie da gestao/juridico de rede humano. Esta e a rota de QUALQUER caso de
        descredenciamento, negativa-de-credenciamento candidata, pendencia, indicio-de-
        irregularidade, ambiguidade ou DMN-indisponivel. Carolina instrui; o humano decide."""
        dossier = await self._build_dossier(state, route="human_review")
        desfecho = state.get("desfecho") or self._human_desfecho(state)
        return {"dossier": dossier, "desfecho": desfecho}

    async def start_process(self, state: CarolinaState) -> dict[str, Any]:
        """Start SP-OP-CRED-001 idempotently (business key `CRED-{tenant}-{prestador}[-{protocolo}]`)."""
        business_key = state.get("business_key") or _business_key(state)
        variables = self._contract_variables(state)
        provenance = AgentDecisionProvenance(
            agent_id="carolina",
            agent_version=self._agent_version,
            tenant_id=state.get("tenant_id", ""),
            model_id=self._model_id,
            prompt_version=SYSTEM_PROMPT_VERSION,
            decision_basis={
                "route": state.get("route", ""),
                "desfecho": state.get("desfecho", ""),
                "motivo_humano": state.get("motivo_humano") or "",
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
            return start_failed_state(business_key=business_key, error=f"start_process indisponivel: {exc}")
        return {
            "process_started": True,
            "business_key": business_key,
            "process_ref": {
                "instance_id": instance.instance_id,
                "state": instance.state,
                "already_existed": instance.already_existed,
            },
        }

    async def notify_start_failure(self, state: CarolinaState) -> dict[str, Any]:
        """CC-01: o start FALHOU — grava o desfecho de erro e ALERTA, em vez de seguir calado.

        Ate CC-01 a aresta que saia de `start_process` era INCONDICIONAL: o turno chegava ao
        terminal com o `desfecho` de SUCESSO que um no a montante ja havia gravado, afirmando um
        fato que nao aconteceu, e sem prazo nenhum — o timer de SLA vive na instancia BPMN que
        nunca nasceu. O corpo deste no e o helper compartilhado
        (`maezo.runtime.start_outcome.notify_start_failure`): uma definicao para os 9 agentes,
        nunca 9 copias.
        """
        return emit_start_failure_notice(dict(state), agent_id="carolina", process_key=PROCESS_KEY)

    async def finalize(self, state: CarolinaState) -> dict[str, Any]:
        """Terminal node — no further computation; `desfecho` was already set by `assess`/
        `human_review`. No episodic memory write (module docstring's divergence #5)."""
        return {}

    # -- Conditional routing ------------------------------------------------------------------

    @staticmethod
    def _route(state: CarolinaState) -> str:
        # FAIL-SAFE: on absence/doubt, ALWAYS human (never auto_route by omission).
        return "auto_route" if state.get("route") == "auto_route" else "human_review"

    @staticmethod
    def _default_human_group(direcao: Direcao) -> str:
        return "juridico-rede" if direcao == "descredenciamento" else "gestao-rede"

    @staticmethod
    def _human_desfecho(state: CarolinaState) -> str:
        return (
            "analise_descredenciamento"
            if _direcao(state) == "descredenciamento"
            else "analise_credenciamento"
        )

    @staticmethod
    def _route_human(motivo: MotivoHumano, dmn_refs: dict[str, str], grupo_humano: str) -> dict[str, Any]:
        return {
            "route": "human_review",
            "motivo_humano": motivo,
            "grupo_humano": grupo_humano,
            "dmn_refs": dmn_refs,
        }

    # -- DMN (cred_admissibility / cred_route / cred_prior_notice / cred_sla; none has an
    # adverse output) --------------------------------------------------------------------------

    async def _evaluate_dmn(self, table: str, dmn_input: dict[str, Any]) -> dict[str, Any]:
        try:
            rows, version = await self._dmn.evaluate(table, dmn_input)
            row = first_row(rows, table, dmn_input)
        except (DmnEvaluationError, DmnNoResultError) as exc:
            return {"error": f"DMN `{table}` indisponivel: {exc}"}
        return {"row": row, "ref": f"{table}#{version.id}"}

    # -- Dossier assembly (ADR-0007 audit provenance; L1-hard structural guardrail) ------------

    async def _build_dossier(self, state: CarolinaState, *, route: Route) -> dict[str, Any]:
        direcao = _direcao(state)
        motivo_humano = state.get("motivo_humano") if route == "human_review" else None
        grupo_humano = state.get("grupo_humano") if route == "human_review" else None
        facts = self._cred_facts(state)
        prompt = (
            f"{dossier_prompt()}\n\ndirecao={direcao} route={route} motivo_humano={motivo_humano}\n"
            f"{render_fatos_para_prompt(facts, booleanos=_FATOS_BOOLEANOS)}"
        )
        try:
            narrativa = await self._llm.generate(
                prompt, phi=True, agent_id="carolina", tenant_id=state.get("tenant_id", "")
            )
        except Exception:  # noqa: BLE001 — dossie deterministico minimo se LLM falhar.
            narrativa = ""
        return {
            "prompt_version": DOSSIER_PROMPT_VERSION,
            "direcao": direcao,
            "route": route,
            "motivo_humano": motivo_humano,
            "grupo_humano": grupo_humano,
            "fatos": facts,
            "dmn_decision_refs": state.get("dmn_refs", {}),
            "narrativa": narrativa,
            "documentos_refs": state.get("documentos_refs") or [],
            # STRUCTURAL GUARDRAIL (L1 hard): the dossier NEVER carries an adverse decision.
            "decisao_credenciamento": None,
            "decisao_descredenciamento": None,
        }

    @staticmethod
    def _cred_facts(state: CarolinaState) -> dict[str, Any]:
        return {
            "prestador_id": state.get("prestador_id"),
            "protocolo_cred": state.get("protocolo_cred"),
            "direcao": state.get("direcao"),
            "tipo_prestador": state.get("tipo_prestador"),
            "origem_solicitacao": state.get("origem_solicitacao"),
            "motivo_informado": state.get("motivo_informado"),
            "data_solicitacao_iso": state.get("data_solicitacao_iso"),
            "licenca_valida": state.get("licenca_valida"),
            "documentacao_completa": state.get("documentacao_completa"),
            "dentro_criterios_rede": state.get("dentro_criterios_rede"),
            "notificacao_previa_feita": state.get("notificacao_previa_feita"),
            "substituto_equivalente_identificado": state.get("substituto_equivalente_identificado"),
            "tem_beneficiarios_vinculados": state.get("tem_beneficiarios_vinculados"),
            "indicio_irregularidade_sinalizado": state.get("indicio_irregularidade_sinalizado"),
            "admissibilidade": state.get("admissibilidade"),
            "roteamento_natureza": state.get("roteamento_natureza"),
            "exige_notificacao_previa": state.get("exige_notificacao_previa"),
            "exige_substituto_equivalente": state.get("exige_substituto_equivalente"),
            "prazo_notificacao": state.get("prazo_notificacao"),
            "fonte_regulatoria": state.get("fonte_regulatoria"),
            "dmn_refs": state.get("dmn_refs", {}),
            "sla_analise": state.get("sla_analise"),
            "lacunas_enriquecimento": state.get("gather_notes", []),
        }

    def _contract_variables(self, state: CarolinaState) -> dict[str, Any]:
        """Monta as variaveis de entrada do SP-OP-CRED-001 (contrato `docs/processes/contracts/
        SP-OP-CRED-001.md`). Inclui o dossie da Carolina como `dossie_carolina` (instrucao) e o
        `motivo_encaminhamento`/`grupo_destino` quando rota humana — NUNCA uma negativa de
        credenciamento nem um descredenciamento."""
        direcao = _direcao(state)
        variables: dict[str, Any] = {
            "tenant_id": state.get("tenant_id", ""),
            "prestador_id": state.get("prestador_id", ""),
            "direcao": direcao,
            "tipo_prestador": str(state.get("tipo_prestador", "")),
            "origem_solicitacao": str(state.get("origem_solicitacao", "")),
            "data_solicitacao_iso": state.get("data_solicitacao_iso", ""),
            "documentos_refs": state.get("documentos_refs") or [],
            "licenca_valida": bool(state.get("licenca_valida", False)),
            "documentacao_completa": bool(state.get("documentacao_completa", False)),
            "dentro_criterios_rede": bool(state.get("dentro_criterios_rede", False)),
            "indicio_irregularidade_sinalizado": bool(state.get("indicio_irregularidade_sinalizado", False)),
            "source_agent_id": "carolina",
            "source_agent_version": self._agent_version,
            "dossie_carolina": state.get("dossier") or {},
            "carolina_route": state.get("route", "human_review"),
        }
        if state.get("protocolo_cred"):
            variables["protocolo_cred"] = state["protocolo_cred"]
        if state.get("motivo_informado"):
            variables["motivo_informado"] = state["motivo_informado"]
        if state.get("regiao_saude"):
            variables["regiao_saude"] = state["regiao_saude"]
        if state.get("especialidade"):
            variables["especialidade"] = state["especialidade"]

        if direcao == "descredenciamento":
            variables["notificacao_previa_feita"] = bool(state.get("notificacao_previa_feita", False))
            variables["tem_beneficiarios_vinculados"] = bool(state.get("tem_beneficiarios_vinculados", False))
            if state.get("substituto_equivalente_identificado") is not None:
                variables["substituto_equivalente_identificado"] = bool(
                    state.get("substituto_equivalente_identificado", False)
                )

        if state.get("route") == "human_review":
            variables["motivo_encaminhamento"] = state.get("motivo_humano") or "outro"
            variables["grupo_destino"] = state.get("grupo_humano") or self._default_human_group(direcao)

        dmn_refs = state.get("dmn_refs")
        if dmn_refs:
            variables["dmn_decision_refs"] = dmn_refs
        return variables

    # -- Graph assembly -----------------------------------------------------------------------

    def compile_graph(self) -> StateGraph[CarolinaState]:
        g: StateGraph[CarolinaState] = StateGraph(CarolinaState)
        g.add_node("receive", self.receive)
        g.add_node("gather", self.gather)
        g.add_node("assess", self.assess)
        g.add_node("auto_route", self.auto_route)
        g.add_node("human_review", self.human_review)
        g.add_node("start_process", self.start_process)
        g.add_node("notify_start_failure", self.notify_start_failure)
        g.add_node("finalize", self.finalize)

        g.add_edge(START, "receive")
        g.add_edge("receive", "gather")
        g.add_edge("gather", "assess")
        g.add_conditional_edges(
            "assess", self._route, {"auto_route": "auto_route", "human_review": "human_review"}
        )
        g.add_edge("auto_route", "start_process")
        g.add_edge("human_review", "start_process")
        # CC-01: a aresta que sai de `start_process` e CONDICIONAL. Uma falha tecnica de
        # start desvia para `notify_start_failure` (desfecho de erro + alerta); qualquer
        # outro caminho — incluindo os no-ops legitimos com `process_started=False` —
        # segue para o terminal de sempre. O predicado e compartilhado (uma definicao).
        g.add_conditional_edges(
            "start_process",
            route_after_start,
            {"notify_start_failure": "notify_start_failure", "continue": "finalize"},
        )
        g.add_edge("notify_start_failure", END)
        g.add_edge("finalize", END)
        return g


def build(config: dict[str, Any] | None = None) -> StateGraph[CarolinaState]:
    """Contract `_template/graph.py:build`, resolved by `AgentLoader`/`runtime.harness.Harness`.

    `config` MUST contain `inference` (ADR-0009), `dmn` (ADR-0028/T1.5), `cibseven`
    (ADR-0001/T1.11). `fhir` is OPTIONAL (see module docstring's divergence #6) — its absence
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
            f"Carolina build(config) is missing required dependencies: {missing} "
            "(ADR-0001/0007/0009/0028 — inference/dmn/cibseven/audit_sink must all be injected; "
            "audit_sink is the T-C2 fence — no process start without a durable sink)"
        )
    agent_version = str(cfg.get("agent_version", "carolina@v0"))
    return CarolinaGraph(
        inference=cast(InferenceProvider, inference),
        dmn=cast(DmnTransport, dmn),
        cibseven=cast(CibSevenTransport, cibseven),
        audit_sink=cast(AuditStartSink, audit_sink),
        fhir=cast("SummaryReader | None", cfg.get("fhir")),
        agent_version=agent_version,
    ).compile_graph()


# Prompt versions exposed for audit/eval gating (ADR-0007/0009).
PROMPT_VERSIONS: dict[str, str] = {
    "system": SYSTEM_PROMPT_VERSION,
    "dossier": DOSSIER_PROMPT_VERSION,
}
