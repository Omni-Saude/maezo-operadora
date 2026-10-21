# Rascunhos da base de saúde pelo Claude Sonnet 5.5 (fase 5, só dev)

Aprovado pelo dono (filipecarmo81, CTO e DPO) em 18/10/2026: ampliar a base de saúde da Helena
(`spec/conhecimento/saude/`, ADR-0064) com ~100 assuntos novos, escritos como **rascunho** pelo Claude
Sonnet 5.5, para a **revisão médica** futura. Registro: DL-0120.

## O que entra e o que não entra

- **Entra:** `assuntos.yaml` — saúde da mulher (endometriose, SOP, mioma, climatério/menopausa, osteoporose…),
  crônicas, infecções comuns, saúde mental sem risco, exames, procedimentos, cirurgias e vacinas. A lista saiu
  das 100 perguntas do experimento de redação (#819), das 270 conversas sintéticas do repo arquivado
  `Maezo-Healthcare-Plan` e das categorias comuns na operadora.
- **Não entra:** `sintoma`. Sinal de alarme só vem das tabelas `triage_redflag_*` (lição do #818: "dor muito
  forte" isolada não é alarme). Nada de medicamento novo nesta rodada.
- **Nenhum dado de beneficiário** vai ao modelo: só o título do assunto e, quando há, o trecho da página
  oficial pública. Por isso o perfil **global** do Bedrock (`global.anthropic.claude-sonnet-5-5`, sem
  `temperature`, `effort=low`) é aceitável aqui.

## Fontes (lista fechada, `spec/conhecimento/saude/FONTES.md`)

- Página leiga da lista fechada que trata do assunto (Saúde de A a Z, INCA "versão para população",
  Calendário Nacional de Vacinação): vira a fonte creditada e o `link_leitura`. `coletar_paginas.py` confere
  que cada URL responde (HTTP 200) antes da geração.
- Exame, procedimento e cirurgia: o Rol da ANS (o mesmo apoio dos rascunhos de 17/10/2026).
- **Nenhuma página leiga da lista fechada** trata do assunto (ex.: SOP, mioma, osteoporose, gastrite):
  **referência sem link** (`url: null`, nome "Sem página leiga oficial na lista fechada…"). O crédito é só
  "Texto da operadora." — nunca um link inventado nem uma fonte que não foi consultada. O médico confere o
  conteúdo; se houver fonte oficial, ela entra por PR.

## Marca no arquivo

```yaml
tipo_texto: proprio
revisao:
  status: rascunho_pendente_revisao_medica
  autor: Claude (assistente de IA)
  aprovado_por: dono (18/10/2026, fase 5, dev)
  revisao_medica: pendente
  revisar_ate: <coleta + 6 meses>
  escrito_por: claude-sonnet-5.5 (rascunho)
```

O rascunho responde em dev **exatamente como os outros textos da base**, só com a chave de sempre
(`MAEZO_HELENA_BASE_SAUDE`, desligada fora do dev). `base_saude.rascunhos_de_modelo()` e
`pendentes_de_revisao_medica()` listam o que falta o médico assinar.

## Checagem de cada rascunho (`gerar.py`)

1. as cercas de saída da Helena (`motivo_de_recusa`, canal não confirmado, anúncio de encaminhamento);
2. os termos proibidos da base (dose, mg, "tome", "tomar", "é normal", "não é grave", diagnóstico, cobertura);
3. a régua de tom (frase ≤ 20 palavras) e ≤ 600 caracteres;
4. a **checagem de conduta** da saúde gerativa (`saude_gerativa.verificar`: dose, mandar usar/parar, dose
   esquecida, liberar combinação, minimizar, diagnóstico, promessa, gênero, link);
5. regras desta fase: sem alarme/pronto-socorro/192, sem "dor muito forte", sem frequência ("todo ano",
   "a cada X anos"), sem "você deve", sem idade/esquema de vacina, sem telefone/link, sem markdown.

Reprovou → **uma** reescrita; reprovou de novo → `reprovado` (não entra). Depois, o Claude Opus 5 faz uma
**leitura de conduta** independente (conduta indevida, erro factual provável, alarme inventado, linguagem),
que vai para a planilha do médico. O CI confere tudo de novo nos YAML
(`tests/unit/agents/test_helena_base_saude_conteudo.py`).

## Como rodar

Pré-requisitos: este código **mesclado** em `main` e a política IAM experimental aplicada
(`deploy/aws-ecs/envs/dev-sa-east-1/experimento-redacao-saude.tf`, prefixo `experimentos/base-saude-rascunhos/`).

```bash
export AWS_PROFILE=amh-data-dev
git fetch
# 1) no notebook (precisa de internet): baixa e confere as páginas oficiais
python -I scripts/base_saude_rascunhos/coletar_paginas.py --saida <pasta>/paginas.json
aws s3 cp <pasta>/paginas.json \
  s3://maezo-operadora-dev-qa-simulador-203312548462/experimentos/base-saude-rascunhos/entrada/paginas.json \
  --sse aws:kms --sse-kms-key-id <CMK do simulador>
# 2) a tarefa na AWS (código de origin/main)
python scripts/tarefa_aws/lancar.py disparar --script scripts/base_saude_rascunhos/gerar.py \
  --prefixo experimentos/base-saude-rascunhos --proposito base-saude-rascunhos -- \
  --paginas s3://maezo-operadora-dev-qa-simulador-203312548462/experimentos/base-saude-rascunhos/entrada/paginas.json
# 3) baixar e montar os YAML (local)
python scripts/tarefa_aws/lancar.py baixar --prefixo experimentos/base-saude-rascunhos --data AAAA-MM-DD --para <pasta>
python scripts/base_saude_rascunhos/montar.py --rascunhos <pasta>/rascunhos.jsonl --paginas <pasta>/paginas.json \
  --relatorio <pasta>/montagem.json
```

`montar.py` nunca sobrescreve arquivo existente, tira sinônimo que já é de outro assunto (um sinônimo pertence
a um único id) e os genéricos demais, carrega a base inteira e **apaga** o arquivo que reprovar no schema ou na
política. Logs da tarefa: `/ecs/maezo-operadora-dev/simulador`.

## Desfazer

Apagar os arquivos com `status: rascunho_pendente_revisao_medica` (ou revert do PR do conteúdo). O prefixo
`experimentos/base-saude-rascunhos/` do bucket pode ser apagado quando o dono não precisar mais dele.
