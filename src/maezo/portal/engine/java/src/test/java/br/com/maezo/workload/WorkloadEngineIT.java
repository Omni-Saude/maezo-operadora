package br.com.maezo.workload;

import static org.junit.jupiter.api.Assertions.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.sql.*;
import java.util.*;
import java.util.function.Supplier;
import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicBoolean;
import org.cibseven.bpm.engine.impl.interceptor.*;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;
import br.com.maezo.human.D7HumanFixture;
import org.cibseven.bpm.engine.*;
import org.cibseven.bpm.engine.authorization.*;
import org.cibseven.bpm.engine.impl.cfg.ProcessEngineConfigurationImpl;
import org.junit.jupiter.api.*;
import org.junit.jupiter.api.io.TempDir;

/** ROOT-only real CIB 2.1/PostgreSQL command tests. No engine mocks, credential skips or HTTP claims. */
@Tag("integration") @TestInstance(TestInstance.Lifecycle.PER_CLASS)
class WorkloadEngineIT {
  @TempDir static Path temp;
  ProcessEngine engine;WorkloadPlugin plugin;BoundaryPolicy policy;
  String url,user,password,schema,contas,escalation,pagto,sourceContas;
  Map<String,Capability> caps=new HashMap<>();
  D7HumanFixture human = new D7HumanFixture();
  ProcessEngineConfigurationImpl securedConfig;
  static String env(String name) {String value=System.getenv(name);if(value==null||value.isBlank())throw new IllegalStateException("explicit real integration setting missing: "+name);return value;}
  @BeforeAll void setup()throws Exception {
    String admin=env("MAEZO_HUMAN_IT_JDBC_URL");user=env("MAEZO_HUMAN_IT_DB_USER");password=env("MAEZO_HUMAN_IT_DB_PASSWORD");
    if(admin.matches("(?i).*([?&])(currentSchema|options)=.*"))throw new IllegalStateException("integration URL cannot override schema");
    schema="d7_it_"+UUID.randomUUID().toString().replace("-","");
    try(var c=DriverManager.getConnection(admin,user,password);var s=c.createStatement()){s.execute("CREATE SCHEMA "+schema);}
    url=admin+(admin.contains("?")?"&":"?")+"currentSchema="+schema;
    var config=config(false);engine=config.buildProcessEngine();
    try(var c=DriverManager.getConnection(url,user,password);var s=c.createStatement();var in=getClass().getResourceAsStream("/human-schema-postgres.sql")){s.execute(new String(in.readAllBytes(),StandardCharsets.UTF_8));s.execute("INSERT INTO MZO_HUMAN_TENANT VALUES('tenant-test',0)");}
    contas=deploy("SP-OP-CONTAS-001","operadora.contas.calculate_impact",false);
    escalation=deploy("SP-OP-ESCALATION-001","operadora.escalation.notify_team",true);
    pagto=deploy("SP-OP-PAGTO-001","synthetic-only-pagto",false);
    sourceContas=deploySourceHuman();
    if(temp==null)throw new IllegalStateException("explicit real integration setting missing: WorkloadEngineIT.temp");
    var manifest=Fixtures.manifest(temp);var original=Json.object(Json.list(manifest.get("peers")).get(0));
    List<Object> peers=new ArrayList<>();
    var agent=new HashMap<>(original);agent.put("capabilities",bindings("helena",List.of("helena.escalation.start.v1","helena.escalation.start.v1.read_active","helena.escalation.start.v1.read_history")));peers.add(agent);
    var worker=new HashMap<>(original);var id=new HashMap<>(Fixtures.identity());id.put("workload","worker_runtime");
    var cert=Fixtures.certificate("wrong-client");worker.put("certificate_sha256",BoundaryPolicy.hashCertificate(cert));
    worker.put("spki_sha256",br.com.maezo.human.Jcs.digest(cert.getPublicKey().getEncoded()));worker.put("subject_dn",cert.getSubjectX500Principal().getName());
    worker.put("identity",id);worker.put("engine_user","fixture-worker");worker.put("capabilities",bindings("worker_runtime",List.of(
        "contas.calculate_impact.complete.v1","contas.calculate_impact.complete.v1.fetch_lock","contas.calculate_impact.complete.v1.external_failure",
        "contas.calculate_impact.complete.v1.external_unlock","contas.calculate_impact.complete.v1.external_extend_lock","escalation.notify_team.bpmn_error.v1",
        "escalation.notify_team.bpmn_error.v1.fetch_lock","contas.pagto.handoff.v1")));peers.add(worker);manifest.put("peers",peers);
    grant("fixture-helena",Resources.PROCESS_DEFINITION,"SP-OP-ESCALATION-001",Permissions.READ,Permissions.READ_INSTANCE,Permissions.READ_HISTORY,Permissions.CREATE_INSTANCE);
    for(String key:List.of("SP-OP-ESCALATION-001","SP-OP-CONTAS-001"))
      grant("fixture-worker",Resources.PROCESS_DEFINITION,key,Permissions.READ,Permissions.READ_INSTANCE,Permissions.UPDATE_INSTANCE);
    grant("fixture-helena",Resources.PROCESS_INSTANCE,"*",Permissions.CREATE);
    grant("fixture-worker",Resources.PROCESS_INSTANCE,"*",Permissions.CREATE);
    grant("fixture-worker",Resources.PROCESS_DEFINITION,"SP-OP-PAGTO-001",Permissions.READ,Permissions.CREATE_INSTANCE);
    // READ_HISTORY applies to actual source-human provenance, not a new schema/grant category.
    var existing=engine.getAuthorizationService().createAuthorizationQuery().userIdIn("fixture-worker")
        .resourceType(Resources.PROCESS_DEFINITION).resourceId("SP-OP-CONTAS-001").singleResult();
    existing.addPermission(Permissions.READ_HISTORY);engine.getAuthorizationService().saveAuthorization(existing);
    engine.close();policy=Fixtures.load(temp,manifest);plugin=new WorkloadPlugin(policy);config=config(true);config.setProcessEnginePlugins(List.of(human.plugin(),plugin));securedConfig=config;engine=config.buildProcessEngine();
    human.attach(engine,url,user,password,schema);
  }
  @BeforeEach void realReceiptAndNativeHistoryWitness() throws Exception {
    var task=human.claimed();
    bootstrap(()->{engine.getTaskService().setVariableLocal(task.getId(),"d7_history_witness","synthetic");return null;});
    assertTrue(state().get("mzo_human_receipt").startsWith("1:") || receiptCount()>0);
  }
  List<Object> bindings(String workload,List<String> rows) {
    List<Object> result=new ArrayList<>();
    for(String row:rows) {
      var b=Fixtures.binding(row);var doc=Json.object(b.get("document"));var target=new HashMap<>(Json.object(doc.get("target")));
      target.put("definition_id",target.get("process_key").equals("SP-OP-CONTAS-001")?contas:target.get("process_key").equals("SP-OP-PAGTO-001")?pagto:escalation);doc.put("target",target);
      if(row.equals("contas.pagto.handoff.v1")) {
        doc.put("source_target",Map.of("process_key","SP-OP-CONTAS-001","process_version",2L,"definition_id",sourceContas,"topic","operadora.contas.handoff_pagamento","message",""));
        b.put("source_kind","locked_external");b.put("source_worker_id","fixture-worker");
        b.put("attestations",List.of(Map.of("name","fonte_valor","source_variable","fonte_valor","human_task_definition",""),
            Map.of("name","lastro_origem","source_variable","lastro_origem","human_task_definition",""),
            Map.of("name","lastro_decisor_id","source_variable","analista_id","human_task_definition","UT_AnalistaContas")));
      }
      b.put("digest",Json.digest(doc));
      var cap=new Capability(b);caps.put(row,cap);result.add(b);
    }return result;
  }
  ProcessEngineConfigurationImpl config(boolean authorization) {
    var c=(ProcessEngineConfigurationImpl)ProcessEngineConfiguration.createStandaloneProcessEngineConfiguration().setProcessEngineName("default")
        .setJdbcDriver("org.postgresql.Driver").setJdbcUrl(url).setJdbcUsername(user).setJdbcPassword(password)
        .setDatabaseSchemaUpdate("true").setJobExecutorActivate(false).setHistory("full").setAuthorizationEnabled(authorization).setTenantCheckEnabled(true);
    c.setMetricsEnabled(false);c.setEnforceHistoryTimeToLive(false);
    // Test-only observer preserves the command object and the product interceptor chain.
    if(authorization) {
      c.setCustomPostCommandInterceptorsTxRequired(List.of(new RaceCommandObserver()));
      c.setCustomPostCommandInterceptorsTxRequiresNew(List.of(new RaceCommandObserver()));
    }
    return c;
  }
  String deploy(String key,String topic,boolean error) {
    String boundary=error?"<error id=\"failure\" errorCode=\"ERR_ESC_NOTIFY_FAILED\"/>":"";
    String timer="<boundaryEvent id=\"d7Timer\" attachedToRef=\"external\"><timerEventDefinition><timeDuration>PT24H</timeDuration></timerEventDefinition></boundaryEvent><sequenceFlow id=\"timerFlow\" sourceRef=\"d7Timer\" targetRef=\"human\"/>";
    String handler=error?"<boundaryEvent id=\"failureBoundary\" attachedToRef=\"external\"><errorEventDefinition errorRef=\"failure\"/></boundaryEvent><sequenceFlow id=\"errFlow\" sourceRef=\"failureBoundary\" targetRef=\"human\"/>":timer;
    String xml="<?xml version=\"1.0\"?><definitions xmlns=\"http://www.omg.org/spec/BPMN/20100524/MODEL\" xmlns:camunda=\"http://camunda.org/schema/1.0/bpmn\" targetNamespace=\"urn:maezo:isolated-d7-test\">"+boundary+
        "<process id=\""+key+"\" isExecutable=\"true\"><startEvent id=\"start\"/><sequenceFlow id=\"first\" sourceRef=\"start\" targetRef=\"external\"/><serviceTask id=\"external\" camunda:type=\"external\" camunda:topic=\""+topic+"\"/><sequenceFlow id=\"second\" sourceRef=\"external\" targetRef=\"human\"/><userTask id=\"human\"/>"+handler+"</process></definitions>";
    var deployment=engine.getRepositoryService().createDeployment().tenantId("tenant-test").addString("isolated-"+key+".bpmn",xml).deploy();
    return engine.getRepositoryService().createProcessDefinitionQuery().deploymentId(deployment.getId()).singleResult().getId();
  }
  String deploySourceHuman() {
    String xml="<definitions xmlns=\"http://www.omg.org/spec/BPMN/20100524/MODEL\" xmlns:camunda=\"http://camunda.org/schema/1.0/bpmn\" targetNamespace=\"urn:maezo:isolated-source-negative\">"
        +"<process id=\"SP-OP-CONTAS-001\" isExecutable=\"true\"><startEvent id=\"start\"/><sequenceFlow id=\"first\" sourceRef=\"start\" targetRef=\"UT_AnalistaContas\"/>"
        +"<userTask id=\"UT_AnalistaContas\"/><sequenceFlow id=\"next\" sourceRef=\"UT_AnalistaContas\" targetRef=\"handoff\"/>"
        +"<serviceTask id=\"handoff\" camunda:type=\"external\" camunda:topic=\"operadora.contas.handoff_pagamento\"/></process></definitions>";
    var deployment=engine.getRepositoryService().createDeployment().tenantId("tenant-test").addString("synthetic-source-negative.bpmn",xml).deploy();
    return engine.getRepositoryService().createProcessDefinitionQuery().deploymentId(deployment.getId()).singleResult().getId();
  }
  void grant(String user,Resource resource,String resourceId,Permission... permissions) {
    var auth=engine.getAuthorizationService().createNewAuthorization(Authorization.AUTH_TYPE_GRANT);auth.setUserId(user);auth.setResource(resource);auth.setResourceId(resourceId);auth.setPermissions(permissions);engine.getAuthorizationService().saveAuthorization(auth);
  }
  <T>T bootstrap(Supplier<T> action) {engine.getIdentityService().clearAuthentication();try{return action.get();}finally{engine.getIdentityService().clearAuthentication();}}
  Map<String,Object> call(String row,Map<String,Object> request) {
    var cap=caps.get(row);var peer=policy.peers.stream().filter(p->p.identity().get("workload").equals(cap.identity.get("workload"))).findFirst().orElseThrow();
    engine.getIdentityService().setAuthentication(peer.engineUser(),List.of(),List.of(policy.tenant));BoundaryFilter.REQUEST_PEER.set(peer);
    try{return Json.parse(plugin.execute(peer,request));}finally{engine.getIdentityService().clearAuthentication();BoundaryFilter.REQUEST_PEER.remove();}
  }
  @Test void scopedStartAndHistoryAreReal() {
    String row="helena.escalation.start.v1";var r=Fixtures.request(caps.get(row));r.put("resource_ref",UUID.randomUUID().toString());
    var result=Json.object(call(row,r).get("result"));assertNotNull(result.get("id"));
    for(String operation:List.of("read_active","read_history")) {
      String read=row+"."+operation;var request=Fixtures.request(caps.get(read));request.put("resource_ref",r.get("resource_ref"));
      assertEquals(1,Json.list(call(read,request).get("result")).size());
    }
    String history=row+".read_history";var request=Fixtures.request(caps.get(history));request.put("resource_ref",r.get("resource_ref"));
    var authorization=bootstrap(()->engine.getAuthorizationService().createAuthorizationQuery().userIdIn("fixture-helena")
        .resourceType(Resources.PROCESS_DEFINITION).resourceId("SP-OP-ESCALATION-001").singleResult());
    try {
      bootstrap(()->{authorization.removePermission(Permissions.READ_HISTORY);engine.getAuthorizationService().saveAuthorization(authorization);return null;});
      assertThrows(Refused.class,()->call(history,request)); // Native filtered [] must not become “no instance”.
    } finally {
      bootstrap(()->{authorization.addPermission(Permissions.READ_HISTORY);engine.getAuthorizationService().saveAuthorization(authorization);return null;});
    }
    assertEquals(1,Json.list(call(history,request).get("result")).size());
  }
  @Test void externalLifecycleAndHumanMutationRefusal() {
    String instance=bootstrap(()->engine.getRuntimeService().startProcessInstanceById(contas,"life-"+UUID.randomUUID()).getId());
    String base="contas.calculate_impact.complete.v1",fetch=base+".fetch_lock";
    var fetched=Json.list(call(fetch,Fixtures.request(caps.get(fetch))).get("result"));assertFalse(fetched.isEmpty());
    String task=(String)Json.object(fetched.get(0)).get("id");
    for(String operation:List.of("external_extend_lock","external_unlock")) {
      String row=base+"."+operation;var request=Fixtures.request(caps.get(row));request.put("resource_ref",task);
      // This lifecycle success case needs a usable lock through the following unlock.
      if(operation.equals("external_extend_lock"))request.put("parameters",Map.of("newDuration",10000L));
      call(row,request);
    }
    assertFalse(Json.list(call(fetch,Fixtures.request(caps.get(fetch))).get("result")).isEmpty());
    var failed=Fixtures.request(caps.get(base+".external_failure"));failed.put("resource_ref",task);call(base+".external_failure",failed);
    // A later poll reacquires after retryTimeout; no fixed sleeps or mocked task states.
    var poll=Fixtures.request(caps.get(fetch));poll.put("parameters",Map.of("maxTasks",1L,"lockDuration",10000L,"asyncResponseTimeout",1000L,"variables",List.of()));
    assertFalse(Json.list(call(fetch,poll).get("result")).isEmpty());
    var complete=Fixtures.request(caps.get(base));complete.put("resource_ref",task);var altered=new HashMap<>(Json.object(complete.get("variables")));altered.put("decisao_auditor","fabricated");complete.put("variables",altered);
    assertThrows(Refused.class,()->call(base,complete));assertNotNull(bootstrap(()->engine.getExternalTaskService().createExternalTaskQuery().externalTaskId(task).singleResult()));
    complete.put("variables",Fixtures.request(caps.get(base)).get("variables"));call(base,complete);
    var human=bootstrap(()->engine.getTaskService().createTaskQuery().processInstanceId(instance).singleResult());assertNotNull(human);
    engine.getIdentityService().setAuthentication("fixture-worker",List.of(),List.of(policy.tenant));
    try {assertThrows(Refused.class,()->engine.getTaskService().complete(human.getId()));assertThrows(Refused.class,()->engine.getRuntimeService().setVariable(instance,"forbidden",true));}
    finally {engine.getIdentityService().clearAuthentication();}
    assertNotNull(bootstrap(()->engine.getTaskService().createTaskQuery().taskId(human.getId()).singleResult()));
    assertNull(bootstrap(()->engine.getRuntimeService().getVariable(instance,"forbidden")));
  }
  @Test void explicitBpmnErrorOnlyWithinReviewedTopic() {
    String id=bootstrap(()->engine.getRuntimeService().startProcessInstanceById(escalation,"err-"+UUID.randomUUID()).getId());
    String row="escalation.notify_team.bpmn_error.v1",fetch=row+".fetch_lock";
    var results=Json.list(call(fetch,Fixtures.request(caps.get(fetch))).get("result"));assertFalse(results.isEmpty());
    var request=Fixtures.request(caps.get(row));request.put("resource_ref",Json.object(results.get(0)).get("id"));request.put("error_code","OTHER_ERROR");
    assertThrows(Refused.class,()->call(row,request));request.put("error_code","ERR_ESC_NOTIFY_FAILED");call(row,request);
    assertNotNull(bootstrap(()->engine.getTaskService().createTaskQuery().processInstanceId(id).singleResult()));
  }

