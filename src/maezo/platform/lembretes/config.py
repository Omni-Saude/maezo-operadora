"""Configuracao do lembrete proativo de cobranca. TUDO desligado/seguro por padrao.

  MAEZO_LEMBRETE_COBRANCA                  false  interruptor geral. Desligado: o job nao monta nada e sai.
  MAEZO_LEMBRETE_DRY_RUN                   true   so' calcula e loga CONTAGENS; nao envia, nao grava.
  MAEZO_LEMBRETE_DIAS_ANTECEDENCIA         3      lembra quando o vencimento e' em ate' N dias (1..15).
  MAEZO_LEMBRETE_ALLOWLIST_CONVERSAS       -      conversation_ids (`wa:amh:hk1_...`), separados por virgula.
  MAEZO_LEMBRETE_ALLOWLIST_TELEFONES       -      telefones; viram conversation_id no boot (nunca logados).
  MAEZO_LEMBRETE_EXIGIR_ALLOWLIST          true   envio REAL sem allowlist e' recusado (dev). Producao
                                                  desliga de proposito, em ato proprio do dono.
  MAEZO_LEMBRETE_TEMPLATE_NAME             ""     template aprovado na Meta (fora da janela de 24 h).
  MAEZO_LEMBRETE_TEMPLATE_IDIOMA           pt_BR
  MAEZO_LEMBRETE_LIMITE_POR_EXECUCAO       50     teto GLOBAL de mensagens por execucao.
  MAEZO_LEMBRETE_PAUSA_ENTRE_ENVIOS_S      1.0    espaco entre dois envios.

Com a allowlist DEFINIDA, so' as conversas listadas recebem qualquer coisa; ver `Allowlist`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_CONVERSA: Final[re.Pattern[str]] = re.compile(r"^wa:[a-z][a-z0-9_]*:hk1_[0-9a-f]+$")


class LembreteSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    habilitado: bool = Field(default=False, validation_alias=AliasChoices("MAEZO_LEMBRETE_COBRANCA"))
    dry_run: bool = Field(default=True, validation_alias=AliasChoices("MAEZO_LEMBRETE_DRY_RUN"))
    dias_antecedencia: int = Field(
        default=3, ge=1, le=15, validation_alias=AliasChoices("MAEZO_LEMBRETE_DIAS_ANTECEDENCIA")
    )
    allowlist_conversas: str | None = Field(
        default=None, validation_alias=AliasChoices("MAEZO_LEMBRETE_ALLOWLIST_CONVERSAS")
    )
    allowlist_telefones: str | None = Field(
        default=None,
        validation_alias=AliasChoices("MAEZO_LEMBRETE_ALLOWLIST_TELEFONES"),
        repr=False,
        exclude=True,
    )
    exigir_allowlist: bool = Field(
        default=True, validation_alias=AliasChoices("MAEZO_LEMBRETE_EXIGIR_ALLOWLIST")
    )
    template_nome: str = Field(default="", validation_alias=AliasChoices("MAEZO_LEMBRETE_TEMPLATE_NAME"))
    template_idioma: str = Field(
        default="pt_BR", validation_alias=AliasChoices("MAEZO_LEMBRETE_TEMPLATE_IDIOMA")
    )
    limite_por_execucao: int = Field(
        default=50, ge=1, le=1000, validation_alias=AliasChoices("MAEZO_LEMBRETE_LIMITE_POR_EXECUCAO")
    )
    pausa_entre_envios_s: float = Field(
        default=1.0, ge=0.0, le=60.0, validation_alias=AliasChoices("MAEZO_LEMBRETE_PAUSA_ENTRE_ENVIOS_S")
    )
    tenant_id: str = Field(default="amh", validation_alias=AliasChoices("TENANT_ID"))
    database_url: str | None = Field(default=None, validation_alias=AliasChoices("DATABASE_URL"), repr=False)


def _itens(valor: str | None) -> list[str]:
    return [p.strip() for p in (valor or "").split(",") if p.strip()]


def conversas_da_lista(valor: str | None) -> frozenset[str] | None:
    """`None` = lista nao definida. Definida (mesmo com item invalido) = conjunto, e item que nao e' uma
    conversa valida e' descartado: um erro de digitacao so' REDUZ quem recebe, nunca amplia."""
    itens = _itens(valor)
    if not itens:
        return None
    return frozenset(i for i in itens if _CONVERSA.match(i))


def telefones_da_lista(valor: str | None) -> list[str]:
    return [re.sub(r"\D", "", t) for t in _itens(valor) if re.sub(r"\D", "", t)]


@dataclass(frozen=True, slots=True)
class Allowlist:
    """O conjunto de conversas que PODEM receber. `None` dentro de `ConfigJob` = sem allowlist."""

    conversas: frozenset[str]

    def permite(self, conversation_id: str) -> bool:
        return conversation_id in self.conversas

    def __repr__(self) -> str:
        return f"Allowlist(n={len(self.conversas)})"


@dataclass(frozen=True, slots=True)
class ConfigJob:
    """A configuracao EFETIVA que o job usa (pura; testes montam direto)."""

    habilitado: bool = False
    dry_run: bool = True
    dias_antecedencia: int = 3
    allowlist: Allowlist | None = None
    exigir_allowlist: bool = True
    template_nome: str = ""
    template_idioma: str = "pt_BR"
    limite_por_execucao: int = 50
    pausa_entre_envios_s: float = 0.0
