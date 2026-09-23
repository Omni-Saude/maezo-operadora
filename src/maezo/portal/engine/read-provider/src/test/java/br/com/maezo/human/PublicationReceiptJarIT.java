package br.com.maezo.human;

import static br.com.maezo.human.readprovider.TestRecords.*;
import static org.junit.jupiter.api.Assertions.*;

import br.com.maezo.human.readprovider.ProviderFixture;
import java.nio.charset.StandardCharsets;
import java.security.Signature;
import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.SQLException;
import java.time.Instant;
import java.util.ArrayList;
import java.util.Base64;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import org.cibseven.bpm.engine.ProcessEngine;
import org.cibseven.bpm.engine.ProcessEngineConfiguration;
import org.cibseven.bpm.engine.impl.cfg.ProcessEngineConfigurationImpl;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.TestInstance;
import org.junit.jupiter.api.function.Executable;

/**
 * T1.7b readiness, end to end on a REAL engine: the {@link PortalReadPlugin} discovered through
 * {@code ServiceLoader} from the BUILT provider JAR, a CIB Seven engine on PostgreSQL 17 with the
 * shipped human and portal-read DDL, and signed {@code portal-read-publication.v1} envelopes through
 * the plugin's own publication path ({@code acquire} → {@code qualifyPublication} →
 * {@code PortalReadPublication}). A membership publication equal to the live
 * {@code portal_memberships} row gets a receipt; the same publication with one field changed is
 * refused and changes nothing. Every negative is followed by the positive on the same engine.
 */
@TestInstance(TestInstance.Lifecycle.PER_CLASS)
class PublicationReceiptJarIT {
  ProviderFixture f;
  String engineSchema;
  ProcessEngine engine;
  PortalReadPlugin plugin;
  Map<String, Object> artifact;

  @BeforeAll
  void start() throws Exception {
    f = new ProviderFixture();
    engineSchema = "rp_engine_" + f.suffix;
    String url = f.adminUrl + (f.adminUrl.contains("?") ? "&" : "?") + "currentSchema="
        + engineSchema;
    try (var c = f.admin(); var s = c.createStatement()) {
      s.execute("CREATE SCHEMA " + engineSchema);
    }
    try (Connection c = DriverManager.getConnection(url, f.adminUser, f.adminPassword);
         var s = c.createStatement()) {
      for (String ddl : List.of("human-schema-postgres.sql", "portal-read-schema-postgres.sql"))
        try (var in = PortalReadPlugin.class.getResourceAsStream("/" + ddl)) {
          s.execute(new String(Objects.requireNonNull(in, ddl).readAllBytes(),
              StandardCharsets.UTF_8));
        }
      s.execute("INSERT INTO MZO_HUMAN_TENANT VALUES('" + TENANT + "',0)");
    }
    // The staff catalog the T1.5 publisher designates: no entries, no policies, no forms.
    artifact = record("schema", "portal-read-catalog.v1", "catalog_ref", CATALOG, "publisher_ref",
        PUBLISHER, "entries", new ArrayList<>(), "policies", new ArrayList<>(), "forms",
        new ArrayList<>(), "deployment_receipt_ref", "deployment-1",
        "deployment_receipt_digest", "b".repeat(64));
    var admission = f.publicationAdmission(1);
    @SuppressWarnings("unchecked")
    var catalog = (Map<String, Object>) admission.get("catalog");
    catalog.put("catalog_digest", Jcs.digest(Jcs.canonical(artifact)));
    f.install(admission);
    plugin = new PortalReadPlugin();
    var config = (ProcessEngineConfigurationImpl) ProcessEngineConfiguration
                     .createStandaloneProcessEngineConfiguration()
                     .setProcessEngineName(f.engine)
                     .setJdbcDriver("org.postgresql.Driver")
                     .setJdbcUrl(url)
                     .setJdbcUsername(f.adminUser)
                     .setJdbcPassword(f.adminPassword)
                     .setDatabaseSchemaUpdate(ProcessEngineConfiguration.DB_SCHEMA_UPDATE_TRUE)
                     .setJobExecutorActivate(false)
                     .setHistory("none");
    config.setMetricsEnabled(false);
    config.setProcessEnginePlugins(new ArrayList<>(List.of(plugin)));
    engine = config.buildProcessEngine();
  }

  @AfterAll
  void stop() throws Exception {
    if (engine != null)
      engine.close();
    if (f != null) {
      try (var c = f.admin(); var s = c.createStatement()) {
        s.execute("DROP SCHEMA IF EXISTS " + engineSchema + " CASCADE");
      }
      f.close();
    }
  }

  Connection engineDb() throws SQLException {
    var c = f.admin();
    try (var s = c.createStatement()) {
      s.execute("SET search_path = " + engineSchema);
    }
    return c;
  }

  long authorityRevision() throws SQLException {
    try (var c = engineDb(); var s = c.createStatement();
         var r = s.executeQuery("SELECT REV_ FROM MZO_HUMAN_TENANT WHERE TENANT_='" + TENANT + "'")) {
      r.next();
      return r.getLong(1);
    }
  }

  long count(String table, String principal) throws SQLException {
    try (var c = engineDb();
         var q = c.prepareStatement("SELECT count(*) FROM " + table + " WHERE PRINCIPAL_=?")) {
      q.setString(1, principal);
      try (var r = q.executeQuery()) {
        r.next();
        return r.getLong(1);
      }
    }
  }

