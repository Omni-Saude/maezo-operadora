"""Avaliador de meta dos KPIs declarados nos blueprints de agente (R-085 / F-091; ADR-0010).

POR QUE EXISTE. ADR-0010 promete "metricas por agente: ... KPIs do blueprint", e cada
`spec/agents/<id>/agent.yaml` declara `kpis: [{name, target}]`. Ate R-085 nenhum desses nomes tinha
consumidor em `src/` (F-091): a meta estava escrita e nada sabia compara-la a um valor medido, nem
dizer DE ONDE o valor medido viria.

O QUE ESTE MODULO FAZ, e so isto:
  1. le os KPIs declarados (`load_declared_kpis`) — a meta mora no `agent.yaml`, nunca aqui;
  2. interpreta a gramatica FECHADA de `target` (`parse_target`): `track`, `==N`, `>N`, `>=N`,
     `<N`, `<=N` (sufixo opcional `s` = segundos) ou uma REFERENCIA externa (ex.: `<RN259/395`,
     o prazo regulatorio que mora no DMN/timer BPMN — nao e resolvido aqui de proposito);
  3. compara um valor medido a meta (`evaluate_kpi`) e devolve um veredito fechado;
  4. declara, para CADA (agente, KPI), a fonte de medicao (`MEASUREMENT_SOURCES`) — inclusive,
     honestamente, `sem_fonte` quando nenhum emissor existe. A cerca
     `tests/unit/runtime/test_kpi_evaluator.py` exige que a tabela cubra os `agent.yaml` EXATAMENTE:
     um KPI novo sem fonte declarada quebra o teste em vez de ficar sem consumidor em silencio.

O QUE ELE NAO FAZ. Nao inventa meta (um `track` continua sem meta), nao resolve prazo regulatorio
(isso e DMN/timer BPMN, AGENTS.md regra 5), nao consulta Prometheus nem engine (quem mede injeta o
valor) e nao decide nada de negocio: o veredito e informativo para o gestor humano do agente
("ficha de funcionario", ADR-0010), nunca uma aresta de processo.
"""

from __future__ import annotations

import enum
import math
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import yaml

__all__ = [
    "MEASUREMENT_SOURCES",
    "DeclaredKpi",
    "KpiEvaluation",
    "KpiSource",
    "KpiTarget",
    "KpiTargetError",
    "KpiVerdict",
    "SourceKind",
    "TargetKind",
    "evaluate_declared",
    "evaluate_kpi",
    "load_declared_kpis",
    "parse_target",
]


class KpiTargetError(ValueError):
    """Um `target` fora da gramatica fechada — falha alto, nunca vira `track` em silencio."""


class TargetKind(enum.StrEnum):
    TRACK = "track"  # medir, nao atingir (nenhuma meta validada)
    COMPARACAO = "comparacao"  # ==, >, >=, <, <=
    REFERENCIA = "referencia"  # meta externa (ex.: prazo RN no DMN) — nao resolvida aqui


class KpiVerdict(enum.StrEnum):
    ATINGIDA = "atingida"
    NAO_ATINGIDA = "nao_atingida"
    SEM_META = "sem_meta"  # target `track`
    SEM_MEDICAO = "sem_medicao"  # nenhum valor medido foi fornecido
    META_NAO_RESOLVIVEL = "meta_nao_resolvivel"  # target e referencia externa


@dataclass(frozen=True, slots=True)
class KpiTarget:
    """A meta interpretada. `raw` e' o texto do `agent.yaml`, preservado para o relatorio."""

    raw: str
    kind: TargetKind
    operator: str | None = None
    threshold: float | None = None
    unit: str | None = None


#: `==0`, `>0.95`, `<15s`, `>=1.0` — operador, numero, unidade opcional (so segundos existe hoje).
_COMPARACAO_RE: Final[re.Pattern[str]] = re.compile(r"^(==|>=|<=|>|<)\s*(\d+(?:\.\d+)?)\s*(s)?$")
#: Referencia externa: operador seguido de um identificador normativo (ex.: `<RN259/395`).
_REFERENCIA_RE: Final[re.Pattern[str]] = re.compile(r"^(==|>=|<=|>|<)\s*([A-Za-z][A-Za-z0-9_./-]*)$")

