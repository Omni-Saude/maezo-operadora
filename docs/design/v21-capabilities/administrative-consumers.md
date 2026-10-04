# Consumidores administrativos candidatos de Lucas — Compras/Suporte

Estado: **PROPOSED / DESABILITADO / SEM BINDING PRODUTIVO**. Base inspecionada:
`e7b14522a4f70242504d2b152a57ee5269ee66e2`. Esta implementação é engenharia de
fronteira e despacho de um passo; não qualifica jornada completa, fornecedor,
contrato publicado, autoridade humana, operação ao vivo ou produção.

O plano admitido permanece sob custódia em
`/Users/familia/code/maezo-operadora/docs/audits/BPMN-CA-2026-10/v2-agent-wiring/`.
O código candidato fica no checkout isolado
`/Users/familia/.codex/worktrees/v21-capability-execution/maezo-operadora`.
Os contratos internos de operação, as atribuições de donos e os gates W0 estão
em `contracts.json` neste diretório. Nenhuma definição atual de agente/política
foi ampliada por estes módulos.

## Limite da implementação

`src/maezo/agents/lucas/administrative/graph.py` fornece dois consumidores da
mesma classe `AdministrativeConsumer`: `compras_consumer` e `suporte_consumer`.
Eles reutilizam os mesmos DTOs, parsers e `CapabilityService`, na versão
`v21-capabilities.proposed.v1`. As instâncias de serviço/admission são separadas
por tarefa e as admissions por operação. Reuso da implementação não concede
autoridade de Compras a Suporte.

Cada turno executa `receive → execute → END`: aceita exatamente uma operação,
valida handoff/objeto/tarefa atual, zera saídas antigas, chama o serviço comum
admitido e devolve seu `CapabilityOutcome`. `succeeded` significa sucesso
técnico da operação; não significa direito emitido, reserva confirmada, caso
resolvido, comunicação entregue ou jornada concluída. Um resultado `pending`
continua `pending`. Nenhuma revisão de negócio é fabricada ou incrementada no
grafo, e nenhuma tentativa incerta é repetida automaticamente.

Compras tem a topologia candidata OP01–OP10. Suporte tem OP01/07/08/09/10/11,
conforme o ledger admitido. Suporte recusa OP03 antes de chamar o gateway;
confirmação de resolução não é aceite comercial. Os membros de case kind,
case status e milestone continuam vazios por padrão e só podem vir da
composição/fonte que publicar o contrato W0 correspondente.

Não se invocam `LucasGraph.assess`, suas DMNs, prompts ou ferramentas billing.
Não há LLM, efeito direto, timer regulatório, negócio determinístico novo,
matrícula via AUTH/coverage/care, NIP informal ou start ADEQUACAO.

## Entrada e precedência

`handoff.py` propõe o objeto fechado
`v21-administrative-handoff.proposed.v1`, com somente `schema_version`,
`task_type`, `tenant_ref`, `journey_ref` e `message_ref`. Ele não é wire A2A,
evento de fornecedor ou mudança ratificada da conduta Helena. O dispatcher
atual não o constrói. `message_ref` usa o esquema keyed `hk1_` vigente; deve
ser recalculado pelo ingresso autenticado para a mensagem recebida naquele
turno. Igualdade de referência prova correlação, não assinatura/autoridade.

A entrada do consumidor contém envelope, request fechado, esse handoff e
contexto de controle do ingresso confiável: `current_message_ref`,
`health_priority`, `human_requested`. Esses controles não são campos de
payload de fornecedor e não podem ser inferidos como evidência humana.
Saúde vence pedido humano simultâneo; qualquer um deles devolve interrupção
técnica antes de chamar o gateway. Quem tratará a interrupção continua sendo
o caminho Helena/humano já governado, que não foi modificado aqui.

`state.py::new_administrative_state` faz parse fechado de envelope/request.
`validate_administrative_input` reconstrói envelope, request e handoff antes
de toda invocação pública, inclusive quando recebe o estado `{turn}` produzido
pela factory. Classe exata, dataclass frozen, `model_copy` e `model_construct`
não constituem admissão. O parser rejeita fields desconhecidos, campos
clínicos/texto livre e outputs plantados; mantém apenas o DTO exato da OP.
Membership de domínio é argumento corrente de composição, nunca input do turno
nem autoridade inferida de um request já construído.
Tenant e journey do handoff precisam casar exatamente com o envelope; o
consumidor fixa tarefa e tenant no construtor. `enabled=True` exige que todas
as admissions do serviço pertençam à tarefa fixa. O gateway ainda exige sua
própria admissão efetiva por principal/tarefa/operação/finalidade e fonte.

