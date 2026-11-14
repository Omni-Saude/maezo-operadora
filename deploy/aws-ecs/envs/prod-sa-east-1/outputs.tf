output "ecs_tasks_security_group_id" {
  description = <<-EOT
    ID do SG das tasks. Em prod o BANCO NAO depende dele (o ingresso no Aurora e' pelo
    SG-marcador `aurora_hapi_clientes`, ja' anexado). Ele precisa entrar, no cutover, em:
      * `ingest_allowed_source_sg_ids` do HAPI de prod (amh-data-platform,
        envs/prod-sa-east-1-hapi) — sem isso o FHIR da timeout;
      * a lista de SGs de origem do ALB interno do interop de prod (envs/prod-sa-east-1-interop),
        quando o interop for ligado.
  EOT
  value       = aws_security_group.tasks.id
}

output "ecr_repository_urls" {
  description = "ECR de prod (nascem vazios): destino do `crane copy` das imagens do dev, por digest."
  value = {
    app    = aws_ecr_repository.app.repository_url
    engine = aws_ecr_repository.engine.repository_url
  }
}

output "webhook_receiver_task_role_arn" {
  description = "Papel da TAREFA do receptor (so' existe com `recipient_vault_enabled`)."
  value       = one(aws_iam_role.webhook_receiver_task[*].arn)
}

output "task_execution_role_arn" {
  description = <<-EOT
    Papel de EXECUCAO das tasks. As chaves de interop (`amh/interop/*`) entram no container por
    `secrets`/`valueFrom`, ou seja, quem chama GetSecretValue + kms:Decrypt e' ESTE papel, nao o da
    tarefa. E' o ARN que o foundation de prod precisa como leitor (amh-data-platform,
    `maezo_receiver_task_role_arn` em interop_keys.tf) — conferir no PR de la' qual principal a
    policy da CMK de fato precisa antes de ligar `helena_identidade_amh`/`acesso_beneficiario`.
  EOT
  value       = aws_iam_role.task_execution.arn
}

output "ecs_cluster_name" {
  description = "Cluster onde rodam os componentes do maezo-operadora."
  value       = aws_ecs_cluster.this.name
}

output "servicos_habilitados" {
  description = "Interruptor geral. false = todo servico com desired_count 0 e nenhuma rotina agendada."
  value       = var.servicos_habilitados
}

output "migrations_task_family" {
  description = "Family da task de migrations — usada em `aws ecs run-task`."
  value       = aws_ecs_task_definition.migrations.family
}

output "cibseven_internal_url" {
  description = "URL interna do engine BPMN (Cloud Map). E' o CIBSEVEN_BASE_URL dos componentes."
  value       = local.cibseven_base_url
}

output "fhir_base_url" {
  description = "FHIR do HAPI de prod pelo endpoint interno (remote state do -hapi) — o trafego clinico nao sai da VPC."
  value       = local.fhir_base_url
}

output "private_app_subnet_ids" {
  description = "Subnets das tasks — necessarias no `aws ecs run-task` manual."
  value       = data.aws_subnets.private_app.ids
}

output "run_migrations_command" {
  description = "Comando pronto para aplicar as migrations (ver runbook)."
  value = join(" ", [
    "aws ecs run-task",
    "--cluster ${aws_ecs_cluster.this.name}",
    "--task-definition ${aws_ecs_task_definition.migrations.family}",
    "--launch-type FARGATE",
    "--region ${var.aws_region}",
    "--network-configuration 'awsvpcConfiguration={subnets=[${join(",", data.aws_subnets.private_app.ids)}],securityGroups=[${join(",", local.task_security_groups)}],assignPublicIp=DISABLED}'",
  ])
}

output "cloudflared_tunnel_token_secret" {
  description = <<-EOT
    Onde gravar o token do tunel Cloudflare de PROD (tunel proprio, nunca o do dev).
    Formato {"token":"<TOKEN>"}, com `--secret-string file://arquivo.json`.
  EOT
  value       = aws_secretsmanager_secret.cloudflared_token.name
}

output "segredos_sem_valor" {
  description = "Segredos que este state cria VAZIOS (o valor e' passo do runbook, com OrganizationAccountAccessRole)."
  value = sort([
    aws_secretsmanager_secret.a2a_card_signing_key.name,
    aws_secretsmanager_secret.phi_hmac_key.name,
    aws_secretsmanager_secret.engine_admin.name,
    aws_secretsmanager_secret.whatsapp_meta.name,
    aws_secretsmanager_secret.cloudflared_token.name,
  ])
}
