# Sourced by /camunda/bin/catalina.sh (T1.2; ADR-0060 D2). POSIX sh, no bashisms.
#
# 1. The datasource of conf/server.xml pins the schema path and TLS; only host, port,
#    database and credential come from the environment. cibseven.sh would OVERWRITE that url
#    with DB_URL (or with its H2 default) unless SKIP_DB_CONFIG is set, so anything other than
#    the image default refuses to start instead of booting against the wrong path.
# 2. Tomcat resolves ${DB_*} in server.xml from the environment (EnvironmentPropertySource):
#    the password never reaches a file or the process arguments.
# 3. EXIT_ON_INIT_FAILURE: a connector that cannot initialise (missing/unreadable mTLS
#    material, bad key) stops the JVM. Tomcat's default would keep serving 8080 without 8443.
if [ "${SKIP_DB_CONFIG:-}" != "true" ]; then
  echo "FATAL (T1.2): SKIP_DB_CONFIG must stay 'true'; the pinned datasource in conf/server.xml would be rewritten from DB_URL" >&2
  exit 1
fi
# DB_USERNAME/DB_PASSWORD are not checked here: cibseven.sh exports 'sa' for both when they are
# empty, so an absent credential reaches Tomcat as 'sa' and is refused by PostgreSQL (the engine
# does not start). The location of the database is checked, because nothing else defaults it.
for maezo_required in DB_HOST DB_PORT DB_NAME; do
  eval "maezo_value=\${$maezo_required:-}"
  if [ -z "$maezo_value" ]; then
    echo "FATAL (T1.2): $maezo_required is required by the pinned datasource" >&2
    exit 1
  fi
done
unset maezo_required maezo_value
CATALINA_OPTS="${CATALINA_OPTS:-} -Dorg.apache.tomcat.util.digester.PROPERTY_SOURCE=org.apache.tomcat.util.digester.EnvironmentPropertySource -Dorg.apache.catalina.startup.EXIT_ON_INIT_FAILURE=true"
export CATALINA_OPTS
