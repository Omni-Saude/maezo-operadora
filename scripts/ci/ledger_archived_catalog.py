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


# D7 is a separate finite successor, never a babe/v1 schema normalization.
D7_REVIEW_FILES = {
    "actual-REPORT.md": "cb967991a03fe053fce982c75e917ec7697a3fde1aba688bd4a54204f8783f93",
    "actual-MANIFEST.json": "dafe81a46cde069e04732ee527a1eb02b1fca9466a0d19db266a124c87c6f0cb",
    "source-REPORT.md": "4d2eec0223bc485c21331f0b31b6c797871f13ffd4fe3669d4f4c238a775d7f7",
    "source-MANIFEST.json": "bb4432f85d1b42ead5a239895102e66fa40805b3c06d11de81ca8b0035397e02",
    "HISTORICAL-DESCRIPTOR.json": "75d9c1c441c342361a010787789583e5f1dc5d7ae7f62a6617eb6de2a524b934",
    "CONSUMER-MANDATE.md": "87a9918199e7301ca716e81dd697b8874df9ab7c16fd99dcda3823607310d59d",
}
D7_INNER_PINS = {
    "manifest.json": "f253d4a951493089496576e48a529f2a35d8bdf932c002983f7c19a101b908d6",
    "receipt.json": "12c1d73c1a87c6dd4ba203f666111214f4546499781f1bf0f63b6e2ae64941a8",
}
D7_ARCHIVED_REPOSITORY = "/Users/familia/code/maezo-completion-wt/ledger-d7-async-successor-repair"
# Finite identity comparisons only, never paths to open or execute.
D7_PRODUCER_RUNTIMES = dict(PRODUCER_RUNTIMES)


def d7_archive_members(git: static.FrozenGit, relation: static.Relation) -> dict[str, bytes]:
    """Authenticate review closure and both complete manifests before field use.

    V2 retains its closed seven original-reference representation: actual report,
    outer manifest, and five packet references for the other review artifacts.
    Review manifests are separately pinned review attestations; their original
    diagnostic payloads are not relabeled as this relation's execution evidence.
    """
    from scripts.ci import ledger_history_proofs as operational

    reviewed = operational.d7_reviewed(relation)
    if reviewed is None:
        static.fail("D7_NOT_CATALOGUED")
    hist = relation.record.historical
    directory = hist.receipt.path.rsplit("/", 1)[0]
    review_dir = directory.rsplit("/", 1)[0] + "/review/"
    expected_refs = {
        directory + "/manifest.json": reviewed.manifest_sha256,
        **{review_dir + name: pin for name, pin in D7_REVIEW_FILES.items()},
    }
    if {ref.path: ref.sha256 for ref in hist.originals} != expected_refs:
        static.fail("D7_REVIEW_CLOSURE_REQUIRED")
    # Authenticate the distinct source review, actual review, frozen descriptor
    # and mandate before reading any untrusted catalogue or receipt fields.
    for path, pin in expected_refs.items():
        if static.digest(git.current(path, limit=static.MAX_CAPTURE_BYTES)) != pin:
            static.fail("D7_REVIEW_CUSTODY")
    if hist.receipt.path != directory + "/successor.json" or hist.receipt.sha256 != reviewed.receipt_sha256:
        static.fail("D7_OUTER_RECEIPT_IDENTITY")
    raw = git.current(directory + "/manifest.json", limit=static.MAX_JSON_BYTES)
    manifest = static.bounded_json(raw)
    if not 1 <= len(manifest) <= 256:
        static.fail("D7_OUTER_MEMBERS")
    result = {"manifest.json": raw}
    for name, expected in manifest.items():
        static.safe_path(name)
        if type(expected) is not str or name == "manifest.json":
            static.fail("D7_OUTER_MEMBERS")
        static.sha(expected)
        data = git.current(directory + "/" + name, limit=static.MAX_CAPTURE_BYTES)
        if static.digest(data) != expected:
            static.fail("D7_OUTER_MEMBER_HASH")
        result[name] = data
    if static.digest(result.get("successor.json", b"")) != reviewed.receipt_sha256:
        static.fail("D7_OUTER_RECEIPT_PIN")
    for name, expected in D7_INNER_PINS.items():
        if static.digest(result.get("capture/" + name, b"")) != expected:
            static.fail("D7_INNER_PIN")
    inner = static.bounded_json(result["capture/manifest.json"])
    if set(result) != {"manifest.json", "successor.json", "capture/manifest.json"} | {
        "capture/" + name for name in inner
    }:
        static.fail("D7_COMPLETE_NESTED_CUSTODY")
    for name, expected in inner.items():
        static.safe_path(name)
        if type(expected) is not str or static.digest(result["capture/" + name]) != expected:
            static.fail("D7_INNER_MEMBER_HASH")
    for stream in ("stdout", "stderr"):
        ref = getattr(hist, stream)
        if ref.path != directory + "/capture/pytest." + stream or ref.sha256 != static.digest(
            result["capture/pytest." + stream]
        ):
            static.fail("D7_RAW_STREAM_BINDING")
    return result


