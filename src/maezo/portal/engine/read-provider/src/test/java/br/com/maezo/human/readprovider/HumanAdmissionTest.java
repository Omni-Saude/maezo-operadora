package br.com.maezo.human.readprovider;

import static br.com.maezo.human.readprovider.TestRecords.*;
import static org.junit.jupiter.api.Assertions.*;

import br.com.maezo.human.Jcs;
import br.com.maezo.human.Rejected;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.KeyPair;
import java.time.Instant;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.function.Consumer;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.function.Executable;

/**
 * H1 (D-N), no database: the {@code human} block of the admission, the task identity policy, the
 * classification and the {@code resource} contract, against the vector shared with the Python H2
 * ({@code tests/fixtures/portal_read/jcs-resource-vector.json}). Every negative is one field away
 * from a positive that passes in the same test.
 */
class HumanAdmissionTest {
  static final Instant FAR = Instant.parse("2099-01-01T00:00:00Z");
  final KeyPair root = ed25519();

  static Map<String, Object> vector() throws Exception {
    String file = System.getProperty("maezo.jcs.resource.vector");
    assertNotNull(file, "the pom passes the shared vector path (maezo.jcs.resource.vector)");
    return Fields.object(Jcs.parse(Files.readAllBytes(Path.of(file))));
  }

  static Map<String, Object> deep(Object value) {
    return Fields.object(Jcs.parse(Jcs.canonical(value)));
  }

  static void refused(Executable action) {
    Rejected r = assertThrows(Rejected.class, action);
    assertEquals(503, r.status);
    assertEquals("READ_DEPENDENCY_UNAVAILABLE", r.code);
  }

  static Map<String, Object> human() throws Exception {
    return deep(vector().get("human"));
  }

  static Map<String, Object> firstEntry(Map<String, Object> human) {
    return Fields.object(Fields.list(human.get("entries")).get(0));
  }

  /** The catalog entry the engine hands to verifyClassification (only the keys it reads). */
  static Map<String, Object> catalogEntry(Map<String, Object> admitted) {
    var c = Fields.object(admitted.get("classification"));
    return record("process_definition_id", admitted.get("process_definition_id"),
        "task_definition_key", admitted.get("task_definition_key"), "disclosure_policy",
        record("artifact_ref", c.get("policy_ref"), "digest", c.get("policy_digest")),
        "opaque_task_id_policy", admitted.get("identity_policy"));
  }

  static Map<String, Object> classification(Map<String, Object> admitted, Instant until) {
    var c = deep(admitted.get("classification"));
    c.put("valid_until", time(until));
    return c;
  }

  static Map<String, Object> task(Map<String, Object> admitted, String id) {
    return record("task_id", id, "task_revision", "3", "process_definition_id",
        admitted.get("process_definition_id"), "task_definition_key",
        admitted.get("task_definition_key"), "tenant_id", "amh", "assignee_ref", null,
        "engine_due_at", null, "active", true);
  }

  static Map<String, Object> group(String task, String group) {
    return record("link_id", "link-" + group, "link_revision", "1", "task_id", task, "tenant_id",
        "amh", "type", "candidate", "group_id", group, "user_id", null);
  }

  static List<Object> links(Object... items) {
    return new ArrayList<>(List.of(items));
  }

  // ------------------------------------------------------------------------------ the vector

  @Test
  void theResourceVectorIsTheJavaJcsAndTheHumanBlockParses() throws Exception {
    var v = vector();
    assertEquals("portal-read-jcs-resource-vector.v1", v.get("schema"));
    byte[] human = Jcs.canonical(v.get("human"));
    assertEquals(v.get("human_jcs"), new String(human, StandardCharsets.UTF_8));
    assertEquals(v.get("human_sha256"), Jcs.digest(human));
    var parsed = HumanAdmission.parse(v.get("human"));
    assertEquals(1, parsed.entries.size());
    var cases = Fields.list(v.get("cases"));
    assertTrue(cases.size() >= 3, "the vector covers complete, granted and revoked");
    for (Object o : cases) {
      var c = Fields.object(o);
      byte[] raw = ((String) c.get("payload_jcs")).getBytes(StandardCharsets.UTF_8);
      var payload = Fields.canonical(raw); // exact own-JCS bytes, number-free
      assertEquals(c.get("source_digest"), Fields.digest(payload), (String) c.get("name"));
      var source = Fields.object(c.get("source"));
      String task = (String) payload.get("task_id"), revision = (String) payload.get("resource_revision");
      assertEquals(v.get("source_ref_prefix") + task, source.get("source_ref"));
      assertEquals(revision, source.get("source_revision"));
      assertEquals(c.get("source_digest"), source.get("source_digest"));
      assertEquals("portal-resource:" + v.get("tenant") + ":task:" + task + "@" + revision,
          source.get("receipt_ref"));
      assertEquals(Fields.time(source, "observed_at")
              .plusSeconds(Long.parseLong((String) v.get("observation_seconds"))),
          Fields.time(source, "valid_until"));
      parsed.verifyPublishedClassification((String) payload.get("process_definition_id"),
          Fields.object(payload.get("classification")), FAR);
    }
  }

