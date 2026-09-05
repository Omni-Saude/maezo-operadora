"""Vocabulario FECHADO do label `error_type` (ALERT-COUNTER-LABELS / R-063) — MODULO FOLHA.

Este modulo existe por uma razao de ARQUITETURA, nao de organizacao: ele nao importa NADA de
`maezo` (so `typing`), e o `__init__` do pacote que o hospeda (`maezo.platform`) tambem e leve —
importa apenas `maezo.platform.erasure` e `maezo.platform.retention`, que por sua vez nao importam
nada de `maezo` (stdlib + `structlog`).

Por que isso importa (invariante "sem acoplamento em tempo de import" de
`maezo.platform.observability`): `maezo.platform.observability` e a fronteira que
`maezo.gateway` chama de forma preguicosa justamente "para manter o nucleo de politica do gateway
livre de acoplamento em tempo de import a pilha de observabilidade". Se as constantes deste modulo
morassem em `maezo.runtime.metrics`, um `from maezo.runtime.metrics import ...` no topo de
`observability.py` dispararia `maezo.runtime.__init__`, que importa `harness`/`checkpoint`/
`inference` — e portanto `langgraph` — em TODO import de `maezo.platform.observability`. Pior: isso
fecha um CICLO, porque `maezo.runtime.harness` importa `record_agent_error` deste mesmo
`observability` (hoje de forma preguicosa; qualquer futuro import no topo passaria a levantar
`ImportError: partially initialized module`).

A cerca que trava isso e
`tests/unit/platform/test_alert_metrics_fence.py::test_importing_observability_does_not_pull_the_agent_runtime`
(mede num interpretador NOVO, por subprocesso, quantos modulos `maezo.*` e se `langgraph` entram
em `sys.modules` ao importar `maezo.platform.observability`).

`maezo.runtime.metrics` RE-EXPORTA todos estes nomes por compatibilidade — os call-sites que ja
importam de la (harness, os 4 handlers A2A de delegacao, o dispatcher WhatsApp) continuam validos e
nao pagam nada por isso: esses modulos ja carregam `langgraph` por conta propria.
"""

from __future__ import annotations

from typing import Final

# ---------------------------------------------------------------------------
# ALERT-COUNTER-LABELS / R-063 (owner-ratified 2026-09-04): closed `error_type` vocabulary for the
# `maezo_tool_calls_total`/`maezo_agent_errors_total` labels. Bounded on purpose — the raw
# exception CLASS NAME is unbounded (a new exception type would mint a new series forever, the
# exact cardinality hazard `worker_task_total`/`llm_tokens` warn against in
# `maezo/runtime/metrics.py`), so `classify_agent_error_type` maps every exception to one of these
# tokens and nothing else reaches the label.
# ---------------------------------------------------------------------------

AGENT_ERROR_TYPE_VALIDACAO: Final[str] = "validacao"
AGENT_ERROR_TYPE_TIMEOUT: Final[str] = "timeout"
AGENT_ERROR_TYPE_UPSTREAM_INDISPONIVEL: Final[str] = "upstream_indisponivel"
AGENT_ERROR_TYPE_RUNTIME: Final[str] = "runtime"
AGENT_ERROR_TYPE_OUTRO: Final[str] = "outro"

#: Sentinel for `maezo_tool_calls_total`'s `error_type` label. A tool call is counted on every
#: gated invocation regardless of outcome (`_count_tool_call`, gateway/seams/_base.py) — it is not
#: about an error at all, so "error_type" has no natural value there. This token exists ONLY so
#: both counters share the same label SET, which is what lets
#: `sum by (agent) (rate(maezo_agent_errors_total[5m])) / sum by (agent)
#: (rate(maezo_tool_calls_total[5m]))` (alert-rules.yml `MaezoSLAAgentErrorRateHigh`) evaluate per
#: agent without PromQL's default vector matching failing on a mismatched label set.
AGENT_ERROR_TYPE_NONE: Final[str] = "nao_aplicavel"

AGENT_ERROR_TYPES: Final[frozenset[str]] = frozenset(
    {
        AGENT_ERROR_TYPE_VALIDACAO,
        AGENT_ERROR_TYPE_TIMEOUT,
        AGENT_ERROR_TYPE_UPSTREAM_INDISPONIVEL,
        AGENT_ERROR_TYPE_RUNTIME,
        AGENT_ERROR_TYPE_OUTRO,
        AGENT_ERROR_TYPE_NONE,
    }
)

#: Exact exception CLASS NAME -> bounded `error_type` token. Deliberately an EXACT lookup (no
#: isinstance/MRO walk): a subclass not listed here falls back to `AGENT_ERROR_TYPE_OUTRO` rather
#: than silently inheriting a category nobody reviewed for it.
_AGENT_ERROR_TYPE_BY_EXCEPTION_CLASS: Final[dict[str, str]] = {
    "ValueError": AGENT_ERROR_TYPE_VALIDACAO,
    "TypeError": AGENT_ERROR_TYPE_VALIDACAO,
    "KeyError": AGENT_ERROR_TYPE_VALIDACAO,
    "ValidationError": AGENT_ERROR_TYPE_VALIDACAO,  # pydantic
    "TimeoutError": AGENT_ERROR_TYPE_TIMEOUT,
    "ConnectTimeout": AGENT_ERROR_TYPE_TIMEOUT,  # httpx
    "ReadTimeout": AGENT_ERROR_TYPE_TIMEOUT,  # httpx
    "ConnectionError": AGENT_ERROR_TYPE_UPSTREAM_INDISPONIVEL,
    "ConnectError": AGENT_ERROR_TYPE_UPSTREAM_INDISPONIVEL,  # httpx
    "HTTPStatusError": AGENT_ERROR_TYPE_UPSTREAM_INDISPONIVEL,  # httpx
    "OSError": AGENT_ERROR_TYPE_UPSTREAM_INDISPONIVEL,
    "RuntimeError": AGENT_ERROR_TYPE_RUNTIME,
}


def classify_agent_error_type(exc: BaseException) -> str:
    """Map one exception instance to a bounded `error_type` label value (ALERT-COUNTER-LABELS / R-063).

    Exact `type(exc).__name__` lookup against `_AGENT_ERROR_TYPE_BY_EXCEPTION_CLASS`; anything not
    listed maps to `AGENT_ERROR_TYPE_OUTRO` — never the raw class name, never free text. Never
    raises: a classification fault must not be able to break the `except` block calling it.
    """
    return _AGENT_ERROR_TYPE_BY_EXCEPTION_CLASS.get(type(exc).__name__, AGENT_ERROR_TYPE_OUTRO)
