"""Unit tests for `maezo.tools.workers.tiss_schema_pin` — the T2.6-2 TISS-schema-pin ratification
gate (dark build; NOT wired into `ans_submit.py` this wave — see that module's docstring
"SCOPING DECISION").

Every XSD and every XML payload here is HAND-WRITTEN and SYNTHETIC — none of it is, or is derived
from, the real ANS Padrão-TISS schema. TWO DISTINCT fixture families are exercised, deliberately
using DIFFERENT namespaces (GK REVISE, MAJOR-2 — see the "Synthetic-self-declaration fixture
family" banner below for why):

  1. `_NS`/`_FIXTURE_XSD`/`_VALID_XML`/etc. (this section) — a namespace that does NOT self-
     declare synthetic, used by every test that needs a pin which genuinely LOADS as ratified.
     Built fresh in `tmp_path` by `_write_pin_manifest` (mission instruction: "prove with a
     second synthetic 'ratified' fixture in tmp_path") — same element vocabulary/structure as the
     repo's shipped synthetic artifact by construction (both hand-authored against the same
     design), but its OWN identity, precisely because a genuinely ratified pin's XSD must not
     self-declare synthetic (MAJOR-2's own check would refuse it if it did).
  2. `_SYNTHETIC_NS`/`_SYNTHETIC_VALID_XML`/etc. (further below) — the SAME namespace the REPO-
     SHIPPED `spec/policies/ans/synthetic-tiss-v1.xsd` declares. Used ONLY by (a) the shipped-
     artifact smoke tests, which validate directly against that real repo file bypassing the
     ratification gate entirely, and (b) MAJOR-2's forged-manifest refusal test, which needs a
     genuinely synthetic-self-declaring XSD to forge a ratification against.

Both families share the SAME element vocabulary/structure on purpose, so the structural
(valid/violation) test bodies exercise the identical schema SHAPE whichever family is behind the
gate — proving ratification only toggles AVAILABILITY, never the validation logic — while keeping
"is this the repo's synthetic artifact" a question the namespace alone can answer.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest
import yaml

from maezo.tools.workers.tiss_schema_pin import (
    REASON_ACCOUNTABILITY_INCOMPLETE,
    REASON_ARTIFACT_DIGEST_MISMATCH,
    REASON_ARTIFACT_MISSING,
    REASON_ARTIFACT_SELF_DECLARES_SYNTHETIC,
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
# "Ratified" fixture family — schema + payloads. Deliberately NOT namespaced like the repo's
# synthetic artifact (see module docstring, MAJOR-2). NONE of this is real TISS wire content.
# ---------------------------------------------------------------------------------------------

_NS = "urn:maezo:ratified-fixture:tiss-schema-pin:v1"

_FIXTURE_XSD = f"""<?xml version="1.0" encoding="UTF-8"?>
<!-- Test-local RATIFIED-shaped fixture — NOT the real ANS Padrão-TISS XSD, and deliberately NOT
     namespaced like the repo's synthetic artifact (see module docstring, MAJOR-2). -->
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


# ---------------------------------------------------------------------------------------------
# Synthetic-self-declaration fixture family — the SAME namespace
# spec/policies/ans/synthetic-tiss-v1.xsd itself declares. Used ONLY by the shipped-artifact
# smoke tests (validate directly against that real repo file) and MAJOR-2's forged-manifest
# refusal test (needs a genuinely synthetic-self-declaring XSD to forge a ratification against).
# ---------------------------------------------------------------------------------------------

_SYNTHETIC_NS = "urn:maezo:synthetic:tiss-schema-pin:v1"

_SYNTHETIC_VALID_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<tiss:LoteGuiasSintetico xmlns:tiss="{_SYNTHETIC_NS}">
  <tiss:numeroLoteSintetico>LOTE-0001</tiss:numeroLoteSintetico>
  <tiss:competenciaSintetica>2026-06</tiss:competenciaSintetica>
  <tiss:totalGuiasSintetico>2</tiss:totalGuiasSintetico>
  <tiss:guiaSintetica>GUIA-0001</tiss:guiaSintetica>
  <tiss:guiaSintetica>GUIA-0002</tiss:guiaSintetica>