#: Igualdade de ponto flutuante: razoes calculadas (ex.: 0.9999999999) nao podem falhar `==1.0`.
_ABS_TOL: Final[float] = 1e-9


def parse_target(raw: str) -> KpiTarget:
    """Interpreta um `target` do `agent.yaml`. Gramatica fechada; o resto levanta.

    Raises:
        KpiTargetError: texto vazio, tipo errado ou fora da gramatica.
    """
    if not isinstance(raw, str) or not raw.strip():
        raise KpiTargetError(f"target vazio ou nao-texto: {raw!r}")
    text = raw.strip()
    if text == "track":
        return KpiTarget(raw=raw, kind=TargetKind.TRACK)
    match = _COMPARACAO_RE.match(text)
    if match:
        return KpiTarget(
            raw=raw,
            kind=TargetKind.COMPARACAO,
            operator=match.group(1),
            threshold=float(match.group(2)),
            unit="s" if match.group(3) else None,
        )
    reference = _REFERENCIA_RE.match(text)
    if reference:
        return KpiTarget(raw=raw, kind=TargetKind.REFERENCIA, operator=reference.group(1))
    raise KpiTargetError(f"target fora da gramatica fechada: {raw!r}")


def evaluate_kpi(target: KpiTarget, measured: float | None) -> KpiVerdict:
    """Compara `measured` a `target`. Ordem: sem meta > meta nao resolvivel > sem medicao > comparacao."""
    if target.kind is TargetKind.TRACK:
        return KpiVerdict.SEM_META
    if target.kind is TargetKind.REFERENCIA:
        return KpiVerdict.META_NAO_RESOLVIVEL
    if measured is None:
        return KpiVerdict.SEM_MEDICAO
    if isinstance(measured, bool) or not isinstance(measured, int | float) or math.isnan(measured):
        raise KpiTargetError(f"valor medido invalido: {measured!r}")
    if target.threshold is None or target.operator is None:  # impossivel via parse_target
        raise KpiTargetError(f"meta de comparacao incompleta: {target.raw!r}")
    threshold = target.threshold
    equal = math.isclose(measured, threshold, rel_tol=0.0, abs_tol=_ABS_TOL)
    met = {
        "==": equal,
        ">": measured > threshold and not equal,
        ">=": measured > threshold or equal,
        "<": measured < threshold and not equal,
        "<=": measured < threshold or equal,
    }[target.operator]
    return KpiVerdict.ATINGIDA if met else KpiVerdict.NAO_ATINGIDA


class SourceKind(enum.StrEnum):
    RECORDING_RULE = "recording_rule"  # `record:` em deploy/observability/alert-rules.yml
    HISTOGRAM_P95 = "histogram_p95"  # histogram_quantile(0.95, ...) sobre um Histogram de metrics.py
    CI_EVAL = "ci_eval"  # golden dataset da lane `make evals` (ADR-0009)
    SEM_FONTE = "sem_fonte"  # nenhum emissor existe — declarado, nunca omitido


@dataclass(frozen=True, slots=True)
class KpiSource:
    kind: SourceKind
    ref: str
    note: str = ""


_EVAL = KpiSource(
    SourceKind.CI_EVAL,
    "make evals",
    "golden dataset por agente (ADR-0009); tests/evals ainda sem dataset coletavel",
)
_SEM_DOSSIE = KpiSource(
    SourceKind.SEM_FONTE,
    "",
    "nenhum emissor de completude de dossie em src/; fonte exige decisao do dono",
)
_SEM_ROTEAMENTO = KpiSource(
    SourceKind.SEM_FONTE,
    "",
    "precisao de roteamento exige o desfecho humano do caso; nenhum emissor em src/",
)
_INVARIANTE_L0 = KpiSource(
    SourceKind.SEM_FONTE,
    "",
    "invariante L0 hard garantida por codigo (gateway.pep.HARD_ACTIONS); "
    "nao existe contador de violacao — a meta ==0 nao e medida, e imposta",
)


def _sem_fonte(note: str) -> KpiSource:
    return KpiSource(SourceKind.SEM_FONTE, "", note)


