// Entradas NAO SECRETAS do BFF humano do portal (ADR-0049 D3/D4/D8/D11, DL-0048).
//
// Por que versionado e `.auto.tfvars`: se `portal` voltar a ser null num apply futuro, o
// Terraform DESTROI task definition, roles, SGs, NLB e listener do portal. Deixar esses
// valores no terminal de quem aplicou nao existe para o time. Nada aqui e' segredo:
// ARNs, subnets, IDs de client publico e IPs de endpoint sao metadados de infra. A DSN vive
// SO' no Secrets Manager (`maezo-operadora/dev/portal/amh/session-dsn`), e o Terraform le
// apenas os metadados dela.
//
// `portal_enabled` fica FALSO aqui. Ligar e' um segundo passo, depois das 7 verificacoes
// de `portal.md`/1.7 — e hoje ele esta' bloqueado no DNS externo (ver
// docs/runbooks/portal-dev-provisionamento.md).
//
// Medido na conta 203312548462 / sa-east-1 em 21/09/2026.

portal_enabled = false

portal = {
  // Tenant desta instancia; tem de ser igual a `tenant_id` do ambiente.
  tenant = "amh"

  // Pool Cognito `amh-maezo-bpm-dev` (sa-east-1_9oKv7gHOJ). O issuer e' o host regional do
  // cognito-idp, NAO o dominio do managed login — e' dele que sai o JWKS
  // (`{issuer}/.well-known/jwks.json`, src/maezo/portal/api/config.py:75).
  issuer = "https://cognito-idp.sa-east-1.amazonaws.com/sa-east-1_9oKv7gHOJ"

  // Dominio do managed login do MESMO pool (Domain=amh-maezo-bpm-dev, CustomDomain=null,
  // medido com describe-user-pool). Daqui saem `/oauth2/authorize` (browser) e
  // `/oauth2/token` (servidor).
  cognito_origin = "https://amh-maezo-bpm-dev.auth.sa-east-1.amazoncognito.com"

  // Client PUBLICO dedicado, criado em 21/09/2026: `portal-humano-amh-dev`.
  // Sem client secret, AllowedOAuthFlowsUserPoolClient=true, flows exatamente ["code"],
  // scope ["openid"], callback exatamente {public_origin}/api/v1/portal/auth/callback,
  // ExplicitAuthFlows so' ALLOW_REFRESH_TOKEN_AUTH. O S256/state/nonce sao do BFF: o
  // Cognito nao tem flag que prove PKCE, entao a evidencia e' a leitura sanitizada do
  // client no runbook.
  human_client_id      = "61gml104sr5nc8jskrptstua0u"
  human_client_purpose = "dedicated-human-code-pkce"

  // Client M2M EXISTENTE (`agent-rafael-omni`), declarado so' para comparacao: o validador
  // exige que seja igual a `fhir_cognito_client_id` e diferente do humano. Conferido contra
  // a task definition VIVA de agent-rafael (FHIR_CLIENT_ID), nao contra o default do repo.
  machine_client_id = "3kr6l4lq5mgq84rta88a2ugpd"

  // Hostname publico decidido pelo dono. A zona `austa.com.br` e' EXTERNA a esta conta
  // (nao existe hosted zone publica no Route53 daqui): o dono do DNS aponta este nome para
  // o alias do NLB e cria o CNAME de validacao do ACM.
  public_origin = "https://portal-maezo-dev.austa.com.br"

  // Artefato `8b014a60`, verificado DENTRO da imagem como uid 1000 em 21/09/2026: modulo
  // do portal presente, `portal.api.production:create_production_app` presente, migration
  // 0012 no wheel, e as 3 raizes publicas do RDS sa-east-1 no contexto TLS padrao do
  // Python com check_hostname ligado. SBOM/assinatura desta imagem NAO existem (o job de
  // syft/cosign do cd.yml esta' atras do gate AWS_ENABLED, ausente) — pendencia declarada.
  image_digest = "sha256:685ddb6d80c89f713ac776700bb7b759cb56253d85ab1b273cc7946ecdf12c3c"

  // Segredo externo criado fora do Terraform (SCP `deny-secrets-without-rotation` exige o
  // OrganizationAccountAccessRole). O SecretString INTEIRO e' a DSN asyncpg da role
  // dedicada `portal_bff_amh`; nao e' JSON e nao e' o segredo de maezo_app/cibseven_app/
  // master.
  database_secret_arn = "arn:aws:secretsmanager:sa-east-1:203312548462:secret:maezo-operadora/dev/portal/amh/session-dsn-e3YgbH"

  // BOOTSTRAP, TEMPORARIO — trocar pelo certificado real assim que ele emitir.
  //
  // O certificado REAL de `portal-maezo-dev.austa.com.br` (validacao DNS) existe e e'
  //   arn:aws:acm:sa-east-1:203312548462:certificate/34be6754-441c-4ab5-add0-67037d628c72
  // mas fica `PENDING_VALIDATION` ate' o dono do DNS externo criar o CNAME de validacao
  // (valores no runbook). MEDIDO em 21/09/2026, nao presumido: o listener TLS do NLB
  // RECUSA certificado pendente —
  //   CreateListener 400 UnsupportedCertificate: "The certificate '...34be6754...' must have
  //   a fully-qualified domain name, a supported signature, and a supported key size."
  // Sem certificado utilizavel nao existe listener, e sem listener o `aws_ecs_service` do
  // portal nem e' criado (`depends_on`) — ou seja, o passo 1.5 inteiro travaria esperando
  // um DNS que nao esta nesta conta.
  //
  // Por isso o valor ativo abaixo e' um self-signed importado no ACM em 21/09/2026, com
  // `notBefore` um dia no passado (clock skew no import ja custou uma volta antes), emitido
  // SO' para materializar o listener enquanto o servico esta em ZERO tasks. Nada serve
  // trafego por ele. Trocar para o real e' update in-place do `certificate_arn`.
  // NAO ative o portal (`portal_enabled=true`) com este certificado: navegador nenhum confia
  // nele e o login PKCE nao fecha.
  certificate_arn = "arn:aws:acm:sa-east-1:203312548462:certificate/46784e0d-4ab0-4f4b-8932-c544b0aa3871"

  // Subnets do NLB: Tier=public do mesmo VPC (vpc-0a850a9d40b36ac5b), AZs distintas
  // (1a e 1b), com rota 0.0.0.0/0 para o IGW. As TASKS nao usam estas: o service roda em
  // Tier=private-app sem IP publico (service-portal.tf).
  public_subnet_ids = [
    "subnet-0f6fd7c008207fe5b", // sa-east-1a, 10.40.0.0/24
    "subnet-0b9f91b867920aa44", // sa-east-1b, 10.40.1.0/24
  ]

  // Egresso 443 EXATO do SG das tasks. Resolvido DE DENTRO DA VPC em 2026-09-21T17:44Z
  // (task avulsa no cluster), com handshake TLS confirmado em cada destino. `/32` e'
  // controle de IP, nao de FQDN: se o Cognito mudar de endereco, a lista muda por revisao
  // — nunca por alargamento para 0.0.0.0/0.
  //
  // O S3 dos layers do ECR NAO entra aqui: naquele VPC ele e' GATEWAY endpoint
  // (vpce-09e34704570f7f756, prefix list pl-6aa54003), nao tem ENI e os IPs publicos do
  // bucket `starport` rotacionam. Ele e' liberado por uma regra propria de prefix list em
  // portal-network.tf (`portal_s3_layers`).
  https_egress_ipv4_cidrs = [
    // cognito-idp.sa-east-1.amazonaws.com — JWKS do issuer (publico, via NAT).
    "52.67.144.206/32",
    "54.20.171.151/32",
    "54.94.110.228/32",
    // amh-maezo-bpm-dev.auth.sa-east-1.amazoncognito.com — POST /oauth2/token (via NAT).
    "52.67.250.153/32",
    "52.67.98.193/32",
    "54.20.130.20/32",
    // ENIs do VPC endpoint de interface com.amazonaws.sa-east-1.ecr.api (vpce-0f200a15dbd1824ec).
    "10.40.40.27/32",
    "10.40.41.104/32",
    // ENIs do VPC endpoint de interface com.amazonaws.sa-east-1.ecr.dkr (vpce-0548b6b556a17b262).
    "10.40.40.123/32",
    "10.40.41.249/32",
    // ENIs do VPC endpoint de interface com.amazonaws.sa-east-1.secretsmanager (vpce-0034f3f1e8e8c86b8).
    "10.40.40.139/32",
    "10.40.41.125/32",
    // ENIs do VPC endpoint de interface com.amazonaws.sa-east-1.logs (vpce-0309137590270feae).
    "10.40.40.233/32",
    "10.40.41.76/32",
  ]

  // Perfil `identity` puro. O perfil `staff` (fila de casos) exige uma imagem derivada, o
  // bundle de materiais assinado e a autoridade nativa — nada disso existe hoje, e o
  // pacote recusa metade disso. Ver a pendencia no runbook.
  staff = null
}
