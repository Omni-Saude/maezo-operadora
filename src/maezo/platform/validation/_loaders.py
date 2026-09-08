"""Tolerant YAML/XML loaders for the artifact validators.

Centralizes parsing (stdlib `xml.etree` + PyYAML) and converts parse failures
into a readable `ParseError` instead of letting the raw exception propagate —
so a single malformed file produces one finding for that file, never an
uncaught traceback that aborts the whole validation run.
"""

from __future__ import annotations

from collections.abc import Hashable
from pathlib import Path
from typing import TextIO
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


class _UniqueKeySafeLoader(yaml.SafeLoader):
    """Reject repeated explicit keys, preserving YAML merge inheritance and aliases."""

    def __init__(self, stream: TextIO) -> None:
        super().__init__(stream)
        self._checked_mappings: set[int] = set()

    def flatten_mapping(self, node: yaml.MappingNode) -> None:
        # Check BEFORE flattening: inherited keys and explicit overrides are legitimate
        # YAML merge semantics. The superclass recursively calls this method for merge
        # sources too. An aliased source can be visited again after it was flattened.
        if id(node) not in self._checked_mappings:
            self._checked_mappings.add(id(node))
            seen: set[Hashable] = set()
            merge_key = object()
            for key_node, _ in node.value:
                if key_node.tag == "tag:yaml.org,2002:merge":
                    key = merge_key
                elif key_node.tag == "tag:yaml.org,2002:value":
                    key = key_node.value
                else:
                    key = self.construct_object(key_node, deep=True)
                if not isinstance(key, Hashable):
                    raise yaml.constructor.ConstructorError(
                        None, None, "unhashable YAML key", key_node.start_mark
                    )
                if key in seen:
                    # Location only: do not echo an arbitrary key or source line.
                    raise yaml.YAMLError(
                        f"duplicate YAML key at line {key_node.start_mark.line + 1}, "
                        f"column {key_node.start_mark.column + 1}"
                    )
                seen.add(key)
        super().flatten_mapping(node)


def load_yaml(path: Path, *, reject_duplicate_keys: bool = False) -> object:
    """Parse YAML or raise `ParseError`; R-199 opts into unambiguous explicit keys.

    The default preserves the existing shared validators' parsing semantics. Strict
    loading is local to this call; it never mutates PyYAML's global SafeLoader.
    """
    try:
        with path.open(encoding="utf-8") as handle:
            if reject_duplicate_keys:
                return yaml.load(handle, Loader=_UniqueKeySafeLoader)
            return yaml.safe_load(handle)
    except yaml.YAMLError as exc:
        raise ParseError(f"malformed YAML: {exc}") from exc
    except OSError as exc:
        raise ParseError(f"could not read file: {exc}") from exc


def local_name(tag: str) -> str:
    """Strip the Clark-notation `{namespace}` prefix from an XML tag."""
    return tag.rsplit("}", 1)[-1]
