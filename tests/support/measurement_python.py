"""A stdlib-only interpreter for real subprocess measurement fixtures."""

from pathlib import Path
from venv import EnvBuilder


def measurement_python(root: Path) -> str:
    # pytest-cov's startup .pth imports installed pytest before `-m pytest` runs.
    # That makes a temporary pytest.py payload ineffective under the CI coverage
    # lane. Keep coverage on the parent code under test, and give only the
    # synthetic child payload a fresh interpreter without third-party startup hooks.
    environment = root / "measurement-python"
    EnvBuilder(with_pip=False, system_site_packages=False).create(environment)
    return str(environment / "bin" / "python")
