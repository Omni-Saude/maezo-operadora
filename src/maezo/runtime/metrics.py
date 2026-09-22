"""Prometheus metrics for agent runtime (ADR-0010, M11).

Exposes core metrics via prometheus_client:
- maezo_agent_latency_seconds (Histogram) — latency of agent interactions
- maezo_tool_calls_total (Counter) — total tool call invocations
- maezo_agent_errors_total (Counter) — total agent errors

M11 worker metrics:
- maezo_worker_execution_time_seconds (Histogram) — worker execution duration
- maezo_worker_error_count_total (Counter) — worker error count by type

T1.1 dispatch-outcome metrics (design docs/design/T1.1-runtime-spine.md §13, GAP-XOBS-4):
- maezo_worker_task_total (Counter) — external-task dispatch outcome by {tenant,topic,outcome}
- maezo_worker_task_duration_seconds (Histogram) — external-task handler wall-clock latency

T8 LLM token-metering (ADR-0009 single seam, maezo.runtime.inference):
- maezo_llm_tokens_total (Counter) — token COUNTS consumed by {provider,model,token_type};
  never a cost/dollar value (pricing is finance-gated — see inference.py's extension-point note)

AF-12 model-tier routing (ADR-0009 §2, maezo.runtime.inference.InferenceProvider.generate):
- maezo_llm_tier_resolution_total (Counter) — how a declared {task_kind,tier} actually resolved
  to a model. Exists because this repo configures ONE model and no per-tier map: the fallback is
  legitimate but must never be silent.

GAP-SC-04-a notifications-bridge dead-letter metering:
- maezo_bridge_dlq_total (Counter) — poison messages shunted to `<topic>.dlq` by {topic,reason}

CC-09 (Agent Fleet Audit, 2026-09-04) per-agent DESFECHO telemetry:
- maezo_agent_desfecho_total (Counter) — ONE terminal turn outcome by
  {agent_id,desfecho,route,motivo_categoria}. Emitted by
  `runtime.turn_telemetry.emit_turn_desfecho`, adopted at the terminal node of all 10 agent
  graphs (`complete`/`finalize`/`respond`/the two LGPD gates in Valentina) plus the ONE shared
  start-failure site (`runtime.start_outcome.notify_start_failure`). This is what makes the
  `agent.yaml` KPIs declared `track`/`>0.95`/`==0` (resolution_rate, escalation_precision,
  false_denial_rate, ...) actually MEASURABLE — before CC-09 no per-agent outcome telemetry
  existed at all (`grep -rln 'record_' src/maezo/agents/*/graph.py` was empty).

GAP 11.2 (round-5, 2026-09-05) `first_response_p95` — the one Helena KPI CC-09's
`agent_desfecho_total` cannot feed (it counts outcomes, not latency):
- maezo_agent_first_response_seconds (Histogram) — wall-clock from inbound receipt to turn
  completion by {agent_id}. Emitted by `platform.webhooks.whatsapp.dispatch.HelenaDispatcher
  .dispatch` — the ONE synchronous receive..respond chokepoint (module docstring: "each verified
  inbound WhatsApp message runs Helena's compiled graph to completion SYNCHRONOUSLY"), so this IS
  the round-trip a beneficiary experiences. `resolution_rate`/`escalation_rate` (the other two
  `agent.yaml` KPIs GAP 11.2 closes) need no new metric: both are RATIOS over the desfecho
  vocabulary `agent_desfecho_total` already emits (`desfecho="resolvido_automatico"` /
  `"escalado_humano"` for Helena) — see the `maezo_helena_resolution_rate` /
  `maezo_helena_escalation_rate` / `maezo_lucas_resolution_rate` recording rules in
  `deploy/observability/alert-rules.yml` (group `maezo_agent_kpi_derived`).

R-104/WP-ALERTA-SLA-CANAL notifications-bridge SLA-alert -> human task (SP-OP-ESCALATION-001):
- maezo_sla_alert_human_task_total (Counter) — what became of an SLA-risk alert, by
  {alert_domain,outcome}; `outcome` is "escalated" (a real SP-OP-ESCALATION-001 User Task was
  opened through the fenced chokepoint), "not_anchored" (a known SLA `type` whose payload lacked
  `tenant_id`/its business-key anchor -> the rule stayed fail-closed dormant) or
  "unrecognised_shape" (shaped like an SLA-risk alert but not yet in the spec table) — never a
  silent drop, see `notifications_bridge._record_sla_alert_outcome`

NEW-B1 (fleet hardening ciclo 2) A2A handler-failure metering:
- maezo_a2a_handler_error_total (Counter) — delegacoes cujo handler alvo falhou TERMINALMENTE, por
  {target,error_type}. Emitido por `a2a.dispatcher.DelegationDispatcher._reject_handler_error`.
  Contador PROPRIO e nao `maezo_agent_errors_total` de proposito — ver o comentario no
  `__init__` da classe: o handler A2A ja conta a sua propria falha de turno no `except` do
  `ainvoke`, e a falha que ESTA serie mede muitas vezes acontece antes de qualquer turno comecar.

WHICH OF THESE THE SHIPPED ALERTS READ (deploy/observability/alert-rules.yml, read-only here):
`maezo_worker_execution_time_seconds`, `maezo_worker_error_count_total`, `maezo_agent_errors_total`
and `maezo_tool_calls_total`. The last two had NO emitter in `src/` at all until AF-13
(ALERTS-WITHOUT-METRICS-a) — `MaezoSLAAgentErrorRateHigh` and `MaezoAgentCrashLoop` could not fire.
`tests/unit/platform/test_alert_metrics_fence.py` is the gate that keeps that from recurring.
"""

