package br.com.maezo.workload;

import static org.junit.jupiter.api.Assertions.*;
import java.util.*;
import org.cibseven.bpm.engine.impl.cmd.CompleteExternalTaskCmd;
import org.cibseven.bpm.engine.impl.cmd.HandleExternalTaskFailureCmd;
import org.cibseven.bpm.engine.impl.cmd.HandleExternalTaskBpmnErrorCmd;
import org.cibseven.bpm.engine.impl.cmd.UnlockExternalTaskCmd;
import org.cibseven.bpm.engine.impl.cmd.*;
import org.cibseven.bpm.engine.impl.MessageCorrelationBuilderImpl;
import org.cibseven.bpm.engine.impl.interceptor.CommandExecutor;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.TestFactory;
import org.junit.jupiter.api.DynamicTest;
import java.lang.reflect.*;
import java.util.stream.Stream;

/** Real native command types at the fence; no engine or acquisition is mocked as integrated. */
class ClassifiedConsumerFenceTest {
  @Test void guardedBooleanCannotAuthorizeAnyNativeEffect(){
    WorkloadPlugin.GUARDED.set(true);
    try {
      for(Object command:List.of(new CompleteExternalTaskCmd("task","worker",Map.of(),Map.of()),
          new UnlockExternalTaskCmd("task"),new HandleExternalTaskFailureCmd("task","worker","worker_failure",null,0,0,Map.of(),Map.of()),
          new HandleExternalTaskBpmnErrorCmd("task","worker","ERR_TEST",null,Map.of())))
        assertThrows(Refused.class,()->ClassifiedConsumerFence.nested(command));
    }finally{WorkloadPlugin.GUARDED.remove();}
  }
  @Test void markerOmissionNeverSelectsUnclassifiedSuccess(){
    for(Map<String,Object> variables:List.of(Map.<String,Object>of(),Map.<String,Object>of("human_decision_command_ref","x"),Map.<String,Object>of("human_approved",true)))
      for(boolean v2:List.of(true,false))
        for(String operation:List.of("external_complete","external_bpmn_error"))
          assertThrows(Refused.class,()->ClassifiedConsumerFence.disposition(true,v2,operation,variables,Map.of()));
  }
  @Test void onlyNarrowV2NonSuccessDispositionRemains(){
    assertDoesNotThrow(()->ClassifiedConsumerFence.disposition(true,true,"external_failure",Map.of(),Map.of("retries",0L)));
    assertDoesNotThrow(()->ClassifiedConsumerFence.disposition(true,true,"external_unlock",Map.of(),Map.of()));
    assertDoesNotThrow(()->ClassifiedConsumerFence.disposition(true,true,"external_extend_lock",Map.of(),Map.of()));
    assertThrows(Refused.class,()->ClassifiedConsumerFence.disposition(true,false,"external_failure",Map.of(),Map.of("retries",0L)));
    assertThrows(Refused.class,()->ClassifiedConsumerFence.disposition(true,true,"external_failure",Map.of(),Map.of("retries",1L)));
    assertThrows(Refused.class,()->ClassifiedConsumerFence.disposition(true,true,"external_unlock",Map.of("x","y"),Map.of()));
  }
  @Test void ordinaryQualifiedOperationsRetainTheirClassification(){
    for(String operation:List.of("external_complete","external_bpmn_error","external_failure","external_unlock","external_extend_lock"))
      assertDoesNotThrow(()->ClassifiedConsumerFence.disposition(false,true,operation,Map.of(),Map.of()));
  }

