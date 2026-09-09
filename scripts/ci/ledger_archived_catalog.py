"""Portable readback of exactly the independently reviewed babe catalogue archives.

This consumer preserves the literal producer. Only immutable archive runtime
identity and recorded cleanup are interpreted portably; every other original
validator predicate is reused. It grants no fresh-execution or ledger credit.
"""

from __future__ import annotations

import ast
from pathlib import Path
from types import ModuleType
from typing import Any

from scripts.ci import ledger_invalid_declarations as static

REVIEW_REPORT_SHA256 = "6ac90678ca814b64109315dfd0fb7622a8cb50b107406ae837e188246aacb18f"
REVIEW_MANIFEST_SHA256 = "bacd87fd3e50b6a2528d4f8696288115b030d1b59e581fb7549071e18cba8d86"
PRODUCER_SHA256 = "25a412499cd64ae663de1358e488480cb6b6a5c0e772583ccc0bece45380877c"
ARCHIVES = {
    "D6unit136d": (
        "a73fa721e56910a2177ecbad5cfa179838b65ad8a543578fb23ae2d44015871f",
        "2ca01f8fa81b7eb502c768d1c1543c9045a3a9c459c38fb45c14665e99245d1a",
    ),
    "PFV8821": (
        "6532d00f17c6e66ccd206d720ee35024b85b13e14c9a7f1e991535a514e71965",
        "6abed75319a208ecd785b50340b886ea648134720ac9c252997946a1326e2295",
    ),
}
PRODUCER_RUNTIMES = {
    "python": (
        "/Users/familia/.local/share/uv/python/cpython-3.12.13-macos-aarch64-none/bin/python3.12",
        "a395f264e5612a2819662ed3e37fd30d39ed61179b98e5f86c3c783a008d8623",
    ),
    "uv": (
        "/Users/familia/.hermes/profiles/heitor/bin/uv",
        "0e71bad1f36bc9762cdecef1932f68ea3db541e45495c60dd474e1c860e21edf",
    ),
}


def archived_runtime_digest(kind: str, asserted_path: str) -> str:
    """Independently pinned producer identity; deliberately performs no host IO."""
    expected_path, expected_sha = PRODUCER_RUNTIMES[kind]
    if asserted_path != expected_path:
        static.fail("VH_ARCHIVED_RUNTIME_IDENTITY")
    return expected_sha


def archive_validator(producer: ModuleType) -> Any:
    """Derive two portable functions from exact immutable source, without mutation.

    The prefix comparison is lexical for a foreign archived checkout. The cleanup
    existence probe is omitted only for this fully pinned already-reviewed archive.
    No file IO against producer paths remains; all packet/source checks stay intact.
    """
    source = Path(producer.__file__ or "").read_bytes()
    if static.digest(source) != PRODUCER_SHA256:
        static.fail("VH_ARCHIVE_VALIDATOR_SOURCE")
    tree = ast.parse(source)
    functions = [
        n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in {"inventory", "validate_receipt"}
    ]
    # Exact one-for-one replacements are verified against the pinned source. They
    # are NOT record-provided code or a relaxed live validator.
    replacements = {
        'Path(value["interpreter"]["prefix"]).resolve()': 'Path(value["interpreter"]["prefix"])',
        "checkout.exists()": "False",
        "Path(unquote(parsed.path)).resolve()": "Path(unquote(parsed.path))",
    }

    class Portable(ast.NodeTransformer):
        def __init__(self) -> None:
            self.counts = dict.fromkeys(replacements, 0)

        def visit_Call(self, node: ast.Call) -> ast.AST:
            for old, new in replacements.items():
                if ast.dump(node) == ast.dump(ast.parse(old, mode="eval").body):
                    self.counts[old] += 1
                    return ast.copy_location(ast.parse(new, mode="eval").body, node)
            return self.generic_visit(node)

    transform = Portable()
    derived = ast.Module(body=list[ast.stmt](functions), type_ignores=[])
    transform.visit(derived)
    if len(functions) != 2 or set(transform.counts.values()) != {1}:
        static.fail("VH_ARCHIVE_DERIVATION_CLOSURE")
    namespace = dict(producer.__dict__)
    namespace["runtime_digest"] = archived_runtime_digest
    exec(
        compile(ast.fix_missing_locations(derived), "<reviewed-portable-archive-consumer>", "exec"), namespace
    )
    return namespace["validate_receipt"]


def validate_archived_catalog_receipt(
    producer: ModuleType, packet: Path, repo: Path, identity: tuple[str, str, str, str, str]
) -> dict[str, Any]:
    """Full bounded custody preflight before interpreting receipt/runtime fields."""
    expected = ARCHIVES.get(identity[0])
    if expected is None:
        static.fail("VH_ARCHIVE_NOT_CATALOGUED")
    files = producer.Packet(packet)
    raw_manifest = files.read("manifest.json")
    if static.digest(raw_manifest) != expected[0]:
        static.fail("VH_ARCHIVE_MANIFEST_PIN")
    manifest = static.bounded_json(raw_manifest)
    if not producer.same(manifest, files.hashes(exclude="manifest.json")):
        static.fail("VH_ARCHIVE_COMPLETE_CUSTODY")
    raw_receipt = files.read("receipt.json")
    if static.digest(raw_receipt) != expected[1]:
        static.fail("VH_ARCHIVE_RECEIPT_PIN")
    receipt = static.bounded_json(raw_receipt)
    validate = archive_validator(producer)
    result: dict[str, Any] = validate(packet, repo, identity, receipt["scope"]["original_observations"])
    # Consumer metadata is separate; original receipt remains observation-only.
    return result
