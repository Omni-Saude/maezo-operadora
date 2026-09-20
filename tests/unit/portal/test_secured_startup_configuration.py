"""Closed startup package wiring and immutable vendor/descriptor provenance."""

import hashlib
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest
from tests.unit.portal.test_package_lifecycle import secured

ROOT = Path(__file__).parents[3]
DESCRIPTORS = ROOT / "deploy/cibseven/secured/descriptors"
RESOURCES = ROOT / "src/maezo/portal/engine/java/src/main/resources"
APPS = (
    "ROOT",
    "camunda",
    "docs",
    "engine-rest",
    "examples",
    "host-manager",
    "maezo-human",
    "manager",
    "webapp",
)


def test_startup_replaces_original_owner_and_pins_propagation():
    tree = ET.ElementTree(
        ET.fromstring(
            '<Server><Listener className="'
            'org.cibseven.bpm.container.impl.tomcat.TomcatBpmPlatformBootstrap"/>'
            '<Listener className="org.apache.catalina.mbeans.GlobalResourcesLifecycleListener"/>'
            '<GlobalNamingResources><Resource name="jdbc/ProcessEngine" '
            'factory="org.apache.tomcat.jdbc.pool.DataSourceFactory"/></GlobalNamingResources>'
            "<Service><Engine/></Service></Server>"
        )
    )
    secured.secure_startup_boundary(tree)
    assert tree.find("Listener").get("className") == "br.com.maezo.workload.SecuredBpmPlatformBootstrap"
    assert all(
        node.get("throwOnFailure") == "true"
        for node in (tree.getroot(), tree.find("Service"), tree.find("Service/Engine"))
    )
    before = ET.tostring(tree.getroot())
    with pytest.raises(ValueError):
        secured.secure_startup_boundary(tree)
    assert ET.tostring(tree.getroot()) == before


@pytest.mark.parametrize("listeners", ["", '<Listener className="other"/>'])
def test_unknown_original_bootstrap_is_not_adopted(listeners):
    tree = ET.ElementTree(ET.fromstring(f"<Server>{listeners}<Service><Engine/></Service></Server>"))
    with pytest.raises(ValueError):
        secured.secure_startup_boundary(tree)


def test_all_shipped_contexts_have_no_jar_sci_admission_and_are_copied():
    docker = (ROOT / "deploy/cibseven/Dockerfile.secured").read_text()
    for app in APPS:
        name = "human" if app == "maezo-human" else "startup-engine-rest" if app == "engine-rest" else app
        path = DESCRIPTORS / f"{name}-web.xml"
        root = ET.parse(path).getroot()
        assert root.get("metadata-complete") == "true"
        order = [n for n in root if n.tag.split("}")[-1] == "absolute-ordering"]
        assert len(order) == 1 and len(order[0]) == 0
        assert f"descriptors/{name}-web.xml /camunda/webapps/{app}/WEB-INF/web.xml" in docker
    assert ET.parse(DESCRIPTORS / "context.xml").getroot().get("containerSciFilter") == ".*"


def test_compiled_descriptor_pins_match_actual_source_bytes():
    for line in (RESOURCES / "secured-startup-descriptors.sha256").read_text().splitlines():
        expected, relative = line.split(" ", 1)
        if relative == "conf/context.xml":
            source = DESCRIPTORS / "context.xml"
        elif relative == "conf/web.xml":
            source = DESCRIPTORS / "startup-global-web.xml"
        elif relative == "conf/bpm-platform.xml":
            source = DESCRIPTORS / "bpm-platform.xml"
        else:
            app = relative.split("/")[1]
            source = DESCRIPTORS / (
                "human-web.xml"
                if app == "maezo-human"
                else "startup-engine-rest-web.xml"
                if app == "engine-rest"
                else f"{app}-web.xml"
            )
        assert hashlib.sha256(source.read_bytes()).hexdigest() == expected


def test_closed_contexts_cannot_construct_old_background_listeners():
    for app in ("webapp", "examples", "camunda"):
        root = ET.parse(DESCRIPTORS / f"{app}-web.xml").getroot()
        assert not [n for n in root.iter() if n.tag.split("}")[-1] == "listener-class"]
        assert [n.text for n in root.iter() if n.tag.split("}")[-1] == "servlet-class"] == [
            "br.com.maezo.workload.ClosedServlet"
        ]
    global_xml = ET.parse(DESCRIPTORS / "startup-global-web.xml").getroot()
    assert "org.apache.jasper.servlet.JspServlet" not in ET.tostring(global_xml, encoding="unicode")


def test_v1_transformations_preserve_shared_rest_except_reviewed_initializer_delta():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "startup_descriptors", ROOT / "deploy/cibseven/secured/startup_descriptors.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for name, transform in (("engine-rest", module.rest), ("global", module.global_web)):
        assert (DESCRIPTORS / f"startup-{name}-web.xml").read_bytes() == transform(
            (DESCRIPTORS / f"{name}-web.xml").read_bytes()
        )
    native_v2 = (ROOT / "deploy/cibseven/Dockerfile.secured-v2").read_text()
    assert "startup-engine-rest-web.xml" not in native_v2 and "startup-global-web.xml" not in native_v2
