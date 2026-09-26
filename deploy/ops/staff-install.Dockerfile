# Imagem de OPERACAO do plano staff (Onda 3: bloqueio B5; Ondas 4-7: tools/staff_ops).
#
# Por que uma imagem e nao `run-db-task.ps1`: o RunTask limita `containerOverrides` a 8192 bytes e
# `engine-native-install.sql` sozinho tem ~53 KB; a imagem do app nao traz `deploy/sql` nem `tools/`.
# Aqui entra SO o que as ferramentas de operacao leem, por cima da imagem do app pinada por DIGEST
# (mesmo venv, mesmas raizes RDS no trust store, mesmo usuario 1000). Nenhum segredo ou material.
#
# ENTRYPOINT `python -m` + CMD `tools.staff_install`: a task da Onda 3 (sem `command`) roda o mesmo
# instalador de antes; as tasks novas passam `command = ["tools.staff_ops", "<comando>"]`.
ARG APP_REPOSITORY
ARG APP_DIGEST
FROM ${APP_REPOSITORY}@${APP_DIGEST}

WORKDIR /app
COPY --chown=0:0 deploy/sql/ ./deploy/sql/
COPY --chown=0:0 tools/staff_materials/ ./tools/staff_materials/
COPY --chown=0:0 tools/staff_install/ ./tools/staff_install/
COPY --chown=0:0 tools/staff_ops/ ./tools/staff_ops/
COPY --chown=0:0 tools/dev_syn_fixture/ ./tools/dev_syn_fixture/
COPY --chown=0:0 src/maezo/portal/engine/java/src/main/resources/external-case-schema-postgres.sql ./src/maezo/portal/engine/java/src/main/resources/external-case-schema-postgres.sql

USER 1000:1000
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app
ENTRYPOINT ["python", "-m"]
CMD ["tools.staff_install"]
