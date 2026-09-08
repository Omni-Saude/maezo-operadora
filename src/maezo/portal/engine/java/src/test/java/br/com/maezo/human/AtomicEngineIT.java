package br.com.maezo.human;

import static org.junit.jupiter.api.Assertions.*;

import java.nio.charset.StandardCharsets;
import java.sql.*;
import java.util.*;
import java.util.concurrent.*;
import org.cibseven.bpm.engine.*;
import org.cibseven.bpm.engine.impl.cfg.*;
import org.cibseven.bpm.engine.task.Task;
import org.junit.jupiter.api.*;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;

/**
 * ROOT-only real pinned CIB 2.1.0 + external PostgreSQL. Never mocked or skipped. Fails if its
 * explicit isolated PostgreSQL configuration is absent.
 */
@Tag("integration")
@TestInstance(TestInstance.Lifecycle.PER_CLASS)
class AtomicEngineIT {
  String adminUrl, url, user, password, schema, processId, processDigest;
  ProcessEngine engine;
  ProcessEngineConfigurationImpl config;
  HumanCommandPlugin plugin;
  TestKeys keys;

  static String env(String name) {
    String v = System.getenv(name);
    if (v == null || v.isBlank())
      throw new IllegalStateException("explicit integration setting missing: " + name);
    return v;
  }

  @BeforeAll
  void start() throws Exception {
    adminUrl = env("MAEZO_HUMAN_IT_JDBC_URL");
    user = env("MAEZO_HUMAN_IT_DB_USER");
    password = env("MAEZO_HUMAN_IT_DB_PASSWORD");
    if (adminUrl.matches("(?i).*([?&])(currentSchema|options)=.*"))
      throw new IllegalStateException("integration admin URL must not override schema");
    schema = "human_it_" + UUID.randomUUID().toString().replace("-", "");
    try (var c = DriverManager.getConnection(adminUrl, user, password);
        var s = c.createStatement()) {
      s.execute("CREATE SCHEMA " + schema);
    }
    url = adminUrl + (adminUrl.contains("?") ? "&" : "?") + "currentSchema=" + schema;
    try (var c = connection();
        var s = c.createStatement();
        var in = getClass().getResourceAsStream("/human-schema-postgres.sql")) {
      s.execute(new String(Objects.requireNonNull(in).readAllBytes(), StandardCharsets.UTF_8));
      s.execute("INSERT INTO MZO_HUMAN_TENANT VALUES('tenant-test',0)");
    }
    keys = new TestKeys();
    plugin = new HumanCommandPlugin(keys.trust());
    config =
        (ProcessEngineConfigurationImpl)
            ProcessEngineConfiguration.createStandaloneProcessEngineConfiguration()
                .setProcessEngineName("human-it")
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
    byte[] xml;
    try (var in = getClass().getResourceAsStream("/synthetic-human.bpmn")) {
      xml = Objects.requireNonNull(in).readAllBytes();
    }
    var deployment =
        engine
            .getRepositoryService()
            .createDeployment()
            .tenantId("tenant-test")
            .addInputStream("synthetic-human.bpmn", new java.io.ByteArrayInputStream(xml))
            .deploy();
    var definition =
        engine
            .getRepositoryService()
            .createProcessDefinitionQuery()
            .deploymentId(deployment.getId())
            .singleResult();
    processId = definition.getId();
    processDigest = Jcs.digest(xml);
  }

  Connection connection() throws SQLException {
    Connection c = DriverManager.getConnection(url, user, password);
    if (!schema.equals(c.getSchema())) {
      c.close();
      throw new IllegalStateException("owned integration schema not active");
    }
    return c;
  }

  @AfterAll
  void stop() throws Exception {
    if (engine != null) engine.close();
    if (schema != null)
      try (var c = DriverManager.getConnection(adminUrl, user, password);
          var s = c.createStatement()) {
        s.execute("DROP SCHEMA " + schema + " CASCADE");
      }
  }

  @BeforeEach
  void reset() throws Exception {
    for (var pi : engine.getRuntimeService().createProcessInstanceQuery().list())
      engine.getRuntimeService().deleteProcessInstance(pi.getId(), "synthetic test cleanup");
    try (var c = connection();
        var s = c.createStatement()) {
      s.execute(
          "TRUNCATE"
              + " MZO_HUMAN_RECEIPT,MZO_HUMAN_EVIDENCE,MZO_HUMAN_PRINCIPAL,MZO_HUMAN_REVOKED_KEY");
      s.execute("UPDATE MZO_HUMAN_TENANT SET REV_=0");
    }
    principal("human-test", true, List.of("synthetic-reviewers"));
  }

