# TestOnly C2 overlay. ROOT supplies the immutable measured, already sealed base,
# final SAME candidate JARs and complete13descriptor layout from assembly staging.
# No compile/SQL/bootstrap/authority is performed in this build.
ARG SECURED_BASE_IMAGE
FROM ${SECURED_BASE_IMAGE}
ARG SECURED_BASE_IMAGE
ARG NATIVE_PHASE
USER root
COPY maezo-human-command.jar /camunda/lib/maezo-human-command.jar
COPY maezo-rest-spi.jar /camunda/webapps/engine-rest/WEB-INF/lib/maezo-rest-spi.jar
COPY provider-auth-test-support.jar /camunda/lib/provider-auth-test-support.jar
COPY --chown=root:root secured-layout/ /camunda/
RUN printf '%s\n' "$SECURED_BASE_IMAGE" | grep -Eq '^.+@sha256:[a-f0-9]{64}$' \
    && test -f /camunda/lib/maezo-human-command.jar \
    && test -f /camunda/lib/provider-auth-test-support.jar \
    && test -f /camunda/webapps/engine-rest/WEB-INF/lib/maezo-rest-spi.jar \
    && chown root:root /camunda/conf/bpm-platform.xml /camunda/conf/server.xml \
    && chmod 0444 /camunda/conf/bpm-platform.xml /camunda/conf/server.xml /camunda/conf/context.xml /camunda/conf/web.xml /camunda/webapps/*/WEB-INF/web.xml \
    && case "$NATIVE_PHASE" in \
      PHASE_A) ! grep -q 'br.com.maezo.human.ProviderAuthTestComposition' /camunda/conf/bpm-platform.xml ;; \
      PHASE_B) grep -q 'br.com.maezo.human.ProviderAuthTestComposition' /camunda/conf/bpm-platform.xml ;; \
      *) exit 1 ;; \
    esac \
    && ! grep -q 'br.com.maezo.human.StaffDeploymentComposition' /camunda/conf/bpm-platform.xml \
    && ! grep -q 'br.com.maezo.human.PortalReadPlugin' /camunda/conf/bpm-platform.xml \
    && grep -q 'certificateVerification="required"' /camunda/conf/server.xml \
    && grep -q 'sslmode=verify-full' /camunda/conf/server.xml \
    && grep -q 'br.com.maezo.workload.SecuredBpmPlatformBootstrap' /camunda/conf/server.xml \
    && grep -q 'br.com.maezo.workload.SecuredDataSourceFactory' /camunda/conf/server.xml
# Inherit validated EnvironmentPropertySource + SKIP_DB_CONFIG from measured base.
# Dedicated observation PKI and PostgreSQL CA are mounted privately by ROOT.
USER camunda
