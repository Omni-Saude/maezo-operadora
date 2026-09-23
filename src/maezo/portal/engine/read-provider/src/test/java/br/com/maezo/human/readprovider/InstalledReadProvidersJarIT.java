package br.com.maezo.human.readprovider;

import static br.com.maezo.human.readprovider.TestRecords.*;
import static org.junit.jupiter.api.Assertions.*;

import br.com.maezo.human.Jcs;
import br.com.maezo.human.PortalReadTrust;
import br.com.maezo.human.Rejected;
import java.nio.charset.StandardCharsets;
import java.time.Clock;
import java.time.Instant;
import java.time.ZoneId;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.concurrent.atomic.AtomicReference;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.function.Executable;

/** The provider itself, against the real table, the built JAR and a controllable clock. */
class InstalledReadProvidersJarIT {
  ProviderFixture f;
  final Moving clock = new Moving();

  static final class Moving extends Clock {
    final AtomicReference<Instant> now = new AtomicReference<>(Instant.now());

    @Override
    public ZoneId getZone() {
      return ZoneOffset.UTC;
    }

    @Override
    public Clock withZone(ZoneId zone) {
      return this;
    }

    @Override
    public Instant instant() {
      return now.get();
    }

    void advance(long seconds) {
      now.updateAndGet(t -> t.plusSeconds(seconds));
    }
  }

  @BeforeEach
  void start() throws Exception {
    f = new ProviderFixture();
  }

  @AfterEach
  void stop() throws Exception {
    f.close();
  }

  InstalledReadProviders provider() {
    return new InstalledReadProviders(f.providerFile, clock);
  }

  PortalReadTrust.Admission acquire(InstalledReadProviders p) {
    return acquire(p, "portal-task-read", scope());
  }

  PortalReadTrust.Admission acquire(
      InstalledReadProviders p, String purpose, Map<String, Object> scope) {
    return p.acquire(scope, f.engine, f.incarnation, f.deployment, f.deploymentDigest,
        f.trustDigest, purpose);
  }

  static void refused(Executable action) {
    Rejected r = assertThrows(Rejected.class, action);
    assertEquals(503, r.status);
    assertEquals("READ_DEPENDENCY_UNAVAILABLE", r.code);
  }

  @Test
  void retainedAdmissionExpiresAfterObservationSeconds() throws Exception {
    f.install(f.admission(1));
    var p = provider();
    var a = acquire(p);
    assertEquals(clock.instant(), a.observedAt());
    assertEquals(clock.instant().plusSeconds(300), a.validUntil());
    clock.advance(299);
    a.requireCurrent();
    clock.advance(1);
    refused(a::requireCurrent);
    acquire(p).requireCurrent(); // a fresh observation is current again
  }

  @Test
  void admissionValidityCapsTheObservationWindow() throws Exception {
    var short_ = f.admission(1);
    Instant end = now().plusSeconds(100);
    short_.put("valid_until", time(end));
    f.install(short_);
    assertEquals(end, acquire(provider()).validUntil());
  }

  @Test
  void laterRevisionSupersedesTheRetainedAdmission() throws Exception {
    f.install(f.admission(1));
    var p = provider();
    var first = acquire(p);
    f.install(f.admission(2));
    var second = acquire(p);
    assertEquals("2", second.generation());
    refused(first::requireCurrent);
    second.requireCurrent();
  }

  @Test
  void revokedRowKillsTheRetainedAdmissionAndItsKeys() throws Exception {
    f.install(f.admission(1));
    var p = provider();
    var a = acquire(p);
    var keys = p.continuity(a);
    keys.requireCurrent();
    f.revoke(1);
    refused(() -> acquire(p));
    refused(a::requireCurrent);
    refused(keys::requireCurrent);
    refused(keys::current);
    refused(() -> p.continuity(a));
  }

  @Test
  void continuityKeyDigestIsTheAdmittedCommitmentNeverAHashOfTheKey() throws Exception {
    f.install(f.admission(1));
    var p = provider();
    var keys = p.continuity(acquire(p));
    var key = keys.current();
    assertEquals(KEY_ID, key.id);
    assertEquals("1", key.generation);
    assertEquals(f.commitment(), key.digest);
    assertNotEquals(Jcs.digest(f.secret), key.digest);
    assertSame(key, keys.verification(KEY_ID));
    assertNull(keys.verification("native-continuity-unknown"));
    assertFalse(key.toString().contains(java.util.Base64.getEncoder().encodeToString(f.secret)));
    assertEquals("NativeKeySet[redacted]", keys.toString());
  }

