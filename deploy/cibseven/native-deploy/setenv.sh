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
# does not start). They land in their own attributes, never in the url.
#
# DB_HOST/DB_PORT/DB_NAME are interpolated INTO the JDBC url, and pgjdbc keeps the LAST value of a
# repeated parameter: DB_NAME='x?currentSchema=public&y=' would override the pinned path, and
# '...&sslfactory=org.postgresql.ssl.NonValidatingFactory' would void verify-full. So each one is
# checked against an explicit allowlist (no ranges: they depend on the locale) and anything else,
# including an empty value or a newline, refuses to start.
maezo_lower=abcdefghijklmnopqrstuvwxyz
maezo_upper=ABCDEFGHIJKLMNOPQRSTUVWXYZ
maezo_digit=0123456789
maezo_refuse() {
  echo "FATAL (T1.2): $1 is missing or has characters outside $2 (it is interpolated into the pinned JDBC url)" >&2
  exit 1
}
case "${DB_HOST:-}" in
  '' | *[!"$maezo_lower$maezo_digit".-]*) maezo_refuse DB_HOST '[a-z0-9.-]' ;;
esac
case "${DB_PORT:-}" in
  '' | *[!"$maezo_digit"]* | ??????*) maezo_refuse DB_PORT '[0-9]{1,5}' ;;
esac
case "${DB_NAME:-}" in
  '' | *[!"$maezo_lower$maezo_upper$maezo_digit"_]*) maezo_refuse DB_NAME '[A-Za-z0-9_]' ;;
esac
unset maezo_lower maezo_upper maezo_digit
unset -f maezo_refuse
CATALINA_OPTS="${CATALINA_OPTS:-} -Dorg.apache.tomcat.util.digester.PROPERTY_SOURCE=org.apache.tomcat.util.digester.EnvironmentPropertySource -Dorg.apache.catalina.startup.EXIT_ON_INIT_FAILURE=true"
export CATALINA_OPTS
