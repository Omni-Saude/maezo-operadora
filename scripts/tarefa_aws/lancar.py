"""Roda um script do repo como TAREFA ECS AVULSA no dev (a task definition do simulador, comando sobrescrito).

    python scripts/tarefa_aws/lancar.py disparar --script scripts/<pasta>/<arquivo>.py --prefixo <prefixo> \
        [--ref origin/main] [--data AAAA-MM-DD] [-- <args do script>]
    python scripts/tarefa_aws/lancar.py baixar --prefixo <prefixo> --data AAAA-MM-DD --para <pasta>

`disparar`:
  1. monta um PACOTE (`git archive`, tar.gz) EXATAMENTE como esta' no commit `--ref` (padrao `origin/main`: so'
     roda o que foi mesclado — nada configurado so' no notebook) com `src/maezo`, `spec/`, `tests/support/` e a
     pasta do script, e grava em `s3://<bucket do simulador>/<prefixo>/codigo/<sha>-<hash>.tar.gz` (SSE-KMS, a CMK do
     bucket). O pacote leva o CODIGO DO COMMIT: o script importa a Helena/base/redator dele, nao os da imagem;
  2. `ecs run-task` da task definition do SIMULADOR (`maezo-operadora-dev-simulador`: a mesma imagem, a mesma
     rede, a role `simulador-task`), com o COMANDO sobrescrito por um carregador curto que baixa o pacote,
     extrai em `/dev/shm/codigo` (a raiz do container e' SO' LEITURA; `/dev/shm` e' o unico tmpfs), poe `src/` na frente do `sys.path` e executa o script. Nao muda a task
     definition, nao toca o receptor nem os agentes.
  O script recebe `--saida s3://<bucket>/<prefixo>/<data>` e os `--` extras.

`baixar`: copia os arquivos de `<prefixo>/<data>/` para uma pasta local.

Usa as credenciais do perfil AWS ativo (`AWS_PROFILE=amh-data-dev`). A role da tarefa so' le/grava os
prefixos liberados em `deploy/aws-ecs/envs/dev-sa-east-1/experimento-redacao-saude.tf`.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REGIAO = "sa-east-1"
CLUSTER = "maezo-operadora-dev"
TASK_DEF = "maezo-operadora-dev-simulador"
CONTAINER = "simulador"
# A mesma rede do runbook do simulador (`docs/runbooks/simulador-beneficiario.md`).
SUBNETS = ["subnet-06647b0c664d0d22a", "subnet-0e1f840dbec44e66a"]
SECURITY_GROUPS = ["sg-0e2aebe1a2c1d253d"]
#: O que vai no pacote alem da pasta do script.
CAMINHOS_DO_PACOTE = ("src/maezo", "spec", "tests/__init__.py", "tests/support", "scripts/tarefa_aws")

CARREGADOR = (
    "import io,os,runpy,sys,tarfile,boto3\n"
    "b=os.environ['MAEZO_SIMULADOR_BUCKET'];k=os.environ['TAREFA_PACOTE'];d='/dev/shm/codigo'\n"
    "o=boto3.client('s3',region_name='sa-east-1').get_object(Bucket=b,Key=k)['Body'].read()\n"
    "tarfile.open(fileobj=io.BytesIO(o),mode='r:gz').extractall(d,filter='data')\n"
    "sys.path[:0]=[d+'/src',d];os.chdir(d)\n"
    "s=d+'/'+os.environ['TAREFA_SCRIPT'];sys.argv=[s]+sys.argv[1:]\n"
    "runpy.run_path(s,run_name='__main__')\n"
)


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout


def _env_da_task(ecs: Any) -> dict[str, str]:
    td = ecs.describe_task_definition(taskDefinition=TASK_DEF)["taskDefinition"]
    cont = next(c for c in td["containerDefinitions"] if c["name"] == CONTAINER)
    return {e["name"]: e["value"] for e in cont.get("environment", [])}


def pacote(sha: str, script: str, incluir: list[str] | None = None) -> bytes:
    pasta = str(Path(script).parent.as_posix())
    caminhos = list(dict.fromkeys([*CAMINHOS_DO_PACOTE, pasta, *(incluir or [])]))
    return subprocess.run(
        ["git", "archive", "--format=tar.gz", sha, *caminhos], check=True, capture_output=True
    ).stdout


def disparar(args: argparse.Namespace, extras: list[str]) -> int:
    import boto3

    sha = _git("rev-parse", args.ref).strip()
    ecs = boto3.client("ecs", region_name=REGIAO)
    s3 = boto3.client("s3", region_name=REGIAO)
    env = _env_da_task(ecs)
    bucket, kms = env["MAEZO_SIMULADOR_BUCKET"], env["MAEZO_SIMULADOR_KMS_KEY"]
    prefixo = args.prefixo.strip("/")
    corpo = pacote(sha, args.script, args.incluir)
    # O nome leva o hash do PACOTE: dois disparos do mesmo commit com scripts diferentes nao se sobrescrevem.
    chave = f"{prefixo}/codigo/{sha}-{hashlib.sha256(corpo).hexdigest()[:12]}.tar.gz"
    s3.put_object(
        Bucket=bucket,
        Key=chave,
        Body=corpo,
        ServerSideEncryption="aws:kms",
        SSEKMSKeyId=kms,
    )
    data = args.data or datetime.now(UTC).date().isoformat()
    saida = f"s3://{bucket}/{prefixo}/{data}"
    ambiente = [
        {"name": "TAREFA_PACOTE", "value": chave},
        {"name": "TAREFA_SCRIPT", "value": Path(args.script).as_posix()},
        {"name": "TAREFA_GIT_SHA", "value": sha},
        *({"name": k, "value": v} for k, v in (item.split("=", 1) for item in args.env)),
    ]
    dono = boto3.client("sts", region_name=REGIAO).get_caller_identity()["UserId"]
    r = ecs.run_task(
        cluster=CLUSTER,
        launchType="FARGATE",
        taskDefinition=TASK_DEF,
        enableExecuteCommand=False,
        networkConfiguration={
            "awsvpcConfiguration": {
                "subnets": SUBNETS,
                "securityGroups": SECURITY_GROUPS,
                "assignPublicIp": "DISABLED",
            }
        },
        tags=[
            {"key": "MaezoPurpose", "value": args.proposito},
            {"key": "MaezoOwner", "value": dono},
        ],
        overrides={
            "containerOverrides": [
                {
                    "name": CONTAINER,
                    "command": ["python", "-c", CARREGADOR, "--saida", saida, *extras],
                    "environment": ambiente,
                }
            ]
        },
    )
    if r.get("failures"):
        print(json.dumps(r["failures"], indent=2))
        return 1
    tarefa = r["tasks"][0]["taskArn"]
    print(json.dumps({"tarefa": tarefa, "sha": sha, "saida": f"{saida}/"}, indent=2))
    print(
        f"logs: /ecs/maezo-operadora-dev/simulador, stream simulador/{CONTAINER}/{tarefa.rsplit('/', 1)[-1]}"
    )
    return 0


def baixar(args: argparse.Namespace) -> int:
    import boto3

    ecs = boto3.client("ecs", region_name=REGIAO)
    s3 = boto3.client("s3", region_name=REGIAO)
    bucket = _env_da_task(ecs)["MAEZO_SIMULADOR_BUCKET"]
    destino = Path(args.para)
    destino.mkdir(parents=True, exist_ok=True)
    paginas = s3.get_paginator("list_objects_v2").paginate(
        Bucket=bucket, Prefix=f"{args.prefixo.strip('/')}/{args.data}/"
    )
    for pagina in paginas:
        for obj in pagina.get("Contents", []):
            nome = obj["Key"].rsplit("/", 1)[-1]
            s3.download_file(bucket, obj["Key"], str(destino / nome))
            print(destino / nome)
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    extras: list[str] = []
    if "--" in argv:
        i = argv.index("--")
        argv, extras = argv[:i], argv[i + 1 :]
    p = argparse.ArgumentParser(description="roda um script do repo como tarefa avulsa no ECS do dev")
    sub = p.add_subparsers(dest="acao", required=True)
    d = sub.add_parser("disparar")
    d.add_argument("--script", required=True, help="caminho do script no repo (scripts/...)")
    d.add_argument("--prefixo", required=True, help="prefixo no bucket do simulador")
    d.add_argument("--ref", default="origin/main")
    d.add_argument("--data", default="", help="pasta de saida (padrao: hoje, UTC)")
    d.add_argument("--proposito", default="tarefa-avulsa-dev", help="tag MaezoPurpose")
    d.add_argument("--env", action="append", default=[], help="NOME=valor extra no container (repetivel)")
    d.add_argument(
        "--incluir", action="append", default=[], help="outro caminho do repo no pacote (repetivel)"
    )
    b = sub.add_parser("baixar")
    b.add_argument("--prefixo", required=True)
    b.add_argument("--data", required=True)
    b.add_argument("--para", required=True)
    args = p.parse_args(argv)
    return disparar(args, extras) if args.acao == "disparar" else baixar(args)


if __name__ == "__main__":
    sys.exit(main())
