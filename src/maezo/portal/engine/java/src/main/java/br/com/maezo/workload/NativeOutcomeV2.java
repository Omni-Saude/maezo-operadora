package br.com.maezo.workload;

import java.nio.charset.StandardCharsets;
import java.security.SecureRandom;
import java.util.*;
import br.com.maezo.human.Jcs;

/** Exact approved v2 unions: replay returns technical history, never a projection. */
final class NativeOutcomeV2 {
  private static final SecureRandom RANDOM=new SecureRandom();
  static final Set<String> OPERATIONS=Set.of("start","correlate","read_active","read_history","fetch_lock",
      "external_complete","external_failure","external_bpmn_error","external_unlock","external_extend_lock");
  record Encoded(int status,byte[] bytes) {
    Encoded {bytes=bytes.clone();}
    @Override public byte[] bytes(){return bytes.clone();}
  }
  /** Publication is a nonthrowing assignment; missing publication NEVER proves rollback. */
  static final class Publication {
    private Encoded prepared;
    private volatile Encoded committed;
    void prepare(Encoded value){if(prepared!=null || value==null)throw Refused.unavailable();prepared=value;}
    void publish(){committed=prepared;}
    Encoded committed(){if(committed==null)throw Refused.unavailable();return committed;}
  }
  static String newRef(){byte[] bytes=new byte[32];RANDOM.nextBytes(bytes);return Base64.getUrlEncoder().withoutPadding().encodeToString(bytes);}
  static String ref(Map<String,Object> value,String key){
    String text=Json.string(value,key);
    try {byte[] raw=Base64.getUrlDecoder().decode(text);if(raw.length!=32 || !Base64.getUrlEncoder().withoutPadding().encodeToString(raw).equals(text))throw Refused.body();}
    catch(IllegalArgumentException e){throw Refused.body();} return text;
  }
  static String digest(Map<String,Object> value,String key){String text=Json.string(value,key);if(!text.matches("[0-9a-f]{64}"))throw Refused.body();return text;}
  static Map<String,Object> reference(Object input){var v=Json.object(input);Json.keys(v,"acquisition_ref","lease_revision");ref(v,"acquisition_ref");if(Json.number(v,"lease_revision")<1)throw Refused.body();return v;}
  static Map<String,Object> command(Object input){
    var c=Json.object(input);Json.keys(c,"engine","database_incarnation","identity","native_user","operation","capability_digest","command_id","request_digest","activation_ref");
    for(String f:List.of("engine","database_incarnation","native_user","activation_ref"))Json.token(c,f);
    Capability.identity(c.get("identity"));if(!OPERATIONS.contains(c.get("operation")))throw Refused.body();
    digest(c,"capability_digest");digest(c,"request_digest");ref(c,"command_id");return c;
  }
  static Map<String,Object> snapshot(Object input){
    var s=Json.object(input);Json.keys(s,"role","task_ref","acquisition_ref","lease_revision","lock_expires_at","state");
    if(!Set.of("resource","source").contains(s.get("role")) || !Set.of("live","closed").contains(s.get("state")))throw Refused.unavailable();
    Json.token(s,"task_ref");ref(s,"acquisition_ref");if(Json.number(s,"lease_revision")<1)throw Refused.unavailable();if(Json.number(s,"lock_expires_at")<0)throw Refused.unavailable();return s;
  }
  static Map<String,Object> receipt(Object input,Map<String,Object> expected){
    var r=Json.object(input);Json.keys(r,"receipt_ref","state","command","resource_ref_digest","source_ref_digest","resource_acquisition","source_acquisition","acquisitions");
    ref(r,"receipt_ref");if(!"committed".equals(r.get("state")) || !command(r.get("command")).equals(expected))throw Refused.unavailable();
    digest(r,"resource_ref_digest");digest(r,"source_ref_digest");
    for(String role:List.of("resource","source"))if(r.get(role+"_acquisition")!=null)reference(r.get(role+"_acquisition"));
    Set<String> roles=new HashSet<>();Map<String,Map<String,Object>> refs=new HashMap<>();
    Map<String,List<Map<String,Object>>> byRole=new HashMap<>();byRole.put("resource",new ArrayList<>());byRole.put("source",new ArrayList<>());
    for(Object item:Json.list(r.get("acquisitions"))){var v=snapshot(item);
      if(!roles.add(v.get("role")+"\u0000"+v.get("task_ref")))throw Refused.unavailable();
      byRole.get(v.get("role")).add(v);var previous=refs.putIfAbsent((String)v.get("acquisition_ref"),v);
      if(previous!=null)for(String field:List.of("task_ref","lease_revision","lock_expires_at","state"))if(!previous.get(field).equals(v.get(field)))throw Refused.unavailable();
    }
    String op=Json.token(expected,"operation");boolean fetch=op.equals("fetch_lock"),renew=op.equals("external_extend_lock");
    boolean terminal=Set.of("external_complete","external_failure","external_bpmn_error","external_unlock").contains(op);
    if((renew||terminal)!=(r.get("resource_acquisition")!=null))throw Refused.unavailable();
    for(String role:List.of("resource","source")){
      var rows=byRole.get(role);Object inputRef=r.get(role+"_acquisition");
      if(inputRef==null){
        if(role.equals("resource")&&fetch){for(var v:rows)if(!Long.valueOf(1).equals(v.get("lease_revision"))||!"live".equals(v.get("state")))throw Refused.unavailable();}
        else if(!rows.isEmpty())throw Refused.unavailable();
      }else{
        if(rows.size()!=1)throw Refused.unavailable();var v=rows.get(0);var consumed=reference(inputRef);
        if(!consumed.get("acquisition_ref").equals(v.get("acquisition_ref")) || !pointerDigest((String)v.get("task_ref")).equals(r.get(role+"_ref_digest")))throw Refused.unavailable();
        boolean sameResource=role.equals("resource") || consumed.equals(r.get("resource_acquisition"));
        long revision=Json.number(consumed,"lease_revision");
        if(sameResource&&renew){if(revision==Long.MAX_VALUE)throw Refused.unavailable();revision++;}
        if(Json.number(v,"lease_revision")!=revision || !(sameResource&&terminal?"closed":"live").equals(v.get("state")))throw Refused.unavailable();
      }
    }
    return r;
  }
  static Encoded operation(Map<String,Object> command,String state,Map<String,Object> receipt,Object value,List<?> acquisitions){
    Map<String,Object> body=new HashMap<>();body.put("protocol","maezo.engine-result.v2");body.put("command",command);body.put("status",state);
    int status=switch(state){case "executed","duplicate"->200;case "conflict"->409;case "unavailable"->503;default->throw Refused.unavailable();};
    if(status==200)receipt(receipt,command);else if(receipt!=null)throw Refused.unavailable();
    body.put("receipt",receipt);
    if(state.equals("executed")){
      if(!Json.list(receipt.get("acquisitions")).equals(acquisitions))throw Refused.unavailable();
      Map<String,Object> result=new HashMap<>();result.put("value",value);result.put("acquisitions",acquisitions);body.put("result",result);
    }else body.put("result",null);
    return new Encoded(status,Json.bytes(body));
  }
  static Encoded outcome(Map<String,Object> query,String queryDigest,String state,Map<String,Object> receipt){
    int status=switch(state){case "committed","not_observed"->200;case "conflict"->409;case "unavailable"->503;default->throw Refused.unavailable();};
    var command=command(query.get("command"));if(state.equals("committed"))receipt(receipt,command);else if(receipt!=null)throw Refused.unavailable();
    Map<String,Object> body=new HashMap<>();body.put("protocol","maezo.engine-outcome.v2");body.put("recovery_capability_digest",query.get("recovery_capability_digest"));body.put("reader_activation_ref",query.get("reader_activation_ref"));body.put("query_digest",queryDigest);body.put("command",command);body.put("status",state);body.put("receipt",receipt);
    return new Encoded(status,Json.bytes(body));
  }
  static Encoded refusal(String code){if(!Set.of("invalid_request","denied","unavailable").contains(code))throw Refused.unavailable();return new Encoded(switch(code){case "invalid_request"->400;case "denied"->403;default->503;},Json.bytes(Map.of("protocol","maezo.engine-refusal.v2","code",code)));}
  static String bodyDigest(byte[] bytes){return Jcs.digest(bytes);}
  static String pointerDigest(String value){return bodyDigest(value.getBytes(StandardCharsets.UTF_8));}
}
