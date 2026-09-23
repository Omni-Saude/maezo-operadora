package br.com.maezo.human.readprovider;

import br.com.maezo.human.Jcs;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.nio.ByteBuffer;
import java.nio.charset.CharacterCodingException;
import java.nio.charset.CodingErrorAction;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.Path;
import java.nio.file.attribute.PosixFileAttributes;
import java.nio.file.attribute.PosixFilePermission;
import java.sql.Connection;
import java.sql.Driver;
import java.sql.DriverManager;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Statement;
import java.time.DateTimeException;
import java.time.Instant;
import java.time.OffsetDateTime;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Properties;
import java.util.Set;
import java.util.TreeMap;

/**
 * The live membership source (T1.7b): reads the COMMITTED row of
 * {@code <source_schema>.portal_memberships} through the observer login
 * ({@code portal_read_source_amh}) and rebuilds, in Java, the exact projection the Python publisher
 * signs ({@code read_publisher.py} {@code MembershipProjection}). Nothing the publisher declares is
 * trusted: the provider re-reads the source itself.
 *
 * <ul>
 *   <li>The DSN file is a secret (password): owned by the process user, mode exactly {@code 0400},
 *       no link, bounded. It is read on every observation, so a rotated password needs no restart.
 *   <li>TLS is {@code verify-full} against the pinned CA file; the JDBC driver must be pgjdbc
 *       itself, never whatever other driver answers the URL.
 *   <li>The session is read-only, REPEATABLE READ, with local statement/lock timeouts. The login
 *       is a member of no role, cannot write the table (table or column privilege) and runs with
 *       {@code search_path=pg_catalog}; the relation must be a plain table.
 *   <li>The query is the one of {@code src/maezo/portal/api/postgres.py} {@code get_membership},
 *       schema-qualified: {@code SELECT payload ... WHERE tenant AND issuer AND subject}.
 * </ul>
 */
final class MembershipSourceObserver {
  static final String TABLE = "portal_memberships";
  static final int MAX_DSN = 4096;
  static final String DRIVER = "org.postgresql.Driver";
  static final Set<String> RECORD_KEYS = Set.of("tenant", "issuer", "subject", "principal_ref",
      "revision", "audience", "memberships", "subject_bindings", "reviewed_until", "revoked");

  final Path dsnFile, caFile;
  final String schema, publisherRef;

  /** The observer login's connection, parsed from {@code postgresql://login:password@host[:port]/db}. */
  record Dsn(String login, String password, String host, int port, String database) {
    @Override
    public String toString() {
      return "MembershipSourceDsn[redacted]";
    }
  }

  MembershipSourceObserver(Map<String, Object> source) {
    Fields.keys(source, "dsn_file", "ca_file", "source_schema", "publisher_ref");
    dsnFile = Fields.absolute(source, "dsn_file");
    caFile = Fields.absolute(source, "ca_file");
    schema = Fields.identifier(source, "source_schema");
    publisherRef = Fields.ref(source, "publisher_ref");
  }

  /**
   * One observation: the live row, validated as {@code MembershipRecord} and projected. Refuses when
   * the row is absent, malformed, of another tenant/issuer/subject, or when anything about the
   * connection or the login is not what the installation pinned.
   */
  Map<String, Object> observe(String tenant, String issuer, String subject, int timeoutSeconds) {
    Dsn dsn = dsn(readSecret(dsnFile));
    requireCa(caFile);
    byte[] payload;
    try {
      payload = read(dsn, tenant, issuer, subject, timeoutSeconds);
    } catch (Exception failure) {
      throw Fields.unavailable();
    }
    // The row's own issuer/subject must be the publication's: verify() compares the whole payload.
    return projection(tenant, SourceJson.parse(payload));
  }

  static byte[] readSecret(Path file) {
    try {
      if (file == null || Files.isSymbolicLink(file))
        throw Fields.unavailable();
      PosixFileAttributes attributes =
          Files.readAttributes(file, PosixFileAttributes.class, LinkOption.NOFOLLOW_LINKS);
      if (!attributes.isRegularFile()
          || !attributes.permissions().equals(Set.of(PosixFilePermission.OWNER_READ))
          || !attributes.owner().getName().equals(System.getProperty("user.name"))
          || attributes.size() > MAX_DSN)
        throw Fields.unavailable();
      return Files.readAllBytes(file);
    } catch (IOException | UnsupportedOperationException | SecurityException ex) {
      throw Fields.unavailable();
    }
  }