  @Test
  void theVectorsRefusedRowsAreRefused() throws Exception {
    var v = vector();
    var parsed = HumanAdmission.parse(v.get("human"));
    for (Object o : Fields.list(v.get("refused"))) {
      var c = Fields.object(o);
      if (c.containsKey("payload_json")) {
        byte[] raw = ((String) c.get("payload_json")).getBytes(StandardCharsets.UTF_8);
        assertThrows(Rejected.class, () -> Jcs.parse(raw), (String) c.get("name"));
      } else {
        var payload = Fields.canonical(((String) c.get("payload_jcs")).getBytes(StandardCharsets.UTF_8));
        refused(() -> parsed.verifyPublishedClassification(
            (String) payload.get("process_definition_id"),
            Fields.object(payload.get("classification")), FAR));
      }
    }
  }

  // ------------------------------------------------------------------------- the human block

  void blockRefused(Consumer<Map<String, Object>> change) throws Exception {
    var h = human();
    HumanAdmission.parse(deep(h)); // the positive control
    change.accept(h);
    refused(() -> HumanAdmission.parse(h));
  }

  @Test
  void theHumanBlockIsClosed() throws Exception {
    blockRefused(h -> h.put("extra", "x"));
    blockRefused(h -> h.put("entries", new ArrayList<>()));
    blockRefused(h -> firstEntry(h).put("extra", "x"));
    blockRefused(h -> firstEntry(h).remove("user_candidates"));
    blockRefused(h -> firstEntry(h).put("user_candidates", "anyone"));
    blockRefused(h -> firstEntry(h).put("task_id_format", "any"));
    blockRefused(h -> firstEntry(h).put("candidate_groups", new ArrayList<>()));
    blockRefused(h -> firstEntry(h).put("candidate_groups", links("g", "g")));
    blockRefused(h -> firstEntry(h).put("candidate_groups", links("${expression}")));
    blockRefused(h -> Fields.object(firstEntry(h).get("classification")).put("projection", "x"));
    blockRefused(h -> Fields.object(firstEntry(h).get("classification")).put("valid_until",
        time(FAR)));
    blockRefused(h -> Fields.object(firstEntry(h).get("identity_policy")).put("digest", "x"));
    blockRefused(h -> {
      var entries = Fields.list(h.get("entries"));
      entries.add(deep(entries.get(0))); // the same (definition, task) twice
    });
  }

  // --------------------------------------------------------------------- the admission record

  Map<String, Object> staff() {
    Instant before = now().minusSeconds(60);
    return admission(1, "default", "incarnation-1", "read-release-1", "c".repeat(64),
        "d".repeat(64), "1".repeat(64), "2".repeat(64), "3".repeat(64), before,
        before.plusSeconds(3600));
  }

  Map<String, Object> withHuman() throws Exception {
    var a = staff();
    a.put("human", human());
    Fields.list(a.get("publishers")).add(record("kind", "resource", "publisher_ref", PUBLISHER,
        "source_ref_prefix", "portal-resource:amh:task:"));
    return a;
  }

  AdmissionRecord verify(Map<String, Object> record) {
    byte[] raw = Jcs.canonical(record);
    return AdmissionRecord.verify(raw, sign(root.getPrivate(), raw), root.getPublic());
  }

  @Test
  void resourceAndHumanComeTogetherOrNotAtAll() throws Exception {
    assertNull(verify(staff()).human, "a staff-only admission keeps its T1.7a shape");
    var both = verify(withHuman());
    assertNotNull(both.human);
    assertTrue(both.publishers.containsKey("resource"));
    var resourceOnly = withHuman();
    resourceOnly.remove("human");
    refused(() -> verify(resourceOnly));
    var humanOnly = staff();
    humanOnly.put("human", human());
    refused(() -> verify(humanOnly));
    var badHuman = withHuman();
    badHuman.put("human", record("entries", new ArrayList<>()));
    refused(() -> verify(badHuman));
  }

  // ------------------------------------------------------------------------ identity policy

  static final String TASK = "4711", UUID_TASK = "0f3c2a1e-7b6d-4c5e-9a8b-112233445566";

