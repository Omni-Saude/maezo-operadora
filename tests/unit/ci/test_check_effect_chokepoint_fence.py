"""Unit tests for the repo-wide effect-chokepoint fence gate (Onda 1 design §8).

Mirrors `test_check_start_process_fence.py`'s two layers:

1. **Synthetic-tree** tests inject ONE violation into a throwaway `tmp_path` source tree and
   drive `scan_tree`/`check_completeness` directly — the permanent regression form of "inject a
   bypass in a scratch copy -> the gate goes RED".
2. **Real-tree** tests run the full scan against `src/maezo` (+ the real `spec/`/`tests/` trees)
   and assert the gate PASSES today, with non-vacuity counters proving every rule actually found
   something to allow, not merely nothing to complain about.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from scripts.ci.check_effect_chokepoint_fence import (
    CONSTRUCTION_ALLOWLIST_BY_NAME,
    DECLARED_TEST_DOUBLE_EXCEPTIONS,
    FORBIDDEN_CONSTRUCTION_NAMES,
    FORBIDDEN_ENV_VAR_NAMES,
    FORBIDDEN_REST_PATH_SUBSTRINGS,
    HTTPX_SANCTIONED_MODULES,
    REST_PATH_SANCTIONED_MODULES,
    RUNTIME_MODE_ENV_VAR_NAMES,
    check_completeness,
    scan_module,
    scan_tree,
)

from maezo.gateway import effect_classes

# tests/unit/ci/<file> -> parents[3] == repo root.
_REPO_ROOT = Path(__file__).resolve().parents[3]
_SRC_DIR = _REPO_ROOT / "src" / "maezo"


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


# =================================================================================================
# Real-tree: the gate passes today, with non-vacuous counters on every rule
# =================================================================================================


def test_real_tree_passes() -> None:
    result = scan_tree(_SRC_DIR)
    assert result.ok, result.render()


def test_real_tree_scanned_files_sanity() -> None:
    result = scan_tree(_SRC_DIR)
    assert result.scanned_files > 100  # sanity: the scan covers the real tree, not a stub


def test_real_tree_is_not_vacuous_every_rule_counter_fired() -> None:
    """A passing gate whose allowlists were never actually exercised would be worthless — every
    §8.1-§8.4 rule must have found at least one REAL, sanctioned occurrence in the live tree."""
    result = scan_tree(_SRC_DIR)
    expected_keys = {
        "8.1_construction_sanctioned",
        "8.2_httpx_client_sanctioned",
        "8.2_rest_path_sanctioned",
        "8.3_whole_module_import_sanctioned",
        "8.3_concrete_provider_import_sanctioned",
        "8.3_env_read_sanctioned",
        "8.4_declared_exception",
    }
    assert expected_keys <= result.counters.keys(), result.render()
    for key in expected_keys:
        assert result.counters[key] > 0, f"{key} never fired — is the rule dead? {result.render()}"


def test_real_tree_completeness_passes_and_is_not_vacuous() -> None:
    violations, counters = check_completeness(_SRC_DIR, _REPO_ROOT)
    assert violations == []
    expected_keys = {
        "8.5_item1_classes_constructed",
        "8.5_item2_tool_ids_declared",
        "8.5_item3_choked_true",
        "8.5_item3_choked_false_documented",
        "8.5_item4_ladder_classes",
    }
    assert expected_keys <= counters.keys()
    for key in expected_keys:
        assert counters[key] > 0, f"{key} is zero — {counters}"
    # The one deliberately-zero counter: an undocumented choked:false must never occur today.
    assert counters["8.5_item3_choked_false_undocumented"] == 0


def test_cibseven_server_stays_absent_on_the_real_tree() -> None:
    """R-6: the deleted enforcement-point class must not have crept back in anywhere."""
    for path in sorted(_SRC_DIR.rglob("*.py")):
        scan = scan_module(path)
        assert not scan.cibseven_server_defined, f"CibSevenServer redefined at {path}"


# =================================================================================================
# §8.1 — fixture-based: one test PER composition root, proving a raw construction added there
# raises a violation (never by mutating the real tree)
# =================================================================================================


@pytest.mark.parametrize(
    ("relative_path", "offending_name"),
    [
        ("runtime/agent_runtime/service.py", "WhatsAppServer"),
        ("runtime/agent_runtime/a2a_composition.py", "FhirServer"),
        ("platform/webhooks/service.py", "CibSevenHttpTransport"),
        ("platform/integrations/notifications_bridge.py", "FhirServer"),
        ("runtime/worker_runtime/service.py", "FhirServer"),
    ],
)
def test_raw_construction_in_a_composition_root_raises(
    tmp_path: Path, relative_path: str, offending_name: str
) -> None:
    """Each of the five composition roots B2 repointed at the registry: re-adding a raw
    construction of a class that root is NOT allowlisted for must fail the gate. Fixture-based —
    the real tree is never mutated."""
    assert relative_path not in CONSTRUCTION_ALLOWLIST_BY_NAME.get(offending_name, frozenset()), (
        "test setup error: the chosen offending_name IS allowlisted for this root — pick another"
    )
    _write(
        tmp_path / relative_path,
        f"from maezo.tools.mcp_fhir.server import {offending_name}\n\n"
        f"def build():\n    return {offending_name}()\n",
    )

    result = scan_tree(tmp_path)

    assert not result.ok
    assert any(offending_name in v and "[8.1]" in v for v in result.violations), result.render()
    assert any(str(tmp_path / relative_path) in v for v in result.violations)


def test_raw_construction_inside_the_registry_is_sanctioned(tmp_path: Path) -> None:
    _write(
        tmp_path / "gateway" / "tool_registry.py",
        "def build_fhir_seam():\n    return FhirServer()\n",
    )
    result = scan_tree(tmp_path)
    assert result.ok, result.render()
    assert result.counters.get("8.1_construction_sanctioned") == 1


def test_raw_construction_inside_a_seam_decorator_is_sanctioned(tmp_path: Path) -> None:
    _write(
        tmp_path / "gateway" / "seams" / "fhir.py",
        "def _rebuild():\n    return FhirServer()\n",
    )
    result = scan_tree(tmp_path)
    assert result.ok, result.render()
    assert result.counters.get("8.1_construction_sanctioned") == 1


def test_raw_construction_outside_any_allowlist_raises(tmp_path: Path) -> None:
    _write(
        tmp_path / "agents" / "example" / "graph.py",
        "from maezo.tools.mcp_whatsapp.server import WhatsAppServer\n\n"
        "def build():\n    return WhatsAppServer()\n",
    )
    result = scan_tree(tmp_path)
    assert not result.ok
    assert any("WhatsAppServer" in v and "[8.1]" in v for v in result.violations)


def test_cibseven_server_reintroduction_raises(tmp_path: Path) -> None:
    """§8.1's assert-absent arm, exercised on a synthetic tree — never on the real one."""
    _write(
        tmp_path / "tools" / "mcp_cibseven" / "transport.py",
        "class CibSevenServer:\n    '''resurrected enforcement-point class.'''\n",
    )
    result = scan_tree(tmp_path)
    assert not result.ok
    assert any("CibSevenServer" in v and "8.1-absent" in v for v in result.violations)


