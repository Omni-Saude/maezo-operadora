# Runbook: gerar e renovar o material staff/human do portal NA AWS (sem Docker no notebook)

Decisao: **DL-0113**. O dono decidiu em 15/10/2026 que nada disto roda em Docker no notebook dele: a operacao de
material (gerar, assinar, publicar catalogo e admissao) roda **toda na AWS**, com a raiz e o estado guardados na AWS.

**Aprovacao de troca de ancora de confianca (so DEV).** O dono aprovou em 15/10/2026 a troca da raiz de
instalacao `installation-root` em dev (nova raiz Ed25519 gerada NA AWS), porque a chave privada da raiz anterior so
existia no volume Docker local `maezo-onda2b-materials` e nao e recuperavel por nenhum outro caminho. Vale **so** em
`dev`/`amh`, pela delegacao N1 (emenda de 25/09/2026 ao plano `portal-autoridade-nativa-dev.md`). **Producao continua
exigindo aprovador humano** (D-F): a ferramenta recusa qualquer escopo que nao seja `dev`/`amh` (`trava_dev`).

Este runbook **substitui** `renovar-material-staff-dev.md` nos passos de geracao e publicacao (secoes 2 a 5 dele).
As secoes 6 a 10 dele (linhas no banco, task definitions, emissor, verificacao, rollback) continuam valendo e sao
citadas abaixo. Plano e invariantes: `docs/plans/portal-autoridade-nativa-dev.md` (D-F, D-G, N1, N2).

## 0. Janela e prazo

O material atual vence em **2026-10-22T18:19:46Z** (`staff-material-validade.json`). Depois disso o motor nao sobe no
proximo restart e `maezo-dev.austa.com.br` da 502 (`renovar-material-staff-dev.md` §0). Cronograma sugerido:

| Quando | O que |
|---|---|
| ate 17/10 | PR mergeado, infra (passo 1), imagem construida, `verificar` e `ensaio` verdes (passos 2 e 3) |
| 18/10 a 19/10 | `raiz-gerar`, aprovacao do hash pelo dono, `gerar` real, publicacao (passos 4 a 6) |
| 20/10 | corte: `staff-rows`, applies, emissor e verificacao (passos 7 a 9), numa janela de ~30 min avisada |
| 21/10 a 22/10 | folga. Nao deixar o corte para depois de 21/10 |

## 1. O que mudou e onde fica cada coisa

| Antes (volume local) | Agora (AWS) |
|---|---|
| raiz privada `installation-root-key.pem` | **S3 de estado** `maezo-operadora-dev-staff-material-<conta>`, prefixo `raiz/<id>/`, SSE-KMS com **CMK propria** (`alias/maezo-operadora-dev-staff-material`), versionado, BPA, sem DELETE por API. So a task `staff-material` le ou grava `raiz/`. Gerada DENTRO da task (`raiz-gerar`); so a publica sai |
| `/m/materials` (saida do `generate`) | remontado **dos segredos vivos** pela task (`restaurar`): chaves do servidor/CA, chaves do job, continuidade, resultado nativo (conferido por hash) |
| `estado/` da renovacao anterior | remontado dos segredos vivos (os 4 segredos privados sao o proprio segredo; os arquivos publicos sao derivados e conferidos) |
| `spec.r2.json`, `assignment-source.json` | derivados do manifesto vivo e do segredo `staff-install/assignment` |
| `docker run ... gerar` | task Fargate `maezo-operadora-dev-staff-material` (imagem de operacao `deploy/ops/staff-install.Dockerfile`, ECR, build pelo CodeBuild) |
| `docker run ... publicar` | `publicar --s3 ...` com a OrganizationAccountAccessRole, **sem Docker** (CloudShell ou Python+boto3) |

Nao e recuperavel e e **reescrito**: o corpo de `validity-policy.json` (so existia no volume). O engine compara apenas o
digest como pin opaco (D-L c) e o `gerar` ja troca esse digest a cada renovacao, entao nada muda para o motor.

