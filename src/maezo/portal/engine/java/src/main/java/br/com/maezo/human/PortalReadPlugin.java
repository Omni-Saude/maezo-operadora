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
  /** Acquired outside the engine command before its tenant lock. Optional only for
   * an explicitly qualified assignment binding with constraint_mode=none. */
  static final class AssignmentConstraintLease {
    final PortalReadTrust trust; final PortalReadTrust.Admission admission;
    AssignmentConstraintLease(PortalReadTrust trust,PortalReadTrust.Admission admission){this.trust=trust;this.admission=admission;}
    PortalReadCommand.AssignmentFacts verify(org.cibseven.bpm.engine.impl.interceptor.CommandContext context,
        java.util.Map<String,Object> principal, java.util.Map<String,Object> binding,
        java.util.Map<String,Object> designation,Runnable current) {
      if(!trust.scope.get("tenant").equals(binding.get("tenant"))||!trust.scope.get("environment").equals(binding.get("environment"))||!trust.incarnation.equals(binding.get("database_incarnation")))throw denied();
      return new PortalReadCommand(trust,admission,current).assignmentConstraints(context,principal,binding,designation);
    }
  }
  static AssignmentConstraintLease assignmentConstraints(String tenant,String environment){
    var p=running;if(p==null)return null;
    if(!p.trust.scope.get("tenant").equals(tenant)||!p.trust.scope.get("environment").equals(environment))throw denied();
    return new AssignmentConstraintLease(p.trust,p.trust.acquire("portal-task-read"));
  }
  /** Same installed engine/read plugin only. Provider admission is acquired before
   * entering any engine command or native tenant lock; there is no synthesized SPI. */
  static StaffCaseReadCommand.Q2Lease staffLease(ProcessEngineConfigurationImpl expected,
      java.util.Map<String,Object> scope,java.util.Map<String,Object> anchor) {
    if(expected==null||org.cibseven.bpm.engine.impl.context.Context.getCommandContext()!=null)
      throw unavailable();
    var plugins=expected.getProcessEnginePlugins();
    if(plugins==null)throw unavailable();
    var matches=plugins.stream().filter(p->p instanceof PortalReadPlugin).toList();
    if(matches.size()!=1)throw unavailable();
    var installed=(PortalReadPlugin)matches.get(0);
    if(installed.configuration!=expected||installed.trust==null)throw unavailable();
    var admission=installed.trust.acquire("portal-task-read",installed.trust.scope);
    return new StaffCaseReadCommand.Q2Lease(installed.trust,admission,scope,anchor);
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
