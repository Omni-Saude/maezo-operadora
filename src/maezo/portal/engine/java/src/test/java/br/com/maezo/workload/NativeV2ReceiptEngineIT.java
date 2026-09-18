package br.com.maezo.workload;
import static org.junit.jupiter.api.Assertions.*;
import java.util.*;
import org.junit.jupiter.api.*;

/** Durable duplicate/outcome checks against the same actual qualified native database. */
@Tag("integration")
class NativeV2ReceiptEngineIT {
  @Test void duplicateAndOutcomeCarryReceiptWithoutReacquiring()throws Exception{
    var f=new NativeV2AdmissionEngineIT.Fixture();var request=f.template("receipt_fetch");byte[] bytes=Json.bytes(request);var first=f.operation(request);String before=f.technicalState();
    var response=f.send("/maezo-workload/v2/operations",bytes);assertEquals(200,response.statusCode());var duplicate=Json.parse(response.body());assertEquals("duplicate",duplicate.get("status"));assertNull(duplicate.get("result"));assertEquals(first.get("receipt"),duplicate.get("receipt"));assertEquals(before,f.technicalState());
    var query=new HashMap<>(Json.object(f.metadata.get("outcome_query")));query.put("command",first.get("command"));var history=f.send("/maezo-workload/v2/outcomes",Json.bytes(query));assertEquals(200,history.statusCode());var outcome=Json.parse(history.body());assertEquals("committed",outcome.get("status"));assertEquals(first.get("receipt"),outcome.get("receipt"));assertFalse(outcome.containsKey("result"));assertEquals(before,f.technicalState());
    var snapshot=Json.object(Json.list(Json.object(first.get("result")).get("acquisitions")).get(0));f.operation(f.consume("receipt_unlock",snapshot));
  }
  @Test void sameCommandIdWithDifferentExactBodyConflictsBeforeTaskRead()throws Exception{
    var f=new NativeV2AdmissionEngineIT.Fixture();var request=f.template("conflict_fetch");var first=f.operation(request);String before=f.technicalState();String changed=" "+new String(Json.bytes(request),java.nio.charset.StandardCharsets.UTF_8);var r=f.send("/maezo-workload/v2/operations",changed.getBytes(java.nio.charset.StandardCharsets.UTF_8));assertEquals(409,r.statusCode());var body=Json.parse(r.body());assertEquals("conflict",body.get("status"));assertNull(body.get("receipt"));assertNull(body.get("result"));assertEquals(before,f.technicalState());var snapshot=Json.object(Json.list(Json.object(first.get("result")).get("acquisitions")).get(0));f.operation(f.consume("conflict_unlock",snapshot));
  }
}
