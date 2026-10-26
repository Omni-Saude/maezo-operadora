"""D-36 commission ADJUSTMENT STRUCTURE drafts — closed shapes of the DARK internal surface.

Decision D-36 (dono, 2026-10-25): an INTERNAL management surface to REGISTER commission
adjustment STRUCTURES across the three dimensions (produto, canal, corretor) — multiple
simultaneous adjustments per target, vigência windows, declared precedence — with data
entering in the FUTURE: every value slot is nullable, nothing is calculated, published or
settled, and the ONLY lifecycle state is `DRAFT` (activation is a FUTURE act that requires
the A8 Forma 1 ratification by the `comercial` + `legal` + `DPO` trio and arrives as a
SUCCESSOR migration, never as an update).

Family separation is structural: these shapes are MAEZO-INTERNAL draft structures and never
speak the closed portal stub dialect `commission-policy.proposed.v1` — no `monetaryContent`,
no `ratification`, no stub `schemaVersion` string (the route refuses all of them on sight,
through `refuse_commission_policy_payload` and the reserved-name guard). A payload that
fabricates the contract family is a 403 here, never a draft.

No shape here computes anything: no total, no "effective rate", no conversion, no ranking.
The receipt mirrors exactly what was stored and who stored it — structure, windows,
precedence, audit columns — and nothing else.
"""

from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from maezo.portal.contracts.intake import DecimalRevision, ResourceRef

#: The closed dimension domain (D-36): exactly the three dimensions, no fourth, no "global".
CommissionDimensao = Literal["produto", "canal", "corretor"]

#: The closed adjustment KIND domain — the structure of an adjustment, never its value.
CommissionAjusteTipo = Literal["percentual", "valor", "faixa"]

#: Symbolic id of the dimension target (an opaque internal identifier — a product SKU-like
#: ref, a channel ref, a broker ref). Never a name, document or free text: the closed charset
#: keeps accented prose, spaces and email-like strings out of the draft store. Mirrors the
#: migration CHECK byte for byte.
DimensaoRef = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_.:-]{1,128}$")]

#: A percent as a bounded decimal STRING (no floats on a money-adjacent boundary): optional
#: sign, up to 8 integer digits and 4 decimals — exactly the `numeric(12, 4)` storage of the
#: migration. The parseable range is a storage fit, NOT a business rule: which percentages
#: are acceptable is a future ratified decision, so no sign or magnitude is refused here.
PercentualStr = Annotated[str, StringConstraints(pattern=r"^-?(0|[1-9][0-9]{0,7})(\.[0-9]{1,4})?$")]

#: A monetary amount as an integer-CENTAVOS string (house mold), optional sign, `numeric(
#: 16, 2)` storage fit. Nullable everywhere: D-36 inserts structures, values come later.
CentavosStr = Annotated[str, StringConstraints(pattern=r"^-?(0|[1-9][0-9]{0,13})$")]

#: Structural ISO-4217-shaped currency tag for the monetary kinds (`valor`/`faixa`); a
#: percentage has no currency. Which currency applies is future data, never inferred here.
MoedaStr = Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")]


