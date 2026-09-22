# Runbook: provisionar o portal humano em dev (ADR-0049, FRENTE 1)

> Executado em **21/09/2026** na conta **203312548462** / `sa-east-1`, a partir do mandato
> `PORTAL_FULL_E_PAGINA_DE_TESTE.md` (Diretoria de Tecnologia). O portal esta **preparado e
> NAO ativado**: `portal_enabled = false`, serviço em **zero tasks**. Este documento descreve
> o que foi feito, com que comando, e o que exatamente falta para ligar.
>
> Fonte canonica das regras: [`deploy/aws-ecs/envs/dev-sa-east-1/portal.md`](../../deploy/aws-ecs/envs/dev-sa-east-1/portal.md).
> Entradas nao secretas: [`portal.auto.tfvars`](../../deploy/aws-ecs/envs/dev-sa-east-1/portal.auto.tfvars).

---

## 1. Retrato do que existe agora

| | |
|---|---|
| Client humano Cognito | `61gml104sr5nc8jskrptstua0u` (`portal-humano-amh-dev`), pool `sa-east-1_9oKv7gHOJ` |
| Client M2M de comparacao | `3kr6l4lq5mgq84rta88a2ugpd` (`agent-rafael-omni`) — **nao** e client novo |
| Segredo da sessao | `maezo-operadora/dev/portal/amh/session-dsn` (`...-e3YgbH`) — SecretString **inteiro** = DSN |
| Role de banco do BFF | `portal_bff_amh` — nao-owner, nao-superuser, `search_path = amh` |
| Imagem | `8b014a60` = `sha256:685ddb6d80c89f713ac776700bb7b759cb56253d85ab1b273cc7946ecdf12c3c` |
| Servico ECS | `maezo-operadora-dev-portal-76e3a981` — **0/0 tasks** |
| SG das tasks do portal | `sg-0d382c5d005a12995` (dedicado; **nao** e o SG do maezo) |
| SG do ingresso TLS | `sg-0d0...` (`aws_security_group.portal_ingress`), 443 aberto para o NLB |
| NLB (alias para o DNS) | `portal-cceba8c66318e03e-da82ba413a53e0d5.elb.sa-east-1.amazonaws.com` (zona `ZTK26PT1VY4CU`) |
| Hostname publico alvo | `portal-maezo-dev.austa.com.br` |
| Certificado no listener | **bootstrap self-signed** (`.../certificate/46784e0d-...`) — temporario |
| Certificado real | `.../certificate/34be6754-441c-4ab5-add0-67037d628c72` — `PENDING_VALIDATION` |

---

## 2. Entregas para o dono do DNS externo

`austa.com.br` **nao** tem hosted zone publica nesta conta AWS. Os dois registros abaixo
precisam ser criados por quem administra a zona:

**a) Validacao do certificado ACM** (sem isto o certificado real nunca emite):

| Tipo | Nome | Valor |
|---|---|---|
| CNAME | `_a97abaa85b2ea90dec7850f381c79512.portal-maezo-dev.austa.com.br.` | `_1d34fdfc01d78f0ba2abe5e7a19f0039.wzccmgtwzk.acm-validations.aws.` |

**b) O hostname publico apontando para o NLB:**

| Tipo | Nome | Valor |
|---|---|---|
| CNAME (ou ALIAS/ANAME) | `portal-maezo-dev.austa.com.br` | `portal-cceba8c66318e03e-da82ba413a53e0d5.elb.sa-east-1.amazonaws.com` |

Acompanhar a emissao:

```powershell
$env:AWS_PROFILE='adm-dev'
aws acm describe-certificate --region sa-east-1 `
  --certificate-arn arn:aws:acm:sa-east-1:203312548462:certificate/34be6754-441c-4ab5-add0-67037d628c72 `
  --query 'Certificate.Status' --output text   # PENDING_VALIDATION -> ISSUED
```

---

## 3. O que foi feito, item por item

### 3.1 Client humano no Cognito (1.1) — FEITO

```powershell
aws cognito-idp create-user-pool-client --user-pool-id sa-east-1_9oKv7gHOJ `
  --client-name "portal-humano-amh-dev" --no-generate-secret `
  --allowed-o-auth-flows-user-pool-client --allowed-o-auth-flows code `
  --allowed-o-auth-scopes openid `
  --callback-urls "https://portal-maezo-dev.austa.com.br/api/v1/portal/auth/callback" `
  --supported-identity-providers COGNITO --explicit-auth-flows ALLOW_REFRESH_TOKEN_AUTH `
  --prevent-user-existence-errors ENABLED --enable-token-revocation `
  --token-validity-units "AccessToken=minutes,IdToken=minutes,RefreshToken=hours" `
  --access-token-validity 60 --id-token-validity 60 --refresh-token-validity 2 `
  --region sa-east-1
```

Leitura sanitizada do resultado (o segredo M2M **nao** foi consultado em nenhum momento;
este client **nao tem** secret):

```json
{
  "ClientId": "61gml104sr5nc8jskrptstua0u",
  "ClientName": "portal-humano-amh-dev",
  "SecretPresente": null,
  "AllowedOAuthFlowsUserPoolClient": true,
  "AllowedOAuthFlows": ["code"],
  "AllowedOAuthScopes": ["openid"],
  "CallbackURLs": ["https://portal-maezo-dev.austa.com.br/api/v1/portal/auth/callback"],
  "LogoutURLs": null,
  "ExplicitAuthFlows": ["ALLOW_REFRESH_TOKEN_AUTH"],
  "SupportedIdentityProviders": ["COGNITO"],
  "PreventUserExistenceErrors": "ENABLED",
  "EnableTokenRevocation": true,
  "IdTokenValidity": 60, "AccessTokenValidity": 60, "RefreshTokenValidity": 2
}
```

`cognito_origin` confirmado por `describe-user-pool`: `Domain = amh-maezo-bpm-dev`,
`CustomDomain = null` -> `https://amh-maezo-bpm-dev.auth.sa-east-1.amazoncognito.com`.

`machine_client_id` foi lido do **ambiente vivo**, nao do default do repo:
`aws ecs describe-task-definition --task-definition maezo-operadora-dev-agent-rafael` ->
`FHIR_CLIENT_ID = 3kr6l4lq5mgq84rta88a2ugpd`. Coincide com o default de
`fhir_cognito_client_id`, o que satisfaz a validacao que trava o `apply`.

### 3.2 Banco, role e segredo (1.2) — FEITO

**Achado que muda o roteiro:** as 4 tabelas do portal **ja existiam** no schema `amh`
(`amh_alembic_version = 0015`, medido). A migration `0012_portal_identity_session` nao
precisou ser aplicada; nao ha migration nova neste item.

**Interpretacao de "database/schema exclusivo do tenant do portal":** o schema exclusivo do
tenant e o `amh` — o mesmo que `env.py` usa como `search_path` (DL-0017) e onde a `0012`
cria as tabelas. Um DATABASE separado colocaria as tabelas onde a cadeia de migrations nao
as cria. O isolamento e' de **grants por tabela**, e foi provado negativamente (abaixo).

O que foi criado, por task avulsa dentro da VPC (nao ha rota da estacao ate o Aurora):

