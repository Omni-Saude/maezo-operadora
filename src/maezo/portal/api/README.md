# Sessão humana do portal — PLAN-PORTAL-D4

Implementação de ADR-0049 D3/D4/D9 e DL-0048. O BFF autentica a sessão humana;
`HumanSessionResolver.resolve()` produz o `HumanPrincipal` a partir de sessão e membership
reconsultadas no servidor. Isto não concede decisão clínica, tarefa, dossiê, acesso PHI ou
transporte engine. O futuro HumanGateway deve resolver novamente, conferir a revisão esperada,
autorização por recurso e demais contratos antes de produzir qualquer efeito. Nenhum endpoint
engine, task mutation, credencial de agente ou assinatura pessoal é implementado aqui.

## Composição de produção

`python -m maezo.portal.api` inicia FastAPI dedicado, com access logs de URL desativados.
Configuração obrigatória por deployment (prefixo `MAEZO_PORTAL_`):

| Variável | Contrato |
|---|---|
| `TENANT` | Tenant interno fixo do deployment; browser não o seleciona |
| `ISSUER` | Issuer exato do user pool Cognito HTTPS; região do hostname e pool devem concordar |
| `COGNITO_ORIGIN` | Origin HTTPS fixo e previamente aprovado do domínio Cognito/managed login; também admite domínio customizado Cognito |
| `CLIENT_ID` | Client humano dedicado, público, sem client secret, somente authorization code + PKCE S256 e scope openid |
| `CLIENT_PURPOSE` | Literal `dedicated-human-code-pkce`; declaração explícita de composição, não prova por si só de identidade |
| `MACHINE_CLIENT_ID` | ID distinto do client M2M; token de client-credentials não contém ID token humano válido e é recusado |
| `PUBLIC_ORIGIN` | Origin HTTPS exato do portal, sem path/query/fragmento |
| `DATABASE_URL` | Secret de conexão PostgreSQL `postgresql+asyncpg`, fornecido pela infraestrutura; TLS com CA/hostname verificados e parâmetros de SQL ocultos |
| `MODE` | `production` por padrão; overrides de store/client recusados neste modo |
| `SESSION_SECONDS` | 1800 por padrão, 60–3600; prazo técnico, limitado adicionalmente por exp do ID token e revisão da membership |
| `TRANSACTION_SECONDS` | 300 por padrão, 30–300; transação OIDC de uso único |

Registrar exatamente `{PUBLIC_ORIGIN}/api/v1/portal/auth/callback` no client humano.
Provisionamento AWS/IdP, client real, certificados, rede e secrets permanecem externos; nenhum
usuário/membership é semeado. O browser nunca envia issuer/JWKS/client/tenant como autoridade.
HTTPX verifica TLS, não usa proxies de ambiente e não segue redirects. JWKS só é buscado no
issuer configurado e é revalidado por troca, sem fonte de chave escolhida pelo token. RS256 é
fixo; RSA mínimo 2048 bits. Não há cliente confidencial/M2M reutilizado.

Executar a migration `0012_portal_identity_session` antes do BFF. Ela revisa `0010`, o ancestral
real deste pacote: a migration de outbox `0011` é trabalho concorrente e a integração exige uma
revisão de 0012 para suceder 0011 antes da publicação/aplicação combinada e teste de upgrade real.
Preservar cadeia linear e histórico Git; não apontar para arquivo ausente neste candidato isolado.
As tabelas são dedicadas e vazias, com privilégios PUBLIC removidos. O usuário de conexão do
BFF precisa DML apenas nas tabelas de sessões/transações/códigos e SELECT em membership; a
identidade de migração/administração é separada. Não executar BFF como owner/superuser do banco.
A implantação deve conceder esses grants sob nomes reais, isolamento por tenant, disco/backups
criptografados, residência e Zona PHI para identidades/vínculos. O módulo não concede grants a
roles inventadas nem provisiona infraestrutura.

`portal_memberships` pertence a um plano administrativo autorizado externo ao BFF. Cada linha
liga `(tenant, issuer, subject)` a `principal_ref` interno estável e um payload `MembershipRecord`
validado: revisão, memberships/roles/groups autorizados, público, vínculos verificados e
`reviewed_until`. A administração mantém colunas e payload coerentes e incrementa `revision`
para toda mudança de autoridade. Revisão vencida, membership ausente/revogada, tenant divergente
ou sujeito divergente recusam resolução. Beneficiário/prestador exige pelo menos um vínculo
do tipo correspondente; saber um resource ID não cria vínculo. Nenhum grupo de claim Cognito,
e-mail, nome, header ou body cria membership. Login não é prova ratificada de identidade LGPD.

