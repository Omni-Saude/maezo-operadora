# Guia de equipe e operação — os 4 processos CA (COMPRA · UTILIZAÇÃO · SUPORTE · SERVIÇOS COMPARTILHADOS)

**Versão:** 1.1 — refinamento dos adaptados v4 · **Data:** 2026-10 · **Objetivo:** O3 (operação e dimensionamento de equipe)

**Propósito.** Este guia orienta a leitura dos quatro modelos adaptados v4 e suas 57 lanes, preservando a autoria dos originais v3. Cada lane representa responsabilidade no desenho, sem provar pessoa, equipe física, capacidade instalada ou executor registrado. O guia distingue decisões humanas explícitas, automação e contratos externos esperados. As perguntas são checklist educativo para avaliação futura; a devolutiva foi preparada autonomamente, sem esperar participação da equipe autora.

**Escopo.** O guia **não altera nenhuma disposição dos modelos**. É companheiro de `CORRECOES_BPMN_2026-10.md` e não substitui as correções lá propostas. Nenhum arquivo BPMN foi modificado para produzi-lo. As citações seguem a convenção `arquivo#elementoId` (por exemplo `CA_3_SUPORTE_v4_adaptado.bpmn#S3_T_Enfermagem`); onde o guia aponta um *limite do desenho*, isso é matéria de discussão, não de correção silenciosa.

**Fontes ativas: quatro adaptados v4 refinados; originais v3 são referências de autoria. Inventário atual e hashes: manifesto desta execução.**

| Arquivo | Processos | Lanes |
|---|---|---|
| `CA_0_SERVICOS_COMPARTILHADOS_v4_adaptado.bpmn` | 6 (H2, H2.1, H1, H3, H3.1, H3.2) | 16 |
| `CA_1_COMPRA (com suporte)_v4_adaptado.bpmn` | 5 (COMPRA, C1, C2, C3, C4) | 16 |
| `CA_2_UTILIZACAO_v4_adaptado.bpmn` | 5 (UTILIZAÇÃO, U1, U2, U3, U4) | 12 |
| `CA_3_SUPORTE_v4_adaptado.bpmn` | 5 (SUPORTE, S1, S2, S3, S4) | 13 |

---

## 0. Como ler este guia

**Três verbos por lane.** **Decide** = pontos em que a lane determina o rumo do caso: tarefas humanas (`userTask`) e gateways de decisão (`exclusiveGateway` com 2+ saídas nomeadas). **Executa** = trabalho que a lane realiza (tarefas de serviço/executor automático que a lane governa, chamadas de processo, registro de marcos). **Acompanha/recebe** = sinais, timers e marcos que chegam prontos para a lane agir sobre eles, sem decisão nova.

**Volume indicativo de decisão.** Score topológico = userTasks da equipe + gateways exclusivos de decisão com 2+ saídas. Alta = 3+; Média = 1–2; Baixa = 0. Tarefas do cliente, merges e gateways de espera não contam. É densidade do desenho, não volume de casos, fila, capacidade ou necessidade de contratação. Gravidade e alçada são critérios separados; faltam taxas de chegada, duração de trabalho, paralelismo e capacidade para dimensionar equipes.

**Tarefas do cliente não são cabeça de equipe.** Cinco tarefas humanas do corpus têm `assignee=${clienteId}` — `C2_T_Aceite`, `C4_T_Confirmar`, `U1_T_CheckIn`, `U4_T_Pesquisa`, `S4_T_Confirmar`. Elas aparecem como **execução do cliente**: a equipe da lane desenha o formulário, monitora o retorno e cuida do prazo, mas não executa a tarefa. Isso define requisitos de desenho/monitoramento; não permite inferir quantidade de pessoas ou vagas.

**Vocabulário do guia** (sem jargão interno): *executor automático* (tarefa de serviço), *recomendação* (saída de motor de sugestão, sempre revisável), *regra nomeável* (condição de gateway que pode virar tabela de regras com dono), *marco factual* (algo que aconteceu) versus *estimativa* (algo previsto).

---

# 1. SERVIÇOS COMPARTILHADOS — `CA_0_SERVICOS_COMPARTILHADOS_v4_adaptado.bpmn`

Seis processos que os outros três arquivos chamam. Quem opera CA_0 opera **para todos**: as decisões daqui se multiplicam por toda a operação.

### 1.1 H2 — Comunicar o cliente (`Process_CA_H2_ComunicarCliente`)

Lane **CRM e comunicação** (`LN_CA0_H2_CrmEComunicacao`) — **volume: Alta** (4 gateways de decisão: `H2_GW_Tipo`, `H2_GW_Confiavel`, `H2_GW_Envio`, `H2_GW_Entregue`)
- **Decide:** se o evento pede ação ou antecipa dúvida (`CA_0_SERVICOS_COMPARTILHADOS_v4_adaptado.bpmn#H2_GW_Tipo`); se a mensagem é confiável e está fora do risco clínico 3 (`CA_0_SERVICOS_COMPARTILHADOS_v4_adaptado.bpmn#H2_GW_Confiavel`); enviar, adiar ou seguir para revisão (`CA_0_SERVICOS_COMPARTILHADOS_v4_adaptado.bpmn#H2_GW_Envio`); e o que fazer quando não houver confirmação de entrega (`CA_0_SERVICOS_COMPARTILHADOS_v4_adaptado.bpmn#H2_GW_Entregue`). É a lane que interpreta a política de contato.
- **Executa:** classificação do evento, geração da mensagem, envio, canal de contingência, atualização da central de ajuda e registro no contexto do cliente (`CA_0_SERVICOS_COMPARTILHADOS_v4_adaptado.bpmn#H2_T_Classificar`, `CA_0_SERVICOS_COMPARTILHADOS_v4_adaptado.bpmn#H2_T_Gerar`, `CA_0_SERVICOS_COMPARTILHADOS_v4_adaptado.bpmn#H2_T_Enviar`, `CA_0_SERVICOS_COMPARTILHADOS_v4_adaptado.bpmn#H2_T_Fallback`, `CA_0_SERVICOS_COMPARTILHADOS_v4_adaptado.bpmn#H2_T_Portal`, `CA_0_SERVICOS_COMPARTILHADOS_v4_adaptado.bpmn#H2_T_Log`), e a chamada de seleção de canal `CA_0_SERVICOS_COMPARTILHADOS_v4_adaptado.bpmn#H2_CA_Canal`.
- **Acompanha/recebe:** a janela permitida para envio (`CA_0_SERVICOS_COMPARTILHADOS_v4_adaptado.bpmn#H2_EV_Janela`) e os desfechos registrados (`CA_0_SERVICOS_COMPARTILHADOS_v4_adaptado.bpmn#H2_End_Suprimido`, `CA_0_SERVICOS_COMPARTILHADOS_v4_adaptado.bpmn#H2_End_Contingencia`, `CA_0_SERVICOS_COMPARTILHADOS_v4_adaptado.bpmn#H2_End_Portal`, `CA_0_SERVICOS_COMPARTILHADOS_v4_adaptado.bpmn#H2_End_Log`).
- **Pré-requisitos:** operação de CRM/canais com noção firme de preferência e consentimento; treinamento obrigatório na fronteira **comunicação de obrigação legal nunca suprimida** versus comunicação informativa (esta fronteira *não* está explicitada no desenho — ver pergunta Q1); vocabulário de risco clínico suficiente para saber quando tirar as mãos e chamar a lane de governança.

Lane **Governança clínica** (`LN_CA0_H2_GovernancaClinica`) — **volume: Média** (1 tarefa humana, alta gravidade)
- **Decide:** revisar clinicamente a mensagem antes de sair (`CA_0_SERVICOS_COMPARTILHADOS_v4_adaptado.bpmn#H2_T_Revisao`, grupo `governanca-clinica`), quando a confiança é baixa ou o conteúdo é clínico.
- **Executa:** o ajuste do texto com segurança clínica.
- **Acompanha/recebe:** tudo o que a lane de CRM aprovou sozinha nos casos de risco baixo.
- **Pré-requisitos:** profissional com formação clínica e registro ativo; familiaridade com as regras de comunicação de negativa e de prazo; autoridade real para vetar — revisão sem poder de veto é carimbo.

**Perguntas para discussão**
1. **(A-01)** O desenho tem `CA_0_SERVICOS_COMPARTILHADOS_v4_adaptado.bpmn#H2_GW_Envio` decidindo "ENVIAR/ADIAR" e `CA_0_SERVICOS_COMPARTILHADOS_v4_adaptado.bpmn#H2_End_Suprimido` como desfecho legítimo. Quando a mensagem é um aviso com prazo legal (negativa, resposta de autorização), quem garante que esse caminho de supressão **não** se aplique — e como o revisor clínico reconhece, no texto, que está diante de um aviso "sempre envia"?
2. **(A-02)** Os marcos aqui são factuais ("cliente informado"). Onde a equipe fixa a linha entre *aconteceu* e *previsto* na redação — e quem aprova essa linha? Uma promessa de data mal escrita aqui se repete em todo o resto da operação.
3. **(A-05)** `CA_0_SERVICOS_COMPARTILHADOS_v4_adaptado.bpmn#H2_GW_Confiavel` é automática e `CA_0_SERVICOS_COMPARTILHADOS_v4_adaptado.bpmn#H2_T_Revisao` é o contrapeso humano. Que competência mínima o revisor precisa para **discordar** da recomendação automática, e como a discordância fica registrada para treinamento do motor?

### 1.2 H2.1 — Selecionar canal e momento (`Process_CA_H2_SelecionarCanal`)

Lane **CRM e comunicação** (`LN_CA0_H21_CrmEComunicacao`) — **volume: Média** (1 gateway: `SC_GW_Critico`)
- **Decide:** urgência crítica ou não (`CA_0_SERVICOS_COMPARTILHADOS_v4_adaptado.bpmn#SC_GW_Critico`).
- **Executa:** o mapeamento de urgência para canal (`CA_0_SERVICOS_COMPARTILHADOS_v4_adaptado.bpmn#SC_T_Canal`).
- **Acompanha/recebe:** o retorno das outras duas lanes (opt-in e capping).
- **Pré-requisitos:** taxonomia de urgência escrita e treinada — sem taxonomia, "crítico" vira opinião do operador do turno.

Lane **Agenda e capacidade** (`LN_CA0_H21_AgendaECapacidade`) — **volume: Média** (1 gateway: `SC_GW_Decisao`, 3 saídas)
- **Decide:** enviar agora, adiar ou suprimir (`CA_0_SERVICOS_COMPARTILHADOS_v4_adaptado.bpmn#SC_GW_Decisao`) — o coração do limite de frequência e do horário silencioso (`CA_0_SERVICOS_COMPARTILHADOS_v4_adaptado.bpmn#SC_T_Capping`).
- **Executa:** a aplicação do capping (`CA_0_SERVICOS_COMPARTILHADOS_v4_adaptado.bpmn#SC_T_Capping`).
- **Acompanha/recebe:** a fila de mensagens prontas vindas de `CA_0_SERVICOS_COMPARTILHADOS_v4_adaptado.bpmn#SC_Start`.
- **Pré-requisitos:** gestão de frequência de contato e janelas; leitura de regulatório o bastante para saber que **adiar é aceitável, suprimir aviso obrigatório não é**.

Lane **Disponibilização de insumos** (`LN_CA0_H21_DisponibilizacaoDeInsumos`) — **volume: Média** (1 gateway: `SC_GW_OptIn`)
- **Decide:** se a categoria tem opt-in/base legal (`CA_0_SERVICOS_COMPARTILHADOS_v4_adaptado.bpmn#SC_GW_OptIn`).
- **Executa:** a consulta à central de preferências (`CA_0_SERVICOS_COMPARTILHADOS_v4_adaptado.bpmn#SC_T_Preferencias`).
- **Acompanha/recebe:** o evento de mensagem pronta (`CA_0_SERVICOS_COMPARTILHADOS_v4_adaptado.bpmn#SC_Start`).
- **Pré-requisitos:** LGPD aplicada a base legal por categoria de mensagem; disciplina de catalogação de categorias.

**Perguntas para discussão**
1. **(A-01)** No desenho, `CA_0_SERVICOS_COMPARTILHADOS_v4_adaptado.bpmn#SC_GW_OptIn` vem **antes** de `CA_0_SERVICOS_COMPARTILHADOS_v4_adaptado.bpmn#SC_GW_Critico`. Se o cliente está sem opt-in e a comunicação é um aviso obrigatório, o fluxo atual leva a `CA_0_SERVICOS_COMPARTILHADOS_v4_adaptado.bpmn#SC_End_OptOut`. O time quer uma exceção explícita de "categoria obrigatória ignora opt-in" desenhada, ou prefere tratar isso como regra operacional fora do modelo? (A segunda opção exige um dono formal.)
2. **(A-03)** "Enviar agora?" tem três saídas nomeadas. Se essa regra virar tabela com dono, quem assina cada linha — e com que frequência se revisa o limite de frequência?
3. **(A-06)** Capping e horário silencioso são política de *jornada de relacionamento*. Que treinamento evita que o time de canal aplique essa política por reflexo em comunicações que pertencem ao *processo* (prazo legal)?

### 1.3 H1 — Identidade e consentimento (`Process_CA_H1_IdentidadeConsentimento`)

