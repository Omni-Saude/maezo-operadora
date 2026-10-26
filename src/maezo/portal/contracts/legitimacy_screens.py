"""REG-1.1 — OP21 `legitimacy.screen`: a forma FECHADA do checklist da tela staff.

O parecer do Diretor Jurídico (2026-10-26, snapshot canônico
`docs/compliance/legal/textos-base/PARECER-OP21-2026-10-26-LUCAS.txt` sha256 `d80740cc…`,
registrado em `docs/compliance/legal/PARECER-OP21-RESPOSTA-LUCAS-2026-10-26.md` sha256
`56b8887d…`) decidiu os critérios (§5 "Proposta de critérios", verbatim) e o desenho
`OP21-LEGITIMACY-SCREEN-DESIGN-V1.md` (sha256 `5639f41d…`, §5 DECISÃO 4) fixou a mecânica:
a tela recebe UM checklist FECHADO cujos campos são EXATAMENTE os critérios do parecer —
nenhum campo extra (`extra="forbid"`), nenhuma âncora digitada (a âncora normativa é
propriedade do critério, fixada em tabela fechada da superfície) e NENHUMA caracterização
jurídica do canal em texto nenhum (parecer §3, recomendação técnica: a responsabilidade é da
operadora e da administradora de benefícios; "o canal, em si, não seria alcançado").

Escopo integral desde a v1 (parecer §2): TODOS os critérios do §5 nesta forma — não existe
versão reduzida, não existe campo de "milestone 2027".

Os domínios fechados são os do SUBSTRATO (PR #1022, `gateway/commercial_legitimacy_screen.py`)
importados por referência — nunca redeclarados: um quarto tipo/forma/resultado não existe em
nenhuma camada. O resultado de cada critério é DECLARADO pelo operador (`pending` é um
resultado declarado, nunca inferido); o que dali decorre comercialmente é postura sucessora
(desenho §4), nunca forma deste contrato. A guarda documental permanece dever da operadora
e/ou da administradora (RN 557/2022, art. 12): cada evidência é REFERÊNCIA TIPADA do substrato
(`kind` + `locator` + `document_sha256`) — não existe slot onde o documento caiba.
"""

from datetime import datetime
from typing import Annotated, Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from maezo.gateway.commercial_legitimacy_screen import (
    ContractingForm,
    ContractKind,
    CriterionResultKind,
    LegitimacyEvidenceRef,
)
from maezo.portal.contracts.models import OpaqueRef

__all__ = [
    "Art15Inciso",
    "ChecklistCriterion",
    "ChecklistField",
    "ChecklistOption",
    "ContratacaoNorma",
    "CriterionCheck",
    "INCISOS_ISENTOS_ART16",
    "LegitimacyChecklistManifest",
    "LegitimacyScreenRevalidation",
    "LegitimacyScreenSubmission",
    "PenalidadeAnchor",
    "PortalLegitimacyScreenError",
]

#: A forma CURTA da citação da penalidade — a ÚNICA forma que a tela exibe (parecer §1:
#: "e a forma curta 'RN 489/2022, art. 23-A' na tela"). Registros e testes carregam a forma
#: canônica longa; as formas imprecisas ("RN 659/2025, art. 23-A" e "RN 557/659") não
#: existem em texto nenhum desta superfície — e um Literal não as deixa nem chegar.
PenalidadeAnchor = Literal["RN 489/2022, art. 23-A"]

#: A norma da CONTRATAÇÃO, desdobrada por assunto (parecer §4: "Separar as citações por
#: assunto: contratação na RN 557/2022 e penalidade na RN 489/2022, art. 23-A" — juntá-las
#: como "RN 557/659" é a forma imprecisa recusada).
ContratacaoNorma = Literal["RN 557/2022"]

#: A lista taxativa do art. 15 — incisos I a VI, sem sétima porta (parecer §5: "contratante
#: na lista do art. 15"). O TEXTO de cada inciso é o verbatim da RN 557/2022, exibido pela
#: tela via `ChecklistOption.rotulo` (constante `CHECKLIST` da superfície).
Art15Inciso = Literal[
    "inciso_i",
    "inciso_ii",
    "inciso_iii",
    "inciso_iv",
    "inciso_v",
    "inciso_vi",
]

