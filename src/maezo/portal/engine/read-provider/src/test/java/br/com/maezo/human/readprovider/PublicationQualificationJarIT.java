package br.com.maezo.human.readprovider;

import static br.com.maezo.human.readprovider.TestRecords.*;
import static org.junit.jupiter.api.Assertions.*;

import br.com.maezo.human.Jcs;
import br.com.maezo.human.PortalReadTrust;
import br.com.maezo.human.Rejected;
import java.nio.file.Files;
import java.time.Instant;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.function.Consumer;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.function.Executable;

/**
 * T1.7b, the provider alone against the built JAR: {@code qualifyPublication} reads the LIVE
 * {@code portal_memberships} row through the observer login over {@code verify-full} TLS, and the
 * qualification's {@code verify} accepts only the projection of that row under the T1.5 contract.
 * Every negative ends with the positive control on the same fixture, so a refusal can never pass
 * because the fixture itself was broken.
 */
class PublicationQualificationJarIT {
  ProviderFixture f;
  final InstalledReadProvidersJarIT.Moving clock = new InstalledReadProvidersJarIT.Moving();
  InstalledReadProviders p;
  PortalReadTrust.Admission a;
  Member m;

  @BeforeEach
  void start() throws Exception {
    f = new ProviderFixture();
    f.install(f.publicationAdmission(1));
    p = new InstalledReadProviders(f.providerFile, clock);
    a = admission(p);
    m = member("human-1", f.until.minusSeconds(60));
    f.putMembership(TENANT, m);
  }

  @AfterEach
  void stop() throws Exception {
    f.close();
  }

  PortalReadTrust.Admission admission(InstalledReadProviders provider) {
    return provider.acquire(publisherScope(), f.engine, f.incarnation, f.deployment,
        f.deploymentDigest, f.trustDigest, "portal-read-publication");
  }

  static Map<String, Object> publisherScope() {
    return record("tenant", TENANT, "environment", "dev", "workload_ref", PUBLISHER);
  }

  static void refused(Executable action) {
    Rejected r = assertThrows(Rejected.class, action);
    assertEquals(503, r.status);
    assertEquals("READ_DEPENDENCY_UNAVAILABLE", r.code);
  }

  /** The request the Python publisher would send for the live row of {@code who}. */
  Map<String, Object> membership(Member who) {
    var projection = projection(who);
    return f.publication("membership",
        membershipProvenance(TENANT, projection, clock.instant().minusSeconds(1), 300),
        projection, 0);
  }

  @SuppressWarnings("unchecked")
  static Map<String, Object> obj(Map<String, Object> m, String key) {
    return (Map<String, Object>) m.get(key);
  }

  static Map<String, Object> deep(Map<String, Object> m) {
    return Fields.object(br.com.maezo.human.Jcs.parse(Jcs.canonical(m)));
  }

  /** qualify + verify exactly as PortalReadPublication does (verify gets the request's own maps). */
  void publishes(Map<String, Object> request) {
    var q = p.qualifyPublication(a, request);
    q.requireCurrent();
    q.verify((String) request.get("kind"), obj(request, "source"), obj(request, "payload"));
    assertFalse(q.validUntil().isAfter(clock.instant().plusSeconds(300)));
  }

  void refusedPublication(Map<String, Object> request) {
    refused(() -> publishes(request));
  }

  /** Breaks one thing of a valid membership request, refuses it, then the valid one passes. */
  void membershipRefusedWhen(Consumer<Map<String, Object>> breakIt) {
    var request = deep(membership(m));
    breakIt.accept(request);
    refusedPublication(request);
    publishes(membership(m));
  }

  @Test
  void membershipEqualToTheLiveRowQualifiesAndVerifies() {
    publishes(membership(m));
    var revoked = m.revoked(true).revision(2);
    assertDoesNotThrow(() -> f.putMembership(TENANT, revoked));
    publishes(membership(revoked)); // a revocation is published the same way (state=revoked)
  }

  @Test
  void anyFieldOfThePayloadDifferentFromTheLiveRowIsRefused() {
    membershipRefusedWhen(r -> obj(r, "payload").put("audience", "provider"));
    membershipRefusedWhen(r -> obj(r, "payload").put("state", "revoked"));
    membershipRefusedWhen(r -> obj(r, "payload").put("reviewed_until", time(f.until)));
    membershipRefusedWhen(r -> obj(r, "payload").put("principal_ref", "human-2"));
    membershipRefusedWhen(r -> {
      var memberships = new ArrayList<>(List.of(record("membership_ref", "staff-membership",
          "roles", new ArrayList<>(List.of("atendimento", "gestor")), "groups",
          new ArrayList<>(List.of("atendimento-humano", "medico-auditor")))));
      obj(r, "payload").put("memberships", memberships);
    });
    membershipRefusedWhen(r -> {
      // A consistent forgery: payload, digest and receipt all say revision 2; the row says 1.
      var forged = projection(m.revision(2));
      r.put("payload", forged);
      r.put("source", membershipProvenance(TENANT, forged, clock.instant().minusSeconds(1), 300));
    });
  }

