"""Per-tenant signing-key resolution seam (ADR-0039 §4.4, owner decision 4).

This is leg E2 of the envelope-signing train: it builds ONE per-tenant keyset seam that BOTH
Card signing (built today) and envelope signing (leg E3, next) resolve their signing key through.
It does NOT implement envelope signing itself — it only creates the single per-tenant resolution
point both surfaces will share (`TenantKeyset.key_for`).

WHY PER-TENANT, NOT REPO-WIDE (owner decision 4). Today's Card-signing key
(`assembly.CARD_SIGNING_KEY_ENV_VAR = "MAEZO_A2A_CARD_SIGNING_KEY"`, `assembly.py:45`) is a single
repo-wide scalar shared across every tenant. The owner ruled that envelope signing — and, going
forward, the key custody this seam owns — is scoped PER TENANT (ADR-0039 §4.4, `## Decisoes do
dono` table row 4): each tenant resolves its OWN key, never a single repo-wide key shared across
tenants. The blast radius is why: a leaked repo-wide HMAC key would let an attacker forge across
EVERY tenant at once, a strictly worse outcome than a leaked single-tenant key. This mirrors the
tenant-scoping `A2ARegistry` already enforces (ADR-0004, `registry.py:8-10`).

THE VAULT/KMS SEAM IS UNCHANGED — ONLY THE NAMESPACING IS NEW. Key custody stays the identical
vault/KMS delivery seam that already provisions `MAEZO_A2A_CARD_SIGNING_KEY`: a deployment's
secret-injector (or a local `.env` for dev) places the key material in the process environment;
this module only READS what an injector placed there. Real key PROVISIONING in the vault/KMS
remains an EXTERNAL/owner dependency (`docs/design/A2A-dispatcher-card-signing.md` §6.2) — this
module never wires a real key, and the env-backed impl below reads nothing until an injector has
populated the per-tenant variable.

PER-TENANT ENV CONVENTION (`per_tenant_key_env_var`). Each tenant's key lives in a NAMESPACED
sibling of the repo-wide var: `MAEZO_A2A_CARD_SIGNING_KEY__<TENANT>`, where `<TENANT>` is the
tenant id upper-cased, joined by a DOUBLE underscore (`_ENV_TENANT_SEPARATOR`). Examples:
`MAEZO_A2A_CARD_SIGNING_KEY__AMH` for tenant `"amh"`. The double-underscore delimiter guarantees a
per-tenant variable can NEVER collide with the bare repo-wide `MAEZO_A2A_CARD_SIGNING_KEY` (which
carries no `__` suffix), and the tenant id is validated against the platform's tenant convention
(`^[a-z][a-z0-9_]*$`, the SAME `_SAFE_TENANT_ID` regex `gateway.audit_postgres.schema_for_tenant`
uses at `audit_postgres.py:74` for anti-injection). Because that convention is lowercase
`[a-z0-9_]` only, upper-casing it is INJECTIVE — two distinct tenants can never map to the same
variable name (there is no hyphen→underscore folding that could alias `"a-b"` onto `"a_b"`). A
tenant id outside that convention is REFUSED fail-closed (`ValueError`), never silently coerced
into a possibly-colliding variable name — an ambiguous tenant→variable mapping is itself a
cross-tenant-confusion vector, so it must raise rather than guess.

NO SILENT CROSS-TENANT FALLBACK (the attack decision 4 exists to prevent). `key_for(tenant)`
reads ONLY that tenant's namespaced variable. A missing tenant key returns `None` — it NEVER falls
back to the bare repo-wide `MAEZO_A2A_CARD_SIGNING_KEY`, nor to any other tenant's key. Falling
back to a shared/other key is precisely the cross-tenant key-confusion attack: it would let a
tenant whose key was never provisioned sign/verify under someone else's key. The fail-closed
consequence of a `None` return is enforced one layer up, at the composition root
(`runtime.agent_runtime.a2a_composition._require_signer_or_fail_closed`): production runtime mode
with a missing tenant key REFUSES to compose (`RuntimeError`); non-production preserves the
existing explicit-opt-out dev path.

MIGRATION PATH — FAIL-CLOSED, NO SILENT SHIM (ADR-0039 §4.4). No envelope-signing key exists today
(envelope signing is not built), so there is no live per-request key deployment to migrate. For
Card signing, the repo-wide `MAEZO_A2A_CARD_SIGNING_KEY` is the pre-existing precedent — and it is
exactly the anti-pattern decision 4 rules out. The migration is therefore the fail-closed one the
ADR mandates: **deployments provision per-tenant keys (`MAEZO_A2A_CARD_SIGNING_KEY__<TENANT>`);
until they do, production refuses to compose the edge for that tenant** (the composition root
raises, mirroring `_require_signer_or_fail_closed`'s existing posture). This module deliberately
adds NO compatibility shim: it does NOT read the bare repo-wide `MAEZO_A2A_CARD_SIGNING_KEY` as a
silent per-tenant default, because reusing it that way is the untimed silent shim the ADR forbids
(§4.4: any shim must be named distinctly, loudly logged, and explicitly time-boxed). If a rollout
genuinely needs a transitional shared key, that shim is a SEPARATE, explicit, time-boxed change a
human must author and record in the ADR — not something this seam grants by default.

THE SEAM FOR LEG E3. `key_for(tenant)` is the single per-tenant resolution point both signing
surfaces share. Card signing wires it TODAY (through `_require_signer_or_fail_closed`, which builds
a `CardSigner` from the resolved bytes via `assembly.card_signer_from_key`). Envelope signing (leg
E3) will resolve the SAME per-tenant key through the SAME `key_for(tenant)` to sign envelopes — so
a tenant's Card signature and its envelope signature are produced under one custody surface, never
two divergent key sources.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from typing import Protocol, runtime_checkable

from maezo.a2a.assembly import CARD_SIGNING_KEY_ENV_VAR

#: Delimiter between the fixed repo-namespaced prefix and the per-tenant suffix. A DOUBLE
#: underscore (the pydantic-nested-env convention) so a per-tenant variable can never be confused
#: with the bare repo-wide `MAEZO_A2A_CARD_SIGNING_KEY`, which carries no `__` suffix.
_ENV_TENANT_SEPARATOR = "__"

#: The platform tenant convention, IDENTICAL to `gateway.audit_postgres._SAFE_TENANT_ID`
#: (`audit_postgres.py:74`, used by `schema_for_tenant` for anti-injection): lowercase, starting
#: with a letter, `[a-z0-9_]` thereafter. It is reproduced (not imported) to keep this seam free of
#: a gateway dependency; the citation is the contract. Upper-casing a string in this alphabet is
#: injective, so distinct tenants can never alias onto the same env var name.
_SAFE_TENANT_ID = re.compile(r"^[a-z][a-z0-9_]*$")


def per_tenant_key_env_var(tenant: str) -> str:
    """Return the env var name carrying `tenant`'s signing key: `MAEZO_A2A_CARD_SIGNING_KEY__<T>`.

    `<T>` is `tenant` upper-cased. Fail-closed: a `tenant` that is not a valid platform tenant id
    (`^[a-z][a-z0-9_]*$`) raises `ValueError` rather than being coerced into a possibly-colliding
    variable name — an ambiguous tenant→variable mapping is a cross-tenant-confusion vector, so it
    must refuse, never guess (mirroring `schema_for_tenant`'s fail-closed validation).
    """
    if not _SAFE_TENANT_ID.match(tenant):
        raise ValueError(
            f"tenant {tenant!r} is not a valid tenant id (expected [a-z][a-z0-9_]*, the platform "
            "convention shared with gateway.audit_postgres.schema_for_tenant) — refusing to derive "
            "a per-tenant signing-key env var name from it, to avoid a cross-tenant-confusion alias"
        )
    return f"{CARD_SIGNING_KEY_ENV_VAR}{_ENV_TENANT_SEPARATOR}{tenant.upper()}"


@runtime_checkable
class TenantKeyset(Protocol):
    """Resolves a PER-TENANT signing key from the vault/KMS injection seam (ADR-0039 §4.4).

    The single per-tenant resolution point BOTH Card signing (today) and envelope signing (leg E3)
    share. Implementations MUST NOT fall back across tenants: a missing key for `tenant` returns
    `None`, never another tenant's key nor a repo-wide default (that fallback IS the cross-tenant
    key-confusion attack owner-decision 4 rules out). The fail-closed consequence of `None` is the
    composition root's job (`_require_signer_or_fail_closed`), not this resolver's.
    """

    def key_for(self, tenant: str) -> bytes | None:
        """The `tenant`'s signing key bytes, or `None` if none is provisioned for THAT tenant."""
        ...


