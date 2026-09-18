# BFF humano dedicado no ECS — ADR-0049 D3/D4/D8/D11, DL-0048

`service-portal.tf` executa `python -m maezo.portal.api` na porta 8080, usando a
imagem de aplicação por digest. Este pacote cobre a composição de identidade/sessão
existente. Não entrega o frontend, os 43 formulários, transporte de comandos ao engine,
ativação de decisões, staging ou liberação de produção.

O default `portal = null`, `portal_enabled = false` não cria recursos do portal.
Um objeto `portal` completo prepara task definition, roles, SGs e ingresso TLS,
com serviço em **zero tasks**. Só depois dos pré-requisitos externos verificados,
`portal_enabled = true` solicita uma task. Zero tasks nesta preparação não altera
os serviços existentes. Não use `-target` como receita de implantação.

## Entradas obrigatórias e donos

Os campos originais do objeto `portal` são obrigatórios; `staff` é opcional e nulo por default; não há tfvars com IDs, segredo,
origins ou digest fictícios de implantação. Os valores em `tests/` são sintéticos.

| Entrada | Origem e pré-requisito verificável |
|---|---|
| `tenant` | Tenant interno igual ao `tenant_id` do ambiente; uma implantação/state por tenant. Browser não escolhe tenant. |
| `issuer` | Issuer exato do user pool Cognito em `sa-east-1`, confirmado pelo dono AMH. |
| `cognito_origin` | Origin HTTPS do managed login/domínio customizado vinculado àquele pool, aprovado e verificado pelo dono IdP. Sem path, porta ou credenciais. |
| `human_client_id`, `human_client_purpose` | Client **público dedicado ao portal e tenant**, sem client secret; `AllowedOAuthFlowsUserPoolClient=true`, flows exatamente `code`, scope `openid`, sem `implicit` ou `client_credentials`; callback exatamente `{public_origin}/api/v1/portal/auth/callback`. Propósito literal `dedicated-human-code-pkce`. O BFF envia S256, state e nonce; Cognito não tem um flag Terraform separado que por si prove PKCE. |
| `machine_client_id` | ID M2M real distinto do humano e igual ao `fhir_cognito_client_id` configurado neste ambiente. Apenas identificador de comparação; segredo M2M nunca vai ao portal. |
| `public_origin` | Origin HTTPS público fixo. Dono DNS liga o hostname ao NLB usando `portal_prerequisites`; não apontar para testchannel ou proxy do engine. |
| `image_digest` | `sha256:` com 64 hex do artefato de aplicação revisado, com módulo portal e dependências travadas. Requer SBOM/assinatura e confiança CA do Aurora verificadas no artefato exato. |
| `database_secret_arn` | Secret externo da mesma conta/região, nome `maezo-operadora/dev/portal/{tenant}/session-dsn`, ARN completo com sufixo real. Seu **SecretString inteiro** é a DSN `postgresql+asyncpg`; não é JSON nem o segredo de `maezo_app`/`cibseven_app`/master. |
| `certificate_arn` | Certificado ACM emitido, válido para o hostname público e cadeia verificada, na conta/região do NLB. Renovação pertence ao dono ACM/DNS. |
| `public_subnet_ids` | Duas ou mais subnets do mesmo VPC em AZs distintas, com rotas públicas/IGW confirmadas pelo dono de rede. Terraform confere VPC e AZ; não inventa rotas do state AMH. |
| `https_egress_ipv4_cidrs` | IPs `/32` aprovados e atuais de Cognito/JWKS/token e endpoints privados AWS necessários a ECR API/DKR, S3 de layers, Secrets Manager e Logs. Lista vazia, CIDR universal ou IPv6 são recusados. Endpoint policies/regras de ingresso correspondentes pertencem ao dono AMH. |

Terraform valida formato, consistência e consumo destas entradas; a string de propósito
não atesta a configuração real do IdP, titularidade do domínio ou revisão de identidade.
O dono AMH deve produzir leitura sanitizada da configuração do client humano, callback,
pool/domínio e client M2M. Não consultar/exportar o client secret de máquina para esta
evidência. Nenhum usuário, grupo ou membership é semeado por este pacote.