  @Test
  void keyInTheFileThatTheAdmissionDoesNotCommitRefusesContinuity() throws Exception {
    f.install(f.admission(1));
    byte[] other = new byte[32];
    other[0] = 1;
    writeKeys(f.keysFile,
        List.of(continuityKey(KEY_ID, "1", f.secret, f.before, f.until),
            continuityKey("native-continuity-2", "2", other, f.before, f.until)),
        "r--------");
    var p = provider();
    var a = acquire(p);
    refused(() -> p.continuity(a));
  }

  @Test
  void commitmentOutsideTheAdmissionRefusesContinuity() throws Exception {
    var wrong = f.admission(1);
    ((Map<String, Object>) ((List<Object>) wrong.get("continuity_keys")).get(0))
        .put("commitment", "b".repeat(64));
    f.install(wrong);
    var p = provider();
    var a = acquire(p);
    refused(() -> p.continuity(a));
  }

  @Test
  void keyFileWithoutOwnerOnlyReadModeRefusesEverything() throws Exception {
    f.install(f.admission(1));
    for (String mode : List.of("rw-------", "r--r-----", "r-----r--")) {
      writeKeys(f.keysFile, List.of(continuityKey(KEY_ID, "1", f.secret, f.before, f.until)), mode);
      refused(() -> acquire(provider()));
    }
    writeKeys(
        f.keysFile, List.of(continuityKey(KEY_ID, "1", f.secret, f.before, f.until)), "r--------");
    var p = provider();
    p.continuity(acquire(p)).requireCurrent();
  }

  @Test
  void noCurrentKeyRefusesContinuity() throws Exception {
    Instant later = now().plusSeconds(3600);
    var future = f.admission(1);
    var entry = (Map<String, Object>) ((List<Object>) future.get("continuity_keys")).get(0);
    entry.put("not_before", time(later));
    entry.put("not_after", time(later.plusSeconds(3600)));
    f.install(future);
    writeKeys(f.keysFile,
        List.of(continuityKey(KEY_ID, "1", f.secret, later, later.plusSeconds(3600))),
        "r--------");
    var p = provider();
    var a = acquire(p);
    refused(() -> p.continuity(a));
  }

  @Test
  void continuityOfAForeignAdmissionIsRefused() throws Exception {
    f.install(f.admission(1));
    var p = provider();
    var other = provider();
    var foreign = acquire(other);
    refused(() -> p.continuity(foreign));
  }

  @Test
  void verifySourceAdmitsOnlyThePublisherPrefixKindAndWindow() throws Exception {
    f.install(f.admission(1));
    var a = acquire(provider());
    Instant now = clock.instant();
    var good = record("publisher_ref", PUBLISHER, "source_ref", MEMBERSHIP_PREFIX + "human-1",
        "source_revision", "3", "source_digest", "f".repeat(64), "receipt_ref",
        MEMBERSHIP_PREFIX + "human-1@3", "observed_at", time(now.minusSeconds(5)), "valid_until",
        time(now.plusSeconds(60)));
    a.verifySource("membership", good);
    var catalog = new java.util.TreeMap<>(good);
    catalog.put("source_ref", CATALOG + ":1");
    a.verifySource("catalog-designate", catalog);
    refused(() -> a.verifySource("resource", good));
    refused(() -> a.verifySource("catalog-revoke", good));
    refused(() -> a.verifySource("membership", catalog));
    var publisher = new java.util.TreeMap<>(good);
    publisher.put("publisher_ref", "another-publisher");
    refused(() -> a.verifySource("membership", publisher));
    var future = new java.util.TreeMap<>(good);
    future.put("observed_at", time(now.plusSeconds(5)));
    refused(() -> a.verifySource("membership", future));
    var expired = new java.util.TreeMap<>(good);
    expired.put("valid_until", time(now));
    refused(() -> a.verifySource("membership", expired));
    refused(() -> a.verifySource("membership", null));
  }

