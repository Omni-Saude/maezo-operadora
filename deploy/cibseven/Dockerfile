# Engine BPMN sem o showcase de demonstracao.
#
# POR QUE UMA IMAGEM PROPRIA
#
# `cibseven/cibseven:2.1.0` embute a aplicacao de exemplo "invoice", e ela cria dados de
# demonstracao NO BOOT (`DemoDataGenerator.createUsers` no log da nossa task). Medido em
# 18/08/2026 no engine em execucao:
#
#   usuarios:       demo, john, mary, peter  (@camunda.org)
#   camunda-admin:  ['demo']
#   deployments:    2 do showcase + 1 nosso (maezo-spec-processes)
#   definicoes:     20 SP-OP nossas + `invoice` e `ReviewInvoice`
#
# Apagar o usuario pelo REST NAO resolve: o gerador roda a cada subida do container e o
# recria. A correcao duravel e' o webapp nao existir na imagem.
#
# O QUE ISTO NAO RESOLVE — dito para nao virar falsa seguranca: o `engine-rest` continua
# SEM AUTENTICACAO nesta configuracao (medido: `GET /user` sem credencial devolve 200).
# Quem alcanca a porta 8080 dirige o motor. A fronteira hoje e' o Cloudflare Access na
# borda e o Security Group na rede. Exigir credencial no engine-rest atinge worker,
# agentes e canal (todos chamam sem credencial) — trabalho proprio, no plano de acesso.
FROM cibseven/cibseven:2.1.0

# NAO troco de USER de proposito: nao sei o uid da imagem base e um `USER` errado
# quebraria o entrypoint dela. Se a remocao exigir privilegio, a ASSERCAO abaixo falha o
# build — que e' o resultado certo, em vez de publicar uma imagem identica a base.
RUN rm -rf /camunda/webapps/camunda-invoice \
           /camunda/webapps/camunda-invoice.war \
    && rm -f /camunda/lib/camunda-invoice*.jar 2>/dev/null || true

# A prova de que a imagem e' diferente da base. Sem isto, um `rm` que falha em silencio
# produziria uma imagem "limpa" que recria o usuario demo no primeiro boot.
RUN test ! -e /camunda/webapps/camunda-invoice \
    && test ! -e /camunda/webapps/camunda-invoice.war \
    && echo "OK: showcase invoice ausente da imagem"
