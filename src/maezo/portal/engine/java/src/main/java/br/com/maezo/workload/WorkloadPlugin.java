package br.com.maezo.workload;

import java.util.*;
import org.cibseven.bpm.engine.*;
import org.cibseven.bpm.engine.authorization.*;
import org.cibseven.bpm.engine.impl.cfg.*;
import org.cibseven.bpm.engine.impl.interceptor.*;

/** D7 native authorization is mandatory and never disabled for workload operations. */
public final class WorkloadPlugin extends AbstractProcessEnginePlugin {
  private static volatile WorkloadPlugin running;
  private BoundaryPolicy policy;
  private ProcessEngineConfigurationImpl configuration;
  private ProcessEngine engine;
  private BoundaryPolicyV2 nativeV2;
  static final ThreadLocal<Boolean> GUARDED = ThreadLocal.withInitial(()->false);
  public WorkloadPlugin() {}
  // Package-private constructor for real PostgreSQL engine tests; production uses mounted Tomcat layout.
  WorkloadPlugin(BoundaryPolicy policy) {this.policy=policy;}

  @Override public void preInit(ProcessEngineConfigurationImpl config) {
    if(policy==null) {if(!BoundaryPolicyV2.configured())SecuredBpmPlatformBootstrap.claimConfiguration(config);policy=BoundaryPolicy.environment(); SecureLayout.verify(policy);} configuration=config;
    if(BoundaryPolicyV2.configured()) {
      nativeV2=BoundaryPolicyV2.environment();SecureLayout.verifyV2(nativeV2);
      if(!"false".equals(config.getDatabaseSchemaUpdate()) || !nativeV2.transport.engine.equals(config.getProcessEngineName())
          || (config.getAdminUsers()!=null && nativeV2.transport.peers.stream().anyMatch(p->config.getAdminUsers().contains(p.engineUser()))))throw Refused.unavailable();
    }
    if(!policy.engine.equals(config.getProcessEngineName()) || !config.isAuthorizationEnabled() || !config.isTenantCheckEnabled())throw Refused.unavailable();
    if(config.getAdminUsers()!=null && policy.peers.stream().anyMatch(p->config.getAdminUsers().contains(p.engineUser())))throw Refused.unavailable();
    var required=new ArrayList<CommandInterceptor>();
    if(config.getCustomPreCommandInterceptorsTxRequired()!=null)required.addAll(config.getCustomPreCommandInterceptorsTxRequired());
    required.add(0,new Fence());config.setCustomPreCommandInterceptorsTxRequired(required);
    var requiresNew=new ArrayList<CommandInterceptor>();
    if(config.getCustomPreCommandInterceptorsTxRequiresNew()!=null)requiresNew.addAll(config.getCustomPreCommandInterceptorsTxRequiresNew());
    requiresNew.add(0,new Fence());config.setCustomPreCommandInterceptorsTxRequiresNew(requiresNew);
    var postRequired=new ArrayList<CommandInterceptor>();
    if(config.getCustomPostCommandInterceptorsTxRequired()!=null)postRequired.addAll(config.getCustomPostCommandInterceptorsTxRequired());
    postRequired.add(new Freshness());config.setCustomPostCommandInterceptorsTxRequired(postRequired);
    var postNew=new ArrayList<CommandInterceptor>();
    if(config.getCustomPostCommandInterceptorsTxRequiresNew()!=null)postNew.addAll(config.getCustomPostCommandInterceptorsTxRequiresNew());
    postNew.add(new Freshness());config.setCustomPostCommandInterceptorsTxRequiresNew(postNew);
  }
  @Override public void postProcessEngineBuild(ProcessEngine engine) {
    this.engine=engine; policy.current(null);
    if(!configuration.isAuthorizationEnabled() || !configuration.isTenantCheckEnabled())throw Refused.unavailable();
    running=this;
  }
  void verifyStartup(ProcessEngineConfigurationImpl expected) {
    if(running!=this || configuration!=expected || engine==null || nativeV2!=null
        || !(engine instanceof org.cibseven.bpm.engine.impl.ProcessEngineImpl actual)
        || actual.getProcessEngineConfiguration()!=configuration
        || ProcessEngines.getProcessEngines().get(engine.getName())!=engine)throw Refused.unavailable();
    var fresh=BoundaryPolicy.environment();
    if(!fresh.digest.equals(policy.digest) || !fresh.engine.equals(engine.getName()))throw Refused.unavailable();
    policy.current(null);SecureLayout.verify(fresh);
  }
  static boolean installed() {return running!=null;}
  public static WorkloadPlugin running() {
    WorkloadPlugin value=running;if(value==null)throw Refused.unavailable();return value;
  }
  BoundaryPolicy policy() {policy.current(null);return policy;}
  /**
   * C1 F2: E04 separation for the human/staff image (deploy/cibseven/Dockerfile.human), which
   * never deploys D7: its layout (8080 + mTLS 8443, no BoundaryFilter) is not the D7 secured
   * layout that {@link #preInit} verifies, so registering this plugin there can never boot.
   * Without D7 in this process there is no D7 peer to collide with, and the check degrades to the
   * spki shape only. It stays fail-closed whenever D7 is meant to be here: a running plugin is
   * always asked; a boundary env, or a root-owned bpm-platform.xml that names this plugin while
   * it is not running (or a descriptor that cannot be read), is unavailable, never "separated".
   */
  public static void requireHumanAuthPeerSeparatedInProcess(String spki) {
    if(spki==null||!spki.matches("[0-9a-f]{64}"))throw Refused.denied();
    WorkloadPlugin value=running;
    if(value!=null){value.requireHumanAuthPeerSeparated(spki);return;}
    if(System.getenv("MAEZO_ENGINE_BOUNDARY_FILE")!=null||System.getenv("MAEZO_ENGINE_BOUNDARY_SHA256")!=null
        ||BoundaryPolicyV2.configured())throw Refused.unavailable();
    // Tomcat publishes its base as the `catalina.base` system property (the env var is optional).
    String base=System.getProperty("catalina.base");
    if(base==null||base.isBlank())throw Refused.unavailable();
    try {
      String descriptor=java.nio.file.Files.readString(java.nio.file.Path.of(base,"conf","bpm-platform.xml"));
      if(descriptor.contains(WorkloadPlugin.class.getName()))throw Refused.unavailable();
    } catch(java.io.IOException|RuntimeException unreadable) {
      if(unreadable instanceof Refused r)throw r;
      throw Refused.unavailable();
    }
  }
  /** E04 read-only separation check; no peer enumeration or nested-command permit is returned. */
  public void requireHumanAuthPeerSeparated(String spki) {
    if(spki==null||!spki.matches("[0-9a-f]{64}"))throw Refused.denied();
    if(policy==null||configuration==null||engine==null)throw Refused.unavailable();
    policy.current(null);
    if(policy.peers.stream().anyMatch(peer->peer.spki().equals(spki)))throw Refused.denied();
    // An uninstalled v2 has no executable v2 entry point (v2() already refuses it).
    // Once installed, its actual current mounted policy must also prove separation.
    if(nativeV2!=null) {
      nativeV2.current(null);
      if(nativeV2.transport.peers.stream().anyMatch(peer->peer.spki().equals(spki)))throw Refused.denied();
    }
  }

