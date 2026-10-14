"""Adaptadores de LEITURA do job sobre o que ja' existe: o acesso verificado, a fonte de cobranca do Lucas e a
supressao. So' leem; nenhum grava.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any, Final

import asyncpg  # type: ignore[import-untyped]  # no py.typed upstream

from maezo.agents.lucas.fonte_cobranca import FatosCobranca, FonteCobranca, Indisponivel
from maezo.gateway.audit_postgres import normalize_dsn, schema_for_tenant
from maezo.gateway.vendor_suppression import SuppressionStore
from maezo.platform.webhooks.whatsapp.acesso import VERIFICADO
from maezo.platform.webhooks.whatsapp.acesso_ponte import RefsVerificadas

from .job import Beneficiario

#: Acesso verificado E consentimento em vigor: estado `verificado`, consentimento dado e gravado na AMH
#: (`consent_ref`), nao revogado, nada pendente de gravacao no lago e a referencia pseudonima presente.
#: O estado `verificado` fica ate' a proxima mensagem depois da validade da verificacao; o que limita o
#: tempo e' a custodia do telefone (30 dias desde a ultima mensagem: sem telefone, nao ha' envio).
_VERIFICADOS_SQL: Final[str] = """
SELECT conversation_id, portable_subject_ref, consent_ref
FROM conversa_acesso_beneficiario
WHERE tenant = $1
  AND estado = $2
  AND consentido_em IS NOT NULL
  AND revogado_em IS NULL
  AND NOT revogacao_pendente
  AND NOT consentimento_pendente_gravacao
  AND portable_subject_ref IS NOT NULL
  AND consent_ref IS NOT NULL
ORDER BY conversation_id
"""


class PostgresLeitorDeAcesso:
    def __init__(self, *, dsn: str, tenant: str, pool: Any = None) -> None:
        self._schema = schema_for_tenant(tenant)
        self._tenant = tenant
        self._dsn = normalize_dsn(dsn)
        self._pool = pool

    def __repr__(self) -> str:
        return "PostgresLeitorDeAcesso(<redacted>)"

    async def _ensure_pool(self) -> Any:
        if self._pool is None:
            self._pool = await asyncpg.create_pool(
                self._dsn, min_size=1, max_size=2, setup=self._set_search_path
            )
        return self._pool

    async def _set_search_path(self, conn: Any) -> None:
        await conn.execute(f'SET search_path TO "{self._schema}"')

    async def verificados(self) -> Sequence[Beneficiario]:
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(_VERIFICADOS_SQL, self._tenant, VERIFICADO)
        return [
            Beneficiario(
                conversation_id=str(r["conversation_id"]),
                portable_ref=str(r["portable_subject_ref"]),
                consent_ref=str(r["consent_ref"]),
            )
            for r in rows
        ]

    async def aclose(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None


class LeitorDeFatosDoLucas:
    """Os fatos de cobranca pela `FonteCobranca` do Lucas, com a ref. VERIFICADA registrada durante a chamada
    (o mesmo mecanismo do despachante: `RefsVerificadas.do_turno`). Sem consulta pelo telefone."""

    def __init__(
        self, *, fonte: FonteCobranca, refs: RefsVerificadas, pseudo_id_de: Callable[[str], str]
    ) -> None:
        self._fonte = fonte
        self._refs = refs
        self._pseudo_id_de = pseudo_id_de

    async def de(self, beneficiario: Beneficiario) -> FatosCobranca | Indisponivel:
        pseudo_id = self._pseudo_id_de(beneficiario.conversation_id)
        with self._refs.do_turno(pseudo_id, beneficiario.portable_ref, beneficiario.consent_ref):
            return await self._fonte.fatos(pseudo_id, None, phone_hash=None)


class LeitorDeSupressaoDoStore:
    """`SuppressionStore.active_suppressions` (SP-OP-SUPP-001) como pares opacos. Falha PROPAGA."""

    def __init__(self, *, store: SuppressionStore, tenant: str) -> None:
        self._store = store
        self._tenant = tenant

    async def vigentes(self) -> frozenset[tuple[str, str]]:
        chaves = await self._store.active_suppressions(self._tenant)
        return frozenset((str(c.subject_ref), str(c.contact_channel)) for c in chaves)
