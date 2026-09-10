"""Tolerant YAML/XML loaders for the artifact validators.

Centralizes parsing (stdlib `xml.etree` + PyYAML) and converts parse failures
into a readable `ParseError` instead of letting the raw exception propagate —
so a single malformed file produces one finding for that file, never an
uncaught traceback that aborts the whole validation run.
"""

from __future__ import annotations

from pathlib import Path
from typing import IO
from xml.etree import ElementTree as ET

import yaml
from yaml.nodes import MappingNode


class ParseError(Exception):
    """A file could not be parsed (malformed XML/YAML or unreadable)."""


class _UniqueMappingLoader(yaml.SafeLoader):
    """Reject repeated source keys while retaining standard YAML merge precedence.

    Validate before SafeLoader flattens merges: an explicit override of a merged default
    is valid YAML, whereas two explicit occurrences of a signature field are ambiguous.
    Anchored mappings are checked once, before flattening mutates their shared nodes.
    The strict mode is opt-in; unrelated artifact consumers retain their old semantics.
    """

    def __init__(self, stream: str | bytes | IO[str] | IO[bytes]) -> None:
        super().__init__(stream)
        self._checked_mappings: set[MappingNode] = set()

    def flatten_mapping(self, node: MappingNode) -> None:
        if node not in self._checked_mappings:
            self._checked_mappings.add(node)
            seen: set[object] = set()
            merge_key = object()
            for key_node, _ in node.value:
                if key_node.tag == "tag:yaml.org,2002:merge":
                    key = merge_key
                elif key_node.tag == "tag:yaml.org,2002:value":
                    key = self.construct_scalar(key_node)
                else:
                    key = self.construct_object(key_node, deep=True)
                try:
                    duplicate = key in seen
                    seen.add(key)
                except TypeError as exc:
                    raise yaml.YAMLError("unhashable YAML mapping key") from exc
                if duplicate:
                    # MarkedYAMLError includes the source line, which can contain PHI.
                    # Report only its position; do not echo the key, values or source buffer.
                    raise yaml.YAMLError(f"duplicate YAML mapping key at line {key_node.start_mark.line + 1}")
        super().flatten_mapping(node)


def load_xml(path: Path) -> ET.Element:
    """Parse an XML file and return its root element, or raise `ParseError`."""
    try:
        tree = ET.parse(path)
    except ET.ParseError as exc:
        raise ParseError(f"malformed XML: {exc}") from exc
    except OSError as exc:
        raise ParseError(f"could not read file: {exc}") from exc
    return tree.getroot()


def load_yaml(path: Path, *, reject_duplicate_keys: bool = False) -> object:
    """Parse YAML, optionally refusing repeated mapping keys, or raise `ParseError`."""
    try:
        with path.open(encoding="utf-8") as handle:
            if reject_duplicate_keys:
                return yaml.load(handle, Loader=_UniqueMappingLoader)
            return yaml.safe_load(handle)
    except yaml.YAMLError as exc:
        raise ParseError(f"malformed YAML: {exc}") from exc
    except OSError as exc:
        raise ParseError(f"could not read file: {exc}") from exc


def local_name(tag: str) -> str:
    """Strip the Clark-notation `{namespace}` prefix from an XML tag."""
    return tag.rsplit("}", 1)[-1]
