package br.com.maezo.human;

import static br.com.maezo.human.PortalReadModels.*;
import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.util.*;
import org.cibseven.bpm.engine.impl.cfg.TransactionState;
import org.cibseven.bpm.engine.impl.interceptor.*;
import org.cibseven.bpm.engine.impl.persistence.entity.TaskEntity;

/** Shared authoritative ownership evaluator and same-transaction effect/receipt. */
final class GovernedAssignment implements Command<byte[]> {
  final AssignmentTrust trust;final byte[] raw;final String peer;
  private byte[][] result;
  final PortalReadPlugin.AssignmentConstraintLease constraints;
  final ExternalCaseModels.Configuration cases;
  GovernedAssignment(AssignmentTrust trust,byte[] raw,String peer,PortalReadPlugin.AssignmentConstraintLease constraints,ExternalCaseModels.Configuration cases){this.trust=trust;this.raw=raw.clone();this.peer=peer;this.constraints=constraints;this.cases=cases;}
  public byte[] execute(CommandContext context){
    if(trust==null)throw unavailable();var db=new EngineStore(context,trust.tenant);var installation=AssignmentInstallation.acquire(context,db,trust);
    var envelope=Envelope.verify(raw,trust.human,"human-command",peer,Instant.now().getEpochSecond(),db::revoked);var c=AssignmentModels.check("command",envelope.command());var identity=AssignmentModels.identity(c);
    var principal=db.principal(identity.principalRef(),identity.principalIssuer(),identity.principalSubject(),Instant.now().getEpochSecond());
    boolean historical=db.receipt(identity.taskId(),identity.commandId(),envelope.digest(),identity.principalRef(),identity.workloadRef())!=null;
    if(historical)AssignmentReceiptAuthority.commandRetry(context,trust,raw,peer,cases);
    long revision=db.lockTenant();
    byte[] previous=db.receipt(identity.taskId(),identity.commandId(),envelope.digest(),identity.principalRef(),identity.workloadRef());
    if(previous!=null&&!historical)throw unavailable(); // concurrent first commit: retry with the required lock order
    if(previous!=null){context.getTransactionContext().addTransactionListener(TransactionState.COMMITTING,ignored->{installation.current();envelope.requireCurrent(Instant.now().getEpochSecond());EngineStore.requireCurrentPrincipal(principal,Instant.now().getEpochSecond());});return previous;}
    State state=new State(context,db,trust,installation,revision,identity.taskId(),identity.principalRef(),identity.principalIssuer(),identity.principalSubject(),constraints);
    state.expect(c);String operation=str(c,"operation");Map<String,Object> target=c.get("target_ref")==null?null:state.member(str(c,"target_ref"));
    if(target!=null&&!target.get("membership_revision").equals(c.get("target_membership_revision")))throw conflict();
    state.require(operation,target);state.current();envelope.requireCurrent(Instant.now().getEpochSecond());
    String before=state.task.getAssignee(),after=operation.equals("release")?null:operation.equals("claim")?identity.principalRef():str(c,"target_ref");
    if(!Objects.equals(before,after))state.task.setAssignee(after);
    context.getDbEntityManager().forceUpdate(state.task);
    // Byte carrier populated only after normal engine flush; executor returns only after commit.
    final byte[][] committed=new byte[1][];
    context.getTransactionContext().addTransactionListener(TransactionState.COMMITTING,committing->{
      var rows=db.rows("SELECT REV_,ASSIGNEE_ FROM ACT_RU_TASK WHERE ID_=? AND TENANT_ID_=?",identity.taskId(),trust.tenant);
      if(rows.size()!=1||!Objects.equals(rows.get(0).get("assignee_"),after))throw conflict();
      var receipt=record("schema","human-engine-assignment-receipt.v1","status","committed","tenant",trust.tenant,"task_id",identity.taskId(),"command_id",identity.commandId(),"operation",operation,"payload_digest",envelope.digest(),"principal_ref",identity.principalRef(),"workload_ref",identity.workloadRef(),"audit_intent_ref",identity.auditIntentRef(),"consumed_task_revision",c.get("task_revision"),"resulting_task_revision",rows.get(0).get("rev_").toString(),"engine_receipt_ref",UUID.randomUUID().toString(),"recorded_at",Long.toString(Instant.now().getEpochSecond()),"command_schema","human-assignment.v2","prior_assignee_ref",before,"resulting_assignee_ref",after,"assignment_disposition",Objects.equals(before,after)?"unchanged":"changed");
      for(String field:List.of("binding_ref","binding_version","binding_digest","policy_ref","policy_version","policy_digest","source_revision","generation_digest","target_ref","target_membership_revision"))receipt.put(field,c.get(field));
      byte[] bytes=bounded(receipt);db.insertReceipt(identity,envelope.digest(),bytes);
      var linkage=AssignmentReceiptAuthority.linkage(state.task,state.binding);
      db.update("INSERT INTO MZO_HUMAN_ASSIGNMENT_RECEIPT_RESOURCE(TENANT_,TASK_,COMMAND_,DIGEST_,PRINCIPAL_,WORKLOAD_,LINKAGE_) VALUES(?,?,?,?,?,?,?)",trust.tenant,identity.taskId(),identity.commandId(),envelope.digest(),identity.principalRef(),identity.workloadRef(),AssignmentPublication.text(linkage));
      state.current();envelope.requireCurrent(Instant.now().getEpochSecond());
      committing.getTransactionContext().addTransactionListener(TransactionState.COMMITTED,ignored->committed[0]=bytes);
    });
    // HumanCommandPlugin retrieves the committed bytes through executeResult below.
    result=committed;return null;
  }
  static byte[] executeResult(CommandExecutor executor,GovernedAssignment command){
    return committedResult(command,executor.execute(command));
  }
  private static byte[] committedResult(Command<byte[]> command,byte[] previous){
    if(previous!=null)return previous;
    if(!(command instanceof GovernedAssignment assignment)||assignment.result==null||assignment.result[0]==null)throw unavailable();
    return assignment.result[0].clone();
  }

