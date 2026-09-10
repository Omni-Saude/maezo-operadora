package br.com.maezo.human;

import static br.com.maezo.human.PortalReadModels.*;
import java.time.Instant;
import java.util.*;
import org.cibseven.bpm.engine.impl.cfg.TransactionState;
import org.cibseven.bpm.engine.impl.interceptor.*;

/** Principal-aware internal query; current facts are not a mutation capability. */
final class AssignmentQuery implements Command<byte[]> {
  final AssignmentTrust trust;final byte[] raw;final String peer,operation;
  final PortalReadPlugin.AssignmentConstraintLease constraints;
  AssignmentQuery(AssignmentTrust trust,byte[] raw,String peer,String operation,PortalReadPlugin.AssignmentConstraintLease constraints){this.trust=trust;this.raw=raw.clone();this.peer=peer;this.operation=operation;this.constraints=constraints;}
  public byte[] execute(CommandContext context){
    if(trust==null)throw unavailable();var db=new EngineStore(context,trust.tenant);var installed=AssignmentInstallation.acquire(context,db,trust);long revision=db.lockTenant();
    var envelope=Envelope.verify(raw,trust.human,"human-assignment-read",peer,Instant.now().getEpochSecond(),db::revoked,trust.readKeys);var q=AssignmentModels.check("query",envelope.command());
    if(!operation.equals(q.get("operation")))throw invalid();
    var state=new GovernedAssignment.State(context,db,trust,installed,revision,str(q,"task_id"),str(q,"principal_ref"),str(q,"principal_issuer"),str(q,"principal_subject"),constraints);
    // Initial reads resolve native membership; authority compares the displayed context below.
    state.deadlines.add(Instant.ofEpochSecond(envelope.expiresAt()));
    Object candidates=null,authority=null;
    if(operation.equals("authority")){
      var expected=AssignmentModels.check("context",q.get("expected_context"));var current=state.publicContext();
      for(String key:current.keySet())if(!key.equals("valid_until")&&!Objects.equals(current.get(key),expected.get(key)))throw conflict();
      PortalReadModels.current(Instant.now(),expected.get("valid_until"));
      String requested=str(q,"requested_operation");
      if(!Set.of("claim","release","reassign").contains(requested)||requested.equals("reassign")!=(q.get("target_ref")!=null&&q.get("target_membership_revision")!=null))throw invalid();
      if(!requested.equals("reassign")&&(q.get("target_ref")!=null||q.get("target_membership_revision")!=null))throw invalid();
      var target=q.get("target_ref")==null?null:state.member(str(q,"target_ref"));if(target!=null&&!target.get("membership_revision").equals(q.get("target_membership_revision")))throw conflict();state.require(requested,target);authority=state.authority(requested);
    }else{
      if(q.get("expected_context")!=null||q.get("target_ref")!=null||q.get("target_membership_revision")!=null||q.get("requested_operation")!=null)throw invalid();
      if(operation.equals("candidates"))candidates=state.candidates();
    }
    var result=record("schema","human-assignment-query-result.v1","request_digest",envelope.digest(),"context",state.publicContext(),"candidates",candidates,"authority",authority,"task",state.task(),"evidence",record("tenant",trust.tenant,"task_id",state.task.getId(),"evidence_ref",state.evidence.get("ref_"),"revision",state.evidence.get("rev_").toString(),"digest",state.evidence.get("digest_"),"valid_until",time(state.deadline())),"valid_until",time(state.deadline()));byte[] bytes=bounded(result);
    String taskRevision=Integer.toString(state.task.getRevision());String assignee=state.task.getAssignee();
    context.getTransactionContext().addTransactionListener(TransactionState.COMMITTING,ignored->{
      var actual=db.rows("SELECT REV_,ASSIGNEE_,SUSPENSION_STATE_ FROM ACT_RU_TASK WHERE ID_=? AND TENANT_ID_=?",state.task.getId(),trust.tenant);
      if(actual.size()!=1||!actual.get(0).get("rev_").toString().equals(taskRevision)||!Objects.equals(actual.get(0).get("assignee_"),assignee)||((Number)actual.get(0).get("suspension_state_")).intValue()!=1)throw conflict();state.current();envelope.requireCurrent(Instant.now().getEpochSecond());
    });return bytes;
  }
}
