"""R228 supplementary lexical tripwire, not runtime authorization.

The finite inventory detects selected named proposals, not all aggregate semantics.
R117 canonical validation and the actual program sink guards enforce consumption.
No current program aggregate sink is contracted; prose can never authorize one.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import yaml

from maezo.gateway.seams.population import GatedPopulationFeatureClient
from maezo.platform.privacy.population_policy import PopulationPolicyUnavailableError, load_population_policy

_REPO = Path(__file__).resolve().parents[3]
_BPMN_DIR = _REPO / "spec" / "processes" / "bpmn"
_DMN_DIR = _REPO / "spec" / "processes" / "dmn"
_POLICIES_DIR = _REPO / "spec" / "policies"
_APPROVALS = _POLICIES_DIR / "autonomy" / "action-approvals.yaml"
_VALENTINA_YAML = _REPO / "spec" / "agents" / "valentina" / "agent.yaml"
_VALENTINA_SRC = _REPO / "src" / "maezo" / "agents" / "valentina"
_CONTRACT = _REPO / "docs" / "processes" / "contracts" / "SP-OP-PROGRAMA-001.md"
_DPO_PACKAGE = _REPO / "docs" / "sme-dispatch" / "dpo" / "PACKAGE.md"

_BPMN_NS = {
    "bpmn": "http://www.omg.org/spec/BPMN/20100524/MODEL",
    "camunda": "http://camunda.org/schema/1.0/bpmn",
}
_DMN_NS = {"dmn": "https://www.omg.org/spec/DMN/20191111/MODEL/"}

#: A classe de efeito que NOMEIA "agregado populacional" nesta casa (`action-approvals.yaml`).
AGGREGATE_EFFECT_CLASS = "leitura_populacional"

#: Ancoras textuais (INICIO/FIM) do bloco de fechamento WP3.5 no contrato. Toda cerca de fatia de
#: texto tem ancora de inicio E de fim — sem a de fim, a fatia cresce sem ninguem notar.
_CLOSURE_START = "<!-- R-228-WP3.5-FECHAMENTO-INICIO -->"
_CLOSURE_END = "<!-- R-228-WP3.5-FECHAMENTO-FIM -->"

#: Par ortografico pt-BR de cada conceito DERIVADO acima. Declarado, e dito que e declarado.
_PT_BR_STEMS: dict[str, tuple[str, ...]] = {
    "population": ("populac",),
    "populacional": ("populac",),
    "aggregate": ("agregad",),
    "cohort": ("coorte",),
    "actuarial": ("atuarial",),
}


# ---------------------------------------------------------------------------
# Derivacao do vocabulario (ancorada na arvore)
# ---------------------------------------------------------------------------
def _load_yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def aggregate_operations() -> tuple[str, ...]:
    """Operacoes mapeadas para `leitura_populacional` em `action-approvals.yaml::mapeamento_acoes`."""
    mapping = _load_yaml(_APPROVALS)["mapeamento_acoes"]
    return tuple(sorted(op for op, cls in mapping.items() if cls == AGGREGATE_EFFECT_CLASS))


def _camel_tokens(name: str) -> tuple[str, ...]:
    return tuple(part.lower() for part in re.findall(r"[A-Z][a-z0-9]*", name))


def aggregate_value_types() -> tuple[str, ...]:
    """Tipo de retorno dos metodos do seam de agregado, colhido pela ANOTACAO (introspeccao).

    A cadeia e: operacao mapeada -> nome de metodo do `GatedPopulationFeatureClient` -> anotacao de
    retorno. Se qualquer elo for renomeado, `test_marker_derivation_is_anchored` reprova.
    """
    found: set[str] = set()
    for op in aggregate_operations():
        method_name = op.rsplit(".", 1)[-1]
        method = getattr(GatedPopulationFeatureClient, method_name, None)
        if method is None:
            continue
        annotation = getattr(method, "__annotations__", {}).get("return")
        if isinstance(annotation, str) and annotation:
            found.add(annotation)
    return tuple(sorted(found))


def aggregate_markers() -> frozenset[str]:
    """Radicais (substring, minusculas) que denunciam um agregado populacional."""
    markers: set[str] = set()
    for op in aggregate_operations():
        segments = op.split(".")
        markers.add(segments[-1])  # p.ex. "population_metrics"
        if len(segments) >= 3:
            markers.add(segments[-2])  # o contexto: "population"
    markers.update(tok for tok in AGGREGATE_EFFECT_CLASS.split("_") if tok != "leitura")
    for type_name in aggregate_value_types():
        markers.update(_camel_tokens(type_name))  # "CohortAggregate" -> cohort, aggregate
    # Aplica o par ortografico pt-BR sobre os TOKENS de cada marcador derivado (split em "_"), nao
    # so sobre o marcador inteiro: "actuarial_risk" nunca bate em `_PT_BR_STEMS["actuarial_risk"]",
    # mas o token "actuarial" (apos o split) bate em `_PT_BR_STEMS["actuarial"]` -> adiciona
    # "atuarial". Um marcador sem "_" (p.ex. "population") tem split-de-um-token == ele mesmo, entao
    # o comportamento anterior (lookup do marcador inteiro) continua coberto.
    for marker in tuple(markers):
        for token in marker.split("_"):
            markers.update(_PT_BR_STEMS.get(token, ()))
    return frozenset(m for m in markers if m)


# ---------------------------------------------------------------------------
# Piso k declarado (VALOR, nunca so o arquivo)
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class KFloor:
    key: str
    value: int
    source: str


def _as_declared_int(value: Any) -> int | None:
    """Um piso so EXISTE quando ha VALOR inteiro >= 1. Vazio/None/placeholder nao conta."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 1 else None
    return None


