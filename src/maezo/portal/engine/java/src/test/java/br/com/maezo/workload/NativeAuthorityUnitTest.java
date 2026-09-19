package br.com.maezo.workload;

import static org.junit.jupiter.api.Assertions.*;
import java.util.*;
import org.apache.ibatis.cursor.Cursor;
import org.apache.ibatis.executor.*;
import org.apache.ibatis.mapping.*;
import org.apache.ibatis.session.*;
import org.apache.ibatis.session.defaults.*;
import org.cibseven.bpm.engine.authorization.*;
import org.cibseven.bpm.engine.impl.cfg.*;
import org.cibseven.bpm.engine.impl.cfg.standalone.StandaloneTransactionContextFactory;
import org.cibseven.bpm.engine.impl.context.Context;
import org.cibseven.bpm.engine.impl.db.AuthorizationCheck;
import org.cibseven.bpm.engine.impl.db.entitymanager.DbEntityManager;
import org.cibseven.bpm.engine.impl.db.sql.*;
import org.cibseven.bpm.engine.impl.interceptor.CommandContext;
import org.cibseven.bpm.engine.impl.persistence.entity.*;
import org.junit.jupiter.api.*;
import org.junit.jupiter.api.io.TempDir;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;

/** Offline unit checks: real CIB managers and MyBatis cache, programmable query results, no engine/database. */
class NativeAuthorityUnitTest {
  @TempDir java.nio.file.Path temp;
  private CommandContext context;
  private ProcessEngineConfigurationImpl config;
  private QueryResults queries;
  private SqlSession sql;
  private WorkloadPlugin plugin;
  private BoundaryPolicy.Peer peer;

  @BeforeEach void setup() throws Exception {
    var policy=Fixtures.load(temp,Fixtures.manifest(temp));peer=policy.peers.get(0);plugin=new WorkloadPlugin(policy);
    config=new StandaloneProcessEngineConfiguration();config.setAuthorizationEnabled(true);config.setTenantCheckEnabled(true);
    config.setAuthorizationCheckRevokes("auto");
    context=new CommandContext(config,new StandaloneTransactionContextFactory());
    Context.setCommandContext(context);Context.setProcessEngineConfiguration(config);
    var mapping=new Configuration();queries=new QueryResults(mapping);sql=new DefaultSqlSession(mapping,queries,false);
    for(String id:List.of("selectRevokeAuthorization","isUserAuthorizedForResource")) {
      // These are query-result units, not a replacement SQL grant policy. Distinct native inputs retain distinct cache keys.
      SqlSource source=p->new BoundSql(mapping,id+" "+queryKey(p),List.of(),p);
      mapping.addMappedStatement(new MappedStatement.Builder(mapping,id,source,SqlCommandType.SELECT).build());
    }
    var factory=new DbSqlSessionFactory(false);factory.setDatabaseType("postgres");
    factory.setSqlSessionFactory(new DefaultSqlSessionFactory(mapping){@Override public SqlSession openSession(){return sql;}});
    var nativeSql=new SimpleDbSqlSession(factory);
    context.getSessions().put(DbSqlSession.class,nativeSql);
    context.getSessions().put(DbEntityManager.class,new DbEntityManager(()->"unused",nativeSql));
    context.getSessions().put(AuthorizationManager.class,new AuthorizationManager());
  }
  @AfterEach void teardown() {
    Context.removeCommandContext();Context.removeProcessEngineConfiguration();WorkloadPlugin.GUARDED.remove();
  }
  private static String queryKey(Object p) {
    if(p instanceof AuthorizationCheck a)return a.getAuthUserId()+":"+a.isRevokeAuthorizationCheckEnabled()+":"+
        a.getPermissionChecks().getAllPermissionChecks().stream().map(c->c.getResourceType()+":"+c.getResourceId()+":"+c.getPermission().getName()).toList();
    return p.toString();
  }
  private void refused(Runnable action) {
    Refused refusal=assertThrows(Refused.class,action::run);assertEquals(503,refusal.status);assertEquals("engine_profile_unavailable",refusal.getMessage());
  }
  private Capability completion(){return new Capability(Fixtures.binding("contas.calculate_impact.complete.v1"));}

