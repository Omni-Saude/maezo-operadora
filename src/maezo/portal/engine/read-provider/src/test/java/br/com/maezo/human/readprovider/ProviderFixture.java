package br.com.maezo.human.readprovider;

import static br.com.maezo.human.readprovider.TestRecords.*;

import br.com.maezo.human.Jcs;
import br.com.maezo.human.PortalReadTrust;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.attribute.PosixFilePermissions;
import java.security.KeyPair;
import java.security.SecureRandom;
import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Statement;
import java.time.Instant;
import java.util.ArrayList;
import java.util.Base64;
import java.util.HexFormat;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import org.postgresql.ds.PGSimpleDataSource;

/**
 * Real PostgreSQL fixture for the JAR ITs: a random native schema owned by a NOLOGIN owner role, the
 * shipped admission DDL applied AS that owner, and a separate LOGIN runtime role with SELECT only
 * (the grant matrix T1.4 installs for {@code cibseven_app}). The provider reaches the runtime role
 * through JNDI, exactly as in Tomcat. Explicit configuration is required: a missing variable fails
 * the test, it never skips it.
 */
public final class ProviderFixture implements AutoCloseable {
  public final String adminUrl, adminUser, adminPassword;
  public final String suffix, schema, owner, runtime;
  private final String runtimePassword;
  public final Path trustFile, providerFile, keysFile;
  public final KeyPair root = ed25519(), otherRoot = ed25519(), reader = ed25519(),
                       publisher = ed25519();
  /** T1.7b: the live membership source (a stand-in for {@code amh}) and its observer login. */
  public final String sourceSchema, sourceOwner, sourceLogin;
  private final String sourcePassword;
  public final Path dsnFile;
  public final ServerTls.Material tls;
  public static final String PUBLISHER_PEER = "b".repeat(64);
  public final byte[] secret = new byte[32];
  public final String engine = "default", deployment = "read-release-1",
                      deploymentDigest = "c".repeat(64);
  public final String incarnation;
  public final Instant before, until;
  public final Map<String, Object> trustRecord;
  public final String trustDigest;
  public final String engineCode, providerCode;
  public final long tableOid;

  public static String required(String name) {
    String value = System.getenv(name);
    if (value == null || value.isBlank())
      throw new IllegalStateException("explicit provider IT setting missing: " + name);
    return value;
  }

