"""Offline XML boundary controls; the real TRACE package cases remain unchanged."""

from xml.etree import ElementTree as ET

import pytest
from tests.unit.portal.test_package_lifecycle import secured


def server(connector: str = '<Connector SSLEnabled="true" port="8443"/>') -> ET.ElementTree:
    return ET.ElementTree(
        ET.fromstring(
            "<Server><Service>" + connector + '<Engine><Host autoDeploy="false"/>'
            '<Valve className="org.apache.catalina.valves.AccessLogValve"/>'
            "</Engine></Service></Server>"
        )
    )


def test_trace_is_disabled_and_terminal_valve_precedes_existing_pipeline() -> None:
    tree = server()
    secured.secure_trace_boundary(tree)
    assert tree.find(".//Connector").attrib == {"SSLEnabled": "true", "port": "8443", "allowTrace": "false"}
    valves = tree.findall(".//Engine/Valve")
    assert [v.get("className") for v in valves] == [
        "br.com.maezo.workload.TraceRefusalValve",
        "org.apache.catalina.valves.AccessLogValve",
    ]
    assert tree.find(".//Host").get("autoDeploy") == "false"


@pytest.mark.parametrize(
    "connector",
    [
        "",
        '<Connector port="8080"/>',
        '<Connector SSLEnabled="true"/><Connector port="8080"/>',
    ],
)
def test_ambiguous_or_plaintext_layout_cannot_install_boundary(connector: str) -> None:
    tree = server(connector)
    before = ET.tostring(tree.getroot())
    with pytest.raises(ValueError, match="one HTTPS connector"):
        secured.secure_trace_boundary(tree)
    assert ET.tostring(tree.getroot()) == before


def test_second_install_refuses_without_mutation() -> None:
    tree = server()
    secured.secure_trace_boundary(tree)
    before = ET.tostring(tree.getroot())
    with pytest.raises(ValueError, match="already configured"):
        secured.secure_trace_boundary(tree)
    assert ET.tostring(tree.getroot()) == before


@pytest.mark.parametrize("count", [0, 2])
def test_engine_identity_must_be_unique(count: int) -> None:
    tree = ET.ElementTree(
        ET.fromstring(
            '<Server><Service><Connector SSLEnabled="true"/>' + "<Engine/>" * count + "</Service></Server>"
        )
    )
    with pytest.raises(ValueError, match="one Engine"):
        secured.secure_trace_boundary(tree)
