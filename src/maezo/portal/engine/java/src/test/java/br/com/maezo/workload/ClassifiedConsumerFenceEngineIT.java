package br.com.maezo.workload;

import static org.junit.jupiter.api.Assertions.*;
import java.nio.file.Path;
import java.util.*;
import org.junit.jupiter.api.*;
import org.junit.jupiter.api.io.TempDir;

/** ROOT-only actual CIB/PostgreSQL interceptor chain; no protocol mock or PHI success claim. */
@Tag("integration")
@TestInstance(TestInstance.Lifecycle.PER_CLASS)
class ClassifiedConsumerFenceEngineIT {
  // static: injected by the TempDirectory BeforeAllCallback, which is ordered before this
  // class's own @BeforeAll. An instance @TempDir was null at start() (V3-Q3 report §3.3).
  @TempDir static Path directory;
  final WorkloadEngineIT f=new WorkloadEngineIT();
  @BeforeAll void start()throws Exception{WorkloadEngineIT.temp=directory;f.setup();}
  @AfterAll void stop()throws Exception{f.cleanup();}
  @AfterEach void reset(){f.clearOwnedFixtureInstances();}
  <T>T asWorker(java.util.function.Supplier<T> operation){
    var peer=f.policy.peers.stream().filter(p->p.engineUser().equals("fixture-worker")).findFirst().orElseThrow();
    f.engine.getIdentityService().setAuthentication(peer.engineUser(),List.of(),List.of(f.policy.tenant));
    WorkloadPlugin.GUARDED.set(true);
    try{return operation.get();}finally{WorkloadPlugin.GUARDED.remove();f.engine.getIdentityService().clearAuthentication();}
  }
  @Test void actualGuardedNativeMutationCannotBypassTypedTargetOperationContext()throws Exception{
    var pi=f.bootstrap(()->f.engine.getRuntimeService().startProcessInstanceById(f.contas));
    var task=f.bootstrap(()->f.engine.getExternalTaskService().createExternalTaskQuery().processInstanceId(pi.getId()).singleResult());
    f.bootstrap(()->f.engine.getExternalTaskService().fetchAndLock(1,"fixture-worker").topic("operadora.contas.calculate_impact",60000).execute());
    assertThrows(Refused.class,()->asWorker(()->{f.engine.getExternalTaskService().complete(task.getId(),"fixture-worker",Map.of("human_approved",true));return null;}));
    assertThrows(Refused.class,()->asWorker(()->{f.engine.getExternalTaskService().handleBpmnError(task.getId(),"fixture-worker","ERR_TEST");return null;}));
    assertThrows(Refused.class,()->asWorker(()->{f.engine.getRuntimeService().setVariable(pi.getId(),"human_decision_command_ref","caller");return null;}));
    assertThrows(Refused.class,()->asWorker(()->{f.engine.getRuntimeService().createProcessInstanceModification(pi.getId()).cancelAllForActivity("external").execute();return null;}));
    assertNotNull(f.bootstrap(()->f.engine.getExternalTaskService().createExternalTaskQuery().externalTaskId(task.getId()).singleResult()));
    assertNull(f.bootstrap(()->f.engine.getRuntimeService().getVariable(pi.getId(),"human_decision_command_ref")));
  }
  @Test void wrongNativeTaskAndUnsealedCorrelationLeaveActualStateIntact(){
    var pi=f.bootstrap(()->f.engine.getRuntimeService().startProcessInstanceById(f.contas));
    assertThrows(Refused.class,()->asWorker(()->{f.engine.getExternalTaskService().unlock("wrong-native-task");return null;}));
    assertThrows(Refused.class,()->asWorker(()->{f.engine.getRuntimeService().createMessageCorrelation("message").processInstanceId(pi.getId()).correlate();return null;}));
    assertNotNull(f.bootstrap(()->f.engine.getRuntimeService().createProcessInstanceQuery().processInstanceId(pi.getId()).singleResult()));
  }
  @Test void previousUnclassifiedV1WorkflowStillUsesActualQualifiedCommand()throws Exception{
    // Reuses the original real-engine assertions unchanged, including operation result and next activity.
    f.realReceiptAndNativeHistoryWitness();
    f.externalLifecycleAndHumanMutationRefusal();
  }

