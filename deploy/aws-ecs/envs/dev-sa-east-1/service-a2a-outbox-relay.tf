# a2a-outbox-relay (SC-01 / R-004) — drena `a2a_fact_outbox` (Postgres) para o Kafka,
# at-least-once. Servico dedicado, ECS-equivalente de `deployment-a2a-outbox-relay.yaml`
# no chart Helm (mesma decisao do dono, OWNER-DECISIONS-REGISTER R-004 opcao B).
#
# DRIFT vs a leitura literal do memorando ("servico equivalente ... mirroring how the existing
# bridge service is declared" / "espelhando o sibling notificationsBridge"): nenhuma das tres
# pontes Kafka->process-start (notifications-bridge, network-change-bridge,
# consent-revocation-bridge) tem service ECS neste ambiente — sao Helm-only (verificado:
# `grep -rln "notifications_bridge\|notifications-bridge" deploy/aws-ecs/envs/dev-sa-east-1/*.tf`
# nao acha nada). Nao ha "servico de bridge existente" para espelhar literalmente. Este arquivo
# implementa a INTENCAO da decisao (relay drenando o outbox em producao, com paridade ECS/Helm)
# usando o padrao mais proximo que EXISTE no ambiente: `service-worker.tf` (daemon Python de
# longa duracao, TENANT_ID + DATABASE_URL via `local.dsn_export_app` + KAFKA_BOOTSTRAP_SERVERS,
# sem trafego de entrada, sem ALB/Cloud Map).
#
# Sem healthCheck (ao contrario do worker): `outbox_relay.py` nao sobe servidor de health (mesma
# situacao do notifications-bridge no chart Helm — so' roda o loop de poll).
#
# [CORRIGIDO 2026-09-04 pelo reparo REVISE do gatekeeper R1, achado F3: esta secao afirmava
# "pgrep funcionaria" — FALSO. A imagem de runtime e' `python:3.12-slim` + `libpq5`
# (deploy/Dockerfile) e NAO instala `procps`; `pgrep` sairia com exit 127, nao com um resultado
# util. O chart Helm tinha o MESMO defeito na sua livenessProbe (`deployment-a2a-outbox-relay.yaml`
# rodava `pgrep` ate' este mesmo reparo) e foi corrigido para usar um arquivo de heartbeat que
# `run_relay_loop` (outbox_relay.py) toca a cada sweep, checado via `python -c`.]
#
# [CORRIGIDO 2026-09-04 pelo reparo REVISE do gatekeeper R1, achado G2 (secao Delta): a frase acima
# dizia "o ECS poderia reaproveitar o MESMO heartbeat" — TAMBEM FALSO, e pela mesma razao estrutural
# que a correcao anterior: esta task tem `readonlyRootFilesystem = true` E NAO declara `volume`/
# `mountPoints` algum (verificado: nenhum arquivo em deploy/aws-ecs/envs/dev-sa-east-1/*.tf usa
# `volume`/`mountPoints`) — `/tmp` aqui e' somente-leitura, entao `run_relay_loop` tentaria
# `Path(heartbeat_path).touch()` e receberia `OSError` (Read-only file system) em TODO sweep. O
# proprio `dsn.tf:16-19` ja registra essa exata falha, ocorrida de verdade numa execucao real
# ("a primeira versao escrevia /tmp/dsn.env e falhou ... `sh: cannot create /tmp/dsn.env:
# Permission denied`"). Por isso `A2A_OUTBOX_RELAY_HEARTBEAT_PATH` abaixo e' explicitamente `""` —
# `_touch_heartbeat`'s `if not path: return` (outbox_relay.py) desativa o heartbeat de forma limpa
# e silenciosa em vez de tentar escrever e falhar (e logar) a cada sweep. Reintroduzir um heartbeat
# real no ECS exigiria um `volume` de task (Fargate ephemeral storage ou um `docker_volume_configuration`)
# + `mountPoints` apontando para o diretorio do heartbeat — nenhum padrao desse tipo existe hoje
# neste ambiente para nenhuma outra task, entao introduzi-lo aqui seria a PRIMEIRA instancia,
# fora do escopo deste reparo (REVISE-minor). Ate' la', o ECS nao tem sinal de liveness alem do que
# `essential = true` + o exit code do container ja garantem — ver docs/review-queue.md
# HELM-BRIDGE-LIVENESS-PGREP para o gap analogo das pontes Helm.]
#
# Um healthCheck via `CMD`/`CMD-SHELL` do ECS (que roda contra o PROPRIO container) mediria apenas
# "o processo esta' vivo", o mesmo que ECS ja' garante via `essential = true` + o exit code do
# container — omitido por honestidade: nenhum sinal adicional real estaria sendo checado, com ou
# sem heartbeat, sem um volume escrivel que hoje nao existe.

