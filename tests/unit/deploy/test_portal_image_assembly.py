"""Offline packaging fences and actual shell RUN controls; no engine/UID claim."""

import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


def test_effective_human_ignore_keeps_closed_read_descriptor_ancestry() -> None:
    lines = (ROOT / "deploy/cibseven/Dockerfile.human.dockerignore").read_text().splitlines()
    rules = [line for line in lines if line and not line.startswith("#")]
    assert rules == [
        "**",
        "!src/",
        "src/*",
        "!src/maezo/",
        "src/maezo/*",
        "!src/maezo/portal/",
        "src/maezo/portal/*",
        "!src/maezo/portal/engine/",
        "src/maezo/portal/engine/*",
        "!src/maezo/portal/engine/java/",
        "src/maezo/portal/engine/java/*",
        "!src/maezo/portal/engine/java/pom.xml",
        "!src/maezo/portal/engine/java/src/",
        "!src/maezo/portal/engine/java/src/**",
        "!deploy/",
        "deploy/*",
        "!deploy/cibseven/",
        "deploy/cibseven/*",
        "!deploy/cibseven/human-webapp/",
        "!deploy/cibseven/human-webapp/**",
        "!deploy/cibseven/read-webapp/",
        "deploy/cibseven/read-webapp/*",
        "!deploy/cibseven/read-webapp/WEB-INF/",
        "deploy/cibseven/read-webapp/WEB-INF/*",
        "!deploy/cibseven/read-webapp/WEB-INF/web.xml",
        ".env",
        ".env.*",
        "*.env",
        "**/.env",
        "**/.env.*",
        "**/*.env",
    ]
    assert not (ROOT / "deploy/cibseven/Dockerfile.secured-v2.dockerignore").exists()


def test_root_read_and_native_exceptions_keep_every_sibling_excluded() -> None:
    lines = (ROOT / ".dockerignore").read_text().splitlines()
    for directory in ("read-webapp", "secured/native-v2"):
        prefix = "deploy/cibseven/" + directory
        start = lines.index("!" + prefix + "/")
        assert lines[start : start + 5] == [
            "!" + prefix + "/",
            prefix + "/*",
            "!" + prefix + "/WEB-INF/",
            prefix + "/WEB-INF/*",
            "!" + prefix + "/WEB-INF/web.xml",
        ]
    assert {".env", ".env.*", "*.env"}.issubset(lines)


@pytest.mark.skipif(sys.platform != "linux", reason="RUN requires Linux sed -i; image UID remains unverified")
@pytest.mark.parametrize("flag", ["true", "false", "invalid"])
def test_complete_human_plugin_run_on_owned_fixture(tmp_path: Path, flag: str) -> None:
    """Prove shell chaining on caller-owned files, not Docker COPY ownership."""
    dockerfile = (ROOT / "deploy/cibseven/Dockerfile.human").read_text()
    logical = re.sub(r"\\\n\s*", " ", dockerfile).splitlines()
    steps = [
        line.removeprefix("RUN ")
        for line in logical
        if line.startswith("RUN test -f /camunda/conf") or line.startswith("RUN case ")
    ]
    assert len(steps) == 2
    assert not any(line.startswith("USER ") for line in logical)
    camunda = tmp_path / "camunda"
    stage = tmp_path / "tmp/maezo-human-read"
    (camunda / "conf").mkdir(parents=True)
    (camunda / "webapps").mkdir()
    (stage / "WEB-INF").mkdir(parents=True)
    xml = (ROOT / "deploy/cibseven/read-webapp/WEB-INF/web.xml").read_bytes()
    (stage / "WEB-INF/web.xml").write_bytes(xml)
    descriptor = camunda / "conf/bpm-platform.xml"
    descriptor.write_text("<bpm-platform><process-engine><plugins></plugins></process-engine></bpm-platform>")
    for index, step in enumerate(steps):
        command = step.replace("/camunda/", shlex.quote(str(camunda)) + "/")
        command = command.replace("/tmp/maezo-human-read", shlex.quote(str(stage)))
        result = subprocess.run(
            ["sh", "-ec", command],
            env={"PATH": os.environ["PATH"], "INSTALL_PORTAL_READ": flag},
            capture_output=True,
            timeout=5,
            check=False,
        )
        assert result.returncode == (1 if index == 1 and flag == "invalid" else 0), result.stderr
    assert descriptor.read_text().count("br.com.maezo.human.HumanCommandPlugin") == 1
    assert descriptor.read_text().count("br.com.maezo.human.PortalReadPlugin") == (flag == "true")
    installed = camunda / "webapps/maezo-human-read"
    assert installed.exists() == (flag == "true")
    assert stage.exists() == (flag == "invalid")
    if flag == "true":
        assert (installed / "WEB-INF/web.xml").read_bytes() == xml
