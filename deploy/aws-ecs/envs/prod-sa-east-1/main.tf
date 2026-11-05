locals {
  env = "prod"

  # Prefixo com o nome do PRODUTO inteiro, nunca "maezo" sozinho: a plataforma de
  # dados tem um servico dela chamado maezo (applications/maezo/). Mesmo motivo do dev.
  name = "maezo-operadora-${local.env}"

  base_tags = {
    Platform    = "maezo-operadora"
    ManagedBy   = "terraform"
    Repo        = "Omni-Saude/maezo-operadora"
    environment = local.env
    tenant      = var.tenant_id
    # Producao: o ambiente nasce para receber dado REAL de beneficiario no cutover.
    # A tag ja' nasce `phi` para que os controles que filtram por DataClass (backup,
    # Config, Macie) o tratem como tal desde o primeiro recurso — mesmo sem trafego.
    DataClass = "phi"
  }

  # Plataforma de prod (amh-data-platform, envs/prod-sa-east-1 e -hapi): lida por REMOTE
  # STATE, nunca por ID cravado. Ver data.tf.
  plataforma = data.terraform_remote_state.foundation.outputs
  hapi       = data.terraform_remote_state.hapi.outputs

  # Endpoint de escrita do Aurora do HAPI de prod. O reader nao e' usado (mesmo motivo do dev).
  aurora_endpoint = data.aws_rds_cluster.shared.endpoint
  aurora_port     = data.aws_rds_cluster.shared.port

  # SG do cluster Aurora: destino da regra de egress 5432 do SG das tasks. O INGRESSO no banco
  # em prod e' pelo SG-MARCADOR do foundation (`aurora_hapi_clientes`), anexado a toda task
  # deste state (`local.task_security_groups`). Diferente do dev, NAO ha PR cruzado para
  # liberar o banco: o marcador ja' esta em `allowed_security_group_ids` do Aurora.
  aurora_security_group_id = tolist(data.aws_rds_cluster.shared.vpc_security_group_ids)[0]
  aurora_clientes_sg_id    = local.plataforma.aurora_hapi_clientes_sg_id

  # Lista de SGs que TODA task/servico deste env usa (tasks + marcador do Aurora).
  task_security_groups = [aws_security_group.tasks.id, local.aurora_clientes_sg_id]

  aurora_master_secret_arn = data.aws_rds_cluster.shared.master_user_secret[0].secret_arn

  kafka_bootstrap = "kafka.${local.name}.internal:9092"

  # FHIR: o endpoint INTERNO do HAPI de prod, como o -hapi o publica (HTTPS com o nome do
  # certificado interno, ou http://<ALB interno>). O ALB interno do HAPI so' aceita os SGs de
  # `ingest_allowed_source_sg_ids` daquele state: incluir `ecs_tasks_security_group_id` la' e'
  # passo do cutover (README).
  fhir_base_url = local.hapi.internal_ingest_url

  cibseven_base_url = "http://cibseven.${local.name}.internal:8080/engine-rest"
  agent_ingress_url = "http://agent-rafael.${local.name}.internal:8000"
  receptor_base_url = "http://webhook-receiver.${local.name}.internal:8080"

  # Bucket de artefatos de PIPELINE da conta de prod (pacotes dos agentes, ADR-0067 fase 3).
  bucket_artefatos = local.plataforma.lake_buckets.artifacts
}

# ---------------------------------------------------------------------------
# Decisao N5 (scripts/ci/check_engine_rest_auth.py): fora de dev o `engine-rest` NAO
# pode ser anonimo. Este ambiente assume subir uma imagem de engine `secured*`
# (deploy/cibseven/Dockerfile.secured-v2: certificado de cliente, sem fallback
# anonimo) e migrar os callers (pacote C). ATENCAO: a imagem de engine que roda no
# dev e' a ABERTA; copia-la para ca' sem o pacote C contradiz esta declaracao e e'
# bloqueio do cutover (README, "Bloqueios conhecidos").
# ---------------------------------------------------------------------------
locals {
  engine_rest_authentication = "client-certificate"
}
