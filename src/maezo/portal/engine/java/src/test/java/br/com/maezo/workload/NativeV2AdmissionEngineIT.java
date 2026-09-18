package br.com.maezo.workload;

import static org.junit.jupiter.api.Assertions.*;
import java.net.URI;
import java.net.http.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.security.KeyStore;
import java.sql.*;
import java.util.*;
import javax.net.ssl.*;
import org.junit.jupiter.api.*;

/** ROOT-only installed PostgreSQL/CIB/Tomcat. Missing real credentials fail, never skip or mock. */
@Tag("integration")
class NativeV2AdmissionEngineIT {
  static final class Fixture {
    final Path root;final Map<String,Object> metadata;final HttpClient client;
    Fixture()throws Exception{
      String supplied=System.getenv("MAEZO_NATIVE_V2_IT_FIXTURE");assertNotNull(supplied,"owner-prepared real native v2 fixture required");root=Path.of(supplied);assertTrue(root.isAbsolute());assertEquals(root,root.toRealPath());
      metadata=Json.parse(Files.readAllBytes(root.resolve("native-v2-fixture.json")));assertEquals(Boolean.TRUE,metadata.get("fixture_only"));assertEquals("maezo.native-v2-real-fixture.v1",metadata.get("protocol"));
      char[] password=Files.readString(root.resolve("keystore-password")).strip().toCharArray();KeyStore keys=KeyStore.getInstance("PKCS12"),trust=KeyStore.getInstance("PKCS12");
      try(var in=Files.newInputStream(root.resolve("client.p12"))){keys.load(in,password);}try(var in=Files.newInputStream(root.resolve("trust.p12"))){trust.load(in,password);}
      var km=KeyManagerFactory.getInstance(KeyManagerFactory.getDefaultAlgorithm());km.init(keys,password);Arrays.fill(password,'\0');var tm=TrustManagerFactory.getInstance(TrustManagerFactory.getDefaultAlgorithm());tm.init(trust);
      SSLContext ssl=SSLContext.getInstance("TLSv1.3");ssl.init(km.getKeyManagers(),tm.getTrustManagers(),null);
      client=HttpClient.newBuilder().sslContext(ssl).connectTimeout(java.time.Duration.ofSeconds(5)).followRedirects(HttpClient.Redirect.NEVER).build();
      URI base=URI.create(Json.token(metadata,"base_url"));assertEquals("https",base.getScheme());assertNull(base.getUserInfo());assertTrue(base.getPath().isEmpty());
    }
    Map<String,Object> template(String name){var body=new HashMap<>(Json.object(Json.object(metadata.get("requests")).get(name)));body.put("command_id",NativeOutcomeV2.newRef());return body;}
    HttpResponse<byte[]> send(String path,byte[] body)throws Exception{var b=HttpRequest.newBuilder(URI.create(Json.token(metadata,"base_url")+path)).timeout(java.time.Duration.ofSeconds(10));if(body==null)b.GET();else b.header("Content-Type","application/json").POST(HttpRequest.BodyPublishers.ofByteArray(body));return client.send(b.build(),HttpResponse.BodyHandlers.ofByteArray());}
    Map<String,Object> operation(Map<String,Object> body)throws Exception{var r=send("/maezo-workload/v2/operations",Json.bytes(body));assertEquals(200,r.statusCode(),"real native operation must commit");var value=Json.parse(r.body());assertEquals("executed",value.get("status"));return value;}
    Map<String,Object> fetched(String template)throws Exception{var out=operation(template(template));var result=Json.object(out.get("result"));var snapshots=Json.list(result.get("acquisitions"));assertEquals(1,snapshots.size(),"owner prepared one eligible task for this case");return Json.object(snapshots.get(0));}
    Map<String,Object> consume(String template,Map<String,Object> snapshot){var body=template(template);body.put("resource_ref",snapshot.get("task_ref"));body.put("resource_acquisition",Map.of("acquisition_ref",snapshot.get("acquisition_ref"),"lease_revision",snapshot.get("lease_revision")));return body;}
    Connection runtime()throws Exception{return DriverManager.getConnection(Json.token(metadata,"jdbc_url"),Json.token(metadata,"runtime_login"),Files.readString(root.resolve("runtime-db-password")).strip());}
    String technicalState()throws Exception{
      try(var c=DriverManager.getConnection(Json.token(metadata,"jdbc_url"),Json.token(metadata,"observer_login"),Files.readString(root.resolve("observer-db-password")).strip());var q=c.prepareStatement("SELECT encode(sha256(convert_to(coalesce(jsonb_agg(to_jsonb(a) ORDER BY task_id COLLATE \"C\",acquisition_ref COLLATE \"C\")::text,'[]'),'UTF8')),'hex') FROM maezo_native_v2.acquisition a WHERE scope=?::jsonb")){
        q.setString(1,new String(Json.bytes(metadata.get("scope")),StandardCharsets.UTF_8));try(var r=q.executeQuery()){assertTrue(r.next());return r.getString(1);}
      }
    }
  }
  @Test void readinessIsActualQualifiedV2()throws Exception{var f=new Fixture();var r=f.send("/maezo-workload/v2/readiness",null);assertEquals(200,r.statusCode());var body=Json.parse(r.body());assertEquals("maezo.engine-readiness.v2",body.get("protocol"));assertEquals(Boolean.TRUE,body.get("ready"));assertFalse(Json.list(body.get("capabilities")).isEmpty());assertEquals(Json.object(f.metadata.get("scope")).get("database_incarnation"),body.get("database_incarnation"));}
  @Test void runtimeHasNoNativeDmlOrDHelperRoute()throws Exception{var f=new Fixture();try(var c=f.runtime();var q=c.createStatement();var r=q.executeQuery("SELECT session_user=current_user,has_table_privilege(current_user,'maezo_native_v2.admission','INSERT,UPDATE,DELETE'),has_table_privilege(current_user,'maezo_native_v2.acquisition','INSERT,UPDATE,DELETE'),has_table_privilege(current_user,'maezo_native_v2.operation_receipt','INSERT,UPDATE,DELETE'),has_function_privilege(current_user,'maezo_native_v2.register_admission_v2(jsonb)','EXECUTE'),has_function_privilege(current_user,'maezo_d7_control.lock_runtime_v2_scope(jsonb)','EXECUTE')")){assertTrue(r.next());assertTrue(r.getBoolean(1));for(int i=2;i<=6;i++)assertFalse(r.getBoolean(i));}}
  @Test void malformedGuardCannotCreateAuthority()throws Exception{var f=new Fixture();try(var c=f.runtime()){c.setAutoCommit(false);try(var q=c.prepareStatement("SELECT maezo_native_v2.guard_runtime_v2('{}'::jsonb)")){SQLException failure=assertThrows(SQLException.class,q::execute);assertEquals("P7N01",failure.getSQLState());}finally{c.rollback();}}}
  @Test void managedLegacyAndAliasRoutesAreClosed()throws Exception{var f=new Fixture();for(String path:List.of("/engine-rest/maezo/v1/operations","/engine-rest/external-task/fetchAndLock","/maezo-workload/v1/operations","/maezo-workload/v2/operations/")){var r=f.send(path,Json.bytes(f.template("legacy_denial")));assertTrue(r.statusCode()==403||r.statusCode()==404,"legacy route must refuse");}}
}
