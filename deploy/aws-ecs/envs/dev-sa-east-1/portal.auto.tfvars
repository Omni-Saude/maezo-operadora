// Entradas NAO SECRETAS do BFF humano do portal (ADR-0049 D3/D4/D8/D11, DL-0048).
//
// Por que versionado e `.auto.tfvars`: se `portal` voltar a ser null num apply futuro, o
// Terraform DESTROI task definition, roles, SGs, NLB e listener do portal. Deixar esses
// valores no terminal de quem aplicou nao existe para o time. Nada aqui e' segredo:
// ARNs, subnets, IDs de client publico e IPs de endpoint sao metadados de infra. A DSN vive
// SO' no Secrets Manager (`maezo-operadora/dev/portal/amh/session-dsn`), e o Terraform le
// apenas os metadados dela.
//
// `portal_enabled` passou a TRUE em 21/09/2026, depois das verificacoes de `portal.md`/1.7 e
// depois que os dois pre-requisitos fora desta conta cairam no mesmo dia: o CNAME de validacao
// do ACM + o hostname na zona Cloudflare de `austa.com.br`, e o ingress 5432 do Aurora para o
// SG dedicado do portal (`Omni-Saude/amh-data-platform#175`, aplicado com -target).
//
// O DEFAULT DESCREVE O QUE RODA: se isto voltar a `false` num apply futuro, o servico do
// portal e' escalado para zero sem erro nenhum, e quem perceber vai perceber pelo 502.
//
// O que este `true` NAO entrega: o perfil `staff` continua `null` (ver o fim deste arquivo),
// entao o portal AUTENTICA e nao tem FILA DE CASOS — `MAEZO_PORTAL_CAPABILITIES=identity`.
// Os criterios 3 a 6 do mandato de 21/09 dependem da autoridade nativa, que nao existe em dev.
//
// Medido na conta 203312548462 / sa-east-1 em 21/09/2026.