def _derive_d7_functions(
    module: ModuleType, names: set[str], replacements: dict[str, str], namespace: dict[str, Any]
) -> dict[str, Any]:
    """Exact source-pinned AST derivation; callers must verify the module first."""
    nodes = [
        n
        for n in ast.parse(Path(module.__file__ or "").read_bytes()).body
        if isinstance(n, ast.FunctionDef) and n.name in names
    ]

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
    derived = ast.Module(body=list[ast.stmt](nodes), type_ignores=[])
    transform.visit(derived)
    if len(nodes) != len(names) or set(transform.counts.values()) != {1}:
        static.fail("D7_ARCHIVE_DERIVATION_CLOSURE")
    exec(compile(ast.fix_missing_locations(derived), "<finite-d7-archived-consumer>", "exec"), namespace)
    return namespace


def d7_archive_validator(adapter: ModuleType) -> Any:
    """Portable archive-only adapter, isolated from every native execution module.

    Two lexical path checks and the recorded removed-checkout fact follow the
    accepted archived-producer pattern. The async plugin paths stay bound to the
    exact archived own-venv trace; the clone argv uses the frozen producer repo
    identity while all Git reads use the actual independently supplied repository.
    """
    from scripts.ci import ledger_history_proofs as operational

    original, recipe = adapter.original_producer, adapter.async_recipe
    if (
        static.digest(Path(original.__file__).read_bytes()) != PRODUCER_SHA256
        or static.digest(Path(recipe.__file__).read_bytes())
        != operational.D7_TOOL_SOURCES["scripts/dev/historical_async_recipe.py"]
    ):
        static.fail("D7_ARCHIVE_VALIDATOR_SOURCE")

    def runtime_digest(kind: str, asserted_path: str) -> str:
        path, pin = D7_PRODUCER_RUNTIMES[kind]
        if path != asserted_path:
            static.fail("D7_ARCHIVED_RUNTIME_IDENTITY")
        return pin

    helper_namespace = _derive_d7_functions(
        original,
        {"inventory"},
        {
            'Path(value["interpreter"]["prefix"]).resolve()': 'Path(value["interpreter"]["prefix"])',
            "Path(unquote(parsed.path)).resolve()": "Path(unquote(parsed.path))",
        },
        {**original.__dict__, "runtime_digest": runtime_digest},
    )
    helper = ModuleType("d7_archive_only_original_primitives")
    helper.__dict__.update(helper_namespace)
    namespace = _derive_d7_functions(
        recipe,
        {"validate_plugin", "validate_receipt"},
        {
            "location.resolve()": "location",
            "path.resolve()": "path",
            "checkout.exists()": "False",
            "str(repo)": "D7_ARCHIVED_REPOSITORY",
        },
        {**recipe.__dict__, "D7_ARCHIVED_REPOSITORY": D7_ARCHIVED_REPOSITORY},
    )

    def validate(
        packet: Path, repo: Path, identity: tuple[str, str, str, str, str], observations: dict[str, str]
    ) -> dict[str, Any]:
        result: dict[str, Any] = namespace["validate_receipt"](helper, packet, repo, identity, observations)
        return result

    return validate


def validate_d7_archived_receipt(
    adapter: ModuleType, out: Path, repo: Path, identity: tuple[str, str, str, str, str]
) -> dict[str, Any]:
    """Fully pinned archive readback. This returns observation-only scope unchanged.

    Operational caller has already authenticated committed review references via
    d7_archive_members; this seam rechecks both materialized custody boundaries.
    """
    from scripts.ci import ledger_history_proofs as operational

    reviewed = next((entry for entry in operational.D7_CATALOG if entry.identity() == identity), None)
    if reviewed is None:
        static.fail("D7_NOT_CATALOGUED")
    packet = adapter.Packet(out)
    inner = adapter.Packet(out / "capture")
    for name, pin in {
        "manifest.json": reviewed.manifest_sha256,
        "successor.json": reviewed.receipt_sha256,
    }.items():
        if static.digest(packet.read(name)) != pin:
            static.fail("D7_ARCHIVE_OUTER_PIN")
    if not adapter.same(adapter.load(out / "manifest.json"), packet.hashes(exclude="manifest.json")):
        static.fail("D7_ARCHIVE_OUTER_CUSTODY")
    for name, pin in D7_INNER_PINS.items():
        if static.digest(inner.read(name)) != pin:
            static.fail("D7_ARCHIVE_INNER_PIN")
    if not adapter.same(adapter.load(out / "capture/manifest.json"), inner.hashes(exclude="manifest.json")):
        static.fail("D7_ARCHIVE_INNER_CUSTODY")
    expected = {"successor.json", "manifest.json"} | {"capture/" + name for name in inner.files}
    if set(packet.files) != expected or set(packet.entries) != expected | {"capture", "capture/guard"}:
        static.fail("D7_ARCHIVE_MEMBER_SET")
    catalog = adapter.async_catalogue
    if (
        static.digest(Path(catalog.__file__).read_bytes())
        != operational.D7_TOOL_SOURCES["scripts/dev/run_historical_catalog_recipe.py"]
    ):
        static.fail("D7_ARCHIVE_ENTRYPOINT_PIN")
    entry = catalog.select(identity[0])
    if entry.identity() != identity:
        static.fail("D7_ARCHIVE_CATALOGUE_IDENTITY")
    catalog.preflight(adapter.original_producer, repo, entry)
    envelope = catalog.async_envelope(adapter.original_producer, entry, out)
    if not adapter.same(adapter.load(out / "successor.json"), envelope):
        static.fail("D7_ARCHIVE_ENVELOPE_BINDING")
    validate = d7_archive_validator(adapter)
    result: dict[str, Any] = validate(out / "capture", repo, identity, envelope["observations"])
    return result