  @Test
  void theT15ProvenanceContractIsEnforced() {
    membershipRefusedWhen(r -> obj(r, "source").put("source_revision", "2"));
    membershipRefusedWhen(r -> obj(r, "source").put("source_digest", "f".repeat(64)));
    membershipRefusedWhen(r -> obj(r, "source").put("receipt_ref",
        "portal-identity:" + TENANT + ":membership:human-1@2"));
    membershipRefusedWhen(r -> obj(r, "source").put("receipt_ref",
        "portal-identity:other-tenant:membership:human-1@1"));
    membershipRefusedWhen(r -> obj(r, "source").put("valid_until",
        time(Fields.time(obj(r, "source"), "observed_at").plusSeconds(299))));
    membershipRefusedWhen(r -> obj(r, "source").put("publisher_ref", "other-publisher"));
  }

  @Test
  void requestNotBoundToTheAdmittedEngineAndScopeIsRefused() {
    membershipRefusedWhen(r -> r.put("engine_name", "other-engine"));
    membershipRefusedWhen(r -> r.put("database_incarnation", "incarnation-other"));
    membershipRefusedWhen(r -> r.put("read_deployment_ref", "read-release-2"));
    membershipRefusedWhen(r -> r.put("read_deployment_digest", "d".repeat(64)));
    membershipRefusedWhen(r -> obj(r, "scope").put("tenant", "other-tenant"));
    membershipRefusedWhen(r -> obj(r, "scope").put("environment", "prod"));
    membershipRefusedWhen(r -> obj(r, "scope").put("workload_ref", "portal-staff"));
    membershipRefusedWhen(r -> r.put("schema", "portal-read-publication.v2"));
    membershipRefusedWhen(r -> r.put("extra", "x"));
  }

  @Test
  void absentRowAndRowOfAnotherTenantAreRefused() throws Exception {
    var other = member("human-9", f.until.minusSeconds(60));
    // Same issuer/subject exists ONLY under another tenant: the observer filters by the admitted
    // tenant, never by what the publication claims.
    f.putMembership("other-tenant", other);
    refusedPublication(membership(other));
    // Even a row whose PAYLOAD claims the admitted tenant is not read from another tenant's key.
    f.putMembership("other-tenant", other.issuer(), other.subject(), other.principal(),
        membershipRow(TENANT, other));
    refusedPublication(membership(other));
    f.putMembership(TENANT, other);
    publishes(membership(other));
    f.deleteMembership(TENANT, m.issuer(), m.subject());
    refusedPublication(membership(m));
    f.putMembership(TENANT, m);
    publishes(membership(m));
  }

  @Test
  void rowChangedAfterThePublisherReadItIsRefused() throws Exception {
    var stale = membership(m);
    f.putMembership(TENANT, m.revision(2));
    refusedPublication(stale);
    publishes(membership(m.revision(2)));
  }

  @Test
  void rowThatIsNotAValidMembershipRecordIsRefused() throws Exception {
    String row = membershipRow(TENANT, m);
    for (String bad : List.of(row.replace("\"revision\":1", "\"revision\":\"1\""),
             row.replace("\"revision\":1", "\"revision\":1.0"),
             row.replace("\"revoked\":false", "\"revoked\":false,\"extra\":1"),
             row.replace("\"roles\":[\"atendimento\"]", "\"roles\":[]"),
             row.replace("Z\"", "\""), row.replace("\"tenant\":\"" + TENANT, "\"tenant\":\"x"),
             row.substring(0, row.length() - 1))) {
      f.putMembership(TENANT, m.issuer(), m.subject(), m.principal(), bad);
      refusedPublication(membership(m));
    }
    f.putMembership(TENANT, m);
    publishes(membership(m));
  }

