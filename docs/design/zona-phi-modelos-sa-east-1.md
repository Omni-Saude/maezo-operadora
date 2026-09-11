# Zona PHI: quais modelos existem de verdade em sa-east-1

**Pergunta que este documento fecha** (direção, 11/09/2026): *existe alternativa BR-resident ao
Mistral?* — levantada como pré-condição para ligar `helena_zona_phi=true`.

**Resposta curta: sim, 36 alternativas, e nenhuma delas é "mais BR-resident" que o Mistral.**
A residência não vem do fornecedor do modelo; vem da região do endpoint e do ARN regional do
modelo. Trocar de fornecedor dentro de sa-east-1 não muda residência nenhuma — muda qualidade de
saída. Por isso a pergunta, embora legítima, não é um bloqueio: ela já está respondida.

## Medição ao vivo (11/09/2026, `aws bedrock list-*`, conta 169446931765, região sa-east-1)

| Como o modelo é servido | Quantos | Residência |
|---|---|---|
| `ON_DEMAND` — modelo regional, ARN preso em `sa-east-1` | **37** | fica no Brasil |
| `INFERENCE_PROFILE` — só via perfil `global.*` | 17 | **roteia entre regiões** |

Os 17 da segunda linha não têm perfil regional: todos os perfis `SYSTEM_DEFINED` visíveis em
sa-east-1 são da forma `global.*`. Não existe `sa-east-1.anthropic.*`. Verificado modelo por
modelo.

**Consequência que importa, e é contraintuitiva:** os modelos de fronteira que alguém
naturalmente preferiria — Claude Opus 5, Claude Sonnet 5, GPT-6, Grok 4.6 — são exatamente os que
**não podem** servir a zona PHI, porque só existem atrás de um perfil que sai do Brasil por
desenho. O `secrets.tf` já registra isso: o `global.*` é aceito na zona GERAL e a região fica
`*`; na zona PHI a região está **fixa** em `sa-east-1` no ARN, e essa fixação é a garantia.

### Os in-region relevantes (subconjunto dos 37)

`mistral.mistral-large-3-675b-instruct` (escolhido) · `deepseek.v3.2` · `zai.glm-5` ·
`qwen.qwen3-next-80b-a3b` · `qwen.qwen3-vl-235b-a22b` · `nvidia.nemotron-super-3-120b` ·
`minimax.minimax-m2.5` · `moonshotai.kimi-k2.5` · `openai.gpt-oss-120b-1:0` ·
`google.gemma-3-27b-it` · `anthropic.claude-3-sonnet-20240229-v1:0` (2024) ·
`anthropic.claude-3-haiku-20240307-v1:0` (2024).

Os únicos Anthropic in-region são os dois de 2024. Um Claude atual na zona PHI implicaria
cross-region — ou seja, desfazer a residência.

## Por que Mistral Large 3, e não outro

Escolha de 19/08/2026, registrada em `deploy/aws-ecs/envs/dev-sa-east-1/variables.tf`
(`phi_model_id`): quatro candidatos in-region foram testados com prompt de dossiê real
(`mistral.mistral-large-3-675b-instruct`, `deepseek.v3.2`, `zai.glm-5`,
`qwen.qwen3-next-80b-a3b`); **todos viáveis**, o Mistral produziu o texto mais bem estruturado.
É o maior modelo in-region disponível. Trocar depois é uma linha (`-var phi_model_id=...`) mais o
ARN do IAM, que já é parametrizado pela mesma variável.

## Nenhum fornecedor novo entra com este flip

O instrumento contratual da zona PHI é `phi_vendor_dpa_ref` = os Termos de Serviço da AWS, o
contrato que a empresa já tem. `rafael` e `marina` rodam **este mesmo** provedor, modelo, endpoint
e instrumento desde o apply de 20/08/2026. Ligar a Helena estende uma decisão de agosto a um
terceiro agente; não é uma aceitação nova de fornecedor.

## O que foi provado antes de recomendar (11/09/2026)

1. **Boot.** Com as variáveis exatas que o apply escreve, a fachada de inferência constrói
   `BrResidentInferenceProvider`: `phi_capable=True`, `deployment_region=br-sao-paulo`,
   `retention_policy=zero-retention`, `max_data_classification=phi`. Os cinco critérios de
   `phi_zone_denial_reasons` passam. Hoje, com `bedrock`, o mesmo boot dá
   `phi_capable=False` e todo turno morre em `PhiZoneRoutingError`.
2. **Chamada real.** Invocação ao vivo em sa-east-1 respondeu com
   `served_region=br-sao-paulo`, `region_evidence_source=regional_direct_model_contract`,
   `retention_evidence_source=operator_contract_reference`.
3. **O objetivo, não só o boot.** O prompt REAL de classificação da Helena (`classify-v2`) rodou
   contra o modelo com três mensagens **sintéticas** e devolveu JSON válido no vocabulário que as
   tabelas DMN consomem nos três casos:

   | Mensagem sintética | Saída do modelo |
   |---|---|
   | dor no peito forte + falta de ar | `intent=symptom`, `dor_toracica`, `grave`, `adult` |
   | cobertura de dermatologista | `intent=information`, sem sintoma |
   | filha de 3 anos, febre 39, vômito | `intent=symptom`, `febre`, `grave`, `pediatric`, `idade_meses=36` |

   `risco_imediato` veio `null` nos três — correto: quem decide bandeira vermelha e conduta é o
   DMN, nunca o modelo (ADR-0012). Nenhum dado de paciente foi usado.

4. **Permissão e rota já existem.** `secrets.tf` concede `bedrock:InvokeModel` no ARN regional
   `arn:aws:bedrock:sa-east-1::foundation-model/${var.phi_model_id}`; o SG tem saída 443 pela NAT;
   `phi_endpoint_url` já aponta para o host da allowlist. Nada de infraestrutura falta.

## O que o flip NÃO resolve

- **Ratificação da narrativa do dossiê** (`spec/policies/phi/dossier-narrative-zone.yaml`) segue
  pendente e independente: precisa de DPO e médico auditor. Este flip não a toca.
- **`triage_sufficiency`** (passo 4, coleta) segue sem ratificação.
- O flip é `-var`, não default. `helena_zona_phi` continua `false` no repo de propósito: a
  atestação e a autorização são atos de dono, não linhas de código.

## Como executar

```
terraform -chdir=deploy/aws-ecs/envs/dev-sa-east-1 apply \
  -var-file=<atestacao PHI local, fora do repo> \
  -var helena_zona_phi=true
```

Sem o var-file da atestação, `rafael` e `marina` caem em `phi_zone_mock` no mesmo apply. Conferir
no plan que os dois seguem em `bedrock_br` antes de aplicar. Reverter é o mesmo comando sem a
variável.
