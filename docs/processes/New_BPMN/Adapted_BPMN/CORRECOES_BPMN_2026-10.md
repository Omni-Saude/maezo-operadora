# Correções e anotações nos 4 modelos BPMN — revisão de outubro de 2026

## 1. O que é este documento

Este documento é a **revisão por pares dos 4 modelos de jornada** (`CA_0_SERVICOS_COMPARTILHADOS`, `CA_1_COMPRA (com suporte)`, `CA_2_UTILIZACAO`, `CA_3_SUPORTE`). Uma verificação independente percorreu os arquivos v3, apontou desvios factuais e lacunas de contrato e, a partir daí, produziu as **versões adaptadas 1:1** (`*_v4_adaptado.bpmn`): são os **seus próprios arquivos, corrigidos e enriquecidos** — nada foi re-modelado, nada foi re-estruturado.

Três compromissos desta revisão:

- **Nada aqui vira obrigação de reestruturação.** Cada ajuste é cirúrgico: 0 elementos removidos, 0 renomeados, 0 re-tipados, 0 shapes/waypoints existentes movidos. O que já estava certo permaneceu byte a byte (businessKey, mapeamentos de `finalidade`, catálogo de mensagens/erros/sinal, anotação existente, memberships de lane).
- **A equipe decide.** Onde a evidência não existia, nada foi inventado: o ponto fica marcado como `[CONFIRMAR]` dentro do próprio modelo (seção 6) e aguarda decisão do time.
- **Como verificamos:** os 4 arquivos adaptados passam pelas mesmas verificações estruturais dos originais — 0 achados novos, catálogo de correlação idêntico, 0 elementos inalcançáveis, 0 buracos de token, cobertura completa de diagrama, 0 IDs duplicados (54 IDs novos, todos sem colisão). A verificação posterior dos arquivos encontrou dois desvios de conformidade (posição de documentation e um atributo `default` inválido), ambos já corrigidos — ver nota ao fim da seção 3. Recomendamos abrir os 4 arquivos no modeler antes de homologar: a validação por ferramenta de modelagem (importação/lint) ainda não foi executada neste ambiente.

Cada mudança nos arquivos está listada na **seção 7 (rastreio completo)**, com origem em uma correção (F-01..F-05) ou direção (A-01..A-06). Nada nos arquivos está fora dessa lista; nada nesta lista está fora dos arquivos.

---

## 2. Resumo por arquivo

Abreviaturas usadas no documento:
- **CA_0** = `CA_0_SERVICOS_COMPARTILHADOS_v4_adaptado.bpmn`
- **CA_1** = `CA_1_COMPRA (com suporte)_v4_adaptado.bpmn`
- **CA_2** = `CA_2_UTILIZACAO_v4_adaptado.bpmn`
- **CA_3** = `CA_3_SUPORTE_v4_adaptado.bpmn`

### CA_0 — Serviços compartilhados (55 ajustes)

| Origem | Ajustes | Observação |
|---|---|---|
| F-02 — sinal consumido fora do modelo | 5 | 2 de diagrama |
| F-03 — contratos de variáveis | 11 | 3 callActivities |
| F-04 — SLA não interruptivo | 22 | 10 de diagrama |
| A-01 — barreiras de contato | 1 | |
| A-02 — marcos de jornada | 2 | |
| A-03 — regras de negócio em gateways | 13 | |
| A-06 — nota de arquitetura | 1 | |
| **Total** | **55** | |

### CA_1 — Compra (com suporte) (74 ajustes)

| Origem | Ajustes | Observação |
|---|---|---|
| F-03 — contratos de variáveis | 28 | 7 callActivities; 1 linha também carrega A-05 |
| F-04 — SLA não interruptivo | 20 | 10 de diagrama |
| F-05 — erro "Sem slot" como contrato externo | 1 | |
| A-02 — marcos de jornada | 6 | |
| A-03 — regras de negócio em gateways | 13 | 2 linhas também carregam A-05 |
| A-04 — base do direito × autorização do serviço | 1 | |
| A-05 — decisões de IA e de contratação | 4 | |
| A-06 — nota de arquitetura | 1 | |
| **Total** | **74** | |

### CA_2 — Utilização (30 ajustes)

| Origem | Ajustes | Observação |
|---|---|---|
| F-01 — trava de tokens na liquidação/avaliação | 10 | 4 de diagrama |
| F-03 — contratos de variáveis | 5 | 4 callActivities |
| A-02 — marcos de jornada | 9 | |
| A-03 — regras de negócio em gateways | 5 | |
| A-06 — nota de arquitetura | 1 | |
| **Total** | **30** | |

### CA_3 — Suporte (60 ajustes)

| Origem | Ajustes | Observação |
|---|---|---|
| F-03 — contratos de variáveis | 10 | 5 callActivities |
| F-04 — SLA não interruptivo | 33 | 24 de diagrama |
| A-02 — marcos de jornada | 2 | |
| A-03 — regras de negócio em gateways | 11 | |
| A-05 — decisões de IA | 3 | |
| A-06 — nota de arquitetura | 1 | |
| **Total** | **60** | |

**Conjunto: 219 ajustes.** Por correção: F-01 = 10 · F-02 = 5 · F-03 = 54 · F-04 = 75 · F-05 = 1. Por direção: A-01 = 1 · A-02 = 19 · A-03 = 42 · A-04 = 1 · A-05 = 7 (+3 linhas compartilhadas) · A-06 = 4.

---

## 3. Correções aplicadas (F-01..F-05)

### F-01 — Trava de tokens na liquidação/avaliação (CA_2#U4)

**O que estava acontecendo.** Em `CA_2#Process_CA_UTILIZACAO_U4_LiquidarAvaliar`, o split (`U4_GW_Split`) abre dois ramos — desfecho (`U4_T_Desfecho`) e pesquisa (`U4_T_Pesquisa`) — mas o gateway paralelo de entrada `U4_GW_Join` declarava **três entradas**: `_08` (desfecho), `_09` (pesquisa concluída) e `_10` (tempo esgotado do boundary `U4_B_Pesquisa`, timer PT72H). O gateway paralelo espera um token por entrada; como **"pesquisa respondida" e "tempo esgotado" nunca coexistem** — quem responde entrega `_08`+`_09`, quem deixa estourar entrega `_08`+`_10` — a instância ficava esperando para sempre um terceiro token que nunca chega (achado P1-I0011 da auditoria). A documentação do boundary ("Timeout evita deadlock do join") descrevia o comportamento inverso do que o fluxo fazia; o texto foi preservado — a topologia é que corrige o problema.

**O que mudou.**
- Novo gateway exclusivo `CA_2#U4_GW_EscapePesquisa` — "Pesquisa respondida ou tempo esgotado?" — que **converge os dois desfechos**: recebe `_09` (pesquisa respondida) e `_10` (tempo esgotado) e entrega **um único fluxo** (`_13`) ao gateway paralelo. **Sem atributo `default`**: o gateway de escape é um merge de 2 entradas com 1 saída, e `default` só se aplica a split; os fluxos de entrada seguem sem rótulo condicional.
- Os fluxos `CA_2#F_CA_UTILIZACAO_U4_LiquidarAvaliar_09` e `_10` foram redirecionados para o novo gateway (IDs, nomes e origens intocados).
- Novo fluxo `CA_2#F_CA_UTILIZACAO_U4_LiquidarAvaliar_13` (gateway → `U4_GW_Join`), sem rótulo e sem condição.
- `CA_2#U4_GW_Join` volta a **2 entradas** (`_08`, `_13`), casando com o fork de 2 do split.
- Lane `CA_2#LN_CA2_U4_LiquidacaoAuditoria` ganhou o ref do gateway novo (append-only). Diagrama: shape do gateway + edge nova + re-roteamento dos waypoints de `_09`/`_10`; o plano `Collaboration_CA_UTILIZACAO` apenas — os planos dos sub-processos de evento ficaram byte a byte.

**Por quê assim.** O caminho do boundary é um desfecho de "não aconteceu", não uma alternativa de decisão — os dois caminhos apenas convergem antes do gateway paralelo, que fica com **2 entradas**, uma por ramo do fork. A especificação inicial previa `default = _10`; a verificação posterior apontou que `default` não se aplica a um merge (é atributo de split, que escolhe entre saídas) e o atributo foi removido. Verificamos por simulação de tokens: os dois casos (pesquisa respondida; timeout PT72H) fecham o join com exatamente 2 tokens.

### F-02 — Sinal "Alerta Time Data Science" consumido fora do modelo (CA_0#H3_EV_AlertaDS)

**O que estava acontecendo.** O sinal `Signal_CA_AlertaDS` é lançado por `CA_0#H3_EV_AlertaDS` ("Alertar cientistas de dados") e **nenhum elemento modelado o captura** — o consumo acontece no monitoramento do time de Ciência de Dados, fora do conjunto de processos.

**Decisão (contrato declarado externo).** O consumo externo passou a estar **escrito no modelo** em vez de implícito:
- documentation em `CA_0#H3_EV_AlertaDS` (consumo externo + marco factual);
- anotação `CA_0#TextAnnotation_AlertaDSExterno` — "Consumo externo: monitoramento do time de Ciência de Dados (fora do conjunto modelado). Payload a definir pelo time." — ligada por `CA_0#Association_AlertaDSExterno` (associationDirection=None, sem efeito de fluxo);
- diagrama: shape da anotação abaixo do throw + edge da associação.

O sinal **não foi renomeado e nenhum captor foi criado** — criar um catch modelaria um consumidor que não está no escopo dos 4 arquivos.

**O que a equipe deve fazer:** definir o **contrato de payload** do sinal com o time de Ciência de Dados (hoje o texto do modelo declara "a definir").

### F-03 — Contratos de variáveis nas 19 callActivities

