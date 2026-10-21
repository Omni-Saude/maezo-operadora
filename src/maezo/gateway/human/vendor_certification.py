"""GP5 wiring — certificação scoreada do canal (PAT-03; VW4, ratificado 2026-10-09).

Materializa o instrumento `GP5-CERT-V1` (VW4-RATIFICATION-ANSWERS-V1 §GP5, sha
`ab262f7b…`, ratificado pelo dono 2026-10-09 "todos confirmados, pode prosseguir"):
o score S de 6 dimensões com pesos w=(0.25,0.20,0.20,0.15,0.10,0.10), a regra dos
30% com cap PROBATION, os cortes θ_admit=70/θ_probation=50 (provisórios até a
cohort de 20), o veto booleano por evento e o regime de vigência lapse/W=60/g=15
com t_expiry de s1 = 12 meses.

Separação de autoridade (§4e — a condição ii do dossiê): este módulo é o
SCOREADOR — computa S, aplica θ's e devolve `faixa`. Ele NUNCA determina admissão
(a escolha é humana: dono de canal para admissão, Jurídico-Produtos para veto),
nunca emite vocabulário `LIBERAR/AUTORIZAR/PAGAR/CALCULAR` (a saída é sempre
`FAIXA_ADMISSIVEL | FAIXA_PROBATION | FAIXA_INADMISSIVEL | ANALISE_HUMANA`), e
nunca toca qualquer superfície de vínculo trabalhista — o desagendamento por
lapse é ato comercial no plano de publicação da audiência vendor (store 0019 /
`vendor_membership_administration.py`), NUNCA efeito no vínculo (garantia
textual do instrumento; VW0-D13 referenciado, nunca reaberto).

Fail-closed (a inércia honesta do §4c): dimensão sem insumo apurável é
`UNMEASURED` (excluída do denominador pela regra dos 30%); canal SEM insumos
suficientes (fração de peso indisponível > 0.30 ⇒ cap PROBATION; sem NENHUM
insumo ⇒ `ANALISE_HUMANA`) — nunca zero fabricado, nunca admissão por omissão.
Veto booleano ativo ⇒ `FAIXA_INADMISSIVEL` qualquer que seja S (§4b: a escada
do art. 21 da Lei 4.594 é disparada por EVENTO, não por média).

PHI (gate negativo 1): nenhum insumo clínico ou per-beneficiário entra no score
— s3 é o agregado atuarial read-only da carteira (nunca por contrato); o gate
de seguro-saúde (Lei 4.594/SUSEP) é instrumento próprio (VW0-D-canal,
RATIFY-LATER) e não é atalhado aqui.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Final, Self

#: Pesos w das 6 dimensões (§4a — ASSUMPTION ratificada como provisória; Σw = 1.00).
DIMENSION_WEIGHTS: Final[dict[str, float]] = {
    "s1_credenciais_pessoas": 0.25,
    "s2_producao_evidenciada": 0.20,
    "s3_sinistralidade_carteira": 0.20,
    "s4_conduta_compliance": 0.15,
    "s5_adesao_documental": 0.10,
    "s6_tenure_sustentacao": 0.10,
}

#: N_min de acreditados (§4a s1 — ASSUMPTION; calibrada no piso AWS Select).
CREDENTIAL_MIN_INDIVIDUALS: Final[int] = 2
#: Piso de contratos ativos para s2 saturar (§4a — ASSUMPTION).
ACTIVE_CONTRACTS_FLOOR: Final[int] = 3
#: Meses de tenure para s6 saturar (§4a — ASSUMPTION).
TENURE_MONTHS_FULL: Final[int] = 6
#: Corredor de sinistralidade do s3 (§4a — DERIVED do corredor OWNER-SOURCED GP6).
SINISTRALITY_CEILING: Final[float] = 0.85
SINISTRALITY_TARGET: Final[float] = 0.75
#: Sub-pesos dos checklists s4/s5 (§4a — ASSUMPTION).
S4_CHECKLIST: Final[dict[str, int]] = {"rn529_cadastro": 40, "reclamacoes": 30, "sancao": 30}
S5_CHECKLIST: Final[dict[str, int]] = {"contrato": 40, "politica_conteudo_gp8": 30, "cadastrais": 30}

#: Cortes (§4b — provisórios até a cohort de calibração de 20 canais).
THETA_ADMIT: Final[float] = 70.0
THETA_PROBATION: Final[float] = 50.0
#: Regra dos 30% (§4a R6): fração de peso indisponível acima deste teto ⇒ cap PROBATION.
UNMEASURED_WEIGHT_CAP: Final[float] = 0.30

#: Vigência (§4d — W DERIVED da RN 557/2022 art. 10 §1º; g e t_expiry conforme instrumento).
RENEWAL_NOTICE_DAYS: Final[int] = 60  # W — notificações em t_expiry−60/−30/−7
RETURN_GRACE_DAYS: Final[int] = 15  # g — SOMENTE caminho de retorno, nunca mantém venda
CREDENTIAL_VALIDITY_MONTHS: Final[int] = 12  # t_expiry de s1 (RN 557 art. 10 caput, anual)


class CertificationFaixa(StrEnum):
    """Vocabulário FECHADO de saída do scoreador (§4f) — jamais verbo de decisão."""

    ADMITIDO = "FAIXA_ADMISSIVEL"
    PROBATION = "FAIXA_PROBATION"
    INADMISSIVEL = "FAIXA_INADMISSIVEL"
    ANALISE_HUMANA = "ANALISE_HUMANA"


class VetoEvent(StrEnum):
    """Veto booleano por EVENTO (§4b) — nunca corte numérico."""

    SANCAO_ATIVA = "sancao_ativa"
    TERMO_DESCUMPRIDO = "termo_descumprido"
    FRAUDE_COMPROVADA = "fraude_comprovada"
    QUEBRA_GATE_GP8 = "quebra_gate_gp8"


@dataclass(frozen=True, slots=True)
class DimensionInput:
    """Insumo DECLARADO de uma dimensão — `None` = UNMEASURED (regra dos 30%), nunca 0."""

    s1: tuple[int, float] | None = None  # (n_acreditados, nota_exame/100)
    s2: int | None = None  # contratos ativos
    s3: float | None = None  # S_sin da carteira [0,1], agregado atuarial read-only
    s4: dict[str, bool] | None = None  # checklist completo (chave -> atendido)
    s5: dict[str, bool] | None = None
    s6: int | None = None  # meses ativos


@dataclass(frozen=True, slots=True)
class DimensionScore:
    id: str
    value: float  # [0,100], 2 decimais
    weight: float


@dataclass(frozen=True, slots=True)
class CertificationScore:
    """O resultado do scoreador — um PROVIMENTO para a autoridade humana, nunca uma decisão."""

    faixa: CertificationFaixa
    s_renorm: float | None  # 1 decimal; None quando nenhum insumo (ANALISE_HUMANA)
    dimensions: tuple[DimensionScore, ...]
    unmeasured_weight_fraction: float
    capped_by_missing_weight: bool
    veto_active: bool
    theta_admit: float
    theta_probation: float
    computed_at: str  # ISO-8601 UTC

    @property
    def provenance(self) -> str:
        return "GP5-CERT-V1 (ANSWERS-V1 §GP5 sha ab262f7b…; ratificado 2026-10-09)"


def _s1(inputs: DimensionInput) -> float:
    assert inputs.s1 is not None
    n, exam = inputs.s1
    return 50.0 * min(1.0, n / CREDENTIAL_MIN_INDIVIDUALS) + 50.0 * (exam / 100.0)


def _s2(inputs: DimensionInput) -> float:
    assert inputs.s2 is not None
    return 100.0 * min(1.0, inputs.s2 / ACTIVE_CONTRACTS_FLOOR)


def _s3(inputs: DimensionInput) -> float:
    # Agregado da CARTEIRA (nunca por contrato; nunca dado clínico) — clip [0,100].
    assert inputs.s3 is not None
    return max(
        0.0,
        min(100.0, 100.0 * (SINISTRALITY_CEILING - inputs.s3) / (SINISTRALITY_CEILING - SINISTRALITY_TARGET)),
    )


def _checklist(items: dict[str, bool], weights: dict[str, int]) -> float:
    return float(sum(weights[k] for k, ok in items.items() if ok and k in weights))


def _s6(inputs: DimensionInput) -> float:
    assert inputs.s6 is not None
    return 100.0 * min(1.0, inputs.s6 / TENURE_MONTHS_FULL)


#: (id, extrator) por dimensão — a regra dos 30% consulta o MESMO vetor de pesos.
_DIMENSION_EXTRACTORS: Final[tuple[tuple[str, Callable[[DimensionInput], float]], ...]] = (
    ("s1_credenciais_pessoas", _s1),
    ("s2_producao_evidenciada", _s2),
    ("s3_sinistralidade_carteira", _s3),
    ("s4_conduta_compliance", lambda i: _checklist(i.s4 or {}, S4_CHECKLIST)),
    ("s5_adesao_documental", lambda i: _checklist(i.s5 or {}, S5_CHECKLIST)),
    ("s6_tenure_sustentacao", _s6),
)


def score_channel(
    inputs: DimensionInput,
    *,
    veto_events: frozenset[VetoEvent] = frozenset(),
    now: datetime | None = None,
) -> CertificationScore:
    """Compute S, apply the 30% rule + θ's + boolean veto — classify, NEVER choose.

    Fail-closed por construção: dimensão `None` sai do denominador; > 0.30 de peso
    indisponível ⇒ cap PROBATION; SEM nenhum insumo ⇒ `ANALISE_HUMANA` (não 0.0);
    veto ativo ⇒ INADMISSIVEL independente de S (§4b).
    """
    now = now or datetime.now(UTC)
    scored: list[DimensionScore] = []
    unmeasured_weight = 0.0
    for dim_id, extract in _DIMENSION_EXTRACTORS:
        raw = {
            "s1_credenciais_pessoas": inputs.s1,
            "s2_producao_evidenciada": inputs.s2,
            "s3_sinistralidade_carteira": inputs.s3,
            "s4_conduta_compliance": inputs.s4,
            "s5_adesao_documental": inputs.s5,
            "s6_tenure_sustentacao": inputs.s6,
        }[dim_id]
        if raw is None:
            unmeasured_weight += DIMENSION_WEIGHTS[dim_id]
            continue
        scored.append(DimensionScore(dim_id, round(extract(inputs), 2), DIMENSION_WEIGHTS[dim_id]))

    veto = bool(veto_events)
    total_weight = sum(d.weight for d in scored)
    if not scored:
        # Nenhum insumo apurável — unknown ≠ zero: a autoridade humana escolhe com nada.
        return CertificationScore(
            faixa=CertificationFaixa.ANALISE_HUMANA,
            s_renorm=None,
            dimensions=(),
            unmeasured_weight_fraction=1.0,
            capped_by_missing_weight=True,
            veto_active=veto,
            theta_admit=THETA_ADMIT,
            theta_probation=THETA_PROBATION,
            computed_at=now.isoformat(),
        )

    s = sum(d.weight * d.value for d in scored) / total_weight
    s = round(s, 1)  # half-up a 1 decimal no display; o valor interno segue 2 casas por dimensão
    unmeasured_fraction = unmeasured_weight  # Σw = 1.00 ⇒ fração == peso

    if veto:
        faixa = CertificationFaixa.INADMISSIVEL
    elif s >= THETA_ADMIT and unmeasured_fraction <= UNMEASURED_WEIGHT_CAP:
        faixa = CertificationFaixa.ADMITIDO
    elif s >= THETA_PROBATION:
        # Acima de θ_probation mas com peso demais em aberto ⇒ PROBATION por cap (§4a exemplo 3).
        faixa = CertificationFaixa.PROBATION
    else:
        faixa = CertificationFaixa.INADMISSIVEL

    return CertificationScore(
        faixa=faixa,
        s_renorm=s,
        dimensions=tuple(scored),
        unmeasured_weight_fraction=round(unmeasured_fraction, 2),
        capped_by_missing_weight=unmeasured_fraction > UNMEASURED_WEIGHT_CAP,
        veto_active=veto,
        theta_admit=THETA_ADMIT,
        theta_probation=THETA_PROBATION,
        computed_at=now.isoformat(),
    )


@dataclass(frozen=True, slots=True)
class LapseState:
    """Regime de vigência (§4d) — lapse < 0 SEMPRE desagenda; g é só caminho de retorno."""

    t_expiry: datetime
    t_now: datetime
    renewal_started_before_expiry: bool = False

    @property
    def lapse_days(self) -> int:
        return (self.t_expiry - self.t_now).days

    @property
    def desagendado(self) -> bool:
        """lapse < 0 ⇒ publicações do canal saem do ar (efeito COMERCIAL, nunca trabalhista)."""
        return self.lapse_days < 0

    @property
    def notice_due(self) -> bool:
        """True quando dentro da janela W (≤60d do expiry) — notificações −60/−30/−7."""
        return 0 <= self.lapse_days <= RENEWAL_NOTICE_DAYS

    @property
    def return_path_open(self) -> bool:
        """Durante g (15d) o canal segue DESAGENDADO; só a renovação já iniciada restaura."""
        return (
            self.desagendado
            and self.renewal_started_before_expiry
            and abs(self.lapse_days) <= RETURN_GRACE_DAYS
        )

    @classmethod
    def from_issue(cls, t_issue: datetime, t_now: datetime, **kw: bool) -> Self:
        """t_expiry de s1 = t_issue + 12 meses (RN 557 art. 10 caput — verificação anual)."""
        return cls(t_expiry=t_issue + timedelta(days=30 * CREDENTIAL_VALIDITY_MONTHS), t_now=t_now, **kw)