Lane **Verificador de identidade** (`LN_CA0_H1_VerificadorDeIdentidade`) — **volume: Média** (1 gateway: `H1_GW_Identidade`)
- **Decide:** se a identidade digital já está unificada (`CA_0_SERVICOS_COMPARTILHADOS_v4_adaptado.bpmn#H1_GW_Identidade`).
- **Executa:** diagnóstico de completude do perfil, unificação de identidade e registro de tentativa negada (`CA_0_SERVICOS_COMPARTILHADOS_v4_adaptado.bpmn#H1_T_Diagnostico`, `CA_0_SERVICOS_COMPARTILHADOS_v4_adaptado.bpmn#H1_T_Unificar`, `CA_0_SERVICOS_COMPARTILHADOS_v4_adaptado.bpmn#H1_T_Auditoria`).
- **Acompanha/recebe:** o resultado da consulta de consentimento (`CA_0_SERVICOS_COMPARTILHADOS_v4_adaptado.bpmn#H1_GW_Consentimento`) e o erro de consentimento negado.
- **Pré-requisitos:** operação de identidade digital e prevenção de fraude de identidade **sem** usar dado de saúde para pontuar risco (LGPD art. 11 §5º); escrita de trilha de auditoria impecável — esta lane é testemunha em quase toda contestação.

Lane **Disponibilização de insumos** (`LN_CA0_H1_DisponibilizacaoDeInsumos`) — **volume: Média** (1 gateway: `H1_GW_Consentimento`)
- **Decide:** se o consentimento é válido para a finalidade pedida (`CA_0_SERVICOS_COMPARTILHADOS_v4_adaptado.bpmn#H1_GW_Consentimento`).
- **Executa:** a consulta de consentimento por finalidade (`CA_0_SERVICOS_COMPARTILHADOS_v4_adaptado.bpmn#H1_T_Consentimento`).
- **Acompanha/recebe:** a solicitação de verificação (`CA_0_SERVICOS_COMPARTILHADOS_v4_adaptado.bpmn#H1_Start`).
- **Pré-requisitos:** domínio de base legal por finalidade; autoridade para dizer **não** a um time interno — a lane é chamada por compra, suporte e enriquecimento, e todos terão urgência.

**Perguntas para discussão**
1. **(A-05)** A consulta é *por finalidade*, e o time que chama H1 define essa finalidade. Quem é o dono do catálogo de finalidades e quem treina as lanes chamadoras a recusar um uso sem base legal em vez de "pedir uma finalidade que passe"?
2. **(A-03)** "Identidade digital já unificada?" é uma regra nomeável com critérios próprios. Quando virar tabela, quem assina os critérios de unificação — e quem responde quando a unificação errada faz o dado de um cliente vazar para outro?
3. **(A-06)** H1 é o gate de entrada de três macroprocessos. Que formação comum os três times precisam para tratar H1 como **obrigatório antes de usar dado do cliente**, e não como etapa que se pula quando há pressão?

### 1.4 H3 — Conhecer o cliente e aprender (`Process_CA_H3_ConhecerCliente`)

Lane **Dados e CRM (consolidação de perfil)** (`LN_CA0_H3_DadosECrm`) — **volume: Média** (1 gateway: `H3_GW_Nutrir`)
- **Decide:** se vale nutrir o cliente com conteúdo a partir do evento registrado (`CA_0_SERVICOS_COMPARTILHADOS_v4_adaptado.bpmn#H3_GW_Nutrir`).
- **Executa:** registro do evento no perfil 360 (`CA_0_SERVICOS_COMPARTILHADOS_v4_adaptado.bpmn#H3_T_Registrar`) e o lote de enriquecimento via chamada `CA_0_SERVICOS_COMPARTILHADOS_v4_adaptado.bpmn#H3_CA_Lote`.
- **Acompanha/recebe:** os eventos de negócio (`CA_0_SERVICOS_COMPARTILHADOS_v4_adaptado.bpmn#H3_Start_Evento`) e publica o marco de recomendação de valor (`CA_0_SERVICOS_COMPARTILHADOS_v4_adaptado.bpmn#H3_EV_Recomendar`).
- **Pré-requisitos:** modelagem de dados e consolidação de perfil; noção de minimização (o que *não* entra no perfil); leitura de evento de negócio.

Lane **Modelagem e growth (motor de recomendação)** (`LN_CA0_H3_ModelagemEGrowth`) — **volume: Média** (1 gateway: `H3_GW_Modelo`)
- **Decide:** se o modelo atingiu métricas mínimas para ir a produção (`CA_0_SERVICOS_COMPARTILHADOS_v4_adaptado.bpmn#H3_GW_Modelo`).
- **Executa:** retreino, promoção ao registro de produção e retenção do modelo anterior (`CA_0_SERVICOS_COMPARTILHADOS_v4_adaptado.bpmn#H3_T_Retreinar`, `CA_0_SERVICOS_COMPARTILHADOS_v4_adaptado.bpmn#H3_T_Promover`).
- **Acompanha/recebe:** o alerta à equipe de dados quando as métricas falham (`CA_0_SERVICOS_COMPARTILHADOS_v4_adaptado.bpmn#H3_EV_AlertaDS`, sinal) e o desfecho de modelo retido (`CA_0_SERVICOS_COMPARTILHADOS_v4_adaptado.bpmn#H3_End_ModeloRetido`).
- **Pré-requisitos:** ciência de dados aplicada a recomendação; **governança de modelo com aprovação humana antes de falar com cliente** — hoje a promoção é automática após métricas mínimas, e esse é um ponto de discussão (Q1); LGPD art. 11 §5º.

Lane **Disponibilização de insumos** (`LN_CA0_H3_DisponibilizacaoDeInsumos`) — **volume: Baixa**
- **Decide:** nada — é esteira.
- **Executa:** seleção do lote de clientes elegíveis (`CA_0_SERVICOS_COMPARTILHADOS_v4_adaptado.bpmn#H3_T_Lote`), acionada pelo ciclo diário (`CA_0_SERVICOS_COMPARTILHADOS_v4_adaptado.bpmn#H3_Start_Ciclo`).
- **Acompanha/recebe:** o gatilho diário.
- **Pré-requisitos:** critério de elegibilidade documentado — a seleção de lote é onde "quem entra na esteira" é decidido na prática.

**Perguntas para discussão**
1. **(A-05)** `CA_0_SERVICOS_COMPARTILHADOS_v4_adaptado.bpmn#H3_GW_Modelo` → `CA_0_SERVICOS_COMPARTILHADOS_v4_adaptado.bpmn#H3_T_Promover` é automático. Que regime de aprovação humana o time quer **antes** de um modelo novo gerar recomendação ao cliente — e o que precisa mudar no desenho (ou no procedimento fora do modelo) para isso existir?
2. **(A-06)** `CA_0_SERVICOS_COMPARTILHADOS_v4_adaptado.bpmn#H3_EV_Recomendar` joga a recomendação para a jornada de relacionamento. A recomendação cria uma **expectativa** no cliente: quem é o responsável por honrá-la, e em que prazo?
3. **(A-05)** O perfil 360 alimenta recomendação comercial. Que controle escrito impede que dado de saúde seja usado para priorizar ou excluir alguém (LGPD art. 11 §5º) — e quem audita esse controle?

### 1.5 H3.1 — Enriquecer perfil 360 do cliente (`Process_CA_H3_EnriquecerCliente`)

Lane **Verificador de identidade** (`LN_CA0_H31_VerificadorDeIdentidade`) — **volume: Baixa**
- **Decide:** nada localmente — apenas encaminha ou encerra pela captura do erro de consentimento (`CA_0_SERVICOS_COMPARTILHADOS_v4_adaptado.bpmn#EC_B_Negado`, `CA_0_SERVICOS_COMPARTILHADOS_v4_adaptado.bpmn#EC_End_Negado`).
- **Executa:** a chamada de identidade e consentimento (`CA_0_SERVICOS_COMPARTILHADOS_v4_adaptado.bpmn#EC_CA_H1`) e a abertura do fanout paralelo (`CA_0_SERVICOS_COMPARTILHADOS_v4_adaptado.bpmn#EC_GW_Split`).
- **Acompanha/recebe:** o cliente a enriquecer (`CA_0_SERVICOS_COMPARTILHADOS_v4_adaptado.bpmn#EC_Start`).
- **Pré-requisitos:** as mesmas de H1 — e o treinamento de que **nenhuma fonte é consultada antes do gate**.

Lane **Dados e CRM (consolidação de perfil)** (`LN_CA0_H31_DadosECrm`) — **volume: Baixa**
- **Decide:** nada.
- **Executa:** consolidação e versionamento do perfil 360 (`CA_0_SERVICOS_COMPARTILHADOS_v4_adaptado.bpmn#EC_T_Consolidar`) e a publicação do perfil (`CA_0_SERVICOS_COMPARTILHADOS_v4_adaptado.bpmn#EC_End`).
- **Acompanha/recebe:** o join das cinco fontes (`CA_0_SERVICOS_COMPARTILHADOS_v4_adaptado.bpmn#EC_GW_Join`).
- **Pré-requisitos:** versionamento de dados e reprodutibilidade — o perfil versionado é a resposta que o time dará a um pedido de eliminação.

Lane **Disponibilização de insumos** (`LN_CA0_H31_DisponibilizacaoDeInsumos`) — **volume: Baixa**
- **Decide:** nada.
- **Executa:** as cinco coletas em paralelo — dados abertos, histórico de uso, sinais digitais, dados clínicos (HL7 FHIR) e pesquisas respondidas (`CA_0_SERVICOS_COMPARTILHADOS_v4_adaptado.bpmn#EC_T_Abertos`, `CA_0_SERVICOS_COMPARTILHADOS_v4_adaptado.bpmn#EC_T_Uso`, `CA_0_SERVICOS_COMPARTILHADOS_v4_adaptado.bpmn#EC_T_Digitais`, `CA_0_SERVICOS_COMPARTILHADOS_v4_adaptado.bpmn#EC_T_Clinicos`, `CA_0_SERVICOS_COMPARTILHADOS_v4_adaptado.bpmn#EC_T_Pesquisas`).
- **Acompanha/recebe:** o fanout de `CA_0_SERVICOS_COMPARTILHADOS_v4_adaptado.bpmn#EC_GW_Split`.
- **Pré-requisitos:** integração de fontes com minimização por fonte; LGPD aplicada a fonte externa; e um dono por fonte — hoje as cinco coletas são paralelas e sem responsável nomeado no desenho.

**Perguntas para discussão**
1. **(A-05)** A chamada `CA_0_SERVICOS_COMPARTILHADOS_v4_adaptado.bpmn#EC_CA_H1` vem antes do fanout. Que mecanismo de treinamento e revisão impede que uma fonte nova seja acrescentada à esteira **fora** do gate (o gate vale se ninguém lembra por quê)?
2. **(A-03)** Cada fonte do paralelo tem condição própria. Quando uma fonte é negada por consentimento, quem decide se ela sai da esteira, ou volta com outra finalidade — e onde essa decisão fica registrada?
3. **(A-01)** O fim `CA_0_SERVICOS_COMPARTILHADOS_v4_adaptado.bpmn#EC_End_Negado` encerra sem falar com o cliente. O time quer que "não enriquecemos por falta de consentimento" vire uma comunicação com tom próprio, ou é silêncio por desenho? Quem decide?

### 1.6 H3.2 — Calcular próxima melhor ação (`Process_CA_H3_ProximaMelhorAcao`)

Lane **Modelagem e growth (motor de recomendação)** (`LN_CA0_H32_ModelagemEGrowth`) — **volume: Média** (1 gateway de decisão: `NBA_GW_Clinico`; o restante é esteira)
- **Decide:** se a recomendação tem consequência clínica (risco 3) (`CA_0_SERVICOS_COMPARTILHADOS_v4_adaptado.bpmn#NBA_GW_Clinico`).
- **Executa:** carregamento de contexto, ranqueamento, fallback de cold start, guardrails de elegibilidade/cobertura e geração de explicação (`CA_0_SERVICOS_COMPARTILHADOS_v4_adaptado.bpmn#NBA_T_Contexto`, `CA_0_SERVICOS_COMPARTILHADOS_v4_adaptado.bpmn#NBA_T_Ranquear`, `CA_0_SERVICOS_COMPARTILHADOS_v4_adaptado.bpmn#NBA_T_Popularidade`, `CA_0_SERVICOS_COMPARTILHADOS_v4_adaptado.bpmn#NBA_T_Guardrails`, `CA_0_SERVICOS_COMPARTILHADOS_v4_adaptado.bpmn#NBA_T_Explicar`).
- **Acompanha/recebe:** o retorno da revisão humana (`CA_0_SERVICOS_COMPARTILHADOS_v4_adaptado.bpmn#NBA_T_Revisao`) e publica o fim `CA_0_SERVICOS_COMPARTILHADOS_v4_adaptado.bpmn#NBA_End`.
- **Pré-requisitos:** motor de recomendação com explicação legível; disciplina de guardrail — o guardrail **recomenda-se a excluir**, nunca decide; taxonomia de risco clínico 1/2/3 escrita e treinada.