  private static final class ConstraintRequired extends RuntimeException {}
  /** Only State's qualified published_resource branch may produce this pre-effect demand. */
  private static void requireConstraints(){throw new ConstraintRequired();}

  static byte[] withConstraints(CommandExecutor executor,
      java.util.function.Function<PortalReadPlugin.AssignmentConstraintLease,Command<byte[]>> commands,
      java.util.function.Supplier<PortalReadPlugin.AssignmentConstraintLease> acquire){
    outsideCommand();
    Command<byte[]> first=commands.apply(null);boolean[] demand={false};
    byte[] bytes=executor.execute(context->{
      try{return first.execute(context);}
      catch(ConstraintRequired required){
        // Normal standalone commit, not a rollback notification, proves this read-only TX ended.
        if(context.getTransactionContext().getClass()!=org.cibseven.bpm.engine.impl.cfg.standalone.StandaloneTransactionContext.class)throw unavailable();
        var environment=context.getDbSqlSession().getSqlSession().getConfiguration().getEnvironment();
        if(environment==null||environment.getTransactionFactory().getClass()!=org.apache.ibatis.transaction.jdbc.JdbcTransactionFactory.class)throw unavailable();
        demand[0]=true;return null;
      }
    });
    if(!demand[0])return committedResult(first,bytes);
    outsideCommand();
    var lease=acquire.get();if(lease==null)throw unavailable();
    Command<byte[]> second=commands.apply(lease);
    // No catch here: a second demand or any commit/close/interceptor error refuses.
    return committedResult(second,executor.execute(second));
  }
  private static void outsideCommand(){
    if(org.cibseven.bpm.engine.impl.context.Context.getCommandContext()!=null
        ||org.cibseven.bpm.engine.impl.context.Context.getCommandInvocationContext()!=null)throw unavailable();
  }


