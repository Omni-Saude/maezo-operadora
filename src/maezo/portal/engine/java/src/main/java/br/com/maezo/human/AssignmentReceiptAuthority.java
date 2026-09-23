package br.com.maezo.human;

import static br.com.maezo.human.PortalReadModels.*;
import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.util.*;
import org.cibseven.bpm.engine.impl.cfg.TransactionState;
import org.cibseven.bpm.engine.impl.interceptor.*;
import org.cibseven.bpm.engine.impl.persistence.entity.TaskEntity;

/** C9 exact historical resource disclosure. Neither old assignee nor receipt possession grants it. */
final class AssignmentReceiptAuthority implements Command<byte[]> {
  final AssignmentTrust trust;final byte[] raw;final String peer;final ExternalCaseModels.Configuration cases;final boolean commandRetry;
  AssignmentReceiptAuthority(AssignmentTrust trust,byte[] raw,String peer,ExternalCaseModels.Configuration cases){this(trust,raw,peer,cases,false);}
  private AssignmentReceiptAuthority(AssignmentTrust trust,byte[] raw,String peer,ExternalCaseModels.Configuration cases,boolean commandRetry){this.trust=trust;this.raw=raw.clone();this.peer=peer;this.cases=cases;this.commandRetry=commandRetry;}
  static void commandRetry(CommandContext context,AssignmentTrust trust,byte[] raw,String peer,ExternalCaseModels.Configuration cases){new AssignmentReceiptAuthority(trust,raw,peer,cases,true).execute(context);}
  static Map<String,Object> linkage(TaskEntity task,Map<String,Object> binding){
    var l=record("task_id",task.getId(),"process_instance_id",task.getProcessInstanceId());
    for(String k:List.of("process_definition_id","process_definition_key","process_definition_version","process_definition_digest","task_definition_key","binding_ref"))l.put(k,binding.get(k));
    l.put("binding_version",binding.get("version"));l.put("binding_digest",hash(binding));return AssignmentModels.check("receiptlinkage",l);
  }
  public byte[] execute(CommandContext context){
    if(trust==null)throw unavailable();var db=new EngineStore(context,trust.tenant);var installed=AssignmentInstallation.acquire(context,db,trust);
    var envelope=Envelope.verify(raw,trust.human,commandRetry?"human-command":"human-assignment-read",peer,Instant.now().getEpochSecond(),db::revoked,trust.readKeys);
    var q=commandRetry?retryQuery(db,envelope):AssignmentModels.check("receiptquery",envelope.command());var identity=obj(q,"identity");
    if(!trust.tenant.equals(identity.get("tenant"))||!identity.get("principal_ref").equals(q.get("principal_ref"))||trust.human.keys.values().stream().noneMatch(k->k.purpose().equals("human-command")&&k.workload().equals(identity.get("workload_ref"))))throw denied();
    // Inspect only to choose the documented lock order. Exact current disclosure is reread under locks.
    var peek=disclosure(db,identity);ExternalCaseStore external=null;
    if(obj(obj(peek,"disclosure"),"resource").get("kind").equals("case")){
      if(cases==null)throw unavailable();external=new ExternalCaseStore(context,cases.scope(),1,cases.engineSchema());external.lock();
    }
    db.lockTenant();var request=disclosure(db,identity);var d=obj(request,"disclosure");
    AssignmentReceiptPublication.validate(request,trust,db);if(!d.get("identity").equals(identity)||!d.get("state").equals("active"))throw denied();
    List<Instant> ceilings=new ArrayList<>(List.of(installed.deadline(),Instant.ofEpochSecond(envelope.expiresAt()),time(d.get("valid_until")),time(obj(d,"policy").get("valid_until")),time(obj(d,"grant").get("valid_until")),time(obj(obj(request,"source"),"source").get("valid_until")),Instant.ofEpochSecond(number(obj(trust.config,"receipt_disclosure_source").get("not_after")))));
    var member=currentMember(db,q,ceilings);var grant=obj(d,"grant");
    for(String[] names:List.of(new String[]{"issuer","principal_issuer"},new String[]{"subject","principal_subject"},new String[]{"principal_ref","principal_ref"},new String[]{"membership_revision","membership_revision"}))if(!grant.get(names[0]).equals(q.get(names[1])))throw denied();
    var policy=obj(d,"policy");var requirement=obj(policy,"membership_requirement");
    if(!list(member.get("memberships")).stream().map(PortalReadModels::map).anyMatch(m->list(m.get("roles")).containsAll(list(requirement.get("roles")))&&!Collections.disjoint(list(m.get("groups")),list(requirement.get("groups"))))||!list(member.get("subject_bindings")).containsAll(list(d.get("required_subject_bindings")))||!list(grant.get("consent_scopes")).containsAll(list(policy.get("required_consent_scopes"))))throw denied();
    var retained=db.rows("SELECT * FROM MZO_HUMAN_ASSIGNMENT_RECEIPT_RESOURCE WHERE TENANT_=? AND TASK_=? AND COMMAND_=?",trust.tenant,identity.get("task_id"),identity.get("command_id"));var expected=obj(d,"linkage");
    if(!retained.isEmpty()){
      var row=retained.get(0);if(!identity.get("payload_digest").equals(row.get("digest_"))||!identity.get("principal_ref").equals(row.get("principal_"))||!identity.get("workload_ref").equals(row.get("workload_"))||!expected.equals(map(Jcs.parse(str(row,"linkage_").getBytes(StandardCharsets.UTF_8)))))throw denied();
      byte[] receipt=db.receipt(str(identity,"task_id"),str(identity,"command_id"),str(identity,"payload_digest"),str(identity,"principal_ref"),str(identity,"workload_ref"));
      if(receipt==null||!map(Jcs.parse(receipt)).get("schema").equals("human-engine-assignment-receipt.v1"))throw denied();
    }else{
      var old=db.rows("SELECT RECEIPT_ FROM MZO_HUMAN_RECEIPT WHERE TENANT_=? AND TASK_=? AND COMMAND_=?",trust.tenant,identity.get("task_id"),identity.get("command_id"));if(!old.isEmpty())throw unavailable();
      TaskEntity task=context.getTaskManager().findTaskById(str(identity,"task_id"));if(task==null||!trust.tenant.equals(task.getTenantId())||!task.getProcessInstanceId().equals(expected.get("process_instance_id"))||!task.getProcessDefinitionId().equals(expected.get("process_definition_id"))||!task.getTaskDefinitionKey().equals(expected.get("task_definition_key")))throw unavailable();
      var definition=task.getProcessDefinition();if(!definition.getKey().equals(expected.get("process_definition_key"))||definition.getVersion()!=number(expected.get("process_definition_version")))throw unavailable();
      try(var bytes=context.getProcessEngineConfiguration().getRepositoryService().getProcessModel(definition.getId())){PortalReadCommand.resourceDigest(bytes,str(expected,"process_definition_digest"));}catch(java.io.IOException ex){throw unavailable();}
      context.getTransactionContext().addTransactionListener(TransactionState.COMMITTING,x->{var actual=db.rows("SELECT PROC_INST_ID_,PROC_DEF_ID_,TASK_DEF_KEY_ FROM ACT_RU_TASK WHERE ID_=? AND TENANT_ID_=?",task.getId(),trust.tenant);if(actual.size()!=1||!actual.get(0).get("proc_inst_id_").equals(expected.get("process_instance_id"))||!actual.get(0).get("proc_def_id_").equals(expected.get("process_definition_id"))||!actual.get(0).get("task_def_key_").equals(expected.get("task_definition_key")))throw unavailable();});
    }
    var resource=obj(d,"resource");ExternalCaseModels.Authority caseAuthority=null;
    if(resource.get("kind").equals("case")){
      if(external==null)throw conflict();caseAuthority=external.authority(cases);separated(caseAuthority);
      var head=external.head(str(resource,"ownership_source_ref"));long version=number(resource.get("ownership_source_revision"));
      if(head==null||ExternalCaseStore.numberColumn(head,"source_revision")!=version||!resource.get("ownership_source_digest").equals(head.get("payload_digest"))||Boolean.TRUE.equals(head.get("revoked")))throw denied();
      var event=external.event(str(resource,"ownership_source_ref"),version);if(event==null)throw unavailable();var packet=ExternalCaseStore.json(event.get("canonical_payload"));if(!hash(packet).equals(resource.get("ownership_source_digest")))throw denied();var source=caseAuthority.source(packet);
      if(!source.get("state").equals("active")||!source.get("identity").equals(resource.get("case_identity"))||Collections.disjoint(list(source.get("owners")),list(d.get("required_subject_bindings"))))throw denied();
      var projected=external.one("SELECT source_ref,source_revision,revoked,publication_id,identity FROM maezo_external.mzo_external_case WHERE "+ExternalCaseStore.S+" AND case_ref=?",external.args(resource.get("resource_ref")));
      if(projected==null||Boolean.TRUE.equals(projected.get("revoked"))||!projected.get("source_ref").equals(resource.get("ownership_source_ref"))||ExternalCaseStore.numberColumn(projected,"source_revision")!=version||!ExternalCaseStore.json(projected.get("identity")).equals(resource.get("case_identity")))throw denied();
      var pub=external.receipt((String)projected.get("publication_id"));if(pub==null||external.revoked().contains(pub.get("requester_fingerprint")))throw unavailable();var proof=ExternalCaseStore.json(pub.get("canonical_receipt"));if(!proof.get("source_ref").equals(resource.get("ownership_source_ref"))||!proof.get("source_revision").equals(resource.get("ownership_source_revision"))||!proof.get("source_digest").equals(resource.get("ownership_source_digest")))throw denied();ceilings.add(caseAuthority.until());
    }
    Runnable guard=()->{installed.current();envelope.requireCurrent(Instant.now().getEpochSecond());for(Instant until:ceilings)if(!Instant.now().isBefore(until))throw unavailable();};guard.run();
    Instant until=ceilings.stream().min(Instant::compareTo).orElseThrow();var authority=record("identity",identity,"issuer",q.get("principal_issuer"),"subject",q.get("principal_subject"),"membership_revision",q.get("membership_revision"),"read_permitted",true,"valid_until",time(until));
    context.getTransactionContext().addTransactionListener(TransactionState.COMMITTING,x->{AssignmentReceiptPublication.validate(request,trust,db);guard.run();});return bounded(record("schema","human-assignment-receipt-authority-result.v1","request_digest",envelope.digest(),"authority",authority,"valid_until",time(until)));
  }
  Map<String,Object> retryQuery(EngineStore db,Envelope.Verified envelope){
    var c=AssignmentModels.check("command",envelope.command());
    // Read only a revision hint before the W6/tenant lock order; currentMember revalidates it under lock.
    var rows=db.rows("SELECT GENERATION_ FROM MZO_HUMAN_ASSIGNMENT_GENERATION WHERE TENANT_=?",trust.tenant);if(rows.size()!=1)throw unavailable();
    var generation=AssignmentModels.check("generation",Jcs.parse(str(rows.get(0),"generation_").getBytes(StandardCharsets.UTF_8)));
    var member=list(generation.get("memberships")).stream().map(PortalReadModels::map).filter(m->m.get("principal_ref").equals(c.get("principal_ref"))).findFirst().orElseThrow(PortalReadModels::denied);
    var identity=record("tenant",c.get("tenant"),"task_id",c.get("task_id"),"command_id",c.get("command_id"),"payload_digest",envelope.digest(),"principal_ref",c.get("principal_ref"),"workload_ref",c.get("workload_ref"));
    return AssignmentModels.check("receiptquery",record("schema","human-assignment-receipt-authority.v1","tenant",trust.tenant,"workload_ref",c.get("workload_ref"),"principal_ref",c.get("principal_ref"),"principal_issuer",c.get("principal_issuer"),"principal_subject",c.get("principal_subject"),"membership_revision",member.get("membership_revision"),"identity",identity));
  }
  Map<String,Object> disclosure(EngineStore db,Map<String,Object> identity){var rows=db.rows("SELECT REQUEST_ FROM MZO_HUMAN_ASSIGNMENT_RECEIPT_DISCLOSURE WHERE TENANT_=? AND TASK_=? AND COMMAND_=?",trust.tenant,identity.get("task_id"),identity.get("command_id"));if(rows.size()!=1)throw unavailable();return AssignmentModels.check("receiptpublication",Jcs.parse(str(rows.get(0),"request_").getBytes(StandardCharsets.UTF_8)));}
  Map<String,Object> currentMember(EngineStore db,Map<String,Object> query,List<Instant> ceilings){
    var rows=db.rows("SELECT * FROM MZO_HUMAN_ASSIGNMENT_GENERATION WHERE TENANT_=? AND STATE_='active'",trust.tenant);if(rows.size()!=1)throw unavailable();var row=rows.get(0);var generation=AssignmentModels.check("generation",Jcs.parse(str(row,"generation_").getBytes(StandardCharsets.UTF_8)));if(!hash(generation).equals(row.get("generation_digest_")))throw unavailable();trust.scope(generation);AssignmentPublication.validateGeneration(generation,trust,Instant.now());var source=AssignmentModels.check("attestation",Jcs.parse(str(row,"attestation_").getBytes(StandardCharsets.UTF_8)));trust.source(source,Instant.now(),db);if(!source.get("generation_digest").equals(hash(generation))||!obj(source,"source").get("source_digest").equals(hash(generation))||!obj(source,"source").get("source_revision").equals(generation.get("source_revision")))throw unavailable();ceilings.add(time(obj(source,"source").get("valid_until")));ceilings.add(Instant.ofEpochSecond(number(generation.get("valid_until"))));ceilings.add(Instant.ofEpochSecond(number(obj(trust.config,"source").get("not_after"))));
    var member=list(generation.get("memberships")).stream().map(PortalReadModels::map).filter(m->m.get("principal_ref").equals(query.get("principal_ref"))).findFirst().orElseThrow(PortalReadModels::denied);
    if(!member.get("state").equals("active")||!member.get("issuer").equals(query.get("principal_issuer"))||!member.get("subject").equals(query.get("principal_subject"))||!member.get("membership_revision").equals(query.get("membership_revision")))throw denied();
    var nativeMember=db.principal(str(member,"principal_ref"),str(member,"issuer"),str(member,"subject"),Instant.now().getEpochSecond());if(!nativeMember.get("rev_").equals(row.get("authority_revision_")))throw conflict();var groups=new TreeSet<String>(UTF8);for(Object m:list(member.get("memberships")))for(Object g:list(map(m).get("groups")))groups.add((String)g);if(!new HashSet<>(list(Jcs.parse(str(nativeMember,"groups_").getBytes(StandardCharsets.UTF_8)))).equals(groups))throw denied();ceilings.add(time(member.get("reviewed_until")));ceilings.add(Instant.ofEpochSecond(((Number)nativeMember.get("valid_until_")).longValue()));return member;
  }
  void separated(ExternalCaseModels.Authority authority){var own=new HashSet<String>();trust.human.keys.values().forEach(k->own.add(k.fingerprint()));trust.readKeys.values().forEach(k->own.add(k.fingerprint()));own.add(Jcs.digest(trust.sourceKey.getEncoded()));own.add(Jcs.digest(trust.receiptSourceKey.getEncoded()));for(Object signer:list(authority.bundle.get("signers")))if(own.contains(str(map(signer),"fingerprint")))throw denied();}
}