</tiss:LoteGuiasSintetico>
"""

_SYNTHETIC_MISSING_ELEMENT_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<tiss:LoteGuiasSintetico xmlns:tiss="{_SYNTHETIC_NS}">
  <tiss:numeroLoteSintetico>LOTE-0001</tiss:numeroLoteSintetico>
  <tiss:totalGuiasSintetico>2</tiss:totalGuiasSintetico>
  <tiss:guiaSintetica>GUIA-0001</tiss:guiaSintetica>
  <tiss:guiaSintetica>GUIA-0002</tiss:guiaSintetica>
</tiss:LoteGuiasSintetico>
"""

_SYNTHETIC_WRONG_TYPE_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<tiss:LoteGuiasSintetico xmlns:tiss="{_SYNTHETIC_NS}">
  <tiss:numeroLoteSintetico>LOTE-0001</tiss:numeroLoteSintetico>
  <tiss:competenciaSintetica>2026-06</tiss:competenciaSintetica>
  <tiss:totalGuiasSintetico>{_PHI_CANARY}</tiss:totalGuiasSintetico>
  <tiss:guiaSintetica>GUIA-0001</tiss:guiaSintetica>
</tiss:LoteGuiasSintetico>
"""

_SYNTHETIC_CARDINALITY_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<tiss:LoteGuiasSintetico xmlns:tiss="{_SYNTHETIC_NS}">
  <tiss:numeroLoteSintetico>LOTE-0001</tiss:numeroLoteSintetico>
  <tiss:competenciaSintetica>2026-06</tiss:competenciaSintetica>
  <tiss:totalGuiasSintetico>3</tiss:totalGuiasSintetico>
  <tiss:guiaSintetica>GUIA-0001</tiss:guiaSintetica>
  <tiss:guiaSintetica>GUIA-0002</tiss:guiaSintetica>
  <tiss:guiaSintetica>GUIA-0003</tiss:guiaSintetica>
</tiss:LoteGuiasSintetico>
"""

#: `schema_artifact.report_type` the "ratified" fixture manifest carries by default — threaded
#: through `.validate(report_type=...)` calls below (MINOR-5) so they match the pin and exercise
#: ordinary structural validation rather than tripping the report_type-mismatch short-circuit.
_FIXTURE_REPORT_TYPE = "TEST_FIXTURE_STRUCTURE_ONLY"

