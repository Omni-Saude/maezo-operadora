"""Entrypoint `python -m maezo.platform.webhooks` (T1.6, defect B1).

Before this build, `deployment-webhook-receiver.yaml`'s `command: ["python", "-m",
"maezo.platform.webhooks"]` pointed at a module that did not exist at all — and
`webhookReceiver.enabled: true` in `values.yaml` (unlike `gateway`, this one was already ON)
meant every environment applying the chart got a CrashLoopBackOff pod. This module removes that
defect.

`WhatsAppWebhookSettings()` fails closed (constraint 2): a missing `WHATSAPP_APP_SECRET` /
`WHATSAPP_VERIFY_TOKEN` raises a pydantic `ValidationError` right here, before any server binds —
this is INTENTIONAL (see `settings.py`'s module docstring) and matches
`deployment-webhook-receiver.yaml:43-46`'s own documented expectation.

Importing this module has NO side effect (no `asyncio.run` at import time) — matches
`worker_runtime`/`agent_runtime`/`gateway`'s `__main__.py` (T1.1/T1.6).
"""

from __future__ import annotations

import asyncio

from .service import run
from .whatsapp.settings import WhatsAppWebhookSettings


def main() -> None:
    """Console-script entrypoint (`maezo-webhook-receiver`) — identical to running this module
    directly."""
    # mypy sees `app_secret`/`verify_token` as required constructor kwargs (no default, by
    # design — settings.py's fail-closed rationale); pydantic-settings fills them from the
    # environment at RUNTIME, which mypy cannot see statically. Known pydantic-settings/mypy
    # friction, not a masked bug — construction still raises `ValidationError` if the env vars
    # are genuinely absent.
    asyncio.run(run(WhatsAppWebhookSettings()))  # type: ignore[call-arg]


if __name__ == "__main__":
    main()
