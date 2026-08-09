"""Architecture test: `maezo.adapters.amh` PURITY — the fence that keeps phase A honest (MZO-050a).

**The invariant.** An adapter is allowed to touch infrastructure — that is what an adapter is for.
So this fence is NOT a copy of `tests/unit/ports/test_ports_purity.py`'s "stdlib only" rule. It
enforces the three things that must hold for THIS package, at THIS phase:

  (i)   **No raw-CDC / source-table vocabulary, ever.** ADR-0037 immutable prohibition #1 bans any
        consumption of raw `cdc.*` topics on the AMH compatibility path, and #2 bans
        Tasy/Debezium/AWS/Glue/HAPI types in the payer core. This adapter speaks the CANONICAL
        contract; a Debezium envelope type, a source-table row type or a CDC client appearing here
        would mean the boundary had been re-plumbed straight back to the source system ADR-0037
        exists to decouple from. This layer never relaxes, in any phase.

  (ii)  **No dependency inversion.** `maezo.adapters.amh` may import `maezo.ports` (the seams it
        serves) and itself. It may NOT import `maezo.domain` — this adapter is a leaf of the
        application and does no reference-parsing (`mapping.py` design decision 4: "NO reference
        is parsed"; `maezo.domain` is merged at base, ledger row `mzo-020` — the fence is
        architectural, not a branch-merge artefact) — nor reach back into `maezo.tools`,
        `maezo.runtime`, `maezo.agents`, `maezo.gateway`, `maezo.a2a` or `maezo.platform`. An adapter
        is a leaf of the application, not a peer of it.

  (iii) **PHASE-A DEPENDENCY FENCE: no broker, no codec, no cloud SDK.** Phase A adds no runtime
        dependency, builds no consumer, decodes no Avro and calls no AWS API. Rather than assert that
        in prose, this layer asserts it structurally: `aiokafka`, `fastavro`, `boto3` and friends are
        unimportable from this package today.

        **This layer is EXPECTED to be relaxed by phase B, and that is the point.** A consumer needs
        `fastavro` and an MSK IAM/SASL signer, so the phase-B diff must edit this allowlist — which
        makes it a mandatory review checkpoint at exactly the moment the hard questions come due
        (`ack` durability against the MZO-060 inbox, the undeclared Glue wire framing). A silent
        arrival of a broker client in this package is what the fence prevents; a deliberate,
        reviewed one is what it invites.

Every layer carries a NON-VACUITY guard, and the extraction the layers rest on is itself proved
non-vacuous — `test_import_extraction_is_not_vacuous` asserts the extractors return specific known
imports of a real module, and `test_synthetic_violations_are_detected_by_the_real_predicates` puts
known-bad sources through the SAME predicate functions the real scan uses. Counting modules only
proves the fences were handed files; it does not prove anything was read out of them. Stubbing an
extractor to `set()` must not leave this file green.

**Relative imports are RESOLVED, not skipped** (`from .. import X` gives no `node.module` at all),
and the level arithmetic is CLAMPED with `max(0, ...)`. That clamp is a regression pin, not
defensiveness: an unclamped index goes negative, and a negative slice wraps to a shorter-but-
non-empty prefix, so `from .... import ports` would resolve to the whitelisted `maezo.ports` and pass
unseen. The same bug bit the ports fence once.

**Known limit, stated rather than implied:** this is an AST fence, so it sees only STATIC imports.
`importlib.import_module("aiokafka")` is invisible to every layer here. The mitigation is that these
modules are declarations and pure functions with no dynamic-import site, and that such a call
appearing in this package would be a conspicuous review event on the diff.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

import maezo.adapters.amh as amh_pkg

# The EXACT module set of the package. Phase A ships THREE layers; MZO-050b adds a FOURTH, the
# wire-framing codec seam (still a dark build — no broker, no Avro decode, no new dependency, see
# _FORBIDDEN_PHASE_A_ROOTS below, which `wire_framing.py` must also clear). The absent `consumer.py` is
# deliberate (see the package docstring: `ack` durability needs the MZO-060 inbox, which does not
# exist). Adding a module must be a deliberate edit here.
_EXPECTED_MODULES: frozenset[str] = frozenset(
    {
        "__init__",  # public surface (re-exports only) + the "why no consumer" record
        "contract",  # fail-closed runtime loader for the immutable pin
        "mapping",  # decoded wire event <-> canonical port value types
        "settings",  # env-driven configuration, declared not wired
        "wire_framing",  # MZO-050b: pluggable wire-framing codec seam, fails closed on an undeclared pin
    }
)

# (i) Raw-CDC / source-table / vendor-ERP vocabulary. NEVER allowed, in any phase.
_FORBIDDEN_CDC_ROOTS: frozenset[str] = frozenset(
    {
        "debezium",
        "cdc",
        "kafka_connect",
        "kafkaconnect",
        "oracle",
        "cx_Oracle",
        "oracledb",
        "cx_oracle",
        "tasy",
        "hapi",
        "fhirclient",
        "fhir",
    }
)

# (iii) Phase-A dependency fence: broker clients, Avro codecs, cloud SDKs. Relaxing this is phase B's
# reviewed decision, not an accident.
_FORBIDDEN_PHASE_A_ROOTS: frozenset[str] = frozenset(
    {
        "aiokafka",
        "kafka",
        "confluent_kafka",
        "fastavro",
        "avro",
        "boto3",
        "botocore",
        "awscrt",
        "aws_msk_iam_sasl_signer",
        "httpx",
        "requests",
        "aiohttp",
        "asyncpg",
        "sqlalchemy",
        "alembic",
    }
)

# (ii) `maezo` subpackages this adapter may never import. `domain` is listed FIRST because it is the
# one the brief names explicitly: this adapter is a leaf of the application and does no
# reference-parsing (`mapping.py` design decision 4: "NO reference is parsed"), so a dependency on
# `maezo.domain`'s parsed identity value objects here would be unearned — not a branch-merge
# artefact: `maezo.domain` is merged at base (ledger row `mzo-020`).
_FORBIDDEN_MAEZO_SUBPACKAGES: frozenset[str] = frozenset(
    {"domain", "tools", "runtime", "agents", "gateway", "a2a", "platform"}
)

# The non-stdlib import roots this package may name. `pydantic`/`pydantic_settings` are legitimate
# here and NOT in the ports: `settings.py` is a `BaseSettings` in the house shape, and a settings
# object is adapter configuration, not part of the ERP-neutral core.
_ALLOWED_NON_STDLIB_ROOTS: frozenset[str] = frozenset({"maezo", "pydantic", "pydantic_settings"})

# `maezo.*` dotted prefixes this package MAY import.
_ALLOWED_MAEZO_PREFIXES: tuple[str, ...] = ("maezo.ports", "maezo.adapters.amh")

_PACKAGE_PARTS: tuple[str, ...] = ("maezo", "adapters", "amh")

#: Stand-in for a relative import reaching above `maezo`. Deliberately not a real module name: it is
#: neither stdlib nor an allowed root, so the fences reject it on sight.
_ESCAPED_RELATIVE_IMPORT = "<relative-import-above-maezo>"


def _adapter_modules() -> dict[str, Path]:
    pkg_dir = Path(amh_pkg.__file__).parent
    return {p.stem: p for p in sorted(pkg_dir.glob("*.py"))}


def _absolute_import_targets(tree: ast.AST) -> set[str]:
    """Absolute dotted module path named by EVERY import, relative imports RESOLVED.

    `from .. import contract` names `maezo.adapters.contract` just as surely as the absolute spelling
    does; an unresolved relative import is invisible to every fence built on this function.
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
            # max(0, ...): a level walking past the root must clamp to the empty prefix and be
            # reported as escaped. Unclamped, the index goes NEGATIVE and the slice wraps — level 5
            # would resolve to `maezo.adapters`, and `from ..... import amh` would read back as an
            # allowed prefix and pass the fence.
            base = _PACKAGE_PARTS[: max(0, len(_PACKAGE_PARTS) - (node.level - 1))]
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
    return {target.split(".")[0] for target in _absolute_import_targets(tree)}


