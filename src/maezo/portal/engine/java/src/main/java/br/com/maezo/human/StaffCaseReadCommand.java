package br.com.maezo.human;

import static br.com.maezo.human.PortalReadModels.*;
import java.time.Instant;
import java.util.*;
import org.cibseven.bpm.engine.impl.interceptor.*;
import org.cibseven.bpm.engine.impl.cfg.TransactionState;
import java.sql.Timestamp;

/** SC1 same-command Q2 intersection used by the staff detail/native finalizer.
 * Construction checkpoint: outer durable staff read/publication composition is
 * separate below; this helper never emits a public response or a Q2 capability.
 */
public final class StaffCaseReadCommand implements Command<StaffCaseReadCommand.Result> {
  public static final class Result {
    byte[] frozen;boolean committed;Runnable current;
    public byte[] bytes(){if(!committed||frozen==null)throw unavailable();current.run();return frozen.clone();}
  }
  final StaffCaseInstallation.Configuration config;final byte[] raw;final String peer;final Q2Lease lease;
  StaffCaseReadCommand(StaffCaseInstallation.Configuration config,byte[] raw,String peer,Q2Lease lease){
    if(config==null||raw==null||raw.length>MAX||lease==null)throw invalid();
    this.config=config;this.raw=raw.clone();this.peer=peer;this.lease=lease;
  }
  @Override public Result execute(CommandContext context){
    // Parsing a principal is not authority. It is used only inside the same native
    // Q2 admission; no row is emitted until the independently signed request passes.
    var parsed=StaffCaseInstallation.canonical(raw);
    var principal=StaffCaseModels.shape("principal",parsed.get("principal"));
    var q2=lease.enter(context,principal,()->lease.admission.requireCurrent());
    var store=new StaffCaseStore(context,config.authScope(),lease.admission.statementTimeoutSeconds(),config.nativeRole(),config.relationPins());
    store.auth.lock();var installed=new StaffCaseInstallation(config,store,lease.admission);
    var request=installed.signedRead(raw,peer);
    Map<String,Object> original=request,retained=null;
    String continuityRef;
    if("finalize".equals(request.get("operation"))){
      var query=obj(request,"query");if(!list(query.get("subordinate_continuities")).isEmpty())throw invalid();
      continuityRef=str(query,"continuity_ref");retained=store.continuity(continuityRef);
      Jcs.keys(retained,"schema","scope","request","projection","semantic","source_pins","observed_at","valid_until","q2_ceilings");
      if(!"staff-case-continuity.v1".equals(retained.get("schema"))||!store.scope.equals(retained.get("scope"))
          ||!query.get("frozen_projection_digest").equals(hash(retained.get("projection"))))throw conflict();
      original=obj(retained,"request");
      if(!Set.of("detail","list").contains(original.get("operation"))||!principal.equals(original.get("principal"))
          ||!request.get("membership_witness").equals(original.get("membership_witness")))throw conflict();
      StaffCaseModels.requireReadCapabilities(installed.entries.get(str(request,"key_fingerprint")),str(original,"operation"));
      // A newly admitted signer cannot replace an original retained source key.
      for(Object value:list(retained.get("source_pins"))){var pin=StaffCaseModels.shape("readpin",value);
        if("source_key".equals(pin.get("kind"))){var entry=installed.entries.get(str(pin,"ref"));
          if(entry==null||!hash(entry).equals(pin.get("digest"))||store.revoked(str(pin,"ref")))throw denied();
          installed.fresh(entry,"not_before","valid_until");installed.usedKeys.add(str(pin,"ref"));}}
      installed.retain(time(retained.get("valid_until")));
      for(Object ceiling:list(retained.get("q2_ceilings")))installed.retain(time(map(ceiling).get("valid_until")));
    }else continuityRef=UUID.randomUUID().toString();
    var initial=collect(store,installed,q2,original);
    Instant observed=retained==null?installed.current():time(retained.get("observed_at"));
    Instant until=installed.until().isBefore(q2.read.until())?installed.until():q2.read.until();
    installed.retain(until);
    var projection=projection(initial,observed,until);
    List<Object> pins=readPins(store,installed,original,initial,until);
    Map<String,Object> continuity;
    if(retained==null){
      continuity=record("schema","staff-case-continuity.v1","scope",store.scope,"request",copy(original),
        "projection",projection,"semantic",initial,"source_pins",pins,"observed_at",time(observed),
        "valid_until",time(until),"q2_ceilings",q2.read.ceilings.stream().map(PortalReadModels::copy).toList());
      store.continuity(continuityRef,continuity);
    }else{
      if(!hash(initial).equals(hash(retained.get("semantic"))))throw conflict();
      // The original bytes and original ceiling are retained. A new qualified read
      // proves currentness; it does not rewrite an issued snapshot or renew it.
      continuity=retained;projection=obj(retained,"projection");pins=list(retained.get("source_pins"));
      until=time(retained.get("valid_until"));installed.retain(until);
    }
    var observation=record("schema","staff-case-observation.v1","request_digest",hash(request),"projection",projection,
      "continuity_ref",continuityRef,"continuity_digest",hash(continuity),"source_pins",pins,
      "observed_at",time(observed),"valid_until",time(until));
    observation.put("proof",installed.resultProof(observation));
    var out=new Result();out.frozen=bounded(observation);out.current=()->{installed.current();q2.current();};
    var originalRead=original;String expected=hash(initial);
    context.getTransactionContext().addTransactionListener(TransactionState.COMMITTING,ignored->{
      out.current.run();q2.finalReads();
      var currentQ2=lease.enter(context,principal,installed::current);
      if(!expected.equals(hash(collect(store,installed,currentQ2,originalRead))))throw conflict();
      currentQ2.finalReads();installed.finalDesignation();out.current.run();
    });
    context.getTransactionContext().addTransactionListener(TransactionState.COMMITTED,ignored->out.committed=true);
    out.current.run();return out;
  }
  static Map<String,Object> collect(StaffCaseStore store,StaffCaseInstallation installed,Q2Intersection q2,Map<String,Object> request){
    if("list".equals(request.get("operation")))return collectList(store,installed,q2,request);
    return collectDetail(store,installed,q2,request);
  }
  static Map<String,Object> collectDetail(StaffCaseStore store,StaffCaseInstallation installed,Q2Intersection q2,Map<String,Object> request){
    var query=StaffCaseModels.shape("detail",request.get("query"));
    if(query.get("task_cursor")!=null)throw unavailable(); // Required checkpoint provider is the next product slice.
    var principal=obj(request,"principal");String caseRef=str(query,"case_ref");
    var member=installed.membership(q2.read,obj(request,"membership_witness"),principal);
    var peek=store.peekGrant(caseRef,principal);if(peek==null)throw new Rejected(404,"case_unavailable");
    var publication=store.publication(str(peek,"publication_id"));if(publication==null)throw unavailable();
    var grant=obj(publication,"payload");var policies=store.policyPins(grant);
    var locked=store.exactGrant(caseRef,principal);
    if(locked==null||!peek.equals(locked))throw conflict();
    store.requireRecordedPolicies(locked,policies);
    var identityState=new NativeCaseIdentityReader(store.auth).read(caseRef);var identity=obj(identityState,"identity");
    var fields=installed.grant(publication,principal,identity,policies);
    var event=new StaffCaseEventStore(store.auth).read(caseRef);
    if(!identity.get("process_instance_ref").equals(event.get("process_instance_id")))throw unavailable();
    var tasks=new ArrayList<Object>();var created=new ArrayList<Object>();
    var required=new HashSet<>(StaffCaseModels.FIELDS.get("staff_current_task.v1"));required.remove("created_at");
    boolean taskFields=required.equals(fields.get("staff_current_task.v1"));
    // Exhaustive actual native task scan. The admitted ten-second ceiling bounds
    // work; overflow is unavailable, never a fabricated complete or cursor page.
    store.taskRows(identity,row->{
      installed.current();q2.current();
      if(!taskFields)return true;
      String id=str(row,"id_");var task=q2.task(id);if(task==null)return true;
      var resource=StaffCaseModels.shape("task_resource",record("scope",store.scope,"case_ref",caseRef,
        "process_instance_id",identity.get("process_instance_ref"),"task_id",id,"task_definition_key",row.get("task_def_key_")));
      boolean allowed=false;String digest=hash(resource);
      for(Object value:list(grant.get("decisions"))){var d=map(value);
        if("staff_current_task.v1".equals(d.get("projection"))&&digest.equals(d.get("resource_identity_digest"))
            &&list(d.get("fields")).contains("created_at")){allowed=true;break;}}
      if(!allowed)return true;
      if(!store.scope.get("tenant").equals(row.get("tenant_id_"))||!identity.get("process_instance_ref").equals(row.get("proc_inst_id_"))
          ||!identity.get("process_definition_id").equals(row.get("proc_def_id_"))||!task.get("task_definition_key").equals(row.get("task_def_key_"))
          ||!(row.get("rev_") instanceof Number)||!task.get("task_revision").equals(Long.toString(((Number)row.get("rev_")).longValue()))
          ||!(row.get("create_time_") instanceof Timestamp))throw unavailable();
      var creation=StaffCaseModels.shape("task_created",record("resource",resource,"task_revision",task.get("task_revision"),
        "created_at",time(((Timestamp)row.get("create_time_")).toInstant())));
      var projected=copy(task);projected.put("created_at",creation.get("created_at"));tasks.add(projected);created.add(creation);
      if(tasks.size()>number(query.get("task_limit")))throw unavailable();return true;
    });
    if("ended".equals(obj(identityState,"native").get("state"))&&!tasks.isEmpty())throw unavailable();
    q2.current();installed.current();
    return record("operation","detail","identity",identityState,"membership",member,"publication",publication,"policies",policies,
      "event",event,"tasks",tasks,"created",created,"q2",q2.semantic());
  }
  static Map<String,Object> collectList(StaffCaseStore store,StaffCaseInstallation installed,Q2Intersection q2,Map<String,Object> request){
    var query=StaffCaseModels.shape("list",request.get("query"));var principal=obj(request,"principal"),witness=obj(request,"membership_witness");
    var member=installed.membership(q2.read,witness,principal);var accepted=store.activeCheckpoint(principal);if(accepted==null)throw unavailable();
    var checkpoint=StaffCaseModels.shape("scope_checkpoint",AuthStore.parse(accepted.get("canonical_checkpoint")));
    if(!hash(checkpoint).equals(accepted.get("checkpoint_digest"))||!hash(StaffCaseModels.actor(principal)).equals(checkpoint.get("principal_identity_digest"))
        ||!principal.get("session_ref").equals(witness.get("session_ref"))||!list(checkpoint.get("operations")).contains("list"))throw unavailable();
    installed.fresh(checkpoint,"observed_at","valid_until");installed.proof(obj(checkpoint,"proof"),withoutProof(checkpoint,"proof"),
      "case_issuer","scope_complete",str(accepted,"source_ref"),null);
    var scopePolicy=store.policyHead(str(checkpoint,"policy_scope_ref"));if(scopePolicy==null)throw unavailable();var scopeHead=AuthStore.parse(scopePolicy.get("canonical_head"));
    if(!"active".equals(scopeHead.get("state"))||!checkpoint.get("policy_scope_revision").equals(scopeHead.get("policy_revision"))
        ||!checkpoint.get("policy_scope_digest").equals(scopeHead.get("policy_digest")))throw conflict();installed.policy(scopeHead);
    String queryDigest=hash(record("kind",query.get("kind"),"limit",query.get("limit")));String after=null;
    if(query.get("cursor")!=null){var cursor=store.cursor(str(query,"cursor"));
      if(!store.scope.equals(cursor.get("scope"))||!hash(StaffCaseModels.actor(principal)).equals(cursor.get("principal_identity_digest"))
          ||!principal.get("membership_revision").equals(cursor.get("membership_revision"))||!principal.get("session_ref").equals(cursor.get("session_ref"))
          ||!queryDigest.equals(cursor.get("query_digest"))||!accepted.get("checkpoint_digest").equals(cursor.get("checkpoint_digest"))
          ||!accepted.get("checkpoint_ref").equals(cursor.get("checkpoint_ref"))||!query.get("limit").equals(cursor.get("limit")))throw conflict();
      installed.retain(time(cursor.get("initial_valid_until")));after=str(cursor,"after_ref");}
    var items=new ArrayList<Object>();var pins=new ArrayList<Object>();String last=null;int limit=(int)number(query.get("limit"));
    for(var entry:store.checkpointEntries(accepted)){String caseRef=str(entry,"case_ref");if(after!=null&&caseRef.compareTo(after)<=0)continue;
      var row=store.exactGrant(caseRef,principal);if(row==null||!entry.get("grant_ref").equals(row.get("grant_ref")))throw unavailable();
      var publication=store.publication(str(row,"publication_id"));if(publication==null)throw unavailable();var grant=obj(publication,"payload"),policies=store.policyPins(grant);
      store.requireRecordedPolicies(row,policies);var identityState=new NativeCaseIdentityReader(store.auth).read(caseRef);var identity=obj(identityState,"identity");
      installed.grant(publication,principal,identity,policies,"list");var event=new StaffCaseEventStore(store.auth).read(caseRef);
      if(!identity.get("process_instance_ref").equals(event.get("process_instance_id")))throw unavailable();
      if(items.size()<limit){items.add(record("case_ref",caseRef,"kind","authorization","state",obj(identityState,"native").get("state"),
          "record_revision",event.get("revision"),"state_observed_at",event.get("observed_at")));last=caseRef;
        pins.add(pin("case_grant",caseRef,grant.get("grant_revision"),hash(grant),installed.until()));
        pins.add(pin("native_case",caseRef,event.get("revision"),hash(event),installed.until()));
      }else{last=last==null?caseRef:last;break;}}
    boolean more=false;if(last!=null){for(var entry:store.checkpointEntries(accepted))if(str(entry,"case_ref").compareTo(last)>0){more=true;break;}}
    pins.add(0,pin("checkpoint",str(checkpoint,"checkpoint_ref"),checkpoint.get("generation"),hash(checkpoint),installed.until()));
    pins.add(0,pin("membership",str(obj(witness,"actor"),"principal_ref"),obj(witness,"actor").get("membership_revision"),hash(witness),installed.until()));
    pins.add(0,pin("designation",str(installed.designation,"designation_ref"),installed.designation.get("designation_revision"),hash(installed.designation),installed.until()));
    for(String fingerprint:new TreeSet<>(installed.usedKeys))pins.add(pin("source_key",fingerprint,installed.designation.get("designation_revision"),hash(installed.entries.get(fingerprint)),installed.until()));
    String next=null;if(more&&last!=null){var value=record("cursor_ref",UUID.randomUUID().toString(),"scope",store.scope,
        "principal_identity_digest",hash(StaffCaseModels.actor(principal)),"membership_revision",principal.get("membership_revision"),"session_ref",principal.get("session_ref"),
        "operation","list","query_digest",queryDigest,"kind","authorization","checkpoint_ref",checkpoint.get("checkpoint_ref"),"generation",checkpoint.get("generation"),
        "checkpoint_digest",hash(checkpoint),"case_ref",null,"native_revision",null,"after_ref",last,"limit",query.get("limit"),"source_pins",pins,
        "initial_valid_until",time(installed.until()));next=str(store.pageCursor(value),"cursor_ref");}
    q2.current();installed.current();return record("operation","list","membership",member,"checkpoint",checkpoint,"accepted",copy(accepted),
      "items",items,"next_cursor",next,"pins",pins,"q2",q2.semantic());
  }
  static Map<String,Object> projection(Map<String,Object> state,Instant observed,Instant until){
    if("list".equals(state.get("operation"))){var checkpoint=obj(state,"checkpoint");return record("schema","portal-staff-case-page.v1",
      "items",state.get("items"),"next_cursor",state.get("next_cursor"),"freshness",record("observed_at",time(observed),
      "source_observed_at",checkpoint.get("observed_at"),"valid_until",time(until),"refresh_after_seconds","10"));}
    var identityState=obj(state,"identity");var identity=obj(identityState,"identity");var event=obj(state,"event");
    return record("schema","portal-staff-case-detail.v1","case",record("case_ref",identity.get("case_ref"),"kind","authorization",
      "state",obj(identityState,"native").get("state"),"record_revision",event.get("revision"),"state_observed_at",event.get("observed_at")),
      "identity",identity,"active_tasks",state.get("tasks"),"next_task_cursor",null,"tasks_complete",true,
      "freshness",record("observed_at",time(observed),"source_observed_at",event.get("observed_at"),"valid_until",time(until),"refresh_after_seconds","10"));
  }
  static List<Object> readPins(StaffCaseStore store,StaffCaseInstallation installed,Map<String,Object> request,Map<String,Object> state,Instant until){
    if("list".equals(state.get("operation")))return new ArrayList<>(list(state.get("pins")));
    var pins=new ArrayList<Object>();var designation=installed.designation;var identity=obj(obj(state,"identity"),"identity");
    var witness=obj(request,"membership_witness");var grant=obj(obj(state,"publication"),"payload");var event=obj(state,"event");
    pins.add(pin("designation",str(designation,"designation_ref"),designation.get("designation_revision"),hash(designation),until));
    pins.add(pin("identity",str(identity,"case_ref"),identity.get("process_definition_version"),hash(identity),until));
    pins.add(pin("membership",str(obj(witness,"actor"),"principal_ref"),obj(witness,"actor").get("membership_revision"),hash(witness),until));
    pins.add(pin("case_grant",str(grant,"grant_ref"),grant.get("grant_revision"),hash(grant),until));
    pins.add(pin("native_case",str(identity,"case_ref"),event.get("revision"),hash(event),until));
    for(Object value:list(state.get("policies"))){var p=map(value);var closed=copy(p);closed.remove("head");
      pins.add(pin("policy_head",str(p,"policy_ref"),p.get("head_revision"),hash(closed),until));}
    for(Object value:list(state.get("created"))){var p=map(value);pins.add(pin("native_task_created_at",str(obj(p,"resource"),"task_id"),p.get("task_revision"),hash(p),until));}
    for(Object value:list(state.get("tasks"))){var p=map(value);pins.add(pin("native_task",str(p,"task_id"),p.get("task_revision"),hash(p),until));}
    for(String fingerprint:new TreeSet<>(installed.usedKeys))
      pins.add(pin("source_key",fingerprint,designation.get("designation_revision"),hash(installed.entries.get(fingerprint)),until));
    return pins;
  }
  static Map<String,Object> pin(String kind,String ref,Object revision,String digest,Instant until){
    return StaffCaseModels.shape("readpin",record("kind",kind,"ref",ref,"revision",revision,"digest",digest,"valid_until",time(until)));
  }

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
    Map<String,Object> semantic() {
      var retained=new ArrayList<Object>();
      for(Object item:observations){var observation=copy(map(item));
        if(observation.containsKey("disclosure")){var d=copy(obj(observation,"disclosure"));d.remove("valid_until");observation.put("disclosure",d);}
        retained.add(observation);
      }
      return record("principal",copy(principal),"anchor",copy(anchor),"observations",retained);
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
