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

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import structlog

from maezo.tools.mcp_cibseven.transport import CibSevenHttpTransport

if TYPE_CHECKING:
    from maezo.tools.mcp_cibseven.transport import (
        CibSevenTransport,
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
        auth_token: str | None = None,
        timeout: float = 15.0,
        transport_factory: Callable[[], CibSevenTransport] | None = None,
    ) -> None:
        self._base_url = base_url
        self._auth_token = auth_token
        self._timeout = timeout
        # `transport_factory` is a DI seam (tests assert per-call construction with a spy factory);
        # production leaves it None -> a fresh `CibSevenHttpTransport` per call.
        self._transport_factory = transport_factory

    def _new_transport(self) -> CibSevenTransport:
        if self._transport_factory is not None:
            return self._transport_factory()
        return CibSevenHttpTransport(self._base_url, auth_token=self._auth_token, timeout=self._timeout)

    async def find_active_instance(self, business_key: str) -> ProcessInstance | None:
        inner = self._new_transport()
        try:
            return await inner.find_active_instance(business_key)
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