  static void requireCa(Path file) {
    try {
      if (file == null || Files.isSymbolicLink(file)
          || !Files.isRegularFile(file, LinkOption.NOFOLLOW_LINKS)
          || Files.size(file) > Fields.MAX_FILE || Files.size(file) == 0)
        throw Fields.unavailable();
    } catch (IOException | SecurityException ex) {
      throw Fields.unavailable();
    }
  }

  /**
   * {@code postgresql://login:password@host[:port]/database}, one line, no query string (every
   * connection parameter is fixed by the provider, never by the file), optional single trailing LF.
   * The password may be percent-encoded, as SQLAlchemy URLs are.
   */
  static Dsn dsn(byte[] raw) {
    String value;
    try {
      value = StandardCharsets.UTF_8.newDecoder()
                  .onMalformedInput(CodingErrorAction.REPORT)
                  .onUnmappableCharacter(CodingErrorAction.REPORT)
                  .decode(ByteBuffer.wrap(raw))
                  .toString();
    } catch (CharacterCodingException malformed) {
      throw Fields.unavailable();
    }
    if (value.endsWith("\n"))
      value = value.substring(0, value.length() - 1);
    var m = java.util.regex.Pattern
                .compile("postgresql://([a-z_][a-z0-9_]{0,62}):([^@/?#\\s]+)@"
                    + "([A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?)(?::([1-9][0-9]{0,4}))?"
                    + "/([A-Za-z_][A-Za-z0-9_-]{0,62})")
                .matcher(value);
    if (!m.matches() || m.group(3).contains(".."))
      throw Fields.unavailable();
    int port = m.group(4) == null ? 5432 : Integer.parseInt(m.group(4));
    if (port > 65535)
      throw Fields.unavailable();
    String password = percentDecode(m.group(2));
    if (password.isEmpty() || password.chars().anyMatch(c -> c < 32 || c == 127))
      throw Fields.unavailable();
    return new Dsn(m.group(1), password, m.group(3), port, m.group(5));
  }

  static String percentDecode(String s) {
    var out = new ByteArrayOutputStream();
    for (int i = 0; i < s.length(); i++) {
      char c = s.charAt(i);
      if (c == '%') {
        if (i + 2 >= s.length() || !s.substring(i + 1, i + 3).matches("[0-9A-Fa-f]{2}"))
          throw Fields.unavailable();
        out.write(Integer.parseInt(s.substring(i + 1, i + 3), 16));
        i += 2;
      } else {
        byte[] b = String.valueOf(c).getBytes(StandardCharsets.UTF_8);
        if (Character.isSurrogate(c))
          throw Fields.unavailable(); // the regex admits only BMP-safe text; refuse the rest
        out.write(b, 0, b.length);
      }
    }
    try {
      return StandardCharsets.UTF_8.newDecoder()
          .onMalformedInput(CodingErrorAction.REPORT)
          .onUnmappableCharacter(CodingErrorAction.REPORT)
          .decode(ByteBuffer.wrap(out.toByteArray()))
          .toString();
    } catch (CharacterCodingException malformed) {
      throw Fields.unavailable();
    }
  }

  /** The connection properties are the provider's, never the file's. */
  Properties properties(Dsn dsn, int timeoutSeconds) {
    var p = new Properties();
    p.setProperty("user", dsn.login());
    p.setProperty("password", dsn.password());
    p.setProperty("ssl", "true");
    p.setProperty("sslmode", "verify-full");
    p.setProperty("sslrootcert", caFile.toString());
    p.setProperty("sslfactory", "org.postgresql.ssl.LibPQFactory");
    p.setProperty("gssEncMode", "disable");
    p.setProperty("readOnly", "true");
    p.setProperty("connectTimeout", Integer.toString(timeoutSeconds));
    p.setProperty("loginTimeout", Integer.toString(timeoutSeconds));
    p.setProperty("socketTimeout", Integer.toString(timeoutSeconds + 1));
    p.setProperty("ApplicationName", "maezo-portal-read-provider");
    return p;
  }

