locals {
  env = "dev"

  # Prefixo com o nome do PRODUTO inteiro, nunca "maezo" sozinho: a plataforma de
  # dados tem um servico dela chamado maezo (applications/maezo/ — orquestracao
  # Flink/DLQ/RNDS). Cluster, ECR, log group e task family colidiriam.
  name = "maezo-operadora-${local.env}"

  base_tags = {
    Platform    = "maezo-operadora"
    ManagedBy   = "terraform"
    Repo        = "Omni-Saude/maezo-operadora"
    environment = local.env
    tenant      = var.tenant_id
    # Staging TECNICO: sem dado real de paciente. Vira PHI quando (e se) a zona PHI
    # for ligada — o que depende do DPA do endpoint de inferencia BR-resident
    # (docs/Tarefas_Pendentes.md §3.1), decisao juridica, nao de engenharia.
    DataClass = "none"
  }

  # Endpoint de escrita do Aurora. O reader nao e' usado: o maezo escreve cadeia de
  # auditoria e checkpoint em quase todo caminho.
  aurora_endpoint = data.aws_rds_cluster.shared.endpoint
  aurora_port     = data.aws_rds_cluster.shared.port

  # SG do cluster Aurora — usado APENAS como destino de uma regra de egress do SG
  # das tasks. Nao escrevemos nada no SG do Aurora (ver README, secao da
  # dependencia cross-repo).
  aurora_security_group_id = tolist(data.aws_rds_cluster.shared.vpc_security_group_ids)[0]

  fhir_base_url = "http://${data.aws_lb.hapi_internal.dns_name}/fhir"

  # Descoberta de servico interna: o worker fala com o engine BPMN por nome DNS
  # estavel, nao por IP de task (que muda a cada deploy).
  cibseven_base_url = "http://cibseven.${local.name}.internal:8080/engine-rest"
}
