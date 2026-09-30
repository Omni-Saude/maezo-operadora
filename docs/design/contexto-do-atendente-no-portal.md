# Contexto do atendente no portal — o que ele vê, o que falta, e o caminho

**Status: PROPOSTO, 29/09/2026. Nada aqui está implementado.** Este documento pede uma decisão
antes de qualquer código, porque a mudança cruza duas fronteiras governadas (o contrato assinado de
leitura e as permissões de leitura do banco do motor).

Escrito por Claude sob direcao da Diretoria de Tecnologia (AMH).

## 1. O problema

O atendente que abre uma tarefa de `SP-OP-ESCALATION-001` no portal enxerga só metadados técnicos.
Não sabe **por que** o caso foi escalado, **com que gravidade**, **qual a prioridade**, nem **há
quanto tempo** está aberto. Não sabe o nome nem o telefone de quem escreveu, e não vê a conversa.

Isso foi observado em dev em 28/09/2026: o detalhe mostra processo, versão, formulário, revisões,
responsável e "Sem prazo informado" (`engine_due_at` é nulo — os prazos do processo são timers de
fronteira, não o `dueDate` da tarefa).

## 2. O que existe e o que pode ser mostrado

| Informação | Existe hoje? | Onde | Pode ir ao portal? |
|---|---|---|---|
| Grupo, prioridade, gravidade, motivo, hora do aviso | Sim | `escalation_team_notice` (migração 0015) | **Sim** — 5 colunas, todas vocabulário fechado, sem texto livre |
| Referência do caso (`ESC-{tenant}-{conversation_id}`) | Sim | `business_key` da mesma tabela | Sim |
| Resumo de 1–2 frases escrito pela Helena | Sim | variável de processo `resumo_contexto` | **Não sem decisão explícita do DPO/dono**: está em `PHI_PROCESS_VARS` (`tools/workers/phi_vars.py`), a lista de variáveis de processo que carregam texto clínico livre e não saem de um worker sem redação |
| Telefone | Só cifrado no cofre (ADR-0061) | `beneficiario_contato_retomada` | Não — só o `agent-resume` tem `kms:Decrypt` |
| Histórico da conversa | Na memória da Helena (zona PHI) | checkpoint do LangGraph | Não — ADR-0049: narrativa clínica bruta fica na zona PHI |
| Nome | **Não existe em lugar nenhum** | — | Depende da ponte com o cadastro (`identidade-por-telefone.md`) |

## 3. Por que não é um PR pequeno

Verificado no código, com o caminho de cada afirmação:

1. **O contrato de leitura só aceita um tipo de evidência: a de pagamento.**
   `PublicTaskSnapshot.read_only_evidence: PagtoAdmissibilityEvidence | None`
   (`portal/contracts/queues.py`, `portal/contracts/models.py`).
2. **O plugin do motor recusa evidência em qualquer outra tarefa.** `PortalReadModels.java`:
   `form_key == "pagto_admissibilidade"` se e somente se `read_only_evidence != null`; qualquer
   outra combinação lança contrato inválido. Uma tarefa de escalonamento com contexto seria rejeitada.
3. **O publicador grava a evidência vazia.** `task_publication_source.py`, `resource_projection`:
   `read_only_evidence=None`. A "evidência" publicada é só uma atestação por digest
   (`mzo_human_evidence`: ref, revisão, digest) — não carrega conteúdo.
4. **O portal lê o motor por uma visão com permissão por coluna.** `NativeTaskSource` seleciona
   `id_`, `rev_`, `proc_def_id_` e `task_def_key_` de `act_ru_task`, filtrando por `tenant_id_` e
   `suspension_state_`. O login de leitura só recebe `SELECT` de coluna ("nada mais"), definido em
   `deploy/sql/portal-task-source-grants.sql` e aplicado pelo dono de cada schema. Ler variáveis
   do processo exigiria novas permissões de coluna nesse arquivo.
5. **38 arquivos** de código e teste citam a evidência ou o formulário de pagamento. Nem todos mudariam,
   mas o contrato tem vetores JCS assinados, OpenAPI versionado e cliente TypeScript gerado.
