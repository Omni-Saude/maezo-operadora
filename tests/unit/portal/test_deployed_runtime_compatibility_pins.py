"""ENGINE-IT.md: the deployed-runtime-compatibility profile mirrors the pinned CIB image.

Dependabot bumps every <version> it finds in the pom, including the profile's, which
silently turns "test the candidate against the deployed runtime" into "test it against
the newest release" (#838 moved the engine to 2.2.0, #1220 tried 10.1.62 for JDBC).
These pins move only together with the image (Dockerfile.human) and its vendor manifest.
"""

import re
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).parents[3]
JAVA_ROOT = ROOT / "src/maezo/portal/engine/java"
VENDOR_MANIFEST = JAVA_ROOT / "src/main/resources/secured-startup-vendor.sha256"
NS = {"m": "http://maven.apache.org/POM/4.0.0"}

# sha256 of tomcat-jdbc-10.1.47.jar on Maven Central == lib/tomcat-jdbc.jar in the image.
# The image ships the jar unversioned, so the version is bound to the hash here.
TOMCAT_JDBC_BY_IMAGE_HASH = {
    "d8bbf4c822ccf2118a321a0e48a2c200c7beeddfeca3791ddc90adfb88c7bd33": "10.1.47",
}


def _profile_versions() -> dict[str, str]:
    pom = ET.parse(JAVA_ROOT / "pom.xml")
    profile = next(
        p
        for p in pom.findall("m:profiles/m:profile", NS)
        if p.findtext("m:id", namespaces=NS) == "deployed-runtime-compatibility"
    )
    return {
        d.findtext("m:artifactId", namespaces=NS): d.findtext("m:version", namespaces=NS)
        for d in profile.findall("m:dependencies/m:dependency", NS)
    }


def _manifest() -> dict[str, str]:
    entries = (
        line.split(maxsplit=1)
        for line in VENDOR_MANIFEST.read_text(encoding="utf-8").splitlines()
        if line.strip()
    )
    return {path: digest for digest, path in entries}


def test_profile_engine_version_is_the_image_engine_version() -> None:
    image_tags = {
        m.group(1)
        for name in ("Dockerfile.human", "Dockerfile.secured-v2")
        for m in re.finditer(
            r"^FROM cibseven/cibseven:tomcat-([0-9.]+)@",
            (ROOT / "deploy/cibseven" / name).read_text(encoding="utf-8"),
            re.M,
        )
    }
    manifest_engine = {
        m.group(1) for p in _manifest() if (m := re.fullmatch(r"lib/cibseven-engine-([0-9.]+)\.jar", p))
    }

    assert len(image_tags) == 1
    assert manifest_engine == image_tags
    assert _profile_versions()["cibseven-engine"] in image_tags


def test_profile_tomcat_jdbc_is_the_jar_hashed_in_the_image() -> None:
    image_digest = _manifest()["lib/tomcat-jdbc.jar"]

    assert image_digest in TOMCAT_JDBC_BY_IMAGE_HASH, (
        "image changed: re-derive the tomcat-jdbc version from its hash"
    )
    assert _profile_versions()["tomcat-jdbc"] == TOMCAT_JDBC_BY_IMAGE_HASH[image_digest]
