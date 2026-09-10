# Contrato — SP-OP-AUTH-001 (Autorizacao Previa)

**Status:** DRAFT (v0.1.0) — `DRAFT — requires human review before any deploy` (docs/review-queue.md)
**Fase:** 1 (modelado uma fase a frente) · **BPMN:** `spec/processes/bpmn/SP-OP-AUTH-001_Autorizacao_Previa.bpmn`
**Gatilho regulatorio:** RN 259/2011 (garantia de atendimento — consolidacoes posteriores ANS: **DRAFT/verify**), RN 395/2016 (resposta/negativa por escrito: **DRAFT/verify**), RN 424/2017 (junta medica: **DRAFT/verify**), Lei 9.656/1998 art. 35-C.

## Invariante L0 hard (nao negociavel)

Negativa de cobertura SO nasce nas User Tasks humanas (`UT_AnaliseMedicoAuditor`,
`UT_CoordenacaoAssume`, `UT_RegistrarParecerJunta`); a decisao carrega `auditor_id` para a
cadeia de auditoria (ADR-0007) e e essa a proveniencia humana que o guard de
`operadora.auth.send_denial_notice` exige (`ERR_DENIAL_NOT_HUMAN`). Nenhuma DMN deste processo
possui saida de negativa; inelegibilidade/carencia aparentes roteiam para analise humana.
Aprovacao automatica (L2) existe apenas quando os QUATRO criterios computados por
`operadora.auth.validate_auto_criteria` (tecnico, financeiro, regulatorio, contratual) sao
atendidos E o validador comprovadamente executou (`auto_criteria_verificado`) — ADR-0008. Um
criterio so pode contribuir com um PASS se sua fonte de regra estiver RATIFICADA
(`spec/processes/dmn/auth-criteria-ratification.yaml`); fonte DRAFT falha FECHADO para analise
humana. O validador NUNCA nega: todo caminho fail-closed leva ao medico auditor.

## Business key (idempotencia)

```
AUTH-{tenant_id}-{numero_guia_tiss}
```

Uma instancia por guia TISS; reenvio retorna a instancia ativa.

### Portal AUTH — fronteira de construção E01 (2026-09-10)

Extensão de interface autorizada pelo ADR-0049 e pelo plano product-first; **DRAFT de
engenharia, revisão independente e aceitação operacional pendentes**. Não ratifica regras
clínicas/regulatórias, não altera o BPMN executável e não declara APIs já disponíveis.
Prestador solicita; beneficiário acompanha e responde a pedidos documentais autorizados;
colaborador assume/revisa a mesma instância `SP-OP-AUTH-001`. Não criar processos por público.
O escalonamento de SLA já é `UT_CoordenacaoAssume`, e junta já é
`UT_RegistrarParecerJunta`; nenhum deles inicia ESCALATION ou PAGTO automaticamente.

**Identidade e chave.** O BFF resolve tenant, pessoa, vínculo atual com beneficiário/prestador
e referências de caso no servidor (ADR-0049 D4). A chave legada acima é interna: nunca
exibi-la em URL/erro/recibo externo nem derivar acesso de sua posse. ADR-0038 continua
Proposed; não autoriza renomear chaves implantadas nem ignorar opacidade/colisões da
fronteira ADR-0037. O adaptador de intake deve provar isolamento e deduplicação concorrente
na admissão durável; o `NON_STRICT` do start legado, sozinho, não prova essa garantia
(DL-0046). Retentar o mesmo comando recupera seu resultado; payload diferente com o mesmo
comando conflita. A política para uma guia já encerrada exige resolução explícita no
adaptador/contrato antes de ativar; não presumir uma nova autorização.

#### Interface tipada para E02–E05

Paths relativos a `/api/v1/portal`; identificadores de recurso são opacos. Estes campos
definem a fronteira semântica; a implementação publica sua forma exata em OpenAPI e gera
o cliente TypeScript, sem um segundo schema manual. Campos desconhecidos são recusados.

| Ação / API | Entrada permitida | Autoridade / efeito e resposta |
|---|---|---|
| Prestador envia `POST /intakes/auth` | `command_id`, referências de beneficiário e prestador selecionadas de vínculos autorizados, guia TISS protegida, `codigo_procedimento_tuss`, `categoria_procedimento`, `carater_atendimento`, valor em centavos como string decimal inteira, referências de anexos finalizados; CID opcional somente por custódia PHI | Servidor verifica vínculo/escopo/revisão/validade/revogação, resolve pseudônimo e fatos de admissibilidade; seleciona exclusivamente `SP-OP-AUTH-001`. Responde com referência de intake e estado técnico do comando após admissão durável; caso só é afirmado após prova do start. Nenhum `tenant_id`, actor, process key, mapa de variáveis ou fato de aprovação fornecido pelo browser se torna autoridade |
| Todos os públicos autorizados consultam `GET /cases`, `/cases/{case_ref}`, `/history` | Referência opaca e cursor emitido pelo servidor | Projeção mínima por público, vínculo e tenant atuais; responsável, fase, freshness, prazo do engine, ações permitidas e histórico. Beneficiário/prestador não obtêm formulário interno nem dossiê clínico por compartilhar o caso |
| Upload em `POST /intakes/{intake_ref}/document-uploads` ou `/cases/{case_ref}/document-uploads`; finalização `POST /document-uploads/{upload_ref}/complete` | Metadados mínimos previstos na política documental e referência de upload; bytes somente na zona/canal protegido | Custódia liga upload a tenant, intake/caso, principal e finalidade. Finalização verifica bytes/digest, tipo/tamanho conforme política aplicável e screening. Quarentena/falha nunca produz anexo utilizável; reentrega é idempotente |
| `GET /cases/{case_ref}/document-requests`; `POST /cases/{case_ref}/document-requests/{request_ref}/responses` | `command_id`, revisão esperada do pedido e referências de documentos finalizados | Autoridade atual permite ao prestador e, quando o vínculo/escopo autoriza, ao beneficiário responder à MESMA pendência. Recibo técnico da resposta distingue admissão, correlação executada e conflito; não afirma aprovação nem completude por mero upload |
| `GET /cases/{case_ref}/documents`, `GET /documents/{document_ref}/content` | Referências opacas | Revalidação de acesso/custódia em cada leitura; nenhuma URL durável pública nem cache persistente no browser |
| Colaborador usa `/tasks/{task_id}/assignments`, `/assignment-candidates`, decision-context e decisões existentes | Claim/release/reassign discriminados, command ID, revisões; alvo opaco entre candidatos autorizados; decisão/formulário tipados | Claim/release reutilizam autoridade existente; reassign depende de suporte nativo novo. Revisão, membership, atribuição, evidência e binding exato são rechecados na execução. `auditor_id` é ligado à identidade humana autenticada, não escolhido pelo browser |
| Comunicações em `GET /cases/{case_ref}/communications` e recibos/estado em `/commands/{command_id}` e `/receipt` | Referência opaca; cursor quando aplicável | Inbox/histórico durável por destinatário autorizado, conteúdo separado por público e PHI. Registro de publicação Kafka não é entrega de comunicação; `202` não é execução. Rechecagem de vínculo também após tarefa/caso encerrado |

