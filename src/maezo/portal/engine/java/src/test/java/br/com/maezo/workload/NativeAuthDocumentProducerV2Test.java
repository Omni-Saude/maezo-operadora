package br.com.maezo.workload;

import java.util.*;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

/** Query codec/transaction publication tests only; no native execution claimed. */
class NativeAuthDocumentProducerV2Test {
  static final String REF=Base64.getUrlEncoder().withoutPadding().encodeToString(new byte[32]);
  static Map<String,Object> fixture() {
    var identity=Map.of("tenant","tenant","environment","test","workload","worker","workload_version","1",
      "issuer","issuer","subject","subject","origin","verified_mtls");
    var command=Map.of("engine","engine","database_incarnation","db","identity",identity,"native_user","native",
      "operation","fetch_lock","capability_digest","a".repeat(64),"command_id",REF,"request_digest","b".repeat(64),"activation_ref","active");
    return new HashMap<>(Map.of("protocol","maezo.auth-document-producer-query.v1","designation_digest","c".repeat(64),
      "query_id",REF,"reader_activation_ref","active","fetch_command",command,"resource_ref","task",
      "resource_acquisition",Map.of("acquisition_ref",REF,"lease_revision",2L)));
  }
  static Map<String,Object> copy(byte[] raw) {
    var out=new HashMap<String,Object>();
    Json.parse(raw).forEach((key,value)->out.put(key,value instanceof Map<?,?>?new HashMap<>(Json.object(value)):value));
    return out;
  }
  @Test void queryKeepsCurrentRenewalRevisionDistinctFromInitialReceipt() {
    var q=fixture();assertDoesNotThrow(()->NativeAuthDocumentProducerV2.validate(q));
    assertEquals(2L,Json.object(q.get("resource_acquisition")).get("lease_revision"));
    var bad=copy(Json.bytes(q));Json.object(bad.get("resource_acquisition")).put("lease_revision","2");
    assertThrows(Refused.class,()->NativeAuthDocumentProducerV2.validate(bad));
  }
  @Test void noExtraActorOrMutationCommandOrNoncanonicalReference() {
    var actor=fixture();actor.put("actor","human");assertThrows(Refused.class,()->NativeAuthDocumentProducerV2.validate(actor));
    var mutation=copy(Json.bytes(fixture()));Json.object(mutation.get("fetch_command")).put("operation","external_complete");
    assertThrows(Refused.class,()->NativeAuthDocumentProducerV2.validate(mutation));
    var reference=fixture();reference.put("query_id",REF+"=");assertThrows(Refused.class,()->NativeAuthDocumentProducerV2.validate(reference));
  }
  @Test void preparedObservationIsUnavailableUntilCommitted() {
    var publication=new NativeOutcomeV2.Publication();var bytes=Json.bytes(Map.of("private","observation"));
    publication.prepare(new NativeOutcomeV2.Encoded(200,bytes));
    assertThrows(Refused.class,publication::committed);publication.publish();
    var response=new NativeAuthDocumentProducerV2.Response(200,publication.committed().bytes());
    var copy=response.body();copy[0]=0;assertArrayEquals(bytes,response.body());
  }
}
