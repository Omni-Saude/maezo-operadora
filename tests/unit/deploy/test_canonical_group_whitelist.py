"""ADR0049 / PR358 F1: canonical identities and group-only image configuration.

Offline source/configuration fences. Actual CIB Seven admission remains a live gate.
"""

from __future__ import annotations

import re
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
    assert "grep -c 'groupResourceWhitelistPattern' /camunda/conf/bpm-platform.xml | grep -qx 1" in dockerfile
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
    descriptor = tmp_path / "bpm-platform.xml"
    assert SCRIPT.is_file()
    descriptor.write_text(content)
    result = subprocess.run(["sh", str(SCRIPT), str(descriptor)], capture_output=True, text=True, check=False)
    assert result.returncode != 0
    assert descriptor.read_text() == content