  /** Finding 5: a prefix is a whole segment, so `amh` never admits `amhx`. */
  @Test
  void sourceRefPrefixIsASegmentNotACharacterPrefix() throws Exception {
    var admission = f.admission(1);
    ((Map<String, Object>) ((List<Object>) admission.get("publishers")).get(0))
        .put("source_ref_prefix", "portal-identity:amh:");
    f.install(admission);
    var a = acquire(provider());
    Instant now = clock.instant();
    var source = record("publisher_ref", PUBLISHER, "source_ref", "portal-identity:amh:human-1",
        "source_revision", "1", "source_digest", "f".repeat(64), "receipt_ref", "r-1",
        "observed_at", time(now.minusSeconds(1)), "valid_until", time(now.plusSeconds(60)));
    a.verifySource("membership", source);
    var neighbour = new java.util.TreeMap<>(source);
    neighbour.put("source_ref", "portal-identity:amhx:human-1");
    refused(() -> a.verifySource("membership", neighbour));
  }

  /**
   * Finding 2: the live engine states, once per admitted revision, WHICH root and WHICH admission
   * bytes it accepted. Nothing secret rides on that line.
   */
  @Test
  void firstAcquireOfEachRevisionEmitsOnePublicLineWithoutSecrets() throws Exception {
    List<String> lines = new java.util.concurrent.CopyOnWriteArrayList<>();
    var handler = new java.util.logging.Handler() {
      @Override
      public void publish(java.util.logging.LogRecord r) {
        lines.add(r.getMessage());
      }

      @Override
      public void flush() {}

      @Override
      public void close() {}
    };
    InstalledReadProviders.LOG.addHandler(handler);
    try {
      var admission = f.admission(1);
      f.install(admission);
      var p = provider();
      acquire(p);
      acquire(p);
      p.continuity(acquire(p)).current();
      String rootPin = Jcs.digest(f.root.getPublic().getEncoded());
      assertEquals(List.of("portal_read_provider root_sha256=" + rootPin + " admission_ref="
                       + ADMISSION_REF + " revision=1 capability_digest="
                       + Jcs.digest(Jcs.canonical(admission)) + " engine_code=" + f.engineCode
                       + " provider_code=" + f.providerCode),
          lines);
      f.install(f.admission(2));
      acquire(p);
      acquire(p);
      assertEquals(2, lines.size());
      assertTrue(lines.get(1).contains(" revision=2 "));
      String secret = java.util.Base64.getEncoder().encodeToString(f.secret);
      for (String line : lines) {
        assertFalse(line.contains(secret));
        assertFalse(line.contains(java.util.HexFormat.of().formatHex(f.secret)));
        assertFalse(line.contains(f.commitment()));
        assertFalse(line.contains(f.adminPassword));
        assertFalse(line.contains("jdbc:"));
        assertFalse(line.contains("{"), "no record body");
        assertTrue(line.matches("portal_read_provider( [a-z0-9_]+=[A-Za-z0-9_.:@/-]+){6}"));
      }
    } finally {
      InstalledReadProviders.LOG.removeHandler(handler);
    }
  }

  @Test
  void verifyCatalogComparesTheAdmittedDigest() throws Exception {
    var artifact = record("schema", "portal-read-catalog.v1", "catalog_ref", CATALOG,
        "publisher_ref", PUBLISHER, "entries", new ArrayList<>(), "policies", new ArrayList<>(),
        "forms", new ArrayList<>(), "deployment_receipt_ref", "deployment-1",
        "deployment_receipt_digest", "9".repeat(64));
    var admission = f.admission(1);
    ((Map<String, Object>) admission.get("catalog"))
        .put("catalog_digest", Jcs.digest(Jcs.canonical(artifact)));
    f.install(admission);
    var a = acquire(provider());
    a.verifyCatalog(artifact);
    var changed = new java.util.TreeMap<>(artifact);
    changed.put("deployment_receipt_ref", "deployment-2");
    refused(() -> a.verifyCatalog(changed));
    refused(() -> a.verifyCatalog(null));
  }

  @Test
  void taskAuthorityRefusedAndTaskReadAdmissionQualifiesNothing() throws Exception {
    f.install(f.admission(1));
    var p = provider();
    var a = acquire(p);
    refused(() -> a.verifyIdentityPolicy(record(), record(), List.of()));
    refused(() -> a.verifyClassification(record(), record()));
    refused(() -> p.qualifyPublication(a, record("kind", "membership")));
  }

