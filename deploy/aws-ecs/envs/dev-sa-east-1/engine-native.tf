# Onda 4 (docs/plans/portal-autoridade-nativa-dev.md): o engine vira servidor NATIVO.
#
# D-B: NLB INTERNO TCP 443 -> Tomcat 8443 (mTLS de ponta a ponta; o NLB nao termina TLS, entao o
# certificado do cliente chega ao `HumanServlet`). Nome estavel `engine-native.<namespace>` na
# zona privada que o Cloud Map ja mantem para este state (ecs.tf) — nenhuma zona nova. O SG do NLB
# so aceita 443 do SG do portal; o SG das tasks so aceita 8443 do SG do NLB.
#
# B4: init container `engine-native-materialize` (imagem do app, `maezo.platform.
# engine_native_materialize`) le o segredo `maezo-operadora/dev/engine/native-materials` pinado
# por versionId e escreve arquivos regulares, uid 1000, 0400, sem symlink, em volumes da task que
# o engine monta READ-ONLY. B7 (decisao do dono: SIDECAR): `staff-case-issuer` opcional na mesma
# task, com a composicao materializada pelo mesmo init.
#
# TUDO atras de `var.engine_native`. Com o default `null` o plano e' NO-OP contra o dev de hoje:
# nenhum recurso novo, e a task definition do cibseven gera o MESMO JSON (cerca em
# tests/unit/deploy/test_engine_native_dev.py). Virar a chave e' a Onda 4, na janela combinada.
#
# O que a chave NAO faz sozinha: trocar a imagem do engine. `engine_image_digest` continua sendo a
# variavel de sempre e precisa apontar, no mesmo apply, para a imagem de `Dockerfile.human`
# assinada (a antiga com `currentSchema=maezo_native,cibseven` sobe mas nao serve; a nova com
# `currentSchema=cibseven` morre no boot — ADR-0060, Consequencia 3). O init e o sidecar usam a
# imagem do APP (`amh/maezo-operadora`), que precisa ser RECONSTRUIDA a partir deste commit: o
# modulo do materializador nao existe nas imagens publicadas antes dele.

variable "engine_native" {
  description = "Onda 4: segredo nativo pinado (ARN + versionId + CMK), digest da imagem do app para o init/sidecar e o sidecar staff-case-issuer. null (default) = engine de hoje, sem NLB/zona/SG/init."
  # `any` + conjunto EXATO de chaves (mesma regra de `portal.staff`): um object() descartaria em
  # silencio uma chave desconhecida.
  type    = any
  default = null

  validation {
    condition = var.engine_native == null ? true : try(
      toset(keys(var.engine_native)) == toset([
        "native_secret_arn", "native_secret_version_id", "kms_key_arn", "app_image_digest", "staff_case_issuer"
      ]) &&
      alltrue([for name in ["native_secret_arn", "native_secret_version_id", "kms_key_arn", "app_image_digest"] :
        can(regex("^\"", jsonencode(var.engine_native[name])))
      ]) &&
      contains(["true", "false"], jsonencode(var.engine_native.staff_case_issuer)) &&
      startswith(var.engine_native.native_secret_arn, "arn:aws:secretsmanager:sa-east-1:${var.aws_account_id}:secret:maezo-operadora/dev/engine/native-materials-") &&
      can(regex("^arn:aws:secretsmanager:sa-east-1:[0-9]{12}:secret:maezo-operadora/dev/engine/native-materials-[A-Za-z0-9]{6}$", var.engine_native.native_secret_arn)) &&
      can(regex("^[A-Za-z0-9-]{32,64}$", var.engine_native.native_secret_version_id)) &&
      startswith(var.engine_native.kms_key_arn, "arn:aws:kms:sa-east-1:${var.aws_account_id}:key/") &&
      can(regex("^arn:aws:kms:sa-east-1:[0-9]{12}:key/[0-9a-f-]{36}$", var.engine_native.kms_key_arn)) &&
      can(regex("^sha256:[0-9a-f]{64}$", var.engine_native.app_image_digest)),
      false
    )
    error_message = "engine_native exige exatamente native_secret_arn (maezo-operadora/dev/engine/native-materials, desta conta, sa-east-1), native_secret_version_id, kms_key_arn (CMK regional da conta), app_image_digest (sha256) e staff_case_issuer (bool); sem default nem chave extra."
  }
  validation {
    # O unico cliente do 443 e' o SG do portal (D-B); sem portal nao ha a quem abrir.
    condition     = var.engine_native == null || var.portal != null
    error_message = "engine_native exige var.portal configurado: o ingress 443 do NLB nativo vem so do SG do portal."
  }
}

