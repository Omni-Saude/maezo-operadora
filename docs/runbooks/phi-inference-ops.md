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

**Code:** `src/maezo/runtime/inference/__init__.py` (module docstring, `_PROVIDER_FACTORIES`,
`_build_provider`), `src/maezo/runtime/inference/settings.py` (`InferenceSettings`)

`src/maezo/runtime/inference/` is the **single import point** for any LLM SDK in this codebase
(ADR-0009) — every other module MUST go through `InferenceProvider`, never import an LLM SDK
directly. Five provider values, selected via `MAEZO_INFERENCE_PROVIDER`
(`InferenceSettings(env_prefix="MAEZO_INFERENCE_")`):

| Value | Zone | Behavior |
|---|---|---|
| `noop` (default) | — | Deterministic mock, no network calls, no credentials required. |
| `anthropic` | Geral | Real calls via the official `anthropic` SDK. Requires an API key (§4). |
| `bedrock` | Geral | Real calls to the same model family through **AWS Bedrock** (`anthropic[bedrock]` extra, classic `bedrock-runtime` client `AsyncAnthropicBedrock`). **No API key**: SigV4 via the standard AWS credential chain (env / `AWS_PROFILE` / IRSA), resolved at request time — so there is no construction-time credential gate. Model id is a **`global.*` inference profile** (`MAEZO_BEDROCK_MODEL_ID`, default `global.anthropic.claude-opus-5`); region is `MAEZO_BEDROCK_REGION` (default `sa-east-1`). |
| `phi_zone_mock` | PHI | Explicitly-labeled mock standing in for the not-yet-provisioned BR-resident, zero-retention PHI-zone endpoint (ADR-0006/ADR-0017). Every response is marked synthetic. |
| `br_resident` | PHI | The real BR-resident, zero-retention adapter. **Built inert**: refuses to construct without `MAEZO_PHI_VENDOR_DPA_REF`, `MAEZO_PHI_API_KEY` and an allowlisted `MAEZO_PHI_ENDPOINT_URL`, and has no transport wired. |

> `bedrock` is **general zone only** (`phi_capable=False`, `deployment_region=global-multi-region`).
> `MAEZO_BEDROCK_REGION=sa-east-1` is an operational default, **not** a residency guarantee — the
> provider enforces nothing about the region and a deployment can point it anywhere. PHI residency
> is the `br_resident` leg, which enforces an endpoint allowlist and a per-response `served_region`
> attestation. A `phi=True` request against `bedrock` raises `PhiZoneRoutingError`.
>
> ⚠️ **LGPD — transferência internacional (aberto, decisão de dono).** O id sancionado é um
> **inference profile `global.*`**, que por construção roteia a inferência **cross-region**. A
> região configurada é onde a request é *assinada*, não necessariamente onde a inferência
> *executa*. Está sancionado para a **zona geral** (dado pseudonimizado, ADR-0006) e é o motivo
> factual — não apenas prudencial — de `deployment_region` ser `global-multi-region`. Antes de
> produção: avaliar base legal de transferência internacional, ou trocar por um inference profile
> regional caso a conta passe a oferecer um em `sa-east-1`.

### Por que o client clássico `bedrock-runtime`, e não o Mantle

O client recomendado para Bedrock é normalmente `AnthropicBedrockMantle` (endpoint Messages-API).
Este repo **não** o usa, e a razão é medida, não preferência — probe ao vivo com a sessão real
(perfil `amh-data-dev`, `sa-east-1`, 2026-08-12):

| Tentativa | Resultado |
|---|---|
| Mantle + `anthropic.claude-opus-5` | **404** "The model … does not exist" (request chegou ao endpoint, SigV4 ok, request_id `req_wg2absqs…`) |
| Mantle + `global.anthropic.claude-opus-5` | **404**, idem |
| `bedrock-runtime converse --model-id global.anthropic.claude-opus-5` | **SUCESSO** (`end_turn`, 179 output tokens, resposta real do Opus-5) |

Ou seja: a conta serve Claude por **inference profiles `global.*` no `bedrock-runtime` clássico**,
e o Mantle não está habilitado para ela. Se o Mantle for habilitado no futuro, a troca é uma linha
em `BedrockInferenceProvider.__init__` mais o pin de client em
`tests/unit/runtime/test_inference_bedrock.py::test_uses_the_classic_bedrock_runtime_client_not_the_mantle_one`
— que existe exatamente para impedir que alguém "corrija" o client de volta sem essa evidência.

```bash
# Inspect current selection (no secrets printed):
echo "${MAEZO_INFERENCE_PROVIDER:-noop (default)}"
```

## 2. Fail-closed posture

**Code:** `src/maezo/runtime/inference/errors.py` (`InferenceConfigError`),
`src/maezo/runtime/inference/__init__.py` (`_build_provider`)

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
`bedrock`, `phi_zone_mock`, `br_resident`) and raises `InferenceConfigError` listing the valid set
if it isn't found. The `anthropic` provider additionally raises `InferenceConfigError` at
**construction time** (not on first use) if no API key is found in the environment (§4) — a
missing key is caught at process startup, before any request can silently no-op or fall back.

`bedrock` deliberately has **no equivalent credential gate**, and the difference is auditable
rather than implied: it declares `credential_source=aws-default-chain` (not `environment`) because
botocore resolves the credential per request, so there is nothing for the constructor to check. A
missing or unauthorized AWS credential therefore surfaces on the **first** `generate()` as an
`InferenceProviderError` (`retryable=False`, message naming the botocore exception type only) —
loudly, never as a silent degrade. Its `health_check()` reports `ok` but states explicitly that no
credential was verified; it fails closed at construction only on an explicitly-blank
`MAEZO_BEDROCK_REGION`.

Runtime failures from a configured, credentialed provider (timeouts, rate limits, auth failures,
refusals) surface as `InferenceProviderError` (`provider`, `message`, `retryable` fields) — a
provider-agnostic type so callers never need to import or catch an SDK-specific exception class.

## 3. PHI-zone routing

**Code:** `src/maezo/runtime/inference/errors.py` (`PhiZoneRoutingError`),
`src/maezo/runtime/inference/__init__.py` (`InferenceProvider.generate`)

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

**Code:** `src/maezo/runtime/inference/settings.py` (`_ANTHROPIC_API_KEY_ENV_VARS`),
`src/maezo/runtime/inference/providers.py` (`AnthropicInferenceProvider._resolve_api_key`)

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

**Code:** `src/maezo/runtime/inference/__init__.py` (module docstring),
`src/maezo/runtime/inference/providers.py` (`_emit_llm_token_usage`)

Every real response reaching `AnthropicInferenceProvider.generate` passes through
`_emit_llm_token_usage`, which reads the raw Anthropic SDK's `response.usage`
(`input_tokens`/`output_tokens`) and emits it through the platform's existing structlog +
Prometheus mechanisms (`maezo.platform.observability`) — never a parallel logging/telemetry
system. Emission is **PHI-safe** (model id + token counts only, never prompt/response content)
and **fail-safe** (a metering defect degrades to a skipped emission, never an exception that
could break or stall the LLM call). Mock providers (`NoopInferenceProvider`,
`PhiZoneMockProvider`) never emit metering — they make no real API call, so there is nothing to
meter.
