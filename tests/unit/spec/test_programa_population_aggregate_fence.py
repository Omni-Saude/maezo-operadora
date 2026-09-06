"""Cerca R-228 — egresso de agregado populacional do SP-OP-PROGRAMA-001 x piso k de anonimato.

O QUE ESTA CERCA E (e o que ela explicitamente NAO E)
-----------------------------------------------------
A decisao aprovada do dono (`OWNER-DECISIONS-REGISTER` **R-228**) fechou a pendencia **WP3.5** do
contrato `SP-OP-PROGRAMA-001` como **"sem sujeito hoje"**: nenhum agregado populacional do programa
e exposto a Zona Geral, logo nao ha consumidor para o qual fixar um parametro de k-anonimato. A
pergunta 4 do pacote do DPO (`docs/sme-dispatch/dpo/PACKAGE.md`) foi RETIRADA e substituida por
esta cerca.

**Fechar a pendencia NAO ratifica piso de k nenhum.** Esta cerca e a CONDICAO DE RETORNO ao DPO: no
primeiro consumidor de agregado proposto, o piso k ratificado (R-117 / ADR-0019 clausula 4) volta a
ser pre-condicao. Nenhum numero de k e escolhido, lido como default ou sugerido aqui — ADR-0042
(`Proposed — DRAFT/verify. NADA AQUI ESTA RATIFICADO`) e quem pede esse valor ao dono, e
`andre/graph.py::DEFAULT_MIN_K_ANONYMITY` (== 1) e mecanismo, nao piso ratificado.

A CERCA VIRA DE SENTIDO, NUNCA DESLIGA
--------------------------------------
Dois regimes, escolhidos pelo estado da arvore e nao por configuracao de teste:

* **SEM PISO DECLARADO (estado de hoje)** — qualquer consumidor de agregado populacional no
  PROGRAMA REPROVA. E o fail-closed que torna "sem sujeito hoje" uma afirmacao verificavel em vez
  de uma nota de rodape que apodrece.
* **COM PISO DECLARADO** (quando R-117 landar o manifesto assinado) — a cerca deixa de exigir
  ZERO consumidores e passa a exigir que **todo** consumidor REFERENCIE o parametro. O mesmo teste
  muda de exigencia; ele nunca vira no-op. `evaluate_gate` e a funcao pura que decide isso, e os
  DOIS regimes sao exercitados por teste hoje — o ramo futuro nao e codigo morto.

`_declared_k_floor` exige um **VALOR**, nao um arquivo. R-117 landa o manifesto com `k` VAZIO e
campos de ratificacao em branco de proposito; um manifesto sem valor NAO vira o regime, que e
exatamente o comportamento correto (o parametro ainda nao existe).

INVENTARIO DERIVADO DA ARVORE — nao ha lista escrita a mao aqui
---------------------------------------------------------------
Tudo que a cerca VARRE e parseado/introspectado: as service tasks e `event_payload_vars` do BPMN
do PROGRAMA, os `<output>` das DMNs `programa_*`, a superficie declarada da Valentina
(`spec/agents/valentina/agent.yaml`) e os simbolos do seam de agregado. O VOCABULARIO de marcadores
tambem e ancorado na arvore: parte da classe de efeito `leitura_populacional` em
`action-approvals.yaml`, colhe as operacoes mapeadas para ela (`mapeamento_acoes`), usa os nomes
dessas operacoes como nomes de metodo do seam (`GatedPopulationFeatureClient`) e extrai o tipo de
retorno pela anotacao (`CohortAggregate`). `test_marker_derivation_is_anchored` reprova se
qualquer elo dessa cadeia sumir — apagar a classe de efeito quebra a cerca ALTO, nunca a desarma
em silencio.

A unica parte DECLARADA (nao derivada) e `_PT_BR_STEMS`: o par ortografico pt-BR de cada conceito
derivado, porque os artefatos de spec desta casa sao escritos em pt-BR e um egresso de agregado
seria plausivelmente nomeado em qualquer das duas linguas. Esta declarado como expansao dos
conceitos derivados, nunca como inventario independente de nomes.

POR QUE A VARREDURA E ESTRUTURAL, NUNCA SOBRE PROSA
---------------------------------------------------
A cerca le NOMES de interface (topico, variavel publicada, saida de DMN, id de tool), jamais o
texto de `<bpmn:documentation>`. O BPMN do PROGRAMA diz `estratificacao_populacional` (um VALOR do
enum `gatilho`) e `criterio populacional/clinico informativo` em documentacao: as duas coisas sao
prosa/rotulo por instancia, nao egresso de agregado, e contar prosa como egresso tornaria a cerca
inutilizavel no primeiro comentario. Mesmo precedente de
`test_dmn_dead_inputs_fence.py` ("prose is not a read").

Esta cerca vive em `tests/unit/` e nao em `scripts/ci/`: a decisao R-228 nao pede um gate de CI
novo, e `/scripts/ci/` e CODEOWNED — a lane unitaria ja e contexto obrigatorio de `main`
(`lint / type / unit`), logo a cerca ja bloqueia merge sem ampliar superficie dono-gated.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import yaml

from maezo.gateway.seams.population import GatedPopulationFeatureClient

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

#: Chaves que declaram um piso de k-anonimato numa politica versionada sob `spec/policies/`
#: (ADR-0019 clausula 4 "parametro de compliance versionado"; ADR-0042 Parte 3 propoe exatamente
#: esse endereco; R-117 landa o manifesto sob `spec/policies/privacy/`).
_K_FLOOR_KEY = re.compile(r"k_anon|piso_k|min_cell_size")

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
    for token in tuple(markers):
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


def _walk(node: Any, path: tuple[str, ...] = ()) -> Iterator[tuple[tuple[str, ...], Any]]:
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _walk(value, (*path, str(key)))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _walk(value, (*path, f"[{index}]"))
    else:
        yield path, node


def _as_declared_int(value: Any) -> int | None:
    """Um piso so EXISTE quando ha VALOR inteiro >= 1. Vazio/None/placeholder nao conta."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 1 else None
    if isinstance(value, str) and value.strip().isdigit():
        parsed = int(value.strip())
        return parsed if parsed >= 1 else None
    return None