def declared_k_floor() -> KFloor | None:
    """O piso k ratificado, se ja existir numa politica versionada sob `spec/policies/`."""
    try:
        policy = load_population_policy()
    except PopulationPolicyUnavailableError:
        return None
    return KFloor(key="k_min", value=policy.k_min, source=policy.policy_id)


# ---------------------------------------------------------------------------
# Inventario de consumidores (parseado, nunca escrito a mao)
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class Consumer:
    kind: str
    ident: str
    where: str
    marker: str
    declaration: str


def _matched_marker(text: str, markers: frozenset[str]) -> str | None:
    lowered = text.lower()
    for marker in sorted(markers):
        if marker in lowered:
            return marker
    return None


def _programa_bpmn_paths() -> tuple[Path, ...]:
    return tuple(sorted(_BPMN_DIR.glob("SP-OP-PROGRAMA-001_*.bpmn")))


def _programa_dmn_paths() -> tuple[Path, ...]:
    return tuple(sorted(_DMN_DIR.glob("programa_*.dmn")))


def _bpmn_consumers(markers: frozenset[str]) -> list[Consumer]:
    out: list[Consumer] = []
    for path in _programa_bpmn_paths():
        root = ET.parse(path).getroot()
        for task in root.iter():
            if not task.tag.endswith("}serviceTask") and not task.tag.endswith("}businessRuleTask"):
                continue
            ident = task.get("id") or "<sem id>"
            declaration = ET.tostring(task, encoding="unicode")
            # Campos ESTRUTURAIS: topico, id, e os nomes das variaveis/topicos publicados.
            surface: list[str] = [ident, task.get(f"{{{_BPMN_NS['camunda']}}}topic") or ""]
            surface.append(task.get(f"{{{_BPMN_NS['camunda']}}}decisionRef") or "")
            for param in task.iter(f"{{{_BPMN_NS['camunda']}}}inputParameter"):
                name = param.get("name") or ""
                if name.startswith("event_topic"):
                    surface.append((param.text or "").strip())
                elif name == "event_payload_vars":
                    surface.extend(v.strip() for v in (param.text or "").split(","))
                else:
                    surface.append(name)
            for field in surface:
                marker = _matched_marker(field, markers)
                if marker is not None:
                    out.append(
                        Consumer(
                            kind="bpmn",
                            ident=f"{ident}::{field}",
                            where=path.name,
                            marker=marker,
                            declaration=declaration,
                        )
                    )
    return out