**O que estava acontecendo.** As chamadas entre jornadas usavam `variables="all"` nas duas direções: qualquer variável de instância atravessava a fronteira sem declaração. Isso impede saber o que cada chamada realmente consome e produz, e faz variáveis internas vazarem para o chamador.

**O que mudou.** Nas **19 callActivities** do conjunto (3 em CA_0, 7 em CA_1, 4 em CA_2, 5 em CA_3):
- **38 `variables="all"` removidos** (2 por chamada: entrada e saída);
- cada direção recebeu **mapeamento explícito `source/target`** **somente** para variável com produtor comprovado no XML;
- onde o produtor **não** está comprovado, a direção ficou **vazia** (nada inventado) ou a variável foi mapeada com a marca **`[CONFIRMAR]`** na documentation (seção 6.2);
- `businessKey` (20 ocorrências) e os mapeamentos de `finalidade` (`enriquecimento`, `personalizacao,agendamento`, `atendimento`) preservados byte a byte;
- documentation "Contrato de variáveis a confirmar com a equipe" adicionada em **14 callActivities** (CA_0: H2_CA_Canal, H3_CA_Lote, EC_CA_H1 · CA_1: CP_CA_C1, CP_CA_C3, CP_CA_C4, C1_CA_H1, C1_CA_NBA, C3_CA_Perfil · CA_3: SP_CA_S1..S4, S1_CA_H1). Em CA_1, `CP_CA_C2` não recebeu a nota: sua saída (`decisaoCliente`) tem produtor comprovado.

**Princípio aplicado:** mapeamento explícito é contrato; contrato sem evidência não é preenchido — é sinalizado para o time.

### F-04 — SLA não interruptivo em 10 userTasks

**O que estava acontecendo.** Dez tarefas humanas críticas não tinham nenhum timer associado: se a pessoa não der cobertura, o caso fica parado sem sinal para ninguém (o modelo só tinha timer de recuperação no processo de suporte e nos catches de autorização).

**O que mudou.** Para cada uma das 10 tarefas, um ramo de escalonamento com o mesmo padrão:
- **boundaryEvent não interruptivo** (`cancelActivity="false"`) com timer de duração — a tarefa **continua** aberta enquanto a escalação acontece;
- **serviceTask** de escalonamento ("Escalar … pendente");
- **endEvent próprio** (sem event definition) — o ramo **não alimenta join** nem desvia o fluxo principal;
- fluxos novos, refs de lane (append-only) e shapes/edges no diagrama.

| Arquivo#tarefa (host) | Boundary novo | Timer proposto |
|---|---|---|
| CA_0#H2_T_Revisao | `H2_B_SLA_Revisao` "SLA 4h sem revisão" | PT4H `[CONFIRMAR]` |
| CA_0#NBA_T_Revisao | `NBA_B_SLA_Revisao` "SLA 24h sem revisão" | PT24H `[CONFIRMAR]` |
| CA_1#C3_T_Parecer | `C3_B_SLA_Parecer` "SLA 48h sem parecer" | PT48H `[CONFIRMAR]` |
| CA_1#C3_T_Renegociar | `C3_B_SLA_Renegociar` "SLA 24h sem alternativa" | PT24H `[CONFIRMAR]` |
| CA_3#S3_T_Especialista | `S3_B_SLA_Especialista` "SLA 2h estourado" | PT2H `[CONFIRMAR]` |
| CA_3#S3_T_Enfermagem | `S3_B_SLA_Enfermagem` "SLA 2h estourado" | PT2H `[CONFIRMAR]` |
| CA_3#S3_T_Protocolo | `S3_B_SLA_Protocolo` "SLA 2h sem protocolo" | PT2H `[CONFIRMAR]` |
| CA_3#S3_T_Priorizar | `S3_B_SLA_Priorizar` "SLA 4h sem priorização" | PT4H `[CONFIRMAR]` |
| CA_3#S3_T_Supervisor | `S3_B_SLA_Supervisor` "SLA 24h sem ação" | PT24H `[CONFIRMAR]` |
| CA_3#S3_T_Intervencao | `S3_B_SLA_Intervencao` "SLA 4h sem intervenção" | PT4H `[CONFIRMAR]` |

Cada boundary carrega na documentation a nota **"SLA proposto — confirmar com a equipe"** com o fundamento usado (convenção dos próprios modelos ou proposta de janela). Os **valores são propostos, não determinados** — ver seção 6.1 e 6.7.

### F-05 — Erro "Sem slot" declarado como contrato externo (CA_1#C4_B_SemSlot)

**O que estava acontecendo.** O boundary `CA_1#C4_B_SemSlot` captura o erro `SEM_SLOT_EQUIVALENTE`, que a documentação do modelo declara ser lançado pelo serviço de **reagendamento automático** (`autoRebookingService`). A auditoria não conseguiu comprovar essa execução — a recomendação heurística (P1-RAW005) pede **implementação/teste de erro antes de incorporar** o comportamento.

**Decisão (contrato declarado externo).** O boundary **não é um artefato morto**: o erro tem origem declarada fora do fluxo modelado. Mantivemos o wiring intacto (boundary + errorEventDefinition + fluxo de saída) e adicionamos documentation registrando o **contrato do erro** (quem lança, qual código, qual desfecho). Nada foi reconfigurado.

**O que a equipe deve fazer:** garantir o **teste de erro do reagendamento automático** — é a evidência que falta para tratar esse contrato como comprovado em vez de declarado.

### Nota — dois ajustes de conformidade aplicados após a verificação independente

A verificação posterior dos arquivos encontrou dois desvios, ambos já reparados — o ciclo de revisão funcionando como esperado, sem alterar a lógica modelada:

1. **CA_3 — posição de documentation:** as 5 documentations de contrato de variáveis (`SP_CA_S1`, `SP_CA_S2`, `SP_CA_S3`, `SP_CA_S4`, `S1_CA_H1`) foram reposicionadas para **antes de `extensionElements`**, exigência da sequência do esquema BPMN 2.0; conteúdo idêntico.
2. **CA_2 — atributo `default` removido** do gateway de escape `U4_GW_EscapePesquisa`: `default` só se aplica a gateway de split (que escolhe entre saídas); o escape é um merge de 2 entradas com 1 saída. A especificação inicial pedia `default = _10` — estava errada e foi corrigida.

Os arquivos refletem o estado final; a seção 7 já descreve esses dois pontos como ficaram.

---

## 4. Direções incorporadas como anotações (A-01..A-06)

Todas as direções foram incorporadas **exclusivamente como documentation** (e, em F-02, uma `textAnnotation`). **Nenhuma mudança de fluxo decorre delas** — nenhum gateway re-condicionado, nenhum caminho novo, nenhuma tarefa re-tipada.

### A-01 — Barreiras de contato (1 anotação)
Documentation no processo `CA_0#Process_CA_H2_ComunicarCliente`: opt-in/base legal por categoria, frequency capping e horário silencioso, limiar de confiança com revisão humana e respeito à decisão de envio (enviar, adiar ou suprimir) — toda nova comunicação deve passar por essas barreiras. Os gateways que implementam cada barreira já existiam no modelo e seguem intactos.

### A-02 — Marcos de jornada (19 anotações)
Documentation nos elementos que emitem evento, distinguindo **marco factual** ("registra o que ACONTECEU com o cliente, não estimativa nem previsão") de **previsão**: `CA_2#U3_EV_Atraso` recebe o rótulo de **predição** (antecipa o que PODE acontecer) e `CA_2#U2_EV_Escalar` o de **escalação factual** (o SLA violado aconteceu no instante do evento). Alvos: CA_0 — H3_EV_AlertaDS, H3_EV_Recomendar · CA_1 — C2_EV_Lembrete (nota de lembrete agendado/previsão de ciclo), C2_EV_Agendado, C3_EV_Autorizado, C3_EV_EmAnalise, C4_EV_Lembrete, C4_EV_Agendado · CA_2 — UT_EC_Comunicar, U1_EV_Lembrete, U1_EV_Pronto, U2_EV_EmAtendimento, U2_EV_Escalar, U2_EV_Realizado, U3_EV_Atraso, U3_EV_Resultado, U4_EV_ProxAcao · CA_3 — S1_EV_Cuidando, S4_EV_Aprendizado. Critério objetivo para o time ao nomear novos eventos: emitir "Marco: …" só para o que de fato ocorreu.

### A-03 — Regras de negócio nos gateways de decisão (42 anotações)
Cada XOR de decisão recebeu documentation no formato **"Regra: … — dono: …"**, com a regra e a lane responsável: CA_0 — H2_GW_Tipo, H2_GW_Confiavel, H2_GW_Envio, H2_GW_Entregue, SC_GW_OptIn, SC_GW_Critico, SC_GW_Decisao, H1_GW_Consentimento, H1_GW_Identidade, H3_GW_Modelo, H3_GW_Nutrir, NBA_GW_ColdStart, NBA_GW_Clinico · CA_1 — CP_GW_Proposta, CP_GW_SemOferta, CP_GW_Decisao, CP_GW_Habilitado, CP_GW_TipoDireito, CP_GW_Manutencao, C1_GW_Proposta, C3_GW_Tipo, C3_GW_Auto, C3_GW_Pendencia, C3_GW_TipoNegativa, C3_GW_Alternativa, C4_GW_Motivo · CA_2 — UT_GW_Status, UT_GW_Satisfacao, U1_GW_Preparo, U3_GW_Resultado, U4_GW_ProxAcao · CA_3 — SP_GW_Origem, SP_GW_Compra, SP_GW_Desfecho, S1_GW_Iniciativa, S2_GW_Clinico, S3_GW_Origem, S3_GW_Confianca, S3_GW_Natureza, S3_GW_PrimeiroContato, S4_GW_Resolvido, S4_GW_Rota. Os textos com regra já existente na documentation do elemento foram **acrescidos após ela** — nenhum texto seu foi substituído. **As condições dos fluxos não foram tocadas**; onde a regra escrita divergir da condição implementada, é isso que o time deve revisar.

