package br.com.maezo.workload;

import java.sql.*;
import java.nio.charset.StandardCharsets;
import java.util.*;
import org.cibseven.bpm.engine.*;
import org.cibseven.bpm.engine.externaltask.*;
import org.cibseven.bpm.engine.history.*;
import org.cibseven.bpm.engine.impl.cfg.TransactionState;
import org.cibseven.bpm.engine.impl.interceptor.*;
import org.cibseven.bpm.engine.variable.value.*;

/** V2 F/G/A/R/P/T/L transaction; all finalization precedes physical commit. */
final class NativeOperationV2 implements Command<NativeOutcomeV2.Publication> {
  static final class ReadCommand implements Command<NativeOutcomeV2.Publication> {
    final WorkloadPlugin plugin;final BoundaryPolicy.Peer peer;final Map<String,Object> query;final String digest;final CapabilityV2 probe;
    ReadCommand(WorkloadPlugin plugin,BoundaryPolicy.Peer peer,Map<String,Object> query,String digest,CapabilityV2 probe){this.plugin=plugin;this.peer=peer;this.query=query;this.digest=digest;this.probe=probe;}
    @Override public NativeOutcomeV2.Publication execute(CommandContext ctx){
      boolean before=WorkloadPlugin.GUARDED.get();WorkloadPlugin.GUARDED.set(true);
      try {
        var policy=plugin.v2();NativeOutcomeV2.Encoded encoded;List<NativeAdmissionV2> guards=new ArrayList<>();
        if(probe!=null){
          probe.validate(query);var a=new NativeAdmissionV2(policy,peer,ctx,plugin.engine(),probe.digest,probe.binding,"operation");guards.add(a);a.grants(probe);
          // A read-only polling probe never executes or writes a receipt. All locks release before waiting.
          var binding=new NativeOperationV2(plugin,peer,probe,query,digest).binding();
          var prior=a.call("read_receipt_v2",Map.of("command",binding,"history",false));
          boolean found=!"not_observed".equals(prior.get("status")) || !new NativeFetchV2(a,probe,Json.object(query.get("parameters"))).ids.isEmpty();
          encoded=new NativeOutcomeV2.Encoded(200,Json.bytes(Map.of("candidate",found)));
        }else if(query==null){
          var caps=policy.capabilities.getOrDefault(peer.certificate(),List.of()).stream().sorted(Comparator.comparing(c->c.digest)).toList();
          if(caps.isEmpty())throw Refused.unavailable();
          for(var cap:caps){var a=new NativeAdmissionV2(policy,peer,ctx,plugin.engine(),cap.digest,cap.binding,"readiness");guards.add(a);a.grants(cap);}
          Map<String,Object> body=new HashMap<>();body.put("protocol","maezo.engine-readiness.v2");body.put("ready",true);body.put("policy_digest",policy.digest);body.put("config_digest",policy.digest);body.put("schema_digest",policy.schemaDigest);body.put("capabilities",caps.stream().map(c->c.digest).toList());body.put("activation_ref",policy.admission.get("activation_ref"));body.put("database_incarnation",policy.admission.get("database_incarnation"));body.put("purpose",policy.admission.get("purpose"));
          encoded=new NativeOutcomeV2.Encoded(200,Json.bytes(body));
        }else{
          Json.keys(query,"protocol","recovery_capability_digest","reader_activation_ref","command");
          if(!"maezo.engine-outcome-query.v2".equals(query.get("protocol")))throw Refused.body();
          if(!policy.admission.get("activation_ref").equals(Json.token(query,"reader_activation_ref")))throw Refused.denied();
          String recovery=NativeOutcomeV2.digest(query,"recovery_capability_digest");var command=NativeOutcomeV2.command(query.get("command"));var binding=policy.recovery(peer,recovery,command);
          var a=new NativeAdmissionV2(policy,peer,ctx,plugin.engine(),recovery,binding,"outcome");guards.add(a);
          var row=a.call("read_receipt_v2",Map.of("command",command,"history",true));
          encoded=NativeOutcomeV2.outcome(query,digest,Json.token(row,"status"),row.get("receipt")==null?null:Json.object(row.get("receipt")));
        }
        var publication=new NativeOutcomeV2.Publication();
        ctx.getTransactionContext().addTransactionListener(TransactionState.COMMITTING,ignored->{
          for(var a:guards){a.current();if(!"outcome".equals(a.captured.get("kind")))a.grantsFinal(policy.capability(peer,(String)a.captured.get("capability_digest")));}
          publication.prepare(encoded);
        });
        ctx.getTransactionContext().addTransactionListener(TransactionState.COMMITTED,ignored->publication.publish());return publication;
      }finally{if(before)WorkloadPlugin.GUARDED.set(true);else WorkloadPlugin.GUARDED.remove();}
    }
  }
  private final WorkloadPlugin plugin;
  private final BoundaryPolicy.Peer peer;
  private final CapabilityV2 cap;
  private final Map<String,Object> request;
  private final String requestDigest;
  private final NativeOutcomeV2.Publication publication=new NativeOutcomeV2.Publication();
  private NativeAdmissionV2 admission;
  private NativeAcquisitionStoreV2 store;
  private BoundaryPolicy policy;
  private Connection connection;
  private CommandContext context;
  private ProcessEngine engine;
  private Map<String,Object> command;
  private final Map<String,Map<String,Object>> consumed=new HashMap<>();
  private List<String> correlationRoots=List.of();
  private NativeFetchV2 fetch;
  private Object value;
  NativeOperationV2(WorkloadPlugin plugin,BoundaryPolicy.Peer peer,CapabilityV2 cap,Map<String,Object> request,String digest){
    this.plugin=plugin;this.peer=peer;this.cap=cap;this.request=request;this.requestDigest=digest;
  }
  Map<String,Object> binding(){
    var p=plugin.v2();Map<String,Object> result=new HashMap<>();result.put("engine",p.transport.engine);result.put("database_incarnation",p.admission.get("database_incarnation"));
    result.put("identity",peer.identity());result.put("native_user",peer.engineUser());result.put("operation",cap.schema.get("operation"));result.put("capability_digest",cap.digest);result.put("command_id",request.get("command_id"));result.put("request_digest",requestDigest);result.put("activation_ref",request.get("activation_ref"));
    return NativeOutcomeV2.command(result);
  }
  @Override public NativeOutcomeV2.Publication execute(CommandContext ctx){
    boolean before=WorkloadPlugin.GUARDED.get();WorkloadPlugin.GUARDED.set(true);
    try {
      context=ctx;engine=plugin.engine();policy=plugin.v2().transport;connection=ctx.getDbSqlSession().getSqlSession().getConnection();
      Map<String,Object> variables=cap.validate(request);
      if(!request.get("activation_ref").equals(plugin.v2().admission.get("activation_ref")))throw Refused.denied();
      admission=new NativeAdmissionV2(plugin.v2(),peer,ctx,engine,cap.digest,cap.binding,"operation");command=binding();admission.grants(cap);
      var prior=admission.call("read_receipt_v2",Map.of("command",command,"history",false));
      String disposition=Json.token(prior,"status");
      if(!disposition.equals("not_observed")){
        if(!Set.of("committed","conflict","unavailable").contains(disposition))throw Refused.unavailable();
        String state=disposition.equals("committed")?"duplicate":disposition;
        prepareReadOnly(NativeOutcomeV2.operation(command,state,prior.get("receipt")==null?null:Json.object(prior.get("receipt")),null,List.of()));return publication;
      }
      store=new NativeAcquisitionStoreV2(admission);List<String> ids=new ArrayList<>();
      if(request.get("resource_acquisition")!=null)ids.add(Json.token(request,"resource_ref"));
      if(request.get("source_acquisition")!=null)ids.add(Json.token(request,"source_ref"));
      if("fetch_lock".equals(cap.schema.get("operation"))){fetch=new NativeFetchV2(admission,cap,Json.object(request.get("parameters")));ids.addAll(fetch.ids);}
      if("correlate".equals(cap.schema.get("operation")))correlationRoots=discoverCorrelation();
      store.lock(ids,correlationRoots,NativeAcquisitionStoreV2.selection(request,fetch==null?List.of():fetch.ids));
      if(request.get("resource_acquisition")!=null){String id=Json.token(request,"resource_ref");consumed.put(id,store.consume(id,request.get("resource_acquisition"),cap.target,cap.worker,peer.identity(),peer.engineUser(),null));}
      if(request.get("source_acquisition")!=null){String id=Json.token(request,"source_ref");var owner=cap.sourceOwner;var row=store.consume(id,request.get("source_acquisition"),cap.sourceTarget,cap.sourceWorker,Json.object(owner.get("identity")),Json.token(owner,"native_user"),Json.token(owner,"fetch_capability_digest"));if(consumed.containsKey(id)&&!consumed.get(id).equals(row))throw Refused.resource();consumed.put(id,row);}
      attest(variables);current();
      value=switch(Json.token(cap.schema,"operation")){
        case "fetch_lock" -> {fetch.acquire(store);yield null;}
        case "start" -> start(variables);case "read_active" -> readActive();case "read_history" -> readHistory();case "correlate" -> correlate(variables);
        case "external_complete","external_failure","external_bpmn_error","external_unlock","external_extend_lock" -> external(variables);
        default -> throw Refused.denied();};
      // No lazy serialization or authorization in COMMITTED. COMMITTING follows native flush.
      ctx.getTransactionContext().addTransactionListener(TransactionState.COMMITTING,ignored->finalizeBeforeCommit());
      ctx.getTransactionContext().addTransactionListener(TransactionState.COMMITTED,ignored->publication.publish());
      return publication;
    }finally{if(before)WorkloadPlugin.GUARDED.set(true);else WorkloadPlugin.GUARDED.remove();}
  }
  private void prepareReadOnly(NativeOutcomeV2.Encoded result){
    context.getTransactionContext().addTransactionListener(TransactionState.COMMITTING,ignored->{admission.grantsFinal(cap);publication.prepare(result);});
    context.getTransactionContext().addTransactionListener(TransactionState.COMMITTED,ignored->publication.publish());
  }
  private void current(){admission.grantsFinal(cap);for(var row:consumed.values())if(Json.number(row,"lock_expires_at")<=admission.now())throw Refused.resource();}
  private void finalizeBeforeCommit(){
    current();List<Object> snapshots=new ArrayList<>();Map<String,Map<String,Object>> finalRows=new HashMap<>(consumed);
    if(fetch!=null){
      var actual=store.rows(fetch.ids);if(!actual.keySet().equals(new HashSet<>(fetch.ids)))throw Refused.resource();
      for(String id:fetch.ids){var row=fetch.finalizeTask(id,command,actual.get(id),store.predecessor(id));snapshots.add(store.snapshot("resource",row));}
      value=fetch.value(actual);
    }else{
      if(request.get("resource_acquisition")!=null){
        String id=Json.token(request,"resource_ref"),op=Json.token(cap.schema,"operation");var old=consumed.get(id);Map<String,Object> changed;
        if(op.equals("external_extend_lock")){
          var actual=store.rows(List.of(id));var task=actual.get(id);if(task==null)throw Refused.resource();NativeAcquisitionStoreV2.checkTask(task,cap.target,cap.worker,admission.now());
          changed=admission.call("renew_acquisition_v2",Map.of("reference",request.get("resource_acquisition"),"task_id",id,"lock_expires_at",task.get("expiry"),"command",command));
        }else changed=admission.call("close_acquisition_v2",Map.of("reference",request.get("resource_acquisition"),"task_id",id,"command",command));
        finalRows.put(id,changed);snapshots.add(store.snapshot("resource",changed));
      }
      if(request.get("source_acquisition")!=null){String id=Json.token(request,"source_ref");var row=finalRows.get(id);
        if(!id.equals(request.get("resource_ref"))){var task=store.rows(List.of(id)).get(id);if(task==null)throw Refused.resource();NativeAcquisitionStoreV2.checkTask(task,cap.sourceTarget,cap.sourceWorker,admission.now());if(!task.get("expiry").equals(row.get("lock_expires_at")))throw Refused.resource();}
        snapshots.add(store.snapshot("source",row));}
    }
    Map<String,Object> receipt=new HashMap<>();receipt.put("receipt_ref",NativeOutcomeV2.newRef());receipt.put("state","committed");receipt.put("command",command);
    receipt.put("resource_ref_digest",NativeOutcomeV2.pointerDigest(Json.string(request,"resource_ref")));receipt.put("source_ref_digest",NativeOutcomeV2.pointerDigest(Json.string(request,"source_ref")));
    receipt.put("resource_acquisition",request.get("resource_acquisition"));receipt.put("source_acquisition",request.get("source_acquisition"));receipt.put("acquisitions",snapshots);
    NativeOutcomeV2.receipt(receipt,command);var encoded=NativeOutcomeV2.operation(command,"executed",receipt,value,snapshots);
    var stored=admission.call("append_receipt_v2",Map.of("receipt",receipt,"receipt_bytes",new String(Json.bytes(receipt),StandardCharsets.UTF_8),"receipt_digest",Json.digest(receipt)));
    if(!receipt.equals(stored))throw Refused.unavailable();admission.grantsFinal(cap);publication.prepare(encoded);
  }
  private List<String> discoverCorrelation(){
    var query=engine.getRuntimeService().createExecutionQuery().processDefinitionId(Json.token(cap.target,"definition_id")).tenantIdIn(policy.tenant).messageEventSubscriptionName(Json.token(cap.schema,"message"));
    Json.object(request.get("correlation")).forEach((name,value)->query.processVariableValueEquals(name,value));String business=Json.string(request,"resource_ref");if(!business.isEmpty())query.processInstanceBusinessKey(business);
    var executions=query.listPage(0,limit()+1);if(executions.size()>limit() || (!Json.bool(cap.schema,"all_matching")&&executions.size()!=1))throw Refused.resource();
    return NativeAcquisitionStoreV2.ordered(executions.stream().map(e->e.getProcessInstanceId()).toList());
  }
  private int limit(){return (int)Math.min(policy.maxTasks,Integer.MAX_VALUE-1);}
  private Object start(Map<String,Object> variables) {
    var instance=engine.getRuntimeService().startProcessInstanceById(Json.token(cap.target,"definition_id"),Json.token(request,"resource_ref"),Capability.engineVariables(variables));
    return Map.of("id",instance.getId(),"definition_id",instance.getProcessDefinitionId(),"tenant",policy.tenant);
  }
  private Object readActive() {
    var instances=engine.getRuntimeService().createProcessInstanceQuery().processDefinitionId(Json.token(cap.target,"definition_id"))
        .tenantIdIn(policy.tenant).processInstanceBusinessKey(Json.token(request,"resource_ref")).listPage(0,limit()+1);
    if(instances.size()>limit())throw Refused.unavailable();
    return instances.stream().map(i->Map.of("id",i.getId(),"definition_id",i.getProcessDefinitionId(),"state","ACTIVE")).toList();
  }
  private Object readHistory() {
    var instances=engine.getHistoryService().createHistoricProcessInstanceQuery().processDefinitionId(Json.token(cap.target,"definition_id"))
        .tenantIdIn(policy.tenant).processInstanceBusinessKey(Json.token(request,"resource_ref")).listPage(0,limit()+1);
    if(instances.size()>limit())throw Refused.unavailable();
    return instances.stream().map(i->Map.of("id",i.getId(),"definition_id",i.getProcessDefinitionId(),"state",i.getState())).toList();
  }
  private Object external(Map<String,Object> variables) {
    String id=Json.token(request,"resource_ref");locked(id,cap.target,cap.worker);
    current();var service=engine.getExternalTaskService();var p=Json.object(request.get("parameters"));
    try(var consumerSeal=ClassifiedConsumerFence.external(this,context,id,Json.token(cap.schema,"operation"),variables,p)) {
    switch(Json.token(cap.schema,"operation")) {
      case "external_complete":service.complete(id,cap.worker,Capability.engineVariables(variables));break;
      case "external_bpmn_error":service.handleBpmnError(id,cap.worker,Json.token(request,"error_code"),null,Capability.engineVariables(variables));break;
      case "external_failure":
        if(Json.number(p,"retryTimeout")>policy.maxLockMillis)throw Refused.body();
        service.handleFailure(id,cap.worker,"worker_failure",null,(int)Json.number(p,"retries"),Json.number(p,"retryTimeout"));break;
      case "external_extend_lock":
        if(Json.number(p,"newDuration")>policy.maxLockMillis)throw Refused.body();
        service.extendLock(id,cap.worker,Json.number(p,"newDuration"));break;
      case "external_unlock":service.unlock(id);break;
      default:throw Refused.denied();
    }
    }
    return Map.of("applied",true);
  }
  private ExternalTask locked(String id,Map<String,Object> target,String worker) {
    if(!consumed.containsKey(id))throw Refused.resource();
    var task=engine.getExternalTaskService().createExternalTaskQuery().externalTaskId(id).tenantIdIn(policy.tenant).singleResult();
    if(task==null)throw Refused.resource();return task;
  }
  private Object correlate(Map<String,Object> variables) {
    var query=engine.getRuntimeService().createExecutionQuery().processDefinitionId(Json.token(cap.target,"definition_id"))
        .tenantIdIn(policy.tenant).messageEventSubscriptionName(Json.token(cap.schema,"message"));
    var correlation=Json.object(request.get("correlation"));
    correlation.forEach((name,value)->query.processVariableValueEquals(name,value));
    String business=Json.string(request,"resource_ref");
    if(!business.isEmpty())query.processInstanceBusinessKey(business);
    var executions=query.listPage(0,limit()+1);
    if(executions.size()>limit() || (!Json.bool(cap.schema,"all_matching") && executions.size()!=1))throw Refused.resource();
    var processes=NativeAcquisitionStoreV2.ordered(executions.stream().map(e->e.getProcessInstanceId()).toList());
    if(!processes.equals(correlationRoots))throw Refused.resource();
    for(String id:processes) {
      try(var consumerSeal=ClassifiedConsumerFence.correlation(this,context,List.of(id))) {
      current();engine.getRuntimeService().createMessageCorrelation(Json.token(cap.schema,"message"))
          .processInstanceId(id).setVariables(Capability.engineVariables(variables)).correlateWithResult();
      }
    }
    return Map.of("correlated",(long)processes.size());
  }
  private void attest(Map<String,Object> variables) {
    if(cap.sourceTarget.isEmpty())return;
    WorkloadCommand.definition(engine,cap.sourceTarget,policy.tenant);
    String source=Json.token(request,"source_ref"),process,execution;
    HistoricTaskInstance sourceHuman=null;
    if(cap.sourceKind.equals("locked_external")) {
      ExternalTask task=locked(source,cap.sourceTarget,cap.sourceWorker);process=task.getProcessInstanceId();execution=task.getExecutionId();
    } else {
      sourceHuman=engine.getHistoryService().createHistoricTaskInstanceQuery().taskId(source).tenantIdIn(policy.tenant).finished().singleResult();
      if(sourceHuman==null || !cap.sourceTarget.get("definition_id").equals(sourceHuman.getProcessDefinitionId()) || !hasReceipt(sourceHuman))throw Refused.resource();
      process=sourceHuman.getProcessInstanceId();execution=sourceHuman.getExecutionId();
    }
    var correlation=Json.object(request.get("correlation"));
    for(Object item:cap.attestations) {
      var binding=Json.object(item);String name=Json.token(binding,"name"),variable=Json.token(binding,"source_variable");
      boolean inVariables=variables.containsKey(name);if(!inVariables && !correlation.containsKey(name))continue;
      Object supplied=inVariables?variables.get(name):correlation.get(name);
      Object actual;
      if("@business_key".equals(variable)) {
        var instance=engine.getHistoryService().createHistoricProcessInstanceQuery().processInstanceId(process).tenantIdIn(policy.tenant).singleResult();
        if(instance==null)throw Refused.resource();actual=instance.getBusinessKey();
      } else if(sourceHuman==null)actual=value(engine.getRuntimeService().getVariableTyped(execution,variable,false));
      else {
        var values=engine.getHistoryService().createHistoricVariableInstanceQuery().processInstanceId(process).variableName(variable)
            .tenantIdIn(policy.tenant).disableCustomObjectDeserialization().disableBinaryFetching().listPage(0,2);
        if(values.size()!=1)throw Refused.resource();actual=value(values.get(0).getTypedValue());
      }
      if(!Objects.equals(actual,supplied))throw Refused.resource();
      var rule=Json.list(cap.schema.get("fields")).stream().map(Json::object).filter(f->name.equals(f.get("name"))).findFirst().orElse(Map.of());
      if("prior_human_evidence".equals(rule.get("origin"))) {
        boolean empty=false;
        if("".equals(supplied) && rule.get("empty_evidence_when")!=null) {
          var condition=Json.list(rule.get("empty_evidence_when"));
          Object expected=Json.parse(("{\"v\":"+condition.get(1)+"}").getBytes(java.nio.charset.StandardCharsets.UTF_8)).get("v");
          empty=Objects.equals(variables.get(condition.get(0)),expected);
        }
        if(!empty) {
          String taskDefinition=Json.token(binding,"human_task_definition");
          var tasks=engine.getHistoryService().createHistoricTaskInstanceQuery().processInstanceId(process).tenantIdIn(policy.tenant)
              .taskDefinitionKey(taskDefinition).finished().orderByHistoricTaskInstanceEndTime().desc().listPage(0,1);
          if(tasks.size()!=1 || !humanSource(tasks.get(0),cap.sourceTarget,policy.tenant,process,taskDefinition)
              || !hasReceipt(tasks.get(0)))throw Refused.resource();
          // A copied actor/value is not provenance. Require the actual completed task's recorded variable update.
          if(!"@business_key".equals(variable)) {
            var updates=engine.getHistoryService().createHistoricDetailQuery().taskId(tasks.get(0).getId()).tenantIdIn(policy.tenant)
                .variableUpdates().disableCustomObjectDeserialization().disableBinaryFetching().listPage(0,limit()+1);
            if(updates.size()>limit() || updates.stream().filter(d->d instanceof HistoricVariableUpdate)
                .map(d->(HistoricVariableUpdate)d).noneMatch(u->humanUpdate(u,tasks.get(0))
                    && variable.equals(u.getVariableName()) && Objects.equals(value(u.getTypedValue()),supplied)))throw Refused.resource();
          }
        }
      }
    }
  }
  static boolean humanSource(HistoricTaskInstance task,Map<String,Object> target,String tenant,String process,String taskDefinition) {
    return task!=null && target.get("definition_id").equals(task.getProcessDefinitionId())
        && tenant.equals(task.getTenantId()) && process.equals(task.getProcessInstanceId()) && taskDefinition.equals(task.getTaskDefinitionKey());
  }
  static boolean humanUpdate(HistoricVariableUpdate update,HistoricTaskInstance task) {
    return task.getId().equals(update.getTaskId()) && task.getProcessDefinitionId().equals(update.getProcessDefinitionId())
        && task.getTenantId().equals(update.getTenantId()) && task.getProcessInstanceId().equals(update.getProcessInstanceId());
  }
  private boolean hasReceipt(HistoricTaskInstance task) {
    if(task.getEndTime()==null || (task.getDeleteReason()!=null && !"completed".equals(task.getDeleteReason())))return false;
    // Claim/release receipts are not proof of a human decision, even when a legacy client completed the task later.
    try(var statement=connection.prepareStatement("SELECT COMMAND_,PRINCIPAL_,RECEIPT_ FROM MZO_HUMAN_RECEIPT WHERE TENANT_=? AND TASK_=? AND RECEIPT_::jsonb->>'operation'='decision'")) {
      statement.setString(1,policy.tenant);statement.setString(2,task.getId());statement.setMaxRows(2);
      try(var rows=statement.executeQuery()) {
        if(!rows.next())return false;
        var receipt=Json.parse(rows.getString(3).getBytes(StandardCharsets.UTF_8));
        boolean valid=decisionReceipt(receipt,policy.tenant,task.getId(),rows.getString(1),rows.getString(2),task.getAssignee());
        return valid && !rows.next();
      }
    } catch(SQLException e){throw Refused.unavailable();}
  }
  static boolean decisionReceipt(Map<String,Object> receipt,String tenant,String task,String command,String principal,String assignee) {
    return "human-engine-receipt.v1".equals(receipt.get("schema")) && "committed".equals(receipt.get("status"))
        && "decision".equals(receipt.get("operation")) && tenant.equals(receipt.get("tenant")) && task.equals(receipt.get("task_id"))
        && command.equals(receipt.get("command_id")) && principal.equals(receipt.get("principal_ref")) && principal.equals(assignee);
  }
  private boolean exists(String sql,Object... args) {
    try(var statement=connection.prepareStatement(sql)) {
      for(int i=0;i<args.length;i++)statement.setObject(i+1,args[i]);
      try(var result=statement.executeQuery()){return result.next();}
    } catch(SQLException e){throw Refused.unavailable();}
  }
  static Object value(TypedValue typed) {
    if(typed==null)throw Refused.resource();
    String type=typed.getType().getName();
    return switch(type) {
      case "null" -> null;
      case "string","boolean","double","long" -> typed.getValue();
      case "integer" -> ((Integer)typed.getValue()).longValue();
      case "json" -> {
        if(!(typed instanceof SerializableValue serialized))throw Refused.resource();
        yield Json.parse(("{\"v\":"+serialized.getValueSerialized()+"}").getBytes(java.nio.charset.StandardCharsets.UTF_8)).get("v");
      }
      default -> throw Refused.resource();
    };
  }
  static Map<String,Object> wire(TypedValue typed) {
    Map<String,Object> result=new HashMap<>();result.put("type",typed.getType().getName());result.put("value",value(typed));return result;
  }
}
