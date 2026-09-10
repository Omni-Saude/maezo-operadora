package br.com.maezo.workload;

import java.nio.file.*;
import java.security.cert.*;
import java.time.Instant;
import java.util.*;
import jakarta.servlet.DispatcherType;
import jakarta.servlet.http.HttpServletRequest;
import br.com.maezo.human.Jcs;

final class Fixtures {
  static Map<String,Object> identity() {
    return Map.of("tenant","tenant-test","environment","test","workload","helena","workload_version","1",
        "issuer","CN=D7-Test-CA","subject","spiffe://maezo.test/tenant-test/helena","origin","verified_mtls");
  }
  static Map<String,Object> binding(String schemaId) {
    var schema=Capability.schemas().get(schemaId);
    var identity=new HashMap<>(identity());identity.put("workload",schema.get("workload"));
    Map<String,Object> doc=new HashMap<>();
    doc.put("protocol","maezo.engine-capability.v1");doc.put("schema",schema);doc.put("identity",identity);
    doc.put("target",Map.of("process_key",schema.get("process_key"),"process_version",1L,"definition_id","fixture-definition","topic",schema.get("topic"),"message",schema.get("message")));
    doc.put("worker_id",schema.get("topic").equals("")?"":"fixture-worker");doc.put("source_target",null);
    return new HashMap<>(Map.of("document",doc,"digest",Json.digest(doc),"source_kind","","source_worker_id","","attestations",List.of()));
  }
  static Map<String,Object> request(Capability cap) {
    Map<String,Object> variables=new HashMap<>();
    for(Object item:Json.list(cap.schema.get("fields"))) {
      var f=Json.object(item);if(!Json.bool(f,"required"))continue;
      String name=Json.token(f,"name");Object value=switch(Json.token(f,"kind")) {
        case "String" -> "fixture";case "Boolean" -> false;case "Integer" -> 1L;case "Double" -> 1.0;case "Json" -> List.of();default->throw new AssertionError();
      };
      if("tenant_id".equals(name))value=cap.identity.get("tenant");
      if("source_agent_id".equals(name))value=cap.identity.get("workload");
      if("source_agent_version".equals(name))value=cap.identity.get("workload_version");
      variables.put(name,value);
    }
    Map<String,Object> request=new HashMap<>();
    request.put("protocol","maezo.engine-operation.v1");request.put("capability_digest",cap.digest);
    for(String field:List.of("operation","process_key","topic","message","all_matching"))request.put(field,cap.schema.get(field));
    request.put("resource_ref","fixture-resource");request.put("variables",variables);request.put("correlation",Map.of());
    request.put("error_code",Json.list(cap.schema.get("error_codes")).isEmpty()?"":Json.list(cap.schema.get("error_codes")).get(0));
    request.put("worker_id",cap.worker);request.put("source_ref","");
    request.put("parameters",switch(Json.token(cap.schema,"operation")) {
      case "fetch_lock" -> Map.of("maxTasks",1L,"lockDuration",10000L,"asyncResponseTimeout",10L,"variables",List.of());
      case "external_failure" -> Map.of("retries",1L,"retryTimeout",10L,"errorCategory","worker_failure");
      case "external_extend_lock" -> Map.of("newDuration",100L);default -> Map.of();
    });return request;
  }
  static X509Certificate certificate(String name)throws Exception {
    try(var in=Fixtures.class.getResourceAsStream("/d7/"+name+".pem")) {
      return (X509Certificate)CertificateFactory.getInstance("X.509").generateCertificate(in);
    }
  }
  static Map<String,Object> manifest(Path directory)throws Exception {
    directory=directory.toRealPath();
    Path ca=directory.resolve("ca.pem");Files.write(ca,Fixtures.class.getResourceAsStream("/d7/ca.pem").readAllBytes());
    var leaf=certificate("client");long now=Instant.now().getEpochSecond();
    Map<String,Object> peer=new HashMap<>();
    peer.put("certificate_sha256",BoundaryPolicy.hashCertificate(leaf));peer.put("spki_sha256",Jcs.digest(leaf.getPublicKey().getEncoded()));
    peer.put("issuer_dn",leaf.getIssuerX500Principal().getName());peer.put("subject_dn",leaf.getSubjectX500Principal().getName());
    peer.put("uri_san","spiffe://maezo.test/tenant-test/helena");peer.put("purpose","nonhuman");peer.put("engine_user","fixture-helena");
    peer.put("identity",identity());peer.put("not_before",now-60);peer.put("expires_at",now+600);
    peer.put("capabilities",List.of(binding("helena.escalation.start.v1")));
    Map<String,Object> m=new HashMap<>();
    m.put("protocol","maezo.engine-boundary.v1");m.put("tenant","tenant-test");m.put("environment","test");m.put("engine_name","default");
    m.put("not_before",now-60);m.put("expires_at",now+600);m.put("roots",List.of(Map.of("path",ca.toString(),"sha256",Jcs.digest(Files.readAllBytes(ca)))));
    m.put("peers",List.of(peer));m.put("files",List.of());m.put("listener_port",8443L);
    m.put("max_tasks",10L);m.put("max_lock_millis",60000L);m.put("max_poll_millis",10000L);return m;
  }
  static BoundaryPolicy load(Path directory,Map<String,Object> manifest)throws Exception {
    directory=directory.toRealPath();
    byte[] raw=Json.bytes(manifest);Path file=directory.resolve("policy.json");Files.write(file,raw);return BoundaryPolicy.load(file,Jcs.digest(raw));
  }
  static HttpServletRequest requestProxy(Map<String,Object> values) {
    return (HttpServletRequest)java.lang.reflect.Proxy.newProxyInstance(Fixtures.class.getClassLoader(),new Class[]{HttpServletRequest.class},(proxy,method,args)-> {
      String key=method.getName();if(key.equals("getAttribute"))return values.get("attribute:"+args[0]);
      if(key.equals("getHeader"))return values.get("header:"+args[0]);
      if(values.containsKey(key))return values.get(key);
      if(method.getReturnType()==boolean.class)return false;if(method.getReturnType()==int.class)return 0;if(method.getReturnType()==long.class)return 0L;return null;
    });
  }
  static Map<String,Object> requestValues()throws Exception {
    Map<String,Object> m=new HashMap<>();m.put("isSecure",true);m.put("getScheme","https");m.put("getLocalPort",8443);
    m.put("attribute:jakarta.servlet.request.X509Certificate",new X509Certificate[]{certificate("client"),certificate("ca")});
    m.put("getDispatcherType",DispatcherType.REQUEST);m.put("getMethod","POST");m.put("getContentType","application/json");
    m.put("getRequestURI","/engine-rest/maezo/v1/operations");m.put("getContextPath","/engine-rest");m.put("getServletPath","/maezo");m.put("getPathInfo","/v1/operations");return m;
  }
}