  BoundaryPolicy.Peer peer(String row) {
    var cap=caps.get(row);
    return policy.peers.stream().filter(p->p.identity().get("workload").equals(cap.identity.get("workload"))).findFirst().orElseThrow();
  }
  <T>T authenticated(String row,Supplier<T> action) {
    var p=peer(row);engine.getIdentityService().setAuthentication(p.engineUser(),List.of(),List.of(policy.tenant));
    BoundaryFilter.REQUEST_PEER.set(p);
    try{return action.get();}finally{engine.getIdentityService().clearAuthentication();BoundaryFilter.REQUEST_PEER.remove();}
  }
  void ready(String row) {
    var result=authenticated(row,()->Json.parse(plugin.readiness(peer(row))));
    assertEquals(true,result.get("ready"));assertFalse(Json.list(result.get("capabilities")).isEmpty());
  }
  Connection connection()throws SQLException {var c=DriverManager.getConnection(url,user,password);assertEquals(schema,c.getSchema());return c;}
  long receiptCount()throws Exception {
    try(var c=connection();var s=c.createStatement();var r=s.executeQuery("SELECT count(*) FROM mzo_human_receipt")){assertTrue(r.next());return r.getLong(1);}
  }
  Map<String,String> state()throws Exception {
    var result=new TreeMap<String,String>();
    try(var c=connection()) {
      c.setAutoCommit(false);c.setReadOnly(true);c.setTransactionIsolation(Connection.TRANSACTION_REPEATABLE_READ);
      for(String table:List.of("act_ru_task","act_ru_execution","act_ru_ext_task","act_ru_variable","act_hi_varinst",
          "act_hi_detail","act_hi_taskinst","act_hi_procinst","act_ru_identitylink","act_hi_identitylink",
          "act_ge_bytearray","act_re_deployment","act_re_procdef","act_ru_job","mzo_human_receipt")) {
        var hash=java.security.MessageDigest.getInstance("SHA-256");long count=0;
        try(var s=c.createStatement();var rows=s.executeQuery("SELECT to_jsonb(t)::text FROM "+table+" t ORDER BY to_jsonb(t)::text")) {
          while(rows.next()){byte[] bytes=rows.getString(1).getBytes(StandardCharsets.UTF_8);hash.update(java.nio.ByteBuffer.allocate(4).putInt(bytes.length).array());hash.update(bytes);count++;}
        }
        result.put(table,count+":"+HexFormat.of().formatHex(hash.digest()));
      }
      c.rollback();
    }
    for(String table:List.of("act_ru_task","act_ru_execution","act_ru_variable","act_hi_varinst","act_hi_detail","mzo_human_receipt"))
      assertFalse(result.get(table).startsWith("0:"),"nonempty actual native/history/receipt precondition "+table);
    return result;
  }
  static void refusal(Throwable failure,String code,int status) {
    while(!(failure instanceof Refused)&&failure.getCause()!=null)failure=failure.getCause();
    var denied=assertInstanceOf(Refused.class,failure);assertEquals(code,denied.code);assertEquals(status,denied.status);
  }
  String lockedTask(String definition) {
    String process=bootstrap(()->engine.getRuntimeService().startProcessInstanceById(definition,"d7-"+UUID.randomUUID()).getId());
    var task=bootstrap(()->engine.getExternalTaskService().createExternalTaskQuery().processInstanceId(process).singleResult());assertNotNull(task);
    bootstrap(()->{engine.getExternalTaskService().lock(task.getId(),"fixture-worker",60000);return null;});return task.getId();
  }
  Map<String,Object> completion(String task) {
    var request=Fixtures.request(caps.get("contas.calculate_impact.complete.v1"));request.put("resource_ref",task);return request;
  }
  @ParameterizedTest @ValueSource(strings={"CREATE","READ","READ_INSTANCE","READ_HISTORY","UPDATE_INSTANCE"})
  void everyNativeGrantGatesReadinessAndSamePendingOperation(String permissionName)throws Exception {
    boolean worker=permissionName.equals("UPDATE_INSTANCE");
    String row=worker?"contas.calculate_impact.complete.v1":"helena.escalation.start.v1";
    var request=worker?completion(lockedTask(contas)):Fixtures.request(caps.get(row));request.put("resource_ref",worker?request.get("resource_ref"):"grant-"+UUID.randomUUID());
    if(permissionName.equals("READ_INSTANCE")||permissionName.equals("READ_HISTORY")) {
      call(row,request);row+="."+(permissionName.equals("READ_HISTORY")?"read_history":"read_active");
      var read=Fixtures.request(caps.get(row));read.put("resource_ref",request.get("resource_ref"));request=read;
    }
    final String selected=row;final var pending=request;ready(row);
    var resource=permissionName.equals("CREATE")?Resources.PROCESS_INSTANCE:Resources.PROCESS_DEFINITION;
    String resourceId=permissionName.equals("CREATE")?"*":worker?"SP-OP-CONTAS-001":"SP-OP-ESCALATION-001";
    var grant=bootstrap(()->engine.getAuthorizationService().createAuthorizationQuery().userIdIn(peer(selected).engineUser()).resourceType(resource).resourceId(resourceId).singleResult());
    assertNotNull(grant);Permission permission=Permissions.valueOf(permissionName);var original=grant.getPermissions(Permissions.values());
    var before=state();
    try {
      bootstrap(()->{grant.removePermission(permission);engine.getAuthorizationService().saveAuthorization(grant);return null;});
      // READ is filtered by the native definition query before requireGrant;
      // the existing bound definition therefore produces the safe resource code.
      String code=permissionName.equals("READ")?"engine_resource_mismatch":"engine_profile_unavailable";
      int status=permissionName.equals("READ")?403:503;
      refusal(assertThrows(Refused.class,()->ready(selected)),code,status);
      refusal(assertThrows(Refused.class,()->call(selected,pending)),code,status);
      assertEquals(before,state());
    } finally {bootstrap(()->{grant.setPermissions(original);engine.getAuthorizationService().saveAuthorization(grant);return null;});}
    ready(selected);assertNotNull(call(selected,pending).get("result"));
  }
  @ParameterizedTest @ValueSource(strings={"TX_REQUIRED","REQUIRES_NEW"})
  void actualNativeCommandFencesAndBackgroundContext(String mode)throws Exception {
    var executor=mode.equals("TX_REQUIRED")?securedConfig.getCommandExecutorTxRequired():securedConfig.getCommandExecutorTxRequiresNew();
    AtomicBoolean reached=new AtomicBoolean();
    Command<Void> command=context->{assertNotNull(context);reached.set(true);return null;};
    var before=state();
    refusal(assertThrows(Refused.class,()->authenticated("helena.escalation.start.v1",()->executor.execute(command))),"engine_operation_denied",403);
    assertFalse(reached.get());assertEquals(before,state());
    bootstrap(()->executor.execute(command));assertTrue(reached.get(),"normal native background command must execute in actual context");
  }
  void blocked(int pid,String prefix,Future<?> pending)throws Exception {
    long until=System.nanoTime()+TimeUnit.SECONDS.toNanos(10);boolean observed=false;
    while(System.nanoTime()<until && !observed && !pending.isDone()) {
      try(var c=connection();var s=c.prepareStatement("SELECT count(*) FROM pg_stat_activity WHERE ?=ANY(pg_blocking_pids(pid)) AND query ILIKE ?")) {
        s.setInt(1,pid);s.setString(2,"%"+prefix+"%");try(var rows=s.executeQuery()){rows.next();observed=rows.getLong(1)>0;}
      }
      if(!observed)Thread.sleep(10);
    }
    assertTrue(observed,"actual expected SQL must be observed waiting on the owned PostgreSQL lock");
  }
  int pid(Connection c)throws SQLException {try(var s=c.createStatement();var r=s.executeQuery("SELECT pg_backend_pid()")){r.next();return r.getInt(1);}}
  @ParameterizedTest @ValueSource(strings={"expired-lock","changed-worker","policy-corrupt","policy-missing","native-grant-revoked"})
  void authorityChangesWhileActualExternalRowIsBlockedRollback(String fault)throws Exception {
    String row="contas.calculate_impact.complete.v1",task=lockedTask(contas);var pending=completion(task);ready(row);
    var baseline=state();byte[] policyBytes=Files.readAllBytes(policy.path);var executor=Executors.newSingleThreadExecutor();
    var grant=bootstrap(()->engine.getAuthorizationService().createAuthorizationQuery().userIdIn("fixture-worker").resourceType(Resources.PROCESS_DEFINITION).resourceId("SP-OP-CONTAS-001").singleResult());
    var permissions=grant.getPermissions(Permissions.values());
    try(var blocker=connection()) {
      blocker.setAutoCommit(false);int owner=pid(blocker);
      try(var s=blocker.prepareStatement("SELECT id_ FROM act_ru_ext_task WHERE id_=? FOR UPDATE")){s.setString(1,task);try(var r=s.executeQuery()){assertTrue(r.next());}}
      Future<?> result=executor.submit(()->call(row,pending));
      try {
        blocked(owner,"SELECT ID_ FROM ACT_RU_EXT_TASK",result);
        if(fault.equals("expired-lock")||fault.equals("changed-worker")) {
          try(var s=blocker.prepareStatement(fault.equals("expired-lock")?"UPDATE act_ru_ext_task SET lock_exp_time_=clock_timestamp()-interval '1 second' WHERE id_=?":"UPDATE act_ru_ext_task SET worker_id_='foreign-worker' WHERE id_=?")){s.setString(1,task);s.executeUpdate();}
        } else if(fault.equals("policy-corrupt"))Files.writeString(policy.path,"{}");
        else if(fault.equals("policy-missing"))Files.delete(policy.path);
        else bootstrap(()->{grant.removePermission(Permissions.UPDATE_INSTANCE);engine.getAuthorizationService().saveAuthorization(grant);return null;});
        // Snapshot must include the injected competing state, before releasing the victim.
        blocker.commit();
        var failure=assertThrows(ExecutionException.class,()->result.get(10,TimeUnit.SECONDS));
        refusal(failure, fault.startsWith("policy")||fault.equals("native-grant-revoked")?"engine_profile_unavailable":"engine_resource_mismatch",
            fault.startsWith("policy")||fault.equals("native-grant-revoked")?503:403);
        var after=state();
        if(fault.equals("expired-lock")||fault.equals("changed-worker")){baseline.remove("act_ru_ext_task");after.remove("act_ru_ext_task");}
        assertEquals(baseline,after,"only the explicitly injected competing ownership state may differ");
        assertNotNull(bootstrap(()->engine.getExternalTaskService().createExternalTaskQuery().externalTaskId(task).singleResult()));
        assertEquals(0,bootstrap(()->engine.getTaskService().createTaskQuery().processInstanceId(
            engine.getExternalTaskService().createExternalTaskQuery().externalTaskId(task).singleResult().getProcessInstanceId()).count()));
      } finally {blocker.rollback();Files.write(policy.path,policyBytes);result.cancel(true);}
    } finally {executor.shutdownNow();assertTrue(executor.awaitTermination(10,TimeUnit.SECONDS));bootstrap(()->{grant.setPermissions(permissions);engine.getAuthorizationService().saveAuthorization(grant);if(engine.getExternalTaskService().createExternalTaskQuery().externalTaskId(task).singleResult()!=null){engine.getExternalTaskService().unlock(task);engine.getExternalTaskService().lock(task,"fixture-worker",60000);}return null;});}
    ready(row);call(row,pending);assertNull(bootstrap(()->engine.getExternalTaskService().createExternalTaskQuery().externalTaskId(task).singleResult()));
  }
  @ParameterizedTest @ValueSource(strings={"policy-corrupt","policy-missing","native-grant-revoked"})
  void afterNativeFlushWaitAuthorityIsRecheckedAtCommitting(String fault)throws Exception {
    String row="contas.calculate_impact.complete.v1",task=lockedTask(contas);var pending=completion(task);ready(row);
    long key=Math.abs(UUID.randomUUID().getMostSignificantBits());byte[] saved=Files.readAllBytes(policy.path);
    var grant=bootstrap(()->engine.getAuthorizationService().createAuthorizationQuery().userIdIn("fixture-worker").resourceType(Resources.PROCESS_DEFINITION).resourceId("SP-OP-CONTAS-001").singleResult());var permissions=grant.getPermissions(Permissions.values());
    var pool=Executors.newSingleThreadExecutor();
    try(var c=connection();var statement=c.createStatement()) {
      statement.execute("CREATE FUNCTION d7_hold_flush() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN PERFORM pg_advisory_xact_lock("+key+"); RETURN NEW; END $$");
      statement.execute("CREATE TRIGGER d7_hold_flush BEFORE INSERT ON act_ru_task FOR EACH ROW EXECUTE FUNCTION d7_hold_flush()");
    }
    var before=state();
    try(var blocker=connection()) {
      blocker.setAutoCommit(false);int owner=pid(blocker);try(var s=blocker.createStatement()){s.execute("SELECT pg_advisory_xact_lock("+key+")");}
      Future<?> victim=pool.submit(()->call(row,pending));
      try {
        blocked(owner,"insert into ACT_RU_TASK",victim);
        if(fault.equals("policy-corrupt"))Files.writeString(policy.path,"{}");else if(fault.equals("policy-missing"))Files.delete(policy.path);
        else bootstrap(()->{grant.removePermission(Permissions.UPDATE_INSTANCE);engine.getAuthorizationService().saveAuthorization(grant);return null;});
        blocker.commit();
        refusal(assertThrows(ExecutionException.class,()->victim.get(10,TimeUnit.SECONDS)),"engine_profile_unavailable",503);
        assertEquals(before,state(),"COMMITTING refusal must roll back native flush and preserve real receipt/history rows");
      } finally {blocker.rollback();Files.write(policy.path,saved);victim.cancel(true);}
    } finally {
      pool.shutdownNow();assertTrue(pool.awaitTermination(10,TimeUnit.SECONDS));
      try(var c=connection();var s=c.createStatement()){s.execute("DROP TRIGGER d7_hold_flush ON act_ru_task");s.execute("DROP FUNCTION d7_hold_flush()");}
      bootstrap(()->{grant.setPermissions(permissions);engine.getAuthorizationService().saveAuthorization(grant);return null;});
    }
    ready(row);call(row,pending);
  }
  @ParameterizedTest @ValueSource(strings={"policy-corrupt","policy-missing"})
  void policyLossDuringActualD5EnlistedReceiptRollsBackHumanEffect(String fault)throws Exception {
    var task=human.claimed();var command=human.command(task,"release");
    var before=state();byte[] saved=Files.readAllBytes(policy.path);long key=Math.abs(UUID.randomUUID().getMostSignificantBits());
    try(var c=connection();var s=c.createStatement()) {
      s.execute("CREATE FUNCTION d7_hold_receipt() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN PERFORM pg_advisory_xact_lock("+key+"); RETURN NEW; END $$");
      s.execute("CREATE TRIGGER d7_hold_receipt AFTER INSERT ON mzo_human_receipt FOR EACH ROW EXECUTE FUNCTION d7_hold_receipt()");
    }
    var pool=Executors.newSingleThreadExecutor();
    try(var blocker=connection()) {
      blocker.setAutoCommit(false);int owner=pid(blocker);try(var s=blocker.createStatement()){s.execute("SELECT pg_advisory_xact_lock("+key+")");}
      Future<?> victim=pool.submit(()->{BoundaryFilter.REQUEST_PEER.set(peer("helena.escalation.start.v1"));try{return human.send(command);}finally{BoundaryFilter.REQUEST_PEER.remove();}});
      try {
        blocked(owner,"INSERT INTO MZO_HUMAN_RECEIPT",victim);
        if(fault.equals("policy-corrupt"))Files.writeString(policy.path,"{}");else Files.delete(policy.path);
        blocker.commit();refusal(assertThrows(ExecutionException.class,()->victim.get(10,TimeUnit.SECONDS)),"engine_profile_unavailable",503);
        assertEquals(before,state());
      } finally {blocker.rollback();Files.write(policy.path,saved);victim.cancel(true);}
    } finally {
      pool.shutdownNow();assertTrue(pool.awaitTermination(10,TimeUnit.SECONDS));
      try(var c=connection();var s=c.createStatement()){s.execute("DROP TRIGGER d7_hold_receipt ON mzo_human_receipt");s.execute("DROP FUNCTION d7_hold_receipt()");}
    }
    assertNotNull(human.send(command));
  }

