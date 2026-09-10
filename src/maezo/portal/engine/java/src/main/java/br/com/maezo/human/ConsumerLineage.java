package br.com.maezo.human;

import java.nio.charset.StandardCharsets;
import java.util.*;
import org.cibseven.bpm.engine.impl.context.Context;
import org.cibseven.bpm.engine.impl.interceptor.CommandContext;
import org.cibseven.bpm.engine.impl.persistence.entity.ExecutionEntity;
import org.cibseven.bpm.engine.impl.persistence.entity.ExternalTaskEntity;

/** P2 enlisted original provenance. No business-key/variable-based ancestry or PHI access. */
public final class ConsumerLineage {
  private ConsumerLineage() {}
  static final Map<String,String> LEGACY_SOURCES = Map.of(
      "SP-OP-AUTH-001/UT_AnaliseMedicoAuditor", "auth_decisao",
      "SP-OP-AUTH-001/UT_CoordenacaoAssume", "auth_decisao",
      "SP-OP-AUTH-001/UT_RegistrarParecerJunta", "auth_junta",
      "SP-OP-PAGTO-001/UT_AnaliseAdmissibilidade", "pagto_admissibilidade",
      "SP-OP-ESCALATION-001/UT_TratarEscalonamento", "escalation",
      "SP-OP-ESCALATION-001/UT_SupervisorAssume", "escalation");

  static final Map<String,String> SOURCES;
  static {
    var sources=new TreeMap<>(LEGACY_SOURCES);
    sources.put("SP-OP-AUTH-001/UT_DecidirPendenciaExpirada","auth_pendencia");
    SOURCES=Collections.unmodifiableMap(sources);
  }

  static void require(boolean value) { if (!value) throw EngineStore.unavailable(); }
  static String text(byte[] bytes) { return new String(bytes, StandardCharsets.UTF_8); }
  static Map<String,Object> object(byte[] bytes) {
    require(bytes.length <= 65536);
    var result=Jcs.object(Jcs.parse(bytes));
    require(Arrays.equals(bytes,Jcs.canonical(result)));
    depth(result,0);
    return Collections.unmodifiableMap(result);
  }
  private static void depth(Object value,int level) {
    require(level<=16);
    if(value instanceof Map<?,?> map)for(Object child:map.values())depth(child,level+1);
    else if(value instanceof List<?> list)for(Object child:list)depth(child,level+1);
  }
  static String ref(Map<String,Object> row,String field) {
    String value=Jcs.string(row,field);
    require(!value.isEmpty() && value.getBytes(StandardCharsets.UTF_8).length<=255
        && value.codePoints().noneMatch(c->c<32 || (c>=127 && c<=159)));
    return value;
  }
  static long decimal(Map<String,Object> row,String field,boolean positive) {
    String value=Jcs.string(row,field);
    require(value.matches("0|[1-9][0-9]*"));
    try {long number=Long.parseLong(value);require(!positive || number>0);return number;}
    catch(NumberFormatException ex){throw EngineStore.unavailable();}
  }
  public static Map<String,Object> scope(Map<String,Object> input) {
    Jcs.keys(input,"tenant","environment","engine_name","database_incarnation","database_binding_digest");
    for(String name:List.of("tenant","environment","engine_name","database_incarnation"))ref(input,name);
    Jcs.hash(input,"database_binding_digest");
    return object(Jcs.canonical(input));
  }
  public static String scopeHash(Map<String,Object> input) {
    return Jcs.digest(Jcs.canonical(Map.of("schema","phi-consumer-scope-hash.v1","value",scope(input))));
  }
  public static Map<String,Object> link(Map<String,Object> input) {
    Jcs.keys(input,"scope_digest","external_task_id","original_task_id","original_task_key","command_ref",
        "principal_ref","decision_workload_ref","process_definition_id","process_instance_id","execution_id",
        "activity_id","binding_digest","classified_command_digest");
    for(String name:List.of("scope_digest","binding_digest","classified_command_digest"))Jcs.hash(input,name);
    for(String name:List.of("external_task_id","original_task_id","original_task_key","command_ref","principal_ref",
        "decision_workload_ref","process_definition_id","process_instance_id","execution_id","activity_id"))ref(input,name);
    return object(Jcs.canonical(input));
  }
  public static String linkHash(Map<String,Object> input) {
    return Jcs.digest(Jcs.canonical(Map.of("schema","phi-consumer-link-hash.v1","value",link(input))));
  }