def declared_k_floor() -> KFloor | None:
    """O piso k ratificado, se ja existir numa politica versionada sob `spec/policies/`."""
    paths = sorted([*_POLICIES_DIR.rglob("*.yaml"), *_POLICIES_DIR.rglob("*.yml")])
    for path in paths:
        for key_path, value in _walk(_load_yaml(path)):
            if not key_path or not _K_FLOOR_KEY.search(key_path[-1]):
                continue
            parsed = _as_declared_int(value)
            if parsed is not None:
                return KFloor(key=key_path[-1], value=parsed, source=str(path.relative_to(_REPO)))
    return None


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
    for module in sorted(_VALENTINA_SRC.glob("*.py")):
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


def aggregate_consumers() -> list[Consumer]:
    """TODO consumidor de agregado populacional alcancavel pelo SP-OP-PROGRAMA-001."""
    markers = aggregate_markers()
    return [*_bpmn_consumers(markers), *_dmn_consumers(markers), *_valentina_consumers(markers)]


# ---------------------------------------------------------------------------
# O nucleo puro: os DOIS regimes
# ---------------------------------------------------------------------------
def evaluate_gate(k_floor: KFloor | None, consumers: Sequence[Consumer]) -> list[str]:
    """Violacoes (lista vazia == PASSA). SEM piso: qualquer consumidor viola. COM piso: viola quem
    nao referencia o parametro."""
    if k_floor is None:
        return [
            f"[{c.kind}] {c.where}: {c.ident} parece egresso de agregado populacional "
            f"(marcador {c.marker!r}), mas NENHUM piso de k-anonimato esta declarado em "
            f"spec/policies/. R-228 fechou WP3.5 como 'sem sujeito hoje': o primeiro consumidor "
            f"de agregado reabre a pre-condicao do piso k ratificado (R-117 / ADR-0019)."
            for c in consumers
        ]
    return [
        f"[{c.kind}] {c.where}: {c.ident} e consumidor de agregado populacional e NAO referencia "
        f"o piso {k_floor.key!r} declarado em {k_floor.source}."
        for c in consumers
        if k_floor.key not in c.declaration
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
    assert {"population", "populac", "aggregate", "agregad"} <= markers, markers
    assert _programa_bpmn_paths(), "BPMN do PROGRAMA nao encontrado — a cerca varreria o vazio."
    assert _programa_dmn_paths(), "DMNs programa_* nao encontradas — a cerca varreria o vazio."


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
        ('<serviceTask><param name="k_anonimato_min"/></serviceTask>', 0),  # referencia -> passa
    ],
)
def test_gate_with_floor_requires_every_consumer_to_reference_it(declaration: str, expected: int) -> None:
    """COM piso declarado a cerca MUDA DE EXIGENCIA — deixa de proibir e passa a exigir referencia."""
    floor = KFloor(key="k_anonimato_min", value=5, source="spec/policies/privacy/fake.yaml")
    assert len(evaluate_gate(floor, [_fake_consumer(declaration)])) == expected


def test_gate_with_floor_still_admits_the_empty_world() -> None:
    floor = KFloor(key="k_anonimato_min", value=5, source="spec/policies/privacy/fake.yaml")
    assert evaluate_gate(floor, []) == []


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, None), ("", None), ("PENDENTE", None), (0, None), (True, None), (5, 5), ("5", 5)],
)
def test_only_a_real_value_counts_as_a_declared_floor(value: Any, expected: int | None) -> None:
    """R-117 landa o manifesto com `k` VAZIO: um manifesto sem VALOR nao vira o regime."""
    assert _as_declared_int(value) == expected