```sql
CREATE ROLE portal_bff_amh WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
  NOINHERIT NOREPLICATION NOBYPASSRLS PASSWORD '<verificador SCRAM>';
ALTER ROLE portal_bff_amh SET search_path = "amh";          -- fixado pelo DONO da role
SET ROLE maezo_app;                                          -- dono do database e das tabelas
GRANT CONNECT ON DATABASE maezo TO portal_bff_amh;
GRANT USAGE ON SCHEMA amh TO portal_bff_amh;
GRANT SELECT, INSERT, UPDATE, DELETE ON amh.portal_sessions            TO portal_bff_amh;
GRANT SELECT, INSERT, UPDATE, DELETE ON amh.portal_login_transactions  TO portal_bff_amh;
GRANT SELECT, INSERT, UPDATE, DELETE ON amh.portal_code_claims         TO portal_bff_amh;
GRANT SELECT                          ON amh.portal_memberships        TO portal_bff_amh;
REVOKE USAGE ON SCHEMA public FROM PUBLIC;                   -- ver nota abaixo
```

**A senha nunca entrou num comando SQL.** O verificador `SCRAM-SHA-256` foi calculado no
cliente (PBKDF2-HMAC-SHA256, salt de 16 bytes, 4096 iteracoes, RFC 5802) e so ele viajou no
`CREATE ROLE`. Motivo: `log_statement` do servidor e' `none` hoje, mas `ddl` registraria o
`CREATE ROLE ... PASSWORD` inteiro, sem redigir nada — e o log do CloudWatch/Postgres e'
lido por outras pessoas. A prova de que o verificador presta e' o login real da fase 2.

**`REVOKE USAGE ON SCHEMA public FROM PUBLIC`** — o schema `public` do database `maezo`
hospeda `checkpoints`, `checkpoint_blobs`, `checkpoint_writes`, `checkpoint_migrations`
(estado dos agentes). Medido **antes** do revoke: `maezo_app` e `cibseven_app` tem `USAGE`
**explicito** (`nspacl = {pg_database_owner=UC/...,=U/...,maezo_app=U/...,cibseven_app=U/...}`),
portanto o revoke nao alcanca nenhum consumidor real. Depois: `PUBLIC=False`,
`maezo_app=True`, `cibseven_app=True`, `portal_bff_amh=False`.
**O que NAO foi revogado, de proposito:** `CONNECT`/`TEMP` de `PUBLIC` no database `maezo`.
`datacl` era `None` — ou seja, `cibseven_app` e `maezo_app` chegam ao banco **por PUBLIC**, e
revogar derrubaria o engine. O portal recebeu `CONNECT` explicito em vez disso.
Rollback do revoke, se necessario: `GRANT USAGE ON SCHEMA public TO PUBLIC;` como `maezo_app`.

**Segredo** — criado **fora** do Terraform. A SCP `deny-secrets-without-rotation`
(`p-9m8f47yd`) nega `CreateSecret` ao perfil SSO administrativo; medido, nao presumido:

```
AccessDeniedException ... not authorized to perform: secretsmanager:CreateSecret
... with an explicit deny in a service control policy: .../p-9m8f47yd
```

O caminho sancionado e' o `OrganizationAccountAccessRole`. **Sem prompt de MFA**: assume-se
o papel a partir do perfil SSO da conta de GESTAO (`adm-mgmt`), que a trust policy aceita
(`Principal = 169446931765:root`, sem `Condition`). Isso evita tanto a chave estatica do
disco quanto o codigo interativo:

```powershell
$env:AWS_PROFILE='adm-mgmt'
$c = (aws sts assume-role --role-arn arn:aws:iam::203312548462:role/OrganizationAccountAccessRole `
       --role-session-name portal-dev-secret-bootstrap --output json | ConvertFrom-Json).Credentials
Remove-Item Env:\AWS_PROFILE
$env:AWS_ACCESS_KEY_ID=$c.AccessKeyId; $env:AWS_SECRET_ACCESS_KEY=$c.SecretAccessKey
$env:AWS_SESSION_TOKEN=$c.SessionToken; $env:AWS_REGION='sa-east-1'

aws secretsmanager create-secret --name "maezo-operadora/dev/portal/amh/session-dsn" `
  --secret-string "file://<arquivo com a DSN>" --tags "file://<tags.json>" --region sa-east-1
```

`--secret-string file://` e obrigatorio: JSON/valor inline pelo PowerShell perde aspas e o
ECS rejeita. O arquivo local foi apagado depois. A DSN e' o SecretString **inteiro**:
`postgresql+asyncpg://portal_bff_amh:<senha>@amh-aurora-hapi-dev.cluster-c3iw2s2gk0bw.sa-east-1.rds.amazonaws.com:5432/maezo`
— sem parametros de SSL na URL, porque `gateway/portal_identity.py` passa
`connect_args={"ssl": ssl.create_default_context()}` no proprio codigo.

### 3.3 Rede, DNS, certificado (1.3) — FEITO, com uma correcao de TF

Subnets do NLB: `subnet-0f6fd7c008207fe5b` (1a) e `subnet-0b9f91b867920aa44` (1b),
`Tier=public`, mesmo VPC `vpc-0a850a9d40b36ac5b`. As **tasks** ficam em `Tier=private-app`
sem IP publico.

Os `/32` de `https_egress_ipv4_cidrs` foram resolvidos **de dentro da VPC** (2026-09-21
17:44Z), com handshake TLS confirmado em cada destino — nao pela resolucao da estacao:

| Destino | `/32` |
|---|---|
| `cognito-idp.sa-east-1.amazonaws.com` (JWKS do issuer) | 52.67.144.206, 54.20.171.151, 54.94.110.228 |
| `amh-maezo-bpm-dev.auth...amazoncognito.com` (`/oauth2/token`) | 52.67.250.153, 52.67.98.193, 54.20.130.20 |
| VPCE interface `ecr.api` | 10.40.40.27, 10.40.41.104 |
| VPCE interface `ecr.dkr` | 10.40.40.123, 10.40.41.249 |
| VPCE interface `secretsmanager` | 10.40.40.139, 10.40.41.125 |
| VPCE interface `logs` | 10.40.40.233, 10.40.41.76 |

O SG dos endpoints de interface (`sg-0f014b46a81c5cd29`) admite 443 de `10.40.0.0/16`
inteiro — **nao** ha dependencia cross-repo de ingresso para eles.

**Correcao de Terraform necessaria — o S3 dos layers nao cabe em `/32`.** Neste VPC o S3 e'
**gateway endpoint** (`vpce-09e34704570f7f756`, prefix list `pl-6aa54003`, rota presente na
route table `rtb-0c230a53171b2b8d5` de `private-app`): **nao tem ENI**, e o bucket
`prod-sa-east-1-starport-layer-bucket` responde em enderecos publicos que rotacionam (8
medidos as 17:44Z; as 18:17Z a conexao saiu por `3.5.234.5`, que nao estava entre eles).
`portal.md` pede egresso para "S3 de layers" assumindo endpoint de interface; a realidade
divergiu. Sem essa saida a task Fargate **nao materializa** — falha ao puxar a imagem, e o
sintoma chega como "o servico nunca fica 1/1".