  @Test void optimisticFetchReturnsOnlyActuallyCommittedOwnership()throws Exception {
    String task=lockedTask(contas);bootstrap(()->{engine.getExternalTaskService().unlock(task);return null;});
    String row="contas.calculate_impact.complete.v1.fetch_lock";ready(row);
    var start=new CyclicBarrier(2);var pool=Executors.newFixedThreadPool(2);
    Callable<List<?>> fetch=()->{start.await(5,TimeUnit.SECONDS);return Json.list(call(row,Fixtures.request(caps.get(row))).get("result"));};
    try {
      var first=pool.submit(fetch);var second=pool.submit(fetch);
      var results=new ArrayList<Object>(first.get(10,TimeUnit.SECONDS));results.addAll(second.get(10,TimeUnit.SECONDS));
      assertEquals(1,results.size(),"only the winning committed native fetch can advertise ownership");
      assertEquals(task,Json.object(results.get(0)).get("id"));
      var nativeTask=bootstrap(()->engine.getExternalTaskService().createExternalTaskQuery().externalTaskId(task).singleResult());
      assertEquals("fixture-worker",nativeTask.getWorkerId());
      assertEquals(nativeTask.getLockExpirationTime().getTime(),Json.object(results.get(0)).get("lock_expires_at"));
      call("contas.calculate_impact.complete.v1",completion(task));
    } finally {pool.shutdownNow();assertTrue(pool.awaitTermination(10,TimeUnit.SECONDS));}
  }
  @ParameterizedTest @ValueSource(strings={"wrong-task","wrong-worker","wrong-topic","wrong-process","wrong-tenant","wrong-definition-version"})
  void resourceAndIdentityMismatchesHavePositiveControlAndNoEffect(String fault)throws Exception {
    String task=lockedTask(contas),row="contas.calculate_impact.complete.v1";var request=completion(task);ready(row);
    byte[] saved=Files.readAllBytes(policy.path);var before=state();
    try {
      if(fault.equals("wrong-task"))request.put("resource_ref",lockedTask(escalation));
      else if(fault.equals("wrong-worker"))request.put("worker_id","foreign-worker");
      else if(fault.equals("wrong-topic"))request.put("topic","operadora.escalation.notify_team");
      else if(fault.equals("wrong-process"))request.put("process_key","SP-OP-ESCALATION-001");
      else if(fault.equals("wrong-tenant")) {
        before=state();var p=peer(row);engine.getIdentityService().setAuthentication(p.engineUser(),List.of(),List.of("foreign-tenant"));
        try{refusal(assertThrows(Refused.class,()->plugin.execute(p,request)),"engine_operation_denied",403);}finally{engine.getIdentityService().clearAuthentication();}
        assertEquals(before,state());return;
      } else {
        // Definition version is immutable. Bind the reviewed capability to a real ID
        // with an intentionally wrong version, then exercise the native lookup.
        var target=new HashMap<>(caps.get(row).target);target.put("process_version",99L);
        refusal(assertThrows(Refused.class,()->bootstrap(()->WorkloadCommand.definition(engine,target,policy.tenant))),"engine_resource_mismatch",403);
        assertEquals(before,state());return;
      }
      before=state();
      refusal(assertThrows(Refused.class,()->call(row,request)),fault.equals("wrong-task")?"engine_resource_mismatch":"engine_operation_denied",403);
      assertEquals(before,state());
    } finally {Files.write(policy.path,saved);ready(row);if(bootstrap(()->engine.getExternalTaskService().createExternalTaskQuery().externalTaskId(task).singleResult())!=null)call(row,completion(task));}
  }

