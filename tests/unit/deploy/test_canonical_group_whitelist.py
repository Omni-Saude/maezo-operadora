"""ADR0049 / PR358 F1: canonical identities and group-only image configuration.

Offline source/configuration fences. Actual CIB Seven admission remains a live gate.
"""

from __future__ import annotations

import hashlib
import re
import shlex
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
CANONICAL_STATIC = frozenset(
    [
        "analise-reembolso",
        "analista-recurso-glosa",
        "auditoria-contas",
        "coordenacao-auditoria-medica",
        "coordenacao-clinica",
        "coordenacao-cobranca",
        "coordenacao-contas",
        "coordenacao-contratos",
        "coordenacao-financeira",
        "coordenacao-investigacao",
        "coordenacao-recurso",
        "coordenacao-rede",
        "coordenacao-reembolso",
        "coordenacao-regulatorio",
        "equipe-cuidado",
        "gestao-cobranca",
        "gestao-contratos",
        "gestao-rede",
        "investigacao-fraude",
        "junta-medica",
        "juridico-contratos",
        "juridico-fraude",
        "juridico-rede",
        "juridico-regulatorio",
        "medico-auditor",
        "regulatorio-ans",
        "supervisao-atendimento",
    ]
)
ESCALATION = frozenset(
    {"plantao-clinico", "enfermagem-triagem", "atendimento-humano", "supervisao-atendimento"}
)
SCRIPT = ROOT / "deploy/cibseven/configure-group-whitelist.sh"


def test_original_27_static_candidate_groups_are_preserved() -> None:
    actual = set()
    expressions = set()
    for path in (ROOT / "spec/processes/bpmn").glob("*.bpmn"):
        for element in ET.parse(path).iter():
            value = element.get("{http://camunda.org/schema/1.0/bpmn}candidateGroups", "")
            if "${" in value:
                expressions.add(value)
            if value and "${" not in value:
                actual.update(x.strip() for x in value.split(","))
    assert len(CANONICAL_STATIC) == 27
    assert actual == CANONICAL_STATIC
    assert expressions == {
        "${grupo_humano}",
        "${pagto_alcada.grupo_aprovador}",
        "${roteamento.grupo_atendimento}",
        "${roteamento_dsr.grupo_revisor}",
    }


def test_escalation_groups_are_consistent_with_canonical_bootstrap() -> None:
    from maezo.platform.engine_bootstrap.bootstrap import GRUPOS_DE_ATENDIMENTO

    assert {name for name, _ in GRUPOS_DE_ATENDIMENTO} == ESCALATION
    dmn = (ROOT / "spec/processes/dmn/escalation_routing.dmn").read_text()
    for name in ESCALATION - {"supervisao-atendimento"}:
        assert f'"{name}"' in dmn
    adr = (ROOT / "docs/adr/0049-portal-humano-e-comandos-atomicos.md").read_text()
    assert all(f"`{name}`" in adr for name in CANONICAL_STATIC)


# A representative Tomcat descriptor, including an unrelated job-executor properties
# block. The build transform must preserve every existing property byte-for-byte.
DESCRIPTOR = """<bpm-platform>
  <job-executor><job-acquisition name="default">
    <properties><property name="lockTimeInMillis">300000</property></properties>
  </job-acquisition></job-executor>
  <process-engine name="default">
    <properties>
      <property name="generalResourceWhitelistPattern">[a-zA-Z0-9]+</property>
      <property name="userResourceWhitelistPattern">[a-zA-Z0-9]+</property>
      <property name="tenantResourceWhitelistPattern">[a-zA-Z0-9]+</property>
      <property name="authorizationEnabled">true</property>
      <property name="jobExecutorDeploymentAware">false</property>
    </properties>
  </process-engine>
</bpm-platform>
"""


