package br.com.maezo.human;

import java.nio.file.Path;
import org.cibseven.bpm.engine.ProcessEngine;
import org.cibseven.bpm.engine.impl.cfg.*;

/** Registered explicitly in Tomcat /camunda/conf/bpm-platform.xml, ADR0049 D5. */
public final class HumanCommandPlugin extends AbstractProcessEnginePlugin {
  private Trust trust;
  private HumanAuthConfiguration authConfiguration;
  private AuthRuntime authRuntime;
  private AssignmentTrust assignmentTrust;
  private ExternalCaseModels.Configuration receiptCaseConfiguration;
  private String receiptCaseConfigurationDigest;
  private ProcessEngineConfigurationImpl configuration;
  private StaffCaseInstallation.Configuration staffConfiguration;
  private java.util.Map<String,Object> staffAnchor;
  private String staffConfigurationDigest;
  private boolean staffHistoryInstalled;
  private static volatile HumanCommandPlugin running;

  public HumanCommandPlugin() {}

  HumanCommandPlugin(Trust trust) {
    this.trust = trust;
  }

  /** Protected composition supplies AUTH independently of assignment and workload authority. */
  public synchronized void setHumanAuthConfiguration(HumanAuthConfiguration installed) {
    if (configuration != null || authConfiguration != null || installed == null) throw Rejected.denied();
    authConfiguration = installed;
  }

  /** Deployment-owner injection of the SAME independently installed W6 configuration.
   * It is immutable after engine initialization; no runtime source/config provider is created. */
  public synchronized void setAssignmentReceiptCaseConfiguration(ExternalCaseModels.Configuration value) {
    if(value==null||configuration!=null||receiptCaseConfiguration!=null)throw new IllegalStateException("receipt case configuration unavailable");
    receiptCaseConfiguration=value;receiptCaseConfigurationDigest=value.digest();
  }

  /** One immutable protected deployment input. No environment default, source owner
   * inference, alternate Q2 provider or installation row is created by this setter. */
  public synchronized void setStaffCaseConfiguration(StaffCaseInstallation.Configuration installed,
      java.util.Map<String,Object> catalogAnchor) {
    if(configuration!=null||staffConfiguration!=null||installed==null)throw Rejected.denied();
    var anchor=PortalReadModels.copy(PortalReadModels.validate("anchor",catalogAnchor));
    var expected=PortalReadModels.record("tenant",installed.authScope().get("tenant"),
      "workload_ref",PortalReadModels.obj(anchor,"scope").get("workload_ref"),
      "environment",installed.authScope().get("environment"));
    if(!expected.equals(anchor.get("scope")))throw Rejected.denied();
    staffConfiguration=installed;staffAnchor=java.util.Collections.unmodifiableMap(anchor);
    staffConfigurationDigest=staffDigest();
  }

  private String staffDigest(){return PortalReadModels.hash(PortalReadModels.record(
    "configuration_digest",staffConfiguration.digest(),"catalog_anchor",staffAnchor));}
  private java.util.Map<String,Object> staffScope(){return PortalReadModels.record(
    "tenant",staffConfiguration.authScope().get("tenant"),"environment",staffConfiguration.authScope().get("environment"),
    "engine_name",staffConfiguration.authScope().get("engine_name"),"database_incarnation",staffConfiguration.authScope().get("database_incarnation"));}
  private void staffConfigured(){
    if(staffConfiguration==null||configuration==null||authRuntime==null||!staffHistoryInstalled
        ||!staffDigest().equals(staffConfigurationDigest)||authRuntime.configuration!=configuration
        ||!authRuntime.scope.equals(staffConfiguration.authScope())
        ||!configuration.isAuthorizationEnabled()||!configuration.isTenantCheckEnabled())throw EngineStore.unavailable();
  }

