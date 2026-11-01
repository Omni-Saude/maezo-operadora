"""Worker-daemon engine seam — FRESH-CLIENT-PER-CALL `CibSevenTransport` (T1.10 T-D, GAP-INAD-1).

The composition root (`worker_runtime/service.py`) injects a `CibSevenTransport` into the
function workers that talk to the engine agent->engine (the residual ADR-0001 leg that
`tools/workers/harness.py` does NOT cover): `inadimplencia.resolve_facts`'s anti-dupla-terminacao
`find_active_instance` query and `inadimplencia.handoff_rescisao`'s CANCEL-001 `start_process`.

Those worker functions are SYNCHRONOUS by design (`FunctionWorker`) and the harness dispatches
each one on a FRESH OS thread via `asyncio.to_thread`; inside, the engine call is bridged with a
fresh `asyncio.run(...)` — i.e. a NEW event loop is created and closed PER dispatch (identical to
`dmn_transport.evaluate_sync`). `mcp_cibseven.transport.CibSevenHttpTransport` holds ONE
`httpx.AsyncClient` for its whole lifetime, whose connection pool binds to whichever loop first
uses it; a second worker call from a *different* fresh loop then dies with
``RuntimeError: Event loop is closed`` (the same real, live-reproduced defect
`CibSevenDmnTransport`'s docstring documents — that transport already builds a fresh client per
call for exactly this reason). Injecting a single shared `CibSevenHttpTransport` into the daemon
would therefore fail on the SECOND engine-touching task.

`FreshClientCibSevenTransport` is the fix: it owns NO persistent client and constructs a fresh
`CibSevenHttpTransport` (fresh `httpx.AsyncClient`) INSIDE each method call, then closes it in a
`finally`. Every call is self-contained within whatever loop invoked it — the proven
`CibSevenDmnTransport` fresh-client-per-call pattern, applied to the agent->engine transport. It
delegates to `CibSevenHttpTransport` for the actual REST/wire logic (the load-bearing `Json`
decode, `deserializeValues=false` handling, and `CibSevenError`/`ProcessNotFoundError`
fail-closed classification) rather than re-implementing it, so there is exactly one source of
truth for that behavior.

Cost: no cross-call connection pooling — dominated by (and consistent with) the per-task
fresh-OS-thread cost `asyncio.to_thread` already pays. Correctness beats a pool the event-loop
lifecycle would break anyway.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any

import structlog

from maezo.tools.mcp_cibseven.transport import (
    CibSevenError,
    CibSevenHttpTransport,
    HistoricInstanceResolvingTransport,
    HistoricVariableReadingTransport,
    HistoryQueryingTransport,
    UserTaskAssigneeReadingTransport,
    require_engine_tenant_token,
)

if TYPE_CHECKING:
    from maezo.tools.mcp_cibseven.transport import (
        CibSevenTransport,
        HistoricInstanceRecord,
        HistoricProcessVariables,
        ProcessInstance,
        ProcessStatus,
    )

logger = structlog.get_logger(__name__)


class FreshClientCibSevenTransport:
    """`CibSevenTransport` seam with FRESH-CLIENT-PER-CALL semantics (see module docstring).

    Structurally conforms to the `CibSevenTransport` Protocol (`mcp_cibseven/transport.py`) so it
    is a drop-in for any consumer of that seam — here, the `engine=` seam threaded into
    `inadimplencia`'s workers by `register_all_workers`. A fresh underlying `CibSevenHttpTransport`
    (hence a fresh `httpx.AsyncClient` bound to the calling loop) is built and closed inside every
    method, so consecutive calls from distinct `asyncio.run(...)` bridges never share — nor
    outlive — an event loop.
    """

    def __init__(
        self,
        base_url: str,
        *,
        tenant_id: str | None = None,
        auth_token: str | None = None,
        timeout: float = 15.0,
        transport_factory: Callable[[], CibSevenTransport] | None = None,
    ) -> None:
        self._base_url = base_url
        # Tenant do motor dos STARTS (ver `CibSevenHttpTransport.__init__`). Validado aqui, na
        # construcao, e nao so no primeiro start: composicao errada falha no boot.
        self._tenant_id = require_engine_tenant_token(tenant_id) if tenant_id is not None else None
        self._auth_token = auth_token
        self._timeout = timeout
        # `transport_factory` is a DI seam (tests assert per-call construction with a spy factory);
        # production leaves it None -> a fresh `CibSevenHttpTransport` per call.
        self._transport_factory = transport_factory

    def _new_transport(self) -> CibSevenTransport:
        if self._transport_factory is not None:
            return self._transport_factory()
        return CibSevenHttpTransport(
            self._base_url, tenant_id=self._tenant_id, auth_token=self._auth_token, timeout=self._timeout
        )

    @property
    def engine_tenant_id(self) -> str | None:
        """O tenant em que os starts nascem — conferido por `require_start_tenant`."""
        return self._tenant_id

    async def find_active_instance(self, business_key: str) -> ProcessInstance | None:
        inner = self._new_transport()
        try:
            return await inner.find_active_instance(business_key)
        finally:
            await inner.close()

    async def find_any_instance(self, business_key: str, *, process_key: str = "") -> ProcessInstance | None:
        """`HistoryQueryingTransport` delegation — a decorator that DROPPED this capability would
        turn any GATED start routed through the worker daemon into a fail-closed refusal
        (`StartDedupGateUnavailableError`), because `start_process_idempotent` probes the OUTER
        transport. NOT hypothetical since GAP-D3-02: the two CANCEL-001 starts this daemon owns
        (`inadimplencia.handoff_rescisao`, `fraude.start_contratual`) target an `EXCLUSIVE` family,
        so this delegation is now load-bearing for a live path rather than a future one.
        Production's inner is always `CibSevenHttpTransport`, which implements it; a test
        `transport_factory` injecting a history-blind double raises here rather than
        `AttributeError`-ing mid-gate."""
        inner = self._new_transport()
        try:
            if not isinstance(inner, HistoryQueryingTransport):
                raise CibSevenError(
                    f"inner transport {type(inner).__name__!r} does not implement "
                    "`find_any_instance` — cannot answer the engine-history question"
                )
            return await inner.find_any_instance(business_key, process_key=process_key)
        finally:
            await inner.close()

    async def read_user_task_assignee(self, process_instance_id: str, task_definition_key: str) -> str | None:
        """`UserTaskAssigneeReadingTransport` delegation (pendencia 40, DL-0152): quem assumiu a User
        Task ativa. Inner sem a capacidade LEVANTA `CibSevenError` — o worker de escalonamento le isso
        como leitura que falhou e ALERTA (lado seguro)."""
        inner = self._new_transport()
        try:
            if not isinstance(inner, UserTaskAssigneeReadingTransport):
                raise CibSevenError(
                    f"inner transport {type(inner).__name__!r} does not implement `read_user_task_assignee`"
                )
            return await inner.read_user_task_assignee(process_instance_id, task_definition_key)
        finally:
            await inner.close()

    async def read_historic_variables(
        self, process_instance_id: str, names: tuple[str, ...]
    ) -> HistoricProcessVariables | None:
        """`HistoricVariableReadingTransport` delegation. Existe para que `gate_cibseven` possa
        expor a leitura seguinte (que exige as tres pernas do historico); inner sem a capacidade
        LEVANTA `CibSevenError` — nunca uma leitura vazia lida como "nao ha nada"."""
        inner = self._new_transport()
        try:
            if not isinstance(inner, HistoricVariableReadingTransport):
                raise CibSevenError(
                    f"inner transport {type(inner).__name__!r} does not implement `read_historic_variables`"
                )
            return await inner.read_historic_variables(process_instance_id, names)
        finally:
            await inner.close()

    async def resolve_historic_instance(
        self,
        process_key: str,
        *,
        business_key: str = "",
        variable_equals: Mapping[str, str] | None = None,
        names: tuple[str, ...] = (),
    ) -> HistoricInstanceRecord:
        """`HistoricInstanceResolvingTransport` delegation (pacote PAGTO PR-D): a costura de PR-C
        chega ao daemon de workers SO' por aqui, e SO' atras do decorator cercado (C0
        `consulta_processo`). Inner sem a capacidade LEVANTA `CibSevenError` (o consumidor,
        `pagto_lastro.resolve_lastro`, le isso como `INDISPONIVEL`)."""
        inner = self._new_transport()
        try:
            if not isinstance(inner, HistoricInstanceResolvingTransport):
                raise CibSevenError(
                    f"inner transport {type(inner).__name__!r} does not implement `resolve_historic_instance`"
                )
            return await inner.resolve_historic_instance(
                process_key, business_key=business_key, variable_equals=variable_equals, names=names
            )
        finally:
            await inner.close()

    async def start_process_instance(
        self,
        process_key: str,
        business_key: str,
        variables: dict[str, Any],
    ) -> ProcessInstance:
        inner = self._new_transport()
        try:
            return await inner.start_process_instance(process_key, business_key, variables)
        finally:
            await inner.close()

    async def correlate_message(
        self,
        message_name: str,
        business_key: str,
        variables: dict[str, Any],
        *,
        correlation_keys: dict[str, Any] | None = None,
        all_matching: bool = False,
    ) -> None:
        inner = self._new_transport()
        try:
            await inner.correlate_message(
                message_name,
                business_key,
                variables,
                correlation_keys=correlation_keys,
                all_matching=all_matching,
            )
        finally:
            await inner.close()

    async def get_process_status(self, business_key: str) -> ProcessStatus:
        inner = self._new_transport()
        try:
            return await inner.get_process_status(business_key)
        finally:
            await inner.close()

    async def close(self) -> None:
        """No-op: no persistent client to close (fresh-client-per-call). Kept for
        `CibSevenTransport` lifecycle symmetry — the daemon calls it on drain."""
        return None