#: Sentinel for `_write_pin_manifest`'s `sha256` parameter: "compute the REAL digest of
#: `xsd_content` automatically" (MAJOR-1 default — a fixture that wants to "just load" must carry
#: a genuinely correct digest). Distinct from `None`, which some tests pass explicitly to exercise
#: the blank/None-value refusal path.
_AUTO_SHA256 = object()


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
    sha256: Any = _AUTO_SHA256,
    padrao_tiss_versao: str = "TEST-FIXTURE-RATIFIED-v1",
    report_type: str = _FIXTURE_REPORT_TYPE,
    extra_top_level: dict[str, Any] | None = None,
    extra_schema_artifact: dict[str, Any] | None = None,
    manifest_name: str = "tiss-schema-pin.yaml",
) -> Path:
    """Write a pin manifest in `tmp_path` — defaults to a FULLY RATIFIED shape (the "second
    synthetic 'ratified' fixture" the mission asks for) so individual gate tests can override
    exactly ONE field to prove that gate alone blocks. NONE of the field VALUES here (version
    strings, report_type) resemble real TISS/ANS content — see module docstring.

    `sha256` defaults to the REAL digest of `xsd_content` (MAJOR-1, GK REVISE) — the loader now
    verifies this against the file's actual bytes on disk, so a fixture that wants to "just load"
    must carry a genuinely correct digest by default; tests proving the digest gate itself pass an
    explicit override (a bad string, or `None`).
    """
    sha256_value: Any = (
        hashlib.sha256(xsd_content.encode("utf-8")).hexdigest() if sha256 is _AUTO_SHA256 else sha256
    )
    data: dict[str, Any] = {
        "version": 1,
        "status": status,
        "ratificado": ratificado,
        "revisor": revisor,
        "ratificado_em": ratificado_em,
        "schema_artifact": {
            "xsd_filename": xsd_filename,
            "synthetic": synthetic,
            "sha256": sha256_value,
            "padrao_tiss_versao": padrao_tiss_versao,
            "report_type": report_type,
        },
        "observacao": "test fixture — not a real ratification",
    }
    if extra_top_level:
        data.update(extra_top_level)
    if extra_schema_artifact:
        data["schema_artifact"].update(extra_schema_artifact)

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
    gate opens when (and only when) all fields genuinely agree. `pin.sha256` assertion is
    MAJOR-1's "ratified+correct digest loads" case."""
    manifest_path = _write_pin_manifest(tmp_path)
    pin = load_tiss_schema_pin(path=manifest_path)
    assert isinstance(pin, TissSchemaPin)
    assert pin.xsd_path == tmp_path / "ratified-fixture-tiss.xsd"
    assert pin.xsd_path.is_file()
    assert pin.revisor == "Dra. Auditoria TISS Sintetica"
    assert pin.ratificado_em == "2026-08-09"
    assert pin.padrao_tiss_versao == "TEST-FIXTURE-RATIFIED-v1"
    assert pin.sha256 == hashlib.sha256(_FIXTURE_XSD.encode("utf-8")).hexdigest()


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
# MAJOR-1 (GK REVISE) — schema_artifact.sha256 is a DIGEST gate bound to the XSD's actual bytes,
# not a filename-only binding. GK proved a ratified pin previously survived an in-place XSD swap
# after ratification, a literal placeholder sha256, and an arbitrary non-matching "deadbeef".
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_sha256",
    ["", "   ", "PLACEHOLDER-SHA256-sme-must-fill-when-real-xsd-lands", "TODO-fill-me", None],
)
def test_ratified_manifest_with_blank_or_placeholder_sha256_refuses(
    tmp_path: Path, bad_sha256: str | None
) -> None:
    manifest_path = _write_pin_manifest(tmp_path, sha256=bad_sha256)
    with pytest.raises(TissSchemaPinUnavailableError) as exc:
        load_tiss_schema_pin(path=manifest_path)
    assert exc.value.reason == REASON_INVALID_SCHEMA


def test_ratified_manifest_with_deadbeef_sha256_refuses(tmp_path: Path) -> None:
    """The literal string `"deadbeef"` is a plausible-looking but WRONG hash — it carries no
    placeholder marker (so it must NOT be caught by the placeholder check) but also does not
    match the XSD's real digest, so it must be caught by the digest-MISMATCH check instead."""
    manifest_path = _write_pin_manifest(tmp_path, sha256="deadbeef")
    with pytest.raises(TissSchemaPinUnavailableError) as exc:
        load_tiss_schema_pin(path=manifest_path)
    assert exc.value.reason == REASON_ARTIFACT_DIGEST_MISMATCH


def test_ratified_manifest_with_xsd_swapped_in_place_after_ratify_refuses(tmp_path: Path) -> None:
    """MAJOR-1's exact live proof: a ratified pin binds to the digest of the XSD's BYTES, not
    merely its filename. Swapping the file's content in place AFTER the manifest was written
    (same filename, same claimed sha256, DIFFERENT bytes) must refuse — the pin cannot silently
    keep validating against content the ratification never actually reviewed."""
    manifest_path = _write_pin_manifest(tmp_path)  # correct digest of _FIXTURE_XSD at write time
    (tmp_path / "ratified-fixture-tiss.xsd").write_text(
        _FIXTURE_XSD.replace("LoteGuiasSintetico", "LoteGuiasSinteticoAlterado"),
        encoding="utf-8",
    )
    with pytest.raises(TissSchemaPinUnavailableError) as exc:
        load_tiss_schema_pin(path=manifest_path)
    assert exc.value.reason == REASON_ARTIFACT_DIGEST_MISMATCH


