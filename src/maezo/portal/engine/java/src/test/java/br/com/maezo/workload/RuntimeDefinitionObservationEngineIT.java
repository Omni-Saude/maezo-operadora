package br.com.maezo.workload;

import static org.junit.jupiter.api.Assertions.*;
import br.com.maezo.human.Jcs;
import java.io.*;
import java.net.*;
import java.net.http.*;
import java.nio.file.*;
import java.security.*;
import java.sql.*;
import java.time.*;
import java.util.*;
import javax.net.ssl.*;
import org.junit.jupiter.api.*;

/** ROOT lane only: served CIB/PG and genuine mTLS. Missing explicit material fails, never skips. */
@Tag("integration") @TestMethodOrder(MethodOrderer.OrderAnnotation.class)
class RuntimeDefinitionObservationEngineIT {
  static HttpClient client;static URI origin;static byte[] request;static Map<String,Object> admission;
  static String env(String key){String value=System.getenv(key);if(value==null || value.isBlank())throw new IllegalStateException("real integration input absent: "+key);return value;}
  static Path path(String key)throws IOException {Path p=Path.of(env(key));assertEquals(p,p.toRealPath());return p;}
  @BeforeAll static void setup()throws Exception {
    origin=URI.create(env("MAEZO_NATIVE_OBSERVATION_IT_ORIGIN"));
    assertEquals("https",origin.getScheme());assertNull(origin.getUserInfo());assertNull(origin.getQuery());assertNull(origin.getFragment());
    assertTrue(Set.of("localhost","127.0.0.1").contains(origin.getHost()));assertEquals("",origin.getPath());
    char[] password=Files.readString(path("MAEZO_NATIVE_OBSERVATION_IT_KEYSTORE_PASSWORD_FILE")).strip().toCharArray();
    try {
      var key=KeyStore.getInstance("PKCS12");try(var in=Files.newInputStream(path("MAEZO_NATIVE_OBSERVATION_IT_KEYSTORE"))){key.load(in,password);}
      var trust=KeyStore.getInstance("PKCS12");try(var in=Files.newInputStream(path("MAEZO_NATIVE_OBSERVATION_IT_TRUSTSTORE"))){trust.load(in,password);}
      assertEquals(1,trust.size());var managers=KeyManagerFactory.getInstance(KeyManagerFactory.getDefaultAlgorithm());managers.init(key,password);
      var anchors=TrustManagerFactory.getInstance(TrustManagerFactory.getDefaultAlgorithm());anchors.init(trust);
      var tls=SSLContext.getInstance("TLS");tls.init(managers.getKeyManagers(),anchors.getTrustManagers(),new SecureRandom());
      var ssl=new SSLParameters();ssl.setEndpointIdentificationAlgorithm("HTTPS");ssl.setProtocols(new String[]{"TLSv1.3","TLSv1.2"});
      client=HttpClient.newBuilder().sslContext(tls).sslParameters(ssl).followRedirects(HttpClient.Redirect.NEVER).connectTimeout(Duration.ofSeconds(5)).build();
    }finally{Arrays.fill(password,'\0');}
    request=Files.readAllBytes(path("MAEZO_NATIVE_OBSERVATION_IT_REQUEST"));RuntimeObservationAdmission.validate("Request",Json.parse(request));
    admission=Json.parse(Files.readAllBytes(path("MAEZO_NATIVE_OBSERVATION_IT_ADMISSION")));RuntimeObservationAdmission.validate("ObservationAdmission",admission);
    assertEquals(Jcs.digest(Files.readAllBytes(path("MAEZO_NATIVE_OBSERVATION_IT_ADMISSION"))),Json.object(Json.parse(request).get("parameters")).get("admission_sha256"));
  }
  static HttpResponse<InputStream> send(String route,byte[] body)throws Exception {
    var req=HttpRequest.newBuilder(origin.resolve(route)).timeout(Duration.ofSeconds(30)).header("Content-Type","application/json")
        .POST(HttpRequest.BodyPublishers.ofByteArray(body)).build();return client.send(req,HttpResponse.BodyHandlers.ofInputStream());
  }
  @Test @Order(1) void actualServedObservationOrHeldLockExpiry()throws Exception {
    String scenario=env("MAEZO_NATIVE_OBSERVATION_IT_SCENARIO");
    assertTrue(Set.of("positive","expiry-held-lock").contains(scenario));
    if(scenario.equals("expiry-held-lock")) {heldLockExpiry();return;}
    Instant before=Instant.now();var response=send("/engine-rest/maezo/v1/operations",request);byte[] raw;
    try(var body=response.body()){raw=body.readNBytes(Json.LIMIT+1);}
    Instant after=Instant.now();assertEquals(200,response.statusCode());assertTrue(raw.length<=Json.LIMIT);
    assertTrue(response.headers().firstValue("Cache-Control").orElse("").contains("no-store"));
    var parsed=Json.parse(raw);RuntimeObservationAdmission.validate("Response",parsed);var result=Json.object(parsed.get("result"));
    assertEquals(Jcs.digest(request),result.get("request_sha256"));assertEquals(admission.get("nonce"),result.get("nonce"));
    assertEquals(admission.get("original_deadline"),result.get("original_deadline"));
    byte[] retrieved=Base64.getDecoder().decode(Json.string(Json.object(result.get("definition")),"xml_base64"));
    assertArrayEquals(Files.readAllBytes(path("MAEZO_NATIVE_OBSERVATION_IT_SOURCE_XML")),retrieved);
    assertEquals(Jcs.digest(retrieved),admission.get("xml_sha256"));
    assertEquals("2.1.0",result.get("cibseven_version"));assertTrue(after.isBefore(Instant.parse(Json.string(admission,"original_deadline"))));
    // Real response interval/source check. This IT still requires ROOT's actual CIB/PG lane.
    var clock=Json.object(result.get("clock"));assertEquals("jvm-system-currentTimeMillis",clock.get("source"));
    Instant sampleStart=Instant.parse(Json.string(clock,"sample_started_at")),sampleEnd=Instant.parse(Json.string(clock,"sample_finished_at"));
    assertFalse(sampleStart.isBefore(before.minusSeconds(5)));assertFalse(sampleEnd.isAfter(after.plusSeconds(5)));
    assertFalse(sampleEnd.isBefore(sampleStart));assertEquals(sampleEnd,Instant.parse(Json.string(result,"cutoff_observed_at")));
    assertEquals(sampleEnd,Instant.parse(Json.string(clock,"engine_clock")));
    long elapsed=Long.parseLong(Json.string(clock,"monotonic_elapsed_ns"));assertTrue(elapsed>=0);
    assertTrue(Math.abs(Duration.between(sampleStart,sampleEnd).toNanos()-elapsed)<=1_000_000L);
    assertEquals("unqualified_wall_clock",clock.get("accuracy_status"));assertNull(clock.get("absolute_utc_uncertainty_ms"));
    Instant issued=Instant.parse(Json.string(admission,"issued_at")),deadline=Instant.parse(Json.string(admission,"original_deadline"));
    assertTrue(issued.isBefore(deadline));assertTrue(Duration.between(issued,deadline).compareTo(Duration.ofMinutes(15))<=0);
    var capture=Json.object(result.get("database_witness"));assertEquals(0L,capture.get("stage_sequence"));
    assertEquals("provider-native-runtime-snapshot.v4",result.get("schema"));
    assertEquals("finite-private-emission-stage.v1",Json.object(result.get("emission_recheck")).get("validation_protocol"));
    var rechecks=Json.list(Json.object(result.get("emission_recheck")).get("read_stages"));assertFalse(rechecks.isEmpty());assertTrue(rechecks.size()<=8);
    long index=1;Map<String,Object> previous=capture;
    for(Object value:rechecks) {
      var stage=Json.object(value);assertEquals(index++,stage.get("stage_sequence"));assertEquals("EMISSION_RECHECK",stage.get("stage"));
      assertNotEquals(previous.get("server_stage_execution_uuid"),stage.get("server_stage_execution_uuid"));
      RuntimeDefinitionObservation.sameStable(Json.object(capture.get("entry")),Json.object(stage.get("entry")));
      RuntimeDefinitionObservation.sameSession(Json.object(stage.get("entry")),Json.object(stage.get("committing")));
      assertEquals(true,stage.get("native_commit_confirmed"));
      var old=RuntimeDefinitionObservation.session(Json.object(previous.get("committing")));var fresh=RuntimeDefinitionObservation.session(Json.object(stage.get("entry")));
      if(old.get("transaction_id_if_assigned")!=null && fresh.get("transaction_id_if_assigned")!=null)assertNotEquals(old.get("transaction_id_if_assigned"),fresh.get("transaction_id_if_assigned"));
      previous=stage;
    }
    for(Object stage:java.util.stream.Stream.concat(java.util.stream.Stream.of(capture),rechecks.stream()).toList()) {
      for(String endpoint:List.of("entry","committing")) {
        var sample=Json.object(Json.object(stage).get(endpoint));
        assertEquals(Json.object(admission.get("expected_database")).get("stable_projection"),sample.get("stable_database_projection"));
        var tx=RuntimeDefinitionObservation.session(sample);
        assertEquals(true,tx.get("backend_ssl"));assertEquals("actual-commandcontext-dbsqlsession-connection",tx.get("connection_source"));
        Instant observed=Instant.parse(Json.string(tx,"observed_at"));assertFalse(observed.isBefore(before.minusSeconds(5)));assertFalse(observed.isAfter(after.plusSeconds(5)));
      }
    }
    // Independent connection verifies DB incarnation without pretending it was the server's connection.
    try(var c=independent();var s=c.prepareStatement("SELECT current_database(),d.oid::text,(pg_control_system()).system_identifier::text FROM pg_database d WHERE d.datname=current_database()");var r=s.executeQuery()) {
      assertTrue(r.next());var expected=Json.object(Json.object(admission.get("expected_database")).get("stable_projection"));
      assertEquals(expected.get("database_name"),r.getString(1));assertEquals(expected.get("database_oid"),r.getString(2));assertEquals(expected.get("system_identifier"),r.getString(3));
    }
  }
  static Connection independent()throws Exception {
    char[] password=Files.readString(path("MAEZO_NATIVE_OBSERVATION_IT_DB_PASSWORD_FILE")).strip().toCharArray();
    try{return DriverManager.getConnection(env("MAEZO_NATIVE_OBSERVATION_IT_JDBC_URL"),env("MAEZO_NATIVE_OBSERVATION_IT_DB_USER"),new String(password));}
    finally{Arrays.fill(password,'\0');}
  }
  static void heldLockExpiry()throws Exception {
    // A real owner connection holds the target row. The native server must refuse after its wait.
    try(var c=independent()) {
      c.setAutoCommit(false);
      try(var s=c.prepareStatement("SELECT ID_ FROM cibseven.ACT_RE_PROCDEF WHERE ID_=? FOR UPDATE")) {
        s.setString(1,Json.string(admission,"definition_id"));try(var r=s.executeQuery()){assertTrue(r.next());}
        var future=java.util.concurrent.CompletableFuture.supplyAsync(()->{try{return send("/engine-rest/maezo/v1/operations",request);}catch(Exception e){throw new java.util.concurrent.CompletionException(e);}});
        long remaining=Duration.between(Instant.now(),Instant.parse(Json.string(admission,"original_deadline"))).toMillis();
        assertTrue(remaining>0 && remaining<15000,"ROOT must supply a fresh short original deadline for lock expiry");
        Thread.sleep(remaining+100);c.rollback();var response=future.get(20,java.util.concurrent.TimeUnit.SECONDS);
        try(var body=response.body()){byte[] raw=body.readNBytes(4097);assertEquals(503,response.statusCode());assertFalse(new String(raw,java.nio.charset.StandardCharsets.UTF_8).contains("provider-native-runtime-snapshot"));}
      }finally{c.rollback();}
    }
  }
  @Test @Order(2) void replayNeverReturnsSnapshot()throws Exception {
    var response=send("/engine-rest/maezo/v1/operations",request);assertNotEquals(200,response.statusCode());try(var body=response.body()){assertTrue(body.readNBytes(4097).length<=4096);}
  }
  @Test @Order(3) void unknownAndRawDefinitionRoutesRemainDenied()throws Exception {
    for(String route:List.of("/engine-rest/process-definition/"+Json.string(admission,"definition_id"),"/engine-rest/process-definition/"+Json.string(admission,"definition_id")+"/xml","/engine-rest/maezo/v1/unknown")) {
      var response=client.send(HttpRequest.newBuilder(origin.resolve(route)).timeout(Duration.ofSeconds(5)).GET().build(),HttpResponse.BodyHandlers.ofInputStream());
      assertNotEquals(200,response.statusCode());try(var body=response.body()){assertTrue(body.readNBytes(4097).length<=4096);}
    }
  }
}
