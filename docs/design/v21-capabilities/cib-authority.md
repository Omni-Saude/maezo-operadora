# v2.1 — CIB, DMN e autoridade humana

Autoria de engenharia em 2026-10-04; baseline de implementação
`e7b14522a4f70242504d2b152a57ee5269ee66e2`, branch
`codex/v21-capability-execution`. Este pacote W0 concretiza os gates de W4; não
ratifica contrato, política, prazo, fonte ou nova permissão. O registro legível
por máquina está em [cib-readiness.json](cib-readiness.json).

A direção de produto admite jornadas conduzidas pelos agentes existentes e
reuso de mecanismos de governança. Os cinco patches D3 propostos no plano e os
dois candidatos GP1/GP2 ainda exigem contrato e qualificação próprios. Nenhuma
nova chave, tabela DMN, timer, binding humano ou starter de ADEQUACAO é admitido
por este documento. Runtime e implantação permanecem não verificados.

## Autoridade e seleção de artefato

As autoridades normativas são ADR-0001, ADR-0012, ADR-0018, ADR-0028,
ADR-0037, ADR-0049 e os contratos SP-OP afetados. O plano admitido permanece no
bundle absoluto `docs/audits/BPMN-CA-2026-10/v2-agent-wiring/`, composto com o
`../v2-capabilities/plan.json` preservado. Os campos usados aqui são
`artifacts`, `existing_governance_patches`, `new_governance_candidates`,
`source_processes`, `internal_operations` e `waves`. A autoria futura exige
gate de delta sobre os bytes de cada pacote, sem reescrever o bundle admitido.

ADR-0001 reserva CIB para obrigação governada: SLA regulatório, ato humano
mandatório, auditoria de não repúdio ou transação legal entre atores. Navegação,
opções, reserva ordinária e confirmação de resolução ficam nos módulos
compartilhados e no runtime dos agentes. Nenhuma etapa do material de referência
constitui, por si só, razão para criar BPMN ou worker.

| Pacote | Autoridade e fonte necessárias | Implementabilidade nesta janela | Gate para o efeito |
|---|---|---|---|
| AUTH / C3, patch D3 | Contrato SP-OP-AUTH-001; pedido assistencial/TISS legítimo; médico auditor no ramo protegido | Preparar correlation e contrato de marcos; preservar validação de critérios, análise humana e publicação de pendência | Delta contratado, allowlists de variáveis/eventos, reviewer de contrato e engine real; matrícula administrativa continua separada |
| ADEQUACAO / C4 e U2, dois patches D3 | Contrato SP-OP-ADEQUACAO-001; obrigação de rede verificada; fato autoritativo da origem | Concretizar ingresso governado e retorno referenciado; starter ainda não construído/admitido | Dono da rede/PO decide origem; regulatório ratifica obrigação/prazo; contrato de fonte; gate independente do bridge, human authority e engine |
| ESCALATION / S3, patch D3 | Contrato SP-OP-ESCALATION-001; pedido humano, risco ou obrigação comprovada | Reusar intake/caso/espera e preparar referência de caso; grupos e timers existentes preservados | Qualificação exata de ingress, contexto referenciado, binding humano nativo e retorno/resume; engine real se o fluxo for alterado |
| NIP / S4, patch D3 | Contrato SP-OP-NIP-001; NIP efetivamente recebida da autoridade competente | Preparar correlation do caso de origem e retorno de status | Prova de ingresso ANS; contrato/eventos e engine real; insatisfação ou ouvidoria interna não abrem NIP |
| GP1 / F3, candidato D2 | Operadora/emissor administrativo e jurídico; aceite versionado, direito-base e obrigação distinta | Dossier de decisão; nenhuma chave/tópico/BPMN a implementar agora | Contrato aceito identifica autoridade/obrigação; reuso avaliado; eventos/estado/compensação ratificados; ADR-0037/0038 reconciliados pelo dono; arquitetura/compliance/security independentes |
| GP2 / F4, candidato D2 | Dono da entrega/rede e regulatório; obrigação distinta não coberta por AUTH/ADEQUACAO | Dossier de necessidade condicional; agendamento ordinário permanece adapter/jornada | Demonstrar lacuna de obrigação após avaliar reuso; contrato/fonte/prazo/recuperação humanos; mesmos gates independentes de GP1 |
| AR2 / regras administrativas DMN | Dono do produto/catálogo publica regras, entradas, saídas, versão e validade; fonte de catálogo/disponibilidade | Reuso de `DmnTransport`/`CibSevenDmnTransport`; nenhuma tabela administrativa inventada | Regras ratificadas, tipos/allowlists, composição gated, versão engine comprovada e testes reais da decisão |
| U4 / fatos, feedback e financeiro | Fonte de prestação/desfecho; dono de instrumento de feedback; financeiro em CONTAS/PAGTO | Extração conceitual em contratos distintos; prova estática registrada abaixo | Fatos/receipts da fonte; feedback opcional independente; financeiro conserva autoridade; fluxo transformado sound e integração real qualificada |