  @Test
  void theQualifiedSnapshotIsTheOnlyThingVerifyAccepts() {
    var request = membership(m);
    var q = p.qualifyPublication(a, request);
    var other = deep(request);
    obj(other, "payload").put("state", "revoked");
    refused(() -> q.verify("membership", obj(other, "source"), obj(other, "payload")));
    refused(() -> q.verify("catalog-designate", obj(request, "source"), obj(request, "payload")));
    refused(() -> q.verify("membership", null, obj(request, "payload")));
    // A different but self-consistent source (other observation time) is not the qualified one.
    var moved = membershipProvenance(TENANT, projection(m), clock.instant().minusSeconds(2), 300);
    refused(() -> q.verify("membership", moved, obj(request, "payload")));
    q.verify("membership", obj(request, "source"), obj(request, "payload"));
    // Kinds with no per-kind check still cannot be swapped for each other.
    var revoke = f.publication("catalog-revoke", plainProvenance(CATALOG + ":revoke",
        clock.instant().minusSeconds(1), f.until), record("catalog_ref", CATALOG,
        "expected_catalog_revision", "1"), 0);
    var rq = p.qualifyPublication(a, revoke);
    refused(() -> rq.verify("revoke-key", obj(revoke, "source"), obj(revoke, "payload")));
    rq.verify("catalog-revoke", obj(revoke, "source"), obj(revoke, "payload"));
  }

  @Test
  void theSnapshotExpiresAfterObservationSecondsAndDiesWithTheAdmission() throws Exception {
    var request = membership(m);
    var q = p.qualifyPublication(a, request);
    clock.advance(301);
    refused(q::requireCurrent);
    refused(() -> q.verify("membership", obj(request, "source"), obj(request, "payload")));
    clock.advance(-301);
    q.requireCurrent();
    f.revoke(1);
    refused(() -> admission(p));
    refused(q::requireCurrent);
    f.install(f.publicationAdmission(2));
    a = admission(p);
    publishes(membership(m));
  }

  @Test
  void onlyAPublicationAdmissionOfThisProviderQualifies() throws Exception {
    var read = p.acquire(scope(), f.engine, f.incarnation, f.deployment, f.deploymentDigest,
        f.trustDigest, "portal-task-read");
    refused(() -> p.qualifyPublication(read, membership(m)));
    var foreign = admission(new InstalledReadProviders(f.providerFile, clock));
    refused(() -> p.qualifyPublication(foreign, membership(m)));
    refused(() -> p.qualifyPublication(null, membership(m)));
    refused(() -> p.qualifyPublication(a, null));
    publishes(membership(m));
  }

  @Test
  void resourceAndUnknownKindsAreRefused() {
    var resource = f.publication("resource", plainProvenance("resource-1", clock.instant(),
        f.until), record("task_id", "task-1"), 0);
    refusedPublication(resource);
    var unknown = deep(resource);
    unknown.put("kind", "principal");
    refusedPublication(unknown);
    publishes(membership(m));
  }

  @Test
  void catalogDesignationMustBeTheAdmittedCatalog() {
    Instant now = clock.instant();
    var payload = record("catalog_ref", CATALOG, "catalog_revision", "1", "catalog_digest",
        "a".repeat(64), "catalog_artifact_base64", "e30=", "deployment_receipt_ref",
        "deployment-1", "deployment_receipt_digest", "b".repeat(64), "valid_until",
        time(f.until));
    var valid = f.publication("catalog-designate",
        plainProvenance(CATALOG + ":1", now.minusSeconds(1), f.until), payload, 0);
    for (var breakIt : List.<Consumer<Map<String, Object>>>of(
             r -> obj(r, "payload").put("catalog_digest", "f".repeat(64)),
             r -> obj(r, "payload").put("catalog_ref", "other-catalog"),
             r -> obj(r, "source").put("publisher_ref", "other-publisher"))) {
      var broken = deep(valid);
      breakIt.accept(broken);
      refusedPublication(broken);
      publishes(valid);
    }
  }

  @Test
  void revocationsQualifyWithoutASource() {
    Instant now = clock.instant();
    publishes(f.publication("catalog-revoke", plainProvenance(CATALOG + ":revoke",
        now.minusSeconds(1), f.until), record("catalog_ref", CATALOG,
        "expected_catalog_revision", "1"), 0));
    publishes(f.publication("revoke-key", plainProvenance("key-revoke", now.minusSeconds(1),
        f.until), record("key_fingerprint", "c".repeat(64)), 0));
  }

  @Test
  void membershipPublisherMustBeTheAdmittedOne() throws Exception {
    var admission = f.publicationAdmission(2);
    @SuppressWarnings("unchecked")
    var publishers = (List<Object>) admission.get("publishers");
    publishers.removeIf(o -> "membership".equals(((Map<?, ?>) o).get("kind")));
    f.install(admission);
    var without = admission(p); // the publisher scope still resolves through catalog-designate
    refused(() -> p.qualifyPublication(without, membership(m)));
    f.install(f.publicationAdmission(3));
    a = admission(p);
    publishes(membership(m));
  }

  // ------------------------------------------------------------------ the observer's posture

