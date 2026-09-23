package br.com.maezo.human;

import static org.junit.jupiter.api.Assertions.*;

import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.sql.*;
import java.util.*;
import org.junit.jupiter.api.*;

/**
 * D-J.4 2.a on a real PostgreSQL (>= 17): the tables that deploy/sql/engine-native-install.sql
 * installs as owner are accepted by the engine installers' pin only while identical; a divergent
 * grant or column is refused, partial presence is refused, and an empty schema still installs.
 * Skipped (not failed) without MAEZO_NATIVE_PIN_JDBC_URL/_USER/_PASSWORD of a disposable server.
 */
class NativeCatalogPinPgTest {
  static final Path SQL = Path.of(System.getProperty("maezo.deploy.sql", "../../../../../deploy/sql"));
  static final String OWNER = "maezo_native_schema_owner", RUNTIME = "cibseven_app";
  static final List<String> LOGINS = List.of(OWNER, "maezo_native_case_issuer", "maezo_native_issuer_witness", "portal_read_source_amh");
  static final List<String> AUTH = names("mzo_auth_", "installation", "trust", "revoked_key", "input_head", "input_version",
      "guide_claim", "instance_head", "doc_occurrence", "effect_receipt");
  static final List<String> CONSUMER = names("mzo_human_consumer_", "database", "trust", "revoked", "qualification", "head",
      "pointer", "pointer_head", "link");
  String url, user, password, database;

  static List<String> names(String prefix, String... suffixes) {
    var out = new ArrayList<String>();
    for (String s : suffixes) out.add(prefix + s);
    return out;
  }

  @BeforeEach
  void setUp() throws Exception {
    url = System.getenv("MAEZO_NATIVE_PIN_JDBC_URL");
    Assumptions.assumeTrue(url != null && !url.isBlank(), "COULD NOT VERIFY: MAEZO_NATIVE_PIN_JDBC_URL");
    user = System.getenv("MAEZO_NATIVE_PIN_DB_USER");
    password = System.getenv("MAEZO_NATIVE_PIN_DB_PASSWORD");
    database = "pin_" + UUID.randomUUID().toString().replace("-", "").substring(0, 12);
    try (var c = DriverManager.getConnection(url + "postgres", user, password); var s = c.createStatement()) {
      s.execute("CREATE ROLE " + RUNTIME + " LOGIN");
      s.execute("CREATE DATABASE " + database);
    }
    try (var c = admin(); var s = c.createStatement()) {
      for (String login : LOGINS)
        s.execute("SELECT set_config('maezo.verifier." + login + "','SCRAM-SHA-256$4096:c2FsdA==$c3RvcmVk:c2VydmVy',false)");
      s.execute(read("engine-native-roles.sql"));
      s.execute("SET ROLE " + OWNER);
      s.execute(read("engine-native-install.sql"));
    }
  }

  @AfterEach
  void tearDown() throws Exception {
    if (database == null) return;
    try (var c = DriverManager.getConnection(url + "postgres", user, password); var s = c.createStatement()) {
      s.execute("DROP DATABASE IF EXISTS " + database + " WITH (FORCE)");
      var roles = new ArrayList<String>(LOGINS);
      roles.add(RUNTIME);
      try (var rs = s.executeQuery("SELECT rolname FROM pg_roles WHERE starts_with(rolname,'portal_external_')")) {
        while (rs.next()) roles.add(rs.getString(1));
      }
      for (String r : roles) s.execute("DROP ROLE IF EXISTS " + r);
    }
  }

  Connection admin() throws SQLException { return DriverManager.getConnection(url + database, user, password); }

  static String read(String name) throws Exception {
    return new String(Files.readAllBytes(SQL.resolve(name)), StandardCharsets.UTF_8);
  }

  static boolean absent(Connection c, String family, String schema, List<String> tables) {
    return NativeCatalogPin.absent(c, family, schema, tables, OWNER, RUNTIME);
  }

  @Test
  void identicalCatalogIsAcceptedAndDivergenceRefused() throws Exception {
    try (var c = admin(); var s = c.createStatement()) {
      c.setAutoCommit(false);
      s.execute("SET LOCAL ROLE " + OWNER);
      assertFalse(absent(c, NativeCatalogPin.AUTH, "maezo_native", AUTH));
      assertFalse(absent(c, NativeCatalogPin.CONSUMER, "maezo_native", CONSUMER));
      s.execute("GRANT DELETE ON maezo_native.mzo_auth_trust TO " + RUNTIME);
      assertThrows(RuntimeException.class, () -> absent(c, NativeCatalogPin.AUTH, "maezo_native", AUTH));
      c.rollback();
      s.execute("SET LOCAL ROLE " + OWNER);
      s.execute("ALTER TABLE maezo_native.mzo_human_consumer_link ADD COLUMN x_ int");
      assertThrows(RuntimeException.class, () -> absent(c, NativeCatalogPin.CONSUMER, "maezo_native", CONSUMER));
      c.rollback();
      s.execute("SET LOCAL ROLE " + OWNER);
      s.execute("DROP TABLE maezo_native.mzo_auth_effect_receipt");
      assertThrows(RuntimeException.class, () -> absent(c, NativeCatalogPin.AUTH, "maezo_native", AUTH));
      c.rollback();
      s.execute("CREATE SCHEMA empty_native");
      assertTrue(absent(c, NativeCatalogPin.AUTH, "empty_native", AUTH));
      c.rollback();
    }
  }
}
