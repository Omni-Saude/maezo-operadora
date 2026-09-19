package br.com.maezo.human;

import static org.junit.jupiter.api.Assertions.*;

import java.sql.Connection;
import java.time.Instant;
import java.util.*;
import java.util.concurrent.*;
import org.cibseven.bpm.engine.task.Task;
import org.junit.jupiter.api.Tag;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;

/** PAE-V2 ROOT-only real PostgreSQL propagation probes; select these methods explicitly. */
@Tag("integration")
class FreshnessEngineIT extends AtomicEngineIT {
  Map<String, Object> principalPublication(long until) throws Exception {
    var p = authority("principal");
    p.putAll(Map.of("principal_ref", "human-test", "issuer", "https://issuer.example.test/",
        "subject", "subject-human-test", "active", true, "valid_until", Long.toString(until),
        "groups", List.of("synthetic-reviewers")));
    return p;
  }

  void expirePrincipalOrEvidence(String kind, Task task, long deadline) throws Exception {
    if (kind.equals("principal")) publish(principalPublication(deadline));
    else if (kind.equals("evidence")) {
      var e = authority("evidence");
      e.putAll(Map.of("task_id", task.getId(), "process_definition_id", processId,
          "evidence_ref", "snapshot-test", "evidence_digest", "a".repeat(64),
          "valid_until", Long.toString(deadline)));
      publish(e);
    }
  }

  int pid(Connection blocker) throws Exception {
    try (var s = blocker.createStatement(); var r = s.executeQuery("SELECT pg_backend_pid()")) {
      assertTrue(r.next());
      return r.getInt(1);
    }
  }

  void assertExpiredAfterObservedWait(Connection blocker, int blockerPid, Future<byte[]> pending,
      String sqlPrefix, long deadline, int status) throws Exception {
    boolean observed = false;
    long limit = System.nanoTime() + TimeUnit.SECONDS.toNanos(2);
    while (!observed && System.nanoTime() < limit) {
      try (var c = connection(); var s = c.prepareStatement(
          "SELECT COUNT(*) FROM pg_stat_activity WHERE ? = ANY(pg_blocking_pids(pid)) AND query LIKE ?")) {
        s.setInt(1, blockerPid);
        s.setString(2, sqlPrefix + "%");
        try (var r = s.executeQuery()) {
          assertTrue(r.next());
          observed = r.getLong(1) > 0;
        }
      }
      if (!observed) Thread.sleep(10);
    }
    assertTrue(observed, "Must observe the actual intended SQL blocked before expiry");
    while (Instant.now().getEpochSecond() <= deadline) Thread.sleep(20);
    blocker.commit();
    var failure = assertThrows(ExecutionException.class, () -> pending.get(10, TimeUnit.SECONDS));
    var refusal = assertInstanceOf(Rejected.class, failure.getCause());
    assertEquals(status, refusal.status, "Reject expired authority, not an unrelated SQL failure");
  }

  @ParameterizedTest
  @ValueSource(strings = {"retry-envelope", "retry-principal", "lookup-envelope", "lookup-principal"})
  void receiptAccessCannotOutliveAuthorityDuringReceiptRead(String scenario) throws Exception {
    Task t = task();
    var command = command(t, "claim", "previously-committed");
    byte[] committed = send(command);
    String[] parts = scenario.split("-");
    long deadline = Instant.now().getEpochSecond() + 4;
    expirePrincipalOrEvidence(parts[1], t, deadline);
    var payload = command;
    String purpose = "human-command";
    if (parts[0].equals("lookup")) {
      payload = new TreeMap<>();
      payload.putAll(Map.of("schema", "human-receipt-query.v1", "tenant", "tenant-test",
          "workload_ref", "gateway-test", "task_id", t.getId(), "command_id", command.get("command_id"),
          "principal_ref", "human-test", "principal_issuer", "https://issuer.example.test/",
          "principal_subject", "subject-human-test", "payload_digest", Jcs.digest(Jcs.canonical(command))));
      purpose = "human-receipt";
    }
    long now = Instant.now().getEpochSecond();
    byte[] raw = keys.sign(payload, purpose, now, parts[1].equals("envelope") ? deadline : now + 60);
    var pool = Executors.newSingleThreadExecutor();
    try (var blocker = connection()) {
      blocker.setAutoCommit(false);
      int blockerPid = pid(blocker);
      try (var s = blocker.createStatement()) {
        s.execute("LOCK TABLE MZO_HUMAN_RECEIPT IN ACCESS EXCLUSIVE MODE");
      }
      var pending = pool.submit(() -> parts[0].equals("retry")
          ? plugin.execute(raw, keys.peer, "human-command")
          : plugin.receipt(raw, keys.peer, t.getId(), (String) command.get("command_id")));
      assertExpiredAfterObservedWait(blocker, blockerPid, pending,
          "SELECT * FROM MZO_HUMAN_RECEIPT", deadline, 403);
    } finally {
      pool.shutdownNow();
      assertTrue(pool.awaitTermination(15, TimeUnit.SECONDS));
    }
    assertEquals(1, countReceipts());
    assertEquals("human-test", engine.getTaskService().createTaskQuery().taskId(t.getId()).singleResult().getAssignee());
    principal("human-test", true, List.of("synthetic-reviewers"));
    assertArrayEquals(committed, send(command), "Renewal still permits exact replay with old membership/evidence");
    assertArrayEquals(committed, lookup(command, "human-test", "subject-human-test"));
  }