def test_every_forbidden_construction_name_has_an_allowlist_entry_key() -> None:
    """Sanity: every name §8.1 fences is at least a KEY in the per-name table (possibly an empty
    frozenset, when the base allowlist alone covers it) — a name silently absent from the table
    would fall back to `frozenset()` anyway, but pinning the key set catches a typo'd name."""
    assert set(CONSTRUCTION_ALLOWLIST_BY_NAME) == FORBIDDEN_CONSTRUCTION_NAMES


# =================================================================================================
# §8.2 — hand-rolled transport
# =================================================================================================


def test_httpx_async_client_outside_allowlist_raises(tmp_path: Path) -> None:
    _write(
        tmp_path / "agents" / "example" / "client.py",
        "import httpx\n\ndef build():\n    return httpx.AsyncClient(base_url='http://x')\n",
    )
    result = scan_tree(tmp_path)
    assert not result.ok
    assert any("httpx.AsyncClient" in v and "[8.2]" in v for v in result.violations)


def test_httpx_client_inside_a_sanctioned_transport_module_is_allowed(tmp_path: Path) -> None:
    sanctioned_rel = next(iter(HTTPX_SANCTIONED_MODULES))
    _write(
        tmp_path / sanctioned_rel,
        "import httpx\n\ndef build():\n    return httpx.Client(timeout=5)\n",
    )
    result = scan_tree(tmp_path)
    assert result.ok, result.render()
    assert result.counters.get("8.2_httpx_client_sanctioned") == 1


