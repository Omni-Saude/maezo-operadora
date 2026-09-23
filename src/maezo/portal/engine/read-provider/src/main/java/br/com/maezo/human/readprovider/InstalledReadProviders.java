package br.com.maezo.human.readprovider;

import br.com.maezo.human.Jcs;
import br.com.maezo.human.PortalReadTrust;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.Path;
import java.sql.Connection;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Statement;
import java.time.Clock;
import java.time.Instant;
import java.util.Map;
import java.util.Set;
import java.util.logging.Logger;
import javax.naming.InitialContext;
import javax.sql.DataSource;

/**
 * The installed {@code PortalReadTrust.Providers} (T1.7a), cut to the staff scope. Registered once in
 * {@code META-INF/services/br.com.maezo.human.PortalReadTrust$Providers}; the JAR ships only in the
 * {@code INSTALL_PORTAL_READ=true} image.
 *
 * <p>Fail-closed in everything: a missing/invalid configuration, key file or code digest is
 * recorded at construction (ServiceLoader instantiation must not throw an unbounded error) and
 * makes every method refuse with {@code 503 READ_DEPENDENCY_UNAVAILABLE}, which the plugin turns
 * into a boot refusal. Publication qualification covers the staff scope (T1.7b: membership,
 * catalog designation/revocation, key revocation); resource publication, identity policy and
 * classification are Onda 8 and are refused.
 */
public final class InstalledReadProviders implements PortalReadTrust.Providers {
  static final String CONFIGURATION_ENV = "MAEZO_PORTAL_READ_PROVIDER_FILE";
  static final String TABLE = "mzo_portal_read_admission";
  /** Bound for the admission read itself, before the admitted statement timeout is known. */
  static final int READ_TIMEOUT_SECONDS = 5;
  static final long MAX_CODE_BYTES = 67108864L;

  private record State(ProviderConfiguration configuration, NativeContinuityKeys keys,
      MembershipSourceObserver membership, String engineCode, String providerCode) {}
  private record Row(long revision, byte[] record, byte[] signature, boolean revoked) {}

  final Clock clock;
  private final State state;
  /** Anti-rollback, in memory: the highest revision ever verified by this process. */
  private long highest;
  /** Highest revision observed revoked at or above {@link #highest}; kills retained admissions. */
  private long revokedUpTo;
  /** Highest revision already stated on the public log line (once per admitted revision). */
  private long announced;
  static final Logger LOG = Logger.getLogger(InstalledReadProviders.class.getName());

  public InstalledReadProviders() {
    this(configured(), Clock.systemUTC());
  }

  InstalledReadProviders(Path configurationFile, Clock clock) {
    this.clock = clock;
    State loaded = null;
    try {
      var configuration = ProviderConfiguration.load(configurationFile);
      loaded = new State(configuration, NativeContinuityKeys.load(configuration.continuityKeysFile),
          new MembershipSourceObserver(configuration.membershipSource),
          codeDigest(PortalReadTrust.class), codeDigest(InstalledReadProviders.class));
    } catch (RuntimeException refused) {
      loaded = null;
    }
    state = loaded;
    highest = state == null ? Long.MAX_VALUE : state.configuration.minimumAdmissionRevision;
    revokedUpTo = 0;
  }

  private static Path configured() {
    try {
      String file = System.getenv(CONFIGURATION_ENV);
      return file == null || file.isBlank() ? null : Path.of(file);
    } catch (RuntimeException invalid) {
      return null;
    }
  }

  /** SHA-256 of the JAR the class was actually loaded from; a directory or link is refused. */
  static String codeDigest(Class<?> type) {
    try {
      Path jar = Path.of(type.getProtectionDomain().getCodeSource().getLocation().toURI());
      if (Files.isSymbolicLink(jar) || !Files.isRegularFile(jar, LinkOption.NOFOLLOW_LINKS)
          || Files.size(jar) > MAX_CODE_BYTES)
        throw Fields.unavailable();
      return Jcs.digest(Files.readAllBytes(jar));
    } catch (Exception failure) {
      throw Fields.unavailable();
    }
  }

  private State require() {
    if (state == null)
      throw Fields.unavailable();
    return state;
  }

  synchronized boolean current(long revision) {
    return revision == highest && revision > revokedUpTo;
  }

