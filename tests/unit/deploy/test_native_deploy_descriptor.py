"""T1.2 (plano portal-autoridade-nativa-dev; ADR-0049 D5; ADR-0060 D2): offline fences on the
deployment descriptor of the native engine image.

What these tests pin, and why each matters:
- ONE mTLS connector (8443, certificateVerification=required, TLS 1.3) next to the unchanged
  HTTP 8080 that worker/agents/canal/Helena use for engine-rest;
- no password in the descriptor and no proxy/forwarded identity trust;
- the datasource path and TLS pinned in the image (currentSchema=maezo_native,cibseven,
  sslmode=verify-full, the vendored RDS roots), so image and path revert together;
- cibseven.sh cannot rewrite that datasource (SKIP_DB_CONFIG=true + setenv.sh refusal);
- the dev-parity edits of bpm-platform.xml (deploymentAware=false, group whitelist).

The real boot (5 package tests, PostgreSQL over verify-full, negatives) is a local Docker proof
recorded in deploy/cibseven/native-deploy/README.md; these tests only keep the source honest.
"""

from __future__ import annotations

import hashlib
import importlib.util
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

ROOT = Path(__file__).resolve().parents[3]
SERVER_XML = ROOT / "deploy/cibseven/native-deploy/server.xml"
SETENV = ROOT / "deploy/cibseven/native-deploy/setenv.sh"
DOCKERFILE = ROOT / "deploy/cibseven/Dockerfile.human"
DEV_DOCKERFILE = ROOT / "deploy/cibseven/Dockerfile"
MOUNT_ROOT = "/run/maezo/native/"
PINNED_ROOTS = "/camunda/conf/rds-sa-east-1-bundle.pem"


def _server() -> ET.Element:
    return ET.parse(SERVER_XML).getroot()


def _services() -> dict[str, ET.Element]:
    services = _server().findall("./Service")
    names = [s.get("name") for s in services]
    assert names == ["Catalina", "MaezoNative"], names
    return {str(s.get("name")): s for s in services}


def _connectors() -> list[ET.Element]:
    return [c for service in _services().values() for c in service.findall("./Connector")]


def _logical(dockerfile: str) -> list[str]:
    return re.sub(r"\\\n\s*", " ", dockerfile).splitlines()


def test_exactly_one_tls_connector_with_required_client_certificate() -> None:
    connectors = _connectors()
    assert sorted(c.get("port") for c in connectors) == ["8080", "8443"]
    tls = [c for c in connectors if c.get("SSLEnabled") == "true"]
    assert len(tls) == 1
    (native,) = tls
    assert native.get("port") == "8443"
    assert native.get("scheme") == "https"
    assert native.get("secure") == "true"
    hosts = native.findall("./SSLHostConfig")
    assert len(hosts) == 1
    (host,) = hosts
    assert host.get("certificateVerification") == "required"
    assert host.get("protocols") == "TLSv1.3"
    assert host.get("truststoreFile") == MOUNT_ROOT + "client-ca.p12"
    (certificate,) = host.findall("./Certificate")
    assert certificate.get("certificateFile") == MOUNT_ROOT + "server.crt"
    assert certificate.get("certificateKeyFile") == MOUNT_ROOT + "server.key"


def test_mtls_connector_lives_in_its_own_service_with_only_the_native_app_base() -> None:
    """Security review of #482: a valid client certificate must not reach the open engine-rest."""
    services = _services()
    assert [c.get("port") for c in services["Catalina"].findall("./Connector")] == ["8080"]
    assert [c.get("port") for c in services["MaezoNative"].findall("./Connector")] == ["8443"]
    (engine,) = services["MaezoNative"].findall("./Engine")
    hosts = engine.findall("./Host")
    assert len(hosts) == 1
    (host,) = hosts
    assert engine.get("defaultHost") == host.get("name")
    assert host.get("appBase") == "native-webapps"
    assert host.get("autoDeploy") == "false"
    # No Context/alias can graft another docBase (engine-rest) into the native host.
    assert host.findall(".//Context") == [] and host.findall("./Alias") == []
    (catalina_engine,) = services["Catalina"].findall("./Engine")
    assert [h.get("appBase") for h in catalina_engine.findall("./Host")] == ["webapps"]


def test_image_installs_only_human_webapps_in_the_native_app_base() -> None:
    text = DOCKERFILE.read_text(encoding="utf-8")
    runtime = _logical(text.split("\nFROM cibseven/", 1)[1])
    into_native = [
        line for line in runtime if "/camunda/native-webapps/" in line and not line.startswith("#")
    ]
    copies = [line for line in into_native if line.startswith("COPY ")]
    assert copies == ["COPY deploy/cibseven/human-webapp /camunda/native-webapps/maezo-human"]
    (read_branch,) = [line for line in runtime if line.startswith('RUN case "$INSTALL_PORTAL_READ"')]
    assert "cp -R /tmp/maezo-human-read /camunda/native-webapps/maezo-human-read" in read_branch
    (guard,) = [line for line in runtime if "unexpected native appBase content" in line]
    assert '"$native" = "maezo-human "' in guard
    assert '"$native" = "maezo-human maezo-human-read "' in guard


