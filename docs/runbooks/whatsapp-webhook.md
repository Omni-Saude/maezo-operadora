# Runbook: WhatsApp Webhook — Message ingestion & validation

**Audience:** Platform engineers, operations  
**Last updated:** 2026-06-12  
**Applies to:** Maezo Healthcare Plan Phase 0+

> **T1.6 implementation status (defect B1).** `deployment-webhook-receiver.yaml`'s
> `command: ["python", "-m", "maezo.platform.webhooks"]` used to point at a module
> that did not exist at all — with `webhookReceiver.enabled: true` already the
> default, this was a live CrashLoopBackOff. `src/maezo/platform/webhooks/` now
> exists and implements §1 (Meta GET-verification handshake) and §2 (real
> HMAC-SHA256 POST signature validation, timing-safe) exactly as documented below.
> **§3/§4 are NOT wired yet**: there is no idempotency store, no
> `WhatsAppMessageEvent`/`WhatsAppStatusEvent` normalization, and no Kafka publish
> in this build — a signature-verified POST returns **HTTP 501** with an explicit
> `"message queuing pending T1.11"` detail instead of silently pretending to queue
> the message (no downstream consumer exists yet: Helena's WhatsApp intake graph
> is T1.11's job). §5 ("Kafka producer unavailable") and the Kafka-publish rows of
> §7's metrics table describe **target** behavior once that wiring lands, not
> today's. Code: `src/maezo/platform/webhooks/whatsapp/{app,security,settings}.py`
> (not the `whatsapp/app.py`-only layout §1 implies — `security.py`/`settings.py`
> are separate modules, `idempotency.py` does not exist yet).

---

## Table of Contents

1. [Endpoint overview](#1-endpoint-overview)
2. [HMAC validation & security](#2-hmac-validation--security)
3. [Idempotency store configuration](#3-idempotency-store-configuration)
4. [Kafka topics produced](#4-kafka-topics-produced)
5. [Failure modes](#5-failure-modes)
6. [Local testing](#6-local-testing)
7. [Monitoring](#7-monitoring)

---

## 1. Endpoint overview

**Code:** `src/maezo/platform/webhooks/whatsapp/app.py`

Two endpoints:

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/webhook` | GET | Meta verification (hub.challenge handshake) |
| `/webhook` | POST | Event ingestion (messages + delivery status) |

### GET /webhook — Verification

Meta calls this with query params to verify webhook ownership:

```bash
GET /webhook?hub.mode=subscribe&hub.challenge=XXXX&hub.verify_token=YOUR_TOKEN
```

**Flow:**
1. Verify `hub.mode == "subscribe"`
2. Compare `hub.verify_token` (timing-safe comparison)
3. Return `hub.challenge` as plaintext if valid; 403 if invalid

**Env vars required:**
- `WHATSAPP_VERIFY_TOKEN` — configured in Meta app panel

### POST /webhook — Message/Status Ingestion

Meta sends inbound messages and delivery status updates.

**Flow:**
1. Read raw request body (needed for HMAC)
2. Validate `X-Hub-Signature-256` header
3. Parse JSON payload
4. Deduplicate by wamid (idempotency key)
5. Normalize to `WhatsAppMessageEvent` or `WhatsAppStatusEvent`
6. Hash phone number (PHI pseudonymization)
7. Publish to Kafka (fire-and-forget)
8. Return 200 OK (Meta requires < 20s)

---

## 2. HMAC validation & security

**Code:** `src/maezo/platform/webhooks/whatsapp/security.py`

### Signature validation

Meta sends `X-Hub-Signature-256: sha256=<hex_digest>` where the digest is HMAC-SHA256 of the raw body using the app secret.

**Validation:**
```python
from maezo.platform.webhooks.whatsapp.security import verify_hub_signature

is_valid = verify_hub_signature(
    payload=raw_body,  # bytes
    signature_header="sha256=...",
    app_secret="YOUR_SECRET"
)
```

**Env var:**
- `WHATSAPP_APP_SECRET` — Meta app secret (Secrets Manager in prod, **never in git**)

**Rejection:** Invalid signature → log + 401 Unauthorized

### Phone number pseudonymization

Phone numbers are pseudonymized with a **keyed HMAC-SHA256** before any log or Kafka event
(ADR-0006 General Zone; ADR-0035/ADR-0036 keyed identity — a bare `sha256(phone)` is trivially
reversible over the ~6.7e9 BR-mobile keyspace and is **forbidden** for any persisted identity):

```python
from maezo.platform.webhooks.whatsapp.security import hash_phone

phone_hash = hash_phone("+55 11 99999-9999", tenant="amh", pseudonymizer=pseudonymizer)
# Output: "hk1_" + 64-hex HMAC-SHA256(PHI_HMAC_KEY, "amh:+55 11 99999-9999")
```

The `pseudonymizer` is built once at the composition root (`webhooks/service.py`) via
`Pseudonymizer.from_settings`, which **fails closed in production** when `PHI_HMAC_KEY` is
absent/blank (`PseudonymizerKeyMissingError`) — there is no unkeyed fallback path in prod.
The raw phone is **never** stored, logged, or sent to Kafka.

---

## 3. Idempotency store configuration

**Code:** `src/maezo/platform/webhooks/whatsapp/idempotency.py`

Deduplication by `wamid` (WhatsApp message ID) with 24h TTL.

### In-memory store (dev/test)

```python
from maezo.platform.webhooks.whatsapp.idempotency import InMemoryIdempotencyStore

store = InMemoryIdempotencyStore()
```

No env vars needed. Suitable for local dev and unit tests.

### Redis store (production)

```python
from maezo.platform.webhooks.whatsapp.idempotency import RedisIdempotencyStore

store = RedisIdempotencyStore("redis://localhost:6379")
```

**Requires:** `pip install redis>=5.0`

**Env vars:**
- `WHATSAPP_REDIS_URL` — Redis connection (optional, defaults to localhost)

**TTL:** 24 hours (configurable per call). Messages re-delivered after 24h are reprocessed (acceptable).

---

## 4. Kafka topics produced

**Code:** `src/maezo/platform/webhooks/whatsapp/app.py` (lines 37–38)

| Topic | Event type | Contents |
|-------|-----------|----------|
| `agents.events.whatsapp.message-received` | `WhatsAppMessageEvent` | User message (text, media, interactive, location) |
| `agents.events.whatsapp.status` | `WhatsAppStatusEvent` | Delivery status (sent, delivered, read, failed) |

**Message schema:**

```json
{
  "wamid": "wamid.XXXX",
  "tenant": "amh",
  "phone_number_hash": "hk1_<64hex> (keyed HMAC-SHA256, ADR-0036)",
  "message_type": "text | audio | image | document | interactive | location",
  "message_body": "...",
  "timestamp_ms": 1718181600000,
  "whatsapp_business_account_id": "...",
  "phone_number_id": "..."
}
```

**Status schema:**

```json
{
  "wamid": "wamid.XXXX",
  "tenant": "amh",
  "status": "sent | delivered | read | failed",
  "timestamp_ms": 1718181600000,
  "phone_number_hash": "hk1_<64hex> (keyed HMAC-SHA256, ADR-0036)",
  "error_code": null,
  "error_title": null
}
```

**Producer config:** `acks="all"`, idempotent, Kafka 3.x+

---

## 5. Failure modes

### Kafka producer unavailable

**Symptom:** Pod logs show `whatsapp_kafka_publish_failed`

**Impact:** Event lost; no retry (fire-and-forget by design)

**Recovery:**
1. Verify Kafka health — production Kafka is **MSK Serverless** (no in-cluster
   broker pods/namespace): check the MSK cluster in the AWS console / CloudWatch.
   Local dev: `docker compose logs kafka`.
2. Check network connectivity from webhook pod to Kafka bootstrap servers
3. Restart webhook pod (the Deployment is named `webhook-receiver`): `kubectl rollout restart deploy/webhook-receiver -n maezo-amh`

### Invalid HMAC signature

**Symptom:** `whatsapp_webhook_invalid_signature` in logs; request returns 401

**Cause:** App secret mismatch or payload tampering

**Fix:**
1. Verify `WHATSAPP_APP_SECRET` in Secrets Manager matches Meta app panel
2. Check for proxy/load balancer modifying request body (unlikely but possible)

### Duplicate message processing

**Symptom:** Same conversation appears twice in logs despite idempotency

**Cause:** Idempotency key collision (very rare) or store TTL expiry (24h reprocessing is expected)

**Check:** Compare `wamid` and `timestamp_ms` — if identical, idempotent reprocessing; if different wamid, new message.

---

## 6. Local testing

### Using curl

```bash
# Set vars
APP_SECRET="YOUR_DEV_SECRET"
VERIFY_TOKEN="YOUR_DEV_TOKEN"
PAYLOAD='{"entry":[{"changes":[{"value":{"messages":[{"id":"wamid.test123","from":"5511999999999","timestamp":1718181600,"text":{"body":"Oi"}}],"metadata":{"display_phone_number":"5511999999999","phone_number_id":"123456789"}}]}]}]}'

# Compute HMAC
SIGNATURE=$(echo -n "$PAYLOAD" | openssl dgst -sha256 -hmac "$APP_SECRET" -hex | cut -d' ' -f2)

# POST
curl -X POST http://localhost:8000/webhook \
  -H "X-Hub-Signature-256: sha256=$SIGNATURE" \
  -H "Content-Type: application/json" \
  -d "$PAYLOAD"
```

Expected response: `{"status": "ok"}`

### GET verification

```bash
CHALLENGE="test_challenge_value"
curl -G http://localhost:8000/webhook \
  --data-urlencode "hub.mode=subscribe" \
  --data-urlencode "hub.challenge=$CHALLENGE" \
  --data-urlencode "hub.verify_token=$VERIFY_TOKEN"
```

Expected response: `test_challenge_value`

---

## 7. Monitoring

**Metrics to track** (Prometheus — real emitted names, defined in
`src/maezo/platform/webhooks/whatsapp/app.py`; scrape job `maezo-webhook` in
`config/prometheus.yml`; there are no `whatsapp_*`-prefixed metrics):

| Metric | Query | Alert threshold |
|--------|-------|-----------------|
| Requests by outcome | `rate(maezo_webhook_requests_total[5m])` — labels `tenant`, `status` (`ok`\|`invalid_signature`\|`parse_error`\|`error`) | N/A (baseline) |
| Messages processed/s | `rate(maezo_webhook_messages_total[5m])` — labels `tenant`, `message_type`, `deduplicated` | N/A (baseline) |
| Invalid signatures | `rate(maezo_webhook_requests_total{status="invalid_signature"}[5m])` | > 0.1/s (attack) |
| Kafka publish latency | `histogram_quantile(0.95, rate(maezo_webhook_kafka_publish_duration_seconds_bucket[5m]))` — **defined in code but not yet observed anywhere; expect no data until emission is wired** | — |

**Kafka publish failures have no metric today** — the only signal is the
structured log event `whatsapp_kafka_publish_failed` (fire-and-forget `_publish`
logs and swallows the error). Watch it via log aggregation, not PromQL.

**Dashboard:** none yet — `deploy/observability/dashboards/gateway.json` has **no**
WhatsApp-ingestion section; the webhook metrics are not on any checked-in dashboard.

**Alerts:** none defined — `deploy/observability/alert-rules.yaml` contains **no**
WhatsApp-specific alerts (`WhatsAppInvalidSignatureSpike` /
`WhatsAppKafkaPublishFailure` do not exist). Until rules are added, use the
invalid-signature query above and the `whatsapp_kafka_publish_failed` log event
as the manual watch points.

**Logs:** Structured JSON to stdout; aggregate in ELK/CloudWatch.
