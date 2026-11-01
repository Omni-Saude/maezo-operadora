# Runbook — Helena: saúde pelo desenho original (porta única, DL-0160)

## O que é
Nas conversas da allowlist do modelo (dono, Tania, time-1, time-2, simulador 790–799), toda mensagem de saúde passa
só pela habilidade `src/maezo/agents/helena/habilidades/saude_original/`, que segue a jornada original
`docs/processes/journeys/AGJ-HELENA-TRIAGE.md` (05/07/2026):

| Situação | O que a Helena faz |
|---|---|
| Risco de suicídio/autolesão no texto | O caminho de sempre: tabela de saúde mental, CVV 188 e 192, caso para a equipe |
| Tabela DMN dá P1 (emergência) | Texto de emergência (192) e caso **grave** para a equipe |
| Tabela DMN dá P2 (enfermagem) | Caso **moderado** e o aviso da equipe de saúde, sem "Ligue 192 agora" |
| Sintoma sem alarme, pergunta clínica ("pode ser covid?", "qual remédio?") | Caso `intencao_clinica` moderado para a equipe |
| Dúvida geral de saúde ("o que é PSA?") | Só o texto revisado da base de saúde; sem assunto na base, a equipe |
| Caso de saúde já aberto e sem emergência nova | Texto fixo: a pessoa conta ao profissional (o portal não mostra a conversa) |
| Leitura do modelo ou tabela fora do ar | Caso para a equipe (na dúvida, uma pessoa assume) |

O modelo (Haiku 5.5, rota global já autorizada — DL-0155) **só lê** a conversa e preenche os campos da tabela
(`leitura.py`): assunto, público, código do sintoma da lista da tabela daquele público, intensidade e idade. Ele não
conversa, não pergunta, não julga gravidade e não escreve para a pessoa. Quem decide é a tabela DMN
(`spec/processes/dmn/triage_redflag_*.dmn`, ADR-0012).

Fora da saúde, nessas conversas, `conversa` tem texto fixo ("Claro, pode contar." na troca de assunto).

**Um caso por conversa.** O processo de escalonamento é idempotente por conversa (mais um caso clínico paralelo para
alerta). Depois do primeiro caso de saúde, outra queixa sem emergência recebe o texto de caso aberto. Para testar
vários casos do zero, zerar a conversa entre eles.

## Chave
`saude_original` no pacote da Helena (`spec/agents/helena/pacote/configuracao.yaml`), publicado pelo workflow
`agentes-pacote.yml` no merge na `main` e relido pelos serviços em até 60 s. Ausente ou `false` = a Helena de antes.
Fora da allowlist, nada muda mesmo ligada. Ordem de implantação: a imagem com o código antes do pacote com a chave
(chave desconhecida faz o pacote ser recusado inteiro; o serviço fica no último pacote bom).

## Frases do dono
| Frase | O que fazer |
|---|---|
| "Desliga a saúde original" | PR com `saude_original: false` + merge (o pacote é publicado sozinho). Sem deploy. |
| "Religa a saúde original" | `saude_original: true` + merge. |

A triagem com juiz (DL-0159) foi **removida** (código, tabela e testes): não há mais chave para religá-la.

## Logs (sem conteúdo da conversa)
`helena_saude_original_leitura` (assunto, populacao, sintoma_codigo, intensidade), `helena_saude_original` (via:
`tabela_alarme` com conduta e severidade, `tabela_sem_alarme`, `base_revisada`, `risco_pelo_texto`, `leitura_falhou`,
`tabela_fora`), `helena_saude_original_leitura_falhou` (falha).

## Etapa 2 (depois do teste do dono)
Remover do código as camadas antigas de saúde que a porta única deixou sem uso nas conversas dela (protocolos,
perguntas de alerta v4, travas de alarme por palavra, saúde gerativa) e decidir o caminho das conversas fora da
allowlist.
