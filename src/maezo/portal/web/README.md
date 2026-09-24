# Portal web — sessão humana

Este pacote implementa somente a entrada e a casa de sessão do contrato público existente no BFF:
login, `GET /api/v1/portal/session` e logout protegido por CSRF. O OpenAPI exportado pelo app FastAPI
gera `src/generated/api.ts`; não existe um segundo `SessionDTO` manual.

Para o perfil staff, a área Casos tem endereço próprio: `/portal/cases` (fila de casos de autorização
liberados ao grupo, `StaffPage`) e `/portal/cases/{case_ref}` (detalhe somente leitura, `StaffDetail`).
A tela mostra só os campos desses DTOs; uma faixa de validade drena até `freshness.valid_until`, quando a
leitura é refeita. O 503 cobre tanto serviço fora do ar quanto capacidade staff desligada no ambiente: o
contrato não distingue os dois, e a mensagem diz isso. Entrada direta nesses endereços exige fallback SPA
no host.

Use `npm ci` e `npm run verify`. `check:generated` reconstrói o schema com dependências locais que
recusam rede e compara os bytes do OpenAPI e dos tipos gerados. Os testes de comportamento substituem
somente a fronteira `fetch`; não iniciam navegador, BFF, Cognito, banco, engine ou outro serviço.
