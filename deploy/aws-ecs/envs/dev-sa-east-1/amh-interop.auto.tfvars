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
