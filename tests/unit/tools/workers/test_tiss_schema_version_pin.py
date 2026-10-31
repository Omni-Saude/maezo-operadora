"""R-068 (F-066, PEND-050): pin de versao padrao-TISS no worker de submissao ANS.

Direcao do dono (DEC-17 -> R-036, issue #1081, "Posicao do dono" 2026-10-29): pinar a edicao
Padrao TISS Setembro/2026, Componente de Comunicacao 04.03.00 e 01.06.00, no worker de submissao.

O que estes testes provam:
  - o pin e uma lista FECHADA em codigo; `MAEZO_TISS_SCHEMA_VERSION` so seleciona DENTRO dela
    (valor fora do pin -> fail-closed, mesmo com XSD presente no disco);
  - 04.03.00 tem UMA fonte so (o pin do gateway provider, `provider_schema.SOURCE_PIN_SHA256`):
    os dois caminhos de versao deixam de ser desacoplados;
  - 01.06.00 esta vendorizado byte a byte do zip oficial da ANS, com sha256 e proveniencia
    (`spec/schemas/tiss/01.06.00/ans-source-pin.json`), e o conjunto oficial esta INCOMPLETO
    (include de `tissSimpleTypesMonitoramentoV1_05_01.xsd` ausente do zip) — registrado, sem alias;
  - versao pinada liga a validacao aos BYTES: manifesto alterado, arquivo listado adulterado ou
    XSD solto fora do manifesto -> fail-closed;
  - o parser e endurecido (sem DTD/entidades): dataset com DOCTYPE e recusado.

Fixtures sinteticas; nenhum dado real.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from maezo.agents import resolve_spec_dir
from maezo.gateway.tiss import CATALOGUE_VERSION, SOURCE_PIN_SHA256
from maezo.tools.workers.tiss_schema import (
    MONITORAMENTO_CATALOGUE_VERSION,
    MONITORAMENTO_SOURCE_PIN_SHA256,
    PINNED_TISS_SCHEMA_VERSIONS,
    TISS_SCHEMA_ROOT_ENV,
    TISS_SCHEMA_VERSION_ENV,
    TissSchemaValidator,
    _compile_pinned_schema,
    _verified_pinned_files,
)

_REPORT_TYPE = "RN_424_TISS_MONITORAMENTO"

_FIXTURE_XSD = """<?xml version="1.0" encoding="UTF-8"?>
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">
  <xs:element name="loteGuias">
    <xs:complexType>
      <xs:sequence>
        <xs:element name="numeroLote" type="xs:string"/>
      </xs:sequence>
    </xs:complexType>
  </xs:element>
