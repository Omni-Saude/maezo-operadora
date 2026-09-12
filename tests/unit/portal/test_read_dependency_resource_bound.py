"""The portal-read dependency bound must admit the operadora's own deployed BPMN/DMN.

V3-Q3 rows 6-8: `PortalReadCommand.resourceDigest` bounded the *deployed* process model with
`MAX` (64 KiB), the *untrusted ingress* bound, so `verifyCatalog` refused
`SP-OP-AUTH-001_Autorizacao_Previa.bpmn` (69 706 B) with READ_DEPENDENCY_UNAVAILABLE. The two
concerns are now separate constants. This module pins both against the real spec tree so the
fence can never be silently lowered back, nor a model land that the catalog path cannot read.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).parents[3]
JAVA_ROOT = ROOT / "src/maezo/portal/engine/java"
MODELS = JAVA_ROOT / "src/main/java/br/com/maezo/human/PortalReadModels.java"
COMMAND = JAVA_ROOT / "src/main/java/br/com/maezo/human/PortalReadCommand.java"
BINDING = ROOT / "src/maezo/gateway/human/decision_binding.py"
AUTH_BPMN = ROOT / "spec/processes/bpmn/SP-OP-AUTH-001_Autorizacao_Previa.bpmn"


def _constant(name: str) -> int:
    match = re.search(rf"static final int {name} = (\d+);", MODELS.read_text(encoding="utf-8"))
    assert match is not None, f"{name} missing from {MODELS.relative_to(ROOT)}"
    return int(match.group(1))


def _deployed_models() -> list[Path]:
    models = sorted(
        path
        for path in (ROOT / "spec/processes").rglob("*")
        if path.is_file() and path.suffix in {".bpmn", ".dmn"}
    )
    assert models, "no BPMN/DMN found under spec/processes"
    return models


def test_ingress_bound_is_unchanged() -> None:
    # Raising MAX would widen every untrusted surface (servlet body, envelopes, base64 fields,
    # stored rows) and must not be the way a large deployed model gets admitted.
    assert _constant("MAX") == 65536


def test_auth_bpmn_exceeds_the_ingress_bound_so_the_split_is_load_bearing() -> None:
    size = AUTH_BPMN.stat().st_size
    assert size > _constant("MAX"), (
        f"{AUTH_BPMN.name} is {size} B; if it ever fits in MAX this fence stops proving "
        "anything — re-derive the bound, do not delete the test"
    )
    assert size <= _constant("RESOURCE_MAX")


def test_every_deployed_model_fits_the_resource_bound() -> None:
    resource_max = _constant("RESOURCE_MAX")
    over = {
        str(path.relative_to(ROOT)): path.stat().st_size
        for path in _deployed_models()
        if path.stat().st_size > resource_max
    }
    assert over == {}, (
        f"deployed models over RESOURCE_MAX ({resource_max}): verifyCatalog would refuse them "
        "with READ_DEPENDENCY_UNAVAILABLE"
    )


def test_several_production_models_need_the_wider_bound() -> None:
    # Documents why the split exists: this was never an AUTH-only overshoot.
    maximum = _constant("MAX")
    over = [path.name for path in _deployed_models() if path.stat().st_size > maximum]
    assert AUTH_BPMN.name in over
    assert len(over) >= 7, over


def test_resource_bound_matches_the_python_engine_bytearray_bound() -> None:
    # The same ACT_GE_BYTEARRAY bytes are read by the Python decision binding with a 1 MiB bound;
    # the Java reader of that artifact uses the same number rather than an invented one.
    binding = BINDING.read_text(encoding="utf-8")
    assert "octet_length(b.bytes_)<=1048576" in binding
    assert _constant("RESOURCE_MAX") == 1048576


def test_the_bounded_read_lives_with_the_bound() -> None:
    models = MODELS.read_text(encoding="utf-8")
    assert "static boolean resourceMatches(InputStream stream, Object expected)" in models
    assert "byte[] raw = stream.readNBytes(RESOURCE_MAX + 1);" in models
    assert "return raw.length <= RESOURCE_MAX && Jcs.digest(raw).equals(expected);" in models
    # PortalReadCommand keeps its own 503 contract but no longer its own copy of the bound.
    assert "if (!resourceMatches(stream, expected))" in COMMAND.read_text(encoding="utf-8")


def _human_main_sources() -> list[Path]:
    sources = sorted((JAVA_ROOT / "src/main/java/br/com/maezo/human").glob("*.java"))
    assert sources, "human main package sources not found"
    return sources


def test_every_deployed_model_reader_uses_the_shared_bounded_read() -> None:
    # V9-Q4 F2: `AuthRuntime` re-typed the 1 MiB literals and `AtomicHumanCommand` read a sibling
    # deployed model with an unbounded readAllBytes(), while the pin only looked at one file.
    unbounded = []
    for source in _human_main_sources():
        text = source.read_text(encoding="utf-8")
        if "getProcessModel(" not in text and "getDecisionModel(" not in text:
            continue
        if "resourceMatches(" not in text and "resourceDigest(" not in text:
            unbounded.append(source.name)
    assert unbounded == [], (
        "these read an engine-deployed model without PortalReadModels.resourceMatches: an "
        "unbounded read of a deployed model is the regression this fence exists to stop"
    )


def test_the_resource_bound_is_stated_only_once() -> None:
    resource_max = _constant("RESOURCE_MAX")
    retyped = [
        source.name
        for source in _human_main_sources()
        if source.name != "PortalReadModels.java"
        and (
            str(resource_max) in source.read_text(encoding="utf-8")
            or str(resource_max + 1) in source.read_text(encoding="utf-8")
        )
    ]
    assert retyped == [], (
        f"the {resource_max}-byte deployed-model bound is re-typed as a literal here; "
        "use PortalReadModels.RESOURCE_MAX so it cannot drift"
    )