@pytest.mark.parametrize("explicit_patterns", [True, False])
def test_image_installs_only_group_specific_admission_and_preserves_existing_config(
    tmp_path: Path, explicit_patterns: bool
) -> None:
    descriptor = tmp_path / "bpm-platform.xml"
    assert SCRIPT.is_file()
    original = (
        DESCRIPTOR
        if explicit_patterns
        else re.sub(
            r"^.*(?:general|user|tenant)ResourceWhitelistPattern.*\n", "", DESCRIPTOR, flags=re.MULTILINE
        )
    )
    descriptor.write_text(original)
    result = subprocess.run(["sh", str(SCRIPT), str(descriptor)], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    text = descriptor.read_text()
    root = ET.fromstring(text)
    properties = root.findall("./process-engine/properties/property")
    additions = [p for p in properties if p.get("name") == "groupResourceWhitelistPattern"]
    assert len(additions) == 1
    addition = additions[0]
    pattern = addition.text
    assert pattern
    assert (
        re.sub(
            r'^.*<property name="groupResourceWhitelistPattern">.*</property>\n', "", text, flags=re.MULTILINE
        )
        == original
    )
    for name in (
        CANONICAL_STATIC
        | ESCALATION
        | {"Alice01", "abc", "123", "juridico-privacidade", "aprovador-N1", "foo-bar"}
    ):
        assert re.fullmatch(pattern, name), name
    for name in (
        "",
        "-admin",
        "admin-",
        "admin--ops",
        "admin_ops",
        "admin.ops",
        "../admin",
        "admin/user",
        "admin group",
        "a,b",
        "${admin}",
        "á",
        "admin\n",
    ):
        assert re.fullmatch(pattern, name) is None, name
    dockerfile = (ROOT / "deploy/cibseven/Dockerfile").read_text()
    # As linhas que de fato CONSTROEM (provado em build local e CodeBuild em 09/09/2026): o contexto e'
    # a raiz do repo (por isso `deploy/cibseven/` no COPY), e o script vai para /camunda, nao /tmp —
    # o /tmp da base tem sticky bit e o USER nao-root nao consegue remover o que foi copiado como root.
    assert (
        "COPY deploy/cibseven/configure-group-whitelist.sh /camunda/configure-group-whitelist.sh"
        in dockerfile
    )
    assert "RUN sh /camunda/configure-group-whitelist.sh /camunda/conf/bpm-platform.xml" in dockerfile
    assert "FROM cibseven/cibseven:2.1.0" in dockerfile


@pytest.mark.parametrize(
    "content",
    [
        "<bpm-platform/>",
        DESCRIPTOR.replace('name="default"', 'name="other"'),
        DESCRIPTOR.replace(
            "    <properties>",
            '    <properties>\n      <property name="groupResourceWhitelistPattern">.*</property>',
        ),
    ],
)
def test_unexpected_descriptor_refuses_without_replacing_file(tmp_path: Path, content: str) -> None:
    # A successful same-script control prevents broken shell syntax from masking refusals.
    _assert_transform(tmp_path, DESCRIPTOR.encode())
    descriptor = tmp_path / "bpm-platform.xml"
    assert SCRIPT.is_file()
    descriptor.write_text(content)
    result = subprocess.run(["sh", str(SCRIPT), str(descriptor)], capture_output=True, text=True, check=False)
    assert result.returncode != 0
    assert descriptor.read_text() == content


# Public vendor SOURCE, not an extracted image or a running engine descriptor.
# cibseven/cibseven tag v2.1.0, commit 334d1da8d7f1ba86dbdac5ecd3a77cbbb74082a9:
# https://raw.githubusercontent.com/cibseven/cibseven/334d1da8d7f1ba86dbdac5ecd3a77cbbb74082a9/distro/tomcat/assembly/src/conf/bpm-platform.xml
VENDOR = (Path(__file__).parent / "fixtures/cibseven-v2.1.0-bpm-platform.xml").read_bytes()
GROUP_PROPERTY = b'<property name="groupResourceWhitelistPattern">[a-zA-Z0-9]+(-[a-zA-Z0-9]+)*</property>'
COMMENT = (
    b"<!-- commented engine settings\n<properties>"
    b'<property name="groupResourceWhitelistPattern">.*</property></properties>\n-->'
)


def _assert_transform(tmp_path: Path, original: bytes) -> bytes:
    descriptor = tmp_path / "bpm-platform.xml"
    descriptor.write_bytes(original)
    descriptor.chmod(0o640)
    before = descriptor.stat()
    result = subprocess.run(["sh", str(SCRIPT), str(descriptor)], capture_output=True, check=False)
    assert result.returncode == 0, result.stderr
    assert not result.stderr
    output = descriptor.read_bytes()
    after = descriptor.stat()
    assert (after.st_uid, after.st_gid, after.st_mode, after.st_ino) == (
        before.st_uid,
        before.st_gid,
        before.st_mode,
        before.st_ino,
    )
    # Remove only the inserted bytes: every comment and existing setting survives.
    if b"      " + GROUP_PROPERTY + b"\r\n" in output:
        restored = output.replace(b"      " + GROUP_PROPERTY + b"\r\n", b"", 1)
    elif b"      " + GROUP_PROPERTY + b"\n" in output:
        restored = output.replace(b"      " + GROUP_PROPERTY + b"\n", b"", 1)
    else:
        restored = output.replace(GROUP_PROPERTY, b"", 1)
    assert restored == original
    root = ET.fromstring(output)
    engines = root.findall("./{*}process-engine")
    assert len(engines) == 1 and engines[0].get("name") == "default"
    blocks = engines[0].findall("./{*}properties")
    assert len(blocks) == 1
    groups = [p for p in blocks[0] if p.get("name") == "groupResourceWhitelistPattern"]
    assert len(groups) == 1 and groups[0].text == "[a-zA-Z0-9]+(-[a-zA-Z0-9]+)*"
    assert not list(tmp_path.glob("*.groups.*"))
    return output


def test_pinned_public_vendor_source_is_exact() -> None:
    assert (
        hashlib.sha256(VENDOR).hexdigest()
        == "1830a00251008ea8bb83afa5b975b210df53b5acb815bac7a9723eddd9ae6ae5"
    )


@pytest.mark.parametrize(
    "original",
    [
        VENDOR,
        VENDOR.replace(b"    <properties>", COMMENT + b"\n    <properties>", 1),
        VENDOR.replace(b"    <properties>", b"    " + COMMENT + b"<properties>", 1),
        VENDOR.replace(b"</process-engine>", COMMENT + b"</process-engine>"),
        VENDOR.replace(
            b"    <properties>", b"<plugins><plugin><properties/></plugin></plugins>\n    <properties>", 1
        ),
        VENDOR.replace(b' name="default"', b" name = 'default'"),
        VENDOR.replace(b"\n", b"\r\n"),
        VENDOR.rstrip(b"\n"),
        (
            b'<bpm-platform><process-engine name="default">'
            b"<properties></properties></process-engine></bpm-platform>"
        ),
        VENDOR.replace(b"    <properties>", b'    <properties marker="a>b">', 1),
    ],
    ids=[
        "vendor",
        "comment-before",
        "inline-comment",
        "comment-after",
        "nested-before",
        "single-quotes",
        "crlf",
        "no-final-newline",
        "one-line",
        "quoted-angle",
    ],
)
def test_active_direct_properties_with_comments_and_formatting(tmp_path: Path, original: bytes) -> None:
    _assert_transform(tmp_path, original)


@pytest.mark.parametrize(
    "invalid",
    [
        VENDOR.replace(b"    <properties>", COMMENT, 1).replace(b"    </properties>", b"", 1),
        VENDOR.replace(b"    <properties>", b"    <other>", 1).replace(
            b"    </properties>", b"    </other>", 1
        ),
        VENDOR.replace(b"    <properties>", b"    <properties></properties>\n    <properties>", 1),
        VENDOR.replace(b"    <properties>", b"    <properties/>\n    <properties>", 1),
        VENDOR.replace(b"    <properties>", b"    <properties>" + GROUP_PROPERTY, 1),
        VENDOR.replace(b' name="default"', b' name="other"'),
        VENDOR.replace(
            b"</bpm-platform>",
            b'<process-engine name="default"><properties></properties></process-engine></bpm-platform>',
        ),
        VENDOR.replace(
            b"</bpm-platform>",
            b'<process-engine name="other"><properties></properties></process-engine></bpm-platform>',
        ),
        VENDOR.replace(b"    <properties>", b"    <plugins><properties>", 1).replace(
            b"    </properties>", b"    </properties></plugins>", 1
        ),
        VENDOR.replace(b"    <properties>", b"    <!-- <properties>", 1),
        VENDOR.replace(b"    <properties>", b"    <!-- invalid -- comment --> <properties>", 1),
        VENDOR.replace(b"</process-engine>", b"</other>"),
        VENDOR.replace(b' name="default"', b' name="default" name="other"'),
        VENDOR.replace(b' name="default"', b' name="def&#97;ult"'),
        VENDOR.replace(b"    <properties>", b"    <x:properties>", 1),
        VENDOR + b"<bpm-platform/>",
        (
            b"<!-- <bpm-platform><process-engine name='default'>"
            b"<properties/></process-engine></bpm-platform> -->"
        ),
        b'<bpm-platform><process-engine name="default"><properties/></process-engine></bpm-platform>',
        VENDOR.replace(
            b"<bpm-platform ", b'<!DOCTYPE bpm-platform SYSTEM "file:///untrusted">\n<bpm-platform ', 1
        ),
        VENDOR.replace(b"    <properties>", b"    <![CDATA[<properties>]]>", 1),
        VENDOR.replace(b"    <properties>", b"    <?hook properties?> <properties>", 1),
        VENDOR.replace(b'xmlns="http://www.camunda.org/schema/1.0/BpmPlatform"', b'xmlns="urn:other"'),
        VENDOR.replace(b"    <properties>", b'    <properties xmlns="urn:other">', 1),
    ],
    ids=[
        "comment-only-target",
        "missing",
        "duplicate",
        "empty-and-active",
        "existing-policy",
        "nondefault",
        "two-defaults",
        "two-engines",
        "nested-only",
        "unclosed-comment",
        "invalid-comment",
        "mismatched-close",
        "duplicate-name",
        "encoded-name",
        "prefixed-target",
        "two-roots",
        "all-comment",
        "self-closing-target",
        "doctype",
        "cdata",
        "processing-hook",
        "wrong-namespace",
        "rebound-namespace",
    ],
)
def test_ambiguous_or_missing_active_target_refuses_after_positive_control(
    tmp_path: Path, invalid: bytes
) -> None:
    _assert_transform(tmp_path, VENDOR)
    descriptor = tmp_path / "bpm-platform.xml"
    descriptor.write_bytes(invalid)
    before = descriptor.stat()
    result = subprocess.run(["sh", str(SCRIPT), str(descriptor)], capture_output=True, check=False)
    assert result.returncode != 0
    assert result.stderr == b"Refusing unexpected CIB Seven descriptor/group whitelist\n"
    assert descriptor.read_bytes() == invalid
    after = descriptor.stat()
    assert (after.st_uid, after.st_gid, after.st_mode, after.st_ino) == (
        before.st_uid,
        before.st_gid,
        before.st_mode,
        before.st_ino,
    )
    assert not list(tmp_path.glob("*.groups.*"))


def test_reapplication_refuses_without_changing_first_success(tmp_path: Path) -> None:
    output = _assert_transform(tmp_path, VENDOR)
    descriptor = tmp_path / "bpm-platform.xml"
    result = subprocess.run(["sh", str(SCRIPT), str(descriptor)], capture_output=True, check=False)
    assert result.returncode != 0
    assert result.stderr == b"Refusing unexpected CIB Seven descriptor/group whitelist\n"
    assert descriptor.read_bytes() == output


def test_repository_root_copy_and_narrow_ignore_exception_are_retained() -> None:
    dockerfile = (ROOT / "deploy/cibseven/Dockerfile").read_text()
    assert (
        "COPY deploy/cibseven/configure-group-whitelist.sh /camunda/configure-group-whitelist.sh"
        in dockerfile
    )
    assert "Build with deploy/cibseven as the context" not in dockerfile
    ignore = (ROOT / ".dockerignore").read_text().splitlines()
    assert "deploy/" in ignore
    assert "!deploy/cibseven/configure-group-whitelist.sh" in ignore
    assert [line for line in ignore if line.startswith("!deploy")] == [
        "!deploy/",
        "!deploy/cibseven/",
        "!deploy/cibseven/configure-group-whitelist.sh",
        "!deploy/cibseven/human-webapp/",
        "!deploy/cibseven/human-webapp/WEB-INF/",
        "!deploy/cibseven/human-webapp/WEB-INF/web.xml",
        "!deploy/cibseven/read-webapp/",
        "!deploy/cibseven/read-webapp/WEB-INF/",
        "!deploy/cibseven/read-webapp/WEB-INF/web.xml",
        "!deploy/cibseven/secured/",
        "!deploy/cibseven/secured/descriptors/",
        "!deploy/cibseven/secured/descriptors/global-web.xml",
        "!deploy/cibseven/secured/descriptors/bpm-platform.xml",
        "!deploy/cibseven/secured/descriptors/engine-rest-web.xml",
        "!deploy/cibseven/secured/descriptors/camunda-web.xml",
        "!deploy/cibseven/secured/native-v2/",
        "!deploy/cibseven/secured/native-v2/WEB-INF/",
        "!deploy/cibseven/secured/native-v2/WEB-INF/web.xml",
        # ADR-0049 D8 / WP-C076: unica outra excecao de deploy/ e o par
        # instalador+bundle pinado das raizes RDS sa-east-1 (o README fica fora).
        "!deploy/certificates/",
        "!deploy/certificates/install_rds_roots.py",
        "!deploy/certificates/sa-east-1-bundle.pem",
    ]
    assert {".env", ".env.*", "*.env"}.issubset(ignore)
    assert not (ROOT / "deploy/cibseven/Dockerfile.dockerignore").exists()


def _run_build_whitelist_step(
    directory: Path, original: bytes
) -> tuple[subprocess.CompletedProcess[bytes], Path, Path]:
    """Execute the actual RUN body with only its /camunda fixture root remapped.

    This tests shell chaining and files, not image USER/ownership or an engine.
    The transform is the exact-one active-property gate; grep line counts would
    incorrectly count XML comments preserved by that transform.
    """
    dockerfile = (ROOT / "deploy/cibseven/Dockerfile").read_text()
    logical_lines = re.sub(r"\\\n\s*", " ", dockerfile).splitlines()
    steps = [
        line.removeprefix("RUN ")
        for line in logical_lines
        if line.startswith("RUN sh /camunda/configure-group-whitelist.sh ")
    ]
    assert steps == [
        "sh /camunda/configure-group-whitelist.sh /camunda/conf/bpm-platform.xml  "
        "&& rm /camunda/configure-group-whitelist.sh  "
        '&& echo "OK: groupResourceWhitelistPattern presente uma vez"'
    ]
    (directory / "conf").mkdir(parents=True)
    script = directory / "configure-group-whitelist.sh"
    script.write_bytes(SCRIPT.read_bytes())
    script.chmod(0o444)
    descriptor = directory / "conf/bpm-platform.xml"
    descriptor.write_bytes(original)
    command = steps[0].replace("/camunda/", shlex.quote(str(directory)) + "/")
    result = subprocess.run(["sh", "-c", command], capture_output=True, check=False)
    return result, descriptor, script


@pytest.mark.parametrize("comment", [b"", COMMENT])
def test_complete_build_run_publishes_exactly_one_active_property_and_cleans_script(
    tmp_path: Path, comment: bytes
) -> None:
    original = VENDOR.replace(b"    <properties>", comment + b"\n    <properties>", 1)
    result, descriptor, script = _run_build_whitelist_step(tmp_path / "camunda", original)
    assert result.returncode == 0, result.stderr
    assert result.stdout == b"OK: groupResourceWhitelistPattern presente uma vez\n"
    assert not result.stderr
    assert not script.exists()
    output = descriptor.read_bytes()
    assert output.replace(b"      " + GROUP_PROPERTY + b"\n", b"", 1) == original
    properties = ET.fromstring(output).findall("./{*}process-engine/{*}properties/{*}property")
    active_groups = [p for p in properties if p.get("name") == "groupResourceWhitelistPattern"]
    assert len(active_groups) == 1
    assert active_groups[0].text == "[a-zA-Z0-9]+(-[a-zA-Z0-9]+)*"


@pytest.mark.parametrize(
    "invalid",
    [
        VENDOR.replace(b"    <properties>", b"    <properties></properties>\n    <properties>", 1),
        VENDOR.replace(b"    <properties>", b"    <other>", 1).replace(
            b"    </properties>", b"    </other>", 1
        ),
        VENDOR.replace(b' name="default"', b' name="other"'),
        VENDOR.replace(b"    <properties>", b"    <properties>" + GROUP_PROPERTY, 1),
    ],
    ids=["duplicate", "missing", "invalid-engine", "existing-policy"],
)
def test_complete_build_run_refusal_prevents_cleanup_and_success_echo(tmp_path: Path, invalid: bytes) -> None:
    control, _, control_script = _run_build_whitelist_step(tmp_path / "control", VENDOR)
    assert control.returncode == 0 and not control_script.exists()
    result, descriptor, script = _run_build_whitelist_step(tmp_path / "invalid", invalid)
    assert result.returncode != 0
    assert result.stderr == b"Refusing unexpected CIB Seven descriptor/group whitelist\n"
    assert not result.stdout
    assert descriptor.read_bytes() == invalid
    assert script.exists()  # && stops before rm and the success echo.