  /**
   * Re-reads the admission row OUTSIDE any engine transaction, on its own pooled connection, in a
   * read-only REPEATABLE READ transaction with local statement/lock timeouts, then verifies it
   * completely. Never trusts, echoes or caches a caller-declared fact.
   */
  @Override
  public PortalReadTrust.Admission acquire(Map<String, Object> scope, String engine,
      String incarnation, String deployment, String digest, String configurationDigest,
      String purpose) {
    State s = require();
    Row row;
    try {
      row = read(s.configuration);
    } catch (Exception failure) {
      throw Fields.unavailable();
    }
    synchronized (this) {
      if (row.revoked) {
        if (row.revision >= highest)
          revokedUpTo = Math.max(revokedUpTo, row.revision);
        throw Fields.unavailable();
      }
      if (row.revision < highest)
        throw Fields.unavailable();
    }
    Instant now = clock.instant();
    AdmissionRecord record;
    try {
      record = AdmissionRecord.verify(row.record, row.signature, s.configuration.root);
      if (!record.admissionRef.equals(s.configuration.admissionRef)
          || record.revision != row.revision || !record.engine.equals(engine)
          || !record.incarnation.equals(incarnation) || !record.deployment.equals(deployment)
          || !record.deploymentDigest.equals(digest)
          || !record.trustConfigurationDigest.equals(configurationDigest)
          || !record.purposes.contains(purpose) || !scopeAdmitted(record, scope, purpose)
          || !record.engineCodeDigest.equals(s.engineCode)
          || !record.providerCodeDigest.equals(s.providerCode)
          || now.isBefore(record.notBefore) || !now.isBefore(record.validUntil))
        throw Fields.unavailable();
    } catch (RuntimeException refused) {
      throw Fields.unavailable();
    }
    boolean announce;
    synchronized (this) {
      if (record.revision < highest || record.revision <= revokedUpTo)
        throw Fields.unavailable();
      highest = record.revision;
      announce = record.revision > announced;
      if (announce)
        announced = record.revision;
    }
    if (announce)
      LOG.info(publicLine(s, record));
    Instant ceiling = now.plusSeconds(record.observationSeconds);
    return new ProviderAdmission(this, record, purpose, now,
        record.validUntil.isBefore(ceiling) ? record.validUntil : ceiling);
  }

  /**
   * The independent channel for the approver (plan section 4, {@code portal_read_root_sha256}):
   * the configuration file that names the root is mounted by engineering, so the root pin in it
   * proves nothing by itself. The live engine states WHICH root and WHICH admission bytes it
   * accepted, and the approver compares that with his own key and the record he signed. Only
   * public, closed values (hex digests and refs); never a key, DSN, signature or record body.
   */
  static String publicLine(State s, AdmissionRecord record) {
    return "portal_read_provider root_sha256=" + s.configuration.rootPin
        + " admission_ref=" + record.admissionRef + " revision=" + record.revision
        + " capability_digest=" + record.digest + " engine_code=" + s.engineCode
        + " provider_code=" + s.providerCode;
  }

  private static boolean scopeAdmitted(
      AdmissionRecord record, Map<String, Object> scope, String purpose) {
    if (scope == null || !scope.keySet().equals(Set.of("tenant", "environment", "workload_ref")))
      return false;
    if (purpose.equals("portal-task-read"))
      return record.scope.equals(scope);
    return record.scope.get("tenant").equals(scope.get("tenant"))
        && record.scope.get("environment").equals(scope.get("environment"))
        && record.publishers.values().stream().anyMatch(
            p -> p.publisherRef().equals(scope.get("workload_ref")));
  }

  private Row read(ProviderConfiguration c) throws Exception {
    DataSource source = (DataSource) new InitialContext().lookup(c.dataSourceJndi);
    try (Connection connection = source.getConnection()) {
      boolean autoCommit = connection.getAutoCommit();
      try {
        if (!autoCommit)
          connection.rollback(); // never inherit a pooled transaction: this read opens its own
        connection.setAutoCommit(false);
        try (Statement statement = connection.createStatement()) {
          statement.setQueryTimeout(READ_TIMEOUT_SECONDS);
          statement.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY");
          String millis = Integer.toString(READ_TIMEOUT_SECONDS * 1000);
          // search_path=pg_catalog: no operator, function or type below may resolve through a
          // schema the engine login (or anyone) can create objects in (CVE-2018-1058).
          statement.execute("SELECT pg_catalog.set_config('statement_timeout','" + millis
              + "',true),pg_catalog.set_config('lock_timeout','" + millis + "',true),"
              + "pg_catalog.set_config('search_path','pg_catalog',true)");
        }
        pin(connection, c);
        return row(connection, c);
      } finally {
        try {
          connection.rollback();
        } finally {
          connection.setAutoCommit(autoCommit);
        }
      }
    }
  }