def _cdc_offenders(tree: ast.AST) -> set[str]:
    """Fence (i): raw-CDC / source-table / vendor-ERP roots."""
    return _imported_roots(tree) & _FORBIDDEN_CDC_ROOTS


def _phase_a_offenders(tree: ast.AST) -> set[str]:
    """Fence (iii): broker/codec/cloud/HTTP/SQL roots phase A must not carry."""
    return _imported_roots(tree) & _FORBIDDEN_PHASE_A_ROOTS


def _non_stdlib_offenders(tree: ast.AST) -> set[str]:
    """The POSITIVE form — the failure mode a blocklist alone always has: an unlisted third party."""
    return {
        root
        for root in _imported_roots(tree)
        if root not in sys.stdlib_module_names and root not in _ALLOWED_NON_STDLIB_ROOTS
    }


def _foreign_maezo_offenders(tree: ast.AST) -> set[str]:
    """Fence (ii): `maezo.*` imports outside `maezo.ports` / `maezo.adapters.amh`."""
    return {
        dotted
        for dotted in _absolute_import_targets(tree)
        if dotted.split(".")[0] == "maezo"
        and not any(dotted == p or dotted.startswith(f"{p}.") for p in _ALLOWED_MAEZO_PREFIXES)
    }


def _parsed(path: Path) -> ast.AST:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def test_adapter_modules_discovered() -> None:
    """Non-vacuity: the fences scan the REAL package, and `consumer.py` is provably absent."""
    mods = _adapter_modules()
    assert set(mods) == _EXPECTED_MODULES, (
        f"maezo.adapters.amh module set drifted: found {sorted(mods)}, expected "
        f"{sorted(_EXPECTED_MODULES)}. Adding a module changes the adapter's shape — update "
        "_EXPECTED_MODULES in the same commit."
    )
    assert len(mods) == 5, f"expected exactly 5 modules (phase A + the MZO-050b seam), found {sorted(mods)}"
    assert "consumer" not in mods, (
        "consumer.py exists — phase A must NOT ship a consumer: WorkItemSource.ack may only report "
        "success on DURABLE settlement (ADR-0037 XRD-10: Kafka offsets never represent business "
        "completion), and the durable inbox is MZO-060 (DBA-gated, absent — newest migration is "
        "0006_*). See the package docstring."
    )