### A-04 — Base do direito × autorização do serviço (1 anotação)
Documentation no processo `CA_1#Process_CA_COMPRA_C3_HabilitarDireito`: C3 separa **habilitação da base do direito** (tipo de direito, elegibilidade, direito ao plano) de **autorização do serviço** (autorização automática, requisição, pendências, negativas, parecer). Recomendação registrada: alterações de autorização não devem reusar caminhos de base, e vice-versa.

### A-05 — Decisões de IA e de contratação (10 anotações)
Guard-rails registrados onde a decisão acontece. **IA** — "a IA apenas recomenda; triagem clínica, negativa e suspeita de fraude são sempre humanas. Proibido usar dados de saúde para seleção de risco (LGPD art. 11 §5º)": `CA_1#C1_CA_NBA`, `CA_3#S2_GW_Clinico`, `CA_3#S3_GW_Confianca`, `CA_3#S3_T_IA`. **Contratação** — "a decisão de contratar é do cliente/humano; a automação prepara a decisão, nunca decide": `CA_1#C2_T_Aceite`, `CA_1#C2_B_PrazoMax`, `CA_1#C2_B_Lembrete`, `CA_1#C2_B_Recusa`, e junto da regra de roteamento em `CA_1#CP_GW_Decisao` e `CA_1#C1_GW_Proposta`. Em CA_1, a chamada do recomendador (`C1_CA_NBA`) concentra contrato de variáveis + guard-rail.

### A-06 — Nota de arquitetura por arquivo (4 anotações)
Documentation na colaboração de cada arquivo, descrevendo o que ele concentra, a quem chama e quais transições de jornada são contrato de fronteira: `CA_0#Collaboration_CA_SERVICOS` (H1/H2/H3; dependência de implantação: publicar este arquivo antes dos chamadores), `CA_1#Collaboration_CA_COMPRA` (T-01, T-02/T-03, T-06, T-07, T-09, T-10), `CA_2#Collaboration_CA_UTILIZACAO` (inclui a **retenção das 9 interseções inter-plano**, seção 5), `CA_3#Collaboration_CA_SUPORTE` (T-05, T-07, T-09, T-10; ouvidoria fora do conjunto modelado).

---

## 5. O que NÃO mudou (e por quê)

- **As fusões legítimas (25 gateways)** — 7 em CA_0 (H2_GW_MergeRevisao, SC_GW_MergeEnviar, H1_GW_Merge, H3_GW_MergeEvt, EC_GW_Join, NBA_GW_Merge, NBA_GW_MergeFim), 7 em CA_1 (CP_GW_MergeIntencao, CP_GW_MergeHabilitar, CP_GW_MergeSuporte, C3_GW_MergeEleg, C3_GW_MergeAut, C3_GW_MergeReq, C3_GW_MergeReneg), 4 em CA_2 (U1_GW_Merge, U3_GW_Merge, U4_GW_MergeProx, U4_GW_Join), 7 em CA_3 (SP_GW_MergeInicio, SP_GW_MergeResolver, S1_GW_MergeCons, S1_GW_Merge, S3_GW_MergeHumano, S3_GW_MergeAtend, S4_GW_MergeAprender). Gateway com mais de uma entrada e uma saída é padrão do modelo para unir ramos convergentes — não é defeito. A única exceção foi o `U4_GW_Join`, corrigido pelo **F-01** porque suas entradas eram mutuamente exclusivas.
- **`historyTimeToLive="180"` já estava presente** em 21 processos do conjunto — nada foi adicionado nem alterado.
- **A composição das lanes foi preservada.** Os 297 refs originais (82 em CA_0, 92 em CA_1, 57 em CA_2, 66 em CA_3) permanecem na ordem original; os refs novos foram **acrescidos ao fim** da lane correspondente. `CA_1#CP_ES_Ajuda` segue sem lanes internas (herda a lane do atendente) — não criamos lanes agora.
- **As 9 interseções de coordenadas entre planos (CA_2)** foram mantidas. São os achados P1-DI-2-01..09 da auditoria: sobreposições entre o plano principal e os planos dos sub-processos de evento (`Participant_CompraSup`, `LN_CA2_UTILIZACAO_AgendamentoAutorizacao`, `UT_ES_Start_CancCliente`, `UT_ES_End_CancCliente` × elementos dos eventos). Conhecidas, herdadas do layout original e não reproduzidas visualmente (renderer indisponível); os dois planos de evento ficaram **byte a byte**. Quando houver elementos novos nesses planos, regenere apenas o plano principal.
- **O erro "Sem slot" NÃO é boundary morto.** O serviço de reagendamento automático lança `SEM_SLOT_EQUIVALENTE` conforme a documentação do próprio modelo; a auditoria apenas não pôde comprovar a execução — por isso o tema virou o contrato externo do **F-05** (com teste de erro pendente), e não uma remoção do boundary.
- **`CA_2#U4_T_Liquidar` e `CA_3#S4_T_Ouvidoria` permanecem como o time deixou** (tarefas abstratas; achados P1-I0016 e P1-I0022). Re-tipar exige decisão da process owner sobre o comportamento e o aceite — não é algo que a revisão decida sozinha.
- **Rótulos editoriais e a anotação existente foram preservados:** `CA_0#F_CA_H2_ComunicarCliente_09` e `CA_0#F_CA_H3_ProximaMelhorAcao_12` (rótulos apontados pelo lint como supérfluos, mantidos por opção editorial) e `CA_3#TextAnnotation_16ktuur` + `CA_3#Association_11mohme` (a anotação do corpus, mantida byte a byte como referência de estilo — foi o padrão usado na anotação nova do F-02).
- **O catálogo de correlação ficou idêntico:** nenhuma message, error, signal ou escalation renomeada (CA_0: 2 messages + 1 error + 1 signal · CA_1: 15 messages + 2 errors · CA_2: 13 messages + 1 escalation · CA_3: 6 messages + 1 error).

---

## 6. Pendências para a equipe decidir

Tudo abaixo está marcado `[CONFIRMAR]` dentro dos próprios modelos. Nenhuma dessas pendências bloqueia a leitura dos arquivos — bloqueia apenas fechar o contrato.

### 6.1 Valores de SLA propostos (10)

Os ramos do F-04 foram criados com valores **propostos** a partir de convenções dos próprios modelos — nenhum prazo foi tratado como definitivo:

| # | Elemento | Valor proposto | Fundamento |
|---|---|---|---|
| 1 | CA_0#H2_B_SLA_Revisao | PT4H | janela de decisão de envio no mesmo ciclo (o arquivo não tem SLA literal) |
| 2 | CA_0#NBA_B_SLA_Revisao | PT24H | alinhado ao ciclo diário (start timer 03:00 em H3) |
| 3 | CA_1#C3_B_SLA_Parecer | PT48H | mesmo prazo do catch de autorização (`C3_EV_Expirou`, PT48H) |
| 4 | CA_1#C3_B_SLA_Renegociar | PT24H | ritmo de lembrete 24h do modelo (`C2_B_Lembrete`/`C4_B_Lembrete`) |
| 5 | CA_3#S3_B_SLA_Especialista | PT2H | mesmo SLA literal do processo (`S3_B_SLARecovery`, PT2H) |
| 6 | CA_3#S3_B_SLA_Enfermagem | PT2H | idem |
| 7 | CA_3#S3_B_SLA_Protocolo | PT2H | idem |
| 8 | CA_3#S3_B_SLA_Priorizar | PT4H | proposta: meia jornada de supervisão |
| 9 | CA_3#S3_B_SLA_Supervisor | PT24H | convenção 24h do modelo (`S4_B_Silencio`, PT24H) |
| 10 | CA_3#S3_B_SLA_Intervencao | PT4H | proposta: entrega em risco |

### 6.2 Variáveis sem produtor comprovado (32, por callActivity)

Mapeadas com `[CONFIRMAR]` na documentation da chamada. O pedido ao time é simples: para cada uma, dizer **quem produz** (tarefa, formulário ou serviço externo) — ou pedir a remoção do mapeamento.

| CallActivity (arquivo) | Variáveis a confirmar | Qtd. |
|---|---|---|
| CA_0#H2_CA_Canal | categoriaPermitida, urgencia, envioPermitidoAgora, proximaJanela | 4 |
| CA_0#EC_CA_H1 | consentimentoValido, identidadeUnificada | 2 |
| CA_1#CP_CA_C1 | propostaRelevante | 1 |
| CA_1#CP_CA_C3 | autorizacaoAutomatica, pendenciaResolvida, tentativasAutorizacao, negativaDeCobertura, clienteAceitouAlternativa, clienteSolicitouSuporte | 6 |
| CA_1#CP_CA_C4 | statusEntrega | 1 |
| CA_1#C1_CA_H1 | consentimentoValido, identidadeUnificada | 2 |
| CA_1#C1_CA_NBA | historicoSuficiente, tierClinico | 2 |
| CA_2#UT_CA_U1 | requerPreparo | 1 |
| CA_2#UT_CA_U3 | geraResultado | 1 |
| CA_2#UT_CA_U4 | proximaAcaoIndicada | 1 |
| CA_3#SP_CA_S2 | questaoClinica | 1 |
| CA_3#SP_CA_S3 | rotaAtendimento, confiancaIA, limiarConfianca, respostaVerificada, naturezaCaso, resolvidoPrimeiroContato | 6 |
| CA_3#SP_CA_S4 | rotaAtendimento, clienteConfirmouResolucao | 2 |
| CA_3#S1_CA_H1 | consentimentoValido, identidadeUnificada | 2 |
| **Total** | | **32** |

