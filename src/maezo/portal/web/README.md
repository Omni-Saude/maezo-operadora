# Portal web — sessão humana

Este pacote implementa somente a entrada e a casa de sessão do contrato público existente no BFF:
login, `GET /api/v1/portal/session` e logout protegido por CSRF. O OpenAPI exportado pelo app FastAPI
gera `src/generated/api.ts`; não existe um segundo `SessionDTO` manual.

O backend desta revisão ainda não publica filas, casos, decisões ou recibos. Por isso a interface não
oferece links nem dados simulados para essas funções planejadas.

Use `npm ci` e `npm run verify`. `check:generated` reconstrói o schema com dependências locais que
recusam rede e compara os bytes do OpenAPI e dos tipos gerados. Os testes de comportamento substituem
somente a fronteira `fetch`; não iniciam navegador, BFF, Cognito, banco, engine ou outro serviço.
