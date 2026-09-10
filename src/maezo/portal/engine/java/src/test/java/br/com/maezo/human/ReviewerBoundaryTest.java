package br.com.maezo.human;

import static org.junit.jupiter.api.Assertions.*;
import java.nio.charset.StandardCharsets;
import java.util.*;
import java.util.stream.Stream;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.MethodSource;
import org.cibseven.bpm.engine.impl.db.entitymanager.operation.*;
import org.cibseven.bpm.engine.impl.persistence.entity.TaskMeterLogEntity;

/** Independent bounded verifier, offline protocol and actual CIB kernel checks only. */
class ReviewerBoundaryTest {
  static Map<String,Object> command() {
    var c = new TreeMap<String,Object>();
    c.put("schema", "human-command.v1");
    c.put("tenant", "tenant-test"); c.put("workload_ref", "gateway-test");
    c.put("task_id", "task-verifier"); c.put("command_id", "command-verifier");
    c.put("principal_ref", "human-verifier");
    c.put("principal_issuer", "https://issuer.example.test/");
    c.put("principal_subject", "subject-verifier");
    c.put("operation", "decision");
    c.put("process_definition_id", "process-verifier:1:id");
    c.put("process_definition_key", "MZO-HUMAN-SYNTHETIC");
    c.put("process_definition_version", "1");
    c.put("process_definition_digest", "a".repeat(64));
    c.put("task_definition_key", "UT_Acknowledge");
    c.put("form_key", "maezo.synthetic-ack.v1"); c.put("form_version", "1");
    c.put("form_digest", "b".repeat(64));
    c.put("task_revision", "2"); c.put("authority_revision", "3");
    c.put("membership_revision", "1"); c.put("evidence_revision", "3");
    c.put("evidence_ref", "evidence-verifier"); c.put("evidence_digest", "c".repeat(64));
    c.put("assignee_ref", "human-verifier"); c.put("audit_intent_ref", "intent-verifier");
    c.put("outcome", "ACK"); return c;
  }

  static Stream<String> semanticFields() { return command().keySet().stream(); }

  @ParameterizedTest @MethodSource("semanticFields")
  void changingEachSemanticFieldAndRecomputingDigestStillRequiresNewSignature(String field) {
    var keys = new TestKeys();
    var e = Jcs.object(Jcs.parse(keys.sign(command(), "human-command", 100, 160)));
    var c = new TreeMap<>(Jcs.object(e.get("command")));
    c.put(field, "attacker-value"); e.put("command", c);
    e.put("digest", Jcs.digest(Jcs.canonical(c)));
    assertThrows(Rejected.class,
        () -> Envelope.verify(Jcs.canonical(e), keys.trust(), "human-command", keys.peer, 120, x -> false));
  }

  static Stream<String> envelopeFields() {
    return Stream.of("schema", "purpose", "algorithm", "audience", "issuer", "tenant",
      "key_id", "issued_at", "expires_at", "digest", "command", "signature");
  }

  @ParameterizedTest @MethodSource("envelopeFields")
  void everyEnvelopeFieldIsMandatoryEvenForTrustedSigner(String field) {
    var keys = new TestKeys(); var e = keys.envelope(command(), "human-command", 100, 160);
    e.remove(field); var raw = TestKeys.signEnvelope(e, keys.command.getPrivate());
    if (field.equals("signature")) {
      var parsed = Jcs.object(Jcs.parse(raw)); parsed.remove("signature"); raw = Jcs.canonical(parsed);
    }
    final byte[] input = raw;
    assertThrows(Rejected.class,
        () -> Envelope.verify(input, keys.trust(), "human-command", keys.peer, 120, x -> false));
  }

  static Stream<long[]> times() {
    return Stream.of(new long[]{121,160,120}, new long[]{100,120,120},
      new long[]{100,100,100}, new long[]{100,161,120}, new long[]{-1,10,0},
      new long[]{100,160,160}, new long[]{Long.MAX_VALUE-1,Long.MAX_VALUE,120});
  }

  @ParameterizedTest @MethodSource("times")
  void signedInvalidTimeWindowsRefuse(long[] t) {
    var keys = new TestKeys(); var raw = keys.sign(command(), "human-command", t[0], t[1]);
    assertThrows(Rejected.class,
        () -> Envelope.verify(raw, keys.trust(), "human-command", keys.peer, t[2], x -> false));
  }

  @Test void actualSignatureCannotMoveToAnotherTransportPurpose() {
    var keys = new TestKeys(); var raw = keys.sign(command(), "human-command", 100, 160);
    assertThrows(Rejected.class, () -> Envelope.verify(raw, keys.trust(), "human-receipt", keys.peer, 120, x -> false));
    assertThrows(Rejected.class, () -> Envelope.verify(raw, keys.trust(), "human-authority", keys.adminPeer, 120, x -> false));
  }

  @Test void exactTimesAndRevocationFingerprintAreEnforced() {
    var keys = new TestKeys(); var raw = keys.sign(command(), "human-command", 100, 160);
    assertNotNull(Envelope.verify(raw, keys.trust(), "human-command", keys.peer, 100, x -> false));
    assertNotNull(Envelope.verify(raw, keys.trust(), "human-command", keys.peer, 159, x -> false));
    var seen = new ArrayList<String>();
    assertThrows(Rejected.class, () -> Envelope.verify(raw, keys.trust(), "human-command", keys.peer, 120, x -> {seen.add(x); return true;}));
    assertEquals(List.of(Jcs.digest(keys.command.getPublic().getEncoded())), seen);
  }