Foi adicionada, em `portal-network.tf`, **uma** regra de egresso 443 para a prefix list
gerenciada do S3 regional (`data.aws_ec2_managed_prefix_list` + `portal_s3_layers`). Isso
**nao** afrouxa o modelo: `https_egress_ipv4_cidrs` continua validado fail-closed (`/32`,
nao-vazio, sem `0.0.0.0/0`), e o novo destino e' uma prefix list da AWS, porta 443.
`terraform test` do pacote do portal continua **32 passed, 0 failed**.

**O listener TLS recusa certificado pendente** (medido, nao presumido):

```
CreateListener 400 UnsupportedCertificate: The certificate '...34be6754...' must have a
fully-qualified domain name, a supported signature, and a supported key size.
```

Como o certificado real depende de DNS externo, o listener foi materializado com um
**self-signed importado no ACM**, com `notBefore` um dia no passado (clock skew no import ja
custou uma volta em outro projeto):

```powershell
& "C:\Program Files\Git\usr\bin\openssl.exe" req -x509 -newkey rsa:2048 -nodes `
  -keyout bootstrap.key -out bootstrap.crt -config bootstrap-cert.cnf `
  -not_before <ontem>Z -not_after <ontem+365d>Z
aws acm import-certificate --certificate fileb://bootstrap.crt --private-key fileb://bootstrap.key --region sa-east-1
```

A chave privada local foi apagada. **Nao ative o portal com este certificado:** navegador
nenhum confia nele.

### 3.4 Imagem (1.4) — FECHADO em 21/09/2026 (ver `supply-chain-imagem.md`)

> **ATUALIZACAO 21/09/2026.** O portal foi **repinado** de `685ddb6d` (tag `8b014a60`, 20/09)
> para **`sha256:d29ce828...`** (tag `e857e284`) — a mesma imagem que o resto da frota ja roda
> em dev, com as correcoes da Helena do PR #455. Religar no digest antigo seria testar o
> portal contra um build atrasado.
>
> O digest novo tem **SBOM (SPDX/syft) e assinatura (chave KMS
> `alias/maezo-operadora-dev-image-signing`) publicados no ECR** como
> `sha256-d29ce828....sig` / `.att`, e **verificados** por `cosign verify` e
> `cosign verify-attestation` (run 35665780360, inclusive pelo job que usa a role
> verify-only). A pendencia abaixo, escrita quando o portao estava aberto, fica como
> historico. As verificacoes DENTRO da imagem (modulo, `create_production_app`, CAs do RDS)
> foram feitas em `685ddb6d` e **nao** foram refeitas no digest novo — o que se provou no
> novo foi boot real, `/session` 401 e o redirect de login com S256.

### 3.4 (historico) Imagem — FEITO em parte (SBOM/assinatura AUSENTES)

Verificado **dentro do artefato exato**, como **uid 1000**
(`docker run --user 1000:1000 --entrypoint /app/.venv/bin/python <repo>@sha256:685ddb...`):

```
uid: 1000 gid: 1000
import ok: maezo.portal.api / maezo.portal.api.production / maezo.portal.admin.assignments
           maezo.gateway.portal_identity / maezo.gateway.human.engine_reads
create_production_app presente: True
migrations no artefato: [... 0012_portal_identity_session.py, 0013, 0014, 0015]
verify_mode: 2 check_hostname: True
CAs Amazon RDS no contexto padrao do Python: 3
  - ['Amazon RDS sa-east-1 Root CA ECC384 G1']   notAfter: May 19 19:16:01 2121 GMT
  - ['Amazon RDS sa-east-1 Root CA RSA2048 G1']  notAfter: May 19 19:06:26 2061 GMT
  - ['Amazon RDS sa-east-1 Root CA RSA4096 G1']  notAfter: May 19 19:11:20 2121 GMT
```

TLS/hostname contra o Aurora **real** foi provado no mesmo artefato rodando dentro da VPC,
com `ssl.create_default_context()` — o mesmo objeto que o codigo usa:
`TLS = {'ssl': True, 'version': 'TLSv1.3', 'cipher': 'TLS_AES_256_GCM_SHA384'}`.

**PENDENCIA REAL: nao existem SBOM nem assinatura desta imagem.** Os passos de `syft` e
`cosign` do `cd.yml` estao atras do gate `AWS_ENABLED`, que esta ausente por desenho; o ECR
nao tem nenhum artefato `sha256-685ddb....sig`/`.att` (medido com `list-images`). O item
1.4 de `portal.md` pede SBOM e assinatura antes de aceitar o digest — isso continua **nao
satisfeito**, e e' decisao do dono aceitar o digest sem eles ou republicar a imagem por um
pipeline com o gate ligado.

### 3.5 Preparar sem ativar (1.5) — FEITO

```powershell
cd deploy/aws-ecs/envs/dev-sa-east-1
terraform init -backend-config="bucket=amh-tfstate-dev" `
  -backend-config="key=envs/dev-sa-east-1/maezo-operadora/terraform.tfstate" `
  -backend-config="region=sa-east-1"

terraform plan -input=false -out=portal-frente1.tfplan `
  -var-file=<atestacao PHI, FORA do repo> -var helena_zona_phi=true `
  -var agents_image_digest=sha256:685ddb... -var worker_image_digest=sha256:685ddb... `
  -var diagnostics_image_digest=sha256:685ddb... -var engine_image_digest=sha256:6f478e...

# CONFERIR O PLANO ANTES DE APLICAR. Esta linha nao e' zelo: um apply de um checkout
# sem `portal.auto.tfvars` planeja 43 destroys, porque `var.portal` fica null. O
# `aws_security_group.portal` e' o pior deles — o id dele esta na regra de ingress do
# Aurora, em outro repositorio e outro state (amh-data-platform#175).
terraform show -json portal-frente1.tfplan > plano.json
python ../../../../scripts/ci/checar_plano_sem_destroy_do_portal.py plano.json