def test_draft_manifest_with_placeholder_sha256_still_refuses_via_status_not_digest(
    tmp_path: Path,
) -> None:
    """DRAFT path unchanged: a DRAFT manifest with a placeholder sha256 still refuses via
    REASON_STATUS_NOT_RATIFIED (gate 1, checked first) — the NEW digest/placeholder checks only
    apply once status/ratificado/accountability/synthetic already agree the pin IS ratified; they
    must not change the reason a DRAFT pin reports (placeholder sha256 is fine while DRAFT)."""
    manifest_path = _write_pin_manifest(
        tmp_path, status="DRAFT", sha256="PLACEHOLDER-SHA256-sme-must-fill-when-real-xsd-lands"
    )
    with pytest.raises(TissSchemaPinUnavailableError) as exc:
        load_tiss_schema_pin(path=manifest_path)
    assert exc.value.reason == REASON_STATUS_NOT_RATIFIED


def test_compiled_schema_cache_keyed_on_digest_not_stale_after_reratification(
    tmp_path: Path,
) -> None:
    """`_compile_schema`'s lru_cache must be keyed on the ratified DIGEST, not merely the path
    (task :423) — otherwise a legitimate re-ratification (new bytes + a correctly updated sha256,
    same filename) could still silently validate against the STALE pre-swap compiled schema
    object from an earlier call within the same process."""
    manifest_path = _write_pin_manifest(tmp_path)
    validator = TissSchemaPinValidator(manifest_path=manifest_path)
    dataset_ref = _write_xml(tmp_path, "valid.xml", _VALID_XML)
    first = validator.validate(report_type=_FIXTURE_REPORT_TYPE, dataset_ref=dataset_ref)
    assert first.schema_valid is True

    # Re-ratify in place: a DIFFERENT schema (guiaSintetica maxOccurs 2 -> 1) + a correctly
    # updated sha256 for the NEW bytes, same filename/path.
    new_xsd = _FIXTURE_XSD.replace(
        '<xs:element name="guiaSintetica" type="xs:string" minOccurs="1" maxOccurs="2"/>',
        '<xs:element name="guiaSintetica" type="xs:string" minOccurs="1" maxOccurs="1"/>',
    )
    assert new_xsd != _FIXTURE_XSD
    (tmp_path / "ratified-fixture-tiss.xsd").write_text(new_xsd, encoding="utf-8")
    new_digest = hashlib.sha256(new_xsd.encode("utf-8")).hexdigest()
    _write_pin_manifest(tmp_path, sha256=new_digest, write_xsd=False)

    second = validator.validate(report_type=_FIXTURE_REPORT_TYPE, dataset_ref=dataset_ref)
    # _VALID_XML has TWO guiaSintetica elements — under the NEW (maxOccurs=1) schema this is now
    # a cardinality violation. A STALE cached schema (keyed only on path) would still report
    # `True` here; the digest-keyed cache must recompile and correctly report `False`.
    assert second.schema_valid is False


# ---------------------------------------------------------------------------------------------
# MAJOR-2 (GK REVISE) — the XSD's OWN targetNamespace is checked against
# `_SYNTHETIC_NAMESPACE_PREFIX`; a manifest's `schema_artifact.synthetic: false` self-declaration
# is not, by itself, evidence. GK proved a forged manifest (all four gates forged) ratifying the
# verbatim SYNTHETIC XSD loaded.
# ---------------------------------------------------------------------------------------------


def test_forged_manifest_with_verbatim_synthetic_xsd_still_refuses(tmp_path: Path) -> None:
    """GK's exact forge: ALL FOUR human-intent gates forged correctly (status=RATIFIED,
    ratificado=true, revisor/ratificado_em filled, schema_artifact.synthetic=false) PLUS a
    CORRECT sha256 digest of the copied bytes (so MAJOR-1's digest gate is satisfied and cannot
    be what refuses this manifest — isolating this test to MAJOR-2's own check) — but the XSD
    itself is a VERBATIM copy of the real repo-shipped `synthetic-tiss-v1.xsd`, whose own
    targetNamespace self-identifies as `urn:maezo:synthetic:...`. The manifest's claim
    (`synthetic: false`) must not override what the artifact itself declares."""
    from maezo.agents import resolve_spec_dir

    shipped_xsd_path = resolve_spec_dir() / "policies" / "ans" / "synthetic-tiss-v1.xsd"
    shipped_bytes = shipped_xsd_path.read_bytes()
    (tmp_path / "forged.xsd").write_bytes(shipped_bytes)
    correct_digest = hashlib.sha256(shipped_bytes).hexdigest()

    manifest_path = _write_pin_manifest(
        tmp_path,
        xsd_filename="forged.xsd",
        write_xsd=False,
        sha256=correct_digest,
        synthetic=False,
    )
    with pytest.raises(TissSchemaPinUnavailableError) as exc:
        load_tiss_schema_pin(path=manifest_path)
    assert exc.value.reason == REASON_ARTIFACT_SELF_DECLARES_SYNTHETIC