  public ProviderFixture() throws Exception {
    adminUrl = required("MAEZO_READ_PROVIDER_IT_JDBC_URL");
    adminUser = required("MAEZO_READ_PROVIDER_IT_DB_USER");
    adminPassword = required("MAEZO_READ_PROVIDER_IT_DB_PASSWORD");
    if (adminUrl.matches("(?i).*([?&])(currentSchema|options)=.*"))
      throw new IllegalStateException("the fixture pins its own schema");
    trustFile = Path.of(required("MAEZO_PORTAL_READ_TRUST_FILE"));
    providerFile = Path.of(required("MAEZO_PORTAL_READ_PROVIDER_FILE"));
    keysFile = providerFile.resolveSibling("portal-read-continuity-keys.json");
    var random = new SecureRandom();
    byte[] id = new byte[8];
    random.nextBytes(id);
    random.nextBytes(secret);
    suffix = HexFormat.of().formatHex(id);
    schema = "rp_native_" + suffix;
    owner = "rp_owner_" + suffix;
    runtime = "rp_runtime_" + suffix;
    sourceSchema = "rp_amh_" + suffix;
    sourceOwner = "rp_amh_owner_" + suffix;
    sourceLogin = "rp_source_" + suffix;
    byte[] pw = new byte[24];
    random.nextBytes(pw);
    runtimePassword = HexFormat.of().formatHex(pw);
    random.nextBytes(pw);
    sourcePassword = HexFormat.of().formatHex(pw);
    dsnFile = providerFile.resolveSibling("membership-source.dsn");
    tls = ServerTls.ensure(adminUrl, adminUser, adminPassword, providerFile.resolveSibling("tls"));
    incarnation = "incarnation-" + suffix;
    before = now().minusSeconds(60);
    until = before.plusSeconds(7L * 24 * 3600);
    try (var c = admin(); var s = c.createStatement()) {
      s.execute("CREATE ROLE " + owner + " NOLOGIN");
      s.execute("CREATE ROLE " + runtime + " LOGIN PASSWORD '" + runtimePassword + "'");
      s.execute("CREATE SCHEMA " + schema + " AUTHORIZATION " + owner);
      s.execute("GRANT USAGE ON SCHEMA " + schema + " TO " + runtime);
      s.execute("SET ROLE " + owner);
      s.execute("SET search_path = " + schema);
      try (var in = getClass().getResourceAsStream("/portal-read-admission-postgres.sql")) {
        s.execute(new String(Objects.requireNonNull(in).readAllBytes(), StandardCharsets.UTF_8));
      }
      s.execute("RESET ROLE");
      s.execute("GRANT SELECT ON " + schema + ".mzo_portal_read_admission TO " + runtime);
      // The membership source: migration 0012's table, owned by a NOLOGIN owner, and the observer
      // login with SELECT on exactly the columns the provider reads (what T1.4 grants).
      s.execute("CREATE ROLE " + sourceOwner + " NOLOGIN");
      s.execute("CREATE ROLE " + sourceLogin + " LOGIN PASSWORD '" + sourcePassword + "'");
      s.execute("CREATE SCHEMA " + sourceSchema + " AUTHORIZATION " + sourceOwner);
      s.execute("GRANT USAGE ON SCHEMA " + sourceSchema + " TO " + sourceLogin);
      s.execute("SET ROLE " + sourceOwner);
      s.execute("CREATE TABLE " + sourceSchema + ".portal_memberships (tenant text NOT NULL, "
          + "issuer text NOT NULL, subject text NOT NULL, principal_ref text NOT NULL, "
          + "payload text NOT NULL, PRIMARY KEY (tenant, issuer, subject), "
          + "UNIQUE (tenant, principal_ref))");
      s.execute("REVOKE ALL ON TABLE " + sourceSchema + ".portal_memberships FROM PUBLIC");
      s.execute("RESET ROLE");
      s.execute("GRANT SELECT (tenant, issuer, subject, payload) ON " + sourceSchema
          + ".portal_memberships TO " + sourceLogin);
    }
    try (var c = admin(); var q = c.prepareStatement(
             "SELECT c.oid::text FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
             + "WHERE n.nspname=? AND c.relname='mzo_portal_read_admission'")) {
      q.setString(1, schema);
      try (ResultSet r = q.executeQuery()) {
        if (!r.next())
          throw new IllegalStateException("admission DDL did not create the table");
        tableOid = Long.parseLong(r.getString(1));
      }
    }
    var source = new PGSimpleDataSource();
    source.setURL(adminUrl);
    source.setUser(runtime);
    source.setPassword(runtimePassword);
    TestNaming.bind(source);
    trustRecord = record("schema", "portal-read-trust.v1", "scope", scope(), "engine_name", engine,
        "database_incarnation", incarnation, "audience", "portal-staff-read",
        "read_deployment_ref", deployment, "read_deployment_digest", deploymentDigest,
        "validity_policy_ref", "PUBLIC_SYNTHETIC", "validity_policy_digest", "d".repeat(64),
        "max_envelope_seconds", "60", "public_keys",
        new ArrayList<>(List.of(record("key_id", "reader", "purpose", "portal-task-read",
            "workload_ref", "portal-staff", "peer_spki_sha256", "a".repeat(64),
            "public_key_spki_base64",
            Base64.getEncoder().encodeToString(reader.getPublic().getEncoded()), "not_before",
            time(before), "not_after", time(until)),
        record("key_id", "publisher", "purpose", "portal-read-publication", "workload_ref",
            PUBLISHER, "peer_spki_sha256", PUBLISHER_PEER, "public_key_spki_base64",
            Base64.getEncoder().encodeToString(publisher.getPublic().getEncoded()),
            "not_before", time(before), "not_after", time(until), "publication_kinds",
            new ArrayList<>(List.of("catalog-designate", "catalog-revoke", "membership",
                "resource", "revoke-key")),
            "catalog_ref", CATALOG))));
    trustDigest = Jcs.digest(Jcs.canonical(trustRecord));
    engineCode = loadedJarDigest(PortalReadTrust.class);
    providerCode = loadedJarDigest(InstalledReadProviders.class);
    writeJson(trustFile, trustRecord);
    writeProvider(providerConfiguration());
    writeKeys(keysFile, List.of(continuityKey(KEY_ID, "1", secret, before, until)), "r--------");
    writeDsn(dsn(tls.host()), "r--------");
  }

  /** {@code postgresql://observer:password@host:port/database} for the observer login. */
  public String dsn(String host) {
    var m = java.util.regex.Pattern.compile("jdbc:postgresql://[^:/]+(?::([0-9]+))?/([^?]+).*")
                .matcher(adminUrl);
    if (!m.matches())
      throw new IllegalStateException("IT JDBC URL must be jdbc:postgresql://host[:port]/db");
    return "postgresql://" + sourceLogin + ":" + sourcePassword + "@" + host + ":"
        + (m.group(1) == null ? "5432" : m.group(1)) + "/" + m.group(2);
  }