O consumidor também recusa a divulgação/persistência de resultado OP06 que
traga `clinical_result_context_ref`. Referência clínica não é contexto
comercial. Nenhum contexto clínico ou proxy seleciona opções/risco comercial.
Referências opacas são shape, e sua autoridade/finalidade dependem do
verificador e do contrato da fonte; não se inferem permissões pelo texto.

## Estado e checkpoint

`build()` retorna um builder fechado; `compile()` retorna o runner
`CompiledAdministrativeConsumer`, com `ainvoke` e leitura `aget_state` por
journey. Nenhuma dessas APIs devolve `StateGraph`/Pregel cru ou encaminha
atributos por `__getattr__`. `Command`, input `None`, escrita `update_state`,
streaming cru e replay não possuem contrato administrativo e não estão expostos.
O input schema LangGraph contém somente `turn`; `outcome` e `technical_status`
são canais de saída dos nós. A entrada passa pelo parser profundo e pelo match
de tenant/tarefa antes de o LangGraph receber qualquer valor, permitindo rejeição
antes até do primeiro checkpoint de input. O nó `receive` também revalida e zera
saídas do turno anterior antes de executar o passo corrente.

Sem saver injetado, a compilação usa `checkpointer=False`, impedindo também
herança incidental de saver de um grafo pai. Com saver real, a composição deve
injetar `Pseudonymizer` do gateway e o saver é passado integralmente ao LangGraph.
Roteamento permanece na tabela atual `conversa_agente_ativo`. Seu CAS é posterior aos turnos; não é
claim de comando nem proteção de efeitos matrícula/reserva/aviso. O estado de
negócio durável, revisões, dedup e recibos continuam responsabilidade da fonte
qualificada e ainda não estão implementados por este módulo.

`administrative_checkpoint_config` fornece identidade de root própria por
tarefa/tenant/journey, com derivação keyed pelo `Pseudonymizer` injetado da fronteira
gateway e prefixo `lucas:administrative:hk1_…`. A entrada de derivação tem
domínio próprio e serialização inequívoca. `checkpoint_ns` permanece vazio;
não é usado como isolamento. Nenhum telefone, mensagem crua ou referência de
journey reversível aparece no thread id. A política de chave vigente continua
exigindo composição produtiva via `Pseudonymizer.from_settings`. O runner deriva
a configuração a partir da tarefa/tenant fixos e da journey validada do turno;
aceita configuração fornecida somente se for exatamente essa configuração.
Thread arbitrário, namespace, `checkpoint_id`, metadata e outras extensões são
recusados antes do saver. `aget_state(journey_ref=...)` deriva o mesmo root sob
o tenant/tarefa fixos, sem aceitar configuração global ou seletor de replay.

O teste UNIT usa o `InMemorySaver` e LangGraph efetivamente instalados para
gravar/ler dois journeys de Compras, um root Suporte com a mesma referência
do primeiro journey e outro tenant com a mesma journey, sem sobrescrever os roots.
As regressões inspecionam `storage`, `blobs`, `writes`, warnings e erros limitados:
outputs plantados, DTOs forjados e seletores não contratados são recusados com
zero persistência. Turno seguinte no mesmo root limpa a saída anterior e preserva
precedência de saúde. Elas provam esse saver e configuração; não provam Postgres/DDL,
retention, revogação, recovery de processo nem implementação durable do
domínio. O saver produtivo escolhido precisa repetir a qualificação antes
de qualquer binding com checkpoints.

## Gates pendentes e pontos de integração

| Pacote/efeito | Fonte/dono que precisa agir | Gate necessário | Prontidão atual |
|---|---|---|---|
| Escopo Lucas e passagem administrativa Helena | Donos de produto/agente/conduta; papéis afetados em contracts.json | AW0 conduta + AW-SPEC/AW-AUTH, tarefa/canal/tools/actions | Candidato interno; nenhum YAML/Card/allowlist estendido |
| Catálogo/aceite/matrícula/agenda | Operadora ou fornecedor publicado, conforme ownership contratado | Publicação de contrato/finalidade/receipt, fonte qualificada e injetada | Ausente; nenhuma fixture usada como produto |
| Caso e confirmação S4 | Owner do caso/intake e requester atual | W0/CR-F5: membership, intenção, estados e evidências OP07 | Shape fechado; membership não publicada |
| Retorno/resume administrativo | Fonte do negócio e owner do driver | Evento/comando assinado, receipt atual, correlação tenant/journey/case/revisão | Sem driver ou binding administrativo |
| Checkpoint/durable state | Owner da persistência e segurança/DPO conforme dado | Saver real, isolamento, CAS/dedup/outbox/recovery qualificados | Somente prova UNIT InMemorySaver; fonte durable ausente |

