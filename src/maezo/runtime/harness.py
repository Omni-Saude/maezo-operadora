"""LangGraph StateGraph harness for agent runtime.

Integrates the checkpointer (state persistence) and inference provider (LLM)
into a runnable LangGraph state machine. This is the primary entry point
for agent execution.

T1.11 (defect B6): `create_graph` previously always built a trivial, hardcoded default graph —
no production code ever invoked an agent-specific `build()`. `create_graph(agent_id=...)` now
resolves the REAL per-agent graph via the T0.3 `AgentLoader` conventions (`spec/agents/<id>/
agent.yaml` existence + `maezo.agents.<id>.graph:build`), matching every agent.yaml's declared
`graph: graph.py:build`. Calling `create_graph()` with no `agent_id` (the default) preserves the
EXACT prior trivial-default behavior — existing callers/tests are unaffected.

Fail-closed (constraint 2): an `agent_id` with no `spec/agents/<id>/agent.yaml`, no importable
`graph` module, or no callable `build` raises `UnknownAgentError` — never a silent fallback to
the trivial default graph.
"""

from __future__ import annotations

import importlib
import inspect
from collections.abc import Mapping
from typing import Any, TypedDict, cast

import structlog
from langchain_core.runnables.config import RunnableConfig
from langgraph.graph import StateGraph
from langgraph.graph.state import CompiledStateGraph

from maezo.runtime.checkpoint import Checkpointer, checkpoint_thread_config
from maezo.runtime.inference import InferenceProvider
from maezo.runtime.metrics import classify_agent_error_type

logger = structlog.get_logger(__name__)


class UnknownAgentError(ValueError):
    """`create_graph(agent_id=...)` was given an agent with no resolvable graph.

    Raised when `spec/agents/<agent_id>/agent.yaml` does not exist (via `AgentLoader`), the
    `maezo.agents.<agent_id>.graph` module is not importable, or it has no callable `build`.
    Fail-closed: never falls back to the trivial default graph (constraint 2).
    """


def _resolve_agent_build(agent_id: str) -> Any:
    """Resolve `agent_id`'s real `build` callable via T0.3 `AgentLoader` conventions.

    Validates `spec/agents/<agent_id>/agent.yaml` exists (the single source of truth, T0.3/B14)
    before even attempting the import — an agent with tools/graph but no agent.yaml is not a
    registered agent, regardless of whether a `graph.py` happens to sit on disk.
    """
    from maezo.agents import AgentLoader  # local import: avoids a hard agents<->runtime coupling

    try:
        AgentLoader().load_by_id(agent_id)
    except (FileNotFoundError, ValueError) as exc:
        raise UnknownAgentError(f"unknown agent {agent_id!r}: {exc}") from exc

    module_name = f"maezo.agents.{agent_id}.graph"
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        raise UnknownAgentError(
            f"agent {agent_id!r} has no importable graph module ({module_name}): {exc}"
        ) from exc

    build_fn = getattr(module, "build", None)
    if build_fn is None or not callable(build_fn):
        raise UnknownAgentError(f"agent {agent_id!r} graph module ({module_name}) has no callable build()")
    return build_fn


class AgentState(TypedDict, total=False):
    """Canonical agent state passed through the LangGraph nodes."""

    messages: list[str]