  static class NamedComplete extends CompleteExternalTaskCmd { NamedComplete(){super("task","worker",Map.of(),Map.of());} }
  static class DeepComplete extends NamedComplete {}
  static class NamedError extends HandleExternalTaskBpmnErrorCmd { NamedError(){super("task","worker","ERR_TEST",null,Map.of());} }
  static class NamedFailure extends HandleExternalTaskFailureCmd { NamedFailure(){super("task","worker","worker_failure",null,0,0,Map.of(),Map.of());} }
  static class NamedUnlock extends UnlockExternalTaskCmd { NamedUnlock(){super("task");} }
  static class NamedExtend extends ExtendLockOnExternalTaskCmd { NamedExtend(){super("task","worker",1000);} }
  static class NamedTask extends CompleteTaskCmd { NamedTask(){super("task",Map.of());} }
  static class NamedVariables extends SetExecutionVariablesCmd { NamedVariables(){super("execution",Map.of(),false);} }
  static class NamedDelete extends DeleteProcessInstanceCmd { NamedDelete(){super("process","synthetic",false,false,false,false,false);} }
  static class NamedCorrelation extends CorrelateMessageCmd { NamedCorrelation(){super(builder(),false,false,false);} }
  static class NamedCorrelateAll extends CorrelateAllMessageCmd { NamedCorrelateAll(){super(builder(),false,false);} }
  private static MessageCorrelationBuilderImpl builder(){
    var builder=new MessageCorrelationBuilderImpl(new CommandExecutor(){
      @Override public <T>T execute(org.cibseven.bpm.engine.impl.interceptor.Command<T> command){
        throw new AssertionError("This unit control must never execute a native effect");
      }
    },"message");
    builder.processInstanceId("process");return builder;
  }
  private static List<Object> inheritedEffects(){
    return List.of(
      new CompleteExternalTaskCmd("task","worker",Map.of(),Map.of()){},new NamedComplete(),new DeepComplete(),
      new HandleExternalTaskBpmnErrorCmd("task","worker","ERR_TEST",null,Map.of()){},new NamedError(),
      new HandleExternalTaskFailureCmd("task","worker","worker_failure",null,0,0,Map.of(),Map.of()){},new NamedFailure(),
      new UnlockExternalTaskCmd("task"){},new NamedUnlock(),
      new ExtendLockOnExternalTaskCmd("task","worker",1000){},new NamedExtend(),
      new CompleteTaskCmd("task",Map.of()){},new NamedTask(),
      new SubmitTaskFormCmd("task",Map.of(),false,false){},
      new SetTaskVariablesCmd("task",Map.of(),false){},
      new SetExecutionVariablesCmd("execution",Map.of(),false){},new NamedVariables(),
      new DeleteProcessInstanceCmd("process","synthetic",false,false,false,false,false){},new NamedDelete(),
      new SignalCmd("execution","signal",null,Map.of()){},
      new SetExternalTaskRetriesCmd("task",0,false){},
      new AssignTaskCmd("task","user"){},
      new CorrelateMessageCmd(builder(),false,false,false){},new NamedCorrelation(),
      new CorrelateAllMessageCmd(builder(),false,false){},new NamedCorrelateAll());
  }
  @TestFactory Stream<DynamicTest> inheritedNativeEffectsCannotHideBehindGuarded(){
    return inheritedEffects().stream().map(command->DynamicTest.dynamicTest(command.getClass().getName(),()->{
      WorkloadPlugin.GUARDED.set(true);
      try{assertThrows(Refused.class,()->ClassifiedConsumerFence.nested(command));}
      finally{WorkloadPlugin.GUARDED.remove();}
    }));
  }
  @Test void unrelatedNamesAndReadCommandsRemainNonEffects(){
    class CompleteExternalTaskCmd {}
    class SetExecutionVariablesCmd {}
    class CorrelateMessageCmd {}
    for(Object command:List.of(new Object(),new CompleteExternalTaskCmd(),new SetExecutionVariablesCmd(),new CorrelateMessageCmd(),
        new GetTaskVariableCmd("task","name",false),new GetTaskVariableCmd("task","name",false){},
        new GetExternalTaskErrorDetailsCmd("task"),new GetExternalTaskErrorDetailsCmd("task"){}))
      assertDoesNotThrow(()->ClassifiedConsumerFence.nested(command));
  }
  @Test void exactNativeTargetsRemainReadableButInheritedTargetsRefuse() throws Exception {
    Method read=ClassifiedConsumerFence.class.getDeclaredMethod("externalTaskId",Object.class);read.setAccessible(true);
    for(Object command:List.of(new CompleteExternalTaskCmd("task","worker",Map.of(),Map.of()),
        new HandleExternalTaskBpmnErrorCmd("task","worker","ERR_TEST",null,Map.of()),
        new HandleExternalTaskFailureCmd("task","worker","worker_failure",null,0,0,Map.of(),Map.of()),
        new UnlockExternalTaskCmd("task"),new ExtendLockOnExternalTaskCmd("task","worker",1000)))
      assertEquals("task",read.invoke(null,command));
    assertInstanceOf(Refused.class,assertThrows(InvocationTargetException.class,()->read.invoke(null,new NamedComplete())).getCause());
    Method correlation=ClassifiedConsumerFence.class.getDeclaredMethod("correlationTarget",Object.class);correlation.setAccessible(true);
    for(Object command:List.of(new CorrelateMessageCmd(builder(),false,false,false),new CorrelateAllMessageCmd(builder(),false,false)))
      assertEquals("process",correlation.invoke(null,command));
    assertInstanceOf(Refused.class,assertThrows(InvocationTargetException.class,()->correlation.invoke(null,new NamedCorrelation())).getCause());
  }
  @Test void inheritedCommandsRefuseEvenWithMatchingSyntheticSeal() throws Exception {
    Class<?> seal=Class.forName("br.com.maezo.workload.ClassifiedConsumerFence$Seal");
    Constructor<?> make=seal.getDeclaredConstructors()[0];make.setAccessible(true);
    Map<Object,String> commands=Map.of(new NamedComplete(),"external_complete",new NamedError(),"external_bpmn_error",
        new NamedFailure(),"external_failure",new NamedUnlock(),"external_unlock",new NamedExtend(),"external_extend_lock");
    for(var entry:commands.entrySet()){
      try(var permit=(ClassifiedConsumerFence.Permit)make.newInstance(new Object(),null,"task",entry.getValue())){
        assertThrows(Refused.class,()->ClassifiedConsumerFence.nested(entry.getKey()));
      }
    }
    try(var permit=(ClassifiedConsumerFence.Permit)make.newInstance(new Object(),null,"process","correlate")){
      assertThrows(Refused.class,()->ClassifiedConsumerFence.nested(new NamedCorrelation()));
      assertThrows(Refused.class,()->ClassifiedConsumerFence.nested(new NamedCorrelateAll()));
    }
  }
  @Test void wrongOperationOrTargetCannotConsumeEvenASyntheticSeal() throws Exception {
    // Pure seal-comparison unit control. No context, issuer or engine authority is qualified here.
    Class<?> seal=Class.forName("br.com.maezo.workload.ClassifiedConsumerFence$Seal");
    Constructor<?> make=seal.getDeclaredConstructors()[0];make.setAccessible(true);
    for(String[] tuple:List.of(new String[]{"task","external_unlock"},new String[]{"other-task","external_complete"})){
      try(var permit=(ClassifiedConsumerFence.Permit)make.newInstance(new Object(),null,tuple[0],tuple[1])){
        assertThrows(Refused.class,()->ClassifiedConsumerFence.nested(new CompleteExternalTaskCmd("task","worker",Map.of(),Map.of())));
        assertThrows(Refused.class,()->ClassifiedConsumerFence.nested(new NamedComplete()));
      }
    }
    assertThrows(Refused.class,()->ClassifiedConsumerFence.nested(new CompleteExternalTaskCmd("task","worker",Map.of(),Map.of())));
  }
}
