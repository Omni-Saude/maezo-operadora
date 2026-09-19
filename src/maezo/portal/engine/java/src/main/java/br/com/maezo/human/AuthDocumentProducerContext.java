package br.com.maezo.human;

import java.time.Instant;
import java.nio.charset.StandardCharsets;
import br.com.maezo.workload.Json;
import java.util.*;
import org.cibseven.bpm.engine.impl.interceptor.CommandContext;

/** Installed AUTH occurrence projection for a separately designated nonhuman reader. */
public final class AuthDocumentProducerContext {
  private final AuthRuntime runtime;
  AuthDocumentProducerContext(AuthRuntime runtime) { this.runtime=Objects.requireNonNull(runtime); }
  public static final Map<String,String> PROJECTION=Map.ofEntries(
    Map.entry("scope","Scope"),Map.entry("definition","Definition"),Map.entry("case_ref","Ref"),
    Map.entry("request_ref","Ref"),Map.entry("generation","PositiveDecimalString"),
    Map.entry("request_revision","NonnegativeDecimalString"),Map.entry("binding_revision","NonnegativeDecimalString"),
    Map.entry("process_instance_id","Ref"),Map.entry("creator_execution_id","Ref"),Map.entry("producer_external_task_id","Ref"),
    Map.entry("occurrence_state","created|awaiting_publication_worker|bound"),Map.entry("created_at","Time"),
    Map.entry("policy_ref","Ref|null"),Map.entry("policy_digest","Hash|null"));
  public static String projectionDigest(){return Jcs.digest(Jcs.canonical(PROJECTION));}

