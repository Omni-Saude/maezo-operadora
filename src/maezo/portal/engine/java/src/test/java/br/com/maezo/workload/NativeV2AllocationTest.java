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
}