  @Test
  void identityPolicyAdmitsOnlyTheAdmittedNamespaces() throws Exception {
    var parsed = HumanAdmission.parse(human());
    var admitted = firstEntry(human());
    var policy = Fields.object(admitted.get("identity_policy"));
    parsed.verifyIdentityPolicy(policy, task(admitted, TASK),
        links(group(TASK, "atendimento-humano")), "amh");
    parsed.verifyIdentityPolicy(policy, task(admitted, TASK), links(), "amh");
    var assigned = task(admitted, TASK);
    assigned.put("assignee_ref", "staff-c1-no-grupo");
    parsed.verifyIdentityPolicy(policy, assigned, links(), "amh");

    refused(() -> parsed.verifyIdentityPolicy(
        record("artifact_ref", "other", "digest", policy.get("digest")), task(admitted, TASK),
        links(), "amh"));
    refused(() -> parsed.verifyIdentityPolicy(policy, task(admitted, TASK), links(), "other"));
    refused(() -> parsed.verifyIdentityPolicy(policy, task(admitted, UUID_TASK), links(), "amh"));
    refused(() -> parsed.verifyIdentityPolicy(policy, task(admitted, "04711"),
        links(), "amh"));
    refused(() -> parsed.verifyIdentityPolicy(policy, task(admitted, TASK),
        links(group(TASK, "grupo-fora-do-dominio")), "amh"));
    refused(() -> parsed.verifyIdentityPolicy(policy, task(admitted, TASK),
        links(group("4712", "atendimento-humano")), "amh"));
    var user = group(TASK, "x");
    user.put("group_id", null);
    user.put("user_id", "human-1");
    refused(() -> parsed.verifyIdentityPolicy(policy, task(admitted, TASK), links(user), "amh"));
    var neither = group(TASK, "x");
    neither.put("group_id", null);
    refused(() -> parsed.verifyIdentityPolicy(policy, task(admitted, TASK), links(neither), "amh"));
    var inactive = task(admitted, TASK);
    inactive.put("active", false);
    refused(() -> parsed.verifyIdentityPolicy(policy, inactive, links(), "amh"));
    var otherTask = task(admitted, TASK);
    otherTask.put("task_definition_key", "UT_Outra");
    refused(() -> parsed.verifyIdentityPolicy(policy, otherTask, links(), "amh"));
    var phiAssignee = task(admitted, TASK);
    phiAssignee.put("assignee_ref", "Maria da Silva");
    refused(() -> parsed.verifyIdentityPolicy(policy, phiAssignee, links(), "amh"));
  }

  @Test
  void nativePrincipalCandidatesOnlyWhenAdmittedAndUuidIdsOnlyWhenAdmitted() throws Exception {
    var h = human();
    firstEntry(h).put("user_candidates", "native_principal");
    firstEntry(h).put("task_id_format", "uuid");
    var parsed = HumanAdmission.parse(h);
    var admitted = firstEntry(h);
    var policy = Fields.object(admitted.get("identity_policy"));
    var user = group(UUID_TASK, "x");
    user.put("group_id", null);
    user.put("user_id", "human-1");
    parsed.verifyIdentityPolicy(policy, task(admitted, UUID_TASK), links(user), "amh");
    refused(() -> parsed.verifyIdentityPolicy(policy, task(admitted, TASK), links(), "amh"));
    refused(() -> parsed.verifyIdentityPolicy(policy, task(admitted, UUID_TASK.toUpperCase()), links(), "amh"));
  }

  // ------------------------------------------------------------------------- classification

  @Test
  void classificationIsExactlyTheAdmittedOneAndBoundedByTheAdmission() throws Exception {
    var parsed = HumanAdmission.parse(human());
    var admitted = firstEntry(human());
    Instant ceiling = Instant.parse("2026-10-01T00:00:00Z");
    var entry = catalogEntry(admitted);
    parsed.verifyClassification(classification(admitted, ceiling), entry, ceiling);

    refused(() -> parsed.verifyClassification(
        classification(admitted, ceiling.plusNanos(1000)), entry, ceiling));
    for (String k : HumanAdmission.CLASSIFICATION_KEYS) {
      var changed = classification(admitted, ceiling);
      changed.put(k, k.endsWith("digest") ? "0".repeat(64) : "other-ref");
      refused(() -> parsed.verifyClassification(changed, entry, ceiling));
    }
    var extra = classification(admitted, ceiling);
    extra.put("extra", "x");
    refused(() -> parsed.verifyClassification(extra, entry, ceiling));
    var otherDisclosure = deep(entry);
    Fields.object(otherDisclosure.get("disclosure_policy")).put("digest", "0".repeat(64));
    refused(() -> parsed.verifyClassification(classification(admitted, ceiling), otherDisclosure,
        ceiling));
    var otherEntry = deep(entry);
    otherEntry.put("task_definition_key", "UT_Outra");
    refused(() -> parsed.verifyClassification(classification(admitted, ceiling), otherEntry,
        ceiling));
    refused(() -> parsed.verifyClassification(null, entry, ceiling));
    // Publication side: any admitted entry of that definition, same ceiling.
    parsed.verifyPublishedClassification((String) admitted.get("process_definition_id"),
        classification(admitted, ceiling), ceiling);
    refused(() -> parsed.verifyPublishedClassification("SP-OTHER:1:x",
        classification(admitted, ceiling), ceiling));
    refused(() -> parsed.verifyPublishedClassification(
        (String) admitted.get("process_definition_id"),
        classification(admitted, ceiling.plusSeconds(1)), ceiling));
  }
}
