# Provisionamento do WhatsApp outbound (por tenant)

Este runbook descreve **como** o dono provisiona o número remetente (WABA) de um tenant e **o que a
cerca de pré-voo do chart garante**. Ele **não executa nada**: a execução é ato do dono
(**GTM-04/O1, OWNER-DEPLOY**) e, enquanto esse ato não acontece, o outbound segue desligado em todo
ambiente (E-SF-14 / GTM-E-44; GT-GTM-077 e GT-GTM-078).

> **Proibição vigente.** Ligar outbound para **beneficiário** segue proibido: não existe texto de
> consentimento de admissão (Adendo 01, §7 (a)). Este runbook prepara a infraestrutura; ele não
> autoriza nenhum envio a beneficiário. O lembrete de cobrança (`lembrete-cobranca.md`) continua
> dev, em dry-run e preso a allowlist.

## 1. O que existe hoje

| Peça | Onde | Estado |
|---|---|---|
| `whatsapp.phoneNumberId` (id Graph do número remetente, **não é segredo**) | `deploy/helm/maezo-tenant/values.yaml` | `""` (nada provisionado) |
| `whatsapp.outbound.enabled`, `whatsapp.lembrete.enabled` | `deploy/helm/maezo-tenant/values.yaml` | `false` |
| Env `WHATSAPP_PHONE_NUMBER_ID` do receptor | `templates/deployment-webhook-receiver.yaml` (`value:` vindo de `whatsapp.phoneNumberId`) | vazio |
| ExternalSecret das 3 credenciais (`waba-token`, `app-secret`, `verify-token`) | `templates/externalsecret.yaml` → Secret `maezo-whatsapp-config` | BLOQUEADO (onboarding WABA) |
| Segredo ECS `maezo/<env>/whatsapp/meta` (JSON com `app_secret`, `verify_token`, `waba_token`, `phone_number_id`) | `deploy/aws-ecs/envs/{dev,prod}-sa-east-1/secrets.tf` | casca criada; valores populados **fora** do Terraform |
| Cerca de pré-voo | `templates/_helpers.tpl` (`maezo-tenant.whatsapp.requirePhoneNumberId`) disparada por `templates/guard-whatsapp-outbound.yaml` | ativa |

As flags `whatsapp.outbound.enabled` e `whatsapp.lembrete.enabled` do chart são **declaração de
intenção para a cerca**: por si sós não ligam envio algum (nenhum outro template as lê). O envio real
depende dos interruptores do código (`MAEZO_LEMBRETE_COBRANCA` etc., ver `lembrete-cobranca.md`) e da
decisão do dono.

## 2. A cerca de pré-voo (o que ela barra)

Com `whatsapp.outbound.enabled=true` **ou** `whatsapp.lembrete.enabled=true` e
`whatsapp.phoneNumberId` vazio (ou só espaços), `helm template`/`helm install` **falha no render**
com mensagem que nomeia a chave `whatsapp.phoneNumberId`. Com as duas flags desligadas (default), id
vazio é o estado normal e passa. Sem a cerca, o chart renderizaria `WHATSAPP_PHONE_NUMBER_ID=""` e a
falha só apareceria em runtime (`send_message` fail-closed em `src/maezo/tools/mcp_whatsapp/server.py`).

O marcador `chart_required` de `scripts/ci/check_chart_env_reconciliation.py` só prova que o chart
**declara** a variável; quem barra o valor vazio é esta cerca.

Provas: `tests/unit/deploy/test_chart_whatsapp_outbound_guard.py`. Os dois testes nomeados
(`test_chart_refuses_enabled_outbound_with_empty_phone_number_id`,
`test_chart_allows_empty_phone_number_id_when_outbound_off`) leem o template real e avaliam a cerca sem
precisar do binário `helm`; os testes `*_rendered_by_helm` renderizam o chart de verdade e rodam no job
`lint / type / unit` do CI (shard `unit (restante)`, com o helm pré-instalado na imagem `ubuntu-latest`).

## 3. Passos do dono, por tenant (execução = GTM-04/O1)

Nada abaixo é feito por engenharia sem o ato do dono. Valores reais **nunca** entram em repositório,
chat ou ticket.

