// Valores NAO secretos da fonte REAL de cobranca do Lucas (contratos billing-status /
// subject-resolution da AMH; ver `amh-interop.tf` e `docs/runbooks/ligar-fonte-amh-dev.md`).
//
// Este arquivo NAO liga nada: sem `lucas_fonte_cobranca = "amh"` os locals de `amh-interop.tf`
// ficam vazios e nenhuma env, segredo ou regra de rede entra no plano. Ligar e' UMA linha em
// `lucas.auto.tfvars`, no momento certo (manifest v1.1 publicado e pin XRG-3 mergeado).
//
// Valores lidos (somente leitura) na conta de dados da AMH em 06/10/2026. NUNCA colocar aqui o
// client secret nem a chave do hash do telefone: os dois vivem no Secrets Manager (nomes nos
// defaults de `variables.tf`: amh/cognito/dev/maezo-operadora-interop e amh/interop/phone-lookup-key).

// ALB INTERNO `amh-interop-maezo-dev` (porta 80 dentro da VPC; sem caminho na URL).
amh_interop_base_url = "http://internal-amh-interop-maezo-dev-885055285.sa-east-1.elb.amazonaws.com"

// SG do ALB (`amh-interop-maezo-dev-alb-sg`): regra de saida 80 dedicada a partir do SG das tasks.
// O ingress correspondente (do SG das tasks do Maezo) ja' existe no lado da AMH.
amh_interop_alb_security_group_id = "sg-06aecbfc06289c9cf"

// Cognito: pool sa-east-1_9oKv7gHOJ, dominio `amh-maezo-bpm-dev` (ACTIVE); app client
// `maezo-operadora-interop` (client_credentials; escopos interop/billing.read,
// interop/subject.resolve, interop/profile.read).
amh_interop_token_url = "https://amh-maezo-bpm-dev.auth.sa-east-1.amazoncognito.com/oauth2/token"
amh_interop_client_id = "3n2cmh41eslj5mi2lf0b577teb"

// OpenAPI publicados, DENTRO da imagem (`deploy/Dockerfile` copia `config/` para /app/config).
// Os bytes sao colocados no passo de pin (runbook, secao 2), com digest conferido contra o pin.
amh_billing_status_openapi_path     = "/app/config/integrations/amh/openapi/billing-status.openapi.yaml"
amh_subject_resolution_openapi_path = "/app/config/integrations/amh/openapi/subject-resolution.openapi.yaml"

// Identidade do beneficiario na Helena pelos mesmos contratos (DL-0077, #677). So contexto: nao entra
// no prompt nem decide triagem/escala. Ligada em 07/10/2026; voltar: false.
helena_identidade_amh = true

// Historico curto da conversa da Helena (DL-0080, #689): ultimas 12 mensagens, 6 h, so' como dado no
// classificador e na redacao; liga tambem o modo coleta (triage_sufficiency, ainda nao ratificada).
// So' dev. Base LGPD PENDENTE do DPO; a varredura dos checkpoints antigos ainda nao existe. Voltar: false.
helena_historico = true

// Nova triagem da Helena (DL-0104, 14/10/2026): a tabela `helena_triage_admissibility` decide a saida do
// turno depois dos portoes de seguranca; pergunta clinica sem sintoma deixa de abrir caso. EXPERIMENTO so'
// em dev, decisao do dono; conteudo clinico DRAFT (medico-auditor pendente). Voltar: false (runbook
// docs/runbooks/helena-triagem-v2.md). DESLIGADA em 15/10/2026 a pedido do dono: o teste mostrou respostas
// genericas que nao resolviam a necessidade; o desenho sera refeito (catalogo v2) antes de religar.
helena_triagem_v2 = false

// Helena v4 (DL-0115, 16/10/2026): catalogo de necessidades v4 do dono. Especialista e carteirinha com texto
// fixo; sintoma leve so' com perguntas de alerta antes do caminho. Com ela ligada a nova triagem (v2) fica
// inerte. EXPERIMENTO so' em dev, decisao do dono; conteudo clinico DRAFT (medico-auditor pendente) e prazo de
// 14 dias uteis a confirmar pelo juridico. Voltar: false (runbook docs/runbooks/helena-v4.md).
helena_v4 = true

// Base de conhecimento de saude da Helena (ADR-0064, DL-0118, 17/10/2026): MODELO DE TESTE do dono, so' em dev.
// A Helena INFORMA (sem diagnosticar, sem conduta, sem dose) perguntas gerais de saude pela base curada
// `spec/conhecimento/saude`, sem abrir caso; dor sem alarme recebe perguntas de alerta (com a v4); recusa com
// caso aberto encerra o caso P2/P3 com confirmacao (msg.escalation.withdrawn, SP-OP-ESCALATION-001 v1.1.0 —
// deploy-processes ANTES de ligar). Revisao medica e juridica antes de producao. Voltar: false (runbook
// docs/runbooks/helena-base-saude.md).
helena_base_saude = true

# Acesso do beneficiario (DL-0083/DL-0084) LIGADO no dev em 08/10/2026 23:10 UTC, decisao do dono; testado
# ponta a ponta no mesmo dia (aceite, CPF, Helena -> Lucas, revogacao, consentimento gravado no lago).
acesso_beneficiario = true

// Fatos do plano na Helena pelo contrato TINA da AMH (DL-0081): elegibilidade, carteirinha, carencia e
// autorizacao do proprio beneficiario. Ligado so' no dev em 13/10/2026, decisao do dono, depois da
// publicacao do manifest v1.2 pela AMH (XRG-2 run 38346972222, AMH #232) e do pin `manifest_v1_2`.
// Base legal LGPD para fora do dev segue PENDENTE de ratificacao do DPO (DL-0081). Voltar: false.
helena_consultas_amh = true

// Lucas v4 (DL-0114; manifests v1.10 e v1.11 PUBLISHED em 16/10/2026): os OpenAPI do detalhamento do pagador e da
// 2a via (digest conferido contra o pin no boot). O executor so' e' composto quando `amh_interop_boleto_client_id`
// (o cliente Cognito DEDICADO da 2a via, criado pela AMH) estiver preenchido: ate' la o segundo sinal fica desligado.
// `maezo-operadora-interop-boleto` (unico escopo interop/billing.boleto), criado em 16/10/2026 (AMH #255); o client id e'
// publico por desenho, o segredo vive em `amh/cognito/dev/maezo-operadora-interop-boleto` (Secrets Manager).
// Voltar: "" (o Lucas v4 segue sem o segundo sinal).
amh_interop_boleto_client_id                 = "558ecnfomm33qbj6cd030rmlbh"
amh_billing_status_detalhamento_openapi_path = "/app/config/integrations/amh/openapi/billing-status-detalhamento.openapi.yaml"
amh_billing_segunda_via_openapi_path         = "/app/config/integrations/amh/openapi/billing-segunda-via.openapi.yaml"
