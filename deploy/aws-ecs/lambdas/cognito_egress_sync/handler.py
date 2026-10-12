"""Sincroniza o egresso 443 do SG do portal com os IPs atuais do Cognito.

Os hosts do Cognito (dominio do managed login e ``cognito-idp``) nao tem IP fixo e o
PrivateLink nao cobre os endpoints de dominio; a lista ``/32`` manual do Terraform
(``portal.auto.tfvars``) quebrou o login a cada rotacao. Esta rotina, agendada, resolve os
hosts (DNS publico) e mantem regras ``/32`` PROPRIAS no SG, marcadas pela tag
``maezo:managed-by=cognito-egress-sync``. Ela nunca altera nem remove regras sem essa tag
(as do Terraform).

Invariantes (ver docs/runbooks/portal-egresso-cognito.md):
  * so cria ``tcp/443`` ``/32`` para IP PUBLICO; IP privado/reservado aborta a rodada inteira;
  * nao cria regra para IP ja coberto por QUALQUER regra existente;
  * remove so regras proprias com ``maezo:last-seen`` mais velho que ``STALE_HOURS``, e nada se
    alguma resolucao falhar/vier vazia; nunca remove a ultima regra propria de um host;
  * no maximo ``MAX_RULES`` regras proprias.

So stdlib + boto3 (runtime python3.12 da Lambda).
"""

from __future__ import annotations

import ipaddress
import json
import os
import socket
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

MANAGED_BY_KEY = "maezo:managed-by"
MANAGED_BY_VALUE = "cognito-egress-sync"
HOST_KEY = "maezo:host"
LAST_SEEN_KEY = "maezo:last-seen"
PORT = 443
METRIC_NAMESPACE_DEFAULT = "maezo-operadora/dev/portal"

Resolver = Callable[[str], set[str]]


class NonPublicAddressError(RuntimeError):
    """Um host resolveu para IP privado/reservado: a rodada aborta sem mudar nada."""


@dataclass(frozen=True)
class Config:
    security_group_id: str
    hosts: tuple[str, ...]
    stale_hours: float = 24.0
    max_rules: int = 20
    resolve_rounds: int = 5
    metric_namespace: str = METRIC_NAMESPACE_DEFAULT

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> Config:
        env = dict(os.environ if env is None else env)
        hosts = tuple(h.strip().lower() for h in env.get("HOSTS", "").split(",") if h.strip())
        sg = env.get("SECURITY_GROUP_ID", "").strip()
        if not sg.startswith("sg-") or not hosts:
            raise ValueError("SECURITY_GROUP_ID (sg-...) e HOSTS sao obrigatorios")
        return cls(
            security_group_id=sg,
            hosts=hosts,
            stale_hours=float(env.get("STALE_HOURS", "24")),
            max_rules=int(env.get("MAX_RULES", "20")),
            resolve_rounds=max(1, int(env.get("RESOLVE_ROUNDS", "5"))),
            metric_namespace=env.get("METRIC_NAMESPACE", METRIC_NAMESPACE_DEFAULT),
        )


@dataclass
class ManagedRule:
    rule_id: str
    ip: str
    host: str
    last_seen: datetime | None


def log(event: str, **fields: Any) -> None:
    """Uma linha JSON por evento. So contagens, IDs de regra e IPs publicos da AWS."""
    print(json.dumps({"event": event, **fields}, sort_keys=True, default=str))


def emit_metrics(namespace: str, sg_id: str, values: dict[str, int]) -> None:
    """CloudWatch Embedded Metric Format: vira metrica sem permissao extra (so logs)."""
    payload: dict[str, Any] = {
        "_aws": {
            "Timestamp": int(datetime.now(UTC).timestamp() * 1000),
            "CloudWatchMetrics": [
                {
                    "Namespace": namespace,
                    "Dimensions": [["SecurityGroupId"]],
                    "Metrics": [{"Name": name, "Unit": "Count"} for name in values],
                }
            ],
        },
        "SecurityGroupId": sg_id,
        **values,
    }
    print(json.dumps(payload, sort_keys=True))


def resolve_ipv4(host: str, rounds: int = 5) -> set[str]:
    """A records via resolver do sistema; varias consultas para pegar o conjunto rotativo."""
    found: set[str] = set()
    for _ in range(rounds):
        for info in socket.getaddrinfo(host, PORT, socket.AF_INET, socket.SOCK_STREAM):
            found.add(str(info[4][0]))
    return found


