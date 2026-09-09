package br.com.maezo.workload;

import static org.junit.jupiter.api.Assertions.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.sql.*;
import java.util.*;
import java.util.function.Supplier;
import org.cibseven.bpm.engine.*;
import org.cibseven.bpm.engine.authorization.*;
import org.cibseven.bpm.engine.impl.cfg.ProcessEngineConfigurationImpl;
import org.junit.jupiter.api.*;
import org.junit.jupiter.api.io.TempDir;

/** ROOT-only real CIB 2.1/PostgreSQL command tests. No engine mocks, credential skips or HTTP claims. */
@Tag("integration") @TestInstance(TestInstance.Lifecycle.PER_CLASS)
class WorkloadEngineIT {
  @TempDir Path temp;
  ProcessEngine engine;WorkloadPlugin plugin;BoundaryPolicy policy;
  String url,user,password,schema,contas,escalation;
  Map<String,Capability> caps=new HashMap<>();
  static String env(String name) {String value=System.getenv(name);if(value==null||value.isBlank())throw new IllegalStateException("explicit real integration setting missing: "+name);return value;}
  @BeforeAll void setup()throws Exception {
    String admin=env("MAEZO_HUMAN_IT_JDBC_URL");user=env("MAEZO_HUMAN_IT_DB_USER");password=env("MAEZO_HUMAN_IT_DB_PASSWORD");
    if(admin.matches("(?i).*([?&])(currentSchema|options)=.*"))throw new IllegalStateException("integration URL cannot override schema");
    schema="d7_it_"+UUID.randomUUID().toString().replace("-","");
    try(var c=DriverManager.getConnection(admin,user,password);var s=c.createStatement()){s.execute("CREATE SCHEMA "+schema);}
    url=admin+(admin.contains("?")?"&":"?")+"currentSchema="+schema;
    var config=config(false);engine=config.buildProcessEngine();
    try(var c=DriverManager.getConnection(url,user,password);var s=c.createStatement();var in=getClass().getResourceAsStream("/human-schema-postgres.sql")){s.execute(new String(in.readAllBytes(),StandardCharsets.UTF_8));}
    contas=deploy("SP-OP-CONTAS-001","operadora.contas.calculate_impact",false);
    escalation=deploy("SP-OP-ESCALATION-001","operadora.escalation.notify_team",true);
    var manifest=Fixtures.manifest(temp);var original=Json.object(Json.list(manifest.get("peers")).get(0));
    List<Object> peers=new ArrayList<>();
    var agent=new HashMap<>(original);agent.put("capabilities",bindings("helena",List.of("helena.escalation.start.v1","helena.escalation.start.v1.read_active","helena.escalation.start.v1.read_history")));peers.add(agent);
    var worker=new HashMap<>(original);var id=new HashMap<>(Fixtures.identity());id.put("workload","worker_runtime");
    var cert=Fixtures.certificate("wrong-client");worker.put("certificate_sha256",BoundaryPolicy.hashCertificate(cert));
    worker.put("spki_sha256",br.com.maezo.human.Jcs.digest(cert.getPublicKey().getEncoded()));worker.put("subject_dn",cert.getSubjectX500Principal().getName());
    worker.put("identity",id);worker.put("engine_user","fixture-worker");worker.put("capabilities",bindings("worker_runtime",List.of(
        "contas.calculate_impact.complete.v1","contas.calculate_impact.complete.v1.fetch_lock","contas.calculate_impact.complete.v1.external_failure",
        "contas.calculate_impact.complete.v1.external_unlock","contas.calculate_impact.complete.v1.external_extend_lock","escalation.notify_team.bpmn_error.v1",
        "escalation.notify_team.bpmn_error.v1.fetch_lock")));peers.add(worker);manifest.put("peers",peers);
    grant("fixture-helena",Resources.PROCESS_DEFINITION,"SP-OP-ESCALATION-001",Permissions.READ,Permissions.READ_INSTANCE,Permissions.READ_HISTORY,Permissions.CREATE_INSTANCE);
    for(String key:List.of("SP-OP-ESCALATION-001","SP-OP-CONTAS-001"))
      grant("fixture-worker",Resources.PROCESS_DEFINITION,key,Permissions.READ,Permissions.READ_INSTANCE,Permissions.UPDATE_INSTANCE);
    grant("fixture-helena",Resources.PROCESS_INSTANCE,"*",Permissions.CREATE);
    engine.close();policy=Fixtures.load(temp,manifest);plugin=new WorkloadPlugin(policy);config=config(true);config.setProcessEnginePlugins(List.of(plugin));engine=config.buildProcessEngine();
  }
  List<Object> bindings(String workload,List<String> rows) {
    List<Object> result=new ArrayList<>();
    for(String row:rows) {
      var b=Fixtures.binding(row);var doc=Json.object(b.get("document"));var target=new HashMap<>(Json.object(doc.get("target")));
      target.put("definition_id",target.get("process_key").equals("SP-OP-CONTAS-001")?contas:escalation);doc.put("target",target);b.put("digest",Json.digest(doc));
      var cap=new Capability(b);caps.put(row,cap);result.add(b);
    }return result;
  }
  ProcessEngineConfigurationImpl config(boolean authorization) {
    var c=(ProcessEngineConfigurationImpl)ProcessEngineConfiguration.createStandaloneProcessEngineConfiguration().setProcessEngineName("default")
        .setJdbcDriver("org.postgresql.Driver").setJdbcUrl(url).setJdbcUsername(user).setJdbcPassword(password)
        .setDatabaseSchemaUpdate("true").setJobExecutorActivate(false).setHistory("full").setAuthorizationEnabled(authorization).setTenantCheckEnabled(true);
    c.setMetricsEnabled(false);c.setEnforceHistoryTimeToLive(false);return c;
  }
  String deploy(String key,String topic,boolean error) {
    String boundary=error?"<error id=\"failure\" errorCode=\"ERR_ESC_NOTIFY_FAILED\"/>":"";
    String handler=error?"<boundaryEvent id=\"failureBoundary\" attachedToRef=\"external\"><errorEventDefinition errorRef=\"failure\"/></boundaryEvent><sequenceFlow id=\"errFlow\" sourceRef=\"failureBoundary\" targetRef=\"human\"/>":"";
    String xml="<?xml version=\"1.0\"?><definitions xmlns=\"http://www.omg.org/spec/BPMN/20100524/MODEL\" xmlns:camunda=\"http://camunda.org/schema/1.0/bpmn\" targetNamespace=\"urn:maezo:isolated-d7-test\">"+boundary+
        "<process id=\""+key+"\" isExecutable=\"true\"><startEvent id=\"start\"/><sequenceFlow id=\"first\" sourceRef=\"start\" targetRef=\"external\"/><serviceTask id=\"external\" camunda:type=\"external\" camunda:topic=\""+topic+"\"/><sequenceFlow id=\"second\" sourceRef=\"external\" targetRef=\"human\"/><userTask id=\"human\"/>"+handler+"</process></definitions>";
    var deployment=engine.getRepositoryService().createDeployment().tenantId("tenant-test").addString("isolated-"+key+".bpmn",xml).deploy();
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
      String row=base+"."+operation;var request=Fixtures.request(caps.get(row));request.put("resource_ref",task);call(row,request);
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