def _aware(value: datetime) -> datetime:
    """Timezone-aware boundary instant or refusal; naive vigência is never a stored fact."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("vigencia must be timezone-aware")
    return value


class Closed(BaseModel):
    model_config = ConfigDict(
        frozen=True, extra="forbid", strict=True, hide_input_in_errors=True, revalidate_instances="always"
    )


class _EstruturaBase(Closed):
    """The editable structure — shared verbatim by create and replace (full-shape replace).

    Every value slot is OPTIONAL and defaults to absent: a draft may be pure structure (the
    D-36 property "dados inseridos no FUTURO", pinned by test). The kind/slot consistency
    validators are STRUCTURAL (a percent never carries centavos), never business: no value
    domain, no sign rule, no cross-draft uniqueness — multiple simultaneous adjustments per
    target are the point of D-36.
    """

    dimensao: CommissionDimensao
    dimensao_ref: DimensaoRef
    ajuste_tipo: CommissionAjusteTipo
    percentual: PercentualStr | None = None
    valor: CentavosStr | None = None
    faixa_min: CentavosStr | None = None
    faixa_max: CentavosStr | None = None
    moeda: MoedaStr | None = None
    #: Wire instants arrive as ISO-8601 strings (the model is strict; these two fields
    #: intentionally relax to the datetime schema, which still refuses anything but a real
    #: instant) and are refused if NAIVE — a vigência without an offset is not a fact.
    vigencia_inicio: Annotated[datetime | None, Field(strict=False)] = None
    vigencia_fim: Annotated[datetime | None, Field(strict=False)] = None
    #: Declared structural rank (1 = evaluated first by the FUTURE rule). Recorded, never
    #: resolved: no combination semantics ships with the dark surface.
    precedencia: Annotated[int, Field(strict=True, ge=1, le=32767)]

    @model_validator(mode="after")
    def _estrutura_consistente(self) -> Self:
        if self.vigencia_inicio is not None:
            _aware(self.vigencia_inicio)
        if self.vigencia_fim is not None:
            _aware(self.vigencia_fim)
        if (
            self.vigencia_inicio is not None
            and self.vigencia_fim is not None
            and self.vigencia_inicio >= self.vigencia_fim
        ):
            raise ValueError("vigencia_inicio must precede vigencia_fim")
        if self.ajuste_tipo == "percentual" and (
            self.valor is not None or self.faixa_min is not None or self.faixa_max is not None
        ):
            raise ValueError("percentual adjustment carries no monetary slot")
        if self.ajuste_tipo == "valor" and (
            self.percentual is not None or self.faixa_min is not None or self.faixa_max is not None
        ):
            raise ValueError("valor adjustment carries no percentual/faixa slot")
        if self.ajuste_tipo == "faixa" and (self.percentual is not None or self.valor is not None):
            raise ValueError("faixa adjustment carries no percentual/valor slot")
        if self.moeda is not None and self.ajuste_tipo == "percentual":
            raise ValueError("percentual adjustment carries no moeda")
        if self.faixa_min is not None and self.faixa_max is not None:
            try:
                ordered = Decimal(self.faixa_min) <= Decimal(self.faixa_max)
            except InvalidOperation:
                raise ValueError("faixa bounds must be decimal strings") from None
            if not ordered:
                raise ValueError("faixa_min must not exceed faixa_max")
        return self


class CommissionAdjustmentDraftSubmission(_EstruturaBase):
    """CREATE a draft: what an internal operator may register. Nothing else is an input."""

    schema_version: Annotated[int, Field(strict=True, ge=1, le=1)] = 1


class CommissionAdjustmentDraftReplacement(_EstruturaBase):
    """REPLACE a draft's structure (POST-only house posture): full shape + expected revision.

    The replacement is idempotent over the WHOLE editable structure — absent slots clear
    values (they are nullable by design). `revision` must match the stored one exactly
    (`stale_revision` otherwise, 409): two operators editing the same draft is a conflict to
    surface, never a silent last-write. `status` is NOT an input on this surface at all.
    """

    schema_version: Annotated[int, Field(strict=True, ge=1, le=1)] = 1
    revision: DecimalRevision


class CommissionAdjustmentDraft(Closed):
    """What the surface PROVES back: the stored structure, always `DRAFT`. Never a verdict.

    No computed field exists — no total, no effective rate, no currency conversion, no
    ranking, no "next step". The audit columns are WHO/WHEN of the draft edit, not an
    approval: nobody approves anything here, and no field claims otherwise.
    """

    schema_version: Literal[1] = 1
    policy_ref: ResourceRef
    revision: DecimalRevision
    dimensao: CommissionDimensao
    dimensao_ref: DimensaoRef
    ajuste_tipo: CommissionAjusteTipo
    percentual: PercentualStr | None = None
    valor: CentavosStr | None = None
    faixa_min: CentavosStr | None = None
    faixa_max: CentavosStr | None = None
    moeda: MoedaStr | None = None
    vigencia_inicio: datetime | None = None
    vigencia_fim: datetime | None = None
    precedencia: Annotated[int, Field(strict=True, ge=1, le=32767)]
    status: Literal["DRAFT"] = "DRAFT"
    criado_por: str
    criado_em: datetime
    atualizado_por: str
    atualizado_em: datetime


class CommissionAdjustmentDraftPage(Closed):
    """The draft list: server-ordered (precedencia, dimensao, policy_ref), no pagination.

    Dark internal surface, drafts only — the store returns the tenant's whole draft set in
    the declared order; a pagination contract is a future need, not an invented one.
    """

    schema_version: Literal[1] = 1
    drafts: tuple[CommissionAdjustmentDraft, ...]
    count: Annotated[int, Field(strict=True, ge=0)]


class PortalCommissionPolicyError(Closed):
    """The closed refusal vocabulary of this boundary. Nothing here suggests a next state."""

    code: Literal[
        "invalid_request",
        "authentication_unavailable",
        "operation_forbidden",
        "commission_policy_fabricated",
        "field_not_client_owned",
        "resource_unavailable",
        "stale_revision",
        "dependency_unavailable",
    ]