Nota: `consentimentoValido`/`identidadeUnificada` repetem-se porque as três chamadas a H1 (EC_CA_H1, C1_CA_H1, S1_CA_H1) compartilham o mesmo contrato do callee — resolver uma vez, replicar nas três.

### 6.3 CallActivities com SAÍDA vazia (6)

Sem produtor comprovado, a saída ficou **vazia de propósito**. Se houver saída esperada, apontar o produtor:

- `CA_0#H3_CA_Lote` (EnriquecerCliente)
- `CA_1#CP_CA_C1` (EntenderRecomendar)
- `CA_1#CP_CA_C4` (ManterDireito)
- `CA_1#C1_CA_NBA` (chamada do recomendador)
- `CA_1#C3_CA_Perfil` (EnriquecerCliente)
- `CA_3#SP_CA_S3` (ResolverEncaminhar)

### 6.4 GAP — `experienciaSatisfatoria` / `npsScore` (CA_2#UT_CA_U4)

A documentação de `CA_2#U4_T_Pesquisa` afirma produzir `npsScore`/`experienciaSatisfatoria`, mas **nenhum produtor declarado existe no modelo** — e o chamador lê `experienciaSatisfatoria` (`CA_2#F_CA_UTILIZACAO_10`). Não inferimos produtor de documentação. Pendência: definir se a produção acontece em formulário, serviço ou se a leitura é remanescente.

### 6.5 GAP — `prazoMaximoDecisao` (CA_1#C2_B_PrazoMax)

O timer lê `prazoMaximoDecisao`, que não recebe mapping em `CP_CA_C2` — por desenho desta revisão (a entrada de C2 ficou vazia por regra e não inferimos produtor). Pendência: definir onde a variável nasce.

### 6.6 Consumo das saídas a confirmar (mapeamentos possivelmente sem leitura)

Saídas mapeadas com produtor comprovado, mas que **o chamador não lê em condição/entrada**. Podem estar corretas (uso em payload de mensagem) ou ser remanescentes — confirmar antes de remover:

