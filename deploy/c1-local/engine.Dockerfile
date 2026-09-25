# C1 (checkpoint de integracao, plano portal-autoridade-nativa-dev §3, Onda 1): imagem LOCAL de teste.
# Parte da imagem de `deploy/cibseven/Dockerfile.human` (INSTALL_STAFF_COMPOSITION=true e
# INSTALL_PORTAL_READ=true, construida por run.sh com a tag de C1_HUMAN_BASE) e acrescenta SO uma coisa:
#  o caminho pinado do sslrootcert (`/camunda/conf/rds-sa-east-1-bundle.pem`) vira link para a CA
#  local do PostgreSQL descartavel. E a mesma troca da prova local da T1.2 (native-deploy/README.md).
# O JAR do provedor Q2 ja vem da base: Dockerfile.human o instala com INSTALL_PORTAL_READ=true (B1).
# Nunca publicar esta imagem: a CA do banco e de teste.
ARG C1_HUMAN_BASE=maezo-c1-human:base
FROM ${C1_HUMAN_BASE}
USER root
RUN ln -sf /run/maezo/c1/pg-ca.pem /camunda/conf/rds-sa-east-1-bundle.pem
USER camunda
