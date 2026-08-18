variable "aws_region" {
  description = "Regiao. sa-east-1 por residencia de dado (LGPD) — o mesmo motivo que prende a plataforma de dados aqui."
  type        = string
  default     = "sa-east-1"
}

variable "aws_account_id" {
  description = "Conta de dados da AMH. Usado como trava em provider.allowed_account_ids e na montagem de ARNs."
  type        = string
  default     = "203312548462"
}

# ---------------------------------------------------------------------------
# Rede e banco — recursos da PLATAFORMA, lidos por data source. Nunca criados aqui.
# ---------------------------------------------------------------------------

variable "vpc_name" {
  description = "Name tag da VPC da plataforma (infrastructure/modules/network do amh-data-platform)."
  type        = string
  default     = "amh-data-vpc-dev"
}

variable "aurora_cluster_identifier" {
  description = <<-EOT
    Cluster Aurora compartilhado. Decisao do dono (2026-08-13): reaproveitar em vez de
    subir um proprio, porque as roles `maezo_app` e `cibseven_app` JA existem nele com
    senha gravada desde 2026-06-04 — foram criadas exatamente para isto.
  EOT
  type        = string
  default     = "amh-aurora-hapi-dev"
}

variable "aurora_database_name" {
  description = <<-EOT
    Database alvo das migrations do maezo. `applications/maezo/migrations/README.md` (repo da
    plataforma) descreve os schemas `maezo` e `cibseven` dentro de um database `maezo`.
    ATENCAO: a existencia desse database NAO foi verificada — nao ha rota da minha maquina ate
    a subnet privada. A task de migrations e' o primeiro ponto onde isso aparece; se falhar com
    "database maezo does not exist", rode o bootstrap descrito no runbook.
  EOT
  type        = string
  default     = "maezo"
}

variable "aurora_bootstrap_database" {
  description = <<-EOT
    Database usado apenas para ABRIR a conexao da task de bootstrap, antes de o
    database do maezo existir. `hapi` e' o unico que o cluster tem ao nascer
    (DatabaseName do modulo aurora-cluster da plataforma). Nada e' criado nele.
  EOT
  type        = string
  default     = "hapi"
}

variable "aurora_app_secret_name" {
  description = "Secret com usuario/senha da role de aplicacao do maezo (populado fora do Terraform)."
  type        = string
  default     = "amh-aurora-hapi-dev/maezo_app"
}

variable "cibseven_app_secret_name" {
  description = "Secret com usuario/senha da role do engine BPMN."
  type        = string
  default     = "amh-aurora-hapi-dev/cibseven_app"
}

variable "hapi_internal_alb_name" {
  description = "ALB interno do HAPI FHIR — por onde o maezo consome FHIR sem sair para a internet."
  type        = string
  default     = "amh-hapi-fhir-dev-int"
}

# ---------------------------------------------------------------------------
# Aplicacao
# ---------------------------------------------------------------------------

variable "tenant_id" {
  description = "Tenant desta instancia. Instancia-por-cliente e' principio inegociavel do produto (README, principio 3)."
  type        = string
  default     = "amh"
}

variable "image_tag" {
  description = <<-EOT
    Tag da imagem no ECR. NAO use `latest` fora de um bootstrap: o service so troca de imagem
    quando a task definition muda, e `latest` torna impossivel saber o que esta rodando.
    A CD passa o SHA curto do commit.
  EOT
  type        = string
  default     = "bootstrap"
}

variable "log_retention_days" {
  description = "Retencao dos log groups. 30 dias em dev; auditoria de verdade nao mora em CloudWatch (ADR de retencao de 5 anos)."
  type        = number
  default     = 30
}

variable "worker_cpu" {
  description = "CPU do worker-runtime (256 = 0.25 vCPU)."
  type        = number
  default     = 512
}

variable "worker_memory" {
  description = "Memoria do worker-runtime em MB."
  type        = number
  default     = 1024
}

