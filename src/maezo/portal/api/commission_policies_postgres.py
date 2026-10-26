"""PostgreSQL store of D-36 commission adjustment DRAFTS; drafts only, by schema.

Every statement is scoped to the tenant this store was constructed with, and every row the
table can hold is `DRAFT` (migration 0029 pins `CHECK (status = 'DRAFT')`): no statement
here — and no statement that CAN be written against this table — activates, calculates or
settles anything. The replace is a single optimistic `UPDATE ... WHERE policy_ref = :ref
AND revision = :expected` returning the row: two editors colliding yields zero rows and
the service refuses `stale_revision`; the loser's structure is never merged, never kept.

The nullable value slots round-trip as SQL NULLs — structure without value is the D-36
shipped state, not a gap. Money is stored as `numeric` and read back EXACTLY as it was
typed in (integer-centavos strings, decimal percent strings): the store never converts,
rounds or derives. `None` from `read`/`replace` means "not here for this tenant"; no other
absence is invented.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncEngine

from maezo.portal.api.commission_policies import CommissionPolicyError
from maezo.portal.contracts.commission_policies import CommissionAdjustmentDraft

_COLUMNS = """
    policy_ref, revision, dimensao, dimensao_ref, ajuste_tipo, percentual, valor,
    faixa_min, faixa_max, moeda, vigencia_inicio, vigencia_fim, precedencia,
    criado_por, criado_em, atualizado_por, atualizado_em
