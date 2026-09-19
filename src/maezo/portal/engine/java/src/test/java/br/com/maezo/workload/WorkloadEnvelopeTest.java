package br.com.maezo.workload;

import static org.junit.jupiter.api.Assertions.*;
import java.nio.charset.StandardCharsets;
import java.util.*;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;

/** Pure wire/selection controls; native authentication and no-effect acceptance remain packaged gates. */
class WorkloadEnvelopeTest {
  private static Capability capability() {
    return new Capability(Fixtures.binding("helena.escalation.start.v1"));
  }

  private static void invalidBody(Map<String,Object> request) {
    // An empty admitted set would deny lookup; malformed shape must win before that lookup.
    Refused refused=assertThrows(Refused.class,()->Capability.forRequest(List.of(),request));
    assertEquals(400,refused.status);assertEquals("engine_invalid_body",refused.code);
    Refused direct=assertThrows(Refused.class,()->capability().validate(request));
    assertEquals(400,direct.status);assertEquals("engine_invalid_body",direct.code);
  }

  @ParameterizedTest @ValueSource(strings={"protocol","capability_digest","operation","process_key",
      "resource_ref","variables","correlation","all_matching","error_code","topic","message",
      "worker_id","parameters","source_ref"})
  void everyOuterFieldIsRequiredAndTypedBeforeLookup(String field) {
    var missing=Fixtures.request(capability());missing.remove(field);invalidBody(missing);
    var explicitNull=Fixtures.request(capability());explicitNull.put(field,null);invalidBody(explicitNull);
    var wrongType=Fixtures.request(capability());
    wrongType.put(field,wrongType.get(field) instanceof String?1L:List.of());
    invalidBody(wrongType);
  }

  @ParameterizedTest @ValueSource(strings={"extra","tenant_id","principal","valueInfo","variablesLocal"})
  void extraOuterFieldsRefuseBeforeLookup(String field) {
    var request=Fixtures.request(capability());request.put(field,"untrusted");invalidBody(request);
  }

  @Test void historicalSizeWitnessIsSchemaInvalidBelowTheActualByteLimit() {
    byte[] raw=("{\"x\":\""+"x".repeat(131073)+"\"}").getBytes(StandardCharsets.UTF_8);
    assertEquals(131081,raw.length);assertTrue(raw.length<Json.LIMIT);
    var parsed=assertDoesNotThrow(()->Json.parse(raw));
    assertEquals(Set.of("x"),parsed.keySet());invalidBody(parsed);
  }

  @Test void structurallyValidUnknownCapabilityRemainsDenied() {
    var request=Fixtures.request(capability());request.put("capability_digest","f".repeat(64));
    Refused refused=assertThrows(Refused.class,()->Capability.forRequest(List.of(capability()),request));
    assertEquals(403,refused.status);assertEquals("engine_operation_denied",refused.code);
  }

  @Test void selectionDoesNotReplaceCapabilitySpecificAuthorization() {
    var cap=capability();var request=Fixtures.request(cap);
    assertSame(cap,Capability.forRequest(List.of(cap),request));
    assertEquals(request.get("variables"),cap.validate(request));
    request.put("process_key","other-process");
    assertSame(cap,Capability.forRequest(List.of(cap),request));
    Refused refused=assertThrows(Refused.class,()->cap.validate(request));
    assertEquals(403,refused.status);assertEquals("engine_operation_denied",refused.code);
  }

  @Test void wrongPurposeStillRefusesBeforeBodyHandling()throws Exception {
    for(String purpose:List.of("observer","bootstrap","deployment","human-relay")) {
      Refused refused=assertThrows(Refused.class,()->BoundaryFilter.route(
          Fixtures.requestProxy(Fixtures.requestValues()),purpose));
      assertEquals(403,refused.status);assertEquals("engine_operation_denied",refused.code);
    }
  }

  @Test void exactParserByteLimitRemainsOneMiBWithOneByteOverRefused() {
    assertEquals(1_048_576,Json.LIMIT);
    var cap=capability();var request=Fixtures.request(cap);byte[] raw=Json.bytes(request);
    byte[] within=Arrays.copyOf(raw,Json.LIMIT);
    Arrays.fill(within,raw.length,within.length,(byte)' ');
    var parsed=assertDoesNotThrow(()->Json.parse(within));
    assertEquals(request,parsed);assertSame(cap,Capability.forRequest(List.of(cap),parsed));
    assertDoesNotThrow(()->cap.validate(parsed));
    byte[] over=Arrays.copyOf(within,Json.LIMIT+1);over[over.length-1]=' ';
    Refused refused=assertThrows(Refused.class,()->Json.parse(over));
    assertEquals(400,refused.status);assertEquals("engine_invalid_body",refused.code);
  }
}
