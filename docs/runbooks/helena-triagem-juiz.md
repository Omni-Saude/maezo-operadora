# Runbook — Helena: triagem com juiz (experimento do dev, DL-0159)

## O que é
Na queixa de sintoma, nas conversas da allowlist do modelo (dono, Tania, time-1, time-2, simulador 790–799), a
Helena conduz a triagem pela habilidade `src/maezo/agents/helena/habilidades/triagem_juiz/`:

- a tabela `spec/agents/helena/triagem_juiz/tabela.yaml` é o **piso** (regras com fonte);
- o modelo (Haiku 5.5, rota global já autorizada — DL-0155) julga a conversa inteira e **só sobe** a gravidade;
- no máximo 3 perguntas, uma por turno; 192 só pela lista de 12 itens da tabela.

Chave: `triagem_juiz` no pacote da Helena (`spec/agents/helena/pacote/configuracao.yaml`), publicado pelo workflow
`agentes-pacote.yml` no merge na `main` e relido pelos serviços em até 60 s. Ausente ou `false` = a Helena de antes.
Fora da allowlist, nada muda mesmo ligada.

## Frases do dono
| Frase | O que fazer |
|---|---|
| "Volte para a tabela original" / "Desliga a triagem com juiz" | PR com `triagem_juiz: false` no `configuracao.yaml` + merge (o pacote é publicado sozinho). Sem deploy. O código fica. |
| "Desfaz a triagem com juiz de vez" | Desligar como acima + `git revert` do PR da DL-0159 + build/deploy da imagem. |
| "Religa a triagem com juiz" | `triagem_juiz: true` + merge. |

Ordem de implantação: a imagem com o código precisa estar no ar ANTES do pacote com `triagem_juiz` (chave desconhecida
faz o pacote ser recusado inteiro; o serviço fica no último pacote bom).

## Ajustes do teste do dono (01/11/2026)
No primeiro teste real, "minha mãe de 70 anos… chegou a desmaiar" (um desmaio da semana anterior) recebeu o 192 da
trava antiga por palavra, antes da triagem; depois a conversa misturou triagem, protocolo antigo e conversa geral.
- Com a chave ligada (e na allowlist), o alarme pela palavra (`_sinal_claro_pelo_texto`, a trava de alarme e o alarme
  "claro" do roteador) **abre a triagem** em vez de dar 192 direto; o motor tem os mesmos sinais claros (não respira,
  convulsão agora, não acorda → 192 sem modelo) e, para "desmaiou", pergunta "foi hoje?". A trava de risco
  psicossocial e a de dados pessoais seguem como antes.
- Sinais de emergência só contam como presentes se aconteceram hoje; o leitor entende "foi semana passada" como "não".
- Pronto-socorro e "atendimento hoje" dizem o porquê (uma frase do juiz, sem 192/SAMU nem link).
- Logo depois da conclusão: "o que eu te respondi?" / "por que o pronto-socorro?" recebe o porquê, sem nova triagem.
- 2º teste (01/11, tarde): juntar a mensagem seguinte ao caso concluído misturou o caso do irmão com o do marido.
  Agora a conclusão ENCERRA o caso: mensagem nova de sintoma = caso novo, só com ela; "outro assunto", "mudei de
  assunto", "outra coisa" encerram o caso (sem o caso novo na mensagem: "Claro, pode contar.").
- Falha do modelo: 192 se havia palavra de alarme; senão, a oferta da equipe (nunca a lista do protocolo antigo).
- "O que você está sentindo?" só quando a pessoa ainda não contou.

## Logs (sem conteúdo da conversa)
`helena_triagem_juiz_pergunta` (motivo, rodada), `helena_triagem_juiz_saida` (saida, regra, piso, juiz, perguntas,
com_razao), `helena_triagem_juiz_explicou`, `helena_triagem_juiz_troca_de_assunto`, `helena_triagem_juiz_falhou` (falha,
continua, palavra), `helena_triagem_juiz_contexto_invalido`.

## Limites conhecidos (teste de mesa, 01/11/2026)
- Conjunto com 40 alertas escondidos: 83–85% de destino certo e 1–2 emergências perdidas por rodada
  (violência doméstica e maus-tratos disfarçados; transtorno alimentar).
- Gabaritos sem revisão médica; respostas da pessoa simuladas.
- Um turno pode levar até ~4 chamadas curtas ao modelo (teto de 30 s por turno).
