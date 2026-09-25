"""Runtime do job T1.5 (`membership_publication_job`) no ECS: `job-init` + `job-run`.

`job-init` (init container, root so para o fchown -> 1000, rootfs read-only): recebe o segredo
`maezo-operadora/dev/staff-job/materials` INTEIRO, versao pinada, pelo `secrets` do ECS em
`STAFF_JOB_MATERIALS` (objeto `{"human/<nome>" | "job/<nome>": "<base64>"}`) e escreve:

* ``<INIT_ROOT>/human/current/`` os 15 arquivos do `portal-human-material.v1` (0400, uid 1000,
  diretorio 0500) — o job monta o volume READ-ONLY em `/run/maezo-human-materials`, exatamente o
  que `production_materials.read_material_directory` exige;
* ``<INIT_ROOT>/job/`` configuracao, certificado/chave mTLS do job, chaves de publicacao e de
  autoridade e a DSN de identidade (0400) — montado READ-ONLY em `/run/maezo-job`.

Allowlist fechada: nome a mais, a menos, repetido, base64 nao canonico ou volume nao vazio = sai 1.

`job-run`: o ledger CAS do job (`PublicationLedger`, um arquivo JSON) e duravel em S3 VERSIONADO
(`STAFF_JOB_LEDGER_BUCKET`/`STAFF_JOB_LEDGER_KEY`): baixa antes da rodada para o volume efemero,
roda `publish`, e sobe de volta SO se a rodada saiu 0. Ledger ausente no bucket = primeira rodada.
A ultima linha e sempre `T15_RESULT ok=<true|false>` (a metrica do alarme de 2 falhas seguidas).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from .common import OpsError, b64, env, parse_json_object, write_private

INIT_ROOT = Path("/run/staff-job-init")
OWNER = 1000
JOB_FILES = frozenset(
    {
        "config.json",
        "client-certificate.pem",
        "client-key.pem",
        "publication-signing-key.pem",
        "authority-signing-key.pem",
        "identity-dsn.txt",
    }
)
#: Onda 8 (H2): a fonte nativa de tarefas e o catalogo/admissao que ela publica. Tudo ou nada.
TASK_FILES = frozenset({"native-dsn.txt", "task-catalog.json", "task-admission.json"})
LEDGER_PATH = Path("/run/staff-job-ledger/ledger.json")
CONFIG_PATH = "/run/maezo-job/config.json"


def human_files() -> frozenset[str]:
    from maezo.gateway.human.production_materials import FILES

    return frozenset(FILES) | {"manifest.json"}


def decode(raw: str) -> tuple[dict[str, bytes], dict[str, bytes]]:
    document = parse_json_object(raw, "segredo do job")
    human: dict[str, bytes] = {}
    job: dict[str, bytes] = {}
    for name, value in document.items():
        prefix, _, leaf = name.partition("/")
        if "/" in leaf or leaf in ("", ".", ".."):
            raise OpsError("nome invalido no segredo do job")
        target = {"human": human, "job": job}.get(prefix)
        if target is None:
            raise OpsError("prefixo invalido no segredo do job")
        target[leaf] = b64(value, "arquivo do job")
    if frozenset(human) != human_files() or frozenset(job) not in (JOB_FILES, JOB_FILES | TASK_FILES):
        raise OpsError("segredo do job fora da allowlist fechada")
    return human, job


def _empty_directory(path: Path) -> None:
    if path.is_symlink() or not path.is_dir() or any(path.iterdir()):
        raise OpsError("volume de destino ausente, symlink ou nao vazio")


def materialize(
    human: dict[str, bytes], job: dict[str, bytes], root: Path = INIT_ROOT, owner: int | None = OWNER
) -> None:
    human_root, job_root = root / "human", root / "job"
    for volume in (human_root, job_root):
        _empty_directory(volume)
    current = human_root / "current"
    current.mkdir(mode=0o700)
    for name, data in sorted(human.items()):
        write_private(current / name, data, owner=owner)
    for name, data in sorted(job.items()):
        write_private(job_root / name, data, owner=owner)
    # chmod ANTES do chown: o init roda sem FOWNER, entao root nao muda o modo do que ja e de 1000.
    for directory in (current, job_root):
        os.chmod(directory, 0o500)
        if owner is not None:
            os.chown(directory, owner, owner)
    os.chmod(human_root, 0o555)
    if owner is not None:
        os.chown(human_root, owner, owner)


def init_main() -> int:
    try:
        human, job = decode(env("STAFF_JOB_MATERIALS"))
        materialize(human, job)
    except Exception as failure:
        print(f"job-init recusado: {type(failure).__name__}", file=sys.stderr)  # sem nome nem conteudo
        return 1
    print(f"job-init ok human={len(human)} job={len(job)}")
    return 0


def _s3():
    import boto3  # type: ignore[import-untyped]

    return boto3.client("s3", region_name=os.environ.get("AWS_REGION", "sa-east-1"))


def fetch_ledger(client, bucket: str, key: str, path: Path = LEDGER_PATH) -> str:  # type: ignore[no-untyped-def]
    try:
        body = client.get_object(Bucket=bucket, Key=key)
    except client.exceptions.NoSuchKey:
        return "absent"
    data = body["Body"].read()
    json.loads(data)  # ledger ilegivel = falha, nunca rodada "do zero" em silencio
    write_private(path, data, mode=0o600)
    return body.get("VersionId") or "unversioned"


def store_ledger(client, bucket: str, key: str, path: Path = LEDGER_PATH) -> str:  # type: ignore[no-untyped-def]
    response = client.put_object(
        Bucket=bucket,
        Key=key,
        Body=path.read_bytes(),
        ContentType="application/json",
        ServerSideEncryption="AES256",
    )
    return str(response.get("VersionId") or "unversioned")


def run_main() -> int:
    ok, detail = False, "falha"
    try:
        bucket, key = env("STAFF_JOB_LEDGER_BUCKET"), env("STAFF_JOB_LEDGER_KEY")
        env("MAEZO_HUMAN_MATERIAL_VERSION_ID")
        env("MAEZO_HUMAN_PUBLIC_MANIFEST_SHA256")
        client = _s3()
        before = fetch_ledger(client, bucket, key)
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "maezo.gateway.human.membership_publication_job",
                "publish",
                "--config",
                CONFIG_PATH,
            ],
            capture_output=True,
            text=True,
            timeout=180,
        )
        # O job so imprime o JobResult publico (contagens) ou `publication refused: <Tipo>`.
        last = (completed.stdout.strip().splitlines() or completed.stderr.strip().splitlines() or [""])[-1][
            :400
        ]
        print(last)
        if completed.returncode == 0:
            after = store_ledger(client, bucket, key)
            ok, detail = True, f"ledger {before}->{after}"
        else:
            detail = f"rc={completed.returncode}"
    except Exception as failure:
        detail = type(failure).__name__
    print(f"T15_RESULT ok={'true' if ok else 'false'} {detail}")
    return 0 if ok else 2
