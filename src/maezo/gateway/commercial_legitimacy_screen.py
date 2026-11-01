"""OP21 `legitimacy.screen` — substrato da triagem de legitimidade da PJ contratante (REG-1.1, W1 REG).

**Por que existe.** O parecer do Diretor Jurídico (2026-10-26, snapshot canônico
`docs/compliance/legal/textos-base/PARECER-OP21-2026-10-26-LUCAS.txt` sha256 `d80740cc…`,
registrado em `docs/compliance/legal/PARECER-OP21-RESPOSTA-LUCAS-2026-10-26.md` sha256
`56b8887d…`) validou as âncoras do controle e o desenho
`OP21-LEGITIMACY-SCREEN-DESIGN-V1.md` (sha256 `5639f41d…`, §3 DECISÃO 2) fixou o modelo de
dados: o banco guarda a VERIFICAÇÃO (critério→resultado→referência) — NUNCA os documentos.
A guarda documental é dever da operadora e/ou da administradora de benefícios
(RN 557/2022, arts. 12 e 15 §3º; parecer §5 "Registros", verbatim: "a Maezo guarde o registro
da verificação no banco de dados … e a referência aos documentos, sem duplicar a custódia").
Cada evidência é uma REFERÊNCIA TIPADA (`kind` + `locator` + `document_sha256`), com espaço
FECHADO: não existe campo onde bytes de documento caibam — na tabela (CHECK de forma do
JSONB), no modelo (pydantic `extra="forbid"`) e no store.

**Âncoras normativas, desdobradas por assunto (parecer §1/§4 — proibido juntar as normas):**

- **Contratação** — o que a verificação apura — cita **RN 557/2022 por artigo próprio**:
  art. 29 I–III (tipo e forma de contratação), art. 5º (vínculo empresarial), arts. 15 e 16
  (adesão: lista taxativa + constituída há ≥ 1 ano, com isenção estrutural dos incisos I e II),
  arts. 9º–14 (empresário individual), art. 10 (reconfirmação anual), art. 29 §1º (vedada a
  inclusão de beneficiário sem a PJ legitimada) e art. 39 (ingresso irregular).
- **Penalidade** — por que o controle existe — cita **"RN 489/2022, art. 23-A (redação dada
  pela RN 659/2025)"** (forma canônica do parecer §1; forma curta "RN 489/2022, art. 23-A" é
  da TELA, que é PR 2). As formas "RN 659/2025, art. 23-A" e "RN 557/659" são IMPRECISAS e
  REFUSADAS em âncora persistida — na camada de modelo (`_anchor_sem_forma_imprecisa`) e na
  camada de banco (CHECK da migração 0030 sobre o JSONB).
- **Custódia** — RN 557/2022, art. 12: o `kind` da referência diz QUEM TEM A GUARDA do
  documento (`operadora` | `administradora`), domínio fechado que torna o canal/corretor
  estruturalmente irrepresentável como custodiante (parecer §3 — "o canal, em si, não seria
  alcançado"; recomendação técnica de não caracterizá-lo). O rol documental operacional por
  critério é RATIFY-LATER (desenho §9 E-4): o enum de `kind` pode crescer POR MIGRAÇÃO
  SUCESSORA, nunca por relabel.
- **Efeito do art. 39** — parecer §6: bloquear + registrar + SINALIZAR. O sinal persistido
  carrega a âncora literal **"RN 557/2022, art. 39"** (pin do desenho §3). O controle MODELA a
  sinalização — nunca constitui o vínculo individual, que é fato jurídico externo.

**O pin estrutural do estado.** `status` é CHECK fechado em `('valid','invalid','pending')`
(migração 0030): nenhum outro estado é persistível nem por bug. O ENFORCING não é estado
desta tabela — é postura sucessora (desenho §4 DECISÃO 3: classe própria, bind explícito em
composition root, nunca knob de ambiente); "recibo vigente único por PJ" é regra de VIGÊNCIA
avaliada pelo chamador, não derivada aqui: o store devolve a ÚLTIMA revisão e nunca inventa
veredito de vigência.

**Idempotência (desenho §3, emenda ao registry).** Duas unicidades ESTRUTURAIS, no nível do
storage: a instância é `(tenant, operation='legitimacy_screen', screen_ref, screen_revision)`
(PK — reenvio nunca cria segunda instância, molde 0020) e a binding à oportunidade é
`(tenant, operation, opportunity_ref, screen_revision)` (UNIQUE — uma tela por oportunidade e
revisão; revalidação por evento = NOVA `screen_revision`, nunca UPDATE de revisão).

**Escuro e sem wiring (este PR é o substrato, desenho §8.3 PR 1).** Nenhuma flag, nenhuma
rota, nenhum guard e nenhum enforcing existe neste PR: a tabela nasce sem nenhum escritor na
composição default e nenhuma superfície comercial muda byte a byte. Ficam para os PRs
seguintes: a flag `collective_legitimacy_screen` e a tela staff (PR 2), o vocabulário de
recusa do guard (`LEGITIMACY_UNPROVEN`/`SOURCE_UNAVAILABLE`/`PHI_IN_COMMERCIAL_INPUT`) e a
postura shadow com o wrap do `CommercialGateway` (PR 3), e a postura enforcing (PR 4). O
erro do store (`LegitimacyScreenStoreError`) é camada de PERSISTÊNCIA e não antecipa esse
vocabulário.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Annotated, Any, Final, Literal, Protocol, Self, final

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from maezo.portal.contracts.models import OpaqueRef, Sha256Digest

__all__ = [
    "ART39_ANCHOR",
    "ContractKind",
    "ContractingForm",
    "CriterionId",
    "CriterionResultKind",
    "EvidenceLocator",
    "EvidenceRefKind",
    "LegitimacyCriterion",
    "LegitimacyEvidenceRef",
    "LegitimacyScreenRecord",
    "LegitimacyScreenStatus",
    "LegitimacyScreenStore",
    "LegitimacyScreenStoreError",
    "NormAnchor",
    "PostgresLegitimacyScreenStore",
    "PROHIBITED_ANCHOR_FRAGMENTS",
    "ScreenRef",
]

# ----------------------------------------------------------------------------------
# Âncoras e cercas de citação (parecer §1/§4/§5 — desdobro contratação × penalidade).
# ----------------------------------------------------------------------------------

#: Âncora literal da sinalização do efeito do art. 39 (desenho §3: "art39_anchor CHECK =
#: 'RN 557/2022, art. 39' quando true"). Verbatim, nunca reescrita nem abreviada.
ART39_ANCHOR: Final = "RN 557/2022, art. 39"

#: Formas de citação IMPRECISAS que nenhuma âncora persistida pode carregar (parecer §1:
#: "A forma que circula hoje, 'RN 659/2025, art. 23-A', é imprecisa"; §4: o registro interno
#: que junta as normas como "RN 557/659" deve ser desdobrado). A forma canônica
#: "RN 489/2022, art. 23-A (redação dada pela RN 659/2025)" NÃO contém nenhum destes
#: fragmentos e passa. Aplicada no modelo e, byte a byte, no CHECK da migração 0030.
PROHIBITED_ANCHOR_FRAGMENTS: Final = ("RN 659/2025, art. 23-A", "RN 557/659")

# ----------------------------------------------------------------------------------
# Domínios fechados — Literals e refs espelhadas byte a byte com a migração 0030.
# ----------------------------------------------------------------------------------

#: Tipo de contratação — RN 557/2022 art. 29 e arts. 9º–14 (empresário individual é o
#: regime próprio dos arts. 9º–14, sub-caso do coletivo empresarial). Sem quarto valor.
ContractKind = Literal["empresarial", "adesao", "empresario_individual"]

#: Forma de contratação — RN 557/2022 art. 29 I–III (direta; com administradora; com
#: administradora COESTIPULANTE). Sem quarta forma.
ContractingForm = Literal["direta", "administradora", "coestipulante"]

#: Resultado de um critério — closed set do desenho §3. `pending` não é aprovação por
#: omissão: é resultado declarado, e o chamador decide o que dele decorre (postura).
CriterionResultKind = Literal["proven", "not_proven", "not_applicable", "pending"]

#: Estado da verificação persistida — o PIN estrutural da 0030. O enforcing não é estado.
LegitimacyScreenStatus = Literal["valid", "invalid", "pending"]

#: Guarda da evidência (RN 557/2022, art. 12): QUEM retém o documento apontado. Fechado em
#: exatamente os dois custodiantes do parecer §5 — o canal nunca é custodiante representável
#: (parecer §3). Cresce só por migração sucessora (E-4).
EvidenceRefKind = Literal["operadora", "administradora"]

#: Referência de screen — molde `policy_ref` da 0029, espelhado byte a byte no CHECK.
ScreenRef = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{16,128}$")]

#: Identificador do critério — id simbólico opaco do checklist FECHADO (a enumeração dos
#: critérios é a tela, PR 2 — desenho §5; o substrato recusa qualquer coisa fora da forma).
CriterionId = Annotated[str, StringConstraints(pattern=r"^[a-z0-9_]{1,64}$")]

#: Ponteiro para o documento sob custódia — charset fechado com separadores de caminho,
#: teto de 256 (o mesmo teto de ref do gateway) — o "CHECK de tamanho de locator cerca
#: abuso" do desenho §3: nenhum texto longo, nenhum embed, nenhum byte passa por aqui.
#: A forma fatorada (`{1,255}` + um caractere) existe porque o motor de regex do PostgreSQL
#: recusa contagem de repetição acima de 255 — o espelho byte a byte na migração 0030 usa
#: exatamente este padrão.
EvidenceLocator = Annotated[str, StringConstraints(pattern=r"^([A-Za-z0-9_.:/-]{1,255})?[A-Za-z0-9_.:/-]$")]

#: Âncora normativa de um critério — texto curto com a forma canônica de citação. O range
#: `À-ÿ` existe para a forma longa canônica ("… (redação dada pela RN 659/2025)") caber;
#: as formas imprecisas são recusadas pelo validador explícito, não pelo charset.
NormAnchor = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9À-ÿ .,;:()/§ºª-]{1,192}$")]


def _anchor_sem_forma_imprecisa(anchor: str) -> str:
    """Recusa LOUD as formas de citação imprecisas em âncora que vai persistir (parecer §1)."""
    for fragment in PROHIBITED_ANCHOR_FRAGMENTS:
        if fragment in anchor:
            raise ValueError(f"anchor carries the imprecise citation form {fragment!r}")
    return anchor


class _Closed(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        strict=True,
        extra="forbid",
        hide_input_in_errors=True,
        revalidate_instances="always",
    )


class LegitimacyEvidenceRef(_Closed):
    """Referência TIPADA a um documento sob custódia — nunca o documento.

    Exatamente três campos e nenhum outro (o fechado é a fronteira anti-custódia: não
    existe slot para conteúdo, preview, base64 ou "só o primeiro parágrafo"). `kind` diz
    quem tem a GUARDA do art. 12; `locator` é o ponteiro opaco no repositório DO
    CUSTODIANTE; `document_sha256` é o digest do documento apontado, que permite conferir
    integridade SEM transportar o objeto. Custódia é da operadora e/ou da administradora
    (RN 557/2022, art. 12; parecer §5 "Registros").
    """

    kind: EvidenceRefKind
    locator: EvidenceLocator
    document_sha256: Sha256Digest


class LegitimacyCriterion(_Closed):
    """Um critério verificado: critério→resultado→referências, com a âncora normativa.

    A enumeração FECHADA dos critérios (os campos exatos da tela) é a superfície do PR 2
    (desenho §5); o substrato fixa a FORMA: id simbólico fechado, resultado no closed set,
    âncora por artigo PRÓPRIO da norma de contratação (RN 557/2022) — ou da penalidade
    (RN 489/2022, art. 23-A, redação dada pela RN 659/2025) quando o critério é de infração —
    e referências tipadas. `pending` é um resultado declarado, nunca inferido.
    """

    criterion_id: CriterionId
    result: CriterionResultKind
    norm_anchor: NormAnchor
    evidence_refs: tuple[LegitimacyEvidenceRef, ...] = ()

    @model_validator(mode="after")
    def _ancora_canonica(self) -> Self:
        _anchor_sem_forma_imprecisa(self.norm_anchor)
        return self


@final
class LegitimacyScreenRecord(_Closed):
    """A verificação persistida — o recibo que o banco guarda, linha por revisão.

    Imutável por revisão: revalidação por evento (renovação, reajuste, troca de
    contratante) é NOVA `screen_revision` (nova linha), nunca UPDATE de revisão (desenho
    §3). `would_block` registra o que a postura enforcing DECIDIRIA — registrável em shadow,
    nunca aplicador de efeito aqui. A sinalização do art. 39 é par exclusivo com a âncora
    literal: sinalizada ⟺ âncora presente (CHECK espelhado da 0030). `reconfirmation_due_at`
    materializa o art. 10 (aniversário do contrato) quando o regime exige.
    """

    schema_version: Literal["legitimacy-screen-record.v1"] = "legitimacy-screen-record.v1"
    tenant: OpaqueRef
    operation: Literal["legitimacy_screen"] = "legitimacy_screen"
    screen_ref: ScreenRef
    screen_revision: Annotated[int, Field(strict=True, ge=1)]
    legal_entity_ref: OpaqueRef
    opportunity_ref: OpaqueRef
    contract_kind: ContractKind
    contracting_form: ContractingForm
    criteria: tuple[LegitimacyCriterion, ...] = Field(min_length=1)
    status: LegitimacyScreenStatus
    would_block: bool
    art39_signaled: bool
    # Literal inline (mypy não aceita variável em Literal) — byte-igual a `ART39_ANCHOR`,
    # igualdade que o teste de unidade pinia.
    art39_anchor: Literal["RN 557/2022, art. 39"] | None = None
    reconfirmation_due_at: datetime | None = None
    created_by: OpaqueRef
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def _estado_consistente(self) -> Self:
        for stamp in (self.created_at, self.updated_at, self.reconfirmation_due_at):
            if stamp is not None and (stamp.tzinfo is None or stamp.utcoffset() is None):
                raise ValueError("screen instants must be timezone-aware")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at must not precede created_at")
        # Par exclusivo espelhado no CHECK da 0030: sinalizada ⟺ âncora "RN 557/2022, art. 39".
        if self.art39_signaled != (self.art39_anchor is not None):
            raise ValueError("art39_signaled must pair with the art39 anchor")
        return self


# ----------------------------------------------------------------------------------
# O port de persistência (desenho §2 item 1: "e o port de persistência").
# ----------------------------------------------------------------------------------


@final
class LegitimacyScreenStoreError(Exception):
    """Erro de PERSISTÊNCIA do substrato — não é o vocabulário de recusa do guard.

    O vocabulário do guard (`LEGITIMACY_UNPROVEN`/`SOURCE_UNAVAILABLE`/`PHI_IN_COMMERCIAL_INPUT`)
    é da postura no caminho comercial (PRs 3–4, desenho §2 item 1) e não nasce aqui. Códigos
    fechados: `source_unavailable` = a fonte não respondeu/está ilegível (nenhum texto de
    driver cruza a fronteira); `contract_mismatch` = a linha violaria a unicidade de
    instância da oportunidade (mesma oportunidade + revisão sob outro screen_ref) — recusa
    LOUD, nunca overwrite silencioso (molde 0020).
    """

    code: Literal["source_unavailable", "contract_mismatch"]

    def __init__(self, code: Literal["source_unavailable", "contract_mismatch"]) -> None:
        super().__init__(code)
        self.code = code


class LegitimacyScreenStore(Protocol):
    """Port de persistência do registro de verificação (molde `SuppressionStore`).

    `None` significa "não está aqui" (UNKNOWN) — nunca uma tela fabricada. O port devolve a
    ÚLTIMA revisão por eixo (oportunidade; PJ contratante) e NUNCA deriva vigência: julgar
    "recibo vigente" (status × reconfirmation_due_at × relógio) é da postura sucessora
    (desenho §4 — enforcing), não do storage.
    """

    async def save(self, record: LegitimacyScreenRecord) -> LegitimacyScreenRecord:
        """Persiste a revisão; reenvio da MESMA chave devolve a linha já existente."""
        ...

    async def read(self, tenant: str, screen_ref: str, screen_revision: int) -> LegitimacyScreenRecord | None:
        """Lê uma revisão exata da instância; ausência = `None`."""
        ...

    async def latest_for_opportunity(
        self, tenant: str, opportunity_ref: str
    ) -> LegitimacyScreenRecord | None:
        """Última revisão da tela da OPORTUNIDADE (eixo instância/binding); `None` se nenhuma."""
        ...

    async def latest_for_legal_entity(
        self, tenant: str, legal_entity_ref: str
    ) -> LegitimacyScreenRecord | None:
        """Última revisão da tela da PJ CONTRATANTE (eixo "tela verde é da PJ"); `None` se nenhuma."""
        ...


# ----------------------------------------------------------------------------------
# Store PostgreSQL (molde `PostgresSuppressionStore`; DDL pela migração 0030).
# ----------------------------------------------------------------------------------

_INSERT_SQL = text(
    """
    INSERT INTO portal_legitimacy_screens (
        tenant, operation, screen_ref, screen_revision, legal_entity_ref, opportunity_ref,
        contract_kind, contracting_form, criteria, status, would_block,
        art39_signaled, art39_anchor, reconfirmation_due_at, created_by, created_at, updated_at
    ) VALUES (
        :tenant, 'legitimacy_screen', :screen_ref, :screen_revision, :legal_entity_ref,
        :opportunity_ref, :contract_kind, :contracting_form, CAST(:criteria AS jsonb),
        :status, :would_block, :art39_signaled, :art39_anchor, :reconfirmation_due_at,
        :created_by, :created_at, :updated_at
    )
    ON CONFLICT (tenant, operation, screen_ref, screen_revision) DO NOTHING
    """
)

_SELECT_ONE_SQL = text(
    """
    SELECT tenant, operation, screen_ref, screen_revision, legal_entity_ref, opportunity_ref,
           contract_kind, contracting_form, criteria, status, would_block,
           art39_signaled, art39_anchor, reconfirmation_due_at, created_by, created_at, updated_at
      FROM portal_legitimacy_screens
     WHERE tenant = :tenant AND operation = 'legitimacy_screen'
       AND screen_ref = :screen_ref AND screen_revision = :screen_revision
    """
)

#: Template da última revisão por eixo. O `{axis}` é SEMPRE uma constante do próprio módulo
#: (`opportunity_ref` | `legal_entity_ref`) — nunca entrada do chamador; os valores são bind.
_SELECT_LATEST_TEMPLATE = """
    SELECT tenant, operation, screen_ref, screen_revision, legal_entity_ref, opportunity_ref,
           contract_kind, contracting_form, criteria, status, would_block,
           art39_signaled, art39_anchor, reconfirmation_due_at, created_by, created_at, updated_at
      FROM portal_legitimacy_screens
     WHERE tenant = :tenant AND operation = 'legitimacy_screen' AND {axis} = :axis_value
     ORDER BY screen_revision DESC, screen_ref
     LIMIT 1
    """


def _criteria_json(record: LegitimacyScreenRecord) -> str:
    """Serialização canônica do bloco de critérios (o jsonb do banco re-ordena chaves)."""
    return json.dumps(
        [criterion.model_dump(mode="json") for criterion in record.criteria],
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _record_from_row(row: Any) -> LegitimacyScreenRecord:
    """Hidrata o recibo — o modelo fechado é a validação REAL da leitura.

    A via é o caminho JSON do modelo (`model_validate_json`): jsonb chega como
    listas (codec do dialect asyncpg) e o modo strict do pydantic não converge
    lista→tupla em memória, mas o parser JSON constrói as tuplas fechadas de
    `criteria`/`evidence_refs` e valida cada nível — linha malformada (ou jsonb
    que perdeu a forma fechada) vira `ValidationError` ⇒ `source_unavailable`
    no chamador: o banco nunca devolve uma tela que o modelo não assuma. Um
    `str` residual do codec é parseado antes; instantes viajam como ISO-8601.
    """
    raw_criteria = row["criteria"]
    criteria = json.loads(raw_criteria) if isinstance(raw_criteria, str) else raw_criteria

    def _iso(value: datetime | None) -> str | None:
        return None if value is None else value.isoformat()

    return LegitimacyScreenRecord.model_validate_json(
        json.dumps(
            {
                "tenant": row["tenant"],
                "screen_ref": row["screen_ref"],
                "screen_revision": row["screen_revision"],
                "legal_entity_ref": row["legal_entity_ref"],
                "opportunity_ref": row["opportunity_ref"],
                "contract_kind": row["contract_kind"],
                "contracting_form": row["contracting_form"],
                "criteria": criteria,
                "status": row["status"],
                "would_block": row["would_block"],
                "art39_signaled": row["art39_signaled"],
                "art39_anchor": row["art39_anchor"],
                "reconfirmation_due_at": _iso(row["reconfirmation_due_at"]),
                "created_by": row["created_by"],
                "created_at": _iso(row["created_at"]),
                "updated_at": _iso(row["updated_at"]),
            },
            ensure_ascii=False,
        )
    )


class PostgresLegitimacyScreenStore:
    """`LegitimacyScreenStore` sobre a migração 0030 — idempotente no nível do storage.

    Reenvio da mesma instância `(tenant, operation, screen_ref, screen_revision)` devolve a
    linha JÁ EXISTENTE (`ON CONFLICT DO NOTHING` + re-leitura). A segunda unicidade
    (`tenant, operation, opportunity_ref, screen_revision`) não é conflito silencioso: a
    mesma oportunidade+revisão sob OUTRO `screen_ref` viola a UNIQUE e vira
    `contract_mismatch` — instância duplicada é recusa, nunca segunda trilha (molde 0020).
    Nenhum método faz UPDATE: revisão persistida é imutável; revalidação é nova revisão.
    """

    __slots__ = ("_engine",)

    def __init__(self, engine: AsyncEngine) -> None:
        if engine.dialect.name != "postgresql":
            raise ValueError("PostgreSQL required")
        self._engine = engine

    async def save(self, record: LegitimacyScreenRecord) -> LegitimacyScreenRecord:
        params = {
            "tenant": record.tenant,
            "screen_ref": record.screen_ref,
            "screen_revision": record.screen_revision,
            "legal_entity_ref": record.legal_entity_ref,
            "opportunity_ref": record.opportunity_ref,
            "contract_kind": record.contract_kind,
            "contracting_form": record.contracting_form,
            "criteria": _criteria_json(record),
            "status": record.status,
            "would_block": record.would_block,
            "art39_signaled": record.art39_signaled,
            "art39_anchor": record.art39_anchor,
            "reconfirmation_due_at": record.reconfirmation_due_at,
            "created_by": record.created_by,
            "created_at": record.created_at,
            "updated_at": record.updated_at,
        }
        try:
            async with self._engine.begin() as db:
                await db.execute(_INSERT_SQL, params)
                row = (
                    (
                        await db.execute(
                            _SELECT_ONE_SQL,
                            {
                                "tenant": record.tenant,
                                "screen_ref": record.screen_ref,
                                "screen_revision": record.screen_revision,
                            },
                        )
                    )
                    .mappings()
                    .one()
                )
        except IntegrityError:
            # Instância duplicada da oportunidade (UNIQUE opportunity+revision sob outro
            # screen_ref): recusa de CONTRATO, não indisponibilidade de fonte.
            raise LegitimacyScreenStoreError("contract_mismatch") from None
        except LegitimacyScreenStoreError:
            raise
        except Exception:
            raise LegitimacyScreenStoreError("source_unavailable") from None
        return _record_from_row(row)

    async def read(self, tenant: str, screen_ref: str, screen_revision: int) -> LegitimacyScreenRecord | None:
        return await self._read_one(
            _SELECT_ONE_SQL,
            {"tenant": tenant, "screen_ref": screen_ref, "screen_revision": screen_revision},
        )

    async def latest_for_opportunity(
        self, tenant: str, opportunity_ref: str
    ) -> LegitimacyScreenRecord | None:
        return await self._read_one(
            # nosemgrep: avoid-sqlalchemy-text # fonte: axis = coluna literal nesta linha
            text(_SELECT_LATEST_TEMPLATE.format(axis="opportunity_ref")),
            {"tenant": tenant, "axis_value": opportunity_ref},
        )

    async def latest_for_legal_entity(
        self, tenant: str, legal_entity_ref: str
    ) -> LegitimacyScreenRecord | None:
        return await self._read_one(
            # nosemgrep: avoid-sqlalchemy-text # fonte: axis = coluna literal nesta linha
            text(_SELECT_LATEST_TEMPLATE.format(axis="legal_entity_ref")),
            {"tenant": tenant, "axis_value": legal_entity_ref},
        )

    async def _read_one(self, statement: Any, params: dict[str, Any]) -> LegitimacyScreenRecord | None:
        try:
            async with self._engine.connect() as db:
                row = (await db.execute(statement, params)).mappings().one_or_none()
        except Exception:
            raise LegitimacyScreenStoreError("source_unavailable") from None
        return None if row is None else _record_from_row(row)
