package br.com.maezo.workload;

import java.sql.*;
import java.nio.charset.StandardCharsets;
import java.util.*;
import org.cibseven.bpm.engine.*;
import org.cibseven.bpm.engine.authorization.*;
import org.cibseven.bpm.engine.impl.interceptor.CommandContext;
import org.apache.ibatis.session.SqlSession;

/** Actual session/native/D guard. Mounted expected values never self-authenticate. */
final class NativeAdmissionV2 {
  final BoundaryPolicyV2 policy;
  final BoundaryPolicy.Peer peer;
  final CommandContext context;
  final ProcessEngine engine;
  final SqlSession session;
  final Map<String,Object> captured;
  final long deadline;
  NativeAdmissionV2(BoundaryPolicyV2 policy,BoundaryPolicy.Peer peer,CommandContext context,
      ProcessEngine engine,String capabilityDigest,Map<String,Object> binding,String kind) {
    this.policy=policy;this.peer=peer;this.context=context;this.engine=engine;
    session=context.getDbSqlSession().getSqlSession();authentication();
    try {
      Connection c=session.getConnection();
      if(c.getAutoCommit() || c.getTransactionIsolation()!=Connection.TRANSACTION_READ_COMMITTED
          || !"PostgreSQL".equals(c.getMetaData().getDatabaseProductName()))throw Refused.unavailable();
    }catch(SQLException e){throw Refused.unavailable();}
    NativeEnlistedWritesV2.install(session.getConfiguration(),Json.token(policy.database,"act_schema"));
    Map<String,Object> connection=connection();
    if(!connection.get("session_user").equals(connection.get("current_user"))
        || !policy.database.get("database_oid").equals(connection.get("database_oid"))
        || !policy.database.get("act_schema_oid").equals(connection.get("act_schema_oid")))throw Refused.unavailable();
    Map<String,Object> request=new HashMap<>();request.put("scope",scope());request.put("expected",policy.admission);
    request.put("database",policy.database);request.put("policy_digest",policy.digest);request.put("schema_digest",policy.schemaDigest);
    request.put("identity",peer.identity());request.put("native_user",peer.engineUser());request.put("login_name",connection.get("session_user"));request.put("login_oid",connection.get("login_oid"));
    request.put("capability_digest",capabilityDigest);request.put("capability_binding",binding);request.put("kind",kind);
    NativeEnlistedWritesV2.rows(session,"timeouts","1000ms","5000ms");
    captured=NativeEnlistedWritesV2.call(session,"guard_runtime_v2",request);
    Json.keys(captured,"scope","activation_ref","runtime_generation","decision_digest","identity","native_user","capability_digest","kind","deadline","lock_timeout","statement_timeout","guard_ref");
    if(!scope().equals(captured.get("scope")) || !policy.admission.get("activation_ref").equals(captured.get("activation_ref"))
        || !policy.admission.get("runtime_generation").equals(captured.get("runtime_generation"))
        || !policy.admission.get("decision_digest").equals(captured.get("decision_digest"))
        || !peer.identity().equals(captured.get("identity")) || !peer.engineUser().equals(captured.get("native_user"))
        || !capabilityDigest.equals(captured.get("capability_digest")) || !kind.equals(captured.get("kind")))throw Refused.unavailable();
    deadline=Json.number(captured,"deadline");NativeOutcomeV2.ref(captured,"guard_ref");
    long lock=Json.number(captured,"lock_timeout"),statement=Json.number(captured,"statement_timeout");
    if(lock<1 || statement<1 || lock>statement || statement>deadline-Json.number(connection,"now"))throw Refused.unavailable();
    NativeEnlistedWritesV2.rows(session,"timeouts",lock+"ms",statement+"ms");current();
  }
  Map<String,Object> scope(){return Map.of("tenant",policy.transport.tenant,"environment",policy.transport.environment,"engine_name",policy.transport.engine,"database_incarnation",policy.admission.get("database_incarnation"));}
  Map<String,Object> connection(){
    List<String> rows=NativeEnlistedWritesV2.rows(session,"connection",Json.token(policy.database,"act_schema"));
    if(rows.size()!=1)throw Refused.unavailable();return Json.parse(rows.get(0).getBytes(StandardCharsets.UTF_8));
  }
  void authentication(){
    policy.current(peer);var auth=engine.getIdentityService().getCurrentAuthentication();
    if(!context.isAuthorizationCheckEnabled() || !context.isTenantCheckEnabled() || auth==null
        || !peer.engineUser().equals(auth.getUserId()) || !List.of(policy.transport.tenant).equals(auth.getTenantIds())
        || !auth.getGroupIds().isEmpty())throw Refused.denied();
  }
  void current(){
    authentication();var c=connection();
    if(!c.get("session_user").equals(c.get("current_user")) || Json.number(c,"now")>=deadline)throw Refused.unavailable();
    var checked=call("validate_guard_v2",Map.of());Json.keys(checked,"valid");if(!Json.bool(checked,"valid"))throw Refused.unavailable();
  }
  long now(){return Json.number(connection(),"now");}
  Map<String,Object> call(String function,Map<String,Object> data){
    Map<String,Object> payload=new HashMap<>(data);payload.put("guard",captured);return NativeEnlistedWritesV2.call(session,function,payload);
  }
  void grants(CapabilityV2 cap){
    WorkloadCommand.definition(engine,cap.target,policy.transport.tenant);
    if(!cap.sourceTarget.isEmpty())WorkloadCommand.definition(engine,cap.sourceTarget,policy.transport.tenant);
    grantsFinal(cap);
  }
  /** No service/engine entity changes or new earlier-order locks at COMMITTING. */
  void grantsFinal(CapabilityV2 cap){
    current();session.clearCache();var manager=new org.cibseven.bpm.engine.impl.persistence.entity.AuthorizationManager();
    List<Permission> permissions=new ArrayList<>(List.of(Permissions.READ));
    switch(Json.token(cap.schema,"operation")){
      case "start":permissions.add(Permissions.CREATE_INSTANCE);require(manager,Permissions.CREATE,Resources.PROCESS_INSTANCE,"*");break;
      case "read_active":permissions.add(Permissions.READ_INSTANCE);break;
      case "read_history":permissions.add(Permissions.READ_HISTORY);break;
      default:permissions.add(Permissions.READ_INSTANCE);permissions.add(Permissions.UPDATE_INSTANCE);
    }
    for(Permission p:permissions)require(manager,p,Resources.PROCESS_DEFINITION,Json.token(cap.target,"process_key"));
    if(!cap.sourceTarget.isEmpty()){
      String source=Json.token(cap.sourceTarget,"process_key");require(manager,Permissions.READ,Resources.PROCESS_DEFINITION,source);
      if(cap.sourceKind.equals("locked_external"))require(manager,Permissions.READ_INSTANCE,Resources.PROCESS_DEFINITION,source);
      if(cap.sourceKind.equals("completed_human") || cap.attestations.stream().map(Json::object).anyMatch(a->"@business_key".equals(a.get("source_variable")))
          || Json.list(cap.schema.get("fields")).stream().map(Json::object).anyMatch(f->"prior_human_evidence".equals(f.get("origin"))))require(manager,Permissions.READ_HISTORY,Resources.PROCESS_DEFINITION,source);
    }
  }
  private void require(org.cibseven.bpm.engine.impl.persistence.entity.AuthorizationManager manager,Permission permission,Resource resource,String id){
    if(manager.isPermissionDisabled(permission) || !manager.isAuthorized(peer.engineUser(),List.of(),permission,resource,id))throw Refused.unavailable();
  }
}
