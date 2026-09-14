package br.com.maezo.human;
import static br.com.maezo.human.PortalReadModels.*;
import static org.junit.jupiter.api.Assertions.*;

import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.security.*;
import java.sql.*;
import java.time.Instant;
import java.util.*;
import javax.crypto.spec.SecretKeySpec;
import org.cibseven.bpm.engine.*;
import org.cibseven.bpm.engine.impl.cfg.*;
import org.junit.jupiter.api.*;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;

/**
 * ROOT-only real CIB2.1/PostgreSQL. Missing configuration FAILS; never a mock or skipped PASS.
 * Real immutable AUTH/ESCALATION BPMN+DMN bytes are deployed. Technical membership,
 * policy receipts and keys below are PUBLIC_SYNTHETIC, not organizational designation.
 * Fixtures use createProcessInstanceById.startBeforeActivity after ACTUAL DMN evaluation;
 * no clinical worker, decision, timer value or deployed byte is replaced.
 */
@Tag("integration")
class PortalReadEngineIT {
  Harness h;
  @BeforeEach
  void start() throws Exception {
    h = new Harness();
    h.start();
  }
  @AfterEach
  void stop() throws Exception {
    if (h != null)
      h.close();
  }
  @ParameterizedTest
  @ValueSource(ints = {0, 248, 252})
  void realMineTeamAndOriginalSnapshotContinuity(int firstByte) throws Exception {
    h.requestFirstByte = firstByte;
    String first = h.task(false), second = h.task(false), third = h.task(false);
    h.engine.getTaskService().setAssignee(first, "human-1");
    h.engine.getTaskService().setAssignee(second, "human-2");
    for (String id : List.of(first, second, third)) h.resource(id);
    var e = obj(h.read("catalog", record("anchor", h.anchor)), "value");
    var expectation = obj(e, "expectation");
    var team = obj(h.read("discover",
                       record("principal", h.principal, "expectation", expectation, "queue", "team",
                           "limit", "1", "after_task_id", null)),
        "value");
    assertEquals(2, list(team.get("task_ids")).size());
    assertEquals(list(team.get("task_ids")).get(0), team.get("after_task_id"));
    var mine = obj(h.read("discover",
                       record("principal", h.principal, "expectation", expectation, "queue", "mine",
                           "limit", "100", "after_task_id", null)),
        "value");
    assertEquals(List.of(first), mine.get("task_ids"));
    var t = obj(h.read("task", record("anchor", h.anchor, "task_id", first)), "value");
    var a = obj(h.read("authority",
                    record("anchor", h.anchor, "principal", h.principal, "task", t.get("task"),
                        "task_continuity", t.get("task_continuity"))),
        "value");
    var d = obj(h.read("disclosure",
                    record("anchor", h.anchor, "principal", h.principal, "task", t.get("task"),
                        "task_continuity", t.get("task_continuity"), "authority",
                        a.get("authority"), "authority_continuity", a.get("authority_continuity"))),
        "value");
    assertEquals(obj(t, "task").get("snapshot"), obj(d, "grant").get("snapshot"));
  }
  @Test
  void linkOnlyRevisionAndForgedContextRefuse() throws Exception {
    String id = h.task(false);
    h.resource(id);
    var t = obj(h.read("task", record("anchor", h.anchor, "task_id", id)), "value");
    try (var c = h.connection();
        var p = c.prepareStatement(
            "UPDATE ACT_RU_IDENTITYLINK SET REV_=REV_+1 WHERE TASK_ID_=? AND TYPE_='candidate'")) {
      p.setString(1, id);
      assertTrue(p.executeUpdate() > 0);
    }
    Rejected changed = assertThrows(Rejected.class,
        ()
            -> h.read("authority",
                record("anchor", h.anchor, "principal", h.principal, "task", t.get("task"),
                    "task_continuity", t.get("task_continuity"))));
    assertEquals(409, changed.status);
    var receipt = copy(obj(t, "task_continuity"));
    obj(receipt, "claims").put("snapshot_at", "2026-01-01T00:00:00.000000Z");
    assertEquals(503,
        assertThrows(Rejected.class,
            ()
                -> h.read("authority",
                    record("anchor", h.anchor, "principal", h.principal, "task", t.get("task"),
                        "task_continuity", receipt)))
            .status);
  }
  @ParameterizedTest
  @ValueSource(ints = {0, 248, 252})
  void missingProjectionCannotBecomeEmptyAndDynamicDmnUsesRealLinks(int firstByte) throws Exception {
    h.requestFirstByte = firstByte;
    String id = h.task(true);
    Rejected rejection = assertThrows(Rejected.class, () -> {
      var e = obj(obj(h.read("catalog", record("anchor", h.anchor)), "value"), "expectation");
      h.read("discover",
          record("principal", h.principal, "expectation", e, "queue", "team", "limit", "25",
              "after_task_id", null));
    });
    if (rejection.status != 503)
      throw new AssertionError("Missing projection expected 503, got " + rejection.status, rejection);
    assertEquals(503, rejection.status);
    h.resource(id);
    var t = obj(obj(h.read("task", record("anchor", h.anchor, "task_id", id)), "value"), "task");
    assertEquals(
        List.of("atendimento-humano"), obj(t, "snapshot").get("eligible_candidate_groups"));
  }
  @Test
  void completeDenyAndSplitMembershipCannotGrant() throws Exception {
    String id = h.task(false);
    h.resource(id);
    var t = obj(h.read("task", record("anchor", h.anchor, "task_id", id)), "value");
    var original = copy(h.principal);
    h.principal.put("memberships",
        List.of(record("membership_ref", "a", "roles", List.of("technical-fixture-role"), "groups",
                    List.of()),
            record("membership_ref", "b", "roles", List.of("different-role"), "groups",
                List.of("medico-auditor", "atendimento-humano"))));
    h.principal.put("membership_revision", "2");
    h.membership();
    h.resource(id); // Keep the ACL membership revision current; only tuple splitting must deny.
    t = obj(h.read("task", record("anchor", h.anchor, "task_id", id)), "value");
    var exact = t;
    assertEquals(404,
        assertThrows(Rejected.class,
            ()
                -> h.read("authority",
                    record("anchor", h.anchor, "principal", h.principal, "task", exact.get("task"),
                        "task_continuity", exact.get("task_continuity"))))
            .status);
  }

