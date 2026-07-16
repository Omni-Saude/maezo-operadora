"""Unit tests for maezo.platform.validation.cli — CI validation gate.

`validate_artifacts()` is a real, fail-closed gate (T2.1): every test in
`TestValidateArtifactsFailClosed` and `TestMutation*` below asserts a
non-zero exit for a genuine problem — there is no test left in this file
that blesses the old always-pass/warn-and-continue behavior (defect B2,
worsened by e1c0b34). `TestHappyPathOnRealSpecTree` proves the real spec/
tree validates clean end to end.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from maezo.platform.validation.cli import (
    build_parser,
    main,
    validate_artifacts,
    validate_signoff,
)

REPO_ROOT = Path(__file__).resolve().parents[3]

# ---------------------------------------------------------------------------
# CLI parser
# ---------------------------------------------------------------------------


class TestCliParser:
    """Argument parsing tests."""

    def test_help_flag(self) -> None:
        """--help produces help text."""
        parser = build_parser()
        with pytest.raises(SystemExit) as exc_info:
            parser.parse_args(["--help"])
        assert exc_info.value.code == 0

    def test_validate_subcommand(self) -> None:
        """validate subcommand accepts paths."""
        parser = build_parser()
        args = parser.parse_args(["validate", "/some/path", "/other/path"])
        assert args.command == "validate"
        assert args.paths == ["/some/path", "/other/path"]

    def test_signoff_subcommand(self) -> None:
        """signoff subcommand with --strict."""
        parser = build_parser()
        args = parser.parse_args(["signoff", "--strict"])
        assert args.command == "signoff"
        assert args.strict is True

    def test_no_subcommand_fails(self) -> None:
        """Missing subcommand raises SystemExit."""
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args([])


# ---------------------------------------------------------------------------
# happy path — the real spec/ tree
# ---------------------------------------------------------------------------


class TestHappyPathOnRealSpecTree:
    """The actual committed spec/ tree must validate clean, end to end."""

    def test_real_spec_tree_passes(self) -> None:
        result = validate_artifacts(
            [
                str(REPO_ROOT / "spec" / "processes"),
                str(REPO_ROOT / "spec" / "policies"),
                str(REPO_ROOT / "spec" / "agents"),
            ]
        )
        assert result == 0

    def test_main_validate_real_tree_returns_zero(self) -> None:
        result = main(
            [
                "validate",
                str(REPO_ROOT / "spec" / "processes"),
                str(REPO_ROOT / "spec" / "policies"),
                str(REPO_ROOT / "spec" / "agents"),
            ]
        )
        assert result == 0


# ---------------------------------------------------------------------------
# validate_artifacts — fail-closed on ambiguous/missing/malformed input
# ---------------------------------------------------------------------------


class TestValidateArtifactsFailClosed:
    """Every one of these used to return 0 under the old greenfield stub."""

    def test_nonexistent_path_fails(self) -> None:
        assert validate_artifacts(["/nonexistent/path/12345"]) != 0

    def test_existing_empty_directory_fails(self, tmp_path: Path) -> None:
        """An empty directory matches none of the recognized artifact-root
        shapes (no bpmn/dmn subdir, no autonomy subdir, no per-item
        agent.yaml) — fail-closed: cannot validate what cannot be classified."""
        assert validate_artifacts([str(tmp_path)]) != 0

    def test_path_is_file_not_directory_fails(self, tmp_path: Path) -> None:
        f = tmp_path / "afile.txt"
        f.write_text("x")
        assert validate_artifacts([str(f)]) != 0

    def test_empty_paths_list_fails(self) -> None:
        assert validate_artifacts([]) != 0

    def test_multiple_paths_one_bad_still_fails(self) -> None:
        result = validate_artifacts([str(REPO_ROOT / "spec" / "processes"), "/nonexistent/path/12345"])
        assert result != 0

    def test_main_validate_nonexistent_path_fails(self) -> None:
        result = main(["validate", "/nonexistent/path/12345"])
        assert result != 0


# ---------------------------------------------------------------------------
# mutation tests — corrupt a real, working spec/processes/ copy
# ---------------------------------------------------------------------------


@pytest.fixture
def processes_copy(tmp_path: Path) -> Path:
    """A throwaway copy of the real spec/processes/ tree, safe to mutate."""
    src = REPO_ROOT / "spec" / "processes"
    dst = tmp_path / "processes"
    shutil.copytree(src, dst)
    return dst


@pytest.fixture
def policies_copy(tmp_path: Path) -> Path:
    """A throwaway copy of the real spec/policies/ tree, safe to mutate."""
    src = REPO_ROOT / "spec" / "policies"
    dst = tmp_path / "policies"
    shutil.copytree(src, dst)
    return dst


_AUTH_BPMN = "SP-OP-AUTH-001_Autorizacao_Previa.bpmn"
_AUTH_ADMISSIBILITY_DMN = "auth_admissibility.dmn"


class TestMutationCorruptDmnReference:
    """Acceptance criterion: corrupt a DMN reference -> validator exits nonzero."""

    def test_corrupted_decision_ref_fails(self, processes_copy: Path) -> None:
        bpmn_file = processes_copy / "bpmn" / _AUTH_BPMN
        text = bpmn_file.read_text()
        assert 'camunda:decisionRef="auth_admissibility"' in text
        bpmn_file.write_text(
            text.replace(
                'camunda:decisionRef="auth_admissibility"',
                'camunda:decisionRef="auth_admissibility_CORRUPTED"',
            )
        )
        result = validate_artifacts([str(processes_copy)])
        assert result != 0


class TestMutationMissingArtifact:
    """Acceptance criterion: missing artifact -> validator exits nonzero."""

    def test_deleted_dmn_file_leaves_broken_reference(self, processes_copy: Path) -> None:
        dmn_file = processes_copy / "dmn" / _AUTH_ADMISSIBILITY_DMN
        assert dmn_file.exists()
        dmn_file.unlink()
        result = validate_artifacts([str(processes_copy)])
        assert result != 0

    def test_deleted_bpmn_directory_fails(self, processes_copy: Path) -> None:
        shutil.rmtree(processes_copy / "bpmn")
        result = validate_artifacts([str(processes_copy)])
        assert result != 0

    def test_missing_top_level_path_fails(self) -> None:
        assert validate_artifacts(["/nonexistent/path/12345"]) != 0


class TestMutationUnparseableXml:
    """Acceptance criterion: unparseable XML -> validator exits nonzero."""

    def test_truncated_bpmn_xml_fails(self, processes_copy: Path) -> None:
        bpmn_file = processes_copy / "bpmn" / _AUTH_BPMN
        bpmn_file.write_text("<bpmn:definitions><not-closed>")
        result = validate_artifacts([str(processes_copy)])
        assert result != 0

    def test_truncated_dmn_xml_fails(self, processes_copy: Path) -> None:
        dmn_file = processes_copy / "dmn" / _AUTH_ADMISSIBILITY_DMN
        dmn_file.write_text("<definitions><not-closed>")
        result = validate_artifacts([str(processes_copy)])
        assert result != 0


class TestMutationUnparseableYaml:
    """Acceptance criterion: unparseable YAML -> validator exits nonzero."""

    def test_malformed_core_policy_yaml_fails(self, policies_copy: Path) -> None:
        core_file = policies_copy / "autonomy" / "L0-core.yaml"
        core_file.write_text("version: 1\nactions: [this is not\n  valid: :: yaml at all")
        result = validate_artifacts([str(policies_copy)])
        assert result != 0

    def test_malformed_agent_yaml_fails(self, tmp_path: Path) -> None:
        src = REPO_ROOT / "spec" / "agents"
        dst = tmp_path / "agents"
        shutil.copytree(src, dst)
        (dst / "andre" / "agent.yaml").write_text("id: andre\nname: [unterminated\n")
        result = validate_artifacts([str(dst)])
        assert result != 0


# ---------------------------------------------------------------------------
# validate_signoff — deliberately NOT a real gate yet (T2.2)
# ---------------------------------------------------------------------------


class TestValidateSignoffStub:
    """T2.2 owns making this real; here we only prove it can't masquerade as one."""

    def test_signoff_returns_zero_but_says_so_loudly(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert validate_signoff() == 0
        captured = capsys.readouterr()
        assert "NOT YET IMPLEMENTED" in captured.err

    def test_signoff_strict_flag_accepted(self) -> None:
        assert validate_signoff(strict=True) == 0
        assert validate_signoff(strict=False) == 0

    def test_main_signoff(self) -> None:
        assert main(["signoff"]) == 0

    def test_main_signoff_strict(self) -> None:
        assert main(["signoff", "--strict"]) == 0
