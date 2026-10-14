"""O envio do lembrete: o UNICO ponto por onde uma mensagem do lembrete sai.

Tres travas, todas AQUI (nao so' na selecao):
  1. ALLOWLIST: com a lista definida, uma conversa fora dela e' recusada (`DestinoForaDaAllowlistError`)
     antes de qualquer chamada. Independe de quem chamou e de como o candidato foi escolhido.
  2. O destino cru so' existe dentro do remetente por envio, que recusa se `to_hash` nao e' o da conversa
     (mesma regra do `_ScopedWhatsAppSender` do despachante).
  3. O remetente passa pelo GATE do principal `lucas` (`gate_whatsapp`): decisao de efeito e auditoria como
     qualquer outra mensagem ao beneficiario. O gate e' injetado pela raiz de composicao (nunca opcional).

Texto livre dentro da janela de 24 h; fora dela, o TEMPLATE aprovado na Meta, com as mesmas tres variaveis.
Numero, texto e variaveis nunca sao logados.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from .config import Allowlist
from .texto import MensagemLembrete

CANAL_TEXTO = "texto"
CANAL_TEMPLATE = "template"


class DestinoForaDaAllowlistError(PermissionError):
    """A conversa nao esta' na allowlist definida: nada e' enviado."""


class ClienteWhatsApp(Protocol):
    async def send_message(
        self, to: str, text: str, *, idempotency_key: str | None = None
    ) -> dict[str, Any]: ...

    async def send_template(
        self,
        to: str,
        template_name: str,
        parameters: list[str],
        *,
        language: str = "pt_BR",
        idempotency_key: str | None = None,
    ) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class ResultadoEnvio:
    canal: str
    entregue: bool


@dataclass(frozen=True, slots=True)
class _Envio:
    texto: str
    parametros: tuple[str, str, str]
    template_nome: str | None
    template_idioma: str


class _RemetentePorEnvio:
    """O `WhatsAppSender` que o gate embrulha: um destino, um envio."""

    def __init__(self, *, client: ClienteWhatsApp, raw_to: str, expected_hash: str, envio: _Envio) -> None:
        self._client = client
        self._raw_to = raw_to
        self._expected_hash = expected_hash
        self._envio = envio

    async def send(self, to_hash: str, text: str, *, idempotency_key: str | None = None) -> dict[str, Any]:
        if to_hash != self._expected_hash:
            raise ValueError("lembrete: o destino nao bate com a conversa — recusando enviar")
        e = self._envio
        if e.template_nome is not None:
            return await self._client.send_template(
                self._raw_to,
                e.template_nome,
                list(e.parametros),
                language=e.template_idioma,
                idempotency_key=idempotency_key,
            )
        return await self._client.send_message(self._raw_to, text, idempotency_key=idempotency_key)


class Enviador:
    def __init__(
        self,
        *,
        tenant_id: str,
        client: ClienteWhatsApp,
        gate: Callable[[Any], Any],
        allowlist: Allowlist | None,
        template_nome: str = "",
        template_idioma: str = "pt_BR",
    ) -> None:
        self._prefixo = f"wa:{tenant_id}:"
        self._client = client
        self._gate = gate
        self._allowlist = allowlist
        self._template_nome = template_nome.strip()
        self._template_idioma = template_idioma

    @property
    def tem_template(self) -> bool:
        return bool(self._template_nome)

    async def enviar(
        self,
        *,
        conversation_id: str,
        raw_to: str,
        mensagem: MensagemLembrete,
        via_template: bool,
        chave: str,
    ) -> ResultadoEnvio:
        """Envia UMA mensagem. `entregue=False` quando o cliente suprimiu (duplicata ou faixa sintetica)."""
        if self._allowlist is not None and not self._allowlist.permite(conversation_id):
            raise DestinoForaDaAllowlistError("lembrete: conversa fora da allowlist")
        if not conversation_id.startswith(self._prefixo):
            raise ValueError("lembrete: conversa de outro tenant")
        if via_template and not self._template_nome:
            raise ValueError("lembrete: template nao configurado")
        phone_hash = conversation_id[len(self._prefixo) :]
        envio = _Envio(
            texto=mensagem.texto,
            parametros=mensagem.parametros_template,
            template_nome=self._template_nome if via_template else None,
            template_idioma=self._template_idioma,
        )
        remetente = self._gate(
            _RemetentePorEnvio(client=self._client, raw_to=raw_to, expected_hash=phone_hash, envio=envio)
        )
        ack = await remetente.send(phone_hash, mensagem.texto, idempotency_key=chave)
        suprimido = bool(ack.get("suppressed_duplicate") or ack.get("suppressed_synthetic"))
        return ResultadoEnvio(canal=CANAL_TEMPLATE if via_template else CANAL_TEXTO, entregue=not suprimido)