resource "aws_cloudwatch_log_group" "a2a_outbox_relay" {
  name              = "/ecs/${local.name}/a2a-outbox-relay"
  retention_in_days = var.log_retention_days
  tags              = local.base_tags
}

resource "aws_ecs_task_definition" "a2a_outbox_relay" {
  family                   = "${local.name}-a2a-outbox-relay"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = tostring(var.a2a_outbox_relay_cpu)
  memory                   = tostring(var.a2a_outbox_relay_memory)
  execution_role_arn       = aws_iam_role.task_execution.arn
  task_role_arn            = aws_iam_role.task.arn

  runtime_platform {
    cpu_architecture        = "X86_64"
    operating_system_family = "LINUX"
  }

  container_definitions = jsonencode([{
    name      = "a2a-outbox-relay"
    image     = "${aws_ecr_repository.app.repository_url}:${var.image_tag}"
    essential = true

    # `build_relay` (outbox_relay.py) recusa construir sem DATABASE_URL nem
    # KAFKA_BOOTSTRAP_SERVERS — fail-closed, nunca reivindica linhas do outbox e as descarta em
    # silencio. Sem `--once`: o modulo faz poll ate SIGTERM/SIGINT por padrao (module docstring).
    command = ["sh", "-c", "set -eu\n${local.dsn_export_app}\nexec python -m maezo.a2a.outbox_relay"]

    environment = concat(local.db_env, [
      { name = "DSN_SCHEME", value = "postgresql+asyncpg" },
      { name = "TENANT_ID", value = var.tenant_id },
      { name = "KAFKA_BOOTSTRAP_SERVERS", value = local.kafka_bootstrap },
      # Mesmos defaults do bloco `a2aOutboxRelay` do chart Helm (outbox.py `DEFAULT_BATCH_SIZE` /
      # outbox_relay.py `DEFAULT_POLL_INTERVAL_S`) — paridade Helm/ECS deliberada.
      { name = "A2A_OUTBOX_RELAY_BATCH_SIZE", value = "100" },
      { name = "A2A_OUTBOX_RELAY_POLL_INTERVAL_S", value = "2" },
      # readonlyRootFilesystem = true + nenhum volume/mountPoints nesta task => "/tmp" e'
      # somente-leitura aqui (gatekeeper achado G2). Vazio explicito, nao o default do modulo
      # (`DEFAULT_HEARTBEAT_PATH`): `_touch_heartbeat`'s `if not path: return` desativa o
      # heartbeat de forma limpa, em vez de tentar escrever e falhar (e logar) a cada sweep. Ver o
      # comentario do bloco acima para a justificativa completa e o caminho para reintroduzir um
      # heartbeat real (volume de task + mountPoints).
      { name = "A2A_OUTBOX_RELAY_HEARTBEAT_PATH", value = "" },
      { name = "PYTHONDONTWRITEBYTECODE", value = "1" },
    ])

    secrets = local.db_secrets_maezo

    readonlyRootFilesystem = true

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.a2a_outbox_relay.name
        "awslogs-region"        = var.aws_region
        "awslogs-stream-prefix" = "a2a-outbox-relay"
      }
    }
  }])

  tags = local.base_tags
}

resource "aws_ecs_service" "a2a_outbox_relay" {
  name            = "a2a-outbox-relay"
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.a2a_outbox_relay.arn
  desired_count   = var.a2a_outbox_relay_desired_count
  launch_type     = "FARGATE"

  enable_execute_command = true

  network_configuration {
    subnets          = data.aws_subnets.private_app.ids
    security_groups  = [aws_security_group.tasks.id]
    assign_public_ip = false
  }

  # O relay nao recebe trafego de entrada — nao ha ALB nem registro em Cloud Map. Ele PUXA do
  # outbox Postgres e EMPURRA para o Kafka; nada precisa alcanca-lo de fora (mesma topologia do
  # worker-runtime em service-worker.tf).

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  tags = local.base_tags
}