1. **Número WABA do tenant.** O dono obtém o número remetente no Meta Business Manager e anota o
   *Phone number ID* (identificador numérico do Graph, não o telefone). Um valor **por tenant**; tenants
   não compartilham remetente. Registrar a correspondência tenant → id apenas no cofre/registro do dono.
2. **Credenciais.** Popular, fora do Terraform e fora do git:
   - Helm/EKS: as três entradas lidas pelo ExternalSecret `whatsapp-config` no prefixo
     `externalSecrets.secretPathPrefix` do tenant (`<prefixo>/whatsapp/waba-token` propriedade `token`;
     `.../app-secret` propriedade `app_secret`; `.../verify-token` propriedade `verify_token`). Ao
     entregar, remover a anotação `maezo.io/blocked` do ExternalSecret por PR próprio.
   - ECS: o JSON do segredo `maezo/<env>/whatsapp/meta` (definido em
     `deploy/aws-ecs/envs/{dev,prod}-sa-east-1/secrets.tf`) com os **quatro** campos `app_secret`,
     `verify_token`, `waba_token` e `phone_number_id`. As tasks leem `phone_number_id` desse JSON; a
     cerca do chart **não** cobre o caminho ECS, então confira o campo manualmente antes de iniciar a
     task. Não editar o Terraform para isso: só o valor do segredo muda.
3. **Valor do chart.** Em PR de `deploy/**` com owner-review, preencher no values do tenant
   (`values-<tenant>.yaml`) `whatsapp.phoneNumberId: "<id entre aspas>"` (string; sem aspas o YAML
   converte número longo em notação científica). A cerca exige presença, não valida o conteúdo.
4. **Flags.** Só depois dos passos 1–3 e do ato do dono, `whatsapp.outbound.enabled` /
   `whatsapp.lembrete.enabled` podem ir a `true` no mesmo PR ou em PR seguinte. Se o id estiver vazio, o
   render falha e o PR não passa no CI.
5. **Registro de templates.** Fora da janela de 24 h só sai template aprovado pela Meta. Submeter no
   WhatsApp Manager (Modelos de mensagem) os modelos aprovados pelo dono; o texto e o nome sugerido do
   lembrete estão em `lembrete-cobranca.md` (§ Template a submeter). O nome aprovado vai em
   `MAEZO_LEMBRETE_TEMPLATE_NAME` (e o idioma em `MAEZO_LEMBRETE_TEMPLATE_IDIOMA`). Um registro de
   templates no código (GTM-E-42) é trabalho à parte; até lá o registro é o próprio Meta Business
   Manager mais a variável de ambiente.

## 4. Teste de fumaça (depois do ato do dono)

Ordem do mais inofensivo ao mais real; pare no primeiro item que falhar.

1. `helm template <release> deploy/helm/maezo-tenant -f <values do tenant>` deve renderizar sem erro e
   exibir `WHATSAPP_PHONE_NUMBER_ID` com o id do tenant (e `valueFrom` nas três credenciais).
2. Handshake do webhook (GET `/webhook` com `verify_token`) e POST assinado de teste conforme
   `whatsapp-webhook.md`; resposta 200 só com assinatura válida.
3. Lembrete em **dry-run** (`MAEZO_LEMBRETE_DRY_RUN=true`): só contagens no log, nenhum envio.
4. Envio real único, **somente** a um número de teste controlado pela equipe, listado na allowlist
   (`MAEZO_LEMBRETE_ALLOWLIST_CONVERSAS`), com `MAEZO_LEMBRETE_EXIGIR_ALLOWLIST=true`. Nunca a
   beneficiário. Verificar entrega e a métrica de selo de idempotência de outbound.
5. Desligar de novo as flags se o teste fizer parte de uma janela de validação sem ato de produção.

## 5. Reversão

`whatsapp.outbound.enabled=false` e `whatsapp.lembrete.enabled=false` (e `MAEZO_LEMBRETE_COBRANCA=false`
no ECS). O id pode permanecer preenchido; a cerca só age com flag ligada.

## 6. Fora de escopo deste runbook

- Qualquer envio a beneficiário (depende do texto de consentimento de admissão; Adendo 01, §7 (a)).
- Campanhas outbound (gate C9 / SP-OP-CAMPANHA-001) e o gate de outbound (OutboundGate).
- Alteração de Terraform, de `.github/workflows/` ou de segredos.
