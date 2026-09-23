package br.com.maezo.human;

import static org.junit.jupiter.api.Assertions.*;

import br.com.maezo.human.readprovider.ProviderFixture;
import br.com.maezo.human.readprovider.TestRecords;
import java.io.IOException;
import java.net.URL;
import java.net.URLClassLoader;
import java.nio.file.Files;
import java.nio.file.Path;
import java.sql.SQLException;
import java.time.Instant;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Enumeration;
import java.util.List;
import java.util.Map;
import org.cibseven.bpm.engine.impl.cfg.ProcessEngineConfigurationImpl;
import org.cibseven.bpm.engine.impl.cfg.StandaloneProcessEngineConfiguration;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.function.Executable;

/**
 * T1.7a readiness, directly on the lease (the HTTP route also needs T1.1's composition): the REAL
 * {@link PortalReadPlugin}, with the {@code MAEZO_PORTAL_READ_TRUST_FILE} it reads in the image,
 * discovers the provider through {@code ServiceLoader} from the BUILT JAR, boots ({@code preInit}),
 * and {@link PortalReadPlugin#staffLease} returns a {@code Q2Lease} whose Admission is the
 * provider's. Every negative is paired with the positive control on the same fixture, so a refusal
 * can never pass because the fixture itself was broken.
 */
class ReadProviderBootJarIT {
  static final String SERVICE = "META-INF/services/br.com.maezo.human.PortalReadTrust$Providers";
  ProviderFixture f;

  @BeforeEach
  void start() throws Exception {
    f = new ProviderFixture();
  }

  @AfterEach
  void stop() throws Exception {
    f.close();
  }

  record Booted(PortalReadPlugin plugin, ProcessEngineConfigurationImpl configuration) {
    StaffCaseReadCommand.Q2Lease lease(ProviderFixture f) {
      return PortalReadPlugin.staffLease(configuration, f.staffScope(), f.anchor());
    }
  }

  Booted boot() {
    var plugin = new PortalReadPlugin();
    var configuration = new StandaloneProcessEngineConfiguration();
    configuration.setProcessEngineName(f.engine);
    configuration.setProcessEnginePlugins(new ArrayList<>(List.of(plugin)));
    plugin.preInit(configuration);
    return new Booted(plugin, configuration);
  }

  static void refused(Executable action) {
    Rejected r = assertThrows(Rejected.class, action);
    assertEquals(503, r.status);
    assertEquals("READ_DEPENDENCY_UNAVAILABLE", r.code);
  }

  void bootRefused() {
    refused(this::boot);
  }

  static void withContextLoader(ClassLoader loader, Executable action) throws Throwable {
    Thread t = Thread.currentThread();
    ClassLoader previous = t.getContextClassLoader();
    t.setContextClassLoader(loader);
    try {
      action.execute();
    } finally {
      t.setContextClassLoader(previous);
    }
  }

  @Test
  void realProviderFromTheBuiltJarBootsAndTheStaffLeaseCarriesItsAdmission() throws Exception {
    Path jar = Path.of(Class.forName("br.com.maezo.human.readprovider.InstalledReadProviders")
                           .getProtectionDomain().getCodeSource().getLocation().toURI());
    assertEquals(Path.of(System.getProperty("maezo.provider.jar")).toRealPath(), jar.toRealPath(),
        "the provider under test must be the packaged JAR, not target/classes");
    var admission = f.admission(1);
    f.install(admission);
    Instant start = Instant.now();
    var lease = boot().lease(f);
    assertEquals("br.com.maezo.human.readprovider.ProviderAdmission",
        lease.admission.getClass().getName());
    assertEquals(TestRecords.ADMISSION_REF, lease.admission.providerRef());
    assertEquals("1", lease.admission.generation());
    assertEquals("1", lease.admission.providerRevision());
    assertEquals(Jcs.digest(Jcs.canonical(admission)), lease.admission.capabilityDigest());
    assertEquals(5, lease.admission.statementTimeoutSeconds());
    assertFalse(lease.admission.observedAt().isBefore(start));
    assertFalse(lease.admission.validUntil().isAfter(Instant.now().plusSeconds(300)));
    assertEquals(f.anchor(), lease.anchor);
    lease.admission.requireCurrent();
  }

  @Test
  void missingProviderRegistrationRefusesBoot() throws Throwable {
    f.install(f.admission(1));
    var hiding = new ClassLoader(Thread.currentThread().getContextClassLoader()) {
      @Override
      public Enumeration<URL> getResources(String name) throws IOException {
        return name.equals(SERVICE) ? Collections.emptyEnumeration() : super.getResources(name);
      }
    };
    withContextLoader(hiding, this::bootRefused);
    boot().lease(f); // control: the same fixture boots with the registration visible
  }

