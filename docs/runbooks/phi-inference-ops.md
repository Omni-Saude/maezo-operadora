# Runbook: PHI Inference Ops — provider selection, fail-closed routing, credentials

**Audience:** Platform, security, PHI operations, on-call
**Last updated:** 2026-08-11
**Applies to:** Maezo Healthcare Plan, all environments

> **Standing constraint — read before touching `MAEZO_INFERENCE_PROVIDER` in any real
> environment.** `PLANS.md` (Onda 2 — Inferência PHI real): **"NÃO ligar LLM mais capaz em
> produção antes da Onda 1 enforçar + canários da Onda 2 verdes"** — do not switch production
> to a stronger/real LLM before the Wave-1 effect-control-plane enforcement is live AND Wave-2's
> synthetic canaries are green. As of this writing, Wave-1 is inert (build-only, per
> `maezo-hardening-program` status) and Wave-2 has not landed. This is a program-level human
> gate, not something this runbook can override.

---

## Table of Contents

1. [Provider selection — `MAEZO_INFERENCE_PROVIDER`](#1-provider-selection--maezo_inference_provider)
2. [Fail-closed posture](#2-fail-closed-posture)
3. [PHI-zone routing](#3-phi-zone-routing)
4. [Credential handling](#4-credential-handling)
5. [Token-usage metering](#5-token-usage-metering)

---

## 1. Provider selection — `MAEZO_INFERENCE_PROVIDER`

**Code:** `src/maezo/runtime/inference.py` (module docstring, `InferenceSettings`,
`_PROVIDER_FACTORIES`, `_build_provider`)

`src/maezo/runtime/inference.py` is the **single import point** for any LLM SDK in this codebase
(ADR-0009) — every other module MUST go through `InferenceProvider`, never import an LLM SDK
directly. Three provider values, selected via `MAEZO_INFERENCE_PROVIDER`
(`InferenceSettings(env_prefix="MAEZO_INFERENCE_")`):

| Value | Behavior |
|---|---|
| `noop` (default) | Deterministic mock, no network calls, no credentials required. |
| `anthropic` | Real calls via the official `anthropic` SDK. Requires an API key (§4). |
| `phi_zone_mock` | Explicitly-labeled mock standing in for the not-yet-provisioned BR-resident, zero-retention PHI-zone endpoint (ADR-0006/ADR-0017). Every response is marked synthetic. |

```bash
# Inspect current selection (no secrets printed):
echo "${MAEZO_INFERENCE_PROVIDER:-noop (default)}"
```

## 2. Fail-closed posture

**Code:** `src/maezo/runtime/inference.py` (`InferenceConfigError`, `_build_provider`)

An unrecognized `MAEZO_INFERENCE_PROVIDER` value is a **fail-closed startup error** — the process
refuses to boot with a misconfigured provider rather than silently degrading to `noop`:

```python
class InferenceConfigError(ValueError):
    """Raised when the inference provider configuration is invalid.

    Fail-closed: an unknown provider name, or a real provider missing
    required credentials, MUST raise here rather than silently falling
    back to a mock/noop provider. Raised at construction time (startup),
    not on first use.
    """
```

`_build_provider` looks the configured name up in `_PROVIDER_FACTORIES` (`noop`, `anthropic`,
`phi_zone_mock`) and raises `InferenceConfigError` listing the valid set if it isn't found. The
`anthropic` provider additionally raises `InferenceConfigError` at **construction time** (not on
first use) if no API key is found in the environment (§4) — a missing key is caught at process
startup, before any request can silently no-op or fall back.

Runtime failures from a configured, credentialed provider (timeouts, rate limits, auth failures,
refusals) surface as `InferenceProviderError` (`provider`, `message`, `retryable` fields) — a
provider-agnostic type so callers never need to import or catch an SDK-specific exception class.

## 3. PHI-zone routing

**Code:** `src/maezo/runtime/inference.py` (`PhiZoneRoutingError`, `InferenceProvider.generate`)

`InferenceProvider.generate(..., phi: bool = False, ...)` accepts a `phi` flag for requests that
carry PHI-tagged content. Such a request may **only** be served by a provider explicitly marked
`phi_capable` — today, only `PhiZoneMockProvider` (no real BR-resident endpoint exists yet). A
PHI-tagged request against any other provider raises:

```python
class PhiZoneRoutingError(PermissionError):
    """Raised when a PHI-tagged inference request has no PHI-zone provider.

    Per ADR-0006 (duas zonas) / ADR-0017 (egress enforcement), PHI-tagged
    inference must NEVER silently fall back to the general-zone cloud
    provider.
    """
```

The `PermissionError` subclass mirrors the pattern used elsewhere in the codebase for structural,
fail-closed denials (e.g. ADR-0016's `ProcessKeyNotAllowedError`). **On `PhiZoneRoutingError`: the
caller must route to a human / incident — never retry against a different provider, and never
catch-and-continue against the general-zone cloud provider.** This is the invariant an on-call
engineer should verify held if a PHI-routing alert fires: check that the error propagated to a
human/incident path, not that it was silently swallowed to keep a request flowing.

## 4. Credential handling

**Code:** `src/maezo/runtime/inference.py` (`_ANTHROPIC_API_KEY_ENV_VARS`,
`AnthropicInferenceProvider._resolve_api_key`)

```python
#: Env vars consulted for the Anthropic API key, in precedence order.
#: MAEZO_ANTHROPIC_API_KEY (repo-namespaced, preferred) wins over the
#: SDK-conventional ANTHROPIC_API_KEY. Neither is ever read from
#: InferenceSettings / pydantic-settings — credentials are STRICTLY
#: environment-sourced and never committed.
_ANTHROPIC_API_KEY_ENV_VARS = ("MAEZO_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY")
```

- **Env var names:** `MAEZO_ANTHROPIC_API_KEY` (preferred, repo-namespaced) or
  `ANTHROPIC_API_KEY` (SDK-conventional fallback), checked in that order.
- **Never committed, never in settings objects.** The key is deliberately **not** a field on
  `InferenceSettings` — it is read directly from the process environment inside
  `AnthropicInferenceProvider`, specifically so it never round-trips through a settings object
  that might be logged, serialized, or defaulted. Grep any incident/log export for either env var
  name before sharing it — the intent is that neither ever appears in a settings dump.
  `InferenceConfigError` is raised at construction if neither env var is set (§2).
- **How the A2A signing-key seam mirrors this pattern:** `src/maezo/a2a/assembly.py`'s
  `card_signing_key_from_env()` explicitly cites this module's `_resolve_api_key` as the
  precedent it follows (repo-namespaced env var, never hard-coded, never logged) — see
  [`a2a-key-rotation.md`](a2a-key-rotation.md).
- **Real key provisioning is external/infra, not this repo's concern.** Setting the env var in a
  deployed environment (vault/KMS sidecar, secret-injector) is out of this module's scope; it
  only reads whatever the environment already has.

## 5. Token-usage metering

**Code:** `src/maezo/runtime/inference.py` (module docstring, `_emit_llm_token_usage`)

Every real response reaching `AnthropicInferenceProvider.generate` passes through
`_emit_llm_token_usage`, which reads the raw Anthropic SDK's `response.usage`
(`input_tokens`/`output_tokens`) and emits it through the platform's existing structlog +
Prometheus mechanisms (`maezo.platform.observability`) — never a parallel logging/telemetry
system. Emission is **PHI-safe** (model id + token counts only, never prompt/response content)
and **fail-safe** (a metering defect degrades to a skipped emission, never an exception that
could break or stall the LLM call). Mock providers (`NoopInferenceProvider`,
`PhiZoneMockProvider`) never emit metering — they make no real API call, so there is nothing to
meter.
