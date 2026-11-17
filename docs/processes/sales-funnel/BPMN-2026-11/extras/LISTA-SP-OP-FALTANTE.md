# LISTA-SP-OP-FALTANTE.md — Processos executáveis SP-OP-*: existentes × referenciados × faltantes

Missão §10 · Data: 2026-11-17 (America/Sao_Paulo) · Sem rede · Sem nomes de pessoa/empresa.
Fontes: `ls maezo-main/spec/processes/bpmn/` (17 arquivos .bpmn, conferido 2026-11-17); grep de `SP-OP-` em `diagrams/*.bpmn` (presença por diagrama, conferida 2026-11-17); `EPICS-PLAN.tsv`; `bundle/.../ADDENDUM-01/ADDENDUM-01.md` §5.3; `bundle/.../EXECUTION-LOG.md`; `bundle/.../RATIFY-LATER-REGISTER.md` (RLR); `TABELA-ETAPAS.md`; `REG-NOTAS/*.md`.

**Regra da casa (TABELA-ETAPAS.md:43, regra 10):** nível 2 aponta SP-OP-* EXISTENTE por call activity/anotação; **nunca criar executável** nesta entrega; faltantes são apenas PROPOSTA. A criação de cada processo novo segue o caminho canônico do programa (épico E-SF-nn no repo-casa → gates → incorporação pelo orquestrador), e a disposição final é do dono — cf. RL-SF-119 (RLR:127: os 8 flips element-level do MAPA-ELEMENTOS-V4 só se aplicam com "disposição do dono — adotado, rejeitado ou pendente"; "nenhum flip é aplicado sem disposição") e EXECUTION-LOG.md:84 (CD-07: "a emissão ao lago é de E-SF-35r", não de processo novo criado fora do plano).

---

## (a) Os 17 SP-OP-* EXISTENTES em `maezo-main/spec/processes/bpmn/`

| # | Processo (arquivo) | O que cobre (1 linha) | Referenciado nos diagramas? | Onde |
|---|---|---|---|---|
| 1 | SP-OP-ADEQUACAO-001 (Adequacao_Rede) | Adequação da rede credenciada | Não | — |
| 2 | SP-OP-ANS-CRON-001 (Agendador_Envios_ANS) | Agendador dos envios periódicos à ANS | Não | — |
| 3 | SP-OP-ANS-SUBMIT-001 (Envios_Periodicos_ANS) | Execução dos envios periódicos à ANS | Não | — |
| 4 | SP-OP-AUTH-001 (Autorizacao_Previa) | Autorização prévia assistencial (carência/CPT; termo de negativa) | **Sim** | E07 |
| 5 | SP-OP-CANCEL-001 (Cancelamento_Contrato) | Cancelamento/exclusão de contrato (e relação com o relógio pós-26º dia) | **Sim** | E06, E09 |
| 6 | SP-OP-CONTAS-001 (Processamento_Contas_Glosa) | Contas e glosa — fluxo do PRESTADOR (não cobre contestação de beneficiário) | **Sim** (citado como NÃO aplicável) | F04 (anotação: "SP-OP-CONTAS-001 é glosa de PRESTADOR — contestação de beneficiário sem SP-OP-*") |
| 7 | SP-OP-CRED-001 (Descredenciamento) | Descredenciamento de prestador | Não | — |
| 8 | SP-OP-ESCALATION-001 (Escalonamento_Humano_Universal) | Escalonamento humano universal (mensagem correlacionada por nome — GAP-PROC-05) | Não | — |
| 9 | SP-OP-FRAUDE-001 (Investigacao_Fraude) | Investigação de fraude/DLP | Não | — |
| 10 | SP-OP-INADIMPLENCIA-001 (Suspensao_Rescisao) | Suspensão/rescisão por inadimplência (art. 13 §ú II; notificação até o 50º dia) | Não | — |
| 11 | SP-OP-LGPD-DSR-001 (Direitos_do_Titular) | Direitos do titular (DSR) — citado, não duplicado nos diagramas | **Sim** (citado) | E04 (anotação Nível 2: "existe — citado, não duplicado") |
| 12 | SP-OP-NIP-001 (Resposta_NIP) | Resposta a NIP | Não | — |
| 13 | SP-OP-PAGTO-001 (Pagamentos_Alcada) | Pagamentos por alçada | **Sim** | E06 |
| 14 | SP-OP-PROGRAMA-001 (Programas_Cuidado) | Programas de cuidado (consentimento específico do convite — GTM-07.D1) | Não (citado no registro do portão) | — |
| 15 | SP-OP-RECURSO-001 (Recurso_Glosa) | Recurso de glosa | Não | — |
| 16 | SP-OP-REEMBOLSO-001 (Reembolso_Beneficiario) | Reembolso ao beneficiário (relógio legal de 30 d — PROC-06/RL-SF-121/122) | **Sim** | E09 |
| 17 | SP-OP-SUPP-001 (Direitos_de_Nao_Contato) | Não-contato/oposição (taxonomia C1–C6; SLA 15 d.u.; k-anon 100) | **Sim** | E09, F02, F04 |