def _dmn_consumers(markers: frozenset[str]) -> list[Consumer]:
    out: list[Consumer] = []
    for path in _programa_dmn_paths():
        root = ET.parse(path).getroot()
        for decision in root.iter(f"{{{_DMN_NS['dmn']}}}decision"):
            declaration = ET.tostring(decision, encoding="unicode")
            decision_id = decision.get("id") or "<sem id>"
            for output in decision.iter(f"{{{_DMN_NS['dmn']}}}output"):
                name = output.get("name") or output.get("label") or ""
                marker = _matched_marker(name, markers)
                if marker is not None:
                    out.append(
                        Consumer(
                            kind="dmn",
                            ident=f"{decision_id}::{name}",
                            where=path.name,
                            marker=marker,
                            declaration=declaration,
                        )
                    )
    return out


def _valentina_declared_surface() -> list[tuple[str, str]]:
    """(campo, valor) da superficie DECLARADA da Valentina — ids, nunca prosa."""
    spec = _load_yaml(_VALENTINA_YAML)
    surface: list[tuple[str, str]] = []
    for field in ("tools", "autonomy_actions"):
        for value in spec.get(field) or ():
            surface.append((field, str(value)))
    a2a = spec.get("a2a") or {}
    for field in ("capabilities", "skills", "accepted_task_types"):
        for value in a2a.get(field) or ():
            surface.append((f"a2a.{field}", str(value)))
    return surface


def _valentina_consumers(markers: frozenset[str]) -> list[Consumer]:
    out: list[Consumer] = []
    declaration = _VALENTINA_YAML.read_text(encoding="utf-8")
    for field, value in _valentina_declared_surface():
        marker = _matched_marker(value, markers)
        if marker is not None:
            out.append(
                Consumer(
                    kind="agent-spec",
                    ident=f"{field}={value}",
                    where=_VALENTINA_YAML.name,
                    marker=marker,
                    declaration=declaration,
                )
            )
    # Simbolos EXATOS do seam de agregado no grafo da Valentina (nomes de simbolo, nao prosa).
    symbols = {GatedPopulationFeatureClient.__name__, *aggregate_value_types()}
    symbols.update(op.rsplit(".", 1)[-1] for op in aggregate_operations())
    for module in sorted(_VALENTINA_SRC.rglob("*.py")):
        text = module.read_text(encoding="utf-8")
        for symbol in sorted(symbols):
            if symbol in text:
                out.append(
                    Consumer(
                        kind="agent-src",
                        ident=f"{module.name}::{symbol}",
                        where=str(module.relative_to(_REPO)),
                        marker=symbol.lower(),
                        declaration=text,
                    )
                )
    return out


#: Prefixo dos topicos de worker do PROGRAMA na declaração estática de mapeamento de tópicos
#: (1 dos 8 tópicos do BPMN; `action-approvals.yaml::mapeamento_topicos` — NAO e um registro).
_PROGRAMA_TOPIC_PREFIX = "operadora.programa."


def _topic_map_consumers(markers: frozenset[str]) -> list[Consumer]:
    """Topicos do PROGRAMA na declaração estática de mapeamento de tópicos (1 dos 8 tópicos do
    BPMN; `action-approvals.yaml::mapeamento_topicos`).

    `platform/topic_registry.py` NAO carrega catalogo estatico — ele valida convencao de nome e os
    topicos entram por `register`/`register_batch` em runtime, entao nao ha inventario de arvore a
    ancorar la. O sitio de declaracao estatica que EXISTE e este mapa topico -> classe de efeito,
    e e ele que a cerca varre.
    """
    approvals = _load_yaml(_APPROVALS)
    topic_map = approvals["mapeamento_topicos"]
    declaration = _APPROVALS.read_text(encoding="utf-8")
    out: list[Consumer] = []
    for topic, effect_class in sorted(topic_map.items()):
        if not topic.startswith(_PROGRAMA_TOPIC_PREFIX):
            continue
        marker = _matched_marker(topic, markers)
        if marker is not None:
            out.append(
                Consumer(
                    kind="topic-map",
                    ident=f"{topic} -> {effect_class}",
                    where=_APPROVALS.name,
                    marker=marker,
                    declaration=declaration,
                )
            )
    return out


