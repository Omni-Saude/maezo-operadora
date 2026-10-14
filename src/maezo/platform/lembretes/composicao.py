"""Raiz de composicao do job do lembrete (`python -m maezo.platform.lembretes`).

Reusa, sem reescrever, o que o receptor do WhatsApp ja' monta: o mesmo despachante (cliente de envio com dedup
duravel, pseudonimizador, guard de dedup), a MESMA fonte de cobranca do Lucas (`FonteCobrancaAmh` sobre a
referencia verificada) e o gate do principal `lucas`. Por isso a task herda o ambiente e os segredos do
receptor (`service-webhook-receiver.tf`), mais as variaveis `MAEZO_LEMBRETE_*`.

So' e' chamada com o interruptor LIGADO: desligado, `__main__` sai antes de montar qualquer coisa.
Exercida por suas partes (`tests/unit/platform/lembretes/`); a montagem inteira e' coberta pelo teste em dev.
"""

from __future__ import annotations

from collections.abc import Callable

from maezo.gateway.audit_postgres import schema_for_tenant
from maezo.gateway.recipient_custody import AwsKmsKeyWrapper, PostgresRecipientVault
from maezo.gateway.seams import GatedSeam
from maezo.gateway.seams.whatsapp import gate_whatsapp
from maezo.gateway.tool_registry import build_agent_seam_context, build_agent_seams
from maezo.gateway.vendor_suppression import PostgresSuppressionStore
from maezo.platform.webhooks.service import _build_dispatcher, _build_fonte_cobranca_amh
from maezo.platform.webhooks.whatsapp.acesso_ponte import RefsVerificadas
from maezo.platform.webhooks.whatsapp.security import hash_phone
from maezo.platform.webhooks.whatsapp.settings import WhatsAppWebhookSettings

from .armazenamento import PostgresLembreteStore
from .config import Allowlist, ConfigJob, LembreteSettings, conversas_da_lista, telefones_da_lista
from .envio import Enviador
from .job import Dependencias
from .leitura import LeitorDeFatosDoLucas, LeitorDeSupressaoDoStore, PostgresLeitorDeAcesso


class ComposicaoDoLembreteError(RuntimeError):
    """O job nao pode subir nesta configuracao. A mensagem e' um token, nunca dado."""


def configurar(
    settings: LembreteSettings, *, hash_do_telefone: Callable[[str], str] | None = None
) -> ConfigJob:
    """`LembreteSettings` -> `ConfigJob`. Os telefones da allowlist viram conversation_id (HMAC keyed)."""
    conversas = conversas_da_lista(settings.allowlist_conversas)
    telefones = telefones_da_lista(settings.allowlist_telefones)
    allowlist: Allowlist | None = None
    if conversas is not None or telefones:
        todos = set(conversas or ())
        if telefones:
            if hash_do_telefone is None:
                raise ComposicaoDoLembreteError("lembrete: allowlist de telefones sem pseudonimizador")
            todos.update(f"wa:{settings.tenant_id}:{hash_do_telefone(t)}" for t in telefones)
        allowlist = Allowlist(frozenset(todos))
    return ConfigJob(
        habilitado=settings.habilitado,
        dry_run=settings.dry_run,
        dias_antecedencia=settings.dias_antecedencia,
        allowlist=allowlist,
        exigir_allowlist=settings.exigir_allowlist,
        template_nome=settings.template_nome,
        template_idioma=settings.template_idioma,
        limite_por_execucao=settings.limite_por_execucao,
        pausa_entre_envios_s=settings.pausa_entre_envios_s,
    )


def montar_store(settings: LembreteSettings) -> PostgresLembreteStore:
    if not settings.database_url:
        raise ComposicaoDoLembreteError("lembrete: DATABASE_URL ausente")
    return PostgresLembreteStore(dsn=settings.database_url, tenant=settings.tenant_id)


async def montar(settings: LembreteSettings) -> tuple[Dependencias, ConfigJob]:  # pragma: no cover
    """Monta tudo para a rodada. Qualquer pre-condicao ausente RECUSA (nunca degrada para outra fonte)."""
    if not settings.database_url:
        raise ComposicaoDoLembreteError("lembrete: DATABASE_URL ausente")
    web = WhatsAppWebhookSettings()  # type: ignore[call-arg]  # campos obrigatorios vem do ambiente
    if not (web.acesso_beneficiario and web.lucas_fonte_cobranca == "amh" and web.lucas_consulta_valores):
        raise ComposicaoDoLembreteError(
            "lembrete: exige MAEZO_ACESSO_BENEFICIARIO, MAEZO_LUCAS_FONTE_COBRANCA=amh e "
            "MAEZO_LUCAS_CONSULTA_VALORES ligados (valor so' para beneficiario verificado)"
        )
    if not web.recipient_vault_kms_key_arn:
        raise ComposicaoDoLembreteError(
            "lembrete: custodia do telefone (RECIPIENT_VAULT_KMS_KEY_ARN) ausente"
        )
    dispatcher, _transport = _build_dispatcher(web)
    dedup = dispatcher.dedup
    if dedup is None:
        raise ComposicaoDoLembreteError("lembrete: sem guard de dedup nao ha' chave de saida")
    tenant = settings.tenant_id
    prefixo = f"wa:{tenant}:"
    pseudonymizer = dispatcher.pseudonymizer
    cfg = configurar(settings, hash_do_telefone=lambda t: hash_phone(t, tenant, pseudonymizer))

    refs = RefsVerificadas()
    seam_ctx = build_agent_seam_context(tenant=tenant, agent_id="lucas")
    inferencia = dispatcher.inference
    if not isinstance(inferencia, GatedSeam):
        raise ComposicaoDoLembreteError("lembrete: o provedor da Helena nao e' um seam cercado")
    seams = build_agent_seams(settings=web, agent_id="lucas", inference=inferencia.inner)
    fonte, _interop = _build_fonte_cobranca_amh(
        web, seam_context=seam_ctx, audit=seams.get("audit_sink"), refs=refs
    )

    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(
        settings.database_url, connect_args={"server_settings": {"search_path": schema_for_tenant(tenant)}}
    )
    cofre = PostgresRecipientVault(
        dsn=settings.database_url,
        tenant=tenant,
        wrapper=AwsKmsKeyWrapper(key_arn=web.recipient_vault_kms_key_arn),
        ttl_days=web.recipient_vault_ttl_days,
    )
    deps = Dependencias(
        tenant_id=tenant,
        acesso=PostgresLeitorDeAcesso(dsn=settings.database_url, tenant=tenant),
        fatos=LeitorDeFatosDoLucas(
            fonte=fonte,
            refs=refs,
            pseudo_id_de=lambda conv: str(
                pseudonymizer.pseudonymize({"telefone": conv[len(prefixo) :]})["telefone"]
            ),
        ),
        store=montar_store(settings),
        supressao=LeitorDeSupressaoDoStore(store=PostgresSuppressionStore(engine), tenant=tenant),
        contatos=cofre,
        enviador=Enviador(
            tenant_id=tenant,
            client=dispatcher.whatsapp_client,
            gate=lambda inner: gate_whatsapp(inner, seam_ctx),
            allowlist=cfg.allowlist,
            template_nome=cfg.template_nome,
            template_idioma=cfg.template_idioma,
        ),
        chave_de_saida=lambda identificador: dedup.outbound_key(identificador, occurrence=1),
    )
    return deps, cfg
