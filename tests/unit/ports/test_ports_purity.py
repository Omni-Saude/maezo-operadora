"""Architecture test: `maezo.ports` PURITY — the property that makes the ports an isolation
boundary instead of a re-export of the infrastructure they hide (ADR-0037, MZO-030).

**The invariant.** `maezo.ports` is LEAF DOMAIN: standard library + `typing` only, plus imports of
`maezo.ports` itself. It may not import a broker client, a schema registry codec, a cloud SDK, an
HTTP/SQL client, a clinical-record library — nor ANY other `maezo` package.

**Why it is load-bearing.** ADR-0037's immutable prohibition #2 forbids Tasy/Debezium/AWS/Glue/
HAPI/contract-owner types in the payer core, and the whole XRD-06/XRD-07 argument is that the
payer core stays portable off the hosted cell's infrastructure. A port that imported `aiokafka`
would make "ERP-neutral" a comment rather than a property: every consumer of the port would
transitively acquire the dependency, the core would no longer build without a broker library
installed, and the boundary review that ADR-0037 buys would have nothing to review. A port that
imported `maezo.agents`/`maezo.tools`/`maezo.runtime` would invert the dependency direction the
ports exist to establish (the core depends on the ports; the ports depend on nothing).

This test enforces the invariant statically, as a blunt import fence over the AST — the same
technique, and deliberately the same shape, as
`tests/unit/tools/workers/test_worker_handler_purity.py` (the P1 handler-purity fence), in FOUR
layers:

  (i)   NAMED BLOCKLIST (strict): the concrete infrastructure roots the boundary exists to keep
        out. Explicit so the fence documents its own intent and a reviewer can see the list.
  (ii)  STDLIB ALLOWLIST (strict, and strictly stronger than (i)): every imported root must be in
        `sys.stdlib_module_names` or be `maezo`. This catches an infrastructure dependency that
        is not on the blocklist yet — the failure mode a blocklist alone always has.
  (iii) INTRA-REPO FENCE (strict): any `maezo.*` import must be `maezo.ports.*`. Ports may compose
        with each other and with nothing else in this repository.
  (iv)  PHI-SHAPE PROBE (strict): no value type or port method in the package may carry a field or
        parameter whose NAME is patient-identifying (ADR-0037 immutable prohibition #5 — no PHI in
        keys, logs, traces, metrics or quarantine metadata; and no raw patient/beneficiary/MPI id
        in the payer domain, XRD-05). The port value types are OPAQUE — they reject nothing by
        parsing — so the name-shape probe is the structural guarantee that no PHI-shaped slot was
        ever created for a value to be put into.

Every layer carries a NON-VACUITY assertion (an exact module count, an exact value-type count), AND
the extraction the layers rest on is itself proved non-vacuous: `test_import_extraction_is_not_
vacuous` asserts the extractors return specific known imports of a real port module, and
`test_synthetic_violations_are_detected_by_the_real_predicates` runs known-bad sources through the
SAME predicate functions the real scan uses. Counting modules only proves the fences were handed a
non-empty set of files; it does not prove anything was read out of them. Both are needed — an
extractor stubbed to return an empty set left all of these tests green before those two existed.

**Known limit, stated rather than implied: this is an AST fence, so it sees only STATIC imports.**
`importlib.import_module("aiokafka")`, `__import__(name)` and any other runtime resolution are
invisible to every layer below, because the module name may not exist until the call runs. A port
that reached for infrastructure dynamically would pass this file. The mitigation is not in this
test: it is that the port modules are pure declarations — Protocols, dataclasses and one StrEnum,
with no executable body to hide such a call in — and that a dynamic import appearing in a port
module would be a conspicuous review event on a diff to this package.
"""

from __future__ import annotations

import ast
import dataclasses
import importlib
import inspect
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

import maezo.ports as ports_pkg

# The EXACT module set of the package. An added module must be added here deliberately — which is
# the review checkpoint, since a new module is a new piece of the boundary's public shape.
_EXPECTED_MODULES: frozenset[str] = frozenset(
    {
        "__init__",  # public surface (re-exports only)
        "envelope",  # the 28 frozen envelope fields, in the pinned order
        "errors",  # the ONE closed refusal taxonomy + the pinned timeout default
        "work_items",  # WorkItemSource
        "consent",  # ConsentDecisionSource
        "clinical_context",  # ClinicalContextPort (XRD-06)
        "population_features",  # PopulationFeaturePort (XRD-07)
        "outcomes",  # OutcomePublisherPort
    }
)

