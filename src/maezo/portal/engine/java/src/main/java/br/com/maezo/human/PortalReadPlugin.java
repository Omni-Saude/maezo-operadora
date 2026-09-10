package br.com.maezo.human;
import static br.com.maezo.human.PortalReadModels.*;

import java.nio.file.Path;
import java.time.Instant;
import java.util.ServiceLoader;
import org.cibseven.bpm.engine.ProcessEngine;
import org.cibseven.bpm.engine.impl.cfg.*;
/** Explicit opt-in read deployment. Missing qualified SPI prevents activation. */
public final class PortalReadPlugin extends AbstractProcessEnginePlugin {
  private PortalReadTrust trust;
  private ProcessEngineConfigurationImpl configuration;
  private static volatile PortalReadPlugin running;
  public PortalReadPlugin() {}
  PortalReadPlugin(PortalReadTrust trust) {
    this.trust = trust;
  }
  public void preInit(ProcessEngineConfigurationImpl configuration) {
    if (trust == null) {
      String file = System.getenv("MAEZO_PORTAL_READ_TRUST_FILE");
      var providers = ServiceLoader.load(PortalReadTrust.Providers.class).stream().toList();
      if (file == null || file.isBlank() || providers.size() != 1)
        throw unavailable();
      try {
        trust = PortalReadTrust.load(Path.of(file), providers.get(0).get());
      } catch (java.io.IOException ex) {
        throw unavailable();
      }
    }
    if (!trust.engine.equals(configuration.getProcessEngineName()))
      throw unavailable();
    this.configuration = configuration;
    var admission = trust.acquire("portal-task-read");
    var keys = trust.providers.continuity(admission);
    if (keys == null)
      throw unavailable();
    keys.requireCurrent();
    keys.current().current(Instant.now());
  }
  public void postInit(ProcessEngineConfigurationImpl configuration) {
    PortalReadStore.Writes.install(configuration.getSqlSessionFactory().getConfiguration());
  }
  public void postProcessEngineBuild(ProcessEngine engine) {
    configuration.getCommandExecutorTxRequired().execute(c -> {
      var a = trust.acquire("portal-task-read");
      new PortalReadStore(c, trust, a.statementTimeoutSeconds()).lockTenant();
      return null;
    });
    running = this;
  }
  static PortalReadPlugin running() {
    var p = running;
    if (p == null)
      throw unavailable();
    return p;
  }
  byte[] execute(byte[] raw, String peer, String route) {
    String purpose = route.equals("publications") ? "portal-read-publication" : "portal-task-read";
    var verified = PortalReadEnvelope.verify(raw, trust, purpose, peer, Instant.now());
    if (purpose.equals("portal-task-read") && !route.equals(verified.request().get("operation")))
      throw invalid();
    var admission = trust.acquire(purpose,
        obj(verified.request(), "scope")); // Provider/admission locks ALWAYS precede tenant lock.
    if (purpose.equals("portal-read-publication")) {
      var qualification = trust.providers.qualifyPublication(admission, verified.request());
      return configuration.getCommandExecutorTxRequired()
          .execute(new PortalReadPublication(trust, verified, admission, qualification))
          .bytes();
    }
    var keys = trust.providers.continuity(admission);
    if (keys == null)
      throw unavailable();
    keys.requireCurrent();
    return configuration.getCommandExecutorTxRequired()
        .execute(new PortalReadCommand(trust, verified, admission, keys))
        .bytes();
  }
}
