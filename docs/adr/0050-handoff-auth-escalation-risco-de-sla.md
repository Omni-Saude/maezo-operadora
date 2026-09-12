# ADR-0050: Handoff AUTH -> ESCALATION para risco de SLA (`ESC-{tenant}-sla-auth-{guia}`)

**Status:** Accepted — decisao do DONO DO CONTRATO #17 (2026-09-12), RATIFICADA em sessao
**Data:** 2026-09-12
**Area:** Orquestracao | Produto

## Contexto

`SP-OP-AUTH-001` modela DOIS relogios sobre `UT_AnaliseMedicoAuditor`, com semanticas
diferentes:

- `BT_SlaAnalise` — boundary **interruptivo** em `${sla.sla_analise}`: publica
  `agents.events.auth.sla_breached` e entrega a guia a `UT_CoordenacaoAssume`. Essa perna
  SEMPRE existiu e continua intacta; e a retomada por coordenacao, dentro do proprio AUTH.
- `BT_AlertaSla` — boundary **nao-interruptivo** em `${sla.sla_alerta}` (50–70% do SLA, valor
  da DMN `auth_sla`): serve `ST_NotificarRiscoSla` e termina em `End_RiscoSlaNotificado`.
  Informativo e jamais adverso.

A segunda perna nao chegava a ninguem. `NotifySlaRiskWorker` e um `WorkerBase` **sincrono**, e
`WorkerBase.execute` nao alcanca seam async nenhum, logo o worker nao podia publicar — e por um
tempo ele AFIRMOU que tinha (`status=risk_notified` + um topico de evento que pertence ao outro
ramo), fabricacao removida em FAB-SLA-RISK-NOTIFIED-SLICE4. Depois disso a etapa ficou honesta e
muda: um `logger.warning` com `notified_asserted=False`.

O `notification_bridge` ja convertia exatamente esse tipo de alerta em tarefa humana para
`recurso`, `programa` e `lgpd` (R-104). A tabela de specs do modulo NOMEAVA `auth` como o caso
que tinha a etapa e nao tinha produtor. Ou seja: o mecanismo existia, faltava a ratificacao.

E faltava mesmo. O contrato de `SP-OP-ESCALATION-001` declarava, com todas as letras, que **AUTH
nao chama ESCALATION automaticamente**, e listava quatro exigencias que um start precisa
satisfazer (gatilho do contrato, conversa de origem, produtor autorizado, correlacao de retorno),
alem de proibir inventar `source_agent_id`. Construir o handoff sem emendar isso seria contornar
um contrato FINAL — decisao de dono, nao de engenharia.

Ha ainda um risco real do lado do produto, levantado no desenho e que a decisao precisou pesar:
**dupla titularidade**. A coordenacao ja e dona do estouro de SLA dentro de AUTH; um escalonamento
paralelo poderia sugerir que outra fila assumiu o caso. E, como o vocabulario fechado de
`motivo_categoria` nao tem membro para risco de SLA, o alerta cai no catch-all `outro` e e roteado
pela DMN para `atendimento-humano` — uma fila de atendimento ao beneficiario recebendo o sinal de
um risco de auditoria medica.

## Decisao

O boundary nao-interruptivo `BT_AlertaSla` de `SP-OP-AUTH-001` pode levantar UM escalonamento em
`SP-OP-ESCALATION-001`, sob a chave de negocio

```
ESC-{tenant_id}-sla-auth-{numero_guia_tiss}
```

Usamos o caminho que ja existe, sem inventar um segundo: o worker publica
`auth.notify_sla_risk` em `operadora.notifications.internal` e o `notification_bridge` inicia o
processo pela cerca `start_process_idempotent` (ADR-0007/T-C2). **Nao ha BPMN novo, chave de
processo nova nem chamada crua ao engine** — `KNOWN_PROCESS_KEYS` fica igual, e a DMN
`escalation_routing` continua sendo a unica autoridade de roteamento.

A ancora e `numero_guia_tiss` SOZINHA, porque a business key do proprio AUTH e
`AUTH-{tenant_id}-{numero_guia_tiss}`: a unidade de idempotencia do escalonamento e a MESMA do
processo de origem. `beneficiario_pseudo_id` viaja (e variavel de entrada declarada de AUTH, e o
contrato de ESCALATION o exige) mas **nao e ancora** — duas guias do mesmo beneficiario abrem dois
escalonamentos.