  @Test
  void theSourceIsReadOnlyOverVerifyFullTlsWithThePinnedCa() throws Exception {
    f.writeDsn(f.dsn(f.tls.mismatchHost()), "r--------"); // right CA, name not in the SAN
    refusedPublication(membership(m));
    f.writeDsn(f.dsn(f.tls.host()), "r--------");
    publishes(membership(m));
    var config = f.providerConfiguration();
    obj(config, "membership_source").put("ca_file", f.tls.otherCa().toString());
    f.writeProvider(config);
    var otherCa = new InstalledReadProviders(f.providerFile, clock);
    var admitted = admission(otherCa);
    refused(() -> otherCa.qualifyPublication(admitted, membership(m)));
    f.writeProvider(f.providerConfiguration());
    publishes(membership(m));
  }

  @Test
  void anObserverThatCanWriteOrOwnTheSourceIsRefused() throws Exception {
    String table = f.sourceSchema + ".portal_memberships";
    for (String grant : List.of("GRANT UPDATE (payload) ON " + table + " TO " + f.sourceLogin,
             "GRANT INSERT ON " + table + " TO " + f.sourceLogin,
             "GRANT DELETE ON " + table + " TO " + f.sourceLogin,
             "GRANT TRUNCATE ON " + table + " TO " + f.sourceLogin,
             "GRANT " + f.sourceOwner + " TO " + f.sourceLogin)) {
      f.adminExecute(grant);
      refusedPublication(membership(m));
      f.adminExecute(grant.replaceFirst("^GRANT", "REVOKE").replace(" TO ", " FROM "));
      publishes(membership(m));
    }
    // A writer reachable only by SET ROLE (WITH INHERIT FALSE): has_*_privilege(session_user)
    // does not see it, so only "member of no role" refuses it.
    String writer = "rp_writer_" + f.suffix;
    f.adminExecute("CREATE ROLE " + writer + " NOLOGIN");
    f.adminExecute("GRANT USAGE ON SCHEMA " + f.sourceSchema + " TO " + writer);
    f.adminExecute("GRANT SELECT, UPDATE ON " + table + " TO " + writer);
    f.adminExecute("GRANT " + writer + " TO " + f.sourceLogin + " WITH INHERIT FALSE");
    refusedPublication(membership(m));
    f.adminExecute("REVOKE " + writer + " FROM " + f.sourceLogin);
    publishes(membership(m));
    f.adminExecute("REVOKE SELECT (payload) ON " + table + " FROM " + f.sourceLogin);
    refusedPublication(membership(m));
    f.adminExecute("GRANT SELECT (payload) ON " + table + " TO " + f.sourceLogin);
    publishes(membership(m));
  }

  @Test
  void aViewStandingInForTheSourceTableIsRefused() throws Exception {
    String table = f.sourceSchema + ".portal_memberships";
    f.adminExecute("ALTER TABLE " + table + " RENAME TO portal_memberships_real");
    f.adminExecute("SET ROLE " + f.sourceOwner + "; CREATE VIEW " + table
        + " AS SELECT * FROM " + f.sourceSchema + ".portal_memberships_real; RESET ROLE");
    f.adminExecute("GRANT SELECT (tenant, issuer, subject, payload) ON " + table + " TO "
        + f.sourceLogin);
    refusedPublication(membership(m));
    f.adminExecute("DROP VIEW " + table);
    f.adminExecute("ALTER TABLE " + f.sourceSchema + ".portal_memberships_real RENAME TO "
        + "portal_memberships");
    publishes(membership(m));
  }

  @Test
  void theDsnFileIsASecretInCustody() throws Exception {
    String dsn = f.dsn(f.tls.host());
    f.writeDsn(dsn, "r--r--r--");
    refusedPublication(membership(m));
    f.writeDsn(dsn + "?sslmode=disable", "r--------");
    refusedPublication(membership(m));
    f.writeDsn(dsn.replace("postgresql://", "postgresql+asyncpg://"), "r--------");
    refusedPublication(membership(m));
    Files.delete(f.dsnFile);
    refusedPublication(membership(m));
    f.writeDsn(dsn + "\n", "r--------"); // one trailing LF, as secret files usually end
    publishes(membership(m));
  }

  @Test
  void nothingSecretLeaksThroughToString() {
    var q = p.qualifyPublication(a, membership(m));
    assertFalse(q.toString().contains(m.subject()));
    assertFalse(p.toString().contains(f.sourceLogin));
    var dsn = MembershipSourceObserver.dsn(f.dsn(f.tls.host()).getBytes());
    assertEquals("MembershipSourceDsn[redacted]", dsn.toString());
  }
}