locals {
  engine_native_config = var.engine_native == null ? {} : { this = var.engine_native }
  engine_native_list   = var.engine_native == null ? [] : [var.engine_native]
  engine_native_issuer = [for native in local.engine_native_list : native if native.staff_case_issuer]
  # SAN do certificado do servidor (decisao do dono, 24/09): o nome na zona do Cloud Map.
  engine_native_hostname = "engine-native.${aws_service_discovery_private_dns_namespace.this.name}"
  engine_native_origin   = "https://${local.engine_native_hostname}"
  # Mesmos nomes que o `native-secret` emite (tools/staff_materials/native_secret.py); os caminhos
  # absolutos dentro dos JSON (continuity_keys_file, dsn_file, ca_file) precisam apontar para ca.
  engine_native_run_path    = "/run/maezo/engine"
  engine_native_tls_path    = "/run/maezo/native" # pinado no server.xml da imagem
  engine_native_issuer_path = "/run/maezo/staff-issuer"
  engine_native_init_root   = "/run/maezo-engine-native" # ROOT do materializador
  engine_native_tags        = merge(local.base_tags, { Component = "engine-native" })
}

# Metadados apenas; o SecretString nunca entra no Terraform.
data "aws_secretsmanager_secret" "engine_native" {
  for_each = local.engine_native_config
  arn      = each.value.native_secret_arn
}
data "aws_kms_key" "engine_native" {
  for_each = { for key, secret in data.aws_secretsmanager_secret.engine_native : key => secret.kms_key_id if try(length(secret.kms_key_id), 0) > 0 }
  key_id   = each.value
}

# ---------------------------------------------------------------------------
# Rede: SG do NLB, regras, NLB interno, target group 8443, listener 443
# ---------------------------------------------------------------------------
resource "aws_security_group" "engine_native_nlb" {
  for_each    = local.engine_native_config
  name        = "${local.name}-engine-native-nlb"
  description = "NLB interno do engine nativo (Onda 4, D-B): 443 so do portal"
  vpc_id      = data.aws_vpc.this.id
  tags        = merge(local.engine_native_tags, { Name = "${local.name}-engine-native-nlb" })
}

resource "aws_vpc_security_group_ingress_rule" "engine_native_nlb_from_portal" {
  for_each                     = { for key, native in local.engine_native_config : key => native if contains(keys(local.portal_config), key) }
  security_group_id            = aws_security_group.engine_native_nlb[each.key].id
  referenced_security_group_id = aws_security_group.portal[each.key].id
  ip_protocol                  = "tcp"
  from_port                    = 443
  to_port                      = 443
  description                  = "Portal staff BFF -> engine nativo (mTLS no Tomcat)"
}

# O sidecar `staff-case-issuer` publica pelo mesmo origin nativo (importer mTLS) e roda no SG das
# tasks. So existe com `staff_case_issuer = true`; o mTLS continua exigido pelo Tomcat.
resource "aws_vpc_security_group_ingress_rule" "engine_native_nlb_from_issuer" {
  for_each                     = { for key, native in local.engine_native_config : key => native if native.staff_case_issuer }
  security_group_id            = aws_security_group.engine_native_nlb[each.key].id
  referenced_security_group_id = aws_security_group.tasks.id
  ip_protocol                  = "tcp"
  from_port                    = 443
  to_port                      = 443
  description                  = "Sidecar staff-case-issuer -> engine nativo (importer mTLS)"
}

resource "aws_vpc_security_group_egress_rule" "engine_native_nlb_to_engine" {
  for_each                     = local.engine_native_config
  security_group_id            = aws_security_group.engine_native_nlb[each.key].id
  referenced_security_group_id = aws_security_group.tasks.id
  ip_protocol                  = "tcp"
  from_port                    = 8443
  to_port                      = 8443
  description                  = "NLB -> Tomcat 8443 (trafego e health check)"
}

resource "aws_vpc_security_group_ingress_rule" "tasks_engine_native_from_nlb" {
  for_each                     = local.engine_native_config
  security_group_id            = aws_security_group.tasks.id
  referenced_security_group_id = aws_security_group.engine_native_nlb[each.key].id
  ip_protocol                  = "tcp"
  from_port                    = 8443
  to_port                      = 8443
  description                  = "Tomcat 8443 so a partir do NLB nativo"
}