// LIGADO DE NOVO EM 21/09/2026, com o portao de imagem de `portal.md`/1.4 FECHADO — nao
// contornado. O que mudou desde o `false` do review do dono (P1):
//   - existe chave de assinatura na conta (`alias/maezo-operadora-dev-image-signing`,
//     KMS assimetrica; assinar so' pela role do CI, com deny explicito para o resto);
//   - o digest deste portal (`sha256:d29ce828...`) tem SBOM (SPDX, syft) e assinatura
//     PUBLICADOS no ECR (`sha256-d29ce828....sig` / `.att`) e VERIFICADOS —
//     `cosign verify` e `cosign verify-attestation` verdes no run 35665780360, inclusive
//     pelo job que usa a role verify-only (sem `kms:Sign`);
//   - a verificacao virou PORTAO no caminho de entrega: o job `verificar` de
//     `.github/workflows/supply-chain.yml` roda em pull request sobre ESTE arquivo, entao
//     apontar o portal para um artefato nao assinado reprova o PR.
// Comando exato de verificacao: `docs/runbooks/supply-chain-imagem.md`.
//
// O QUE FICOU PROVADO ENQUANTO ESTEVE LIGADO (21/09/2026, ~19:40Z, e nao se perde ao desligar):
//   - servico 1/1, rolloutState=COMPLETED, steady state;
//   - GET https://portal-maezo-dev.austa.com.br/api/v1/portal/session -> HTTP 401 com
//     {"erro":"Nao foi possivel validar a sessao."}, TLS verificado (ssl_verify_result=0),
//     certificado servido CN=portal-maezo-dev.austa.com.br, emissor Amazon RSA 2048 M01;
//   - GET /api/v1/portal/auth/login -> 303 para /oauth2/authorize do pool, com
//     client_id=61gml104sr5nc8jskrptstua0u, response_type=code, scope=openid, redirect_uri no
//     hostname real, code_challenge_method=S256 (challenge de 43 chars), state e nonce, e
//     cookie __Host-maezo-login; HttpOnly; Secure; SameSite=lax; Max-Age=300;
//   - verificacao 1.7 #7: log do boot com 4 linhas de uvicorn e ZERO ocorrencias de
//     postgresql://|password|secret|__Host-|set-cookie|code_verifier|Bearer |eyJ.
//
// O QUE ESTE `true` CONTINUA NAO ENTREGANDO: `staff = null` (fim deste arquivo), entao o portal
// AUTENTICA e nao tem FILA DE CASOS (`MAEZO_PORTAL_CAPABILITIES=identity`).
portal_enabled = true

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

  // Hostname publico decidido pelo dono. A zona `austa.com.br` e' EXTERNA a esta conta —
  // ela vive no Cloudflare, e os dois registros (validacao do ACM + este hostname para o
  // NLB) foram criados la' em 21/09/2026, DNS-only. Resolucao publica conferida:
  // portal-maezo-dev.austa.com.br -> portal-cceba8c66318e03e-...elb.sa-east-1.amazonaws.com
  // -> 52.67.211.205 / 54.232.19.187.
  public_origin = "https://portal-maezo-dev.austa.com.br"

  // REPINADO EM 21/09/2026 para `e857e284` (main com o PR #455, as correcoes da Helena da
  // bateria de 21/09) — o MESMO artefato que o resto da frota ja roda em dev. Antes daqui
  // apontava para `8b014a60` (sha256:685ddb6d...), de 20/09: religar o portal naquele digest
  // seria testar o portal contra um build atrasado, e deixar o portal numa imagem diferente
  // da frota e' a divergencia que ninguem lembra de conferir quando algo quebra.
  //
  // SBOM e ASSINATURA DESTE digest existem e foram VERIFICADOS — e' o portao 1.4, agora
  // fechado: `cosign verify`/`verify-attestation` com a chave KMS
  // `alias/maezo-operadora-dev-image-signing`, artefatos `sha256-d29ce828....sig`/`.att` no
  // proprio ECR. Comando exato e o que ele prova: `docs/runbooks/supply-chain-imagem.md`.
  // Trocar este digest sem assinar o novo REPROVA o job `verificar` do workflow
  // `supply-chain.yml` no proprio PR.
  // 23/09/2026: imagem da `main` cccb64c9 (tag `cccb64c9`, com o #471 sobre 79cb6ab3), construida pelo CodeBuild
  // `maezo-operadora-dev-imagem` e assinada/atestada/verificada pelo `supply-chain.yml`
  // (workflow_dispatch com o digest). Traz os 3 criticos da bateria (#460), a conclusao pelo
  // portal (#456), o painel do canal (#457) e o alinhamento do teste psicossocial (#467).
  image_digest = "sha256:871ecc0b1dc460fae93d1b70d83cbd868c5abb44972b3d80480e2985973e5864"

  // Segredo externo criado fora do Terraform (SCP `deny-secrets-without-rotation` exige o
  // OrganizationAccountAccessRole). O SecretString INTEIRO e' a DSN asyncpg da role
  // dedicada `portal_bff_amh`; nao e' JSON e nao e' o segredo de maezo_app/cibseven_app/
  // master.
  database_secret_arn = "arn:aws:secretsmanager:sa-east-1:203312548462:secret:maezo-operadora/dev/portal/amh/session-dsn-e3YgbH"

  // Certificado REAL, emitido pelo ACM por validacao DNS em 21/09/2026 e valido ate'
  // 2027-04-06 (`Status=ISSUED`, `ValidationStatus=SUCCESS`, medido).
  //
  // HISTORIA, porque ela explica o self-signed que aparece no historico deste arquivo: a
  // zona `austa.com.br` e' do Cloudflare (`austin/crystal.ns.cloudflare.com`), nao ha hosted
  // zone publica no Route53 desta conta, e enquanto o CNAME de validacao nao existia o
  // certificado ficava `PENDING_VALIDATION`. O listener TLS do NLB RECUSA certificado
  // pendente (medido: `CreateListener 400 UnsupportedCertificate`), e sem listener o
  // `aws_ecs_service` do portal nem e' criado (`depends_on`) — entao o passo 1.5 foi
  // materializado com um self-signed importado
  // (`...certificate/46784e0d-4ab0-4f4b-8932-c544b0aa3871`) enquanto o servico estava em
  // ZERO tasks. Nada serviu trafego por ele.
  //
  // Os dois registros foram criados em 21/09/2026 na zona do Cloudflare, ambos DNS-ONLY:
  //   _a97abaa85b2ea90dec7850f381c79512.portal-maezo-dev -> _1d34fdfc01d78f0ba2abe5e7a19f0039.wzccmgtwzk.acm-validations.aws
  //   portal-maezo-dev -> portal-cceba8c66318e03e-da82ba413a53e0d5.elb.sa-east-1.amazonaws.com
  // O proxy do Cloudflare ficaria ERRADO nos dois: no primeiro ele reescreveria o CNAME que
  // a AWS consulta; no segundo ele terminaria o TLS nele mesmo, e o navegador veria o
  // certificado do Cloudflare em vez deste — e o callback do Cognito exige este.
  //
  // O self-signed continua importado no ACM (nao apagado de proposito: apagar um certificado
  // que ja esteve num listener nao ajuda ninguem a entender o historico). Ele nao esta em uso.
  certificate_arn = "arn:aws:acm:sa-east-1:203312548462:certificate/34be6754-441c-4ab5-add0-67037d628c72"

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
    // [25/09/2026] enderecos atuais medidos (o login quebrava: JWKS inalcancavel a partir do SG).
    "177.71.135.231/32",
    "54.207.182.198/32",
    "56.126.35.144/32",
    // amh-maezo-bpm-dev.auth.sa-east-1.amazoncognito.com — POST /oauth2/token (via NAT).
    "52.67.250.153/32",
    "52.67.98.193/32",
    "54.20.130.20/32",
    // [25/09/2026] endereco atual medido.
    "52.67.104.18/32",
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

  // Perfil `staff` (Onda 6, 25/09/2026, plano portal-autoridade-nativa-dev). Proveniencia por campo:
  staff = {
    // Segredo D-G criado pela OrganizationAccountAccessRole (file://), sob a CMK portal-staff
    // (kms-native-materials.tf). Pacote `portal-staff-material.v2` montado pelo `assemble` e aceito
    // pelo `verify` (decode_bundle do portal) com estes mesmos pins.
    material_secret_arn = "arn:aws:secretsmanager:sa-east-1:203312548462:secret:maezo-operadora/dev/portal/amh/staff-materials-R8JeAL"
    // O VersionId do segredo E o `material_version_id` do pacote (vira MAEZO_PORTAL_STAFF_MATERIAL_VERSION_ID):
    // publicado com ClientRequestToken = material_version_id. Um VersionId aleatorio derruba o init.
    material_secret_version_id = "amh-dev-staff-33ebb909c424cdfb8226b4ce28"
    material_kms_key_arn       = "arn:aws:kms:sa-east-1:203312548462:key/2d36e2a3-b73f-4de5-b174-7870ceeac409"
    // deploy/portal.Dockerfile sobre o app 8c3a2938 (branch ops/staff-onda4-runtime), CodeBuild
    // `8229e2ca-portal-staff`; assinado + SBOM pelo supply-chain.yml (run 36182463891).
    // Onda 8 / D-O (25/09): mesma derivacao sobre o app f9c5ba9e (branch ops/onda8-assignment-owner,
    // 4bd50928: fixes do plano de atribuicao). CodeBuild 4bd50928-portal-human, assinada pelo supply-chain.
    portal_image_digest = "sha256:449369ff29d2e5bae5ef0e49bbc93bd5c7f274c6322cefef7806d06c9c41340a"
    // `verify --print-manifest-digest` sobre o manifesto conferido (Onda 5).
    public_manifest_sha256 = "6ffe3a5d692f81b37a38fea066c1f259b80a882d381189019733815a7b1637ad"
    // SHA-256 do SPKI da raiz Ed25519 (recalculado por openssl/cryptography, Onda 2, delegacao N1).
    root_key_sha256 = "de41c7a0ebac65b3a90a2405002e5118f18ca76dfd38b7ef52161fb44865942c"
    // Designacao assinada e instalada (tools.staff_ops rows, releitura byte a byte).
    designation_sha256 = "86e47bc2e24a92e261acfec69933ea729684b9f63184a43aa7ef8a258434954b"
    // Log do boot do engine vivo: staff_native_configuration_digest=
    native_configuration_sha256 = "3eec4f4bef91e8ea63df0627cfcf545c9bb31c895cd12c014bc816fe28ceadc8"
    scope = {
      tenant               = "amh"
      environment          = "dev"
      engine_name          = "default"
      database_incarnation = "amh-aurora-hapi-dev:maezo:maezo_native:1"
    }
    // Servico `engine-native` do namespace (Cloud Map) -> NLB interno -> Tomcat 8443.
    native_origin             = "https://engine-native.maezo-operadora-dev.internal"
    native_server_spki_sha256 = "1c0c35483d42370b32c083e10a524c08ae29960c80668d3735cfb982bd6e15d4"
    native_schema             = "maezo_native"
    engine_schema             = "cibseven"
    // entries[read_requester] e entries[identity_verifier] da designacao assinada.
    read_key_sha256    = "b0af380e417859db02f6ecc1e271679fca27ab6ef557843985bf6e59aa3d56ac"
    witness_key_sha256 = "db0e2d5614bdcbea69e6d36b385440e7749d4aa6b5b8448e6bf3911fb9a98887"
    maximum_seconds    = 10
    // SG do NLB nativo (output engine_native_nlb_sg_id) e o SG/porta do Aurora compartilhado.
    native_https_security_group_id    = "sg-07f59839f3e19a8f2"
    native_database_security_group_id = "sg-0a8364a76c605d782"
    native_database_port              = 5432
  }

  // Plano humano (Onda 8 / H5 + D-O). `portal-human-material.v1` remontado (mesmas chaves) com a
  // admissao humana espelho rev 3 (engine com o fix do PortalReadCommand, code digest 4253d799);
  // segredo D-G sob a CMK portal-staff, VersionId = material_version_id. O BFF so liga o `human`
  // com `portal_assignment_source.state='active'` (staff-assignment, 25/09: active -> ja-ativa).
  human = {
    material_secret_arn        = "arn:aws:secretsmanager:sa-east-1:203312548462:secret:maezo-operadora/dev/portal/amh/human-materials-FAErGt"
    material_secret_version_id = "amh-dev-human-73097339c50d1bc85fcfd7e459"
    material_kms_key_arn       = "arn:aws:kms:sa-east-1:203312548462:key/2d36e2a3-b73f-4de5-b174-7870ceeac409"
    public_manifest_sha256     = "e406724fadcffe4d636c1433895bcde786d4e4ff8d5d40f68ff2efa7d5f059ff"
    // Imagem de operacao (tools.staff_ops human-init), CodeBuild 4bd50928-staff-ops, assinada.
    init_image_digest = "sha256:becc9e37e8d2afef854b79f14eedbc18f12b143722eee0651d39222b1e9179ee"
  }
}