Lane **Governança clínica** (`LN_CA0_H32_GovernancaClinica`) — **volume: Média** (1 tarefa humana, gravidade máxima)
- **Decide:** revisar a recomendação clínica no loop obrigatório (`CA_0_SERVICOS_COMPARTILHADOS_v4_adaptado.bpmn#NBA_T_Revisao`, grupo `governanca-clinica`).
- **Executa:** o ajuste/veto da recomendação com julgamento clínico.
- **Acompanha/recebe:** o que a lane de modelagem classificou como risco 3.
- **Pré-requisitos:** formação clínica com registro; treino específico em *recomendação versus decisão* — a recomendação instrui, a decisão é dele e precisa de justificativa registrada.

Lane **Disponibilização de insumos** (`LN_CA0_H32_DisponibilizacaoDeInsumos`) — **volume: Média** (1 gateway: `NBA_GW_ColdStart`)
- **Decide:** se o histórico do cliente é suficiente (`CA_0_SERVICOS_COMPARTILHADOS_v4_adaptado.bpmn#NBA_GW_ColdStart`).
- **Executa:** o início do cálculo (`CA_0_SERVICOS_COMPARTILHADOS_v4_adaptado.bpmn#NBA_Start`) e o carregamento de contexto (`CA_0_SERVICOS_COMPARTILHADOS_v4_adaptado.bpmn#NBA_T_Contexto`).
- **Acompanha/recebe:** a solicitação de cálculo.
- **Pré-requisitos:** critério de suficiência de dados documentado — o cold start mal calibrado gera recomendação genérica em massa.

**Perguntas para discussão**
1. **(A-05)** Onde termina "recomendação que o revisor ajusta" e começa "decisão clínica que o revisor assume"? O desenho tem o loop (`CA_0_SERVICOS_COMPARTILHADOS_v4_adaptado.bpmn#NBA_T_Revisao`) mas não tem o vocabulário do limite — quem o escreve e treina?
2. **(A-03)** `CA_0_SERVICOS_COMPARTILHADOS_v4_adaptado.bpmn#NBA_GW_ColdStart` é regra nomeável. Quem é o dono do limiar de histórico suficiente e como o time aprende quando ele erra (recomendação fria demais ou exposição indevida)?
3. **(A-02)** `CA_0_SERVICOS_COMPARTILHADOS_v4_adaptado.bpmn#NBA_T_Explicar` gera a explicação ao cliente. Essa explicação é fato ou estimativa? Quem valida o vocabulário antes de ela ser lida por milhares de pessoas?

---

# 2. COMPRA — `CA_1_COMPRA (com suporte)_v4_adaptado.bpmn`

### 2.1 COMPRA — Obter o direito ao cuidado (`Process_CA_COMPRA`)

Lane **Consultor comercial** (`LN_CA1_COMPRA_ConsultorComercial`) — **volume: Média** (2 gateways de decisão: `CP_GW_Proposta`, `CP_GW_SemOferta`)
- **Decide:** se a proposta é relevante e viável (`CA_1_COMPRA (com suporte)_v4_adaptado.bpmn#CP_GW_Proposta`) e quando a busca ativa termina sem oferta (`CA_1_COMPRA (com suporte)_v4_adaptado.bpmn#CP_GW_SemOferta`).
- **Executa:** o recebimento das três intenções de compra (`CA_1_COMPRA (com suporte)_v4_adaptado.bpmn#CP_Start_Necessidade`, `CA_1_COMPRA (com suporte)_v4_adaptado.bpmn#CP_Start_Reacao`, `CA_1_COMPRA (com suporte)_v4_adaptado.bpmn#CP_Start_NovaNecessidade`) e a chamada de entendimento/recomendação (`CA_1_COMPRA (com suporte)_v4_adaptado.bpmn#CP_CA_C1`).
- **Acompanha/recebe:** o retorno de consentimento negado (`CA_1_COMPRA (com suporte)_v4_adaptado.bpmn#CP_B_ConsentimentoNegado`) e os fins de nutrição e encaminhamento ao suporte (`CA_1_COMPRA (com suporte)_v4_adaptado.bpmn#CP_End_Nutrir`, `CA_1_COMPRA (com suporte)_v4_adaptado.bpmn#CP_End_Suporte`).
- **Pré-requisitos:** consultoria de cobertura com **treinamento explícito em A-04**: falar de direito-base sem prometer cobertura de serviço; e em A-05: a recomendação vinda de C1 é sugestão, não veredito.

Lane **Contratação e back-office comercial** (`LN_CA1_COMPRA_ContratacaoBackOfficeComercial`) — **volume: Média** (1 gateway: `CP_GW_Decisao`, 3 saídas)
- **Decide:** o que fazer com a decisão do cliente — aceite, recusa ou sem decisão (`CA_1_COMPRA (com suporte)_v4_adaptado.bpmn#CP_GW_Decisao`).
- **Executa:** a chamada de decisão e contratação (`CA_1_COMPRA (com suporte)_v4_adaptado.bpmn#CP_CA_C2`) e a liberação de slot quando não há aceite (`CA_1_COMPRA (com suporte)_v4_adaptado.bpmn#CP_End_Recusa`, `CA_1_COMPRA (com suporte)_v4_adaptado.bpmn#CP_End_SemDecisao`).
- **Acompanha/recebe:** o desfecho da proposta.
- **Pré-requisitos:** contratos e condições comerciais; gestão de slot reservado (o que a operadora promete manter enquanto o cliente decide — ver pergunta 2 de C2).

Lane **Cadastro e operações** (`LN_CA1_COMPRA_CadastroOperacoes`) — **volume: Média** (2 gateways de decisão: `CP_GW_Habilitado`, `CP_GW_TipoDireito`)
- **Decide:** se o direito foi habilitado (`CA_1_COMPRA (com suporte)_v4_adaptado.bpmn#CP_GW_Habilitado`) e **que tipo de direito** está em jogo — plano (novo beneficiário) ou serviço (`CA_1_COMPRA (com suporte)_v4_adaptado.bpmn#CP_GW_TipoDireito`).
- **Executa:** a entrada do novo beneficiário (`CA_1_COMPRA (com suporte)_v4_adaptado.bpmn#CP_Start_NovoBeneficiario`) e a chamada de habilitação (`CA_1_COMPRA (com suporte)_v4_adaptado.bpmn#CP_CA_C3`).
- **Acompanha/recebe:** os desfechos de venda perdida e direito habilitado (`CA_1_COMPRA (com suporte)_v4_adaptado.bpmn#CP_End_VendaPerdida`, `CA_1_COMPRA (com suporte)_v4_adaptado.bpmn#CP_End_Beneficiario`, `CA_1_COMPRA (com suporte)_v4_adaptado.bpmn#CP_End_DireitoHabilitado`).
- **Pré-requisitos:** cadastro e habilitação com leitura de rol/diretrizes de utilização (RN 465/2021) e das regras aplicáveis de resposta e garantia de atendimento, qualificadas por caso; a **distinção A-04** é o coração do treinamento desta lane.

Lane **Retenção e relacionamento** (`LN_CA1_COMPRA_RetencaoRelacionamento`) — **volume: Média** (1 gateway: `CP_GW_Manutencao`, 3 saídas)
- **Decide:** o que fazer com o resultado da manutenção — remarcado, sem slot ou encerrado (`CA_1_COMPRA (com suporte)_v4_adaptado.bpmn#CP_GW_Manutencao`).
- **Executa:** o gatilho de consumo não realizado (`CA_1_COMPRA (com suporte)_v4_adaptado.bpmn#CP_Start_ConsumoNaoRealizado`) e a chamada de manutenção (`CA_1_COMPRA (com suporte)_v4_adaptado.bpmn#CP_CA_C4`).
- **Acompanha/recebe:** os fins de direito encerrado e recovery (`CA_1_COMPRA (com suporte)_v4_adaptado.bpmn#CP_End_DireitoEncerrado`, `CA_1_COMPRA (com suporte)_v4_adaptado.bpmn#CP_End_Recovery`).
- **Pré-requisitos:** recovery de relacionamento; fronteira **cancelar slot ≠ cancelar contrato** (A-06) treinada e testada em cenário.

Lane **Atendente de suporte** (`LN_CA1_COMPRA_AtendenteSuporte`) — **volume: Baixa**
- **Decide:** nada no desenho — o pedido de ajuda publica e a compra segue (`CA_1_COMPRA (com suporte)_v4_adaptado.bpmn#CP_ES_Start_Ajuda`, `CA_1_COMPRA (com suporte)_v4_adaptado.bpmn#CP_ES_End_Ajuda`).
- **Executa:** o atendimento do pedido de ajuda via subprocesso de eventos (`CA_1_COMPRA (com suporte)_v4_adaptado.bpmn#CP_ES_Ajuda`).
- **Acompanha/recebe:** "cliente pede ajuda" em qualquer ponto da compra.
- **Pré-requisitos:** atendimento com contexto da jornada; **compromisso de prazo de resposta** — o desenho publica o pedido mas não nomeia quem responde em quanto tempo (ver pergunta 2).

**Perguntas para discussão**
1. **(A-04)** `CA_1_COMPRA (com suporte)_v4_adaptado.bpmn#CP_GW_TipoDireito` separa plano de serviço avulso. O time concorda que *habilitar direito-base* e *autorizar serviço* são competências de equipes diferentes? Se sim, que formação diferente cada uma exige — e onde o cliente sente a diferença?
2. **(A-06)** `CA_1_COMPRA (com suporte)_v4_adaptado.bpmn#CP_ES_Start_Ajuda` publica o pedido de ajuda sem interromper a compra. Quem responde, em quanto tempo, e o que acontece com a proposta se a resposta demorar? (Compromisso sem prazo é promessa implícita — A-02.)
3. **(A-02)** `CA_1_COMPRA (com suporte)_v4_adaptado.bpmn#CP_End_VendaPerdida` registra motivo. Quem consome esses motivos, com que cadência, e o que muda no processo quando o motivo se repete?

### 2.2 C1 — Entender a necessidade e recomendar (`Process_CA_COMPRA_C1_EntenderRecomendar`)

Lane **Verificador de identidade** (`LN_CA1_C1_VerificadorIdentidade`) — **volume: Baixa**
- **Decide:** nada — o gate é o próprio H1.
- **Executa:** o recebimento da intenção (`CA_1_COMPRA (com suporte)_v4_adaptado.bpmn#C1_Start`) e a chamada de identidade/consentimento (`CA_1_COMPRA (com suporte)_v4_adaptado.bpmn#C1_CA_H1`).
- **Acompanha/recebe:** o resultado do gate de consentimento.
- **Pré-requisitos:** os de H1.

Lane **Modelagem e growth (motor de recomendação)** (`LN_CA1_C1_ModelagemGrowth`) — **volume: Baixa**
- **Decide:** nada — consome a recomendação calculada.
- **Executa:** a chamada de próxima melhor ação (`CA_1_COMPRA (com suporte)_v4_adaptado.bpmn#C1_CA_NBA`) e o registro para medir adesão (`CA_1_COMPRA (com suporte)_v4_adaptado.bpmn#C1_T_Reward`).
- **Acompanha/recebe:** a proposta pronta (`CA_1_COMPRA (com suporte)_v4_adaptado.bpmn#C1_End_Proposta`).
- **Pré-requisitos:** desenho de experimento de adesão (o `CA_1_COMPRA (com suporte)_v4_adaptado.bpmn#C1_T_Reward` só vale algo se alguém lê o resultado).

Lane **Consultor comercial** (`LN_CA1_C1_ConsultorComercial`) — **volume: Média** (1 gateway: `C1_GW_Proposta`)
- **Decide:** se há proposta composta (`CA_1_COMPRA (com suporte)_v4_adaptado.bpmn#C1_GW_Proposta`).
- **Executa:** o cruzamento de perfil 360 com oferta de cuidado (`CA_1_COMPRA (com suporte)_v4_adaptado.bpmn#C1_T_Cruzar`).
- **Acompanha/recebe:** o fim sem proposta (`CA_1_COMPRA (com suporte)_v4_adaptado.bpmn#C1_End_SemProposta`), que devolve ao macro.
- **Pré-requisitos:** catálogo de ofertas e critério de relevância **escrito** (a condição é regra nomeável); LGPD art. 11 §5º — vedada seleção de risco na contratação ou exclusão, inclusive por ofertas/proxies que produzam esse efeito. Recomendações assistenciais exigem finalidade, necessidade, base e autoridade próprias; o §5º não as proíbe indiscriminadamente.

Lane **Agenda e capacidade** (`LN_CA1_C1_AgendaCapacidade`) — **volume: Baixa**
- **Decide:** nada local — aplica política.
- **Executa:** consulta de agenda em tempo real e política de capacidade de acesso (`CA_1_COMPRA (com suporte)_v4_adaptado.bpmn#C1_T_Agenda`, `CA_1_COMPRA (com suporte)_v4_adaptado.bpmn#C1_T_Capacidade`).
- **Acompanha/recebe:** o contexto da proposta.
- **Pré-requisitos:** gestão de capacidade com critério de prioridade clínica escrito — e a resposta à pergunta 3: o comercial pode negociar capacidade por caso?

