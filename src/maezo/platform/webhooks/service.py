"""Webhook-receiver daemon (T1.6, defect B1; T1.11 real dispatch) — serves the WhatsApp app
built in `whatsapp/app.py`.

`deployment-webhook-receiver.yaml` runs `command: ["python", "-m", "maezo.platform.webhooks"]`,
but `src/maezo/platform/webhooks/` did not exist at all — `webhookReceiver.enabled: true` in
`values.yaml` (already ON, unlike `gateway`) meant this Deployment was CrashLoopBackOff'ing in
any environment that actually applied the chart. T1.6 closed that gap with a health-only
scaffold; T1.11 adds the bounded, non-fatal dependency bring-up Helena's real dispatch needs.

Health-first bring-up, matching the `worker_runtime`/`agent_runtime` pattern (T1.1/T1.6):
`WhatsAppWebhookSettings` construction (in `__main__.py`, BEFORE `run()` is even called) is
ITSELF the fail-closed boot gate (constraint 2): a missing `WHATSAPP_APP_SECRET`/
`WHATSAPP_VERIFY_TOKEN` raises a pydantic `ValidationError` before any server binds, matching
`deployment-webhook-receiver.yaml:43-46`'s documented expectation exactly.

  STEP A  Bring up Helena's dispatch dependencies, BOUNDED and NON-FATAL (same isolation
          discipline as `worker_runtime`/`agent_runtime`'s `_bring_up_dependencies`): the
          inference provider, the DMN transport, the CIB Seven start transport, the WhatsApp
          send client, and the pseudonymizer — all PURE constructors (no network call until a
          node runs) — PLUS (T4b) the ONE bounded bring-up I/O this receiver now performs: the
          durable LangGraph checkpointer's connect+`setup()` (multi-turn conversation state that
          survives webhook invocations / receiver restarts), bounded by `dep_connect_timeout_s`
          so a hung Postgres cannot stall the `/healthz` bind that follows. F2 FAIL-CLOSED: in
          production (`runtime_mode != "local"`) a checkpointer that fails to provision makes the
          receiver REFUSE TO SERVE — the dispatcher is dropped and `/webhook` returns its explicit
          501 rather than running Helena stateless; local/dev falls back to an in-memory saver
          with a loud warning. On success, `/webhook` dispatches for real; on any failure (caught,
          logged) it keeps the prior explicit-501 behavior — never a fabricated dispatch, never a
          silently-stateless prod dispatch. No Kafka producer is constructed (still true — see
          `dispatch.py`'s module docstring for why an in-process call, not a queue, is this
          build's honest choice).
  STEP B  Bind the app (health + `/webhook`) with the STEP A result baked in.
  STEP C  `/readyz` reflects `config_loaded` (`whatsapp/app.py`) — dispatch readiness is NOT a
          separate gate: an unconfigured dispatcher degrades `/webhook` to 501, it does not
          affect `/readyz` (mirrors `agent_runtime`'s stance that a "ready" pod may still be
          missing an optional capability, as long as it says so honestly per request).
  STEP D  SIGTERM/SIGINT -> drain: `live=False` (`/healthz` -> 503), stop the server, close the
          long-lived CIB Seven transport.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
from dataclasses import dataclass
from typing import TYPE_CHECKING

import structlog

from maezo.gateway.pseudonymizer import Pseudonymizer
from maezo.gateway.seams import GatedSeam
from maezo.gateway.seams.cibseven import GatedCibSevenTransport
from maezo.gateway.tool_registry import (
    build_agent_seam_context,
    build_agent_seams,
    effect_seams_gated,
)
from maezo.platform.driver_idempotency import PostgresDriverIdempotencyRegistry
from maezo.platform.health import build_health_server
from maezo.runtime.checkpoint import Checkpointer, provision_checkpointer
from maezo.runtime.inference import InferenceProvider
from maezo.tools.mcp_whatsapp.server import WhatsAppServer

from .whatsapp.app import create_app
from .whatsapp.dedup import WhatsAppDedupGuard
from .whatsapp.dispatch import HelenaDispatcher
from .whatsapp.limite import LimitadorDeVolume
from .whatsapp.settings import WhatsAppWebhookSettings

if TYPE_CHECKING:
    from maezo.agents.lucas.administrative.runtime import AdministrativeJourneyRuntime
    from maezo.agents.lucas.fonte_cobranca_amh import FonteCobrancaAmh
    from maezo.gateway.amh_interop import AmhInteropComposition
    from maezo.gateway.capabilities.journeys.contracts import JourneyBinding, JourneyCurrentnessPort
    from maezo.gateway.capabilities.journeys.driver import JourneyDriver
    from maezo.gateway.seams import SeamContext

    from .whatsapp.helena_consultas import ConsultasPlanoAmh
    from .whatsapp.helena_identidade import IdentidadeHelena
    from .whatsapp.lucas_turno import LucasTurno

#: F2 mode discriminator — the ONLY non-production `runtime_mode`. Anything else (Helm injects
#: "kubernetes") is PRODUCTION, where a checkpointer that fails to provision makes the receiver
#: refuse to serve. Mirrors `agent_runtime.service._LOCAL_RUNTIME_MODE`.
_LOCAL_RUNTIME_MODE = "local"

#: A versao do Lucas que o executor da fonte AMH grava no elo de auditoria (a mesma do `LucasTurno`).
_LUCAS_AGENT_VERSION = "lucas@v0"
#: Idem para a identidade da Helena (DL-0077): a leitura AMH dela e' auditada sob o principal `helena`.
_HELENA_AGENT_VERSION = "helena@v0"

logger = structlog.get_logger(__name__)


@dataclass
class WebhookState:
    settings: WhatsAppWebhookSettings
    live: bool = True
    dispatcher: HelenaDispatcher | None = None
    dispatcher_error: str | None = None
    # ONDA 1 §5.5: the GATED engine transport (its `close()` is a pass-through lifecycle call,
    # not an effect — no catalogue operation, no decision, no telemetry line).
    cibseven_transport: GatedCibSevenTransport | None = None
    # T4b: the durable LangGraph checkpointer wired into the dispatcher (its pool is released on
    # drain). None when the dispatcher was never built, or when a prod-mode provision failed
    # closed (in which case `dispatcher` is also None -> `/webhook` 501, refuse-to-serve).
    checkpointer: Checkpointer | None = None
    checkpointer_backend: str | None = None
    #: NUMERO UNICO, onda (d) (ADR-0062). O turno do Lucas, SO' com `MAEZO_ROTEADOR_LUCAS`
    #: ligado (`_build_lucas_turno`); desligado fica `None` e nada do Lucas existe no processo.
    #: Nesta onda ninguem o chama: o despachante passa a chamar na onda (e).
    lucas_turno: LucasTurno | None = None
    #: IDENTIDADE DO BENEFICIARIO NA HELENA (DL-0077), SO' com `MAEZO_HELENA_IDENTIDADE_AMH` ligada
    #: (`_build_helena_identidade`); desligado fica `None` e nada da AMH existe para a Helena.
    helena_identidade: IdentidadeHelena | None = None
    #: FATOS DO PLANO NA HELENA (DL de 07/10/2026), SO' com `MAEZO_HELENA_CONSULTAS_AMH` ligada (que
    #: exige a identidade ligada). Reusa a composicao AMH da identidade; desligado fica `None`.
    helena_consultas: ConsultasPlanoAmh | None = None

    administrative_runtime: AdministrativeJourneyRuntime | None = None

    def is_live(self) -> bool:
        return self.live


def _helena_model_tiers() -> dict[str, str]:
    """Helena's `task_kind -> tier` map from `spec/agents/helena/agent.yaml` (AF-12). TOTAL.

    LOUD, NOT SILENT, AND DELIBERATELY NOT FAIL-CLOSED. Unlike `agent_runtime`, this receiver has
    no `AGENT_DEFINITION_PATH` ConfigMap contract, so whether `spec/agents/` is readable here is a
    deployment property this function cannot assume. Refusing to build the dispatcher over a
    missing tier map would take Helena's live WhatsApp path to a 501 for a telemetry-grade config
    fact — a far worse failure than the one it would prevent, given that the map cannot change
    which model is used while exactly one is configured (`InferenceProvider._resolve_task_model`).

    So a load failure logs at ERROR and returns `{}`, and every subsequent call is counted under
    the `sem_mapa` tier on `maezo_llm_tier_resolution_total` — visible in both the log stream and
    the metric, which is what makes this a disclosed degrade rather than a silent fallback. A
    tier OUTSIDE the ADR-0009 vocabulary is NOT absorbed here: `InferenceProvider` still refuses
    to construct on it, and that refusal still takes `/webhook` to its explicit 501.
    """
    from maezo.agents import AgentLoader

    try:
        return AgentLoader().load_by_id("helena").model_tiers()
    except Exception as exc:  # see the docstring: total by design, never silent.
        logger.error(
            "helena_model_tiers_unavailable",
            error=f"{type(exc).__name__}: {exc}",
            detail="spec/agents/helena/agent.yaml could not be read; LLM calls will be counted "
            "under the `sem_mapa` tier sentinel. This does NOT change which model is used.",
        )
        return {}


def _build_dispatcher(
    settings: WhatsAppWebhookSettings,
) -> tuple[HelenaDispatcher, GatedCibSevenTransport]:
    """Construct Helena's dispatch dependencies. Pure construction — no network call happens
    until a node actually runs (mirrors `agent_runtime`'s `_build_tool_deps`).

    T-C2 fail-closed: Helena's escalation start (SP-OP-ESCALATION-001) structurally requires a
    durable ADR-0007 audit sink (`start_process_idempotent`'s fence). Without `DATABASE_URL` the
    sink cannot be constructed, so this raises — caught by `_bring_up_dependencies`, leaving
    `state.dispatcher` None so `/webhook` degrades to its explicit 501 (module docstring STEP A):
    Helena never starts an un-audited escalation."""
    if not settings.database_url:
        raise ValueError(
            "DATABASE_URL is required to build Helena's dispatcher: the escalation process start "
            "must audit to a durable ADR-0007 sink BEFORE any engine effect (T-C2 fence). Refusing "
            "to construct a dispatcher that could start an un-audited escalation."
        )
    # ONDA 1 §5.5 — THE LIVE AGENT PATH. This root used to construct its own `InferenceProvider()`,
    # `CibSevenDmnTransport`, `CibSevenHttpTransport` and `WhatsAppServer` (adversary A-4's
    # concrete instance, and counterexample C-A1: `_build_tool_deps` gates only the health-only
    # daemon, which never executes a turn). Every effect seam now comes from the ONE sanctioned
    # constructor, so the objects Helena's graph receives here are the SAME gated shapes the other
    # four roots hand out.
    #
    # `build_agent_seams` RAISES if a seam cannot be wrapped (I-2). That raise is caught by
    # `_bring_up_dependencies`, which leaves `state.dispatcher` None and degrades `/webhook` to
    # its explicit 501 — the identical refuse-to-serve shape a missing DSN or a missing PHI key
    # already triggers. There is no path where this receiver serves a turn with an ungated seam.
    seams = build_agent_seams(
        settings=settings,
        agent_id="helena",
        # AF-12: the LIVE agent path gets Helena's declared tier map too. Without this, the one
        # process that actually runs turns would report every LLM call under the `sem_mapa`
        # sentinel, and the tiering wire would be true only of the health-only daemon.
        inference=InferenceProvider(model_tiers=_helena_model_tiers()),
    )
    seam_context = build_agent_seam_context(tenant=settings.tenant_id, agent_id="helena")
    # Gap `WEBHOOK-WAMID-DEDUP` (owner decisions R-071/R-072/R-073): the durable dedup registry
    # over the repurposed `driver_idempotency` table. Built HERE, in the one place that already
    # owns the mandatory DSN and the keyed pseudonymizer, so BOTH legs of the guard — the inbound
    # claim in `whatsapp/app.py` and the outbound `send` claim in `WhatsAppServer.send_message` —
    # share one registry, one pool and one key derivation. Construction opens NO connection (the
    # pool is lazy), matching this function's "pure construction" contract; a DSN this receiver
    # cannot reach surfaces on the first claim, where the receiver fails closed with a non-2xx
    # instead of dispatching unprotected.
    pseudonymizer = Pseudonymizer.from_settings(
        phi_hmac_key=settings.phi_hmac_key,
        production=settings.runtime_mode != _LOCAL_RUNTIME_MODE,
        tenant_id=settings.tenant_id,
    )
    dedup = WhatsAppDedupGuard(
        registry=PostgresDriverIdempotencyRegistry(
            dsn=settings.database_url,
            tenant=settings.tenant_id,
            lease_s=settings.wamid_dedup_lease_s,
        ),
        pseudonymizer=pseudonymizer,
        tenant=settings.tenant_id,
        ttl_s=settings.wamid_dedup_ttl_s,
    )
    whatsapp_client = WhatsAppServer(dedup=dedup.registry)
    # GAP-XHITL-4 / ADR-0061: custodia cifrada do telefone para a retomada. SO' com a chave KMS
    # configurada (Proposto: ligar exige ciencia do DPO); sem ela, nada e' gravado.
    recipient_vault = None
    if settings.recipient_vault_kms_key_arn:
        from maezo.gateway.recipient_custody import AwsKmsKeyWrapper, PostgresRecipientVault

        recipient_vault = PostgresRecipientVault(
            dsn=settings.database_url,
            tenant=settings.tenant_id,
            wrapper=AwsKmsKeyWrapper(key_arn=settings.recipient_vault_kms_key_arn),
            ttl_days=settings.recipient_vault_ttl_days,
        )
    # NUMERO UNICO (ADR-0062, plano `lucas-numero-unico.md` §4). O roteador SO' e' construido com
    # `MAEZO_ROTEADOR_LUCAS` ligado: desligado, `roteador` fica `None` e o despachante segue o
    # caminho de hoje. `scripts/ci/check_roteador_lucas.py` (item 5) reprova construir
    # `ConversaRouter` fora de um `if settings.roteador_lucas_enabled`. Um lexico invalido recusa
    # aqui, no boot, como qualquer politica do repo (`pre_roteamento.carregar` e' fail-closed).
    roteador = None
    if settings.roteador_lucas_enabled:
        from datetime import timedelta

        from maezo.platform.webhooks.whatsapp import pre_roteamento
        from maezo.platform.webhooks.whatsapp.roteamento import ConversaRouter, PostgresAgenteAtivoStore

        roteador = ConversaRouter(
            tenant_id=settings.tenant_id,
            store=PostgresAgenteAtivoStore(dsn=settings.database_url, tenant=settings.tenant_id),
            lexicos=pre_roteamento.carregar(),
            inatividade=timedelta(minutes=settings.lucas_inatividade_minutos),
            # Onda (e): o Lucas e' construido logo depois, sob o MESMO interruptor
            # (`_build_lucas_turno`), e uma falha ali recusa servir — entao, com o roteador
            # existindo, o Lucas existe. Sem ele o despachante nem chega a pedir um handoff.
            lucas_disponivel=True,
        )
        logger.warning(
            "roteador_lucas_ligado",
            tenant_id=settings.tenant_id,
            lexicos=roteador.versao_lexicos,
            inatividade_minutos=settings.lucas_inatividade_minutos,
            fonte_cobranca=settings.lucas_fonte_cobranca,
        )
    dispatcher = HelenaDispatcher(
        tenant_id=settings.tenant_id,
        inference=seams["inference"],
        dmn=seams["dmn"],
        cibseven=seams["cibseven"],
        whatsapp_client=whatsapp_client,
        # Keyed HMAC pseudonymizer (ADR-0035). Fail-closed: in a production `runtime_mode` an
        # absent PHI_HMAC_KEY raises `PseudonymizerKeyMissingError` here — caught by
        # `_bring_up_dependencies`, leaving `state.dispatcher` None so `/webhook` degrades to its
        # explicit 501 (never a fabricated dispatch, never a reversible unkeyed pseudonym). Same
        # fence discipline as the DATABASE_URL check above.
        pseudonymizer=pseudonymizer,
        audit_sink=seams["audit_sink"],
        # TETO DE VOLUME (Frente 7.1). Construido AQUI, na raiz de composicao, e nao dentro do
        # despachante: o limitador guarda estado entre mensagens, e um por turno nao limitaria
        # nada. Os testes que constroem um despachante direto continuam sem teto (`None`), que e'
        # o comportamento anterior; o receptor implantado sempre tem.
        limitador=LimitadorDeVolume(
            por_conversa=settings.limite_por_conversa_por_minuto,
            por_tenant=settings.limite_por_tenant_por_minuto,
        ),
        # OUTBOUND leg of the same guard (R-071: "tratadas como uma entrega so").
        dedup=dedup,
        # MEMORIA CLINICA ENTRE TURNOS (Frente 2.1). Vem das settings, entao o receptor implantado
        # lembra quem e' o paciente salvo desligamento EXPLICITO na task definition — e a decisao
        # fica legivel no `terraform plan` em vez de escondida num default de codigo.
        memoria_clinica_enabled=settings.memoria_clinica_enabled,
        # HISTORICO CURTO DA CONVERSA (DL-0080): `MAEZO_HELENA_HISTORICO`, default `false`.
        historico_enabled=settings.helena_historico,
        # ONDA 1 §5.5 / O4 — the per-request knot. The WhatsApp seam CANNOT be built here: it is
        # `_ScopedWhatsAppSender`, created per turn inside `dispatch()` around the raw recipient of
        # the one inbound request. So the DECISION half is built once, here, and frozen; the
        # dispatcher re-wraps its per-turn sender with it. See `tool_registry`'s module docstring
        # for the three rejected alternatives and their counterexamples.
        seam_context=seam_context,
        recipient_vault=recipient_vault,
        roteador=roteador,
    )
    cibseven = seams["cibseven"]
    return dispatcher, cibseven


def _build_lucas_turno(settings: WhatsAppWebhookSettings, dispatcher: HelenaDispatcher) -> LucasTurno | None:
    """Onda (d) do numero unico: o turno do Lucas, SO' com o roteador ligado. Pura construcao.

    Desligado devolve `None` sem importar nada do Lucas por este caminho. Ligado, os seams vem do
    construtor sancionado com o principal `lucas` (`build_agent_seams("lucas")`), entao o DMN, o
    motor e o modelo decidem e registram sob o Lucas, nunca sob a Helena. Tres escolhas:

    - O MESMO `InferenceProvider` cru da Helena, re-embrulhado para o Lucas (plano §3: mesma zona,
      mesmo provedor in-region quando `helena_phi_ligado`). Construir um segundo provedor seria o
      contra-exemplo C-A2 de `tool_registry`.
    - O MESMO guard de dedup do despachante: as chaves de saida do Lucas (`lucas:{message_id}`)
      saem do mesmo pseudonimizador e do mesmo store duravel que as da Helena.
    - O seam `whatsapp` que `build_agent_seams` monta para o Lucas e' DESCARTADO: ele envia para o
      hash literal (`agents/lucas/adapters.py`), e o envio vivo e' o remetente por turno que o
      `LucasTurno` embrulha com o gate do principal `lucas`.

    `scripts/ci/check_roteador_lucas.py` (item 5) reprova construir `LucasTurno` fora do corpo de
    um `if settings.roteador_lucas_enabled`. Qualquer falha aqui (zona do Lucas que nao seja
    `general`, fonte desconhecida, seam que nao embrulha) LEVANTA, e `_bring_up_dependencies`
    recusa servir: ligado, o receptor nao sobe pela metade.
    """
    lucas_turno = None
    if settings.roteador_lucas_enabled:
        from maezo.agents.lucas.fonte_cobranca import FonteCobranca, FonteCobrancaSimulada
        from maezo.platform.webhooks.whatsapp.lucas_turno import LucasTurno

        if dispatcher.dedup is None:
            raise ValueError(
                "lucas_turno: o despachante nao tem guard de dedup; sem ele nao ha' chave de saida"
            )
        helena_inference = dispatcher.inference
        if not isinstance(helena_inference, GatedSeam):
            raise ValueError(
                "lucas_turno: o provedor da Helena nao e' um seam cercado; recusando reembrulhar"
            )
        if settings.lucas_fonte_cobranca not in ("simulada", "amh"):  # Literal: defesa em profundidade
            raise ValueError("lucas_turno: fonte de cobranca desconhecida")
        seams = build_agent_seams(settings=settings, agent_id="lucas", inference=helena_inference.inner)
        seam_context = build_agent_seam_context(tenant=settings.tenant_id, agent_id="lucas")
        fonte: FonteCobranca = FonteCobrancaSimulada()
        amh_interop: AmhInteropComposition | None = None
        if settings.lucas_fonte_cobranca == "amh":
            fonte, amh_interop = _build_fonte_cobranca_amh(
                settings, seam_context=seam_context, audit=seams.get("audit_sink")
            )
        lucas_turno = LucasTurno(
            tenant_id=settings.tenant_id,
            inference=seams["inference"],
            dmn=seams["dmn"],
            cibseven=seams["cibseven"],
            audit_sink=seams["audit_sink"],
            seam_context=seam_context,
            dedup=dispatcher.dedup,
            fonte=fonte,
            hash_telefone_amh=amh_interop.phone_hasher if amh_interop is not None else None,
            fonte_aclose=amh_interop.aclose if amh_interop is not None else None,
        )
        logger.warning(
            "lucas_turno_construido",
            tenant_id=settings.tenant_id,
            fonte_cobranca=settings.lucas_fonte_cobranca,
            detail="entrada do Lucas pronta; o despachante so' a chama a partir da onda (e)",
        )
    return lucas_turno


def _build_fonte_cobranca_amh(
    settings: WhatsAppWebhookSettings, *, seam_context: SeamContext, audit: object
) -> tuple[FonteCobrancaAmh, AmhInteropComposition]:
    """A fonte REAL de cobranca do Lucas (`MAEZO_LUCAS_FONTE_COBRANCA=amh`, decisao do dono 06/10/2026).

    executores do gateway -> adaptadores pinados (`tool_registry.build_amh_interop`) ->
    `ResolvedorDeSujeitoAmh` + `BaseLegalExecucaoDeContrato` (base legal fixa, NAO consentimento) ->
    `FonteCobrancaAmh`. Chamada SO' de dentro do `if settings.roteador_lucas_enabled` de
    `_build_lucas_turno`. FAIL-CLOSED: sem pin, sem segredo, sem URL ou sem OpenAPI, `build_amh_interop`
    LEVANTA e o receptor recusa servir — nunca cai de volta na simulada em silencio. Hoje os contratos
    nao estao publicados (XRG-2/XRG-3), entao `amh` recusa o boot por desenho.
    """
    from maezo.agents.lucas.fonte_cobranca_amh import FonteCobrancaAmh
    from maezo.agents.lucas.identidade_amh import BaseLegalExecucaoDeContrato, ResolvedorDeSujeitoAmh
    from maezo.gateway.tool_registry import build_amh_interop

    interop = build_amh_interop(
        settings=settings, seam=seam_context, audit=audit, agent_version=_LUCAS_AGENT_VERSION
    )
    fonte = FonteCobrancaAmh(
        billing=interop.billing,
        resolvedor=ResolvedorDeSujeitoAmh(
            port=interop.subjects,
            amh_tenant=interop.amh_tenant,
            hash_scheme=interop.hash_scheme,
            purpose_of_use=interop.purpose_of_use,
        ),
        consentimento=BaseLegalExecucaoDeContrato(),
        purpose_of_use=interop.purpose_of_use,
    )
    logger.warning(
        "lucas_fonte_cobranca_amh_construida",
        tenant_id=settings.tenant_id,
        amh_tenant=interop.amh_tenant,
        purpose_of_use=interop.purpose_of_use,
        base_legal="execucao-de-contrato",
    )
    return fonte, interop


def _build_helena_identidade(
    settings: WhatsAppWebhookSettings, dispatcher: HelenaDispatcher
) -> IdentidadeHelena | None:
    """A identidade (DL-0077). Ver `_build_helena_amh`, que tambem monta as consultas do plano."""
    return _build_helena_amh(settings, dispatcher)[0]


def _build_helena_amh(
    settings: WhatsAppWebhookSettings, dispatcher: HelenaDispatcher
) -> tuple[IdentidadeHelena | None, ConsultasPlanoAmh | None]:
    """DL-0077: a identidade do beneficiario na Helena, SO' com `MAEZO_HELENA_IDENTIDADE_AMH` ligada.

    Desligado devolve `None` sem importar nada da AMH. Ligado, REUSA a composicao da #662
    (`tool_registry.build_amh_interop`: executores do gateway -> adaptadores pinados) sob o principal
    `helena` (o `SeamContext` e a trilha de auditoria sao os do proprio despachante, nunca os do Lucas) e
    o MESMO `ResolvedorDeSujeitoAmh` + base legal fixa de execucao de contrato. FAIL-CLOSED: sem pin,
    segredo, URL, OpenAPI ou seam de Helena, LEVANTA e `_bring_up_dependencies` recusa servir — nunca
    sobe "sem identidade" em silencio quando o dono a ligou. Hoje os contratos nao estao publicados
    (XRG-2/XRG-3), entao ligar esta flag recusa o boot por desenho.

    FATOS DO PLANO (DL de 07/10/2026): com `helena_consultas_amh` ligada, a MESMA composicao inclui as
    leituras TINA (`incluir_tina=True`) e devolve tambem `ConsultasPlanoAmh`. Consultas sem
    identidade e' recusado (o settings ja' recusa; aqui de novo, defesa em profundidade); sem o pin
    v1.2, o OpenAPI TINA ou o escopo `interop/tina.read`, `build_amh_interop` LEVANTA e o receptor
    recusa servir.
    """
    consultas_ligadas = settings.helena_consultas_amh is True
    if not settings.helena_identidade_amh:
        if consultas_ligadas:
            raise ValueError("helena_consultas: exige MAEZO_HELENA_IDENTIDADE_AMH ligada")
        return None, None
    from maezo.agents.lucas.identidade_amh import BaseLegalExecucaoDeContrato, ResolvedorDeSujeitoAmh
    from maezo.gateway.tool_registry import build_amh_interop
    from maezo.platform.webhooks.whatsapp.helena_consultas import ConsultasPlanoAmh
    from maezo.platform.webhooks.whatsapp.helena_identidade import IdentidadeHelena

    if dispatcher.seam_context is None or dispatcher.seam_context.principal != "helena":
        raise ValueError("helena_identidade: o despachante nao tem o SeamContext do principal helena")
    interop = build_amh_interop(
        settings=settings,
        seam=dispatcher.seam_context,
        audit=dispatcher.audit_sink,
        agent_version=_HELENA_AGENT_VERSION,
        incluir_tina=consultas_ligadas,
    )
    consultas: ConsultasPlanoAmh | None = None
    if consultas_ligadas:
        if interop.tina is None:
            raise ValueError("helena_consultas: a composicao AMH nao trouxe o adaptador TINA")
        # A drenagem dos executores e' UMA so' (a da identidade): `aclose_fn` fica vazio aqui.
        consultas = ConsultasPlanoAmh(
            port=interop.tina,
            consentimento=BaseLegalExecucaoDeContrato(),
            purpose_of_use=interop.purpose_of_use,
        )
    identidade = IdentidadeHelena(
        port=interop.subjects,
        resolvedor=ResolvedorDeSujeitoAmh(
            port=interop.subjects,
            amh_tenant=interop.amh_tenant,
            hash_scheme=interop.hash_scheme,
            purpose_of_use=interop.purpose_of_use,
        ),
        consentimento=BaseLegalExecucaoDeContrato(),
        purpose_of_use=interop.purpose_of_use,
        hash_telefone=interop.phone_hasher,
        aclose_fn=interop.aclose,
    )
    logger.warning(
        "helena_identidade_amh_construida",
        tenant_id=settings.tenant_id,
        amh_tenant=interop.amh_tenant,
        purpose_of_use=interop.purpose_of_use,
        base_legal="execucao-de-contrato",
        consultas_plano=consultas is not None,
    )
    return identidade, consultas


async def _provision_dispatch_checkpointer(state: WebhookState) -> None:
    """T4b: attach the durable LangGraph checkpointer to the just-built dispatcher, with F2
    fail-closed discipline (shared `runtime.checkpoint.provision_checkpointer` — the SAME policy
    the agent-runtime daemon's readiness gate uses, so the two can never drift).

    Fail-closed SHAPE for a webhook (decided from how this receiver already handles a mandatory
    dep): a production receiver that cannot durably checkpoint must REFUSE TO SERVE — so on a
    prod-mode provision failure the just-built dispatcher is DROPPED (`state.dispatcher = None`),
    exactly like a missing audit sink / DSN already does, and `/webhook` returns its explicit 501
    rather than silently running Helena stateless (no resume-after-restart). In local/dev the
    provision falls back to an in-memory saver (loud warning) and the dispatcher is kept.

    Note: this receiver's DSN is ALREADY mandatory for the dispatcher to build at all (the T-C2
    audit sink), so the checkpointer's "DSN absent" branch is unreachable from here — the live
    fail-closed axis for the webhook is a connect/`setup()` FAILURE (or timeout), not a missing
    DSN (a missing DSN refuses even earlier, in `_build_dispatcher`)."""
    if state.dispatcher is None:
        return  # dispatcher never built (e.g. missing DSN) — nothing to checkpoint; already 501.
    settings = state.settings
    is_production = settings.runtime_mode != _LOCAL_RUNTIME_MODE
    provision = await provision_checkpointer(
        database_url=settings.database_url,
        is_production=is_production,
        component="webhook",
        setup_timeout_s=settings.dep_connect_timeout_s,
    )
    if not provision.ready:
        # FAIL-CLOSED (production): refuse to serve rather than run stateless. Drop the dispatcher
        # so `/webhook` returns the explicit 501 (same refuse-to-serve shape as a missing dep).
        state.dispatcher = None
        state.dispatcher_error = (
            f"durable checkpointer unavailable and runtime_mode={settings.runtime_mode!r} is "
            f"production — refusing to serve Helena stateless (T4b/F4 fail-closed): {provision.error}"
        )
        logger.error(
            "webhook_dispatcher_refused_no_durable_checkpointer",
            tenant=settings.tenant_id,
            runtime_mode=settings.runtime_mode,
            reason=provision.error,
        )
        return
    state.dispatcher.checkpointer = provision.checkpointer
    state.checkpointer = provision.checkpointer
    state.checkpointer_backend = provision.backend
    logger.info(
        "webhook_dispatch_checkpointer_ready",
        tenant=settings.tenant_id,
        backend=provision.backend,
    )


async def _bring_up_dependencies(state: WebhookState) -> None:
    """STEP A: bounded, non-fatal. Pure dispatcher CONSTRUCTION (no I/O) followed by the ONE
    bounded bring-up I/O this receiver now performs — the durable checkpointer's connect+setup()
    (T4b), bounded by `dep_connect_timeout_s`. Any failure leaves `state.dispatcher` `None` —
    `/webhook` degrades to the explicit 501 for a message needing dispatch (never a fabricated
    dispatch, never a silently-stateless prod dispatch)."""
    try:
        state.dispatcher, state.cibseven_transport = _build_dispatcher(state.settings)
        logger.info("webhook_dispatcher_ready", tenant=state.settings.tenant_id)
    except Exception as exc:  # isolated: liveness/readiness must stay up.
        state.dispatcher_error = f"{type(exc).__name__}: {exc}"
        logger.error("webhook_dispatcher_build_failed", exc_info=True)
        return

    # ONDA 1 §5.5's boot assertion, on THE LIVE AGENT PATH. This receiver has no `/readyz` gate for
    # dispatch (module docstring STEP C: an unconfigured dispatcher degrades `/webhook` to 501, it
    # does not colour readiness), so the assertion is enforced the way this root already enforces
    # every other mandatory dep: an ungated seam DROPS the dispatcher and `/webhook` answers its
    # explicit 501. Refuse-to-serve, never serve-ungated — the runtime half of I-11 on the one path
    # that actually runs traffic.
    gated, detail = effect_seams_gated(
        {
            "dmn": state.dispatcher.dmn,
            "cibseven": state.dispatcher.cibseven,
            "inference": state.dispatcher.inference,
        }
    )
    if not gated:
        state.dispatcher = None
        state.dispatcher_error = f"effect seams not gated — refusing to serve: {detail}"
        logger.error(
            "webhook_dispatcher_refused_ungated_effect_seams",
            tenant=state.settings.tenant_id,
            detail=detail,
        )
        return
    logger.info("webhook_effect_seams_gated", tenant=state.settings.tenant_id, detail=detail)

    # NUMERO UNICO, onda (d): o turno do Lucas, so' com o roteador ligado, sob a MESMA assercao de
    # boot. Ligado e quebrado = recusa servir (o mesmo formato de um lexico invalido).
    try:
        state.lucas_turno = _build_lucas_turno(state.settings, state.dispatcher)
    except Exception as exc:  # isolated: liveness/readiness must stay up.
        state.dispatcher = None
        state.dispatcher_error = f"lucas turn build failed — refusing to serve: {type(exc).__name__}: {exc}"
        logger.error("webhook_lucas_turno_build_failed", exc_info=True)
        return
    if state.lucas_turno is not None:
        gated, detail = effect_seams_gated(
            {
                "dmn": state.lucas_turno.dmn,
                "cibseven": state.lucas_turno.cibseven,
                "inference": state.lucas_turno.inference,
            }
        )
        if not gated:
            state.dispatcher = None
            state.lucas_turno = None
            state.dispatcher_error = f"lucas effect seams not gated — refusing to serve: {detail}"
            logger.error(
                "webhook_dispatcher_refused_ungated_lucas_seams",
                tenant=state.settings.tenant_id,
                detail=detail,
            )
            return
        logger.info("webhook_lucas_seams_gated", tenant=state.settings.tenant_id, detail=detail)
        # Onda (e): so' agora, com as costuras do Lucas conferidas, o despachante ganha o turno do
        # Lucas — e com ele a passagem de cobranca (`HelenaDispatcher._roteamento_completo`).
        state.dispatcher.lucas_turno = state.lucas_turno

    # DL-0077: identidade do beneficiario na Helena, so' com a flag ligada. Ligada e quebrada = recusa
    # servir (o mesmo formato do Lucas AMH); desligada, `None` e o despachante nao muda.
    try:
        state.helena_identidade, state.helena_consultas = _build_helena_amh(state.settings, state.dispatcher)
    except Exception as exc:  # isolated: liveness/readiness must stay up.
        state.dispatcher = None
        state.dispatcher_error = (
            f"helena identity build failed — refusing to serve: {type(exc).__name__}: {exc}"
        )
        logger.error("webhook_helena_identidade_build_failed", exc_info=True)
        return
    if state.helena_identidade is not None:
        state.dispatcher.identidade = state.helena_identidade
        # DL-0078: o nome de exibicao da operadora nos textos fixos de identidade da Helena.
        state.dispatcher.identidade_nome_operadora = state.settings.helena_identidade_nome_operadora
    if state.helena_consultas is not None:
        # DL de 07/10/2026: os fatos do plano na Helena (so' com a identidade, conferido acima).
        state.dispatcher.consultas_plano = state.helena_consultas

    # T4b: wire durable multi-turn persistence into the dispatcher, fail-closed in production.
    # Isolated exactly like the construction above — a failure here must not crash bring-up.
    try:
        await _provision_dispatch_checkpointer(state)
    except Exception as exc:  # isolated; never propagate (liveness stays up).
        state.dispatcher = None
        state.dispatcher_error = f"checkpointer provisioning error: {type(exc).__name__}: {exc}"
        logger.error("webhook_dispatch_checkpointer_provision_failed", exc_info=True)


async def run(settings: WhatsAppWebhookSettings) -> None:
    """Run the webhook-receiver until SIGTERM/SIGINT. See module docstring for STEP A..D."""
    logger.info("webhook_receiver_starting", tenant=settings.tenant_id, health_port=settings.health_port)

    state = WebhookState(settings=settings)
    # STEP A: bring up dispatch dependencies — pure construction + the ONE bounded I/O (the T4b
    # durable checkpointer connect+setup, `dep_connect_timeout_s`-bounded; module docstring).
    await _bring_up_dependencies(state)

    # STEP B: bind the app (health + /webhook) with the STEP A result baked in. The dedup guard
    # rides on the dispatcher (`_build_dispatcher` builds both or neither), so a replica that has
    # a dispatcher NEVER serves `/webhook` without duplicate protection.
    dedup = state.dispatcher.dedup if state.dispatcher is not None else None
    app = create_app(settings, is_live=state.is_live, dispatcher=state.dispatcher, dedup=dedup)
    server = build_health_server(app, port=settings.health_port)
    server.capture_signals = contextlib.nullcontext  # type: ignore[assignment]  # we own the signals
    shutdown = asyncio.Event()
    loop = asyncio.get_running_loop()
    serve_task = asyncio.create_task(server.serve(), name="webhook-server")

    def _request_shutdown(sig: signal.Signals) -> None:
        logger.info("shutdown_signal", signal=sig.name)
        state.live = False
        server.should_exit = True
        shutdown.set()

    def _on_serve_done(task: asyncio.Task[None]) -> None:
        state.live = False
        if not shutdown.is_set():
            exc = None if task.cancelled() else task.exception()
            logger.error("health_server_stopped_early", error=repr(exc) if exc else "no_exception")
            shutdown.set()

    serve_task.add_done_callback(_on_serve_done)
    for _sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(_sig, _request_shutdown, _sig)

    logger.info("webhook_receiver_running")
    await shutdown.wait()

    server.should_exit = True
    with contextlib.suppress(asyncio.CancelledError, SystemExit):
        await serve_task

    if state.cibseven_transport is not None:
        with contextlib.suppress(Exception):
            await state.cibseven_transport.close()
    if state.lucas_turno is not None:
        with contextlib.suppress(Exception):
            await state.lucas_turno.aclose()
    if state.helena_identidade is not None:
        with contextlib.suppress(Exception):
            await state.helena_identidade.aclose()

    # T4b: release the checkpointer's connection pool (idempotent; no-op for the in-memory
    # fallback / when never provisioned). Non-fatal — a close failure must not mask shutdown.
    if state.checkpointer is not None:
        with contextlib.suppress(Exception):
            await state.checkpointer.aclose()

    # Gap `WEBHOOK-WAMID-DEDUP`: release the dedup registry's pool the same way (lazy — a receiver
    # that never claimed anything never opened it). Non-fatal, for the same reason.
    if dedup is not None:
        with contextlib.suppress(Exception):
            await dedup.aclose()

    logger.info("webhook_receiver_stopped", tenant=settings.tenant_id)


def _attach_administrative_runtime(
    state: WebhookState,
    *,
    binding: JourneyBinding,
    driver: JourneyDriver,
    currentness: JourneyCurrentnessPort | None = None,
    enabled: bool = False,
) -> AdministrativeJourneyRuntime | None:
    """Explicit trusted composition after saver provisioning; no public route activation."""
    from maezo.gateway.tool_registry import build_administrative_journey_runtime

    if state.dispatcher is None or state.checkpointer is None:
        return None
    if binding.tenant_ref != state.settings.tenant_id or binding.tenant_ref != state.dispatcher.tenant_id:
        return None
    runtime = build_administrative_journey_runtime(
        binding=binding,
        driver=driver,
        currentness=currentness,
        checkpointer=state.checkpointer.saver,
        pseudonymizer=state.dispatcher.pseudonymizer,
        enabled=enabled,
    )
    state.administrative_runtime = runtime
    state.dispatcher.administrative_runtime = runtime
    state.dispatcher.administrative_binding = binding.model_copy(deep=True)
    return runtime