</xs:schema>
"""
_VALID_XML = "<loteGuias><numeroLote>1</numeroLote></loteGuias>"
_DOCTYPE_XML = (
    '<?xml version="1.0"?>\n<!DOCTYPE loteGuias [<!ENTITY e "1">]>\n'
    "<loteGuias><numeroLote>&e;</numeroLote></loteGuias>"
)


def _tiss_root() -> Path:
    return resolve_spec_dir() / "schemas" / "tiss"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _xml(tmp_path: Path, name: str, content: str) -> str:
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return str(path)


def _copy_pinned_tree(tmp_path: Path, version: str) -> Path:
    root = tmp_path / "root"
    shutil.copytree(_tiss_root() / version, root / version)
    return root


# ---------------------------------------------------------------------------
# O pin em si
# ---------------------------------------------------------------------------


def test_pin_is_the_owner_decided_edition_pair() -> None:
    assert set(PINNED_TISS_SCHEMA_VERSIONS) == {"04.03.00", "01.06.00"}
    assert MONITORAMENTO_CATALOGUE_VERSION == "01.06.00"


def test_bilateral_version_has_a_single_source_shared_with_the_provider_gateway() -> None:
    """F-066: `provider_schema.py` fixava 04.03.00 e o worker de submissao nao tinha pin — dois
    caminhos desacoplados. O pin do worker reusa o MESMO literal e o MESMO sha do gateway."""
    manifest_name, digest = PINNED_TISS_SCHEMA_VERSIONS[CATALOGUE_VERSION]
    assert manifest_name == "provider-source-pin.json"
    assert digest == SOURCE_PIN_SHA256


def test_vendored_monitoramento_manifest_matches_the_code_pin() -> None:
    manifest_name, digest = PINNED_TISS_SCHEMA_VERSIONS[MONITORAMENTO_CATALOGUE_VERSION]
    assert digest == MONITORAMENTO_SOURCE_PIN_SHA256
    assert _sha(_tiss_root() / "01.06.00" / manifest_name) == digest


def test_vendored_monitoramento_bytes_match_the_provenance_manifest() -> None:
    version_dir = _tiss_root() / "01.06.00"
    manifest = json.loads((version_dir / "ans-source-pin.json").read_text(encoding="utf-8"))
    assert manifest["catalogue_version"] == "01.06.00"
    assert manifest["archive"]["sha256"] == (
        "db8640e1c3b87085892f54f838bfcea9934439ff365798c8428559f88c13d62d"
    )
    assert manifest["archive"]["url"].startswith("https://www.gov.br/ans/")
    files = manifest["files"]
    assert set(files) == {
        "tissMonitoramentoV1_06_00.xsd",
        "tissComplexTypesMonitoramentoV1_06_00.xsd",
        "tissSimpleTypesMonitoramentoV1_06_00.xsd",
    }
    assert {p.name for p in version_dir.iterdir()} == set(files) | {"ans-source-pin.json"}
    for name, digest in files.items():
        assert _sha(version_dir / name) == digest, name


def test_official_monitoramento_set_is_recorded_as_incomplete_without_alias() -> None:
    """O zip oficial traz `tissComplexTypesMonitoramentoV1_06_00.xsd` incluindo
    `tissSimpleTypesMonitoramentoV1_05_01.xsd`, que NAO esta no zip. O manifesto registra a
    lacuna; nenhum arquivo foi editado nem ganhou alias."""
    version_dir = _tiss_root() / "01.06.00"
    manifest = json.loads((version_dir / "ans-source-pin.json").read_text(encoding="utf-8"))
    complex_types = (version_dir / "tissComplexTypesMonitoramentoV1_06_00.xsd").read_bytes()
    assert b'schemaLocation="tissSimpleTypesMonitoramentoV1_05_01.xsd"' in complex_types
    missing = [d["schemaLocation"] for d in manifest["unresolved_dependencies"]]
    assert missing == ["tissSimpleTypesMonitoramentoV1_05_01.xsd"]
    assert not (version_dir / "tissSimpleTypesMonitoramentoV1_05_01.xsd").exists()
    assert manifest["runtime_admitted"] is False
    assert manifest["professional_ratification_claimed"] is False


# ---------------------------------------------------------------------------
# Selecao por ambiente so dentro do pin
# ---------------------------------------------------------------------------


def test_env_version_outside_pin_fails_closed_even_with_xsd_on_disk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    version_dir = tmp_path / "04.02.00"
    version_dir.mkdir()
    (version_dir / f"{_REPORT_TYPE}.xsd").write_text(_FIXTURE_XSD, encoding="utf-8")
    monkeypatch.setenv(TISS_SCHEMA_VERSION_ENV, "04.02.00")
    monkeypatch.setenv(TISS_SCHEMA_ROOT_ENV, str(tmp_path))
    result = TissSchemaValidator().validate(
        report_type=_REPORT_TYPE, dataset_ref=_xml(tmp_path, "d.xml", _VALID_XML)
    )
    assert result.schema_valid is False
    assert result.tiss_schema_version == "04.02.00"
    assert any("fora do pin" in e for e in result.errors)


def test_env_pinned_version_is_honored_and_fails_closed_without_report_type_xsd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """01.06.00 pinado via ambiente, raiz padrao: a versao e honrada, mas nenhum XSD de
    report_type faz parte do conjunto oficial -> fail-closed (rota humana), nunca auto-pass."""
    monkeypatch.setenv(TISS_SCHEMA_VERSION_ENV, "01.06.00")
    monkeypatch.delenv(TISS_SCHEMA_ROOT_ENV, raising=False)
    result = TissSchemaValidator().validate(
        report_type=_REPORT_TYPE, dataset_ref=_xml(tmp_path, "d.xml", _VALID_XML)
    )
    assert result.schema_valid is False
    assert result.tiss_schema_version == "01.06.00"
    assert any("fora do manifesto pinado" in e for e in result.errors)


@pytest.mark.parametrize("version", ["04.03.00", "01.06.00"])
def test_pinned_version_rejects_xsd_dropped_outside_the_manifest(tmp_path: Path, version: str) -> None:
    """Um XSD qualquer deixado em `<root>/<versao pinada>/` nao vira schema oficial."""
    root = _copy_pinned_tree(tmp_path, version)
    (root / version / f"{_REPORT_TYPE}.xsd").write_text(_FIXTURE_XSD, encoding="utf-8")
    result = TissSchemaValidator(schema_root=root, version=version).validate(
        report_type=_REPORT_TYPE, dataset_ref=_xml(tmp_path, "d.xml", _VALID_XML)
    )
    assert result.schema_valid is False
    assert any("fora do manifesto pinado" in e for e in result.errors)


def test_pinned_version_with_altered_manifest_fails_closed(tmp_path: Path) -> None:
    root = _copy_pinned_tree(tmp_path, "01.06.00")
    manifest = root / "01.06.00" / "ans-source-pin.json"
    manifest.write_text(manifest.read_text(encoding="utf-8") + " ", encoding="utf-8")
    result = TissSchemaValidator(schema_root=root, version="01.06.00").validate(
        report_type=_REPORT_TYPE, dataset_ref=_xml(tmp_path, "d.xml", _VALID_XML)
    )
    assert result.schema_valid is False
    assert any("proveniencia" in e for e in result.errors)


def test_pinned_version_with_tampered_listed_file_fails_closed(tmp_path: Path) -> None:
    root = _copy_pinned_tree(tmp_path, "01.06.00")
    target = root / "01.06.00" / "tissSimpleTypesMonitoramentoV1_06_00.xsd"
    target.write_bytes(target.read_bytes() + b"\n")
    result = TissSchemaValidator(schema_root=root, version="01.06.00").validate(
        report_type=_REPORT_TYPE, dataset_ref=_xml(tmp_path, "d.xml", _VALID_XML)
    )
    assert result.schema_valid is False
    assert any("proveniencia" in e for e in result.errors)


def test_pinned_version_with_missing_manifest_fails_closed(tmp_path: Path) -> None:
    root = _copy_pinned_tree(tmp_path, "01.06.00")
    (root / "01.06.00" / "ans-source-pin.json").unlink()
    result = TissSchemaValidator(schema_root=root, version="01.06.00").validate(
        report_type=_REPORT_TYPE, dataset_ref=_xml(tmp_path, "d.xml", _VALID_XML)
    )
    assert result.schema_valid is False
    assert any("proveniencia" in e for e in result.errors)


# ---------------------------------------------------------------------------
# Parser endurecido
# ---------------------------------------------------------------------------


def test_dataset_with_doctype_is_refused(tmp_path: Path) -> None:
    """Sem o parser endurecido a entidade interna e expandida e o XML valida; agora o DOCTYPE
    e recusado antes da validacao (mesmo padrao de `gateway/tiss/provider_schema.py`)."""
    version_dir = tmp_path / "FIXTURE-0"
    version_dir.mkdir()
    (version_dir / f"{_REPORT_TYPE}.xsd").write_text(_FIXTURE_XSD, encoding="utf-8")
    result = TissSchemaValidator(schema_root=tmp_path, version="FIXTURE-0").validate(
        report_type=_REPORT_TYPE, dataset_ref=_xml(tmp_path, "d.xml", _DOCTYPE_XML)
    )
    assert result.schema_valid is False
    assert any("DOCTYPE" in e for e in result.errors)


# ---------------------------------------------------------------------------
# Compilacao a partir dos bytes verificados (sem rede, sem reler o disco)
# ---------------------------------------------------------------------------


def test_bilateral_official_set_compiles_from_verified_bytes() -> None:
    manifest_name, digest = PINNED_TISS_SCHEMA_VERSIONS["04.03.00"]
    files = _verified_pinned_files(_tiss_root() / "04.03.00", manifest_name, digest)
    assert files is not None
    assert _compile_pinned_schema(files, "tissV4_03_00.xsd") is not None


def test_monitoramento_official_set_does_not_compile_missing_include() -> None:
    """Prova com os bytes oficiais: o entrypoint 01.06.00 nao compila porque o include
    `tissSimpleTypesMonitoramentoV1_05_01.xsd` nao existe no conjunto — fail-closed (None)."""
    manifest_name, digest = PINNED_TISS_SCHEMA_VERSIONS["01.06.00"]
    files = _verified_pinned_files(_tiss_root() / "01.06.00", manifest_name, digest)
    assert files is not None
    assert _compile_pinned_schema(files, "tissMonitoramentoV1_06_00.xsd") is None


def test_pinned_resolver_refuses_include_outside_the_verified_set(tmp_path: Path) -> None:
    """Includes resolvem SO a partir dos bytes pinados ja verificados: um include apontando para
    um XSD real fora do conjunto (file://) e recusado, nunca carregado do disco."""
    outside = tmp_path / "fora.xsd"
    outside.write_text(_FIXTURE_XSD, encoding="utf-8")
    files = {
        "e.xsd": (
            '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">'
            f'<xs:include schemaLocation="{outside.as_uri()}"/></xs:schema>'
        ).encode()
    }
    assert _compile_pinned_schema(files, "e.xsd") is None
