"""ADR-0049 D5: keep the human webapp on Tomcat 10's Jakarta Servlet ABI."""

from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).parents[3]
JAVA_ROOT = ROOT / "src/maezo/portal/engine/java"


def test_servlet_api_is_jakarta_6_and_container_provided() -> None:
    pom = ET.parse(JAVA_ROOT / "pom.xml")
    namespace = {"m": "http://maven.apache.org/POM/4.0.0"}
    dependencies = {
        (
            dependency.findtext("m:groupId", namespaces=namespace),
            dependency.findtext("m:artifactId", namespaces=namespace),
            dependency.findtext("m:version", namespaces=namespace),
            dependency.findtext("m:scope", namespaces=namespace),
        )
        for dependency in pom.findall("m:dependencies/m:dependency", namespace)
    }

    assert ("jakarta.servlet", "jakarta.servlet-api", "6.0.0", "provided") in dependencies
    assert not any(group == "javax.servlet" for group, _, _, _ in dependencies)


def test_human_servlet_uses_only_jakarta_request_identity() -> None:
    servlet = (JAVA_ROOT / "src/main/java/br/com/maezo/human/HumanServlet.java").read_text(encoding="utf-8")
    boundary_test = (JAVA_ROOT / "src/test/java/br/com/maezo/human/ServletBoundaryTest.java").read_text(
        encoding="utf-8"
    )

    assert "import jakarta.servlet.http.*;" in servlet
    assert 'getAttribute("jakarta.servlet.request.X509Certificate")' in servlet
    assert "javax.servlet" not in servlet
    assert "import jakarta.servlet.*;" in boundary_test
    assert "import jakarta.servlet.http.*;" in boundary_test
    assert "import javax.servlet" not in boundary_test


def test_web_descriptor_declares_jakarta_servlet_6() -> None:
    descriptor = ET.parse(ROOT / "deploy/cibseven/human-webapp/WEB-INF/web.xml").getroot()
    schema_location = "{http://www.w3.org/2001/XMLSchema-instance}schemaLocation"

    assert descriptor.tag == "{https://jakarta.ee/xml/ns/jakartaee}web-app"
    assert descriptor.attrib["version"] == "6.0"
    assert descriptor.attrib[schema_location] == (
        "https://jakarta.ee/xml/ns/jakartaee https://jakarta.ee/xml/ns/jakartaee/web-app_6_0.xsd"
    )