"""

_INSERT = text(
    """
    INSERT INTO commission_adjustment_policies (
        tenant, policy_ref, dimensao, dimensao_ref, ajuste_tipo, percentual, valor,
        faixa_min, faixa_max, moeda, vigencia_inicio, vigencia_fim, precedencia, revision,
        status, criado_por, criado_em, atualizado_por, atualizado_em
    ) VALUES (
        :tenant, :policy_ref, :dimensao, :dimensao_ref, :ajuste_tipo, :percentual, :valor,
        :faixa_min, :faixa_max, :moeda, :vigencia_inicio, :vigencia_fim, :precedencia, :revision,
        'DRAFT', :criado_por, :criado_em, :atualizado_por, :atualizado_em
    )
    """
)

_SELECT_ONE = text(
    f"""
    SELECT {_COLUMNS}
      FROM commission_adjustment_policies
     WHERE tenant = :tenant AND policy_ref = :policy_ref
    """
)

_SELECT_ALL = text(
    f"""
    SELECT {_COLUMNS}
      FROM commission_adjustment_policies
     WHERE tenant = :tenant
     ORDER BY precedencia, dimensao, policy_ref
    """
)

# Optimistic replace: identity (policy_ref) and creation audit are never in the SET list;
# `status` cannot appear — the table refuses anything but its DRAFT default anyway.
_UPDATE = text(
    """
    UPDATE commission_adjustment_policies
       SET dimensao = :dimensao, dimensao_ref = :dimensao_ref, ajuste_tipo = :ajuste_tipo,
           percentual = :percentual, valor = :valor, faixa_min = :faixa_min,
           faixa_max = :faixa_max, moeda = :moeda, vigencia_inicio = :vigencia_inicio,
           vigencia_fim = :vigencia_fim, precedencia = :precedencia, revision = :revision,
           atualizado_por = :atualizado_por, atualizado_em = :atualizado_em
     WHERE tenant = :tenant AND policy_ref = :policy_ref AND revision = :expected_revision
    RETURNING policy_ref
    """
)


class PostgresCommissionDraftStore:
    """The `CommissionDraftStore` over migration 0029; one tenant per instance."""

    __slots__ = ("tenant", "_engine")

    def __init__(self, tenant: str, engine: AsyncEngine) -> None:
        if engine.dialect.name != "postgresql":
            raise ValueError("PostgreSQL required")
        self.tenant = tenant
        self._engine = engine

    async def create(self, draft: CommissionAdjustmentDraft) -> None:
        async with self._engine.begin() as connection:
            await connection.execute(_INSERT, _params(self.tenant, draft))

    async def replace(
        self, policy_ref: str, *, expected_revision: str, draft: CommissionAdjustmentDraft
    ) -> CommissionAdjustmentDraft | None:
        async with self._engine.begin() as connection:
            updated = (
                await connection.execute(
                    _UPDATE, _params(self.tenant, draft) | {"expected_revision": int(expected_revision)}
                )
            ).first()
        return draft if updated is not None else None

    async def read(self, policy_ref: str) -> CommissionAdjustmentDraft | None:
        async with self._engine.connect() as connection:
            row = (
                (await connection.execute(_SELECT_ONE, {"tenant": self.tenant, "policy_ref": policy_ref}))
                .mappings()
                .first()
            )
        return _draft(row) if row is not None else None

    async def list(self) -> tuple[CommissionAdjustmentDraft, ...]:
        async with self._engine.connect() as connection:
            rows = (await connection.execute(_SELECT_ALL, {"tenant": self.tenant})).mappings().all()
        return tuple(_draft(row) for row in rows)


def _params(tenant: str, draft: CommissionAdjustmentDraft) -> dict[str, object]:
    """The exact row values of a draft. The state is the table's own `'DRAFT'` literal."""
    try:
        return {
            "tenant": tenant,
            "policy_ref": draft.policy_ref,
            "dimensao": draft.dimensao,
            "dimensao_ref": draft.dimensao_ref,
            "ajuste_tipo": draft.ajuste_tipo,
            "percentual": None if draft.percentual is None else Decimal(draft.percentual),
            "valor": None if draft.valor is None else Decimal(draft.valor).scaleb(-2),
            "faixa_min": None if draft.faixa_min is None else Decimal(draft.faixa_min).scaleb(-2),
            "faixa_max": None if draft.faixa_max is None else Decimal(draft.faixa_max).scaleb(-2),
            "moeda": draft.moeda,
            "vigencia_inicio": draft.vigencia_inicio,
            "vigencia_fim": draft.vigencia_fim,
            "precedencia": draft.precedencia,
            "revision": int(draft.revision),
            "criado_por": draft.criado_por,
            "criado_em": draft.criado_em,
            "atualizado_por": draft.atualizado_por,
            "atualizado_em": draft.atualizado_em,
        }
    except ValueError:
        # A decimal string that survived the DTO cannot fail here; refusing beats guessing.
        raise CommissionPolicyError("invalid_request") from None


def _draft(row: RowMapping) -> CommissionAdjustmentDraft:
    """One row, one receipt — the columns mirror the contract; nothing is derived."""
    return CommissionAdjustmentDraft(
        policy_ref=row["policy_ref"],
        revision=str(row["revision"]),
        dimensao=row["dimensao"],
        dimensao_ref=row["dimensao_ref"],
        ajuste_tipo=row["ajuste_tipo"],
        percentual=_percentual(row["percentual"]),
        valor=_centavos(row["valor"]),
        faixa_min=_centavos(row["faixa_min"]),
        faixa_max=_centavos(row["faixa_max"]),
        moeda=row["moeda"],
        vigencia_inicio=row["vigencia_inicio"],
        vigencia_fim=row["vigencia_fim"],
        precedencia=row["precedencia"],
        criado_por=row["criado_por"],
        criado_em=row["criado_em"],
        atualizado_por=row["atualizado_por"],
        atualizado_em=row["atualizado_em"],
    )


def _percentual(value: Decimal | None) -> str | None:
    """`numeric(12,4)` back to the decimal string the operator typed, sign included."""
    if value is None:
        return None
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered


def _centavos(value: Decimal | None) -> str | None:
    """`numeric(16,2)` back to the integer-centavos string of the house mold."""
    return None if value is None else str(int(value.scaleb(2)))
