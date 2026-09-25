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

variable "fhir_partition" {
  description = <<-EOT
    Particao do HAPI que o Rafael le. O HAPI e' multitenant POR URL, e o caminho sem
    particao nao serve busca — medido: `/fhir/Patient` devolve 400 "does not know how to
    handle", `/fhir/omni/Patient` devolve 200.

    `omni` e' a operadora, e e' a particao semanticamente correta para autorizacao previa.
    ATENCAO ao que isso significa hoje: medido em 19/08/2026, a particao `omni` tem
    Patient=0, Condition=0, Encounter=0. O dado clinico vive nas particoes dos HOSPITAIS
    (austa_hospital=90.565 pacientes, imc=13.870, hmc=1.590), porque a operadora tem guia e
    o hospital tem prontuario.

    Ler o prontuario de um beneficiario da operadora e' portanto uma resolucao pelo MPI
    (`mpi/patient.read`, que o client tem) seguida de leitura na particao do prestador — e
    NAO uma troca desta variavel. Apontar isto para `austa_hospital` faria o Rafael ler
    prontuario de hospital com caso de operadora, o que o isolamento por token impede de
    propósito: token de `omni` recebe 403 na particao `austa_hospital`.
  EOT
  type        = string
  default     = "omni"
}

variable "fhir_cognito_client_id" {
  description = <<-EOT
    App client do Cognito para a particao escolhida — `agent-rafael-<particao>`, criado no
    amh-data-platform (PR #167). UM CLIENT POR AGENTE POR EMPRESA, e o motivo esta escrito
    no cognito.tf de lá: o token M2M nao carrega claim de tenant de proposito, porque "um
    agente que escolhe o proprio tenant nao tem fronteira". O SEGREDO E' A FRONTEIRA, e o
    raio de um vazamento e' uma empresa.

    O id e' gerado pela AWS: se o client for recriado, este valor muda. Fica como variavel
    (e nao derivado) porque nao existe data source de client por NOME — e um valor errado
    aqui falha alto, no boot, em vez de silenciosamente.
  EOT
  type        = string
  default     = "3kr6l4lq5mgq84rta88a2ugpd" # agent-rafael-omni
}

variable "fhir_token_url" {
  description = "Endpoint de token do pool amh-maezo-bpm-dev (client_credentials)."
  type        = string
  default     = "https://amh-maezo-bpm-dev.auth.sa-east-1.amazoncognito.com/oauth2/token"
}

variable "fhir_scope" {
  description = <<-EOT
    Escopos pedidos ao Cognito. PEDIR UM ESCOPO QUE O CLIENT NAO TEM faz o Cognito recusar
    o TOKEN INTEIRO com 400, antes de chegar ao servidor clinico — o agente perde o turno
    descobrindo isso. Mantenha alinhado com `agent-rafael` no cognito.tf.

    `Coverage.read` foi CRIADO em 19/08/2026: o resource server tinha 17 escopos e nenhum de
    Coverage, e `search_coverage` e' uma das duas unicas chamadas FHIR do grafo do Rafael.
    O interceptor exige escopo por tipo de recurso — sem ele, 403 "Escopo insuficiente".
  EOT
  type        = string
  default     = "fhir/Patient.read fhir/Coverage.read fhir/Encounter.read fhir/Condition.read"
}

variable "phi_vendor_dpa_ref" {
  description = <<-EOT
    Referência ao INSTRUMENTO CONTRATUAL que garante residência e retenção zero para a
    inferência da zona PHI. O adaptador RECUSA construir sem isto, e o motivo está escrito
    no próprio `inference.py`: para que a declaração de capacidade "não chegue à produção
    por decisão de um agente".

    SEM DEFAULT, de propósito, e isso é o ponto: quem preenche é quem tem autoridade para
    afirmar que o instrumento existe. Um default aqui transformaria a atestação numa linha
    de código, que é exatamente o que o portão foi escrito para impedir.

    Para `bedrock_br` o instrumento são os Termos de Serviço da AWS — o contrato que a
    empresa já tem, sem fornecedor novo. Para `br_resident` com endpoint de terceiro, é o
    DPA assinado com aquele fornecedor.

    O valor viaja para o container e aparece no header `X-Maezo-Vendor-Dpa-Ref` de cada
    requisição: é o registro de QUAL instrumento foi invocado naquela chamada.
  EOT
  type        = string
  default     = ""
}

variable "phi_endpoint_url" {
  description = <<-EOT
    Endpoint da inferência da zona PHI. Precisa passar `br_endpoint_denial_reasons`, que
    exige https, sem userinfo, sem query, e host na allowlist.

    Para `bedrock_br`: `https://bedrock-runtime.sa-east-1.amazonaws.com`. A REGIÃO ESTÁ NO
    NOME DO HOST, e é isso que faz a allowlist ser uma verificação de residência — outra
    região simplesmente não casa.
  EOT
  type        = string
  default     = "https://bedrock-runtime.sa-east-1.amazonaws.com"
}

variable "phi_model_id" {
  description = <<-EOT
    Modelo da zona PHI. O adaptador exige explícito: `BR_RESIDENT_CAPABILITIES` declara
    `supported_model_versions` VAZIO de propósito, porque a repo não sanciona nenhum id —
    então escolher é um ato registrado, não um default herdado.

    `mistral.mistral-large-3-675b-instruct`: medido em 19/08/2026 na conta 203312548462,
    é `ON_DEMAND` em sa-east-1 (servido na região, sem perfil `global.*`) e produziu o
    texto mais bem estruturado entre os quatro testados com prompt de dossiê real — os
    outros foram `deepseek.v3.2`, `zai.glm-5` e `qwen.qwen3-next-80b-a3b`, todos viáveis.

    RE-MEDIDO EM 11/09/2026, e o inventário completo está em
    `docs/design/zona-phi-modelos-sa-east-1.md`: sa-east-1 serve 37 modelos de texto
    `ON_DEMAND` in-region e outros 17 SÓ por perfil `global.*`. Os 17 incluem todo Claude
    atual, GPT-6 e Grok — nenhum tem perfil regional, e é por isso que os modelos que
    alguém preferiria são justamente os que NÃO podem servir a zona PHI. Trocar de modelo
    aqui é trocar qualidade, nunca residência: esta variável já alimenta o ARN regional do
    IAM em `secrets.tf`.
  EOT
  type        = string
  default     = "mistral.mistral-large-3-675b-instruct"
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

    09/09/2026 — o default era `bootstrap`, TAG QUE NAO EXISTE NO ECR (medido: 3da4596, ef1ca1d,
    b249391, 40dc6d4). Um `terraform apply` sem `-var image_tag=...` substituiria as sete task
    definitions por uma imagem inexistente e o circuit breaker derrubaria o cluster em rollback.
    O default agora DESCREVE O QUE RODA (`8b014a60`, desde 20/09): um apply sem variavel vira
    no-op nos servicos em vez de incidente. Promover imagem continua sendo passar a variavel.
  EOT
  type        = string
  default     = "07e72eae" # main de 23/09/2026 (cccb64c9 = #471 identidade nao e pedido de humano, sobre 79cb6ab3: #460 criticos da Helena, #456 conclusao pelo portal, #457 painel do canal, #467) promovida a dev em 23/09; antes e857e284 ((merge de #440) — promovida a dev por #445 (21/09); antes c745fd94 09/09, 40dc6d4 27/08
}

variable "webhook_receiver_image_tag" {
  description = <<-EOT
    Tag da imagem do RECEPTOR de webhook — separada de `image_tag` de proposito. O receptor
    mudou 1.593 linhas entre a imagem que os agentes rodam (`40dc6d4`, 27/08) e a `main` de
    09/09 (dedup por wamid, ack-then-queue, pseudonimizacao do wamid). Subir o receptor com a
    imagem velha testaria codigo que ja nao existe; subir TODOS os servicos com a nova seria um
    deploy de 400 commits de carona numa entrega que so' pede um servico. Quando os agentes
    forem promovidos para a mesma tag, esta variavel pode voltar a apontar para `image_tag`.
  EOT
  type        = string
  # RECONCILIADO EM 15/09/2026 contra o cluster. O default apontava para uma imagem de 11/09
  # enquanto o servico rodava a de 13/09 (`e86c0f89`) — dois dias de deriva, criada porque os
  # deploys de 12 e 13/09 foram feitos com `-var`/`-target` e o repo nunca foi atualizado.
  #
  # O QUE A DERIVA CUSTAVA, e nao e' hipotetico: medi no `terraform plan` de 15/09, ANTES desta
  # correcao, que um apply sem `-var` faria `e86c0f89 -> 5cdb09a5` no webhook-receiver. Isso apagaria o `response-v3` — a
  # correcao que proibiu a Helena AFIRMAR AUSENCIA DE ALERTA a um beneficiario — junto com a
  # saudacao e a pagina de escalonamento. Nao houve incidente porque o apply estava pausado; quem
  # rodasse um apply de rotina nao teria essa sorte.
  #
  # A REGRA, que o proprio texto desta variavel ja enunciava e que esta correcao cumpre: O DEFAULT
  # DESCREVE O QUE RODA. Promover e' um commit que muda este numero, nao um `-var` que so' existe
  # no terminal de quem aplicou.
  default = "07e72eae" # main de 23/09/2026 (cccb64c9 = #471 identidade nao e pedido de humano, sobre 79cb6ab3: #460 criticos da Helena, #456 conclusao pelo portal, #457 painel do canal, #467) promovida a dev em 23/09; antes e857e284 ((#440) promovida a dev por #445 (21/09); response-v3 de 13/09 (`e86c0f89`) que o receptor ja rodava
}

variable "webhook_receiver_desired_count" {
  description = "Replicas do receptor de webhook WhatsApp. 1 para exercitar a Helena com mensagem simulada; 0 para tirar do ar."
  type        = number
  default     = 1
}

variable "bridge_desired_count" {
  description = "Replicas da ponte de notificacao (consumer do topico interno -> start auditado de processos). 1 para o passo 9 existir; 0 para tirar do ar."
  type        = number
  default     = 1
}

variable "agent_resume_desired_count" {
  description = <<-EOT
    Replicas do consumidor de retomada (GAP-XHITL-4: `agents.events.process_completed` ->
    Helena retoma a conversa com a instrucao do humano). DEFAULT 0, e com 0 nenhum recurso de
    `service-agent-resume.tf` existe: o modulo RECUSA SUBIR enquanto nao houver custodia de
    destinatario decidida pelo dono/DPO (o WhatsApp exige o numero; v2 guarda so' o hash keyed).
    Teto 3 = particoes do topico no registro (`topic_registry.TopicEntry.partitions`); replicas
    alem disso ficariam ociosas no consumer group.
  EOT
  type        = number
  default     = 0

  validation {
    condition     = var.agent_resume_desired_count == null ? true : (var.agent_resume_desired_count >= 0 && var.agent_resume_desired_count <= 3)
    error_message = "agent_resume_desired_count aceita de 0 a 3 (particoes do topico process_completed)."
  }

  # Sem custodia o modulo recusa subir: ligar replicas sem ela so' produziria tasks morrendo.
  validation {
    condition     = var.agent_resume_desired_count == null ? true : (var.agent_resume_desired_count == 0 ? true : var.recipient_vault_enabled)
    error_message = "agent_resume_desired_count > 0 exige recipient_vault_enabled = true (ADR-0061)."
  }
}

variable "recipient_vault_enabled" {
  description = <<-EOT
    Liga a custodia CIFRADA do telefone do beneficiario (ADR-0061, status Proposto): chave KMS
    propria, role do receptor so' com kms:Encrypt, role do agent-resume so' com kms:Decrypt.
    DEFAULT false — com false nenhum recurso de `recipient-vault.tf` existe e o plan nao muda.
    NAO LIGAR sem ciencia registrada do DPO.
  EOT
  type        = bool
  default     = false
}

variable "recipient_vault_ttl_days" {
  description = "Retencao (dias apos a ultima mensagem) do telefone cifrado. Proposta do ADR-0061: 30; decisao final do DPO."
  type        = number
  default     = 30

  validation {
    condition     = var.recipient_vault_ttl_days == null ? true : (var.recipient_vault_ttl_days >= 1 && var.recipient_vault_ttl_days <= 365)
    error_message = "recipient_vault_ttl_days aceita de 1 a 365."
  }
}

variable "helena_zona_phi" {
  description = <<-EOT
    Decisao do dono (09/09/2026): a inferencia da Helena vai para o provedor da
    zona de saude (`phi_zone_provider`, hoje bedrock_br/Mistral em sa-east-1)? O grafo dela marca
    toda chamada como PHI; com `false` (default) e o `bedrock` comum, TODO turno cai em
    `falha_tecnica` -> humano (seguro, nao o desejado). `true` so' tem efeito com
    `phi_vendor_dpa_ref` preenchido — a atestacao continua sendo a outra metade.

    A PRE-CONDICAO QUE A DIRECAO LEVANTOU ("existe alternativa BR-resident ao Mistral?") FOI
    RESPONDIDA EM 11/09/2026 e nao bloqueia mais: sim, 36 alternativas in-region, e nenhuma e'
    mais BR-resident, porque residencia vem da regiao do endpoint e do ARN regional, nao do
    fornecedor do modelo. Provas no mesmo documento (`docs/design/zona-phi-modelos-sa-east-1.md`):
    o boot constroi `BrResidentInferenceProvider` com `phi_capable=True` / `br-sao-paulo` /
    `zero-retention`; a chamada real respondeu com `served_region=br-sao-paulo`; e o prompt
    `classify-v2` da propria Helena devolveu JSON valido no vocabulario do DMN em tres mensagens
    sinteticas. `rafael` e `marina` usam este mesmo provedor, modelo e instrumento desde 20/08 —
    ligar a Helena nao admite fornecedor novo.

    O DEFAULT SEGUE `false` DE PROPOSITO. O que falta nao e' evidencia, e' a autorizacao: ligar
    continua sendo `-var helena_zona_phi=true` num apply, para que o ato tenha autor.
  EOT
  type        = bool
  default     = false
}

variable "limite_por_conversa_por_minuto" {
  description = <<-EOT
    Teto de mensagens por CONVERSA por minuto no receptor (Frente 7.1). Protege do caso que de
    fato acontece: um numero em laco. `0` desliga, e desligar passa a ser um ato declarado aqui
    em vez de um esquecimento.

    6 e' ponto de partida — mais do que alguem digita conversando —, nao verdade medida. Quem diz
    se esta cortando quem nao devia e' `maezo_webhook_mensagem_limitada_total{escopo="conversa"}`.
  EOT
  type        = number
  default     = 6
}

variable "limite_por_tenant_por_minuto" {
  description = <<-EOT
    Teto global de mensagens por minuto no tenant (Frente 7.1). Protege do incidente: mil pessoas
    escrevendo ao mesmo tempo. `0` desliga.

    120 e' o dobro do pico das baterias de 13/09. ATENCAO: o limitador e' memoria de PROCESSO —
    com mais de uma replica do receptor, o teto efetivo e' N vezes este numero. Hoje
    `webhook_receiver_desired_count` e' 1, entao a conta fecha.
  EOT
  type        = number
  default     = 120
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

    `sem-showcase-executor-global` (09/09/2026): alem do acima, `jobExecutorDeploymentAware`
    = false. A tag anterior (`sem-showcase`) rodou de 18/08 a 09/09 com o executor de jobs
    restrito a deployments registrados em memoria — conjunto que ficava vazio apos cada
    reinicio — e por isso NENHUM relogio disparou nesse periodo (97 vencidos em 09/09).
    O motivo completo esta' no proprio Dockerfile.
  EOT
  type        = string
  default     = "sem-showcase-executor-global-grupos-hifen"
  # `-grupos-hifen` (09/09/2026): acrescenta `groupResourceWhitelistPattern` =
  # `[a-zA-Z0-9]+(-[a-zA-Z0-9]+)*` ao bpm-platform.xml (deploy/cibseven/configure-group-whitelist.sh).
  # Sem isso NENHUM dos ~40 candidateGroups canonicos dos contratos (`medico-auditor`,
  # `plantao-clinico`, ...) podia existir no motor — a lista branca da base e` [a-zA-Z0-9]+ — e toda
  # User Task apontava para fila vazia. Decisao do dono: admitir hifen no motor, nao renomear.
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
    condition     = contains(["phi_zone_mock", "br_resident", "bedrock_br"], var.phi_zone_provider)
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

variable "canal_teste_image_tag" {
  description = <<-EOT
    Tag da imagem do CANAL DE TESTE — separada de `image_tag` pelo mesmo motivo que
    `webhook_receiver_image_tag` foi: o canal ganhou a rota `/receptor/simular` e a pagina
    `escalonamento.html`, e essas duas coisas vivem NA IMAGEM (o canal roda como modulo desde
    19/08). Subir todos os servicos na tag nova seria um deploy de carona numa entrega que
    pede um servico.

    O default DESCREVE O QUE RODA. Se ele voltar a apontar para `image_tag`, um apply sem
    variavel devolveria o canal a uma imagem SEM a rota, e a pagina do escalonamento passaria
    a responder 404 no meio do teste de alguem — sem erro de Terraform, so' um botao que para
    de funcionar.
  EOT
  type        = string
  # RECONCILIADO EM 15/09/2026 contra o cluster. O default apontava para uma imagem de 11/09
  # enquanto o servico rodava a de 13/09 (`e86c0f89`) — dois dias de deriva, criada porque os
  # deploys de 12 e 13/09 foram feitos com `-var`/`-target` e o repo nunca foi atualizado.
  #
  # O QUE A DERIVA CUSTAVA, e nao e' hipotetico: medi no `terraform plan` de 15/09, ANTES desta
  # correcao, que um apply sem `-var` faria `e86c0f89 -> e4edbef6` no canal-teste. Isso apagaria o `response-v3` — a
  # correcao que proibiu a Helena AFIRMAR AUSENCIA DE ALERTA a um beneficiario — junto com a
  # saudacao e a pagina de escalonamento. Nao houve incidente porque o apply estava pausado; quem
  # rodasse um apply de rotina nao teria essa sorte.
  #
  # A REGRA, que o proprio texto desta variavel ja enunciava e que esta correcao cumpre: O DEFAULT
  # DESCREVE O QUE RODA. Promover e' um commit que muda este numero, nao um `-var` que so' existe
  # no terminal de quem aplicou.
  default = "07e72eae" # main de 23/09/2026 (cccb64c9 = #471 identidade nao e pedido de humano, sobre 79cb6ab3: #460 criticos da Helena, #456 conclusao pelo portal, #457 painel do canal, #467) promovida a dev em 23/09; antes e857e284 ((#440) promovida a dev por #445 (21/09); pagina de escalonamento de 13/09 (`e86c0f89`)
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

variable "a2a_outbox_relay_cpu" {
  description = "CPU do a2a-outbox-relay (256 = 0.25 vCPU). Daemon leve: so drena o outbox Postgres para o Kafka."
  type        = number
  default     = 256
}

variable "a2a_outbox_relay_memory" {
  description = "Memoria do a2a-outbox-relay em MB."
  type        = number
  default     = 512
}

variable "a2a_outbox_relay_desired_count" {
  description = <<-EOT
    Quantas tasks do a2a-outbox-relay (SC-01/R-004). 1 e' suficiente: o relay usa
    `claim_batch`/lease para concorrencia segura (`src/maezo/a2a/outbox.py`), entao mais
    replicas so' aumentam o paralelismo do drain, nunca corretude. 0 zera o custo sem quebrar
    nada alem da entrega de fatos A2A (o outbox acumula com seguranca — nenhuma linha e'
    perdida, so' fica pendente ate o proximo drain).
  EOT
  type        = number
  default     = 1
}

variable "helena_memoria_clinica" {
  description = <<-EOT
    MEMORIA CLINICA ENTRE TURNOS (Frente 2.1). `true` faz a Helena lembrar QUEM E' O PACIENTE —
    populacao e idades — de um turno para o outro, dentro de uma janela de horas.

    LIGADA por padrao, ao contrario das outras novidades do receptor. Desligada, o sistema fica no
    comportamento MEDIDO COMO ERRADO em 13/09/2026: um bebe de 11 meses triado pela tabela de
    ADULTO, tres vezes, porque a mae disse a idade num turno e o sintoma no seguinte.

    `false` faz cada turno comecar do zero, exatamente como antes desta frente — a reversao existe
    e e' um ato declarado, nao um esquecimento. So' tem efeito com checkpointer atachado: sem
    estado duravel nao ha turno anterior de onde lembrar.
  EOT
  type        = bool
  default     = true
}

# ---------------------------------------------------------------------------
# Coletor de metricas (Frente 5)
# ---------------------------------------------------------------------------

variable "prometheus_workspace_alias" {
  description = <<-EOT
    Prefixo do ALIAS do workspace gerenciado (AMP) onde o coletor escreve. Lido por data source,
    nunca criado por este state: o workspace e' da plataforma e compartilhado, e um `resource`
    aqui faria um `terraform destroy` do maezo apagar serie historica de outro time.
  EOT
  type        = string
  default     = "amh-prometheus-dev"
}

variable "metrics_collector_image" {
  description = <<-EOT
    Imagem do coletor. `aws-otel-collector` (ADOT) e nao o coletor upstream porque a escrita no
    workspace gerenciado exige assinatura SigV4 pela role da task, e a extensao `sigv4auth` ja'
    vem nesta distribuicao. VERSAO FIXA de proposito: `latest` num coletor significa que a forma
    da configuracao pode mudar num deploy que nao mexeu em nada nosso.
  EOT
  type        = string
  # DIGEST, nao tag (auditoria 18/09/2026): esta task carrega uma IAM role com `aps:RemoteWrite` e
  # alcanca a rede interna onde os agentes PHI expoem /metrics. Uma tag e mutavel — um republish
  # do repositorio publico troca o binario no proximo restart sem nenhum commit aqui. O digest e o
  # do INDICE OCI de v0.43.1 (sha256 dos bytes do manifest list; o ECS resolve a plataforma), e
  # todas as outras imagens deste ambiente ja sao digest-pinned. Para subir de versao: novo digest.
  default = "public.ecr.aws/aws-observability/aws-otel-collector@sha256:db725ac7007ef1d9b5a357a59f3b964ab9e1df23ebb0dfc694275019048b489b"
}

variable "metrics_collector_desired_count" {
  description = <<-EOT
    Quantas tasks do coletor. 1 e' o certo e 2 seria pior: duas replicas raspam os MESMOS alvos e
    escrevem a MESMA serie no workspace, o que produz amostras duplicadas no mesmo timestamp em
    vez de redundancia. Zero desliga a coleta — e volta ao estado que a Frente 5 veio corrigir,
    entao que seja um ato declarado.
  EOT
  type        = number
  default     = 1

  validation {
    condition     = var.metrics_collector_desired_count >= 0 && var.metrics_collector_desired_count <= 1
    error_message = "metrics_collector_desired_count so aceita 0 ou 1: duas replicas raspariam os mesmos alvos e duplicariam amostras no workspace."
  }
}

variable "metrics_collector_cpu" {
  description = "CPU do coletor (256 = 0.25 vCPU). Quatro alvos a cada 30s nao pedem mais."
  type        = number
  default     = 256
}

variable "metrics_collector_memory" {
  description = "Memoria do coletor em MB. 512 cobre o buffer de escrita remota com folga no volume de dev."
  type        = number
  default     = 512
}