## HTTP e ciclo de vida

- `GET /api/v1/portal/auth/login?return_to=/portal`: inicia state/nonce/verifier criptograficamente
  aleatórios, PKCE S256 e cookie de browser binding; return_to só admite `/`, `/portal`, `/portal/`.
- `GET /api/v1/portal/auth/callback`: consome atomicamente state + browser binding antes da troca.
  Código é reservado por digest, único no tenant, antes de I/O. Timeout/erro queima a transação;
  não existe retry cego que atribui resultado incerto a login válido. O novo cookie rotaciona a
  sessão anterior na mesma transação do store e redireciona para path limpo fixo.
- `GET /api/v1/portal/session`: retorna somente versão, principal opaco, público, roles, expiração
  e CSRF. Tokens, issuer/sub, grupos clínicos, vínculos e dados de beneficiário ficam no servidor.
- `POST /api/v1/portal/auth/logout`: exige cookie de sessão, Origin exato e `X-CSRF-Token` da sessão.
  Revoga sessão local no banco e remove cookies. Não afirma revogação global do login SSO Cognito.

Cookies `__Host-` são Secure/HttpOnly/SameSite=Lax, Path=/ e sem Domain; sessão é referência
aleatória de 256 bits, armazenada apenas como SHA-256. ID/access/refresh tokens não chegam ao
browser e são descartados após validação server-side; nenhum refresh é simulado ou persistido.
Ao expirar, o usuário inicia outra autorização. Membership é lida em cada resolução; mudança de
revisão invalida a sessão antiga. Não alegamos atomicidade distribuída com IdP ou revisão engine.

Transações expiradas, sessões e digests de código são podados no startup e início de login;
digests de código retidos por 24 horas superam a [validade do código Cognito de cinco minutos](https://docs.aws.amazon.com/cognito/latest/developerguide/authorization-endpoint.html).
Sessões expiradas são recusadas mesmo antes da poda. Não há bearer token em storage. O verifier
PKCE existe no store somente enquanto a transação está pendente, até consumo/poda (máximo 5min
para uso). Backups/cópias e ausência de tráfego exigem a política operacional de retenção do
banco; a elegibilidade expirada nunca é restabelecida por não ocorrer limpeza imediata.

Middleware remove query da scope usada pelo access logger do servidor; respostas e falhas
possuem `Cache-Control: no-store`, `Referrer-Policy: no-referrer`, erros estáticos em pt-BR e
nenhum exc_info. O entrypoint desabilita access logs e confiança em forwarded headers. O
balanceador/proxy deve preservar Host público, omitir query/cookies/Authorization dos próprios
logs e não instalar captura automática de requests, tokens ou PHI. OIDC necessariamente traz
code/state no callback; estes são credenciais transitórias, não payload de PHI de caso. Não há
analytics, URL de caso PHI, localStorage/IndexedDB ou cache offline neste módulo.

## Verificação e limites

`tests/unit/portal/test_human_session.py` usa ASGI e IdP de teste explícito, com assinaturas RSA
reais e oráculo PKCE. Não é teste live Cognito. `LocalTestIdentityStore` precisa injeção explícita
em `mode=local-test` e nunca é fallback de produção.

A lane PostgreSQL real é `tests/unit/portal/test_human_session_postgres.py -m integration`;
exige `MAEZO_TEST_DATABASE_URL`, cria schema isolado, aplica/downgrada migration real, usa duas
engines independentes para corridas e prova rollback/rotação/restart/revogação/TTL. Ausência do
banco falha explicitamente. O autor apenas coletou esta lane; execução pertence ao root sob
coordenação de recursos, seguida de assurance independente. Não há mock engine nesta lane.

Referências primárias de implementação: [PyJWT API](https://pyjwt.readthedocs.io/en/stable/api.html),
[AWS ID token](https://docs.aws.amazon.com/cognito/latest/developerguide/amazon-cognito-user-pools-using-the-id-token.html),
[AWS app clients](https://docs.aws.amazon.com/cognito/latest/developerguide/user-pool-settings-client-apps.html),
[OIDC ID Token Validation](https://openid.net/specs/openid-connect-core-1_0.html#IDTokenValidation).