def test_ratified_manifest_with_non_synthetic_namespace_xsd_loads(tmp_path: Path) -> None:
    """MAJOR-2's converse: a genuinely ratified pin whose XSD's targetNamespace does NOT self-
    declare synthetic loads normally — the check does not false-positive on an ordinary
    ratified-shaped fixture."""
    manifest_path = _write_pin_manifest(tmp_path)
    pin = load_tiss_schema_pin(path=manifest_path)
    assert isinstance(pin, TissSchemaPin)


# ---------------------------------------------------------------------------------------------
# MINOR-3 (GK REVISE) — xsd_filename must be a bare filename inside the manifest's own
# CODEOWNERS-gated directory. GK proved a `../../` probe LOADED a file outside that directory.
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_xsd_filename",
    ["sub/dir.xsd", "sub\\dir.xsd", "../escaped.xsd", "../../etc/passwd"],
)
def test_xsd_filename_with_traversal_or_separators_refuses(tmp_path: Path, bad_xsd_filename: str) -> None:
    manifest_path = _write_pin_manifest(tmp_path, xsd_filename=bad_xsd_filename, write_xsd=False)
    with pytest.raises(TissSchemaPinUnavailableError) as exc:
        load_tiss_schema_pin(path=manifest_path)
    assert exc.value.reason == REASON_INVALID_SCHEMA


def test_xsd_filename_dotdot_traversal_to_an_existing_file_still_refuses(tmp_path: Path) -> None:
    """The exact GK probe: `../`-traversal to a file that GENUINELY EXISTS (so the OLD code's
    naive `manifest_path.parent / xsd_filename` join would have resolved `.is_file()` -> True and
    LOADED it) must still refuse — proving the fix is the traversal REJECTION itself, not merely
    an incidental "missing file" outcome."""
    manifest_dir = tmp_path / "manifest_dir"
    manifest_dir.mkdir()
    manifest_path = _write_pin_manifest(manifest_dir, xsd_filename="../escaped.xsd")
    assert (tmp_path / "escaped.xsd").is_file()  # the traversal target genuinely exists
    with pytest.raises(TissSchemaPinUnavailableError) as exc:
        load_tiss_schema_pin(path=manifest_path)
    assert exc.value.reason == REASON_INVALID_SCHEMA


# ---------------------------------------------------------------------------------------------
# MINOR-4 (GK REVISE) — schema_artifact's own nested-key fence (the top-level fence, F4, does
# not recurse).
# ---------------------------------------------------------------------------------------------


def test_schema_artifact_unknown_nested_key_raises_invalid_schema(tmp_path: Path) -> None:
    """A typo'd nested key (`sintetico:`, the Portuguese near-miss for `synthetic:`) must refuse
    the WHOLE manifest rather than silently sit unread beside an otherwise fully-ratified-shaped
    `schema_artifact`."""
    manifest_path = _write_pin_manifest(tmp_path, extra_schema_artifact={"sintetico": False})
    with pytest.raises(TissSchemaPinUnavailableError) as exc:
        load_tiss_schema_pin(path=manifest_path)
    assert exc.value.reason == REASON_INVALID_SCHEMA
    assert "sintetico" in exc.value.message


# ---------------------------------------------------------------------------------------------
# Validator — refuses with a typed error while unavailable (never a False/PASS result).
# ---------------------------------------------------------------------------------------------


