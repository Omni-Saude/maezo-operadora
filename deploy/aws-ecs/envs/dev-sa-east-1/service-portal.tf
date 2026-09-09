# Dedicated human BFF; ADR-0049 D3/D4/D8/D11 and DL-0048. Never an agent/testchannel proxy.
locals {
  portal_config = var.portal == null ? {} : { this = var.portal }
  portal_name   = "${local.name}-portal-${substr(sha256(var.tenant_id), 0, 8)}"
  portal_tags   = merge(local.base_tags, { Component = "portal", Zone = "PHI", DataClass = "identity" })
}

# Metadata only. SecretString never enters Terraform or outputs. The owner supplies
# a full asyncpg DSN, dedicated role/database and grants; dsn.tf is NOT reused.
data "aws_secretsmanager_secret" "portal_session" {
  for_each = local.portal_config
  arn      = each.value.database_secret_arn
}

# DescribeSecret can return a key ID/alias. Resolve it to an ARN before using it
# as an IAM Resource; reading KMS metadata does not decrypt the session secret.
data "aws_kms_key" "portal_session" {
  for_each = {
    for key, secret in data.aws_secretsmanager_secret.portal_session : key => secret.kms_key_id
    if try(length(secret.kms_key_id), 0) > 0
  }
  key_id = each.value
}

resource "aws_cloudwatch_log_group" "portal" {
  for_each          = local.portal_config
  name              = "/ecs/${local.portal_name}"
  retention_in_days = var.log_retention_days
  tags              = local.portal_tags
}

resource "aws_iam_role" "portal_execution" {
  for_each           = local.portal_config
  name               = "${local.portal_name}-exec"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
  tags               = local.portal_tags
}

resource "aws_iam_role" "portal_task" {
  for_each           = local.portal_config
  name               = "${local.portal_name}-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
  tags               = local.portal_tags
  # Deliberately no runtime AWS policies, agent credentials, Bedrock or ECS Exec.
}

data "aws_iam_policy_document" "portal_execution" {
  for_each = local.portal_config
  statement {
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"] # API has no resource-level scope; pull actions below do.
  }
  statement {
    actions   = ["ecr:BatchCheckLayerAvailability", "ecr:GetDownloadUrlForLayer", "ecr:BatchGetImage"]
    resources = [aws_ecr_repository.app.arn]
  }
  statement {
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.portal[each.key].arn}:*"]
  }
  statement {
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [each.value.database_secret_arn]
  }
  dynamic "statement" {
    for_each = contains(keys(data.aws_kms_key.portal_session), each.key) ? [data.aws_kms_key.portal_session[each.key].arn] : []
    content {
      actions   = ["kms:Decrypt"]
      resources = [statement.value]
      condition {
        test     = "StringEquals"
        variable = "kms:ViaService"
        values   = ["secretsmanager.sa-east-1.amazonaws.com"]
      }
      condition {
        test     = "StringEquals"
        variable = "kms:EncryptionContext:SecretARN"
        values   = [each.value.database_secret_arn]
      }
    }
  }
}

resource "aws_iam_role_policy" "portal_execution" {
  for_each = local.portal_config
  name     = "portal-image-logs-session-secret"
  role     = aws_iam_role.portal_execution[each.key].id
  policy   = data.aws_iam_policy_document.portal_execution[each.key].json
}

resource "aws_ecs_task_definition" "portal" {
  for_each                 = local.portal_config
  family                   = local.portal_name
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = "256"
  memory                   = "512"
  execution_role_arn       = aws_iam_role.portal_execution[each.key].arn
  task_role_arn            = aws_iam_role.portal_task[each.key].arn
  runtime_platform {
    cpu_architecture        = "X86_64"
    operating_system_family = "LINUX"
  }
  container_definitions = jsonencode([{
    name                   = "portal"
    image                  = "${aws_ecr_repository.app.repository_url}@${each.value.image_digest}"
    essential              = true
    command                = ["python", "-m", "maezo.portal.api"]
    user                   = "1000:1000"
    readonlyRootFilesystem = true
    linuxParameters        = { capabilities = { drop = ["ALL"] } }
    portMappings           = [{ containerPort = 8080, protocol = "tcp" }]
    environment = [
      { name = "MAEZO_PORTAL_TENANT", value = each.value.tenant },
      { name = "MAEZO_PORTAL_ISSUER", value = each.value.issuer },
      { name = "MAEZO_PORTAL_COGNITO_ORIGIN", value = each.value.cognito_origin },
      { name = "MAEZO_PORTAL_CLIENT_ID", value = each.value.human_client_id },
      { name = "MAEZO_PORTAL_CLIENT_PURPOSE", value = each.value.human_client_purpose },
      { name = "MAEZO_PORTAL_MACHINE_CLIENT_ID", value = each.value.machine_client_id },
      { name = "MAEZO_PORTAL_PUBLIC_ORIGIN", value = each.value.public_origin },
      { name = "MAEZO_PORTAL_MODE", value = "production" },
      { name = "PYTHONDONTWRITEBYTECODE", value = "1" },
    ]
    secrets = [{ name = "MAEZO_PORTAL_DATABASE_URL", valueFrom = each.value.database_secret_arn }]
    # Exact public Host is enforced by the real app. Probe the existing unauthenticated
    # session refusal, not a nonexistent /health or a 404. This is liveness, NOT a
    # post-startup DB readiness check. No credentials, logging or side effects.
    healthCheck = {
      command = ["CMD", "python", "-c", <<-PY
        import http.client, json, os, sys
        from urllib.parse import urlsplit
        c = http.client.HTTPConnection('127.0.0.1', 8080, timeout=3)
        c.request('GET', '/api/v1/portal/session', headers={'Host': urlsplit(os.environ['MAEZO_PORTAL_PUBLIC_ORIGIN']).netloc})
        r = c.getresponse()
        ok = r.status == 401 and json.loads(r.read()) == {'erro': 'Não foi possível validar a sessão.'} and r.getheader('Cache-Control') == 'no-store'
        c.close()
        sys.exit(0 if ok else 1)
      PY
      ]
      interval    = 30
      timeout     = 5
      retries     = 3
      startPeriod = 60
    }
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.portal[each.key].name
        "awslogs-region"        = var.aws_region
        "awslogs-stream-prefix" = "portal"
      }
    }
  }])
  tags = local.portal_tags
}

resource "aws_ecs_service" "portal" {
  for_each               = local.portal_config
  name                   = local.portal_name
  cluster                = aws_ecs_cluster.this.id
  task_definition        = aws_ecs_task_definition.portal[each.key].arn
  desired_count          = var.portal_enabled ? 1 : 0
  launch_type            = "FARGATE"
  platform_version       = "1.4.0"
  enable_execute_command = false
  network_configuration {
    subnets          = data.aws_subnets.private_app.ids
    security_groups  = [aws_security_group.portal[each.key].id]
    assign_public_ip = false
  }
  load_balancer {
    target_group_arn = aws_lb_target_group.portal[each.key].arn
    container_name   = "portal"
    container_port   = 8080
  }
  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }
  depends_on = [aws_lb_listener.portal, aws_iam_role_policy.portal_execution]
  tags       = local.portal_tags
}
