"""Helena's `WhatsAppSender` adapter — now defined in `maezo.tools.mcp_whatsapp.sender` (ADR-0067).

The class adapts a platform tool and carries nothing of Helena's, so it moved to the tool's own
package; the gateway imports it from there. This module stays as Helena's re-export (an agent may
import the platform; the platform may not import an agent) and because accepted ADRs (0036, 0041)
cite this path for the boundary below.

The live path never uses it: a caller passing a phone-number HASH (Helena's graph only ever has a
hash, never a raw number — ADR-0006) would send to the hash literal, not a real recipient. The
per-turn resolution from hash back to the raw number is the dispatch driver's job
(`maezo.platform.webhooks.whatsapp.dispatch`) — no persistent, reversible hash->phone vault exists.
"""

from __future__ import annotations

from maezo.tools.mcp_whatsapp.sender import WhatsAppServerSender

__all__ = ["WhatsAppServerSender"]
