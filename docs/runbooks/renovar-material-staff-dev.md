# Runbook: renovar o material staff/human do portal nativo em DEV (a cada 14 dias)

Plano: `docs/plans/portal-autoridade-nativa-dev.md` (Ondas 4-10, decisao N2 = 14 dias, emenda N1 de
25/09 e D-L). **So dev.** Em dev o agente usa a raiz `installation-root` do volume e assina
designacao, admissoes e dono da atribuicao DENTRO do container; a chave privada da raiz nunca sai do
volume. Producao exige aprovador humano.

## 0. Quando

- O workflow agendado `staff-material-validade` fica vermelho 3 dias antes do fim da janela
  (`deploy/aws-ecs/envs/dev-sa-east-1/staff-material-validade.json`, `valid_until`).
- Ou ja venceu. O sintoma, no log do `cibseven` no PRIMEIRO restart depois do vencimento:
  `trusted certificate with alias [maezo-human-client-ca] ... is not valid (NotAfter ...)` e
  `staff deployment composition refused`. O Tomcat nao sobe, o ECS tenta a cada ~6 min
  (`TaskFailedToStart`) e `maezo-dev.austa.com.br` da 502 (o cloudflared nao acha o motor).
  Antes do restart nada cai: o job T1.5 passa a falhar (`T15_RESULT ok=false`) e o emissor fica
  com publicacao pendente.

Tudo vence junto, no mesmo instante: CA de clientes humana e os certificados `read`/`command`,
designacao + prova de instalacao, manifesto e revogacao do pacote staff, admissao Q2 e a humana
espelho, chaves dos trusts do engine (`portal-read-trust`, `trust`, `assignment-trust`), certificado
do AUTH (`AuthResultSigner.checkValidity`) e o dono da atribuicao. Os certificados do `generate`
(servidor/CA nativa) valem ate 2026-12-23 e NAO entram na renovacao de 14 dias.

## 1. O que se renova e o que fica igual

| Muda | Fica igual |
|---|---|
| chaves humanas (3 Ed25519 + CA de clientes + certs `read`/`command`), key_id `amh-dev-<proposito>-AAAAMMDD` | raiz `installation-root` (`de41c7a0...`) |
| designacao N+1 (mesmas chaves) + prova; politica do emissor `@dN+1` | chaves/certificados do `generate` (`/m/materials`), SPKI do servidor nativo (`1c0c3548...`) |
| politica de validade, `trust_configuration_digest`, `staff_native_configuration_digest` | catalogo Q2 e o bloco `human` da admissao (mesma imagem do motor) |
| admissao Q2 N+1 e humana espelho N+1; trust de atribuicao + dono | chave da fonte de atribuicao (`67938f5a...`), DSNs, logins |
| segredos: native-materials, staff-materials, human-materials, staff-job/materials, staff-install/rows | imagens (motor, app, operacao, portal) |

## 2. Pre-requisitos (estacao Windows com Docker Desktop e Git Bash)

```bash
REPO=D:/Work/Austa/Datalake/maezo-operadora
S=<scratchpad local, FORA do repo>; T=C:/Users/<voce>/AppData/Local/Temp
aws sts get-caller-identity --profile adm-dev          # SSO AdministratorAccess em 203312548462
aws sts get-caller-identity --profile adm-mgmt         # SSO da conta de gestao (para a OAAR)
docker volume inspect maezo-onda2b-materials >/dev/null # material + raiz (0700, root)
cd $REPO && docker build -q -f deploy/c1-local/runner.Dockerfile -t maezo-staff-runner:$(date +%Y%m%d) .
```

A ferramenta de material so roda em POSIX: tudo abaixo roda no container runner (repo `:ro`).

## 3. Ponto de rollback do banco

```bash
AWS_PROFILE=adm-dev aws rds create-db-cluster-snapshot --region sa-east-1 \
  --db-cluster-identifier amh-aurora-hapi-dev --db-cluster-snapshot-identifier pre-renovacao-staff-AAAAMMDD \
  --tags Key=tenant,Value=amh Key=environment,Value=dev Key=workload,Value=maezo-operadora Key=owner,Value=<voce> Key=cost_center,Value=maezo
```

## 4. Gerar (sem rede; assina dentro do container)

