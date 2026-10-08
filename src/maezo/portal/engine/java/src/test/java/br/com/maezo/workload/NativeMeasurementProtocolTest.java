package br.com.maezo.workload;

import static org.junit.jupiter.api.Assertions.*;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;
import java.lang.reflect.*;
import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.util.*;
import br.com.maezo.human.Jcs;

/** UnitOnly grammar/time/binding-negative proofs. No surrogate instrument or mocked engine. */
class NativeMeasurementProtocolTest {
  private static Map<String,Object> config() {
    var d=new TreeMap<String,Object>();
    d.put("schema","provider-native-independent-measurement-config.v2");d.put("protocol","closed-jvm-pg-rendezvous-terminal-readback.v2");
    d.put("environment","TestOnly-unit");d.put("tenant","tenant-unit");d.put("engine_name","engine-unit");
    d.put("candidate_sha","a".repeat(40));d.put("expected_image_id","sha256:"+"b".repeat(64));
    for(String key:List.of("support_jar_sha256","instrument_class_sha256","observation_admission_sha256","root_pg_preboot_witness_sha256","request_sha256"))d.put(key,"c".repeat(64));
    d.put("instrument_class","br.com.maezo.workload.ProviderNativeIndependentMeasurement");d.put("instrument_jar_relative_path","lib/provider-auth-test-support.jar");
    d.put("protocol_schema_sha256",NativeMeasurementConfiguration.SCHEMA_SHA);d.put("observation_schema_sha256","d0affa2deb9c75e8aa2a52b52abc70e8052e81a9c1d98540af889ec32d27e2a1");
    String nonce="e".repeat(32);d.put("session_nonce",nonce);d.put("socket_path","/run/maezo-native-qualify/"+nonce+"/root.sock");
    d.put("outcome_socket_path","/run/maezo-native-qualify/"+nonce+"/outcome.sock");d.put("root_evidence_directory","/unit/measurement-"+nonce);
    d.put("root_peer_user","root-observer");d.put("root_peer_group","ipc");d.put("root_peer_uid",1001L);d.put("root_peer_gid",1002L);d.put("jvm_uid",1003L);d.put("jvm_gid",1002L);
    d.put("issued_at",RuntimeObservationAdmission.time(Instant.now().minusSeconds(1)));d.put("original_deadline",RuntimeObservationAdmission.time(Instant.now().plusSeconds(120)));
    d.put("maximum_stage_sequence",8L);d.put("maximum_endpoints",18L);d.put("maximum_total_frames",65L);d.put("maximum_frame_bytes",32768L);d.put("maximum_total_bytes",2097152L);
    d.put("maximum_connections",2L);d.put("maximum_terminal_records",1L);d.put("primary_channel_id","PRIMARY_RENDEZVOUS");d.put("outcome_channel_id","SEALED_PRIMARY_OUTCOME_READBACK");return d;
  }
  private static NativeMeasurementConfiguration construct(byte[] raw)throws Throwable {
    var c=NativeMeasurementConfiguration.class.getDeclaredConstructor(byte[].class,String.class,Map.class);c.setAccessible(true);
    var boundary=Map.<String,Object>of("environment","TestOnly-unit","tenant","tenant-unit","engine_name","engine-unit","expires_at",Instant.now().plusSeconds(1800).getEpochSecond());
    try{return c.newInstance(raw,Jcs.digest(raw),boundary);}catch(InvocationTargetException wrapped){throw wrapped.getCause();}
  }
  private static NativeMeasurementConfiguration construct(Map<String,Object> d)throws Throwable{return construct(Json.bytes(d));}
  @Test void typedPortHasOnlySevenClosedCallsAndTwoEndpoints() {
    assertEquals(Set.of("openStage","hold","awaitRootRelease","seal","stageClosed","complete","abort"),
        new HashSet<>(Arrays.stream(NativeMeasurementPort.class.getDeclaredMethods()).map(Method::getName).toList()));
    assertEquals(7,NativeMeasurementPort.class.getDeclaredMethods().length);assertArrayEquals(new NativeMeasurementPort.Endpoint[]{NativeMeasurementPort.Endpoint.ENTRY,NativeMeasurementPort.Endpoint.COMMITTING},NativeMeasurementPort.Endpoint.values());
  }
  @Test void validConfigIsImmutableTechnicalExpectationOnly()throws Throwable {
    var d=config();var c=construct(d);d.put("tenant","mutated");assertEquals("tenant-unit",c.tenant());
    byte[] raw=c.rawConfiguration();raw[0]=0;assertEquals('{',c.rawConfiguration()[0]);c.timeOnly();
    assertThrows(Refused.class,c::current); // A grammar-valid config cannot manufacture an admitted binding.
    assertTrue(Arrays.stream(NativeMeasurementConfiguration.class.getDeclaredMethods()).filter(m->!Modifier.isPrivate(m.getModifiers())).noneMatch(m->Map.class.isAssignableFrom(m.getReturnType())));
  }
  @ParameterizedTest @ValueSource(strings={"schema","protocol","environment","instrument_class","instrument_jar_relative_path","protocol_schema_sha256","observation_schema_sha256","socket_path","outcome_socket_path","original_deadline"})
  void malformedClosedFieldsRefuse(String key) {
    var d=config();d.put(key,"invalid");assertThrows(Refused.class,()->construct(d));
  }
  @ParameterizedTest @ValueSource(strings={"maximum_stage_sequence","maximum_endpoints","maximum_total_frames","maximum_frame_bytes","maximum_total_bytes","maximum_connections","maximum_terminal_records"})
  void limitsCannotBeRelaxedOrBoolean(String key) {
    var d=config();d.put(key,9999999L);assertThrows(Refused.class,()->construct(d));d.put(key,true);assertThrows(Refused.class,()->construct(d));d.put(key,8.0);assertThrows(Refused.class,()->construct(d));
  }
  @Test void unknownAndMissingFieldsRefuse() {
    var d=config();d.put("callback","arbitrary");assertThrows(Refused.class,()->construct(d));d.remove("callback");d.remove("root_peer_user");assertThrows(Refused.class,()->construct(d));
  }
  @Test void duplicateJsonAndBadUtf8Refuse() {
    byte[] raw=Json.bytes(config());String text=new String(raw,StandardCharsets.UTF_8);text=text.replaceFirst("\\{","{\"tenant\":\"other\",");
    String duplicate=text;assertThrows(Refused.class,()->construct(duplicate.getBytes(StandardCharsets.UTF_8)));
    assertThrows(Refused.class,()->construct(new byte[]{'{',(byte)0xff,'}'}));
  }
  @Test void literalSchemaDateStillRequiresRealDate() {
    var d=config();d.put("issued_at","2026-02-30T00:00:00.000000Z");assertThrows(Refused.class,()->construct(d));
  }
  @Test void noncePathsDistinctPrincipalsAndCanonicalDirectoryAreJoined() {
    var d=config();d.put("socket_path","/run/maezo-native-qualify/"+"f".repeat(32)+"/root.sock");var mismatched=d;assertThrows(Refused.class,()->construct(mismatched));
    d=config();d.put("root_peer_uid",1003L);var same=d;assertThrows(Refused.class,()->construct(same));
    d=config();d.put("root_evidence_directory","/unit/../unit/measurement-"+"e".repeat(32));var traversal=d;assertThrows(Refused.class,()->construct(traversal));
  }
  @Test void originalIssuedCeilingCannotBeRenewed() {
    var d=config();d.put("issued_at",RuntimeObservationAdmission.time(Instant.now().minusSeconds(1000)));assertThrows(Refused.class,()->construct(d));
  }
  @Test void expiredOrFutureIssuedRefuses() {
    var d=config();d.put("original_deadline",RuntimeObservationAdmission.time(Instant.now().minusSeconds(1)));var expired=d;assertThrows(Refused.class,()->construct(expired));
    d=config();d.put("issued_at",RuntimeObservationAdmission.time(Instant.now().plusSeconds(10)));var future=d;assertThrows(Refused.class,()->construct(future));
  }
  private static void endpointCompare(Map<String,Object> a,Map<String,Object> b)throws Throwable {
    var method=RuntimeDefinitionObservation.class.getDeclaredMethod("sameEndpointSession",Map.class,Map.class);method.setAccessible(true);
    try{method.invoke(null,a,b);}catch(InvocationTargetException wrapped){throw wrapped.getCause();}
  }
  @ParameterizedTest @ValueSource(strings={"backend_pid","backend_start","transaction_id_if_assigned","transaction_started_at","backend_ssl","backend_tls_version"})
  void ownEndpointWaitCannotChangeAnyOfTheSixSessionFacts(String key)throws Throwable {
    var a=RuntimeDefinitionObservationTest.sample("40",null,"2026-10-06T04:01:01.000000Z");
    var b=RuntimeDefinitionObservationTest.sample("40",null,"2026-10-06T04:01:02.000000Z");
    assertDoesNotThrow(()->endpointCompare(a,b));var changed=new TreeMap<>(Json.object(b.get("session_transaction")));
    changed.put(key,key.equals("backend_ssl")?false:"changed");b.put("session_transaction",changed);
    assertThrows(Refused.class,()->endpointCompare(a,b));
  }
  @Test void nullableXidAssignmentIsRefusedAtOwnWaitButPreservedAcrossNativeStage() {
    var a=RuntimeDefinitionObservationTest.sample("40",null,"2026-10-06T04:01:01.000000Z");
    var b=RuntimeDefinitionObservationTest.sample("40","101","2026-10-06T04:01:02.000000Z");
    assertThrows(Refused.class,()->endpointCompare(a,b));assertDoesNotThrow(()->RuntimeDefinitionObservation.sameSession(a,b));
  }
  @Test void fixedBinderRejectsUnrelatedClassWithoutConstructingAnything()throws Throwable {
    var config=construct(config());var method=NativeMeasurementBinding.class.getDeclaredMethod("verifyType",Class.class,java.nio.file.Path.class,NativeMeasurementConfiguration.class);method.setAccessible(true);
    var failure=assertThrows(InvocationTargetException.class,()->method.invoke(null,NativeMeasurementProtocolTest.class,java.nio.file.Path.of("/never-opened.jar"),config));
    assertInstanceOf(Refused.class,failure.getCause());
  }

