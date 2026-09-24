package br.com.maezo.human;

import static org.junit.jupiter.api.Assertions.*;
import java.sql.Timestamp;
import java.time.Instant;
import java.util.*;
import org.junit.jupiter.api.Test;

/** D-M / T-M2: the closed staff_escalation.v1 projection, pure part (the SQL runs in EngineSchemaEngineIT). */
class StaffEscalationTest {
  static final Instant START=Instant.parse("2026-09-24T12:00:00Z");
  static Map<String,Object> row(){
    var r=new HashMap<String,Object>();
    for(String n:List.of("instances","decisions","n_priority","n_ack","n_resolution","n_input","n_reason","n_routing"))r.put(n,1L);
    r.put("started",Timestamp.from(START));r.put("priority","P2");r.put("sla_ack","PT30M");r.put("sla_resolution","PT4H");
    r.put("reason","solicitacao_humano");r.put("reason_input","solicitacao_humano");return r;
  }
  @Test void resolvedCarriesAbsoluteDeadlinesFromEscalationStart(){
    var e=StaffEscalation.from("SYN-C1GUIA1",row());
    assertEquals("resolved",e.get("escalation_state"));assertEquals("SYN-C1GUIA1",e.get("guide_number"));
    assertEquals("solicitacao_humano",e.get("reason_code"));assertEquals("P2",e.get("priority"));
    assertEquals(PortalReadModels.time(START.plusSeconds(1800)),e.get("ack_due_at"));assertEquals(PortalReadModels.time(START.plusSeconds(4*3600)),e.get("resolution_due_at"));
    var keys=new HashSet<>(e.keySet());keys.remove("escalation_state");assertEquals(StaffEscalation.FIELDS,keys);
  }
  @Test void absentOrAmbiguousIsUnresolvedAndKeepsTheGuide(){
    var cases=new ArrayList<Map<String,Object>>();cases.add(null);
    for(String n:List.of("instances","decisions","n_priority","n_ack","n_resolution","n_input","n_reason","n_routing"))
      for(long v:new long[]{0,2}){var r=row();r.put(n,v);cases.add(r);}
    for(var r:cases){var e=StaffEscalation.from("123456789",r);
      assertEquals("unresolved",e.get("escalation_state"),String.valueOf(r));assertEquals("123456789",e.get("guide_number"));
      for(String f:List.of("reason_code","priority","ack_due_at","resolution_due_at")){assertTrue(e.containsKey(f));assertNull(e.get(f));}}
  }
  @Test void reasonIsACodeNeverFreeTextAndMustMatchTheDmnInput(){
    for(Object bad:new Object[]{"Paciente relatou dor","SOLICITACAO","", null,"a".repeat(65)}){var r=row();r.put("reason",bad);r.put("reason_input",bad);
      assertEquals("unresolved",StaffEscalation.from("g-1",r).get("escalation_state"),String.valueOf(bad));}
    var r=row();r.put("reason_input","falha_tecnica");assertEquals("unresolved",StaffEscalation.from("g-1",r).get("escalation_state"));
  }
  @Test void invalidPriorityOrDurationIsUnresolved(){
    for(Object p:new Object[]{"P0","p1","Alta",null}){var r=row();r.put("priority",p);assertEquals("unresolved",StaffEscalation.from("g",r).get("escalation_state"));}
    for(Object d:new Object[]{"PT0S","-PT5M","P1M","P400D","5 minutos",null,1800}){var r=row();r.put("sla_resolution",d);
      assertEquals("unresolved",StaffEscalation.from("g",r).get("escalation_state"),String.valueOf(d));}
    var r=row();r.put("sla_ack","PT5H");assertEquals("unresolved",StaffEscalation.from("g",r).get("escalation_state"));
    r=row();r.put("started",null);assertEquals("unresolved",StaffEscalation.from("g",r).get("escalation_state"));
    assertEquals(java.time.Duration.ofDays(1),StaffEscalation.duration("P1D"));
  }
  @Test void listMasksTheGuideDetailCarriesItWhole(){
    var e=StaffEscalation.from("123456789",row());
    assertEquals("***6789",StaffEscalation.forOperation(e,"list").get("guide_number"));
    assertEquals("123456789",StaffEscalation.forOperation(e,"detail").get("guide_number"));
    assertEquals("123456789",e.get("guide_number")); // the semantic state is never mutated by the mask
    assertEquals("***",StaffEscalation.mask("1234"));assertEquals("***",StaffEscalation.mask("1"));
    assertEquals("***UIA1",StaffEscalation.mask("SYN-C1GUIA1"));
    assertThrows(Rejected.class,()->StaffEscalation.forOperation(e,"finalize"));
  }
  @Test void onlyTheExactGrantedFieldSetAuthorizesTheProjection(){
    assertFalse(StaffEscalation.authorized(Map.of("staff_summary.v1",Set.of("case_ref"))));
    assertTrue(StaffEscalation.authorized(Map.of("staff_escalation.v1",StaffEscalation.FIELDS)));
    assertThrows(Rejected.class,()->StaffEscalation.authorized(Map.of("staff_escalation.v1",Set.of("guide_number"))));
  }
  @Test void escalationKeyUsesTheAnchorComponentRules(){
    assertEquals("ESC-amh-sla-auth-SYN-C1GUIA1",StaffEscalation.escalationKey("amh","SYN-C1GUIA1"));
    assertThrows(Rejected.class,()->StaffEscalation.escalationKey("a-b","1"));
    assertThrows(Rejected.class,()->StaffEscalation.escalationKey("amh","1 2"));
  }
  @Test void projectionIsOptionalInTheGrammarAndNeverRequiredForARead(){
    assertEquals(StaffEscalation.FIELDS,StaffCaseModels.FIELDS.get("staff_escalation.v1"));
    var entry=new HashMap<String,Object>(Map.of("role","read_requester","operations",List.of("detail","list"),
      "projections",List.of("staff_summary.v1","staff_identity.v1","staff_current_task.v1")));
    StaffCaseModels.requireReadCapabilities(entry,"detail");StaffCaseModels.requireReadCapabilities(entry,"list");
  }
  @Test void detailCarriesTheWholeGuideInsideCaseAndOmitsTheKeyWhenNotGranted(){
    var state=PortalReadModels.record("operation","detail","identity",PortalReadModels.record("identity",PortalReadModels.record("case_ref","c-1"),
      "native",PortalReadModels.record("state","active")),"event",PortalReadModels.record("revision","1","observed_at","2026-09-24T12:00:00Z"),
      "tasks",List.of(),"escalation",StaffEscalation.from("123456789",row()));
    var detail=StaffCaseReadCommand.projection(state,START,START.plusSeconds(10));
    assertFalse(detail.containsKey("escalation"));
    assertEquals("123456789",PortalReadModels.obj(PortalReadModels.obj(detail,"case"),"escalation").get("guide_number"));
    state.put("escalation",null);
    assertFalse(PortalReadModels.obj(StaffCaseReadCommand.projection(state,START,START.plusSeconds(10)),"case").containsKey("escalation"));
  }
  @Test void sqlReadsOnlyThePinnedSchemaAndNeverFreeTextVariables(){
    String sql=StaffEscalation.sql("cibseven");
    assertTrue(sql.contains("\"cibseven\".ACT_HI_DEC_OUT"));assertFalse(sql.contains("public."));
    assertFalse(sql.contains("'motivo'"));assertFalse(sql.contains("motivo_fallback"));
    assertThrows(Rejected.class,()->StaffEscalation.sql("public"));
    assertTrue(EngineSchema.TABLES.containsAll(List.of("act_hi_decinst","act_hi_dec_in","act_hi_dec_out","act_hi_varinst")));
  }
}
