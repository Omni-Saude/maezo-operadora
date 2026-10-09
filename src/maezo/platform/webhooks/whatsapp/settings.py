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

import re
from typing import Any, Literal

from pydantic import AliasChoices, Field, field_validator, model_validator
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

    # MEMORIA CLINICA ENTRE TURNOS (Frente 2.1, 15/09/2026). LIGADA por padrao — ao contrario de
    # toda outra novidade deste modulo, e a diferenca e' deliberada.
    #
    # O motivo: desligada, o sistema fica no comportamento MEDIDO COMO ERRADO. Em 13/09 um bebe de
    # 11 meses foi triado pela tabela de ADULTO, tres vezes, porque a mae disse a idade num turno e
    # o sintoma no seguinte. Entre um default que preserva um defeito com paciente do outro lado e
    # um que o corrige, e' o segundo que nao precisa de justificativa.
    #
    # `false` faz cada turno comecar do zero, exatamente como antes desta frente — reversivel por
    # variavel de ambiente, sem deploy de imagem. E ela SO' tem efeito com checkpointer atachado:
    # sem estado duravel nao ha turno anterior de onde lembrar.
    memoria_clinica_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices("WHATSAPP_WEBHOOK_MEMORIA_CLINICA", "memoria_clinica_enabled"),
    )

    # CUSTODIA DO TELEFONE PARA A RETOMADA (GAP-XHITL-4, ADR-0061 — status Proposto; ligar exige
    # ciencia do DPO). Ausente = o receptor NAO grava nada (comportamento anterior). Presente = a
    # cada mensagem recebida o numero e' gravado CIFRADO (envelope KMS, so' `kms:Encrypt` na role
    # do receptor) em `beneficiario_contato_retomada`, com `last_inbound_at` para a janela da Meta.
    recipient_vault_kms_key_arn: str | None = Field(
        default=None,
        validation_alias=AliasChoices("RECIPIENT_VAULT_KMS_KEY_ARN", "recipient_vault_kms_key_arn"),
    )
    recipient_vault_ttl_days: int = Field(
        default=30,
        ge=1,
        validation_alias=AliasChoices("RECIPIENT_VAULT_TTL_DAYS", "recipient_vault_ttl_days"),
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

    # NUMERO UNICO: ROTEADOR HELENA -> LUCAS (ADR-0062, plano `docs/plans/lucas-numero-unico.md`
    # §4). DESLIGADO por padrao, e o default e' o LITERAL `False` de proposito:
    # `scripts/ci/check_roteador_lucas.py` (item 4) reprova qualquer outra forma, porque um default
    # ligado abriria o roteador em todo ambiente que nao o declara — o oposto do que a cerca de
    # `deploy/**` consegue ver. Desligado, o roteador NEM E' CONSTRUIDO (`service.py`) e o
    # despachante segue byte a byte o caminho de hoje. Ligado nesta onda, ele roda em SOMBRA: grava
    # o agente ativo (`helena`) e o motivo, loga os sinais lexicos e nao muda resposta nenhuma.
    roteador_lucas_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices("MAEZO_ROTEADOR_LUCAS", "roteador_lucas_enabled"),
    )
    # Janela de inatividade da conversa com o Lucas (§2.2): depois dela, um "oi" volta para a
    # Helena. `expira_em = ultimo_turno_em + janela`. A faixa recusa no boot o que nao faz sentido.
    lucas_inatividade_minutos: int = Field(
        default=60,
        ge=5,
        le=1440,
        validation_alias=AliasChoices("MAEZO_LUCAS_INATIVIDADE_MINUTOS", "lucas_inatividade_minutos"),
    )
    # Fonte dos fatos de cobranca do Lucas. `simulada` (o default) ou `amh` — a fonte REAL pelos
    # contratos `billing-status`/`subject-resolution` da AMH (decisao do dono 06/10/2026). `amh` so'
    # sobe com os contratos publicados e pinados e com toda a configuracao abaixo; faltando qualquer
    # peca o receptor RECUSA servir (`service.py::_build_lucas_turno`). Qualquer outro valor (o
    # antigo `cnab`, por exemplo) e' recusado aqui. A cerca reprova a variavel presente fora de
    # `dev-sa-east-1`.
    lucas_fonte_cobranca: Literal["simulada", "amh"] = Field(
        default="simulada",
        validation_alias=AliasChoices("MAEZO_LUCAS_FONTE_COBRANCA", "lucas_fonte_cobranca"),
    )

    # IDENTIDADE DO BENEFICIARIO NA HELENA (DL-0077, decisao do dono 06/10/2026). DESLIGADA por padrao.
    # Ligada, o despachante resolve QUEM escreve (telefone -> `portable_subject_ref` -> perfil minimo)
    # pelos MESMOS contratos/executores da AMH do Lucas, sob o principal `helena`, e entrega ao
    # estado da Helena so' a identidade pseudonima. Exige TODA a configuracao `amh_*` abaixo e os dois
    # OpenAPI pinados: faltando qualquer peca o receptor RECUSA servir (nunca cai em "sem identidade"
    # em silencio). Independe do roteador do Lucas e de `lucas_fonte_cobranca`.
    helena_identidade_amh: bool = Field(
        default=False,
        validation_alias=AliasChoices("MAEZO_HELENA_IDENTIDADE_AMH", "helena_identidade_amh"),
    )
    # PRAZO TOTAL da identidade (DL-0079): o teto, em segundos, que a resolucao (telefone ->
    # referencia) MAIS o perfil podem somar ao turno. Tambem e' o teto de CADA chamada ao port (o
    # executor do gateway aceita ate' 30 s), para o prazo por chamada nunca ser o limite que derruba
    # uma resolucao que caberia no total. Medido em dev (07/10/2026): ~3 s + ~2-3 s; o antigo 6 s fixo
    # estourava com a AMH tendo achado a pessoa. A faixa recusa no boot o que nao faz sentido.
    helena_identidade_prazo_s: float = Field(
        default=15.0,
        ge=1.0,
        le=30.0,
        validation_alias=AliasChoices("MAEZO_HELENA_IDENTIDADE_PRAZO_S", "helena_identidade_prazo_s"),
    )
    # HISTORICO CURTO DA CONVERSA NA HELENA (DL-0080, 07/10/2026). DESLIGADO por padrao, e desligado
    # e' a Helena de antes byte a byte. Ligado: as ultimas 12 mensagens (so' texto, 300 caracteres,
    # janela de 6 h) vao ao classificador e a redacao do `inform` como bloco NAO CONFIAVEL, e o modo
    # coleta liga junto. Base LGPD PENDENTE de ratificacao do DPO — nao ligar fora de dev sem ela.
    helena_historico: bool = Field(
        default=False,
        validation_alias=AliasChoices("MAEZO_HELENA_HISTORICO", "helena_historico"),
    )
    # AVISO DE IDENTIDADE (DL-0078, decisao do dono 07/10/2026): o nome de EXIBICAO da operadora nos
    # textos fixos de identidade da Helena ("Reconheci este numero no cadastro de beneficiarios da
    # {nome}..."). So' tem efeito com `helena_identidade_amh` ligada. Texto curto e simples (letras,
    # espaco, `.`, `'`, `&`, `-`): ele vai literal ao beneficiario, entao nada de chave, URL ou controle.
    helena_identidade_nome_operadora: str = Field(
        default="Austa Clínicas",
        validation_alias=AliasChoices(
            "MAEZO_HELENA_IDENTIDADE_NOME_OPERADORA", "helena_identidade_nome_operadora"
        ),
    )

    # FATOS DO PLANO NA HELENA (DL de 07/10/2026, decisao do dono). DESLIGADA por padrao. Ligada, a
    # Helena responde elegibilidade, carteirinha, carencia e autorizacao do PROPRIO beneficiario com os
    # fatos do contrato TINA da AMH (`interop/tina.read`). EXIGE `helena_identidade_amh` ligada (sem
    # identidade nao ha' de quem consultar: recusado aqui, no boot), o interop configurado, o escopo
    # `interop/tina.read` em `amh_interop_scopes`, `amh_tina_openapi_path` e o manifest v1.2 pinado —
    # faltando qualquer peca o receptor RECUSA servir. Desligada = a Helena de sempre, byte a byte.
    helena_consultas_amh: bool = Field(
        default=False,
        validation_alias=AliasChoices("MAEZO_HELENA_CONSULTAS_AMH", "helena_consultas_amh"),
    )

    # ACESSO DO BENEFICIARIO (DL-0083, decisao do dono 08/10/2026). DESLIGADO por padrao; desligado, o
    # receptor e' o de sempre byte a byte. Ligado, TODA conversa passa antes por uma maquina de estados
    # deterministica (consentimento -> CPF [-> nascimento] -> verificado), sem LLM, antes de qualquer
    # agente. EXIGE o interop configurado, os escopos `interop/subject.verify` e `interop/consent.write`,
    # a chave do hash de verificacao e os dois OpenAPI pinados: faltando qualquer peca o receptor
    # RECUSA servir (nunca sobe "sem acesso" em silencio).
    acesso_beneficiario: bool = Field(
        default=False,
        validation_alias=AliasChoices("MAEZO_ACESSO_BENEFICIARIO", "acesso_beneficiario"),
    )
    # Validade da verificacao (CPF/nascimento) em horas. Passado esse prazo desde a verificacao, a
    # proxima mensagem recomeca em `aguardando_cpf` (o consentimento vale ate' ser revogado).
    acesso_validade_horas: int = Field(
        default=24,
        ge=1,
        le=168,
        validation_alias=AliasChoices("MAEZO_ACESSO_VALIDADE_HORAS", "acesso_validade_horas"),
    )

    # VALORES NA RESPOSTA DO LUCAS (DL-0086, revisao de seguranca do PR #709). DESLIGADA por padrao, e
    # desligada e' o Lucas de antes: a fonte AMH entrega so' os quatro fatos de conciliacao e a pergunta
    # de valor (`consulta_valores`) escala. Ligada, a fonte AMH entrega tambem os FATOS DE VALOR
    # (valores, coparticipacao, saldo, datas de pagamento, boleto mascarado) — e SO' para a ref. que veio
    # da verificacao do acesso (DL-0083). EXIGE `acesso_beneficiario` ligada e `lucas_fonte_cobranca=amh`:
    # sem verificacao quem segura o celular receberia os valores de outra pessoa (recusado aqui, no boot).
    # A Helena classifica `consulta_valores` com a flag ligada ou nao (classify-v7 nao depende dela).
    lucas_consulta_valores: bool = Field(
        default=False,
        validation_alias=AliasChoices("MAEZO_LUCAS_CONSULTA_VALORES", "lucas_consulta_valores"),
    )

    @model_validator(mode="after")
    def _consultas_exigem_identidade(self) -> WhatsAppWebhookSettings:
        if self.helena_consultas_amh and not self.helena_identidade_amh:
            raise ValueError(
                "MAEZO_HELENA_CONSULTAS_AMH exige MAEZO_HELENA_IDENTIDADE_AMH ligada "
                "(sem identidade resolvida nao ha' de quem consultar os fatos do plano)"
            )
        return self

    @model_validator(mode="after")
    def _valores_exigem_verificacao(self) -> WhatsAppWebhookSettings:
        if self.lucas_consulta_valores and not (
            self.acesso_beneficiario and self.lucas_fonte_cobranca == "amh"
        ):
            raise ValueError(
                "MAEZO_LUCAS_CONSULTA_VALORES exige MAEZO_ACESSO_BENEFICIARIO ligada e "
                "MAEZO_LUCAS_FONTE_COBRANCA=amh (valor so' para beneficiario verificado, DL-0086)"
            )
        return self

    @field_validator("helena_identidade_nome_operadora")
    @classmethod
    def _nome_operadora_valido(cls, valor: str) -> str:
        nome = " ".join(valor.split())
        if not 1 <= len(nome) <= 60 or not re.fullmatch(r"[^\W\d_]+(?:[ .'&-]+[^\W\d_]+)*\.?", nome):
            raise ValueError(
                "MAEZO_HELENA_IDENTIDADE_NOME_OPERADORA: nome de exibicao invalido "
                "(1 a 60 caracteres; letras, espaco, `.`, `'`, `&`, `-`)"
            )
        return nome

    # --- Interop AMH (lidas com `lucas_fonte_cobranca == "amh"` OU `helena_identidade_amh`) ----
    # Origem do servico interop da AMH: ALB INTERNO, `http(s)://host[:porta]`, sem caminho.
    amh_interop_base_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices("MAEZO_AMH_INTEROP_BASE_URL", "amh_interop_base_url"),
    )
    # Endpoint OAuth2 de token do Cognito da AMH (`https://.../oauth2/token`).
    amh_interop_token_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices("MAEZO_AMH_INTEROP_TOKEN_URL", "amh_interop_token_url"),
    )
    amh_interop_client_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("MAEZO_AMH_INTEROP_CLIENT_ID", "amh_interop_client_id"),
    )
    # Escopos pedidos no `client_credentials`, separados por espaco. O default e' o de SEMPRE: o Cognito
    # recusa o token INTEIRO se o client nao tiver um escopo pedido, entao `interop/tina.read` e os do acesso
    # (`interop/subject.verify`, `interop/consent.write`) so' entram pelo deploy junto com a flag de cada um
    # (`amh_interop_scopes_efetivos` no Terraform) — nunca por default (revisao do #700, P1-a).
    amh_interop_scopes: str = Field(
        default="interop/billing.read interop/subject.resolve interop/profile.read",
        validation_alias=AliasChoices("MAEZO_AMH_INTEROP_SCOPES", "amh_interop_scopes"),
    )
    # Segredo do cliente Cognito. Nunca renderizado; chega ao executor SO' pelo cofre
    # (`tool_registry.AGENT_CREDENTIAL_FIELDS`). So' o nome canonico no `AliasChoices` (como
    # `app_secret`): o nome do campo abriria um `AMH_INTEROP_CLIENT_SECRET` sem prefixo no ambiente.
    amh_interop_client_secret: str | None = Field(
        default=None,
        validation_alias=AliasChoices("MAEZO_AMH_INTEROP_CLIENT_SECRET"),
        repr=False,
        exclude=True,
    )
    # Chave DEDICADA do hash `amh-phone-lookup-v1` (segredo `amh/interop/phone-lookup-key`). Nao e'
    # o `PHI_HMAC_KEY`. Mesmo tratamento do segredo acima.
    amh_phone_lookup_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("MAEZO_AMH_PHONE_LOOKUP_KEY"),
        repr=False,
        exclude=True,
    )
    # Chave DEDICADA do hash `amh-subject-verify-v1` (segredo `amh/interop/subject-verify-key`), que cobre
    # CPF e CPF+nascimento na verificacao do acesso (DL-0083). Lida SO' com `acesso_beneficiario` ligada.
    # Nao e' o `PHI_HMAC_KEY` nem a chave do telefone. Mesmo tratamento dos segredos acima.
    amh_subject_verify_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("MAEZO_AMH_SUBJECT_VERIFY_KEY"),
        repr=False,
        exclude=True,
    )
    # Tenant da operadora no vocabulario da AMH (entra no HMAC do telefone e no corpo da resolucao).
    amh_interop_tenant: str = Field(
        default="austa_operadora",  # AMH ADR-046 (06/10/2026): era "omni"; o dado e' da Austa Clinicas
        validation_alias=AliasChoices("MAEZO_AMH_INTEROP_TENANT", "amh_interop_tenant"),
    )
    amh_interop_purpose_of_use: str = Field(
        default="sharing_amh_internal",
        validation_alias=AliasChoices("MAEZO_AMH_INTEROP_PURPOSE_OF_USE", "amh_interop_purpose_of_use"),
    )
    # Caminhos dos dois artefatos OpenAPI publicados pela AMH; os bytes so' valem com o digest no pin.
    amh_billing_status_openapi_path: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "MAEZO_AMH_BILLING_STATUS_OPENAPI_PATH", "amh_billing_status_openapi_path"
        ),
    )
    amh_subject_resolution_openapi_path: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "MAEZO_AMH_SUBJECT_RESOLUTION_OPENAPI_PATH", "amh_subject_resolution_openapi_path"
        ),
    )
    # Os dois OpenAPI do acesso do beneficiario (DL-0083), lidos SO' com `acesso_beneficiario` ligada; os
    # bytes so' valem com o digest no pin (ainda NAO publicados na AMH em 08/10/2026: inerte ate' la').
    amh_subject_verification_openapi_path: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "MAEZO_AMH_SUBJECT_VERIFICATION_OPENAPI_PATH", "amh_subject_verification_openapi_path"
        ),
    )
    # O billing-status 0.2.0 (manifest v1.4, decisao do DPO de 08/10/2026): a leitura de cobranca do Lucas
    # com o acesso ligado declara `purpose_of_use=atendimento_whatsapp`. Lido SO' com o acesso ligado e a
    # fonte AMH; sem ele (ou sem o bloco `manifest_v1_4` no pin) a composicao recusa e o receptor nao sobe.
    amh_billing_status_atendimento_openapi_path: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "MAEZO_AMH_BILLING_STATUS_ATENDIMENTO_OPENAPI_PATH", "amh_billing_status_atendimento_openapi_path"
        ),
    )
    #: DL-0084 (AMH #214): o OpenAPI da resolucao pelo documento (telefone sem cadastro), 3o artefato do v1.3.
    amh_document_resolution_openapi_path: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "MAEZO_AMH_DOCUMENT_RESOLUTION_OPENAPI_PATH", "amh_document_resolution_openapi_path"
        ),
    )
    amh_consent_record_openapi_path: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "MAEZO_AMH_CONSENT_RECORD_OPENAPI_PATH", "amh_consent_record_openapi_path"
        ),
    )
    # O OpenAPI TINA (fatos do plano), lido SO' com `helena_consultas_amh` ligada; os bytes so' valem
    # com o digest no bloco `manifest_v1_2` do pin (ainda DRAFT na AMH em 07/10/2026).
    amh_tina_openapi_path: str | None = Field(
        default=None,
        validation_alias=AliasChoices("MAEZO_AMH_TINA_OPENAPI_PATH", "amh_tina_openapi_path"),
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
                ("amh_interop_client_secret", "MAEZO_AMH_INTEROP_CLIENT_SECRET"),
                ("amh_phone_lookup_key", "MAEZO_AMH_PHONE_LOOKUP_KEY"),
                ("amh_subject_verify_key", "MAEZO_AMH_SUBJECT_VERIFY_KEY"),
            ):
                if field_name in data:
                    data[canonical] = data.pop(field_name)
        return data