resource "aws_lb" "engine_native" {
  for_each                         = local.engine_native_config
  name                             = "${local.name}-eng-native"
  internal                         = true
  load_balancer_type               = "network"
  ip_address_type                  = "ipv4"
  subnets                          = data.aws_subnets.private_app.ids
  security_groups                  = [aws_security_group.engine_native_nlb[each.key].id]
  enable_cross_zone_load_balancing = true
  tags                             = local.engine_native_tags
}

resource "aws_lb_target_group" "engine_native" {
  for_each    = local.engine_native_config
  name        = "${local.name}-eng-native"
  vpc_id      = data.aws_vpc.this.id
  port        = 8443
  protocol    = "TCP"
  target_type = "ip"
  # Sem preservar IP: o sidecar na propria task alcanca o NLB (hairpin) e o SG das tasks confere
  # o SG do NLB, nao o do cliente.
  preserve_client_ip   = false
  proxy_protocol_v2    = false
  deregistration_delay = 30
  health_check {
    protocol = "TCP" # liveness do conector; o handshake exige certificado de cliente
    port     = "traffic-port"
  }
  tags = local.engine_native_tags
}

resource "aws_lb_listener" "engine_native" {
  for_each          = local.engine_native_config
  load_balancer_arn = aws_lb.engine_native[each.key].arn
  port              = 443
  protocol          = "TCP" # NAO termina TLS (D-B)
  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.engine_native[each.key].arn
  }
}

# B3: registro na zona privada que o namespace do Cloud Map ja criou (ecs.tf). Nao ha zona nova.
resource "aws_route53_record" "engine_native" {
  for_each = local.engine_native_config
  zone_id  = aws_service_discovery_private_dns_namespace.this.hosted_zone
  name     = local.engine_native_hostname
  type     = "A"
  alias {
    name                   = aws_lb.engine_native[each.key].dns_name
    zone_id                = aws_lb.engine_native[each.key].zone_id
    evaluate_target_health = true
  }
}

# ---------------------------------------------------------------------------
# IAM: a execution role le EXATAMENTE este segredo, na versao pinada pela task definition
# ---------------------------------------------------------------------------
data "aws_iam_policy_document" "task_execution_engine_native" {
  for_each = local.engine_native_config
  statement {
    sid       = "LerSegredoNativoDoEngine"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [each.value.native_secret_arn]
  }
  statement {
    sid       = "DecifrarCMKDoSegredoNativo"
    actions   = ["kms:Decrypt"]
    resources = [each.value.kms_key_arn]
    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["secretsmanager.sa-east-1.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "kms:EncryptionContext:SecretARN"
      values   = [each.value.native_secret_arn]
    }
  }
}

resource "aws_iam_role_policy" "task_execution_engine_native" {
  for_each = local.engine_native_config
  name     = "${local.name}-engine-native-secret"
  role     = aws_iam_role.task_execution.id
  policy   = data.aws_iam_policy_document.task_execution_engine_native[each.key].json
  lifecycle {
    precondition {
      condition     = try(data.aws_kms_key.engine_native[each.key].arn == each.value.kms_key_arn, false)
      error_message = "O segredo nativo precisa estar sob a CMK regional declarada em kms_key_arn; metadado ausente ou divergente recusa."
    }
  }
}

