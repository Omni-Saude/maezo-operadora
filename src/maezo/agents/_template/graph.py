"""Agent template graph — o CONTRATO CANONICO que todo agente Maezo copia (HEL-13/HEL-15).

O QUE ESTE ARQUIVO E, HONESTAMENTE
----------------------------------
Um ESQUELETO EXECUTAVEL, nao um agente. Ele nao classifica nada, nao chama LLM e nao decide
regra de negocio (isso e DMN/BPMN/policy, nunca Python — AGENTS.md). O que ele CARREGA sao os
cinco padroes fail-closed que helena/rafael/beatriz provaram e que, ate HEL-13, cada agente novo
tinha de reinventar (ou omitia):

1. `build(config)` COM LISTA `missing -> ValueError` (padrao `helena/graph.py::build`). Um agente
   derivado nunca constroi um grafo que so quebraria no primeiro tool call. `audit_sink` esta
   entre as deps OBRIGATORIAS: e a fence T-C2 — nenhum start de processo sem sink duravel.
2. PARTICAO INPUT-ONLY x OUTPUT-ONLY do estado (`INPUT_FIELDS` / `NEUTRAL_OUTPUTS`) com GUARDA DE
   COMPLETUDE EM IMPORT-TIME (padrao T1.11 anti-plantio de `rafael/graph.py::RAFAEL_INPUT_FIELDS`
   e `beatriz/graph.py::_CALLER_INPUT_FIELDS`): todo campo do `TemplateState` tem de estar
   classificado em EXATAMENTE um dos dois conjuntos, senao o import falha. "Qualquer chave
   esquecida e um buraco."
3. `receive` RESETA todo campo output-only (`_output_field_resets`) antes de qualquer no a
   jusante rodar — um valor plantado pelo chamador morre na entrada.
4. START de processo SO pelo chokepoint sancionado `start_process_idempotent`
   (`tools/mcp_cibseven/transport.py`), com `audit_sink` + `AgentDecisionProvenance`
   obrigatorios. O gate `make check-start-process-fence` proibe a chamada direta ao transport.
5. FAIL-NOTIFY DE START (CC-01 da auditoria de frota, 2026-09-04): quando o start falha
   (`process_started is False`), o grafo NAO segue calado para `complete` — uma aresta
   condicional leva a `notify_start_failure`, que escreve `desfecho="erro_inicio_processo"` e
   loga um evento observavel com a business key. Sem isso o caso some sem SLA, sem alerta e sem
   retry, porque o timer de SLA vive na instancia BPMN que nunca nasceu.
   ESCOPO (atualizado 2026-09-04, CC-01/RAF-02/LUC-05): o padrao deixou de morar AQUI. A unica
   definicao do desfecho, do predicado de roteamento e do no de alerta vive em
   `maezo.runtime.start_outcome` — o ponto mais baixo —, e este template a IMPORTA como qualquer
   outro agente. O backport foi feito: os 9 agentes que iniciam processo (rafael, marina, lucas,
   carolina, andre, gustavo, valentina, fernando + helena, que inicia dentro de `escalate`)
   adotam o mesmo helper. O predicado NAO le `process_started` — le o marcador `start_failed`,
   porque `process_started is False` tambem significa no-op legitimo em varios agentes (ver o
   docstring de `runtime/start_outcome.py`).

O QUE ESTE ARQUIVO DELIBERADAMENTE NAO TEM
------------------------------------------
* `prompts.py` / `PROMPT_VERSIONS` / exemplo de `generate(phi=True, agent_id=...)` — HEL-14 (P2),
  fora do escopo deste commit. O `spec/agents/_template/agent.yaml` ainda declara
  `prompt_versions: {classify: classify-v1}` sem modulo correspondente; isso e o proprio HEL-14.
* `delegation.py` / `adapters.py` — seams A2A e de worker, por agente.
* `memory` episodica/semantica ligada — CC-07, outro pacote de trabalho.
* Qualquer regra de negocio. `contract_variables` e o UNICO seam abstrato: ele levanta
  `NotImplementedError` NOMEANDO-SE, nunca devolve um dict vazio silencioso.

COMO DERIVAR UM AGENTE DESTE TEMPLATE
-------------------------------------
1. Substitua `TemplateGraph.PROCESS_KEY` e `TemplateGraph.BUSINESS_KEY_PREFIX`. A chave TEM de
   existir em `docs/processes/contracts/<chave>.md` E em
   `src/maezo/tools/process_allowlist.py::KNOWN_PROCESS_KEYS`, senao `make validate-artifacts`
   falha (cerca HEL-12 em `platform/validation/agent_def.py`) e o `EffectPolicy` do gateway nega
   o start em runtime.
2. Implemente `contract_variables` com EXATAMENTE as variaveis de entrada do contrato.
3. Renomeie `TemplateState` -> `<Agente>State` e CLASSIFIQUE cada campo novo em `INPUT_FIELDS` ou
   `NEUTRAL_OUTPUTS`. A guarda de import-time recusa qualquer campo nao classificado.
4. Acrescente os nos do agente entre `receive` e `start_process`. Todo no que possa terminar a
   conversa cedo tem de mergear `_output_field_resets()` no seu retorno (padrao
   `gustavo/graph.py`).
"""