O wiring futuro precisa ser construído na raiz real `platform/webhooks/service.py`
e `whatsapp/dispatch.py`, mantendo triagem atual em toda mensagem. Se A2A for
necessário, reutilizar assinatura/dispatcher e registrar o handler estritamente
admitido em `runtime/agent_runtime/a2a_composition.py`. Não montar ingresso HTTP
genérico nem criar persona/daemon por jornada. `gateway/tool_registry.py` e seu
construtor sancionado precisam fornecer as dependências e política efetiva.
Nenhuma dessas raízes foi editada pelo módulo candidato.

`platform/integrations/agent_resume.py` já registra Helena e `LucasRetomada`.
Eles são retorno de ESCALATION validado no histórico, não protocolo de
matrícula/reserva/jornada administrativa. Sua extensão precisa do contrato
de retorno próprio e verificação de receipt/currentness; não reutilizar
instrução livre ou direct completion como recibo administrativo.

ADR-0059 continua `Proposed — NÃO RATIFICADO`; ADR-0062 continua `Proposto`.
DL-0053 registra decisão limitada de cobrança/dev do PR #596, enquanto o ADR
registra assinatura pendente. A reconciliação exige o dono e sua evidência;
nenhum status ou assinatura foi alterado. Aprovação da direção do produto
não fecha esses gates para Compras/Suporte.

## Verificação e checklist histórico

Testes focais:
`tests/unit/agents/test_lucas_administrative_{handoff,state,graph}.py`.
Cobrem schema fechado, mensagem anterior, tenant/journey errado, outputs,
flags sem coerção, ausência de binding da tarefa, default off, saúde/humano,
reuso Compras/Suporte, pendência/refusal sem retry, proibição OP03 Suporte,
recusa de contexto clínico, revalidação de instâncias forjadas e isolamento de
roots no saver escolhido no teste.

O objeto Git `76cd8be173c93df50aa87101395b0e837d03e269` de
`docs/reports/predeploy-findings.json` foi lido sem restaurar o delete
deliberado do ROOT. Tem 127 registros históricos, incluindo findings refutados;
eles não são lista de defeitos atuais. Aplicáveis como classes a não repetir:
`lucas-cancel-start-overclaim`,
`phone-hash-keyless-brute-forceable-reidentification`,
`resume-driver-error-dlq-paths-unredacted-phi-echo-inbound-sibling-hardened` e
wiring A2A decorativo. A implementação evita overclaim, usa pseudônimo keyed,
não loga payload/exception de fonte e não afirma ingresso/retorno ativo.

Autor executou testes UNIT e lint/type focais. O gate independente, integração,
CI amplo e qualificação operacional pertencem aos papéis separados do plano.
Nenhum teste UNIT fornece assinatura, recibo de produção ou ratificação.

Achado independente **AW-SOURCE-F01 / P1**, no freeze
`e91590f1888874df1420916b395406baaebfb6f7`: o `StateGraph` cru persistia input
antes de `receive`. Terceiro autor reproduziu com LangGraph/InMemorySaver reais:
output clínico plantado e `model_copy` com dict em `wait_ref` resultavam em
`disabled`/`outcome=None`, mas o marcador sintético continuava em `blobs`/`writes`;
o segundo caso também aparecia no warning de serialização. O reparo acima fecha
esse ingresso antes da persistência e conserva o saver real injetado.

Checagem focal do autor de repair: **76 testes UNIT PASS**, `ruff check`/`ruff format`
PASS nos quatro módulos e três arquivos de teste; `mypy` PASS nos quatro módulos.
Reprodução/delta pelo reviewer original, integração e gates finais permanecem
independentes e pendentes; este resultado não os assina. Comando focal:

```sh
.venv/bin/python -m pytest tests/unit/agents/test_lucas_administrative_handoff.py tests/unit/agents/test_lucas_administrative_state.py tests/unit/agents/test_lucas_administrative_graph.py -q
```