  long revision() throws Exception {
    try (var c = connection();
        var s = c.createStatement();
        var r = s.executeQuery("SELECT REV_ FROM MZO_HUMAN_TENANT")) {
      r.next();
      return r.getLong(1);
    }
  }

  Map<String, Object> authority(String operation) throws Exception {
    var c = new TreeMap<String, Object>();
    c.put("schema", "human-authority.v1");
    c.put("tenant", "tenant-test");
    c.put("workload_ref", "publisher-test");
    c.put("operation", operation);
    c.put("expected_revision", Long.toString(revision()));
    return c;
  }

  byte[] publish(Map<String, Object> c) {
    long now = java.time.Instant.now().getEpochSecond();
    return plugin.execute(
        keys.sign(c, "human-authority", now, now + 60), keys.adminPeer, "human-authority");
  }

  void principal(String ref, boolean active, List<String> groups) throws Exception {
    var c = authority("principal");
    c.put("principal_ref", ref);
    c.put("issuer", "https://issuer.example.test/");
    c.put("subject", "subject-" + ref);
    c.put("active", active);
    c.put("valid_until", Long.toString(java.time.Instant.now().getEpochSecond() + 600));
    c.put("groups", groups);
    publish(c);
  }

  Task task() throws Exception {
    var pi = engine.getRuntimeService().startProcessInstanceById(processId);
    Task task =
        engine.getTaskService().createTaskQuery().processInstanceId(pi.getId()).singleResult();
    evidence(task);
    return engine.getTaskService().createTaskQuery().taskId(task.getId()).singleResult();
  }

  void evidence(Task task) throws Exception {
    var c = authority("evidence");
    c.put("task_id", task.getId());
    c.put("process_definition_id", processId);
    c.put("evidence_ref", "snapshot-test");
    c.put("evidence_digest", "a".repeat(64));
    c.put("valid_until", Long.toString(java.time.Instant.now().getEpochSecond() + 600));
    publish(c);
  }

  Map<String, Object> command(Task task, String operation, String commandId) throws Exception {
    var c = new TreeMap<String, Object>();
    c.put("schema", "human-command.v1");
    c.put("tenant", "tenant-test");
    c.put("task_id", task.getId());
    c.put("command_id", commandId);
    c.put("principal_ref", "human-test");
    c.put("principal_issuer", "https://issuer.example.test/");
    c.put("principal_subject", "subject-human-test");
    c.put("workload_ref", "gateway-test");
    c.put("operation", operation);
    c.put("process_definition_id", processId);
    c.put("process_definition_key", "MZO-HUMAN-SYNTHETIC");
    c.put("process_definition_version", "1");
    c.put("process_definition_digest", processDigest);
    c.put("task_definition_key", "UT_Acknowledge");
    c.put("form_key", AtomicHumanCommand.SYNTHETIC_FORM);
    c.put("form_version", "1");
    c.put("form_digest", AtomicHumanCommand.FORM_DIGEST);
    try (var con = connection();
        var s = con.prepareStatement("SELECT REV_ FROM ACT_RU_TASK WHERE ID_=?")) {
      s.setString(1, task.getId());
      try (var r = s.executeQuery()) {
        assertTrue(r.next());
        c.put("task_revision", r.getString(1));
      }
    }
    c.put("authority_revision", Long.toString(revision()));
    try (var con = connection();
        var s = con.createStatement();
        var r =
            s.executeQuery("SELECT REV_ FROM MZO_HUMAN_PRINCIPAL WHERE PRINCIPAL_='human-test'")) {
      r.next();
      c.put("membership_revision", r.getString(1));
    }
    try (var con = connection();
        var s = con.prepareStatement("SELECT REV_ FROM MZO_HUMAN_EVIDENCE WHERE TASK_=?")) {
      s.setString(1, task.getId());
      try (var r = s.executeQuery()) {
        r.next();
        c.put("evidence_revision", r.getString(1));
      }
    }
    c.put("evidence_ref", "snapshot-test");
    c.put("evidence_digest", "a".repeat(64));
    c.put(
        "assignee_ref",
        engine
            .getTaskService()
            .createTaskQuery()
            .taskId(task.getId())
            .singleResult()
            .getAssignee());
    c.put("audit_intent_ref", "synthetic-intent");
    c.put("outcome", operation.equals("decision") ? "ACK" : null);
    return c;
  }

