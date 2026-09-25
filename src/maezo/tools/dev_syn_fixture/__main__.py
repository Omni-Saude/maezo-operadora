"""`python -m maezo.tools.dev_syn_fixture`: a guia SINTETICA do teste do diretor (Onda 7) no DEV.

Roda DENTRO da VPC (task avulsa com a imagem da aplicacao ou o sidecar do emissor). Le a
configuracao de `MAEZO_DEV_SYN_FIXTURE_FILE` (JSON `dev-syn-fixture.v1`; segredos so por ARQUIVO,
nunca por env/override) e faz, idempotente:

1. B12: a linha `MZO_AUTH_INSTALLATION` do tenant com qualificacao SINTETICA apontando a
   SP-OP-AUTH-001 deployada (insere se ausente; se presente, so atualiza a definicao);
2. as 6 publicacoes `SYN-` e o `human-auth-start.v1` nativo da guia -> `AUTH-amh-{guia}`
   (pulado se a instancia ja existe);
3. a escalacao `ESC-amh-sla-auth-{guia}` com `motivo_categoria=red_flag_clinico`,
   `severidade=grave` (DMN r1 -> `plantao-clinico`, P1), conferindo o grupo candidato.

Cercas (`guards`): conta explicita 203312548462, ambiente `dev`, tenant `amh`, guia `^SYN-[A-Z0-9]+$`,
e recusa se o tenant ja tiver QUALQUER guia reivindicada nao-`SYN-`. So dado sintetico.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import ssl
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from maezo.portal.engine.profile import canonicalize

from . import core, guards

CONFIG_ENV = "MAEZO_DEV_SYN_FIXTURE_FILE"
SCHEMA = "dev-syn-fixture.v1"
MOTIVO = "red_flag_clinico"
SEVERIDADE = "grave"
EXPECTED_GROUP = "plantao-clinico"
_KEYS = {
    "schema",
    "guide_number",
    "auth_scope",
    "engine_rest_url",
    "native",
    "database",
    "installation",
    "result_signer",
}
_NATIVE = {"origin", "ca_file", "client_certificate_file", "client_key_file", "audience"}
_DATABASE = {"owner_dsn_file", "ca_file", "native_schema", "runtime_role"}
_INSTALLATION = {"native_code_digest", "valid_days"}
_SIGNER = {"certificate_file", "key_id", "issuer"}
_SCOPE = {
    "tenant",
    "environment",
    "engine_name",
    "database_incarnation",
    "installation_ref",
    "installation_revision",
}


class ConfigError(ValueError):
    """Configuracao invalida: nada e escrito."""


@dataclass(frozen=True)
class Config:
    raw: dict[str, Any]

    @property
    def guide_number(self) -> str:
        return str(self.raw["guide_number"])

    @property
    def scope(self) -> dict[str, Any]:
        scope: dict[str, Any] = self.raw["auth_scope"]
        return scope

    @property
    def tenant(self) -> str:
        return str(self.scope["tenant"])

    def refs(self) -> core.FixtureRefs:
        label = "dev-" + self.guide_number.removeprefix(core.PREFIX).lower()
        return core.FixtureRefs.for_label(label, self.guide_number, audience=self.raw["native"]["audience"])


def _closed(
    value: object, keys: set[str], where: str, optional: frozenset[str] = frozenset()
) -> dict[str, Any]:
    if not isinstance(value, dict) or not (keys - optional) <= set(value) <= keys:
        raise ConfigError(f"{where}: campos esperados {sorted(keys)}")
    return value


def load_config(raw: bytes, env: Mapping[str, str]) -> Config:
    """Valida a configuracao E as cercas. Qualquer desvio -> excecao antes de qualquer rede."""
    try:
        value = json.loads(raw)
    except ValueError as failure:
        raise ConfigError("configuracao nao e JSON") from failure
    _closed(value, _KEYS, "configuracao", optional=frozenset({"result_signer"}))
    if value["schema"] != SCHEMA:
        raise ConfigError(f"schema {value['schema']!r} != {SCHEMA}")
    scope = _closed(value["auth_scope"], _SCOPE, "auth_scope")
    _closed(value["native"], _NATIVE, "native")
    _closed(value["database"], _DATABASE, "database")
    installation = _closed(value["installation"], _INSTALLATION, "installation")
    if value.get("result_signer") is not None:
        _closed(value["result_signer"], _SIGNER, "result_signer")
    digest = installation["native_code_digest"]
    if not (isinstance(digest, str) and len(digest) == 64 and all(c in "0123456789abcdef" for c in digest)):
        raise ConfigError("installation.native_code_digest: sha256 hex")
    days = installation["valid_days"]
    if not isinstance(days, int) or isinstance(days, bool) or not 1 <= days <= 14:
        raise ConfigError("installation.valid_days: inteiro de 1 a 14")
    guards.check_target(
        env, environment=scope["environment"], tenant=scope["tenant"], guide_number=value["guide_number"]
    )
    config = Config(value)
    config.refs().check()
    return config


def _spki_sha256_of_certificate(pem: bytes) -> tuple[bytes, str]:
    from cryptography import x509
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    certificate = x509.load_pem_x509_certificate(pem)
    spki = certificate.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    return spki, hashlib.sha256(spki).hexdigest()


async def _installation(
    owner: Any, config: Config, definition: dict[str, Any], now: datetime
) -> tuple[str, dict[str, Any]]:
    """B12: insere a instalacao sintetica se ausente; senao confere o escopo e atualiza a definicao."""
    database, tenant = config.raw["database"], config.tenant
    schema = database["native_schema"]
    async with owner.transaction():
        await owner.execute(f"SET LOCAL search_path = {schema}")
        row = await owner.fetchrow(
            "SELECT scope_ FROM mzo_auth_installation WHERE tenant_=$1 FOR UPDATE", tenant
        )
        if row is None:
            ids = await owner.fetchrow(
                "SELECT d.oid::bigint AS db, n.oid::bigint AS ns, current_database() AS name, "
                "current_user AS owner "
                "FROM pg_database d, pg_namespace n WHERE d.datname=current_database() AND n.nspname=$1",
                schema,
            )
            binding = dict(
                schema="human-auth-native-database.v1",
                database_name=ids["name"],
                database_oid=str(ids["db"]),
                schema_name=schema,
                schema_oid=str(ids["ns"]),
                owner_role=ids["owner"],
                runtime_role=database["runtime_role"],
            )
            qualification = core.synthetic_qualification(
                definition,
                native_code_digest=config.raw["installation"]["native_code_digest"],
                label="dev",
                valid_until=now + timedelta(days=config.raw["installation"]["valid_days"]),
            )
            scope = config.scope
            await owner.execute(
                "INSERT INTO mzo_auth_installation(tenant_,incarnation_,rev_,scope_,binding_,qualification_) "
                "VALUES($1,$2,$3,$4,$5,$6)",
                tenant,
                scope["database_incarnation"],
                int(scope["installation_revision"]),
                canonicalize(scope).decode(),
                canonicalize(binding).decode(),
                canonicalize(qualification).decode(),
            )
            return "inserida", qualification["definition"]
        if json.loads(row["scope_"]) != config.scope:
            raise guards.DevGuardError("mzo_auth_installation com escopo diferente da configuracao: recusado")
    return "atualizada", await core.requalify(owner, schema, tenant, definition)


async def _db(config: Config, fn: Any, *args: Any) -> Any:
    import asyncpg  # type: ignore[import-untyped]  # no py.typed upstream

    database = config.raw["database"]
    dsn = Path(database["owner_dsn_file"]).read_text(encoding="utf-8").strip()  # noqa: ASYNC240
    context = ssl.create_default_context(cafile=database["ca_file"])
    owner = await asyncpg.connect(dsn, ssl=context, timeout=10)
    try:
        return await fn(owner, *args)
    finally:
        await owner.close()


def run(config: Config) -> dict[str, Any]:
    import httpx
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from maezo.gateway.human.auth_profile import Definition, Scope
    from maezo.gateway.human.read_profile import parse_model

    def now() -> datetime:
        return datetime.now(UTC)

    refs, tenant, schema = config.refs(), config.tenant, config.raw["database"]["native_schema"]
    report: dict[str, Any] = dict(guide=refs.guide_number, tenant=tenant)

    # Antes de QUALQUER escrita: nenhuma guia real reivindicada no tenant.
    guides = asyncio.run(_db(config, lambda o: core.claimed_guides(o, schema, tenant)))
    core.refuse_non_synthetic(guides)

    with httpx.Client(base_url=config.raw["engine_rest_url"], timeout=30, trust_env=False) as rest:
        definition = core.deployed_definition(rest, tenant)
        action, qualified = asyncio.run(_db(config, _installation, config, definition, now()))
        report["installation"] = dict(action=action, definition_id=qualified["definition_id"])

        if core.auth_instance_exists(rest, tenant, refs.guide_number):
            report["auth_start"] = "existing"
        else:
            native = config.raw["native"]
            start, until, end = (
                now() - timedelta(minutes=2),
                now() + timedelta(hours=2),
                now() + timedelta(hours=3),
            )
            _, peer = _spki_sha256_of_certificate(Path(native["client_certificate_file"]).read_bytes())
            keys = {purpose: Ed25519PrivateKey.generate() for purpose in refs.keys}  # efemeras, so em memoria
            inputs = core.build_inputs(refs, start, until, cutover="SYN-dev-cutover")
            designations = [
                core.key_designation(refs, p, k, peer, inputs.grants(refs), start, end)
                for p, k in keys.items()
            ]
            signer = config.raw.get("result_signer")
            if signer is not None:
                spki, _ = _spki_sha256_of_certificate(Path(signer["certificate_file"]).read_bytes())
                designations.append(
                    core.result_designation(spki, signer["key_id"], signer["issuer"], start, end)
                )
            asyncio.run(_db(config, core.designate, schema, tenant, designations))
            expected = asyncio.run(_db(config, core.input_generations, schema, tenant, inputs))
            context = ssl.create_default_context(cafile=native["ca_file"])
            context.load_cert_chain(native["client_certificate_file"], native["client_key_file"])
            with httpx.Client(
                base_url=native["origin"], verify=context, timeout=30, trust_env=False
            ) as client:
                outcome = core.publish_and_start(
                    client,
                    refs,
                    parse_model(Scope, config.scope),
                    parse_model(Definition, qualified),
                    keys,
                    peer,
                    inputs,
                    start=start,
                    until=until,
                    end=end,
                    now=now,
                    expected=expected,
                )
            report["publications"] = outcome.publications
            report["auth_start"] = f"{outcome.status} {outcome.outcome}"
            if not outcome.ok:
                report["error"] = json.dumps(outcome.body)[:300]
                return report

        escalation = core.open_escalation(
            rest,
            tenant,
            refs.guide_number,
            motivo=MOTIVO,
            severidade=SEVERIDADE,
            agent="SYN-dev-agent",
            conversation="SYN-dev-conversation",
            canal="SYN-dev",
            worker="SYN-dev-worker",
        )
    report["escalation"] = dict(
        key=escalation.key,
        created=escalation.created,
        tasks=escalation.tasks,
        groups=escalation.groups,
        auth_instances=escalation.auth_instances,
    )
    report["ok"] = (
        escalation.tasks >= 1
        and set(escalation.groups) == {EXPECTED_GROUP}
        and escalation.auth_instances == 1
    )
    return report


def main(env: Mapping[str, str] = os.environ) -> int:
    path = env.get(CONFIG_ENV)
    if not path:
        print(json.dumps(dict(ok=False, error=f"{CONFIG_ENV} ausente")), flush=True)
        return 2
    try:
        config = load_config(Path(path).read_bytes(), env)
        report = run(config)
    except (ConfigError, core.SyntheticRefusedError) as refused:
        print(json.dumps(dict(ok=False, refused=str(refused))), flush=True)
        return 3
    print(json.dumps(report, sort_keys=True), flush=True)
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
