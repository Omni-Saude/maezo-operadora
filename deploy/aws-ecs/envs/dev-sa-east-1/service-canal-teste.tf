# Canal de Teste — a pagina que o time de dados usa para testar SEM CLI e SEM mim.
#
# Mesma experiencia do teste local (formulario de lancamento dos processos SP-OP +
# visor de evidencia ao lado), agora dentro da AWS e atras do Cloudflare Access.
#
# POR QUE O SCRIPT VEM DE `file()` E NAO DA IMAGEM
#
# O canal e' stdlib pura, e a imagem que esta no ECR (`8f06890`) ainda nao contem o
# modulo — o Docker Desktop desta estacao esta parado e nao consigo publicar imagem
# nova agora. Em vez de bloquear a entrega, o Terraform LE o arquivo do repositorio e
# o passa como argumento de `python -c`. Consequencias, ditas:
#
#   + o arquivo continua sendo a UNICA fonte de verdade (nada de copia colada aqui);
#   + a task definition muda quando o script muda, entao o deploy e' rastreavel;
#   - o script viaja na task definition (~17 KB do limite de 64 KB), nao na imagem,
#     e portanto NAO tem o digest da imagem como proveniencia.
#
# RESOLVIDO EM 19/08/2026, e o proprio comentario acima previa: agora roda como MODULO.
# O `file()` cobrava um preco que so' apareceu quando o canal precisou de um arquivo de
# dados: `python -c <fonte>` executa sem `__file__`, entao qualquer caminho relativo ao
# modulo levanta `NameError` no boot. Foi exatamente o que aconteceu ao servir `paginas/`
# — o circuit breaker do service fez rollback e o ambiente ficou no artefato anterior,
# que e' o comportamento correto e o motivo de ele existir.
#
# Como modulo, o script viaja NA IMAGEM: ganha o digest como proveniencia, deixa de
# ocupar a task definition, e `paginas/` passa a resolver adjacente ao pacote.
#
# SEGURANCA, sem rodeio: o canal NAO tem autenticacao propria e faz proxy de caminho
# arbitrario para o `engine-rest`. Quem abre a pagina dirige o motor. Isso e' aceitavel
# aqui SOMENTE porque o Access exige identidade antes, e e' a mesma exposicao que o
# Cockpit ja tem. A autorizacao continua sendo do engine — que segue com o usuario
# `demo` do CIB Seven ativo.

resource "aws_cloudwatch_log_group" "canal_teste" {
  name              = "/ecs/${local.name}/canal-teste"
  retention_in_days = var.log_retention_days
  tags              = local.base_tags
}

resource "aws_service_discovery_service" "canal_teste" {
  name = "canal-teste"

  dns_config {
    namespace_id = aws_service_discovery_private_dns_namespace.this.id

    dns_records {
      ttl  = 10
      type = "A"
    }

    routing_policy = "MULTIVALUE"
  }

  tags = local.base_tags
}

