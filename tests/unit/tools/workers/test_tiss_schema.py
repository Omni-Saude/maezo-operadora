"""Unit tests for `maezo.tools.workers.tiss_schema` — the TISS/XSD validation seam (T2.6-2,
design §2.B).

Hand-written FIXTURE XSDs (tmp_path) — NOT real ANS "Padrão TISS" schemas. The real vendored
XSD set + the exact version in force are an OPEN EXTERNAL DEPENDENCY (SME-gated, design §2.B/§7;
see `tiss_schema.py`'s module docstring). These tests prove the validation MECHANISM — loader,
version-pin resolution, fail-closed rules — against a minimal representative schema, exactly the
posture the task's honest-seam-over-fake-substance instruction calls for.

Every fail-closed branch design §2.B's verification plan enumerates is covered:
  - no version pinned (config absent) -> False
  - no vendored XSD for report_type -> False
  - dataset_ref blank / not a real file -> False
  - malformed XML -> False
  - well-formed XML violating the schema -> False + structured errors
  - well-formed XML validating cleanly -> True
  - the resolved version is always surfaced on the result (audit record, design §2.B)
"""

from __future__ import annotations

import os
from pathlib import Path

from maezo.tools.workers.tiss_schema import (
    TISS_SCHEMA_ROOT_ENV,
    TISS_SCHEMA_VERSION_ENV,
    TissSchemaValidator,
)

_VERSION = "FIXTURE-0"
_REPORT_TYPE = "RN_124_SIP"

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
_SCHEMA_INVALID_XML = "<loteGuias><campoInexistente/></loteGuias>"
_MALFORMED_XML = "<loteGuias><numeroLote>1</numeroLote>"  # unclosed tag


def _vendor_xsd(tmp_path: Path, *, version: str = _VERSION, report_type: str = _REPORT_TYPE) -> Path:
    version_dir = tmp_path / version
    version_dir.mkdir(parents=True, exist_ok=True)
    xsd_path = version_dir / f"{report_type}.xsd"
    xsd_path.write_text(_FIXTURE_XSD, encoding="utf-8")
    return xsd_path


def _write_xml(tmp_path: Path, name: str, content: str) -> str:
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return str(path)


# ---------------------------------------------------------------------------
# Version pin resolution
# ---------------------------------------------------------------------------


def test_no_version_pinned_fails_closed(tmp_path: Path) -> None:
    """No `version=` override and no `MAEZO_TISS_SCHEMA_VERSION` env -> fail-closed, no schema
    version resolved at all."""
    validator = TissSchemaValidator(schema_root=tmp_path)
    result = validator.validate(
        report_type=_REPORT_TYPE, dataset_ref=_write_xml(tmp_path, "d.xml", _VALID_XML)
    )
    assert result.schema_valid is False
    assert result.tiss_schema_version is None
    assert result.errors


def test_version_env_override_is_honored(tmp_path: Path) -> None:
    """`MAEZO_TISS_SCHEMA_VERSION` env is honored when no explicit `version=` override is given."""
    _vendor_xsd(tmp_path)
    os.environ[TISS_SCHEMA_VERSION_ENV] = _VERSION
    os.environ[TISS_SCHEMA_ROOT_ENV] = str(tmp_path)
    try:
        validator = TissSchemaValidator()
        result = validator.validate(
            report_type=_REPORT_TYPE, dataset_ref=_write_xml(tmp_path, "d.xml", _VALID_XML)
        )
        assert result.schema_valid is True
        assert result.tiss_schema_version == _VERSION
    finally:
        del os.environ[TISS_SCHEMA_VERSION_ENV]
        del os.environ[TISS_SCHEMA_ROOT_ENV]


def test_explicit_version_override_wins_over_env(tmp_path: Path) -> None:
    """Explicit constructor `version=`/`schema_root=` (test-pinning hook) takes precedence over
    env vars — mirrors `CeilingResolver`'s override precedence."""
    _vendor_xsd(tmp_path)
    os.environ[TISS_SCHEMA_VERSION_ENV] = "some-other-version"
    try:
        validator = TissSchemaValidator(schema_root=tmp_path, version=_VERSION)
        result = validator.validate(
            report_type=_REPORT_TYPE, dataset_ref=_write_xml(tmp_path, "d.xml", _VALID_XML)
        )
        assert result.schema_valid is True
        assert result.tiss_schema_version == _VERSION
    finally:
        del os.environ[TISS_SCHEMA_VERSION_ENV]


# ---------------------------------------------------------------------------
# Missing / unvendored XSD
# ---------------------------------------------------------------------------


