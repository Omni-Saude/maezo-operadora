"""As settings do SERVICO do agente de entrada remoto (ADR-0067 fase 4b, revisao de seguranca).

Sao as do receptor (`WhatsAppWebhookSettings`: a composicao das dependencias e' a MESMA) MENOS os segredos que
so' o receptor deve ter. `WHATSAPP_APP_SECRET` e `WHATSAPP_VERIFY_TOKEN` assinam/validam o `/webhook` publico:
com eles, quem comprometesse o agente forjaria webhook valido. Aqui os dois NAO sao exigidos e, mesmo que
vazem no ambiente, sao DESCARTADOS na validacao (o agente nunca os guarda). A chave de assinatura A2A
(`MAEZO_A2A_CARD_SIGNING_KEY__*`) nem e' campo destas settings: o agente remoto nao publica Card.

O receptor nao muda: `WhatsAppWebhookSettings` continua exigindo os dois no boot (`min_length=1`).
"""

from __future__ import annotations

from pydantic import AliasChoices, Field, model_validator

from maezo.platform.webhooks.whatsapp.settings import WhatsAppWebhookSettings


class AgenteRemotoSettings(WhatsAppWebhookSettings):
    """`WhatsAppWebhookSettings` sem os segredos do webhook (sempre vazios aqui)."""

    app_secret: str = Field(
        default="",
        validation_alias=AliasChoices("WHATSAPP_APP_SECRET"),
        repr=False,
        exclude=True,
    )
    verify_token: str = Field(
        default="",
        validation_alias=AliasChoices("WHATSAPP_VERIFY_TOKEN"),
        repr=False,
        exclude=True,
    )

    @model_validator(mode="after")
    def _descartar_segredos_do_webhook(self) -> AgenteRemotoSettings:
        # Falha fechada no sentido certo: nem um segredo vazado no ambiente fica na memoria do agente.
        self.app_secret = ""
        self.verify_token = ""
        return self


__all__ = ["AgenteRemotoSettings"]
