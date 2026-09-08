# HumanGateway — fundação D4

Fontes: ADR-0049 D3–D7/D9, ADR-0006/0008/0037, DL-0048/0005,
SP-OP-AUTH-001, SP-OP-ESCALATION-001 e SP-OP-PAGTO-001. Esta implementação
separa a autorização humana do gateway de agentes e preserva os limites hard.
Não depende de shadow, flip de política ou allowlist de testes.

`HumanGateway` resolve a sessão opaca pelo `HumanSessionResolver` atual em
cada entrada. Somente o público existente `staff` pode acessar estas tarefas
internas. Papéis e grupos devem coincidir na mesma membership; nenhum papel
administrativo recebe acesso implícito. Os vínculos de sujeito e consentimentos
exigidos pelo contrato são conferidos contra a projeção autoritativa atual.
Beneficiário/prestador precisa da futura superfície própria por recurso.

A composição injeta três portas fechadas, todas vinculadas a tenant, ambiente e
workload. Estas interfaces são **dependências confiáveis do servidor**, nunca
objetos ou endpoints que o browser pode fornecer:

- `HumanTaskTransport.read_task`: tarefa ativa e snapshot do deployment/catalogo
  pinado; grupos dinâmicos já resolvidos. Nenhum cliente REST genérico.
- `AuthorityProjection.current_authority`: principal imutável, pins completos de
  processo/formulário, revisões de membership/tarefa/evidência/autoridade e
  consentimentos atuais. O adaptador deve recusar fontes indisponíveis ou atrasadas.
- `DurableAdmission.admit`: futura TX única tenant de audit intent + outbox humano,
  com dedup/conflito por comando e cadeia íntegra. Retorna `PendingAdmission`
  somente após commit. Não envia comando ao engine.

O gateway compara as duas fontes independentes, expectativas do browser,
atribuição e operações permitidas. Reconsulta a sessão após chamadas remotas
antes de devolver snapshot ou admitir claim/release. Essa reconsulta não promete
atomicidade entre IdP, banco de membership e engine: D5 deve validar novamente as
revisões no domínio de serialização do engine antes de executar.

`HumanCommandCredentialPartition` guarda **referências não secretas**, isoladas
do `CredentialVault`/`AgentCredentialView` existente. O locator é derivado de
`human-command/{environment}/{tenant}/{workload_ref}/{key_id}`. Toda chamada
confere o escopo. Não carrega chave, não assina e não reutiliza partição de agente,
admin, OIDC, PHI HMAC ou A2A. A exclusividade do material real em KMS/cofre precisa
da futura implementação/prova de provisionamento; um nome de referência não a prova.
Principal humano e workload executor continuam distintos. Não existe assinatura
pessoal humana nem envelope D5 implícito neste pacote.

## Limite funcional e dependências obrigatórias

`create_production_gateway` recusa configuração com
`production_capabilities_unavailable`: não existem adaptadores concretos D5/D6
neste pacote. A composição por portas permite testes unitários das cercas, mas
não é uma factory de produção alternativa. Não há fallback de persistência em
memória, noop de auditoria, credencial default ou emissão direta de `/complete`.
`PendingAdmission` só representa o retorno do adaptador; seu schema não prova
commit, e o gateway nunca sintetiza `HumanCommandReceipt` ou HTTP202.

Os snapshots projetam somente claim/release elegíveis neste recorte. Decisões não
aparecem como ação ativa. As seis combinações atualmente tipadas continuam com
estas dependências explícitas, que devem ser **implementadas antes das jornadas
verticais**, sem reduzir o escopo final do portal:

| Binding | Dependência de ativação de decisão |
|---|---|
| AUTH auditor, coordenação, junta | Contrato reconciliado de custódia PHI da justificativa/cid/fundamentação e projeção classificada/referência consumível pelo engine/guard |
| ESCALATION tratamento, supervisão | Contrato reconciliado de custódia PHI de `notas_resolucao` e projeção/referência; não copiar narrativa para Zona Geral |
| PAGTO admissibilidade | Reconciliar fonte `BPMN_TASK_DOCUMENTATION_DRAFT_VERIFY` com contrato e classificação; lastro como evidência não vira confirmação |

Nenhum input obrigatório é descartado ou remapeado. `submit_decision` valida o
DTO, autorização e revisões, preserva o objeto integral na fronteira PHI e recusa
**antes da admissão**. Este módulo deve ser hospedado na Zona PHI enquanto receber
esses inputs. Não persistimos narrativa, não criamos referência PHI fictícia e não
criamos regra clínica/financeira nova. As outras 37 tarefas seguem dependentes dos
respectivos contratos/formulários, sem fallback de variáveis abertas.

Ainda obrigatórios: adaptadores autoritativos com pins/freshness verificáveis,
classificação e custódia por formulário, D5 JCS/assinatura/receipt e TX CIB real,
D6 outbox/auditoria duráveis e recuperação, D7 permissões e migração dos callers,
composição BFF e autorização de documentos/recibos. Nenhuma rota BFF foi ativada.
Esta fundação não prova egress, credenciais reais, REST protegido, atomicidade,
Cognito live, jornadas completas, deploy ou prontidão de produção.

Verificação focal:

```sh
uv sync --frozen --extra dev
.venv/bin/python -m pytest tests/unit/gateway/human/test_gateway.py -vv
.venv/bin/ruff check src/maezo/gateway/human tests/unit/gateway/human
.venv/bin/mypy src/maezo/gateway/human
```

Os dublês de portas existem somente nos testes unitários. Testes reais de engine,
PostgreSQL e recuperação pertencem aos pacotes D5/D6; não foram substituídos por
mocks de integração ou declarados executados neste pacote.
