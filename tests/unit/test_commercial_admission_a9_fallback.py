"""E-SF-35 / EP-CORE-06F (CORE-06, variante fallback): A9 recusada, verificada por walk sintético.

A medição do canal de vendas não é admitida (A9 só produz a recusa registrada; nada emite). Estas
cercas têm os nomes do plano e se apoiam no loader e no espelho do stub que já existem em
`maezo.gateway.commercial_analytics_admission`, com o registro SINTÉTICO em memória (o registro real
vive no repositório do OSP e depende de ato do DPO; nenhum registro é versionado aqui):

- a recusa registrada mantém a dimensão de medição fechada (nenhum comando admitido; os campos do
  envelope de medição seguem recusados com o registro em mãos; nenhuma superfície de emissão);
- o walk sintético com tenant ou jornada reais é recusado;
- o resultado do walk sintético nunca é rotulado como baseline.

Deriva registrada: o plano cita uma recusa `measurement_not_admitted` e um "contador inalterado";
nenhum dos dois existe (não há agregador nem emissor no Maezo). As cercas usam os motivos reais.
"""

from __future__ import annotations

import ast
import dataclasses
import re
from pathlib import Path
from typing import Any

import pytest

from maezo.gateway import commercial_analytics_admission as a9
from maezo.gateway.commercial_analytics_admission import (
    A9_ADMITTED_COMMANDS,
    A9_DEDUPE_KEY_FIELD,
    A9_JOURNEY_MEASUREMENT_KEY_FIELD,
    A9_REGISTERED_REFUSAL_DECISION,
    A9_VARIANT_ID_FIELD,
    MEASUREMENT_STUB_FIELDS,
    MeasurementPayloadError,
    refuse_measurement_payload,
)
from tests.unit.test_commercial_admission_a9 import _clean_stub_payload, a9_admission

_SRC = Path(a9.__file__).resolve().parents[1]

#: Rótulos que transformariam o resultado de um walk sintético em referência de medição.
_ROTULOS_DE_BASELINE = ("operationalBaseline", "baseline", "baselineLabel", "meta", "target", "isBaseline")


def _recusa(payload: dict[str, Any]) -> str:
    with pytest.raises(MeasurementPayloadError) as exc:
        refuse_measurement_payload(payload)
    return exc.value.reason_code


def test_refused_record_keeps_measurement_dimension_closed() -> None:
    admissao = a9_admission()
    # A recusa registrada é o único estado que o loader produz, e ela não admite comando nenhum: o valor
    # validado não carrega campo de comando, decisão aberta ou escopo, e o escopo admitido é vazio.
    assert A9_REGISTERED_REFUSAL_DECISION == "refused"
    assert tuple(A9_ADMITTED_COMMANDS) == ()
    campos = {f.name for f in dataclasses.fields(admissao)}
    assert not [c for c in campos if re.search(r"(?i)command|scope|decision|metric|measure", c)], campos
    # Com o registro em mãos, o envelope de medição continua recusado campo a campo.
    for campo, motivo in (
        (A9_JOURNEY_MEASUREMENT_KEY_FIELD, "measurement_journey_key_fabricated"),
        (A9_DEDUPE_KEY_FIELD, "measurement_dedupe_key_fabricated"),
        (A9_VARIANT_ID_FIELD, "measurement_variant_id_fabricated"),
    ):
        assert _recusa({campo: "qualquer"}) == motivo
        assert _recusa({campo: None}) == motivo
    # O próprio loader não chama nada que publique, agregue ou grave medição (o uso da guarda por outros
    # módulos de `src/` é cercado em `test_nada_alem_do_loader_conhece_o_stub_de_medicao`). A guarda não
    # recebe a admissão: o "com o registro em mãos" acima é só a ordem do cenário.
    emissores = {"publish", "emit", "increment", "record_metric", "Counter", "Gauge", "Histogram"}
    arvore = ast.parse(Path(a9.__file__).read_text(encoding="utf-8"))
    chamados = {
        n.func.attr if isinstance(n.func, ast.Attribute) else getattr(n.func, "id", "")
        for n in ast.walk(arvore)
        if isinstance(n, ast.Call)
    }
    assert not chamados & emissores
    assert "refuse_measurement_payload" in {n.name for n in arvore.body if isinstance(n, ast.FunctionDef)}


@pytest.mark.parametrize(
    "mudanca",
    [
        {"tenant": "acme-corp"},
        {"tenant": "SYNTHETIC-TENANT-A"},
        {"tenant": " synthetic-tenant-a"},
        {"tenant": None},
        {"journey": "jornada-real-123"},
        {"journey": "Synthetic-Journey-A"},
        {"tenant": "acme-corp", "journey": "jornada-real-123"},
    ],
)
def test_synthetic_walk_with_real_tenant_refuses(mudanca: dict[str, Any]) -> None:
    assert refuse_measurement_payload(_clean_stub_payload()) is None  # controle: o walk sintético passa
    payload = {**_clean_stub_payload(), **mudanca}
    assert _recusa(payload) == "measurement_stub_not_synthetic"


@pytest.mark.parametrize("rotulo", _ROTULOS_DE_BASELINE)
@pytest.mark.parametrize("valor", ["UNMEASURED", "MEASURED", True, 0, {"periodo": "2026-11"}])
def test_synthetic_walk_result_never_labelled_baseline(rotulo: str, valor: object) -> None:
    assert rotulo not in MEASUREMENT_STUB_FIELDS
    payload = {**_clean_stub_payload(), rotulo: valor}
    assert _recusa(payload) == "measurement_stub_shape_invalid"


def test_sem_o_marcador_da_familia_o_payload_nao_e_medicao() -> None:
    """Limite DECLARADO da guarda: pertencer à família `measurement.proposed.v1` depende do marcador
    `schemaVersion`, nunca é inferido. Um payload sem o marcador (ou com outra família) não é payload
    de medição e passa, mesmo com rótulo de baseline; o que a cerca garante é que o RESULTADO do walk
    sintético (que leva o marcador) nunca é rotulado como baseline."""
    for payload in (
        {"operationalBaseline": "MEASURED"},
        {**_clean_stub_payload(schemaVersion="measurement.proposed.v2"), "operationalBaseline": "MEASURED"},
    ):
        assert refuse_measurement_payload(payload) is None


def test_nada_alem_do_loader_conhece_o_stub_de_medicao() -> None:
    """Controle de escopo: o stub e os campos do envelope só aparecem no loader A9 em `src/`."""
    nomes = {"refuse_measurement_payload", "MeasurementPayloadError"}
    leitores = []
    for path in sorted(_SRC.rglob("*.py")):
        if path.resolve() == Path(a9.__file__).resolve():
            continue
        arvore = ast.parse(path.read_text(encoding="utf-8"))
        usados = {n.id for n in ast.walk(arvore) if isinstance(n, ast.Name)}
        usados |= {n.attr for n in ast.walk(arvore) if isinstance(n, ast.Attribute)}
        usados |= {a.name for n in ast.walk(arvore) if isinstance(n, ast.ImportFrom) for a in n.names}
        prefixados = {u for u in usados if u.startswith(("MEASUREMENT_STUB_", "A9_"))}
        texto = path.read_text(encoding="utf-8")
        if usados & nomes or prefixados or "measurement.proposed.v1" in texto:
            leitores.append(path.relative_to(_SRC).as_posix())
    assert not leitores