**Mensagem documental.** Reutilizar `msg.auth.docs_received` em `ICE_DocsRecebidos`,
sem aceitar nome de mensagem ou business key do browser. `request_ref` identifica uma
ocorrência durável de `ST_SolicitarDocumentos`, ligada a tenant/caso/instância e à espera
exata, com revisão. Não basta correlacionar pela guia: a mesma instância pode voltar à
pendência. A execução precisa consumir uma única ocorrência aberta, revalidar anexos e
autoridade e atualizar `documentos_refs` + `documentacao_completa` a partir da verificação
documental, antes do retorno a `BRT_Admissibilidade`. Completude é fato produzido pela
política documental, nunca `true` enviado pelo solicitante. Mesmo comando repetido retorna
o mesmo recibo; mensagem atrasada, espera expirada/substituída ou corrida com timer não
consome uma espera posterior. Se a espera ainda não estiver criada, conservar o comando
admitido pendente de despacho/reconciliação, sem correlação ampla ou sucesso fabricado.

**Comunicados reais.** `ST_SolicitarDocumentos` hoje retorna metadados; seu worker não
entrega uma inbox. E04 deve persistir pedido/destinatários e entregar conteúdo autorizado,
com recuperação por ocorrência para não duplicar pendências em retry. `ST_PublishAuthPended`
continua dono do evento de domínio. `send_denial_notice` compõe o registro, não comprova
envio; E04 deve ligar sua evidência humana e conteúdo protegido à comunicação durável.
Não converter `auth.completed` em "negativa entregue" sem evidência de entrega. Reusar
publicador/bridge e gateway existentes; nenhum MCP novo ou credencial de agente é
necessário para dar autoridade a um humano. Falha de canal/autoridade é estado técnico
visível e recuperável, nunca decisão clínica adversa (ADR-0030/0049).

#### Matriz única de impacto da jornada AUTH

Versão de implantação numérica não existe no XML fonte: congelar definição/versão/digest
BPMN + DMN/formulário/worker/adaptador compatíveis no catálogo de deployment (ADR-0049 D2),
sem usar `latest` como binding de tarefa viva. DMNs abaixo são as referências existentes;
`auth_auto_approval` permanece v0.2.0 DRAFT; as demais conservam seus próprios artefatos e
estado de ratificação. Não criar versões fictícias para esta extensão documental.

| Requisito | Processo/elemento → decisão | Produtor/tipo → consumidor; worker/erro | Evento/correlação; gateway/tool/política | API/form → estado por público → recibo / evidência exigida |
|---|---|---|---|---|
| Entrada única autorizada | AUTH `Start_SolicitacaoRecebida` → `BRT_Admissibilidade` / `auth_admissibility` | Adaptador resolve strings/referências e booleanos verificados → engine; `operadora.events.publish` | `agents.events.auth.received`; start tipado via gateway, política de start/dedup existente com admissão concorrente E04 | `/intakes/auth` → prestador: envio em processamento; beneficiário: caso apenas após start; staff: caso roteado → comando/recibo nativo; provar reenvio, conflito e isolamento |
| Instrução e completude documental | AUTH `ST_SolicitarDocumentos`, `GW_AguardarDocs`, `ICE_DocsRecebidos` → `auth_admissibility` | Custódia/política produz refs JSON e booleano de completude → reavaliação; `operadora.auth.request_documents` | `auth.pended`, `msg.auth.docs_received`; gateway humano de correlação, ocorrência/revisão exatas | uploads/requests/responses → públicos autorizados veem pedido, envio e processamento; staff vê evidência → recibo de correlação; provar caso errado, quarentena, reentrega, espera repetida e timer concorrente |
| Expiração sem negativa automática | AUTH `ICE_PrazoPendencia` → `UT_DecidirPendenciaExpirada` | Engine produz prazo P5D DRAFT; humano produz `decisao_pendencia` → `GW_PendenciaExpirada` | Timer BPMN; `auth.completed` somente no cancelamento humano; gateway de decisão, L0 | Formulário da tarefa exata → staff decide; externos veem estado permitido → recibo da decisão + resultado executado; provar timeout não nega |
| Avaliação técnica/financeira/regulatória/contratual | AUTH `BRT_SlaAnalise` / `auth_sla` → `ST_ValidateAutoApprovalCriteria` → `BRT_AutoApproval` / `auth_auto_approval` | `validate_auto_criteria` computa booleanos, tokens e prova de execução; DUT/ROL, carência, teto e critério contratual → gateway de rota | Sem comando browser para semear critérios; DMN e matriz de autonomia existentes | Read-only no dossier staff; externos só fase permitida → evidência por critério; provar fonte não ratificada/ausente leva à análise humana |
| Análise humana e posse | AUTH `ST_PrepararDossie` → `UT_AnaliseMedicoAuditor`, `UT_CoordenacaoAssume`, `UT_RegistrarParecerJunta` | `analyze_request`/Rafael produz dossiê instrutivo; humano produz decisão e proveniência → emissão/negativa; `convene_junta` | A2A existente só instrui; timers `BT_AlertaSla`/`BT_SlaAnalise`, `auth.sla_breached`; HumanGateway enforcing | assignments + formulários existentes → staff: responsável/revisão e ação; externos: em análise/junta quando autorizado → recibo exato; provar claim simultâneo, revogação e takeover versus decisão |
| Resultado e entrega protegida | AUTH `ST_EmitirAutorizacaoAuto`/`ST_EmitirAutorizacaoAuditor`, `ST_EnviarNegativaFormal` | `issue_authorization`, `send_denial_notice`; `ERR_AUTH_DENIAL_INCOMPLETE` captura em `BE_NegativaIncompleta`; guard `ERR_DENIAL_NOT_HUMAN` não ganha boundary por esta emenda | `auth.completed` com desfechos existentes; sem AUTH→PAGTO; outbox/bridge + custódia PHI | Caso/comunicações/recibo → cada público vê apenas resultado e documento autorizado; distinguir decisão executada de comunicado entregue → prova real de emissão/registro e inbox; guard bloqueado não vira negativa entregue |