  /** Public cross-classloader SPI bridge; a shared package name does not grant package-private access. */
  public BoundaryPolicy.Peer nativePeer(jakarta.servlet.http.HttpServletRequest request,String engineName) {
    BoundaryPolicy.Peer peer=policy().authenticate(request);
    if(!policy.engine.equals(engineName) || "human-relay".equals(peer.purpose()))throw Refused.denied();
    return peer;
  }
  ProcessEngine engine() {return engine;}
  BoundaryPolicyV2 v2(){if(nativeV2==null)throw Refused.unavailable();nativeV2.current(null);return nativeV2;}
  NativeOutcomeV2.Encoded executeV2(Command<NativeOutcomeV2.Publication> command){
    return configuration.getCommandExecutorTxRequired().execute(command).committed();
  }
  void authenticated(BoundaryPolicy.Peer peer) {
    policy.current(peer);
    var auth=engine.getIdentityService().getCurrentAuthentication();
    if(!configuration.isAuthorizationEnabled() || !configuration.isTenantCheckEnabled() || auth==null || !peer.engineUser().equals(auth.getUserId())
        || !List.of(policy.tenant).equals(auth.getTenantIds()) || !auth.getGroupIds().isEmpty())throw Refused.denied();
  }
  byte[] execute(BoundaryPolicy.Peer peer,Map<String,Object> request) {
    authenticated(peer);
    Capability cap=Capability.forRequest(peer.capabilities(),request);
    cap.validate(request);
    if(nativeV2!=null && nativeV2.manages(cap))throw Refused.denied();
    long poll=0;
    if("fetch_lock".equals(cap.schema.get("operation"))) {
      poll=Json.number(Json.object(request.get("parameters")),"asyncResponseTimeout");
      if(poll>policy.maxPollMillis)throw Refused.body();
    }
    long deadline=System.nanoTime()+java.util.concurrent.TimeUnit.MILLISECONDS.toNanos(poll);
    while(true) {
      byte[] result;
      try {result=configuration.getCommandExecutorTxRequired().execute(new WorkloadCommand(this,peer,cap,request)).get();}
      // A native permission can change between checks. The executor has rolled back before this boundary returns.
      catch(AuthorizationException e) {throw Refused.unavailable();}
      if(poll==0 || !Json.list(Json.parse(result).get("result")).isEmpty() || System.nanoTime()>=deadline)return result;
      policy.current(peer);
      java.util.concurrent.locks.LockSupport.parkNanos(Math.min(java.util.concurrent.TimeUnit.MILLISECONDS.toNanos(100),Math.max(1,deadline-System.nanoTime())));
      if(Thread.currentThread().isInterrupted())throw Refused.unavailable();
    }
  }
  byte[] readiness(BoundaryPolicy.Peer peer) {
    authenticated(peer);
    // Readiness exercises each exact definition and required native grants. An empty profile is unavailable.
    if(peer.capabilities().isEmpty())throw Refused.unavailable();
    boolean guarded=GUARDED.get();GUARDED.set(true);
    try {
      for(Capability cap:peer.capabilities())authorizeCapability(peer,cap);
      policy.current(peer);
      return Json.bytes(Map.of("protocol","maezo.engine-readiness.v1","ready",true,"policy_digest",policy.digest,
          "capabilities",peer.capabilities().stream().map(c->c.digest).toList()));
    } finally {if(guarded)GUARDED.set(true);else GUARDED.remove();}
  }
  /** Query authorization filters must never turn missing grants into an authoritative empty result. */
  void authorizeCapability(BoundaryPolicy.Peer peer,Capability cap) {
    WorkloadCommand.definition(engine,cap.target,policy.tenant);
    if(!cap.sourceTarget.isEmpty())WorkloadCommand.definition(engine,cap.sourceTarget,policy.tenant);
    authorizeGrants(cap,(permission,resource,id)->requireGrant(peer,permission,resource,id));
  }
  /** Recheck native authority without entering the service/interceptor chain during COMMITTING. */
  void reauthorizeCapability(CommandContext context,BoundaryPolicy.Peer peer,Capability cap) {
    if(context!=org.cibseven.bpm.engine.impl.context.Context.getCommandContext()
        || !context.isAuthorizationCheckEnabled() || !context.isTenantCheckEnabled())throw Refused.denied();
    // CIB 2.1 delegates boolean grant queries to MyBatis. Clear only query results, not enlisted entities/writes.
    context.getDbSqlSession().getSqlSession().clearCache();
    authorizeGrants(cap,(permission,resource,id)->{
      // Its revoke-discovery flag is also cached per manager. A fresh native manager uses this SAME context/session.
      var authority=new org.cibseven.bpm.engine.impl.persistence.entity.AuthorizationManager();
      if(authority.isPermissionDisabled(permission)
          || !authority.isAuthorized(peer.engineUser(),List.of(),permission,resource,id))throw Refused.unavailable();
    });
  }
  @FunctionalInterface private interface GrantCheck {void require(Permission permission,Resource resource,String id);}
  private static void authorizeGrants(Capability cap,GrantCheck grants) {
    List<Permission> permissions=new ArrayList<>(List.of(Permissions.READ));
    switch(Json.token(cap.schema,"operation")) {
      case "start":
        permissions.add(Permissions.CREATE_INSTANCE);
        grants.require(Permissions.CREATE,Resources.PROCESS_INSTANCE,"*");break;
      case "fetch_lock", "external_complete", "external_failure", "external_bpmn_error", "external_unlock", "external_extend_lock", "correlate":
        permissions.add(Permissions.READ_INSTANCE);permissions.add(Permissions.UPDATE_INSTANCE);break;
      case "read_active": permissions.add(Permissions.READ_INSTANCE);break;
      case "read_history": permissions.add(Permissions.READ_HISTORY);break;
      default: throw Refused.unavailable();
    }
    String key=Json.token(cap.target,"process_key");
    for(Permission permission:permissions)grants.require(permission,Resources.PROCESS_DEFINITION,key);
    if(!cap.sourceTarget.isEmpty()) {
      String source=Json.token(cap.sourceTarget,"process_key");
      grants.require(Permissions.READ,Resources.PROCESS_DEFINITION,source);
      if("locked_external".equals(cap.sourceKind))grants.require(Permissions.READ_INSTANCE,Resources.PROCESS_DEFINITION,source);
      boolean history="completed_human".equals(cap.sourceKind)
          || cap.attestations.stream().map(Json::object).anyMatch(a->"@business_key".equals(a.get("source_variable")))
          || Json.list(cap.schema.get("fields")).stream().map(Json::object).anyMatch(f->"prior_human_evidence".equals(f.get("origin")));
      if(history)grants.require(Permissions.READ_HISTORY,Resources.PROCESS_DEFINITION,source);
    }
  }
  private void requireGrant(BoundaryPolicy.Peer peer,Permission permission,Resource resource,String id) {
    if(!engine.getAuthorizationService().isUserAuthorized(peer.engineUser(),List.of(),permission,resource,id))throw Refused.unavailable();
  }
  private final class Fence extends CommandInterceptor {
    @Override public <T>T execute(Command<T> command) {
      var auth=configuration.getIdentityService().getCurrentAuthentication();
      if(auth!=null && ((nativeV2!=null && nativeV2.transport.peers.stream().anyMatch(p->p.engineUser().equals(auth.getUserId()))) || policy.peers.stream().anyMatch(p->p.engineUser().equals(auth.getUserId())))) ClassifiedConsumerFence.nested(command);
      if(auth!=null && nativeV2!=null && nativeV2.transport.peers.stream().anyMatch(p->p.engineUser().equals(auth.getUserId()))) {
        if(!(command instanceof NativeOperationV2) && !(command instanceof NativeOperationV2.ReadCommand) && !GUARDED.get())throw Refused.denied();
        nativeV2.current(null);
      }
      if(auth!=null && policy.peers.stream().anyMatch(p->p.engineUser().equals(auth.getUserId()))) {
        if(!(command instanceof WorkloadCommand) && !(command instanceof NativeOperationV2) && !(command instanceof NativeOperationV2.ReadCommand) && !GUARDED.get())throw Refused.denied();
        policy.current(null);
      }
      return next.execute(command);
    }
  }
  /** Actual custom-post interceptors run inside CommandContext, after native context/auth interceptors. */
  private final class Freshness extends CommandInterceptor {
    @Override public <T>T execute(Command<T> command) {
      BoundaryPolicy.Peer peer=BoundaryFilter.REQUEST_PEER.get();
      if(peer!=null)policy.current(peer);
      T result=next.execute(command);
      if(peer!=null) {
        var context=org.cibseven.bpm.engine.impl.context.Context.getCommandContext();
        if(context==null)throw Refused.unavailable();
        // Register AFTER D5's command listeners so mounted-policy loss after its enlisted receipt SQL rolls back too.
        context.getTransactionContext().addTransactionListener(TransactionState.COMMITTING,ignored->policy.current(peer));
      }
      return result;
    }
  }
}
