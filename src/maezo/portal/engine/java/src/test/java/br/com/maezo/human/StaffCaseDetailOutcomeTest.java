package br.com.maezo.human;

import static org.junit.jupiter.api.Assertions.*;
import java.time.Instant;
import java.util.*;
import org.junit.jupiter.api.Test;

/** WP-J1-07 contract on the staff detail: `outcome` is a mandatory wire key, present exactly when `ended`. */
class StaffCaseDetailOutcomeTest {
  static final Instant NOW=Instant.parse("2026-09-24T12:00:00Z");
  static Map<String,Object> state(String caseState){
    var identity=new HashMap<String,Object>();identity.put("case_ref","8dd7f019-c68f-4f38-a8f3-4cc97c19befe");
    var identityState=new HashMap<String,Object>();identityState.put("identity",identity);identityState.put("native",Map.of("state",caseState));
    var state=new HashMap<String,Object>();state.put("operation","detail");state.put("identity",identityState);
    state.put("event",Map.of("revision","1","observed_at","2026-09-24T11:59:00Z"));state.put("tasks",List.of());
    return state;
  }
  @Test void activeDetailCarriesTheOutcomeKeyAsNull(){
    var detail=StaffCaseReadCommand.projection(state("active"),NOW,NOW.plusSeconds(10));
    assertTrue(detail.containsKey("outcome"),"the BFF refuses a detail without the outcome key");
    assertNull(detail.get("outcome"));
  }
  @Test void endedDetailRefusesInsteadOfEmittingANullOutcome(){
    assertThrows(RuntimeException.class,()->StaffCaseReadCommand.projection(state("ended"),NOW,NOW.plusSeconds(10)));
  }
}
