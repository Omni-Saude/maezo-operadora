package br.com.maezo.human;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.*;
import org.cibseven.bpm.engine.impl.interceptor.*;

/** AUTH-only strict human start: current publications, permanent guide claim, process and receipt. */
final class HumanAuthStartCommand implements Command<AuthRuntime.Result> {
  private final AuthRuntime runtime;private final byte[] raw;private final String peer;
  HumanAuthStartCommand(AuthRuntime runtime,byte[] raw,String peer){this.runtime=runtime;this.raw=raw.clone();this.peer=peer;}
  @Override public AuthRuntime.Result execute(CommandContext context) {
    var invocation=runtime.invoke(context,raw,peer,"human-auth-start");
    var c=invocation.envelope.command();var db=invocation.store;var actor=Jcs.object(c.get("actor"));
    var inputs=new AuthInputs(db);String command=Jcs.ref(c,"command_id"),intake=Jcs.ref(c,"intake_ref");
    var previous=db.effectReceipt(command,invocation.envelope.digest());
    if(previous!=null) {
      if(!intake.equals(previous.get("intake_ref"))||!actor.get("principal_ref").equals(previous.get("actor_principal_ref")))throw Rejected.denied();
      inputs.readAuthority(actor,"auth.receipt.read","intake",intake,null,Instant.now());
      return invocation.finish(()->previous,()->inputs.current(Instant.now()),inputs.ceiling());
    }
    inputs.pins(c.get("input_pins"),Instant.now());inputs.actor(actor,inputs.value("actor",Jcs.ref(actor,"principal_ref")),Instant.now());
    String guideRef=Jcs.ref(c,"guide_identity_ref");var authority=inputs.only("resource_authority");
    if(!"guide".equals(authority.get("resource_kind")))throw Rejected.denied();
    inputs.authority(actor,authority,"auth.start",guideRef,null,Instant.now());inputs.admission(c,"auth.start");
    var guide=AuthModels.validate("guide",inputs.value("guide",guideRef));
    if(!"absent_at_cutover".equals(guide.get("legacy_state"))||!invocation.qualification.get("cutover_ref").equals(guide.get("cutover_ref")))throw Rejected.denied();
    if(db.guide(guideRef)!=null)throw Rejected.conflict();
    var facts=AuthModels.validate("facts",inputs.value("start_facts",Jcs.ref(c,"start_facts_ref")));
    if(!PortalReadModels.hash(facts).equals(c.get("start_facts_digest"))||!intake.equals(facts.get("intake_ref"))
        ||!guideRef.equals(facts.get("guide_identity_ref"))||!Objects.equals(authority.get("provider_ref"),facts.get("provider_ref"))
        ||!Jcs.object(c.get("admission")).get("admitted_digest").equals(facts.get("request_digest")))throw Rejected.denied();
    inputs.source(guide.get("source"),Instant.now());
    for(Object source:PortalReadModels.list(facts.get("factual_sources")))inputs.source(source,Instant.now());
    if(PortalReadModels.list(facts.get("factual_sources")).isEmpty()||PortalReadModels.list(facts.get("policy_artifacts")).isEmpty())throw Rejected.denied();
    inputs.documents(facts.get("document_refs"),"intake",intake,Instant.now());
    var policy=inputs.policy(Jcs.ref(facts,"documentary_assessment_ref"),"intake",intake,null,0,
      facts.get("document_refs"),facts.get("documentacao_completa"),null,Instant.now());
    if(!policy.get("missing_codes").equals(facts.get("missing_requirement_codes")))throw Rejected.denied();
    var documents=new HashSet<String>();for(Object item:AuthModels.documents(facts.get("document_refs")))documents.add(Jcs.ref(Jcs.object(item),"document_ref"));
    inputs.exactKinds(Set.of("actor","resource_authority","guide","start_facts","document_policy"),documents);
    var definition=Jcs.object(c.get("definition"));invocation.definition(definition);
    Map<String,Object> projected=AuthValues.project(invocation.store.tenant,guideRef,facts);
    // Hash the explicit number-free projection profile, not Java Object.toString or a float cast.
    if(!PortalReadModels.hash(AuthValues.descriptor(invocation.store.tenant,guideRef,facts)).equals(c.get("projected_variables_digest")))throw Rejected.denied();
    inputs.current(Instant.now());invocation.current();
    var instance=runtime.configuration.getRuntimeService().startProcessInstanceById(
      Jcs.ref(definition,"definition_id"),"AUTHI-"+guideRef,projected);
    if(instance==null||!definition.get("definition_id").equals(instance.getProcessDefinitionId())||!db.tenant.equals(instance.getTenantId()))throw Rejected.denied();
    String caseRef=UUID.randomUUID().toString();
    db.claimGuide(guideRef,intake,command,invocation.envelope.digest(),Jcs.ref(actor,"principal_ref"),instance.getId(),caseRef,definition);
    return invocation.finish(()->{
      // Reject a serializer that silently narrowed exact decimal money to Double.
      var money=runtime.configuration.getRuntimeService().getVariableTyped(instance.getId(),"valor_estimado_brl",false);
      if(money==null||!(money.getValue() instanceof BigDecimal actual)||actual.compareTo(AuthDecimalSerializer.cents(facts.get("claimed_amount_cents")).getValue())!=0)throw Rejected.denied();
      var admission=Jcs.object(c.get("admission"));
      var receipt=PortalReadModels.record("schema","human-auth-effect-receipt.v1","scope",runtime.scope,
        "receipt_ref",UUID.randomUUID().toString(),"command_id",command,"command_digest",invocation.envelope.digest(),
        "operation","auth.start","actor_principal_ref",actor.get("principal_ref"),"admission_ref",admission.get("intent_ref"),
        "admitted_digest",admission.get("admitted_digest"),"intake_ref",intake,"case_ref",caseRef,"process_instance_id",instance.getId(),
        "definition",definition,"guide_identity_ref",guideRef,"request_ref",null,"occurrence_generation",null,
        "subscription_id",null,"document_set_digest",null,"outcome","started","committed_at",PortalReadModels.time(Instant.now()));
      AuthModels.validate("receipt",receipt);db.saveEffectReceipt(receipt);return receipt;
    },()->inputs.current(Instant.now()),inputs.ceiling());
  }
}
