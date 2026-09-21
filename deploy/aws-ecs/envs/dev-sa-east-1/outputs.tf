output "ecs_tasks_security_group_id" {
  description = <<-EOT
    ID do SG das tasks. ESTE e' o valor que precisa entrar em
    `module.aurora_hapi.allowed_security_group_ids` no state da plataforma
    (amh-data-platform, infrastructure/envs/dev-sa-east-1) — sem isso o Aurora
    recusa a conexao e a task de migrations falha em timeout, nao em erro de senha.
  EOT
  value       = aws_security_group.tasks.id
}

output "ecr_repository_url" {
  description = "Destino do `docker push` da imagem da aplicacao."
  value       = aws_ecr_repository.app.repository_url
}

output "github_supply_chain_role_arn" {
  description = <<-EOT
    ARN da role OIDC assumida por `.github/workflows/supply-chain.yml` para publicar
    SBOM + assinatura cosign. E' o valor da Actions variable `AWS_SUPPLY_CHAIN_ROLE_ARN`.
    NAO e' role de deploy: so' le e escreve no repositorio ECR da aplicacao.
  EOT
  value       = aws_iam_role.github_supply_chain.arn
}

output "ecs_cluster_name" {
  description = "Cluster onde rodam os componentes do maezo-operadora."
  value       = aws_ecs_cluster.this.name
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
  description = "FHIR consumido do HAPI da plataforma, pelo ALB interno — o trafego clinico nao sai da VPC."
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
    "--network-configuration 'awsvpcConfiguration={subnets=[${join(",", data.aws_subnets.private_app.ids)}],securityGroups=[${aws_security_group.tasks.id}],assignPublicIp=DISABLED}'",
  ])
}

output "cloudflared_tunnel_token_secret" {
  description = <<-EOT
    Onde gravar o token do tunel Cloudflare. Formato {"token":"<TOKEN>"}, e use
    `--secret-string file://arquivo.json`: JSON inline pelo PowerShell perde as aspas
    e o ECS rejeita com "invalid character 'k'" (ja aconteceu aqui em 13/08).
  EOT
  value       = aws_secretsmanager_secret.cloudflared_token.name
}

output "destino_interno_do_cockpit" {
  description = "O que declarar como Public Hostname -> Service no painel do tunel."
  value       = "http://cibseven.${local.name}.internal:8080"
}
