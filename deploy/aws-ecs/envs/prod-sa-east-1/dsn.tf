# Composicao do DSN — o equivalente ECS do que o External Secrets Operator fazia
# no chart via `target.template`.
#
# O segredo `amh-aurora-hapi-dev/maezo_app` guarda campos SEPARADOS
# ({username, password, host, port, engine}); NAO existe uma propriedade
# `database_url` pronta. O ECS injeta campo a campo (sintaxe `arn:...:campo::`) e a
# URL e' montada no arranque do container.
#
# Por que via python e nao interpolando no shell: a senha e' gerada pela AWS e pode
# conter `@`, `/`, `#` ou `%`. Interpolada crua numa URL, ela quebra o parser em
# silencio — ou pior, muda o host de destino. `urllib.parse.quote(safe="")`
# percent-encoda usuario e senha, exatamente como o ESO fazia com `urlquery`.
# Efeito colateral util: o resultado nao contem espaco, aspa nem metacaractere de
# shell, entao a substituicao de comando abaixo e' segura.
#
# Por que substituicao de comando e NAO um arquivo em /tmp: a primeira versao
# escrevia /tmp/dsn.env e falhou em execucao real (task
# cf76229f8b3144d385e6fd2af2d61b3c, 13/08/2026) com
# `sh: cannot create /tmp/dsn.env: Permission denied`. Um volume de task do Fargate
# nasce root:root 0755 e o container roda como uid 1000 (deploy/Dockerfile) — nao
# ha `fsGroup` em ECS para corrigir a posse. Com a URL vindo de `$(...)` nao ha
# arquivo, a raiz continua somente-leitura e nenhum segredo toca o disco.
#
# O segredo nunca aparece em `ps`: a expansao acontece dentro do proprio shell.

locals {
  # Le DB_USER/DB_PASSWORD (de `secrets`), DB_HOST/DB_PORT/DB_NAME e DSN_SCHEME
  # (de `environment`) e imprime a URL. Sem aspas simples no corpo — ele viaja
  # dentro de aspas simples no shell.
  dsn_python = "python -c \"import os,urllib.parse as u; q=lambda k: u.quote(os.environ[k],safe=''); print(os.environ['DSN_SCHEME']+'://'+q('DB_USER')+':'+q('DB_PASSWORD')+'@'+os.environ['DB_HOST']+':'+os.environ['DB_PORT']+'/'+os.environ['DB_NAME'])\""

  # alembic exige DSN SINCRONO (psycopg) e le ALEMBIC_DATABASE_URL.
  dsn_export_alembic = "export ALEMBIC_DATABASE_URL=$(${local.dsn_python})"

  # A aplicacao usa asyncpg e le DATABASE_URL.
  dsn_export_app = "export DATABASE_URL=$(${local.dsn_python})"

  # Host, porta e nome do banco NAO sao sensiveis (endpoint de recurso ja
  # referenciado por data source) — so usuario e senha vem de `secrets`.
  db_env = [
    { name = "DB_HOST", value = local.aurora_endpoint },
    { name = "DB_PORT", value = tostring(local.aurora_port) },
    { name = "DB_NAME", value = var.aurora_database_name },
  ]

  db_secrets_maezo = [
    { name = "DB_USER", valueFrom = "${data.aws_secretsmanager_secret.maezo_app_db.arn}:username::" },
    { name = "DB_PASSWORD", valueFrom = "${data.aws_secretsmanager_secret.maezo_app_db.arn}:password::" },
  ]

  db_secrets_cibseven = [
    { name = "DB_USER", valueFrom = "${data.aws_secretsmanager_secret.cibseven_app_db.arn}:username::" },
    { name = "DB_PASSWORD", valueFrom = "${data.aws_secretsmanager_secret.cibseven_app_db.arn}:password::" },
  ]
}
