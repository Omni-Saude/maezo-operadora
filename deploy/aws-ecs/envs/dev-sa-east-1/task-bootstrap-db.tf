# Bootstrap do banco — cria o que as migrations PRESSUPOEM e ninguem criava.
#
# Descoberto rodando de verdade (task 13/08/2026):
#   asyncpg.exceptions.InvalidCatalogNameError: database "maezo" does not exist
#
# `applications/maezo/migrations/README.md`, no repo da plataforma, afirma:
# "Schema `maezo` must exist (created by Terraform before any migration runs)".
# Nao e' verdade — nenhum Terraform, nem deles nem nosso, criava o database ou o
# schema. O cluster nasce com um database so' (`hapi`, do modulo aurora-cluster).
# Era mais uma afirmacao de documentacao que nunca tinha sido exercitada.
#
# Por que uma task e nao um script na maquina de alguem: nao ha rota da estacao de
# trabalho ate a subnet privada do Aurora, e um comando que vive no historico do
# terminal de um dev nao existe para o time. Aqui o bootstrap e' idempotente,
# versionado e reexecutavel — inclusive num cenario de restore.
#
# Roda como o usuario MESTRE (amh_admin), porque `maezo_app` nao tem CREATEDB nem
# privilegio para instalar extensao. E' o unico ponto do stack que toca a
# credencial mestre, e ele nao e' um service: roda, sai e pronto.

# ---------------------------------------------------------------------------
# Grant do segredo mestre — restrito a este ARN
# ---------------------------------------------------------------------------
# Com `manage_master_user_password = true` no modulo aurora-cluster, a AWS cria e
# roda o segredo `rds!cluster-...`. Ele fica FORA de qualquer prefixo nosso, e por
# isso precisa de um statement proprio.
data "aws_iam_policy_document" "task_execution_master_secret" {
  statement {
    sid       = "LerSegredoMestreDoAurora"
    effect    = "Allow"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [local.aurora_master_secret_arn]
  }
}

resource "aws_iam_role_policy" "task_execution_master_secret" {
  name   = "${local.name}-master-secret"
  role   = aws_iam_role.task_execution.id
  policy = data.aws_iam_policy_document.task_execution_master_secret.json
}

resource "aws_cloudwatch_log_group" "bootstrap_db" {
  name              = "/ecs/${local.name}/bootstrap-db"
  retention_in_days = var.log_retention_days
  tags              = local.base_tags
}

