"""Gera o binding TS mecânico do contrato comercial C6-4 (EP-CORE-09c, EXTRACT #2, caminho B).

Spec-fonte única: `tests/candidate_artifacts/commercial/contract-c6-4.json` (bytes custodiados,
nunca editados aqui). O pin é o que o repo já usa: `sha256` de
`config/integrations/amh/commercial-c6-4.source.lock.json` — o mesmo valor do codec Python
`maezo.adapters.amh.commercial.CANDIDATE_SCHEMA_SHA256`. Bytes com sha diferente são recusados
com `ContractPinRefusal.SHA_MISMATCH` antes de qualquer parse; nada é emitido.

A saída descreve FORMA (tipos de `$defs`, tabela `x-commands`, `x-status`). Não é autoridade de
publicação, admissão, identidade nem decisão; condicionais (`if/then/else`), limites e padrões
ficam no codec do servidor. Somente stdlib; saída determinística. Nesta onda nada no portal a
importa (o arquivo mora fora de `web/src/`).

Uso: `python generate_c64_binding.py` (escreve) ou `--check` (falha se a saída commitada diverge).
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from enum import StrEnum
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[5]
CONTRACT_PATH = REPO / "tests/candidate_artifacts/commercial/contract-c6-4.json"
LOCK_PATH = REPO / "config/integrations/amh/commercial-c6-4.source.lock.json"
OUTPUT_PATH = HERE / "commercial-c6-4.generated.ts"
_REF_PREFIX = "#/$defs/"
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_SHA = re.compile(r"[0-9a-f]{64}")
_PRIMITIVES = {"string": "string", "integer": "number", "number": "number", "boolean": "boolean"}


class ContractPinRefusal(StrEnum):
    """Recusas tipadas do gerador; nunca carregam bytes do contrato."""

    SHA_MISMATCH = "SHA_MISMATCH"
    LOCK_INVALID = "LOCK_INVALID"
    CONTRACT_SHAPE_UNSUPPORTED = "CONTRACT_SHAPE_UNSUPPORTED"


class ContractPinRefusalError(ValueError):
    def __init__(self, reason: ContractPinRefusal) -> None:
        self.reason = reason
        super().__init__(reason.value)


def _rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPO).as_posix()
    except ValueError:
        return path.name


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ContractPinRefusalError(ContractPinRefusal.CONTRACT_SHAPE_UNSUPPORTED)
        result[key] = value
    return result


def load_pin(lock_path: Path = LOCK_PATH) -> str:
    """Lê o pin já usado pelo repo (lock da fonte C6-4)."""
    try:
        lock = json.loads(lock_path.read_bytes(), object_pairs_hook=_no_duplicates)
        pin = lock["sha256"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ContractPinRefusalError(ContractPinRefusal.LOCK_INVALID) from exc
    if type(pin) is not str or not _SHA.fullmatch(pin):
        raise ContractPinRefusalError(ContractPinRefusal.LOCK_INVALID)
    return pin


def load_contract(
    contract_path: Path = CONTRACT_PATH, lock_path: Path = LOCK_PATH
) -> tuple[str, dict[str, Any]]:
    """Lê os bytes reais, confere o sha contra o pin e só então faz o parse."""
    pin = load_pin(lock_path)
    raw = contract_path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != pin:
        raise ContractPinRefusalError(ContractPinRefusal.SHA_MISMATCH)
    document = json.loads(raw.decode("utf-8"), object_pairs_hook=_no_duplicates)
    if type(document) is not dict:
        raise ContractPinRefusalError(ContractPinRefusal.CONTRACT_SHAPE_UNSUPPORTED)
    return digest, document


def _literal(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _ref(ref: Any, defs: dict[str, Any]) -> str:
    if type(ref) is not str or not ref.startswith(_REF_PREFIX):
        raise ContractPinRefusalError(ContractPinRefusal.CONTRACT_SHAPE_UNSUPPORTED)
    name = ref[len(_REF_PREFIX) :]
    if name not in defs or not _IDENT.fullmatch(name):
        raise ContractPinRefusalError(ContractPinRefusal.CONTRACT_SHAPE_UNSUPPORTED)
    return name


def _composite(text: str) -> bool:
    """True quando há `|`/`&` no nível de topo (fora de chaves, parênteses e strings)."""
    depth, in_string, escaped = 0, False, False
    for index, char in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in "{([":
            depth += 1
        elif char in "})]":
            depth -= 1
        elif depth == 0 and text[index : index + 3] in (" | ", " & "):
            return True
    return False


def _wrap(text: str) -> str:
    return f"({text})" if _composite(text) else text


def _union(parts: list[str]) -> str:
    unique = list(dict.fromkeys(parts))
    return unique[0] if len(unique) == 1 else " | ".join(_wrap(p) for p in unique)


def _object(node: dict[str, Any], defs: dict[str, Any], indent: int) -> str:
    properties = node.get("properties", {})
    required = set(node.get("required", []))
    closed = node.get("additionalProperties") is False
    if not properties:
        return "{ readonly [key: string]: never }" if closed else "{ readonly [key: string]: unknown }"
    pad = "  " * (indent + 1)
    lines = ["{"]
    for name, child in properties.items():
        optional = "" if name in required else "?"
        lines.append(f"{pad}readonly {_literal(name)}{optional}: {_type(child, defs, indent + 1)};")
    if not closed:
        lines.append(f"{pad}readonly [key: string]: unknown;")
    lines.append("  " * indent + "}")
    return "\n".join(lines)


def _type(node: Any, defs: dict[str, Any], indent: int = 0) -> str:
    if node is True:
        return "unknown"
    if type(node) is not dict:
        raise ContractPinRefusalError(ContractPinRefusal.CONTRACT_SHAPE_UNSUPPORTED)
    if "$ref" in node:
        return _ref(node["$ref"], defs)
    parts: list[str] = []
    if "const" in node:
        parts.append(_literal(node["const"]))
    elif "enum" in node:
        parts.append(_union([_literal(v) for v in node["enum"]]))
    elif "type" in node:
        kind = node["type"]
        if kind == "object":
            parts.append(_object(node, defs, indent))
        elif kind == "array":
            items = node.get("items")
            if node.get("maxItems") == 0:
                parts.append("readonly never[]")
            elif items:
                parts.append(f"readonly {_wrap(_type(items, defs, indent))}[]")
            else:
                parts.append("readonly unknown[]")
        elif kind == "null":
            parts.append("null")
        elif kind in _PRIMITIVES:
            parts.append(_PRIMITIVES[kind])
        else:
            raise ContractPinRefusalError(ContractPinRefusal.CONTRACT_SHAPE_UNSUPPORTED)
    for keyword in ("anyOf", "oneOf"):
        if keyword in node:
            parts.append(_wrap(_union([_type(child, defs, indent) for child in node[keyword]])))
    for child in node.get("allOf", []):
        parts.append(_wrap(_type(child, defs, indent)))
    parts = [part for part in parts if part != "unknown"]
    if not parts:
        return "unknown"
    return parts[0] if len(parts) == 1 else " & ".join(_wrap(p) for p in parts)


def render(contract_path: Path = CONTRACT_PATH, lock_path: Path = LOCK_PATH) -> str:
    """Texto TS determinístico; recusa tipada se o contrato não bater com o pin."""
    digest, document = load_contract(contract_path, lock_path)
    defs: dict[str, Any] = document["$defs"]
    commands: dict[str, Any] = document["x-commands"]
    header = [
        "// GERADO; NÃO EDITAR. Regenere com:",
        "//   python src/maezo/portal/web/codegen/c6-4/generate_c64_binding.py",
        f"// Contrato: {_rel(contract_path)}",
        f"// SHA-256 do contrato: {digest}",
        f"// Pin conferido: {_rel(lock_path)} (campo sha256)",
        "// Somente forma (tipos de $defs, x-commands, x-status). Não é autoridade de publicação,",
        "// admissão, identidade nem decisão; if/then/else, limites e padrões são do codec do servidor.",
        "",
        f"export const C64_CONTRACT_SHA256 = {_literal(digest)} as const;",
        f"export const C64_SCHEMA_ID = {_literal(document['$id'])} as const;",
        "",
        "export const C64_STATUS = {",
    ]
    header += [f"  {_literal(k)}: {_literal(v)}," for k, v in document["x-status"].items()]
    header += ["} as const;", "", "export const C64_COMMANDS = {"]
    for operation, spec in commands.items():
        header.append(
            f"  {_literal(operation)}: {{ payloadSchema: {_literal(_ref(spec['payload_schema'], defs))}, "
            f"resultKind: {_literal(spec['result_kind'])}, scope: {_literal(spec['scope'])}, "
            f"mode: {_literal(spec['mode'])} }},"
        )
    header += ["} as const;", "", "export type C64Operation = keyof typeof C64_COMMANDS;", ""]
    body = [f"export type {name} = {_type(defs[name], defs)};" for name in sorted(defs)]
    roots = _union([_type(branch, defs) for branch in document["oneOf"]])
    footer = ["", f"export type C64Document = {roots};", ""]
    return "\n".join(header + body + footer)


def main(argv: list[str]) -> int:
    if argv not in ([], ["--check"]):
        print("uso: generate_c64_binding.py [--check]", file=sys.stderr)
        return 2
    try:
        text = render()
    except ContractPinRefusalError as refusal:
        print(f"recusado: ContractPinRefusal.{refusal.reason.value}", file=sys.stderr)
        return 1
    if argv == ["--check"]:
        current = OUTPUT_PATH.read_text(encoding="utf-8") if OUTPUT_PATH.exists() else None
        if current != text:
            print(f"{_rel(OUTPUT_PATH)} diverge da regeneração a partir do contrato pinado", file=sys.stderr)
            return 1
        return 0
    OUTPUT_PATH.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