def test_httpx_client_imported_by_name_is_also_caught(tmp_path: Path) -> None:
    """The `from httpx import AsyncClient` form, not just the `httpx.AsyncClient` attribute form."""
    _write(
        tmp_path / "agents" / "example" / "client.py",
        "from httpx import AsyncClient\n\ndef build():\n    return AsyncClient()\n",
    )
    result = scan_tree(tmp_path)
    assert not result.ok
    assert any("httpx.AsyncClient" in v for v in result.violations)


@pytest.mark.parametrize("substring", sorted(FORBIDDEN_REST_PATH_SUBSTRINGS))
def test_rest_path_literal_outside_allowlist_raises(tmp_path: Path, substring: str) -> None:
    _write(
        tmp_path / "agents" / "example" / "graph.py",
        f"async def call(client):\n    return await client.post({substring!r})\n",
    )
    result = scan_tree(tmp_path)
    assert not result.ok
    assert any(substring in v and "[8.2]" in v for v in result.violations)


def test_rest_path_literal_inside_a_sanctioned_module_is_allowed(tmp_path: Path) -> None:
    sanctioned_rel = next(iter(REST_PATH_SANCTIONED_MODULES))
    _write(
        tmp_path / sanctioned_rel,
        "async def call(client):\n    return await client.post('/messages')\n",
    )
    result = scan_tree(tmp_path)
    assert result.ok, result.render()
    assert result.counters.get("8.2_rest_path_sanctioned") == 1


def test_rest_path_literal_inside_a_docstring_is_not_a_violation(tmp_path: Path) -> None:
    """Regression: a module docstring merely MENTIONING a forbidden fragment in prose (e.g.
    describing a JSON payload shape) is not a REST-path duplication."""
    _write(
        tmp_path / "gateway" / "tool_registry.py",
        '"""Parses entry/changes/value/messages into an InboundMessage."""\n\n'
        "def build():\n    return None\n",
    )
    result = scan_tree(tmp_path)
    assert result.ok, result.render()


# =================================================================================================
# §8.3 — policy-plane imports + env reads
# =================================================================================================


def test_whole_module_import_of_mcp_whatsapp_outside_allowlist_raises(tmp_path: Path) -> None:
    _write(
        tmp_path / "agents" / "example" / "graph.py",
        "from maezo.tools.mcp_whatsapp.server import WhatsAppServer\n",
    )
    result = scan_tree(tmp_path)
    assert not result.ok
    assert any("maezo.tools.mcp_whatsapp.server" in v and "[8.3]" in v for v in result.violations)


def test_whole_module_import_inside_an_adapter_is_allowed(tmp_path: Path) -> None:
    _write(
        tmp_path / "agents" / "helena" / "adapters.py",
        "from maezo.tools.mcp_whatsapp.server import WhatsAppServer\n",
    )
    result = scan_tree(tmp_path)
    assert result.ok, result.render()
    assert result.counters.get("8.3_whole_module_import_sanctioned") == 1


def test_concrete_provider_import_outside_allowlist_raises(tmp_path: Path) -> None:
    _write(
        tmp_path / "agents" / "example" / "graph.py",
        "from maezo.tools.mcp_cibseven.transport import CibSevenHttpTransport\n",
    )
    result = scan_tree(tmp_path)
    assert not result.ok
    assert any("CibSevenHttpTransport" in v and "[8.3]" in v for v in result.violations)


def test_protocol_and_value_type_imports_from_dmn_transport_are_not_fenced(tmp_path: Path) -> None:
    """Regression for the disclosed §8.3 carve-out: every agent graph imports these for
    typing/exception-catching, and NONE of that is a policy-plane bypass."""
    _write(
        tmp_path / "agents" / "example" / "graph.py",
        "from maezo.tools.workers.dmn_transport import (\n"
        "    DmnTransport,\n"
        "    DmnEvaluationError,\n"
        "    DmnNoResultError,\n"
        "    evaluate_sync,\n"
        "    first_row,\n"
        ")\n",
    )
    result = scan_tree(tmp_path)
    assert result.ok, result.render()