**Perguntas para discussão**
1. **(A-05)** `CA_1_COMPRA (com suporte)_v4_adaptado.bpmn#C1_T_Cruzar` é automático. Que regra impede que a oferta direcionada produza seleção de risco na contratação/exclusão por dados de saúde ou proxies (LGPD art. 11 §5º), e quem audita essa regra com que frequência?
2. **(A-03)** "Proposta composta?" decide o destino do caso. Quem é o dono do critério "relevante e viável" — e como o time converte esse critério em tabela sem perder o julgamento do consultor?
3. **(A-06)** `CA_1_COMPRA (com suporte)_v4_adaptado.bpmn#C1_T_Capacidade` aplica política de acesso. Que treinamento evita que o time comercial trate essa política como negociável por caso, e onde o cliente registra quando a política limita o acesso dele?

### 2.3 C2 — Decidir e contratar (`Process_CA_COMPRA_C2_DecidirContratar`)

Lane **Contratação e back-office comercial** (`LN_CA1_C2_ContratacaoBackOfficeComercial`) — **volume: Baixa para a equipe** (a decisão é do cliente: `CA_1_COMPRA (com suporte)_v4_adaptado.bpmn#C2_T_Aceite` tem `assignee=${clienteId}`; a equipe é dona da política)
- **Decide:** o cliente decide sobre a proposta em 1 clique (`CA_1_COMPRA (com suporte)_v4_adaptado.bpmn#C2_T_Aceite`) — a equipe **decide apenas a moldura**: texto da proposta, política de lembrete e prazo máximo.
- **Executa:** a apresentação da proposta (`CA_1_COMPRA (com suporte)_v4_adaptado.bpmn#C2_Start`), o marco "agendado" (`CA_1_COMPRA (com suporte)_v4_adaptado.bpmn#C2_EV_Agendado`) e o lembrete recorrente (`CA_1_COMPRA (com suporte)_v4_adaptado.bpmn#C2_EV_Lembrete`).
- **Acompanha/recebe:** três timers/bordas — prazo máximo de decisão (`CA_1_COMPRA (com suporte)_v4_adaptado.bpmn#C2_B_PrazoMax`, variável sem valor fixado), lembrete a cada 24h (`CA_1_COMPRA (com suporte)_v4_adaptado.bpmn#C2_B_Lembrete`, `R/PT24H`) e recusa por mensagem (`CA_1_COMPRA (com suporte)_v4_adaptado.bpmn#C2_B_Recusa`).
- **Pré-requisitos:** desenhos de formulário de 1 clique; gestão de slot reservado; e clareza sobre **o que é fato e o que é estimativa** ao comunicar prazo (A-02).

**Perguntas para discussão**
1. **(A-02)** O lembrete é recorrente a cada 24h e o prazo máximo é variável (`${prazoMaximoDecisao}`). O que o time comunica ao cliente como "prazo" — e é fato calculado ou estimativa? Quem fixa o valor da variável e o comunica como compromisso?
2. **(A-06)** Enquanto o cliente decide, a operadora reserva slot. Que compromisso ela assume (preço, vaga, validade) e quem no time responde por ele quando expira?
3. **(A-03)** A recusa encerra com slot liberado. Quem é o dono da regra de liberação de slot — e o que impede que um slot fique preso por erro de status?

### 2.4 C3 — Habilitar o direito (`Process_CA_COMPRA_C3_HabilitarDireito`)

Lane **Cadastro e operações** (`LN_CA1_C3_CadastroOperacoes`) — **volume: Alta** (4 gateways de decisão: `C3_GW_Tipo`, `C3_GW_Auto`, `C3_GW_Pendencia`, `C3_GW_TipoNegativa` + gateway de espera com 4 desfechos)
- **Decide:** tipo de direito (`CA_1_COMPRA (com suporte)_v4_adaptado.bpmn#C3_GW_Tipo`); se a autorização pode ser automática (`CA_1_COMPRA (com suporte)_v4_adaptado.bpmn#C3_GW_Auto`); se a pendência foi resolvida dentro do limite de tentativas (`CA_1_COMPRA (com suporte)_v4_adaptado.bpmn#C3_GW_Pendencia`); e se a negativa é de cobertura assistencial (`CA_1_COMPRA (com suporte)_v4_adaptado.bpmn#C3_GW_TipoNegativa`) — a pergunta que dispara parecer médico.
- **Executa:** avaliação de elegibilidade, emissão automática, submissão de requisição (`CA_1_COMPRA (com suporte)_v4_adaptado.bpmn#C3_T_Elegibilidade`, `CA_1_COMPRA (com suporte)_v4_adaptado.bpmn#C3_T_AutoAutorizar`, `CA_1_COMPRA (com suporte)_v4_adaptado.bpmn#C3_T_Requisitar`) e a espera dos quatro desfechos (`CA_1_COMPRA (com suporte)_v4_adaptado.bpmn#C3_GW_Espera`: concedida, pendência documental, negada, expirou).
- **Acompanha/recebe:** os marcos "autorizado" e "em análise" (`CA_1_COMPRA (com suporte)_v4_adaptado.bpmn#C3_EV_Autorizado`, `CA_1_COMPRA (com suporte)_v4_adaptado.bpmn#C3_EV_EmAnalise`).
- **Pré-requisitos:** separar habilitação administrativa/matrícula de autorização assistencial, com fonte e alçada de cada direito. Prazos de resposta, realização e junta têm objetos distintos; a referência anterior à RN 464/2021 foi retirada por não sustentar autorização. PT48H é proposta do modelo, não norma homologada. O treinamento A-04 deve preservar essa separação.

Lane **Dados e CRM (consolidação de perfil)** (`LN_CA1_C3_DadosCRM`) — **volume: Baixa**
- **Decide:** nada.
- **Executa:** o enriquecimento inicial do perfil (`CA_1_COMPRA (com suporte)_v4_adaptado.bpmn#C3_CA_Perfil`) e o fim de direito ao plano habilitado (`CA_1_COMPRA (com suporte)_v4_adaptado.bpmn#C3_End_Plano`).
- **Acompanha/recebe:** o contexto cadastral.
- **Pré-requisitos:** os de H3.1 (o gate de consentimento vem antes).

Lane **Governança clínica** (`LN_CA1_C3_GovernancaClinica`) — **volume: Média** (1 tarefa humana, gravidade máxima da compra)
- **Decide:** emitir o parecer médico da negativa (`CA_1_COMPRA (com suporte)_v4_adaptado.bpmn#C3_T_Parecer`, grupo `governanca-clinica`) — decisão humana indelegável; a documentação do modelo cita junta médica como desempatador em caso de divergência.
- **Executa:** a análise clínica e a redação do parecer com fundamento de cobertura.
- **Acompanha/recebe:** a negativa classificada como assistencial por `CA_1_COMPRA (com suporte)_v4_adaptado.bpmn#C3_GW_TipoNegativa`.
- **Pré-requisitos:** médico com registro ativo; domínio de rol e diretrizes de utilização (RN 465/2021) e do rito de negativa; dupla-checagem e trilha de auditoria — o parecer é o documento que sustenta (ou derruba) a operadora.

Lane **Centrais de experiência** (`LN_CA1_C3_CentraisExperiencia`) — **volume: Média** (1 tarefa humana + 1 gateway de decisão)
- **Decide:** oferecer alternativa ao cliente (`CA_1_COMPRA (com suporte)_v4_adaptado.bpmn#C3_T_Renegociar`, grupo `centrais-experiencia`) e classificar a reação — aceitou, recusou, pediu humano (`CA_1_COMPRA (com suporte)_v4_adaptado.bpmn#C3_GW_Alternativa`, 3 saídas).
- **Executa:** a negociação da alternativa.
- **Acompanha/recebe:** os fins de direito não habilitado e de contestação/humano solicitado (`CA_1_COMPRA (com suporte)_v4_adaptado.bpmn#C3_End_Perdida`, `CA_1_COMPRA (com suporte)_v4_adaptado.bpmn#C3_End_Suporte`).
- **Pré-requisitos:** atendimento com autoridade de alternativa (a documentação do modelo exige que toda negativa carregue caminho imediato); treino A-05: a alternativa oferecida nunca pode ser condicionada ao perfil de risco do cliente.

**Perguntas para discussão**
1. **(A-04)** O desenho separa direito-base e autorização por `CA_1_COMPRA (com suporte)_v4_adaptado.bpmn#C3_GW_Tipo`, mas depois pergunta "autorização automática possível?" na **mesma** lane. O time reconhece aqui duas competências na mesma lane? Se sim, que training separa as duas na cabeça do time — e quem responde quando a automação erra?
2. **(A-05)** `CA_1_COMPRA (com suporte)_v4_adaptado.bpmn#C3_T_Parecer` é humano por desenho. Que formação, registro profissional e dupla-checagem o time exige para essa tarefa — e que salvaguarda impede que o prazo pressione o conteúdo do parecer?
3. **(A-01)** `CA_1_COMPRA (com suporte)_v4_adaptado.bpmn#C3_EV_EmAnalise` é factual; o prazo de resposta exige fonte e aplicabilidade qualificadas. Como comunicar o prazo efetivamente aplicável, distinguindo resposta de realização e proposta de norma — e o que acontece com o marco quando o prazo está prestes a estourar?

### 2.5 C4 — Manter o direito (`Process_CA_COMPRA_C4_ManterDireito`)

Lane **Retenção e relacionamento** (`LN_CA1_C4_RetencaoRelacionamento`) — **volume: Média** (1 gateway: `C4_GW_Motivo`)
- **Decide:** o motivo da manutenção — no-show ou cancelamento do prestador versus cancelamento pelo cliente (`CA_1_COMPRA (com suporte)_v4_adaptado.bpmn#C4_GW_Motivo`).
- **Executa:** o gatilho de manutenção (`CA_1_COMPRA (com suporte)_v4_adaptado.bpmn#C4_Start`) e o encaminhamento quando o cliente cancela (`CA_1_COMPRA (com suporte)_v4_adaptado.bpmn#C4_End_Cancelado`).
- **Acompanha/recebe:** o erro de slot sem equivalente propagado do outro ramo.
- **Pré-requisitos:** recovery e a fronteira A-06 treinada: **cancelar slot de entrega não é cancelar contrato** — o ramo do cliente cancelando não deve reter, mas também não pode encerrar contrato por conta própria.

Lane **Agenda e capacidade** (`LN_CA1_C4_AgendaCapacidade`) — **volume: Baixa** (score 0; a confirmação é do cliente)
- **Decide:** o cliente confirma ou ajusta o novo slot em 1 toque (`CA_1_COMPRA (com suporte)_v4_adaptado.bpmn#C4_T_Confirmar`, `assignee=${clienteId}`); a equipe decide a política de equivalência de slot.
- **Executa:** o reagendamento automático em slot equivalente (`CA_1_COMPRA (com suporte)_v4_adaptado.bpmn#C4_T_Reagendar`), com erro de sem-slot (`CA_1_COMPRA (com suporte)_v4_adaptado.bpmn#C4_B_SemSlot`), o lembrete do novo slot (`CA_1_COMPRA (com suporte)_v4_adaptado.bpmn#C4_EV_Lembrete`) e o marco "reagendado" (`CA_1_COMPRA (com suporte)_v4_adaptado.bpmn#C4_EV_Agendado`).
- **Acompanha/recebe:** lembrete recorrente a cada 24h (`CA_1_COMPRA (com suporte)_v4_adaptado.bpmn#C4_B_Lembrete`), prazo máximo de confirmação (`CA_1_COMPRA (com suporte)_v4_adaptado.bpmn#C4_B_PrazoMax`) e os fins sem confirmação/sem slot (`CA_1_COMPRA (com suporte)_v4_adaptado.bpmn#C4_End_SemConfirmacao`, `CA_1_COMPRA (com suporte)_v4_adaptado.bpmn#C4_End_SemSlot`).
- **Pré-requisitos:** gestão de agenda com critério de equivalência de slot **escrito** (equivalente para quem? para o horário ou para o profissional?); capacidade de reagendar em massa quando um prestador cai.

**Perguntas para discussão**
1. **(A-06)** `CA_1_COMPRA (com suporte)_v4_adaptado.bpmn#C4_End_Cancelado` encerra o ramo do cliente. Que treinamento impede que o time de retenção converta "cancelou este serviço" em "cancelou o contrato" — e onde essa fronteira está escrita para o cliente?
2. **(A-02)** "Sem slot equivalente" é fato; a nova data é estimativa até ser confirmada. Quem valida a equivalência do slot **antes** de oferecer ao cliente — e como se comunica um reagendamento que ainda não tem data?
3. **(A-03)** O limite de tentativas de confirmação e o prazo máximo são regras nomeáveis. Quem é o dono, quando mudam, e o que o cliente recebe quando o ciclo se esgota?

---

# 3. UTILIZAÇÃO — `CA_2_UTILIZACAO_v4_adaptado.bpmn`

### 3.1 UTILIZAÇÃO — Receber o cuidado contratado (`Process_CA_UTILIZACAO`)

Lane **Agendamento e autorização** (`LN_CA2_UTILIZACAO_AgendamentoAutorizacao`) — **volume: Baixa**
- **Decide:** nada no macro — dispara a esteira.
- **Executa:** o início pelo direito habilitado (`CA_2_UTILIZACAO_v4_adaptado.bpmn#UT_Start`) e a chamada de preparação (`CA_2_UTILIZACAO_v4_adaptado.bpmn#UT_CA_U1`).
- **Acompanha/recebe:** a guia/autorização como insumo.
- **Pré-requisitos:** ver A-04 — esta lane precisa distinguir "tenho direito" de "este serviço está autorizado" ao alimentar U1.

