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
# FROM por TAG virou TAG+DIGEST em 19/09/2026 (pacote trivy/IaC D2). Prova local, feita
# na mesma data com `docker buildx imagetools inspect`: `cibseven/cibseven:2.1.0` e
# `cibseven/cibseven:tomcat-2.1.0` resolvem para O MESMO index digest
# sha256:20a2135bbc634924838b6a067ac27172bb95d68ddec035e01b40d2b9e95a1169 (resolvido
# 2026-09-19T14:08:26Z) — ou seja, este fold nao troca um byte do artefato; so' torna o
# FROM imutavel como os tres Dockerfiles irmaos ja' eram.
FROM cibseven/cibseven:2.1.0@sha256:20a2135bbc634924838b6a067ac27172bb95d68ddec035e01b40d2b9e95a1169

# O comentario antigo dizia "nao sei o uid da imagem base" e por isso nao declarava USER.
# O uid agora E' SABIDO — provado em 19/09/2026 na propria base (Alpine 3.22.2, index
# sha256:20a2135b…) com `docker image inspect` + `docker run`:
#
#   .config.User      = "camunda"      (a base JA' roda como camunda por default)
#   .config.WorkingDir = "/camunda"    (entrypoint /sbin/tini -- ./cibseven.sh)
#   getent passwd camunda = camunda:x:1000:1000::/camunda:/bin/bash
#   /camunda = camunda:camunda 2755 (com escrita)  /tmp = 1777 (sticky)
#   NAO existe /usr/local/tomcat nesta base — o layout "tomcat" do nome do tag nao se
#   aplica; os caminhos runtime reais sao /camunda e /tmp.
#
# O USER abaixo entao NAO muda quem executa o container (ja' era camunda) — ele torna o
# fato explicito no Dockerfile (trivy DS-0002 avaliava a AUSENCIA da diretiva como root)
# e a ASSERCAO do fim do arquivo falha o build se a base vier a mudar sob nos.
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
# O contexto do build e' a RAIZ do repo (`docker build -f deploy/cibseven/Dockerfile .`, tanto no
# CodeBuild quanto local) — o caminho do COPY e' relativo a ela, nao a este arquivo. Medido em
# 09/09/2026: `COPY configure-group-whitelist.sh` falhou com "not found" na AWS e local. O CI nao
# constroi esta imagem, entao um caminho errado aqui so' aparece no build de verdade.
#
# Em /camunda e nao em /tmp: o /tmp da base tem sticky bit e o build roda como o USER da base
# (nao-root, de proposito — ver acima), entao um arquivo copiado como root em /tmp nao pode ser
# removido por ele (`rm: Operation not permitted`, medido em 09/09/2026). Em /camunda o mesmo
# usuario ja' remove o showcase.
COPY deploy/cibseven/configure-group-whitelist.sh /camunda/configure-group-whitelist.sh
RUN sh /camunda/configure-group-whitelist.sh /camunda/conf/bpm-platform.xml \
    && rm /camunda/configure-group-whitelist.sh \
    && echo "OK: groupResourceWhitelistPattern presente uma vez"

# ---------------------------------------------------------------------------
# DS-0002 (trivy "Least Privilege User") — ultimo USER explicito nao-root, com
# ASSERCAO fail-closed no build. Os RUNs acima ja' rodavam como o usuario default
# da base (camunda, provado acima); o USER abaixo so' o declara — e, a partir dele,
# a assersao roda COMO camunda, entao o `test -w` e' prova real de escrita do
# usuario de runtime, nao de root. Se a base mudar (usuario ausente, uid diferente,
# caminho runtime fora do controle do camunda), o build FALHA em vez de publicar
# uma imagem root — a mesma disciplina dos blocos "OK:" acima.
# ---------------------------------------------------------------------------
USER camunda
RUN set -eu; \
    die() { echo "FATAL (DS-0002): $*" >&2; exit 1; }; \
    getent passwd camunda >/dev/null || die "usuario 'camunda' ausente na base (getent passwd falhou)"; \
    [ "$(id -u)" = "1000" ] || die "uid efetivo $(id -u) != 1000 — USER camunda nao resolveu para o uid provado da base"; \
    [ "$(id -g)" = "1000" ] || die "gid efetivo $(id -g) != 1000"; \
    for d in /camunda /tmp; do \
        [ -d "$d" ] || die "caminho runtime ausente na base: $d"; \
        [ -w "$d" ] || die "camunda(1000:1000) sem escrita em $d — o entrypoint ./cibseven.sh vai quebrar"; \
    done; \
    echo "OK (DS-0002): USER camunda 1000:1000 ativo; camunda escreve em /camunda e /tmp"
