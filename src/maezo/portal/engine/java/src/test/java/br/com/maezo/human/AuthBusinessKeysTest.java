package br.com.maezo.human;

import static org.junit.jupiter.api.Assertions.*;

import org.junit.jupiter.api.Test;

/** T1.11: the engine composes the AUTH key exactly like tools/process_business_keys.py (shared vector). */
class AuthBusinessKeysTest {
  private Object vectors() throws Exception {
    try(var stream=getClass().getResourceAsStream("/auth-business-key-vectors.json")) {
      assertNotNull(stream);return Jcs.parse(stream.readAllBytes());
    }
  }
  @Test void sharedVectorKeysMatchPythonComposer() throws Exception {
    var v=Jcs.object(vectors());
    for(Object item:PortalReadModels.list(v.get("keys"))) {
      var row=Jcs.object(item);
      assertEquals(row.get("key"),AuthBusinessKeys.auth((String)row.get("tenant"),(String)row.get("numero_guia_tiss")));
    }
    for(Object item:PortalReadModels.list(v.get("refused_keys"))) {
      var row=Jcs.object(item);
      assertThrows(Rejected.class,()->AuthBusinessKeys.auth((String)row.get("tenant"),(String)row.get("numero_guia_tiss")),row.toString());
    }
    assertThrows(Rejected.class,()->AuthBusinessKeys.auth(null,"1"));
  }
  @Test void sharedVectorGuideNumbersMatchDtoPattern() throws Exception {
    var v=Jcs.object(vectors());
    for(Object n:PortalReadModels.list(v.get("guide_numbers_valid")))assertTrue(AuthModels.GUIDE_NUMBER.matcher((String)n).matches(),(String)n);
    for(Object n:PortalReadModels.list(v.get("guide_numbers_invalid")))assertFalse(AuthModels.GUIDE_NUMBER.matcher((String)n).matches(),(String)n);
  }
  @Test void caseUpstreamKeyIsTheGuideNumberRecoveredFromTheBusinessKey() {
    var claim=java.util.Map.<String,Object>of("tenant_","amh");
    assertEquals("SYN-C1GUIA1",NativeCaseIdentityReader.guideNumber(claim,java.util.Map.of("historic_business_key","AUTH-amh-SYN-C1GUIA1")));
    for(String bad:new String[]{"AUTHI-guide-ref","AUTH-other-123","AUTH-amh-","AUTH-amh-12/3"})
      assertThrows(RuntimeException.class,()->NativeCaseIdentityReader.guideNumber(claim,java.util.Map.of("historic_business_key",bad)),bad);
    assertThrows(RuntimeException.class,()->NativeCaseIdentityReader.guideNumber(claim,java.util.Map.of()));
  }
  @Test void legacyIntakePrefixIsNotTheContractualOne() {
    assertFalse(AuthBusinessKeys.auth("amh","G1").startsWith("AUTHI-"));
  }
}