  private byte[] read(Dsn dsn, String tenant, String issuer, String subject, int timeoutSeconds)
      throws SQLException {
    String url = "jdbc:postgresql://" + dsn.host() + ":" + dsn.port() + "/" + dsn.database();
    Driver driver = DriverManager.getDriver(url);
    if (!DRIVER.equals(driver.getClass().getName()))
      throw Fields.unavailable();
    try (Connection connection = driver.connect(url, properties(dsn, timeoutSeconds))) {
      if (connection == null)
        throw Fields.unavailable();
      connection.setAutoCommit(false);
      try {
        try (Statement statement = connection.createStatement()) {
          statement.setQueryTimeout(timeoutSeconds);
          statement.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY");
          String millis = Integer.toString(timeoutSeconds * 1000);
          statement.execute("SELECT pg_catalog.set_config('statement_timeout','" + millis
              + "',true),pg_catalog.set_config('lock_timeout','" + millis + "',true),"
              + "pg_catalog.set_config('search_path','pg_catalog',true)");
        }
        posture(connection, timeoutSeconds);
        return row(connection, tenant, issuer, subject, timeoutSeconds);
      } finally {
        connection.rollback();
      }
    }
  }

  /**
   * The observer is an observer: a plain table (never a view standing in for it), a read-only
   * transaction, no write privilege on the table (table OR column level; this also covers being
   * its owner or a superuser), and NO role membership at all, so no role reachable by inheritance,
   * by {@code SET ROLE} ({@code WITH INHERIT FALSE}) or by a login default can write for it
   * (ADR-0060 D1: a pinned login is a member of nothing). What the server enforces on its own (TLS
   * under verify-full, SELECT on the read columns) is not re-asked here.
   */
  private void posture(Connection connection, int timeoutSeconds) throws SQLException {
    try (PreparedStatement query = connection.prepareStatement("SELECT c.relkind::text AS kind, "
             + "pg_catalog.current_setting('transaction_read_only') AS read_only, "
             + "pg_catalog.has_any_column_privilege(session_user, c.oid, 'INSERT') AS can_insert, "
             + "pg_catalog.has_any_column_privilege(session_user, c.oid, 'UPDATE') AS can_update, "
             + "pg_catalog.has_table_privilege(session_user, c.oid, 'DELETE') AS can_delete, "
             + "pg_catalog.has_table_privilege(session_user, c.oid, 'TRUNCATE') AS can_truncate, "
             + "EXISTS (SELECT 1 FROM pg_catalog.pg_auth_members a JOIN pg_catalog.pg_roles s "
             + " ON s.oid = a.member WHERE s.rolname = session_user) AS member_of_any "
             + "FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n "
             + "ON n.oid = c.relnamespace WHERE n.nspname = ? AND c.relname = ?")) {
      query.setQueryTimeout(timeoutSeconds);
      query.setString(1, schema);
      query.setString(2, TABLE);
      try (ResultSet r = query.executeQuery()) {
        if (!r.next())
          throw Fields.unavailable();
        boolean qualified = "r".equals(r.getString("kind"))
            && "on".equals(r.getString("read_only")) && !r.getBoolean("can_insert")
            && !r.getBoolean("can_update") && !r.getBoolean("can_delete")
            && !r.getBoolean("can_truncate") && !r.getBoolean("member_of_any");
        if (!qualified || r.next())
          throw Fields.unavailable();
      }
    }
  }

  private byte[] row(Connection connection, String tenant, String issuer, String subject,
      int timeoutSeconds) throws SQLException {
    // source_schema is a closed lower-case identifier (Fields.identifier); quoting keeps it exact.
    try (PreparedStatement query = connection.prepareStatement("SELECT payload FROM \"" + schema
             + "\"." + TABLE + " WHERE tenant=? AND issuer=? AND subject=?")) {
      query.setQueryTimeout(timeoutSeconds);
      query.setString(1, tenant);
      query.setString(2, issuer);
      query.setString(3, subject);
      try (ResultSet r = query.executeQuery()) {
        if (!r.next())
          throw Fields.unavailable();
        String payload = r.getString(1);
        if (payload == null || r.next())
          throw Fields.unavailable();
        return payload.getBytes(StandardCharsets.UTF_8);
      }
    }
  }

  // ---------------------------------------------------------------- MembershipRecord -> projection