A task **nunca grava segredo**: a SCP `deny-secrets-without-rotation` (`p-9m8f47yd`) nega `PutSecretValue` a task role
(`portal-dev-provisionamento.md` §Segredo) e a role ainda leva um `Deny` explicito. Por isso a publicacao (passo 6) e
humana, com a OAAR.

## 2. Pre-requisitos

```bash
aws sts get-caller-identity --profile adm-dev      # SSO AdministratorAccess em 203312548462
aws sts get-caller-identity --profile adm-mgmt     # SSO da conta de gestao (para a OAAR, passo 6)
git -C <repo> checkout feat/material-staff-na-aws  # ou main, depois do merge
```

Nenhum Docker. Ferramentas: AWS CLI, Git Bash, Python 3 (so para montar o JSON do override). Todo comando de task usa
`scripts/ops/staff_material_run.sh`, que monta o override **so com `MATERIAL_*`** (nunca segredo), roda a task, espera
e imprime o log e o `exitCode` (diferente de zero = recusado).

## 3. Sequencia 0 a 9 (com rollback de cada passo)

### Passo 0. Snapshot e retrato do que esta vivo

```bash
AWS_PROFILE=adm-dev aws rds create-db-cluster-snapshot --region sa-east-1 \
  --db-cluster-identifier amh-aurora-hapi-dev --db-cluster-snapshot-identifier pre-material-aws-AAAAMMDD \
  --tags Key=tenant,Value=amh Key=environment,Value=dev Key=workload,Value=maezo-operadora Key=owner,Value=<voce> Key=cost_center,Value=maezo
for s in engine/native-materials portal/amh/staff-materials staff-job/materials staff-install/rows staff-install/syn-fixture; do
  aws secretsmanager list-secret-version-ids --secret-id maezo-operadora/dev/$s \
    --query 'Versions[].[VersionId,VersionStages[0]]' --output text   # anote o AWSCURRENT de cada um
done
```

**Rollback:** nao ha o que desfazer; o snapshot e o ponto de retorno do passo 7.

### Passo 1. Infraestrutura (inerte ate este passo)

Dois PRs pequenos de ativacao, ambos **so descomentando** `deploy/aws-ecs/envs/dev-sa-east-1/staff-material.auto.tfvars`:

1. **1a, sem `image_digest`:** nasce so o bucket de estado, a CMK, a role e o log group.
2. Build da imagem de operacao (o contexto e o **commit**; `git archive` nao leva a arvore suja):

   ```bash
   git archive --format=zip -o /tmp/fonte.zip HEAD
   aws s3 cp /tmp/fonte.zip s3://amh-pipeline-artifacts-dev-sa-east-1/maezo-operadora/fonte.zip --sse AES256
   aws codebuild start-build --region sa-east-1 --project-name maezo-operadora-dev-imagem \
     --environment-variables-override name=TAG_IMAGEM,value=$(git rev-parse --short=8 HEAD)-staff-ops \
     name=DOCKERFILE,value=deploy/ops/staff-install.Dockerfile name=REPOSITORIO,value=amh/maezo-operadora \
     "name=BUILD_ARGS,value=APP_REPOSITORY=203312548462.dkr.ecr.sa-east-1.amazonaws.com/amh/maezo-operadora APP_DIGEST=<o MESMO app digest das imagens staff-ops vivas, ver dev-vivo.auto.tfvars>"
   ```

   O digest da imagem sai no fim do log do build (`imageDetails[0].imageDigest`). Use o mesmo `APP_DIGEST` base da
   imagem `1ded30e7-staff-ops` (comentario em `dev-vivo.auto.tfvars`, bloco `staff_ops`).
3. **1b, com `image_digest`:** nasce a task definition `maezo-operadora-dev-staff-material`.

Apply (so `-target`; o plano tem de mostrar **somente adicoes**, `0 to change, 0 to destroy`):