def test_validator_raises_using_shipped_draft_manifest_regardless_of_payload(tmp_path: Path) -> None:
    """DRAFT can never validate-as-PASS: even a well-formed, schema-shaped payload is refused via
    the typed error BEFORE the validator ever looks at it — using the real shipped manifest."""
    dataset_ref = _write_xml(tmp_path, "would-be-valid.xml", _VALID_XML)
    validator = TissSchemaPinValidator()  # default resolution -> shipped DRAFT manifest
    with pytest.raises(TissSchemaPinUnavailableError) as exc:
        validator.validate(report_type=_FIXTURE_REPORT_TYPE, dataset_ref=dataset_ref)
    assert exc.value.reason == REASON_STATUS_NOT_RATIFIED


def test_validator_raises_for_forged_ratificado_with_status_draft(tmp_path: Path) -> None:
    manifest_path = _write_pin_manifest(tmp_path, status="DRAFT", ratificado=True)
    dataset_ref = _write_xml(tmp_path, "payload.xml", _VALID_XML)
    validator = TissSchemaPinValidator(manifest_path=manifest_path)
    with pytest.raises(TissSchemaPinUnavailableError) as exc:
        validator.validate(report_type=_FIXTURE_REPORT_TYPE, dataset_ref=dataset_ref)
    assert exc.value.reason == REASON_STATUS_NOT_RATIFIED


# ---------------------------------------------------------------------------------------------
# Validator — structural correctness once ratified (item 2 + item 3's "structure keeps passing").
# ---------------------------------------------------------------------------------------------


def test_valid_payload_passes(tmp_path: Path) -> None:
    validator = _ratified_validator(tmp_path)
    dataset_ref = _write_xml(tmp_path, "valid.xml", _VALID_XML)
    result = validator.validate(report_type=_FIXTURE_REPORT_TYPE, dataset_ref=dataset_ref)
    assert result.schema_valid is True
    assert result.diagnostics == ()
    assert result.schema_version == "TEST-FIXTURE-RATIFIED-v1"


def test_missing_element_violation_fails_with_bounded_diagnostic(tmp_path: Path) -> None:
    validator = _ratified_validator(tmp_path)
    dataset_ref = _write_xml(tmp_path, "missing.xml", _MISSING_ELEMENT_XML)
    result = validator.validate(report_type=_FIXTURE_REPORT_TYPE, dataset_ref=dataset_ref)
    assert result.schema_valid is False
    assert len(result.diagnostics) == 1
    diag = result.diagnostics[0]
    assert diag.kind == "elemento_ausente"
    assert diag.element == "competenciaSintetica"


def test_wrong_type_violation_fails_with_bounded_diagnostic(tmp_path: Path) -> None:
    validator = _ratified_validator(tmp_path)
    dataset_ref = _write_xml(tmp_path, "wrongtype.xml", _WRONG_TYPE_XML)
    result = validator.validate(report_type=_FIXTURE_REPORT_TYPE, dataset_ref=dataset_ref)
    assert result.schema_valid is False
    assert len(result.diagnostics) == 1
    diag = result.diagnostics[0]
    assert diag.kind == "tipo_invalido"
    assert diag.element == "totalGuiasSintetico"


def test_cardinality_violation_fails_with_bounded_diagnostic(tmp_path: Path) -> None:
    validator = _ratified_validator(tmp_path)
    dataset_ref = _write_xml(tmp_path, "cardinality.xml", _CARDINALITY_XML)
    result = validator.validate(report_type=_FIXTURE_REPORT_TYPE, dataset_ref=dataset_ref)
    assert result.schema_valid is False
    assert len(result.diagnostics) == 1
    diag = result.diagnostics[0]
    assert diag.kind == "cardinalidade"
    assert diag.element == "guiaSintetica"


def test_namespace_violation_fails_with_bounded_diagnostic(tmp_path: Path) -> None:
    validator = _ratified_validator(tmp_path)
    dataset_ref = _write_xml(tmp_path, "namespace.xml", _NAMESPACE_XML)
    result = validator.validate(report_type=_FIXTURE_REPORT_TYPE, dataset_ref=dataset_ref)
    assert result.schema_valid is False
    assert len(result.diagnostics) == 1
    diag = result.diagnostics[0]
    assert diag.kind == "namespace_invalido"