#### Fatos AUTH e dependências de publicação

Nomes de processo abaixo não são novos campos AMH. E02 deve casar cada fato com a publicação
canônica atual e seu pin imutável (ADR-0037), registrar provenance/revisão/freshness e
qualificar a fonte antes de habilitar o consumidor. Ausência não vira booleano favorável.

| Fato / tipo | Finalidade e autoridade | Fronteira atual e trabalho necessário |
|---|---|---|
| Pessoa autenticada + vínculo sujeito/prestador (referências, escopo, validade, revisão/revogação) | OIDC humano Maezo autentica; vínculo autoritativo publicado autoriza operação | Nenhuma afiliação clínica/CNPJ/e-mail comprova representação. E02 qualifica contrato produtor e consumidor; falta de fonte implica acesso externo indisponível |
| `beneficiario_pseudo_id`, `prestador_id`, tenant (strings internas) | Adaptador resolve referências autorizadas preservando limite tenant/legal entity; AMH reconcilia Tasy payer/hospital | Usar APIs/eventos canônicos pinados; não copiar schemas, consultar Tasy/Oracle ou derivar identidade de linha analítica |
| `numero_guia_tiss`, procedimento/categoria/caráter, valor, CID protegido | Solicitação do prestador é alegação, com provenance; valor depende de tabela/faturamento qualificados | E04 valida forma/custódia; E02 qualifica fatos. Valor do browser não comprova critério financeiro; conversão de centavos à variável legada `valor_estimado_brl` exige ponte exata e validada, sem float/Number silencioso |
| `documentos_refs: json`, `documentacao_completa: boolean` | Custódia + política documental do procedimento/tenant | Upload finalizado não prova todos os documentos presentes; produtor de política/lista faltante deve ser qualificado. `missing_docs` lido pelo worker hoje não possui produtor contratual completo; E04 não inventa uma lista universal |
| `requer_autorizacao`, `beneficiario_ativo`, `carencia_cumprida: boolean` | Catálogo de cobertura do payer, cadastro e carência autoritativos → admissibilidade | Seeds legados permanecem gap; E02 mapeia publicação/proveniência atual e E04 impede escrita desses fatos pelo browser |
| `tipo_procedimento: string`, `dias_desde_adesao: integer`, `cpt_declarada: boolean` | Regra ratificada de mapeamento + cadastro/DPS do payer → `carencia_check` | Mapeamento regulatório SME e publicação AMH ainda devem ser qualificados; não derivar categoria por semelhança textual |
| DUT/ROL e parâmetros clínicos; teto monetário; regra contratual | Fontes existentes do validador e respectivos ratificadores | E02 entrega fatos clínicos read-only autorizados; não ratifica tabelas, cria DUT nem muda teto. `rede_credenciada` continua fato sem consumidor de critério definido |
| Decisão, `auditor_id`, justificativa e evidências | Tarefa humana exata + principal autenticado + snapshot PHI | E03 mantém binding/revisão/assinatura; E05 apresenta formulário e confirmação; não promover dossiê Rafael a decisão |

**Lacunas comportamentais que esta emenda não resolve por inferência.** No BPMN atual,
`conceder_prazo_extra` sai de `GW_PendenciaExpirada` para `BRT_SlaAnalise`, assim como
`seguir_analise`: não rearma a espera documental. O portal não deve prometer novo prazo
por esse literal; corrigir a semântica exige decisão do dono do contrato/SME e pacote
BPMN/DMN/form/worker compatível. Também não existe caminho contratado de desistência livre
do prestador/beneficiário em AUTH: não expor botão que force `cancelada_pendencia` nem
reutilizar SP-OP-CANCEL (cancelamento de contrato). O cancelamento existente é decisão
humana da pendência expirada. Nenhum prazo regulatório foi criado ou ratificado aqui.

**Compatibilidade e aceitação.** A extensão E01 não muda IDs, mensagens, timers, decisões,
binding nem semântica executável. Novos produtores E02–E04 precisam ser revistos com seus
consumidores E05 e aceitos em CIB/PG real + browser sintético antes de habilitar o slice.
Inventariar instâncias/esperas/bindings ativos antes de implantação; migração/rollback de
instância exige pacote separado. Testes estáticos não provam entrega nem autorização viva.

## Variaveis de entrada

