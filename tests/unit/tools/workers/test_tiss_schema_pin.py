"""Unit tests for `maezo.tools.workers.tiss_schema_pin` — the T2.6-2 TISS-schema-pin ratification
gate (dark build; NOT wired into `ans_submit.py` this wave — see that module's docstring
"SCOPING DECISION").

Every XSD and every XML payload here is HAND-WRITTEN and SYNTHETIC — none of it is, or is derived
from, the real ANS Padrão-TISS schema. Two synthetic artifacts are exercised:

  1. The REPO-SHIPPED `spec/policies/ans/synthetic-tiss-v1.xsd`, referenced by the (DRAFT,
     unratified) shipped `spec/policies/ans/tiss-schema-pin.yaml`. Tests against the DEFAULT
     resolution path prove this exact artifact is genuinely inert today.
  2. A SECOND, independently-defined synthetic fixture built fresh in `tmp_path` by every test
     that needs a *ratified*-shaped pin (mission instruction: "prove with a second synthetic
     'ratified' fixture in tmp_path") — this is what activation would look like once an SME
     ratifies, without this test suite ever needing (or claiming to have) the real schema.

Both fixtures share the SAME element vocabulary/structure on purpose, so the structural
(valid/violation) test bodies below exercise the identical schema shape whichever artifact is
behind the gate — proving ratification only toggles AVAILABILITY, never the validation logic.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from maezo.tools.workers.tiss_schema_pin import (
    REASON_ACCOUNTABILITY_INCOMPLETE,
    REASON_ARTIFACT_MISSING,
    REASON_ARTIFACT_STILL_SYNTHETIC,
    REASON_FILE_NOT_FOUND,
    REASON_INVALID_ENCODING,
    REASON_INVALID_SCHEMA,
    REASON_INVALID_YAML,
    REASON_RATIFICADO_FLAG_FALSE,
    REASON_STATUS_NOT_RATIFIED,
    TissSchemaPin,
    TissSchemaPinUnavailableError,
    TissSchemaPinValidator,
    load_tiss_schema_pin,
    tiss_schema_pin_gate_entry,
)

# ---------------------------------------------------------------------------------------------
# Synthetic fixture — schema + payloads. Structurally identical to
# spec/policies/ans/synthetic-tiss-v1.xsd (same element vocabulary) but defined independently
# here, as its own artifact, per the mission's "second synthetic fixture" instruction. NONE of
# this is real TISS wire content — see module docstring.
# ---------------------------------------------------------------------------------------------

_NS = "urn:maezo:synthetic:tiss-schema-pin:v1"

_FIXTURE_XSD = f"""<?xml version="1.0" encoding="UTF-8"?>
<!-- SYNTHETIC — test-local fixture, NOT the real ANS Padrão-TISS XSD. -->
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema"
           xmlns:tiss="{_NS}"
           targetNamespace="{_NS}"
           elementFormDefault="qualified">
  <xs:element name="LoteGuiasSintetico">
    <xs:complexType>
      <xs:sequence>
        <xs:element name="numeroLoteSintetico" type="xs:string" minOccurs="1" maxOccurs="1"/>
        <xs:element name="competenciaSintetica" type="xs:string" minOccurs="1" maxOccurs="1"/>
        <xs:element name="totalGuiasSintetico" type="xs:integer" minOccurs="1" maxOccurs="1"/>
        <xs:element name="guiaSintetica" type="xs:string" minOccurs="1" maxOccurs="2"/>
      </xs:sequence>
    </xs:complexType>
  </xs:element>
</xs:schema>
"""

_VALID_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<tiss:LoteGuiasSintetico xmlns:tiss="{_NS}">
  <tiss:numeroLoteSintetico>LOTE-0001</tiss:numeroLoteSintetico>
  <tiss:competenciaSintetica>2026-06</tiss:competenciaSintetica>
  <tiss:totalGuiasSintetico>2</tiss:totalGuiasSintetico>
  <tiss:guiaSintetica>GUIA-0001</tiss:guiaSintetica>
  <tiss:guiaSintetica>GUIA-0002</tiss:guiaSintetica>
</tiss:LoteGuiasSintetico>
"""