  @Override
  public void preInit(ProcessEngineConfigurationImpl configuration) {
    if (trust == null) {
      String file = System.getenv("MAEZO_HUMAN_TRUST_FILE");
      if (file == null || file.isBlank())
        throw new IllegalStateException("MAEZO_HUMAN_TRUST_FILE must be explicitly configured");
      try {
        trust = Trust.load(Path.of(file));
      } catch (java.io.IOException ex) {
        throw new IllegalStateException("human trust configuration unavailable");
      }
    }
    String assignmentFile=System.getenv("MAEZO_HUMAN_ASSIGNMENT_TRUST_FILE");
    if(assignmentFile!=null && !assignmentFile.isBlank())try{assignmentTrust=AssignmentTrust.load(Path.of(assignmentFile),trust);}catch(java.io.IOException ex){throw new IllegalStateException("assignment trust configuration unavailable");}
    if(receiptCaseConfiguration!=null){
      if(assignmentTrust==null||!receiptCaseConfiguration.digest().equals(receiptCaseConfigurationDigest))throw new IllegalStateException("receipt case configuration unavailable");
      assignmentTrust.scope(receiptCaseConfiguration.scope());
      var used=new java.util.HashSet<String>();trust.keys.values().forEach(k->used.add(k.fingerprint()));assignmentTrust.readKeys.values().forEach(k->used.add(k.fingerprint()));used.add(Jcs.digest(assignmentTrust.sourceKey.getEncoded()));if(assignmentTrust.receiptSourceKey!=null)used.add(Jcs.digest(assignmentTrust.receiptSourceKey.getEncoded()));
      if(used.contains(Jcs.digest(receiptCaseConfiguration.installationKey().getEncoded()))||used.contains(receiptCaseConfiguration.transportFingerprint()))throw new IllegalStateException("receipt case key partition unavailable");
    }
    if(staffConfiguration!=null&&(authConfiguration==null
        ||!trust.tenant.equals(staffConfiguration.authScope().get("tenant"))
        ||!trust.engineName.equals(staffConfiguration.authScope().get("engine_name"))
        ||!staffDigest().equals(staffConfigurationDigest)))throw Rejected.denied();
    if (!trust.engineName.equals(configuration.getProcessEngineName()))
      throw new IllegalStateException("human engine name mismatch");
    var listeners = new java.util.ArrayList<org.cibseven.bpm.engine.impl.bpmn.parser.BpmnParseListener>();
    if (configuration.getCustomPostBPMNParseListeners() != null) listeners.addAll(configuration.getCustomPostBPMNParseListeners());
    listeners.add(new ConsumerTaskListener());
    if (authConfiguration != null) {
      HumanAuthConfiguration.registerSerializer(configuration);
      listeners.add(new AuthDocumentLifecycleListener(() -> authRuntime));
    }
    configuration.setCustomPostBPMNParseListeners(listeners);
    this.configuration = configuration;
  }

  @Override
  public void postInit(ProcessEngineConfigurationImpl configuration) {
    EnlistedWrites.install(configuration.getSqlSessionFactory().getConfiguration());
    if (authConfiguration != null) authRuntime = authConfiguration.bind(configuration,staffConfiguration);
    if(staffConfiguration!=null){
      var existing=configuration.getHistoryEventHandler();
      if(staffHistoryInstalled||existing==null||existing instanceof StaffCaseHistoryEventHandler)throw Rejected.denied();
      configuration.setHistoryEventHandler(new StaffCaseHistoryEventHandler(existing,staffConfiguration));
      staffHistoryInstalled=true;
    }
  }

  @Override
  public void postProcessEngineBuild(ProcessEngine engine) {
    configuration
        .getCommandExecutorTxRequired()
        .execute(context -> new EngineStore(context, trust.tenant).lockTenant());
    if(assignmentTrust!=null)configuration.getCommandExecutorTxRequired().execute(context->{AssignmentInstallation.acquire(context,new EngineStore(context,trust.tenant),assignmentTrust);return null;});
    if(staffConfiguration!=null){
      staffConfigured();
      var lease=PortalReadPlugin.staffLease(configuration,staffScope(),staffAnchor);
      configuration.getCommandExecutorTxRequired().execute(context->{staffCurrent(context,lease,true);return null;});
    }
    running = this;
  }