  @ParameterizedTest @ValueSource(strings={"missing-decision","claim-only","release-only","wrong-task","wrong-definition","wrong-assignee","copied-actor-no-history","wrong-source-case"})
  void priorHumanEvidenceRequiresActualTaskDecisionProvenance(String fault)throws Exception {
    String row="contas.pagto.handoff.v1";
    String process=bootstrap(()->engine.getRuntimeService().startProcessInstanceById(sourceContas,"source-"+UUID.randomUUID()).getId());
    var sourceHuman=bootstrap(()->engine.getTaskService().createTaskQuery().processInstanceId(process).singleResult());assertNotNull(sourceHuman);
    bootstrap(()->{
      engine.getTaskService().setAssignee(sourceHuman.getId(),"human-test");
      engine.getRuntimeService().setVariables(process,Map.of("fonte_valor","liberado","lastro_origem","contas_adjudicacao_humana","analista_id","human-test"));
      if(!fault.equals("copied-actor-no-history"))engine.getTaskService().setVariableLocal(sourceHuman.getId(),"analista_id","human-test");
      engine.getTaskService().complete(sourceHuman.getId());return null;
    });
    var source=bootstrap(()->engine.getExternalTaskService().createExternalTaskQuery().processInstanceId(process).singleResult());assertNotNull(source);
    bootstrap(()->{engine.getExternalTaskService().lock(source.getId(),"fixture-worker",60000);return null;});
    // Genuine signed D5 receipts are obtained from its synthetic form. Adversarial
    // copies below are explicitly negative database-corruption inputs, NEVER a
    // positive PAGTO/consent receipt or activation proof.
    if(!fault.equals("missing-decision")&&!fault.equals("wrong-source-case")) {
      var genuine=human.claimed();String operation=fault.equals("claim-only")?"claim":fault.equals("release-only")?"release":"decision";
      Map<String,Object> receipt;
      if(operation.equals("claim")) {
        try(var c=connection();var query=c.prepareStatement("SELECT receipt_ FROM mzo_human_receipt WHERE task_=?")) {
          query.setString(1,genuine.getId());try(var r=query.executeQuery()){assertTrue(r.next());receipt=Json.parse(r.getString(1).getBytes(StandardCharsets.UTF_8));}
        }
      } else receipt=Json.parse(human.send(human.command(genuine,operation)));
      var attack=new HashMap<>(receipt);String forgedTask=fault.equals("wrong-task")?genuine.getId():sourceHuman.getId();
      attack.put("task_id",forgedTask);attack.put("principal_ref",fault.equals("wrong-assignee")?"foreign-assignee":"human-test");
      try(var c=connection();var insert=c.prepareStatement("INSERT INTO mzo_human_receipt(tenant_,task_,command_,digest_,principal_,workload_,receipt_) VALUES(?,?,?,?,?,?,?)")) {
        insert.setString(1,policy.tenant);insert.setString(2,sourceHuman.getId());insert.setString(3,(String)attack.get("command_id"));insert.setString(4,"a".repeat(64));
        insert.setString(5,(String)attack.get("principal_ref"));insert.setString(6,"gateway-test");insert.setString(7,new String(Json.bytes(attack),StandardCharsets.UTF_8));insert.executeUpdate();
      }
      if(fault.equals("wrong-definition"))try(var c=connection();var q=c.prepareStatement("UPDATE act_hi_taskinst SET proc_def_id_=? WHERE id_=?")){q.setString(1,contas);q.setString(2,sourceHuman.getId());q.executeUpdate();}
    }
    var request=Fixtures.request(caps.get(row));request.put("resource_ref","negative-pagto-"+UUID.randomUUID());
    request.put("source_ref",fault.equals("wrong-source-case")?lockedTask(contas):source.getId());
    var values=new HashMap<>(Json.object(request.get("variables")));values.put("tipo_pagamento","prestador_rede");values.put("moeda","BRL");
    values.put("fonte_valor","liberado");values.put("lastro_origem","contas_adjudicacao_humana");values.put("lastro_decisor_id","human-test");request.put("variables",values);
    caps.get(row).validate(request);ready(row);
    var before=state();
    refusal(assertThrows(Refused.class,()->call(row,request)),"engine_resource_mismatch",403);
    assertEquals(before,state());ready(row);
    assertEquals(0,bootstrap(()->engine.getRuntimeService().createProcessInstanceQuery().processDefinitionId(pagto).count()));
    // Reachability control is native start of the synthetic target only; never
    // presented as a source-human positive or a canonical financial decision.
    assertNotNull(bootstrap(()->engine.getRuntimeService().startProcessInstanceById(pagto,"target-reachability-"+UUID.randomUUID())));
  }

