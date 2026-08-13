"""Unit tests for maezo.agents package initialization — AgentLoader + AgentRegistry.

TDD London School: tests written BEFORE implementation.
"""

from pathlib import Path

import pytest

from maezo.agents import (
    MAEZO_SPEC_DIR_ENV,
    AgentLoader,
    AgentRegistry,
    SpecDirOverrideRefusedError,
    is_explicit_local_runtime,
    resolve_spec_agents_dir,
    resolve_spec_dir,
)

#: Repo root, computed independently of maezo.agents._default_spec_dir_candidates()
#: so these tests don't tautologically validate against the same math.
_REPO_ROOT = Path(__file__).parent.parent.parent.parent

#: The two runtime-mode variable names, HARDCODED rather than imported from
#: `maezo.agents.RUNTIME_MODE_ENVS` (provenance: `src/maezo/agents/__init__.py`, the constant this
#: file's Q-6 tests exist to pin). A matrix derived from the value under test SHRINKS silently
#: when someone shortens that value and still reports green — the exact defect the Onda-1 B3
#: gatekeeper caught three times in one train.
_RUNTIME_MODE_ENV_NAMES = ("RUNTIME_MODE", "AGENT_RUNTIME_MODE")

#: The literal an operator must declare to keep `MAEZO_SPEC_DIR` working (owner's ratification,
#: 2026-08-12: "permitindo-o apenas em runtime local explícito").
_LOCAL = "local"


def _declare_runtime(monkeypatch: pytest.MonkeyPatch, **modes: str | None) -> None:
    """Set/clear the two runtime-mode variables. `None` means UNDECLARED (the production default)."""
    for name in _RUNTIME_MODE_ENV_NAMES:
        value = modes.get(name)
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)


@pytest.fixture(autouse=True)
def _explicit_local_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    """Declare this test process EXPLICITLY local (Wave-1 Q-6).

    Since the Q-6 ratification an ABSENT runtime mode is PRODUCTION, and production REFUSES
    `MAEZO_SPEC_DIR`. Every test below that exercises the override therefore has to say what a dev
    box has to say. The Q-6 tests themselves override this fixture per-case — that is the point of
    setting it here rather than inline: the refusal cases must DECLARE production, so a future
    edit that accidentally drops the declaration turns them red rather than vacuous.
    """
    _declare_runtime(monkeypatch, RUNTIME_MODE=_LOCAL, AGENT_RUNTIME_MODE=_LOCAL)


class TestAgentLoader:
    """Tests for AgentLoader — loads agent.yaml → AgentDefinition."""

    def test_load_agent_definition_from_yaml(self, tmp_path: Path) -> None:
        """AgentLoader should parse a minimal agent.yaml into an AgentDefinition."""
        yaml_content = """\
id: test-agent
name: "Test Agent"
role: "Testing framework"
phase: 0
autonomy_level: L3
process_keys:
  - SP-OP-TEST-001
tools:
  - mcp-dmn.evaluate
  - mcp-memory.read_write
"""
        agent_dir = tmp_path / "test-agent"
        agent_dir.mkdir()
        yaml_path = agent_dir / "agent.yaml"
        yaml_path.write_text(yaml_content)

        loader = AgentLoader()
        definition = loader.load(yaml_path)

        assert definition.id == "test-agent"
        assert definition.name == "Test Agent"
        assert definition.role == "Testing framework"
        assert definition.phase == 0
        assert definition.autonomy_level == "L3"
        assert definition.process_keys == ["SP-OP-TEST-001"]
        assert definition.tools == ["mcp-dmn.evaluate", "mcp-memory.read_write"]

    def test_load_agent_definition_from_dict(self) -> None:
        """AgentLoader should accept a dict directly (for programmatic loading)."""
        data = {
            "id": "dict-agent",
            "name": "Dict Agent",
            "role": "Dict role",
            "phase": 1,
            "autonomy_level": "L2",
            "process_keys": ["SP-OP-DICT-001"],
            "tools": ["mcp-test.tool"],
        }

        loader = AgentLoader()
        definition = loader.load_from_dict(data)

        assert definition.id == "dict-agent"
        assert definition.autonomy_level == "L2"

    def test_load_agent_definition_missing_required_raises(self, tmp_path: Path) -> None:
        """AgentLoader should raise ValueError when required fields are missing."""
        yaml_content = """\
id: incomplete-agent
# name is missing
"""
        agent_dir = tmp_path / "incomplete-agent"
        agent_dir.mkdir()
        yaml_path = agent_dir / "agent.yaml"
        yaml_path.write_text(yaml_content)

        loader = AgentLoader()
        with pytest.raises((ValueError, TypeError)):
            loader.load(yaml_path)