Lane **Equipe assistencial (prestador)** (`LN_CA2_UTILIZACAO_EquipeAssistencial`) — **volume: Média** (1 gateway: `UT_GW_Status`) · *terceiro, não staff interno*
- **Decide:** o desfecho da entrega — realizado ou não (`CA_2_UTILIZACAO_v4_adaptado.bpmn#UT_GW_Status`).
- **Executa:** a entrega do cuidado via chamada `CA_2_UTILIZACAO_v4_adaptado.bpmn#UT_CA_U2`.
- **Acompanha/recebe:** o boundary de SLA violado (`CA_2_UTILIZACAO_v4_adaptado.bpmn#UT_B_SLA`, escalation → fim `CA_2_UTILIZACAO_v4_adaptado.bpmn#UT_End_SLA` "escalado ao SUPORTE") e o caso de no-show (`CA_2_UTILIZACAO_v4_adaptado.bpmn#UT_End_NoShow`).
- **Pré-requisitos:** é parceiro externo — o que o time precisa é de **contrato de dados**: quem informa os marcos e em quanto tempo (ver pergunta 1 de U2).

Lane **Acompanhamento e gestão de cuidado** (`LN_CA2_UTILIZACAO_AcompanhamentoCuidado`) — **volume: Baixa**
- **Decide:** nada no macro.
- **Executa:** o acompanhamento do resultado via chamada `CA_2_UTILIZACAO_v4_adaptado.bpmn#UT_CA_U3`.
- **Acompanha/recebe:** o evento clínico comunicado (`CA_2_UTILIZACAO_v4_adaptado.bpmn#UT_EC_Comunicar`, publicado pelo subprocesso de eventos clínicos).
- **Pré-requisitos:** gestão de cuidado com treino em *comunicação de evento clínico* — factual, com tom adequado, sem prognóstico por mensagem.

Lane **Liquidação e auditoria de contas** (`LN_CA2_UTILIZACAO_LiquidacaoAuditoria`) — **volume: Média** (1 gateway: `UT_GW_Satisfacao`)
- **Decide:** se a experiência foi satisfatória (`CA_2_UTILIZACAO_v4_adaptado.bpmn#UT_GW_Satisfacao`) e o encaminhamento da insatisfação (`CA_2_UTILIZACAO_v4_adaptado.bpmn#UT_End_Insatisfeito`).
- **Executa:** a liquidação/avaliação via chamada `CA_2_UTILIZACAO_v4_adaptado.bpmn#UT_CA_U4` e o encerramento do consumo (`CA_2_UTILIZACAO_v4_adaptado.bpmn#UT_End_Encerrado`).
- **Acompanha/recebe:** os dados de faturamento/glosa do prestador.
- **Pré-requisitos:** análise de contas no padrão TISS (RN 501/2022) e noção de alçada financeira — o macro apenas encerra; quem decide glosa e pagamento é esta competência, e o treinamento precisa deixar claro o limite.

Lane **Equipe clínica e urgência** (`LN_CA2_UTILIZACAO_EquipeClinicaUrgencia`) — **volume: Baixa** (subprocesso de eventos)
- **Decide:** dentro do subprocesso `CA_2_UTILIZACAO_v4_adaptado.bpmn#UT_ES_EventoClinico`, não há ato de conduta clínica nem userTask modelado; o evento apenas recebe e publica comunicação automaticamente.
- **Executa:** no desenho, publicação automática por comunicarClientePublisher; conduta e protocolo clínicos dependem de responsabilidade externa qualificada, não deste throw.
- **Acompanha/recebe:** eventos clínicos relevantes durante o consumo (`CA_2_UTILIZACAO_v4_adaptado.bpmn#UT_EC_Start`).
- **Pré-requisitos:** responsável clínico qualificado para eventual conduta externa. UT_EC_Start é não interruptivo (isInterrupting=false); o ramo não cancela nem pausa a esteira e não implementa aprovação humana.

Lane **Atendente de suporte** (`LN_CA2_UTILIZACAO_AtendenteSuporte`) — **volume: Baixa** (subprocesso de eventos)
- **Decide:** a condução do cancelamento dentro do subprocesso `CA_2_UTILIZACAO_v4_adaptado.bpmn#UT_ES_Cancelamento`.
- **Executa:** o tratamento de cancelamento de cliente (`CA_2_UTILIZACAO_v4_adaptado.bpmn#UT_ES_Start_CancCliente`) e de prestador (`CA_2_UTILIZACAO_v4_adaptado.bpmn#UT_ES_Start_CancPrestador`).
- **Acompanha/recebe:** o agendamento/guia vigente a cancelar.
- **Pré-requisitos:** fronteira A-06 explícita: cancelar o consumo em curso não é cancelar o contrato — a lane precisa saber qual dos dois está fazendo.

**Perguntas para discussão**
1. **(A-02)** O macro publica marcos factuais e escala quando o SLA estoura (`CA_2_UTILIZACAO_v4_adaptado.bpmn#UT_B_SLA`). Quem é o dono de avisar o cliente **antes** de ele perceber o atraso — e qual é o texto aprovado para esse aviso?
2. **(A-05)** O evento clínico tem protocolo próprio e comunicação ao cliente (`CA_2_UTILIZACAO_v4_adaptado.bpmn#UT_EC_Comunicar`). Quem aprova essa comunicação, e qual o limite do que se comunica por mensagem automática versus por pessoa?
3. **(A-06)** Cancelamentos durante o consumo têm subprocesso separado (`CA_2_UTILIZACAO_v4_adaptado.bpmn#UT_ES_Cancelamento`). O time de atendimento sabe explicar ao cliente a diferença entre cancelar o consumo, o contrato e o slot?

### 3.2 U1 — Preparar o consumo (`Process_CA_UTILIZACAO_U1_Preparar`)

Lane **Agendamento e autorização** (`LN_CA2_U1_AgendamentoAutorizacao`) — **volume: Média** (1 tarefa humana — executada pelo cliente — e 1 gateway)
- **Decide:** se o consumo requer preparo/check-in (`CA_2_UTILIZACAO_v4_adaptado.bpmn#U1_GW_Preparo`); o cliente faz o check-in digital e preparo (`CA_2_UTILIZACAO_v4_adaptado.bpmn#U1_T_CheckIn`, `assignee=${clienteId}`).
- **Executa:** o marco de lembrete de preparo (`CA_2_UTILIZACAO_v4_adaptado.bpmn#U1_EV_Lembrete`) e o marco "pronto para o serviço" (`CA_2_UTILIZACAO_v4_adaptado.bpmn#U1_EV_Pronto`).
- **Acompanha/recebe:** lembrete D-1 (`CA_2_UTILIZACAO_v4_adaptado.bpmn#U1_B_Lembrete`, `${dataLembrete}`) e a virada do dia do serviço (`CA_2_UTILIZACAO_v4_adaptado.bpmn#U1_B_DiaServico`, `${dataHoraServico}`).
- **Pré-requisitos:** suporte ao cliente em dificuldade de check-in (a tarefa é dele, mas a falha é atendida por gente); e o treinamento A-05 mais fino deste arquivo: **"requer preparo" não responde "requer autorização"** — são perguntas diferentes com donos diferentes.

**Perguntas para discussão**
1. **(A-04)** `CA_2_UTILIZACAO_v4_adaptado.bpmn#U1_GW_Preparo` decide sobre preparo/check-in. Onde, no time, vive a pergunta "requer autorização?" — e que treinamento impede alguém de responder uma pela outra quando o cliente pergunta se pode fazer o exame?
2. **(A-02)** O lembrete D-1 e o "pronto para o serviço" são factuais. E o check-in pendente é estimativa de risco de no-show? Quem acompanha e age antes do dia do serviço?
3. **(A-06)** O check-in é do beneficiário. Que caminho de resgate humano existe quando ele não consegue completar o preparo — e em que prazo ele é acionado?

### 3.3 U2 — Receber o cuidado (`Process_CA_UTILIZACAO_U2_Receber`)

Lane **Equipe assistencial (prestador)** (`LN_CA2_U2_EquipeAssistencial`) — **volume: Baixa** · *terceiro*
- **Decide:** nada modelado como decisão — a espera é por eventos (`CA_2_UTILIZACAO_v4_adaptado.bpmn#U2_GW_Chegada`).
- **Executa:** o registro dos marcos "em atendimento" e "realizado" (`CA_2_UTILIZACAO_v4_adaptado.bpmn#U2_EV_EmAtendimento`, `CA_2_UTILIZACAO_v4_adaptado.bpmn#U2_EV_Realizado`).
- **Acompanha/recebe:** a chegada do cliente (`CA_2_UTILIZACAO_v4_adaptado.bpmn#U2_EV_Chegou`), a espera da realização (`CA_2_UTILIZACAO_v4_adaptado.bpmn#U2_T_Aguardar`) e o SLA de 2h (`CA_2_UTILIZACAO_v4_adaptado.bpmn#U2_B_SLA` → escalação `CA_2_UTILIZACAO_v4_adaptado.bpmn#U2_EV_Escalar`, fim `CA_2_UTILIZACAO_v4_adaptado.bpmn#U2_End_Escalado`).
- **Pré-requisitos:** contrato de dados com o prestador — marco informado em tempo é o que faz o resto do processo funcionar.

Lane **Agenda e capacidade** (`LN_CA2_U2_AgendaCapacidade`) — **volume: Baixa**
- **Decide:** nada.
- **Executa:** o registro de no-show e liberação de slot (`CA_2_UTILIZACAO_v4_adaptado.bpmn#U2_T_NoShow`), acionado pelo limite de no-show (`CA_2_UTILIZACAO_v4_adaptado.bpmn#U2_EV_NoShow`).
- **Acompanha/recebe:** o desfecho de no-show (`CA_2_UTILIZACAO_v4_adaptado.bpmn#U2_End_NoShow`).
- **Pré-requisitos:** política de no-show e de reoferta — liberar slot é fácil; recontatar o cliente que faltou é compromisso, e alguém precisa ser dono dele.

**Perguntas para discussão**
1. **(A-02)** `CA_2_UTILIZACAO_v4_adaptado.bpmn#U2_EV_EmAtendimento` e `CA_2_UTILIZACAO_v4_adaptado.bpmn#U2_EV_Realizado` são factuais e dependem do prestador. Quem cobra esse prazo do parceiro e o que o cliente vê quando o marco atrasa?
2. **(A-06)** O no-show libera slot e registra o fato (`CA_2_UTILIZACAO_v4_adaptado.bpmn#U2_T_NoShow`). Isso vira compromisso de novo contato com o cliente? Em quanto tempo e por quem?
3. **(A-05)** A escalação do SLA de 2h é automática (`CA_2_UTILIZACAO_v4_adaptado.bpmn#U2_B_SLA`). Quem a recebe, qual o prazo de resposta humano — e o que impede a escalação de virar ruído que ninguém lê?

### 3.4 U3 — Acompanhar o resultado (`Process_CA_UTILIZACAO_U3_Acompanhar`)

Lane **Acompanhamento e gestão de cuidado** (`LN_CA2_U3_AcompanhamentoCuidado`) — **volume: Média** (1 gateway: `U3_GW_Resultado`)
- **Decide:** se o serviço gera resultado/laudo (`CA_2_UTILIZACAO_v4_adaptado.bpmn#U3_GW_Resultado`).
- **Executa:** a espera pela liberação do resultado (`CA_2_UTILIZACAO_v4_adaptado.bpmn#U3_T_Aguardar`) e os marcos "atraso predito" (`CA_2_UTILIZACAO_v4_adaptado.bpmn#U3_EV_Atraso`, com o cliente avisado antes — `CA_2_UTILIZACAO_v4_adaptado.bpmn#U3_End_Atraso`) e "resultado disponível" (`CA_2_UTILIZACAO_v4_adaptado.bpmn#U3_EV_Resultado`).
- **Acompanha/recebe:** o timer de prazo em risco (`CA_2_UTILIZACAO_v4_adaptado.bpmn#U3_B_Atraso`, `${prazoResultadoAlerta}`).
- **Pré-requisitos:** comunicação de estimativa sem prometer data (A-02); leitura clínica suficiente para saber quando um atraso de resultado é só atraso e quando é caso de cuidado.

**Perguntas para discussão**
1. **(A-02)** `CA_2_UTILIZACAO_v4_adaptado.bpmn#U3_EV_Atraso` comunica um atraso **predito**. Que formação impede que o time prometa data de laudo — e qual o vocabulário aprovado para estimativa?
2. **(A-06)** "Resultado disponível" é marco, não fim de ciclo. Quem fecha o ciclo com o cliente (leitura, retorno, próximo passo) e em que prazo?
3. **(A-03)** `${prazoResultadoAlerta}` é regra nomeável por tipo de exame. Quem define esses prazos, com que base clínica, e com que frequência se revisam?

### 3.5 U4 — Liquidar e avaliar o consumo (`Process_CA_UTILIZACAO_U4_LiquidarAvaliar`)