def test_import_extraction_is_not_vacuous() -> None:
    """NON-VACUITY FOR THE EXTRACTOR ITSELF — the guard a module count cannot give.

    Every fence is `<extractor>(module) & <forbidden>`, so an extractor returning an empty set makes
    all of them pass while proving nothing. Assert the positive: parsing a real module yields the
    specific roots and dotted paths it is known to import.
    """
    tree = _parsed(_adapter_modules()["mapping"])

    roots = _imported_roots(tree)
    assert {"__future__", "hashlib", "json", "collections", "datetime", "maezo", "typing"} <= roots, (
        f"_imported_roots did not extract mapping's known import roots: {sorted(roots)}"
    )

    paths = _absolute_import_targets(tree)
    assert {
        "maezo.adapters.amh.contract",
        "maezo.ports.envelope",
        "maezo.ports.work_items",
    } <= paths, f"_absolute_import_targets did not extract mapping's known dotted paths: {sorted(paths)}"

    # And no module in the package extracts zero imports — the one shape that would make an empty
    # extraction legitimate.
    for name, path in _adapter_modules().items():
        assert _imported_roots(_parsed(path)), f"{name} extracted zero import roots"


# (label, source, cdc, phase_a, non_stdlib, foreign_maezo)
_SYNTHETIC_VIOLATIONS: tuple[tuple[str, str, set[str], set[str], set[str], set[str]], ...] = (
    ("raw-CDC client", "import debezium\n", {"debezium"}, set(), {"debezium"}, set()),
    ("vendor ERP driver", "import oracledb\n", {"oracledb"}, set(), {"oracledb"}, set()),
    (
        "clinical-record library",
        "from fhirclient import models\n",
        {"fhirclient"},
        set(),
        {"fhirclient"},
        set(),
    ),
    ("phase-A broker client", "import aiokafka\n", set(), {"aiokafka"}, {"aiokafka"}, set()),
    (
        "phase-A Avro codec",
        "from fastavro import schemaless_reader\n",
        set(),
        {"fastavro"},
        {"fastavro"},
        set(),
    ),
    ("phase-A cloud SDK", "import boto3\n", set(), {"boto3"}, {"boto3"}, set()),
    (
        "phase-A MSK IAM signer",
        "import aws_msk_iam_sasl_signer\n",
        set(),
        {"aws_msk_iam_sasl_signer"},
        {"aws_msk_iam_sasl_signer"},
        set(),
    ),
    (
        "unlisted third party (the failure mode a blocklist alone always has)",
        "import some_unlisted_vendor_sdk\n",
        set(),
        set(),
        {"some_unlisted_vendor_sdk"},
        set(),
    ),
    (
        "maezo.domain — DPO-gated identity semantics, leaf-of-application fence",
        "from maezo.domain.identity import SubjectRef\n",
        set(),
        set(),
        set(),
        {"maezo.domain.identity"},
    ),
    (
        "reaching back into the application",
        "from maezo.tools.workers import ceilings\n",
        set(),
        set(),
        set(),
        {"maezo.tools.workers"},
    ),
    (
        "RELATIVE cross-package import, bare `from .. import X` form",
        "from .. import fake_sibling\n",
        set(),
        set(),
        set(),
        {"maezo.adapters.fake_sibling"},
    ),
    (
        "RELATIVE reach into another maezo package",
        "from ...runtime import settings\n",
        set(),
        set(),
        set(),
        {"maezo.runtime"},
    ),
    (
        "relative import escaping above maezo entirely",
        "from .... import anything\n",
        set(),
        set(),
        {_ESCAPED_RELATIVE_IMPORT},
        set(),
    ),
    (
        # Level 5 walks TWO levels past the root. The arithmetic must clamp: unclamped, the negative
        # slice wraps to `maezo.adapters`, so `from ..... import amh` would resolve to the allowed
        # `maezo.adapters.amh` and the import would pass unseen.
        "relative import escaping TWO levels above maezo, aliasing the allowed package name",
        "from ..... import amh\n",
        set(),
        set(),
        {_ESCAPED_RELATIVE_IMPORT},
        set(),
    ),
)