def aggregate_consumers() -> list[Consumer]:
    """Finite named proposal inventory; runtime sink guards cover opaque field additions."""
    markers = aggregate_markers()
    return [
        *_bpmn_consumers(markers),
        *_dmn_consumers(markers),
        *_valentina_consumers(markers),
        *_topic_map_consumers(markers),
    ]


# ---------------------------------------------------------------------------
# O nucleo puro: os DOIS regimes
# ---------------------------------------------------------------------------
def evaluate_gate(k_floor: KFloor | None, consumers: Sequence[Consumer]) -> list[str]:
    """All proposed program aggregates require a new actual sink contract, even with a floor.

    This supplementary inventory cannot authorize a sink from documentation or a token.
    """
    if k_floor is None:
        return [
            f"[{c.kind}] {c.where}: {c.ident} parece egresso de agregado populacional "
            f"(marcador {c.marker!r}), mas NENHUM piso de k-anonimato esta declarado em "
            f"spec/policies/. R-228 fechou WP3.5 como 'sem sujeito hoje': o primeiro consumidor "
            f"de agregado reabre a pre-condicao do piso k ratificado (R-117 / ADR-0019)."
            for c in consumers
        ]
    return [
        f"[{c.kind}] {c.where}: {c.ident} e consumidor proposto sem contrato de sink vinculado; exige "
        f"o piso {k_floor.key!r} declarado em {k_floor.source}."
        for c in consumers
        # No program aggregate consumer is contracted. Prose never establishes a bound call.
    ]


# ---------------------------------------------------------------------------
# Testes ancorados na arvore
# ---------------------------------------------------------------------------
def test_marker_derivation_is_anchored() -> None:
    """A cerca nao pode se desarmar sozinha: apagar a classe de efeito reprova ALTO."""
    operations = aggregate_operations()
    assert len(operations) >= 2, (
        f"`{AGGREGATE_EFFECT_CLASS}` deixou de mapear operacoes em action-approvals.yaml"
        f"::mapeamento_acoes ({operations!r}) — o vocabulario da cerca R-228 vem dali."
    )
    assert AGGREGATE_EFFECT_CLASS in _load_yaml(_APPROVALS)["acoes"]
    assert aggregate_value_types(), (
        "nenhum tipo de retorno colhido de GatedPopulationFeatureClient pelos nomes das operacoes "
        f"{operations!r} — a cadeia de derivacao quebrou."
    )
    markers = aggregate_markers()
    assert {"population", "populac", "aggregate", "agregad", "atuarial"} <= markers, markers
    assert _programa_bpmn_paths(), "BPMN do PROGRAMA nao encontrado — a cerca varreria o vazio."
    assert _programa_dmn_paths(), "DMNs programa_* nao encontradas — a cerca varreria o vazio."
    topic_map = _load_yaml(_APPROVALS)["mapeamento_topicos"]
    assert any(t.startswith(_PROGRAMA_TOPIC_PREFIX) for t in topic_map), (
        "nenhum topico `operadora.programa.*` em mapeamento_topicos — a superficie de topico "
        "estatico sumiu e a cerca varreria o vazio nela."
    )


def test_ptbr_stem_map_applies_over_marker_tokens() -> None:
    """Regressao VER-R228 F1 (probe M6b): `_PT_BR_STEMS["actuarial"]` tinha efeito zero porque
    `aggregate_markers()` so aplicava o par ortografico sobre o marcador INTEIRO. O marcador
    derivado de `agente.population.actuarial_risk` (`mapeamento_acoes`) e o leaf composto
    `actuarial_risk`, nunca o token isolado `actuarial` — entao `_PT_BR_STEMS.get("actuarial_risk")`
    sempre voltava vazio e `"atuarial"` nunca entrava no vocabulario. Um egresso batizado na grafia
    pt-BR que a propria ADR-0042 Parte 2 propoe (`exposicao_atuarial`) atravessava a cerca sem
    acionar nenhum marcador. Prova end-to-end: o campo estrutural bate em `_matched_marker` e
    `evaluate_gate` (regime sem piso) o rejeita, exatamente como `_bpmn_consumers` trataria uma
    entrada real de `event_payload_vars`.
    """
    markers = aggregate_markers()
    assert "atuarial" in markers, markers
    field = "exposicao_atuarial"
    marker = _matched_marker(field, markers)
    assert marker == "atuarial", (
        f"a grafia pt-BR {field!r} do egresso deveria acionar o marcador 'atuarial'; bateu {marker!r}."
    )
    consumer = Consumer(
        kind="bpmn",
        ident=f"ST_PublishCompleted::{field}",
        where="SP-OP-PROGRAMA-001_Programas_Cuidado.bpmn",
        marker=marker,
        declaration="<serviceTask/>",
    )
    violations = evaluate_gate(None, [consumer])
    assert len(violations) == 1 and "atuarial" in violations[0], violations


