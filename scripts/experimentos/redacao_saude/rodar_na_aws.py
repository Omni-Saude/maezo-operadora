"""Dispara o experimento "redacao de saude" como TAREFA ECS AVULSA no dev e baixa os resultados.

    python scripts/experimentos/redacao_saude/rodar_na_aws.py disparar [--ref origin/main] [-- <args do experimento>]
    python scripts/experimentos/redacao_saude/rodar_na_aws.py baixar --data AAAA-MM-DD --para <pasta>

`disparar`:
  1. pega `experimento.py` e `perguntas.yaml` EXATAMENTE como estao no commit `--ref` (padrao `origin/main`:
     so' roda o que foi mesclado — nada configurado so' no notebook) e grava os dois em
     `s3://<bucket do simulador>/experimentos/redacao-saude/codigo/<sha>/` (SSE-KMS, a CMK do bucket);
  2. `ecs run-task` da task definition do SIMULADOR (`maezo-operadora-dev-simulador`: a mesma imagem, a mesma
     rede, a role `simulador-task`), com o COMANDO sobrescrito: um carregador de 6 linhas que le' o
     `experimento.py` do S3 e chama `main()` (a imagem nao copia `scripts/`). Nao muda a task definition, nao
     toca o receptor nem os agentes.
  Resultados: `s3://<bucket>/experimentos/redacao-saude/<data>/` (manifesto, respostas, juizes, resumo).

`baixar`: copia os resultados de uma data para uma pasta local (para `planilha.py`).

Usa as credenciais do perfil AWS ativo (`AWS_PROFILE=amh-data-dev`).
"""

from __future__ import annotations

import argparse
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
PREFIXO = "experimentos/redacao-saude"
# A mesma rede do runbook do simulador (`docs/runbooks/simulador-beneficiario.md`).
SUBNETS = ["subnet-06647b0c664d0d22a", "subnet-0e1f840dbec44e66a"]
SECURITY_GROUPS = ["sg-0e2aebe1a2c1d253d"]
_PASTA = "scripts/experimentos/redacao_saude"

CARREGADOR = (
    "import os,sys,types,boto3\n"
    "b=os.environ['MAEZO_SIMULADOR_BUCKET'];k=os.environ['EXP_CODIGO']\n"
    "src=boto3.client('s3',region_name='sa-east-1').get_object(Bucket=b,Key=k)['Body'].read()\n"
    "m=types.ModuleType('experimento');m.__file__='experimento.py';sys.modules['experimento']=m\n"
    "exec(compile(src,'experimento.py','exec'),m.__dict__)\n"
    "sys.exit(m.main(sys.argv[1:]))\n"
)


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout


def _env_da_task(ecs: Any) -> dict[str, str]:
    td = ecs.describe_task_definition(taskDefinition=TASK_DEF)["taskDefinition"]
    cont = next(c for c in td["containerDefinitions"] if c["name"] == CONTAINER)
    return {e["name"]: e["value"] for e in cont.get("environment", [])}


def disparar(args: argparse.Namespace, extras: list[str]) -> int:
    import boto3

    sha = _git("rev-parse", args.ref).strip()
    ecs = boto3.client("ecs", region_name=REGIAO)
    s3 = boto3.client("s3", region_name=REGIAO)
    env = _env_da_task(ecs)
    bucket, kms = env["MAEZO_SIMULADOR_BUCKET"], env["MAEZO_SIMULADOR_KMS_KEY"]
    codigo = f"{PREFIXO}/codigo/{sha}"
    for nome in ("experimento.py", "perguntas.yaml"):
        conteudo = subprocess.run(
            ["git", "show", f"{sha}:{_PASTA}/{nome}"], check=True, capture_output=True
        ).stdout
        s3.put_object(
            Bucket=bucket,
            Key=f"{codigo}/{nome}",
            Body=conteudo,
            ServerSideEncryption="aws:kms",
            SSEKMSKeyId=kms,
        )
    data = args.data or datetime.now(UTC).date().isoformat()
    comando = [
        "python",
        "-c",
        CARREGADOR,
        "--perguntas",
        f"s3://{bucket}/{codigo}/perguntas.yaml",
        "--saida",
        f"s3://{bucket}/{PREFIXO}/{data}",
        *extras,
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
            {"key": "MaezoPurpose", "value": "experimento-redacao-saude"},
            {"key": "MaezoOwner", "value": dono},
        ],
        overrides={
            "containerOverrides": [
                {
                    "name": CONTAINER,
                    "command": comando,
                    "environment": [
                        {"name": "EXP_CODIGO", "value": f"{codigo}/experimento.py"},
                        {"name": "EXP_GIT_SHA", "value": sha},
                    ],
                }
            ]
        },
    )
    if r.get("failures"):
        print(json.dumps(r["failures"], indent=2))
        return 1
    tarefa = r["tasks"][0]["taskArn"]
    print(json.dumps({"tarefa": tarefa, "sha": sha, "saida": f"s3://{bucket}/{PREFIXO}/{data}/"}, indent=2))
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
    pagina = s3.list_objects_v2(Bucket=bucket, Prefix=f"{PREFIXO}/{args.data}/")
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
    p = argparse.ArgumentParser(
        description="experimento redacao de saude: disparar no ECS / baixar resultados"
    )
    sub = p.add_subparsers(dest="acao", required=True)
    d = sub.add_parser("disparar")
    d.add_argument("--ref", default="origin/main")
    d.add_argument("--data", default="", help="pasta de saida (padrao: hoje, UTC)")
    b = sub.add_parser("baixar")
    b.add_argument("--data", required=True)
    b.add_argument("--para", required=True)
    args = p.parse_args(argv)
    return disparar(args, extras) if args.acao == "disparar" else baixar(args)


if __name__ == "__main__":
    sys.exit(main())
