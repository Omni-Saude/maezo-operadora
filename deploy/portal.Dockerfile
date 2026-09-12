# ADR-0049 staff bootstrap. Build only from an independently reviewed application
# repository/digest with the concrete production modules and identity DB CA trust.
# No material, credentials, trust anchors or dependency downloads are added here.
ARG APP_REPOSITORY
ARG APP_DIGEST
FROM ${APP_REPOSITORY}@${APP_DIGEST}

USER 0:0
RUN mkdir -p /run/maezo-staff-materials /run/maezo-staff-scratch \
    && chown 1000:1000 /run/maezo-staff-materials /run/maezo-staff-scratch \
    && chmod 0700 /run/maezo-staff-materials /run/maezo-staff-scratch
USER 1000:1000
# Paths must equal task mountPoints for ECS ownership initialization. Runtime also
# verifies owner/mode and refuses incompatible mounts; source is not mount proof.
VOLUME ["/run/maezo-staff-materials", "/run/maezo-staff-scratch"]
CMD ["python", "-m", "maezo.portal.api"]