def test_missing_vendored_xsd_for_report_type_fails_closed(tmp_path: Path) -> None:
    """A pinned version dir exists, but no XSD for THIS report_type -> fail-closed."""
    (tmp_path / _VERSION).mkdir()
    validator = TissSchemaValidator(schema_root=tmp_path, version=_VERSION)
    result = validator.validate(
        report_type="RN_209_UTILIZACAO", dataset_ref=_write_xml(tmp_path, "d.xml", _VALID_XML)
    )
    assert result.schema_valid is False
    assert result.tiss_schema_version == _VERSION
    assert "nao vendorizado" in result.errors[0] or "vendorizado" in result.errors[0]


def test_unversioned_schema_root_fails_closed(tmp_path: Path) -> None:
    """The version subdirectory itself does not exist -> fail-closed (never a crash)."""
    validator = TissSchemaValidator(schema_root=tmp_path, version="v-nao-existe")
    result = validator.validate(
        report_type=_REPORT_TYPE, dataset_ref=_write_xml(tmp_path, "d.xml", _VALID_XML)
    )
    assert result.schema_valid is False


def test_malformed_xsd_fails_closed(tmp_path: Path) -> None:
    """A vendored 'XSD' that is not valid schema XML -> fail-closed, never a crash."""
    version_dir = tmp_path / _VERSION
    version_dir.mkdir()
    (version_dir / f"{_REPORT_TYPE}.xsd").write_text("<not-a-schema>", encoding="utf-8")
    validator = TissSchemaValidator(schema_root=tmp_path, version=_VERSION)
    result = validator.validate(
        report_type=_REPORT_TYPE, dataset_ref=_write_xml(tmp_path, "d.xml", _VALID_XML)
    )
    assert result.schema_valid is False


# ---------------------------------------------------------------------------
# dataset_ref resolution
# ---------------------------------------------------------------------------


def test_blank_dataset_ref_fails_closed(tmp_path: Path) -> None:
    _vendor_xsd(tmp_path)
    validator = TissSchemaValidator(schema_root=tmp_path, version=_VERSION)
    result = validator.validate(report_type=_REPORT_TYPE, dataset_ref="")
    assert result.schema_valid is False
    assert result.tiss_schema_version == _VERSION


def test_dataset_ref_not_a_real_file_fails_closed(tmp_path: Path) -> None:
    """Today's stub `prepare_submission` produces a synthetic string, never a real path — this is
    the exact branch that keeps the pipeline fail-closed in production (design §2.B)."""
    _vendor_xsd(tmp_path)
    validator = TissSchemaValidator(schema_root=tmp_path, version=_VERSION)
    result = validator.validate(report_type=_REPORT_TYPE, dataset_ref="dataset-RN_124_SIP-2026-06")
    assert result.schema_valid is False


# ---------------------------------------------------------------------------
# XML validation outcomes
# ---------------------------------------------------------------------------


def test_malformed_xml_fails_closed(tmp_path: Path) -> None:
    _vendor_xsd(tmp_path)
    validator = TissSchemaValidator(schema_root=tmp_path, version=_VERSION)
    result = validator.validate(
        report_type=_REPORT_TYPE, dataset_ref=_write_xml(tmp_path, "bad.xml", _MALFORMED_XML)
    )
    assert result.schema_valid is False
    assert result.errors


def test_schema_invalid_xml_fails_with_structured_errors(tmp_path: Path) -> None:
    _vendor_xsd(tmp_path)
    validator = TissSchemaValidator(schema_root=tmp_path, version=_VERSION)
    result = validator.validate(
        report_type=_REPORT_TYPE, dataset_ref=_write_xml(tmp_path, "invalid.xml", _SCHEMA_INVALID_XML)
    )
    assert result.schema_valid is False
    assert result.errors
    assert result.tiss_schema_version == _VERSION


def test_schema_valid_xml_passes(tmp_path: Path) -> None:
    _vendor_xsd(tmp_path)
    validator = TissSchemaValidator(schema_root=tmp_path, version=_VERSION)
    result = validator.validate(
        report_type=_REPORT_TYPE, dataset_ref=_write_xml(tmp_path, "ok.xml", _VALID_XML)
    )
    assert result.schema_valid is True
    assert result.errors == ()
    assert result.tiss_schema_version == _VERSION


def test_validator_is_reusable_across_calls(tmp_path: Path) -> None:
    """A single `TissSchemaValidator` instance (as registered once per worker harness) validates
    multiple datasets correctly — the cached schema load does not leak state across calls."""
    _vendor_xsd(tmp_path)
    validator = TissSchemaValidator(schema_root=tmp_path, version=_VERSION)
    ok = validator.validate(report_type=_REPORT_TYPE, dataset_ref=_write_xml(tmp_path, "ok.xml", _VALID_XML))
    bad = validator.validate(
        report_type=_REPORT_TYPE, dataset_ref=_write_xml(tmp_path, "bad.xml", _SCHEMA_INVALID_XML)
    )
    assert ok.schema_valid is True
    assert bad.schema_valid is False