# (i) Named blocklist — brokers, codecs, registries, cloud SDKs, HTTP/SQL clients, clinical-record
# libraries. Presence of ANY of these roots in a port module means the boundary leaked.
_FORBIDDEN_IMPORT_ROOTS: frozenset[str] = frozenset(
    {
        # brokers / streaming
        "aiokafka",
        "kafka",
        "confluent_kafka",
        # serialisation / schema registry
        "fastavro",
        "avro",
        # cloud SDKs
        "boto3",
        "botocore",
        "awscrt",
        # HTTP clients
        "httpx",
        "requests",
        "aiohttp",
        "urllib",
        "urllib3",
        "socket",
        "websockets",
        # clinical-record libraries
        "hapi",
        "fhirclient",
        "fhir",
        # databases
        "psycopg",
        "psycopg2",
        "asyncpg",
        "sqlalchemy",
        "alembic",
        # framework / runtime surfaces that would drag infrastructure in transitively
        "fastapi",
        "pydantic",
        "langgraph",
        "anthropic",
        "mcp",
        "structlog",
        "opentelemetry",
        "prometheus_client",
        "tenacity",
        "lxml",
        "yaml",
    }
)

# (iii) `maezo` subpackages a port may NEVER import — the ports are the leaf, everything else in
# the repository is a consumer or an adapter of them.
_FORBIDDEN_MAEZO_SUBPACKAGES: frozenset[str] = frozenset(
    {"tools", "runtime", "platform", "agents", "a2a", "gateway"}
)

# (ii) The only non-stdlib import root a port module may name.
_ALLOWED_NON_STDLIB_ROOTS: frozenset[str] = frozenset({"maezo"})

# (iv) Patient-identifying name fragments. Matched as a substring of a lowercased field/parameter
# name. NOTE what is deliberately ABSENT: `beneficiary`, `subject` and `mpi` — `beneficiary_ref`,
# `portable_subject_ref` and `amh_mpi_ref` are OPAQUE references from the frozen contract baseline
# (ADR-0037 XRD-05), not identifying values, and banning their names would ban the pin itself.
_PHI_NAME_FRAGMENTS: frozenset[str] = frozenset(
    {
        "cpf",
        "cns",
        "mrn",
        "ssn",
        "patient",
        "paciente",
        "beneficiario",
        "nome",
        "full_name",
        "given_name",
        "family_name",
        "patient_name",
        "birth",
        "nasc",
        "phone",
        "telefone",
        "email",
        "address",
        "endereco",
        "gender",
        "sexo",
    }
)


def _port_modules() -> dict[str, Path]:
    pkg_dir = Path(ports_pkg.__file__).parent
    return {p.stem: p for p in sorted(pkg_dir.glob("*.py"))}


# Every module fenced here lives directly in `maezo/ports/`, so a relative import resolves against
# this package: level 1 is `maezo.ports`, level 2 is `maezo`, level 3+ escapes the tree entirely.
_PORTS_PACKAGE_PARTS: tuple[str, ...] = ("maezo", "ports")

# Stand-in dotted path for a relative import that reaches ABOVE `maezo` (`from ...x import y`).
# Not a real module name, and deliberately not one: it is not in `sys.stdlib_module_names` and is
# not `maezo`, so fence (ii) rejects it on sight.
_ESCAPED_RELATIVE_IMPORT = "<relative-import-above-maezo>"


def _absolute_import_targets(tree: ast.AST) -> set[str]:
    """Absolute dotted module path named by EVERY import in a `maezo.ports` module.

    Relative imports are RESOLVED, not skipped. `from .. import agents` names `maezo.agents` just
    as surely as the absolute spelling does, and an unresolved relative import is invisible to
    every fence built on this function — which is precisely how a real cross-package dependency
    once sat inside a port module with the whole suite green.

    `from .. import agents` gives no `node.module` at all, only alias names, so the aliases are
    resolved as SUBMODULES of the resolved package. That is the only reading under which the fence
    is sound: if `agents` is a submodule the import is a dependency and must be caught, and if it
    is merely an attribute of `maezo/__init__.py` then naming it here is still a dependency on
    `maezo` outside `maezo.ports`, which is equally forbidden.
    """
    targets: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                targets.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                if node.module:
                    targets.add(node.module)
                continue
            # max(0, ...): a level that walks past the root must clamp to the empty prefix and be
            # reported as escaped. Without the clamp the index goes NEGATIVE and Python's slice
            # wraps it — level 4 would resolve to `maezo`, so `from .... import ports` would read
            # back as the whitelisted `maezo.ports` and pass the fence.
            base = _PORTS_PACKAGE_PARTS[: max(0, len(_PORTS_PACKAGE_PARTS) - (node.level - 1))]
            if not base:
                targets.add(_ESCAPED_RELATIVE_IMPORT)
                continue
            prefix = ".".join(base)
            if node.module:
                targets.add(f"{prefix}.{node.module}")
            else:
                targets.update(f"{prefix}.{alias.name}" for alias in node.names)
    return targets