resource "aws_ecs_task_definition" "bootstrap_db" {
  family                   = "${local.name}-bootstrap-db"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = "512"
  memory                   = "1024"
  execution_role_arn       = aws_iam_role.task_execution.arn
  task_role_arn            = aws_iam_role.task.arn

  runtime_platform {
    cpu_architecture        = "X86_64"
    operating_system_family = "LINUX"
  }

  container_definitions = jsonencode([{
    name      = "bootstrap-db"
    image     = "${aws_ecr_repository.app.repository_url}:${var.image_tag}"
    essential = true

    # Script pelo STDIN (`python - <<'PY'`), nao por arquivo: a raiz e'
    # somente-leitura e um volume de task nasceria root:root, com o container
    # rodando como uid 1000. Foi assim que a primeira versao da task de migrations
    # falhou; aqui o problema nem existe.
    command = ["sh", "-c", <<-SH
      set -eu
      python - <<'PY'
      import asyncio, os
      import asyncpg


      async def main() -> None:
          host = os.environ["DB_HOST"]
          port = int(os.environ["DB_PORT"])
          user = os.environ["ADMIN_USER"]
          pwd = os.environ["ADMIN_PASSWORD"]
          alvo = os.environ["TARGET_DB"]
          dono = os.environ["APP_ROLE"]
          schema = os.environ["TENANT_SCHEMA"]

          # Fase 1 — no database que JA existe, cria o do maezo.
          # CREATE DATABASE nao aceita IF NOT EXISTS em Postgres, dai a checagem
          # explicita em pg_database (mantem a task idempotente).
          conn = await asyncpg.connect(
              host=host, port=port, user=user, password=pwd,
              database=os.environ["BOOTSTRAP_DB"],
          )
          try:
              # No RDS o usuario mestre NAO e' superusuario: para criar objeto
              # pertencente a outra role ele precisa ser MEMBRO dela. Sem isto a
              # primeira execucao morreu com
              # `InsufficientPrivilegeError: must be able to SET ROLE "maezo_app"`.
              # `amh_admin` herda de rds_superuser, que pode conceder a role.
              # GRANT repetido nao e' erro, entao segue idempotente.
              await conn.execute(f'GRANT "{dono}" TO CURRENT_USER')
              print(f"membership em {dono}: ok")

              existe = await conn.fetchval(
                  "select 1 from pg_database where datname = $1", alvo
              )
              if existe:
                  print(f"database {alvo}: ja existia")
              else:
                  await conn.execute(f'CREATE DATABASE "{alvo}" OWNER "{dono}"')
                  print(f"database {alvo}: criado, dono {dono}")
          finally:
              await conn.close()

          # Fase 2 — dentro do database do maezo.
          conn = await asyncpg.connect(
              host=host, port=port, user=user, password=pwd, database=alvo,
          )
          try:
              # A extensao `vector` era criada aqui, em `public` (DL-0017). DU-01-b
              # (decisao do dono R-005, 2026-09-04) removeu a camada semantica:
              # `0009_drop_pgvector` dropa a coluna `agent_memory.embedding` e a
              # extensao. Cria-la no bootstrap so para a migration seguinte remove-la
              # seria churn que contradiz a decisao — entao o bootstrap nao a cria mais.
              # ADR-0002 §3 fica suspenso ate existir consumidor (ADR-0047, DRAFT).

              # Schema do tenant. O env.py do alembic monta
              # search_path = "<tenant>, public" e cria as tabelas SEM qualificar,
              # entao o schema tem de existir antes.
              await conn.execute(
                  f'CREATE SCHEMA IF NOT EXISTS "{schema}" AUTHORIZATION "{dono}"'
              )
              print(f"schema {schema}: ok, dono {dono}")

              # USAGE (e nao CREATE) em public: a role precisa VER o tipo vector,
              # nao criar objeto la.
              await conn.execute(f'GRANT USAGE ON SCHEMA public TO "{dono}"')
              print(f"grant usage em public para {dono}: ok")

              # Schema do engine BPMN, com role propria. O container do CIB Seven
              # conecta com `?currentSchema=cibseven` e cria as tabelas dele ali
              # (DB_SCHEMA_UPDATE=true) — mas o SCHEMA em si ele nao cria, e sem
              # ele o boot falha. Separado do schema do tenant de proposito: o
              # estado do motor de processo nao se mistura com o dado do maezo, e
              # o V001 do repo da plataforma ja desenhava essa fronteira.
              engine_role = os.environ["ENGINE_ROLE"]
              engine_schema = os.environ["ENGINE_SCHEMA"]
              await conn.execute(f'GRANT "{engine_role}" TO CURRENT_USER')
              await conn.execute(
                  f'CREATE SCHEMA IF NOT EXISTS "{engine_schema}" '
                  f'AUTHORIZATION "{engine_role}"'
              )
              await conn.execute(f'GRANT USAGE ON SCHEMA public TO "{engine_role}"')
              print(f"schema {engine_schema}: ok, dono {engine_role}")
          finally:
              await conn.close()


      asyncio.run(main())
      PY
    SH
    ]

    environment = concat(local.db_env, [
      # O database onde a conexao da fase 1 e' aberta: `hapi`, o unico que o
      # cluster tem ao nascer (DatabaseName do modulo aurora-cluster).
      { name = "BOOTSTRAP_DB", value = var.aurora_bootstrap_database },
      { name = "TARGET_DB", value = var.aurora_database_name },
      { name = "APP_ROLE", value = "maezo_app" },
      # Schema = tenant id: e' o que o env.py usa em SEARCH_PATH.
      { name = "TENANT_SCHEMA", value = var.tenant_id },
      { name = "ENGINE_ROLE", value = "cibseven_app" },
      { name = "ENGINE_SCHEMA", value = "cibseven" },
      { name = "PYTHONDONTWRITEBYTECODE", value = "1" },
    ])

    secrets = [
      { name = "ADMIN_USER", valueFrom = "${local.aurora_master_secret_arn}:username::" },
      { name = "ADMIN_PASSWORD", valueFrom = "${local.aurora_master_secret_arn}:password::" },
    ]

    readonlyRootFilesystem = true

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.bootstrap_db.name
        "awslogs-region"        = var.aws_region
        "awslogs-stream-prefix" = "bootstrap"
      }
    }
  }])

  tags = local.base_tags
}
