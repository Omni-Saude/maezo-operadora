# Descritor de deploy do engine nativo (T1.2)

Plano: `docs/plans/portal-autoridade-nativa-dev.md`, Onda 1, T1.2. Decisões: ADR-0049 D5
(o servidor nativo é o plugin dentro do engine), ADR-0060 D2 (path `maezo_native,cibseven` e
`sslmode=verify-full`), D-A/D-B do plano (mesmo engine, TLS de ponta a ponta até o Tomcat 8443).

**Nada aqui foi aplicado na AWS e a imagem viva não mudou.** Trocar o engine de dev para esta
imagem é a Onda 4, com os pré-requisitos dela (T1.1, T1.7a/b, C1, materiais das Ondas 2 e 3).

## O que a imagem `deploy/cibseven/Dockerfile.human` passa a ter

| Peça | Onde | Por quê |
|---|---|---|
| `conf/server.xml` de deploy | `native-deploy/server.xml` | HTTP 8080 igual ao de hoje (engine-rest de worker, agentes, canal e Helena) e **um** connector TLS 8443, `certificateVerification="required"`, TLS 1.3, `allowTrace="false"`, sem proxy/forwarded, sem senha no arquivo |
| Datasource pinado | mesmo arquivo | `currentSchema=maezo_native,cibseven&sslmode=verify-full&sslrootcert=/camunda/conf/rds-sa-east-1-bundle.pem`. Imagem e path revertem juntos (ADR-0060, Consequência 3): voltar o digest volta o path |
| `bin/setenv.sh` | `native-deploy/setenv.sh` | `${DB_*}` do `server.xml` resolvidos do ambiente (`EnvironmentPropertySource`), `EXIT_ON_INIT_FAILURE=true`, e recusa de boot se `SKIP_DB_CONFIG` não for `true` ou faltar `DB_HOST`/`DB_PORT`/`DB_NAME` |
| Raízes RDS sa-east-1 | `deploy/certificates/sa-east-1-bundle.pem` | o mesmo arquivo vendorizado do portal, conferido por SHA-256 no build |
| `ENV SKIP_DB_CONFIG=true` | Dockerfile | sem isso o `cibseven.sh` da base reescreve o datasource a partir de `DB_URL` (ou do H2 padrão) |
| Paridade com a imagem viva | Dockerfile | `jobExecutorDeploymentAware=false` e `configure-group-whitelist.sh`, os dois deltas de `deploy/cibseven/Dockerfile`. Sem eles, a Onda 4 pararia os timers e a whitelist de grupos |
| `ARG INSTALL_STAFF_COMPOSITION` | Dockerfile | `false` por padrão. `true` registra `br.com.maezo.human.StaffDeploymentComposition` (T1.1, D-E) **antes** do `HumanCommandPlugin` e **recusa o build** se a classe não estiver no JAR. Hoje (T1.1 não mergeada) `true` falha de propósito |
| `ARG INSTALL_PORTAL_READ` | Dockerfile | inalterado. `true` constrói, mas **não sobe** até a T1.7a (I6). A imagem da Onda 4 só usa `true` depois dela |

## Contrato de execução (o que a task definition da Onda 4 precisa entregar)

| Entrada | Forma | Observação |
|---|---|---|
| `DB_HOST`, `DB_PORT`, `DB_NAME` | env | obrigatórios; a ausência recusa o boot |
| `DB_USERNAME`, `DB_PASSWORD` | `secrets` do ECS (os de hoje, `cibseven_app`) | sem eles o `cibseven.sh` injeta `sa`, e o PostgreSQL recusa |
| `DB_URL` | — | **inerte** nesta imagem. A TD pode mantê-lo para a imagem antiga, e o rollback continua sendo só o digest |
| `SKIP_DB_CONFIG` | — | não declarar. Qualquer valor diferente de `true` recusa o boot |
| `MAEZO_HUMAN_TRUST_FILE` | env + arquivo montado | exigido pelo `HumanCommandPlugin` (sem ele o engine não sobe) |
| `/run/maezo/native/server.crt`, `server.key` | arquivos montados read-only, legíveis pelo uid 1000 | PEM, chave sem senha. SAN = nome da zona privada de D-B. Gerados pela T1.3 |
| `/run/maezo/native/client-ca.p12` | arquivo montado read-only | truststore PKCS12 **sem senha** só com a CA dos clientes nativos. O JSSE do Tomcat 10.1 ignora `caCertificateFile` (medido: o cliente confiável foi recusado com `CERTIFICATE_UNKNOWN`) |
| `portMappings` 8443 | TD | mais o NLB TCP 443 → 8443 e o SG, na Onda 4 |

