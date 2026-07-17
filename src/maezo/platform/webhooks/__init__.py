"""Webhook receivers (T1.6, defect B1).

Today: `whatsapp/` — the WhatsApp Cloud API inbound webhook (`deployment-webhook-receiver.yaml`,
`python -m maezo.platform.webhooks`). Real Meta handshake + HMAC signature verification; message
queuing is explicitly NOT wired yet (no downstream consumer — Helena's intake graph is T1.11).

Importing this package has no side effect (no `asyncio.run` at import time).
"""

from __future__ import annotations

from maezo.platform.webhooks.service import WebhookState, run

__all__ = ["WebhookState", "run"]
