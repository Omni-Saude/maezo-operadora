package br.com.maezo.workload;
import static org.junit.jupiter.api.Assertions.*;
import java.util.*;
import org.junit.jupiter.api.*;

/** Actual mTLS/native task effects, never the finite eligibility model. ROOT lane only. */
@Tag("integration")
class NativeV2AcquisitionEngineIT {
  @Test void sameWorkerReacquisitionFencesSerializedOldCommand()throws Exception{
    var f=new NativeV2AdmissionEngineIT.Fixture();var first=f.fetched("aba_fetch");var delayed=f.consume("aba_complete",first);
    f.operation(f.consume("aba_unlock",first));var second=f.fetched("aba_fetch");assertEquals(first.get("task_ref"),second.get("task_ref"));assertNotEquals(first.get("acquisition_ref"),second.get("acquisition_ref"));assertEquals(1L,second.get("lease_revision"));
    String before=f.technicalState();var stale=f.send("/maezo-workload/v2/operations",Json.bytes(delayed));assertTrue(stale.statusCode()==403||stale.statusCode()==503);assertEquals(before,f.technicalState());
    f.operation(f.consume("aba_unlock",second));
  }
  @Test void renewalRetainsGenerationButCasRejectsOldRevision()throws Exception{
    var f=new NativeV2AdmissionEngineIT.Fixture();var first=f.fetched("renew_fetch");var old=f.consume("renew_extend",first);var applied=f.operation(old);var next=Json.object(Json.list(Json.object(applied.get("result")).get("acquisitions")).get(0));assertEquals(first.get("acquisition_ref"),next.get("acquisition_ref"));assertEquals((Long)first.get("lease_revision")+1,next.get("lease_revision"));
    var stale=new HashMap<>(old);stale.put("command_id",NativeOutcomeV2.newRef());String before=f.technicalState();var r=f.send("/maezo-workload/v2/operations",Json.bytes(stale));assertTrue(r.statusCode()==403||r.statusCode()==503);assertEquals(before,f.technicalState());f.operation(f.consume("renew_unlock",next));
  }
}