  @Test
  void currentAdmissionLossRefusesBeforeTaskExistence() throws Exception {
    h.invalidate = true;
    assertEquals(503,
        assertThrows(Rejected.class,
            () -> h.read("task", record("anchor", h.anchor, "task_id", "not-existing")))
            .status);
  }

  @Test
  void contributingAssigneeAndMultipleUserCandidatesKeepOriginalMinimum() throws Exception {
    String id = h.task(false);
    Instant minimum = Instant.now().plusSeconds(20).truncatedTo(java.time.temporal.ChronoUnit.SECONDS);
    h.nativeIdentity("human-2", minimum.plusSeconds(15), 2);
    h.nativeIdentity("human-3", minimum, 3);
    h.nativeIdentity("human-4", minimum.plusSeconds(10), 4);
    h.engine.getTaskService().setAssignee(id, "human-2");
    h.engine.getTaskService().addCandidateUser(id, "human-3");
    h.engine.getTaskService().addCandidateUser(id, "human-4");
    h.resource(id);
    var e = obj(obj(h.read("catalog", record("anchor", h.anchor)), "value"), "expectation");
    var discovery = h.read("discover", record("principal", h.principal, "expectation", e,
        "queue", "team", "limit", "25", "after_task_id", null));
    assertEquals(minimum, time(discovery.get("valid_until")));
    var t = obj(h.read("task", record("anchor", h.anchor, "task_id", id)), "value");
    assertEquals(minimum, time(obj(t,"task").get("valid_until")));
    var a = obj(h.read("authority", record("anchor", h.anchor, "principal", h.principal,
        "task", t.get("task"), "task_continuity", t.get("task_continuity"))), "value");
    assertEquals(minimum, time(obj(a,"authority").get("valid_until")));
    var d = obj(h.read("disclosure", record("anchor", h.anchor, "principal", h.principal,
        "task", t.get("task"), "task_continuity", t.get("task_continuity"),
        "authority", a.get("authority"), "authority_continuity", a.get("authority_continuity"))), "value");
    assertEquals(minimum, time(obj(d,"grant").get("valid_until")));
  }
  @Test
  void identityOnlyRenewalRefusesOldContinuityButFreshTaskStillWorks() throws Exception {
    String id = h.task(false);
    Instant oldEnd = Instant.now().plusSeconds(15).truncatedTo(java.time.temporal.ChronoUnit.SECONDS);
    h.nativeIdentity("human-2", oldEnd, 2);
    h.engine.getTaskService().setAssignee(id, "human-2"); h.resource(id);
    var t = obj(h.read("task", record("anchor", h.anchor, "task_id", id)), "value");
    h.nativeIdentity("human-2", oldEnd.plusSeconds(40), 3);
    assertEquals(409, assertThrows(Rejected.class, () -> h.read("authority",
        record("anchor", h.anchor, "principal", h.principal, "task", t.get("task"),
            "task_continuity", t.get("task_continuity")))).status);
    var fresh = obj(h.read("task", record("anchor", h.anchor, "task_id", id)), "value");
    assertEquals(obj(t,"task").get("snapshot"),
        recordWithOriginalSnapshotTime(obj(fresh,"task"), obj(t,"task")));
    assertNotEquals(obj(obj(t,"task_continuity"),"claims").get("native_task_state_digest"),
        obj(obj(fresh,"task_continuity"),"claims").get("native_task_state_digest"));
  }
  static Map<String,Object> recordWithOriginalSnapshotTime(Map<String,Object> newer, Map<String,Object> older) {
    var snapshot = copy(obj(newer,"snapshot")); snapshot.put("snapshot_at",obj(older,"snapshot").get("snapshot_at"));
    return snapshot;
  }
  @Test
  void contributingIdentityExpiryGuardsRealCommittedReturnAndContinuations() throws Exception {
    String id = h.task(false);
    Instant deadline = Instant.now().plusSeconds(8).truncatedTo(java.time.temporal.ChronoUnit.SECONDS);
    h.nativeIdentity("human-2", deadline, 2);
    h.engine.getTaskService().setAssignee(id, "human-2"); h.resource(id);
    var retained = h.retainedTask(id);
    var t = obj(map(Jcs.parse(retained.bytes())), "value");
    var a = obj(h.read("authority", record("anchor", h.anchor, "principal", h.principal,
        "task", t.get("task"), "task_continuity", t.get("task_continuity"))), "value");
    long wait = java.time.Duration.between(Instant.now(), deadline).toMillis()+10;
    if (wait > 0) Thread.sleep(wait);
    assertEquals(503, assertThrows(Rejected.class, retained::bytes).status);
    assertEquals(503, assertThrows(Rejected.class, () -> h.read("authority", record("anchor", h.anchor,
        "principal", h.principal, "task", t.get("task"), "task_continuity", t.get("task_continuity")))).status);
    assertEquals(503, assertThrows(Rejected.class, () -> h.read("disclosure", record("anchor", h.anchor,
        "principal", h.principal, "task", t.get("task"), "task_continuity", t.get("task_continuity"),
        "authority", a.get("authority"), "authority_continuity", a.get("authority_continuity")))).status);
  }

