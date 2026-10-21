# ADR-0065: Saúde gerativa da Helena — redação pelo Claude Haiku 5.5 por rota global autorizada pelo DPO

**Status:** Accepted pelo dono para dev (17/10/2026), experimento reversível; NÃO vale para produção
**Data:** 2026-10-17
**Área:** Agentes (Helena) | Privacidade/LGPD (residência de dados) | Inferência (ADR-0006, ADR-0009, ADR-0017)
**Decisor:** dono do repositório (filipecarmo81 — CTO e **DPO**), como DPO, só para a conversa dele, só em dev.
**Registro curto:** DL-0119 (`docs/decisions-log.md`). **Runbook:** `docs/runbooks/helena-base-saude.md` §10.
**Evidência que aprovou o desenho:** experimento `scripts/experimentos/redacao_saude/` (PR #819), resultados em
`s3://maezo-operadora-dev-qa-simulador-203312548462/experimentos/redacao-saude/2026-10-17/` (Haiku 5.5:
utilidade 4,1/5, seguiu o esperado 79%, 21% reescritas, 1 conduta indevida — corrigida aqui).

## Contexto

Com a base de saúde (ADR-0064) a Helena responde com textos fixos; o dono testou em 17/10/2026 e achou ruim
(faltam assuntos, a busca casa errado, ignora o que a pessoa disse, soa robótico). O experimento do PR #819
mostrou que um modelo REDIGINDO sob regras rígidas, com checagem antes de enviar, resolve muito mais. O modelo
escolhido (Claude Haiku 5.5) só existe neste ambiente pelo **perfil GLOBAL** do Bedrock, que **pode processar
fora do Brasil** — o oposto da zona PHI (ADR-0006/0017: GLM-5 regional em sa-east-1, atestação de região).

## Decisão

1. **Rota de residência PRÓPRIA e rotulada**: `experimento_global_autorizado_dpo_2026-10-17`
   (`src/maezo/runtime/inference/experimento_global.py`). Não é uma zona do roteador e o roteador de zonas não a
   conhece; ela só é construída pela raiz de composição do receptor com a chave ligada e só admite UM modelo
   (`global.anthropic.claude-haiku-5-5`, sem `temperature`, `output_config.effort=low`). Todo log dela leva o
   rótulo da rota.
2. **Escopo**: SÓ a REDAÇÃO de respostas a perguntas informativas de saúde (o que é, sintomas, tratamento em
   geral, "pra que serve", exame, resultado de exame da pessoa, especialidade, procedimento) e da orientação de
   uma queixa leve SEM sinal de alarme (resposta "não" ou contexto às perguntas de alerta). Classificação,
   alarme/P1 (192), saúde mental com risco (CVV 188), identificação, plano, carteirinha, cobrança, cancelamento
   e casos **não mudam** e continuam no GLM-5 em sa-east-1: o grafo decide ANTES e só então pede a redação.
3. **Allowlist por conversa** (`MAEZO_HELENA_SAUDE_GERATIVA_CONVERSAS`): a conversa do dono e as conversas
   sintéticas do simulador (telefones `5511900000790..799`, roteiro `helena_saude_gerativa`). Fora dela, a
   Helena de antes, byte a byte (provado em teste). A Tania NÃO autorizou: fica fora.
4. **Minimização**: vai ao modelo só a mensagem e até 2 mensagens anteriores DA PESSOA (nunca as falas da Helena,
   que podem ter dado do plano), com CPF/CNPJ, telefone, e-mail, números longos (carteirinha), datas, data de
   nascimento e nome dito mascarados (`saude_gerativa.minimizar`, sobre o redator compartilhado
   `redact_free_text`). Nada do cadastro/lago.
5. **Checagem antes de enviar** (`saude_gerativa.verificar`): as cercas de saída da Helena, os termos proibidos da
   base e regras próprias (dose/quantidade, mandar usar/não usar/parar/continuar/dobrar, dose esquecida,
   liberar combinação, minimizar, diagnóstico, promessa, gênero suposto, pediatra para adulto, 192 nos modos de
   sintoma, ~100 palavras, markdown, link/fonte não fornecidos — fonte conferida por PREFIXO). Reprovou → 1
   reescrita → senão a resposta segura padrão. Falha/prazo do modelo → resposta segura padrão (nunca silêncio).
6. **IAM mínimo, rotulado experimental** (`deploy/aws-ecs/envs/dev-sa-east-1/experimento-saude-gerativa.tf`):
   `bedrock:InvokeModel` no perfil global do Haiku 5.5 e no modelo de base, nos quatro papéis que executam a
   Helena; `task_bedrock` (zona geral + PHI regional) intacto.

## Consequências

- Texto de beneficiário (minimizado) da conversa do dono pode ser processado fora do Brasil — **decisão
  consciente do DPO para este teste**; registrada aqui e no DL-0119.
- Resíduo de minimização: nome solto no meio da frase ("a Maria do RH...") e dado clínico identificador por si
  só não são detectáveis por padrão; aceitável na allowlist do dono, NÃO para outras pessoas.
- Desligar: `helena_saude_gerativa = false` (tfvars do dev) + apply direcionado — a chave desligada nem
  constrói o cliente. Desfazer: revert dos PRs (código + IAM) + apply direcionado da remoção das 4 políticas.
- Produção: exigiria nova decisão (DPO + jurídico + médico), de preferência com modelo regional.
