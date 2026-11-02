"""Uma rodada da limpeza da custodia do telefone. Ver o docstring do pacote.

  MAEZO_CUSTODIA_LIMPEZA   false  interruptor. Desligado: loga `modo=desligado` e sai com 0.
  TENANT_ID                amh    schema do tenant (o cofre e' por tenant).
  DATABASE_URL             -      obrigatorio com o interruptor ligado.

Saida: 0 = desligado ou rodada concluida; 1 = nao montou ou a exclusao falhou. O log carrega so'
contagem e tokens de classe — nunca conversation_id, telefone ou texto de excecao.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Final, Protocol

import structlog
from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from maezo.gateway.recipient_custody import PostgresRecipientVault, RecipientCustodyError

logger = structlog.get_logger(__name__)

EVENTO: Final[str] = "custodia_limpeza_resumo"


class LimpezaCustodiaSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    habilitado: bool = Field(default=False, validation_alias=AliasChoices("MAEZO_CUSTODIA_LIMPEZA"))
    tenant_id: str = Field(default="amh", validation_alias=AliasChoices("TENANT_ID"))
    database_url: str | None = Field(default=None, validation_alias=AliasChoices("DATABASE_URL"), repr=False)


class ComposicaoDaLimpezaError(RuntimeError):
    """A limpeza nao pode montar nesta configuracao. A mensagem e' um token, nunca dado."""


class SemChaveKeyWrapper:
    """`KeyWrapper` da limpeza: nao tem chave e RECUSA cifrar e decifrar.

    `PostgresRecipientVault` exige um wrapper, mas `purge_expired` nao usa nenhum. Em vez de dar a
    esta task uma role com KMS que ela nao precisa, o cofre e' montado com um wrapper que falha
    fechado: se algum dia esta task tentar abrir ou gravar um telefone, levanta.
    """

    key_ref: str = "limpeza-sem-chave"

    async def wrap(self, data_key: bytes, context: dict[str, str]) -> bytes:
        raise RecipientCustodyError("recipient_custody: a limpeza nao cifra")

    async def unwrap(self, wrapped: bytes, context: dict[str, str]) -> bytes:
        raise RecipientCustodyError("recipient_custody: a limpeza nao decifra")


class CofrePurgavel(Protocol):
    async def purge_expired(self) -> int: ...

    async def aclose(self) -> None: ...


def montar_cofre(settings: LimpezaCustodiaSettings) -> PostgresRecipientVault:
    if not settings.database_url:
        raise ComposicaoDaLimpezaError("custodia_limpeza: DATABASE_URL ausente")
    return PostgresRecipientVault(
        dsn=settings.database_url, tenant=settings.tenant_id, wrapper=SemChaveKeyWrapper()
    )


async def executar(cofre: CofrePurgavel) -> int:
    """Apaga as linhas vencidas e devolve quantas. Fecha o cofre mesmo se a exclusao falhar."""
    try:
        return await cofre.purge_expired()
    finally:
        await cofre.aclose()


async def rodar(
    settings: LimpezaCustodiaSettings,
    *,
    montar: Callable[[LimpezaCustodiaSettings], CofrePurgavel] = montar_cofre,
) -> int:
    """Uma rodada. Devolve o codigo de saida do processo."""
    if not settings.habilitado:
        logger.info(EVENTO, modo="desligado")
        return 0
    try:
        cofre = montar(settings)
    except (ComposicaoDaLimpezaError, ValueError) as exc:
        logger.error(EVENTO, modo="nao_montou", erro=type(exc).__name__)
        return 1
    try:
        apagadas = await executar(cofre)
    except Exception as exc:  # o job agendado registra a falha e sai 1; o proximo disparo tenta de novo
        logger.error(EVENTO, modo="falhou", erro=type(exc).__name__)
        return 1
    logger.info(EVENTO, modo="executado", apagadas=apagadas)
    return 0