  // D7B-RACE-ORACLE-01: only the two explicitly submitted race futures set this scope.
  // No datasource, transaction, grant, native command or product fence is replaced.
  static final ThreadLocal<RaceCall> RACE_CALL = new ThreadLocal<>();
  record NativeBackend(int pid,String schema,String command,String backendStart) {}
  static final class RaceCall {
    final String role,command,schema,resourceHash,sql;
    volatile NativeBackend backend;
    RaceCall(String role,String command,String schema,String resource,String sql) {
      this.role=role;this.command=command;this.schema=schema;resourceHash=sqlHash(resource);this.sql=canonicalSql(sql);
    }
    <T>T invoke(Supplier<T> action) {
      assertNull(RACE_CALL.get(),"race observation scopes cannot nest");RACE_CALL.set(this);
      try{return action.get();}finally{RACE_CALL.remove();}
    }
  }
  static final class RaceCommandObserver extends CommandInterceptor {
    @Override public <T>T execute(Command<T> command) {
      var observation=RACE_CALL.get();
      if(observation!=null && command.getClass().getSimpleName().equals(observation.command)) {
        var context=org.cibseven.bpm.engine.impl.context.Context.getCommandContext();
        assertNotNull(context,"observation must run in the actual native CommandContext");
        var c=context.getDbSqlSession().getSqlSession().getConnection();
        // This read uses the enlisted native connection, not a guessed pg_stat_activity row.
        try(var q=c.createStatement()) {
          q.setQueryTimeout(2);
          try(var r=q.executeQuery("SELECT pg_backend_pid(),current_schema(),backend_start::text FROM pg_stat_activity WHERE pid=pg_backend_pid()")) {
          assertTrue(r.next());assertEquals(observation.schema,r.getString(2));
          var found=new NativeBackend(r.getInt(1),r.getString(2),command.getClass().getName(),r.getString(3));
          assertFalse(r.next());
          if(observation.backend!=null)assertEquals(observation.backend,found,"one observed native command must retain its actual backend");
          observation.backend=found;
          }
        } catch(SQLException e){throw new AssertionError("cannot bind the owned native race backend",e);}
      }
      return next.execute(command);
    }
  }
  static String canonicalSql(String sql) {
    return sql.replaceAll("\\$[0-9]+","?").replaceAll("\\s+"," ").replaceAll("\\s*=\\s*","=")
        .replaceAll("\\s*,\\s*",",").trim().replaceAll(";+$","").toLowerCase(Locale.ROOT);
  }
  static String sqlHash(String value) {return br.com.maezo.human.Jcs.digest(value.getBytes(StandardCharsets.UTF_8));}
  static boolean sameNativeWait(String actualSql,String expectedSql,String actualStart,String recordedStart) {
    return actualStart!=null && actualStart.equals(recordedStart) && canonicalSql(actualSql).equals(canonicalSql(expectedSql));
  }
  record WaitNode(int pid,String backendStart,String state,String waitType,String waitEvent,
      String sqlHash,boolean expectedSql,boolean ownedRelation,List<Integer> blockers,List<Object> locks) {
    boolean waiting() {return "active".equals(state)&&"Lock".equals(waitType)&&expectedSql&&ownedRelation;}
    Map<String,Object> safe() {
      var row=new LinkedHashMap<String,Object>();row.put("pid",pid);row.put("backend_start",backendStart);row.put("state",state);
      row.put("wait_type",waitType);row.put("wait_event",waitEvent);row.put("sql_sha256",sqlHash);row.put("expected_sql",expectedSql);
      row.put("owned_relation",ownedRelation);row.put("blocking_pids",blockers);row.put("locks",locks);return row;
    }
  }
  // The accepted universe is exactly owner, identified competitor and (optionally) victim.
  // An unrelated/absent/cyclic path never establishes this race's precondition.
  static boolean ownedWaitPath(Map<Integer,WaitNode> graph,int owner,int competitor,Integer victim) {
    if(owner<=0||competitor<=0||owner==competitor||victim!=null&&(victim<=0||victim==owner||victim==competitor))return false;
    var root=graph.get(owner);var first=graph.get(competitor);
    if(root==null||!root.ownedRelation()||!root.blockers().isEmpty()||first==null||!first.waiting()
        ||!new HashSet<>(first.blockers()).equals(Set.of(owner)))return false;
    if(victim==null)return true;
    var second=graph.get(victim);if(second==null||!second.waiting()||second.blockers().isEmpty())return false;
    return Set.of(owner,competitor).containsAll(second.blockers());
  }
  String nativeRaceSql(String action,Object task) {
    String mapping=action.equals("complete")||action.equals("timer")?"deleteExternalTask":"updateExternalTask";
    // Use the actual pinned CIB MyBatis template, including its ID/revision predicate.
    return securedConfig.getSqlSessionFactory().getConfiguration().getMappedStatement(mapping).getBoundSql(task).getSql();
  }
  Map<Integer,WaitNode> raceGraph(int owner,long relation,RaceCall competitor,RaceCall victim)throws Exception {
    var expected=new LinkedHashMap<Integer,RaceCall>();
    if(competitor.backend!=null)expected.put(competitor.backend.pid(),competitor);
    if(victim!=null && victim.backend!=null)expected.put(victim.backend.pid(),victim);
    var pids=new ArrayList<Integer>();pids.add(owner);pids.addAll(expected.keySet());
    var result=new LinkedHashMap<Integer,WaitNode>();
    try(var c=connection();var s=c.prepareStatement("SELECT pid,backend_start::text,state,wait_event_type,wait_event,query,pg_blocking_pids(pid) FROM pg_stat_activity WHERE datname=current_database() AND pid=ANY(?) ORDER BY pid")) {
      var ids=c.createArrayOf("integer",pids.toArray());
      try {
        s.setArray(1,ids);s.setQueryTimeout(2);
        try(var rows=s.executeQuery()) {
          while(rows.next()) {
            int id=rows.getInt(1);String query=canonicalSql(Objects.toString(rows.getString(6),""));
            List<Integer> blockers=new ArrayList<>();var blocking=rows.getArray(7);
            try {for(Object value:(Object[])blocking.getArray())blockers.add(((Number)value).intValue());}finally{blocking.free();}
            var observed=expected.get(id);boolean exact=observed!=null
                && sameNativeWait(query,observed.sql,rows.getString(2),observed.backend.backendStart());
            List<Object> locks=new ArrayList<>();boolean owned=false;
            try(var lock=c.prepareStatement("SELECT locktype,mode,granted,relation,page,tuple,transactionid::text FROM pg_locks WHERE pid=? AND (relation=?::oid OR locktype IN ('transactionid','virtualxid')) ORDER BY locktype,mode,granted LIMIT 33")) {
              lock.setInt(1,id);lock.setLong(2,relation);lock.setQueryTimeout(2);
              try(var lr=lock.executeQuery()) {
                while(lr.next()) {
                  var data=new LinkedHashMap<String,Object>();data.put("type",lr.getString(1));data.put("mode",lr.getString(2));data.put("granted",lr.getBoolean(3));
                  data.put("relation",lr.getString(4));data.put("page",lr.getString(5));data.put("tuple",lr.getString(6));data.put("transaction",lr.getString(7));locks.add(data);
                  if(lr.getLong(4)==relation&&lr.getBoolean(3))owned=true;
                }
              }
            }
            assertTrue(locks.size()<=32,"owned lock snapshot must stay bounded");
            result.put(id,new WaitNode(id,rows.getString(2),rows.getString(3),rows.getString(4),rows.getString(5),sqlHash(query),exact,owned,blockers,locks));
          }
        }
      } finally {ids.free();}
    }
    return result;
  }
  static final class EarlyRaceCompletion extends AssertionError {
    final Object originalResult;
    EarlyRaceCompletion(String role,Object result) {
      super(role+" completed before its required SQL wait; original result SHA-256="+br.com.maezo.human.Jcs.digest(Json.bytes(result)));
      originalResult=result; // Keep the actual return, while the emitted diagnostic redacts row values.
    }
  }
  static void preserveEarlyResult(Future<?> future,String role)throws Exception {
    if(future.isDone()) {
      // get() propagates the original ExecutionException/cause, not a generic wait timeout.
      Object result=future.get();
      throw new EarlyRaceCompletion(role,result);
    }
  }
  void observedRaceWait(String action,String stage,int owner,long relation,String tuple,RaceCall competitor,
      Future<?> competing,RaceCall victim,Future<?> pending)throws Exception {
    long started=System.nanoTime(),until=started+TimeUnit.SECONDS.toNanos(10);List<Object> evidence=new ArrayList<>();String previous="";
    Map<Integer,WaitNode> graph=Map.of();boolean accepted=false;
    try {
      while(System.nanoTime()<until) {
        preserveEarlyResult(competing,"competitor");if(pending!=null)preserveEarlyResult(pending,"victim");
        graph=raceGraph(owner,relation,competitor,victim);
        String signature=new String(Json.bytes(graph.values().stream().map(WaitNode::safe).toList()),StandardCharsets.UTF_8);
        if(!signature.equals(previous)&&evidence.size()<15){evidence.add(Map.of("observed_at",java.time.Instant.now().toString(),"nodes",graph.values().stream().map(WaitNode::safe).toList()));previous=signature;}
        if(System.nanoTime()<until && competitor.backend!=null && (victim==null||victim.backend!=null)
            && ownedWaitPath(graph,owner,competitor.backend.pid(),victim==null?null:victim.backend.pid())) {
          preserveEarlyResult(competing,"competitor");if(pending!=null)preserveEarlyResult(pending,"victim");accepted=true;return;
        }
        Thread.sleep(10);
      }
      preserveEarlyResult(competing,"competitor");if(pending!=null)preserveEarlyResult(pending,"victim");
      fail("owned native race wait not established within 10 seconds; see D7_RACE_WAIT evidence");
    } finally {
      var output=new LinkedHashMap<String,Object>();output.put("schema","d7-race-wait-observation-v1");output.put("action",action);output.put("stage",stage);output.put("accepted",accepted);output.put("elapsed_ms",TimeUnit.NANOSECONDS.toMillis(System.nanoTime()-started));
      output.put("owned_schema",schema);output.put("resource_sha256",competitor.resourceHash);output.put("relation_oid",relation);output.put("locked_tuple",tuple);output.put("owner_pid",owner);
      output.put("competitor",raceIdentity(competitor));output.put("victim",victim==null?Map.of():raceIdentity(victim));output.put("changes",evidence);
      output.put("final_nodes",graph.values().stream().map(WaitNode::safe).toList());
      System.out.println("D7_RACE_WAIT "+new String(Json.bytes(output),StandardCharsets.UTF_8));
    }
  }
  static Map<String,Object> raceIdentity(RaceCall call) {
    var result=new LinkedHashMap<String,Object>();result.put("role",call.role);result.put("command",call.command);result.put("expected_sql_sha256",sqlHash(call.sql));
    result.put("resource_sha256",call.resourceHash);
    if(call.backend!=null){result.put("pid",call.backend.pid());result.put("native_command",call.backend.command());result.put("schema",call.backend.schema());result.put("backend_start",call.backend.backendStart());}
    return result;
  }