def _imported_roots(tree: ast.AST) -> set[str]:
    """First dotted component of every import — same shape as the worker-purity fence."""
    return {target.split(".")[0] for target in _absolute_import_targets(tree)}


def _imported_dotted_paths(tree: ast.AST) -> set[str]:
    """Full dotted module paths, for the intra-repo fence (root alone cannot distinguish
    `maezo.ports.errors` from `maezo.agents.andre.graph`)."""
    return _absolute_import_targets(tree)


# The three per-module PREDICATES the fences are built from. Factored out so the synthetic-violation
# test below can exercise the EXACT code the real scan runs — a synthetic test against a re-spelled
# copy of the rule would prove only that the copy works.


def _infrastructure_offenders(tree: ast.AST) -> set[str]:
    """Fence (i): imported roots that are on the named infrastructure blocklist."""
    return _imported_roots(tree) & _FORBIDDEN_IMPORT_ROOTS


def _non_stdlib_offenders(tree: ast.AST) -> set[str]:
    """Fence (ii): imported roots that are neither stdlib nor `maezo`."""
    return {
        root
        for root in _imported_roots(tree)
        if root not in sys.stdlib_module_names and root not in _ALLOWED_NON_STDLIB_ROOTS
    }


def _foreign_maezo_offenders(tree: ast.AST) -> set[str]:
    """Fence (iii): `maezo.*` imports that are not `maezo.ports.*`."""
    return {
        dotted
        for dotted in _imported_dotted_paths(tree)
        if dotted.split(".")[0] == "maezo" and not dotted.startswith("maezo.ports")
    }


def _parsed(path: Path) -> ast.AST:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _imported_modules() -> list[ModuleType]:
    return [importlib.import_module(f"maezo.ports.{n}") for n in sorted(_EXPECTED_MODULES - {"__init__"})]


def _value_types() -> dict[str, type[Any]]:
    """Every dataclass DEFINED in the package (its declaring module is a port module)."""
    found: dict[str, type[Any]] = {}
    for module in _imported_modules():
        for name, obj in vars(module).items():
            if inspect.isclass(obj) and dataclasses.is_dataclass(obj) and obj.__module__ == module.__name__:
                found[f"{module.__name__}.{name}"] = obj
    return found


def _protocol_types() -> dict[str, type[Any]]:
    """Every `typing.Protocol` DEFINED in the package."""
    found: dict[str, type[Any]] = {}
    for module in _imported_modules():
        for name, obj in vars(module).items():
            if (
                inspect.isclass(obj)
                and getattr(obj, "_is_protocol", False)
                and obj.__module__ == module.__name__
            ):
                found[f"{module.__name__}.{name}"] = obj
    return found


def test_port_modules_discovered() -> None:
    """Non-vacuity guard: the fences below must be scanning the REAL package, not an empty glob."""
    mods = _port_modules()
    assert set(mods) == _EXPECTED_MODULES, (
        f"maezo.ports module set drifted: found {sorted(mods)}, expected {sorted(_EXPECTED_MODULES)}. "
        "Adding/removing a port module is a deliberate change to the boundary's shape — update "
        "_EXPECTED_MODULES in the same commit."
    )
    assert len(mods) == 8, f"expected exactly 8 port modules, found {sorted(mods)}"