  /** Called after native F/G/A/R guard, before process/task/acquisition locks. */
  public Lease open(CommandContext context,String designationDigest) {
    return new Lease(context,designationDigest);
  }
  public final class Lease {
    private final CommandContext context;
    private final AuthStore store;
    private final Map<String,Object> designation,qualification;
    private final String designationDigest;
    private final Instant deadline;
    private Map<String,Object> captured;
    Lease(CommandContext context,String hash) {
      this.context=context;this.designationDigest=hash;
      if(!hash.matches("[0-9a-f]{64}"))throw Rejected.invalid();
      store=new AuthStore(context,runtime.scope,runtime.timeout);store.lock();
      qualification=AuthInstallation.runtime(store);
      var row=store.one("SELECT RECORD_ FROM MZO_AUTH_PRODUCER_DESIGNATION WHERE TENANT_=? AND DIGEST_=? FOR SHARE",store.tenant,hash);
      designation=AuthDocumentProducerContext.designation(nativeRecord(row.get("record_")));
      if(!Json.digest(designation).equals(hash)||!runtime.scope.equals(designation.get("scope")))throw Rejected.denied();
      var privileges=store.one("SELECT has_table_privilege(current_user,'mzo_auth_producer_designation','SELECT') AS readable, "
        +"(has_table_privilege(current_user,'mzo_auth_producer_designation','INSERT,UPDATE,DELETE,TRUNCATE,TRIGGER') "
        +"OR has_any_column_privilege(current_user,'mzo_auth_producer_designation','INSERT,UPDATE,REFERENCES')) AS mutable");
      if(!Boolean.TRUE.equals(privileges.get("readable"))||Boolean.TRUE.equals(privileges.get("mutable")))throw Rejected.denied();
      deadline=Collections.min(List.of(PortalReadModels.time(designation.get("valid_until")),
        PortalReadModels.time(Jcs.object(designation.get("source")).get("valid_until")),
        PortalReadModels.time(qualification.get("valid_until"))));
      currentLocal();
    }
    public Map<String,Object> designation(){return Json.parse(Json.bytes(designation));}
    public long validUntil(){return deadline.toEpochMilli();}
    public Map<String,Object> read(String taskId,String processId,String executionId,String definitionId) {
      var task=context.getExternalTaskManager().findExternalTaskById(taskId);
      if(task==null||!taskId.equals(task.getId())||!processId.equals(task.getProcessInstanceId())
        ||!executionId.equals(task.getExecutionId())||!definitionId.equals(task.getProcessDefinitionId())
        ||!"ST_SolicitarDocumentos".equals(task.getActivityId())||!store.tenant.equals(task.getTenantId()))throw Rejected.denied();
      var link=store.instanceLink(processId);var row=store.producerOccurrence(processId,taskId);
      if(link==null||row==null)throw Rejected.denied();
      var occurrence=AuthModels.validate("occurrence",AuthStore.parse(row.get("record_")));
      if(!Set.of("created","awaiting_publication_worker","bound").contains(occurrence.get("state"))
        ||!runtime.scope.equals(occurrence.get("scope"))||!processId.equals(occurrence.get("process_instance_id"))
        ||!taskId.equals(occurrence.get("producer_external_task_id"))||!executionId.equals(occurrence.get("creator_execution_id"))
        ||!link.get("case_").equals(occurrence.get("case_ref"))||!AuthStore.parse(link.get("definition_")).equals(occurrence.get("definition"))
        ||!qualification.get("definition").equals(occurrence.get("definition"))
        ||!definitionId.equals(Jcs.object(occurrence.get("definition")).get("definition_id")))throw Rejected.denied();
      Map<String,Object> projection=new TreeMap<>();
      for(String key:PROJECTION.keySet())projection.put(key,key.equals("occurrence_state")?occurrence.get("state"):occurrence.get(key));
      if(captured!=null&&!captured.equals(projection))throw Rejected.conflict();
      captured=PortalReadModels.copy(projection);currentLocal();return PortalReadModels.copy(captured);
    }
    public void audit(String queryId,String queryDigest,String acquisitionRef) {
      store.write("INSERT INTO MZO_AUTH_PRODUCER_QUERY(TENANT_,QUERY_,QUERY_DIGEST_,DESIGNATION_DIGEST_,ACQUISITION_) VALUES(?,?,?,?,?)",
        store.tenant,queryId,queryDigest,designationDigest,acquisitionRef);
    }
    public void currentRows() {
      var current=store.one("SELECT RECORD_ FROM MZO_AUTH_PRODUCER_DESIGNATION WHERE TENANT_=? AND DIGEST_=?",store.tenant,designationDigest);
      if(!designation.equals(nativeRecord(current.get("record_")))||!qualification.equals(AuthInstallation.runtime(store)))throw Rejected.denied();
    }
    /** Final no-SQL guard follows every native/source SQL and artifact read. */
    public void currentLocal() {
      runtime.requireQualifiedCode(qualification);
      Instant now=Instant.now();
      if(!"active".equals(designation.get("state"))||now.isBefore(PortalReadModels.time(designation.get("valid_from")))
        ||!now.isBefore(deadline))throw Rejected.denied();
    }
  }
  private static Map<String,Object> nativeRecord(Object raw) {
    if(!(raw instanceof String value))throw Rejected.invalid();
    return Json.parse(value.getBytes(StandardCharsets.UTF_8));
  }
  static Map<String,Object> designation(Map<String,Object> d) {
    Jcs.keys(d,"schema","designation_ref","revision","identity","native_user","scope","fetch_capability_digest","target",
      "activity_id","action","projection_digest","source","valid_from","valid_until","state");
    if(!"auth-document-producer-designation.v1".equals(d.get("schema"))||!"ST_SolicitarDocumentos".equals(d.get("activity_id"))
      ||!"auth.document_request.observe".equals(d.get("action"))||!projectionDigest().equals(d.get("projection_digest"))
      ||!Set.of("active","revoked").contains(d.get("state"))||PortalReadModels.number(d.get("revision"))<1)throw Rejected.invalid();
    Jcs.ref(d,"designation_ref");Jcs.ref(d,"native_user");Jcs.hash(d,"fetch_capability_digest");
    AuthModels.validate("scope",d.get("scope"));AuthModels.validate("source",d.get("source"));
    var identity=Json.object(d.get("identity"));
    Json.keys(identity,"tenant","environment","workload","workload_version","issuer","subject","origin");
    for(String key:identity.keySet())Json.token(identity,key);
    if(!"verified_mtls".equals(identity.get("origin")))throw Rejected.invalid();
    var scope=Jcs.object(d.get("scope"));
    if(!identity.get("tenant").equals(scope.get("tenant"))||!identity.get("environment").equals(scope.get("environment")))throw Rejected.invalid();
    Json.token(d,"native_user");
    var target=Json.object(d.get("target"));
    Json.keys(target,"process_key","process_version","definition_id","topic","message");
    Json.token(target,"definition_id");
    if(!"SP-OP-AUTH-001".equals(target.get("process_key"))||!"operadora.auth.request_documents".equals(target.get("topic"))
      ||!"".equals(target.get("message"))||Json.number(target,"process_version")<1)throw Rejected.invalid();
    PortalReadModels.time(d.get("valid_from"));PortalReadModels.time(d.get("valid_until"));
    return Json.parse(Json.bytes(d));
  }
}
