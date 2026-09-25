package br.com.maezo.human.readprovider;

import static br.com.maezo.human.readprovider.TestRecords.*;
import static org.junit.jupiter.api.Assertions.*;

import br.com.maezo.human.Jcs;
import br.com.maezo.human.PortalReadTrust;
import br.com.maezo.human.Rejected;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.function.Consumer;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.function.Executable;

/**
 * H1 (D-N), the provider alone against the built JAR and a real admission row: a {@code resource}
 * publication qualifies only under an admission that carries the approver's {@code human} block and
 * the {@code resource} publisher, with the provenance DERIVED from the payload (the vector contract)
 * and a classification the approver admitted. The staff-only admission (T1.7a/b) keeps refusing it,
 * and the same admission's identity policy and classification checks switch from "always refuse"
 * to "verify". Every negative ends with the positive control on the same fixture.
 */
class ResourceQualificationJarIT {
  ProviderFixture f;
  final InstalledReadProvidersJarIT.Moving clock = new InstalledReadProvidersJarIT.Moving();
  InstalledReadProviders p;
  PortalReadTrust.Admission a;
  Map<String, Object> human, entry, payload;

  @BeforeEach
  void start() throws Exception {
    f = new ProviderFixture();
    human = HumanAdmissionTest.human();
    entry = HumanAdmissionTest.firstEntry(human);
    var c = Fields.object(Fields.list(HumanAdmissionTest.vector().get("cases")).get(0));
    payload = Fields.canonical(((String) c.get("payload_jcs")).getBytes(StandardCharsets.UTF_8));
    f.install(withHuman(f.publicationAdmission(1), human));
    p = new InstalledReadProviders(f.providerFile, clock);
    a = admission();
  }

  @AfterEach
  void stop() throws Exception {
    f.close();
  }

  PortalReadTrust.Admission admission() {
    return p.acquire(PublicationQualificationJarIT.publisherScope(), f.engine, f.incarnation,
        f.deployment, f.deploymentDigest, f.trustDigest, "portal-read-publication");
  }

  static void refused(Executable action) {
    Rejected r = assertThrows(Rejected.class, action);
    assertEquals(503, r.status);
    assertEquals("READ_DEPENDENCY_UNAVAILABLE", r.code);
  }

  Map<String, Object> resource(Map<String, Object> body) {
    return f.publication("resource",
        resourceProvenance(TENANT, body, clock.instant().minusSeconds(1), 300), body, 0);
  }

  void publishes(Map<String, Object> request) {
    var q = p.qualifyPublication(a, request);
    q.requireCurrent();
    q.verify("resource", PublicationQualificationJarIT.obj(request, "source"),
        PublicationQualificationJarIT.obj(request, "payload"));
    a.verifySource("resource", PublicationQualificationJarIT.obj(request, "source"));
  }

  void refusedWhen(Consumer<Map<String, Object>> breakIt) {
    var request = PublicationQualificationJarIT.deep(resource(payload));
    breakIt.accept(request);
    refused(() -> publishes(request));
    publishes(resource(payload));
  }

  @Test
  void aResourceUnderTheHumanAdmissionQualifiesAndVerifies() {
    publishes(resource(payload));
  }

  @Test
  void theProvenanceIsDerivedFromThePayload() {
    refusedWhen(r -> src(r).put("source_ref", RESOURCE_PREFIX + "other-task"));
    refusedWhen(r -> src(r).put("source_ref", "portal-resource:other:task:" + payload.get("task_id")));
    refusedWhen(r -> src(r).put("source_revision", "2"));
    refusedWhen(r -> src(r).put("source_digest", "0".repeat(64)));
    refusedWhen(r -> src(r).put("receipt_ref", "portal-resource:other:task:x@1"));
    refusedWhen(r -> src(r).put("valid_until", time(clock.instant().plusSeconds(3600))));
    refusedWhen(r -> {
      src(r).put("publisher_ref", "another-publisher");
      Fields.object(r.get("scope")).put("workload_ref", "another-publisher");
    });
    // The payload moved but the provenance still names the old digest.
    refusedWhen(r -> PublicationQualificationJarIT.obj(r, "payload").put("resource_revision", "9"));
  }