## Banco, confiança e sequência de preparação

O dono do banco deve fornecer database/schema exclusivo do tenant do portal, role de
login própria não-owner/não-superuser, administração/migration separada e grants
mínimos: DML em `portal_sessions`, `portal_login_transactions`, `portal_code_claims`;
somente SELECT em `portal_memberships`. Sem grants nos schemas de outros tenants,
engine, FHIR ou auditoria geral; sem criação de objetos. Revogar privilégios PUBLIC
incompatíveis. A conexão do BFF precisa de `search_path` configurado pelo dono da role
para o schema tenant real; o BFF não injeta `MAEZO_TENANT_ID` nem o `search_path` de
outros runtimes. Verificar resolução das quatro tabelas sob a role real. As migrations
usam `MAEZO_TENANT_ID` e não autorizam o BFF como identidade migradora.

1. Donos AMH provisionam client, endpoints/rotas, ACM, database/role/secret e migration
   linear revisada incluindo `0012_portal_identity_session`. O conteúdo de segredo
   não passa pelo Terraform; este lê somente metadados do secret.
2. Revisar e aplicar a preparação com `portal` completo e `portal_enabled=false`, em
   janela autorizada. Neste reparo **nenhum apply foi executado**.
3. Entregar `portal_prerequisites.this.task_security_group_id` ao dono do state
   `amh-data-platform/infrastructure/envs/dev-sa-east-1`. Ele adiciona esse SG a
   `module.aurora_hapi.allowed_security_group_ids`. O SG compartilhado anterior não
   concede acesso ao SG dedicado novo. A regra inline autoritativa do Aurora não deve
   ser criada/importada neste state. Entregar também o alias do NLB ao dono DNS.
4. Verificar DSN/role/schema, grants negativos entre tenants, CA/hostname do Aurora,
   login PKCE e negações M2M, egress permitido/negado, endpoint rotation e logs sem
   query/cookie/token no artefato exato. Só então revisar a ativação.

**Pendência concreta de imagem:** `gateway/portal_identity.py` usa
`ssl.create_default_context()` para PostgreSQL. `deploy/Dockerfile` ainda não instala
explicitamente a cadeia CA do RDS. O autor de imagem deve fornecer o bundle confiável
pinado e sua instalação no trust store, provar TLS/hostname e publicar o digest antes
da ativação. Não desabilitar verificação TLS, usar CA inventada ou tratar digest com
formato válido como prova de imagem apta. Startup falha fechado se persistência/CA
estiver indisponível. Esta pendência não foi executada ou encerrada aqui.

## Rede, identidades e sondas

Browser → NLB usa TLS 1.2/1.3, certificado ACM e somente porta 443. NLB → BFF usa
HTTP/TCP 8080 **sem TLS interno**, permitido somente entre os dois SGs dedicados,
em subnets privadas para tasks, sem public IP ou proxy protocol. Host HTTP permanece
intacto; o BFF recusa Host diferente do origin fixo e não confia em Forwarded headers.
Não se afirma criptografia ponta a ponta desse trecho. ADR-0006/0049 exigem zonas,
TLS e isolamento, mas não especificam mTLS para o ingresso BFF; o mTLS obrigatório
do transporte humano ao engine é outra fronteira, conectada somente pelo perfil staff completo abaixo.
Revisão e prova real da topologia continuam no gate de staging.

BFF → Cognito e BFF → Aurora verificam TLS e hostname. No perfil identity o SG não tem egress para
engine, agentes, HAPI ou internet universal. O perfil staff acrescenta somente os dois destinos nativos explícitos abaixo. Endpoints privados S3/ECR podem ser
necessários para obter layers sem abrir internet; seus IPs/SGs/policies devem ser
fornecidos pela plataforma. `/32` é controle IP, **não enforcement FQDN**: mudança
de IP/DNS requer atualização revisada, e IP compartilhado de CDN não isola hostname.
Testar acesso negativo fora dos destinos aprovados antes de cutover; falha de DNS,
NAT, endpoint ou mudança de IP não autoriza ampliar a regra para `0.0.0.0/0`.

