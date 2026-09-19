package br.com.maezo.workload;

import static org.junit.jupiter.api.Assertions.*;
import java.util.Set;
import org.apache.catalina.core.StandardContext;
import org.apache.catalina.core.StandardHost;
import org.cibseven.bpm.engine.impl.cfg.StandaloneProcessEngineConfiguration;
import org.junit.jupiter.api.Test;

class StartupLifecycleTest {
  @Test void policyRefusalCannotBecomeAStartedEngineOrRetry() {
    var state=new StartupLifecycle(new StandardHost(),Set.of("/engine-rest"));
    state.refusePolicy();
    assertEquals(StartupLifecycle.Stage.PREFLIGHT_REFUSED,state.stage());
    assertThrows(Refused.class,state::vendorStarting);
    assertThrows(Refused.class,state::vendorStarted);
    assertThrows(Refused.class,state::refusePolicy);
    assertThrows(Refused.class,()->state.claimConfiguration(new StandaloneProcessEngineConfiguration()));
    state.stopped();assertThrows(Refused.class,state::vendorStarting);
  }

  @Test void positiveBranchRequiresExactlyOneActualConfiguration() {
    var state=new StartupLifecycle(new StandardHost(),Set.of("/engine-rest"));
    var configuration=new StandaloneProcessEngineConfiguration();
    state.acceptPolicy();state.vendorStarting();assertThrows(Refused.class,state::vendorStarted);
    state.claimConfiguration(configuration);
    assertTrue(state.isConfiguration(configuration));
    assertFalse(state.isConfiguration(new StandaloneProcessEngineConfiguration()));
    assertThrows(Refused.class,()->state.claimConfiguration(configuration));
    state.vendorStarted();assertEquals(StartupLifecycle.Stage.STARTED,state.stage());
    assertThrows(Refused.class,state::verifyRefusedContexts);
    assertThrows(Refused.class,state::refusePolicy);
  }

  @Test void unknownEmptyRuntimeAndNeverStartedContextCannotProveContainedFailure() {
    var host=new StandardHost();host.setName("localhost");
    var context=new StandardContext();context.setName("/engine-rest");context.setPath("/engine-rest");
    host.addChild(context);
    var state=new StartupLifecycle(host,Set.of("/engine-rest"));
    state.enroll(context);state.refusePolicy();
    assertThrows(Refused.class,state::verifyRefusedContexts);
    assertThrows(Refused.class,()->state.enroll(context));
  }

  @Test void contextFromAnotherHostOrUnexpectedPathCannotBeAdopted() {
    var expected=new StandardHost();var other=new StandardHost();other.setName("other");
    var context=new StandardContext();context.setName("/engine-rest");context.setPath("/engine-rest");
    other.addChild(context);
    var state=new StartupLifecycle(expected,Set.of("/engine-rest"));
    assertThrows(Refused.class,()->state.enroll(context));
    var unexpected=new StandardContext();unexpected.setName("/unexpected");unexpected.setPath("/unexpected");
    expected.addChild(unexpected);
    assertThrows(Refused.class,()->state.enroll(unexpected));
  }
}
