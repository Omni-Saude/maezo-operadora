"""Testes da Lambda de sincronizacao do egresso Cognito do portal.

EC2 via ``botocore.stub.Stubber`` (sem rede, sem credencial real); DNS via resolver injetado.
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import boto3
import pytest
from botocore.stub import Stubber

_HANDLER = (
    Path(__file__).resolve().parents[3]
    / "deploy"
    / "aws-ecs"
    / "lambdas"
    / "cognito_egress_sync"
    / "handler.py"
)
_spec = importlib.util.spec_from_file_location("cognito_egress_sync_handler", _HANDLER)
assert _spec and _spec.loader
handler = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = handler
_spec.loader.exec_module(handler)

SG = "sg-0d382c5d005a12995"
LOGIN = "amh-maezo-bpm-dev.auth.sa-east-1.amazoncognito.com"
IDP = "cognito-idp.sa-east-1.amazonaws.com"
NOW = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)
STAMP = "2026-10-12T12:00:00Z"


def _config(**kw: Any) -> Any:
    base = {"security_group_id": SG, "hosts": (LOGIN, IDP), "stale_hours": 24.0, "max_rules": 20}
    base.update(kw)
    return handler.Config(**base)


def _client() -> tuple[Any, Stubber]:
    ec2 = boto3.client(
        "ec2", region_name="sa-east-1", aws_access_key_id="testing", aws_secret_access_key="testing"
    )
    return ec2, Stubber(ec2)


def _tf_rule(rule_id: str, cidr: str) -> dict[str, Any]:
    return {
        "SecurityGroupRuleId": rule_id,
        "GroupId": SG,
        "IsEgress": True,
        "IpProtocol": "tcp",
        "FromPort": 443,
        "ToPort": 443,
        "CidrIpv4": cidr,
        "Tags": [],
    }


def _managed(rule_id: str, ip: str, host: str, last_seen: datetime) -> dict[str, Any]:
    rule = _tf_rule(rule_id, f"{ip}/32")
    rule["Tags"] = [
        {"Key": "maezo:managed-by", "Value": "cognito-egress-sync"},
        {"Key": "maezo:host", "Value": host},
        {"Key": "maezo:last-seen", "Value": last_seen.strftime("%Y-%m-%dT%H:%M:%SZ")},
    ]
    return rule


def _describe(stub: Stubber, rules: list[dict[str, Any]]) -> None:
    stub.add_response(
        "describe_security_group_rules",
        {"SecurityGroupRules": rules},
        {"Filters": [{"Name": "group-id", "Values": [SG]}]},
    )


def _authorize(stub: Stubber, ip: str, host: str, rule_id: str) -> None:
    stub.add_response(
        "authorize_security_group_egress",
        {"Return": True, "SecurityGroupRules": [{"SecurityGroupRuleId": rule_id}]},
        {
            "GroupId": SG,
            "IpPermissions": [
                {
                    "IpProtocol": "tcp",
                    "FromPort": 443,
                    "ToPort": 443,
                    "IpRanges": [{"CidrIp": f"{ip}/32", "Description": f"cognito-egress-sync {host}"}],
                }
            ],
            "TagSpecifications": [
                {
                    "ResourceType": "security-group-rule",
                    "Tags": [
                        {"Key": "maezo:managed-by", "Value": "cognito-egress-sync"},
                        {"Key": "maezo:host", "Value": host},
                        {"Key": "maezo:last-seen", "Value": STAMP},
                    ],
                }
            ],
        },
    )


def _resolver(mapping: dict[str, Any]) -> Any:
    def resolve(host: str) -> set[str]:
        value = mapping[host]
        if isinstance(value, Exception):
            raise value
        return set(value)

    return resolve


def test_cria_regra_para_ip_novo() -> None:
    ec2, stub = _client()
    _describe(stub, [_tf_rule("sgr-tf1", "54.233.100.143/32")])
    _authorize(stub, "18.229.1.1", LOGIN, "sgr-new1")
    with stub:
        out = handler.sync(
            ec2,
            _config(),
            _resolver({LOGIN: ["18.229.1.1"], IDP: ["54.233.100.143"]}),
            now=NOW,
        )
    stub.assert_no_pending_responses()
    assert out["created"] == [{"rule_id": "sgr-new1", "ip": "18.229.1.1", "host": LOGIN}]
    assert out["removed"] == []


def test_nao_duplica_ip_ja_coberto_pelo_terraform() -> None:
    ec2, stub = _client()
    # /32 do Terraform e um bloco mais largo de outra regra nao gerenciada (ambos cobrem 443).
    _describe(
        stub,
        [_tf_rule("sgr-tf1", "18.229.1.1/32"), _tf_rule("sgr-tf2", "52.67.0.0/16")],
    )
    with stub:
        out = handler.sync(ec2, _config(), _resolver({LOGIN: ["18.229.1.1"], IDP: ["52.67.142.58"]}), now=NOW)
    stub.assert_no_pending_responses()  # nenhum authorize/create_tags/revoke
    assert out["created"] == [] and out["refreshed"] == 0


def test_regra_de_outra_porta_nao_conta_como_cobertura() -> None:
    ec2, stub = _client()
    other = _tf_rule("sgr-tf1", "18.229.1.1/32")
    other["FromPort"] = other["ToPort"] = 5432
    _describe(stub, [other])
    _authorize(stub, "18.229.1.1", LOGIN, "sgr-new1")
    with stub:
        out = handler.sync(ec2, _config(hosts=(LOGIN,)), _resolver({LOGIN: ["18.229.1.1"]}), now=NOW)
    assert [c["ip"] for c in out["created"]] == ["18.229.1.1"]


def test_atualiza_last_seen_de_regra_propria() -> None:
    ec2, stub = _client()
    _describe(stub, [_managed("sgr-m1", "18.229.1.1", LOGIN, NOW - timedelta(hours=30))])
    stub.add_response(
        "create_tags",
        {},
        {"Resources": ["sgr-m1"], "Tags": [{"Key": "maezo:last-seen", "Value": STAMP}]},
    )
    with stub:
        out = handler.sync(ec2, _config(hosts=(LOGIN,)), _resolver({LOGIN: ["18.229.1.1"]}), now=NOW)
    stub.assert_no_pending_responses()
    # Vista agora: atualizada e NAO removida, apesar do last-seen antigo.
    assert out["refreshed"] == 1 and out["removed"] == [] and out["created"] == []


def test_remove_so_regras_proprias_velhas() -> None:
    ec2, stub = _client()
    _describe(
        stub,
        [
            _tf_rule("sgr-tf-old", "9.9.9.9/32"),  # Terraform: nunca tocada, mesmo sem ser resolvida
            _managed("sgr-old", "18.229.9.9", LOGIN, NOW - timedelta(hours=25)),
            _managed("sgr-recent", "18.229.8.8", LOGIN, NOW - timedelta(hours=2)),
            _managed("sgr-cur", "18.229.1.1", LOGIN, NOW - timedelta(minutes=5)),
        ],
    )
    stub.add_response(
        "create_tags",
        {},
        {"Resources": ["sgr-cur"], "Tags": [{"Key": "maezo:last-seen", "Value": STAMP}]},
    )
    stub.add_response(
        "revoke_security_group_egress",
        {"Return": True},
        {"GroupId": SG, "SecurityGroupRuleIds": ["sgr-old"]},
    )
    with stub:
        out = handler.sync(ec2, _config(hosts=(LOGIN,)), _resolver({LOGIN: ["18.229.1.1"]}), now=NOW)
    stub.assert_no_pending_responses()
    assert [r["rule_id"] for r in out["removed"]] == ["sgr-old"]


def test_nunca_remove_a_ultima_regra_de_um_host() -> None:
    ec2, stub = _client()
    # O IP atual do IDP ja e coberto pelo Terraform; as duas regras proprias do host estao velhas.
    _describe(
        stub,
        [
            _tf_rule("sgr-tf1", "52.67.142.58/32"),
            _managed("sgr-a", "18.228.1.1", IDP, NOW - timedelta(hours=48)),
            _managed("sgr-b", "18.228.2.2", IDP, NOW - timedelta(hours=30)),
        ],
    )
    stub.add_response(
        "revoke_security_group_egress",
        {"Return": True},
        {"GroupId": SG, "SecurityGroupRuleIds": ["sgr-a"]},
    )
    with stub:
        out = handler.sync(ec2, _config(hosts=(IDP,)), _resolver({IDP: ["52.67.142.58"]}), now=NOW)
    stub.assert_no_pending_responses()
    assert [r["rule_id"] for r in out["removed"]] == ["sgr-a"]  # sgr-b (mais recente) fica


@pytest.mark.parametrize("failure", [[], OSError("dns down")])
def test_nao_remove_nada_com_resolucao_vazia_ou_erro(failure: Any) -> None:
    ec2, stub = _client()
    _describe(
        stub,
        [
            _managed("sgr-old", "18.229.9.9", LOGIN, NOW - timedelta(hours=100)),
            _managed("sgr-old2", "18.228.9.9", IDP, NOW - timedelta(hours=100)),
            _managed("sgr-cur", "18.228.1.1", IDP, NOW - timedelta(minutes=5)),
        ],
    )
    stub.add_response(
        "create_tags",
        {},
        {"Resources": ["sgr-cur"], "Tags": [{"Key": "maezo:last-seen", "Value": STAMP}]},
    )
    with stub:
        out = handler.sync(ec2, _config(), _resolver({LOGIN: failure, IDP: ["18.228.1.1"]}), now=NOW)
    stub.assert_no_pending_responses()  # nenhum revoke
    assert out["resolution_ok"] is False and out["removed"] == []


def test_respeita_max_rules() -> None:
    ec2, stub = _client()
    _describe(stub, [_managed("sgr-m1", "18.229.1.1", LOGIN, NOW)])
    stub.add_response(
        "create_tags",
        {},
        {"Resources": ["sgr-m1"], "Tags": [{"Key": "maezo:last-seen", "Value": STAMP}]},
    )
    _authorize(stub, "18.229.1.2", LOGIN, "sgr-new2")
    with stub:
        out = handler.sync(
            ec2,
            _config(hosts=(LOGIN,), max_rules=2),
            _resolver({LOGIN: ["18.229.1.1", "18.229.1.2", "18.229.1.3", "18.229.1.4"]}),
            now=NOW,
        )
    stub.assert_no_pending_responses()
    assert [c["ip"] for c in out["created"]] == ["18.229.1.2"]
    assert out["skipped_max_rules"] == ["18.229.1.3", "18.229.1.4"]


@pytest.mark.parametrize("bad_ip", ["10.40.40.27", "127.0.0.1", "169.254.169.254", "100.64.0.1", "0.0.0.0"])
def test_recusa_ip_privado_ou_reservado(bad_ip: str) -> None:
    ec2, stub = _client()
    with stub, pytest.raises(handler.NonPublicAddressError):
        handler.sync(ec2, _config(), _resolver({LOGIN: ["18.229.1.1", bad_ip], IDP: ["18.228.1.1"]}), now=NOW)
    stub.assert_no_pending_responses()  # abortou antes de qualquer chamada ao EC2


def test_duplicate_na_criacao_nao_derruba_a_rodada() -> None:
    ec2, stub = _client()
    _describe(stub, [])
    stub.add_client_error("authorize_security_group_egress", service_error_code="InvalidPermission.Duplicate")
    with stub:
        out = handler.sync(ec2, _config(hosts=(LOGIN,)), _resolver({LOGIN: ["18.229.1.1"]}), now=NOW)
    assert out["created"] == []


def test_config_from_env() -> None:
    cfg = handler.Config.from_env(
        {"SECURITY_GROUP_ID": SG, "HOSTS": f" {LOGIN} ,{IDP},", "STALE_HOURS": "12", "MAX_RULES": "5"}
    )
    assert cfg.hosts == (LOGIN, IDP) and cfg.stale_hours == 12.0 and cfg.max_rules == 5
    with pytest.raises(ValueError):
        handler.Config.from_env({"HOSTS": LOGIN})
