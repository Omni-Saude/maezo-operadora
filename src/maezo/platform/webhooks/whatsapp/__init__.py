"""WhatsApp Cloud API webhook receiver (T1.6, defect B1).

Real signature verification (`security.py`) + Meta handshake (`app.py`), matching
`docs/runbooks/whatsapp-webhook.md` §1/§2. Message queuing (Kafka publish) is deliberately NOT
wired — see `app.py`'s module docstring: no downstream consumer exists yet (Helena's WhatsApp
intake graph is T1.11).
"""

from __future__ import annotations

from maezo.platform.webhooks.whatsapp.app import create_app
from maezo.platform.webhooks.whatsapp.security import hash_phone, verify_hub_signature
from maezo.platform.webhooks.whatsapp.settings import WhatsAppWebhookSettings

__all__ = [
    "WhatsAppWebhookSettings",
    "create_app",
    "hash_phone",
    "verify_hub_signature",
]