  byte[] send(Map<String, Object> c) {
    long now = java.time.Instant.now().getEpochSecond();
    return plugin.execute(keys.sign(c, "human-command", now, now + 60), keys.peer, "human-command");
  }

  Task claimed() throws Exception {
    Task t = task();
    send(command(t, "claim", UUID.randomUUID().toString()));
    return engine.getTaskService().createTaskQuery().taskId(t.getId()).singleResult();
  }

  long countReceipts() throws Exception {
    try (var c = connection();
        var s = c.createStatement();
        var r = s.executeQuery("SELECT COUNT(*) FROM MZO_HUMAN_RECEIPT")) {
      r.next();
      return r.getLong(1);
    }
  }

  @Test
  void claimReleaseDecisionAndExactRetryHaveOneReceiptEach() throws Exception {
    Task t = task();
    var claim = command(t, "claim", "claim-1");
    byte[] receipt = send(claim);
    assertArrayEquals(receipt, send(claim));
    assertEquals(
        "human-test",
        engine.getTaskService().createTaskQuery().taskId(t.getId()).singleResult().getAssignee());
    var release = command(t, "release", "release-1");
    send(release);
    assertNull(
        engine.getTaskService().createTaskQuery().taskId(t.getId()).singleResult().getAssignee());
    send(command(t, "claim", "claim-2"));
    var decision = command(t, "decision", "decision-1");
    byte[] completed = send(decision);
    assertArrayEquals(completed, send(decision));
    assertNull(engine.getTaskService().createTaskQuery().taskId(t.getId()).singleResult());
    assertEquals(
        true,
        engine.getRuntimeService().getVariable(t.getProcessInstanceId(), "synthetic_acknowledged"));
    assertEquals(4, countReceipts());
  }

  @Test
  void conflictingCommandReuseNeverChangesWinner() throws Exception {
    Task t = task();
    var c = command(t, "claim", "same");
    byte[] winner = send(c);
    c.put("audit_intent_ref", "changed");
    assertThrows(Rejected.class, () -> send(c));
    assertEquals(1, countReceipts());
    c.put("audit_intent_ref", "synthetic-intent");
    assertArrayEquals(winner, send(c));
  }

  @ParameterizedTest
  @ValueSource(strings = {"claim", "release", "decision"})
  void receiptFailureAfterEngineFlushRollsBackAllOperations(String operation) throws Exception {
    Task t = operation.equals("claim") ? task() : claimed();
    var decision = command(t, operation, "fail-receipt");
    long before = countReceipts();
    String assigneeBefore =
        engine.getTaskService().createTaskQuery().taskId(t.getId()).singleResult().getAssignee();
    try (var c = connection();
        var s = c.createStatement()) {
      s.execute(
          "CREATE FUNCTION reject_receipt() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF"
              + " NEW.COMMAND_='fail-receipt' THEN RAISE EXCEPTION 'synthetic fault'; END IF;"
              + " RETURN NEW; END $$");
      s.execute(
          "CREATE TRIGGER receipt_fault AFTER INSERT ON MZO_HUMAN_RECEIPT FOR EACH ROW EXECUTE"
              + " FUNCTION reject_receipt()");
    }
    try {
      assertThrows(RuntimeException.class, () -> send(decision));
      assertNotNull(engine.getTaskService().createTaskQuery().taskId(t.getId()).singleResult());
      assertNull(
          engine
              .getRuntimeService()
              .getVariable(t.getProcessInstanceId(), "synthetic_acknowledged"));
      assertEquals(before, countReceipts());
      assertEquals(
          assigneeBefore,
          engine.getTaskService().createTaskQuery().taskId(t.getId()).singleResult().getAssignee());
    } finally {
      try (var c = connection();
          var s = c.createStatement()) {
        s.execute("DROP TRIGGER receipt_fault ON MZO_HUMAN_RECEIPT");
        s.execute("DROP FUNCTION reject_receipt()");
      }
    }
    send(decision);
    assertEquals(before + 1, countReceipts());
    if (operation.equals("decision"))
      assertNull(engine.getTaskService().createTaskQuery().taskId(t.getId()).singleResult());
    else
      assertEquals(
          operation.equals("claim") ? "human-test" : null,
          engine.getTaskService().createTaskQuery().taskId(t.getId()).singleResult().getAssignee());
  }