class TestResolveSpecDir:
    """Tests for resolve_spec_dir()/resolve_spec_agents_dir() (T0.3/B14 single source of truth)."""

    def test_default_resolves_to_repo_spec_directory(self) -> None:
        """With no MAEZO_SPEC_DIR override, resolution finds <repo_root>/spec."""
        assert resolve_spec_dir() == _REPO_ROOT / "spec"

    def test_default_spec_agents_dir_contains_known_agents(self) -> None:
        """resolve_spec_agents_dir() must resolve to the real spec/agents/ directory."""
        agents_dir = resolve_spec_agents_dir()
        assert agents_dir == _REPO_ROOT / "spec" / "agents"
        assert (agents_dir / "rafael" / "agent.yaml").exists()
        assert (agents_dir / "helena" / "agent.yaml").exists()

    def test_env_override_is_honored(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """MAEZO_SPEC_DIR, when set, takes priority over the package-relative default."""
        monkeypatch.setenv(MAEZO_SPEC_DIR_ENV, str(tmp_path))
        assert resolve_spec_dir() == tmp_path.resolve()

    def test_env_override_missing_directory_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Fail-closed: a MAEZO_SPEC_DIR pointing nowhere must raise, never fall back silently."""
        bogus = tmp_path / "does-not-exist"
        monkeypatch.setenv(MAEZO_SPEC_DIR_ENV, str(bogus))

        with pytest.raises(FileNotFoundError, match=MAEZO_SPEC_DIR_ENV):
            resolve_spec_dir()

    def test_spec_agents_missing_raises(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Fail-closed: a resolved spec/ dir without an agents/ subdirectory must raise."""
        monkeypatch.setenv(MAEZO_SPEC_DIR_ENV, str(tmp_path))

        with pytest.raises(FileNotFoundError, match="agents"):
            resolve_spec_agents_dir()

    def test_installed_wheel_package_adjacent_fallback(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """T1.8 REVISE-1: in an installed wheel (no repo layout), resolution falls back to
        the package-adjacent `<site-packages>/maezo/spec` laid down by the hatch
        force-include — without requiring MAEZO_SPEC_DIR."""
        import maezo.agents as agents_pkg

        site = tmp_path / "venv" / "lib" / "python3.12" / "site-packages"
        fake_init = site / "maezo" / "agents" / "__init__.py"
        fake_init.parent.mkdir(parents=True)
        fake_init.write_text("# simulated installed module file\n")
        pkg_spec = site / "maezo" / "spec"
        (pkg_spec / "policies" / "autonomy").mkdir(parents=True)

        monkeypatch.delenv(MAEZO_SPEC_DIR_ENV, raising=False)
        monkeypatch.setattr(agents_pkg, "__file__", str(fake_init))

        # repo-layout candidate (parents[3]/spec = .../python3.12/spec) does not
        # exist here, so the package-adjacent fallback must win.
        assert resolve_spec_dir() == pkg_spec

    def test_repo_layout_wins_over_package_adjacent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Candidate order is pinned: the repo checkout layout takes precedence over the
        installed-wheel package-adjacent fallback when both exist."""
        import maezo.agents as agents_pkg

        fake_init = tmp_path / "src" / "maezo" / "agents" / "__init__.py"
        fake_init.parent.mkdir(parents=True)
        fake_init.write_text("# simulated checkout module file\n")
        repo_spec = tmp_path / "spec"
        repo_spec.mkdir()
        pkg_spec = tmp_path / "src" / "maezo" / "spec"
        pkg_spec.mkdir(parents=True)

        monkeypatch.delenv(MAEZO_SPEC_DIR_ENV, raising=False)
        monkeypatch.setattr(agents_pkg, "__file__", str(fake_init))

        assert resolve_spec_dir() == repo_spec

    def test_no_candidate_raises_fail_closed(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Fail-closed: with no env override and NO existing default candidate (neither
        repo layout nor package-adjacent), resolution must raise — never silently
        accept a nonexistent path."""
        import maezo.agents as agents_pkg

        fake_init = tmp_path / "a" / "b" / "c" / "d" / "__init__.py"
        fake_init.parent.mkdir(parents=True)
        fake_init.write_text("# simulated module file with no spec/ anywhere\n")

        monkeypatch.delenv(MAEZO_SPEC_DIR_ENV, raising=False)
        monkeypatch.setattr(agents_pkg, "__file__", str(fake_init))

        with pytest.raises(FileNotFoundError, match=MAEZO_SPEC_DIR_ENV):
            resolve_spec_dir()


class TestSpecDirRefusedOutsideExplicitLocalRuntime:
    """WAVE-1 Q-6, ratified fail-closed by the owner 2026-08-12 (PLANS.md §0.8).

    Verbatim intent: *"produção deve recusar `MAEZO_SPEC_DIR`, permitindo-o apenas em runtime
    local explícito; remova o companion bypass e aceite futuras exceções somente por bundle
    imutável com digest permitido."*

    `resolve_spec_dir()` is the ONE mechanism every policy loader routes through, so this variable
    substitutes the ENTIRE policy plane — the autonomy matrix, the MZO-040 approval manifest, the
    L-0 ceilings, the frozen hard-action list and every `agent.yaml` — in one string, invisibly to
    the `MAEZO_ACTION_APPROVALS_PATH` override fence (adversary A-6,
    `docs/design/wave1-effect-chokepoint.md` §5.7 / C-B4).

    Every expectation below is HARDCODED against that ratification, never derived from the module
    under test.
    """

    def test_production_runtime_with_the_variable_set_refuses_by_name(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The headline: a production runtime REFUSES, and the refusal is a NAMED type."""
        _declare_runtime(monkeypatch, AGENT_RUNTIME_MODE="kubernetes")
        monkeypatch.setenv(MAEZO_SPEC_DIR_ENV, str(tmp_path))

        with pytest.raises(SpecDirOverrideRefusedError):
            resolve_spec_dir()

    def test_an_absent_runtime_mode_is_production_and_refuses(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """THE FAIL-CLOSED FLIP, and the single most load-bearing row here.

        The WEAK pin this replaces read an unset variable as LOCAL, so a production pod that
        forgot to inject a mode variable left the fence DISARMED while running an entirely
        substituted policy plane. Absent is now PRODUCTION — the same direction ADR-0039 Q7 /
        Train F flipped `AgentRuntimeSettings.agent_runtime_mode` and
        `worker_runtime_mode_from_env()`. Forgetting to configure the mode can no longer be the
        thing that unlocks the override.
        """
        _declare_runtime(monkeypatch)  # BOTH names undeclared
        monkeypatch.setenv(MAEZO_SPEC_DIR_ENV, str(tmp_path))

        with pytest.raises(SpecDirOverrideRefusedError):
            resolve_spec_dir()

    def test_explicit_local_runtime_still_honors_the_override(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """ "...permitindo-o apenas em runtime local explícito": the dev-box path is UNCHANGED."""
        _declare_runtime(monkeypatch, RUNTIME_MODE=_LOCAL)
        monkeypatch.setenv(MAEZO_SPEC_DIR_ENV, str(tmp_path))

        assert resolve_spec_dir() == tmp_path.resolve()

    def test_production_without_the_variable_resolves_the_repo_spec_normally(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """THE NON-REGRESSION CONTROL. Q-6 refuses an OVERRIDE, it does not refuse production.

        Without this row the whole suite would still pass if `resolve_spec_dir()` simply raised
        whenever the runtime is production — which would take every deployed daemon down.
        """
        _declare_runtime(monkeypatch, AGENT_RUNTIME_MODE="kubernetes")
        monkeypatch.delenv(MAEZO_SPEC_DIR_ENV, raising=False)

        assert resolve_spec_dir() == _REPO_ROOT / "spec"

    def test_the_refusal_message_names_the_variable_the_mode_and_the_sanctioned_alternative(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The 3am-legibility contract, mirroring `_require_envelope_signing_or_fail_closed`.

        Key phrases hardcoded, not derived: an operator must be able to act on this line without
        opening the design doc, and a future edit that "simplifies" the message into
        `RuntimeError('bad spec dir')` has to turn this red.
        """
        _declare_runtime(monkeypatch, AGENT_RUNTIME_MODE="kubernetes")
        monkeypatch.setenv(MAEZO_SPEC_DIR_ENV, str(tmp_path))

        with pytest.raises(SpecDirOverrideRefusedError) as excinfo:
            resolve_spec_dir()
        message = str(excinfo.value)

        # The variable, by name — the single most important token in the line.
        assert "refusing to resolve the policy plane through MAEZO_SPEC_DIR" in message
        # The mode that produced the verdict, and where the "absent means production" rule is from.
        assert "'AGENT_RUNTIME_MODE': 'kubernetes'" in message
        assert "an ABSENT or blank mode is PRODUCTION" in message
        # The rationale, with its citation.
        assert "docs/design/wave1-effect-chokepoint.md" in message
        assert "Wave-1 Q-6" in message
        # The sanctioned alternatives: the explicit operator flag and the explicit local runtime.
        assert "--spec-dir" in message
        assert "RUNTIME_MODE or AGENT_RUNTIME_MODE=local" in message
        # The future-exception design, recorded and explicitly NOT BUILT (owner's words).
        assert "NO environment escape hatch" in message
        assert (
            "IMMUTABLE bundle with an allowlisted content digest, and that mechanism is NOT BUILT" in message
        )

    def test_the_refusal_precedes_the_directory_existence_check(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """ORDER IS PART OF THE CONTRACT: the refusal is about the variable being SET.

        A `MAEZO_SPEC_DIR` pointing nowhere must still report the GOVERNANCE refusal, not a
        `FileNotFoundError` — otherwise an operator reads "no such directory", fixes the path, and
        discovers the real answer only on the next deploy. `SpecDirOverrideRefusedError` is a
        `RuntimeError`, NOT a `FileNotFoundError`, so this assertion is not vacuous.
        """
        _declare_runtime(monkeypatch, AGENT_RUNTIME_MODE="kubernetes")
        monkeypatch.setenv(MAEZO_SPEC_DIR_ENV, str(tmp_path / "does-not-exist"))

        with pytest.raises(SpecDirOverrideRefusedError):
            resolve_spec_dir()
        assert not issubclass(SpecDirOverrideRefusedError, FileNotFoundError)

    def test_resolve_spec_agents_dir_inherits_the_refusal(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An `agent.yaml` is a policy artefact too (it carries `tools:` and `autonomy_level`)."""
        _declare_runtime(monkeypatch, AGENT_RUNTIME_MODE="kubernetes")
        (tmp_path / "agents").mkdir()
        monkeypatch.setenv(MAEZO_SPEC_DIR_ENV, str(tmp_path))

        with pytest.raises(SpecDirOverrideRefusedError):
            resolve_spec_agents_dir()

    def test_no_environment_variable_lifts_the_refusal(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """THE COMPANION-BYPASS REMOVAL, stated as a behaviour.

        The WEAK form of this pin (`action_execution._parse`, removed by this change) was lifted
        by `MAEZO_ACTION_APPROVALS_ALLOW_OVERRIDE_ENFORCEMENT=1` — one more variable re-opened a
        total policy-plane substitution. The owner removed that by name. This row sets that flag
        AND the other plausible escape-hatch spellings a future author might reach for, and proves
        none of them changes the verdict.
        """
        _declare_runtime(monkeypatch, AGENT_RUNTIME_MODE="kubernetes")
        monkeypatch.setenv(MAEZO_SPEC_DIR_ENV, str(tmp_path))
        for candidate_hatch in (
            "MAEZO_ACTION_APPROVALS_ALLOW_OVERRIDE_ENFORCEMENT",
            "MAEZO_ALLOW_SPEC_DIR_OVERRIDE",
            "MAEZO_SPEC_DIR_ALLOW_OVERRIDE",
            "MAEZO_ALLOW_UNSAFE_SPEC_DIR",
        ):
            monkeypatch.setenv(candidate_hatch, "1")

        with pytest.raises(SpecDirOverrideRefusedError):
            resolve_spec_dir()

    @pytest.mark.parametrize(
        ("runtime_mode", "agent_runtime_mode", "explicit_local"),
        [
            # ---- EXPLICIT LOCAL: the override survives ------------------------------------
            (_LOCAL, None, True),
            (None, _LOCAL, True),
            (_LOCAL, _LOCAL, True),
            # ---- THE FAIL-CLOSED FLIP: nothing declared is PRODUCTION ---------------------
            # The weak pin read this row as local. It is the reason Q-6 exists.
            (None, None, False),
            # ---- A DISJUNCTION, never first-set-wins --------------------------------------
            # A pod inheriting a base-image `RUNTIME_MODE=local` and then labelled by Helm.
            # First-set-wins reads `local` here and hands over the whole policy plane.
            (_LOCAL, "kubernetes", False),
            ("kubernetes", _LOCAL, False),
            ("kubernetes", "kubernetes", False),
            ("kubernetes", None, False),
            (None, "kubernetes", False),
            # ---- BLANK IS UNDECLARED, hence production ------------------------------------
            # Matches `key_scrubber`'s `or` chain and `worker_runtime_mode_from_env`'s
            # strip-to-"production". A blank variable is not a declaration of anything.
            ("", None, False),
            ("", "", False),
            ("", "kubernetes", False),
            # A blank alongside a REAL local declaration is still explicit local: the blank is
            # invisible, so the only thing declared says local.
            ("", _LOCAL, True),
            # ---- NO NORMALISATION: a mistyped mode is PRODUCTION --------------------------
            # Tighter than `key_scrubber`, which `.strip().lower()`s. Guessing which mode an
            # operator meant is exactly how a fail-closed pin becomes fail-open.
            (" local ", None, False),
            ("Local", None, False),
            (None, "LOCAL", False),
            ("local ", None, False),
        ],
    )
    def test_the_explicit_local_truth_table(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        runtime_mode: str | None,
        agent_runtime_mode: str | None,
        explicit_local: bool,
    ) -> None:
        """The discriminator, pinned row by row — and pinned through OBSERVABLE behaviour.

        Each row asserts BOTH the predicate and what `resolve_spec_dir()` actually does with it,
        so a future change that keeps `is_explicit_local_runtime()` honest while wiring it into
        the resolver backwards cannot pass.
        """
        _declare_runtime(monkeypatch, RUNTIME_MODE=runtime_mode, AGENT_RUNTIME_MODE=agent_runtime_mode)
        monkeypatch.setenv(MAEZO_SPEC_DIR_ENV, str(tmp_path))

        assert is_explicit_local_runtime() is explicit_local, (
            f"RUNTIME_MODE={runtime_mode!r} AGENT_RUNTIME_MODE={agent_runtime_mode!r}"
        )
        if explicit_local:
            assert resolve_spec_dir() == tmp_path.resolve()
        else:
            with pytest.raises(SpecDirOverrideRefusedError):
                resolve_spec_dir()

    def test_the_discriminator_agrees_with_the_shared_runtime_gate_on_every_declared_mode(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """NO SECOND ANSWER TO "IS THIS PRODUCTION?".

        `maezo.agents` cannot import `a2a_composition.is_production_runtime_mode` (that package
        imports this one — a cycle), so the discriminator is RESTATED here, exactly as
        `action_execution` used to restate it. A restatement without a pin is how two gates drift
        into disagreeing, so this test imports BOTH and requires the same verdict on every
        declared mode. It is deliberately silent about the UNDECLARED case: the two answer
        different questions there (`is_production_runtime_mode` takes a mode string that a
        fail-closed resolver has already defaulted to "production"; this one reads the environment
        itself), and they agree on the VERDICT — production — which the row above pins directly.
        """
        from maezo.runtime.agent_runtime.a2a_composition import is_production_runtime_mode

        for mode in (_LOCAL, "kubernetes", "production", "", " local ", "Local", "LOCAL", "staging"):
            _declare_runtime(monkeypatch, RUNTIME_MODE=mode)
            assert is_explicit_local_runtime() is not is_production_runtime_mode(mode), (
                f"the two prod/dev discriminators disagree on {mode!r}"
            )


class TestAgentLoaderLoadById:
    """Tests for AgentLoader.load_by_id() — reads spec/agents/<id>/agent.yaml (T0.3/B14)."""

    def test_load_by_id_reads_real_agent_from_spec_agents(self) -> None:
        """load_by_id resolves spec/agents/ by default and loads a real agent."""
        loader = AgentLoader()
        definition = loader.load_by_id("rafael")

        assert definition.id == "rafael"
        assert definition.name == "Rafael Nogueira"

    def test_load_by_id_missing_agent_raises(self) -> None:
        """Fail-closed: an unknown agent id must raise, never return a default/empty definition."""
        loader = AgentLoader()

        with pytest.raises(FileNotFoundError):
            loader.load_by_id("this-agent-does-not-exist")

    def test_load_by_id_honors_explicit_spec_agents_dir_override(self, tmp_path: Path) -> None:
        """An explicit spec_agents_dir argument overrides the resolved default."""
        agent_dir = tmp_path / "custom-agent"
        agent_dir.mkdir()
        (agent_dir / "agent.yaml").write_text('id: custom-agent\nname: "Custom Agent"\nrole: "Custom role"\n')

        loader = AgentLoader()
        definition = loader.load_by_id("custom-agent", spec_agents_dir=tmp_path)

        assert definition.id == "custom-agent"
        assert definition.name == "Custom Agent"


class TestAgentRegistry:
    """Tests for AgentRegistry — registers agents by id."""

    def test_register_agent(self) -> None:
        """AgentRegistry.register should store and retrieve an AgentDefinition by id."""
        from maezo.agents import AgentDefinition

        registry = AgentRegistry()
        definition = AgentDefinition(
            id="rafael",
            name="Rafael Nogueira",
            role="Analista de Autorizacao",
            phase=1,
            autonomy_level="L2",
            process_keys=["SP-OP-AUTH-001"],
            tools=["mcp-dmn.evaluate"],
        )

        registry.register(definition)

        retrieved = registry.get("rafael")
        assert retrieved is not None
        assert retrieved.id == "rafael"
        assert retrieved.name == "Rafael Nogueira"

    def test_register_duplicate_raises(self) -> None:
        """AgentRegistry.register should raise ValueError on duplicate agent id."""
        from maezo.agents import AgentDefinition

        registry = AgentRegistry()
        definition = AgentDefinition(
            id="helena",
            name="Helena Moreira",
            role="Health Navigator",
            phase=0,
            autonomy_level="L3",
            process_keys=["SP-OP-ESCALATION-001"],
            tools=["mcp-whatsapp.send_message"],
        )

        registry.register(definition)

        with pytest.raises(ValueError, match="already registered"):
            registry.register(definition)

    def test_get_nonexistent_returns_none(self) -> None:
        """AgentRegistry.get should return None for unregistered agents."""
        registry = AgentRegistry()

        result = registry.get("nonexistent")
        assert result is None

    def test_list_agents(self) -> None:
        """AgentRegistry.list should return all registered agent ids."""
        from maezo.agents import AgentDefinition

        registry = AgentRegistry()
        registry.register(
            AgentDefinition(
                id="agent-1",
                name="One",
                role="test",
                phase=0,
                autonomy_level="L3",
                process_keys=[],
                tools=[],
            )
        )
        registry.register(
            AgentDefinition(
                id="agent-2",
                name="Two",
                role="test",
                phase=1,
                autonomy_level="L2",
                process_keys=[],
                tools=[],
            )
        )

        ids = registry.list_ids()
        assert set(ids) == {"agent-1", "agent-2"}