Os patches D3 estão classificados no plano como
`CONTRACT_SPEC_AND_INDEPENDENT_ENGINE_QUALIFICATION_REQUIRED`; aprovação do
plano não os converte em contrato FINAL nem em fonte viva. Os gates humanos
afetam o efeito dependente e permitem continuar engenharia independente.

## ADEQUACAO: origem governada, sem start de agente

R-049, no contrato SP-OP-ADEQUACAO-001, fixa que nenhum agente inicia esse
processo. André recebe delegação para instruir dossiê de uma instância existente.
A presença da chave em `KNOWN_PROCESS_KEYS` é universo de validação, não
concessão a uma persona. O teste
`tests/unit/gateway/test_adequacao_sem_binding_de_agente.py` verifica todos os
manifests, incluindo o template, e a capacidade efetiva de `AgentCapabilities`.

A origem de rede descrita pelo contrato tem produtor em CRED e tópico
`agents.events.cred.network_changed`, mas o consumidor
`maezo.platform.integrations.network_change_bridge` não existe no baseline.
O contrato ainda registra AF-01/PERSP-NETBRIDGE e fatos/taxonomia/periodicidade
como DRAFT/verify. Nenhum start direto de Carolina, André ou outro agente fecha
essa lacuna. As duas novas causas propostas pelo plano exigem contrato de
obrigação, source authority e nova admissão do bridge; nenhum trigger/timer é
escolhido aqui.

Um bridge futuro deve validar produtor, tenant, identidade completa do objeto,
versão do fato e currentness; preservar inbox/outbox e idempotência; recusar
origem incompleta antes do start; devolver outcome ao caso por causalidade.
Não presumir que Kafka offset ou `already_existed` confirma a resolução.
Compromisso de fallback permanece em `UT_DecisaoFallback` ou
`UT_CoordenacaoRede`, nos grupos canônicos, com guard do worker. Timeout e DMN
de severidade não autorizam compromisso financeiro.

ADR-0038 está Proposed e declara que nenhuma cláusula tem efeito. A divergência
de forma e a questão de opacidade/PHI das business keys seguem DL-0043/DL-0044.
Preservar os compositores existentes; nova chave requer decisão concreta do
dono e revisão de privacidade. Nenhuma troca de separador resolve sozinha
idempotência, colisões ou identidade de fonte.

## DMN: engine, tipos e versão efetivamente avaliada

Usar `src/maezo/tools/workers/dmn_transport.py` e composição do consumidor
qualificada. `require_dmn` recusa seam ausente; `first_row` recusa resultado
vazio. Erros de transporte/proveniência são transientes; no-match e JSON
malformado são incidentes determinísticos. O chamador valida os tipos do
contrato; o mapper de wire não transforma qualquer entrada em regra válida.
Campos monetários seguem a declaração canônica de centavos/Long.