def test_malformed_xml_payload_fails_with_bounded_diagnostic(tmp_path: Path) -> None:
    validator = _ratified_validator(tmp_path)
    dataset_ref = _write_xml(tmp_path, "malformed.xml", _MALFORMED_XML)
    result = validator.validate(report_type=_FIXTURE_REPORT_TYPE, dataset_ref=dataset_ref)
    assert result.schema_valid is False
    assert result.diagnostics[0].kind == "xml_malformado"


def test_blank_dataset_ref_fails_with_bounded_diagnostic(tmp_path: Path) -> None:
    validator = _ratified_validator(tmp_path)
    result = validator.validate(report_type=_FIXTURE_REPORT_TYPE, dataset_ref="")
    assert result.schema_valid is False
    assert result.diagnostics[0].kind == "dataset_ref_ausente"


def test_dataset_ref_not_a_real_file_fails_with_bounded_diagnostic(tmp_path: Path) -> None:
    validator = _ratified_validator(tmp_path)
    result = validator.validate(
        report_type=_FIXTURE_REPORT_TYPE, dataset_ref=str(tmp_path / "does-not-exist.xml")
    )
    assert result.schema_valid is False
    assert result.diagnostics[0].kind == "dataset_ref_nao_resolve_a_arquivo"


def test_validator_reusable_across_multiple_datasets(tmp_path: Path) -> None:
    """A single validator instance (as a future once-per-registration seam would be) validates
    multiple datasets correctly — the cached schema compile does not leak state across calls."""
    validator = _ratified_validator(tmp_path)
    ok = validator.validate(
        report_type=_FIXTURE_REPORT_TYPE, dataset_ref=_write_xml(tmp_path, "ok.xml", _VALID_XML)
    )
    bad = validator.validate(
        report_type=_FIXTURE_REPORT_TYPE,
        dataset_ref=_write_xml(tmp_path, "bad.xml", _MISSING_ELEMENT_XML),
    )
    assert ok.schema_valid is True
    assert bad.schema_valid is False


# ---------------------------------------------------------------------------------------------
# MINOR-5 (GK REVISE option 1) — report_type is threaded through validate()/gate_entry and
# checked against the ratified pin's own schema_artifact.report_type. GK found that
# TissSchemaPin already carried report_type but neither consumer read it — a direct adoption
# would have silently dropped the report-type discrimination the live seam
# (tiss_schema.py:187) has.
# ---------------------------------------------------------------------------------------------


def test_validate_with_matching_report_type_passes_through_to_normal_validation(
    tmp_path: Path,
) -> None:
    validator = _ratified_validator(tmp_path)
    dataset_ref = _write_xml(tmp_path, "valid.xml", _VALID_XML)
    result = validator.validate(report_type=_FIXTURE_REPORT_TYPE, dataset_ref=dataset_ref)
    assert result.schema_valid is True


def test_validate_with_mismatched_report_type_refuses_before_schema_validation(
    tmp_path: Path,
) -> None:
    """A report_type that does not match the ratified pin's own `schema_artifact.report_type`
    refuses with a distinct diagnostic kind — even for an otherwise VALID payload — proving the
    check runs BEFORE ordinary schema validation, not merely alongside it."""
    validator = _ratified_validator(tmp_path)
    dataset_ref = _write_xml(tmp_path, "valid.xml", _VALID_XML)
    result = validator.validate(report_type="SOME-OTHER-REPORT-TYPE", dataset_ref=dataset_ref)
    assert result.schema_valid is False
    assert len(result.diagnostics) == 1
    assert result.diagnostics[0].kind == "report_type_incompativel_com_pin"


