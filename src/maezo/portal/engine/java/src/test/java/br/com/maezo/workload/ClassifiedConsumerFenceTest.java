package br.com.maezo.workload;

import static org.junit.jupiter.api.Assertions.*;
import java.util.*;
import org.cibseven.bpm.engine.impl.cmd.CompleteExternalTaskCmd;
import org.cibseven.bpm.engine.impl.cmd.HandleExternalTaskFailureCmd;
import org.cibseven.bpm.engine.impl.cmd.HandleExternalTaskBpmnErrorCmd;
import org.cibseven.bpm.engine.impl.cmd.UnlockExternalTaskCmd;
import org.junit.jupiter.api.Test;

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
}
