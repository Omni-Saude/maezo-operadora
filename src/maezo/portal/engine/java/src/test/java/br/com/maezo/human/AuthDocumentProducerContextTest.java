package br.com.maezo.human;

import java.util.*;
import org.junit.jupiter.api.Test;
import br.com.maezo.workload.Json;
import static org.junit.jupiter.api.Assertions.*;

/** Closed designation/profile controls; no mocked engine or persistence acceptance. */
class AuthDocumentProducerContextTest {
  static Map<String,Object> fixture() {
    Map<String,Object> d=new TreeMap<>();
    d.put("schema","auth-document-producer-designation.v1");d.put("designation_ref","designation");d.put("revision","1");
    d.put("identity",Map.of("tenant","tenant","environment","test","workload","worker","workload_version","1",
      "issuer","issuer","subject","subject","origin","verified_mtls"));d.put("native_user","worker");
    d.put("scope",Map.of("tenant","tenant","environment","test","engine_name","engine","database_incarnation","db",
      "installation_ref","install","installation_revision","1"));d.put("fetch_capability_digest","a".repeat(64));
    d.put("target",Map.of("process_key","SP-OP-AUTH-001","process_version",1L,"definition_id","definition",
      "topic","operadora.auth.request_documents","message",""));
    d.put("activity_id","ST_SolicitarDocumentos");d.put("action","auth.document_request.observe");
    d.put("projection_digest",AuthDocumentProducerContext.projectionDigest());
    d.put("source",AuthNativeCodecTest.policy().get("source"));
    d.put("valid_from","2026-09-10T00:00:00.000000Z");d.put("valid_until","2026-09-11T00:00:00.000000Z");
    d.put("state","active");return d;
  }
  static Map<String,Object> copy(byte[] raw) {
    var out=new HashMap<String,Object>();
    Json.parse(raw).forEach((key,value)->out.put(key,value instanceof Map<?,?>?new HashMap<>(Json.object(value)):value));
    return out;
  }
  @Test void nativeTargetIntegerAndEmbeddedNumberFreeScopeRoundTrip() {
    var d=fixture();var actual=AuthDocumentProducerContext.designation(copy(Json.bytes(d)));
    assertEquals(d,actual);assertEquals(1L,Json.object(actual.get("target")).get("process_version"));
    assertEquals("1",Jcs.object(actual.get("scope")).get("installation_revision"));
    assertEquals(14,AuthDocumentProducerContext.PROJECTION.size());
    assertFalse(AuthDocumentProducerContext.PROJECTION.containsKey("required_codes"));
  }
  @Test void scalarAndUnknownKeysNeverCoerceAcrossProfiles() {
    var d=fixture();Json.object(d.get("target"));
    var bad=copy(Json.bytes(d));Json.object(bad.get("target")).put("process_version","1");
    assertThrows(RuntimeException.class,()->AuthDocumentProducerContext.designation(bad));
    var scope=copy(Json.bytes(d));Json.object(scope.get("scope")).put("installation_revision",1L);
    assertThrows(RuntimeException.class,()->AuthDocumentProducerContext.designation(scope));
    var identity=copy(Json.bytes(d));Json.object(identity.get("identity")).put("actor","human");
    assertThrows(RuntimeException.class,()->AuthDocumentProducerContext.designation(identity));
  }
  @Test void wrongPurposeScopeTaskOrProjectionRefuses() {
    for(String key:List.of("action","activity_id","projection_digest")) {
      var d=fixture();d.put(key,"other");assertThrows(RuntimeException.class,()->AuthDocumentProducerContext.designation(d));
    }
    var d=copy(Json.bytes(fixture()));Json.object(d.get("identity")).put("tenant","other");
    assertThrows(RuntimeException.class,()->AuthDocumentProducerContext.designation(d));
    var target=copy(Json.bytes(fixture()));Json.object(target.get("target")).put("topic","agents.events.auth.pended");
    assertThrows(RuntimeException.class,()->AuthDocumentProducerContext.designation(target));
  }
}
