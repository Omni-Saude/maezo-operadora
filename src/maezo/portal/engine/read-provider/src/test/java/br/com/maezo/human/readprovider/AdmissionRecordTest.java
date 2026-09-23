package br.com.maezo.human.readprovider;

import static br.com.maezo.human.readprovider.TestRecords.*;
import static org.junit.jupiter.api.Assertions.*;

import br.com.maezo.human.Jcs;
import br.com.maezo.human.Rejected;
import java.nio.charset.StandardCharsets;
import java.security.KeyPair;
import java.time.Instant;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.function.Consumer;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.function.Executable;

class AdmissionRecordTest {
  final KeyPair root = ed25519();
  final Instant before = now().minusSeconds(60), until = before.plusSeconds(3600);

  Map<String, Object> valid() {
    return admission(1, "default", "incarnation-1", "read-release-1", "c".repeat(64),
        "d".repeat(64), "1".repeat(64), "2".repeat(64), "3".repeat(64), before, until);
  }

  AdmissionRecord verify(Map<String, Object> record, KeyPair signer) {
    byte[] raw = Jcs.canonical(record);
    return AdmissionRecord.verify(raw, sign(signer.getPrivate(), raw), root.getPublic());
  }

  static void refused(Executable action) {
    Rejected r = assertThrows(Rejected.class, action);
    assertEquals(503, r.status);
    assertEquals("READ_DEPENDENCY_UNAVAILABLE", r.code);
  }

  void refusedWith(Consumer<Map<String, Object>> change) {
    var record = valid();
    change.accept(record);
    refused(() -> verify(record, root));
  }

  @Test
  void validRecordVerifiesAndItsDigestIsTheSignedBytes() {
    var record = valid();
    var verified = verify(record, root);
    assertEquals(Jcs.digest(Jcs.canonical(record)), verified.digest);
    assertEquals(1, verified.revision);
    assertEquals(Map.of("tenant", "tenant-test", "environment", "dev", "workload_ref",
                     "portal-staff"),
        verified.scope);
    assertEquals(5, verified.statementTimeoutSeconds);
    assertEquals(300, verified.observationSeconds);
    assertEquals(
        java.util.Set.of("membership", "catalog-designate"), verified.publishers.keySet());
  }

  @Test
  void anotherRootOrATamperedByteIsRefused() {
    refused(() -> verify(valid(), ed25519()));
    byte[] raw = Jcs.canonical(valid());
    byte[] signature = sign(root.getPrivate(), raw);
    byte[] tampered = raw.clone();
    tampered[tampered.length - 3] ^= 1;
    refused(() -> AdmissionRecord.verify(tampered, signature, root.getPublic()));
    refused(() -> AdmissionRecord.verify(raw, new byte[64], root.getPublic()));
    refused(() -> AdmissionRecord.verify(raw, new byte[63], root.getPublic()));
    refused(() -> AdmissionRecord.verify(raw, null, root.getPublic()));
    AdmissionRecord.verify(raw, signature, root.getPublic());
  }

  @Test
  void signatureWithoutTheDomainIsRefused() throws Exception {
    byte[] raw = Jcs.canonical(valid());
    var signer = java.security.Signature.getInstance("Ed25519");
    signer.initSign(root.getPrivate());
    signer.update(raw);
    byte[] undomained = signer.sign();
    refused(() -> AdmissionRecord.verify(raw, undomained, root.getPublic()));
  }

  @Test
  void nonCanonicalBytesAreRefusedEvenWhenSigned() {
    byte[] raw = (" " + new String(Jcs.canonical(valid()), StandardCharsets.UTF_8))
                     .getBytes(StandardCharsets.UTF_8);
    refused(() -> AdmissionRecord.verify(raw, sign(root.getPrivate(), raw), root.getPublic()));
  }

  @Test
  void theShapeIsClosed() {
    refusedWith(r -> r.put("unexpected", "x"));
    refusedWith(r -> r.remove("observation_seconds"));
    refusedWith(r -> r.put("schema", "portal-read-admission.v2"));
    refusedWith(r -> ((Map<String, Object>) r.get("scope")).put("engine_name", "default"));
    refusedWith(r -> ((Map<String, Object>) r.get("code_digests")).remove("provider"));
    refusedWith(r -> ((Map<String, Object>) r.get("catalog")).put("catalog_digest", "A".repeat(64)));
    refusedWith(r -> r.put("admission_revision", "0"));
    refusedWith(r -> r.put("admission_revision", "01"));
  }

  @Test
  void validityIsBoundedToFourteenDays() {
    refusedWith(r -> r.put("valid_until", time(before.plusSeconds(14L * 86400 + 1))));
    refusedWith(r -> r.put("valid_until", time(before)));
    var edge = valid();
    edge.put("valid_until", time(before.plusSeconds(14L * 86400)));
    verify(edge, root);
  }

  @Test
  void timeoutsAreBounded() {
    for (String bad : List.of("0", "11", "-1", "5.0"))
      refusedWith(r -> r.put("statement_timeout_seconds", bad));
    for (String bad : List.of("59", "901"))
      refusedWith(r -> r.put("observation_seconds", bad));
  }

  @Test
  void onlyStaffPurposesAndSourceKindsAreAdmissible() {
    refusedWith(r -> r.put("purposes", new ArrayList<>(List.of("portal-task-write"))));
    refusedWith(r -> r.put("purposes", new ArrayList<>()));
    refusedWith(r
        -> r.put("purposes", new ArrayList<>(List.of("portal-task-read", "portal-task-read"))));
    refusedWith(r
        -> ((List<Object>) r.get("publishers"))
               .add(record("kind", "resource", "publisher_ref", PUBLISHER, "source_ref_prefix",
                   "task")));
    refusedWith(r
        -> ((List<Object>) r.get("publishers"))
               .add(record("kind", "membership", "publisher_ref", "second", "source_ref_prefix",
                   "x")));
  }

  @Test
  void continuityCommitmentsAreClosedAndDistinct() {
    refusedWith(r -> r.put("continuity_keys", new ArrayList<>()));
    refusedWith(r
        -> ((List<Object>) r.get("continuity_keys"))
               .add(record("key_id", KEY_ID, "generation", "2", "commitment", "4".repeat(64),
                   "not_before", time(before), "not_after", time(until))));
    refusedWith(r
        -> ((List<Object>) r.get("continuity_keys"))
               .add(record("key_id", "native-continuity-2", "generation", "2", "commitment",
                   "3".repeat(64), "not_before", time(before), "not_after", time(until))));
  }

  @Test
  void jsonNumbersAreNotAdmissible() {
    String text = new String(Jcs.canonical(valid()), StandardCharsets.UTF_8)
                      .replace("\"observation_seconds\":\"300\"", "\"observation_seconds\":300");
    byte[] raw = text.getBytes(StandardCharsets.UTF_8);
    refused(() -> AdmissionRecord.verify(raw, sign(root.getPrivate(), raw), root.getPublic()));
  }
}
