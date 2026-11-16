"""EP-CORE-09b (E-SF-33; CORE-09 caminho B) — registro de sala limpa por EXTRACT.

Casos negativos montam, em `tmp_path`, um modulo marcado e um registro; cada caso quebra UMA propriedade.
Caso real: o head nao tem modulo marcado nem registro (nenhuma re-derivacao B entrou ainda) e o
verificador nao acusa nada; o esquema versionado aceita o registro valido de fixture.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import jsonschema
import pytest
from scripts.docs.cleanroom_records import (
    CLEANROOM_REL,
    REGISTRO_GLOB,
    SCHEMA_REL,
    CleanroomRefusal,
    modulos_marcados,
    verificar,
)
from scripts.docs.omni_provenance import arquivos_rastreados

_REPO = Path(__file__).resolve().parents[2]
_MODULO = "src/maezo/portal/web/src/effects.ts"
# Montado por concatenacao para que ESTE arquivo nao contenha um marcador real em inicio de linha.
_MARCADOR = "// " + "derived-from" + ": OMNI-V-04\n"

_VALIDO: dict[str, Any] = {
    "schema_version": "1.0.0",
    "extract": "OMNI-V-04",
    "modules": [_MODULO],
    "spec_sources": [
        {
            "repository": "Omni-Saude/maezo-operadora",
            "path": "tests/candidate_artifacts/commercial/contract-c6-4.json",
            "lines": "445-464",
        },
        {
            "repository": "Omni-Saude/maezo-operadora",
            "path": "src/maezo/gateway/commercial_acceptance_admission.py",
        },
    ],
    "spec_author": "Ana",
    "implementer": "Bruno",
    "attestation": {"implementer_did_not_open_omni_files": True, "attested_by": "Bruno"},
}


def _montar(tmp_path: Path, registro: dict[str, Any] | None) -> tuple[Path, list[str], Path]:
    raiz = tmp_path / "maezo"
    alvo = raiz / _MODULO
    alvo.parent.mkdir(parents=True)
    alvo.write_text(_MARCADOR + "export const efeito = 'issued';\n", encoding="utf-8")
    pasta = raiz / CLEANROOM_REL
    pasta.mkdir(parents=True)
    if registro is not None:
        (pasta / f"CLEANROOM-{registro['extract']}.json").write_text(json.dumps(registro), encoding="utf-8")
    return raiz, [_MODULO], pasta


def test_valid_record_passes_control(tmp_path: Path) -> None:
    raiz, arquivos, pasta = _montar(tmp_path, copy.deepcopy(_VALIDO))
    assert verificar(raiz, arquivos, pasta) == []


def test_cleanroom_spec_cites_omni_path_refused(tmp_path: Path) -> None:
    reg = copy.deepcopy(_VALIDO)
    reg["spec_sources"].append(
        {"repository": "Omni-Saude/omni_sales_platform", "path": "src/contracts/customer-effects.ts"}
    )
    raiz, arquivos, pasta = _montar(tmp_path, reg)
    motivos = [r.motivo for r in verificar(raiz, arquivos, pasta)]
    assert CleanroomRefusal.FORBIDDEN_SOURCE in motivos
    # o modulo deixa de estar coberto: registro invalido nao cobre
    assert CleanroomRefusal.RECORD_MISSING in motivos


def test_cleanroom_source_disguised_as_maezo_but_naming_omni_refused(tmp_path: Path) -> None:
    reg = copy.deepcopy(_VALIDO)
    reg["spec_sources"] = [
        {"repository": "Omni-Saude/maezo-operadora", "path": "omni_sales_platform/src/x.ts"}
    ]
    raiz, arquivos, pasta = _montar(tmp_path, reg)
    assert CleanroomRefusal.FORBIDDEN_SOURCE in [r.motivo for r in verificar(raiz, arquivos, pasta)]


def test_cleanroom_same_actor_refused(tmp_path: Path) -> None:
    reg = copy.deepcopy(_VALIDO)
    reg["implementer"] = " ana "
    reg["attestation"]["attested_by"] = "ana"
    raiz, arquivos, pasta = _montar(tmp_path, reg)
    assert CleanroomRefusal.ACTOR_NOT_SEPARATED in [r.motivo for r in verificar(raiz, arquivos, pasta)]


def test_derived_module_without_record_refused(tmp_path: Path) -> None:
    raiz, arquivos, pasta = _montar(tmp_path, None)
    recusas = verificar(raiz, arquivos, pasta)
    assert [(r.motivo, r.alvo) for r in recusas] == [(CleanroomRefusal.RECORD_MISSING, _MODULO)]


@pytest.mark.parametrize(
    "mutacao",
    [
        lambda r: r.pop("attestation"),
        lambda r: r["attestation"].update(implementer_did_not_open_omni_files=False),
        lambda r: r["attestation"].update(attested_by="Carla"),
        lambda r: r.update(spec_sources=[]),
    ],
    ids=["sem-atestacao", "atestacao-falsa", "atestacao-de-outro", "sem-fontes"],
)
def test_malformed_record_refused(tmp_path: Path, mutacao: Any) -> None:
    reg = copy.deepcopy(_VALIDO)
    mutacao(reg)
    raiz, arquivos, pasta = _montar(tmp_path, reg)
    assert CleanroomRefusal.RECORD_MALFORMED in [r.motivo for r in verificar(raiz, arquivos, pasta)]


def test_versioned_schema_accepts_valid_fixture_and_rejects_omni_source() -> None:
    schema = json.loads((_REPO / SCHEMA_REL).read_text(encoding="utf-8"))
    jsonschema.validate(_VALIDO, schema)
    ruim = copy.deepcopy(_VALIDO)
    ruim["spec_sources"] = [
        {"repository": "Omni-Saude/omni_sales_platform", "path": "src/contracts/customer-effects.ts"}
    ]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(ruim, schema)


def test_real_head_has_no_marked_module_and_no_record() -> None:
    rastreados = arquivos_rastreados(_REPO)
    assert modulos_marcados(_REPO, rastreados) == {}
    assert sorted((_REPO / CLEANROOM_REL).glob(REGISTRO_GLOB)) == []
    assert verificar(_REPO, rastreados, _REPO / CLEANROOM_REL) == []
