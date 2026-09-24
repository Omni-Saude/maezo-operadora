"""Offline packaging fences and actual shell RUN controls; no engine claim.

The runtime UID is not left to inheritance: the cibseven images declare
`USER camunda` (uid/gid 1000 in the pinned base) and carry a fail-closed
build-time assertion for it. The tests below pin that declaration so a
revert to root — or to an undeclared user — fails here.
"""

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
        "!deploy/cibseven/configure-group-whitelist.sh",
        "!deploy/cibseven/native-deploy/",
        "deploy/cibseven/native-deploy/*",
        "!deploy/cibseven/native-deploy/server.xml",
        "!deploy/cibseven/native-deploy/setenv.sh",
        "!deploy/certificates/",
        "deploy/certificates/*",
        "!deploy/certificates/sa-east-1-bundle.pem",
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


def test_staged_read_webapp_copy_is_owned_by_declared_nonroot_user() -> None:
    """Keep both mv and recursive removal usable beneath the image's sticky /tmp.

    Docker COPY defaults to root ownership. This source fence complements the
    complete RUN controls below; it does not simulate Docker ownership or a build.
    """
    dockerfile = (ROOT / "deploy/cibseven/Dockerfile.human").read_text()
    logical = re.sub(r"\\\n\s*", " ", dockerfile).splitlines()
    # Select the actual source path, not comments or an unrelated COPY option.
    copies = [
        shlex.split(line)
        for line in logical
        if line.startswith("COPY ") and "deploy/cibseven/read-webapp" in shlex.split(line)
    ]
    assert copies == [
        [
            "COPY",
            "--chown=camunda:camunda",
            "deploy/cibseven/read-webapp",
            "/tmp/maezo-human-read",
        ]
    ]
    # DS-0002 fence (owner decision D2, 19/09/2026): the runtime user is DECLARED,
    # not inherited — exactly `USER camunda` (uid/gid 1000 in the pinned base), as
    # the LAST user directive, with the fail-closed build assertion that pins uid
    # 1000 still present. This fails if the directive is removed (the trivy
    # DS-0002 finding returns and non-root stops being enforced by this repo),
    # demoted to root (`USER root` / `USER 0`), renamed to another user, or if the
    # build-time assertion is stripped.
    # C1 (item 3): exactly one transient `USER root`, immediately around the RUN that makes
    # bpm-platform.xml root-owned (test_human_image_descriptor_ownership.py); camunda stays last.
    users = [line for line in logical if line.startswith("USER ")]
    assert users == ["USER root", "USER camunda"]
    root_at = logical.index("USER root")
    assert logical[root_at + 1].startswith("RUN chown root:root /camunda/conf/bpm-platform.xml")
    assert logical[root_at + 2] == "USER camunda"
    assert any(line.startswith("RUN set -eu;") and "DS-0002" in line and "1000" in line for line in logical)


@pytest.mark.skipif(sys.platform != "linux", reason="RUN requires Linux sed -i; image UID remains unverified")
@pytest.mark.parametrize(
    ("flag", "composition"),
    [
        ("true", "false"),
        ("false", "false"),
        ("invalid", "false"),
        ("false", "true"),
        ("true", "true"),
        ("false", "invalid"),
    ],
)
def test_complete_human_plugin_run_on_owned_fixture(tmp_path: Path, flag: str, composition: str) -> None:
    """Prove shell chaining on caller-owned files, not Docker COPY ownership."""
    dockerfile = (ROOT / "deploy/cibseven/Dockerfile.human").read_text()
    logical = re.sub(r"\\\n\s*", " ", dockerfile).splitlines()
    # Only the runtime stage edits the descriptor; the build stage has its own composition check.
    runtime = re.sub(r"\\\n\s*", " ", dockerfile.split("\nFROM cibseven/", 1)[1]).splitlines()
    steps = [
        line.removeprefix("RUN ")
        for line in runtime
        if line.startswith("RUN test -f /camunda/conf") or line.startswith("RUN case ")
    ]
    assert len(steps) == 3
    # Same DS-0002 fence as above: the declared runtime user is exactly `USER
    # camunda` (never root, never absent — absence re-hides the trivy finding),
    # and the fail-closed build assertion pinning uid 1000 is still there.
    # C1 (item 3): exactly one transient `USER root`, immediately around the RUN that makes
    # bpm-platform.xml root-owned (test_human_image_descriptor_ownership.py); camunda stays last.
    users = [line for line in logical if line.startswith("USER ")]
    assert users == ["USER root", "USER camunda"]
    root_at = logical.index("USER root")
    assert logical[root_at + 1].startswith("RUN chown root:root /camunda/conf/bpm-platform.xml")
    assert logical[root_at + 2] == "USER camunda"
    assert any(line.startswith("RUN set -eu;") and "DS-0002" in line and "1000" in line for line in logical)
    camunda = tmp_path / "camunda"
    stage = tmp_path / "tmp/maezo-human-read"
    (camunda / "conf").mkdir(parents=True)
    (camunda / "webapps").mkdir()
    (camunda / "native-webapps").mkdir()
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
            env={
                "PATH": os.environ["PATH"],
                "INSTALL_PORTAL_READ": flag,
                "INSTALL_STAFF_COMPOSITION": composition,
            },
            capture_output=True,
            timeout=5,
            check=False,
        )
        refused = (index == 1 and flag == "invalid") or (index == 2 and composition == "invalid")
        assert result.returncode == (1 if refused else 0), result.stderr
        if refused:
            break
    text = descriptor.read_text()
    assert text.count("br.com.maezo.human.HumanCommandPlugin") == 1
    if flag != "invalid":
        assert text.count("br.com.maezo.human.StaffDeploymentComposition") == (composition == "true")
    if composition == "true":
        # D-E: the composition plugin calls HumanCommandPlugin's setters, so it must come first.
        assert text.index("StaffDeploymentComposition") < text.index("HumanCommandPlugin")
    assert descriptor.read_text().count("br.com.maezo.human.PortalReadPlugin") == (flag == "true")
    installed = camunda / "webapps/maezo-human-read"
    assert installed.exists() == (flag == "true")
    assert stage.exists() == (flag == "invalid")
    if flag == "true":
        assert (installed / "WEB-INF/web.xml").read_bytes() == xml
        # The mTLS-only app base gets its own copy (native-deploy/server.xml, Service MaezoNative).
        assert (camunda / "native-webapps/maezo-human-read/WEB-INF/web.xml").read_bytes() == xml
    else:
        assert not (camunda / "native-webapps/maezo-human-read").exists()
