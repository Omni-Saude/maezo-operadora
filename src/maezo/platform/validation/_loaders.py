"""Tolerant YAML/XML loaders for the artifact validators.

Centralizes parsing (stdlib `xml.etree` + PyYAML) and converts parse failures
into a readable `ParseError` instead of letting the raw exception propagate —
so a single malformed file produces one finding for that file, never an
uncaught traceback that aborts the whole validation run.
"""

from __future__ import annotations

from pathlib import Path
from xml.etree import ElementTree as ET

import yaml


class ParseError(Exception):
    """A file could not be parsed (malformed XML/YAML or unreadable)."""


def load_xml(path: Path) -> ET.Element:
    """Parse an XML file and return its root element, or raise `ParseError`."""
    try:
        tree = ET.parse(path)
    except ET.ParseError as exc:
        raise ParseError(f"malformed XML: {exc}") from exc
    except OSError as exc:
        raise ParseError(f"could not read file: {exc}") from exc
    return tree.getroot()


def load_yaml(path: Path) -> object:
    """Parse a YAML file and return the resulting object, or raise `ParseError`."""
    try:
        with path.open(encoding="utf-8") as handle:
            return yaml.safe_load(handle)
    except yaml.YAMLError as exc:
        raise ParseError(f"malformed YAML: {exc}") from exc
    except OSError as exc:
        raise ParseError(f"could not read file: {exc}") from exc


def local_name(tag: str) -> str:
    """Strip the Clark-notation `{namespace}` prefix from an XML tag."""
    return tag.rsplit("}", 1)[-1]