def test_http_connector_for_engine_rest_stays_plain_and_untrusted() -> None:
    (plain,) = [c for c in _connectors() if c.get("port") == "8080"]
    assert plain.get("SSLEnabled") is None
    assert plain.get("secure") in (None, "false")
    assert plain.get("scheme") in (None, "http")
    # No redirect of plain requests into the native port.
    assert plain.get("redirectPort") is None


def test_descriptor_carries_no_password_proxy_trust_or_extra_listener() -> None:
    server = _server()
    assert server.get("port") == "-1", "shutdown port must stay disabled"
    for element in server.iter():
        for name, value in element.attrib.items():
            if "assword" in name:
                # The only password is the datasource credential, resolved from the environment.
                assert element.tag == "Resource" and value == "${DB_PASSWORD}", (element.tag, name)
            assert name not in {"proxyName", "proxyPort", "trustedProxies", "internalProxies"}
    valves = [v.get("className", "") for v in server.iter("Valve")]
    assert not [v for v in valves if "RemoteIp" in v or "SSLValve" in v]
    for connector in _connectors():
        assert connector.get("allowTrace") == "false"
        assert "AJP" not in (connector.get("protocol") or "").upper()
    for host in server.iter("Host"):
        assert host.get("autoDeploy") == "false"


def test_datasource_pins_native_schema_path_and_verify_full_tls() -> None:
    resources = _server().findall("./GlobalNamingResources/Resource[@name='jdbc/ProcessEngine']")
    assert len(resources) == 1
    (datasource,) = resources
    assert datasource.get("driverClassName") == "org.postgresql.Driver"
    assert datasource.get("username") == "${DB_USERNAME}"
    assert datasource.get("password") == "${DB_PASSWORD}"
    url = datasource.get("url") or ""
    assert url.startswith("jdbc:postgresql://${DB_HOST}:${DB_PORT}/${DB_NAME}?")
    query = parse_qs(urlsplit(url.removeprefix("jdbc:")).query, strict_parsing=True)
    # ADR-0060 D5: exact comparison, never a prefix (maezo_native is a prefix of maezo_native_v2).
    assert query == {
        "currentSchema": ["maezo_native,cibseven"],
        "sslmode": ["verify-full"],
        "sslrootcert": [PINNED_ROOTS],
    }


