package br.com.maezo.workload;

import java.util.*;
import java.io.ByteArrayOutputStream;
import java.lang.reflect.Proxy;
import java.util.concurrent.atomic.AtomicLong;
import jakarta.servlet.ServletOutputStream;
import jakarta.servlet.WriteListener;
import jakarta.servlet.http.HttpServletResponse;
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
    var response=new NativeAuthDocumentProducerV2.Response(200,publication.committed().bytes(),1000L);
    var copy=response.body();copy[0]=0;assertArrayEquals(bytes,response.body());
  }
  @Test void finalReleaseRetainsOriginalCeilingAcrossAllHandoffs()throws Exception {
    for(String boundary:List.of("current","engine_return","policy_io","identity_restore","output_stream","status")) {
      var clock=new AtomicLong(900L);var bytes=new ByteArrayOutputStream();var events=new ArrayList<String>();
      int[] status={0};
      var output=new ServletOutputStream(){
        @Override public boolean isReady(){return true;}
        @Override public void setWriteListener(WriteListener ignored){}
        @Override public void write(int value){events.add("write");bytes.write(value);}
      };
      var servlet=(HttpServletResponse)Proxy.newProxyInstance(getClass().getClassLoader(),new Class<?>[]{HttpServletResponse.class},(proxy,method,args)->{
        if(method.getName().equals("getOutputStream")) {
          events.add("output_stream");if(boundary.equals("output_stream"))clock.set(1000L);return output;
        }
        if(method.getName().equals("setStatus")) {
          events.add("status");status[0]=(Integer)args[0];if(boundary.equals("status"))clock.set(1000L);return null;
        }
        throw new AssertionError(method.getName());
      });
      byte[] protectedBody=Json.bytes(Map.of("context","protected"));
      var original=new NativeAuthDocumentProducerV2.Response(200,protectedBody,1000L);
      protectedBody[0]=0;
      // Conversion must not discard the separate immutable context response.
      var encoded=new NativeOutcomeV2.Encoded(original.status(),original.body());
      assertArrayEquals(original.body(),encoded.bytes());
      for(String step:List.of("engine_return","policy_io","identity_restore")) {
        events.add(step);if(boundary.equals(step))clock.set(1000L);
      }
      original.writeTo(servlet,()->{events.add("original_deadline");return clock.get();});
      assertTrue(events.indexOf("original_deadline")>events.indexOf("identity_restore"));
      assertTrue(events.indexOf("original_deadline")>events.indexOf("output_stream"));
      assertTrue(events.indexOf("original_deadline")>events.indexOf("status"));
      assertEquals("write",events.get(events.indexOf("original_deadline")+1+(boundary.equals("current")?0:1)));
      assertEquals(boundary.equals("current")?200:503,status[0]);
      assertArrayEquals(boundary.equals("current")?original.body():NativeOutcomeV2.refusal("unavailable").bytes(),bytes.toByteArray());
    }
  }
}
