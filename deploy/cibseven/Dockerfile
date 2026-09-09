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

# EXECUTOR DE JOBS: DESLIGAR O "DEPLOYMENT AWARE"
#
# O ACHADO. Medido em 09/09/2026 no engine em execucao (ECS Exec, engine-rest local):
#
#   GET /deployment/registered  -> []          (nenhum deployment registrado)
#   GET /deployment             -> 1           (maezo-spec-processes, 13/08)
#   GET /job?timers=true        -> 97, TODOS vencidos, todos com retries=3 (o valor inicial)
#   bpm-platform.xml            -> jobExecutorDeploymentAware = true
#
# O executor esta' LIGADO — o bloco `<job-executor>` existe. O que `deploymentAware=true`
# faz e' restringi-lo a jobs de deployments REGISTRADOS nesta instancia do engine. O
# registro e' em memoria: acontece quando o deployment entra (por REST, no nosso caso) e
# morre no reinicio do container. Depois do primeiro restart, nenhum job dos nossos 20
# processos volta a ser adquirido. Relogio criado com a data certa e nunca executado;
# `retries=3` porque um job que nunca foi TENTADO nao decrementa nem gera incidente.
#
# Consequencia medida: todo prazo regulatorio dos processos (alerta de risco de SLA,
# coordenacao assumindo, prazos ANS da autorizacao previa) ficou decorativo desde 22/08.
#
# POR QUE `false` E' O VALOR CERTO AQUI. A propriedade existe para VARIOS engines
# compartilhando UM banco, cada um executando so' o que ele proprio implantou. Nesta
# conta ha' um unico engine (servico `cibseven` do cluster `maezo-operadora-dev`) e o
# schema `cibseven` da Aurora de dev e' so' dele; staging e producao sao contas e bancos
# separados. Com `false`, o executor adquire job de todo deployment do banco — que e'
# exatamente o conjunto que este engine deve executar.
#
# ALTERNATIVA DESCARTADA: registrar os deployments no boot via Process Application. E' a
# solucao "canonica" da distribuicao Tomcat, mas exige um WAR proprio — a peca que esta
# imagem existe para NAO ter.
#
# O QUE ACONTECE NO PRIMEIRO BOOT COM ISTO: os relogios represados disparam de uma vez.
# E' fila drenando, nao dano — mas e' ruido concentrado em dev. Decisao de 09/09: drenar,
# nao apagar; apagar seria remover prazo regulatorio do historico.
RUN sed -i 's|<property name="jobExecutorDeploymentAware">true</property>|<property name="jobExecutorDeploymentAware">false</property>|' \
        /camunda/conf/bpm-platform.xml

# Mesma disciplina do showcase: se o `sed` nao casar (a base mudou o arquivo), o build
# FALHA em vez de publicar uma imagem igual a anterior com nome novo.
RUN grep -q '<property name="jobExecutorDeploymentAware">false</property>' /camunda/conf/bpm-platform.xml \
    && ! grep -q '<property name="jobExecutorDeploymentAware">true</property>' /camunda/conf/bpm-platform.xml \
    && echo "OK: jobExecutorDeploymentAware=false"

# Preserve the 27 static candidate groups and four ESCALATION identities (ADR-0049).
# Only groupResourceWhitelistPattern changes; user/tenant/general admission and
# authorization remain inherited. Actual engine admission still requires live proof.
# Build with deploy/cibseven as the context.
COPY configure-group-whitelist.sh /tmp/configure-group-whitelist.sh
RUN sh /tmp/configure-group-whitelist.sh /camunda/conf/bpm-platform.xml \
    && rm /tmp/configure-group-whitelist.sh