```bash
MSYS_NO_PATHCONV=1 docker run --rm --user 0 --network none \
  -v $REPO:/repo:ro -v maezo-onda2b-materials:/m -e PYTHONDONTWRITEBYTECODE=1 \
  maezo-staff-runner:AAAAMMDD python /repo/scripts/ops/staff_material_renovar_dev.py gerar \
  --anterior /m/onda11-renovacao-20261008/estado \
  --generate-spec /m/onda8c/public/spec.r2.json \
  --assignment-source /m/onda8/assignment/source/public/assignment-source.json \
  --sufixo AAAAMMDD --out /m/ondaNN-renovacao-AAAAMMDD
```

`--anterior` e SEMPRE o `estado/` da renovacao anterior (a de 08/10 e a primeira com esse layout).
A saida e um diretorio NOVO; nada anterior e tocado. O pacote staff passa pelo `decode_bundle` do
portal e o humano pelo `verify_materials` do job antes de gravar. Imprime so digests: guarde o
JSON (`public/resumo.json` no volume).

## 5. Publicar os segredos (OAAR; o SSO adm-dev nao cria versao por causa da SCP)

```bash
aws sts assume-role --profile adm-mgmt --role-arn arn:aws:iam::203312548462:role/OrganizationAccountAccessRole \
  --role-session-name renovacao-staff --duration-seconds 3600 \
  --query 'Credentials.[AccessKeyId,SecretAccessKey,SessionToken]' --output text > "$S/oaar.raw"
read A B C < "$S/oaar.raw"; rm -f "$S/oaar.raw"
printf 'AWS_ACCESS_KEY_ID=%s\nAWS_SECRET_ACCESS_KEY=%s\nAWS_SESSION_TOKEN=%s\nAWS_REGION=sa-east-1\n' "$A" "$B" "$C" > "$S/oaar.env"
MSYS_NO_PATHCONV=1 docker run --rm --user 0 --env-file "$S/oaar.env" \
  -v $REPO:/repo:ro -v maezo-onda2b-materials:/m:ro maezo-staff-runner:AAAAMMDD \
  python /repo/scripts/ops/staff_material_renovar_dev.py publicar --out /m/ondaNN-renovacao-AAAAMMDD \
  native-materials.json staff-materials.json portal-human.json job.json rows.json
```

`put_secret_value` com boto3, byte a byte (nunca `--output text` + `file://`: ja gerou quebra de
linha errada); staff/human com `ClientRequestToken` = `material_version_id` (o VersionId E o pin
do portal). Cada linha volta com `identico=True` e o VersionId: anote native e job.

## 6. Linhas no banco ANTES do motor (designacao N+1, admissao N+1, atribuicao requalificada)

```bash
export AWS_PROFILE=adm-dev AWS_REGION=sa-east-1 MSYS_NO_PATHCONV=1
NET="awsvpcConfiguration={subnets=[subnet-0e1f840dbec44e66a,subnet-06647b0c664d0d22a],securityGroups=[sg-0e2aebe1a2c1d253d],assignPublicIp=DISABLED}"
TA=$(aws ecs run-task --cluster maezo-operadora-dev --task-definition maezo-operadora-dev-staff-rows \
  --launch-type FARGATE --network-configuration "$NET" --query 'tasks[0].taskArn' --output text)
aws ecs wait tasks-stopped --cluster maezo-operadora-dev --tasks $TA
aws logs filter-log-events --log-group-name /ecs/maezo-operadora-dev/staff-rows \
  --start-time $(( ($(date +%s) - 900) * 1000 )) --query 'events[].[logStreamName,message]' --output text | grep ${TA##*/} | cut -f2
```

Esperado: `"ok": true`, `designation_current: rotacionada rN->rN+1`, `admission: inserida`,
`assignment: instalacao requalificada`. O `rows` le o segredo pelo AWSCURRENT (sem pin).

## 7. Task definitions (Terraform `-target`, var-files reconstruidos do que roda)

```bash
cd $REPO && python scripts/ops/staff_tfvars_vivos.py --saida "$S" \
  --native-version <VersionId native> --job-version <VersionId job> \
  --human-version <human_material_version_id> --human-manifest <human_public_manifest_sha256>
```

