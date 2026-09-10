package br.com.maezo.human;

import java.time.Instant;
import java.util.*;

/** One engine-local currentness observation under the shared tenant lock. No remote source calls. */
final class AuthInputs {
  private final AuthStore store;
  private final List<Instant> ceilings=new ArrayList<>();
  private final Map<String,Map<String,Object>> values=new HashMap<>();
  AuthInputs(AuthStore store){this.store=store;}
  void current(Instant now){for(Instant until:ceilings)if(!now.isBefore(until))throw Rejected.denied();}
  Instant ceiling(){return ceilings.stream().min(Instant::compareTo).orElseThrow(Rejected::denied);}
  void source(Object input,Instant now) {
    var source=AuthModels.validate("source",input);
    Instant observed=PortalReadModels.time(source.get("observed_at")),until=PortalReadModels.time(source.get("valid_until"));
    if(observed.isAfter(now)||!observed.isBefore(until))throw Rejected.denied();ceilings.add(until);current(now);
  }
  private Map<String,Object> active(Map<String,Object> row,Instant now) {
    if(row==null||!"active".equals(row.get("state_")))throw Rejected.denied();
    ceilings.add(((java.sql.Timestamp)row.get("valid_until_")).toInstant());
    var source=AuthStore.parse(row.get("source_"));source(source,now);
    var publisher=AuthTrust.publisher(store,row,Instant.now());
    publisher.requireSource(Jcs.string(row,"kind_"),source,Jcs.ref(row,"resource_"));
    ceilings.add(publisher.notAfter());
    var payload=AuthStore.parse(row.get("payload_"));
    if(!PortalReadModels.hash(payload).equals(row.get("payload_digest_")))throw Rejected.denied();current(Instant.now());return payload;
  }
  void pins(Object pins,Instant now) {
    AuthModels.pins(pins);
    for(Object item:PortalReadModels.list(pins)) {
      var pin=Jcs.object(item);String kind=Jcs.string(pin,"kind"),resource=Jcs.ref(pin,"resource_ref");
      var row=store.inputHead(kind,resource);var payload=active(row,now);
      if(((Number)row.get("generation_")).longValue()!=PortalReadModels.number(pin.get("head_generation"))
          ||!row.get("payload_digest_").equals(pin.get("payload_digest"))
          ||!AuthStore.parse(row.get("source_")).equals(pin.get("source")))throw Rejected.conflict();
      values.put(kind+"\n"+resource,payload);
    }
  }
  Map<String,Object> value(String kind,String resource) {
    var value=values.get(kind+"\n"+resource);if(value==null)throw Rejected.denied();return value;
  }
  Map<String,Object> only(String kind) {
    var matching=values.entrySet().stream().filter(e->e.getKey().startsWith(kind+"\n")).toList();
    if(matching.size()!=1)throw Rejected.denied();return matching.get(0).getValue();
  }
  void exactKinds(Set<String> singletons,Set<String> documents) {
    exactKinds(values.keySet(),singletons,documents);
  }
  static void exactKinds(Set<String> actual,Set<String> singletons,Set<String> documents) {
    var expected=new HashSet<String>();
    for(String kind:singletons){
      var matching=actual.stream().filter(key->key.startsWith(kind+"\n")).toList();
      if(matching.size()!=1)throw Rejected.denied();expected.add(matching.get(0));
    }
    for(String document:documents)expected.add("document_custody\n"+document);
    if(!actual.equals(expected))throw Rejected.denied();
  }
  void actor(Map<String,Object> actor,Map<String,Object> membership,Instant now) {
    AuthModels.validate("actor",actor);AuthModels.validate("membership",membership);
    if(!"active".equals(membership.get("state")))throw Rejected.denied();
    for(String key:List.of("principal_ref","issuer","subject","membership_revision","audience"))if(!actor.get(key).equals(membership.get(key)))throw Rejected.denied();
    ceilings.add(PortalReadModels.time(membership.get("reviewed_until")));current(now);
  }
  void authority(Map<String,Object> actor,Map<String,Object> authority,String operation,String resource,String request,Instant now) {
    AuthModels.validate("authority",authority);
    if(!actor.equals(authority.get("actor"))||!operation.equals(authority.get("action"))
        ||!resource.equals(authority.get("resource_ref"))||!Objects.equals(request,authority.get("request_ref"))
        ||!"active".equals(authority.get("state"))||"revoked".equals(authority.get("consent_state")))throw Rejected.denied();
    if("consent".equals(authority.get("legal_basis"))&&!"valid".equals(authority.get("consent_state")))throw Rejected.denied();
    if(now.isBefore(PortalReadModels.time(authority.get("valid_from"))))throw Rejected.denied();
    ceilings.add(PortalReadModels.time(authority.get("valid_until")));source(authority.get("source"),now);current(now);
  }
  void admission(Map<String,Object> command,String operation,Instant envelopeUntil) {
    var admitted=Jcs.object(command.get("admission"));
    var row=store.inputHead("audit_intent",Jcs.ref(admitted,"intent_ref"));
    // Separate approved admission lookup: never insert this source into mutation input_pins.
    var intent=active(row,Instant.now());
    admissionIdentity(command,operation,intent,AuthStore.parse(row.get("source_")),Instant.now());
    var binding=Jcs.object(intent.get("session_binding"));
    Instant originalUntil=admissionCeiling(intent,envelopeUntil,Instant.now());
    ceilings.add(PortalReadModels.time(binding.get("session_expires_at")));
    ceilings.add(originalUntil);
    current(Instant.now());
  }
  static Instant admissionCeiling(Map<String,Object> intent,Instant envelopeUntil,Instant now) {
    AuthModels.validate("intent",intent);
    var binding=Jcs.object(intent.get("session_binding"));
    Instant authenticated=PortalReadModels.time(binding.get("authenticated_at"));
    Instant admitted=PortalReadModels.time(intent.get("admitted_at"));
    Instant sessionUntil=PortalReadModels.time(binding.get("session_expires_at"));
    Instant until=PortalReadModels.time(binding.get("authorization_until"));
    if(authenticated.isAfter(admitted)||!admitted.isBefore(until)||until.isAfter(sessionUntil)
        ||envelopeUntil.isAfter(until)||!now.isBefore(until))throw Rejected.denied();
    return until;
  }
  static void admissionIdentity(Map<String,Object> command,String operation,Map<String,Object> intent,
      Map<String,Object> publishedSource,Instant now) {
    var admitted=Jcs.object(command.get("admission"));AuthModels.validate("intent",intent);
    if(!admitted.get("intent_ref").equals(intent.get("intent_ref"))
        ||!"committed".equals(intent.get("state"))||!command.get("actor").equals(intent.get("actor"))
        ||!operation.equals(intent.get("operation"))||!command.get("command_id").equals(admitted.get("admitted_command_id"))
        ||!admitted.get("admitted_command_id").equals(intent.get("command_id"))
        ||!admitted.get("admitted_digest").equals(intent.get("admitted_digest"))
        ||!publishedSource.equals(admitted.get("source")))throw Rejected.denied();
    if(PortalReadModels.time(intent.get("admitted_at")).isAfter(now))throw Rejected.denied();
    if(operation.equals("auth.start")&&!command.get("intake_ref").equals(intent.get("intake_or_response_ref")))throw Rejected.denied();
  }
  void documents(Object refs,String resourceKind,String resource,Instant now) {
    for(Object item:AuthModels.documents(refs)) {
      var document=Jcs.object(item);var custody=value("document_custody",Jcs.ref(document,"document_ref"));
      AuthModels.validate("custody",custody);
      if(!document.equals(custody.get("document"))||!resourceKind.equals(custody.get("resource_kind"))
          ||!resource.equals(custody.get("resource_ref"))||!"available".equals(custody.get("custody_state"))
          ||!"clean".equals(custody.get("screening_result")))throw Rejected.denied();
      ceilings.add(PortalReadModels.time(custody.get("valid_until")));current(now);
    }
  }
  Map<String,Object> policy(String ref,String resourceKind,String resource,String request,long requestRevision,
      Object refs,Object complete,Object admittedDigest,Instant now) {
    var policy=value("document_policy",ref);AuthModels.validate("policy",policy);
    if(!resourceKind.equals(policy.get("resource_kind"))||!resource.equals(policy.get("resource_ref"))
        ||!Objects.equals(request,policy.get("request_ref"))||requestRevision!=PortalReadModels.number(policy.get("request_revision"))
        ||!refs.equals(policy.get("effective_document_refs"))||!complete.equals(policy.get("complete"))
        ||!Objects.equals(admittedDigest,policy.get("submitted_response_digest")))throw Rejected.denied();
    if(!PortalReadModels.list(policy.get("recipient_principal_refs")).contains(only("actor").get("principal_ref")))throw Rejected.denied();
    source(policy.get("source"),now);ceilings.add(PortalReadModels.time(policy.get("valid_until")));current(now);return policy;
  }
  /** Queries use independently current read grants, never the old mutation command's pins. */
  void readAuthority(Map<String,Object> actor,String operation,String resourceKind,String resource,String request,Instant now) {
    var membership=active(store.inputHead("actor",Jcs.ref(actor,"principal_ref")),now);actor(actor,membership,now);
    var row=store.optional("SELECT * FROM MZO_AUTH_INPUT_HEAD WHERE TENANT_=? AND KIND_='resource_authority' AND STATE_='active' AND PAYLOAD_::jsonb->'actor'->>'principal_ref'=? AND PAYLOAD_::jsonb->>'action'=? AND PAYLOAD_::jsonb->>'resource_kind'=? AND PAYLOAD_::jsonb->>'resource_ref'=? AND (PAYLOAD_::jsonb->>'request_ref') IS NOT DISTINCT FROM ? FOR UPDATE",store.tenant,Jcs.ref(actor,"principal_ref"),operation,resourceKind,resource,request);
    authority(actor,active(row,now),operation,resource,request,now);
  }
}