_SYNTHETIC_CLEAN: tuple[tuple[str, str], ...] = (
    ("stdlib absolute", "from dataclasses import dataclass\n"),
    ("stdlib dotted", "from collections.abc import Mapping\n"),
    ("the ports this adapter serves", "from maezo.ports.work_items import CanonicalWorkItem\n"),
    ("sibling module, absolute", "from maezo.adapters.amh.contract import AmhContractPin\n"),
    ("sibling module, relative", "from .contract import AmhContractPin\n"),
    ("sibling module, bare relative", "from . import mapping\n"),
    ("house settings library", "from pydantic_settings import BaseSettings\n"),
)


@pytest.mark.parametrize(
    ("label", "source", "cdc", "phase_a", "non_stdlib", "foreign_maezo"), _SYNTHETIC_VIOLATIONS
)
def test_synthetic_violations_are_detected_by_the_real_predicates(
    label: str,
    source: str,
    cdc: set[str],
    phase_a: set[str],
    non_stdlib: set[str],
    foreign_maezo: set[str],
) -> None:
    """The other half of the non-vacuity proof: known-bad sources through the SAME predicates the
    real scan calls, pinning WHICH fence catches each shape. A synthetic test against a re-spelled
    copy of the rule would prove only that the copy works."""
    tree = ast.parse(source)
    assert _cdc_offenders(tree) == cdc, label
    assert _phase_a_offenders(tree) == phase_a, label
    assert _non_stdlib_offenders(tree) == non_stdlib, label
    assert _foreign_maezo_offenders(tree) == foreign_maezo, label