def test_no_k_floor_is_declared_today() -> None:
    """Estado de hoje, e o motivo de WP3.5 fechar 'sem sujeito': nao ha VALOR de k em lugar nenhum.

    ADR-0042 esta `Proposed — DRAFT/verify` e R-117 landara o manifesto com `k` VAZIO; nenhum dos
    dois declara valor. `andre/graph.py::DEFAULT_MIN_K_ANONYMITY` e mecanismo em `src/`, nao
    politica versionada, e por isso NAO e lido aqui.
    """
    floor = declared_k_floor()
    assert floor is None, f"piso k passou a existir ({floor}) — R-117/ADR-0019 destravou; reveja WP3.5."


def test_programa_has_no_population_aggregate_egress() -> None:
    """O invariante que R-228 fechou: zero agregado populacional no PROGRAMA sem piso k."""
    violations = evaluate_gate(declared_k_floor(), aggregate_consumers())
    assert violations == [], "\n".join(violations)


def test_programa_dmn_outputs_are_per_instance_only() -> None:
    """`programa_routing`/`programa_sla` so emitem banda/roteamento POR INSTANCIA a Zona Geral.

    Duas provas independentes: (1) nenhum nome de saida carrega marcador de agregado; (2) nenhuma
    tabela usa `hitPolicy=COLLECT` com `aggregation` — que e a primitiva DMN que TRANSFORMA linhas
    por instancia num numero populacional.
    """
    markers = aggregate_markers()
    outputs: dict[str, list[str]] = {}
    for path in _programa_dmn_paths():
        root = ET.parse(path).getroot()
        for table in root.iter(f"{{{_DMN_NS['dmn']}}}decisionTable"):
            hit_policy = (table.get("hitPolicy") or "UNIQUE").upper()
            aggregation = table.get("aggregation")
            assert not (hit_policy == "COLLECT" and aggregation), (
                f"{path.name}::{table.get('id')} usa hitPolicy=COLLECT aggregation={aggregation!r} "
                "— agregacao populacional numa DMN do PROGRAMA (R-228)."
            )
            names = [
                (o.get("name") or o.get("label") or "") for o in table.iter(f"{{{_DMN_NS['dmn']}}}output")
            ]
            outputs[f"{path.name}::{table.get('id')}"] = names
            for name in names:
                assert _matched_marker(name, markers) is None, (
                    f"{path.name}::{table.get('id')} declara a saida {name!r}, que carrega "
                    "marcador de agregado populacional (R-228)."
                )
    assert outputs, "nenhuma decisionTable programa_* parseada."


def test_leitura_populacional_stays_unapproved() -> None:
    """A classe de agregado segue SEM aprovacao nos tres dominios — R-228 nao aprovou nada."""
    action = _load_yaml(_APPROVALS)["acoes"][AGGREGATE_EFFECT_CLASS]
    assert action["enforcement"] == "shadow"
    for domain, record in action["aprovacoes"].items():
        assert record["aprovado"] is False, (
            f"`{AGGREGATE_EFFECT_CLASS}.aprovacoes.{domain}.aprovado` virou {record['aprovado']!r}; "
            "aprovar leitura populacional e ato humano (DPO/medica/ANS/seguranca), nunca de R-228."
        )


