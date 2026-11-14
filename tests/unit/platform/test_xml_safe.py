"""XML with DOCTYPE/ENTITY is refused before parsing (forensic audit F-152/F-153)."""

from __future__ import annotations

from pathlib import Path

import pytest

from maezo.platform.validation._loaders import ParseError, load_xml
from maezo.platform.xml_safe import DtdRefusedError, parse_xml_without_dtd

_BOMB = b'<?xml version="1.0"?><!DOCTYPE l [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;">]><r>&b;</r>'
_XXE = b'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY x SYSTEM "file:///etc/passwd">]><r>&x;</r>'


@pytest.mark.parametrize(
    "raw",
    [
        _BOMB,
        _XXE,
        b"<!doctype r><r/>",
        _BOMB.replace(b'1.0"', b'1.0" encoding="UTF-16"').decode().encode("utf-16"),
        _BOMB.decode().encode("utf-16-le"),
    ],
)
def test_dtd_and_entities_are_refused(raw: bytes) -> None:
    with pytest.raises(DtdRefusedError):
        parse_xml_without_dtd(raw)


def test_plain_xml_still_parses() -> None:
    assert parse_xml_without_dtd(b'<?xml version="1.0"?><a><b/></a>').tag == "a"


def test_load_xml_reports_dtd_as_parse_error(tmp_path: Path) -> None:
    bad = tmp_path / "x.bpmn"
    bad.write_bytes(_BOMB)
    with pytest.raises(ParseError, match="DOCTYPE"):
        load_xml(bad)