def _is_public(ip: str) -> bool:
    addr = ipaddress.ip_address(ip)
    return addr.version == 4 and addr.is_global and not addr.is_multicast


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _iso(ts: datetime) -> str:
    return ts.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _covers_443(rule: dict[str, Any]) -> bool:
    proto = str(rule.get("IpProtocol", ""))
    if proto == "-1":
        return True
    if proto not in ("tcp", "6"):
        return False
    return int(rule.get("FromPort", -1)) <= PORT <= int(rule.get("ToPort", -1))


def _list_egress_rules(ec2: Any, sg_id: str) -> list[dict[str, Any]]:
    rules: list[dict[str, Any]] = []
    kwargs: dict[str, Any] = {"Filters": [{"Name": "group-id", "Values": [sg_id]}]}
    while True:
        page = ec2.describe_security_group_rules(**kwargs)
        rules.extend(r for r in page.get("SecurityGroupRules", []) if r.get("IsEgress"))
        token = page.get("NextToken")
        if not token:
            return rules
        kwargs["NextToken"] = token


def _tags(rule: dict[str, Any]) -> dict[str, str]:
    return {t["Key"]: t["Value"] for t in rule.get("Tags", []) or []}


def _split(
    rules: Iterable[dict[str, Any]],
) -> tuple[list[ManagedRule], list[ipaddress.IPv4Network]]:
    managed: list[ManagedRule] = []
    foreign_nets: list[ipaddress.IPv4Network] = []
    for rule in rules:
        cidr = rule.get("CidrIpv4")
        tags = _tags(rule)
        if tags.get(MANAGED_BY_KEY) == MANAGED_BY_VALUE:
            if cidr:
                managed.append(
                    ManagedRule(
                        rule_id=rule["SecurityGroupRuleId"],
                        ip=str(ipaddress.ip_network(cidr, strict=False).network_address),
                        host=tags.get(HOST_KEY, ""),
                        last_seen=_parse_ts(tags.get(LAST_SEEN_KEY)),
                    )
                )
            continue
        if cidr and _covers_443(rule):
            foreign_nets.append(ipaddress.IPv4Network(cidr, strict=False))
    return managed, foreign_nets


