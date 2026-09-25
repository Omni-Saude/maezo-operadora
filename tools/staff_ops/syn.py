"""`syn`: runner in-VPC da fixture sintetica `SYN-` (runbook portal-dev-provisionamento §10, #524).

Le `STAFF_SYN_SECRET_ARN` (`staff-syn-runner.v1`): a configuracao `dev-syn-fixture.v1` SEM os
caminhos de arquivo e os arquivos em base64 (certificado/chave mTLS do cliente, CA do servidor
nativo e o certificado do assinante de resultado AUTH). A DSN do dono vem do segredo do dono
(`STAFF_OWNER_SECRET_ARN`) e e montada aqui. Tudo vai para arquivos 0400 no volume efemero
`/run/dev-syn`; a ferramenta so recebe caminhos. As cercas continuam as da ferramenta
(`guards`): conta 203312548462, ambiente dev, tenant amh, guia `^SYN-[A-Z0-9]+$` — e este runner
recusa antes, por conta propria, qualquer guia sem o prefixo.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import quote

from .common import OpsError, b64, env, secret_json, secrets_client, write_private

RUN = Path("/run/dev-syn")
SCHEMA = "staff-syn-runner.v1"
FILES = {
    "client_certificate": "client.pem",
    "client_key": "client-key.pem",
    "native_ca": "native-ca.pem",
    "result_certificate": "auth-result.pem",
}
OWNER = "maezo_native_schema_owner"
RDS_CA = "/usr/local/share/ca-certificates/maezo-rds-sa-east-1"


def build(document: dict, owner_password: str, host: str, port: str, database: str, run: Path = RUN) -> Path:  # type: ignore[type-arg]
    if set(document) != {"schema", "config", "files"} or document["schema"] != SCHEMA:
        raise OpsError(f"segredo SYN: esperado {SCHEMA}")
    config = document["config"]
    guide = config.get("guide_number") if isinstance(config, dict) else None
    if not isinstance(guide, str) or not re.fullmatch(r"SYN-[A-Z0-9]+", guide):
        raise OpsError("guia sem prefixo SYN-: recusado")
    files = document["files"]
    if (
        not isinstance(files, dict)
        or set(files) - set(FILES)
        or set(FILES) - {"result_certificate"} - set(files)
    ):
        raise OpsError("arquivos do segredo SYN fora da allowlist")
    for name, leaf in FILES.items():
        if name in files:
            write_private(run / leaf, b64(files[name], name))
    dsn = f"postgresql://{OWNER}:{quote(owner_password, safe='')}@{host}:{port}/{database}"
    write_private(run / "owner-dsn", dsn.encode())
    value = dict(config)
    value["native"] = dict(
        config["native"],
        ca_file=str(run / "native-ca.pem"),
        client_certificate_file=str(run / "client.pem"),
        client_key_file=str(run / "client-key.pem"),
    )
    value["database"] = dict(config["database"], owner_dsn_file=str(run / "owner-dsn"), ca_file=_rds_ca(run))
    if "result_certificate" in files:
        value["result_signer"] = dict(config["result_signer"], certificate_file=str(run / "auth-result.pem"))
    path = run / "config.json"
    write_private(path, json.dumps(value).encode())
    return path


def _rds_ca(run: Path) -> str:
    """As raizes RDS pinadas que a imagem instalou (um .crt por raiz) num unico PEM."""
    directory = Path(RDS_CA)
    if not directory.is_dir():
        raise OpsError("raizes RDS da imagem ausentes")
    pem = b"".join(p.read_bytes() for p in sorted(directory.glob("*.crt")))
    if pem.count(b"BEGIN CERTIFICATE") != 3:
        raise OpsError("raizes RDS da imagem inesperadas")
    target = run / "rds-ca.pem"
    write_private(target, pem)
    return str(target)


def main() -> int:
    from tools.staff_install.installer import parse_credential

    try:
        client = secrets_client()
        document = secret_json(client, env("STAFF_SYN_SECRET_ARN"))
        password = parse_credential(
            client.get_secret_value(SecretId=env("STAFF_OWNER_SECRET_ARN"))["SecretString"], OWNER
        )
        path = build(document, password, env("DB_HOST"), env("DB_PORT"), env("DB_NAME"))
    except OpsError as failure:
        print(f"recusado: {failure}", file=sys.stderr)
        return 3
    except Exception as failure:
        print(f"falhou: {type(failure).__name__}", file=sys.stderr)
        return 1
    os.environ["MAEZO_DEV_SYN_FIXTURE_FILE"] = str(path)
    from tools.dev_syn_fixture.__main__ import main as fixture_main

    return int(fixture_main() or 0)
