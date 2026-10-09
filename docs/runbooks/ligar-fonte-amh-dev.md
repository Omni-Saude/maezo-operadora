# Ligar a fonte AMH de cobranca do Lucas (dev)

O que e': trocar a fonte dos fatos de cobranca do Lucas de `simulada` para `amh` (os contratos
`billing-status` e `subject-resolution` da AMH, pelo ALB interno `amh-interop-maezo-dev`). So' em
`dev-sa-east-1`: a cerca `scripts/ci/check_roteador_lucas.py` reprova a variavel em qualquer outro
ambiente. Decisao do dono de 06/10/2026 (`docs/decisions-log.md`).

Quem faz: o time (quem aplica Terraform em dev). Tempo: uns 20 minutos, fora a promocao de imagem.

Enquanto `lucas_fonte_cobranca = "simulada"` (hoje) nada deste runbook existe no plano do Terraform.

---

## 1. Antes de comecar (todos precisam ser verdade)

1. O manifest v1.1 da AMH esta **PUBLISHED** em `main` da AMH (`schemas/contracts/maezo/v1.1/contract-manifest.yaml`
   com `status: PUBLISHED`, sem `<SET-AT-PUBLICATION>`). Ver `docs/runbooks/publicar-contratos-maezo-v1-1.md`
   no repositorio da AMH.
2. O PR do pin XRG-3 do Maezo foi mergeado: `config/integrations/amh/contracts.lock.json` tem o bloco
   `manifest_v1_1` com os 2 digests OpenAPI. Confira:
   ```
   uv run python scripts/ci/verify_amh_contract_pin.py
   uv run python scripts/ci/verify_amh_contract_pin.py --manifest <manifest-v1.1-publicado.yaml> --manifest-version v1.1
   ```
   Os dois tem de sair `PASS`. Sem o bloco, `billing_status.py` e `subject_resolution.py` recusam subir
   (sem digest), e o receptor nao sobe.
3. Os 2 OpenAPI estao **dentro da imagem**, em `config/integrations/amh/openapi/` (o `deploy/Dockerfile`
   copia `config/` para `/app/config`). Eles vem do commit AMH pinado e **nao se editam**; so' se
   confere o digest:
   ```
   mkdir -p config/integrations/amh/openapi
   SHA=<provenance.amh_commit_sha do bloco manifest_v1_1>
   for f in billing-status subject-resolution; do
     gh api -H "Accept: application/vnd.github.raw+json" \
       "repos/Omni-Saude/amh-data-platform/contents/schemas/openapi/maezo/v1/$f.openapi.yaml?ref=$SHA" \
       > config/integrations/amh/openapi/$f.openapi.yaml
     sha256sum config/integrations/amh/openapi/$f.openapi.yaml
   done
   ```
   Cada `sha256` tem de ser igual ao do `artifacts[]` do bloco `manifest_v1_1` no lock. Se diferir,
   pare: o pin ou os bytes estao errados.
4. Uma **imagem nova** com o pin e os 2 OpenAPI foi construida, assinada e promovida
   (`webhook_receiver_image_tag` em `variables.tf`; ver `docs/runbooks/supply-chain-imagem.md`). A imagem
   de hoje (`f7026a7e`) nao tem o bloco v1.1.
5. Credencial da conta de dados (`docs/runbooks/aws-ecs.md`, secao 2) e `terraform init` feito.
6. Os dois segredos da AMH existem (so' confira nomes, nunca leia o valor):
   ```
   aws secretsmanager describe-secret --secret-id amh/cognito/dev/maezo-operadora-interop --query Name
   aws secretsmanager describe-secret --secret-id amh/interop/phone-lookup-key --query Name
   ```

Valores ja' preenchidos em `deploy/aws-ecs/envs/dev-sa-east-1/amh-interop.auto.tfvars` (nao secretos,
lidos da AWS em 06/10/2026). Se algum mudar (ALB ou client recriados), atualize esse arquivo antes:

| Variavel | Valor |
|---|---|
| `amh_interop_base_url` | `http://internal-amh-interop-maezo-dev-885055285.sa-east-1.elb.amazonaws.com` |
| `amh_interop_alb_security_group_id` | `sg-06aecbfc06289c9cf` (`amh-interop-maezo-dev-alb-sg`) |
| `amh_interop_token_url` | `https://amh-maezo-bpm-dev.auth.sa-east-1.amazoncognito.com/oauth2/token` (pool `sa-east-1_9oKv7gHOJ`) |
| `amh_interop_client_id` | `3n2cmh41eslj5mi2lf0b577teb` (`maezo-operadora-interop`) |
| `amh_billing_status_openapi_path` | `/app/config/integrations/amh/openapi/billing-status.openapi.yaml` |
| `amh_subject_resolution_openapi_path` | `/app/config/integrations/amh/openapi/subject-resolution.openapi.yaml` |

