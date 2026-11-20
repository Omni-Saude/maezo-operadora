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

from maezo.platform.xml_safe import DtdRefusedError, parse_xml_without_dtd


class ParseError(Exception):
    """A file could not be parsed (malformed XML/YAML or unreadable)."""


class DuplicateYAMLKeyError(yaml.YAMLError):
    """A mapping repeats a key — YAML's default would silently keep the LAST occurrence."""


class _UniqueMappingLoader(yaml.SafeLoader):
    """Reject repeated source keys while retaining standard YAML merge precedence.

    Validate before SafeLoader flattens merges: an explicit override of a merged default
    is valid YAML, whereas two explicit occurrences of a signature field are ambiguous.
    Anchored mappings are checked once, before flattening mutates their shared nodes.
    The strict mode is opt-in; unrelated artifact consumers retain their old semantics.
    """

    #: Whether the refusal names the repeated key. Off by default: a PHI-adjacent file may carry
    #: sensitive text in a key. On only for files whose keys are governance names (see below).
    name_duplicate_key: bool = False

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
                    # Report only its position; do not echo the values or source buffer.
                    line = key_node.start_mark.line + 1
                    if self.name_duplicate_key:
                        shown = str(key)[:80] if isinstance(key, str | int | float | bool) else "<key>"
                        raise DuplicateYAMLKeyError(f"duplicate YAML mapping key {shown!r} at line {line}")
                    raise DuplicateYAMLKeyError(f"duplicate YAML mapping key at line {line}")
        super().flatten_mapping(node)


class PolicyUniqueKeyLoader(_UniqueMappingLoader):
    """The strict loader for `spec/policies/autonomy/*.yaml` (DL-0193): every reader of that folder.

    A key appended at the end of `L0-core.yaml` or a tenant overlay with an EXISTING action name
    would otherwise silently override the reviewed entry (last-wins) — an "addition" that loosens a
    rule. Duplicate keys are refused at ANY depth. Policy keys are action/field names, never PHI,
    so the refusal names the key.
    """

    name_duplicate_key = True


def load_xml(path: Path) -> ET.Element:
    """Parse an XML file and return its root element, or raise `ParseError`."""
    try:
        return parse_xml_without_dtd(path.read_bytes())
    except DtdRefusedError as exc:
        raise ParseError(str(exc)) from exc
    except ET.ParseError as exc:
        raise ParseError(f"malformed XML: {exc}") from exc
    except OSError as exc:
        raise ParseError(f"could not read file: {exc}") from exc


def load_yaml(path: Path, *, reject_duplicate_keys: bool = False, name_duplicate_key: bool = False) -> object:
    """Parse YAML, optionally refusing repeated mapping keys, or raise `ParseError`.

    `name_duplicate_key` (implies `reject_duplicate_keys`) uses :class:`PolicyUniqueKeyLoader`,
    whose refusal names the key — only for files whose keys are known not to carry PHI.
    """
    try:
        with path.open(encoding="utf-8") as handle:
            if name_duplicate_key:
                return yaml.load(handle, Loader=PolicyUniqueKeyLoader)
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