from __future__ import annotations

from typing import Any, TypedDict, cast

import structlog
from langgraph.graph import END, START, StateGraph

from maezo.runtime.start_outcome import (
    DESFECHO_ERRO_INICIO_PROCESSO,
    route_after_start,
    start_failed_state,
)
from maezo.runtime.start_outcome import (
    notify_start_failure as emit_start_failure_notice,
)
from maezo.tools.mcp_cibseven.transport import (
    AgentDecisionProvenance,
    AuditStartSink,
    CibSevenError,
    CibSevenTransport,
    start_process_idempotent,
)
from maezo.tools.workers.dmn_transport import DmnTransport

logger = structlog.get_logger(__name__)

#: Desfecho canonico de falha de start (CC-01). REEXPORTADO, nao redefinido: a UNICA definicao
#: vive em `maezo.runtime.start_outcome` — o ponto mais baixo, de onde os 9 agentes e este
#: template a importam. Duas definicoes do mesmo literal e como o valor diverge.
__all__ = ["DESFECHO_ERRO_INICIO_PROCESSO"]

#: Desfecho neutro de sucesso do esqueleto. Um agente real substitui pelo desfecho do SEU
#: contrato (`encaminhado_auditor`, `aprovacao_automatica_solicitada`, ...).
DESFECHO_PROCESSO_INICIADO: str = "processo_iniciado"


class TemplateState(TypedDict, total=False):
    """Estado do turno. TODO campo tem de estar classificado abaixo (guarda de import-time)."""

    # --- INPUT-ONLY: as UNICAS chaves que um chamador/upstream pode setar. ---
    tenant_id: str
    correlation_id: str
    request: dict[str, Any]

    # --- OUTPUT-ONLY: chaves de propriedade dos nos deste grafo. Um chamador nunca as seta. ---
    business_key: str
    process_started: bool
    #: CC-01: o start foi TENTADO e FALHOU tecnicamente. Escrito por EXATAMENTE um caminho de
    #: codigo (o `except CibSevenError` de `start_process`, via `start_failed_state`) e lido pela
    #: aresta condicional. Distinto de `process_started is False`, que num agente real tambem
    #: significa no-op legitimo (fluxo sem processo, `ALREADY_COMPLETED`, rota informativa).
    start_failed: bool
    process_ref: dict[str, Any] | None
    desfecho: str
    error: str


#: As UNICAS chaves que um chamador/upstream (envelope A2A, worker de origem, webhook) pode setar.
INPUT_FIELDS: frozenset[str] = frozenset({"tenant_id", "correlation_id", "request"})