Role de execução só obtém token ECR, puxa do repositório de aplicação, escreve no
log group próprio e lê o único secret de sessão. CMK, quando presente, restringe
Decrypt à chave e ao contexto daquele secret via Secrets Manager. Role de runtime
não recebe políticas AWS, segredo de agente, Bedrock, cofre de engine ou ECS Exec.
Raiz é read-only, usuário 1000:1000, capabilities removidas. NLB não captura payload
HTTP; access logging está desativado no BFF e não é habilitado no NLB.

Não existe `/health`. O NLB usa TCP 8080; a sonda ECS faz GET local à rota real
`/api/v1/portal/session` com Host público explícito e exige **401 + corpo estático
exato + Cache-Control:no-store**. 200, 400, 404, 500 e corpo diferente falham.
O startup executa uma operação real de banco antes de servir; a sonda posterior
sem cookie **não consulta banco nem IdP**. Ela prova liveness e fronteira de sessão,
nunca readiness contínua, membership ou sucesso de login.

## Verificação local e limites de evidência

No diretório deste ambiente, executar `terraform init -backend=false -input=false`
e `terraform validate`. `terraform test -filter=tests/portal.tftest.hcl` testa configuração
com provider AWS mockado, inputs sintéticos e targets do grafo do portal. Casos positivos
usam apply apenas no state mockado em memória (compatível com Terraform 1.10); casos
negativos usam plan. Não há apply AWS ou leitura de credenciais. Isso permite
conferir environment/secrets/roles/rede reais sem credenciais ou cloud apply; não é
integração AWS. Os targets evitam aceitar a ratificação pendente de agentes fora
deste reparo; não são uma receita de deploy nem um full-root cloud plan verde.

No repo, executar `pytest tests/unit/ci/test_ecs_portal_deployment.py`
e `pytest tests/unit/ci/test_check_chart_env_reconciliation.py`, além dos testes de
sessão humana. Testes de infra exigem Terraform/provider já inicializados. Preservar
o lock do provider; não atualizar SDK, runtime, scanner ou allowlists para passar.

Rollback de aplicação usa digest previamente verificado, com migration compatível;
não apaga sessões/auditoria nem reverte dados automaticamente. Rotação do secret
exige nova materialização das tasks ECS e validação de persistência. Deploy real,
recuperação, backup, login Cognito e decisões humanas permanecem gates separados.

