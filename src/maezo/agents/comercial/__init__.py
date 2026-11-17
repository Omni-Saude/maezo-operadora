"""Agente `comercial` (E-SF-17 / GTM-E-12): conduz a conversa com o prospect e encaminha ao humano.

Desligado por padrao e fora de toda raiz de composicao: nenhum receptor, gateway ou despachante o
constroi hoje. `build(config)` recusa montar o grafo sem `comercial_ligado=True` explicito, e a
ativacao fora de dev depende dos portoes GTM-01.R1, R2, R3 e OWNER-DEPLOY (ver
`spec/agents/comercial/agent.yaml`). A persona nao tem nome: nomea-la e' ato do dono
(`GAP-PERSONA-NAME`).
"""