**Resumo:** 7 referenciados nos diagramas (4, 5, 6, 11, 13, 16, 17); 10 não referenciados (1, 2, 3, 7, 8, 9, 10, 12, 14, 15). Nenhum diagrama cria executável (`isExecutable="false"` — regra 1, TABELA-ETAPAS.md:34).

---

## (b) PROCESSOS EXECUTÁVEIS FALTANTES (não existem em `spec/`) que o funil precisa

Nomes propostos na convenção `SP-OP-<DOMINIO>-NNN` — **proposta desta entrega, não criação**. A criação de cada um segue o caminho canônico do programa: épico E-SF-nn no repo-casa (gates, revisão independente, incorporação pelo orquestrador) — nunca por esta entrega documental (regra 10, TABELA-ETAPAS.md:43; disposição do dono conforme RL-SF-119/MAPA-ELEMENTOS-V4 — RLR:127; precedentes de processo criado por épico: "BPMN novo de arrependimento" no escopo PROC de E-SF-09, EXECUTION-LOG.md:64; "implantação de SP-OP-NOTIF-001" como OWNER-DEPLOY de épico, RLR:139).

| Nome proposto | O que cobriria | Por quê (lacuna) | Épico que o cria | Fonte |
|---|---|---|---|---|
| SP-OP-EXEC-COMERCIAL-001 | Execução comercial do funil na fonte: resolver de contexto, journal/vault de intake, `prospect.save`→`contract.issue`, braço coverage | O pool "Executor comercial e lago (amh-data-platform)" de todos os diagramas E01–E09 é caixa-preta sem processo próprio; a fábrica do CommercialService está rota-fechada em hml (RL-SF-125) | **E-SF-49** (amh-data-platform, Onda 2; executor comercial composto, resolver de contexto e JourneySource concreto) | EPICS-PLAN.tsv:84; TABELA-ETAPAS.md:7 (pool); RLR:133 |
| SP-OP-RELOGIO-001 | Relógio de contratação de 25 dias: relógio-sombra, alarmes, caminho do 26º dia ("nunca silente"), suspensões §§5º/7º | O SLA-mestre (RL-SF-038/NORM-GAP-8) é desenhado como timer/portão nos diagramas mas não tem processo executável; três filhos de E-SF-08 parados (CD-24) | **E-SF-07** (maezo-operadora, Onda 2; "sombra, alarmes, 26º dia e nunca silente") | EPICS-PLAN.tsv:11; REG-NOTAS/ETAPA-06.md §(3); TABELA-ETAPAS.md:19 |
| SP-OP-TRACKER-001 | Tracker de contratação de 7 estágios no portal: "sem ETA" como padrão, sincronização receipt-first, aviso de estouro (interno) | O rastreador de 7 estágios (E08/F01) é desenhado, mas a projeção `journey.get` do catálogo de prazos está "sem consumidor nesta onda" (DMN L23–24, orphans-allowlist/pending wiring) | **E-SF-42** (omni_sales_platform — casa OMNI, Onda 2; tracker de contratação e acompanhamento no portal) | EPICS-PLAN.tsv:71; DMN L23–24; EXECUTION-LOG.md:126; TABELA-ETAPAS.md:21 |
| SP-OP-DS-001 | Entrevista digital da Declaração de Saúde (E7): fases/attestation, navegação Carta→DS, banco aprovado, custódia clínica isolada, ponte CPT (fato "existe/não, escopo" por decisão humana) | O bloco DS (E07) não tem processo executável próprio; o modo real fica atrás de RL-SF-132 (OWNER-DEPLOY) e do trio RL-SF-023/024/017 | **E-SF-44** (superfície, omni_sales_platform — casa OMNI: TSV L75 "omni_sales_platform executável Onda 2") + perna maezo **E-SF-25/26/27/28** (entidade/banco, roteamento, custódia clínica, gate aceite/emissão — Onda 2, TSV L45/47/49/50) | EPICS-PLAN.tsv:75/45/47/49/50; REG-NOTAS/ETAPAS-07-08-09.md §e; RLR:140 |
| SP-OP-ARREP-001 | Arrependimento 7 d: formulário com declaração destacada, duas âncoras (`signed_at`/`vigencia_start`), timer P7D parametrizado, nenhuma negativa automática por intempestividade | O ramo de arrependimento (E09) é GAP: "arrependimento = GAP E-SF-09" (anotação do próprio E09) e E-SF-09 está "não iniciado" | **E-SF-09** (maezo-operadora, Onda 1; o escopo PROC do épico já prevê "BPMN novo de arrependimento") | EXECUTION-LOG.md:64/128; EPICS-PLAN.tsv:15; anotação E09 |
| SP-OP-NOTIF-001 | Notificação do funil: OutboundGate, registro de templates, catálogo de mensagens, roteamento por consentimento/oposição (default off) | Nome já reservado pelo programa; hoje o processo não existe em `spec/` e a implantação é portão OWNER-DEPLOY próprio (RL-SF-131) | **E-SF-13** (maezo-operadora, Onda 1; "OutboundGate, registro de templates, catálogo de mensagens e NOTIF-001"; PR 1274 aberto sem a migration 0036) | EPICS-PLAN.tsv:23; RLR:139; EXECUTION-LOG.md:130; FRENTES-01-04.md F2 §d |
| SP-OP-REAJUSTE-001 | Renovação/reajuste: jornada D−30/D0, quadro "antes → depois", catálogo `ret.*` com cerca anti-sinal-clínico, contestação | **F04 referencia este nome e ele NÃO existe** (anotação F04: "SP-OP-REAJUSTE-001 não existe — GAP (E-SF-RT-01; CD-07)"); nenhum emissor de evento de reajuste em processo | **E-SF-RT-01** (maezo-operadora, Onda 1 — catálogo `ret.*` + cerca local; emissão ao lago vai a E-SF-35r) + jornada **E-SF-21** (Onda 2) | EXECUTION-LOG.md:84/109; ADDENDUM-01.md:280; EPICS-PLAN.tsv:38 (E-SF-21); anotação F04 |
| SP-OP-CAMPANHA-001 | Campanhas outbound (gate C9): campanha só com lista honrada de oposição e consentimento ratificado | Nome já reservado pelo plano (é épico nomeado), processo inexistente em `spec/`; fechado até o portão RL-SF-031/GT-4 | **E-SF-15** (maezo-operadora, Onda 2; "gate C9 e SP-OP-CAMPANHA-001") | EPICS-PLAN.tsv:27; RLR:39 (RL-SF-031); F02-VEREDITO D4 |
| SP-OP-ONBOARD-001 | Boas-vindas/onboarding do beneficiário: pacote de documentos, entrega art. 16 (meio), check-in D+7 | O fallback do portão GTM-07.J1 explicitamente assume a ausência ("sem SP-OP-ONBOARD-001"); boas-vindas fechada (sendT) até GTM-04.O1 | **E-SF-22r** (maezo-operadora, linha ratificada — AGUARDA-RATIFICAÇÃO; boas-vindas e onboarding do beneficiário) | RLR:122 (RL-SF-114); EPICS-PLAN.tsv:40; REG-NOTAS/ETAPAS-07-08-09.md §b G9-4 |