  static HumanCommandPlugin running() {
    HumanCommandPlugin plugin = running;
    if (plugin == null) throw new Rejected(503, "HUMAN_ENGINE_UNAVAILABLE");
    return plugin;
  }

  /** Scope-bound bridge construction only; native peer/designation admission remains independent. */
  public static AuthDocumentProducerContext documentProducerContext(String engineName) {
    HumanCommandPlugin plugin = running();
    if (plugin.authRuntime == null || plugin.configuration == null
        || !plugin.configuration.getProcessEngineName().equals(engineName))
      throw new Rejected(503, "HUMAN_ENGINE_UNAVAILABLE");
    return new AuthDocumentProducerContext(plugin.authRuntime);
  }

  /** Checks actual installed staff relations/designation and cross-purpose separation.
   * No caller-provided key or peer can appoint itself an owner. */
  private void staffCurrent(org.cibseven.bpm.engine.impl.interceptor.CommandContext context,
      StaffCaseReadCommand.Q2Lease lease,boolean startup) {
    staffConfigured();lease.admission.requireCurrent();
    var q2=new PortalReadStore(context,lease.trust,lease.admission.statementTimeoutSeconds());q2.lockTenant();
    var store=new StaffCaseStore(context,staffConfiguration.authScope(),lease.admission.statementTimeoutSeconds(),
      staffConfiguration.nativeRole(),staffConfiguration.relationPins());
    store.auth.lock();var installed=new StaffCaseInstallation(staffConfiguration,store,lease.admission);
    var used=new java.util.HashSet<String>();var peers=new java.util.HashSet<String>();
    trust.keys.values().forEach(k->{used.add(k.fingerprint());peers.add(k.peerSpki());});
    lease.trust.keys.values().forEach(k->{used.add(k.fingerprint());peers.add(k.peer());});
    used.add(authRuntime.signer.peerSpki);
    if(assignmentTrust!=null){
      assignmentTrust.readKeys.values().forEach(k->{used.add(k.fingerprint());peers.add(k.peerSpki());});
      used.add(Jcs.digest(assignmentTrust.sourceKey.getEncoded()));
      if(assignmentTrust.receiptSourceKey!=null)used.add(Jcs.digest(assignmentTrust.receiptSourceKey.getEncoded()));
    }
    if(receiptCaseConfiguration!=null){used.add(Jcs.digest(receiptCaseConfiguration.installationKey().getEncoded()));used.add(receiptCaseConfiguration.transportFingerprint());}
    var staffKeys=new java.util.HashSet<String>(installed.entries.keySet());
    staffKeys.add(Jcs.digest(staffConfiguration.rootKey().getEncoded()));
    for(String fingerprint:staffKeys){
      if(used.contains(fingerprint)||peers.contains(fingerprint))throw Rejected.denied();
      var authKey=store.auth.optional("SELECT KEY_ID_ FROM MZO_AUTH_TRUST WHERE TENANT_=? AND (encode(sha256(decode(DESIGNATION_::jsonb->>'public_key_base64','base64')),'hex')=? OR DESIGNATION_::jsonb->>'peer_spki_sha256'=?) LIMIT 1",store.auth.tenant,fingerprint,fingerprint);
      if(authKey!=null)throw Rejected.denied();
    }
    for(var entry:installed.entries.values())if(entry.get("certificate_spki")!=null){
      String peer=PortalReadModels.str(entry,"certificate_spki");
      if(peers.contains(peer)||used.contains(peer))throw Rejected.denied();
      if(store.auth.optional("SELECT KEY_ID_ FROM MZO_AUTH_TRUST WHERE TENANT_=? AND (DESIGNATION_::jsonb->>'peer_spki_sha256'=? OR encode(sha256(decode(DESIGNATION_::jsonb->>'public_key_base64','base64')),'hex')=?) LIMIT 1",store.auth.tenant,peer,peer)!=null)throw Rejected.denied();
      br.com.maezo.workload.WorkloadPlugin.running().requireHumanAuthPeerSeparated(peer);
    }
    if(startup){
      // Activation requires genuine heads for existing claims. No old event is
      // reconstructed and no ordinary request repairs the missing source cut.
      var missing=store.auth.optional("SELECT g.CASE_ FROM MZO_AUTH_GUIDE_CLAIM g LEFT JOIN mzo_staff_native_event_head e ON e.tenant=g.TENANT_ AND e.environment=? AND e.engine_name=? AND e.database_incarnation=? AND e.case_ref=g.CASE_ AND e.process_instance_id=g.INSTANCE_ WHERE g.TENANT_=? AND (e.case_ref IS NULL OR e.case_revision<1) LIMIT 1",staffConfiguration.authScope().get("environment"),staffConfiguration.authScope().get("engine_name"),staffConfiguration.authScope().get("database_incarnation"),store.auth.tenant);
      if(missing!=null)throw EngineStore.unavailable();
    }
    installed.finalDesignation();lease.admission.requireCurrent();staffConfigured();
  }

