"""Cercas da Onda 4 no Terraform do dev (`engine-native.tf` + `service-cibseven.tf`).

O que se tranca aqui, na FONTE: (1) tudo que a Onda 4 cria e' condicional a `var.engine_native`
(default null), para o dev nao mudar antes da janela; (2) o NLB e' interno e so aceita 443 do SG
do portal; (3) o 8443 das tasks so vem do SG do NLB; (4) o registro vai na zona que o Cloud Map ja
mantem, sem zona nova; (5) o init le o segredo pinado por versionId. O formato avaliado (JSON da
task definition ligado/desligado) fica em `tests/engine-native.tftest.hcl`, com provider mockado;
o no-op contra o state vivo, no `terraform plan` real do PR.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final

import pytest

from tests.unit.deploy._hcl_probe import (
    attr_raw,
    attr_strings,
    block_body,
    direct_attrs,
    has_attr,
    strip_comments,
)

_ENV: Final[Path] = Path(__file__).resolve().parents[3] / "deploy" / "aws-ecs" / "envs" / "dev-sa-east-1"


def _src(name: str) -> str:
    path = _ENV / name
    assert path.is_file(), f"arquivo Terraform ausente: {path}"
    return strip_comments(path.read_text(encoding="utf-8"))


NATIVE = _src("engine-native.tf")
CIBSEVEN = _src("service-cibseven.tf")


def _resource(kind: str, name: str) -> str:
    return block_body(NATIVE, rf'resource\s+"{kind}"\s+"{name}"\s*\{{')


def test_variavel_tem_default_null() -> None:
    body = block_body(NATIVE, r'variable\s+"engine_native"\s*\{')
    assert attr_raw(direct_attrs(body), "default") == "null"


@pytest.mark.parametrize(
    ("kind", "name"),
    [
        ("aws_security_group", "engine_native_nlb"),
        ("aws_vpc_security_group_ingress_rule", "engine_native_nlb_from_portal"),
        ("aws_vpc_security_group_ingress_rule", "engine_native_nlb_from_issuer"),
        ("aws_vpc_security_group_egress_rule", "engine_native_nlb_to_engine"),
        ("aws_vpc_security_group_ingress_rule", "tasks_engine_native_from_nlb"),
        ("aws_lb", "engine_native"),
        ("aws_lb_target_group", "engine_native"),
        ("aws_lb_listener", "engine_native"),
        ("aws_service_discovery_service", "engine_native"),
        ("aws_service_discovery_instance", "engine_native"),
        ("aws_iam_role_policy", "task_execution_engine_native"),
    ],
)
def test_todo_recurso_novo_e_condicional_a_engine_native(kind: str, name: str) -> None:
    raw = attr_raw(_resource(kind, name), "for_each")
    assert "local.engine_native_config" in raw, f"{kind}.{name} nao e' gated por engine_native"


def test_locais_de_gate_derivam_so_da_variavel() -> None:
    assert re.search(
        r"engine_native_config\s*=\s*var\.engine_native == null \? \{\} : \{ this = var\.engine_native \}",
        NATIVE,
    )
    assert re.search(
        r"engine_native_list\s*=\s*var\.engine_native == null \? \[\] : \[var\.engine_native\]", NATIVE
    )


def test_nlb_interno_de_rede_nas_subnets_privadas() -> None:
    body = direct_attrs(_resource("aws_lb", "engine_native"))
    assert attr_raw(body, "internal") == "true"
    assert attr_strings(body, "load_balancer_type") == ["network"]
    assert attr_raw(body, "subnets") == "data.aws_subnets.private_app.ids"


def test_target_group_8443_ip_e_listener_tcp_443() -> None:
    tg = direct_attrs(_resource("aws_lb_target_group", "engine_native"))
    assert attr_raw(tg, "port") == "8443"
    assert attr_strings(tg, "protocol") == ["TCP"]
    assert attr_strings(tg, "target_type") == ["ip"]
    listener = direct_attrs(_resource("aws_lb_listener", "engine_native"))
    assert attr_raw(listener, "port") == "443"
    assert attr_strings(listener, "protocol") == ["TCP"]  # nao termina TLS (D-B)
    assert not has_attr(listener, "certificate_arn")


def test_ingress_443_do_nlb_so_do_sg_do_portal() -> None:
    body = _resource("aws_vpc_security_group_ingress_rule", "engine_native_nlb_from_portal")
    assert attr_raw(body, "security_group_id") == "aws_security_group.engine_native_nlb[each.key].id"
    assert attr_raw(body, "referenced_security_group_id") == "aws_security_group.portal[each.key].id"
    assert attr_raw(body, "from_port") == attr_raw(body, "to_port") == "443"
    assert "cidr_ipv4" not in body and "cidr_ipv6" not in body and "prefix_list_id" not in body
    # O unico outro ingress do NLB e' o do sidecar, e so com staff_case_issuer.
    issuer = _resource("aws_vpc_security_group_ingress_rule", "engine_native_nlb_from_issuer")
    assert "if native.staff_case_issuer" in attr_raw(issuer, "for_each")
    ingress_to_nlb = {
        name
        for name in re.findall(r'resource\s+"aws_vpc_security_group_ingress_rule"\s+"(\w+)"', NATIVE)
        if attr_raw(_resource("aws_vpc_security_group_ingress_rule", name), "security_group_id")
        == "aws_security_group.engine_native_nlb[each.key].id"
    }
    assert ingress_to_nlb == {"engine_native_nlb_from_portal", "engine_native_nlb_from_issuer"}


def test_ingress_8443_das_tasks_so_do_sg_do_nlb() -> None:
    body = _resource("aws_vpc_security_group_ingress_rule", "tasks_engine_native_from_nlb")
    assert attr_raw(body, "security_group_id") == "aws_security_group.tasks.id"
    assert (
        attr_raw(body, "referenced_security_group_id") == "aws_security_group.engine_native_nlb[each.key].id"
    )
    assert attr_raw(body, "from_port") == attr_raw(body, "to_port") == "8443"
    assert "cidr_ipv4" not in body


def test_registro_na_zona_do_cloud_map_sem_zona_nova() -> None:
    # A zona do namespace e' gerida pelo Cloud Map: registro direto nela e' recusado (403).
    assert 'resource "aws_route53_record"' not in NATIVE
    service = _resource("aws_service_discovery_service", "engine_native")
    assert "aws_service_discovery_private_dns_namespace.this.id" in service
    assert 'routing_policy = "WEIGHTED"' in service
    assert "AWS_ALIAS_DNS_NAME = aws_lb.engine_native[each.key].dns_name" in _resource(
        "aws_service_discovery_instance", "engine_native"
    )
    assert 'resource "aws_route53_zone"' not in NATIVE
    assert re.search(
        r'engine_native_hostname\s*=\s*"engine-native\.\$\{aws_service_discovery_private_dns_namespace\.this\.name\}"',
        NATIVE,
    )


def test_init_le_segredo_pinado_por_versao_e_iam_so_nesse_arn() -> None:
    assert 'valueFrom = "${native.native_secret_arn}:::${native.native_secret_version_id}"' in NATIVE
    policy = block_body(NATIVE, r'data\s+"aws_iam_policy_document"\s+"task_execution_engine_native"\s*\{')
    assert "resources = [each.value.native_secret_arn]" in policy
    assert "resources = [each.value.kms_key_arn]" in policy
    assert "kms:EncryptionContext:SecretARN" in policy
    assert '"*"' not in policy


def test_cibseven_so_muda_por_listas_vazias_com_null() -> None:
    # Cada acrescimo ao servico/task do engine passa por uma lista derivada de engine_native.
    assert "jsonencode(concat([merge({" in CIBSEVEN
    assert "}, local.engine_native_cibseven...)], local.engine_native_containers))" in CIBSEVEN
    assert "], local.engine_native_environment)" in CIBSEVEN
    assert '[for native in local.engine_native_list : { containerPort = 8443, protocol = "tcp" }]' in CIBSEVEN
    assert 'currentSchema=${var.engine_native == null ? "cibseven" : "maezo_native,cibseven"}' in CIBSEVEN
    volume = block_body(CIBSEVEN, r'dynamic\s+"volume"\s*\{')
    assert attr_raw(volume, "for_each") == "local.engine_native_volumes"
    lb = block_body(CIBSEVEN, r'dynamic\s+"load_balancer"\s*\{')
    assert attr_raw(lb, "for_each") == "aws_lb_target_group.engine_native"
    assert "container_port   = 8443" in lb
    assert "health_check_grace_period_seconds = var.engine_native == null ? null : 300" in CIBSEVEN


def test_env_do_engine_nativo_usa_os_nomes_do_harness() -> None:
    compose = (Path(__file__).resolve().parents[3] / "deploy" / "c1-local" / "compose.yaml").read_text(
        encoding="utf-8"
    )
    engine = compose.split("\n  engine:\n", 1)[1].split("\n  native:\n", 1)[0]
    harness = set(re.findall(r"^\s{6}(MAEZO_[A-Z_]+_FILE|DB_HOST|DB_PORT|DB_NAME):", engine, re.MULTILINE))
    assert harness == {
        "DB_HOST",
        "DB_PORT",
        "DB_NAME",
        "MAEZO_HUMAN_TRUST_FILE",
        "MAEZO_PORTAL_READ_TRUST_FILE",
        "MAEZO_PORTAL_READ_PROVIDER_FILE",
        "MAEZO_STAFF_COMPOSITION_FILE",
    }
    env = NATIVE.split("engine_native_environment = flatten(", 1)[1].split("]])", 1)[0]
    assert set(re.findall(r'name = "([A-Z_]+)"', env)) == harness


def test_materializador_e_emissor_sao_os_modulos_do_repo() -> None:
    root = Path(__file__).resolve().parents[3] / "src"
    assert "exec python -m maezo.platform.engine_native_materialize" in NATIVE
    assert (root / "maezo" / "platform" / "engine_native_materialize.py").is_file()
    assert "python -m maezo.gateway.staff_cases" in NATIVE
    assert (root / "maezo" / "gateway" / "staff_cases" / "__main__.py").is_file()


def test_descricoes_de_regra_de_sg_so_com_caracteres_aceitos_pela_api() -> None:
    # AuthorizeSecurityGroup*: a-zA-Z0-9. _-:/()#,@[]+=&;{}!$* (400 InvalidParameterValue, 25/09).
    rules = [
        ("aws_vpc_security_group_ingress_rule", "engine_native_nlb_from_portal"),
        ("aws_vpc_security_group_ingress_rule", "engine_native_nlb_from_issuer"),
        ("aws_vpc_security_group_egress_rule", "engine_native_nlb_to_engine"),
        ("aws_vpc_security_group_ingress_rule", "tasks_engine_native_from_nlb"),
    ]
    for kind, name in rules:
        description = re.search(r'description\s*=\s*"([^"]*)"', _resource(kind, name)).group(1)  # type: ignore[union-attr]
        assert re.fullmatch(r"[a-zA-Z0-9. _\-:/()#,@\[\]+=&;{}!$*]*", description), description
