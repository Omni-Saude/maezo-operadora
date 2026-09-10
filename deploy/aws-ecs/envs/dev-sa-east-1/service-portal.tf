# Dedicated human BFF; ADR-0049 D3/D4/D8/D11 and DL-0048. Never an agent/testchannel proxy.
locals {
  portal_config       = var.portal == null ? {} : { this = var.portal }
  portal_staff_config = { for key, config in local.portal_config : key => config.staff if config.staff != null }
  portal_staff_environment = { for key, staff in local.portal_staff_config : key => [
    { name = "MAEZO_PORTAL_STAFF_MATERIAL_DIRECTORY", value = "/run/maezo-staff-materials/current" },
    { name = "MAEZO_PORTAL_STAFF_MATERIAL_VERSION_ID", value = staff.material_secret_version_id },
    { name = "MAEZO_PORTAL_STAFF_PUBLIC_MANIFEST_SHA256", value = staff.public_manifest_sha256 },
    { name = "MAEZO_PORTAL_STAFF_ROOT_KEY_SHA256", value = staff.root_key_sha256 },
    { name = "MAEZO_PORTAL_STAFF_DESIGNATION_SHA256", value = staff.designation_sha256 },
    { name = "MAEZO_PORTAL_STAFF_NATIVE_CONFIGURATION_SHA256", value = staff.native_configuration_sha256 },
    { name = "MAEZO_PORTAL_STAFF_SCOPE", value = jsonencode(staff.scope) },
    { name = "MAEZO_PORTAL_STAFF_NATIVE_ORIGIN", value = staff.native_origin },
    { name = "MAEZO_PORTAL_STAFF_NATIVE_SERVER_SPKI_SHA256", value = staff.native_server_spki_sha256 },
    { name = "MAEZO_PORTAL_STAFF_READ_KEY_SHA256", value = staff.read_key_sha256 },
    { name = "MAEZO_PORTAL_STAFF_WITNESS_KEY_SHA256", value = staff.witness_key_sha256 },
    { name = "MAEZO_PORTAL_STAFF_MAXIMUM_SECONDS", value = tostring(staff.maximum_seconds) }
  ] }
  portal_name = "${local.name}-portal-${substr(sha256(var.tenant_id), 0, 8)}"
  portal_tags = merge(local.base_tags, { Component = "portal", Zone = "PHI", DataClass = "identity" })
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

# Metadata only; NEVER read SecretString or create/update the external bundle.
data "aws_secretsmanager_secret" "portal_staff" {
  for_each = local.portal_staff_config
  arn      = each.value.material_secret_arn
}
data "aws_kms_key" "portal_staff" {
  for_each = { for key, secret in data.aws_secretsmanager_secret.portal_staff : key => secret.kms_key_id if try(length(secret.kms_key_id), 0) > 0 }
  key_id   = each.value
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
    for_each = each.value.staff == null ? [] : [each.value.staff]
    content {
      actions   = ["secretsmanager:GetSecretValue"]
      resources = [statement.value.material_secret_arn]
    }
  }
  dynamic "statement" {
    for_each = each.value.staff == null ? [] : [each.value.staff]
    content {
      actions   = ["kms:Decrypt"]
      resources = [statement.value.material_kms_key_arn]
      condition {
        test     = "StringEquals"
        variable = "kms:ViaService"
        values   = ["secretsmanager.sa-east-1.amazonaws.com"]
      }
      condition {
        test     = "StringEquals"
        variable = "kms:EncryptionContext:SecretARN"
        values   = [statement.value.material_secret_arn]
      }
    }
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
  # Fargate's finite task-wide disk budget; not a per-directory tmpfs/quota.
  dynamic "ephemeral_storage" {
    for_each = each.value.staff == null ? [] : [true]
    content { size_in_gib = 21 }
  }
  dynamic "volume" {
    for_each = each.value.staff == null ? [] : ["staff-materials", "staff-scratch"]
    content { name = volume.value }
  }
  lifecycle {
    precondition {
      condition = each.value.staff == null ? true : try(
        data.aws_kms_key.portal_staff[each.key].arn == each.value.staff.material_kms_key_arn,
        false
      )
      error_message = "Staff bundle must use its explicitly qualified regional account CMK; missing/mismatched metadata refuses."
    }
    precondition {
      condition = each.value.staff == null ? true : (
        data.aws_security_group.portal_staff_native_https[each.key].owner_id == var.aws_account_id &&
        data.aws_security_group.portal_staff_native_https[each.key].vpc_id == data.aws_vpc.this.id &&
        data.aws_security_group.portal_staff_native_database[each.key].owner_id == var.aws_account_id &&
        data.aws_security_group.portal_staff_native_database[each.key].vpc_id == data.aws_vpc.this.id
      )
      error_message = "Both staff native targets require explicit account/VPC qualification, including reuse of existing exact DB egress."
    }
  }
  container_definitions = jsonencode(concat([{
    name                   = "portal"
    image                  = "${aws_ecr_repository.app.repository_url}@${each.value.staff == null ? each.value.image_digest : each.value.staff.portal_image_digest}"
    essential              = true
    command                = ["python", "-m", "maezo.portal.api"]
    user                   = "1000:1000"
    readonlyRootFilesystem = true
    linuxParameters        = { capabilities = { drop = ["ALL"] } }
    portMappings           = [{ containerPort = 8080, protocol = "tcp" }]
    dependsOn              = each.value.staff == null ? [] : [{ containerName = "portal-staff-materialize", condition = "SUCCESS" }]
    mountPoints = each.value.staff == null ? [] : [
      { sourceVolume = "staff-materials", containerPath = "/run/maezo-staff-materials", readOnly = true },
      { sourceVolume = "staff-scratch", containerPath = "/run/maezo-staff-scratch", readOnly = false }
    ]
    environment = concat([
      { name = "MAEZO_PORTAL_TENANT", value = each.value.tenant },
      { name = "MAEZO_PORTAL_ISSUER", value = each.value.issuer },
      { name = "MAEZO_PORTAL_COGNITO_ORIGIN", value = each.value.cognito_origin },
      { name = "MAEZO_PORTAL_CLIENT_ID", value = each.value.human_client_id },
      { name = "MAEZO_PORTAL_CLIENT_PURPOSE", value = each.value.human_client_purpose },
      { name = "MAEZO_PORTAL_MACHINE_CLIENT_ID", value = each.value.machine_client_id },
      { name = "MAEZO_PORTAL_PUBLIC_ORIGIN", value = each.value.public_origin },
      { name = "MAEZO_PORTAL_MODE", value = "production" },
      { name = "MAEZO_PORTAL_CAPABILITIES", value = each.value.staff == null ? "identity" : "identity,staff_cases" },
      { name = "PYTHONDONTWRITEBYTECODE", value = "1" },
      ], lookup(local.portal_staff_environment, each.key, []), each.value.staff == null ? [] : [
      { name = "TMPDIR", value = "/run/maezo-staff-scratch" }
    ])
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
    }], [for staff in(each.value.staff == null ? [] : [each.value.staff]) : {
    name                   = "portal-staff-materialize"
    image                  = "${aws_ecr_repository.app.repository_url}@${staff.portal_image_digest}"
    essential              = false
    command                = ["python", "-m", "maezo.gateway.staff_cases.materialize"]
    user                   = "1000:1000"
    readonlyRootFilesystem = true
    linuxParameters        = { capabilities = { drop = ["ALL"] } }
    # No HTTP port, log driver, health probe, restart policy or scratch mount.
    mountPoints = [{ sourceVolume = "staff-materials", containerPath = "/run/maezo-staff-materials", readOnly = false }]
    environment = concat([
      { name = "MAEZO_PORTAL_TENANT", value = each.value.tenant },
      { name = "MAEZO_PORTAL_ISSUER", value = each.value.issuer },
      { name = "MAEZO_PORTAL_CAPABILITIES", value = "identity,staff_cases" },
      { name = "PYTHONDONTWRITEBYTECODE", value = "1" }
    ], local.portal_staff_environment[each.key])
    # Full SecretString, exact immutable version; neither JSON key nor mutable stage.
    secrets = [{ name = "MAEZO_PORTAL_STAFF_SECRET_BUNDLE", valueFrom = "${staff.material_secret_arn}:::${staff.material_secret_version_id}" }]
  }]))
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
  depends_on = [
    aws_lb_listener.portal, aws_iam_role_policy.portal_execution,
    aws_vpc_security_group_egress_rule.portal_staff_native_https,
    aws_vpc_security_group_egress_rule.portal_staff_native_database
  ]
  tags = local.portal_tags
}
