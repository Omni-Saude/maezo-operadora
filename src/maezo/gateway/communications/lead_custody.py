"""Custodia cifrada do telefone de LEAD, com finalidade (E-SF-13 / GTM-E-43). Irma de `recipient_custody.py`.

O mesmo envelope da custodia do beneficiario (`recipient_custody.seal_with_context`/`open_with_context`:
chave de dados AES-256 por linha, GCM, chave embrulhada pelo KMS), com outra amarracao: o AAD do GCM e o
encryption context do KMS sao `(tenant, prospect_ref, finalidade)`. Consequencias, todas testadas:

* uma linha copiada para outro `prospect_ref` NAO abre (`test_lead_custody_refuses_cross_prospect_decrypt`);
* a mesma linha pedida para outra FINALIDADE nao abre: o telefone guardado para uma finalidade nao serve
  a outra;
* linha vencida (`expires_at <= agora`) nunca e' aberta — nem chega ao KMS — e o gate ve' isso como
  `RECIPIENT_UNRESOLVED` (`test_lead_custody_refuses_after_expiry`).

SEM ADMISSAO A6, O BOOT RECUSA. O cofre so' e' construido com um `SuppressionAdmission` validado
(`gateway/commercial_suppression_admission.py`, ato do dono + DPO). A raiz de composicao obtem esse valor
SO' por `read_suppression_admission(caminho, now=...)` (que relê e valida o registro a cada chamada);
nenhum codigo de `src/` constroi `SuppressionAdmission(...)` a mao fora daquele modulo — o teste AST
`test_ninguem_constroi_suppression_admission_a_mao` fixa isso. Sem ele, `PostgresLeadContactVault`
levanta `LeadCustodyError("lead_custody_admission_missing")` no construtor: guardar telefone de lead para
contato sem oposicao executavel seria o envio proativo sem a lista de nao-contato. Nenhum registro A6 e'
publicado neste repositorio (`commercial_suppression_admission.py`), entao hoje nenhuma raiz constroi este
cofre e todo lead fica `RECIPIENT_UNRESOLVED` (variante fallback do plano).

A TABELA `lead_contato_custodia` AINDA NAO EXISTE NA CADEIA ALEMBIC. A migracao proposta (0036, molde
`0016_recipient_custody.py`) nao entrou neste lote: toda tabela nova tem de ser enumerada no plano de
eliminacao (`platform/lifecycle/erasure_plan.py` + `spec/policies/retention/erasure-plan.template.yaml`,
artefatos do DPO, fora da posse deste epico). Sem a tabela, o cofre e' escuro em dobro. Aplicar a
migracao em producao, quando existir, e' ato do dono (OWNER-DEPLOY, CA-11); este modulo nao aplica nada.

O TELEFONE EM CLARO NUNCA E' LOGADO. Erros carregam so' tokens de classe.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Final

import asyncpg  # type: ignore[import-untyped]  # no py.typed upstream

from maezo.gateway.audit_postgres import normalize_dsn, schema_for_tenant
from maezo.gateway.commercial_suppression_admission import SuppressionAdmission
from maezo.gateway.recipient_custody import (
    KeyWrapper,
    RecipientCustodyError,
    SealedPhone,
    open_with_context,
    seal_with_context,
)

#: Rotulo fixo do AAD (separa este envelope do da retomada, mesmo com a mesma chave KMS).
ROTULO: Final[str] = "maezo-lead-contato"

#: Retencao PROPOSTA: 30 dias, como a custodia irma (ADR-0061). Sujeita ao DPO.
DEFAULT_TTL_DAYS: Final[int] = 30

#: `prospect_ref` e' OPACO: nunca o telefone, nunca so' digitos.
_PROSPECT_REF: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{7,127}$")
_SO_DIGITOS: Final[re.Pattern[str]] = re.compile(r"^\+?[0-9]+$")
_FINALIDADE: Final[re.Pattern[str]] = re.compile(r"^[a-z0-9][a-z0-9_.]{1,63}$")


class LeadCustodyError(RecipientCustodyError):
    """Falha da custodia de lead. Mensagem = token de classe, nunca numero nem blob."""


def _validar(tenant: str, prospect_ref: str, finalidade: str) -> None:
    if not tenant:
        raise LeadCustodyError("lead_custody_tenant_invalid")
    if not _PROSPECT_REF.match(prospect_ref) or _SO_DIGITOS.match(prospect_ref):
        raise LeadCustodyError("lead_custody_prospect_ref_invalid")
    if not _FINALIDADE.match(finalidade):
        raise LeadCustodyError("lead_custody_finalidade_invalid")


def encryption_context(tenant: str, prospect_ref: str, finalidade: str) -> dict[str, str]:
    return {"rotulo": ROTULO, "tenant": tenant, "prospect_ref": prospect_ref, "finalidade": finalidade}


def _aad(tenant: str, prospect_ref: str, finalidade: str) -> bytes:
    return f"{ROTULO}|{tenant}|{prospect_ref}|{finalidade}".encode()


@dataclass(frozen=True, slots=True)
class LeadContactRow:
    """Uma linha da custodia como ela sai do banco (cifrada) + o vencimento."""

    sealed: SealedPhone
    expires_at: datetime

    def __repr__(self) -> str:
        return f"LeadContactRow(<cifrado>, expires_at={self.expires_at.isoformat()})"


async def seal_lead_phone(
    phone: str,
    wrapper: KeyWrapper,
    *,
    tenant: str,
    prospect_ref: str,
    finalidade: str,
    agora: datetime,
    ttl: timedelta = timedelta(days=DEFAULT_TTL_DAYS),
) -> LeadContactRow:
    _validar(tenant, prospect_ref, finalidade)
    if agora.tzinfo is None:
        raise LeadCustodyError("lead_custody_clock_invalid")
    sealed = await seal_with_context(
        phone,
        wrapper,
        context=encryption_context(tenant, prospect_ref, finalidade),
        aad=_aad(tenant, prospect_ref, finalidade),
    )
    return LeadContactRow(sealed=sealed, expires_at=agora + ttl)


async def open_lead_phone(
    row: LeadContactRow,
    wrapper: KeyWrapper,
    *,
    tenant: str,
    prospect_ref: str,
    finalidade: str,
    agora: datetime,
) -> str | None:
    """O telefone, ou `None` se a linha venceu (nunca vai ao KMS).

    Amarracao divergente (outro prospect, finalidade ou tenant) -> `LeadCustodyError`.
    """
    _validar(tenant, prospect_ref, finalidade)
    if agora.tzinfo is None:
        raise LeadCustodyError("lead_custody_clock_invalid")
    if row.expires_at <= agora:
        return None
    try:
        return await open_with_context(
            row.sealed,
            wrapper,
            context=encryption_context(tenant, prospect_ref, finalidade),
            aad=_aad(tenant, prospect_ref, finalidade),
            erro="lead_custody: nao foi possivel abrir",
        )
    except RecipientCustodyError as exc:
        raise LeadCustodyError(str(exc)) from None


_UPSERT_SQL: Final[str] = (
    "INSERT INTO lead_contato_custodia "
    "(tenant, prospect_ref, finalidade, key_ref, wrapped_key, nonce, ciphertext, expires_at) "
    "VALUES ($1, $2, $3, $4, $5, $6, $7, $8) "
    "ON CONFLICT (tenant, prospect_ref, finalidade) DO UPDATE SET "
    "key_ref = EXCLUDED.key_ref, wrapped_key = EXCLUDED.wrapped_key, nonce = EXCLUDED.nonce, "
    "ciphertext = EXCLUDED.ciphertext, expires_at = EXCLUDED.expires_at, updated_at = clock_timestamp()"
)
_SELECT_SQL: Final[str] = (
    "SELECT key_ref, wrapped_key, nonce, ciphertext, expires_at FROM lead_contato_custodia "
    "WHERE tenant = $1 AND prospect_ref = $2 AND finalidade = $3 AND expires_at > $4"
)
_PURGE_SQL: Final[str] = "DELETE FROM lead_contato_custodia WHERE expires_at <= $1"


class PostgresLeadContactVault:
    """A custodia duravel de lead, por tenant. Construtor RECUSA sem admissao A6 validada."""

    def __init__(
        self,
        *,
        dsn: str,
        tenant: str,
        wrapper: KeyWrapper,
        admissao: SuppressionAdmission | None,
        ttl_days: int = DEFAULT_TTL_DAYS,
        pool: Any = None,
    ) -> None:
        if not isinstance(admissao, SuppressionAdmission):
            raise LeadCustodyError("lead_custody_admission_missing")
        if ttl_days < 1:
            raise ValueError("lead_custody: ttl_days deve ser >= 1")
        self._schema = schema_for_tenant(tenant)
        self._tenant = tenant
        self._dsn = normalize_dsn(dsn)
        self._wrapper = wrapper
        self._ttl = timedelta(days=ttl_days)
        self._pool = pool

    def __repr__(self) -> str:
        return "PostgresLeadContactVault(<redacted>)"

    async def _ensure_pool(self) -> Any:
        if self._pool is None:
            self._pool = await asyncpg.create_pool(
                self._dsn, min_size=1, max_size=2, setup=self._set_search_path
            )
        return self._pool

    async def _set_search_path(self, conn: Any) -> None:
        await conn.execute(f'SET search_path TO "{self._schema}"')

    async def guardar(self, *, prospect_ref: str, finalidade: str, phone: str, agora: datetime) -> None:
        row = await seal_lead_phone(
            phone,
            self._wrapper,
            tenant=self._tenant,
            prospect_ref=prospect_ref,
            finalidade=finalidade,
            agora=agora,
            ttl=self._ttl,
        )
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                _UPSERT_SQL,
                self._tenant,
                prospect_ref,
                finalidade,
                row.sealed.key_ref,
                row.sealed.wrapped_key,
                row.sealed.nonce,
                row.sealed.ciphertext,
                row.expires_at,
            )

    async def resolver(self, *, prospect_ref: str, finalidade: str, agora: datetime) -> str | None:
        """O telefone do lead para ESTA finalidade, ou `None` (ausente/vencido => RECIPIENT_UNRESOLVED)."""
        _validar(self._tenant, prospect_ref, finalidade)
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            r = await conn.fetchrow(_SELECT_SQL, self._tenant, prospect_ref, finalidade, agora)
        if r is None:
            return None
        row = LeadContactRow(
            sealed=SealedPhone(
                key_ref=str(r["key_ref"]),
                wrapped_key=bytes(r["wrapped_key"]),
                nonce=bytes(r["nonce"]),
                ciphertext=bytes(r["ciphertext"]),
            ),
            expires_at=r["expires_at"],
        )
        return await open_lead_phone(
            row,
            self._wrapper,
            tenant=self._tenant,
            prospect_ref=prospect_ref,
            finalidade=finalidade,
            agora=agora,
        )

    async def purge_expired(self, *, agora: datetime) -> int:
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            status = await conn.execute(_PURGE_SQL, agora)
        return int(str(status).rsplit(" ", 1)[-1] or 0)

    async def aclose(self) -> None:
        pool, self._pool = self._pool, None
        if pool is not None:
            await pool.close()