  public void writeDsn(String content, String mode) throws IOException {
    Files.deleteIfExists(dsnFile);
    Files.write(dsnFile, content.getBytes(StandardCharsets.UTF_8));
    Files.setPosixFilePermissions(dsnFile, PosixFilePermissions.fromString(mode));
  }

  /** Upserts one {@code portal_memberships} row AS the table owner (the administration plane). */
  public void putMembership(String tenant, String issuer, String subject, String principal,
      String payload) throws SQLException {
    try (var c = admin()) {
      try (Statement s = c.createStatement()) {
        s.execute("SET ROLE " + sourceOwner);
      }
      try (var q = c.prepareStatement("INSERT INTO " + sourceSchema + ".portal_memberships "
               + "(tenant,issuer,subject,principal_ref,payload) VALUES(?,?,?,?,?) "
               + "ON CONFLICT(tenant,issuer,subject) DO UPDATE SET payload=EXCLUDED.payload")) {
        q.setString(1, tenant);
        q.setString(2, issuer);
        q.setString(3, subject);
        q.setString(4, principal);
        q.setString(5, payload);
        q.executeUpdate();
      }
    }
  }

  public void deleteMembership(String tenant, String issuer, String subject) throws SQLException {
    try (var c = admin(); var q = c.prepareStatement("DELETE FROM " + sourceSchema
             + ".portal_memberships WHERE tenant=? AND issuer=? AND subject=?")) {
      q.setString(1, tenant);
      q.setString(2, issuer);
      q.setString(3, subject);
      q.executeUpdate();
    }
  }

  /** Runs one administrative statement (e.g. a grant that breaks the observer posture). */
  public void adminExecute(String sql) throws SQLException {
    try (var c = admin(); var s = c.createStatement()) {
      s.execute(sql);
    }
  }

  /** A {@code portal-read-publication.v1} request bound to this fixture's engine and scope. */
  public Map<String, Object> publication(String kind, Map<String, Object> source,
      Map<String, Object> payload, long expectedAuthorityRevision) {
    byte[] id = new byte[32];
    new SecureRandom().nextBytes(id);
    return record("schema", "portal-read-publication.v1", "scope",
        record("tenant", TENANT, "environment", "dev", "workload_ref", PUBLISHER), "engine_name",
        engine, "database_incarnation", incarnation, "read_deployment_ref", deployment,
        "read_deployment_digest", deploymentDigest, "publication_id",
        "publication-" + HexFormat.of().formatHex(id), "expected_authority_revision",
        Long.toString(expectedAuthorityRevision), "source", source, "kind", kind, "payload",
        payload);
  }

  public void putMembership(String tenant, Member m) throws SQLException {
    putMembership(tenant, m.issuer(), m.subject(), m.principal(), membershipRow(tenant, m));
  }

  public void putMembership(String tenant, Member m, List<String> groups) throws SQLException {
    putMembership(tenant, m.issuer(), m.subject(), m.principal(), membershipRow(tenant, m, groups));
  }

  /** An admission that also admits publications (the T1.7b purpose). */
  public Map<String, Object> publicationAdmission(long revision) {
    var a = admission(revision);
    a.put("purposes", new ArrayList<>(List.of("portal-task-read", "portal-read-publication")));
    return a;
  }

  /** The digest the approver would admit: the bytes of the JAR the class really came from. */
  public static String loadedJarDigest(Class<?> type) throws Exception {
    Path jar = Path.of(type.getProtectionDomain().getCodeSource().getLocation().toURI());
    if (!Files.isRegularFile(jar))
      throw new IllegalStateException(type.getName() + " was not loaded from a JAR: " + jar);
    return Jcs.digest(Files.readAllBytes(jar));
  }

  public Connection admin() throws SQLException {
    return DriverManager.getConnection(adminUrl, adminUser, adminPassword);
  }

  public Map<String, Object> providerConfiguration() {
    return TestRecords.providerConfiguration(root, schema, tableOid, owner, keysFile,
        record("dsn_file", dsnFile.toString(), "ca_file", tls.ca().toString(), "source_schema",
            sourceSchema, "publisher_ref", PUBLISHER));
  }

  public void writeProvider(Map<String, Object> configuration) throws Exception {
    writeJson(providerFile, configuration);
  }

  public String commitment() {
    return TestRecords.commitment(secret);
  }

