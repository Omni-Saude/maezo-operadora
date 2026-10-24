"""Titular DSR channel contract (W5 CLNT 5.2) — SP-OP-LGPD-DSR-001 intake boundary.

Closed DRAFT shape (plan `docs/plans/maezo-completion-program.md` §W5 5.2): the ONLY facts the
browser supplies on this surface are the request KIND (`tipo_requisicao`, the six-value domain
declared by the contract's "Variaveis de entrada" table and carried by the worker map) and the
titular's own free-text request (`detalhes_requisicao`, the pseudonymized free-text field of
`bpmn:55`). Everything else is resolved on the server:

- `tenant_id` comes from the deployment, never the payload;
- the titular identity is the AUTHENTICATED principal (server-resolved; a login is never
  ratified LGPD identity proof, so the channel never seeds `identidade_verificada=True` — the
  identity challenge sub-flow (`GW_Identidade`/`ST_PedirProvaAdicional`) stays in charge);
- `canal` is the literal `portal` (contract: `whatsapp | portal | email`);
- `data_solicitacao_iso` is the server clock's UTC date, because it composes the business key
  `DSR-{tenant_id}-{titular_pseudo_id}-{tipo_requisicao}-{data_solicitacao_iso}` that makes a
  same-day re-request return the active instance instead of a second one;
- `envolve_dados_saude` is "Pré-resolvido por worker" per the contract — the boundary never
  takes it from the browser;
- `decisao_dsr`/`fundamentacao_legal` are NEVER inputs here at all: the decision is a human
  DPO/juridico-privacidade act written by `UT_RevisaoDpo` through the governed decision
  endpoint (`lgpd_decisao` closed shape), guarded by the engine's `GW_DecisaoDsr` fail-closed
  default. A payload that carries one of those names is refused before any admission.

A receipt is a stage, never a verdict: it proves the request was received and staged for the
engine start — never that data was compiled, executed, answered or granted (LGPD art. 18).
"""

from datetime import datetime
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from maezo.portal.contracts.intake import ResourceRef

#: The closed `tipo_requisicao` domain. Source of truth: `docs/processes/contracts/
#: SP-OP-LGPD-DSR-001.md` "Variaveis de entrada" and the worker map
#: `_TIPO_REQUISICAO_PARA_DIREITO` (`src/maezo/tools/workers/lgpd.py`) — both derive from the
#: same six declared values; the DMN `lgpd_dsr_routing` catch-all handles anything else
#: engine-side (fail-safe to juridico-privacidade), but this boundary refuses it earlier.
DsrTipoRequisicao = Literal[
    "confirmacao_acesso",
    "correcao",
    "eliminacao",
    "portabilidade",
    "info_compartilhamento",
    "revogacao_consentimento",
]


class Closed(BaseModel):
    model_config = ConfigDict(
        frozen=True, extra="forbid", strict=True, hide_input_in_errors=True, revalidate_instances="always"
    )


class DsrSubmission(Closed):
    """What the authenticated titular may ask for. Nothing else is an input on this surface."""

    schema_version: Annotated[int, Field(strict=True, ge=1, le=1)] = 1
    tipo_requisicao: DsrTipoRequisicao
    #: Free-text PHI (GAP-DU-07 disposition is open): presence is required, content is NOT
    # length-bounded here beyond the bounded transport allocation of the route — inventing a
    # business ceiling would be an unpinned decision. Never echoed in a receipt or log.
    detalhes_requisicao: Annotated[str, StringConstraints(min_length=1, strip_whitespace=False)] = Field(
        repr=False
    )

    @model_validator(mode="after")
    def _non_blank_request_text(self) -> Self:
        if not self.detalhes_requisicao.strip():
            raise ValueError("detalhes_requisicao must not be blank")
        return self


class DsrReceipt(Closed):
    """Stage receipt: received and staged for the engine start. NEVER a DSR outcome."""

    schema_version: Literal[1] = 1
    request_ref: ResourceRef
    tipo_requisicao: DsrTipoRequisicao
    received_at: datetime
    status: Literal["received"] = "received"


class PortalDsrError(Closed):
    code: Literal[
        "invalid_request",
        "authentication_unavailable",
        "operation_forbidden",
        "dependency_unavailable",
    ]
