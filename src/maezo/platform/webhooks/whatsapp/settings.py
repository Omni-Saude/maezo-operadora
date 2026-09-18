"""Injectable configuration for the WhatsApp webhook receiver (T1.6, defect B1; T1.11 dispatch).

`app_secret` and `verify_token` are REQUIRED — no default (fail-closed, constraint 2). This
mirrors `deployment-webhook-receiver.yaml`'s own documented expectation ("Missing either raises
a pydantic ValidationError at startup -> CrashLoopBackOff" — `deployment-webhook-receiver.yaml:43-46`):
a webhook receiver that would silently accept an unverifiable signature (or hand out a
default-valued verify token) is a real security defect, not a convenience worth defaulting away.
Provisioning this secret (the ExternalSecret `maezo-whatsapp-config`) is an ops/deploy concern
outside T1.6's scope; local dev sets these via `docker-compose.yml` env directly.
"""

from __future__ import annotations

from typing import Any

from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class WhatsAppWebhookSettings(BaseSettings):
    """Config for the webhook-receiver daemon — matches `deployment-webhook-receiver.yaml`'s env
    (`TENANT_ID`, `WHATSAPP_TOKEN`, `WHATSAPP_APP_SECRET`, `WHATSAPP_VERIFY_TOKEN`,
    `KAFKA_BOOTSTRAP_SERVERS`, `deployment-webhook-receiver.yaml:36-64`)."""

    # No `populate_by_name`: with no `env_prefix` set, that flag makes pydantic-settings' env
    # source ALSO try each field's bare Python name as an env-var candidate (case-insensitively),
    # in addition to its alias. For most fields below that adds nothing new — their Python name
    # case-folds to exactly the same string as their canonical env name (`tenant_id` ->
    # `TENANT_ID`, `phi_hmac_key` -> `PHI_HMAC_KEY`, etc.), so a second `AliasChoices` entry with
    # the field name is safe and is used to keep by-field-name CONSTRUCTION working (every
    # `WhatsAppWebhookSettings(tenant_id=..., phi_hmac_key=..., ...)` call in this codebase).
    # `app_secret`/`verify_token` are the exception: their Python names do NOT case-fold to their
    # canonical `WHATSAPP_`-prefixed env name, so putting them in `AliasChoices` the same way would
    # silently open a bare `APP_SECRET`/`VERIFY_TOKEN` (no prefix) env surface that no deployment
    # ever sets — the residual gate finding this class is fixed for. Their by-field-name
    # construction is restored instead by the `_map_bare_field_name_kwargs` validator below, which
    # only ever sees `"app_secret"`/`"verify_token"` as a key when a caller passed exactly that
    # kwarg — pydantic-settings' env source is not wired to produce those keys at all now, so a
    # bare env var can never reach it.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    tenant_id: str = Field(default="amh", validation_alias=AliasChoices("TENANT_ID", "tenant_id"))

    # PHI pseudonymizer HMAC key (ADR-0006/ADR-0035) — the vault-synced secret Helena's dispatch
    # keys her CPF/telefone/nome/email pseudonyms with (`gateway/pseudonymizer.py`). This is the
    # daemon that actually constructs+calls the Pseudonymizer (service.py `_build_dispatcher` ->
    # dispatch.py), so the key MUST be injected here (ExternalSecret `phi-hmac-key`). Absent in a
    # production `runtime_mode` -> `Pseudonymizer.from_settings` fails closed (never a reversible
    # unkeyed pseudonym). Empty in dev/CI -> deterministic per-tenant DEV key (non-secret).
    # `repr=False, exclude=True`: this is the most sensitive value in the class (it is what makes
    # every beneficiary pseudonym irreversible) — never rendered by `repr`/`str`, and excluded from
    # `model_dump`/`model_dump_json` too (`repr=False` alone leaves those two leaking verbatim).
    phi_hmac_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("PHI_HMAC_KEY", "phi_hmac_key"),
        repr=False,
        exclude=True,
    )

    # Meta app secret (HMAC-SHA256 signature validation, POST /webhook) — REQUIRED, no default.
    # `min_length=1`: an EMPTY secret is not a configured secret. Without it, `WHATSAPP_APP_SECRET=""`
    # passed validation and keyed `verify_hub_signature`'s HMAC with b"" — a signature anyone can
    # forge, reached through the same "silently accept an unverifiable signature" path this module's
    # docstring calls a real security defect. Fail at boot (CrashLoopBackOff), never at the edge.
    # `AliasChoices` carries ONLY the canonical name (see the class-level comment above for why the
    # field name is deliberately absent) — never rendered (`repr=False, exclude=True`).
    app_secret: str = Field(
        validation_alias=AliasChoices("WHATSAPP_APP_SECRET"),
        min_length=1,
        repr=False,
        exclude=True,
    )
    # Meta verify token (GET /webhook handshake) — REQUIRED, no default. `min_length=1` for the same
    # reason: an empty configured token made `?hub.verify_token=` (or a missing param) a VALID
    # handshake, i.e. the endpoint would register itself to any caller. Same `AliasChoices`/rendering
    # treatment as `app_secret` above.
    verify_token: str = Field(
        validation_alias=AliasChoices("WHATSAPP_VERIFY_TOKEN"),
        min_length=1,
        repr=False,
        exclude=True,
    )
    # WABA send-side token. The receiver DOES send with it: since T1.11, every Helena reply goes
    # `service.py:123 WhatsAppServer()` -> `HelenaDispatcher` -> `dispatch.py:122
    # _ScopedWhatsAppSender.send` -> `server.py:111 send_message`, which reads this same token via
    # the sibling `WhatsAppSettings` class. The reply path is nonetheless inoperative in Helm today
    # — not because of this field, but because `WHATSAPP_PHONE_NUMBER_ID` is injected by no
    # deployment, so `send_message` refuses fail-closed before it would ever use the token
    # (`server.py:163-164`; disclosed in full at `docs/review-queue.md:680-705`).
    # Never rendered (`repr=False, exclude=True`) — same treatment as the other three secrets above.
    whatsapp_token: str | None = Field(
        default=None,
        validation_alias=AliasChoices("WHATSAPP_TOKEN", "whatsapp_token"),
        repr=False,
        exclude=True,
    )

    # Accepted for Helm/env parity (deployment-webhook-receiver.yaml:60-64); NOT dialed by this
    # build — see service.py's module docstring for why (no downstream consumer yet, T1.11).
    kafka_bootstrap_servers: str = Field(
        default="localhost:9092",
        validation_alias=AliasChoices("KAFKA_BOOTSTRAP_SERVERS", "kafka_bootstrap_servers"),
    )

    # T1.11: the engine URL Helena's in-process dispatch needs (DMN evaluation + starting
    # SP-OP-ESCALATION-001) — mirrors `agent_runtime`/`worker_runtime`'s own `CIBSEVEN_BASE_URL`.
    cibseven_base_url: str = Field(
        default="http://cibseven:8080/engine-rest",
        validation_alias=AliasChoices("CIBSEVEN_BASE_URL", "cibseven_base_url"),
    )

    # T-C2 / T4b: the tenant Postgres DSN Helena's in-process dispatch needs — BOTH to construct
    # the durable ADR-0007 audit sink her escalation start (SP-OP-ESCALATION-001) fails-closed on
    # AND (T4b) to open the durable LangGraph checkpointer that makes multi-turn conversation state
    # survive across webhook invocations / receiver restarts. Unset here (no default) means the
    # dispatcher cannot be built (module docstring STEP A) and `/webhook` degrades to its explicit
    # 501 — Helena never starts an un-audited escalation, and never runs stateless in prod.
    # Provisioning the secret is an ops/deploy concern (mirrors `app_secret` above), outside scope.
    database_url: str | None = Field(
        default=None, validation_alias=AliasChoices("DATABASE_URL", "database_url")
    )

    # T4b F2 mode discriminator (mirrors `agent_runtime`'s `agent_runtime_mode`): "local" is the
    # ONLY non-production value. Anything else (Helm injects "kubernetes") is PRODUCTION, where a
    # durable checkpointer that fails to provision makes the receiver REFUSE to serve (no dispatcher
    # -> `/webhook` 501) rather than silently run Helena stateless. Local/dev falls back to an
    # in-memory checkpointer with a loud warning. Accepted for env parity (like `database_url`).
    runtime_mode: str = Field(default="local", validation_alias=AliasChoices("RUNTIME_MODE", "runtime_mode"))

    # T4b: bounded timeout for the checkpointer connect+setup() at bring-up. Unlike the two
    # health-first daemons, this receiver binds its health server AFTER dependency bring-up, so a
    # hung Postgres connect must not stall the `/healthz` bind — mirrors the identically-named field
    # in `agent_runtime`/`worker_runtime` settings. A timeout is treated as a setup failure (prod
    # refuses to serve; local falls back to in-memory).
    dep_connect_timeout_s: float = Field(
        default=5.0,
        validation_alias=AliasChoices("DEP_CONNECT_TIMEOUT_S", "dep_connect_timeout_s"),
    )

    # Helm's containerPort is a hardcoded 8080 (deployment-webhook-receiver.yaml:67-69), not env-
    # driven — HEALTH_PORT is accepted for local-dev override parity with the other two daemons.
    health_port: int = Field(default=8080, validation_alias=AliasChoices("HEALTH_PORT", "health_port"))

    # --- Gap WEBHOOK-WAMID-DEDUP (owner decisions R-071/R-072, 2026-09-04) -------------------

    # Dedup window for a `wamid`, in seconds. The default is NOT invented here: ADR-0024:60 fixes
    # "TTL default 86400s (24h, igual ao whatsapp idempotency)" and `docs/runbooks/
    # whatsapp-webhook.md` §3 documents the same 24h for `wamid` deduplication. Meta re-delivers
    # an unacknowledged webhook with backoff over a window far longer than one request, which is
    # why this is measured in hours; a deployment that measured a different window sets the env
    # var instead of editing code. Mirrored in `platform/driver_idempotency.py::DEFAULT_TTL_S`.
    wamid_dedup_ttl_s: float = Field(
        default=86400.0,
        validation_alias=AliasChoices("WHATSAPP_WAMID_DEDUP_TTL_S", "wamid_dedup_ttl_s"),
    )

    # In-flight lease: how long a claimed-but-unfinished delivery suppresses redelivery before it
    # is treated as abandoned (receiver killed mid-turn) and becomes re-claimable. Must stay ABOVE
    # the longest honest synchronous turn and far BELOW the TTL, or the dedup would either lose
    # real messages (too long) or answer twice (too short).
    wamid_dedup_lease_s: float = Field(
        default=120.0,
        validation_alias=AliasChoices("WHATSAPP_WAMID_DEDUP_LEASE_S", "wamid_dedup_lease_s"),
    )

    # R-072 ack-then-queue, DEFAULT OFF. ON, the receiver claims the `wamid` durably, answers Meta
    # 200 immediately and runs the Helena turn in a background task — the ack no longer waits for
    # an LLM/engine round trip, which is what closes the timeout window that CAUSES Meta's retry.
    #
    # WHY THE DEFAULT IS OFF, stated because the owner adopted ack-then-queue as the TARGET form:
    # this build has no broker and no re-drive worker. With the flag ON, a turn that dies after
    # the 200 leaves a `pending` row in `driver_idempotency` and NOTHING re-drives it — Meta was
    # already told the delivery was accepted, so it will not re-deliver either. The durable row
    # makes that loss auditable (`status='pending'` older than the lease is the exact query), but
    # auditable loss is still loss. Turning this ON in production is safe only once a re-drive
    # consumer exists; see `docs/processes/webhook-whatsapp-ack-then-queue.md` for the redelivery
    # contract and the ack-latency budget that note fixes.
    ack_then_queue: bool = Field(
        default=False,
        validation_alias=AliasChoices("WHATSAPP_WEBHOOK_ACK_THEN_QUEUE", "ack_then_queue"),
    )

    # TETO DE VOLUME (Frente 7.1, 14/09/2026). O receptor nao tinha protecao nenhuma: um numero em
    # laco, ou um incidente que faca mil pessoas escreverem ao mesmo tempo, entrava inteiro — cada
    # mensagem uma chamada de modelo, uma DMN e, quando o modelo cai, um escalonamento. A fila
    # humana recebia tudo no pior momento possivel.
    #
    # LIGADOS POR PADRAO, ao contrario das outras novidades deste modulo, e a diferenca e'
    # deliberada: um teto que precisa ser ligado e' um teto que ninguem liga. Os numeros abaixo
    # sao PONTO DE PARTIDA para calibrar com trafego real, nao verdade medida — 6 mensagens por
    # minuto e' mais do que alguem digita conversando, e 120 no tenant e' o dobro do pico das
    # baterias. Zero DESLIGA aquele teto, e desligar passa a ser um ato declarado na task
    # definition em vez de um esquecimento.
    limite_por_conversa_por_minuto: int = Field(
        default=6,
        validation_alias=AliasChoices(
            "WHATSAPP_LIMITE_POR_CONVERSA_POR_MINUTO", "limite_por_conversa_por_minuto"
        ),
    )
    limite_por_tenant_por_minuto: int = Field(
        default=120,
        validation_alias=AliasChoices(
            "WHATSAPP_LIMITE_POR_TENANT_POR_MINUTO", "limite_por_tenant_por_minuto"
        ),
    )

    # DEVOLVER O TURNO NO CORPO DO ACK (12/09/2026). Com `True`, a resposta 200 de `/webhook`
    # ganha dois campos: `resposta` (o texto que a Helena redigiu neste turno) e
    # `conversation_id`. Sem ele, o corpo fica BYTE POR BYTE como sempre foi.
    #
    # POR QUE ISTO PRECISA EXISTIR. O texto da Helena nunca foi lido por ninguem: ele e' redigido,
    # entregue ao envio do WhatsApp e morre num 401 de credencial de preenchimento em dev. Sem ver
    # o texto nao ha' como avaliar se ele presta nem conferir se vaza orientacao clinica — que e'
    # exatamente a cerca `leak_canaries` dos conjuntos golden.
    #
    # POR QUE E' UM PORTAO E NAO O PADRAO. Em producao quem recebe esta resposta e' a Meta, e o
    # contrato do ack com ela e' o CODIGO de status: a Meta re-entrega o que nao recebeu 200 e nao
    # le' o corpo. (O runbook §6 "Local testing" documenta o corpo VISIVEL em dev — inclusive os
    # campos deste portao — e nao e' a fonte do contrato da Meta; a citacao anterior apontava para
    # la' como se fosse.)
    # Ampliar o corpo por padrao mudaria o que sai do processo em producao para ganhar uma
    # conveniencia de teste — entao o default e' `False` e uma implantacao que nao o nomeia nao
    # muda em nada. O `conversation_id` e' keyed-irreversivel e ja' aparece em log e no Cockpit; o
    # `resposta` e' texto voltado ao beneficiario, de uma agente proibida de dar orientacao
    # clinica, sobre uma mensagem ja' pseudonimizada. Os dois viajam sob o MESMO portao porque o
    # motivo de liberar e' um so': alguem estar olhando a conversa numa tela de teste.
    devolve_turno: bool = Field(
        default=False,
        validation_alias=AliasChoices("WHATSAPP_WEBHOOK_DEVOLVE_TURNO", "devolve_turno"),
    )

    @model_validator(mode="before")
    @classmethod
    def _map_bare_field_name_kwargs(cls, data: Any) -> Any:
        """Let `app_secret=`/`verify_token=` keep working as CONSTRUCTION kwargs (this module's own
        tests, `test_app.py`, `test_service.py`) without ever making the bare, un-prefixed
        `APP_SECRET`/`VERIFY_TOKEN` env-var names bindable (see the class-level comment above).

        This only ever fires for a directly-passed kwarg: pydantic-settings' env source has no
        `AliasChoices`/`populate_by_name` path left that produces an `"app_secret"` or
        `"verify_token"` dict key from the environment, so a colliding env var of that bare name
        can never reach this method — it is invisible to `WhatsAppWebhookSettings()` entirely.
        """
        if isinstance(data, dict):
            for field_name, canonical in (
                ("app_secret", "WHATSAPP_APP_SECRET"),
                ("verify_token", "WHATSAPP_VERIFY_TOKEN"),
            ):
                if field_name in data:
                    data[canonical] = data.pop(field_name)
        return data