  @ParameterizedTest @ValueSource(strings={"complete","unlock","extend","timer"})
  void competingNativeOwnershipActionAndTypedCompleteAreSerialized(String action)throws Exception {
    String task=lockedTask(contas),row="contas.calculate_impact.complete.v1";var request=completion(task);
    String process=bootstrap(()->engine.getExternalTaskService().createExternalTaskQuery().externalTaskId(task).singleResult().getProcessInstanceId());
    var jobs=bootstrap(()->engine.getManagementService().createJobQuery().processInstanceId(process).list());
    assertEquals(1,jobs.size(),"synthetic native timer must be deployed and reachable before the race");
    var nativeTask=bootstrap(()->engine.getExternalTaskService().createExternalTaskQuery().externalTaskId(task).singleResult());
    String command=switch(action){case "complete"->"CompleteExternalTaskCmd";case "unlock"->"UnlockExternalTaskCmd";case "extend"->"ExtendLockOnExternalTaskCmd";case "timer"->"ExecuteJobsCmd";default->throw new AssertionError();};
    var competingCall=new RaceCall("competitor",command,schema,task,nativeRaceSql(action,nativeTask));
    var victimCall=new RaceCall("victim","WorkloadCommand",schema,task,"SELECT ID_ FROM ACT_RU_EXT_TASK WHERE ID_=? AND TENANT_ID_=? FOR UPDATE");
    var pool=Executors.newFixedThreadPool(2);
    try(var blocker=connection()) {
      blocker.setAutoCommit(false);int owner=pid(blocker);
      try(var s=blocker.prepareStatement("SELECT id_ FROM act_ru_ext_task WHERE id_=? FOR UPDATE")){s.setString(1,task);try(var r=s.executeQuery()){assertTrue(r.next());}}
      long relation;String tuple;
      try(var q=blocker.prepareStatement("SELECT tableoid::bigint,ctid::text FROM act_ru_ext_task WHERE id_=?")) {
        q.setString(1,task);try(var r=q.executeQuery()){assertTrue(r.next());relation=r.getLong(1);tuple=r.getString(2);assertFalse(r.next());}
      }
      Future<?> competing=pool.submit(()->competingCall.invoke(()->bootstrap(()->{
        switch(action) {
          case "complete"->engine.getExternalTaskService().complete(task,"fixture-worker");
          case "unlock"->engine.getExternalTaskService().unlock(task);
          case "extend"->engine.getExternalTaskService().extendLock(task,"fixture-worker",60000);
          case "timer"->engine.getManagementService().executeJob(jobs.get(0).getId());
          default->throw new AssertionError();
        }return null;
      })));
      try {
        observedRaceWait(action,"competitor",owner,relation,tuple,competingCall,competing,null,null);
        Future<?> victim=pool.submit(()->victimCall.invoke(()->call(row,request)));
        try {
          observedRaceWait(action,"victim",owner,relation,tuple,competingCall,competing,victimCall,victim);blocker.commit();competing.get(10,TimeUnit.SECONDS);
          if(action.equals("extend"))assertNotNull(victim.get(10,TimeUnit.SECONDS));
          else refusal(assertThrows(ExecutionException.class,()->victim.get(10,TimeUnit.SECONDS)),"engine_resource_mismatch",403);
          if(action.equals("unlock")) {
            assertNotNull(bootstrap(()->engine.getExternalTaskService().createExternalTaskQuery().externalTaskId(task).singleResult()));
            bootstrap(()->{engine.getExternalTaskService().lock(task,"fixture-worker",60000);return null;});call(row,request);
          }
          assertEquals(1,bootstrap(()->engine.getTaskService().createTaskQuery().processInstanceId(process).count()));
          assertNull(bootstrap(()->engine.getExternalTaskService().createExternalTaskQuery().externalTaskId(task).singleResult()));
        } finally {blocker.rollback();victim.cancel(true);}
      } finally {blocker.rollback();competing.cancel(true);}
    } finally {pool.shutdownNow();assertTrue(pool.awaitTermination(10,TimeUnit.SECONDS));}
  }