```bash
cd deploy/aws-ecs/envs/dev-sa-east-1
eval "$(aws configure export-credentials --profile adm-dev --format env)"; unset AWS_PROFILE
terraform plan -input=false -no-color -var-file=<os var-files vivos, renovar-material-staff-dev.md §7> \
  -target=aws_iam_role.staff_material -target=aws_iam_role_policy.staff_material \
  -target=aws_kms_key.staff_material -target=aws_kms_alias.staff_material \
  -target=aws_s3_bucket.staff_material -target=aws_s3_bucket_versioning.staff_material \
  -target=aws_s3_bucket_server_side_encryption_configuration.staff_material \
  -target=aws_s3_bucket_public_access_block.staff_material -target=aws_s3_bucket_policy.staff_material \
  -target=aws_cloudwatch_log_group.staff_material -target=aws_ecs_task_definition.staff_material \
  -out=$S/material.plan > $S/material.txt; grep -c 'Error:' $S/material.txt; grep -E "Plan:" $S/material.txt
terraform apply -input=false $S/material.plan
```

**Rollback:** voltar o `staff_material` para `null` e aplicar. O bucket tem a politica `SemApagarObjeto` e
`force_destroy = false`: se ja houver objeto (a raiz!), o destroy falha de proposito. Remover exige editar essa
politica, um ato deliberado do admin da conta. Nada vivo depende da infra ate o passo 5.

### Passo 2. `verificar` (dry-run, sem efeito)

```bash
AWS_PROFILE=adm-dev bash scripts/ops/staff_material_run.sh verificar
```

Le os 6 segredos vivos, remonta tudo **em memoria** e confere por hash. Nao escreve nada (nem S3, nem segredo, nem
disco). Esperado: `exitCode 0`, uma linha JSON com **14 conferencias** e `raiz_vigente_sha256` igual ao
`root_key_sha256` de `portal.auto.tfvars` (`de41c7a0...`), e a lista `arvore/caminho bytes=N sha256=16hex`. Cada
conferencia compara chave do job com os trusts do motor, certificado de cliente com os peers, continuidade com a
admissao Q2, composicao com o manifesto, raiz igual no pacote staff, no humano e no segredo nativo. `RECUSADO:
conferencia por hash falhou: <nome>` diz qual. **Nao siga se falhar.**

**Rollback:** nenhum (sem efeito).

### Passo 3. `ensaio` (raiz efemera de teste)

```bash
AWS_PROFILE=adm-dev bash scripts/ops/staff_material_run.sh ensaio MATERIAL_SUFIXO=AAAAMMDD
```

Remonta, gera uma raiz **efemera** (so no scratch da task, morre com ela) e roda o `gerar` completo, mais os
verificadores offline: `decode_bundle` do portal, `verify_materials` do job, `review_admission`, `rows.verify` (o mesmo
que o `staff-rows` roda). Imprime os **tamanhos** das mensagens assinadas e dos segredos de saida e projeta a admissao
Q2 com as **4 tarefas do CANCEL-001** (`UT_AnaliseRescisao`, `UT_CoordenacaoCancelamento`,
`UT_ExecutarCancelamentoCadastro`, `UT_CoordenacaoExecucaoCadastro`) contra o limite da coluna `RECORD_` (65536) e o
limite de 65536 do Secrets Manager. **Nada vai para o S3 nem para o Secrets Manager.** Esperado: `ensaio: OK`.

**Rollback:** nenhum (sem efeito).

### Passo 4. Gerar a raiz nova (`raiz-gerar`) e o dono aprovar o hash

```bash
AWS_PROFILE=adm-dev bash scripts/ops/staff_material_run.sh raiz-gerar MATERIAL_RAIZ_ID=raiz-AAAAMMDD
```

Imprime `root_key_sha256`. A chave privada fica **so** em `s3://<bucket>/raiz/raiz-AAAAMMDD/` (nunca sobrescreve; a
tarefa recusa se ja existir). A publica fica tambem em `publico/raiz-AAAAMMDD/installation-root.der`: o dono confere o
hash por conta propria, como o aprovador da D-F:

```bash
aws s3 cp s3://<bucket>/publico/raiz-AAAAMMDD/installation-root.der - --profile <OAAR> | sha256sum   # tem de ser o hash impresso
```