# ---------------------------------------------------------------------------
# Containers extras da task do cibseven (consumidos por service-cibseven.tf)
# ---------------------------------------------------------------------------
locals {
  # Volumes da task: um por prefixo do segredo; o do emissor so com o sidecar.
  engine_native_volumes = concat(
    [for native in local.engine_native_list : "engine-native"],
    [for native in local.engine_native_list : "engine-run"],
    [for native in local.engine_native_issuer : "staff-issuer"],
  )

  # O que muda no container `cibseven`. Env: exatamente os nomes do harness C1
  # (deploy/c1-local/compose.yaml, servico `engine`) e do contrato de native-deploy/README.md.
  engine_native_cibseven = [for native in local.engine_native_list : {
    dependsOn = [{ containerName = "engine-native-materialize", condition = "SUCCESS" }]
    mountPoints = [
      { sourceVolume = "engine-native", containerPath = local.engine_native_tls_path, readOnly = true },
      { sourceVolume = "engine-run", containerPath = local.engine_native_run_path, readOnly = true },
    ]
  }]
  engine_native_environment = flatten([for native in local.engine_native_list : [
    # setenv.sh da imagem nativa monta a URL JDBC pinada destes tres (allowlist); DB_URL fica inerte.
    { name = "DB_HOST", value = local.aurora_endpoint },
    { name = "DB_PORT", value = tostring(local.aurora_port) },
    { name = "DB_NAME", value = var.aurora_database_name },
    { name = "MAEZO_HUMAN_TRUST_FILE", value = "${local.engine_native_run_path}/trust.json" },
    { name = "MAEZO_PORTAL_READ_TRUST_FILE", value = "${local.engine_native_run_path}/portal-read-trust.json" },
    { name = "MAEZO_PORTAL_READ_PROVIDER_FILE", value = "${local.engine_native_run_path}/portal-read-provider.json" },
    { name = "MAEZO_STAFF_COMPOSITION_FILE", value = "${local.engine_native_run_path}/staff/staff-composition.json" },
  ]])

  engine_native_containers = concat([for native in local.engine_native_list : {
    name      = "engine-native-materialize"
    image     = "${aws_ecr_repository.app.repository_url}@${native.app_image_digest}"
    essential = false
    command   = ["python", "-m", "maezo.platform.engine_native_materialize"]
    # root SO para o fchown -> 1000 (volume vazio do Fargate nasce root:root). Todas as
    # capabilities padrao caem exceto CHOWN; o Fargate nao permite `add` alem de SYS_PTRACE.
    user                   = "0:0"
    readonlyRootFilesystem = true
    linuxParameters = { capabilities = { drop = [
      "AUDIT_WRITE", "DAC_OVERRIDE", "FOWNER", "FSETID", "KILL", "MKNOD", "NET_BIND_SERVICE",
      "NET_RAW", "SETFCAP", "SETGID", "SETPCAP", "SETUID", "SYS_CHROOT"
    ] } }
    mountPoints = [for volume in local.engine_native_volumes :
      { sourceVolume = volume, containerPath = "${local.engine_native_init_root}/${volume}", readOnly = false }
    ]
    environment = [
      { name = "MAEZO_ENGINE_NATIVE_STAFF_ISSUER", value = tostring(native.staff_case_issuer) },
      { name = "PYTHONDONTWRITEBYTECODE", value = "1" },
    ]
    # SecretString inteiro, versao IMUTAVEL; nem chave JSON nem stage mutavel.
    secrets = [{ name = "MAEZO_ENGINE_NATIVE_SECRET", valueFrom = "${native.native_secret_arn}:::${native.native_secret_version_id}" }]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.cibseven.name
        "awslogs-region"        = var.aws_region
        "awslogs-stream-prefix" = "engine-native-materialize"
      }
    }
    }], [for native in local.engine_native_issuer : {
    name      = "staff-case-issuer"
    image     = "${aws_ecr_repository.app.repository_url}@${native.app_image_digest}"
    essential = false # o emissor parado nao derruba o engine
    # Uma rodada por execucao (case_issuer_runtime.py); o sidecar repete a cada 60 s. O REST do
    # engine e' o localhost da task (engine_rest_url = http://localhost:8080/engine-rest).
    command                = ["sh", "-c", "while :; do python -m maezo.gateway.staff_cases; sleep 60; done"]
    user                   = "1000:1000"
    readonlyRootFilesystem = true
    linuxParameters        = { capabilities = { drop = ["ALL"] } }
    dependsOn = [
      { containerName = "engine-native-materialize", condition = "SUCCESS" },
      { containerName = "cibseven", condition = "HEALTHY" },
    ]
    mountPoints = [{ sourceVolume = "staff-issuer", containerPath = local.engine_native_issuer_path, readOnly = true }]
    environment = [
      { name = "MAEZO_STAFF_CASE_ISSUER_FILE", value = "${local.engine_native_issuer_path}/composition.json" },
      { name = "PYTHONDONTWRITEBYTECODE", value = "1" },
    ]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.cibseven.name
        "awslogs-region"        = var.aws_region
        "awslogs-stream-prefix" = "staff-case-issuer"
      }
    }
  }])
}

output "engine_native_origin" {
  description = "Origin nativo (D-B) para `portal.staff.native_origin`; null enquanto engine_native = null."
  value       = var.engine_native == null ? null : local.engine_native_origin
}

output "engine_native_nlb_sg_id" {
  description = "SG do NLB nativo: `portal.staff.native_https_security_group_id`; null enquanto engine_native = null."
  value       = try(aws_security_group.engine_native_nlb["this"].id, null)
}