#: Neutro de CADA campo output-only. `receive` escreve uma copia disto sobre o estado de entrada,
#: entao um valor plantado e SUBSTITUIDO pelo neutro correto, nao apenas descartado.
NEUTRAL_OUTPUTS: dict[str, Any] = {
    "business_key": "",
    "process_started": False,
    "start_failed": False,
    "process_ref": None,
    "desfecho": "",
    "error": "",
}


def _assert_state_partition_is_complete(
    *,
    declared: frozenset[str],
    input_fields: frozenset[str],
    neutral_outputs: dict[str, Any],
) -> None:
    """Guarda de COMPLETUDE (T1.11). Falha em IMPORT-TIME se algum campo nao foi classificado.

    Extraida como funcao (em vez de um `if` solto no modulo, como em `rafael/graph.py`) por dois
    motivos: um agente derivado a REUSA com os seus proprios conjuntos, e um teste consegue
    provar que ela de fato RECUSA um campo nao classificado — uma guarda que nunca foi vista
    falhar nao e uma guarda.
    """
    classified = input_fields | frozenset(neutral_outputs)
    overlap = input_fields & frozenset(neutral_outputs)
    if declared != classified or overlap:
        raise RuntimeError(
            "particao input/output do estado incompleta (gate T1.11 de fronteira de entrada): "
            f"nao classificados={sorted(declared - classified)} "
            f"entradas obsoletas={sorted(classified - declared)} "
            f"classificados nos DOIS conjuntos={sorted(overlap)} — todo campo do State tem de ser "
            "OU um INPUT_FIELD OU carregar um neutro em NEUTRAL_OUTPUTS."
        )


_assert_state_partition_is_complete(
    declared=frozenset(TemplateState.__annotations__),
    input_fields=INPUT_FIELDS,
    neutral_outputs=NEUTRAL_OUTPUTS,
)


def _output_field_resets() -> dict[str, Any]:
    """Copia fresca dos neutros. Todo no que possa retornar cedo mergeia isto no seu retorno."""
    return dict(NEUTRAL_OUTPUTS)


def gate_inbound_state(raw: dict[str, Any]) -> TemplateState:
    """Allowlist fail-closed de entrada: so `INPUT_FIELDS` sobrevive; o resto e DERRUBADO e logado.

    Use na costura de delegacao/ingestao do agente derivado. A variante ESTRITA (levantar em vez
    de derrubar) esta em `rafael/graph.py::new_rafael_state` — escolha uma por costura e
    documente por que.
    """
    dropped = sorted(k for k in raw if k not in INPUT_FIELDS)
    if dropped:
        logger.warning("template_inbound_output_fields_dropped", dropped=dropped)
    return cast(TemplateState, {k: raw[k] for k in INPUT_FIELDS if k in raw})