| Variavel | Tipo | Obrigatoria | Descricao |
|---|---|---|---|
| `tenant_id` | string | sim | Tenant |
| `numero_guia_tiss` | string | sim | Numero da guia TISS (chave de negocio) |
| `beneficiario_pseudo_id` | string | sim | Pseudonimo (ADR-0006) |
| `prestador_id` | string | sim | Prestador solicitante |
| `codigo_procedimento_tuss` | string | sim | Procedimento TUSS |
| `categoria_procedimento` | string | sim | `consulta` \| `exame_simples` \| `exame_especial` \| `terapia` \| `internacao` \| `opme` \| `alta_complexidade` |
| `carater_atendimento` | string | sim | `urgencia` \| `eletivo` |
| `valor_estimado_brl` | number | sim | Valor estimado. Consumido por `ST_ValidateAutoApprovalCriteria` (criterio FINANCEIRO — derivacao ESTRITA em centavos: bool/NaN/inf/negativo/nao-numerico => `FINANCEIRO_VALOR_INVALIDO`), por `AnalyzeRequestWorker` (`dentro_teto_l2` na perna humana) e pelo guard de teto de `issue_authorization` no canal AUTOMATICO. **O VALOR em si continua semeado no start e nao verificado** — a fonte confiavel e' o faturamento/tabela do prestador, ainda ausente (residuo declarado no bloco GAP-AUTH-4) |
| `cid10` | string | nao | CID-10 informado |
| `documentos_refs` | json | sim | Referencias de anexos TISS (pode ser vazio) |
| `requer_autorizacao` | boolean | sim | Pre-resolvido por worker (catalogo do tenant) |
| `documentacao_completa` | boolean | sim | Pre-resolvido por worker |
| `beneficiario_ativo` | boolean | sim | Pre-resolvido por worker (cadastro) |
| `carencia_cumprida` | boolean | sim | Pre-resolvido por worker (contagem de carencia) |
| `dut_atendida` | boolean | nao | **NAO E MAIS LIDO por `auth_auto_approval`** (v0.2.0). O fato tecnico agora e COMPUTADO por `ST_ValidateAutoApprovalCriteria` (DMN `dut_rol_coverage` + a `dut_criteria_*` do procedimento) e publicado como `criterio_tecnico_ok`. Um valor semeado aqui nao influencia a rota automatica |
| `dentro_teto_l2` | boolean | nao | **NAO E MAIS LIDO por `auth_auto_approval`** (v0.2.0). O teto agora e COMPUTADO por `ST_ValidateAutoApprovalCriteria` (`CeilingResolver.within_l2_ceiling` sobre `authorization_approval.max_value_brl`) e publicado como `criterio_financeiro_ok`; `issue_authorization` mantem a verificacao no ponto de emissao, em defesa-em-profundidade |
| `rede_credenciada` | boolean | nao | **NAO E MAIS LIDO por `auth_auto_approval`** (v0.2.0). Nenhum criterio o consome hoje: rede credenciada NAO esta coberta por nenhuma das quatro fontes de regra — ver o bloco GAP-AUTH-4 abaixo (residuo declarado) |
| `tipo_procedimento` | string | nao | Input de `carencia_check` (`urgencia_emergencia` \| `parto` \| `eletivo` \| `alta_complexidade` \| `outros`). **Sem fonte hoje**: a derivacao a partir de `carater_atendimento`/`categoria_procedimento` e um mapeamento REGULATORIO pendente de SME (docs/review-queue.md) e nao foi inventada. Ausente => `criterio_regulatorio_ok=false` / `REGULATORIO_ENTRADA_AUSENTE` |
| `dias_desde_adesao` | integer | nao | Input de `carencia_check`. **Sem fonte hoje**: exige dado cadastral da fronteira AMH (MZO-050b, bloqueado). Ausente/negativo/nao-inteiro => `REGULATORIO_ENTRADA_AUSENTE` |
| `cpt_declarada` | boolean | nao | Input de `carencia_check` (Cobertura Parcial Temporaria). **Sem fonte hoje** (DPS — mesma fronteira). Ausente/nao-booleano => `REGULATORIO_ENTRADA_AUSENTE` |

