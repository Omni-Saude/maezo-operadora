package br.com.maezo.workload;

import static org.junit.jupiter.api.Assertions.*;
import java.nio.charset.StandardCharsets;
import java.util.*;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;

class CapabilityTest {
  @Test void positiveStartPreservesFullFields() {
    Capability cap=new Capability(Fixtures.binding("helena.escalation.start.v1"));var req=Fixtures.request(cap);
    assertEquals(req.get("variables"),cap.validate(req));
    assertEquals("helena",Json.object(req.get("variables")).get("source_agent_id"));
  }
  @Test void preservesR173LongTypeAndIntegerTypes() {
    var values=Capability.engineVariables(Map.of("total_glosado_candidato_centavos",1L,"count",1L,"huge",2147483648L));
    assertEquals("long",((org.cibseven.bpm.engine.variable.value.TypedValue)values.get("total_glosado_candidato_centavos")).getType().getName());
    assertEquals("integer",((org.cibseven.bpm.engine.variable.value.TypedValue)values.get("count")).getType().getName());
    assertEquals("long",((org.cibseven.bpm.engine.variable.value.TypedValue)values.get("huge")).getType().getName());
  }
  @ParameterizedTest @ValueSource(strings={"contas.calculate_impact.complete.v1","escalation.notify_team.bpmn_error.v1","contas.calculate_impact.complete.v1.fetch_lock","contas.calculate_impact.complete.v1.external_failure","contas.calculate_impact.complete.v1.external_extend_lock","contas.calculate_impact.complete.v1.external_unlock","helena.escalation.start.v1.read_active","helena.escalation.start.v1.read_history"})
  void validReviewedLifecycle(String row) {var cap=new Capability(Fixtures.binding(row));assertDoesNotThrow(()->cap.validate(Fixtures.request(cap)));}
  @ParameterizedTest @ValueSource(strings={"tenant_id","source_agent_id","source_agent_version"})
  void wrongBoundIdentity(String name) {var cap=new Capability(Fixtures.binding("helena.escalation.start.v1"));var r=Fixtures.request(cap);Json.object(r.get("variables")).put(name,"wrong");assertThrows(Refused.class,()->cap.validate(r));}
  @ParameterizedTest @ValueSource(strings={"decisao_auditor","assignee","lastro_confirmado","variablesLocal","startInstructions","valueInfo"})
  void exactFieldsRejectPrivileges(String field) {var cap=new Capability(Fixtures.binding("helena.escalation.start.v1"));var r=Fixtures.request(cap);Json.object(r.get("variables")).put(field,true);assertThrows(Refused.class,()->cap.validate(r));}
  @ParameterizedTest @ValueSource(strings={"operation","process_key","topic","message","worker_id","capability_digest","protocol"})
  void wrongScope(String field) {var cap=new Capability(Fixtures.binding("helena.escalation.start.v1"));var r=Fixtures.request(cap);r.put(field,"wrong");assertThrows(Refused.class,()->cap.validate(r));}
  @Test void deploymentCannotWidenReviewedSchemaEvenWithNewDigest() {
    var b=Fixtures.binding("helena.escalation.start.v1");var d=Json.object(b.get("document"));var s=new HashMap<>(Json.object(d.get("schema")));
    s.put("fields",List.of());d.put("schema",s);b.put("digest",Json.digest(d));assertThrows(Refused.class,()->new Capability(b));
  }
  @Test void noDmnGrantCanBeInvented() {assertTrue(Capability.schemas().values().stream().noneMatch(s->s.get("operation").toString().startsWith("dmn_")));}
  @Test void all47RowsAreRegistered() {assertEquals(47,Capability.schemas().size());}
  @Test void workerMayNotInjectHumanVariablesOnFailure() {var cap=new Capability(Fixtures.binding("contas.calculate_impact.complete.v1.external_failure"));var r=Fixtures.request(cap);r.put("variables",Map.of("decisao",true));assertThrows(Refused.class,()->cap.validate(r));}
  @Test void dossierInitMustRemainNull() {var cap=new Capability(Fixtures.binding("lucas.escalation.start.v1"));var r=Fixtures.request(cap);Json.object(r.get("variables")).put("dossie_lucas",Map.of("decisao_cancelamento","fabricated"));assertThrows(Refused.class,()->cap.validate(r));}
  @ParameterizedTest @ValueSource(strings={"{\"a\":1,\"a\":2}","{\"n\":{\"x\":null,\"x\":1}}","{\"a\":NaN}","{\"a\":9223372036854775808}","{}{}","[]","{\"a\":\"\\ud800\"}","{\"a\":1e999}"})
  void strictMalformedJson(String value) {assertThrows(Refused.class,()->Json.parse(value.getBytes(StandardCharsets.UTF_8)));}
  @Test void jsonDepthAndSizeBound() {
    assertThrows(Refused.class,()->Json.parse(("{\"a\":".repeat(18)+"0"+"}".repeat(18)).getBytes(StandardCharsets.UTF_8)));
    assertThrows(Refused.class,()->Json.parse(new byte[Json.LIMIT+1]));
    assertThrows(Refused.class,()->Json.parse(new byte[]{'{','"','a','"',':','"',(byte)0xc0,(byte)0x80,'"','}'}));
  }
  @Test void floatBooleanNullAndIntRemainDistinct() {
    var parsed=Json.parse("{\"d\":1.0,\"i\":1,\"b\":true,\"n\":null}".getBytes(StandardCharsets.UTF_8));
    assertInstanceOf(Double.class,parsed.get("d"));assertInstanceOf(Long.class,parsed.get("i"));assertInstanceOf(Boolean.class,parsed.get("b"));assertNull(parsed.get("n"));
  }
  @Test void onlyDecisionReceiptWithMatchingAssigneeIsHumanEvidence() {
    var receipt=new HashMap<String,Object>(Map.of("schema","human-engine-receipt.v1","status","committed","operation","decision",
        "tenant","tenant","task_id","task","command_id","command","principal_ref","human"));
    assertTrue(WorkloadCommand.decisionReceipt(receipt,"tenant","task","command","human","human"));
    assertFalse(WorkloadCommand.decisionReceipt(receipt,"tenant","task","command","human","other"));
    for(String operation:List.of("claim","release")) {receipt.put("operation",operation);assertFalse(WorkloadCommand.decisionReceipt(receipt,"tenant","task","command","human","human"));}
  }
}