  @Test
  void purposeAndScopeMustBeAdmitted() throws Exception {
    var admission = f.admission(1);
    f.install(admission);
    var p = provider();
    refused(() -> acquire(p, "portal-read-publication", record("tenant", "tenant-test",
        "environment", "dev", "workload_ref", PUBLISHER)));
    var otherWorkload = scope();
    otherWorkload.put("workload_ref", "portal-other");
    refused(() -> acquire(p, "portal-task-read", otherWorkload));
    var extra = scope();
    extra.put("engine_name", f.engine);
    refused(() -> acquire(p, "portal-task-read", extra));
    refused(() -> acquire(p, "portal-task-read", null));

    var both = f.admission(2);
    both.put("purposes", new ArrayList<>(List.of("portal-task-read", "portal-read-publication")));
    f.install(both);
    var publication = acquire(p, "portal-read-publication",
        record("tenant", "tenant-test", "environment", "dev", "workload_ref", PUBLISHER));
    assertEquals("2", publication.generation());
    refused(() -> acquire(p, "portal-read-publication", scope()));
  }

  @Test
  void everyInstallationBindingIsComparedExactly() throws Exception {
    f.install(f.admission(1));
    var p = provider();
    refused(() -> p.acquire(scope(), "other-engine", f.incarnation, f.deployment,
        f.deploymentDigest, f.trustDigest, "portal-task-read"));
    refused(() -> p.acquire(scope(), f.engine, f.incarnation + "-restored", f.deployment,
        f.deploymentDigest, f.trustDigest, "portal-task-read"));
    refused(() -> p.acquire(scope(), f.engine, f.incarnation, "read-release-2",
        f.deploymentDigest, f.trustDigest, "portal-task-read"));
    refused(() -> p.acquire(scope(), f.engine, f.incarnation, f.deployment, "e".repeat(64),
        f.trustDigest, "portal-task-read"));
    refused(() -> p.acquire(scope(), f.engine, f.incarnation, f.deployment,
        f.deploymentDigest, "e".repeat(64), "portal-task-read"));
    acquire(p).requireCurrent();
  }

  @Test
  void tablePinIsExactOidOwnerAndSchema() throws Exception {
    f.install(f.admission(1));
    var configuration = f.providerConfiguration();
    configuration.put("admission_table_oid", Long.toString(f.tableOid + 1));
    f.writeProvider(configuration);
    refused(() -> acquire(provider()));
    configuration = f.providerConfiguration();
    configuration.put("admission_table_owner", f.runtime);
    f.writeProvider(configuration);
    refused(() -> acquire(provider()));
    configuration = f.providerConfiguration();
    configuration.put("native_schema", f.schema + "_v2");
    f.writeProvider(configuration);
    refused(() -> acquire(provider()));
    f.writeProvider(f.providerConfiguration());
    acquire(provider()).requireCurrent();
  }

  @Test
  void revisionBelowTheConfiguredMinimumIsRefused() throws Exception {
    f.install(f.admission(1));
    var configuration = f.providerConfiguration();
    configuration.put("minimum_admission_revision", "2");
    f.writeProvider(configuration);
    refused(() -> acquire(provider()));
    f.install(f.admission(2));
    assertEquals("2", acquire(provider()).generation());
  }

  @Test
  void storedRecordMustBeTheExactSignedCanonicalBytes() throws Exception {
    byte[] canonical = Jcs.canonical(f.admission(1));
    byte[] spaced = (" " + new String(canonical, StandardCharsets.UTF_8))
                        .getBytes(StandardCharsets.UTF_8);
    f.installRaw(1, spaced, sign(f.root.getPrivate(), spaced)); // valid signature, not JCS
    refused(() -> acquire(provider()));
    byte[] mismatched = Jcs.canonical(f.admission(3));
    f.installRaw(2, mismatched, sign(f.root.getPrivate(), mismatched)); // row says 2, record 3
    refused(() -> acquire(provider()));
  }

  @Test
  void brokenProviderRefusesEveryMethod() throws Exception {
    f.install(f.admission(1));
    var broken = new InstalledReadProviders(f.providerFile.resolveSibling("absent.json"), clock);
    refused(() -> acquire(broken));
    var admission = acquire(provider());
    refused(() -> broken.continuity(admission));
    refused(() -> broken.qualifyPublication(admission, record()));
  }
}
