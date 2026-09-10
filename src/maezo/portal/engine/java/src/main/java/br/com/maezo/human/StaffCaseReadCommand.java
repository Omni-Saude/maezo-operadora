package br.com.maezo.human;

import static br.com.maezo.human.PortalReadModels.*;
import java.time.Instant;
import java.util.*;
import org.cibseven.bpm.engine.impl.interceptor.CommandContext;

/** SC1 same-command Q2 intersection used by the staff detail/native finalizer.
 * Construction checkpoint: outer durable staff read/publication composition is
 * separate below; this helper never emits a public response or a Q2 capability.
 */
public final class StaffCaseReadCommand {
  private StaffCaseReadCommand() {}

  /** Acquired by the actual PortalReadPlugin before entering the engine command.
   * The same admission is retained; no acquisition/renewal happens below staff locks.
   */
  static final class Q2Lease {
    final PortalReadTrust trust;
    final PortalReadTrust.Admission admission;
    final Map<String,Object> anchor;
    Q2Lease(PortalReadTrust trust,PortalReadTrust.Admission admission,Map<String,Object> scope,
        Map<String,Object> anchor) {
      if(trust==null||admission==null)throw unavailable();
      ExternalCaseModels.shape("scope",scope);validate("anchor",anchor);
      if(!trust.scope.get("tenant").equals(scope.get("tenant"))
          ||!trust.scope.get("environment").equals(scope.get("environment"))
          ||!trust.engine.equals(scope.get("engine_name"))||!trust.incarnation.equals(scope.get("database_incarnation"))
          ||!trust.scope.equals(anchor.get("scope")))throw denied();
      admission.requireCurrent();this.trust=trust;this.admission=admission;this.anchor=copy(anchor);
    }
    Q2Intersection enter(CommandContext context,Map<String,Object> principal,Runnable outerCurrent) {
      return new Q2Intersection(context,this,principal,outerCurrent);
    }
  }

  static final class Q2Intersection {
    final PortalReadCommand read;
    final Map<String,Object> principal,anchor;
    final List<Object> observations=new ArrayList<>();
    Q2Intersection(CommandContext context,Q2Lease lease,Map<String,Object> principal,Runnable outerCurrent) {
      this.principal=copy(validate("principal",principal));this.anchor=copy(lease.anchor);
      read=new PortalReadCommand(lease.trust,lease.admission,Objects.requireNonNull(outerCurrent));
      read.context=context;read.db=new PortalReadStore(context,lease.trust,lease.admission.statementTimeoutSeconds());
      read.guard();read.ceiling("native_admission",lease.admission.providerRef(),lease.admission.providerRevision(),
        lease.admission.capabilityDigest(),lease.admission.observedAt(),lease.admission.validUntil());
      // This MUST execute before designation/source/claim/event-head locks.
      read.db.lockTenant();
      var catalog=read.catalog(anchor);String catalogDigest=hash(read.designation(catalog));
      read.finalReads.add(()->{if(!hash(read.designation(read.catalog(anchor))).equals(catalogDigest))throw conflict();});
    }

    /** Only existing Q2-approved fields are returned. Native creation time is NOT
     * silently added to full_task_detail.v1; the outer staff projection must carry
     * its separately qualified exact field policy and current native-row pin.
     */
    Map<String,Object> task(String taskId) {
      PortalReadCommand.State state;
      try { state=read.state(taskId,anchor,str(principal,"principal_ref")); }
      catch(Rejected rejection) {
        // A missing complete resource publication is dependency failure in state().
        // True absent/suspended native task needs the outer exact task-set check.
        if(rejection.status==404)return null;throw rejection;
      }
      read.retain(state,anchor,principal);
      Map<String,Object> authorityState;
      try { authorityState=read.authorityState(state,principal); }
      catch(Rejected rejection) {
        if(rejection.status!=404)throw rejection;
        String nativeDigest=hash(state.nativeState());
        observations.add(record("task_state_digest",nativeDigest,"visible",false));
        read.finalReads.add(()->{
          var latest=read.state(taskId,anchor,str(principal,"principal_ref"));
          try {read.authorityState(latest,principal);}
          catch(Rejected current){if(current.status==404)return;throw current;}
          throw conflict();
        });
        return null;
      }
      String authorityDigest=hash(authorityState);
      read.finalReads.add(()->{
        var latest=read.state(taskId,anchor,str(principal,"principal_ref"));
        if(!hash(read.authorityState(latest,principal)).equals(authorityDigest))throw conflict();
      });
      Instant observed=read.guard(),until=read.until();
      var task=read.task(state,observed,until);
      var authority=read.authority(state,principal,authorityState,until);
      var classification=obj(state.resource(),"classification");
      var disclosure=record("scope",read.trust.scope,"principal",principal,"snapshot",task.get("snapshot"),
        "authority_revision",authority.get("authority_revision"),"classification_ref",classification.get("classification_ref"),
        "classification_digest",classification.get("classification_digest"),"projection","full_task_detail.v1","valid_until",time(until));
      // Staff-owned continuity retains complete subordinate observations, not an
      // invented Q2 token from the internal constructor's null envelope/keys.
      observations.add(record("native_task",copy(state.nativeState()),"catalog",read.designation(state.catalog()),
        "authority_state",authorityState,"disclosure",disclosure,"visible",true));
      read.guard();
      return record("task_id",state.row().get("task_id"),"task_definition_key",state.entry().get("task_definition_key"),
        "task_revision",state.row().get("task_revision"),"assignee_ref",state.row().get("assignee_ref"),
        "due_at",state.row().get("engine_due_at"));
    }
    Map<String,Object> continuity() {
      read.guard();
      return record("principal_digest",hash(principal),"anchor",copy(anchor),"observations",new ArrayList<>(observations),
        "ceilings",read.ceilings.stream().map(PortalReadModels::copy).toList(),"valid_until",time(read.until()));
    }
    /** Caller runs at COMMITTING and again in the staff native-finalize read. */
    void finalReads() {
      read.guard();for(Runnable check:List.copyOf(read.finalReads))check.run();read.guard();
    }
    void current(){read.guard();}
  }
}