def test_gate_entry_with_matching_report_type_available_and_passes(tmp_path: Path) -> None:
    manifest_path = _write_pin_manifest(tmp_path)
    validator = TissSchemaPinValidator(manifest_path=manifest_path)
    dataset_ref = _write_xml(tmp_path, "valid.xml", _VALID_XML)
    result = tiss_schema_pin_gate_entry(
        {"dataset_ref": dataset_ref, "report_type": _FIXTURE_REPORT_TYPE},
        pin_validator=validator,
    )
    assert result["tiss_schema_pin_available"] is True
    assert result["tiss_schema_pin_schema_valid"] is True


def test_gate_entry_with_mismatched_report_type_available_but_not_a_pass(tmp_path: Path) -> None:
    manifest_path = _write_pin_manifest(tmp_path)
    validator = TissSchemaPinValidator(manifest_path=manifest_path)
    dataset_ref = _write_xml(tmp_path, "valid.xml", _VALID_XML)
    result = tiss_schema_pin_gate_entry(
        {"dataset_ref": dataset_ref, "report_type": "SOME-OTHER-REPORT-TYPE"},
        pin_validator=validator,
    )
    assert result["tiss_schema_pin_available"] is True
    assert result["tiss_schema_pin_schema_valid"] is False
    assert result["tiss_schema_pin_diagnostics"] == ["report_type_incompativel_com_pin"]


# ---------------------------------------------------------------------------------------------
# PHI discipline — bounded diagnostics NEVER carry payload content.
# ---------------------------------------------------------------------------------------------


def test_wrong_type_diagnostic_never_leaks_the_offending_value(tmp_path: Path) -> None:
    """The canary value in `_WRONG_TYPE_XML` must not appear ANYWHERE in the returned
    diagnostics — not in `.kind`, not in `.element`, not in `.bounded_message()`."""
    validator = _ratified_validator(tmp_path)
    dataset_ref = _write_xml(tmp_path, "wrongtype.xml", _WRONG_TYPE_XML)
    result = validator.validate(report_type=_FIXTURE_REPORT_TYPE, dataset_ref=dataset_ref)
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
    result = validator.validate(report_type=_FIXTURE_REPORT_TYPE, dataset_ref=dataset_ref)
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
    result = tiss_schema_pin_gate_entry(
        {"dataset_ref": dataset_ref, "report_type": _FIXTURE_REPORT_TYPE},
        pin_validator=validator,
    )
    assert result["tiss_schema_pin_available"] is True
    assert result["tiss_schema_pin_schema_valid"] is True
    assert result["tiss_schema_pin_diagnostics"] == []
    assert result["tiss_schema_pin_version"] == "TEST-FIXTURE-RATIFIED-v1"


def test_gate_entry_available_but_schema_invalid_is_still_not_a_pass(tmp_path: Path) -> None:
    manifest_path = _write_pin_manifest(tmp_path)
    validator = TissSchemaPinValidator(manifest_path=manifest_path)
    dataset_ref = _write_xml(tmp_path, "missing.xml", _MISSING_ELEMENT_XML)
    result = tiss_schema_pin_gate_entry(
        {"dataset_ref": dataset_ref, "report_type": _FIXTURE_REPORT_TYPE},
        pin_validator=validator,
    )
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
    orthogonal to "is this a well-formed, structurally-meaningful XSD"). Uses the `_SYNTHETIC_*`
    payload family (same element vocabulary as the "ratified" fixture above by construction, but
    namespaced to match this REAL file's own `targetNamespace` exactly — see module docstring)."""
    from lxml import etree

    from maezo.agents import resolve_spec_dir

    xsd_path = resolve_spec_dir() / "policies" / "ans" / "synthetic-tiss-v1.xsd"
    assert xsd_path.is_file()
    schema = etree.XMLSchema(etree.parse(str(xsd_path)))

    assert schema.validate(etree.fromstring(_SYNTHETIC_VALID_XML.encode("utf-8"))) is True
    assert schema.validate(etree.fromstring(_SYNTHETIC_MISSING_ELEMENT_XML.encode("utf-8"))) is False
    assert schema.validate(etree.fromstring(_SYNTHETIC_WRONG_TYPE_XML.encode("utf-8"))) is False
    assert schema.validate(etree.fromstring(_SYNTHETIC_CARDINALITY_XML.encode("utf-8"))) is False
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