**Total: 9 faltantes** — 4 com nome já reservado pelo programa (SP-OP-NOTIF-001, SP-OP-REAJUSTE-001, SP-OP-CAMPANHA-001, SP-OP-ONBOARD-001) e 5 propostos nesta entrega (SP-OP-EXEC-COMERCIAL-001, SP-OP-RELOGIO-001, SP-OP-TRACKER-001, SP-OP-DS-001, SP-OP-ARREP-001).

Cobertura mínima pedida — conferida: execução comercial do funil ✓ (SP-OP-EXEC-COMERCIAL-001 ← E-SF-49; o relógio de 25 d citado como E-SF-07 ficou na linha própria SP-OP-RELOGIO-001, pois E-SF-07 é "Relógio de contratação de 25 dias", TSV L11); tracker omni ✓ (E-SF-42, TSV L71); entrevista DS ✓ (E-SF-44 é da casa omni_sales_platform — TSV L75; a perna maezo da DS é E-SF-25/26/27/28, TSV L45–50); arrependimento ✓ (E-SF-09); NOTIF-001 ✓ (E-SF-13); catálogo `ret.*` cerca ✓ (E-SF-RT-01, ADDENDUM §5.3 L280).

### Aviso de caminho canônico

Nenhum destes processos é criado por esta entrega. A criação de SP-OP-* novo passa pelo caminho canônico do programa: épico E-SF-nn no repo-casa (linha executável ou ratificada do EPICS-PLAN.tsv/ADDENDUM §5.3) → gates de autoria (EP-PROC-04) → verificação independente → incorporação pelo orquestrador; e qualquer mudança de elemento nos processos existentes depende da **disposição do dono** sobre os flips do MAPA-ELEMENTOS-V4/D-02 (RL-SF-119 — RLR:127: "Nenhum flip é aplicado sem disposição"). Precedentes: SP-OP-NOTIF-001 só entra em produção por OWNER-DEPLOY após gates 1–5 e RL-SF-099 (RLR:139); o BPMN de arrependimento nasce do escopo PROC de E-SF-09 (EXECUTION-LOG.md:64); SP-OP-REAJUSTE-001 segue inexistente até que um épico o crie — hoje o E-SF-RT-01 entrega só allowlist/cerca local (EXECUTION-LOG.md:84, CD-07).
