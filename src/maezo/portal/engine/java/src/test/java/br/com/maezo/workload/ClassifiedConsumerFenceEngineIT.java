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
  @TempDir Path directory;
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
}
