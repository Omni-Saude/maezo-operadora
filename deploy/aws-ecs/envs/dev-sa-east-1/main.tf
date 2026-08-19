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

  # Segredo mestre gerido pela AWS (`manage_master_user_password = true` no modulo
  # aurora-cluster da plataforma). Lido do data source em vez de fixado: o ARN
  # carrega sufixo aleatorio e mudaria se o segredo fosse recriado.
  aurora_master_secret_arn = data.aws_rds_cluster.shared.master_user_secret[0].secret_arn

  # Endereco do broker de dev, pelo Cloud Map. Sem esta variavel o codigo cai no
  # default `localhost:9092` (adapters/amh/settings.py:49) e o primeiro passo do
  # processo morre em KafkaConnectionError.
  kafka_bootstrap = "kafka.${local.name}.internal:9092"

  fhir_base_url = "http://${data.aws_lb.hapi_internal.dns_name}/fhir"

  # Descoberta de servico interna: o worker fala com o engine BPMN por nome DNS
  # estavel, nao por IP de task (que muda a cada deploy).
  cibseven_base_url = "http://cibseven.${local.name}.internal:8080/engine-rest"

  # Ingresso do agente que executa turnos. O rafael e' o unico com a rota ligada
  # (ver `service-agents.tf`), por isso o endereco e' dele e nao de um agente qualquer.
  agent_ingress_url = "http://agent-rafael.${local.name}.internal:8000"
}