  /** The CLOSED pin: every clause is a column that must be exactly true or exactly false. */
  static final String PIN_SQL = "SELECT c.oid::text AS table_oid, "
      + "pg_catalog.pg_get_userbyid(c.relowner) AS owner, c.relkind::text AS kind, "
      + "session_user::text AS login, current_user::text AS acting, "
      + "pg_catalog.has_table_privilege(session_user, c.oid, 'SELECT') AS can_select, "
      + "pg_catalog.has_table_privilege(session_user, c.oid, "
      + "'INSERT, UPDATE, DELETE, TRUNCATE, TRIGGER, REFERENCES') AS can_write, "
      // Closed ACL list: besides the owner's own entries, exactly one entry, SELECT for the
      // engine login granted by the owner, not grantable. A write grant to ANY other role
      // (including a role reachable only by SET ROLE, i.e. WITH INHERIT FALSE) is refused here,
      // because has_table_privilege() only sees inherited privileges.
      + "(SELECT pg_catalog.count(*) FROM pg_catalog.aclexplode(c.relacl) x "
      + " WHERE NOT (x.grantee = c.relowner AND x.grantor = c.relowner)"
      + "   AND NOT (x.grantee = s.oid AND x.grantor = c.relowner "
      + "            AND x.privilege_type = 'SELECT' AND NOT x.is_grantable)) AS foreign_acl, "
      + "(SELECT pg_catalog.count(*) FROM pg_catalog.aclexplode(c.relacl) x "
      + " WHERE x.grantee = s.oid AND x.privilege_type = 'SELECT') AS engine_select, "
      // Column ACLs are invisible to has_table_privilege(): GRANT UPDATE (revoked_) must refuse.
      + "EXISTS (SELECT 1 FROM pg_catalog.pg_attribute a "
      + " WHERE a.attrelid = c.oid AND a.attacl IS NOT NULL) AS column_acl, "
      // A trigger or rule can make revocation impossible (BEFORE UPDATE ... RETURN OLD).
      + "EXISTS (SELECT 1 FROM pg_catalog.pg_trigger t WHERE t.tgrelid = c.oid) AS has_trigger, "
      + "EXISTS (SELECT 1 FROM pg_catalog.pg_rewrite w WHERE w.ev_class = c.oid) AS has_rule, "
      + "c.relrowsecurity AS row_security, "
      + "pg_catalog.pg_has_role(session_user, c.relowner, 'MEMBER') AS owner_member, "
      + "EXISTS (SELECT 1 FROM pg_catalog.pg_roles w WHERE w.rolname = 'pg_write_all_data' "
      + " AND pg_catalog.pg_has_role(session_user, w.oid, 'MEMBER')) AS write_all, "
      + "(s.rolsuper OR s.rolbypassrls OR s.rolcreaterole) AS privileged "
      + "FROM pg_catalog.pg_class c "
      + "JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
      + "JOIN pg_catalog.pg_roles s ON s.rolname = session_user "
      + "WHERE n.nspname = ? AND c.relname = ?";

  /**
   * The table must be the pinned one (OID, owner, exact schema) and the engine login must have no
   * path to change it: no write privilege (table or column, direct, inherited or via SET ROLE),
   * not the owner nor a member of it, not superuser/bypassrls/createrole, no trigger, rule or RLS
   * on the table. A login that CAN write is refused even if it did not: the provider does not
   * admit a table its runtime could forge or un-revoke.
   */
  private static void pin(Connection connection, ProviderConfiguration c) throws SQLException {
    try (PreparedStatement query = connection.prepareStatement(PIN_SQL)) {
      query.setQueryTimeout(READ_TIMEOUT_SECONDS);
      query.setString(1, c.nativeSchema);
      query.setString(2, TABLE);
      try (ResultSet r = query.executeQuery()) {
        if (!r.next())
          throw Fields.unavailable();
        boolean qualified = Long.toString(c.admissionTableOid).equals(r.getString("table_oid"))
            && c.admissionTableOwner.equals(r.getString("owner"))
            && "r".equals(r.getString("kind"))
            && r.getString("login").equals(r.getString("acting"))
            && !c.admissionTableOwner.equals(r.getString("login"))
            && isTrue(r, "can_select") && isFalse(r, "can_write")
            && r.getLong("foreign_acl") == 0 && !r.wasNull()
            && r.getLong("engine_select") == 1 && !r.wasNull()
            && isFalse(r, "column_acl") && isFalse(r, "has_trigger") && isFalse(r, "has_rule")
            && isFalse(r, "row_security") && isFalse(r, "owner_member")
            && isFalse(r, "write_all") && isFalse(r, "privileged");
        if (!qualified || r.next())
          throw Fields.unavailable();
      }
    }
  }

  private static boolean isTrue(ResultSet r, String column) throws SQLException {
    boolean value = r.getBoolean(column);
    return !r.wasNull() && value;
  }

  private static boolean isFalse(ResultSet r, String column) throws SQLException {
    boolean value = r.getBoolean(column);
    return !r.wasNull() && !value;
  }