O transporte resolve definição por chave/tenant e mantém cache; avalia por
chave. Seu contrato atual exclui redeploy durante a vida do daemon. A
qualificação de uma nova política deve provar definição/versão/deployment
efetivamente utilizados e manter esse limite operacional, ou propor e verificar
um delta específico antes de permitir atualização concorrente. Não inferir
segurança de redeploy de um teste de cache. Definição tenant-specific requerida
não pode cair silenciosamente na global.

Não usar `mcp_dmn.server.DmnServer` como avaliador de runtime, nem reimplementar
tabela em Python/prompt. Nenhuma regra nova de elegibilidade, ranking, canal,
capping, deadline, preço ou liquidação foi ratificada neste pacote. A autoridade
do catálogo comercial e a autoridade clínica continuam distintas; tabela de
roteamento de cuidado não vira elegibilidade comercial.

## Comando humano nativo e recibo

Reutilizar mecanismos de `HumanGateway.submit_decision`,
`decision_binding`, projeção, custódia, outbox/admission e `relay`. A sessão,
membership, tarefa, authority revision, evidência, form/process pin e binding
digest devem estar atuais antes da admissão e ser reconferidos após I/O de
custódia. O engine serializa a revisão e mantém o recibo na transação do efeito;
redelivery/reconciliação conservam identidade e bytes do comando.

`projection.verify_engine_receipt` confere schema, tenant, tarefa, comando,
operação, principal/workload, audit intent, digest e revisão consumida. A leitura
de recibo exige concessão atual ao recurso e reconsulta de sessão, inclusive
depois de concluir a tarefa. Timestamp de registro no engine não é relógio de
currentness da autorização.

`PendingDecisionAdmission` confirma admissão durável, não decisão aplicada.
Ausência/timeout do engine conserva estado pendente/incerto; nunca vira
commit inventado. Direct completion interina de DL-0049 não é fallback desta
jornada. Nenhuma nova forma/grupo/binding nasce de extensão genérica do gateway.
OP03 usa autoridade do cliente e oferta/termos exatos: reutilizar proteção de
comando/recibo não transforma recibo clínico em assinatura comercial. OP04 exige
recibo do emissor de matrícula; coverage GET, `care.enroll`, usuário do portal e
enrollment técnico de engine não emitem direito-base.

## U4: prova estática e limite financeiro

Os XML de referência ficam sob a custódia privada existente; não copiar,
instalar, anexar ou publicar esses bytes no PR. Referências e digests constam do
JSON acompanhante. Foi usada análise somente leitura com
`xml.etree.ElementTree`, independente dos scripts históricos baseados em lxml.
Nenhum XML ou evidência histórica foi regravado.

| Fonte privada | Token witness estático | Pagamento independente de feedback | Engine |
|---|---|---|---|
| Fonte original, SHA-256 `0d39296d011bbc531f8f87a57e1120d661450fca819033590c79fdb8989e7658` | FAIL | FAIL | UNVERIFIED |
| Adaptado preservado, SHA-256 `dae17b4872432d751c102c8318112f7cfc650763048ddd2aa1282310a1045023` | PASS para o fragmento analisado | FAIL | UNVERIFIED |

A prova abstrata é: fork de dois ramos emite dois tokens. Conclusão de pesquisa
e boundary interruptivo do mesmo host são alternativas, portanto não alimentam
três entradas distintas de AND. Convergir essas alternativas uma única vez
antes de join de duas entradas elimina esse deadlock. Isso não prova liquidação
independente: se o pagamento fica após o join com feedback, aguarda resposta ou
timeout. Ambos os arquivos privados conservam essa dependência. O adaptado não
fecha W4 e não é candidato automático a instalação.

A transformação deve separar fato de prestação/desfecho, solicitação financeira
e feedback. Feedback pode ser omitido, respondido, expirar ou falhar sem barrar
o caminho financeiro admitido. Nenhum silêncio confirma resolução; nenhum
desfecho clínico ou avaliação de experiência libera pagamento. Valor, alçada,
lastro/obrigação e recibo pertencem aos contratos CONTAS/PAGTO. Não transpor prazo
do material de referência para obrigação regulatória.