def sync(
    ec2: Any,
    config: Config,
    resolver: Resolver | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Uma rodada de sincronizacao. Retorna o resumo (tambem logado)."""
    now = now or datetime.now(UTC)
    resolve = resolver or (lambda host: resolve_ipv4(host, config.resolve_rounds))
    sg = config.security_group_id

    # 1. Resolucao. Falha/vazio em QUALQUER host desliga a remocao nesta rodada.
    resolved: dict[str, set[str]] = {}
    resolution_ok = True
    for host in config.hosts:
        try:
            ips = set(resolve(host))
        except Exception as exc:  # DNS pode falhar de muitas formas
            log("resolve_failed", host=host, error=type(exc).__name__)
            ips = set()
        if not ips:
            resolution_ok = False
            log("resolve_empty", host=host)
        resolved[host] = ips

    non_public = sorted({ip for ips in resolved.values() for ip in ips if not _is_public(ip)})
    if non_public:
        log("abort_non_public_ip", ips=non_public, security_group_id=sg)
        emit_metrics(config.metric_namespace, sg, {"NonPublicAbort": 1})
        raise NonPublicAddressError(f"IP nao publico resolvido: {non_public}")

    # 2. Estado atual do SG.
    managed, foreign_nets = _split(_list_egress_rules(ec2, sg))
    managed_by_ip = {m.ip: m for m in managed}

    def covered_by_foreign(ip: str) -> bool:
        addr = ipaddress.IPv4Address(ip)
        return any(addr in net for net in foreign_nets)

    stamp = _iso(now)
    to_refresh: list[str] = []
    to_create: list[tuple[str, str]] = []
    seen_ips: set[str] = set()
    for host in config.hosts:
        for ip in sorted(resolved[host]):
            if ip in seen_ips:
                continue
            seen_ips.add(ip)
            if ip in managed_by_ip:
                to_refresh.append(managed_by_ip[ip].rule_id)
                managed_by_ip[ip].last_seen = now
            elif not covered_by_foreign(ip):
                to_create.append((host, ip))

    # 3. Atualiza last-seen das regras proprias ainda vistas.
    if to_refresh:
        ec2.create_tags(Resources=sorted(to_refresh), Tags=[{"Key": LAST_SEEN_KEY, "Value": stamp}])

    # 4. Cria as faltantes, respeitando o teto.
    created: list[dict[str, str]] = []
    skipped_cap: list[str] = []
    budget = config.max_rules - len(managed)
    for host, ip in to_create:
        if budget <= 0:
            skipped_cap.append(ip)
            continue
        rule_id = _authorize(ec2, sg, host, ip, stamp)
        if rule_id is None:
            continue
        budget -= 1
        created.append({"rule_id": rule_id, "ip": ip, "host": host})
        managed.append(ManagedRule(rule_id=rule_id, ip=ip, host=host, last_seen=now))
        log("rule_created", security_group_id=sg, rule_id=rule_id, ip=ip, host=host)
    if skipped_cap:
        log("max_rules_reached", max_rules=config.max_rules, skipped_ips=skipped_cap)

    # 5. Remove regras proprias velhas (nunca a ultima de um host; nada se a resolucao falhou).
    removed: list[dict[str, str]] = []
    if resolution_ok:
        cutoff = now - timedelta(hours=config.stale_hours)
        by_host: dict[str, list[ManagedRule]] = {}
        for rule in managed:
            by_host.setdefault(rule.host, []).append(rule)
        to_revoke: list[ManagedRule] = []
        for rules in by_host.values():
            stale = [r for r in rules if r.last_seen is None or r.last_seen < cutoff]
            if len(stale) == len(rules):
                # Todas velhas: preserva a mais recente (ultima regra do host).
                stale.sort(key=lambda r: r.last_seen or datetime.min.replace(tzinfo=UTC))
                stale = stale[:-1]
            to_revoke.extend(stale)
        if to_revoke:
            ids = sorted(r.rule_id for r in to_revoke)
            ec2.revoke_security_group_egress(GroupId=sg, SecurityGroupRuleIds=ids)
            for r in to_revoke:
                removed.append({"rule_id": r.rule_id, "ip": r.ip, "host": r.host})
                log("rule_removed", security_group_id=sg, rule_id=r.rule_id, ip=r.ip, host=r.host)
    elif managed:
        log("removal_skipped_resolution_failed", security_group_id=sg)

    summary = {
        "security_group_id": sg,
        "resolved": {h: sorted(ips) for h, ips in resolved.items()},
        "resolution_ok": resolution_ok,
        "managed_rules": len(managed) - len(removed),
        "refreshed": len(to_refresh),
        "created": created,
        "removed": removed,
        "skipped_max_rules": skipped_cap,
    }
    log("sync_summary", **summary)
    emit_metrics(
        config.metric_namespace,
        sg,
        {
            "RulesCreated": len(created),
            "RulesRemoved": len(removed),
            "RulesRefreshed": len(to_refresh),
            "ManagedRules": len(managed) - len(removed),
            "ResolveFailures": sum(1 for ips in resolved.values() if not ips),
            "MaxRulesSkipped": len(skipped_cap),
        },
    )
    return summary


def _authorize(ec2: Any, sg: str, host: str, ip: str, stamp: str) -> str | None:
    try:
        resp = ec2.authorize_security_group_egress(
            GroupId=sg,
            IpPermissions=[
                {
                    "IpProtocol": "tcp",
                    "FromPort": PORT,
                    "ToPort": PORT,
                    "IpRanges": [{"CidrIp": f"{ip}/32", "Description": f"cognito-egress-sync {host}"[:255]}],
                }
            ],
            TagSpecifications=[
                {
                    "ResourceType": "security-group-rule",
                    "Tags": [
                        {"Key": MANAGED_BY_KEY, "Value": MANAGED_BY_VALUE},
                        {"Key": HOST_KEY, "Value": host},
                        {"Key": LAST_SEEN_KEY, "Value": stamp},
                    ],
                }
            ],
        )
    except Exception as exc:
        code = getattr(exc, "response", {}).get("Error", {}).get("Code", type(exc).__name__)
        if code == "InvalidPermission.Duplicate":
            log("rule_duplicate", ip=ip, host=host)
            return None
        raise
    rules = resp.get("SecurityGroupRules") or [{}]
    return str(rules[0].get("SecurityGroupRuleId", "unknown"))


def lambda_handler(event: Any, context: Any) -> dict[str, Any]:
    import boto3  # import tardio: os testes injetam o client

    config = Config.from_env()
    return sync(boto3.client("ec2"), config)
