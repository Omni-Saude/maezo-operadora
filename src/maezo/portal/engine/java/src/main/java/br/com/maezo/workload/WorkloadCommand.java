package br.com.maezo.workload;

import java.sql.*;
import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.util.*;
import org.cibseven.bpm.engine.*;
import org.cibseven.bpm.engine.externaltask.*;
import org.cibseven.bpm.engine.history.*;
import org.cibseven.bpm.engine.impl.cfg.TransactionState;
import org.cibseven.bpm.engine.impl.interceptor.*;
import org.cibseven.bpm.engine.repository.ProcessDefinition;
import org.cibseven.bpm.engine.variable.value.*;

/** Validates authoritative resource and source provenance in the SAME native command transaction. */
final class WorkloadCommand implements Command<java.util.function.Supplier<byte[]>> {
  private final WorkloadPlugin plugin;
  private final BoundaryPolicy.Peer peer;
  private final Capability cap;
  private final Map<String,Object> request;
  private final List<Long> lockDeadlines=new ArrayList<>();
  private Connection connection;
  private CommandContext context;
  private ProcessEngine engine;
  private BoundaryPolicy policy;

  WorkloadCommand(WorkloadPlugin plugin,BoundaryPolicy.Peer peer,Capability cap,Map<String,Object> request) {
    this.plugin=plugin;this.peer=peer;this.cap=cap;this.request=request;
  }
  @Override public java.util.function.Supplier<byte[]> execute(CommandContext ctx) {
    boolean guarded=WorkloadPlugin.GUARDED.get();WorkloadPlugin.GUARDED.set(true);
    try {
      context=ctx;engine=plugin.engine();policy=plugin.policy();plugin.authenticated(peer);
      if(!ctx.isAuthorizationCheckEnabled() || !ctx.isTenantCheckEnabled())throw Refused.denied();
      connection=ctx.getDbSqlSession().getSqlSession().getConnection();
      try {if(connection.getAutoCommit() || !"PostgreSQL".equals(connection.getMetaData().getDatabaseProductName())
          || connection.getTransactionIsolation()!=Connection.TRANSACTION_READ_COMMITTED)throw Refused.unavailable();}
      catch(SQLException e){throw Refused.unavailable();}
      Map<String,Object> variables=cap.validate(request);
      plugin.authorizeCapability(peer,cap);
      attest(variables);
      current();
      Object result=switch(Json.token(cap.schema,"operation")) {
        case "start" -> start(variables);
        case "read_active" -> readActive();
        case "read_history" -> readHistory();
        case "correlate" -> correlate(variables);
        case "fetch_lock" -> fetch();
        case "external_complete","external_failure","external_bpmn_error","external_unlock","external_extend_lock" -> external(variables);
        default -> throw Refused.denied();
      };
      java.util.function.Supplier<Object> output=result instanceof FetchOutput fetch?fetch::render:()->result;
      java.util.function.Supplier<byte[]> encoded=()->Json.bytes(Map.of("protocol","maezo.engine-result.v1","capability_digest",cap.digest,"result",output.get()));
      encoded.get(); // Check serialization/size before commit; fetch's final result may only shrink on native races.
      current();
      ctx.getTransactionContext().addTransactionListener(TransactionState.COMMITTING,ignored->current());
      return encoded;
    } finally {if(guarded)WorkloadPlugin.GUARDED.set(true);else WorkloadPlugin.GUARDED.remove();}
  }
  private void current() {
    plugin.authenticated(peer);
    if(!context.isAuthorizationCheckEnabled() || !context.isTenantCheckEnabled())throw Refused.denied();
    long now=System.currentTimeMillis();
    for(long deadline:lockDeadlines)if(deadline<=now)throw Refused.resource();
    plugin.reauthorizeCapability(context,peer,cap);
  }
  static ProcessDefinition definition(ProcessEngine engine,Map<String,Object> target,String tenant) {
    var d=engine.getRepositoryService().createProcessDefinitionQuery().processDefinitionId(Json.token(target,"definition_id")).tenantIdIn(tenant).singleResult();
    if(d==null || !tenant.equals(d.getTenantId()) || !target.get("process_key").equals(d.getKey()) || Json.number(target,"process_version")!=d.getVersion() || d.isSuspended())throw Refused.resource();
    return d;
  }
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
  private int limit() {return (int)Math.min(policy.maxTasks,Integer.MAX_VALUE-1);}
  private Object fetch() {
    var p=Json.object(request.get("parameters"));
    long count=Json.number(p,"maxTasks"),duration=Json.number(p,"lockDuration"),poll=Json.number(p,"asyncResponseTimeout");
    if(count>policy.maxTasks || duration>policy.maxLockMillis || poll>policy.maxPollMillis)throw Refused.body();
    @SuppressWarnings("unchecked") var projection=(List<String>)Json.list(p.get("variables"));
    var tasks=engine.getExternalTaskService().fetchAndLock((int)count,cap.worker).topic(Json.token(cap.schema,"topic"),duration)
        .processDefinitionId(Json.token(cap.target,"definition_id")).tenantIdIn(policy.tenant).variables(projection).execute();
    for(var task:tasks) {
      if(!cap.target.get("definition_id").equals(task.getProcessDefinitionId()) || !policy.tenant.equals(task.getTenantId())
          || !cap.schema.get("topic").equals(task.getTopicName()) || !cap.worker.equals(task.getWorkerId()))throw Refused.resource();
      lockDeadlines.add(task.getLockExpirationTime().getTime());
    }
    return new FetchOutput(tasks,projection);
  }
  private record FetchOutput(List<LockedExternalTask> tasks,List<String> projection) {
    Object render() {
    List<Object> result=new ArrayList<>();
    for(var task:tasks) {
      Map<String,Object> values=new HashMap<>();
      for(String name:projection)if(task.getVariables().containsKey(name))values.put(name,wire(task.getVariables().getValueTyped(name)));
      Map<String,Object> row=new HashMap<>();
      row.put("id",task.getId());row.put("definition_id",task.getProcessDefinitionId());row.put("process_instance_id",task.getProcessInstanceId());
      row.put("execution_id",task.getExecutionId());row.put("topic",task.getTopicName());row.put("worker_id",task.getWorkerId());
      row.put("lock_expires_at",task.getLockExpirationTime().getTime());row.put("variables",values);row.put("retries",task.getRetries());result.add(row);
    }
    return result;
    }
  }
  private Object external(Map<String,Object> variables) {
    String id=Json.token(request,"resource_ref");locked(id,cap.target,cap.worker);
    current();var service=engine.getExternalTaskService();var p=Json.object(request.get("parameters"));
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
    return Map.of("applied",true);
  }
  private ExternalTask locked(String id,Map<String,Object> target,String worker) {
    // Native commands use optimistic revisions too; this enlisted row lock serializes lock ownership checks.
    if(!exists("SELECT ID_ FROM ACT_RU_EXT_TASK WHERE ID_=? AND TENANT_ID_=? FOR UPDATE",id,policy.tenant))throw Refused.resource();
    current();
    var task=engine.getExternalTaskService().createExternalTaskQuery().externalTaskId(id).tenantIdIn(policy.tenant).singleResult();
    if(task==null || task.isSuspended() || !target.get("definition_id").equals(task.getProcessDefinitionId())
        || !target.get("topic").equals(task.getTopicName()) || !worker.equals(task.getWorkerId()) || task.getLockExpirationTime()==null)throw Refused.resource();
    lockDeadlines.add(task.getLockExpirationTime().getTime());current();return task;
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
    Set<String> processes=new TreeSet<>();executions.forEach(e->processes.add(e.getProcessInstanceId()));
    for(String id:processes) {
      if(!exists("SELECT ID_ FROM ACT_RU_EXECUTION WHERE ID_=? AND TENANT_ID_=? AND PROC_DEF_ID_=? FOR UPDATE",id,policy.tenant,cap.target.get("definition_id")))throw Refused.resource();
      current();engine.getRuntimeService().createMessageCorrelation(Json.token(cap.schema,"message"))
          .processInstanceId(id).setVariables(Capability.engineVariables(variables)).correlateWithResult();
    }
    return Map.of("correlated",(long)processes.size());
  }
  private void attest(Map<String,Object> variables) {
    if(cap.sourceTarget.isEmpty())return;
    definition(engine,cap.sourceTarget,policy.tenant);
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