  @Test
  void staleEvidenceMembershipAssignmentAndWrongFormFailClosed() throws Exception {
    Task t = claimed();
    var stale = command(t, "decision", "stale");
    evidence(t);
    assertThrows(Rejected.class, () -> send(stale));
    var current = command(t, "decision", "current");
    principal("human-test", true, List.of("other-role"));
    assertThrows(Rejected.class, () -> send(current));
    var wrongRole = command(t, "decision", "wrong-role");
    assertThrows(Rejected.class, () -> send(wrongRole));
    principal("human-test", true, List.of("synthetic-reviewers"));
    var reassigned = command(t, "decision", "reassigned");
    engine.getTaskService().setAssignee(t.getId(), "other");
    assertThrows(Rejected.class, () -> send(reassigned));
    engine.getTaskService().setAssignee(t.getId(), "human-test");
    var form = command(t, "decision", "wrong-form");
    form.put("form_key", "auth_decisao");
    assertThrows(Rejected.class, () -> send(form));
    assertEquals(1, countReceipts());
  }

  @Test
  void revokedPrincipalCannotRetryOrReadReceipt() throws Exception {
    Task t = task();
    var c = command(t, "claim", "claim");
    send(c);
    principal("human-test", false, List.of());
    assertThrows(Rejected.class, () -> send(c));
    assertThrows(Rejected.class, () -> lookup(c, "human-test", "subject-human-test"));
    assertEquals(1, countReceipts());
  }

  byte[] lookup(Map<String, Object> command, String principal, String subject) {
    var query = new TreeMap<String, Object>();
    query.put("schema", "human-receipt-query.v1");
    query.put("tenant", "tenant-test");
    query.put("workload_ref", "gateway-test");
    query.put("task_id", command.get("task_id"));
    query.put("command_id", command.get("command_id"));
    query.put("principal_ref", principal);
    query.put("principal_issuer", "https://issuer.example.test/");
    query.put("principal_subject", subject);
    query.put("payload_digest", Jcs.digest(Jcs.canonical(command)));
    long now = java.time.Instant.now().getEpochSecond();
    return plugin.receipt(
        keys.sign(query, "human-receipt", now, now + 60),
        keys.peer,
        (String) command.get("task_id"),
        (String) command.get("command_id"));
  }

  @Test
  void receiptReadsRequireSameCurrentPrincipalAndDigest() throws Exception {
    Task t = task();
    var c = command(t, "claim", "claim");
    byte[] receipt = send(c);
    assertArrayEquals(receipt, lookup(c, "human-test", "subject-human-test"));
    principal("another-human", true, List.of("synthetic-reviewers"));
    assertThrows(Rejected.class, () -> lookup(c, "another-human", "subject-another-human"));
  }

  @Test
  void monotonicProvisioningAndKeyRevocationCannotBeReplayedOrResurrected() throws Exception {
    Task t = task();
    var c = command(t, "claim", "claim");
    var revoke = authority("revoke-key");
    revoke.put("key_fingerprint", Jcs.digest(keys.command.getPublic().getEncoded()));
    publish(revoke);
    assertThrows(Rejected.class, () -> send(c));
    assertThrows(Rejected.class, () -> publish(revoke));
    assertEquals(0, countReceipts());
    var old = authority("principal");
    old.putAll(
        Map.of(
            "principal_ref",
            "human-test",
            "issuer",
            "https://issuer.example.test/",
            "subject",
            "subject-human-test",
            "active",
            true,
            "valid_until",
            Long.toString(java.time.Instant.now().getEpochSecond() + 600),
            "groups",
            List.of("synthetic-reviewers")));
    principal("human-test", false, List.of());
    assertThrows(Rejected.class, () -> publish(old));
  }

  @Test
  void immutableIssuerSubjectCannotBeReboundByPublisher() throws Exception {
    var c = authority("principal");
    c.putAll(
        Map.of(
            "principal_ref",
            "human-test",
            "issuer",
            "https://other.example.test/",
            "subject",
            "subject-human-test",
            "active",
            true,
            "valid_until",
            Long.toString(java.time.Instant.now().getEpochSecond() + 600),
            "groups",
            List.of("synthetic-reviewers")));
    assertThrows(Rejected.class, () -> publish(c));
  }

  List<Boolean> race(Callable<?> a, Callable<?> b) throws Exception {
    var gate = new CyclicBarrier(2);
    var pool = Executors.newFixedThreadPool(2);
    try {
      var fa =
          pool.submit(
              () -> {
                gate.await();
                try {
                  a.call();
                  return true;
                } catch (RuntimeException ex) {
                  return false;
                }
              });
      var fb =
          pool.submit(
              () -> {
                gate.await();
                try {
                  b.call();
                  return true;
                } catch (RuntimeException ex) {
                  return false;
                }
              });
      return List.of(fa.get(30, TimeUnit.SECONDS), fb.get(30, TimeUnit.SECONDS));
    } finally {
      pool.shutdownNow();
    }
  }