  Map<String,Object> automaticSourceRequest() {
    String process=bootstrap(()->engine.getRuntimeService().startProcessInstanceById(sourceContas,"automatic-source-"+UUID.randomUUID()).getId());
    var humanTask=bootstrap(()->engine.getTaskService().createTaskQuery().processInstanceId(process).singleResult());
    bootstrap(()->{engine.getRuntimeService().setVariables(process,Map.of("fonte_valor","apresentado","lastro_origem","contas_adjudicacao_automatica","analista_id",""));engine.getTaskService().complete(humanTask.getId());return null;});
    var source=bootstrap(()->engine.getExternalTaskService().createExternalTaskQuery().processInstanceId(process).singleResult());
    bootstrap(()->{engine.getExternalTaskService().lock(source.getId(),"fixture-worker",60000);return null;});
    var request=Fixtures.request(caps.get("contas.pagto.handoff.v1"));var values=new HashMap<>(Json.object(request.get("variables")));
    values.put("tipo_pagamento","prestador_rede");values.put("moeda","BRL");values.put("fonte_valor","apresentado");values.put("lastro_origem","contas_adjudicacao_automatica");values.put("lastro_decisor_id","");
    request.put("variables",values);request.put("source_ref",source.getId());request.put("resource_ref","synthetic-pagto-"+UUID.randomUUID());return request;
  }
  @ParameterizedTest @ValueSource(strings={"source-expiry","policy-corrupt","native-history-revoked"})
  void realSourceAuthorityCannotExpireAfterLastTargetSql(String fault)throws Exception {
    String row="contas.pagto.handoff.v1";var request=automaticSourceRequest();ready(row);
    // This positive is the reviewed empty-evidence automatic synthetic branch only;
    // it establishes source-model reachability without fabricating a human decision.
    var positive=new HashMap<>(request);positive.put("resource_ref","source-control-"+UUID.randomUUID());assertNotNull(call(row,positive).get("result"));
    long deadline=System.currentTimeMillis()+3000,key=Math.abs(UUID.randomUUID().getMostSignificantBits());
    if(fault.equals("source-expiry"))try(var c=connection();var s=c.prepareStatement("UPDATE act_ru_ext_task SET lock_exp_time_=? WHERE id_=?")){s.setTimestamp(1,new Timestamp(deadline));s.setString(2,(String)request.get("source_ref"));s.executeUpdate();}
    var grant=bootstrap(()->engine.getAuthorizationService().createAuthorizationQuery().userIdIn("fixture-worker").resourceType(Resources.PROCESS_DEFINITION).resourceId("SP-OP-CONTAS-001").singleResult());var permissions=grant.getPermissions(Permissions.values());
    byte[] saved=Files.readAllBytes(policy.path);var before=state();var pool=Executors.newSingleThreadExecutor();
    try(var c=connection();var s=c.createStatement()) {
      s.execute("CREATE FUNCTION d7_hold_source_target() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN PERFORM pg_advisory_xact_lock("+key+"); RETURN NEW; END $$");
      s.execute("CREATE TRIGGER d7_hold_source_target BEFORE INSERT ON act_ru_ext_task FOR EACH ROW EXECUTE FUNCTION d7_hold_source_target()");
    }
    try(var blocker=connection()) {
      blocker.setAutoCommit(false);int owner=pid(blocker);try(var s=blocker.createStatement()){s.execute("SELECT pg_advisory_xact_lock("+key+")");}
      Future<?> victim=pool.submit(()->call(row,request));
      try {
        blocked(owner,"insert into ACT_RU_EXT_TASK",victim);
        if(fault.equals("source-expiry")){while(System.currentTimeMillis()<=deadline)Thread.sleep(10);}
        else if(fault.equals("policy-corrupt"))Files.writeString(policy.path,"{}");
        else bootstrap(()->{grant.removePermission(Permissions.READ_HISTORY);engine.getAuthorizationService().saveAuthorization(grant);return null;});
        blocker.commit();refusal(assertThrows(ExecutionException.class,()->victim.get(10,TimeUnit.SECONDS)),fault.equals("source-expiry")?"engine_resource_mismatch":"engine_profile_unavailable",fault.equals("source-expiry")?403:503);
        assertEquals(before,state());
      } finally {blocker.rollback();Files.write(policy.path,saved);victim.cancel(true);}
    } finally {
      pool.shutdownNow();assertTrue(pool.awaitTermination(10,TimeUnit.SECONDS));
      try(var c=connection();var s=c.createStatement()){s.execute("DROP TRIGGER d7_hold_source_target ON act_ru_ext_task");s.execute("DROP FUNCTION d7_hold_source_target()");}
      bootstrap(()->{grant.setPermissions(permissions);engine.getAuthorizationService().saveAuthorization(grant);engine.getExternalTaskService().unlock((String)request.get("source_ref"));engine.getExternalTaskService().lock((String)request.get("source_ref"),"fixture-worker",60000);return null;});
    }
    ready(row);call(row,request);
  }
  @Test void typedRequestWithMissingNativeAuthenticationIsRefused()throws Exception {
    String row="helena.escalation.start.v1";var request=Fixtures.request(caps.get(row));var before=state();
    engine.getIdentityService().clearAuthentication();
    refusal(assertThrows(Refused.class,()->plugin.execute(peer(row),request)),"engine_operation_denied",403);
    assertEquals(before,state());ready(row);assertNotNull(call(row,request).get("result"));
  }
  @AfterAll void cleanup()throws Exception {
    if(engine!=null)engine.close();
    if(schema!=null && url!=null)try(var c=DriverManager.getConnection(url,user,password);var s=c.createStatement()){s.execute("DROP SCHEMA "+schema+" CASCADE");}
  }
  @AfterEach void clearOwnedFixtureInstances() {
    if(engine!=null)bootstrap(()->{
      for(var instance:engine.getRuntimeService().createProcessInstanceQuery().tenantIdIn("tenant-test").list())
        engine.getRuntimeService().deleteProcessInstance(instance.getId(),"isolated D7 fixture teardown");
      return null;
    });
  }
}