from __future__ import annotations

import structlog
from prometheus_client import CollectorRegistry, Counter, Histogram

from maezo.platform.error_types import (
    AGENT_ERROR_TYPE_NONE,
    AGENT_ERROR_TYPE_OUTRO,
    AGENT_ERROR_TYPE_RUNTIME,
    AGENT_ERROR_TYPE_TIMEOUT,
    AGENT_ERROR_TYPE_UPSTREAM_INDISPONIVEL,
    AGENT_ERROR_TYPE_VALIDACAO,
    AGENT_ERROR_TYPES,
    classify_agent_error_type,
)

logger = structlog.get_logger(__name__)

#: Re-exportados de `maezo.platform.error_types` (ver o bloco de comentario abaixo). Declarados em
#: `__all__` para que o re-export seja INTENCIONAL e nao um import acidentalmente nao usado.
__all__ = [
    "AGENT_ERROR_TYPES",
    "AGENT_ERROR_TYPE_NONE",
    "AGENT_ERROR_TYPE_OUTRO",
    "AGENT_ERROR_TYPE_RUNTIME",
    "AGENT_ERROR_TYPE_TIMEOUT",
    "AGENT_ERROR_TYPE_UPSTREAM_INDISPONIVEL",
    "AGENT_ERROR_TYPE_VALIDACAO",
    "MetricsCollector",
    "classify_agent_error_type",
]


# ---------------------------------------------------------------------------
# ALERT-COUNTER-LABELS / R-063 (owner-ratified 2026-09-04): o vocabulario FECHADO do label
# `error_type` dos contadores `maezo_tool_calls_total`/`maezo_agent_errors_total` abaixo MORA em
# `maezo.platform.error_types` — um modulo FOLHA (zero imports de `maezo`) — e e apenas
# RE-EXPORTADO por este modulo (import no topo + `__all__`), por compatibilidade com os call-sites
# que ja importavam estes nomes daqui.
#
# Por que a fonte nao mora mais neste arquivo: importar `maezo.runtime.metrics` dispara
# `maezo.runtime.__init__`, que importa `harness`/`checkpoint`/`inference` e portanto `langgraph`.
# `maezo.platform.observability` precisa de `AGENT_ERROR_TYPE_*`/`AGENT_ERROR_TYPES` no TOPO do
# arquivo e nao pode pagar esse acoplamento em tempo de import — nem fechar o ciclo
# observability -> runtime -> harness -> observability. Ver o docstring de
# `src/maezo/platform/error_types.py` e a cerca `test_importing_observability_does_not_pull_the_agent_runtime`
# em `tests/unit/platform/test_alert_metrics_fence.py`.
# ---------------------------------------------------------------------------


