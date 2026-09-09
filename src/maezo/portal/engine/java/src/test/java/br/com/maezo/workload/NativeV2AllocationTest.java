package br.com.maezo.workload;

import static org.junit.jupiter.api.Assertions.*;
import java.util.*;
import org.apache.ibatis.session.Configuration;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;

/** Fixed mapping and native eligibility predicates only; actual lock/flush tests are EngineIT. */
class NativeV2AllocationTest {
  static Map<String,Object> task(Object expiry,Object suspension,Object retries){var m=new HashMap<String,Object>();m.put("expiry",expiry);m.put("suspension",suspension);m.put("retries",retries);return m;}
  @Test void nativeNullEligibilityAndExpiryEqualityArePreserved(){assertTrue(NativeFetchV2.eligible(task(null,null,null),100));assertTrue(NativeFetchV2.eligible(task(100L,1L,1L),100));assertTrue(NativeFetchV2.eligible(task(99L,null,null),100));assertFalse(NativeFetchV2.eligible(task(101L,1L,1L),100));}
  @Test void nativeSuspensionRetriesAndTypesRefuse(){for(Object s:List.of(0L,2L,true,"1"))assertFalse(NativeFetchV2.eligible(task(null,s,null),100));for(Object r:List.of(0L,-1L,true,1.0,"1"))assertFalse(NativeFetchV2.eligible(task(null,null,r),100));assertFalse(NativeFetchV2.eligible(task("100",null,null),100));}
  @Test void acquireEligibilityIsNotLiveConsumerEligibility(){var t=task(100L,1L,1L);t.put("definition","d");t.put("topic","t");t.put("worker","w");assertTrue(NativeFetchV2.eligible(t,100));assertThrows(Refused.class,()->NativeAcquisitionStoreV2.checkTask(t,Map.of("definition_id","d","topic","t"),"w",100));}
  @Test void wholeLockSetUsesStableUtf8OrderAndDeduplicates(){assertEquals(List.of("A","z","é","😀"),NativeAcquisitionStoreV2.ordered(List.of("😀","z","é","A","z")));}
  @ParameterizedTest @ValueSource(strings={"","a.b","a\";DROP SCHEMA x","a b","*","1bad"})
  void schemaIdentifiersRefuse(String s){assertThrows(Refused.class,()->NativeEnlistedWritesV2.identifier(s));}
  @Test void allMappedStatementsAreEnlistedDirtyUncachedSelects(){var c=new Configuration();NativeEnlistedWritesV2.install(c,"qualified_act");var names=c.getMappedStatementNames().stream().filter(n->n.startsWith(NativeEnlistedWritesV2.PREFIX)).toList();assertEquals(14,names.size());for(String name:names){var m=c.getMappedStatement(name);assertTrue(m.isDirtySelect(),name);assertFalse(m.isUseCache(),name);assertTrue(m.isFlushCacheRequired(),name);assertEquals(org.apache.ibatis.mapping.SqlCommandType.SELECT,m.getSqlCommandType());}}
  @Test void schemaMappingCannotBeRebound(){var c=new Configuration();NativeEnlistedWritesV2.install(c,"qualified_act");NativeEnlistedWritesV2.install(c,"qualified_act");assertThrows(Refused.class,()->NativeEnlistedWritesV2.install(c,"other_act"));}
  @Test void sqlValuesStayBoundAndWrongArityRefuses(){var c=new Configuration();NativeEnlistedWritesV2.install(c,"qualified_act");var m=c.getMappedStatement(NativeEnlistedWritesV2.PREFIX+"candidates");var b=m.getBoundSql(new Object[]{"tenant';SELECT 1--","d","t",1});assertFalse(b.getSql().contains("tenant'"));assertTrue(b.getSql().contains("lock_exp_time_<=clock_timestamp()"));assertTrue(b.getSql().contains("suspension_state_ IS NULL"));assertEquals(4,b.getParameterMappings().size());assertThrows(Refused.class,()->m.getBoundSql(new Object[]{"too-few"}));}
  @Test void sqlFunctionNamesAreClosedBeforeSessionUse(){assertThrows(Refused.class,()->NativeEnlistedWritesV2.call(null,"arbitrary",Map.of()));assertThrows(Refused.class,()->NativeEnlistedWritesV2.rows(null,"arbitrary"));}
  @Test void productionRegistryIsExplicitlyUninstalled()throws Exception{try(var in=CapabilityV2.class.getResourceAsStream("/engine-schemas-v2.json")){assertNotNull(in);var doc=Json.parse(in.readAllBytes());assertEquals("maezo.engine-schemas.v2",doc.get("protocol"));assertTrue(Json.list(doc.get("schemas")).isEmpty());}}