**O dono aprova esse hash** (registrar no PR de pins). Sem essa aprovacao o `gerar` nao roda: ele exige
`MATERIAL_RAIZ_SHA256` igual ao hash da raiz carregada.

**Rollback:** a raiz criada e inofensiva sem o passo 7. Para trocar de ideia, gere outra com outro `MATERIAL_RAIZ_ID`.
Nao se apaga raiz por API (de proposito).

### Passo 5. `gerar` (real)

```bash
AWS_PROFILE=adm-dev bash scripts/ops/staff_material_run.sh gerar MATERIAL_RAIZ_ID=raiz-AAAAMMDD \
  MATERIAL_RAIZ_SHA256=<hash aprovado> MATERIAL_SAIDA_ID=onda12-AAAAMMDD MATERIAL_SUFIXO=AAAAMMDD
```

Remonta, carrega a raiz do S3, confere o hash aprovado e o escopo `dev/amh`, gera a janela nova de 14 dias (`agora-2min`
a `+14d`), roda `rows.verify` sobre o segredo de linhas e grava `saida/onda12-AAAAMMDD/{ops-secrets,public}/` no S3
(objetos novos, SSE-KMS; a saida nunca e sobrescrita). O log traz `resumo.json` (digests e ids publicos). A raiz nova
passa a assinar: designacao N+1 e prova, admissao Q2, espelho humano, dono da atribuicao; entra no pacote staff, no
humano, no provider e na composicao do motor, em `staff-issuer/installation-root.der` e em `root_public_key_b64` do
segredo de linhas.

**Entradas do CANCEL-001 (cancel-001-pf.md §3.3).** Depois do deploy do BPMN, suba o catalogo novo e as entradas
`human` por `insumos/` (OAAR, `--sse aws:kms --sse-kms-key-id <CMK>`) e acrescente ao comando
`MATERIAL_CATALOGO=<chave em insumos/> MATERIAL_ENTRADAS_HUMANAS=<chave em insumos/>`. O `gerar` troca o catalogo,
atualiza o `catalog_digest` e acrescenta as entradas, e o `review_admission` recusa antes de assinar qualquer
divergencia entre catalogo e admissao. **Ordem obrigatoria** (cancel-001-pf §3.3): imagem nova do motor, depois o
catalogo; um catalogo com tarefa que o plugin nao conhece derruba as filas (503).

**Rollback:** apagar o prefixo e impossivel por API; basta gerar com **outro** `MATERIAL_SAIDA_ID`. Nada vivo muda.

### Passo 6. Publicar os segredos (OAAR, sem Docker)

A SCP so deixa a OrganizationAccountAccessRole criar versao de segredo. A saida esta no S3; o script le do S3 e
publica byte a byte (`put_secret_value`, `ClientRequestToken` = `material_version_id` para staff e humano), relendo e
conferindo. **Nenhum arquivo vai para disco.**

**Opcao A (recomendada): CloudShell.** Console, trocar para `OrganizationAccountAccessRole` na conta 203312548462,
abrir o CloudShell, enviar `scripts/ops/staff_material_renovar_dev.py` (Actions, Upload) e:

```bash
python3 staff_material_renovar_dev.py publicar --s3 s3://<bucket>/saida/onda12-AAAAMMDD/ \
  native-materials.json staff-materials.json portal-human.json job.json rows.json syn-fixture.json
```

**Opcao B: notebook** (Python 3 + boto3, sem Docker; a credencial da OAAR so em variaveis de um subshell):

```bash
( read A B C < <(aws sts assume-role --profile adm-mgmt --role-arn arn:aws:iam::203312548462:role/OrganizationAccountAccessRole \
    --role-session-name material-staff --duration-seconds 900 \
    --query 'Credentials.[AccessKeyId,SecretAccessKey,SessionToken]' --output text | tr -d '\r')
  export AWS_ACCESS_KEY_ID=$A AWS_SECRET_ACCESS_KEY=$B AWS_SESSION_TOKEN=$C AWS_REGION=sa-east-1
  python scripts/ops/staff_material_renovar_dev.py publicar --s3 s3://<bucket>/saida/onda12-AAAAMMDD/ \
    native-materials.json staff-materials.json portal-human.json job.json rows.json syn-fixture.json )
```