  @Test
  void doubleClickReturnsSameReceiptAndDistinctCommandHasSingleWinner() throws Exception {
    Task t = claimed();
    var c = command(t, "decision", "decision");
    assertEquals(List.of(true, true), race(() -> send(c), () -> send(c)));
    assertEquals(2, countReceipts());
    Task other = claimed();
    var a = command(other, "decision", "a");
    var b = command(other, "decision", "b");
    var results = race(() -> send(a), () -> send(b));
    assertEquals(1, results.stream().filter(x -> x).count());
    assertEquals(4, countReceipts());
  }

  @Test
  void timerAndOtherTaskClientRacesNeverLeaveLosingReceipt() throws Exception {
    Task t = claimed();
    var c = command(t, "decision", "decision");
    var timer =
        engine
            .getManagementService()
            .createJobQuery()
            .processInstanceId(t.getProcessInstanceId())
            .singleResult();
    var results =
        race(
            () -> send(c),
            () -> {
              engine.getManagementService().executeJob(timer.getId());
              return null;
            });
    assertEquals(1, results.stream().filter(x -> x).count());
    assertNull(engine.getTaskService().createTaskQuery().taskId(t.getId()).singleResult());
    assertEquals(results.get(0) ? 2 : 1, countReceipts());
    Task other = claimed();
    var d = command(other, "decision", "other");
    long before = countReceipts();
    var clients =
        race(
            () -> send(d),
            () -> {
              engine.getTaskService().complete(other.getId());
              return null;
            });
    assertEquals(1, clients.stream().filter(x -> x).count());
    assertEquals(before + (clients.get(0) ? 1 : 0), countReceipts());
  }

  @Test
  void reassignmentRaceAndTwoReviewerClaimsHaveSingleWinner() throws Exception {
    Task t = claimed();
    var decision = command(t, "decision", "decision");
    long before = countReceipts();
    var reassignment =
        race(
            () -> send(decision),
            () -> {
              engine.getTaskService().setAssignee(t.getId(), "another-human");
              return null;
            });
    assertEquals(1, reassignment.stream().filter(x -> x).count());
    assertEquals(before + (reassignment.get(0) ? 1 : 0), countReceipts());
    principal("another-human", true, List.of("synthetic-reviewers"));
    Task other = task();
    var a = command(other, "claim", "reviewer-a");
    var b = command(other, "claim", "reviewer-b");
    b.put("principal_ref", "another-human");
    b.put("principal_subject", "subject-another-human");
    try (var con = connection();
        var st = con.createStatement();
        var r =
            st.executeQuery(
                "SELECT REV_ FROM MZO_HUMAN_PRINCIPAL WHERE PRINCIPAL_='another-human'")) {
      r.next();
      b.put("membership_revision", r.getString(1));
    }
    long claimsBefore = countReceipts();
    var claims = race(() -> send(a), () -> send(b));
    assertEquals(1, claims.stream().filter(x -> x).count());
    assertEquals(claimsBefore + 1, countReceipts());
  }

  @Test
  void productionConfigurationRefusesSyntheticDecisionActivation() throws Exception {
    Task t = claimed();
    var c = command(t, "decision", "disabled");
    var trust = keys.config();
    trust.put("enable_synthetic_fixture", false);
    long now = java.time.Instant.now().getEpochSecond();
    var rejection =
        assertThrows(
            Rejected.class,
            () ->
                config
                    .getCommandExecutorTxRequired()
                    .execute(
                        new AtomicHumanCommand(
                            new Trust(trust),
                            keys.sign(c, "human-command", now, now + 60),
                            keys.peer)));
    assertEquals("FORM_NOT_ACTIVATED", rejection.code);
    assertNotNull(engine.getTaskService().createTaskQuery().taskId(t.getId()).singleResult());
    assertEquals(1, countReceipts());
  }

  @Test
  void alreadyReassignedOrTimedOutTaskIsConflictNotSuccessorCompletion() throws Exception {
    Task t = claimed();
    var c = command(t, "decision", "decision");
    var timer =
        engine
            .getManagementService()
            .createJobQuery()
            .processInstanceId(t.getProcessInstanceId())
            .singleResult();
    engine.getManagementService().executeJob(timer.getId());
    assertThrows(Rejected.class, () -> send(c));
    assertEquals(1, countReceipts());
  }
}
