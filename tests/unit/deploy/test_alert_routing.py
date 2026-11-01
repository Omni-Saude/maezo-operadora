"""Cerca do roteamento de alertas (R-085, decisao do dono 2026-10-31, issue #1084 Rodada 2):
Alertmanager do AMP com `critical -> page` e `warning -> ticket`, DARK por padrao.

O que esta cerca impede:
  * o roteamento divergir entre ambientes — a definicao vem de UMA fonte
    (`deploy/observability/alertmanager-amp.yml.tftpl`), lida pelo env dev e pelo modulo;
  * um alerta sem `severity` reconhecida paginar por acidente (a rota raiz e `ticket`);
  * o recurso nascer ligado — no workspace AMP COMPARTILHADO do dev, a definicao e uma por
    workspace e tomaria o roteamento de outros times.

Terraform lido com `_hcl_probe` (o repo nao tem credencial para `plan`); `terraform validate` e
`amtool check-config` foram rodados localmente no PR.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Final

import yaml

from tests.unit.deploy._hcl_probe import attr_raw, block_body, direct_attrs, strip_comments

_REPO: Final[Path] = Path(__file__).resolve().parents[3]
_TEMPLATE: Final[Path] = _REPO / "deploy" / "observability" / "alertmanager-amp.yml.tftpl"
_DEV_TF: Final[Path] = _REPO / "deploy" / "aws-ecs" / "envs" / "dev-sa-east-1" / "alert-routing.tf"
_MODULE: Final[Path] = _REPO / "deploy" / "terraform" / "modules" / "observability"
_ALERT_RULES: Final[Path] = _REPO / "deploy" / "observability" / "alert-rules.yml"


def _rendered() -> dict[str, Any]:
    text = _TEMPLATE.read_text(encoding="utf-8")
    placeholders = set(re.findall(r"\$\{([a-z_]+)\}", text))
    assert placeholders == {"page_topic_arn", "ticket_topic_arn", "aws_region"}, placeholders
    rendered = (
        text.replace("${page_topic_arn}", "arn:aws:sns:sa-east-1:000000000000:page")
        .replace("${ticket_topic_arn}", "arn:aws:sns:sa-east-1:000000000000:ticket")
        .replace("${aws_region}", "sa-east-1")
    )
    outer = yaml.safe_load(rendered)
    assert set(outer) == {"alertmanager_config"}, set(outer)
    config: dict[str, Any] = yaml.safe_load(outer["alertmanager_config"])
    return config


def _route_for(config: dict[str, Any], labels: dict[str, str]) -> str:
    """Alertmanager first-match semantics over `matchers` of the form `name="value"`."""
    for route in config["route"].get("routes", []):
        wanted = dict(re.fullmatch(r'(\w+)="([^"]*)"', m).groups() for m in route["matchers"])  # type: ignore[union-attr]
        if all(labels.get(k) == v for k, v in wanted.items()):
            return str(route["receiver"])
    return str(config["route"]["receiver"])


def test_critical_pages_and_warning_opens_a_ticket() -> None:
    config = _rendered()
    assert _route_for(config, {"severity": "critical"}) == "page"
    assert _route_for(config, {"severity": "warning"}) == "ticket"


def test_unknown_or_missing_severity_never_pages() -> None:
    config = _rendered()
    assert config["route"]["receiver"] == "ticket"
    assert _route_for(config, {"severity": "info"}) == "ticket"
    assert _route_for(config, {}) == "ticket"


def test_the_two_receivers_are_sns_topics_from_the_template_parameters() -> None:
    config = _rendered()
    receivers = {r["name"]: r for r in config["receivers"]}
    assert set(receivers) == {"page", "ticket"}
    assert receivers["page"]["sns_configs"][0]["topic_arn"].endswith(":page")
    assert receivers["ticket"]["sns_configs"][0]["topic_arn"].endswith(":ticket")
    for receiver in receivers.values():
        assert receiver["sns_configs"][0]["sigv4"]["region"] == "sa-east-1"


def test_every_shipped_alert_severity_has_a_route() -> None:
    document: Any = yaml.safe_load(_ALERT_RULES.read_text(encoding="utf-8"))
    severities = {
        rule["labels"]["severity"]
        for group in document["groups"]
        for rule in group["rules"]
        if "alert" in rule
    }
    assert severities <= {"critical", "warning"}, severities


def test_dev_env_routing_is_dark_by_default_and_reads_the_single_template() -> None:
    src = strip_comments(_DEV_TF.read_text(encoding="utf-8"))
    variable = block_body(src, r'variable\s+"amp_alert_routing_enabled"\s*')
    assert attr_raw(variable, "default") == "false"
    assert "var.amp_alert_routing_enabled" in block_body(src, r"locals\s*")
    resources = re.findall(r'(?m)^\s*(resource|data)\s+"([a-z_]+)"\s+"([a-z_]+)"', src)
    assert {r[1] for r in resources} >= {
        "aws_sns_topic",
        "aws_sns_topic_policy",
        "aws_prometheus_alert_manager_definition",
    }
    for kind, rtype, name in resources:
        attrs = direct_attrs(block_body(src, rf'{kind}\s+"{rtype}"\s+"{name}"\s*'))
        gate = re.search(r"(?m)^\s*(count|for_each)\s*=\s*(.+)$", attrs)
        assert gate, f"{rtype}.{name} has no count/for_each: it would exist with the flag off"
        assert "amp_alert_routing_ativo" in gate.group(2) or "aws_sns_topic.alertas" in gate.group(2), (
            rtype,
            name,
            gate.group(2),
        )
    definition = block_body(src, r'resource\s+"aws_prometheus_alert_manager_definition"\s+"maezo"\s*')
    assert '"${path.module}/../../../observability/alertmanager-amp.yml.tftpl"' in definition


def test_dev_env_topic_policy_is_scoped_to_this_workspace_and_account() -> None:
    src = strip_comments(_DEV_TF.read_text(encoding="utf-8"))
    policy = block_body(src, r'data\s+"aws_iam_policy_document"\s+"alertas_amp_publish"\s*')
    assert '"aps.amazonaws.com"' in policy
    assert '"aws:SourceArn"' in policy and "local.amp_workspace_arn" in policy
    assert '"aws:SourceAccount"' in policy and "var.aws_account_id" in policy
    assert '"sns:Publish"' in policy


def test_module_definition_is_dark_unless_the_env_passes_a_rendered_definition() -> None:
    main = strip_comments((_MODULE / "main.tf").read_text(encoding="utf-8"))
    resource = block_body(main, r'resource\s+"aws_prometheus_alert_manager_definition"\s+"maezo"\s*')
    assert attr_raw(direct_attrs(resource), "count") == 'var.alertmanager_definition == "" ? 0 : 1'
    assert attr_raw(resource, "workspace_id") == "aws_prometheus_workspace.maezo.id"
    variables = strip_comments((_MODULE / "variables.tf").read_text(encoding="utf-8"))
    assert attr_raw(block_body(variables, r'variable\s+"alertmanager_definition"\s*'), "default") == '""'