  /** Called only by AtomicHumanCommand after original native admission, before completion. */
  static ConsumerEdgeInstallation.Installed decision(CommandContext context,HumanCommand command,String classifiedDigest,
      org.cibseven.bpm.engine.impl.persistence.entity.TaskEntity task) {
    require(context==Context.getCommandContext() && command.classified()!=null
        && "decision".equals(command.operation()));
    var qualified=ConsumerEdgeInstallation.current(context,command.tenant());
    var s=qualified.scope();
    require(command.classified().environment().equals(s.get("environment")));
    Map<String,Object> target=qualified.target(command.processId(),command.taskKey());
    require(command.classified().bindingDigest().equals(target.get("binding_digest"))
        && command.processDigest().equals(target.get("process_digest")));
    ExecutionEntity execution=task.getExecution();
    require(execution!=null && execution.getProcessDefinitionId().equals(command.processId())
        && task.getProcessInstanceId().equals(execution.getProcessInstanceId()));
    Map<String,Object> pointer=new TreeMap<>();
    pointer.put("scope_digest",scopeHash(s));pointer.put("original_task_id",command.taskId());
    pointer.put("original_task_key",command.taskKey());pointer.put("command_ref",command.commandId());
    pointer.put("principal_ref",command.principalRef());pointer.put("decision_workload_ref",command.workloadRef());
    pointer.put("process_definition_id",command.processId());pointer.put("process_instance_id",execution.getProcessInstanceId());
    pointer.put("execution_id",execution.getId());pointer.put("binding_digest",command.classified().bindingDigest());
    pointer.put("classified_command_digest",classifiedDigest);pointer.put("outcome",command.classified().outcome());
    var db=new EngineStore(context,command.tenant());
    require(db.update("INSERT INTO MZO_HUMAN_CONSUMER_POINTER(TENANT_,SCOPE_DIGEST_,TASK_,COMMAND_,EXECUTION_,GENERATION_,POINTER_) VALUES(?,?,?,?,?,?,?)",
        command.tenant(),scopeHash(s),command.taskId(),command.commandId(),execution.getId(),qualified.generation(),text(Jcs.canonical(pointer)))==1);
    require(db.update("INSERT INTO MZO_HUMAN_CONSUMER_POINTER_HEAD(TENANT_,SCOPE_DIGEST_,EXECUTION_,TASK_,COMMAND_) VALUES(?,?,?,?,?) ON CONFLICT(TENANT_,SCOPE_DIGEST_,EXECUTION_) DO UPDATE SET TASK_=EXCLUDED.TASK_,COMMAND_=EXCLUDED.COMMAND_",
        command.tenant(),scopeHash(s),execution.getId(),command.taskId(),command.commandId())==1);
    context.getTransactionContext().addTransactionListener(org.cibseven.bpm.engine.impl.cfg.TransactionState.COMMITTING,
        ignored->qualified.requireCurrent(context));
    return qualified;
  }