`decisaoCliente` (CA_1#CP_CA_C2) · `marcoJornada` (CA_1#CP_CA_C3; CA_2#UT_CA_U1, UT_CA_U2, UT_CA_U3) · `transicao` (CA_2#UT_CA_U4 — viaja como payload da mensagem de próxima ação) · `urgenciaSugerida` (CA_3#SP_CA_S1) · `tipoEventoNegocio` (CA_3#SP_CA_S4) · `rotaAtendimento` (CA_3#SP_CA_S2 → entrada de SP_CA_S3/SP_CA_S4, corrente entre etapas; em SP_CA_S4 mantida por esse contrato de corrente) · `consentimentoValido`/`identidadeUnificada` (as três chamadas a H1, seção 6.2).

### 6.7 Timers propostos × prazos contratuais

Nenhum valor da seção 6.1 foi confrontado com norma ou contrato. Caso específico: `CA_1#C3_T_Parecer` cita **RN 424/2017** na documentação do host — o prazo de 48h proposto para o boundary deve ser validado contra o prazo regulatório/contratual real da negativa com parecer médico. Os demais valem a mesma conferência quando houver SLA contratual equivalente.

---

## 7. Rastreio completo (219 linhas)

Colunas: elemento (formato `arquivo#elementoId`) · operação · antes→depois · origem · evidência (linha no arquivo adaptado; `A:` = adaptado, `O:` = original v3). **Prova de completude: as 219 linhas abaixo correspondem 1:1 às 219 linhas dos quatro registros de mudança (55 + 74 + 30 + 60); cada linha carrega um ID de origem (F-xx / A-0x); zero linhas sem origem; zero linha extra.**

### 7.1 CA_0 — Serviços compartilhados (55)

| Elemento | Operação | Antes → depois | Origem | Evidência |
|---|---|---|---|---|
| CA_0#H3_EV_AlertaDS | documentar | + documentation de consumo externo do sinal | F-02 | 533 |
| CA_0#TextAnnotation_AlertaDSExterno | adicionar (textAnnotation) | inexistente → texto curto PT-BR do plano de consumo externo (estilo TextAnnotation_16ktuur) | F-02 | 608 |
| CA_0#Association_AlertaDSExterno | adicionar (association) | inexistente → H3_EV_AlertaDS→TextAnnotation_AlertaDSExterno, associationDirection=None | F-02 | 611 |
| CA_0#H2_CA_Canal | remover-atributo | `<camunda:in variables="all"/>` → 4 in source/target explícitos (categoriaPermitida, urgencia, envioPermitidoAgora, proximaJanela) | F-03 | 115 |
| CA_0#H2_CA_Canal | remover-atributo | `<camunda:out variables="all"/>` → 1 out source/target explícito (decisaoEnvio) | F-03 | 119 |
| CA_0#H2_CA_Canal | documentar | + documentation de contrato de variáveis a confirmar | F-03 | 112 |
| CA_0#H2_CA_Canal | reter | `businessKey` preservado byte a byte | F-03 | 114 |
| CA_0#H3_CA_Lote | remover-atributo | in/out `variables="all"` removidos; nenhum mapping novo (entrada/saída vazias por regra) | F-03 | 495 |
| CA_0#H3_CA_Lote | documentar | + documentation de contrato de variáveis | F-03 | 497 |
| CA_0#H3_CA_Lote | reter | `businessKey` preservado byte a byte | F-03 | 499 |
| CA_0#EC_CA_H1 | remover-atributo | `<camunda:in variables="all"/>` removido; entrada vazia por regra (callee H1 lê só variáveis de delegado internas) | F-03 | 643 |
| CA_0#EC_CA_H1 | remover-atributo | `<camunda:out variables="all"/>` → 2 out source/target explícitos (consentimentoValido, identidadeUnificada) `[CONFIRMAR]` | F-03 | 649 |
| CA_0#EC_CA_H1 | reter | `businessKey` + `<camunda:in sourceExpression="enriquecimento" target="finalidade"/>` preservados byte a byte | F-03 | 648 |
| CA_0#EC_CA_H1 | documentar | + documentation de contrato de variáveis | F-03 | 645 |
| CA_0#H2_B_SLA_Revisao | adicionar (boundaryEvent) | inexistente → não interruptivo (`cancelActivity="false"`), attachedToRef=H2_T_Revisao, PT4H `[valor PROPOSTO]` | F-04 | 90 |
| CA_0#H2_T_EscalarRevisao | adicionar (serviceTask) | inexistente → "Escalar revisão clínica pendente" | F-04 | 96 |
| CA_0#H2_End_SLA_Revisao | adicionar (endEvent) | inexistente → "Revisão escalada — mensagem retida", sem event definition (ramo NÃO alimenta join) | F-04 | 100 |
| CA_0#F_CA_H2_ComunicarCliente_22 | adicionar (sequenceFlow) | inexistente → H2_B_SLA_Revisao→H2_T_EscalarRevisao | F-04 | 225 |
| CA_0#F_CA_H2_ComunicarCliente_23 | adicionar (sequenceFlow) | inexistente → H2_T_EscalarRevisao→H2_End_SLA_Revisao | F-04 | 226 |
| CA_0#LN_CA0_H2_GovernancaClinica | adicionar (flowNodeRef) | 3 refs acrescentados ao fim da lane (nenhum ref existente movido) | F-04 | 50 |
| CA_0#NBA_B_SLA_Revisao | adicionar (boundaryEvent) | inexistente → não interruptivo, attachedToRef=NBA_T_Revisao, PT24H `[valor PROPOSTO]` | F-04 | 886 |
| CA_0#NBA_T_EscalarRevisao | adicionar (serviceTask) | inexistente → "Escalar revisão de recomendação pendente" | F-04 | 892 |
| CA_0#NBA_End_SLA_Revisao | adicionar (endEvent) | inexistente → "Revisão escalada — recomendação retida", sem event definition | F-04 | 896 |
| CA_0#F_CA_H3_ProximaMelhorAcao_14 | adicionar (sequenceFlow) | inexistente → NBA_B_SLA_Revisao→NBA_T_EscalarRevisao | F-04 | 925 |
| CA_0#F_CA_H3_ProximaMelhorAcao_15 | adicionar (sequenceFlow) | inexistente → NBA_T_EscalarRevisao→NBA_End_SLA_Revisao | F-04 | 926 |
| CA_0#LN_CA0_H32_GovernancaClinica | adicionar (flowNodeRef) | 3 refs acrescentados ao fim da lane (nenhum ref existente movido) | F-04 | 812 |
| CA_0#Process_CA_H2_ComunicarCliente | documentar | + documentation "Barreiras de contato: …" | A-01 | 23 |
| CA_0#H3_EV_AlertaDS | documentar | + documentation de marco factual | A-02 | 534 |
| CA_0#H3_EV_Recomendar | documentar | + documentation de marco factual | A-02 | 562 |
| CA_0#Collaboration_CA_SERVICOS | documentar | + documentation de nota de arquitetura (primeiro filho da colaboração) | A-06 | 4 |
| CA_0#H2_GW_Tipo | documentar | + documentation "Regra: … — dono: …" após a documentation existente | A-03 | 67 |
| CA_0#H2_GW_Confiavel | documentar | + documentation "Regra: … — dono: …" após a documentation existente | A-03 | 80 |
| CA_0#H2_GW_Envio | documentar | + documentation "Regra: … — dono: …" após a documentation existente | A-03 | 131 |
| CA_0#H2_GW_Entregue | documentar | + documentation "Regra: … — dono: …" após a documentation existente | A-03 | 156 |
| CA_0#SC_GW_OptIn | documentar | + documentation "Regra: … — dono: …" após a documentation existente | A-03 | 272 |
| CA_0#SC_GW_Critico | documentar | + documentation "Regra: … — dono: …" após a documentation existente | A-03 | 291 |
| CA_0#SC_GW_Decisao | documentar | + documentation "Regra: … — dono: …" após a documentation existente | A-03 | 303 |
| CA_0#H1_GW_Consentimento | documentar | + documentation "Regra: … — dono: …" após a documentation existente | A-03 | 390 |
| CA_0#H1_GW_Identidade | documentar | + documentation "Regra: … — dono: …" após a documentation existente | A-03 | 412 |
| CA_0#H3_GW_Modelo | documentar | + documentation "Regra: … — dono: …" após a documentation existente | A-03 | 517 |
| CA_0#H3_GW_Nutrir | documentar | + documentation "Regra: … — dono: …" após a documentation existente | A-03 | 555 |
| CA_0#NBA_GW_ColdStart | documentar | + documentation "Regra: … — dono: …" após a documentation existente | A-03 | 843 |
| CA_0#NBA_GW_Clinico | documentar | + documentation "Regra: … — dono: …" após a documentation existente | A-03 | 876 |
| CA_0#H2_B_SLA_Revisao_di | adicionar (BPMNShape) | shape adjacente ao host H2_T_Revisao (boundary cobre a borda inferior do host; demais ao lado, dentro da faixa da lane) | F-04 (DI) | 1046 |
| CA_0#H2_T_EscalarRevisao_di | adicionar (BPMNShape) | shape adjacente ao host, dentro da faixa da lane | F-04 (DI) | 1049 |
| CA_0#H2_End_SLA_Revisao_di | adicionar (BPMNShape) | shape adjacente ao host, dentro da faixa da lane | F-04 (DI) | 1052 |
| CA_0#F_CA_H2_ComunicarCliente_22_di | adicionar (BPMNEdge) | edge nova, ≥2 waypoints; parte do centro-inferior do boundary | F-04 (DI) | 1185 |
| CA_0#F_CA_H2_ComunicarCliente_23_di | adicionar (BPMNEdge) | edge nova, ≥2 waypoints | F-04 (DI) | 1191 |
| CA_0#NBA_B_SLA_Revisao_di | adicionar (BPMNShape) | shape adjacente ao host NBA_T_Revisao | F-04 (DI) | 1744 |
| CA_0#NBA_T_EscalarRevisao_di | adicionar (BPMNShape) | shape adjacente ao host | F-04 (DI) | 1747 |
| CA_0#NBA_End_SLA_Revisao_di | adicionar (BPMNShape) | shape adjacente ao host | F-04 (DI) | 1750 |
| CA_0#F_CA_H3_ProximaMelhorAcao_14_di | adicionar (BPMNEdge) | edge nova, ≥2 waypoints | F-04 (DI) | 1816 |
| CA_0#F_CA_H3_ProximaMelhorAcao_15_di | adicionar (BPMNEdge) | edge nova, ≥2 waypoints | F-04 (DI) | 1822 |
| CA_0#TextAnnotation_AlertaDSExterno_di | adicionar (BPMNShape) | anotação abaixo do throw H3_EV_AlertaDS, dentro da faixa da lane ModelagemEGrowth | F-02 (DI) | 1993 |
| CA_0#Association_AlertaDSExterno_di | adicionar (BPMNEdge) | edge da associação (2 waypoints, verticais throw→anotação) | F-02 (DI) | 1997 |

### 7.2 CA_1 — Compra (com suporte) (74)

| Elemento | Operação | Antes → depois | Origem | Evidência |
|---|---|---|---|---|
| CA_1#CP_CA_C1 | remoção de mapping | `<camunda:in variables="all"/>` → removido | F-03 | 128 |
| CA_1#CP_CA_C1 | remoção de mapping | `<camunda:out variables="all"/>` → removido | F-03 | — |
| CA_1#CP_CA_C1 | mapping explícito | (vazio) → `<camunda:in source="propostaRelevante" target="propostaRelevante"/>` | F-03 | 128 |
| CA_1#CP_CA_C1 | documentation | (só "Origem: ARQ-01 C1") → + "Contrato de variáveis a confirmar com a equipe: …" | F-03 (add-on) | 124 |
| CA_1#CP_CA_C2 | remoção de mapping | `<camunda:in variables="all"/>` → removido | F-03 | 175 |
| CA_1#CP_CA_C2 | remoção de mapping | `<camunda:out variables="all"/>` → removido | F-03 | — |
| CA_1#CP_CA_C2 | mapping explícito | (vazio) → `<camunda:out source="decisaoCliente" target="decisaoCliente"/>` | F-03 | 175 |
| CA_1#CP_CA_C3 | remoção de mapping | `<camunda:in variables="all"/>` → removido | F-03 | 228 |
| CA_1#CP_CA_C3 | remoção de mapping | `<camunda:out variables="all"/>` → removido | F-03 | — |
| CA_1#CP_CA_C3 | mapping explícito (7 entradas) | (vazio) → in tipoDireito, autorizacaoAutomatica, pendenciaResolvida, tentativasAutorizacao, negativaDeCobertura, clienteAceitouAlternativa, clienteSolicitouSuporte | F-03 | 228–234 |
| CA_1#CP_CA_C3 | mapping explícito (4 saídas) | (vazio) → out direitoHabilitado, encaminharSuporte, motivoDificuldade, marcoJornada | F-03 | 235–238 |
| CA_1#CP_CA_C3 | documentation | (só "Origem: ARQ-01 C3") → + "Contrato de variáveis a confirmar com a equipe: …" | F-03 (add-on) | 227 |
| CA_1#CP_CA_C4 | remoção de mapping | `<camunda:in variables="all"/>` → removido | F-03 | 298 |
| CA_1#CP_CA_C4 | remoção de mapping | `<camunda:out variables="all"/>` → removido | F-03 | — |
| CA_1#CP_CA_C4 | mapping explícito | (vazio) → `<camunda:in source="statusEntrega" target="statusEntrega"/>` | F-03 | 298 |
| CA_1#CP_CA_C4 | documentation | (só "Origem: ARQ-01 C4") → + "Contrato de variáveis a confirmar com a equipe: …" | F-03 (add-on) | 296 |
| CA_1#C1_CA_H1 | remoção de mapping | `<camunda:in variables="all"/>` → removido | F-03 | 503 |
| CA_1#C1_CA_H1 | remoção de mapping | `<camunda:out variables="all"/>` → removido | F-03 | — |
| CA_1#C1_CA_H1 | mapping preservado | `in sourceExpression="personalizacao,agendamento" target="finalidade"` → byte a byte | F-03 (preservar) | 504 |
| CA_1#C1_CA_H1 | mapping explícito (2 saídas) | (vazio) → out consentimentoValido, identidadeUnificada | F-03 | 505–506 |
| CA_1#C1_CA_H1 | documentation | (só "Origem: M2_CA_Consentimento + …") → + "Contrato de variáveis a confirmar com a equipe: …" | F-03 (add-on) | 501 |
| CA_1#C1_CA_NBA | remoção de mapping | `<camunda:in variables="all"/>` → removido | F-03 | 520 |
| CA_1#C1_CA_NBA | remoção de mapping | `<camunda:out variables="all"/>` → removido | F-03 | — |
| CA_1#C1_CA_NBA | mapping explícito (2 entradas) | (vazio) → in historicoSuficiente, tierClinico | F-03 | 520–521 |
| CA_1#C1_CA_NBA | documentation ×2 | (só "Origem: M1_CA_NBA") → + "Contrato de variáveis a confirmar com a equipe: …" e + guard-rail de IA (LGPD art. 11 §5º) | F-03 (add-on) + A-05 | 518–519 |
| CA_1#C3_CA_Perfil | remoção de mapping | `<camunda:in variables="all"/>` → removido | F-03 | 784 |
| CA_1#C3_CA_Perfil | remoção de mapping | `<camunda:out variables="all"/>` → removido | F-03 | — |
| CA_1#C3_CA_Perfil | documentation | (só "Origem: M1_CA_EnriquecerCliente") → + "Contrato de variáveis a confirmar com a equipe: …" (entrada 0 / saída 0) | F-03 (add-on) | 782 |
| CA_1#C4_B_SemSlot | documentation | (só "RT-04 [PROPOSTO]. …") → + "Contrato do erro (externo ao fluxo): … autoRebookingService … SEM_SLOT_EQUIVALENTE …" — wiring intocado | F-05 | 1088–1089 |
| CA_1#C3_T_Parecer (host) | boundary novo | (não existia) → `C3_B_SLA_Parecer` "SLA 48h sem parecer" `cancelActivity="false"` attachedToRef="C3_T_Parecer" + timeDuration PT48H + doc "[CONFIRMAR]" | F-04 | 926–932 |
| CA_1#C3_T_Parecer (host) | serviceTask nova | (não existia) → `C3_T_EscalarParecer` "Escalar parecer médico pendente" | F-04 | 933–936 |
| CA_1#C3_T_Parecer (host) | endEvent novo | (não existia) → `C3_End_SLA_Parecer` "Parecer escalado — negativa retida" (sem event definition) | F-04 | 937–939 |
| CA_1#(C3) | sequenceFlows novos | (não existiam) → `F_CA_COMPRA_C3_HabilitarDireito_33` (boundary→tarefa), `_34` (tarefa→end) | F-04 | 1034–1035 |
| CA_1#LN_CA1_C3_GovernancaClinica | flowNodeRef ×3 (append) | 1 ref → 4 refs (+ C3_B_SLA_Parecer, C3_T_EscalarParecer, C3_End_SLA_Parecer) — nada movido | F-04 | 756–758 |
| CA_1#C3_T_Renegociar (host) | boundary novo | (não existia) → `C3_B_SLA_Renegociar` "SLA 24h sem alternativa" `cancelActivity="false"` attachedToRef="C3_T_Renegociar" + timeDuration PT24H + doc "[CONFIRMAR]" | F-04 | 953–959 |
| CA_1#C3_T_Renegociar (host) | serviceTask nova | (não existia) → `C3_T_EscalarRenegociar` "Escalar oferta de alternativa pendente" | F-04 | 960–963 |
| CA_1#C3_T_Renegociar (host) | endEvent novo | (não existia) → `C3_End_SLA_Renegociar` "Alternativa escalada — negativa retida" (sem event definition) | F-04 | 964–966 |
| CA_1#(C3) | sequenceFlows novos | (não existiam) → `F_CA_COMPRA_C3_HabilitarDireito_35` (boundary→tarefa), `_36` (tarefa→end) | F-04 | 1036–1037 |
| CA_1#LN_CA1_C3_CentraisExperiencia | flowNodeRef ×3 (append) | 5 refs → 8 refs (+ C3_B_SLA_Renegociar, C3_T_EscalarRenegociar, C3_End_SLA_Renegociar) — nada movido | F-04 | 767–769 |
| CA_1#C2_EV_Lembrete | documentation | (só "Aciona H2 … REENGAJAR_PROPOSTA…") → + "Lembrete agendado (previsão de ciclo): distinto de marco factual." | A-02 | 642 |
| CA_1#C2_EV_Agendado | documentation | (só "Promessa de data/hora…") → + "Marco factual: registra o que ACONTECEU com o cliente, não estimativa nem previsão." | A-02 | 657 |
| CA_1#C3_EV_Autorizado | documentation | (só "Aciona H2 … AUTORIZADO…") → + "Marco factual: …" | A-02 | 834 |
| CA_1#C3_EV_EmAnalise | documentation | (só "Sua autorização está em análise…") → + "Marco factual: …" | A-02 | 862 |
| CA_1#C4_EV_Lembrete | documentation | (só "Aciona H2 … REENGAJAR_NOVO_SLOT…") → + "Marco factual: …" | A-02 | 1113 |
| CA_1#C4_EV_Agendado | documentation | (só "Aciona H2 … AGENDADO…") → + "Marco factual: …" | A-02 | 1128 |
| CA_1#CP_GW_Proposta | documentation | (só "Origem: M2_GW_Proposta…") → + "Regra: seguir para proposta somente se relevante e viável… — dono: Consultor comercial" | A-03 | 149 |
| CA_1#CP_GW_SemOferta | documentation | (só "RT-02 [PROPOSTO]…") → + "Regra: sem oferta viável, ativar busca ativa… — dono: Consultor comercial" | A-03 | 158 |
| CA_1#CP_GW_Decisao | documentation ×2 (sem doc anterior) | (nenhuma) → + "Regra: aceite confirma o agendamento… — dono: Contratação e back-office comercial" e + guard-rail de contratação | A-03 + A-05 | 192–193 |
| CA_1#CP_GW_Habilitado | documentation | (só "RT-03: …") → + "Regra: direito habilitado segue para uso… — dono: Cadastro e operações" | A-03 | 253 |
| CA_1#CP_GW_TipoDireito | documentation (sem doc anterior) | (nenhuma) → + "Regra: trilha de habilitação depende do tipo de direito — dono: Cadastro e operações" | A-03 | 270 |
| CA_1#CP_GW_Manutencao | documentation (sem doc anterior) | (nenhuma) → + "Regra: remarcado segue novo ciclo… — dono: Retenção e relacionamento" | A-03 | 316 |
| CA_1#C1_GW_Proposta | documentation ×2 | (só "Origem: M1_GW_Relevancia") → + "Regra: recomendar somente proposta relevante… — dono: Consultor comercial" e + guard-rail de contratação | A-03 + A-05 | 566–567 |
| CA_1#C3_GW_Tipo | documentation (sem doc anterior) | (nenhuma) → + "Regra: trilha de habilitação por tipo de direito dentro da etapa C3 — dono: Cadastro e operações" | A-03 | 776 |
| CA_1#C3_GW_Auto | documentation | (só "Origem: M2_GW_RequerAutorizacao") → + "Regra: autorização automática quando a elegibilidade permitir… — dono: Cadastro e operações" | A-03 | 817 |
| CA_1#C3_GW_Pendencia | documentation | (só "Origem: M2_GW_PosExcecao") → + "Regra: pendência resolvida com menos de três tentativas… — dono: Cadastro e operações" | A-03 | 909 |
| CA_1#C3_GW_TipoNegativa | documentation | (só "Origem: M2_GW_TipoNegativa") → + "Regra: negativa de cobertura assistencial exige parecer médico… — dono: Cadastro e operações" | A-03 | 916 |
| CA_1#C3_GW_Alternativa | documentation | (só "Três saídas (RT-03)…") → + "Regra: alternativa aceita segue habilitação… — dono: Centrais de experiência" | A-03 | 970 |
| CA_1#C4_GW_Motivo | documentation | (só "Origem: M2_GW_JoinEntrega (parte)") → + "Regra: no-show ou cancelamento do prestador reabre a manutenção… — dono: Retenção e relacionamento" | A-03 | 1076 |
| CA_1#Process_CA_COMPRA_C3_HabilitarDireito | documentation | (só "Garantir cobertura e autorização…") → + "C3 separa duas preocupações: habilitação da base do direito … e autorização do serviço …" | A-04 | 723 |
| CA_1#C2_T_Aceite | documentation | (só "Formulário produz: …") → + "Guard-rail de contratação: decisão de contratar é do cliente/humano; a automação prepara a decisão, nunca decide." | A-05 | 694 |
| CA_1#C2_B_PrazoMax | documentation | (só "⚠️ PD-02 [PENDENTE]…") → + "Guard-rail de contratação: …" | A-05 | 701 |
| CA_1#C2_B_Lembrete | documentation | (só "Origem: M2_Boundary_SLAAceite…") → + "Guard-rail de contratação: …" | A-05 | 709 |
| CA_1#C2_B_Recusa | documentation | (só "Origem: M2_Boundary_Cancelamento") → + "Guard-rail de contratação: …" | A-05 | 716 |
| CA_1#Collaboration_CA_COMPRA | documentation | (nenhuma) → + "COMPRA é o macroprocesso que obtém o direito; chama H1, H3.2 e H3.1 … T-01, T-02/T-03, T-06, T-07, T-09, T-10 …" | A-06 | 4 |
| CA_1#C3_B_SLA_Parecer_di | adicionar (BPMNShape) | inexistente → Bounds 2360,4752,36,36 (borda inferior do host, convenção C4_B_SemSlot x=host.x+52 / y=host.y+62) | F-04 (DI) | 1882–1884 |
| CA_1#C3_T_EscalarParecer_di | adicionar (BPMNShape) | inexistente → Bounds 2458,4690,100,80 (ao lado do host, dentro da faixa da lane) | F-04 (DI) | 1885–1889 |
| CA_1#C3_End_SLA_Parecer_di | adicionar (BPMNShape) | inexistente → Bounds 2618,4708,36,36 | F-04 (DI) | 1890–1894 |
| CA_1#C3_B_SLA_Renegociar_di | adicionar (BPMNShape) | inexistente → Bounds 2510,5012,36,36 (borda inferior do host) | F-04 (DI) | 1895–1897 |
| CA_1#C3_T_EscalarRenegociar_di | adicionar (BPMNShape) | inexistente → Bounds 2458,4830,100,80 (acima do host, dentro da faixa da lane) | F-04 (DI) | 1898–1902 |
| CA_1#C3_End_SLA_Renegociar_di | adicionar (BPMNShape) | inexistente → Bounds 2618,4852,36,36 | F-04 (DI) | 1903–1907 |
| CA_1#F_CA_COMPRA_C3_HabilitarDireito_33_di | adicionar (BPMNEdge) | inexistente → 4 waypoints, parte do bottom-center do boundary | F-04 (DI) | 1908–1912 |
| CA_1#F_CA_COMPRA_C3_HabilitarDireito_34_di | adicionar (BPMNEdge) | inexistente → 2 waypoints | F-04 (DI) | 1913–1915 |
| CA_1#F_CA_COMPRA_C3_HabilitarDireito_35_di | adicionar (BPMNEdge) | inexistente → 5 waypoints, parte do bottom-center do boundary | F-04 (DI) | 1916–1920 |
| CA_1#F_CA_COMPRA_C3_HabilitarDireito_36_di | adicionar (BPMNEdge) | inexistente → 2 waypoints | F-04 (DI) | 1921–1923 |

### 7.3 CA_2 — Utilização (30)

| Elemento | Operação | Antes → depois | Origem | Evidência |
|---|---|---|---|---|
| CA_2#U4_GW_Join | editar declarações `<bpmn:incoming>` | `[_08, _09, _10]` → `[_08, _13]` (join = 2 entradas, casando com o fork de 2 de U4_GW_Split) | F-01 | A:696-699 (O:691-694) |
| CA_2#U4_GW_EscapePesquisa | criar exclusiveGateway | inexistente → `name="Pesquisa respondida ou tempo esgotado?"`, incoming `[_09,_10]`, outgoing `[_13]`, **SEM `default`** (escape é merge 2 entradas/1 saída; default só se aplica a split — especificação inicial pedia `default=_10` e foi corrigida) | F-01 | A:691-695 |
| CA_2#F_CA_UTILIZACAO_U4_LiquidarAvaliar_09 | retarget (id+name+sourceRef intactos) | `targetRef: U4_GW_Join → U4_GW_EscapePesquisa` (sourceRef `U4_T_Pesquisa` inalterado) | F-01 | A:720 (O:714) |
| CA_2#F_CA_UTILIZACAO_U4_LiquidarAvaliar_10 | retarget (id+name+sourceRef intactos) | `targetRef: U4_GW_Join → U4_GW_EscapePesquisa` (sourceRef `U4_B_Pesquisa` inalterado) | F-01 | A:721 (O:715) |
| CA_2#F_CA_UTILIZACAO_U4_LiquidarAvaliar_13 | criar sequenceFlow | inexistente → `U4_GW_EscapePesquisa → U4_GW_Join`, SEM `name`, SEM `conditionExpression` (fluxo de escape nunca recebe rótulo condicional) | F-01 | A:724 |
| CA_2#LN_CA2_U4_LiquidacaoAuditoria | lane flowNodeRef (append-only) | 7 refs → 8 refs: `U4_GW_EscapePesquisa` adicionado após `U4_End`; nenhum ref movido | F-01 | A:633 |
| CA_2#U4_GW_EscapePesquisa_di | criar BPMNShape (plano Collaboration_CA_UTILIZACAO) | inexistente → `Bounds (850,3085,50,50)`, `isMarkerVisible="true"`, no ponto médio da rota pesquisa/boundary→join, dentro da faixa da lane | F-01 (DI) | A:1204-1206 |
| CA_2#F_CA_UTILIZACAO_U4_LiquidarAvaliar_09_di | re-rotear waypoints | `(608,3110)→(1133,3110)` → `(608,3110)→(850,3110)` (termina na borda esquerda do escape) | F-01 (DI) | A:1249-1252 |
| CA_2#F_CA_UTILIZACAO_U4_LiquidarAvaliar_10_di | re-rotear waypoints | 6 wp via topo do join → 4 wp `(558,3168)(558,3188)(875,3188)(875,3135)` (entra na borda inferior do escape) | F-01 (DI) | A:1253-1258 |
| CA_2#F_CA_UTILIZACAO_U4_LiquidarAvaliar_13_di | criar BPMNEdge | inexistente → `(900,3110)→(1133,3110)` (escape → join) | F-01 (DI) | A:1267-1270 |
| CA_2#UT_CA_U1 | mapping io | `in variables="all"` + `out variables="all"` → `in source/target="requerPreparo"` + `out source/target="marcoJornada"`; `in businessKey` preservado | F-03 | A:66-69 (O:66-68) |
| CA_2#UT_CA_U2 | mapping io | `in variables="all"` + `out variables="all"` → `out source/target="statusEntrega"` + `out source/target="marcoJornada"` (entrada vazia por regra — callee U2 sem conditionExpression); `in businessKey` preservado | F-03 | A:85-88 (O:85-87) |
| CA_2#UT_CA_U3 | mapping io | `in variables="all"` + `out variables="all"` → `in source/target="geraResultado"` + `out source/target="marcoJornada"`; `in businessKey` preservado | F-03 | A:131-134 (O:131-133) |
| CA_2#UT_CA_U4 | mapping io | `in variables="all"` + `out variables="all"` → `in source/target="proximaAcaoIndicada"` + `out source/target="transicao"`; `in businessKey` preservado | F-03 | A:146-149 (O:146-148) |
| CA_2#UT_CA_U4 | documentation (GAP de contrato) | `Origem: ARQ-01 U4` → `Origem: ARQ-01 U4 \| Pendência: confirmar com a equipe quem produz experienciaSatisfatoria/npsScore (documentação da tarefa de pesquisa afirma; nenhum produtor declarado no modelo).` | F-03 | A:145 |
| CA_2#UT_EC_Comunicar | documentation | + "Marco factual: registra o que ACONTECEU com o cliente, não estimativa nem previsão." antes de `\| Origem:` | A-02 | A:248 |
| CA_2#U1_EV_Lembrete | documentation | + texto de marco factual (texto padrão) | A-02 | A:363 |
| CA_2#U1_EV_Pronto | documentation | + texto de marco factual | A-02 | A:392 |
| CA_2#U2_EV_EmAtendimento | documentation | + texto de marco factual | A-02 | A:476 |
| CA_2#U2_EV_Escalar | documentation (escalation) | `Origem: ES_EV_EscalarSLA` → `Escalação factual: SLA violado ACONTECEU no instante do evento. \| Origem: ES_EV_EscalarSLA` | A-02 | A:499 |
| CA_2#U2_EV_Realizado | documentation | + texto de marco factual | A-02 | A:509 |
| CA_2#U3_EV_Atraso | documentation (predição) | + "Predição (previsão de atraso): antecipa o que PODE acontecer — distinto de marco factual." | A-02 | A:576 |
| CA_2#U3_EV_Resultado | documentation | + texto de marco factual | A-02 | A:591 |
| CA_2#U4_EV_ProxAcao | documentation | + texto de marco factual | A-02 | A:664 |
| CA_2#UT_GW_Status | documentation | + "Regra: serviço realizado segue para acompanhamento e liquidação; não realizado registra no-show e libera o slot — dono: Equipe assistencial (prestador)" | A-03 | A:114 |
| CA_2#UT_GW_Satisfacao | documentation | + "Regra: experiência insatisfatória encaminha ao suporte com o contexto do consumo — dono: Liquidação e auditoria de contas" | A-03 | A:160 |
| CA_2#U1_GW_Preparo | documentation | + "Regra: exigir preparo/check-in somente quando o serviço requer; caso contrário, seguir direto — dono: Agendamento e autorização" | A-03 | A:345 |
| CA_2#U3_GW_Resultado | documentation | + "Regra: serviço com resultado ou laudo entra em acompanhamento de prazo; demais encerram — dono: Acompanhamento e gestão de cuidado" | A-03 | A:558 |
| CA_2#U4_GW_ProxAcao | documentation | + "Regra: próxima ação clínica indicada gera nova necessidade de cuidado; caso contrário, o consumo encerra — dono: Acompanhamento e gestão de cuidado" | A-03 | A:658 |
| CA_2#Collaboration_CA_UTILIZACAO | documentation | sem documentation → nota de arquitetura (inclui a retenção das 9 interseções inter-plano) como primeiro filho | A-06 | A:4 |

### 7.4 CA_3 — Suporte (60)

| Elemento | Operação | Antes → depois | Origem | Evidência |
|---|---|---|---|---|
| CA_3#SP_CA_S1 | mapping io | 2× `variables="all"` → in `origemSuporte` (produtor no chamador: SP_Start_Contato/Insatisfacao/SLA) + out `urgenciaSugerida` (produtor S1_EV_Cuidando) | F-03 | 89–90 |
| CA_3#SP_CA_S1 | documentation (reposicionada para antes de `extensionElements` — conformidade XSD; conteúdo idêntico) | + "Contrato de variáveis a confirmar com a equipe…" | F-03 (add-on) | 92 |
| CA_3#SP_CA_S2 | mapping io | 2× `variables="all"` → in `questaoClinica` `[CONFIRMAR]` (mantido mapeado) + out `rotaAtendimento` (contrato de corrente S2→S3/S4) | F-03 | 112–113 |
| CA_3#SP_CA_S2 | documentation (reposicionada para antes de `extensionElements` — conformidade XSD; conteúdo idêntico) | + "Contrato de variáveis a confirmar com a equipe…" | F-03 (add-on) | 115 |
| CA_3#SP_CA_S3 | mapping io | 2× `variables="all"` → 7 in (`origemSuporte`, `rotaAtendimento` `[corrente]`, `confiancaIA`, `limiarConfianca`, `respostaVerificada`, `naturezaCaso`, `resolvidoPrimeiroContato`) + saída vazia (sem produtor XML comprovado) | F-03 | 151–157 |
| CA_3#SP_CA_S3 | documentation (reposicionada para antes de `extensionElements` — conformidade XSD; conteúdo idêntico) | + "Contrato de variáveis a confirmar com a equipe…" | F-03 (add-on) | 159 |
| CA_3#SP_CA_S4 | mapping io | 2× `variables="all"` → 3 in (`origemSuporte`, `rotaAtendimento` `[corrente]`, `clienteConfirmouResolucao`) + 3 out (`desfechoSuporte`, `rotaAtendimento` [mantida por contrato de corrente], `tipoEventoNegocio`) | F-03 | 172–177 |
| CA_3#SP_CA_S4 | documentation (reposicionada para antes de `extensionElements` — conformidade XSD; conteúdo idêntico) | + "Contrato de variáveis a confirmar com a equipe…" | F-03 (add-on) | 179 |
| CA_3#S1_CA_H1 | mapping io | 2× `variables="all"` → 0 in + 2 out (`consentimentoValido`, `identidadeUnificada`) `[CONFIRMAR]` (mantidas mapeadas); `businessKey` e `sourceExpression="atendimento" target="finalidade"` preservados byte a byte | F-03 | 289–292 |
| CA_3#S1_CA_H1 | documentation (reposicionada para antes de `extensionElements` — conformidade XSD; conteúdo idêntico) | + "Contrato de variáveis a confirmar com a equipe…" | F-03 (add-on) | 294 |
| CA_3#S3_T_Especialista (host) | +ramo | + `S3_B_SLA_Especialista` "SLA 2h estourado" PT2H + `S3_T_EscalarEspecialista` + `S3_End_SLA_Especialista` + fluxos `_25`/`_26` | F-04 | 529–543, 734–735 |
| CA_3#S3_T_Enfermagem (host) | +ramo | + `S3_B_SLA_Enfermagem` "SLA 2h estourado" PT2H + `S3_T_EscalarEnfermagem` + `S3_End_SLA_Enfermagem` + fluxos `_27`/`_28` | F-04 | 548–562, 736–737 |
| CA_3#S3_T_Protocolo (host) | +ramo | + `S3_B_SLA_Protocolo` "SLA 2h sem protocolo" PT2H + `S3_T_EscalarProtocolo` + `S3_End_SLA_Protocolo` + fluxos `_29`/`_30` | F-04 | 584–598, 738–739 |
| CA_3#S3_T_Priorizar (host) | +ramo | + `S3_B_SLA_Priorizar` "SLA 4h sem priorização" PT4H + `S3_T_EscalarPriorizar` + `S3_End_SLA_Priorizar` + fluxos `_31`/`_32` | F-04 | 608–622, 740–741 |
| CA_3#S3_T_Supervisor (host) | +ramo | + `S3_B_SLA_Supervisor` "SLA 24h sem ação" PT24H + `S3_T_EscalarSupervisao` + `S3_End_SLA_Supervisao` + fluxos `_33`/`_34` | F-04 | 640–654, 742–743 |
| CA_3#S3_T_Intervencao (host) | +ramo | + `S3_B_SLA_Intervencao` "SLA 4h sem intervenção" PT4H + `S3_T_EscalarIntervencao` + `S3_End_SLA_Intervencao` + fluxos `_35`/`_36` | F-04 | 666–680, 744–745 |
| CA_3#LN_CA3_S3_Resolucao | lane flowNodeRef | 22 → 31 refs (+9: trio Especialista, trio Protocolo, trio Intervencao) — append-only, nenhum ref movido | F-04 | 454–462 |
| CA_3#LN_CA3_S3_Supervisao | lane flowNodeRef | 2 → 8 refs (+6: trio Priorizar, trio Supervisao) — append-only | F-04 | 468–473 |
| CA_3#LN_CA3_S3_Enfermagem | lane flowNodeRef | 1 → 4 refs (+3: trio Enfermagem) — append-only | F-04 | 478–480 |
| CA_3#S1_EV_Cuidando | documentation | + "Marco factual: registra o que ACONTECEU, não estimativa nem previsão." | A-02 | 330 |
| CA_3#S4_EV_Aprendizado | documentation | + "Marco factual: registra o que ACONTECEU, não estimativa nem previsão." | A-02 | 807 |
| CA_3#SP_GW_Origem | documentation | + "Regra: contato do cliente abre atendimento pleno; eventos internos seguem trilha reduzida — dono: Atendente de suporte" | A-03 | 103 |
| CA_3#SP_GW_Compra | documentation | + "Regra: sinal de intenção de compra dentro do suporte devolve o caso à compra — dono: Analista de triagem e roteamento" | A-03 | 126 |
| CA_3#SP_GW_Desfecho | documentation | + "Regra: reencaminhar devolve ao especialista; resolvido confirma; ouvidoria registra manifestação; sem confirmação encerra — dono: Qualidade e melhoria contínua" | A-03 | 193 |
| CA_3#S1_GW_Iniciativa | documentation | + "Regra: caso iniciado pela própria organização dispensa nova verificação pelo cliente; contato do cliente passa pela verificação — dono: Disponibilização de insumos" | A-03 | 323 |
| CA_3#S2_GW_Clinico | documentation | + "Regra: questão clínica vai a trilha assistencial; questão administrativa segue rota de atendimento — dono: Analista de triagem e roteamento" | A-03 | 400 |
| CA_3#S3_GW_Origem | documentation | + "Regra: rota por origem do caso — resposta por IA, recuperação de experiência ou atendimento humano — dono: Resolução e encaminhamento" | A-03 | 487 |
| CA_3#S3_GW_Confianca | documentation | + "Regra: resposta da IA só é entregue com confiança suficiente e verificação; caso contrário, vai a humano — dono: Resolução e encaminhamento" | A-03 | 502 |
| CA_3#S3_GW_Natureza | documentation | + "Regra: conteúdo clínico vai a enfermagem de navegação; demais casos a especialista — dono: Resolução e encaminhamento" | A-03 | 519 |
| CA_3#S3_GW_PrimeiroContato | documentation | + "Regra: resolvido no primeiro contato encerra; caso contrário, abre protocolo com prazo — dono: Resolução e encaminhamento" | A-03 | 570 |
| CA_3#S4_GW_Resolvido | documentation | + "Regra: sem confirmação do cliente, reabrir conforme a rota anterior — dono: Qualidade e melhoria contínua" | A-03 | 778 |
| CA_3#S4_GW_Rota | documentation | + "Regra: caso originado em resposta de IA reabre com reencaminhamento; demais encerram sem confirmação — dono: Qualidade e melhoria contínua" | A-03 | 784 |
| CA_3#S2_GW_Clinico | documentation | + guard-rail de IA: "a IA apenas recomenda — triagem clínica, negativa e suspeita de fraude são sempre humanas. Proibido usar dados de saúde para seleção de risco (LGPD art. 11 §5º)." | A-05 | 401 |
| CA_3#S3_GW_Confianca | documentation | + guard-rail de IA (LGPD art. 11 §5º) | A-05 | 503 |
| CA_3#S3_T_IA | documentation | + guard-rail de IA (LGPD art. 11 §5º) | A-05 | 496 |
| CA_3#Collaboration_CA_SUPORTE | documentation | (sem documentation) → + "SUPORTE escuta, diagnostica, resolve e encerra, com entrada por contato do cliente ou por transições das demais jornadas (T-05, T-07, T-09, T-10); chama H1 de serviços compartilhados e encaminha à ouvidoria fora do conjunto modelado." | A-06 | 4 |
| CA_3#S3_B_SLA_Especialista_di | adicionar (BPMNShape) | (1580,2774,36,36) sobre S3_T_Especialista (1548,2712,100,80) | F-04 (DI) | 1770 |
| CA_3#S3_T_EscalarEspecialista_di | adicionar (BPMNShape) | (1548,2850,100,80) abaixo do host, lane Resolucao | F-04 (DI) | 1773 |
| CA_3#S3_End_SLA_Especialista_di | adicionar (BPMNShape) | (1730,2872,36,36) ao lado | F-04 (DI) | 1776 |
| CA_3#F_CA_SUPORTE_S3_ResolverEncaminhar_25_di / _26_di | adicionar (BPMNEdge) | 2 e 2 waypoints | F-04 (DI) | 1779, 1783 |
| CA_3#S3_B_SLA_Enfermagem_di | adicionar (BPMNShape) | (1430,3267,36,36) sobre S3_T_Enfermagem (1398,3205,100,80) | F-04 (DI) | 1787 |
| CA_3#S3_T_EscalarEnfermagem_di | adicionar (BPMNShape) | (1560,3205,100,80) à direita (lane Enfermagem tem 150px; abaixo do host não cabe) | F-04 (DI) | 1790 |
| CA_3#S3_End_SLA_Enfermagem_di | adicionar (BPMNShape) | (1730,3227,36,36) | F-04 (DI) | 1793 |
| CA_3#F_CA_SUPORTE_S3_ResolverEncaminhar_27_di / _28_di | adicionar (BPMNEdge) | 4 e 2 waypoints (dogleg por baixo do host) | F-04 (DI) | 1796, 1802 |
| CA_3#S3_B_SLA_Protocolo_di | adicionar (BPMNShape) | (2030,2774,36,36) sobre S3_T_Protocolo (1998,2712,100,80) | F-04 (DI) | 1806 |
| CA_3#S3_T_EscalarProtocolo_di | adicionar (BPMNShape) | (1998,2850,100,80) | F-04 (DI) | 1809 |
| CA_3#S3_End_SLA_Protocolo_di | adicionar (BPMNShape) | (2160,2872,36,36) | F-04 (DI) | 1812 |
| CA_3#F_CA_SUPORTE_S3_ResolverEncaminhar_29_di / _30_di | adicionar (BPMNEdge) | 2 e 2 waypoints | F-04 (DI) | 1815, 1819 |
| CA_3#S3_B_SLA_Priorizar_di | adicionar (BPMNShape) | (2330,3112,36,36) sobre S3_T_Priorizar (2298,3050,100,80) | F-04 (DI) | 1823 |
| CA_3#S3_T_EscalarPriorizar_di | adicionar (BPMNShape) | (2440,3050,100,80) à direita (lane Supervisao tem 160px) | F-04 (DI) | 1826 |
| CA_3#S3_End_SLA_Priorizar_di | adicionar (BPMNShape) | (2552,3072,36,36) dentro da lane Supervisao | F-04 (DI) | 1829 |
| CA_3#F_CA_SUPORTE_S3_ResolverEncaminhar_31_di / _32_di | adicionar (BPMNEdge) | 4 e 2 waypoints | F-04 (DI) | 1832, 1838 |
| CA_3#S3_B_SLA_Supervisor_di | adicionar (BPMNShape) | (830,3112,36,36) sobre S3_T_Supervisor (798,3050,100,80) | F-04 (DI) | 1842 |
| CA_3#S3_T_EscalarSupervisao_di | adicionar (BPMNShape) | (940,3050,100,80) à direita | F-04 (DI) | 1845 |
| CA_3#S3_End_SLA_Supervisao_di | adicionar (BPMNShape) | (1110,3072,36,36) | F-04 (DI) | 1848 |
| CA_3#F_CA_SUPORTE_S3_ResolverEncaminhar_33_di / _34_di | adicionar (BPMNEdge) | 4 e 2 waypoints | F-04 (DI) | 1851, 1857 |
| CA_3#S3_B_SLA_Intervencao_di | adicionar (BPMNShape) | (680,2940,36,36) sobre S3_T_Intervencao (648,2878,100,80) | F-04 (DI) | 1861 |
| CA_3#S3_T_EscalarIntervencao_di | adicionar (BPMNShape) | (880,2878,100,80) à direita (desvio de S3_End_Intervencao em 830,2900) | F-04 (DI) | 1864 |
| CA_3#S3_End_SLA_Intervencao_di | adicionar (BPMNShape) | (1040,2900,36,36) | F-04 (DI) | 1867 |
| CA_3#F_CA_SUPORTE_S3_ResolverEncaminhar_35_di / _36_di | adicionar (BPMNEdge) | 4 e 2 waypoints (rota por baixo, sem cruzar shapes) | F-04 (DI) | 1870, 1876 |