  @Test
  void duplicateProviderRegistrationRefusesBoot() throws Throwable {
    f.install(f.admission(1));
    Path extra = Files.createTempDirectory("read-provider-duplicate");
    Path file = extra.resolve(SERVICE);
    Files.createDirectories(file.getParent());
    Files.writeString(file, "br.com.maezo.human.readprovider.ShadowProvider\n");
    try (var duplicate = new URLClassLoader(new URL[] {extra.toUri().toURL()},
             Thread.currentThread().getContextClassLoader())) {
      assertEquals(2, Collections.list(duplicate.getResources(SERVICE)).size());
      withContextLoader(duplicate, this::bootRefused);
    }
    boot().lease(f);
  }

  @Test
  void missingProviderConfigurationRefusesBoot() throws Exception {
    f.install(f.admission(1));
    Files.delete(f.providerFile);
    bootRefused();
    f.writeProvider(f.providerConfiguration());
    boot().lease(f);
  }

  @Test
  void missingAdmissionRowRefusesBoot() throws Exception {
    bootRefused();
    f.install(f.admission(1));
    boot().lease(f);
  }

  @Test
  void admissionSignedByAnotherRootRefusesBoot() throws Exception {
    f.install(f.admission(1), f.otherRoot);
    bootRefused();
    f.install(f.admission(2));
    boot().lease(f);
  }

  @Test
  void revokedAdmissionRefusesBoot() throws Exception {
    f.install(f.admission(1));
    f.revoke(1);
    bootRefused();
    f.install(f.admission(2));
    boot().lease(f);
  }

  @Test
  void expiredAndNotYetValidAdmissionsRefuseBoot() throws Exception {
    Instant now = TestRecords.now();
    var expired = f.admission(1);
    expired.put("not_before", TestRecords.time(now.minusSeconds(7200)));
    expired.put("valid_until", TestRecords.time(now.minusSeconds(1)));
    f.install(expired);
    bootRefused();
    var early = f.admission(2);
    early.put("not_before", TestRecords.time(now.plusSeconds(3600)));
    early.put("valid_until", TestRecords.time(now.plusSeconds(7200)));
    f.install(early);
    bootRefused();
    f.install(f.admission(3));
    boot().lease(f);
  }

  @Test
  void swappedTrustConfigurationDigestRefusesBoot() throws Exception {
    var swapped = f.admission(1);
    swapped.put("trust_configuration_digest", "e".repeat(64));
    f.install(swapped);
    bootRefused();
    f.install(f.admission(2));
    boot().lease(f);
  }

  @Test
  void codeDigestsOfAnotherJarRefuseBoot() throws Exception {
    var provider = f.admission(1);
    ((Map<String, Object>) provider.get("code_digests")).put("provider", f.engineCode);
    f.install(provider);
    bootRefused();
    var engine = f.admission(2);
    ((Map<String, Object>) engine.get("code_digests")).put("engine", f.providerCode);
    f.install(engine);
    bootRefused();
    f.install(f.admission(3));
    boot().lease(f);
  }

  @Test
  void revocationAfterBootRefusesTheNextStaffLeaseAndKillsTheRetainedOne() throws Exception {
    f.install(f.admission(1));
    var booted = boot();
    var retained = booted.lease(f);
    retained.admission.requireCurrent();
    f.revoke(1);
    refused(() -> booted.lease(f));
    refused(retained.admission::requireCurrent);
  }

  @Test
  void rollbackToAnEarlierAdmissionIsRefusedWhileTheEngineRuns() throws Exception {
    f.install(f.admission(1));
    f.install(f.admission(2));
    var booted = boot();
    assertEquals("2", booted.lease(f).admission.generation());
    f.delete(2); // revision 1 is still validly signed: reinstalling it is a rollback
    refused(() -> booted.lease(f));
    f.install(f.admission(3));
    assertEquals("3", booted.lease(f).admission.generation());
  }

  @Test
  void engineLoginThatCanWriteTheAdmissionTableIsRefused() throws Exception {
    f.install(f.admission(1));
    try (var c = f.asRuntime(); var s = c.createStatement()) {
      var denied = assertThrows(SQLException.class,
          () -> s.executeUpdate("UPDATE " + f.schema + ".mzo_portal_read_admission "
              + "SET revoked_=false"));
      assertEquals("42501", denied.getSQLState());
    }
    boot().lease(f);
    f.grantRuntime("INSERT");
    bootRefused();
  }
}
