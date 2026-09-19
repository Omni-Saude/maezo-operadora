"""Installer output must put attributed denial before native authentication."""

import importlib.util
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).parents[3]


def test_application_boundary_precedes_authentication_and_keeps_global_coverage(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "secured_install", ROOT / "deploy/cibseven/secured/install.py"
    )
    assert spec and spec.loader
    installer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(installer)
    for relative, content in {
        "conf/web.xml": '<web-app xmlns="https://jakarta.ee/xml/ns/jakartaee"/>',
        "webapps/engine-rest/WEB-INF/web.xml": (
            '<web-app xmlns="https://jakarta.ee/xml/ns/jakartaee">'
            "<servlet><servlet-name>Resteasy</servlet-name>"
            "<servlet-class>org.jboss.resteasy.plugins.server.servlet.HttpServletDispatcher</servlet-class>"
            "</servlet><filter><filter-name>old</filter-name></filter>"
            "<filter-mapping><filter-name>old</filter-name></filter-mapping></web-app>"
        ),
        "conf/bpm-platform.xml": "<bpm-platform><plugins/></bpm-platform>",
    }.items():
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    monkeypatch.setattr(installer, "ROOT", tmp_path)
    installer.install()
    ns = {"w": "https://jakarta.ee/xml/ns/jakartaee"}
    app = ET.parse(tmp_path / "webapps/engine-rest/WEB-INF/web.xml")
    global_xml = ET.parse(tmp_path / "conf/web.xml")
    assert [
        node.findtext("w:filter-name", namespaces=ns) for node in app.findall("w:filter-mapping", ns)
    ] == ["maezo-boundary", "maezo-native-auth"]
    for document in (app, global_xml):
        boundary = [
            node
            for node in document.findall("w:filter", ns)
            if node.findtext("w:filter-name", namespaces=ns) == "maezo-boundary"
        ]
        assert len(boundary) == 1
        assert boundary[0].findtext("w:filter-class", namespaces=ns) == "br.com.maezo.workload.BoundaryFilter"
        mappings = [
            node
            for node in document.findall("w:filter-mapping", ns)
            if node.findtext("w:filter-name", namespaces=ns) == "maezo-boundary"
        ]
        assert len(mappings) == 1
        assert mappings[0].findtext("w:url-pattern", namespaces=ns) == "/*"
        assert {node.text for node in mappings[0].findall("w:dispatcher", ns)} == {
            "REQUEST",
            "FORWARD",
            "INCLUDE",
            "ERROR",
            "ASYNC",
        }
    assert app.findtext("w:filter[w:filter-name='maezo-native-auth']/w:filter-class", namespaces=ns) == (
        "org.cibseven.bpm.engine.rest.security.auth.ProcessEngineAuthenticationFilter"
    )

    # Dockerfile.secured and prepare_fixture consume the checked-in descriptor.
    # The generator alone is not the installed artifact. Keep their active filters equivalent.
    shipped = ET.parse(ROOT / "deploy/cibseven/secured/descriptors/engine-rest-web.xml")
    shipped_ns = {"w": "http://java.sun.com/xml/ns/javaee"}

    def active_filters(document, namespaces):
        return [
            (
                node.tag.partition("}")[2],
                tuple(
                    (child.tag.partition("}")[2], (child.text or "").strip())
                    for child in node.iter()
                    if child is not node
                ),
            )
            for node in document.getroot()
            if node.tag.partition("}")[2] in ("filter", "filter-mapping")
        ]

    assert active_filters(shipped, shipped_ns) == active_filters(app, ns)

    for document, namespaces in ((shipped, shipped_ns), (app, ns)):
        rest = [
            node
            for node in document.findall("w:servlet", namespaces)
            if node.findtext("w:servlet-name", namespaces=namespaces) == "Resteasy"
        ]
        assert len(rest) == 1
        assert rest[0].findtext("w:load-on-startup", namespaces=namespaces) == "0"