class EnvTenantKeyset:
    """Environment-backed `TenantKeyset` — the real per-tenant half of the vault/KMS seam.

    Reads the per-tenant variable `MAEZO_A2A_CARD_SIGNING_KEY__<TENANT>` (`per_tenant_key_env_var`)
    that a deployment's secret-injector (or a dev `.env`) is expected to populate. It does not
    itself talk to a vault/KMS; real key PROVISIONING there is external/blocked (§6.2). Mirrors the
    exact posture of `assembly.card_signing_key_from_env` for each tenant:

      * value whitespace-STRIPPED (a trailing newline from a mounted secret file is a transport
        artifact, not key material) and UTF-8 encoded;
      * absent OR whitespace-only -> `None` (dev fail-safe: a blank-but-present variable can never
        construct a signer, same as absent);
      * present-but-too-short is NOT filtered here — it flows to `CardSigner` (via
        `card_signer_from_key`), which refuses it fail-closed (`CardSignatureError`,
        `MIN_SIGNING_KEY_BYTES`), so a present-but-garbage key fails loudly rather than silently
        downgrading to unsigned;
      * NO cross-tenant fallback: only THIS tenant's namespaced variable is read, never the bare
        repo-wide `MAEZO_A2A_CARD_SIGNING_KEY` nor another tenant's variable.
    """

    def __init__(self, environ: Mapping[str, str] | None = None) -> None:
        # Default to the live `os.environ` (the same mutable object monkeypatch/secret-injectors
        # mutate), so a key injected after construction is still seen. An explicit mapping is
        # accepted for hermetic unit tests.
        self._environ: Mapping[str, str] = os.environ if environ is None else environ

    def key_for(self, tenant: str) -> bytes | None:
        value = self._environ.get(per_tenant_key_env_var(tenant), "").strip()
        return value.encode("utf-8") if value else None