  @Test void inheritedNativeEffectsThroughActualInterceptorPreserveLockedStateAndReceipts()throws Exception{
    // P2-R1: the three original inherited-command witnesses now traverse the actual
    // secured CIB executor, with real valid targets and no call to the fence predicate.
    String configuredJar=System.getProperty("maezo.consumer.jar");
    assertNotNull(configuredJar,"phi-consumer-engine-it JAR profile required");
    Path expectedJar=Path.of(configuredJar).toRealPath();
    for(Class<?> production:List.of(ClassifiedConsumerFence.class,WorkloadPlugin.class))
      assertEquals(expectedJar,Path.of(production.getProtectionDomain().getCodeSource().getLocation().toURI()).toRealPath(),
          production.getName()+" must execute from the exact selected JAR");
    // Seed genuine existing human receipt/history evidence; this is not a classified decision.
    f.realReceiptAndNativeHistoryWitness();
    for(String family:List.of("complete","bpmn_error","unlock")){
      String taskId=f.lockedTask(family.equals("bpmn_error")?f.escalation:f.contas);
      var task=f.bootstrap(()->f.engine.getExternalTaskService().createExternalTaskQuery().externalTaskId(taskId).singleResult());
      assertNotNull(task);assertEquals("fixture-worker",task.getWorkerId());
      assertNotNull(task.getLockExpirationTime());assertTrue(task.getLockExpirationTime().after(new Date()));
      String process=task.getProcessInstanceId();
      assertEquals(0L,f.bootstrap(()->f.engine.getTaskService().createTaskQuery().processInstanceId(process).count()));
      assertNull(f.bootstrap(()->f.engine.getRuntimeService().getVariable(process,"p2_inherited_effect_probe")));
      var variables=Map.<String,Object>of("p2_inherited_effect_probe",family);
      List<org.cibseven.bpm.engine.impl.interceptor.Command<?>> commands=switch(family){
        case "complete" -> List.of(
            new org.cibseven.bpm.engine.impl.cmd.CompleteExternalTaskCmd(taskId,"fixture-worker",variables,Map.of()),
            new org.cibseven.bpm.engine.impl.cmd.CompleteExternalTaskCmd(taskId,"fixture-worker",variables,Map.of()){});
        case "bpmn_error" -> List.of(
            new org.cibseven.bpm.engine.impl.cmd.HandleExternalTaskBpmnErrorCmd(taskId,"fixture-worker","ERR_ESC_NOTIFY_FAILED",null,variables),
            new org.cibseven.bpm.engine.impl.cmd.HandleExternalTaskBpmnErrorCmd(taskId,"fixture-worker","ERR_ESC_NOTIFY_FAILED",null,variables){});
        case "unlock" -> List.of(
            new org.cibseven.bpm.engine.impl.cmd.UnlockExternalTaskCmd(taskId),
            new org.cibseven.bpm.engine.impl.cmd.UnlockExternalTaskCmd(taskId){});
        default -> throw new AssertionError(family);
      };
      var before=f.state();long receipts=f.receiptCount();assertTrue(receipts>0);
      for(int variant=0;variant<commands.size();variant++){
        var command=commands.get(variant);String witness=family+(variant==0?" direct":" inherited");
        assertTrue(task.getLockExpirationTime().after(new Date()),witness+" lock remains valid before attempt");
        var refused=assertThrows(Refused.class,()->asWorker(()->f.securedConfig.getCommandExecutorTxRequired().execute(command)),witness);
        WorkloadEngineIT.refusal(refused,"engine_operation_denied",403);
        assertTrue(Arrays.stream(refused.getStackTrace()).anyMatch(frame->
            frame.getClassName().equals("br.com.maezo.workload.ClassifiedConsumerFence") && frame.getMethodName().equals("nested")),
            witness+" must be refused by the installed effect fence, not native not-found/authorization validation");
        assertEquals(before,f.state(),witness+" must preserve actual native/history/receipt rows");
        assertEquals(receipts,f.receiptCount(),witness+" cannot append a receipt");
        var after=f.bootstrap(()->f.engine.getExternalTaskService().createExternalTaskQuery().externalTaskId(taskId).singleResult());
        assertNotNull(after,witness+" cannot complete or consume the BPMN error task");
        assertEquals(task.getExecutionId(),after.getExecutionId(),witness);
        assertEquals(process,after.getProcessInstanceId(),witness);
        assertEquals(task.getWorkerId(),after.getWorkerId(),witness+" cannot unlock");
        assertEquals(task.getLockExpirationTime(),after.getLockExpirationTime(),witness+" cannot change the lock");
        assertEquals(task.getRetries(),after.getRetries(),witness);
        assertEquals(task.getErrorMessage(),after.getErrorMessage(),witness);
        assertEquals(0L,f.bootstrap(()->f.engine.getTaskService().createTaskQuery().processInstanceId(process).count()),witness+" cannot reach downstream human task");
        assertEquals(0L,f.bootstrap(()->f.engine.getHistoryService().createHistoricActivityInstanceQuery().processInstanceId(process).activityId("human").count()),witness+" cannot enter downstream history");
        assertNull(f.bootstrap(()->f.engine.getRuntimeService().getVariable(process,"p2_inherited_effect_probe")),witness);
      }
    }
  }
}