def test_import_extraction_is_not_vacuous() -> None:
    """NON-VACUITY FOR THE EXTRACTORS THEMSELVES — the guard the module/type counts cannot give.

    Every fence below is `<extractor>(module) & <forbidden>`, so an extractor that returned an
    empty set would make all of them pass unconditionally while proving nothing. (That was not
    hypothetical: stubbing both extractors to `set()` left the entire suite green.) A count of
    modules does not catch it, because the modules are still found and still opened — nothing is
    read OUT of them. So assert the positive: parsing a real port module yields the specific roots
    and dotted paths that module is known to import.
    """
    tree = _parsed(_port_modules()["clinical_context"])

    roots = _imported_roots(tree)
    assert {"__future__", "collections", "dataclasses", "maezo", "typing"} <= roots, (
        f"_imported_roots did not extract clinical_context's known import roots: {sorted(roots)}"
    )

    paths = _imported_dotted_paths(tree)
    assert {"collections.abc", "maezo.ports.errors"} <= paths, (
        f"_imported_dotted_paths did not extract clinical_context's known dotted paths: {sorted(paths)}"
    )

    # And the extraction is non-trivial for EVERY module in the package: a port module with no
    # imports at all would be the one shape that makes an empty extraction legitimate, so pin that
    # no such module exists.
    for name, path in _port_modules().items():
        assert _imported_roots(_parsed(path)), f"{name} extracted zero import roots"


# (source, expected offenders from each of the three predicates). Each case is a source a port
# module could plausibly acquire, and the ONE fence that must catch it.
_SYNTHETIC_VIOLATIONS: tuple[tuple[str, str, set[str], set[str], set[str]], ...] = (
    (
        "blocklisted-infrastructure",
        "import aiokafka\n",
        {"aiokafka"},
        {"aiokafka"},
        set(),
    ),
    (
        "unlisted-third-party (the failure mode a blocklist alone always has)",
        "import some_unlisted_vendor_sdk\n",
        set(),
        {"some_unlisted_vendor_sdk"},
        set(),
    ),
    (
        "absolute cross-package import",
        "from maezo.agents.andre import graph\n",
        set(),
        set(),
        {"maezo.agents.andre"},
    ),
    (
        "RELATIVE cross-package import, bare `from .. import X` form",
        "from .. import agents\n",
        set(),
        set(),
        {"maezo.agents"},
    ),
    (
        "RELATIVE cross-package import, `from ..pkg import X` form",
        "from ..runtime import settings\n",
        set(),
        set(),
        {"maezo.runtime"},
    ),
    (
        "relative import escaping above maezo entirely",
        "from ... import anything\n",
        set(),
        {_ESCAPED_RELATIVE_IMPORT},
        set(),
    ),
    (
        # Level 4 walks TWO levels past the root. The arithmetic that resolves a relative import
        # must clamp there: an unclamped index is negative, and a negative slice wraps around to
        # a SHORTER-BUT-NON-EMPTY prefix, so `from .... import ports` would resolve to the one
        # name this fence whitelists — `maezo.ports` — and the import would pass unseen.
        "relative import escaping TWO levels above maezo, aliasing the whitelisted package name",
        "from .... import ports\n",
        set(),
        {_ESCAPED_RELATIVE_IMPORT},
        set(),
    ),
)

_SYNTHETIC_CLEAN: tuple[tuple[str, str], ...] = (
    ("stdlib absolute", "from dataclasses import dataclass\n"),
    ("stdlib dotted", "from collections.abc import Mapping\n"),
    ("sibling port, absolute", "from maezo.ports.errors import PortResult\n"),
    ("sibling port, relative", "from . import errors\n"),
    ("sibling port, relative from-module", "from .errors import PortResult\n"),
)


@pytest.mark.parametrize(("label", "source", "infra", "non_stdlib", "foreign_maezo"), _SYNTHETIC_VIOLATIONS)
def test_synthetic_violations_are_detected_by_the_real_predicates(
    label: str, source: str, infra: set[str], non_stdlib: set[str], foreign_maezo: set[str]
) -> None:
    """The other half of the non-vacuity proof: known-bad sources put through the SAME predicate
    functions the real scan calls. The extractor test above proves something comes out; this proves
    what comes out is actually judged, and pins WHICH fence is the one that catches each shape.

    The `from .. import agents` case is a regression pin for a real hole: that form gives
    `node.module is None`, so a helper that skipped such nodes let a genuine cross-package
    dependency sit in a port module with every fence green.
    """
    tree = ast.parse(source)
    assert _infrastructure_offenders(tree) == infra, label
    assert _non_stdlib_offenders(tree) == non_stdlib, label
    assert _foreign_maezo_offenders(tree) == foreign_maezo, label