Lane **Liquidação e auditoria de contas** (`LN_CA2_U4_LiquidacaoAuditoria`) — **volume: Baixa** (score 0; pesquisa do cliente não conta como tarefa da equipe)
- **Decide:** o cliente responde a pesquisa pós-serviço com NPS e desfecho (`CA_2_UTILIZACAO_v4_adaptado.bpmn#U4_T_Pesquisa`, `assignee=${clienteId}`).
- **Executa:** a liquidação do consumo (`CA_2_UTILIZACAO_v4_adaptado.bpmn#U4_T_Liquidar` — tarefa abstrata **com lane responsável, sem binding de executor/alçada/conclusão**) e a abertura paralela (`CA_2_UTILIZACAO_v4_adaptado.bpmn#U4_GW_Split`/`CA_2_UTILIZACAO_v4_adaptado.bpmn#U4_GW_Join`).
- **Acompanha/recebe:** o timer de 72h sem resposta (`CA_2_UTILIZACAO_v4_adaptado.bpmn#U4_B_Pesquisa`).
- **Pré-requisitos:** a lane Liquidação e auditoria de contas já representa a responsabilidade. Executor, alçada e mecanismo de conclusão de U4_T_Liquidar não estão vinculados; precisam de contrato antes da futura operação. O desenho, sozinho, não exige contratação nem comprova software de liquidação.

Lane **Acompanhamento e gestão de cuidado** (`LN_CA2_U4_AcompanhamentoCuidado`) — **volume: Média** (1 gateway: `U4_GW_ProxAcao`)
- **Decide:** se há próxima ação clínica indicada (`CA_2_UTILIZACAO_v4_adaptado.bpmn#U4_GW_ProxAcao`) — com a recomendação **instruindo** e a decisão clínica sendo humana (A-05).
- **Executa:** o registro de desfecho e próxima ação clínica (`CA_2_UTILIZACAO_v4_adaptado.bpmn#U4_T_Desfecho`) e a publicação de nova necessidade de cuidado (`CA_2_UTILIZACAO_v4_adaptado.bpmn#U4_EV_ProxAcao`).
- **Acompanha/recebe:** o retorno da pesquisa.
- **Pré-requisitos:** equipe de cuidado com registro clínico; disciplina de não deixar a "próxima ação indicada" virar decisão automática.

**Perguntas para discussão**
1. **(A-05)** `CA_2_UTILIZACAO_v4_adaptado.bpmn#U4_T_Desfecho` registra a próxima ação indicada e `CA_2_UTILIZACAO_v4_adaptado.bpmn#U4_GW_ProxAcao` decide o encaminhamento. Quem, de fato, decide — e como o dossiê chega a essa pessoa com contexto suficiente?
2. **(A-03)** `CA_2_UTILIZACAO_v4_adaptado.bpmn#U4_T_Liquidar` tem lane responsável, mas não tem executor/alçada/conclusão vinculados. Que competência (TISS, glosa, alçada) o time exige e qual o limite de valor que essa lane pode liquidar sozinha?
3. **(A-06)** A pesquisa coleta insatisfação, mas o follow-up de quem insatisfação é jornada de suporte. Quem assume o caso insatisfeito e em quanto tempo ele vira caso de SUPORTE?

---

# 4. SUPORTE — `CA_3_SUPORTE_v4_adaptado.bpmn`

### 4.1 SUPORTE — Ser ouvido e ter o problema resolvido (`Process_CA_SUPORTE`)

Lane **Atendente de suporte** (`LN_CA3_SUPORTE_Atendente`) — **volume: Média** (1 gateway: `SP_GW_Origem`)
- **Decide:** a origem do suporte — cliente, insatisfação ou SLA violado (`CA_3_SUPORTE_v4_adaptado.bpmn#SP_GW_Origem`).
- **Executa:** o acolhimento das três entradas (`CA_3_SUPORTE_v4_adaptado.bpmn#SP_Start_Contato`, `CA_3_SUPORTE_v4_adaptado.bpmn#SP_Start_Insatisfacao`, `CA_3_SUPORTE_v4_adaptado.bpmn#SP_Start_SLA`) e a chamada de escuta/registro (`CA_3_SUPORTE_v4_adaptado.bpmn#SP_CA_S1`).
- **Acompanha/recebe:** o contexto do caso.
- **Pré-requisitos:** atendimento omnicanal com leitura de contexto; reconhecer quando a entrada "insatisfação" precisa virar caso com prazo, não só conversa.

Lane **Analista de triagem e roteamento** (`LN_CA3_SUPORTE_Triagem`) — **volume: Média** (1 gateway: `SP_GW_Compra`)
- **Decide:** se o caso carrega intenção de compra (`CA_3_SUPORTE_v4_adaptado.bpmn#SP_GW_Compra`) e vai para a COMPRA com contexto (`CA_3_SUPORTE_v4_adaptado.bpmn#SP_End_Compra`).
- **Executa:** o diagnóstico e roteamento via chamada `CA_3_SUPORTE_v4_adaptado.bpmn#SP_CA_S2`.
- **Acompanha/recebe:** o caso registrado.
- **Pré-requisitos:** triagem com taxonomia de intenção escrita; treino A-05 — triagem é classificação, nunca seleção de risco.

Lane **Resolução e encaminhamento** (`LN_CA3_SUPORTE_Resolucao`) — **volume: Baixa**
- **Decide:** nada no macro (merge).
- **Executa:** a resolução/encaminhamento via chamada `CA_3_SUPORTE_v4_adaptado.bpmn#SP_CA_S3`.
- **Acompanha/recebe:** o caso roteado.
- **Pré-requisitos:** ver S3 — é onde a densidade de decisão real do suporte mora.

Lane **Qualidade e melhoria contínua** (`LN_CA3_SUPORTE_Qualidade`) — **volume: Média** (1 gateway: `SP_GW_Desfecho`, 4 saídas)
- **Decide:** o desfecho do caso — resolvido, ouvidoria/ANS com retorno, sem confirmação, ou reencaminhamento (`CA_3_SUPORTE_v4_adaptado.bpmn#SP_GW_Desfecho`).
- **Executa:** o encerramento e aprendizado via chamada `CA_3_SUPORTE_v4_adaptado.bpmn#SP_CA_S4`.
- **Acompanha/recebe:** os fins `CA_3_SUPORTE_v4_adaptado.bpmn#SP_End_Resolvido`, `CA_3_SUPORTE_v4_adaptado.bpmn#SP_End_Ouvidoria`, `CA_3_SUPORTE_v4_adaptado.bpmn#SP_End_SemConfirmacao`.
- **Pré-requisitos:** análise de causa e o **rito de ouvidoria com prazo de retorno** — competência regulatória, não administrativa (ver pergunta 2).

**Perguntas para discussão**
1. **(A-06)** Três entradas viram um caso (`CA_3_SUPORTE_v4_adaptado.bpmn#SP_Start_Contato`, `CA_3_SUPORTE_v4_adaptado.bpmn#SP_Start_Insatisfacao`, `CA_3_SUPORTE_v4_adaptado.bpmn#SP_Start_SLA`). Quando o caso atravessa COMPRA e UTILIZAÇÃO, quem é o dono único — e como o cliente evita contar a história três vezes?
2. **(A-01)** `CA_3_SUPORTE_v4_adaptado.bpmn#SP_End_Ouvidoria` promete "com retorno". O rito regulatório de ouvidoria tem prazo próprio — quem responde por esse prazo, e como o time mede resolutividade (e não só satisfação)?
3. **(A-03)** `CA_3_SUPORTE_v4_adaptado.bpmn#SP_GW_Desfecho` tem 4 saídas. Cada uma tem dono nomeado hoje? Se não, qual das quatro primeiro precisa de dono?

### 4.2 S1 — Ouvir e registrar o caso (`Process_CA_SUPORTE_S1_OuvirRegistrar`)

Lane **Verificador de identidade** (`LN_CA3_S1_Verificador`) — **volume: Baixa**
- **Decide:** nada — o gate é H1, com a captura do consentimento negado (`CA_3_SUPORTE_v4_adaptado.bpmn#S1_B_Consent`).
- **Executa:** o registro do caso (`CA_3_SUPORTE_v4_adaptado.bpmn#S1_Start`) e a chamada de identificação/consentimento (`CA_3_SUPORTE_v4_adaptado.bpmn#S1_CA_H1`).
- **Acompanha/recebe:** o resultado do gate.
- **Pré-requisitos:** os de H1.

Lane **Disponibilização de insumos** (`LN_CA3_S1_Insumos`) — **volume: Média** (1 gateway: `S1_GW_Iniciativa`)
- **Decide:** se o contato partiu da organização (contato ativo) ou do cliente (`CA_3_SUPORTE_v4_adaptado.bpmn#S1_GW_Iniciativa`).
- **Executa:** a montagem do contexto completo — perfil 360 e histórico (`CA_3_SUPORTE_v4_adaptado.bpmn#S1_T_Contexto`).
- **Acompanha/recebe:** o histórico do cliente.
- **Pré-requisitos:** LGPD aplicada a **contato ativo**: quem autorizou a iniciativa, para qual finalidade e quando ela expira.

Lane **Atendente de suporte** (`LN_CA3_S1_Atendente`) — **volume: Baixa**
- **Decide:** nada modelado como gateway — apenas registra e publica o aviso.
- **Executa:** o marco "avisar que estamos cuidando" (`CA_3_SUPORTE_v4_adaptado.bpmn#S1_EV_Cuidando`) e o registro do caso (`CA_3_SUPORTE_v4_adaptado.bpmn#S1_End`).
- **Acompanha/recebe:** o contexto montado.
- **Pré-requisitos:** comunicação de acolhimento sem prometer prazo que não existe (A-02).

**Perguntas para discussão**
1. **(A-05)** O contexto completo só é montado **depois** do gate `CA_3_SUPORTE_v4_adaptado.bpmn#S1_CA_H1`. Que treinamento impede o atendente de "ajudar antes" usando dados que ainda não podia ver — e como isso aparece no treinamento de quem atende por telefone sob pressão?
2. **(A-02)** `CA_3_SUPORTE_v4_adaptado.bpmn#S1_EV_Cuidando` é um acuse de recibo factual. O que o time **não** promete nesse aviso — e existe um texto aprovado?
3. **(A-06)** O contato por iniciativa da organização usa o mesmo registro (`CA_3_SUPORTE_v4_adaptado.bpmn#S1_GW_Iniciativa`). Quem é o dono do consentimento para contato ativo e como ele é verificado antes da ligação?

### 4.3 S2 — Diagnosticar e rotear o caso (`Process_CA_SUPORTE_S2_DiagnosticarRotear`)

Lane **Analista de triagem e roteamento** (`LN_CA3_S2_Triagem`) — **volume: Média** (1 gateway: `S2_GW_Clinico`)
- **Decide:** se o caso é clínico (`CA_3_SUPORTE_v4_adaptado.bpmn#S2_GW_Clinico`) — verdadeiro vai a especialista clínico (`CA_3_SUPORTE_v4_adaptado.bpmn#S2_End_Humano`), senão a IA de atendimento (`CA_3_SUPORTE_v4_adaptado.bpmn#S2_End_IA`).
- **Executa:** a triagem de intenção por classificador automático (`CA_3_SUPORTE_v4_adaptado.bpmn#S2_T_Triagem`) — administrativa × clínica.
- **Acompanha/recebe:** o caso registrado (`CA_3_SUPORTE_v4_adaptado.bpmn#S2_Start`).
- **Pré-requisitos:** taxonomia de caso clínico assinada por alguém com formação clínica; regra de **falha fechada** para "não sei" (ver pergunta 1); treino A-05 — a triagem recomenda a rota, e a responsabilidade pela rota é do time.

**Perguntas para discussão**
1. **(A-05)** `CA_3_SUPORTE_v4_adaptado.bpmn#S2_GW_Clinico` tem duas saídas: clínico → humano; o resto → IA. O que acontece quando a classificação é **incerta**? O time quer falha fechada (vai a humano por padrão) ou aberta (vai a IA)? Quem decide e onde isso fica escrito?
2. **(A-03)** A rota clínico × IA é uma regra nomeável com consequência clínica. Quem assina a taxonomia e com que cadência ela é revisada contra os casos que o time classificou errado?
3. **(A-06)** O caso clínico sai para humano e o compromisso de prazo volta para o processo. Como o time evita perder o caso entre a rota humana e o acompanhamento do prazo?

### 4.4 S3 — Resolver ou encaminhar o caso (`Process_CA_SUPORTE_S3_ResolverEncaminhar`)

