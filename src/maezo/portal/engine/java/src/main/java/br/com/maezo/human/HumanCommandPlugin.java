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
    if (authConfiguration != null) authRuntime = authConfiguration.bind(configuration);
  }

  @Override
  public void postProcessEngineBuild(ProcessEngine engine) {
    configuration
        .getCommandExecutorTxRequired()
        .execute(context -> new EngineStore(context, trust.tenant).lockTenant());
    if(assignmentTrust!=null)configuration.getCommandExecutorTxRequired().execute(context->{AssignmentInstallation.acquire(context,new EngineStore(context,trust.tenant),assignmentTrust);return null;});
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
