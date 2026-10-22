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

## 3. Desligar / desfazer

`helena_roteador = "sombra"` (volta à F1) ou `"desligado"` no `amh-interop.auto.tfvars` do dev + apply direcionado do receptor e do simulador.
Desfazer o código: revert do PR (sem a chave, o grafo é o de antes nó a nó).