terraform apply -input=false portal-frente1.tfplan
```

`portal.auto.tfvars` e' **versionado e auto-carregado** de proposito: se `portal` voltar a
ser `null` num apply futuro, o Terraform **destroi** task definition, roles, SGs, NLB e
listener do portal. `.gitignore` ganhou a negacao correspondente (`*.tfvars` ficava no
caminho). Apagar o `.tfplan` depois do apply: ele carrega os `-var` da atestacao PHI.

**O plan encontrou um drift REAL que o mandato descrevia como cosmetico.** O diff dos 3
agentes nao era normalizacao do provider: `service-agents.tf` declarava `FHIR_BASE_URL`
**duas vezes** na mesma lista `environment` (uma no bloco base, outra no bloco da particao
adicionado depois). `terraform show -json` do plano mostrou, no `container_definitions`
planejado do `agent-rafael`:

```
FHIR_BASE_URL indices/valores = [(8, '.../fhir/omni'), (9, '.../fhir')]
nomes duplicados: ['FHIR_BASE_URL']
```

Nome repetido em `environment` nao e' erro para o ECS: as duas entradas chegam ao runtime e
a **ultima vence**. O rafael passaria a subir **sem particao**, e o comentario do proprio
arquivo diz o custo: `/fhir/Patient` devolve 400, `/fhir/omni/Patient` devolve 200. Como o
rafael esta em `desired_count = 0`, a quebra nao apareceria no dia do apply — apareceria no
dia em que alguem o escalasse.

A entrada duplicada do bloco base foi removida (o bloco da particao ja cobre os tres
agentes). Efeito no plan: as 3 task definitions de agente **sairam do diff**, e a Helena,
que estava rodando, **nao foi reiniciada** (continua na revisao 28).

Plan final aplicado: **31 to add, 1 to change, 1 to destroy**.

- 31 to add: tudo do portal.
- 1 to destroy / recriar: `aws_ecs_task_definition.diagnostics` — normalizacao pura do
  provider (`mountPoints=[]`, `portMappings=[]`, `systemControls=[]`, `volumesFrom=[]`,
  `capabilities.add=[]` sumindo). `diagnostics` e' task avulsa, sem service: nada reinicia.
- 1 to change: `aws_prometheus_rule_group_namespace.maezo[0]` — deriva **pre-existente** e
  **somente de comentario** no YAML de alertas (`expectedFailUntil` renovado pelo dono em
  20/09 de `2026-11-11` para `2027-02-09`). Nenhuma regra, `expr` ou limiar mudou; o repo
  estava a frente da nuvem e o apply so' reconciliou nessa direcao.

> **Diff perpetuo conhecido:** com o provider AWS 6.59, `aws_ecs_task_definition.portal`
> reaparece como "must be replaced" no plan seguinte, pela mesma normalizacao de listas
> vazias. Nao e' drift de configuracao. Nao ha mitigacao aqui alem de saber disso.

Saida relevante:

```
portal_prerequisites = { "this" = {
  "activated" = false
  "callback_url" = "https://portal-maezo-dev.austa.com.br/api/v1/portal/auth/callback"
  "dns_name" = "portal-cceba8c66318e03e-da82ba413a53e0d5.elb.sa-east-1.amazonaws.com"
  "dns_zone_id" = "ZTK26PT1VY4CU"
  "public_origin" = "https://portal-maezo-dev.austa.com.br"
  "task_security_group_id" = "sg-0d382c5d005a12995"
} }
```

Conferido depois do apply: portal `0/0`; `cibseven` `1/1`; `worker-runtime` `1/1`;
`agent-helena` `1/1` na **mesma** revisao 28.

### 3.6 Security group no Aurora (1.6) — **BLOQUEADO** (PR aberto, apply e' do dono)

`sg-0d382c5d005a12995` precisa entrar em `module.aurora_hapi.allowed_security_group_ids` em
`amh-data-platform/infrastructure/envs/dev-sa-east-1/databases.tf`. O modulo
`aurora-cluster` usa `ingress` *dynamic inline*, que e' autoritativo: regra criada do lado
do maezo seria apagada no proximo apply de la.

PR aberto: **`Omni-Saude/amh-data-platform#175`**. `terraform plan` rodado la (sem apply):
1 regra aditiva, as 2 existentes preservadas, nada destruido. **Atencao:** o mesmo plan traz
3 recursos a criar que sao deriva pre-existente daquele state
(`aws_ssm_parameter.hapi_client_id_map["agent-rafael"]` e 2 assinaturas de e-mail do
`module.observability`) — decidir antes do apply.