Emendamos o contrato de `SP-OP-ESCALATION-001`: a regra "AUTH nao chama ESCALATION
automaticamente" permanece o CASO GERAL, com UMA excecao nominada — esta. As quatro exigencias
continuam satisfeitas, e nao por dispensa: o gatilho e um timer BPMN modelado (nao uma decisao de
agente); `source_agent_id`/`source_agent_version` sao `notification_bridge` /
`notification_bridge@v1`, a identidade do daemon que RELATOU o fato, declarada como tal — nada e
inventado para uma pessoa, que era a proibicao literal; `conversation_id` e DERIVADO do caso
(`sla-auth-{numero_guia_tiss}`, no espaco de nomes `sla-`), portanto deterministico e sem colisao
com conversa real; `canal` e `bridge_sla`, origem nao-conversacional declarada em vez de um dos
tres canais de beneficiario emprestado.

Sobre a dupla titularidade, decidimos que ela **nao ocorre**, e o contrato passa a dize-lo: o
escalonamento e informativo, a analise nao e interrompida, `UT_AnaliseMedicoAuditor` segue aberta,
nenhuma negativa nasce dali (ADR-0008, L0 hard) e a retomada continua sendo `BT_SlaAnalise` ->
`UT_CoordenacaoAssume`, DENTRO do AUTH. O escalonamento pede visibilidade; a titularidade do caso
nao se move.

Sobre o roteamento para `atendimento-humano`: aceitamos, e e a direcao certa. `outro` cai na
regra fail-safe `r7` (P2 / `atendimento-humano`), e um relogio ADMINISTRATIVO correndo e
exatamente o que NAO deve acordar uma fila clinica. Inventar um membro novo em `motivo_categoria`
seria mudanca de spec que nenhum agente ratifica; se gestao assistencial quiser uma fila propria
para risco de SLA, isso e uma regra da DMN `escalation_routing`, nao codigo.

`SP-OP-ESCALATION-001` permanece `NON_STRICT` em `_START_DEDUP_POLICY`. A convergencia vem de
`find_active_instance` por business key, que ja e o que este handoff precisa. Uma postura com
portao recusaria start apos instancia FINALIZADA — e um alerta que reincide num ciclo posterior de
analise seria engolido em silencio. Escalonamento perdido e a direcao adversa; tarefa duplicada e
ruido que um humano fecha.

Decidimos tambem a perna que faltava do outro lado: `escalation.notify_team` ganha **consumidor de
inbox**, como o proprio complemento portal deste contrato ja exigia ("`notify_team`/
`notify_supervisor` e os eventos de dominio exigem consumidor de inbox e evidencia de entrega para
que o portal afirme notificacao... Kafka aceitou nao significa humano recebeu"). ENTREGA e uma
linha commitada em `escalation_team_notice` mais o recibo dela — **nunca um offset Kafka**
(ADR-0037 XRD-10). O aviso e enderecado ao GRUPO (`grupo_atendimento`, o mesmo valor do
`candidateGroups`), com `audience` travado em `staff`: expandir grupo em pessoas exigiria uma
autoridade de pertencimento que nao existe nesta arvore, e inventa-la seria fabricar.

## Consequencias

**Positivas:**
- O risco de SLA de uma guia passa a ter destinatario humano fora da fila de quem ja esta atrasado.
- `escalation.notify_team` deixa de ser mensagem em sala vazia; ganha evidencia de entrega.
- O sinal segue a mesma cerca auditada (ADR-0007/T-C2) das outras tres origens de alerta.
- A notificacao passa a carregar `business_key`, entao a linha de inbox nomeia um caso abrivel.

**Negativas (aceitas):**
- `motivo_categoria=outro` e semanticamente pobre. Aceito: e honesto, e o catch-all e fail-safe.
- Um segundo canal humano para o mesmo caso pode gerar ruido quando a coordenacao ja assumiu.
  Aceito conscientemente: ruido fecha-se; escalonamento perdido, nao.
- `severidade=moderada` e PROPOSTO, nao ratificado clinicamente — nao e load-bearing (a regra
  `r7` casa qualquer severidade), e uma escada real de severidade para risco de SLA e decisao de
  gestao assistencial.
- `canal=bridge_sla` fica fora do dominio de tres valores que a tabela do contrato lista. Ja era
  verdade para `a2a` e `portal_tiss`; a ampliacao do dominio segue registrada para ratificacao.
- Uma nova tabela (`escalation_team_notice`, migracao 0015) sem politica de retencao: os valores
  pertencem a matriz DPO/seguranca e esta ADR nao os inventa.

## Supersedes

—

## Emenda

Emenda `docs/processes/contracts/SP-OP-ESCALATION-001.md` (a clausula "AUTH nao chama ESCALATION
automaticamente", que passa a ser o caso geral com UMA excecao nominada) e atualiza
`docs/processes/contracts/SP-OP-AUTH-001.md` (a linha de `operadora.auth.notify_sla_risk` e a
tabela de SLAs). **Nao supersede** ADR-0005, ADR-0007, ADR-0008 nem ADR-0030, e nao altera
`KNOWN_PROCESS_KEYS`.