  /** Actual native execution ancestry only. Called after the delegated CIB creation behavior. */
  static void created(ExecutionEntity execution,ExternalTaskEntity task) {
    var context=Context.getCommandContext();require(context!=null);
    String tenant=task.getTenantId();
    if(!expectedEdge(task.getProcessDefinitionKey(),task.getTopicName()))return;
    var qualified=ConsumerEdgeInstallation.current(context,tenant);
    var db=new EngineStore(context,tenant);String scope=scopeHash(qualified.scope());
    List<Map<String,Object>> ancestors=new ArrayList<>();Set<String> seen=new HashSet<>();
    for(ExecutionEntity current=execution;current!=null;current=current.getParent()) {
      require(seen.add(current.getId()) && seen.size()<=32);
      require(task.getProcessInstanceId().equals(current.getProcessInstanceId()));
      var found=db.rows("SELECT P.* FROM MZO_HUMAN_CONSUMER_POINTER_HEAD H JOIN MZO_HUMAN_CONSUMER_POINTER P ON P.TENANT_=H.TENANT_ AND P.TASK_=H.TASK_ AND P.COMMAND_=H.COMMAND_ WHERE H.TENANT_=? AND H.SCOPE_DIGEST_=? AND H.EXECUTION_=?",
          tenant,scope,current.getId());
      ancestors.addAll(found);
    }
    require(ancestors.size()==1);
    var row=ancestors.get(0);require(((Number)row.get("generation_")).longValue()==qualified.generation());
    var pointer=object(((String)row.get("pointer_")).getBytes(StandardCharsets.UTF_8));
    require(task.getProcessDefinitionId().equals(pointer.get("process_definition_id"))
        && task.getProcessInstanceId().equals(pointer.get("process_instance_id"))
        && scope.equals(pointer.get("scope_digest")));
    var target=qualified.target(task.getProcessDefinitionId(),ref(pointer,"original_task_key"));
    var edge=qualified.edge(target,ref(pointer,"outcome"),task.getActivityId(),task.getTopicName());
    Map<String,Object> link=new TreeMap<>(pointer);link.remove("outcome");
    // Link execution is the actual descendant execution, not a copied original execution.
    link.put("execution_id",task.getExecutionId());link.put("external_task_id",task.getId());link.put("activity_id",task.getActivityId());
    var value=link(link);
    require(db.update("INSERT INTO MZO_HUMAN_CONSUMER_LINK(TENANT_,SCOPE_DIGEST_,EXTERNAL_TASK_,TASK_,COMMAND_,GENERATION_,LINK_,LINK_DIGEST_,CONSUMER_KIND_,CONSUMER_DIGEST_,OUTCOME_) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        tenant,scope,task.getId(),pointer.get("original_task_id"),pointer.get("command_ref"),qualified.generation(),
        text(Jcs.canonical(value)),linkHash(value),edge.get("consumer_kind"),target.get("consumer_digest"),pointer.get("outcome"))==1);
    context.getTransactionContext().addTransactionListener(org.cibseven.bpm.engine.impl.cfg.TransactionState.COMMITTING,
        ignored->qualified.requireCurrent(context));
  }

  /** A native known edge is never inferred unclassified because marker variables disappeared. */
  public static boolean expectedEdge(String process,String topic) {
    return ("SP-OP-AUTH-001".equals(process) && Set.of("operadora.auth.send_denial_notice","operadora.auth.convene_junta").contains(topic))
        || ("SP-OP-PAGTO-001".equals(process) && "operadora.pagto.register_payment_refusal".equals(topic));
  }

  public static boolean classified(CommandContext context,ExternalTaskEntity task) {
    require(context==Context.getCommandContext() && task!=null);
    if(expectedEdge(task.getProcessDefinitionKey(),task.getTopicName()))return true;
    var db=new EngineStore(context,task.getTenantId());
    // Presence is a read-only discriminator, not grant authority. Missing P2 tables are unavailable.
    var present=db.rows("SELECT to_regclass(current_schema() || '.mzo_human_consumer_link') IS NOT NULL AS present");
    require(present.size()==1);
    if (!Boolean.TRUE.equals(present.get(0).get("present"))) return false;
    var existing=db.rows("SELECT EXTERNAL_TASK_ FROM MZO_HUMAN_CONSUMER_LINK WHERE TENANT_=? AND EXTERNAL_TASK_=?",task.getTenantId(),task.getId());
    return !existing.isEmpty();
  }
}
