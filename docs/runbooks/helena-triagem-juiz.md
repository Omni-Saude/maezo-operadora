# Runbook — Helena: triagem com juiz (experimento do dev, DL-0161)

## O que é
Na queixa de sintoma, nas conversas da allowlist do modelo (dono, Tania, time-1, time-2, simulador 790–799), a
Helena conduz a triagem pela habilidade `src/maezo/agents/helena/habilidades/triagem_juiz/`:

- a tabela `spec/agents/helena/triagem_juiz/tabela.yaml` é o **piso** (regras com fonte);
- o modelo (Haiku 5.5, rota global já autorizada — DL-0155) julga a conversa inteira e **só sobe** a gravidade;
- no máximo 3 perguntas, uma por turno; 192 só pela lista de 12 itens da tabela.

Chave: `MAEZO_HELENA_TRIAGEM_JUIZ` (variável Terraform `helena_triagem_juiz`, em
`deploy/aws-ecs/envs/dev-sa-east-1/amh-interop.auto.tfvars`). Fora da allowlist ou com a chave desligada, nada muda.

## Frases do dono
| Frase | O que fazer |
|---|---|
| "Volte para a tabela original" / "Desliga a triagem com juiz" | `helena_triagem_juiz = false` (PR) + apply direcionado do `webhook_receiver` e do simulador (o agent-helena recebe a mesma env). O código fica. |
| "Desfaz a triagem com juiz de vez" | Desligar como acima + `git revert` do PR da DL-0161 + build/deploy. |
| "Religa a triagem com juiz" | `helena_triagem_juiz = true` + o mesmo apply. |

## Logs (sem conteúdo da conversa)
`helena_triagem_juiz_pergunta` (motivo, rodada), `helena_triagem_juiz_saida` (saida, regra, piso, juiz, perguntas),
`helena_triagem_juiz_falhou` (falha, continua), `helena_triagem_juiz_contexto_invalido`.

## Limites conhecidos (teste de mesa, 01/11/2026)
- Conjunto com 40 alertas escondidos: 83–85% de destino certo e 1–2 emergências perdidas por rodada
  (violência doméstica e maus-tratos disfarçados; transtorno alimentar).
- Gabaritos sem revisão médica; respostas da pessoa simuladas.
- Um turno pode levar até ~4 chamadas curtas ao modelo (teto de 30 s por turno).
