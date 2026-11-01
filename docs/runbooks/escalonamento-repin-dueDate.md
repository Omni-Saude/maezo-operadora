# Runbook: renovação do material staff + repin do escalonamento com `dueDate` (um movimento só)

Decisões: **DL-0145** (prazo da tarefa humana vindo da DMN, PR #848), **DL-0143** (eventos `human_task_skipped`),
**DL-0113** (material na AWS). Este documento **não substitui** `docs/runbooks/material-staff-na-aws.md`: ele é o
checklist que costura aquele runbook (passos 0 a 9) com o deploy do BPMN novo do `SP-OP-ESCALATION-001`, para que a
renovação de 14 dias e o repin do catálogo sejam **o mesmo corte**.

> Nada deste documento foi executado pelo PR que o criou. Ele foi escrito sem acesso à AWS: os números das versões, os
> ids e os digests reais só existem depois do deploy e saem dos comandos abaixo.

## 0. Prazos

| Quando | O quê |
|---|---|
| **2026-10-27T13:03:34Z** (ter, 10:03 de Brasília) | **aviso**: `valid_until` menos `aviso_dias = 3` (`deploy/aws-ecs/envs/dev-sa-east-1/staff-material-validade.json`). Corte tem de estar FEITO até aqui |
| **2026-10-30T13:03:34Z** (sex, 10:03 de Brasília) | **vencimento**: depois disso o motor não sobe no próximo restart e o portal dá 502 (`renovar-material-staff-dev.md` §0) |

Calendário sugerido: **21–22/10** preparação sem efeito (§3); **22/10 (qui) ou 26/10 (seg)** corte numa janela
avisada de ~45 min (§4); **27/10** é o último dia aceitável. Não deixar para sexta 30/10.

## 1. Por que é um movimento só

- O BPMN do `main` (#848) acrescenta `camunda:dueDate="${roteamento.sla_resolucao}"` em `UT_TratarEscalonamento`.
  Implantar cria a versão **N** de `SP-OP-ESCALATION-001` e muda o `process_definition_id`.
- O catálogo humano do portal admite uma versão FIXA (`SP-OP-ESCALATION-001:2`, comentário do bloco `human` em
  `deploy/aws-ecs/envs/dev-sa-east-1/portal.auto.tfvars`). Sem repin, toda tarefa nova da versão N fica **fora da
  fila** e o job T1.5 emite `human_task_skipped` com `reason=definition_not_admitted`.
- O repin só existe dentro de um `gerar` (o catálogo e a admissão Q2 são assinados pela raiz). Como o material precisa
  ser renovado antes de 27/10 de qualquer jeito, o `gerar` desta renovação leva o catálogo novo.

O que **não** muda (conferido no código, não no ambiente):

- **Imagem do motor:** o plugin humano casa o binding pela CHAVE do processo e da tarefa
  (`ReadCatalogEntry.compiled_binding` chama `_binding_for(process_definition_key, task_definition_key)`), não pela
  versão. Sem imagem nova, sem `MATERIAL_CODE_ENGINE`/`MATERIAL_CODE_PROVIDER`: os `code_digests` da admissão seguem.
- **Formulário e políticas:** as 2 tarefas novas reaproveitam o formulário e as políticas das tarefas da versão
  admitida (`forms[]`/`policies[]` iguais). A imagem de operação atual aceita o catálogo (nenhum formulário novo).
- **DMN `escalation_routing`:** o #848 não a mudou; o `group_domain` dinâmico continua pinado na mesma definição. Se o
  deploy reimplantar a DMN (ver §3.4), use `--dmn-definition-id/--dmn-definition-version/--dmn-sha256` no `montar`.
- **Portal:** a coluna Prazo já lê `engine_due_at` de `ACT_RU_TASK.DUE_DATE_`.

As entradas da versão antiga **ficam** no catálogo e na admissão (o `gerar` só acrescenta): as tarefas em andamento na
versão admitida hoje continuam visíveis até serem concluídas.

## 2. Quem executa

| Papel | O quê |
|---|---|
| Engenharia (este PR) | `scripts/ops/staff_material_escalation_catalogo.py`, este checklist, conferências offline |
| **Dono** (SSO `adm-dev`) | `verificar`, `ensaio`, `extrair-vivo`, deploy do BPMN, `gerar`, `staff-rows`, applies, emissor |
| **Dono** (OAAR, conta de gestão) | upload de `insumos/` e publicação dos segredos (a SCP só deixa a OAAR gravar segredo) |
| Operação | ciência da janela (fila do portal some ~10–20 min) e teste da fila depois do corte |

## 3. Antes do corte — tudo sem efeito

Marque cada item; **não siga para a §4 com algum vermelho**.

- [ ] **3.1 `verificar`** (`material-staff-na-aws.md` passo 2): `exitCode 0`, 14 conferências,
  `raiz_vigente_sha256` = raiz aprovada em 16/10 (`a03ab845...`, histórico daquele runbook).
- [ ] **3.2 `ensaio`** (passo 3): `ensaio: OK`. Atenção: o `ensaio` **não** usa o catálogo novo
  (`tools/staff_ops/material.py::modo_ensaio` passa `catalogo=None`); ele prova a renovação, não o repin. O repin é
  provado no item 3.3.
- [ ] **3.3 Ensaio OFFLINE do repin** (o mesmo `approver.review_admission` que o `gerar` roda antes de assinar):

  ```bash
  # dono, leitura de segredo SEM efeito: grava catalogo-vivo.json e admissao-viva.json (nao sao segredo) e
  # imprime digests + tamanho do segredo do job
  mkdir -p $S/vivo
  AWS_PROFILE=adm-dev python scripts/ops/staff_material_escalation_catalogo.py extrair-vivo --out $S/vivo
  # offline, sem rede: clona as 2 tarefas para a versao admitida+1 com um id de ENSAIO e o BPMN do main
  git show origin/main:spec/processes/bpmn/SP-OP-ESCALATION-001_Escalonamento_Humano_Universal.bpmn > $S/esc.bpmn
  PYTHONPATH=src python scripts/ops/staff_material_escalation_catalogo.py ensaio \
    --catalogo-vivo $S/vivo/catalogo-vivo.json --admissao-viva $S/vivo/admissao-viva.json \
    --tamanho-segredo-job <tamanho_segredo_job do extrair-vivo> --bpmn-repo $S/esc.bpmn
  ```

  Esperado: `"review_admission": "OK"`, `versao_admitida` (hoje 2), `folga_admissao_bytes` > 0 e
  `segredo_job_projetado_bytes` < 65536. Recusa = não há corte até a engenharia olhar.
- [ ] **3.4 Retrato das versões vivas** (`renovar-material-staff-dev.md` §9a, consultas (a) e (b), `READ ONLY`):
  anote as versões de `SP-OP-ESCALATION-001` já implantadas e quantas tarefas vivas há por definição. Se já existir
  uma versão acima da admitida (ex.: `:3`, de um deploy depois do #816 de 17/10) **com tarefas vivas**, ela também
  entra no `montar` (`--definicao` é repetível); sem tarefas vivas, pode ficar de fora.
- [ ] **3.5 O que o deploy vai reimplantar.** O `deploy-processes` reimplanta todo recurso de `spec/processes/` que
  mudou desde o último deployment com o mesmo nome (`src/maezo/platform/deploy/engine_deploy.py`). No `main` de
  20/10, desde 14/10 mudaram: `SP-OP-ESCALATION-001` (#816 em 17/10, #848 em 19/10), `SP-OP-CANCEL-001` (#786 em
  15/10, já implantado e admitido em 16/10) e DMNs da Helena/Lucas (sem tarefa humana no catálogo). Se o deploy listar
  `SP-OP-CANCEL-001` em `redeployed resources`, o pin do CANCEL também quebra: **pare** e acrescente o CANCEL ao
  repin (`scripts/ops/staff_material_cancel_catalogo.py`) antes do `gerar`.
- [ ] **3.6 Linha de base da fila** (`renovar-material-staff-dev.md` §9a (d)): eventos `human_task_skipped` desde
  16/10. Anote quantos `definition_not_admitted` já existem (se houver, é a hipótese H1 confirmada: uma versão viva
  não admitida).
- [ ] **3.7 `MATERIAL_CATALOG_REVISION`:** a última revisão do catálogo no ledger (`ledger/amh.json`, entrada
  `catalog-designate`, `material-staff-na-aws.md` §7). O valor passado tem de ser maior (o `gerar` também recusa se
  não for maior que o `catalog_revision` do `job/config.json`).
- [ ] **3.8 Janela avisada** à operação (data, hora, ~45 min, fila do portal indisponível por ~10–20 min).

## 4. O corte — ordem exata

| # | Passo | Quem | Efeito | Volta |
|---|---|---|---|---|
| 1 | Snapshot e retrato (`material-staff-na-aws.md` passo 0) | dono | nenhum | — |
| 2 | **Deploy do BPMN** (task `maezo-operadora-dev-deploy-processes`, `aws-ecs.md` §3 passo 5) | dono | instâncias NOVAS vão para a versão N; as tarefas delas **não aparecem no portal até o passo 10** | não se desfaz (redeploy do BPMN antigo cria outra versão). Minimizar o tempo entre 2 e 10 |
| 3 | Id e digest da versão N (abaixo) e conferência com o BPMN do `main` | dono | nenhum | — |
| 4 | `montar` offline (abaixo) | dono/engenharia | nenhum | — |
| 5 | Upload de `task-catalog.json` e `entradas-humanas.json` em `insumos/` (OAAR, `--sse aws:kms --sse-kms-key-id <CMK>`) | dono (OAAR) | nenhum vivo | outra chave em `insumos/` |
| 6 | `gerar` (passo 5) com os insumos | dono | só S3 (`saida/<id>/`) | outro `MATERIAL_SAIDA_ID` |
| 7 | Publicar os segredos (passo 6) | dono (OAAR) | nenhum vivo (VersionId fixo) | republicar `AWSPREVIOUS` |
| 8 | **PONTO SEM VOLTA: `staff-rows`** (passo 7) | dono | designação e admissão N+1 no banco; motor velho passa a recusar; **fila fora do ar até o passo 9** | só para a frente; volta real = snapshot do passo 1 |
| 9 | Pins, applies `-target` e emissor (passo 8), em até ~10 min do passo 8 | dono | motor e portal com o material novo | corrigir para a frente |
| 10 | Verificação (passo 9) e da fila (§5) | dono + operação | — | — |

**Passo 3 — id e digest da versão N** (leitura, sem efeito). Pela mesma via do §9a (`scripts/ops/run-db-task.ps1`,
transação `READ ONLY`):

```sql
BEGIN TRANSACTION READ ONLY;
SELECT d.id_, d.version_, encode(sha256(b.bytes_), 'hex') AS bpmn_sha256
  FROM cibseven.act_re_procdef d
  JOIN cibseven.act_ge_bytearray b ON b.deployment_id_ = d.deployment_id_ AND b.name_ = d.resource_name_
 WHERE d.key_ = 'SP-OP-ESCALATION-001' AND d.tenant_id_ = 'amh' ORDER BY d.version_;
-- se a DMN tambem foi reimplantada (3.5), a mesma consulta em act_re_decision_def com key_='escalation_routing'
ROLLBACK;
```

(Alternativa REST, como em `material-staff-na-aws.md` §7: `GET /process-definition/key/SP-OP-ESCALATION-001/tenant-id/amh`
e `GET /deployment/{id}/resources/{resourceId}/data`.)

**Passo 4 — `montar`** (offline; `--bpmn-repo` recusa se a versão N não tiver os bytes do `main` com `dueDate`):

```bash
PYTHONPATH=src python scripts/ops/staff_material_escalation_catalogo.py montar \
  --catalogo-vivo $S/vivo/catalogo-vivo.json --admissao-viva $S/vivo/admissao-viva.json \
  --tamanho-segredo-job <do extrair-vivo> --bpmn-repo $S/esc.bpmn \
  --definicao 'SP-OP-ESCALATION-001:<N>:<deployment>=<bpmn_sha256>' \
  --out $S/insumos-esc
```

Esperado: `"review_admission": "OK"`, `entradas_human_novas: 2` (4 se entrar uma versão intermediária) e as linhas
`SP-OP-ESCALATION-001:<N>:.../UT_TratarEscalonamento` e `.../UT_SupervisorAssume`. Guarde o `catalog_sha256`: o
`gerar` tem de imprimir o mesmo `catalog_digest`.

**Passo 6 — `gerar`** (renovação seguinte, `material-staff-na-aws.md` §4: raiz já aprovada, sem `raiz-gerar`):

```bash
AWS_PROFILE=adm-dev bash scripts/ops/staff_material_run.sh gerar MATERIAL_RAIZ_ID=raiz-20261016 \
  MATERIAL_RAIZ_SHA256=a03ab84558894040cf34d984763cd1e47e3eaccdcc2d3c6ab7b51476bb383c81 \
  MATERIAL_SAIDA_ID=onda13-AAAAMMDD MATERIAL_SUFIXO=AAAAMMDD \
  MATERIAL_CATALOGO=<chave do task-catalog.json em insumos/> \
  MATERIAL_ENTRADAS_HUMANAS=<chave do entradas-humanas.json em insumos/> \
  MATERIAL_CATALOG_REVISION=<maior que a do ledger, 3.7>
```

Sem `MATERIAL_CODE_*` (motor igual, §1). O `gerar` roda o `review_admission` antes de assinar: recusa aqui não tem
efeito vivo.

**Passo 9 — pins:** além dos de sempre (`material-staff-na-aws.md` passo 8, inclusive `staff-material-validade.json`),
atualize o comentário do bloco `human` de `portal.auto.tfvars` com a versão N admitida e a data.

## 5. Verificar a fila depois do corte

1. Os itens do passo 9 do runbook de material (`T15_RESULT ok=true`, `portal_read_provider root_sha256=...`, sessão 401).
2. Eventos do job desde o fim do apply (`renovar-material-staff-dev.md` §9a (d), `--start-time` = epoch em ms do fim
   do passo 9):

   ```bash
   aws logs filter-log-events --log-group-name /ecs/maezo-operadora-dev/staff-job \
     --start-time <epoch ms> --filter-pattern '?human_task_skipped ?human_publication_refused' \
     --query 'events[].[timestamp,message]' --output text
   ```

   Esperado: **nenhum** `human_task_skipped` com `reason=definition_not_admitted` para `SP-OP-ESCALATION-001` e
   `"tasks_skipped": 0` antes do `T15_RESULT ok=true`. Um `definition_not_admitted` com a versão N = o repin não
   pegou (a admissão viva não nomeia N): parar e abrir com a engenharia. Com uma versão intermediária fora do
   `montar` (3.4), os eventos dela são esperados até as tarefas dela terminarem.
3. Uma escalação de TESTE (Canal de Teste, `docs/runbooks/simulador-beneficiario.md`): a `UT_TratarEscalonamento`
   aparece na fila do grupo certo com a coluna **Prazo** preenchida (antes: "Sem prazo informado"). Consulta (b) do
   §9a: a tarefa está em `proc_def_id_` = versão N. Concluir/limpar a instância de teste como de costume.
4. Registrar no `Histórico` de `material-staff-na-aws.md`: janela, versão N e id, `catalog_sha256`, revisão do
   catálogo, VersionIds e pins.

## 6. Fora deste corte (decisões da operação, `Operacao_Decisoes_Escalonamento_2026-10-20.xlsx`)

- Alarmes de escalonamento e inbox de avisos: PR em rascunho com `escalation_alarms_enabled = true`,
  `notifications_inbox_desired_count = 1` e o destinatário dos alarmes a preencher. Não entra no corte; depende de
  decidir o destinatário e confirmar a assinatura SNS (hoje possivelmente `PendingConfirmation`).
- Pessoas nos grupos `enfermagem-triagem` e `supervisao-atendimento` (hoje sem ninguém): CSV no modelo
  `docs/runbooks/portal-usuarios-staff-modelo.csv` →
  `python scripts/ops/portal_usuarios_staff.py --csv <arquivo> --out <dir vazio>` gera os `admin-create-user`
  (sem executar) e as memberships validadas por `MembershipRecord`; depois do `sub` real (`--subs`), o `pessoas.json`
  alimenta `scripts/ops/portal_semear_membership.py` (`MODO=ler`, depois `semear`). Sem pessoa no
  `supervisao-atendimento`, a `UT_SupervisorAssume` (prazo de resolução estourado) não tem quem a veja.

## 7. Rollback (resumo)

| Até | Como voltar |
|---|---|
| Passos 1, 3, 4 | nada a desfazer |
| Passo 2 (deploy) | não se desfaz; tarefas da versão N ficam fora da fila até o repin. Se o corte for abortado aqui, faça o repin na próxima janela ou reimplante o BPMN anterior (nova versão, também não admitida — não resolve). Melhor: não abortar entre 2 e 8 |
| Passos 5 a 7 | outra chave/saída; republicar `AWSPREVIOUS` (nada vivo mudou) |
| Passo 8 em diante | só para a frente (`material-staff-na-aws.md` passos 7 e 8); desastre = snapshot do passo 1 (Aurora compartilhado: decisão do dono) |
