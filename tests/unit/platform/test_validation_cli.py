"""Unit tests for maezo.platform.validation.cli — CI validation gate.

TDD London School: tests exercise the CLI argument parsing and
the validate/signoff entry points without real filesystem artifacts.
"""

import pytest

from maezo.platform.validation.cli import (
    build_parser,
    main,
    validate_artifacts,
    validate_signoff,
)

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
# main entry point
# ---------------------------------------------------------------------------


class TestMain:
    """main() CLI entry point tests."""

    def test_main_validate_empty_paths(self) -> None:
        """main validate with non-existent paths returns 1."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            result = main(["validate", tmpdir])
            # Empty directory is SKIP, not error
            assert result == 0

    def test_main_validate_nonexistent_path(self) -> None:
        """main validate with non-existent path returns 0 (warn, not error — greenfield)."""
        result = main(["validate", "/nonexistent/path/12345"])
        assert result == 0

    def test_main_signoff(self) -> None:
        """main signoff returns 0 (greenfield stub)."""
        result = main(["signoff"])
        assert result == 0

    def test_main_signoff_strict(self) -> None:
        """main signoff --strict returns 0 (greenfield stub)."""
        result = main(["signoff", "--strict"])
        assert result == 0


# ---------------------------------------------------------------------------
# validate_artifacts
# ---------------------------------------------------------------------------


class TestValidateArtifacts:
    """Direct validate_artifacts function tests."""

    def test_empty_paths_list(self) -> None:
        """Empty paths list returns 0 with warning."""
        result = validate_artifacts([])
        assert result == 0

    def test_nonexistent_path(self) -> None:
        """Non-existent path returns 0 (warn, not error — greenfield)."""
        result = validate_artifacts(["/nonexistent/path/12345"])
        assert result == 0

    def test_existing_empty_directory(self) -> None:
        """Existing empty directory returns 0 (SKIP)."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            result = validate_artifacts([tmpdir])
            assert result == 0

    def test_multiple_paths_one_bad(self) -> None:
        """One bad path among several returns 0 (warn, not error — greenfield)."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            result = validate_artifacts([tmpdir, "/nonexistent/path/12345"])
            assert result == 0

    def test_path_is_file_not_directory(self) -> None:
        """File path (not directory) returns 0 (warn, not error — greenfield)."""
        import tempfile

        with tempfile.NamedTemporaryFile() as tf:
            result = validate_artifacts([tf.name])
            assert result == 0


# ---------------------------------------------------------------------------
# validate_signoff
# ---------------------------------------------------------------------------


class TestValidateSignoff:
    """Direct validate_signoff function tests."""

    def test_signoff_always_returns_zero(self) -> None:
        """validate_signoff always returns 0 during greenfield."""
        assert validate_signoff() == 0
        assert validate_signoff(strict=True) == 0
        assert validate_signoff(strict=False) == 0