class Harness:
    """LangGraph harness integrating checkpointer and inference provider.

    Orchestrates the agent's StateGraph execution, wiring together
    the checkpointer for state persistence and the inference provider
    for LLM calls.

    Typical usage:
        harness = Harness(checkpointer=ck, inference=inf)
        graph = harness.create_graph()
        result = await harness.invoke({"messages": ["hello"]})
    """

    def __init__(
        self,
        checkpointer: Checkpointer | None = None,
        inference: InferenceProvider | None = None,
        tool_deps: Mapping[str, Any] | None = None,
    ) -> None:
        """Initialize the harness.

        Args:
            checkpointer: Optional checkpointer for state persistence.
            inference: Optional inference provider for LLM calls.
            tool_deps: Extra dependencies (e.g. `dmn`, `cibseven`, `whatsapp`, `fhir` transports)
                merged into the `config` dict passed to a real agent's `build(config)` when
                `create_graph(agent_id=...)` is used. Ignored by the trivial-default graph
                (`create_graph()` with no `agent_id`).
        """
        self._checkpointer = checkpointer
        self._inference = inference
        self._tool_deps: dict[str, Any] = dict(tool_deps) if tool_deps else {}
        self._graph: StateGraph[Any] | None = None
        self._compiled: CompiledStateGraph[Any, None, Any, Any] | None = None
        self._agent_id: str | None = None  # set by create_graph; the turn-telemetry label (G3)
        logger.info(
            "harness_initialized",
            has_checkpointer=checkpointer is not None,
            has_inference=inference is not None,
            tool_deps=sorted(self._tool_deps),
        )

    def create_graph(self, agent_id: str | None = None) -> StateGraph[Any]:
        """Create and return a LangGraph StateGraph for the agent.

        With no `agent_id` (default): the trivial default graph — a single 'agent' node that
        processes messages through the inference provider. Preserved EXACTLY as before T1.11 for
        back-compat with existing callers/tests.

        With `agent_id`: resolves and invokes the agent's REAL `build(config)` (T1.11, defect
        B6) via `AgentLoader`/`maezo.agents.<agent_id>.graph:build` conventions. `config` is
        `self._tool_deps` merged with `inference` and a default `agent_version`. An agent whose
        `build` takes no parameters (e.g. the `_template` scaffold; post-B6 all 10 named
        agents expose `build(config)`) is called
        with no arguments — introspected via `inspect.signature`, never guessed by try/except
        (a real `TypeError` raised *inside* a real `build(config)` must propagate, not be
        misread as "this build takes no config").

        Raises:
            UnknownAgentError: `agent_id` has no `spec/agents/<agent_id>/agent.yaml`, no
                importable graph module, or no callable `build` — fail-closed, never a silent
                fallback to the trivial default graph (constraint 2).

        Returns:
            A configured StateGraph instance (not yet compiled).
        """
        if agent_id is None:
            graph: StateGraph[Any] = StateGraph(AgentState)

            async def agent_node(state: AgentState) -> dict[str, list[str]]:
                """Default agent node: process messages through inference."""
                messages: list[str] = list(state.get("messages", []))
                if self._inference:
                    last_msg = messages[-1] if messages else ""
                    response = await self._inference.generate(prompt=last_msg)
                    messages = [*messages, response]
                return {"messages": messages}

            graph.add_node("agent", agent_node)
            graph.add_edge("__start__", "agent")
            graph.add_edge("agent", "__end__")

            self._graph = graph
            self._agent_id = None  # trivial default graph — no named agent
            self._compiled = None  # reset compiled version
            logger.info("harness_graph_created")
            return graph

        build_fn = _resolve_agent_build(agent_id)
        params = inspect.signature(build_fn).parameters
        if params:
            config: dict[str, Any] = dict(self._tool_deps)
            config.setdefault("inference", self._inference)
            config.setdefault("agent_version", f"{agent_id}@v0")
            resolved_graph = build_fn(config)
        else:
            resolved_graph = build_fn()

        self._graph = resolved_graph
        self._agent_id = agent_id
        self._compiled = None
        logger.info("harness_graph_created", agent_id=agent_id)
        return cast("StateGraph[Any]", resolved_graph)

    def set_graph(self, graph: StateGraph[Any], *, agent_id: str | None = None) -> None:
        """Wire an already-built graph directly into this harness, bypassing the
        `AgentLoader`/`maezo.agents.<agent_id>.graph:build` resolution `create_graph` performs.

        The public counterpart to the three attribute writes `create_graph` itself makes
        (`_graph`/`_agent_id`/`_compiled`) — for a caller that already HAS a `StateGraph` from
        somewhere other than the agent registry (a test driving a deliberately-failing graph
        without constructing a real agent's full dependency set, an ad-hoc graph assembled
        outside `spec/agents/`) and wants `Harness.invoke`'s turn-execution seam (telemetry,
        error classification, compile-on-first-use) around it, without duplicating that seam or
        reaching into the harness's own internals to get it.

        Args:
            graph: the (uncompiled) StateGraph to wire in. `invoke()` compiles it lazily, exactly
                as it does after `create_graph()`.
            agent_id: the turn-telemetry label `record_agent_error`/`record_agent_turn` read
                (`Harness._agent_id`). `None` (the default) reproduces the "trivial default
                graph" case (label `"nao_declarado"`), matching `create_graph()`'s own
                no-`agent_id` behaviour.
        """
        self._graph = graph
        self._agent_id = agent_id
        self._compiled = None
        logger.info("harness_graph_set", agent_id=agent_id)

    async def invoke(self, state: dict[str, object], *, thread_id: str | None = None) -> dict[str, object]:
        """Compile the graph (if needed) and invoke it with the given state.

        Args:
            state: Initial state dict with 'messages' key.
            thread_id: PHI-safe checkpoint thread id (a KEYED `wa:{tenant}:hk1_{phone_hash}`
                conversation id or an `ESC-{tenant}-...` process business key). REQUIRED when a
                checkpointer is wired — langgraph cannot persist/resume without one, and this
                harness fail-closes rather than silently run stateless under a configured
                checkpointer. Validated PHI-safe via `checkpoint_thread_config` (a raw phone/CPF
                AND an unkeyed hash without the `hk1_` keyed-pseudonym marker are both refused).

        Returns:
            The final state after graph execution.
        """
        if self._graph is None:
            raise RuntimeError("No graph created. Call create_graph() first.")

        if self._compiled is None:
            checkpointer_arg = self._checkpointer.saver if self._checkpointer is not None else None
            self._compiled = self._graph.compile(checkpointer=checkpointer_arg)

        config: RunnableConfig | None = None
        if self._checkpointer is not None and self._checkpointer.saver is not None:
            if thread_id is None:
                raise ValueError(
                    "a checkpointer is wired but no thread_id was supplied — refusing to run a "
                    "checkpointed graph without a thread scope (langgraph requires one; running "
                    "stateless here would silently drop persistence)."
                )
            config = checkpoint_thread_config(thread_id)

        msgs = state.get("messages", [])
        logger.info(
            "harness_invoke",
            messages_count=len(msgs) if isinstance(msgs, list) else 0,
            thread_id=thread_id,
        )
        try:
            result = await self._compiled.ainvoke(state, config=config)
        except Exception as exc:
            # ALERTS-WITHOUT-METRICS-a: `maezo_agent_errors_total` is the numerator of
            # `MaezoSLAAgentErrorRateHigh` and the whole of `MaezoAgentCrashLoop`
            # (`deploy/observability/alert-rules.yml:36-52,:97-111`), and NOTHING in `src/`
            # incremented it — both alerts were unfireable. This is one of the TWO turn-execution
            # seams in the repo (the other is `platform/webhooks/whatsapp/dispatch.py`); counting
            # here rather than inside any agent graph is what keeps coverage independent of how
            # each of the ten graphs handles its own errors.
            #
            # `Exception`, NOT `BaseException`, ON PURPOSE: `asyncio.CancelledError` is a
            # BaseException and means "the pod is draining / the client hung up", not "the agent
            # failed". Counting it would make `MaezoAgentCrashLoop` (`rate(...[1m]) > 0` for 2m)
            # fire critical on every rolling deploy — a false positive that would train the
            # on-call to ignore exactly the alert this repair exists to make fireable.
            # The bare `raise` re-raises regardless, so nothing is swallowed either way.
            #
            # ALERT-COUNTER-LABELS / R-063: `agent`/`error_type` labels. `self._agent_id` is None
            # for the trivial default graph (`create_graph()` with no `agent_id`) — the
            # `"nao_declarado"` fallback is a bounded token, not a silent unlabelled series.
            from maezo.platform.observability import record_agent_error  # noqa: PLC0415

            record_agent_error(
                agent=self._agent_id or "nao_declarado",
                error_type=classify_agent_error_type(exc),
            )
            raise

        # G3: emit ONE PHI-gated turn-telemetry record now that the turn has completed — message
        # COUNTS and a HASHED conversation token only (never message content, never the raw
        # thread id / business key), via the platform's existing structured-log sink. The local
        # import mirrors inference.py's `_emit_llm_token_usage` (no import-time coupling to the
        # observability stack); `record_agent_turn` is itself best-effort and never raises.
        from maezo.platform.observability import record_agent_turn  # noqa: PLC0415

        out_msgs = result.get("messages")
        record_agent_turn(
            agent_id=self._agent_id,
            input_message_count=len(msgs) if isinstance(msgs, list) else 0,
            output_message_count=len(out_msgs) if isinstance(out_msgs, list) else 0,
            conversation_ref=thread_id,
        )
        return dict(result)