#: Fonte de medicao de CADA KPI declarado, por (agente, kpi). A cerca do teste exige cobertura
#: EXATA dos `spec/agents/*/agent.yaml` (exceto `_template`). `sem_fonte` e uma declaracao honesta
#: de lacuna, nao um placeholder: cada uma diz por que nao ha emissor.
MEASUREMENT_SOURCES: Final[Mapping[tuple[str, str], KpiSource]] = {
    # andre
    ("andre", "false_pricing_decision_rate"): _INVARIANTE_L0,
    ("andre", "phi_egress_violations"): _sem_fonte(
        "egresso bloqueado e desfecho `egresso_bloqueado` (CC-09), nao violacao; "
        "nenhum contador de violacao de egresso em src/"
    ),
    ("andre", "lgpd_erasure_coverage"): _sem_fonte(
        "cobertura de apagamento LGPD confirmada pelo DPO; jobs de lifecycle ainda recusam por desenho"
    ),
    ("andre", "dossier_completeness"): _SEM_DOSSIE,
    ("andre", "human_routing_precision"): _SEM_ROTEAMENTO,
    ("andre", "eval_score"): _EVAL,
    # beatriz
    ("beatriz", "zero_auto_accusation"): _INVARIANTE_L0,
    ("beatriz", "dossier_completeness"): _SEM_DOSSIE,
    ("beatriz", "evidence_pseudonymized_rate"): _sem_fonte(
        "pseudonimizacao imposta na custodia (scan PHI); nenhum contador de taxa em src/"
    ),
    ("beatriz", "human_routing_precision"): _SEM_ROTEAMENTO,
    ("beatriz", "eval_score"): _EVAL,
    ("beatriz", "false_accusation_rate"): _INVARIANTE_L0,
    # carolina
    ("carolina", "dossier_completeness"): _SEM_DOSSIE,
    ("carolina", "human_routing_precision"): _SEM_ROTEAMENTO,
    ("carolina", "eval_score"): _EVAL,
    ("carolina", "false_decredentialing_rate"): _INVARIANTE_L0,
    # fernando
    ("fernando", "cure_rate"): _sem_fonte("purga de inadimplencia e fato do engine; nenhum emissor em src/"),
    ("fernando", "escalation_precision"): _SEM_ROTEAMENTO,
    ("fernando", "notice_compliance"): _sem_fonte(
        "notificacao previa (RN 593) e ordem de processo no BPMN; nenhum contador de conformidade"
    ),
    ("fernando", "eval_score"): _EVAL,
    ("fernando", "false_denial_rate"): _INVARIANTE_L0,
    # gustavo
    ("gustavo", "submission_on_time_rate"): _sem_fonte(
        "prazo de envio ANS e timer BPMN (SP-OP-ANS-*); poller de engine ainda nao existe"
    ),
    ("gustavo", "nip_deadline_compliance"): _sem_fonte(
        "prazo NIP e timer BPMN (SP-OP-NIP-*); poller de engine ainda nao existe"
    ),
    ("gustavo", "dossier_completeness"): _SEM_DOSSIE,
    ("gustavo", "human_routing_precision"): _SEM_ROTEAMENTO,
    ("gustavo", "eval_score"): _EVAL,
    ("gustavo", "false_denial_rate"): _INVARIANTE_L0,
    # helena
    ("helena", "resolution_rate"): KpiSource(SourceKind.RECORDING_RULE, "maezo_helena_resolution_rate"),
    ("helena", "escalation_rate"): KpiSource(SourceKind.RECORDING_RULE, "maezo_helena_escalation_rate"),
    ("helena", "first_response_p95"): KpiSource(
        SourceKind.HISTOGRAM_P95, "maezo_agent_first_response_seconds"
    ),
    ("helena", "eval_score"): _EVAL,
    ("helena", "nps"): _sem_fonte(
        "CSAT/NPS e placeholder no agent.yaml (hook no encerramento ainda nao existe)"
    ),
    # lucas
    ("lucas", "resolution_rate"): KpiSource(SourceKind.RECORDING_RULE, "maezo_lucas_resolution_rate"),
    ("lucas", "escalation_precision"): _SEM_ROTEAMENTO,
    ("lucas", "eval_score"): _EVAL,
    ("lucas", "false_denial_rate"): _INVARIANTE_L0,
    # marina
    ("marina", "dossier_completeness"): _SEM_DOSSIE,
    ("marina", "glosa_rate"): _sem_fonte(
        "taxa de glosa e fato da decisao humana no engine; nenhum emissor em src/"
    ),
    ("marina", "prazo_medio_analise_conta"): _sem_fonte("prazo de analise e fato do engine; nenhum emissor"),
    ("marina", "taxa_deferimento_recurso"): _sem_fonte(
        "deferimento e decisao humana no engine; nenhum emissor"
    ),
    ("marina", "prazo_medio_resposta_recurso"): _sem_fonte(
        "prazo de resposta e fato do engine; nenhum emissor"
    ),
    ("marina", "human_routing_precision"): _SEM_ROTEAMENTO,
    ("marina", "eval_score"): _EVAL,
    ("marina", "false_denial_rate"): _INVARIANTE_L0,
    # rafael
    ("rafael", "dossier_completeness"): _SEM_DOSSIE,
    ("rafael", "auto_approval_rate"): _sem_fonte(
        "estruturalmente 0 no agente (RAF-01): a decisao L2 e do engine (GW_AutoAprovacao)"
    ),
    ("rafael", "human_routing_precision"): _SEM_ROTEAMENTO,
    ("rafael", "sla_analise_p95"): _sem_fonte(
        "meta e referencia ao prazo RN da DMN (nao resolvida aqui); tempo ate dossie nao e emitido"
    ),
    ("rafael", "eval_score"): _EVAL,
    ("rafael", "false_denial_rate"): _INVARIANTE_L0,
    # valentina
    ("valentina", "care_plan_completeness"): _sem_fonte(
        "completude de plano de cuidado: nenhum emissor em src/; fonte exige decisao do dono"
    ),
    ("valentina", "stratification_precision"): _SEM_ROTEAMENTO,
    ("valentina", "human_routing_precision"): _SEM_ROTEAMENTO,
    ("valentina", "eval_score"): _EVAL,
    ("valentina", "false_denial_rate"): _INVARIANTE_L0,
}