Lane **Resolução e encaminhamento** (`LN_CA3_S3_Resolucao`) — **volume: Alta** (5 tarefas humanas + 4 gateways de decisão — a lane mais densa do corpus)
- **Decide:** tipo de tratamento (`CA_3_SUPORTE_v4_adaptado.bpmn#S3_GW_Origem`, 4 saídas); se a resposta da IA tem confiança suficiente e resposta verificada (`CA_3_SUPORTE_v4_adaptado.bpmn#S3_GW_Confianca`); se o caso tem conteúdo clínico (`CA_3_SUPORTE_v4_adaptado.bpmn#S3_GW_Natureza`); se foi resolvido no primeiro contato (`CA_3_SUPORTE_v4_adaptado.bpmn#S3_GW_PrimeiroContato`); e as próprias execuções humanas de resolver (`CA_3_SUPORTE_v4_adaptado.bpmn#S3_T_Especialista`, grupo `centrais-experiencia`), abrir protocolo com prazo de retorno (`CA_3_SUPORTE_v4_adaptado.bpmn#S3_T_Protocolo`), cumprir o protocolo no prazo (`CA_3_SUPORTE_v4_adaptado.bpmn#S3_T_Cumprir`), recuperar a experiência (`CA_3_SUPORTE_v4_adaptado.bpmn#S3_T_Recovery`) e intervir na entrega (`CA_3_SUPORTE_v4_adaptado.bpmn#S3_T_Intervencao`).
- **Executa:** a resposta assistida por IA com contexto (`CA_3_SUPORTE_v4_adaptado.bpmn#S3_T_IA`) — sempre como **recomendação** revisável, nunca como decisão.
- **Acompanha/recebe:** o SLA de 2h do recovery (`CA_3_SUPORTE_v4_adaptado.bpmn#S3_B_SLARecovery`) e o alerta D-1 do prazo de protocolo (`CA_3_SUPORTE_v4_adaptado.bpmn#S3_B_D1`).
- **Pré-requisitos:** a formação mais exigente do suporte — resolução com contexto, escrita de protocolo com prazo realista, e capacidade de **auditar a resposta sugerida pela IA** antes de enviá-la; treino A-02 (prazo de retorno é compromisso, não estimativa) e A-05 (a IA responde só sob confiança + verificação).

Lane **Supervisão da experiência** (`LN_CA3_S3_Supervisao`) — **volume: Média** (2 tarefas humanas)
- **Decide:** priorizar o protocolo em risco (`CA_3_SUPORTE_v4_adaptado.bpmn#S3_T_Priorizar`, grupo `supervisao-experiencia`) e assumir a escalação ao supervisor da Central (`CA_3_SUPORTE_v4_adaptado.bpmn#S3_T_Supervisor`).
- **Executa:** a reordenação da fila e a decisão de escalação.
- **Acompanha/recebe:** o D-1 e o SLA estourado.
- **Pré-requisitos:** liderança de fila com critério de prioridade clínica escrito; autoridade real sobre as Centrais (priorizar alguém é tirar outro da frente — a decisão precisa de justificativa registrada).

Lane **Enfermagem de navegação** (`LN_CA3_S3_Enfermagem`) — **volume: Média** (1 tarefa humana, escopo sensível)
- **Decide:** resolver o caso clínico de navegação (`CA_3_SUPORTE_v4_adaptado.bpmn#S3_T_Enfermagem`, grupo `governanca-clinica`).
- **Executa:** a orientação e o encaminhamento assistencial.
- **Acompanha/recebe:** os casos que `CA_3_SUPORTE_v4_adaptado.bpmn#S3_GW_Natureza` classificou como clínicos.
- **Pré-requisitos:** enfermeiro(a) com registro ativo (COREN) e **limite de escopo escrito**: navegação orienta e encaminha; não emite parecer de cobertura nem autoriza serviço — isso é território de `CA_1_COMPRA (com suporte)_v4_adaptado.bpmn#C3_T_Parecer` e da decisão clínica humana. Esse limite é o ponto mais delicado desta lane (ver pergunta 3).

**Perguntas para discussão**
1. **(A-05)** `CA_3_SUPORTE_v4_adaptado.bpmn#S3_GW_Confianca` só deixa a IA responder com confiança suficiente **e** resposta verificada. Que competência o especialista precisa para verificar a resposta sugerida — e o que o time faz quando a verificação falha repetidamente (retreina, restringe, desliga o caminho)?
2. **(A-02)** `CA_3_SUPORTE_v4_adaptado.bpmn#S3_T_Protocolo` produz `prazoRetorno` e o alerta D-1. Quem é o dono do prazo prometido ao cliente, e o que acontece no D-1 além do alerta — quem age?
3. **(A-05)** `CA_3_SUPORTE_v4_adaptado.bpmn#S3_T_Enfermagem` resolve caso clínico. Qual é o limite de escopo da enfermagem de navegação frente ao parecer médico e à autorização — e como o cliente percebe essa diferença sem virar burocracia para ele?

### 4.5 S4 — Confirmar, encerrar e aprender (`Process_CA_SUPORTE_S4_ConfirmarEncerrar`)

Lane **Qualidade e melhoria contínua** (`LN_CA3_S4_Qualidade`) — **volume: Média** (score 2; a confirmação é do cliente)
- **Decide:** a confirmação de resolução é do cliente, em 1 toque (`CA_3_SUPORTE_v4_adaptado.bpmn#S4_T_Confirmar`, `assignee=${clienteId}`); a equipe decide com o gateway de confirmação (`CA_3_SUPORTE_v4_adaptado.bpmn#S4_GW_Resolvido`) e o de rota — já passou por especialista? (`CA_3_SUPORTE_v4_adaptado.bpmn#S4_GW_Rota`).
- **Executa:** o registro da interação para treinar a IA (`CA_3_SUPORTE_v4_adaptado.bpmn#S4_T_Aprender`) e a publicação do caso convertido em aprendizado (`CA_3_SUPORTE_v4_adaptado.bpmn#S4_EV_Aprendizado`).
- **Acompanha/recebe:** o timer de 24h sem reação (`CA_3_SUPORTE_v4_adaptado.bpmn#S4_B_Silencio`) e o fim de reencaminhamento (`CA_3_SUPORTE_v4_adaptado.bpmn#S4_End_Reencaminhar`).
- **Pré-requisitos:** análise de causa e curadoria de dataset — o "aprendizado" só vale se alguém é dono do dado e do consentimento de uso; e a decisão consciente sobre **encerrar sem confirmação** (ver pergunta 1).

Lane **Resolução e encaminhamento** (`LN_CA3_S4_Resolucao`) — **volume: Baixa** (tarefa abstrata com lane, sem binding de executor)
- **Decide:** nada modelado como decisão.
- **Executa:** o encaminhamento à ouvidoria/ANS com retorno (`CA_3_SUPORTE_v4_adaptado.bpmn#S4_T_Ouvidoria` — tarefa abstrata **com lane responsável, sem binding de executor/alçada/conclusão**).
- **Acompanha/recebe:** o caso cujo cliente contestou.
- **Pré-requisitos:** classificação do rito e prazo aplicável antes da operação; a lane já responde pelo encaminhamento, mas executor/alçada/conclusão não têm binding. Nem todo caso de Suporte é NIP formal.

**Perguntas para discussão**
1. **(A-05)** O silêncio de 24h (`CA_3_SUPORTE_v4_adaptado.bpmn#S4_B_Silencio`) encerra o caso sem confirmação. O time aceita encerrar sem o "sim, resolvido" do cliente? Com que salvaguarda (reabertura fácil, contagem como insatisfação, etc.)?
2. **(A-06)** `CA_3_SUPORTE_v4_adaptado.bpmn#S4_T_Aprender` transforma o caso em dado de treinamento. Quem é o dono do dataset, qual a base legal para usar o conteúdo do caso (que pode conter dado de saúde), e quem aprova o que entra?
3. **(A-01)** `CA_3_SUPORTE_v4_adaptado.bpmn#S4_T_Ouvidoria` prevê retorno; o rito, fonte e prazo aplicável precisam ser qualificados. Como esse compromisso é monitorado quando o caso sai para o rito regulatório — e que alerta toca quando o prazo está em risco?

---

# 5. Síntese transversal de dimensionamento

### 5.1 Lanes que atravessam processos (23 nomes em 57 instâncias)

| Lane (nome) | Processos em que aparece | Implicação de equipe |
|---|---|---|
| **Agenda e capacidade** | H2.1, C1, C4, U2 | Mecanismos podem compartilhar consulta/contrato, mas capping/horário de contato e capacidade de agenda têm políticas e autoridades diferentes; mesmo nome de lane não comprova uma equipe física |
| **Verificador de identidade** | H1, H3.1, C1, S1 | Gate único de identidade/consentimento; competência centralizada, presença em todas as frentes |
| **Disponibilização de insumos** | H1, H2.1, H3, H3.1, H3.2, S1 | Seis consumidores no desenho; reuso da interface por finalidade não determina número nem composição de equipes |
| **Dados e CRM (consolidação de perfil)** | H3, H3.1, C3 | Dono do perfil 360 — e, com ele, da resposta a pedidos de eliminação |
| **Modelagem e growth** | H3, H3.2, C1 | Mecanismo compartilhado; finalidade, regra e autoridade de oferta/cuidado continuam específicas |
| **Governança clínica** | H2, H3.2, C3 (e `CA_3_SUPORTE_v4_adaptado.bpmn#S3_T_Enfermagem` no grupo) | Revisão de mensagem, recomendação e parecer têm autoridades específicas; grupo de acesso/lane não prova mesmas pessoas nem escassez física |
| **Consultor comercial** | COMPRA, C1 | Consultoria com critério de relevância único |
| **Contratação e back-office comercial** | COMPRA, C2 | Políticas de prazo de decisão e slot reservado |
| **Cadastro e operações** | COMPRA, C3 | Habilitação e autorização — onde A-04 precisa virar procedimento |
| **Retenção e relacionamento** | COMPRA, C4 | Recovery com fronteira slot ≠ contrato |
| **Atendente de suporte** | COMPRA, UTILIZAÇÃO, SUPORTE, S1 | Quatro portas de entrada; competência de contexto pode ser reutilizada sem presumir mesmas pessoas físicas |
| **Centrais de experiência** | C3, S3 | Alternativa em negativa e resolução de caso — perfis próximos, exigências diferentes |
| **Agendamento e autorização** | UTILIZAÇÃO, U1 | A separação A-04 (preparo ≠ autorização) vive aqui |
| **Equipe assistencial (prestador)** | UTILIZAÇÃO, U2 | Terceiro — o entregável é contrato de dados e marcos |
| **Acompanhamento e gestão de cuidado** | UTILIZAÇÃO, U3, U4 | Decisão clínica humana em três pontos da esteira |
| **Liquidação e auditoria de contas** | UTILIZAÇÃO, U4 | Lane responsável; executor/alçada/conclusão de U4 ainda sem binding |
| **Analista de triagem e roteamento** | SUPORTE, S2 | Taxonomia de caso e regra de falha fechada |
| **Resolução e encaminhamento** | SUPORTE, S3, S4 | Maior score topológico em S3; S4 tem lane, mas executor/conclusão não vinculados |
| **Qualidade e melhoria contínua** | SUPORTE, S4 | Desfecho, aprendizado e reabertura |
| **Supervisão da experiência** | S3 | Priorização com justificativa registrada |
| **Enfermagem de navegação** | S3 | Escopo navegacional, não decisório de cobertura |
| **Equipe clínica e urgência** | UTILIZAÇÃO | Protocolo de contingência clínica |
| **CRM e comunicação** | H2, H2.1 | Fronteira obrigatório × informativo |

### 5.2 Pontos quentes de decisão (volume)

1. **`LN_CA3_S3_Resolucao`** — 9 pontos de decisão (5 tarefas humanas + 4 gateways): maior score topológico do corpus; efeito sobre fila e ganho de treinamento não medidos.
2. **`LN_CA1_C3_CadastroOperacoes`** — 4 gateways de negócio + 4 desfechos de espera: superfícies prioritárias para validar regra, fonte e prazo aplicável; atraso regulatório não é consequência demonstrada por contagem.
3. **`LN_CA0_H2_CrmEComunicacao`** — 4 gateways: a política de comunicação definida aqui se multiplica por toda a operação.
4. **`LN_CA3_S4_Qualidade`** — confirmação do cliente + 2 gateways + encerramento sem confirmação: o fim do ciclo decide a qualidade percebida.
5. **`LN_CA0_H32_ModelagemEGrowth` + `LN_CA0_H32_GovernancaClinica`** — o par recomendação/revisão: onde A-05 é testado a cada cálculo.

### 5.3 Prioridades sugeridas de validação e treinamento

1. **Governança clínica — qualificar alçadas e proteger decisões.** H2_T_Revisao, NBA_T_Revisao e C3_T_Parecer têm tarefas humanas com propósitos distintos; o grupo governanca-clinica não prova mesmas pessoas. Treinar limites de recomendação/decisão e o rito aplicável ao caso. Registro e competência profissional devem corresponder ao ato. O modelo não mede necessidade de ampliar vagas.
2. **Triagem e resolução do suporte (S2/S3) — a densidade de decisão do corpus.** `CA_3_SUPORTE_v4_adaptado.bpmn#S3_GW_Confianca`, `CA_3_SUPORTE_v4_adaptado.bpmn#S3_GW_Natureza`, `CA_3_SUPORTE_v4_adaptado.bpmn#S3_GW_Origem` e as cinco tarefas humanas exigem perfil de resolução com auditoria de resposta assistida por IA. Treinar taxonomia de caso clínico, escrita de protocolo com prazo e o limite de escopo da enfermagem de navegação.
3. **Agenda e contato — políticas distintas.** SC_T_Capping governa cadência/horário por frequencyCappingService; C1_T_Capacidade governa acesso por capacityPolicyService. Reutilizar mecanismos e contratos quando equivalentes, preservando finalidade, prioridade e dono de cada política. Não é evidência de um time único ou novo cargo.
4. **Ouvidoria — vincular execução à responsabilidade já desenhada.** S4_T_Ouvidoria tem lane Resolução e encaminhamento, sem binding de executor/alçada/conclusão. Qualificar rito e prazo aplicável; medir resolutividade separadamente de satisfação.
5. **Liquidação e cadastro — completar contratos e alçadas.** U4_T_Liquidar tem lane, mas não binding de executor. C3_GW_Auto/C3_GW_TipoNegativa exigem separação entre matrícula/direito-base e autorização assistencial. Atribuição formal e limites dependem do ato; contagem de tarefas não prova necessidade de contratação.