resource "aws_ecs_task_definition" "canal_teste" {
  family                   = "${local.name}-canal-teste"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = "256"
  memory                   = "512"
  execution_role_arn       = aws_iam_role.task_execution.arn
  task_role_arn            = aws_iam_role.task.arn

  runtime_platform {
    cpu_architecture        = "X86_64"
    operating_system_family = "LINUX"
  }

  container_definitions = jsonencode([{
    name      = "canal-teste"
    image     = "${aws_ecr_repository.app.repository_url}:${var.canal_teste_image_tag}"
    essential = true

    # `python -c <programa>` como UM argumento de argv: sem shell no meio, portanto sem
    # heredoc, sem escape de aspas e sem risco de o conteudo do script ser interpretado
    # pelo `sh`. O arquivo e' lido no plan.
    command = ["python", "-m", "maezo.platform.testchannel"]

    portMappings = [{ containerPort = 8500, protocol = "tcp" }]

    environment = [
      { name = "ENGINE_REST_URL", value = local.cibseven_base_url },
      { name = "CANAL_PORT", value = "8500" },
      # 0.0.0.0 e' obrigatorio: ligado em 127.0.0.1 o container sobe, nada alcanca a
      # porta, e o sintoma e' um servico "saudavel" que nao responde.
      { name = "CANAL_BIND", value = "0.0.0.0" },
      # O link do Cockpit na pagina aponta para o hostname PUBLICO, nao para o DNS
      # interno — quem abre a pagina esta no navegador, fora da VPC.
      { name = "COCKPIT_URL", value = "https://${var.hostname_cockpit}" },
      # Ingresso do agente, pelo DNS do Cloud Map — o IP da task muda a cada deploy.
      # Vazio esconde o painel do agente na pagina em vez de deixar um botao que sempre
      # falha; e' o mesmo raciocinio do `desired_count` que descreve o que existe.
      { name = "AGENT_INGRESS_URL", value = local.agent_ingress_url },
      { name = "PYTHONDONTWRITEBYTECODE", value = "1" },

      # ---------------------------------------------------------------------
      # Rota `/receptor/simular` — a que fabrica mensagem de WhatsApp assinada.
      # ---------------------------------------------------------------------
      # POR QUE ELA EXISTE. O receptor exige `X-Hub-Signature-256` e devolve 401 sem
      # ela, o que esta certo. Uma pagina no navegador so' assinaria carregando o
      # segredo da Meta no JavaScript — pior do que o problema que resolve. Entao a
      # assinatura acontece no canal, que roda no cluster, e o segredo fica no
      # container. O time passa a testar a TRIAGEM inteira sem CLI e sem a Meta.
      #
      # O RISCO, DITO: a assinatura produzida e' INDISTINGUIVEL da da Meta. Quem
      # alcanca o canal fabrica uma mensagem de beneficiario. As tres cercas, e as
      # tres tem de continuar valendo:
      #   1. este `CANAL_SIMULAR_RECEPTOR=1` — sem ele a rota devolve 404, e
      #      `scripts/ci/check_canal_simular.py` reprova quem o ligar fora deste
      #      ambiente. E' a unica razao pela qual a variavel existe em vez de a rota
      #      simplesmente funcionar quando ha segredo: um portao tem de ser visivel.
      #   2. a faixa `55119000000xx` no proprio `server.py`, ancorada nas duas pontas;
      #   3. o Cloudflare Access na frente — a mesma fronteira de identidade de que o
      #      proxy arbitrario de `/engine` ja depende neste servico.
      { name = "RECEPTOR_URL", value = local.receptor_base_url },
      { name = "CANAL_SIMULAR_RECEPTOR", value = "1" },
    ]

    # PRIMEIRO segredo deste container. Vale dizer o que ele NAO e': aqui o
    # `app_secret` serve para PRODUZIR assinatura, nao para verificar — que e' o uso
    # legitimo dele no receptor. E' essa inversao que a cerca de CI vigia.
    secrets = [
      { name = "WHATSAPP_APP_SECRET", valueFrom = "${aws_secretsmanager_secret.whatsapp_meta.arn}:app_secret::" },
    ]

    readonlyRootFilesystem = true

    healthCheck = {
      command     = ["CMD-SHELL", "python -c \"import sys,urllib.request as u; sys.exit(0 if u.urlopen('http://localhost:8500/',timeout=3).status==200 else 1)\""]
      interval    = 30
      timeout     = 5
      retries     = 3
      startPeriod = 30
    }

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.canal_teste.name
        "awslogs-region"        = var.aws_region
        "awslogs-stream-prefix" = "canal"
      }
    }
  }])

  tags = local.base_tags
}

resource "aws_ecs_service" "canal_teste" {
  name            = "canal-teste"
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.canal_teste.arn
  desired_count   = var.canal_teste_desired_count
  launch_type     = "FARGATE"

  enable_execute_command = true

  network_configuration {
    subnets          = data.aws_subnets.private_app.ids
    security_groups  = [aws_security_group.tasks.id]
    assign_public_ip = false
  }

  service_registries {
    registry_arn = aws_service_discovery_service.canal_teste.arn
  }

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  tags = local.base_tags
}

# 8500 entre os componentes do maezo (o tunel alcanca o canal por aqui).
resource "aws_vpc_security_group_ingress_rule" "interno_8500" {
  security_group_id            = aws_security_group.tasks.id
  description                  = "Canal de Teste a partir do tunel"
  ip_protocol                  = "tcp"
  from_port                    = 8500
  to_port                      = 8500
  referenced_security_group_id = aws_security_group.tasks.id
}

resource "aws_vpc_security_group_egress_rule" "interno_8500" {
  security_group_id            = aws_security_group.tasks.id
  description                  = "Saida para o Canal de Teste"
  ip_protocol                  = "tcp"
  from_port                    = 8500
  to_port                      = 8500
  referenced_security_group_id = aws_security_group.tasks.id
}
