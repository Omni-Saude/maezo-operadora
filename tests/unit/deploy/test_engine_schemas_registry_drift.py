"""Deriva do registro Java de schemas do engine (DEC-31 D31-8, issue #1084).

O recurso `engine-schemas-v1.json` empacotado no plugin Java (admissao nativa do engine
securizado, `Capability.java`) e' DERIVADO de `maezo.gateway.engine_schemas.SCHEMAS` pelo
gerador `deploy/cibseven/secured/export_schemas.py`. Ninguem chamava o gerador: o JSON
congelou no snapshot de 2026-09-12 (47 linhas) enquanto o lado Python ganhou as linhas da
ponte de documentos, o erro BPMN da negativa e o `numero_conta` opcional do PAGTO (#1180).

Esta cerca roda o gerador EM MEMORIA (mesma funcao `registry()` que o `--check` usa) e
compara byte a byte com o arquivo commitado. Ela roda nos shards unitarios existentes do
CI, sem passo novo de workflow. Para corrigir uma falha:

    uv run python deploy/cibseven/secured/export_schemas.py \
        src/maezo/portal/engine/java/src/main/resources/engine-schemas-v1.json

e revise o diff (e os pins de contagem em `CapabilityTest`/`RuntimeDefinitionObservationTest`).
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[3]
GENERATOR = ROOT / "deploy/cibseven/secured/export_schemas.py"
REGISTRY = ROOT / "src/maezo/portal/engine/java/src/main/resources/engine-schemas-v1.json"
REGEN = (
    "uv run python deploy/cibseven/secured/export_schemas.py "
    "src/maezo/portal/engine/java/src/main/resources/engine-schemas-v1.json"
)


def _generator() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_maezo_export_schemas", GENERATOR)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _rows(document: bytes) -> list[str]:
    return sorted(row["schema_id"] for row in json.loads(document)["schemas"])


def test_committed_java_registry_is_exactly_the_generator_output() -> None:
    expected = _generator().registry()
    committed = REGISTRY.read_bytes()
    if committed != expected:
        old, new = set(_rows(committed)), set(_rows(expected))
        raise AssertionError(
            "engine-schemas-v1.json difere da saida do gerador "
            f"(so no commitado: {sorted(old - new)}; so no gerador: {sorted(new - old)}; "
            f"linhas {len(old)} -> {len(new)}). Regenere: {REGEN}"
        )


def test_registry_is_non_vacuous_and_covers_every_reviewed_python_row() -> None:
    from maezo.gateway.engine_schemas import SCHEMAS

    registered = _rows(REGISTRY.read_bytes())
    assert len(registered) == len(set(registered)), "schema_id duplicado no registro Java"
    assert {row.schema_id for row in SCHEMAS} <= set(registered)
    assert json.loads(REGISTRY.read_bytes())["protocol"] == "maezo.engine-schemas.v1"


def test_drift_fence_bites_on_a_single_byte_change() -> None:
    expected = _generator().registry()
    mutated = expected.replace(b'"numero_conta"', b'"numero_contb"', 1)
    assert mutated != expected, "mutante vacuo: numero_conta sumiu do registro gerado"
    assert REGISTRY.read_bytes() != mutated