  /** Retains the actual committed result and its original admission until disclosure. */
  static final class StaffResponse {
    private final java.util.function.Supplier<byte[]> committedBytes;
    private final Runnable originalCurrent;
    private StaffResponse(java.util.function.Supplier<byte[]> committedBytes,Runnable originalCurrent){
      this.committedBytes=committedBytes;this.originalCurrent=originalCurrent;
    }
    byte[] bytes(){
      try{return committedBytes.get();}
      catch(RuntimeException expired){throw new Rejected(503,"HUMAN_ENGINE_UNAVAILABLE");}
    }
    void requireCurrent(){
      try{originalCurrent.run();}
      catch(RuntimeException expired){throw new Rejected(503,"HUMAN_ENGINE_UNAVAILABLE");}
    }
  }

  StaffResponse executeStaff(String path,byte[] raw,String peer){
    staffConfigured();if(raw==null||raw.length>65536)throw Rejected.invalid();
    byte[] immutable=raw.clone();String operation=switch(path){
      case "/v1/staff-case-detail"->"detail";case "/v1/staff-case-list"->"list";case "/v1/staff-case-finalize"->"finalize";
      case "/v1/staff-case-publication"->"publication";default->throw Rejected.invalid();};
    if(!operation.equals("publication")&&!operation.equals(Jcs.object(Jcs.parse(immutable)).get("operation")))throw Rejected.invalid();
    var lease=PortalReadPlugin.staffLease(configuration,staffScope(),staffAnchor);
    if(operation.equals("publication")){
      var result=configuration.getCommandExecutorTxRequired().execute(context->{
        staffCurrent(context,lease,false);
        var committed=new StaffCasePublicationCommand(staffConfiguration,immutable,peer,lease).execute(context);
        context.getTransactionContext().addTransactionListener(TransactionState.COMMITTING,ignored->{
          staffCurrent(context,lease,false);committed.current.run();});
        return committed;
      });
      return new StaffResponse(result::bytes,()->{lease.admission.requireCurrent();result.current.run();});
    }
    var result=configuration.getCommandExecutorTxRequired().execute(context->{
      staffCurrent(context,lease,false);
      var committed=new StaffCaseReadCommand(staffConfiguration,immutable,peer,lease).execute(context);
      context.getTransactionContext().addTransactionListener(TransactionState.COMMITTING,ignored->{
        staffCurrent(context,lease,false);committed.current.run();});
      return committed;
    });
    return new StaffResponse(result::bytes,()->{lease.admission.requireCurrent();result.current.run();});
  }

  byte[] executeAuth(String path, byte[] raw, String peer) {
    if (authRuntime == null) throw new Rejected(503, "HUMAN_ENGINE_UNAVAILABLE");
    return authRuntime.execute(path, raw, peer);
  }