def test_inference_provider_facade_import_is_not_fenced(tmp_path: Path) -> None:
    """`InferenceProvider` itself (the façade, used for typing/`cast()` in every graph) is NOT
    fenced by §8.3 — only its concrete `BaseInferenceProvider` strategy subclasses are."""
    _write(
        tmp_path / "agents" / "example" / "graph.py",
        "from maezo.runtime.inference import InferenceProvider\n\n"
        "def build(inference: InferenceProvider):\n    return inference\n",
    )
    result = scan_tree(tmp_path)
    assert result.ok, result.render()


def test_anthropic_inference_provider_import_outside_allowlist_raises(tmp_path: Path) -> None:
    _write(
        tmp_path / "agents" / "example" / "graph.py",
        "from maezo.runtime.inference import AnthropicInferenceProvider\n",
    )
    result = scan_tree(tmp_path)
    assert not result.ok
    assert any("AnthropicInferenceProvider" in v and "[8.3]" in v for v in result.violations)


@pytest.mark.parametrize("env_var", sorted(FORBIDDEN_ENV_VAR_NAMES))
def test_direct_env_read_outside_gateway_raises(tmp_path: Path, env_var: str) -> None:
    _write(
        tmp_path / "agents" / "example" / "graph.py",
        f"import os\n\ndef resolve():\n    return os.environ.get({env_var!r})\n",
    )
    result = scan_tree(tmp_path)
    assert not result.ok
    assert any(env_var in v and "[8.3]" in v for v in result.violations)


def test_symbolic_alias_env_read_outside_gateway_raises(tmp_path: Path) -> None:
    """The real usage shape: `NAME_ENV = "MAEZO_SPEC_DIR"` then `os.environ.get(NAME_ENV)` — the
    fence resolves the same-file alias, not just a bare literal."""
    _write(
        tmp_path / "agents" / "example" / "graph.py",
        "import os\n\nMAEZO_SPEC_DIR_ENV = 'MAEZO_SPEC_DIR'\n\n"
        "def resolve():\n    return os.environ.get(MAEZO_SPEC_DIR_ENV)\n",
    )
    result = scan_tree(tmp_path)
    assert not result.ok
    assert any("MAEZO_SPEC_DIR" in v and "[8.3]" in v for v in result.violations)


def test_env_read_inside_gateway_is_allowed(tmp_path: Path) -> None:
    _write(
        tmp_path / "gateway" / "action_execution.py",
        "import os\n\ndef resolve():\n    return os.environ.get('MAEZO_SPEC_DIR')\n",
    )
    result = scan_tree(tmp_path)
    assert result.ok, result.render()
    assert result.counters.get("8.3_env_read_sanctioned") == 1


def test_env_read_inside_agents_init_is_allowed(tmp_path: Path) -> None:
    _write(
        tmp_path / "agents" / "__init__.py",
        "import os\n\ndef resolve():\n    return os.environ.get('MAEZO_SPEC_DIR')\n",
    )
    result = scan_tree(tmp_path)
    assert result.ok, result.render()
    assert result.counters.get("8.3_env_read_sanctioned") == 1


@pytest.mark.parametrize("env_var", sorted(RUNTIME_MODE_ENV_VAR_NAMES))
def test_runtime_mode_env_vars_are_never_fenced_anywhere(tmp_path: Path, env_var: str) -> None:
    """B1 fact: the runtime-mode discriminator reads BOTH spellings inside the gateway — pinned
    as a non-fenced pair, not merely absent from the forbidden set by omission."""
    assert env_var not in FORBIDDEN_ENV_VAR_NAMES
    _write(
        tmp_path / "agents" / "example" / "graph.py",
        f"import os\n\ndef resolve():\n    return os.environ.get({env_var!r})\n",
    )
    result = scan_tree(tmp_path)
    assert result.ok, result.render()


# =================================================================================================
# §8.4 — test doubles reachable from a composition root
# =================================================================================================


def test_fake_import_inside_a_composition_root_raises(tmp_path: Path) -> None:
    _write(
        tmp_path / "runtime" / "agent_runtime" / "service.py",
        "from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport\n",
    )
    result = scan_tree(tmp_path)
    assert not result.ok
    assert any("FakeCibSevenTransport" in v and "[8.4]" in v for v in result.violations)


def test_noop_class_defined_inside_a_composition_root_raises_unless_declared(tmp_path: Path) -> None:
    _write(
        tmp_path / "runtime" / "agent_runtime" / "a2a_composition.py",
        "class NoopSomethingElse:\n    pass\n",
    )
    result = scan_tree(tmp_path)
    assert not result.ok
    assert any("NoopSomethingElse" in v and "[8.4]" in v for v in result.violations)