6. **Não há como validar o lado Java fora da CI.** O job `plugin humano Java (mvn verify)` é o
   único verificador do plugin; a máquina de desenvolvimento não tem Java nem Maven.

## 4. Opções

### Opção A — recomendada: usar `escalation_team_notice`

A tabela já existe, é imutável (gatilhos bloqueiam UPDATE, DELETE e TRUNCATE), tem `audience`
fixo em `staff`, e foi desenhada para isto: o comentário da migração descreve `business_key` como a
referência do caso que o humano abre, e registra que, antes dela, a fila humana existia só como
tarefa do motor. O consumidor
(`platform/integrations/notifications_inbox.py`, com `main()` e testes) **existe e não está
implantado**: em dev só roda `notifications_bridge` (`service-bridge.tf`).

O que falta:

| # | Peça | Quem |
|---|---|---|
| 1 | Serviço ECS para o consumidor da caixa de entrada (Terraform) | Infra |
| 2 | Permissão `SELECT` na tabela para o papel do portal (só essa tabela, sem PHI) | DBA / infra |
| 3 | Rota de leitura no BFF, seguindo o padrão do `HumanGateway` (autorização por vínculo do grupo, recibo de leitura) | Código |
| 4 | Contrato + OpenAPI + tipos TypeScript + painel "Avisos do meu grupo" | Código |
| 5 | Testes de contrato, de autorização por grupo e de não-vazamento | Código |

**Limites conhecidos da opção A:**

- **Sem vínculo por tarefa na v1.** O aviso é por `business_key`; o detalhe da tarefa não expõe a
  chave do caso. A v1 mostraria a **lista de avisos abertos do grupo** (prioridade, gravidade,
  motivo, idade), não o contexto dentro da tarefa. Vincular por tarefa exige expor a chave no
  snapshot (opção B).
- **Segunda linha não registrada.** O aviso da supervisão (`notify_supervisor`) não é gravado pelo
  consumidor atual, então o `supervisor` não teria painel.
- **Prazos não estão armazenados.** Mostrar "aberto há N min" e a prioridade é possível; mostrar
  o vencimento exato do prazo exigiria novas colunas (migração) e mudança no worker.
- **Retenção ausente por desenho** (matriz do DPO não ratificada).

### Opção B — evidência no contrato de leitura

Novo tipo de evidência `escalation`, regra do plugin Java alterada, publicador lendo variáveis do
processo, permissões de coluna novas, vetores JCS e OpenAPI regenerados, imagem do motor refeita.
É a única que dá contexto **dentro** da tarefa. É a mais cara e a mais arriscada: mexe no motor de
todos os agentes e na fronteira de aquisição nativa. Só se justifica se a v1 da opção A não bastar.

### Opção C — mostrar o resumo da Helena

Fora deste documento até o DPO decidir. O campo é classificado como PHI; o texto é gerado por
modelo a partir da mensagem do beneficiário e pode vir vazio se o modelo falhar
(`graph.py::_resumo_contexto`).

## 5. Decisões pedidas

1. Aprovar a **opção A** como v1, ciente dos limites acima.
2. Definir quem faz as peças 1 e 2 (Terraform do serviço e permissão de leitura).
3. DPO: decidir sobre o `resumo_contexto` (opção C) e sobre telefone e histórico.
4. Confirmar se a v1 **sem vínculo por tarefa** é aceitável ou se a opção B é requisito.

## 6. Critérios de pronto (opção A)

- O consumidor está implantado e cada aviso publicado vira uma linha em `escalation_team_notice`.
- Um atendente do grupo `plantao-clinico` vê os avisos do seu grupo e **não** vê os de outro grupo.
- O painel não exibe nenhum campo fora das cinco colunas; um teste falha se aparecer texto livre.
- Nenhuma variável de `PHI_PROCESS_VARS` atravessa o BFF.

## 7. O que este documento não cobre

Telefone, histórico da conversa e nome (passos seguintes, dependem do DPO e da ponte com o
cadastro); o papel do atendente — só repassar texto pela Helena, ou falar direto com a pessoa —
que é decisão de produto e clínica.
