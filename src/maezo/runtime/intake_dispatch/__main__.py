"""Entry point: `python -m maezo.runtime.intake_dispatch [--once]`.

Composition root. Importing this module has no side effect (the pattern
`runtime/worker_runtime/__main__.py` and `a2a/outbox_relay.py` follow), so `--help`, the console
script and the unit tests never open a database or read protected material.

Explicitly invoked, never auto-started: the portal's own composition root does not run this loop.
Draining an admitted intake is an engine EFFECT, and a deployment decides when a tenant's AUTH
installation is allowed to perform one. The chart ships the Deployment gated `enabled: false`.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import signal
from pathlib import Path
from typing import Final

import structlog

from maezo.gateway.intake.native_authority import _engine as protected_engine
from maezo.gateway.intake.native_authority import load_auth_lifecycle
from maezo.gateway.intake.published_authority import PublishedIntakeAuthority

from .assembly import PostgresPendingIntakes, PublishedCommandSource
from .service import IntakeDispatchService, run_dispatch_loop
from .settings import IntakeDispatchSettings

logger = structlog.get_logger(__name__)

#: Floor on the heartbeat staleness threshold, in seconds, and the same formula (and the same
#: reason) as `a2a/outbox_relay.py:heartbeat_stale_after_s`: three sweeps of slack, floored so a
#: misconfigured sub-second interval cannot produce a probe that never passes.
MIN_HEARTBEAT_STALE_AFTER_S: Final[float] = 5.0

_heartbeat_write_failure_logged = False


def heartbeat_stale_after_s(poll_interval_s: float) -> float:
    """Pure: the liveness probe's staleness threshold for a given poll interval."""
    return max(3.0 * poll_interval_s, MIN_HEARTBEAT_STALE_AFTER_S)


def touch_heartbeat(path: str | None) -> None:
    """Update the heartbeat file's mtime — the liveness probe's only progress signal.

    Empty/`None` disables it. A write failure is logged ONCE for the life of the process and then
    swallowed: an observability side-channel must never restart a daemon that is draining fine.
    """
    global _heartbeat_write_failure_logged
    if not path:
        return
    try:
        Path(path).touch()
    except OSError as exc:
        if not _heartbeat_write_failure_logged:
            logger.warning("intake_dispatch_heartbeat_write_failed", path=path, error=str(exc))
            _heartbeat_write_failure_logged = True


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m maezo.runtime.intake_dispatch",
        description=(
            "Drain the AUTH intake outbox (portal_intake.native_outbox) through the audited start "
            "seam. Explicitly invoked — never auto-started by the portal."
        ),
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="run a single drain sweep and exit (default: poll until SIGTERM/SIGINT)",
    )
    return parser


async def main(argv: list[str] | None = None) -> None:
    args = build_arg_parser().parse_args(argv)
    settings = IntakeDispatchSettings()
    # Refuses by name before anything is opened (`settings.materials`).
    materials = settings.materials()
    # `_engine` is `native_authority`'s own constructor for these DSNs (asyncpg + TLS context +
    # single-connection pool + command timeout). Imported rather than re-implemented on purpose:
    # a second copy of those constraints in the daemon is a second thing to forget to harden.
    composition = load_auth_lifecycle(
        materials.lifecycle_path,
        tenant=materials.tenant,
        identity_writer=protected_engine(materials.identity_writer_url),
    )
    try:
        # Full installation qualification (roles, database/schema/relation pins, TLS) before the
        # first sweep. A daemon that cannot prove its installation must not claim a row.
        await composition.qualify()
        # The SAME authority the portal binds (`gateway/intake/published_authority.py`), so the
        # daemon can never be composed against a different reading of the published contracts than
        # the door that admitted the row. It is unused on the drain path (the effect authority is
        # re-read per command inside the seam) and is composed anyway rather than passed as `None`.
        components = composition.intake_components(PublishedIntakeAuthority(composition.native))
        service = IntakeDispatchService(
            pending=PostgresPendingIntakes(composition.source.protected),
            commands=PublishedCommandSource(
                composition=composition,
                intake_store=components.admission,
                definition=materials.definition,
            ),
            target=components,
            receipts=composition.source.protected,
            batch_size=settings.batch_size,
        )

        stop_event = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            with contextlib.suppress(NotImplementedError):  # pragma: no cover - e.g. Windows
                loop.add_signal_handler(sig, stop_event.set)

        logger.info(
            "intake_dispatch_started",
            tenant=materials.tenant,
            batch_size=settings.batch_size,
            poll_interval_s=settings.poll_interval_s,
            once=args.once,
        )
        await run_dispatch_loop(
            service,
            poll_interval_s=settings.poll_interval_s,
            stop_event=stop_event,
            max_sweeps=1 if args.once else None,
            heartbeat=lambda: touch_heartbeat(settings.heartbeat_path),
        )
    finally:
        await composition.close()
        logger.info("intake_dispatch_stopped")


if __name__ == "__main__":  # pragma: no cover - process entry point
    asyncio.run(main())
