# Bateria do Lucas (teste isolado, sem WhatsApp)

O Lucas nao tem canal de entrada (gap 11.7) e nenhum codigo de producao executa o grafo dele. A
bateria e' o chamador que falta, **so' para teste**: ela monta o grafo com os mesmos seams gated dos
outros agentes e roda 24 casos de cobranca (`src/maezo/agents/lucas/bateria_casos.py`).

Isto **nao** decide como o beneficiario vai chegar ao Lucas. Essa decisao continua aberta.

## O que ela faz no ambiente
- Chama o modelo (Bedrock), avalia as 2 DMN do Lucas e, nos casos que escalam, **abre
  SP-OP-ESCALATION-001** (isso cria tarefa na fila humana do dev).
- Cada caso usa uma conversa nova, `wa:amh:lucas-bat-<execucao>-<id>`; a chave de negocio
  `ESC-amh-<conversa>` aparece na saida (`business_key`) para quem for encerrar os casos.
- Os destinos ficam na faixa `5511900000xxx`: `mcp_whatsapp.send_message` suprime o envio nessa
  faixa em qualquer ambiente. Nada chega a Meta.

## Pre-requisitos
1. Imagem dos agentes com este modulo (`agents_image_digest` promovido).
2. Nenhum Terraform novo: a bateria roda na definicao de tarefa que JA existe do `agent-helena`
   (mesma imagem, papel de tarefa, segredos e provedor de IA), so' com o `command` trocado. O plano
   `docs/plans/lucas-numero-unico.md` §6g proibe `agent-lucas` em `local.agentes` (daemon sem turno).
   O grafo do Lucas e' montado por `build_agent_seams(agent_id="lucas")`, independente do `AGENT_ID`
   do container.
3. Para o Lucas **responder** (e nao cair em `falha_tecnica` em todo turno): o mesmo provedor da
   Helena — `helena_zona_phi=true` + `phi_vendor_dpa_ref` (decisao do DPO, nao do agente).

## Rodar
```
aws ecs run-task --cluster maezo-operadora-dev --launch-type FARGATE \
  --task-definition maezo-operadora-dev-agent-helena \
  --network-configuration "awsvpcConfiguration={subnets=[<subnets privadas>],securityGroups=[<sg das tasks>],assignPublicIp=DISABLED}" \
  --overrides '{"containerOverrides":[{"name":"agent-helena","command":["sh","-c","set -eu\nexport DATABASE_URL=$(python -c \"import os,urllib.parse as u; q=lambda k: u.quote(os.environ[k],safe=\u0027\u0027); print(os.environ[\u0027DSN_SCHEME\u0027]+\u0027://\u0027+q(\u0027DB_USER\u0027)+\u0027:\u0027+q(\u0027DB_PASSWORD\u0027)+\u0027@\u0027+os.environ[\u0027DB_HOST\u0027]+\u0027:\u0027+os.environ[\u0027DB_PORT\u0027]+\u0027/\u0027+os.environ[\u0027DB_NAME\u0027])\")\nexec python -m maezo.agents.lucas.bateria"]}]}'
```
`--ids L01,L11` roda so' alguns casos; `--listar` lista sem tocar em nada. A saida sai no grupo de
logs `/ecs/<name>/agent-helena`, uma linha `CASE {...}` por caso.

## Depois
Encerrar as tarefas humanas abertas pelos casos que escalam (chaves `ESC-amh-wa:amh:lucas-bat-*`).

## Achados (bateria de 01/10/2026)
- **Fechado (DL-0055):** L07, L09 e L18 — o catch-all de ambiguidade era rotulado `inadimplencia_detectada`
  e o texto ao beneficiario dizia "inadimplencia detectada" sem indicio de atraso. A DMN agora devolve
  `categoria`. **Depende de reimplantar a tabela no motor**: com a tabela antiga o grafo rotula
  `ambiguidade` tambem nos casos de atraso (falha para o lado de nao acusar).
- **Fechado (DL-0054):** com `ciclos_sem_conciliacao` ilegivel o grafo levantava excecao e o turno morria
  sem escalar (L24).
- **Aberto (L19):** sem `tenant_id` o motor recusa abrir o caso (isolamento de tenant) e o Lucas nao
  envia nada ao beneficiario.
