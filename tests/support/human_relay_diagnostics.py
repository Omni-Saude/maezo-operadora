"""D6-RELAY-EIR-01 disposable observations; never substitutes a receipt or decision.

The original transport and verifier execute unchanged. A ContextVar routes Python
trace events to the current request (including concurrent tasks and new children).
No frame locals, exception text, request headers, config or credentials are dumped.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import sys
import time
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path

import asyncpg

from maezo.gateway.human import projection, transport

_ACTIVE: ContextVar[dict | None] = ContextVar("human_relay_diagnostic_request", default=None)
_REQUEST_CODE = transport.MTLSHumanEngineTransport._request.__code__
_VERIFY_CODE = projection.verify_engine_receipt.__code__


def stamp() -> dict:
    return {
        "host_utc": datetime.now(UTC).isoformat(),
        "monotonic_ns": time.monotonic_ns(),
        "pid": os.getpid(),
    }


def observe(frame, event, arg):
    """Read only allowlisted synthetic values; return the actual frame trace hook."""
    record = _ACTIVE.get()
    if record is None or frame.f_code not in (_REQUEST_CODE, _VERIFY_CODE):
        return None
    entry = {"event": event, "function": frame.f_code.co_name, "line": frame.f_lineno, **stamp()}
    if frame.f_code is _REQUEST_CODE:
        response = frame.f_locals.get("response")
        if response is not None and "response" not in record:
            record["response"] = {
                "status": response.status_code,
                "content_type": response.headers.get("content-type", "")[:256],
                "server_date": response.headers.get("date", "")[:128],
                "observed": stamp(),
                "server_date_uncertainty": "HTTP server wall clock; whole seconds; not JVM sampling",
            }
        payload = frame.f_locals.get("payload")
        if payload is not None and "payload" not in record:
            record["payload"] = {
                "bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
                "base64": base64.b64encode(payload[:65536]).decode("ascii"),
                "truncated": len(payload) > 65536,
                "observed": stamp(),
            }
            # Raw bytes are evidence; parsing here would add another verifier.
    if event == "exception":
        partial = frame.f_locals.get("raw") if frame.f_code is _REQUEST_CODE else None
        if isinstance(partial, bytearray) and "payload" not in record:
            record["partial_payload"] = {
                "bytes_seen": len(partial),
                "bounded_base64": base64.b64encode(partial[:65536]).decode("ascii"),
                "bounded_sha256": hashlib.sha256(partial[:65536]).hexdigest(),
                "truncated": len(partial) > 65536,
                "observed": stamp(),
            }
        entry["exception_type"] = type(arg[1]).__module__ + "." + type(arg[1]).__qualname__
        # Original line identifies the inner guard even when production suppresses
        # its context. Never serialize exception messages or arbitrary locals.
    if event in {"line", "call", "return", "exception"}:
        record["trace"].append(entry)
    return observe


def install() -> None:
    existing = sys.gettrace()
    if existing is not None and existing is not observe:
        raise RuntimeError("disposable diagnostic refuses to replace another Python trace hook")
    sys.settrace(observe)


class BoundaryDiagnostics:
    def __init__(self, directory: Path, config):
        self.directory = directory
        self.config = config
        self.requests: list[dict] = []
        install()

    async def failed_clock(self, record: dict) -> None:
        """Separate connection AFTER rejection; never in an engine/outbox transaction.

        This is a bracketed delayed measurement, not an assertion that the clocks
        were equal at validation. It cannot retroactively prove the failed run.
        """
        measurement = {"before": stamp(), "position": "after_original_request_exception"}
        connection = None
        try:
            connection = await asyncpg.connect(**self.config.db_parameters(), timeout=3)
            row = await connection.fetchrow(
                "SELECT clock_timestamp() AS pg_clock, pg_backend_pid() AS pg_pid", timeout=3
            )
            measurement.update(pg_clock=row["pg_clock"].isoformat(), pg_pid=row["pg_pid"])
        except Exception as error:
            measurement["measurement_error_type"] = type(error).__qualname__
        finally:
            if connection is not None:
                await connection.close(timeout=3)
            measurement["after"] = stamp()
            record["postgres_clock"] = measurement

    def save(self) -> None:
        self.directory.mkdir(exist_ok=True)
        (self.directory / "boundary-diagnostics.json").write_text(
            json.dumps(
                {
                    "schema": "human-relay-boundary-diagnostic.v1",
                    "pid": os.getpid(),
                    "requests": self.requests,
                    "engine_clock_source": (
                        "actual receipt recorded_at from AtomicHumanCommand.persistReceipt; epoch seconds"
                    ),
                    "measurement_limits": (
                        "trace overhead; PG sampled after failure on separate connection; "
                        "HTTP Date is server clock with one-second resolution; "
                        "engine JVM PID requires ROOT external observation"
                    ),
                },
                indent=2,
            )
            + "\n"
        )


class DiagnosticMTLSHumanEngineTransport(transport.MTLSHumanEngineTransport):
    """The sole override delegates exactly once, returning/rethrowing unchanged."""

    diagnostics: BoundaryDiagnostics

    async def _request(self, command, *, query):
        record = {
            "method": "GET" if query else "POST",
            "command_id": command.command_id,
            "command_digest": command.digest,
            "task_id": command.task_id,
            "begin": stamp(),
            "trace": [],
        }
        self.diagnostics.requests.append(record)
        token = _ACTIVE.set(record)
        try:
            result = await super()._request(command, query=query)
        except BaseException as error:
            record["outcome"] = "raised"
            record["exception_type"] = type(error).__module__ + "." + type(error).__qualname__
            record["end"] = stamp()
            _ACTIVE.reset(token)
            # Do not sample for cancellation/process interruption or alter the
            # original exception if the auxiliary measurement itself fails.
            if isinstance(error, Exception):
                try:
                    await self.diagnostics.failed_clock(record)
                except Exception as measurement_error:
                    record["measurement_error_type"] = type(measurement_error).__qualname__
            raise
        else:
            record["outcome"] = "missing" if result is None else "receipt"
            record["end"] = stamp()
            _ACTIVE.reset(token)
            return result