Edite `deploy/aws-ecs/envs/dev-sa-east-1/portal.auto.tfvars` (6 pins, do `resumo.json`):
`staff.material_secret_version_id`, `staff.public_manifest_sha256`, `staff.designation_sha256`,
`staff.native_configuration_sha256`, `human.material_secret_version_id`, `human.public_manifest_sha256`.
E `staff-material-validade.json` (`valid_until` = fim da janela MENOS 60 s, que e quando a prova de
instalacao vence; `renovado_em`, revisoes).

```bash
cd $REPO/deploy/aws-ecs/envs/dev-sa-east-1
eval "$(aws configure export-credentials --profile adm-dev --format env)"; unset AWS_PROFILE
# dev-phi.tfvars / allowlist.tfvars.json somem na limpeza do %TEMP%: recrie dos valores vivos
# (memoria aws-caminhos-sancionados-dev). O var-file que faltar NAO para o terraform (rc=0): grep Error:.
VF=(-var-file=$T/dev-phi.tfvars -var-file=$T/onda3-digests.tfvars -var-file=$S/allowlist.tfvars.json \
    -var-file=$S/cur-engine.tfvars.json -var-file=$S/cur-ops.tfvars.json)
terraform plan -input=false -no-color "${VF[@]}" -target=aws_ecs_task_definition.cibseven \
  -target=aws_ecs_service.cibseven -out=$S/motor.plan > $S/motor.txt; grep -c 'Error:' $S/motor.txt
grep -nE '^\s*[~+-] ' $S/motor.txt | grep -E 'valueFrom|image ' # SO o VersionId do native-materials muda
terraform apply -input=false $S/motor.plan
terraform plan -input=false -no-color "${VF[@]}" -target=aws_ecs_task_definition.portal -target=aws_ecs_service.portal \
  -target=aws_ecs_task_definition.staff_job -target=aws_scheduler_schedule.staff_job \
  -target=aws_ecs_task_definition.staff_assignment -out=$S/portal.plan > $S/portal.txt; grep -c 'Error:' $S/portal.txt
terraform apply -input=false $S/portal.plan
```

Conferir no plano: imagem do motor INALTERADA (o `onda3-digests.tfvars` local pode estar atrasado;
o `cur-engine` sobrescreve com o digest vivo); `hostPort`, listas vazias e `volume` re-listado sao
ruido de normalizacao do provider. NUNCA `-target` na TD do `staff_job` sem o
`aws_scheduler_schedule.staff_job` (o schedule fica apontando para revisao deregistrada e o job para
em silencio). `ecs wait services-stable`: o motor leva ~3 min (boot de 63 s + health).

## 8. Destravar o emissor e renovar a qualificacao AUTH

```bash
cd $REPO && AWS_PROFILE=adm-dev bash scripts/ops/staff_vpc_run.sh scripts/ops/staff_material_reparo_vpc.py \
  maezo-operadora-dev-staff-syn staff-syn staff-syn MODO=medir
AWS_PROFILE=adm-dev bash scripts/ops/staff_vpc_run.sh scripts/ops/staff_material_reparo_vpc.py \
  maezo-operadora-dev-staff-syn staff-syn staff-syn MODO=aplicar RENOVAR_ATE=<fim da janela, AAAA-MM-DDTHH:MM:SS.000000Z>
```

Por que: (a) o sidecar `staff-case-issuer` so semeia a politica `@dN+1` sem publicacao pendente nas
outras (`case_issuer_sources._seed`), e a rodada da politica antiga nunca mais acontece; o script so
descarta a pendencia que o engine NUNCA viu (sem recibo e sem source_event) e registra o SHA-256 dela.
(b) a qualificacao AUTH sintetica (`mzo_auth_installation.valid_until`) vence sozinha e nenhuma
ferramenta a renova. Depois do `aplicar`, o sidecar loga `staff_case_issuer_round` com
`policy_ref ...@dN+1` e `refused: 0` em ate ~1 min.

## 9. Verificacao

