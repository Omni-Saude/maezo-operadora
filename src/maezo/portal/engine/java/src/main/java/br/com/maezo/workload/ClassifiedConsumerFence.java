package br.com.maezo.workload;

import br.com.maezo.human.ConsumerLineage;
import java.lang.reflect.Field;
import java.util.*;
import org.cibseven.bpm.engine.impl.cmd.*;
import org.cibseven.bpm.engine.impl.context.Context;
import org.cibseven.bpm.engine.impl.interceptor.CommandContext;
import org.cibseven.bpm.engine.impl.persistence.entity.ExternalTaskEntity;

/** P2 common native effect boundary. No classified success is admitted before P4/P6/P7. */
final class ClassifiedConsumerFence {
  private ClassifiedConsumerFence(){}
  private static final ThreadLocal<Seal> CURRENT=new ThreadLocal<>();
  private static final Map<Class<?>,String> EXTERNAL=Map.of(
      CompleteExternalTaskCmd.class,"external_complete",HandleExternalTaskBpmnErrorCmd.class,"external_bpmn_error",
      HandleExternalTaskFailureCmd.class,"external_failure",UnlockExternalTaskCmd.class,"external_unlock",
      ExtendLockOnExternalTaskCmd.class,"external_extend_lock");
  private static final Set<Class<?>> FORBIDDEN=Set.of(
      SetExecutionVariablesCmd.class,RemoveExecutionVariablesCmd.class,PatchExecutionVariablesCmd.class,
      SetTaskVariablesCmd.class,RemoveTaskVariablesCmd.class,PatchTaskVariablesCmd.class,
      ProcessInstanceModificationCmd.class,ProcessInstanceModificationBatchCmd.class,RestartProcessInstancesCmd.class,
      org.cibseven.bpm.engine.impl.batch.RestartProcessInstancesBatchCmd.class,DeleteProcessInstanceCmd.class,DeleteProcessInstancesCmd.class,
      org.cibseven.bpm.engine.impl.cmd.batch.DeleteProcessInstanceBatchCmd.class,org.cibseven.bpm.engine.impl.cmd.batch.variables.SetVariablesToProcessInstancesBatchCmd.class,org.cibseven.bpm.engine.impl.cmd.batch.CorrelateAllMessageBatchCmd.class,
      org.cibseven.bpm.engine.impl.migration.MigrateProcessInstanceCmd.class,org.cibseven.bpm.engine.impl.migration.batch.MigrateProcessInstanceBatchCmd.class,SetExternalTaskRetriesCmd.class,
      SetExternalTasksRetriesCmd.class,SetExternalTasksRetriesBatchCmd.class,SetExternalTaskPriorityCmd.class,
      ModifyProcessInstanceCmd.class,ModifyProcessInstanceAsyncCmd.class,StartProcessInstanceAtActivitiesCmd.class,
      SignalCmd.class,SignalEventReceivedCmd.class,CompleteTaskCmd.class,SubmitTaskFormCmd.class,ResolveTaskCmd.class,
      HandleTaskBpmnErrorCmd.class,HandleTaskEscalationCmd.class,DeleteTaskCmd.class,SaveTaskCmd.class,CreateTaskCmd.class,
      ClaimTaskCmd.class,AssignTaskCmd.class,DelegateTaskCmd.class,SetTaskOwnerCmd.class,AbstractAddIdentityLinkCmd.class,DeleteIdentityLinkCmd.class,
      ActivateProcessInstanceCmd.class,SuspendProcessInstanceCmd.class,UpdateProcessInstancesSuspendStateCmd.class,
      UpdateProcessInstancesSuspendStateBatchCmd.class);

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
    // Class identity and assignability retain native semantics even for anonymous subclasses.
    // Only exact pinned effect implementations may consume a seal; inherited effects cannot
    // become unrelated commands merely by changing their Java name.
    for(Class<?> family:FORBIDDEN)if(family.isInstance(command))throw Refused.denied();
    for(Class<?> family:EXTERNAL.keySet())
      if(family.isInstance(command) && command.getClass()!=family)throw Refused.denied();
    String operation=EXTERNAL.get(command.getClass());
    if(operation!=null){
      Seal seal=CURRENT.get();
      if(seal==null || seal.context!=Context.getCommandContext() || !operation.equals(seal.operation)
          || !seal.target.equals(externalTaskId(command)))throw Refused.denied();
      var task=seal.context.getExternalTaskManager().findExternalTaskById(seal.target);if(task==null)throw Refused.resource();
      // Reclassify the actual native entity; a valid unclassified seal cannot bless a classified target.
      if(ConsumerLineage.classified(seal.context,task) && (!Set.of("external_failure","external_unlock","external_extend_lock").contains(operation) || !(seal.outer instanceof NativeOperationV2)))throw Refused.unavailable();
      return;
    }
    if(command instanceof AbstractCorrelateMessageCmd){
      if(command.getClass()!=CorrelateMessageCmd.class && command.getClass()!=CorrelateAllMessageCmd.class)throw Refused.denied();
      Seal seal=CURRENT.get();if(seal==null || seal.context!=Context.getCommandContext() || !seal.operation.equals("correlate") || !seal.target.equals(correlationTarget(command)))throw Refused.denied();
    }
  }
  private static String correlationTarget(Object command){
    try {
      Class<?> type=AbstractCorrelateMessageCmd.class;
      if(command.getClass()!=CorrelateMessageCmd.class && command.getClass()!=CorrelateAllMessageCmd.class)throw Refused.denied();
      Field field=type.getDeclaredField("builder");if(!field.trySetAccessible())throw Refused.unavailable();
      var builder=(org.cibseven.bpm.engine.impl.MessageCorrelationBuilderImpl)field.get(command);
      String id=builder.getProcessInstanceId();if(id==null || id.isEmpty())throw Refused.denied();return id;
    }catch(ReflectiveOperationException e){throw Refused.unavailable();}
  }
  private static String externalTaskId(Object command){
    // Pinned CIB2.1 has no public getter. Read only the exact native base class field.
    try {
      Class<?> type=ExternalTaskCmd.class;
      if(!EXTERNAL.containsKey(command.getClass()))throw Refused.denied();
      Field field=type.getDeclaredField("externalTaskId");if(!field.trySetAccessible())throw Refused.unavailable();
      Object id=field.get(command);if(!(id instanceof String value)||value.isEmpty())throw Refused.resource();return value;
    }catch(ReflectiveOperationException e){throw Refused.unavailable();}
  }
}