  /** The native human the engine checks an ACTIVE membership against (MZO_HUMAN_PRINCIPAL). */
  void nativeHuman(Member m) throws SQLException {
    try (var c = engineDb(); var q = c.prepareStatement(
             "INSERT INTO MZO_HUMAN_PRINCIPAL VALUES(?,?,?,?,1,true,?,?)")) {
      q.setString(1, TENANT);
      q.setString(2, m.principal());
      q.setString(3, m.issuer());
      q.setString(4, m.subject());
      q.setLong(5, f.until.getEpochSecond());
      q.setString(6, "[\"atendimento-humano\",\"medico-auditor\"]");
      q.executeUpdate();
    }
  }

  byte[] signed(Map<String, Object> request) throws Exception {
    long now = Instant.now().getEpochSecond();
    var e = record("schema", "portal-read-envelope.v1", "purpose", "portal-read-publication",
        "algorithm", "Ed25519", "audience", "portal-staff-read", "issuer", PUBLISHER, "tenant",
        TENANT, "key_id", "publisher", "issued_at", Long.toString(now), "expires_at",
        Long.toString(now + 30), "digest", Jcs.digest(Jcs.canonical(request)), "request",
        request);
    var sign = Signature.getInstance("Ed25519");
    sign.initSign(f.publisher.getPrivate());
    sign.update(Jcs.canonical(e));
    e.put("signature", Base64.getUrlEncoder().withoutPadding().encodeToString(sign.sign()));
    return Jcs.canonical(e);
  }

  byte[] publish(Map<String, Object> request) throws Exception {
    return plugin.execute(signed(request), ProviderFixture.PUBLISHER_PEER, "publications");
  }

  void refused(Executable action) {
    Rejected r = assertThrows(Rejected.class, action);
    assertEquals(503, r.status, r.code);
  }

  Map<String, Object> membership(Member m) throws SQLException {
    var projection = projection(m);
    return f.publication("membership",
        membershipProvenance(TENANT, projection, now().minusSeconds(1), 300), projection,
        authorityRevision());
  }

  Map<String, Object> receipt(byte[] bytes, String kind, Map<String, Object> request, long rev) {
    var receipt = Jcs.object(Jcs.parse(bytes));
    assertEquals("portal-read-publication-receipt.v1", receipt.get("schema"));
    assertEquals(kind, receipt.get("kind"));
    assertEquals(request.get("publication_id"), receipt.get("publication_id"));
    assertEquals(Long.toString(rev), receipt.get("authority_revision"));
    assertEquals(Jcs.digest(Jcs.canonical(request.get("payload"))), receipt.get("record_digest"));
    return receipt;
  }

  @Test
  void membershipEqualToTheLiveRowGetsAReceiptAndAChangedFieldIsRefused() throws Exception {
    var m = member("human-receipt", f.until.minusSeconds(120));
    f.putMembership(TENANT, m);
    nativeHuman(m);
    long before = authorityRevision();

    var changed = membership(m);
    @SuppressWarnings("unchecked")
    var payload = (Map<String, Object>) changed.get("payload");
    payload.put("audience", "provider");
    payload.put("subject_bindings",
        new ArrayList<>(List.of(record("kind", "provider", "resource_ref", "provider-1"))));
    refused(() -> publish(changed));
    assertEquals(before, authorityRevision(), "a refused publication changes nothing");
    assertEquals(0, count("MZO_PORTAL_READ_MEMBERSHIP", m.principal()));

    var request = membership(m);
    byte[] first = publish(request);
    receipt(first, "membership", request, before + 1);
    assertEquals(before + 1, authorityRevision());
    assertEquals(1, count("MZO_PORTAL_READ_MEMBERSHIP", m.principal()));
    assertArrayEquals(first, publish(request), "a lost ack replays the same receipt");
    assertEquals(before + 1, authorityRevision());
  }

  @Test
  void aRowChangedAfterThePublisherReadItIsRefusedAndTheNewRowPublishes() throws Exception {
    var m = member("human-stale", f.until.minusSeconds(120));
    f.putMembership(TENANT, m);
    nativeHuman(m);
    var stale = membership(m);
    f.putMembership(TENANT, m.revision(2));
    long before = authorityRevision();
    refused(() -> publish(stale));
    assertEquals(before, authorityRevision());
    var fresh = membership(m.revision(2));
    receipt(publish(fresh), "membership", fresh, before + 1);
  }

  @Test
  void theEmptyStaffCatalogDesignatesAndAnotherCatalogIsRefused() throws Exception {
    Instant valid = f.until.minusSeconds(3600);
    var payload = record("catalog_ref", CATALOG, "catalog_revision", "1", "catalog_digest",
        Jcs.digest(Jcs.canonical(artifact)), "catalog_artifact_base64",
        Base64.getEncoder().encodeToString(Jcs.canonical(artifact)), "deployment_receipt_ref",
        "deployment-1", "deployment_receipt_digest", "b".repeat(64), "valid_until", time(valid));
    var other = new java.util.TreeMap<>(artifact);
    other.put("deployment_receipt_ref", "deployment-2");
    var forged = new java.util.TreeMap<>(payload);
    forged.put("catalog_digest", Jcs.digest(Jcs.canonical(other)));
    forged.put("catalog_artifact_base64",
        Base64.getEncoder().encodeToString(Jcs.canonical(other)));
    forged.put("deployment_receipt_ref", "deployment-2");
    long before = authorityRevision();
    refused(() -> publish(f.publication("catalog-designate",
        plainProvenance(CATALOG + ":1", now().minusSeconds(1), valid), forged, before)));
    assertEquals(before, authorityRevision());
    var request = f.publication("catalog-designate",
        plainProvenance(CATALOG + ":1", now().minusSeconds(1), valid), payload, before);
    receipt(publish(request), "catalog-designate", request, before + 1);
  }
}