class MetricsCollector:
    """Collector for Prometheus metrics: latency, error rate, tool call counters.

    Implements ADR-0010 observability requirements.
    Creates a dedicated CollectorRegistry so metrics don't collide
    with the default PROCESS_COLLECTOR or other libraries.

    M11: adds worker_execution_time and worker_error_count metrics
    with labels for worker name, topic, and error type.
    """

    def __init__(self) -> None:
        """Create the registry and register all metrics."""
        self._registry = CollectorRegistry(auto_describe=True)

        self._latency = Histogram(
            "maezo_agent_latency_seconds",
            "Agent interaction latency in seconds",
            registry=self._registry,
        )

        self._tool_calls = Counter(
            "maezo_tool_calls_total",
            "Total number of tool call invocations",
            labelnames=["agent", "error_type"],
            registry=self._registry,
        )

        self._errors = Counter(
            "maezo_agent_errors_total",
            "Total number of agent errors",
            labelnames=["agent", "error_type"],
            registry=self._registry,
        )

        # M11: Worker metrics
        self._worker_execution_time = Histogram(
            "maezo_worker_execution_time_seconds",
            "Worker execution time in seconds",
            labelnames=["worker", "topic"],
            registry=self._registry,
        )

        self._worker_error_count = Counter(
            "maezo_worker_error_count_total",
            "Worker error count by error type",
            labelnames=["worker", "topic", "error_type"],
            registry=self._registry,
        )

        # T1.1: external-task dispatch-outcome metrics (design §13, GAP-XOBS-4). Emitted by
        # `WorkerHarness._handle` (tools/workers/harness.py) on every terminal branch — distinct
        # from worker_execution_time/error_count above (those are per-worker, emitted by
        # `WorkerBase.run()`; these are per-dispatch, emitted once per external task regardless of
        # whether the topic is served by a WorkerBase or a raw handler).
        self._worker_task_total = Counter(
            "maezo_worker_task_total",
            "External-task dispatch outcome count",
            labelnames=["tenant", "topic", "outcome"],
            registry=self._registry,
        )

        self._worker_task_duration = Histogram(
            "maezo_worker_task_duration_seconds",
            "External-task handler wall-clock latency in seconds",
            labelnames=["tenant", "topic", "outcome"],
            registry=self._registry,
        )

        # T8: LLM token-metering (design: single seam in maezo.runtime.inference,
        # AnthropicInferenceProvider.generate). `provider`/`model` are a small, bounded set
        # (one provider today, a handful of model ids) — safe Prometheus label cardinality
        # per ADR-0010 discipline. `token_type` is "input" or "output". Deliberately NO
        # tenant/agent/thread label here: those are per-instance correlation, which belongs
        # in the structured log line this metric's emitter also writes (see
        # maezo.runtime.inference._emit_llm_token_usage), never on a metric label — same
        # rule `worker_task_total` documents for task/business-key identifiers above.
        self._llm_tokens = Counter(
            "maezo_llm_tokens_total",
            "LLM tokens consumed (COUNTS ONLY, never a cost value) by provider/model/token_type",
            labelnames=["provider", "model", "token_type"],
            registry=self._registry,
        )

        # DL-0043 leg (c): SHADOW telemetry for the PHI-in-business-keys remediation flag. Counts
        # business-key MINTS by which anchor the key was derived from — it is the owner's
        # evidence for deciding whether to ratify `spec/policies/privacy/
        # phi-business-key-remediation.yaml`. The `anchor="matricula"` series is exactly "how
        # many keys we minted today that WOULD have been pseudonymized under `pseudo_keys`".
        #
        # CONTENT-FREE BY CONSTRUCTION. Every label is a closed vocabulary: `family` in
        # {CANCEL, INAD}, `modo` in {off, scrub_only, pseudo_keys}, `anchor` in
        # {contrato, matricula, pseudo}. NO tenant, NO business key, NO matricula — the same rule
        # `worker_task_total` and `llm_tokens` document above, and the reason this counter can
        # exist at all while the thing it measures is PHI.
        self._phi_business_key_mint = Counter(
            "maezo_phi_business_key_mint_total",
            "Business-key mints by family and derivation anchor (DL-0043 shadow telemetry; "
            "COUNTS ONLY — never any key, tenant or matricula content)",
            labelnames=["family", "modo", "anchor"],
            registry=self._registry,
        )

        # AF-12 (ADR-0009 §2 "Routing por tarefa"): how each declared tier actually resolved.
        # CLOSED vocabularies only — `task_kind` in `inference.MODEL_TASK_KINDS`, `tier` in
        # `inference.MODEL_TIERS` plus the two sentinels, `resolution` in
        # `inference.TIER_RESOLUTIONS`. No agent id, no tenant, no prompt: same discipline
        # `llm_tokens` and `phi_business_key_mint` document above.
        self._llm_tier_resolution = Counter(
            "maezo_llm_tier_resolution_total",
            "Model-tier resolutions by task_kind/tier/resolution (ADR-0009 routing; COUNTS ONLY)",
            labelnames=["task_kind", "tier", "resolution"],
            registry=self._registry,
        )

        # GAP-SC-04-a (audit D5): the notifications-bridge's poison-message shunt. Counts
        # messages the bridge could not even classify and therefore moved to `<topic>.dlq`
        # instead of blocking the partition behind them (head-of-line blocking) or — worse —
        # dropping them.
        #
        # THIS IS A COUNTER, AND IT IS NOT `maezo_dead_letter_queue_size`. The alert rules
        # `MaezoDeadLetterBacklog`/`MaezoDeadLetterGrowth` (`deploy/observability/alert-rules.yml`)
        # read a GAUGE named `maezo_dead_letter_queue_size` — the CURRENT DEPTH of a DLQ topic.
        # Nothing in `src/` can honestly emit that: depth is a broker-side fact (records produced
        # minus records consumed by the DLQ's own reader), and this process only ever sees the
        # records IT produces. Emitting a src-side gauge would fabricate a number that drifts from
        # the broker the moment anyone drains the DLQ. The gauge belongs to a Kafka/JMX exporter
        # scrape job that does not exist yet (owner-gated — `docs/review-queue.md`); this counter
        # is the honest src-side signal, and `rate(maezo_bridge_dlq_total[5m])` is a real
        # INFLOW alert that needs no exporter.
        #
        # CONTENT-FREE BY CONSTRUCTION. `topic` is the bridge's own input topic (a bounded set —
        # one value in this build). `reason` is a CLOSED vocabulary
        # (`notifications_bridge.BRIDGE_DLQ_REASONS`), never the parser's error text, never any
        # part of the offending payload — same rule `worker_task_total` and `llm_tokens` document
        # above. A message that reaches the DLQ is by definition one nobody validated, so putting
        # anything derived from its bytes on a metric label would be the worst possible place for
        # it (unbounded cardinality AND potential PHI).
        self._bridge_dlq = Counter(
            "maezo_bridge_dlq_total",
            "Notifications-bridge poison messages shunted to a dead-letter topic (COUNTS ONLY; "
            "reason is a closed vocabulary, never payload-derived)",
            labelnames=["topic", "reason"],
            registry=self._registry,
        )

        # CC-09 (Agent Fleet Audit): per-agent terminal-turn outcome. Emitted by
        # `runtime.turn_telemetry.emit_turn_desfecho`, called from the terminal node of every
        # agent graph (`complete`/`finalize`/`respond`/Valentina's two LGPD gate terminals) and
        # from the one shared start-failure site (`runtime.start_outcome.notify_start_failure`).
        #
        # CLOSED VOCABULARIES ONLY, per agent (`turn_telemetry._DESFECHO_VOCAB`/`_ROUTE_VOCAB`/
        # `_MOTIVO_CATEGORIA_VOCAB`) — a value outside an agent's declared set is normalized to
        # "outro" BEFORE it reaches this label (never the raw string), same discipline
        # `worker_task_total`/`phi_business_key_mint`/`bridge_dlq` document above. NEVER a
        # tenant id, business key, or beneficiary/tenant identifier: every one of these four
        # labels is a bounded routing/outcome TOKEN, not per-instance data.
        self._agent_desfecho_total = Counter(
            "maezo_agent_desfecho_total",
            "Terminal turn outcome by agent/desfecho/route/motivo_categoria (CC-09; COUNTS "
            "ONLY, closed vocabularies per agent, never PHI or a business/tenant identifier)",
            labelnames=["agent_id", "desfecho", "route", "motivo_categoria"],
            registry=self._registry,
        )

        # TETO DE VOLUME (14/09/2026). Mensagens recusadas por limite, por tenant e por ESCOPO
        # (`conversa` = um numero em laco; `tenant` = pico agregado). CONTENT-FREE: nenhum dos dois
        # rotulos identifica pessoa, e o `conversation_id` NAO e' rotulo — ele tem cardinalidade de
        # beneficiario e viraria um identificador por serie temporal.
        self._mensagem_limitada = Counter(
            "maezo_webhook_mensagem_limitada_total",
            "Mensagens recusadas pelo teto de volume do canal, por tenant e escopo do teto "
            "(COUNTS ONLY, vocabulario fechado, nunca a conversa nem o texto)",
            labelnames=["tenant", "escopo"],
            registry=self._registry,
        )

        # RECUSA DE SAIDA (13/09/2026). Quantas vezes o texto redigido pelo modelo foi BARRADO
        # antes de sair, por grupo de padrao e rota. CONTENT-FREE: `motivo` e' um dos dois rotulos
        # fechados de `prompts.py` (nunca o texto nem o padrao exato, que vao para o log), e
        # `response_kind` e' o vocabulario fechado do grafo.
        #
        # POR QUE ELE IMPORTA MAIS QUE O NUMERO. Enquanto ele sobe, o prompt esta pedindo e o
        # modelo esta tentando assim mesmo — e' a medida de quanto a instrucao sozinha valeria.
        # Zero sustentado nao prova obediencia: prova que a cerca nao teve o que barrar NAQUELE
        # periodo, o que so' e' interpretavel junto do volume de turnos.
        self._agent_resposta_recusada = Counter(
            "maezo_agent_resposta_recusada_total",
            "Respostas redigidas pelo modelo que foram BARRADAS antes de chegar ao beneficiario, "
            "por agente/motivo/rota (COUNTS ONLY, vocabularios fechados, nunca o texto)",
            labelnames=["agent_id", "motivo", "response_kind"],
            registry=self._registry,
        )

        # CODIGO FORA DA TABELA DA POPULACAO (22/09/2026, revisao do CRITICO 2). Quantas vezes o
        # `sintoma_codigo` foi DESCARTADO em `helena.classify` por nao existir na tabela de red
        # flag que aquele turno ia consultar.
        #
        # POR QUE AS DUAS LABELS, e nao so' a populacao: o descarte tem DUAS causas que pedem
        # conduta oposta. `origem_populacao="mensagem"` e' o modelo devolvendo um par incoerente
        # (defeito de extracao -> prompt/allowlist); `origem_populacao="memoria"` e' a FUSAO tendo
        # imposto a populacao lembrada sobre um codigo legitimo do turno — a conversa pediatrica
        # em que a mae passa a falar de si ("eu estou com dor no peito"), a populacao pediatrica
        # entra por falta de lastro (F4) e um `dor_toracica` de ADULTO e' jogado fora. Somar as
        # duas no mesmo numero apagaria exatamente a diferenca que faz alguem agir.
        #
        # CONTENT-FREE: `populacao` e' o vocabulario fechado de cinco literais de `graph.
        # _VALID_POPULATIONS` e `origem_populacao` tem dois valores. O CODIGO recusado NAO e'
        # label — ele vai para a linha de log, pela mesma disciplina de `agent_resposta_recusada`,
        # que deixa o padrao exato fora do contador.
        self._helena_sintoma_fora_da_tabela = Counter(
            "maezo_helena_sintoma_fora_da_tabela_total",
            "sintoma_codigo DESCARTADO em helena.classify por nao existir na tabela de red flag "
            "da populacao do turno (COUNTS ONLY, vocabularios fechados, nunca o codigo)",
            labelnames=["populacao", "origem_populacao"],
            registry=self._registry,
        )

        # R-104/WP-ALERTA-SLA-CANAL. SLA-risk alerts turned into a human task, per
        # `notifications_bridge._record_sla_alert_outcome`. CONTENT-FREE BY CONSTRUCTION, same rule
        # as `bridge_dlq` above: `alert_domain` is a closed vocabulary (`notification_bridge.
        # SLA_ALERT_DOMAINS` -> recurso | programa | lgpd, plus the literal `unknown` for a
        # `<dominio>.notify_sla_risk` shape the spec table does not carry — the raw, producer-
        # controlled `type` is NEVER a label), `outcome` is closed ("escalated" | "not_anchored" |
        # "unrecognised_shape"). Never a tenant id, business key, or any payload byte.
        self._sla_alert_human_task = Counter(
            "maezo_sla_alert_human_task_total",
            "SLA-risk alert notifications turned into an SP-OP-ESCALATION-001 human task "
            "(COUNTS ONLY; alert_domain and outcome are closed vocabularies, never payload-derived)",
            labelnames=["alert_domain", "outcome"],
            registry=self._registry,
        )

        # D6-01: the effect-chokepoint rate limit. Counts gated calls REFUSED by
        # `gateway/rate_limit.py` inside `gateway/seams/_base.py::gate` — never a silent drop.
        #
        # LABELS ARE THE THROTTLE KEY AND NOTHING ELSE. `tenant` and `principal` are exactly the
        # pair the bucket is keyed by, both bounded non-PHI tokens validated at `SeamContext`
        # construction (`effect_pep._TOKEN_RE`) and both of small, closed cardinality (tenants;
        # ~10 agent ids plus `worker_runtime`/`notifications_bridge`). `operation` is DELIBERATELY
        # absent: an uncatalogued operation token is reachable at this point (it is an L-0 deny,
        # not a construction error), so it is not a bounded label — it goes on the structured log
        # line instead, the same rule `worker_task_total` documents for business identifiers.
        self._effect_rate_limited = Counter(
            "maezo_effect_rate_limited_total",
            "Gated effect calls refused by the chokepoint rate limit (COUNTS ONLY)",
            labelnames=["tenant", "principal"],
            registry=self._registry,
        )

        # GAP 11.2: `first_response_p95` (spec/agents/helena/agent.yaml, target "<15s"). The ONE
        # `agent.yaml` outcome-latency KPI `agent_desfecho_total` cannot feed — that counter says
        # WHAT happened, never HOW LONG it took. `agent_id` is the same closed, small vocabulary
        # `agent_desfecho_total` already uses (today: only "helena", the one agent that declares
        # this KPI and runs a synchronous receive..respond webhook path at all) — never a
        # conversation id, phone hash or business key.
        #
        # WHAT IS OBSERVED, AND WHY THIS SHAPE. The single emitter
        # (`platform.webhooks.whatsapp.dispatch.HelenaDispatcher.dispatch`) times its own
        # `compiled.ainvoke(...)` call — wall-clock from the moment the verified inbound webhook
        # is being handled to the moment Helena's graph run returns. Every completed turn reaches
        # `respond()` (the graph's one terminal node) exactly once and attempts exactly one
        # WhatsApp send there, whatever the outcome (`resolvido_automatico`/`escalado_humano`/a
        # technical-start-failure all go through the SAME `respond()` send), so this genuinely
        # measures "how long until Helena tried to reply" for every turn. It is NOT gated on the
        # send's own success/failure: `HelenaState` carries no live `mensagem_enviada` field
        # (`runtime.turn_telemetry`'s own HELENA note — the local `enviada` bool `respond()`
        # computes never reaches the returned state dict), so a transport failure is honestly
        # indistinguishable from a delivered reply at this chokepoint. A raised exception (the
        # `ainvoke` itself failing) is NOT observed — no reply was even attempted.
        self._agent_first_response_seconds = Histogram(
            "maezo_agent_first_response_seconds",
            "Wall-clock from inbound receipt to turn completion by agent_id (GAP 11.2, "
            "first_response_p95). Not gated on transport-send success — see construction comment.",
            labelnames=["agent_id"],
            registry=self._registry,
        )

        #
        # NEW-B1. DELIBERADAMENTE UM CONTADOR PROPRIO, nao `maezo_agent_errors_total`. Os quatro
        # handlers A2A vivos ja contam a SUA falha de turno no proprio `except` do
        # `compiled.ainvoke(state)` e RELEVANTAM; se o dispatcher somasse no MESMO contador, uma
        # excecao de classe `validacao` vinda de dentro do grafo seria contada DUAS vezes para uma
        # unica falha logica, inflando o numerador de `MaezoSLAAgentErrorRateHigh`
        # (`sum by (agent) (rate(maezo_agent_errors_total[5m])) / ...`) — quebrando a invariante
        # "EXATAMENTE UMA CONTAGEM POR TURNO FALHO" que o docstring de
        # `platform.observability.record_agent_error` sustenta. E sao eventos diferentes: a falha
        # que este contador mede acontece frequentemente ANTES de qualquer turno comecar (o
        # `ValueError` de `state_from_envelope` e levantado fora do `try` do handler, sem nenhum
        # `ainvoke`), entao ela nao e um "agent error" — e uma delegacao que nao pode ser
        # executada. Rotulos limitados: `target` so pode ser um agent id REGISTRADO (`_validate`
        # rejeita o resto com `no_handler` antes de rotear) e `error_type` e o vocabulario fechado
        # de `platform/error_types.py` (ALERT-COUNTER-LABELS / R-063). Nenhuma regra de alerta
        # embarcada le esta serie — a mesma postura de `maezo_effect_rate_limited_total`; propor a
        # regra exige editar `deploy/observability/alert-rules.yml`, que e owner-gated.
        self._a2a_handler_errors = Counter(
            "maezo_a2a_handler_error_total",
            "A2A delegations whose target handler failed terminally (COUNTS ONLY)",
            labelnames=["target", "error_type"],
            registry=self._registry,
        )

        logger.info("metrics_collector_initialized")

    @property
    def registry(self) -> CollectorRegistry:
        """Return the dedicated Prometheus CollectorRegistry."""
        return self._registry

    @property
    def latency(self) -> Histogram:
        """Histogram for agent interaction latency."""
        return self._latency

    @property
    def tool_calls(self) -> Counter:
        """Counter for tool call invocations."""
        return self._tool_calls

    @property
    def errors(self) -> Counter:
        """Counter for agent errors."""
        return self._errors

    def agent_counter_labelnames(self) -> dict[str, tuple[str, ...]]:
        """The configured `labelnames` of `tool_calls`/`errors` (ALERT-COUNTER-LABELS / R-063),
        keyed by attribute name — the public accessor callers pinning the label-SHAPE contract
        should use instead of reaching into `prometheus_client.Counter`'s own internals directly.

        `prometheus_client.Counter` exposes NO public way to read a metric's configured label
        NAMES before its first observation: `collect()` — the one public introspection path —
        yields zero `Sample`s until `.labels(...).inc()` has run at least once (a labelled metric
        with no observation yet is indistinguishable, via `collect()`, from one that will never be
        used). `_labelnames` (defined on `prometheus_client`'s own `MetricWrapperBase`, a class
        this repo does not own) is the only place the answer lives before that. This method
        confines that one unavoidable reach-in to the single place that already owns and
        constructs both `Counter` instances, so every caller — starting with the test pinning the
        two agent counters' shared label set — reads a genuinely public contract instead.
        """
        return {
            name: counter._labelnames  # no public API exists; see docstring above.
            for name, counter in (("tool_calls", self._tool_calls), ("errors", self._errors))
        }

    @property
    def worker_execution_time(self) -> Histogram:
        """Histogram for worker execution time (M11).

        Labels: worker (class name), topic (external task topic).
        """
        return self._worker_execution_time

    @property
    def worker_error_count(self) -> Counter:
        """Counter for worker errors by type (M11).

        Labels: worker (class name), topic (external task topic),
        error_type (Exception class name).
        """
        return self._worker_error_count

    @property
    def worker_task_total(self) -> Counter:
        """Counter for external-task dispatch outcomes (T1.1, GAP-XOBS-4).

        Labels: tenant, topic (external task topic), outcome (one of
        `harness.WORKER_TASK_OUTCOMES`: completed | bpmn_error | failed | incident).
        """
        return self._worker_task_total

    @property
    def worker_task_duration(self) -> Histogram:
        """Histogram for external-task handler wall-clock latency (T1.1, GAP-XOBS-4).

        Same label set as `worker_task_total`.
        """
        return self._worker_task_duration

    @property
    def llm_tokens(self) -> Counter:
        """Counter for LLM token consumption (T8, token-metering).

        Labels: provider, model, token_type ("input" | "output"). COUNTS ONLY — never
        incremented with, or converted to, a cost/dollar value.
        """
        return self._llm_tokens

    @property
    def llm_tier_resolution(self) -> Counter:
        """Counter for model-tier resolutions (AF-12, ADR-0009 §2).

        Labels: task_kind ("task_default" | "reasoning" | "batch"), tier ("fast" | "frontier" |
        "batch" | "nao_declarado" | "sem_mapa"), resolution (see `inference.TIER_RESOLUTIONS`).
        """
        return self._llm_tier_resolution

    @property
    def effect_rate_limited(self) -> Counter:
        """Counter for effect calls refused by the chokepoint rate limit (D6-01).

        Labels: tenant, principal — the (tenant, principal) pair the token bucket is keyed by.
        COUNTS ONLY; no operation, no call argument, no business identifier.
        """
        return self._effect_rate_limited

    @property
    def agent_first_response_seconds(self) -> Histogram:
        """Histogram for per-turn inbound-to-reply-attempt latency (GAP 11.2, first_response_p95).

        Labels: agent_id (closed vocabulary, same as `agent_desfecho_total`; only "helena" today).
        Not gated on transport-send success — see the construction comment for why.
        Scope boundary: a non-text inbound handled by `acknowledge_non_text` (which never calls
        `dispatch()`) is NOT observed here — only the text conversational path this KPI targets.
        """
        return self._agent_first_response_seconds

    @property
    def a2a_handler_errors(self) -> Counter:
        """Counter for A2A delegations whose target handler failed terminally (NEW-B1).

        Labels: target (a REGISTERED agent id — `DelegationDispatcher._validate` rejects anything
        else with `no_handler` before routing), error_type (the closed `AGENT_ERROR_TYPES`
        vocabulary). COUNTS ONLY; no task_id, no chain, no payload reference.
        """
        return self._a2a_handler_errors

    @property
    def bridge_dlq(self) -> Counter:
        """Counter for notifications-bridge dead-letter shunts (GAP-SC-04-a).

        Labels: topic (the bridge's SOURCE topic, not the `.dlq` name), reason (one of
        `notifications_bridge.BRIDGE_DLQ_REASONS`). COUNTS ONLY — no payload byte, no offset, no
        business identifier ever reaches this metric.

        NOT the same series as the `maezo_dead_letter_queue_size` GAUGE the
        `MaezoDeadLetterBacklog`/`MaezoDeadLetterGrowth` alert rules read — see the construction
        comment for why `src/` cannot honestly emit that one.
        """
        return self._bridge_dlq

    @property
    def agent_desfecho_total(self) -> Counter:
        """Counter for per-agent terminal-turn outcomes (CC-09).

        Labels: agent_id (one of the 10 fleet agents), desfecho, route, motivo_categoria — the
        last three are CLOSED VOCABULARIES PER AGENT (`turn_telemetry` module), normalized to
        "outro" outside that set. This is the counter the `agent.yaml` KPIs (resolution_rate,
        escalation_precision, false_denial_rate, ...) are measured FROM.
        """
        return self._agent_desfecho_total

    @property
    def mensagem_limitada(self) -> Counter:
        """Counter for inbound messages refused by the channel's volume ceiling (Frente 7.1).

        Labels: tenant, escopo (`conversa` | `tenant` — the two closed scopes in
        `webhooks/whatsapp/limite.py`). The conversation is NOT a label: it has beneficiary
        cardinality and would turn into one time series per person.
        """
        return self._mensagem_limitada

    @property
    def agent_resposta_recusada(self) -> Counter:
        """Counter for model-drafted replies BLOCKED before reaching the beneficiary (13/09/2026).

        Labels: agent_id, motivo (a closed group declared in the agent's own `prompts.py` —
        Helena's are `negativa_clinica`, `promessa_de_humano`, `promessa_de_capacidade`, and,
        since 21/09/2026, the three the TEXT-vs-FACT fence adds: `promessa_sem_start`,
        `handoff_sem_mencao`, `escalonamento_ja_aberto`), response_kind (the graph's closed route
        vocabulary). The exact matched pattern is NOT a label: it goes to the log line, so this
        counter's cardinality does not grow when the pattern list does.

        `promessa_de_humano` vs `promessa_sem_start` is the distinction the 21/09 battery bought:
        the first means the SENTENCE was wrong for that route, the second that the route was right
        and the FACT was missing (the start never happened). Aggregating them would erase it.
        """
        return self._agent_resposta_recusada

    @property
    def helena_sintoma_fora_da_tabela(self) -> Counter:
        """Counter for `sintoma_codigo` DISCARDED by `helena.classify` (22/09/2026).

        Labels: `populacao` (the closed five of `graph._VALID_POPULATIONS` — the population the
        turn actually consulted, i.e. the one AFTER the clinical-memory fusion) and
        `origem_populacao` (`memoria` | `mensagem`). The discarded CODE is not a label: it goes
        to the `helena_sintoma_fora_da_tabela_da_populacao` log line, so this counter's
        cardinality does not grow with the allowlist.

        `mensagem` vs `memoria` is the whole point — see the construction comment: the first is a
        model that hallucinated an incoherent pair, the second is the fusion imposing a remembered
        population over a legitimate code, and only the second is a defect of THIS repo's rules.
        """
        return self._helena_sintoma_fora_da_tabela

    @property
    def sla_alert_human_task(self) -> Counter:
        """Counter for SLA-risk alerts turned into a human task (R-104/WP-ALERTA-SLA-CANAL).

        Labels: alert_domain (closed — recurso | programa | lgpd | unknown), outcome
        ("escalated" | "not_anchored" | "unrecognised_shape"). COUNTS ONLY — see construction
        comment.
        """
        return self._sla_alert_human_task

    @property
    def phi_business_key_mint(self) -> Counter:
        """Counter for business-key mints by derivation anchor (DL-0043 shadow telemetry).

        Labels: family ("CANCEL" | "INAD"), modo ("off" | "scrub_only" | "pseudo_keys"),
        anchor ("contrato" | "matricula" | "pseudo"). COUNTS ONLY — no key, tenant or
        matricula value ever reaches this metric.
        """
        return self._phi_business_key_mint