  private static Row row(Connection connection, ProviderConfiguration c) throws SQLException {
    // native_schema is a closed lower-case identifier (Fields.identifier); quoting keeps it exact.
    // tableoid binds the rows to the SAME relation the pin qualified: the name is not resolved
    // a second time on trust.
    try (PreparedStatement query = connection.prepareStatement(
             "SELECT revision_::text AS revision, record_, signature_, revoked_ "
             + "FROM \"" + c.nativeSchema + "\"." + TABLE + " WHERE admission_ref_ = ? "
             + "AND tableoid = CAST(? AS pg_catalog.int8)::pg_catalog.oid "
             + "ORDER BY revision_ DESC LIMIT 1")) {
      query.setQueryTimeout(READ_TIMEOUT_SECONDS);
      query.setString(1, c.admissionRef);
      query.setLong(2, c.admissionTableOid);
      try (ResultSet r = query.executeQuery()) {
        if (!r.next())
          throw Fields.unavailable();
        String revision = r.getString("revision");
        if (revision == null || !revision.matches("[1-9][0-9]{0,17}"))
          throw Fields.unavailable();
        return new Row(Long.parseLong(revision), r.getBytes("record_"), r.getBytes("signature_"),
            r.getBoolean("revoked_"));
      }
    }
  }

  @Override
  public PortalReadTrust.NativeKeySet continuity(PortalReadTrust.Admission admission) {
    State s = require();
    if (!(admission instanceof ProviderAdmission own) || own.provider != this)
      throw Fields.unavailable();
    own.requireCurrent();
    try {
      return s.keys.bind(own, clock);
    } catch (RuntimeException refused) {
      throw Fields.unavailable();
    }
  }

  /**
   * T1.7b: qualifies one staff-scope publication OUTSIDE the engine command (the plugin calls this
   * after {@code acquire} and before the tenant lock). The request must be bound to the admitted
   * engine, incarnation, deployment and scope. {@code membership} re-reads the live source row
   * itself; {@code catalog-designate}, {@code catalog-revoke} and {@code revoke-key} need no I/O;
   * {@code resource} and anything else are Onda 8 and refused. The returned qualification only
   * compares in memory ({@link StaffScopeQualification#verify}).
   */
  @Override
  public PortalReadTrust.PublicationQualification qualifyPublication(
      PortalReadTrust.Admission admission, Map<String, Object> publication) {
    State s = require();
    if (!(admission instanceof ProviderAdmission own) || own.provider != this
        || !"portal-read-publication".equals(own.purpose))
      throw Fields.unavailable();
    own.requireCurrent();
    try {
      var record = own.record;
      if (publication == null)
        throw Fields.unavailable();
      Fields.keys(publication, "schema", "scope", "engine_name", "database_incarnation",
          "read_deployment_ref", "read_deployment_digest", "publication_id",
          "expected_authority_revision", "source", "kind", "payload");
      Fields.exact(publication, "schema", "portal-read-publication.v1");
      Fields.exact(publication, "engine_name", record.engine);
      Fields.exact(publication, "database_incarnation", record.incarnation);
      Fields.exact(publication, "read_deployment_ref", record.deployment);
      Fields.exact(publication, "read_deployment_digest", record.deploymentDigest);
      var scope = Fields.object(publication.get("scope"));
      Fields.keys(scope, "tenant", "environment", "workload_ref");
      Fields.exact(scope, "tenant", (String) record.scope.get("tenant"));
      Fields.exact(scope, "environment", (String) record.scope.get("environment"));
      String kind = Fields.string(publication, "kind");
      var source = Fields.object(publication.get("source"));
      Fields.keys(source, "publisher_ref", "source_ref", "source_revision", "source_digest",
          "receipt_ref", "observed_at", "valid_until");
      Fields.exact(scope, "workload_ref", Fields.ref(source, "publisher_ref"));
      Fields.ref(source, "source_ref");
      Fields.decimal(source, "source_revision", 0, Long.MAX_VALUE - 1);
      Fields.hash(source, "source_digest");
      Fields.ref(source, "receipt_ref");
      Fields.time(source, "observed_at");
      Fields.time(source, "valid_until");
      var payload = Fields.object(publication.get("payload"));
      Map<String, Object> observed = null;
      switch (kind) {
        case "membership" -> {
          var publisher = record.publishers.get("membership");
          if (publisher == null || !publisher.publisherRef().equals(s.membership.publisherRef))
            throw Fields.unavailable();
          observed = s.membership.observe((String) record.scope.get("tenant"),
              Fields.string(payload, "issuer"), Fields.ref(payload, "subject"),
              record.statementTimeoutSeconds);
        }
        case "catalog-designate", "catalog-revoke", "revoke-key" -> {}
        default -> throw Fields.unavailable(); // resource (Onda 8) and anything unknown
      }
      return new StaffScopeQualification(own, kind, source, payload, observed);
    } catch (RuntimeException refused) {
      throw Fields.unavailable();
    }
  }

  @Override
  public String toString() {
    return "InstalledReadProviders[redacted]";
  }
}