@pytest.mark.parametrize(("file_rel", "name"), sorted(DECLARED_TEST_DOUBLE_EXCEPTIONS))
def test_declared_exception_is_accepted(tmp_path: Path, file_rel: str, name: str) -> None:
    _write(tmp_path / file_rel, f"class {name}:\n    pass\n")
    result = scan_tree(tmp_path)
    assert result.ok, result.render()
    assert result.counters.get("8.4_declared_exception") == 1


@pytest.mark.parametrize(("_file_rel", "name"), sorted(DECLARED_TEST_DOUBLE_EXCEPTIONS))
def test_declared_exception_is_scoped_to_its_exact_file_never_by_pattern(
    tmp_path: Path, _file_rel: str, name: str
) -> None:
    """The SAME double name, defined in a DIFFERENT composition root, is NOT covered by the
    declared exception — proving the allowlist is a (file, name) pair, never a bare pattern."""
    other_root = "platform/webhooks/service.py"
    assert (other_root, name) not in DECLARED_TEST_DOUBLE_EXCEPTIONS
    _write(tmp_path / other_root, f"class {name}:\n    pass\n")
    result = scan_tree(tmp_path)
    assert not result.ok
    assert any(name in v and "[8.4]" in v for v in result.violations)


def test_fake_outside_any_composition_root_is_out_of_scope_for_8_4(tmp_path: Path) -> None:
    """§8.4 only governs the named composition-root module set — a `Fake*` symbol anywhere else
    (a genuine test file, an unrelated tool module) is not this rule's concern."""
    _write(
        tmp_path / "tools" / "mcp_cibseven" / "transport.py",
        "class FakeCibSevenTransport:\n    pass\n",
    )
    result = scan_tree(tmp_path)
    assert result.ok, result.render()


# =================================================================================================
# Unparseable file — fail-closed, never silently skipped
# =================================================================================================


def test_gate_fails_closed_on_unparseable_file(tmp_path: Path) -> None:
    _write(tmp_path / "broken.py", "def build(:\n    this is not valid python\n")
    result = scan_tree(tmp_path)
    assert not result.ok
    assert any("could not parse" in v for v in result.violations)


def test_unparseable_file_does_not_short_circuit_the_rest_of_the_scan(tmp_path: Path) -> None:
    _write(tmp_path / "broken.py", "def build(:\n    this is not valid python\n")
    _write(
        tmp_path / "agents" / "example" / "graph.py",
        "from maezo.tools.mcp_whatsapp.server import WhatsAppServer\n\n"
        "def build():\n    return WhatsAppServer()\n",
    )
    result = scan_tree(tmp_path)
    assert not result.ok
    assert any("could not parse" in v for v in result.violations)
    assert any("WhatsAppServer" in v and "[8.1]" in v for v in result.violations)


# =================================================================================================
# §8.5 — completeness, one synthetic-repo test per named failure mode
# =================================================================================================


