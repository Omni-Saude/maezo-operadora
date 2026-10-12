# Portal: sincronizacao automatica do egresso para o Cognito (dev)

## O que e

O SG das tasks do portal (`aws_security_group.portal`, hoje `sg-0d382c5d005a12995`) libera 443
so para `/32` exatos. Dois destinos do Cognito rotacionam IP e ja derrubaram o login 5 vezes
(#695, #720 e anteriores):

| Host | Uso no portal |
|---|---|
| `amh-maezo-bpm-dev.auth.sa-east-1.amazoncognito.com` | `POST /oauth2/token` (callback do login) |
| `cognito-idp.sa-east-1.amazonaws.com` | JWKS do issuer (validacao da sessao) |

PrivateLink nao cobre os endpoints de dominio do Cognito, entao a decisao do dono (12/10/2026,
opcao A) foi uma rotina automatica: a Lambda `maezo-operadora-dev-portal-cognito-egress-sync`
(codigo em `deploy/aws-ecs/lambdas/cognito_egress_sync/handler.py`, Terraform em
`deploy/aws-ecs/envs/dev-sa-east-1/portal-egresso-cognito-sync.tf`), disparada pelo EventBridge
Scheduler a cada 5 minutos, fora da VPC.

A cada rodada ela:

1. resolve os A records (IPv4) dos hosts (`HOSTS`) varias vezes, pelo DNS publico;
2. lista as regras de egress do SG e separa as **proprias** (tag
   `maezo:managed-by=cognito-egress-sync`) das demais (Terraform);
3. cria `tcp/443 <ip>/32` para cada IP que nenhuma regra existente cobre, com as tags
   `maezo:managed-by`, `maezo:host` e `maezo:last-seen`; nas proprias ainda resolvidas, atualiza
   `maezo:last-seen`;
4. remove apenas regras proprias com `last-seen` acima de `STALE_HOURS` (24 h).

Travas: nunca toca regra sem a tag (o IAM tambem impede revogar/retaguear regra sem a tag);
se qualquer host falhar ou resolver vazio, nao remove nada; nunca remove a ultima regra propria
de um host; no maximo `MAX_RULES` (20) regras proprias; se algum host resolver para IP
privado/reservado, a rodada aborta sem alterar nada (erro na Lambda).

Os `/32` manuais de `portal.auto.tfvars` continuam valendo. A rotina complementa: IP ja coberto
pelo Terraform nao ganha regra propria. Nao e preciso mais editar a lista a cada rotacao. Se um
IP passar a ser fixado no Terraform, a regra propria dele deixa de ser necessaria e expira
sozinha em 24 h.

## Ver o que ela fez

Log group: `/aws/lambda/maezo-operadora-dev-portal-cognito-egress-sync` (30 dias). Uma linha
JSON por evento, so contagens, IDs de regra e IPs publicos da AWS:

| `event` | Significado |
|---|---|
| `rule_created` | regra nova (`ip`, `host`, `rule_id`), a linha de auditoria |
| `rule_removed` | regra propria expirada removida |
| `sync_summary` | resumo da rodada (resolvidos, criados, removidos, atualizados) |
| `resolve_failed` / `resolve_empty` | DNS falhou, e a remocao fica desligada nessa rodada |
| `max_rules_reached` | teto atingido, com os IPs que ficaram sem regra |
| `abort_non_public_ip` | IP privado/reservado resolvido, rodada abortada |

```bash
aws logs tail /aws/lambda/maezo-operadora-dev-portal-cognito-egress-sync --since 1h \
  --region sa-east-1 --filter-pattern '"rule_created"'
```

Metricas (Embedded Metric Format), namespace `maezo-operadora/dev/portal`, dimensao
`SecurityGroupId`: `RulesCreated`, `RulesRemoved`, `RulesRefreshed`, `ManagedRules`,
`ResolveFailures`, `MaxRulesSkipped`, `NonPublicAbort`.

Alarme: `maezo-operadora-dev-portal-cognito-egress-sync-falhando` dispara com 3 rodadas seguidas com
erro, ou sem invocacao. Avisa pelo topico `staff-alerts` quando ele existe.

Regras proprias no SG:

```bash
aws ec2 describe-security-group-rules --region sa-east-1 \
  --filters Name=group-id,Values=sg-0d382c5d005a12995 Name=tag:maezo:managed-by,Values=cognito-egress-sync \
  --query 'SecurityGroupRules[].{id:SecurityGroupRuleId,cidr:CidrIpv4,tags:Tags}'
```

Para comparar com o que o Terraform fixa, use o verificador de drift ja existente
(`scripts/ops/cognito_egress_check.py`).

## Desligar

- **Pausar sem apagar nada:** desabilite o agendamento
  (`aws scheduler update-schedule ... --state DISABLED`, repetindo os campos obrigatorios) ou, pelo
  Terraform, mude `schedule_expression` / `enabled`. As regras proprias ficam como estao.
- **Remover a rotina:** `portal_cognito_egress_sync = { enabled = false }` num `*.auto.tfvars` e
  `terraform apply` com os mesmos `-target` abaixo. As regras **proprias ja criadas
  permanecem** no SG, porque o Terraform nao as conhece. Para limpar, revogue pelas IDs listadas acima
  (`aws ec2 revoke-security-group-egress --group-id ... --security-group-rule-ids ...`), mas so
  depois de confirmar que os IPs atuais estao nos `/32` do Terraform. Senao o login quebra.

## Aplicar

Nao faz parte de nenhum apply sem alvo pendente. Na raiz `deploy/aws-ecs/envs/dev-sa-east-1`,
com o backend real (README) e o provider `hashicorp/archive` ja no lock:

```bash
terraform init   # baixa hashicorp/archive (novo no lock)
terraform plan \
  -target='aws_cloudwatch_log_group.cognito_egress_sync["this"]' \
  -target='aws_iam_role.cognito_egress_sync["this"]' \
  -target='aws_iam_role_policy.cognito_egress_sync["this"]' \
  -target='aws_lambda_function.cognito_egress_sync["this"]' \
  -target='aws_lambda_function_event_invoke_config.cognito_egress_sync["this"]' \
  -target='aws_iam_role.cognito_egress_sync_scheduler["this"]' \
  -target='aws_iam_role_policy.cognito_egress_sync_scheduler["this"]' \
  -target='aws_scheduler_schedule.cognito_egress_sync["this"]' \
  -target='aws_cloudwatch_metric_alarm.cognito_egress_sync_errors["this"]' \
  -out=cognito-egress-sync.tfplan
terraform apply cognito-egress-sync.tfplan
```

O plan deve mostrar so criacoes (9 recursos), sem mudanca no SG nem nas regras do Terraform.
Depois do apply, espere uma rodada (ate 5 min) e confira `sync_summary` no log. Com a lista
manual atualizada, o esperado e `created: []`. Se o Cognito ja tiver rotacionado, aparecem
`rule_created` com os IPs novos.