class TemplateGraph:
    """Esqueleto canonico: `receive -> start_process -> {notify_start_failure | complete}`."""

    #: SUBSTITUIR na definicao do agente. Tem de existir em `docs/processes/contracts/<chave>.md`
    #: E em `KNOWN_PROCESS_KEYS` (cerca HEL-12 no `validate-artifacts`).
    PROCESS_KEY: str = "SP-OP-<AGENT>-001"

    #: SUBSTITUIR: prefixo da business key idempotente (`ESC`, `AUTH`, `PAGTO`, ...).
    BUSINESS_KEY_PREFIX: str = "<AGENT>"

    def __init__(
        self,
        *,
        inference: Any,
        dmn: DmnTransport,
        cibseven: CibSevenTransport,
        audit_sink: AuditStartSink,
        agent_version: str,
    ) -> None:
        # Guardadas mesmo sem uso NESTE esqueleto: sao as deps que o contrato canonico declara
        # obrigatorias, e os nos que um agente derivado acrescenta as consomem imediatamente
        # (`inference` no no de classificacao, `dmn` na decisao — nunca o LLM decidindo).
        self._inference = inference
        self._dmn = dmn
        self._cibseven = cibseven
        self._audit_sink = audit_sink
        self._agent_version = agent_version

    # --- Business key ---------------------------------------------------------------------

    def business_key(self, state: TemplateState) -> str:
        """Chave idempotente do contrato. SUBSTITUIR pela forma exata que o contrato define."""
        return f"{self.BUSINESS_KEY_PREFIX}-{state.get('tenant_id', '')}-{state.get('correlation_id', '')}"

    # --- Seam abstrato --------------------------------------------------------------------

    def contract_variables(self, state: TemplateState) -> dict[str, Any]:
        """As variaveis de entrada do contrato BPMN. O UNICO seam abstrato do template.

        Levanta `NotImplementedError` DE PROPOSITO: um `return {}` silencioso iniciaria uma
        instancia sem as variaveis que o processo exige, e o defeito so apareceria no primeiro
        gateway do BPMN. Nunca troque isto por um no-op.

        Disciplina PHI (ADR-0006/0017): estas variaveis vao para o engine. Nada de texto clinico
        livre, CPF, telefone ou nome — so identificadores pseudonimizados e tokens de enum. O
        chokepoint ainda passa a provenance por `redact_phi_vars` como backstop, mas o backstop
        nao e a politica.
        """
        raise NotImplementedError(
            f"{type(self).__name__}.contract_variables nao foi implementado — todo agente derivado "
            f"do _template DEVE devolver as variaveis de entrada do contrato {self.PROCESS_KEY} "
            "(spec-first: o contrato em docs/processes/contracts/ vem ANTES do Python)."
        )

    # --- Nos ------------------------------------------------------------------------------

    async def receive(self, state: TemplateState) -> dict[str, Any]:
        """Inicio do turno: RESETA todo campo output-only e atribui a business key.

        Esta e a camada 1 da defesa anti-plantio (T1.11): nenhum valor de saida vindo do chamador
        sobrevive a entrada, entao nenhum no a jusante pode LER um campo forjado.
        """
        reset = _output_field_resets()
        reset["business_key"] = self.business_key(state)
        return reset

    async def start_process(self, state: TemplateState) -> dict[str, Any]:
        """Inicia o processo pelo CHOKEPOINT sancionado (`start_process_idempotent`).

        Nunca chame o transport direto: `make check-start-process-fence` faz um AST-scan de
        `src/maezo` e falha em qualquer start fora deste helper. O helper garante, nesta ordem,
        (1) registro de auditoria duravel ANTES do efeito e (2) idempotencia por business key.
        """
        key = state.get("business_key") or self.business_key(state)
        provenance = AgentDecisionProvenance(
            agent_id="_template",
            agent_version=self._agent_version,
            tenant_id=state.get("tenant_id", ""),
            model_id="",
            prompt_version="",
            # SO tokens de rota/enum limitados. NUNCA um passthrough de `state`/`variables`.
            decision_basis={"desfecho": state.get("desfecho", "")},
        )
        try:
            instance = await start_process_idempotent(
                self._cibseven,
                process_key=self.PROCESS_KEY,
                business_key=key,
                variables=self.contract_variables(state),
                audit_sink=self._audit_sink,
                provenance=provenance,
            )
        except CibSevenError as exc:
            # NAO engula: `start_failed_state` marca a falha (`start_failed=True`) e e' esse
            # marcador que `route_after_start` le para desviar a `notify_start_failure` (CC-01).
            return start_failed_state(
                business_key=key, error=f"start de {self.PROCESS_KEY} indisponivel: {exc}"
            )
        return {
            "process_started": True,
            "business_key": key,
            "process_ref": {
                "instance_id": instance.instance_id,
                "state": instance.state,
                "already_existed": instance.already_existed,
            },
        }

    async def notify_start_failure(self, state: TemplateState) -> dict[str, Any]:
        """CC-01: o start falhou — marque o desfecho e ALERTE, em vez de seguir calado.

        Enquanto a instancia nao nasce, o timer de SLA do BPMN nao existe: este evento e o
        substituto do prazo. Um agente derivado liga aqui o seu canal de alerta real (evento
        `<PROCESS_KEY>.start_failed` com a business key) e, se o caso admitir, UM retry com
        backoff — seguro porque `start_process_idempotent` e idempotente por business key.
        """
        return emit_start_failure_notice(dict(state), agent_id="_template", process_key=self.PROCESS_KEY)

    async def complete(self, state: TemplateState) -> dict[str, Any]:
        """Fim do turno feliz. Um agente derivado escreve aqui o desfecho do SEU contrato."""
        return {"desfecho": DESFECHO_PROCESSO_INICIADO}

    # --- Roteamento -----------------------------------------------------------------------

    def compile_graph(self) -> StateGraph[TemplateState]:
        """Monta (sem compilar) o StateGraph. `START`/`END` sao CONSTANTES, nunca strings (HEL-15)."""
        g: StateGraph[TemplateState] = StateGraph(TemplateState)
        g.add_node("receive", self.receive)
        g.add_node("start_process", self.start_process)
        g.add_node("notify_start_failure", self.notify_start_failure)
        g.add_node("complete", self.complete)

        g.add_edge(START, "receive")
        g.add_edge("receive", "start_process")
        g.add_conditional_edges(
            "start_process",
            route_after_start,
            {"notify_start_failure": "notify_start_failure", "continue": "complete"},
        )
        g.add_edge("notify_start_failure", END)
        g.add_edge("complete", END)
        return g