#: Incisos com ISENÇÃO ESTRUTURAL do requisito temporal do art. 16 — verbatim do art. 16:
#: "exceto as pessoas jurídicas previstas nos incisos I e II daquele artigo" (conselhos
#: profissionais e entidades de classe; sindicatos). Para eles o critério NÃO se aplica e a
#: aplicação é DERIVADA do inciso — nunca digitada (desenho §5: "o campo é … derivado do
#: inciso, nunca digitado livre").
INCISOS_ISENTOS_ART16: Final = ("inciso_i", "inciso_ii")


class _Closed(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        strict=True,
        extra="forbid",
        hide_input_in_errors=True,
        revalidate_instances="always",
    )


class CriterionCheck(_Closed):
    """O estado DECLARADO de um critério: resultado no closed set do substrato + referências.

    O operador declara; nada aqui infere, pondera ou aprova. As evidências são referências
    tipadas do substrato (custódia `operadora`/`administradora`, RN 557/2022 art. 12) —
    nunca bytes de documento. No WIRE a sequência chega como LISTA e é ACEITA SÓ AÍ: o modo
    strict do pydantic não converge lista→tupla no caminho python do ASGI (o mesmo motivo
    pelo qual o substrato hidrata via JSON), então o validador `before` fecha a tupla
    ANTES da validação — o campo persiste tupla fechada e imutável, como no recibo.
    """

    result: CriterionResultKind
    evidence_refs: tuple[LegitimacyEvidenceRef, ...] = ()

    @field_validator("evidence_refs", mode="before")
    @classmethod
    def _sequencia_do_wire(cls, value: object) -> object:
        if isinstance(value, list):
            return tuple(value)
        return value


class _ChecklistBase(_Closed):
    """Os critérios do parecer §5, na ordem deles — compartilhados por criar e revalidar.

    `contract_kind`/`contracting_form` são a MATERIALIZAÇÃO do critério 1 (o registro do
    substrato os carrega como colunas próprias); os quatro critérios de verificação são
    opcionais e independentes — ausente é "ainda não declarado nesta revisão", e `pending`
    é como o operador registra "em verificação". A vedação do art. 29 §1º NÃO é campo
    (desenho §5: é o critério que o ESTADO bloqueante aplica, nunca um checkbox).
    """

    legal_entity_ref: OpaqueRef
    opportunity_ref: OpaqueRef
    # Critério 1 — "tipo de contratação (empresarial ou adesão) e forma (direta, com
    # administradora ou com administradora coestipulante — art. 29)" (parecer §5, verbatim).
    contract_kind: ContractKind
    contracting_form: ContractingForm
    # Critério 2 — "empresarial: vínculo do beneficiário com a contratante (art. 5º)".
    vinculo_beneficiario_contratante: CriterionCheck | None = None
    # Critério 3 — "adesão: contratante na lista do art. 15 e constituída há pelo menos um
    # ano (art. 16, salvo conselhos, entidades de classe e sindicatos)".
    contratante_lista_art15: Art15Inciso | None = None
    constituida_um_ano: CriterionCheck | None = None
    # Critério 4 — "empresário individual: documentos do art. 9º e reconfirmação anual
    # (art. 10)".
    documentos_art9: CriterionCheck | None = None
    reconfirmacao_anual: CriterionCheck | None = None
    #: O vencimento da reconfirmação anual (art. 10 — aniversário do contrato). Instante
    #: com fuso: data "ingênua" não é fato. Par exclusivo com `reconfirmacao_anual`.
    reconfirmation_due_at: Annotated[datetime | None, Field(strict=False)] = None

    @model_validator(mode="after")
    def _checklist_consistente(self) -> Self:
        critérios = (
            self.vinculo_beneficiario_contratante,
            self.contratante_lista_art15,
            self.constituida_um_ano,
            self.documentos_art9,
            self.reconfirmacao_anual,
        )
        if all(c is None for c in critérios):
            raise ValueError("a verification without criteria is not a verification")
        if self.reconfirmation_due_at is not None:
            if self.reconfirmation_due_at.tzinfo is None or self.reconfirmation_due_at.utcoffset() is None:
                raise ValueError("reconfirmation instant must be timezone-aware")
            if self.reconfirmacao_anual is None:
                raise ValueError("reconfirmation_due_at pairs with the reconfirmacao_anual criterion")
        if self.constituida_um_ano is not None:
            # A aplicação do requisito temporal é DERIVADA do inciso (desenho §5), nunca
            # digitada: inciso isento não declara o critério; critério sem inciso é órfão.
            if self.contratante_lista_art15 in INCISOS_ISENTOS_ART16:
                raise ValueError("art. 16 exemption is derived from the inciso, never typed")
            if self.contratante_lista_art15 is None:
                raise ValueError("constituida_um_ano requires the art. 15 inciso")
        elif (
            self.contratante_lista_art15 is not None
            and self.contratante_lista_art15 not in INCISOS_ISENTOS_ART16
        ):
            # Aplicável (incisos III–VI) ⇒ declarado; "em verificação" é result="pending",
            # nunca ausência silenciosa do único critério temporal do art. 16.
            raise ValueError("constituida_um_ano is required for non-exempt art. 15 incisos")
        return self


