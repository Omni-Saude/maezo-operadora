# Experimento: a Helena redige respostas de saúde

Aprovado pelo dono em 17/10/2026 ("Avance"). Só dev. **Não altera a Helena que está no ar nem as chaves do dev.**

## Objetivo

Decidir o futuro das respostas de saúde da Helena. Hoje ela responde com textos fixos da base
(`spec/conhecimento/saude/`, 206 assuntos, ADR-0064) e o modelo só classifica — o dono testou e achou ruim
(faltam assuntos, a busca por palavras casa errado, ignora o que a pessoa disse, soa robótico).

A abordagem testada: **o modelo redige** com as próprias palavras, sob regras rígidas (`PROMPT_SISTEMA` em
`experimento.py`), apoiado no trecho/link oficial da base quando a pergunta casa com um assunto que tem fonte
de responder (Saúde de A a Z, INCA, Calendário de Vacinação, ANS), e cada texto passa por uma **verificação
automática** antes de "enviar":

1. as cercas de saída que já existem no repo — Helena (`motivo_de_recusa`, `motivo_de_canal_nao_confirmado`,
   `_conduta_clinica`) e base (`termos_proibidos_no_texto`);
2. as regras do experimento (`regras_do_experimento`): dose numérica, mandar usar/parar remédio, liberar
   combinação, minimizar, diagnóstico afirmativo, 192 no início quando a mensagem tem sinal de alarme (detectores
   que a Helena já tem), CVV 188 quando há risco à vida, tamanho, markdown, link/fonte não fornecidos.

Reprovou → **uma** reescrita com o que corrigir; reprovou de novo → resposta segura padrão. Registra se passou de
primeira, se reescreveu ou se caiu no padrão.

Modelos (mesmo prompt, mesmo `max_tokens`): `mistral.mistral-large-3-675b-instruct` e `zai.glm-5` (sa-east-1,
temperatura 0,2) e `global.anthropic.claude-sonnet-5-5` / `global.anthropic.claude-haiku-5-5` (perfil global; os
Claude 5.x recusam `temperature` e rodam sem ele, com `effort=low`). Juiz: `global.anthropic.claude-opus-5` em
todas as respostas; `zai.glm-5` julga de novo uma amostra de 30 perguntas para medir a concordância (o viés de
Claude julgando Claude). As 9 perguntas do dono também têm a resposta **atual** da Helena (reconstruída com as
mesmas funções da base) julgada pelo mesmo juiz.

As perguntas (`perguntas.yaml`, 100) são **sintéticas**, sem nenhum dado de beneficiário — por isso o perfil
global é aceitável neste experimento.

## Como rodar

Pré-requisito: o PR com este código **mesclado** em `main` e a política IAM experimental aplicada
(`deploy/aws-ecs/envs/dev-sa-east-1/experimento-redacao-saude.tf`, apply direcionado).

```bash
export AWS_PROFILE=amh-data-dev
git fetch
python scripts/experimentos/redacao_saude/rodar_na_aws.py disparar            # roda tudo (origin/main)
python scripts/experimentos/redacao_saude/rodar_na_aws.py disparar -- --limite 5 --sem-juiz   # ensaio
```

O `disparar` grava `experimento.py` e `perguntas.yaml` **do commit mesclado** em
`s3://maezo-operadora-dev-qa-simulador-203312548462/experimentos/redacao-saude/codigo/<sha>/` e chama
`ecs run-task` da task definition do simulador com o comando sobrescrito (a imagem não copia `scripts/`; um
carregador de 6 linhas lê o arquivo do S3). Logs: `/ecs/maezo-operadora-dev/simulador`. Reexecutar na mesma data
**retoma** (o que já foi gerado/julgado não é refeito). Cotas: no máximo 2 chamadas simultâneas por modelo e
0,6 s entre chamadas; erro do serviço (throttling, 5xx, timeout) é repetido até 3 vezes com espera crescente.

Depuração local (poucas perguntas): `python scripts/experimentos/redacao_saude/experimento.py --perguntas
scripts/experimentos/redacao_saude/perguntas.yaml --saida <pasta> --ids q001,q086 --sem-juiz`
(com `src` e a raiz do repo no `PYTHONPATH`).

## Onde ficam os resultados

`s3://maezo-operadora-dev-qa-simulador-203312548462/experimentos/redacao-saude/<data>/` (SSE-KMS, a CMK do
bucket do simulador):

| arquivo | conteúdo |
|---|---|
| `manifesto.json` | commit, prompts, modelos, preços, parâmetros |
| `respostas.jsonl` | por pergunta × modelo: apoio injetado, 1ª tentativa, violações, reescrita, resposta final, desfecho, tokens, latência |
| `atual.json` | a resposta atual (reconstruída) da Helena para as 9 perguntas do dono |
| `juiz_opus.jsonl` / `juiz_glm.jsonl` | vereditos dos juízes (notas, booleanos, justificativa) |
| `resumo.json` | médias por modelo e categoria, concordância entre juízes |

Planilha:

```bash
python scripts/experimentos/redacao_saude/rodar_na_aws.py baixar --data AAAA-MM-DD --para <pasta>
python scripts/experimentos/redacao_saude/planilha.py --resultados <pasta> \
    --perguntas scripts/experimentos/redacao_saude/perguntas.yaml --xlsx Experimento_Redacao_Saude.xlsx
```

## Depois do experimento

A política IAM é **experimental**: apagar `experimento-redacao-saude.tf`, PR, merge e apply direcionado
(`-target=aws_iam_role_policy.simulador_experimento_redacao_saude`). Apagar o prefixo do bucket quando o dono não
precisar mais dos resultados.