Rede: o SG do ALB da AMH ja' aceita a porta 80 vinda do SG das tasks do Maezo
(`sg-0e2aebe1a2c1d253d`). O lado do Maezo (saida 80) ja' passa pela regra `hapi_internal` (CIDR da VPC)
e ganha uma regra propria por referencia ao SG do ALB com este apply.

---

## 2. A troca (uma linha)

Em `deploy/aws-ecs/envs/dev-sa-east-1/lucas.auto.tfvars`, mude:

```
lucas_fonte_cobranca = "amh"
```

(era `"simulada"`). Abra o PR; a cerca `check_roteador_lucas.py` precisa passar. Depois do merge, na
`main`:

## 3. Plano e apply apenas do receptor

```
cd deploy/aws-ecs/envs/dev-sa-east-1
terraform plan -input=false -out=amh.tfplan \
  -target=aws_vpc_security_group_egress_rule.amh_interop_alb \
  -target=aws_iam_role_policy.task_execution_secrets \
  -target=aws_ecs_task_definition.webhook_receiver \
  -target=aws_ecs_service.webhook_receiver
```

O plano tem de mostrar **somente**:

- `aws_vpc_security_group_egress_rule.amh_interop_alb[0]` criada (tcp 80 para `sg-06aecbfc06289c9cf`);
- `aws_iam_role_policy.task_execution_secrets` alterada: +2 ARNs de segredo e, se as chaves forem CMK,
  +2 chaves no `kms:Decrypt`;
- `aws_ecs_task_definition.webhook_receiver` substituida: `MAEZO_LUCAS_FONTE_COBRANCA = amh`, +8 env
  `MAEZO_AMH_*` e +2 `secrets` (`MAEZO_AMH_INTEROP_CLIENT_SECRET`, `MAEZO_AMH_PHONE_LOOKUP_KEY`);
- `aws_ecs_service.webhook_receiver` atualizado para a nova revisao.

Se aparecer **qualquer outra coisa** (destruir portal, tocar outro servico, mudar `image_tag` sem ser
a imagem nova prevista): pare e nao aplique. No PowerShell use `terraform --% ...` e nada depois do `--%`.

```
terraform apply -input=false amh.tfplan
```

O circuit breaker do servico (`rollback = true`) volta sozinho para a revisao anterior se a task nova
nao ficar saudavel.

---

## 4. Como verificar

1. Servico estavel:
   ```
   aws ecs describe-services --cluster maezo-operadora-dev --services webhook-receiver \
     --query "services[0].deployments[].[status,rolloutState,runningCount]"
   ```
   Esperado: um deployment `PRIMARY` `COMPLETED`.
2. Env na task definition (nomes e valores nao secretos; os 2 segredos aparecem em `secrets`, nunca com valor):
   `MAEZO_LUCAS_FONTE_COBRANCA=amh`, `MAEZO_AMH_INTEROP_BASE_URL`, `MAEZO_AMH_INTEROP_TOKEN_URL`,
   `MAEZO_AMH_INTEROP_CLIENT_ID`, `MAEZO_AMH_INTEROP_SCOPES`, `MAEZO_AMH_INTEROP_TENANT=austa_operadora`,
   `MAEZO_AMH_INTEROP_PURPOSE_OF_USE`, `MAEZO_AMH_BILLING_STATUS_OPENAPI_PATH`,
   `MAEZO_AMH_SUBJECT_RESOLUTION_OPENAPI_PATH`.
3. Log de boot, grupo `/ecs/maezo-operadora-dev/webhook-receiver`:
   - tem de aparecer `lucas_turno_construido` com `fonte_cobranca=amh`;
   - **nao** pode aparecer `amh_interop_config_ausente`, `amh_interop_escopos_insuficientes`,
     `billing_status_contract_invalid` nem erro de pin (`AmhContractPinError`). Qualquer um deles e'
     o receptor recusando subir de proposito: leia a mensagem, corrija a causa, ou faca o rollback (secao 5).
4. Teste de fumaca (sem WhatsApp, numeros `5511900000xxx` nao chegam a Meta): rode a bateria do roteador
   (`docs/plans/lucas-numero-unico.md` secao 6g, tarefa `maezo-operadora-dev-bateria`) com um caso de
   cobranca e confira nos logs a resposta do Lucas com fatos vindos da AMH. Do lado da AMH, o servico
   `amh-interop-maezo-dev` (cluster de mesmo nome) deve registrar a chamada autenticada com o client
   `maezo-operadora-interop`.