Enquanto o PR nao for aplicado, o portal **nao conecta no banco**. Isso foi medido, nao
presumido (ver 3.7 #6).

### 3.7 As 7 verificacoes (1.7) — 5 de 7 completas

Todas as sondas rodaram **dentro da VPC**, no artefato exato, via task avulsa
(`run-task` sobre `maezo-operadora-dev-bootstrap-db` com `--overrides`; o script viaja
gzip+base64 no `command` porque `containerOverrides` tem teto de 8192 bytes, e a raiz do
container e' somente-leitura).

**1. DSN, role e schema resolvem sob a role real — OK**

```
session_user: portal_bff_amh    search_path efetivo: amh    is_superuser: False
TLS: {'ssl': True, 'version': 'TLSv1.3', 'cipher': 'TLS_AES_256_GCM_SHA384'}
to_regclass(portal_sessions|portal_login_transactions|portal_code_claims|portal_memberships)
  -> as 4 resolvem SEM qualificar schema
insert/update/delete em portal_code_claims: ok (em transacao, com ROLLBACK)
select em portal_sessions e portal_login_transactions: ok
select em portal_memberships: ok
```

**2. Grants negativos — OK (9 negativos provados)**

```
insert em portal_memberships            -> InsufficientPrivilegeError
select em amh.audit_chain               -> InsufficientPrivilegeError
select em amh.human_command_outbox      -> InsufficientPrivilegeError
select em amh.portal_assignment_source  -> InsufficientPrivilegeError
select em amh.agent_memory              -> InsufficientPrivilegeError
select em public.checkpoints            -> InsufficientPrivilegeError
select em cibseven.act_ru_task          -> InsufficientPrivilegeError
create table em amh                     -> InsufficientPrivilegeError
create table em public                  -> InsufficientPrivilegeError
USAGE em cibseven: False | USAGE em public: False | CREATE em amh: False
```

**3. CA e hostname do Aurora no artefato exato — OK** (ver 3.4)

**4. Login PKCE — provado **ate o IdP**; o trecho do BFF esta bloqueado no DNS**

O `authorize` foi montado exatamente como `gateway/oidc.py:authorization_url` monta
(`response_type=code`, `scope=openid`, `state`, `nonce`, `code_challenge_method=S256`) e
dirigido num navegador real:

1. o hosted UI aceitou o client e os parametros PKCE;
2. `plantao.teste` entrou com a senha temporaria e o Cognito **exigiu a troca** (pagina
   `/changePassword`);
3. depois da troca, o Cognito emitiu o codigo e redirecionou para
   `https://portal-maezo-dev.austa.com.br/api/v1/portal/auth/callback?code=...&state=...`
   com o `state` **identico** ao enviado. O navegador falhou com `ERR_NAME_NOT_RESOLVED`:
   **o unico trecho que falta e' o DNS**, nao a configuracao;
4. a troca do codigo pelo token, com o `code_verifier`, devolveu:

```
POST /oauth2/token -> HTTP 200
ID token: alg = RS256, kid presente
iss confere com o issuer configurado: True
aud confere com o client humano:      True
token_use: id
nonce confere com o enviado:          True
kid do token presente no JWKS do issuer: True   (RSA 2048 — o BFF exige >= 2048)
sub bate com o subject da membership semeada: True
```

A sessao criada por esse teste foi invalidada (`admin-user-global-sign-out`) e o usuario
voltou ao estado `FORCE_CHANGE_PASSWORD` com senha temporaria nova.
**Falta:** exercitar o `/api/v1/portal/auth/callback` do BFF de verdade — depende do DNS, do
certificado real e do servico ativo.

**5. Client M2M nao entra pelo portal — OK no IdP**

```
client HUMANO com grant_type=client_credentials  -> HTTP 400 {"error":"invalid_grant"}
client M2M no /oauth2/authorize?response_type=code -> 302 /error?error=redirect_mismatch
troca de code inexistente pelo client humano     -> HTTP 400 {"error":"invalid_grant"}
```

A recusa de um token M2M **pelo BFF** e' coberta por `tests/unit/portal/test_human_session.py`
(106 passed), nao por teste ao vivo: exige o servico de pe.

**6. Egresso permitido e negado — OK, e revelou o bloqueio do 1.6**

Task avulsa nas subnets `private-app` com `securityGroups=[sg-0d382c5d005a12995]`
(o SG **do portal**, nao o do maezo):

```
== DEVEM SER PERMITIDOS ==
  [OK] JWKS do issuer ...................... TLSv1.3 ip=52.67.144.206
  [OK] token endpoint do pool .............. TLSv1.3 ip=52.67.250.153
  [OK] Secrets Manager (interface) ......... TLSv1.3 ip=10.40.40.139
  [OK] CloudWatch Logs (interface) ......... TLSv1.3 ip=10.40.41.76
  [OK] ECR API (interface) ................. TLSv1.3 ip=10.40.41.104
  [OK] ECR DKR (interface) ................. TLSv1.3 ip=10.40.40.123
  [OK] S3 de layers (gateway/prefix list) .. TLSv1.3 ip=3.5.234.5
== DEVEM SER NEGADOS ==
  [OK] internet generica (example.com) ..... Network is unreachable
  [OK] Bedrock ............................. timeout (SG barrou)
  [OK] STS ................................. timeout (SG barrou)
  [OK] KMS ................................. timeout (SG barrou)
  [OK] HAPI FHIR interno (80) .............. timeout em 10.40.11.222:80
  [OK] engine BPMN (8080) .................. timeout em 10.40.10.45:8080
  [OK] ingresso do agente PHI (8000) ....... timeout em 10.40.10.216:8000
== AURORA 5432 ==
  BARRADO (timeout em 10.40.20.70:5432)
```

O Aurora barrado e' o **esperado hoje** e e' a prova de que o item 1.6 e' bloqueio real: o
egresso existe no SG do portal, a contrapartida de ingress esta no PR #175, nao aplicado.

**7. Logs sem query, cookie ou token — NAO verificavel ao vivo ainda**

Nao existe log do portal porque o servico esta em zero tasks. O que foi verificado e' a
postura no artefato e nos testes: `entrypoint` desabilita access logs, o middleware remove
a query do escopo do logger, `Cache-Control: no-store` em resposta e falha, sem `exc_info`;
o NLB nao habilita access logging. `tests/unit/portal/test_human_session.py` (106 passed) e
`tests/unit/ci/test_ecs_portal_deployment.py` (17 passed) cobrem essa postura.
**Conferir no CloudWatch depois da ativacao** — declaracao no artefato nao e' medicao no log.

### 3.8 Semear pessoas (1.8) — FEITO

Dois usuarios ficticios no pool (`--message-action SUPPRESS`: nenhum e-mail foi enviado para
`austa.com.br`), ambos em `FORCE_CHANGE_PASSWORD`, `email_verified=true`:

| Username | E-mail (alias) | `sub` | Grupo na membership |
|---|---|---|---|
| `plantao.teste` | `plantao.teste@austa.com.br` | `13ac3a7a-30c1-70b4-7f0c-73e26de7c128` | `plantao-clinico` |
| `atendimento.teste` | `atendimento.teste@austa.com.br` | `f33c1ada-40e1-70cd-5b78-128958f04003` | `atendimento-humano` |

> O username **nao** pode ser o e-mail: o pool tem `AliasAttributes=["email"]`, e o Cognito
> recusa username em formato de e-mail nessa configuracao. O login funciona pelos dois
> (username ou e-mail), porque o e-mail esta verificado.
>
> As senhas temporarias valem **7 dias** (`TemporaryPasswordValidityDays`). Politica do pool:
> minimo 14, maiuscula, minuscula, numero e simbolo. Renovar com
> `admin-set-user-password --no-permanent`.

Duas linhas em `amh.portal_memberships`, com o payload validado pelo **proprio
`MembershipRecord` do artefato** antes de gravar e relido sob a role real depois — o mesmo
caminho de `PostgresIdentityStore.get_membership` (`model_validate_json`):

```
membership plantao.teste:     grupo=plantao-clinico    revision=1 audience=staff
membership atendimento.teste: grupo=atendimento-humano revision=1 audience=staff
releitura sob portal_bff_amh: parse ok, grupo_casa=True, papel_casa=True,
                              reviewed_until=2026-12-31T23:59:59+00:00, revoked=False
```

> **`MembershipRecord` e' `strict=True`**: em modo Python ele recusa lista onde o campo e
> `tuple` e string onde e' `datetime`. Semear **tem** de passar por `model_validate_json`
> (modo JSON), que e' o caminho do store.
>
> `principal_ref` e' opaco (`prn_<32 hex>`) de proposito: `SessionDTO` devolve
> `principal_ref` ao **browser**; um e-mail ali viraria PII no corpo de
> `/api/v1/portal/session`.
>
> **Papel PROVISORIO.** Nao existe vocabulario de papel ratificado: R-034 / gap
> `PERSP-ESCALATION-VOCAB` esta aberto e `docs/sme-dispatch/po/org-taxonomy-table.yaml` tem
> `nome_real: ''` em todas as linhas. Os GRUPOS sao os canonicos
> (`plantao-clinico`/`atendimento-humano`, conferidos em `engine_bootstrap/bootstrap.py`,
> `tools/workers/escalation.py` e no contrato SP-OP-ESCALATION-001). O papel gravado e
> `escalonamento-tratar`, escolhido porque o gateway exige
> `required_roles subset de roles` na **mesma** membership. Ao ratificar R-034: trocar o nome
> **e incrementar `revision`** (mudar payload sem bump nao invalida a sessao antiga).

---

## 4. Por que o portal **nao** foi ativado (1.7 final)

`portal_enabled = true` exigiria as 7 verificacoes fechadas. Faltam tres coisas, e nenhuma
delas esta nesta conta:

1. **DNS externo** — sem `portal-maezo-dev.austa.com.br` apontando para o NLB, o critério
   `GET {public_origin}/api/v1/portal/session` nao tem como responder, e o BFF **recusa Host
   diferente do origin fixo** por desenho.
2. **Certificado real** — o listener esta com self-signed de bootstrap. Login PKCE por
   navegador nao fecha com certificado nao confiavel.
3. **Ingress do Aurora (PR #175)** — medido: o SG do portal **nao alcanca** o Aurora hoje. O
   startup do BFF executa uma operacao real de banco antes de servir e **falha fechado**;
   ativar agora daria task morrendo, circuit breaker e rollback — um incidente encomendado.

Ativar tambem com zero tasks e sem essas tres pecas seria trocar "preparado" por "quebrado".

### Como ligar, quando as tres pecas existirem

```powershell
# 1) certificado real ISSUED?
aws acm describe-certificate --region sa-east-1 `
  --certificate-arn arn:aws:acm:sa-east-1:203312548462:certificate/34be6754-441c-4ab5-add0-67037d628c72 `
  --query 'Certificate.Status' --output text

# 2) em portal.auto.tfvars: certificate_arn -> o ARN do certificado REAL (34be6754-...)
#    e portal_enabled -> true

# 3) plan, conferir que so o portal muda, aplicar
cd deploy/aws-ecs/envs/dev-sa-east-1
terraform plan -input=false -out=portal-ativar.tfplan `
  -var-file=<atestacao PHI> -var helena_zona_phi=true `
  -var agents_image_digest=sha256:685ddb... -var worker_image_digest=sha256:685ddb... `
  -var diagnostics_image_digest=sha256:685ddb... -var engine_image_digest=sha256:6f478e...
# CONFERIR O PLANO ANTES DE APLICAR. Esta linha nao e' zelo: um apply de um checkout
# sem `portal.auto.tfvars` planeja 43 destroys, porque `var.portal` fica null. O
# `aws_security_group.portal` e' o pior deles — o id dele esta na regra de ingress do
# Aurora, em outro repositorio e outro state (amh-data-platform#175).
terraform show -json portal-ativar.tfplan > plano.json
python ../../../../scripts/ci/checar_plano_sem_destroy_do_portal.py plano.json

terraform apply -input=false portal-ativar.tfplan
Remove-Item portal-ativar.tfplan

# 4) servico 1/1 e a rota real respondendo 401 com corpo estatico
aws ecs describe-services --cluster maezo-operadora-dev `
  --services maezo-operadora-dev-portal-76e3a981 --region sa-east-1 `
  --query 'services[].{run:runningCount,des:desiredCount,ev:events[0].message}' --output json
curl -i https://portal-maezo-dev.austa.com.br/api/v1/portal/session
#   esperado: 401 + {"erro":"Nao foi possivel validar a sessao."} + Cache-Control: no-store
#   (a sonda do ECS exige exatamente isso; 200/400/404/500 ou corpo diferente = falha)

# 5) log SEM query/cookie/token (verificacao 1.7 #7, que so fecha aqui)
aws logs tail /ecs/maezo-operadora-dev-portal-76e3a981 --since 15m --region sa-east-1
```

**Rollback da ativacao** (testado como caminho, nao como teoria — e' o mesmo mecanismo que
criou o serviço em zero tasks): `portal_enabled = false` + `plan`/`apply` devolve o serviço
a `desired_count = 0` sem apagar task definition, roles, SG, NLB, listener, segredo, role de
banco ou memberships. Nada de dado de sessao e' destruido; sessoes vivas simplesmente param
de ser atendidas. Se a task nao estabilizar, o `deployment_circuit_breaker` com
`rollback = true` ja reverte sozinho para a revisao anterior.

**Rollback do provisionamento inteiro**, se a decisao for desfazer: `portal = null` no
tfvars derruba **todos** os recursos do portal (e por isso o tfvars e' versionado — ninguem
faz isso por acidente). O segredo, a role `portal_bff_amh`, as memberships e os usuarios do
Cognito **nao** sao do Terraform e precisam ser removidos a mao, nesta ordem: usuarios
Cognito -> client humano -> linhas de `portal_memberships` -> `DROP ROLE portal_bff_amh`
(depois de `REVOKE` dos grants) -> `delete-secret`. O `GRANT USAGE ON SCHEMA public TO PUBLIC`
tambem precisa voltar se a postura anterior for desejada.

---

## 5. Como rodar SQL/rede dentro da VPC (receita reutilizavel)

> **Versionado desde 22/09/2026:** `scripts/ops/run-db-task.ps1` faz exatamente o que esta
> abaixo (gzip+base64 do script, overrides UTF-8 sem BOM, espera a task parar e despeja o log
> do stream certo). Um comando que vive no historico do terminal de um dev nao existe para o
> time — o mesmo argumento que criou a task `bootstrap-db`.

Nao ha rota da estacao ate as subnets privadas. O caminho usado aqui:

```powershell
# o script Python viaja gzip+base64 no command (teto de 8192 bytes no containerOverrides);
# a raiz do container e somente-leitura, entao nao ha arquivo intermediario.
$b64 = <gzip+base64 do arquivo .py>
$ov = @{ containerOverrides = @(@{
    name = 'bootstrap-db'
    command = @('python','-c',"import base64,gzip;exec(compile(gzip.decompress(base64.b64decode('$b64')),'<vpc>','exec'))")
    environment = @(@{ name='<CHAVE>'; value='<valor>' })
  }) }
aws ecs run-task --cluster maezo-operadora-dev --task-definition maezo-operadora-dev-bootstrap-db `
  --launch-type FARGATE --region sa-east-1 `
  --network-configuration 'file://net.json' --overrides 'file://ov.json'
```

- A task definition `bootstrap-db` carrega a credencial **mestre** (`amh_admin`) por
  `secrets`; e' o unico ponto do stack que a toca, e roda e sai.
- Para testar o SG **do portal**, troque `securityGroups` para `sg-0d382c5d005a12995` nas
  mesmas subnets `subnet-06647b0c664d0d22a,subnet-0e1f840dbec44e66a`.
- Stream do log: `bootstrap/bootstrap-db/<taskId>` em `/ecs/maezo-operadora-dev/bootstrap-db`
  — **nao** pegue o "mais recente" do grupo, tasks avulsas compartilham o grupo.
- Arquivos JSON de `--overrides`/`--network-configuration` precisam de UTF-8 **sem BOM**:
  `Out-File -Encoding utf8` no PowerShell 5.1 escreve BOM e a CLI recusa com
  `Expected: '=', received: '\ufeff'`.
- **Segredo NUNCA por `containerOverrides`.** Este runbook dizia, ate 22/09/2026, que passar
  a senha por `containerOverrides.environment` bastava porque ela "nao aparece em `awslogs`".
  **Estava errado** e o review do dono (P1, PR #454) pegou: `awslogs` nao e' o unico leitor —
  o override inteiro e' devolvido por `ecs:DescribeTasks` enquanto a task existir na janela de
  retencao do ECS, para qualquer principal com essa permissao. A regra agora e':
  **sonda que precisa de segredo LE do Secrets Manager, nunca de override.** O override
  carrega codigo e ARNs; o valor vem por `GetSecretValue` com a task role (secao 7) ou por
  `secrets` na task definition. Isso inclui o **verificador SCRAM**: ele e' password-equivalent
  para autenticacao, entao passa-lo por override repete exatamente o mesmo defeito.

---

## 7. Rotacao da credencial do portal (P1-A do review de 22/09/2026)

**Por que.** No provisionamento (secao 3.2) a DSN da role `portal_bff_amh` viajou em
`containerOverrides.environment`. Esse campo e' recuperavel por `ecs:DescribeTasks`. Medido em
22/09/2026: **zero tasks paradas** restavam no cluster (a janela de retencao do ECS ja tinha
expirado), entao nao ha hoje de onde extrair o valor — **mas exposicao havida e' exposicao**, e
a credencial foi rotacionada.

**Caminho escolhido: (a) — gravar a DSN nova no Secrets Manager PRIMEIRO e fazer a task
BUSCAR por API.** As alternativas e por que nao:

| Caminho | Veredito |
|---|---|
| Passar a senha ou o verificador por `containerOverrides` | **Recusado.** Repete o defeito. O verificador SCRAM e' password-equivalent: quem o tem autentica. |
| (b) Gerar a senha dentro da task e a propria task gravar no Secrets Manager | **Impossivel.** A SCP `deny-secrets-without-rotation` (`p-9m8f47yd`) nega `PutSecretValue` a tudo que nao seja `OrganizationAccountAccessRole`/break-glass/bootstrap/CI — uma task role nunca escrevera ali. |
| (a) Secrets Manager primeiro + `GetSecretValue` na task | **Escolhido.** O texto claro so' trafega em TLS ate a AWS; o override carrega apenas codigo e o ARN (que nao e' segredo: vive em `portal.auto.tfvars`, versionado). |

**A permissao que faltava.** A task role `maezo-operadora-dev-task` nao tinha
`secretsmanager:GetSecretValue` nesse segredo. Foi adicionada **por Terraform**
(`task-bootstrap-db.tf`, `aws_iam_role_policy.task_portal_session_secret`) e nao a mao —
mudanca de um statement, um unico ARN, revisavel no diff. Nao se usou `secrets` da task
definition porque `containerOverrides` **nao aceita** `secrets`: seria uma revisao nova de TD
so' para este passo. E ela nao alarga o raio de explosao: **a mesma task ja carrega a
credencial MESTRE do Aurora** (`amh_admin`), ou seja, quem a executa ja podia reescrever a
senha dessa role de qualquer jeito.

**Sequencia executada (22/09/2026):**

```powershell
# 1. permissao, por Terraform, com plan TARGETADO (1 to add, 0 to change, 0 to destroy)
terraform plan ... -target='aws_iam_role_policy.task_portal_session_secret["this"]' -out=rotacao-iam.tfplan
terraform apply rotacao-iam.tfplan

# 2. senha nova (48 chars alfanumericos, `secrets.choice`) e DSN montada em ARQUIVO LOCAL,
#    sem newline no fim — `--secret-string file://` e' obrigatorio (inline o PowerShell come
#    as aspas). O arquivo foi apagado logo apos o put.
$env:AWS_PROFILE='adm-mgmt'
$c = (aws sts assume-role --role-arn arn:aws:iam::203312548462:role/OrganizationAccountAccessRole `
       --role-session-name portal-dev-rotacao-p1a --output json | ConvertFrom-Json).Credentials
# ... exporta AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY / AWS_SESSION_TOKEN ...
aws secretsmanager put-secret-value --secret-id <ARN do segredo> --secret-string "file://dsn-nova.txt"
#   -> VersionId 2503e8cb-17c7-4e16-a642-ee449a074f46 = AWSCURRENT
#      (a versao anterior 48c1246f-... vira AWSPREVIOUS: e' o rollback)

# 3. a task de rotacao — o override leva SO' codigo e o ARN
.\scripts\ops\run-db-task.ps1 -Script .\scripts\ops\portal_rotacionar_dsn.py -ExtraEnv @{ PORTAL_SECRET_ARN = '<ARN do segredo>' }

# 4. o portal pega a DSN nova
aws ecs update-service --cluster maezo-operadora-dev `
  --service maezo-operadora-dev-portal-76e3a981 --force-new-deployment
```

**O que o script faz** (`scripts/ops/portal_rotacionar_dsn.py`, contrato: *fazer o BANCO concordar com o
Secrets Manager*): `GetSecretValue` -> valida o formato da DSN e que o usuario e'
`portal_bff_amh` -> deriva o **verificador SCRAM-SHA-256 dentro do container** (RFC 5802,
salt de 16 bytes, 4096 iteracoes) -> `ALTER ROLE ... PASSWORD '<verificador>'` como
`amh_admin` -> **loga de verdade com a senha nova** para provar. A senha em claro nunca entra
no SQL, porque `log_statement=ddl` registraria `ALTER ROLE ... PASSWORD` inteiro, sem redigir.

**Provas (22/09/2026):**

- task `5ce5c215b3c64adda21f1be6152f26f2`, `exitCode: 0`. Log:
  `login com a senha NOVA: current_user=portal_bff_amh search_path=amh portal_memberships=2`.
- `ecs describe-tasks` da propria task de rotacao: `overrides.containerOverrides[0].environment`
  contem **um unico par**, `PORTAL_SECRET_ARN` -> o ARN. Nenhum segredo.
- deployment `ecs-svc/1232776679780156949`: `rolloutState=COMPLETED`, `1/1`.
- `GET https://portal-maezo-dev.austa.com.br/api/v1/portal/session` -> **HTTP 401** com
  `{"erro":"Nao foi possivel validar a sessao."}` e `cache-control: no-store`. O 401 prova que
  o app subiu **e falou com o banco**: o lifespan de `portal/api/app.py:189` chama
  `store.purge_expired(...)` antes do `yield` e converte qualquer falha em
  `RuntimeError("Persistencia de identidade indisponivel.")` — DSN errada nao passa do startup.
- verificacao 1.7 #7 repetida no boot novo (task `ae571658cfe6496b9297e29d17e15c07`): 4 linhas
  de uvicorn e **zero** ocorrencias de
  `postgresql://|postgresql+asyncpg|password|secret|__Host-|set-cookie|code_verifier|Bearer |eyJ`.

**Rollback:** a versao `48c1246f-...` continua como `AWSPREVIOUS`. Para voltar:
`put-secret-value` com o valor dela (ou `update-secret-version-stage` movendo `AWSCURRENT`) e
**reexecutar `portal_rotacionar_dsn.py`** — como o contrato do script e' "faca o banco
concordar com o segredo", ele reescreve o verificador para a senha antiga. Depois,
`--force-new-deployment`.

**Licao, em uma linha: sonda que precisa de segredo le' do Secrets Manager, nunca de
override.**

---

## 8. Policy do endpoint S3 (P1-B) — **MEDIDO E PROPOSTO, NAO APLICADO**

O review pede restringir a regra `portal_s3_layers` (`portal-network.tf`) ao bucket de camadas
do ECR. A medicao de 22/09/2026 diz que **isso nao pode ser feito de forma aditiva, e nao pode
ser feito por este repositorio sozinho**. Os tres fatos:

**1 — O endpoint nao tem policy hoje: ele e' full access.**

```
aws ec2 describe-vpc-endpoints --vpc-endpoint-ids vpce-09e34704570f7f756
  PolicyDocument: {"Version":"2008-10-17","Statement":[
    {"Effect":"Allow","Principal":"*","Action":"*","Resource":"*"}]}
```

Nao existe statement ao qual "acrescentar". Trocar `Action:*`/`Resource:*` por uma allowlist do
bucket starport e', **por definicao, restritivo para todos os consumidores** do endpoint — nao
aditivo.

**2 — O endpoint e' compartilhado, e o repo que o possui nao e' este.** Ele esta em
**5 route tables**, todas com `Repo=amh-data-platform` e `ManagedBy=terraform` — ou seja, um
`aws_vpc_endpoint_policy` escrito aqui brigaria com o state da plataforma no proximo apply
deles (mesma armadilha ja documentada para o ingress do Aurora, secao 3.6):

| Route table | Subnets | Quem esta la |
|---|---|---|
| `rtb-0c230a53171b2b8d5` (`...-private`) | `subnet-06647b0c664d0d22a`, `subnet-0e1f840dbec44e66a` | **as 13 tasks do `maezo-operadora-dev`** (incluindo o portal), **MWAA `amh-mwaa-dev`** (medido: `NetworkConfiguration.SubnetIds` sao exatamente estas duas), ALB interno do HAPI FHIR, 2 mount targets de EFS |
| `rtb-055edba2186bef9dc` (`...-db`) | `subnet-0a5fa41803114cab0`, `subnet-0c7fe595add73e979` | ENIs do Aurora, aplicacao KDA/Flink **`amh-cdc-bronze-v3-dev`** |
| `rtb-07a08846255fe793a` / `rtb-078fed12e3f04c388` (`...-private-egress-1a/1b`) | `subnet-0e357ef571c77b3c1`, `subnet-050694d3c3803f201` | 11 VPC endpoints de interface + attachment do **Transit Gateway** (`tgw-attach-0941e0ad8f653bfc6`) — tudo que chega por TGW tambem herda esta policy |
| `rtb-01727c3058be41404` (`...-intra`) | `subnet-0cb4b83e72123f93f`, `subnet-0ee75a83a444c7fa8` | sem ENI no momento da medicao |

Outros clusters ECS na mesma conta/VPC: `amh-hapi-fhir-dev`, `amh-steward-ui-dev`,
`amh-grafana-hosted-dev`. Projeto CodeBuild: `maezo-operadora-dev-imagem`.
**O caso mais obvio de quebra e' o MWAA**: ele le DAGs, `requirements.txt` e plugins de um
bucket S3 do lago, pelas mesmas subnets — uma allowlist so' do bucket starport corta o Airflow
inteiro. A KDA de CDC escreve no bronze do lago pelo mesmo caminho.

**3 — Nao ha dois gateway endpoints de S3 na mesma route table.** "Um endpoint so' para o
portal" **nao e' caminho**. Isolamento real exigiria: subnets dedicadas ao portal -> route
table propria -> gateway endpoint proprio com a policy restritiva. Isso e' trabalho de rede no
repo da plataforma, nao um statement aqui.

**Nome do bucket, confirmado na documentacao da AWS** (*Amazon ECR interface VPC endpoints /
Minimum Amazon S3 Bucket Permissions for Amazon ECR*): o padrao e'
`arn:aws:s3:::prod-{region}-starport-layer-bucket/*`, com a regiao substituida literalmente —
para `sa-east-1`, **`prod-sa-east-1-starport-layer-bucket`**, e a acao minima e' apenas
`s3:GetObject`. A mesma pagina registra que *"The **Full Access** policy can be used because
any restrictions that you have put in your task IAM roles or other IAM user policies still
apply on top of this policy"* — e avisa que conexoes S3 existentes **podem ser interrompidas**
ao mexer no gateway endpoint.

**Policy proposta (para o dono do `amh-data-platform` aplicar, com os consumidores cientes):**

```json
{
  "Version": "2008-10-17",
  "Statement": [
    {
      "Sid": "CamadasDeImagemDoECR",
      "Effect": "Allow",
      "Principal": "*",
      "Action": ["s3:GetObject"],
      "Resource": ["arn:aws:s3:::prod-sa-east-1-starport-layer-bucket/*"]
    },
    {
      "Sid": "BucketsDestaConta",
      "Effect": "Allow",
      "Principal": "*",
      "Action": "s3:*",
      "Resource": "*",
      "Condition": { "StringEquals": { "aws:ResourceAccount": "203312548462" } }
    }
  ]
}
```

O segundo statement e' o que mantem MWAA, KDA, CodeBuild e os demais clusters de pe **e ao
mesmo tempo fecha o canal de exfiltracao que o review descreve**: uma URL pre-assinada de um
bucket de **terceiro** em `sa-east-1` deixa de passar, porque `aws:ResourceAccount` nao casa.
E' menos restritivo do que "so' o starport" e mais restritivo do que o `*` de hoje.

**Aviso honesto:** `aws:ResourceAccount` nao cobre exfiltracao para um bucket **desta mesma
conta**, e um consumidor que hoje leia bucket publico de terceiro (ex.: repositorio de pacotes)
quebraria. Fechar o primeiro exige a rota dedicada do item 3; medir o segundo exige
VPC Flow Logs / CloudTrail de dados antes do apply.

**Por que nada foi aplicado:** as duas condicoes do diretor. (i) A mudanca nao e' aditiva —
qualquer policy substitui o full access e afeta **todos** os consumidores medidos acima;
(ii) o endpoint e' de outro repositorio Terraform. Fica **proposto**, com a medicao acima, para
decisao do dono.

---

## 9. Pendencias declaradas

| # | Pendencia | Dono |
|---|---|---|
| 1 | CNAME de validacao do ACM + CNAME do hostname para o NLB (secao 2) | dono do DNS `austa.com.br` |
| 2 | Aplicar `amh-data-platform#175` (ingress do Aurora) — e decidir sobre os 3 recursos de deriva do mesmo plan | dono do state da plataforma |
| 3 | Trocar o certificado de bootstrap pelo real depois de `ISSUED` | quem aplicar |
| 4 | ~~SBOM e assinatura da imagem nao existem~~ **FECHADA em 21/09/2026**: o portal foi repinado para `sha256:d29ce828...` (tag `e857e284`), que tem SBOM SPDX e assinatura KMS publicados no ECR (`sha256-d29ce828....sig`/`.att`) e **verificados** (run 35665780360). O portao virou automatico: o job `verificar` de `.github/workflows/supply-chain.yml` roda em PR sobre `portal.auto.tfvars`. Ver `supply-chain-imagem.md` | — |
| 5 | Verificacao 1.7 #7 (log sem query/cookie/token) so fecha com o servico de pe | quem ativar |
| 6 | Papel `escalonamento-tratar` e PROVISORIO — R-034 pendente. Trocar **e** bump de `revision` | dono organizacional |
| 7 | **Perfil `staff` nao provisionado** (`portal.staff = null` -> `MAEZO_PORTAL_CAPABILITIES=identity`). Sem ele o portal autentica mas **nao tem fila de casos**: os criterios 2 a 6 do mandato (ver a fila, isolamento entre grupos, concluir tarefa) dependem de imagem derivada, bundle de materiais assinado, CMK e autoridade nativa — nenhum existe | dono do produto |
| 8 | `tests/unit/ci/test_ecs_portal_deployment.py` falha no Windows por locale: `TASK.read_text()` sem `encoding="utf-8"` le o `.tf` como cp1252 e o corpo acentuado da sonda deixa de casar. Passa com `PYTHONUTF8=1`. Defeito do teste, nao do deploy | `test-engineer` |
| 9 | Senhas temporarias dos 2 usuarios expiram em **28/09/2026** | quem testar |
| 10 | **Policy do endpoint S3 `vpce-09e34704570f7f756`** (secao 8) — proposta e medida, **nao aplicada**: o endpoint e' compartilhado por 5 route tables e pertence ao state do `amh-data-platform` | dono do `amh-data-platform` + dono do produto |
| 11 | Isolamento **real** do egresso S3 do portal exige subnets + route table + gateway endpoint dedicados (um gateway S3 por route table, secao 8, item 3) | dono da rede da plataforma |