  static Map<String, Object> src(Map<String, Object> request) {
    return PublicationQualificationJarIT.obj(request, "source");
  }

  @Test
  void onlyAnAdmittedClassificationOfThatDefinitionQualifies() {
    var other = PublicationQualificationJarIT.deep(payload);
    Fields.object(other.get("classification")).put("fields_digest", "0".repeat(64));
    refused(() -> publishes(resource(other)));
    var definition = PublicationQualificationJarIT.deep(payload);
    definition.put("process_definition_id", "SP-OP-AUTH-001:1:x");
    refused(() -> publishes(resource(definition)));
    var late = PublicationQualificationJarIT.deep(payload);
    Fields.object(late.get("classification")).put("valid_until", time(f.until.plusSeconds(1)));
    refused(() -> publishes(resource(late)));
    publishes(resource(payload));
  }

  @Test
  void theQualifiedResourceIsTheOnlyThingVerifyAccepts() {
    var request = resource(payload);
    var q = p.qualifyPublication(a, request);
    var membership = f.publication("membership",
        membershipProvenance(TENANT, projection(member("h", f.until)), clock.instant(), 300),
        projection(member("h", f.until)), 0);
    refused(() -> q.verify("membership", src(request),
        PublicationQualificationJarIT.obj(request, "payload")));
    refused(() -> q.verify("resource", src(membership),
        PublicationQualificationJarIT.obj(request, "payload")));
    q.verify("resource", src(request), PublicationQualificationJarIT.obj(request, "payload"));
  }

  @Test
  void theStaffOnlyAdmissionRefusesResourceIdentityAndClassification() throws Exception {
    f.install(f.publicationAdmission(2)); // a newer, staff-only revision supersedes the human one
    a = admission();
    refused(() -> publishes(resource(payload)));
    refused(() -> a.verifySource("resource", src(resource(payload))));
    var catalogEntry = HumanAdmissionTest.catalogEntry(entry);
    var classification = HumanAdmissionTest.classification(entry, f.until.minusSeconds(60));
    var task = HumanAdmissionTest.task(entry, HumanAdmissionTest.TASK);
    var policy = Fields.object(entry.get("identity_policy"));
    refused(() -> a.verifyClassification(classification, catalogEntry));
    refused(() -> a.verifyIdentityPolicy(policy, task, new ArrayList<>()));
    f.install(withHuman(f.publicationAdmission(3), human));
    a = admission();
    // amh is the vector's tenant; the fixture's is tenant-test.
    task.put("tenant_id", TENANT);
    a.verifyClassification(classification, catalogEntry);
    a.verifyIdentityPolicy(policy, task, new ArrayList<>(List.of(record("link_id", "l1",
        "link_revision", "1", "task_id", HumanAdmissionTest.TASK, "tenant_id", TENANT, "type",
        "candidate", "group_id", "atendimento-humano", "user_id", null))));
    publishes(resource(payload));
  }

  @Test
  void aRevokedHumanAdmissionKillsTheResourceChecks() throws Exception {
    var request = resource(payload);
    var q = p.qualifyPublication(a, request);
    var catalogEntry = HumanAdmissionTest.catalogEntry(entry);
    var classification = HumanAdmissionTest.classification(entry, f.until.minusSeconds(60));
    a.verifyClassification(classification, catalogEntry);
    f.revoke(1);
    refused(this::admission);
    refused(q::requireCurrent);
    refused(() -> a.verifyClassification(classification, catalogEntry));
    f.install(withHuman(f.publicationAdmission(2), human));
    a = admission();
    publishes(resource(payload));
  }

  @Test
  void theResourceVectorPayloadIsItsOwnJcs() {
    assertArrayEquals(Jcs.canonical(payload),
        Jcs.canonical(Fields.object(Jcs.parse(Jcs.canonical(payload)))));
  }
}
