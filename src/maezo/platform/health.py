"""Generic health/readiness/metrics ASGI app for long-running Maezo services (T1.1, design §12).

Reusable by any long-running service (worker-runtime today; agent-runtime later, T1.11). This
module is intentionally dependency-free of `maezo.runtime`/`maezo.gateway`/the engine — readiness
checks are INJECTED by the caller (the entrypoint wires the real ones: engine reachability,
worker-registry coverage, Kafka...). Only `fastapi`, `prometheus_client`, and `uvicorn` are used
here.

Endpoint contract
------------------
- ``GET /healthz`` — liveness. 200 ``{"status": "ok"}`` while running; during drain (SIGTERM) the
  caller flips ``is_live()`` to False and this responds 503 ``{"status": "shutting_down"}`` so the
  pod leaves the load-balancing rotation. Cheap — no dependency I/O (liveness must never depend on
  the engine).
- ``GET /readyz`` — readiness. Runs every check concurrently; each is isolated (an exception OR a
  timeout become ``CheckResult(healthy=False)`` — never a 500). 200
  ``{"ready": true, "checks": [...]}`` iff every check is healthy, else 503. Fail-closed: an empty
  check list means "nothing to verify" (200), never used to mask a genuine gap.
- ``GET /metrics`` — Prometheus exposition of the given (or default global) registry.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import asdict, dataclass

from fastapi import FastAPI
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, REGISTRY, CollectorRegistry, generate_latest
from uvicorn import Config, Server

#: Per-check timeout: one hung readiness probe (e.g. a stalled engine call) must never hang the
#: whole /readyz response. Generous but finite.
_READINESS_CHECK_TIMEOUT_SECONDS: float = 5.0


@dataclass(frozen=True, slots=True)
class CheckResult:
    """Result of one readiness check (JSON-serializable).

    `name` identifies the dependency (e.g. "engine_reachable", "workers_registered").
    `detail` carries the failure reason when `healthy` is False.
    """

    name: str
    healthy: bool
    detail: str | None = None


#: A readiness check is a named async callable returning a CheckResult. The caller wires the real
#: dependencies; this module stays agnostic.
ReadinessCheck = Callable[[], Awaitable[CheckResult]]


async def _run_one_check(check: ReadinessCheck) -> CheckResult:
    """Run one check, isolating exception/timeout into `CheckResult(healthy=False)`.

    Never propagates: a probe that raises or hangs becomes readiness=False with a detail, never
    an HTTP 500. The name is recovered from `__name__` when available.
    """
    name = getattr(check, "__name__", repr(check))
    try:
        return await asyncio.wait_for(check(), timeout=_READINESS_CHECK_TIMEOUT_SECONDS)
    except TimeoutError:
        return CheckResult(
            name=name,
            healthy=False,
            detail=f"timeout after {_READINESS_CHECK_TIMEOUT_SECONDS:.0f}s",
        )
    except Exception as exc:  # intentional isolation: a probe never fails /readyz's own request.
        return CheckResult(name=name, healthy=False, detail=f"{type(exc).__name__}: {exc}")


def create_health_app(
    *,
    readiness_checks: Sequence[ReadinessCheck],
    is_live: Callable[[], bool] | None = None,
    registry: CollectorRegistry | None = None,
) -> FastAPI:
    """Create the FastAPI app exposing `/healthz`, `/readyz`, `/metrics`.

    Args:
        readiness_checks: Checks injected by the caller; run concurrently on every `/readyz`
            call. Empty list -> `/readyz` always 200 (nothing to check).
        is_live: Liveness predicate. Default: always alive. The entrypoint swaps in a callable
            that returns False during drain.
        registry: Prometheus registry to expose. Default: the global `prometheus_client` registry.
    """
    live = is_live if is_live is not None else (lambda: True)
    reg = registry if registry is not None else REGISTRY

    # 9.7: OpenAPI/docs/redoc are disabled on purpose, not an oversight — this app exposes
    # exactly the three fixed, internal ops endpoints named in this function's docstring
    # (`/healthz`, `/readyz`, `/metrics`), never a public/evolving API surface, so a generated
    # schema would document nothing a k8s probe or a scrape config needs and would be one more
    # internal-topology surface reachable without auth. The published "contract" for these
    # endpoints is this docstring + `docs/runbooks/worker-runtime.md` (probe URLs, k8s
    # liveness/readiness wiring), not an OpenAPI document.
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> JSONResponse:
        if live():
            return JSONResponse(status_code=200, content={"status": "ok"})
        return JSONResponse(status_code=503, content={"status": "shutting_down"})

    @app.get("/readyz", include_in_schema=False)
    async def readyz() -> JSONResponse:
        results = await asyncio.gather(*(_run_one_check(c) for c in readiness_checks))
        all_healthy = all(r.healthy for r in results)
        body = {"ready": all_healthy, "checks": [asdict(r) for r in results]}
        return JSONResponse(status_code=200 if all_healthy else 503, content=body)

    @app.get("/metrics", include_in_schema=False)
    async def metrics() -> Response:
        return Response(content=generate_latest(reg), media_type=CONTENT_TYPE_LATEST)

    return app


def build_health_server(app: FastAPI, *, host: str = "0.0.0.0", port: int = 8000) -> Server:  # bind-all is intentional inside a pod's network namespace.
    """Build a controllable `uvicorn.Server` for the health app, without starting it.

    The caller runs `await server.serve()` as an asyncio task and, on shutdown, sets
    `server.should_exit = True` to stop it. `access_log=False` keeps Prometheus scrapes / kubelet
    probes out of the log stream.
    """
    config = Config(app, host=host, port=port, log_level="info", access_log=False)
    return Server(config)