class LegitimacyScreenSubmission(_ChecklistBase):
    """CREATE a screen: what an internal operator declares. Nothing else is an input."""

    schema_version: Annotated[int, Field(strict=True, ge=1, le=1)] = 1


class LegitimacyScreenRevalidation(_ChecklistBase):
    """REVALIDATE por evento (renovação, reajuste, troca de contratante): checklist COMPLETO
    de novo + a revisão conhecida (molde D-36 `revision`).

    A revisão persistida é SEMPRE `screen_revision + 1`, mintada pelo servidor — revalidação
    é NOVA `screen_revision` (nova linha do substrato), nunca update de revisão (desenho §3).
    """

    schema_version: Annotated[int, Field(strict=True, ge=1, le=1)] = 1
    screen_revision: Annotated[int, Field(strict=True, ge=1)]


class ChecklistOption(_Closed):
    """Uma opção fechada de campo, com o rótulo exibido — sem texto livre de operador."""

    valor: str
    rotulo: str


class ChecklistField(_Closed):
    """Um campo do checklist com a âncora normativa QUE A TELA EXIBE para ele.

    `norm_anchor` é sempre artigo PRÓPRIO da RN 557/2022 (parecer §4 — desdobro por
    assunto); `condicao_estrutural` carrega a derivação do art. 16 quando existe.
    """

    campo_id: str
    norm_anchor: str
    condicao_estrutural: str | None = None


class ChecklistCriterion(_Closed):
    """Um critério do parecer §5 — o rótulo é o VERBATIM do parecer, nunca paráfrase.

    `estrutural=True` marca o critério que o ESTADO bloqueante aplica (art. 29 §1º): ele é
    EXIBIDO e nunca é campo de entrada (desenho §5: "nunca um checkbox").
    """

    criterion_id: str
    rotulo: str
    norm_anchor: str
    campos: tuple[ChecklistField, ...] = ()
    opcoes: tuple[ChecklistOption, ...] = ()
    estrutural: bool = False


class LegitimacyChecklistManifest(_Closed):
    """O checklist FECHADO que a tela renderiza — os 5 critérios do parecer §5 e nada mais.

    `penalidade_anchor` é a forma CURTA (a única forma de tela, parecer §1);
    `norma_contratacao` é a RN 557/2022 desdobrada por assunto; `escopo="integral"` fixa o
    parecer §2 (conteúdo integral desde a v1). Nenhum texto deste manifesto caracteriza
    juridicamente o papel do canal (parecer §3) — cerca mecânica no teste da superfície.
    """

    schema_version: Literal[1] = 1
    norma_contratacao: ContratacaoNorma = "RN 557/2022"
    penalidade_anchor: PenalidadeAnchor = "RN 489/2022, art. 23-A"
    escopo: Literal["integral"] = "integral"
    resultados: tuple[ChecklistOption, ...]
    criterios: tuple[ChecklistCriterion, ...]


class PortalLegitimacyScreenError(_Closed):
    """The closed refusal vocabulary of this boundary. Nothing here suggests a next state."""

    code: Literal[
        "invalid_request",
        "authentication_unavailable",
        "operation_forbidden",
        "field_not_client_owned",
        "resource_unavailable",
        "screen_conflict",
        "stale_revision",
        "dependency_unavailable",
    ]
