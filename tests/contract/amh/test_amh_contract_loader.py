"""Cross-check: the pin, the CI gate, the ports and the runtime loader all state ONE contract.

MZO-050a. Three independent copies of the frozen truth now exist, deliberately:

  1. `config/integrations/amh/contracts.lock.json` — the AMH-published pin (immutable, XRD-04).
  2. `scripts/ci/verify_amh_contract_pin.py` — the CI gate's hardcoded catalogue, which exists so "an
     edit to the lock that renames a topic or reorders the envelope has to fight a second,
     independent copy of the truth" (that file's own words).
  3. `src/maezo/adapters/amh/contract.py` — the RUNTIME loader's catalogue. It cannot import (2):
     `scripts/` is declared stdlib-only, is not part of the `maezo` package, and is NOT shipped in the
     wheel (`[tool.hatch.build.targets.wheel] packages = ["src/maezo"]`), so importing it from `src/`
     would raise `ModuleNotFoundError` inside a container while passing every local test.

Plus a fourth statement of the envelope shape in `maezo.ports.envelope.ENVELOPE_FIELD_ORDER`.

Duplication is only safe if drift is impossible, so this module asserts every copy equal to every
other. Importing the CI gate HERE is legitimate — pytest sets `pythonpath = ["."]` and
`tests/contract/amh/test_contract_pin.py` already does exactly this.

These tests are hermetic: no network, no engine, no broker.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from scripts.ci.verify_amh_contract_pin import (
    _SEMVER_RE as GATE_SEMVER_RE,
)
from scripts.ci.verify_amh_contract_pin import (
    DEFAULT_LOCK_PATH,
)
from scripts.ci.verify_amh_contract_pin import (
    FROZEN_ARTIFACT_PATHS as GATE_ARTIFACT_PATHS,
)
from scripts.ci.verify_amh_contract_pin import (
    FROZEN_COMPATIBILITY_MODE as GATE_COMPATIBILITY_MODE,
)
from scripts.ci.verify_amh_contract_pin import (
    FROZEN_CONTRACT_NAME as GATE_CONTRACT_NAME,
)
from scripts.ci.verify_amh_contract_pin import (
    FROZEN_ENVELOPE_FIELD_ORDER as GATE_ENVELOPE_FIELD_ORDER,
)
from scripts.ci.verify_amh_contract_pin import (
    FROZEN_GLUE_SCHEMA_KEYS as GATE_GLUE_SCHEMA_KEYS,
)
from scripts.ci.verify_amh_contract_pin import (
    FROZEN_SCHEMA_VERSION_STATUS as GATE_SCHEMA_VERSION_STATUS,
)
from scripts.ci.verify_amh_contract_pin import (
    FROZEN_SOURCE_PRODUCT_VOCABULARY as GATE_SOURCE_PRODUCT_VOCABULARY,
)
from scripts.ci.verify_amh_contract_pin import (
    FROZEN_STATUS as GATE_STATUS,
)
from scripts.ci.verify_amh_contract_pin import (
    FROZEN_TOPIC_MAJOR as GATE_TOPIC_MAJOR,
)
from scripts.ci.verify_amh_contract_pin import (
    FROZEN_TOPICS as GATE_TOPICS,
)
from scripts.ci.verify_amh_contract_pin import (
    PLACEHOLDER_SUBSTRINGS as GATE_PLACEHOLDER_SUBSTRINGS,
)
from scripts.ci.verify_amh_contract_pin import (
    REQUIRED_SECTIONS as GATE_REQUIRED_SECTIONS,
)
from scripts.ci.verify_amh_contract_pin import (
    parse_semver as gate_parse_semver,
)

from maezo.adapters.amh import contract as loader
from maezo.ports.envelope import ENVELOPE_FIELD_COUNT, ENVELOPE_FIELD_ORDER

REPO_ROOT = Path(__file__).resolve().parents[3]
LOCK_PATH = REPO_ROOT / DEFAULT_LOCK_PATH


def _lock() -> dict[str, Any]:
    parsed: dict[str, Any] = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    return parsed


# ---------------------------------------------------------------------------
# Loader catalogue == CI gate catalogue
# ---------------------------------------------------------------------------


def test_loader_and_ci_gate_agree_on_the_frozen_topics() -> None:
    assert loader.FROZEN_TOPICS == GATE_TOPICS


def test_loader_and_ci_gate_agree_on_the_envelope_field_order() -> None:
    assert loader.FROZEN_ENVELOPE_FIELD_ORDER == GATE_ENVELOPE_FIELD_ORDER


def test_loader_and_ci_gate_agree_on_the_source_product_vocabulary() -> None:
    assert loader.FROZEN_SOURCE_PRODUCT_VOCABULARY == GATE_SOURCE_PRODUCT_VOCABULARY


def test_loader_and_ci_gate_agree_on_artifacts_glue_keys_and_scalars() -> None:
    assert loader.FROZEN_ARTIFACT_PATHS == GATE_ARTIFACT_PATHS
    assert loader.FROZEN_GLUE_SCHEMA_KEYS == GATE_GLUE_SCHEMA_KEYS
    assert loader.FROZEN_STATUS == GATE_STATUS
    assert loader.FROZEN_CONTRACT_NAME == GATE_CONTRACT_NAME
    assert loader.FROZEN_COMPATIBILITY_MODE == GATE_COMPATIBILITY_MODE
    assert loader.FROZEN_TOPIC_MAJOR == GATE_TOPIC_MAJOR


def test_loader_and_ci_gate_agree_on_the_glue_schema_version_status() -> None:
    """The one frozen constant the loader used to OMIT (MZO-050a repair, LOW-3), which is exactly how a
    gap between the two copies looks before it is closed: not a disagreement, an absence."""
    assert loader.FROZEN_SCHEMA_VERSION_STATUS == GATE_SCHEMA_VERSION_STATUS


def test_loader_and_ci_gate_agree_on_the_compatibility_result() -> None:
    """The gate spells this one as a literal inside `check_formats` rather than as a module constant, so
    the drift guard has to read the gate's SOURCE. If the gate ever promotes it to a constant or changes
    the expected verdict, this fails and someone reconciles the two deliberately."""
    gate_source = (REPO_ROOT / "scripts/ci/verify_amh_contract_pin.py").read_text(encoding="utf-8")
    assert f'"compatibility_report.result", "{loader.FROZEN_COMPATIBILITY_RESULT}"' in gate_source


def test_loader_and_ci_gate_agree_on_the_required_evidence_sections() -> None:
    """The loader checks a SUBSET of the gate's `REQUIRED_SECTIONS` — the two evidence sections it can
    verify without the artifact bytes — and that subset must really be a subset."""
    assert set(loader.REQUIRED_EVIDENCE_SECTIONS) <= set(GATE_REQUIRED_SECTIONS)
    assert set(loader.REQUIRED_EVIDENCE_SECTIONS) == {"compatibility_report", "xrg3_verification"}


def test_loader_and_ci_gate_agree_on_the_placeholder_tokens() -> None:
    assert loader.PLACEHOLDER_SUBSTRINGS == GATE_PLACEHOLDER_SUBSTRINGS


def test_loader_and_ci_gate_agree_on_the_pin_path() -> None:
    assert loader.CONTRACT_PIN_RELATIVE_PATH == DEFAULT_LOCK_PATH


# ---------------------------------------------------------------------------
# The CI gate must be the STRICTEST layer, not a looser one
# ---------------------------------------------------------------------------


def test_loader_and_ci_gate_share_one_strict_semver_grammar() -> None:
    """The gate was LOOSER than the adapter on two axes — bare `\\d` (every Unicode decimal digit) and
    leading zeros — so adapter-accepts was a strict SUBSET of gate-accepts.

    That direction is SAFE (no legitimate pin is wedged, and the committed pin is all `1.0.0`) but it
    points the wrong way: CI could go GREEN on a pin the adapter then refuses at boot, which is later
    than ADR-0037 XRD-04 wants — "falham fail-closed ANTES de merge/deploy de adapter". Byte-equality is
    asserted rather than mere subset-ness, because a subset assertion would let the two drift apart again
    in the safe direction and quietly restore exactly this gap."""
    assert GATE_SEMVER_RE.pattern == loader._SEMVER_RE.pattern
    assert GATE_SEMVER_RE.flags == loader._SEMVER_RE.flags


def test_the_two_semver_validators_agree_on_every_adversarial_spelling() -> None:
    """Pattern equality is the mechanism; agreement on VALUES is the property that matters. Every
    spelling here is one the two copies used to disagree on, or one that used to crash a copy."""
    over_limit = "1" * 4301
    for spelling in (
        "1.0.0",
        "0.0.0",
        "1.10.20",
        "999999999.0.0",
        "01.0.0",
        "1.00.0",
        "1.0.00",
        "１.０.０",
        "1.0.٠",
        "².0.0",
        "1.0",
        "v1.0.0",
        "1.0.0 ",
        " 1.0.0",
        "",
        f"{over_limit}.0.0",
        f"1.{over_limit}.0",
        "1000000000.0.0",
    ):
        assert gate_parse_semver(spelling) == loader.parse_semver(spelling), spelling


def test_neither_semver_validator_raises_on_a_digit_run_past_the_int_limit() -> None:
    """Both copies fed an unbounded `\\d+`/`[0-9]*` capture to `int()`, and CPython refuses
    `int(str)` past `sys.int_max_str_digits`. Both had to be bounded, not just the adapter's: a gate
    that CRASHES is failing closed by accident rather than by design, and its violation report is the
    thing an operator reads."""
    over_limit = "1" * (sys.get_int_max_str_digits() + 1)
    assert gate_parse_semver(f"{over_limit}.0.0") is None
    assert loader.parse_semver(f"{over_limit}.0.0") is None


def test_the_committed_pin_is_accepted_by_the_tightened_grammar() -> None:
    """Non-vacuity for the tightening: the real pin must still pass, in both copies, or the gate is
    strict in a way that wedges the contract it exists to protect."""
    lock = _lock()
    for dotted in ("provenance", "envelope"):
        version = lock[dotted]["canonical_schema_version"]
        assert loader.parse_semver(version) == (1, 0, 0), dotted
        assert gate_parse_semver(version) == (1, 0, 0), dotted


# ---------------------------------------------------------------------------
# Loader catalogue == the ports
# ---------------------------------------------------------------------------


def test_the_pin_and_the_ports_agree_on_the_envelope_field_order() -> None:
    """THE cross-check the brief names: the loaded pin's envelope order must equal
    `maezo.ports.envelope.ENVELOPE_FIELD_ORDER`. The pin is the contract and the ports are the payer
    core's mirror of it; if they diverge, the boundary means two different things and the mapping
    layer would silently build events with a transposed field."""
    pin = loader.load_contract_pin(LOCK_PATH)
    assert pin.envelope_field_order == ENVELOPE_FIELD_ORDER
    assert pin.envelope_field_count == ENVELOPE_FIELD_COUNT == 28
    assert loader.FROZEN_ENVELOPE_FIELD_ORDER == ENVELOPE_FIELD_ORDER


def test_all_four_statements_of_the_envelope_shape_are_identical() -> None:
    """Pin file, CI gate, runtime loader, ports — one tuple, four places, no drift."""
    from_file = tuple(_lock()["envelope"]["field_order"])
    assert from_file == GATE_ENVELOPE_FIELD_ORDER
    assert from_file == loader.FROZEN_ENVELOPE_FIELD_ORDER
    assert from_file == ENVELOPE_FIELD_ORDER
    assert len(from_file) == 28


# ---------------------------------------------------------------------------
# Loader catalogue == the pin file on disk
# ---------------------------------------------------------------------------


def test_the_committed_pin_loads_through_the_runtime_loader() -> None:
    """The gate proves the pin is well-formed for CI; this proves PRODUCTION code can consume it."""
    pin = loader.load_contract_pin(LOCK_PATH)
    assert pin.status == loader.FROZEN_STATUS
    assert pin.compatibility_mode == loader.FROZEN_COMPATIBILITY_MODE
    assert pin.contract_name == loader.FROZEN_CONTRACT_NAME


def test_loaded_topics_match_the_pin_file_verbatim() -> None:
    pin = loader.load_contract_pin(LOCK_PATH)
    raw_topics = _lock()["topics"]
    assert len(pin.topics) == len(raw_topics)
    for loaded, raw in zip(pin.topics, raw_topics, strict=True):
        assert loaded.name == raw["name"]
        assert loaded.quarantine == raw["quarantine"]
        assert loaded.direction == raw["direction"]
        assert loaded.schema_path == raw["schema_path"]
        assert loaded.major_version == raw["major_version"]


def test_loaded_glue_ids_match_the_pin_file_verbatim() -> None:
    pin = loader.load_contract_pin(LOCK_PATH)
    raw = _lock()["glue_registration"]
    assert dict(pin.glue.schema_version_ids) == raw["schema_version_ids"]
    assert pin.glue.region == raw["region"]
    assert pin.glue.registry_name == raw["registry_name"]
    assert pin.glue.environment == raw["environment"]


def test_loaded_digests_match_the_pin_file_verbatim() -> None:
    pin = loader.load_contract_pin(LOCK_PATH)
    lock = _lock()
    assert dict(pin.artifact_digests) == {a["path"]: a["sha256"] for a in lock["artifacts"]}
    assert dict(pin.fixture_digests) == {f["vendored_path"]: f["sha256"] for f in lock["fixtures"]}
    assert pin.manifest_digest == lock["manifest_pin"]["sha256"]


def test_every_vendored_fixture_the_loader_reports_exists_on_disk() -> None:
    """The loader reports fixture digests keyed by `vendored_path`; a key that pointed nowhere would
    make the mapping tests silently skip their vectors."""
    pin = loader.load_contract_pin(LOCK_PATH)
    assert len(pin.fixture_digests) == 10
    for vendored_path in pin.fixture_digests:
        assert (REPO_ROOT / vendored_path).is_file(), f"pinned fixture missing: {vendored_path}"


# ---------------------------------------------------------------------------
# Packaging
# ---------------------------------------------------------------------------


def test_the_pin_is_force_included_in_the_wheel() -> None:
    """PACKAGING PROOF (the declaration half). `config/` is outside `packages = ["src/maezo"]`, so
    without an explicit force-include the pin would not reach a container and the fail-closed loader
    would refuse to start every packaged deployment — the same bricking failure ADR-0025 D2 documents
    for the autonomy matrix.

    Asserted against `pyproject.toml` text rather than a built wheel so the check is hermetic and
    fast; `tests/unit/adapters/amh/test_contract_loader.py` proves the RESOLUTION half by importing
    the loader from a synthesised wheel-shaped tree.
    """
    pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert '"config/integrations/amh" = "maezo/config/integrations/amh"' in pyproject, (
        "the AMH contract pin is not force-included in the wheel — a packaged deployment would have "
        "no pin and maezo.adapters.amh.contract would fail closed at boot"
    )
    # And the precedent it follows is still there (if that line vanished, this test's rationale would
    # be stale and the reviewer should know).
    assert '"spec/policies/autonomy" = "maezo/spec/policies/autonomy"' in pyproject


def test_the_loader_does_not_import_the_ci_gate() -> None:
    """The whole reason the catalogue is duplicated. `scripts/` is not in the wheel, so an import from
    `src/` would work in CI and `ModuleNotFoundError` in production — the worst possible split."""
    import ast

    source = (REPO_ROOT / "src/maezo/adapters/amh/contract.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    offenders = {name for name in imported if name.split(".")[0] == "scripts"}
    assert not offenders, (
        f"maezo.adapters.amh.contract imports {offenders} — scripts/ is stdlib-only, is not part of "
        "the maezo package, and is not shipped in the wheel"
    )