5. Se o token falhar (401/403 do Cognito): confira se o client ainda tem os 3 escopos e se o segredo
   `amh/cognito/dev/maezo-operadora-interop` foi rotacionado (a AMH rotaciona; o receptor le' o valor na
   subida da task, entao e' preciso reiniciar o servico apos uma rotacao).

---

## 5. Rollback (volta para `simulada`)

1. No `lucas.auto.tfvars`, volte a linha para `lucas_fonte_cobranca = "simulada"` (PR ou, em emergencia,
   edite localmente e depois registre o PR).
2. Mesmo `plan -target` e `apply` da secao 3. O plano tem de mostrar a regra de saida e os 2 segredos
   saindo, e a task definition voltando a `simulada` sem as env `MAEZO_AMH_*`.
3. Mais rapido, sem Terraform (so' em incidente): `aws ecs update-service --cluster maezo-operadora-dev
   --service webhook-receiver --task-definition maezo-operadora-dev-webhook-receiver:<revisao anterior>`.
   Depois alinhe o Terraform (passo 1-2), senao o proximo apply reaplica `amh`.

O roteador (`roteador_lucas_enabled`) nao muda: so' a fonte dos fatos. Com `simulada` o Lucas segue
respondendo com fatos simulados, como hoje.

## 6. Fatos do plano na Helena (`helena_consultas_amh`, DL-0081)

Desligado por padrao. So' depois de TODOS estes passos:

1. A AMH publicou o manifest aditivo **v1.2** (`schemas/contracts/maezo/v1.2/contract-manifest.yaml`, 1
   artefato: `schemas/openapi/maezo/v1/tina.openapi.yaml`) — XRG-2 com atestacao humana. Hoje (07/10/2026)
   ele e' DRAFT.
2. Pin XRG-3 por PR: `python scripts/ci/gerar_pin_v1_1.py --manifest-version v1.2 --gh-ref <commit> --verified-by
   "<pessoa>" --write`, depois `python scripts/ci/verify_amh_contract_pin.py --manifest <bytes> --manifest-version
   v1.2`. Os bytes do OpenAPI vao para `config/integrations/amh/openapi/tina.openapi.yaml` (sha256 = o do pin).
3. Base legal LGPD ratificada pelo DPO (DL-0081 a declara PENDENTE).
4. `helena_identidade_amh = true` ja' ligado (a variavel nova recusa o plan sem ela) e o client
   `maezo-operadora-interop` com o escopo `interop/tina.read` (ja' tem em dev).

A troca: `helena_consultas_amh = true` em `amh-interop.auto.tfvars`. O Terraform acrescenta
`interop/tina.read` a `MAEZO_AMH_INTEROP_SCOPES` e injeta `MAEZO_AMH_TINA_OPENAPI_PATH`. Sem o bloco
`manifest_v1_2` no pin o receptor RECUSA servir (`tina_contract_invalid` no `dispatcher_error`). Rollback:
`helena_consultas_amh = false`.

## 7. Cobranca com a finalidade do atendimento (`atendimento_whatsapp`, manifest v1.4)

Decisao do DPO de 08/10/2026 (AMH #223/#225, run XRG-2 37929182045): com `acesso_beneficiario = true` e a fonte
`amh`, a leitura de cobranca do Lucas usa o billing-status 0.2.0
(`config/integrations/amh/openapi/billing-status-atendimento.openapi.yaml`, digest no bloco `manifest_v1_4` do pin)
e declara `purpose_of_use=atendimento_whatsapp`, a mesma finalidade do consentimento do WhatsApp; o `consent_ref`
do registro do acesso vai como referencia de consentimento (enquanto a gravacao estiver pendente, a base legal de
execucao de contrato de sempre). O Terraform injeta `MAEZO_AMH_BILLING_STATUS_ATENDIMENTO_OPENAPI_PATH` (variavel
`amh_billing_status_atendimento_openapi_path`, default `/app/config/integrations/amh/openapi/billing-status-atendimento.openapi.yaml`)
SO' com o acesso ligado; sem o caminho, ou sem o bloco `manifest_v1_4` no pin, o receptor RECUSA servir
(`amh_interop_config_ausente: amh_billing_status_atendimento_openapi_path` / `billing_status_contract_invalid` no
`dispatcher_error`). Com o acesso desligado nada muda: billing-status 0.1.0 e `MAEZO_AMH_INTEROP_PURPOSE_OF_USE`.
O servico interop da AMH aceita as duas finalidades na mesma rota. Rollback: `acesso_beneficiario = false`.
