"""E-SF-04r — estado fechado da taxonomia de classes de campo do fio comercial.

O que este módulo fixa (itens aprovados a, b, d; o item c fica FORA porque duplicaria
`tests/unit/gateway/test_esf04_commercial_fences.py` — TB-E-24-a, chave clínica recusada):

(a) Nenhuma construção de `CommercialWire(...)` em `src/maezo/**/*.py` passa os keywords
    `intake_purpose_allowlist` ou `journey_label_vocabulary` (nem `**kwargs`, que poderia
    carregá-los às escondidas), e os defaults do construtor continuam `frozenset()` vazio.
    Vazio = todo descritor/valor de intake e todo rótulo de jornada recusados: a lista é
    publicada pela fonte/DPO, nunca preenchida no código. O varrimento exige ao menos uma
    chamada encontrada (não-vacuidade).
(b) `src/maezo/adapters/amh/commercial_fences.py` e `src/maezo/adapters/amh/commercial.py`
    não importam `maezo.gateway.vendor_suppression` nem o nome `FieldClass` (AST, imports
    relativos resolvidos, `from X import Y` lido como `X.Y`), e o identificador `FieldClass`
    não aparece como `Name`/`Attribute`. `vendor_suppression.FieldClass` (C1–C6, egresso a
    fornecedor) NÃO é a taxonomia DPO de classes de campo do fio comercial; nenhuma classe é
    liberada por classificação.
(d) SOMENTE LEITURA sobre o contrato fixado `tests/candidate_artifacts/commercial/contract-c6-4.json`:
    nenhuma chave de propriedade contém "agravo" (sem distinção de caixa); as chaves que
    contêm "cpt" ficam fixadas como catraca (só podem encolher). Nada é decidido sobre elas.

Cada cerca tem controle positivo do detector (fonte/árvore plantada que ele DEVE acusar) e
caso sem falso positivo (fonte plantada vizinha que ele NÃO deve acusar).

Limites declarados:
- (a) é estático: reconhece `CommercialWire(...)` por nome (`Name` ou `Attribute`); um alias
  (`W = CommercialWire; W(...)`), `functools.partial(CommercialWire, ...)`, uma subclasse que
  repassa a lista em `super().__init__(...)` ou construção via `getattr`/reflexão não é visto. Testes e
  fixtures fora de `src/` não são varridos (lá a lista sintética é legítima).
- (b) cobre só os dois módulos do adaptador nomeados; import dinâmico (`importlib`) e menção
  em string/comentário não são vistos.
- (d) lê chaves de objeto JSON em qualquer profundidade; valores (strings em `required`,
  `enum`, descrições) não são chaves e não contam. O contrato nunca é editado aqui.
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from maezo.adapters.amh.commercial import CommercialWire

_ROOT = Path(__file__).resolve().parents[3]
_SRC = _ROOT / "src" / "maezo"
_CONTRACT = _ROOT / "tests" / "candidate_artifacts" / "commercial" / "contract-c6-4.json"
_ADAPTER_MODULES = {
    "maezo.adapters.amh.commercial_fences": _SRC / "adapters" / "amh" / "commercial_fences.py",
    "maezo.adapters.amh.commercial": _SRC / "adapters" / "amh" / "commercial.py",
}

#: Parâmetros do construtor que só a fonte/DPO publica; nenhum código em src/ os preenche.
_CLOSED_KWARGS = frozenset({"intake_purpose_allowlist", "journey_label_vocabulary"})
_FORBIDDEN_MODULE = "maezo.gateway.vendor_suppression"
_FORBIDDEN_NAME = "FieldClass"

#: "agravo": conjunto vazio no contrato C6-4. Se um dia aparecer, o registro é GAP-04-3 e
#: este conjunto não pode crescer sem nova decisão do dono.
_AGRAVO_KEYS_PINNED: frozenset[str] = frozenset()
#: "cpt": catraca — só pode encolher (found <= pinned). Registro em GAP-04-3; nada decidido.
_CPT_KEYS_PINNED: frozenset[str] = frozenset({"cpt"})


# ------------------------------------------------------------------------------ detectores


def _wire_calls(tree: ast.AST) -> list[ast.Call]:
    """Toda chamada cujo alvo se chama `CommercialWire` (`Name` ou `Attribute`)."""
    calls: list[ast.Call] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if (isinstance(func, ast.Name) and func.id == "CommercialWire") or (
            isinstance(func, ast.Attribute) and func.attr == "CommercialWire"
        ):
            calls.append(node)
    return calls


def _closed_kwarg_violations(calls: list[ast.Call]) -> list[tuple[int, str]]:
    """(linha, keyword) para cada keyword fechado ou `**` passado a `CommercialWire`."""
    hits: list[tuple[int, str]] = []
    for call in calls:
        for keyword in call.keywords:
            if keyword.arg is None:
                hits.append((call.lineno, "**"))
            elif keyword.arg in _CLOSED_KWARGS:
                hits.append((call.lineno, keyword.arg))
    return hits


def _resolve_from(module: str, node: ast.ImportFrom) -> str:
    """Resolve `from .x import y` relativo ao pacote do módulo analisado."""
    if node.level == 0:
        return node.module or ""
    package = module.split(".")[: -node.level]
    if node.module:
        package.append(node.module)
    return ".".join(package)


def _imported_targets(module: str, source: str) -> set[str]:
    """Nomes pontuados importados; `from X import Y` vira `X` e `X.Y`."""
    targets: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            targets.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = _resolve_from(module, node)
            targets.add(base)
            targets.update(f"{base}.{alias.name}" for alias in node.names)
    return targets


def _import_violations(module: str, source: str) -> set[str]:
    return {
        target
        for target in _imported_targets(module, source)
        if target == _FORBIDDEN_MODULE
        or target.startswith(_FORBIDDEN_MODULE + ".")
        or target.rsplit(".", 1)[-1] == _FORBIDDEN_NAME
    }


def _identifier_hits(source: str) -> list[int]:
    """Linhas onde `FieldClass` aparece como `Name` ou `Attribute`."""
    return [
        node.lineno
        for node in ast.walk(ast.parse(source))
        if (isinstance(node, ast.Name) and node.id == _FORBIDDEN_NAME)
        or (isinstance(node, ast.Attribute) and node.attr == _FORBIDDEN_NAME)
    ]


def _keys(tree: Any) -> Iterator[str]:
    """Toda chave de objeto JSON, em qualquer profundidade."""
    if isinstance(tree, dict):
        for key, value in tree.items():
            yield key
            yield from _keys(value)
    elif isinstance(tree, list):
        for item in tree:
            yield from _keys(item)


def _keys_containing(tree: Any, needle: str) -> frozenset[str]:
    return frozenset(key for key in _keys(tree) if needle in key.lower())


def _src_files() -> list[Path]:
    return sorted(_SRC.rglob("*.py"))


# ------------------------------------------------------------------------------ (a)


def test_no_src_wire_construction_passes_closed_vocabularies() -> None:
    total = 0
    violations: list[tuple[str, int, str]] = []
    for path in _src_files():
        calls = _wire_calls(ast.parse(path.read_text(encoding="utf-8")))
        total += len(calls)
        rel = str(path.relative_to(_ROOT))
        violations.extend((rel, line, kw) for line, kw in _closed_kwarg_violations(calls))
    # Não-vacuidade: as construções conhecidas de commercial_admission.py são encontradas.
    assert total >= 2
    assert violations == []


def test_wire_kwarg_detector_positive_control_and_no_false_positive() -> None:
    planted = (
        "w1 = CommercialWire(b, intake_purpose_allowlist=frozenset({'x'}))\n"
        "w2 = mod.CommercialWire(b, journey_label_vocabulary=frozenset())\n"
        "w3 = CommercialWire(b, **opts)\n"
    )
    hits = _closed_kwarg_violations(_wire_calls(ast.parse(planted)))
    assert hits == [(1, "intake_purpose_allowlist"), (2, "journey_label_vocabulary"), (3, "**")]
    clean = (
        "w = CommercialWire(b, hosted_origins=h, delivery_origins=d)\n"
        "other = OtherWire(b, intake_purpose_allowlist=frozenset({'x'}))\n"
    )
    tree = ast.parse(clean)
    assert len(_wire_calls(tree)) == 1
    assert _closed_kwarg_violations(_wire_calls(tree)) == []


def test_wire_closed_vocabulary_defaults_are_empty_frozenset() -> None:
    if dataclasses.is_dataclass(CommercialWire):
        defaults = {field.name: field.default for field in dataclasses.fields(CommercialWire)}
    else:
        parameters = inspect.signature(CommercialWire.__init__).parameters
        defaults = {name: parameters[name].default for name in _CLOSED_KWARGS if name in parameters}
    assert set(defaults) >= _CLOSED_KWARGS
    for name in _CLOSED_KWARGS:
        value = defaults[name]
        assert type(value) is frozenset
        assert value == frozenset()


# ------------------------------------------------------------------------------ (b)


def test_adapter_does_not_borrow_vendor_field_class() -> None:
    """vendor_suppression.FieldClass (C1–C6, egresso a fornecedor) NÃO é a taxonomia DPO de
    classes de campo do fio comercial; nenhuma classe é liberada por classificação."""
    for module, path in _ADAPTER_MODULES.items():
        source = path.read_text(encoding="utf-8")
        assert _imported_targets(module, source), module  # não-vacuidade: há imports lidos
        assert _import_violations(module, source) == set(), module
        assert _identifier_hits(source) == [], module


def test_field_class_detector_positive_control_and_no_false_positive() -> None:
    module = "maezo.adapters.amh.commercial"
    for planted in (
        "from maezo.gateway.vendor_suppression import FieldClass\n",
        "from maezo.gateway import vendor_suppression\n",
        "import maezo.gateway.vendor_suppression as vs\n",
        "from ...gateway.vendor_suppression import VendorSubmissionRefusal\n",
        "from ...gateway import vendor_suppression\n",
        "from somewhere import FieldClass\n",
    ):
        assert _import_violations(module, planted), planted
    assert _identifier_hits("x = FieldClass.C1\n") == [1]
    assert _identifier_hits("x = vs.FieldClass\n") == [1]
    clean = (
        "from maezo.gateway.vendor_suppression_audit import Other\n"
        "from maezo.adapters.amh.commercial_fences import screen_commercial_input\n"
        "from .commercial_fences import FieldClassifier\n"
        "note = 'FieldClass'  # FieldClass em string/comentário não conta\n"
        "x = FieldClassifier\n"
    )
    assert _import_violations(module, clean) == set()
    assert _identifier_hits(clean) == []
    assert "maezo.adapters.amh.commercial_fences.FieldClassifier" in _imported_targets(module, clean)


# ------------------------------------------------------------------------------ (d)


def test_contract_property_keys_agravo_and_cpt_ratchet() -> None:
    contract = json.loads(_CONTRACT.read_text(encoding="utf-8"))  # leitura apenas
    assert sum(1 for _ in _keys(contract)) > 0  # não-vacuidade
    assert _keys_containing(contract, "agravo") == _AGRAVO_KEYS_PINNED
    assert _keys_containing(contract, "cpt") <= _CPT_KEYS_PINNED


def test_contract_key_detector_positive_control_and_no_false_positive() -> None:
    planted = {
        "properties": {"CPT_status": {"type": "string"}, "nested": [{"temAgravo": True}]},
        "x": {"Agravo": 1},
    }
    assert _keys_containing(planted, "agravo") == frozenset({"temAgravo", "Agravo"})
    assert _keys_containing(planted, "cpt") == frozenset({"CPT_status"})
    assert not _keys_containing(planted, "cpt") <= _CPT_KEYS_PINNED
    clean = {"required": ["cpt", "agravo"], "description": "cpt agravo", "enum": ["CPT"]}
    assert _keys_containing(clean, "agravo") == frozenset()
    assert _keys_containing(clean, "cpt") == frozenset()