def _installer():
    spec = importlib.util.spec_from_file_location(
        "install_rds_roots", ROOT / "deploy/certificates/install_rds_roots.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_image_installs_the_descriptor_and_byte_pinned_rds_roots() -> None:
    text = DOCKERFILE.read_text(encoding="utf-8")
    runtime = _logical(text.split("\nFROM cibseven/", 1)[1])
    assert "COPY deploy/cibseven/native-deploy/server.xml /camunda/conf/server.xml" in runtime
    assert "COPY deploy/cibseven/native-deploy/setenv.sh /camunda/bin/setenv.sh" in runtime
    assert f"COPY deploy/certificates/sa-east-1-bundle.pem {PINNED_ROOTS}" in runtime
    pin = _installer().BUNDLE_SHA256
    bundle = (ROOT / "deploy/certificates/sa-east-1-bundle.pem").read_bytes()
    assert hashlib.sha256(bundle).hexdigest() == pin
    assert any(line.startswith(f'RUN echo "{pin}  {PINNED_ROOTS}" | sha256sum -c -') for line in runtime)
    assert "ENV SKIP_DB_CONFIG=true" in runtime
    assert "EXPOSE 8443" in runtime
    # The descriptor replaced by the harness fixture must be the ONLY server.xml copy.
    assert sum("/camunda/conf/server.xml" in line and line.startswith("COPY ") for line in runtime) == 1


def test_bpm_platform_keeps_parity_with_the_dev_engine_image() -> None:
    human = _logical(DOCKERFILE.read_text(encoding="utf-8"))
    dev = _logical(DEV_DOCKERFILE.read_text(encoding="utf-8"))
    flip = (
        'RUN sed -i \'s|<property name="jobExecutorDeploymentAware">true</property>|'
        '<property name="jobExecutorDeploymentAware">false</property>|\' '
    )
    assert any(line.startswith(flip) for line in dev)
    assert any(line.startswith(flip) for line in human)
    whitelist = "RUN sh /camunda/configure-group-whitelist.sh /camunda/conf/bpm-platform.xml"
    assert any(line.startswith(whitelist) for line in dev)
    assert any(line.startswith(whitelist) for line in human)
    # Staff needs authorizationEnabled=true (inherited) and no tenantCheckEnabled override.
    guard = next(line for line in human if line.startswith(flip))
    assert "grep -c '<property name=\"authorizationEnabled\">true</property>'" in guard
    assert "! grep -q 'tenantCheckEnabled'" in guard
    # Parity edits run before any plugin is inserted (the whitelist parser reads the base layout).
    index = {line: n for n, line in enumerate(human)}
    first_plugin = min(n for line, n in index.items() if "<plugins><plugin>" in line)
    assert max(n for line, n in index.items() if line.startswith((flip, whitelist))) < first_plugin


def test_composition_flag_is_checked_against_the_built_jar() -> None:
    build = _logical(DOCKERFILE.read_text(encoding="utf-8").split("\nFROM cibseven/", 1)[0])
    (check,) = [line for line in build if line.startswith('RUN case "$INSTALL_STAFF_COMPOSITION"')]
    assert "jar tf target/human-command-engine-1.0.0.jar" in check
    assert "grep -qx 'br/com/maezo/human/StaffDeploymentComposition.class'" in check
    assert "*) exit 1 ;;" in check
    assert "ARG INSTALL_STAFF_COMPOSITION=false" in build


def _source_setenv(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    script = f'. {SETENV.as_posix()!s}; printf "%s" "$CATALINA_OPTS"'
    return subprocess.run(
        ["sh", "-c", script],
        env={"PATH": os.environ["PATH"], **env},
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )


BASE_ENV = {
    "SKIP_DB_CONFIG": "true",
    "DB_HOST": "amh-aurora-hapi-dev.cluster-abc.sa-east-1.rds.amazonaws.com",
    "DB_PORT": "5432",
    "DB_NAME": "maezo",
}


@pytest.mark.skipif(sys.platform != "linux", reason="sources the POSIX setenv.sh with /bin/sh")
@pytest.mark.parametrize(
    "override",
    [{"DB_NAME": "Maezo_1"}, {"DB_PORT": "1"}, {"DB_PORT": "65535"}, {"DB_HOST": "10.40.20.70"}],
)
def test_setenv_accepts_the_allowlisted_datasource_location(override: dict[str, str]) -> None:
    result = _source_setenv(dict(BASE_ENV, **override))
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(sys.platform != "linux", reason="sources the POSIX setenv.sh with /bin/sh")
def test_setenv_resolves_datasource_from_environment_and_exits_on_connector_failure() -> None:
    result = _source_setenv(dict(BASE_ENV, CATALINA_OPTS="-Dkeep=1"))
    assert result.returncode == 0, result.stderr
    options = result.stdout.split()
    assert options[0] == "-Dkeep=1"
    assert (
        "-Dorg.apache.tomcat.util.digester.PROPERTY_SOURCE=org.apache.tomcat.util.digester.EnvironmentPropertySource"
        in options
    )
    assert "-Dorg.apache.catalina.startup.EXIT_ON_INIT_FAILURE=true" in options


@pytest.mark.skipif(sys.platform != "linux", reason="sources the POSIX setenv.sh with /bin/sh")
@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"SKIP_DB_CONFIG": ""}, "SKIP_DB_CONFIG must stay 'true'"),
        ({"SKIP_DB_CONFIG": "false"}, "SKIP_DB_CONFIG must stay 'true'"),
        ({"DB_HOST": ""}, "DB_HOST is missing"),
        ({"DB_PORT": ""}, "DB_PORT is missing"),
        ({"DB_NAME": ""}, "DB_NAME is missing"),
        # pgjdbc keeps the LAST value of a repeated parameter: these would void the pin.
        ({"DB_NAME": "cibseven?currentSchema=public&x="}, "DB_NAME is missing or has characters"),
        (
            {"DB_NAME": "maezo&sslfactory=org.postgresql.ssl.NonValidatingFactory"},
            "DB_NAME is missing or has characters",
        ),
        ({"DB_NAME": "maezo-x"}, "DB_NAME is missing or has characters"),
        ({"DB_NAME": "maezo\nx"}, "DB_NAME is missing or has characters"),
        ({"DB_HOST": "db:5432/maezo?currentSchema=public#"}, "DB_HOST is missing or has characters"),
        ({"DB_HOST": "DB.example"}, "DB_HOST is missing or has characters"),
        ({"DB_HOST": "db x"}, "DB_HOST is missing or has characters"),
        ({"DB_HOST": "db\n"}, "DB_HOST is missing or has characters"),
        ({"DB_PORT": "5432&sslmode=disable"}, "DB_PORT is missing or has characters"),
        ({"DB_PORT": "123456"}, "DB_PORT is missing or has characters"),
        ({"DB_PORT": "-1"}, "DB_PORT is missing or has characters"),
    ],
)
def test_setenv_refuses_to_start_without_the_pinned_datasource(
    override: dict[str, str], message: str
) -> None:
    result = _source_setenv({k: v for k, v in dict(BASE_ENV, **override).items() if v})
    assert result.returncode == 1
    assert message in result.stderr
    assert result.stdout == ""