  static final class Harness implements AutoCloseable {
    String admin, url, user, password, schema, authDefinition, dynamicDefinition;
    ProcessEngine engine;
    ProcessEngineConfigurationImpl config;
    PortalReadPlugin plugin;
    PortalReadTrust trust;
    Map<String, Object> scope =
        record("tenant", "tenant-test", "environment", "test", "workload_ref", "read-workload");
    Map<String, Object> anchor =
        record("scope", scope, "catalog_ref", "catalog", "publisher_ref", "publisher-workload");
    Map<String, Object> principal;
    Map<String, Object> artifact;
    Map<String, Map<String, Object>> sourceFacts = new HashMap<>();
    KeyPair readKey, publisherKey;
    String readPeer = "a".repeat(64), publisherPeer = "b".repeat(64),
           context = Base64.getUrlEncoder().withoutPadding().encodeToString(new byte[32]);
    Instant before, until;
    long sourceRevision = 0;
    boolean invalidate = false;
    Integer requestFirstByte;
    Runnable finalHook = () -> {};
    static String required(String key) {
      String value = System.getenv(key);
      if (value == null || value.isBlank())
        throw new IllegalStateException("explicit Q2 integration configuration missing");
      return value;
    }
    void start() throws Exception {
      admin = required("MAEZO_HUMAN_IT_JDBC_URL");
      user = required("MAEZO_HUMAN_IT_DB_USER");
      password = required("MAEZO_HUMAN_IT_DB_PASSWORD");
      if (admin.matches("(?i).*([?&])(currentSchema|options)=.*"))
        throw new IllegalStateException("owned schema required");
      schema = "portal_read_it_" + UUID.randomUUID().toString().replace("-", "");
      try (var c = DriverManager.getConnection(admin, user, password);
          var s = c.createStatement()) {
        s.execute("CREATE SCHEMA " + schema);
      }
      url = admin + (admin.contains("?") ? "&" : "?") + "currentSchema=" + schema;
      try (var c = connection(); var s = c.createStatement()) {
        for (String name : List.of("human-schema-postgres.sql", "portal-read-schema-postgres.sql"))
          try (var in = getClass().getResourceAsStream("/" + name)) {
            s.execute(
                new String(Objects.requireNonNull(in).readAllBytes(), StandardCharsets.UTF_8));
          }
        s.execute("INSERT INTO MZO_HUMAN_TENANT VALUES('tenant-test',0)");
      }
      before = Instant.now().minusSeconds(2).truncatedTo(java.time.temporal.ChronoUnit.MICROS);
      until = before.plusSeconds(600);
      var generator = KeyPairGenerator.getInstance("Ed25519");
      readKey = generator.generateKeyPair();
      publisherKey = generator.generateKeyPair();
      var trustRecord = record("schema", "portal-read-trust.v1", "scope", scope, "engine_name",
          "read-it", "database_incarnation", schema, "audience", "read-it", "read_deployment_ref",
          "release-it", "read_deployment_digest", "c".repeat(64), "validity_policy_ref",
          "PUBLIC_SYNTHETIC", "validity_policy_digest", "d".repeat(64), "max_envelope_seconds",
          "60", "public_keys",
          List.of(key("reader", "portal-task-read", "read-workload", readKey, readPeer),
              key("publisher", "portal-read-publication", "publisher-workload", publisherKey,
                  publisherPeer)));
      trust = PortalReadTrust.configured(trustRecord, providers());
      plugin = new PortalReadPlugin(trust);
      config = (ProcessEngineConfigurationImpl) ProcessEngineConfiguration
                   .createStandaloneProcessEngineConfiguration()
                   .setProcessEngineName("read-it")
                   .setJdbcDriver("org.postgresql.Driver")
                   .setJdbcUrl(url)
                   .setJdbcUsername(user)
                   .setJdbcPassword(password)
                   .setDatabaseSchemaUpdate(ProcessEngineConfiguration.DB_SCHEMA_UPDATE_TRUE)
                   .setJobExecutorActivate(false)
                   .setHistory("full");
      config.setEnforceHistoryTimeToLive(false);
      config.setMetricsEnabled(false);
      config.setProcessEnginePlugins(List.of(plugin));
      engine = config.buildProcessEngine();
      Path repo = Path.of(required("MAEZO_PORTAL_READ_IT_REPO"));
      var deploy = engine.getRepositoryService().createDeployment().tenantId("tenant-test");
      for (String name : List.of("bpmn/SP-OP-AUTH-001_Autorizacao_Previa.bpmn",
               "bpmn/SP-OP-ESCALATION-001_Escalonamento_Humano_Universal.bpmn", "dmn/auth_sla.dmn",
               "dmn/escalation_routing.dmn"))
        deploy.addInputStream(name,
            new java.io.ByteArrayInputStream(
                Files.readAllBytes(repo.resolve("spec/processes").resolve(name))));
      var deployed = deploy.deploy();
      authDefinition = engine.getRepositoryService()
                           .createProcessDefinitionQuery()
                           .deploymentId(deployed.getId())
                           .processDefinitionKey("SP-OP-AUTH-001")
                           .singleResult()
                           .getId();
      dynamicDefinition = engine.getRepositoryService()
                              .createProcessDefinitionQuery()
                              .deploymentId(deployed.getId())
                              .processDefinitionKey("SP-OP-ESCALATION-001")
                              .singleResult()
                              .getId();
      principal = record("schema_version", "1", "principal_ref", "human-1", "issuer",
          "https://issuer.example", "subject", "subject-human-1", "tenant", "tenant-test",
          "membership_revision", "1", "memberships",
          List.of(record("membership_ref", "technical-fixture-membership", "roles",
              List.of("technical-fixture-role"), "groups",
              List.of("atendimento-humano", "medico-auditor"))),
          "session_ref", "session-it", "authenticated_at", time(before), "subject_bindings",
          List.of());
      try (var c = connection();
          var s = c.prepareStatement("INSERT INTO MZO_HUMAN_PRINCIPAL "
              + "VALUES('tenant-test',?,'https://issuer.example',?,1,true,?,?)")) {
        for (String who : List.of("human-1", "human-2")) {
          s.setString(1, who);
          s.setString(2, "subject-" + who);
          s.setLong(3, until.getEpochSecond());
          s.setString(4, "[\"atendimento-humano\",\"medico-auditor\"]");
          s.executeUpdate();
        }
      }
      catalog(deployed.getId());
      membership();
    }
    Map<String, Object> key(String id, String purpose, String workload, KeyPair pair, String peer) {
      var m = record("key_id", id, "purpose", purpose, "workload_ref", workload, "peer_spki_sha256",
          peer, "public_key_spki_base64",
          Base64.getEncoder().encodeToString(pair.getPublic().getEncoded()), "not_before",
          time(before), "not_after", time(until));
      if (purpose.equals("portal-read-publication")) {
        m.put("publication_kinds", new ArrayList<>(KINDS));
        m.put("catalog_ref", "catalog");
      }
      return m;
    }
    PortalReadTrust.Providers providers() {
      return new PortalReadTrust.Providers() {
        public PortalReadTrust.Admission acquire(Map<String, Object> s, String e, String i,
            String d, String hash, String configurationDigest, String purpose) {
          if (!scope.get("tenant").equals(s.get("tenant")) || !schema.equals(i)
              || !e.equals("read-it") || !d.equals("release-it")
              || (trust != null && !trust.configurationDigest.equals(configurationDigest)))
            throw unavailable();
          return new PortalReadTrust.Admission() {
            public String generation() {
              return "1";
            }
            public String providerRef() {
              return "PUBLIC_SYNTHETIC-admission";
            }
            public String providerRevision() {
              return "1";
            }
            public String capabilityDigest() {
              return "e".repeat(64);
            }
            public Instant observedAt() {
              return before;
            }
            public Instant validUntil() {
              return until;
            }
            public int statementTimeoutSeconds() {
              return 10;
            }
            public void requireCurrent() {
              if (invalidate)
                throw unavailable();
              finalHook.run();
            }
            public void verifySource(String kind, Map<String, Object> source) {
              if (!sourceFacts.containsKey(str(source, "receipt_ref")))
                throw unavailable();
            }
            public void verifyCatalog(Map<String, Object> a) {
              if (artifact == null || !artifact.equals(a))
                throw unavailable();
            }
            public void verifyIdentityPolicy(
                Map<String, Object> p, Map<String, Object> t, List<Object> links) {
              if (!p.equals(pin()))
                throw unavailable();
            }
            public void verifyClassification(Map<String, Object> c, Map<String, Object> e) {
              if (!c.get("policy_digest").equals(pin().get("digest"))
                  || !c.get("fields_digest").equals("f".repeat(64)))
                throw unavailable();
            }
          };
        }
        public PortalReadTrust.NativeKeySet continuity(PortalReadTrust.Admission a) {
          var key = new PortalReadTrust.NativeKey("native-test", "1", "e".repeat(64), before, until,
              new SecretKeySpec(new byte[32], "HmacSHA256"), () -> {
                if (invalidate)
                  throw unavailable();
              });
          return new PortalReadTrust.NativeKeySet() {
            public PortalReadTrust.NativeKey current() {
              return key;
            }
            public PortalReadTrust.NativeKey verification(String id) {
              return id.equals(key.id) ? key : null;
            }
            public void requireCurrent() {
              if (invalidate)
                throw unavailable();
            }
          };
        }
        public PortalReadTrust.PublicationQualification qualifyPublication(
            PortalReadTrust.Admission a, Map<String, Object> request) {
          return new PortalReadTrust.PublicationQualification() {
            public void verify(
                String kind, Map<String, Object> source, Map<String, Object> payload) {
              if (!payload.equals(sourceFacts.get(str(source, "receipt_ref"))))
                throw unavailable();
            }
            public void requireCurrent() {
              if (invalidate)
                throw unavailable();
            }
            public Instant validUntil() {
              return until;
            }
          };
        }
      };
    }
    Connection connection() throws SQLException {
      var c = DriverManager.getConnection(url, user, password);
      if (!schema.equals(c.getSchema()))
        throw new SQLException("owned schema mismatch");
      return c;
    }
    long revision() throws Exception {
      try (var c = connection(); var s = c.createStatement();
          var r = s.executeQuery("SELECT REV_ FROM MZO_HUMAN_TENANT")) {
        r.next();
        return r.getLong(1);
      }
    }
    byte[] signed(Map<String, Object> request, boolean publication) throws Exception {
      long now = Instant.now().getEpochSecond();
      var key = publication ? publisherKey : readKey;
      var e = record("schema", "portal-read-envelope.v1", "purpose",
          publication ? "portal-read-publication" : "portal-task-read", "algorithm", "Ed25519",
          "audience", "read-it", "issuer", publication ? "publisher-workload" : "read-workload",
          "tenant", "tenant-test", "key_id", publication ? "publisher" : "reader", "issued_at",
          Long.toString(now), "expires_at", Long.toString(now + 30), "digest", hash(request),
          "request", request);
      var sign = Signature.getInstance("Ed25519");
      sign.initSign(key.getPrivate());
      sign.update(Jcs.canonical(e));
      e.put("signature", Base64.getUrlEncoder().withoutPadding().encodeToString(sign.sign()));
      return bounded(e);
    }
    Map<String, Object> common() {
      return record("scope", scope, "engine_name", "read-it", "database_incarnation", schema,
          "read_deployment_ref", "release-it", "read_deployment_digest", "c".repeat(64));
    }
    Map<String, Object> read(String operation, Map<String, Object> extras) throws Exception {
      var r = common();
      byte[] nonce = SecureRandom.getSeed(32);
      if (requestFirstByte != null)
        nonce[0] = requestFirstByte.byteValue();
      r.putAll(record("schema", "portal-engine-read.v1", "operation", operation, "request_id",
          Base64.getUrlEncoder().withoutPadding().encodeToString(nonce),
          "read_context_id", context));
      r.putAll(extras);
      return map(Jcs.parse(plugin.execute(signed(r, false), readPeer, operation)));
    }
    Map<String, Object> publication(String kind, Map<String, Object> payload) throws Exception {
      var r = common();
      r.put("scope",
          record("tenant", "tenant-test", "environment", "test", "workload_ref",
              "publisher-workload"));
      String id = "publication-" + (++sourceRevision);
      sourceFacts.put(id, copy(payload));
      r.putAll(record("schema", "portal-read-publication.v1", "publication_id", id,
          "expected_authority_revision", Long.toString(revision()), "source",
          record("publisher_ref", "publisher-workload", "source_ref", "source-" + kind,
              "source_revision", Long.toString(sourceRevision), "source_digest", hash(payload),
              "receipt_ref", id, "observed_at", time(before), "valid_until", time(until)),
          "kind", kind, "payload", payload));
      return r;
    }
    Map<String, Object> publish(String kind, Map<String, Object> payload) throws Exception {
      return map(Jcs.parse(
          plugin.execute(signed(publication(kind, payload), true), publisherPeer, "publications")));
    }
    Map<String, Object> pin() {
      return record("artifact_ref", "PUBLIC_SYNTHETIC-policy", "digest",
          Jcs.digest("PUBLIC_SYNTHETIC technical policy".getBytes(StandardCharsets.UTF_8)));
    }
    void catalog(String deployment) throws Exception {
      var entries = new ArrayList<Object>();
      var forms = new ArrayList<Object>();
      for (boolean dynamic : List.of(false, true)) {
        String id = dynamic ? dynamicDefinition : authDefinition;
        var def = engine.getRepositoryService()
                      .createProcessDefinitionQuery()
                      .processDefinitionId(id)
                      .singleResult();
        String task = dynamic ? "UT_TratarEscalonamento" : "UT_AnaliseMedicoAuditor";
        var compiled = PortalReadCommand.COMPILED.get(def.getKey() + "/" + task);
        byte[] form =
            ("PUBLIC_SYNTHETIC read form " + compiled.get(0)).getBytes(StandardCharsets.UTF_8);
        var domain = record("kind", "static", "groups", List.of("medico-auditor"),
            "dmn_definition_id", null, "dmn_definition_key", null, "dmn_definition_version", null,
            "dmn_resource_digest", null);
        if (dynamic) {
          var d = engine.getRepositoryService()
                      .createDecisionDefinitionQuery()
                      .decisionDefinitionKey("escalation_routing")
                      .singleResult();
          domain = record("kind", "dmn", "groups",
              List.of("plantao-clinico", "enfermagem-triagem", "atendimento-humano"),
              "dmn_definition_id", d.getId(), "dmn_definition_key", d.getKey(),
              "dmn_definition_version", Integer.toString(d.getVersion()), "dmn_resource_digest",
              Jcs.digest(engine.getRepositoryService().getDecisionModel(d.getId()).readAllBytes()));
        }
        var entry = record("process_definition_id", id, "process_definition_key", def.getKey(),
            "process_definition_version", Integer.toString(def.getVersion()),
            "process_definition_digest",
            Jcs.digest(engine.getRepositoryService().getProcessModel(id).readAllBytes()),
            "task_definition_key", task, "form_key", compiled.get(0), "form_version", "1",
            "form_digest", Jcs.digest(form), "form_source_status", compiled.get(1),
            "allowed_inputs", compiled.subList(2, compiled.size()), "required_roles",
            List.of("technical-fixture-role"), "subject_policy", pin(), "consent_policy", pin(),
            "resource_policy", pin(), "disclosure_policy", pin(), "opaque_task_id_policy", pin(),
            "group_domain", domain);
        entries.add(entry);
        forms.add(record("artifact_ref", compiled.get(0), "digest", Jcs.digest(form),
            "bytes_base64", Base64.getEncoder().encodeToString(form)));
      }
      artifact = record("schema", "portal-read-catalog.v1", "catalog_ref", "catalog",
          "publisher_ref", "publisher-workload", "entries", entries, "policies",
          List.of(record("artifact_ref", pin().get("artifact_ref"), "digest", pin().get("digest"),
              "bytes_base64",
              Base64.getEncoder().encodeToString(
                  "PUBLIC_SYNTHETIC technical policy".getBytes(StandardCharsets.UTF_8)))),
          "forms", forms, "deployment_receipt_ref", deployment, "deployment_receipt_digest",
          hash(entries));
      publish("catalog-designate",
          record("catalog_ref", "catalog", "catalog_revision", "1", "catalog_digest",
              hash(artifact), "catalog_artifact_base64",
              Base64.getEncoder().encodeToString(bounded(artifact)), "deployment_receipt_ref",
              deployment, "deployment_receipt_digest", hash(entries), "valid_until", time(until)));
    }
    void nativeIdentity(String who, Instant valid, long revision) throws Exception {
      try (var c = connection(); var p = c.prepareStatement(
          "INSERT INTO MZO_HUMAN_PRINCIPAL VALUES('tenant-test',?,'https://issuer.example',?,?,true,?,?) "
          + "ON CONFLICT(TENANT_,PRINCIPAL_) DO UPDATE SET REV_=excluded.REV_,VALID_UNTIL_=excluded.VALID_UNTIL_")) {
        p.setString(1,who); p.setString(2,"subject-"+who); p.setLong(3,revision);
        p.setLong(4,valid.getEpochSecond()); p.setString(5,"[\"atendimento-humano\",\"medico-auditor\"]");
        assertEquals(1,p.executeUpdate());
      }
    }
    PortalReadCommand.Result retainedTask(String id) throws Exception {
      var r = common(); r.putAll(record("schema","portal-engine-read.v1","operation","task",
          "request_id",Base64.getUrlEncoder().withoutPadding().encodeToString(SecureRandom.getSeed(32)),
          "read_context_id",context,"anchor",anchor,"task_id",id));
      var verified = PortalReadEnvelope.verify(signed(r,false),trust,"portal-task-read",readPeer,Instant.now());
      var a = trust.acquire("portal-task-read",obj(verified.request(),"scope"));
      var keys = trust.providers.continuity(a); keys.requireCurrent();
      return config.getCommandExecutorTxRequired().execute(new PortalReadCommand(trust,verified,a,keys));
    }
    void membership() throws Exception {
      var p = record("audience", "staff", "state", "active", "reviewed_until", time(until));
      for (String k : List.of("principal_ref", "issuer", "subject", "membership_revision",
               "memberships", "subject_bindings"))
        p.put(k, principal.get(k));
      publish("membership", p);
    }
    String task(boolean dynamic) throws Exception {
      String definition = dynamic ? dynamicDefinition : authDefinition;
      String key = dynamic ? "escalation_routing" : "auth_sla";
      var vars = dynamic
          ? Map.<String, Object>of("motivo_categoria", "falha_tecnica", "severidade", "leve")
          : Map.<String, Object>of(
                "carater_atendimento", "eletivo", "categoria_procedimento", "exame");
      var decision = engine.getRepositoryService()
                         .createDecisionDefinitionQuery()
                         .decisionDefinitionKey(key)
                         .singleResult();
      var result = engine.getDecisionService()
                       .evaluateDecisionTableById(decision.getId(), vars)
                       .getSingleResult()
                       .getEntryMap();
      var pi =
          engine.getRuntimeService()
              .createProcessInstanceById(definition)
              .setVariable(dynamic ? "roteamento" : "sla", new HashMap<>(result))
              .startBeforeActivity(dynamic ? "UT_TratarEscalonamento" : "UT_AnaliseMedicoAuditor")
              .execute();
      return engine.getTaskService()
          .createTaskQuery()
          .processInstanceId(pi.getId())
          .singleResult()
          .getId();
    }
    Map<String, Object> resourcePayload(String id) throws Exception {
      var task = engine.getTaskService().createTaskQuery().taskId(id).singleResult();
      long revision = config.getCommandExecutorTxRequired().execute(
          c -> (long) c.getTaskManager().findTaskById(id).getRevision());
      var entry =
          list(artifact.get("entries"))
              .stream()
              .map(PortalReadModels::map)
              .filter(e -> e.get("process_definition_id").equals(task.getProcessDefinitionId()))
              .findFirst()
              .orElseThrow();
      try (var c = connection();
          var p = c.prepareStatement(
              "INSERT INTO MZO_HUMAN_EVIDENCE VALUES('tenant-test',?,1,'evidence','"
              + "a".repeat(64) + "',?,?) ON CONFLICT(TENANT_,TASK_) DO NOTHING")) {
        p.setString(1, id);
        p.setLong(2, until.getEpochSecond());
        p.setString(3, task.getProcessDefinitionId());
        p.executeUpdate();
      }
      return record("task_id", id, "process_definition_id", task.getProcessDefinitionId(),
          "process_definition_digest", entry.get("process_definition_digest"),
          "observed_task_revision", Long.toString(revision), "evidence_ref", "evidence",
          "evidence_revision", "1", "evidence_digest", "a".repeat(64), "resource_ref",
          "resource-" + id, "resource_revision", "1", "resource_digest", "b".repeat(64),
          "resource_policy", pin(), "classification",
          record("classification_ref", "PUBLIC_SYNTHETIC-classification", "classification_digest",
              "c".repeat(64), "policy_ref", pin().get("artifact_ref"), "policy_digest",
              pin().get("digest"), "projection", "full_task_detail.v1", "fields_digest",
              "f".repeat(64), "valid_until", time(until)),
          "required_subject_bindings", List.of(), "required_consent_scopes", List.of(),
          "positive_grants",
          List.of(record("issuer", principal.get("issuer"), "subject", principal.get("subject"),
              "principal_ref", principal.get("principal_ref"), "membership_revision",
              principal.get("membership_revision"), "consent_scopes", List.of(),
              "decision_receipt_ref", "PUBLIC_SYNTHETIC-resource-decision", "decision_digest",
              "d".repeat(64), "valid_until", time(until))),
          "read_only_evidence", null, "state", "complete", "valid_until", time(until));
    }
    void resource(String id) throws Exception {
      publish("resource", resourcePayload(id));
    }
    public void close() throws Exception {
      if (engine != null)
        engine.close();
      if (schema != null)
        try (var c = DriverManager.getConnection(admin, user, password);
            var s = c.createStatement()) {
          s.execute("DROP SCHEMA " + schema + " CASCADE");
        }
    }
  }
}