@pytest.mark.parametrize(("label", "source"), _SYNTHETIC_CLEAN)
def test_legitimate_imports_are_not_flagged(label: str, source: str) -> None:
    """The negative control. A fence that flagged everything would pass every test above and make the
    package unbuildable rather than pure."""
    tree = ast.parse(source)
    assert _cdc_offenders(tree) == set(), label
    assert _phase_a_offenders(tree) == set(), label
    assert _non_stdlib_offenders(tree) == set(), label
    assert _foreign_maezo_offenders(tree) == set(), label


def test_no_module_imports_raw_cdc_or_source_table_types() -> None:
    """Fence (i): ADR-0037 immutable prohibitions #1/#2 — this adapter speaks the CANONICAL contract,
    never the source system's."""
    offenders = {
        name: bad for name, path in _adapter_modules().items() if (bad := _cdc_offenders(_parsed(path)))
    }
    assert not offenders, (
        "ADR-0037 violation — maezo.adapters.amh imports raw-CDC / source-table / vendor-ERP "
        f"type(s): {offenders}. The compatibility path consumes the canonical catalogue only."
    )


def test_no_module_imports_a_phase_a_forbidden_dependency() -> None:
    """Fence (iii): phase A ships no consumer, no codec, no cloud client and no new runtime
    dependency — enforced structurally, not promised in prose."""
    offenders = {
        name: bad for name, path in _adapter_modules().items() if (bad := _phase_a_offenders(_parsed(path)))
    }
    assert not offenders, (
        f"phase-A dependency fence breached: {offenders}. A broker client, Avro codec or cloud SDK "
        "means a consumer is being built — which requires the MZO-060 durable inbox for ack "
        "settlement (ADR-0037 XRD-10) and a deliberate edit to this allowlist."
    )


def test_modules_import_only_allowed_non_stdlib_roots() -> None:
    """The POSITIVE form of the dependency rule: stdlib + `maezo` + the house settings library."""
    offenders = {
        name: bad
        for name, path in _adapter_modules().items()
        if (bad := _non_stdlib_offenders(_parsed(path)))
    }
    assert not offenders, (
        f"unexpected non-stdlib import root(s) in maezo.adapters.amh: {offenders}. Phase A adds NO "
        "runtime dependency; allowed roots are "
        f"{sorted(_ALLOWED_NON_STDLIB_ROOTS)}."
    )


def test_no_module_imports_another_maezo_package() -> None:
    """Fence (ii): the adapter depends on the ports and itself — never on `maezo.domain` (DPO-gated,
    unmerged) and never back into the application."""
    offenders = {
        name: bad
        for name, path in _adapter_modules().items()
        if (bad := _foreign_maezo_offenders(_parsed(path)))
    }
    assert not offenders, (
        f"dependency inversion — maezo.adapters.amh imports a foreign maezo package: {offenders}. "
        f"Allowed prefixes: {list(_ALLOWED_MAEZO_PREFIXES)}."
    )
    # And the specific subpackages the brief names, spelled out so the failure message is explicit.
    forbidden_prefixes = {f"maezo.{sub}" for sub in _FORBIDDEN_MAEZO_SUBPACKAGES}
    for name, path in _adapter_modules().items():
        for dotted in _absolute_import_targets(_parsed(path)):
            assert not any(dotted == p or dotted.startswith(f"{p}.") for p in forbidden_prefixes), (
                f"{name} imports {dotted} — forbidden for this adapter"
            )


def test_ports_are_actually_used_so_the_fence_is_about_a_real_dependency() -> None:
    """Non-vacuity for fence (ii)'s allowlist: `maezo.ports` must actually be imported somewhere.

    A fence that permits `maezo.ports` proves nothing if the package never imports it — the adapter
    would not be serving the seams at all. Pin that the dependency the allowlist exists for is real.
    """
    all_targets: set[str] = set()
    for path in _adapter_modules().values():
        all_targets |= _absolute_import_targets(_parsed(path))
    ports_imports = {t for t in all_targets if t.startswith("maezo.ports")}
    assert ports_imports, "no maezo.ports import found — this adapter does not serve any port"