  @ParameterizedTest
  @ValueSource(strings = {"envelope", "principal", "evidence"})
  void receiptInsertWaitCannotOutliveDecisionAuthority(String kind) throws Exception {
    Task t = task();
    long deadline = Instant.now().getEpochSecond() + 4;
    expirePrincipalOrEvidence(kind, t, deadline);
    var command = command(t, "claim", "late-receipt");
    long metrics = taskMetricCount();
    long now = Instant.now().getEpochSecond();
    byte[] raw = keys.sign(command, "human-command", now, kind.equals("envelope") ? deadline : now + 60);
    long lock = UUID.randomUUID().getMostSignificantBits();
    try (var c = connection(); var s = c.createStatement()) {
      s.execute("CREATE FUNCTION hold_receipt() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN PERFORM pg_advisory_xact_lock("
          + lock + "); RETURN NEW; END $$");
      s.execute("CREATE TRIGGER late_receipt BEFORE INSERT ON MZO_HUMAN_RECEIPT FOR EACH ROW EXECUTE FUNCTION hold_receipt()");
    }
    var pool = Executors.newSingleThreadExecutor();
    try (var blocker = connection()) {
      blocker.setAutoCommit(false);
      int blockerPid = pid(blocker);
      try (var s = blocker.createStatement()) { s.execute("SELECT pg_advisory_xact_lock(" + lock + ")"); }
      var pending = pool.submit(() -> plugin.execute(raw, keys.peer, "human-command"));
      assertExpiredAfterObservedWait(blocker, blockerPid, pending,
          "INSERT INTO MZO_HUMAN_RECEIPT", deadline, kind.equals("evidence") ? 409 : 403);
    } finally {
      pool.shutdownNow();
      assertTrue(pool.awaitTermination(15, TimeUnit.SECONDS));
      try (var c = connection(); var s = c.createStatement()) {
        s.execute("DROP TRIGGER late_receipt ON MZO_HUMAN_RECEIPT");
        s.execute("DROP FUNCTION hold_receipt()");
      }
    }
    assertEquals(0, countReceipts());
    assertEquals(metrics, taskMetricCount());
    var unchanged = engine.getTaskService().createTaskQuery().taskId(t.getId()).singleResult();
    assertNotNull(unchanged);
    assertNull(unchanged.getAssignee());
    assertEquals(command.get("task_revision"), command(unchanged, "claim", "readback").get("task_revision"));
    assertNull(engine.getHistoryService().createHistoricTaskInstanceQuery().taskId(t.getId()).singleResult().getAssignee());
  }

  @ParameterizedTest
  @ValueSource(strings = {"envelope", "publication"})
  void authorityPublicationCannotOutliveValidityDuringPrincipalRead(String kind) throws Exception {
    long before = revision();
    long now = Instant.now().getEpochSecond();
    long deadline = now + 4;
    var publication = principalPublication(kind.equals("publication") ? deadline : now + 600);
    byte[] raw = keys.sign(publication, "human-authority", now, kind.equals("envelope") ? deadline : now + 60);
    var pool = Executors.newSingleThreadExecutor();
    try (var blocker = connection()) {
      blocker.setAutoCommit(false);
      int blockerPid = pid(blocker);
      try (var s = blocker.createStatement()) {
        s.execute("LOCK TABLE MZO_HUMAN_PRINCIPAL IN ACCESS EXCLUSIVE MODE");
      }
      var pending = pool.submit(() -> plugin.execute(raw, keys.adminPeer, "human-authority"));
      assertExpiredAfterObservedWait(blocker, blockerPid, pending,
          "SELECT ISSUER_,SUBJECT_ FROM MZO_HUMAN_PRINCIPAL", deadline, kind.equals("envelope") ? 403 : 400);
    } finally {
      pool.shutdownNow();
      assertTrue(pool.awaitTermination(15, TimeUnit.SECONDS));
    }
    assertEquals(before, revision());
  }
}
