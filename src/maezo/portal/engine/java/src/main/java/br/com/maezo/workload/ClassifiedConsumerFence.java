package br.com.maezo.workload;

import br.com.maezo.human.ConsumerLineage;
import java.lang.reflect.Field;
import java.util.*;
import org.cibseven.bpm.engine.impl.context.Context;
import org.cibseven.bpm.engine.impl.interceptor.CommandContext;
import org.cibseven.bpm.engine.impl.persistence.entity.ExternalTaskEntity;

/** P2 common native effect boundary. No classified success is admitted before P4/P6/P7. */
final class ClassifiedConsumerFence {
  private ClassifiedConsumerFence(){}
  private static final ThreadLocal<Seal> CURRENT=new ThreadLocal<>();
  private static final Map<String,String> EXTERNAL=Map.of(
      "CompleteExternalTaskCmd","external_complete","HandleExternalTaskBpmnErrorCmd","external_bpmn_error",
      "HandleExternalTaskFailureCmd","external_failure","UnlockExternalTaskCmd","external_unlock",
      "ExtendLockOnExternalTaskCmd","external_extend_lock");
  private static final Set<String> FORBIDDEN=Set.of(
      "SetExecutionVariablesCmd","RemoveExecutionVariablesCmd","PatchExecutionVariablesCmd",
      "SetTaskVariablesCmd","RemoveTaskVariablesCmd","PatchTaskVariablesCmd",
      "ProcessInstanceModificationCmd","ProcessInstanceModificationBatchCmd","RestartProcessInstancesCmd",
      "RestartProcessInstancesBatchCmd","DeleteProcessInstanceCmd","DeleteProcessInstancesCmd",
      "DeleteProcessInstanceBatchCmd","SetVariablesToProcessInstancesBatchCmd","CorrelateAllMessageBatchCmd",
      "MigrateProcessInstanceCmd","MigrateProcessInstanceBatchCmd","SetExternalTaskRetriesCmd",
      "SetExternalTasksRetriesCmd","SetExternalTasksRetriesBatchCmd","SetExternalTaskPriorityCmd",
      "ModifyProcessInstanceCmd","ModifyProcessInstanceAsyncCmd","StartProcessInstanceAtActivitiesCmd",
      "SignalCmd","SignalEventReceivedCmd","CompleteTaskCmd","SubmitTaskFormCmd","ResolveTaskCmd",
      "HandleTaskBpmnErrorCmd","HandleTaskEscalationCmd","DeleteTaskCmd","SaveTaskCmd","CreateTaskCmd",
      "ClaimTaskCmd","AssignTaskCmd","DelegateTaskCmd","SetTaskOwnerCmd","AddIdentityLinkCmd","DeleteIdentityLinkCmd",
      "ActivateProcessInstanceCmd","SuspendProcessInstanceCmd","UpdateProcessInstancesSuspendStateCmd",
      "UpdateProcessInstancesSuspendStateBatchCmd");