#: Missing element — required scalar `competenciaSintetica` entirely absent.
_MISSING_ELEMENT_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<tiss:LoteGuiasSintetico xmlns:tiss="{_NS}">
  <tiss:numeroLoteSintetico>LOTE-0001</tiss:numeroLoteSintetico>
  <tiss:totalGuiasSintetico>2</tiss:totalGuiasSintetico>
  <tiss:guiaSintetica>GUIA-0001</tiss:guiaSintetica>
  <tiss:guiaSintetica>GUIA-0002</tiss:guiaSintetica>
</tiss:LoteGuiasSintetico>
"""

#: Wrong type — `totalGuiasSintetico` (xs:integer) gets a non-numeric value. The value is a
#: canary string so tests can assert it never reaches a diagnostic (PHI discipline).
_PHI_CANARY = "PHI-CANARY-SHOULD-NOT-LEAK-12345"
_WRONG_TYPE_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<tiss:LoteGuiasSintetico xmlns:tiss="{_NS}">
  <tiss:numeroLoteSintetico>LOTE-0001</tiss:numeroLoteSintetico>
  <tiss:competenciaSintetica>2026-06</tiss:competenciaSintetica>
  <tiss:totalGuiasSintetico>{_PHI_CANARY}</tiss:totalGuiasSintetico>
  <tiss:guiaSintetica>GUIA-0001</tiss:guiaSintetica>
</tiss:LoteGuiasSintetico>
"""

#: Cardinality — `guiaSintetica` (maxOccurs=2) occurs 3 times.
_CARDINALITY_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<tiss:LoteGuiasSintetico xmlns:tiss="{_NS}">
  <tiss:numeroLoteSintetico>LOTE-0001</tiss:numeroLoteSintetico>
  <tiss:competenciaSintetica>2026-06</tiss:competenciaSintetica>
  <tiss:totalGuiasSintetico>3</tiss:totalGuiasSintetico>
  <tiss:guiaSintetica>GUIA-0001</tiss:guiaSintetica>
  <tiss:guiaSintetica>GUIA-0002</tiss:guiaSintetica>
  <tiss:guiaSintetica>GUIA-0003</tiss:guiaSintetica>
</tiss:LoteGuiasSintetico>
"""

#: Namespace — same content as _VALID_XML but with no namespace declared at all.
_NAMESPACE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<LoteGuiasSintetico>
  <numeroLoteSintetico>LOTE-0001</numeroLoteSintetico>
  <competenciaSintetica>2026-06</competenciaSintetica>
  <totalGuiasSintetico>2</totalGuiasSintetico>
  <guiaSintetica>GUIA-0001</guiaSintetica>
  <guiaSintetica>GUIA-0002</guiaSintetica>
</LoteGuiasSintetico>
"""

_MALFORMED_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<tiss:LoteGuiasSintetico xmlns:tiss="{_NS}">
  <tiss:numeroLoteSintetico>LOTE-0001</tiss:numeroLoteSintetico>
