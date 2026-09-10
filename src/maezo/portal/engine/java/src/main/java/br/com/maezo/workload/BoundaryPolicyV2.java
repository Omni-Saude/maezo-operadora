package br.com.maezo.workload;

import java.nio.file.*;
import java.util.*;
import jakarta.servlet.DispatcherType;
import jakarta.servlet.http.HttpServletRequest;

/** Mounted exact v2 trust/config binding. SQL/D admission, never this file, grants execution. */
public final class BoundaryPolicyV2 {
  final BoundaryPolicy transport;
  final Map<String,Object> database, admission;
  final String digest,schemaDigest;
  final Map<String,List<CapabilityV2>> capabilities;
  final Map<String,List<Map<String,Object>>> recovery;
  static BoundaryPolicyV2 environment() {
    String file=System.getenv("MAEZO_ENGINE_BOUNDARY_V2_FILE"),hash=System.getenv("MAEZO_ENGINE_BOUNDARY_V2_SHA256");
    if(file==null || hash==null)throw Refused.unavailable();return load(Path.of(file),hash);
  }
  static boolean configured(){return System.getenv("MAEZO_ENGINE_BOUNDARY_V2_FILE")!=null || System.getenv("MAEZO_ENGINE_BOUNDARY_V2_SHA256")!=null;}
  static BoundaryPolicyV2 load(Path path,String digest){return new BoundaryPolicyV2(path,digest,Json.parse(BoundaryPolicy.read(path,digest)));}
  BoundaryPolicyV2(Path path,String hash,Map<String,Object> document) {
    document=Json.parse(Json.bytes(document));
    Json.keys(document,"protocol","tenant","environment","engine_name","not_before","expires_at","roots","peers","files","listener_port","max_tasks","max_lock_millis","max_poll_millis","database","admission","schema_digest");
    if(!"maezo.engine-boundary.v2".equals(document.get("protocol")))throw Refused.unavailable();
    digest=hash;schemaDigest=NativeOutcomeV2.digest(document,"schema_digest");
    database=Json.object(document.get("database"));
    Json.keys(database,"database_oid","act_schema","act_schema_oid","migration_digest","database_binding_sha256");
    NativeEnlistedWritesV2.identifier(Json.token(database,"act_schema"));
    if(Json.number(database,"database_oid")<1 || Json.number(database,"act_schema_oid")<1 || Json.number(database,"database_oid")>4294967295L || Json.number(database,"act_schema_oid")>4294967295L)throw Refused.unavailable();
    NativeOutcomeV2.digest(database,"migration_digest");NativeOutcomeV2.digest(database,"database_binding_sha256");
    admission=Json.object(document.get("admission"));
    Json.keys(admission,"activation_ref","database_incarnation","runtime_generation","epoch","decision_digest","purpose","admission_phase","account","region");
    for(String key:List.of("activation_ref","database_incarnation","admission_phase","account","region"))Json.token(admission,key);
    if(Json.number(admission,"epoch")<1 || Json.number(admission,"runtime_generation")<1
        || Json.number(admission,"epoch")>9007199254740991L || Json.number(admission,"runtime_generation")>9007199254740991L
        || !Set.of("runtime","candidate").contains(admission.get("purpose"))
        || !Set.of("ACTIVE","CANDIDATE_QUALIFICATION","RESTORE_QUALIFICATION").contains(admission.get("admission_phase")))throw Refused.unavailable();
    NativeOutcomeV2.digest(admission,"decision_digest");
    if(!Json.string(admission,"account").matches("[0-9]{12}") || !Json.string(admission,"region").matches("[a-z]{2}(?:-[a-z]+)+-[0-9]+")
        || !Json.string(admission,"database_incarnation").matches("[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"))throw Refused.unavailable();
    Map<String,Object> tls=new HashMap<>(document);tls.remove("database");tls.remove("admission");tls.remove("schema_digest");tls.put("protocol","maezo.engine-boundary.v1");
    List<Object> peers=new ArrayList<>();Map<String,List<CapabilityV2>> caps=new HashMap<>();Map<String,List<Map<String,Object>>> reads=new HashMap<>();
    for(Object item:Json.list(document.get("peers"))) {
      var peer=Json.object(item);Json.keys(peer,"certificate_sha256","spki_sha256","issuer_dn","subject_dn","uri_san","purpose","engine_user","identity","not_before","expires_at","capabilities","recovery_capabilities");
      if(!"nonhuman".equals(peer.get("purpose")))throw Refused.unavailable();
      String cert=NativeOutcomeV2.digest(peer,"certificate_sha256");
      List<CapabilityV2> bound=new ArrayList<>();Set<String> unique=new HashSet<>();
      for(Object entry:Json.list(peer.get("capabilities"))) {
        var c=new CapabilityV2(Json.object(entry));if(!c.identity.equals(peer.get("identity")) || !unique.add(c.digest))throw Refused.unavailable();bound.add(c);
      }
      List<Map<String,Object>> recovered=new ArrayList<>();
      for(Object entry:Json.list(peer.get("recovery_capabilities"))) {
        var r=Json.object(entry);Json.keys(r,"digest","document");var d=Json.object(r.get("document"));
        Json.keys(d,"protocol","kind","identity","reader_native_user","reader_purpose","selector");
        if(!"maezo.engine-capability.v2".equals(d.get("protocol")) || !"outcome".equals(d.get("kind"))
            || !d.get("identity").equals(peer.get("identity")) || !d.get("reader_native_user").equals(peer.get("engine_user"))
            || !d.get("reader_purpose").equals(admission.get("purpose")) || !Json.digest(d).equals(NativeOutcomeV2.digest(r,"digest"))
            || !unique.add((String)r.get("digest")))throw Refused.unavailable();
        var s=Json.object(d.get("selector"));Json.keys(s,"engine","database_incarnation","identity","native_user","operation","capability_digest");
        for(String k:List.of("engine","database_incarnation","native_user"))Json.token(s,k);
        Capability.identity(s.get("identity"));NativeOutcomeV2.digest(s,"capability_digest");
        if(!NativeOutcomeV2.OPERATIONS.contains(s.get("operation")))throw Refused.unavailable();recovered.add(r);
      }
      if(caps.put(cert,List.copyOf(bound))!=null)throw Refused.unavailable();reads.put(cert,List.copyOf(recovered));
      Map<String,Object> reduced=new HashMap<>(peer);reduced.remove("recovery_capabilities");reduced.put("capabilities",List.of());peers.add(reduced);
    }
    capabilities=Map.copyOf(caps);recovery=Map.copyOf(reads);tls.put("peers",peers);
    transport=BoundaryPolicy.transportV2(path,hash,tls);
    try(var in=CapabilityV2.class.getResourceAsStream("/engine-schemas-v2.json")) {
      if(in==null || !NativeOutcomeV2.bodyDigest(in.readAllBytes()).equals(schemaDigest))throw Refused.unavailable();
    }catch(java.io.IOException e){throw Refused.unavailable();}
  }
  public BoundaryPolicy.Peer authenticate(HttpServletRequest request){return transport.authenticate(request);}
  void current(BoundaryPolicy.Peer peer){transport.current(peer);}
  boolean manages(Capability old){
    for(var list:capabilities.values())for(var cap:list){
      if(!old.identity.get("tenant").equals(cap.identity.get("tenant")))continue;
      for(var a:List.of(old.target,old.sourceTarget))for(var b:List.of(cap.target,cap.sourceTarget))
        if(!a.isEmpty()&&!b.isEmpty()&&a.get("definition_id").equals(b.get("definition_id"))
            && (Json.string(a,"topic").isEmpty()||Json.string(b,"topic").isEmpty()||a.get("topic").equals(b.get("topic"))))return true;
    }return false;
  }
  CapabilityV2 capability(BoundaryPolicy.Peer peer,String digest){return capabilities.getOrDefault(peer.certificate(),List.of()).stream().filter(c->c.digest.equals(digest)).findFirst().orElseThrow(Refused::denied);}
  Map<String,Object> recovery(BoundaryPolicy.Peer peer,String digest,Map<String,Object> command){
    var row=recovery.getOrDefault(peer.certificate(),List.of()).stream().filter(r->digest.equals(r.get("digest"))).findFirst().orElseThrow(Refused::denied);
    var selector=Json.object(Json.object(row.get("document")).get("selector"));
    for(var field:selector.entrySet())if(!field.getValue().equals(command.get(field.getKey())))throw Refused.denied();
    return row;
  }
  static boolean route(HttpServletRequest r){
    String uri=r.getRequestURI();if(uri==null || !uri.startsWith("/maezo-workload/"))return false;
    if(r.getDispatcherType()!=DispatcherType.REQUEST || r.isAsyncStarted() || r.getQueryString()!=null
        || !uri.equals(r.getContextPath()+r.getServletPath()+(r.getPathInfo()==null?"":r.getPathInfo())))throw Refused.denied();
    if("/maezo-workload/v2/readiness".equals(uri) && "GET".equals(r.getMethod())){
      if(r.getContentLengthLong()>0 || r.getHeader("Transfer-Encoding")!=null)throw Refused.body();return true;
    }
    if(Set.of("/maezo-workload/v2/operations","/maezo-workload/v2/outcomes").contains(uri) && "POST".equals(r.getMethod())){
      if(!"application/json".equals(r.getContentType()) || r.getContentLengthLong()>Json.LIMIT)throw Refused.body();return true;
    }
    throw Refused.denied();
  }
}
