package br.com.maezo.human;

import java.time.Instant;
import java.util.*;
import org.cibseven.bpm.engine.impl.interceptor.*;
import org.cibseven.bpm.engine.variable.Variables;

/** One exact occurrence, native subscription, effective document set and atomic effect receipt. */
final class HumanAuthDocumentCommand implements Command<AuthRuntime.Result> {
  private final AuthRuntime runtime;private final byte[] raw;private final String peer;
  HumanAuthDocumentCommand(AuthRuntime runtime,byte[] raw,String peer){this.runtime=runtime;this.raw=raw.clone();this.peer=peer;}
  @Override public AuthRuntime.Result execute(CommandContext context) {
    var invocation=runtime.invoke(context,raw,peer,"human-auth-documents");
    var c=invocation.envelope.command();var db=invocation.store;var actor=Jcs.object(c.get("actor"));
    var inputs=new AuthInputs(db);String command=Jcs.ref(c,"command_id"),caseRef=Jcs.ref(c,"case_ref");
    var previous=db.effectReceipt(command,invocation.envelope.digest());
    if(previous!=null) {
      if(!caseRef.equals(previous.get("case_ref"))||!actor.get("principal_ref").equals(previous.get("actor_principal_ref")))throw Rejected.denied();
      inputs.readAuthority(actor,"auth.receipt.read","case",caseRef,null,Instant.now());
      return invocation.finish(()->previous,()->inputs.current(Instant.now()),inputs.ceiling());
    }
    var binding=Jcs.object(c.get("occurrence"));String request=Jcs.ref(binding,"request_ref"),instance=Jcs.ref(binding,"process_instance_id");
    var link=db.caseLink(caseRef);if(link==null||!instance.equals(link.get("instance_")))throw Rejected.denied();
    var head=db.instanceHead(instance);var row=db.occurrence(request);
    if(row==null||!request.equals(head.get("current_request_")))throw Rejected.conflict();
    var occurrence=AuthModels.validate("occurrence",AuthStore.parse(row.get("record_")));
    if(!caseRef.equals(occurrence.get("case_ref"))||!"bound".equals(occurrence.get("state"))||!binding.equals(occurrence.get("binding")))throw Rejected.conflict();
    inputs.pins(c.get("input_pins"),Instant.now());inputs.actor(actor,inputs.value("actor",Jcs.ref(actor,"principal_ref")),Instant.now());
    var authority=inputs.only("resource_authority");if(!"case".equals(authority.get("resource_kind")))throw Rejected.denied();
    inputs.authority(actor,authority,"auth.documents.respond",caseRef,request,Instant.now());inputs.admission(c,"auth.documents.respond");
    Object refs=c.get("document_refs");String setDigest=PortalReadModels.hash(refs);
    if(!setDigest.equals(c.get("document_set_digest")))throw Rejected.denied();
    inputs.documents(refs,"case",caseRef,Instant.now());
    Object admittedDigest=Jcs.object(c.get("admission")).get("admitted_digest");if(admittedDigest==null)throw Rejected.denied();
    var policy=inputs.policy(Jcs.ref(c,"assessment_ref"),"case",caseRef,request,PortalReadModels.number(binding.get("request_revision")),
      refs,c.get("documentacao_completa"),admittedDigest,Instant.now());
    if(policy.get("submitted_response_digest")==null||!PortalReadModels.hash(policy).equals(c.get("assessment_digest"))
        ||!c.get("assessment_ref").equals(occurrence.get("policy_ref"))||!c.get("assessment_digest").equals(occurrence.get("policy_digest")))throw Rejected.denied();
    var docs=new HashSet<String>();for(Object item:AuthModels.documents(refs))docs.add(Jcs.ref(Jcs.object(item),"document_ref"));
    inputs.exactKinds(Set.of("actor","resource_authority","document_policy"),docs);
    invocation.definition(Jcs.object(binding.get("definition")));AuthNativeWait.require(db,binding,Instant.now());
    var subscription=context.getEventSubscriptionManager().findEventSubscriptionById(Jcs.ref(binding,"subscription_id"));
    if(subscription==null||!binding.get("subscription_execution_id").equals(subscription.getExecutionId())
        ||subscription.getRevision()!=PortalReadModels.number(binding.get("subscription_revision")))throw Rejected.conflict();
    var root=context.getExecutionManager().findExecutionById(instance);
    if(root==null||!root.isProcessInstanceExecution()||root.isSuspended())throw Rejected.conflict();
    var terminal=new TreeMap<>(occurrence);terminal.put("state","consumed");terminal.put("terminal_command_id",command);
    db.updateOccurrence(terminal,((Number)row.get("rev_")).longValue());
    // Reserve the terminal occurrence before synchronous delivery; any successor loop gets a new
    // task/request generation. All native/custom rows roll back together on any later failure.
    root.setVariable("documentos_refs",AuthValues.json(AuthStore.text(refs)));
    root.setVariable("documentacao_completa",Variables.booleanValue((Boolean)c.get("documentacao_completa")));
    inputs.current(Instant.now());invocation.current();
    subscription.eventReceived(null,false);
    Instant timer=PortalReadModels.time(binding.get("timer_deadline"));Instant deadline=inputs.ceiling().isBefore(timer)?inputs.ceiling():timer;
    return invocation.finish(()->{
      if(db.optional("SELECT ID_ FROM ACT_RU_EVENT_SUBSCR WHERE ID_=?",binding.get("subscription_id"))!=null
          ||db.optional("SELECT ID_ FROM ACT_RU_JOB WHERE ID_=?",binding.get("timer_job_id"))!=null)throw Rejected.conflict();
      var admission=Jcs.object(c.get("admission"));var receipt=PortalReadModels.record("schema","human-auth-effect-receipt.v1",
        "scope",runtime.scope,"receipt_ref",UUID.randomUUID().toString(),"command_id",command,"command_digest",invocation.envelope.digest(),
        "operation","auth.documents.respond","actor_principal_ref",actor.get("principal_ref"),"admission_ref",admission.get("intent_ref"),
        "admitted_digest",admittedDigest,"intake_ref",null,"case_ref",caseRef,"process_instance_id",instance,"definition",binding.get("definition"),
        "guide_identity_ref",null,"request_ref",request,"occurrence_generation",binding.get("generation"),"subscription_id",binding.get("subscription_id"),
        "document_set_digest",setDigest,"outcome","documents_correlated","committed_at",PortalReadModels.time(Instant.now()));
      AuthModels.validate("receipt",receipt);db.saveEffectReceipt(receipt);return receipt;
    },()->inputs.current(Instant.now()),deadline);
  }
}
