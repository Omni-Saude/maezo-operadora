"""GP11 wiring — store de supressão, taxonomia C1–C6, egresso k-anon e KPI (VW4, OP20).

Materializa os INSUMOS DPO ACEITOS PELO DONO em ato próprio (2026-10-07 —
`VW0-DECISION-REGISTER.md` §"INCORPORAÇÃO VW4-ANSWERS"; base canônica
`VW4-RATIFICATION-ANSWERS-V1.md` §GP11, sha256 `ab262f7b…`). O registro de aceite destroi a
barreira RATIFY-LATER que mantinha o envelope SP-OP-SUPP-001 em recusa permanente: este módulo
é o plano de aplicação que o envelope nomeava como PR-2.

O que cada insumo aceito vira aqui (tabela de ripple §8 do ANSWERS-V1):

- **k-anon `k=100` sobre a TUPLA C2/C6** (§4a/§6.1 — p*=1% declarada; 2× o piso ADH k=50):
  `K_ANON_FLOOR`, aplicado SOMENTE sobre o conjunto quase-identificador delimitado pela
  taxonomia (célula = contagem de linhas por valor de tupla C2/C6; célula < k ⇒ linha
  suprimida — nunca "completar" lista). `k` é PARÂMETRO: a DMN `suppression_routing` o recebe
  como input (`k_piso`) — a tabela nunca carrega a cifra.
- **Taxonomia C1–C6** (§4b) com regras MECÂNICAS de chokepoint: C1/C3/C4 proibidas ⇒ recusa
  tipada do egresso inteiro; classe DESCONHECIDA ⇒ recusa (OP20 §3 — "classe clínica ou classe
  desconhecida → recusa tipada"); C2/C6 ⇒ k-anon obrigatório sobre a tupla; C5 livre com base
  declarada, presence-only (G-CADE: nenhuma RATE atravessa payload).
- **SLA `T_total` = 15 dias úteis** (§4c/§6.3) com relógio visível: `t_deadline` COMPUTADO NO
  NASCIMENTO do registro por calendário own-code seg–sex (SEM feriados — limite documentado;
  dias úteis são cálculo de APLICAÇÃO, nunca SQL). Registro sem deadline = recusa do store
  (`SuppressionRefusalReason.DEADLINE_UNCOMPUTABLE` — fail-closed). Escalonamento mecânico:
  50% lembrete (7º dia útil — piso, nunca depois da metade), 80% encarregado (12º), 100%
  catch-all `ANALISE_HUMANA` (= `t_deadline`, 15º).
- **Gate de audiência do canal = 300** (§4a — LinkedIn matched audiences, SOURCED): exportação
  inteira recusada quando a audiência pós-k-anon fica abaixo do piso do canal — nunca se
  "completa" lista para fechar audiência.
- **KPI `phi_egress_violations == 0`** (§4e) com a definição operacional ratificada: recusa
  tipada É contenção e NÃO violação (critério 4 — "a recusa é o firewall funcionando");
  violação é conteúdo proibido que PASSOU a fronteira ou célula < k que saiu. O contador é
  presence-only (molde `platform/telemetry/vendor_activity.py` do #681 — SEM conteúdo, só
  contagens) e COMPUTADO (nunca constante).

Fronteiras deste módulo (o que ele NÃO é): não é o chokepoint de início de processo
(`mcp_cibseven/transport.py#start_process_idempotent` — camada 1 do firewall, intocado); não é
a cerca repo-wide (`scripts/ci/check_start_process_fence.py` — camada 2); é a camada 3
(verificação de classe/célula sobre lista/egresso, perna `T_verify` do SLA). Não decide honra
"por omissão": a rota `REGISTRO_HONRADO` é sempre EXPLÍCITA da DMN engine-side
(`tools/workers/suppression.py` via `CibSevenDmnTransport` — evaluator local proibido,
`dmn_transport.py:1-8`).
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from fractions import Fraction
from typing import Any, Final, Literal, NamedTuple, Protocol, Self, final

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from maezo.portal.contracts.models import OpaqueRef, Sha256Digest

__all__ = [
    "AUDIENCE_GATE_MINIMUM",
    "EGRESS_AUDIENCE_GATE_MINIMUM",
    "DeclaredField",
    "EgressExport",
    "EgressLine",
    "EscalationClock",
    "FieldClass",
    "K_ANON_FLOOR",
    "PhiEgressViolationClass",
    "PostgresSuppressionStore",
    "SLA_BUSINESS_DAYS",
    "SLA_ESCALATION_FRACTION",
    "SLA_REMINDER_FRACTION",
    "SuppressionCommand",
    "SuppressionEgressAttempt",
    "SuppressionEgressTelemetry",
    "SuppressionError",
    "SuppressionKey",
    "SuppressionRecord",
    "SuppressionRefusalReason",
    "SuppressionStore",
    "add_business_days",
    "build_contact_list",
    "prepare_egress_export",
    "suppression_ref_of",
]


# ----------------------------------------------------------------------------------
# Insumos ACEITOS pelo dono (2026-10-07, sha ab262f7b…) — as ÚNICAS cifras deste domínio.
# ----------------------------------------------------------------------------------

#: Piso k-anon do egresso (§4a, opção O-B): `Pr(re-id) <= 1/k` com p* = 1% ASSUMPTION.
#: Aplica-se sobre a TUPLA C2/C6 delimitada pela taxonomia — NUNCA sobre campo isolado
#: (efeito de composição de quase-identificadores, §4a in fine). 2× o piso ADH (k=50, SOURCED).
K_ANON_FLOOR: Final = 100

#: SLA `T_total` de honra de supressão, em DIAS ÚTEIS (§4c, opção O-A): âncora CCPA opt-out
#: (<= 15 business days, SOURCED). Política do DPO citável como tal — a LGPD NÃO impõe prazo
#: para supressão (fato 3) e a ANPD não fornece âncora (fato 4, NEGATIVE-FINDING).
SLA_BUSINESS_DAYS: Final = 15

#: Fração do SLA do LEMBRETE (§4c: "em 50% do SLA ⇒ lembrete"). 50% de 15 dias úteis = 7,5 ⇒
#: piso inteiro 7 (o lembrete nunca fica DEPOIS da metade do prazo — direção conservadora).
SLA_REMINDER_FRACTION: Final = Fraction(1, 2)

#: Fração do SLA da ESCALADA AO ENCARGADO (§4c: "em 80% ⇒ escala ao encarregado").
#: 80% de 15 dias úteis = 12 dias úteis exatos (Fraction — sem drift de float).
SLA_ESCALATION_FRACTION: Final = Fraction(4, 5)

#: Gate de audiência do canal (§4a — LinkedIn "Minimum audience count of 300", SOURCED):
#: exportação INTEIRA recusada quando a audiência pós-supressão fica abaixo deste piso.
#: Gate distinto e CUMULATIVO do gate de LINHA (k): o k protege cada registro; este protege a
#: audiência que o canal receptor exige. Recusar é o comportamento correto — nunca completar.
EGRESS_AUDIENCE_GATE_MINIMUM: Final = 300

#: Alias explícito usado pelo plano de lista (mesmo número, semântica de audiência).
AUDIENCE_GATE_MINIMUM: Final = EGRESS_AUDIENCE_GATE_MINIMUM


# ----------------------------------------------------------------------------------
# Taxonomia C1–C6 (§4b — ratificada CLASSE a CLASSE; regras mecânicas de chokepoint)
# ----------------------------------------------------------------------------------


@final
class FieldClass(StrEnum):
    """Classe de campo clínico×contratual (§4b) — token DECLARADO, nunca inferido de conteúdo.

    G-PHI absoluto: a classe é metadado estrutural da lista/egresso; nenhum valor de campo
    chega a este módulo (o que chega é o token da classe que o construtor declarou). Classe
    fora do catálogo fechado (ou ausente) é recusa — OP20 §3: "campo de classe clínica ou
    classe desconhecida → recusa tipada, zero efeito a jusante".
    """

    #: C1 — identificativo direto (nome, CPF, e-mail, telefone, conta, ID de lead).
    #: PROIBIDO no egresso em qualquer granularidade; permitido APENAS dentro do registro de
    #: supressão minimizado (art. 10 §1º).
    C1_IDENTIFICATIVO = "C1"
    #: C2 — quase-identificador contratual (matrícula, CEP+nascimento, produto+vigência+corretor).
    #: k-anon OBRIGATÓRIO sobre a tupla.
    C2_QUASE_IDENTIFICADOR = "C2"
    #: C3 — clínico-protegido (CID, procedimento, DUT, CRM, nº de autorização, qualquer campo de
    #: saúde). PROIBIDO como input de targeting e no egresso, em qualquer granularidade
    #: (G-PHI; art. 11 §5º) — sem exceção "com consentimento" na v1.
    C3_CLINICO_PROTEGIDO = "C3"
    #: C4 — clínico-agregado (contagem de autorizações por família de procedimento). PROIBIDO na
    #: v1 (default); célula-mínima é extensão FUTURA com contrato próprio — nunca relabel.
    C4_CLINICO_AREGREGADO = "C4"
    #: C5 — comportamental-comercial (cliques, eventos de portal, leads sem dado de saúde).
    #: Livre com base declarada; presence-only; nenhuma RATE atravessa payload (G-CADE).
    C5_COMPORTAMENTAL = "C5"
    #: C6 — contratual-administrativo (vigência, preço, status de contrato, produção por
    #: carteira). k-anon na saída de audiência; agregação POR CARTEIRA, nunca per-contrato.
    C6_CONTRATUAL_ADMINISTRATIVO = "C6"


#: Classes PROIBIDAS no egresso/targeting na v1 (§4b): C1 fora do registro minimizado; C3 pelo
#: G-PHI absoluto + art. 11 §5º; C4 proibido na v1 (presença trata-se como C3 no chokepoint).
PROHIBITED_FIELD_CLASSES: Final[frozenset[FieldClass]] = frozenset(
    {
        FieldClass.C1_IDENTIFICATIVO,
        FieldClass.C3_CLINICO_PROTEGIDO,
        FieldClass.C4_CLINICO_AREGREGADO,
    }
)

#: Classes sujeitas ao k-anon de TUPLA (§4b: C2 e a perna C6 por carteira — o mesmo guard do
#: "livro mínimo" do GP6: célula com < k sujeitos não é publicável nem mensurável).
TUPLE_K_ANON_FIELD_CLASSES: Final[frozenset[FieldClass]] = frozenset(
    {FieldClass.C2_QUASE_IDENTIFICADOR, FieldClass.C6_CONTRATUAL_ADMINISTRATIVO}
)


# ----------------------------------------------------------------------------------
# Recusas tipadas (o firewall é a recusa — §4e critério 4: recusa ≠ violação)
# ----------------------------------------------------------------------------------


@final
class SuppressionRefusalReason(StrEnum):
    """Vocabulário fechado de recusa do domínio GP11 (registry OP20 + insumos aceitos)."""

    #: Registro sem deadline computável — o store RECUSA (§4c: registro sem deadline não é aceito).
    DEADLINE_UNCOMPUTABLE = "DEADLINE_UNCOMPUTABLE"
    #: Store ausente/ilegível — UNKNOWN, nunca zero (molde `erasure_plan.py#IdentityResolution`).
    SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"
    #: Conteúdo declarado fora do contrato (digest/status/args) — nunca overwrite silencioso.
    CONTRACT_MISMATCH = "CONTRACT_MISMATCH"
    #: Campo de classe proibida (C1/C3/C4) em payload de egresso/targeting — G-PHI absoluto.
    PHI_IN_COMMERCIAL_INPUT = "PHI_IN_COMMERCIAL_INPUT"
    #: Classe fora do catálogo fechado C1–C6 — recusa, nunca classificação por inferência.
    UNKNOWN_FIELD_CLASS = "UNKNOWN_FIELD_CLASS"
    #: Audiência pós-k-anon abaixo do piso do canal (300) — exportação INTEIRA recusada (§4a).
    AUDIENCE_BELOW_GATE = "AUDIENCE_BELOW_GATE"


class SuppressionError(PermissionError):
    """Recusa tipada do domínio GP11 — sempre com razão fechada, nunca texto de causa cruzado."""

    def __init__(self, reason: SuppressionRefusalReason) -> None:
        self.reason = reason
        super().__init__(reason.value)


def require(condition: bool, reason: SuppressionRefusalReason) -> None:
    if not condition:
        raise SuppressionError(reason)


# ----------------------------------------------------------------------------------
# Calendário own-code de dias úteis (§4c) — seg–sex, SEM feriados (limite documentado)
# ----------------------------------------------------------------------------------


def add_business_days(start: datetime, days: int) -> datetime:
    """Soma `days` dias úteis (seg–sex) a `start`, preservando o horário (TZ-aware obrigatório).

    LIMITE DOCUMENTADO: o calendário é own-code SIMPLES — sábados e domingos são não-úteis e
    NENHUM feriado (nacional, estadual ou corporativo) é observado. O SLA aceito é política do
    DPO (§4c — a LGPD não impõe prazo para supressão, fato 3); feriados exigiriam tabela
    custodiada (RATIFY-LATER) e ninguém a inventa aqui. Sob-contagem de dias úteis só pode
    ENCURTAR o prazo percebido pelo titular (direção conservadora para o titular).
    """
    require(days >= 0, SuppressionRefusalReason.DEADLINE_UNCOMPUTABLE)
    aware = start.tzinfo is not None and start.utcoffset() is not None
    require(aware, SuppressionRefusalReason.DEADLINE_UNCOMPUTABLE)
    current = start
    remaining = days
    while remaining > 0:
        current = current + timedelta(days=1)
        if current.weekday() < 5:  # 0=segunda … 4=sexta; 5=sábado, 6=domingo
            remaining -= 1
    return current


def _fraction_days(sla_days: int, fraction: Fraction, *, mode: Literal["floor", "exact"]) -> int:
    """Dias úteis de uma fração do SLA — aritmética exata (Fraction), sem drift de float."""
    total = Fraction(sla_days) * fraction
    if mode == "floor":
        return math.floor(total)
    require(total.denominator == 1, SuppressionRefusalReason.DEADLINE_UNCOMPUTABLE)
    return total.numerator


@final
@dataclass(frozen=True, slots=True)
class EscalationClock:
    """Relógio visível de UM registro (§4c) — computado UMA vez, no nascimento.

    `t_deadline` é a âncora 100%; `t_lembrete` (50% — piso, nunca depois da metade) e
    `t_escalonado` (80%) derivam do MESMO calendário. Os timers do BPMN são `timeDate`
    absolutos sobre estas datas (idioma CONTAS GAP-4: a âncora é o fato gerador, nunca o
    attach da User Task) — nada aqui dispara nada: disparar é do engine.
    """

    t_recorded: datetime
    t_lembrete: datetime
    t_escalonado: datetime
    t_deadline: datetime

    @classmethod
    def compute(cls, t_recorded: datetime) -> Self:
        """Computa o relógio de um registro nascido em `t_recorded` (TZ-aware obrigatório).

        100% do SLA = 15º dia útil; 80% = 12º (exato); 50% = 7º (piso de 7,5 — o lembrete
        nunca fica depois da metade do prazo). Ordem estrutural garantida:
        `t_recorded < t_lembrete <= t_escalonado < t_deadline`.
        """
        require(t_recorded.tzinfo is not None, SuppressionRefusalReason.DEADLINE_UNCOMPUTABLE)
        t_deadline = add_business_days(t_recorded, SLA_BUSINESS_DAYS)
        reminder_days = _fraction_days(SLA_BUSINESS_DAYS, SLA_REMINDER_FRACTION, mode="floor")
        escalation_days = _fraction_days(SLA_BUSINESS_DAYS, SLA_ESCALATION_FRACTION, mode="exact")
        clock = cls(
            t_recorded=t_recorded,
            t_lembrete=add_business_days(t_recorded, reminder_days),
            t_escalonado=add_business_days(t_recorded, escalation_days),
            t_deadline=t_deadline,
        )
        ordered = clock.t_lembrete <= clock.t_escalonado < clock.t_deadline
        require(ordered, SuppressionRefusalReason.DEADLINE_UNCOMPUTABLE)
        return clock


# ----------------------------------------------------------------------------------
# Contrato tipado OP20 — SuppressionCommand → SuppressionRecord
# ----------------------------------------------------------------------------------


def suppression_ref_of(subject_ref: str, contact_channel: str) -> Sha256Digest:
    """Digest canônico de `(subject_ref, contact_channel)` — a chave do registry OP20.

    Projeção fiel do par `tenant + suppression + subject_ref + contact_channel`: o digest É a
    projeção dos dois últimos componentes (BPMN SP-OP-SUPP-001, business key por NOME).
    Determinístico e estável: worker, plano de lista e store cunham o MESMO digest.
    """
    canonical = json.dumps([subject_ref, contact_channel], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class _Closed(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        strict=True,
        extra="forbid",
        hide_input_in_errors=True,
        revalidate_instances="always",
    )


class SuppressionCommand(_Closed):
    """Pedido de registro preventivo de não-contato — minimização art. 10 §1º.

    EXATAMENTE identificador + data + canal: `subject_ref` OPACO (ADR-0006), `contact_channel`
    opaco, `canal` categórico, `categoria_sujeito` DECLARADA no pedido (vendedor | lead —
    NUNCA inferida de dado clínico/comportamental; G-PHI). Nenhum outro campo existe.
    """

    schema_version: Literal["vendor-suppression-command.v1"] = "vendor-suppression-command.v1"
    tenant: OpaqueRef
    subject_ref: OpaqueRef
    contact_channel: OpaqueRef
    canal: str = Field(min_length=1, max_length=64)
    categoria_sujeito: Literal["vendedor", "lead"]
    motivo: str = Field(min_length=1, max_length=512)

    @property
    def suppression_ref(self) -> Sha256Digest:
        return suppression_ref_of(self.subject_ref, self.contact_channel)


class SuppressionRecord(_Closed):
    """Registro nascido COM relógio — a forma que o store devolve (nunca existe sem prazo)."""

    schema_version: Literal["vendor-suppression-record.v1"] = "vendor-suppression-record.v1"
    tenant: OpaqueRef
    suppression_ref: Sha256Digest
    subject_ref: OpaqueRef
    contact_channel: OpaqueRef
    canal: str
    categoria_sujeito: Literal["vendedor", "lead"]
    t_recorded: datetime
    t_lembrete: datetime
    t_escalonado: datetime
    t_deadline: datetime
    status: Literal["pendente", "honrado", "escalado"] = "pendente"
    motivo: str

    @field_validator("t_recorded", "t_lembrete", "t_escalonado", "t_deadline")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        require(
            value.tzinfo is not None and value.utcoffset() is not None,
            SuppressionRefusalReason.DEADLINE_UNCOMPUTABLE,
        )
        return value

    @model_validator(mode="after")
    def _clock_order(self) -> Self:
        require(
            self.t_recorded <= self.t_lembrete <= self.t_escalonado < self.t_deadline,
            SuppressionRefusalReason.CONTRACT_MISMATCH,
        )
        require(
            self.suppression_ref == suppression_ref_of(self.subject_ref, self.contact_channel),
            SuppressionRefusalReason.CONTRACT_MISMATCH,
        )
        return self


@final
class SuppressionKey(NamedTuple):
    """Par opaco (subject_ref, contact_channel) — a unidade de honra na construção de lista."""

    subject_ref: OpaqueRef
    contact_channel: OpaqueRef


class SuppressionStore(Protocol):
    """Fonte de supressão — o seam `suppression_store` do worker (molde vendor_admin).

    Ausência de linha é UNKNOWN (nunca "ninguém suprimido"); ausência do STORE é recusa tipada
    (`SOURCE_UNAVAILABLE`) — nunca um conjunto vazio sobre o qual se age.
    """

    async def record(self, command: SuppressionCommand) -> SuppressionRecord:
        """Nasce o registro COM relógio computado no INSERT (§4c); reenvio = mesmo registro."""
        ...

    async def resolve(self, tenant: str, suppression_ref: Sha256Digest) -> SuppressionRecord | None:
        """Resolve um registro pelo digest; ausência = UNKNOWN (`None`), nunca um fabricado."""
        ...

    async def active_suppressions(self, tenant: str) -> frozenset[SuppressionKey]:
        """Chaves de supressão VIGENTES do tenant — a consulta da construção de lista.

        Semântica preventiva (art. 7º IX + 18 §2º): a oposição EFEITIVA nasce com o registro —
        o status governa o ciclo do relógio/escalonamento, não a prevenção.
        """
        ...

    async def set_status(
        self, tenant: str, suppression_ref: Sha256Digest, status: Literal["honrado", "escalado"], motivo: str
    ) -> SuppressionRecord:
        """Transição fechada de status do relógio; registro inexistente = recusa tipada."""
        ...


# ----------------------------------------------------------------------------------
# Honra na construção de lista + egresso k-anon (chokepoint da camada 3 — §4b/§4a)
# ----------------------------------------------------------------------------------


@final
class SuppressionEgressAttempt(StrEnum):
    """Catálogo FECHADO de tentativas de egresso/uso de lista — presence-only, ZERO conteúdo.

    Espelha `VendorActivitySignal` (#681): contagens agregadas, nunca medida, nunca payload.
    Recusa tipada É contenção — conta AQUI (visibilidade do firewall), nunca no KPI de
    violação (§4e critério 4).
    """

    #: Linha/registro removido da construção de lista por supressão vigente.
    LIST_ROW_REMOVED = "list_row_removed"
    #: Egresso recusado por classe de TUPLA (C2/C6) declarada SEM tuple_value — o
    #: quase-identificador escaparia da contagem de célula (§4b: k aplica-se à TUPLA).
    EGRESS_REFUSED_TUPLE_CLASS_WITHOUT_TUPLE = "egress_refused_tuple_class_without_tuple"
    #: Egresso recusado por classe proibida (C1/C3/C4) no payload.
    EGRESS_REFUSED_PROHIBITED_CLASS = "egress_refused_prohibited_class"
    #: Egresso recusado por classe fora do catálogo fechado.
    EGRESS_REFUSED_UNKNOWN_CLASS = "egress_refused_unknown_class"
    #: Linha suprimida por célula de tupla C2/C6 < k (o k-anon a funcionar).
    EGRESS_LINE_SUPPRESSED_SUBK = "egress_line_suppressed_subk"
    #: Exportação inteira recusada: audiência pós-k-anon abaixo do piso do canal (300).
    EGRESS_REFUSED_AUDIENCE_GATE = "egress_refused_audience_gate"


@final
class PhiEgressViolationClass(StrEnum):
    """Catálogo FECHADO de VIOLAÇÃO (§4e critérios 1–4) — o que `phi_egress_violations` conta.

    Recusa tipada NÃO está aqui (contenção ≠ violação). Cada membro cita a fonte da contagem:
    camada 1/3 (conteúdo proibido que PASSOU a fronteira), célula < k que saiu, cerca
    repo-wide achando egresso fora do chokepoint sancionado.
    """

    #: Critério 1 — C1 publicado fora do registro de supressão minimizado.
    C1_PAST_BOUNDARY = "c1_past_boundary"
    #: Critério 2 — C3 (ou C4, proibido na v1) em payload publicado/input de targeting.
    PROHIBITED_CLASS_PAST_BOUNDARY = "prohibited_class_past_boundary"
    #: Critério 3 — linha exportada com tupla C2/C6 em célula < k.
    SUBK_CELL_EXPORTED = "subk_cell_exported"
    #: Critério 4 — egresso fora do chokepoint sancionado (achado de cerca/camada 2).
    EGRESS_OUTSIDE_CHOKEPOINT = "egress_outside_chokepoint"


@final
class SuppressionEgressTelemetry:
    """Contadores presence-only do domínio GP11 (molde #681 — SEM conteúdo, só contagens).

    Duas famílias fechadas: TENTATIVAS (firewall a funcionar — contenção) e VIOLAÇÕES
    (§4e). A superfície de escrita são exatamente dois métodos, cada um aceitando UM membro do
    catálogo (posição-única; `type(...) is not` — string solta é recusada, é por onde conteúdo
    disfarçado entraria). Nenhum parâmetro `**kwargs`/payload existe — a ausência é o firewall.
    """

    __slots__ = ("_attempts", "_violations")

    def __init__(self) -> None:
        self._attempts: dict[str, int] = {}
        self._violations: dict[str, int] = {}

    def record_attempt(self, attempt: SuppressionEgressAttempt, /) -> None:
        """Conta UMA contenção/tentativa. String solta = `ContentSmugglingError` (molde #681)."""
        if type(attempt) is not SuppressionEgressAttempt:  # recusa de str solta É o objetivo
            raise TypeError(
                "contador presence-only aceita apenas um membro do catálogo fechado "
                "SuppressionEgressAttempt; nenhum payload, conteúdo ou string solta tem entrada aqui"
            )
        key = attempt.value
        self._attempts[key] = self._attempts.get(key, 0) + 1

    def record_violation(self, violation: PhiEgressViolationClass, /) -> None:
        """Conta UMA VIOLAÇÃO §4e observada (cabe ao chamador provar que PASSOU a fronteira)."""
        if type(violation) is not PhiEgressViolationClass:
            raise TypeError(
                "contador presence-only aceita apenas um membro do catálogo fechado "
                "PhiEgressViolationClass; nenhum payload, conteúdo ou string solta tem entrada aqui"
            )
        key = violation.value
        self._violations[key] = self._violations.get(key, 0) + 1

    @property
    def phi_egress_violations(self) -> int:
        """KPI §4e — COMPUTADO, nunca constante: somatório das violações registradas na janela.

        Entrada irregular do estado interno (chave fora do catálogo, valor não-`int` exato,
        negativo) conta como violação na MESMA varredura — corruptar o estado nunca esconde a
        irregularidade (molde #681 `_phi_egress_violations`).
        """
        catalog = {v.value for v in PhiEgressViolationClass}
        total = 0
        for key, value in self._violations.items():
            if key not in catalog or type(value) is not int or value < 0:
                total += 1
                continue
            total += value
        return total

    def window(self) -> Mapping[str, int]:
        """Contagens presence-only da janela (tentativas + violações), catálogo completo."""
        counts: dict[str, int] = {a.value: 0 for a in SuppressionEgressAttempt}
        counts.update({v.value: 0 for v in PhiEgressViolationClass})
        for source in (self._attempts, self._violations):
            for key, value in source.items():
                if key in counts and type(value) is int and value >= 0:
                    counts[key] = counts.get(key, 0) + value
        return {key: counts[key] for key in sorted(counts)}

    def reset(self) -> None:
        """Zera a janela (rotação). Não preserva histórico — aqui não há histórico."""
        self._attempts.clear()
        self._violations.clear()


@final
@dataclass(frozen=True, slots=True)
class DeclaredField:
    """Campo de linha de egresso com sua classe DECLARADA — nome é opaco, classe é token."""

    name: str
    field_class: FieldClass | None  # None = classe não declarada ⇒ recusa (UNKNOWN_FIELD_CLASS)


@final
@dataclass(frozen=True, slots=True)
class EgressLine:
    """Linha candidata à exportação: chave opaca do sujeito + campos com classe declarada."""

    key: SuppressionKey
    fields: tuple[DeclaredField, ...]
    tuple_value: tuple[str, ...] = ()
    """Valor da TUPLA quase-identificadora (C2/C6) desta linha — célula = contagem de linhas
    com o MESMO `tuple_value`. Vazio ⇒ a linha não participa de célula (só campos livres)."""


@final
@dataclass(frozen=True, slots=True)
class EgressExport:
    """Resultado de um egresso que PASSOU o chokepoint — o que pode, de fato, sair."""

    exported: tuple[EgressLine, ...]
    suppressed_lines: int
    below_gate_cells: int
    audience_size: int
    k: int


def prepare_egress_export(
    lines: Sequence[EgressLine],
    *,
    audience_size: int,
    k: int = K_ANON_FLOOR,
    telemetry: SuppressionEgressTelemetry | None = None,
) -> EgressExport:
    """Chokepoint de egresso vendor (§4b/§4a) — recusa tipada, linha suprimida, gate de audiência.

    Ordem mecânica (§4a, exemplo trabalhado): (1) classe proibida/desconhecida em QUALQUER
    campo ⇒ recusa do egresso INTEIRO (`PHI_IN_COMMERCIAL_INPUT`/`UNKNOWN_FIELD_CLASS`) — o
    firewall funciona, contenção não é violação; (2) k-anon sobre a TUPLA C2/C6 — célula < k ⇒
    LINHA suprimida (nunca se completa lista); (3) gate de audiência do canal — audiência
    pós-k-anon < 300 ⇒ exportação INTEIRA recusada (`AUDIENCE_BELOW_GATE`).
    """
    require(audience_size >= 0 and type(audience_size) is int, SuppressionRefusalReason.CONTRACT_MISMATCH)
    require(type(k) is int and k > 0, SuppressionRefusalReason.CONTRACT_MISMATCH)
    telemetry = telemetry if telemetry is not None else SuppressionEgressTelemetry()

    # (1) taxonomia: proibida ou desconhecida em qualquer campo ⇒ recusa do egresso inteiro.
    for line in lines:
        for field in line.fields:
            if field.field_class is None:
                telemetry.record_attempt(SuppressionEgressAttempt.EGRESS_REFUSED_UNKNOWN_CLASS)
                raise SuppressionError(SuppressionRefusalReason.UNKNOWN_FIELD_CLASS)
            if field.field_class in PROHIBITED_FIELD_CLASSES:
                telemetry.record_attempt(SuppressionEgressAttempt.EGRESS_REFUSED_PROHIBITED_CLASS)
                raise SuppressionError(SuppressionRefusalReason.PHI_IN_COMMERCIAL_INPUT)

    # (2a) consistência classe×tupla (§4b): campo declarado C2/C6 SEM tuple_value deixaria o
    # quase-identificador FORA da contagem de célula (a linha viraria "campo livre" e nunca
    # seria suprimida) — recusa tipada do egresso INTEIRO, loud, nunca silêncio (achado do
    # guard-test-engineer, fechado no ato).
    for line in lines:
        if not line.tuple_value and any(
            field.field_class in TUPLE_K_ANON_FIELD_CLASSES for field in line.fields
        ):
            telemetry.record_attempt(SuppressionEgressAttempt.EGRESS_REFUSED_TUPLE_CLASS_WITHOUT_TUPLE)
            raise SuppressionError(SuppressionRefusalReason.CONTRACT_MISMATCH)

    # (2b) k-anon sobre a TUPLA C2/C6: célula = contagem de linhas com o mesmo tuple_value.
    cell_sizes: dict[tuple[str, ...], int] = {}
    for line in lines:
        if line.tuple_value:
            cell_sizes[line.tuple_value] = cell_sizes.get(line.tuple_value, 0) + 1
    exported: list[EgressLine] = []
    suppressed = 0
    below_gate_cells = 0
    for line in lines:
        size = cell_sizes.get(line.tuple_value, 0) if line.tuple_value else k
        if size < k:
            suppressed += 1
            below_gate_cells += 1
            telemetry.record_attempt(SuppressionEgressAttempt.EGRESS_LINE_SUPPRESSED_SUBK)
            continue
        exported.append(line)

    # (3) gate de audiência do canal (§4a): recusa a exportação INTEIRA — nunca completar lista.
    if len(exported) < EGRESS_AUDIENCE_GATE_MINIMUM or audience_size < EGRESS_AUDIENCE_GATE_MINIMUM:
        telemetry.record_attempt(SuppressionEgressAttempt.EGRESS_REFUSED_AUDIENCE_GATE)
        raise SuppressionError(SuppressionRefusalReason.AUDIENCE_BELOW_GATE)

    return EgressExport(
        exported=tuple(exported),
        suppressed_lines=suppressed,
        below_gate_cells=below_gate_cells,
        audience_size=audience_size,
        k=k,
    )


async def build_contact_list(
    tenant: str,
    candidate_keys: Sequence[SuppressionKey],
    *,
    store: SuppressionStore | None,
    telemetry: SuppressionEgressTelemetry | None = None,
) -> tuple[SuppressionKey, ...]:
    """Construção de lista COM honra preventiva do registro de supressão (OP20 binding).

    Store ausente = `SOURCE_UNAVAILABLE` — UNKNOWN nunca é um conjunto vazio sobre o qual se
    age (nunca "ninguém está suprimido"): a lista NÃO é construída. Linhas de sujeitos
    suprimidos NUNCA entram (ordem dos candidatos preservada); cada remoção é contada
    presence-only. Trabalha apenas sobre chaves opacas — nenhum conteúdo de pessoa existe aqui.
    """
    telemetry = telemetry if telemetry is not None else SuppressionEgressTelemetry()
    if store is None:
        raise SuppressionError(SuppressionRefusalReason.SOURCE_UNAVAILABLE)
    try:
        suppressed = await store.active_suppressions(tenant)
    except SuppressionError:
        raise
    except Exception:
        # Nem texto de driver/DB cruza a fronteira: a fonte ilegível é SOURCE_UNAVAILABLE.
        raise SuppressionError(SuppressionRefusalReason.SOURCE_UNAVAILABLE) from None
    blocked = frozenset(suppressed)
    kept: list[SuppressionKey] = []
    for key in candidate_keys:
        if key in blocked:
            telemetry.record_attempt(SuppressionEgressAttempt.LIST_ROW_REMOVED)
            continue
        kept.append(key)
    return tuple(kept)


# ----------------------------------------------------------------------------------
# Store concreto (migration 0021) — PostgreSQL, mesmo padrão do adapter VW1-P0
# ----------------------------------------------------------------------------------

_INSERT_SQL = text("""
    INSERT INTO portal_vendor_suppressions (
        tenant, suppression_ref, subject_ref, contact_channel, canal, categoria_sujeito,
        t_recorded, t_deadline, t_lembrete, t_escalonado, status, motivo
    ) VALUES (
        :tenant, :suppression_ref, :subject_ref, :contact_channel, :canal, :categoria_sujeito,
        :t_recorded, :t_deadline, :t_lembrete, :t_escalonado, 'pendente', :motivo
    )
    ON CONFLICT (tenant, suppression_ref) DO NOTHING
""")

_SELECT_SQL = text("""
    SELECT tenant, suppression_ref, subject_ref, contact_channel, canal, categoria_sujeito,
           t_recorded, t_deadline, t_lembrete, t_escalonado, status, motivo
      FROM portal_vendor_suppressions
     WHERE tenant = :tenant AND suppression_ref = :suppression_ref
""")

_ACTIVE_SQL = text("""
    SELECT subject_ref, contact_channel
      FROM portal_vendor_suppressions
     WHERE tenant = :tenant
""")

_STATUS_SQL = text("""
    UPDATE portal_vendor_suppressions
       SET status = :status, motivo = :motivo
     WHERE tenant = :tenant AND suppression_ref = :suppression_ref
""")


class PostgresSuppressionStore:
    """`SuppressionStore` sobre a migration 0021 — relógio computado AQUI, no INSERT.

    O deadline NUNCA é parâmetro do chamador: `record` computa o `EscalationClock` no ato
    (§4c — "t_deadline = t_recorded + SLA" no nascimento). Calendário falho ⇒ recusa
    `DEADLINE_UNCOMPUTABLE` ANTES de qualquer I/O. Reenvio da mesma chave devolve o registro
    JÁ EXISTENTE (ON CONFLICT DO NOTHING + re-leitura — "mesmo pedido repetido = mesmo
    registro, nunca contagem dupla de SLA", registry OP20).
    """

    def __init__(self, engine: AsyncEngine) -> None:
        require(
            engine.dialect.name == "postgresql" and not engine.echo,
            SuppressionRefusalReason.SOURCE_UNAVAILABLE,
        )
        self._engine = engine

    async def record(self, command: SuppressionCommand) -> SuppressionRecord:
        try:
            command = SuppressionCommand.model_validate(command.model_dump(mode="python"))
            t_recorded = datetime.now(UTC)
            clock = EscalationClock.compute(t_recorded)  # recusa ANTES de qualquer I/O
            async with self._engine.begin() as db:
                await db.execute(
                    _INSERT_SQL,
                    {
                        "tenant": command.tenant,
                        "suppression_ref": command.suppression_ref,
                        "subject_ref": command.subject_ref,
                        "contact_channel": command.contact_channel,
                        "canal": command.canal,
                        "categoria_sujeito": command.categoria_sujeito,
                        "t_recorded": clock.t_recorded,
                        "t_deadline": clock.t_deadline,
                        "t_lembrete": clock.t_lembrete,
                        "t_escalonado": clock.t_escalonado,
                        "motivo": command.motivo,
                    },
                )
                row = (
                    (
                        await db.execute(
                            _SELECT_SQL,
                            {"tenant": command.tenant, "suppression_ref": command.suppression_ref},
                        )
                    )
                    .mappings()
                    .one()
                )
            return _record_from_row(row)
        except SuppressionError:
            raise
        except Exception:
            # Nem texto de driver/DB cruza a fronteira: a fonte ilegível é SOURCE_UNAVAILABLE.
            raise SuppressionError(SuppressionRefusalReason.SOURCE_UNAVAILABLE) from None

    async def resolve(self, tenant: str, suppression_ref: Sha256Digest) -> SuppressionRecord | None:
        try:
            async with self._engine.connect() as db:
                row = (
                    (await db.execute(_SELECT_SQL, {"tenant": tenant, "suppression_ref": suppression_ref}))
                    .mappings()
                    .one_or_none()
                )
            return None if row is None else _record_from_row(row)
        except SuppressionError:
            raise
        except Exception:
            raise SuppressionError(SuppressionRefusalReason.SOURCE_UNAVAILABLE) from None

    async def active_suppressions(self, tenant: str) -> frozenset[SuppressionKey]:
        try:
            async with self._engine.connect() as db:
                rows = (await db.execute(_ACTIVE_SQL, {"tenant": tenant})).mappings().all()
            return frozenset(
                SuppressionKey(subject_ref=row["subject_ref"], contact_channel=row["contact_channel"])
                for row in rows
            )
        except SuppressionError:
            raise
        except Exception:
            raise SuppressionError(SuppressionRefusalReason.SOURCE_UNAVAILABLE) from None

    async def set_status(
        self, tenant: str, suppression_ref: Sha256Digest, status: Literal["honrado", "escalado"], motivo: str
    ) -> SuppressionRecord:
        try:
            require(bool(motivo.strip()), SuppressionRefusalReason.CONTRACT_MISMATCH)
            async with self._engine.begin() as db:
                result = await db.execute(
                    _STATUS_SQL,
                    {
                        "tenant": tenant,
                        "suppression_ref": suppression_ref,
                        "status": status,
                        "motivo": motivo,
                    },
                )
                require(result.rowcount == 1, SuppressionRefusalReason.CONTRACT_MISMATCH)
                row = (
                    (await db.execute(_SELECT_SQL, {"tenant": tenant, "suppression_ref": suppression_ref}))
                    .mappings()
                    .one()
                )
            return _record_from_row(row)
        except SuppressionError:
            raise
        except Exception:
            raise SuppressionError(SuppressionRefusalReason.SOURCE_UNAVAILABLE) from None


def _record_from_row(row: Any) -> SuppressionRecord:
    """Hidrata o registro — o banco NUNCA devolve linha sem prazo (NOT NULL da 0021).

    `row` é o `RowMapping` do SQLAlchemy (dialect-postgresql: timestamps já `datetime`
    aware, textos já `str`). O pydantic `strict=True` do modelo fechado é a validação REAL
    daqui para baixo: linha malformada vira `ValidationError` ⇒ `SOURCE_UNAVAILABLE`.
    """
    return SuppressionRecord(
        tenant=row["tenant"],
        suppression_ref=row["suppression_ref"],
        subject_ref=row["subject_ref"],
        contact_channel=row["contact_channel"],
        canal=row["canal"],
        categoria_sujeito=row["categoria_sujeito"],
        t_recorded=row["t_recorded"],
        t_deadline=row["t_deadline"],
        t_lembrete=row["t_lembrete"],
        t_escalonado=row["t_escalonado"],
        status=row["status"],
        motivo=row["motivo"],
    )