  interface Permit extends AutoCloseable { @Override void close(); }
  private static final class Seal implements Permit {
    final CommandContext context;final Object outer;final String target,operation;final Seal previous;
    Seal(Object outer,CommandContext context,String target,String operation){
      this.context=context;this.outer=outer;this.target=target;this.operation=operation;previous=CURRENT.get();
      if(previous!=null)throw Refused.denied();CURRENT.set(this);
    }
    @Override public void close(){if(CURRENT.get()!=this)throw Refused.unavailable();CURRENT.remove();}
  }
  static Permit external(Object outer,CommandContext context,String id,String operation,Map<String,Object> variables,Map<String,Object> parameters){
    if(context!=Context.getCommandContext() || !(outer instanceof NativeOperationV2 || outer instanceof WorkloadCommand))throw Refused.denied();
    ExternalTaskEntity task=context.getExternalTaskManager().findExternalTaskById(id);if(task==null)throw Refused.resource();
    boolean classified=ConsumerLineage.classified(context,task);
    disposition(classified,outer instanceof NativeOperationV2,operation,variables,parameters);
    return new Seal(outer,context,id,operation);
  }
  static void disposition(boolean classified,boolean v2,String operation,Map<String,Object> variables,Map<String,Object> parameters){
    if(!EXTERNAL.containsValue(operation))throw Refused.denied();
    if(!classified)return;
    if(!v2 || !Set.of("external_failure","external_unlock","external_extend_lock").contains(operation) || !variables.isEmpty())throw Refused.unavailable();
    // This unactivated P2 slice has only the permanent unavailable incident disposition.
    if("external_failure".equals(operation) && Json.number(parameters,"retries")!=0)throw Refused.unavailable();
  }
  static Permit correlation(Object outer,CommandContext context,List<String> roots){
    if(context!=Context.getCommandContext() || !(outer instanceof NativeOperationV2 || outer instanceof WorkloadCommand) || roots.isEmpty())throw Refused.denied();
    for(String id:roots){
      var execution=context.getExecutionManager().findExecutionById(id);if(execution==null)throw Refused.resource();
      // Auth/PAGTO/ESC are the admitted source families; correlation cannot skip a classified edge.
      if(Set.of("SP-OP-AUTH-001","SP-OP-PAGTO-001","SP-OP-ESCALATION-001").contains(execution.getProcessDefinition().getKey()))throw Refused.unavailable();
    }
    return new Seal(outer,context,String.join("\n",roots),"correlate");
  }
  static void nested(Object command){
    String name=command.getClass().getSimpleName();
    if(FORBIDDEN.contains(name))throw Refused.denied();
    String operation=EXTERNAL.get(name);
    if(operation!=null){
      Seal seal=CURRENT.get();
      if(seal==null || seal.context!=Context.getCommandContext() || !operation.equals(seal.operation)
          || !seal.target.equals(externalTaskId(command)))throw Refused.denied();
      var task=seal.context.getExternalTaskManager().findExternalTaskById(seal.target);if(task==null)throw Refused.resource();
      // Reclassify the actual native entity; a valid unclassified seal cannot bless a classified target.
      if(ConsumerLineage.classified(seal.context,task) && (!Set.of("external_failure","external_unlock","external_extend_lock").contains(operation) || !(seal.outer instanceof NativeOperationV2)))throw Refused.unavailable();
      return;
    }
    if(name.equals("CorrelateMessageCmd") || name.equals("CorrelateAllMessageCmd")){
      Seal seal=CURRENT.get();if(seal==null || seal.context!=Context.getCommandContext() || !seal.operation.equals("correlate") || !seal.target.equals(correlationTarget(command)))throw Refused.denied();
    }
  }
  private static String correlationTarget(Object command){
    try {
      Class<?> type=Class.forName("org.cibseven.bpm.engine.impl.cmd.AbstractCorrelateMessageCmd");
      if(!type.isInstance(command) || !command.getClass().getName().equals("org.cibseven.bpm.engine.impl.cmd."+command.getClass().getSimpleName()))throw Refused.denied();
      Field field=type.getDeclaredField("builder");if(!field.trySetAccessible())throw Refused.unavailable();
      var builder=(org.cibseven.bpm.engine.impl.MessageCorrelationBuilderImpl)field.get(command);
      String id=builder.getProcessInstanceId();if(id==null || id.isEmpty())throw Refused.denied();return id;
    }catch(ReflectiveOperationException e){throw Refused.unavailable();}
  }
  private static String externalTaskId(Object command){
    // Pinned CIB2.1 has no public getter. Read only the exact native base class field.
    try {
      Class<?> type=Class.forName("org.cibseven.bpm.engine.impl.cmd.ExternalTaskCmd");
      if(!type.isInstance(command) || !command.getClass().getName().equals("org.cibseven.bpm.engine.impl.cmd."+command.getClass().getSimpleName()))throw Refused.denied();
      Field field=type.getDeclaredField("externalTaskId");if(!field.trySetAccessible())throw Refused.unavailable();
      Object id=field.get(command);if(!(id instanceof String value)||value.isEmpty())throw Refused.resource();return value;
    }catch(ReflectiveOperationException e){throw Refused.unavailable();}
  }
}
