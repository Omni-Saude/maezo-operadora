# Teste de CONFIGURACAO do cluster ECS do dev. AWS mockada: nenhuma credencial, rede ou apply real.
# Container Insights fica DESLIGADO (e74990b5, 04/11/2026: 463 metricas customizadas, ~US$ 139/mes,
# sem alarme nem painel que as leia). Sem esta assercao, um apply com -target que puxe o
# aws_ecs_cluster.this a partir de um checkout que diga outra coisa religaria a cobranca em silencio.
# Espelha deploy/aws-ecs/envs/prod-sa-east-1/tests/sem-trafego.tftest.hcl.
mock_provider "aws" {}

variables {
  # Fixtures sinteticas, nunca implantaveis: o Terraform exige valor para toda variavel
  # obrigatoria mesmo quando o run so' mira o cluster.
  diagnostics_image_digest = "sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
  worker_image_digest      = "sha256:dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd"
  agents_image_digest      = "sha256:eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
  engine_image_digest      = "sha256:ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
  fhir_cognito_client_id   = "machine123"
  portal_enabled           = false
  portal                   = null
}

run "container_insights_desligado" {
  command = plan
  plan_options {
    target = [aws_ecs_cluster.this]
  }
  assert {
    condition     = one([for s in aws_ecs_cluster.this.setting : s.value if s.name == "containerInsights"]) == "disabled"
    error_message = "Container Insights tem de ficar desligado no dev (decisao de custo, e74990b5)."
  }
}
