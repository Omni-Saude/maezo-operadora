"""Business-key scrubbing for logs, mirrored payloads and Kafka message keys (DL-0043 leg (c)).

Installed ONLY when `phi_key_policy().scrubbing_enabled` — i.e. a human ratified `scrub_only` or
`pseudo_keys` in `spec/policies/privacy/phi-business-key-remediation.yaml`. Under the shipped
DRAFT/`off` manifest nothing here runs.

WHY A SECOND SCRUBBER AND NOT `PHI_FIELDS`. `gateway.pseudonymizer.PHI_FIELDS` is
`{cpf, nome, telefone, email}` and is documented as an immutable, CI-enforced frozenset. The
values this module has to reach are NOT those: they are business KEYS (`CANCEL-{tenant}-{id}`)
and the `matricula_beneficiario` anchor inside them. So this composes with `LogScrubber` rather
than mutating its canonical set — `BusinessKeyScrubber` runs the `LogScrubber` first (the
canonical PHI fields keep their exact current treatment) and then handles the key-bearing fields.
The HMAC itself is NOT re-implemented: it goes through the same keyed `Pseudonymizer` the gateway
and the webhook dispatcher use, via the single-value idiom `webhooks/whatsapp/security.py`
already established.

WHAT A SCRUBBED KEY LOOKS LIKE. `CANCEL-amh-mat-99` -> `CANCEL-hk1_<64 hex>`.

The FAMILY prefix is deliberately preserved in the clear: it is a decision-definition class token
(exactly what `_start_audit_record` already keeps in the clear as `process_key`), it carries no
subject identity, and without it a log line loses the one thing an operator needs to know which
process a message concerns. Everything after it — tenant AND identity — is replaced by one keyed
HMAC over the WHOLE original value. Hashing the whole value rather than parsing out a
`{family}-{tenant}-{identity}` split is deliberate: tenant ids and contract numbers may both
contain `-`, so any split is a guess, and a wrong guess leaks the segment it failed to cover.
`tenant_id` is already an explicit, separate field on essentially every key-bearing log call in
this repo, so nothing operational is lost.

Determinism matters: the same logical key always yields the same pseudonym, so cross-line
correlation in a log aggregator and per-key ordering in Kafka both survive.
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

from maezo.gateway.log_scrubber import LogScrubber
from maezo.gateway.pseudonymizer import KEYED_PSEUDONYM_PREFIX, Pseudonymizer

#: The one non-production `runtime_mode` token (mirrors `webhooks/service._LOCAL_RUNTIME_MODE`).
#: An UNSET mode counts as production, deliberately: a pod that never declared its mode must not
#: silently pseudonymize with the publicly-known dev key.
_LOCAL_RUNTIME_MODE = "local"

#: Log/payload field NAMES whose value is a business key. Swept from every `logger.*` call in
#: `src/` that carries a key (see `tests/unit/platform/privacy/test_key_scrubber.py`, which pins
#: this set against a re-sweep so a new key-bearing field name cannot be added silently).
BUSINESS_KEY_LOG_FIELDS: frozenset[str] = frozenset(
    {
        "business_key",
        "cancel_business_key",
        "_business_key",
        "inadimplencia_business_key",
        "cred_business_key",
        "fraude_business_key",
        "recurso_business_key",
        "escalation_business_key",
        "engine_business_key",
    }
)

#: Field NAMES that carry the raw PHI anchor a key is derived FROM. `matricula_beneficiario` is
#: the `PHI_PROCESS_VARS` name; `matricula` is the short form `inadimplencia.notify_beneficiario`
#: logs today. Both are replaced whole (no family prefix — these are not keys).
PHI_KEY_ANCHOR_LOG_FIELDS: frozenset[str] = frozenset({"matricula_beneficiario", "matricula"})

#: Business-key FAMILIES whose Kafka MESSAGE KEY is pseudonymized under `scrub_only`. Only the
#: two families that can carry `matricula_beneficiario` — every other family is anchored on a
#: non-PHI identifier (see the manifest's `escopo.nao_afetadas`), and re-keying them would change
#: their partitioning for no privacy gain.
PSEUDONYMIZED_MESSAGE_KEY_FAMILIES: frozenset[str] = frozenset({"CANCEL", "INAD"})

#: Every family prefix this module recognizes when preserving a class token in the clear. A value
#: whose leading token is not here is pseudonymized WHOLE (fail-closed: an unknown shape is not
#: assumed to be safe to partially reveal).
_KNOWN_KEY_FAMILIES: frozenset[str] = frozenset(
    # "AUTH" entrou em 19/08/2026 junto com a rota de ingresso do Rafael
    # (`runtime/agent_runtime/ingress.py`). Sem ela, `scrub_key_value("AUTH-amh-123")` devolvia
    # um `hk1_<hmac>` PELADO — que passa no validador de thread PHI-safe, mas perde o marcador
    # de familia e deixa a linha de checkpoint anonima quanto ao processo a que pertence.
    # Efeito colateral, e e' pequeno: chave AUTH em log scrubbed passa de `hk1_...` para
    # `AUTH-hk1_...`. `scrub_key_value` nao e' usado em nenhum outro lugar do repo.
    {"CANCEL", "INAD", "RECURSO", "CRED", "FRAUDE", "PROG", "ESC", "DSR", "ANS", "NIP", "AUTH"}
)


@lru_cache(maxsize=1)
def egress_pseudonymizer() -> Pseudonymizer:
    """The keyed pseudonymizer used by every policy-gated egress seam (logs, Kafka message keys).

    Built from the environment because the seams that need it (`structlog.configure`,
    `events.publish`'s producer call) are not composition roots and have no settings object in
    hand. `Pseudonymizer.from_settings` still decides the key, so ADR-0035 is INHERITED: a
    production runtime with no `PHI_HMAC_KEY` RAISES `PseudonymizerKeyMissingError` rather than
    returning a dev-keyed instance.

    Only ever called when `phi_key_policy().scrubbing_enabled` — under the shipped `off` policy
    this function never runs, so an unprovisioned key cannot break a daemon today.

    WHERE THAT RAISE ACTUALLY SURFACES (re-derived from the call graph, not assumed). This
    function has exactly two callers in `src/`:

      * `egress_message_key` below — reached from the `operadora.events.publish` handler
        (`tools/workers/events.py::make_publish_event_handler`). This is the ONLY
        production-reachable one today, so under a ratified `scrub_only` with no provisioned key
        the failure appears on the FIRST publish external task after the restart. That handler
        deliberately calls `egress_message_key` OUTSIDE its publish-`try`, so the raise propagates
        RAW to the harness retry/incident ladder instead of being caught and re-labelled
        `event_publish_failed` / `WorkerBpmnError(ERR_EVENT_PUBLISH_FAILED)`.
      * `observability._build_key_scrubber` — reachable ONLY from `setup_observability`, which has
        NO production caller today (repo-wide sweep: only tests call it). The "operator finds out
        at BOOT" reading of ADR-0035 is therefore NOT what happens in production; it becomes true
        only once a composition root wires that bootstrap.

    Blast radius is the whole `operadora.events.publish` topic set, not just CANCEL/INAD:
    `egress_message_key` constructs this pseudonymizer as an ARGUMENT, before
    `pseudonymize_message_key` gets to decide the family is out of scope. That is fail-closed and
    deliberate — an unprovisioned key under an active policy must stop the egress, not
    half-cover it — but it means the first affected task need not be a CANCEL/INAD one.
    """
    runtime_mode = os.environ.get("RUNTIME_MODE") or os.environ.get("AGENT_RUNTIME_MODE") or ""
    return Pseudonymizer.from_settings(
        phi_hmac_key=os.environ.get("PHI_HMAC_KEY"),
        production=runtime_mode.strip().lower() != _LOCAL_RUNTIME_MODE,
        tenant_id=os.environ.get("TENANT_ID", "amh"),
    )


def reset_egress_pseudonymizer_cache() -> None:
    """Clear the `egress_pseudonymizer` cache. Tests only."""
    egress_pseudonymizer.cache_clear()


def egress_message_key(key: Any) -> Any:
    """Policy-gated Kafka MESSAGE KEY for `operadora.events.publish`.

    Returns `key` UNCHANGED under the shipped (`off`) policy — byte-identical partitioning. Once
    `scrub_only` is ratified, CANCEL/INAD keys become `{FAMILY}-hk1_<hmac>` and every other
    family is still returned untouched. See `pseudonymize_message_key` for the partitioning note.
    """
    from maezo.platform.privacy.phi_key_policy import phi_key_policy  # noqa: PLC0415 — lazy

    if not phi_key_policy().scrubbing_enabled:
        return key
    return pseudonymize_message_key(key, egress_pseudonymizer())


def _digest(pseudonymizer: Pseudonymizer, value: str) -> str:
    """Keyed HMAC-SHA256 hex digest of one value, marked `hk1_`.

    Uses the established single-value idiom (`platform/webhooks/whatsapp/security.py`) rather
    than reaching into the Pseudonymizer's private key, so there is exactly one HMAC
    implementation in the repo and the ADR-0035 fail-closed key policy is INHERITED, not
    re-implemented.
    """
    digest = pseudonymizer.pseudonymize({"cpf": value})["cpf"]
    return f"{KEYED_PSEUDONYM_PREFIX}{digest}"


def scrub_key_value(value: Any, pseudonymizer: Pseudonymizer) -> Any:
    """Replace a business-key value with `{FAMILY}-hk1_<hmac>` (or a bare `hk1_<hmac>`).

    Non-string and empty values pass through unchanged (nothing to scrub — mirrors
    `Pseudonymizer.pseudonymize`'s own empty-value posture, and keeps `None` a `None` so a
    downstream `key=... or None` branch is unaffected).
    """
    if not isinstance(value, str) or not value:
        return value
    family, sep, _rest = value.partition("-")
    digest = _digest(pseudonymizer, value)
    if sep and family in _KNOWN_KEY_FAMILIES:
        return f"{family}-{digest}"
    return digest


def pseudonymize_message_key(key: Any, pseudonymizer: Pseudonymizer) -> Any:
    """Pseudonymize a Kafka MESSAGE KEY, but only for `PSEUDONYMIZED_MESSAGE_KEY_FAMILIES`.

    PARTITIONING: for CANCEL/INAD this changes which partition a message lands on. Ordering per
    logical key is preserved (the HMAC is deterministic), but messages published across the flag
    flip can straddle partitions — the manifest documents the drain window this requires. Every
    other family is returned UNCHANGED, so no other topic's partitioning moves.
    """
    if not isinstance(key, str) or not key:
        return key
    family, sep, _rest = key.partition("-")
    if not sep or family not in PSEUDONYMIZED_MESSAGE_KEY_FAMILIES:
        return key
    return f"{family}-{_digest(pseudonymizer, key)}"


class BusinessKeyScrubber:
    """structlog processor: `LogScrubber` PLUS business-key / matricula field scrubbing.

    Composition, not replacement: the wrapped `LogScrubber` handles `PHI_FIELDS`
    (`cpf`/`nome`/`telefone`/`email`) exactly as it does everywhere else, and this class adds the
    key-bearing field names on top. Installing this processor is therefore a strict superset of
    installing `LogScrubber` alone — which is what `platform/observability.py` wires when the
    remediation policy is active (and nothing at all when it is not).
    """

    def __init__(self, pseudonymizer: Pseudonymizer) -> None:
        self._pseudonymizer = pseudonymizer
        self._log_scrubber = LogScrubber(pseudonymizer)

    def __call__(self, logger: Any, method_name: str, event_dict: dict[str, Any]) -> dict[str, Any]:
        """Scrub `event_dict` in place-of-copy: PHI fields, then key/anchor fields."""
        scrubbed = self._log_scrubber(logger, method_name, event_dict)
        for field, value in list(scrubbed.items()):
            if field in BUSINESS_KEY_LOG_FIELDS:
                scrubbed[field] = scrub_key_value(value, self._pseudonymizer)
            elif field in PHI_KEY_ANCHOR_LOG_FIELDS and isinstance(value, str) and value:
                scrubbed[field] = _digest(self._pseudonymizer, value)
        return scrubbed