  /**
   * {@code MembershipRecord} (src/maezo/portal/api/records.py) validated with the same closed rules,
   * then projected exactly as {@code read_publisher.py:111-121} and wired as
   * {@code read_profile.wire} does: revision as a decimal string, {@code state} from
   * {@code revoked}, {@code reviewed_until} in UTC with six fractional digits. Field types are the
   * engine's own ({@code PortalReadModels} shape {@code membership}), since the engine validates the
   * published payload with them anyway.
   */
  static Map<String, Object> projection(String tenant, Object value) {
    Map<String, Object> record = Fields.object(value);
    if (!RECORD_KEYS.containsAll(record.keySet())
        || !record.keySet().containsAll(Set.of("tenant", "issuer", "subject", "principal_ref",
            "revision", "audience", "memberships", "subject_bindings", "reviewed_until")))
      throw Fields.unavailable();
    if (!tenant.equals(record.get("tenant")))
      throw Fields.unavailable();
    String issuer = Fields.string(record, "issuer");
    String subject = Fields.ref(record, "subject");
    String principal = Fields.ref(record, "principal_ref");
    if (!(record.get("revision") instanceof Long revision) || revision < 0
        || revision > Long.MAX_VALUE - 1)
      throw Fields.unavailable();
    String audience = Fields.string(record, "audience");
    if (!Set.of("staff", "beneficiary", "provider").contains(audience))
      throw Fields.unavailable();
    List<Object> memberships = new ArrayList<>();
    boolean anyRole = false;
    Set<String> seen = new HashSet<>();
    for (Object o : Fields.list(record.get("memberships"))) {
      var m = Fields.object(o);
      Fields.keys(m, "membership_ref", "roles", "groups");
      var roles = refs(m.get("roles"));
      anyRole |= !roles.isEmpty();
      var binding = new TreeMap<String, Object>(Map.of("membership_ref",
          Fields.ref(m, "membership_ref"), "roles", roles, "groups", refs(m.get("groups"))));
      if (!seen.add(Fields.digest(binding)))
        throw Fields.unavailable();
      memberships.add(binding);
    }
    if (memberships.isEmpty() || !anyRole)
      throw Fields.unavailable();
    List<Object> bindings = new ArrayList<>();
    seen.clear();
    for (Object o : Fields.list(record.get("subject_bindings"))) {
      var b = Fields.object(o);
      Fields.keys(b, "kind", "resource_ref");
      String kind = Fields.string(b, "kind");
      if (!kind.equals("beneficiary") && !kind.equals("provider"))
        throw Fields.unavailable();
      if (!audience.equals("staff") && !kind.equals(audience))
        throw Fields.unavailable(); // incompatible subject relationship
      var binding = new TreeMap<String, Object>(
          Map.of("kind", kind, "resource_ref", Fields.ref(b, "resource_ref")));
      if (!seen.add(Fields.digest(binding)))
        throw Fields.unavailable();
      bindings.add(binding);
    }
    if (!audience.equals("staff") && bindings.isEmpty())
      throw Fields.unavailable(); // verified subject relationship required
    Object revoked = record.getOrDefault("revoked", Boolean.FALSE);
    if (!(revoked instanceof Boolean))
      throw Fields.unavailable();
    Map<String, Object> out = new TreeMap<>();
    out.put("principal_ref", principal);
    out.put("issuer", issuer);
    out.put("subject", subject);
    out.put("membership_revision", Long.toString(revision));
    out.put("audience", audience);
    out.put("memberships", memberships);
    out.put("subject_bindings", bindings);
    out.put("state", (Boolean) revoked ? "revoked" : "active");
    out.put("reviewed_until", Fields.format(aware(record.get("reviewed_until"))));
    Jcs.canonical(out); // the projection must be expressible in the engine's profile
    return out;
  }

  private static List<Object> refs(Object value) {
    List<Object> out = new ArrayList<>();
    Set<String> seen = new HashSet<>();
    for (Object o : Fields.list(value)) {
      if (!(o instanceof String s) || !s.matches("[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,254}")
          || !seen.add(s))
        throw Fields.unavailable();
      out.add(s);
    }
    return out;
  }

  private static final java.util.regex.Pattern AWARE = java.util.regex.Pattern.compile(
      "[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\\.[0-9]{1,6})?(Z|[+-][0-9]{2}:[0-9]{2})");

  /**
   * An offset-aware instant as pydantic {@code model_dump_json} writes it ({@code Z} or
   * {@code ±HH:MM}, at most microseconds). A naive time is refused (records.py requires an aware
   * review deadline); anything finer than a microsecond is refused rather than truncated.
   */
  static Instant aware(Object value) {
    if (!(value instanceof String s) || !AWARE.matcher(s).matches())
      throw Fields.unavailable();
    try {
      return OffsetDateTime.parse(s, DateTimeFormatter.ISO_OFFSET_DATE_TIME).toInstant();
    } catch (DateTimeException ex) {
      throw Fields.unavailable();
    }
  }
}