@pytest.mark.parametrize(("label", "source"), _SYNTHETIC_CLEAN)
def test_legitimate_imports_are_not_flagged(label: str, source: str) -> None:
    """The negative control. A fence that flagged everything would also pass every test above, and
    would make the package unmaintainable rather than pure — sibling-port imports (absolute AND
    relative) and stdlib imports must stay legal."""
    tree = ast.parse(source)
    assert _infrastructure_offenders(tree) == set(), label
    assert _non_stdlib_offenders(tree) == set(), label
    assert _foreign_maezo_offenders(tree) == set(), label


def test_no_port_module_imports_infrastructure() -> None:
    """Fence (i): a port importing a broker/codec/cloud/HTTP/SQL/clinical-record client would make
    the payer core depend on the infrastructure ADR-0037 isolates it from."""
    offenders = {
        name: bad
        for name, path in _port_modules().items()
        if (bad := _infrastructure_offenders(_parsed(path)))
    }
    assert not offenders, (
        "ADR-0037 boundary violation — maezo.ports module(s) import infrastructure directly "
        f"(that belongs to an adapter, MZO-050+): {offenders}"
    )


def test_port_modules_import_only_stdlib_and_themselves() -> None:
    """Fence (ii): the POSITIVE form of the rule — stdlib + `maezo` and nothing else. Strictly
    stronger than the blocklist, which by construction can only ban what someone thought of."""
    offenders = {
        name: bad for name, path in _port_modules().items() if (bad := _non_stdlib_offenders(_parsed(path)))
    }
    assert not offenders, (
        "maezo.ports is leaf-domain (stdlib + typing only). Non-stdlib import(s) found: "
        f"{offenders}. A dependency belongs in the ADAPTER that implements the port."
    )


def test_no_port_module_imports_another_maezo_package() -> None:
    """Fence (iii): ports are the leaf. `maezo.tools/runtime/platform/agents/a2a/gateway` are
    consumers or adapters of the ports and may never be dependencies OF them."""
    offenders = {
        name: bad
        for name, path in _port_modules().items()
        if (bad := _foreign_maezo_offenders(_parsed(path)))
    }
    assert not offenders, (
        f"dependency inversion — maezo.ports module(s) import another maezo package: {offenders}"
    )
    # And the specific subpackages the brief names, spelled out so the failure message is explicit.
    forbidden_prefixes = {f"maezo.{sub}" for sub in _FORBIDDEN_MAEZO_SUBPACKAGES}
    for name, path in _port_modules().items():
        for dotted in _imported_dotted_paths(_parsed(path)):
            assert not any(dotted == p or dotted.startswith(f"{p}.") for p in forbidden_prefixes), (
                f"{name} imports {dotted} — ports must not depend on any maezo application package"
            )


def test_no_port_value_type_has_a_phi_shaped_field() -> None:
    """Fence (iv), value types: the ports are OPAQUE (they parse nothing), so the only structural
    guarantee against PHI crossing the boundary is that no PHI-shaped SLOT exists at all."""
    value_types = _value_types()
    assert len(value_types) == 19, (
        f"expected 19 port value types, found {sorted(value_types)} — update this non-vacuity pin "
        "when a value type is deliberately added or removed"
    )
    offenders: dict[str, list[str]] = {}
    for qualname, cls in value_types.items():
        bad = [
            f.name
            for f in dataclasses.fields(cls)
            if any(fragment in f.name.lower() for fragment in _PHI_NAME_FRAGMENTS)
        ]
        if bad:
            offenders[qualname] = bad
    assert not offenders, (
        f"PHI-shaped field name(s) in maezo.ports value types (ADR-0037 prohibition #5): {offenders}"
    )


def test_no_port_method_has_a_phi_shaped_parameter() -> None:
    """Fence (iv), port surfaces: same probe over every Protocol method's parameters — a PHI-shaped
    ARGUMENT would let a caller push identifying data across the boundary even though no value
    type holds it."""
    protocols = _protocol_types()
    assert len(protocols) == 5, f"expected exactly 5 port Protocols, found {sorted(protocols)}"
    offenders: dict[str, list[str]] = {}
    for qualname, proto in protocols.items():
        for method_name, member in inspect.getmembers(proto, callable):
            if method_name.startswith("_"):
                continue
            bad = [
                param
                for param in inspect.signature(member).parameters
                if any(fragment in param.lower() for fragment in _PHI_NAME_FRAGMENTS)
            ]
            if bad:
                offenders[f"{qualname}.{method_name}"] = bad
    assert not offenders, f"PHI-shaped parameter name(s) on maezo.ports Protocol method(s): {offenders}"