Referências primárias: [NLB health checks](https://docs.aws.amazon.com/elasticloadbalancing/latest/network/target-group-health-checks.html),
[NLB listeners/TLS](https://docs.aws.amazon.com/elasticloadbalancing/latest/network/load-balancer-listeners.html),
[Terraform provider mocks](https://developer.hashicorp.com/terraform/language/tests/mocking).


## Bootstrap staff exact-case (SC1; ADR-0049 D4/D8/D9/D11)

`portal.staff = null` injeta `MAEZO_PORTAL_CAPABILITIES=identity`, usa o `portal.image_digest`
original e não cria init, volumes ou egress nativo. Um objeto staff completo injeta
`identity,staff_cases` e usa **o mesmo** `staff.portal_image_digest` no init e no BFF.
O entrypoint continua `python -m maezo.portal.api`, cuja implementação aprovada deve
chamar `portal.api.production:create_production_app`. Este patch exige essa composição
no artefato final; uma imagem antiga sem bootstrap não é aceita apenas pelo digest.
`portal_enabled` continua falso por default, independente de ambos os perfis.

Campos **exatos** de `portal.staff` (nenhum segredo literal):

| Campo | Consumo e qualificação externa |
|---|---|
| `material_secret_arn`, `material_secret_version_id`, `material_kms_key_arn` | Secret externo `maezo-operadora/dev/portal/{tenant}/staff-materials` com sufixo real, na conta/região configuradas; versão imutável explícita, CMK ARN exato. Terraform lê só metadados e confere a chave resolvida. Não usa SecretVersion/SecretString data source. |
| `portal_image_digest` | Imagem portal derivada revisada, publicada no repositório de aplicação existente; fonte concreta, dependências, CA de identidade, SBOM/assinatura e mounts qualificados. |
| `public_manifest_sha256`, `root_key_sha256`, `designation_sha256`, `native_configuration_sha256` | Pins públicos aprovados independentemente pelo dono. Não calculados de um bundle não confiável nem de URL/recibo fictício. |
| `scope` | Objeto fechado `tenant`, `environment`, `engine_name`, `database_incarnation`; strings exatas do domínio instalado. Tenant igual ao portal. Nome do diretório Terraform não escolhe engine/incarnation/environment. |
| `native_origin`, `native_server_spki_sha256` | Origin DNS HTTPS fixo (porta443 somente), pin real do servidor mTLS, vínculo owner-qualified ao destino SG. Formato/SG não prova que DNS aponta ao endpoint correto. |
| `read_key_sha256`, `witness_key_sha256` | Pins distintos entre si e do root; papéis/purposes e separação de todas as outras chaves verificados pelo loader/native. |
| `maximum_seconds` | Inteiro1–10; env decimal; não pode exceder o máximo do native instalado. |
| `native_https_security_group_id` | Destino mTLS porta443, metadados de conta/VPC conferidos; dono instala ingress correspondente e peer mapping. Sem administração REST genérica. |
| `native_database_security_group_id`, `native_database_port` | Destino/porta exatos do banco native para login witness SELECT-only. Dono qualifica igualdade com host/porta da conexão declarada no manifesto e roles/grants/OIDs; SG não confere identidade SQL. |

As entradas públicas viram exatamente `MAEZO_PORTAL_STAFF_MATERIAL_DIRECTORY`
(`/run/maezo-staff-materials/current`), `..._MATERIAL_VERSION_ID`,
`..._PUBLIC_MANIFEST_SHA256`, `..._ROOT_KEY_SHA256`, `..._DESIGNATION_SHA256`,
`..._NATIVE_CONFIGURATION_SHA256`, `..._SCOPE` (JSON com quatro strings),
`..._NATIVE_ORIGIN`, `..._NATIVE_SERVER_SPKI_SHA256`, `..._READ_KEY_SHA256`,
`..._WITNESS_KEY_SHA256`, `..._MAXIMUM_SECONDS`. Ambos os containers recebem os
mesmos pins públicos/tenant/issuer. O init recebe **somente** o segredo novo
`MAEZO_PORTAL_STAFF_SECRET_BUNDLE` por `arn:::version-id`; o BFF recebe somente sua
DSN de identidade já existente. Não há segredo novo no environment principal,
comando, Terraform state/output, imagem ou log. O environment privado do init é
transitório; não se alega zeroização ou ausência de exposição à plataforma ECS.

Bundle: JSON canônico sem números, schema `portal-staff-secret-bundle.v1`, exatamente
`schema`, `material_version_id`, `public_manifest`, `files` (valores base64), limite
65.536 bytes. Manifesto schema `portal-staff-material.v1` com os campos fechados no
bootstrap aprovado. O mapa files tem exatamente12 basenames:

- Públicos, SHA256 obrigatório no manifesto: `designation.json`, `installation-proof.json`,
  `installation-root.der`, `native-ca.pem`, `read-client-certificate.pem`,
  `session-lock-ca.pem`, `native-witness-ca.pem`.
- Privados, hash **null** obrigatório no manifesto: `read-signing-key.pem`,
  `witness-signing-key.pem`, `read-client-key.pem`, `session-lock-dsn.txt`,
  `native-witness-dsn.txt`. Não publicar hashes de DSNs/chaves privadas.

`session_lock_connection` e `native_witness_connection` incluem host, port, database,
login, tls_server_name, ca_file e function_pin. Porta/OID/maximum_seconds no manifesto
são strings decimais. Session lock usa a **mesma DB de identidade**, login separado e
`function_pin={oid,owner,definition_sha256}` para a função instalada
`portal_identity.lock_external_session(text)`. Native witness usa outro login
SELECT-only no banco do engine e `function_pin=null`. Exact relation pins:
`mzo_portal_read_membership` e `mzo_human_principal`, OID/owner reais. Nada cria ou
move banco/schema, instala função, concede grants ou usa a role writer/master.
O manifesto inclui snapshot de revogação qualificado; sua expiração só reduz a
validade local. A autoridade nativa atual continua obrigatória a cada operação.

`deploy/portal.Dockerfile` exige `APP_REPOSITORY` e `APP_DIGEST`, com FROM explícito
`repository@digest`; não baixa CAs/material/dependências. A imagem base revisada deve
incluir o bootstrap e a confiança CA real para a conexão de identidade existente.
Cria só os dois diretórios0700/UID:GID1000 e declara VOLUME com paths iguais aos
mountPoints. O runtime confere ownership/modes; qualificação real deve demonstrar
propagação de ownership no Fargate, nenhum conteúdo privado pré-embutido e a CA/TLS
original. Acrescentar diretórios **não fecha** a pendência de confiança RDS anterior.

Init `python -m maezo.gateway.staff_cases.materialize`: nonessential, UID:GID1000,
root filesystem read-only, capabilities ALL removidas, sem porta/listener, logs,
ECS Exec, healthcheck ou restart automático. Publica uma geração `current` exclusiva
com rename atômico após validação/fsync, diretório0500 e treze arquivos0400 (os doze materiais mais manifesto público `manifest.json`); não sobrescreve
conteúdo existente. BFF depende de init SUCCESS e monta material read-only. Somente
BFF monta scratch read-write, `TMPDIR=/run/maezo-staff-scratch`, diretório0700/1000.
Não há bind de host/EFS ou persistência entre tasks. Fargate1.4 usa orçamento explícito
**21GiB total** de ephemeral storage compartilhado por imagem, material e scratch;
isso não é quota por diretório nem tmpfs. Fonte limita bundle/arquivos e utiliza
scratch para o certificado público retido; sem expansão automática de disco.

Execution role acrescenta GetSecretValue só no ARN staff e Decrypt só na CMK exata,
com ViaService regional e EncryptionContext:SecretARN. A task role permanece sem
políticas AWS; init não chama SDK para obter segredo. Metadados/inputs não qualificam
owner/state/revogações/versão/imagem. Se o SG/porta native DB coincide exatamente com a saída Aurora existente, reutiliza-se
a regra para evitar DuplicatePermission; conta/VPC do destino explícito ainda são
verificadas. Isso não confunde as três conexões, databases ou roles.
Ingress dos dois destinos nativos permanece
exclusivamente no state do dono; aqui só se acrescenta egress do SG portal. Nenhum
CIDR universal novo, grant de engine, propriedade de recurso externo ou conta
escolhida por inferência.

Rotação exige nova versão, manifesto/pins e task/image compatíveis; nunca reload ou
mutação de arquivos na task existente. Revogação emergencial acontece primeiro na
autoridade nativa real, seguida de drain/substituição; rollback de aplicação não
reverte revogações/head/recibos nem renova material expirado. Init falho impede o BFF;
falha de fonte/TLS/material impede startup staff, sem fallback silencioso identity.
A sonda session401 continua liveness. Startup/SELECT1 não prova staff readiness;
aceitação requer resposta real autorizada de caso exato e negações/currentness.

Checks mínimos propostos: `terraform fmt -check` nos arquivos alterados;
`terraform validate` com provider já disponível; `terraform test -filter=tests/portal.tftest.hcl`
(mock AWS) mantém identidade, init/env/versão/CMK/roles/mounts/egress e recusas. Nenhum
provider install, teste, build ou cloud apply faz parte desta construção de patch.
Preparação zero tasks não equivale a autorização de ativação.

Mecânicas primárias: [versão explícita ECS](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/secrets-envvar-secrets-manager.html),
[ownership e armazenamento dos bind mounts](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/bind-mounts.html).