  byte[] execute(byte[] raw, String peer, String purpose) {
    var outer=Jcs.object(Jcs.parse(raw));var command=Jcs.object(outer.get("command"));
    if(purpose.equals("human-command") && "human-assignment.v2".equals(command.get("schema"))){
      if(assignmentTrust==null)throw EngineStore.unavailable();
      byte[] immutable=raw.clone();
      return GovernedAssignment.withConstraints(configuration.getCommandExecutorTxRequired(),
        constraints->new GovernedAssignment(assignmentTrust,immutable,peer,constraints,receiptCaseConfiguration),
        ()->PortalReadPlugin.assignmentConstraints(assignmentTrust.tenant,assignmentTrust.environment));
    }
    if(purpose.equals("human-authority") && "human-assignment-receipt-publication.v1".equals(command.get("schema")))return configuration.getCommandExecutorTxRequired().execute(new AssignmentReceiptPublication(assignmentTrust,raw,peer));
    if(purpose.equals("human-authority") && "human-assignment-publication.v1".equals(command.get("schema")))return configuration.getCommandExecutorTxRequired().execute(new AssignmentPublication(assignmentTrust,raw,peer));
    return switch (purpose) {
      case "human-command" ->
          configuration
              .getCommandExecutorTxRequired()
              .execute(new AtomicHumanCommand(trust, raw, peer))
              .bytes();
      case "human-authority" ->
          configuration
              .getCommandExecutorTxRequired()
              .execute(new AuthorityCommand(trust, raw, peer));
      default -> throw Rejected.invalid();
    };
  }

  byte[] assignmentQuery(byte[] raw,String peer,String operation){
    if(operation.equals("receipt-authority")){
      if(receiptCaseConfiguration!=null&&!receiptCaseConfiguration.digest().equals(receiptCaseConfigurationDigest))throw EngineStore.unavailable();
      return configuration.getCommandExecutorTxRequired().execute(new AssignmentReceiptAuthority(assignmentTrust,raw,peer,receiptCaseConfiguration));
    }
    if(assignmentTrust==null)throw EngineStore.unavailable();
    byte[] immutable=raw.clone();
    return GovernedAssignment.withConstraints(configuration.getCommandExecutorTxRequired(),
      constraints->new AssignmentQuery(assignmentTrust,immutable,peer,operation,constraints),
      ()->PortalReadPlugin.assignmentConstraints(assignmentTrust.tenant,assignmentTrust.environment));
  }

  byte[] receipt(byte[] raw, String peer, String task, String command) {
    return configuration
        .getCommandExecutorTxRequired()
        .execute(
            context -> {
              EngineStore db = new EngineStore(context, trust.tenant);
              db.lockTenant();
              long now = java.time.Instant.now().getEpochSecond();
              var v = Envelope.verify(raw, trust, "human-receipt", peer, now, db::revoked);
              var c = v.command();
              Jcs.keys(
                  c,
                  "schema",
                  "tenant",
                  "workload_ref",
                  "task_id",
                  "command_id",
                  "principal_ref",
                  "principal_issuer",
                  "principal_subject",
                  "payload_digest");
              if (!"human-receipt-query.v1".equals(c.get("schema"))
                  || !task.equals(c.get("task_id"))
                  || !command.equals(c.get("command_id"))) throw Rejected.invalid();
              String principal = Jcs.ref(c, "principal_ref");
              var currentPrincipal =
                  db.principal(
                      principal,
                      Jcs.string(c, "principal_issuer"),
                      Jcs.ref(c, "principal_subject"),
                      now);
              byte[] result =
                  db.receipt(
                      task, command, Jcs.hash(c, "payload_digest"), principal, v.key().workload());
              if (result == null) throw new Rejected(404, "RECEIPT_NOT_FOUND");
              context
                  .getTransactionContext()
                  .addTransactionListener(
                      TransactionState.COMMITTING,
                      ignored -> {
                        long current = java.time.Instant.now().getEpochSecond();
                        v.requireCurrent(current);
                        EngineStore.requireCurrentPrincipal(currentPrincipal, current);
                      });
              return result;
            });
  }
}