  @Test void executorFailureAfterCaptureReturnAbortsAndPreservesPrimary() {
    var primary=new IllegalStateException("unit-only-executor-fault");var calls=new ArrayList<String>();
    var thrown=assertThrows(IllegalStateException.class,()->WorkloadPlugin.observationBoundary(()->{
      calls.add("capture-returned");throw primary;
    },()->calls.add("abort")));
    assertSame(primary,thrown);assertEquals(List.of("capture-returned","abort"),calls);
  }
  @Test void confirmationFailureAndFatalExecutorErrorBothAbort() {
    var calls=new ArrayList<String>();var primary=new AssertionError("unit-only-context-close");
    assertSame(primary,assertThrows(AssertionError.class,()->WorkloadPlugin.observationBoundary(()->{
      calls.add("executor-returned");throw primary;
    },()->calls.add("abort"))));
    assertEquals(List.of("executor-returned","abort"),calls);
  }
  @Test void abortFailureIsSuppressedWithoutMaskingOrSelfSuppression() {
    var primary=new IllegalStateException("unit-only-primary");var cleanup=new AssertionError("unit-only-abort");
    assertSame(primary,assertThrows(IllegalStateException.class,()->WorkloadPlugin.observationBoundary(()->{throw primary;},()->{throw cleanup;})));
    assertArrayEquals(new Throwable[]{cleanup},primary.getSuppressed());
    assertSame(primary,assertThrows(IllegalStateException.class,()->WorkloadPlugin.observationBoundary(()->{throw primary;},()->{throw primary;})));
  }
  @Test void nativeAuthorizationFailureAbortsBeforeFixedSafeTranslation() {
    var primary=new org.cibseven.bpm.engine.AuthorizationException("unit-only-native-text");var calls=new ArrayList<String>();
    var refused=assertThrows(Refused.class,()->WorkloadPlugin.observationBoundary(()->{throw primary;},()->calls.add("abort")));
    assertEquals(List.of("abort"),calls);assertEquals(503,refused.status);assertEquals("engine_profile_unavailable",refused.code);
    assertNull(refused.getCause());assertEquals(0,refused.getSuppressed().length);
  }
  @Test void successfulBoundaryDoesNotAbort() {
    var calls=new ArrayList<String>();assertEquals("closed-result",WorkloadPlugin.observationBoundary(()->"closed-result",()->calls.add("abort")));
    assertTrue(calls.isEmpty());
  }

}