  // The following are real Java parser/selection predicates, never a SQL/engine simulation.
  static String ref(int i){byte[] b=new byte[32];java.nio.ByteBuffer.wrap(b).putInt(i);return Base64.getUrlEncoder().withoutPadding().encodeToString(b);}
  static Map<String,Object> historyRow(int i,boolean live){
    var r=new HashMap<String,Object>();r.put("acquisition_ref",ref(i));r.put("task_id","synthetic-task");
    r.put("owner_identity",NativeV2ProfileTest.identity());r.put("native_user","fixture-worker");r.put("worker_id","fixture-worker");
    r.put("target",CapabilityV2.target(Map.of("definition_id","synthetic-def","process_key","synthetic-key","process_version",1L,"topic","synthetic-topic","message","")));
    r.put("process_instance_id","synthetic-process");r.put("execution_id","synthetic-execution");r.put("activation_ref","synthetic-activation");
    r.put("runtime_generation",1L);r.put("decision_digest","a".repeat(64));r.put("fetch_capability_digest","b".repeat(64));r.put("command_id",ref(i));r.put("request_digest","c".repeat(64));
    r.put("lease_revision",1L);r.put("lock_expires_at",live?5000000L:1000L);r.put("state",live?"live":"closed");return r;
  }
  static Map<String,Object> guard(){return Map.of("runtime_generation",1L,"activation_ref","synthetic-activation","decision_digest","a".repeat(64));}
  static Map<String,Object> selection(String role,int i){return NativeAcquisitionStoreV2.selection(Map.of(role+"_ref","synthetic-task",role+"_acquisition",Map.of("acquisition_ref",ref(i),"lease_revision",1L)),List.of());}
  static String functionResult(List<Object> rows){return "{\"acquisitions\":["+rows.stream().map(r->new String(Json.bytes(r),java.nio.charset.StandardCharsets.UTF_8)).collect(java.util.stream.Collectors.joining(","))+"]}";}
  static Map<String,Object> parserCall(Map<String,Object> selection,List<Object> rows){
    var input=new HashMap<String,Object>(selection);input.put("guard",guard());
    String raw=functionResult(rows);
    var session=(org.apache.ibatis.session.SqlSession)java.lang.reflect.Proxy.newProxyInstance(NativeV2AllocationTest.class.getClassLoader(),new Class<?>[]{org.apache.ibatis.session.SqlSession.class},(p,m,args)->{
      assertEquals("selectList",m.getName());assertEquals(NativeEnlistedWritesV2.PREFIX+"read_acquisitions_v2",args[0]);
      assertEquals(input,Json.parse(((String)((Object[])args[1])[0]).getBytes(java.nio.charset.StandardCharsets.UTF_8)));return List.of(raw);
    });
    return NativeEnlistedWritesV2.call(session,"read_acquisitions_v2",input);
  }
  @Test void original1600ClosedHistoryParserFailureIsPreservedAndFiniteResultPasses(){
    List<Object> historical=new ArrayList<>();for(int i=0;i<1600;i++)historical.add(historyRow(i,false));historical.add(historyRow(1600,true));
    byte[] raw=functionResult(historical).getBytes(java.nio.charset.StandardCharsets.UTF_8);assertEquals(1601019,raw.length);assertEquals(1048576,Json.LIMIT);
    assertThrows(Refused.class,()->parserCall(selection("resource",1600),historical));
    for(String role:List.of("resource","source"))assertEquals(List.of(historyRow(1600,true)),parserCall(selection(role,1600),List.of(historyRow(1600,true))).get("acquisitions"));
    assertEquals(List.of(historyRow(1600,true)),parserCall(NativeAcquisitionStoreV2.selection(Map.of(),List.of("synthetic-task")),List.of(historyRow(1600,true))).get("acquisitions"));
  }
  @Test void sameTaskDeduplicatesOnlyIdenticalReferenceRevision(){
    var request=new HashMap<String,Object>();for(String role:List.of("resource","source")){request.put(role+"_ref","synthetic-task");request.put(role+"_acquisition",Map.of("acquisition_ref",ref(1),"lease_revision",1L));}
    assertEquals(selection("resource",1),NativeAcquisitionStoreV2.selection(request,List.of()));
    for(var changed:List.of(Map.of("acquisition_ref",ref(2),"lease_revision",1L),Map.of("acquisition_ref",ref(1),"lease_revision",2L))){request.put("source_acquisition",changed);assertThrows(Refused.class,()->NativeAcquisitionStoreV2.selection(request,List.of()));}
  }
  @Test void bothTaskOrdersHaveSameSortedSelectionAndFetchDeduplicates(){
    var reference=Map.of("acquisition_ref",ref(1),"lease_revision",1L);
    var one=NativeAcquisitionStoreV2.selection(Map.of("resource_ref","z","resource_acquisition",reference,"source_ref","A","source_acquisition",reference),List.of());
    var two=NativeAcquisitionStoreV2.selection(Map.of("resource_ref","A","resource_acquisition",reference,"source_ref","z","source_acquisition",reference),List.of());assertEquals(one,two);
    assertEquals(List.of("A","z"),Json.list(one.get("references")).stream().map(Json::object).map(r->r.get("task_id")).toList());
    assertEquals(List.of("A","z"),NativeAcquisitionStoreV2.selection(Map.of(),List.of("z","A","z")).get("fetch_task_ids"));
    assertThrows(Refused.class,()->NativeAcquisitionStoreV2.selection(Map.of("resource_ref","z","resource_acquisition",reference),List.of("z")));
  }
  @ParameterizedTest @ValueSource(strings={"closed","reference","revision","task","generation","activation","decision","extra","duplicate"})
  void actualParserRefusesUnselectedRows(String change){
    var row=historyRow(1600,true);switch(change){case "closed"->row.put("state","closed");case "reference"->row.put("acquisition_ref",ref(1));case "revision"->row.put("lease_revision",2L);case "task"->row.put("task_id","other");case "generation"->row.put("runtime_generation",2L);case "activation"->row.put("activation_ref","other");case "decision"->row.put("decision_digest","d".repeat(64));}
    List<Object> rows=new ArrayList<>(List.of(row));if(change.equals("extra"))rows.add(historyRow(1,false));if(change.equals("duplicate"))rows.add(row);
    assertThrows(Refused.class,()->parserCall(selection("resource",1600),rows));
  }
  @Test void onlyNeededLiveFetchPredecessorMayBelongToPriorGeneration(){
    var row=historyRow(1,true);row.put("runtime_generation",2L);row.put("activation_ref","prior");
    assertEquals(List.of(row),parserCall(NativeAcquisitionStoreV2.selection(Map.of(),List.of("synthetic-task")),List.of(row)).get("acquisitions"));
    row.put("task_id","unrelated");assertThrows(Refused.class,()->parserCall(NativeAcquisitionStoreV2.selection(Map.of(),List.of("synthetic-task")),List.of(row)));
  }
  static Map<String,Object> consumeRow(Map<String,Object> row){
    var task=Map.<String,Object>of("definition","synthetic-def","topic","synthetic-topic","worker","fixture-worker","expiry",5000000L,"process","synthetic-process","execution","synthetic-execution");
    return NativeAcquisitionStoreV2.checkConsumed(row,Map.of("acquisition_ref",ref(1),"lease_revision",1L),"synthetic-task",task,Json.object(historyRow(1,true).get("target")),"fixture-worker",NativeV2ProfileTest.identity(),"fixture-worker","b".repeat(64),guard(),1000);
  }
  @Test void exactOwnerResourceSourceCurrentConsumptionPasses(){var row=historyRow(1,true);assertEquals(row,consumeRow(row));}
  @ParameterizedTest @ValueSource(strings={"owner_identity","native_user","worker_id","target","process_instance_id","execution_id","runtime_generation","decision_digest","activation_ref","fetch_capability_digest","task_id","lease_revision","lock_expires_at","state"})
  void existingConsumptionChecksRemainExact(String field){var row=historyRow(1,true);Object old=row.get(field);row.put(field,old instanceof Long?2L:old instanceof Map?Map.of("wrong","identity"):"wrong");assertThrows(Refused.class,()->consumeRow(row));}
}