Reprodução privada: conferir os SHA-256 antes de ler; selecionar processo,
fork, join, survey host, boundary e settlement pelos locators da evidência
custodiada; reconciliar `incoming/outgoing` declarados com `sequenceFlow` reais;
enumerar os dois traços (resposta ou cancelamento por boundary interruptivo);
confirmar uma saída por alternativa; calcular dominância do join sobre
settlement removendo o join do grafo. Os scripts históricos
`evidence/p1-token-witness.py` e `feedback/token-witness-postfix-ca2.py` contêm os
locators, mas escrevem relatórios nos paths originais: não executá-los sobre o
bundle preservado. O verificador deve reproduzir com leitor independente e
saída em sua custódia, sem publicar o grafo ou conteúdo CA. Resultado estático
não substitui engine real nem qualificação do contrato financeiro.

## Checklist autoritativo e qualificação

O inventário foi lido pelo objeto Git do baseline, preservando o delete do ROOT:

```sh
git show e7b14522a4f70242504d2b152a57ee5269ee66e2:docs/reports/predeploy-findings.json
```

Blob `76cd8be173c93df50aa87101395b0e837d03e269`; SHA-256
`7a99ffb007c66aa93115a81fff4089a386ddd6c55ba6c3a0ef5023f91e106751`; 127
entradas, incluindo refutadas. A contagem histórica de AGENTS não substitui
o objeto. Aplicar os padrões abaixo; seus rótulos históricos não são diagnóstico
atual e nenhum finding é encerrado por este documento.

| Finding do inventário | Pacote / prevenção |
|---|---|
| `auth-denial-not-human-undeclared-error` | AUTH/human: código emitido, catálogo e comportamento real de incidente/escape devem casar |
| `cancel-brtcancelsla-dangling-outgoing-idref` | Todo delta BPMN: conferir incidência declarada e real, além de reachability |
| `cred-business-key-missing-collision-fallback` | Bridge/idempotência: identidade completa, sem colapsar casos distintos em chave vazia |
| `publish-missing-topic-guard-unreachable-in-current-deployment` | Publicação: catálogo fechado e produtor de variável/evento conferidos antes de tornar parametrizado |
| `no-denial-boundary-escape-reachability-blindspot` | Prova de autoridade: incluir boundaries de todas as atividades, não só user tasks |
| `dangling-not-human-guard-errors-no-boundary-catch` (KILLED) | Não reabrir severidade refutada por ausência de catch isolada; guard fail-closed/incident e semântica final exigem prova |
| `fraude-scoring-dmn-ungoverned-by-signoff-gate` (KILLED) | Não usar classificação refutada como waiver; nova regra exige fonte/ratificação e consumidor comprovados |

Checks baratos aplicáveis: R-049, `check_start_process_fence.py`,
`check_effect_chokepoint_fence.py`, `check_portal_direct_completion.py`,
testes de transporte DMN, projeção/recibo/currentness e contrato dos bindings.
O JSON registra execução própria; o verificador independente reproduz sobre
SHA exato e emite veredicto próprio. Gate do plano, teste unitário e engine
qualificado são evidências diferentes.

Este delta documental não altera engine e não precisa iniciar stack. Para
admitir delta em BPMN/DMN, starter, timer ou binding humano: uma única lane CIB
Seven real, coordenada, com versão/digest/deployment, tenant, process/task pins,
trace de ambas as alternativas de espera, redelivery/concurrency e recibos
reais. Usar as famílias relevantes em `tests/integration/dmn/` e
`tests/integration/processes/`; não mockar engine, fabricar fixture ou converter
ausência em PASS. Na ausência de lane qualificada, manter runtime UNVERIFIED e
o efeito bloqueado. Antes de entregar implementação afetada, completar
`make lint type test validate-artifacts` e CI aplicável, sem ativar produção.