> **GAP-AUTH-4 — PORTAO DE CRITERIOS (fechamento ESTRUTURAL, 2026-08-06).** Os quatro fatos que a
> rota automatica consome deixaram de ser semeados: `ST_ValidateAutoApprovalCriteria`
> (`operadora.auth.validate_auto_criteria`, worker DETERMINISTICO — nunca LLM) roda entre
> `BRT_SlaAnalise` e `BRT_AutoApproval` — o precedente `BRT_Calculo -> ST_CalculateAmount ->
> BRT_AutoApproval` de SP-OP-REEMBOLSO-001 (GAP-REEMBOLSO-5) — e COMPUTA:
>
> | Criterio | Fonte da regra | Estado |
> |---|---|---|
> | `criterio_tecnico_ok` | DMN `dut_rol_coverage` + a `dut_criteria_*` do procedimento | tabelas SINTETICAS, **nao ratificadas** |
> | `criterio_financeiro_ok` | `CeilingResolver.within_l2_ceiling` (`authorization_approval.max_value_brl`) | fonte REAL e ratificada; **valor = 0 (D-07 em aberto)** |
> | `criterio_regulatorio_ok` | DMN `carencia_check` | tabela SINTETICA, **nao ratificada**; inputs sem fonte (AMH/MZO-050b) |
> | `criterio_contratual_ok` | DMN `auth_criteria_contratual` | costura VAZIA (catch-all `SEM_REGRA_RATIFICADA`), **nao ratificada** |
>
> O worker escreve tambem `auto_criteria_verificado=true` — PROVA DE EXECUCAO que a regra r1 de
> `auth_auto_approval` v0.2.0 exige. **Essa exigencia e a cerca que faltava:** pular o validador faz
> o token cair no catch-all -> `ANALISE_HUMANA`. Os quatro criterios sao reescritos em TODO caminho
> (inclusive na degradacao), entao nenhum homonimo semeado no start sobrevive ate a BRT.
>
> **PORTAO DE RATIFICACAO (o ponto de seguranca).** As tabelas clinicas/regulatorias/contratuais sao
> SINTETICAS. Um criterio so pode contribuir com um PASS se sua fonte estiver RATIFICADA em
> `spec/processes/dmn/auth-criteria-ratification.yaml` (`ratificado: true` + `revisor` +
> `ratificado_em`, os tres — responsabilizacao ADR-0007). Fonte DRAFT devolve `false` com
> `*_FONTE_NAO_RATIFICADA` **independentemente do que a tabela computou**. Espelha o loader da
> matriz de retencao do DPO, que recusa o proprio template nao ratificado. Ratificar e' mudanca de
> DADOS: nenhuma linha de codigo muda. Em modo SOMBRA (`auto_criteria_shadow`) o validador registra
> o que a tabela DRAFT TERIA decidido, para o revisor medico/ANS ratificar contra dados reais.
>
> **DESFECHO OBSERVAVEL HOJE, sem exagero.** Com o teto em 0 e nenhuma fonte ratificada, os quatro
> criterios sao false: **NADA auto-aprova; todo pedido vai para analise humana.** E' o MESMO desfecho
> seguro de antes — agora por quatro motivos explicitos, per-criterio e auditaveis
> (`auto_criteria_falhas` no historico do engine; `motivo_bloqueio_criterios` e os quatro booleanos
> na cadeia duravel ADR-0007) em vez de um fato semeado nao verificado. Cada criterio passa a valer
> SOZINHO conforme sua fonte for ratificada/populada, sem mudanca de codigo.
>
> **O QUE PERMANECE ABERTO (agora e' DADO e ESCOPO, nao estrutura) — portao Medico/ANS + financeiro:**
> 1. **Ratificacao** das 5 tabelas DUT/carencia e populacao da tabela contratual (medico auditor,
>    juridico/regulatorio, financas). Nenhuma regra clinica, regulatoria ou contratual foi inventada.
> 2. **D-07**: `authorization_approval.max_value_brl` continua 0 (diretoria).
> 3. **`rede_credenciada` nao e coberto por nenhum criterio.** Nao existe fonte de rede credenciada
>    no repo; inventar uma seria fabricar um fato. Enquanto isso ele simplesmente nao participa da
>    decisao automatica (o que e' fail-closed: menos caminhos para auto-aprovar, nunca mais).
> 4. **Inputs de `carencia_check`** (`tipo_procedimento`, `dias_desde_adesao`, `cpt_declarada`) nao
>    existem no contrato de start: dado cadastral da fronteira AMH (MZO-050b, bloqueado) + um
>    mapeamento regulatorio pendente de SME. O criterio regulatorio falha FECHADO com
>    `REGULATORIO_ENTRADA_AUSENTE` — desfecho correto, nao um stub.
> 5. **Seeds de admissibilidade** (`requer_autorizacao`, `beneficiario_ativo`,
>    `documentacao_completa`) continuam nao verificados a montante (`auth_admissibility`).
> 6. **`carater_atendimento` e' don't-care na regra r1**: uma urgencia auto-aprova sob exatamente os
>    mesmos fatos de um eletivo. Input MANTIDO na DMN de proposito, para o SME diferenciar (RN 259)
>    sem mudanca de codigo. **Pergunta aberta ao portao Medico/ANS.**
>
> **A promocao deste contrato de DRAFT para FINAL continua vinculada aos itens 1-2 acima.**
>
> **Mitigacao anterior, mantida em defesa-em-profundidade.** `operadora.auth.issue_authorization`
> segue verificando `authorization_approval.max_value_brl` **antes de emitir, exclusivamente no canal
> AUTOMATICO** (`ERR_AUTH_AUTO_CEILING_NOT_AUTHORIZED`, RETORNADO — nunca lancado), fail-closed em
> tenant/valor/resolver. **O canal humano NAO e afetado:** `decisao_auditor == 'APROVAR'` (e a rota da
> junta) emite independentemente do teto — exceder o teto AUTOMATICO e' exatamente para o que a
> analise humana existe.

## Variaveis de saida

| Variavel | Tipo | Descricao |
|---|---|---|
| `decisao_auditor` | string | `APROVAR` \| `NEGAR` \| `SOLICITAR_INFO` \| `JUNTA_MEDICA` (User Tasks humanas) |
| `justificativa_clinica` | string | Obrigatoria se NEGAR |
| `cid10_referencia` | string | Obrigatoria se NEGAR |
| `fundamentacao_dut` | string | Obrigatoria se NEGAR |
| `auditor_id` | string | Id do medico auditor humano que setou `decisao_auditor` (cadeia de auditoria ADR-0007). **Obrigatoria se NEGAR** — e a proveniencia humana que o guard de `operadora.auth.send_denial_notice` consome (`ERR_DENIAL_NOT_HUMAN`). Declarada como `camunda:formField` nas tres User Tasks humanas (`UT_AnaliseMedicoAuditor`, `UT_CoordenacaoAssume`, `UT_RegistrarParecerJunta`); em `UT_RegistrarParecerJunta` e o medico relator do parecer. Espelha `analista_id`/`auditor_id` de SP-OP-RECURSO-001 e SP-OP-REEMBOLSO-001 |
| `decisao_pendencia` | string | `cancelar_guia` \| `conceder_prazo_extra` \| `seguir_analise` (pendencia expirada — humano) |
| `numero_autorizacao` | string | Emitida por `operadora.auth.issue_authorization`. **Ausente** quando o guard de teto recusa a emissao automatica (ver `ERR_AUTH_AUTO_CEILING_NOT_AUTHORIZED`) |
| `dentro_teto_l2` | boolean | Fato COMPUTADO escrito de volta pelos workers que o resolvem: `analyze_request` (sempre) e `issue_authorization` **so no canal automatico** (nunca na perna humana — la o teto nao e' consultado e escreve-lo seria fabricar uma verificacao) |
| `motivo_bloqueio_teto` | string | Token limitado (`TENANT_AUSENTE` \| `VALOR_AUSENTE_OU_INVALIDO` \| `TETO_NAO_AUTORIZA` \| `RESOLVER_INDISPONIVEL`), escrito SO na recusa de teto de `issue_authorization` — evidencia engine-visivel de QUAL vetor fail-closed disparou |
| `criterio_tecnico_ok` | boolean | Fato COMPUTADO por `validate_auto_criteria` (DUT/ROL). Sobrescreve qualquer homonimo semeado |
| `criterio_financeiro_ok` | boolean | Fato COMPUTADO por `validate_auto_criteria` (teto do tenant) |
| `criterio_regulatorio_ok` | boolean | Fato COMPUTADO por `validate_auto_criteria` (carencia/CPT) |
| `criterio_contratual_ok` | boolean | Fato COMPUTADO por `validate_auto_criteria` (milestones/regras/KPI) |
| `auto_criteria_verificado` | boolean | **PROVA DE EXECUCAO** do validador — `true` em TODO caminho, inclusive na degradacao. Atesta "o validador rodou", nunca "os criterios passaram". Exigido `true` pela regra r1 de `auth_auto_approval` |
| `auto_criteria_falhas` | json | Lista de tokens limitados nao-PHI (enum fechado): `TECNICO_FONTE_NAO_RATIFICADA` \| `TECNICO_ENTRADA_AUSENTE` \| `TECNICO_TABELA_INDISPONIVEL` \| `TECNICO_PROCEDIMENTO_NAO_MAPEADO` \| `TECNICO_FORA_DO_ROL` \| `TECNICO_DUT_NAO_ATENDIDA` \| `FINANCEIRO_TENANT_AUSENTE` \| `FINANCEIRO_VALOR_INVALIDO` \| `FINANCEIRO_RESOLVER_INDISPONIVEL` \| `FINANCEIRO_TETO_NAO_AUTORIZA` \| `REGULATORIO_FONTE_NAO_RATIFICADA` \| `REGULATORIO_ENTRADA_AUSENTE` \| `REGULATORIO_TABELA_INDISPONIVEL` \| `REGULATORIO_CARENCIA_NAO_CUMPRIDA` \| `CONTRATUAL_FONTE_NAO_RATIFICADA` \| `CONTRATUAL_TABELA_INDISPONIVEL` \| `CONTRATUAL_SEM_FONTE` \| `VALIDADOR_INDISPONIVEL` |
| `auto_criteria_shadow` | json | MODO SOMBRA: o que cada tabela DRAFT TERIA decidido — `{TECNICO,REGULATORIO,CONTRATUAL}_SOMBRA_{APROVARIA,REPROVARIA,INDETERMINADO}`. Evidencia de ratificacao para o revisor medico/ANS. **Nunca influencia o veredito** |
| `motivo_bloqueio_criterios` | string | O PRIMEIRO token de `auto_criteria_falhas` na ordem tecnico->financeiro->regulatorio->contratual (`""` quando tudo passou). Token unico e limitado porque a cadeia ADR-0007 nao aceita listas — mesmo idioma de `motivo_bloqueio_teto` |

## Variaveis de proveniencia do agente (Rafael — ADR-0007/ADR-0015)

Convencao repo-wide de nao-repudio (ADR-0007) e delegacao A2A (ADR-0015) — nao especifica de AUTH
(mirror `source_agent_id`/`source_agent_version` de `docs/processes/contracts/SP-OP-ESCALATION-001.md`).
Semeadas por `RafaelGraph._contract_variables` (`src/maezo/agents/rafael/graph.py`) junto com as
variaveis de entrada; NENHUMA delas e uma decisao de cobertura — so proveniencia, dossie instrutivo
e roteamento humano (CC-13 — Agent Fleet Audit: antes deste registro, `_contract_variables` as
emitia sem declaracao no contrato).

| Variavel | Tipo | Obrigatoria | Descricao |
|---|---|---|---|
| `source_agent_id` | string | nao | Agente que preparou o dossie de auditoria medica (`rafael`) — cadeia de nao-repudio (ADR-0007) |
| `source_agent_version` | string | nao | Versao do agente Rafael que preparou o dossie (auditoria ADR-0007) |
| `dossie_rafael` | json | nao | Dossie factual de admissibilidade/cobertura montado por Rafael — instrui `UT_AnaliseMedicoAuditor`/`UT_RegistrarParecerJunta`; carrega `decisao_cobertura` sempre `None` (Rafael NUNCA decide a cobertura) |
| `rafael_route` | string | nao | Roteamento do grafo do Rafael (`auto_approve` \| `human_auditor`) — espelha, nao decide, o roteamento do processo |
| `motivo_encaminhamento` | string | nao | Presente so quando `rafael_route=human_auditor`; motivo do encaminhamento a auditoria medica (`dmn_analise_humana` \| `documentacao_pendente` \| `dmn_indisponivel` \| `outro`) |
| `dmn_decision_refs` | json | nao | Referencias auditaveis (tabela→regra) das DMN que Rafael consultou (`auth_admissibility`/`auth_auto_approval`/`auth_sla`) — cadeia de decisao (ADR-0007/ADR-0012) |

## Topicos

| Tipo | Topico | Sentido | Quando |
|---|---|---|---|
| Kafka | `agents.events.auth.received` | produz | apos start |
| Kafka | `agents.events.auth.pended` | produz | pendencia de documentacao aberta |
| Kafka | `agents.events.auth.sla_breached` | produz | SLA de analise estourado |
| Kafka | `agents.events.auth.completed` | produz | fim (payload.desfecho = `aprovada_automatica` \| `aprovada_auditor` \| `negada_auditor` \| `nao_requer_autorizacao` \| `cancelada_pendencia`) |
| External task | `operadora.events.publish` | consome | publicador generico |
| External task | `operadora.auth.validate_auto_criteria` | consome | **portao de criterios (GAP-AUTH-4)**: computa tecnico/financeiro/regulatorio/contratual ANTES de `BRT_AutoApproval` |
| External task | `operadora.auth.analyze_request` | consome | convoca Rafael: dossie de analise (ja registrado) |
| External task | `operadora.auth.request_documents` | consome | pendencia ao prestador |
| External task | `operadora.auth.issue_authorization` | consome | emite autorizacao (TISS) |
| External task | `operadora.auth.send_denial_notice` | consome (worker) | worker `SendDenialNoticeWorker` (`ST_EnviarNegativaFormal`): **COMPOE** o registro da negativa formal por escrito em nome do auditor humano e o devolve com o conteudo clinico REDIGIDO (ADR-0006). **NAO afirma mais `status=notice_sent`** (AUTH-SEND-DENIAL-NOTICE-STATUS-LITERAL): o worker e sincrono (`WorkerBase.execute`), sem seam de Kafka e sem canal nenhum — nada e transmitido ao beneficiario/prestador nesta etapa; o canal seguro real e de Fase 1. O que sai e real: `notice_type`, `error_code=None`, a proveniencia `human_approved` resolvida pelo GUARD 2 e os tres campos clinicos redigidos. O unico `status` que este worker escreve e o registro de recusa `blocked_by_guard` (+ `ERR_DENIAL_NOT_HUMAN`). O fato `agents.events.auth.completed` (desfecho `negada_auditor`) e publicado adiante por `ST_PublishNegada`, no unico fluxo de saida da task (`Flow_Negativa_Pub`) |
| External task | `operadora.auth.notify_sla_risk` | consome (worker) | worker `NotifySlaRiskWorker`: registra que a ETAPA de alerta de risco de SLA a `coordenacao-auditoria-medica` rodou (timer nao-interruptivo `BT_AlertaSla`) e retorna `{}` — **NAO afirma `status=risk_notified` NEM o evento `agents.events.auth.sla_breached`** e nao contata canal algum (worker sincrono, sem seam de Kafka). Aquele evento e publicado por `ST_PublishSlaBreach`, no ramo do boundary INTERRUPTIVO `BT_SlaAnalise` — outro ramo (FAB-SLA-RISK-NOTIFIED-SLICE4). Informativo e nunca adverso: `UT_AnaliseMedicoAuditor` segue aberta |
| External task | `operadora.auth.convene_junta` | consome | convoca junta medica (RN 424 — DRAFT) |
| Message BPMN | `msg.auth.docs_received` | recebe | correlacao por business key, destrava pendencia |

## DMN referenciadas

### `auth_admissibility` (FIRST — DRAFT)
in: `requer_autorizacao: boolean`, `documentacao_completa: boolean`, `beneficiario_ativo: boolean`, `carencia_cumprida: boolean`
out: `resultado: string` (`NAO_REQUER` | `PENDENTE_DOCUMENTACAO` | `SEGUE_ANALISE`), `motivo: string`
Sem saida de negativa por design.

### `auth_auto_approval` (FIRST — DRAFT, v0.2.0)
in: `auto_criteria_verificado: boolean`, `criterio_tecnico_ok: boolean`, `criterio_financeiro_ok: boolean`, `criterio_regulatorio_ok: boolean`, `criterio_contratual_ok: boolean`, `carater_atendimento: string`
out: `recomendacao: string` (`AUTO_APROVAR` | `ANALISE_HUMANA`), `motivo: string`
r1 (unica favoravel) exige os CINCO booleanos `true`; catch-all r99 = `ANALISE_HUMANA`.
Todos os inputs booleanos sao COMPUTADOS por `operadora.auth.validate_auto_criteria`; nenhum vem
do payload de start. `carater_atendimento` e' don't-care em r1 — mantido para o SME diferenciar
urgencia sem mudanca de codigo (ADR-0012).

### Tabelas avaliadas pelo WORKER `validate_auto_criteria` (costura `dmn=`, ADR-0028 — nao sao `businessRuleTask`)

Todas DRAFT/sinteticas e **nao ratificadas** em `spec/processes/dmn/auth-criteria-ratification.yaml`;
enquanto assim, contribuem apenas com evidencia de SOMBRA, nunca com um PASS. Nenhuma possui saida
de negativa (L0 hard).

- `dut_rol_coverage` — in: `codigo_procedimento_tuss`, `categoria_procedimento`; out: `no_rol`, `requer_dut`, `dut_ref`.
- `dut_criteria_bariatrica` / `dut_criteria_oncologia_pet_ct` / `dut_criteria_terapias_especiais` — selecionadas pelo `dut_ref` atraves do `mapeamento_dut_criteria` do manifesto (declarado por SME, deliberadamente INCOMPLETO: um `dut_ref` sem mapeamento resolve `TECNICO_PROCEDIMENTO_NAO_MAPEADO`).
- `carencia_check` — in: `tipo_procedimento`, `dias_desde_adesao`, `cpt_declarada`; out: `carencia_cumprida`, `prazo_restante_dias`, `fonte`.
- `auth_criteria_contratual` (NOVA) — in: `tenant_id`, `categoria_procedimento`; out: `criterio_contratual_ok`, `motivo`. **Costura VAZIA**: uma unica regra catch-all devolvendo `false` / `SEM_REGRA_RATIFICADA`. Nenhuma regra contratual/milestone/KPI foi inventada.

### `auth_sla` (FIRST — DRAFT, todos os prazos DRAFT/verify)
in: `carater_atendimento: string`, `categoria_procedimento: string`
out: `sla_analise: string (ISO)`, `sla_alerta: string (ISO)`, `fonte_regulatoria: string`

## Papeis humanos

| Grupo | Tarefa |
|---|---|
| `medico-auditor` | `UT_AnaliseMedicoAuditor` (negativa SO aqui), `UT_DecidirPendenciaExpirada` |
| `coordenacao-auditoria-medica` | `UT_CoordenacaoAssume` (SLA estourado) |
| `junta-medica` | `UT_RegistrarParecerJunta` (RN 424 — DRAFT) |

> **PROPOSTO — confirmar contra a taxonomia organizacional da operadora** (ver
> `docs/review-queue.md`; mesmo padrao de R-034 / gap `PERSP-ESCALATION-VOCAB-a`, aplicado aqui
> como follow-up mecanico — gap `AUTH-LGPD-CONTRACTS-NO-PROPOSTO-CAVEAT`). Os grupos
> `medico-auditor`, `coordenacao-auditoria-medica` e `junta-medica` sao candidatos DRAFT e podem
> nao corresponder aos grupos reais do IdP/console de User Tasks da operadora. A tabela
> consolidada de `grupo declarado -> arquivo:linha -> processo -> SLA/ato` para esta sessao de
> nomeacao esta em `docs/sme-dispatch/po/ORG-TAXONOMY-TABLE.md` (linhas 28-30). Nenhum rename e
> aplicado sem os nomes reais do dono organizacional da operadora.

## SLAs

| Timer | Valor | Tipo | Fonte |
|---|---|---|---|
| Analise (urgencia) | PT2H | interruptivo -> coordenacao assume | Lei 9.656 art. 35-C ("imediato") — **DRAFT/verify** |
| Analise (eletivo alta complexidade/OPME/internacao) | P10D | idem | RN 259 (21 dias uteis garantia) — **DRAFT/verify** |
| Analise (eletivo padrao) | P5D | idem | RN 395/2016 (5 dias uteis) — **DRAFT/verify** |
| Alerta de risco | 50–70% do SLA (DMN `sla_alerta`) | nao-interruptivo -> notify_sla_risk | politica interna |
| Pendencia de documentacao | P5D | event gateway -> `UT_DecidirPendenciaExpirada` (humano decide destino) | **DRAFT/verify** (suspensao de prazo durante pendencia: confirmar regra RN) |
| Negativa por escrito | registro COMPOSTO pelo worker `send_denial_notice`; a TRANSMISSAO em si nao tem canal implementado (Fase 1) | — | RN 395 art. 10 (24h) — **DRAFT/verify** |

Nota: prazos legais sao em dias uteis; ISO 8601 usa dias corridos — valores conservadores. Resolver calendario util no worker.

## Desfecho de agente: falha de start (CC-01)

| Desfecho | Onde vive | Quem escreve | Significado |
|---|---|---|---|
| `erro_inicio_processo` | **estado do agente rafael** — NAO e variavel de processo | no `notify_start_failure` do grafo, via o helper unico `maezo.runtime.start_outcome.notify_start_failure` | o agente TENTOU iniciar SP-OP-AUTH-001 pelo chokepoint `start_process_idempotent` e o engine recusou (`CibSevenError`). NENHUMA instancia nasceu |

ONDE ESTE VALOR **NAO** ESTA, e por que. Ele nunca chega ao engine: nao consta de
`## Variaveis de entrada` nem de `## Variaveis de saida`, nao tem `bpmnError` associado, nao
aparece em nenhum `camunda:` do BPMN e **nao exige mudanca nenhuma no BPMN deste processo**. Nao
poderia ser diferente — o processo NAO nasceu, entao nao existe instancia onde gravar uma
variavel nem escopo onde lancar um erro. Ele e declarado AQUI, no contrato, porque e um desfecho
CONTRATUAL do agente que serve este processo e porque quem consome o estado do agente (o handler
A2A, um golden de eval, uma regra de alerta) precisa do literal exato e estavel.

POR QUE ELE EXISTE (auditoria de frota 2026-09-04, achado CC-01). Ate essa data a falha de start
era engolida: o `except CibSevenError` do no de start devolvia apenas `process_started=false` e a
aresta seguinte era INCONDICIONAL para um terminal no-op, de modo que o desfecho de SUCESSO ja
gravado a montante (`encaminhado_auditor`) sobrevivia — o estado do agente AFIRMAVA um fato que nao
aconteceu. E o caso se perdia em silencio, porque os prazos desta especificacao vivem em timers
da instancia BPMN que nunca nasceu: sem SLA, sem alerta, sem retry.

EFEITOS ASSOCIADOS ao desfecho, todos no lado do agente:

* `process_started = false` (e, quando o agente distingue no-ops legitimos, o marcador
  `start_failed = true`, que e o que a aresta condicional le);
* `record_agent_error()` -> `maezo_agent_errors_total`, o contador que a regra
  `MaezoAgentCrashLoop` observa;
* evento estruturado `agent_process_start_failed` com a business key idempotente deste contrato —
  este evento e o SUBSTITUTO operacional do prazo enquanto a instancia nao existe;
* RETRY seguro por construcao: `start_process_idempotent` e idempotente por business key, entao
  uma reentrega reencontra a instancia viva (`ALREADY_ACTIVE`) em vez de abrir uma segunda.


## Codigos de erro

| Codigo | Onde | Tratamento |
|---|---|---|
| `ERR_AUTH_INVALID_GUIA` | declarado (`Error_AuthGuiaInvalida`) para uso dos workers | worker lanca BPMN error se guia inconsistente na origem; tratamento a detalhar na promocao a FINAL |
| `ERR_AUTH_DENIAL_INCOMPLETE` | guard do worker `operadora.auth.send_denial_notice` | recusa compor o registro de uma NEGAR sem `justificativa_clinica` + `cid10_referencia` + `fundamentacao_dut` (RN 395 art. 10). Lanca BPMN error (`Error_AuthDenialIncompleta`), capturado por `BE_NegativaIncompleta` -> `End_FundamentacaoIncompletaBloqueada` (terminal NEUTRO: nada foi enviado) |
| `ERR_DENIAL_NOT_HUMAN` | guard do worker `operadora.auth.send_denial_notice` | recusa compor o registro de uma NEGAR sem proveniencia humana: o sinal explicito `human_approved` **ou** um `auditor_id` nao-vazio setado pela User Task humana. Materializa a invariante L0: nenhuma negativa sem User Task humana na trilha (ADR-0007). NAO ha boundary modelado para este codigo — o worker retorna registro `blocked_by_guard`, nao lanca |
| `ERR_AUTH_AUTO_CEILING_NOT_AUTHORIZED` | guard do worker `operadora.auth.issue_authorization`, **canal AUTOMATICO apenas** (`ST_EmitirAutorizacaoAuto`) | recusa EMITIR quando o teto de autonomia do tenant (`authorization_approval.max_value_brl`, resolvido por `CeilingResolver.within_l2_ceiling`) nao autoriza o `valor_estimado_brl`. Fail-closed em todos os vetores (tenant ausente/branco/nao-string; valor ausente/nao-numerico/bool/negativo/nao-finito; resolver indisponivel). Distinto de `ERR_DENIAL_NOT_HUMAN` de proposito: aquele significa "sem sancao modelada", este significa "sancionado pela DMN, mas o teto nao autoriza emissao AUTOMATICA". **O canal HUMANO nunca e' gateado por este codigo** (`decisao_auditor == 'APROVAR'` e a rota da junta emitem independentemente do teto). NAO ha boundary modelado em `ST_EmitirAutorizacaoAuto` (o unico boundary do arquivo e' `BE_NegativaIncompleta`), entao o worker RETORNA registro `blocked_by_guard` com `dentro_teto_l2=false` + `motivo_bloqueio_teto` como evidencia engine-visivel — nunca lanca (um `bpmnError` nao modelado encerra silenciosamente o escopo, ADR-0030) |

## Pendencias para promocao a FINAL

DI (diagrama); confirmacao de todos os prazos RN com regulatorio; detalhamento do fluxo
de junta (prazos/desempate RN 424); anexos TISS obrigatorios por categoria; politica de
suspensao de prazo em pendencia.
