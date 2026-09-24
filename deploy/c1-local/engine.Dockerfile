# C1 (checkpoint de integracao, plano portal-autoridade-nativa-dev §3, Onda 1): imagem LOCAL de teste.
# Parte da imagem de `deploy/cibseven/Dockerfile.human` (INSTALL_STAFF_COMPOSITION=true e
# INSTALL_PORTAL_READ=true, construida por run.sh com a tag de C1_HUMAN_BASE) e acrescenta SO duas coisas:
#  1. o JAR do provedor Q2 (T1.7a/b) em /camunda/lib. O COPY no Dockerfile.human ainda nao existe
#     (read-provider/README.md: "fica para depois da T1.2"); pendencia registrada no README do C1;
#  2. o caminho pinado do sslrootcert (`/camunda/conf/rds-sa-east-1-bundle.pem`) vira link para a CA
#     local do PostgreSQL descartavel. E a mesma troca da prova local da T1.2 (native-deploy/README.md).
# Nunca publicar esta imagem: a CA do banco e de teste.
ARG C1_HUMAN_BASE=maezo-c1-human:base
FROM maven:3.9.9-eclipse-temurin-17@sha256:f58d59b6273e785ac0a4477f6e9b5ba1d7731c75b906c0f7b34076f1851318cc AS provider
WORKDIR /build
COPY src/maezo/portal/engine/java/pom.xml java/pom.xml
COPY src/maezo/portal/engine/java/src java/src
COPY src/maezo/portal/engine/read-provider/pom.xml read-provider/pom.xml
COPY src/maezo/portal/engine/read-provider/src/main read-provider/src/main
RUN mvn -B -q -f java/pom.xml -DskipTests install \
    && mvn -B -q -f read-provider/pom.xml -DskipTests package

FROM ${C1_HUMAN_BASE}
COPY --from=provider /build/read-provider/target/maezo-portal-read-provider-1.0.0.jar /camunda/lib/maezo-portal-read-provider.jar
USER root
RUN ln -sf /run/maezo/c1/pg-ca.pem /camunda/conf/rds-sa-east-1-bundle.pem
USER camunda
