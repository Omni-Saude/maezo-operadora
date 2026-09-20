"""A stdlib-only interpreter for real subprocess measurement fixtures."""

import subprocess
from pathlib import Path
from venv import EnvBuilder


def measurement_python(root: Path) -> str:
    # pytest-cov's startup .pth imports installed pytest before `-m pytest` runs.
    # That makes a temporary pytest.py payload ineffective under the CI coverage
    # lane. Keep coverage on the parent code under test, and give only the
    # synthetic child payload a fresh interpreter without third-party startup hooks.
    environment = root / "measurement-python"
    EnvBuilder(with_pip=False, system_site_packages=False).create(environment)
    interpreter = str(environment / "bin" / "python")
    # The first exec of a freshly created interpreter carries a one-time,
    # environment-dependent cost that is unrelated to anything under test
    # (measured on macOS/darwin25: 0.49s-2.10s, versus 0.02s warm). Production
    # measures the long-lived repository interpreter, which is never in that
    # never-before-executed state, so pay the cost here — outside every timed
    # measurement window — instead of letting it consume a probe's timeout.
    subprocess.run([interpreter, "-c", "pass"], check=True)
    return interpreter