  @Test void peerAndKeyValidityRemainIndependentOfValidSignature() {
    var keys=new TestKeys(); var raw=keys.sign(command(),"human-command",100,160);
    assertThrows(Rejected.class,()->Envelope.verify(raw,keys.trust(),"human-command",keys.adminPeer,120,x->false));
    for (String field:List.of("not_before","not_after")) {
      var cfg=keys.config();var entries=new ArrayList<Object>((List<?>)cfg.get("keys"));
      var key=new TreeMap<>(Jcs.object(entries.get(0)));
      key.put(field,field.equals("not_before")?"101":"159");entries.set(0,key);cfg.put("keys",entries);
      assertThrows(Rejected.class,()->Envelope.verify(raw,new Trust(cfg),"human-command",keys.peer,120,x->false));
    }
  }

  @Test void aliasingRevokedKeyIdCannotChangeFingerprint() {
    var keys = new TestKeys(); var cfg = keys.config();
    cfg.put("keys", List.of(keys.key("rotated-id", "human-command", "gateway-test", keys.peer, keys.command.getPublic()),
      keys.key("authority-key", "human-authority", "publisher-test", keys.adminPeer, keys.authority.getPublic())));
    var e = keys.envelope(command(), "human-command", 100,160); e.put("key_id","rotated-id");
    var raw=TestKeys.signEnvelope(e,keys.command.getPrivate());
    var fingerprint=Jcs.digest(keys.command.getPublic().getEncoded());
    assertThrows(Rejected.class, () -> Envelope.verify(raw,new Trust(cfg),"human-command",keys.peer,120,fingerprint::equals));
  }

  @Test void noncanonicalBase64TrailingBitsAndPaddingRefuse() {
    var keys=new TestKeys(); var e=Jcs.object(Jcs.parse(keys.sign(command(),"human-command",100,160)));
    String valid=(String)e.get("signature"); String alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";
    int i=alphabet.indexOf(valid.charAt(85));
    e.put("signature",valid.substring(0,85)+alphabet.charAt(i+1));
    assertArrayEquals(Base64.getUrlDecoder().decode(valid),Base64.getUrlDecoder().decode((String)e.get("signature")));
    assertThrows(Rejected.class,()->Envelope.verify(Jcs.canonical(e),keys.trust(),"human-command",keys.peer,120,x->false));
    e.put("signature",valid+"==");
    assertThrows(Rejected.class,()->Envelope.verify(Jcs.canonical(e),keys.trust(),"human-command",keys.peer,120,x->false));
  }

  @Test void trustPurposeSeparationIncludesWorkloadAndMtlsPeer() {
    for (String collision:List.of("workload","peer","public")) {
      var keys=new TestKeys(); var cfg=keys.config();
      cfg.put("keys",List.of(keys.key("command-key","human-command","gateway-test",keys.peer,keys.command.getPublic()),
        keys.key("authority-key","human-authority",collision.equals("workload")?"gateway-test":"publisher-test",
          collision.equals("peer")?keys.peer:keys.adminPeer,
          collision.equals("public")?keys.command.getPublic():keys.authority.getPublic())));
      assertThrows(Rejected.class,()->new Trust(cfg),collision);
    }
  }

  @Test void signedUnknownFieldsCannotSmuggleVariablesOrOptionalAuthority() {
    var keys=new TestKeys();
    var e=keys.envelope(command(),"human-command",100,160); e.put("trusted_override",true);
    byte[] raw=TestKeys.signEnvelope(e,keys.command.getPrivate());
    assertThrows(Rejected.class,()->Envelope.verify(raw,keys.trust(),"human-command",keys.peer,120,x->false));
    for (String field:List.of("variables","human_approved","tier","actor","inputs")) {
      var c=command();c.put(field,Map.of("approved",true));
      assertThrows(Rejected.class,()->HumanCommand.parse(c),field);
    }
  }

  @Test void generatedPythonUnicodeCorpusHasIdenticalCanonicalBytes() throws Exception {
    try(var in=getClass().getResourceAsStream("/reviewer-jcs-corpus.json")) {
      var vectors=(List<?>)Jcs.parse(Objects.requireNonNull(in).readAllBytes());
      assertEquals(128,vectors.size());
      for(Object item:vectors) {
        var v=Jcs.object(item);
        assertArrayEquals(((String)v.get("canonical")).getBytes(StandardCharsets.UTF_8),Jcs.canonical(v.get("input")));
      }
    }
  }

  @Test void pinnedCibOperationQueueRetainsInsertAcrossRepeatedFlushCalculations() {
    var manager=new DbOperationManager(); var entity=new TaskMeterLogEntity();entity.setId("eir-meter");
    var operation=new DbEntityOperation();operation.setEntity(entity);operation.setEntityType(TaskMeterLogEntity.class);
    operation.setOperationType(DbOperationType.INSERT);operation.setFlushRelevantEntityReferences(Set.of());
    assertTrue(manager.addOperation(operation));
    var first=manager.calculateFlush();assertEquals(1,first.size());
    var second=manager.calculateFlush();assertEquals(1,second.size());assertSame(first.get(0),second.get(0));
  }
}