  public Map<String, Object> admission(long revision) {
    return TestRecords.admission(revision, engine, incarnation, deployment, deploymentDigest,
        trustDigest, engineCode, providerCode, commitment(), before, until);
  }

  public Map<String, Object> staffScope() {
    return record("tenant", "tenant-test", "environment", "dev", "engine_name", engine,
        "database_incarnation", incarnation);
  }

  public Map<String, Object> anchor() {
    return record("scope", scope(), "catalog_ref", CATALOG, "publisher_ref", PUBLISHER);
  }

  public void install(Map<String, Object> admission) throws SQLException {
    install(admission, root);
  }

  public void install(Map<String, Object> admission, KeyPair signer) throws SQLException {
    byte[] raw = Jcs.canonical(admission);
    installRaw(Long.parseLong((String) admission.get("admission_revision")), raw,
        sign(signer.getPrivate(), raw));
  }

  public void installRaw(long revision, byte[] raw, byte[] signature) throws SQLException {
    asOwner("INSERT INTO " + schema + ".mzo_portal_read_admission"
            + "(admission_ref_,revision_,record_,signature_) VALUES(?,?,?,?)",
        q -> {
          q.setString(1, ADMISSION_REF);
          q.setLong(2, revision);
          q.setBytes(3, raw);
          q.setBytes(4, signature);
        });
  }

  public void revoke(long revision) throws SQLException {
    asOwner("UPDATE " + schema + ".mzo_portal_read_admission SET revoked_=true WHERE revision_=?",
        q -> q.setLong(1, revision));
  }

  public void delete(long revision) throws SQLException {
    asOwner("DELETE FROM " + schema + ".mzo_portal_read_admission WHERE revision_=?",
        q -> q.setLong(1, revision));
  }

  public void grantRuntime(String privilege) throws SQLException {
    try (var c = admin(); var s = c.createStatement()) {
      s.execute("GRANT " + privilege + " ON " + schema + ".mzo_portal_read_admission TO "
          + runtime);
    }
  }

  /** Runs statements as the fixture's administrator (superuser). {@code %t} = qualified table. */
  public void asAdmin(String... sql) throws SQLException {
    try (var c = admin(); var s = c.createStatement()) {
      for (String each : sql) s.execute(expand(each));
    }
  }

  /** Runs statements as the table owner (what the installation login may do on its own). */
  public void asOwner(String... sql) throws SQLException {
    try (var c = admin(); var s = c.createStatement()) {
      s.execute("SET ROLE " + owner);
      for (String each : sql) s.execute(expand(each));
    }
  }

  private String expand(String sql) {
    return sql.replace("%t", schema + ".mzo_portal_read_admission")
        .replace("%s", schema)
        .replace("%r", runtime)
        .replace("%o", owner)
        .replace("%x", suffix);
  }

  /** What the engine login itself can do to the table (proves the grant matrix, not the pin). */
  public Connection asRuntime() throws SQLException {
    return DriverManager.getConnection(adminUrl, runtime, runtimePassword);
  }

  interface Binder {
    void bind(PreparedStatement q) throws SQLException;
  }

  private void asOwner(String sql, Binder binder) throws SQLException {
    try (var c = admin()) {
      try (Statement s = c.createStatement()) {
        s.execute("SET ROLE " + owner);
      }
      try (var q = c.prepareStatement(sql)) {
        binder.bind(q);
        if (q.executeUpdate() != 1)
          throw new IllegalStateException("expected exactly one admission row: " + sql);
      }
    }
  }

  @Override
  public void close() throws Exception {
    TestNaming.bind(null);
    try (var c = admin(); var s = c.createStatement()) {
      s.execute("DROP SCHEMA IF EXISTS " + schema + " CASCADE");
      s.execute("DROP SCHEMA IF EXISTS " + sourceSchema + " CASCADE");
      // Every role this fixture (or a test) created carries the suffix: runtime, owner, writers,
      // and the membership source's owner and observer (T1.7b).
      List<String> roles = new ArrayList<>();
      try (ResultSet r = s.executeQuery("SELECT rolname FROM pg_roles WHERE rolname LIKE 'rp\\_%\\_"
               + suffix + "' ORDER BY rolname DESC")) {
        while (r.next()) roles.add(r.getString(1));
      }
      for (String role : roles) {
        s.execute("DROP OWNED BY " + role);
        s.execute("DROP ROLE IF EXISTS " + role);
      }
    }
    for (Path p : List.of(trustFile, providerFile, keysFile, dsnFile)) Files.deleteIfExists(p);
  }
}