variable "worker_desired_count" {
  description = <<-EOT
    Quantas tasks do worker-runtime (o harness registra os workers BPMN por task; medido:
    124 registrados).
    Nasceu em ZERO enquanto o banco nao estava alcancavel — sem ele o harness falha
    fechado e registra zero workers. Migrations passaram e o engine subiu em 18/08/2026,
    entao o default agora e' 1: o valor declarado descreve o ambiente que EXISTE.
    Isso importa mais do que parece — com default 0, um `apply` sem `-var` derruba o
    servico em silencio, e quem aplica costuma nao estar olhando o desired_count.
  EOT
  type        = number
  default     = 1
}

variable "engine_image_tag" {
  description = <<-EOT
    Tag da imagem PROPRIA do engine (`amh/cibseven-maezo`), construida a partir de
    `deploy/cibseven/Dockerfile`. Sem o showcase de demonstracao e, portanto, sem o
    usuario `demo` que a imagem oficial recria a cada boot.
  EOT
  type        = string
  default     = "sem-showcase"
}

variable "cibseven_desired_count" {
  description = <<-EOT
    Quantas tasks do engine BPMN. Nasceu em ZERO enquanto o SG deste stack nao estava
    liberado no Aurora — sem isso o engine entraria em crash-loop cobrando Fargate sem
    entregar nada. A liberacao foi aplicada (PR #160 no amh-data-platform) e as
    migrations rodaram limpas, entao o default e' 1.
    NAO volte isto para 0 como "valor seguro": zero aqui significa DERRUBAR O MOTOR de
    processos, e um `apply` sem `-var` passaria a fazer isso sozinho.
    Replica unica: o engine faz aquisicao de job com lock em banco; duas replicas em dev
    so multiplicam contencao.
  EOT
  type        = number
  default     = 1
}

variable "agent_cpu" {
  description = "CPU de cada agente. LLM e' I/O-bound (espera resposta de rede), nao CPU-bound."
  type        = number
  default     = 512
}

variable "agent_memory" {
  description = "Memoria de cada agente em MB."
  type        = number
  default     = 1024
}

variable "caso_sintetico_zona_geral" {
  description = <<-EOT
    Declara que ESTE AMBIENTE não contém dado de paciente real e, portanto, a narrativa
    do dossiê do Rafael pode ser servida pelo provedor da zona GERAL (Bedrock).

    NÃO é a ratificação de DPO/médico auditor. São afirmações diferentes:
      - ratificação: o DADO pseudonimizado pertence à zona geral (via de produção);
      - esta chave:  o AMBIENTE não vê dado real (via de teste).

    Declaração do dono, 18/08/2026, textual: "só é aceitável porque vamos usar caso
    fictício". O código não pode verificar que o caso é fictício — nenhum código pode.
    Por isso a chave grita em WARNING a cada boot e aparece no diff da task definition.

    DESLIGUE antes de qualquer carga com dado real. Com `true` num ambiente que veja
    paciente de verdade, houve transferência internacional de dado de saúde sem base
    legal — o perfil `global.*` do Bedrock roteia entre regiões por desenho.
  EOT
  type        = bool
  default     = false
}

variable "phi_zone_provider" {
  description = <<-EOT
    Provedor de inferencia dos agentes da ZONA PHI (rafael, marina).

    So dois valores satisfazem o contrato da zona (os cinco criterios de
    `phi_zone_denial_reasons`), e a diferenca entre eles e' o que voce esta escolhendo:

      phi_zone_mock  — roda o grafo do agente de verdade, com narrativa SINTETICA e
                       ZERO egresso (`is_mock=True` visivel em log e em /readyz).
                       Prova que o caminho de agente funciona. NAO e' modelo.

      br_resident    — endpoint BR-resident real. Exige `MAEZO_PHI_VENDOR_DPA_REF`
                       (sem ele o provedor RECUSA construir) E um transporte §8.2
                       injetado — que hoje NAO EXISTE no repositorio, entao ele
                       resolve para `RefusingBrRegionalTransport`.

    `bedrock` NAO e' opcao aqui: declara `phi_allowed=False` e a fachada recusa antes
    do egresso. E nao ha como consertar isso por configuracao — medido em 18/08/2026
    na conta 203312548462: `anthropic.claude-opus-5` em sa-east-1 so aceita
    INFERENCE_PROFILE, e TODOS os perfis disponiveis sao `global.*`. Nao existe perfil
    BR-resident de Claude nesta regiao.
  EOT
  type        = string
  default     = "phi_zone_mock"

  validation {
    condition     = contains(["phi_zone_mock", "br_resident"], var.phi_zone_provider)
    error_message = "phi_zone_provider deve ser 'phi_zone_mock' ou 'br_resident' — nenhum provedor de zona geral satisfaz o contrato PHI."
  }
}

variable "inference_provider" {
  description = <<-EOT
    `bedrock` = chamada real ao modelo. Qualquer outro valor cai no stub do T1.7.
    Provado ao vivo em 12/08/2026 no ambiente local.
  EOT
  type        = string
  default     = "bedrock"
}

variable "bedrock_model_id" {
  description = <<-EOT
    Modelo da zona GERAL. O perfil `global.*` roteia entre regioes por desenho
    (inference.py:485) — aceitavel aqui porque a zona geral nao ve dado de paciente,
    e inaceitavel na zona PHI sem o DPA do endpoint BR-resident.
  EOT
  type        = string
  default     = "global.anthropic.claude-opus-5"
}

variable "bucket_fonte_build" {
  description = <<-EOT
    Bucket que guarda o zip de fonte do build. EXISTENTE, de outro state — criar bucket
    nesta conta e' negado por SCP da organizacao (medido 18/08/2026). Escrevemos apenas
    sob `prefixo_fonte_build`.
  EOT
  type        = string
  default     = "amh-pipeline-artifacts-dev-sa-east-1"
}

variable "prefixo_fonte_build" {
  description = "Prefixo dentro do bucket compartilhado. Isola a nossa fonte da dos vizinhos."
  type        = string
  default     = "maezo-operadora"
}

variable "canal_teste_desired_count" {
  description = <<-EOT
    Replicas do Canal de Teste. 1 para o time usar; 0 para tirar do ar.
    Nao tem autenticacao propria e faz proxy para o engine — so' com Access na frente.
  EOT
  type        = number
  default     = 1
}

variable "hostname_cockpit" {
  description = <<-EOT
    Hostname publico do Cockpit, usado APENAS para montar o link exibido na pagina do
    Canal. Quem abre a pagina esta no navegador, fora da VPC, entao o link nao pode ser
    o nome do Cloud Map.
  EOT
  type        = string
  default     = "maezo-dev.austa.com.br"
}

variable "cloudflared_desired_count" {
  description = <<-EOT
    Replicas do tunel Cloudflare (a borda). Nasceu em ZERO porque sem o token no cofre o
    container sobe e morre em loop, e um crash-loop na borda e' o tipo de ruido que faz o
    time parar de olhar log. Tunel criado, hostnames publicados, politica de Access no ar
    e token gravado (18/08/2026) — default 1.
    Zero aqui NAO e' economia: e' tirar do ar os dois hostnames publicos de uma vez.
  EOT
  type        = number
  default     = 1
}

variable "cloudflared_image_tag" {
  description = "Tag da imagem do cloudflared. Fixa de proposito — a borda nao se atualiza sozinha."
  type        = string
  default     = "2026.8.2"
}

variable "kafka_desired_count" {
  description = <<-EOT
    Broker Kafka de DEV. 1 para o fluxo andar (o primeiro passo do SP-OP-AUTH-001
    publica evento e falha fechado sem broker); 0 para zerar o custo. Nao substitui
    o MSK em producao — ver o cabecalho de service-kafka.tf.
  EOT
  type        = number
  default     = 1
}

variable "agent_runtime_mode" {
  description = <<-EOT
    `production` liga os gates fail-closed (checkpoint duravel, assinatura de Agent Card,
    recusa de MAEZO_SPEC_DIR — Wave-1 Q-6). `local` afrouxa. Em qualquer ambiente da AWS isto
    e' `production`: um ambiente de nuvem rodando em modo local seria uma mentira silenciosa
    sobre quais controles estao ativos.
  EOT
  type        = string
  default     = "production"

  validation {
    condition     = contains(["production", "local"], var.agent_runtime_mode)
    error_message = "agent_runtime_mode deve ser 'production' ou 'local'."
  }
}
