package br.com.maezo.human;

import java.time.Instant;
import java.util.*;
import org.cibseven.bpm.engine.impl.interceptor.*;

/** Owner-designated source publication; native CAS acknowledgement only after commit. */
final class AuthInputPublication implements Command<AuthRuntime.Result> {
  private final AuthRuntime runtime;private final byte[] raw;private final String peer;
  AuthInputPublication(AuthRuntime runtime,byte[] raw,String peer){this.runtime=runtime;this.raw=raw.clone();this.peer=peer;}
  @Override public AuthRuntime.Result execute(CommandContext context) {
    var invocation=runtime.invoke(context,raw,peer,"human-auth-input-publication");
    var c=invocation.envelope.command();var db=invocation.store;
    var source=Jcs.object(c.get("source"));String kind=Jcs.string(c,"kind"),resource=Jcs.ref(c,"resource_ref");
    invocation.envelope.key().requireSource(kind,source,resource);
    var inputs=new AuthInputs(db);inputs.source(source,Instant.now());
    Instant until=PortalReadModels.time(c.get("valid_until"));
    if(until.isAfter(inputs.ceiling())||!Instant.now().isBefore(until))throw Rejected.denied();
    var previous=db.publication(Jcs.ref(c,"publication_id"),invocation.envelope.digest());
    if(previous!=null)return invocation.finish(()->previous,()->inputs.current(Instant.now()),until);
    // Payload projection was schema/hash checked by the signed envelope codec. Source grants and
    // source freeze contract were installed by owner; no publisher can designate itself here.
    var receipt=PortalReadModels.record("schema","human-auth-input-receipt.v1","scope",runtime.scope,
      "publication_id",c.get("publication_id"),"request_digest",invocation.envelope.digest(),"kind",kind,
      "resource_ref",resource,"previous_generation",c.get("expected_generation"),
      "head_generation",Long.toString(AuthStore.next(PortalReadModels.number(c.get("expected_generation")))),
      "state",c.get("state"),"payload_digest",c.get("payload_digest"),"committed_at",PortalReadModels.time(Instant.now()));
    AuthModels.validate("publication-receipt",receipt);
    var priorHead=db.inputHead(kind,resource);
    Map<String,Object> priorPolicy=priorHead==null||priorHead.get("payload_")==null?null:AuthStore.parse(priorHead.get("payload_"));
    db.publish(c,invocation.envelope.digest(),receipt);
    if(kind.equals("document_policy")&&c.get("state").equals("active"))attachPolicy(db,Jcs.object(c.get("payload")),priorPolicy);
    return invocation.finish(()->receipt,()->inputs.current(Instant.now()),until);
  }
  private static void attachPolicy(AuthStore db,Map<String,Object> policy,Map<String,Object> priorPolicy) {
    if(!policy.get("resource_kind").equals("case"))return;
    var row=db.occurrence(Jcs.ref(policy,"request_ref"));if(row==null)throw Rejected.denied();
    var occurrence=AuthModels.validate("occurrence",AuthStore.parse(row.get("record_")));
    if(!Set.of("created","awaiting_publication_worker","bound").contains(occurrence.get("state"))
        ||!occurrence.get("case_ref").equals(policy.get("resource_ref")))throw Rejected.conflict();
    long current=PortalReadModels.number(occurrence.get("request_revision"));
    long proposed=PortalReadModels.number(policy.get("request_revision"));
    if(proposed!=current&&proposed!=AuthStore.next(current))throw Rejected.conflict();
    // Response assessment changes do not invent a new request revision. Requirement/recipient
    // changes do. Preserve the original occurrence identity and require an explicit revision.
    if(occurrence.get("policy_digest")!=null&&proposed==current) {
      if(priorPolicy==null)throw Rejected.conflict();
      for(String key:List.of("policy","policy_revision","recipient_principal_refs","required_codes"))
        if(!Objects.equals(priorPolicy.get(key),policy.get(key)))throw Rejected.conflict();
    }
    var changed=new TreeMap<>(occurrence);changed.put("policy_ref",policy.get("assessment_ref"));
    changed.put("policy_digest",PortalReadModels.hash(policy));changed.put("request_revision",Long.toString(proposed));
    if(changed.get("binding")!=null){var binding=new TreeMap<>(Jcs.object(changed.get("binding")));binding.put("request_revision",Long.toString(proposed));changed.put("binding",binding);}
    AuthModels.validate("occurrence",changed);db.updateOccurrence(changed,((Number)row.get("rev_")).longValue());
  }
}
