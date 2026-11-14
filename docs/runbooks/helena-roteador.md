# Runbook — roteador de habilidades da Helena (ADR-0066, DL-0134/DL-0135)

Só dev. Chave `MAEZO_HELENA_ROTEADOR` (`helena_roteador` no tfvars do dev): `desligado` (padrão, a Helena de antes),
`sombra` (F1) ou `ativo` (F2, ainda vale como `sombra`). Contrato: `spec/agents/helena/roteador.yaml`.

## 1. F1 — modo sombra

- O roteador (GLM-5 regional) e as travas de entrada rodam em paralelo ao `classify`. A resposta, o estado, o caso e o
  envio são os da Helena de antes. A decisão vai ao log quando o turno termina:
  `helena_roteador_sombra habilidade=... confianca=... alternativa=... responde_a_pendencia=... trava=... atual=...
  concorda=True|False pendencia=... falha=... ms=... conversa=<rótulo de 10 caracteres>`.
- `atual` é a decisão da Helena de hoje no vocabulário das habilidades (pode ter duas, quando a saída de hoje serve a
  ambas). `trava` = a trava fixa que decidiria pelo texto (alarme, risco, dados pessoais); quando há trava, ela é a
  escolha comparada.
- `falha=prazo|modelo|json_ilegivel` = o roteador não decidiu (nada muda para a pessoa). `ms` = tempo da chamada.
- Nenhum texto da pessoa nem do modelo vai ao log; `conversa` é um hash curto (agrupa os turnos), nunca o id.
- `helena_roteador_contrato_invalido` na subida = o contrato não carregou e a chave ficou desligada.

## 2. Comparação no simulador

`C:/vc/sombra_compara.py <roteiro>=<log.json> ...` (logs das tarefas do simulador exportados do CloudWatch) dá a
concordância por roteiro, as falhas, o tempo e cada desacordo com o texto do passo do roteiro (texto sintético).

## 2b. F2 — modo ativo (só nas conversas do simulador)

- `helena_roteador = "ativo"` + `helena_roteador_conversas` (só os telefones 790..799 do simulador). Fora da
  lista: sombra. A lista de teste das pessoas só entra na F4, com o OK do dono.
- `helena_roteador_decidiu habilidade=... confianca=... pendencia_mantida=True|False ms=...` = o roteador
  decidiu; `helena_roteador_caminho_de_sempre motivo=trava|prazo|modelo|json_ilegivel` = trava ou roteador sem
  decisão (o caminho de hoje, com o estado inteiro); `helena_roteador_duvida x=... y=...` = a pergunta "X ou
  Y?" (pendência `ROT_DUVIDA:x:y`).
- A pendência só vale quando o roteador diz que a mensagem responde a ela; senão é descartada antes da
  habilidade.
- `roteador-v5` (22/10/2026): a linha de `saude` no contrato diz que "quais exames ou cuidados são indicados
  para a idade ou condição" é saúde mesmo citando o plano; a de `cobertura` diz "se o plano cobre (paga,
  autoriza)". Regra geral de fronteira entre as duas habilidades, sem termo do roteiro.
- Latência (22/10/2026): sem trava de entrada e com pergunta de cobertura pelo detector que já existe
  (`_pergunta_de_cobertura_ampla`), a EXTRAÇÃO da cobertura começa junto com o roteador (antes esperava por
  ele). O roteador continua decidindo: outra habilidade descarta a extração; a cobertura só usa a extração do
  mesmo texto (`helena_cobertura_extracao_antecipada` no log quando usada). Nenhuma chamada nova quando o
  roteador escolhe cobertura; no máximo uma extração descartada quando escolhe outra.
- Medição por turno de cobertura (um procedimento × lista, etapas em sequência): `C:/vc/latencia_cobertura.py`
  sobre o log exportado da tarefa do simulador.

## 2b-2. F2 fechada (r4 de 22/10/2026): as regras de palavra não competem com o roteador

- Com o roteador ATIVO decidindo, a habilidade limita as intenções do turno (`_INTENTS_DA_HABILIDADE`):
  `plano` → consulta do plano (subtipo do modelo ou da própria habilidade); `cobranca` → Lucas; `sintoma` →
  sintoma; `saude` → informação; `conversa` → saudação/informação; `equipe` → informação ou pedido de pessoa
  (só os portões de pedido explícito abrem caso: trava `caso_so_com_sim`). Log
  `helena_habilidade_fecha_intencao habilidade=... intent_do_modelo=...` quando o modelo vinha de fora.
- As regras de PALAVRA do `classify` que levariam o turno para OUTRA habilidade (cobrança negada, identidade,
  despedida, pergunta financeira, dependentes, pergunta do plano → consulta, conversa sem relato, pedido de
  pessoa léxico/explícito) só valem sem roteador decidindo ou dentro da habilidade dela (`_regra_vale`).
- Ficam SEMPRE: as travas de segurança (risco, alarme determinístico e da base, neonatal, caso aberto com sinal
  de emergência, ameaça regulatória), as respostas às pendências e as regras internas de cada habilidade.

## 2c. Lista da F5 — condicionais de transição no `graph.py` (remover ou migrar para o contrato)

São pontes da F2 entre a decisão do roteador e o código de hoje; na F5 saem do `graph.py` (a decisão passa a
ser do contrato `spec/agents/helena/roteador.yaml` e de cada habilidade fechada):

1. Reclamação sobre a resposta anterior e resposta curta à pendência ("não", "deixa pra lá", `ler_resposta`), sem
   pedido de pessoa, ficam com a dona da pendência (`e_reclamacao` + `dona_da_pendencia`, #897 e #901); o
   roteador dizendo "responde à pendência" também vai para a dona → regra de pendência do contrato.
2. `habilidade_roteador` no estado barrando a cobertura assistida e o caminho do Rol no `consultar_plano`
   quando o roteador escolheu outra habilidade (#897, #900) → a habilidade `plano` fechada, sem passar pelas
   regras de cobertura.
3. `_HABILIDADE_DO_TURNO` valendo sobre as regras lexicais no `classify` (#877; generalizado na F2 fechada:
   `_regra_vale` / `_INTENTS_DA_HABILIDADE`) → some com o `classify` antigo.
4. Extração da cobertura antecipada pelo detector lexical `_pergunta_de_cobertura_ampla` (latência, 22/10) →
   a habilidade `cobertura` recebe a extração do próprio roteador ou dispara em paralelo sem detector.
5. Discordância sem procedimento tratada antes do desvio para autorização (#897) → regra da habilidade
   `cobertura`.

## 2d. Cobertura em modo simples (DL-0140, decisão do dono de 22/10/2026)

- `MAEZO_HELENA_COBERTURA_MODO=simples` (padrão; dev explícito): a cobertura nunca consulta catálogo/AMH por
  procedimento. Procedimento ou lista = texto geral + "A equipe confirma para você. Quer que eu encaminhe?"
  (caso só com "sim"). Pergunta geral = o texto geral do plano. Os textos de regra própria continuam.
- Log: `helena_cobertura_simples resultado=oferta_da_equipe|geral itens=N`. Nenhum `helena_cobertura_lida`.
- Voltar para a consulta automática: `helena_cobertura_modo = "consulta"` no tfvars do dev + apply direcionado.

## 3. Desligar / desfazer

`helena_roteador = "sombra"` (volta à F1) ou `"desligado"` no `amh-interop.auto.tfvars` do dev + apply direcionado do receptor e do simulador.
Desfazer o código: revert do PR (sem a chave, o grafo é o de antes nó a nó).