  static final class State {
    final CommandContext context;final EngineStore db;final AssignmentTrust trust;final AssignmentInstallation installation;final long revision;
    final Map<String,Object> stored,generation,binding,policy,actor,evidence,entry;
    final TaskEntity task;final Set<String> groups=new TreeSet<>();
    final PortalReadPlugin.AssignmentConstraintLease constraints;
    final List<Instant> deadlines=new ArrayList<>();final List<Runnable> constraintGuards=new ArrayList<>();final Map<String,Map<String,Object>> resources=new HashMap<>();
    State(CommandContext context,EngineStore db,AssignmentTrust trust,AssignmentInstallation installation,long revision,String taskId,String principal,String issuer,String subject,PortalReadPlugin.AssignmentConstraintLease constraints){
      this.context=context;this.db=db;this.trust=trust;this.installation=installation;this.revision=revision;this.constraints=constraints;deadlines.add(installation.deadline());
      var rows=db.rows("SELECT * FROM MZO_HUMAN_ASSIGNMENT_GENERATION WHERE TENANT_=?",trust.tenant);if(rows.size()!=1||!rows.get(0).get("state_").equals("active"))throw unavailable();stored=rows.get(0);
      generation=AssignmentModels.check("generation",Jcs.parse(str(stored,"generation_").getBytes(StandardCharsets.UTF_8)));
      if(!hash(generation).equals(stored.get("generation_digest_")))throw unavailable();trust.scope(generation);AssignmentPublication.validateGeneration(generation,trust,Instant.now());
      var source=AssignmentModels.check("attestation",Jcs.parse(str(stored,"attestation_").getBytes(StandardCharsets.UTF_8)));trust.source(source,Instant.now(),db);if(!source.get("generation_digest").equals(hash(generation))||!obj(source,"source").get("source_digest").equals(hash(generation))||!obj(source,"source").get("source_revision").equals(generation.get("source_revision")))throw unavailable();deadlines.add(Instant.ofEpochSecond(number(obj(trust.config,"source").get("not_after"))));deadlines.add(time(obj(source,"source").get("valid_until")));deadlines.add(Instant.ofEpochSecond(number(generation.get("valid_until"))));
      task=context.getTaskManager().findTaskById(taskId);if(task==null||task.isSuspended()||!trust.tenant.equals(task.getTenantId()))throw conflict();
      var matches=list(generation.get("bindings")).stream().map(PortalReadModels::map).filter(b->b.get("qualification").equals("qualified")&&b.get("process_definition_id").equals(task.getProcessDefinitionId())&&b.get("task_definition_key").equals(task.getTaskDefinitionKey())).toList();
      if(matches.size()!=1)throw new Rejected(409,"FORM_NOT_ACTIVATED");binding=matches.get(0);
      policy=list(generation.get("policies")).stream().map(PortalReadModels::map).filter(p->p.get("policy_ref").equals(binding.get("policy_ref"))&&p.get("version").equals(binding.get("policy_version"))&&hash(p).equals(binding.get("policy_digest"))).findFirst().orElseThrow(PortalReadModels::unavailable);
      deadlines.add(Instant.ofEpochSecond(number(binding.get("valid_until"))));deadlines.add(Instant.ofEpochSecond(number(policy.get("valid_until"))));
      var process=task.getProcessDefinition();if(!process.getKey().equals(binding.get("process_definition_key"))||process.getVersion()!=number(binding.get("process_definition_version")))throw conflict();
      try(var bytes=context.getProcessEngineConfiguration().getRepositoryService().getProcessModel(process.getId())){PortalReadCommand.resourceDigest(bytes,str(binding,"process_definition_digest"));}catch(java.io.IOException ex){throw unavailable();}
      var domain=obj(binding,"group_domain");for(var link:task.getCandidates())if(link.getGroupId()!=null){if(!list(domain.get("groups")).contains(link.getGroupId()))throw denied();groups.add(link.getGroupId());}
      if(groups.isEmpty())throw denied();
      if(domain.get("kind").equals("dmn")){
        var repository=context.getProcessEngineConfiguration().getRepositoryService();var definition=repository.createDecisionDefinitionQuery().decisionDefinitionId(str(domain,"dmn_definition_id")).singleResult();
        if(definition==null||!definition.getKey().equals(domain.get("dmn_definition_key"))||definition.getVersion()!=number(domain.get("dmn_definition_version"))||!trust.tenant.equals(definition.getTenantId()))throw conflict();
        try(var bytes=repository.getDecisionModel(definition.getId())){PortalReadCommand.resourceDigest(bytes,str(domain,"dmn_resource_digest"));}catch(java.io.IOException ex){throw unavailable();}
      }
      var catalog=validate("artifactcatalog",Jcs.parse(artifact(str(binding,"catalog_ref"),str(binding,"catalog_digest"))));
      var entries=list(catalog.get("entries")).stream().map(PortalReadModels::map).filter(e->e.get("process_definition_id").equals(task.getProcessDefinitionId())&&e.get("task_definition_key").equals(task.getTaskDefinitionKey())).toList();if(entries.size()!=1)throw unavailable();entry=entries.get(0);PortalReadCommand.compiled(entry);
      for(String field:List.of("process_definition_id","process_definition_key","process_definition_version","process_definition_digest","task_definition_key","form_key","form_version","form_digest","group_domain","subject_policy","consent_policy"))if(!Objects.equals(entry.get(field),binding.get(field)))throw conflict();
      if(!catalog.get("catalog_ref").equals(binding.get("catalog_ref"))||!catalog.get("deployment_receipt_ref").equals(obj(binding,"deployment_receipt").get("artifact_ref"))||!catalog.get("deployment_receipt_digest").equals(obj(binding,"deployment_receipt").get("digest")))throw denied();
      var ev=db.rows("SELECT * FROM MZO_HUMAN_EVIDENCE WHERE TENANT_=? AND TASK_=?",trust.tenant,taskId);if(ev.size()!=1)throw conflict();evidence=ev.get(0);if(!task.getProcessDefinitionId().equals(evidence.get("process_")))throw conflict();deadlines.add(Instant.ofEpochSecond(((Number)evidence.get("valid_until_")).longValue()));
      actor=member(principal);if(!actor.get("issuer").equals(issuer)||!actor.get("subject").equals(subject))throw denied();
      if(!roleContract(actor))throw denied();constraints(actor);current();
    }
    byte[] artifact(String ref,String digest){for(Object o:list(generation.get("artifacts"))){var a=map(o);if(a.get("artifact_ref").equals(ref)&&a.get("digest").equals(digest))return b64(a.get("bytes_base64"),false,-1);}throw unavailable();}
    Map<String,Object> member(String ref){
      var found=list(generation.get("memberships")).stream().map(PortalReadModels::map).filter(m->m.get("principal_ref").equals(ref)).findFirst().orElseThrow(PortalReadModels::denied);
      if(!found.get("state").equals("active"))throw denied();var nativeMember=db.principal(ref,str(found,"issuer"),str(found,"subject"),Instant.now().getEpochSecond());
      if(((Number)nativeMember.get("rev_")).longValue()!=((Number)stored.get("authority_revision_")).longValue())throw conflict();
      var flat=new TreeSet<String>();for(Object m:list(found.get("memberships")))for(Object g:list(map(m).get("groups")))flat.add((String)g);
      if(!new HashSet<>(list(Jcs.parse(str(nativeMember,"groups_").getBytes(StandardCharsets.UTF_8)))).equals(flat))throw conflict();
      deadlines.add(time(found.get("reviewed_until")));deadlines.add(Instant.ofEpochSecond(((Number)nativeMember.get("valid_until_")).longValue()));return found;
    }
    boolean roleContract(Map<String,Object> member){return list(member.get("memberships")).stream().map(PortalReadModels::map).anyMatch(m->list(m.get("roles")).containsAll(list(entry.get("required_roles")))&&!Collections.disjoint(list(m.get("groups")),groups));}
    Map<String,Object> constraints(Map<String,Object> member){
      if(binding.get("constraint_mode").equals("none"))return record("required_subject_bindings",List.of(),"required_consent_scopes",List.of(),"read_only_evidence",null);
      String ref=str(member,"principal_ref");if(resources.containsKey(ref))return resources.get(ref);
      if(constraints==null)requireConstraints();var designation=list(generation.get("resource_designations")).stream().map(PortalReadModels::map).filter(d->d.get("task_id").equals(task.getId())&&d.get("binding_ref").equals(binding.get("binding_ref"))&&d.get("binding_version").equals(binding.get("version"))).findFirst().orElseThrow(PortalReadModels::unavailable);
      var principal=new TreeMap<>(member);principal.put("tenant",trust.tenant);
      var leased=constraints.verify(context,principal,binding,designation,installation::current);constraintGuards.add(leased.current());var verified=leased.facts();deadlines.add(time(verified.get("valid_until")));var resource=obj(verified,"resource");resources.put(ref,resource);return resource;
    }
    Map<String,Object> rule(String operation){return list(policy.get("rules")).stream().map(PortalReadModels::map).filter(r->r.get("operation").equals(operation)).findFirst().orElseThrow(PortalReadModels::denied);}
    void require(String operation,Map<String,Object> target){
      if(!list(binding.get("allowed_operations")).contains(operation)||!AssignmentModels.permits(rule(operation),actor,target,task.getAssignee(),groups,Instant.now()))throw denied();
      if(target!=null){if(!roleContract(target))throw denied();constraints(target);}current();
    }
    List<Object> candidates(){
      var rule=rule("reassign");if(!list(binding.get("allowed_operations")).contains("reassign")||!AssignmentModels.requirement(obj(rule,"actor"),actor,groups))throw denied();
      String ownership=task.getAssignee()==null?"unassigned":task.getAssignee().equals(actor.get("principal_ref"))?"actor_assigned":"other_assigned";if(!list(rule.get("ownership_states")).contains(ownership))throw denied();
      var result=new ArrayList<Object>();for(Object o:list(generation.get("memberships"))){var target=map(o);
        if(AssignmentModels.permits(rule,actor,target,task.getAssignee(),groups,Instant.now())&&roleContract(target)){
          member(str(target,"principal_ref"));try{constraints(target);}catch(Rejected denied){if(denied.status==404)continue;throw denied;}result.add(record("target_ref",target.get("principal_ref"),"target_membership_revision",target.get("membership_revision")));
        }
      }bounded(result);current();return result;
    }
    List<Object> operations(){var result=new ArrayList<Object>();for(Object o:list(policy.get("rules"))){var rule=map(o);String op=str(rule,"operation");if(!list(binding.get("allowed_operations")).contains(op))continue;
      if(op.equals("reassign")){String state=task.getAssignee()==null?"unassigned":task.getAssignee().equals(actor.get("principal_ref"))?"actor_assigned":"other_assigned";if(AssignmentModels.requirement(obj(rule,"actor"),actor,groups)&&list(rule.get("ownership_states")).contains(state))result.add(op);}
      else if(AssignmentModels.permits(rule,actor,null,task.getAssignee(),groups,Instant.now()))result.add(op);
    }return result;}
    Instant deadline(){current();return deadlines.stream().min(Instant::compareTo).orElseThrow(PortalReadModels::unavailable);}
    void current(){installation.current();for(Runnable guard:constraintGuards)guard.run();Instant now=Instant.now();for(Instant until:deadlines)if(!now.isBefore(until))throw conflict();}
    Map<String,Object> publicContext(){var c=record("schema_version","portal-assignment-context.v2","task_id",task.getId(),"expected_task_revision",Integer.toString(task.getRevision()),"expected_evidence_revision",evidence.get("rev_").toString(),"expected_evidence_digest",evidence.get("digest_"),"expected_membership_revision",actor.get("membership_revision"),"expected_authority_revision",Long.toString(revision),"assignee_ref",task.getAssignee(),"allowed_operations",operations(),"valid_until",time(deadline()),"binding_ref",binding.get("binding_ref"),"binding_version",binding.get("version"),"binding_digest",hash(binding),"policy_ref",policy.get("policy_ref"),"policy_version",policy.get("version"),"policy_digest",hash(policy),"source_revision",generation.get("source_revision"),"generation_digest",stored.get("generation_digest_"));for(String k:List.of("process_definition_key","process_definition_version","process_definition_id","process_definition_digest","task_definition_key","form_key","form_version","form_digest"))c.put(k,binding.get(k));return c;}
    Map<String,Object> task(){var snap=record("schema_version","1","snapshot_at",time(Instant.now()),"task_id",task.getId(),"task_revision",Integer.toString(task.getRevision()),"assignee_ref",task.getAssignee(),"eligible_candidate_groups",new ArrayList<>(groups),"evidence_revision",evidence.get("rev_").toString(),"evidence_digest",evidence.get("digest_"),"engine_due_at",task.getDueDate()==null?null:time(task.getDueDate().toInstant()),"allowed_actions",operations());for(String k:List.of("process_definition_key","process_definition_version","process_definition_id","process_definition_digest","task_definition_key","form_key","form_version","form_digest","form_source_status","allowed_inputs"))snap.put(k,entry.get(k));var facts=constraints(actor);snap.put("read_only_evidence",facts.get("read_only_evidence"));return record("tenant",trust.tenant,"snapshot",snap,"active",true,"authority_revision",Long.toString(revision),"required_roles",entry.get("required_roles"),"required_subject_bindings",facts.get("required_subject_bindings"),"required_consent_scopes",facts.get("required_consent_scopes"),"valid_until",time(deadline()));}
    Map<String,Object> authority(String operation){var a=new TreeMap<>(publicContext());for(String k:List.of("schema_version","expected_task_revision","expected_evidence_revision","expected_evidence_digest","expected_membership_revision","expected_authority_revision","assignee_ref","allowed_operations","binding_ref","binding_version","binding_digest","policy_ref","policy_version","policy_digest","source_revision","generation_digest"))a.remove(k);
      a.putAll(record("tenant",trust.tenant,"issuer",actor.get("issuer"),"subject",actor.get("subject"),"principal_ref",actor.get("principal_ref"),"membership_revision",actor.get("membership_revision"),"authority_revision",Long.toString(revision),"task_revision",Integer.toString(task.getRevision()),"evidence_revision",evidence.get("rev_").toString(),"evidence_digest",evidence.get("digest_"),"read_permitted",true,"permitted_operations",List.of(operation),"consent_scopes",constraints(actor).get("required_consent_scopes")));return a;}
    void expect(Map<String,Object> c){var expected=publicContext();for(String[] p:List.of(new String[]{"task_revision","expected_task_revision"},new String[]{"membership_revision","expected_membership_revision"},new String[]{"authority_revision","expected_authority_revision"},new String[]{"evidence_revision","expected_evidence_revision"},new String[]{"evidence_digest","expected_evidence_digest"},new String[]{"assignee_ref","assignee_ref"},new String[]{"binding_ref","binding_ref"},new String[]{"binding_version","binding_version"},new String[]{"binding_digest","binding_digest"},new String[]{"policy_ref","policy_ref"},new String[]{"policy_version","policy_version"},new String[]{"policy_digest","policy_digest"},new String[]{"source_revision","source_revision"},new String[]{"generation_digest","generation_digest"}))if(!Objects.equals(c.get(p[0]),expected.get(p[1])))throw conflict();
      for(String k:List.of("process_definition_key","process_definition_version","process_definition_id","process_definition_digest","task_definition_key","form_key","form_version","form_digest"))if(!c.get(k).equals(binding.get(k)))throw conflict();if(!c.get("evidence_ref").equals(evidence.get("ref_")))throw conflict();}
  }
}