  @Test void warmedMyBatisGrantCannotSurviveRevocation() {
    var cap=completion();plugin.reauthorizeCapability(context,peer,cap);
    int before=queries.reads;queries.denied="UPDATE_INSTANCE";
    assertTrue(new AuthorizationManager().isAuthorized(peer.engineUser(),List.of(),Permissions.UPDATE_INSTANCE,Resources.PROCESS_DEFINITION,"SP-OP-CONTAS-001"),"the deliberately warmed MyBatis result is stale");
    assertEquals(before,queries.reads);
    refused(()->plugin.reauthorizeCapability(context,peer,cap));assertTrue(queries.reads>before);
    queries.denied="";assertDoesNotThrow(()->plugin.reauthorizeCapability(context,peer,cap));
  }
  @Test void warmedNativeRevokeDiscoveryCannotSurviveNewRevoke() {
    var authority=context.getAuthorizationManager();
    assertTrue(authority.isAuthorized(peer.engineUser(),List.of(),Permissions.READ,Resources.PROCESS_DEFINITION,"SP-OP-CONTAS-001"));
    queries.revoke=true;sql.clearCache();
    assertTrue(authority.isAuthorized(peer.engineUser(),List.of(),Permissions.READ,Resources.PROCESS_DEFINITION,"SP-OP-CONTAS-001"),"existing native manager cached revoke absence");
    refused(()->plugin.reauthorizeCapability(context,peer,completion()));
    assertTrue(queries.sawRevoke);
  }
  @Test void sourceHistoryRemainsRequiredForReviewedAutomaticEmptyEvidence() {
    var b=Fixtures.binding("contas.pagto.handoff.v1");var doc=Json.object(b.get("document"));
    doc.put("source_target",Map.of("process_key","SP-OP-CONTAS-001","process_version",2L,"definition_id","contas-v2","topic","operadora.contas.handoff_pagamento","message",""));
    b.put("source_kind","locked_external");b.put("source_worker_id","fixture-worker");
    b.put("attestations",List.of(Map.of("name","fonte_valor","source_variable","fonte_valor","human_task_definition",""),
        Map.of("name","lastro_origem","source_variable","lastro_origem","human_task_definition",""),
        Map.of("name","lastro_decisor_id","source_variable","analista_id","human_task_definition","UT_AnalistaContas")));
    b.put("digest",Json.digest(doc));var cap=new Capability(b);
    plugin.reauthorizeCapability(context,peer,cap);queries.denied="READ_HISTORY";
    refused(()->plugin.reauthorizeCapability(context,peer,cap));
    assertTrue(queries.observed.contains(Resources.PROCESS_DEFINITION.resourceName()+":SP-OP-CONTAS-001:READ_HISTORY"));
    assertTrue(queries.observed.contains(Resources.PROCESS_INSTANCE.resourceName()+":*:CREATE"));
    assertTrue(queries.observed.contains(Resources.PROCESS_DEFINITION.resourceName()+":SP-OP-PAGTO-001:CREATE_INSTANCE"));
  }
  @Test void disabledNativePermissionIsUnavailableRatherThanImplicitGrant() {
    config.setDisabledPermissions(List.of("UPDATE_INSTANCE"));refused(()->plugin.reauthorizeCapability(context,peer,completion()));
  }
  @ParameterizedTest @ValueSource(booleans={false,true})
  void recheckKeepsGuardAndEnlistedStateEvenOnRefusal(boolean guarded) {
    WorkloadPlugin.GUARDED.set(guarded);var sessions=new HashMap<>(context.getSessions());
    var cache=context.getDbEntityManager().getDbEntityCache();var entity=new AuthorizationEntity();entity.setId("enlisted-existing");
    context.getDbEntityManager().insert(entity);
    plugin.reauthorizeCapability(context,peer,completion());queries.denied="UPDATE_INSTANCE";
    refused(()->plugin.reauthorizeCapability(context,peer,completion()));
    assertEquals(guarded,WorkloadPlugin.GUARDED.get());assertEquals(sessions,context.getSessions());
    assertSame(cache,context.getDbEntityManager().getDbEntityCache());assertSame(entity,context.getDbEntityManager().getCachedEntity(AuthorizationEntity.class,"enlisted-existing"));
    assertEquals(0,queries.writes);assertEquals(0,queries.flushes);
  }
  @Test void committingReadDoesNotAppendTransactionListeners() {
    var called=new ArrayList<String>();
    context.getTransactionContext().addTransactionListener(TransactionState.COMMITTING,ignored->{called.add("before");plugin.reauthorizeCapability(context,peer,completion());});
    context.getTransactionContext().addTransactionListener(TransactionState.COMMITTING,ignored->{called.add("after");throw Refused.unavailable();});
    refused(()->context.getTransactionContext().commit());assertEquals(List.of("before","after"),called);
    assertEquals(0,queries.writes);assertEquals(0,queries.flushes);
  }
  @ParameterizedTest @ValueSource(strings={"authorization-disabled","tenant-disabled","foreign-context"})
  void noInternalFenceBypass(String fault) {
    if(fault.equals("authorization-disabled"))context.setAuthorizationCheckEnabled(false);
    else if(fault.equals("tenant-disabled"))context.setTenantCheckEnabled(false);
    else Context.removeCommandContext();
    try {assertEquals(403,assertThrows(Refused.class,()->plugin.reauthorizeCapability(context,peer,completion())).status);assertEquals(0,queries.reads);}
    finally {if(fault.equals("foreign-context"))Context.setCommandContext(context);}
  }
  @ParameterizedTest @ValueSource(booleans={false,true})
  void failedCommandRestoresPreviousGuard(boolean guarded) {
    WorkloadPlugin.GUARDED.set(guarded);
    assertThrows(RuntimeException.class,()->new WorkloadCommand(new WorkloadPlugin(),null,null,null).execute(context));
    assertEquals(guarded,WorkloadPlugin.GUARDED.get());
  }
  private static HistoricTaskInstanceEntity human() {
    var task=new HistoricTaskInstanceEntity();task.setId("human-task");task.setProcessDefinitionId("contas-v2");
    task.setProcessInstanceId("source-process");task.setTenantId("tenant-test");task.setTaskDefinitionKey("UT_AnalistaContas");return task;
  }
  @ParameterizedTest @ValueSource(strings={"valid","definition","tenant","process","task-definition"})
  void historicTaskMustMatchPinnedSource(String fault) {
    var task=human();
    switch(fault){case "definition"->task.setProcessDefinitionId("contas-v1");case "tenant"->task.setTenantId("other");
      case "process"->task.setProcessInstanceId("other");case "task-definition"->task.setTaskDefinitionKey("other");}
    assertEquals(fault.equals("valid"),WorkloadCommand.humanSource(task,Map.of("definition_id","contas-v2"),"tenant-test","source-process","UT_AnalistaContas"));
  }
  @ParameterizedTest @ValueSource(strings={"valid","definition","tenant","process","task"})
  void historicUpdateMustBelongToThatExactHumanTask(String fault) {
    var task=human();var update=new HistoricDetailVariableInstanceUpdateEntity();update.setTaskId(task.getId());
    update.setProcessDefinitionId(task.getProcessDefinitionId());update.setProcessInstanceId(task.getProcessInstanceId());update.setTenantId(task.getTenantId());
    switch(fault){case "definition"->update.setProcessDefinitionId("contas-v1");case "tenant"->update.setTenantId("other");
      case "process"->update.setProcessInstanceId("other");case "task"->update.setTaskId("other");}
    assertEquals(fault.equals("valid"),WorkloadCommand.humanUpdate(update,task));
  }
  private static final class QueryResults extends BaseExecutor {
    int reads,writes,flushes;boolean revoke,sawRevoke;String denied="";Set<String> observed=new HashSet<>();
    QueryResults(Configuration config){super(config,null);}
    @Override protected int doUpdate(MappedStatement m,Object p){writes++;throw new AssertionError("No offline SQL writes permitted");}
    @Override protected List<BatchResult> doFlushStatements(boolean rollback){flushes++;throw new AssertionError("No offline SQL flush permitted");}
    @Override protected <E> Cursor<E> doQueryCursor(MappedStatement m,Object p,RowBounds r,BoundSql s){throw new AssertionError();}
    @Override @SuppressWarnings("unchecked") protected <E> List<E> doQuery(MappedStatement m,Object p,RowBounds r,ResultHandler h,BoundSql s) {
      reads++;boolean result;
      if(m.getId().equals("selectRevokeAuthorization"))result=revoke;
      else {
        var check=(AuthorizationCheck)p;assertEquals("fixture-helena",check.getAuthUserId());assertTrue(check.getAuthGroupIds().isEmpty());
        sawRevoke|=check.isRevokeAuthorizationCheckEnabled();
        for(var grant:check.getPermissionChecks().getAllPermissionChecks())observed.add(grant.getResource().resourceName()+":"+grant.getResourceId()+":"+grant.getPermission().getName());
        result=!(revoke&&check.isRevokeAuthorizationCheckEnabled())
            && check.getPermissionChecks().getAllPermissionChecks().stream().noneMatch(c->c.getPermission().getName().equals(denied));
      }
      return (List<E>)(List<?>)List.of(result?1:0);
    }
  }
}
