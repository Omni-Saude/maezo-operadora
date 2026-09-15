package br.com.maezo.human;

import static org.junit.jupiter.api.Assertions.*;

import java.time.Instant;
import java.util.*;
import org.junit.jupiter.api.Tag;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;

/** PAE-V3: real PostgreSQL rollback without any CIB task mutation to mask enlistment. */
@Tag("integration")
class EnlistmentEngineIT extends AtomicEngineIT {
  Map<String, Object> publication(String operation) throws Exception {
    var command = authority(operation);
    if (operation.equals("principal"))
      command.putAll(Map.of("principal_ref", "human-test", "issuer", "https://issuer.example.test/",
          "subject", "subject-human-test", "active", false, "groups", List.of(),
          "valid_until", Long.toString(Instant.now().getEpochSecond() + 600)));
    else command.put("key_fingerprint", Jcs.digest(keys.command.getPublic().getEncoded()));
    return command;
  }

  List<Object> authorityState() throws Exception {
    try (var c = connection(); var s = c.createStatement(); var r = s.executeQuery(
        "SELECT t.REV_,p.REV_,p.ACTIVE_,p.VALID_UNTIL_,p.GROUPS_,"
            + "(SELECT COUNT(*) FROM MZO_HUMAN_REVOKED_KEY) FROM MZO_HUMAN_TENANT t "
            + "JOIN MZO_HUMAN_PRINCIPAL p ON t.TENANT_=p.TENANT_ WHERE p.PRINCIPAL_='human-test'")) {
      assertTrue(r.next());
      var values = new ArrayList<Object>();
      for (int i = 1; i <= 6; i++) values.add(r.getObject(i));
      assertFalse(r.next());
      return values;
    }
  }

  void retryOnce(Map<String, Object> command, long before) throws Exception {
    publish(command);
    assertEquals(before + 1, revision());
    var committed = authorityState();
    assertThrows(Rejected.class, () -> publish(command));
    assertEquals(committed, authorityState());
  }

  @ParameterizedTest
  @ValueSource(strings = {"principal", "revoke-key"})
  void commandAbortRollsBackAuthorityWithoutTaskWrites(String operation) throws Exception {
    var command = publication(operation);
    var before = authorityState();
    long revision = revision();
    long now = Instant.now().getEpochSecond();
    byte[] raw = keys.sign(command, "human-authority", now, now + 60);
    var failure = assertThrows(Rejected.class, () -> config.getCommandExecutorTxRequired().execute(context -> {
      new AuthorityCommand(keys.trust(), raw, keys.adminPeer).execute(context);
      throw new Rejected(409, "ENLISTMENT_ABORT_PROBE");
    }));
    assertEquals("ENLISTMENT_ABORT_PROBE", failure.code);
    assertEquals(before, authorityState(), "Rollback must restore tenant, principal and revocation rows");
    retryOnce(command, revision);
  }

  @ParameterizedTest
  @ValueSource(strings = {"principal", "revoke-key"})
  void finalCommitFailureRollsBackAuthorityWithoutTaskWrites(String operation) throws Exception {
    var command = publication(operation);
    var before = authorityState();
    long revision = revision();
    try (var c = connection(); var s = c.createStatement()) {
      s.execute("CREATE FUNCTION fail_authority_commit() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'enlistment-final-commit'; END $$");
      s.execute("CREATE CONSTRAINT TRIGGER authority_commit_fault AFTER UPDATE ON MZO_HUMAN_TENANT DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION fail_authority_commit()");
    }
    try {
      var failure = assertThrows(RuntimeException.class, () -> publish(command));
      StringBuilder causes = new StringBuilder();
      for (Throwable e = failure; e != null; e = e.getCause()) causes.append(e.getMessage());
      assertTrue(causes.toString().contains("enlistment-final-commit"),
          "Must reach the actual deferred database commit, not implicit close-time success");
      assertEquals(before, authorityState());
    } finally {
      try (var c = connection(); var s = c.createStatement()) {
        s.execute("DROP TRIGGER authority_commit_fault ON MZO_HUMAN_TENANT");
        s.execute("DROP FUNCTION fail_authority_commit()");
      }
    }
    retryOnce(command, revision);
  }
}