Transversal a todas: **um módulo de treinamento único sobre as seis direções de desenho** — (i) aviso com prazo legal nunca é suprimido por preferência ou limite de frequência; (ii) fato ("aconteceu") não se confunde com estimativa ("previsto"); (iii) toda decisão do fluxo tem nome e dono, e pode virar tabela; (iv) habilitar direito-base ≠ autorizar serviço; (v) negativa, decisão clínica e fraude são humanas — recomendação apenas recomenda, e dado de saúde/proxy não produz seleção de risco na contratação ou exclusão (LGPD art. 11 §5º); (vi) jornada de relacionamento e processo com auditoria são mundos diferentes, e o cliente não deveria notar a fronteira.

---

## 6. Asserts de consistência deste guia

| Assert | Resultado |
|---|---|
| Processos cobertos | **21/21** (6 CA_0 + 5 CA_1 + 5 CA_2 + 5 CA_3), cada um com seção própria e perguntas |
| LaneSets / lanes | **21 laneSets / 57 lanes** (23 nomes distintos), todas listadas com volume indicativo |
| Toda lane com Decidir/Executar/Acompanhar + pré-requisitos | Cobertura editorial nominal de 57/57; verificar conteúdo contra os bytes atuais nos gates finais |
| Perguntas por processo | **63** (3 por processo, 21 processos) |
| Processos sem userTask de equipe | 12 (H2.1, H1, H3, H3.1, COMPRA, C1, UTILIZAÇÃO, U2, U3, S1, S2, SUPORTE) — cobertura por gateways de decisão; C2/C4/U1/U4/S4 têm tarefas humanas atribuídas ao cliente |
| Tarefas humanas atribuídas ao cliente sinalizadas | 5/5 (`CA_1_COMPRA (com suporte)_v4_adaptado.bpmn#C2_T_Aceite`, `CA_1_COMPRA (com suporte)_v4_adaptado.bpmn#C4_T_Confirmar`, `CA_2_UTILIZACAO_v4_adaptado.bpmn#U1_T_CheckIn`, `CA_2_UTILIZACAO_v4_adaptado.bpmn#U4_T_Pesquisa`, `CA_3_SUPORTE_v4_adaptado.bpmn#S4_T_Confirmar`) |
| Tarefas abstratas com lane, mas sem binding executivo, sinalizadas | 2/2 (`CA_2_UTILIZACAO_v4_adaptado.bpmn#U4_T_Liquidar`, `CA_3_SUPORTE_v4_adaptado.bpmn#S4_T_Ouvidoria`) |
| Escopo desta revisão | Quatro BPMNs refinados por autores por arquivo; documentos alinhados ao resultado, sem implantação ou execução de delegates |

## 7. Leitura operacional do v4 refinado

O inventário abaixo usa os candidatos atuais lidos pelo autor documental. O manifesto final é a autoridade de custódia após gates; qualquer mudança posterior exige atualizar evidência e revalidar.

| Arquivo | Processos top-level | Lanes | Subprocessos | SHA-256 dos BPMNs lidos para este guia |
|---|---|---|---|---|
| CA_0_SERVICOS_COMPARTILHADOS_v4_adaptado.bpmn | 6 | 16 | 0 | `47d3369e1ec386121c1cd1a016480b3f24b6118ed6cb7949a0df7054ba1d8b13` |
| CA_1_COMPRA (com suporte)_v4_adaptado.bpmn | 5 | 16 | 1 | `067cf607ddb0c6647598d1b52d1237f2e1eafbbfc944aedcfbdddf91bcf12cb9` |
| CA_2_UTILIZACAO_v4_adaptado.bpmn | 5 | 12 | 3 | `7d33f1f2612c9e7eaf59c09f9d5c5630acd3bb6559f2f471b76d87067934e035` |
| CA_3_SUPORTE_v4_adaptado.bpmn | 5 | 13 | 0 | `004385a82d32e36235c46e844d16298ea8545b53450e60f2c4d36fdabd85c659` |

### Escalações automáticas introduzidas na adaptação

| Elemento automático | Lane no desenho |
|---|---|
| CA_0_SERVICOS_COMPARTILHADOS_v4_adaptado.bpmn#H2_T_EscalarRevisao | LN_CA0_H2_GovernancaClinica |
| CA_0_SERVICOS_COMPARTILHADOS_v4_adaptado.bpmn#NBA_T_EscalarRevisao | LN_CA0_H32_GovernancaClinica |
| CA_1_COMPRA (com suporte)_v4_adaptado.bpmn#C3_T_EscalarParecer | LN_CA1_C3_GovernancaClinica |
| CA_1_COMPRA (com suporte)_v4_adaptado.bpmn#C3_T_EscalarRenegociar | LN_CA1_C3_CentraisExperiencia |
| CA_3_SUPORTE_v4_adaptado.bpmn#S3_T_EscalarEspecialista | LN_CA3_S3_Resolucao |
| CA_3_SUPORTE_v4_adaptado.bpmn#S3_T_EscalarEnfermagem | LN_CA3_S3_Enfermagem |
| CA_3_SUPORTE_v4_adaptado.bpmn#S3_T_EscalarProtocolo | LN_CA3_S3_Resolucao |
| CA_3_SUPORTE_v4_adaptado.bpmn#S3_T_EscalarPriorizar | LN_CA3_S3_Supervisao |
| CA_3_SUPORTE_v4_adaptado.bpmn#S3_T_EscalarSupervisao | LN_CA3_S3_Supervisao |
| CA_3_SUPORTE_v4_adaptado.bpmn#S3_T_EscalarIntervencao | LN_CA3_S3_Resolucao |

As dez tarefas estão nas lanes existentes e seguem timers não interruptivos: o prazo dispara o ramo automático, a tarefa humana original continua aberta e nenhum parecer/decisão é substituído. A lane representa responsabilidade; destinatário, executor genérico, receipt, idempotência e canal de escalação são contratos externos ainda não comprovados. Não há delegate inventado nem claim de notificação operacional. PT4H/PT24H/PT48H/PT2H continuam propostas, inclusive onde o rótulo diz SLA.

### Eventos, tokens e retorno

- UT_ES_EventoClinico recebe mensagem e publica comunicação automaticamente com start não interruptivo; não modela conduta clínica, userTask ou suspensão da esteira. A atuação clínica requer fonte e profissional com autoridade externos ao ramo.
- UT_ES_Cancelamento mantém o cancelamento do cliente; UT_ES_CancelamentoPrestador contém o cancelamento do prestador. Ambos preservam suas mensagens e são ramos distintos, com DI próprio. Separar container não muda cancelar consumo para cancelar contrato.
- U4_GW_EscapePesquisa converge resposta ou timeout antes de U4_GW_Join. O join espera duas entradas correspondentes aos dois ramos do fork. Pesquisa ausente não é satisfação positiva nem negativa e não recebe NPS fictício. U4_T_Pesquisa declara produção pelo formulário, ainda não executado.
- C4 confirma um slot; C2 registra aceite comercial; C3 distingue matrícula/base do direito de autorização assistencial; S4 registra confirmação de resolução. Compartilhar interface/recibo não permite substituir uma decisão pela outra.
- U4_T_Liquidar e S4_T_Ouvidoria têm lanes responsáveis e tipos abstratos preservados. Executor, alçada e conclusão permanecem não vinculados; isso é diferente de ausência de equipe no desenho.
- Loops e esperas mantêm seus prazos/budgets declarados ou propostos, com contratos anotados; não se demonstra término global por ter timer nem se fabricou limite para passar checker.

As 57 lanes continuam cobertas pelas seções anteriores. O cancelamento ganhou um container, não nova lane ou novo processo top-level. C4 agenda e U4 liquidação têm score 0/Baixa; S4 qualidade tem score 2/Média. Mesmo nome de lane não impõe uma única equipe física. Antes de dimensionar, medir chegada, duração, concorrência, capacidade e gravidade.

### Fontes e limites normativos

- A atribuição anterior “RN 464/2021 = prazo de autorização” foi retirada. A RN 464/2020 tratava de processo administrativo eletrônico e foi revogada pela [RN 534/2022, art. 44 II](https://bvsms.saude.gov.br/bvs/saudelegis/ans/2022/res0534_06_05_2022.html). Isso não fixa novo timer assistencial.
- A [RN 424/2017](https://bvsms.saude.gov.br/bvs/saudelegis/ans/2017/res0424_27_06_2017.html) delimita junta/divergência técnico-assistencial. PT48H dos modelos continua proposta; duração corrida não equivale a dias úteis nem é prazo universal de negativa. Diferenciar resposta de realização antes de aplicar regra; ver [FAQ oficial ANS RN 623](https://www.gov.br/ans/pt-br/arquivos/assuntos/espaco-da-operadora-de-plano-de-saude/atendimento-ao-beneficiario/FAQ_RN_623__17.09.25.pdf/@@download/file).
- A [LGPD, art. 11 §5º](https://www.planalto.gov.br/ccivil_03/_ato2015-2018/2018/lei/l13709.htm) veda seleção de risco no contexto de contratação/exclusão, inclusive por ofertas ou proxies com esse efeito. Não torna automaticamente ilícita toda recomendação assistencial; finalidade, necessidade, base e autoridade devem ser próprias. Uma exclusão mais ampla de dados clínicos do motor comercial seria política proposta, não transcrição da lei.

Fontes conferidas em 04/10/2026, com URLs/escopo no ledger `variable-authority-audit.json`. Nenhum prazo novo foi criado ou homologado.

### Reuso incorporado e benchmarks

Os benchmarks sustentam mecanismos, não resultado quantitativo, obrigação de adotar tecnologia ou identidade física de equipes. Workers, APIs e MCPs genéricos são direção de contrato nesta devolutiva; não foram construídos.

| Sugestão | Elementos anotados | Mecanismo e referência | Limite de adaptação |
|---|---|---|---|
| RB-01 | CA_0#Process_CA_H1_IdentidadeConsentimento | Composição de capacidades — [BIAN Portal](https://bian.org/bian-portal/) e [Service Landscape 14.0](https://bian.org/deliverables/service-landscape/) | H1 tem três chamadas reais no modelo; a interface não amplia finalidade, acesso ou autoridade |
| RB-02 | CA_0#H2_T_Enviar / H2_T_Fallback | Estado da fonte, recibo e repetição — [Stripe fulfillment](https://docs.stripe.com/checkout/fulfillment.md?payment-ui=stripe-hosted) e [idempotência](https://docs.stripe.com/api/idempotent_requests) | Enviado não prova entregue; fallback é tentativa distinta, com política própria; sem copiar janela de 24h |
| RB-03 | CA_1#C2_EV_Agendado / C4_EV_Agendado | Referências e ciclo de pedido — [IATA ONE Order](https://www.iata.org/en/programs/airline-distribution/retailing/one-order/) | Promessa/reserva/confirmado são estados diferentes; journeyId não funde matrícula, slot e direito, nem prova agenda |
| RB-04 | CA_2#U2_T_Aguardar | Espera correlacionada à fonte e recibo — IATA ONE Order / Stripe fulfillment | Pedido, chegada, timeout e realização não se equivalem; regra clínica e consequência da espera são específicas |
| RB-05 | CA_3#S3_T_Cumprir / S4_T_Confirmar | Registro de retorno/recibo — BIAN / Stripe | Resposta enviada não prova resolução; confirmação de Suporte não é aceite comercial; alçada humana permanece |

Páginas primárias verificadas em 04/10/2026, com versões/datas e limites em `reuse-benchmarks.json`; BIAN 14.0 é versão do landscape. IATA/Stripe são páginas contínuas; não se declara conformidade com seus schemas, SDKs ou APIs. H1 demonstra reuso de chamada; os demais contratos são sugestões de design, sem execução comprovada ou ROI demonstrado.

*Fim do guia.*


### Revalidação pós-Modeler no encerramento — 04/10/2026

A edição posterior de Compras foi preservada e revalidada. Foram mantidas a serialização do Modeler 5.49.0 e as melhorias manuais de layout. O refinamento local corrigiu o cruzamento de `MF_CP_06` e preservou a responsabilidade original do Consultor comercial para `C1_GW_Proposta` e `C1_End_SemProposta`, mantendo a regra e os scores deste guia. Não houve alteração de condições, timers, listeners, mapeamentos, IDs ou nomes.

SHA-256 final de Compras: `067cf607ddb0c6647598d1b52d1237f2e1eafbbfc944aedcfbdddf91bcf12cb9`. Dois gates independentes de delta aprovaram estes bytes (366 verificações semânticas e 16 visuais). O bundle inicial `e73cbc80…` e os pareceres REVISE anteriores permanecem como evidência histórica; a versão final é ligada ao recibo de encerramento em `session-close/`. Esta nota não declara execução em engine, binding de executor ou homologação da equipe. A equipe autora não participou; a responsabilidade original foi a escolha conservadora de fidelidade da revisão.