"""  # unclosed


def _write_xml(tmp_path: Path, name: str, content: str) -> str:
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return str(path)


def _write_pin_manifest(
    tmp_path: Path,
    *,
    status: str = "RATIFIED",
    ratificado: Any = True,
    revisor: Any = "Dra. Auditoria TISS Sintetica",
    ratificado_em: Any = "2026-08-09",
    synthetic: Any = False,
    xsd_filename: str = "ratified-fixture-tiss.xsd",
    write_xsd: bool = True,
    xsd_content: str = _FIXTURE_XSD,
    padrao_tiss_versao: str = "TEST-FIXTURE-RATIFIED-v1",
    report_type: str = "TEST_FIXTURE_STRUCTURE_ONLY",
    extra_top_level: dict[str, Any] | None = None,
    manifest_name: str = "tiss-schema-pin.yaml",
) -> Path:
    """Write a pin manifest in `tmp_path` — defaults to a FULLY RATIFIED shape (the "second
    synthetic 'ratified' fixture" the mission asks for) so individual gate tests can override
    exactly ONE field to prove that gate alone blocks. NONE of the field VALUES here (version
    strings, report_type) resemble real TISS/ANS content — see module docstring.
    """
    data: dict[str, Any] = {
        "version": 1,
        "status": status,
        "ratificado": ratificado,
        "revisor": revisor,
        "ratificado_em": ratificado_em,
        "schema_artifact": {
            "xsd_filename": xsd_filename,
            "synthetic": synthetic,
            "sha256": "TEST-PLACEHOLDER-SHA256",
            "padrao_tiss_versao": padrao_tiss_versao,
            "report_type": report_type,
        },
        "observacao": "test fixture — not a real ratification",
    }
    if extra_top_level:
        data.update(extra_top_level)

    manifest_path = tmp_path / manifest_name
    manifest_path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    if write_xsd:
        (tmp_path / xsd_filename).write_text(xsd_content, encoding="utf-8")
    return manifest_path


def _ratified_validator(tmp_path: Path, **overrides: Any) -> TissSchemaPinValidator:
    manifest_path = _write_pin_manifest(tmp_path, **overrides)
    return TissSchemaPinValidator(manifest_path=manifest_path)


# ---------------------------------------------------------------------------------------------
# Loader — infrastructure failure modes (fail-closed, typed error, mirrors legal_bases_matrix.py)
# ---------------------------------------------------------------------------------------------


def test_missing_manifest_file_raises_file_not_found(tmp_path: Path) -> None:
    with pytest.raises(TissSchemaPinUnavailableError) as exc:
        load_tiss_schema_pin(path=tmp_path / "does-not-exist.yaml")
    assert exc.value.reason == REASON_FILE_NOT_FOUND
    assert exc.value.code == "ERR_TISS_SCHEMA_PIN_UNAVAILABLE"


def test_malformed_yaml_raises_invalid_yaml(tmp_path: Path) -> None:
    path = tmp_path / "broken.yaml"
    path.write_text("status: [unterminated", encoding="utf-8")
    with pytest.raises(TissSchemaPinUnavailableError) as exc:
        load_tiss_schema_pin(path=path)
    assert exc.value.reason == REASON_INVALID_YAML


def test_non_utf8_manifest_raises_invalid_encoding(tmp_path: Path) -> None:
    path = tmp_path / "cp1252.yaml"
    path.write_bytes("status: DRAFT # jurídico\n".encode("cp1252"))
    with pytest.raises(TissSchemaPinUnavailableError) as exc:
        load_tiss_schema_pin(path=path)
    assert exc.value.reason == REASON_INVALID_ENCODING


def test_non_mapping_root_raises_invalid_schema(tmp_path: Path) -> None:
    path = tmp_path / "list-root.yaml"
    path.write_text("- item1\n- item2\n", encoding="utf-8")
    with pytest.raises(TissSchemaPinUnavailableError) as exc:
        load_tiss_schema_pin(path=path)
    assert exc.value.reason == REASON_INVALID_SCHEMA


def test_unknown_top_level_key_raises_invalid_schema(tmp_path: Path) -> None:
    manifest_path = _write_pin_manifest(tmp_path, extra_top_level={"bogus_extra_key": 1})
    with pytest.raises(TissSchemaPinUnavailableError) as exc:
        load_tiss_schema_pin(path=manifest_path)
    assert exc.value.reason == REASON_INVALID_SCHEMA
    assert "bogus_extra_key" in exc.value.message


def test_duplicate_top_level_key_raises_invalid_yaml(tmp_path: Path) -> None:
    """A duplicated `ratificado:` key must refuse rather than silently letting the LAST
    occurrence (stock PyYAML behavior) win unnoticed."""
    raw = (
        "version: 1\n"
        "status: RATIFIED\n"
        "ratificado: false\n"
        "ratificado: true\n"
        "revisor: 'x'\n"
        "ratificado_em: '2026-08-09'\n"
        "schema_artifact:\n"
        "  xsd_filename: 'x.xsd'\n"
        "  synthetic: false\n"
    )
    path = tmp_path / "dup.yaml"
    path.write_text(raw, encoding="utf-8")
    with pytest.raises(TissSchemaPinUnavailableError) as exc:
        load_tiss_schema_pin(path=path)
    assert exc.value.reason == REASON_INVALID_YAML


# ---------------------------------------------------------------------------------------------
# Loader — the ratification gate itself. Core mission ask: DRAFT can never validate-as-PASS,
# even with `ratificado: true` forged while `status` stays DRAFT.
# ---------------------------------------------------------------------------------------------


def test_shipped_draft_manifest_refuses_via_default_resolution() -> None:
    """The REPO-SHIPPED `spec/policies/ans/tiss-schema-pin.yaml` is DRAFT. The default
    resolution path (no explicit path/env override — what any real caller would use) must
    refuse, proving the artifact that ships to every environment today is genuinely inert."""
    with pytest.raises(TissSchemaPinUnavailableError) as exc:
        load_tiss_schema_pin()
    assert exc.value.reason == REASON_STATUS_NOT_RATIFIED


def test_forged_ratificado_true_with_status_still_draft_refuses(tmp_path: Path) -> None:
    """THE core ratification-gate test: forging `ratificado: true` (with fully-populated
    accountability fields and a non-synthetic artifact flag) while `status` is left as the
    literal string "DRAFT" must STILL refuse — gate 1 is independent of, and checked before,
    gate 2."""
    manifest_path = _write_pin_manifest(tmp_path, status="DRAFT", ratificado=True)
    with pytest.raises(TissSchemaPinUnavailableError) as exc:
        load_tiss_schema_pin(path=manifest_path)
    assert exc.value.reason == REASON_STATUS_NOT_RATIFIED


def test_ratified_status_but_ratificado_flag_false_refuses(tmp_path: Path) -> None:
    manifest_path = _write_pin_manifest(tmp_path, status="RATIFIED", ratificado=False)
    with pytest.raises(TissSchemaPinUnavailableError) as exc:
        load_tiss_schema_pin(path=manifest_path)
    assert exc.value.reason == REASON_RATIFICADO_FLAG_FALSE


def test_ratificado_as_string_not_boolean_refuses(tmp_path: Path) -> None:
    """`ratificado: "true"` (a string) must NOT count — only the boolean literal does (mirrors
    auth_criteria.py's `is not True` fail-closed pin idiom)."""
    manifest_path = _write_pin_manifest(tmp_path, ratificado="true")
    with pytest.raises(TissSchemaPinUnavailableError) as exc:
        load_tiss_schema_pin(path=manifest_path)
    assert exc.value.reason == REASON_RATIFICADO_FLAG_FALSE


@pytest.mark.parametrize("revisor,ratificado_em", [("", "2026-08-09"), ("Dra. X", ""), (None, None)])
def test_ratified_but_accountability_incomplete_refuses(
    tmp_path: Path, revisor: str | None, ratificado_em: str | None
) -> None:
    manifest_path = _write_pin_manifest(tmp_path, revisor=revisor, ratificado_em=ratificado_em)
    with pytest.raises(TissSchemaPinUnavailableError) as exc:
        load_tiss_schema_pin(path=manifest_path)
    assert exc.value.reason == REASON_ACCOUNTABILITY_INCOMPLETE


def test_ratified_but_artifact_still_synthetic_refuses(tmp_path: Path) -> None:
    """Flipping status/ratificado/revisor/ratificado_em WITHOUT also swapping the synthetic XSD
    (`schema_artifact.synthetic` still `true`) must refuse — activation requires BOTH acts."""
    manifest_path = _write_pin_manifest(tmp_path, synthetic=True)
    with pytest.raises(TissSchemaPinUnavailableError) as exc:
        load_tiss_schema_pin(path=manifest_path)
    assert exc.value.reason == REASON_ARTIFACT_STILL_SYNTHETIC


def test_ratified_manifest_pointing_at_missing_xsd_file_refuses(tmp_path: Path) -> None:
    manifest_path = _write_pin_manifest(tmp_path, write_xsd=False)
    with pytest.raises(TissSchemaPinUnavailableError) as exc:
        load_tiss_schema_pin(path=manifest_path)
    assert exc.value.reason == REASON_ARTIFACT_MISSING


def test_fully_ratified_manifest_loads_successfully(tmp_path: Path) -> None:
    """The "second synthetic 'ratified' fixture in tmp_path", loading successfully — proof the
    gate opens when (and only when) all four fields genuinely agree."""
    manifest_path = _write_pin_manifest(tmp_path)
    pin = load_tiss_schema_pin(path=manifest_path)
    assert isinstance(pin, TissSchemaPin)
    assert pin.xsd_path == tmp_path / "ratified-fixture-tiss.xsd"
    assert pin.xsd_path.is_file()
    assert pin.revisor == "Dra. Auditoria TISS Sintetica"
    assert pin.ratificado_em == "2026-08-09"
    assert pin.padrao_tiss_versao == "TEST-FIXTURE-RATIFIED-v1"


def test_env_override_is_honored(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest_path = _write_pin_manifest(tmp_path)
    monkeypatch.setenv("MAEZO_TISS_SCHEMA_PIN_MANIFEST_PATH", str(manifest_path))
    pin = load_tiss_schema_pin()
    assert pin.xsd_path == tmp_path / "ratified-fixture-tiss.xsd"


def test_explicit_path_wins_over_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_manifest = _write_pin_manifest(tmp_path, manifest_name="env.yaml", xsd_filename="env.xsd")
    explicit_manifest = _write_pin_manifest(
        tmp_path, manifest_name="explicit.yaml", xsd_filename="explicit.xsd"
    )
    monkeypatch.setenv("MAEZO_TISS_SCHEMA_PIN_MANIFEST_PATH", str(env_manifest))
    pin = load_tiss_schema_pin(path=explicit_manifest)
    assert pin.xsd_path.name == "explicit.xsd"


# ---------------------------------------------------------------------------------------------
# Validator — refuses with a typed error while unavailable (never a False/PASS result).
# ---------------------------------------------------------------------------------------------


def test_validator_raises_using_shipped_draft_manifest_regardless_of_payload(tmp_path: Path) -> None:
    """DRAFT can never validate-as-PASS: even a well-formed, schema-shaped payload is refused via
    the typed error BEFORE the validator ever looks at it — using the real shipped manifest."""
    dataset_ref = _write_xml(tmp_path, "would-be-valid.xml", _VALID_XML)
    validator = TissSchemaPinValidator()  # default resolution -> shipped DRAFT manifest
    with pytest.raises(TissSchemaPinUnavailableError) as exc:
        validator.validate(dataset_ref=dataset_ref)
    assert exc.value.reason == REASON_STATUS_NOT_RATIFIED


def test_validator_raises_for_forged_ratificado_with_status_draft(tmp_path: Path) -> None:
    manifest_path = _write_pin_manifest(tmp_path, status="DRAFT", ratificado=True)
    dataset_ref = _write_xml(tmp_path, "payload.xml", _VALID_XML)
    validator = TissSchemaPinValidator(manifest_path=manifest_path)
    with pytest.raises(TissSchemaPinUnavailableError) as exc:
        validator.validate(dataset_ref=dataset_ref)
    assert exc.value.reason == REASON_STATUS_NOT_RATIFIED


# ---------------------------------------------------------------------------------------------
# Validator — structural correctness once ratified (item 2 + item 3's "structure keeps passing").
# ---------------------------------------------------------------------------------------------


def test_valid_payload_passes(tmp_path: Path) -> None:
    validator = _ratified_validator(tmp_path)
    dataset_ref = _write_xml(tmp_path, "valid.xml", _VALID_XML)
    result = validator.validate(dataset_ref=dataset_ref)
    assert result.schema_valid is True
    assert result.diagnostics == ()
    assert result.schema_version == "TEST-FIXTURE-RATIFIED-v1"


def test_missing_element_violation_fails_with_bounded_diagnostic(tmp_path: Path) -> None:
    validator = _ratified_validator(tmp_path)
    dataset_ref = _write_xml(tmp_path, "missing.xml", _MISSING_ELEMENT_XML)
    result = validator.validate(dataset_ref=dataset_ref)
    assert result.schema_valid is False
    assert len(result.diagnostics) == 1
    diag = result.diagnostics[0]
    assert diag.kind == "elemento_ausente"
    assert diag.element == "competenciaSintetica"


def test_wrong_type_violation_fails_with_bounded_diagnostic(tmp_path: Path) -> None:
    validator = _ratified_validator(tmp_path)
    dataset_ref = _write_xml(tmp_path, "wrongtype.xml", _WRONG_TYPE_XML)
    result = validator.validate(dataset_ref=dataset_ref)
    assert result.schema_valid is False
    assert len(result.diagnostics) == 1
    diag = result.diagnostics[0]
    assert diag.kind == "tipo_invalido"
    assert diag.element == "totalGuiasSintetico"


def test_cardinality_violation_fails_with_bounded_diagnostic(tmp_path: Path) -> None:
    validator = _ratified_validator(tmp_path)
    dataset_ref = _write_xml(tmp_path, "cardinality.xml", _CARDINALITY_XML)
    result = validator.validate(dataset_ref=dataset_ref)
    assert result.schema_valid is False
    assert len(result.diagnostics) == 1
    diag = result.diagnostics[0]
    assert diag.kind == "cardinalidade"
    assert diag.element == "guiaSintetica"


def test_namespace_violation_fails_with_bounded_diagnostic(tmp_path: Path) -> None:
    validator = _ratified_validator(tmp_path)
    dataset_ref = _write_xml(tmp_path, "namespace.xml", _NAMESPACE_XML)
    result = validator.validate(dataset_ref=dataset_ref)
    assert result.schema_valid is False
    assert len(result.diagnostics) == 1
    diag = result.diagnostics[0]
    assert diag.kind == "namespace_invalido"


def test_malformed_xml_payload_fails_with_bounded_diagnostic(tmp_path: Path) -> None:
    validator = _ratified_validator(tmp_path)
    dataset_ref = _write_xml(tmp_path, "malformed.xml", _MALFORMED_XML)
    result = validator.validate(dataset_ref=dataset_ref)
    assert result.schema_valid is False
    assert result.diagnostics[0].kind == "xml_malformado"


def test_blank_dataset_ref_fails_with_bounded_diagnostic(tmp_path: Path) -> None:
    validator = _ratified_validator(tmp_path)
    result = validator.validate(dataset_ref="")
    assert result.schema_valid is False
    assert result.diagnostics[0].kind == "dataset_ref_ausente"


def test_dataset_ref_not_a_real_file_fails_with_bounded_diagnostic(tmp_path: Path) -> None:
    validator = _ratified_validator(tmp_path)
    result = validator.validate(dataset_ref=str(tmp_path / "does-not-exist.xml"))
    assert result.schema_valid is False
    assert result.diagnostics[0].kind == "dataset_ref_nao_resolve_a_arquivo"


def test_validator_reusable_across_multiple_datasets(tmp_path: Path) -> None:
    """A single validator instance (as a future once-per-registration seam would be) validates
    multiple datasets correctly — the cached schema compile does not leak state across calls."""
    validator = _ratified_validator(tmp_path)
    ok = validator.validate(dataset_ref=_write_xml(tmp_path, "ok.xml", _VALID_XML))
    bad = validator.validate(dataset_ref=_write_xml(tmp_path, "bad.xml", _MISSING_ELEMENT_XML))
    assert ok.schema_valid is True
    assert bad.schema_valid is False


# ---------------------------------------------------------------------------------------------
# PHI discipline — bounded diagnostics NEVER carry payload content.
# ---------------------------------------------------------------------------------------------


def test_wrong_type_diagnostic_never_leaks_the_offending_value(tmp_path: Path) -> None:
    """The canary value in `_WRONG_TYPE_XML` must not appear ANYWHERE in the returned
    diagnostics — not in `.kind`, not in `.element`, not in `.bounded_message()`."""
    validator = _ratified_validator(tmp_path)
    dataset_ref = _write_xml(tmp_path, "wrongtype.xml", _WRONG_TYPE_XML)
    result = validator.validate(dataset_ref=dataset_ref)
    assert result.schema_valid is False
    for diag in result.diagnostics:
        assert _PHI_CANARY not in diag.kind
        assert _PHI_CANARY not in (diag.element or "")
        assert _PHI_CANARY not in diag.bounded_message()


@pytest.mark.parametrize(
    "xml_fixture",
    [_MISSING_ELEMENT_XML, _WRONG_TYPE_XML, _CARDINALITY_XML, _NAMESPACE_XML, _MALFORMED_XML],
)
def test_no_violation_diagnostic_ever_echoes_raw_libxml_message_text(
    tmp_path: Path, xml_fixture: str
) -> None:
    """Structural fence: `bounded_message()` never contains the literal substrings libxml2 uses
    to narrate a value (`"is not a valid value"`) — bounded diagnostics are always resynthesized
    from `kind`/`element`/`line`, never the raw error-log message."""
    validator = _ratified_validator(tmp_path)
    dataset_ref = _write_xml(tmp_path, "payload.xml", xml_fixture)
    result = validator.validate(dataset_ref=dataset_ref)
    assert result.schema_valid is False
    for diag in result.diagnostics:
        rendered = diag.bounded_message()
        assert "is not a valid value" not in rendered
        assert "atomic type" not in rendered


# ---------------------------------------------------------------------------------------------
# Consumption contract (`tiss_schema_pin_gate_entry`) — demonstration only, NOT wired.
# ---------------------------------------------------------------------------------------------


def test_gate_entry_never_available_and_never_pass_using_shipped_draft_manifest() -> None:
    result = tiss_schema_pin_gate_entry({"dataset_ref": "irrelevant-because-refused-first"})
    assert result["tiss_schema_pin_available"] is False
    assert result["tiss_schema_pin_schema_valid"] is False
    assert result["tiss_schema_pin_reason"] == REASON_STATUS_NOT_RATIFIED


def test_gate_entry_available_and_computed_once_ratified(tmp_path: Path) -> None:
    manifest_path = _write_pin_manifest(tmp_path)
    validator = TissSchemaPinValidator(manifest_path=manifest_path)
    dataset_ref = _write_xml(tmp_path, "valid.xml", _VALID_XML)
    result = tiss_schema_pin_gate_entry({"dataset_ref": dataset_ref}, pin_validator=validator)
    assert result["tiss_schema_pin_available"] is True
    assert result["tiss_schema_pin_schema_valid"] is True
    assert result["tiss_schema_pin_diagnostics"] == []
    assert result["tiss_schema_pin_version"] == "TEST-FIXTURE-RATIFIED-v1"


def test_gate_entry_available_but_schema_invalid_is_still_not_a_pass(tmp_path: Path) -> None:
    manifest_path = _write_pin_manifest(tmp_path)
    validator = TissSchemaPinValidator(manifest_path=manifest_path)
    dataset_ref = _write_xml(tmp_path, "missing.xml", _MISSING_ELEMENT_XML)
    result = tiss_schema_pin_gate_entry({"dataset_ref": dataset_ref}, pin_validator=validator)
    assert result["tiss_schema_pin_available"] is True
    assert result["tiss_schema_pin_schema_valid"] is False
    assert result["tiss_schema_pin_diagnostics"]


def test_gate_entry_unavailable_never_reports_schema_valid_true() -> None:
    """No code path returns `tiss_schema_pin_available=False` alongside
    `tiss_schema_pin_schema_valid=True` — unavailable can never read as PASS."""
    result = tiss_schema_pin_gate_entry({})
    if result["tiss_schema_pin_available"] is False:
        assert result["tiss_schema_pin_schema_valid"] is False


def test_gate_entry_missing_dataset_ref_variable_defaults_to_blank_not_a_crash() -> None:
    result = tiss_schema_pin_gate_entry({"some_other_variable": "x"})
    assert result["tiss_schema_pin_available"] is False


# ---------------------------------------------------------------------------------------------
# Structural fence — this dark-build seam is NOT wired into ans_submit.py this wave.
# ---------------------------------------------------------------------------------------------


def test_tiss_schema_pin_not_imported_or_referenced_by_ans_submit() -> None:
    """Regression fence for the module docstring's "SCOPING DECISION": `ans_submit.py` must not
    import this module, reference `TissSchemaPinValidator`, or reference
    `tiss_schema_pin_gate_entry` — the seam stays dark until a future, separately reviewed
    change wires it in."""
    import maezo.tools.workers.ans_submit as ans_submit_module

    assert not hasattr(ans_submit_module, "TissSchemaPinValidator")
    assert not hasattr(ans_submit_module, "tiss_schema_pin_gate_entry")
    assert not hasattr(ans_submit_module, "tiss_schema_pin")

    import inspect

    source = inspect.getsource(ans_submit_module)
    assert "tiss_schema_pin" not in source


def test_register_ans_submit_workers_topics_unchanged() -> None:
    """The seven BPMN topics `register_ans_submit_workers` owns are UNCHANGED by this dark
    build — no eighth topic was added."""
    from maezo.tools.workers.ans_submit import register_ans_submit_workers
    from maezo.tools.workers.harness import FakeKafkaPublisher, FakeWorkerTransport, WorkerHarness

    harness = WorkerHarness(FakeWorkerTransport(), worker_id="probe")
    register_ans_submit_workers(harness, FakeKafkaPublisher())
    topics = set(harness.registered_topics)
    assert topics == {
        "regulatorio.anssubmit.assemble",
        "regulatorio.anssubmit.validate",
        "regulatorio.anssubmit.submit",
        "regulatorio.anssubmit.track_protocol",
        "regulatorio.anssubmit.retransmit",
        "regulatorio.anssubmit.publish_completed",
        "regulatorio.anssubmit.notify_regulatorio",
    }


# ---------------------------------------------------------------------------------------------
# Shipped-artifact smoke test — the REAL repo file, independent of the ratification gate.
# ---------------------------------------------------------------------------------------------


def test_shipped_synthetic_xsd_compiles_and_enforces_the_same_structure() -> None:
    """Ties the actual repo-committed `spec/policies/ans/synthetic-tiss-v1.xsd` to this test
    suite's structural claims — bypassing the ratification gate entirely (gate refusal is
    orthogonal to "is this a well-formed, structurally-meaningful XSD"). Uses the SAME element
    vocabulary as the tmp_path fixture above by construction (both hand-authored against the
    same design), so the identical payload constants are reused here."""
    from lxml import etree

    from maezo.agents import resolve_spec_dir

    xsd_path = resolve_spec_dir() / "policies" / "ans" / "synthetic-tiss-v1.xsd"
    assert xsd_path.is_file()
    schema = etree.XMLSchema(etree.parse(str(xsd_path)))

    assert schema.validate(etree.fromstring(_VALID_XML.encode("utf-8"))) is True
    assert schema.validate(etree.fromstring(_MISSING_ELEMENT_XML.encode("utf-8"))) is False
    assert schema.validate(etree.fromstring(_WRONG_TYPE_XML.encode("utf-8"))) is False
    assert schema.validate(etree.fromstring(_CARDINALITY_XML.encode("utf-8"))) is False
    assert schema.validate(etree.fromstring(_NAMESPACE_XML.encode("utf-8"))) is False


def test_shipped_xsd_has_synthetic_header_comment() -> None:
    """The file-header XML comment must carry the SYNTHETIC label verbatim — a cheap, durable
    regression fence against the artifact ever being silently swapped for something that looks
    real without the manifest's `schema_artifact.synthetic` flag also changing."""
    from maezo.agents import resolve_spec_dir

    xsd_path = resolve_spec_dir() / "policies" / "ans" / "synthetic-tiss-v1.xsd"
    text = xsd_path.read_text(encoding="utf-8")
    assert "SYNTHETIC" in text
    assert "NOT the real ANS" in text
