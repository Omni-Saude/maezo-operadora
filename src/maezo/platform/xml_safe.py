"""XML parsing that refuses DOCTYPE/ENTITY declarations (forensic audit F-152/F-153)."""

from __future__ import annotations

from xml.etree import ElementTree as ET


class DtdRefusedError(ValueError):
    """XML carrying a DOCTYPE/ENTITY declaration was refused before parsing."""


def parse_xml_without_dtd(raw: bytes) -> ET.Element:
    """Parse XML bytes, refusing any DOCTYPE/ENTITY declaration first.

    BPMN/DMN never need a DTD. Refusing it up front closes entity-expansion and external-entity
    classes regardless of the expat version (forensic audit F-152/F-153), with the same check
    `gateway/human/decision_binding.py` already applies to deployed BPMN.
    """
    # BPMN/DMN are UTF-8/ASCII. A UTF-16/32 document would hide `<!DOCTYPE` from the byte check
    # below (each char becomes 2+ bytes), so refuse it outright: NUL is illegal in XML 1.0 text.
    if b"\x00" in raw or raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        raise DtdRefusedError("XML must be UTF-8 (UTF-16/32 or NUL bytes refused)")
    upper = raw.upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        raise DtdRefusedError("XML with DOCTYPE/ENTITY declaration refused")
    return ET.fromstring(raw)


__all__ = ["DtdRefusedError", "parse_xml_without_dtd"]