Faltando qualquer arquivo do connector, o Tomcat **sai** (`EXIT_ON_INIT_FAILURE`), em vez de
servir só a 8080. Falha de banco (credencial, TLS, path) faz o engine não subir; o processo sai
com código 0, então o sinal no ECS é a task parada e o health check, não o exit code.

## Prova local (23/09/2026, Docker Engine 29.8.0, nada na AWS)

Receita, com a fixture do harness (`package-test/prepare._generate_fixture`) para a CA, o
certificado do servidor, os clientes e o `trust.json`, e um compose próprio que **não** monta
`server.xml` (o que se prova é o descritor da imagem):

1. `docker build -f deploy/cibseven/Dockerfile.human -t <tag> .` (contexto na raiz).
2. PostgreSQL 17 com TLS (`hostssl` obrigatório no `pg_hba`, certificado com SAN `postgres`) e o
   layout de D-C2: `cibseven` (dono `cibseven_app`), `maezo_native` (dono
   `maezo_native_schema_owner`), `human-schema-postgres.sql` instalado pelo dono em `maezo_native`,
   `USAGE` + DML para `cibseven_app`.
3. Primeiro boot com a imagem **antiga** (`deploy/cibseven/Dockerfile`) e `currentSchema=cibseven`,
   para criar os `ACT_*` (ordem do runbook de banco vazio, ADR-0060 Consequência 4).
4. Imagem nova com `DB_HOST/DB_PORT/DB_NAME`, a CA local montada **no caminho pinado**
   `/camunda/conf/rds-sa-east-1-bundle.pem` (só na prova local) e os materiais em
   `/run/maezo/native/`, rodando como uid 1000.
5. `package-test/check.py` com `MAEZO_ROOT_FIXTURES=1` e `ENGINE_REST_URL` da porta publicada.

| Verificação | Resultado |
|---|---|
| boot da imagem nova | 20-23 s, `http-nio-8080` e `https-jsse-nio-8443`, uid 1000 |
| `ACT_*` / `mzo_*` por schema | 49 em `cibseven` / 6 em `maezo_native`, nenhum cruzado |
| sessões do engine | `cibseven_app`, `ssl=t`, TLS 1.3 (10 conexões do pool) |
| precondições AUTH com o mesmo path | `current_schema=maezo_native`, dono `maezo_native_schema_owner`, `owner_differs=t`, `lacks_create=t`, memberships 0 |
| `check.py` (5 testes do pacote) | **5 passed** |
| `POST https://…:8443/maezo-human/v1/staff-case-list` `{}` com certificado confiável | `503 {"error":"HUMAN_ENGINE_UNAVAILABLE"}`, a recusa fechada do plugin sem configuração staff (T1.1) |
| mesma rota pela 8080 | `403 {"error":"AUTHORITY_DENIED"}` |
| `GET /engine-rest/engine` e `/version` pela 8080 | `200 [{"name":"default"}]`, `{"version":"2.1.0"}` |
| `TRACE` na 8080 e na 8443 | `405` |
| senha do banco no log do engine | ausente |
| `DB_URL=…currentSchema=cibseven` na TD | inerte: engine sobe e o `server.xml` fica intacto |
| sem `server.key` | Tomcat sai com 1: `Protocol handler initialization failed` / `FileNotFoundException` |
| `SKIP_DB_CONFIG=""` | sai com 1: `FATAL (T1.2): SKIP_DB_CONFIG must stay 'true'` |
| sem `DB_HOST` | sai com 1: `FATAL (T1.2): DB_HOST is required` |
| sem `DB_PASSWORD` | PostgreSQL recusa (`password authentication failed`), engine não sobe |
| com as raízes RDS reais (servidor local) | `PKIX path building failed`: o `verify-full` é de verdade |
| build `INSTALL_STAFF_COMPOSITION=true` / `=yes` | falha no estágio Maven (classe ausente / valor inválido) |
| build `INSTALL_PORTAL_READ=true` | constrói; descritor com `PortalReadPlugin` antes do `HumanCommandPlugin` (não sobe até a T1.7a) |

## O que isto não resolve

- O `engine-rest` continua sem autenticação na 8080 (dívida D7, N5). Isso é aceito **só em dev**,
  e a cerca `scripts/ci/check_engine_rest_auth.py` reprova qualquer outro ambiente (T1.9).
- A 8443 também serve o `engine-rest` a quem tiver certificado de cliente da CA nativa. Só o portal
  recebe esse certificado e o NLB só aceita o SG do portal (D-B), mas é superfície a registrar.
- Staff continua em `503` até a T1.1 (composição) e o provedor Q2 (T1.7a/b).
- DDL staff/AUTH/admissão em `maezo_native` é a T1.4. A prova acima instala só o DDL human.
