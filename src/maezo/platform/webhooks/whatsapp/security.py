"""WhatsApp Cloud API webhook security — HMAC signature validation + phone pseudonymization.

Real, self-contained cryptography (constraint 3 — not a fabricated boundary): `verify_hub_signature`
is pure/deterministic and needs no dependency. `hash_phone` is now KEYED (ADR-0035): it derives the
conversation/thread identity through the SAME vault-keyed `Pseudonymizer` the gateway uses, so the
`telefone`-derived identity that egresses (conversation_id → checkpoint thread_id → CIB Seven
business key → INFO logs) is irreversible without `PHI_HMAC_KEY`.
"""

from __future__ import annotations

import hashlib
import hmac
from typing import Final

from maezo.gateway.pseudonymizer import KEYED_PSEUDONYM_PREFIX, Pseudonymizer

_SIGNATURE_PREFIX = "sha256="

#: O que uma linha de log carrega NO LUGAR do `wamid` quando nao ha pseudonimizador alcancavel no
#: ponto do log (ver `log_safe_message_id`). Um marcador constante, NAO um hash "so para
#: depurar": qualquer digest sem chave do wamid seria reversivel pela mesma tabela precomputada
#: que `hash_message_id` existe para impedir.
MESSAGE_ID_LOG_OMITTED: Final[str] = "<wamid-sem-pseudonimo>"


def verify_hub_signature(payload: bytes, signature_header: str | None, app_secret: str) -> bool:
    """Validate Meta's `X-Hub-Signature-256: sha256=<hex_digest>` header.

    The digest is HMAC-SHA256 of the raw request body using the Meta app secret. Uses
    `hmac.compare_digest` (timing-safe) — never a plain `==` string comparison on a secret-derived
    value. Returns False (never raises) for a missing header, a malformed `sha256=` prefix, or a
    mismatched digest — the caller (app.py) is responsible for turning False into 401.
    """
    if not signature_header or not signature_header.startswith(_SIGNATURE_PREFIX):
        return False
    provided_hex = signature_header[len(_SIGNATURE_PREFIX) :]
    expected = hmac.new(app_secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(provided_hex, expected)


def hash_phone(phone: str, tenant: str, pseudonymizer: Pseudonymizer) -> str:
    """KEYED pseudonym of `"{tenant}:{phone}"`, tagged with the `hk1_` keyed-scheme marker.

    The raw phone number is NEVER logged, stored, or forwarded past this point (ADR-0006 General
    Zone). CRITICAL (ADR-0035 extension): the digest is produced by the injected KEYED HMAC-SHA256
    `Pseudonymizer` — NOT a bare `sha256` — because this value becomes the `conversation_id` /
    checkpoint `thread_id` / `ESC-{tenant}-...` CIB Seven business key that is durably PERSISTED
    (Postgres checkpoint tables) and Cockpit-visible. A bare sha256 of a phone is trivially
    reversible via a precomputed table over the ~6.7e9 BR-mobile keyspace; a keyed HMAC is not,
    without the vault-synced `PHI_HMAC_KEY`.

    Fail-closed is INHERITED, not re-implemented: the `pseudonymizer` is always built via
    `Pseudonymizer.from_settings` at the composition root (`webhooks/service.py`), which raises
    `PseudonymizerKeyMissingError` in a production `runtime_mode` when the key is absent/blank —
    so there is NO code path here that can emit an unkeyed identity in production. In dev/CI the
    injected pseudonymizer carries a non-secret deterministic key (loud warning at construction).

    The `tenant:` prefix inside the HMAC input keeps the pseudonym distinct per tenant even under a
    single shared vault key; the `hk1_` marker on the output lets `assert_phi_safe_thread_id`
    require a keyed form and refuse the legacy `wa:{tenant}:{bare-sha256}` identity.
    """
    digest = pseudonymizer.pseudonymize({"telefone": f"{tenant}:{phone}"})["telefone"]
    return f"{KEYED_PSEUDONYM_PREFIX}{digest}"


def hash_message_id(message_id: str, tenant: str, pseudonymizer: Pseudonymizer) -> str:
    """KEYED pseudonym of `"{tenant}:{wamid}"`, tagged with the `hk1_` keyed-scheme marker.

    Gap `WEBHOOK-WAMID-DEDUP`: the dedup registry (`platform/driver_idempotency.py`) PERSISTS its
    key in Postgres, and the key derives from Meta's `wamid`. A raw `wamid` is NOT an opaque
    token — it embeds the counterpart phone number as base64 inside its own payload
    (`wamid.HBgNNTUxMT...` decodes to bytes containing `5511...`; proven by
    `tests/unit/platform/webhooks/whatsapp/test_dedup_keys.py`). Storing it verbatim would put a
    beneficiary identifier in a durable table that the LGPD retention inventory classifies as
    `SEM_COLUNA_DE_TITULAR` — i.e. it would make that classification false.

    So the wamid takes EXACTLY the treatment `hash_phone` gives the phone number: the same
    vault-keyed HMAC-SHA256 `Pseudonymizer` (ADR-0035), irreversible without `PHI_HMAC_KEY`, with
    the same `tenant:` prefix inside the HMAC input (distinct pseudonyms per tenant under one
    shared key) and the same `hk1_` marker on the output. Determinism is what makes dedup work:
    the same wamid re-delivered by Meta must produce the same key.

    Fail-closed is INHERITED from the injected pseudonymizer, exactly as in `hash_phone`.
    """
    digest = pseudonymizer.pseudonymize({"telefone": f"{tenant}:{message_id}"})["telefone"]
    return f"{KEYED_PSEUDONYM_PREFIX}{digest}"


def log_safe_message_id(message_id: str, tenant: str, pseudonymizer: Pseudonymizer | None) -> str:
    """A UNICA forma de um `wamid` que pode aparecer num campo de log (gap `WEBHOOK-LOG-RAW-WAMID`).

    O `wamid` bruto NAO e um token opaco: ele embute o telefone da contraparte em base64 dentro do
    proprio payload (`hash_message_id` acima; provado por
    `tests/unit/platform/webhooks/whatsapp/test_dedup_keys.py::test_a_real_shaped_wamid_leaks_the_phone_number_in_base64`).
    `app.py` e `dispatch.py` logavam `message_id=<wamid bruto>` — o mesmo dado que a chave DURAVEL
    de dedup ja se recusa a persistir — o que jogava um identificador de beneficiario em stdout,
    no coletor de logs e na retencao desse coletor, fora de qualquer inventario LGPD.

    Um SO ponto de decisao, para que nenhum call site invente a sua propria renderizacao:
      - com pseudonimizador: exatamente o mesmo pseudonimo `hk1_` KEYED que a chave de dedup usa
        (`hash_message_id`), correlacionavel com as linhas `dedup_key=` da mesma requisicao;
      - sem pseudonimizador alcancavel (ou sem `wamid`): :data:`MESSAGE_ID_LOG_OMITTED`, um
        marcador constante. NUNCA um sha256 sem chave do wamid — o docstring de `hash_message_id`
        explica por que um digest sem chave sobre um espaco de chaves enumeravel nao e anonimizacao.

    Nao ha caminho de retorno que devolva o `wamid` bruto: o parametro so e lido dentro do ramo
    keyed, entao um pseudonimizador ausente degrada para o marcador em vez de vazar em silencio.
    """
    if pseudonymizer is None or not message_id:
        return MESSAGE_ID_LOG_OMITTED
    return hash_message_id(message_id, tenant, pseudonymizer)