def _write_yaml(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")


def _baseline_acoes() -> dict[str, dict[str, Any]]:
    """A minimal-but-COMPLETE `acoes:` block, generated from the REAL closed catalogue so it
    stays correct as the catalogue evolves — every class gets exactly one `choked: true` surface
    with a trivial reference, which is all item 3's checks need."""
    acoes: dict[str, dict[str, Any]] = {}
    for name in effect_classes.ACTION_CLASSES:
        acoes[name] = {
            "enforcement": "shadow",
            "descricao": "fixture",
            "superficies": [{"referencia": f"fixture:{name}", "detalhe": "fixture surface", "choked": True}],
            "dominios_exigidos": ["medica", "ans", "seguranca"],
            "aprovacoes": {
                "medica": {"aprovado": False, "aprovador": "PENDENTE"},
                "ans": {"aprovado": False, "aprovador": "PENDENTE"},
                "seguranca": {"aprovado": False, "aprovador": "PENDENTE"},
            },
        }
    return acoes


def _baseline_mapeamento_acoes() -> dict[str, str]:
    return {
        effect_classes.agent_action_ref(op): spec.action_class
        for op, spec in effect_classes.OPERATIONS.items()
    }


def _write_baseline_manifest(
    repo_root: Path,
    *,
    acoes: dict[str, Any] | None = None,
    mapeamento_acoes: dict[str, Any] | None = None,
    mapeamento_topicos: dict[str, Any] | None = None,
) -> None:
    manifest = {
        "status": "DRAFT",
        "modo": "shadow",
        "acoes": _baseline_acoes() if acoes is None else acoes,
        "mapeamento_acoes": _baseline_mapeamento_acoes() if mapeamento_acoes is None else mapeamento_acoes,
        "mapeamento_topicos": {} if mapeamento_topicos is None else mapeamento_topicos,
    }
    _write_yaml(repo_root / "spec" / "policies" / "autonomy" / "action-approvals.yaml", manifest)


def _write_baseline_agent_yaml(repo_root: Path, *, extra_tools: list[str] | None = None) -> None:
    tools = sorted(effect_classes.CATALOGUED_TOOL_IDS) + ["mcp-memory.read_write"] + (extra_tools or [])
    _write_yaml(
        repo_root / "spec" / "agents" / "fixture_agent" / "agent.yaml",
        {"id": "fixture_agent", "tools": tools},
    )


def _write_baseline_ladder_test(
    repo_root: Path, *, class_names: list[str] | None = None, include_test_function: bool = True
) -> None:
    names = sorted(effect_classes.ACTION_CLASSES) if class_names is None else class_names
    rows = "\n".join(f'    ("{n}", "rung", "shape"),' for n in names)
    fn = (
        "def test_every_class_carries_the_exact_rung_and_denial_shape_design_6_1_assigns():\n    pass\n"
        if include_test_function
        else ""
    )
    _write(
        repo_root / "tests" / "unit" / "gateway" / "test_effect_enforcement.py",
        f"_DESIGN_6_1_LADDER = (\n{rows}\n)\n\n\n{fn}",
    )


def _write_baseline_registry(src_dir: Path, *, omit: str | None = None) -> None:
    """A `gateway/tool_registry.py` fixture constructing every §8.1 name (base-allowlisted for
    any name), optionally omitting one — item 1's positive/negative fixture."""
    names = sorted(FORBIDDEN_CONSTRUCTION_NAMES - {omit} if omit else FORBIDDEN_CONSTRUCTION_NAMES)
    body = "\n".join(f"    {n}()" for n in names)
    _write(src_dir / "gateway" / "tool_registry.py", f"def build_everything():\n{body}\n")


def _write_full_baseline_repo(repo_root: Path, src_dir: Path) -> None:
    _write_baseline_manifest(repo_root)
    _write_baseline_agent_yaml(repo_root)
    _write_baseline_ladder_test(repo_root)
    _write_baseline_registry(src_dir)


def test_completeness_baseline_fixture_passes(tmp_path: Path) -> None:
    """The generator itself is correct: a from-scratch synthetic repo built purely from the REAL
    catalogue passes §8.5 cleanly — the control case every failure-mode test below deviates from."""
    src_dir = tmp_path / "src"
    _write_full_baseline_repo(tmp_path, src_dir)
    violations, _counters = check_completeness(src_dir, tmp_path)
    assert violations == [], violations


def test_completeness_item1_missing_construction_raises(tmp_path: Path) -> None:
    src_dir = tmp_path / "src"
    _write_baseline_manifest(tmp_path)
    _write_baseline_agent_yaml(tmp_path)
    _write_baseline_ladder_test(tmp_path)
    _write_baseline_registry(src_dir, omit="FhirServer")

    violations, _counters = check_completeness(src_dir, tmp_path)

    assert any("item 1" in v and "FhirServer" in v for v in violations), violations


def test_completeness_item1_disclosed_ans_exception_does_not_require_construction(tmp_path: Path) -> None:
    """`RealAnsGatewayTransport` is fenced but, per R-2, deliberately never constructed — omitting
    it must NOT raise an item-1 violation."""
    src_dir = tmp_path / "src"
    _write_baseline_manifest(tmp_path)
    _write_baseline_agent_yaml(tmp_path)
    _write_baseline_ladder_test(tmp_path)
    _write_baseline_registry(src_dir, omit="RealAnsGatewayTransport")

    violations, _counters = check_completeness(src_dir, tmp_path)

    assert not any("RealAnsGatewayTransport" in v for v in violations), violations


def test_completeness_item2_uncatalogued_tool_id_raises(tmp_path: Path) -> None:
    src_dir = tmp_path / "src"
    _write_baseline_manifest(tmp_path)
    _write_baseline_agent_yaml(tmp_path, extra_tools=["mcp-bogus.made_up_action"])
    _write_baseline_ladder_test(tmp_path)
    _write_baseline_registry(src_dir)

    violations, _counters = check_completeness(src_dir, tmp_path)

    assert any("item 2" in v and "mcp-bogus.made_up_action" in v for v in violations), violations


def test_completeness_item2_memory_gap_stays_disclosed_not_silently_catalogued(tmp_path: Path) -> None:
    """The ONE disclosed exception is accepted only because it is actually declared somewhere —
    proven by a NEGATIVE: if no agent declares it, the exception itself is flagged as stale."""
    src_dir = tmp_path / "src"
    tools = sorted(effect_classes.CATALOGUED_TOOL_IDS)  # mcp-memory.read_write deliberately absent
    _write_baseline_manifest(tmp_path)
    _write_yaml(
        tmp_path / "spec" / "agents" / "fixture_agent" / "agent.yaml", {"id": "fixture_agent", "tools": tools}
    )
    _write_baseline_ladder_test(tmp_path)
    _write_baseline_registry(src_dir)

    violations, _counters = check_completeness(src_dir, tmp_path)

    assert any("mcp-memory.read_write" in v and "gone stale" in v for v in violations), violations


def test_completeness_item3_class_missing_from_manifest_raises(tmp_path: Path) -> None:
    src_dir = tmp_path / "src"
    acoes = _baseline_acoes()
    dropped = next(iter(acoes))
    del acoes[dropped]
    mapeamento_acoes = {ref: cls for ref, cls in _baseline_mapeamento_acoes().items() if cls != dropped}
    _write_baseline_manifest(tmp_path, acoes=acoes, mapeamento_acoes=mapeamento_acoes)
    _write_baseline_agent_yaml(tmp_path)
    _write_baseline_ladder_test(tmp_path)
    _write_baseline_registry(src_dir)

    violations, _counters = check_completeness(src_dir, tmp_path)

    assert any("item 3" in v and dropped in v for v in violations), violations


def test_completeness_item3_extra_class_not_in_catalogue_raises(tmp_path: Path) -> None:
    src_dir = tmp_path / "src"
    acoes = _baseline_acoes()
    acoes["classe_inventada_sem_catalogo"] = acoes[next(iter(acoes))]
    _write_baseline_manifest(tmp_path, acoes=acoes)
    _write_baseline_agent_yaml(tmp_path)
    _write_baseline_ladder_test(tmp_path)
    _write_baseline_registry(src_dir)

    violations, _counters = check_completeness(src_dir, tmp_path)

    assert any("classe_inventada_sem_catalogo" in v for v in violations), violations


def test_completeness_item3_mapeamento_acoes_drift_raises(tmp_path: Path) -> None:
    src_dir = tmp_path / "src"
    mapeamento_acoes = _baseline_mapeamento_acoes()
    ref = next(iter(mapeamento_acoes))
    mapeamento_acoes[ref] = "a_completely_different_class"
    _write_baseline_manifest(tmp_path, mapeamento_acoes=mapeamento_acoes)
    _write_baseline_agent_yaml(tmp_path)
    _write_baseline_ladder_test(tmp_path)
    _write_baseline_registry(src_dir)

    violations, _counters = check_completeness(src_dir, tmp_path)

    assert any("round-trip drift" in v for v in violations), violations


def test_completeness_item3_topic_map_points_at_undeclared_class_raises(tmp_path: Path) -> None:
    src_dir = tmp_path / "src"
    _write_baseline_manifest(
        tmp_path, mapeamento_topicos={"operadora.exemplo.topico": "classe_nao_declarada"}
    )
    _write_baseline_agent_yaml(tmp_path)
    _write_baseline_ladder_test(tmp_path)
    _write_baseline_registry(src_dir)

    violations, _counters = check_completeness(src_dir, tmp_path)

    assert any("mapeamento_topicos" in v and "classe_nao_declarada" in v for v in violations), violations


def test_completeness_item3_choked_false_undocumented_raises(tmp_path: Path) -> None:
    src_dir = tmp_path / "src"
    acoes = _baseline_acoes()
    target = next(iter(acoes))
    acoes[target]["superficies"] = [{"referencia": "fixture", "detalhe": "", "choked": False}]
    _write_baseline_manifest(tmp_path, acoes=acoes)
    _write_baseline_agent_yaml(tmp_path)
    _write_baseline_ladder_test(tmp_path)
    _write_baseline_registry(src_dir)

    violations, _counters = check_completeness(src_dir, tmp_path)

    assert any("undocumented" in v and target in v for v in violations), violations


def test_completeness_item3_choked_false_documented_is_accepted(tmp_path: Path) -> None:
    """The positive half of the SAME assertion: a `choked: false` WITH a substantive reason is
    accepted, never flagged — the fence distinguishes disclosed from silent."""
    src_dir = tmp_path / "src"
    acoes = _baseline_acoes()
    target = next(iter(acoes))
    acoes[target]["superficies"] = [
        {
            "referencia": "fixture",
            "detalhe": "PORT-PENDING: the concrete client does not exist yet, so nothing can be observed.",
            "choked": False,
        }
    ]
    _write_baseline_manifest(tmp_path, acoes=acoes)
    _write_baseline_agent_yaml(tmp_path)
    _write_baseline_ladder_test(tmp_path)
    _write_baseline_registry(src_dir)

    violations, counters = check_completeness(src_dir, tmp_path)

    assert not any("undocumented" in v for v in violations), violations
    assert counters["8.5_item3_choked_false_documented"] == 1
    assert counters["8.5_item3_choked_false_undocumented"] == 0


def test_completeness_item3_non_boolean_choked_raises(tmp_path: Path) -> None:
    src_dir = tmp_path / "src"
    acoes = _baseline_acoes()
    target = next(iter(acoes))
    acoes[target]["superficies"] = [{"referencia": "fixture", "detalhe": "x", "choked": "true"}]
    _write_baseline_manifest(tmp_path, acoes=acoes)
    _write_baseline_agent_yaml(tmp_path)
    _write_baseline_ladder_test(tmp_path)
    _write_baseline_registry(src_dir)

    violations, _counters = check_completeness(src_dir, tmp_path)

    assert any("non-boolean choked" in v for v in violations), violations


def test_completeness_item4_missing_ladder_file_raises(tmp_path: Path) -> None:
    src_dir = tmp_path / "src"
    _write_baseline_manifest(tmp_path)
    _write_baseline_agent_yaml(tmp_path)
    _write_baseline_registry(src_dir)
    # deliberately do NOT write the ladder test file

    violations, _counters = check_completeness(src_dir, tmp_path)

    assert any("item 4" in v and "ladder test file missing" in v for v in violations), violations


def test_completeness_item4_ladder_missing_a_class_raises(tmp_path: Path) -> None:
    src_dir = tmp_path / "src"
    names = sorted(effect_classes.ACTION_CLASSES)
    dropped = names.pop()
    _write_baseline_manifest(tmp_path)
    _write_baseline_agent_yaml(tmp_path)
    _write_baseline_ladder_test(tmp_path, class_names=names)
    _write_baseline_registry(src_dir)

    violations, _counters = check_completeness(src_dir, tmp_path)

    assert any("item 4" in v and dropped in v and "no row" in v for v in violations), violations


def test_completeness_item4_ladder_has_a_stale_extra_row_raises(tmp_path: Path) -> None:
    src_dir = tmp_path / "src"
    names = [*sorted(effect_classes.ACTION_CLASSES), "classe_fantasma"]
    _write_baseline_manifest(tmp_path)
    _write_baseline_agent_yaml(tmp_path)
    _write_baseline_ladder_test(tmp_path, class_names=names)
    _write_baseline_registry(src_dir)

    violations, _counters = check_completeness(src_dir, tmp_path)

    assert any("item 4" in v and "classe_fantasma" in v and "stale" in v for v in violations), violations


def test_completeness_item4_missing_test_function_raises(tmp_path: Path) -> None:
    src_dir = tmp_path / "src"
    _write_baseline_manifest(tmp_path)
    _write_baseline_agent_yaml(tmp_path)
    _write_baseline_ladder_test(tmp_path, include_test_function=False)
    _write_baseline_registry(src_dir)

    violations, _counters = check_completeness(src_dir, tmp_path)

    assert any("item 4" in v and "no longer defines" in v for v in violations), violations