```bash
aws ecs describe-services --cluster maezo-operadora-dev --services cibseven maezo-operadora-dev-portal-76e3a981 \
  --query 'services[].[serviceName,runningCount,deployments[0].rolloutState]' --output text   # 1 COMPLETED cada
# log do motor: staff_native_configuration_digest=<o do resumo> e "Server startup", sem "refused"
# REST do motor de dentro da VPC (task bateria, sem credencial): /engine-rest/engine 200 e
#   /process-definition/count > 0 (deploy-processes NAO precisa rodar se o banco esta intacto)
# job T1.5: T15_RESULT ok=true em /ecs/maezo-operadora-dev/staff-job
# fila humana pelo cliente de producao (sem o BFF):
echo '{"containerOverrides":[{"name":"staff-job","command":["tools.staff_ops","task-read"]}]}' > $S/ov.json
aws ecs run-task --cluster maezo-operadora-dev --task-definition maezo-operadora-dev-staff-job --launch-type FARGATE \
  --network-configuration "$NET" --overrides file://$S/ov.json
curl -s -o /dev/null -w '%{http_code}\n' https://portal-maezo-dev.austa.com.br/api/v1/portal/session   # 401
# maezo-dev.austa.com.br esta atras do Cloudflare Access (302 para o login): confira o cloudflared
#   (/ecs/maezo-operadora-dev/cloudflared) sem "Unable to reach the origin" depois do motor subir.
```

## 10. Rollback

- Segredos: as versoes anteriores continuam (AWSPREVIOUS). Mas o material anterior esta vencido:
  voltar a ele nao traz o motor de volta. O caminho e corrigir para a frente (gerar de novo).
- Motor/portal: o circuit breaker do ECS volta sozinho para a revisao anterior se a nova nao sobe.
- Banco: a rotacao da designacao e para a frente (evento imutavel, so o ponteiro anda). Desastre =
  restore do snapshot do passo 3 (Aurora compartilhado: decisao do dono).

## 11. O que esta renovacao NAO cobre (ainda)

- **Geracao do plano de atribuicao** (`mzo_human_assignment_generation.valid_until`, 12 dias desde a
  ativacao de 26/09: venceu em 2026-10-08T00:08Z). A fonte esta `active`, entao
  `tools.staff_ops assignment-activate` devolve `ja-ativa` sem republicar; renovar exige
  `disable` + nova ativacao (`PostgresStaffAssignmentAdministration.prepare_change` a partir de
  `active` congela um disable), que a ferramenta nao faz. O segredo de ativacao novo (recibo do dono
  N+1) ja sai do `gerar` (`assignment-activate.json`), sem publicar.
- **Fixture SYN** (`staff-install/syn-fixture`): o `result_certificate` e o do AUTH antigo; regerar
  antes de rodar `staff_ops syn`.
- **Topico SNS `maezo-operadora-dev-staff-alerts` sem assinante** (medido em 08/10): o alarme
  `staff-job-2-falhas-seguidas` foi para ALARM as 04:52Z e nao avisou ninguem. Defina
  `staff_alerts_email` (o destinatario confirma a assinatura).

## Historico

- **08/10/2026 (Onda 11):** a janela de 25/09 venceu em 2026-10-08T04:44:25Z; o motor caiu no restart
  das ~17:40Z. Renovado com janela 2026-10-08T18:20:46Z .. 2026-10-22T18:20:46Z (prova ate 18:19:46Z):
  designacao r3 `ef548c55...`, admissao Q2 rev6 `e65ad672...`, `staff_native_configuration_digest`
  `c7eb48c0...`, trust `a1e0a68e...`, staff `amh-dev-staff-a734b48205220138f538815c28` (manifesto
  `f8387af8...`), human `amh-dev-human-1dced1fcdb361e47e278952fef` (manifesto `96f43b71...`),
  native-materials `d3c6dc4f-9ada-4f0f-b8f4-f9ae95aac8c9`, job `0660dd69-f333-4df9-82f5-5123a1c5e682`,
  rows `7c5cc2b8-0e7e-4d9f-97dd-aef61bc64228`; cibseven :17, portal :35. Emissor: pendencia
  `staff-cases-amh@3336` (de 07/10 10:11Z, nunca vista pelo engine) descartada; AUTH renovada.
  Snapshot `pre-onda11-renovacao-20261008`. Os arquivos foram gerados com o prototipo deste script; o
  `estado/` dessa saida foi montado a partir de `onda8c`/`onda10` e o `gerar` versionado foi ensaiado
  de ponta a ponta sobre ele (saida descartada). A entrada do native-secret derivada pelo script
  reproduz byte a byte a do segredo vivo v5 e a usada na v6.