@dataclass(frozen=True, slots=True)
class DeclaredKpi:
    agent_id: str
    name: str
    target: KpiTarget


@dataclass(frozen=True, slots=True)
class KpiEvaluation:
    agent_id: str
    name: str
    target: str
    verdict: KpiVerdict
    source: KpiSource
    measured: float | None


def load_declared_kpis(agents_dir: Path) -> tuple[DeclaredKpi, ...]:
    """Le `kpis:` de cada `<agents_dir>/<id>/agent.yaml` (ignora `_template`). Ordem estavel.

    Raises:
        KpiTargetError: um `target` fora da gramatica, ou uma entrada sem `name`/`target`.
    """
    declared: list[DeclaredKpi] = []
    for path in sorted(agents_dir.glob("*/agent.yaml")):
        agent_id = path.parent.name
        if agent_id.startswith("_"):
            continue
        document: Any = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for entry in document.get("kpis") or ():
            if not isinstance(entry, Mapping) or "name" not in entry or "target" not in entry:
                raise KpiTargetError(f"{path}: entrada de KPI sem name/target: {entry!r}")
            declared.append(DeclaredKpi(agent_id, str(entry["name"]), parse_target(str(entry["target"]))))
    return tuple(declared)


def evaluate_declared(
    declared: Iterable[DeclaredKpi],
    measurements: Mapping[tuple[str, str], float | None],
) -> tuple[KpiEvaluation, ...]:
    """Avalia cada KPI declarado contra a medicao injetada (ausente => `sem_medicao`).

    Raises:
        KeyError: um KPI declarado sem entrada em `MEASUREMENT_SOURCES` — fail-closed, a mesma
            lacuna que a cerca do teste pega em CI.
    """
    results: list[KpiEvaluation] = []
    for kpi in declared:
        source = MEASUREMENT_SOURCES[(kpi.agent_id, kpi.name)]
        measured = measurements.get((kpi.agent_id, kpi.name))
        results.append(
            KpiEvaluation(
                agent_id=kpi.agent_id,
                name=kpi.name,
                target=kpi.target.raw,
                verdict=evaluate_kpi(kpi.target, measured),
                source=source,
                measured=measured,
            )
        )
    return tuple(results)