(O `tr -d '\r'` e obrigatorio no Git Bash: a `aws.exe` termina a linha com CR e o token com CR quebra o header do boto3.)

Cada linha volta com `identico=True` e o `VersionId`; anote o do `native-materials`, do `job` e do `rows`. Leia o
`resumo.json` para os pins: `aws s3 cp s3://<bucket>/saida/onda12-AAAAMMDD/public/resumo.json - `.

**Efeito vivo da publicacao: nenhum.** O motor, o portal e o job apontam para `VersionId` fixo; so o `rows` e o `syn` sao
lidos em `AWSCURRENT`, e so quando a task roda. **Rollback:** republicar o valor da versao anterior
(`AWSPREVIOUS`) como nova versao, antes do passo 7.

### Passo 7. PONTO SEM VOLTA: `staff-rows`

> **Atencao.** A partir daqui nao ha `undo`. O `staff-rows` instala no banco a designacao N+1 (assinada pela raiz
> nova) e a admissao Q2 N+1. O ponteiro `mzo_staff_case_designation_current` so anda para a frente e o provider so le
> a **ultima** revisao da admissao; a raiz antiga e irrecuperavel, entao nao existe como reassinar para voltar. O
> motor VIVO (ainda com o segredo e a raiz antigos) **passa a recusar** leituras e publicacoes staff e comandos
> humanos: `StaffCaseInstallation.finalDesignation()` relê a designacao a cada comando e lanca `conflict()` quando o
> digest do banco difere do pinado (`StaffCaseReadCommand.java:92`, `StaffCasePublicationCommand.java:87`,
> `HumanCommandPlugin.java:183`). Falha fechada, sem corrupcao, mas **a fila humana e o job T1.5 ficam indisponiveis
> ate o motor reiniciar com o segredo novo (passo 8)**. Faca os passos 7 e 8 em sequencia, em ate ~10 min. O
> alarme `staff-job-2-falhas-seguidas` pode disparar (rodada a cada 4 min).
>
> Saida de emergencia, **so para a frente**: corrigir e seguir o passo 8. A volta real e restaurar o snapshot do
> passo 0 (Aurora compartilhado: decisao do dono).

```bash
# renovar-material-staff-dev.md §6: run-task da familia maezo-operadora-dev-staff-rows e conferir o log
NET="awsvpcConfiguration={subnets=[subnet-0e1f840dbec44e66a,subnet-06647b0c664d0d22a],securityGroups=[sg-0e2aebe1a2c1d253d],assignPublicIp=DISABLED}"
TA=$(aws ecs run-task --cluster maezo-operadora-dev --task-definition maezo-operadora-dev-staff-rows \
  --launch-type FARGATE --network-configuration "$NET" --query 'tasks[0].taskArn' --output text)
aws ecs wait tasks-stopped --cluster maezo-operadora-dev --tasks $TA
```

Esperado no log `/ecs/maezo-operadora-dev/staff-rows`: `"ok": true`, `designation_current: rotacionada rN->rN+1`,
`admission: inserida`, `assignment: instalacao requalificada`.

### Passo 8. Pins no Terraform, applies e emissor

Pins (do `resumo.json`) em `deploy/aws-ecs/envs/dev-sa-east-1/portal.auto.tfvars`: **`root_key_sha256` (agora a RAIZ
NOVA, o hash aprovado no passo 4)**, `staff.material_secret_version_id`, `staff.public_manifest_sha256`,
`staff.designation_sha256`, `staff.native_configuration_sha256`, `human.material_secret_version_id`,
`human.public_manifest_sha256`; e `staff-material-validade.json` (`valid_until` = fim da janela menos 60 s,
`renovado_em`, revisoes). Os applies `-target` do motor e do portal/job seguem **exatamente**
`renovar-material-staff-dev.md` §7 (var-files vivos por `scripts/ops/staff_tfvars_vivos.py`; nunca `-target` na TD do
`staff_job` sem o `aws_scheduler_schedule.staff_job`). Depois, o emissor e a qualificacao AUTH, §8 do mesmo runbook:

```bash
AWS_PROFILE=adm-dev bash scripts/ops/staff_vpc_run.sh scripts/ops/staff_material_reparo_vpc.py \
  maezo-operadora-dev-staff-syn staff-syn staff-syn MODO=medir
AWS_PROFILE=adm-dev bash scripts/ops/staff_vpc_run.sh scripts/ops/staff_material_reparo_vpc.py \
  maezo-operadora-dev-staff-syn staff-syn staff-syn MODO=aplicar RENOVAR_ATE=<fim da janela, AAAA-MM-DDTHH:MM:SS.ffffffZ>
```

**Rollback:** o circuit breaker volta o motor para a revisao anterior se a nova nao subir, mas essa revisao tem a raiz
antiga e tambem recusa. Corrigir para a frente.

### Passo 9. Verificacao e aposentadoria do volume

`renovar-material-staff-dev.md` §9 (servicos `COMPLETED`, log do motor com `staff_native_configuration_digest=<o do
resumo>` e `Server startup` sem `refused`, `T15_RESULT ok=true`, `task-read`, `/api/v1/portal/session` 401,
cloudflared sem `Unable to reach the origin`). Confira tambem no log do motor
`portal_read_provider root_sha256=<o hash da raiz nova>`.

Depois de tudo verde, o volume `maezo-onda2b-materials` e a raiz antiga ficam **obsoletos** (nada vivo os pina). O dono
pode descarta-los (`docker volume rm maezo-onda2b-materials`, um comando, ou desinstalar o Docker Desktop). Registrar
no `Historico` do runbook antigo: janela, VersionIds, pins, hash da raiz nova, id da saida.

## 4. Renovacoes seguintes (a cada 14 dias)

Mesmo fluxo, sem raiz nova: passo 2 (`verificar`), passo 5 (`gerar` com `MATERIAL_RAIZ_ID` e `MATERIAL_RAIZ_SHA256` da
raiz ja aprovada), passos 6 a 9. Cada `gerar` remonta do que esta **vivo**, entao nao ha estado local a manter.

## 5. Invariantes de seguranca (o que impede esta mudanca de virar atalho)

- Dev apenas: `trava_dev` exige `dev`/`amh` na designacao e o hash aprovado da raiz; a infra existe so no stack
  `dev-sa-east-1`. Producao exige aprovador humano (D-F) e nao usa nada daqui.
- A raiz nunca sai da AWS: `raiz/` so e legivel pela task role (policy de bucket com `Deny` para todo o resto, inclusive
  a OAAR), com CMK propria. Nao ha `Delete*` por API para ninguem.
- A task nao grava segredo (`Deny` explicito na role e SCP). Quem publica e a OAAR, depois de o dono aprovar o hash.
- Nada imprime valor: o log so tem caminho, tamanho e SHA-256 truncado; segredos e chaves nunca vao em
  `containerOverrides` (so `MATERIAL_*` nao secretos).
- A task so le os 6 segredos por ARN exato, via Secrets Manager (`kms:ViaService`).

## 6. Quando algo falha

| Sintoma | Causa provavel e saida |
|---|---|
| `verificar`: `conferencia por hash falhou: <x>` | segredos vivos incoerentes entre si; nao gerar. Abrir o item `<x>` com a engenharia |
| `RECUSADO: ... ja existe` | id de raiz ou de saida repetido; use outro |
| `recusado: a raiz carregada nao e a que o dono aprovou` | `MATERIAL_RAIZ_SHA256` diferente do hash da raiz do S3 |
| `recusado: a renovacao delegada (N1) so roda em dev/amh` | escopo errado; nao e dev |
| `ensaio` acima do limite | admissao ou segredo perto de 65536 bytes; reduzir entradas antes do `gerar` |
| `put_secret_value` negado | a credencial nao e da OAAR (SCP) |
