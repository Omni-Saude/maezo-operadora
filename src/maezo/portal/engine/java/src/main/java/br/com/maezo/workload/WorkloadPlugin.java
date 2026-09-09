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
  static final ThreadLocal<Boolean> GUARDED = ThreadLocal.withInitial(()->false);
  public WorkloadPlugin() {}
  // Package-private constructor for real PostgreSQL engine tests; production uses mounted Tomcat layout.
  WorkloadPlugin(BoundaryPolicy policy) {this.policy=policy;}

  @Override public void preInit(ProcessEngineConfigurationImpl config) {
    if(policy==null) {policy=BoundaryPolicy.environment(); SecureLayout.verify(policy);} configuration=config;
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
  public static WorkloadPlugin running() {
    WorkloadPlugin value=running;if(value==null)throw Refused.unavailable();return value;
  }
  BoundaryPolicy policy() {policy.current(null);return policy;}
  /** Public cross-classloader SPI bridge; a shared package name does not grant package-private access. */
  public BoundaryPolicy.Peer nativePeer(jakarta.servlet.http.HttpServletRequest request,String engineName) {
    BoundaryPolicy.Peer peer=policy().authenticate(request);
    if(!policy.engine.equals(engineName) || "human-relay".equals(peer.purpose()))throw Refused.denied();
    return peer;
  }
  ProcessEngine engine() {return engine;}
  void authenticated(BoundaryPolicy.Peer peer) {
    policy.current(peer);
    var auth=engine.getIdentityService().getCurrentAuthentication();
    if(!configuration.isAuthorizationEnabled() || !configuration.isTenantCheckEnabled() || auth==null || !peer.engineUser().equals(auth.getUserId())
        || !List.of(policy.tenant).equals(auth.getTenantIds()) || !auth.getGroupIds().isEmpty())throw Refused.denied();
  }
  byte[] execute(BoundaryPolicy.Peer peer,Map<String,Object> request) {
    authenticated(peer);
    Capability cap=peer.capabilities().stream().filter(c->c.digest.equals(request.get("capability_digest"))).findFirst().orElseThrow(Refused::denied);
    cap.validate(request);
    long poll=0;
    if("fetch_lock".equals(cap.schema.get("operation"))) {
      poll=Json.number(Json.object(request.get("parameters")),"asyncResponseTimeout");
      if(poll>policy.maxPollMillis)throw Refused.body();
    }
    long deadline=System.nanoTime()+java.util.concurrent.TimeUnit.MILLISECONDS.toNanos(poll);
    while(true) {
      byte[] result=configuration.getCommandExecutorTxRequired().execute(new WorkloadCommand(this,peer,cap,request)).get();
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
    GUARDED.set(true);
    try {
      for(Capability cap:peer.capabilities())authorizeCapability(peer,cap);
      policy.current(peer);
      return Json.bytes(Map.of("protocol","maezo.engine-readiness.v1","ready",true,"policy_digest",policy.digest,
          "capabilities",peer.capabilities().stream().map(c->c.digest).toList()));
    } finally {GUARDED.remove();}
  }
  /** Query authorization filters must never turn missing grants into an authoritative empty result. */
  void authorizeCapability(BoundaryPolicy.Peer peer,Capability cap) {
    WorkloadCommand.definition(engine,cap.target,policy.tenant);
    List<Permission> permissions=new ArrayList<>(List.of(Permissions.READ));
    switch(Json.token(cap.schema,"operation")) {
      case "start":
        permissions.add(Permissions.CREATE_INSTANCE);
        requireGrant(peer,Permissions.CREATE,Resources.PROCESS_INSTANCE,"*");break;
      case "fetch_lock", "external_complete", "external_failure", "external_bpmn_error", "external_unlock", "external_extend_lock", "correlate":
        permissions.add(Permissions.READ_INSTANCE);permissions.add(Permissions.UPDATE_INSTANCE);break;
      case "read_active": permissions.add(Permissions.READ_INSTANCE);break;
      case "read_history": permissions.add(Permissions.READ_HISTORY);break;
      default: throw Refused.unavailable();
    }
    String key=Json.token(cap.target,"process_key");
    for(Permission permission:permissions)requireGrant(peer,permission,Resources.PROCESS_DEFINITION,key);
    if(!cap.sourceTarget.isEmpty()) {
      WorkloadCommand.definition(engine,cap.sourceTarget,policy.tenant);
      String source=Json.token(cap.sourceTarget,"process_key");
      requireGrant(peer,Permissions.READ,Resources.PROCESS_DEFINITION,source);
      if("locked_external".equals(cap.sourceKind))requireGrant(peer,Permissions.READ_INSTANCE,Resources.PROCESS_DEFINITION,source);
      boolean history="completed_human".equals(cap.sourceKind)
          || cap.attestations.stream().map(Json::object).anyMatch(a->"@business_key".equals(a.get("source_variable")))
          || Json.list(cap.schema.get("fields")).stream().map(Json::object).anyMatch(f->"prior_human_evidence".equals(f.get("origin")));
      if(history)requireGrant(peer,Permissions.READ_HISTORY,Resources.PROCESS_DEFINITION,source);
    }
  }
  private void requireGrant(BoundaryPolicy.Peer peer,Permission permission,Resource resource,String id) {
    if(!engine.getAuthorizationService().isUserAuthorized(peer.engineUser(),List.of(),permission,resource,id))throw Refused.unavailable();
  }
  private final class Fence extends CommandInterceptor {
    @Override public <T>T execute(Command<T> command) {
      var auth=configuration.getIdentityService().getCurrentAuthentication();
      if(auth!=null && policy.peers.stream().anyMatch(p->p.engineUser().equals(auth.getUserId()))) {
        if(!(command instanceof WorkloadCommand) && !GUARDED.get())throw Refused.denied();
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