def test_contract_records_wp35_closure_between_anchors() -> None:
    """O contrato registra o fechamento 'sem sujeito hoje' E aponta a condicao de retorno ao DPO."""
    text = _CONTRACT.read_text(encoding="utf-8")
    assert _CLOSURE_START in text and _CLOSURE_END in text, "ancoras do bloco R-228 sumiram."
    block = text.split(_CLOSURE_START, 1)[1].split(_CLOSURE_END, 1)[0]
    for needle in ("sem sujeito hoje", "R-228", "R-117", __name__.rsplit(".", 1)[-1]):
        assert needle in block, f"bloco de fechamento WP3.5 nao menciona {needle!r}."
    assert "nao ratifica" in block.lower() or "não ratifica" in block.lower(), (
        "o bloco precisa dizer, explicitamente, que o fechamento NAO ratifica piso de k."
    )


def test_dpo_package_no_longer_asks_for_k_parameters() -> None:
    """A pergunta 4 saiu da FILA do encarregado; a evidencia da linha original fica preservada."""
    text = _DPO_PACKAGE.read_text(encoding="utf-8")
    questions_block = text.split("### SP-OP-PROGRAMA-001", 1)[1].split("## Turnaround", 1)[0]
    numbered = re.findall(r"^  \d+\. .*(?:\n(?:     |\s{5,}).*)*", questions_block, re.MULTILINE)
    asked = [q for q in numbered if "k-anonymity" in q or "small-cell" in q]
    assert asked == [], f"a pergunta de k-anonimato voltou a fila do DPO: {asked!r}"
    assert "R-228" in text, "o pacote precisa registrar POR QUE a pergunta saiu (redline R-228)."


# ---------------------------------------------------------------------------
# O regime FUTURO, exercitado hoje (o ramo nao pode ser codigo morto)
# ---------------------------------------------------------------------------
def _fake_consumer(declaration: str) -> Consumer:
    return Consumer(
        kind="bpmn",
        ident="ST_PublishPopulationMetrics::programa.population_metrics",
        where="fake.bpmn",
        marker="population_metrics",
        declaration=declaration,
    )


def test_gate_without_floor_rejects_any_consumer() -> None:
    violations = evaluate_gate(None, [_fake_consumer("<serviceTask/>")])
    assert len(violations) == 1
    assert "NENHUM piso de k-anonimato" in violations[0]


@pytest.mark.parametrize(
    ("declaration", "expected"),
    [
        ("<serviceTask/>", 1),  # consumidor SEM referencia ao parametro -> reprova
        ('<serviceTask><param name="k_anonimato_min"/></serviceTask>', 1),  # referencia -> passa
    ],
)
def test_gate_with_floor_requires_every_consumer_to_reference_it(declaration: str, expected: int) -> None:
    """A floor or parameter reference alone cannot authorize a new program aggregate sink."""
    # value=7 e um numero de teste NEUTRO — `evaluate_gate` so le `.key`/`.source` (linhas 408/410
    # do modulo), nunca `.value`; 5 coincidiria com a opcao A (nao ratificada) da ADR-0042 (VER-R228 F2).
    floor = KFloor(key="k_anonimato_min", value=7, source="spec/policies/privacy/fake.yaml")
    assert len(evaluate_gate(floor, [_fake_consumer(declaration)])) == expected


def test_gate_with_floor_still_admits_the_empty_world() -> None:
    # value=7 e um numero de teste NEUTRO — `evaluate_gate` so le `.key`/`.source` (linhas 408/410
    # do modulo), nunca `.value`; 5 coincidiria com a opcao A (nao ratificada) da ADR-0042 (VER-R228 F2).
    floor = KFloor(key="k_anonimato_min", value=7, source="spec/policies/privacy/fake.yaml")
    assert evaluate_gate(floor, []) == []


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, None), ("", None), ("PENDENTE", None), (0, None), (True, None), (5, 5), ("5", None)],
)
def test_only_a_real_value_counts_as_a_declared_floor(value: Any, expected: int | None) -> None:
    """R-117 landa o manifesto com `k` VAZIO: um manifesto sem VALOR nao vira o regime."""
    assert _as_declared_int(value) == expected
