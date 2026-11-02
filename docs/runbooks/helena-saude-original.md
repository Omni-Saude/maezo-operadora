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
| Dúvida geral de saúde ("o que é PSA?") | Caso `intencao_clinica` moderado para a equipe (a base de conhecimento saiu em 02/11/2026: os textos nunca tiveram revisão médica) |
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
`tabela_alarme` com conduta e severidade, `tabela_sem_alarme`, `risco_pelo_texto`, `leitura_falhou`,
`tabela_fora`), `helena_saude_original_leitura_falhou` (falha).

## Etapa 2 (depois do teste do dono)
Remover do código as camadas antigas de saúde que a porta única deixou sem uso nas conversas dela (protocolos,
perguntas de alerta v4, travas de alarme por palavra, saúde gerativa) e decidir o caminho das conversas fora da
allowlist.

## Adendo 02/11/2026 — todas as conversas pela porta única (DL-0165, limpeza da saúde, etapa 2)
Decisão do dono (02/11/2026), com a autorização dele como DPO (27/10/2026) para o Haiku global em todos os números
do dev. O texto acima continua valendo, com estas mudanças:

- **Interruptor** `MAEZO_HELENA_ROTA_GLOBAL_TODAS` (terraform `helena_rota_global_todas`; `true` só no dev,
  `amh-interop.auto.tfvars`): a rota global do Haiku, o roteador ativo e a porta única valem para **toda** conversa
  keyed (`wa:{tenant}:hk1_...`), sem lista. Chega também ao `agent-helena` remoto e ao simulador (mesmo
  `environment_app`). Desligado = só as conversas das listas, como antes.
- **A saúde só é decidida pela porta.** O caminho de sempre (`classify`), nessas conversas, nunca decide saúde: com
  dado pessoal na mensagem, com o roteador sem decisão ou numa habilidade fora da saúde, quando o classificador vê
  saúde a porta lê (uma leitura por turno) e a tabela decide. Leitura "não é saúde" contra o classificador que afirma
  saúde, sem decisão do roteador: na dúvida, caso `intencao_clinica` moderado para a equipe (sem 192).
- **Sem lista de palavras de alarme na porta.** A mensagem chega à porta pela habilidade de saúde do roteador, pelo
  sinal de alarme dito pelo modelo ou pela própria leitura (a fala de `conversa` é lida antes do texto fixo).
  Leitura que falha = caso para a equipe (não há mais 192 por palavra sem leitura). Fica a lista de risco do DL-0151
  (CVV 188 e 192).

| Frase do dono | O que fazer |
|---|---|
| "Volta a valer só para a lista" | `helena_rota_global_todas = false` em `amh-interop.auto.tfvars` + apply direcionado da task do receptor e do `agent-helena`. |
| "Todas as conversas de novo" | `helena_rota_global_todas = true` + o mesmo apply. |

Log novo: `helena_saude_original` com `via=classify_sem_saude` (a leitura disse que não é saúde) e
`via=classify_diverge_da_leitura` (na dúvida, uma pessoa); `helena_rota_global_ligada` ganha `todas_as_conversas`.

Etapa 3 (a seguir): apagar do código as camadas que ficaram sem uso (base, saúde gerativa, v4, triagem v2,
protocolos, alarme por palavra, acolhimento por palavra).
