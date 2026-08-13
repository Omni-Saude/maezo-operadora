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
#
# /tmp e' o unico ponto gravavel da imagem (raiz somente-leitura, ver deploy/Dockerfile).

locals {
  # $1 = esquema SQLAlchemy · $2 = nome da variavel a exportar
  dsn_builder = <<-SH
    set -eu
    python - <<'PY' > /tmp/dsn.env
    import os, urllib.parse as u
    user = u.quote(os.environ["DB_USER"], safe="")
    pwd  = u.quote(os.environ["DB_PASSWORD"], safe="")
    host = os.environ["DB_HOST"]
    port = os.environ["DB_PORT"]
    name = os.environ["DB_NAME"]
    print(f'export {os.environ["DSN_VAR"]}={os.environ["DSN_SCHEME"]}://{user}:{pwd}@{host}:{port}/{name}')
    PY
    . /tmp/dsn.env
  SH

  # Variaveis de ambiente comuns a todo container que fala com o Aurora. Host, porta
  # e nome do banco NAO sao sensiveis (endpoint de recurso ja referenciado por data
  # source) — so usuario e senha vem de `secrets`.
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