#: Deps que `build(config)` EXIGE. Um agente derivado edita esta lista — o que ele nao usa sai,
#: e o que ele usa entra — mas NUNCA a esvazia: uma dep opcional que o grafo consome e como o
#: `build()` mudo voltava a existir.
REQUIRED_DEPENDENCIES: tuple[str, ...] = ("inference", "dmn", "cibseven", "audit_sink")


def build(config: dict[str, Any] | None = None) -> StateGraph[TemplateState]:
    """Contrato `_template/graph.py:build`, resolvido por `AgentLoader`/`runtime.harness.Harness`.

    `config` TEM de conter:
      - `inference`: um `InferenceProvider` (ADR-0009).
      - `dmn`: um `DmnTransport` (ADR-0028/T1.5) — quem DECIDE e a DMN, nunca o LLM.
      - `cibseven`: um `CibSevenTransport` (ADR-0001/T1.11).
      - `audit_sink`: um `AuditStartSink` (fence T-C2) — sem sink duravel nao ha start.
    Opcional:
      - `agent_version`: string de provenance de auditoria (ADR-0007), default `"_template@v0"`.

    Fail-closed: uma dep obrigatoria ausente levanta `ValueError` NOMEANDO-A em tempo de build —
    o agente nunca constroi calado um grafo que quebraria no primeiro tool call (HEL-13, padrao
    `helena/graph.py::build`).
    """
    cfg = config or {}
    missing = [name for name in REQUIRED_DEPENDENCIES if cfg.get(name) is None]
    if missing:
        raise ValueError(
            f"_template build(config) is missing required dependencies: {missing} "
            "(ADR-0001/0007/0009/0028 — inference/dmn/cibseven/audit_sink; audit_sink is the T-C2 "
            "fence: no process start without a durable sink)"
        )
    return TemplateGraph(
        inference=cfg["inference"],
        dmn=cast(DmnTransport, cfg["dmn"]),
        cibseven=cast(CibSevenTransport, cfg["cibseven"]),
        audit_sink=cast(AuditStartSink, cfg["audit_sink"]),
        agent_version=str(cfg.get("agent_version", "_template@v0")),
    ).compile_graph()
